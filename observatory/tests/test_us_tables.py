# -*- coding: utf-8 -*-
"""The step route's extra tables (app/us_tables.py): segment names, the
derived fourth quarter, the 13F split and identifier-change guards, and the
shared sandbox's lock-down. All in memory; no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_tables
"""
import datetime
import unittest
from unittest import mock

import duckdb

from app import us_tables

D = datetime.date


class SegmentNames(unittest.TestCase):
    def test_names_read_as_written(self):
        n = us_tables.segment_name
        self.assertEqual(n("msft:ProductivityAndBusinessProcessesMember"), "Productivity and Business Processes")
        self.assertEqual(n("aapl:IPhoneMember"), "iPhone")
        self.assertEqual(n("NonUs"), "Non-US")
        self.assertEqual(n("GreaterChinaSegment"), "Greater China")
        self.assertEqual(n("RestOfAsiaPacificSegment"), "Rest of Asia Pacific")

    def test_country_codes_are_named_only_as_geographies(self):
        self.assertEqual(us_tables.segment_name("CN", "geography"), "China (CN)")
        self.assertEqual(us_tables.segment_name("country:JP"), "Japan (JP)")
        self.assertEqual(us_tables.segment_name("CN", "product"), "CN")


def _quarters_db():
    """One company: three reported quarters and the full year (so Q4 is
    derived), the year before the same way, and EPS on the reported ones."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE sec_filings (adsh VARCHAR, cik BIGINT, form VARCHAR, status VARCHAR, filed DATE,
                   fy INTEGER, fp VARCHAR, period DATE)""")
    con.execute("""CREATE TABLE sec_facts (adsh VARCHAR, tag VARCHAR, ddate DATE, qtrs INTEGER, uom VARCHAR,
                   value DOUBLE, segment_id BIGINT, coreg VARCHAR)""")
    con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
    con.execute("INSERT INTO sec_companies VALUES (1, 'ACME')")
    n = 0
    for fy, base in ((2025, 100.0), (2026, 120.0)):
        q_ends = [D(fy - 1, 9, 30), D(fy - 1, 12, 31), D(fy, 3, 31)]
        for i, qe in enumerate(q_ends):
            n += 1
            adsh = "a%d" % n
            con.execute("INSERT INTO sec_filings VALUES (?, 1, '10-Q', 'clean', ?, ?, ?, ?)",
                        [adsh, qe + datetime.timedelta(days=30), fy, "Q%d" % (i + 1), qe])
            con.execute("INSERT INTO sec_facts VALUES (?, 'Revenues', ?, 1, 'USD', ?, NULL, NULL)", [adsh, qe, base + i])
            con.execute("INSERT INTO sec_facts VALUES (?, 'EarningsPerShareDiluted', ?, 1, 'USD/shares', 1.0, NULL, NULL)", [adsh, qe])
        n += 1
        adsh = "a%d" % n
        ye = D(fy, 6, 30)
        con.execute("INSERT INTO sec_filings VALUES (?, 1, '10-K', 'clean', ?, ?, 'FY', ?)", [adsh, ye + datetime.timedelta(days=60), fy, ye])
        con.execute("INSERT INTO sec_facts VALUES (?, 'Revenues', ?, 4, 'USD', ?, NULL, NULL)", [adsh, ye, 4 * base + 10])
    return con


class Quarters(unittest.TestCase):
    def test_fourth_quarter_is_the_year_less_three_and_has_a_year_ago(self):
        con = _quarters_db()
        with mock.patch.object(us_tables, "_has", return_value=True):
            rows = us_tables.quarter_rows(con, today=D(2026, 10, 1))
        by = {r["quarter_end"]: r for r in rows}
        q4 = by[D(2026, 6, 30)]
        self.assertTrue(q4["q4_derived"])
        self.assertEqual(q4["fiscal_quarter"], "Q4")
        self.assertEqual(q4["revenue_usd"], 4 * 120 + 10 - (120 + 121 + 122))
        self.assertIsNone(q4["eps_usd"], "EPS is not derived: it does not add up across quarters")
        prior_q4 = by[D(2025, 6, 30)]
        self.assertTrue(prior_q4["q4_derived"], "the year before's Q4 must not block this year's")
        self.assertAlmostEqual(q4["revenue_growth_yoy_pct"], (q4["revenue_usd"] / prior_q4["revenue_usd"] - 1) * 100, places=3)
        self.assertTrue(q4["is_latest"])
        self.assertEqual(sum(r["is_latest"] for r in rows), 1)
        q1 = by[D(2025, 9, 30)] if D(2025, 9, 30) in by else by[D(2024, 9, 30)]
        self.assertFalse(q1["q4_derived"])


class FundGuards(unittest.TestCase):
    def _run(self, now, before, splits):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("INSERT INTO sec_companies VALUES (1, 'SPLT'), (2, 'MOVE')")

        def window(batch):
            return now if batch == "b2" else before
        fake = mock.MagicMock()
        fake.execute.return_value.fetchall.return_value = [("b2",), ("b1",)]
        with mock.patch.object(us_tables, "_has", return_value=True), \
                mock.patch("app.us_link._build_cusips_memo", return_value={"by_cik": {1: [{"cusip": "C1"}], 2: [{"cusip": "C2"}]}}), \
                mock.patch.object(us_tables, "_splits", return_value=splits), \
                mock.patch.object(us_tables, "_tickers", return_value={1: "SPLT", 2: "MOVE"}):
            real_execute = con.execute
            calls = {"n": 0}

            class Cur(object):
                def execute(self, sql, params=None):
                    if "sec_13f_batches" in sql:
                        return real_execute("SELECT * FROM (VALUES ('b2'), ('b1')) t(batch)")
                    calls["n"] += 1
                    rows = window(params[0])
                    res = mock.MagicMock()
                    res.fetchall.return_value = [(c,) + v for c, v in rows.items()]
                    return res
            return {r["ticker"]: r for r in us_tables.fund_rows(Cur())}

    def test_split_between_quarters_is_adjusted_and_a_new_identifier_is_blank(self):
        now = {"C1": (D(2026, 6, 30), 100, 1e9, 2500.0, list(range(100))),
               "C2": (D(2026, 6, 30), 900, 5e9, 1000.0, list(range(900)))}
        before = {"C1": (D(2026, 3, 31), 100, 9e8, 100.0, list(range(100))),
                  "C2": (D(2026, 3, 31), 20, 1e8, 10.0, list(range(20)))}
        out = self._run(now, before, {"SPLT": [(D(2026, 4, 6), 25.0)]})
        self.assertAlmostEqual(out["SPLT"]["shares_change_pct"], 0.0, places=6, msg="25-for-1 split: no buying")
        self.assertEqual(out["SPLT"]["shares_held_prior"], 2500.0)
        self.assertIsNone(out["MOVE"]["shares_change_pct"], "most holders new at once: a new CUSIP, not buying")
        self.assertEqual(out["MOVE"]["new_holders"], 880)


class Sandbox(unittest.TestCase):
    def test_tables_load_and_the_database_is_locked(self):
        rows = {"pay": [{"ticker": "AAPL", "ceo": "Tim Cook", "fiscal_year_end": "2025-09-27", "ceo_total_pay_usd": 74.3e6}],
                "quarters": [], "segments": [], "insider_trades": [], "fund_holdings": []}
        us_tables._STATE.clear()
        with mock.patch.object(us_tables, "build", return_value=rows), \
                mock.patch("app.sec_api.file_version", return_value=("t", 1)):
            db = us_tables.sandbox(None)
            cur = db.cursor()
            self.assertEqual(cur.execute("SELECT ceo, ceo_total_pay_usd FROM pay").fetchall(), [("Tim Cook", 74.3e6)])
            self.assertEqual(cur.execute("SELECT count(*) FROM quarters").fetchone()[0], 0)
            cur.execute("CREATE TEMP TABLE stocks AS SELECT 'AAPL' AS ticker")
            self.assertEqual(cur.execute("SELECT count(*) FROM stocks JOIN pay USING (ticker)").fetchone()[0], 1)
            with self.assertRaises(Exception):
                cur.execute("SELECT * FROM read_csv('/etc/passwd')")
            with self.assertRaises(Exception):
                cur.execute("SET enable_external_access=true")
            self.assertIs(us_tables.sandbox(None), db, "built once per database version")
            cur.close()
        us_tables._STATE.clear()

    def test_every_event_knows_the_close_before_its_news(self):
        """2026-10-29: "after earnings" was measured from the results-day close on one
        question and the next day's close on another. Every window now starts from
        events.before_date, the last trading day before the event, for any filing hour."""
        db = duckdb.connect(":memory:")
        db.execute("CREATE TABLE prices AS SELECT * FROM (VALUES ('JPM', DATE '2026-10-09'), ('JPM', DATE '2026-10-12'), "
                   "('JPM', DATE '2026-10-13'), ('SPY', DATE '2026-10-13'), ('SPY', DATE '2026-10-14')) t(ticker, date)")
        db.execute("CREATE TABLE events AS SELECT * FROM (VALUES ('results', DATE '2026-10-13', 'JPM', NULL::DATE), "
                   "('results', DATE '2026-10-12', 'JPM', NULL::DATE), ('cpi', DATE '2026-10-14', NULL, NULL::DATE), "
                   "('results', DATE '2026-10-13', 'NOPX', NULL::DATE)) t(kind, date, ticker, before_date)")
        us_tables._event_before_dates(db)
        got = db.execute("SELECT kind, date, ticker, before_date FROM events ORDER BY kind, date, ticker").fetchall()
        self.assertEqual(got, [("cpi", D(2026, 10, 14), None, D(2026, 10, 13)),                  # the S&P 500 fund's day before
                               ("results", D(2026, 10, 12), "JPM", D(2026, 10, 9)),              # over a weekend
                               ("results", D(2026, 10, 13), "JPM", D(2026, 10, 12)),
                               ("results", D(2026, 10, 13), "NOPX", None)])                       # no prices: blank, never a guess
        self.assertIn("before_date", us_tables.doc())
        from app import us_checks
        self.assertIn("last trading day before the event day", us_checks.review_prompt("q", {}, "h", [], []))

    def test_every_table_is_described_for_the_model(self):
        d = us_tables.doc()
        for name, (_, cols) in us_tables.TABLES.items():
            self.assertIn(name + ":", d)
            for c, _, _ in cols:
                self.assertIn("  %s " % c, d)


if __name__ == "__main__":
    unittest.main()
