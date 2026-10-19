# -*- coding: utf-8 -*-
"""The result checks on the step-by-step answers (app/us_checks.py), built
from the misses seen on 19 October 2026. All in memory, no model, no data.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_checks
"""
import datetime
import os
import unittest
from unittest import mock

from app import us_checks

T = datetime.date(2026, 10, 19)


def plan(sql, headline="{x}", period=None, steps=None):
    return {"sql": sql, "headline": headline, "period": period or {"from": "2026-01-01", "to": None},
            "steps": steps or ["a step"]}


class MeanSwamped(unittest.TestCase):
    def test_mean_far_from_median_led_by_the_mean_is_revised(self):
        p = plan("SELECT avg(operating_margin_pct) AS average_operating_margin_pct, median(operating_margin_pct) AS median_operating_margin_pct, count(operating_margin_pct) AS n FROM stocks",
                 "Software companies run an average operating margin of {average_operating_margin_pct} (median {median_operating_margin_pct}).")
        f = us_checks.mean_swamped(p, [[-3643.4, -5.1, 340]], ["average_operating_margin_pct", "median_operating_margin_pct", "n"])
        self.assertEqual(f["severity"], "revise")
        self.assertIn("median", f["instruction"])

    def test_led_by_the_median_is_only_a_note(self):
        p = plan("SELECT median(x) AS median_pct, avg(x) AS avg_pct", "The median is {median_pct}; the mean {avg_pct}.")
        f = us_checks.mean_swamped(p, [[-5.1, -3643.4]], ["median_pct", "avg_pct"])
        self.assertEqual(f["severity"], "note")
        self.assertIn("−3643.4%", f["note"])

    def test_close_mean_and_median_pass(self):
        p = plan("SELECT avg(x) AS avg_pct, median(x) AS median_pct", "{avg_pct}")
        self.assertIsNone(us_checks.mean_swamped(p, [[12.0, 10.5]], ["avg_pct", "median_pct"]))

    def test_extreme_mean_alone_is_revised(self):
        p = plan("SELECT avg(x) AS avg_margin_pct", "{avg_margin_pct}")
        self.assertEqual(us_checks.mean_swamped(p, [[-900.0]], ["avg_margin_pct"])["severity"], "revise")


class TinyBase(unittest.TestCase):
    def test_growth_ranked_without_a_floor_is_revised(self):
        p = plan("SELECT ticker, name, revenue_growth_pct FROM stocks WHERE revenue_growth_pct > 20 ORDER BY revenue_growth_pct DESC LIMIT 10")
        f = us_checks.tiny_base(p, [["VRDN", "Viridian", 23359.9]], ["ticker", "name", "revenue_growth_pct"])
        self.assertEqual(f["severity"], "revise")
        self.assertIn("revenue_prior_usd", f["instruction"])

    def test_floored_growth_passes(self):
        p = plan("SELECT ticker, revenue_growth_pct FROM stocks WHERE revenue_prior_usd >= 5e7 ORDER BY revenue_growth_pct DESC")
        self.assertIsNone(us_checks.tiny_base(p, [["A", 45.0]], ["ticker", "revenue_growth_pct"]))

    def test_growth_mentioned_but_not_ranked_passes(self):
        p = plan("SELECT count(*) FILTER (WHERE revenue_growth_pct > 10) AS n, count(revenue_growth_pct) AS members FROM stocks")
        self.assertIsNone(us_checks.tiny_base(p, [[40, 400]], ["n", "members"]))


class FewBehind(unittest.TestCase):
    def test_aggregate_without_a_count_is_revised(self):
        p = plan("SELECT median(pe_x) AS median_pe_x FROM stocks WHERE in_sp500")
        f = us_checks.few_behind("median P/E of the S&P 500", p, [[22.1]], ["median_pe_x"])
        self.assertEqual(f["severity"], "revise")

    def test_small_count_is_a_note(self):
        p = plan("SELECT avg(roe_pct) AS avg_roe_pct, count(roe_pct) AS n FROM stocks WHERE industry ILIKE '%TOBACCO%'")
        f = us_checks.few_behind("average ROE of tobacco companies", p, [[30.2, 4]], ["avg_roe_pct", "n"])
        self.assertEqual(f["severity"], "note")
        self.assertIn("Only 4", f["note"])

    def test_how_many_with_a_small_count_is_fine(self):
        p = plan("SELECT count(*) FILTER (WHERE x) AS n, count(y) AS members FROM stocks")
        self.assertIsNone(us_checks.few_behind("how many Dow stocks fell", p, [[3, 30]], ["n", "members"]))


class WordVsColumn(unittest.TestCase):
    def test_debt_as_liabilities_unnamed_is_revised(self):
        p = plan("SELECT ticker, cash_usd, total_assets_usd - equity_usd AS debt_usd FROM stocks WHERE cash_usd > total_assets_usd - equity_usd",
                 "{name} leads the companies with more cash than debt.")
        f = us_checks.word_vs_column("which companies have more cash than debt", p)
        self.assertEqual(f["severity"], "revise")
        self.assertIn("borrowings_usd", f["instruction"])

    def test_debt_as_borrowings_passes(self):
        p = plan("SELECT ticker FROM stocks WHERE cash_usd > borrowings_usd")
        self.assertIsNone(us_checks.word_vs_column("more cash than debt", p))

    def test_liabilities_named_in_the_headline_passes(self):
        p = plan("SELECT ticker, total_assets_usd - equity_usd AS liabilities_usd FROM stocks",
                 "{name} holds more cash than total liabilities (for debt).")
        self.assertIsNone(us_checks.word_vs_column("more cash than debt", p))


class Impossible(unittest.TestCase):
    def test_leading_row_with_an_absurd_value_is_revised(self):
        f = us_checks.impossible([["X", -4000.0], ["Y", -20.0]], ["ticker", "operating_margin_pct"])
        self.assertEqual(f["severity"], "revise")

    def test_absurd_value_lower_down_is_a_note(self):
        f = us_checks.impossible([["X", 32.6], ["Y", 80.0]], ["ticker", "dividend_yield_pct"])
        self.assertEqual(f["severity"], "note")

    def test_ordinary_values_pass(self):
        self.assertIsNone(us_checks.impossible([["X", 15.0, 3.1]], ["ticker", "pe_x", "dividend_yield_pct"]))


class Shape(unittest.TestCase):
    def test_how_many_answered_with_a_list_is_revised(self):
        f = us_checks.how_many("how many banks have ROE above 15%", [["A", 16.0], ["B", 17.0]], ["ticker", "roe_pct"])
        self.assertEqual(f["severity"], "revise")
        self.assertIsNone(us_checks.how_many("how many banks", [[12, 200]], ["n", "members"]))

    def test_named_year_outside_the_period_is_revised(self):
        p = plan("SELECT 1", period={"from": "2026-01-01", "to": None})
        self.assertEqual(us_checks.period("which stocks fell most in 2024", p, T)["severity"], "revise")
        p = plan("SELECT 1", period={"from": "2024-01-01", "to": "2024-12-31"})
        self.assertIsNone(us_checks.period("which stocks fell most in 2024", p, T))
        self.assertIsNone(us_checks.period("how many beat the S&P 500", p, T))

    def test_other_market_is_refused_before_anything_else(self):
        fs = us_checks.check("which Japanese banks are cheapest", plan("SELECT 1"), [["A", 6.2]], ["ticker", "pe_x"], "x", T)
        self.assertEqual([f["severity"] for f in fs], ["refuse"])
        self.assertIn("Japan tab", fs[0]["note"])
        self.assertEqual(us_checks.check("cheapest US banks", plan("SELECT 1"), [["A", 6.2]], ["ticker", "pe_x"], "x", T), [])

    def test_refused_headline_is_a_finding(self):
        fs = us_checks.check("how many", plan("SELECT count(*) AS n FROM stocks", "About 300 beat it."), [[202]], ["n"], None, T)
        self.assertEqual(fs[0]["check"], "headline")
        self.assertIn("n", fs[0]["instruction"])

    def test_revise_text_and_notes(self):
        fs = [us_checks.finding("a", "revise", "n1", "do this"), us_checks.finding("b", "note", "n2"),
              us_checks.finding("c", "revise", "n3", "do that")]
        self.assertEqual(us_checks.to_revise(fs), "do this do that Reply with the whole JSON object again.")
        self.assertEqual(us_checks.notes(fs), ["n2"])
        self.assertIsNone(us_checks.to_revise([fs[1]]))


class Review(unittest.TestCase):
    def setUp(self):
        us_checks._REVIEWS.clear()

    def test_model_off_is_ok(self):
        with mock.patch.dict(os.environ, {"US_ASK_MODEL": "0"}):
            self.assertEqual(us_checks.review("q", plan("SELECT 1"), "h", [[1]], ["n"])["verdict"], "ok")

    def test_verdict_is_read_and_cached(self):
        calls = []

        def fake(prov, key, model, messages, max_tokens=300, timeout=None, **kw):
            calls.append(messages)
            return {"text": "Sure: {\"verdict\": \"revise\", \"reason\": \"wrong group\"}", "usage": {}}
        with mock.patch("app.us_ask_model.provider", return_value=("deepseek", "k", "m")), \
                mock.patch("app.us_ask_model.under_cap", return_value=True), \
                mock.patch("app.us_ask_model._tick"), \
                mock.patch("app.us_ask_model._complete", side_effect=fake):
            r1 = us_checks.review("Which banks did best?", plan("SELECT 1"), "X did best.", [["X", 3.0]], ["ticker", "change_period_pct"])
            r2 = us_checks.review("which banks did best", plan("SELECT 1"), "X did best.", [["X", 3.0]], ["ticker", "change_period_pct"])
        self.assertEqual(r1, {"verdict": "revise", "reason": "wrong group"})
        self.assertEqual(r2, r1)
        self.assertEqual(len(calls), 1)
        self.assertIn("QUESTION: Which banks did best?", calls[0][0]["content"])

    def test_unreadable_or_failing_model_is_ok(self):
        with mock.patch("app.us_ask_model.provider", return_value=("deepseek", "k", "m")), \
                mock.patch("app.us_ask_model.under_cap", return_value=True), \
                mock.patch("app.us_ask_model._tick"), \
                mock.patch("app.us_ask_model._complete", side_effect=TimeoutError()):
            self.assertEqual(us_checks.review("q", plan("SELECT 1"), "h", [[1]], ["n"])["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
