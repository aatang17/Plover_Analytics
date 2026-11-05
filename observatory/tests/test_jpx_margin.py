# -*- coding: utf-8 -*-
"""JPX margin balances (app/adapters/jpx_margin.py): the 過去推移表 page is found
by name, each workbook is told apart by what it is, and the combined table JPX
publishes from 25 September 2026 is read by its stacked headers with the two
summed series built from checked parts. No network, no data/ files."""
import datetime
import pathlib
import unittest

from app.adapters import jpx_margin as a
from app.adapters import xlsx

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jpx_margin_combined_20261104.xlsx"

INDEX = u'''
<a href="/markets/statistics-equities/margin/01.html">銘柄別信用取引残高</a>
<a href="/markets/statistics-equities/margin/04.html"><span>信用取引現在高</span></a>
<a href="/markets/statistics-equities/margin/05.html"><span>信用取引現在高　過去推移表</span></a>
'''
INDEX_NO_TEXT = u'''
<a href="/markets/statistics-equities/margin/01.html"><img src="a.png"></a>
<a href="/markets/statistics-equities/margin/05.html"><img src="b.png"></a>
'''


class HistoryPage(unittest.TestCase):
    def test_found_by_link_text_whatever_the_number(self):
        self.assertEqual(a.history_page(INDEX),
                         "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html")

    def test_found_by_page_title_when_links_carry_no_words(self):
        titles = {"https://www.jpx.co.jp/markets/statistics-equities/margin/01.html": u"<title>銘柄別</title>",
                  "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html": u"<title>信用取引現在高　過去推移表 | JPX</title>"}
        self.assertEqual(a.history_page(INDEX_NO_TEXT, fetch_page=lambda u: titles[u]),
                         "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html")

    def test_last_known_address_when_nothing_matches(self):
        self.assertEqual(a.history_page(u"<p>nothing</p>", fetch_page=lambda u: ""), a.PAGE)


class CombinedTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = xlsx.sheets(FIXTURE.read_bytes())

    def test_layout_is_told_from_the_header_not_the_file_name(self):
        self.assertEqual(a.layout_of(self.book), a.LAYOUT_COMBINED)

    def test_published_figures_and_summed_series(self):
        got = a._read_combined(list(self.book.values())[0])
        week = datetime.date(2026, 10, 30)
        self.assertEqual(got["proprietary.sales.shares"][week], 78597.0)
        self.assertEqual(got["negotiable.sales.shares"][week], 77755.0)
        self.assertEqual(got["total.sales.shares"][week], 349230.0)
        self.assertEqual(got["total.purchases.value"][week], 6505887.0)
        # customers = standardised 委託 (192,876) + negotiable 委託 (77,755)
        self.assertEqual(got["customer.sales.shares"][week], 192876.0 + 77755.0)
        # standardised = 自己 (78,597) + standardised 委託 (192,876)
        self.assertEqual(got["standardized.sales.shares"][week], 78597.0 + 192876.0)
        self.assertNotIn("std-customer.sales.shares", got)
        self.assertEqual(sorted(got), sorted("%s.%s.%s" % (s, side, k)
                                             for s in ("customer", "proprietary", "total", "negotiable", "standardized")
                                             for side in ("sales", "purchases") for k in ("shares", "value")))
        self.assertEqual(min(got["total.sales.shares"]), datetime.date(2026, 9, 25))

    def test_parts_must_make_the_published_total(self):
        book = xlsx.sheets(FIXTURE.read_bytes())
        grid = list(book.values())[0]
        row = [r for r in grid if a._week(xlsx.cell_text(grid[r].get("A"))) == datetime.date(2026, 10, 30)][0]
        grid[row]["N"] = (u"999999", False)
        with self.assertRaises(a.ValidationError) as ctx:
            a._read_combined(grid)
        self.assertIn(u"合計", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
