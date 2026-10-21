# -*- coding: utf-8 -*-
"""The research desk's data search, asked what its readers will ask.

The desk sits behind a sign-in, so ci/search_examples.py (which types into
every public search box in a real browser) cannot reach it. This runs the
same search the box calls, with the examples read out of the box's own
placeholder, so changing the hint changes the test.

2026-10-03: the box said "e.g. core CPI, 7203" and found nothing for
"core CPI", nor for "CPI".
"""
import pathlib
import re
import threading
import time
import unittest

from app import research_api
from tests import _data

ROOT = pathlib.Path(__file__).resolve().parent.parent

# What a writer types first, beyond the box's own examples.
OBVIOUS = ["CPI", "GDP", "10-year", "Toyota", "7203", "CEO compensation", "buybacks"]


def placeholder_examples(path="desk-research.js", pattern=r'class="dk-rs-q" placeholder="([^"]*)"'):
    text = (ROOT / "web" / "assets" / path).read_text(encoding="utf-8")
    m = re.search(pattern, text)
    assert m, "the desk's data search box has moved: update this test"
    after = re.split(r"\be\.g\.?\s*", m.group(1), flags=re.I)[-1]
    return [p.strip() for p in after.split(",") if p.strip()]


class DeskSearchExamples(unittest.TestCase):
    def setUp(self):
        self._writer = research_api._writer
        research_api._writer = lambda request: {"id": 0}

    def tearDown(self):
        research_api._writer = self._writer

    def test_the_box_shows_examples(self):
        self.assertTrue(placeholder_examples())

    @unittest.skipUnless(_data.MACRO and _data.EQUITY, _data.NO_MACRO)
    def test_every_example_and_obvious_query_finds_something(self):
        for q in placeholder_examples() + OBVIOUS:
            with self.subTest(q=q):
                r = research_api.data_search(None, q)
                self.assertTrue(r["series"] or r["companies"] or r["pages"],
                                "the desk search finds nothing for %r" % q)

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_the_chart_search_examples_find_series(self):
        # An empty chart in the desk searches every dataset with the same
        # search; a chart needs a series, so a company or page hit is not enough.
        examples = placeholder_examples("desk.js", r'data-f="q" placeholder="([^"]*)"')
        self.assertTrue(examples)
        for q in examples + ["CPI", "guest nights"]:
            with self.subTest(q=q):
                self.assertTrue(research_api.data_search(None, q)["series"],
                                "an empty chart finds no series for %r" % q)

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_a_town_is_found_by_its_english_and_japanese_name(self):
        # Municipal names are stored with the town in Japanese and shown in
        # English (app/place_en.py); the search must match what is shown.
        import json
        from app import tools_v2
        for q, want in (("Koriyama", "Koriyama-shi"), ("郡山市", "Koriyama-shi")):
            with self.subTest(q=q):
                r = json.loads(tools_v2.search(q, dataset="population-jp-municipal", limit=3))
                self.assertTrue(r["series"], q)
                self.assertIn(want, r["series"][0]["name_en"])

    def test_a_topic_finds_its_pages(self):
        # 2026-10-03: "CEO Compensation" found nothing; the pay pages say
        # "CEO pay" and "officer remuneration".
        titles = [p["title"] for p in research_api.page_hits("CEO Compensation")]
        self.assertIn("US Executive Pay", titles)
        self.assertIn("Boards & Pay", titles)

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_a_market_word_picks_the_market(self):
        # 2026-10-09: "us rates" listed BoJ loan rates at Trust banks ("us" in
        # "Trust") and "US CPI" listed Japanese housing ("Housing").
        r = research_api.data_search(None, "us rates")
        self.assertEqual((r["series"][0]["dataset"], r["series"][0]["code"]), ("ust-yields", "10Y"))
        self.assertTrue(all(s["dataset"].startswith("ust-") for s in r["series"]), r["series"])
        r = research_api.data_search(None, "US CPI")
        self.assertEqual((r["series"][0]["dataset"], r["series"][0]["name"]), ("cpi-us", "All items"))
        self.assertTrue(all(s["dataset"].startswith("cpi-us") for s in r["series"]))
        r = research_api.data_search(None, "japan rates")
        self.assertEqual(r["series"][0]["dataset"], "jgb-yields")
        r = research_api.data_search(None, "us 10y")
        self.assertEqual([(s["dataset"], s["code"]) for s in r["series"]][:1], [("ust-yields", "10Y")])
        # a market can also be a trade partner in a series' name
        r = research_api.data_search(None, "semiconductor exports to US")
        self.assertIn("United States", r["series"][0]["name"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_words_match_whole_words_and_not_what_a_series_leaves_out(self):
        names = [s["name"] for s in research_api.data_search(None, "food CPI")["series"]]
        self.assertEqual(names[0], "Food")
        self.assertNotIn("All items, less fresh food", names)
        r = research_api.data_search(None, "guest nights")
        self.assertEqual(r["series"][0]["name"], "All Japan — guest nights")

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_cpi_lists_the_headline_first(self):
        r = research_api.data_search(None, "CPI")
        self.assertEqual((r["series"][0]["dataset"], r["series"][0]["name"]), ("cpi-jp", "All items"))


class SearchBlocks(unittest.TestCase):
    """2026-10-09: after a deploy the backfill swaps the database file again
    and again; every swap dropped the search blocks, the next searches rebuilt
    them while the reader waited, and searches timed out at 15s."""

    def setUp(self):
        from app import tools_v2
        self.t = tools_v2
        self.saved = (tools_v2.db.file_version, tools_v2._build_searchable, dict(tools_v2._SEARCHABLE))
        tools_v2._SEARCHABLE.update({"maps": {}, "building": {}, "refresher": None})
        self.version = [1]
        self.builds = []
        self.release = threading.Event()
        self.release.set()

        def build(dataset):
            self.builds.append(dataset)
            self.release.wait(5)
            return {"built": len(self.builds)}
        tools_v2.db.file_version = lambda: self.version[0]
        tools_v2._build_searchable = build

    def tearDown(self):
        self.release.set()
        self.t.db.file_version, self.t._build_searchable, saved = self.saved
        self.t._SEARCHABLE.clear()
        self.t._SEARCHABLE.update(saved)

    def test_a_changed_file_never_makes_a_search_wait(self):
        first = self.t._searchable("x")
        self.version[0] = 2
        self.release.clear()                      # the rebuild is slow
        started = time.time()
        self.assertIs(self.t._searchable("x"), first)
        self.assertLess(time.time() - started, 1)
        self.release.set()
        for _ in range(50):
            if self.t._SEARCHABLE["refresher"] is None:
                break
            time.sleep(0.05)
        self.assertEqual(self.t._searchable("x"), {"built": 2})
        self.assertEqual(self.builds, ["x", "x"])

    def test_searches_at_the_same_time_build_a_block_once(self):
        self.release.clear()
        got = []
        threads = [threading.Thread(target=lambda: got.append(self.t._searchable("y")))
                   for _ in range(5)]
        for th in threads:
            th.start()
        time.sleep(0.2)
        self.release.set()
        for th in threads:
            th.join(5)
        self.assertEqual(self.builds, ["y"])
        self.assertEqual(len(got), 5)


if __name__ == "__main__":
    unittest.main()


class CoverageSearchExamples(unittest.TestCase):
    """The Writer Desk's Coverage tab finds companies with the desk's company
    search (app/assistant/api.find_companies); its box says what to type."""

    def test_the_box_shows_examples(self):
        self.assertTrue(placeholder_examples("write.js", r'id="wd-cvq"[^>]*placeholder="([^"]*)"'))

    @unittest.skipUnless(_data.EQUITY, _data.NO_EQUITY)
    def test_every_example_and_obvious_company_finds_it(self):
        from app.assistant.api import find_companies
        examples = placeholder_examples("write.js", r'id="wd-cvq"[^>]*placeholder="([^"]*)"')
        for q in examples + ["Toyota", "7203", "Sony", "MUFG"]:
            with self.subTest(q=q):
                self.assertTrue(find_companies(q), "the coverage search finds nothing for %r" % q)

    def _my_examples(self):
        """Your Plover's Add a company box (my.html) uses the same search."""
        text = (ROOT / "web" / "my.html").read_text(encoding="utf-8")
        m = re.search(r'id="my-co-q"[^>]*placeholder="([^"]*)"', text, re.S)
        self.assertTrue(m, "my.html's Add a company box has moved: update this test")
        after = re.split(r"\be\.g\.?\s*", m.group(1), flags=re.I)[-1]
        return [p.strip() for p in after.split(",") if p.strip()]

    def test_your_plover_box_shows_examples(self):
        self.assertTrue(self._my_examples())

    @unittest.skipUnless(_data.EQUITY and _data.SEC, "equity and SEC databases not both present")
    def test_your_plover_examples_find_companies(self):
        # the box searches Japan and the US together (/companies?markets=all)
        from app.assistant.api import find_companies, find_us_companies
        for q in self._my_examples() + ["Toyota", "7203", "Sony", "MUFG", "Microsoft", "TSLA"]:
            with self.subTest(q=q):
                self.assertTrue(find_companies(q) or find_us_companies(q),
                                "Your Plover's company box finds nothing for %r" % q)

    @unittest.skipUnless(_data.SEC, "SEC database not present")
    def test_us_names_find_the_company_people_mean(self):
        from app.assistant.api import find_us_companies
        for q, ticker in (("Apple", "AAPL"), ("apple", "AAPL"), ("NVDA", "NVDA"), ("Nvidia", "NVDA"),
                          ("Tesla", "TSLA"), ("Amazon", "AMZN")):
            with self.subTest(q=q):
                hits = find_us_companies(q)
                self.assertTrue(hits and hits[0]["ticker"] == ticker,
                                "%r should find %s first, found %r" % (q, ticker, [h["ticker"] for h in hits[:3]]))
