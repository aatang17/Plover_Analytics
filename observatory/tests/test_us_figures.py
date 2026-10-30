# -*- coding: utf-8 -*-
"""Everything Plover holds is answerable, and "we don't hold it" is never
taken on trust (app/us_figures.py, app/ask_coverage.py, app/ask_review.py).

2026-10-30: "Plover's data doesn't include capital expenditure figures", with
Alphabet's $91.4bn on the same page."""
import datetime
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import duckdb
from fastapi import HTTPException

from app import ask_miss, ask_review, sec_api, stock_recipes, us_figures, us_steps, us_tables

from . import _data


def setUpModule():
    os.environ["US_ASK_MODEL"] = "0"
    us_steps.LOG_FILE = os.path.join(tempfile.mkdtemp(), "us_ask_log.jsonl")


def tearDownModule():
    os.environ.pop("US_ASK_MODEL", None)


class CatalogueTest(unittest.TestCase):
    def test_every_figure_the_fixed_answers_read_reaches_the_model(self):
        cols = {c for c, _, _ in us_steps.STOCK_COLUMNS}
        annual = {c for c, _, _ in us_tables.TABLES["annual"][1]}
        ids = {e["id"]: e for e in us_figures.catalogue()}
        for f in stock_recipes.FIGURES:
            self.assertIn(f["id"], ids, "%s has no column" % f["id"])
        for field, _, _ in sec_api.FIELDS:
            self.assertIn(field, ids, "%s has no column" % field)
        for e in us_figures.catalogue():
            self.assertIn(e["col"], cols, "%s missing from stocks" % e["col"])
            if e["id"] not in us_figures._NOT_YEARLY:
                self.assertIn(e["col"], annual, "%s missing from annual" % e["col"])

    def test_the_prompt_names_every_figure(self):
        text = us_steps._table_doc()
        for e in us_figures.catalogue():
            self.assertIn(e["col"], text)
        self.assertIn("annual:", text)

    def test_column_names_are_unique_and_end_in_their_unit(self):
        cols = [e["col"] for e in us_figures.catalogue()]
        self.assertEqual(len(cols), len(set(cols)))
        for e in us_figures.catalogue():
            want = {"USD": "_usd", "USD/share": "_usd", "%": "_pct", "shares": ""}[e["unit"]]
            self.assertTrue(e["col"].endswith(want), e["col"])

    def test_the_rules_never_list_a_held_figure_as_missing(self):
        with open(us_steps.RULES_FILE, encoding="utf-8") as f:
            line = next(x for x in f if x.startswith("- Figures the tables do not hold:"))
        for name in re.split(r",\s*", line.split(":", 1)[1].split(".")[0]):
            self.assertEqual(us_figures.held_in(us_figures._GAP.sub(" ", name)), [], name)

    def test_worked_refusals_stand(self):
        """A worked example that teaches a refusal must itself pass the check."""
        for ex in us_steps.examples(datetime.date(2026, 10, 30)):
            c = ex.get("cannot")
            if c and c != "unclear":
                self.assertEqual(us_figures.judge(c, ex["question"])["verdict"], "gap", c)

    def test_worked_examples_only_use_columns_that_exist(self):
        cols = {c for c, _, _ in us_steps.STOCK_COLUMNS} | {c for c, _, _ in us_tables.TABLES["annual"][1]}
        for ex in us_steps.examples(datetime.date(2026, 10, 30)):
            for hit in re.findall(r"\b\w+_usd\b", (ex.get("sql") or "")):
                if " AS " + hit not in ex["sql"]:
                    self.assertIn(hit, cols | {c for _, (_, tc) in us_tables.TABLES.items() for c, _, _ in tc}, ex["question"])


class JudgeTest(unittest.TestCase):
    def verdict(self, sentence, question=""):
        return us_figures.judge(sentence, question)["verdict"]

    def test_the_refusal_that_started_this(self):
        self.assertEqual(self.verdict("Plover's data doesn't include capital expenditure figures.", "Alphabet capex share"), "held")
        names = [n["label"] for n in us_figures.judge("Plover's data doesn't include capital spending or operating cash flow as separate figures.")["names"]]
        self.assertIn("capital expenditure", names)
        self.assertIn("operating cash flow", names)

    def test_other_held_figures(self):
        for s in ("Plover's data doesn't include research spending.", "Plover's data doesn't hold goodwill.",
                  "Plover's data doesn't include dividends per share history.", "No figures for inventories are held."):
            self.assertEqual(self.verdict(s), "held", s)

    def test_true_gaps_stand(self):
        for s in ("Plover's data doesn't include employee counts, so it can't rank companies by them.",
                  "Plover's data doesn't include analyst ratings.", "Plover's data doesn't include short interest.",
                  "Plover's data doesn't include intraday prices.", "Plover doesn't hold subscriber counts.",
                  "EBITDA is not a filed line; I can show operating income and depreciation separately."):
            self.assertEqual(self.verdict(s), "gap", s)

    def test_what_it_offers_instead_is_not_what_it_says_is_missing(self):
        self.assertEqual(self.verdict("Plover's data doesn't include backlog; it does hold revenue and capital expenditure."), "gap")
        self.assertEqual(self.verdict("I don't explain why a price moved; I can show what the company reported."), "gap")

    def test_operating_is_not_a_rating(self):
        self.assertIn("operating cash flow", [n["label"] for n in us_figures.judge("Plover's data doesn't include operating cash flow.")["names"]])

    def test_a_vague_refusal_is_checked_against_the_question(self):
        self.assertEqual(self.verdict("Plover's data doesn't hold what that question needs.", "Alphabet capex to something"), "held")
        self.assertEqual(self.verdict("Plover's data doesn't hold what that question needs.", "how tall is the CEO"), "unsure")


class _Cur:
    """Enough of a cursor for us_steps.answer's first lines."""
    def execute(self, *a):
        raise AssertionError("not reached")


class RefusalCheckTest(unittest.TestCase):
    """The model refuses; the server decides whether to believe it."""

    def answer_with(self, plans):
        plans = list(plans)
        sent = []

        def fake(messages):
            sent.append(messages[-1]["content"])
            return plans.pop(0), "raw"
        patches = [mock.patch.object(us_steps, "_ask_model", fake), mock.patch.object(sec_api, "_has", lambda c, t: True),
                   mock.patch.object(us_steps.us_prices_api, "latest_date", lambda c: datetime.date(2026, 10, 30)),
                   mock.patch.object(us_steps, "prompt", lambda q, last=None: "p")]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return sent, lambda q: us_steps.answer(_Cur(), q)

    def test_refusing_a_held_figure_twice_never_tells_the_reader_it_is_missing(self):
        refuse = {"cannot": "Plover's data doesn't include capital expenditure figures.", "sql": ""}
        sent, ask = self.answer_with([refuse, refuse, refuse])
        with self.assertRaises(HTTPException) as cm:
            ask("Alphabet capex as a share of revenue")
        self.assertEqual(cm.exception.miss, "unsure")
        self.assertEqual(cm.exception.detail, ask_miss.UNSURE)
        self.assertNotIn("capital expenditure", cm.exception.detail)
        self.assertIn("capex_usd", sent[1], "the model was told where the figure is")

    def test_a_true_gap_reaches_the_reader_at_once(self):
        sent, ask = self.answer_with([{"cannot": "Plover's data doesn't include analyst ratings.", "sql": ""}])
        with self.assertRaises(HTTPException) as cm:
            ask("Who has the best analyst ratings?")
        self.assertEqual(cm.exception.miss, "not_held")
        self.assertEqual(len(sent), 1, "no second call for a true gap")

    def test_a_refusal_it_cannot_check_is_not_repeated_as_fact(self):
        sent, ask = self.answer_with([{"cannot": "Plover's data doesn't include the colour of the logo.", "sql": ""}])
        with self.assertRaises(HTTPException) as cm:
            ask("what colour is the logo")
        self.assertEqual(cm.exception.miss, "unsure")

    def test_the_reader_is_offered_the_figure_they_asked_about(self):
        with mock.patch.object(ask_miss, "company_in", lambda cur, q: "Alphabet"):
            tries = ask_miss.suggestions("Alphabet capex to something", "unsure", "", None)
        self.assertEqual(tries[0], "What is Alphabet's capital expenditure?")


class OtherMarketTest(unittest.TestCase):
    def test_a_share_of_revenue_is_not_a_chinese_a_share(self):
        """2026-10-30: every "as a share of" question through the model was told the box covers US companies only."""
        from app import us_checks
        self.assertIsNone(us_checks.other_market("Alphabet capex as a share of revenue"))
        self.assertIsNone(us_checks.other_market("Nvidia buybacks as a share of revenue"))
        self.assertIsNotNone(us_checks.other_market("Largest A-shares by market value"))
        self.assertIsNotNone(us_checks.other_market("Best China A shares this year"))
        self.assertIsNotNone(us_checks.other_market("Largest Shanghai stocks"))

    def test_one_line_against_another_is_worked_out(self):
        self.assertTrue(stock_recipes._RATIO_ASK.search("alphabet capex as a share of revenue over the last 5 years"))
        self.assertFalse(stock_recipes._RATIO_ASK.search("alphabet capex over the last 5 years"))
        self.assertTrue(stock_recipes._named_ratio("nvidia r&d as a share of revenue"))
        self.assertFalse(stock_recipes._named_ratio("nvidia capex as a share of revenue"))


class LaterFixesTest(unittest.TestCase):
    def test_a_ratio_on_a_sliver_of_a_base_does_not_rank(self):
        rd = stock_recipes.FIGURE_BY_ID["rd_intensity"]
        self.assertFalse(stock_recipes._ratio_is_sound(rd, 164633.0, {"size": {"revenue": 1e6}}, 1e6))
        self.assertTrue(stock_recipes._ratio_is_sound(rd, 20.0, {"size": {"revenue": 1e9}}, 1e9))
        tax = stock_recipes.FIGURE_BY_ID["tax_rate"]
        self.assertFalse(stock_recipes._ratio_is_sound(tax, 120.0, {"size": {}}, 5e9))
        self.assertFalse(stock_recipes._ratio_is_sound(tax, 20.0, {"size": {}}, 1e6))
        self.assertTrue(stock_recipes._ratio_is_sound(tax, 20.0, {"size": {}}, 5e9))
        de = stock_recipes.FIGURE_BY_ID["debt_to_equity"]
        self.assertFalse(stock_recipes._ratio_is_sound(de, 12724.0, {"size": {"equity": 1e7, "total_assets": 1e10}}, 1e7))

    def test_a_quarterly_line_we_do_not_hold_is_a_true_gap(self):
        self.assertEqual(us_figures.judge("Plover's data doesn't include quarterly financing cash flow.")["verdict"], "gap")
        self.assertEqual(us_figures.judge("Plover's data doesn't include quarterly revenue.")["verdict"], "held")

    def test_a_blank_never_reaches_a_headline(self):
        self.assertEqual(us_steps._blank_in_headline("spent {a_pct} of {b_usd}", ["a_pct", "b_usd"], [[None, 5.0]]), ["a_pct"])
        self.assertEqual(us_steps._blank_in_headline("spent {a_pct}", ["a_pct"], [[1.0]]), [])
        said = us_steps._not_filed_sentence(["Taiwan Semiconductor", None], ["name", "capex_usd"], ["capex_usd"])
        self.assertIn("capital expenditure", said)
        self.assertIn("Taiwan Semiconductor", said)

    def test_a_line_the_quarters_lack_is_not_answered_with_revenue(self):
        self.assertTrue(stock_recipes._not_a_quarterly_line("apple financing cash flow last quarter"))
        self.assertFalse(stock_recipes._not_a_quarterly_line("apple revenue last quarter"))
        self.assertFalse(stock_recipes._not_a_quarterly_line("apple operating income last quarter"))

    def test_markets_without_a_cursor(self):
        from app import us_checks
        self.assertTrue(us_checks.about_us_listed("Which US companies make the most revenue in India?"))
        self.assertFalse(us_checks.about_us_listed("Best Japanese banks"))
        self.assertFalse(us_checks.about_us_listed("Largest companies in Japan by sales"))
        self.assertIsNotNone(us_steps.other_market("best japanese banks"))

    def test_the_review_takes_over_a_day_a_deploy_cut_short(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ask_review.ask_log, "ANALYTICS_DIR", __import__("pathlib").Path(d)), \
                mock.patch.object(ask_review, "_STARTED", 0), mock.patch.object(ask_review, "_safely", lambda: None):
            now = datetime.datetime(2026, 10, 30, tzinfo=datetime.timezone.utc)
            self.assertTrue(ask_review.run_if_due(now))
            self.assertFalse(ask_review.run_if_due(now))
            old = 1
            os.utime(str(ask_review._lock_path("2026-10-30")), (old, old))
            self.assertTrue(ask_review.run_if_due(now))


class RankingWordsTest(unittest.TestCase):
    def line(self, q):
        hit = stock_recipes._named_line(q.lower())
        return hit["id"] if hit else None

    def test_the_longest_line_wins(self):
        self.assertEqual(self.line("highest non-interest expense"), "noninterest_expense")
        self.assertEqual(self.line("largest gross profit"), "gross_profit")
        self.assertEqual(self.line("biggest debt"), "debt")
        self.assertEqual(self.line("highest income taxes paid"), "taxes_paid")
        self.assertEqual(self.line("most operating cash flow"), "cf_operating")

    def test_a_measure_named_first_stays_that_measure(self):
        self.assertIsNone(self.line("highest return on equity"))
        self.assertIsNone(self.line("best buyback yield"))

    def test_a_ranking_on_any_line_is_not_refused(self):
        for q, want in (("Which S&P 500 company has the highest total assets?", "fig:total_assets"),
                        ("Which company has the most operating cash flow?", "fig:cf_operating"),
                        ("Largest companies by total liabilities", "fig:total_liabilities")):
            self.assertEqual(stock_recipes._metric(q)[0], want, q)

    def test_figure_counts_read_as_read(self):
        self.assertEqual(stock_recipes.unread_words(None, "Which S&P 500 company has the highest gross profit?",
                                                    stock_recipes._RANK_READS["market_rank"]), [])


class AnnualTableTest(unittest.TestCase):
    """The yearly table, on a small database of invented filings."""

    @classmethod
    def setUpClass(cls):
        cls.cur = con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sec_filings (adsh VARCHAR, cik BIGINT, form VARCHAR, status VARCHAR, filed DATE, period DATE, sic INTEGER)")
        con.execute("CREATE TABLE sec_facts (adsh VARCHAR, tag VARCHAR, ddate DATE, qtrs INTEGER, uom VARCHAR, value DOUBLE, segment_id VARCHAR, coreg VARCHAR)")
        con.execute("CREATE TABLE sec_cf_facts (cik BIGINT, tag VARCHAR, unit VARCHAR, period_start DATE, period_end DATE, value DOUBLE, accn VARCHAR, fy INTEGER, fp VARCHAR, form VARCHAR, filed DATE)")
        con.execute("INSERT INTO sec_filings VALUES ('a25', 1, '10-K', 'clean', '2026-02-01', '2025-12-31', 7370), ('b25', 2, '10-K', 'clean', '2026-02-01', '2025-12-31', 6022), "
                    "('c25', 3, '20-F', 'clean', '2026-03-01', '2025-12-31', 3711)")
        facts = [("a25", "RevenueFromContractWithCustomerExcludingAssessedTax", "2025-12-31", 4, "USD", 1000.0),
                 ("a25", "RevenueFromContractWithCustomerExcludingAssessedTax", "2024-12-31", 4, "USD", 800.0),
                 ("a25", "PaymentsToAcquirePropertyPlantAndEquipment", "2025-12-31", 4, "USD", 250.0),
                 ("a25", "NetCashProvidedByUsedInOperatingActivities", "2025-12-31", 4, "USD", 400.0),
                 ("a25", "GrossProfit", "2025-12-31", 4, "USD", 600.0),
                 ("a25", "Assets", "2025-12-31", 0, "USD", 5000.0), ("a25", "Assets", "2025-06-30", 0, "USD", 4000.0),
                 ("b25", "InterestAndDividendIncomeOperating", "2025-12-31", 4, "USD", 90.0), ("b25", "NoninterestIncome", "2025-12-31", 4, "USD", 10.0),
                 ("c25", "Revenue", "2025-12-31", 4, "EUR", 777.0)]
        for f in facts:
            con.execute("INSERT INTO sec_facts VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)", list(f))
        # company facts: a second, larger "Revenues" the company's statements do not use, and an older year
        con.execute("INSERT INTO sec_cf_facts VALUES (1, 'Revenues', 'USD', '2025-01-01', '2025-12-31', 1200.0, 'x', 2025, 'FY', '10-K', '2026-02-01'), "
                    "(1, 'RevenueFromContractWithCustomerExcludingAssessedTax', 'USD', '2015-01-01', '2015-12-31', 100.0, 'y', 2015, 'FY', '10-K', '2016-02-01'), "
                    "(1, 'Revenues', 'USD', '2015-01-01', '2015-12-31', 130.0, 'y', 2015, 'FY', '10-K', '2016-02-01'), "
                    "(1, 'PaymentsToAcquirePropertyPlantAndEquipment', 'USD', '2015-01-01', '2015-12-31', 20.0, 'y', 2015, 'FY', '10-K', '2016-02-01'), "
                    "(1, 'Assets', 'USD', NULL, '2923-12-31', 9.0, 'z', 2025, 'FY', '10-K', '2026-02-01')")
        real = sec_api._has
        cls._p = mock.patch.object(sec_api, "_has", lambda c, t: c is con and t in ("sec_facts", "sec_filings", "sec_cf_facts") or (c is not con and real(c, t)))
        cls._p.start()
        cls.rows = {(r["ticker"], r["fiscal_year"]): r for r in us_figures.annual_rows(con, {1: "AAA", 2: "BNK", 3: "EUR"}, datetime.date(2026, 10, 30))}

    @classmethod
    def tearDownClass(cls):
        cls._p.stop()

    def test_a_ratio_of_two_filed_lines(self):
        r = self.rows[("AAA", 2025)]
        self.assertEqual(r["capex_usd"], 250.0)
        self.assertEqual(r["gross_margin_pct"], 60.0)
        self.assertEqual(r["fcf_usd"], 150.0)

    def test_the_companys_own_revenue_line_wins_in_every_year(self):
        self.assertEqual(self.rows[("AAA", 2025)]["revenue_usd"], 1000.0)
        self.assertEqual(self.rows[("AAA", 2015)]["revenue_usd"], 100.0)

    def test_a_banks_revenue_is_composed_as_on_the_statements(self):
        self.assertEqual(self.rows[("BNK", 2025)]["revenue_usd"], 100.0)

    def test_missing_is_blank_never_zero(self):
        self.assertIsNone(self.rows[("AAA", 2024)]["capex_usd"])
        self.assertIsNone(self.rows[("AAA", 2015)]["fcf_usd"])

    def test_another_currency_is_left_out(self):
        self.assertNotIn(("EUR", 2025), self.rows)

    def test_a_filers_typo_date_and_a_mid_year_balance_are_left_out(self):
        self.assertFalse([k for k in self.rows if k[1] > 2026])
        self.assertEqual(self.rows[("AAA", 2025)]["total_assets_usd"], 5000.0)

    def test_latest_year_is_marked(self):
        self.assertTrue(self.rows[("AAA", 2025)]["is_latest"])
        self.assertFalse(self.rows[("AAA", 2015)]["is_latest"])


class ReviewTest(unittest.TestCase):
    def test_refused_questions_are_sorted(self):
        self.assertEqual(ask_review.sort_miss("Alphabet capex as a share of revenue", "not_held"), "fix")
        self.assertEqual(ask_review.sort_miss("Alphabet capex as a share of revenue", "unsure"), "fix")
        self.assertEqual(ask_review.sort_miss("how many employees does the S&P 500 have", "not_held"), "gap")
        self.assertEqual(ask_review.sort_miss("should I buy Apple", "policy"), "fine")
        self.assertEqual(ask_review.sort_miss("asdf", "unclear"), "fine")
        self.assertEqual(ask_review.sort_miss("how tall is the CEO", "not_held"), "look")

    def test_a_question_that_now_answers_is_marked_fixed(self):
        def ask(q):
            if "capex" in q:
                return {"headline": "It spent $91.4bn."}
            raise ask_miss.miss("Plover's data doesn't include analyst ratings.", "not_held")
        done = ask_review.replay(None, [{"q": "Alphabet capex"}, {"q": "analyst ratings for Apple"}], ask=ask)
        self.assertEqual(done[0]["pile"], "fixed")
        self.assertEqual(done[1]["pile"], "gap")

    def test_a_why_question_is_not_asked_again(self):
        def ask(q):
            raise AssertionError("a paid news search for nothing")
        self.assertEqual(ask_review.replay(None, [{"q": "why did Intel rise", "kind": "policy"}], ask=ask)[0]["pile"], "fine")

    def test_the_check_turns_the_model_off_for_itself_only(self):
        import threading
        from app import us_ask_model
        seen = {}
        with mock.patch.dict(os.environ, {"US_ASK_MODEL": "", "DEEPSEEK_API_KEY": "k"}):
            with us_ask_model.no_model():
                self.assertIsNone(us_ask_model.provider())
                t = threading.Thread(target=lambda: seen.update(other=us_ask_model.provider()))
                t.start()
                t.join()
            self.assertIsNotNone(seen["other"], "a reader on another thread keeps the model")
            self.assertIsNotNone(us_ask_model.provider())

    def test_one_failure_never_stops_the_review(self):
        def ask(q):
            raise ValueError("boom")
        self.assertEqual(ask_review.replay(None, [{"q": "x"}], ask=ask)[0]["pile"], "fix")

    def test_only_readers_questions_count(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ask_review.ask_log, "ANALYTICS_DIR", __import__("pathlib").Path(d)):
            path = ask_review.ask_log._path_of("2026-10")
            with open(str(path), "w", encoding="utf-8") as f:
                for line in ({"at": "2026-10-30T01:00:00Z", "q": "alphabet capex", "answered": False, "route": "not_held", "from": "reader"},
                             {"at": "2026-10-30T01:05:00Z", "q": "our own check", "answered": False, "route": "not_held", "from": "us"},
                             {"at": "2026-10-30T01:10:00Z", "q": "apple revenue", "answered": True, "route": "quick", "from": "reader"}):
                    f.write(json.dumps(line) + "\n")
            rows = ask_review.refused(today=datetime.date(2026, 10, 30))
        self.assertEqual([r["q"] for r in rows], ["alphabet capex"])

    def test_the_message_says_nothing_when_nothing_is_wrong(self):
        report = {"day": "2026-10-30", "questions": [{"q": "x", "pile": "gap"}],
                  "matrix": {"code_asked": 9, "code_failed": [], "model_asked": 3, "model_failed": []}}
        self.assertIsNone(ask_review.summary_text(report))
        report["questions"].append({"q": "Alphabet capex", "pile": "fix"})
        self.assertIn("Alphabet capex", ask_review.summary_text(report))

    def test_a_day_runs_once_however_many_servers_ask(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ask_review.ask_log, "ANALYTICS_DIR", __import__("pathlib").Path(d)), \
                mock.patch.object(ask_review, "_STARTED", 0), mock.patch.object(ask_review, "_safely", lambda: None):
            now = datetime.datetime(2026, 10, 30, tzinfo=datetime.timezone.utc)
            self.assertTrue(ask_review.run_if_due(now))
            self.assertFalse(ask_review.run_if_due(now))


@unittest.skipUnless(_data.SEC, "data/sec.duckdb not built")
class LiveFiguresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cur = sec_api._require()

    def run_sql(self, sql):
        rows, cols = us_steps.run(self.cur, sql, datetime.date(2026, 1, 1))
        return [dict(zip(cols, r)) for r in rows]

    def test_alphabet_capex_is_the_figure_the_fixed_answer_gives(self):
        fixed = stock_recipes.answer(self.cur, "What is Alphabet's capital expenditure?")
        value = fixed["figures"][0]["value"]
        self.assertEqual(self.run_sql("SELECT capex_usd FROM stocks WHERE ticker = 'GOOGL'")[0]["capex_usd"], value)
        self.assertEqual(self.run_sql("SELECT capex_usd FROM annual WHERE ticker = 'GOOGL' AND is_latest")[0]["capex_usd"], value)

    def test_stocks_agrees_with_the_fixed_figures(self):
        rows = {r["ticker"]: r for r in us_steps.base_rows(self.cur)}
        tickers = {r["cik"]: r["ticker"] for r in stock_recipes.all_metric_rows(self.cur) if r.get("ticker")}
        for fid in ("capex", "rd", "gross_profit", "inventory", "sga"):
            col = next(e["col"] for e in us_figures.catalogue() if e["id"] == fid)
            wrong = [t for cik, (v, _) in stock_recipes.figure_cross(self.cur, stock_recipes.FIGURE_BY_ID[fid]).items()
                     for t in [tickers.get(cik)] if t in rows and (rows[t].get(col) is None or abs(rows[t][col] - v) > 1)]
            self.assertLessEqual(len(wrong), 3, "%s: %s" % (fid, wrong[:5]))

    def test_annual_agrees_with_stocks_on_the_same_year(self):
        got = self.run_sql("""SELECT count(*) AS n,
                 count(*) FILTER (WHERE abs(a.revenue_usd - s.revenue_usd) > 1) AS revenue,
                 count(*) FILTER (WHERE abs(a.capex_usd - s.capex_usd) > 1) AS capex
               FROM annual a JOIN stocks s USING (ticker)
               WHERE s.in_sp500 AND a.fiscal_year_end = last_day(s.fiscal_year_end)""")[0]
        self.assertGreater(got["n"], 400)
        self.assertLessEqual(got["revenue"], 3)
        self.assertLessEqual(got["capex"], 3)

    def test_one_row_per_company_per_year(self):
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM (SELECT ticker, fiscal_year FROM annual GROUP BY 1, 2 HAVING count(*) > 1)")[0]["n"], 0)

    def test_a_country_in_a_named_companys_question_is_not_another_market(self):
        from app import us_checks
        for q in ("Apple revenue from Japan", "Nvidia revenue from Taiwan as a share of revenue", "Taiwan Semiconductor capex as a share of revenue",
                  "British American Tobacco debt as a share of equity", "Sony capex vs revenue"):
            self.assertIsNone(us_checks.other_market(q, self.cur), q)
            self.assertIsNone(us_steps.other_market(q, self.cur), q)
        for q in ("best japanese banks", "largest UK companies", "Nikkei performance this year", "Hong Kong stocks", "Largest companies in Japan"):
            self.assertIsNotNone(us_steps.other_market(q, self.cur) or us_checks.other_market(q, self.cur), q)

    def test_with_the_model_off_a_line_is_never_answered_as_something_else(self):
        for q in ("Nvidia capital expenditure as a share of revenue", "Alphabet capex vs net income"):
            try:
                out = stock_recipes.answer(self.cur, q)
            except HTTPException as exc:
                self.assertEqual(exc.miss, "unsure", q)
                continue
            self.assertIn("capital expenditure", out["headline"].lower(), q)

    def test_dates_on_a_filed_line(self):
        out = stock_recipes.answer(self.cur, "Compare total assets of Apple and Microsoft in 2022")
        self.assertNotIn("no average", out["headline"])
        self.assertIn("2022", out["headline"])
        self.assertEqual(stock_recipes.line_match(self.cur, "Apple financing cash flow last quarter"), None)
        out = stock_recipes.answer(self.cur, "Apple capex this year")
        self.assertIn("capital expenditure", out["headline"])

    def test_share_counts_fill_as_the_market_value_uses_them(self):
        got = self.run_sql("SELECT count(*) AS n, count(shares_outstanding) AS have FROM stocks WHERE in_sp500")[0]
        self.assertGreater(got["have"], 0.9 * got["n"])

    def test_rankings_on_a_ratio_are_sane(self):
        for q, top in (("Which company has the highest R&D intensity?", 1000), ("Which company has the highest effective tax rate?", 100.01)):
            out = stock_recipes.answer(self.cur, q)
            value = out["table"]["rows"][0][4]
            self.assertLessEqual(value, top, q)

    def test_every_figure_answers_however_it_is_asked(self):
        from app import ask_coverage
        res = ask_coverage.check_code(self.cur)
        self.assertGreater(res["asked"], 150)
        self.assertEqual(res["failed"], [], "\n".join("%s: %s" % (f.get("question") or f.get("col"), f["why"]) for f in res["failed"]))
