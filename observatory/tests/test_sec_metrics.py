# -*- coding: utf-8 -*-
"""The US ratios (app/sec_metrics.py), the company universe loader
(equity/sec_universe.py) and the endpoints that serve them.

The ratio tests build their inputs in memory. The universe tests parse the
SEC's two files from small fixtures. The API tests use data/sec.duckdb when
it holds the universe tables and skip cleanly when it does not.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_sec_metrics
"""
import datetime
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

import duckdb
from fastapi.testclient import TestClient

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

_spec = importlib.util.spec_from_file_location(
    "sec_universe", str(ROOT / "equity" / "sec_universe.py"))
sec_universe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sec_universe)

from app import sec_metrics  # noqa: E402

P = datetime.date(2025, 12, 31)
P0 = datetime.date(2024, 12, 31)


def head(**kw):
    h = {"adsh": "0000000001-26-000001", "cik": 1, "name": "ALPHA CORP", "sic": 3674,
         "country_ba": "US", "form": "10-K", "period": P, "fy": 2025, "filed": datetime.date(2026, 2, 27),
         "status": "clean"}
    h.update(kw)
    return h


def facts(**values):
    """(tag, ddate, qtrs, uom, value) tuples from a {tag: (cur, prior)} map;
    instants carry qtrs 0, durations 4."""
    out = []
    instants = {"Assets", "StockholdersEquity", "CashAndCashEquivalentsAtCarryingValue"}
    for tag, (cur, prior) in values.items():
        q = 0 if tag in instants else 4
        if cur is not None:
            out.append((tag, P, q, "USD", float(cur)))
        if prior is not None:
            out.append((tag, P0, q, "USD", float(prior)))
    return out


class CompositeRevenueTest(unittest.TestCase):
    def test_bank_revenue_is_interest_plus_fees(self):
        # Cullen/Frost: $122mn of fees tagged as revenue from contracts, $2.4bn of interest income beside it
        row = sec_metrics.compute_row(head(sic=6021), facts(
            RevenueFromContractWithCustomerExcludingAssessedTax=(121.5, 110.0),
            InterestAndDividendIncomeOperating=(2420.7, 2300.0), NoninterestIncome=(499.1, 450.0),
            NetIncomeLoss=(648.6, 600.0)))
        self.assertAlmostEqual(row["size"]["revenue"], 2420.7 + 499.1, places=3)
        self.assertEqual(row["tags"]["revenue"], "InterestAndDividendIncomeOperating + NoninterestIncome")
        self.assertAlmostEqual(row["prior"]["revenue"], 2750.0, places=3)
        self.assertLess(row["metrics"]["net_margin_pct"], 100)

    def test_landlord_revenue_is_lease_income_plus_the_contract_line(self):
        row = sec_metrics.compute_row(head(sic=6798), facts(
            RevenueFromContractWithCustomerExcludingAssessedTax=(12.9, 12.0), OperatingLeaseLeaseIncome=(1573.5, 1500.0),
            NetIncomeLoss=(384.5, 300.0)))
        self.assertAlmostEqual(row["size"]["revenue"], 1586.4, places=3)
        self.assertEqual(row["tags"]["revenue"], "OperatingLeaseLeaseIncome + RevenueFromContractWithCustomerExcludingAssessedTax")

    def test_a_filers_own_revenues_line_is_kept(self):
        row = sec_metrics.compute_row(head(sic=6021), facts(
            Revenues=(3000.0, 2800.0), InterestAndDividendIncomeOperating=(2420.7, 2300.0), NoninterestIncome=(499.1, 450.0)))
        self.assertEqual((row["size"]["revenue"], row["tags"]["revenue"]), (3000.0, "Revenues"))
        plain = sec_metrics.compute_row(head(sic=3674), facts(RevenueFromContractWithCustomerExcludingAssessedTax=(1000.0, 900.0)))
        self.assertEqual(plain["tags"]["revenue"], "RevenueFromContractWithCustomerExcludingAssessedTax")


class PriceFiguresTest(unittest.TestCase):
    def test_depositary_share_ratio_in_pe_and_a_foreign_currency_market_value(self):
        row = {"unit": "USD", "size": {"buybacks": None, "dividends_paid": 10.0}, "metrics": {},
               "per_share": {"shares_outstanding": 3000.0, "eps_diluted": 0.48}, "period_end": "2026-03-31"}
        basis = {"market_cap": 1000 * 23.84, "shares": 3000.0, "listed_shares": 1000.0, "per_listed_share": 3.0,
                 "method": "one count", "classes": [], "note": None}
        sec_metrics.with_prices(row, 23.84, 20.0, "2026-10-16", basis)
        self.assertAlmostEqual(row["metrics"]["pe_x"], 23.84 / (0.48 * 3), places=3)
        self.assertAlmostEqual(row["metrics"]["market_cap"], 23840.0)
        self.assertAlmostEqual(row["metrics"]["dividend_yield_pct"], 10.0 / 23840 * 100, places=4)
        # a yen filer has a dollar market value and no P/E
        yen = {"unit": "JPY", "size": {"buybacks": None, "dividends_paid": None}, "metrics": {},
               "per_share": {"shares_outstanding": 13000.0, "eps_diluted": 300.0}, "period_end": "2026-03-31"}
        sec_metrics.with_prices(yen, 194.61, None, "2026-10-16", {"market_cap": 1300 * 194.61, "per_listed_share": 10.0,
                                                                  "shares": 13000.0, "listed_shares": 1300.0, "method": "one count",
                                                                  "classes": [], "note": None})
        self.assertAlmostEqual(yen["metrics"]["market_cap"], 1300 * 194.61, places=2)
        self.assertIsNone(yen["metrics"]["pe_x"])


class RatioTest(unittest.TestCase):
    def test_averages_growth_and_cash(self):
        row = sec_metrics.compute_row(head(), facts(
            Revenues=(1000, 800), OperatingIncomeLoss=(200, None), NetIncomeLoss=(150, 100),
            Assets=(2000, 1800), StockholdersEquity=(1000, 800),
            CashAndCashEquivalentsAtCarryingValue=(300, None),
            NetCashProvidedByUsedInOperatingActivities=(250, None),
            PaymentsToAcquirePropertyPlantAndEquipment=(50, None),
            PaymentsOfDividends=(30, None), PaymentsForRepurchaseOfCommonStock=(60, None)))
        m = row["metrics"]
        self.assertAlmostEqual(m["roe_pct"], 150 / 900 * 100, places=3)       # average equity
        self.assertAlmostEqual(m["roa_pct"], 150 / 1900 * 100, places=3)
        self.assertAlmostEqual(m["operating_margin_pct"], 20.0, places=3)
        self.assertAlmostEqual(m["equity_ratio_pct"], 50.0, places=3)          # closing balances
        self.assertAlmostEqual(m["revenue_growth_pct"], 25.0, places=3)
        self.assertAlmostEqual(m["profit_growth_pct"], 50.0, places=3)
        self.assertAlmostEqual(m["fcf"], 200.0, places=3)
        self.assertAlmostEqual(m["payout_ratio_pct"], 20.0, places=3)
        self.assertAlmostEqual(m["buyback_ratio_pct"], 40.0, places=3)
        self.assertEqual(m["shareholder_return"], 90.0)
        self.assertEqual(row["unit"], "USD")
        self.assertEqual(row["tags"]["revenue"], "Revenues")
        self.assertEqual(row["prior"]["equity"], 800.0)
        self.assertEqual(row["flags"], [])

    def test_negative_equity_withholds_roe(self):
        row = sec_metrics.compute_row(head(), facts(
            Revenues=(1000, None), NetIncomeLoss=(150, None),
            Assets=(2000, 1800), StockholdersEquity=(-100, -300)))
        self.assertIsNone(row["metrics"]["roe_pct"])
        self.assertIn("negative_equity", row["flags"])
        self.assertIsNotNone(row["metrics"]["roa_pct"])

    def test_missing_opening_balance_withholds_not_substitutes(self):
        row = sec_metrics.compute_row(head(), facts(
            NetIncomeLoss=(150, None), Assets=(2000, None), StockholdersEquity=(1000, None)))
        self.assertIsNone(row["metrics"]["roe_pct"])
        self.assertIsNone(row["metrics"]["roa_pct"])
        self.assertIn("no_opening_equity", row["flags"])
        self.assertAlmostEqual(row["metrics"]["equity_ratio_pct"], 50.0)

    def test_loss_withholds_payout_growth_and_conversion(self):
        row = sec_metrics.compute_row(head(), facts(
            Revenues=(1000, 900), NetIncomeLoss=(-50, -20),
            NetCashProvidedByUsedInOperatingActivities=(10, None), PaymentsOfDividends=(5, None)))
        m = row["metrics"]
        self.assertIsNone(m["payout_ratio_pct"])
        self.assertIsNone(m["profit_growth_pct"])
        self.assertIsNone(m["cash_conversion_x"])
        self.assertAlmostEqual(m["net_margin_pct"], -5.0)

    def test_financial_filer_has_no_fcf_or_cash_ratio(self):
        row = sec_metrics.compute_row(head(sic=6021), facts(
            Revenues=(1000, None), NetIncomeLoss=(100, None), Assets=(9000, 8000),
            StockholdersEquity=(800, 700), CashAndCashEquivalentsAtCarryingValue=(500, None),
            NetCashProvidedByUsedInOperatingActivities=(300, None),
            PaymentsToAcquirePropertyPlantAndEquipment=(20, None)))
        self.assertIn("financial_company", row["flags"])
        self.assertIsNone(row["metrics"]["fcf"])
        self.assertIsNone(row["metrics"]["cash_to_assets_pct"])
        self.assertIsNotNone(row["metrics"]["roe_pct"])

    def test_unit_is_the_core_lines_currency(self):
        f = facts(Revenues=(5000, None), Assets=(9000, None), NetIncomeLoss=(100, None))
        f = [(t, d, q, "VND", v) for t, d, q, u, v in f]
        f.append(("EarningsPerShareBasic", P, 4, "USD", 0.1))
        row = sec_metrics.compute_row(head(), f)
        self.assertEqual(row["unit"], "VND")

    def test_prior_period_drifts_for_53_week_years(self):
        f = facts(Revenues=(1000, None), NetIncomeLoss=(100, None), Assets=(2000, None))
        f.append(("Assets", datetime.date(2025, 1, 4), 0, "USD", 1800.0))   # 361 days earlier
        row = sec_metrics.compute_row(head(), f)
        self.assertEqual(row["prior"]["total_assets"], 1800.0)
        self.assertAlmostEqual(row["metrics"]["roa_pct"], 100 / 1900 * 100, places=3)

    def test_every_metric_has_a_formula_and_label(self):
        for k in sec_metrics.METRIC_ORDER:
            self.assertIn(k, sec_metrics.FORMULAS)
            self.assertIn(k, sec_metrics.METRIC_LABELS)
        self.assertEqual(len(sec_metrics.metric_defs()), len(sec_metrics.METRIC_ORDER))


class UniverseParseTest(unittest.TestCase):
    def test_tickers_collapse_share_classes(self):
        doc = {"fields": ["cik", "name", "ticker", "exchange"],
               "data": [[1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"],
                        [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"],
                        [320193, "Apple Inc.", "AAPL", "Nasdaq"],
                        [99, "No Ticker Co", "", None]] + [[1000 + i, "X%d" % i, "T%d" % i, "NYSE"] for i in range(5000)]}
        rows = sec_universe.parse_tickers(json.dumps(doc).encode())
        cos = {c["cik"]: c for c in sec_universe.companies_from(rows)}
        self.assertEqual(cos[1652044]["tickers"], ["GOOGL", "GOOG"])
        self.assertEqual(cos[320193]["exchanges"], ["Nasdaq"])
        self.assertNotIn(99, cos)

    def test_tickers_file_shape_change_is_refused(self):
        doc = {"fields": ["cik", "title"], "data": []}
        with self.assertRaises(sec_universe.UniverseError):
            sec_universe.parse_tickers(json.dumps(doc).encode())

    def test_sic_table_rows(self):
        rows = "".join("<tr><td>%d</td><td>Office %d</td><td>INDUSTRY &amp; %d</td></tr>" % (i, i, i)
                       for i in range(100, 100 + 320))
        html = "<html><table><tr><th>SIC Code</th><th>Office</th><th>Industry Title</th></tr>%s</table></html>" % rows
        out = sec_universe.parse_sic(html.encode())
        self.assertEqual(len(out), 320)
        self.assertEqual(out[0], (100, "Office 100", "INDUSTRY & 100"))

    def test_load_into_empty_db_and_pull_log(self):
        tmp = tempfile.mkdtemp(prefix="secu-")
        db = os.path.join(tmp, "sec.duckdb")
        con = duckdb.connect(db)
        con.execute(sec_universe.SCHEMA_SQL)
        sec_universe.load(con, [{"cik": 1, "name": "A", "tickers": ["A"], "exchanges": ["NYSE"]}],
                          [(3674, "Office", "SEMICONDUCTORS")])
        sec_universe.record_pull(con, "tickers", "u", "sha", 10, "p", "ok", 1)
        self.assertEqual(con.execute("SELECT ticker, exchange FROM sec_companies").fetchall(), [("A", "NYSE")])
        self.assertEqual(con.execute("SELECT count(*) FROM sec_universe_pulls WHERE status='ok'").fetchone()[0], 1)
        con.close()


def _live_db_has_universe():
    path = ROOT / "data" / "sec.duckdb"
    if not path.exists():
        return False
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
            return "sec_companies" in names and "sec_filings" in names
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live_db_has_universe(), "data/sec.duckdb without the universe tables")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_ticker_resolves_to_apple(self):
        r = self.c.get("/api/v1/us/financials/metrics/AAPL")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(d["cik"], 320193)
        self.assertEqual(d["ticker"], "AAPL")
        self.assertIn("roe_pct", d["metrics"])
        self.assertEqual(d["provenance"]["trust"], "derived")
        self.assertIn("formula", d["metric_defs"][0])

    def test_unknown_ticker_is_404_not_a_guess(self):
        self.assertEqual(self.c.get("/api/v1/us/financials/metrics/ZZZZQQ").status_code, 404)

    def test_screener_ranks_and_filters(self):
        r = self.c.get("/api/v1/us/financials/screener?sort=operating_margin_pct&sic=37&min_revenue=1e9&limit=10")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertGreater(d["ranked"], 0)
        vals = [row["sort_value"] for row in d["rows"]]
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertTrue(all(str(row["sic"]).startswith("37") for row in d["rows"]))
        self.assertTrue(all(row["unit"] == "USD" for row in d["rows"]))
        self.assertEqual(d["rows"][0]["rank"], 1)

    def test_screener_rejects_bad_sort_and_number(self):
        self.assertEqual(self.c.get("/api/v1/us/financials/screener?sort=nonsense").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/financials/screener?roe_min=abc").status_code, 400)

    def test_companies_search_by_ticker(self):
        d = self.c.get("/api/v1/us/financials/companies?q=NVDA").json()
        self.assertTrue(any(c["cik"] == 1045810 for c in d["companies"]))
        self.assertTrue(all("ticker" in c for c in d["companies"]))

    def test_industries_and_options(self):
        d = self.c.get("/api/v1/us/financials/industries?q=semiconductor").json()
        self.assertTrue(any(i["sic"] == 3674 for i in d["industries"]))
        o = self.c.get("/api/v1/us/financials/screener/options").json()
        self.assertGreater(o["universe"], 1000)
        self.assertTrue(o["industries"] and o["exchanges"])

    def test_health_has_universe_row(self):
        rows = self.c.get("/api/v1/catalog/health").json().get("equity_extractors") or []
        names = {r["dataset"] for r in rows}
        self.assertIn("sec-universe", names)
        self.assertIn("sec-financials", names)


if __name__ == "__main__":
    unittest.main()
