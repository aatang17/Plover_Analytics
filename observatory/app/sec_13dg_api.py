# -*- coding: utf-8 -*-
"""US 5% holders — Schedules 13D and 13G, served.

The loader (equity/sec_13dg_extract.py) reads every structured Schedule 13D
and 13G (filed as XML since 18 December 2024) from the EDGAR archive into
data/sec.duckdb. This module answers:

  * **Who holds 5% or more of this company?** `/company/{cik or ticker}`:
    the latest filing of each filer, largest stake first, with every filing
    underneath. A 13D is an active holder, who states its purpose (Item 4);
    a 13G a passive one, filed under the rule it names.
  * **Who has just crossed 5%, and who is active?** `/recent`: the newest
    filings across the market, new 13Ds (not amendments) by default.
  * **What does this holder hold?** `/holder/{cik}`: a filer's latest filing
    in each company.

A filing's reporting persons are often one holder's affiliated entities, each
reporting the same shares, so a stake is the largest percent on the filing's
cover pages and never their sum. Trust contract: every share count and percent
exactly as filed; nothing is recomputed. An amendment is its own row.
Missing is null, never 0.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import sec_api

router = APIRouter(prefix="/api/v1/us/stakes", tags=["US 5% holders"])

# A filing is banked the morning after it is filed and read in the same night's
# refresh: a weekend plus a holiday, plus that morning
STALE_AFTER_DAYS = 5

PROVENANCE = {
    "trust": "official",
    "note": ("Each stake exactly as the holder filed it on Schedule 13D or 13G: shares, voting and "
             "dispositive power and percent of class from the cover pages, the purpose in the holder's "
             "words. A filing's stake is the largest percent among its reporting persons, which are "
             "usually one holder's affiliated entities reporting the same shares."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Schedules 13D and 13G).",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{issuer_cik}/{accession_nodash}/",
}

KINDS = {"13D": "SCHEDULE 13D", "13G": "SCHEDULE 13G"}


def _require():
    cur = sec_api._cur()
    if not sec_api._has(cur, "sec_13dg_filings"):
        raise HTTPException(503, "US 5% holder filings not loaded on this server yet")
    return cur


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _url(issuer_cik, accession):
    if not issuer_cik or not accession:
        return None
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (issuer_cik, accession.replace("-", ""))


# One row per filing: its lead reporting person and its stake. The lead is the
# first person on the cover pages; the stake the largest percent on any of them.
_FILINGS = """
    WITH p AS (
        SELECT accession, count(*) AS persons, max(percent) AS percent, max(shares) AS shares,
               arg_min(name, seq) AS lead_name, arg_min(person_cik, seq) AS lead_cik,
               arg_min(person_type, seq) AS lead_type
        FROM sec_13dg_persons GROUP BY accession)
    SELECT f.accession, f.form, f.filed, f.event_date, f.amendment, f.amendment_no, f.issuer_cik,
           f.issuer_name, f.cusip, f.filer_cik, f.rule, f.five_percent_or_less, f.purpose,
           p.persons, p.percent, p.shares, p.lead_name, coalesce(p.lead_cik, f.filer_cik) AS lead_cik, p.lead_type
    FROM sec_13dg_filings f JOIN p USING (accession)
"""


def _shape(r, purpose_chars=600):
    out = {k: _d(v) for k, v in r.items()}
    out["kind"] = "13D" if "13D" in (r.get("form") or "") else "13G"
    if out.get("purpose") and len(out["purpose"]) > purpose_chars:
        out["purpose"] = out["purpose"][:purpose_chars].rstrip() + "…"
    out["source_url"] = _url(r.get("issuer_cik"), r.get("accession"))
    return out


def _holder_key(r):
    return r.get("filer_cik") or (r.get("lead_name") or "").upper()


@router.get("/company/{company}", openapi_extra={"x-example": "/api/v1/us/stakes/company/DELL"})
def company(company: str, limit: int = Query(100, ge=1, le=1000)):
    """Every filer's latest stake in one company, largest first, and the filings behind them."""
    cur = _require()
    cik = sec_api.resolve_company(cur, company)
    rows = sec_api._rows(cur, _FILINGS + " WHERE f.issuer_cik = ? ORDER BY f.filed DESC, f.accession DESC", [cik])
    if not rows:
        raise HTTPException(404, "no structured Schedule 13D or 13G names CIK %d as the issuer "
                                 "(filed as XML since 18 December 2024)" % cik)
    latest = {}
    for r in rows:                                   # newest first: the first seen per holder is its latest
        latest.setdefault(_holder_key(r), r)
    holders = sorted(latest.values(), key=lambda r: -(r["percent"] if r["percent"] is not None else -1))
    name = rows[0]["issuer_name"]
    return {"cik": cik, "name": name, "cusip": rows[0]["cusip"],
            "holders": [_shape(r) for r in holders],
            "filings": [_shape(r, 300) for r in rows[:limit]], "filings_total": len(rows),
            "provenance": PROVENANCE}


@router.get("/recent", openapi_extra={"x-example": "/api/v1/us/stakes/recent?kind=13D"})
def recent(kind: str = Query("13D", description="13D (active holders), 13G (passive) or all"),
           new_only: bool = Query(True, description="first filings only, not amendments"),
           days: int = Query(30, ge=1, le=800), limit: int = Query(100, ge=1, le=1000)):
    """The newest filings across the market: by default, holders who have just filed a new 13D."""
    cur = _require()
    k = kind.strip().upper()
    if k not in ("13D", "13G", "ALL"):
        raise HTTPException(400, "kind is 13D, 13G or all")
    where, params = ["f.filed >= ?"], [datetime.date.today() - datetime.timedelta(days=days)]
    if k != "ALL":
        where.append("f.form LIKE ?")
        params.append(KINDS[k] + "%")
    if new_only:
        where.append("NOT f.amendment")
    rows = sec_api._rows(cur, _FILINGS + " WHERE " + " AND ".join(where) +
                         " ORDER BY f.filed DESC, p.percent DESC NULLS LAST LIMIT ?", params + [limit])
    return {"kind": k, "new_only": new_only, "days": days, "filings": [_shape(r, 300) for r in rows],
            "provenance": PROVENANCE}


@router.get("/holder/{cik}", openapi_extra={"x-example": "/api/v1/us/stakes/holder/1418226"})
def holder(cik: str, limit: int = Query(200, ge=1, le=2000)):
    """A filer's latest filing in each company it has filed on."""
    cur = _require()
    try:
        n = int(cik.lower().replace("cik:", ""))
    except ValueError:
        raise HTTPException(400, "give the holder's CIK, e.g. 1418226")
    rows = sec_api._rows(cur, _FILINGS + """ WHERE f.filer_cik = ? OR f.accession IN (
                             SELECT accession FROM sec_13dg_persons WHERE person_cik = ?)
                         ORDER BY f.filed DESC, f.accession DESC""", [n, n])
    if not rows:
        raise HTTPException(404, "no structured Schedule 13D or 13G filed by or naming CIK %d" % n)
    latest = {}
    for r in rows:
        latest.setdefault(r["issuer_cik"], r)
    out = sorted(latest.values(), key=lambda r: r["filed"], reverse=True)[:limit]
    return {"cik": n, "name": next((r["lead_name"] for r in rows if r["lead_cik"] == n), rows[0]["lead_name"]),
            "companies": [_shape(r, 300) for r in out], "companies_total": len(latest),
            "provenance": PROVENANCE}


@router.get("/summary", openapi_extra={"x-example": "/api/v1/us/stakes/summary"})
def summary():
    """What is loaded: filings by form, the span of filing dates, the last read."""
    cur = _require()
    forms = sec_api._rows(cur, "SELECT form, count(*) AS filings FROM sec_13dg_filings GROUP BY 1 ORDER BY 1")
    tot = cur.execute("SELECT count(*), count(DISTINCT issuer_cik), min(filed), max(filed) FROM sec_13dg_filings").fetchone()
    return {"forms": forms, "filings": tot[0], "issuers": tot[1], "earliest_filed": _d(tot[2]),
            "latest_filed": _d(tot[3]), "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    try:
        cur = sec_api._cur()
    except HTTPException:
        return []
    if not sec_api._has(cur, "sec_13dg_runs"):
        return []
    last = cur.execute("SELECT run_at, status, detail FROM sec_13dg_runs ORDER BY run_at DESC LIMIT 1").fetchone()
    span = cur.execute("SELECT min(filed), max(filed) FROM sec_13dg_filings").fetchone()
    # fresh as the newest filing read, not the newest day looked at: a day with
    # nothing banked (the capture stopped) must not count as read
    through = cur.execute("SELECT max(day) FROM sec_13dg_filings").fetchone()[0]
    flags = []
    if last and last[1] != "ok":
        flags.append("the last read failed: %s" % (last[2] or "no detail"))
    if through is None:
        return [{"dataset": "sec-13dg", "status": "attention", "archive_read_through": None, "days_behind": None,
                 "stale_after_days": STALE_AFTER_DAYS, "stale": True,
                 "last_extracted_at": last[0].isoformat() + "Z" if last else None,
                 "archive_read_back_to": None, "shape_flags": flags or ["nothing read yet"]}]
    days = (datetime.date.today() - through).days
    stale = days > STALE_AFTER_DAYS
    return [{"dataset": "sec-13dg", "status": "attention" if (stale or flags) else "ok",
             "archive_read_through": through.isoformat(), "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": last[0].isoformat() + "Z" if last else None,
             "archive_read_back_to": _d(span[0]), "shape_flags": flags}]


MANIFEST = {
    "id": "sec-13dg",
    "section": "us-reference",
    "name": {"en": "US 5% holders (Schedules 13D and 13G)", "ja": "米国大量保有報告（Schedule 13D・13G）"},
    "shape": "company",
    "summary": ("Everyone who reports owning more than 5% of a US-registered company's shares, from each "
                "Schedule 13D and 13G the day after it is filed: the stake, voting power and type of "
                "holder, and for a 13D the holder's stated purpose. The US counterpart of Japan's 5% filings."),
    "source": {
        "publisher": "U.S. Securities and Exchange Commission",
        "document": "Schedules 13D and 13G (and amendments), filed as structured XML since 18 December 2024",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=SCHEDULE+13D",
        "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Schedules 13D and 13G).",
        "license_note": "US government work; public domain.",
    },
    "keys": ["issuer_cik", "accession"],
    "frequency": "per-filing",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": False,
                "history_from": "18 December 2024, the first day Schedules 13D and 13G were filed as XML",
                "stale_after_days": STALE_AFTER_DAYS},
    "measures": [
        {"id": "percent", "label": "Percent of class, as filed", "unit": "%", "trust": "official"},
        {"id": "shares", "label": "Shares beneficially owned, as filed", "unit": "shares", "trust": "official"},
        {"id": "sole_vote", "label": "Sole voting power", "unit": "shares", "trust": "official"},
        {"id": "shared_vote", "label": "Shared voting power", "unit": "shares", "trust": "official"},
    ],
    "endpoints": {
        "company": "/api/v1/us/stakes/company/{cik_or_ticker}",
        "recent": "/api/v1/us/stakes/recent",
        "holder": "/api/v1/us/stakes/holder/{cik}",
        "summary": "/api/v1/us/stakes/summary",
    },
    "capabilities": ["company", "summary"],
    "cite": "/api/v1/us/stakes/company/{issuer_cik}",
    "page": "/api.html",
    "notes": [
        "A 13D is filed by a holder who may seek to influence or control the company and must state its "
        "purpose (Item 4); a 13G by a passive investor, an institution under Rule 13d-1(b) or an exempt holder.",
        "A filing's stake is the largest percent on its cover pages: its reporting persons are usually one "
        "holder's affiliates reporting the same shares, so percents are never added up.",
        "Percent of class is the filer's own figure, on the share count the filer used; it is not recomputed.",
        "Only the structured filings since 18 December 2024 are read; earlier SC 13D and SC 13G filings "
        "were free text.",
    ],
}
