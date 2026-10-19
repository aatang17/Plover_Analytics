# -*- coding: utf-8 -*-
"""US reporting calendar (app/sec_calendar_api.py): expected dates rest on
last year's filing and roll off weekends; the API uses data/sec.duckdb when
present and skips otherwise.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_sec_calendar
"""
import datetime
import pathlib
import unittest

import duckdb
from fastapi.testclient import TestClient

from app import sec_calendar_api as cal

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ExpectedDateTest(unittest.TestCase):
    def test_one_year_on(self):
        self.assertEqual(cal._expected(datetime.date(2025, 10, 30)), datetime.date(2026, 10, 30))

    def test_weekend_rolls_to_monday(self):
        # 31 Oct 2025 was a Friday; 31 Oct 2026 is a Saturday -> Monday 2 Nov
        self.assertEqual(cal._expected(datetime.date(2025, 10, 31)), datetime.date(2026, 11, 2))

    def test_leap_day(self):
        self.assertEqual(cal._expected(datetime.date(2024, 2, 29)), datetime.date(2025, 2, 28)
                         if datetime.date(2025, 2, 28).weekday() < 5 else cal._expected(datetime.date(2024, 2, 29)))


def _live():
    p = ROOT / "data" / "sec.duckdb"
    if not p.exists():
        return False
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            n = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'sec_filings'").fetchone()[0]
            return bool(n)
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live(), "data/sec.duckdb not built")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_window_rows_rest_on_a_named_filing(self):
        r = self.c.get("/api/v1/us/earnings/calendar?start=2026-10-19&days=30")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertGreater(d["count"], 0)
        for row in d["rows"][:50]:
            self.assertTrue(row["basis"]["filed"] and row["basis"]["adsh"])
            self.assertIn(row["status"], ("expected", "filed", "overdue"))
            self.assertTrue("2026-10-19" <= row["expected_date"] <= "2026-11-18")
            if row["status"] == "filed":
                self.assertIsNotNone(row["actual_filed"])
        dates = [row["expected_date"] for row in d["rows"]]
        self.assertEqual(dates, sorted(dates))

    def test_filters_and_errors(self):
        d = self.c.get("/api/v1/us/earnings/calendar?start=2026-10-01&days=60&report=annual&exchange=Nasdaq").json()
        self.assertTrue(all(r["report"] == "annual report" for r in d["rows"]))
        self.assertTrue(all((r["exchange"] or "").lower() == "nasdaq" for r in d["rows"]))
        self.assertEqual(self.c.get("/api/v1/us/earnings/calendar?start=bad").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/earnings/calendar?report=weekly").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/earnings/calendar?start=2026-01-01&end=2025-01-01").status_code, 400)

    def test_company_history_and_upcoming(self):
        r = self.c.get("/api/v1/us/earnings/company/AAPL")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(d["cik"], 320193)
        self.assertTrue(d["filings"])
        self.assertTrue(all(f["lag_days"] is not None and 0 < f["lag_days"] < 200 for f in d["filings"]))
        for u in d["upcoming"]:
            self.assertEqual(u["cik"], 320193)
            self.assertNotEqual(u["status"], "filed")
        self.assertEqual(self.c.get("/api/v1/us/earnings/company/ZZZZQQ").status_code, 404)


if __name__ == "__main__":
    unittest.main()
