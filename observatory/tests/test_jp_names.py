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




class LowerCaseAndWidthTest(unittest.TestCase):
    """2026-11-03: the registry spells Suzuki in lower case, registers file
    custodians in full-width letters and ㈱ as one glyph."""

    def test_a_name_typed_in_lower_case_is_re_cased(self):
        self.assertEqual(display_name("suzuki motor corporation"), "Suzuki Motor Corporation")

    def test_mixed_case_is_left_alone(self):
        self.assertEqual(display_name("Nomura Securities Co.,Ltd."), "Nomura Securities Co., Ltd.")

    def test_japanese_widths_even_out(self):
        from app.jp_names import display_name_ja
        self.assertEqual(display_name_ja("㈱日本カストディ銀行"), "(株)日本カストディ銀行")
        self.assertEqual(display_name_ja("株式会社　ミスミグループ本社"), "株式会社 ミスミグループ本社")


if __name__ == "__main__":
    unittest.main()
