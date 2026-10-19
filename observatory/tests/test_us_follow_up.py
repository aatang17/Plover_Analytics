# -*- coding: utf-8 -*-
"""Follow-up questions in the US ask box: "and Microsoft?" after "what is
Apple worth" is read as the whole question, and a follow-up with nothing
before it is refused rather than guessed (2026-10-19: "and Microsoft?" was
answered against an invented S&P 500 comparison).

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_follow_up
"""
import datetime
import unittest
from unittest import mock

from app import us_ask_model
from app.stock_recipes import _REFUSALS


class ReaderInput(unittest.TestCase):
    def test_previous_question_is_given_above_the_new_one(self):
        self.assertEqual(us_ask_model.user_message("and Microsoft?", "what is Apple worth"),
                         "Previous question: what is Apple worth\nQuestion: and Microsoft?")
        self.assertEqual(us_ask_model.user_message("what is Apple worth"), "what is Apple worth")

    def test_prompt_asks_for_the_whole_question_and_can_refuse_a_fragment(self):
        p = us_ask_model.prompt("- a: b", ["x"], ["sp500"], ["sp500"], datetime.date(2026, 10, 19))
        self.assertIn('"question":', p)
        self.assertIn('"fragment"', p)
        self.assertIn("question", us_ask_model.FORM_KEYS)
        self.assertIn("fragment", _REFUSALS)
        self.assertIn("whole question", _REFUSALS["fragment"])

    def test_cache_keeps_a_follow_up_apart_from_the_bare_words(self):
        seen = []

        def fake(prov, key, model, messages, max_tokens=300, timeout=None, **kw):
            seen.append(messages[-1]["content"])
            return {"text": '{"shape": "company_value", "question": "what is Microsoft worth"}', "usage": {}}
        us_ask_model._CACHE.clear()
        with mock.patch("app.us_ask_model.provider", return_value=("deepseek", "k", "m")), \
                mock.patch("app.us_ask_model.under_cap", return_value=True), \
                mock.patch("app.us_ask_model._tick"), \
                mock.patch("app.us_ask_model._complete", side_effect=fake):
            a = us_ask_model.read("and Microsoft?", "sys", "what is Apple worth")
            b = us_ask_model.read("and Microsoft?", "sys", "who owns Apple")
            c = us_ask_model.read("and Microsoft?", "sys", "what is Apple worth")
        us_ask_model._CACHE.clear()
        self.assertEqual(a["question"], "what is Microsoft worth")
        self.assertEqual(len(seen), 2, "two different previous questions, one repeat from the cache")
        self.assertEqual(a, c)
        self.assertIn("Previous question: who owns Apple", seen[1])
        self.assertIsNotNone(b)


if __name__ == "__main__":
    unittest.main()
