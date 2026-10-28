# -*- coding: utf-8 -*-
"""A company's Price and Insiders and holders tabs are their own page.

Until 2026-10-28 they were the front page with ?t=, which showed for about two
seconds before the company replaced it. Links already shared must still land
on the company, now without the front page in between.
"""
import pathlib
import unittest

from fastapi.testclient import TestClient

from app import prerender
from app.main import app

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


class Moved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_old_ticker_links_go_to_the_page(self):
        for old in ("/?t=SBUX&view=owners", "/index.html?t=SBUX&view=owners"):
            r = self.client.get(old, follow_redirects=False)
            self.assertEqual(r.status_code, 301, old)
            self.assertEqual(r.headers["location"], "/us-stock.html?t=SBUX&view=owners")

    def test_the_front_page_itself_is_not_moved(self):
        for path in ("/", "/?q=Who%20owns%20Nvidia%3F"):
            r = self.client.get(path, follow_redirects=False)
            self.assertEqual(r.status_code, 200, path)

    def test_the_page_exists_and_is_titled_per_company(self):
        self.assertTrue((WEB / "us-stock.html").exists())
        self.assertIn("us-stock.html", prerender.ENTITY_PAGES)
        self.assertNotIn("index.html", prerender.ENTITY_PAGES)

    def test_no_link_still_points_at_the_front_page(self):
        for path in list((WEB / "assets").glob("*.js")) + list((WEB.parent / "app").glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("index.html?t=", text, path.name)
            self.assertNotIn("\"/?t=", text, path.name)
            self.assertNotIn("'/?t=", text, path.name)


if __name__ == "__main__":
    unittest.main()
