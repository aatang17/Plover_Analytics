# -*- coding: utf-8 -*-
"""US institutional holdings — Form 13F filings, served.

The loader (equity/sec_13f_extract.py) puts the SEC's Form 13F Data Sets
into data/sec.duckdb, one filing window at a time. This module answers:

  * **Who owns this stock?** `/issuer?cusip=` (or `?q=` to find the CUSIP by
    name): every manager's position in the latest window, ranked by value,
    with the change against the manager's position in the previous window
    where both are loaded.
  * **What does this manager own?** `/manager/{cik}`: a manager's latest
    filing, positions ranked by value, with the filing's own totals.
  * **Which managers?** `/managers?q=`: search by name.

Identity is the CUSIP. The 13F data carries no issuer CIK and the issuer
name is free text typed by each manager, so holders are gathered by CUSIP;
a name search is only a way to find the CUSIP, and the API says which
spellings it folded together.

Trust contract: value and shares exactly as filed and extracted. Values are
US dollars for report periods from 2023 (the SEC changed the form; earlier
sets are in thousands, and the batch says which). The share of total is the
one derived figure: a manager's value ÷ the sum of all managers' values for
that CUSIP in the window — a share of what was reported, not of the
company, because 13F covers managers over $100m and long positions only.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import sec_api

router = APIRouter(prefix="/api/v1/us/holdings", tags=["US 13F holdings"])

STALE_AFTER_DAYS = 130

PROVENANCE = {
    "trust": "official",
    "note": ("Positions exactly as each manager filed them on Form 13F and the SEC extracted them "
             "(Form 13F Data Sets). Value is in the batch's unit (US dollars from 2023, thousands "
             "before). `share_of_reported_pct` is the one platform figure: this manager's value ÷ "
             "all reported value for the CUSIP in the window — a share of what 13F filers hold, "
             "not of the company's shares outstanding."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Form 13F).",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{manager_cik}/{accession_nodash}/",
}


def _require():
    cur = sec_api._cur()
    if not sec_api._has(cur, "sec_13f_filings"):
        raise HTTPException(503, "US 13F holdings not loaded on this server yet")
    return cur


def _rows(cur, sql, params=()):
    return sec_api._rows(cur, sql, params)


def _d(v):
    return sec_api._d(v)


def _batches(cur):
    """Loaded windows, newest first: (batch, start, end, unit)."""
    return cur.execute("SELECT batch, window_start, window_end, value_unit FROM sec_13f_batches "
                       "WHERE status = 'ok' ORDER BY window_end DESC").fetchall()


def _cusip_for(cur, q, batch):
    """Name search -> the CUSIPs managers used for it in the window, by value."""
    needle = "%" + q.strip().lower() + "%"
    return _rows(cur, """
        SELECT cusip, arg_max(issuer_name, value) AS issuer_name, count(*) AS positions,
               sum(value) AS value, count(DISTINCT issuer_name) AS spellings
        FROM sec_13f_holdings WHERE batch = ? AND lower(issuer_name) LIKE ? AND cusip IS NOT NULL
        GROUP BY cusip ORDER BY value DESC NULLS LAST LIMIT 12""", [batch, needle])


@router.get("/issuer", openapi_extra={"x-example": "/api/v1/us/holdings/issuer?cusip=037833100"})
def issuer(cusip: str = Query("", description="9-character CUSIP (8 accepted)"),
           q: str = Query("", description="issuer name to look the CUSIP up by"),
           limit: int = Query(50, ge=1, le=1000)):
    """Holders of one security in the latest window, ranked by reported value,
    with each manager's change against the previous window."""
    cur = _require()
    batches = _batches(cur)
    if not batches:
        raise HTTPException(503, "no 13F window loaded")
    latest, prior = batches[0], (batches[1] if len(batches) > 1 else None)
    c = (cusip or "").strip().upper()
    candidates = []
    if not c:
        if not (q or "").strip():
            raise HTTPException(400, "give a cusip, or q= a name to look it up")
        candidates = _cusip_for(cur, q, latest[0])
        if not candidates:
            raise HTTPException(404, "no 13F position names %r in the latest window" % q.strip())
        c = candidates[0]["cusip"]
    elif len(c) not in (8, 9):
        raise HTTPException(400, "a CUSIP is 9 characters (8 without the check digit)")
    like = c if len(c) == 9 else c + "_"
    rows = _rows(cur, """
        WITH latest_report AS (
            -- a manager that filed two quarters' reports in one window (a late
            -- one and the current one) counts once, on its newest report period
            SELECT manager_cik, max(report_period) AS report_period
            FROM sec_13f_filings WHERE batch = ? AND NOT amendment GROUP BY manager_cik),
        raw_cur AS (
            SELECT h.accession, f.manager_cik, f.manager_name, f.report_period, f.filed, f.amendment,
                   h.issuer_name, h.title_of_class, h.cusip, h.value, h.shares, h.shares_type, h.put_call,
                   h.discretion, h.voting_sole, h.voting_shared, h.voting_none
            FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
            JOIN latest_report l ON l.manager_cik = f.manager_cik AND l.report_period = f.report_period
            WHERE h.batch = ? AND h.cusip LIKE ? AND NOT f.amendment AND h.put_call IS NULL),
        -- a line whose value ÷ shares is more than three times off the median
        -- implied price for the security is a units slip (shares in dollars,
        -- dollars in thousands); its shares are set aside, its value kept
        med AS (SELECT median(value / shares) AS px FROM raw_cur WHERE shares > 0 AND value > 0),
        cur AS (
            SELECT r.*, CASE WHEN r.shares > 0 AND r.value > 0 AND m.px > 0
                              AND (r.value / r.shares) / m.px BETWEEN 1.0/3 AND 3 THEN r.shares END AS shares_ok
            FROM raw_cur r, med m),
        agg AS (
            SELECT manager_cik, arg_max(manager_name, filed) AS manager_name, max(report_period) AS report_period,
                   max(filed) AS filed, arg_max(issuer_name, value) AS issuer_name,
                   arg_max(title_of_class, value) AS title_of_class, sum(value) AS value, sum(shares_ok) AS shares,
                   arg_max(shares_type, value) AS shares_type, sum(voting_sole) AS voting_sole,
                   sum(voting_shared) AS voting_shared, sum(voting_none) AS voting_none, count(*) AS lines
            FROM cur GROUP BY manager_cik),
        prev_report AS (
            SELECT manager_cik, max(report_period) AS report_period
            FROM sec_13f_filings WHERE batch = ? AND NOT amendment GROUP BY manager_cik),
        raw_prev AS (
            SELECT f.manager_cik, h.value, h.shares
            FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
            JOIN prev_report l ON l.manager_cik = f.manager_cik AND l.report_period = f.report_period
            WHERE h.batch = ? AND h.cusip LIKE ? AND NOT f.amendment AND h.put_call IS NULL),
        pmed AS (SELECT median(value / shares) AS px FROM raw_prev WHERE shares > 0 AND value > 0),
        prev AS (
            SELECT r.manager_cik, sum(r.value) AS value,
                   sum(CASE WHEN r.shares > 0 AND r.value > 0 AND m.px > 0 AND (r.value / r.shares) / m.px BETWEEN 1.0/3 AND 3 THEN r.shares END) AS shares
            FROM raw_prev r, pmed m GROUP BY r.manager_cik)
        SELECT a.*, p.value AS prior_value, p.shares AS prior_shares,
               (SELECT sum(value) FROM agg) AS total_value, (SELECT count(*) FROM agg) AS holders
        FROM agg a LEFT JOIN prev p USING (manager_cik)
        ORDER BY a.value DESC NULLS LAST LIMIT ?""",
                 [latest[0], latest[0], like, prior[0] if prior else "", prior[0] if prior else "", like, limit])
    if not rows:
        raise HTTPException(404, "no 13F position in CUSIP %s in the latest window" % c)
    total, holders = rows[0]["total_value"], rows[0]["holders"]
    out = []
    for r in rows:
        d = {k: _d(v) for k, v in r.items() if k not in ("total_value", "holders")}
        d["share_of_reported_pct"] = round(r["value"] / total * 100, 4) if (total and r["value"] is not None) else None
        d["shares_change"] = ((r["shares"] or 0) - (r["prior_shares"] or 0)) if prior and (
            r["shares"] is not None or r["prior_shares"] is not None) else None
        d["new_position"] = bool(prior) and r["prior_shares"] is None
        d["source_url"] = "https://www.sec.gov/Archives/edgar/data/%d/" % r["manager_cik"]
        out.append(d)
    spellings = _rows(cur, "SELECT issuer_name, count(*) AS n FROM sec_13f_holdings WHERE batch = ? AND cusip LIKE ? "
                           "GROUP BY 1 ORDER BY 2 DESC LIMIT 6", [latest[0], like])
    return {"cusip": c, "issuer_name": rows[0]["issuer_name"], "spellings": spellings,
            "candidates": candidates[:6] if candidates else None,
            "window": {"batch": latest[0], "start": _d(latest[1]), "end": _d(latest[2]), "value_unit": latest[3]},
            "prior_window": ({"batch": prior[0], "start": _d(prior[1]), "end": _d(prior[2])} if prior else None),
            "holders": holders, "total_reported_value": total, "rows": out, "provenance": PROVENANCE}


@router.get("/manager/{manager_cik}", openapi_extra={"x-example": "/api/v1/us/holdings/manager/1067983"})
def manager(manager_cik: str, limit: int = Query(100, ge=1, le=5000)):
    """One manager's latest 13F: positions ranked by value and the filing's own totals."""
    cur = _require()
    n = sec_api.parse_cik(manager_cik)
    f = _rows(cur, """
        SELECT accession, batch, filed, submission_type, report_period, amendment, amendment_type,
               manager_name, city, state, report_type, other_managers, table_entries, table_value
        FROM sec_13f_filings WHERE manager_cik = ? AND NOT amendment
        ORDER BY report_period DESC, filed DESC LIMIT 1""", [n])
    if not f:
        raise HTTPException(404, "no 13F filing by manager CIK %d in the loaded windows" % n)
    f = f[0]
    unit = cur.execute("SELECT value_unit FROM sec_13f_batches WHERE batch = ?", [f["batch"]]).fetchone()
    rows = _rows(cur, """
        SELECT issuer_name, title_of_class, cusip, figi, value, shares, shares_type, put_call, discretion,
               voting_sole, voting_shared, voting_none
        FROM sec_13f_holdings WHERE accession = ? ORDER BY value DESC NULLS LAST LIMIT ?""",
                 [f["accession"], limit])
    total = cur.execute("SELECT sum(value), count(*) FROM sec_13f_holdings WHERE accession = ?",
                        [f["accession"]]).fetchone()
    for r in rows:
        r["share_of_portfolio_pct"] = round(r["value"] / total[0] * 100, 4) if (total[0] and r["value"] is not None) else None
    filing = {k: _d(v) for k, v in f.items()}
    filing["source_url"] = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (n, f["accession"].replace("-", ""))
    return {"manager_cik": n, "manager_name": f["manager_name"], "filing": filing,
            "value_unit": unit[0] if unit else None, "positions": total[1], "total_value": total[0],
            "rows": rows, "provenance": PROVENANCE}


@router.get("/managers", openapi_extra={"x-example": "/api/v1/us/holdings/managers?q=berkshire"})
def managers(q: str = Query(..., min_length=2), limit: int = Query(25, ge=1, le=200)):
    """Managers by name, with their latest filing's size."""
    cur = _require()
    rows = _rows(cur, """
        SELECT manager_cik, arg_max(manager_name, filed) AS manager_name, max(report_period) AS report_period,
               max(filed) AS filed, arg_max(table_value, filed) AS table_value,
               arg_max(table_entries, filed) AS table_entries
        FROM sec_13f_filings WHERE lower(manager_name) LIKE ? AND NOT amendment
        GROUP BY manager_cik ORDER BY table_value DESC NULLS LAST LIMIT ?""",
                 ["%" + q.strip().lower() + "%", limit])
    for r in rows:
        r["report_period"], r["filed"] = _d(r["report_period"]), _d(r["filed"])
    return {"query": q.strip(), "managers": rows, "provenance": PROVENANCE}


@router.get("/summary", openapi_extra={"x-example": "/api/v1/us/holdings/summary"})
def summary():
    """What is loaded: windows, filings, positions."""
    cur = _require()
    batches = _rows(cur, "SELECT batch, window_start, window_end, filings, holdings, value_unit, loaded_at, "
                         "status, detail FROM sec_13f_batches ORDER BY window_end DESC")
    for b in batches:
        for k in ("window_start", "window_end", "loaded_at"):
            b[k] = _d(b[k])
    tot = cur.execute("SELECT count(*), count(DISTINCT manager_cik), min(filed), max(filed) FROM sec_13f_filings").fetchone()
    return {"batches": batches, "filings": tot[0], "managers": tot[1],
            "earliest_filed": _d(tot[2]), "latest_filed": _d(tot[3]), "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    try:
        cur = sec_api._cur()
    except HTTPException:
        return []
    if not sec_api._has(cur, "sec_13f_batches"):
        return []
    rows = cur.execute("SELECT batch, window_end, window_start, loaded_at, status FROM sec_13f_batches "
                       "ORDER BY window_end").fetchall()
    good = [r for r in rows if r[4] == "ok"]
    failed = [r[0] for r in rows if r[4] == "failed"]
    if not good:
        return [{"dataset": "sec-13f", "status": "attention", "archive_read_through": None,
                 "days_behind": None, "stale_after_days": STALE_AFTER_DAYS, "stale": True,
                 "last_extracted_at": None, "archive_read_back_to": None,
                 "shape_flags": ["no window loaded"] + ["window %s failed" % f for f in failed]}]
    through, back_to = good[-1][1], good[0][2]
    days = (datetime.date.today() - through).days
    stale = days > STALE_AFTER_DAYS
    ran = max((r[3] for r in good if r[3]), default=None)
    return [{"dataset": "sec-13f", "status": "attention" if (stale or failed) else "ok",
             "archive_read_through": through.isoformat(), "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": ran.isoformat() + "Z" if ran else None,
             "archive_read_back_to": back_to.isoformat(),
             "shape_flags": ["window %s failed to load" % f for f in failed]}]


MANIFEST = {
    "id": "sec-13f",
    "section": "us-reference",
    "name": {"en": "US institutional holdings (Form 13F)", "ja": "米国機関投資家保有状況（Form 13F）"},
    "shape": "company",
    "summary": ("Every position every US institutional manager over $100m reports each quarter, "
                "from the SEC's Form 13F Data Sets: who holds a security and how that changed "
                "since the previous window, and what a manager holds."),
    "source": {
        "publisher": "U.S. Securities and Exchange Commission",
        "document": "Form 13F Data Sets (quarterly filing windows since 2013 Q2)",
        "url": "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
        "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Form 13F).",
        "license_note": "US government work; public domain.",
    },
    "keys": ["cusip", "manager_cik", "accession"],
    "frequency": "quarterly",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": False,
                "history_from": "the newest filing windows loaded (SEC_13F_BATCHES); the SEC's run from 2013 Q2",
                "stale_after_days": STALE_AFTER_DAYS},
    "measures": [
        {"id": "value", "label": "Position value, as filed", "unit": "count", "trust": "official"},
        {"id": "shares", "label": "Shares or principal amount", "unit": "shares", "trust": "official"},
        {"id": "share_of_reported_pct", "label": "Share of all reported value in the CUSIP", "unit": "%", "trust": "derived",
         "calc": "this manager's reported value ÷ the sum of every manager's reported value for the CUSIP in the "
                 "window × 100 — a share of what 13F filers hold, not of the company"},
    ],
    "endpoints": {
        "issuer": "/api/v1/us/holdings/issuer",
        "manager": "/api/v1/us/holdings/manager/{manager_cik}",
        "managers": "/api/v1/us/holdings/managers",
        "summary": "/api/v1/us/holdings/summary",
    },
    "capabilities": ["summary"],
    "cite": "/api/v1/us/holdings/issuer?cusip={cusip}",
    "page": "/api.html",
    "notes": [
        "Identity is the CUSIP; the data carries no issuer CIK and issuer names are each manager's own spelling.",
        "A line whose value ÷ shares is more than three times off the median implied price for the security in that "
        "window is treated as a units slip: its value counts, its share count does not.",
        "Values are US dollars for report periods from 2023 and thousands of dollars before; each window says which.",
        "13F covers long positions of managers over $100m, reported up to 45 days after quarter end; "
        "a share of reported value is not a share of the company.",
        "Amendments keep their own row and flag; holders are gathered from original filings only.",
    ],
}
