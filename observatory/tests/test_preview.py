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
from unittest import mock

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ.setdefault("ASSISTANT_SECRET", "test-secret-not-for-production")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import us_web  # noqa: E402
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
        # no test here pays for a search, whatever key the machine holds
        off = mock.patch.object(us_web, "search", side_effect=us_web.WebError("off in tests"))
        off.start()
        self.addCleanup(off.stop)

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

    def test_the_boj_is_not_answered_with_its_holdings_unless_they_are_asked_for(self):
        for q in ("When did the BoJ last raise rates in Japan", "What is the BOJ policy rate", "Who is the governor of the Bank of Japan",
                  "Is the BOJ still buying ETFs", "When is the next BOJ meeting"):
            self.assertIsNone(preview.catalogue_hit(q), q)
        for q in ("How many JGBs does the BOJ own", "BOJ balance sheet", "Bank of Japan's holdings", "BOJ JGB holdings"):
            self.assertEqual(preview.catalogue_hit(q)[:2], ("boj-assets", "MA03021034S"), q)
        self.assertEqual(preview.catalogue_hit("Japan 10-year JGB yield")[0], "jgb-yields")

    def test_a_question_with_no_data_goes_to_the_web_with_every_source_linked(self):
        self.no_stock()
        got = {"text": "The BOJ last raised its rate on 24 September 2026. It went to 1.25%.", "searches": ["boj rate"], "model": "m",
               "asked_at": "2026-11-03T01:00", "sources": [{"title": "BOJ statement", "site": "boj.or.jp", "url": "https://www.boj.or.jp/a"}]}
        with mock.patch.object(us_web, "search", return_value=got) as search:
            res = TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "When did the BoJ last raise rates in Japan"})
        self.assertTrue(search.call_args[1]["general"])
        p = res.json()
        self.assertEqual(p["kind"], "web")
        self.assertEqual(p["headline"], "The BOJ last raised its rate on 24 September 2026.")
        self.assertEqual(p["lead"], "It went to 1.25%.")
        self.assertEqual(p["reports"][0]["url"], "https://www.boj.or.jp/a")
        self.assertIn("not from data Plover holds", p["sources"][0])
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_a_web_answer_is_not_cut_inside_a_sentence(self):
        self.assertEqual(preview._headline("It bought ¥157.75 at 5 p.m. Tokyo time on Monday. That is weak."),
                         ("It bought ¥157.75 at 5 p.m. Tokyo time on Monday.", "That is weak."))
        self.assertEqual(preview._headline("No. The BOJ held its rate at 1.25% in October. It meets in December."),
                         ("No. The BOJ held its rate at 1.25% in October.", "It meets in December."))
        self.assertEqual(preview._headline("Rates in the U.S. Federal Reserve system are set by the committee."),
                         ("Rates in the U.S. Federal Reserve system are set by the committee.", ""))

    def test_advice_stays_refused_and_a_failed_search_says_so(self):
        from app import ask_miss
        with mock.patch.object(us_web, "search") as search:
            preview._stock = lambda q, prev=None: (None, ask_miss.miss("I don't give advice.", "policy"))
            self.assertEqual(TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "Should I buy gold"}).json()["kind"], "none")
            preview._stock = lambda q, prev=None: (None, "error")
            self.assertEqual(TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "What is the Nikkei at"}).json()["kind"], "none")
        search.assert_not_called()
        self.no_stock()
        with mock.patch.object(us_web, "search", side_effect=us_web.WebError("no reports")):
            self.assertEqual(TestClient(self._app()).get("/api/v1/assistant/preview", params={"q": "What is the Nikkei at"}).json()["kind"], "none")

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
