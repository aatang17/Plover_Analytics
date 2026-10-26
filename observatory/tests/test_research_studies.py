# -*- coding: utf-8 -*-
"""The editor's Studies tab: a writer's own studies (Investment Assistant ›
Studies) put into an article as a chart.

What is proved: the list holds only the signed-in writer's studies (found by
email, as their data sources are), with the newest run that has rows; the
shared admin password and another writer see none of them; a study's chart
comes back as a datachart block the article accepts, its numbers frozen,
published columns named as published and anything calculated carrying its
inputs and query; archived studies and stock answers are not offered; a run
too long for an article says what to do.
"""
import os
import pathlib
import tempfile

from app import accounts, analysis, research_doc as rd
from app.assistant import store
from tests.test_research_mcp import Base

SOURCES = [{"name": "japan", "series": "jgb-yields:10Y", "title": "10-year JGB yield", "unit": "%",
            "release": "Data through 27 August 2026", "credit": "Source: Ministry of Finance, Japan."},
           {"name": "us", "series": "ust-yields:10Y", "title": "10-year Treasury yield", "unit": "%",
            "release": "Data through 22 September 2026", "credit": "Source: U.S. Department of the Treasury."}]
ROWS = [["2026-01-01", 2.1, 4.3], ["2026-02-01", 2.2, None], ["2026-03-01", 0.30000000000000004, 4.1]]


def result(query, columns, rows, chart):
    return {"spec": {"series": {"japan": "jgb-yields:10Y", "us": "ust-yields:10Y"}, "query": query,
                     "chart": chart},
            "columns": columns, "rows": rows, "sources": SOURCES, "ran_at": "2026-10-23"}


PUBLISHED = result("SELECT date, japan, us FROM data", ["date", "japan", "us"], ROWS,
                   {"columns": ["japan", "us"], "title": "10-year yields", "unit": "%"})
GAP = result("SELECT date, spread_pp(us, japan) AS gap FROM data", ["date", "gap"],
             [["2026-01-01", 2.2], ["2026-02-01", None], ["2026-03-01", 3.8]],
             {"columns": ["gap"], "title": "US minus Japan", "unit": "pp"})


class StudiesTab(Base):

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "workspace.db")
        saved = (accounts.DB_PATH, store.DB_PATH, os.environ.get("ACCOUNTS_ENABLED"))
        accounts.DB_PATH = pathlib.Path(path)
        accounts._conn = None
        store.reset_for_tests(path)

        def restore():
            accounts.DB_PATH = saved[0]
            accounts._conn = None
            store.reset_for_tests(saved[1])
        self.addCleanup(restore)
        db = accounts.conn()
        for email in ("w@example.com", "other@example.com"):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, accounts._now()))
        db.commit()
        self.aid = db.execute("SELECT id FROM accounts WHERE email = 'w@example.com'").fetchone()[0]
        self.other_aid = db.execute("SELECT id FROM accounts WHERE email = 'other@example.com'").fetchone()[0]

    def study(self, aid, title, res):
        st = store.add_study(aid, title)
        store.add_study_run(aid, st["id"], "you", res)
        return st["id"]

    def test_list_is_the_writers_own(self):
        mine = self.study(self.aid, "Yield gap", GAP)
        self.study(self.other_aid, "Someone else's", PUBLISHED)
        # a stock answer has no rows; an archived study is hidden: neither is offered
        stock = store.add_study(self.aid, "Apple buybacks")
        store.add_study_run(self.aid, stock["id"], "stocks", {"stock": {"headline": "x"}, "spec": {}, "rows": [], "columns": []})
        gone = self.study(self.aid, "Archived", PUBLISHED)
        store.archive_study(self.aid, gone, True)
        r = self.w.get("/admin/api/research/studies")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["cache-control"], "private, no-store")
        got = r.json()
        self.assertEqual([s["title"] for s in got["studies"]], ["Yield gap"])
        self.assertEqual(got["studies"][0]["id"], mine)
        self.assertEqual(got["studies"][0]["rows"], 3)
        self.assertTrue(got["studies"][0]["ran_on"])
        self.assertEqual(got["link"], "/assistant.html#/studies")
        # the shared admin password is nobody; a writer with no account has an empty list
        self.assertEqual(self.admin.get("/admin/api/research/studies").status_code, 403)
        lone, _ = self.person("lone@example.com", ["writing"])
        self.assertEqual(lone.get("/admin/api/research/studies").json()["studies"], [])
        # someone else's study is a 404, not a chart
        theirs = store.studies(self.other_aid)[0]["id"]
        self.assertEqual(self.w.get("/admin/api/research/studies/%d/chart" % theirs).status_code, 404)
        self.assertEqual(self.w.get("/admin/api/research/studies/%d/chart" % gone).status_code, 404)
        self.assertEqual(self.w.get("/admin/api/research/studies/%d/chart" % stock["id"]).status_code, 404)

    def test_published_columns_chart_as_published(self):
        sid = self.study(self.aid, "Yields", PUBLISHED)
        b = self.w.get("/admin/api/research/studies/%d/chart" % sid).json()["block"]
        self.assertEqual(b["type"], "datachart")
        self.assertEqual(b["origin"], "database")
        self.assertEqual(b["rows"][0], ["Date", "10-year JGB yield", "10-year Treasury yield"])
        self.assertEqual(b["rows"][2], ["2026-02-01", "2.2", ""])            # missing stays blank, never 0
        self.assertEqual(b["rows"][3][1], "0.3")                             # no float noise frozen in
        self.assertEqual(b["unit"], "%")
        self.assertEqual(b["title"], "10-year yields")
        self.assertIn("Ministry of Finance", b["source"])
        self.assertNotIn("calculated", b["source"])
        self.assertIn("Values as published", b["note"])
        self.assertIn("“Yields”", b["note"])
        # the article accepts it as it is
        clean = rd._block(dict(b, id="b1"))
        self.assertEqual(clean["rows"], b["rows"])
        self.assertEqual(rd.datachart_source_line(clean).split(" · ")[-1], "Values from saved Plover data.")

    def test_calculated_column_carries_its_formula(self):
        sid = self.study(self.aid, "Gap", GAP)
        b = self.w.get("/admin/api/research/studies/%d/chart" % sid).json()["block"]
        self.assertEqual(b["rows"][0], ["Date", "gap"])
        self.assertEqual(len(b["rows"]), 3)                                  # the all-blank row is dropped
        self.assertIn("calculated by Plover", b["source"])
        self.assertIn("Calculation: SELECT date, spread_pp(us, japan) AS gap FROM data", b["note"])
        self.assertIn("japan = 10-year JGB yield", b["note"])
        self.assertLessEqual(len(b["note"]), rd.TEXT_MAX)
        rd._block(dict(b, id="b2"))

    def test_a_long_title_gives_way_to_the_formula(self):
        q = "SELECT date, spread_pp(us, japan) AS gap FROM data WHERE date >= '2020-01-01' AND us IS NOT NULL"
        res = dict(GAP, spec=dict(GAP["spec"], query=q))
        b = analysis.datachart_block(res, "A very long study title " * 12, "23 October 2026")
        self.assertLessEqual(len(b["note"]), rd.TEXT_MAX)
        self.assertTrue(b["note"].startswith("From a study run 23 October 2026."))
        self.assertIn("Calculation: " + q, b["note"])
        # a query too long for any note is cut, and says so with an ellipsis
        long_q = "SELECT date, " + ", ".join("japan AS j%d" % i for i in range(60)) + " FROM data"
        b = analysis.datachart_block(dict(GAP, spec=dict(GAP["spec"], query=long_q)), "T", "")
        self.assertEqual(len(b["note"]), rd.TEXT_MAX)
        self.assertTrue(b["note"].endswith("\u2026"))

    def test_too_many_rows_says_what_to_do(self):
        rows = [["2000-01-%02d" % (i % 28 + 1), float(i), float(i)] for i in range(rd.DATA_ROWS_MAX + 5)]
        sid = self.study(self.aid, "Daily", result("SELECT date, japan, us FROM data", ["date", "japan", "us"], rows,
                                                   {"columns": ["japan", "us"]}))
        r = self.w.get("/admin/api/research/studies/%d/chart" % sid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("make it monthly or shorten the period", r.json()["detail"])
        with self.assertRaises(analysis.AnalysisError):
            analysis.datachart_block(dict(PUBLISHED, rows=[["2026-01-01", None, None]]))
