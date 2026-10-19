# -*- coding: utf-8 -*-
"""The editor's chart library (app/chart_library.py).

What is proved: every common chart names a real dataset, a measure the editor
draws and a topic, and the ones whose data is here resolve to real series; a
search answers with the chart people mean ("CPI", "10-year", "bank lending")
and nothing for a vague one; saved charts are each writer's own, only charts
can be saved, a page chart only from a Plover page, and the shared password
keeps nothing; inserts are counted and the most used come first.
"""
import unittest

from fastapi.testclient import TestClient

from app import chart_library as L
from app import registry, research_api, research_doc
from tests import _data
from tests.test_research_mcp import Base


class CommonChartsTest(unittest.TestCase):

    def setUp(self):
        self._avail = L._available
        L._available = lambda entry: True       # what is matched, not what is installed
        self.addCleanup(lambda: setattr(L, "_available", self._avail))

    def test_every_entry_is_well_formed(self):
        ids = set(registry.ids())
        topics = set(k for k, _ in L.TOPICS)
        self.assertEqual(len(L.BY_ID), len(L.COMMON))            # ids are unique
        for c in L.COMMON:
            with self.subTest(c=c["id"]):
                self.assertIn(c["dataset"], ids)
                self.assertIn(c["topic"], topics)
                self.assertIn(c["measure"], research_doc.MEASURES)
                self.assertTrue(1 <= len(c["series"]) <= 6)
                self.assertTrue(c["words"])
                self.assertEqual(L.block(c)["type"], "chart")

    def test_search_means_the_common_chart(self):
        expect = {"CPI": "jp-cpi", "inflation": "jp-cpi", "10-year": "jgb", "japan yields": "jgb",
                  "bank lending": "loan-rates", "unemployment": "jp-unemployment", "wages": "jp-wages",
                  "foreign investors": "foreign-flows", "us cpi": "us-cpi", "GDP": "jp-gdp"}
        for q, want in expect.items():
            with self.subTest(q=q):
                self.assertEqual([c["id"] for c in L.match(q)][:1], [want])
        for q in ("co", "the", "Toyota", "7203", ""):
            with self.subTest(q=q):
                self.assertEqual(L.match(q), [])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_installed_entries_resolve_to_real_series(self):
        L._available = self._avail
        for c in L.COMMON:
            if registry.available(c["dataset"]):
                with self.subTest(c=c["id"]):
                    self.assertTrue(L._available(c), "%s names a series %s does not have"
                                    % (c["id"], c["dataset"]))


class SavedChartsTest(Base):

    def setUp(self):
        Base.setUp(self)
        self.w2, self.wid2 = self.person("w2@example.com", ["writing"])

    def save(self, client, block):
        return client.post("/admin/api/research/library/saved", json={"block": block})

    def test_each_writer_keeps_their_own(self):
        chart = {"type": "chart", "dataset": "cpi-jp", "series": ["0001"], "measure": "yoy",
                 "title": "CPI", "cfg": {"data": "never kept"}}
        r = self.save(self.w, chart)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("cfg", r.json()["block"])
        self.assertEqual(self.save(self.w, chart).json()["id"], r.json()["id"])   # once, not twice
        mine = self.w.get("/admin/api/research/library").json()["saved"]
        self.assertEqual([x["title"] for x in mine], ["CPI"])
        self.assertEqual(self.w2.get("/admin/api/research/library").json()["saved"], [])
        self.assertEqual(self.w.delete("/admin/api/research/library/saved/%d" % r.json()["id"]).status_code, 200)
        self.assertEqual(self.w.get("/admin/api/research/library").json()["saved"], [])

    def test_only_charts_and_only_plover_pages(self):
        self.assertEqual(self.save(self.w, {"type": "image", "media": "x"}).status_code, 400)
        self.assertEqual(self.save(self.w, {"type": "chart", "dataset": "cpi-jp", "series": []}).status_code, 400)
        bad = {"type": "snapshot", "url": "https://example.com/x.html", "chart": 1}
        self.assertEqual(self.save(self.w, bad).status_code, 400)
        ok = {"type": "snapshot", "url": "/financials.html?c=7203&chart=cf", "chart": 1, "title": "Toyota: Cash Flows"}
        self.assertEqual(self.save(self.w, ok).status_code, 200)
        self.assertEqual(self.save(self.admin, ok).status_code, 403)              # the shared password is nobody

    def test_inserts_are_counted(self):
        for _ in range(3):
            self.w.post("/admin/api/research/library/used", json={"id": "jgb"})
        self.w.post("/admin/api/research/library/used", json={"id": "jp-cpi"})
        self.w.post("/admin/api/research/library/used", json={"id": "no-such"})
        self.assertEqual(L._json_setting("chart_uses", {}), {"jgb": 3, "jp-cpi": 1})
        avail = L._available
        L._available = lambda entry: True
        try:
            self.assertEqual([c["id"] for c in L.popular()], ["jgb", "jp-cpi"])
        finally:
            L._available = avail


@unittest.skipUnless(_data.MACRO and _data.EQUITY, _data.NO_MACRO)
class SmarterSearchTest(unittest.TestCase):
    """The editor's data search, asked what writers type (real data)."""

    def setUp(self):
        self._writer = research_api._writer
        research_api._writer = lambda request: {"id": 0}

    def tearDown(self):
        research_api._writer = self._writer

    def test_a_company_name_gives_the_company_and_its_charts(self):
        for q, name in (("Toyota", "TOYOTA"), ("7203", "TOYOTA"), ("Sony", "SONY"), ("MUFG", "Mitsubishi UFJ")):
            with self.subTest(q=q):
                r = research_api.data_search(None, q)
                self.assertTrue(r["company_first"])
                self.assertTrue(r["companies"][0]["name"].upper().startswith(name.upper()))
                self.assertTrue(r["companies"][0]["charts"])
        r = research_api.data_search(None, "Toyota")
        self.assertFalse([s for s in r["series"] if s["dataset"] in research_api.PLACE_DATASETS])

    def test_a_topic_gives_the_chart_people_mean(self):
        for q, want in (("CPI", "jp-cpi"), ("10-year", "jgb"), ("bank lending", "loan-rates")):
            with self.subTest(q=q):
                self.assertEqual(research_api.data_search(None, q)["common"][0]["id"], want)


if __name__ == "__main__":
    unittest.main()
