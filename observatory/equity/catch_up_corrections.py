# -*- coding: utf-8 -*-
"""Read the corrections already in the archive that a reader has not stored.

Until 2026-10-24 the annual-report readers took doc type 120 only and the
buyback reader 220 only, so every correction filed before then
(訂正有価証券報告書 130, 訂正自己株券買付状況報告書 230) sits in the archive
unread. The nightly run reads new ones by itself; this reads the old ones, a
batch per reader per run, for listed companies (the readers' main population;
an unlisted company's correction still arrives with the nightly run from now
on). Each reader stores a correction as a filing of its own beside the
original, so nothing stored is touched.

    ./.venv/bin/python equity/catch_up_corrections.py --db data/equity.work.duckdb
    ./.venv/bin/python equity/catch_up_corrections.py --db /tmp/copy.duckdb --source local --per-run 50

Prints one "landed <reader> <n>" line per reader that stored something and
exits 0; exits 3 when no reader has anything left to read. app/backfill.py
runs it on a copy of the served database and swaps the copy in when something
landed.
"""
import argparse
import os
import subprocess
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract import LocalSource, S3Source  # noqa: E402

# (script, filings table, doc type, extra args): the nightly run's own flags
# (refresh_equity.py), financials before segments, which reads its revenue.
READERS = [
    ("fin_extract.py",       "eq_fin_filings",     "130", []),
    ("extract.py",           "eq_filings",         "130", ["--all"]),
    ("ownership_extract.py", "eq_own_filings",     "130", ["--all"]),
    ("board_extract.py",     "eq_company_year",    "130", ["--all"]),
    ("facility_extract.py",  "eq_fac_filings",     "130", ["--all"]),
    ("rental_extract.py",    "eq_rental_filings",  "130", ["--all"]),
    ("seg_extract.py",       "eq_seg_filings",     "130", ["--all"]),
    ("risk_extract.py",      "eq_risk_filings",    "130", []),
    ("buyback.py",           "eq_buyback_filings", "230", []),
]


def held(db, table):
    con = duckdb.connect(db, read_only=True)
    try:
        have = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        if table not in have:
            return set()
        return {r[0] for r in con.execute("SELECT doc_id FROM %s" % table).fetchall()}
    finally:
        con.close()


def corrections(src):
    """{doc type: [docID, oldest first]} of listed companies' corrections."""
    out = {"130": [], "230": []}
    for d, r in src.list_metadata().items():
        kind = r.get("docTypeCode")
        if kind in out and r.get("secCode") and not r.get("fundCode"):
            out[kind].append((r.get("submitDateTime") or "", d))
    return {k: [d for _, d in sorted(v)] for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", choices=("local", "s3"), default="s3")
    ap.add_argument("--per-run", type=int, default=1500, help="corrections per reader this run")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    src = S3Source(a.workers) if a.source == "s3" else LocalSource()
    archived = set(src.filings())
    wanted = corrections(src)
    landed = pending_any = 0
    for script, table, kind, extra in READERS:
        before = held(a.db, table)
        # a correction not in the archive's CSV packages (PDF only) is never
        # readable; buyback corrections are found by buyback.py's own listing
        todo = [d for d in wanted[kind] if d not in before and (kind == "230" or d in archived)]
        if not todo:
            print("%-22s nothing to read" % script, flush=True)
            continue
        pending_any += 1
        batch = todo[:a.per_run]
        cmd = [sys.executable, os.path.join(HERE, script), "--source", a.source,
               "--workers", str(a.workers), "--docs", ",".join(batch)] + extra
        env = dict(os.environ)
        if script == "buyback.py":
            env["EQUITY_DB_PATH"] = os.path.abspath(a.db)        # it takes no --db
        else:
            cmd += ["--db", a.db, "--no-compact"]
        print("%-22s %d of %d corrections" % (script, len(batch), len(todo)), flush=True)
        r = subprocess.run(cmd, env=env, cwd=HERE)
        got = len(held(a.db, table) - before)
        if r.returncode != 0:
            print("%-22s FAILED (exit %d) after storing %d" % (script, r.returncode, got), flush=True)
        if got:
            landed += got
            print("landed %s %d" % (script, got), flush=True)
    if not pending_any:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
