# -*- coding: utf-8 -*-
"""Cash position: liquid cash, net cash and investment securities from the
face of the balance sheet (app/fin_metrics.cash_position).

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_cash_position
"""
import unittest

from app.fin_metrics import cash_position, other_debt_line

BN = 1e9


class CashPositionTest(unittest.TestCase):

    def test_japan_gaap_adds_short_term_securities(self):
        # Keyence-shaped: deposits plus securities, no borrowing line at all.
        out, notes = cash_position({"jppfs_cor:CashAndDeposits": 597 * BN,
                                    "jppfs_cor:ShortTermInvestmentSecurities": 897 * BN,
                                    "jppfs_cor:InvestmentSecurities": 1514 * BN}, False)
        self.assertEqual(out["liquid_cash"][0], 1494 * BN)
        self.assertEqual(out["interest_bearing_debt"][0], 0)
        self.assertEqual(out["investment_securities"][0], 1514 * BN)
        self.assertEqual(notes, {})

    def test_debt_lines_summed_and_leases_left_out(self):
        out, _ = cash_position({"jppfs_cor:CashAndDeposits": 100 * BN,
                                "jppfs_cor:ShortTermLoansPayable": 10 * BN,
                                "jppfs_cor:BondsPayable": 30 * BN,
                                "jppfs_cor:LeaseObligationsNCL": 50 * BN}, False)
        self.assertEqual(out["interest_bearing_debt"][0], 40 * BN)

    def test_ifrs_uses_cash_equivalents_only(self):
        out, _ = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 50 * BN,
                                "jpigp_cor:OtherFinancialAssetsCAIFRS": 20 * BN,
                                "jpigp_cor:BorrowingsCLIFRS": 5 * BN,
                                "jpigp_cor:LeaseLiabilitiesCLIFRS": 9 * BN}, False)
        self.assertEqual(out["liquid_cash"][0], 50 * BN)
        self.assertEqual(out["interest_bearing_debt"][0], 5 * BN)
        self.assertNotIn("investment_securities", out)

    def test_filer_named_borrowing_counts_as_debt(self):
        # Sony-shaped: its own element for the current portion of long-term debt.
        el = "jpcrp030000-asr_E01777-000:CurrentPortionOfLongTermDebtCLIFRS"
        self.assertEqual(other_debt_line(el), "debt")
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 50 * BN,
                                    "jpigp_cor:BorrowingsCLIFRS": 5 * BN, el: 7 * BN}, False)
        self.assertEqual(out["interest_bearing_debt"][0], 12 * BN)
        self.assertEqual(notes, {})

    def test_borrowings_mixed_with_leases_withholds_net_cash(self):
        el = "jpcrp030000-asr_E01264-000:BondsBorrowingsAndLeaseLiabilitiesCLIFRS"
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 50 * BN, el: 5 * BN}, False)
        self.assertNotIn("interest_bearing_debt", out)
        self.assertIn("net_cash_withheld", notes)

    def test_ifrs_interest_bearing_without_lease_line_withheld(self):
        face = {"jpigp_cor:CashAndCashEquivalentsIFRS": 50 * BN,
                "jpigp_cor:InterestBearingLiabilitiesCLIFRS": 5 * BN}
        self.assertIn("net_cash_withheld", cash_position(face, False)[1])
        face["jpigp_cor:LeaseLiabilitiesCLIFRS"] = 3 * BN
        out, notes = cash_position(face, False)
        self.assertEqual(out["interest_bearing_debt"][0], 5 * BN)
        self.assertEqual(notes, {})

    def test_financing_liabilities_count_as_debt(self):
        # Honda-shaped: the finance arm's borrowing on the filer's own line.
        cl = "jpcrp030000-asr_E02166-000:FinancingLiabilitiesCLIFRS"
        ncl = "jpcrp030000-asr_E02166-000:FinancingLiabilitiesNCLIFRS"
        self.assertEqual(other_debt_line(cl), "debt")
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 5067 * BN,
                                    cl: 4000 * BN, ncl: 6000 * BN,
                                    "jpigp_cor:OtherFinancialLiabilitiesCLIFRS": 300 * BN}, False)
        self.assertEqual(out["interest_bearing_debt"][0], 10000 * BN)
        self.assertEqual(notes, {})

    def test_ifrs_without_borrowing_line_but_financial_liabilities_withheld(self):
        # Fast Retailing-shaped: bonds inside other financial liabilities, leases on their own.
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 893 * BN,
                                    "jpigp_cor:OtherFinancialLiabilitiesCLIFRS": 151 * BN,
                                    "jpigp_cor:LeaseLiabilitiesCLIFRS": 127 * BN}, False)
        self.assertNotIn("interest_bearing_debt", out)
        self.assertIn("OtherFinancialLiabilitiesCLIFRS", notes["net_cash_withheld_lines"])
        # Santen-shaped: a bare "financial liabilities" line
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 50 * BN,
                                    "jpigp_cor:FinancialLiabilitiesNCLIFRS": 31 * BN}, False)
        self.assertIn("net_cash_withheld", notes)

    def test_repaid_borrowing_line_keeps_net_cash(self):
        # Advantest-shaped: the borrowing line is printed with no value this year.
        out, notes = cash_position({"jpigp_cor:CashAndCashEquivalentsIFRS": 400 * BN,
                                    "jpigp_cor:BorrowingsCLIFRS": None,
                                    "jpigp_cor:OtherFinancialLiabilitiesCLIFRS": 12 * BN}, False)
        self.assertEqual(out["interest_bearing_debt"][0], 0)
        self.assertEqual(notes, {})

    def test_japan_gaap_without_borrowing_line_is_still_debt_free(self):
        out, notes = cash_position({"jppfs_cor:CashAndDeposits": 100 * BN,
                                    "jppfs_cor:AccountsPayableOther": 5 * BN}, False)
        self.assertEqual(out["interest_bearing_debt"][0], 0)
        self.assertEqual(notes, {})

    def test_assets_and_provisions_are_not_debt(self):
        for el in ("x:LoansReceivableCAIFRS", "x:OperatingLoansRealEstateBusinessCA",
                   "jppfs_cor:BondIssuanceCostDA", "x:AllowanceForDoubtfulLoansNCL"):
            self.assertIsNone(other_debt_line(el), el)

    def test_financials_get_nothing(self):
        out, notes = cash_position({"jppfs_cor:CashAndDeposits": 100 * BN}, True)
        self.assertEqual(out, {})
        self.assertIn("cash_position_withheld", notes)

    def test_no_balance_sheet_is_missing_not_zero(self):
        self.assertEqual(cash_position({}, False), ({}, {}))


if __name__ == "__main__":
    unittest.main()
