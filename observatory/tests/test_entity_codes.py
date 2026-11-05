# -*- coding: utf-8 -*-
"""A delisted company keeps the securities code it last filed under.

2026-11-05: EDINET's code list blanks the 証券コード of a company it marks 非上場
(Hisamitsu 4530, Fujitec 6406, Toyota Industries 6201 after their
take-privates), so the holdings matched to it carried no code and its page
showed no holders. extract.fill_entity_codes copies the code off the
company's own last annual report, never one another company carries now, and
the company page says it is delisted. No network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_entity_codes
"""
import datetime
import os
import sys
import unittest
from unittest import mock

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import extract  # noqa: E402
from app import equity_api, listing  # noqa: E402

D = datetime.date


def _db():
    con = duckdb.connect(":memory:")
    con.execute(extract.SCHEMA_SQL)
    con.executemany("INSERT INTO eq_entities VALUES (?,?,?,?,?,?)", [
        ("E02144", "7203", u"トヨタ自動車株式会社", None, u"輸送用機器", True),
        ("E00944", None, u"久光製薬株式会社", None, u"医薬品", False),
        # an old company whose code now belongs to someone else
        ("E09999", None, u"旧社株式会社", None, None, False),
        # two delisted companies that last filed under the same code
        ("E08881", None, u"甲株式会社", None, None, False),
        ("E08882", None, u"乙株式会社", None, None, False),
    ])
    filings = [
        ("T1", "E02144", "7203", u"トヨタ自動車株式会社", D(2026, 3, 31), D(2026, 6, 20)),
        ("H1", "E00944", "4530", u"久光製薬株式会社", D(2025, 2, 28), D(2025, 5, 23)),
        # filed after delisting: EDINET sends no code, the earlier one stands
        ("H2", "E00944", None, u"久光製薬株式会社", D(2026, 2, 28), D(2026, 5, 20)),
        ("O1", "E09999", "7203", u"旧社株式会社", D(2021, 3, 31), D(2021, 6, 30)),
        ("A1", "E08881", "8888", u"甲株式会社", D(2022, 3, 31), D(2022, 6, 30)),
        ("B1", "E08882", "8888", u"乙株式会社", D(2025, 3, 31), D(2025, 6, 30)),
    ]
    for doc, ec, sc, nm, pe, fd in filings:
        con.execute("INSERT INTO eq_filings (doc_id, edinet_code, sec_code, filer_name, "
                    "period_end, filed_date, status, issued_shares) "
                    "VALUES (?,?,?,?,?,?,'clean',1000000)", [doc, ec, sc, nm, pe, fd])
    con.executemany(
        "INSERT INTO eq_holdings (doc_id, holder_table, row_no, held_name_raw, held_edinet_code, held_sec_code, match_status, shares, book_value_yen, prior_shares, prior_book_value_yen, purpose_ja, reciprocal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("T1", "reporting", "1", u"久光製薬株式会社", "E00944", None, "matched",
             1000, 9600000000, 1000, 9000000000, None, None),
            ("T1", "reporting", "2", u"旧社株式会社", "E09999", None, "matched",
             10, 1000, None, None, None, None),
            ("T1", "reporting", "3", u"甲株式会社", "E08881", None, "matched",
             10, 1000, None, None, None, None),
            ("H1", "reporting", "1", u"トヨタ自動車株式会社", "E02144", "7203", "matched",
             5, 50000, None, None, None, None),
        ])
    con.execute("INSERT INTO eq_reclassified VALUES "
                "('T1','reporting','9','to_pure',?, 'E00944', NULL, 'matched', "
                "10, 100, NULL, NULL)", [u"久光製薬株式会社"])
    return con


class FillCodes(unittest.TestCase):
    def test_fills_last_filed_code_and_keeps_listed_false(self):
        con = _db()
        filled, clashes = extract.fill_entity_codes(con, verbose=False)
        self.assertEqual(filled, 1)
        self.assertEqual(con.execute(
            "SELECT sec_code, listed FROM eq_entities WHERE edinet_code = 'E00944'"
        ).fetchone(), ("4530", False))
        self.assertEqual(con.execute(
            "SELECT held_sec_code FROM eq_holdings WHERE row_no = '1' AND doc_id = 'T1'"
        ).fetchone()[0], "4530")
        self.assertEqual(con.execute(
            "SELECT held_sec_code FROM eq_reclassified").fetchone()[0], "4530")

    def test_never_takes_a_code_another_company_carries(self):
        con = _db()
        _, clashes = extract.fill_entity_codes(con, verbose=False)
        why = {c[0]: c[3] for c in clashes}
        self.assertIn("E09999", why)
        self.assertIn("E02144", why["E09999"])
        # two delisted companies on one code: neither gets it
        self.assertIn("E08881", why)
        self.assertIn("E08882", why)
        left = con.execute(
            "SELECT count(*) FROM eq_entities WHERE edinet_code IN "
            "('E09999','E08881','E08882') AND sec_code IS NOT NULL").fetchone()[0]
        self.assertEqual(left, 0)
        # their holdings stay without a code rather than join Toyota's page
        self.assertEqual(con.execute(
            "SELECT count(*) FROM eq_holdings WHERE held_sec_code = '7203'"
        ).fetchone()[0], 1)

    def test_rerun_changes_nothing(self):
        con = _db()
        extract.fill_entity_codes(con, verbose=False)
        before = con.execute("SELECT * FROM eq_holdings ORDER BY doc_id, row_no").fetchall()
        filled, _ = extract.fill_entity_codes(con, verbose=False)
        self.assertEqual(filled, 0)
        self.assertEqual(before, con.execute(
            "SELECT * FROM eq_holdings ORDER BY doc_id, row_no").fetchall())

    def test_figures_untouched(self):
        con = _db()
        cols = "doc_id, row_no, shares, book_value_yen, prior_shares, prior_book_value_yen"
        before = con.execute("SELECT %s FROM eq_holdings ORDER BY 1, 2" % cols).fetchall()
        extract.fill_entity_codes(con, verbose=False)
        self.assertEqual(before, con.execute(
            "SELECT %s FROM eq_holdings ORDER BY 1, 2" % cols).fetchall())


class CompanyPage(unittest.TestCase):
    """The API finds the delisted company, its holders, and says it is gone."""

    def _company(self, code, listed_now):
        con = _db()
        extract.fill_entity_codes(con, verbose=False)
        with mock.patch.object(equity_api, "_cur", side_effect=lambda: con.cursor()), \
                mock.patch.object(listing, "_listed_now", return_value=listed_now):
            return equity_api.company(code)

    def test_delisted_company_has_entity_and_holders(self):
        d = self._company("4530", {"7203"})
        self.assertEqual(d["entity"]["sec_code"], "4530")
        self.assertFalse(d["entity"]["listed"])
        self.assertTrue(d["entity"]["delisted"])
        self.assertEqual([h["holder_sec_code"] for h in d["holders"]], ["7203"])
        self.assertEqual(d["holders"][0]["book_value_yen"], 9600000000)

    def test_listed_company_not_marked(self):
        d = self._company("7203", {"7203"})
        self.assertTrue(d["entity"]["listed"])
        self.assertNotIn("delisted", d["entity"])

    def test_unknown_listing_status_marks_nobody(self):
        d = self._company("4530", None)
        self.assertNotIn("delisted", d["entity"])


if __name__ == "__main__":
    unittest.main()
