# -*- coding: utf-8 -*-
"""The question box on a data page (web/assets/page-ask.js): on the three busiest
US pages, with what it needs loaded before it, and able to carry an apostrophe.
"""
import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PAGES = ("us-pay.html", "us-prices.html", "us-treasury.html")


class PageAsk(unittest.TestCase):
    def test_each_page_has_the_box_and_what_it_needs(self):
        for name in PAGES:
            html = (WEB / name).read_text(encoding="utf-8")
            self.assertIn("data-page-ask", html, name)
            self.assertIn("assets/page-ask.css", html, name)
            order = [html.index("assets/%s" % s) for s in ("format.js", "charts.js", "us-format.js", "page-ask.js")]
            self.assertEqual(order, sorted(order), name)

    def test_each_box_offers_questions(self):
        for name in PAGES:
            html = (WEB / name).read_text(encoding="utf-8")
            for found in re.findall(r'data-examples="([^"]*)"', html):
                self.assertGreaterEqual(len(found.split("|")), 2, name)

    def test_the_company_box_is_filled_by_the_page(self):
        self.assertIn("data-company", (WEB / "us-pay.html").read_text(encoding="utf-8"))
        self.assertIn("pageAsk.company(", (WEB / "assets" / "us-pay.js").read_text(encoding="utf-8"))

    def test_an_apostrophe_cannot_end_an_attribute(self):
        js = (WEB / "assets" / "page-ask.js").read_text(encoding="utf-8")
        self.assertNotIn("href='/?q=\" + encodeURIComponent", js)
        self.assertNotIn("placeholder='e.g. \" + esc(", js)


if __name__ == "__main__":
    unittest.main()
