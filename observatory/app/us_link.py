# -*- coding: utf-8 -*-
"""Naming things the way people type them: companies, industries, managers.

The US shelf knows a company by CIK, the price feed by ticker, the 13F sets by
CUSIP and each manager's spelling of the issuer's name, and people by
whatever they say ("Apple", "AAPL", "Berkshire", "carmakers"). This module
is the one place those are joined, so every recipe resolves a mention the
same way and says how it did it.

  * company   ticker token (AAPL, BRK.B) or company name found as whole
              words in the question; the longest name wins
  * cusip     a CIK's CUSIPs, by matching the normalised issuer name that
              most managers used for the CUSIP to the SEC's company name;
              a share class (GOOGL, GOOG) gives two CUSIPs for one CIK
  * industry  a synonym list ("carmakers" → SIC 3711) first, then the SIC
              titles themselves
  * manager   a 13F filer by name
  * money and time words ("$10bn", "this month", "since 2018")

Every lookup is a plain function over the SEC database cursor; the heavy
indexes are memoised per database version. Nothing here guesses: a mention
that resolves to nothing stays None, and the recipe says so.
"""
import datetime
import os
import pathlib
import re
import threading

from . import sec_api

_LOCK = threading.Lock()
_MEMO = {}

_DROP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC",
         "HOLDINGS", "HOLDING", "HLDGS", "GROUP", "GRP", "THE", "COM", "NEW", "DE", "NV", "SA", "AG",
         "LLC", "LP", "TRUST", "CLASS", "CL", "COMMON", "STOCK", "SHS", "ORD", "ADR", "ADS", "DEL"}
_SHARE_CLASS = re.compile(r"\b(CLASS|CL|SERIES)\s+[A-C]\b")
_WORD = re.compile(r"[A-Z0-9]+")
_TICKER_TOKEN = re.compile(r"\b[A-Z]{1,5}(?:[.-][A-Z])?\b")
_TICKER_STOP = {"US", "USA", "PE", "P", "E", "A", "I", "IT", "AT", "ON", "OR", "AN", "ALL", "ARE", "BE",
                "BY", "CEO", "CFO", "ETF", "IPO", "USD", "NYSE", "SEC", "EPS", "ROE", "ROA", "Q", "FY", "CIK", "SIC", "TSR", "YTD", "AI", "EV", "ESG",
                "TOP", "TEN", "VS", "AND", "THE", "OF", "IN", "TO", "FOR", "WHO", "WHAT", "HOW", "IS",
                "DO", "DID", "NEXT", "LAST", "THIS", "YEAR", "MONTH", "WEEK", "BIG", "LOW", "HIGH",
                "NET", "CASH", "DEBT", "BUY", "SELL", "OWN", "OWNS", "ANY", "ONLY", "SOLD", "PLAN",
                "RANK", "LIST", "MOST", "WORTH", "FELL", "ROSE", "DOWN", "UP", "AMONG", "ABOVE",
                "BOTH", "EACH", "WHEN", "WHERE", "WHICH", "WITH", "FROM", "THAN", "ITS", "MARKET",
                "VALUE", "TRADE", "TRADES", "FILE", "FILED", "REPORT", "AGAINST", "SINCE", "OPEN",
                "ONE", "TWO", "FIVE", "TIME", "AS", "ADD", "DROP", "BANK", "BANKS", "OIL", "GAS",
                "CAR", "CARS", "AUTO", "SHARE", "SHARES", "LARGE", "LARGEST", "TRADING", "YIELD",
                "MANY", "HAVE", "HAS", "HAD", "WAS", "WERE", "SO", "WE", "MY", "YOU", "NOT", "NO",
                "YES", "BUT", "IF", "THEN", "THAN", "THEIR", "THEY", "THEM", "ARE", "NOW", "ALSO"}


# Ordinary English words that also begin company names. Never read as a
# company from the word alone, however it is capitalised.
_ENGLISH = {w.upper() for w in """
above about after again against almost along also always american another any apple around back before
behind below best better between beyond big black blue both bright broad can capital central charter city
clear close common core cost country crown daily deep direct double down each early east easy either enough
equal even ever every express fair fast few first flex focus forward free fresh front full future general
giant global golden good great green half happy hard high home hope how image impact inside integral into
just key large last late less life light like little live local long main major many market master metro mid
mobile modern more most much national natural near never new next north nothing now ocean often one only
open orbit other over own pacific park peak people perfect plus point power premier prime pro pure quick rapid
rather real ready rich right rise river royal safe same second select sharp simple since smart solid some
south spring star state still strong such sun super sure target ten than that their them then there these
they this those three through time today top total trade true under unique united universal upper value
very vital well west what when where which while white who whole wide wise with within world would year
yes yet your tokyo london paris york china japan india europe asia africa america canada texas florida
bill people work working works pay paying payment payments cash debt sale sales buy buying sell selling
companies company corporation corporations business businesses industries industry firm firms sector sectors
""".split()}


_KEEP_UPPER = {"LLC", "LP", "LLP", "NV", "SA", "AG", "SE", "PLC", "USA", "US", "UK", "II", "III", "IV", "REIT", "ETF",
               "IBM", "AMD", "AT&T", "HP", "3M", "UPS", "CVS", "AES", "AIG", "BNY", "PNC", "PPG", "MSCI", "KLA", "ASML",
               "NXP", "TJX", "EOG", "DTE", "CME", "ICE", "AMC", "GE", "GM", "HCA", "IQVIA", "RTX", "NRG", "ON", "WEC",
               "TE", "APA", "VICI", "EQT", "TKO", "SBA", "IDEX", "IDEXX", "PACCAR", "EMCOR", "ONEOK", "AMETEK",
               "CBRE", "EPAM", "STERIS", "CDW", "PTC"}
_MIXED = {"JPMORGAN": "JPMorgan", "NVIDIA": "Nvidia", "PAYPAL": "PayPal", "EBAY": "eBay", "IPHONE": "iPhone",
          "MCDONALDS": "McDonald's", "MCDONALD'S": "McDonald's", "BLACKROCK": "BlackRock", "FEDEX": "FedEx",
          "DUPONT": "DuPont", "MASTERCARD": "Mastercard", "LINKEDIN": "LinkedIn", "YOUTUBE": "YouTube",
          "EXXONMOBIL": "ExxonMobil", "SERVICENOW": "ServiceNow", "METLIFE": "MetLife", "NISOURCE": "NiSource",
          "COSTAR": "CoStar", "FACTSET": "FactSet", "TRANSDIGM": "TransDigm", "DAVITA": "DaVita",
          "KEYCORP": "KeyCorp", "L3HARRIS": "L3Harris", "AUTOZONE": "AutoZone", "CARMAX": "CarMax",
          "GODADDY": "GoDaddy", "CROWDSTRIKE": "CrowdStrike", "DOORDASH": "DoorDash", "NETAPP": "NetApp",
          "MARKETAXESS": "MarketAxess", "APPLOVIN": "AppLovin", "LYONDELLBASELL": "LyondellBasell",
          "HUBSPOT": "HubSpot", "DEXCOM": "DexCom", "VERISIGN": "VeriSign", "SNAP-ON": "Snap-on", "LTD": "Ltd", "HLDGS": "Hldgs"}
# Joining words stay lower case inside a name: "Eli Lilly and Company".
_SMALL = {"AND", "OF", "THE", "FOR", "IN"}
# The SEC files some names surname first or without their punctuation; these
# are keyed on the name with its state tag and spacing already cleaned.
_WHOLE = {"SCHWAB CHARLES CORP": "Charles Schwab Corp", "PRICE T ROWE GROUP INC": "T. Rowe Price Group Inc",
          "BERKLEY W R CORP": "W. R. Berkley Corp", "US BANCORP": "U.S. Bancorp", "KKR + CO INC": "KKR & Co Inc",
          "COCA COLA CO": "Coca-Cola Co", "COLGATE PALMOLIVE CO.": "Colgate-Palmolive Co.",
          "LOWES COMPANIES INC": "Lowe's Companies Inc"}
_STATE_TAG = re.compile(r"\s*[/\\]\s*[A-Z]{2,4}\s*[/\\]?\s*$|\s*/\s*$")
_CLASS_TAIL = re.compile(r"\s+CL(?:ASS)?\s+[A-C]$|(?<=\bINC)\.?\s+[A-C]$")


def _word(w):
    if "-" in w and w not in _MIXED:
        return "-".join(_word(p) for p in w.split("-"))
    core = w.strip(",.")
    if core in _MIXED:
        return w.replace(core, _MIXED[core])
    if core in _KEEP_UPPER or "&" in core or (len(core) <= 3 and not any(ch in "AEIOU" for ch in core)):
        return w
    if re.fullmatch(r"(?:[A-Z]\.)+[A-Z]?", core):            # S.A., N.V., L.P.
        return w
    return w.capitalize()


def display_name(name):
    """The SEC's company name as people write it: 'FEDERAL SIGNAL CORP /DE/'
    -> 'Federal Signal Corp', 'ELI LILLY AND COMPANY' -> 'Eli Lilly and Company'.
    Names already in mixed case are kept."""
    n = " ".join((name or "").split())
    prev = None
    while n != prev:
        prev, n = n, _STATE_TAG.sub("", n)
    n = re.sub(r",?\s+/\w+/?$", "", n)
    # Mixed case is kept, unless a word is still in capitals ("ELI LILLY & Co"
    # in the SEC's list): then the whole name is read as capitals.
    if n != n.upper() and not any(w.isupper() and len(w.strip(",.")) >= 4 and w.strip(",.") not in _KEEP_UPPER
                                  for w in n.split()):
        return n
    n = n.upper()
    n = _CLASS_TAIL.sub("", n)
    if n in _WHOLE:
        return _WHOLE[n]
    out = []
    for i, w in enumerate(n.split()):
        out.append(w.lower() if i and w in _SMALL else _word(w))
    return " ".join(out)


# A Form 4 names a person surname first ("Zuckerberg Mark", "COX CHRISTOPHER K")
# and a fund or company as itself; these words mark the second kind.
_ENTITY_WORD = re.compile(r"(?i)\b(?:inc|llc|l\.l\.c|lp|l\.p|llp|ltd|limited|corp|corporation|co|company|fund|funds|capital|"
                          r"partners|partnership|plan|trust|trustee|holdings?|group|management|advisors?|advisers?|associates|"
                          r"association|authority|foundation|bank|investments?|ventures|gp|sicav|a\.s|se|plc|n\.v|s\.a|ag|"
                          r"equity|securities|insurance|retirement|pension|estate)\b\.?|\d|&")
_SURNAME_PARTICLE = {"de", "di", "da", "del", "della", "van", "von", "le", "la", "du", "st", "st.", "mac", "dos", "das"}
_NAME_SUFFIX = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def person_name(name):
    """A Form 4 filer's name as people write it: 'Zuckerberg Mark' -> 'Mark
    Zuckerberg', 'COX CHRISTOPHER K' -> 'Christopher K Cox', 'Culp Robert
    George IV' -> 'Robert George Culp IV'. A fund or company is cased like a
    company name and never reordered."""
    n = " ".join((name or "").split())
    if not n:
        return n
    if _ENTITY_WORD.search(n):
        return display_name(n)
    words = n.split()
    if n == n.upper():
        words = [w if w.lower() in _NAME_SUFFIX and w.lower().startswith(("i", "v")) else
                 "'".join(p.capitalize() for p in w.split("'")) if "'" in w else
                 "-".join(p.capitalize() for p in w.split("-")) for w in words]
    if len(words) < 2:
        return " ".join(words)
    suffix = [words.pop()] if words[-1].lower() in _NAME_SUFFIX and len(words) > 2 else []
    cut = 2 if words[0].lower() in _SURNAME_PARTICLE and len(words) > 2 else 1
    return " ".join(words[cut:] + words[:cut] + suffix)


_LOGOS = pathlib.Path(__file__).resolve().parent.parent / "web" / "assets" / "logos" / "us"
_LOGO_SET = (None, frozenset())


def logo(cik):
    """The company's icon on our own host, or None: an admin edit first
    (app/us_companies.py: uploaded, or hidden), else the built-in one."""
    from . import us_companies
    edit = us_companies.logo_url(cik)
    if edit is not None:
        return edit or None
    return builtin_logo(cik)


def builtin_logo(cik):
    """The icon equity/us_logos.py fetched for the company, or None."""
    global _LOGO_SET
    try:
        stamp = os.stat(_LOGOS).st_mtime
    except OSError:
        return None
    if _LOGO_SET[0] != stamp:
        _LOGO_SET = (stamp, frozenset(f.name for f in _LOGOS.glob("*.png")))
    name = "%d.png" % int(cik)
    return "/assets/logos/us/" + name if name in _LOGO_SET[1] else None


def company_name(cik, filed, cur=None):
    """The one name every US page shows for a company: an admin edit, else a
    name chosen by hand (_curated_names), else the company's own registered name from its latest proxy (S&P 500 members:
    'Amazon.com, Inc.', where the SEC's list has 'Amazon Com Inc'), else the
    filed name it is given, made readable (display_name)."""
    from . import us_companies
    edit = us_companies.name(cik) or _curated_names().get(int(cik))
    if edit:
        return edit
    own = registrant_names(cur).get(int(cik)) if cur is not None else None
    return display_name(own or filed)


_CURATED = {}


def _curated_names():
    """The names chosen by hand for the deep-coverage companies
    (equity/us_universe.json: 'NVIDIA Corporation'), so the financials page
    and every other US page say the same."""
    if not _CURATED:
        import json
        path = pathlib.Path(__file__).resolve().parent.parent / "equity" / "us_universe.json"
        try:
            for c in json.loads(path.read_text(encoding="utf-8"))["companies"]:
                if c.get("cik") and c.get("name"):
                    _CURATED[int(c["cik"])] = c["name"]
        except (OSError, ValueError, KeyError):
            pass
        _CURATED.setdefault(0, None)
    return _CURATED


def registrant_names(cur):
    """{cik: dei:EntityRegistrantName from the latest proxy read}, memoised."""
    return _memo(cur, "registrant_names", _build_registrant_names)


def _build_registrant_names(cur):
    have = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if not {"sec_px_facts", "sec_px_filings"} <= have:
        return {}
    return {r[0]: r[1] for r in cur.execute("""
        SELECT f.cik, arg_max(f.value_text, x.filed)
        FROM sec_px_facts f JOIN sec_px_filings x ON x.accn = f.accn
        WHERE f.prefix = 'dei' AND f.tag = 'EntityRegistrantName' AND f.value_text IS NOT NULL
        GROUP BY f.cik""").fetchall()}


def norm_name(name):
    """'Apple Inc.' and 'APPLE INC' -> 'APPLE'; 'ALPHABET INC CLASS A' -> 'ALPHABET'."""
    s = (name or "").upper().replace("&", " AND ").replace("'", "")
    s = _SHARE_CLASS.sub(" ", s)
    words = [w for w in _WORD.findall(s) if w not in _DROP]
    return " ".join(words)


def _version():
    return sec_api.file_version()


def _memo(cur, key, build):
    v = _version()
    with _LOCK:
        hit = _MEMO.get(key)
        if hit and hit[0] == v:
            return hit[1]
    value = build(cur)
    with _LOCK:
        _MEMO[key] = (v, value)
    return value


# ---- companies -------------------------------------------------------------
def _build_companies(cur):
    if not sec_api._has(cur, "sec_companies"):
        return {"by_ticker": {}, "by_norm": {}, "names": [], "by_cik": {}}
    rows = cur.execute("SELECT cik, ticker, tickers, name, sic, industry, exchange FROM sec_companies").fetchall()
    by_ticker, by_norm, by_cik = {}, {}, {}
    for cik, ticker, tickers, name, sic, industry, exchange in rows:
        rec = {"cik": cik, "ticker": ticker, "tickers": (tickers or "").split(",") if tickers else [],
               "name": display_name(name), "sec_name": name, "sic": sic, "industry": industry, "exchange": exchange}
        by_cik[cik] = rec
        for t in rec["tickers"] or [ticker]:
            if t:
                by_ticker.setdefault(t.upper(), rec)
                by_ticker.setdefault(t.upper().replace("-", "."), rec)
        n = norm_name(name)
        if n and len(n) >= 3:
            by_norm.setdefault(n, rec)
    # A one-word company name that is also an ordinary word, a number, an
    # exchange or a stock term is never read as a company from the words alone;
    # its ticker still works ("TEN Holdings" is TXHG, "Nasdaq, Inc." is NDAQ).
    names = sorted((n for n in by_norm if not (" " not in n and n in _GENERIC_NAMES)), key=len, reverse=True)
    # the first two words of a longer name ("Restaurant Brands" for Restaurant Brands International), when
    # they are distinctive and name one company only
    prefix2, seen = {}, {}
    for n, rec in by_norm.items():
        w = n.split()
        if len(w) >= 3 and len(w[0]) >= 4 and len(w[1]) >= 3 and w[0] not in _GENERIC_NAMES and w[0] not in _ENGLISH:
            key = " ".join(w[:2])
            seen.setdefault(key, []).append((len(w), (rec.get("exchange") or "") in ("OTC", ""), rec))
    # one company per prefix: the shortest registered name on a main exchange ("Restaurant Brands International"
    # over its partnership twin); a prefix shared by unrelated companies is left out
    for key, lst in seen.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        if len(lst) == 1 or (len(lst) == 2 and lst[0][0] != lst[1][0]):
            prefix2[key] = lst[0][2]
    return {"by_ticker": by_ticker, "by_norm": by_norm, "names": names, "by_cik": by_cik, "prefix2": prefix2}


_GENERIC_NAMES = {"TEN", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TOP", "BEST", "TECH", "BIG", "RETURN",
                  "FOOD", "MEDICAL", "CARE", "BILL", "MILK", "ELECTRICITY", "HOUSING", "CARS", "TREASURY", "WAGE", "WAGES", "JOBS",
                  "EMPLOYMENT", "PRICES", "INFLATION", "CONSUMER", "RETAIL", "PARENT", "MAKER", "WEIGHT", "SPLIT", "SPLITS", "SECTOR", "LABS",
                  "CLOUD", "DATA", "CENTER", "GAMING", "SEARCH", "VIDEO", "MUSIC", "MOBILE", "PAYMENTS", "ADVERTISING", "SERVICES",
                  "RETURNS", "HIGHEST", "LOWEST", "CHEAPEST", "FASTEST", "LARGEST", "BIGGEST", "RATE", "RATES", "RENT", "MARGIN", "MARGINS",
                  "FIRST", "LAST", "NEXT", "BIG", "LARGE", "MOST", "MARKET", "VALUE", "GLOBAL", "GENERAL",
                  "AMERICAN", "UNITED", "NATIONAL", "CAPITAL", "FINANCIAL", "ENERGY", "HEALTH", "MEDICAL",
                  "DIGITAL", "TARGET", "GAP", "MATCH", "YIELD", "PRICE", "EARNINGS", "REPORT", "INSIDER",
                  "FUND", "BANK", "BANKS", "OIL", "GAS", "GOLD", "SILVER", "SUN", "STAR", "BOX", "SQUARE",
                  "NASDAQ", "NYSE", "OTC", "CBOE", "DOW", "INDEX", "QUARTER", "ANNUAL", "WEEK", "MONTH",
                  "YEAR", "YEARS", "TODAY", "NOW", "NEW", "OLD", "HIGH", "LOW", "OPEN", "CLOSE", "SHARE",
                  "SHARES", "STOCK", "STOCKS", "MONEY", "CASH", "DEBT", "GROWTH", "MARGIN", "PROFIT",
                  "REVENUE", "SALES", "RANK", "LIST", "BUY", "SELL", "HOLD", "OWN", "US", "USA", "AMERICA",
                  "CHINA", "JAPAN", "EUROPE", "WORLD", "SOUTH", "NORTH", "EAST", "WEST", "CENTRAL", "PACIFIC",
                  "ATLANTIC", "WESTERN", "EASTERN", "SOUTHERN", "NORTHERN", "COMMUNITY", "CITIZENS", "PEOPLES",
                  "PREMIER", "PRIME", "SELECT", "PLUS", "PRO", "MAX", "ALPHA", "BETA", "GAMMA", "DELTA",
                  "OMEGA", "SIGMA", "DIAMOND", "EAGLE", "LIBERTY", "FREEDOM", "PIONEER", "FRONTIER", "SUMMIT",
                  "HORIZON", "LEGACY", "HERITAGE", "VICTORY", "TRUST", "TRUSTS", "GROUP", "HOLDINGS",
                  # ordinary nouns that also start company names: never read as a company from the word alone
                  "RESEARCH", "DEVELOPMENT", "SCIENCE", "SCIENCES", "TECHNOLOGY", "TECHNOLOGIES", "SYSTEMS",
                  "SOLUTIONS", "SERVICES", "PRODUCTS", "INDUSTRIES", "INTERNATIONAL", "RESOURCES", "PARTNERS",
                  "PROPERTIES", "REALTY", "THERAPEUTICS", "PHARMACEUTICALS", "ENTERTAINMENT", "MEDIA",
                  "COMMUNICATIONS", "NETWORK", "NETWORKS", "SOFTWARE", "SEMICONDUCTOR", "BRANDS", "FOODS",
                  "MOTORS", "AIRLINES", "HOTELS", "STORES", "HOMES", "FINANCE", "INSURANCE", "INVESTMENT",
                  "INVESTORS", "ASSET", "EQUITY", "INCOME", "TOTAL", "SPENDING", "EMPLOYEES", "OPTION",
                  "OPTIONS", "DIVIDEND", "DIVIDENDS", "INTEREST", "SECURITY", "SECURITIES", "MANAGEMENT",
                  "PERFORMANCE", "QUALITY", "STANDARD", "SUPERIOR", "ADVANCED", "APPLIED", "INTEGRATED",
                  "MODERN", "SMART", "GREEN", "BLUE", "RED", "BLACK", "WHITE", "GRAND", "ROYAL", "CROWN",
                  "EMPIRE", "CONTINENTAL", "COLUMBIA", "WASHINGTON", "LINCOLN", "FRANKLIN", "JEFFERSON",
                  "MADISON", "HAMILTON", "CHARTER", "CENTURY", "MILLENNIUM", "PUBLIC", "PRIVATE", "HOME",
                  "MOBILE", "POWER", "LIGHT", "WATER", "EARTH", "OCEAN", "RIVER", "LAKE", "MOUNTAIN", "VALLEY"}


def companies(cur):
    return _memo(cur, "companies", _build_companies)


def company_by_cik(cur, cik):
    return companies(cur)["by_cik"].get(int(cik))


def _build_short_names(cur):
    """First word of a multi-word company name -> the companies starting with
    it, most-held first (13F reported value), so "Ford" finds Ford Motor Co
    and "Lockheed" finds Lockheed Martin. One-word names and generic first
    words are left out: "General", "American" and "First" start hundreds."""
    idx = companies(cur)
    value = {cik: sum(c["value"] or 0 for c in lst) for cik, lst in _build_cusips_memo(cur)["by_cik"].items()}
    out = {}
    for n, rec in idx["by_norm"].items():
        words = n.split()
        # only companies that managers actually hold: a $60mn listing whose name
        # starts with "Tokyo" must not catch a question about the weather there
        if len(words) < 2 or value.get(rec["cik"], 0) < 5e8:
            continue
        # any distinctive word of the name ("Disney" in Walt Disney Co, "Hathaway"
        # in Berkshire Hathaway), never an ordinary or generic one
        for w in words:
            if len(w) >= 4 and w.isalpha() and w not in _GENERIC_NAMES and w not in _ENGLISH:
                out.setdefault(w, []).append(rec)
    for w in out:
        out[w].sort(key=lambda r: -value.get(r["cik"], 0))
    return out


# A company that moved to a new SEC filer number (a holding-company reorganisation) keeps its
# history under the old one: {new cik: old cik}. Used wherever the latest annual report is looked up.
PREDECESSOR_CIKS = {2115436: 34088}          # ExxonMobil Holdings Corp <- Exxon Mobil Corp (2026)

# Names people write that are not the filer's registered name. Each maps to
# the ticker the SEC lists, so "Exxon" finds ExxonMobil Holdings and "Google"
# finds Alphabet without a model. Longest phrase wins; whole words only.
ALIASES = {
    "exxon": "XOM", "exxonmobil": "XOM", "exxon mobil": "XOM", "google": "GOOGL", "alphabet": "GOOGL",
    "facebook": "META", "meta": "META", "berkshire": "BRK-B", "berkshire hathaway": "BRK-B", "jp morgan": "JPM",
    "jpmorgan": "JPM", "jpmorgan chase": "JPM", "j.p. morgan": "JPM", "goldman": "GS", "goldman sachs": "GS",
    "morgan stanley": "MS", "bank of america": "BAC", "bofa": "BAC", "wells": "WFC", "wells fargo": "WFC",
    "citi": "C", "citigroup": "C", "coke": "KO", "coca-cola": "KO", "coca cola": "KO", "pepsi": "PEP", "pepsico": "PEP",
    "walmart": "WMT", "wal-mart": "WMT", "costco": "COST", "home depot": "HD", "mcdonald's": "MCD", "mcdonalds": "MCD",
    "tsmc": "TSM", "taiwan semiconductor": "TSM", "novo": "NVO", "novo nordisk": "NVO", "toyota": "TM", "sony": "SONY",
    "nintendo": "NTDOY", "shell": "SHEL", "bp": "BP", "chevron": "CVX", "p&g": "PG", "procter & gamble": "PG",
    "procter and gamble": "PG", "j&j": "JNJ", "johnson & johnson": "JNJ", "johnson and johnson": "JNJ",
    "ups": "UPS", "fedex": "FDX", "amd": "AMD", "nvidia": "NVDA", "nvidea": "NVDA", "micorsoft": "MSFT",
    "microsoft": "MSFT", "amazon": "AMZN", "apple": "AAPL", "tesla": "TSLA", "netflix": "NFLX", "palantir": "PLTR",
    "broadcom": "AVGO", "salesforce": "CRM", "oracle": "ORCL", "ibm": "IBM", "intel": "INTC", "qualcomm": "QCOM",
    "boeing": "BA", "lockheed": "LMT", "raytheon": "RTX", "rtx": "RTX", "ge": "GE", "general electric": "GE",
    "ford": "F", "gm": "GM", "general motors": "GM", "disney": "DIS", "nike": "NKE", "starbucks": "SBUX",
    "visa": "V", "mastercard": "MA", "amex": "AXP", "american express": "AXP", "paypal": "PYPL", "uber": "UBER",
    "airbnb": "ABNB", "united health": "UNH", "unitedhealth": "UNH", "pfizer": "PFE", "merck": "MRK",
    "eli lilly": "LLY", "lilly": "LLY", "abbvie": "ABBV", "caterpillar": "CAT", "deere": "DE", "john deere": "DE",
    "3m": "MMM", "honeywell": "HON", "at&t": "T", "verizon": "VZ", "comcast": "CMCSA", "t-mobile": "TMUS",
    "alibaba": "BABA", "baidu": "BIDU", "jd": "JD", "pdd": "PDD", "temu": "PDD", "nio": "NIO", "byd": "BYDDY",
    "target": "TGT", "lowe's": "LOW", "lowes": "LOW", "kroger": "KR", "exxon's": "XOM", "blackrock": "BLK",
    "berkshire's": "BRK-B", "schwab": "SCHW", "charles schwab": "SCHW", "us bancorp": "USB", "pnc": "PNC",
    "truist": "TFC", "capital one": "COF", "american airlines": "AAL", "delta": "DAL", "united airlines": "UAL",
    "southwest": "LUV", "marriott": "MAR", "hilton": "HLT", "chipotle": "CMG", "spotify": "SPOT", "shopify": "SHOP",
    "snowflake": "SNOW", "crowdstrike": "CRWD", "servicenow": "NOW", "adobe": "ADBE", "cisco": "CSCO",
    "micron": "MU", "arm": "ARM", "asml": "ASML", "applied materials": "AMAT", "lam research": "LRCX",
    "texas instruments": "TXN", "mu": "MU", "dell": "DELL", "hp": "HPQ", "hewlett packard": "HPQ", "lowe": "LOW",
    "google parent": "GOOGL", "google's parent": "GOOGL", "facebook parent": "META", "facebook's parent": "META",
    "the iphone maker": "AAPL", "iphone maker": "AAPL", "windows maker": "MSFT", "chatgpt": None, "openai": None,
    # possessive names lose their "'s" before matching: "McDonald's" arrives as "McDonald"
    "mcdonald": "MCD", "kohl": "KSS", "macy": "M", "wendy": "WEN", "domino": "DPZ", "hershey": "HSY", "moody": "MCO",
    "levi": "LEVI", "casey": "CASY", "papa john": "PZZA", "dick's sporting goods": "DKS", "dick": "DKS", "bj": "BJ",
    "sam": None, "kellogg": "K", "campbell": "CPB", "tyson": "TSN", "hormel": "HRL", "kraft": "KHC", "kraft heinz": "KHC",
}
ALIASES = {k: v for k, v in ALIASES.items() if v}
_ALIAS_RE = re.compile(r"(?<![\w&])(" + "|".join(sorted((re.escape(a) for a in ALIASES), key=len, reverse=True)) +
                       r")(?![\w&])", re.I)


def _alias_hits(idx, text):
    """[(position, record)] for alias phrases written in the question."""
    out = []
    for m in _ALIAS_RE.finditer(text):
        t = ALIASES[m.group(1).lower()]
        rec = idx["by_ticker"].get(t) or idx["by_ticker"].get(t.replace("-", "."))
        if rec is not None:
            out.append((m.start(), rec))
    return out


def find_companies(cur, question, limit=4):
    """Companies mentioned in a question, in order of appearance: ticker
    tokens written in capitals first, then names as whole words, then a
    distinctive first word of a name ("Ford")."""
    idx = companies(cur)
    found = []
    text = re.sub(r"(\w)['\u2019]s\b", r"\1", question or "")      # Nvidia's -> Nvidia
    bare = text.strip().strip("?.!").strip()
    # a one-word question that is a company's whole name, however ordinary the
    # word ("Target", "Visa"), is that company; so is a ticker typed in lower case ("nvda")
    if bare and " " not in bare and norm_name(bare) in idx["by_norm"] and norm_name(bare):
        return [dict(idx["by_norm"][norm_name(bare)], matched_by="name")]
    if bare and " " not in bare and 2 <= len(bare) <= 5 and bare.upper() in idx["by_ticker"] and bare.upper() not in _TICKER_STOP \
            and bare.upper() not in _ENGLISH and bare.lower() not in ALIASES:
        return [dict(idx["by_ticker"][bare.upper()], matched_by="ticker")]
    qn0 = " " + norm_name(text) + " "
    for m in re.finditer(r"\b([A-Z][a-z]{3,4})\b", text):
        w = m.group(1).upper()
        # a word of a person's name ("Tim Cook") is never a ticker, however it is spelt
        beside = re.search(r"\b[A-Z][a-z]+\s*$", text[:m.start()]) or re.match(r"\s*[A-Z][a-z]+\b", text[m.end():])
        if beside:
            continue
        if w in idx["by_ticker"] and w not in _TICKER_STOP and w not in _ENGLISH and w not in _GENERIC_NAMES and m.group(1).lower() not in ALIASES:
            rec = idx["by_ticker"][w]
            if not any(r is rec for _, r, _ in found):
                found.append((m.start(), rec, "ticker"))         # "Googl", "Nvda" typed as a word
    for pos, rec in _alias_hits(idx, text):
        if any(r is rec for _, r, _ in found):
            continue
        # "Apple Hospitality REIT" is written whole: the longer registered name wins over the alias
        alias_word = norm_name(text[pos:pos + 40].split(" ")[0])
        longer = any(" " + n + " " in qn0 for n in idx["names"] if " " in n and n.startswith(alias_word + " ") and n != norm_name(rec["name"])) or \
            any(" " + k + " " in qn0 for k in idx.get("prefix2", {}) if k.startswith(alias_word + " ") and idx["prefix2"][k] is not rec)
        if longer:
            continue
        found.append((pos, rec, "alias"))
    for m in _TICKER_TOKEN.finditer(text):
        tok = m.group(0).upper()
        if tok in _TICKER_STOP or tok not in idx["by_ticker"]:
            continue
        # A one-letter ticker (S, F, C, T) is a company only when written as a
        # ticker on its own ("F", "$F", "F stock") — never inside "S&P", "Q&A"
        # or an initial.
        if len(tok) == 1:
            before, after = text[max(0, m.start() - 1):m.start()], text[m.end():m.end() + 7]
            if not (bare.upper() == tok or before == "$" or re.match(r"\s+(stock|shares)\b", after, re.I)):
                continue
            if re.search(r"(class|series|cl)\s+$", text[:m.start()], re.I):
                continue                                   # "class C shares" is a share class, not Citigroup
            prev_word = (text[:m.start()].split() or [""])[-1].lower().strip(",.'")
            if prev_word in ALIASES or any(prev_word == norm_name(r["name"]).split()[0].lower() for _, r, _ in found if r.get("name")):
                continue                                   # "Berkshire B shares": the class of the company just named
        if re.match(r"[&/]", text[m.end():m.end() + 1]) or (m.start() > 0 and text[m.start() - 1] in "&/"):
            continue
        rec = idx["by_ticker"][tok]
        typed = tok.replace(".", "-")
        if typed != (rec.get("ticker") or "") and typed in (rec.get("tickers") or []):
            rec = dict(rec, ticker=typed, cik=rec["cik"])          # BRK.A, GOOG: that class's prices, the same filer
        if not any(r["cik"] == rec["cik"] and r.get("ticker") == rec.get("ticker") for _, r, _ in found):
            found.append((m.start(), rec, "ticker"))
    qn = " " + norm_name(text) + " "
    lower_q = text == text.lower()
    for n in idx["names"]:
        if len(found) >= limit:
            break
        pos = qn.find(" " + n + " ")
        if pos < 0:
            continue
        if any(how == "alias" and n in norm_name(r["name"]) for _, r, how in found):
            continue
        # A one-word name that is also an English word ("Bill", "People",
        # "Target") counts only when written as a name — capitalised — or when
        # the whole question is typed in lower case.
        if " " not in n and (n in _ENGLISH or len(n) <= 5) and not lower_q:
            if not re.search(r"\b" + re.escape(n[0] + n[1:].lower()) + r"\b|\b" + re.escape(n) + r"\b", text):
                continue
        rec = idx["by_norm"][n]
        if any(r is rec for _, r, _ in found):
            continue
        # a shorter name inside a longer matched one ("ALPHABET" vs "ALPHABET A") is skipped
        if any(n in longer for longer, _, _ in [(x[1]["name"] and norm_name(x[1]["name"]), None, None) for x in found] if longer and n != longer):
            continue
        found.append((pos, rec, "name"))
    if len(found) < limit:
        for key, rec in idx.get("prefix2", {}).items():
            pos = qn.find(" " + key + " ")
            if pos >= 0 and not any(r is rec for _, r, _ in found) and not any(key in norm_name(r["name"]) for _, r, _ in found):
                found.append((pos, rec, "name"))
    if len(found) < limit:
        short = _memo(cur, "short_names", _build_short_names)
        covered = " ".join(norm_name(r["name"]) for _, r, _ in found)
        # A short name is read only from a word written as a name (capitalised,
        # "Ford") — or any word when the whole question is lower case, as people
        # type in a hurry ("who owns ford").
        lower_q = text == text.lower()
        for m in re.finditer(r"[A-Za-z][A-Za-z'\u2019]*", text):
            raw = m.group(0)
            w = norm_name(raw)
            if not w or w not in short or w in covered or any(r is short[w][0] for _, r, _ in found):
                continue
            if w in INDUSTRY_WORD_SET or (w.endswith("S") and w[:-1] in INDUSTRY_WORD_SET):
                continue
            if not (lower_q or raw[0].isupper()):
                continue
            if m.start() == 0 and not lower_q and raw.lower() in _ENGLISH:
                continue
            found.append((m.start(), short[w][0], "short name"))
    found.sort(key=lambda x: x[0])
    return [dict(r, matched_by=how) for _, r, how in found[:limit]]


# ---- CUSIP <-> CIK ---------------------------------------------------------
def _build_cusips(cur):
    """CUSIP -> (cik, name) by normalised issuer name, from the latest 13F window."""
    if not (sec_api._has(cur, "sec_13f_holdings") and sec_api._has(cur, "sec_companies")):
        return {"by_cusip": {}, "by_cik": {}}
    idx = companies(cur)
    # Every spelling managers used for a CUSIP, not only the commonest: the
    # commonest is often an abbreviation ("M & T BK CORP", "FIRST CTZNS
    # BANCSHARES INC D") while some manager spelt the SEC's name in full.
    rows = cur.execute("""
        WITH latest AS (SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 1),
        names AS (
            SELECT cusip, issuer_name, count(*) AS n, sum(value) AS v
            FROM sec_13f_holdings WHERE batch = (SELECT batch FROM latest) AND cusip IS NOT NULL
            GROUP BY cusip, issuer_name),
        totals AS (SELECT cusip, sum(v) AS value FROM names GROUP BY cusip)
        SELECT n.cusip, n.issuer_name, n.n, t.value FROM names n JOIN totals t USING (cusip)
        ORDER BY n.cusip, n.n DESC""").fetchall()
    by_cusip, by_cik = {}, {}
    for cusip, name, n_mgrs, value in rows:
        if cusip in by_cusip:
            continue
        rec = idx["by_norm"].get(norm_name(name))
        if rec is None:
            continue
        by_cusip[cusip] = {"cik": rec["cik"], "name": rec["name"], "issuer_name": name, "value": value}
        by_cik.setdefault(rec["cik"], []).append({"cusip": cusip, "issuer_name": name, "value": value or 0})
    for cik in by_cik:
        by_cik[cik].sort(key=lambda x: -(x["value"] or 0))
    return {"by_cusip": by_cusip, "by_cik": by_cik}


def cusips_for(cur, cik):
    return _build_cusips_memo(cur)["by_cik"].get(int(cik), [])


def cik_for_cusip(cur, cusip):
    rec = _build_cusips_memo(cur)["by_cusip"].get((cusip or "").upper())
    return rec["cik"] if rec else None


def _build_cusips_memo(cur):
    return _memo(cur, "cusips", _build_cusips)


# ---- industries ------------------------------------------------------------
INDUSTRY_WORDS = [
    (("carmaker", "car maker", "automaker", "auto maker", "autos", "automobile", "car company",
      "car companies", "motor vehicle"), ["3711"], "carmakers (SIC 3711)"),
    (("auto parts", "car parts"), ["3714"], "auto parts (SIC 3714)"),
    (("semiconductor", "chipmaker", "chip maker", "chips", "chip"), ["3674"], "semiconductors (SIC 3674)"),
    (("bank", "banks", "lenders"), ["6021", "6022", "6029"], "commercial banks (SIC 6021–6029)"),
    (("software",), ["7372", "7371", "7373", "7374"], "software and services (SIC 7371–7374)"),
    (("pharma", "pharmaceutical", "drugmaker", "drug maker"), ["2834", "2836"], "pharmaceuticals (SIC 2834, 2836)"),
    (("biotech",), ["2836", "8731"], "biotechnology (SIC 2836, 8731)"),
    (("oil", "oil and gas", "energy producer", "petroleum", "refiner", "refiners"),
     ["1311", "2911", "1381", "1382", "1389"], "oil and gas (SIC 1311–1389, 2911)"),
    (("airline", "airlines"), ["4512", "4513"], "airlines (SIC 4512–4513)"),
    (("retailer", "retailers", "retail"), ["53", "54", "56", "57", "59"], "retail trade (SIC 53–59)"),
    (("insurer", "insurers", "insurance"), ["631", "632", "633", "635", "636"], "insurance (SIC 631–636)"),
    (("reit", "reits", "real estate investment"), ["6798"], "REITs (SIC 6798)"),
    (("utility", "utilities", "electric"), ["491", "492", "493"], "utilities (SIC 491–493)"),
    (("telecom", "telecoms", "telephone", "wireless"), ["4812", "4813"], "telecommunications (SIC 4812–4813)"),
    (("restaurant", "restaurants"), ["5812"], "restaurants (SIC 5812)"),
    (("homebuilder", "homebuilders", "home builder"), ["1531"], "homebuilders (SIC 1531)"),
    (("railroad", "railroads", "rail"), ["4011"], "railroads (SIC 4011)"),
    (("aerospace", "defense", "defence"), ["3721", "3724", "3728", "3760"], "aerospace and defence (SIC 3721–3760)"),
    (("medical device", "medical devices", "medtech"), ["3841", "3845"], "medical devices (SIC 3841, 3845)"),
    (("asset manager", "asset managers", "fund manager"), ["6282"], "investment advice (SIC 6282)"),
    (("broker", "brokers", "brokerage"), ["6211"], "brokers and dealers (SIC 6211)"),
    (("miner", "miners", "mining"), ["1000", "1040", "1090", "1220", "1400"], "mining (SIC 10–14)"),
    (("steel",), ["3312"], "steel (SIC 3312)"),
    (("tobacco",), ["2111"], "tobacco (SIC 2111)"),
    (("beverage", "beverages", "drinks"), ["2080", "2086"], "beverages (SIC 2080, 2086)"),
    (("grocer", "grocers", "supermarket", "supermarkets"), ["5411"], "grocery stores (SIC 5411)"),
    (("hotel", "hotels", "casino", "casinos", "resorts"), ["7011", "7990"], "hotels and gaming (SIC 7011, 7990)"),
    (("trucking", "truckers"), ["4213"], "trucking (SIC 4213)"),
    (("shipping", "shippers"), ["4412"], "deep-sea shipping (SIC 4412)"),
    (("apparel", "clothing"), ["2300", "5600", "5651"], "apparel (SIC 23, 56)"),
    (("computer", "computers", "hardware"), ["3571", "3572", "3576", "3577"], "computer hardware (SIC 3571–3577)"),
    (("internet", "online"), ["7370", "7374", "5961"], "internet services and retail (SIC 7370, 7374, 5961)"),
]


# every word that names an industry above: never read as a company's short name
# ("pharma" is not Royalty Pharma, "restaurant" is not Restaurant Brands)
INDUSTRY_WORD_SET = {w.upper() for words, _, _ in INDUSTRY_WORDS for phrase in words for w in phrase.split()} | \
                    {"DEFENSE", "DEFENCE", "CONTRACTORS", "CHAINS", "STOCKS", "STOCK", "COMPANIES", "COMPANY", "SECTOR", "INDUSTRY"}


def resolve_industry(cur, question):
    """(sic prefixes, label) for an industry named in the question, or None."""
    hit = industry_match(cur, question)
    return (hit[0], hit[1]) if hit else None


def industry_match(cur, question):
    """(sic prefixes, label, the question's words that named it), or None."""
    q = " " + re.sub(r"[^a-z0-9 ]", " ", (question or "").lower()) + " "
    best = None
    for words, codes, label in INDUSTRY_WORDS:
        for w in words:
            if " " + w + " " in q or " " + w + "s " in q:
                if best is None or len(w) > len(best[2]):
                    best = (codes, label, w)
    if best:
        return best[0], best[1], [best[2], best[2] + "s"]
    if sec_api._has(cur, "sec_sic"):
        titles = cur.execute("SELECT sic, industry FROM sec_sic").fetchall()
        hits, used = [], set()
        for sic, title in titles:
            t = (title or "").lower()
            words = [w for w in re.findall(r"[a-z]{5,}", t) if w not in ("services", "products", "general", "related", "other")]
            # whole words only: "meta" must not find METAL CANS; a plural title
            # word ("computers") also matches its singular
            if words and all(re.search(r"\b%s\b" % (w[:-1] + "s?" if w.endswith("s") else w + "s?"), q) for w in words[:2]):
                hits.append((str(sic), title))
                used.update(w2 for w in words[:2] for w2 in (w, w[:-1] if w.endswith("s") else w + "s"))
        if hits and len(hits) <= 6:
            return [h[0] for h in hits], " / ".join(h[1].title() for h in hits[:3]), sorted(used)
    return None


# ---- managers --------------------------------------------------------------
# people asked about by name, and the manager that files their 13F
MANAGER_PEOPLE = {"bill ackman": "PERSHING SQUARE", "ackman": "PERSHING SQUARE", "soros": "SOROS FUND MANAGEMENT", "george soros": "SOROS FUND MANAGEMENT",
                  "michael burry": "SCION ASSET MANAGEMENT", "burry": "SCION ASSET MANAGEMENT", "warren buffett": "BERKSHIRE HATHAWAY", "buffett": "BERKSHIRE HATHAWAY",
                  "ray dalio": "BRIDGEWATER ASSOCIATES", "dalio": "BRIDGEWATER ASSOCIATES", "ken griffin": "CITADEL ADVISORS", "griffin": "CITADEL ADVISORS",
                  "cathie wood": "ARK INVESTMENT MANAGEMENT", "ark invest": "ARK INVESTMENT MANAGEMENT", "ark": "ARK INVESTMENT MANAGEMENT",
                  "david tepper": "APPALOOSA", "tepper": "APPALOOSA", "carl icahn": "ICAHN CARL C", "icahn": "ICAHN CARL C", "dan loeb": "THIRD POINT",
                  "daniel loeb": "THIRD POINT", "seth klarman": "BAUPOST GROUP", "klarman": "BAUPOST GROUP", "druckenmiller": "DUQUESNE FAMILY OFFICE",
                  "stanley druckenmiller": "DUQUESNE FAMILY OFFICE", "jim simons": "RENAISSANCE TECHNOLOGIES", "david einhorn": "GREENLIGHT CAPITAL",
                  "einhorn": "GREENLIGHT CAPITAL", "howard marks": "OAKTREE CAPITAL MANAGEMENT", "paul singer": "ELLIOTT INVESTMENT MANAGEMENT",
                  "elliott": "ELLIOTT INVESTMENT MANAGEMENT", "nelson peltz": "TRIAN FUND MANAGEMENT", "peltz": "TRIAN FUND MANAGEMENT",
                  "chase coleman": "TIGER GLOBAL MANAGEMENT", "tiger global": "TIGER GLOBAL MANAGEMENT", "steve cohen": "POINT72 ASSET MANAGEMENT",
                  "point72": "POINT72 ASSET MANAGEMENT", "millennium": "MILLENNIUM MANAGEMENT", "izzy englander": "MILLENNIUM MANAGEMENT",
                  "norges": "NORGES BANK", "norway's oil fund": "NORGES BANK", "norwegian sovereign wealth fund": "NORGES BANK",
                  "saudi pif": "PUBLIC INVESTMENT FUND", "temasek": "TEMASEK HOLDINGS", "gic": "GIC PRIVATE", "softbank": "SOFTBANK GROUP"}
_MANAGER_PEOPLE_RE = re.compile(r"(?<![\w])(" + "|".join(sorted((re.escape(k) for k in MANAGER_PEOPLE), key=len, reverse=True)) + r")(?![\w])", re.I)
_MANAGER_ASKS = re.compile(r"\b(portfolio|positions?|holdings?|added|dropped|manager|fund|funds|sold|sell|sells|selling|bought|buy|buys|buying|"
                           r"stake|trimmed|exited|13f|owns?|holds?|largest holding|biggest holding|top holdings?)\b")
_MANAGER_WORDS = {"PORTFOLIO", "CAPITAL", "MANAGEMENT", "ADVISORS", "ADVISERS", "PARTNERS", "INVESTMENT", "INVEST", "INVESTING",
                  "INVESTMENTS", "INVESTORS", "ASSET", "ASSETS", "WEALTH", "FINANCIAL", "SECURITIES", "HOLDINGS",
                  "POSITIONS", "HOLDING", "MANAGERS", "FUNDS", "STRATEGIES", "STRATEGY", "RESEARCH", "GLOBAL"}


def manager_by_cik(cur, cik, name):
    """(cik, name) when the company itself files Form 13F (Berkshire Hathaway does), else None."""
    if not sec_api._has(cur, "sec_13f_filings"):
        return None
    try:
        hit = cur.execute("SELECT arg_max(manager_name, filed) FROM sec_13f_filings WHERE manager_cik = ?", [int(cik)]).fetchone()
    except Exception:                                             # noqa: BLE001
        return None
    return (int(cik), hit[0] or name) if hit and hit[0] else None


def resolve_manager(cur, question):
    """A 13F manager named in the question: (manager_cik, name) or None."""
    if not sec_api._has(cur, "sec_13f_filings"):
        return None
    pm = _MANAGER_PEOPLE_RE.search(question or "")
    if pm:
        question = MANAGER_PEOPLE[pm.group(1).lower()]              # "Bill Ackman" -> Pershing Square
    qn = norm_name(re.sub(r"(\w)['\u2019]s\b", r"\1", question or ""))      # Vanguard's -> Vanguard
    if not qn:
        return None
    rows = cur.execute("""
        SELECT manager_cik, arg_max(manager_name, filed) AS name, max(table_value) AS v
        FROM sec_13f_filings WHERE NOT amendment GROUP BY manager_cik ORDER BY v DESC NULLS LAST LIMIT 4000""").fetchall()
    best = None
    for cik, name, v in rows:
        n = norm_name(name)
        # a name that is nothing but trade words ("Asset Management") matches any
        # question with those words in it: "Scion Asset Management" found Trust
        # Asset Management LLC after Scion stopped filing
        if len(n) < 4 or all(w in _MANAGER_WORDS or w in _GENERIC_NAMES or len(w) < 3 for w in n.split()):
            continue
        if " " + n + " " in " " + qn + " ":
            if best is None or len(n) > len(best[2]):
                best = (cik, name, n)
    if best:
        return best[0], best[1]
    # "Berkshire" alone: the largest manager whose name starts with the word —
    # a name word, never an ordinary one ("portfolio", "capital", "partners")
    for word in [w for w in qn.split() if len(w) >= 5 and w not in _ENGLISH and w not in _GENERIC_NAMES and w not in _MANAGER_WORDS]:
        for cik, name, v in rows:                  # rows are largest first
            if (" " + word + " ") in (" " + norm_name(name) + " "):
                return cik, name
    return None


# ---- money and time --------------------------------------------------------
_MONEY = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(tn|trillion|bn|billion|b|mn|million|m|k|thousand)?\b", re.I)
_MULT = {"tn": 1e12, "trillion": 1e12, "bn": 1e9, "billion": 1e9, "b": 1e9, "mn": 1e6, "million": 1e6,
         "m": 1e6, "k": 1e3, "thousand": 1e3}


def parse_money(question):
    """The first money-like amount in a question, in dollars; None if none.
    A bare number without a unit is ignored (years, counts)."""
    for m in _MONEY.finditer(question or ""):
        unit = (m.group(2) or "").lower()
        if not unit and not m.group(0).strip().startswith("$"):
            continue
        return float(m.group(1)) * _MULT.get(unit, 1.0)
    return None


def parse_count(question, default=10):
    m = re.search(r"\b(top|largest|biggest|first)\s+(\d{1,3})\b|\b(\d{1,3})\s+(largest|biggest|top)\b", question or "", re.I)
    if m:
        return int(m.group(2) or m.group(3))
    words = {"ten": 10, "five": 5, "twenty": 20, "fifteen": 15, "three": 3}
    m = re.search(r"\b(ten|five|twenty|fifteen|three)\b", (question or "").lower())
    return words[m.group(1)] if m else default


def parse_index(question):
    """The broad index a question ranks within: 'sp500', 'dow', 'nasdaq100',
    'russell2000' … (app/us_benchmarks.py); None otherwise."""
    from . import us_benchmarks
    q = (question or "").lower()
    hits = []
    for key in us_benchmarks.UNIVERSES:
        m = re.search(us_benchmarks.BENCHMARKS[key]["words"], q)
        if m:
            hits.append((m.start(), key))
    return min(hits)[1] if hits else None


def parse_until(question, today=None):
    """The end of a named past year ('in 2025' -> 31 Dec 2025); None otherwise."""
    today = today or datetime.date.today()
    m = re.search(r"\b(?:in|during|for|over)\s+((?:19|20)\d{2})\b", (question or "").lower())
    if m and int(m.group(1)) < today.year:
        return datetime.date(int(m.group(1)), 12, 31)
    return None


def parse_since(question, today=None):
    """A start date from time words; None when the question names none."""
    today = today or datetime.date.today()
    q = (question or "").lower()
    m = re.search(r"\bsince\s+((?:19|20)\d{2})\b", q)
    if m:
        return datetime.date(int(m.group(1)), 1, 1)
    m = re.search(r"\b(?:in|during|for|over)\s+((?:19|20)\d{2})\b", q)
    if m and int(m.group(1)) <= today.year:
        return datetime.date(int(m.group(1)), 1, 1)
    if "this month" in q:
        return today.replace(day=1)
    if "this year" in q or "year to date" in q or "ytd" in q:
        return today.replace(month=1, day=1)
    if "this week" in q or "past week" in q or "last week" in q:
        return today - datetime.timedelta(days=7)
    if "last month" in q or "past month" in q:
        return _months_back(today, 1)
    if "last quarter" in q:
        return today - datetime.timedelta(days=92)
    # any span back from today: "past 5 months", "last two weeks", "previous 90 days", "over the past year", "last 18 months"
    m = re.search(r"\b(?:past|last|previous|prior|trailing)\s+(?:(\d{1,3}|%s|a|one)\s+)?(days?|weeks?|months?|quarters?|years?|decade)\b" % _NUM_ALT, q) or \
        re.search(r"\b(?:over|in|across|during|for|within)\s+(?:the\s+)?(?:past\s+|last\s+)?(\d{1,3}|%s)[- ](days?|weeks?|months?|quarters?|years?)\b" % _NUM_ALT, q) or \
        re.search(r"\bsince\s+(\d{1,3}|%s|a)\s+(days?|weeks?|months?|quarters?|years?)\s+ago\b" % _NUM_ALT, q)
    if m:
        v, unit = m.group(1), m.group(2)
        n = 1 if v in (None, "a", "one") else (int(v) if v.isdigit() else _NUM_WORDS[v])
        if n and unit.startswith("day"):
            return today - datetime.timedelta(days=n)
        if n and unit.startswith("week"):
            return today - datetime.timedelta(days=7 * n)
        if n and unit.startswith(("month", "quarter")):
            return _months_back(today, n * (3 if unit.startswith("quarter") else 1))
        if n and unit.startswith(("year", "decade")):
            return today - datetime.timedelta(days=365 * n * (10 if unit == "decade" else 1))
    # "since March", "since June 2025", "since last March"
    m = re.search(r"\bsince\s+(?:last\s+)?(%s)\b(?:\s+((?:19|20)\d{2}))?" % "|".join(sorted(_MONTHS, key=len, reverse=True)), q)
    if m:
        mo = _MONTHS[m.group(1)]
        y = int(m.group(2)) if m.group(2) else (today.year if mo <= today.month else today.year - 1)
        return datetime.date(y, mo, 1)
    return None


def _months_back(today, n):
    """The same day n calendar months earlier (clamped to the month's end)."""
    y, mo = divmod(today.year * 12 + today.month - 1 - n, 12)
    for d in (today.day, 30, 29, 28):
        try:
            return datetime.date(y, mo + 1, d)
        except ValueError:
            continue


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20,
              "thirty": 30, "sixty": 60, "ninety": 90}
_NUM_ALT = "|".join(sorted(_NUM_WORDS, key=len, reverse=True))

# Every phrase that names a span of time. A phrase found here that no parser
# reads is a question we would otherwise answer over a default period.
TIME_PHRASE = re.compile(
    r"\b(?:(?:in|over|during|for|within|across)\s+)?(?:the\s+)?(?:past|last|previous|prior|trailing|recent|next|coming|few|couple of|several)\s+"
    r"(?:(?:\d{1,3}|%(n)s|a|one|few|couple of|several)\s+)?(?:days?|weeks?|months?|quarters?|years?|decades?|sessions?|trading days?)\b|"
    r"\b(?:\d{1,3}|%(n)s)[- ](?:days?|weeks?|months?|quarters?|years?|decades?|sessions?|trading days?)\b(?:\s+ago)?|"
    r"\bsince\s+[a-z0-9]+(?:\s+[a-z0-9]+){0,2}|"
    r"\b(?:this|last|next)\s+(?:week|month|quarter|year)\b|\bytd\b|\byear to date\b|\bmonth to date\b|\bmtd\b|\bqtd\b|"
    r"\bin\s+(?:19|20)\d{2}\b|\byesterday\b|\btoday\b" % {"n": _NUM_ALT})


def unread_time(question, today=None):
    """The time phrases in a question that no time parser here reads ("over
    the past few months", "since the election"); [] when every one is read."""
    q = (question or "").lower()
    out = []
    for m in TIME_PHRASE.finditer(q):
        p = m.group(0).strip()
        if re.fullmatch(r"(?:in\s+|over\s+|during\s+|for\s+)?(?:the\s+)?(?:this|last|next|past)\s+(?:week|month|quarter|year)|today|yesterday|ytd|year to date|"
                        r"since (?:its |the |their )?(?:ipo|listing|inception|beginning|start|it listed|it went public)(?: [a-z0-9]+)*", p):
            continue                      # read by parse_since, parse_ahead_days or the shapes' own grammars
        if re.match(r"(?:in\s+|over\s+|for\s+|during\s+)?(?:the\s+)?(?:next|coming)\b", p) and \
                re.search(r"next\s+(\d+|two|three|four|one)\s+(week|weeks|day|days|month|months)", p):
            continue                      # a window ahead, read by parse_ahead_days
        if parse_since(p, today) or parse_year_window(p, today) or (parse_years(p) and not re.search(r"\bago\b", p)):
            continue
        out.append(p)
    return out


_YEAR = r"((?:19|20)\d{2})"


def parse_year_window(question, today=None):
    """(since, until) when the question names a year or a span of years for a
    figure: "in 2019", "fiscal 2023", "FY2024", "2015 vs 2025", "2015-2025",
    "between 2015 and 2025", "five years ago". Both dates, or None."""
    today = today or datetime.date.today()
    q = (question or "").lower()
    m = re.search(r"\b" + _YEAR + r"\s*(?:-|–|to|vs\.?|versus|and|through|until)\s*" + _YEAR + r"\b", q)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return datetime.date(a, 1, 1), datetime.date(b, 12, 31)
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\s+ago\b", q)
    if m:
        v = m.group(1)
        n = int(v) if v.isdigit() else _NUM_WORDS[v]
        y = today.year - n
        return datetime.date(y, 1, 1), datetime.date(y, 12, 31)
    m = re.search(r"\b(?:fy|fiscal(?: year)?)\s*'?(\d{2}|(?:19|20)\d{2})\b", q)
    if m:
        y = int(m.group(1))
        y = y + 2000 if y < 100 else y
        return datetime.date(y, 1, 1), datetime.date(y, 12, 31)
    m = re.search(r"\b(?:in|during|for|of)\s+" + _YEAR + r"\b|\b" + _YEAR + r"\s+(?:revenue|sales|profit|net income|earnings|results|figures?)\b|"
                  r"(?:revenue|sales|profits?|income|earnings|cash|flow|margin|eps|assets|equity|capex|dividends?|buybacks?|results)\s+(?:in\s+|for\s+)?" + _YEAR + r"\s*$", q)
    if m:
        y = int(m.group(1) or m.group(2) or m.group(3))
        if y <= today.year:
            return datetime.date(y, 1, 1), datetime.date(y, 12, 31)
    return None


_MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"])}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def parse_weekday(question, today=None, ahead=True):
    """The date of "Thursday", "next Thursday" (ahead) or "last Friday" (behind), or None."""
    today = today or datetime.date.today()
    q = (question or "").lower()
    m = re.search(r"\b(last|next|this|on)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", q)
    if not m:
        return None
    wd = _WEEKDAYS[m.group(2)]
    if m.group(1) == "last" or not ahead:
        back = (today.weekday() - wd) % 7 or 7
        return today - datetime.timedelta(days=back)
    fwd = (wd - today.weekday()) % 7
    return today + datetime.timedelta(days=fwd)


def parse_date(question):
    """One calendar day written in the question ("23 March 2020", "March 23, 2020", "2020-03-23"), or None."""
    q = (question or "").lower()
    if re.search(r"\byesterday\b", q):
        return datetime.date.today() - datetime.timedelta(days=1)
    if re.search(r"\blast (monday|tuesday|wednesday|thursday|friday)\b", q):
        return parse_weekday(q, ahead=False)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+((?:19|20)\d{2})\b", q)
    if m and m.group(2) in _MONTHS:
        try:
            return datetime.date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b", q)
    if m and m.group(1) in _MONTHS:
        try:
            return datetime.date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
    m = re.search(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b", q)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_years(question):
    """'5 year', 'five years', 'last 10 years', 'a decade' -> a count; None if none."""
    q = (question or "").lower()
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty)[- ]?(?:years?|yrs?)\b", q)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else _NUM_WORDS[v]
    if re.search(r"\bdecade\b", q):
        return 10
    return None


def parse_ahead_days(question, default=14):
    q = (question or "").lower()
    if re.search(r"\btomorrow\b|\btoday\b", q):
        return 1
    if re.search(r"\bthis week\b|\bnext week\b", q):
        return 7
    if re.search(r"\bthis month\b|\bnext month\b", q):
        return 30
    m = re.search(r"next\s+(\d+|two|three|four|one)\s+(week|weeks|day|days|month|months)", q)
    if not m:
        return default
    n = {"one": 1, "two": 2, "three": 3, "four": 4}.get(m.group(1), None) or int(m.group(1))
    unit = m.group(2)
    return n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
