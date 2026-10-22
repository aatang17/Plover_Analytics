# -*- coding: utf-8 -*-
"""The front page's preview: a typed question in, the start of its answer out.

GET /api/v1/assistant/preview?q=...   — public, no account, no AI of ours.

The front page (web/index.html) shows a visitor the headline of the answer and
two figures; the full answer (chart, table, sources, follow-ups) is one sign-up
away. Two routes, tried in this order:

  1. US stocks: the US stock box (app/stock_recipes.answer, as served at
     /api/v1/us/ask). The full answer is the home page, /?q=..., signed in.
  2. US economy: the series a question names, found by words
     (app/analysis.suggest_series, no model), US datasets only, then one
     ready-made analysis run by code (app/recipes): a price index as its
     year-on-year rate, anything else as its latest reading. The full answer
     is the dataset's own page.

Nothing here computes a figure the pages do not: the stock answer is passed
through, and the one calculated rate (year on year) carries its formula. A
question neither route can answer says so, with what Plover does hold.
"""
import datetime
import re

from fastapi import APIRouter, HTTPException, Query

from .. import analysis, recipes, registry

router = APIRouter(prefix="/preview", tags=["assistant"])

# The US economy datasets the preview may answer from: the US pages' series,
# not the stock-price feed (us-prices), which the stock route covers.
US_MACRO = ("cpi-us", "cpi-us-sa", "cpi-us-areas", "us-avg-prices", "us-wages", "us-wage-tracker",
            "ust-yields", "ust-real-yields", "us-labour")

# The series the commonest economy questions mean, by market, matched by
# words before any search: (words, dataset, code, measure). "yoy" turns an
# index into its year-on-year rate; "level" shows the reading as published.
# First match wins, so the specific ("core") come before the general ("cpi").
CATALOGUE = {
    "us": [
        (r"unemployment rate|unemployment|jobless rate|joblessness", "us-labour", "LNS14000000", "level"),
        (r"participation", "us-labour", "LNS11300000", "level"),
        (r"job openings|\bjolts\b|openings", "us-labour", "JTS000000000000000JOL", "level"),
        (r"\bquits\b|quit rate", "us-labour", "JTS000000000000000QUL", "level"),
        (r"payrolls?|nonfarm|non-farm|jobs (?:report|added|created|growth|number)|job growth|hiring|employment level|how many jobs", "us-labour", "CES0000000001", "level"),
        (r"core (?:cpi|inflation|prices)|cpi (?:ex|less|excluding) food|(?:ex|excluding|less) food and energy|underlying inflation", "cpi-us", "CUUR0000SA0L1E", "yoy"),
        (r"(?=.*(?:\btips\b|real yield|inflation.protected|real rate))\b(?:5|five)[- ]year", "ust-real-yields", "5Y", "level"),
        (r"(?=.*(?:\btips\b|real yield|inflation.protected|real rate))\b(?:30|thirty)[- ]year", "ust-real-yields", "30Y", "level"),
        (r"\btips\b|real yield|inflation.protected|real rate|real (?:10|ten)[- ]year", "ust-real-yields", "10Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bills?|rates?)\b)\b(?:1|one)[- ]month", "ust-yields", "1M", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bills?|rates?)\b)\b(?:3|three)[- ]month|t.?bill", "ust-yields", "3M", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bills?|rates?)\b)\b(?:6|six)[- ]month", "ust-yields", "6M", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:1|one)[- ]year", "ust-yields", "1Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:3|three)[- ]year", "ust-yields", "3Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:7|seven)[- ]year", "ust-yields", "7Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:20|twenty)[- ]year", "ust-yields", "20Y", "level"),
        (r"\brents?\b|rent inflation|rental", "cpi-us", "CUUR0000SEHA", "yoy"),
        (r"shelter|housing costs?", "cpi-us", "CUUR0000SAH1", "yoy"),
        (r"\beggs?\b", "us-avg-prices", "APU0000708111", "level"),
        (r"\bmilk\b", "us-avg-prices", "APU0000709112", "level"),
        (r"\bbread\b", "us-avg-prices", "APU0000702111", "level"),
        (r"ground beef|\bbeef\b", "us-avg-prices", "APU0000703112", "level"),
        (r"\bchicken\b", "us-avg-prices", "APU0000706111", "level"),
        (r"\bcoffee\b", "us-avg-prices", "APU0000717311", "level"),
        (r"\bbananas?\b", "us-avg-prices", "APU0000711211", "level"),
        (r"(?:average|avg|retail) (?:gas|gasoline|petrol) price|price of (?:gas|gasoline) per gallon|gas price per gallon", "us-avg-prices", "APU000074714", "level"),
        (r"electricity price per kwh|price of electricity|electricity (?:rate|cost) per", "us-avg-prices", "APU000072610", "level"),
        (r"food prices?|grocer|food inflation|food at home", "cpi-us", "CUUR0000SAF11", "yoy"),
        (r"gasoline|gas prices?|petrol", "cpi-us", "CUUR0000SETB01", "yoy"),
        (r"energy prices?|energy inflation", "cpi-us", "CUUR0000SA0E", "yoy"),
        (r"used cars?", "cpi-us", "CUUR0000SETA02", "yoy"),
        (r"new (?:cars?|vehicles?)", "cpi-us", "CUUR0000SETA01", "yoy"),
        (r"medical|health ?care costs?", "cpi-us", "CUUR0000SAM", "yoy"),
        (r"services inflation|services prices", "cpi-us", "CUUR0000SAS", "yoy"),
        (r"goods inflation|goods prices|commodities", "cpi-us", "CUUR0000SAC", "yoy"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:2|two)[- ]year", "ust-yields", "2Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:30|thirty)[- ]year", "ust-yields", "30Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:5|five)[- ]year", "ust-yields", "5Y", "level"),
        (r"(?=.*\b(?:treasur\w*|yields?|bonds?|notes?|rates?)\b)\b(?:10|ten)[- ]year|treasur|long bond|bond yield", "ust-yields", "10Y", "level"),
        (r"\btips\b|real yield", "ust-real-yields", "10Y", "level"),
        (r"electricity|power bills?|utility bills?", "cpi-us", "CUUR0000SEHF01", "yoy"),
        (r"headline (?:cpi|inflation)|\bcpi\b|inflation|consumer prices?|price index|cost of living", "cpi-us", "CUUR0000SA0", "yoy"),
    ],
    "jp": [
        (r"core.core|less fresh food and energy|(?:ex|excluding) fresh food and energy|ex energy", "cpi-jp", "0178", "yoy"),
        (r"core (?:cpi|inflation)|less fresh food|(?:ex|excluding) fresh food", "cpi-jp", "0161", "yoy"),
        (r"tokyo", "cpi-tokyo", "0001", "yoy"),
        (r"rice", "cpi-jp-items", "0004", "yoy"),
        (r"\bjgb|\b(?:10|ten)[- ]year|yield|bond", "jgb-yields", "10Y", "level"),
        (r"monetary base", "boj-assets", "MA03021001S", "level"),
        (r"\bboj\b|bank of japan|balance sheet|jgb holdings", "boj-assets", "MA03021034S", "level"),
        (r"\bgdp\b|economy grow|growth", "gdp-jp", "gdp.real_sa", "yoy"),
        (r"unemployment|jobless", "unemployment-jp", "sa.unemployment_rate.total", "level"),
        (r"real wages?", "wages-jp", "real_wage_yoy.all.5plus.all", "level"),
        (r"wages?|earnings|pay\b", "wages-jp", "cash_earnings_yoy.all.5plus.all", "level"),
        (r"job openings|jobs.to.applicants|job availability", "job-openings-jp", "sa.active.incl_part", "level"),
        (r"\bcpi\b|inflation|consumer prices?|price index|prices", "cpi-jp", "0001", "yoy"),
    ],
    "hk": [
        (r"underlying", "cpi-hk", "UCC_CM_1920", "yoy"),
        (r"\bcpi\b|inflation|consumer prices?|price index|prices", "cpi-hk", "CC_CM_1920", "yoy"),
        (r"\bgdp\b|economy grow|growth", "gdp-hk", "CON", "yoy"),
        (r"unemployment|jobless", "labour-hk", "SAUR", "level"),
    ],
}
MARKET_WORDS = [(r"\bjapan(?:ese)?\b|\btokyo\b|\bboj\b|\bjgbs?\b|\byen\b|bank of japan", "jp"),
                (r"\bhong\s*kong\b|\bhk\b|hang seng", "hk")]
_US_WORDS = re.compile(r"\b(?:us|u\.s\.|usa|america|american|united states|treasur\w*|fed)\b", re.I)
# a metro area or region named: the area tables answer, not the national index
_METRO = re.compile(r"los angeles|chicago|new york|san francisco|boston|dallas|houston|miami|seattle|atlanta|philadelphia|detroit|phoenix|denver|"
                    r"minneapolis|washington|baltimore|st\.? louis|tampa|san diego|riverside|alaska|anchorage|honolulu|hawaii|urban|\bnortheast\b|"
                    r"\bmidwest\b|\bsouth\b|\bwest\b|\bbay area\b", re.I)
# series already published as a rate: their "level" is a percentage
_RATE_SERIES = {"cash_earnings_yoy.all.5plus.all", "real_wage_yoy.all.5plus.all"}


def market_of(q):
    ql = (q or "").lower()
    for pat, m in MARKET_WORDS:
        if re.search(pat, ql):
            return m
    return "us"


# series people ask for that no dataset carries; the Treasury entries must not stand in for them
_NOT_HELD_MACRO = re.compile(r"mortgage|fed funds|federal funds|fomc|fed meeting|\bpce\b|unemployment claims|jobless claims|initial claims|\bgdp\b.*\b(us|u\.s\.|american)\b|"
                             r"\b(us|u\.s\.|american)\b.*\bgdp\b|trade (deficit|balance)|prime rate|sofr|libor|money supply|\bm2\b")


def catalogue_hit(q):
    """(dataset, code, measure, market) for a question the catalogue answers, or None."""
    ql = (q or "").lower()
    m = market_of(q)
    if m == "us" and _NOT_HELD_MACRO.search(ql):
        return None
    if m == "us" and _METRO.search(ql) and re.search(r"cpi|inflation|prices", ql):
        return None                                               # the series search finds the metro table
    for pat, ds, code, measure in CATALOGUE[m]:
        if re.search(pat, ql):
            return ds, code, measure, m
    return None

# A question must name a topic Plover holds, and the series must come from a
# dataset for that topic: without this, "US unemployment rate" (not held) was
# answered with the 10-year yield, the nearest series by words.
TOPICS = (
    (("inflation", "cpi", "consumer price", "consumer prices", "prices", "price index", "deflation"),
     ("cpi-us", "cpi-us-sa", "cpi-us-areas", "us-avg-prices")),
    (("yield", "yields", "treasury", "treasuries", "bond", "bonds", "tips", "yield curve"),
     ("ust-yields", "ust-real-yields")),
    (("wage", "wages", "pay", "earnings", "salary", "salaries", "hourly"),
     ("us-wages", "us-wage-tracker")),
    (("price", "cost", "costs", "milk", "bread", "eggs", "coffee", "chicken", "beef", "bacon", "bananas", "gasoline", "gas", "electricity", "utility"),
     ("us-avg-prices", "cpi-us")),
    (("unemployment", "jobless", "payrolls", "payroll", "nonfarm", "jobs", "employment", "participation", "openings", "jolts", "quits", "hiring", "labor", "labour"),
     ("us-labour",)),
)
# levels in thousands of people: shown in millions, with the change on the month
_THOUSANDS = {"CES0000000001", "CES0500000001", "LNS12000000", "LNS13000000", "LNS11000000", "JTS000000000000000JOL", "JTS000000000000000QUL", "JTS000000000000000HIL"}


def _allowed(q):
    ql = " " + re.sub(r"[^a-z0-9 ]+", " ", q.lower()) + " "
    out = set()
    for words, datasets in TOPICS:
        if any(" " + w + " " in ql for w in words):
            out.update(datasets)
    # "earnings" in a company sense (an earnings calendar, a report, EPS) is not a question about wages
    if re.search(r"\b(calendar|report|reports|reporting|season|eps|per share|q[1-4]|company|companies|stocks?)\b", ql) and \
            not re.search(r"\b(wage|wages|hourly|salary|salaries|pay)\b", ql):
        out -= {"us-wages", "us-wage-tracker"}
    return out


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

HOLDS = ("Plover answers questions about US-listed companies (owners, earnings, buybacks, insider "
         "trades, prices and valuations) and US inflation, wages and Treasury yields.")


def _num(v, dp=1):
    s = ("%." + str(dp) + "f") % abs(v)
    return ("−" + s) if v < 0 and float(s) != 0 else s


def _when(iso, daily):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    if daily:
        return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)
    return "%s %d" % (MONTHS[d.month - 1], d.year)


def _quarterly(rows):
    try:
        a = datetime.date.fromisoformat(str(rows[-1][0])[:10])
        b = datetime.date.fromisoformat(str(rows[-2][0])[:10])
    except ValueError:
        return False
    return 80 <= (a - b).days <= 100


def _quarter_words(iso):
    d = datetime.date.fromisoformat(str(iso)[:10])
    return "Q%d %d" % ((d.month - 1) // 3 + 1, d.year)


_BIG_UNITS = {"\u00a5100mn": (1e8, "\u00a5"), "jpy_100mn": (1e8, "\u00a5"), "\u00a5bn": (1e9, "\u00a5"), "jpy_billion": (1e9, "\u00a5"),
              "hk$mn": (1e6, "HK$"), "hkd_million": (1e6, "HK$"), "jpy_million": (1e6, "\u00a5")}


def _big(value, unit):
    """A level in a large money unit, in words: 5,193,819 \u00a5100mn -> \u00a5519tn."""
    scale, sym = _BIG_UNITS[unit]
    v = value * scale
    for cut, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "mn")):
        if abs(v) >= cut:
            return "%s%s%s" % ("\u2212" if v < 0 else "", sym, ("%.1f" % (abs(v) / cut)).rstrip("0").rstrip(".") + suffix)
    return "%s%s%s" % ("\u2212" if v < 0 else "", sym, "{:,.0f}".format(abs(v)))


def _first_sentence(text):
    m = re.match(r"(.+?[.!?])(\s|$)", (text or "").strip(), re.S)
    return (m.group(1) if m else (text or "")).strip()


def _stock(q, prev=None):
    """The stock box's answer as a preview, or None when it does not answer.
    `prev` is the reader's previous question, so a follow-up reads whole."""
    from .. import sec_api, stock_recipes
    try:
        cur = sec_api._require()
        a = stock_recipes.answer(cur, q, prev)
    except HTTPException as exc:
        return None, exc                       # a miss: app/ask_miss.py words it for the reader
    except Exception:  # noqa: BLE001 — no SEC database, or a failure: try the next route
        return None, "error"
    if not a or not a.get("headline"):
        return None, None
    figs = []
    for f in (a.get("figures") or [])[:2]:
        figs.append({"label": f.get("label"), "value": f.get("value"), "unit": f.get("unit"),
                     "as_of": f.get("as_of")})
    more = []
    if a.get("chart"):
        more.append("The chart: " + (a["chart"].get("title") or "the full picture"))
    rows = len((a.get("table") or {}).get("rows") or [])
    if rows:
        more.append("The table: %d rows to scan and download" % rows)
    more.append("Every source, and how each figure was worked out")
    more.append("Follow-up questions on the same answer")
    return {"kind": "stock", "read_as": (a.get("match") or {}).get("read_as") or "",
            "headline": a["headline"], "lead": _first_sentence(a.get("answer")),
            "figures": figs, "sources": [s.get("credit") for s in a.get("sources") or [] if s.get("credit")],
            "calc": None, "more": more, "period": a.get("period"),
            # the question as the box read it (a follow-up made whole), for the next follow-up
            "question": a.get("question") or q,
            # the full answer is the home page itself, signed in (us-stocks.html redirects there)
            "full_url": "/?q=" + _quote(a.get("question") or q)}, None


def _quote(q):
    import urllib.parse
    return urllib.parse.quote(q, safe="")


def _dataset_name(ds):
    try:
        m = registry.get(ds)
        name = (m.get("name") or {}).get("en") if isinstance(m, dict) else None
        page = m.get("page") if isinstance(m, dict) else None
    except Exception:  # noqa: BLE001
        name, page = None, None
    return (name or ds).split(" — ")[0], (page or "/").lstrip("/")


def _series_rows(ds, code, measure, start=""):
    """(rows [(date, value)], source, title, unit, index) for one series under one measure, or None."""
    ref = "%s:%s" % (ds, code)
    try:
        spec = recipes.build("growth" if measure in ("yoy", "mom") else "latest", {"a": ref}, start or "", "",
                             {"measure": measure} if measure in ("yoy", "mom") else {}, None, "")
        res = analysis.execute(spec)
    except (recipes.RecipeError, analysis.AnalysisError):
        return None
    rows = [r for r in res.get("rows") or [] if r[1] is not None]
    if not rows:
        return None
    src = (res.get("sources") or [{}])[0]
    return rows, src, src.get("title") or code, src.get("unit") or "", measure in ("yoy", "mom")


def _year_in(q, today=None):
    today = today or datetime.date.today()
    m = re.search(r"\b(?:in|during|for|back in)\s+((?:19|20)\d{2})\b", (q or "").lower())
    if m and int(m.group(1)) < today.year:
        return int(m.group(1))
    return None


def _threshold(q):
    """("above"|"below", value) for "last above 5%", "last time inflation was over 3%"."""
    m = re.search(r"\b(above|over|exceeded?|higher than|more than|below|under|less than|lower than)\s+(\d+(?:\.\d+)?)\s*(?:%|percent|pp)?", (q or "").lower())
    if not m or not re.search(r"\blast\b|when was|when did|most recent|since when|how long", (q or "").lower()):
        return None
    return ("below" if m.group(1) in ("below", "under", "less than", "lower than") else "above"), float(m.group(2))


def _curve(q):
    """The 10-year against the 2-year Treasury yield, for "is the curve inverted" and "2s10s"."""
    ten = _series_rows("ust-yields", "10Y", "level")
    two = _series_rows("ust-yields", "2Y", "level")
    if not ten or not two:
        return None
    t_rows, src, _, _, _ = ten
    by2 = {str(r[0])[:10]: r[1] for r in two[0]}
    last = next((r for r in reversed(t_rows) if str(r[0])[:10] in by2), None)
    if not last:
        return None
    d, y10 = str(last[0])[:10], last[1]
    y2 = by2[d]
    gap = y10 - y2
    when = _when(d, True)
    headline = "The 10-year Treasury yield is %s%% and the 2-year %s%% on %s: a gap of %s pp, so the curve is %s." % (
        _num(y10, 2), _num(y2, 2), when, ("+" if gap >= 0 else "\u2212") + "%.2f" % abs(gap), "not inverted" if gap >= 0 else "inverted")
    name, page = _dataset_name("ust-yields")
    return {"kind": "macro", "read_as": "%s \u2014 10-year less 2-year yield" % name, "headline": headline, "lead": "",
            "figures": [{"label": "10-year", "value": round(y10, 2), "unit": "%", "as_of": d, "as_of_label": when},
                        {"label": "2-year", "value": round(y2, 2), "unit": "%", "as_of": d, "as_of_label": when},
                        {"label": "10-year less 2-year", "value": round(gap, 2), "unit": "pp", "as_of": d, "as_of_label": when}],
            "sources": [c for c in [src.get("credit"), src.get("release")] if c],
            "calc": "Gap = 10-year constant-maturity yield \u2212 2-year constant-maturity yield, in percentage points; below zero is an inverted curve.",
            "more": ["The whole curve, every maturity, as a chart", "Every day in a table to download", "The release it came from"],
            "full_url": page or "us-treasury.html"}


def _two_markets(q):
    """["us", "jp"] when the question sets two markets against each other, else None."""
    ql = (q or "").lower()
    found = [m for pat, m in MARKET_WORDS if re.search(pat, ql)]
    if _US_WORDS.search(ql) or (found and re.search(r"\b(vs\.?|versus|against|compared(?: with| to)?|compare|gap|spread|difference between)\b", ql) and "us" not in found):
        found.append("us")
    found = list(dict.fromkeys(found))
    return found if len(found) == 2 else None


def _compare_markets(q, markets):
    """Two markets' readings of the same thing (inflation, the 10-year yield) side by side."""
    ql = (q or "").lower()
    got = []
    for m in markets:
        for pat, ds, code, measure in CATALOGUE[m]:
            if re.search(pat, ql):
                r = _series_rows(ds, code, measure)
                if r:
                    got.append((m, ds, code, measure, r))
                break
    if len(got) != 2:
        return None
    names = {"us": "US", "jp": "Japan", "hk": "Hong Kong"}
    parts, figs, srcs = [], [], []
    for m, ds, code, measure, (rows, src, title, unit, index) in got:
        d, v = rows[-1]
        daily = len(rows) > 2 and str(rows[-1][0])[:7] == str(rows[-2][0])[:7]
        when = _when(d, daily)
        parts.append("%s %s%% (%s)" % (names[m], _num(v, 2 if daily else 1), when))
        figs.append({"label": "%s, %s" % (names[m], title), "value": round(v, 2), "unit": "%", "as_of": str(d)[:10], "as_of_label": when})
        srcs += [c for c in [src.get("credit")] if c and c not in srcs]
    gap = got[0][4][0][-1][1] - got[1][4][0][-1][1]
    headline = "%s against %s: a gap of %s pp." % (parts[0], parts[1], ("+" if gap >= 0 else "\u2212") + "%.2f" % abs(gap))
    name, page = _dataset_name(got[0][1])
    return {"kind": "macro", "read_as": "%s against %s" % (got[0][4][2], got[1][4][2]), "headline": headline, "lead": "",
            "figures": figs, "sources": srcs, "calc": "Each reading as published or as its year-on-year rate; the gap is the first less the second, in percentage points.",
            "more": ["Both series on one chart", "Every period in a table to download", "The releases they came from"], "full_url": page}


def _core_vs_headline(q, market):
    """Core and headline inflation side by side."""
    pair = {"us": (("cpi-us", "CUUR0000SA0L1E"), ("cpi-us", "CUUR0000SA0")),
            "jp": (("cpi-jp", "0161"), ("cpi-jp", "0001")),
            "hk": (("cpi-hk", "UCC_CM_1920"), ("cpi-hk", "CC_CM_1920"))}[market]
    core, head = _series_rows(pair[0][0], pair[0][1], "yoy"), _series_rows(pair[1][0], pair[1][1], "yoy")
    if not core or not head:
        return None
    c_rows, src, c_title, _, _ = core
    h_rows = {str(r[0])[:10]: r[1] for r in head[0]}
    last = next((r for r in reversed(c_rows) if str(r[0])[:10] in h_rows), None)
    if not last:
        return None
    d = str(last[0])[:10]
    cv, hv = last[1], h_rows[d]
    when = _when(d, False)
    name, page = _dataset_name(pair[0][0])
    where = {"us": "US", "jp": "Japan", "hk": "Hong Kong"}[market]
    headline = "%s core inflation was %s%% and headline %s%% on a year earlier in %s; core is %s headline by %s pp." % (
        where, _num(cv, 1), _num(hv, 1), when, "above" if cv >= hv else "below", "%.1f" % abs(cv - hv))
    return {"kind": "macro", "read_as": "%s \u2014 %s against %s" % (name, c_title, head[2]), "headline": headline, "lead": "",
            "figures": [{"label": "Core, year on year", "value": round(cv, 1), "unit": "%", "as_of": d, "as_of_label": when},
                        {"label": "Headline, year on year", "value": round(hv, 1), "unit": "%", "as_of": d, "as_of_label": when}],
            "sources": [c for c in [src.get("credit"), src.get("release")] if c],
            "calc": "Year on year = (index this period \u00f7 index a year earlier \u2212 1) \u00d7 100, from the published index; it can differ from the published rate by 0.1 pp.",
            "more": ["The chart of both series", "Every month in a table to download", "The release it came from"],
            "full_url": page}


def _macro(q):
    """An economy series the question names, as a preview, or None. The
    catalogue (US, Japan, Hong Kong) is tried first; a US question it does
    not cover falls to the series search, gated by topic."""
    ql = (q or "").lower()
    market = market_of(q)
    try:
        if market == "us" and re.search(r"2s10s|10s2s|yield curve|inverted|inversion|10.year (?:minus|less|vs\.?|versus|against) (?:the )?2.year|spread between", ql):
            out = _curve(q)
            if out:
                return out
        if re.search(r"\bcore\b.*\bheadline\b|\bheadline\b.*\bcore\b|core (?:vs\.?|versus|against|and) headline", ql) and re.search(r"cpi|inflation", ql):
            out = _core_vs_headline(q, market)
            if out:
                return out
    except Exception:  # noqa: BLE001 — the single-series answer is still tried
        pass
    both = _two_markets(q)
    if both:
        try:
            out = _compare_markets(q, both)
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
    hit = catalogue_hit(q)
    h = None
    deep = "1900-01-01" if (_year_in(q) or _threshold(q) or re.search(r"\bpeak|all.time high|record high|highest ever|highest since", ql)) else ""
    mom = bool(re.search(r"month.on.month|\bmom\b|monthly change|from the (?:previous|prior) month|over the month", ql))
    if hit:
        ds, code, measure, _ = hit
        if mom and measure == "yoy":
            measure = "mom"
        got = _series_rows(ds, code, measure, deep)
        if got:
            h = {"dataset": ds, "code": code, "name": got[2], "unit": got[3]}
            rows, src, title, unit, index = got
    if h is None:
        if market != "us":
            return None
        try:
            hits = analysis.suggest_series(q)
        except Exception:  # noqa: BLE001
            return None
        allowed = _allowed(q) & set(US_MACRO)
        hits = [x for x in hits if x.get("dataset") in allowed]
        if not hits:
            return None
        # "milk price": the average price in dollars before the price index
        if re.search(r"\bprices?\b|\bcost\b", ql) and not re.search(r"inflation|cpi|index", ql):
            hits = [x for x in hits if x.get("dataset") == "us-avg-prices"] + [x for x in hits if x.get("dataset") != "us-avg-prices"]
        h = hits[0]
        index = (h.get("unit") or "").lower() == "index"
        got = _series_rows(h["dataset"], h["code"], ("mom" if mom else "yoy") if index else "level", deep)
        if not got:
            return None
        rows, src, title, unit, index = got
    title = title or h.get("name") or h["code"]
    unit = unit or h.get("unit") or ""
    name, page = _dataset_name(h["dataset"])
    year = _year_in(q)
    if year:
        # "US CPI in 1980": the reading at the end of that year, or the last one in it
        inyear = [r for r in rows if str(r[0])[:4] == str(year)]
        if not inyear:
            return None
        rows = rows[:rows.index(inyear[-1]) + 1]
    last_date, last = rows[-1][0], rows[-1][1]
    daily = len(rows) > 2 and str(rows[-1][0])[:7] == str(rows[-2][0])[:7]
    when = _when(last_date, daily)
    if not daily and len(rows) > 2 and _quarterly(rows):
        when = _quarter_words(last_date)
    pct = unit in ("%", "percent", "pct") or index or h["code"] in _RATE_SERIES
    th = _threshold(q)
    if th and pct:
        side, level = th
        latest = rows[-1]
        meets = (lambda v: v > level) if side == "above" else (lambda v: v < level)
        if meets(latest[1]):
            headline = "%s, %s: %s%% in %s, which is %s %s%% now." % (name, title, _num(latest[1], 1), when, side, _num(level, 1))
        else:
            hit_row = next((r for r in reversed(rows) if meets(r[1])), None)
            if hit_row is None:
                headline = "%s, %s has not been %s %s%% in the data on file (since %s); it is %s%% in %s." % (
                    name, title, side, _num(level, 1), _when(rows[0][0], daily), _num(latest[1], 1), when)
            else:
                headline = "%s, %s was last %s %s%% in %s (%s%%); it is %s%% in %s." % (
                    name, title, side, _num(level, 1), _when(hit_row[0], daily), _num(hit_row[1], 1), _num(latest[1], 1), when)
        figs = [{"label": "Latest" if not index else "Year on year", "value": round(latest[1], 1), "unit": "%", "as_of": str(latest[0])[:10], "as_of_label": when}]
        return {"kind": "macro", "read_as": "%s \u2014 %s" % (name, title), "headline": headline, "lead": "", "figures": figs,
                "sources": [c for c in [src.get("credit"), src.get("release")] if c],
                "calc": ("Year on year = (index this period \u00f7 index a year earlier \u2212 1) \u00d7 100, from the published index; "
                         "it can differ from the published rate by 0.1 pp.") if index else None,
                "more": ["The chart of the whole history", "Every period in a table to download", "The release it came from"],
                "full_url": page or "us-treasury.html"}
    dp = 1 if index else min(2, len(("%.4f" % last).rstrip("0").split(".")[1]))
    shown = _num(last, dp) + ("%" if pct else (" " + unit if unit else ""))
    if not pct and unit in _BIG_UNITS:
        shown = _big(last, unit)
    elif not pct and unit.lower() in ("usd", "$"):
        shown = "$" + _num(last, dp)
    elif not pct and h.get("code") in _THOUSANDS:
        shown = "%s million" % _num(last / 1000.0, 2 if last < 20000 else 1)
        if len(rows) >= 2 and rows[-2][1] is not None:
            diff = last - rows[-2][1]
            shown += ", %s %s on the month" % ("up" if diff >= 0 else "down", "{:,.0f}".format(abs(diff) * 1000))
    lead = ""
    if index and mom:
        headline = "%s, %s: %s on the month before in %s." % (
            name, title, ("up " if last > 0 else "down " if last < 0 else "") + _num(last, 1) + "%", when)
        calc = "Month on month = (index this month ÷ index the month before − 1) × 100, from the published index (not seasonally adjusted unless the series is)."
    elif index and re.search(r"\bpeak|all.time high|record high|highest ever|highest since|when (?:was|did) .* (?:highest|peak)", ql) and not _year_in(q):
        top = max(rows, key=lambda r: r[1])
        recent = [r for r in rows if str(r[0])[:4] >= str(int(str(last_date)[:4]) - 10)]
        rtop = max(recent, key=lambda r: r[1]) if recent else top
        headline = "%s, %s peaked at %s%% in %s%s; it is %s%% in %s." % (
            name, title, _num(top[1], 1), _when(top[0], False),
            ("" if rtop is top else "; the recent peak was %s%% in %s" % (_num(rtop[1], 1), _when(rtop[0], False))), _num(last, 1), when)
        calc = "Year on year = (index this period \u00f7 index a year earlier \u2212 1) \u00d7 100, from the published index; the series on file begins %s." % _when(rows[0][0], False)
    elif index:
        headline = "%s, %s: %s on a year earlier in %s." % (
            name, title, ("up " if last > 0 else "down " if last < 0 else "") + _num(last, 1) + "%", when)
        calc = "Year on year = (index this period ÷ index a year earlier − 1) × 100, " \
               "from the published index; it can differ from the published rate by 0.1 pp."
        if re.search(r"rising|falling|trend|direction|accelerat|slowing|cooling|heating|going up|going down|coming down|picking up|easing", ql) and len(rows) >= 13:
            back3, back12 = rows[-4][1], rows[-13][1]
            word = "rising" if last > back3 + 0.05 else "falling" if last < back3 - 0.05 else "flat"
            lead = "That is %s: three months earlier it was %s%% and a year earlier %s%%." % (word, _num(back3, 1), _num(back12, 1))
    else:
        headline = "%s: %s on %s." % (title, shown, when) if daily else "%s: %s in %s." % (title, shown, when)
        calc = None
    # the figure carries the headline's own rounding, so the two never disagree (2.449: 2.4 in both)
    fig_dp = 1 if index else dp
    figs = [{"label": "Year on year" if index else "Latest", "value": round(last, fig_dp),
             "unit": "%" if pct else unit, "as_of": str(last_date)[:10], "as_of_label": when}]
    try:
        d = datetime.date.fromisoformat(str(last_date)[:10])
        year_ago = d.replace(year=d.year - 1).isoformat()
        prior = [r for r in rows if str(r[0])[:10] <= year_ago]
        if prior:
            figs.append({"label": "A year earlier", "value": round(prior[-1][1], fig_dp),
                         "unit": "%" if pct else unit, "as_of": str(prior[-1][0])[:10],
                         "as_of_label": _when(prior[-1][0], daily)})
    except ValueError:
        pass
    return {"kind": "macro", "read_as": "%s — %s" % (name, title), "headline": headline,
            "lead": lead, "figures": figs, "sources": [c for c in [src.get("credit"), src.get("release")] if c],
            "calc": calc,
            "more": ["The chart of the whole history", "Every period in a table to download",
                     "The release it came from, and how each figure was worked out"],
            "full_url": page or "us-treasury.html"}


def _company_named(q):
    try:
        from .. import sec_api, stock_recipes, us_link
        cur = sec_api._require()
        return bool(us_link.find_companies(cur, stock_recipes.MACRO_WORDS.sub(" ", stock_recipes._without_benchmarks(q))))
    except Exception:  # noqa: BLE001
        return False


def _is_macro(q):
    try:
        from .. import sec_api, stock_recipes
        try:
            cur = sec_api._require()
        except Exception:  # noqa: BLE001 — no company list: the words alone decide
            cur = None
        return stock_recipes.is_macro_question(cur, q)
    except Exception:  # noqa: BLE001
        return False


def _miss_kind(exc):
    from .. import ask_miss
    return getattr(exc, "miss", None) or ask_miss.classify(exc.detail if isinstance(exc.detail, str) else "")


def _cur():
    try:
        from .. import sec_api
        return sec_api._require()
    except Exception:                                             # noqa: BLE001 — suggestions without company names
        return None


@router.get("")
def preview(q: str = Query(..., min_length=2, max_length=300),
            prev: str = Query("", max_length=300, description="the reader's previous question, for a follow-up")):
    """The start of an answer for the front page: headline, up to two figures,
    the source, and what the full answer adds."""
    q = " ".join(q.split())
    prev = " ".join(prev.split()) if isinstance(prev, str) else ""          # called directly, the default is a Query
    from .. import ask_log, ask_miss
    # a question about the economy goes to the economy answers first: no model
    # call, no company read ("rent inflation" once found a landlord)
    if not prev and _is_macro(q):
        out = None
        try:
            out = _macro(q)
        except Exception:  # noqa: BLE001 — the stock route still gets its turn
            out = None
        if out is not None:
            ask_log.note("/api/v1/assistant/preview", q, True, out.get("kind"))
            out.setdefault("question", q)
            return out
    out, refusal = _stock(q, prev or None)
    # a stock question declined on principle (why, buy or sell, a forecast) is
    # not handed to the economy answers, which would match a word in it
    # ("will Apple beat earnings" is not a question about hourly earnings)
    declined = isinstance(refusal, HTTPException) and _miss_kind(refusal) == "policy"
    # a question that names a company is never answered with an economy series
    # ("Walmart on wages" is not a question about the wage tracker)
    if out is None and not declined and _company_named(q):
        declined = True
    if out is None and not declined:
        # a follow-up ("and Japan?") is read whole by the stock route's reader; that reading answers here
        whole = getattr(refusal, "question", None) if isinstance(refusal, HTTPException) else None
        out = _macro(whole) if whole and whole != q else None
        if out is None:
            out = _macro(q)
        if out is None and prev and not _is_macro(q):
            out = _macro(prev + " " + q)
    if out is None:
        # the same reason, in the same words, as a signed-in reader gets (app/ask_miss.py)
        if isinstance(refusal, HTTPException):
            r = ask_miss.reply(q, refusal, _cur())
        elif refusal == "error":
            r = ask_miss.reply(q, None)
        else:
            r = {"detail": ask_miss.NOTHING, "miss": "not_held", "try": ask_miss.EXAMPLES[:3]}
        ask_log.note("/api/v1/assistant/preview", q, False, r["miss"])
        return {"kind": "none", "question": q, "message": r["detail"], "miss": r["miss"], "try": r["try"],
                "holds": HOLDS}
    ask_log.note("/api/v1/assistant/preview", q, True, out.get("kind"))
    out.setdefault("question", q)
    return out
