# -*- coding: utf-8 -*-
"""The Charts and Screener tabs of Your Plover: a sentence read into a chart
spec or screen filters (app/us_build_api.py), the screener's new limits
(market value, P/E, one-month and one-year change, S&P 500 membership:
app/sec_api.screener), and the saved charts and screens kept per account
(app/assistant/views.py).

The readers are tested with the model switched off, so what is proved is the
word patterns every request falls back to. The examples typed are the two
boxes' own placeholders (ci/search_boxes.json: my.html#ch-q, my.html#sc-q)
and the requests a reader would type first. The screener tests need
data/sec.duckdb with prices and skip without it, as the other US data tests do.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_build
"""
import os
import tempfile
import unittest

os.environ["US_ASK_MODEL"] = "0"            # word patterns only: no key, no network
os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ.setdefault("ASSISTANT_SECRET", "test-secret-not-for-production")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, sec_api, us_build_api  # noqa: E402
from app.assistant import api as aapi  # noqa: E402
from app.assistant import store, views  # noqa: E402
from tests import _data  # noqa: E402


def _live():
    """data/sec.duckdb with the universe and daily prices."""
    if not _data.SEC:
        return False
    try:
        cur = sec_api._require()
        return sec_api._has(cur, "sec_companies") and sec_api._has(cur, "us_px_daily")
    except Exception:  # noqa: BLE001
        return False


def _cur():
    try:
        return sec_api._require()
    except Exception:  # noqa: BLE001
        return None


class ScreenWords(unittest.TestCase):
    """The screener box's own example and the obvious requests, read by words."""

    def chips(self, q):
        spec = us_build_api._screen_by_words(_cur(), q)
        chips, params = us_build_api._finish_screen(spec)
        return [c["text"] for c in chips], params

    def test_the_boxs_own_example(self):
        texts, params = self.chips("big companies still growing sales, that are cheap on earnings")
        self.assertEqual(params["market_cap_min"], "20000000000")
        self.assertEqual(params["revenue_growth_min"], "5")
        self.assertEqual(params["pe_max"], "18")
        self.assertEqual((params["sort"], params["order"]), ("pe_x", "asc"))
        self.assertIn("Market value over $20bn", texts)
        self.assertIn("Sorted: cheapest first", texts)

    def test_down_this_month(self):
        texts, params = self.chips("stocks down 10% this month under 20x earnings")
        self.assertEqual(params["price_change_1m_max"], "-10")
        self.assertEqual(params["pe_max"], "20")
        self.assertIn("Down over the last month by at least 10%", texts)
        texts, params = self.chips("S&P 500 companies that have fallen this month")
        self.assertEqual(params["price_change_1m_max"], "0")
        self.assertEqual(params["sp500"], "true")
        self.assertIn("Fell over the last month", texts)
        texts, params = self.chips("companies up 20% in the past year")
        self.assertEqual(params["price_change_1y_min"], "20")

    def test_dividends_and_order(self):
        texts, params = self.chips("dividend yield over 3%, biggest first")
        self.assertEqual(params["dividend_yield_min"], "3")
        self.assertEqual((params["sort"], params["order"]), ("market_cap", "desc"))
        texts, params = self.chips("market value over $500bn")
        self.assertEqual(params["market_cap_min"], "500000000000")

    def test_nothing_read_is_not_a_crash(self):
        texts, params = self.chips("hello")
        self.assertEqual(texts, ["Sorted: biggest first"])

    def test_filter_text_signs(self):
        self.assertEqual(us_build_api.filter_text("price_change_1y_max", -15.0), "Down over the last year by at least 15%")
        self.assertEqual(us_build_api.filter_text("market_cap_max", 2e9), "Market value under $2bn")
        self.assertEqual(us_build_api.filter_text("pe_max", 18), "Price / earnings under 18×")


class ChartWords(unittest.TestCase):
    """The chart box's own example and a change in words, read by words."""

    def spec(self, q, current=None):
        return us_build_api._chart_by_words(_cur(), q, current)

    def test_period_measure_benchmark_words(self):
        s = self.spec("Tesla operating margin by quarter since 2020")
        self.assertEqual(s["measure"], "operating_margin")
        self.assertEqual(s["from"], "2020-01-01")
        self.assertEqual(s["benchmarks"], [])
        self.assertEqual(s["display"], "actual")
        s = self.spec("chart the Nasdaq since 2015 log scale")
        self.assertEqual(s["benchmarks"], ["nasdaq100"])
        self.assertTrue(s["log"])
        s = self.spec("revenue, trailing 12 months, since 2019")
        self.assertEqual(s["measure"], "revenue_ttm")
        self.assertEqual(s["from"], "2019-01-01")   # "trailing 12 months" is the measure, not the period

    def test_defaults(self):
        s = self.spec("something with no period")
        self.assertEqual((s["period"], s["benchmarks"], s["display"], s["chart_type"]), ("ytd", ["sp500"], "rebased", "line"))

    @unittest.skipUnless(_live(), "data/sec.duckdb without US prices")
    def test_the_boxs_own_example_names_three_companies(self):
        s = self.spec("Apple, Microsoft and Nvidia against the S&P 500 this year")
        self.assertEqual([x["ticker"] for x in s["series"]], ["AAPL", "MSFT", "NVDA"])
        self.assertEqual((s["period"], s["benchmarks"]), ("ytd", ["sp500"]))
        self.assertTrue(us_build_api._chart_title(s).startswith("Share price, rebased to 100: Apple"))

    @unittest.skipUnless(_live(), "data/sec.duckdb without US prices")
    def test_a_change_in_words_keeps_the_rest(self):
        cur = self.spec("Apple vs Microsoft this year")
        s = self.spec("add Amazon", cur)
        self.assertEqual([x["ticker"] for x in s["series"]], ["AAPL", "MSFT", "AMZN"])
        self.assertEqual((s["period"], s["benchmarks"]), ("ytd", ["sp500"]))
        s = self.spec("drop Microsoft and the S&P", s)
        self.assertEqual([x["ticker"] for x in s["series"]], ["AAPL", "AMZN"])
        self.assertEqual(s["benchmarks"], [])

    def test_several_indexes(self):
        s = self.spec("compare to Nasdaq & DJI")
        self.assertEqual(s["benchmarks"], ["nasdaq100", "dow"])
        self.assertIn("Nasdaq-100 and Dow Jones", us_build_api._chart_title(s))
        s = self.spec("add the S&P 500", s)
        self.assertEqual(s["benchmarks"], ["nasdaq100", "dow", "sp500"])
        s = self.spec("drop the Dow", s)
        self.assertEqual(s["benchmarks"], ["nasdaq100", "sp500"])
        s = self.spec("against the Russell 2000", s)
        self.assertEqual(s["benchmarks"], ["russell2000"])
        s = self.spec("remove the index", s)
        self.assertEqual(s["benchmarks"], [])

    def test_six_lines_at_most_and_companies_come_first(self):
        s = us_build_api._fit_lines({"series": [{"ticker": str(i)} for i in range(5)], "benchmarks": ["sp500", "dow"]})
        self.assertEqual(s["benchmarks"], ["sp500"])

    @unittest.skipUnless(_live(), "data/sec.duckdb without US prices")
    def test_the_routes(self):
        from app.main import app
        c = TestClient(app)
        r = c.get("/api/v1/us/build/chart", params={"q": "Apple and Microsoft this year"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual([x["ticker"] for x in body["spec"]["series"]], ["AAPL", "MSFT"])
        self.assertTrue(any(b["id"] == "sp500" and b["fund"] == "SPY" for b in body["benchmarks"]))
        self.assertTrue(any(m["id"] == "operating_margin" for m in body["measures"]))
        r = c.get("/api/v1/us/build/screen", params={"q": "banks with dividend yield over 3%, biggest first"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["params"]["dividend_yield_min"], "3")
        self.assertTrue(body["params"]["sic"].startswith("6021"))
        self.assertTrue(any(c_["text"].startswith("Industry: commercial banks") for c_ in body["chips"]))
        # a chip removed on the page: the rest comes back as chips and a query
        spec = dict(body["spec"], industry=None)
        import json
        r = c.get("/api/v1/us/build/screen/chips", params={"filters": json.dumps(spec)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("sic", r.json()["params"])
        self.assertEqual(r.json()["params"]["dividend_yield_min"], "3")


@unittest.skipUnless(_live(), "data/sec.duckdb without US prices")
class ScreenerLimits(unittest.TestCase):
    """The screener's new limits, against the real database."""

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_market_value_pe_and_one_month_change(self):
        r = self.c.get("/api/v1/us/financials/screener", params={
            "market_cap_min": "20000000000", "pe_max": "18", "pe_min": "5", "sort": "pe_x", "order": "asc", "limit": "50"})
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()["rows"]
        self.assertTrue(rows)
        for x in rows:
            self.assertGreaterEqual(x["metrics"]["market_cap"], 2e10)
            self.assertTrue(5 <= x["metrics"]["pe_x"] <= 18)
            self.assertIn("price_change_1m_pct", x["metrics"])
            self.assertIn("close_1m", x["price"])
        self.assertTrue(any(x["metrics"]["price_change_1m_pct"] is not None for x in rows))
        self.assertEqual(r.json()["filters"]["market_cap_min"], 2e10)
        r = self.c.get("/api/v1/us/financials/screener", params={"price_change_1m_max": "-10", "sort": "price_change_1m_pct",
                                                                 "order": "asc", "limit": "20"})
        self.assertEqual(r.status_code, 200, r.text)
        for x in r.json()["rows"]:
            self.assertLessEqual(x["metrics"]["price_change_1m_pct"], -10)

    def test_sp500_and_several_exchanges(self):
        r = self.c.get("/api/v1/us/financials/screener", params={"sp500": "true", "sort": "market_cap", "limit": "600"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(400 <= r.json()["matched"] <= 520, r.json()["matched"])
        r = self.c.get("/api/v1/us/financials/screener", params={"exchange": "NYSE,Nasdaq", "sort": "market_cap", "limit": "5"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(all(x["exchange"] in ("NYSE", "Nasdaq") for x in r.json()["rows"]))
        r = self.c.get("/api/v1/us/financials/screener", params={"sic": "6021,6022,6029", "sort": "market_cap", "limit": "5"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(all(str(x["sic"]).startswith("602") for x in r.json()["rows"]))


class SavedViews(unittest.TestCase):
    """Saved charts and screens: kept per account, a rerun says what is new."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        views.reset_for_tests()
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        cls.app = app
        db = accounts.conn()
        now = accounts._now()
        for email, tok in (("reader@example.com", "tok-a"), ("other@example.com", "tok-b")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (accounts._hash(tok), aid, now, now + 3600))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def client(self, tok):
        c = TestClient(self.app)
        c.cookies.set(accounts.COOKIE, tok)
        return c

    def test_signed_out_is_refused(self):
        self.assertEqual(TestClient(self.app).get("/api/v1/assistant/charts").status_code, 401)

    def test_chart_saved_updated_deleted_and_private(self):
        me, other = self.client("tok-a"), self.client("tok-b")
        spec = {"series": [{"ticker": "AAPL", "name": "Apple Inc."}], "measure": "price", "period": "ytd", "benchmarks": ["sp500"],
                "display": "rebased", "log": False, "chart_type": "line", "mark_results": False}
        r = me.post("/api/v1/assistant/charts", json={"title": "Apple this year", "spec": spec})
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()["chart"]["id"]
        self.assertEqual([c["title"] for c in r.json()["charts"]], ["Apple this year"])
        r = me.post("/api/v1/assistant/charts", json={"title": "Apple, renamed", "spec": spec, "id": cid})
        self.assertEqual(r.json()["chart"]["id"], cid)
        self.assertEqual(me.get("/api/v1/assistant/charts").json()["charts"][0]["title"], "Apple, renamed")
        self.assertEqual(other.get("/api/v1/assistant/charts").json()["charts"], [])
        self.assertEqual(other.post("/api/v1/assistant/charts", json={"title": "x", "spec": spec, "id": cid}).status_code, 404)
        self.assertEqual(other.delete("/api/v1/assistant/charts/%d" % cid).json()["charts"], [])
        self.assertEqual(len(me.get("/api/v1/assistant/charts").json()["charts"]), 1)   # the other account deleted nothing
        self.assertEqual(me.delete("/api/v1/assistant/charts/%d" % cid).json()["charts"], [])

    def test_screen_rerun_counts_new_matches(self):
        me = self.client("tok-a")
        spec = {"filters": [{"key": "pe_max", "value": 18}], "industry": None, "sp500": False, "exchanges": [],
                "sort_key": "pe_x", "sort_order": "asc"}
        r = me.post("/api/v1/assistant/screens", json={"name": "Cheap", "spec": spec, "tickers": ["AAPL", "MSFT"]})
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()["screen"]
        self.assertEqual((s["last_n"], s["new_n"]), (2, None))
        r = me.post("/api/v1/assistant/screens", json={"name": "Cheap", "spec": spec, "tickers": ["MSFT", "NVDA", "AMZN"], "id": s["id"]})
        s = r.json()["screen"]
        self.assertEqual((s["last_n"], s["new_n"]), (3, 2))
        self.assertEqual(s["last_tickers"], ["AMZN", "MSFT", "NVDA"])
        self.assertEqual(me.delete("/api/v1/assistant/screens/%d" % s["id"]).json()["screens"], [])


if __name__ == "__main__":
    unittest.main()
