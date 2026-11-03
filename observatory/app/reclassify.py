# -*- coding: utf-8 -*-
"""Bring stored holder types up to the current rules, on a copy, then swap.

A register row's holder_kind (custody account, company, pension trust …) is
our reading of the filed name, not a filed value. When the rules learn a name
they misread — 2026-11-03: Moxley & Co., JPMorgan's ADR depositary nominee,
was read as a company in 28 filings, so Honda's "held through nominees" was
5.72 points low — the filings already read keep the old label, because the
nightly refresh only reads new ones.

This finds the filings whose stored types the current rules read differently
(equity/ownership_extract.py --reclassify), reads just those again from the
archive, and swaps the result in with the same copy → work → swap discipline
and update lock as every other write to the served file (app/backfill.py).
Every filed value comes out of the same archived file unchanged; only the
derived columns move. Nothing to do is one read of the register and an exit.

Run by the refresher after each nightly backfill; by hand:
    python -m app.reclassify
"""
import os
import sys

from . import backfill


def run():
    if not os.environ.get("EDINET_S3_BUCKET"):
        backfill.log("reclassify: no EDINET_S3_BUCKET; stored holder types stay as they are")
        return 0
    live = backfill.LIVE_EQUITY
    if not live.exists():
        return 0
    work = backfill.DATA_DIR / "equity.reclassify.duckdb"
    if backfill.fresh_copy(live, work) is None:
        return 0
    script = backfill.ROOT / "equity" / "ownership_extract.py"
    rc = backfill._run([sys.executable, str(script), "--db", str(work), "--source", "s3",
                        "--reclassify", "--no-compact"], dict(os.environ), backfill.ROOT)
    if rc == 3:
        backfill.log("reclassify: every stored holder type matches the current rules")
        backfill._discard(work)
        return 0
    if rc != 0:
        backfill.log("reclassify did not complete (exit %s); served data untouched" % rc)
        backfill._discard(work)
        return rc
    backfill.swap(work, live)
    backfill.log("reclassify: holder types brought up to the current rules")
    return 0


if __name__ == "__main__":
    sys.exit(run())
