# -*- coding: utf-8 -*-
u"""Equity product API — 5% filings (大量保有報告書・変更報告書).

The tape: who is accumulating, in whom, how fast, and — filed, not inferred —
whether they say they may make important proposals to the board. Served from
the same DuckDB file and reader as the rest of the equity product.

WHAT A CONSUMER MUST NOT ASSUME, carried in the data rather than in prose:

  1. A REPORT IS AN EVENT, NOT A POSITION. Every filing is a snapshot at its
     own trigger date (requirement_date, 提出義務発生日). "Who holds 5% of X
     today" is the LATEST report per (issuer, filing group) — which is what
     /company returns — and a group that has fallen below 5% files a final
     report and then stops, so a stale-looking row may be an exit. `is_current`
     says whether the latest report still shows 5% or more.
  2. THE GROUP IS THE UNIT. A report covers a filer and its joint holders, and
     the group ratio is not the sum of its members: the form deducts claims
     between joint holders, and a member that has just left is still described
     in the document with last-report figures only. Members that count toward
     the group carry in_group_total.
  3. 重要提案行為 IS NOT ASKED ON EVERY FORM. The special-reporting form
     (EDINET jplvh030000 — the exemption for institutions that undertake to
     make no such proposals) carries no such field, so important_proposal is
     null on it. The general form asks it on initial, change and amendment
     reports alike. `proposal_asked` says which case a null is: false means
     the form never put the question, true means it did and the filer left it
     blank. Null never means "no". The answer is free text, read into
     proposal_stance (will_or_may / none_planned / unclear).
  4. A CHANGE REPORT OFTEN RESTATES THE ORIGINAL OBLIGATION DATE. 提出義務
     発生日 on a 変更報告書 is frequently the date the holder first crossed 5%,
     not the date of the change reported — so the gap to filed_date is not a
     lateness measure. A trigger date before the 5% rule began (1 December
     1990) or after the report was filed is a typo; it is published as filed,
     marked by date_quality, and never used to order or bound anything.
  6. A JOINT FILING IS ONE REPORT WITH SEVERAL HOLDERS. A holder's book
     (/holder) lists each report once per line the holder has in it, with the
     filing group's position in ratio_pct and shares_held — the same fields
     as everywhere else — and the holder's own line in holder_ratio_pct and
     holder_shares_held. An investment manager whose fund holds the shares
     can file its own line as 0; that is the filing, not a gap.
  5. THE RATIO IS THE STATUTORY ONE. Its denominator is 発行済株式等総数 plus
     the holder's own potential shares, so it does not equal shares_held /
     shares_outstanding and the difference is not an error.
"""
import csv
import io
import json
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import asof
from . import jp_enums
from . import plausibility

from . import aliases
from .equity_api import NAMES_NOTE, NAME_CTES, PROVENANCE, _cur, _rows
from .filer_labels import (GROUP_NOTE, TYPE_EN, TYPE_NOTE, group_of, group_size,
                           type_of)

router = APIRouter(prefix="/api/v1/equity/stakes", tags=["5% filings"])

THRESHOLD_PCT = 5.0

CALC = {
    "ratio_change_pp": "ratio_pct − prior_ratio_pct, both as filed (percentage points)",
    "is_current": "the group's latest report shows ratio_pct >= 5",
    "activist_filings": "filings where any holder states an act under 重要提案行為等",
    "filed_date": ("the date EDINET received the document, from its own "
                   "submission record — not the date printed on the cover page, "
                   "which the filer types and occasionally gets wrong "
                   "(cover_date keeps that one)"),
    "days_to_file": ("filed_date − requirement_date, in days. NOT a lateness "
                     "measure: a change report often restates the date the "
                     "holder first crossed 5%, years earlier"),
    "proposal_stance": ("the filed 重要提案行為 answer read as will_or_may (states "
                        "it will, may or has made important proposals), "
                        "none_planned (states none, in any wording) or unclear "
                        "(text that does not answer the question, such as a "
                        "lock-up clause typed into the box); a filing's stance "
                        "is will_or_may if any holder's is"),
    "purpose_mentions_proposal": ("the 保有目的 (purpose) reserves important "
                                  "proposals in a clause that does not deny a "
                                  "plan. Large asset managers write this into "
                                  "nearly every report as boilerplate, so it "
                                  "is not a campaign signal on its own"),
    "date_quality": ("ok, or after_filing (trigger date later than the date "
                     "EDINET received the report), before_5pct_rule (earlier "
                     "than 1 December 1990, when the rule began) or missing"),
    "unique_proposal_docs": ("distinct reports with important_proposal true; "
                             "proposal_reports counts holder lines, so a joint "
                             "filing counts once per co-filer there"),
}

# The 5% rule (大量保有報告制度) took effect on 1 December 1990; no obligation
# can predate it, and none can arise after the report that discloses it.
RULE_START = "1990-12-01"


def trigger(a="f"):
    u"""The trigger date where it is possible, else NULL — for ordering and
    for bounds. The filed value itself is always published unchanged."""
    return ("CASE WHEN {a}.requirement_date BETWEEN DATE '%s' AND {a}.filed_date "
            "THEN {a}.requirement_date END" % RULE_START).format(a=a)


def date_quality(a="f"):
    return ("CASE WHEN {a}.requirement_date IS NULL THEN 'missing' "
            "WHEN {a}.requirement_date > {a}.filed_date THEN 'after_filing' "
            "WHEN {a}.requirement_date < DATE '%s' THEN 'before_5pct_rule' "
            "ELSE 'ok' END" % RULE_START).format(a=a)


# The newest order of events: possible trigger date, else the filed date.
def event_date(a="f"):
    return "coalesce(%s, %s.filed_date)" % (trigger(a), a)


# Columns lvh-2 added. Until the nightly extraction has run once against a
# database written by lvh-1 they are absent, and the API serves NULL in their
# place rather than failing.
OPTIONAL = {"eq_lvh_filings": ("proposal_stance", "purpose_mentions_proposal"),
            "eq_lvh_holders": ("proposal_stance", "purpose_mentions_proposal")}


def _opt(cur):
    u"""{'f.proposal_stance': 'f.proposal_stance' or 'NULL', ...}."""
    have = {(r[0], r[1]) for r in cur.execute(
        "SELECT table_name, column_name FROM duckdb_columns() "
        "WHERE table_name IN ('eq_lvh_filings', 'eq_lvh_holders')").fetchall()}
    out = {}
    for table, cols in OPTIONAL.items():
        alias = "f" if table == "eq_lvh_filings" else "h"
        for c in cols:
            out["%s.%s" % (alias, c)] = ("%s.%s" % (alias, c)) if (table, c) in have else "NULL"
    return out


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_param(value, name):
    v = (value or "").strip()
    if v and not DATE_RE.match(v):
        raise HTTPException(400, "%s must be YYYY-MM-DD" % name)
    return v

TAPE_NOTE = (
    "The tape is ordered by the date each report was FILED, which is when the "
    "market could read it. The trigger date beside it is the filing's own "
    "提出義務発生日 and is shown as filed — a change report often restates the "
    "date the holder first crossed 5%, and a handful of filers mistype it "
    "outright, so ordering on it would put a typo at the top of the page.")

EVENT_NOTE = (
    "Each row is one report as filed on its own trigger date, not a running "
    "position. A holding group that drops below 5% files once more and then "
    "stops filing, so the absence of recent reports is not evidence of a "
    "position.")

GROUP_NOTE = (
    "Positions are per filing GROUP (a filer plus its joint holders). The "
    "group ratio is the filing's own, not our sum of the members: the form "
    "deducts claims between joint holders, and a member that has left the "
    "group is still described in the report with last-report figures only.")

PROPOSAL_NOTE = (
    "important_proposal is the filed answer to 重要提案行為等 — the acts under "
    "Article 27-26 that separate a campaign from a passive stake — read as "
    "true when the holder states it will, may or has made such proposals and "
    "false when it states none. The general form asks it on initial, change "
    "and amendment reports alike; the special-reporting form used by "
    "institutions that undertake to make no proposals carries no such field, "
    "and proposal_asked is false there. An answer that does not address the "
    "question (a lock-up clause typed into the box, say) is null with "
    "proposal_stance 'unclear'. A null answer never means 'no'.")

RATIO_NOTE = (
    "ratio_pct is the statutory 株券等保有割合. Its denominator is 発行済株式等"
    "総数 plus the holder's own potential shares and its numerator excludes "
    "underwritten shares, so it does not equal shares_held / shares_outstanding; "
    "both are published as filed.")


def _require():
    cur = _cur()
    try:
        cur.execute("SELECT 1 FROM eq_lvh_filings LIMIT 1")
    except Exception:                                            # noqa: BLE001
        raise HTTPException(503, "5% filings dataset not published yet")
    return cur


def _notes(head):
    head["event_note"] = EVENT_NOTE
    head["tape_note"] = TAPE_NOTE
    head["group_note"] = GROUP_NOTE
    head["proposal_note"] = PROPOSAL_NOTE
    head["ratio_note"] = RATIO_NOTE
    head["names_note"] = NAMES_NOTE
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    return head


# The latest report per (issuer, filing group). A correction (訂正報告書,
# EDINET type 360) supersedes on the surface while both rows stay queryable —
# the same rule the rest of the platform applies to a revised vintage.
_LATEST_STAKE = """
    WITH ranked AS (
        SELECT f.*, coalesce(f.issuer_sec_code, f.issuer_edinet_code,
                             f.issuer_name_raw) AS issuer_key,
               row_number() OVER (
                   PARTITION BY coalesce(f.issuer_sec_code, f.issuer_edinet_code,
                                         f.issuer_name_raw),
                                coalesce(f.filer_edinet_code, f.filer_name)
                   ORDER BY /*EVENT*/ DESC,
                            f.filed_date DESC,
                            CASE WHEN f.doc_type = '360' THEN 1 ELSE 0 END DESC,
                            f.doc_id DESC) AS rn
        FROM eq_lvh_filings f
        WHERE f.status IN ('clean','partial')/*ASOF*/
    ),
    current_stakes AS (SELECT * FROM ranked WHERE rn = 1)
"""


def latest_stake():
    """Latest Stake, with the point-in-time ceiling in force (app/asof.py)."""
    return (_LATEST_STAKE.replace("/*ASOF*/", asof.clause("filed_date", "f"))
            .replace("/*EVENT*/", event_date("f")))


_FILING_COLS = """
        f.doc_id, f.doc_type, f.report_type, f.change_no, f.is_special_form,
        f.filer_edinet_code, f.filer_name, f.issuer_name_raw, f.issuer_sec_code,
        f.issuer_edinet_code, f.listing, f.exchange, f.requirement_date,
        f.filed_date, f.cover_date, f.holders_declared, f.holders_rows, f.shares_held,
        f.shares_outstanding, f.ratio_pct, f.prior_ratio_pct, f.ratio_change_pp,
        f.important_proposal, f.proposal_asked, {f.proposal_stance} AS proposal_stance,
        {f.purpose_mentions_proposal} AS purpose_mentions_proposal,
        f.filing_reason_ja, f.purpose_ja,
        f.own_funds_yen, f.borrowings_yen, f.funding_implausible, f.status,
        f.detail, date_diff('day', f.requirement_date, f.filed_date) AS days_to_file,
        {DQ} AS date_quality
"""


def _fill(sql, opt):
    u"""Put each optional column, or NULL, where {alias.column} stands."""
    for key, expr in opt.items():
        sql = sql.replace("{%s}" % key, expr)
    return sql


def filing_cols(opt):
    return _fill(_FILING_COLS.replace("{DQ}", date_quality("f")), opt)


@router.get("/summary")
def summary():
    u"""Coverage first, then what the tape shows."""
    cur = _require()
    head = _rows(cur, """
        SELECT count(*) AS filings,
               count(DISTINCT issuer_sec_code) AS issuers,
               count(DISTINCT filer_edinet_code) AS filers,
               min(filed_date) AS earliest_filed, max(filed_date) AS latest_filed,
               min(/*T*/) AS earliest_trigger,
               max(/*T*/) AS latest_trigger,
               sum(CASE WHEN /*DQ*/ IN ('after_filing', 'before_5pct_rule')
                        THEN 1 ELSE 0 END) AS trigger_dates_implausible,
               sum(CASE WHEN important_proposal THEN 1 ELSE 0 END) AS activist_filings,
               sum(CASE WHEN is_special_form THEN 1 ELSE 0 END) AS special_form_filings,
               median(date_diff('day', /*T*/, filed_date)) AS median_days_to_file
        FROM eq_lvh_filings f WHERE status IN ('clean','partial')""".replace(
            "/*T*/", trigger("f")).replace("/*DQ*/", date_quality("f"))
        + asof.clause("filed_date"))[0]
    opt = _opt(cur)
    if opt["f.proposal_stance"] != "NULL":
        head["by_proposal_stance"] = {
            (r["s"] or "not_stated"): r["n"] for r in _rows(cur, """
                SELECT proposal_stance AS s, count(*) AS n FROM eq_lvh_filings
                WHERE status IN ('clean','partial')""" + asof.clause("filed_date")
                + " GROUP BY 1")}
    head["trigger_note"] = (
        "earliest_trigger and latest_trigger leave out trigger dates that cannot "
        "be right (before 1 December 1990, when the 5% rule began, or after the "
        "report was filed); trigger_dates_implausible counts them, and each "
        "report carries date_quality.")
    head["by_report_type"] = _rows(cur, """
        SELECT report_type, count(*) AS n FROM eq_lvh_filings
        WHERE status IN ('clean','partial')""" + asof.clause("filed_date") + """ GROUP BY 1 ORDER BY 2 DESC""")
    head["current_positions"] = _rows(cur, latest_stake() + """
        SELECT count(*) AS groups,
               sum(CASE WHEN ratio_pct >= ? THEN 1 ELSE 0 END) AS at_or_above_5pct,
               count(DISTINCT issuer_key) AS issuers
        FROM current_stakes""", [THRESHOLD_PCT])[0]
    status = _rows(cur, "SELECT status, count(*) AS n FROM eq_lvh_filings GROUP BY 1")
    head["extraction_status"] = {r["status"]: r["n"] for r in status}
    head["coverage_note"] = (
        "Every archived 大量保有報告書 and 訂正報告書 is parsed. The archive "
        "reaches back only as far as the capture does — EDINET's own list API "
        "reaches about five years — so an accumulation that began earlier is "
        "visible from its first captured report onward, not from its start.")
    return _notes(head)


def _tape_where(activist, min_ratio, min_change, report_type, since, until):
    u"""(conditions, params) shared by the tape and the bulk export."""
    where = ["f.status IN ('clean','partial')"]
    params = []
    if (activist or "").strip().lower() == "true":
        where.append("f.important_proposal")
    if min_ratio:
        where.append("f.ratio_pct >= ?")
        params.append(min_ratio)
    if min_change:
        where.append("abs(f.ratio_change_pp) >= ?")
        params.append(min_change)
    if (report_type or "").strip():
        where.append("f.report_type = ?")
        params.append(report_type.strip())
    if since:
        where.append("f.filed_date >= CAST(? AS DATE)")
        params.append(since)
    if until:
        where.append("f.filed_date <= CAST(? AS DATE)")
        params.append(until)
    return where, params


@router.get("/recent")
def recent(limit: int = Query(50, ge=1, le=1000),
           offset: int = Query(0, ge=0),
           activist: str = Query("", description="'true' for filings stating an "
                                                 "important-proposal act"),
           min_ratio: float = Query(0.0, ge=0, le=100),
           min_change: float = Query(0.0, ge=0, le=100,
                                     description="minimum absolute change in "
                                                 "percentage points"),
           report_type: str = Query("", description="initial | change | amendment"),
           since: str = Query("", description="filed on or after, YYYY-MM-DD"),
           until: str = Query("", description="filed on or before, YYYY-MM-DD")):
    u"""The tape, newest filing first. Page with offset; `total` is the number
    of filings matching the filters, so a reader knows when it has them all.
    For the whole archive at once use /export."""
    cur = _require()
    since, until = _date_param(since, "since"), _date_param(until, "until")
    where, params = _tape_where(activist, min_ratio, min_change, report_type,
                                since, until)
    where_sql = " AND ".join(where) + asof.clause("filed_date", "f")
    total = cur.execute("SELECT count(*) FROM eq_lvh_filings f WHERE " + where_sql,
                        params).fetchone()[0]
    rows = _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT """ + filing_cols(_opt(cur)) + """,
               coalesce(es.name_en, ee.name_en) AS issuer_name_en
        FROM eq_lvh_filings f
        LEFT JOIN en_scode es ON es.sec_code = f.issuer_sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.issuer_edinet_code
        WHERE """ + where_sql + """
        ORDER BY f.filed_date DESC, f.requirement_date DESC, f.doc_id DESC
        LIMIT ? OFFSET ?""", params + [limit, offset])
    plausibility.lvh_rows(rows)
    jp_enums.annotate_listing(rows)
    return _notes({"filings": rows, "total": total, "offset": offset,
                   "returned": len(rows),
                   "next_offset": (offset + len(rows)) if offset + len(rows) < total else None,
                   "filters": {
                       "activist": (activist or "").strip().lower() == "true",
                       "min_ratio": min_ratio, "min_change": min_change,
                       "report_type": report_type.strip() or None,
                       "since": since or None, "until": until or None}})


@router.get("/companies")
def companies(q: str = Query("", description="issuer name or code substring")):
    u"""Search feed: issuers that at least one 5% report names."""
    cur = _require()
    like = "%" + q.strip() + "%"
    alias_sql, alias_params = aliases.clause(cur, "f.issuer_sec_code", q)
    return {"companies": _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT f.issuer_sec_code AS sec_code,
               any_value(f.issuer_name_raw) AS name,
               any_value(coalesce(es.name_en, ee.name_en)) AS name_en,
               count(*) AS reports,
               count(DISTINCT coalesce(f.filer_edinet_code, f.filer_name)) AS groups,
               max(""" + event_date("f") + """) AS latest_report,
               sum(CASE WHEN f.important_proposal THEN 1 ELSE 0 END) AS activist_reports
        FROM eq_lvh_filings f
        LEFT JOIN en_scode es ON es.sec_code = f.issuer_sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.issuer_edinet_code
        WHERE f.status IN ('clean','partial')""" + asof.clause("filed_date", "f") + """ AND f.issuer_sec_code IS NOT NULL
          AND (f.issuer_sec_code LIKE ? OR f.issuer_name_raw LIKE ?
               OR lower(coalesce(es.name_en, ee.name_en, '')) LIKE lower(?)"""
        + alias_sql + """)
        GROUP BY 1 ORDER BY reports DESC LIMIT 25""",
        [like, like, like] + alias_params),
        "names_note": NAMES_NOTE}


@router.get("/company/{sec_code}")
def company(sec_code: str,
            history: int = Query(50, ge=1, le=500,
                                 description="how many past reports to return")):
    u"""Who has filed 5% on this company — the view no single filing shows."""
    cur = _require()
    code = (sec_code or "").strip()
    current = _rows(cur, latest_stake() + NAME_CTES + """
        SELECT f.*, (f.ratio_pct >= ?) AS is_current,
               """ + date_quality("f") + """ AS date_quality,
               coalesce(es.name_en, ee.name_en) AS issuer_name_en
        FROM current_stakes f
        LEFT JOIN en_scode es ON es.sec_code = f.issuer_sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.issuer_edinet_code
        WHERE f.issuer_sec_code = ? OR f.issuer_edinet_code = ?
        ORDER BY f.ratio_pct DESC NULLS LAST""", [THRESHOLD_PCT, code, code])
    if not current:
        raise HTTPException(404, "no 5%% filings archived for %s" % code)
    for row in current:
        row.pop("rn", None)
        row.pop("issuer_key", None)
        row["holders"] = _rows(cur, """
            SELECT holder_no, name_raw, name_en, holder_type_ja, is_individual,
                   holder_edinet_code, holder_sec_code, match_status, shares_held,
                   ratio_pct, prior_ratio_pct, in_group_total, purpose_ja,
                   important_proposal, own_funds_yen, borrowings_yen
            FROM eq_lvh_holders WHERE doc_id = ? ORDER BY holder_no""",
            [row["doc_id"]])
    head = {"issuer_sec_code": code,
            "issuer_name": current[0]["issuer_name_raw"],
            "issuer_name_en": current[0].get("issuer_name_en"),
            "groups": current,
            "groups_at_or_above_5pct": sum(1 for r in current if r.get("is_current")),
            "combined_current_pct": round(sum(r["ratio_pct"] or 0 for r in current
                                              if r.get("is_current")), 2) or None}
    head["reports"] = jp_enums.annotate_listing(plausibility.lvh_rows(_rows(cur, """
        SELECT """ + filing_cols(_opt(cur)) + """
        FROM eq_lvh_filings f
        WHERE (f.issuer_sec_code = ? OR f.issuer_edinet_code = ?)
          AND f.status IN ('clean','partial')""" + asof.clause("filed_date", "f") + """
        ORDER BY """ + event_date("f") + """ DESC, f.doc_id DESC
        LIMIT ?""", [code, code, history])))
    head["combined_note"] = (
        "combined_current_pct adds the current groups' filed ratios. Groups "
        "file independently and their statutory denominators differ slightly, "
        "so it is an indication of how much of the register is disclosed above "
        "5%, not an exact share of the company.")
    return _notes(head)


# One holder's lines across every report it appears in. The filing group's
# position goes out under the same names every other endpoint uses
# (ratio_pct, shares_held); the holder's own line is holder_*. A joint filing
# appears once per line this holder has in it, never once per co-filer.
_BOOK = """
    book AS (
        SELECT h.name_raw, h.name_en, h.holder_edinet_code,
               coalesce(h.holder_edinet_code, h.name_key) AS holder_key,
               h.business_ja, h.holder_type_ja, h.is_individual, h.holder_no,
               (h.holder_no = 1) AS is_primary_filer,
               f.filer_edinet_code, f.filer_name, f.holders_rows AS joint_holders,
               f.issuer_sec_code, f.issuer_name_raw,
               coalesce(es.name_en, ee.name_en) AS issuer_name_en,
               f.report_type, f.is_special_form, f.requirement_date, f.filed_date,
               {DQ} AS date_quality,
               f.shares_held, f.ratio_pct, f.prior_ratio_pct, f.ratio_change_pp,
               h.shares_held AS holder_shares_held, h.ratio_pct AS holder_ratio_pct,
               h.prior_ratio_pct AS holder_prior_ratio_pct, h.in_group_total,
               f.important_proposal, {f.proposal_stance} AS proposal_stance,
               h.important_proposal AS holder_important_proposal,
               {h.proposal_stance} AS holder_proposal_stance,
               {h.purpose_mentions_proposal} AS purpose_mentions_proposal,
               h.purpose_ja, f.doc_id, f.status,
               {EVENT} AS event_date
        FROM eq_lvh_holders h JOIN eq_lvh_filings f USING (doc_id)
        LEFT JOIN en_scode es ON es.sec_code = f.issuer_sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.issuer_edinet_code
        WHERE coalesce(h.holder_edinet_code, h.name_key) = ?
          AND f.status IN ('clean','partial'){ASOF})
"""


@router.get("/holder/{key}", openapi_extra={"x-example": "/api/v1/equity/stakes/holder/E12444"})
def holder(key: str,
           limit: int = Query(200, ge=1, le=5000),
           offset: int = Query(0, ge=0, description="skip this many reports; "
                                                    "page through with next_offset")):
    u"""One holder's book: every issuer it has filed on, latest position first.

    `key` is the holder's EDINET code, or for a holder with none, the
    holder_key that /holders lists. Joint holders are coded in the filing even
    when they never file themselves, so the code identifies a holder across
    every report it appears in — including the ones EDINET's public filer
    registry has no row for (match_status = code_not_in_registry).

    `reports` is one page, newest first; `reports_total` is the whole book and
    `truncated` says whether this page stops short of it. `current` is the
    latest report per issuer over the whole book, whatever the page.
    """
    cur = _require()
    k = (key or "").strip()
    book = "WITH x AS (SELECT 1)" + NAME_CTES + "," + _fill(
        _BOOK.replace("{DQ}", date_quality("f")).replace("{EVENT}", event_date("f"))
        .replace("{ASOF}", asof.clause("filed_date", "f")), _opt(cur))
    # Counted over every report this holder has filed, not the page returned:
    # an active filer has more reports than `limit`, and reporting the page
    # size as its number of issuers would understate its book.
    totals = _rows(cur, book + """
        SELECT count(*) AS reports, count(DISTINCT doc_id) AS documents,
               count(DISTINCT coalesce(issuer_sec_code, issuer_name_raw)) AS issuers
        FROM book""", [k])[0]
    if not totals["reports"]:
        raise HTTPException(404, "no 5%% filings archived for holder %s" % k)
    rows = _rows(cur, book + """
        SELECT * EXCLUDE (event_date) FROM book
        ORDER BY event_date DESC, doc_id DESC, holder_no
        LIMIT ? OFFSET ?""", [k, limit, offset])
    current = _rows(cur, book + """
        SELECT * EXCLUDE (event_date, rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY coalesce(issuer_sec_code, issuer_name_raw)
                ORDER BY event_date DESC, filed_date DESC, doc_id DESC, holder_no) AS rn
            FROM book)
        WHERE rn = 1
        ORDER BY ratio_pct DESC NULLS LAST""", [k])
    first = current[0]
    profile = _label({"holder_edinet_code": first["holder_edinet_code"],
                      "holder_key": first["holder_key"],
                      "name_ja": first["name_raw"], "name_en": first["name_en"],
                      "business_ja": first.get("business_ja"),
                      "holder_type_ja": first.get("holder_type_ja"),
                      "is_individual": first.get("is_individual")})
    end = offset + len(rows)
    return _notes({"holder_edinet_code": first["holder_edinet_code"],
                   "holder_key": first["holder_key"],
                   "name": first["name_raw"], "name_en": first["name_en"],
                   "filer_type": profile["filer_type"],
                   "filer_type_en": profile["filer_type_en"],
                   "filer_type_evidence": profile["filer_type_evidence"],
                   "business_ja": first.get("business_ja"),
                   "group": profile["group"],
                   "group_entities": profile["group_entities"],
                   "type_note": TYPE_NOTE, "group_note": GROUP_NOTE,
                   "issuers": totals["issuers"], "reports_total": totals["reports"],
                   "documents_total": totals["documents"],
                   "offset": offset, "reports_returned": len(rows),
                   "truncated": end < totals["reports"],
                   "next_offset": end if end < totals["reports"] else None,
                   "book_note": BOOK_NOTE,
                   "current": current,
                   "reports": rows})


BOOK_NOTE = (
    "ratio_pct, shares_held and prior_ratio_pct are the filing group's "
    "position as filed, the same fields every other endpoint returns. "
    "holder_ratio_pct and holder_shares_held are this holder's own line in the "
    "filing — null where the filing gives it no current figure, and 0 where it "
    "files 0 (an investment manager whose fund holds the shares). "
    "is_primary_filer marks the first holder named, which is the filer. "
    "reports_total counts this holder's lines; documents_total counts distinct "
    "reports.")


def _label(row):
    u"""Attach the two derived labels to one filing-entity row."""
    kind, evidence = type_of(row.get("business_ja"), row.get("is_individual"),
                             row.get("holder_type_ja"),
                             row.get("name_ja") or row.get("name_raw"))
    row["filer_type"] = kind
    row["filer_type_en"] = TYPE_EN[kind]
    row["filer_type_evidence"] = evidence
    row["group"] = group_of(row.get("holder_edinet_code") or row.get("holder_key"),
                            row.get("name_en") or row.get("name_ja"))
    row["group_entities"] = group_size(row["group"]) or 1
    return row


# One row per FILING ENTITY, before any grouping. Everything the labels need
# travels with it: what the entity filed as its business, and whether the
# filing called it a person.
ENTITY_ROLLUP = """
    SELECT coalesce(h.holder_edinet_code, h.name_key) AS holder_key,
           any_value(h.holder_edinet_code) AS holder_edinet_code,
           min(h.name_raw) AS name_ja, min(h.name_en) AS name_en,
           any_value(h.holder_type_ja) AS holder_type_ja,
           any_value(h.is_individual) AS is_individual,
           max(h.business_ja) AS business_ja,
           count(*) AS reports,
           count(DISTINCT coalesce(f.issuer_sec_code, f.issuer_name_raw)) AS issuers,
           max(h.ratio_pct) AS max_ratio_pct,
           sum(CASE WHEN h.important_proposal THEN 1 ELSE 0 END) AS proposal_reports,
           max({EVENT}) AS latest_report
    FROM eq_lvh_holders h JOIN eq_lvh_filings f USING (doc_id)
    WHERE {WHERE}
    GROUP BY 1
""".replace("{EVENT}", event_date("f"))


@router.get("/holders")
def holders(limit: int = Query(50, ge=1, le=500),
            by: str = Query("group", description="'group' (default) consolidates "
                                                 "a family's filing entities; "
                                                 "'entity' lists them separately"),
            filer_type: str = Query("", description="one filer_type to filter to; "
                                                    "see /holder-types"),
            group: str = Query("", description="one group, to list its entities"),
            activist: str = Query("", description="'true' for holders that have "
                                                  "stated an important-proposal act")):
    u"""The most active 5% filers, by family or by filing entity.

    Grouped is the default because the alternative misleads: BlackRock files
    under sixteen EDINET codes and Fidelity thirteen, so an entity ranking puts
    nine BlackRock subsidiaries in a top twenty and reads as nine investors.
    """
    cur = _require()
    where = ["f.status IN ('clean','partial')"]
    if (activist or "").strip().lower() == "true":
        where.append("h.important_proposal")
    entities = [_label(r) for r in
                _rows(cur, ENTITY_ROLLUP.replace("{WHERE}", " AND ".join(where)))]

    wanted_type = (filer_type or "").strip()
    if wanted_type:
        entities = [e for e in entities if e["filer_type"] == wanted_type]
    wanted_group = (group or "").strip()
    if wanted_group:
        entities = [e for e in entities if e["group"] == wanted_group]

    by_entity = (by or "").strip().lower() == "entity" or bool(wanted_group)
    if by_entity:
        rows = sorted(entities, key=lambda e: (-(e["issuers"] or 0), -(e["reports"] or 0)))
    else:
        # A group's issuer count is the number of DISTINCT companies its
        # entities file on, which is not the sum of theirs: BlackRock's
        # sixteen entities file on largely the same names, and adding them
        # would report it holding 5% of more companies than exist.
        keys = [e["holder_key"] for e in entities]
        per_entity = _sets_by_entity(cur, where) if keys else {}
        empty = (set(), set(), set())
        merged = {}
        for e in entities:
            g = merged.setdefault(e["group"], {
                "group": e["group"], "entities": [], "reports": 0,
                "max_ratio_pct": None, "proposal_reports": 0,
                "latest_report": None, "issuer_set": set(), "doc_set": set(),
                "proposal_doc_set": set()})
            g["entities"].append({
                "holder_key": e["holder_key"],
                "holder_edinet_code": e["holder_edinet_code"],
                "name_ja": e["name_ja"], "name_en": e["name_en"],
                "filer_type": e["filer_type"], "filer_type_en": e["filer_type_en"],
                "issuers": e["issuers"], "reports": e["reports"],
                "max_ratio_pct": e["max_ratio_pct"],
                "proposal_reports": e["proposal_reports"]})
            g["reports"] += e["reports"] or 0
            g["proposal_reports"] += e["proposal_reports"] or 0
            if e["max_ratio_pct"] is not None:
                g["max_ratio_pct"] = max(g["max_ratio_pct"] or 0, e["max_ratio_pct"])
            if e["latest_report"] and (not g["latest_report"]
                                       or e["latest_report"] > g["latest_report"]):
                g["latest_report"] = e["latest_report"]
            issuers, docs, proposal_docs = per_entity.get(e["holder_key"], empty)
            g["issuer_set"] |= issuers
            g["doc_set"] |= docs
            g["proposal_doc_set"] |= proposal_docs
        rows = []
        for g in merged.values():
            g["issuers"] = len(g["issuer_set"])
            # A joint filing names several of a group's entities; these count
            # it once, where reports and proposal_reports count each line.
            g["unique_reports"] = len(g.pop("doc_set"))
            g["unique_proposal_docs"] = len(g.pop("proposal_doc_set"))
            g.pop("issuer_set")
            g["entity_count"] = len(g["entities"])
            # The type of a group is its entities' type where they agree, and
            # stated as mixed where they do not — a bank group that also runs
            # an asset manager is both, and picking one would be a claim.
            kinds = sorted({e["filer_type"] for e in g["entities"]})
            g["filer_type"] = kinds[0] if len(kinds) == 1 else "mixed"
            # "Mixed" is the label; WHICH kinds it mixes is detail for a
            # tooltip, not a sentence inside a badge.
            g["filer_type_en"] = TYPE_EN[kinds[0]] if len(kinds) == 1 else "Mixed"
            g["filer_type_mix"] = [TYPE_EN[k] for k in kinds]
            g["entities"].sort(key=lambda e: -(e["issuers"] or 0))
            rows.append(g)
        rows.sort(key=lambda g: (-(g["issuers"] or 0), -(g["reports"] or 0)))

    head = {"holders": rows[:limit],
            "by": "entity" if by_entity else "group",
            "filer_type": wanted_type or None,
            "group": wanted_group or None,
            "activist_only": (activist or "").strip().lower() == "true",
            "filers_total": len(entities),
            "groups_total": (None if by_entity else len(rows)),
            "unique_proposal_docs_total": cur.execute(
                "SELECT count(DISTINCT f.doc_id) FROM eq_lvh_holders h "
                "JOIN eq_lvh_filings f USING (doc_id) WHERE "
                + " AND ".join(where) + " AND f.important_proposal").fetchone()[0]}
    head["proposal_count_note"] = CALC["unique_proposal_docs"]
    head["type_note"] = TYPE_NOTE
    head["group_note"] = GROUP_NOTE
    return _notes(head)


def _sets_by_entity(cur, where):
    u"""entity key -> (issuers it has filed on, its reports, its reports
    with important_proposal true), each a set so a group can union them."""
    out = {}
    cur.execute("""
            SELECT coalesce(h.holder_edinet_code, h.name_key) AS holder_key,
                   coalesce(f.issuer_sec_code, f.issuer_name_raw) AS issuer,
                   f.doc_id, coalesce(f.important_proposal, false) AS proposal
            FROM eq_lvh_holders h JOIN eq_lvh_filings f USING (doc_id)
            WHERE """ + " AND ".join(where))
    for key, issuer, doc_id, proposal in cur.fetchall():
        sets = out.setdefault(key, (set(), set(), set()))
        sets[0].add(issuer)
        sets[1].add(doc_id)
        if proposal:
            sets[2].add(doc_id)
    return out


@router.get("/holder-types")
def holder_types():
    u"""The filer types, with how many filing entities carry each."""
    cur = _require()
    counts = {}
    for r in _rows(cur, ENTITY_ROLLUP.replace(
            "{WHERE}", "f.status IN ('clean','partial')")):
        kind, _ = type_of(r.get("business_ja"), r.get("is_individual"),
                          r.get("holder_type_ja"), r.get("name_ja"))
        counts[kind] = counts.get(kind, 0) + 1
    return {"types": [{"filer_type": k, "label": TYPE_EN[k], "filers": counts.get(k, 0)}
                      for k in TYPE_EN if counts.get(k)],
            "type_note": TYPE_NOTE, "calc": CALC, "provenance": PROVENANCE}


_EXPORT_HOLDER_COLS = """
        h.holder_no, (h.holder_no = 1) AS is_primary_filer,
        h.holder_edinet_code, coalesce(h.holder_edinet_code, h.name_key) AS holder_key,
        h.name_raw AS holder_name, h.name_en AS holder_name_en,
        h.holder_type_ja, h.is_individual, h.business_ja,
        h.shares_held AS holder_shares_held, h.ratio_pct AS holder_ratio_pct,
        h.prior_ratio_pct AS holder_prior_ratio_pct, h.in_group_total,
        h.important_proposal AS holder_important_proposal,
        {h.proposal_stance} AS holder_proposal_stance,
        {h.purpose_mentions_proposal} AS holder_purpose_mentions_proposal,
        h.important_proposal_ja AS holder_important_proposal_ja,
        h.purpose_ja AS holder_purpose_ja
"""


@router.get("/export")
def export(level: str = Query("filings", description="'filings' (one row per "
                                                     "report) or 'holders' (one "
                                                     "row per holder line, with "
                                                     "its report's columns)"),
           format: str = Query("csv", description="'csv' or 'ndjson'"),
           activist: str = Query("", description="'true' for filings stating an "
                                                 "important-proposal act"),
           report_type: str = Query("", description="initial | change | amendment"),
           since: str = Query("", description="filed on or after, YYYY-MM-DD"),
           until: str = Query("", description="filed on or before, YYYY-MM-DD")):
    u"""The whole tape in one download, streamed: every report (or every
    holder line) matching the filters, oldest first. CSV opens with '#'
    lines carrying the source, the notes and the formulas."""
    cur = _require()
    lvl = (level or "").strip().lower()
    fmt = (format or "").strip().lower()
    if lvl not in ("filings", "holders"):
        raise HTTPException(400, "level must be 'filings' or 'holders'")
    if fmt not in ("csv", "ndjson"):
        raise HTTPException(400, "format must be 'csv' or 'ndjson'")
    since, until = _date_param(since, "since"), _date_param(until, "until")
    where, params = _tape_where(activist, 0, 0, report_type, since, until)
    opt = _opt(cur)
    cols = filing_cols(opt)
    join = ""
    order = "f.filed_date, f.doc_id"
    if lvl == "holders":
        cols += "," + _fill(_EXPORT_HOLDER_COLS, opt)
        join = "JOIN eq_lvh_holders h USING (doc_id)"
        order += ", h.holder_no"
    cur.execute("WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT """ + cols + """, coalesce(es.name_en, ee.name_en) AS issuer_name_en
        FROM eq_lvh_filings f """ + join + """
        LEFT JOIN en_scode es ON es.sec_code = f.issuer_sec_code
        LEFT JOIN en_ecode ee ON ee.edinet_code = f.issuer_edinet_code
        WHERE """ + " AND ".join(where) + asof.clause("filed_date", "f") + """
        ORDER BY """ + order, params)
    names = [d[0] for d in cur.description]

    def text(v):
        return "" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v)

    def rows():
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            for line in ["Plover Analytics — 5% shareholding reports (大量保有報告書), "
                         "level=" + lvl,
                         "Source: large shareholding reports (大量保有報告書・変更報告書) "
                         "filed on EDINET, Financial Services Agency of Japan. Figures "
                         "as filed; derived columns carry their formula below.",
                         EVENT_NOTE, GROUP_NOTE, RATIO_NOTE, PROPOSAL_NOTE,
                         "Missing values are empty, never zero."] + [
                             "%s = %s" % kv for kv in sorted(CALC.items())]:
                buf.write("# " + line + "\n")
            w.writerow(names)
            yield buf.getvalue()
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            buf = io.StringIO()
            if fmt == "csv":
                w = csv.writer(buf)
                for r in batch:
                    w.writerow([text(v) for v in r])
            else:
                for r in batch:
                    buf.write(json.dumps(dict(zip(names, (text(v) for v in r))),
                                         ensure_ascii=False) + "\n")
            yield buf.getvalue()

    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/x-ndjson"
    name = "5pct-%s.%s" % (lvl, fmt)
    return StreamingResponse(rows(), media_type=media, headers={
        "Content-Disposition": 'attachment; filename="%s"' % name})


# The dataset's card (app/registry.py). Events, not positions: each row is one
# report on its own trigger date.
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "large-shareholdings",
    "section": "ownership",
    "name": {"en": "5% shareholding reports", "ja": "大量保有報告書・変更報告書"},
    "shape": "events",
    "summary": ("The 5% tape: every large-shareholding report and change "
                "report as filed — who is accumulating, in whom, how fast, and "
                "whether the holder states it may make important proposals to "
                "the board."),
    "source": dict(_EDINET_SOURCE,
                   document="大量保有報告書 / 変更報告書 (large shareholding reports, "
                            "EDINET)",
                   credit="Source: large shareholding reports filed on EDINET "
                          "(Financial Services Agency of Japan)."),
    "keys": ["doc_id", "sec_code"],
    "frequency": "per-event",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "2026-05 (filed date; capture began then)",
        "stale_after_days": None,
    },
    "measures": [
        {"id": "ratio_pct", "label": "Statutory holding ratio (株券等保有割合)",
         "unit": "%", "trust": "official"},
        {"id": "shares_held", "label": "Shares held by the filing group", "unit": "shares",
         "trust": "official"},
        {"id": "requirement_date", "label": "Trigger date (提出義務発生日), as filed",
         "unit": "date", "trust": "official"},
        {"id": "filed_date", "label": "Date EDINET received the report", "unit": "date",
         "trust": "official"},
        {"id": "important_proposal", "label": "Filed answer to 重要提案行為等",
         "unit": "boolean", "trust": "official"},
        {"id": "ratio_change_pp", "label": "Change in holding ratio since the prior report",
         "unit": "pp", "trust": "derived", "calc": CALC["ratio_change_pp"]},
        {"id": "is_current", "label": "Latest report still shows 5% or more",
         "unit": "boolean", "trust": "derived", "calc": CALC["is_current"]},
        {"id": "activist_filings", "label": "Reports stating an important-proposal act",
         "unit": "count", "trust": "derived", "calc": CALC["activist_filings"]},
        {"id": "days_to_file", "label": "Days from trigger date to filing",
         "unit": "days", "trust": "derived", "calc": CALC["days_to_file"]},
        {"id": "filer_type", "label": "Filer classification", "unit": "category",
         "trust": "derived", "calc": TYPE_NOTE},
        {"id": "proposal_stance", "label": "Filed 重要提案行為 answer, read",
         "unit": "category", "trust": "derived", "calc": CALC["proposal_stance"]},
        {"id": "purpose_mentions_proposal",
         "label": "Stated purpose reserves important proposals", "unit": "boolean",
         "trust": "derived", "calc": CALC["purpose_mentions_proposal"]},
        {"id": "date_quality", "label": "Whether the trigger date is possible",
         "unit": "category", "trust": "derived", "calc": CALC["date_quality"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/stakes/company/{sec_code}",
        "search": "/api/v1/equity/stakes/companies",
        "summary": "/api/v1/equity/stakes/summary",
        "screen": "/api/v1/equity/stakes/holders",
        "recent": "/api/v1/equity/stakes/recent",
        "holder": "/api/v1/equity/stakes/holder/{key}",
        "holder_types": "/api/v1/equity/stakes/holder-types",
        "export": "/api/v1/equity/stakes/export",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [
        {"id": "group", "title": "Most active 5% filers, by family (joint filers consolidated)"},
        {"id": "entity", "title": "Most active 5% filers, by filing entity"},
        {"id": "activist", "title": "Holders that have stated an important-proposal act"},
    ],
    "cite": "/stakes.html?c={sec_code}",
    "page": "/stakes.html",
    "notes": [EVENT_NOTE, GROUP_NOTE, RATIO_NOTE, PROPOSAL_NOTE],
}
