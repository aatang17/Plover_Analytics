# -*- coding: utf-8 -*-
"""Annual report text (equity/sec_text_extract.py, app/sec_text_api.py) and the
stock box answers that quote it (app/stock_recipes.py): how a 10-K is cut into
items, how risks are found in each house style seen in real filings, and how
a question's subject is read. All in memory; no network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_sec_text
"""
import os
import sys
import unittest
import zlib

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import sec_text_extract as x  # noqa: E402
from app import sec_text_api, stock_recipes  # noqa: E402

PARA = "This paragraph is ordinary prose long enough to count as a section's text. " * 8


def page(*parts):
    return ("<html><body><div style='display:none'><ix:header>hidden facts</ix:header></div>%s</body></html>"
            % "".join(parts)).encode("utf-8")


def p(text, style=""):
    return "<p style='%s'>%s</p>" % (style, text)


def b(text):
    return p(text, "font-weight:700")


def toc():
    """A contents page naming every item, as every 10-K has."""
    return "<table>" + "".join("<tr><td>Item %s.</td><td>%s</td><td>%d</td></tr>" % (i, t, n)
                               for n, (i, t) in enumerate([("1", "Business"), ("1A", "Risk Factors"), ("2", "Properties"),
                                                           ("3", "Legal Proceedings"), ("7", "MD&A")])) + "</table>"


class Items(unittest.TestCase):
    def test_contents_page_loses_to_the_real_headings(self):
        body = page(toc(), b("Item 1. Business"), p("We make widgets for factories. " + PARA),
                    b("Item 1A. Risk Factors"), b("Competition could hurt our margins and our market share."), p(PARA),
                    b("Item 2. Properties"), p("Our headquarters is in Ohio. " + PARA),
                    b("Item 3. Legal Proceedings"), p("See Note 12. " + PARA), b("Item 7. MD&A"), p(PARA))
        got = x.parse(body, "10-K")
        self.assertEqual(got["status"], "ok")
        secs = {s[0]: s for s in got["sections"]}
        self.assertEqual(sorted(secs), ["business", "legal", "mdna", "properties", "risk_factors"])
        self.assertTrue(secs["business"][3][0].startswith("We make widgets"))
        self.assertNotIn("hidden facts", " ".join(" ".join(s[3]) for s in got["sections"]))

    def test_repeated_page_header_does_not_end_a_section(self):
        # Microsoft repeats "PART I Item 1" at the top of every page
        body = page(toc(), b("Item 1. Business"), p("Opening: we make software. " + PARA),
                    p("PART I Item 1"), p(PARA), p("PART I Item 1"), p(PARA), p("PART I Item 1"), p(PARA),
                    p("PART I Item 1"), p("Gaming text. " + PARA),
                    b("Item 1A. Risk Factors"), b("Security breaches could harm our reputation and our results."), p(PARA))
        secs = {s[0]: s for s in x.parse(body, "10-K")["sections"]}
        self.assertTrue(secs["business"][3][0].startswith("Opening"))

    def test_page_footers_and_figure_rows_are_dropped(self):
        rows = "<table>%s</table>" % "".join("<tr><td>Revenue</td><td>$ 130,497</td><td>60,922</td></tr>" for _ in range(3))
        foot = "".join(p("%d Table of Contents" % n) for n in range(30, 36))
        body = page(toc(), b("Item 1. Business"), p(PARA), foot, rows, p("Closing words. " + PARA),
                    b("Item 1A. Risk Factors"), b("Demand for our products may fall sharply in a downturn."), p(PARA))
        text = " ".join(x.parse(body, "10-K")["sections"][0][3])
        self.assertNotIn("Table of Contents", text)
        self.assertNotIn("130,497", text)
        self.assertIn("Closing words", text)

    def test_no_item_headings_is_unsplit(self):
        self.assertEqual(x.parse(page(p(PARA)), "10-K")["status"], "unsplit")


def risk_section(*parts):
    return page(toc(), b("Item 1. Business"), p(PARA), b("Item 1A. Risk Factors"), *parts, b("Item 2. Properties"), p(PARA))


class Risks(unittest.TestCase):
    def heads(self, body):
        return [(c, h) for c, h, _, _ in x.parse(body, "10-K")["risks"]]

    def test_sentence_headlines_under_categories(self):
        got = self.heads(risk_section(
            p("The following risk factors should be considered carefully. " + PARA, "font-style:italic"),
            b("Risks Related to Our Industry"), b("Competition could adversely impact our market share and results."), p(PARA),
            b("Risks Related to Regulation"), b("New export rules may restrict sales of our products abroad."), p(PARA)))
        self.assertEqual(got, [("Risks Related to Our Industry", "Competition could adversely impact our market share and results."),
                               ("Risks Related to Regulation", "New export rules may restrict sales of our products abroad.")])

    def test_title_case_headlines_without_full_stops(self):
        # Amazon: "We Face Intense Competition" in bold italic, the category in bold
        got = self.heads(risk_section(b("Business and Industry Risks"), b("We Face Intense Competition"), p(PARA),
                                      b("We Have Foreign Exchange Risk"), p(PARA)))
        self.assertEqual(got, [("Business and Industry Risks", "We Face Intense Competition"),
                               ("Business and Industry Risks", "We Have Foreign Exchange Risk")])

    def test_capitals_topics_and_colon_categories(self):
        # Pfizer: "RISKS RELATED TO ...:" then "MANAGED CARE TRENDS"
        got = self.heads(risk_section(b("RISKS RELATED TO OUR BUSINESS:"), b("MANAGED CARE TRENDS"), p(PARA),
                                      b("COMPETITIVE PRODUCTS"), p(PARA)))
        self.assertEqual([h for _, h in got], ["MANAGED CARE TRENDS", "COMPETITIVE PRODUCTS"])
        self.assertEqual(got[0][0], "RISKS RELATED TO OUR BUSINESS")

    def test_bold_opening_sentences(self):
        # Microsoft: the risk is the bold first sentence of a paragraph; plain capitals above are the category
        got = self.heads(risk_section(
            p("OPERATIONAL RISKS"),
            "<p><b>We may experience supply problems.</b> " + PARA + "</p>", p(PARA),
            "<p><b>We may experience outages and capacity constraints.</b> " + PARA + "</p>"))
        self.assertEqual(got, [("OPERATIONAL RISKS", "We may experience supply problems."),
                               ("OPERATIONAL RISKS", "We may experience outages and capacity constraints.")])

    def test_summary_categories_are_not_risks(self):
        got = self.heads(risk_section(
            b("Risk Factors Summary"), b("Risks Related to Our Industry"), p("• bullet one"), p("• bullet two"),
            b("Risk Factors"), b("Risks Related to Our Industry"), b("Competition could adversely impact our results."), p(PARA)))
        self.assertEqual([h for _, h in got], ["Competition could adversely impact our results."])

    def test_smaller_reporting_company_says_why(self):
        got = x.parse(risk_section(p("As a smaller reporting company, we are not required to provide the information required by this Item.")), "10-K")
        self.assertEqual(got["status"], "partial")
        self.assertIn("smaller reporting company", got["note"])


class TwentyF(unittest.TestCase):
    def test_risks_start_at_risk_factors_inside_item_3(self):
        items20 = "<table>%s</table>" % "".join("<tr><td>Item %s.</td><td>x</td></tr>" % i for i in ("3", "4", "5"))
        body = page(items20, b("Item 3. Key Information"), b("Reasons for the Offer and Use of Proceeds"), p("Not applicable."),
                    b("Risk Factors"), b("Risks Relating to Our Industry"), b("Demand for chips may fall in a downturn."), p(PARA),
                    b("Item 4. Information on the Company"), p("We are a dedicated foundry founded in 1987. " + PARA),
                    b("Item 5. Operating and Financial Review"), p(PARA))
        got = x.parse(body, "20-F")
        secs = {s[0]: s for s in got["sections"]}
        self.assertEqual(got["status"], "ok")
        self.assertNotIn("Not applicable.", secs["risk_factors"][3])
        self.assertEqual([h for _, h, _, _ in got["risks"]], ["Demand for chips may fall in a downturn."])

    def test_line_by_line_typesetting_is_rejoined(self):
        # TSMC's 20-F: every printed line is its own block
        lines = []
        for n in range(30):
            lines += ["Paragraph %s: we were founded as a joint venture among the government and other" % "abcdefghijklmnopqrstuvwxyzABCD"[n],
                      "private investors (%s) and were incorporated as a company limited by shares on" % "abcdefghijklmnopqrstuvwxyzABCD"[n],
                      "February 21, 1987, and have built a strong position (%s) as a dedicated foundry." % "abcdefghijklmnopqrstuvwxyzABCD"[n]]
        blocks = x.blocks(page(*[p(t) for t in lines]))
        self.assertEqual(len(blocks), 30)
        self.assertTrue(blocks[0][0].endswith("dedicated foundry."))


class Api(unittest.TestCase):
    def setUp(self):
        self.c = duckdb.connect(":memory:")
        self.c.execute(x.SCHEMA_SQL)
        paras = ["This Annual Report on Form 10-K contains forward-looking statements within the meaning of the "
                 "safe harbor provisions, and we caution readers not to rely on them unduly in any way at all.",
                 "Overview",
                 "NVIDIA pioneered accelerated computing to help solve the most challenging computational problems. "
                 "It is now a data center scale infrastructure company.",
                 "Our Data Center platform serves hyperscale customers, enterprises and public cloud providers with "
                 "compute and networking; tariffs on imports of components could raise our costs."]
        self.c.execute("INSERT INTO sec_text_sections VALUES ('a', 'business', '1', 'Business', 0, ?, '[1]', 0)",
                       [zlib.compress("\n\n".join(paras).encode("utf-8"))])

    def test_summary_skips_boilerplate_and_headings(self):
        got = sec_text_api.summary(self.c, "a")
        self.assertTrue(got[0].startswith("NVIDIA pioneered"))

    def test_mentions_find_every_word(self):
        hits = sec_text_api.mentions(self.c, "a", "tariffs")
        self.assertEqual(len(hits), 1)
        self.assertIn("tariffs on imports", hits[0][1])
        self.assertEqual(sec_text_api.mentions(self.c, "a", "bitcoin"), [])


class Questions(unittest.TestCase):
    rec = {"name": "Apple Inc.", "ticker": "AAPL"}

    def test_subject_of_a_question(self):
        t = stock_recipes._topic_of
        self.assertEqual(t("what does Apple say about tariffs", self.rec), "tariffs")
        self.assertEqual(t("What does Apple say about China in its 10-K?", self.rec), "China")
        self.assertEqual(t("risks to Apple from tariffs", self.rec), "tariffs")
        self.assertEqual(t("who are Apple's competitors", self.rec), "competition")
        self.assertEqual(t("risks to Tesla from tariffs", {"name": "Tesla, Inc.", "ticker": "TSLA"}), "tariffs")
        self.assertIsNone(t("what are Apple's biggest risks", self.rec))

    def test_new_shapes_match_their_questions(self):
        hit = lambda q, rid: any(__import__("re").search(pat, q, __import__("re").I)  # noqa: E731
                                 for pat in stock_recipes.BY_ID[rid]["patterns"])
        self.assertTrue(hit("what are Nvidia's biggest risks", "company_risks"))
        self.assertTrue(hit("what does Apple say about tariffs", "company_says"))
        self.assertTrue(hit("what lawsuits does Apple face", "company_says"))
        self.assertTrue(hit("what does Nvidia do", "company_business"))


if __name__ == "__main__":
    unittest.main()
