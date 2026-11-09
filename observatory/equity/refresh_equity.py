# -*- coding: utf-8 -*-
"""The nightly equity refresh — every EDINET-derived dataset, incrementally.

Why this exists
---------------
The capture jobs archive EDINET every day and have done so reliably. The
extractors that turn that archive into the served database were hand-run on a
laptop, and in August 2026 that gap showed: capture moved to the cloud bucket
on the 6th, the extractors kept being pointed at the laptop's frozen copy, and
the 5% filings silently stopped four weeks short of the archive while every
dashboard still looked healthy. Nothing was broken; nobody was running it.

So this runs where the data is served, on the same clock as everything else.
`observatory/start.sh` calls it once per refresh cycle, in the window where
the server is stopped and the database has a single writer.

Why subprocesses
----------------
Each extractor is a program with its own argv, module-level state and parser
version. Running them in-process would couple seven parsers into one failure
domain; as subprocesses, a parser that throws costs its own dataset and
nothing else. That matters more than the startup cost: this job must never be
the reason the site fails to come back up, so a failing extractor is reported
and stepped over, exactly as a failing ingest is.

Cost
----
Incremental. Discovery lists the bucket once and shares it (EDINET_LISTING_CACHE),
each extractor takes only documents archived since its own recorded watermark,
and the database is compacted once at the end rather than seven times. A
routine night is one day of filings — roughly 130 documents.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# (script, label, extra args). Order matters only at the front: extract.py
# refreshes the entity registry and eq_company_year that the other annual-report
# extractors read for company identity, so it goes first.
EXTRACTORS = [
    ("extract.py",            "cross-shareholdings",  ["--all"]),
    ("ownership_extract.py",  "shareholder-register", ["--all"]),
    ("board_extract.py",      "boards-and-pay",       ["--all"]),
    ("facility_extract.py",   "facilities",           ["--all"]),
    ("rental_extract.py",     "rental-property",      ["--all"]),
    # These three were held out of the boot path because an extractor with
    # no watermark took `since = None` and set off through the whole archive
    # with the port still closed — financials met an empty table on
    # 2026-09-03, the 15-minute healthcheck window expired, and the site went
    # down. EQUITY_CATCH_UP_DAYS removes that failure mode at the source: a
    # cold start now takes the newest slice and stops (see select_pending in
    # extract.py), so the first night costs the same bounded minutes as any
    # other and the history arrives a slice at a time instead of in one
    # unbounded pass. They are safe here ONLY while that budget is set; with
    # EQUITY_CATCH_UP_DAYS unset the old cold-start behaviour returns.
    ("lvh_extract.py",        "5pct-filings",         []),
    ("fin_extract.py",        "financials",           []),
    ("agm_extract.py",        "agm-votes",            []),
    # Tender offers: the offer, its amendments, the target board's opinion and
    # the result. Same cold-start budget as the three above — it reads t1
    # packages and has no watermark on a fresh database.
    ("toi_extract.py",        "tender-offers",        []),
    # Semiannual reports — the only statutory accounts between annual reports
    # since quarterlies were abolished. Its own tables, never mixed into the
    # annual financials (see the module docstring).
    ("ssr_extract.py",        "semiannual",           ["--all"]),
    # Capital raises: new shares, who they go to, and at what price.
    ("issue_extract.py",      "capital-raises",       []),
    # The other half of the 臨時報告書 pile: mergers, share exchanges,
    # subsidiary and parent moves, chief-executive and major-shareholder
    # changes. agm_extract.py above reads the AGM half of the same documents.
    ("event_extract.py",      "corporate-events",     []),
    # Segment notes (revenue by region, named customers, product segments).
    ("seg_extract.py",        "segments",             ["--all"]),
    # Borrowings from the IFRS financial-liabilities note, for net cash where
    # the balance sheet prints no borrowing line. After financials: it reads
    # only the IFRS filings that extractor has stored, against their face.
    ("debt_note_extract.py",  "debt-notes",           []),
    # Business risks (事業等のリスク): the section's text, cut into the filer's
    # own items. Same t1 package and cold-start budget as the extractors
    # above; companies with a securities code only (see the module docstring).
    ("risk_extract.py",       "business-risks",       []),
    ("buyback.py",            "buybacks",             []),
    # Not an EDINET extractor at all: two small files fetched straight from
    # JPX and Nikkei. It sits at the end because nothing else reads what it
    # writes, and it is as fail-safe as the rest — an unreachable publisher
    # leaves the standing vintage live.
    ("class_extract.py",      "classification",       []),
    # Not EDINET either: the TDnet wire, whose 31-day public retention makes
    # our archive the only copy. Day-partitioned rather than watermarked per
    # document, and cheap — a nightly run reads a handful of days.
    ("tdnet_extract.py",      "tdnet",                []),
    # JPX's daily short-position disclosure. Not EDINET either, and in the
    # same position as TDnet: the exchange deletes each daily file after about
    # twelve business days, so a night this does not run is a day of the
    # record that nobody can recover. Cheap — one page and the days we are
    # missing from it, usually one.
    ("short_extract.py",      "short-positions",      []),
    # JPX's earnings calendar (決算発表予定日): one small workbook per
    # period-end month, re-issued as dates change. Every version is kept, so a
    # moved date stays visible. One page and a few workbooks a night.
    ("calendar_extract.py",   "earnings-calendar",    []),
    # Bank balance-sheet notes: bond maturities, unrealised gains, deposits by
    # term. Reads only the bank-industry filers extract.py has registered, so
    # it is cheap, and runs after extract.py for that reason.
    ("bank_extract.py",       "bank-balance",         []),
    # Each bank's Basel III rate-risk table, from its own disclosure PDF.
    # Skips institutions already read cleanly by this parser version, so a
    # night costs only the ones still missing; needs pdftotext (Dockerfile).
    ("irrbb_collect.py",      "bank-irrbb",           []),
]

# buyback.py takes neither --db nor --no-compact; it reads EQUITY_DB_PATH and
# never compacts on its own.
NO_DB_FLAG = {"buyback.py"}


def run_one(script, label, extra, args, env):
    cmd = [sys.executable, os.path.join(HERE, script),
           "--source", args.source, "--workers", str(args.workers)]
    if args.new_only:
        cmd.append("--new-only")
    if script not in NO_DB_FLAG:
        cmd += ["--db", args.db, "--no-compact"]
    cmd += extra
    print("\n=== %s (%s) ===" % (label, script), flush=True)
    started = time.time()
    try:
        r = subprocess.run(cmd, env=env, cwd=HERE)
    except Exception as e:                                   # noqa: BLE001
        print("EQUITY %s did not run: %s" % (label, e), flush=True)
        return False
    took = time.time() - started
    if r.returncode != 0:
        print("EQUITY %s FAILED (exit %d, %.0fs) — its previous data stays live"
              % (label, r.returncode, took), flush=True)
        return False
    print("EQUITY %s ok (%.0fs)" % (label, took), flush=True)
    return True


def watermark(db_path):
    """The furthest archive date any extractor in this file has read, or None."""
    import duckdb
    if not os.path.exists(db_path):
        return None
    con = duckdb.connect(db_path, read_only=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        if "eq_extract_runs" not in names:
            return None
        return con.execute("SELECT max(through_date) FROM eq_extract_runs").fetchone()[0]
    except Exception:                                        # noqa: BLE001
        return None
    finally:
        con.close()


def adopt_seed(seed_path, db_path):
    """Install the shipped seed only when it is genuinely ahead of the volume.

    The old rule was "copy the seed over the volume on every boot", which was
    right while extraction happened offline and the image was always the
    newest thing anyone had. It is now wrong and dangerous: the volume copy is
    topped up nightly, so an unconditional copy would throw away every night
    of extraction since the image was built, and keep doing it on every
    redeploy.

    So compare watermarks and take the newer. A seed built from a fresh
    offline re-extraction (a parser fix, say) still wins and is still how a
    rebuild reaches production; a seed that has fallen behind is ignored.
    """
    if not os.path.exists(seed_path):
        return
    live, shipped = watermark(db_path), watermark(seed_path)
    if not os.path.exists(db_path):
        why = "no equity database on the volume yet"
    elif live is None and shipped is not None:
        why = "the volume copy predates run tracking"
    elif shipped is not None and live is not None and shipped > live:
        why = "the shipped seed reads through %s, the volume through %s" % (shipped, live)
    else:
        print("EQUITY volume database kept (through %s; seed through %s)"
              % (live, shipped))
        return
    print("EQUITY installing the shipped seed: %s" % why)
    shutil.copyfile(seed_path, db_path)


def coverage(db_path):
    """What each extractor now says it has read, straight from the database."""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        if "eq_extract_runs" not in names:
            return []
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info('eq_extract_runs')").fetchall()}
        depth = "back_to_date" if "back_to_date" in cols else "NULL"
        return con.execute(
            "SELECT extractor, through_date, ran_at, %s FROM eq_extract_runs "
            "ORDER BY extractor" % depth).fetchall()
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("local", "s3"), default="s3")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--db", default=os.environ.get(
        "EQUITY_DB_PATH",
        os.path.join(HERE, "..", "data", "equity.duckdb")))
    ap.add_argument("--full", action="store_true",
                    help="re-extract the whole archive instead of resuming "
                         "from each extractor's watermark (hours, not minutes)")
    ap.add_argument("--only", help="comma-separated labels to run")
    ap.add_argument("--skip", help="comma-separated labels NOT to run. start.sh "
                                   "passes the bank rate-risk collector here: it "
                                   "reads bank WEBSITES, not the archive, so its "
                                   "time is not ours to predict and it has no place "
                                   "in the window before the port opens. "
                                   "app/backfill.py runs the full list after.")
    ap.add_argument("--seed", help="shipped seed database to install first, but "
                                   "only if it reads further than the live one")
    ap.add_argument("--no-compact", action="store_true")
    args = ap.parse_args()
    args.new_only = not args.full
    args.db = os.path.abspath(args.db)

    # Seed adoption happens even when there is no bucket: a container with no
    # credentials must still serve the data the image shipped with.
    if args.seed:
        adopt_seed(args.seed, args.db)

    if args.source == "s3" and not os.environ.get("EDINET_S3_BUCKET"):
        print("EQUITY refresh skipped: no EDINET_S3_BUCKET configured "
              "(serving whatever the database already holds)")
        return 0

    env = dict(os.environ)
    env["EQUITY_DB_PATH"] = args.db
    # One bucket listing shared by all seven, expiring long before the next run.
    env.setdefault("EDINET_LISTING_CACHE",
                   os.path.join(os.path.dirname(args.db), "listing-cache"))

    todo = EXTRACTORS
    if args.skip:
        drop = {x.strip() for x in args.skip.split(",") if x.strip()}
        todo = [e for e in todo if e[1] not in drop]
        print("skipping: %s" % ", ".join(sorted(drop)), flush=True)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        todo = [e for e in EXTRACTORS if e[1] in want]

    started = time.time()
    ok = [label for script, label, extra in todo
          if run_one(script, label, extra, args, env)]
    failed = [label for _, label, _ in todo if label not in ok]

    # Compact once, after everything: each extractor deletes and reinserts its
    # rows, and DuckDB does not hand that space back on its own.
    if ok and not args.no_compact:
        sys.path.insert(0, HERE)
        from extract import compact
        try:
            compact(args.db)
        except Exception as e:                               # noqa: BLE001
            print("EQUITY compaction skipped: %s" % e, flush=True)

    print("\n--- equity refresh: %d ok, %d failed, %.0fs ---"
          % (len(ok), len(failed), time.time() - started))
    for extractor, through, ran, back_to in coverage(args.db):
        # Two numbers, not one: how RECENT the dataset is and how DEEP it
        # goes. A dataset can be a day fresh and three months deep, which is
        # what the 5% filings were while 74,000 filings sat unread in the
        # bucket and every freshness check said ok.
        print("  %-22s archive read %s .. %s"
              % (extractor, back_to or "(depth not recorded)", through))
    if failed:
        print("ATTENTION equity extractors failed: %s" % ", ".join(failed))

    # --- did anything change shape tonight? ------------------------------
    # A failed extractor announces itself. The dangerous failure is the one
    # that does not: the parser runs, reports clean, and a column is quietly
    # empty because the source renamed a field. Two checks for that, both
    # advisory, neither able to stop the server coming back up.
    trouble = []
    try:
        sys.path.insert(0, HERE)
        import metrics
        _n, _flags = metrics.snapshot_and_flag(args.db)
        for f in _flags:
            trouble.append("%s/%s %s: %s was %s%%, now %s%%"
                           % (f["extractor"], f["table"], f["kind"], f["key"],
                              f["was"], f["now"]))
    except Exception as e:                                   # noqa: BLE001
        print("EQUITY drift check skipped: %s" % e, flush=True)

    drift_flags = len(trouble)
    canary_total = canary_failed = 0
    try:
        import canary
        with open(canary.EXPECTED, encoding="utf-8") as f:
            cases = json.load(f)["canaries"]
        canary_total = len(cases)
        for case in cases:
            try:
                good, diffs = canary.run_one(case, args.source)
            except Exception as e:                           # noqa: BLE001
                good, diffs = False, [("(error)", "", str(e)[:100])]
            if not good:
                canary_failed += 1
                for field, want, got in diffs:
                    trouble.append("canary %s: %s expected %r, got %r"
                                   % (case["name"], field, want, got))
    except Exception as e:                                   # noqa: BLE001
        print("EQUITY canaries skipped: %s" % e, flush=True)

    if trouble:
        print("\nATTENTION equity data changed shape "
              "(%d/%d canaries failed, %d drift flags):"
              % (canary_failed, canary_total, drift_flags), flush=True)
        for line in trouble[:20]:
            print("  %s" % line, flush=True)
        if len(trouble) > 20:
            print("  ... and %d more" % (len(trouble) - 20), flush=True)
    else:
        print("\nshape checks: %d canaries pass, no drift" % canary_total,
              flush=True)

    # The heartbeat is the only thing that reaches a human. Failed extractors
    # and broken canaries both ping the failure endpoint; drift alone does not,
    # because a warning that pages someone weekly stops being read.
    try:
        import heartbeat
        heartbeat.ping("equity refresh: %d ok, %d failed, %d canaries failed, "
                       "%d drift flags" % (len(ok), len(failed),
                                           canary_failed, drift_flags),
                       failed=bool(failed) or canary_failed > 0)
    except Exception as e:                                   # noqa: BLE001
        print("EQUITY heartbeat skipped: %s" % e, flush=True)

    # Always 0: a failed extractor leaves its previous data live and must not
    # stop the server coming back up. The ATTENTION lines above are the signal.
    return 0


if __name__ == "__main__":
    sys.exit(main())
