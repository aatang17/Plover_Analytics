# -*- coding: utf-8 -*-
"""The last check on the question box's answers (app/ask_guard.py).

Written after a reader asked for Toyota's cross-shareholdings in Japanese and got
its revenue, in dollars. No model and no real data: the model's calls are stubbed.
"""
import unittest
from unittest import mock

from fastapi import HTTPException

from app import ask_guard, ask_language, jp_holdings_ask, stock_recipes, us_ask_model

QUESTION = "トヨタの政策保有株式が過去5年間でどう変化したか"
HISTORY = [{"year": str(y), "period_end": "%d-03-31" % y, "named_holdings": n, "book_value_yen": v}
           for y, n, v in [(2020, 56, 1776654000000), (2021, 56, 2471979000000), (2022, 54, 3031590000000), (2023, 54, 3094906000000),
                           (2024, 49, 3508690000000), (2025, 43, 2951385000000), (2026, 37, 3237499000000)]]


def revenue_answer(via="reader"):
    return {"recipe": "company_as_filed_history", "question": "q", "headline": "Toyota reported revenue of $50.68tn.", "answer": "",
            "table": {"columns": ["Revenue"], "rows": [[1]], "unit": "JPY"}, "chart": {"unit": "USD"}, "match": {"via": via}}


class Equity:
    def __init__(self, found=True):
        self.found = found

    def companies(self, q=""):
        return {"companies": [{"sec_code": "7203", "name": "トヨタ自動車（株）", "name_en": "TOYOTA MOTOR CORPORATION"}] if self.found else []}

    def company(self, code):
        return {"entity": {"name_ja": "トヨタ自動車株式会社", "name_en": "TOYOTA MOTOR CORPORATION"}, "history": HISTORY}


class CrossShareholdings(unittest.TestCase):
    def ask(self, q, equity=None):
        import app
        equity = equity or Equity()
        with mock.patch.object(app, "equity_api", equity, create=True), mock.patch.dict("sys.modules", {"app.equity_api": equity}):
            return jp_holdings_ask.answer(q)

    def test_the_question_that_went_wrong_gets_the_filed_figures(self):
        out = self.ask(QUESTION)
        self.assertEqual(out["recipe"], "jp_cross_shareholdings")
        self.assertIn("2兆4,720億円", out["headline"])          # 2021, five years before 2026
        self.assertIn("3兆2,375億円", out["headline"])
        self.assertEqual(len(out["table"]["rows"]), 6)
        self.assertEqual(out["table"]["unit"], "JPY")
        self.assertNotIn("$", out["headline"])
        self.assertNotIn("revenue", str(out).lower())

    def test_english_wording_reads_the_same_data(self):
        for q in ("Toyota cross-shareholdings over the last 5 years", "how have Toyota's policy shareholdings changed"):
            self.assertIn("¥3.24tn", self.ask(q)["headline"])

    def test_a_company_without_filings_is_refused_never_replaced(self):
        with self.assertRaises(HTTPException) as got:
            self.ask("Nobody Corp cross-shareholdings", Equity(found=False))
        self.assertEqual(got.exception.status_code, 422)

    def test_other_questions_are_left_alone(self):
        self.assertIsNone(self.ask("Toyota revenue"))


class OffTopic(unittest.TestCase):
    def test_a_different_figure_never_goes_out(self):
        with mock.patch.object(ask_guard, "on_topic", return_value=(False, "carbon emissions")), \
                mock.patch("app.us_steps.answer", side_effect=HTTPException(422, "no")):
            with self.assertRaises(HTTPException) as got:
                ask_guard.checked(None, "Apple carbon emissions", revenue_answer())
        self.assertIn("carbon emissions", got.exception.detail)
        self.assertIn("haven't shown a different figure", got.exception.detail)

    def test_steps_gets_one_try_and_is_checked_too(self):
        steps = dict(revenue_answer("steps"), headline="Worked out.")
        with mock.patch.object(ask_guard, "on_topic", side_effect=[(False, "x"), (True, None)]), \
                mock.patch("app.us_steps.answer", return_value=steps):
            self.assertEqual(ask_guard.checked(None, "q", revenue_answer())["headline"], "Worked out.")
        with mock.patch.object(ask_guard, "on_topic", side_effect=[(False, "x"), (False, "x")]), \
                mock.patch("app.us_steps.answer", return_value=steps):
            with self.assertRaises(HTTPException):
                ask_guard.checked(None, "q", revenue_answer())

    def test_a_good_answer_passes(self):
        out = revenue_answer()
        with mock.patch.object(ask_guard, "on_topic", return_value=(True, None)):
            self.assertIs(ask_guard.checked(None, "q", out), out)

    def test_an_answer_with_no_route_is_not_sent_to_the_model(self):
        out = revenue_answer("web")
        with mock.patch.object(ask_guard, "on_topic", side_effect=AssertionError):
            self.assertIs(ask_guard.checked(None, "q", out), out)

    def test_a_check_that_cannot_run_lets_the_answer_through(self):
        with mock.patch.object(us_ask_model, "provider", return_value=("openai", "k", "m")), \
                mock.patch.object(us_ask_model, "under_cap", return_value=True), \
                mock.patch.object(us_ask_model, "_complete", side_effect=TimeoutError), \
                mock.patch.object(ask_guard, "_log"):
            self.assertEqual(ask_guard.on_topic("q", revenue_answer()), (True, None))


class Language(unittest.TestCase):
    def run_it(self, q, translation, inner, back=None):
        with mock.patch.object(ask_guard.jp_holdings_ask, "answer", return_value=None), \
                mock.patch.object(us_ask_model, "translate", return_value=translation), \
                mock.patch.object(us_ask_model, "translate_strings", side_effect=back or (lambda lang, ss: ["訳:" + s for s in ss])), \
                mock.patch.object(ask_guard, "on_topic", return_value=(True, None)), \
                mock.patch("app.us_steps.log"):
            return ask_guard.answer(None, q, None, None, inner)

    def test_the_answer_comes_back_in_the_language_asked(self):
        seen = []

        def inner(cur, q, prev, opts):
            seen.append(q)
            return dict(revenue_answer("quick"), headline="Apple revenue was 416.")
        out = self.run_it("アップルの売上高", {"language": "Japanese", "english": "What is Apple's revenue?"}, inner)
        self.assertEqual(seen, ["What is Apple's revenue?"])
        self.assertEqual(out["question"], "アップルの売上高")
        self.assertEqual(out["headline"], "訳:Apple revenue was 416.")
        self.assertEqual(out["table"]["columns"], ["訳:Revenue"])
        self.assertEqual(out["table"]["rows"], [[1]])              # the figures themselves are never sent

    def test_a_translation_that_changes_a_figure_stays_in_english(self):
        out = self.run_it("アップルの売上高", {"language": "Japanese", "english": "Apple revenue"},
                          lambda *a: dict(revenue_answer("quick"), headline="Revenue was $416bn."),
                          back=lambda lang, ss: ["売上は4,160億ドル" if "416" in s else s for s in ss])
        self.assertEqual(out["headline"], "Revenue was ¥416bn.")   # the fixture's table is in yen

    def test_a_failed_translation_back_stays_in_english(self):
        out = self.run_it("アップルの売上高", {"language": "Japanese", "english": "Apple revenue"},
                          lambda *a: dict(revenue_answer("quick"), headline="Revenue was $416bn."), back=lambda lang, ss: None)
        self.assertEqual(out["headline"], "Revenue was ¥416bn.")   # the fixture's table is in yen

    def test_no_reading_is_a_plain_refusal_not_a_guess(self):
        with self.assertRaises(HTTPException) as got:
            self.run_it("アップルの売上高", None, lambda *a: self.fail("must not route"))
        self.assertIn("English", got.exception.detail)

    def test_english_costs_no_model_call(self):
        with mock.patch.object(us_ask_model, "translate", side_effect=AssertionError):
            out = ask_guard.answer(None, "Apple revenue", None, None, lambda *a: revenue_answer("quick"))
        self.assertEqual(out["recipe"], "company_as_filed_history")

    def test_a_foreign_name_in_english_stays_english(self):
        out = self.run_it("Nestlé revenue", {"language": "English", "english": "Nestlé revenue"}, lambda *a: revenue_answer("quick"))
        self.assertIn("Toyota reported", out["headline"])


class Figures(unittest.TestCase):
    def test_plain_english_costs_no_model_call(self):
        for q in ("How many companies outperformed the S&P 500?", "Who is the CEO of Intel?", "which stocks fell most in 2024", "Is it rising?"):
            self.assertFalse(ask_language.might_be_foreign(q), q)
        for q in ("アップルの売上高", "Quels sont les revenus d'Apple ?", "¿Cuántos ingresos tiene Apple?", "Nestlé revenue"):
            self.assertTrue(ask_language.might_be_foreign(q), q)

    def test_the_country_is_not_a_fund(self):
        self.assertEqual(ask_language.country_as_us("What is inflation in the United States?"), "What is inflation in the US?")
        self.assertEqual(ask_language.country_as_us("Who owns United States Steel?"), "Who owns United States Steel?")
        self.assertEqual(ask_language.country_as_us("price of the United States Oil Fund"), "price of the United States Oil Fund")

    def test_same_figures(self):
        same = ask_language.same_figures
        self.assertTrue(same("Revenue was $416bn in fiscal 2025, up 6.4%.", "2025年度の売上は$416bnで、6.4%増。"))
        self.assertTrue(same("Filed 31 March 2026.", "2026年3月31日に提出。"))
        self.assertFalse(same("Revenue was $416bn.", "売上は4,160億ドル。"))
        self.assertFalse(same("Revenue was $416bn.", "Le chiffre est de $461bn."))
        self.assertFalse(same("Up 6.4%.", "6.4%増、2025年。"))
        self.assertTrue(same("Up 6.4%.", "６．４％増"))                # full-width digits are the same digits
        self.assertTrue(same("Up 6.4%.", "en hausse de 6,4 %"))        # a decimal comma is the same figure
        self.assertFalse(same("Up 6.4%.", "en hausse de 64 %"))
        self.assertFalse(same("Revenue was $416bn.", "売上は416億ドル。"))   # the digits survive, the size does not
        self.assertFalse(same("Revenue was $416bn.", "Revenue was 416 M$."))
        self.assertTrue(same("Revenue was $3.5bn.", "Le chiffre : $3,5bn."))
        self.assertFalse(same("About 166,000.", "Etwa 166.000."))      # read as 166, so kept in English


class Currency(unittest.TestCase):
    def test_a_dollar_headline_over_a_yen_table_takes_the_yen(self):
        out = ask_guard.right_currency(revenue_answer())
        self.assertIn("¥50.68tn", out["headline"])
        self.assertEqual(out["chart"]["unit"], "JPY")

    def test_dollar_tables_stay_in_dollars(self):
        a = revenue_answer()
        a["table"]["unit"] = "USD"
        self.assertIn("$50.68tn", ask_guard.right_currency(a)["headline"])


class AsFiled(unittest.TestCase):
    def test_a_figure_it_does_not_hold_is_refused_not_answered_with_revenue(self):
        e = {"companies": [{"cik": 320193, "name": "Apple Inc.", "ticker": "AAPL"}]}
        with mock.patch.object(stock_recipes, "call_api", side_effect=AssertionError("revenue was fetched")):
            with self.assertRaises(HTTPException):
                stock_recipes.r_company_as_filed_history(None, "Apple's carbon emissions in each annual report", e)


if __name__ == "__main__":
    unittest.main()
