# -*- coding: utf-8 -*-
u"""Reads a plain question about a US price or a Treasury yield, every word.

The catalogue in preview.py picks a series from one word and shows its latest
reading, so "how much has ground beef gone up?" got today's price and "gasoline
price" got the inflation rate. Here the series comes from the item or maturity
named, and what is asked of it comes from the other words: the price, its change
since a date, its high or low, the last time it passed a level, its year. A word
nothing reads is named in the answer, never quietly dropped.

Everything comes through preview._series_rows, the same numbers the pages show.
Prices are as the BLS publishes them, in the dollars of the day; a change is
calculated from them and carries its formula.
"""
import datetime
import re
import time

# the item a word means: (words, BLS average-price series). First match wins, so
# "peanut butter" comes before "butter". Series the BLS has stopped are kept: the
# answer's date says how old the reading is.
ITEMS = [
    (r"premium (?:gas|gasoline)|gas(?:oline)? premium", "APU000074716"),
    (r"mid-?grade", "APU000074715"),
    (r"diesel", "APU000074717"),
    (r"(?:heating|fuel) oil", "APU000072511"),
    (r"natural gas|piped gas|utility gas", "APU000072620"),
    (r"gasoline|\bgas\b|petrol", "APU000074714"),
    (r"electricity|electric power", "APU000072610"),
    (r"\beggs?\b", "APU0000708111"),
    (r"(?:skim|low.fat|reduced.fat) milk", "APU0000FJ1101"),
    (r"\bmilk\b", "APU0000709112"),
    (r"whole wheat bread|wheat bread", "APU0000702212"),
    (r"\bbread\b", "APU0000702111"),
    (r"ground chuck", "APU0000703111"),
    (r"lean ground beef", "APU0000703113"),
    (r"stew(?:ing)? beef|beef (?:for )?stew", "APU0000703432"),
    (r"chuck roast", "APU0000703213"),
    (r"beef roast|roast beef|round roast", "APU0000703311"),
    (r"round steak", "APU0000703511"),
    (r"\bsteaks?\b|sirloin", "APU0000703613"),
    (r"ground beef|hamburgers?|\bburgers?\b|\bbeef\b", "APU0000703112"),
    (r"chicken breasts?", "APU0000FF1101"),
    (r"chicken legs?", "APU0000706212"),
    (r"\bchickens?\b", "APU0000706111"),
    (r"\bturkeys?\b", "APU0000706311"),
    (r"pork chops?|\bpork\b", "APU0000FD3101"),
    (r"\bbacon\b", "APU0000704111"),
    (r"\bham\b", "APU0000704312"),
    (r"\bsausages?\b", "APU0000704421"),
    (r"hot ?dogs?|frankfurters?", "APU0000705111"),
    (r"\bbologna\b", "APU0000705121"),
    (r"\blamb\b", "APU0000705142"),
    (r"\btuna\b", "APU0000707111"),
    (r"instant coffee", "APU0000717327"),
    (r"\bcoffee\b", "APU0000717311"),
    (r"orange juice|\boj\b", "APU0000713111"),
    (r"\boranges?\b", "APU0000711311"),
    (r"\bbananas?\b", "APU0000711211"),
    (r"\bapples\b", "APU0000711111"),
    (r"\blemons?\b", "APU0000711412"),
    (r"\bgrapefruits?\b", "APU0000711411"),
    (r"\bgrapes\b", "APU0000711417"),
    (r"\bpeach(?:es)?\b", "APU0000711414"),
    (r"\bpears?\b", "APU0000711413"),
    (r"\bcherr(?:y|ies)\b", "APU0000711418"),
    (r"\bstrawberr(?:y|ies)\b", "APU0000711415"),
    (r"\btomato(?:es)?\b", "APU0000712311"),
    (r"potato chips", "APU0000718311"),
    (r"french fries", "APU0000714111"),
    (r"\bpotato(?:es)?\b", "APU0000712112"),
    (r"romaine", "APU0000FL2101"),
    (r"\blettuce\b", "APU0000712211"),
    (r"\bcarrots?\b", "APU0000712403"),
    (r"\bcabbages?\b", "APU0000712401"),
    (r"\bcelery\b", "APU0000712402"),
    (r"\bbroccoli\b", "APU0000712412"),
    (r"\bonions?\b", "APU0000712404"),
    (r"\bcucumbers?\b", "APU0000712409"),
    (r"\b(?:sweet )?peppers\b", "APU0000712406"),
    (r"\bmushrooms?\b", "APU0000712411"),
    (r"peanut butter", "APU0000716141"),
    (r"\bbutter\b", "APU0000FS1101"),
    (r"\bmargarine\b", "APU0000716116"),
    (r"american cheese|processed cheese", "APU0000710211"),
    (r"\bcheddar\b|\bcheese\b", "APU0000710212"),
    (r"\byog(?:h)?urt\b", "APU0000FJ4101"),
    (r"ice cream", "APU0000710411"),
    (r"\bflour\b", "APU0000701111"),
    (r"\brice\b", "APU0000701312"),
    (r"\bpasta\b|\bspaghetti\b|\bmacaroni\b", "APU0000701322"),
    (r"\bsugar\b", "APU0000715211"),
    (r"\bcookies?\b", "APU0000702421"),
    (r"\bbeans\b", "APU0000714233"),
    (r"\bcorn\b", "APU0000714221"),
    (r"\bbeers?\b", "APU0000720111"),
    (r"\bwines?\b", "APU0000720311"),
    (r"\bvodka\b", "APU0000720222"),
    (r"\bsodas?\b|soft drinks?|\bpop\b", "APU0000FN1101"),
]
ENERGY = {"APU000074716", "APU000074715", "APU000074717", "APU000072511", "APU000072620", "APU000074714", "APU000072610"}

_NOT_THE_ITEM = re.compile(r"\b(stocks?|shares?|etfs?|futures?|funds?|dividends?|earnings|revenues?|profits?|market (?:cap|value)|ceos?|"
                           r"companies|company|producers?|suppliers?|tickers?|mortgages?|tips? for)\b")
_ASK_PRICE = re.compile(r"\b(prices?|priced|costs?|costing|expensive|cheap(?:er|est)?|how much|dollars?|inflation)\b|\bper (?:gallon|pound|lb|dozen|kwh|therm)\b|\ba (?:gallon|pound|dozen)\b")
_ASK_CHANGE = re.compile(r"\b(gone up|go up|goes up|went up|going up|gone down|go down|goes down|went down|going down|coming down|risen|rose|rise|rises|rising|"
                         r"increase[sd]?|increasing|climbed|jumped|fallen|fell|fall|falls|falling|dropped|drop|declined?|decreased?|changed?|up or down|"
                         r"more expensive|less expensive|cheaper|(?:a )?year ago|last year|history|over time|trend|versus|vs\.?|compared?|inflation)\b|"
                         r"\bsince\b|\b(?:this|past|last) (?:\d+ )?(?:year|month|week|decade)s?\b|\bytd\b|\byear to date\b|\b\d+ (?:years?|months?) ago\b|\bfrom a (?:year|month) ago\b")
_HIGH = re.compile(r"\b(highest|peak(?:ed)?|record(?: high)?|most expensive|all.time high|the most it has)\b")
_LOW = re.compile(r"\b(lowest|cheapest|least expensive|all.time low|record low|bottom(?:ed)?)\b")
_UP_ASKED = re.compile(r"\b(up|risen|rose|rise|rises|rising|increase[sd]?|increasing|climbed|jumped|more expensive|higher)\b")
_DOWN_ASKED = re.compile(r"\b(down|fallen|fell|fall|falls|falling|dropped|declined?|decreased?|cheaper|less expensive|lower)\b")

# ------------------------------------------------------------ maturities
_NUM = {"one": 1, "two": 2, "three": 3, "five": 5, "six": 6, "seven": 7, "ten": 10, "twenty": 20, "thirty": 30}
_TENOR = re.compile(r"(?<![\d.$,])(\d{1,2}|one|two|three|five|six|seven|ten|twenty|thirty)[- ]?(years?|yrs?|y|months?|mos?)\b")
_YEARS, _MONTHS = {1, 2, 3, 5, 7, 10, 20, 30}, {1, 3, 6}
_PAIR = re.compile(r"(?<![\d.$,])(\d{1,2})s?\s*(?:and|vs\.?|versus|or|to|-)\s*(?:the\s+)?(\d{1,2})s?(?:[- ]?(?:years?|yrs?|y))?\b")
_LONG_BOND = re.compile(r"\b(?:the )?long (?:bond|end)\b")
_BILLS = re.compile(r"\bt-?bills?\b|\btreasury bills?\b")
# the words a maturity may stand among and still mean a Treasury yield: "Has the
# 30 year gone up?" does, "5 year return on Apple" does not
_TENOR_COMPANY = re.compile(
    r"^(?:the|a|an|what|whats|what's|is|was|were|are|has|have|had|did|do|does|how|much|where|gone|go|going|went|up|down|risen|rose|rise|rising|fallen|fell|fall|falling|"
    r"change|changed|high|higher|highest|low|lower|lowest|peak|peaked|when|last|above|over|below|under|more|less|than|vs|versus|and|or|difference|between|spread|gap|"
    r"this|that|year|years|month|months|week|ago|today|now|current|currently|latest|right|in|since|treasury|treasuries|us|yield|yields|rate|rates|bond|bonds|note|notes|at|on|of|"
    r"from|to|compared|compare|with|against|ever|record|time|it|its|it's|percent|tips|real|ytd|\d+(?:\.\d+)?%?|\d{4}|january|february|march|april|may|june|july|august|"
    r"september|october|november|december)$")

_FRAME = ("how much did does do is are was were has have the a an of for at in on what whats what's it its it's cost costs price prices priced us america american average national "
          "today now currently current latest right this that last past year years month months week ago up down gone go going went since from to than a per gallon pound lb dozen "
          "kwh therm more less expensive cheaper cheap cheapest highest lowest peak peaked record ever all time when were most least been change changed risen rose rise rising fallen "
          "fell fall falling dropped increase increased decrease decreased compare compared vs versus and or with against history trend over inflation will still much many they them "
          "get got getting costing dollars ytd date about s above below under exceeded higher lower top by back").split()
_MONTH_WORDS = "january february march april may june july august september october november december covid pandemic".split()


def _words(q):
    return re.findall(r"[a-z0-9$%.'-]+", (q or "").lower().replace("’", "'"))


def _item(ql):
    for pat, code in ITEMS:
        m = re.search(pat, ql)
        if m:
            return code, m.group(0)
    return None, None


def _is_bare(ql, word):
    rest = re.sub(re.escape(word), " ", ql)
    return not re.sub(r"\b(the|a|an|us|american|what about|and|how about|price)\b|[?.!,]", " ", rest).strip()


def item_of(ql):
    """(code, word) when the question asks about a household price."""
    if _NOT_THE_ITEM.search(ql):
        return None, None
    code, word = _item(ql)
    if not code:
        return None, None
    plain = _ASK_PRICE.search(ql) or _ASK_CHANGE.search(ql) or _HIGH.search(ql) or _LOW.search(ql) or _is_bare(ql, word) or \
        re.search(r"\b(?:above|below|over|under|more than|less than)\s+\$", ql)
    if not plain:
        return None, None
    if code == "APU0000706311" and not re.search(r"\b(prices?|costs?|how much)\b", ql):
        return None, None                                         # "Turkey inflation" is the country
    return code, word


def tenors_of(ql):
    """The Treasury maturities a question names, as dataset codes, in order."""
    out = []
    if _LONG_BOND.search(ql):
        out.append("30Y")
    for m in _TENOR.finditer(ql):
        n = _NUM.get(m.group(1)) or int(m.group(1))
        code = None
        if m.group(2).startswith("y") and n in _YEARS:
            code = "%dY" % n
        elif m.group(2).startswith("m") and n in _MONTHS:
            code = "%dM" % n
        if code and code not in out:
            out.append(code)
    p = _PAIR.search(ql)
    if p and int(p.group(1)) in _YEARS and int(p.group(2)) in _YEARS and re.search(r"\byears?\b|\byrs?\b|\byields?\b|\btreasur|s\b", ql):
        for n in (p.group(1), p.group(2)):
            if "%sY" % n not in out:
                out.append("%sY" % n)
    if _BILLS.search(ql) and "3M" not in out:
        out.append("3M")
    return out


def _stripped_of_tenors(ql):
    t = _LONG_BOND.sub(" ", ql)
    t = _PAIR.sub(" ", t)
    t = _TENOR.sub(" ", t)
    return _BILLS.sub(" ", t)


def treasury_tenors(ql):
    """The maturities, when every other word fits a question about the yield."""
    tenors = tenors_of(ql)
    if not tenors:
        return []
    return tenors if all(_TENOR_COMPANY.match(w.strip("?.,!:;")) for w in _words(_stripped_of_tenors(ql)) if w.strip("?.,!:;")) else []


_RANK_ASK = re.compile(r"\b(gone up|risen|rose|increased|climbed|jumped|up|fallen|fell|dropped|gone down|down|got cheaper|gotten cheaper|cheaper|more expensive|rising|falling|inflation)\b.*\b(most|fastest|biggest|the least)\b|"
                       r"\b(most|fastest|biggest)\b.*\b(rise|rises|increase|increases|drop|drops|fall|falls|jump|jumps|inflation)\b|"
                       r"\bwhat(?:'s| is| has| have)? (?:got|gotten|become|getting|gone)? ?(?:cheaper|down)\b|\bwhich .*\b(?:cheaper|gone down|fallen|fell)\b")
_RANK_TOPIC = re.compile(r"\b(foods?|grocer(?:y|ies)|items?|prices?|things?|household)\b")


def asks_ranking(ql, on=""):
    if _NOT_THE_ITEM.search(ql) or not _RANK_ASK.search(ql):
        return False
    return bool(on == "prices" or _RANK_TOPIC.search(ql))


def _rest_names_company(cur, q, word):
    """True when the words beyond the item name a company ("Starbucks coffee prices")."""
    if cur is None:
        return False
    from .. import us_link
    t = (q or "").replace("’", "'")
    if word:
        t = re.sub(re.escape(word), " ", t, flags=re.I)
    for pat, _name in _AREAS:
        t = re.sub(pat, " ", t, flags=re.I)                        # "gas in Atlanta" is not the Atlanta Braves
    keep = [w for w in re.findall(r"[A-Za-z0-9&.'-]+", t) if w.lower().strip(".'") not in _FRAME]
    if not keep:
        return False
    try:
        return bool(us_link.find_companies(cur, " ".join(keep)))
    except Exception:                                             # noqa: BLE001 — no company list
        return False


def matches(cur, q):
    """Whether this reader takes the question, for app/stock_recipes.is_macro_question."""
    ql = (q or "").lower().replace("’", "'")
    if treasury_tenors(ql):
        return True
    if asks_ranking(ql):
        return True
    code, word = item_of(ql)
    return bool(code) and not _rest_names_company(cur, q, word)


# ------------------------------------------------------------ dates
_MONTH = {m: i + 1 for i, m in enumerate(_MONTH_WORDS[:12])}


def _d(v):
    return datetime.date.fromisoformat(str(v)[:10])


def _back_years(d, n):
    try:
        return d.replace(year=d.year - n)
    except ValueError:
        return d.replace(year=d.year - n, day=28)


def _back_months(d, n):
    i = d.year * 12 + d.month - 1 - n
    return datetime.date(i // 12, i % 12 + 1, min(d.day, 28))


def _at_or_before(rows, day):
    hit = None
    for r in rows:
        if _d(r[0]) > day:
            break
        hit = r
    return hit


def _at_or_after(rows, day):
    return next((r for r in rows if _d(r[0]) >= day), None)


def _window_start(ql, last):
    """(the row the question measures from: a callable on rows, words asked), or None."""
    m = re.search(r"\bsince (?:(" + "|".join(_MONTH_WORDS[:12]) + r") )?((?:19|20)\d{2})\b", ql)
    if m:
        d = datetime.date(int(m.group(2)), _MONTH.get(m.group(1), 1), 1)
        return lambda rows: _at_or_after(rows, d)
    m = re.search(r"\bsince (" + "|".join(_MONTH_WORDS[:12]) + r")\b", ql)
    if m:
        mon = _MONTH[m.group(1)]
        d = datetime.date(last.year if mon <= last.month else last.year - 1, mon, 1)
        return lambda rows: _at_or_after(rows, d)
    if re.search(r"\bsince (?:the )?(?:covid|pandemic)\b", ql):
        d = datetime.date(2020, 2, 1)
        return lambda rows: _at_or_after(rows, d)
    if re.search(r"\bthis year\b|\bytd\b|\byear to date\b|\bso far\b", ql):
        d = datetime.date(last.year, 1, 1) - datetime.timedelta(days=1)
        return lambda rows: _at_or_before(rows, d)
    m = re.search(r"\b(\d+) years? ago\b|\b(?:last|past) (\d+) years?\b|\bover (\d+) years?\b", ql)
    if m:
        d = _back_years(last, int(next(x for x in m.groups() if x)))
        return lambda rows: _at_or_before(rows, d)
    m = re.search(r"\b(\d+) months? ago\b|\b(?:last|past) (\d+) months?\b", ql)
    if m:
        d = _back_months(last, int(next(x for x in m.groups() if x)))
        return lambda rows: _at_or_before(rows, d)
    if re.search(r"\b(?:last|past|a|this) decade\b", ql):
        d = _back_years(last, 10)
        return lambda rows: _at_or_before(rows, d)
    if re.search(r"\b(?:this|past|last|in a) week\b|\ba week ago\b", ql):
        d = last - datetime.timedelta(days=7)
        return lambda rows: _at_or_before(rows, d)
    if re.search(r"\b(?:this|past|last) month\b|\ba month ago\b|\bon the month\b", ql):
        d = _back_months(last, 1)
        return lambda rows: _at_or_before(rows, d)
    return None


def _year_named(ql, last):
    m = re.search(r"\b(?:in|during|for|back in)\s+((?:19|20)\d{2})\b|^\s*((?:19|20)\d{2})\b|\b((?:19|20)\d{2})\s*\??\s*$", ql)
    if not m or re.search(r"\bsince\b", ql):
        return None
    y = int(next(x for x in m.groups() if x))
    return y if y <= last.year else None


# ------------------------------------------------------------ one series, every word
_PER = [(r"per 1/2 gal", "a half gallon"), (r"per doz", "a dozen"), (r"per lb|per pound", "a pound"), (r"per gal|per gallon|gallon/", "a gallon"),
        (r"per kwh", "a kilowatt-hour"), (r"per therm", "a therm"), (r"per 1 liter", "a liter"), (r"per 2 liters", "for 2 liters"),
        (r"per 8 oz", "for 8 oz"), (r"per 12 oz", "for 12 oz"), (r"per 16 oz", "for 16 oz")]


def item_label(title):
    """("Ground beef, 100% beef", "a pound") from the BLS's series title."""
    low = (title or "").lower()
    per = next((w for pat, w in _PER if re.search(pat, low)), "")
    name = re.split(r",? per |,? \(cost per|\s+per\s", title or "", flags=re.I)[0].strip(" ,")
    return " ".join(name.split()), per


def _usd(v):
    return "$" + "{:,.2f}".format(v)


_READ_CACHE = {}
_READ_FOR = 600.0     # seconds: a ranking reads some sixty series, and a release changes them once a month


def _rows(ds, code, start):
    """preview._series_rows at level, remembered briefly."""
    from . import preview
    key = (ds, code, start)
    hit = _READ_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _READ_FOR:
        return hit[1]
    got = preview._series_rows(ds, code, "level", start)
    _READ_CACHE[key] = (now, got)
    return got


def _many_rows(codes, start):
    """[(code, the same (rows, source, title, unit) as _rows)] for many average-price series in one read."""
    from .. import db
    first = _rows("us-avg-prices", codes[0], _recent_start()) or _rows("us-avg-prices", codes[0], "1900-01-01")
    if not first:
        return []
    src = first[1]
    by = {}
    q = ("SELECT s.code, s.name_en, o.period, o.value FROM series s JOIN observations o USING (series_id) "
         "WHERE s.dataset = 'us-avg-prices' AND s.code IN (%s) AND o.period >= ? AND o.value IS NOT NULL ORDER BY s.code, o.period" % ",".join("?" * len(codes)))
    for code, name, period, value in db.read_cursor().execute(q, codes + [start]).fetchall():
        by.setdefault(code, [name, []])[1].append([str(period)[:10], value])
    return [(c, (by[c][1], src, by[c][0], first[3], False)) for c in codes if c in by]


def _recent_start():
    """Far enough back for a plain answer and a change on the year."""
    return (datetime.date.today() - datetime.timedelta(days=800)).isoformat()


# the areas the BLS publishes average prices for, as their series names begin
_AREAS = [(r"new york", "New York"), (r"los angeles", "Los Angeles"), (r"san francisco|bay area", "San Francisco"), (r"chicago", "Chicago"), (r"atlanta", "Atlanta"),
          (r"boston", "Boston"), (r"dallas", "Dallas"), (r"houston", "Houston"), (r"miami", "Miami"), (r"seattle", "Seattle"), (r"philadelphia", "Philadelphia"),
          (r"detroit", "Detroit"), (r"washington", "Washington"), (r"\bnortheast\b", "Northeast"), (r"\bmidwest\b", "Midwest"), (r"\bsouth\b", "South"), (r"\bwest\b", "West")]


def _area_series(ql, code):
    """(the area's series code, the word it was read from), or (None, word) when
    the area is named but the BLS publishes no such item for it; (None, None) when none is named."""
    word = next(((m.group(0), name) for pat, name in _AREAS for m in [re.search(pat, ql)] if m), None)
    if not word:
        return None, None
    from .. import db
    title = (_rows("us-avg-prices", code, _recent_start()) or [None, None, ""])[2]
    if not title:
        return None, word[0]
    cur = db.read_cursor()
    row = cur.execute("SELECT code FROM series WHERE dataset = 'us-avg-prices' AND name_en LIKE ? AND name_en LIKE ? ORDER BY code LIMIT 1",
                      ["%%%s%%" % word[1], "%% \u2014 %s" % title]).fetchone()
    return (row[0] if row else None), word[0]


def _pct(v):
    return ("%.1f" % abs(v))


def _sign_pct(v):
    return ("up %s%%" % _pct(v)) if v > 0 else ("down %s%%" % _pct(v)) if v < 0 else "unchanged"


class _Series(object):
    """How one series is worded: a price in dollars, a yield in percent, or an index."""

    def __init__(self, ds, code, got):
        self.ds, self.code = ds, code
        self.rows, self.src, self.title, self.unit = got[0], got[1], got[2] or code, got[3]
        self.kind = "usd" if ds == "us-avg-prices" else "yield" if ds in ("ust-yields", "ust-real-yields") else "index"
        last = self.rows[-1]
        self.last_day = _d(last[0])
        self.daily = len(self.rows) > 2 and str(self.rows[-1][0])[:7] == str(self.rows[-2][0])[:7]

    def when(self, row):
        from . import preview
        return preview._when(row[0], self.daily)

    def on(self, row):
        return ("on " if self.daily else "in ") + self.when(row)

    def value(self, v):
        if self.kind == "usd":
            per = item_label(self.title)[1]
            return _usd(v) + ((" " + per) if per else "")
        if self.kind == "yield":
            return "%.2f%%" % v
        return "%.1f" % v

    def short(self, v):
        return _usd(v) if self.kind == "usd" else self.value(v)

    def name(self):
        if self.kind == "usd":
            return item_label(self.title)[0]
        if self.kind == "index":
            return "US Consumer Price Index, %s" % self.title
        return self.title

    def fig(self, label, row):
        unit = "$" if self.kind == "usd" else "%" if self.kind == "yield" else "index"
        val = round(row[1], 3 if self.kind == "usd" else 2 if self.kind == "yield" else 1)
        out = {"label": label, "value": val, "unit": unit, "as_of": str(row[0])[:10], "as_of_label": self.when(row)}
        if self.kind == "yield":
            out["dp"] = 2                                         # 4.00%, beside 4.16%
        return out

    def change(self, old, new):
        """(words, value, unit, formula)"""
        if self.kind == "yield":
            d = round(new - old, 2)
            words = ("up %.2f pp" % d) if d > 0 else ("down %.2f pp" % abs(d)) if d < 0 else "unchanged"
            return words, d, "pp", "Change = yield now − yield then, in percentage points, from the published daily yields."
        p = (new / old - 1) * 100
        what = "price" if self.kind == "usd" else "index"
        return _sign_pct(p), round(p, 1), "%", "Change = (%s now ÷ %s then − 1) × 100, from the published %s." % (
            what, what, "average prices, in the dollars of each date, not adjusted for inflation" if self.kind == "usd" else "index")

    def package(self, headline, figs, calc=None, lead=""):
        from . import preview
        name, page = preview._dataset_name(self.ds)
        return {"kind": "macro", "read_as": "%s — %s" % (name, self.title), "headline": headline, "lead": lead, "figures": figs,
                "sources": [c for c in [self.src.get("credit"), self.src.get("release")] if c], "calc": calc,
                "more": ["The chart of the whole history", "Every period in a table to download", "The release it came from"],
                "full_url": page or "us-treasury.html"}


def _unread_note(q, word, tenor_read=False):
    """The words nothing read, for the reader to see; empty when every word was."""
    t = (q or "").lower().replace("’", "'")
    if word:
        t = t.replace(word, " ")
    if tenor_read:
        t = _stripped_of_tenors(t)
    t = re.sub(r"\bsince (?:the )?(?:covid|pandemic)\b", " ", t)
    left = []
    for w in _words(t):
        w = w.strip("?.,!:;'")
        if not w or w in _FRAME or w in _MONTH_WORDS or re.fullmatch(r"[$]?\d[\d,.]*%?|percent|above|below|under|treasury|treasuries|yield|yields|rate|rates|bond|bonds|"
                                                                     r"note|notes|difference|between|spread|gap|tips|real|on|is|has|have|does|do|did|are|was|the|by|as|like|"
                                                                     r"exceeded?|higher|lower|top|at|in|back|grocery|store|buy|pay|paying|grade|large|whole|regular|fresh|white", w):
            continue
        left.append(w)
    if not left:
        return ""
    return "I could not read “%s” in your question, so this is the plain reading." % " ".join(left)


def _yes_no(ql, change_value):
    """"Yes. " or "No. " when the question asks whether it went up or down."""
    if not re.match(r"\s*(?:has|have|had|did|is|are|was|were|do|does)\b", ql):
        return ""
    up, down = bool(_UP_ASKED.search(ql)), bool(_DOWN_ASKED.search(ql))
    if up == down:
        return ""
    return "Yes. " if (change_value > 0 if up else change_value < 0) else "No. "


def series_answer(ds, code, q, word=None, tenor_read=False, area=None):
    ql = (q or "").lower().replace("’", "'")
    words = _stripped_of_tenors(ql) if tenor_read else ql        # "the 30 year" asks for no thirty years of history
    deep = bool(re.search(r"\b(?:19|20)\d{2}\b|\b\d+ (?:years?|months?)\b|\bdecade\b|\blast\b.*\b(?:above|over|below|under)\b|\bwhen\b|\bsince\b|\bhow long\b", words)
                or _HIGH.search(ql) or _LOW.search(ql) or re.search(r"\bever\b", ql))
    # a series the BLS has stopped has nothing recent: its last reading, with its date
    got = _rows(ds, code, "1900-01-01" if deep else _recent_start()) or _rows(ds, code, "1900-01-01")
    if not got:
        return None
    s = _Series(ds, code, got)
    rows, last = s.rows, s.rows[-1]
    note = _unread_note(re.sub(re.escape(area), " ", q, flags=re.I) if area else q, word, tenor_read)
    if (datetime.date.today() - s.last_day).days > 300:
        note = ("The BLS stopped publishing this one: this is its last reading. " + note).strip()
    # the last time it passed a level: "when was gas last above $4?"
    th = re.search(r"\b(above|over|exceeded?|higher than|more than|below|under|less than|lower than)\s+\$?(\d+(?:\.\d+)?)", ql)
    if th and re.search(r"\blast\b|when was|when were|when did|most recent|since when|how long", ql):
        level = float(th.group(2))
        above = th.group(1) not in ("below", "under", "less than", "lower than")
        meets = (lambda v: v > level) if above else (lambda v: v < level)
        side = "above" if above else "below"
        shown = s.short(level) if s.kind != "yield" else "%g%%" % level
        if meets(last[1]):
            head = "%s: %s %s, which is %s %s now." % (s.name(), s.value(last[1]), s.on(last), side, shown)
        else:
            hit = next((r for r in reversed(rows) if meets(r[1])), None)
            if hit is None:
                head = "%s has not been %s %s in the data on file, which begins %s; it is %s %s." % (
                    s.name(), side, shown, s.when(rows[0]), s.value(last[1]), s.on(last))
            else:
                head = "%s was last %s %s %s (%s); it is %s %s." % (s.name(), side, shown, s.on(hit), s.short(hit[1]), s.value(last[1]), s.on(last))
        return s.package(head, [s.fig("Latest", last)], None, note)
    # one year: where it spent that year, not only its December
    year = _year_named(ql, s.last_day)
    if year:
        inyear = [r for r in rows if _d(r[0]).year == year]
        if not inyear:
            return s.package("%s has no reading for %d in the data on file, which begins %s; it is %s %s." % (
                s.name(), year, s.when(rows[0]), s.value(last[1]), s.on(last)), [s.fig("Latest", last)], None, note)
        avg = sum(r[1] for r in inyear) / len(inyear)
        hi, lo = max(inyear, key=lambda r: r[1]), min(inyear, key=lambda r: r[1])
        head = "%s averaged %s in %d, from a low of %s %s to a high of %s %s." % (
            s.name(), s.value(avg), year, s.short(lo[1]), s.on(lo), s.short(hi[1]), s.on(hi))
        figs = [dict(s.fig("Average, %d" % year, (inyear[-1][0], avg)), as_of_label=str(year)), s.fig("Latest", last)]
        return s.package(head, figs, "Average = the mean of the %d readings published in %d." % (len(inyear), year), note)
    start = _window_start(ql, s.last_day)
    # a high or a low, over the window asked for, or the whole history
    high, low = bool(_HIGH.search(ql)), bool(_LOW.search(ql))
    if (high or low) and not (s.kind == "usd" and re.search(r"\bcheaper\b|more expensive", ql) and not re.search(r"most expensive|cheapest", ql)):
        first = start(rows) if start else None
        pool = [r for r in rows if not first or _d(r[0]) >= _d(first[0])] or rows
        top = (max if high else min)(pool, key=lambda r: r[1])
        word_hi = "high" if high else "low"
        span = "so far this year" if re.search(r"\bthis year\b|\bytd\b|\byear to date\b", ql) else ("since %s" % s.when(first)) if first else (
            "in records on file back to %s" % s.when(rows[0]))
        chg = s.change(top[1], last[1])
        if str(top[0]) == str(last[0]):
            head = "%s is at its %s %s: %s %s." % (s.name(), word_hi, span, s.value(last[1]), s.on(last))
        else:
            head = "%s: the %s %s was %s %s. It is now %s %s, %s from there." % (s.name(), word_hi, span, s.value(top[1]), s.on(top), s.value(last[1]), s.on(last), chg[0])
        return s.package(head, [s.fig("High" if high else "Low", top), s.fig("Latest", last),
                                {"label": "Change", "value": chg[1], "unit": chg[2], "as_of": str(last[0])[:10], "as_of_label": s.when(last)}],
                         chg[3], note)
    # a change, from the date asked for or a year back
    if start or _ASK_CHANGE.search(ql):
        base = start(rows) if start else None
        worded = bool(start)
        if base is None and not start:
            base = _at_or_before(rows, _back_years(s.last_day, 1))
            if base is not None and (s.last_day - _d(base[0])).days > 460:
                base = None
        if base is None:
            return s.package("%s: %s %s. The data on file has no reading that far back: it begins %s." % (
                s.name(), s.value(last[1]), s.on(last), s.when(rows[0])), [s.fig("Latest", last)], None, note)
        chg = s.change(base[1], last[1])
        then = (s.on(base)) if worded else "a year earlier"
        head = "%s%s: %s %s, %s from %s %s." % (_yes_no(ql, chg[1]), s.name(), s.value(last[1]), s.on(last), chg[0], s.short(base[1]), then)
        return s.package(head, [s.fig("Latest", last), s.fig("A year earlier" if not worded else "Then", base),
                                {"label": "Change", "value": chg[1], "unit": chg[2], "as_of": str(last[0])[:10], "as_of_label": s.when(last)}],
                         chg[3], note)
    # just the price or the yield
    head = "%s: %s %s." % (s.name(), s.value(last[1]), s.on(last))
    figs = [s.fig("Latest", last)]
    ago = _at_or_before(rows, _back_years(s.last_day, 1))
    if ago is not None and (s.last_day - _d(ago[0])).days <= 460:
        figs.append(s.fig("A year earlier", ago))
    return s.package(head, figs, None, note)


# ------------------------------------------------------------ the curve
def _curve_rows():
    out = []
    for code in ("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"):
        got = _rows("ust-yields", code, _recent_start())
        if got:
            out.append((code, got))
    return out


def _tenor_words(code):
    return "%s-%s" % (code[:-1], "year" if code.endswith("Y") else "month")


def pair_answer(a, b, q):
    ga, gb = _rows("ust-yields", a, _recent_start()), _rows("ust-yields", b, _recent_start())
    if not ga or not gb:
        return None
    other = {str(r[0])[:10]: r[1] for r in gb[0]}
    last = next((r for r in reversed(ga[0]) if str(r[0])[:10] in other), None)
    if not last:
        return None
    va, vb = last[1], other[str(last[0])[:10]]
    gap = round(va - vb, 2)
    s = _Series("ust-yields", a, ga)
    gap_words = "%.2f pp above" % abs(gap) if gap > 0 else "%.2f pp below" % abs(gap) if gap < 0 else "level with"
    head = "The %s Treasury yield is %.2f%% and the %s %.2f%% %s: the %s is %s the %s." % (
        _tenor_words(a), va, _tenor_words(b), vb, s.on(last), _tenor_words(a), gap_words, _tenor_words(b))
    day, when = str(last[0])[:10], s.when(last)
    figs = [{"label": _tenor_words(a).capitalize(), "value": round(va, 2), "unit": "%", "as_of": day, "as_of_label": when},
            {"label": _tenor_words(b).capitalize(), "value": round(vb, 2), "unit": "%", "as_of": day, "as_of_label": when},
            {"label": "Gap", "value": gap, "unit": "pp", "as_of": day, "as_of_label": when}]
    return s.package(head, figs, "Gap = %s yield − %s yield, in percentage points." % (_tenor_words(a), _tenor_words(b)), _unread_note(q, None, True))


def curve_answer(ql):
    rows = _curve_rows()
    if not rows:
        return None
    day = max(str(g[0][-1][0])[:10] for _, g in rows)
    today = [(c, g, g[0][-1][1]) for c, g in rows if str(g[0][-1][0])[:10] == day]
    s = _Series("ust-yields", today[0][0], today[0][1])
    last = s.rows[-1]
    if re.search(r"\bshortest\b", ql):
        pick = today[0]
        head = "The shortest Treasury yield on file is the %s: %.2f%% %s." % (_tenor_words(pick[0]), pick[2], s.on(last))
    elif re.search(r"\blongest\b", ql):
        pick = today[-1]
        head = "The longest Treasury yield on file is the %s: %.2f%% %s." % (_tenor_words(pick[0]), pick[2], s.on(last))
    else:
        hi, lo = max(today, key=lambda x: x[2]), min(today, key=lambda x: x[2])
        head = "The highest Treasury yield %s is the %s at %.2f%%; the lowest is the %s at %.2f%%." % (
            s.on(last), _tenor_words(hi[0]), hi[2], _tenor_words(lo[0]), lo[2])
    lead = "The curve: " + "; ".join("%s %.2f%%" % (_tenor_words(c), v) for c, _, v in today) + "."
    return s.package(head, [], None, lead)


# ------------------------------------------------------------ rankings
_RANK_SKIP = {"APU000074716", "APU000074715", "APU0000703113", "APU0000703111"}    # another grade of an item already listed


def _rank_start(ql):
    """Only as far back as the question goes: a ranking reads some sixty series."""
    today = datetime.date.today()
    m = re.search(r"\bsince (?:[a-z]+ )?((?:19|20)\d{2})\b", ql)
    if m:
        return "%s-01-01" % m.group(1)
    if re.search(r"\bsince (?:the )?(?:covid|pandemic)\b", ql):
        return "2020-01-01"
    m = re.search(r"\b(\d+) years?\b", ql)
    if m:
        return (today - datetime.timedelta(days=366 * (int(m.group(1)) + 1))).isoformat()
    if re.search(r"\bdecade\b", ql):
        return (today - datetime.timedelta(days=366 * 11)).isoformat()
    return _recent_start()


def rank_answer(q, ql):
    down = bool(re.search(r"\b(cheaper|fallen|fell|dropped|gone down|down|falling|least)\b", ql))
    food = bool(re.search(r"\bfoods?\b|\bgrocer", ql))
    energy = bool(re.search(r"\benergy\b|\bfuels?\b", ql))
    codes = []
    for _, code in ITEMS:
        if code not in codes and code not in _RANK_SKIP and not (food and code in ENERGY) and not (energy and code not in ENERGY):
            codes.append(code)
    series = [_Series("us-avg-prices", c, got) for c, got in _many_rows(codes, _rank_start(ql))]
    if not series:
        return None
    newest = max(s.last_day for s in series)
    live = [s for s in series if (newest - s.last_day).days <= 100]    # a series the BLS still publishes
    start = _window_start(ql, newest)
    ranked = []
    for s in live:
        base = start(s.rows) if start else _at_or_before(s.rows, _back_years(s.last_day, 1))
        if base is None or (not start and (s.last_day - _d(base[0])).days > 460):
            continue
        ranked.append((s.rows[-1][1] / base[1] - 1, s, base))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=not down)
    n_down = sum(1 for r in ranked if r[0] < 0)
    p, s, base = ranked[0]
    span = "the year to %s" % s.when(s.rows[-1]) if not start else "the period since %s" % s.when(base)
    group = "food and drink prices" if food else "energy prices" if energy else "average prices"
    firsts = [r[1].name().split(",")[0] for r in ranked]
    label = lambda x: x.name().split(",")[0] if firsts.count(x.name().split(",")[0]) == 1 else ", ".join(x.name().split(",")[:2])
    if down:
        if n_down == 0:
            return s.package("None of the %d %s on file fell over %s. The smallest rise was %s, %s." % (len(ranked), group, span, label(s), _sign_pct(p * 100)), [], None)
        head = "%d of the %d %s on file fell over %s. %s fell most, %s to %s." % (n_down, len(ranked), group, span, label(s), _sign_pct(p * 100), s.value(s.rows[-1][1]))
    else:
        head = "%s rose most of the %d %s on file over %s: %s to %s." % (label(s), len(ranked), group, span, _sign_pct(p * 100), s.value(s.rows[-1][1]))
    nxt = [r for r in ranked[1:6] if (r[0] < 0) == (ranked[0][0] < 0)]
    lead = ("Next: " + "; ".join("%s %s" % (label(x[1]), _sign_pct(x[0] * 100)) for x in nxt) + ".") if nxt else ""
    out = s.package(head, [s.fig("Latest", s.rows[-1]), s.fig("Then", base)],
                     "Change = (price now ÷ price then − 1) × 100, from the BLS's published US city average prices, in the dollars of each date, "
                     "for the items it still publishes.", lead)
    out["question"] = "price of %s" % label(s).lower()         # "how much has it gone up?" next is about the item named first
    return out


_CPI_NAMES = re.compile(r"\bprices\b|\binflation\b|\bcpi\b|\bcost of living\b|\brent\b|\bshelter\b|\bmedical\b|\bused cars?\b|\bnew cars?\b")
_FOLLOW = re.compile(r"^\s*(?:and\b|what about\b|how about\b|since\b|from\b)|\b(?:it|that|this one|them|they|those)\b")


def answer(q, prev="", on="", cur=None):
    """The answer to a question this reader takes, or None. `on` is the page the
    box sits on ("prices", "treasury"), which settles what a short question means."""
    from . import preview
    if preview.market_of(q) != "us":
        return None
    ql = (q or "").lower().replace("’", "'").strip()
    if preview._NOT_HELD_MACRO.search(ql):
        return None
    # "gas in Germany" is not the US price; "price of turkey" is the bird
    if preview._OTHER_COUNTRY.search(re.sub(r"\bturkeys?\b", " ", ql) if item_of(ql)[0] == "APU0000706311" else ql):
        return None
    # "how much has it gone up?" after a question about eggs is about eggs
    prev_l = (prev or "").lower().replace("’", "'")
    held = (lambda t: bool(treasury_tenors(t) or item_of(t)[0]))
    if prev_l and not held(ql) and _FOLLOW.search(ql) and held(prev_l):
        word = item_of(prev_l)[1]
        ql = re.sub(r"\b(?:it|that|this one|them|they|those)\b", " ", ql)
        q = "%s %s" % (word or tenors_of(prev_l)[0].replace("Y", " year").replace("M", " month"), ql)
        ql = q.lower()
    tenors = treasury_tenors(ql)
    # after a question about a company, "the 5 year" is not a yield: the follow-up route reads it
    if tenors and prev_l and on != "treasury" and not re.search(r"\byields?\b|treasur", ql) and not held(prev_l) and not preview._is_macro(prev):
        return None
    real = bool(re.search(r"\b(?:tips|real)\b", ql))
    if len(tenors) >= 2 and not real:
        return pair_answer(tenors[0], tenors[1], q)
    if tenors:
        return series_answer("ust-real-yields" if real else "ust-yields", tenors[0], q, None, True)
    if (on == "treasury" or re.search(r"\byields?\b|\bcurve\b", ql)) and re.search(r"\b(highest|lowest|shortest|longest)\b.*\b(yields?|curve|maturity|rate)\b|"
                                                                                   r"\b(whole|full|entire|today's|current) (?:yield )?curve\b|every maturity|all (?:the )?(?:yields|maturities)", ql):
        return curve_answer(ql)
    if on == "treasury":
        if re.fullmatch(r"(?:the )?(?:t-?)?bills?\??", ql):
            return series_answer("ust-yields", "3M", "", None)
        # on this page a change with no maturity named is the benchmark, and the headline says which
        if not item_of(ql)[0] and (_ASK_CHANGE.search(ql) or _HIGH.search(ql) or _LOW.search(ql)) and not _NOT_THE_ITEM.search(ql) and not _rest_names_company(cur, q, None):
            return series_answer("ust-yields", "10Y", q, None, True)
    if asks_ranking(ql, on):
        return rank_answer(q, ql)
    code, word = item_of(ql)
    if code and not _rest_names_company(cur, q, word):
        if re.search(r"\binflation\b|\bcpi\b|\bprice index\b", ql):
            hit = preview.catalogue_hit(ql)
            if hit and hit[2] == "yoy":
                return None                                       # the price index's own rate
        area_code, area_word = _area_series(ql, code)
        if area_code:
            return series_answer("us-avg-prices", area_code, q, word, False, area_word)
        out = series_answer("us-avg-prices", code, q, word, False, area_word)
        if out and area_word:
            out["lead"] = ("The BLS does not publish this item for %s, so this is the US city average." % area_word.title() + " " + (out.get("lead") or "")).strip()
        return out
    # an index over a window of the reader's: "how much have prices gone up since 2020?"
    if _CPI_NAMES.search(ql) and not _NOT_THE_ITEM.search(ql):
        hit = preview.catalogue_hit(ql + " inflation" if re.search(r"\bprices\b", ql) and not re.search(r"\binflation|\bcpi", ql) else ql)
        if hit and hit[2] == "yoy":
            got = _rows(hit[0], hit[1], _recent_start())
            if got and _window_start(ql, _d(got[0][-1][0])):
                return series_answer(hit[0], hit[1], q, _CPI_NAMES.search(ql).group(0))
    return None
