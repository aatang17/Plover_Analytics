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

LEAD = re.compile(r'<p class="entity-lead"[^>]*>.*?</p>', re.S)
MORE = re.compile(r'<section class="entity-more"[^>]*>.*?</section>', re.S)


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

    def test_large_dollars_are_said_at_their_scale(self):
        self.assertEqual(entity_lead._usd_scaled(416161000000.0), "$416.2bn")
        self.assertEqual(entity_lead._usd_scaled(5001541078940.0), "$5.0tn")
        self.assertEqual(entity_lead._usd_scaled(-2.5e9), "\u2212$2.5bn")
        self.assertEqual(entity_lead._usd_scaled(None), entity_lead.DASH)

    def test_dollars_carry_a_true_minus(self):
        self.assertEqual(entity_lead._usd(74294811), "$74.3m")
        self.assertEqual(entity_lead._usd(-2500000), "−$2.5m")
        self.assertEqual(entity_lead._usd(None), "—")


class InjectTest(unittest.TestCase):
    """Where the two blocks go, and that a failure leaves the page as it was."""

    LEAD = {"sentences": ["One <b>."], "links": [("Board & Pay", "governance.html?c=9962")],
            "peers": [("Sojitz", "ownership.html?c=2768")], "peer_label": "Wholesale Trade",
            "name": "Misumi", "value": "9962", "keys": "c"}
    PAGE = ('<main><div class="co-head"><h1 id="co-name"></h1></div>'
            '<p class="filing-meta" id="co-filing"></p><dl class="fact-row"></dl></main>')

    def test_sentence_under_the_header_links_at_the_foot(self):
        with patch.object(entity_lead, "build", return_value=self.LEAD):
            out = entity_lead.inject(self.PAGE, "ownership.html", {"c": "9962"}, {"h1": "Misumi"})
        self.assertIn('<p class="filing-meta" id="co-filing"></p><p class="entity-lead"', out)
        self.assertIn("One &lt;b&gt;.", out)
        self.assertRegex(out, r'<section class="entity-more"[^>]*data-value="9962".*</section></main>$')
        self.assertIn('<a href="governance.html?c=9962">Board &amp; Pay</a>', out)
        self.assertIn("Same industry", out)

    def test_a_failure_serves_the_page_unchanged(self):
        with patch.object(entity_lead, "build", side_effect=RuntimeError("database mid-swap")):
            self.assertEqual(entity_lead.inject(self.PAGE, "ownership.html", {"c": "9962"}, {}),
                             self.PAGE)

    def test_a_page_without_its_anchor_still_gets_its_links(self):
        with patch.object(entity_lead, "build", return_value=self.LEAD):
            out = entity_lead.inject("<main></main>", "ownership.html", {"c": "9962"}, {})
        self.assertNotIn('class="entity-lead"', out)
        self.assertIn('class="entity-more"', out)

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

    def _blocks(self, path):
        page = self.client.get(path).text
        lead, more = LEAD.search(page), MORE.search(page)
        return (lead.group(0) if lead else None), (more.group(0) if more else None)

    def _lead(self, path):
        return self._blocks(path)[0]

    def test_company_page_opens_with_its_figures_and_links_at_the_foot(self):
        lead, more = self._blocks("/company.html?code=7203")
        self.assertRegex(lead, r"revenue \u00a5[\d.,]+(tn|bn), profit")
        self.assertIn('href="ownership.html?c=7203"', more)
        self.assertNotIn('href="company.html?code=7203"', more)  # not a link to itself

    def test_register_names_the_largest_holder_the_tiles_do_not(self):
        lead = self._lead("/ownership.html?c=7203")
        head = self.client.get("/api/v1/equity/ownership/company/7203").json()
        owner = next(r for r in head["holders"]
                     if r["holder_kind"] not in ("trust_bank_nominee", "foreign_nominee", "treasury"))
        self.assertIn("Largest holder that is not a custody account", lead)
        self.assertIn("%.2f%% of shares" % owner["ratio_pct"], lead)

    def test_market_view_and_unknown_codes_get_no_lead(self):
        self.assertIsNone(self._lead("/ownership.html"))
        self.assertIsNone(self._lead("/ownership.html?c=ZZZZ"))
        self.assertIsNone(self._lead("/company.html"))

    def test_the_text_a_crawler_reads_is_the_companys_not_the_markets(self):
        # 2026-11-09: stakes.html?c=7203 served the count of every 5% report
        # in Japan under "Latest values as text and table".
        md = self.client.get("/ownership.md?c=7203").text
        head = self.client.get("/api/v1/equity/ownership/company/7203").json()
        self.assertIn("## Major shareholders of", md)
        self.assertIn("| 1 | ", md)
        self.assertIn("%.2f" % head["holders"][0]["ratio_pct"], md)
        self.assertNotIn("Shareholder registers:", md)  # the market-wide summary
        for page, heading in (("stakes", "5% shareholders of"), ("holdings", "Cross-shareholdings of"),
                              ("governance", "Board and pay of")):
            md = self.client.get("/%s.md?c=7203" % page).text
            self.assertIn("## " + heading, md, page)
        # the market view keeps its summary
        self.assertIn("Shareholder registers:", self.client.get("/ownership.md").text)



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
        self.assertIn('href="us-company.html?t=AAPL"', MORE.search(page).group(0))


    def test_company_record_opens_with_its_filed_figures(self):
        client = TestClient(app)
        page = client.get("/us-company.html?t=AAPL").text
        lead = LEAD.search(page)
        self.assertIsNotNone(lead)
        m = client.get("/api/v1/us/financials/metrics/AAPL").json()
        self.assertIn(entity_lead._usd_scaled(m["size"]["revenue"]), lead.group(0))
        self.assertIn(entity_lead._usd_scaled(m["size"]["net_income"]), lead.group(0))
        self.assertIn(entity_lead._usd_scaled(m["metrics"]["market_cap"]), lead.group(0))
        self.assertIn('href="us-pay.html?t=AAPL"', MORE.search(page).group(0))

    def test_company_addresses_are_served_with_the_company_showing(self):
        client = TestClient(app)
        for address, listing in (("/us-company.html?t=AAPL", "search-view"),
                                 ("/us-pay.html?t=AAPL", "index-view")):
            page = client.get(address).text
            self.assertIn('<section id="%s" hidden>' % listing, page, address)
            self.assertIn('<section id="company-view">', page, address)
        # The list itself still opens on the list.
        self.assertIn('<section id="company-view" hidden>', client.get("/us-company.html").text)

    def test_a_loss_is_said_as_a_loss(self):
        metrics = {"unit": "USD", "period_end": "2025-12-31", "metrics": {}, "price": {},
                   "size": {"revenue": 46.8e6, "net_income": -101.4e6}}
        with patch("app.sec_api.metrics", return_value=metrics):
            self.assertEqual(entity_lead._us_company_lead("Rezolve Ai PLC", "RZLV"),
                             ["Rezolve Ai PLC reported revenue of $46.8m and a net loss of "
                              "$101.4m for the year to December 2025."])

    def test_a_company_outside_the_index_links_no_ceo_pay_page(self):
        self.assertFalse(entity_lead._has_page("us-pay.html", "t=ZZZZQ"))


class CompanyViewSwapTest(unittest.TestCase):

    def test_an_unexpected_page_is_left_alone(self):
        from app import prerender
        page = '<section id="search-view"><section id="company-view">'
        self.assertEqual(prerender._show_company_view(page, "search-view"), page)
        page = '<section id="search-view">x</section><section id="company-view" hidden>y</section>'
        self.assertEqual(prerender._show_company_view(page, "search-view"),
                         '<section id="search-view" hidden>x</section><section id="company-view">y</section>')


if __name__ == "__main__":
    unittest.main()
