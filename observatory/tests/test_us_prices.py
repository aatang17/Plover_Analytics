# -*- coding: utf-8 -*-
"""US daily prices (equity/us_prices_databento.py, app/us_prices_api.py) and
the price-based screener figures (app/sec_metrics.with_prices).

The loader test feeds a small CSV through `load` with no network; the money
guard is tested by making the quote too high. API tests use data/sec.duckdb
when it holds prices and skip otherwise.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_prices
"""
import datetime
import gzip
import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest
from decimal import Decimal

import duckdb
from fastapi.testclient import TestClient

from app import sec_metrics

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("us_prices_databento", str(ROOT / "equity" / "us_prices_databento.py"))
px = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(px)

CSV = ("ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol\n"
       "2026-10-13T00:00:00.000000000Z,35,90,38,344.665,348.1499,340.74,341.27,38983839,AAPL\n"
       "2026-10-14T00:00:00.000000000Z,35,90,38,344.605,347.09,341.72,342.96,50061053,AAPL\n"
       "2026-10-14T00:00:00.000000000Z,35,90,10888,545.27,545.75,532.54,533.69,22324295,MSFT\n")


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uspx-")
        self.db = os.path.join(self.tmp, "sec.duckdb")
        self.gz = os.path.join(self.tmp, "bars.csv.gz")
        with gzip.open(self.gz, "wb") as f:
            f.write(CSV.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_replaces_days_wholesale(self):
        con = duckdb.connect(self.db)
        con.execute(px.SCHEMA_SQL)
        s, e = datetime.date(2026, 10, 13), datetime.date(2026, 10, 14)
        n = px.load(con, self.gz, s, e, 1)
        self.assertEqual((n[0], n[1]), (3, 2))
        self.assertEqual(con.execute("SELECT close FROM us_px_daily WHERE symbol='MSFT'").fetchone()[0], 533.69)
        # a re-pull of the same days is a clean replacement, not a duplicate
        px.load(con, self.gz, s, e, 2)
        self.assertEqual(con.execute("SELECT count(*) FROM us_px_daily").fetchone()[0], 3)
        self.assertEqual(px.last_loaded(con).isoformat(), "2026-10-14")
        con.close()

    def test_request_end_is_exclusive(self):
        p = px.request_params(datetime.date(2026, 10, 1), datetime.date(2026, 10, 16))
        self.assertEqual(p["end"], "2026-10-17")
        self.assertEqual(p["symbols"], "ALL_SYMBOLS")

    def test_cap_refuses_without_downloading(self):
        class FakeClient(object):
            def json(self, endpoint, params, post=False):
                if endpoint == "metadata.get_dataset_range":
                    return {"schema": {"ohlcv-1d": {"end": "2026-10-17T00:00:00Z"}}}
                return 4.52
        downloaded = []
        orig_client, orig_dl, orig_key = px.Client, px.download, px.read_key
        px.Client = lambda key: FakeClient()
        px.read_key = lambda path=None: "db-" + "x" * 29
        px.download = lambda *a, **k: downloaded.append(a) or (0, 0, "")
        try:
            rc = px.main(["--db", self.db, "--start", "2026-10-01", "--end", "2026-10-16", "--max-cost-usd", "1"])
        finally:
            px.Client, px.download, px.read_key = orig_client, orig_dl, orig_key
        self.assertEqual(rc, 1)
        self.assertEqual(downloaded, [])
        con = duckdb.connect(self.db, read_only=True)
        self.assertEqual(con.execute("SELECT status, quote_usd FROM us_px_pulls").fetchall(), [("refused", 4.52)])
        con.close()

    def test_bad_cap_is_rejected(self):
        with self.assertRaises(SystemExit):
            px.main(["--db", self.db, "--max-cost-usd", "-1"])
        with self.assertRaises(SystemExit):
            px.main(["--db", self.db])


class PriceMetricsTest(unittest.TestCase):
    def test_market_cap_pe_and_yields(self):
        row = {"unit": "USD", "metrics": {}, "per_share": {"shares_outstanding": 1000.0, "eps_diluted": 5.0},
               "size": {"buybacks": 2000.0, "dividends_paid": 500.0}}
        sec_metrics.with_prices(row, 50.0, 40.0, "2026-10-16")
        m = row["metrics"]
        self.assertEqual(m["market_cap"], 50000.0)
        self.assertEqual(m["pe_x"], 10.0)
        self.assertEqual(m["buyback_yield_pct"], 4.0)
        self.assertEqual(m["dividend_yield_pct"], 1.0)
        self.assertEqual(m["price_change_1y_pct"], 25.0)

    def test_loss_and_foreign_currency_withhold(self):
        row = {"unit": "USD", "metrics": {}, "per_share": {"shares_outstanding": 1000.0, "eps_diluted": -1.0},
               "size": {"buybacks": None, "dividends_paid": None}}
        sec_metrics.with_prices(row, 50.0, None, "2026-10-16")
        self.assertIsNone(row["metrics"]["pe_x"])
        self.assertIsNone(row["metrics"]["price_change_1y_pct"])
        self.assertIsNone(row["metrics"]["buyback_yield_pct"])
        row = {"unit": "VND", "metrics": {}, "per_share": {"shares_outstanding": 1000.0, "eps_diluted": 5.0},
               "size": {"buybacks": 1.0, "dividends_paid": 1.0}}
        sec_metrics.with_prices(row, 50.0, 40.0, "2026-10-16")
        # the close is in dollars, so the market value is; the dong earnings and yields are not comparable
        self.assertEqual(row["metrics"]["market_cap"], 50000.0)
        self.assertIsNone(row["metrics"]["pe_x"])
        self.assertIsNone(row["metrics"]["dividend_yield_pct"])


def _live():
    p = ROOT / "data" / "sec.duckdb"
    if not p.exists():
        return False
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'us_px_daily'").fetchone()[0])
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live(), "data/sec.duckdb without US prices")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_series_and_errors(self):
        r = self.c.get("/api/v1/us/prices/AAPL?start=2026-01-01")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(d["symbol"], "AAPL")
        self.assertGreater(d["count"], 100)
        self.assertEqual(d["bars"], sorted(d["bars"], key=lambda b: b["date"]))
        self.assertIn("Databento", d["provenance"]["credit"])
        self.assertEqual(self.c.get("/api/v1/us/prices/ZZZZQQ").status_code, 404)
        self.assertEqual(self.c.get("/api/v1/us/prices/AAPL?start=bad").status_code, 400)

    def test_screener_sorts_by_market_cap_and_names_the_formula(self):
        d = self.c.get("/api/v1/us/financials/screener?sort=market_cap&limit=5").json()
        self.assertEqual(len(d["rows"]), 5)
        caps = [r["metrics"]["market_cap"] for r in d["rows"]]
        self.assertEqual(caps, sorted(caps, reverse=True))
        self.assertTrue(all(r["price"]["close"] for r in d["rows"]))
        self.assertIn("market_cap", d["calc"])
        self.assertIsNotNone(d["price_provenance"])

    def test_health_row(self):
        rows = self.c.get("/api/v1/catalog/health").json().get("equity_extractors") or []
        self.assertIn("us-prices", {r["dataset"] for r in rows})


if __name__ == "__main__":
    unittest.main()
