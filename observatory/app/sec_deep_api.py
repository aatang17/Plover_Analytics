# -*- coding: utf-8 -*-
"""US deep coverage — full filed history for a chosen set of large US companies.

sec_api.py serves every US filer for the quarters the data-set shelf holds.
This module serves the other cut, loaded by equity/sec_companyfacts.py from
the SEC's company-facts files: every number a chosen company has filed since
2009, each tied to the filing that reported it. Four views:

  1. **The universe** (`/deep`): the companies covered, their sector, the
     predecessor registrants carrying their early history, and how deep and
     how fresh each one is.
  2. **What a company files** (`/deep/{company}`): every concept (XBRL tag)
     with a value, its standard label, units, how many periods, and the first
     and last period end — the menu to ask the other two views from.
  3. **Key indicators by period** (`/deep/{company}/indicators`): revenue,
     margins' inputs, cash flows, balance-sheet totals — one row per fiscal
     year (or quarter), each value naming the tag and the filing it came from.
  4. **One concept's history** (`/deep/{company}/series`): every value of one
     tag, one per period, or every filing's version of every period.

Point in time
-------------
A period's number is filed more than once — in its own report, then as a
comparative, sometimes restated. `basis=latest` (default) serves the value
from the most recent filing; `basis=first` the value as first reported;
`basis=all` every version. `as_of=YYYY-MM-DD` first drops every filing filed
after that day, so `as_of` + `latest` is what a reader could have known then.
Choosing among filed values is selection, not calculation: every value is
Official, exactly as filed, and names its accession number.

A company's predecessor registrants (Google Inc. before Alphabet, Exxon
Mobil Corp. before its 2026 holding company) are read together with it; each
row names the CIK that filed it.

Period rules: a duration of 350–380 days is annual, 80–100 days a quarter;
anything else (six- and nine-month year-to-date spans) appears only under
`freq=all`. A fourth quarter is served only where a filer tagged one — it is
never derived as the year less three quarters.
"""
import datetime
import json
import os

from fastapi import APIRouter, HTTPException, Query

from . import sec_api

router = APIRouter(prefix="/api/v1/us/financials/deep", tags=["US deep coverage"])

STALE_AFTER_DAYS = 3      # app/backfill.py pulls every day; three missed days is a fault

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as filed in each company's XBRL reports, from "
             "the SEC's company-facts files (data.sec.gov/api/xbrl/companyfacts). "
             "Nothing is recomputed or rescaled. Every pull is archived with "
             "its SHA-256; accn is the SEC accession number of the filing "
             "the value came from."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR.",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/",
}

CALC = {
    "basis": ("latest = the value from the most recently filed report that "
              "carries the period (restatements win); first = the value as "
              "first reported; all = every filed version. Selection among "
              "filed values, never a calculation."),
    "as_of": ("as_of drops every filing filed after that day before the basis "
              "is applied — what a reader could have known then."),
    "periods": ("annual = a duration of 350–380 days; quarterly = 80–100 days; "
                "balances are read on those periods' end dates. A missing "
                "fourth quarter stays missing; it is never derived."),
    "indicators": ("Each indicator is the first tag in its list that has a "
                   "value for the period; the tag used is on every row."),
}

# Indicator mapping: sec_api.FIELDS, except shares outstanding. The dei cover
# figure is dated on the cover page, not the balance sheet, so it never falls
# on a period end; the balance-sheet tag does.
FIELDS = [(f, k, (["CommonStockSharesOutstanding"] if f == "shares_outstanding" else t))
          for f, k, t in sec_api.FIELDS]
UNITS = {"eps_basic": "USD/shares", "eps_diluted": "USD/shares",
         "shares_outstanding": "shares"}
FREQS = {"annual": (350, 380), "quarterly": (80, 100)}
FLOW_TAGS = sorted({t for f, k, ts in FIELDS if k == "duration" and not f.startswith("eps")
                    for t in ts})


# Display names, curated in the universe file. The SEC's own entity names stay
# as filed (entity_name); they are upper case, carry suffixes like /DE/, and
# for BAC name a co-registrant subsidiary (BofA Finance LLC), so a page shows
# `name` and keeps entity_name beside it.
UNIVERSE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "equity", "us_universe.json")


def _names():
    try:
        with open(UNIVERSE_FILE, encoding="utf-8") as f:
            return {c["ticker"]: c.get("name") for c in json.load(f)["companies"]}
    except (OSError, ValueError, KeyError):
        return {}


NAMES = _names()


def _named(row):
    from . import us_companies, us_link
    cik = row.get("cik")
    row["name"] = ((us_companies.name(cik) if cik else None) or NAMES.get(row.get("ticker"))
                   or row.get("entity_name"))
    row["logo"] = us_link.logo(cik) if cik else None
    return row


# ---- helpers ---------------------------------------------------------------
def _require():
    cur = sec_api._cur()
    names = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if "sec_cf_facts" not in names or "sec_cf_companies" not in names:
        raise HTTPException(503, "US deep coverage not loaded on this server yet")
    return cur


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _date(value, name):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, "%s must be YYYY-MM-DD" % name)


def _resolve(cur, company):
    """Ticker or CIK -> the covered company and every CIK carrying its history."""
    key = (company or "").strip()
    rows = sec_api._rows(cur, """
        SELECT cik, ticker, sector, successor_cik, entity_name, first_filed, last_filed,
               facts, filings FROM sec_cf_companies
        WHERE upper(ticker) = upper(?) OR CAST(cik AS VARCHAR) = ltrim(regexp_replace(?, '(?i)^cik:', ''), '0')
        """, [key, key])
    if not rows:
        raise HTTPException(404, "%r is not in the deep-coverage universe; "
                                 "GET /api/v1/us/financials/deep lists it" % key)
    hit = rows[0]
    main_cik = hit["successor_cik"] or hit["cik"]
    group = sec_api._rows(cur, """
        SELECT cik, ticker, sector, successor_cik, entity_name, first_filed, last_filed,
               facts, filings FROM sec_cf_companies
        WHERE cik = ? OR successor_cik = ? ORDER BY successor_cik NULLS FIRST, first_filed DESC
        """, [main_cik, main_cik])
    for g in group:
        for k in ("first_filed", "last_filed"):
            g[k] = _d(g[k])
    return _named(group[0]), group


def _period_sql(freq):
    """WHERE fragment keeping durations of the frequency (instants pass)."""
    if freq == "all":
        return ""
    lo, hi = FREQS[freq]
    return (" AND (period_start IS NULL OR date_diff('day', period_start, period_end) "
            "BETWEEN %d AND %d)" % (lo, hi))


def _ends(cur, ciks, freq, ceiling):
    """The period ends the frequency is defined on: ends of the durations the
    core flow lines (revenue, income, cash flows) are filed over. Only those
    lines, so a one-off twelve-month figure on some other date (a pro-forma
    note, an acquiree's year) cannot invent a fiscal period."""
    lo, hi = FREQS[freq]
    ph = ",".join("?" * len(ciks))
    rows = cur.execute("""
        SELECT DISTINCT period_end FROM sec_cf_facts
        WHERE cik IN (%s) AND taxonomy = 'us-gaap' AND tag IN (%s)
          AND period_start IS NOT NULL
          AND date_diff('day', period_start, period_end) BETWEEN %d AND %d
          AND (CAST(? AS DATE) IS NULL OR filed <= ?)
        """ % (ph, ",".join("?" * len(FLOW_TAGS)), lo, hi),
        list(ciks) + FLOW_TAGS + [ceiling, ceiling]).fetchall()
    return {r[0] for r in rows}


def _pick_sql(basis, picked):
    """One value per (tag, unit, period) out of the `picked` query."""
    order = "filed DESC, accn DESC" if basis == "latest" else "filed, accn"
    return """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY tag, unit, period_start, period_end ORDER BY %s) AS rn
            FROM (%s)) WHERE rn = 1""" % (order, picked)


def _source(cik, accn):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, accn.replace("-", ""))


def _check(name, value, allowed):
    v = (value or "").strip().lower()
    if v not in allowed:
        raise HTTPException(400, "%s must be one of %s" % (name, ", ".join(allowed)))
    return v


# ---- 1. universe -----------------------------------------------------------
@router.get("", openapi_extra={"x-example": "/api/v1/us/financials/deep"})
def universe():
    """The companies with full filed history, by sector, with depth and freshness."""
    cur = _require()
    rows = sec_api._rows(cur, """
        SELECT c.cik, c.ticker, c.sector, c.successor_cik, c.entity_name, c.first_filed,
               c.last_filed, c.facts, c.filings, p.fetched_at AS last_pulled,
               p.bs_checked, p.bs_failed
        FROM sec_cf_companies c
        LEFT JOIN sec_cf_pulls p ON p.pull_id = c.last_pull_id
        ORDER BY c.sector, c.ticker, c.successor_cik NULLS FIRST""")
    out = {}
    for r in rows:
        for k in ("first_filed", "last_filed", "last_pulled"):
            r[k] = _d(r[k])
        main = r["successor_cik"] or r["cik"]
        if r["successor_cik"] is None:
            out[main] = _named(dict(r, predecessors=[], sec_code="cik:%d" % r["cik"]))
            out[main].pop("successor_cik")
        else:
            out[main]["predecessors"].append(
                {k: r[k] for k in ("cik", "entity_name", "first_filed", "last_filed",
                                   "facts", "filings")})
    companies = list(out.values())
    for c in companies:
        c["history_from"] = min([c["first_filed"]] + [p["first_filed"] for p in c["predecessors"]])
    return {"companies": companies, "count": len(companies),
            "sectors": sorted({c["sector"] for c in companies}),
            "calc": CALC, "provenance": PROVENANCE}


# ---- 2. what a company files ----------------------------------------------
@router.get("/{company}", openapi_extra={"x-example": "/api/v1/us/financials/deep/AAPL"})
def concepts(company: str,
             q: str = Query("", description="substring of the tag or its label"),
             taxonomy: str = Query("", description="us-gaap | dei | srt | ifrs-full | ..."),
             limit: int = Query(2000, ge=1, le=5000)):
    """Every concept this company has filed a value for: label, units, period
    count, first and last period end — the menu for /indicators and /series."""
    cur = _require()
    head, group = _resolve(cur, company)
    ciks = [g["cik"] for g in group]
    needle = (q or "").strip().lower()
    rows = sec_api._rows(cur, """
        SELECT f.taxonomy, f.tag, any_value(t.label) AS label,
               list(DISTINCT f.unit ORDER BY f.unit) AS units,
               count(DISTINCT (f.period_start, f.period_end)) AS periods,
               count(DISTINCT CASE WHEN date_diff('day', f.period_start, f.period_end)
                                   BETWEEN 350 AND 380 THEN f.period_end END) AS annual_periods,
               count(DISTINCT CASE WHEN date_diff('day', f.period_start, f.period_end)
                                   BETWEEN 80 AND 100 THEN f.period_end END) AS quarterly_periods,
               bool_or(f.period_start IS NULL) AS is_balance,
               min(f.period_end) AS first_period, max(f.period_end) AS last_period,
               max(f.filed) AS last_filed, count(*) AS filed_values
        FROM sec_cf_facts f
        LEFT JOIN sec_cf_tags t ON t.taxonomy = f.taxonomy AND t.tag = f.tag
        WHERE f.cik IN (%s)
          AND (? = '' OR f.taxonomy = ?)
          AND (? = '' OR lower(f.tag) LIKE ? OR lower(t.label) LIKE ?)
        GROUP BY f.taxonomy, f.tag
        ORDER BY last_period DESC, periods DESC, f.tag
        LIMIT ?""" % ",".join("?" * len(ciks)),
        ciks + [taxonomy.strip(), taxonomy.strip(), needle, "%" + needle + "%",
                "%" + needle + "%", limit])
    for r in rows:
        for k in ("first_period", "last_period", "last_filed"):
            r[k] = _d(r[k])
    by_tax = {}
    for r in rows:
        by_tax[r["taxonomy"]] = by_tax.get(r["taxonomy"], 0) + 1
    return {"company": head, "registrants": group, "concepts": rows,
            "count": len(rows), "by_taxonomy": by_tax,
            "next": {"indicators": "/api/v1/us/financials/deep/%s/indicators" % head["ticker"],
                     "series": "/api/v1/us/financials/deep/%s/series?tag={tag}" % head["ticker"]},
            "provenance": PROVENANCE}


# ---- 3. key indicators by period -------------------------------------------
@router.get("/{company}/indicators",
            openapi_extra={"x-example": "/api/v1/us/financials/deep/AAPL/indicators"})
def indicators(company: str,
               freq: str = Query("annual", description="annual | quarterly"),
               basis: str = Query("latest", description="latest | first"),
               as_of: str = Query("", description="YYYY-MM-DD: only filings filed by then"),
               limit: int = Query(40, ge=1, le=200)):
    """Key indicators, one row per fiscal year (or quarter), newest first.
    Every value names the tag and the filing it came from."""
    cur = _require()
    freq = _check("freq", freq, ("annual", "quarterly"))
    basis = _check("basis", basis, ("latest", "first"))
    ceiling = _date(as_of, "as_of")
    head, group = _resolve(cur, company)
    ciks = [g["cik"] for g in group]
    ends = sorted(_ends(cur, ciks, freq, ceiling), reverse=True)[:limit]
    if not ends:
        raise HTTPException(404, "no %s periods filed%s" % (
            freq, " by %s" % ceiling if ceiling else ""))
    tags = sorted({t for _, _, ts in FIELDS for t in ts})
    lo, hi = FREQS[freq]
    picked = """
        SELECT cik, tag, unit, period_start, period_end, value, accn, form, filed
        FROM sec_cf_facts
        WHERE cik IN (%s) AND taxonomy = 'us-gaap' AND tag IN (%s)
          AND period_end IN (%s)
          AND (period_start IS NULL OR date_diff('day', period_start, period_end) BETWEEN %d AND %d)
          AND (CAST(? AS DATE) IS NULL OR filed <= ?)""" % (
        ",".join("?" * len(ciks)), ",".join("?" * len(tags)), ",".join("?" * len(ends)), lo, hi)
    facts = {}
    for r in sec_api._rows(cur, _pick_sql(basis, picked),
                           ciks + tags + ends + [ceiling, ceiling]):
        facts[(r["tag"], r["unit"], r["period_end"], r["period_start"] is None)] = r

    panel = []
    for end in ends:
        row = {"period_end": _d(end), "values": {}, "sources": {}}
        start = None
        for field, kind, candidates in FIELDS:
            unit = UNITS.get(field, "USD")
            hit = None
            for tag in candidates:
                hit = facts.get((tag, unit, end, kind == "instant"))
                if hit is not None:
                    break
            row["values"][field] = hit["value"] if hit else None
            row["sources"][field] = ({"tag": hit["tag"], "unit": unit, "accn": hit["accn"],
                                      "form": hit["form"], "filed": _d(hit["filed"]),
                                      "cik": hit["cik"]} if hit else None)
            if hit and kind == "duration" and start is None:
                start = hit["period_start"]
        row["period_start"] = _d(start)
        panel.append(row)
    return {"company": head, "registrants": group, "freq": freq, "basis": basis,
            "as_of": _d(ceiling),
            "fields": [{"id": f, "kind": k, "unit": UNITS.get(f, "USD"), "tags": t}
                       for f, k, t in FIELDS],
            "panel": panel, "calc": CALC, "provenance": PROVENANCE}


# ---- 4. one concept's history ----------------------------------------------
@router.get("/{company}/series",
            openapi_extra={"x-example": "/api/v1/us/financials/deep/AAPL/series?tag=NetIncomeLoss"})
def series(company: str,
           tag: str = Query(..., description="e.g. NetIncomeLoss — see /deep/{company} for the menu"),
           taxonomy: str = Query("us-gaap"),
           unit: str = Query("", description="default: the unit this tag is filed in most"),
           freq: str = Query("annual", description="annual | quarterly | all"),
           basis: str = Query("latest", description="latest | first | all"),
           as_of: str = Query("", description="YYYY-MM-DD: only filings filed by then")):
    """Every period of one concept, newest first — or, with basis=all, every
    filing's version of every period (how a number was revised)."""
    cur = _require()
    freq = _check("freq", freq, ("annual", "quarterly", "all"))
    basis = _check("basis", basis, ("latest", "first", "all"))
    ceiling = _date(as_of, "as_of")
    head, group = _resolve(cur, company)
    ciks = [g["cik"] for g in group]
    t, tax = (tag or "").strip(), (taxonomy or "us-gaap").strip()
    units = cur.execute("""
        SELECT unit, count(*) FROM sec_cf_facts WHERE cik IN (%s) AND taxonomy = ? AND tag = ?
        GROUP BY unit ORDER BY 2 DESC""" % ",".join("?" * len(ciks)),
        ciks + [tax, t]).fetchall()
    if not units:
        raise HTTPException(404, "%s has no filed value of %s:%s" % (head["ticker"], tax, t))
    u = (unit or "").strip() or units[0][0]
    if u not in [x[0] for x in units]:
        raise HTTPException(400, "unit must be one of %s" % ", ".join(x[0] for x in units))
    ends_filter, params = "", []
    if freq != "all":
        ends = sorted(_ends(cur, ciks, freq, ceiling))
        if not ends:
            raise HTTPException(404, "no %s periods filed" % freq)
        ends_filter = " AND period_end IN (%s)" % ",".join("?" * len(ends))
        params = ends
    picked = """
        SELECT cik, tag, unit, period_start, period_end, value, accn, fy, fp, form, filed, frame
        FROM sec_cf_facts
        WHERE cik IN (%s) AND taxonomy = ? AND tag = ? AND unit = ?%s%s
          AND (CAST(? AS DATE) IS NULL OR filed <= ?)""" % (
        ",".join("?" * len(ciks)), _period_sql(freq), ends_filter)
    sql = ("SELECT * FROM (%s)" % picked) if basis == "all" else _pick_sql(basis, picked)
    rows = sec_api._rows(cur, sql + " ORDER BY period_end DESC, period_start, filed DESC",
                         ciks + [tax, t, u] + params + [ceiling, ceiling])
    for r in rows:
        r["source_url"] = _source(r["cik"], r["accn"])
        for k in ("period_start", "period_end", "filed"):
            r[k] = _d(r[k])
    lab = cur.execute("SELECT label, description FROM sec_cf_tags WHERE taxonomy=? AND tag=?",
                      [tax, t]).fetchone()
    return {"company": head, "taxonomy": tax, "tag": t,
            "label": lab[0] if lab else None, "definition": lab[1] if lab else None,
            "unit": u, "units_available": [x[0] for x in units],
            "freq": freq, "basis": basis, "as_of": _d(ceiling),
            "values": rows, "count": len(rows), "calc": CALC, "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    """One entry in the shape /catalog/health lists the equity extractors in."""
    try:
        cur = _require()
    except HTTPException:
        return []
    # A pull that found the file unchanged still reached the SEC, so it counts
    # as contact: freshness is "last checked", not "last found something new".
    last_ok, last_filed = cur.execute("""
        SELECT (SELECT max(fetched_at) FROM sec_cf_pulls WHERE status IN ('ok', 'unchanged')),
               (SELECT max(last_filed) FROM sec_cf_companies)""").fetchone()
    first = cur.execute("SELECT min(first_filed) FROM sec_cf_companies").fetchone()[0]
    failed = [r[0] for r in cur.execute("""
        SELECT DISTINCT c.ticker FROM sec_cf_pulls p JOIN sec_cf_companies c USING (cik)
        WHERE p.status = 'failed' AND p.pull_id > coalesce(
            (SELECT max(pull_id) FROM sec_cf_pulls q WHERE q.cik = p.cik
               AND q.status IN ('ok', 'unchanged')), 0)
        """).fetchall()]
    days = (datetime.datetime.utcnow() - last_ok).days if last_ok else None
    stale = days is None or days > STALE_AFTER_DAYS
    return [{
        "dataset": "sec-companyfacts",
        "status": "attention" if (stale or failed) else "ok",
        "archive_read_through": _d(last_filed),
        "days_behind": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
        "archive_read_back_to": _d(first),
        "shape_flags": ["latest pull failed: %s" % t for t in failed],
    }]
