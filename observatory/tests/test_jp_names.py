# -*- coding: utf-8 -*-
"""Japanese English company names as people write them (app/jp_names.py)."""
import unittest

from app.jp_names import display_name


class DisplayNameTests(unittest.TestCase):

    def test_capitals_are_written_as_words(self):
        self.assertEqual(display_name("TOYOTA MOTOR CORPORATION"), "Toyota Motor Corporation")
        self.assertEqual(display_name("SONY GROUP CORPORATION"), "Sony Group Corporation")
        self.assertEqual(display_name("TOKYO ELECTRIC POWER COMPANY HOLDINGS, INCORPORATED"),
                         "Tokyo Electric Power Company Holdings, Incorporated")

    def test_full_width_letters_and_commas_are_tidied(self):
        self.assertEqual(display_name(u"ＯＳＡＫＡ　ＧＡＳ　ＣＯ.,　ＬＴＤ."), "Osaka Gas Co., Ltd.")
        self.assertEqual(display_name("DAIKIN INDUSTRIES,LTD."), "Daikin Industries, Ltd.")
        self.assertEqual(display_name(u"Stanley Electric Co.，Ltd."), "Stanley Electric Co., Ltd.")

    def test_abbreviations_and_brands_keep_their_capitals(self):
        self.assertEqual(display_name("NTT DATA GROUP CORPORATION"), "NTT Data Group Corporation")
        self.assertEqual(display_name("KDDI CORPORATION"), "KDDI Corporation")
        self.assertEqual(display_name("MS&AD Insurance Group Holdings, Inc."), "MS&AD Insurance Group Holdings, Inc.")
        self.assertEqual(display_name("HAKUHODO DY HOLDINGS INCORPORATED"), "Hakuhodo DY Holdings Incorporated")

    def test_mixed_case_is_kept(self):
        for n in ("Mitsubishi UFJ Financial Group, Inc.", "FUJIFILM Holdings Corporation", "Seven & i Holdings Co., Ltd.",
                  "SoftBank Group Corp.", "transcosmos inc.", "M3, Inc."):
            self.assertEqual(display_name(n), n)

    def test_joining_words_and_empty(self):
        self.assertEqual(display_name("THE BANK OF KYOTO, LTD."), "The Bank of Kyoto, Ltd.")
        self.assertIsNone(display_name(None))
        self.assertEqual(display_name(""), "")


if __name__ == "__main__":
    unittest.main()
