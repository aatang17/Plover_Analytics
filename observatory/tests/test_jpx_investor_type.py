# -*- coding: utf-8 -*-
"""JPX trading by investor type (app/adapters/jpx_investor_type.py): the weekly
.xlsx JPX publishes from October 2026 is read by its headers, each block is
checked against its own printed balance and turnover, the aggregates are the
sums the old files printed, and both file shapes are found on the page.
No network, no data/ files."""
import datetime
import pathlib
import unittest

from app.adapters import jpx_investor_type as a
from app.adapters import xlsx

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jpx_stock_1_w_20261019_20261023.xlsx"

PAGE = u'''
<a href="/markets/statistics-equities/investor-type/t13vrt000002k0uf-att/stock_1_w_20261019_20261023.pdf">PDF</a>
<a href="/markets/statistics-equities/investor-type/t13vrt000002k0uf-att/stock_1_w_20261019_20261023.xlsx">Excel</a>
<a href="/markets/statistics-equities/investor-type/tvdivq0000014fy-att/revision_information_j.xls">訂正</a>
<a href="/markets/statistics-equities/investor-type/old-att/stock_val_1_260901.xls">x</a>
<a href="/markets/statistics-equities/investor-type/old-att/stock_vol_1_260901.xls">x</a>
'''


class Listing(unittest.TestCase):
    def test_both_shapes_are_found_and_named_as_jpx_names_them(self):
        got = a.listing(PAGE)
        self.assertEqual(sorted(got), ["stock_1_w_20261019_20261023.xlsx",
                                       "stock_val_260901.xls", "stock_vol_260901.xls"])
        self.assertTrue(got["stock_1_w_20261019_20261023.xlsx"].startswith("https://www.jpx.co.jp/"))

    def test_cache_entries_sort_old_before_new(self):
        old = a._entry("stock_val_260901.xls")
        new = a._entry("stock_1_w_20261019_20261023.xlsx")
        self.assertEqual(old["kind"], a.VALUE)
        self.assertEqual(new["start"], "20261019")
        self.assertLess(old["sort"], new["sort"])
        self.assertIsNone(a._entry("revision_information_j.xls"))


class WeeklyWorkbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = xlsx.sheets(FIXTURE.read_bytes())
        cls.got = a._read_week_book(cls.book, datetime.date(2026, 10, 19), FIXTURE.name)

    def test_published_leaves_are_read_where_jpx_put_them(self):
        g = self.got
        week = datetime.date(2026, 10, 19)
        # TSE Prime, shares: foreign institutions sold 8,549,290 thousand (column T)
        self.assertEqual(g[(week, "prime", "foreigners-institutions", "sales", "volume")], 8549290.0)
        self.assertEqual(g[(week, "prime", "trust-banks", "purchases", "volume")], 297875.0)
        # TSE Prime, value: proprietary cash sold ¥3,930,138,529 thousand (column D)
        self.assertEqual(g[(week, "prime", "proprietary-cash", "sales", "value")], 3930138529.0)
        self.assertEqual(g[(week, "two-markets", "other-financials", "purchases", "value")], 48527497.0)

    def test_aggregates_are_the_sums_the_old_files_printed(self):
        g, week = self.got, datetime.date(2026, 10, 19)
        for market in ("prime", "standard", "growth", "two-markets"):
            for kind in ("value", "volume"):
                for side in ("sales", "purchases"):
                    k = lambda c: g[(week, market, c, side, kind)]
                    self.assertEqual(k("proprietary"), k("proprietary-cash") + k("proprietary-margin"))
                    self.assertEqual(k("foreigners"), k("foreigners-institutions") + k("foreigners-individuals"))
                    self.assertEqual(k("financials"), k("insurers") + k("banks") + k("trust-banks") + k("other-financials"))
                    self.assertEqual(k("total"), k("proprietary") + k("brokerage"))
        self.assertEqual(g[(week, "prime", "proprietary", "sales", "volume")], 1564018.0 + 42266.0)

    def test_a_holiday_week_is_dated_by_its_monday(self):
        got = a._read_week_book(self.book, datetime.date(2026, 10, 22), FIXTURE.name)
        self.assertIn((datetime.date(2026, 10, 19), "prime", "total", "sales", "value"), got)

    def test_a_file_named_for_another_month_is_refused(self):
        with self.assertRaises(a.ValidationError):
            a._read_week_book(self.book, datetime.date(2026, 11, 2), FIXTURE.name)

    def test_a_moved_column_is_caught_by_the_printed_balance(self):
        book = xlsx.sheets(FIXTURE.read_bytes())
        grid = list(book.values())[0]
        grid[8]["E"] = (u"1", False)              # prime, shares, proprietary cash purchases
        with self.assertRaises(a.ValidationError) as ctx:
            a._read_week_book(book, datetime.date(2026, 10, 19), FIXTURE.name)
        self.assertIn(u"差引", str(ctx.exception))


class Validation(unittest.TestCase):
    def test_split_series_are_allowed_and_must_add_up(self):
        week = datetime.date(2026, 10, 19)
        book = xlsx.sheets(FIXTURE.read_bytes())
        got = a._read_week_book(book, week, FIXTURE.name)
        series, obs = [], []
        for (period, market, cat, side, kind), value in got.items():
            code = "%s.%s.%s.%s" % (market, cat, side, kind)
            series.append({"code": code})
            obs.append({"code": code, "period": period, "value": value})
        summary = a.validate(series, obs)
        self.assertEqual(summary["series"], 336)
        bad = [o for o in obs if o["code"] == "prime.foreigners-individuals.sales.value"][0]
        bad["value"] += 1000
        with self.assertRaises(a.ValidationError):
            a.validate(series, obs)


if __name__ == "__main__":
    unittest.main()
