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
                "BY", "CEO", "CFO", "ETF", "IPO", "USD", "NYSE", "SEC", "EPS", "ROE", "ROA", "Q", "FY",
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
               "NXP", "TJX", "EOG", "DTE", "CME", "ICE", "AMC", "GE", "GM", "HCA", "IQVIA", "RTX", "NRG", "ON", "WEC"}
_MIXED = {"JPMORGAN": "JPMorgan", "NVIDIA": "Nvidia", "PAYPAL": "PayPal", "EBAY": "eBay", "IPHONE": "iPhone",
          "MCDONALDS": "McDonald's", "MCDONALD'S": "McDonald's", "BLACKROCK": "BlackRock", "FEDEX": "FedEx",
          "DUPONT": "DuPont", "MASTERCARD": "Mastercard", "LINKEDIN": "LinkedIn", "YOUTUBE": "YouTube"}


def display_name(name):
    """The SEC's company name as people write it: 'FEDERAL SIGNAL CORP /DE/'
    -> 'Federal Signal Corp'. Names already in mixed case are kept."""
    n = re.sub(r"\s*/[A-Z]{2,4}/?\s*$", "", (name or "").strip())
    n = re.sub(r",?\s+/\w+/?$", "", n)
    if n != n.upper():
        return n
    out = []
    for w in n.split():
        core = w.strip(",.")
        if core in _MIXED:
            out.append(w.replace(core, _MIXED[core]))
        elif core in _KEEP_UPPER or "&" in core or (len(core) <= 3 and not any(ch in "AEIOU" for ch in core)):
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


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
    return {"by_ticker": by_ticker, "by_norm": by_norm, "names": names, "by_cik": by_cik}


_GENERIC_NAMES = {"TEN", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TOP", "BEST",
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


def find_companies(cur, question, limit=3):
    """Companies mentioned in a question, in order of appearance: ticker
    tokens written in capitals first, then names as whole words, then a
    distinctive first word of a name ("Ford")."""
    idx = companies(cur)
    found = []
    text = re.sub(r"(\w)['\u2019]s\b", r"\1", question or "")      # Nvidia's -> Nvidia
    bare = text.strip().strip("?.!").strip()
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
        if re.match(r"[&/]", text[m.end():m.end() + 1]) or (m.start() > 0 and text[m.start() - 1] in "&/"):
            continue
        rec = idx["by_ticker"][tok]
        if rec not in found:
            found.append((m.start(), rec, "ticker"))
    qn = " " + norm_name(text) + " "
    lower_q = text == text.lower()
    for n in idx["names"]:
        if len(found) >= limit:
            break
        pos = qn.find(" " + n + " ")
        if pos < 0:
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


def resolve_industry(cur, question):
    """(sic prefixes, label) for an industry named in the question, or None."""
    q = " " + re.sub(r"[^a-z0-9 ]", " ", (question or "").lower()) + " "
    best = None
    for words, codes, label in INDUSTRY_WORDS:
        for w in words:
            if " " + w + " " in q or " " + w + "s " in q:
                if best is None or len(w) > len(best[2]):
                    best = (codes, label, w)
    if best:
        return best[0], best[1]
    if sec_api._has(cur, "sec_sic"):
        titles = cur.execute("SELECT sic, industry FROM sec_sic").fetchall()
        hits = []
        for sic, title in titles:
            t = (title or "").lower()
            words = [w for w in re.findall(r"[a-z]{5,}", t) if w not in ("services", "products", "general", "related", "other")]
            # whole words only: "meta" must not find METAL CANS; a plural title
            # word ("computers") also matches its singular
            if words and all(re.search(r"\b%s\b" % (w[:-1] + "s?" if w.endswith("s") else w + "s?"), q) for w in words[:2]):
                hits.append((str(sic), title))
        if hits and len(hits) <= 6:
            return [h[0] for h in hits], " / ".join(h[1].title() for h in hits[:3])
    return None


# ---- managers --------------------------------------------------------------
_MANAGER_WORDS = {"PORTFOLIO", "CAPITAL", "MANAGEMENT", "ADVISORS", "ADVISERS", "PARTNERS", "INVESTMENT",
                  "INVESTMENTS", "INVESTORS", "ASSET", "ASSETS", "WEALTH", "FINANCIAL", "SECURITIES", "HOLDINGS",
                  "POSITIONS", "HOLDING", "MANAGERS", "FUNDS", "STRATEGIES", "STRATEGY", "RESEARCH", "GLOBAL"}


def resolve_manager(cur, question):
    """A 13F manager named in the question: (manager_cik, name) or None."""
    if not sec_api._has(cur, "sec_13f_filings"):
        return None
    qn = norm_name(question)
    if not qn:
        return None
    rows = cur.execute("""
        SELECT manager_cik, arg_max(manager_name, filed) AS name, max(table_value) AS v
        FROM sec_13f_filings WHERE NOT amendment GROUP BY manager_cik ORDER BY v DESC NULLS LAST LIMIT 4000""").fetchall()
    best = None
    for cik, name, v in rows:
        n = norm_name(name)
        if len(n) < 4:
            continue
        if " " + n + " " in " " + qn + " ":
            if best is None or len(n) > len(best[2]):
                best = (cik, name, n)
    if best:
        return best[0], best[1]
    # "Berkshire" alone: the largest manager whose name starts with the word —
    # a name word, never an ordinary one ("portfolio", "capital", "partners")
    for word in [w for w in qn.split() if len(w) >= 6 and w not in _ENGLISH and w not in _GENERIC_NAMES and w not in _MANAGER_WORDS]:
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
    if re.search(r"\b(past|last)\s+(12 months|twelve months|year|one year|1 year)\b", q) or "over the past year" in q:
        return today - datetime.timedelta(days=365)
    if re.search(r"\b(past|last)\s+(three|3) months\b", q) or "last quarter" in q:
        return today - datetime.timedelta(days=92)
    if re.search(r"\b(past|last)\s+(six|6) months\b", q):
        return today - datetime.timedelta(days=183)
    if re.search(r"\b(past|last)\s+(two|2|five|5|ten|10) years\b", q):
        n = {"two": 2, "2": 2, "five": 5, "5": 5, "ten": 10, "10": 10}[re.search(r"\b(past|last)\s+(\w+) years\b", q).group(2)]
        return today - datetime.timedelta(days=365 * n)
    return None


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20}


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
    m = re.search(r"next\s+(\d+|two|three|four|one)\s+(week|weeks|day|days|month|months)", q)
    if not m:
        return default
    n = {"one": 1, "two": 2, "three": 3, "four": 4}.get(m.group(1), None) or int(m.group(1))
    unit = m.group(2)
    return n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
