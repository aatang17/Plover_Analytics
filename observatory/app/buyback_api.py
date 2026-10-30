# -*- coding: utf-8 -*-
"""Equity product API — buyback lifecycle (自己株券買付状況報告書).

Announcement → execution → cancellation, from one monthly filing (EDINET type
220). Same DuckDB file and the same reader as the two equity APIs next door;
registered before them so its literal /equity/buyback/ paths win.

Three things are worth knowing before reading any number here:

  1. **Announced and executed are different measures.** The authorisation is a
     ceiling a board voted for; the cumulative is what the company actually
     bought. A programme can close with most of the authorisation unspent, and
     that is the question this dataset exists to answer — never present the
     authorisation as if it were spending.
  2. **The filer publishes its own progress percentage** (自己株式取得の進捗状況),
     and that figure is official, as filed. The `completion_pct` this API
     computes is derived — cumulative ÷ authorised on the yen column — and is
     returned alongside, never instead. Where the filer published no percentage
     the row says so (`unverified`): the figures are extracted but nothing
     reconciled them.
  3. **A closed window is not a cancelled programme.** `expired_unspent` means
     the acquisition period ended with the authorisation unspent. A formal
     abandonment is announced on TDnet, which this dataset does not carry.

Retirement (消却) is a separate act from buying: a company can buy shares and
hold them in treasury for years. Shares retired are shares permanently gone,
so retirements are reported on their own, never netted against purchases.

Coverage is capped by EDINET, not by us: type 220 filings are purged after
roughly a year, so nothing before 2025-08-12 is retrievable by anyone.
"""
from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import listing

from . import aliases
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/buyback", tags=["Buybacks"])

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as filed in each company's 自己株券買付状況報告書 "
             "(monthly buyback status report), EDINET. Raw filings archived "
             "with SHA-256; doc_id links to the source filing."),
}

DATES_NOTE = ("A resolution cannot post-date the acquisition window it "
              "authorises. Where a filing's own dates say it does — filers "
              "mistype the year — the row is published exactly as filed and "
              "carries dates_inconsistent. Such a filing also splits one "
              "programme into two rows here, because the resolution date is "
              "what identifies an authorisation.")

CALC = {
    "completion_pct": "100 × cumulative yen acquired ÷ yen authorised",
    "unspent_yen": ("yen authorised − cumulative yen acquired, and left empty "
                    "where the filing states one of them and not the other: an "
                    "unstated cumulative is unknown, not zero"),
    "note": ("Derived from the filed figures. The filer's own published "
             "progress percentage (進捗状況) is returned separately as "
             "progress_yen_pct and is official, as filed. The two can differ "
             "in the last decimal because filers truncate or round."),
}

LIFECYCLE_LABELS = {
    "completed": "Completed — 99.5% or more of the authorisation bought",
    "running": "Running — acquisition period still open",
    "expired_unspent": "Window closed with the authorisation unspent",
    "awaiting_final": "Window closed; the final monthly report is not in the archive yet",
    "unknown": "Not classifiable — the filing states no authorisation or no cumulative",
}

MEASURE_NOTE = ("Yen authorised is a ceiling a board voted for; yen acquired is "
                "what was bought. They are different measures and are never "
                "summed, netted or ranked against one another.")

RETIREMENT_NOTE = ("Shares retired (消却) are cancelled outright and are a "
                   "different act from buying: shares bought may sit in "
                   "treasury indefinitely. Retirements are never netted "
                   "against purchases.")

COVERAGE_NOTE = ("EDINET purges type 220 filings after about a year, so this "
                 "archive begins at 2025-08-12 and no earlier filing is "
                 "retrievable by anyone. The first and last months of the "
                 "series are partial — the filing count per month shows it.")

# One row per authorisation, already computed in the database as a view so the
# rollup can never drift from the filings behind it.
NAMED_LIFECYCLE = """
    SELECT l.*, coalesce(es.name_en, ee.name_en) AS name_en
    FROM eq_buyback_lifecycle l
    LEFT JOIN en_scode es ON es.sec_code = l.sec_code
    LEFT JOIN en_ecode ee ON ee.edinet_code = l.edinet_code
"""

SORTS = {
    "unspent_yen": "unspent_yen DESC NULLS LAST",
    "authorised_yen": "authorised_yen DESC NULLS LAST",
    "acquired_yen": "cumulative_yen DESC NULLS LAST",
    "completion_pct": "completion_pct ASC NULLS LAST",
    "resolution_date": "resolution_date DESC NULLS LAST",
    "window_end": "window_end DESC NULLS LAST",
}


def _tables_present():
    cur = _cur()
    got = {r["table_name"] for r in _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_buyback_filings','eq_buyback_programs',
                             'eq_buyback_treasury','eq_buyback_lifecycle',
                             'eq_buyback_cancellations')""")}
    return len(got) == 5


def _current(alias="f"):
    """` AND …`: the filing aliased `alias` is the newest of its report and
    that report's corrections filed by the ceiling (equity/buyback.py CURRENT).
    Empty on a database written before corrections were read."""
    have = _rows(_cur(), "SELECT 1 FROM information_schema.tables "
                         "WHERE table_name = 'eq_buyback_corrections'")
    if not have:
        return ""
    a = alias
    return (" AND NOT EXISTS (SELECT 1 FROM eq_buyback_corrections c"
            " JOIN eq_buyback_filings n ON n.doc_id = c.doc_id"
            " WHERE c.corrects_doc_id = coalesce((SELECT c2.corrects_doc_id FROM eq_buyback_corrections c2"
            " WHERE c2.doc_id = {a}.doc_id), {a}.doc_id)"
            " AND n.doc_id <> {a}.doc_id AND n.status <> 'failed'"
            " AND (n.submitted > {a}.submitted OR (n.submitted = {a}.submitted AND n.doc_id > {a}.doc_id)){c})"
            .format(a=a, c=asof.clause("submitted", "n")))


def _require():
    if not _tables_present():
        raise HTTPException(503, "buyback dataset not published on this server yet")
    return _cur()


def _label(rows):
    for r in rows:
        if "lifecycle" in r:
            r["lifecycle_label"] = LIFECYCLE_LABELS.get(r["lifecycle"], r["lifecycle"])
    return rows


@router.get("/summary")
def summary():
    """Coverage first, then the market aggregates — one row per authorisation."""
    cur = _require()
    head = _rows(cur, """
        SELECT count(*)                        AS filings,
               count(DISTINCT edinet_code)     AS companies,
               min(submitted)                  AS first_submitted,
               max(submitted)                  AS last_submitted,
               min(as_of)                      AS first_reporting_month,
               max(as_of)                      AS last_reporting_month
        FROM eq_buyback_filings""")[0]
    head.update(_rows(cur, """
        SELECT count(*)                                  AS authorisations,
               sum(authorised_yen)                       AS authorised_yen,
               sum(cumulative_yen)                       AS acquired_yen,
               sum(CASE WHEN lifecycle = 'expired_unspent'
                        THEN unspent_yen END)            AS unspent_yen_expired,
               count(*) FILTER (WHERE dates_inconsistent) AS dates_inconsistent
        FROM eq_buyback_lifecycle""")[0])
    head.update(_rows(cur, """
        SELECT count(*)                 AS retirement_months,
               count(DISTINCT edinet_code) AS companies_retiring,
               sum(cancelled_shares)    AS shares_retired,
               sum(cancelled_yen)       AS retired_yen
        FROM eq_buyback_cancellations""")[0])
    head["lifecycle"] = _label(_rows(cur, """
        SELECT lifecycle, count(*) AS authorisations,
               sum(authorised_yen) AS authorised_yen,
               sum(cumulative_yen) AS acquired_yen
        FROM eq_buyback_lifecycle GROUP BY 1 ORDER BY 2 DESC"""))
    head["extraction_status"] = {r["status"]: r["n"] for r in _rows(
        cur, "SELECT status, count(*) AS n FROM eq_buyback_filings GROUP BY 1")}
    head["gate"] = _rows(cur, """
        SELECT sum(CASE WHEN status = 'clean' THEN 1 ELSE 0 END)      AS clean,
               sum(CASE WHEN status = 'partial' THEN 1 ELSE 0 END)    AS did_not_reconcile,
               sum(CASE WHEN status = 'unverified' THEN 1 ELSE 0 END) AS not_reconcilable
        FROM eq_buyback_programs""")[0]
    head["gate_note"] = ("Each filing publishes its own progress percentage; "
                         "the extractor recomputes it from the rows it read and "
                         "requires the filer's figure back. 'Not reconcilable' "
                         "means the filer published no percentage — those "
                         "figures are extracted but unverified.")
    head["measure_note"] = MEASURE_NOTE
    head["dates_note"] = DATES_NOTE
    head["retirement_note"] = RETIREMENT_NOTE
    head["coverage_note"] = COVERAGE_NOTE
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    return head


@router.get("/monthly")
def monthly():
    """Yen bought and yen retired by reporting month — the chart feed.

    Two different acts on one time axis, deliberately: both are yen flows in the
    same month, and the whole point of the series is that they do not move
    together. They are never summed.
    """
    cur = _require()
    rows = _rows(cur, """
        WITH bought AS (
            SELECT f.as_of AS month,
                   count(DISTINCT f.doc_id) AS filings,
                   sum(p.month_yen)         AS acquired_yen,
                   sum(p.month_shares)      AS acquired_shares
            FROM eq_buyback_programs p JOIN eq_buyback_filings f USING (doc_id)
            WHERE f.as_of IS NOT NULL""" + _current("f") + """ GROUP BY 1),
        retired AS (
            SELECT t.as_of AS month, sum(t.cancelled_yen) AS retired_yen,
                   sum(t.cancelled_shares) AS retired_shares,
                   count(*) AS retirements
            FROM eq_buyback_treasury t JOIN eq_buyback_filings f USING (doc_id)
            WHERE t.as_of IS NOT NULL
              AND t.cancelled_shares > 0""" + _current("f") + """ GROUP BY 1)
        SELECT b.month, b.filings, b.acquired_yen, b.acquired_shares,
               r.retired_yen, r.retired_shares, coalesce(r.retirements, 0) AS retirements
        FROM bought b LEFT JOIN retired r USING (month)
        ORDER BY b.month""")
    # A month covered by a handful of filings is not a small month — it is an
    # edge of the archive. Say which, rather than letting the chart imply it.
    full = [r["filings"] for r in rows if r["filings"] >= 100]
    floor = (min(full) if full else 0) * 0.5
    for r in rows:
        r["partial_month"] = r["filings"] < max(floor, 20)
    return {"months": rows,
            "partial_note": ("A month whose filing count is far below the "
                             "others is an edge of the archive, not a quiet "
                             "market: partial_month marks it."),
            "measure_note": MEASURE_NOTE,
            "retirement_note": RETIREMENT_NOTE,
            "coverage_note": COVERAGE_NOTE,
            "provenance": PROVENANCE}


@router.get("/programs")
def programs(lifecycle: str = Query("", description="one of the lifecycle states"),
             q: str = Query("", description="name or securities code substring"),
             min_authorised_yen: float = Query(0, ge=0),
             sort: str = Query("unspent_yen", description="one of /programs/sorts"),
             limit: int = Query(50, ge=1, le=500)):
    """One row per authorisation: announced, executed, and what is left."""
    cur = _require()
    if sort not in SORTS:
        raise HTTPException(400, "unknown sort '%s'" % sort)
    state = (lifecycle or "").strip()
    if state and state not in LIFECYCLE_LABELS:
        raise HTTPException(400, "unknown lifecycle state '%s'" % state)
    term = (q or "").strip()
    like = "%" + term.lower() + "%"
    sql = """
        WITH x AS (SELECT 1)""" + NAME_CTES + """,
        named AS (""" + NAMED_LIFECYCLE + """)
        SELECT * FROM named
        WHERE (CAST(? AS VARCHAR) = '' OR lifecycle = CAST(? AS VARCHAR))
          AND (CAST(? AS VARCHAR) = ''
               OR lower(coalesce(name_en, '')) LIKE CAST(? AS VARCHAR)
               OR lower(coalesce(filer_name, '')) LIKE CAST(? AS VARCHAR)
               OR coalesce(sec_code, '') LIKE CAST(? AS VARCHAR))
          AND coalesce(authorised_yen, 0) >= ?
        ORDER BY """ + SORTS[sort] + """
        LIMIT ?"""
    rows = _label(_rows(cur, sql, [state, state, term, like, like, like,
                                   min_authorised_yen, limit]))
    # The table shows a page of a ranking; saying "50 authorisations" without
    # the population would read as the whole market.
    total = _rows(cur, """
        SELECT count(*) AS n FROM eq_buyback_lifecycle
        WHERE (CAST(? AS VARCHAR) = '' OR lifecycle = CAST(? AS VARCHAR))
          AND coalesce(authorised_yen, 0) >= ?""",
        [state, state, min_authorised_yen])[0]["n"]
    return {"programs": rows, "count": len(rows), "total": total, "sort": sort,
            "lifecycle": state or "all",
            "lifecycle_labels": LIFECYCLE_LABELS,
            "measure_note": MEASURE_NOTE, "dates_note": DATES_NOTE,
            "calc": CALC,
            "coverage_note": COVERAGE_NOTE, "provenance": PROVENANCE}


@router.get("/programs/sorts")
def program_sorts():
    """The sort keys /programs accepts."""
    return {"sorts": [
        {"key": "unspent_yen", "label": "Authorisation left unspent"},
        {"key": "authorised_yen", "label": "Yen authorised"},
        {"key": "acquired_yen", "label": "Yen acquired"},
        {"key": "completion_pct", "label": "Completion, lowest first"},
        {"key": "resolution_date", "label": "Most recently authorised"},
        {"key": "window_end", "label": "Most recent window end"},
    ]}


@router.get("/retirements")
def retirements(limit: int = Query(50, ge=1, le=500)):
    """Filing-months in which shares were cancelled outright."""
    cur = _require()
    rows = _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT c.*, coalesce(es.name_en, ee.name_en) AS name_en,
               100.0 * c.cancelled_shares
                     / nullif(c.shares_outstanding + c.cancelled_shares, 0) AS pct_of_pre_shares
        FROM eq_buyback_cancellations c
        LEFT JOIN en_scode es ON es.sec_code = c.sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = c.edinet_code
        ORDER BY c.cancelled_yen DESC NULLS LAST
        LIMIT ?""", [limit])
    total = _rows(cur, "SELECT count(*) AS n FROM eq_buyback_cancellations")[0]["n"]
    return {"retirements": rows, "count": len(rows), "total": total,
            "retirement_note": RETIREMENT_NOTE,
            "calc": {"pct_of_pre_shares":
                     "100 × shares retired ÷ (shares outstanding at month end "
                     "+ shares retired). Derived; the share counts are as filed."},
            "coverage_note": COVERAGE_NOTE, "provenance": PROVENANCE}


@router.get("/companies")
@listing.marked
def companies(q: str = Query("", description="name or code substring")):
    """Companies with at least one buyback filing — the search feed."""
    cur = _require()
    term = (q or "").strip()
    like = "%" + term.lower() + "%"
    alias_sql, alias_params = aliases.clause(cur, "f.sec_code", term)
    return {"companies": _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT f.sec_code, f.edinet_code,
               max(f.filer_name) AS filer_name,
               max(coalesce(es.name_en, ee.name_en)) AS name_en,
               count(*) AS filings, max(f.as_of) AS last_reporting_month
        FROM eq_buyback_filings f
        LEFT JOIN en_scode es ON es.sec_code = f.sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.edinet_code
        WHERE (TRUE""" + _current("f") + """) AND (CAST(? AS VARCHAR) = ''
           OR lower(coalesce(es.name_en, ee.name_en, '')) LIKE CAST(? AS VARCHAR)
           OR lower(f.filer_name) LIKE CAST(? AS VARCHAR)
           OR coalesce(f.sec_code, '') LIKE CAST(? AS VARCHAR)"""
        + alias_sql + """)
        GROUP BY 1, 2 ORDER BY count(*) DESC, f.sec_code
        LIMIT 40""", [term, like, like, like] + alias_params)}


@router.get("/company/{sec_code}")
def company(sec_code: str):
    """One company: its authorisations, its month-by-month buying, its retirements."""
    cur = _require()
    code = (sec_code or "").strip()
    ident = _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT f.sec_code, f.edinet_code, max(f.filer_name) AS filer_name,
               max(coalesce(es.name_en, ee.name_en)) AS name_en,
               count(*) AS filings, min(f.as_of) AS first_reporting_month,
               max(f.as_of) AS last_reporting_month
        FROM eq_buyback_filings f
        LEFT JOIN en_scode es ON es.sec_code = f.sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.edinet_code
        WHERE f.sec_code = ?""" + asof.clause("submitted", "f") + _current("f") + """ GROUP BY 1, 2""", [code])
    if not ident:
        raise HTTPException(404, "no buyback filings for securities code '%s'" % code)
    head = ident[0]
    # eq_buyback_lifecycle is a view that rolls every monthly filing of an
    # authorisation into one row — cumulative spend, completion, unspent, the
    # lifecycle state. Those totals are computed over ALL filings, so under a
    # ceiling they would describe a future this response is not allowed to
    # know. Rebuilding the rollup here would duplicate the view's
    # classification and could drift from it, which is the one thing the view
    # exists to prevent. So it is withheld, and says so; `months` below is the
    # filings themselves, each carrying the cumulative figure it stated at the
    # time, which is the point-in-time answer.
    if asof.current():
        head["programs"] = []
        head["programs_unavailable"] = (
            "The programme rollup aggregates every monthly filing of an "
            "authorisation, including ones filed after as_of, so it cannot be "
            "rebuilt as of a date without restating it. Read `months`: each "
            "row is one filing with the cumulative figure it reported then.")
    else:
        head["programs"] = _label(_rows(cur, """
            SELECT * FROM eq_buyback_lifecycle WHERE sec_code = ?
            ORDER BY resolution_date DESC NULLS LAST""", [code]))
    head["months"] = _rows(cur, """
        SELECT f.as_of AS month, f.doc_id, f.submitted, f.status, f.detail,
               p.resolution_type, p.resolution_date, p.window_start, p.window_end,
               p.authorised_shares, p.authorised_yen,
               p.month_shares, p.month_yen,
               p.cumulative_shares, p.cumulative_yen,
               p.progress_shares_pct, p.progress_yen_pct, p.daily_rows,
               p.status AS program_status
        FROM eq_buyback_filings f LEFT JOIN eq_buyback_programs p USING (doc_id)
        WHERE f.sec_code = ?""" + asof.clause("submitted", "f") + _current("f") + """
        ORDER BY f.as_of DESC, p.resolution_date DESC""", [code])
    head["treasury"] = _rows(cur, """
        SELECT as_of AS month, doc_id, cancelled_shares, cancelled_yen,
               shares_outstanding, treasury_shares,
               100.0 * treasury_shares / nullif(shares_outstanding, 0) AS treasury_pct,
               status
        FROM eq_buyback_treasury WHERE doc_id IN (
            SELECT f.doc_id FROM eq_buyback_filings f WHERE f.sec_code = ?"""
            + asof.clause("submitted", "f") + _current("f") + """)
        ORDER BY as_of DESC""", [code])
    head["measure_note"] = MEASURE_NOTE
    head["dates_note"] = DATES_NOTE
    head["retirement_note"] = RETIREMENT_NOTE
    head["coverage_note"] = COVERAGE_NOTE
    head["lifecycle_labels"] = LIFECYCLE_LABELS
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    return head


# The dataset's card (app/registry.py). Authorised and acquired are different
# measures (MEASURE_NOTE); the filer's own progress figure is official and the
# platform's completion figure is derived, returned beside it.
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "buybacks",
    "section": "capital-returns",
    "name": {"en": "Buybacks", "ja": "自己株券買付状況報告書"},
    "shape": "events",
    "summary": ("Share buyback programmes from announcement to execution to "
                "cancellation — what a board authorised, what was actually "
                "bought month by month, what is left unspent, and shares "
                "retired — from the monthly buyback status reports."),
    "source": dict(_EDINET_SOURCE,
                   document="自己株券買付状況報告書 (monthly buyback status report, "
                            "EDINET type 220)",
                   credit="Source: buyback status reports filed on EDINET "
                          "(Financial Services Agency of Japan)."),
    "keys": ["sec_code", "resolution_date"],
    "frequency": "per-event",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "2025-08 (archive start; EDINET purges these filings after a year)",
        "stale_after_days": None,
    },
    "measures": [
        {"id": "authorised_yen", "label": "Yen authorised by the board", "unit": "JPY",
         "trust": "official"},
        {"id": "authorised_shares", "label": "Shares authorised", "unit": "shares",
         "trust": "official"},
        {"id": "cumulative_yen", "label": "Cumulative yen acquired", "unit": "JPY",
         "trust": "official"},
        {"id": "cumulative_shares", "label": "Cumulative shares acquired", "unit": "shares",
         "trust": "official"},
        {"id": "progress_yen_pct", "label": "Progress, as published by the filer (進捗状況)",
         "unit": "%", "trust": "official"},
        {"id": "shares_retired", "label": "Shares retired (消却)", "unit": "shares",
         "trust": "official"},
        {"id": "completion_pct", "label": "Completion, computed", "unit": "%",
         "trust": "derived", "calc": CALC["completion_pct"]},
        {"id": "unspent_yen", "label": "Authorisation left unspent", "unit": "JPY",
         "trust": "derived", "calc": CALC["unspent_yen"]},
        {"id": "lifecycle", "label": "Programme state", "unit": "category",
         "trust": "derived",
         "calc": "; ".join("%s: %s" % (k, v) for k, v in sorted(LIFECYCLE_LABELS.items()))},
        {"id": "pct_of_pre_shares", "label": "Shares retired as a share of the pre-retirement count",
         "unit": "%", "trust": "derived",
         "calc": ("100 × shares retired ÷ (shares outstanding at month end + shares "
                  "retired). Derived; the share counts are as filed.")},
        {"id": "dates_inconsistent", "label": "Filing's own dates contradict each other",
         "unit": "boolean", "trust": "derived",
         "calc": "raised when the resolution date post-dates the acquisition window it authorises"},
    ],
    "endpoints": {
        "company": "/api/v1/equity/buyback/company/{sec_code}",
        "search": "/api/v1/equity/buyback/companies",
        "summary": "/api/v1/equity/buyback/summary",
        "screen": "/api/v1/equity/buyback/programs",
        "screen_sorts": "/api/v1/equity/buyback/programs/sorts",
        "monthly": "/api/v1/equity/buyback/monthly",
        "retirements": "/api/v1/equity/buyback/retirements",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [{"id": k, "title": "Programmes ranked by %s" % k.replace("_", " ")}
                for k in sorted(SORTS)],
    "cite": "/buyback.html?c={sec_code}",
    "page": "/buyback.html",
    "notes": [MEASURE_NOTE, RETIREMENT_NOTE, COVERAGE_NOTE, CALC["note"]],
}
