# -*- coding: utf-8 -*-
"""US figures that cannot be true stay out: a cover share count on the wrong
scale for one filing, and a daily price of 0 (2026-11-07 scan: 114 one-off
cover slips, 249,342 zero prices)."""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "equity"))

from app.sec_shares import drop_slips  # noqa: E402
import us_prices_databento  # noqa: E402


def test_one_off_slip_is_dropped():
    assert drop_slips([(1, 145517832), (2, 145681), (3, 145723142)]) == [(1, 145517832), (3, 145723142)]
    assert drop_slips([(1, 21555392), (2, 21602012000000), (3, 21727157)]) == [(1, 21555392), (3, 21727157)]


def test_a_reverse_split_is_kept():
    counts = [(1, 100e6), (2, 1e6), (3, 1.01e6)]
    assert drop_slips(counts) == counts


def test_alternating_slips_keep_the_good_counts():
    got = drop_slips([(1, 326759644), (2, 10642), (3, 959509644), (4, 11908), (5, 336821595)])
    assert [n for _, n in got] == [326759644, 959509644, 336821595]


def test_newest_count_is_left_to_the_market_value_checks():
    counts = [(1, 52e6), (2, 52.1e6), (3, 52.1e9)]
    assert drop_slips(counts) == counts


def test_zero_prices_become_missing_and_volume_stays():
    con = duckdb.connect()
    con.execute(us_prices_databento.SCHEMA_SQL)
    con.execute("INSERT INTO us_px_daily VALUES ('ELME', '2026-10-23', 0, 0, 0, 0, 235550080, 1, 1, 1), "
                "('AAPL', '2026-10-23', 250.1, 252.0, 249.0, 251.5, 1000, 2, 1, 1)")
    us_prices_databento.blank_zero_prices(con)
    us_prices_databento.blank_zero_prices(con)
    rows = con.execute("SELECT symbol, open, close, volume FROM us_px_daily ORDER BY symbol").fetchall()
    assert rows == [("AAPL", 250.1, 251.5, 1000), ("ELME", None, None, 235550080)]
