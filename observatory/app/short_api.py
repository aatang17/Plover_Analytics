# -*- coding: utf-8 -*-
u"""Equity product API — short positions (空売り残高, JPX daily disclosure).

The mirror of the 5% filings next door. A holder whose short position reaches
0.5% of an issuer's shares must report it, and the exchange publishes every
report the next business day: holder, address, issuer, ratio, share count, and
the day the position was measured.

WHAT A CONSUMER MUST NOT ASSUME, carried in the data rather than in prose:

  1. **A REPORT IS AN EVENT, NOT A POSITION.** Each row is one report on its
     own calculation date. A holder files again only when the position moves,
     so the outstanding book is the LATEST report per (issuer, holder, fund)
     — which is what /company and /companies return, with the formula beside
     it. It is derived and carries no badge.

  2. **THE BOOK IS ONLY AS COMPLETE AS THE TAPE IS LONG.** JPX keeps about
     twelve business days on its site and deletes the rest; our archive starts
     the day capture started. A position opened before that and not moved
     since has never been published into it. Every surface here states how
     many days the archive holds, because that number *is* the completeness of
     the book.

  3. **A CLOSING REPORT IS A REAL ZERO.** A holder falling under 0.5% files one
     last report saying so; it carries below_threshold and a ratio at or near
     zero. That is the position going away, not a missing value, and it is
     what takes the position out of the book.

  4. **THE SUM OVER HOLDERS IS NOT THE SHORT INTEREST.** Only positions of 0.5%
     or more are disclosed at all, so an issuer's total here is the *disclosed*
     short, always an understatement of the true one. It is never presented as
     short interest.

  5. **SHARES AND RATIO HAVE DIFFERENT DENOMINATORS FROM THE 5% FILINGS.** The
     ratio is JPX's own 空売り残高割合 against shares outstanding, not the
     statutory 株券等保有割合 the long filings use. The two are not comparable
     to the decimal and are never netted against each other.
"""
from fastapi import APIRouter, HTTPException, Query

from . import aliases, asof
from . import listing
from .equity_api import NAMES_NOTE, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/shorts", tags=["Short positions"])

THRESHOLD_PCT = 0.5

CALC = {
    "open_position": (
        "the newest report per (issuer, holder, discretionary manager, fund) "
        "by calculation date, counted as open unless that report is a "
        "below-threshold closing report"),
    "disclosed_short_pct": (
        "sum of the open positions' 空売り残高割合 for one issuer, each as "
        "published"),
    "ratio_change_pp": (
        "空売り残高割合 − 直近空売り残高割合, both as published (percentage points)"),
    "disclosed_short_shares": "sum of the open positions' 空売り残高数量, as published",
    "days_covered": "number of distinct publication days held in the archive",
}

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as published by Japan Exchange Group in its daily "
             "空売り残高に関する情報 file. Each daily workbook is archived with "
             "its SHA-256 before parsing. The ratio is stored as the percentage "
             "JPX's own PDF prints; the workbook writes the same number as a "
             "decimal fraction."),
}

EVENT_NOTE = (
    "Each row is one report on its own calculation date (計算年月日), not a "
    "running position. A holder files again only when the position moves, so "
    "the absence of a recent report means the position has not changed, not "
    "that it has gone.")

COVERAGE_NOTE = (
    "JPX keeps roughly twelve business days of these files on its site and "
    "then deletes them. There is no archive, no API and no second source, so "
    "this record begins the day capture began and can never be back-filled. "
    "A position opened before that and not moved since is not in it.")

THRESHOLD_NOTE = (
    "Only positions of 0.5% or more of shares outstanding are disclosed at "
    "all. An issuer's total here is therefore the DISCLOSED short, which is "
    "always less than its true short interest, and is never presented as "
    "short interest.")

CLOSING_NOTE = (
    "A holder whose position falls under 0.5% files one final report, marked "
    "below_threshold with a ratio at or near zero. That zero is the position "
    "closing — a real value, never a gap — and is what removes the position "
    "from the book.")

RATIO_NOTE = (
    "ratio_pct is JPX's 空売り残高割合: the position against shares "
    "outstanding. It is not the statutory 株券等保有割合 used by the 5% long "
    "filings, whose denominator includes the holder's own potential shares. "
    "The two are published on different bases and are never netted.")

AGGREGATION_NOTE = (
    "Ratios are added only WITHIN one issuer, where the holders share a "
    "denominator. A short of 3% of one company and 3% of another are not the "
    "same measure, so no total across issuers is shown — for a holder, the "
    "count of positions and its largest one are, and those do compare.")

FUND_NOTE = (
    "A holder reporting through a discretionary mandate names the manager "
    "(委託者・投資一任契約の相手方) and, where it applies, the fund "
    "(信託財産・運用財産の名称). Those are part of a position's identity here: "
    "one holder can carry several positions in one issuer through different "
    "funds, and they are kept apart exactly as reported.")


def _require():
    cur = _cur()
    try:
        cur.execute("SELECT 1 FROM eq_short_reports LIMIT 1")
    except Exception:                                            # noqa: BLE001
        raise HTTPException(503, "short positions dataset not published yet")
    return cur


def _notes(head):
    head["event_note"] = EVENT_NOTE
    head["coverage_note"] = COVERAGE_NOTE
    head["threshold_note"] = THRESHOLD_NOTE
    head["closing_note"] = CLOSING_NOTE
    head["ratio_note"] = RATIO_NOTE
    head["fund_note"] = FUND_NOTE
    head["aggregation_note"] = AGGREGATION_NOTE
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    head["vintage"] = asof.vintage("publication")
    return head


# The outstanding book, derived. A position's identity is the issuer, the
# holder, the discretionary manager and the fund — one holder can be short the
# same issuer through several mandates and they are reported separately.
#
# The point-in-time ceiling is the PUBLICATION date, which is the honest basis
# here for once: it is the day the market could read the report.
_OPEN_BOOK = """
    WITH ranked AS (
        SELECT r.*,
               row_number() OVER (
                   PARTITION BY r.sec_code, r.holder_key,
                                coalesce(r.manager_name, ''),
                                coalesce(r.fund_name, '')
                   ORDER BY r.calc_date DESC, r.publish_date DESC,
                            r.ratio_pct DESC) AS rn
        FROM eq_short_reports r
        WHERE TRUE/*ASOF*/
    ),
    book AS (
        SELECT *, (NOT below_threshold AND ratio_pct >= %f) AS is_open
        FROM ranked WHERE rn = 1
    )
""" % THRESHOLD_PCT


def open_book():
    u"""The book CTE with the point-in-time ceiling in force."""
    return _OPEN_BOOK.replace("/*ASOF*/", asof.clause("publish_date", "r"))


REPORT_COLS = """
        r.publish_date, r.calc_date, r.sec_code, r.name_ja, r.name_en,
        r.holder_name, r.holder_address, r.holder_key, r.manager_name,
        r.fund_name, r.ratio_pct, r.shares, r.units, r.prev_calc_date,
        r.prev_ratio_pct, r.below_threshold, r.note_raw,
        CASE WHEN r.prev_ratio_pct IS NULL THEN NULL
             ELSE round(r.ratio_pct - r.prev_ratio_pct, 4) END AS ratio_change_pp
"""


def _coverage(cur):
    u"""How much tape we hold. This is the completeness of everything below it."""
    row = _rows(cur, """
        SELECT count(*) AS files, min(publish_date) AS first_published,
               max(publish_date) AS last_published,
               sum(row_count) AS reports_published
        FROM eq_short_files""")[0]
    row["days_covered"] = row.pop("files")
    return row


@router.get("/summary")
def summary():
    u"""Coverage first — it is the honest ceiling on everything after it."""
    cur = _require()
    head = {"coverage": _coverage(cur)}
    head["tape"] = _rows(cur, """
        SELECT count(*) AS reports,
               count(DISTINCT sec_code) AS issuers,
               count(DISTINCT holder_key) AS holders,
               min(calc_date) AS earliest_calc_date,
               max(calc_date) AS latest_calc_date,
               sum(CASE WHEN below_threshold THEN 1 ELSE 0 END) AS closing_reports
        FROM eq_short_reports r WHERE TRUE""" + asof.clause("publish_date", "r"))[0]
    # Counts, and the largest single issuer — never a sum of ratios or of
    # share counts across issuers. A percentage of one company and a
    # percentage of another are not the same measure and do not add up, and
    # neither do their share counts.
    head["book"] = _rows(cur, open_book() + """
        SELECT count(*) FILTER (WHERE is_open) AS open_positions,
               count(DISTINCT CASE WHEN is_open THEN sec_code END) AS issuers_shorted,
               count(DISTINCT CASE WHEN is_open THEN holder_key END) AS holders,
               round(max(CASE WHEN is_open THEN ratio_pct END), 4) AS largest_position_pct,
               round(median(CASE WHEN is_open THEN ratio_pct END), 4) AS median_position_pct
        FROM book""")[0]
    head["by_day"] = _rows(cur, """
        SELECT publish_date, row_count AS reports, issuers, holders,
               calc_date_min, calc_date_max
        FROM eq_short_files ORDER BY publish_date DESC LIMIT 60""")
    return _notes(head)


@router.get("/recent")
def recent(limit: int = Query(50, ge=1, le=500),
           min_ratio: float = Query(0.0, ge=0, le=100),
           min_change: float = Query(0.0, ge=0, le=100,
                                     description="minimum absolute change in "
                                                 "percentage points since the "
                                                 "holder's previous report"),
           closing: str = Query("", description="'true' for closing reports only, "
                                                "'false' to exclude them")):
    u"""The tape, newest calculation date first."""
    cur = _require()
    where = ["TRUE" + asof.clause("publish_date", "r")]
    params = []
    if min_ratio:
        where.append("r.ratio_pct >= ?")
        params.append(min_ratio)
    if min_change:
        where.append("abs(r.ratio_pct - r.prev_ratio_pct) >= ?")
        params.append(min_change)
    flag = (closing or "").strip().lower()
    if flag == "true":
        where.append("r.below_threshold")
    elif flag == "false":
        where.append("NOT r.below_threshold")
    rows = _rows(cur, "SELECT " + REPORT_COLS + """
        FROM eq_short_reports r
        WHERE """ + " AND ".join(where) + """
        ORDER BY r.calc_date DESC, r.publish_date DESC, r.ratio_pct DESC
        LIMIT ?""", params + [limit])
    return _notes({"reports": rows, "filters": {
        "min_ratio": min_ratio, "min_change": min_change,
        "closing": flag or None}})


@router.get("/companies")
@listing.marked
def companies(q: str = Query("", description="issuer name or code substring"),
              sort: str = Query("disclosed_pct",
                                description="disclosed_pct | holders | shares"),
              limit: int = Query(50, ge=1, le=500)):
    u"""Issuers by disclosed short, from the open book. Also the search feed."""
    cur = _require()
    term = (q or "").strip()
    like = "%" + term + "%"
    alias_sql, alias_params = aliases.clause(cur, "sec_code", term)
    order = {"disclosed_pct": "disclosed_short_pct DESC",
             "holders": "holders DESC",
             "shares": "disclosed_short_shares DESC"}.get(sort, "disclosed_short_pct DESC")
    rows = _rows(cur, open_book() + """
        SELECT sec_code,
               any_value(name_ja) AS name,
               any_value(name_en) AS name_en,
               count(*) FILTER (WHERE is_open) AS holders,
               round(sum(CASE WHEN is_open THEN ratio_pct END), 4) AS disclosed_short_pct,
               sum(CASE WHEN is_open THEN shares END) AS disclosed_short_shares,
               max(calc_date) AS latest_calc_date,
               max(publish_date) AS latest_published
        FROM book
        WHERE (? = '' OR sec_code LIKE ? OR name_ja LIKE ?
               OR lower(coalesce(name_en, '')) LIKE lower(?)""" + alias_sql + """)
        GROUP BY 1
        HAVING count(*) FILTER (WHERE is_open) > 0
        ORDER BY """ + order + """ NULLS LAST
        LIMIT ?""", [term, like, like, like] + alias_params + [limit])
    return _notes({"companies": rows, "sort": sort, "names_note": NAMES_NOTE})


@router.get("/company/{sec_code}")
def company(sec_code: str,
            history: int = Query(200, ge=1, le=1000,
                                 description="how many past reports to return")):
    u"""Who is short this company — the view no single report shows."""
    cur = _require()
    code = (sec_code or "").strip()
    positions = _rows(cur, open_book() + """
        SELECT holder_name, holder_key, holder_address, manager_name, fund_name,
               ratio_pct, shares, units, calc_date, publish_date,
               prev_calc_date, prev_ratio_pct, below_threshold, is_open,
               CASE WHEN prev_ratio_pct IS NULL THEN NULL
                    ELSE round(ratio_pct - prev_ratio_pct, 4) END AS ratio_change_pp
        FROM book WHERE sec_code = ?
        ORDER BY is_open DESC, ratio_pct DESC""", [code])
    if not positions:
        raise HTTPException(404, "no short positions archived for %s" % code)
    name = _rows(cur, """
        SELECT any_value(name_ja) AS name, any_value(name_en) AS name_en
        FROM eq_short_reports WHERE sec_code = ?""", [code])[0]
    open_rows = [p for p in positions if p["is_open"]]
    head = {
        "sec_code": code,
        "name": name["name"],
        "name_en": name["name_en"],
        "positions": positions,
        "open_positions": len(open_rows),
        "disclosed_short_pct": round(sum(p["ratio_pct"] or 0 for p in open_rows), 4)
                               or None,
        "disclosed_short_shares": sum(p["shares"] or 0 for p in open_rows) or None,
    }
    head["reports"] = _rows(cur, "SELECT " + REPORT_COLS + """
        FROM eq_short_reports r WHERE r.sec_code = ?""" +
        asof.clause("publish_date", "r") + """
        ORDER BY r.calc_date DESC, r.publish_date DESC LIMIT ?""",
        [code, history])
    head["coverage"] = _coverage(cur)
    return _notes(head)


@router.get("/holders")
def holders(limit: int = Query(50, ge=1, le=500)):
    u"""The short sellers, ranked by how many open positions they hold."""
    cur = _require()
    rows = _rows(cur, open_book() + """
        SELECT holder_key,
               any_value(holder_name) AS holder_name,
               any_value(holder_address) AS holder_address,
               count(*) FILTER (WHERE is_open) AS open_positions,
               count(DISTINCT CASE WHEN is_open THEN sec_code END) AS issuers,
               round(max(CASE WHEN is_open THEN ratio_pct END), 4) AS largest_pct,
               max(calc_date) AS latest_calc_date
        FROM book GROUP BY 1
        HAVING count(*) FILTER (WHERE is_open) > 0
        ORDER BY open_positions DESC LIMIT ?""", [limit])
    return _notes({"holders": rows})


@router.get("/holder/{key}")
def holder(key: str, limit: int = Query(200, ge=1, le=1000)):
    u"""One short seller's open book, largest position first."""
    cur = _require()
    folded = (key or "").strip().lower()
    rows = _rows(cur, open_book() + """
        SELECT sec_code, any_value(name_ja) AS name, any_value(name_en) AS name_en,
               max(CASE WHEN is_open THEN ratio_pct END) AS ratio_pct,
               sum(CASE WHEN is_open THEN shares END) AS shares,
               max(calc_date) AS calc_date, max(publish_date) AS publish_date,
               count(*) FILTER (WHERE is_open) AS positions
        FROM book WHERE holder_key = ?
        GROUP BY 1 HAVING count(*) FILTER (WHERE is_open) > 0
        ORDER BY ratio_pct DESC LIMIT ?""", [folded, limit])
    if not rows:
        raise HTTPException(404, "no open short positions archived for %r" % key)
    name = _rows(cur, """
        SELECT any_value(holder_name) AS holder_name,
               any_value(holder_address) AS holder_address
        FROM eq_short_reports WHERE holder_key = ?""", [folded])[0]
    return _notes({"holder_key": folded,
                   "holder_name": name["holder_name"],
                   "holder_address": name["holder_address"],
                   "positions": rows,
                   "open_positions": len(rows),
                   "coverage": _coverage(cur)})


@router.get("/files")
def files(limit: int = Query(120, ge=1, le=500)):
    u"""The daily workbooks held, each with the hash of the bytes we read.

    This is the dataset's provenance and its completeness in one table: a day
    that is not here is a day nobody can recover, because JPX has deleted it.
    """
    cur = _require()
    return _notes({"files": _rows(cur, """
        SELECT publish_date, vintage_id, sha256, row_count AS reports, issuers,
               holders, calc_date_min, calc_date_max, url, raw_path,
               fetched_at, parser_version
        FROM eq_short_files ORDER BY publish_date DESC LIMIT ?""", [limit]),
        "coverage": _coverage(cur)})


MANIFEST = {
    "id": "short-positions",
    "section": "ownership",
    "name": {"en": "Short positions", "ja": "空売り残高に関する情報"},
    "shape": "events",
    "summary": ("Every disclosed short position in a Japanese listed company — "
                "holder, issuer, size and the day it was measured — as JPX "
                "publishes it each business day. The mirror of the 5% long "
                "filings, and the one record here that cannot be back-filled: "
                "the exchange deletes each file after about twelve business "
                "days."),
    "source": {
        "publisher": "Japan Exchange Group",
        "publisher_ja": "日本取引所グループ",
        "document": "空売り残高に関する情報 (Information on Outstanding Short "
                    "Selling Positions), published daily",
        "url": "https://www.jpx.co.jp/markets/public/short-selling/index.html",
        "credit": "Source: Japan Exchange Group — Information on Outstanding "
                  "Short Selling Positions.",
        "license_note": ("Published by JPX for public reference. Each daily "
                         "workbook is archived with its SHA-256 before parsing; "
                         "JPX itself retains only about twelve business days."),
    },
    "keys": ["sec_code", "holder_key", "calc_date"],
    "frequency": "daily",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "2026-09 (publication date; capture began then)",
        "stale_after_days": 7,
    },
    "measures": [
        {"id": "ratio_pct", "label": "Short position ratio (空売り残高割合)",
         "unit": "%", "trust": "official"},
        {"id": "shares", "label": "Short position in shares (空売り残高数量)",
         "unit": "shares", "trust": "official"},
        {"id": "units", "label": "Short position in trading units (空売り残高売買単位数)",
         "unit": "count", "trust": "official"},
        {"id": "calc_date", "label": "Calculation date (計算年月日)", "unit": "date",
         "trust": "official"},
        {"id": "publish_date", "label": "Date JPX published the report", "unit": "date",
         "trust": "official"},
        {"id": "below_threshold", "label": "Closing report — the position fell under 0.5%",
         "unit": "boolean", "trust": "official"},
        {"id": "is_open", "label": "Position still outstanding", "unit": "boolean",
         "trust": "derived", "calc": CALC["open_position"]},
        {"id": "disclosed_short_pct", "label": "Disclosed short, all holders", "unit": "%",
         "trust": "derived", "calc": CALC["disclosed_short_pct"]},
        {"id": "disclosed_short_shares", "label": "Disclosed short in shares, all holders",
         "unit": "shares", "trust": "derived", "calc": CALC["disclosed_short_shares"]},
        {"id": "ratio_change_pp", "label": "Change since the holder's previous report",
         "unit": "pp", "trust": "derived", "calc": CALC["ratio_change_pp"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/shorts/company/{sec_code}",
        "search": "/api/v1/equity/shorts/companies",
        "summary": "/api/v1/equity/shorts/summary",
        "screen": "/api/v1/equity/shorts/holders",
        "recent": "/api/v1/equity/shorts/recent",
        "holder": "/api/v1/equity/shorts/holder/{key}",
        "files": "/api/v1/equity/shorts/files",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [
        {"id": "companies", "title": "Most shorted issuers, by disclosed ratio"},
        {"id": "holders", "title": "Short sellers, by number of open positions"},
        {"id": "recent", "title": "The tape — newest reports first"},
    ],
    "cite": "/shorts.html?c={sec_code}",
    "page": "/shorts.html",
    "notes": [EVENT_NOTE, COVERAGE_NOTE, THRESHOLD_NOTE, CLOSING_NOTE,
              RATIO_NOTE, FUND_NOTE, AGGREGATION_NOTE],
}
