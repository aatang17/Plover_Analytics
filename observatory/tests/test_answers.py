# -*- coding: utf-8 -*-
"""Answer pages: the first paragraph states the number, dated and sourced,
in the HTML a crawler receives — and the same number everywhere else.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_answers
"""
import os
import re
import unittest

from fastapi.testclient import TestClient

from app import answers, indexnow
from app.main import app
from tests import _data

LEAD = re.compile(r'(?is)<p class="answer-lead" id="answer-lead"[^>]*>(.*?)</p>')


class AnswerPageTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.page = cls.client.get("/japan-inflation-rate.html")
        cls.block = answers.answer("japan-inflation-rate.html")

    def test_every_question_has_a_shell_and_the_shell_declares_its_question(self):
        for q in answers.QUESTIONS:
            r = self.client.get("/" + q["page"])
            self.assertEqual(r.status_code, 200, q["page"])
            self.assertIn('data-answer="%s"' % q["page"][:-5], r.text)
            self.assertIn("<h1>%s</h1>" % q["question"].replace("'", "'"), r.text)

    def test_the_lead_sentence_is_in_the_served_html(self):
        if self.block is None:
            self.skipTest(_data.NO_MACRO)
        found = LEAD.search(self.page.text)
        self.assertIsNotNone(found)
        self.assertIn(self.block["sentence"].replace("'", "'"), found.group(1))
        # dated, and sourced, in the sentence itself
        self.assertRegex(found.group(1), r"(January|February|March|April|May|June|July|"
                                          r"August|September|October|November|December) \d{4}")
        self.assertIn("Statistics Bureau", found.group(1))

    def test_the_lead_number_is_the_api_number(self):
        if self.block is None:
            self.skipTest(_data.NO_MACRO)
        r = self.client.get("/api/v1/cpi-jp/observations?series=0001&measure=yoy")
        pts = [p for p in r.json()["series"][0]["points"] if p[1] is not None]
        latest = pts[-1][1]
        self.assertIn("%.1f%%" % abs(latest), self.block["sentence"])

    def test_the_markdown_twin_and_the_api_carry_the_same_sentence(self):
        if self.block is None:
            self.skipTest(_data.NO_MACRO)
        md = self.client.get("/japan-inflation-rate.md")
        self.assertEqual(md.status_code, 200)
        self.assertIn(self.block["sentence"], md.text)
        api = self.client.get("/api/v1/answers/japan-inflation-rate").json()
        self.assertEqual(api["sentence"], self.block["sentence"])

    def test_a_derived_number_states_its_formula_and_an_official_one_says_as_published(self):
        if self.block is None:
            self.skipTest(_data.NO_MACRO)
        self.assertEqual(self.block["trust"], "derived")
        self.assertIn("Show calculation", self.page.text)
        self.assertIn("index[t−12 months]", self.page.text)
        boj = answers.answer("boj-jgb-holdings.html")
        if boj:
            self.assertEqual(boj["trust"], "official")
            self.assertIn("As published", boj["asof"])

    def test_missing_is_a_dash_never_zero(self):
        for q in answers.QUESTIONS:
            block = answers.answer(q["page"])
            if not block:
                continue
            for row in block["table"]["rows"]:
                for cell in row[1:]:
                    self.assertNotEqual(cell, "0.0")
                    self.assertNotEqual(cell, "")

    def test_an_unknown_answer_404s_and_the_list_names_every_page(self):
        self.assertEqual(self.client.get("/api/v1/answers/no-such-page").status_code, 404)
        listed = self.client.get("/api/v1/answers").json()["answers"]
        self.assertEqual(len(listed), len(answers.QUESTIONS))

    def test_answer_pages_are_in_the_sitemap_and_llms_txt(self):
        pages = self.client.get("/sitemap-pages.xml").text
        llms = self.client.get("/llms.txt").text
        for q in answers.QUESTIONS:
            self.assertIn("/" + q["page"], pages)
            self.assertIn("/" + q["page"], llms)

    def test_a_published_rate_is_stated_as_published_and_never_recomputed(self):
        block = answers.answer("japan-unemployment-rate.html")
        if block is None:
            self.skipTest(_data.NO_MACRO)
        r = self.client.get("/api/v1/unemployment-jp/observations?series=sa.unemployment_rate.total")
        latest = [p for p in r.json()["series"][0]["points"] if p[1] is not None][-1][1]
        self.assertIn("was %.1f%%" % latest, block["sentence"])
        self.assertEqual(block["trust"], "official")
        self.assertIsNone(block["calc"])

    def test_a_ratio_keeps_its_two_published_decimals(self):
        block = answers.answer("japan-jobs-to-applicants-ratio.html")
        if block is None:
            self.skipTest(_data.NO_MACRO)
        self.assertRegex(block["sentence"], r"ratio was \d\.\d\d in ")

    def test_a_rate_column_gets_no_percent_change_of_itself(self):
        # The unemployment rate going from 2.6% to 2.4% is not "−7.7%".
        from app import readable
        block = readable.macro_block("unemployment-jp")
        if block is None:
            self.skipTest(_data.NO_MACRO)
        self.assertFalse(any("YoY" in c["label"] for c in block["table"]["columns"]))


class SinceTest(unittest.TestCase):

    def test_highest_since_names_the_last_period_at_or_above(self):
        import datetime
        pts = [(datetime.date(2024, m, 1), v) for m, v in
               enumerate([3.0, 2.0, 1.0, 1.5, 1.2, 1.1, 1.0, 1.3, 1.4, 1.6, 1.8, 2.5], 1)]
        pts += [(datetime.date(2025, m, 1), v) for m, v in
                enumerate([2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.9, 2.95], 1)]
        # December 2025 at 2.95: the last month at or above it is January 2024
        self.assertEqual(answers._since(pts, "monthly", "highest"),
                         "the highest since January 2024")

    def test_a_short_run_is_not_worth_saying(self):
        import datetime
        pts = [(datetime.date(2025, m, 1), v) for m, v in
               enumerate([1.0, 2.0, 1.0, 1.0, 1.5], 1)]
        self.assertEqual(answers._since(pts, "monthly", "highest"), "")


class IndexNowTest(unittest.TestCase):

    def test_no_key_means_nothing_is_sent_and_the_key_route_404s(self):
        old = os.environ.pop("INDEXNOW_KEY", None)
        try:
            self.assertFalse(indexnow.submit(["https://example.com/"]))
            self.assertEqual(TestClient(app).get("/indexnow-key.txt").status_code, 404)
        finally:
            if old is not None:
                os.environ["INDEXNOW_KEY"] = old

    def test_the_key_is_served_where_the_submission_says(self):
        os.environ["INDEXNOW_KEY"] = "testkey1234567890"
        try:
            r = TestClient(app).get("/indexnow-key.txt")
            self.assertEqual(r.text, "testkey1234567890")
            self.assertEqual(indexnow.key(), "testkey1234567890")
        finally:
            del os.environ["INDEXNOW_KEY"]

    def test_a_malformed_key_is_treated_as_none(self):
        os.environ["INDEXNOW_KEY"] = "short"
        try:
            self.assertEqual(indexnow.key(), "")
        finally:
            del os.environ["INDEXNOW_KEY"]

    def test_a_dataset_maps_to_its_page_its_answers_and_home(self):
        urls = indexnow.urls_for_dataset("cpi-jp")
        self.assertIn("https://ploveranalytics.com/", urls)
        self.assertIn("https://ploveranalytics.com/cpi.html", urls)
        self.assertIn("https://ploveranalytics.com/japan-inflation-rate.html", urls)
        self.assertEqual(len(urls), len(set(urls)))


if __name__ == "__main__":
    unittest.main()
