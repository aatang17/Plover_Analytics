# -*- coding: utf-8 -*-
"""Questions about a US price or a Treasury yield are read word by word
(app/assistant/macro_read.py), and CEO pay across years (app/stock_recipes.py).

Added 2026-11-01: "gasoline price" answered with the inflation rate, "how much
has ground beef gone up?" with today's price, "the 30 year" and "which CEOs got
the biggest raises?" not at all. The series was picked from one word and the
rest of the question dropped.
"""
import datetime
import unittest
from unittest import mock

from app import stock_recipes as sr
from app.assistant import macro_read as m, preview


def months(start, values):
    y, mo = start
    out = []
    for v in values:
        out.append(["%d-%02d-01" % (y, mo), v])
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
    return out


SRC = {"credit": "Source: U.S. Bureau of Labor Statistics.", "release": "Data through August 2026"}
BEEF = "Ground beef, 100% beef, per lb. (453.6 gm)"


def beef_rows(ds, code, measure, start=""):
    assert measure == "level"
    rows = months((2019, 1), [4.0] * 24 + [5.0] * 24 + [6.0] * 24 + [6.32] * 19 + [6.92])
    return rows, SRC, BEEF, "$", False


class Reading(unittest.TestCase):
    def test_a_maturity_alone_is_a_treasury_yield(self):
        for q, want in (("the 30 year", ["30Y"]), ("30y", ["30Y"]), ("ten year", ["10Y"]), ("3 month", ["3M"]), ("the long bond", ["30Y"]),
                        ("Has the 30 year gone up?", ["30Y"]), ("when was the 10 year last above 5%?", ["10Y"]), ("30 year vs 10 year", ["30Y", "10Y"])):
            self.assertEqual(m.treasury_tenors(q.lower()), want, q)

    def test_other_words_keep_it_from_being_a_yield(self):
        for q in ("5 year return on Apple", "Apple 10 year", "last 5 years best stock", "the 30 year mortgage rate", "10 year revenue growth", "best stock over 10 years"):
            self.assertEqual(m.treasury_tenors(q.lower()), [], q)

    def test_an_item_needs_a_price_frame_or_to_stand_alone(self):
        for q in ("gasoline price", "How much is gas?", "How much has ground beef gone up?", "eggs", "milk", "price of apples", "when was gas last above $4?"):
            self.assertTrue(m.item_of(q.lower())[0], q)
        for q in ("Apple price", "coffee stocks", "corn futures", "gas companies", "Turkey inflation"):
            self.assertIsNone(m.item_of(q.lower())[0], q)

    def test_gas_means_the_gallon_price_not_the_inflation_rate(self):
        self.assertEqual(m.item_of("gasoline price")[0], "APU000074714")
        self.assertEqual(m.item_of("gas price")[0], "APU000074714")

    def test_labels(self):
        self.assertEqual(m.item_label(BEEF), ("Ground beef, 100% beef", "a pound"))
        self.assertEqual(m.item_label("Eggs, grade A, large, per doz."), ("Eggs, grade A, large", "a dozen"))
        self.assertEqual(m.item_label("Gasoline, unleaded regular, per gallon/3.785 liters"), ("Gasoline, unleaded regular", "a gallon"))

    def test_price_in_japan_is_not_rice(self):
        self.assertIsNone(next((h for h in [preview.catalogue_hit("price of eggs in japan")] if h and h[1] == "0004"), None))

    def test_no_us_figure_stands_in_for_another_country_or_for_stocks(self):
        self.assertIsNone(preview.catalogue_hit("turkey inflation"))
        self.assertIsNone(preview.catalogue_hit("inflation in china"))
        self.assertIsNone(preview.catalogue_hit("milk stocks"))


class Answers(unittest.TestCase):
    def setUp(self):
        m._READ_CACHE.clear()
        self.addCleanup(m._READ_CACHE.clear)
        p = mock.patch.object(preview, "_series_rows", beef_rows)
        p.start()
        self.addCleanup(p.stop)
        d = mock.patch.object(preview, "_dataset_name", lambda ds: ("US Average Prices", "us-prices.html"))
        d.start()
        self.addCleanup(d.stop)

    def ask(self, q):
        return m.series_answer("us-avg-prices", "APU0000703112", q, m.item_of(q.lower())[1])

    def test_gone_up_gives_the_change_from_a_year_earlier(self):
        a = self.ask("How much has ground beef gone up?")
        self.assertIn("$6.92 a pound in August 2026, up 9.5% from $6.32 a year earlier", a["headline"])
        self.assertIn("Change =", a["calc"])

    def test_since_a_year_reads_from_that_year(self):
        a = self.ask("How much has ground beef risen since 2021?")
        self.assertIn("up 38.4% from $5.00 in January 2021", a["headline"])

    def test_yes_or_no_follows_the_direction_asked(self):
        self.assertTrue(self.ask("Has ground beef gone up?")["headline"].startswith("Yes. "))
        self.assertTrue(self.ask("Has ground beef gone down?")["headline"].startswith("No. "))

    def test_a_year_gives_its_range_not_its_december(self):
        a = self.ask("ground beef price in 2021")
        self.assertIn("averaged $5.00 a pound in 2021", a["headline"])

    def test_the_high(self):
        a = self.ask("when was ground beef most expensive?")
        self.assertIn("is at its high in records on file back to January 2019: $6.92", a["headline"])
        with mock.patch.object(preview, "_series_rows", lambda *x: (months((2024, 1), [3.0, 8.0, 5.0]), SRC, BEEF, "$", False)):
            m._READ_CACHE.clear()
            a = self.ask("when was ground beef most expensive?")
        self.assertIn("the high in records on file back to January 2024 was $8.00 a pound in February 2024. It is now $5.00", a["headline"])

    def test_last_time_past_a_level(self):
        a = self.ask("when was ground beef last under $6?")
        self.assertIn("was last below $6.00 in December 2022 ($5.00)", a["headline"])

    def test_a_word_nothing_reads_is_named(self):
        a = self.ask("How much has ground beef gone up under Biden?")
        self.assertIn("“biden”", a["lead"])
        self.assertEqual(self.ask("How much has ground beef gone up?")["lead"], "")

    def test_a_stopped_series_says_so(self):
        with mock.patch.object(preview, "_series_rows", lambda *a: (months((2015, 1), [1.31] * 30), SRC, "Apples, Red Delicious, per lb. (453.6 gm)", "$", False)):
            a = m.series_answer("us-avg-prices", "APU0000711111", "price of apples", "apples")
        self.assertIn("stopped publishing", a["lead"])

    def test_missing_earlier_reading_is_said_not_invented(self):
        a = self.ask("How much has ground beef risen since 1990?")
        self.assertIn("January 2019", a["headline"])


class Follow(unittest.TestCase):
    def setUp(self):
        m._READ_CACHE.clear()
        self.addCleanup(m._READ_CACHE.clear)
        for name, new in (("_series_rows", beef_rows), ("_dataset_name", lambda ds: ("US Average Prices", "us-prices.html")), ("_is_macro", lambda q: False)):
            p = mock.patch.object(preview, name, new)
            p.start()
            self.addCleanup(p.stop)

    def test_a_follow_up_on_the_same_item(self):
        self.assertIn("a year earlier", m.answer("how much has it gone up?", "price of beef", "prices")["headline"])

    def test_a_maturity_on_the_treasury_page_is_a_yield_whatever_came_before(self):
        self.assertIsNotNone(m.answer("the 30 year", "bills", "treasury"))

    def test_a_maturity_after_a_company_question_is_not_a_yield(self):
        self.assertIsNone(m.answer("the 5 year", "Apple revenue"))

    def test_page_context_makes_a_short_question_about_the_page(self):
        with mock.patch.object(m, "_rest_names_company", lambda *a: False):
            self.assertIn("up", m.answer("how much has it risen this year?", "", "treasury")["headline"])
        self.assertIsNone(m.answer("how much has it risen this year?", "", ""))


def year(ticker, end, ceo, pay, tsr=100.0, sole=True, co=False):
    return {"ticker": ticker, "name": ticker + " Inc", "fiscal_year_end": end, "ceo": ceo, "sole_ceo": sole, "co_ceos": co,
            "total_pay": pay, "actually_paid": pay, "tsr": tsr}


YEARS = [year("AAA", "2025-12-31", "Ann Lee", 30e6, 90.0), year("AAA", "2024-12-31", "Ann Lee", 10e6, 100.0),
         year("BBB", "2025-12-31", "Bo Ray", 25e6, 120.0), year("BBB", "2024-12-31", "Bo Ray", 20e6, 100.0),
         year("CCC", "2025-12-31", "New Boss", 90e6), year("CCC", "2024-12-31", "Old Boss", 10e6),
         year("DDD", "2025-12-31", "Dee Fox", 5e6, 80.0), year("DDD", "2024-12-31", "Dee Fox", 15e6, 100.0),
         year("EEE", "2025-12-31", "Token Ceo", 9e6), year("EEE", "2024-12-31", "Token Ceo", 1.0),
         year("FFF", "2025-12-31", "A / B", None, co=True, sole=False), year("FFF", "2024-12-31", "A / B", None, co=True, sole=False)]
SRC_PAY = {"name": "Proxy statements", "credit": "x"}
CALC = {"ceo_total_pay_usd": "t"}


class Raises(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(sr, "_pay_years", lambda cur: YEARS)
        p.start()
        self.addCleanup(p.stop)

    def changes(self, q):
        return sr._pay_changes(None, q, SRC_PAY, CALC, {})

    def test_biggest_raise_is_the_same_person_in_both_years(self):
        a = self.changes("Which CEOs got the biggest raises?")
        self.assertIn("Ann Lee of AAA, +$20.0mn", a["headline"])
        tickers = [r[2] for r in a["table"]["rows"]]
        self.assertNotIn("CCC", tickers)      # a new CEO's pay against the last one's is not a raise
        self.assertNotIn("EEE", tickers)      # a token $1 the year before is no base
        self.assertNotIn("FFF", tickers)      # co-CEOs

    def test_a_cut(self):
        self.assertIn("Dee Fox", self.changes("biggest pay cuts")["headline"])

    def test_a_raise_while_the_stock_fell_is_a_raise(self):
        a = self.changes("Which CEO pay rose while the stock fell?")
        self.assertIn("Ann Lee", a["headline"])
        self.assertIn("whose shares fell", a["headline"])
        self.assertEqual([r[2] for r in a["table"]["rows"]], ["AAA"])

    def test_words_the_list_does_not_read_are_noticed(self):
        self.assertEqual(sr.pay_unread("Which CEOs got the biggest raises?"), [])
        self.assertEqual(sr.pay_unread("Who is the highest paid CEO?"), [])
        self.assertIn("bank", sr.pay_unread("highest paid bank CEOs"))
        self.assertTrue(sr.pay_unread("average CEO raise"))


if __name__ == "__main__":
    unittest.main()
