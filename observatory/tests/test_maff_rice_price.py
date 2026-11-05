# -*- coding: utf-8 -*-
"""Rice relative-transaction prices (app/adapters/maff_rice_price.py): a brand
MAFF adds in kana gets its series code from the kana themselves, a checked
BRANDS entry always wins, and a kanji name nobody has checked still stops the
ingest. 2026-11-04: three new brands held the dataset at August for a week."""
import unittest

from app.adapters import maff_rice_price as a


class KanaRomaji(unittest.TestCase):
    def test_hepburn(self):
        for kana, romaji in ((u"ひなたまる", "hinatamaru"), (u"あいちのこころ", "aichinokokoro"),
                             (u"コシヒカリ", "koshihikari"), (u"にじのきらめき", "nijinokirameki"),
                             (u"ちゅらひかり", "churahikari"), (u"きっこ", "kikko"),
                             (u"ゆめぴりか", "yumepirika"), (u"ミルキークイーン", "mirukiikuiin")):
            self.assertEqual(a.kana_romaji(kana), romaji)

    def test_kanji_or_brackets_are_not_romanised(self):
        self.assertIsNone(a.kana_romaji(u"つや姫"))
        self.assertIsNone(a.kana_romaji(u"くまさんの輝き"))
        self.assertIsNone(a.kana_romaji(u"こしひかり（一般）"))
        self.assertIsNone(a.kana_romaji(u""))


class BrandSlug(unittest.TestCase):
    def test_a_kana_brand_needs_no_entry(self):
        self.assertEqual(a.brand_slug_label(u"つきあかり"), ("tsukiakari", "Tsukiakari"))
        self.assertEqual(a.series_code("01", u"つきあかり", ""), "price.01.tsukiakari")

    def test_the_checked_entry_wins_over_the_kana(self):
        self.assertEqual(a.brand_slug_label(u"ミルキークイーン")[0], "milkyqueen")
        self.assertEqual(a.brand_slug_label(u"あいちのこころ"), ("aichinokokoro", "Aichi no Kokoro"))

    def test_every_kana_entry_added_since_the_rule_keeps_the_automatic_slug(self):
        for kana in (u"あいちのこころ", u"ひなたまる"):
            self.assertEqual(a.BRANDS[kana][0], a.kana_romaji(kana))

    def test_an_unchecked_kanji_brand_stops_the_ingest(self):
        with self.assertRaises(a.ValidationError):
            a.series_code("43", u"熊本の新しい米", "")
        self.assertEqual(a.series_code("43", u"くまさんの輝き", ""), "price.43.kumasannokagayaki")


if __name__ == "__main__":
    unittest.main()


class RememberedCropYears(unittest.TestCase):
    """Between 21 September and 4 November 2026 MAFF's pages dropped the 2009
    and 2025 tables; the copies this ingest archived stand in for them."""

    def test_the_newest_envelope_holding_a_year_wins(self):
        import base64
        import json
        import os
        import tempfile
        raw = tempfile.mkdtemp()
        older = {"crop_years": {"2009": {"url": "u2009", "b64": base64.b64encode(b"old-2009").decode()},
                                "2025": {"url": "u2025", "b64": base64.b64encode(b"old-2025").decode()}}}
        newer = {"crop_years": {"2025": {"url": "u2025b", "b64": base64.b64encode(b"new-2025").decode()},
                                "2026": {"url": "u2026", "b64": base64.b64encode(b"new-2026").decode()}}}
        for name, body in (("rice-prices-jp-20260921T130000Z-aaaaaaaaaaaa.json", older),
                           ("rice-prices-jp-20261029T130000Z-bbbbbbbbbbbb.json", newer),
                           ("rice-prices-jp-20261105T130000Z-cccccccccccc.json", {"crop_years": {"2026": {"url": "u", "b64": ""}}}),
                           ("other-dataset-20261105T130000Z-dddddddddddd.json", {"crop_years": {"2009": {"url": "x", "b64": "eA=="}}})):
            with open(os.path.join(raw, name), "w") as fh:
                json.dump(body, fh)
        got = a.remembered_crop_years(raw)
        self.assertEqual(sorted(got), [2009, 2025, 2026])
        self.assertEqual(got[2009]["url"], "u2009")
        self.assertEqual(base64.b64decode(got[2025]["b64"]), b"new-2025")

    def test_no_archive_means_nothing_remembered(self):
        self.assertEqual(a.remembered_crop_years("/nonexistent/dir"), {})
