# -*- coding: utf-8 -*-
"""Japanese companies in the question box (app/jp_company_ask.py) and the second
try a refusal gets (app/ask_guard.py). Figures are from Kioxia's real filings."""
import time
import unittest
from unittest import mock

from fastapi import HTTPException

from app import ask_guard, ask_miss, jp_company_ask as jp, stock_recipes, us_ask_model

KIOXIA = {"code": "285A", "name_en": "Kioxia Holdings Corporation", "name_ja": u"キオクシアホールディングス株式会社", "industry": u"電気機器"}


def report(doc, filer, filed, before, after, no):
    return {"doc_id": doc, "filer_edinet_code": filer, "filer_name": filer, "filed_date": filed, "prior_ratio_pct": before, "ratio_pct": after, "change_no": no}


def holder(code, name, shares):
    return {"holder_edinet_code": code, "name_en": name, "name_raw": name, "shares_held": shares}


STAKES = {
    "groups": [
        dict(report("old", "E40363", "2026-06-01", 20.96, 19.57, 14), holders=[holder("E40363", "BCPE Pangea Cayman, L.P.", 0), holder("E40364", "BCPE Pangea Cayman2, Ltd.", 77400000)]),
        dict(report("new", "E40364", "2026-06-16", 18.16, 14.17, 16), holders=[holder("E40364", "BCPE Pangea Cayman2, Ltd.", 77400000), holder("E40365", "BCPE Pangea Cayman 1A, L.P.", 0)]),
        dict(report("tos", "E01738", "2026-10-08", 12.84, 11.8, 21), holders=[holder("E01738", "TOSHIBA CORPORATION", 194193100)]),
    ],
    "reports": [report("tos", "E01738", "2026-10-08", 12.84, 11.8, 21), report("new", "E40364", "2026-06-16", 18.16, 14.17, 16),
                report("old", "E40363", "2026-06-01", 20.96, 19.57, 14), report("first", "E40363", "2026-02-25", 44.33, 43.84, 3),
                report("tos0", "E01738", "2026-03-25", 20.85, 19.61, 14)],
}
REGISTER = {"period_end": "2026-03-31", "foreign_pct": 68.47, "holders": [
    {"name_raw": u"株式会社東芝", "holder_name_en": "TOSHIBA CORPORATION", "holder_edinet_code": "E01738", "holder_kind": "entity", "ratio_pct": 17.59},
    {"name_raw": u"BCPE Pangea Cayman 1A, L.P.（常任代理人　三菱UFJモルガン・スタンレー証券株式会社）", "holder_edinet_code": None, "holder_kind": "foreign_entity", "ratio_pct": 4.91},
    {"name_raw": u"日本マスタートラスト信託銀行株式会社（信託口）", "holder_edinet_code": None, "holder_kind": "trust_bank_nominee", "ratio_pct": 3.16},
]}


def with_stakes():
    return mock.patch("app.lvh_api.company", return_value=STAKES)


class Stakes(unittest.TestCase):
    def test_a_group_that_changed_lead_filer_is_one_seller(self):
        with with_stakes():
            out = jp._r_stakes(KIOXIA, "who is selling Kioxia?")
        self.assertEqual(len(out["table"]["rows"]), 2, "the Bain vehicles file under two numbers, one stake")
        self.assertIn("BCPE Pangea Cayman2, Ltd. from 44.33% to 14.17%", out["headline"])
        self.assertIn("Toshiba Corporation from 20.85% to 11.80%", out["headline"])

    def test_a_decline_is_shown_with_a_true_minus(self):
        with with_stakes():
            rows = jp._r_stakes(KIOXIA, "who is selling Kioxia?")["table"]["rows"]
        self.assertEqual(rows[0][3], u"−30.16 pp")

    def test_buying_is_not_answered_with_selling(self):
        with with_stakes():
            out = jp._r_stakes(KIOXIA, "who is buying Kioxia?")
        self.assertIn("No holder of 5% or more has reported buying", out["headline"])

    def test_a_period_it_cannot_read_is_said_not_dropped(self):
        with with_stakes(), self.assertRaises(HTTPException):
            jp._r_stakes(KIOXIA, "who is selling Kioxia since the election?")


class Owners(unittest.TestCase):
    def test_owners_count_a_holder_once_with_the_latest_figure(self):
        with with_stakes(), mock.patch("app.ownership_api.company", return_value=REGISTER):
            out = jp._r_owners(KIOXIA, "Who owns Kioxia?")
        names = [r[0] for r in out["table"]["rows"]]
        self.assertIn("BCPE Pangea Cayman2, Ltd.", out["headline"])
        self.assertNotIn("BCPE Pangea Cayman 1A, L.P.", names, "already counted in its filing group")
        self.assertEqual(sum(1 for n in names if "Toshiba" in n), 1)
        self.assertTrue(any("custody account" in n for n in names))

    def test_a_custody_bank_never_leads_the_headline(self):
        only = {"period_end": "2026-03-31", "holders": REGISTER["holders"][::-1]}
        with mock.patch("app.lvh_api.company", side_effect=HTTPException(404, "none")), mock.patch("app.ownership_api.company", return_value=only):
            out = jp._r_owners(KIOXIA, "Who owns Kioxia?")
        self.assertNotIn(u"マスタートラスト", out["headline"])


class Results(unittest.TestCase):
    def test_a_half_year_names_its_months_and_says_prices_are_not_held(self):
        d = {"release": {"filed_date": "2026-10-30", "period_label": "Second quarter", "months_covered": 6, "fiscal_period": "2027-03"},
             "results": [{"line": "revenue", "label": "Revenue", "value": 3e12, "prior_value": 1e12, "yoy_pct_published": 200.0, "unit": "JPY"}]}
        with mock.patch("app.earnings_api.company", return_value=d):
            out = jp._r_results(KIOXIA, "How has Kioxia done this year?")
        self.assertIn("the six months to September 2026", out["headline"])
        self.assertTrue(any("share prices" in n for n in out["notes"]))

    def test_a_consolidated_figure_and_a_year_not_on_file(self):
        d = {"panel": [{"fiscal_year_end": "2025-03-31", "values": {"revenue": 1.7e12}}, {"fiscal_year_end": "2026-03-31", "values": {"revenue": 2.3e12}}]}
        with mock.patch("app.financials_api.company", return_value=d) as call:
            self.assertIn("up 35.3%", jp._r_figure(KIOXIA, "Kioxia revenue")["headline"])
            self.assertEqual(call.call_args.kwargs["basis"], "consolidated")
            with self.assertRaises(HTTPException) as got:
                jp._r_figure(KIOXIA, "Kioxia revenue in 2019")
        self.assertIn("2019", got.exception.detail)


class Reading(unittest.TestCase):
    def shape(self, q):
        return jp._shape(q, KIOXIA)

    def test_the_two_questions_in_the_log_and_their_neighbours(self):
        for q, want in (("How has Kioxia done this year?", "results"), ("who is selling Kioxia?", "stakes"), ("who is buying Kioxia?", "stakes"),
                        ("Who owns Kioxia?", "owners"), ("Kioxia share price", "price"), ("Kioxia revenue", "figure"), ("Kioxia", "results"),
                        ("What does Kioxia do?", "business"), ("What does Kioxia sell?", "business"), ("Kioxia buyback", "buybacks"),
                        ("Kioxia short interest", "shorts"), ("Kioxia EPS", "figure"), ("how many employees does Kioxia have", "figure")):
            self.assertEqual(self.shape(q), want, q)

    def test_words_it_does_not_read_are_left_to_the_box(self):
        self.assertIsNone(self.shape("Kioxia carbon emissions"))

    def names(self):
        rows = [{"code": "285A", "name_en": "Kioxia Holdings Corporation"}, {"code": "2788", "name_en": "APPLE INTERNATIONALCO.,LTD."},
                {"code": "9999", "name_en": "Best Denki Co., Ltd."}, {"code": "9983", "name_en": "FAST RETAILING CO., LTD."}, {"code": "3962", "name_en": "CHANGE Holdings,Inc."}]
        first = {}
        for r in rows:
            first.setdefault(jp._core(r["name_en"]).split()[0], []).append(r)
        return mock.patch.object(jp, "_load_names", return_value={"by_core": {jp._core(r["name_en"]): r for r in rows}, "by_first": first, "by_code": {r["code"]: r for r in rows}})

    def test_a_name_a_year_and_a_common_word(self):
        with self.names():
            self.assertEqual(jp._named("who is selling Kioxia?")["code"], "285A")
            self.assertEqual(jp._named("285A revenue")["code"], "285A")
            self.assertEqual(jp._named("Fast Retailing results")["code"], "9983")
            self.assertIsNone(jp._named("what are the best stocks in 2026"))
            # a real miss in testing: a ranking question read as the company Change
            self.assertIsNone(jp._named("The ten largest US companies by market value, with P/E and one-year change."))

    def test_a_us_company_stays_american(self):
        with self.names(), mock.patch("app.us_link.find_companies", return_value=[{"name": "Apple Inc.", "exchange": "Nasdaq"}]):
            self.assertIsNone(jp.resolve(object(), "Who owns Apple?"))

    def test_over_the_counter_is_thin_and_a_real_listing_is_not(self):
        with self.names(), mock.patch("app.us_link.find_companies", return_value=[{"name": "Kioxia Holdings Corporation", "exchange": "OTC"}]):
            self.assertTrue(jp.resolve(object(), "Kioxia")[1])
        with self.names(), mock.patch("app.us_link.find_companies", return_value=[{"name": "Kioxia Holdings Corporation", "exchange": "NYSE"}]):
            self.assertFalse(jp.resolve(object(), "Kioxia")[1])

    def test_advice_and_why_keep_their_own_answers(self):
        self.assertIsNone(jp.first_try(None, "Should I buy Kioxia?"))
        self.assertIsNone(jp.first_try(None, "Why did Kioxia fall?"))

    def test_a_listed_company_only_comes_here_for_who_owns_and_trades_it(self):
        with mock.patch.object(jp, "resolve", return_value=(KIOXIA, False)), mock.patch.object(jp, "_run", return_value={"ok": 1}):
            self.assertIsNone(jp.first_try(None, "Kioxia revenue"))
            self.assertEqual(jp.first_try(None, "who is selling Kioxia?"), {"ok": 1})

    def test_a_price_question_says_prices_are_not_held(self):
        with self.assertRaises(HTTPException) as got:
            jp._run("price", KIOXIA, "Kioxia share price", True)
        self.assertIn("Tokyo share prices", got.exception.detail)
        self.assertEqual(got.exception.miss, "not_held")


class SecondTry(unittest.TestCase):
    def refused(self, text="No annual figure for capex is on file for Foo.", kind="not_held"):
        return ask_miss.miss(text, kind)

    def test_the_japanese_filings_are_read_after_a_refusal(self):
        with mock.patch.object(jp, "second_try", return_value={"headline": "from Japan"}), mock.patch.object(ask_guard, "on_topic", return_value=(True, None)):
            self.assertEqual(ask_guard._second_try(None, "q", None, self.refused())["headline"], "from Japan")

    def test_then_step_by_step_once_and_checked(self):
        with mock.patch.object(jp, "second_try", return_value=None), mock.patch("app.us_steps.tried", return_value=False), \
                mock.patch("app.us_steps.answer", return_value={"headline": "worked out"}) as steps:
            with mock.patch.object(ask_guard, "on_topic", return_value=(True, None)):
                self.assertEqual(ask_guard._second_try(None, "Foo capex", None, self.refused())["headline"], "worked out")
            with mock.patch.object(ask_guard, "on_topic", return_value=(False, "x")):
                self.assertIsNone(ask_guard._second_try(None, "Foo capex", None, self.refused()))
        self.assertEqual(steps.call_count, 2)

    def test_not_a_second_time_when_steps_already_refused(self):
        with mock.patch.object(jp, "second_try", return_value=None), mock.patch("app.us_steps.tried", return_value=True), \
                mock.patch("app.us_steps.answer") as steps:
            self.assertIsNone(ask_guard._second_try(None, "q", None, self.refused()))
        steps.assert_not_called()

    def test_some_refusals_stand(self):
        with mock.patch.object(jp, "second_try") as japan:
            for exc in (ask_miss.miss("advice", "policy"), ask_miss.miss("asdf", "unclear"), HTTPException(500, "boom"), self.refused(stock_recipes.MACRO_MISS)):
                self.assertIsNone(ask_guard._second_try(None, "q", None, exc))
        japan.assert_not_called()

    def test_a_refusal_that_stands_goes_out_as_it_was(self):
        def inner(cur, q, prev, opts):
            raise self.refused()
        with mock.patch.object(ask_guard, "_japan", return_value=None), mock.patch.object(ask_guard, "_second_try", return_value=None), \
                mock.patch.object(ask_guard, "_own_data", return_value=None):
            with self.assertRaises(HTTPException) as got:
                ask_guard.answer(None, "Foo capex", None, None, inner)
        self.assertIn("capex", got.exception.detail)


class CheckWithTheNetworkDown(unittest.TestCase):
    def test_a_stuck_call_cannot_hold_an_answer_and_the_next_checks_skip_it(self):
        def stuck(*a, **k):
            time.sleep(3)
        out = {"headline": "x", "match": {"via": "quick"}}
        with mock.patch.object(us_ask_model, "provider", return_value=("openai", "k", "m")), mock.patch.object(us_ask_model, "under_cap", return_value=True), \
                mock.patch.object(us_ask_model, "_complete", side_effect=stuck) as call, mock.patch.object(ask_guard, "_log"), \
                mock.patch.object(ask_guard, "_hung_until", [0]):
            began = time.time()
            self.assertEqual(ask_guard.on_topic("q", out, seconds=0), (True, None))
            self.assertLess(time.time() - began, 2)
            self.assertEqual(ask_guard.on_topic("q", out, seconds=0), (True, None))
            self.assertEqual(call.call_count, 1)


class Suggestions(unittest.TestCase):
    def test_a_question_that_would_also_be_refused_is_not_offered(self):
        with mock.patch.object(ask_miss, "company_in", return_value="Acme"), \
                mock.patch.object(ask_miss, "_answers", side_effect=lambda cur, q: "done" not in q):
            got = ask_miss.suggestions("Acme price", "not_held", "", object())
        self.assertNotIn("How has Acme done this year?", got)
        self.assertIn("Who owns Acme?", got)


class WhoTrades(unittest.TestCase):
    def test_who_is_selling_a_company_is_about_its_stock(self):
        for q in ("who is selling", "who's buying", "who has been selling", "which funds are buying", "anyone selling the stock"):
            self.assertTrue(stock_recipes._WHO_TRADES.fullmatch(q), q)
        self.assertFalse(stock_recipes._WHO_TRADES.fullmatch("did the ceo sell"))


if __name__ == "__main__":
    unittest.main()
