# -*- coding: utf-8 -*-
"""A renamed company is found under its old name, an ordinary word is not a
company, and a manager's answer counts a company once however many share
classes it holds (2026-11-02: "Equity Residential" answered with Armour
Residential; Berkshire's answer listed "Alphabet Inc" twice).

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_renamed_and_classes
"""
import datetime
import os
import re
import unittest

os.environ["US_ASK_MODEL"] = "0"

from app import sec_api, stock_recipes, us_link  # noqa: E402
from tests import _data  # noqa: E402


def _cur():
    if not _data.SEC:
        return None
    try:
        cur = sec_api._require()
        return cur if sec_api._has(cur, "sec_companies") and sec_api._has(cur, "sec_filings") else None
    except Exception:  # noqa: BLE001
        return None


D, P = datetime.date(2026, 6, 30), datetime.date(2026, 3, 31)


def line(cusip, name, value, shares, prev_value, prev_shares):
    return (cusip, name, value, shares, prev_value, prev_shares, D, P)


class ShareClasses(unittest.TestCase):
    def test_two_classes_are_one_company(self):
        got = stock_recipes._by_issuer([line("A", "APPLE INC", 90.0, 900.0, 80.0, 800.0),
                                        line("B", "ALPHABET INC", 60.0, 200.0, 50.0, 100.0),
                                        line("C", "ALPHABET INC", 50.0, 150.0, 40.0, 100.0)])
        self.assertEqual([r[1] for r in got], ["ALPHABET INC", "APPLE INC"])  # 110 together is the largest
        self.assertEqual(got[0][2:6], (110.0, 350.0, 90.0, 200.0))
        self.assertEqual(got[0][8], 2)

    def test_missing_stays_missing_not_zero(self):
        got = stock_recipes._by_issuer([line("A", "NEW CO", 10.0, 5.0, None, None), line("B", "GONE CO", None, None, 7.0, 3.0)])
        new = [r for r in got if r[1] == "NEW CO"][0]
        gone = [r for r in got if r[1] == "GONE CO"][0]
        self.assertIsNone(new[4])
        self.assertIsNone(gone[2])

    def test_the_words_for_a_change(self):
        fmt = lambda v: "{:,.0f}".format(v)  # noqa: E731
        self.assertEqual(stock_recipes._share_change(("x", "n", 1, 300.0, 1, 100.0, D, P, 2), fmt), "+200 shares across 2 share classes")
        self.assertEqual(stock_recipes._share_change(("x", "n", 1, 100.0, 1, 300.0, D, P, 1), fmt), "-200 shares")


@unittest.skipUnless(_cur(), "data/sec.duckdb not built")
class Names(unittest.TestCase):
    def tickers(self, q):
        return [c["ticker"] for c in us_link.find_companies(_cur(), q, limit=3)]

    def test_a_renamed_company_is_found_every_way(self):
        for q in ("Equity Residential revenue", "equity residential", "Who owns Equity Residential?", "Vivmark revenue", "VMRK", "EQR dividends"):
            self.assertEqual(self.tickers(q), ["VMRK"], q)

    def test_a_former_name_is_read_as_todays_everywhere(self):
        cur = _cur()
        self.assertEqual(us_link.current_names(cur, "Equity Residential's revenue"), "Vivmark Residential's revenue")
        self.assertEqual(us_link.current_names(cur, "shareholders' equity at Apple"), "shareholders' equity at Apple")
        out = stock_recipes.answer(cur, "How much does Equity Residential pay in dividends?", None, "")
        self.assertIn("Vivmark Residential", out["headline"])

    def test_funds_still_writing_the_old_name_are_matched(self):
        cur = _cur()
        if not sec_api._has(cur, "sec_13f_holdings"):
            self.skipTest("needs 13F holdings")
        out = stock_recipes.answer(cur, "Who owns Equity Residential?", None, "")
        self.assertIn("Vivmark Residential", out["headline"])

    def test_an_ordinary_word_is_not_a_company(self):
        self.assertNotIn("ARR", self.tickers("residential property companies"))
        self.assertEqual(self.tickers("Armour Residential"), ["ARR"])
        self.assertIn("APLE", self.tickers("Apple Hospitality REIT"))

    def test_familiar_names_still_resolve(self):
        for q, t in (("Ford", "F"), ("Apple", "AAPL"), ("who owns Disney", "DIS"), ("Berkshire", "BRK-B"), ("Nucor", "NUE")):
            self.assertIn(t, self.tickers(q), q)

    def test_the_answer_lists_alphabet_once(self):
        cur = _cur()
        if not sec_api._has(cur, "sec_13f_holdings"):
            self.skipTest("needs 13F holdings")
        out = stock_recipes.answer(cur, "What did Berkshire Hathaway buy last quarter?", None, "")
        text = out["headline"] + " " + (out.get("answer") or "")
        self.assertLessEqual(len(re.findall(r"Alphabet Inc \(", out["headline"])), 1, text)
        self.assertNotRegex(text, r"\b1 new positions\b")


if __name__ == "__main__":
    unittest.main()
