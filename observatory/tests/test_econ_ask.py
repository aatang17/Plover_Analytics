# -*- coding: utf-8 -*-
"""The question box's reader for the whole economic catalogue (app/econ_ask.py).

Written after "Japan interest payments as % of Government expenses for last 5 yrs" was
told the data did not hold them, with both on file. The model's calls are stubbed: what
is tested is that no figure comes from it, and that it cannot pick what it was not shown.
"""
import unittest
from unittest import mock

from fastapi import HTTPException

from app import analysis, ask_guard, ask_miss, ask_review, econ_ask, stock_recipes, us_figures

SHOWN = {"govt-accounts-jp:central.24": {"code": "central.24", "name_en": "Central government — Interest paid", "unit": "¥bn"},
         "govt-accounts-jp:central.2": {"code": "central.2", "name_en": "Central government — Expense", "unit": "¥bn"}}
ROWS = [["2022-04-01", 7343.3, 116633.2], ["2023-04-01", 7656.6, 111020.8], ["2024-04-01", 8378.5, 111023.3]]
SOURCES = [{"name": "s0", "series": "govt-accounts-jp:central.24", "credit": "Source: Cabinet Office.", "release": "FY2024", "unit": "¥bn"},
           {"name": "s1", "series": "govt-accounts-jp:central.2", "credit": "Source: Cabinet Office.", "release": "FY2024", "unit": "¥bn"}]


def picked(**kw):
    form = {"found": True, "series": list(SHOWN), "calc": "share", "years": 5, "title": "Japan central government interest paid as a share of spending",
            "note": "Central government only."}
    form.update(kw)
    return form


class CheckedTest(unittest.TestCase):
    def test_a_series_it_was_not_shown_is_refused(self):
        with self.assertRaises(econ_ask._Lost):
            econ_ask._checked(picked(series=["govt-accounts-jp:central.24", "made-up:1"]), SHOWN)

    def test_a_calculation_outside_the_list_is_refused(self):
        with self.assertRaises(econ_ask._Lost):
            econ_ask._checked(picked(calc="average"), SHOWN)

    def test_a_share_needs_exactly_two(self):
        with self.assertRaises(econ_ask._Lost):
            econ_ask._checked(picked(series=list(SHOWN)[:1]), SHOWN)

    def test_a_note_with_a_figure_is_dropped(self):
        self.assertEqual(econ_ask._checked(picked(note="Interest is about 7.5% of spending."), SHOWN)["note"], "")
        self.assertEqual(econ_ask._checked(picked(), SHOWN)["note"], "Central government only.")

    def test_closest_keeps_only_what_it_was_shown(self):
        form = econ_ask._checked({"found": False, "missing": "US interest", "closest": [
            {"ref": "govt-accounts-jp:central.24", "question": "Japan interest paid"}, {"ref": "x:y", "question": "Invented"}]}, SHOWN)
        self.assertEqual(form["closest"], ["Japan interest paid"])


class BuildTest(unittest.TestCase):
    def built(self, form=None, rows=ROWS):
        with mock.patch.object(analysis, "load", return_value=([list(r) for r in rows], ["date", "s0", "s1"], SOURCES)):
            return econ_ask.build("q", econ_ask._checked(form or picked(), SHOWN))

    def test_the_share_is_worked_out_by_code(self):
        out = self.built()
        self.assertIn("7.5% in FY2024", out["headline"])
        self.assertIn("6.3% in FY2022", out["headline"])
        self.assertEqual(out["table"]["rows"][-1][0], "FY2024")
        self.assertAlmostEqual(out["table"]["rows"][-1][-1], 100 * 8378.5 / 111023.3, places=3)
        self.assertEqual(out["match"]["via"], "economy")
        self.assertEqual(out["chart"]["kind"], "cols")
        self.assertEqual(out["chart"]["categories"][-1], "FY2024")
        self.assertIn("FY2024", out["figures"][0]["label"])

    def test_the_formula_and_series_are_shown(self):
        out = self.built()
        self.assertIn("÷", out["calc"]["formula"])
        self.assertIn("govt-accounts-jp:central.24", out["calc"]["series"])
        self.assertEqual(out["sources"][0]["credit"], "Source: Cabinet Office.")

    def test_a_missing_value_stays_missing(self):
        out = self.built(rows=[["2022-04-01", None, 116633.2], ["2023-04-01", 7656.6, 111020.8], ["2024-04-01", 8378.5, 111023.3]])
        self.assertEqual([r[0] for r in out["table"]["rows"]], ["FY2023", "FY2024"])

    def test_a_share_of_a_rate_is_refused(self):
        shown = dict(SHOWN)
        shown["govt-accounts-jp:central.2"] = dict(SHOWN["govt-accounts-jp:central.2"], unit="%")
        form = econ_ask._checked(picked(), shown)
        with mock.patch.object(analysis, "load", return_value=(ROWS, ["date", "s0", "s1"], SOURCES)):
            with self.assertRaises(econ_ask._Lost):
                econ_ask.build("q", form)

    def test_yen_scales_are_put_on_one_scale(self):
        shown = {"a:d": {"code": "d", "name_en": "Debt", "unit": "¥100mn"}, "b:g": {"code": "g", "name_en": "GDP", "unit": "¥bn"}}
        form = econ_ask._checked(picked(series=["a:d", "b:g"]), shown)
        with mock.patch.object(analysis, "load", return_value=([["2024-01-01", 12000000.0, 600000.0]], ["date", "s0", "s1"], SOURCES)):
            out = econ_ask.build("q", form)
        self.assertAlmostEqual(out["table"]["rows"][0][-1], 200.0)

    def test_different_kinds_of_unit_are_refused(self):
        shown = {"a:d": {"code": "d", "name_en": "Visitors", "unit": "persons"}, "b:g": {"code": "g", "name_en": "GDP", "unit": "¥bn"}}
        form = econ_ask._checked(picked(series=["a:d", "b:g"]), shown)
        with mock.patch.object(analysis, "load", return_value=(ROWS, ["date", "s0", "s1"], SOURCES)):
            with self.assertRaises(econ_ask._Lost):
                econ_ask.build("q", form)

    def test_last_n_years_counts_back_from_the_newest_reading(self):
        rows = [["%d-04-01" % y, float(y), 1000.0] for y in range(2010, 2025)]
        out = self.built(rows=rows)
        self.assertEqual([r[0] for r in out["table"]["rows"]], ["FY2020", "FY2021", "FY2022", "FY2023", "FY2024"])

    def test_year_on_year_uses_history_before_the_window(self):
        rows = [["%d-01-01" % y, 100.0 + y - 2015] for y in range(2015, 2025)]
        form = econ_ask._checked(picked(series=list(SHOWN)[:1], calc="yoy", years=1), SHOWN)
        with mock.patch.object(analysis, "load", return_value=(rows, ["date", "s0"], SOURCES[:1])):
            out = econ_ask.build("q", form)
        self.assertEqual(len(out["table"]["rows"]), 1)
        self.assertAlmostEqual(out["table"]["rows"][0][-1], 100 * (109.0 / 108.0 - 1), places=3)


class TryAgainTest(unittest.TestCase):
    def test_a_choice_that_cannot_be_worked_out_is_told_why_once(self):
        weights = {"w:shelter": {"code": "shelter", "name_en": "Shelter", "unit": "percent"}, "w:all": {"code": "all", "name_en": "All items", "unit": "percent"}}
        first = econ_ask._checked(picked(series=list(weights)), weights)
        first["_again"] = ("system", "user", weights)
        second = picked(series=["w:shelter"], calc="level", title="Shelter's weight")
        rows = [["2024-01-01", 35.0], ["2025-01-01", 35.6]]
        with mock.patch.object(econ_ask, "read", return_value=first), mock.patch.object(econ_ask, "_ask_model", return_value=second) as ask, \
                mock.patch.object(analysis, "load", side_effect=[([["2025-01-01", 35.6, 100.0]], ["date", "s0", "s1"], SOURCES), (rows, ["date", "s0"], SOURCES[:1])]):
            out = econ_ask.answer("how much of the US CPI is shelter")
        self.assertIn("could not be used", ask.call_args[0][1])
        self.assertIn("35.6", out["headline"])

    def test_it_is_told_only_once(self):
        weights = {"w:a": {"code": "a", "name_en": "A", "unit": "%"}, "w:b": {"code": "b", "name_en": "B", "unit": "%"}}
        first = econ_ask._checked(picked(series=list(weights)), weights)
        first["_again"] = ("system", "user", weights)
        with mock.patch.object(econ_ask, "read", return_value=first), mock.patch.object(econ_ask, "_ask_model", return_value=picked(series=list(weights))) as ask, \
                mock.patch.object(analysis, "load", return_value=([["2025-01-01", 1.0, 2.0]], ["date", "s0", "s1"], SOURCES)), mock.patch("app.us_steps.log"):
            self.assertIsNone(econ_ask.answer("q"))
        self.assertEqual(ask.call_count, 1)


class MoreCalculationsTest(unittest.TestCase):
    def test_a_yearly_amount_takes_the_years_four_gdp_quarters(self):
        rows = [["2024-04-01", 100.0, 600.0], ["2024-07-01", None, 610.0], ["2024-10-01", None, 620.0], ["2025-01-01", None, 630.0], ["2025-04-01", 200.0, 640.0]]
        self.assertEqual(econ_ask._over_yearly_gdp(rows), [["2024-04-01", 100.0, 615.0]])

    def test_only_whole_years_add_up(self):
        rows = [["2024-%02d-01" % m, 10.0] for m in range(1, 13)] + [["2025-01-01", 10.0], ["2025-02-01", 10.0]]
        got = econ_ask._annual_sum(rows, "persons")
        self.assertEqual(got[0], [["2024-01-01", 120.0]])

    def test_a_rate_does_not_add_up(self):
        with self.assertRaises(econ_ask._Lost):
            econ_ask._annual_sum([["2024-01-01", 2.0], ["2024-02-01", 2.0]], "%")

    def test_a_gap_between_rates_is_in_points(self):
        shown = {"a:x": {"code": "x", "name_en": "US 10Y", "unit": "%"}, "b:y": {"code": "y", "name_en": "JP 10Y", "unit": "%"}}
        form = econ_ask._checked(picked(series=list(shown), calc="difference", title="The gap"), shown)
        with mock.patch.object(analysis, "load", return_value=([["2026-09-01", 4.9, 2.8], ["2026-10-01", 4.94, 2.82]], ["date", "s0", "s1"], SOURCES)):
            out = econ_ask.build("q", form)
        self.assertIn("2.12 pp", out["headline"])

    def test_a_series_that_stopped_long_ago_is_not_an_answer(self):
        form = econ_ask._checked(picked(series=list(SHOWN)[:1], calc="level"), SHOWN)
        with mock.patch.object(analysis, "load", return_value=([["1983-04-01", 1.0], ["1984-04-01", 2.0]], ["date", "s0"], SOURCES[:1])):
            with self.assertRaises(econ_ask._Lost):
                econ_ask.build("q", form)


class EveryDestinationTest(unittest.TestCase):
    SHOWN = {"t:kr": {"code": "kr", "name_en": "Cars — exports to Korea (value)", "unit": "jpy_1000", "every_destination": True}}

    def test_a_line_standing_for_every_destination_is_never_one_destination(self):
        self.assertEqual(econ_ask._checked(picked(series=list(self.SHOWN), calc="level"), self.SHOWN)["calc"], "total")
        form = econ_ask._checked(picked(series=list(self.SHOWN), calc="annual_sum"), self.SHOWN)
        self.assertTrue(form["across"])

    def test_a_destination_the_question_named_stays_itself(self):
        shown = {"t:kr": dict(self.SHOWN["t:kr"], every_destination=False)}
        self.assertEqual(econ_ask._checked(picked(series=list(shown), calc="level"), shown)["calc"], "level")

    def test_one_year_is_named_as_a_year(self):
        form = econ_ask._checked(picked(series=list(SHOWN)[:1], calc="annual_sum", years=1, title="Visitors"), SHOWN)
        rows = [["2025-%02d-01" % m, 10.0] for m in range(1, 13)]
        with mock.patch.object(analysis, "load", return_value=(rows, ["date", "s0"], SOURCES[:1])), mock.patch.object(econ_ask.datetime, "date", econ_ask.datetime.date):
            out = econ_ask.build("q", form)
        self.assertIn("in 2025", out["headline"])


class FlowsTest(unittest.TestCase):
    def test_a_years_change_is_never_read_as_debt(self):
        shown = {"g:f": {"code": "general.3303", "name_en": "General government — Liabilities — debt securities", "unit": "¥bn", "one_period_change": True},
                 "g:y": {"code": "gdp", "name_en": "GDP", "unit": "¥bn"}}
        with self.assertRaises(econ_ask._Lost):
            econ_ask._checked(picked(series=list(shown), title="Japan debt as a share of GDP"), shown)

    def test_the_gfs_flow_lines_are_marked(self):
        self.assertTrue(econ_ask.FLOW_NAMES["govt-accounts-jp"].search("general.3303"))
        self.assertFalse(econ_ask.FLOW_NAMES["govt-accounts-jp"].search("general.24"))


class ShortlistTest(unittest.TestCase):
    def rows(self):
        out = [{"series_id": i, "code": "c%d" % i, "name_en": "Central government — line %d" % i, "unit": "¥bn"} for i in range(200)]
        out.append({"series_id": 999, "code": "central.24", "name_en": "Central government — Interest paid", "unit": "¥bn"})
        return out

    def test_a_common_word_does_not_push_out_the_rare_one(self):
        class Con:
            def execute(self, *a):
                return self

            def fetchall(self):
                return []

            def close(self):
                pass
        with mock.patch.object(econ_ask.api, "_con", return_value=Con()), mock.patch.object(econ_ask.api, "_series_map", return_value=self.rows()):
            got = econ_ask.series_in("d", ["central government", "interest paid"])
        self.assertIn("central.24", [s["code"] for s in got])
        self.assertLessEqual(len(got), 120)

    def test_not_held_takes_a_second_wider_look(self):
        answers = [{"about": "economy", "datasets": ["d"], "words": ["x"]}, {"found": False, "missing": "x"},
                   picked(series=["d:central.24", "d:central.2"])]
        wide = {"d:central.24": SHOWN["govt-accounts-jp:central.24"], "d:central.2": SHOWN["govt-accounts-jp:central.2"]}
        lists = [({"d:other": {"code": "other", "name_en": "Other", "unit": "¥bn"}}, ["d:other | Other | ¥bn"]), (wide, ["d:central.24 | i | ¥bn"])]
        econ_ask._CACHE.clear()
        with mock.patch.object(econ_ask, "datasets", return_value=[{"id": "d", "name": {"en": "D"}}]), mock.patch.object(econ_ask, "_found_by_name", return_value=[]), \
                mock.patch.object(econ_ask, "_ask_model", side_effect=answers), mock.patch.object(econ_ask, "_listing", side_effect=lists):
            form = econ_ask.read("q")
        self.assertTrue(form["found"])
        self.assertEqual(form["series"][0]["ref"], "d:central.24")


class LiningUpTest(unittest.TestCase):
    def test_a_yearly_series_never_divides_by_a_quarterly_one(self):
        rows = [["2023-04-01", 10.0, 500.0], ["2023-07-01", None, 510.0], ["2023-10-01", None, 520.0], ["2024-01-01", None, 530.0], ["2024-04-01", 12.0, 540.0]]
        with self.assertRaises(econ_ask._Lost):
            econ_ask._same_frequency(rows)

    def test_the_same_frequency_passes(self):
        econ_ask._same_frequency([["2023-04-01", 1.0, 2.0], ["2024-04-01", 3.0, 4.0], ["2025-04-01", 5.0, 6.0]])

    def test_a_quarter_dated_differently_still_lines_up(self):
        rows = [["2026-04-01", None, 600.0], ["2026-06-01", 1200.0, None]]
        self.assertEqual(econ_ask._lined_up(rows), [["2026-06-01", 1200.0, 600.0]])

    def test_dates_in_common_are_used_as_they_are(self):
        rows = [["2026-04-01", 1.0, 2.0], ["2026-05-01", None, 3.0]]
        self.assertEqual(econ_ask._lined_up(rows), [["2026-04-01", 1.0, 2.0]])

    def test_destinations_of_one_line_become_its_total(self):
        shown = {"t:a": {"name_en": "Chips — exports to China (value)", "unit": "jpy_1000"}, "t:b": {"name_en": "Chips — exports to Taiwan (value)", "unit": "jpy_1000"}}
        self.assertEqual(econ_ask._checked(picked(series=list(shown), calc="level"), shown)["calc"], "total")
        self.assertEqual(econ_ask._checked(picked(series=list(shown), calc="level", compare_destinations=True), shown)["calc"], "level")


class GuardTest(unittest.TestCase):
    def refused(self, text="Plover's data doesn't include that.", kind="not_held"):
        return ask_miss.miss(text, kind)

    def inner(self, exc):
        def run(*a, **k):
            raise exc
        return run

    def answered(self, *a, **k):
        return {"headline": "From the catalogue.", "match": {"via": "economy"}}

    def ask(self, exc, reader, shows=(True, None)):
        with mock.patch.object(ask_guard, "_japan", return_value=None), mock.patch.object(ask_guard, "_own_data", return_value=None), \
                mock.patch.object(ask_guard, "_second_try", return_value=None), mock.patch.object(ask_guard, "on_topic", return_value=shows), \
                mock.patch.object(ask_guard.ask_language, "read", return_value=None), mock.patch.object(econ_ask, "answer", reader):
            return ask_guard.answer(None, "Japan interest payments as % of government expenses", None, None, self.inner(exc))

    def test_each_refusal_kind_goes_to_the_catalogue(self):
        for kind in ask_guard.ECONOMY_MISSES:
            self.assertEqual(self.ask(self.refused(kind=kind), self.answered)["headline"], "From the catalogue.")

    def test_advice_and_failures_do_not(self):
        for exc in (self.refused(kind="policy"), HTTPException(500, "boom")):
            with self.assertRaises(HTTPException):
                self.ask(exc, self.answered)

    def test_no_such_company_stays_refused(self):
        with self.assertRaises(HTTPException):
            self.ask(self.refused("No US-listed filer called Foo is on file."), self.answered)

    def test_not_economic_leaves_the_refusal(self):
        exc = self.refused()
        with self.assertRaises(HTTPException) as got:
            self.ask(exc, lambda *a, **k: None)
        self.assertIs(got.exception, exc)

    def test_the_catalogues_refusal_replaces_the_first(self):
        said = econ_ask.refusal({"missing": "US government interest payments", "closest": ["Japan interest paid"]})
        def reader(*a, **k):
            raise said
        with self.assertRaises(HTTPException) as got:
            self.ask(self.refused(), reader)
        self.assertIn("looked through", got.exception.detail)

    def test_an_off_topic_catalogue_answer_is_stopped(self):
        with self.assertRaises(HTTPException):
            self.ask(self.refused(), self.answered, shows=(False, "x"))

    def test_a_fixed_economy_answer_on_topic_goes_to_the_page(self):
        with mock.patch("app.assistant.preview._macro", return_value={"headline": "GDP"}):
            with self.assertRaises(HTTPException) as got:
                self.ask(HTTPException(422, stock_recipes.MACRO_MISS), self.answered)
        self.assertEqual(got.exception.detail, stock_recipes.MACRO_MISS)


class HonestRefusalTest(unittest.TestCase):
    def test_naming_japan_is_not_a_known_gap(self):
        said = "Plover's data doesn't include Japan's government interest payments or government expenses."
        self.assertNotEqual(us_figures.judge(said, "Japan interest payments")["verdict"], "gap")

    def test_a_catalogue_refusal_never_offers_a_company(self):
        exc = econ_ask.refusal({"missing": "Hong Kong population", "closest": []})
        with mock.patch.object(ask_miss, "company_in", side_effect=lambda cur, q: "Hong Kong Exchanges" if cur else None):
            got = ask_miss.reply("Hong Kong population", exc, object())
        self.assertFalse([t for t in got["try"] if "Exchanges" in t])

    def test_another_market_is_no_longer_filed_as_fine(self):
        self.assertNotIn("other_market", ask_review._NOT_ASKED_AGAIN)
        self.assertNotEqual(ask_review.sort_miss("Japan tax revenue", "other_market"), "fine")


if __name__ == "__main__":
    unittest.main()
