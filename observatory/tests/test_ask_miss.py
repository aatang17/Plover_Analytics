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

    def test_plain_non_questions_are_caught_before_anything_runs(self):
        for q in ("hello", "Test", "testing 123", "thanks!", "?", "asdf qwerty", "   "):
            self.assertTrue(stock_recipes._NOT_A_QUESTION.fullmatch(q.lower().strip(" ?!.,")), q)
        for q in ("GM", "F", "AAPL", "who owns nvidia", "help me find cheap banks", "hello group stock"):
            self.assertFalse(stock_recipes._NOT_A_QUESTION.fullmatch(q.lower().strip(" ?!.,")), q)


def _live():
    from app import sec_api
    try:
        sec_api._require()
        return True
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live(), "data/sec.duckdb with the SEC tables")
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
        r = self.c.get("/api/v1/assistant/preview", params={"q": "Which companies have the most employees?"})
        if r.status_code == 404:
            self.skipTest("the preview is not mounted here")
        p = r.json()
        self.assertEqual((p["kind"], p["miss"]), ("none", "not_held"))
        _, full = self.full("Which companies have the most employees?")
        self.assertEqual(p["message"], full["detail"])
        self.assertEqual(p["try"], full["try"])


if __name__ == "__main__":
    unittest.main()
