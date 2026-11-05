# -*- coding: utf-8 -*-
"""A filing's tagged policy total that its own filing rules out.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_policy_total_plausibility
The rule tests need no data; the endpoint checks skip without data/equity.duckdb.
"""
import unittest

from app import equity_api, plausibility
from tests import _data

BN = 1000000000


class RuleTest(unittest.TestCase):
    def test_total_above_total_assets_is_flagged(self):
        # ソフト99 FY2026 as filed: ¥3.46trn total, ¥3.46bn named, ¥67bn assets
        why = plausibility.policy_total_reason(
            3455541000000, 3455541000000, 3455534000, 67008141000)
        self.assertIsNotNone(why)
        self.assertIn("total assets", why)

    def test_no_named_rows_still_caught_by_assets(self):
        # アビスト FY2025: names no rows, tags ¥1.19trn against ¥9.8bn assets
        self.assertIsNotNone(plausibility.policy_total_reason(
            1188234000000, 1188234000000, None, 9769086000))

    def test_thousandfold_named_gap_is_flagged_below_assets(self):
        # a ×1,000 slip that still fits under total assets
        why = plausibility.policy_total_reason(50 * BN, 50 * BN, 50000000, 100 * BN)
        self.assertIsNotNone(why)
        self.assertIn("1,000 times", why)

    def test_small_total_beside_understated_rows_is_not_flagged(self):
        # 光陽社 FY2026: the rows are wrong (¥3,000 for 11,532 shares), the
        # ¥11m total is right — so the named gap alone must not flag it
        self.assertIsNone(plausibility.policy_total_reason(
            11233000, 4714000, 3000, 3002176000))

    def test_holder_above_its_equity_is_not_flagged(self):
        # shaped like 日本証券金融 FY2024: 12.6x equity, 13% of assets — real
        self.assertIsNone(plausibility.policy_total_reason(
            1819600000000, 1800000000000, 1700000000000, 14000000000000))

    def test_missing_denominator_is_not_a_verdict(self):
        self.assertIsNone(plausibility.policy_total_reason(10 * BN, 10 * BN, 1, None))
        self.assertIsNone(plausibility.policy_total_reason(None, None, None, 10 * BN))

    def test_flag_nulls_the_ratios_never_zero(self):
        row = {"policy_total_yen": 3455541000000, "listed_yen": 3455541000000,
               "named_yen": 3455534000, "total_assets_yen": 67008141000,
               "pct_of_equity": 5872.3, "pct_of_assets": 5156.9}
        equity_api.check_policy_total(row)
        self.assertTrue(row["total_implausible"])
        self.assertIsNone(row["pct_of_equity"])
        self.assertIsNone(row["pct_of_assets"])
        self.assertEqual(row["policy_total_yen"], 3455541000000)

    def test_clean_row_is_untouched(self):
        row = {"policy_total_yen": 3309349000000, "listed_yen": 3237498000000,
               "named_yen": 3237499000000, "total_assets_yen": 105522331000000,
               "pct_of_equity": 8.29, "pct_of_assets": 3.14}
        equity_api.check_policy_total(row)
        self.assertFalse(row["total_implausible"])
        self.assertIsNone(row["total_implausible_reason"])
        self.assertEqual(row["pct_of_equity"], 8.29)


@unittest.skipUnless(_data.EQUITY, _data.NO_EQUITY)
class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.cur = equity_api._cur()

    def test_soft99_total_as_filed_flagged_and_excluded(self):
        d = equity_api.company("4464")
        s = d["scale"]
        if s is None:
            self.skipTest("no 4464 filing in this database")
        self.assertTrue(s["total_implausible"])
        self.assertGreater(s["policy_total_yen"], s["total_assets_yen"])
        self.assertIsNone(s["pct_of_equity"])
        self.assertIsNone(s["pct_of_assets"])
        self.assertGreaterEqual(d["excluded_totals"], 1)
        for p in d["scale_history"]:
            if p["total_implausible"]:
                self.assertIsNone(p["pct_of_equity"])

    def test_large_honest_holders_are_not_flagged(self):
        for code in ("8306", "8316", "7203", "8725"):
            d = equity_api.company(code)
            self.assertEqual(d["excluded_totals"], 0, code)
            if d["scale"]:
                self.assertFalse(d["scale"]["total_implausible"], code)
                self.assertIsNotNone(d["scale"]["pct_of_equity"], code)


if __name__ == "__main__":
    unittest.main()
