# -*- coding: utf-8 -*-
"""The step-by-step answers (app/us_steps.py): the worked examples run on the
data, the query sandbox refuses what it must, the headline never carries a
number the data did not give, and a failed query gets one retry."""
import datetime
import json
import os
import pathlib
import unittest

import duckdb
from fastapi.testclient import TestClient

from app import us_steps

ROOT = pathlib.Path(__file__).resolve().parent.parent


def setUpModule():
    import tempfile
    os.environ["US_ASK_MODEL"] = "0"
    us_steps.LOG_FILE = os.path.join(tempfile.mkdtemp(), "us_ask_log.jsonl")   # tests never write the real log


def tearDownModule():
    os.environ.pop("US_ASK_MODEL", None)


class PlainTest(unittest.TestCase):
    def test_examples_file_is_complete(self):
        exs = us_steps.examples(datetime.date(2026, 10, 19))
        self.assertGreaterEqual(len(exs), 30)
        for ex in exs:
            self.assertTrue(ex["question"] and ex["steps"], ex)
            self.assertIn("period", ex)
            if ex.get("cannot"):
                self.assertFalse(ex["sql"], ex["question"])
            else:
                self.assertTrue(ex["sql"].lstrip().upper().startswith(("SELECT", "WITH")), ex["question"])
                self.assertTrue(ex["headline"], ex["question"])
            self.assertNotIn("YEAR", json.dumps(ex["period"]), "dates are filled in")

    def test_headline_takes_numbers_only_from_the_data(self):
        cols, row = ["beat", "members", "index_change_pct"], [202, 494, 11.72]
        h = us_steps.fill_headline("{beat} of {members} S&P 500 members beat the index {period}, when it changed {index_change_pct}.",
                                   cols, row, "so far in 2026", "How many companies beat the S&P 500?")
        self.assertEqual(h, "202 of 494 S&P 500 members beat the index so far in 2026, when it changed +11.7%.")
        # a number of the model's own is refused; so is a column the query did not return
        self.assertIsNone(us_steps.fill_headline("About 300 of {members} beat it.", cols, row, "so far in 2026", "how many beat it"))
        self.assertIsNone(us_steps.fill_headline("{winners} beat it.", cols, row, "so far in 2026", "how many beat it"))
        # a number the question gave may be repeated, and so may a threshold the query applies
        self.assertIsNotNone(us_steps.fill_headline("{beat} fell more than 20%.", cols, row, "", "fell more than 20%"))
        self.assertIsNotNone(us_steps.fill_headline("{beat} above $50mn and $2bn.", cols, row, "", "how many", "WHERE a >= 5e7 AND b >= 2e9"))
        self.assertIsNone(us_steps.fill_headline("{beat} above $50mn.", cols, row, "", "how many", "WHERE a >= 2e9"))
        # "from {period}" never reads "from since" (the shutdown example on the front page, 2026-10-24)
        self.assertEqual(us_steps.fill_headline("{beat} rose from {period}.", cols, row, "since 1 January 2018", "q"),
                         "202 rose since 1 January 2018.")
        self.assertEqual(us_steps.fill_headline("{beat} rose during {period}.", cols, row, "from 1 May 2025 to 2 June 2025", "q"),
                         "202 rose from 1 May 2025 to 2 June 2025.")

    def test_a_slow_answer_keeps_the_stream_open(self):
        """Cloudflare closes a stream silent for 100 s: a slow answer sends comment
        lines while it works, and the answer still arrives whole."""
        import time
        from unittest import mock
        from fastapi import FastAPI
        from app import sec_api, stock_recipes, us_ask_api

        def slow(cur, q, prev):
            time.sleep(0.5)
            return {"headline": "done", "match": {}}
        app = FastAPI()
        app.include_router(us_ask_api.router)
        with mock.patch.object(us_ask_api, "KEEPALIVE_SECONDS", 0.1), \
                mock.patch.object(sec_api, "_require", lambda: None), \
                mock.patch.object(stock_recipes, "answer", slow):
            body = TestClient(app).get("/api/v1/us/ask/stream?q=slow+question").text
        self.assertIn(": still working", body)
        self.assertIn('"headline": "done"', body)

    def test_value_formats_follow_the_column_suffix(self):
        self.assertEqual(us_steps.fmt_value("change_period_pct", -3.21), "−3.2%")
        self.assertEqual(us_steps.fmt_value("share_pct", 40.1), "40.1%")
        self.assertEqual(us_steps.fmt_value("gap_pp", -6.3), "−6.3 pp")
        self.assertEqual(us_steps.fmt_value("n", 1589), "1,589")
        self.assertEqual(us_steps.fmt_value("election_year", 2018), "2018")       # 2026-10-23: a headline said "In 2,018"
        self.assertEqual(us_steps.fmt_value("year", 2022.0), "2022")
        self.assertEqual(us_steps.fmt_value("x_pct", None), "—")

    def test_a_figure_seen_in_a_look_cannot_be_copied_into_the_query(self):
        seen = [433.5, 5, 2026, -11.027252801786524]
        self.assertEqual(us_steps.copied_figures("SELECT 1 FROM prices WHERE adj_close_usd > 433.5", seen), ["433.5"])
        self.assertEqual(us_steps.copied_figures("SELECT -11.03 AS x", seen), ["-11.03"])
        # thresholds, years, quoted dates and the question's own numbers are not copies
        self.assertEqual(us_steps.copied_figures("SELECT 1 FROM prices WHERE date >= DATE '2026-01-01' AND year = 2026 "
                                                 "AND volume > 5 AND x > 1e8", seen), [])
        self.assertEqual(us_steps.copied_figures("SELECT 1 WHERE x > 433.5", seen, "above 433.5?"), [])
        self.assertEqual(us_steps.copied_figures("SELECT 1 WHERE x > 433.5", []), [])

    def test_months_print_in_words(self):
        self.assertEqual(us_steps.fmt_value("month", "2026-09"), "September 2026")

    def test_sectors(self):
        self.assertEqual(us_steps.sector_for(3674), "Technology")
        self.assertEqual(us_steps.sector_for(6022), "Financials")
        self.assertEqual(us_steps.sector_for(6798), "Real Estate")
        self.assertEqual(us_steps.sector_for(2834), "Health Care")
        self.assertEqual(us_steps.sector_for(1311), "Energy")
        self.assertEqual(us_steps.sector_for(5331), "Consumer Staples")
        self.assertIsNone(us_steps.sector_for(None))

    def test_period_words(self):
        t = datetime.date(2026, 10, 19)
        self.assertEqual(us_steps.period_words(datetime.date(2026, 1, 1), None, t), "so far in 2026")
        self.assertEqual(us_steps.period_words(datetime.date(2025, 1, 1), datetime.date(2025, 12, 31), t), "in 2025")
        self.assertEqual(us_steps.period_words(datetime.date(2025, 10, 19), None, t), "over the past year")


def _live():
    p = ROOT / "data" / "sec.duckdb"
    if not p.exists():
        return False
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
            return {"sec_filings", "sec_companies", "us_px_daily", "us_index_members"} <= names
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live(), "data/sec.duckdb without prices and index members")
class LiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import sec_api
        from app.main import app
        cls.cur = sec_api._require()
        cls.c = TestClient(app)

    def test_every_worked_example_runs(self):
        """A worked example teaches the model; a broken one teaches it wrong."""
        failures = []
        for ex in us_steps.examples():
            if not ex["sql"]:
                continue
            start, end = us_steps._period({"period": ex["period"]})
            try:
                rows, cols = us_steps.run(self.cur, ex["sql"], start, end)
            except us_steps.StepsError as exc:
                failures.append("%s: %s" % (ex["question"], exc))
                continue
            if not rows:
                failures.append("%s: no rows" % ex["question"])
                continue
            h = us_steps.fill_headline(ex["headline"], cols, rows[0], us_steps.period_words(start, end), ex["question"], ex["sql"])
            if not h:
                failures.append("%s: headline does not fill (%s)" % (ex["question"], cols))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_tables(self):
        base = us_steps.base_rows(self.cur)
        self.assertGreater(len(base), 3000)
        self.assertGreater(sum(r["in_sp500"] for r in base), 480)
        self.assertGreater(sum(r["in_dow"] for r in base), 27)
        self.assertEqual(len({r["ticker"] for r in base}), len(base), "one row per ticker")

    def test_sandbox_refuses(self):
        s = datetime.date(datetime.date.today().year, 1, 1)
        for bad in ("SELECT * FROM read_csv('/etc/passwd')", "COPY stocks TO 'x.csv'", "SELECT 1; SELECT 2",
                    "SELECT * FROM sec_filings", "ATTACH 'data/sec.duckdb' AS s", "SELECT coalesce(pe_x, 0) FROM stocks"):
            with self.assertRaises(us_steps.StepsError, msg=bad):
                us_steps.run(self.cur, bad, s)

    def _mocked(self, replies, q):
        """Ask q with the reader saying 'steps' and the solver giving replies in turn."""
        from app import us_ask_model
        calls = []
        real, real_provider = us_ask_model._complete, us_ask_model.provider

        def fake(prov, key, model, messages, **kw):
            calls.append(messages)
            if "fill in a form" in messages[0]["content"]:
                return {"text": json.dumps({"shape": "steps"}), "usage": {}}
            return {"text": json.dumps(replies[min(len(calls) - 2, len(replies) - 1)]), "usage": {}}
        us_ask_model._complete, us_ask_model.provider = fake, (lambda: ("deepseek", "db-test", "deepseek-chat"))
        us_ask_model._CACHE.clear()
        us_steps._PLANS.clear()
        try:
            return self.c.get("/api/v1/us/ask", params={"q": q}), calls
        finally:
            us_ask_model._complete, us_ask_model.provider = real, real_provider
            us_ask_model._CACHE.clear()
            us_steps._PLANS.clear()

    def test_steps_answer_with_one_retry(self):
        good = us_steps.examples()[0]
        bad = dict(good, sql="SELECT count(*) AS beat FROM stockz")
        r, calls = self._mocked([bad, good], "How many companies outperformed the S&P 500?")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(d["recipe"], "steps")
        self.assertEqual(d["match"]["via"], "steps")
        self.assertTrue(d["steps"] and d["query"])
        self.assertIn("S&P 500 members beat the index", d["headline"])
        self.assertEqual(len(calls), 4, "reader, a failed query, the fix, the review")
        self.assertIn("query failed", calls[2][-1]["content"])
        # the headline's numbers are the query's own
        rows, cols = us_steps.run(self.cur, good["sql"], datetime.date(datetime.date.today().year, 1, 1))
        self.assertIn("%s of %s" % (rows[0][0], rows[0][1]), d["headline"])
        # the model never sees a number from the data
        self.assertNotIn(str(rows[0][1]), calls[1][0]["content"].split("WORKED EXAMPLES")[0])

    def test_daily_prices_are_split_adjusted_like_the_price_api(self):
        from app import us_prices_api, us_tables
        c = us_tables.sandbox(self.cur).cursor()
        self.assertGreater(us_tables.counts().get("prices", 0), 1000000)
        for t in ("MSFT", "SPY", "DIA"):
            self.assertTrue(c.execute("SELECT count(*) FROM prices WHERE ticker = ?", [t]).fetchone()[0], t)
        got = c.execute("SELECT date, adj_close_usd FROM prices WHERE ticker = 'AVGO' AND date BETWEEN "
                        "DATE '2024-07-08' AND DATE '2024-07-19' ORDER BY date").fetchall()
        api, _ = us_prices_api.adjusted_closes(self.cur, "AVGO", datetime.date(2024, 7, 8), datetime.date(2024, 7, 19))
        self.assertEqual(len(got), len(api))
        for (d, a), (d2, _, b) in zip(got, api):
            self.assertEqual(d, d2)
            self.assertAlmostEqual(a, b, places=6)

    def test_a_look_runs_first_and_is_shown(self):
        final = next(x for x in us_steps.examples() if x["question"].startswith("In how many months"))
        look = {"look": "SELECT ticker, min(date) AS first_day, count(*) AS days FROM prices WHERE ticker IN ('MSFT', 'SPY') "
                        "GROUP BY ticker LIMIT 5", "sql": ""}
        r, calls = self._mocked([look, final], "How many months has Microsoft outperformed S&P in 2026")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(len(d["looks"]), 1)
        self.assertEqual(d["looks"][0]["rows"], 2)
        self.assertIn("Result of your look", calls[2][-1]["content"])
        self.assertRegex(d["headline"], r"^Microsoft beat the S&P 500 in \d+ of \d+ months")

    def test_other_markets_point_elsewhere(self):
        for q in ("which Japanese banks are cheapest", "best Hong Kong stocks this year"):
            r = self.c.get("/api/v1/us/ask", params={"q": q})
            self.assertEqual(r.status_code, 422, q)
            self.assertIn("US-listed companies only", r.json()["detail"])
        self.assertIsNone(us_steps.other_market("which US banks are cheapest"))

    def test_table_has_borrowings_prior_revenue_and_country(self):
        base = {r["ticker"]: r for r in us_steps.base_rows(self.cur)}
        self.assertGreater(sum(1 for r in base.values() if r["borrowings_usd"]), 1000)
        self.assertGreater(sum(1 for r in base.values() if r["revenue_prior_usd"]), 1000)
        self.assertEqual(base["AAPL"]["country"], "US")
        s = datetime.date(datetime.date.today().year, 1, 1)
        rows, cols = us_steps.run(self.cur, "SELECT count(*) FROM stocks WHERE cash_usd > borrowings_usd", s)
        self.assertGreater(rows[0][0], 100)

    def test_steps_can_say_it_cannot(self):
        r, _ = self._mocked([{"steps": ["Employees are not held."], "sql": "", "cannot": "I don't hold employee numbers."}],
                            "Which companies have the most employees?")
        self.assertEqual(r.status_code, 422)
        self.assertIn("mployee", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
