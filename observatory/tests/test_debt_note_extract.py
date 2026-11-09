# -*- coding: utf-8 -*-
"""Borrowings read from the IFRS financial-liabilities note
(equity/debt_note_extract.py), on the layouts the 17 filers that print no
borrowing line actually use: Fast Retailing's bonds inside "interest-bearing
debt", NSK's table cut in two with a list of bond issues after it,
Globe-ing's total printed twice, BBS's note covering two balance-sheet
headings at once, and NS Solutions' financing reconciliation that lists
leases alone.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_debt_note_extract
"""
import html
import io
import os
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import debt_note_extract as dn   # noqa: E402

M = 1e6
OFL_CL, OFL_NCL = "jpigp_cor:OtherFinancialLiabilitiesCLIFRS", "jpigp_cor:OtherFinancialLiabilitiesNCLIFRS"
HEAD = ["", "前連結会計年度 (2025年3月31日)", "当連結会計年度 (2026年3月31日)"]


def _table(rows):
    return "<table>" + "".join(
        "<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>" for r in rows) + "</table>"


def _t1(blocks, recon=None, period_end="2026-03-31"):
    """A t1 package whose XBRL instance carries the given note blocks."""
    x = '<jpdei_cor:CurrentFiscalYearEndDateDEI contextRef="x">%s</jpdei_cor:CurrentFiscalYearEndDateDEI>' % period_end
    for i, b in enumerate(blocks):
        el = "jpigp_cor:NotesOtherFinancialLiabilitiesConsolidatedFinancialStatementsIFRSTextBlock"
        x += "<%s>%s</%s>" % (el, html.escape(b), el)
    if recon:
        el = "jpigp_cor:NotesChangesInLiabilitiesFromFinancingActivitiesConsolidatedFinancialStatementsIFRSTextBlock"
        x += "<%s>%s</%s>" % (el, html.escape(recon), el)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRL/PublicDoc/x.xbrl", x)
    return buf.getvalue()


class DebtNoteTest(unittest.TestCase):

    def test_interest_bearing_row_with_total_resplit(self):
        # Fast Retailing-shaped: one 合計, then current / non-current subtotals
        # tying to two face lines, one of them the filer's own element.
        ncl = "jpcrp030000-asr_E03217-000:NonCurrentLiabilitiesNCLIFRS"
        block = "(単位：百万円)" + _table([HEAD, ["その他の金融負債", "", ""],
                                          ["有利子負債(注)", "240,935", "211,328"],
                                          ["預り金", "73,669", "79,472"],
                                          ["預り敷金・保証金", "1,312", "1,212"],
                                          ["合計", "315,917", "292,013"],
                                          ["流動負債合計（その他の短期金融負債）", "104,770", "150,942"],
                                          ["非流動負債合計（長期金融負債）", "211,147", "141,071"]])
        got = dn.read_filing(_t1([block]), {OFL_CL: 150942 * M, ncl: 141071 * M,
                                            "jpigp_cor:LeaseLiabilitiesCLIFRS": 126830 * M})
        self.assertEqual(got["status"], "clean")
        self.assertEqual(got["debt_yen"], 211328 * M)
        self.assertEqual(set(got["tied_lines"].split(",")), {OFL_CL, ncl})

    def test_table_cut_in_two_and_bond_list_not_counted(self):
        # NSK-shaped: the non-current half has no header of its own, and the
        # list of bond issues after it repeats bonds already counted.
        head = ["区分", "前連結会計年度 (2025年3月31日)", "当連結会計年度 (2026年3月31日)", "平均利率 (%)", "返済期限"]
        part1 = _table([["(単位:百万円)"] * 5, head,
                        ["短期借入金", "71,850", "72,495", "2.7", "-"],
                        ["1年以内償還の社債", "25,000", "20,000", "", ""],
                        ["その他", "1,000", "8,481", "-", "-"],
                        ["その他の金融負債(流動)", "97,850", "100,976", "-", "-"]])
        part2 = _table([["長期借入金", "62,423", "66,972", "1.5", "2027年~2036年"],
                        ["社債", "128,000", "138,000", "(注2)", "(注2)"],
                        ["その他", "13,047", "14,136", "-", "-"],
                        ["金融負債(非流動)", "203,470", "219,108", "-", "-"]])
        bonds = _table([["(単位:百万円)"] * 5,
                        ["会社名", "銘柄", "前連結会計年度 (2025年3月31日)", "当連結会計年度 (2026年3月31日)", "利率"],
                        ["当社", "第60回無担保社債", "－", "30,000", "1.5"],
                        ["合計", "", "128,000", "138,000", ""]])
        got = dn.read_filing(_t1([part1 + part2 + bonds]), {OFL_CL: 100976 * M, OFL_NCL: 219108 * M})
        self.assertEqual(got["status"], "clean")
        self.assertEqual(got["debt_yen"], (72495 + 20000 + 66972 + 138000) * M)

    def test_total_printed_twice_counts_once(self):
        # Globe-ing-shaped: by category to 合計, then the same 合計 re-split.
        block = "(単位：千円)" + _table([HEAD, ["条件付対価に係る負債", "-", "69,226"], ["合計", "-", "69,226"],
                                         ["流動負債", "-", "-"], ["非流動負債", "-", "69,226"],
                                         ["合計", "-", "69,226"]])
        got = dn.read_filing(_t1([block]), {OFL_NCL: 69226000.0})
        self.assertEqual(got["status"], "clean")
        self.assertEqual(got["debt_yen"], 0)

    def test_total_equal_to_several_face_lines(self):
        # BBS-shaped: deposits, leases and a long-term payable in one table.
        block = "(単位:千円)" + _table([HEAD, ["預り金", "276,069", "351,442"],
                                        ["リース負債(流動)", "807,825", "612,708"],
                                        ["リース負債(非流動)", "1,883,508", "2,687,375"],
                                        ["長期未払金", "40,911", "18,430"],
                                        ["合計", "3,008,313", "3,669,955"]])
        face = {OFL_CL: 351442000.0, OFL_NCL: 18430000.0,
                "jpigp_cor:LeaseLiabilitiesCLIFRS": 612708000.0,
                "jpigp_cor:LeaseLiabilitiesNCLIFRS": 2687375000.0}
        got = dn.read_filing(_t1([block]), face)
        self.assertEqual(got["status"], "clean")
        self.assertEqual(got["debt_yen"], 0)
        self.assertEqual(set(got["tied_lines"].split(",")), set(face))

    def test_two_sections_each_with_a_total(self):
        # Nikon-shaped: current borrowings to 合計, then non-current to 合計.
        cl, ncl = "jpigp_cor:BondsAndBorrowingsCLIFRS", "jpigp_cor:BondsAndBorrowingsNCLIFRS"
        block = "(単位：百万円)" + _table([HEAD, ["流動", "", ""], ["短期借入金", "80,000", "81,691"],
                                          ["1年以内に返済予定の長期借入金", "10,000", "16,026"],
                                          ["合計", "90,000", "97,717"], ["非流動", "", ""],
                                          ["長期借入金", "100,000", "112,266"], ["社債", "9,970", "9,974"],
                                          ["合計", "109,970", "122,240"]])
        got = dn.read_filing(_t1([block]), {cl: 97717 * M, ncl: 122240 * M})
        self.assertEqual(got["status"], "clean")
        self.assertEqual(got["debt_yen"], (97717 + 122240) * M)
        self.assertEqual(set(got["tied_lines"].split(",")), {cl, ncl})

    def test_table_of_assets_never_ties(self):
        # Helios-shaped: collateral pledged equals the secured borrowing.
        block = "(単位：百万円)" + _table([HEAD, ["現金及び現金同等物", "450", "450"], ["合計", "450", "450"]])
        got = dn.read_filing(_t1([block]), {OFL_CL: 450 * M})
        self.assertEqual(got["tables_tied"], 0)

    def test_rows_that_do_not_add_up_are_not_trusted(self):
        # a parent row and its children both printed
        block = "(単位：百万円)" + _table([HEAD, ["借入金", "100", "100"], ["短期借入金", "60", "60"],
                                          ["長期借入金", "40", "40"], ["合計", "100", "100"]])
        got = dn.read_filing(_t1([block]), {OFL_CL: 100 * M})
        self.assertEqual(got["status"], "partial")
        self.assertIsNone(got["debt_yen"])

    def test_reconciliation_listing_leases_only_proves_no_borrowings(self):
        recon = "(単位:百万円)" + _table([["", "リース負債"], ["期首残高", "25,000"], ["期末残高", "25,149"]])
        got = dn.read_filing(_t1([], recon), {OFL_CL: 740 * M, OFL_NCL: 102 * M})
        self.assertEqual((got["status"], got["source"], got["debt_yen"]), ("clean", "reconciliation", 0.0))
        recon = _table([["", "借入金", "リース負債"], ["期末残高", "10", "25,149"]])
        self.assertNotEqual(dn.read_filing(_t1([], recon), {OFL_CL: 740 * M})["status"], "clean")

    def test_no_current_year_column_is_not_read(self):
        block = "(単位：百万円)" + _table([["", "2024年", "2025年"], ["短期借入金", "1", "2"], ["合計", "1", "2"]])
        got = dn.read_filing(_t1([block]), {OFL_CL: 2 * M})
        self.assertEqual(got["tables_read"], 0)
        self.assertNotEqual(got["status"], "clean")


if __name__ == "__main__":
    unittest.main()
