# -*- coding: utf-8 -*-
"""Equity product API — cross-shareholdings (政策保有株式).

Product #2's namespace. Reads its own DuckDB file (data/equity.duckdb, written
offline by equity/extract.py — one writer at a time, same discipline as the
macro DB). Deliberately outside the macro core: none of the /{dataset}/ shapes
apply here, and this router must be registered BEFORE the core router so its
literal /equity/ paths win over the core's /{dataset}/ catch-alls.

Trust contract: share counts, book values, purposes and reciprocity are
Official (as filed) — every row carries its filing doc_id, filed date and the
archived file's SHA-256. Year-on-year changes are derived client-side and carry
their formula there.
"""
import datetime
import os
import pathlib
import threading

import duckdb
from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import db
from . import jp_enums
from . import plausibility

from . import aliases
from . import basis as basis_mod

# EQUITY_DB_PATH overrides the location, the same variable the extractors and
# company_labels already read. Nothing sets it in the container, so production
# is unchanged; it exists so a copy of the database can be served — which is
# how a schema change gets exercised end to end while the live file is held
# open by a running server.
DB_PATH = pathlib.Path(os.environ.get(
    "EQUITY_DB_PATH",
    str(pathlib.Path(__file__).resolve().parent.parent / "data" / "equity.duckdb")))

router = APIRouter(prefix="/api/v1/equity", tags=["Cross-shareholdings"])

_READER = None
_READER_VERSION = None
_LOCK = threading.Lock()


def _version():
    try:
        st = DB_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


# The response cache stamps entries with this too: app/backfill.py can swap a
# new equity file in while the server runs, and an entry stamped only with the
# macro file's version would go on serving the old rows.
file_version = _version


def _cur():
    global _READER, _READER_VERSION
    if not DB_PATH.exists():
        raise HTTPException(503, "equity database not built yet")
    version = _version()
    with _LOCK:
        if _READER is None or _READER_VERSION != version:
            if _READER is not None:
                _READER.close()
            _READER = db.limit_memory(duckdb.connect(str(DB_PATH), read_only=True))
            _READER_VERSION = version
        return _READER.cursor()


def cohort_filter(spec, as_of=""):
    """Resolve an optional ?cohort= into (codes, cohort-description).

    Every cross-company screen takes one, and they all come through here so a
    cohort names the same companies wherever it is used. No cohort given means
    no filter — a screen keeps its whole-market behaviour unless asked.
    """
    from . import cohorts
    try:
        return cohorts.member_set(_cur(), spec, (as_of or "").strip() or None)
    except cohorts.CohortError as e:
        raise HTTPException(400, str(e))


def cohort_sql(codes, alias):
    """(SQL fragment, bind values) restricting `alias`.sec_code to a cohort.

    Bound, not interpolated, even though the codes are already validated —
    a screen is not the place to start building SQL out of query strings.
    An empty cohort restricts to nothing, which is the honest answer; the
    fragment is empty only when no cohort was asked for.
    """
    if codes is None:
        return "", []
    if not codes:
        return " AND FALSE", []
    members = sorted(codes)
    return (" AND %s.sec_code IN (%s)" % (alias, ", ".join("?" * len(members))),
            members)


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# How far each extractor has read the EDINET archive. This is the equity
# equivalent of the ingest health report, and it exists because nothing like
# it did: when the 5% filings stopped advancing on 2026-08-06 every dashboard
# still rendered, every endpoint still answered 200, and the only way to see
# the problem was to notice that the newest filing was a month old.
#
# EDINET publishes on business days, and Japan's longest normal closure (the
# New Year period, plus the weekends either side) runs to about six days, so
# seven is the shortest threshold that never cries wolf.
EXTRACT_STALE_AFTER_DAYS = 7

# Not every source runs on EDINET's clock. The classification is stamped with
# the month-end effective date of the JPX file, so a copy fetched this morning
# is already up to five weeks old by its own date and a weekly threshold would
# report "attention" on data that is perfectly current.
# JPX's earnings-calendar lists are dated by the list itself and re-issued
# every week or two, with a new month's list early each month; the same 45
# days gives a missed month room to show before it cries wolf.
STALE_AFTER_BY_EXTRACTOR = {"classification": 45, "earnings-calendar": 45}


def coverage():
    """[(extractor, through_date, ran_at, back_to_date)] — empty if never run.

    `back_to_date` is how DEEP the extractor has read, not how recent: the
    nightly catch-up walks it backwards a slice at a time until it reaches the
    floor of the archive. It is None on a database written before that column
    existed, and on any extractor that has only ever run forward.
    """
    if not DB_PATH.exists():
        return []
    cur = _cur()
    names = {r[0] for r in cur.execute(
        "SELECT table_name FROM duckdb_tables()").fetchall()}
    if "eq_extract_runs" not in names:
        return []
    cols = {r[1] for r in cur.execute(
        "PRAGMA table_info('eq_extract_runs')").fetchall()}
    depth = "back_to_date" if "back_to_date" in cols else "NULL"
    cur.execute("SELECT extractor, through_date, ran_at, %s "
                "FROM eq_extract_runs ORDER BY extractor" % depth)
    return cur.fetchall()


def shape_flags():
    """{extractor: [flag, ...]} — columns that changed shape on the last run.

    Freshness answers "is it still arriving". This answers the question that
    used to have no answer at all: "does it still look like itself". A parser
    whose source renames a field keeps running and keeps reporting clean, and
    the only visible trace is a column that used to be 92% full and is now 4%.

    Read straight out of the table `equity/metrics.py` writes after each run,
    so the comparison logic lives in one place and this module keeps no copy
    of it. A database written before that table existed simply has no flags.
    """
    if not DB_PATH.exists():
        return {}
    cur = _cur()
    names = {r[0] for r in cur.execute(
        "SELECT table_name FROM duckdb_tables()").fetchall()}
    if "eq_extract_drift" not in names:
        return {}
    out = {}
    for ext, table, kind, key, was, now, detail in cur.execute(
            "SELECT extractor, table_name, kind, key, was, now_pct, detail "
            "FROM eq_extract_drift ORDER BY extractor, table_name, key").fetchall():
        out.setdefault(ext, []).append({
            "table": table, "kind": kind, "column": key,
            "was_pct": was, "now_pct": now, "detail": detail})
    return out


def health():
    """Per-extractor freshness and shape, in the shape /catalog/health uses."""
    today = datetime.date.today()
    flags = shape_flags()
    out = []
    for extractor, through, ran, back_to in coverage():
        days = (today - through).days if through else None
        limit = STALE_AFTER_BY_EXTRACTOR.get(extractor, EXTRACT_STALE_AFTER_DAYS)
        stale = days is None or days > limit
        out.append({
            "dataset": extractor,
            "status": "attention" if stale else "ok",
            "archive_read_through": through.isoformat() if through else None,
            "days_behind": days,
            "stale_after_days": limit,
            "stale": stale,
            "last_extracted_at": ran.isoformat() + "Z" if ran else None,
            # How deep, as opposed to how recent. A dataset can be perfectly
            # fresh and still only three months deep, which is what the 5%
            # filings were for months while 74,000 filings sat in the bucket:
            # freshness alone could not see it.
            "archive_read_back_to": back_to.isoformat() if back_to else None,
            # Empty on a healthy night. A non-empty list means a column moved
            # far enough to be worth a human look; it never means the data is
            # wrong, and it never blocks anything.
            "shape_flags": flags.get(extractor, []),
        })
    return out


# The archive holds five fiscal years, so "one row per filing" is never the
# right population for a cross-section: summing book value over every filing a
# company ever made would overstate the total several times over, and the same
# holder would appear once per year in a holder list. Every cross-sectional
# surface therefore runs against ONE filing per company — its latest, or the
# one covering ?year= — while the year-on-year series lives in /history.
_LATEST_FILINGS = """
    WITH scoped AS (
        SELECT *, coalesce(sec_code, edinet_code, doc_id) AS filer_key
        FROM eq_filings
        WHERE status IN ('clean','partial')/*ASOF*/
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
    ),
    current_filings AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY filer_key
                                         ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM scoped
        ) WHERE rn = 1
    )
"""


def latest_filings():
    """Latest Filings, with the point-in-time ceiling in force (app/asof.py)."""
    return _LATEST_FILINGS.replace("/*ASOF*/", asof.clause("filed_date", ""))


def _year_params(year):
    y = (year or "").strip() or None
    return [y, y]


# A filing names its holdings in Japanese, but every annual report states the
# FILER's own English name on its cover page (【英訳名】) — and that is the name
# used here: as filed, and complete, since all 4,226 filers with a securities
# code state one. EDINET's filer registry is only the fallback, for the few held
# companies that file no annual report of their own; the registry alone missed
# roughly one listed filer in ten, Murata Manufacturing among them. Dating the
# registry rows to 1900 makes the filing win wherever both exist.
NAME_CTES = """,
    en_ecode AS (
        SELECT edinet_code, max_by(name_en, ord) AS name_en FROM (
            SELECT edinet_code, filer_name_en AS name_en, period_end AS ord
            FROM eq_filings WHERE filer_name_en IS NOT NULL
            UNION ALL
            SELECT edinet_code, name_en, DATE '1900-01-01'
            FROM eq_entities WHERE name_en IS NOT NULL)
        WHERE edinet_code IS NOT NULL GROUP BY 1),
    en_scode AS (
        SELECT sec_code, max_by(name_en, ord) AS name_en FROM (
            SELECT sec_code, filer_name_en AS name_en, period_end AS ord
            FROM eq_filings WHERE filer_name_en IS NOT NULL AND sec_code IS NOT NULL
            UNION ALL
            SELECT sec_code, name_en, DATE '1900-01-01'
            FROM eq_entities WHERE name_en IS NOT NULL AND sec_code IS NOT NULL)
        GROUP BY 1)
"""

# Ownership percentage. There is no price feed in this product and there does
# not need to be one: for a single-class listed stock, a stake's share of market
# capitalisation IS its share of shares outstanding. The denominator therefore
# comes from the issuer's OWN annual report — issued shares at its fiscal year
# end, less treasury — which we already hold, because most issuers of policy
# holdings are themselves filers.
#
# Two dates never line up. The holder reports its stake at its own fiscal year
# end; the issuer reports its share count at the issuer's. We take the issuer
# filing NEAREST that date, either side, because nearest-in-time is the best
# estimate of the share base the stake was measured against — an issuer report
# three months later describes it better than one nine months earlier. Ties go
# to the earlier filing, and the date used is always returned.
#
# The failure this guards against is a stock split inside that window. The
# holder's share count is post-split and the issuer's pre-split, so the stake
# reads high by the split ratio — Hulic's stake in Fuyo General Lease read
# 41.8% against a pre-split denominator where the real figure is ~13.9%, and
# the 3-for-1 and 5-for-1 splits of the last two years put 2,502 positions in
# that state. Where the issuer's share count moves by BASIS_MOVE_LIMIT or more
# across the window, the share base is indeterminate and no percentage is
# published. Detect, disclose, never fill — the same rule the yen totals follow.
BASIS_WINDOW_DAYS = 550          # an annual filer, plus room for one missed year
BASIS_MOVE_LIMIT = 1.5           # split or major issuance inside the window

def ownership_cte():
    """The issuer share-count CTEs, built per request: one filing per company
    and year as of the ceiling in force (asof.newest)."""
    return (""",
    issuer_shares AS (
        SELECT edinet_code, period_end, outstanding, share_classes,
               lag(outstanding) OVER (PARTITION BY edinet_code
                                      ORDER BY period_end) AS prev_outstanding
        FROM (SELECT e.edinet_code, e.period_end, e.share_classes,
                     e.issued_shares - coalesce(e.treasury_shares, 0) AS outstanding
              FROM eq_filings e
              WHERE e.status IN ('clean','partial')
                AND e.issued_shares IS NOT NULL
                AND e.issued_shares - coalesce(e.treasury_shares, 0) > 0""" + asof.newest(
                    "e", "eq_filings", ok="newer.status IN ('clean','partial')") + """)
    ),
    basis_candidates AS (
        SELECT h.doc_id, h.held_edinet_code AS edinet_code,
               i.period_end, i.outstanding, i.share_classes, i.prev_outstanding,
               i.period_end <= f.period_end AS at_or_before,
               abs(date_diff('day', i.period_end, f.period_end)) AS gap,
               -- nearest wins; a tie goes to the filing at or before the
               -- holding's date, which is the as-filed reading.
               2 * abs(date_diff('day', i.period_end, f.period_end))
                 + CASE WHEN i.period_end <= f.period_end THEN 0 ELSE 1 END AS rank_key
        FROM eq_holdings h
        JOIN eq_filings f ON f.doc_id = h.doc_id
        JOIN issuer_shares i ON i.edinet_code = h.held_edinet_code
        WHERE abs(date_diff('day', i.period_end, f.period_end)) <= %d
    ),
    basis_agg AS (
        SELECT doc_id, edinet_code,
               arg_min(outstanding, rank_key) AS outstanding,
               arg_min(period_end, rank_key) AS basis_period_end,
               arg_min(share_classes, rank_key) AS share_classes,
               arg_min(gap, rank_key) AS basis_gap,
               arg_min(prev_outstanding, rank_key) AS prev_outstanding,
               arg_min(outstanding, rank_key) FILTER (WHERE at_or_before) AS before_out,
               arg_min(outstanding, rank_key) FILTER (WHERE NOT at_or_before) AS after_out
        FROM basis_candidates GROUP BY 1, 2
    ),
    issuer_basis AS (
        SELECT doc_id, edinet_code, outstanding, basis_period_end, share_classes,
               prev_outstanding,
               -- Only a denominator measured on a DIFFERENT date can straddle a
               -- split. Where the issuer's year end is the holding's year end —
               -- the common case, both being 31 March — the count is exact and
               -- a change in the following year says nothing about it.
               basis_gap > 0
                 AND before_out IS NOT NULL AND after_out IS NOT NULL
                 AND greatest(before_out, after_out)
                     >= %s * least(before_out, after_out) AS basis_ambiguous
        FROM basis_agg
    )
""") % (BASIS_WINDOW_DAYS, BASIS_MOVE_LIMIT)

# One expression, both directions of the page, so the two can never disagree.
#
# Three things stop a percentage being published, and each one has a reason the
# page can show instead of a bare dash:
#
# 1. The issuer's share count moved across the window and nothing places the
#    holding on one side of the move (see basis_ambiguous above).
# 2. The POSITION's own share count multiplied while the issuer's did not. That
#    is a split the issuer has not filed for yet: the holder restates its
#    holding onto the new share base a year before the issuer's next annual
#    report reaches us. Mitsubishi's Ise Chemicals holding went 577,604 to
#    3,726,040 shares against a share count that never moved, and read 73% of a
#    company it holds about a seventh of. Where the issuer's count moved WITH
#    the position, the opposite is true — the two agree, and that agreement
#    resolves case 1, which is why Hulic's post-split Fuyo General Lease stake
#    still publishes.
# 3. The result exceeds 100%, which cannot be a stake in any company. It means
#    the denominator belongs to the wrong share base, and it is a fault to
#    report rather than a number to print.
POSITION_RESTATED = "h.prior_shares > 0 AND h.shares >= %s * h.prior_shares" % BASIS_MOVE_LIMIT
ISSUER_RESTATED = ("i.prev_outstanding > 0 AND i.outstanding >= %s * i.prev_outstanding"
                   % BASIS_MOVE_LIMIT)

PCT_UNAVAILABLE = """
       CASE WHEN h.shares IS NULL OR i.outstanding IS NULL THEN NULL
            WHEN ({restated}) AND NOT ({issuer_restated})
                 THEN 'holding restated onto a share base the issuer has not filed yet'
            WHEN i.basis_ambiguous AND NOT (({restated}) AND ({issuer_restated}))
                 THEN 'issuer share count changed over this window'
            WHEN 100.0 * h.shares / i.outstanding > 100
                 THEN 'stake exceeds the shares outstanding we hold'
            END""".format(restated=POSITION_RESTATED, issuer_restated=ISSUER_RESTATED)

# Filers occasionally tag a figure at the wrong scale — a book value entered in
# 百万円 into a 円 field, or a share count in thousands. The two filed numbers
# are then mutually impossible: no listed Japanese share carries a book value of
# ¥1,000,000 per share. The row is kept exactly as filed (both numbers stay), is
# flagged, and is left OUT of aggregate yen, with the exclusion disclosed —
# the same discipline as the reconciliation gate: detect, disclose, never fill.
# Five reclassified rows breach it, and they would otherwise carry ¥319trn of a
# ¥328trn total.
IMPLAUSIBLE_YEN_PER_SHARE = 1000000

PLAUSIBLE_SQL = ("(r.shares IS NULL OR r.shares <= 0 OR r.book_value_yen IS NULL "
                 "OR r.book_value_yen * 1.0 / r.shares < %d)" % IMPLAUSIBLE_YEN_PER_SHARE)

PCT_SELECT = """
       CASE WHEN h.shares IS NOT NULL AND i.outstanding > 0
                 AND ({unavailable}) IS NULL
            THEN 100.0 * h.shares / i.outstanding END AS pct_outstanding,
       ({unavailable}) AS pct_unavailable,
       i.basis_period_end AS pct_basis_period_end,
       i.share_classes AS pct_share_classes,
       -- The mirror of pct_outstanding. That one sizes a stake against the
       -- ISSUER; these size the SAME position against the HOLDER's own balance
       -- sheet, both figures off the holder's filing (`f` is the holder's
       -- filing in both queries that use this block). Guarded on > 0: a
       -- percentage of negative or absent equity is not a reading.
       CASE WHEN h.book_value_yen IS NOT NULL AND f.equity_yen > 0
            THEN 100.0 * h.book_value_yen / f.equity_yen END AS pct_of_holder_equity,
       CASE WHEN h.book_value_yen IS NOT NULL AND f.total_assets_yen > 0
            THEN 100.0 * h.book_value_yen / f.total_assets_yen END AS pct_of_holder_assets,
       f.equity_basis AS holder_equity_basis,
       f.equity_yen AS holder_equity_yen,
       f.total_assets_yen AS holder_total_assets_yen,
       NOT ({plausible}) AS implausible
""".format(unavailable=PCT_UNAVAILABLE.strip(),
           plausible=PLAUSIBLE_SQL.replace("r.", "h."))

PCT_JOIN = """
        LEFT JOIN issuer_basis i ON i.doc_id = h.doc_id
             AND i.edinet_code = h.held_edinet_code
"""

OWNERSHIP_CALC = ("shares held ÷ (issuer's issued shares − treasury shares) × 100, "
                  "both as filed, the denominator taken from the issuer's own "
                  "annual securities report nearest the holding's fiscal year end")

OWNERSHIP_NOTE = ("Derived, not filed. The denominator is the issuer's own "
                  "reported issued shares less treasury shares, from its annual "
                  "report nearest this holding's fiscal year end on either side "
                  "— pct_basis_period_end gives that date, and it can fall after "
                  "the holding's date where that report is the closer one. For a "
                  "single-class listed stock this equals the stake's share of "
                  "market capitalisation. It is null where the issuer files no "
                  "annual report we hold; where the issuer's share count moves "
                  "by half or more across the window, because a split or issue "
                  "makes the share base indeterminate; and where the result "
                  "would exceed 100%, which is a data fault rather than a "
                  "stake. It is less exact where the issuer has more than one "
                  "share class (pct_share_classes > 1), because treasury shares "
                  "are reported across all classes.")


IMPLAUSIBLE_NOTE = (
    "A row is flagged implausible when its two filed numbers are mutually "
    "impossible — book value ÷ shares of ¥%s or more, which no listed Japanese "
    "share reaches. It is a filer tagging error (a value entered at the wrong "
    "scale, or a share count in thousands), not an extraction error: the values "
    "match the filing's XBRL exactly. Flagged rows are still returned, exactly "
    "as filed, but are excluded from the yen aggregates here and counted in "
    "excluded_positions / excluded_yen so the exclusion is visible."
    % "{:,}".format(IMPLAUSIBLE_YEN_PER_SHARE))

HOLDER_SHARE_CALC = ("this position's book value ÷ the holder's shareholders' "
                     "equity (or total assets) × 100, both as filed in the "
                     "holder's own annual securities report")

HOLDER_SHARE_NOTE = (
    "The mirror of pct_outstanding. That sizes a stake against the ISSUER — how "
    "much of the issuer this holder owns. pct_of_holder_equity and "
    "pct_of_holder_assets size the same position against the HOLDER: how much "
    "of its own capital is committed to this one name. Calculated, not filed. "
    "Null where the holder's filing gives no usable denominator, never zero. "
    "holder_equity_basis says which accounting figure the denominator is, and a "
    "parent_only basis is not comparable with a consolidated one. "
    "A position CAN legitimately exceed 100% of equity — a holder whose book "
    "equity is smaller than one long-held stake is a real and commercially "
    "interesting case, not an error — so no ceiling is imposed. Extreme values "
    "are worth checking against the filing: where the position's own two filed "
    "numbers are mutually impossible the row is marked `implausible`, but that "
    "test catches only scale errors visible per share, so it does not catch "
    "every bad figure. The book value and share count are returned alongside "
    "so the reader can check.")

RECLASSIFIED_NOTE = (
    "As filed, from the annual report's 保有目的を変更した投資株式 table. "
    "direction=to_pure means the filer moved the holding from a policy "
    "shareholding to 純投資目的 (pure investment); the shares are not "
    "necessarily sold, and the position leaves the named policy table in the "
    "year of the change. direction=to_policy is the reverse. fy_of_change_ja is "
    "the fiscal year the filer states the change took effect, verbatim — some "
    "filers list more than one. A filing repeats these rows for several years "
    "after the change, so this is a standing disclosure, not a one-year event.")

FILING_NOTES_NOTE = (
    "The filing's own numbered footnotes to the named policy table, verbatim. "
    "The XBRL numbers them (注)1, (注)2 … and gives no link back to a row, so "
    "a note is tied to a position only where it names that issuer.")

FLOWS_NOTE = (
    "The filer's own tally, as filed, of how many issues increased or decreased "
    "during the year and the yen involved — acquisition cost for increases, "
    "sale proceeds for decreases. Read against the reclassification table this "
    "separates what was actually sold from what merely changed category. "
    "Counts are as filed and are not corrected for filer tagging errors.")

# How big the policy book is relative to the filer's own balance sheet.
#
# The numerator is the filing's OWN total for the whole policy bucket, not our
# sum of the named rows: the named table lists only the largest issues, and
# over seven large financials those rows carry 74.5% of the tagged total —
# 55.5% at Mizuho. Totals are summed across the entities a filing discloses
# (the filer itself, and where different the group's largest and second largest
# holders), because those are separate legal entities inside the same
# consolidated balance sheet. That summing is what separates 42% from 66% at
# MS&AD; for 94% of filers only one entity is disclosed and it changes nothing.
#
# It is a FLOOR, not the group total: a filing names at most those entities, so
# holdings elsewhere in the group are disclosed nowhere and are not counted.
SCALE_ENTITY_LABEL = {"reporting": "The filer itself",
                      "largest": "Largest holder in the group",
                      "second_largest": "Second largest holder in the group"}

# Which rung of the equity ladder the filing actually tagged. JGAAP 純資産 and
# the including-minorities IFRS/US-GAAP totals are comparable; the parent-share
# figures exclude non-controlling interests and so read slightly high as a
# denominator; parent-only is a different measure altogether and says so.
EQUITY_BASIS_LABEL = {
    "jgaap_consolidated": "Consolidated net assets (Japanese GAAP)",
    "ifrs_consolidated": "Consolidated total equity (IFRS)",
    "ifrs_consolidated_excl_nci":
        "Consolidated equity attributable to owners of the parent (IFRS)",
    "usgaap_consolidated": "Consolidated total equity (US GAAP)",
    "usgaap_consolidated_excl_nci":
        "Consolidated equity attributable to owners of the parent (US GAAP)",
    "parent_only": "Parent company only — not consolidated",
}

SCALE_EQUITY_CALC = ("total policy shareholdings ÷ shareholders' equity × 100, "
                     "both as filed in this annual securities report")

SCALE_ASSETS_CALC = ("total policy shareholdings ÷ total assets × 100, "
                     "both as filed in this annual securities report")

SCALE_NOTE = (
    "The policy total is Official (as filed) — the filing's own total carrying "
    "amount for the whole policy bucket, listed and unlisted, summed across the "
    "entities the filing discloses. It is a floor: holdings at group companies "
    "the filing does not name are disclosed nowhere and are not included. The "
    "percentages are calculated, not filed, and carry their formula. "
    "equity_basis names which figure the denominator is, because filers report "
    "under Japanese, IFRS or US accounting standards and an adopter of the "
    "latter two stops tagging the Japanese consolidated figure — a parent-only "
    "denominator is a different measure and is labelled as one, never silently "
    "mixed with a group figure. Percentages are null where equity or total "
    "assets is missing or not positive; missing is never shown as zero.")

# Stated because it is the published threshold professional readers apply, and
# it is a fact about a third party's policy, not our judgement of a company.
SCALE_REFERENCE = (
    "ISS's Japan proxy voting guidelines recommend a vote against the top "
    "executive at a company that allocates 20 percent or more of its net "
    "assets to cross-shareholdings, a policy effective from February 2022. "
    "ISS counts unilateral holdings, not only mutual ones.")

NAMES_NOTE = ("English company names are as filed: each company's own annual "
              "report states its English name on the cover page. For a company "
              "that files no annual report, EDINET's filer registry is used "
              "instead. The as-filed Japanese name is always returned alongside.")

# EDINET records a filer's sector in Japanese only. These are the standard TSE
# 33 sectors, whose English names JPX publishes, plus the filer types EDINET
# puts in the same field for entities that are not listed companies. A fixed
# lookup, never a translation: an unknown value returns None and the page shows
# the Japanese as recorded.
INDUSTRY_EN = {
    u"水産・農林業": "Fishery, Agriculture & Forestry",
    u"鉱業": "Mining",
    u"建設業": "Construction",
    u"食料品": "Foods",
    u"繊維製品": "Textiles & Apparels",
    u"パルプ・紙": "Pulp & Paper",
    u"化学": "Chemicals",
    u"医薬品": "Pharmaceutical",
    u"石油・石炭製品": "Oil & Coal Products",
    u"ゴム製品": "Rubber Products",
    u"ガラス・土石製品": "Glass & Ceramics Products",
    u"鉄鋼": "Iron & Steel",
    u"非鉄金属": "Nonferrous Metals",
    u"金属製品": "Metal Products",
    u"機械": "Machinery",
    u"電気機器": "Electric Appliances",
    u"輸送用機器": "Transportation Equipment",
    u"精密機器": "Precision Instruments",
    u"その他製品": "Other Products",
    u"電気・ガス業": "Electric Power & Gas",
    u"陸運業": "Land Transportation",
    u"海運業": "Marine Transportation",
    u"空運業": "Air Transportation",
    u"倉庫・運輸関連": "Warehousing & Harbor Transportation Services",
    u"情報・通信業": "Information & Communication",
    u"卸売業": "Wholesale Trade",
    u"小売業": "Retail Trade",
    u"銀行業": "Banks",
    u"証券、商品先物取引業": "Securities & Commodities Futures",
    u"保険業": "Insurance",
    u"その他金融業": "Other Financing Business",
    u"不動産業": "Real Estate",
    u"サービス業": "Services",
    u"その他": "Others",
    u"個人（組合発行者を除く）": "Individual (excluding partnership issuers)",
    u"個人（非居住者）（組合発行者を除く）":
        "Individual, non-resident (excluding partnership issuers)",
    u"内国法人・組合（有価証券報告書等の提出義務者以外）":
        "Domestic corporation or partnership (not an annual-report filer)",
    u"外国法人・組合（有価証券報告書等の提出義務者以外）":
        "Foreign corporation or partnership (not an annual-report filer)",
    u"外国法人・組合": "Foreign corporation or partnership",
    u"外国政府等": "Foreign government or equivalent",
}

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as filed in each company's 有価証券報告書 "
             "(annual securities report), EDINET. Raw filings archived with "
             "SHA-256; doc_id links to the source filing."),
}


# A share count that jumps without a purchase is the second-most common way to
# misread this data (the first is a reclassification). Filers say so themselves,
# in the row's stated purpose or in the table's footnotes, and the events are a
# closed vocabulary. Detection is keyword matching on the filed text — derived,
# so it carries this rule rather than a badge, and the filed text always travels
# with the flag so a reader can check it.
CORPORATE_ACTIONS = [
    ("株式分割", "share split"),
    ("株式併合", "share consolidation"),
    ("株式交換", "share exchange"),
    ("株式移転", "share transfer"),
    ("会社分割", "company split"),
    ("合併", "merger"),
    ("持株会社", "holding-company reorganisation"),
    ("商号変更", "name change"),
    ("公開買付", "tender offer"),
    ("上場廃止", "delisting"),
]

ACTION_CALC = ("keyword match over the filing's own stated purpose and table "
               "footnotes: " + "、".join(ja for ja, _ in CORPORATE_ACTIONS))

RATIO_CALC = ("current shares ÷ prior shares where that ratio is an exact whole "
              "number (or its exact reciprocal) — the signature of a split or a "
              "consolidation rather than a trade")

_LEGAL = ("株式会社", "㈱", "（株）", "(株)", "ホールディングス", "グループ本社",
          "有限会社", "合同会社")


def _base_name(s):
    """Issuer name reduced to its core, for finding it inside a footnote."""
    s = (s or "").strip()
    for token in _LEGAL:
        s = s.replace(token, "")
    return s.replace(" ", "").replace("\u3000", "")


def _actions_in(text):
    return [en for ja, en in CORPORATE_ACTIONS if ja in (text or "")]


def _share_ratio(shares, prior):
    """Exact whole-number ratios only. 2,723,220 / 544,644 = 5.00 is a split;
    1,224,000 / 612,000 = 2.00 is a split; anything inexact is a trade."""
    if not shares or not prior or shares <= 0 or prior <= 0:
        return None
    if shares > prior and shares % prior == 0:
        return float(shares // prior)
    if prior > shares and prior % shares == 0:
        return round(1.0 / (prior // shares), 6)
    return None


def _annotate(holdings, notes, reclassified=()):
    """Attach corporate-action flags to each position, in place.

    A footnote is tied to a position when it names that issuer; the footnote
    numbers in the XBRL are the notes' own (注)1, (注)2 … and carry no link
    back to a row, so the name is the only join the filing gives us.

    The same name match carries the reclassification flag back onto the named
    table, which is where it answers the question a reader actually has: a row
    whose current column is blank did not necessarily go anywhere.
    """
    indexed = [(n, _base_name(n.get("text_ja"))) for n in notes]
    moved = set()
    for r in reclassified:
        if r.get("direction") == "to_pure":
            base = _base_name(r.get("held_name_raw"))
            if base:
                moved.add(base)
    for h in holdings:
        name = _base_name(h.get("held_name_raw"))
        hit = [n for n, base in indexed if name and len(name) >= 2 and name in base]
        found = _actions_in(h.get("purpose_ja"))
        for n in hit:
            for a in _actions_in(n["text_ja"]):
                if a not in found:
                    found.append(a)
        h["corporate_actions"] = found
        h["note_refs"] = [n["note_no"] for n in hit]
        h["share_ratio"] = _share_ratio(h.get("shares"), h.get("prior_shares"))
        h["reclassified_to_pure"] = bool(name) and name in moved
    return holdings


@router.get("/years")
def years():
    """Fiscal years available, newest first — the feed for a year picker."""
    cur = _cur()
    return {"years": _rows(cur, """
        SELECT CAST(year(period_end) AS VARCHAR) AS year,
               count(DISTINCT coalesce(edinet_code, doc_id)) AS filings,  -- a correction is not another filer
               min(period_end) AS first_period_end, max(period_end) AS last_period_end
        FROM eq_filings WHERE status IN ('clean','partial') AND period_end IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC""")}


@router.get("/summary")
def summary(year: str = Query("", description="fiscal year, e.g. 2025; default latest")):
    """Market-wide totals for one fiscal year: filers covered, policy holdings
    at book value, and the year-on-year change in the number of stakes held."""
    cur = _cur()
    head = _rows(cur, latest_filings() + """
        SELECT count(DISTINCT f.filer_key)                        AS filers,
               count(*)                                          AS named_holdings,
               sum(h.book_value_yen)                             AS total_book_value_yen,
               sum(CASE WHEN h.shares < h.prior_shares THEN 1 ELSE 0 END) AS positions_reduced,
               sum(CASE WHEN h.shares > h.prior_shares THEN 1 ELSE 0 END) AS positions_increased,
               sum(CASE WHEN h.shares = h.prior_shares THEN 1 ELSE 0 END) AS positions_unchanged,
               sum(CASE WHEN h.shares IS NULL OR h.prior_shares IS NULL
                        THEN 1 ELSE 0 END)                        AS positions_not_comparable,
               min(f.period_end)                                 AS earliest_period_end,
               max(f.period_end)                                 AS latest_period_end
        FROM eq_holdings h JOIN current_filings f USING (doc_id)
    """, _year_params(year))[0]
    # 相互保有の有無 is free text and filers have written it 661 ways, including
    # one that answers for both years at once ("前事業年度：有 当事業年度：無").
    # Reading it in SQL and again in Python gave two different counts, so it is
    # read once, in `jp_enums`, over the distinct spellings -- a few hundred
    # groups, not 200,000 rows.
    head["reciprocal_pairs"] = sum(
        r["n"] for r in _rows(cur, latest_filings() + """
            SELECT h.reciprocal AS filed, count(*) AS n
            FROM eq_holdings h JOIN current_filings f USING (doc_id)
            GROUP BY 1""", _year_params(year))
        if jp_enums.reciprocal(r["filed"]) is True)
    status = _rows(cur, "SELECT status, count(*) AS n FROM eq_filings GROUP BY 1")
    head["extraction_status"] = {r["status"]: r["n"] for r in status}
    head["filings_total_all_years"] = sum(r["n"] for r in status)
    head["scope"] = ("fiscal year %s" % year.strip()) if year.strip() else \
        "each company's latest filing"
    # Japanese year-ends are staggered and a company that has delisted or merged
    # stops filing, so a "latest filing" cross-section mixes reference periods.
    # Publish that spread rather than a single max date, which would read as an
    # as-of it isn't.
    head["as_of_composition"] = _rows(cur, latest_filings() + """
        SELECT CAST(f.period_end AS VARCHAR)[1:4] AS year,
               count(DISTINCT f.filer_key) AS filers
        FROM current_filings f JOIN eq_holdings h USING (doc_id)
        GROUP BY 1 ORDER BY 1 DESC""", _year_params(year))
    head["as_of_note"] = ("Each company's most recent annual report, counting only "
                          "filers that disclosed named holdings. Japanese fiscal "
                          "year-ends are staggered, and a company that has delisted "
                          "or merged stops filing, so reference periods differ by "
                          "company — see as_of_composition. Request ?year= for a "
                          "single fiscal year.")
    # Most positions simply do not move, and a share count can rise without a
    # purchase when the issuer splits its stock. Publishing cut-vs-added alone
    # would read as accumulation across the market, which is not what the
    # filings say — the unchanged and not-comparable counts are the context.
    head["position_change_note"] = ("Share-count comparison against the same "
                                    "filing's prior-year column. A stock split "
                                    "raises the count without a purchase, so "
                                    "'increased' overstates buying; most positions "
                                    "are unchanged.")
    head["provenance"] = PROVENANCE
    # Every yen figure on this response is a sum across filers with staggered
    # year ends, so the basis carries no single as_of -- the composition above
    # is the honest answer to "as of when".
    head["basis"] = basis_mod.market_basis()
    return head


@router.get("/companies")
def companies(q: str = Query("", description="name or code substring")):
    """Search box feed: filers with extracted tables and/or companies that are held."""
    cur = _cur()
    like = "%" + q.strip() + "%"
    # A market nickname is not in any of the three fields: MUFG appears neither
    # in the filed Japanese name nor in "Mitsubishi UFJ Financial Group, Inc.".
    # aliases.clause adds the codes such a query names, and nothing else.
    alias_sql, alias_params = aliases.clause(cur, "sec_code", q)
    # Match the code, the as-filed Japanese name and the registry English name.
    # The match runs AFTER the filer and held rows are grouped into one row per
    # company: filtering the two branches separately would drop a company's
    # held-by count whenever only its filer name matched the query.
    return {"companies": _rows(cur, latest_filings() + NAME_CTES + """,
        filers AS (
            SELECT f.sec_code, max(f.filer_name) AS name, max(n.name_en) AS name_en,
                   count(h.doc_id) AS holdings, 0 AS held_by
            FROM current_filings f LEFT JOIN eq_holdings h USING (doc_id)
            LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
            WHERE f.sec_code IS NOT NULL GROUP BY 1),
        held AS (
            SELECT h.held_sec_code AS sec_code, max(h.held_name_raw) AS name,
                   max(n.name_en) AS name_en, 0 AS holdings, count(*) AS held_by
            FROM eq_holdings h JOIN current_filings f USING (doc_id)
            LEFT JOIN en_ecode n ON n.edinet_code = h.held_edinet_code
            WHERE h.held_sec_code IS NOT NULL GROUP BY 1),
        combined AS (
            SELECT u.sec_code, max(u.name) AS name,
                   coalesce(max(u.name_en), max(s.name_en)) AS name_en,
                   sum(u.holdings) AS holdings_count, sum(u.held_by) AS held_by_count
            FROM (SELECT * FROM filers UNION ALL SELECT * FROM held) u
            LEFT JOIN en_scode s ON s.sec_code = u.sec_code
            GROUP BY u.sec_code)
        SELECT * FROM combined
        WHERE sec_code LIKE ? OR name LIKE ?
           OR lower(coalesce(name_en, '')) LIKE lower(?)"""
        + alias_sql + """
        ORDER BY holdings_count + held_by_count DESC LIMIT 25
    """, _year_params("") + [like, like, like] + alias_params),
        "names_note": NAMES_NOTE}


@router.get("/company/{sec_code}")
def company(sec_code: str):
    """Both directions for one company: what it holds, and who holds it."""
    cur = _cur()
    ent = _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT e.edinet_code, e.sec_code, e.name_ja,
               coalesce(n.name_en, s.name_en) AS name_en, e.industry
        FROM eq_entities e
        LEFT JOIN en_ecode n ON n.edinet_code = e.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = e.sec_code
        WHERE e.sec_code = ? LIMIT 1""", [sec_code])
    if ent:
        ent[0]["industry_en"] = INDUSTRY_EN.get(ent[0]["industry"])
    filing = _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT f.doc_id, f.filer_name, coalesce(n.name_en, s.name_en) AS filer_name_en,
               f.period_end, f.filed_date, f.sha256, f.status
        FROM eq_filings f
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        WHERE f.sec_code = ?""" + asof.clause("filed_date", "f") + """
        ORDER BY f.period_end DESC, f.filed_date DESC, f.doc_id DESC LIMIT 1""", [sec_code])
    holdings = []
    if filing:
        holdings = _rows(cur, """
            WITH x AS (SELECT 1)""" + NAME_CTES + ownership_cte() + """
            SELECT h.holder_table, h.held_name_raw,
                   coalesce(n.name_en, s.name_en) AS held_name_en,
                   h.held_sec_code, h.match_status,
                   h.shares, h.book_value_yen, h.prior_shares, h.prior_book_value_yen,
                   h.purpose_ja, h.reciprocal,
""" + PCT_SELECT + """
            FROM eq_holdings h
            JOIN eq_filings f ON f.doc_id = h.doc_id
            LEFT JOIN en_ecode n ON n.edinet_code = h.held_edinet_code
            LEFT JOIN en_scode s ON s.sec_code = h.held_sec_code
""" + PCT_JOIN + """
            WHERE h.doc_id = ?
            ORDER BY h.book_value_yen DESC NULLS LAST""", [filing[0]["doc_id"]])
    jp_enums.annotate_reciprocal(holdings)
    # one row per holder — its latest filing — not one row per holder per year
    holders = _rows(cur, latest_filings() + NAME_CTES + ownership_cte() + """
        SELECT f.filer_name AS holder_name,
               coalesce(n.name_en, s.name_en) AS holder_name_en,
               f.sec_code AS holder_sec_code,
               f.doc_id, f.period_end, h.holder_table,
               h.shares, h.book_value_yen, h.prior_shares, h.prior_book_value_yen,
               h.purpose_ja, h.reciprocal,
""" + PCT_SELECT + """
        FROM eq_holdings h JOIN current_filings f USING (doc_id)
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
""" + PCT_JOIN + """
        WHERE h.held_sec_code = ?
        ORDER BY h.book_value_yen DESC NULLS LAST""", _year_params("") + [sec_code])
    jp_enums.annotate_reciprocal(holders)
    history = _rows(cur, """
        SELECT CAST(year(f.period_end) AS VARCHAR) AS year, f.period_end,
               count(*) AS named_holdings, sum(h.book_value_yen) AS book_value_yen
        FROM eq_holdings h JOIN eq_filings f USING (doc_id)
        WHERE f.sec_code = ? AND f.status IN ('clean','partial')""" + asof.clause("filed_date", "f") +
        asof.newest("f", "eq_filings", ok="newer.status IN ('clean','partial')") + """
        GROUP BY 1, 2 ORDER BY 1""", [sec_code])
    # The same reading, one point per annual report. Filed totals against filed
    # balance sheets, so the series is comparable year to year even though the
    # named table's composition changes.
    scale_history = _rows(cur, """
        WITH tot AS (
            SELECT doc_id, sum(book_value_yen) AS policy_total_yen
            FROM eq_filing_totals GROUP BY 1)
        SELECT CAST(year(f.period_end) AS VARCHAR) AS year, f.period_end,
               t.policy_total_yen, f.equity_yen, f.equity_basis,
               CASE WHEN f.equity_yen > 0
                    THEN 100.0 * t.policy_total_yen / f.equity_yen END
                    AS pct_of_equity,
               CASE WHEN f.total_assets_yen > 0
                    THEN 100.0 * t.policy_total_yen / f.total_assets_yen END
                    AS pct_of_assets
        FROM eq_filings f JOIN tot t USING (doc_id)
        WHERE f.sec_code = ? AND f.status IN ('clean','partial')""" + asof.clause("filed_date", "f") +
        asof.newest("f", "eq_filings", ok="newer.status IN ('clean','partial')") + """
        ORDER BY 1""", [sec_code])
    reclassified, notes, flows = [], [], []
    scale, scale_entities = None, []
    if filing:
        doc = filing[0]["doc_id"]
        # Summed across the entities the filing discloses — see SCALE_ENTITY_
        # LABEL. Guarded on > 0 rather than IS NOT NULL: equity can be filed
        # negative, and a percentage of negative equity is not a reading.
        got = _rows(cur, """
            WITH tot AS (
                SELECT doc_id,
                       sum(book_value_yen) AS policy_total_yen,
                       sum(CASE WHEN share_class = 'listed'
                                THEN book_value_yen END) AS listed_yen,
                       sum(CASE WHEN share_class = 'unlisted'
                                THEN book_value_yen END) AS unlisted_yen,
                       sum(CASE WHEN share_class = 'listed'
                                THEN issue_count END) AS listed_issues,
                       sum(CASE WHEN share_class = 'unlisted'
                                THEN issue_count END) AS unlisted_issues
                FROM eq_filing_totals GROUP BY 1)
            SELECT t.policy_total_yen, t.listed_yen, t.unlisted_yen,
                   t.listed_issues, t.unlisted_issues,
                   f.equity_yen, f.equity_basis,
                   f.total_assets_yen, f.assets_basis,
                   CASE WHEN f.equity_yen > 0
                        THEN 100.0 * t.policy_total_yen / f.equity_yen END
                        AS pct_of_equity,
                   CASE WHEN f.total_assets_yen > 0
                        THEN 100.0 * t.policy_total_yen / f.total_assets_yen END
                        AS pct_of_assets
            FROM eq_filings f JOIN tot t USING (doc_id)
            WHERE f.doc_id = ?""", [doc])
        if got:
            scale = got[0]
            scale["equity_basis_label"] = EQUITY_BASIS_LABEL.get(
                scale["equity_basis"])
            scale["assets_basis_label"] = EQUITY_BASIS_LABEL.get(
                scale["assets_basis"])
            scale["equity_calc"] = SCALE_EQUITY_CALC
            scale["assets_calc"] = SCALE_ASSETS_CALC
            plausibility.scale_bases(scale)
            # Disclosure order, not alphabetical: the filer first, then the
            # group holders it names, which is how the filing itself reads.
            scale_entities = _rows(cur, """
                SELECT holder_table, share_class, book_value_yen, issue_count
                FROM eq_filing_totals WHERE doc_id = ?
                ORDER BY CASE holder_table WHEN 'reporting' THEN 0
                                           WHEN 'largest' THEN 1
                                           WHEN 'second_largest' THEN 2
                                           ELSE 3 END,
                         share_class""", [doc])
            for e in scale_entities:
                e["holder_table_label"] = SCALE_ENTITY_LABEL.get(
                    e["holder_table"])
        # Positions the filer moved OUT of the policy bucket into 純投資目的.
        # The shares are usually still held: the row leaves the named table
        # without a sale, so an unwind measured on the named table alone counts
        # this as a disposal. Ordered by the size of what left.
        reclassified = _rows(cur, """
            WITH x AS (SELECT 1)""" + NAME_CTES + """
            SELECT r.holder_table, r.direction, r.held_name_raw,
                   coalesce(n.name_en, s.name_en) AS held_name_en,
                   r.held_sec_code, r.match_status,
                   r.shares, r.book_value_yen, r.fy_of_change_ja, r.reason_ja,
                   NOT (""" + PLAUSIBLE_SQL + """) AS implausible
            FROM eq_reclassified r
            LEFT JOIN en_ecode n ON n.edinet_code = r.held_edinet_code
            LEFT JOIN en_scode s ON s.sec_code = r.held_sec_code
            WHERE r.doc_id = ?
            -- to_pure first: it is the direction that removes a holding from
            -- the named table, and the reason this section exists.
            ORDER BY r.direction DESC, r.book_value_yen DESC NULLS LAST""", [doc])
        notes = _rows(cur, """
            SELECT holder_table, note_no, text_ja FROM eq_filing_notes
            WHERE doc_id = ?
            ORDER BY holder_table, CAST(note_no AS INTEGER)""", [doc])
        flows = _rows(cur, """
            SELECT holder_table, share_class, issues_increased,
                   acquisition_cost_yen, issues_decreased, sale_proceeds_yen
            FROM eq_filing_flows WHERE doc_id = ?
            ORDER BY holder_table, share_class""", [doc])
        _annotate(holdings, notes, reclassified)
        # Typed basis for every yen figure below. entity_scope is READ off the
        # filing's own disclosing entities rather than assumed: a bank group
        # that reports its reduction at commercial-bank level is quoting
        # largest_holding_company, not the consolidated group, and the two
        # differ by hundreds of billions of yen at SMFG.
        for e in scale_entities:
            e["entity_scope"] = basis_mod.ENTITY_SCOPE_BY_HOLDER_TABLE.get(
                e["holder_table"], "holdco_consolidated")
            e["share_scope"] = basis_mod.SHARE_SCOPE_BY_SHARE_CLASS.get(
                e["share_class"], "both")
        for fl in flows:
            fl["entity_scope"] = basis_mod.ENTITY_SCOPE_BY_HOLDER_TABLE.get(
                fl["holder_table"], "holdco_consolidated")
            fl["share_scope"] = basis_mod.SHARE_SCOPE_BY_SHARE_CLASS.get(
                fl["share_class"], "both")

    if not ent and not filing and not holders:
        raise HTTPException(404, "no data for securities code %s" % sec_code)
    period_end = filing[0]["period_end"] if filing else None
    return {"entity": ent[0] if ent else None,
            "names_note": NAMES_NOTE,
            "filing": filing[0] if filing else None,
            "basis": basis_mod.basis_for_rows(scale_entities, as_of=period_end),
            "basis_note": ("The basis every yen figure on this response is on. "
                           "entity_scope is read from the filing's own "
                           "disclosing entities, not assumed: scale_entities "
                           "and flows each carry their own entity_scope and "
                           "share_scope, and a figure quoted for one entity is "
                           "not the group figure."),
            "holdings": holdings,
            "holders": holders,
            "reclassified": reclassified,
            "reclassified_note": RECLASSIFIED_NOTE,
            "implausible_note": IMPLAUSIBLE_NOTE,
            "notes": notes,
            "notes_note": FILING_NOTES_NOTE,
            "flows": flows,
            "flows_note": FLOWS_NOTE,
            "no_reduction_measure": basis_mod.NO_REDUCTION_MEASURE,
            "scale": scale,
            "scale_entities": scale_entities,
            "scale_history": scale_history,
            "scale_note": SCALE_NOTE,
            "scale_reference": SCALE_REFERENCE,
            "ownership_calc": OWNERSHIP_CALC,
            "ownership_note": OWNERSHIP_NOTE,
            "holder_share_calc": HOLDER_SHARE_CALC,
            "holder_share_note": HOLDER_SHARE_NOTE,
            "action_calc": ACTION_CALC,
            "ratio_calc": RATIO_CALC,
            "history": history,
            "history_note": ("One point per annual report filed. Each year's book "
                             "value is as filed; book values are fair-valued at "
                             "period end, so a change reflects both trading and "
                             "market moves."),
            "provenance": PROVENANCE}


@router.get("/history")
def history(limit: int = Query(40, ge=1, le=500)):
    """Multi-year unwind: named policy-holding value per filer, per fiscal year.

    The series the five-year archive exists for. One row per filer-year, so a
    caller can chart a company's path; the cross-sectional surfaces stay on a
    single year.
    """
    cur = _cur()
    return {"rows": _rows(cur, """
        WITH x AS (SELECT 1)""" + NAME_CTES + """,
        per_year AS (
            SELECT f.sec_code, CAST(year(f.period_end) AS VARCHAR) AS year,
                   max(f.filer_name) AS name,
                   coalesce(max(n.name_en), max(s.name_en)) AS name_en,
                   max(f.period_end) AS period_end,
                   count(*) AS named_holdings,
                   sum(h.book_value_yen) AS book_value_yen
            FROM eq_holdings h JOIN eq_filings f USING (doc_id)
            LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
            LEFT JOIN en_scode s ON s.sec_code = f.sec_code
            WHERE f.status IN ('clean','partial') AND f.sec_code IS NOT NULL""" + asof.clause("filed_date", "f") +
            asof.newest("f", "eq_filings", ok="newer.status IN ('clean','partial')") + """
            GROUP BY 1, 2),
        ranked AS (
            SELECT sec_code, max(book_value_yen) AS peak FROM per_year GROUP BY 1
            ORDER BY peak DESC NULLS LAST LIMIT ?)
        SELECT p.* FROM per_year p JOIN ranked r USING (sec_code)
        ORDER BY r.peak DESC NULLS LAST, p.sec_code, p.year
    """, [limit]),
        "provenance": PROVENANCE,
        "names_note": NAMES_NOTE,
        "basis": basis_mod.market_basis(),
        "derived_note": ("Each point is the sum of named policy holdings as filed "
                         "for that fiscal year. Book values are fair-valued at "
                         "period end, so a fall reflects both selling and market "
                         "moves — position counts separate the two.")}


@router.get("/unwind")
def unwind(year: str = Query("", description="fiscal year, e.g. 2025; default latest")):
    """Sector unwind ranking: named policy-holding value change, per filer."""
    cur = _cur()
    return {"filers": _rows(cur, latest_filings() + NAME_CTES + """
        SELECT f.sec_code, max(f.filer_name) AS name,
               coalesce(max(n.name_en), max(s.name_en)) AS name_en,
               max(f.period_end) AS period_end,
               count(*) AS named_holdings,
               sum(h.book_value_yen)        AS book_value_yen,
               sum(h.prior_book_value_yen)  AS prior_book_value_yen,
               sum(CASE WHEN h.shares < h.prior_shares THEN 1 ELSE 0 END) AS reduced,
               sum(CASE WHEN h.shares > h.prior_shares THEN 1 ELSE 0 END) AS increased
        FROM eq_holdings h JOIN current_filings f USING (doc_id)
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        GROUP BY f.sec_code ORDER BY book_value_yen DESC NULLS LAST
    """, _year_params(year)), "provenance": PROVENANCE,
        "names_note": NAMES_NOTE,
        "basis": basis_mod.market_basis(),
        "derived_note": ("Value change and reduced/increased counts compare the "
                         "current and prior-year columns of the same filing — "
                         "derived; formula shown on the page.")}


@router.get("/reclassified")
def reclassified(year: str = Query("", description="fiscal year, e.g. 2025; default latest"),
                 limit: int = Query(100, ge=1, le=1000)):
    """Filers ranked by policy holdings moved to 純投資目的, not sold.

    The gap this closes: a position reclassified as pure investment vanishes
    from the named policy table without a transaction, so an unwind measured on
    that table alone books it as a disposal. Filings disclose the move
    explicitly, with the fiscal year and the filer's own reason; this ranks
    what left the bucket against what the same filing says was actually sold.
    """
    cur = _cur()
    filers = _rows(cur, latest_filings() + NAME_CTES + """,
        sold AS (
            SELECT doc_id, sum(sale_proceeds_yen) AS sale_proceeds_yen
            FROM eq_filing_flows WHERE share_class = 'listed' GROUP BY 1
        )
        SELECT f.sec_code, f.filer_name AS name,
               coalesce(n.name_en, s.name_en) AS name_en,
               f.period_end, f.doc_id,
               count(*)                    AS reclassified_positions,
               sum(CASE WHEN """ + PLAUSIBLE_SQL + """ THEN r.book_value_yen END)
                                           AS reclassified_yen,
               count(*) FILTER (WHERE NOT (""" + PLAUSIBLE_SQL + """))
                                           AS excluded_positions,
               sum(CASE WHEN NOT (""" + PLAUSIBLE_SQL + """) THEN r.book_value_yen END)
                                           AS excluded_yen,
               max(sold.sale_proceeds_yen) AS sale_proceeds_yen
        FROM eq_reclassified r
        JOIN current_filings f USING (doc_id)
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        LEFT JOIN sold ON sold.doc_id = f.doc_id
        WHERE r.direction = 'to_pure'
        GROUP BY f.sec_code, f.filer_name, n.name_en, s.name_en, f.period_end, f.doc_id
        ORDER BY reclassified_yen DESC NULLS LAST
        LIMIT ?
    """, _year_params(year) + [limit])
    totals = _rows(cur, latest_filings() + """
        SELECT count(DISTINCT r.doc_id)  AS filers,
               count(*)                  AS positions,
               sum(CASE WHEN """ + PLAUSIBLE_SQL + """ THEN r.book_value_yen END)
                                         AS book_value_yen,
               count(*) FILTER (WHERE NOT (""" + PLAUSIBLE_SQL + """))
                                         AS excluded_positions,
               sum(CASE WHEN NOT (""" + PLAUSIBLE_SQL + """) THEN r.book_value_yen END)
                                         AS excluded_yen
        FROM eq_reclassified r JOIN current_filings f USING (doc_id)
        WHERE r.direction = 'to_pure'
    """, _year_params(year))
    return {"filers": filers,
            "totals": totals[0] if totals else None,
            "provenance": PROVENANCE,
            "basis": basis_mod.market_basis(),
            "names_note": NAMES_NOTE,
            "reclassified_note": RECLASSIFIED_NOTE,
            "flows_note": FLOWS_NOTE,
            "implausible_note": IMPLAUSIBLE_NOTE,
            "coverage_note": ("One filing per filer — its latest, or the one "
                              "covering ?year=. A filing repeats a "
                              "reclassification for several years after the "
                              "change, so this is the standing disclosed stock "
                              "of reclassified holdings, not one year's flow.")}


# ---------------------------------------------------------------------------
# The dataset's card (app/registry.py). Pure data: the formulas are the same
# constants the responses carry, so the card and the API cannot disagree.
# ---------------------------------------------------------------------------
EDINET_SOURCE = {
    "publisher": "Financial Services Agency of Japan — EDINET",
    "publisher_ja": "金融庁 EDINET",
    "url": "https://disclosure2.edinet-fsa.go.jp/",
    "license_note": ("Statutory disclosure documents published on EDINET under the "
                     "Financial Instruments and Exchange Act. Reproduced exactly as "
                     "filed; every row carries the doc_id of its source document and "
                     "the archived file's SHA-256. Filer errors are published as "
                     "filed and flagged, never corrected."),
}

MANIFEST = {
    "id": "cross-shareholdings",
    "section": "ownership",
    "name": {"en": "Cross-shareholdings", "ja": "政策保有株式"},
    "shape": "company",
    "summary": ("Every named policy shareholding disclosed in annual securities "
                "reports — shares, book value, purpose and whether the holding "
                "is reciprocal — both directions, one filing per company per "
                "fiscal year, from FY2021."),
    "source": dict(EDINET_SOURCE,
                   document="有価証券報告書 · 株式の保有状況 (annual securities report, "
                            "shareholding section)",
                   credit="Source: company filings on EDINET (Financial Services "
                          "Agency of Japan)."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2021", "stale_after_days": None,
    },
    "measures": [
        {"id": "shares", "label": "Shares held", "unit": "shares", "trust": "official"},
        {"id": "book_value_yen", "label": "Book value (carrying amount)", "unit": "JPY",
         "trust": "official"},
        {"id": "prior_shares", "label": "Shares held a year earlier", "unit": "shares",
         "trust": "official"},
        {"id": "purpose", "label": "Stated purpose of holding", "unit": "text",
         "trust": "official"},
        {"id": "holds_us", "label": "Reciprocal: the issuer holds the filer's shares",
         "unit": "boolean", "trust": "official"},
        {"id": "policy_total_yen", "label": "Total policy shareholdings, as filed",
         "unit": "JPY", "trust": "official"},
        {"id": "pct_outstanding", "label": "Stake as a share of the issuer's shares",
         "unit": "%", "trust": "derived", "calc": OWNERSHIP_CALC},
        {"id": "pct_of_holder_equity", "label": "Position as a share of the holder's equity",
         "unit": "%", "trust": "derived", "calc": HOLDER_SHARE_CALC},
        {"id": "pct_of_equity", "label": "Policy book as a share of shareholders' equity",
         "unit": "%", "trust": "derived", "calc": SCALE_EQUITY_CALC},
        {"id": "pct_of_assets", "label": "Policy book as a share of total assets",
         "unit": "%", "trust": "derived", "calc": SCALE_ASSETS_CALC},
        {"id": "corporate_action", "label": "Corporate action noted in the filing",
         "unit": "category", "trust": "derived", "calc": ACTION_CALC},
        {"id": "split_ratio", "label": "Implied split or consolidation ratio",
         "unit": "x", "trust": "derived", "calc": RATIO_CALC},
        {"id": "implausible", "label": "Filed figures mutually impossible",
         "unit": "boolean", "trust": "derived",
         "calc": ("book value ÷ shares of ¥%s or more — a filer scale error; the row "
                  "is returned as filed and excluded from yen aggregates"
                  % "{:,}".format(IMPLAUSIBLE_YEN_PER_SHARE))},
    ],
    "endpoints": {
        "company": "/api/v1/equity/company/{sec_code}",
        "search": "/api/v1/equity/companies",
        "summary": "/api/v1/equity/summary",
        "screen": "/api/v1/equity/unwind",
        "history": "/api/v1/equity/history",
        "reclassified": "/api/v1/equity/reclassified",
        "years": "/api/v1/equity/years",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [
        {"id": "unwind", "title": "Named policy-holding value change per filer"},
        {"id": "reclassified",
         "title": "Filers ranked by holdings reclassified to pure investment, not sold"},
    ],
    "cite": "/holdings.html?c={sec_code}",
    "page": "/holdings.html",
    "notes": [PROVENANCE["note"], SCALE_NOTE, NAMES_NOTE],
}


# ---------------------------------------------------------------------------
# Claim check
#
# The gap this closes: a figure quoted in the press and a figure from this
# product can differ by 4.5x and both be right. Nikkei Asia put the three
# megabanks' cross-shareholdings at ¥2.56tn at 30 September 2025; we hold
# ¥11.5085tn at 31 March 2026. Acquisition cost vs carrying amount, interim vs
# annual. Without somewhere to put that, one of those numbers gets quoted as a
# correction of the other.
#
# The design rule that makes this trustworthy: THIS CODE NEVER INFERS THE
# CLAIM'S BASIS. The caller states it, in typed fields, or does not state it —
# and where it is not stated we say the gap cannot be classified rather than
# picking the explanation that fits. `context` is echoed back and never parsed.
# ---------------------------------------------------------------------------

# Deliberately not a truth vocabulary. The strongest verdict available is
# "not consistent with any basis we hold": this reports what we can and cannot
# corroborate from filings, and never grades a publisher.
VERDICTS = {
    "consistent": "Matches a figure we hold, at the precision the claim states.",
    "date_mismatch": "We hold no figure at that date.",
    "basis_mismatch": "The claim is on a measurement basis we do not hold.",
    "scope_mismatch": "The claim covers a different entity or share scope.",
    "coverage_gap": "One or more companies are not in this dataset.",
    "basis_not_supplied": ("The claim's basis was not given, so the gap cannot "
                           "be classified."),
    "not_consistent": ("Dates and basis line up and the figures still differ."),
    "measure_not_held": ("This product holds sale proceeds for a fiscal year, "
                         "not a reduction in book value. No figure here can "
                         "confirm or contradict a book-value-reduction claim."),
}

CLAIM_NOTE = (
    "This compares a figure you supply with figures extracted from annual "
    "securities reports. It never infers the claim's measurement basis: state "
    "it in claimed_* or accept that the gap cannot be classified. A verdict "
    "is a statement about what this dataset can corroborate, never a judgement "
    "on the source of the claim."
)


def _sig_figures(value):
    """How many significant figures a published number is stated to.

    Published figures are rounded — ¥2.56tn is three significant figures — so
    a match is tested at the claim's OWN precision rather than against an
    arbitrary percentage tolerance.
    """
    if not value:
        return 1
    digits = str(int(abs(value))).rstrip("0")
    return max(1, len(digits))


def _rounds_to(ours, claim):
    """True when our figure rounds to the claim at the claim's precision."""
    if not ours or not claim:
        return False
    import math
    sig = _sig_figures(claim)
    exp = math.floor(math.log10(abs(ours))) - (sig - 1)
    step = 10 ** exp
    return abs(round(ours / step) * step - claim) < step / 2


@router.get("/claim-check")
def claim_check(figure_yen: float = Query(..., description="the figure to check, in yen"),
                companies: str = Query(..., description="comma-separated securities codes"),
                as_of: str = Query("", description="date the claim refers to, YYYY-MM-DD"),
                measure: str = Query("holdings", description="holdings | reduction"),
                claimed_measurement: str = Query("", description="carrying_amount | acquisition_cost"),
                claimed_entity_scope: str = Query("", description="parent_only | largest_holding_company | second_largest_holding_company | holdco_consolidated"),
                claimed_share_scope: str = Query("", description="listed | unlisted | both"),
                claimed_period_type: str = Query("", description="annual | interim"),
                context: str = Query("", description="the claim's own wording; echoed, never parsed")):
    """Check a published figure against what the filings support."""
    codes = [c.strip() for c in (companies or "").split(",") if c.strip()]
    if not codes:
        raise HTTPException(400, "no securities codes given")
    cur = _cur()

    target = None
    if as_of:
        try:
            target = datetime.date(*[int(x) for x in as_of.split("-")])
        except (ValueError, TypeError):
            raise HTTPException(400, "as_of must be YYYY-MM-DD")

    ours, missing, reasons = [], [], []
    for code in codes:
        rows = _rows(cur, """
            WITH tot AS (
                SELECT doc_id,
                       sum(book_value_yen) AS policy_total_yen,
                       sum(CASE WHEN share_class = 'listed'
                                THEN book_value_yen END) AS listed_yen
                FROM eq_filing_totals GROUP BY 1)
            SELECT f.sec_code, f.doc_id, f.period_end, f.sha256,
                   coalesce(f.filer_name_en, f.filer_name) AS name,
                   t.policy_total_yen, t.listed_yen
            FROM eq_filings f JOIN tot t USING (doc_id)
            WHERE f.sec_code = ? AND f.status IN ('clean','partial')""" + asof.clause("filed_date", "f") +
            asof.newest("f", "eq_filings", ok="newer.status IN ('clean','partial')") + """
              AND t.policy_total_yen IS NOT NULL
            ORDER BY f.period_end, f.filed_date, f.doc_id""", [code])
        if not rows:
            missing.append(code)
            continue
        if target:
            best = min(rows, key=lambda r: abs((r["period_end"] - target).days))
        else:
            best = rows[-1]
        entities = _rows(cur, "SELECT DISTINCT holder_table, share_class "
                              "FROM eq_filing_totals WHERE doc_id = ?",
                         [best["doc_id"]])
        best = dict(best)
        best["days_from_as_of"] = (
            (best["period_end"] - target).days if target else None)
        best["basis"] = basis_mod.basis_for_rows(
            entities, as_of=best["period_end"])
        best["periods_held"] = [r["period_end"] for r in rows]
        ours.append(best)

    if missing:
        reasons.append("coverage_gap")
    total = sum(r["policy_total_yen"] for r in ours if r["policy_total_yen"])

    # Date. The policy shareholding table exists only in the annual report, so
    # an interim as_of can never match, however close the calendar gets.
    worst_days = max((abs(r["days_from_as_of"]) for r in ours
                      if r["days_from_as_of"] is not None), default=None)
    if worst_days is not None and worst_days > 0:
        reasons.append("date_mismatch")

    # Basis. Stated or not stated — never guessed.
    if measure == "holdings":
        if not claimed_measurement:
            reasons.append("basis_not_supplied")
        elif claimed_measurement != basis_mod.EDINET_MEASUREMENT:
            reasons.append("basis_mismatch")
    if claimed_period_type and claimed_period_type != basis_mod.EDINET_PERIOD_TYPE:
        if "date_mismatch" not in reasons:
            reasons.append("date_mismatch")
    ours_scopes = {r["basis"]["entity_scope"] for r in ours}
    if claimed_entity_scope and claimed_entity_scope not in ours_scopes:
        reasons.append("scope_mismatch")
    if claimed_share_scope and claimed_share_scope not in ("both", ""):
        reasons.append("scope_mismatch")

    matched = bool(ours) and not reasons and _rounds_to(total, figure_yen)
    if matched:
        verdict = "consistent"
    elif not ours:
        verdict = "coverage_gap"
    elif reasons:
        verdict = "cannot_verify"
    elif _rounds_to(total, figure_yen):
        verdict = "consistent"
    else:
        verdict = "not_consistent"

    # A book-value-reduction claim has no counterpart here at all. Saying so is
    # the correct answer: it is what stops ¥160bn of interim, cost-basis
    # reduction being reported as contradicting ¥1.48tn of annual sale
    # proceeds, which is a different measure over a different period.
    if measure == "reduction":
        reasons.append("measure_not_held")
        verdict = "cannot_verify"

    return {
        "claim": {"figure_yen": figure_yen, "companies": codes,
                  "as_of": as_of or None, "measure": measure,
                  "claimed_basis": {
                      "measurement": claimed_measurement or None,
                      "entity_scope": claimed_entity_scope or None,
                      "share_scope": claimed_share_scope or None,
                      "period_type": claimed_period_type or None},
                  "context": context or None},
        "verdict": verdict,
        "reasons": [{"reason": r, "meaning": VERDICTS.get(r)}
                    for r in dict.fromkeys(reasons)],
        "ours": ours,
        "ours_total_yen": total if ours else None,
        "companies_not_covered": missing,
        "gap_yen": (total - figure_yen) if ours else None,
        "ratio_ours_to_claim": (round(total / figure_yen, 3)
                                if ours and figure_yen else None),
        "claim_note": CLAIM_NOTE,
        "no_reduction_measure": basis_mod.NO_REDUCTION_MEASURE,
        "provenance": PROVENANCE,
    }
