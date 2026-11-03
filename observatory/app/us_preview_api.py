# -*- coding: utf-8 -*-
"""Earnings preview: one US company before its next results, from what Plover holds.

The front page's Earnings Preview task (web/assets/home-tasks.js) reads this.
Nothing new is collected: it puts together rows other datasets already serve.

  results days     us_events, kind 'results' with is_earnings: the day the company
                   filed its 8-K under Item 2.02 for a quarter (app/us_events_api.py)
  quarterly figures  the box's `quarters` table (app/us_tables.py): the last eight
                   quarters as last filed, a missing fourth quarter derived
  share prices     daily closes, split-adjusted (app/us_prices_api.py), and SPY for
                   the S&P 500

Two figures are worked out here, each with its formula in `calc`: the share
price move around each results day, and the expected date of the next results.
The expected date is an estimate, never an announced date: the results day of
the same quarter a year earlier, plus 364 days (the same weekday).

Endpoints:
  /upcoming        S&P 500 companies expected to report in the next N days
  /{ticker}        one company's preview
"""
import bisect
import datetime
import re
import statistics
import threading

from fastapi import APIRouter, HTTPException, Query

from . import sec_api, us_benchmarks, us_link, us_prices_api, us_tables

router = APIRouter(prefix="/api/v1/us/preview", tags=["US earnings preview"])

YEAR = datetime.timedelta(days=364)      # 52 weeks: the same weekday a year on
QUARTERS_SHOWN = 8
RESULTS_SHOWN = 8
_LOCK = threading.Lock()
_MEMO = {}

CALC = {
    "expected_date": ("the day the company announced results for the same quarter a year earlier "
                      "(its 8-K under Item 2.02), plus 364 days so it falls on the same weekday; an "
                      "estimate, not a date the company has announced"),
    "results_move": ("(close N trading days after the results day ÷ close on the last trading day "
                     "before it − 1) × 100, on split-adjusted closes, N = 1 (the day after), 5 (a week) "
                     "or 21 (a month); the window holds the results whether they came before the open "
                     "or after the close"),
    "sp500_move": "the same calculation on SPY, the fund that tracks the S&P 500, over the same days",
    "typical_move": "the median of the size of the moves shown, ignoring their direction",
    "since_results": ("(latest close ÷ close on the last trading day before the latest results − 1) × 100"),
    "operating_margin": "operating income ÷ revenue × 100, for the quarter",
    "revenue_growth": "(revenue this quarter ÷ revenue in the same quarter a year earlier − 1) × 100",
    "q4": "a fourth quarter no filing reports on its own is the full year less the first three quarters",
}
SOURCES = [
    {"name": "Results days", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (8-K, Item 2.02)."},
    {"name": "Quarterly results", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (10-Q and 10-K)."},
    {"name": "Daily prices", "credit": us_prices_api.PROVENANCE["credit"]},
]


def _require():
    cur = sec_api._cur()
    for t in ("us_events", "us_px_daily"):
        if not sec_api._has(cur, t):
            raise HTTPException(503, "Results days and prices are not loaded on this server yet")
    return cur


def _d(v):
    return v.isoformat() if v else None


def _quarter_of(detail):
    m = re.search(r"quarter to (\d{4}-\d{2}-\d{2})", detail or "")
    return datetime.date.fromisoformat(m.group(1)) if m else None


def _results(cur, ciks):
    """{cik: [(date, quarter_end, url)]} oldest first: the results days on file."""
    if not ciks:
        return {}
    rows = cur.execute(
        "SELECT cik, date, detail, source_url FROM us_events WHERE kind = 'results' AND is_earnings "
        "AND NOT coalesce(scheduled, false) AND cik IN (%s) ORDER BY cik, date" % ",".join("?" * len(ciks)),
        list(ciks)).fetchall()
    out = {}
    for cik, d, detail, url in rows:
        out.setdefault(int(cik), []).append((d, _quarter_of(detail), url))
    return out


def expected_next(days, today=None):
    """The next results day expected from a company's past ones: the first
    'a year after' date that falls after its latest results. None with too
    little history (fewer than four quarters on file)."""
    if len(days) < 4:
        return None
    today = today or datetime.date.today()
    last = days[-1][0]
    after = [(d + YEAR, d) for d, _, _ in days if d + YEAR > last + datetime.timedelta(days=45)]
    if not after:
        return None
    nxt, base = min(after)
    return {"date": _d(nxt), "based_on": _d(base), "overdue": nxt < today - datetime.timedelta(days=3)}


def results_next(cur, cik, today=None):
    """One company's next results day, estimated, with its latest on file: the
    date every page shows as "results expected" (app/sec_calendar_api.py carries
    it beside the 10-Q/10-K filing estimate). None with no results days on file."""
    if not sec_api._has(cur, "us_events"):
        return None
    days = _results(cur, [int(cik)]).get(int(cik), [])
    if not days:
        return None
    nxt = expected_next(days, today)
    return {"expected_date": nxt["date"] if nxt else None, "based_on": nxt["based_on"] if nxt else None,
            "overdue": bool(nxt and nxt["overdue"]), "last_results": _d(days[-1][0])}


def expected_results(cur, start, end, ciks=None, today=None):
    """[(expected date, cik, based on)] for every company whose estimated next
    results day falls between start and end, soonest first; and
    [(results date, cik)] for those whose latest results fell in that span."""
    if ciks is None:
        ciks = [r[0] for r in cur.execute(
            "SELECT DISTINCT cik FROM us_events WHERE kind = 'results' AND is_earnings AND date >= ?",
            [datetime.date.today() - datetime.timedelta(days=430)]).fetchall()]
    soon, done = [], []
    for cik, rows in _results(cur, ciks).items():
        last = rows[-1][0]
        if start <= last <= end:
            done.append((last, cik))
            continue
        nxt = expected_next(rows, today)
        if nxt and start <= datetime.date.fromisoformat(nxt["date"]) <= end:
            soon.append((nxt["date"], cik, nxt["based_on"]))
    return sorted(soon), sorted(done)


def _closes(cur, ticker, start):
    rows, _ = us_prices_api.clean_closes(cur, ticker, start, None)
    return [(d, a) for d, c, a in rows if a]


def _move(series, dates, day, after=1):
    """(before date, after date, % move) from the close before a results day to
    the close `after` trading days after it, or None when that close is not in yet."""
    i = bisect.bisect_left(dates, day)             # first close on or after the day
    j = bisect.bisect_right(dates, day) + after - 1   # the after-th close after the day
    if i == 0 or j >= len(series):
        return None
    (d0, c0), (d1, c1) = series[i - 1], series[j]
    return d0, d1, (c1 / c0 - 1) * 100


def _name(cur, cik):
    rec = us_link.company_by_cik(cur, cik) or {}
    sec_name = rec.get("sec_name")
    name = us_link.company_name(cik, sec_name, cur) if sec_name else None
    return rec, name or sec_name or "CIK %d" % cik


def _quarters(cur, ticker):
    c = us_tables.sandbox(cur).cursor()
    cols = ["quarter_end", "fiscal_year", "fiscal_quarter", "revenue_usd", "operating_income_usd",
            "net_income_usd", "eps_usd", "revenue_growth_yoy_pct", "q4_derived", "filed"]
    rows = [dict(zip(cols, r)) for r in c.execute(
        "SELECT %s FROM quarters WHERE ticker = ? ORDER BY quarter_end" % ", ".join(cols), [ticker]).fetchall()]
    out = []
    for r in rows[-QUARTERS_SHOWN:]:
        rev, op = r["revenue_usd"], r["operating_income_usd"]
        out.append({
            "quarter_end": _d(r["quarter_end"]), "fiscal": "%s %d" % (r["fiscal_quarter"], r["fiscal_year"]),
            "revenue": rev, "revenue_growth_pct": r["revenue_growth_yoy_pct"],
            "operating_margin_pct": (op / rev * 100) if op is not None and rev else None,
            "net_income": r["net_income_usd"], "eps": r["eps_usd"],
            "q4_derived": bool(r["q4_derived"]), "filed": _d(r["filed"]),
        })
    return out


@router.get("/upcoming", openapi_extra={"x-example": "/api/v1/us/preview/upcoming?days=14"})
def upcoming(days: int = Query(14, ge=1, le=60, description="how far ahead to look"),
             limit: int = Query(12, ge=1, le=100)):
    """S&P 500 companies expected to report results in the next `days` days,
    soonest first and, on one day, the largest first; and those that
    reported in the last seven days."""
    cur = _require()
    key = (sec_api.file_version(), datetime.date.today(), days)
    with _LOCK:
        hit = _MEMO.get(key)
    if hit is None:
        ciks, weights, as_of = us_benchmarks.members(cur, "sp500")
        if not ciks:
            raise HTTPException(503, "The S&P 500 member list is not loaded on this server yet")
        today = datetime.date.today()
        horizon = today + datetime.timedelta(days=days)
        tickers = us_tables._tickers(cur)
        soon, _ = expected_results(cur, today, horizon, ciks, today)
        _, recent = expected_results(cur, today - datetime.timedelta(days=7), today, ciks, today)
        soon = [x for x in soon if tickers.get(x[1])]
        recent = [x for x in recent if tickers.get(x[1])]

        def item(cik, extra):
            rec, name = _name(cur, cik)
            return dict({"cik": cik, "ticker": tickers.get(cik), "name": name, "logo": us_link.logo(cik),
                         "sp500_weight_pct": weights.get(cik)}, **extra)
        soon.sort(key=lambda x: (x[0], -(weights.get(x[1]) or 0)))
        recent.sort(key=lambda x: (-x[0].toordinal(), -(weights.get(x[1]) or 0)))
        hit = {"as_of": _d(today), "days": days, "members_as_of": _d(as_of),
               "expected": [item(c, {"expected_date": d, "based_on": b}) for d, c, b in soon],
               "reported": [item(c, {"results_date": _d(d)}) for d, c in recent]}
        with _LOCK:
            _MEMO.clear()
            _MEMO[key] = hit
    return {"as_of": hit["as_of"], "days": hit["days"], "members_as_of": hit["members_as_of"],
            "expected": hit["expected"][:limit], "expected_count": len(hit["expected"]),
            "reported": hit["reported"][:limit], "reported_count": len(hit["reported"]),
            "calc": {"expected_date": CALC["expected_date"]},
            "provenance": {"trust": "derived", "note": "Expected dates are estimates from past results days, not dates the companies have announced.",
                           "credit": SOURCES[0]["credit"]}}


@router.get("/{ticker}", openapi_extra={"x-example": "/api/v1/us/preview/AAPL?after=5"})
def preview(ticker: str,
            after: int = Query(1, ge=1, le=63, description="trading days after each results day the move runs to: 1, 5 (a week), 21 (a month)")):
    """One company before its next results: when they are expected, its last
    eight quarters, and how the share price moved around each of its last
    eight results days."""
    cur = _require()
    cik = sec_api.resolve_company(cur, ticker)
    rec, name = _name(cur, cik)
    tk = us_tables._tickers(cur).get(cik) or rec.get("ticker") or ticker.upper()
    days = _results(cur, [cik]).get(cik, [])
    today = datetime.date.today()
    shown = days[-RESULTS_SHOWN:]
    start = (shown[0][0] if shown else today - datetime.timedelta(days=60)) - datetime.timedelta(days=10)
    # a reaction window runs past the last results day: closes to today
    px = _closes(cur, tk, start)
    spy = _closes(cur, us_benchmarks.BENCHMARKS["sp500"]["etf"], start)
    px_d, spy_d = [d for d, _ in px], [d for d, _ in spy]
    quarters = _quarters(cur, tk)
    by_end = {q["quarter_end"]: q["fiscal"] for q in quarters}

    reactions = []
    for d, qend, url in reversed(shown):
        m, b = _move(px, px_d, d, after), _move(spy, spy_d, d, after)
        reactions.append({"date": _d(d), "quarter_end": _d(qend), "fiscal": by_end.get(_d(qend)), "url": url,
                          "from": _d(m[0]) if m else None, "to": _d(m[1]) if m else None,
                          "move_pct": m[2] if m else None, "sp500_pct": b[2] if b else None})
    sizes = [abs(r["move_pct"]) for r in reactions if r["move_pct"] is not None]

    since = None
    if days and px:
        last = days[-1][0]
        i = bisect.bisect_left(px_d, last)
        j = bisect.bisect_left(spy_d, last)
        if i > 0 and px[-1][0] > last:
            since = {"from": _d(px[i - 1][0]), "to": _d(px[-1][0]),
                     "move_pct": (px[-1][1] / px[i - 1][1] - 1) * 100,
                     "sp500_pct": (spy[-1][1] / spy[j - 1][1] - 1) * 100 if j > 0 and spy else None}

    return {
        "company": {"cik": cik, "ticker": tk, "name": name, "logo": us_link.logo(cik),
                    "page": "/us-company.html?t=%s" % tk},
        "next": expected_next(days, today),
        "last_results": {"date": _d(days[-1][0]), "quarter_end": _d(days[-1][1]), "url": days[-1][2]} if days else None,
        "since_results": since,
        "price": {"close": px[-1][1], "date": _d(px[-1][0])} if px else None,
        "quarters": quarters,
        "reactions": reactions,
        "after": after,
        "typical_move_pct": statistics.median(sizes) if len(sizes) >= 4 else None,
        "calc": CALC,
        "sources": SOURCES,
        "provenance": {"trust": "derived",
                       "note": ("Quarterly figures as last filed; results days as filed with the SEC; price moves and the "
                                "expected date worked out with the formulas in calc. The expected date is an estimate.")},
    }
