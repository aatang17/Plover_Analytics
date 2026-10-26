# -*- coding: utf-8 -*-
"""The front page's answer preview (app/assistant/preview.py).

What is proved: it needs no account; a stock answer is passed through as
its headline, at most two figures and a link to the full answer; a US
economy question is answered only from a dataset for the topic it names, so
a question about something Plover does not hold ("US unemployment rate")
says so instead of showing the nearest series; a price index is shown as a
year-on-year rate with its formula.
"""
import os
import unittest

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ.setdefault("ASSISTANT_SECRET", "test-secret-not-for-production")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant import api as aapi  # noqa: E402
from app.assistant import preview  # noqa: E402
from tests import _data  # noqa: E402

STOCK = {"recipe": "company_holders", "question": "Who owns Nvidia?",
         "headline": "Nvidia's largest reported holder is BlackRock, with 12.0%.",
         "answer": "5969 managers reported positions. The biggest additions were large.",
         "figures": [{"label": "Holders reporting", "value": 5969, "unit": "count", "as_of": "2026-08-31"},
                     {"label": "Reported value", "value": 3.2e12, "unit": "USD", "as_of": "2026-08-31"},
                     {"label": "Third", "value": 1, "unit": "count", "as_of": None}],
         "table": {"columns": ["Manager"], "rows": [["A"], ["B"]]},
         "chart": {"title": "Largest holders"},
         "sources": [{"name": "13F", "credit": "Source: SEC (Form 13F)."}],
         "match": {"read_as": "The largest 13F holders of Nvidia."}}


class Preview(unittest.TestCase):
    def setUp(self):
        self._stock = preview._stock

    def tearDown(self):
        preview._stock = self._stock

    def no_stock(self):
        preview._stock = lambda q, prev=None: (None, None)

    def test_topics_gate_the_economy_route(self):
        self.assertEqual(preview._allowed("US unemployment rate"), {"us-labour"})     # held since the us-labour dataset (2026-10-22)
        self.assertEqual(preview._allowed("Fed funds rate"), set())
        self.assertIn("cpi-us", preview._allowed("Is US inflation falling?"))
        self.assertIn("ust-yields", preview._allowed("US 10-year Treasury yield"))
        self.assertIn("us-wage-tracker", preview._allowed("Are US wages rising?"))

    def test_a_stock_answer_is_cut_to_a_preview(self):
        from app import stock_recipes, sec_api
        orig = (stock_recipes.answer, sec_api._require)
        stock_recipes.answer = lambda cur, q, prev: STOCK
        sec_api._require = lambda: None
        try:
            got = TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "Who owns Nvidia?"})
        finally:
            stock_recipes.answer, sec_api._require = orig
        self.assertEqual(got.status_code, 200)       # no account needed
        p = got.json()
        self.assertEqual(p["kind"], "stock")
        self.assertEqual(p["headline"], STOCK["headline"])
        self.assertEqual(p["lead"], "5969 managers reported positions.")
        self.assertEqual(len(p["figures"]), 2)
        self.assertEqual(p["full_url"], "/?q=Who%20owns%20Nvidia%3F")
        self.assertTrue(any("2 rows" in m for m in p["more"]))
        self.assertNotIn("table", p)

    def test_a_one_letter_ticker_is_read_not_refused(self):
        # "T" is AT&T: a lone letter reaches the reader, never a 422 the page words as our fault
        seen = []
        preview._stock = lambda q, prev=None: (seen.append(q), (None, None))[1]
        got = TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "T"})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(seen, ["T"])
        self.assertEqual(got.json()["kind"], "none")

    def test_not_held_says_so(self):
        self.no_stock()
        p = preview.preview("US unemployment rate")
        self.assertEqual(p["kind"], "none")
        self.assertIn("Treasury yields", p["holds"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_inflation_is_a_year_on_year_rate_with_its_formula(self):
        self.no_stock()
        p = preview.preview("Is US inflation falling?")
        self.assertEqual(p["kind"], "macro")
        self.assertIn("on a year earlier", p["headline"])
        self.assertIn("index a year earlier", p["calc"])
        self.assertEqual(p["full_url"], "us-inflation.html")
        self.assertEqual(p["figures"][0]["unit"], "%")

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_a_yield_is_its_latest_reading(self):
        self.no_stock()
        p = preview.preview("US 10-year Treasury yield")
        self.assertEqual(p["kind"], "macro")
        self.assertTrue(p["headline"].startswith("10-year Treasury"))
        self.assertIsNone(p["calc"])
        self.assertEqual(p["full_url"], "us-treasury.html")

    @staticmethod
    def _app():
        app = FastAPI()
        app.include_router(aapi.router)
        return app


if __name__ == "__main__":
    unittest.main()
