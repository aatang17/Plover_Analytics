# -*- coding: utf-8 -*-
"""The box for US stocks (app/stock_recipes.py, app/us_link.py, app/us_ask_api.py).

The twenty acceptance questions agreed on 2026-10-18: each must come back
with a headline, an answer, at least one source and a table or chart. They
run against data/sec.duckdb when it holds filings, prices, insiders and 13F
and skip otherwise. The parsers are tested without data.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_ask
"""
import datetime
import pathlib
import unittest

import os

import duckdb
from fastapi.testclient import TestClient

from app import us_link

ROOT = pathlib.Path(__file__).resolve().parent.parent


def setUpModule():
    import os
    import tempfile
    from app import us_steps
    os.environ["US_ASK_MODEL"] = "0"          # the word patterns alone, unless a test mocks the reader
    us_steps.LOG_FILE = os.path.join(tempfile.mkdtemp(), "us_ask_log.jsonl")


def tearDownModule():
    import os
    os.environ.pop("US_ASK_MODEL", None)

QUESTIONS = [
    ("company_value", "What is Apple worth, and what does it trade on?"),
    ("company_growth_margin", "Is Nvidia growing faster than its revenue was a year ago, and at what margin?"),
    ("company_shareholder_return", "How much has Microsoft returned to shareholders this year, buybacks and dividends, and what yield is that?"),
    ("company_price_performance", "How has Tesla stock done over the past year?"),
    ("company_calendar", "When does Amazon report next, and when did it last file?"),
    ("company_holders", "Who are the biggest holders of Apple, and who added last quarter?"),
    ("company_insiders", "Have any insiders at Nvidia bought shares this year, or only sold?"),
    ("company_insiders", "Which Nvidia insiders sold this month, and was it under a 10b5-1 plan?"),
    ("manager_portfolio", "What does Berkshire Hathaway own, and what did it add or drop?"),
    ("managers_holding_both", "Which managers hold both Apple and Alphabet?"),
    ("industry_rank", "Rank the carmakers by operating margin."),
    ("industry_rank", "Which large companies trade on the lowest P/E in semiconductors?"),
    ("market_rank", "Who has the highest buyback yield among companies above $10bn in market value?"),
    ("market_rank", "The ten largest US companies by market value, with P/E and one-year change."),
    ("market_rank", "Which stocks fell most over the past year among companies above $50bn?"),
    ("insider_buying_market", "Where are insiders buying in the open market this month, above $1m?"),
    ("calendar_window", "Which companies report in the next two weeks on Nasdaq?"),
    ("holders_increase_industry", "Biggest 13F position increases last quarter in banks."),
    ("banks_vs_yield", "How have bank profits moved against the 10-year yield since 2018?"),
    ("company_as_filed_history", "What did Apple report for revenue in each of the last five annual reports, as filed at the time, and which filing each number came from?"),
]


class ParserTest(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(us_link.norm_name("Apple Inc."), "APPLE")
        self.assertEqual(us_link.norm_name("ALPHABET INC CLASS A"), "ALPHABET")
        self.assertEqual(us_link.norm_name("Berkshire Hathaway Inc"), "BERKSHIRE HATHAWAY")
        self.assertEqual(us_link.norm_name("Johnson & Johnson"), "JOHNSON AND JOHNSON")

    def test_money(self):
        self.assertEqual(us_link.parse_money("above $10bn in market value"), 1e10)
        self.assertEqual(us_link.parse_money("above $1m"), 1e6)
        self.assertEqual(us_link.parse_money("50 billion"), 5e10)
        self.assertIsNone(us_link.parse_money("since 2018"))

    def test_years_and_names(self):
        self.assertEqual(us_link.parse_years("Apple 5 year revenue"), 5)
        self.assertEqual(us_link.parse_years("over the last ten years"), 10)
        self.assertEqual(us_link.parse_years("a decade of margins"), 10)
        self.assertIsNone(us_link.parse_years("what is apple worth"))
        self.assertEqual(us_link.display_name("FEDERAL SIGNAL CORP /DE/"), "Federal Signal Corp")
        self.assertEqual(us_link.display_name("JPMORGAN CHASE & CO"), "JPMorgan Chase & Co")
        self.assertEqual(us_link.display_name("Apple Inc."), "Apple Inc.")

    def test_index_and_year_words(self):
        today = datetime.date(2026, 10, 19)
        self.assertEqual(us_link.parse_index("Which S&P stock"), "sp500")
        self.assertEqual(us_link.parse_index("top S&P 500 banks"), "sp500")
        self.assertEqual(us_link.parse_index("Nasdaq 100 winners"), "nasdaq100")
        self.assertIsNone(us_link.parse_index("largest companies"))
        self.assertEqual(us_link.parse_since("performed best in 2026", today), datetime.date(2026, 1, 1))
        self.assertEqual(us_link.parse_until("best in 2025", today), datetime.date(2025, 12, 31))
        self.assertIsNone(us_link.parse_until("best in 2026", today))

    def test_time_words(self):
        today = datetime.date(2026, 10, 18)
        self.assertEqual(us_link.parse_since("since 2018", today), datetime.date(2018, 1, 1))
        self.assertEqual(us_link.parse_since("this month", today), datetime.date(2026, 10, 1))
        self.assertEqual(us_link.parse_since("this year", today), datetime.date(2026, 1, 1))
        self.assertEqual(us_link.parse_since("over the past year", today), datetime.date(2025, 10, 18))
        self.assertIsNone(us_link.parse_since("what is apple worth", today))
        self.assertEqual(us_link.parse_ahead_days("in the next two weeks"), 14)
        self.assertEqual(us_link.parse_count("the ten largest"), 10)
        self.assertEqual(us_link.parse_count("top 25 banks"), 25)


def _live():
    p = ROOT / "data" / "sec.duckdb"
    if not p.exists():
        return False
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
            return {"sec_filings", "sec_companies", "us_px_daily", "sec_ins_filings", "sec_13f_holdings"} <= names
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return False


@unittest.skipUnless(_live(), "data/sec.duckdb without the full US set")
class TwentyQuestionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_every_suggested_question_answers_without_the_model(self):
        """The questions the box offers on a miss (app/ask_miss.py TOPICS) answer by
        code: a stock question through the box with the model off, an economy
        question through the economy catalogue (app/assistant/preview.py)."""
        from unittest import mock
        from app import ask_miss
        from app.assistant import preview
        failures = []
        with mock.patch.dict(os.environ, {"US_ASK_MODEL": "0"}):
            for _pat, qs in ask_miss.TOPICS:
                for q in qs:
                    # an economy question is recognised by its words alone, so this holds
                    # wherever the test runs, whichever economy datasets are loaded
                    if preview.catalogue_hit(q) is not None or "2s10s" in q:
                        continue
                    r = self.c.get("/api/v1/us/ask", params={"q": q})
                    if r.status_code != 200 or not r.json().get("headline"):
                        failures.append("%s -> %d %s" % (q, r.status_code, r.text[:160]))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_question_answers(self):
        failures = []
        for recipe, q in QUESTIONS:
            r = self.c.get("/api/v1/us/ask", params={"q": q})
            if r.status_code != 200:
                failures.append("%s -> %d %s" % (q, r.status_code, r.text[:160]))
                continue
            d = r.json()
            if d["match"]["recipe"] != recipe:
                failures.append("%s -> matched %s, wanted %s" % (q, d["match"]["recipe"], recipe))
            if not d["headline"] or not d["answer"] or not d["sources"] or not (d["table"] or d["chart"]):
                failures.append("%s -> incomplete answer" % q)
            if "—" in d["headline"] and "No " not in d["headline"]:
                failures.append("%s -> headline carries a missing value: %s" % (q, d["headline"]))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_entities_are_reported(self):
        d = self.c.get("/api/v1/us/ask", params={"q": "What is Apple worth?"}).json()
        self.assertEqual(d["entities"]["mentioned_companies"][0]["ticker"], "AAPL")
        self.assertEqual(d["entities"]["company"]["cik"], 320193)

    def test_generic_words_are_not_companies(self):
        d = self.c.get("/api/v1/us/ask", params={"q": "The ten largest US companies by market value"}).json()
        self.assertEqual(d["match"]["recipe"], "market_rank")
        self.assertEqual(d["entities"]["mentioned_companies"], [])
        self.assertEqual(d["entities"]["count"], 10)

    def test_unanswerable_says_so(self):
        for q in ("Why did Tesla fall last week?", "Should I buy Nvidia?", "What will Apple earn next year?"):
            r = self.c.get("/api/v1/us/ask", params={"q": q})
            self.assertEqual(r.status_code, 422, q)
            self.assertIn("I", r.json()["detail"])
        r = self.c.get("/api/v1/us/ask", params={"q": "blorple snarf"})
        self.assertEqual(r.status_code, 422)

    def test_split_adjusted_change(self):
        d = self.c.get("/api/v1/us/prices/NFLX?start=2025-10-01").json()
        self.assertTrue(any(s["ratio"] == 10.0 for s in d["splits"]), d["splits"])
        self.assertGreater(d["change_pct"], -50)
        a = self.c.get("/api/v1/us/ask", params={"q": "How has Netflix stock done over the past year?"}).json()
        self.assertIn("split", a["answer"])

    def test_figures_compare_and_honest_refusals(self):
        import os
        os.environ["US_ASK_MODEL"] = "0"          # the pattern layer alone must get these right
        try:
            cases = [
                ("What is Nvidia's research spending?", "company_figure", "research and development"),
                ("How much debt does Ford have?", "company_figure", "segment"),
                ("Tesla debt", "company_figure", "borrowings"),
                ("Compare Apple and Microsoft margins", "compare_companies", "Operating margin"),
                ("Is Tesla more profitable than GM?", "compare_companies", "Net margin"),
                ("Which banks have the most deposits?", "industry_rank", "deposits"),
                ("Apple's gross margin", "company_figure", "gross margin"),
            ]
            for q, recipe, word in cases:
                d = self.c.get("/api/v1/us/ask", params={"q": q}).json()
                self.assertEqual(d.get("match", {}).get("recipe"), recipe, (q, d.get("detail")))
                self.assertIn(word, d["headline"] + d["answer"], q)
            for q, word in (("Which companies have the most employees?", "Employee"),
                            ("What is Microsoft's EBITDA?", "EBITDA"),
                            ("Rank airlines by customer satisfaction", "don't hold a figure"),
                            ("What is the weather in Paris?", "couldn't match")):
                r = self.c.get("/api/v1/us/ask", params={"q": q})
                self.assertEqual(r.status_code, 422, q)
                self.assertIn(word, r.json()["detail"], q)
        finally:
            os.environ["US_ASK_MODEL"] = "0"

    def test_history_charts(self):
        import os
        os.environ["US_ASK_MODEL"] = "0"
        try:
            d = self.c.get("/api/v1/us/ask", params={"q": "Draw a chart of Apple 5 year revenue"}).json()
            self.assertEqual(d["match"]["recipe"], "company_history")
            years = [p[0] for p in d["chart"]["series"][0]["points"]]
            self.assertEqual(len(years), 5)
            self.assertEqual(len(set(years)), 5, "one point per fiscal year: %s" % years)
            self.assertEqual(years, sorted(years))
            self.assertIn("a year on average", d["headline"])
            # two companies on one chart, a ratio, a per-share figure, a derived figure
            two = self.c.get("/api/v1/us/ask", params={"q": "Apple vs Microsoft revenue over 10 years"}).json()
            self.assertEqual(len(two["chart"]["series"]), 2)
            m = self.c.get("/api/v1/us/ask", params={"q": "Microsoft operating margin history"}).json()
            self.assertEqual(m["chart"]["unit"], "%")
            self.assertIn(" pp", m["headline"])
            e = self.c.get("/api/v1/us/ask", params={"q": "JPMorgan earnings per share by year"}).json()
            self.assertEqual(e["match"]["recipe"], "company_history")
            # a share price question over years stays a price question and says where prices begin
            # (prices run from May 2018 since 2026-10-19, so ask for more than that)
            px = self.c.get("/api/v1/us/ask", params={"q": "Chart Apple stock since 2016"}).json()
            self.assertEqual(px["match"]["recipe"], "company_price_performance")
            self.assertIn("prices on file begin", px["answer"])
            # a figure reported by segment only is explained, not charted
            r = self.c.get("/api/v1/us/ask", params={"q": "Ford debt over 5 years"})
            self.assertEqual(r.status_code, 404)
            self.assertIn("segment", r.json()["detail"])
        finally:
            os.environ["US_ASK_MODEL"] = "0"

    def test_index_windows_and_price_repairs(self):
        import os
        os.environ["US_ASK_MODEL"] = "0"
        try:
            d = self.c.get("/api/v1/us/ask", params={"q": "Which S&P stock has performed best in 2026"}).json()
            self.assertEqual(d["match"]["recipe"], "market_rank", d.get("detail"))
            self.assertIn("S&P 500 members", d["headline"])
            self.assertEqual(d["entities"]["mentioned_companies"], [], "S&P must not be read as the ticker S")
            self.assertGreater(len(d["table"]["rows"]), 5)
            # every ranked change is plausible for a member (no ticker reuse, no zero closes)
            self.assertTrue(all(row[4] is not None and -100 < row[4] < 2000 for row in d["table"]["rows"]))
            r = self.c.get("/api/v1/us/ask", params={"q": "Which Nasdaq 100 stock performed best in 2026"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("Nasdaq-100", r.json()["detail"])
            f = self.c.get("/api/v1/us/ask", params={"q": "F stock"}).json()
            self.assertEqual(f["entities"]["company"]["ticker"], "F")
            b = self.c.get("/api/v1/us/ask", params={"q": "How has BNY done this year?"}).json()
            self.assertIn("traded as BK", b["answer"])
            px = self.c.get("/api/v1/us/prices/BNY?start=2026-01-01").json()
            self.assertEqual(px["ticker_change"]["from"], "BK")
            self.assertTrue(all(bar["close"] > 0 for bar in px["bars"]))
            self.assertLess(abs(px["change_pct"]), 100)
        finally:
            os.environ["US_ASK_MODEL"] = "0"

    def _with_reader(self, form, fn):
        """Run fn with the reader switched on and answering with form."""
        import json
        from app import us_ask_model
        calls = []
        real, real_provider = us_ask_model._complete, us_ask_model.provider
        us_ask_model._complete = lambda prov, key, model, messages, **kw: (calls.append(messages) or
            {"text": json.dumps(form), "usage": {"in": 10, "out": 5}})
        us_ask_model.provider = lambda: ("deepseek", "db-test", "deepseek-chat")
        us_ask_model._CACHE.clear()
        from app import stock_recipes
        stock_recipes.QUICK_LANE = False          # these questions must reach the reader, not the quick lane
        try:
            return fn(), calls
        finally:
            stock_recipes.QUICK_LANE = True
            us_ask_model._complete, us_ask_model.provider = real, real_provider
            us_ask_model._CACHE.clear()

    def test_reader_runs_the_shape_it_names(self):
        form = {"shape": "company_insiders", "companies": [{"as_written": "tesla", "ticker": "TSLA"}],
                "read_as": "Insider trades at Tesla this year."}
        d, calls = self._with_reader(form, lambda: self.c.get("/api/v1/us/ask", params={"q": "did anyone at tesla buy stock recently"}).json())
        self.assertEqual(d["match"]["recipe"], "company_insiders")
        self.assertEqual(d["match"]["via"], "reader")
        self.assertEqual(d["match"]["read_as"], "Insider trades at Tesla this year.")
        self.assertEqual(d["entities"]["company"]["ticker"], "TSLA")
        # the model never sees a number: its prompt is the question, the date and the shapes
        self.assertTrue(calls and all("$" not in m["content"] for m in calls[0]))

    def test_reader_cannot_add_a_company(self):
        # a company the question does not mention is dropped, and the patterns answer instead
        form = {"shape": "company_value", "companies": [{"as_written": "Apple", "ticker": "AAPL"}]}
        d, _ = self._with_reader(form, lambda: self.c.get("/api/v1/us/ask", params={"q": "What is Microsoft worth?"}).json())
        self.assertEqual(d["entities"]["company"]["ticker"], "MSFT")
        self.assertEqual(d["match"]["via"], "patterns")

    def test_reader_lower_case_list_against_an_index(self):
        form = {"shape": "vs_benchmark", "benchmark": "sp500", "from": "2026-01-01",
                "companies": [{"as_written": "meta", "ticker": "META"}, {"as_written": "apple", "ticker": "AAPL"}]}
        d, _ = self._with_reader(form, lambda: self.c.get("/api/v1/us/ask", params={"q": "Which stocks meta, apple, underperformed the S&P 500"}).json())
        self.assertEqual(d["match"]["recipe"], "vs_benchmark")
        self.assertEqual([r[1] for r in d["table"]["rows"]], ["META", "AAPL"])
        self.assertIn("S&P 500", d["headline"])

    def test_reader_ranks_within_an_index(self):
        form = {"shape": "market_rank", "universe": "sp500", "rank_by": "price_change", "order": "desc", "count": 10,
                "from": "2026-01-01"}
        d, _ = self._with_reader(form, lambda: self.c.get("/api/v1/us/ask", params={"q": "top 10 performers of S&P in 2026"}).json())
        self.assertEqual(d["match"]["recipe"], "market_rank")
        self.assertEqual(len(d["table"]["rows"]), 10)
        self.assertIn("10 best performers among S&P 500 members", d["headline"])

    def test_reader_index_must_be_named(self):
        # the model calling "US companies" the total-market index must not turn a market ranking into a refusal
        form = {"shape": "market_rank", "universe": "total", "rank_by": "market_cap", "order": "desc", "count": 10}
        d, _ = self._with_reader(form, lambda: self.c.get("/api/v1/us/ask", params={"q": "The ten largest US companies by market value"}).json())
        self.assertEqual(d["match"]["recipe"], "market_rank", d.get("detail"))
        self.assertIn("US-listed companies", d["headline"])

    def test_benchmarks_and_contributions(self):
        # stocks against an index, by its fund
        d = self.c.get("/api/v1/us/ask", params={"q": "Did Meta and Apple underperform the S&P 500 in 2026?"}).json()
        self.assertEqual(d["match"]["recipe"], "vs_benchmark", d.get("detail"))
        self.assertEqual({r[1] for r in d["table"]["rows"]}, {"META", "AAPL"})
        self.assertTrue(all(r[5] in ("Outperformed", "Underperformed") for r in d["table"]["rows"]))
        self.assertEqual(round(d["table"]["rows"][0][2] - d["table"]["rows"][0][3], 2), d["table"]["rows"][0][4])
        # "Dow Jones" is the index, never Jones Lang LaSalle
        dj = self.c.get("/api/v1/us/ask", params={"q": "Did Apple beat the Dow Jones this year"}).json()
        self.assertEqual([r[1] for r in dj["table"]["rows"]], ["AAPL"])
        self.assertIn("Dow Jones Industrial Average", dj["headline"])
        # a stock's contribution to an index: weight x change, adding up to the members' total
        n = self.c.get("/api/v1/us/ask", params={"q": "how much of S&P 500 contribution in 2026 was caused by nvidia"}).json()
        self.assertEqual(n["match"]["recipe"], "index_contribution", n.get("detail"))
        self.assertIn("Nvidia", n["headline"])
        row = [r for r in n["table"]["rows"] if r[0] == "NVDA"][0]
        self.assertAlmostEqual(row[2] * row[3] / 100, row[4], delta=0.01)
        members_change = n["figures"][0]["value"]
        fund_change = n["figures"][1]["value"]
        self.assertLess(abs(members_change - fund_change), 2.5, "member-weighted change should sit near the fund's")
        # rankings within the Dow, and an honest refusal for an index whose members we do not hold
        w = self.c.get("/api/v1/us/ask", params={"q": "best performers in the Dow in 2026"}).json()
        self.assertIn("Dow Jones Industrial Average members", w["headline"])
        self.assertLessEqual(len(w["table"]["rows"]), 30)
        r = self.c.get("/api/v1/us/ask", params={"q": "What drove the Nasdaq 100 this year?"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("QQQ", r.text)

    def test_held_back_and_doubtful_values_say_why(self):
        """A value the box will not show, or shows on a count to read with care, says
        why and when it fixes itself; a split read only from prices says no filing confirms it."""
        from app import sec_api, sec_shares, stock_recipes
        cur = sec_api._require()
        rows = stock_recipes.all_metric_rows(cur)
        held = next((r for r in rows if (r.get("market_value_basis") or {}).get("method") == "held back"
                     and (r.get("price") or {}).get("close") and r.get("ticker")), None)
        if held:
            d = self.c.get("/api/v1/us/ask/run/company_value", params={"company": held["ticker"]}).json()
            self.assertIn("market value is not shown", d["headline"])
            self.assertIn(held["market_value_basis"]["note"], d["answer"])
        care = next((r for r in rows if (r.get("market_value_basis") or {}).get("caution") and r["metrics"].get("market_cap")
                     and r.get("ticker")), None)
        if care:
            d = self.c.get("/api/v1/us/ask/run/company_value", params={"company": care["ticker"]}).json()
            self.assertIn(care["market_value_basis"]["caution"], d["answer"])
        c = sec_shares.cautions(cur)
        split = next((t for t, v in c.items() if "no filing confirms" in v), None)
        if split:
            px = self.c.get("/api/v1/us/prices/%s" % split.replace("-", ".")).json()
            notes = [s_ for s_ in px.get("splits") or [] if s_.get("status") != "confirmed"]
            self.assertTrue(notes and "no filing confirms" in notes[0]["note"], px.get("splits"))

    def test_run_with_explicit_blanks(self):
        d = self.c.get("/api/v1/us/ask/run/industry_rank", params={"industry": "airlines", "metric": "operating margin"}).json()
        self.assertEqual(d["recipe"], "industry_rank")
        self.assertTrue(d["table"]["rows"])
        self.assertEqual(self.c.get("/api/v1/us/ask/run/nope").status_code, 404)
        self.assertEqual(self.c.get("/api/v1/us/ask/run/company_value", params={"company": "ZZZZQQ"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
