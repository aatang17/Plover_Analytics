# -*- coding: utf-8 -*-
"""run_analysis: the AI's query runs on the server, not in the conversation.

What is proved: each helper word expands to its one formula and gives the
right numbers; missing stays missing (no helper fills a gap, a division by
zero is blank, a query that turns blanks into zero is refused); the query can
only read, cannot reach a file or unlock itself, and gives up after its time
limit; "highest since" finds the last date that was matched; and from the AI
tab a chart is written into the draft by code while the model gets back only
a short reply, and an answer-only run cannot add one.
"""
import json
import unittest

from app import analysis, research, research_ai
from app.assistant import gateway

from tests.test_research_ai import AIBase, Fake, call, say

COLS = ["date", "a", "b"]


def months():
    """24 months of a level rising by 1 from 100, and b with a gap in May 2024."""
    rows = []
    for i in range(24):
        y, m = 2024 + i // 12, i % 12 + 1
        rows.append(["%d-%02d-01" % (y, m), 100.0 + i, None if (y, m) == (2024, 5) else 2.0 + i / 10.0])
    return rows


def q(sql, rows=None):
    res, cols = analysis.run_query(rows or months(), COLS, sql)
    return [dict(zip(cols, r)) for r in res]


class HelperTest(unittest.TestCase):
    def test_yoy_mom_and_changes(self):
        last = q("SELECT date, yoy(a) y, mom(a) m, pct_change(a, 1) p, change(a, 2) c, "
                 "diff_pp(b, 1) d FROM data ORDER BY date")[-1]
        self.assertAlmostEqual(last["y"], (123.0 / 111.0 - 1) * 100)
        self.assertAlmostEqual(last["m"], (123.0 / 122.0 - 1) * 100)
        self.assertAlmostEqual(last["p"], last["m"])
        self.assertEqual(last["c"], 2.0)
        self.assertAlmostEqual(last["d"], 0.1)

    def test_yoy_needs_the_month_a_year_earlier(self):
        rows = [r for r in months() if r[0] != "2024-03-01"]   # a missing month
        got = dict((r["date"], r["y"]) for r in q("SELECT date, yoy(a) y FROM data", rows))
        self.assertIsNone(got["2025-03-01"])          # no March 2024: blank, not a shifted month
        self.assertAlmostEqual(got["2025-04-01"], (115.0 / 103.0 - 1) * 100)
        self.assertIsNone(got["2024-12-01"])          # the first year has no year-earlier value

    def test_combinations_and_rebase(self):
        r = q("SELECT date, spread_pp(b, 1) s, ratio(a, b) r, share(b, a) sh, "
              "rebase(a, '2024-06-01') rb FROM data ORDER BY date")
        june = [x for x in r if x["date"] == "2024-06-01"][0]
        self.assertAlmostEqual(june["s"], 1.5)
        self.assertAlmostEqual(june["rb"], 100.0)
        self.assertAlmostEqual(june["r"], 105.0 / 2.5)
        self.assertAlmostEqual(june["sh"], 2.5 / 105.0 * 100)

    def test_rolling_is_blank_unless_every_row_is_there(self):
        got = dict((r["date"], (r["m"], r["s"])) for r in
                   q("SELECT date, rolling_mean(b, 3) m, rolling_sum(b, 3) s FROM data"))
        self.assertEqual(got["2024-06-01"], (None, None))     # window holds the May gap
        self.assertEqual(got["2024-02-01"], (None, None))     # only two rows so far
        self.assertAlmostEqual(got["2024-03-01"][0], 2.1)
        self.assertAlmostEqual(got["2024-08-01"][1], 2.5 + 2.6 + 2.7)

    def test_cumulative(self):
        r = q("SELECT date, cumulative(a) c FROM data ORDER BY date")
        self.assertEqual(r[2]["c"], 100.0 + 101.0 + 102.0)

    def test_one_look_back_on_another_takes_two_steps(self):
        want = (123.0 / 111.0 - 1) * 100 - (122.0 / 110.0 - 1) * 100
        r = q("WITH s AS (SELECT date, yoy(a) AS g FROM data) SELECT date, diff_pp(g, 1) d "
              "FROM s ORDER BY date")
        self.assertAlmostEqual(r[-1]["d"], want)
        with self.assertRaises(analysis.AnalysisError) as cm:
            q("SELECT date, diff_pp(yoy(a), 1) d FROM data")
        self.assertIn("two steps: WITH s AS", str(cm.exception))

    def test_wrong_arguments_are_explained(self):
        for sql, words in (("SELECT yoy(a, 2) FROM data", "takes 1 argument"),
                           ("SELECT pct_change(a, b) FROM data", "whole number"),
                           ("SELECT yoy(a FROM data", "not closed")):
            with self.assertRaises(analysis.AnalysisError) as cm:
                q(sql)
            self.assertIn(words, str(cm.exception))


class SafetyTest(unittest.TestCase):
    def refused(self, sql, words):
        with self.assertRaises(analysis.AnalysisError) as cm:
            q(sql)
        self.assertIn(words, str(cm.exception))

    def test_missing_is_never_zero(self):
        self.refused("SELECT coalesce(b, 0) FROM data", "never 0")
        self.refused("SELECT IFNULL(b,0.0) FROM data", "never 0")
        self.assertIsNone(q("SELECT 1.0 / 0 AS z")[0]["z"])        # not infinity
        self.assertIsNone(q("SELECT b FROM data WHERE date = '2024-05-01'")[0]["b"])

    def test_read_only_one_statement(self):
        self.refused("DROP TABLE data", "one SELECT")
        self.refused("SELECT 1; SELECT 2", "one SELECT")
        self.refused("SET enable_external_access=true", "one SELECT")

    def test_no_files_and_no_unlocking(self):
        self.refused("SELECT * FROM read_csv('/etc/hosts')", "query failed")
        self.refused("SELECT * FROM 'data/observatory.duckdb'", "query failed")
        self.refused("WITH x AS (SELECT current_setting('enable_external_access') v) "
                     "SELECT * FROM read_text('/etc/passwd')", "query failed")

    def test_time_limit(self):
        old = analysis.SECONDS_MAX
        analysis.SECONDS_MAX = 0.2
        self.addCleanup(setattr, analysis, "SECONDS_MAX", old)
        self.refused("SELECT count(*) FROM range(1000000000) a, range(1000) b", "longer than")

    def test_empty_query_is_every_row(self):
        self.assertEqual(len(q("")), 24)


class SummaryTest(unittest.TestCase):
    def test_highest_since_and_figures(self):
        rows = [["2020-01-01", 3.0], ["2020-02-01", 1.0], ["2020-03-01", 2.5], ["2020-04-01", 2.0],
                ["2020-05-01", 2.8]]
        got = analysis.summarise(rows, ["date", "x"], [{"column": "x", "stats": [
            "latest", "min", "max", "mean", "median", "count", "highest_since", "lowest_since"]}])["x"]
        self.assertEqual(got["latest"], {"value": 2.8, "at": "2020-05-01"})
        self.assertEqual(got["min"], {"value": 1.0, "at": "2020-02-01"})
        self.assertEqual(got["max"]["at"], "2020-01-01")
        self.assertAlmostEqual(got["mean"], 11.3 / 5)
        self.assertEqual(got["median"], 2.5)
        self.assertEqual(got["count"], 5)
        self.assertEqual(got["highest_since"]["since"], "2020-01-01")     # 3.0 ≥ 2.8
        self.assertEqual(got["highest_since"]["readings_between"], 3)
        self.assertEqual(got["lowest_since"]["since"], "2020-04-01")      # 2.0 ≤ 2.8, one reading back
        none = analysis.summarise([["2020-01-01", 1.0], ["2020-02-01", 3.0]], ["date", "x"], [{"column": "x", "stats": ["highest_since"]}])
        self.assertIsNone(none["x"]["highest_since"]["since"])           # never matched: says so

    def test_unknown_column_or_stat(self):
        with self.assertRaises(analysis.AnalysisError):
            analysis.summarise([["2020-01-01", 1.0]], ["date", "x"], [{"column": "y"}])
        with self.assertRaises(analysis.AnalysisError):
            analysis.summarise([["2020-01-01", 1.0]], ["date", "x"], [{"column": "x", "stats": ["mode"]}])


SOURCES = [{"name": "a", "series": "test:A", "title": "Series A", "unit": "%", "release": "Data through May",
            "release_id": 1, "credit": "Source: An Agency."}]


class ChartTest(unittest.TestCase):
    def test_chart_markdown_written_by_code(self):
        md, n = analysis.chart_markdown(months(), COLS, {"columns": ["a", "b"], "title": "A and B",
                                                         "unit": "%"}, SOURCES, "SELECT * FROM data")
        self.assertEqual(n, 24)
        self.assertTrue(md.startswith("![A and B](chart:line?unit=%25)"))
        self.assertIn("| 2024-05-01 | 104 |  |", md)                   # the gap stays blank
        self.assertIn("Source: An Agency. Calculated by Plover", md)
        self.assertIn("Calculation: SELECT * FROM data", md)

    def test_chart_limits(self):
        with self.assertRaises(analysis.AnalysisError):
            analysis.chart_markdown(months(), COLS, {"columns": ["z"]}, SOURCES, "")
        old = analysis.CHART_ROWS_MAX
        analysis.CHART_ROWS_MAX = 10
        self.addCleanup(setattr, analysis, "CHART_ROWS_MAX", old)
        with self.assertRaises(analysis.AnalysisError) as cm:
            analysis.chart_markdown(months(), COLS, {"columns": ["a"]}, SOURCES, "")
        self.assertIn("monthly", str(cm.exception))


class MonthlyTest(unittest.TestCase):
    def test_daily_to_monthly_rules(self):
        pts = [("2024-01-02", 1.0), ("2024-01-31", 3.0), ("2024-02-01", 5.0), ("2024-02-29", None)]
        self.assertEqual(analysis._to_monthly(pts, "average"), [("2024-01-01", 2.0), ("2024-02-01", 5.0)])
        self.assertEqual(analysis._to_monthly(pts, "end"), [("2024-01-01", 3.0), ("2024-02-01", None)])
        self.assertEqual(analysis._to_monthly(pts, "sum"), [("2024-01-01", 4.0), ("2024-02-01", None)])


class AITabTest(AIBase):
    def setUp(self):
        super(AITabTest, self).setUp()
        self._load = analysis.load
        analysis.load = lambda *a, **k: (months(), COLS, SOURCES)
        self.addCleanup(setattr, analysis, "load", self._load)

    def test_chart_lands_in_the_draft_and_the_model_reads_little(self):
        self.connect()
        fake = Fake([call("run_analysis", series={"a": "test:A", "b": "test:B"},
                          query="SELECT date, a, b, yoy(a) AS growth FROM data",
                          summary=[{"column": "growth", "stats": ["latest"]}],
                          chart={"columns": ["a", "b"], "title": "A and B", "unit": "%"}),
                     say("Added the chart.")])
        seen = []
        real = fake.__call__

        def spy(provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
            seen.append([m for m in messages if m["role"] == "tool"])
            return real(provider, key, model, messages, tools, max_tokens, base_url)
        gateway.complete = spy
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": "Chart A and B."})
        self.assertEqual(r.status_code, 200, r.text)
        j = self.wait(r.json()["id"])
        self.assertEqual(j["status"], "done", j)
        self.assertIn("run_analysis", fake.seen_tools)
        reply = json.loads(seen[-1][-1]["content"])
        self.assertEqual(reply["rows"], 24)
        self.assertIn("Do not type its numbers", reply["chart"])
        self.assertAlmostEqual(reply["summary"]["growth"]["latest"]["value"], round((123.0 / 111.0 - 1) * 100, 6))
        blocks = research.get(self.aid)["draft"]["blocks"]
        chart = [b for b in blocks if b["type"] == "datachart"]
        self.assertEqual(len(chart), 1)
        self.assertEqual(len(chart[0]["rows"]), 25)                 # header + 24 months
        self.assertEqual(chart[0]["unit"], "%")
        self.assertIn("An Agency", chart[0]["source"])
        self.assertIn("Ran an analysis on Plover data", [s["text"] for s in j["steps"]])

    def test_answer_only_run_cannot_add_a_chart(self):
        out = research_ai.call_tool({"person": None, "client": "Test", "article_id": self.aid,
                                     "read_only": True},
                                    "run_analysis", {"series": {"a": "test:A"},
                                                     "chart": {"columns": ["a"]}})
        self.assertTrue(out[1])
        self.assertIn("cannot change the draft", out[0])
        self.assertEqual([b for b in research.get(self.aid)["draft"]["blocks"] if b["type"] == "datachart"], [])


class LongResultTest(unittest.TestCase):
    def test_a_result_too_long_is_refused_not_cut(self):
        from app import tools_v2
        real = tools_v2.run_tool
        tools_v2.run_tool = lambda name, args: ('{"points": [' + '["2020-01-01", 1.0], ' * 3000
                                                + '["2026-08-27", 2.9]]}', False)
        self.addCleanup(setattr, tools_v2, "run_tool", real)
        text, err = research_ai.call_tool({"person": None, "client": "Test", "article_id": 1},
                                          "get_series", {"dataset": "jgb-yields", "series": "10Y"})
        self.assertTrue(err)
        self.assertIn("run_analysis", text)
        self.assertNotIn("2020-01-01", text)            # no half a series that stops years early


if __name__ == "__main__":
    unittest.main()
