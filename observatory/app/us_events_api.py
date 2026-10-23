# -*- coding: utf-8 -*-
"""US dated events, served: FOMC statement days, CPI release days, federal
shutdowns and each company's results announcements (equity/us_events.py
loads them into data/sec.duckdb). The box's step route reads the same rows
as its `events` table (app/us_tables.py) to answer "how did X do after Y".

Trust contract: every row is a date a public body set — the Fed's calendar,
the BLS's release archive and schedule, EDGAR's filing index — named in
source and source_url; nothing here is estimated. Shutdown rows are a short
list kept in the loader against the article that collects them, and each
is marked verified or not against that article's text. A scheduled row is a
date on a published calendar that has not happened yet.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import sec_api

router = APIRouter(prefix="/api/v1/us/events", tags=["US events"])

STALE_AFTER_DAYS = 3        # the calendars are read nightly; three quiet days means the job is not running
KINDS = ("fomc", "cpi", "shutdown", "results")

PROVENANCE = {
    "trust": "official",
    "note": ("Each row is a day something happened, as the body that set the date publishes it: the Federal "
             "Reserve's FOMC calendar and historical pages (statement days, and scheduled meetings), the BLS "
             "archive and schedule of CPI news releases, the SEC's EDGAR filing index (the day a company filed "
             "an 8-K under Item 2.02, Results of Operations; is_earnings marks the first such filing from seven "
             "days after a fiscal quarter end, taken as that quarter's results), and the Wikipedia article "
             "collecting federal shutdowns (each start date checked against its text). Dates from 2018, where "
             "the daily price history begins."),
    "credit": "Sources: Federal Reserve Board; U.S. Bureau of Labor Statistics; U.S. Securities and Exchange Commission (EDGAR); Wikipedia.",
}


def _require():
    cur = sec_api._cur()
    if not sec_api._has(cur, "us_events"):
        raise HTTPException(503, "US events not loaded on this server yet")
    return cur


def _d(v):
    return sec_api._d(v)


def _date(s, what):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "%s must be a real date, YYYY-MM-DD" % what)


@router.get("", openapi_extra={"x-example": "/api/v1/us/events?kind=fomc&since=2025-01-01"})
@router.get("/", include_in_schema=False)
def events(kind: str = Query("", description="fomc, cpi, shutdown or results; blank for all"),
           ticker: str = Query("", description="a company's ticker (results only)"),
           since: str = Query("", description="YYYY-MM-DD, first event day"),
           until: str = Query("", description="YYYY-MM-DD, last event day"),
           earnings_only: int = Query(1, description="for results: 1 keeps the quarterly results filing only"),
           limit: int = Query(500, ge=1, le=5000)):
    """Event days, newest first, with where each date came from."""
    cur = _require()
    kind = (kind or "").strip().lower()
    if kind and kind not in KINDS:
        raise HTTPException(400, "kind must be one of %s" % ", ".join(KINDS))
    where, args = [], []
    if kind:
        where.append("kind = ?"); args.append(kind)
    t = (ticker or "").strip().upper()
    if t:
        where.append("ticker = ?"); args.append(t)
    lo, hi = _date(since, "since"), _date(until, "until")
    if lo:
        where.append("date >= ?"); args.append(lo)
    if hi:
        where.append("date <= ?"); args.append(hi)
    if earnings_only and (kind == "results" or t):
        where.append("(kind <> 'results' OR is_earnings)")
    sql = ("SELECT kind, date, end_date, ticker, label, detail, scheduled, is_earnings, source, source_url "
           "FROM us_events%s ORDER BY date DESC, kind, ticker LIMIT ?" % ((" WHERE " + " AND ".join(where)) if where else ""))
    rows = cur.execute(sql, args + [limit + 1]).fetchall()
    out = [{"kind": r[0], "date": _d(r[1]), "end_date": _d(r[2]), "ticker": r[3], "label": r[4], "detail": r[5],
            "scheduled": bool(r[6]), "is_earnings": r[7], "source": r[8], "source_url": r[9]} for r in rows[:limit]]
    return {"events": out, "count": len(out), "truncated": len(rows) > limit, "provenance": PROVENANCE}


@router.get("/summary", openapi_extra={"x-example": "/api/v1/us/events/summary"})
def summary():
    """What is loaded: rows and date range by kind, companies read, and the last pulls."""
    cur = _require()
    kinds = cur.execute("""
        SELECT kind, count(*), min(date), max(date) FILTER (WHERE NOT scheduled), max(date) FILTER (WHERE scheduled),
               count(DISTINCT cik)
        FROM us_events GROUP BY kind ORDER BY kind""").fetchall()
    pulls = cur.execute("SELECT kind, requested_at, status, rows, pages, detail FROM us_event_pulls ORDER BY pull_id DESC LIMIT 12").fetchall() \
        if sec_api._has(cur, "us_event_pulls") else []
    return {"kinds": [{"kind": k, "rows": n, "first_date": _d(lo), "last_date": _d(hi), "last_scheduled": _d(sc),
                       "companies": c or None} for k, n, lo, hi, sc, c in kinds],
            "pulls": [{"kind": k, "requested_at": at.isoformat() + "Z" if at else None, "status": st, "rows": n, "pages": p, "detail": d}
                      for k, at, st, n, p, d in pulls],
            "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    try:
        cur = sec_api._cur()
    except HTTPException:
        return []
    if not sec_api._has(cur, "us_event_pulls"):
        return []
    last_ok = cur.execute("SELECT max(requested_at) FROM us_event_pulls WHERE status = 'ok'").fetchone()[0]
    last_date, first_date = cur.execute("SELECT max(date) FILTER (WHERE NOT scheduled), min(date) FROM us_events").fetchone()
    failed = [r[0] for r in cur.execute("""
        SELECT kind FROM us_event_pulls p
        WHERE status <> 'ok' AND pull_id = (SELECT max(pull_id) FROM us_event_pulls q WHERE q.kind = p.kind)""").fetchall()]
    days = (datetime.date.today() - last_ok.date()).days if last_ok else None
    stale = days is None or days > STALE_AFTER_DAYS
    flags = ["latest %s pull failed" % k for k in failed]
    return [{"dataset": "us-events", "status": "attention" if (stale or failed) else "ok",
             "archive_read_through": _d(last_date), "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
             "archive_read_back_to": _d(first_date),
             "shape_flags": flags}]


MANIFEST = {
    "id": "us-events",
    "section": "us-reference",
    "name": {"en": "US dated events (FOMC, CPI releases, shutdowns, results days)",
             "ja": "米国イベント日付（FOMC・CPI発表・政府閉鎖・決算発表日）"},
    "shape": "company",
    "summary": ("The days things happened, from 2018: each FOMC statement and scheduled meeting, each CPI release "
                "morning and the ones on the schedule, each federal shutdown's first and last day, and the day "
                "every US filer announced its quarterly results (its 8-K under Item 2.02). The event layer the "
                "front page's box joins to daily prices for 'how did X do after Y'."),
    "source": {
        "publisher": "Federal Reserve Board; U.S. Bureau of Labor Statistics; U.S. Securities and Exchange Commission; Wikipedia",
        "document": ("FOMC calendar and historical pages; BLS archive and schedule of CPI news releases; EDGAR "
                     "submissions (8-K filings and their items); the article 'Government shutdowns in the United States'"),
        "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "credit": "Sources: Federal Reserve Board; U.S. Bureau of Labor Statistics; U.S. Securities and Exchange Commission (EDGAR); Wikipedia.",
        "license_note": ("Federal Reserve, BLS and SEC pages are US government works in the public domain. The shutdown "
                         "list is five dates checked against a Wikipedia article (CC BY-SA 4.0); the article's text is "
                         "archived as evidence, not redistributed."),
    },
    "keys": ["kind", "date", "ticker"],
    "frequency": "daily",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": False,
                "history_from": "2018-01-01", "stale_after_days": STALE_AFTER_DAYS},
    "measures": [
        {"id": "date", "label": "Event day", "unit": "date", "trust": "official"},
        {"id": "end_date", "label": "Last day (shutdowns)", "unit": "date", "trust": "official"},
    ],
    "endpoints": {"events": "/api/v1/us/events", "summary": "/api/v1/us/events/summary"},
    "capabilities": ["summary"],
    "cite": "/api/v1/us/events",
    "page": "/api.html",
    "notes": [
        "A results day is the day the 8-K was filed; most companies announce after the close and file the same day, so the day's close is the price before the news.",
        "is_earnings marks the first Item 2.02 filing from seven days after a fiscal quarter end; other Item 2.02 filings (delivery numbers, mid-quarter updates) are kept but not marked.",
        "A scheduled row is a date on a published calendar that has not happened; performance questions leave it out.",
        "Shutdown rows are a short list kept in the loader; each start date is checked against the article's text and marked when not found.",
    ],
}
