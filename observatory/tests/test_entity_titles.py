# -*- coding: utf-8 -*-
"""Every address that serves one company, holder or series says which one.

2026-10-23: a Google result for stakes.html?c=5021 read "5% Filings" and sent
readers to Cosmo Energy whatever they had searched for, because every
company's 5% page shipped the same title and an empty heading.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_entity_titles
"""
import datetime
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import prerender
from app.main import app
from tests import _data

TITLE = re.compile(r"(?is)<title>(.*?)</title>")
DESC = re.compile(r'(?is)<meta name="description" content="([^"]*)"')


def _h1(html, h1_id):
    m = re.search(r'(?is)<h1[^>]*\sid="%s"[^>]*>(.*?)</h1>' % h1_id, html)
    return m.group(1) if m else None


class ViewsWithoutData(unittest.TestCase):
    """The views' own logic, on rows supplied here: runs in CI."""

    def setUp(self):
        prerender._entity_cache.clear()

    def test_a_company_is_named_in_title_heading_and_description(self):
        row = (12, datetime.date(2026, 5, 27), "トヨタ自動車株式会社", None)
        with patch.object(prerender, "_one", return_value=row), \
                patch.object(prerender, "company_meta", return_value=None):
            html = prerender.inject(
                (prerender.WEB_DIR / "stakes.html").read_text(encoding="utf-8"),
                "stakes.html", query_string="c=7203")
        self.assertIn("(7203) — 5% Shareholders", TITLE.search(html).group(1))
        self.assertEqual(_h1(html, "co-name"), "トヨタ自動車株式会社")
        self.assertEqual(_h1(html, "ho-name"), "")
        self.assertIn("latest filed 27 May 2026", DESC.search(html).group(1))

    def test_a_holder_lands_in_the_holder_heading(self):
        row = (3, datetime.date(2026, 9, 1), "ジェーン・ストリート", "Jane Street Asia Trading Limited")
        with patch.object(prerender, "_one", return_value=row):
            html = prerender.inject(
                (prerender.WEB_DIR / "shorts.html").read_text(encoding="utf-8"),
                "shorts.html", query_string="h=Jane+Street+Asia+Trading")
        self.assertTrue(TITLE.search(html).group(1).startswith("Jane Street Asia Trading Limited"))
        self.assertEqual(_h1(html, "h-name"), "Jane Street Asia Trading Limited")

    def test_nothing_on_file_is_kept_out_of_the_index(self):
        with patch.object(prerender, "_one", return_value=(0, None, None, None)):
            html = prerender.inject(
                (prerender.WEB_DIR / "stakes.html").read_text(encoding="utf-8"),
                "stakes.html", query_string="c=9999")
        self.assertIn('content="noindex,follow"', html)
        self.assertEqual(TITLE.search(html).group(1), "5% Filings · Plover Analytics")

    def test_a_typed_url_is_kept_out_without_a_lookup(self):
        with patch.object(prerender, "_one") as one:
            self.assertEqual(prerender.entity_meta("risks.html", "c=../../x"), prerender.NOINDEX)
        one.assert_not_called()

    def test_a_page_with_its_own_description_describes_the_company(self):
        row = (2, datetime.date(2026, 3, 31), "トヨタ自動車株式会社", None)
        with patch.object(prerender, "_one", return_value=row), \
                patch.object(prerender, "company_meta", return_value=None):
            html = prerender.inject(
                (prerender.WEB_DIR / "risks.html").read_text(encoding="utf-8"),
                "risks.html", query_string="c=7203")
        self.assertEqual(len(DESC.findall(html)), 1)
        self.assertIn("7203", DESC.search(html).group(1))

    def test_the_market_view_keeps_the_page_title(self):
        for page in ("stakes.html", "ownership.html", "shorts.html", "risks.html",
                     "earnings.html", "facilities.html", "financials.html",
                     "governance.html", "us-pay.html", "us-companies.html", "index.html"):
            self.assertIsNone(prerender.entity_meta(page, ""), page)
            self.assertIsNone(prerender.entity_meta(page, "view=activist"), page)

    def test_company_c_and_code_are_one_page(self):
        meta = {"title": "T", "description": "D", "h1": "H", "noscript": "N"}
        with patch.object(prerender, "company_meta", return_value=meta):
            out = prerender.entity_meta("company.html", "c=7203")
        self.assertTrue(out["canonical"].endswith("/company.html?code=7203"))


@unittest.skipUnless(_data.EQUITY, _data.NO_EQUITY)
class ServedFromRealData(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_the_link_from_the_search_result_names_cosmo_energy(self):
        html = self.client.get("/stakes.html?c=5021").text
        self.assertIn("(5021) — 5% Shareholders", TITLE.search(html).group(1))
        self.assertIn("COSMO", TITLE.search(html).group(1).upper())
        self.assertIn("5021", DESC.search(html).group(1))

    def test_two_companies_never_share_a_title(self):
        a = TITLE.search(self.client.get("/stakes.html?c=5021").text).group(1)
        b = TITLE.search(self.client.get("/stakes.html?c=7203").text).group(1)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
