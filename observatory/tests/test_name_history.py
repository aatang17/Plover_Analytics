# -*- coding: utf-8 -*-
"""A held company is found under the name it had when the holding was filed.

2026-11-05: held names were matched against today's EDINET code list only, so
a company that renamed itself went unmatched in every filing that used its old
name -- 日本電信電話㈱ (NTT, 9432) in all of 2022-2025, about ¥300bn of other
companies' holdings a year. The filers' own names over time (eq_filings) are
now a second index, used within a year either side of when each name was
filed under; a name two companies filed under at the same time matches
neither, and `--rematch` re-resolves stored rows without touching a filed
figure. No network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_name_history
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import extract  # noqa: E402

D = datetime.date


def registry_row(code, sec, name, listing=u"上場"):
    return {u"ＥＤＩＮＥＴコード": code, u"証券コード": sec + "0" if sec else "",
            u"上場区分": listing, u"提出者名": name}


# Today's registry: NTT under its new name only; ヤマト twice, unrelated.
CODELIST = [
    registry_row("E04430", "9432", u"ＮＴＴ株式会社"),
    registry_row("E00001", "1111", u"株式会社ソニック"),
    registry_row("E00002", "2222", u"テスト工業株式会社"),
]

# (doc_id, edinet_code, sec_code, filer_name, period_end)
FILINGS = [
    ("N22", "E04430", "9432", u"日本電信電話株式会社", D(2022, 3, 31)),
    ("N23", "E04430", "9432", u"日本電信電話株式会社", D(2023, 3, 31)),
    ("N24", "E04430", "9432", u"日本電信電話株式会社", D(2024, 3, 31)),
    ("N25", "E04430", "9432", u"日本電信電話株式会社", D(2025, 3, 31)),
    ("N26", "E04430", "9432", u"ＮＴＴ株式会社", D(2026, 3, 31)),
    # Two companies filed as 株式会社アルファ in the same years: no match.
    ("A1", "E00010", "3434", u"株式会社アルファ", D(2023, 3, 31)),
    ("A2", "E00011", "4760", u"株式会社アルファ", D(2023, 6, 30)),
    # One company filed as 旧名工業 until 2021 only.
    ("O21", "E00002", "2222", u"旧名工業株式会社", D(2021, 3, 31)),
    ("O22", "E00002", "2222", u"テスト工業株式会社", D(2022, 3, 31)),
    # The holder.
    ("H22", "E09999", "9999", u"保有者株式会社", D(2022, 3, 31)),
    ("H24", "E09999", "9999", u"保有者株式会社", D(2024, 3, 31)),
]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "eq.duckdb")
        con = duckdb.connect(self.db)
        con.execute(extract.SCHEMA_SQL)
        con.executemany(
            "INSERT INTO eq_filings (doc_id, edinet_code, sec_code, filer_name, "
            "period_end) VALUES (?,?,?,?,?)", FILINGS)
        con.executemany(
            "INSERT INTO eq_entities VALUES (?,?,?,NULL,NULL,?)",
            [("E04430", "9432", u"ＮＴＴ株式会社", True),
             ("E00010", "3434", u"株式会社アルファ", True),
             ("E00011", "4760", u"株式会社アルファ", True),
             ("E00002", "2222", u"テスト工業株式会社", True)])
        holdings = [
            ("H22", "reporting", "1", u"日本電信電話㈱", None, None, "unmatched",
             1000, 5000, None, None, u"取引関係", u"有"),
            ("H24", "reporting", "1", u"日本電信電話株式会社（注）３", None, None,
             "unmatched", 1000, 6000, 900, 5500, u"取引関係", u"有"),
            ("H24", "reporting", "2", u"㈱アルファ", None, None, "unmatched",
             10, 20, None, None, None, None),
            # 旧名工業 was last filed under for 2021: in force for a 2022
            # holding, not for one in 2024.
            ("H22", "reporting", "3", u"旧名工業㈱", None, None, "unmatched",
             5, 7, None, None, None, None),
            ("H24", "reporting", "3", u"旧名工業㈱", None, None, "unmatched",
             5, 7, None, None, None, None),
            ("H24", "reporting", "4", u"ＮＴＴ㈱", "E04430", "9432", "matched",
             1, 2, None, None, None, None),
        ]
        con.executemany("INSERT INTO eq_holdings (doc_id, holder_table, row_no, held_name_raw, held_edinet_code, held_sec_code, match_status, shares, book_value_yen, prior_shares, prior_book_value_yen, purpose_ja, reciprocal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        holdings)
        con.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def rows(self):
        con = duckdb.connect(self.db, read_only=True)
        try:
            return con.execute(
                "SELECT doc_id, row_no, held_name_raw, held_edinet_code, held_sec_code, "
                "match_status, shares, book_value_yen, prior_shares, "
                "prior_book_value_yen, purpose_ja, reciprocal "
                "FROM eq_holdings ORDER BY doc_id, row_no").fetchall()
        finally:
            con.close()


class Rematch(Fixture):
    def test_old_name_matches_with_current_code(self):
        extract.rematch(self.db, CODELIST)
        got = {(r[0], r[1]): r[3:6] for r in self.rows()}
        self.assertEqual(got[("H22", "1")], ("E04430", "9432", "matched"))
        self.assertEqual(got[("H24", "1")], ("E04430", "9432", "matched"))
        self.assertEqual(got[("H24", "4")], ("E04430", "9432", "matched"))

    def test_two_companies_under_one_name_match_neither(self):
        changes = extract.rematch(self.db, CODELIST)
        got = {(r[0], r[1]): r[3:6] for r in self.rows()}
        self.assertEqual(got[("H24", "2")], (None, None, "unmatched"))
        self.assertIn((u"㈱アルファ", ["E00010", "E00011"]), changes["ambiguous"])

    def test_a_name_is_in_force_only_near_when_it_was_filed(self):
        extract.rematch(self.db, CODELIST)
        got = {(r[0], r[1]): r[3:6] for r in self.rows()}
        self.assertEqual(got[("H22", "3")], ("E00002", "2222", "matched"))
        self.assertEqual(got[("H24", "3")], (None, None, "unmatched"))

    def test_filed_figures_are_untouched(self):
        before = [r[:3] + r[6:] for r in self.rows()]
        extract.rematch(self.db, CODELIST)
        self.assertEqual([r[:3] + r[6:] for r in self.rows()], before)

    def test_rerun_changes_nothing(self):
        extract.rematch(self.db, CODELIST)
        once = self.rows()
        changes = extract.rematch(self.db, CODELIST)
        self.assertEqual(changes["eq_holdings"], [])
        self.assertEqual(self.rows(), once)

    def test_history_table_is_published(self):
        extract.rematch(self.db, CODELIST)
        con = duckdb.connect(self.db, read_only=True)
        try:
            got = con.execute(
                "SELECT name_ja, first_period, last_period FROM eq_name_history "
                "WHERE edinet_code = 'E04430' ORDER BY first_period").fetchall()
        finally:
            con.close()
        self.assertEqual(got, [(u"日本電信電話株式会社", D(2022, 3, 31), D(2025, 3, 31)),
                               (u"ＮＴＴ株式会社", D(2026, 3, 31), D(2026, 3, 31))])


class Resolver(unittest.TestCase):
    def test_registry_alone_behaves_as_before(self):
        r = extract.Resolver(extract.build_index(CODELIST))
        self.assertEqual(r(u"ＮＴＴ㈱", "2026-03-31"), ("matched", "E04430", "9432"))
        self.assertEqual(r(u"日本電信電話㈱", "2023-03-31")[0], "unmatched")

    def test_history_beats_todays_holder_of_the_name(self):
        # Today 株式会社ソニック is E00001; in 2023 the company filing under
        # that name was E00020. The holding names the company of its time.
        history = [("E00020", u"株式会社ソニック", D(2022, 3, 31), D(2024, 3, 31),
                    D(2021, 3, 30), D(2025, 3, 31), 3)]
        r = extract.Resolver(extract.build_index(CODELIST),
                             extract.build_history_index(history),
                             sec_codes={"E00020": "5555"})
        self.assertEqual(r(u"㈱ソニック", "2023-03-31"), ("matched", "E00020", "5555"))
        self.assertEqual(r(u"㈱ソニック", "2026-03-31"), ("matched", "E00001", "1111"))
        self.assertIn(u"㈱ソニック", r.conflicts)


if __name__ == "__main__":
    unittest.main()
