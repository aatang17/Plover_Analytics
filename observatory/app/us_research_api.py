# -*- coding: utf-8 -*-
"""Research a Stock: one US company, the brief the reader chooses.

The front page's Research a Stock task (web/assets/home-tasks.js) reads this.
Nothing new is collected: each part is the answer of the function that
already serves it, so the brief cannot disagree with the company's own pages.

  company          who it is (app/us_company_api.py)
  business         the opening of the Business section of its latest 10-K,
                   in the company's own words (app/sec_text_api.py)
  key figures      ratios from its latest annual report with today's price
                   (app/sec_api.py metrics(), the screener's row)
  years/quarters   the figures the reader picks, by year or by quarter, as
                   last filed: every filing since 2009 for S&P 500 companies
                   (app/sec_deep_api.py), the SEC's quarterly data sets for
                   the rest (app/sec_api.py company())
  peers            the reader's own list, or the five companies in its SIC
                   industry closest to it in market value, on the screener's rows
  holders          the largest 13F holders (app/sec_13f_api.py)
  insiders         open-market buying and selling, last 12 months
  risks            the risk headings of its 10-K, in the report's order
  topics           the passages of its 10-K that mention the reader's words
  questions        the reader's own questions, answered by the question box
                   (app/stock_recipes.py); signed-in readers only
  news             its latest press releases (app/us_press_api.py)
  notes            the reader's own words, sent with a download only

Every choice is in the address (`s`, `per`, `n`, `f`, `pc`, `p`, `h`, `r`,
`k`, `ask`), so a tailored brief can be linked to and cited. The brief is
returned twice: as data, and as one plain document (`doc`) the page draws
and app/doc_render.py turns into PDF, Word or Excel, so a download always
matches the page.

Figures worked out here are named in `calc`. A part with no data is named in
`missing`, with the reason; it is never filled in. Only an unknown company
is an error.
"""
import datetime
import math
import re
import statistics
import threading
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import doc_render, sec_api, sec_metrics, us_benchmarks, us_link, us_prices_api

router = APIRouter(prefix="/api/v1/us/research", tags=["US research a stock"])

SITE = "ploveranalytics.com"
BUSINESS_CHARS = 600      # the opening of the Business section, cut at a sentence end
NEWS = 5
SIZE_RATIO = 25           # an automatic peer's market value is within 25 times the company's, either way
MAX_PEERS = 8
MAX_TOPICS = 5
MAX_QUESTIONS = 3
QUOTES_PER_TOPIC = 3
MINUS = u"−"
DASH = u"—"

SECTIONS = [
    ("business", "Business"),
    ("years", "Results by Year or Quarter"),
    ("peers", "Against Its Peers"),
    ("holders", "Largest Fund Holders"),
    ("insiders", "Insider Trades"),
    ("risks", "Main Risks"),
    ("topics", "Your Topics in the Annual Report"),
    ("questions", "Your Questions"),
    ("news", "Latest Announcements"),
    ("notes", "Your Notes"),
]
SECTION_KEYS = [k for k, _ in SECTIONS]
DEFAULT_SECTIONS = list(SECTION_KEYS)

# figures by year or quarter: (key, label, kind); kind says how it is shown
FIGURES = [
    ("revenue", "Revenue", "usd"),
    ("revenue_growth", "Revenue Growth", "pct+"),
    ("operating_income", "Operating Income", "usd"),
    ("operating_margin", "Operating Margin", "pct"),
    ("net_income", "Net Income", "usd"),
    ("net_margin", "Net Margin", "pct"),
    ("eps_diluted", "Diluted EPS", "eps"),
    ("cf_operating", "Operating Cash Flow", "usd"),
    ("capex", "Capital Spending", "usd"),
    ("fcf", "Free Cash Flow", "usd"),
    ("fcf_margin", "Free Cash Flow Margin", "pct"),
    ("cash", "Cash", "usd"),
    ("total_assets", "Total Assets", "usd"),
    ("total_liabilities", "Total Liabilities", "usd"),
    ("equity", "Shareholders' Equity", "usd"),
    ("buybacks", "Share Buybacks", "usd"),
    ("dividends_paid", "Dividends Paid", "usd"),
    ("shares_outstanding", "Shares Outstanding", "count"),
]
FIGURE_KEYS = [k for k, _, _ in FIGURES]
DEFAULT_FIGURES = ["revenue", "revenue_growth", "operating_margin", "net_income", "net_margin", "fcf", "eps_diluted"]

# the peer table's columns, all from the screener's row: (key, label, kind, where)
PEER_COLS = [
    ("market_cap", "Market Value", "usd", "metrics"),
    ("revenue", "Revenue", "usd", "size"),
    ("revenue_growth_pct", "Revenue Growth", "pct+", "metrics"),
    ("operating_margin_pct", "Operating Margin", "pct", "metrics"),
    ("net_margin_pct", "Net Margin", "pct", "metrics"),
    ("roe_pct", "Return on Equity", "pct", "metrics"),
    ("fcf_margin_pct", "Free Cash Flow Margin", "pct", "metrics"),
    ("pe_x", "Price / Earnings", "x", "metrics"),
    ("dividend_yield_pct", "Dividend Yield", "pct", "metrics"),
    ("buyback_yield_pct", "Buyback Yield", "pct", "metrics"),
    ("price_change_1y_pct", "1-Year Change", "pct+", "metrics"),
]
PEER_KEYS = [k for k, _, _, _ in PEER_COLS]
DEFAULT_PEER_COLS = ["market_cap", "revenue_growth_pct", "operating_margin_pct", "pe_x", "price_change_1y_pct"]

PERIODS = {"a": ([3, 5, 10, 20], 5), "q": ([4, 8, 12], 8)}
HOLDERS = ([5, 10, 20], 5)
RISKS = ([6, 12, 0], 6)        # 0 = every heading

CALC = {
    "revenue_growth": ("(revenue for the period ÷ revenue for the same period a year earlier − 1) × 100, "
                       "both as last filed"),
    "operating_margin": "operating income ÷ revenue × 100",
    "net_margin": "net income ÷ revenue × 100",
    "fcf": "operating cash flow − purchases of property, plant and equipment",
    "fcf_margin": "free cash flow ÷ revenue × 100",
    "quarters": ("a quarter is a 10-Q's three months; a fourth quarter is not filed on its own and is left out, "
                 "and cash flows filed year to date show only for a first quarter"),
    "sp500_1y": ("the same calculation as the share price's 1-year change, on SPY, the fund that "
                 "tracks the S&P 500: (latest close ÷ close one year earlier − 1) × 100"),
    "peers": ("the reader's own list; otherwise up to five companies with the same four-digit SIC industry "
              "code (three digits when fewer than three share it) whose market value is closest to its own, "
              "by ratio, and within 25 times it either way, each with revenue on file and a latest annual "
              "report within 18 months"),
    "peer_median": "the middle value of the peers for each column, leaving out a peer with no value",
    "topics": ("paragraphs of the latest annual report that contain every word the reader typed, most "
               "mentions first, word for word; a paragraph longer than 900 characters is cut after a sentence "
               "and the cut marked […]"),
}
CALC_NAMES = {
    "revenue_growth": "Revenue growth", "operating_margin": "Operating margin", "net_margin": "Net margin",
    "fcf": "Free cash flow", "fcf_margin": "Free cash flow margin", "quarters": "Quarters",
    "sp500_1y": "S&P 500, 1 year", "peers": "Peers", "peer_median": "Peer median", "topics": "Topics",
    "market_cap": "Market value", "pe_x": "Price / earnings", "price_change_1y_pct": "Share price, 1 year",
}
SOURCES = [
    "U.S. Securities and Exchange Commission, EDGAR (10-K, 10-Q, Forms 3, 4 and 5, Form 13F)",
    us_prices_api.PROVENANCE["credit"].replace("Source: ", "").rstrip("."),
]


# ---- formatting: every number as the page shows it ---------------------------------
def _num(v, dp):
    return "{:,.{}f}".format(abs(v), dp)


def money(v):
    if v is None:
        return DASH
    a = abs(v)
    if a >= 1e12:
        s = _num(a / 1e12, 2) + "tn"
    elif a >= 1e11:
        s = _num(a / 1e9, 0) + "bn"
    elif a >= 1e9:
        s = _num(a / 1e9, 1) + "bn"
    elif a >= 1e6:
        s = _num(a / 1e6, 1) + "mn"
    elif a >= 1e3:
        s = _num(a, 0)
    else:
        s = _num(a, 2)
    return (MINUS if v < 0 else "") + "$" + s


def pct(v, signed=False):
    if v is None:
        return DASH
    s = _num(v, 1) + "%"
    if round(v, 1) < 0:
        return MINUS + s
    return ("+" if signed and round(v, 1) > 0 else "") + s


def times(v):
    return DASH if v is None else _num(v, 1) + u"×"


def count(v, signed=False):
    if v is None:
        return DASH
    return (MINUS if v < 0 else ("+" if signed and v > 0 else "")) + _num(v, 0)


def eps(v):
    return DASH if v is None else (MINUS if v < 0 else "") + "$" + _num(v, 2)


def show(kind, v):
    return {"usd": money, "pct": pct, "pct+": lambda x: pct(x, True), "x": times, "count": count, "eps": eps}[kind](v)


EXCEL = {"usd": "#,##0", "pct": "0.0", "pct+": "0.0", "x": "0.0", "count": "#,##0", "eps": "0.00"}
UNIT = {"usd": "USD", "pct": "%", "pct+": "%", "x": u"\u00d7", "count": None, "eps": "USD per share"}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


def day(iso):
    if not iso:
        return DASH
    d = datetime.date.fromisoformat(str(iso)[:10])
    return "%d %s %d" % (d.day, _MONTHS[d.month - 1], d.year)


def month(iso):
    d = datetime.date.fromisoformat(str(iso)[:10])
    return "%s %d" % (_MONTHS[d.month - 1][:3], d.year)


# ---- the reader's choices ------------------------------------------------------------
def _list(raw, allowed, limit=None):
    out = []
    for x in (raw or "").split(","):
        x = x.strip()
        if x in allowed and x not in out:
            out.append(x)
    return out[:limit] if limit else out


def _pick(raw, allowed, default):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return v if v in allowed else default


def choices(s="", per="", n="", f="", pc="", p="", h="", r="", k=(), ask=()):
    """The reader's choices, checked, and the usual brief wherever one is left empty."""
    per = per if per in PERIODS else "a"
    sections = _list(s, SECTION_KEYS) or list(DEFAULT_SECTIONS)
    peers = []
    for t in (p or "").split(","):
        t = re.sub(r"[^A-Z0-9.\-]", "", t.strip().upper()).replace(".", "-")[:10]
        if t and t not in peers:
            peers.append(t)
    # the page joins several topics or questions with "|"; the API also takes the key repeated
    k = [x for t in (k or ()) for x in str(t).split("|")]
    ask = [x for t in (ask or ()) for x in str(t).split("|")]
    topics = []
    for t in k:
        t = " ".join(str(t).split())[:40]
        if len(t) >= 3 and t.lower() not in [x.lower() for x in topics]:
            topics.append(t)
    questions = []
    for q in ask:
        q = " ".join(str(q).split())[:300]
        if q and q not in questions:
            questions.append(q)
    return {"sections": sections, "per": per, "n": _pick(n, PERIODS[per][0], PERIODS[per][1]),
            "figures": _list(f, FIGURE_KEYS) or list(DEFAULT_FIGURES),
            "peer_cols": _list(pc, PEER_KEYS) or list(DEFAULT_PEER_COLS),
            "peers": peers[:MAX_PEERS], "holders": _pick(h, HOLDERS[0], HOLDERS[1]),
            "risks": _pick(r, RISKS[0], RISKS[1]), "topics": topics[:MAX_TOPICS],
            "questions": questions[:MAX_QUESTIONS]}


OPTIONS = {
    "sections": [{"key": k, "label": v} for k, v in SECTIONS],
    "figures": [{"key": k, "label": v} for k, v, _ in FIGURES],
    "peer_cols": [{"key": k, "label": v} for k, v, _, _ in PEER_COLS],
    "periods": {"a": PERIODS["a"][0], "q": PERIODS["q"][0]},
    "holders": HOLDERS[0], "risks": RISKS[0],
    "defaults": {"sections": DEFAULT_SECTIONS, "figures": DEFAULT_FIGURES, "peer_cols": DEFAULT_PEER_COLS,
                 "per": "a", "n": {"a": PERIODS["a"][1], "q": PERIODS["q"][1]}, "holders": HOLDERS[1],
                 "risks": RISKS[1]},
    "limits": {"peers": MAX_PEERS, "topics": MAX_TOPICS, "questions": MAX_QUESTIONS},
}


# ---- the parts ---------------------------------------------------------------------
def _part(missing, key, why, fn):
    """fn()'s answer, or None with the reason recorded: one missing part never sinks the brief."""
    try:
        got = fn()
    except HTTPException as e:
        missing[key] = why if e.status_code in (404, 503) else "could not be read just now"
        return None
    except Exception:  # noqa: BLE001 - a part that breaks is shown as missing, not as an error page
        missing[key] = "could not be read just now"
        return None
    if got is None:
        missing.setdefault(key, why)
    return got


def _business(text):
    """The opening of the Business section, cut after the last full sentence within the limit."""
    paras = [p for p in (text or {}).get("summary") or [] if p and p.strip()]
    if not paras:
        return None
    whole = " ".join(paras[0].split())
    if len(whole) <= BUSINESS_CHARS:
        return whole
    head = whole[:BUSINESS_CHARS]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head)]
    return head[:ends[-1]] if ends else head.rsplit(" ", 1)[0] + u"…"


def _ratio(a, b):
    return a / b * 100 if a is not None and b not in (None, 0) and b > 0 else None


def _history(tk, cik, per, n):
    """([(period_end, values)] newest first, source): every filing since 2009 for an S&P 500
    company, the SEC's quarterly data sets for any other filer."""
    from . import sec_deep_api
    want = n + (5 if per == "q" else 1)       # the extra periods are the year-earlier ones growth needs
    try:
        deep = sec_deep_api.indicators(tk, freq="annual" if per == "a" else "quarterly", basis="latest",
                                       as_of="", limit=want)
        rows = [(r["period_end"], r["values"]) for r in deep.get("panel") or []]
        if rows:
            return rows, "deep"
    except HTTPException:
        pass
    got = sec_api.company("cik:%d" % cik, form="10-K" if per == "a" else "10-Q", limit=want)
    rows = [(r["period"], r["values"]) for r in got.get("panel") or []
            if not r.get("amendment") and (per == "a" or r.get("span_quarters") == 1)]
    return (rows, "fsds") if rows else None


def _year_before(rows, i):
    """The row a year before row i: its period end 330 to 400 days earlier."""
    end = datetime.date.fromisoformat(str(rows[i][0])[:10])
    for e, v in rows[i + 1:]:
        gap = (end - datetime.date.fromisoformat(str(e)[:10])).days
        if 330 <= gap <= 400:
            return v
        if gap > 400:
            return None
    return None


def periods(rows, n):
    """Up to n periods, newest first, with growth, margins and free cash flow worked out."""
    out = []
    for i, (end, v) in enumerate(rows[:n]):
        prior = _year_before(rows, i) or {}
        rev, cfo, capex = v.get("revenue"), v.get("cf_operating"), v.get("capex")
        fcf = cfo - capex if cfo is not None and capex is not None else None
        prev = prior.get("revenue")
        row = dict((k, v.get(k)) for k in FIGURE_KEYS if k in v)
        row.update({
            "period_end": str(end)[:10],
            "revenue_growth": (rev / prev - 1) * 100 if rev is not None and prev and prev > 0 else None,
            "operating_margin": _ratio(v.get("operating_income"), rev),
            "net_margin": _ratio(v.get("net_income"), rev),
            "fcf": fcf, "fcf_margin": _ratio(fcf, rev),
        })
        out.append(row)
    return out or None


def _peer_row(r):
    return {"cik": r["cik"], "ticker": r.get("ticker"), "name": us_link.company_name(r["cik"], r["name"]),
            "logo": us_link.logo(r["cik"]),
            "values": dict((k, (r.get(where) or {}).get(k)) for k, _, _, where in PEER_COLS)}


def _peers(cur, cik, chosen, missing):
    """The company, then the reader's peers or the closest five in its industry, then their median."""
    rows = sec_api._decorate(cur, [dict(r) for r in sec_metrics.all_rows(cur)])
    me = next((r for r in rows if r["cik"] == cik), None)
    if not me:
        missing["peers"] = "no annual report on file for it"
        return None
    level, unknown = None, []
    if chosen:
        by_ticker = {}
        for r in rows:
            if r.get("ticker"):
                prev = by_ticker.get(r["ticker"])
                if prev is None or (r.get("period_end") or "") > (prev.get("period_end") or ""):
                    by_ticker[r["ticker"]] = r
        picked = []
        for t in chosen:
            r = by_ticker.get(t)
            if r is None:
                unknown.append(t)
            elif r["cik"] != cik:
                picked.append(r)
        if not picked:
            missing["peers"] = "none of the companies chosen (%s) has an annual report on file" % ", ".join(chosen)
            return None
    else:
        if me.get("sic") is None:
            missing["peers"] = "its annual report gives no industry code"
            return None
        mine = me["metrics"].get("market_cap")
        if not mine:
            missing["peers"] = "its own market value is not on file, so no company is closest to it"
            return None
        cutoff = (datetime.date.today() - datetime.timedelta(days=550)).isoformat()
        pool = [r for r in rows if r["cik"] != cik and r.get("ticker") and r.get("unit") == "USD"
                and (r.get("period_end") or "") >= cutoff and r.get("sic") is not None
                and (r["metrics"].get("market_cap") or 0) > 0 and (r["size"].get("revenue") or 0) > 0
                and 1.0 / SIZE_RATIO <= r["metrics"]["market_cap"] / mine <= SIZE_RATIO]
        code = str(int(me["sic"]))
        same, level = [r for r in pool if str(int(r["sic"])) == code], 4
        if len(same) < 3:
            wider = [r for r in pool if str(int(r["sic"]))[:3] == code[:3]]
            if len(wider) > len(same):
                same, level = wider, 3
        if not same:
            missing["peers"] = ("no other listed company in its industry is within %d times its market value"
                                % SIZE_RATIO)
            return None
        same.sort(key=lambda r: abs(math.log(r["metrics"]["market_cap"] / mine)))
        picked = sorted(same[:5], key=lambda r: -(r["metrics"].get("market_cap") or 0))
    out = [_peer_row(r) for r in picked]
    median = {}
    for k in PEER_KEYS:
        vals = [x["values"][k] for x in out if x["values"][k] is not None]
        median[k] = statistics.median(vals) if vals else None
    return {"auto": not chosen, "industry": me.get("industry"), "sic_digits": level, "company": _peer_row(me),
            "peers": out, "median": median if len(out) > 1 else None, "unknown": unknown}


def _holders(cur, cik, n):
    from . import sec_13f_api
    cusips = [c.get("cusip") for c in us_link.cusips_for(cur, cik) if c.get("cusip")]
    if not cusips:
        return None
    got = sec_13f_api.issuer(cusip=cusips[0], q="", limit=n)
    if not got.get("rows"):
        return None
    return {"holders": got.get("holders"), "window": got.get("window"),
            "rows": [{k: r.get(k) for k in ("manager_cik", "manager_name", "report_period", "value", "shares",
                                            "prior_shares", "shares_change", "new_position")} for r in got["rows"]]}


def _insiders(ticker):
    from . import sec_insiders_api
    got = sec_insiders_api.company(ticker, months=12, code="", include_derivative=False, limit=1)
    s = got.get("summary") or {}
    return {"since": got.get("since"), "last_filed_date": got.get("last_filed_date"),
            "buys": s.get("open_market_buys"), "sells": s.get("open_market_sells"),
            "page": "/us-stock.html?t=%s&view=owners" % ticker}


def _news(ticker):
    """The latest releases. A release filed as an 8-K exhibit has no headline of its own (its
    `title` is the opening of the document), so it is named for what it is, and left out on a
    day the company's own feed carries its release."""
    from . import us_press_api
    got = us_press_api.releases(ticker, limit=NEWS * 3)
    rel = got.get("releases") or []
    fed = {r.get("published") for r in rel if "8-K" not in (r.get("source") or "")}
    rows = []
    for r in rel:
        filed = "8-K" in (r.get("source") or "")
        if filed and r.get("published") in fed:
            continue
        rows.append({"title": "Press release filed with the SEC (8-K)" if filed else r.get("title"),
                     "url": r.get("url"), "published": r.get("published")})
    return rows[:NEWS] or None


def _topics(cik, topics, shown=""):
    """Each topic's paragraphs; a paragraph already quoted for an earlier topic, or already
    printed as the Business opening (`shown`), is not repeated."""
    from . import sec_text_api
    c = sec_text_api._require()
    f = sec_text_api.latest(c, cik)
    if not f:
        return None
    out, seen = [], {}
    lead = " ".join((shown or "").split())[:200]
    for t in topics:
        hits = sec_text_api.mentions(c, f["adsh"], t, limit=QUOTES_PER_TOPIC + 3)
        hits = [(k, p) for k, p in hits if not (lead and " ".join(p.split()).startswith(lead))]
        fresh = [(k, p) for k, p in hits if p not in seen][:QUOTES_PER_TOPIC]
        also = sorted({seen[p] for k, p in hits if p in seen})
        for _, p in fresh:
            seen[p] = t
        out.append({"topic": t, "also": also, "found": len(hits),
                    "passages": [{"section": sec_text_api.SECTION_NAMES.get(k, k), "text": p} for k, p in fresh]})
    return out


# the reader's own questions: answered once and kept an hour, so a download does not ask again
_ASKED = {}
_ASKED_LOCK = threading.Lock()
ASK_KEEP_SECONDS = 3600


def _answer(cur, q):
    from . import stock_recipes, us_ask_api
    now = time.time()
    with _ASKED_LOCK:
        hit = _ASKED.get(q)
        if hit and now - hit[0] < ASK_KEEP_SECONDS:
            return hit[1]
    try:
        out = us_ask_api._noted("/api/v1/us/research", q, lambda: stock_recipes.answer(cur, q, None, ""))
        got = {"question": q, "headline": out.get("headline"), "answer": out.get("answer"),
               "table": out.get("table"),
               "sources": [s.get("credit") for s in out.get("sources") or [] if s.get("credit")], "error": None}
    except HTTPException as e:
        got = {"question": q, "headline": None, "answer": None, "table": None, "sources": [],
               "error": e.detail if isinstance(e.detail, str) else "That question could not be answered from the data."}
    except Exception:  # noqa: BLE001
        got = {"question": q, "headline": None, "answer": None, "table": None, "sources": [],
               "error": "That question could not be answered just now. Try again in a minute."}
    with _ASKED_LOCK:
        if len(_ASKED) > 500:
            _ASKED.clear()
        _ASKED[q] = (now, got)
    return got


def _market(latest):
    """Price, market value and the 1-year move beside the S&P 500's over the same closes."""
    if not latest or not (latest.get("price") or {}).get("close"):
        return None
    m, px = latest["metrics"], latest["price"]
    spy = None
    try:
        s = us_prices_api.latest_closes(sec_api._require()).get(us_benchmarks.BENCHMARKS["sp500"]["etf"])
        if s and s[2]:
            spy = (s[1] / s[2] - 1) * 100
    except Exception:  # noqa: BLE001
        spy = None
    return {"close": px.get("close"), "date": px.get("date"), "market_cap": m.get("market_cap"),
            "price_change_1y_pct": m.get("price_change_1y_pct"), "sp500_1y_pct": spy,
            "pe_x": m.get("pe_x"), "dividend_yield_pct": m.get("dividend_yield_pct"),
            "buyback_yield_pct": m.get("buyback_yield_pct")}


# ---- the brief ------------------------------------------------------------------------
def build(ticker, ch, signed_in, today=None):
    from . import sec_text_api, us_company_api, us_preview_api
    t = (ticker or "").strip().upper().replace(".", "-")[:12]
    if not t:
        raise HTTPException(404, "name a company by its ticker, e.g. /api/v1/us/research/AAPL")
    co = us_company_api.company(t)["company"]          # an unknown company is the one error
    cik, tk = co["cik"], co.get("ticker") or t
    if not co.get("ticker"):
        raise HTTPException(404, "%s is not listed in the US, so it has no share price to research." % co["name"])
    cur = sec_api._require()
    missing, want = {}, set(ch["sections"])
    text = _part(missing, "business", "no annual report text on file", lambda: sec_text_api.company(tk))
    latest = _part(missing, "latest", "no annual report on file", lambda: sec_api.metrics(tk))
    b = {"company": {"cik": cik, "ticker": tk, "name": co["name"], "logo": co.get("logo"),
                     "exchange": co.get("exchange"), "industry": co.get("industry"),
                     "sp500_weight_pct": co.get("sp500_weight_pct"), "page": "/us-company.html?t=" + tk},
         "annual_report": (text or {}).get("filing"), "market": _market(latest),
         "latest": ({"fy": latest.get("fy"), "period_end": latest.get("period_end"), "form": latest.get("form"),
                     "filed": latest.get("filed"), "source_url": latest.get("source_url"),
                     "size": latest.get("size"), "per_share": latest.get("per_share"),
                     "metrics": latest.get("metrics")} if latest else None),
         "results": _part(missing, "results", "no results days on file", lambda: us_preview_api.results_next(cur, cik))}
    if "business" in want:
        b["business"] = _business(text) if text else None
        if b["business"] is None:
            missing.setdefault("business", "its annual report has no Business section on file")
    if "years" in want:
        none = "no %s figures on file" % ("annual" if ch["per"] == "a" else "quarterly")
        hist = _part(missing, "years", none, lambda: _history(tk, cik, ch["per"], ch["n"]))
        b["years"] = periods(hist[0], ch["n"]) if hist else None
        b["years_source"] = hist[1] if hist else None
        if b["years"] is None:
            missing.setdefault("years", none)
    if "peers" in want:
        b["peers"] = _part(missing, "peers", "no peers on file", lambda: _peers(cur, cik, ch["peers"], missing))
    if "holders" in want:
        b["holders"] = _part(missing, "holders", "no 13F filer reports holding it",
                             lambda: _holders(cur, cik, ch["holders"]))
    if "insiders" in want:
        b["insiders"] = _part(missing, "insiders", "no insider filings on file", lambda: _insiders(tk))
    if "risks" in want:
        rs = (text or {}).get("risks") or []
        b["risks_total"] = len(rs)
        b["risks"] = [{"n": r["n"], "category": r.get("category"), "headline": r["headline"]}
                      for r in (rs[:ch["risks"]] if ch["risks"] else rs)] or None
        if not b["risks"]:
            missing["risks"] = ("the annual report's risk factors could not be read as headings" if text
                                else "no annual report text on file")
    if "topics" in want and ch["topics"]:
        b["topics"] = _part(missing, "topics", "no annual report text on file", lambda: _topics(cik, ch["topics"], b.get("business") or ""))
    if "questions" in want and ch["questions"]:
        b["questions"] = [_answer(cur, q) for q in ch["questions"]] if signed_in else None
        if not signed_in:
            missing["questions"] = "sign in with a free account to add your own questions"
    if "news" in want:
        b["news"] = _part(missing, "news", "press releases are kept for S&P 500 companies only", lambda: _news(tk))
    b["missing"] = missing
    b["doc"] = document(b, ch, today=today)
    return b


# ---- the document ------------------------------------------------------------------
def _headline(b):
    lt, mk = b["latest"], b["market"]
    parts = []
    if lt and (lt["size"] or {}).get("revenue") is not None:
        g, rev = lt["metrics"].get("revenue_growth_pct"), lt["size"]["revenue"]
        parts.append(("Revenue was %s" % money(rev) if g is None else
                      "Revenue %s %s to %s" % ("grew" if g >= 0 else "fell", pct(abs(g)), money(rev)))
                     + " in the year to " + day(lt["period_end"]))
    if mk and mk.get("price_change_1y_pct") is not None:
        c = mk["price_change_1y_pct"]
        parts.append("the shares are %s %s in a year%s" % (
            "up" if c >= 0 else "down", pct(abs(c)),
            "" if mk.get("sp500_1y_pct") is None else ", against %s for the S&P 500" % pct(mk["sp500_1y_pct"], True)))
    if not parts:
        return None
    s = "; ".join(parts) + "."
    return s[0].upper() + s[1:]


def _results_line(b, today):
    r = b.get("results")
    if not r:
        return None
    if r.get("last_results") and (today - datetime.date.fromisoformat(r["last_results"])).days <= 14:
        return "Reported results on %s." % day(r["last_results"])
    if r.get("expected_date"):
        if r.get("overdue"):
            return "Results were expected around %s and are not on file yet." % day(r["expected_date"])
        return ("Next results expected around %s, an estimate: a year after the same quarter's."
                % day(r["expected_date"]))
    return None


def _words(n):
    return ["no", "one", "two", "three", "four", "five", "six", "seven", "eight"][n] if 0 <= n <= 8 else str(n)


QUOTE_CHARS = 900


def _quote(text):
    """A paragraph word for word, cut after a sentence when it is long, the cut marked."""
    if len(text) <= QUOTE_CHARS:
        return text
    head = text[:QUOTE_CHARS]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head)]
    return (head[:ends[-1]] if ends else head.rsplit(" ", 1)[0]) + u" [\u2026]"


DISCLAIMER = ("This brief is compiled by Plover Analytics from companies' filings with the U.S. Securities and "
              "Exchange Commission and from daily market prices. Figures are as filed unless named in How Each "
              "Figure Is Worked Out. It is provided for information only: it is not investment advice, an offer or "
              "a recommendation to buy or sell any security, and it does not take account of any reader's "
              "circumstances. Sections headed Your Notes and Your Questions were written or chosen by the reader.")


def _shares_short(v):
    if v is None:
        return DASH
    return _num(v / 1e9, 2) + "bn" if abs(v) >= 1e9 else _num(v / 1e6, 1) + "mn" if abs(v) >= 1e6 else _num(v, 0)


def _peer_median(b, key):
    p = b.get("peers")
    return (p.get("median") or {}).get(key) if p else None


def _points(b, ch, today):
    """Key points: what the figures below say, in a sentence each. Facts only, each one a
    figure on the page; nothing is judged good or bad."""
    co, lt, mk = b["company"], b["latest"], b["market"]
    lm, ls = (lt or {}).get("metrics") or {}, (lt or {}).get("size") or {}
    out = []
    ys = b.get("years") if ch["per"] == "a" else None
    grown = [y for y in (ys or []) if y.get("revenue_growth") is not None]
    if len(grown) >= 3:
        up = sum(1 for y in grown if y["revenue_growth"] > 0)
        out.append("Revenue rose in %s years shown%s." % (
            "each of the last %s" % _words(len(grown)) if up == len(grown) else
            "%s of the last %s" % (_words(up), _words(len(grown))),
            ", from %s to %s" % (money(ys[len(grown) - 1]["revenue"]), money(ys[0]["revenue"])) if up else ""))
    if lt and lm.get("operating_margin_pct") is not None:
        med = _peer_median(b, "operating_margin_pct")
        out.append("Operating margin of %s in the year to %s%s." % (
            pct(lm["operating_margin_pct"]), month(lt["period_end"]),
            ", against a median of %s for the peers shown" % pct(med) if med is not None else ""))
    if mk and mk.get("pe_x") is not None:
        med = _peer_median(b, "pe_x")
        out.append("The shares trade at %s times earnings%s." % (
            _num(mk["pe_x"], 1), ", against a peer median of %s times" % _num(med, 1) if med is not None else ""))
    if lt and lm.get("fcf") is not None:
        bb = ls.get("buybacks")
        out.append("Free cash flow of %s, %s of revenue%s." % (
            money(lm["fcf"]), pct(lm.get("fcf_margin_pct")),
            "; %s spent on buying back shares" % money(bb) if bb else ""))
    ins = b.get("insiders")
    if ins:
        buys, sells = ins.get("buys") or {}, ins.get("sells") or {}
        if buys.get("trades") or sells.get("trades"):
            out.append("Over the last 12 months insiders bought %s and sold %s on the market." % (
                money(buys.get("value")) if buys.get("trades") else "nothing",
                "%s (%s sales by %s insiders)" % (money(sells["value"]), count(sells["trades"]), count(sells["insiders"]))
                if sells.get("trades") else "nothing"))
    h = b.get("holders")
    if h and h.get("rows"):
        top = h["rows"][0]
        out.append("%s is the largest fund holder, with %s shares at %s." % (
            top["manager_name"], _shares_short(top["shares"]), day(top["report_period"])))
    rl = _results_line(b, today)
    if rl:
        out.append(rl)
    return out[:7]


def _keydata(b, ch):
    """The figures a reader looks up first, label and value."""
    co, lt, mk = b["company"], b["latest"], b["market"]
    lm, ls = (lt or {}).get("metrics") or {}, (lt or {}).get("size") or {}
    rows = []
    if mk:
        rows += [["Share price, %s" % _short_day(mk["date"]), "$" + _num(mk["close"], 2)],
                 ["Market value", money(mk["market_cap"])]]
        if co.get("sp500_weight_pct"):
            rows.append(["Weight in the S&P 500", _num(co["sp500_weight_pct"], 2) + "%"])
        rows += [["Share price, 1 year", pct(mk["price_change_1y_pct"], True)],
                 ["S&P 500, 1 year", pct(mk["sp500_1y_pct"], True)],
                 ["Price / earnings", times(mk["pe_x"])]]
        if _peer_median(b, "pe_x") is not None:
            rows.append(["Peer median P/E", times(_peer_median(b, "pe_x"))])
        if mk.get("dividend_yield_pct"):
            rows.append(["Dividend yield", pct(mk["dividend_yield_pct"])])
    if lt:
        rows += [["Revenue, year to %s" % month(lt["period_end"]), money(ls.get("revenue"))],
                 ["Revenue growth", pct(lm.get("revenue_growth_pct"), True)],
                 ["Operating margin", pct(lm.get("operating_margin_pct"))],
                 ["Free cash flow", money(lm.get("fcf"))]]
    r = b.get("results") or {}
    if r.get("expected_date") and not r.get("overdue"):
        rows.append(["Next results (estimate)", _short_day(r["expected_date"])])
    return rows


def _short_day(iso):
    d = datetime.date.fromisoformat(str(iso)[:10])
    return "%d %s %d" % (d.day, _MONTHS[d.month - 1][:3], d.year)


def _gap(b, key):
    why = b["missing"].get(key)
    return {"t": "p", "style": "note", "text": "Not on file: %s." % why if why else "Not on file."}


def _note(text):
    return {"t": "p", "style": "note", "text": text}


def document(b, ch, today=None):
    """The brief as one plain document: what the page draws and every download is made from."""
    today = today or datetime.date.today()
    co, lt, mk = b["company"], b["latest"], b["market"]
    tk = co["ticker"]
    lm = (lt or {}).get("metrics") or {}
    meta = [" · ".join(x for x in (co.get("exchange"), co.get("industry")) if x),
            "Research brief prepared on %s from SEC filings%s." % (
                day(today.isoformat()), " and daily closes to %s" % day(mk["date"]) if mk else "")]
    blocks = [{"t": "points", "items": _points(b, ch, today)}, {"t": "keydata", "items": _keydata(b, ch)}]
    used_calc = ["sp500_1y"] if mk else []
    kinds = dict((k, kd) for k, _, kd in FIGURES)
    labels = dict((k, lb) for k, lb, _ in FIGURES)

    for key in ch["sections"]:
        if key == "business":
            blocks.append({"t": "h", "text": "Business", "id": key})
            if b.get("business"):
                blocks.append({"t": "p", "style": "body", "text": b["business"]})
                ar = b.get("annual_report")
                if ar:
                    blocks.append({"t": "links", "style": "note", "items": [[None, (
                        "The opening of the Business section of its %s for the year to %s, in the company's own "
                        "words. Read the annual report." % (ar["form"], day(ar["period_end"]))), ar.get("url")]]})
            else:
                blocks.append(_gap(b, key))
        elif key == "years":
            q = ch["per"] == "q"
            blocks.append({"t": "h", "text": "Results by Quarter" if q else "Results by Year", "id": key})
            ys = b.get("years")
            if ys:
                cols = [["Quarter Ended" if q else "Year Ended", "l"]] + [[labels[x], "r"] for x in ch["figures"]]
                rows = [[month(y["period_end"])] + [show(kinds[x], y.get(x)) for x in ch["figures"]] for y in ys]
                values = [[y["period_end"]] + [y.get(x) for x in ch["figures"]] for y in ys]
                note = []
                if b.get("years_source") == "fsds":
                    note.append("Figures as each filing reported them, from the SEC's quarterly data sets; every "
                                "filing since 2009 is kept for S&P 500 companies only.")
                if q:
                    note.append("A fourth quarter is not filed on its own and is left out; cash flows filed year "
                                "to date show only for a first quarter.")
                    used_calc.append("quarters")
                src = ("10-Q" if q else "10-K") + (" filings" if b.get("years_source") == "deep" else " filings, SEC quarterly data sets")
                blocks.append({"t": "table", "name": "Results by Quarter" if q else "Results by Year", "cols": cols,
                               "title": "%s results by %s, last %d %s, as filed" % (
                                   co["name"], "quarter" if q else "fiscal year", len(ys),
                                   ("quarter" if q else "year") + ("" if len(ys) == 1 else "s")),
                               "source": "SEC EDGAR (%s); growth, margins and free cash flow worked out by Plover." % src,
                               "rows": rows, "values": values,
                               "formats": [None] + [EXCEL[kinds[x]] for x in ch["figures"]],
                               "units": [None] + [UNIT[kinds[x]] for x in ch["figures"]],
                               "note": " ".join(note) or None})
                used_calc += [x for x in ch["figures"] if x in CALC]
            else:
                blocks.append(_gap(b, key))
        elif key == "peers":
            blocks.append({"t": "h", "text": "Against Its Peers", "id": key})
            p = b.get("peers")
            if p:
                spec = sorted([c for c in PEER_COLS if c[0] in ch["peer_cols"]], key=lambda c: ch["peer_cols"].index(c[0]))
                n = len(p["peers"])
                if p["auto"]:
                    intro = "The %s in %s%s closest to %s in market value." % (
                        "company" if n == 1 else "%s companies" % _words(n), (p.get("industry") or "its industry").lower(),
                        " and related industries" if p.get("sic_digits") == 3 else "", tk)
                else:
                    intro = "The companies you chose." + (
                        " Not found: %s." % ", ".join(p["unknown"]) if p.get("unknown") else "")
                blocks.append(_note(intro))
                cols = [["Company", "l"]] + [[c[1], "r"] for c in spec]
                everyone = [p["company"]] + p["peers"]
                rows = [["%s (%s)" % (x["name"], x["ticker"])] + [show(c[2], x["values"][c[0]]) for c in spec]
                        for x in everyone]
                values = [[r[0]] + [x["values"][c[0]] for c in spec] for r, x in zip(rows, everyone)]
                tickers, logos, muted = [None] + [x["ticker"] for x in p["peers"]], [x["logo"] for x in everyone], []
                if p.get("median"):
                    rows.append(["Median of the %s Peers" % _words(n).title()] + [show(c[2], p["median"][c[0]]) for c in spec])
                    values.append([rows[-1][0]] + [p["median"][c[0]] for c in spec])
                    tickers.append(None)
                    logos.append(None)
                    muted.append(len(rows) - 1)
                    used_calc.append("peer_median")
                blocks.append({"t": "table", "name": "Peers", "cols": cols, "rows": rows, "values": values,
                               "title": "%s against %s" % (co["name"], "its peers" if p["auto"] else "the companies you chose"),
                               "source": "SEC EDGAR (latest annual reports); daily closes from Databento; Plover Screener figures.",
                               "formats": [None] + [EXCEL[c[2]] for c in spec], "strong": [0],
                               "units": [None] + [UNIT[c[2]] for c in spec],
                               "tickers": tickers, "logos": logos, "muted": muted})
                used_calc += ["peers"] + [c[0] for c in spec if c[0] in ("market_cap", "pe_x", "price_change_1y_pct")]
            else:
                blocks.append(_gap(b, key))
        elif key == "holders":
            blocks.append({"t": "h", "text": "Largest Fund Holders", "id": key})
            h = b.get("holders")
            if h:
                blocks.append(_note("The largest %d of %s fund managers reporting %s on Form 13F for the quarter "
                                    "to %s." % (len(h["rows"]), count(h["holders"]), tk, day(h["rows"][0]["report_period"]))))
                rows = [[x["manager_name"], count(x["shares"]), money(x["value"]),
                         "New" if x["new_position"] else count(x["shares_change"], True)] for x in h["rows"]]
                values = [[x["manager_name"], x["shares"], x["value"], x["shares_change"]] for x in h["rows"]]
                blocks.append({"t": "table", "name": "Fund Holders",
                               "title": "Largest fund holders of %s, quarter to %s" % (tk, day(h["rows"][0]["report_period"])),
                               "source": "SEC EDGAR (Form 13F).",
                               "cols": [["Manager", "l"], ["Shares", "r"], ["Value", "r"], ["Change in Shares", "r"]],
                               "rows": rows, "values": values, "formats": [None, "#,##0", "#,##0", "#,##0"],
                               "units": [None, None, "USD", None],
                               "note": "Change in shares is against the manager's filing for the quarter before."})
            else:
                blocks.append(_gap(b, key))
        elif key == "insiders":
            blocks.append({"t": "h", "text": "Insider Trades, Last 12 Months", "id": key})
            ins = b.get("insiders")
            if ins:
                def side(s, word):
                    if not s or not s.get("trades"):
                        return ["None", ""]
                    return [money(s["value"]), "%s %s%s by %s insider%s" % (
                        count(s["trades"]), word, "" if s["trades"] == 1 else "s", count(s["insiders"]),
                        "" if s["insiders"] == 1 else "s")]
                blocks.append({"t": "stats", "items": [["Bought on the Market"] + side(ins["buys"], "purchase"),
                                                       ["Sold"] + side(ins["sells"], "sale")]})
                blocks.append(_note("Since %s, filings to %s. Awards, option exercises and shares withheld for tax "
                                    "are left out." % (day(ins["since"]), day(ins["last_filed_date"]))))
            else:
                blocks.append(_gap(b, key))
        elif key == "risks":
            blocks.append({"t": "h", "text": "Main Risks", "id": key})
            if b.get("risks"):
                blocks.append({"t": "list", "ordered": True, "items": [r["headline"] for r in b["risks"]]})
                shown, total = len(b["risks"]), b.get("risks_total") or len(b["risks"])
                blocks.append(_note(("All %d risk factors" % total if shown >= total else
                                     "The first %d of %d risk factors" % (shown, total))
                                    + ", in the order the annual report gives them."))
            else:
                blocks.append(_gap(b, key))
        elif key == "topics" and ch["topics"]:
            blocks.append({"t": "h", "text": "Your Topics in the Annual Report", "id": key})
            tp = b.get("topics")
            if tp:
                ar = b.get("annual_report") or {}
                for x in tp:
                    blocks.append({"t": "p", "style": "sub", "text": x["topic"]})
                    if not x["found"]:
                        blocks.append(_note("No paragraph of its %s mentions %s." % (ar.get("form") or "annual report",
                                                                                     x["topic"])))
                    elif x.get("also"):
                        blocks.append(_note("%s paragraph%s that mention%s it %s quoted above, under %s." % (
                            "A" if x["passages"] else "The", "" if x["passages"] else "s", "s" if x["passages"] else "",
                            "is also" if x["passages"] else "are", " and ".join(u"\u201c%s\u201d" % a for a in x["also"]))))
                    for psg in x["passages"]:
                        blocks.append({"t": "quote", "text": _quote(psg["text"]), "cite": "%s, %s for the year to %s" % (
                            psg["section"], ar.get("form") or "10-K", day(ar.get("period_end")))})
                used_calc.append("topics")
            else:
                blocks.append(_gap(b, key))
        elif key == "questions" and ch["questions"]:
            blocks.append({"t": "h", "text": "Your Questions", "id": key})
            qs = b.get("questions")
            if qs is None:
                blocks.append({"t": "p", "style": "note", "signin": True,
                               "text": "Sign in with a free account to have your own questions answered in the brief."})
            for a in qs or []:
                blocks.append({"t": "p", "style": "sub", "text": a["question"]})
                if a["error"]:
                    blocks.append(_note(a["error"]))
                    continue
                for part in (a.get("headline"), a.get("answer")):
                    if part:
                        blocks.append({"t": "p", "style": "body", "text": part})
                tbl = _answer_table(a.get("table"))
                if tbl:
                    blocks.append(tbl)
                if a.get("sources"):
                    blocks.append(_note("Sources: " + "; ".join(
                        s.replace("Source: ", "").rstrip(".") for s in a["sources"]) + "."))
        elif key == "news":
            blocks.append({"t": "h", "text": "Latest Announcements", "id": key})
            if b.get("news"):
                blocks.append({"t": "links", "items": [[day(x["published"]), x["title"], x["url"]] for x in b["news"]]})
            else:
                blocks.append(_gap(b, key))
        elif key == "notes":
            blocks.append({"t": "notes", "id": key})     # the reader's own words: the page and a download fill it

    calc = dict(CALC, **{k: sec_metrics.PRICE_FORMULAS[k] for k in ("market_cap", "pe_x", "price_change_1y_pct")
                         if k in sec_metrics.PRICE_FORMULAS})
    sources = list(SOURCES) + (["annual report filed %s" % day(lt["filed"])] if lt else [])
    return {"title": "%s (%s)" % (co["name"], tk), "subtitle": _headline(b), "meta": [m for m in meta if m],
            "company": co["name"], "ticker": tk, "kicker": "Research Brief · United States", "date": day(today.isoformat()),
            "line": " · ".join(x for x in (tk, co.get("exchange"), co.get("industry")) if x),
            "disclaimer": DISCLAIMER,
            "blocks": blocks, "calc": [[CALC_NAMES.get(k, k), calc[k]] for k in dict.fromkeys(used_calc) if k in calc],
            "sources": sources,
            "footer": "Plover Analytics · %s · Research brief on %s, %s" % (SITE, tk, day(today.isoformat()))}


def _answer_table(t):
    """A question-box table as document rows, each cell shown as the box shows it."""
    if not t or not t.get("columns") or not t.get("rows"):
        return None
    cols, rows_in = t["columns"], t["rows"][:25]

    def kind(c):
        low = c.lower()
        if "change" in low and "share" in low and "%" not in low:
            return "count+"
        if "%" in low or "share of" in low or "margin" in low or "growth" in low or "yield" in low:
            return "pct"
        if "usd" in low or "value" in low or "revenue" in low or "income" in low or "$" in low:
            return "usd"
        if "shares" in low:
            return "count"
        return None

    def is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    kinds = [kind(c) for c in cols]
    numeric = [all(is_num(r[i]) or r[i] is None for r in rows_in) for i in range(len(cols))]
    rows, values = [], []
    for r in rows_in:
        cells = []
        for i, v in enumerate(r):
            k = kinds[i]
            if v is None or v == "":
                cells.append(DASH)
            elif is_num(v):
                cells.append(count(v, True) if k == "count+" else show(k, v) if k else
                             (_num(v, 0) if float(v).is_integer() else _num(v, 2)))
            else:
                cells.append(str(v))
        rows.append(cells)
        values.append([v if is_num(v) else None for v in r])
    fm = [("#,##0" if kinds[i] in ("usd", "count", "count+") else "0.0" if kinds[i] == "pct" else "0.00")
          if numeric[i] else None for i in range(len(cols))]
    return {"t": "table", "name": "Answer", "cols": [[c, "r" if numeric[i] else "l"] for i, c in enumerate(cols)],
            "rows": rows, "values": values if any(numeric) else None, "formats": fm}


def with_notes(doc, notes):
    """The document with the reader's notes in the place they chose, or without the empty slot."""
    out, text = [], (notes or "").strip()
    for blk in doc["blocks"]:
        if blk["t"] != "notes":
            out.append(blk)
        elif text:
            out.append({"t": "h", "text": "Your Notes", "id": "notes"})
            out.append(_note("Written by the reader, not by Plover."))
            out += [{"t": "p", "style": "body", "text": para} for para in
                    [x.strip() for x in text.split("\n") if x.strip()]]
    return dict(doc, blocks=out)


def finish(doc):
    """The document as a file: About This Brief at the end, with how each figure is worked
    out, the sources and the important information."""
    blocks = list(doc["blocks"])
    blocks.append({"t": "h", "text": "About This Brief", "about": True})
    if doc.get("calc"):
        blocks.append({"t": "p", "style": "sub", "text": "How each figure is worked out"})
        blocks.append({"t": "list", "ordered": False, "small": True, "items": ["%s = %s" % (k, v) for k, v in doc["calc"]]})
    blocks.append({"t": "p", "style": "sub", "text": "Sources"})
    blocks.append(_note("; ".join(doc["sources"]) + ". Figures exactly as filed unless named above as worked "
                        "out. A missing value is shown as " + DASH + ", never as zero."))
    blocks.append({"t": "p", "style": "sub", "text": "Important information"})
    blocks.append(_note(doc.get("disclaimer") or DISCLAIMER))
    return dict(doc, blocks=blocks)


# ---- routes ---------------------------------------------------------------------------
def _signed_in(request):
    from . import accounts
    try:
        return accounts.current_account(request) is not None
    except Exception:  # noqa: BLE001
        return False


@router.get("/options", openapi_extra={"x-example": "/api/v1/us/research/options"})
def options():
    """What a reader can choose for a brief, and the usual brief."""
    return OPTIONS


# ---- a person's usual choices: kept on their account, used wherever they sign in --------
# Only the choices that suit any company: sections and their order, the period, the
# figures, the peer columns, how many holders and risks. Peers, topics and questions
# are about one company and stay in that brief's address.
LAYOUT_KEYS = ("s", "per", "n", "f", "pc", "h", "r")
LAYOUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ia_research_layouts (
    account_id INTEGER PRIMARY KEY,
    spec TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""
_layout_made = set()


def _layout_conn():
    from .assistant import store
    c = store.conn()
    if str(store.DB_PATH) not in _layout_made:
        c.executescript(LAYOUT_SCHEMA)
        _layout_made.add(str(store.DB_PATH))
    return c


def _layout_clean(spec):
    ch = choices(spec.get("s", ""), spec.get("per", ""), str(spec.get("n", "")), spec.get("f", ""),
                 spec.get("pc", ""), "", str(spec.get("h", "")), str(spec.get("r", "")))
    return {"s": ",".join(ch["sections"]), "per": ch["per"], "n": str(ch["n"]), "f": ",".join(ch["figures"]),
            "pc": ",".join(ch["peer_cols"]), "h": str(ch["holders"]), "r": str(ch["risks"])}


def _me(request):
    from . import accounts
    account = accounts.current_account(request)
    if account is None:
        raise HTTPException(401, "Sign in to keep your usual choices.")
    return account


@router.get("/layout")
def layout(request: Request):
    """The signed-in person's usual choices for a brief, or null for the usual brief."""
    import json
    account = _me(request)
    row = _layout_conn().execute("SELECT spec FROM ia_research_layouts WHERE account_id = ?",
                                 (account["id"],)).fetchone()
    return JSONResponse({"layout": json.loads(row[0]) if row else None}, headers={"Cache-Control": "no-store"})


class LayoutIn(BaseModel):
    layout: Optional[dict] = None     # null puts the usual brief back


@router.post("/layout")
def save_layout(body: LayoutIn, request: Request):
    """Keep these choices as the person's usual brief, or forget them."""
    import json
    from .assistant import store
    account = _me(request)
    c = _layout_conn()                 # before the lock: store.conn() takes it too
    spec = _layout_clean(body.layout) if body.layout else None
    with store._lock:
        if spec is None:
            c.execute("DELETE FROM ia_research_layouts WHERE account_id = ?", (account["id"],))
        else:
            c.execute("INSERT OR REPLACE INTO ia_research_layouts (account_id, spec, updated_at) VALUES (?, ?, ?)",
                      (account["id"], json.dumps(spec), store.now()))
        c.commit()
    return JSONResponse({"layout": spec}, headers={"Cache-Control": "no-store"})


@router.get("/{ticker}", openapi_extra={"x-example": "/api/v1/us/research/AMD?s=business,years,peers&per=q&n=8"})
def research(ticker: str, request: Request,
             s: str = Query("", description="sections in order, comma-separated: " + ", ".join(SECTION_KEYS)),
             per: str = Query("", description="a (by year) or q (by quarter)"),
             n: str = Query("", description="how many years (3, 5, 10, 20) or quarters (4, 8, 12)"),
             f: str = Query("", description="figures by period, comma-separated: " + ", ".join(FIGURE_KEYS)),
             pc: str = Query("", description="peer table columns: " + ", ".join(PEER_KEYS)),
             p: str = Query("", description="your own peers, tickers comma-separated (up to 8); empty = closest five"),
             h: str = Query("", description="fund holders shown: 5, 10 or 20"),
             r: str = Query("", description="risk headings shown: 6, 12 or 0 for all"),
             k: Optional[List[str]] = Query(None, description="a topic to find in the annual report; repeat, up to 5"),
             ask: Optional[List[str]] = Query(None, description="your own question; repeat, up to 3 (signed in)")):
    """One listed US company, the brief the reader chooses: what it does, its
    figures by year or quarter, its peers, who holds it, insider trades, its
    risks, passages on chosen topics, answers to own questions, latest news."""
    ch = choices(s, per, n, f, pc, p, h, r, k or (), ask or ())
    signed = _signed_in(request) if ch["questions"] else False
    out = build(ticker, ch, signed)
    out["choices"] = ch
    out["signed_in"] = signed
    if ch["questions"]:
        # the answer depends on who is reading: never kept for the next reader
        return JSONResponse(content=jsonable_encoder(out), headers={"Cache-Control": "no-store"})
    return out


class DownloadRequest(BaseModel):
    format: str
    query: str = ""          # the brief's choices, as the page's address holds them after "?"
    notes: str = ""


@router.post("/{ticker}/download")
def download(ticker: str, body: DownloadRequest, request: Request):
    """The brief the reader built, as a PDF, Word or Excel file."""
    from urllib.parse import parse_qs
    fmt = (body.format or "").lower()
    if fmt not in doc_render.RENDER:
        raise HTTPException(400, "format must be pdf, docx or xlsx")
    if len(body.notes or "") > 20000:
        raise HTTPException(400, "Your notes are longer than 20,000 characters. Shorten them, then download again.")
    q = parse_qs(body.query or "")

    def one(key):
        return (q.get(key) or [""])[0]
    ch = choices(one("s"), one("per"), one("n"), one("f"), one("pc"), one("p"), one("h"), one("r"),
                 q.get("k") or (), q.get("ask") or ())
    b = build(ticker, ch, _signed_in(request) if ch["questions"] else False)
    data = doc_render.RENDER[fmt](finish(with_notes(b["doc"], body.notes)))
    name = "%s-research-%s.%s" % (b["company"]["ticker"], datetime.date.today().isoformat(), fmt)
    return Response(content=data, media_type=doc_render.MEDIA[fmt],
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})
