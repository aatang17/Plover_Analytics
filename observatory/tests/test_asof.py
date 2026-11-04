# -*- coding: utf-8 -*-
"""Point-in-time reads of the filing datasets.

The ceiling travels on a contextvar (app/asof.py), which means nothing about a
query that forgets to apply it fails loudly — it just quietly returns today's
filing. This file is the guarantee instead of that: it runs every company view
under a ceiling and fails if any filing in the response was filed after it.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_asof
Skips entirely without data/equity.duckdb.
"""
import datetime
import unittest

from fastapi.testclient import TestClient

from app import asof, equity_api, registry
from app.main import app
from app.tools import call_api

EQUITY = equity_api.DB_PATH.exists()

# Between Toyota's FY2025 filing (filed 2025-06-18) and its FY2026 one
# (2026-06-10), so a working ceiling changes the answer rather than merely
# not breaking it.
CEILING = datetime.date(2025, 7, 1)

# The fields that say when a document reached EDINET. A filing dated after the
# ceiling in a point-in-time response is the whole bug this guards against.
FILING_DATE_KEYS = ("filed_date", "submitted", "filed", "last_filed_date",
                    "first_filed_date")

# Dates that legitimately sit in the future of a filing: a buyback's
# acquisition window, a meeting yet to happen, a reporting period end.
IGNORE_KEYS = ("window_end", "window_start", "meeting_date", "period_end",
               "resolution_date", "requirement_date", "cover_date", "as_of",
               "checked_at", "next_meeting_date")


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return datetime.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _filing_dates(obj, path=""):
    """Every (path, date) under a key that names when a document was filed."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = "%s.%s" % (path, k) if path else k
            if k in FILING_DATE_KEYS:
                d = _as_date(v)
                if d is not None:
                    found.append((here, d))
            elif k not in IGNORE_KEYS:
                found.extend(_filing_dates(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            found.extend(_filing_dates(v, "%s[%d]" % (path, i)))
    return found


def _company(mid, code):
    # the same reader get_company and the composed company view use, so the
    # code goes in whichever argument the dataset's company view takes
    fn, arg = registry.company_reader(mid)
    return call_api(fn, **{arg: code})


@unittest.skipUnless(EQUITY, "equity database not present")
class CeilingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load()
        registry.bind(app)
        cls.datasets = [i for i in registry.ids()
                        if "company" in registry.get(i)["capabilities"]
                        and registry.available(i)]

    def test_no_company_view_leaks_a_filing_from_after_the_ceiling(self):
        """The contract. Every dataset, every nested row.

        A dataset whose response states no filing date at all would sail
        through this silently — AGM votes did exactly that until it was made
        to carry one — so each dataset must either be absent as of the ceiling
        or offer a date to check. Passing by saying nothing is not passing.
        """
        examined = {}
        for mid in self.datasets:
            with asof.scope(CEILING):
                try:
                    raw = _company(mid, "7203")
                except Exception as exc:                     # noqa: BLE001
                    if getattr(exc, "status_code", None) in (404, 503):
                        examined[mid] = "absent"              # no rows by then
                        continue
                    raise
            dates = _filing_dates(raw)
            examined[mid] = len(dates)
            for path, day in dates:
                self.assertLessEqual(
                    day, CEILING,
                    "%s leaked a filing from %s at %s (ceiling %s)"
                    % (mid, day, path, CEILING))
        silent = [m for m, n in examined.items() if n == 0]
        self.assertEqual(silent, [], "these returned rows but no filing date to "
                                     "check, so the ceiling is unverified: %s" % silent)
        self.assertTrue(examined, "no dataset was examined at all")

    def test_the_ceiling_actually_changes_the_answer(self):
        """A no-op would pass the test above trivially."""
        changed = []
        for mid in self.datasets:
            try:
                now = _company(mid, "7203")
            except Exception:                                 # noqa: BLE001
                continue
            with asof.scope(CEILING):
                try:
                    then = _company(mid, "7203")
                except Exception:                             # noqa: BLE001
                    changed.append(mid)                       # present now, absent then
                    continue
            if _filing_dates(now) != _filing_dates(then):
                changed.append(mid)
        self.assertTrue(changed, "the ceiling changed nothing anywhere")

    def test_no_ceiling_is_unchanged_behaviour(self):
        self.assertEqual(asof.clause("filed_date"), "")
        self.assertIsNone(asof.current())
        with asof.scope(None):
            self.assertEqual(asof.clause("filed_date"), "")

    def test_scope_is_restored(self):
        with asof.scope(CEILING):
            self.assertEqual(asof.current(), CEILING)
        self.assertIsNone(asof.current())

    def test_clause_shape(self):
        with asof.scope("2025-07-01"):
            self.assertEqual(asof.clause("filed_date"),
                             " AND filed_date <= DATE '2025-07-01'")
            self.assertEqual(asof.clause("submitted", "f"),
                             " AND f.submitted <= DATE '2025-07-01'")

    def test_bad_date_is_a_400(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            asof.parse("yesterday")
        self.assertEqual(ctx.exception.status_code, 400)


@unittest.skipUnless(EQUITY, "equity database not present")
class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load()
        registry.bind(app)
        cls.client = TestClient(app)

    def test_composed_endpoint_serves_a_point_in_time_view(self):
        r = self.client.get("/api/v1/company/7203?as_of=2025-07-01&compact=1")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["vintage"]["as_of"], "2025-07-01")
        self.assertEqual(body["vintage"]["basis"], "filed_date")
        for path, day in _filing_dates(body["datasets"]):
            self.assertLessEqual(day, CEILING, "%s at %s" % (day, path))

    def test_composed_endpoint_as_of_differs_from_today(self):
        now = self.client.get("/api/v1/company/7203?compact=1").json()
        then = self.client.get("/api/v1/company/7203?as_of=2025-07-01&compact=1").json()
        self.assertNotEqual(_filing_dates(now["datasets"]),
                            _filing_dates(then["datasets"]))

    def test_bad_as_of_is_a_400(self):
        self.assertEqual(
            self.client.get("/api/v1/company/7203?as_of=nope").status_code, 400)

    def test_coverage_honours_the_ceiling(self):
        # The facilities archive starts on 2025-04-16 (it was back-filled to
        # there), so a ceiling before that date must report the dataset as
        # missing rather than serving a later filing. Dated from the archive's
        # own floor, not from a fixed year, so back-filling it further does not
        # quietly turn this test into a no-op.
        r = self.client.get("/api/v1/company/7203/coverage?as_of=2025-01-01")
        self.assertEqual(r.status_code, 200)
        missing = [m["dataset"] for m in r.json()["coverage"]["missing"]]
        self.assertIn("facilities", missing)
        # And with no ceiling at all it is present, so the test above is
        # measuring the ceiling and not an absent dataset.
        now = self.client.get("/api/v1/company/7203/coverage").json()
        self.assertIn("facilities", now["coverage"]["present"])


if __name__ == "__main__":
    unittest.main()
