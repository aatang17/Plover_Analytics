# -*- coding: utf-8 -*-
"""robots.txt and sitemap.xml: what we hand a crawler matches what we serve.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_seo
"""
import pathlib
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app import seo
from app.main import app
from tests import _data

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _locs(body):
    return [e.text for e in ET.fromstring(body).iter(NS + "loc")]


class SitemapTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        # /sitemap.xml is an index: it names the two child sitemaps and not a
        # single page. Reading only the top file therefore finds two URLs
        # that both answer 200 and proves nothing about the pages — which is
        # what these tests did for the first day after the split shipped.
        cls.body = cls.client.get("/sitemap.xml").text
        cls.pages_body = cls.client.get("/sitemap-pages.xml").text
        cls.companies_body = cls.client.get("/sitemap-companies.xml").text
        cls.children = _locs(cls.body)
        cls.pages = _locs(cls.pages_body)
        cls.companies = _locs(cls.companies_body)
        cls.locs = cls.pages + cls.companies

    def test_it_is_served_ahead_of_the_static_mount(self):
        # The mount answers "/" and everything under it. Registered after the
        # sitemap routes it would shadow every one of these files with a 404.
        for path, kind in (("/sitemap.xml", "application/xml"),
                           ("/sitemap-pages.xml", "application/xml"),
                           ("/sitemap-companies.xml", "application/xml"),
                           ("/robots.txt", "text/plain")):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(kind, r.headers["content-type"])

    def test_the_index_names_both_halves(self):
        self.assertIn(seo.SITE_BASE_URL + "/sitemap-pages.xml", self.children)
        # The companies half is left out of the index rather than served
        # empty when the equity database is unreadable, so whether it is
        # named has to track whether there is anything behind it. Both states
        # are correct; disagreement between them is not.
        self.assertEqual(
            seo.SITE_BASE_URL + "/sitemap-companies.xml" in self.children,
            bool(self.companies))
        # An index holds sitemaps, never pages: a page listed here is a page
        # Google will try to parse as XML.
        self.assertNotIn(seo.SITE_BASE_URL + "/", self.children)

    def test_every_listed_url_is_a_page_we_actually_serve(self):
        # A sitemap entry that 404s is worse than no sitemap: it is the one
        # error Search Console reports against the whole site.
        #
        # Every built page, and the ends of the company list rather than all
        # four thousand of it — they are one template and one query, so the
        # middle re-proves the first at a minute a run.
        self.assertTrue(self.pages)
        for loc in self.pages + self.companies[:3] + self.companies[-3:]:
            path = loc[len(seo.SITE_BASE_URL):]
            self.assertEqual(self.client.get(path).status_code, 200, loc)

    def test_every_page_is_listed_unless_it_says_noindex(self):
        # The page list is the directory, so a page added to the site is in
        # the sitemap without anyone remembering a second list.
        listed = set(self.pages)
        for page in WEB.glob("*.html"):
            name = "" if page.name == "index.html" else page.name
            url = seo.SITE_BASE_URL + "/" + name
            with page.open(encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
            # Two ways out of the index. A page can decline it in its own
            # head; and the Search Console verification file is excluded by
            # name, because it must be served byte-for-byte as Google wrote
            # it and so cannot carry a noindex tag.
            if seo._NOINDEX.search(head) or seo._VERIFICATION.match(page.name):
                self.assertNotIn(url, listed, "%s is not indexable" % page.name)
            else:
                self.assertIn(url, listed, page.name)

    def test_the_admin_console_is_never_advertised(self):
        for body in (self.body, self.pages_body, self.companies_body):
            self.assertNotIn("/admin", body)
        self.assertIn("Disallow: /admin.html", self.client.get("/robots.txt").text)

    def test_robots_keeps_crawlers_off_sign_in(self):
        self.assertIn("Disallow: /signin.html", self.client.get("/robots.txt").text)

    def test_a_markdown_copy_names_its_page_as_the_original(self):
        r = self.client.get("/methodology.md")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["link"],
                         '<%s/methodology.html>; rel="canonical"' % seo.SITE_BASE_URL)
        r = self.client.get("/index.md?utm_source=x")
        self.assertEqual(r.headers["link"], '<%s/>; rel="canonical"' % seo.SITE_BASE_URL)

    def test_robots_leaves_the_api_crawlable(self):
        # Pages draw their numbers from /api after load. A crawler forbidden
        # the API renders empty charts and indexes those.
        self.assertNotIn("Disallow: /api", self.client.get("/robots.txt").text)

    def test_llms_txt_says_what_a_trust_label_means(self):
        got = self.client.get("/llms.txt")
        self.assertEqual(got.status_code, 200)
        self.assertTrue(got.headers["content-type"].startswith("text/plain"))
        body = got.text
        # The whole point of the file: an assistant must not report a rate this
        # platform calculated as an official statistic.
        for phrase in ('trust: "official"', 'trust: "derived"', "formula",
                       "never zero", "How to cite"):
            self.assertIn(phrase, body, phrase)

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_llms_txt_lists_datasets_that_actually_serve_data(self):
        from app import registry
        body = self.client.get("/llms.txt").text
        live = [d for d in registry.datasets() if d.get("available")]
        self.assertTrue(live, "no dataset is available to list")
        for card in live:
            self.assertIn("`%s`" % card["id"], body, card["id"])

    def test_llms_txt_never_lists_a_dataset_without_data(self):
        # Runs with no database too: then nothing is available, and llms.txt
        # must name no dataset at all rather than advertise empty ones.
        from app import registry
        body = self.client.get("/llms.txt").text
        for card in registry.datasets():
            if not card.get("available"):
                self.assertNotIn("`%s`" % card["id"], body, card["id"])

    def test_robots_points_at_llms_txt(self):
        self.assertIn("/llms.txt", self.client.get("/robots.txt").text)

    def test_one_canonical_host(self):
        # Both files name the same host the cite links do, so the deploy
        # domain never competes with it for the same page.
        self.assertIn("Sitemap: " + seo.SITE_BASE_URL + "/sitemap.xml",
                      self.client.get("/robots.txt").text)
        for loc in self.children + self.locs:
            self.assertTrue(loc.startswith(seo.SITE_BASE_URL + "/"), loc)


if __name__ == "__main__":
    unittest.main()
