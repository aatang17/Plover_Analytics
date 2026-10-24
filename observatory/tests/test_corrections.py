# -*- coding: utf-8 -*-
"""Corrected annual reports (訂正有価証券報告書, EDINET doc type 130).

2026-10-24: every annual-report reader took doc type 120 only, so the ~650
corrections a year from listed companies never reached a page. A correction
is now read as a filing of its own, beside the original; EDINET lists it with
no period, which the description supplies; and every per-year series serves
the newest filed for each year, as of any ceiling. No network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_corrections
"""
import datetime
import os
import sys
import unittest

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import extract  # noqa: E402
from app import asof  # noqa: E402


class Period(unittest.TestCase):
    def test_western_and_era_dates(self):
        self.assertEqual(extract.description_period(
            u"訂正有価証券報告書－第99期(2025/04/01－2026/03/31)"), ("2025-04-01", "2026-03-31"))
        self.assertEqual(extract.description_period(
            u"訂正有価証券報告書－第18期(平成29年4月1日－平成30年3月31日)"), ("2017-04-01", "2018-03-31"))
        self.assertEqual(extract.description_period(
            u"訂正有価証券報告書－第16期(平成31年1月1日－令和元年12月31日)"), ("2019-01-01", "2019-12-31"))
        self.assertIsNone(extract.description_period(u"訂正有価証券報告書"))

    def test_a_correction_takes_its_period_from_the_description_or_its_original(self):
        meta = {
            "P": {"docID": "P", "docTypeCode": "120", "periodStart": "2024-04-01", "periodEnd": "2025-03-31"},
            "C1": {"docID": "C1", "docTypeCode": "130", "periodEnd": None,
                   "docDescription": u"訂正有価証券報告書－第99期(2025/04/01－2026/03/31)"},
            "C2": {"docID": "C2", "docTypeCode": "130", "periodEnd": None, "parentDocID": "P",
                   "docDescription": u"訂正有価証券報告書"},
        }
        extract.dated_corrections(meta)
        self.assertEqual(meta["C1"]["periodEnd"], "2026-03-31")
        self.assertEqual((meta["C2"]["periodStart"], meta["C2"]["periodEnd"]), ("2024-04-01", "2025-03-31"))

    def test_readers_take_corrections_but_never_one_without_a_year(self):
        self.assertTrue(extract.annual({"docTypeCode": "120"}, {}))
        self.assertTrue(extract.annual({"docTypeCode": "130", "periodEnd": "2026-03-31"}, {}))
        self.assertFalse(extract.annual({"docTypeCode": "130", "periodEnd": None}, {}))
        self.assertFalse(extract.annual({"docTypeCode": "140"}, {}))


class NewestPerYear(unittest.TestCase):
    def setUp(self):
        self.c = duckdb.connect(":memory:")
        self.c.execute("CREATE TABLE eq_x (doc_id VARCHAR, edinet_code VARCHAR, period_end DATE, "
                       "filed_date DATE, status VARCHAR, v INTEGER)")
        self.c.executemany("INSERT INTO eq_x VALUES (?,?,?,?,?,?)", [
            ("A24", "E1", "2025-03-31", "2025-06-20", "clean", 10),
            ("A25", "E1", "2026-03-31", "2026-06-20", "clean", 20),
            ("C25", "E1", "2026-03-31", "2026-07-10", "clean", 21),    # the correction
            ("F25", "E1", "2026-03-31", "2026-08-01", "failed", 99),   # a correction that failed to parse
        ])

    def series(self):
        return self.c.execute(
            "SELECT f.doc_id FROM eq_x f WHERE f.status = 'clean'" + asof.clause("filed_date", "f") +
            asof.newest("f", "eq_x", ok="newer.status = 'clean'") + " ORDER BY f.period_end").fetchall()

    def test_a_correction_replaces_its_year_once(self):
        self.assertEqual(self.series(), [("A24",), ("C25",)])

    def test_before_the_correction_was_filed_the_report_stands(self):
        with asof.scope(datetime.date(2026, 7, 1)):
            self.assertEqual(self.series(), [("A24",), ("A25",)])


class Buybacks(unittest.TestCase):
    """A buyback correction (230) replaces the one report it names, never a
    month: some filers state one as_of across several months' reports."""

    def setUp(self):
        import buyback
        self.bb = buyback
        self.c = duckdb.connect(":memory:")
        self.c.execute(buyback.DDL)
        self.c.executemany("INSERT INTO eq_buyback_filings VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("O", "E1", "1111", "x", "2026-07-01", "2026-06-30", "s", "bb-2", "clean", None),
            ("C1", "E1", "1111", "x", "2026-07-15", "2026-06-30", "s", "bb-2", "clean", None),
            ("C2", "E1", "1111", "x", "2026-07-20", "2026-06-30", "s", "bb-2", "failed", None),
            # same stated month, a different report: both stand
            ("A", "E2", "2222", "y", "2026-02-12", "2026-01-31", "s", "bb-2", "clean", None),
            ("B", "E2", "2222", "y", "2026-03-12", "2026-01-31", "s", "bb-2", "clean", None)])
        self.c.executemany("INSERT INTO eq_buyback_corrections VALUES (?,?)", [("C1", "O"), ("C2", "O")])

    def test_the_newest_good_correction_stands_and_other_reports_are_untouched(self):
        got = self.c.execute("SELECT f.doc_id FROM eq_buyback_filings f WHERE " + self.bb.CURRENT +
                             " AND f.status <> 'failed' ORDER BY 1").fetchall()
        self.assertEqual(got, [("A",), ("B",), ("C1",)])


if __name__ == "__main__":
    unittest.main()
