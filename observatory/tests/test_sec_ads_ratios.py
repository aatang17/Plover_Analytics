# -*- coding: utf-8 -*-
"""The depositary-share ratio loader (equity/sec_ads_ratios.py): the ratio
read from cover titles and filing text, in the wordings filers actually use,
and the rule that settles a company's listing."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sec_ads_ratios", str(ROOT / "equity" / "sec_ads_ratios.py"))
ads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ads)


class Numbers(unittest.TestCase):
    def test_words_digits_and_fractions(self):
        for text, want in (("eight", 8), ("13", 13), ("4,000", 4000), ("one-half", 0.5), ("0.5", 0.5), ("1/40", 0.025),
                           ("twenty-five", 25), ("two hundred", 200), ("one thousand", 1000), ("a", 1), ("ten", 10)):
            self.assertEqual(ads.number(text), want, text)
        self.assertIsNone(ads.number("several"))


class CoverTitles(unittest.TestCase):
    # wordings seen on real covers on 2026-10-19
    CASES = [
        ("American Depositary Shares, each representing eight Ordinary Shares, par value US$0.000003125 per share", 8),
        ("American Depositary Shares, each representing three Equity Shares, par value Re. 1 per share", 3),
        ("American Depositary Shares, each representing 13 Ordinary Shares, par value $0.0001 per share", 13),
        ("American Depositary Shares, each representing 4,000 ordinary shares, no par value", 4000),
        ("American Depositary Shares (one American depositary share representing four Class A ordinary shares)", 4),
        ("American Depositary Shares each representedby one Equity Share, par value ₹5/- per share", 1),
        ("American depositary shares, each of which represents one share of common stock", 1),
        ("American Depositary Receipts, each representing one B Share, nominal value DKK 0.10", 1),
        ("American depositary shares, each ADS represents 15 ordinary shares, par valueUS$0.001 per share", 15),
        ("American     Depository Shares, each representing 2,000 Ordinary Shares, par value $0.000000005 per share", 2000),
        ("American Depositary Shares, each representing one-half of one ordinary share", 0.5),
        ("American Depositary Shares, each representing twenty-five (25) ordinary shares", 25),
        ("American Depositary Shares, each representing four 25p ordinary shares", 4),
        ("American Depositary Shares(each representing one RELX PLC ordinary share)", 1),
        ("American Depositary Shares, each representing half a Class B Share, par value CHF 0.10 per share", 0.5),
        ("American Depositary Shares, each representingone common share", 1),
    ]

    def test_ratio_in_title(self):
        for title, want in self.CASES:
            self.assertEqual(ads.ratio_in_title(title), want, title)

    def test_no_ratio_and_not_a_share(self):
        self.assertIsNone(ads.ratio_in_title("American Depositary Shares"))
        # a unit is a bundle of shares (Coca-Cola FEMSA: 3 Series B + 5 Series L), not a share count
        self.assertIsNone(ads.ratio_in_title("American Depositary Shares, each representing 10 units"))
        self.assertIsNone(ads.ratio_in_title("Common Shares"))
        # a preferred-stock depositary share is not the common listing
        self.assertIsNone(ads.ratio_in_title("American Depositary Shares, each representing 1/40th of a share of 6.00% Non-Cumulative Preferred Stock, Series A"))

    def test_read_cover_picks_the_ticker_s_listing(self):
        titles = [("a", "Ordinary Shares, par value US$0.000003125 per share", None),
                  ("b", "American Depositary Shares, each representing eight Ordinary Shares", "BABA")]
        self.assertEqual(ads.read_cover(titles, "BABA"), ("ads", 8.0, titles[1][1]))
        self.assertEqual(ads.read_cover([("", "Common Shares", "TSM")], "TSM")[0], "direct")
        self.assertEqual(ads.read_cover([("", "American Depositary Shares", "TM")], "TM"), ("ads", None, "American Depositary Shares"))
        self.assertEqual(ads.read_cover([], "X"), (None, None, None))


class Documents(unittest.TestCase):
    def test_sentences(self):
        for text, want in (("<p>Each ADS represents five (5) common shares held by such individuals.</p>", 5),
                           ("Each American Depositary Share represents ten shares of our common stock.", 10),
                           ("each ADS representing two Class A ordinary shares", 2),
                           ("Each ADS represents one share of Common Stock of Sony Group.", 1),
                           ("The ADS to ordinary share ratio is 1:8.", 8),
                           ("American Depositary Shares, each representing one-half of one Class A ordinary share", 0.5)):
            r, quote = ads.decide(ads.ratios_in_document(text.encode()))
            self.assertEqual(r, want, text)
            self.assertTrue(quote)

    def test_history_is_not_the_ratio(self):
        text = "symbol DXF on March 5, 2018. Prior to December 17, 2014, each ADS represented the right to receive four Class A ordinary shares."
        self.assertEqual(ads.decide(ads.ratios_in_document(text.encode())), (None, None))

    def test_the_prevailing_ratio_wins_and_a_tie_is_unknown(self):
        text = "we changed the ratio from 1:2 to 1:10. Each ADS now represents ten ordinary shares. Each ADS represents ten ordinary shares."
        self.assertEqual(ads.decide(ads.ratios_in_document(text.encode()))[0], 10)
        tie = "Each ADS represents two ordinary shares. Each ADS represents four ordinary shares."
        self.assertIsNone(ads.decide(ads.ratios_in_document(tie.encode()))[0])
        self.assertEqual(ads.decide([]), (None, None))


class Settle(unittest.TestCase):
    def test_cover_ratio_settles_it(self):
        got = {"listing": "ads", "ratio": 3.0, "title": "ADS, each representing three Equity Shares", "doc": None}
        self.assertEqual(ads.settle(got)[:2], ("ads", 3.0))

    def test_text_ratio_beats_a_plain_cover(self):
        # TSMC: the cover lists "Common Shares"; the text says each ADS is five
        got = {"listing": "direct", "ratio": 1.0, "title": "Common Shares", "doc": b"Each ADS represents five common shares."}
        self.assertEqual(ads.settle(got)[:2], ("ads", 5.0))

    def test_plain_cover_with_no_ratio_in_the_text_is_direct(self):
        # Spotify: ordinary shares listed; the text mentions depositary shares in passing
        got = {"listing": "direct", "ratio": 1.0, "title": "Ordinary Shares", "doc": b"Unlike American Depositary Shares, our ordinary shares trade directly."}
        self.assertEqual(ads.settle(got)[:2], ("direct", 1.0))

    def test_depositary_share_without_a_ratio_is_unknown_never_guessed(self):
        got = {"listing": "ads", "ratio": None, "title": "American Depositary Shares", "doc": b"Nothing about the ratio here."}
        listing, ratio, title, quote, source, status = ads.settle(got)
        self.assertEqual((listing, ratio, status), ("unknown", None, "ratio not stated"))
        got["doc"] = None
        self.assertEqual(ads.settle(got)[0], "unknown")


if __name__ == "__main__":
    unittest.main()
