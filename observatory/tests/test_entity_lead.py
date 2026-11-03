# -*- coding: utf-8 -*-
"""A one-company page opens with its key figures and links to the same
company's other pages and its peers (app/entity_lead.py).

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_entity_lead
"""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import entity_lead
from app.main import app
from tests import _data

LEAD = re.compile(r'<div class="entity-lead"[^>]*>.*?</div>', re.S)


class WordingTest(unittest.TestCase):
    """The pieces a sentence is made of, without a database."""

    def test_custody_account_is_named_for_what_it_is(self):
        row = {"name_raw": "日本マスタートラスト信託銀行株式会社", "holder_kind": "trust_bank_nominee",
               "ratio_pct": 20.65}
        self.assertEqual(entity_lead._holder_clause(row),
                         "a trust-bank custody account (日本マスタートラスト信託銀行株式会社), "
                         "with 20.65% of shares")

    def test_standing_proxy_is_not_part_of_the_holder(self):
        row = {"name_raw": "STATE STREET BANK AND TRUST COMPANY 505103(常任代理人 株式会社みずほ銀行決済営業部)",
               "holder_kind": "entity", "ratio_pct": 3.1}
        self.assertEqual(entity_lead._holder_clause(row),
                         "STATE STREET BANK AND TRUST COMPANY 505103, with 3.10% of shares")

    def test_english_name_a_filer_gives_in_brackets_is_used(self):
        self.assertEqual(entity_lead._filer_name(
            "ウエリントン・マネージメント・カンパニー・エルエルピー（Wellington Management Company LLP）"),
            "Wellington Management Company LLP")
        self.assertEqual(entity_lead._filer_name("株式会社豊田自動織機"), "株式会社豊田自動織機")

    def test_yen_keeps_the_scale_of_the_figure(self):
        self.assertEqual(entity_lead._yen(294000000), "¥294mn")
        self.assertEqual(entity_lead._yen(441.4e9), "¥441.4bn")
        self.assertEqual(entity_lead._yen(50.7e12), "¥50.7tn")
        self.assertEqual(entity_lead._yen(None), "—")

    def test_dollars_carry_a_true_minus(self):
        self.assertEqual(entity_lead._usd(74294811), "$74.3m")
        self.assertEqual(entity_lead._usd(-2500000), "−$2.5m")
        self.assertEqual(entity_lead._usd(None), "—")


class InjectTest(unittest.TestCase):
    """Where the block goes, and that a failure leaves the page as it was."""

    LEAD = {"sentences": ["One <b>.", "Two."], "links": [("Board & Pay", "governance.html?c=9962")],
            "peers": [("Sojitz", "ownership.html?c=2768")], "peer_label": "Wholesale Trade",
            "name": "Misumi", "value": "9962", "keys": "c"}
    PAGE = ('<div class="co-head"><h1 id="co-name"></h1></div>'
            '<p class="filing-meta" id="co-filing"></p><dl class="fact-row"></dl>')

    def test_written_after_the_company_header_and_escaped(self):
        with patch.object(entity_lead, "build", return_value=self.LEAD):
            out = entity_lead.inject(self.PAGE, "ownership.html", {"c": "9962"}, {"h1": "Misumi"})
        self.assertIn('<p class="filing-meta" id="co-filing"></p><div class="entity-lead"', out)
        self.assertIn("One &lt;b&gt;. Two.", out)
        self.assertIn('data-value="9962"', out)
        self.assertIn('<a href="governance.html?c=9962">Board &amp; Pay</a>', out)
        self.assertIn("Same industry (Wholesale Trade)", out)

    def test_a_failure_serves_the_page_unchanged(self):
        with patch.object(entity_lead, "build", side_effect=RuntimeError("database mid-swap")):
            self.assertEqual(entity_lead.inject(self.PAGE, "ownership.html", {"c": "9962"}, {}),
                             self.PAGE)

    def test_a_page_without_its_anchor_is_unchanged(self):
        with patch.object(entity_lead, "build", return_value=self.LEAD):
            self.assertEqual(entity_lead.inject("<main></main>", "ownership.html", {"c": "9962"}, {}),
                             "<main></main>")

    def test_no_company_no_lead(self):
        self.assertIsNone(entity_lead.build("ownership.html", {}, {"h1": "x"}))
        self.assertIsNone(entity_lead.build("ownership.html", {"c": "9962"}, None))
        self.assertIsNone(entity_lead.build("ownership.html", {"c": "9962"}, {"noindex": True}))
        self.assertIsNone(entity_lead.build("cpi.html", {"c": "9962"}, {"h1": "x"}))


@unittest.skipUnless(_data.EQUITY, _data.NO_EQUITY)
class ServedTest(unittest.TestCase):
    """The block as a reader receives it, from the real data."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _lead(self, path):
        m = LEAD.search(self.client.get(path).text)
        return m.group(0) if m else None

    def test_company_page_opens_with_its_figures_and_links(self):
        lead = self._lead("/company.html?code=7203")
        self.assertIsNotNone(lead)
        self.assertRegex(lead, r"reported revenue of ¥[\d.,]+(tn|bn)")
        self.assertIn("largest shareholder is", lead)
        self.assertIn('href="ownership.html?c=7203"', lead)
        self.assertNotIn('href="company.html?code=7203"', lead)  # not a link to itself

    def test_register_figures_match_the_register_api(self):
        lead = self._lead("/ownership.html?c=7203")
        head = self.client.get("/api/v1/equity/ownership/company/7203").json()
        self.assertIn("own %.2f%% of its shares" % head["majors_ratio_filed_pct"], lead)
        top = head["holders"][0]
        self.assertIn("with %.2f%% of shares" % top["ratio_pct"], lead)

    def test_market_view_and_unknown_codes_get_no_lead(self):
        self.assertIsNone(self._lead("/ownership.html"))
        self.assertIsNone(self._lead("/ownership.html?c=ZZZZ"))
        self.assertIsNone(self._lead("/company.html"))


@unittest.skipUnless(_data.SEC, "data/sec.duckdb not built")
class UsPayTest(unittest.TestCase):

    def test_ceo_pay_matches_the_pay_api(self):
        client = TestClient(app)
        page = client.get("/us-pay.html?t=AAPL").text
        lead = LEAD.search(page)
        self.assertIsNotNone(lead)
        years = client.get("/api/v1/us/pay/AAPL").json()["years"]
        latest = max((y for y in years if y["values"].get("ceo_total_pay") is not None),
                     key=lambda y: y["fiscal_year_end"])
        self.assertIn(entity_lead._usd(latest["values"]["ceo_total_pay"]), lead.group(0))
        self.assertIn('href="us-company.html?t=AAPL"', lead.group(0))


if __name__ == "__main__":
    unittest.main()
