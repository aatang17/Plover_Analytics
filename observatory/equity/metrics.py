# -*- coding: utf-8 -*-
u"""Run metrics and drift — noticing when a dataset quietly changes shape.

THE FAILURE THIS EXISTS FOR
---------------------------
Three things can go wrong with a feed, and until now we only caught the first.

  it stops    the job dies or the source is unreachable. Caught: the extractor
              records the failure, the last good data stays live, and the
              freshness clock stops advancing.
  it empties  the source renames a field. The parser still runs, still reports
              `clean`, and one column is now blank. Not caught by anything.
  it lies     the field is full and the values are wrong. Not caught either.

`canary.py` covers a handful of known filings exactly. This covers the whole
dataset approximately: after every run it writes down how many rows each table
holds, what share of each COLUMN came back non-null, and the mix of clean,
partial and failed. Compare that against recent runs and a field that stops
populating shows up the same night instead of the next quarter.

Two real cases from the week these extractors were written, both of which this
would have caught:

  * the board's opinion on a takeover was read on 4% of filings instead of 92%,
    because the element name moved between taxonomy years;
  * 285 allottee rows stored the literal word 名称 as the buyer's name, because
    a merged label cell repeats across the columns it spans.

WHY IT IS COMPUTED, NOT CONFIGURED
----------------------------------
The column list comes from the database schema, never from a hand-maintained
list of fields to watch. A list like that is correct on the day it is written
and wrong by the second dataset, and the field nobody remembered to add is
exactly the one that breaks.

WHY IT ONLY WARNS
-----------------
Drift never fails a run. A quiet week with no takeover bids is not a broken
parser, and a check that can block an ingest would cost a day of data the first
time it cried wolf and be switched off within the month. It flags, it is
reported, and a human decides.
"""
import argparse
import datetime as dt
import os

import duckdb

from extract import DB_PATH

# Which extractor owns which tables. A table nobody claims is still measured,
# under `unassigned`, because an orphan table is itself worth seeing.
OWNED = {
    "cross-shareholdings": ["eq_entities", "eq_filings", "eq_holdings",
                            "eq_filing_totals", "eq_filing_flows"],
    "shareholder-register": ["eq_own_filings", "eq_major_shareholders",
                             "eq_own_category"],
    "boards-and-pay": ["eq_company_year", "eq_board", "eq_pay_category",
                       "eq_pay_named"],
    "facilities": ["eq_fac_filings", "eq_facilities"],
    "rental-property": ["eq_rental_filings", "eq_rental_tables"],
    "5pct-filings": ["eq_lvh_filings", "eq_lvh_holders"],
    "financials": ["eq_fin_filings", "eq_fin_facts", "eq_fin_lines"],
    "agm-votes": ["eq_agm_meetings", "eq_agm_proposals", "eq_agm_votes"],
    "tender-offers": ["eq_toi_filings"],
    "semiannual": ["eq_ssr_filings", "eq_ssr_facts"],
    "capital-raises": ["eq_issue_filings", "eq_issue_allottees"],
    "corporate-events": ["eq_event_filings", "eq_event_shareholders",
                         "eq_event_officers", "eq_event_parties"],
    "segments": ["eq_seg_filings", "eq_seg_regions", "eq_seg_customers",
                 "eq_seg_products"],
    "debt-notes": ["eq_debt_note_filings", "eq_debt_note_rows"],
    "buybacks": ["eq_buyback_filings", "eq_buyback_programs",
                 "eq_buyback_treasury"],
    "classification": ["eq_classification", "eq_index_member"],
    "tdnet": ["eq_tdnet_items", "eq_tdnet_filings", "eq_tdnet_facts"],
}

SCHEMA_SQL = u"""
CREATE TABLE IF NOT EXISTS eq_extract_metrics (
    extractor VARCHAR, ran_at TIMESTAMP, table_name VARCHAR,
    metric VARCHAR, key VARCHAR, value BIGINT, total BIGINT);
CREATE TABLE IF NOT EXISTS eq_extract_drift (
    extractor VARCHAR, ran_at TIMESTAMP, table_name VARCHAR, kind VARCHAR,
    key VARCHAR, was DOUBLE, now_pct DOUBLE, detail VARCHAR);
"""

# A small table swings wildly: a day with three filings can move a fill rate
# thirty points without anything being wrong. Below this, drift is not reported.
MIN_ROWS = 200
# How far a column's fill rate must fall, in percentage points, before it is
# worth a human's attention.
FILL_DROP_PP = 15.0
# The same for the share of rows that came out partial or failed.
STATUS_RISE_PP = 10.0
# How many previous runs to compare against. A median over several runs is
# steadier than the single run before, which may itself have been the broken one.
WINDOW = 5


def _tables(con):
    return {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables()").fetchall()}


def snapshot(db_path, when=None):
    u"""Measure every owned table and store one run's numbers.

    Returns the number of measurement rows written. Never raises on a missing
    table: a dataset that has not been built yet is simply not measured.
    """
    when = when or dt.datetime.now()
    con = duckdb.connect(db_path)
    try:
        con.execute(SCHEMA_SQL)
        have = _tables(con)
        claimed = {t for ts in OWNED.values() for t in ts}
        plan = list(OWNED.items())
        orphans = sorted(t for t in have
                         if t.startswith("eq_") and t not in claimed
                         and not t.startswith(("eq_extract_", "eq_prior",
                                               "eq_guard", "eq_artifact",
                                               "eq_release", "eq_series")))
        if orphans:
            plan.append(("unassigned", orphans))

        rows = []
        for extractor, tables in plan:
            for table in tables:
                if table not in have:
                    continue
                n = con.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
                if not n:
                    rows.append((extractor, when, table, "fill", "_rows", 0, 0))
                    continue
                cols = [r[1] for r in con.execute(
                    "PRAGMA table_info('%s')" % table).fetchall()]
                exprs = ", ".join("count(%s)" % c for c in cols)
                counts = con.execute(
                    "SELECT %s FROM %s" % (exprs, table)).fetchone()
                for col, non_null in zip(cols, counts):
                    rows.append((extractor, when, table, "fill", col,
                                 int(non_null), int(n)))
                if "status" in cols:
                    for st, k in con.execute(
                            "SELECT status, count(*) FROM %s GROUP BY 1" % table).fetchall():
                        rows.append((extractor, when, table, "status",
                                     st or "null", int(k), int(n)))
        if rows:
            con.executemany(
                "INSERT INTO eq_extract_metrics VALUES (?,?,?,?,?,?,?)", rows)
        return len(rows)
    finally:
        con.close()


def drift(db_path, extractor=None):
    u"""[{extractor, table, kind, key, was, now, detail}] for the latest run.

    `was` is the median of the previous runs in the window, not the single run
    before it: comparing against one earlier run means a broken run becomes the
    baseline the next night and the problem disappears from view.
    """
    if not os.path.exists(db_path):
        return []
    con = duckdb.connect(db_path, read_only=True)
    try:
        if "eq_extract_metrics" not in _tables(con):
            return []
        where = "WHERE extractor = ?" if extractor else ""
        params = [extractor] if extractor else []
        runs = con.execute(
            "SELECT extractor, max(ran_at) FROM eq_extract_metrics %s "
            "GROUP BY 1" % where, params).fetchall()
        out = []
        for ext, latest in runs:
            prior = [r[0] for r in con.execute(
                "SELECT DISTINCT ran_at FROM eq_extract_metrics "
                "WHERE extractor = ? AND ran_at < ? "
                "ORDER BY ran_at DESC LIMIT %d" % WINDOW,
                [ext, latest]).fetchall()]
            if not prior:
                continue                 # first run: nothing to compare against
            now = {(r[0], r[1], r[2]): (r[3], r[4]) for r in con.execute(
                "SELECT table_name, metric, key, value, total "
                "FROM eq_extract_metrics WHERE extractor = ? AND ran_at = ?",
                [ext, latest]).fetchall()}
            past = {}
            for r in con.execute(
                    "SELECT table_name, metric, key, "
                    "       median(value * 100.0 / nullif(total, 0)), median(total) "
                    "FROM eq_extract_metrics WHERE extractor = ? AND ran_at IN "
                    "(SELECT DISTINCT ran_at FROM eq_extract_metrics "
                    " WHERE extractor = ? AND ran_at < ? "
                    " ORDER BY ran_at DESC LIMIT %d) GROUP BY 1,2,3" % WINDOW,
                    [ext, ext, latest]).fetchall():
                past[(r[0], r[1], r[2])] = (r[3], r[4])

            for key, (value, total) in sorted(now.items()):
                table, metric, col = key
                if total < MIN_ROWS:
                    continue
                pct = value * 100.0 / total
                was = past.get(key)
                if was is None:
                    if metric == "fill" and col != "_rows":
                        out.append({"extractor": ext, "table": table,
                                    "kind": "new-column", "key": col,
                                    "was": None, "now": round(pct, 1),
                                    "detail": "column not present in earlier runs"})
                    continue
                was_pct = was[0] or 0.0
                if metric == "fill" and col != "_rows":
                    if was_pct - pct >= FILL_DROP_PP:
                        out.append({
                            "extractor": ext, "table": table,
                            "kind": "empty-column" if pct == 0 else "fill-drop",
                            "key": col, "was": round(was_pct, 1),
                            "now": round(pct, 1),
                            "detail": "%s fell %.1f points" % (col, was_pct - pct)})
                elif metric == "status" and col in ("partial", "failed"):
                    if pct - was_pct >= STATUS_RISE_PP:
                        out.append({
                            "extractor": ext, "table": table,
                            "kind": "status-rise", "key": col,
                            "was": round(was_pct, 1), "now": round(pct, 1),
                            "detail": "%s rose %.1f points" % (col, pct - was_pct)})
            # a column that used to be measured and is now gone entirely
            for key, was in sorted(past.items()):
                table, metric, col = key
                if metric != "fill" or col == "_rows" or key in now:
                    continue
                if (was[1] or 0) >= MIN_ROWS:
                    out.append({"extractor": ext, "table": table,
                                "kind": "column-gone", "key": col,
                                "was": round(was[0] or 0.0, 1), "now": None,
                                "detail": "%s is no longer in the table" % col})
        return out
    finally:
        con.close()


def snapshot_and_flag(db_path, when=None):
    u"""Measure this run, compare it with recent ones, and STORE the flags.

    Storing them is what lets the API surface drift without importing this
    module: `app/` reads `eq_extract_drift` with plain SQL, out of the same
    database file, and the comparison logic stays in one place.

    Only the latest run's flags are kept per extractor. Drift is a statement
    about now, not a log; an old flag that nobody acted on is noise, and the
    measurements it was derived from are still in eq_extract_metrics.
    """
    when = when or dt.datetime.now()
    n = snapshot(db_path, when)
    flags = drift(db_path)
    con = duckdb.connect(db_path)
    try:
        con.execute(SCHEMA_SQL)
        seen = {f["extractor"] for f in flags}
        for ext in seen | set(OWNED):
            con.execute("DELETE FROM eq_extract_drift WHERE extractor = ?", [ext])
        if flags:
            con.executemany(
                "INSERT INTO eq_extract_drift VALUES (?,?,?,?,?,?,?,?)",
                [(f["extractor"], when, f["table"], f["kind"], f["key"],
                  f["was"], f["now"], f["detail"]) for f in flags])
    finally:
        con.close()
    return n, flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--snapshot", action="store_true",
                    help="measure the tables and store this run's numbers")
    ap.add_argument("--report", action="store_true",
                    help="print drift against recent runs")
    ap.add_argument("--extractor")
    args = ap.parse_args()

    if args.snapshot:
        print("recorded %d measurements" % snapshot(args.db))
    if args.report or not args.snapshot:
        flags = drift(args.db, args.extractor)
        if not flags:
            print("no drift against the last %d runs" % WINDOW)
        for f in flags:
            print("%-18s %-22s %-14s %-26s was %s now %s"
                  % (f["extractor"], f["table"], f["kind"], f["key"],
                     f["was"], f["now"]))
        print("\nDrift is a warning, never a failure: a quiet week is not a "
              "broken parser. Look at the column before changing anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
