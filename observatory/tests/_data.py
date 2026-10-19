# -*- coding: utf-8 -*-
"""Which databases this machine has, and the only reasons a test may skip
because it hasn't got them.

CI has no data at all: no volume, no DuckDB file, no source fetch. Eighty-odd
of these tests need numbers to mean anything, so they have to say so — and a
skip is the quietest way there is to delete a test. That is why the reason
strings live here rather than being typed at each decorator:

  * ``ci/suite.py --mode ci`` fails the run on a skip whose reason is not in
    ``REASONS``. Nothing can be switched off by inventing a new excuse.
  * ``ci/suite.py --mode full`` fails the run on a skip whose reason *is* in
    ``REASONS``. On the machine that holds the data, every one of them must
    actually execute.

So a test guarded by one of these flags is not a test we have given up on. It
is a test that runs before every push instead of on every commit.
"""
from app import db, equity_api, sec_api


def _has_macro():
    """A macro database with something published in it.

    Existence is not enough: a fresh volume carries an empty file, and a test
    that asks for CPI would fail rather than skip. Falls back to existence if
    the file cannot be read — a locked database on a working machine should
    run the tests and let them complain, not skip them.
    """
    if not db.DB_PATH.exists():
        return False
    try:
        import duckdb
        con = duckdb.connect(str(db.DB_PATH), read_only=True)
        try:
            return bool(con.execute(
                "SELECT count(*) FROM releases WHERE status = 'published'"
            ).fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — an unreadable file is not a verdict
        return True


MACRO = _has_macro()
EQUITY = equity_api.DB_PATH.exists()
SEC = sec_api.DB_PATH.exists()

NO_MACRO = "no macro database"
NO_EQUITY = "equity database not present"
NO_BOTH = "no macro and equity databases"

# A whole database is absent. This is CI's condition, and the only one that
# ``ci/suite.py --mode full`` refuses outright: a machine without the files is
# not the machine that gets to approve a push.
ABSENT = (
    NO_MACRO,
    NO_EQUITY,
    NO_BOTH,
    "equity database not built",
    "data/sec.duckdb not built",
)

# The databases are here but a slice inside them was never built locally. A
# different thing entirely, and worth naming separately: it means real tests
# are not running on the machine that is supposed to run everything, and the
# fix is a command rather than a deploy. ``--mode full`` prints these every
# run and ``--strict`` turns them into failures.
PARTIAL = {
    "no classification in data/equity.duckdb":
        "python equity/class_extract.py  (JPX segments and TOPIX scale bands)",
    "no published population-jp release":
        "python -m app.ingest population-jp",
    # The US shelf (data/sec.duckdb) without one of its slices. CI has no
    # data/sec.duckdb at all; the machine with the data builds each slice so.
    "data/sec.duckdb without the universe tables":
        "python equity/sec_universe.py --db data/sec.work.duckdb  (then swap it in)",
    "data/sec.duckdb without insider, 13F and universe tables":
        "python equity/sec_universe.py, equity/sec_insiders_extract.py and equity/sec_13f_extract.py",
    "data/sec.duckdb without US prices":
        "python equity/us_prices_databento.py --quote, then with --max-cost-usd (it costs money)",
    "data/sec.duckdb without prices and index members":
        "python equity/us_prices_databento.py and equity/us_index_members.py",
    "data/sec.duckdb without the full US set":
        "the universe, prices, index members, insider and 13F slices above",
}

# Every reason a skip may carry for want of data. Add a string here only if it
# means exactly that; anything else belongs in a failing test.
REASONS = ABSENT + tuple(PARTIAL)
