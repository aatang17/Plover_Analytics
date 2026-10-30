# -*- coding: utf-8 -*-
"""The Charts and Screener tabs of Your Plover: a sentence becomes a chart
spec or a set of screen filters, which the page then runs itself.

  GET /api/v1/us/build/chart?q=Apple, Microsoft and Nvidia against the S&P this year
  GET /api/v1/us/build/screen?q=big companies still growing sales, cheap on earnings

Neither route returns a number. A chart spec names the companies (verified
against the SEC universe), the measure, the period, the benchmark and how
to display it; the page fetches the closes and the filed figures from the
same /api/v1/us endpoints every other page uses. A screen spec is a list of
filters, each with the words a reader sees ("Market value over $20bn"), and
the query the screener (/api/v1/us/financials/screener) runs.

Reading happens twice: the ask box's small model fills in a form (the same
reader as app/us_ask_model.py, on Plover's own key, under its daily cap),
and word patterns read the sentence when the model is off, capped or
wrong. Whatever read it, the companies and industries are resolved by
app/us_link.py, so a company the model invents is dropped, not charted.
"""
import datetime
import json
import re
import threading
from collections import OrderedDict

from fastapi import APIRouter, Query

from . import sec_api, us_ask_model, us_benchmarks, us_link

router = APIRouter(prefix="/api/v1/us/build", tags=["US build"])

# ---------------------------------------------------------------- charts
# What a chart can show. `how` tells the page where the series comes from:
# the price feed, a key-indicator field (app/sec_deep_api.py indicators),
# or a ratio of two of them.
MEASURES = OrderedDict([
    ("price", {"label": "Share price", "how": "price", "unit": "USD"}),
    ("revenue", {"label": "Revenue, quarterly", "how": "field", "field": "revenue", "unit": "USD"}),
    ("revenue_ttm", {"label": "Revenue, trailing 12 months", "how": "field_ttm", "field": "revenue", "unit": "USD"}),
    ("operating_margin", {"label": "Operating margin", "how": "ratio", "num": "operating_income", "den": "revenue", "unit": "%"}),
    ("net_margin", {"label": "Net margin", "how": "ratio", "num": "net_income", "den": "revenue", "unit": "%"}),
    ("net_income", {"label": "Net profit, quarterly", "how": "field", "field": "net_income", "unit": "USD"}),
    ("eps", {"label": "Earnings per share, diluted", "how": "field", "field": "eps_diluted", "unit": "USD/share"}),
    ("fcf", {"label": "Free cash flow, quarterly", "how": "fcf", "unit": "USD"}),
    ("rnd", {"label": "Research spending, quarterly", "how": "tag", "tag": "ResearchAndDevelopmentExpense", "unit": "USD"}),
    ("shares", {"label": "Shares outstanding", "how": "field", "field": "shares_outstanding", "unit": "shares"}),
])
MEASURE_WORDS = [
    ("operating_margin", r"operating margin|op(?:erating)? margins?"),
    ("net_margin", r"net margin|profit margin"),
    ("revenue_ttm", r"trailing|ttm|last twelve months|last 12 months"),
    ("revenue", r"revenue|sales|turnover|top line"),
    ("net_income", r"net (?:income|profit)|earnings\b(?! per)|profits?\b|bottom line"),
    ("eps", r"\beps\b|earnings per share"),
    ("fcf", r"free cash ?flow|\bfcf\b"),
    ("rnd", r"r ?& ?d|research"),
    ("shares", r"shares? outstanding|share count|buybacks? shrink"),
]
PERIODS = ("1m", "3m", "ytd", "1y", "5y", "max")
DISPLAYS = ("rebased", "actual", "yoy")

CHART_FORM_KEYS = ("companies", "measure", "period", "from", "to", "benchmark", "display", "log",
                   "chart_type", "mark_results", "read_as")
CHART_RULES = """You read one request for a chart of US-listed stocks and fill in a form. Today is %(today)s.
Reply with one JSON object, these keys only:
{"companies": [{"as_written": <the words naming the company>, "ticker": <its US ticker or null>}, ... in order, [] when none],
 "measure": <one of: %(measures)s>,
 "period": <one of: 1m, 3m, ytd, 1y, 5y, max, or null when "from" is given>,
 "from": "YYYY-MM-DD" or null, "to": "YYYY-MM-DD" or null,
 "benchmark": <the index or fund to draw beside the stocks: %(benchmarks)s; or null; "none" when the request says to drop it>,
 "display": "rebased" (each line starts at 100, the default for a price chart of several stocks), "actual" (the values themselves), or "yoy" (change from a year earlier, %%),
 "log": true for a log scale, else false,
 "chart_type": "line" or "bars",
 "mark_results": true when results or earnings days should be marked,
 "read_as": <one short plain sentence saying what will be drawn, naming the period>}
Rules:
- Never invent a company. "as_written" is copied from the request; the ticker is yours ("google" is GOOGL, "meta" is META).
- "this year" or "year to date" is ytd; "past year" or "12 months" is 1y; "five years" is 5y; "since 2015" is from 2015-01-01; nothing said about time is ytd for prices and 5y for filed figures.
- "S&P", "S&P 500", "the market" is sp500; "Nasdaq" is nasdaq100; "Dow" is dow; sector words name the sector fund (tech, financials, energy, health, industrials, consumer_disc, staples, utilities, materials, real_estate, communication, semis, regional_banks, gold, treasuries).
- A price chart of stocks with no other instruction gets benchmark sp500 and display rebased; a filed figure (revenue, margin, EPS, cash flow, research, shares) gets display actual and no benchmark.
- "revenue growth" or "year on year" is display yoy of that measure. "margin" with no qualifier is operating_margin.
- A request to change a chart ("add Amazon", "make it revenue", "drop the S&P", "log scale", "since 2020") names only the change; the current chart is given as JSON: return the whole form with that change applied."""

SCREEN_FORM_KEYS = ("filters", "industry", "sp500", "exchanges", "sort_key", "sort_order", "read_as")
# Every filter the screener takes, with the words a reader sees. `unit` says
# how a value is written: $ (dollars), % (percent), x (a multiple).
FILTERS = OrderedDict([
    ("market_cap_min", {"label": "Market value over", "unit": "$", "metric": "market_cap"}),
    ("market_cap_max", {"label": "Market value under", "unit": "$", "metric": "market_cap"}),
    ("min_revenue", {"label": "Revenue over", "unit": "$", "metric": "revenue"}),
    ("pe_min", {"label": "Price / earnings over", "unit": "x", "metric": "pe_x"}),
    ("pe_max", {"label": "Price / earnings under", "unit": "x", "metric": "pe_x"}),
    ("revenue_growth_min", {"label": "Revenue growth over", "unit": "%", "metric": "revenue_growth_pct"}),
    ("profit_growth_min", {"label": "Profit growth over", "unit": "%", "metric": "profit_growth_pct"}),
    ("operating_margin_min", {"label": "Operating margin over", "unit": "%", "metric": "operating_margin_pct"}),
    ("net_margin_min", {"label": "Net margin over", "unit": "%", "metric": "net_margin_pct"}),
    ("fcf_margin_min", {"label": "Free cash flow margin over", "unit": "%", "metric": "fcf_margin_pct"}),
    ("roe_min", {"label": "Return on equity over", "unit": "%", "metric": "roe_pct"}),
    ("dividend_yield_min", {"label": "Dividend yield over", "unit": "%", "metric": "dividend_yield_pct"}),
    ("buyback_yield_min", {"label": "Buyback yield over", "unit": "%", "metric": "buyback_yield_pct"}),
    ("price_change_1m_min", {"label": "Up over the last month by at least", "unit": "%", "metric": "price_change_1m_pct"}),
    ("price_change_1m_max", {"label": "Change over the last month at most", "unit": "%", "metric": "price_change_1m_pct"}),
    ("price_change_1y_min", {"label": "Up over the last year by at least", "unit": "%", "metric": "price_change_1y_pct"}),
    ("price_change_1y_max", {"label": "Change over the last year at most", "unit": "%", "metric": "price_change_1y_pct"}),
    ("equity_ratio_min", {"label": "Equity / assets over", "unit": "%", "metric": "equity_ratio_pct"}),
    ("cash_to_assets_min", {"label": "Cash / assets over", "unit": "%", "metric": "cash_to_assets_pct"}),
])
SORT_KEYS = ("market_cap", "pe_x", "revenue_growth_pct", "profit_growth_pct", "operating_margin_pct", "net_margin_pct",
             "fcf_margin_pct", "roe_pct", "dividend_yield_pct", "buyback_yield_pct", "price_change_1m_pct",
             "price_change_1y_pct", "revenue")
SCREEN_RULES = """You read one request to find US-listed stocks and turn it into screen filters. Today is %(today)s.
Reply with one JSON object, these keys only:
{"filters": [{"key": <one of the keys below>, "value": <a number>}, ...],
 "industry": <industry words from the request ("banks", "semiconductors", "software"), or null>,
 "sp500": true when the request limits itself to the S&P 500, else false,
 "exchanges": ["NYSE", "Nasdaq"] or [],
 "sort_key": <one of: %(sort_keys)s>, "sort_order": "asc" or "desc",
 "read_as": <one short plain sentence saying what will be listed>}
Keys (value units): %(filters)s
Rules:
- Dollar values are plain numbers: $20bn is 20000000000, $500mn is 500000000. Percents are plain numbers: 5%% is 5. A multiple is plain: 18x is 18.
- "big", "large" or "major" companies with no figure means market_cap_min 20000000000; "mega" means 200000000000; "small" means market_cap_max 2000000000.
- "cheap", "value", "low multiple" with no figure means pe_max 18 and pe_min 5 (a P/E under 5 is usually a one-off gain). "expensive" means pe_min 40.
- "growing", "still growing", "sales growth" with no figure means revenue_growth_min 5. "fast growing" means 20.
- "profitable" means net_margin_min 0. "high margin" means operating_margin_min 20. "dividend payers" means dividend_yield_min 0.01.
- "down X%% this month" (or "last month", "past month") is price_change_1m_max -X; "up X%% this month" is price_change_1m_min X; "fallen this month" with no figure is price_change_1m_max 0. The same words with "this year", "past year" or "12 months" use the 1y keys.
- Sorting: "cheapest first" is pe_x asc; "biggest first" is market_cap desc; "fastest growing" is revenue_growth_pct desc; "highest dividend" is dividend_yield_pct desc; "best performing" is price_change_1y_pct desc (1m when the request says month). Say nothing about order: pe_x asc when a P/E filter is set, else market_cap desc.
- Never add a filter the request does not imply. Never invent a number."""

_LOCK = threading.Lock()
_CACHE = OrderedDict()
_CACHE_MAX = 2000
# "tech" is not an SIC industry: computers, communications equipment, semiconductors and software
TECH_SIC = ["357", "366", "367", "737"]


def _today():
    return datetime.date.today()


# ------------------------------------------------------------ the reader

def _read_form(kind, question, rules, keys, context=None):
    """The model's form for a request, or None when the reader is off,
    capped, failed or unreadable. `context` is the current chart (JSON) a
    change applies to."""
    cfg = us_ask_model.provider()
    if cfg is None:
        return None
    key_ = (kind, _today().isoformat(), us_ask_model._norm(question), json.dumps(context, sort_keys=True) if context else "")
    with _LOCK:
        if key_ in _CACHE:
            _CACHE.move_to_end(key_)
            return dict(_CACHE[key_])
    if not us_ask_model.under_cap():
        return None
    prov, key, model = cfg
    user = (question or "").strip()[:300]
    if context:
        user = "Current chart: %s\nChange: %s" % (json.dumps(context), user)
    messages = [{"role": "system", "content": rules}, {"role": "user", "content": user}]
    us_ask_model._tick("calls")
    try:
        out = us_ask_model._complete(prov, key, model, messages, max_tokens=400, cache_key="plover-us-build-" + kind,
                                     effort="low")
    except Exception:                                             # noqa: BLE001 — a timeout, a refusal, a bad reply
        us_ask_model._tick("failed")
        return None
    tokens = out.get("usage") or {}
    m = re.search(r"\{.*\}", out.get("text") or "", re.S)
    try:
        form = json.loads(m.group(0)) if m else None
    except ValueError:
        form = None
    if not isinstance(form, dict):
        us_ask_model._tick("failed", tokens)
        return None
    form = {k: form.get(k) for k in keys}
    us_ask_model._tick("matched", tokens)
    with _LOCK:
        _CACHE[key_] = dict(form)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return form


def _cur():
    try:
        return sec_api._require()
    except Exception:                                             # noqa: BLE001 — no SEC database here
        return None


# ------------------------------------------------------------------ charts

def _company(cur, ticker=None, words=None):
    """One company record from the universe, by ticker first, then by the
    words that named it; None when neither resolves."""
    if cur is None:
        return None
    idx = us_link.companies(cur)
    t = (ticker or "").strip().upper().replace(".", "-")
    rec = idx["by_ticker"].get(t) or idx["by_ticker"].get(t.replace("-", ".")) if t else None
    if rec is None and words:
        hits = us_link.find_companies(cur, words, limit=1)
        rec = hits[0] if hits else None
    if rec is None:
        return None
    # the name every US page shows ("NVIDIA Corporation", not the SEC's "NVIDIA CORP")
    return {"ticker": rec.get("ticker"), "name": us_link.company_name(rec["cik"], rec.get("name"), cur), "cik": rec.get("cik"),
            "sec_code": "cik:%d" % rec["cik"]}


def _benchmark_words(q):
    for key, b in us_benchmarks.BENCHMARKS.items():
        if re.search(b["words"], q, re.I):
            return key
    return None


def _period_words(q):
    """(preset, from, to) from the words of a request."""
    ql = q.lower()
    m = re.search(r"since (\d{4})\b", ql)
    if m:
        return None, "%s-01-01" % m.group(1), None
    m = re.search(r"\b(?:in|during|for) (20\d\d)\b", ql)
    if m:
        y = int(m.group(1))
        end = "%d-12-31" % y if y < _today().year else None
        return None, "%d-01-01" % y, end
    if re.search(r"this year|year to date|\bytd\b", ql):
        return "ytd", None, None
    if re.search(r"(past|last) (12|twelve) months|(past|last|one|1) year\b|12 months", ql):
        return "1y", None, None
    if re.search(r"(five|5)[ -]?years?|5y\b", ql):
        return "5y", None, None
    if re.search(r"(three|3)[ -]?months?|quarter\b|3m\b", ql):
        return "3m", None, None
    if re.search(r"(past|last|one|1|this) month|30 days|1m\b", ql):
        return "1m", None, None
    if re.search(r"all time|whole history|as far back|max(imum)?\b|ever\b", ql):
        return "max", None, None
    return None, None, None


def _measure_words(q):
    ql = q.lower()
    for key, pat in MEASURE_WORDS:
        if re.search(pat, ql):
            return key
    return "price"


def _chart_by_words(cur, q, current=None):
    """The word-pattern reading of a chart request; a change applies to `current`."""
    spec = dict(current or {})
    ql = q.lower()
    found = us_link.find_companies(cur, q, limit=6) if cur is not None else []
    cos = [_company(cur, c.get("ticker")) for c in found]
    cos = [c for c in cos if c]
    drop = re.match(r"\s*(remove|drop|take (?:out|off)|without)\b", ql)
    if current:
        have = list(current.get("series") or [])
        if drop:
            gone = {c["ticker"] for c in cos}
            have = [s for s in have if s["ticker"] not in gone]
            if re.search(r"s ?& ?p|benchmark|index|the market", ql):
                spec["benchmark"] = None
        else:
            for c in cos:
                if all(s["ticker"] != c["ticker"] for s in have):
                    have.append(c)
        spec["series"] = have[:6]
    else:
        spec["series"] = cos[:6]
    measure = _measure_words(q) if (not current or re.search("|".join(p for _, p in MEASURE_WORDS) + r"|price", ql)) else current.get("measure")
    spec["measure"] = measure or "price"
    # "trailing 12 months" names the measure, not the period
    preset, start, end = _period_words(re.sub(r"trailing(?: 12| twelve)? months?|\bttm\b|last twelve months", " ", ql))
    if preset or start:
        spec["period"] = preset
        spec["from"], spec["to"] = start, end
    elif not current:
        spec["period"] = "ytd" if spec["measure"] == "price" else "5y"
        spec["from"] = spec["to"] = None
    b = _benchmark_words(q)
    if b and not drop:
        spec["benchmark"] = b
    elif not current:
        spec["benchmark"] = "sp500" if spec["measure"] == "price" else None
    if re.search(r"\byoy\b|year on year|year-on-year|growth\b", ql):
        spec["display"] = "yoy"
    elif re.search(r"actual|in dollars|not rebased|raw", ql):
        spec["display"] = "actual"
    elif re.search(r"rebase|indexed to 100|start(ing)? at 100", ql) or not current:
        spec["display"] = "rebased" if spec["measure"] == "price" else "actual"
    if re.search(r"\blog\b|logarithmic", ql):
        spec["log"] = not re.search(r"(no|drop|remove|linear)", ql)
    elif not current:
        spec["log"] = False
    if re.search(r"\bbars?\b|columns?", ql):
        spec["chart_type"] = "bars"
    elif re.search(r"\bline\b", ql) or not current:
        spec["chart_type"] = "line"
    if re.search(r"results|earnings day", ql):
        spec["mark_results"] = not drop
    elif not current:
        spec["mark_results"] = False
    return spec


def _chart_from_form(cur, form, current=None):
    spec = dict(current or {})
    series = []
    for c in form.get("companies") if isinstance(form.get("companies"), list) else []:
        if not isinstance(c, dict):
            continue
        rec = _company(cur, c.get("ticker"), c.get("as_written"))
        if rec and all(s["ticker"] != rec["ticker"] for s in series):
            series.append(rec)
    if series or not current:
        spec["series"] = series[:6]
    measure = form.get("measure") if form.get("measure") in MEASURES else (current or {}).get("measure") or "price"
    spec["measure"] = measure
    preset = form.get("period") if form.get("period") in PERIODS else None
    start, end = form.get("from"), form.get("to")
    ok = lambda d: isinstance(d, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", d)  # noqa: E731
    if preset or ok(start):
        spec["period"], spec["from"], spec["to"] = preset, (start if ok(start) else None), (end if ok(end) else None)
    elif not current:
        spec["period"], spec["from"], spec["to"] = ("ytd" if measure == "price" else "5y"), None, None
    b = form.get("benchmark")
    if b == "none":
        spec["benchmark"] = None
    elif b in us_benchmarks.BENCHMARKS:
        spec["benchmark"] = b
    elif not current:
        spec["benchmark"] = "sp500" if measure == "price" else None
    d = form.get("display")
    spec["display"] = d if d in DISPLAYS else (current or {}).get("display") or ("rebased" if measure == "price" else "actual")
    spec["log"] = bool(form.get("log")) if form.get("log") is not None else bool((current or {}).get("log"))
    ct = form.get("chart_type")
    spec["chart_type"] = ct if ct in ("line", "bars") else (current or {}).get("chart_type") or "line"
    mr = form.get("mark_results")
    spec["mark_results"] = bool(mr) if mr is not None else bool((current or {}).get("mark_results"))
    return spec


def _chart_title(spec):
    m = MEASURES.get(spec["measure"], MEASURES["price"])
    names = [s["name"] or s["ticker"] for s in spec.get("series") or []]
    b = us_benchmarks.BENCHMARKS.get(spec.get("benchmark") or "") if spec["measure"] == "price" else None
    who = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + (names[-1] if names else "")
    if b and names:
        who += " against the " + b["name"]
    elif b:
        who = b["name"]
    how = {"rebased": ", rebased to 100", "yoy": ", change on a year earlier", "actual": ""}[spec.get("display") or "actual"]
    return (m["label"] + how + ": " + who) if who else m["label"] + how


@router.get("/chart", openapi_extra={"x-example": "/api/v1/us/build/chart?q=Apple and Microsoft against the S&P 500 this year"})
def chart(q: str = Query(..., min_length=1, max_length=300, description="the chart, in plain words"),
          current: str = Query("", max_length=4000, description="the chart on screen, as JSON, when q is a change to it")):
    """A chart spec for a request in words: the companies (verified), the
    measure, the period, the benchmark and the display. The page draws it."""
    cur = _cur()
    try:
        cur_spec = json.loads(current) if current.strip() else None
        if not isinstance(cur_spec, dict):
            cur_spec = None
    except ValueError:
        cur_spec = None
    rules = CHART_RULES % {"today": _today().isoformat(), "measures": ", ".join(MEASURES),
                           "benchmarks": ", ".join(us_benchmarks.BENCHMARKS)}
    form = _read_form("chart", q, rules, CHART_FORM_KEYS, cur_spec)
    how = "model"
    spec = _chart_from_form(cur, form, cur_spec) if form else None
    if spec is None or (not spec.get("series") and not spec.get("benchmark")):
        spec, how = _chart_by_words(cur, q, cur_spec), "words"
    spec["title"] = _chart_title(spec)
    read_as = (form or {}).get("read_as") if form else None
    return {"question": q, "spec": spec, "read_as": read_as if isinstance(read_as, str) else spec["title"],
            "how": how, "measures": [{"id": k, "label": v["label"], "how": v["how"], "unit": v["unit"]} for k, v in MEASURES.items()],
            "benchmarks": [{"id": k, "name": b["name"], "fund": b["etf"]} for k, b in us_benchmarks.BENCHMARKS.items()]}


# ------------------------------------------------------------------ screens

def _money(v):
    if v >= 1e12:
        return "$%gtn" % round(v / 1e12, 1)
    if v >= 1e9:
        return "$%gbn" % round(v / 1e9, 1)
    if v >= 1e6:
        return "$%gmn" % round(v / 1e6, 1)
    return "$%g" % v


def filter_text(key, value):
    f = FILTERS[key]
    if key.startswith("price_change_") and value == 0:
        when = "month" if "_1m_" in key else "year"
        return ("Fell over the last %s" if key.endswith("_max") else "Rose over the last %s") % when
    if key.startswith("price_change_") and key.endswith("_max") and value < 0:
        return "Down over the last %s by at least %g%%" % ("month" if "_1m_" in key else "year", -value)
    if f["unit"] == "$":
        return "%s %s" % (f["label"], _money(value))
    if f["unit"] == "x":
        return "%s %g×" % (f["label"], value)
    return "%s %s%g%%" % (f["label"], "−" if value < 0 else "", abs(value))


def _num_words(s):
    """'$20bn' -> 2e10, '500 million' -> 5e8, '18' -> 18."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(tn|trillion|bn|billion|b|mn|million|m|k|thousand)?", s.lower())
    if not m:
        return None
    v = float(m.group(1))
    return v * {"tn": 1e12, "trillion": 1e12, "bn": 1e9, "billion": 1e9, "b": 1e9, "mn": 1e6, "million": 1e6, "m": 1e6,
                "k": 1e3, "thousand": 1e3, None: 1}[m.group(2)]


def _screen_by_words(cur, q):
    ql = " " + re.sub(r"\s+", " ", q.lower()) + " "
    filters = OrderedDict()
    sort_key, sort_order = None, None

    def add(key, value):
        filters[key] = value

    for m in re.finditer(r"(market (?:value|cap)(?:italisation|italization)?|worth|valued at|revenue|sales)\s*(over|above|more than|at least|under|below|less than|up to)\s*\$?\s*([\d.]+\s*(?:tn|trillion|bn|billion|mn|million|b|m)?)", ql):
        v = _num_words(m.group(3))
        if v is None:
            continue
        big = m.group(2) in ("over", "above", "more than", "at least")
        if m.group(1) in ("revenue", "sales"):
            if big:
                add("min_revenue", v)
        else:
            add("market_cap_min" if big else "market_cap_max", v)
    if "market_cap_min" not in filters and "market_cap_max" not in filters:
        if re.search(r"\bmega.?caps?\b", ql):
            add("market_cap_min", 2e11)
        elif re.search(r"\b(big|large|major|blue.?chip)\b", ql):
            add("market_cap_min", 2e10)
        elif re.search(r"\bsmall.?caps?\b|\bsmall companies\b", ql):
            add("market_cap_max", 2e9)
    for m in re.finditer(r"(?:p/?e|price[- ]to[- ]earnings|price/earnings|earnings multiple|multiple)\s*(?:ratio )?(over|above|more than|under|below|less than|of at most|of at least)\s*([\d.]+)", ql):
        v = float(m.group(2))
        add("pe_min" if m.group(1) in ("over", "above", "more than", "of at least") else "pe_max", v)
    for m in re.finditer(r"(over|above|more than|under|below|less than)\s*([\d.]+)\s*(?:x|×|times)\s*earnings", ql):
        add("pe_min" if m.group(1) in ("over", "above", "more than") else "pe_max", float(m.group(2)))
    if "pe_max" not in filters and "pe_min" not in filters:
        if re.search(r"\bcheap|\bvalue stocks?|low (?:p/?e|multiple)|inexpensive", ql):
            add("pe_max", 18.0)
            add("pe_min", 5.0)
        elif re.search(r"\bexpensive|high (?:p/?e|multiple)", ql):
            add("pe_min", 40.0)
    m = re.search(r"(?:revenue|sales) (?:growth|growing)\s*(?:of |over |above |at least )?([\d.]+)\s*%", ql) or \
        re.search(r"growing (?:revenue|sales)\s*(?:by )?(?:over |more than |at least )?([\d.]+)\s*%", ql)
    if m:
        add("revenue_growth_min", float(m.group(1)))
    elif re.search(r"fast.?growing|rapid growth|high growth", ql):
        add("revenue_growth_min", 20.0)
    elif re.search(r"growing|growth|still growing", ql) and not re.search(r"dividend growth|earnings growth|profit growth", ql):
        add("revenue_growth_min", 5.0)
    m = re.search(r"(?:profit|earnings) growth\s*(?:of |over |above )?([\d.]+)\s*%", ql)
    if m:
        add("profit_growth_min", float(m.group(1)))
    if re.search(r"\bprofitable\b|making (?:a )?profit|in profit", ql):
        add("net_margin_min", 0.0)
    m = re.search(r"(?:operating )?margins?\s*(?:over|above|of at least|more than)\s*([\d.]+)\s*%", ql)
    if m:
        add("operating_margin_min", float(m.group(1)))
    elif re.search(r"high.?margin", ql):
        add("operating_margin_min", 20.0)
    m = re.search(r"dividend(?: yield)?s?\s*(?:over|above|of at least|more than|of)\s*([\d.]+)\s*%", ql)
    if m:
        add("dividend_yield_min", float(m.group(1)))
    elif re.search(r"dividend", ql):
        add("dividend_yield_min", 0.01)
    if re.search(r"buy(?:ing)? back|buybacks?|repurchas", ql):
        add("buyback_yield_min", 1.0)
    m = re.search(r"\broe\b|return on equity", ql)
    if m:
        n = re.search(r"(?:roe|return on equity)\s*(?:over|above|of at least|more than)\s*([\d.]+)", ql)
        add("roe_min", float(n.group(1)) if n else 15.0)
    # price moves: "down 10% this month", "up 20% this year", "fallen this month"
    for m in re.finditer(r"\b(down|fallen|fell|dropped|lost|up|risen|rose|gained)\s*(?:by |more than |over |at least )?([\d.]+)?\s*%?\s*(?:in |over |during )?(?:the )?(this|the last|the past|last|past|a)?\s*(month|30 days|year|12 months|twelve months)", ql):
        down = m.group(1) in ("down", "fallen", "fell", "dropped", "lost")
        v = float(m.group(2)) if m.group(2) else 0.0
        horizon = "1m" if m.group(4) in ("month", "30 days") else "1y"
        add("price_change_%s_%s" % (horizon, "max" if down else "min"), -v if down else v)
    sp500 = bool(re.search(r"s ?& ?p ?500|\bsp ?500\b|s and p 500|\bspx\b", ql))
    exchanges = [e for e, pat in (("Nasdaq", r"\bnasdaq\b"), ("NYSE", r"\bnyse\b|new york stock exchange")) if re.search(pat, ql)]
    industry = None
    hit = us_link.industry_match(cur, q) if cur is not None else None
    if hit:
        industry = {"sic": hit[0], "label": hit[1]}
    elif re.search(r"\btech(nology)?\b", ql):
        industry = {"sic": TECH_SIC, "label": "technology (SIC 357, 366, 367, 737)"}
    if re.search(r"cheapest|lowest (?:p/?e|multiple)", ql):
        sort_key, sort_order = "pe_x", "asc"
    elif re.search(r"most expensive|highest (?:p/?e|multiple)", ql):
        sort_key, sort_order = "pe_x", "desc"
    elif re.search(r"biggest|largest", ql):
        sort_key, sort_order = "market_cap", "desc"
    elif re.search(r"smallest", ql):
        sort_key, sort_order = "market_cap", "asc"
    elif re.search(r"fastest.?growing|highest growth", ql):
        sort_key, sort_order = "revenue_growth_pct", "desc"
    elif re.search(r"highest (?:dividend|yield)", ql):
        sort_key, sort_order = "dividend_yield_pct", "desc"
    elif re.search(r"best perform|biggest (?:gain|rise|winner)", ql):
        sort_key, sort_order = ("price_change_1m_pct" if re.search(r"month", ql) else "price_change_1y_pct"), "desc"
    elif re.search(r"worst perform|biggest (?:fall|loser|drop)", ql):
        sort_key, sort_order = ("price_change_1m_pct" if re.search(r"month", ql) else "price_change_1y_pct"), "asc"
    return {"filters": [{"key": k, "value": v} for k, v in filters.items()], "industry": industry, "sp500": sp500,
            "exchanges": exchanges, "sort_key": sort_key, "sort_order": sort_order}


def _screen_from_form(cur, form):
    filters = OrderedDict()
    for f in form.get("filters") if isinstance(form.get("filters"), list) else []:
        if not isinstance(f, dict) or f.get("key") not in FILTERS:
            continue
        try:
            filters[f["key"]] = float(f.get("value"))
        except (TypeError, ValueError):
            continue
    industry = None
    words = form.get("industry")
    if isinstance(words, str) and words.strip() and cur is not None:
        hit = us_link.industry_match(cur, words)
        if hit:
            industry = {"sic": hit[0], "label": hit[1]}
    ex = [e for e in (form.get("exchanges") if isinstance(form.get("exchanges"), list) else [])
          if isinstance(e, str) and e.strip().lower() in ("nyse", "nasdaq", "otc", "cboe", "txse")]
    sk = form.get("sort_key") if form.get("sort_key") in SORT_KEYS else None
    so = form.get("sort_order") if form.get("sort_order") in ("asc", "desc") else None
    return {"filters": [{"key": k, "value": v} for k, v in filters.items()], "industry": industry,
            "sp500": bool(form.get("sp500")), "exchanges": ex, "sort_key": sk, "sort_order": so}


def _finish_screen(spec):
    """The chips a reader sees and the screener's query string."""
    chips, params = [], {}
    for f in spec["filters"]:
        chips.append({"id": f["key"], "text": filter_text(f["key"], f["value"]), "key": f["key"], "value": f["value"]})
        params[f["key"]] = ("%d" % f["value"]) if float(f["value"]).is_integer() else ("%g" % f["value"])
    if spec.get("industry"):
        chips.append({"id": "industry", "text": "Industry: " + spec["industry"]["label"], "key": "industry",
                      "value": spec["industry"]["label"]})
        params["sic"] = ",".join(spec["industry"]["sic"])
    if spec.get("sp500"):
        chips.append({"id": "sp500", "text": "In the S&P 500", "key": "sp500", "value": True})
        params["sp500"] = "true"
    if spec.get("exchanges"):
        chips.append({"id": "exchanges", "text": "Listed on " + " or ".join(spec["exchanges"]), "key": "exchanges",
                      "value": spec["exchanges"]})
        params["exchange"] = ",".join(spec["exchanges"])
    sk, so = spec.get("sort_key"), spec.get("sort_order")
    if not sk:
        sk, so = ("pe_x", "asc") if any(f["key"].startswith("pe_") for f in spec["filters"]) else ("market_cap", "desc")
    so = so or ("asc" if sk == "pe_x" else "desc")
    spec["sort_key"], spec["sort_order"] = sk, so
    params["sort"], params["order"] = sk, so
    chips.append({"id": "sort", "text": "Sorted: " + sort_text(sk, so), "key": "sort", "value": [sk, so]})
    return chips, params


SORT_WORDS = {
    "market_cap": ("biggest first", "smallest first"), "pe_x": ("most expensive first", "cheapest first"),
    "revenue_growth_pct": ("fastest growing first", "slowest growing first"),
    "profit_growth_pct": ("fastest profit growth first", "slowest profit growth first"),
    "operating_margin_pct": ("highest margin first", "lowest margin first"),
    "net_margin_pct": ("highest net margin first", "lowest net margin first"),
    "fcf_margin_pct": ("highest cash margin first", "lowest cash margin first"),
    "roe_pct": ("highest return on equity first", "lowest return on equity first"),
    "dividend_yield_pct": ("highest dividend first", "lowest dividend first"),
    "buyback_yield_pct": ("biggest buybacks first", "smallest buybacks first"),
    "price_change_1m_pct": ("best month first", "worst month first"),
    "price_change_1y_pct": ("best year first", "worst year first"),
    "revenue": ("most revenue first", "least revenue first"),
}


def sort_text(key, order):
    pair = SORT_WORDS.get(key, (key + " high first", key + " low first"))
    return pair[0] if order == "desc" else pair[1]


@router.get("/screen", openapi_extra={"x-example": "/api/v1/us/build/screen?q=big companies still growing sales, cheap on earnings"})
def screen(q: str = Query(..., min_length=1, max_length=300, description="what to look for, in plain words")):
    """Screen filters for a request in words, each with the words a reader
    sees, and the query /api/v1/us/financials/screener runs."""
    cur = _cur()
    rules = SCREEN_RULES % {"today": _today().isoformat(), "sort_keys": ", ".join(SORT_KEYS),
                            "filters": "; ".join("%s (%s)" % (k, {"$": "US dollars", "%": "percent", "x": "a multiple"}[v["unit"]])
                                                 for k, v in FILTERS.items())}
    form = _read_form("screen", q, rules, SCREEN_FORM_KEYS)
    spec, how = (_screen_from_form(cur, form), "model") if form else (None, "words")
    if spec is None or not (spec["filters"] or spec.get("industry") or spec.get("sp500") or spec.get("exchanges")):
        spec, how = _screen_by_words(cur, q), "words"
    chips, params = _finish_screen(spec)
    read_as = (form or {}).get("read_as") if form else None
    return {"question": q, "spec": spec, "chips": chips, "params": params, "how": how,
            "read_as": read_as if isinstance(read_as, str) else None,
            "menu": [{"key": k, "label": v["label"], "unit": v["unit"]} for k, v in FILTERS.items()]}


@router.get("/screen/chips", include_in_schema=False)
def chips(filters: str = Query("", max_length=2000, description="JSON: the spec's filters, industry, sp500, exchanges, sort")):
    """The chips and query for a spec the page edited itself (a chip removed, a filter added)."""
    try:
        spec = json.loads(filters) if filters.strip() else {}
    except ValueError:
        spec = {}
    clean = _screen_from_form(_cur(), {"filters": spec.get("filters"), "industry": None, "sp500": spec.get("sp500"),
                                       "exchanges": spec.get("exchanges"), "sort_key": spec.get("sort_key"),
                                       "sort_order": spec.get("sort_order")})
    ind = spec.get("industry")
    if isinstance(ind, dict) and isinstance(ind.get("sic"), list) and ind.get("label"):
        clean["industry"] = {"sic": [str(s) for s in ind["sic"] if str(s).isdigit()], "label": str(ind["label"])[:80]}
    ch, params = _finish_screen(clean)
    return {"spec": clean, "chips": ch, "params": params}
