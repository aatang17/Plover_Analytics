# -*- coding: utf-8 -*-
"""Earnings preview (app/us_preview_api.py): the expected date is a year
after the same quarter's results day and never before the latest results,
the move around a results day runs from the close before it to the close the
trading day after, and the endpoints answer on this machine's data when it
holds them. No network."""
import datetime
import unittest

from app import us_preview_api as pv

D = datetime.date


def days(*ds):
    return [(d, None, "u") for d in ds]


class ExpectedDate(unittest.TestCase):
    def test_a_year_after_the_same_quarter(self):
        r = days(D(2025, 10, 30), D(2026, 1, 29), D(2026, 4, 30), D(2026, 7, 30))
        n = pv.expected_next(r, today=D(2026, 9, 1))
        self.assertEqual(n["date"], "2026-10-29")          # 30 Oct 2025 + 364 days, a Thursday again
        self.assertEqual(n["based_on"], "2025-10-30")
        self.assertFalse(n["overdue"])

    def test_skips_quarters_already_reported(self):
        # results on 29 Oct 2026 are on file: the next is a year after January's
        r = days(D(2025, 10, 30), D(2026, 1, 29), D(2026, 4, 30), D(2026, 7, 30), D(2026, 10, 29))
        n = pv.expected_next(r, today=D(2026, 11, 3))
        self.assertEqual(n["date"], "2027-01-28")

    def test_overdue_when_the_day_passes(self):
        r = days(D(2025, 10, 30), D(2026, 1, 29), D(2026, 4, 30), D(2026, 7, 30))
        self.assertTrue(pv.expected_next(r, today=D(2026, 11, 10))["overdue"])

    def test_too_little_history_gives_no_date(self):
        self.assertIsNone(pv.expected_next(days(D(2026, 4, 30), D(2026, 7, 30)), today=D(2026, 8, 1)))


class MoveAroundResults(unittest.TestCase):
    series = [(D(2026, 7, 28), 100.0), (D(2026, 7, 29), 110.0), (D(2026, 7, 30), 120.0), (D(2026, 7, 31), 99.0)]

    def move(self, day):
        return pv._move(self.series, [d for d, _ in self.series], day)

    def test_close_before_to_close_after(self):
        d0, d1, m = self.move(D(2026, 7, 30))
        self.assertEqual((d0, d1), (D(2026, 7, 29), D(2026, 7, 31)))
        self.assertAlmostEqual(m, (99 / 110 - 1) * 100)

    def test_a_results_day_with_no_trading_uses_the_days_around_it(self):
        series = [(D(2026, 7, 31), 100.0), (D(2026, 8, 3), 105.0)]    # results on Saturday 1 August
        d0, d1, m = pv._move(series, [d for d, _ in series], D(2026, 8, 1))
        self.assertEqual((d0, d1), (D(2026, 7, 31), D(2026, 8, 3)))
        self.assertAlmostEqual(m, 5.0)

    def test_a_week_after(self):
        series = [(D(2026, 7, 28) + datetime.timedelta(days=k), 100.0 + k) for k in range(10)]
        d0, d1, m = pv._move(series, [d for d, _ in series], D(2026, 7, 29), after=5)
        self.assertEqual((d0, d1), (D(2026, 7, 28), D(2026, 8, 3)))   # the fifth close after the day
        self.assertAlmostEqual(m, 6.0)
        self.assertIsNone(pv._move(series, [d for d, _ in series], D(2026, 8, 4), after=5))

    def test_no_close_after_yet(self):
        self.assertIsNone(self.move(D(2026, 7, 31)))


class Endpoints(unittest.TestCase):
    """On this machine's data, when it is here: the shape a page relies on."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        from app import sec_api
        if not sec_api.DB_PATH.exists():
            raise unittest.SkipTest("data/sec.duckdb not built")
        cls.client = TestClient(app)
        r = cls.client.get("/api/v1/us/preview/upcoming?days=30")
        if r.status_code == 503:
            raise unittest.SkipTest("data/sec.duckdb without US events and prices")
        cls.upcoming = r.json()

    def test_upcoming(self):
        u = self.upcoming
        dates = [x["expected_date"] for x in u["expected"]]
        self.assertEqual(dates, sorted(dates))
        for x in u["expected"]:
            self.assertTrue(x["ticker"])

    def test_preview(self):
        r = self.client.get("/api/v1/us/preview/AAPL")
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()
        self.assertEqual(p["company"]["ticker"], "AAPL")
        self.assertLessEqual(len(p["quarters"]), 8)
        self.assertLessEqual(len(p["reactions"]), 8)
        for q in p["quarters"]:
            if q["q4_derived"]:
                continue
            if q["revenue"] and q["operating_margin_pct"] is not None:
                self.assertLess(abs(q["operating_margin_pct"]), 100)
        self.assertIn("expected_date", p["calc"])

    def test_unknown_ticker(self):
        self.assertEqual(self.client.get("/api/v1/us/preview/ZZZZZ").status_code, 404)


if __name__ == "__main__":
    unittest.main()
