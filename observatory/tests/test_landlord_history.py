"""A company that moves from "Revenues" to lease income keeps its yearly revenue
(Equity Residential, now Vivmark: the answer stopped at fiscal 2019)."""
import datetime
import unittest
from unittest import mock

import duckdb

from app import stock_recipes as sr

D = datetime.date


def _rows(rows):
    def read(cur, cik, tags, kind):
        return [r for r in rows if r[0] in tags]
    return read


class LandlordHistory(unittest.TestCase):
    def _history(self, rows, sic):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, sic INTEGER, filed DATE)")
        con.execute("INSERT INTO sec_filings VALUES (1, ?, DATE '2026-02-13')", [sic])
        with mock.patch.object(sr, "_cf_rows", _rows(rows)), mock.patch.object(sr, "_fsds_rows", lambda *a: []):
            return dict(sr.annual_history(con, 1, {"id": "revenue", "label": "revenue"}, D(2025, 12, 31)))

    FILED = [("Revenues", D(2019, 12, 31), 2700.0, D(2020, 2, 20), "k19", "USD"),
             ("OperatingLeaseLeaseIncome", D(2019, 12, 31), 2701.0, D(2022, 2, 17), "k21", "USD"),
             ("OperatingLeaseLeaseIncome", D(2022, 12, 31), 2735.0, D(2025, 2, 13), "k24", "USD"),
             ("OperatingLeaseLeaseIncome", D(2024, 12, 31), 2980.0, D(2026, 2, 13), "k25", "USD"),
             ("OperatingLeaseLeaseIncome", D(2025, 12, 31), 3094.0, D(2026, 2, 13), "k25", "USD"),
             ("OperatingLeaseLeaseIncome", D(2022, 12, 31), 3094.0, D(2026, 2, 13), "k25", "USD")]

    def test_history_runs_on_through_lease_income_past_the_copied_year(self):
        h = self._history(self.FILED, 6798)
        self.assertEqual(h[D(2025, 12, 31)]["value"], 3094.0)
        self.assertEqual(h[D(2025, 12, 31)]["tag"], "OperatingLeaseLeaseIncome")
        self.assertEqual(h[D(2022, 12, 31)]["value"], 2735.0, "the FY2025 report's 2022 figure is the 2025 one on the wrong date")
        self.assertEqual((h[D(2019, 12, 31)]["value"], h[D(2019, 12, 31)]["tag"]), (2700.0, "Revenues"))

    def test_lease_income_beside_a_small_contract_line_is_added(self):
        h = self._history([("RevenueFromContractWithCustomerExcludingAssessedTax", D(2025, 12, 31), 13.0, D(2026, 2, 13), "k25", "USD"),
                           ("OperatingLeaseLeaseIncome", D(2025, 12, 31), 1573.0, D(2026, 2, 13), "k25", "USD")], 6798)
        self.assertEqual(h[D(2025, 12, 31)]["value"], 1586.0)

    def test_a_utilitys_lease_line_and_a_bank_are_left_alone(self):
        self.assertEqual(self._history([("OperatingLeaseLeaseIncome", D(2025, 12, 31), 60.0, D(2026, 2, 13), "k25", "USD")], 4911), {})
        h = self._history([("RevenueFromContractWithCustomerExcludingAssessedTax", D(2025, 12, 31), 100.0, D(2026, 2, 13), "k25", "USD"),
                           ("OperatingLeaseLeaseIncome", D(2025, 12, 31), 150.0, D(2026, 2, 13), "k25", "USD")], 6021)
        self.assertEqual(h[D(2025, 12, 31)]["value"], 100.0)


if __name__ == "__main__":
    unittest.main()
