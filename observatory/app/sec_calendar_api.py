# -*- coding: utf-8 -*-
"""US reporting calendar — when each company last filed, and when to expect the next.

The SEC's data sets record the day every 10-K and 10-Q was filed. A company
files the same report at nearly the same point in the calendar every year
(the deadline is a fixed number of days after its period end), so last
year's filing date, moved forward a year and off a weekend, is a usable
expectation for this year's. That is all this does: it never guesses a date
it has no filing behind, and it says which filing each expectation rests on.

Two things it is not. It is not the earnings-release date: most companies
publish results by press release and 8-K days or weeks before the 10-Q, and
those are not in the statement data sets. And an expectation is not a
schedule: the company may file early, late, or on a different form.

Endpoints:
  /calendar    expected and actual filings in a date window, newest first
  /company/{cik or ticker}   one company's filing history with the lag from
                             period end, and its next expected filings
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import sec_api

router = APIRouter(prefix="/api/v1/us/earnings", tags=["US reporting calendar"])

PROVENANCE = {
    "trust": "official",
    "note": ("Filing dates exactly as the SEC recorded them (Financial Statement Data Sets). "
             "An `expected` row is a platform estimate — last year's filing of the same report "
             "moved forward one year and off a weekend — and names the filing it rests on; it is "
             "not an announced date, and it is not the earnings-release date."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR.",
}
CALC = {
    "expected_date": ("the filing date of the same company's same report (annual, or the same "
                      "fiscal quarter) one year earlier, plus one year; a Saturday or Sunday "
                      "moves to the following Monday, as SEC deadlines do"),
    "lag_days": "filing date − fiscal period end, in days, as recorded",
}
FORMS = sec_api.ANNUAL_FORMS + sec_api.QUARTERLY_FORMS

# The year-ago filing for each (company, report) in one pass, with the
# actual filing a year later where it exists (same company, same report
# family, a period end 330–400 days on).
EXPECT_SQL = """
    WITH base AS (
        SELECT s.cik, s.name, s.form, s.fp, s.period, s.filed, s.adsh,
               CASE WHEN s.form IN (%(annual)s) THEN 'annual' ELSE 'quarterly' END AS family
        FROM sec_filings s
        WHERE s.form IN (%(forms)s) AND s.status IN ('clean','partial')%(asof)s
          AND s.filed >= ? - INTERVAL 400 DAY AND s.filed <= ? - INTERVAL 330 DAY
    ),
    actual AS (
        SELECT b.adsh AS base_adsh, a.adsh, a.filed, a.period, a.form,
               row_number() OVER (PARTITION BY b.adsh ORDER BY a.filed) AS rn
        FROM base b
        JOIN sec_filings a ON a.cik = b.cik AND a.status IN ('clean','partial')
             AND a.form IN (%(forms)s)
             AND (CASE WHEN a.form IN (%(annual)s) THEN 'annual' ELSE 'quarterly' END) = b.family
             AND a.period > b.period + INTERVAL 330 DAY AND a.period <= b.period + INTERVAL 400 DAY
    )
    SELECT b.cik, b.name, b.form, b.fp, b.period AS base_period, b.filed AS base_filed, b.adsh AS base_adsh,
           b.family, x.adsh AS actual_adsh, x.filed AS actual_filed, x.period AS actual_period, x.form AS actual_form
    FROM base b LEFT JOIN actual x ON x.base_adsh = b.adsh AND x.rn = 1
"""


def _expected(filed):
    """Last year's filing date, one year on, off a weekend."""
    try:
        d = filed.replace(year=filed.year + 1)
    except ValueError:                       # 29 February
        d = filed + datetime.timedelta(days=365)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d


def _parse_date(value, name, default):
    v = (value or "").strip()
    if not v:
        return default
    try:
        return datetime.date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "%s must be YYYY-MM-DD" % name)


def _rows_for(cur, start, end):
    sql = EXPECT_SQL % {"annual": ",".join("?" * len(sec_api.ANNUAL_FORMS)),
                        "forms": ",".join("?" * len(FORMS)),
                        "asof": asof.clause("filed", "s")}
    params = (list(sec_api.ANNUAL_FORMS) + list(FORMS) + [start, end] + list(FORMS) + list(sec_api.ANNUAL_FORMS))
    return sec_api._rows(cur, sql, params)


def _calendar_rows(cur, start, end, meta):
    out = []
    for r in _rows_for(cur, start, end):
        exp = _expected(r["base_filed"])
        if not (start <= exp <= end):
            continue
        m = meta.get(r["cik"]) or {}
        status = "filed" if r["actual_filed"] else ("overdue" if exp < datetime.date.today() else "expected")
        out.append({
            "cik": r["cik"], "sec_code": "cik:%d" % r["cik"], "name": r["name"],
            "ticker": m.get("ticker"), "exchange": m.get("exchange"), "industry": m.get("industry"),
            "report": "annual report" if r["family"] == "annual" else "quarterly report",
            "form": r["actual_form"] or r["form"], "fiscal_period": r["fp"],
            "expected_date": exp.isoformat(), "status": status,
            "actual_filed": sec_api._d(r["actual_filed"]), "actual_period_end": sec_api._d(r["actual_period"]),
            "days_early": ((exp - r["actual_filed"]).days if r["actual_filed"] else None),
            "basis": {"filed": sec_api._d(r["base_filed"]), "period_end": sec_api._d(r["base_period"]),
                      "form": r["form"], "adsh": r["base_adsh"]},
            "source_url": "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (
                r["cik"], (r["actual_adsh"] or r["base_adsh"]).replace("-", "")),
        })
    return out


def _meta(cur, ciks=None):
    if not sec_api._has(cur, "sec_companies"):
        return {}
    rows = cur.execute("SELECT cik, ticker, exchange, industry FROM sec_companies").fetchall()
    return {r[0]: {"ticker": r[1], "exchange": r[2], "industry": r[3]} for r in rows}


@router.get("/calendar", openapi_extra={"x-example": "/api/v1/us/earnings/calendar?days=14"})
def calendar(start: str = Query("", description="YYYY-MM-DD; default today"),
             end: str = Query("", description="YYYY-MM-DD; default start + days"),
             days: int = Query(14, ge=1, le=120),
             exchange: str = Query("", description="Nasdaq | NYSE | OTC | CBOE | TXSE"),
             tickers: str = Query("", description="comma-separated tickers"),
             report: str = Query("", description="annual | quarterly; default both"),
             status: str = Query("", description="expected | filed | overdue; default all"),
             limit: int = Query(500, ge=1, le=5000)):
    """Expected and actual 10-K/10-Q filings in a window. Each expected date
    rests on the company's own filing a year earlier, which the row names."""
    cur = sec_api._require()
    today = datetime.date.today()
    s = _parse_date(start, "start", today)
    e = _parse_date(end, "end", s + datetime.timedelta(days=days))
    if e < s:
        raise HTTPException(400, "end is before start")
    if (e - s).days > 370:
        raise HTTPException(400, "window is at most a year")
    rep = (report or "").strip().lower()
    if rep and rep not in ("annual", "quarterly"):
        raise HTTPException(400, "report must be annual or quarterly")
    st = (status or "").strip().lower()
    if st and st not in ("expected", "filed", "overdue"):
        raise HTTPException(400, "status must be expected, filed or overdue")
    meta = _meta(cur)
    want = {t.strip().upper().replace(".", "-") for t in (tickers or "").split(",") if t.strip()}
    ex = (exchange or "").strip().lower()
    rows = []
    for r in _calendar_rows(cur, s, e, meta):
        if rep and not r["report"].startswith(rep):
            continue
        if st and r["status"] != st:
            continue
        if ex and (r["exchange"] or "").lower() != ex:
            continue
        if want and (r["ticker"] or "") not in want:
            continue
        rows.append(r)
    rows.sort(key=lambda r: (r["expected_date"], r["name"] or ""))
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("expected", "filed", "overdue")}
    return {"start": s.isoformat(), "end": e.isoformat(), "report": rep or None, "status": st or None,
            "exchange": ex or None, "tickers": sorted(want) or None,
            "count": len(rows), "by_status": counts, "rows": rows[:limit],
            "calc": CALC, "provenance": PROVENANCE}


@router.get("/company/{sec_code}", openapi_extra={"x-example": "/api/v1/us/earnings/company/AAPL"})
def company(sec_code: str):
    """One company's filing history (period end, filing date, lag) and its
    next expected filings, each resting on a filing a year earlier."""
    cur = sec_api._require()
    if not (sec_code or "").strip():
        # The composed company page asks every dataset about a securities code
        # it has normalised, sometimes to nothing: that is "not here", not a bad request.
        raise HTTPException(404, "no US filer under an empty code — this shelf is keyed by SEC CIK or ticker")
    cik = sec_api.resolve_company(cur, sec_code)
    hist = sec_api._rows(cur, """
        SELECT adsh, form, fp, period, filed, status
        FROM sec_filings s WHERE s.cik = ? AND s.form IN (%s) AND s.status IN ('clean','partial')%s
        ORDER BY period DESC, filed DESC""" % (",".join("?" * len(FORMS)), asof.clause("filed", "s")),
                              [cik] + list(FORMS))
    if not hist:
        raise HTTPException(404, "no 10-K or 10-Q loaded for CIK %d" % cik)
    for h in hist:
        h["lag_days"] = (h["filed"] - h["period"]).days if h["filed"] and h["period"] else None
        h["period"], h["filed"] = sec_api._d(h["period"]), sec_api._d(h["filed"])
        h["source_url"] = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, h["adsh"].replace("-", ""))
    today = datetime.date.today()
    meta = _meta(cur)
    upcoming = [r for r in _calendar_rows(cur, today - datetime.timedelta(days=30),
                                          today + datetime.timedelta(days=400), meta)
                if r["cik"] == cik and r["status"] != "filed"]
    upcoming.sort(key=lambda r: r["expected_date"])
    m = meta.get(cik) or {}
    return {"cik": cik, "sec_code": "cik:%d" % cik, "name": hist[0] and sec_api._filings(cur, cik, FORMS)[0]["name"],
            "ticker": m.get("ticker"), "exchange": m.get("exchange"), "industry": m.get("industry"),
            "filings": hist, "upcoming": upcoming, "calc": CALC, "provenance": PROVENANCE}
