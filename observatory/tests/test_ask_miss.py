# -*- coding: utf-8 -*-
"""When the box cannot answer (app/ask_miss.py): one plain sentence, its
kind, and questions that do answer; never a status code, a blank, the box's
own words for its tables, or an attempted answer to gibberish."""
import json
import os
import unittest
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ask_miss, stock_recipes
from tests import _data


def setUpModule():
    os.environ["US_ASK_MODEL"] = "0"


def tearDownModule():
    os.environ.pop("US_ASK_MODEL", None)


class WordsTest(unittest.TestCase):
    def test_the_box_s_internal_words_are_never_shown(self):
        self.assertEqual(ask_miss.plain("The tables do not contain Toyota's vehicle sales in India."),
                         "Plover's data doesn't include Toyota's vehicle sales in India.")
        self.assertEqual(ask_miss.plain("The requested tables contain 5,650 stocks."), "Plover's data includes 5,650 stocks.")
        self.assertEqual(ask_miss.plain("Employee counts sit in the text of the 10-K, not in the data sets I hold."),
                         "Employee counts sit in the text of the 10-K, not in the data Plover holds.")
        self.assertNotIn("table", ask_miss.plain("these tables hold no forecasts").lower())

    def test_kinds(self):
        why = stock_recipes._UNANSWERABLE[0][1]
        self.assertEqual(ask_miss.classify(why), "policy")
        self.assertEqual(ask_miss.classify(stock_recipes.NO_MATCH_TEXT), "unclear")
        self.assertEqual(ask_miss.classify(stock_recipes._REFUSALS["fragment"]), "unclear")
        self.assertEqual(ask_miss.classify("This box covers US-listed companies only. Japanese companies are under the Japan tab."),
                         "other_market")
        self.assertEqual(ask_miss.classify("I don't hold employee numbers."), "not_held")

    def test_our_failures_say_so_and_offer_no_questions(self):
        self.assertEqual(ask_miss.reply("x", None), {"detail": ask_miss.ERROR, "miss": "error", "try": []})
        self.assertEqual(ask_miss.reply("x", HTTPException(500, "Traceback ..."))["detail"], ask_miss.ERROR)
        r = ask_miss.reply("x", HTTPException(503, "US financials not published on this server yet"))
        self.assertEqual((r["miss"], r["detail"]), ("error", ask_miss.NOT_LOADED))

    def test_suggestions_name_the_company_and_never_repeat_the_question(self):
        with mock.patch.object(ask_miss, "company_in", return_value="Intel"):
            self.assertEqual(ask_miss.suggestions("Why did Intel go up?", "policy", ""),
                             ["How has Intel done this year?", "What did Intel report last quarter?", "Are insiders buying Intel?"])
            self.assertEqual(ask_miss.suggestions("Should I buy Intel?", "policy", "")[0], "What is Intel's P/E?")
            self.assertEqual(ask_miss.suggestions("Intel's backlog", "not_held", ""),
                             ["What does Intel do?", "How has Intel done this year?", "Who owns Intel?"])
        with mock.patch.object(ask_miss, "company_in", return_value=None):
            self.assertEqual(ask_miss.suggestions("most employees", "not_held", "")[0], "Largest US companies by revenue")
            self.assertEqual(ask_miss.suggestions("asdf", "unclear", ""), [])
            given = ask_miss.suggestions("x", "not_held", "", given=["How many S&P 500 stocks are up this year?"])
            self.assertEqual(given[0], "How many S&P 500 stocks are up this year?")

    def test_suggestions_follow_the_subject_of_the_question(self):
        with mock.patch.object(ask_miss, "company_in", return_value=None):
            market = ask_miss.suggestions("How has the market performed on midterm election periods", "not_held", "")
            self.assertEqual(market, ["What drove the S&P 500 this year?", "Best performers in the S&P 500 this year",
                                      "Worst S&P 500 stocks this year"])
            self.assertEqual(ask_miss.suggestions("is inflation coming down", "not_held", "")[0], "Is US core inflation falling?")
            self.assertEqual(ask_miss.suggestions("when will the Fed cut rates", "not_held", "")[0], "What is the 10-year Treasury yield?")
            self.assertEqual(ask_miss.suggestions("how many jobs were lost", "not_held", "")[0], "US unemployment rate")
            # two subjects: each gets a turn, the first subject first
            two = ask_miss.suggestions("S&P 500 dividend cuts", "not_held", "")
            self.assertEqual(two[:2], ["What drove the S&P 500 this year?", "Highest dividend yields in the S&P 500"])
            # no subject the box knows: the front page's examples, as before
            self.assertEqual(ask_miss.suggestions("weather in Ohio", "not_held", ""), ask_miss.EXAMPLES)
            # never the question itself
            self.assertNotIn("US unemployment rate", ask_miss.suggestions("US unemployment rate", "not_held", ""))
        # a company question keeps its own lines first
        with mock.patch.object(ask_miss, "company_in", return_value="Intel"):
            self.assertEqual(ask_miss.suggestions("Intel's share of the market", "not_held", "")[0], "What does Intel do?")
        # every topical question is one the box answers without the model (checked against the data in
        # tests/test_us_ask.py); here: no duplicates inside a subject, nothing empty
        for pat, qs in ask_miss.TOPICS:
            self.assertEqual(len(qs), len(set(qs)))
            self.assertTrue(all(q.strip() for q in qs))

    def test_plain_non_questions_are_caught_before_anything_runs(self):
        for q in ("hello", "Test", "testing 123", "thanks!", "?", "asdf qwerty", "   "):
            self.assertTrue(stock_recipes._NOT_A_QUESTION.fullmatch(q.lower().strip(" ?!.,")), q)
        for q in ("GM", "F", "AAPL", "who owns nvidia", "help me find cheap banks", "hello group stock"):
            self.assertFalse(stock_recipes._NOT_A_QUESTION.fullmatch(q.lower().strip(" ?!.,")), q)


@unittest.skipUnless(_data.SEC, "data/sec.duckdb not built")
class DoorsTest(unittest.TestCase):
    """Each door of the box answers a miss with the same reader-facing reply."""

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def full(self, q):
        r = self.c.get("/api/v1/us/ask", params={"q": q})
        return r.status_code, r.json()

    def test_full_answer(self):
        code, d = self.full("Why did Intel go up this year?")
        self.assertEqual((code, d["miss"]), (422, "policy"))
        self.assertIn("How has Intel done this year?", d["try"])
        code, d = self.full("Which companies have the most employees?")
        self.assertEqual(d["miss"], "not_held")
        self.assertEqual(d["try"][0], "Largest US companies by revenue")
        self.assertNotIn("data sets I hold", d["detail"])
        code, d = self.full("asdf qwerty")
        self.assertEqual((code, d["miss"], d["detail"]), (422, "unclear", ask_miss.UNCLEAR))
        code, d = self.full("best japanese banks")
        self.assertEqual(d["miss"], "other_market")

    def test_a_reader_that_calls_a_company_question_unclear_is_overruled(self):
        from app import us_ask_model
        with mock.patch.object(us_ask_model, "read", return_value={"shape": None, "refuse": "unclear"}):
            code, d = self.full("Apple revenue history")
            self.assertEqual(code, 200, d)
            self.assertIn("Apple", d["headline"])
            code, d = self.full("zzkq vbnx")                      # names nothing: the refusal stands
            self.assertEqual((code, d["miss"]), (422, "unclear"))

    def test_no_fit_from_the_reader_lets_a_matching_pattern_answer(self):
        """'Nvidia share price': the reader found no shape; the price pattern does."""
        from app import us_ask_model
        with mock.patch.object(us_ask_model, "read", return_value={"shape": None, "refuse": "no_fit", "read_as": "Nvidia's share price"}):
            code, d = self.full("Nvidia share price")
        self.assertEqual(code, 200, d)
        self.assertEqual(d["recipe"], "company_price_performance")
        self.assertIn("Nvidia", d["headline"])
        self.assertIn(d["match"]["via"], ("patterns", "quick"))          # the fixed routes, never the step route

    def test_steps_calling_a_company_question_unclear_falls_to_patterns(self):
        from app import us_ask_model, us_steps
        us_steps._PLANS.clear()
        with mock.patch.object(us_ask_model, "read", return_value={"shape": "steps"}), \
                mock.patch.object(us_steps, "_ask_model", return_value=({"cannot": "unclear", "sql": ""}, "{}")), \
                mock.patch.object(us_steps, "enabled", return_value=True):
            code, d = self.full("TSLA price")
        self.assertEqual(code, 200, d)
        self.assertEqual(d["recipe"], "company_price_performance")

    def test_a_failure_of_ours_is_worded(self):
        with mock.patch.object(stock_recipes, "answer", side_effect=RuntimeError("boom")):
            code, d = self.full("who owns apple, a question that fails")
        self.assertEqual((code, d["miss"], d["detail"]), (500, "error", ask_miss.ERROR))
        self.assertNotIn("boom", json.dumps(d))

    def test_stream(self):
        r = self.c.get("/api/v1/us/ask/stream", params={"q": "Should I buy Nvidia?"})
        events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]
        err = events[-1]["error"]
        self.assertEqual((err["status"], err["miss"]), (422, "policy"))
        self.assertTrue(err["try"] and err["detail"])

    def test_preview_gives_the_same_reason(self):
        from app.assistant import preview
        # called directly: the route is mounted only where accounts and the assistant are switched on
        p = preview.preview(q="Which companies have the most employees?", prev="")
        self.assertEqual((p["kind"], p["miss"]), ("none", "not_held"))
        _, full = self.full("Which companies have the most employees?")
        self.assertEqual(p["message"], full["detail"])
        self.assertEqual(p["try"], full["try"])


if __name__ == "__main__":
    unittest.main()
