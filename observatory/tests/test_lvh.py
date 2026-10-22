# -*- coding: utf-8 -*-
u"""5% filings (大量保有報告書): reading the proposal answer, the issuer code,
the re-read of stored rows, and the holder endpoint's paging and figures.

Self-contained: the parser tests run on strings and the API tests build a
small database of their own, so nothing here needs the archive or the equity
file.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_lvh
"""
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

import duckdb
from fastapi import HTTPException

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "equity"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lvh = _load("lvh_extract", ROOT / "equity" / "lvh_extract.py")

from app import filer_labels, lvh_api  # noqa: E402


class StanceTest(unittest.TestCase):
    def test_none_in_its_many_wordings(self):
        for text in (u"該当事項なし", u"特にありません。", u"当該事項はございません。",
                     u"該等事項はありません｡", u"記載事項なし", u"該当時効無し",
                     u"現時点において、重要提案行為等を行う予定はありません。",
                     u"令和6年8月30日をもって共同保有者から外れたため、該当事項はありません。",
                     u"該当事項は有りません。", u"重要提案行為はありません。"):
            self.assertEqual(lvh.proposal_stance(text), "none_planned", text)

    def test_a_stated_or_reserved_act(self):
        for text in (u"重要提案行為等を行う可能性がある。", u"重要提案行為等を行う可能性　有",
                     u"取締役2名の指名提案", u"該当あり",
                     u"代表取締役の選解任および役員の構成の重大な変更",
                     u"重要提案行為等を行う予定はないが、状況により行う可能性がある"):
            self.assertEqual(lvh.proposal_stance(text), "will_or_may", text)

    def test_text_that_does_not_answer_is_unclear_not_a_campaign(self):
        for text in (u"ロックアップ契約2023年9月12日から2023年12月10日までの期間中、"
                     u"野村証券株式会社の事前の書面の同意なしには売買等を行わない旨を合意しております。",
                     u"2023年９月20日にみずほ銀行と株式数630,000株について担保契約を締結しました。",
                     u"純投資", u"現時点において未定。"):
            self.assertEqual(lvh.proposal_stance(text), "unclear", text)
        self.assertIsNone(lvh.proposal_stance(""))

    def test_see_purpose_reads_the_purpose(self):
        see = u"上記（２）保有目的に記載のとおり。"
        self.assertEqual(lvh.proposal_stance(see, u"純投資及び状況に応じて重要提案行為等を行うこと"),
                         "will_or_may")
        self.assertEqual(lvh.proposal_stance(see, u"純投資"), "unclear")

    def test_purpose_reading_respects_denials(self):
        self.assertTrue(lvh.purpose_reserves_proposal(
            u"投資及び状況に応じて経営陣への助言、重要提案行為等を行うこと"))
        self.assertFalse(lvh.purpose_reserves_proposal(
            u"経営の安定を図る安定株主として保有しており、重要提案行為等を行う予定はありません。"))
        self.assertTrue(lvh.purpose_reserves_proposal(
            u"現時点で具体的な重要提案行為等の予定はありませんが、今後の業績や対話の状況等に応じて、"
            u"株主還元や役員選任などの提案を検討する"))
        self.assertFalse(lvh.purpose_reserves_proposal(
            u"発行者、提出者若しくは他の株主が提案した議案に対して、反対票を投ずることもある。"))
        self.assertIsNone(lvh.purpose_reserves_proposal(""))


class IssuerCodeTest(unittest.TestCase):
    def test_letters_are_kept(self):
        self.assertEqual(lvh.issuer_code_of("130A"), "130A")
        self.assertEqual(lvh.issuer_code_of(u"４１６Ａ0"), "416A")
        self.assertEqual(lvh.issuer_code_of("72030"), "7203")
        self.assertEqual(lvh.issuer_code_of("No.7203"), "7203")
        self.assertIsNone(lvh.issuer_code_of(""))


class FilerTypeTest(unittest.TestCase):
    def test_insurance_agency_is_a_trade(self):
        self.assertEqual(filer_labels.type_of(
            u"飲食店向け厨房機器の販売、損害保険代理業、労働者派遣業")[0], "operating_company")
        self.assertEqual(filer_labels.type_of(u"保険代理業")[0], "operating_company")

    def test_bank_with_a_trust_licence_is_a_bank(self):
        resona = (u"１．預金又は定期預金の受入れ、資金の貸付け２．債務の保証３．国債の引受"
                  u"４．信託業務５．その他")
        self.assertEqual(filer_labels.type_of(resona)[0], "bank")
        self.assertEqual(filer_labels.type_of(u"信託業務・銀行業務")[0], "trust_bank")
        self.assertEqual(filer_labels.type_of(u"銀行業務、信託業務", name_ja=u"野村信託銀行株式会社")[0],
                         "trust_bank")

    def test_long_list_is_read_by_its_first_item(self):
        abc = u"".join(u"%d．事業%d " % (i, i) for i in range(1, 12)).replace(
            u"1．事業1", u"1．不動産の売買").replace(u"8．事業8", u"8．金融商品取引業")
        self.assertEqual(filer_labels.type_of(abc)[0], "operating_company")

    def test_fund_vehicle_and_manager(self):
        self.assertEqual(filer_labels.type_of(u"投資")[0], "investment_vehicle")
        self.assertEqual(filer_labels.type_of(u"投資マネージャー")[0], "asset_manager")

    def test_uncoded_key_joins_its_group(self):
        self.assertEqual(filer_labels.group_of(u"GFA株式会社"), filer_labels.group_of("E03740"))


def build(path, lvh1=False):
    con = duckdb.connect(path)
    con.execute(lvh.SCHEMA_SQL)
    con.execute("CREATE TABLE eq_filings (edinet_code VARCHAR, sec_code VARCHAR, "
                "filer_name_en VARCHAR, period_end DATE)")
    con.execute("CREATE TABLE eq_entities (edinet_code VARCHAR, sec_code VARCHAR, "
                "name_en VARCHAR)")

    def filing(doc, form, sec, req, filed, ratio):
        row = dict((c, None) for c in lvh.FILING_COLUMNS)
        row.update({"doc_id": doc, "form": form, "report_type": "change",
                    "issuer_sec_code": sec, "issuer_name_raw": u"発行者" + sec,
                    "requirement_date": req, "filed_date": filed, "ratio_pct": ratio,
                    "filer_edinet_code": "E1", "holders_rows": 2, "status": "clean",
                    "parser_version": "lvh-1"})
        con.execute(lvh.FILING_INSERT, [row[c] for c in lvh.FILING_COLUMNS])

    def holder(doc, no, code, name, answer, ratio):
        row = dict((c, None) for c in lvh.HOLDER_COLUMNS)
        row.update({"doc_id": doc, "holder_no": no, "holder_edinet_code": code,
                    "name_raw": name, "name_key": name, "important_proposal_ja": answer,
                    "ratio_pct": ratio, "important_proposal": bool(answer) or None})
        con.execute(lvh.HOLDER_INSERT, [row[c] for c in lvh.HOLDER_COLUMNS])

    filing("D1", "jplvh010000", "130A", "2026-01-02", "2026-01-05", 12.17)
    holder("D1", 1, "E1", u"運用会社", u"特にありません。", 0.0)
    holder("D1", 2, "E2", u"ファンド", None, 12.17)
    filing("D2", "jplvh010000", "7203", "2027-01-01", "2026-02-05", 6.0)
    holder("D2", 1, "E1", u"運用会社", u"重要提案行為等を行う可能性がある。", 6.0)
    filing("D3", "jplvh010000", "7203", "2026-03-01", "2026-03-04", 7.0)
    holder("D3", 1, None, u"GFA株式会社", u"該当事項なし", 7.0)
    filing("D4", "jplvh030000", "6758", "1984-12-25", "2026-04-04", 5.5)
    holder("D4", 1, "E1", u"運用会社", None, 5.5)
    if lvh1:
        for t, cols in lvh.ADDED_COLUMNS.items():
            for c, _ in cols:
                if c in ("proposal_stance", "purpose_mentions_proposal"):
                    con.execute("ALTER TABLE %s DROP COLUMN %s" % (t, c))
    else:
        lvh.reclassify(con)
    con.close()


class ReclassifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "eq.duckdb")
        build(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stored_rows_are_reread(self):
        con = duckdb.connect(self.path)
        got = dict(((r[0], r[1]), r[2:]) for r in con.execute(
            "SELECT doc_id, holder_no, important_proposal, proposal_stance "
            "FROM eq_lvh_holders").fetchall())
        self.assertEqual(got[("D1", 1)], (False, "none_planned"))
        self.assertEqual(got[("D2", 1)], (True, "will_or_may"))
        special = dict(con.execute(
            "SELECT doc_id, is_special_form FROM eq_lvh_filings").fetchall())
        self.assertTrue(special["D4"])
        self.assertFalse(special["D1"])
        self.assertEqual(lvh.reclassify(con), (0, 0))      # idempotent
        con.close()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.cons = {}
        for name, old in (("new", False), ("old", True)):
            path = os.path.join(cls.tmp, name + ".duckdb")
            build(path, lvh1=old)
            cls.cons[name] = duckdb.connect(path, read_only=True)
        cls._cur = lvh_api._cur
        cls.use("new")

    @classmethod
    def use(cls, name):
        lvh_api._cur = lambda: cls.cons[name].cursor()

    @classmethod
    def tearDownClass(cls):
        lvh_api._cur = cls._cur
        for con in cls.cons.values():
            con.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.use("new")

    def test_holder_pages_through_the_whole_book(self):
        a = lvh_api.holder("E1", limit=1, offset=0)
        b = lvh_api.holder("E1", limit=1, offset=1)
        self.assertEqual(a["reports_total"], 3)
        self.assertTrue(a["truncated"])
        self.assertEqual(a["next_offset"], 1)
        self.assertNotEqual(a["reports"][0]["doc_id"], b["reports"][0]["doc_id"])
        docs = set()
        offset = 0
        while offset is not None:
            page = lvh_api.holder("E1", limit=1, offset=offset)
            docs |= {r["doc_id"] for r in page["reports"]}
            offset = page["next_offset"]
        self.assertEqual(docs, {"D1", "D2", "D4"})
        self.assertEqual(len(a["current"]), 3)       # over the book, not the page

    def test_group_and_holder_figures_are_separate(self):
        d = lvh_api.holder("E1", limit=10, offset=0)
        row = [r for r in d["reports"] if r["doc_id"] == "D1"][0]
        self.assertEqual(row["ratio_pct"], 12.17)
        self.assertEqual(row["holder_ratio_pct"], 0.0)
        self.assertTrue(row["is_primary_filer"])

    def test_holder_without_a_code_is_found_by_its_key(self):
        d = lvh_api.holder(u"GFA株式会社", limit=10, offset=0)
        self.assertEqual(d["reports_total"], 1)
        with self.assertRaises(HTTPException):
            lvh_api.holder("E999", limit=10, offset=0)

    def test_impossible_trigger_dates_never_bound_the_summary(self):
        s = lvh_api.summary()
        self.assertEqual(str(s["latest_trigger"]), "2026-03-01")
        self.assertEqual(str(s["earliest_trigger"]), "2026-01-02")
        self.assertEqual(s["trigger_dates_implausible"], 2)
        q = {r["doc_id"]: r["date_quality"] for r in lvh_api.recent(
            limit=10, offset=0, activist="", min_ratio=0, min_change=0,
            report_type="", since="", until="")["filings"]}
        self.assertEqual(q["D2"], "after_filing")
        self.assertEqual(q["D4"], "before_5pct_rule")

    def test_a_database_written_by_lvh1_still_serves(self):
        self.use("old")
        d = lvh_api.holder("E1", limit=10, offset=0)
        self.assertIsNone(d["reports"][0]["proposal_stance"])
        self.assertEqual(lvh_api.recent(limit=10, offset=0, activist="", min_ratio=0,
                                        min_change=0, report_type="", since="",
                                        until="")["total"], 4)


if __name__ == "__main__":
    unittest.main()
