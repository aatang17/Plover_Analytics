# -*- coding: utf-8 -*-
"""Studies: analyses a person keeps, run on Plover's server.

What is proved: a study belongs to one account (another account sees 404);
a query run by hand uses no AI and keeps every row; asking the Study Analyst
runs the person's own model, which reads only a preview while the full result
is kept as the study's newest run, with its token count; a later run never
changes an earlier one; archive hides a study and restore brings it back; the
CSV carries the inputs, the query and the trust of each column above every
row, with missing values blank; and an outside agent with a desk key can run
an analysis and save it as a study over /mcp/desk.
"""
import datetime
import json
import os
import tempfile
import unittest

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests import _data  # noqa: E402
from app import accounts, analysis, recipes  # noqa: E402
from app.assistant import api as aapi  # noqa: E402
from app.assistant import desk_mcp, gateway, runner, specialists, store  # noqa: E402

from tests.test_assistant import TOOLS, Script, fake_run_tool, fake_search  # noqa: E402

COLS = ["date", "japan", "us"]
SOURCES = [{"name": "japan", "series": "jgb-yields:10Y", "title": "10-year JGB yield", "unit": "%",
            "release": "Data through 27 August 2026", "release_id": 1, "credit": "Source: Ministry of Finance, Japan."},
           {"name": "us", "series": "ust-yields:10Y", "title": "10-year Treasury yield", "unit": "%",
            "release": "Data through 22 September 2026", "release_id": 2, "credit": "Source: U.S. Department of the Treasury."}]


def rows():
    out = []
    for i in range(30):
        out.append(["2024-%02d-01" % (i % 12 + 1) if i < 12 else "2025-%02d-01" % (i % 12 + 1)
                    if i < 24 else "2026-%02d-01" % (i % 12 + 1),
                    0.5 + i / 10.0, None if i == 3 else 4.0 + i / 100.0])
    return out


SPEC = {"series": {"japan": "jgb-yields:10Y", "us": "ust-yields:10Y"},
        "query": "SELECT date, japan, us, spread_pp(us, japan) AS gap FROM data",
        "summary": [{"column": "gap", "stats": ["latest", "max"]}],
        "chart": {"columns": ["japan", "us"], "title": "10-year yields", "unit": "%"}}


def fake_load(series, start=None, end=None, as_of=None, join="both", monthly=None):
    """The data without DuckDB: the rows above under whatever names the spec gives."""
    names = list(series.keys())
    base = rows()
    out = [[r[0]] + [r[1 + (i % 2)] for i in range(len(names))] for r in base]
    srcs = [dict(SOURCES[i % 2], name=n, series=series[n]) for i, n in enumerate(names)]
    return out, ["date"] + names, srcs


class _Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        cls._paths = (accounts.DB_PATH, store.DB_PATH)
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        cls._orig = (runner.tools_v2.descriptors, runner.tools_v2.run_tool,
                     runner.tools_v2.instructions, aapi.tools_v2.search, analysis.load, aapi._stock_answer)
        # the US stock box answers stock questions first; these tests are about the data series
        aapi._stock_answer = lambda q, prev: None
        cls._complete = gateway.complete
        runner.tools_v2.descriptors = lambda: TOOLS
        runner.tools_v2.run_tool = fake_run_tool
        runner.tools_v2.instructions = lambda compact=False: "canned instructions"
        aapi.tools_v2.search = fake_search
        analysis.load = fake_load
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        app.include_router(desk_mcp.router)
        cls.app = app
        db = accounts.conn()
        now = accounts._now()
        cls.ids = {}
        for email, tok in (("pm@example.com", "tok"), ("other@example.com", "tok2")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (accounts._hash(tok), aid, now, now + 3600))
            cls.ids[tok] = aid
        db.commit()

    @classmethod
    def tearDownClass(cls):
        (runner.tools_v2.descriptors, runner.tools_v2.run_tool, runner.tools_v2.instructions,
         aapi.tools_v2.search, analysis.load, aapi._stock_answer) = cls._orig
        gateway.complete = cls._complete
        accounts.DB_PATH, store_path = cls._paths
        accounts._conn = None
        store.reset_for_tests(store_path)
        cls.tmp.cleanup()

    def tearDown(self):
        runner.gateway.complete = self._complete

    def client(self, tok="tok"):
        c = TestClient(self.app)
        c.cookies.set(accounts.COOKIE, tok)
        return c

    def new(self, c, **body):
        r = c.post("/api/v1/assistant/studies", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["study"]


class StudiesTests(_Base):

    def test_run_by_hand_keeps_rows_and_charts_them(self):
        c = self.client()
        st = self.new(c, title="Yield gap")
        r = c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "you")
        self.assertEqual(run["rows_total"], 30)
        self.assertEqual(run["columns"], ["date", "japan", "us", "gap"])
        self.assertIsNone(run["rows"][3][2])                      # the gap stays blank
        self.assertAlmostEqual(run["summary"]["gap"]["latest"]["value"], round((4.29 - 3.4), 6))
        ch = run["chart"]
        # a published series is named as its publisher names it, not by its slot
        self.assertEqual([s["name"] for s in ch["series"]], ["10-year JGB yield", "10-year Treasury yield"])
        self.assertEqual([s["trust"] for s in ch["series"]], ["official", "official"])
        self.assertEqual(ch["unit"], "%")
        self.assertEqual(r.json()["study"]["spec"]["query"], SPEC["query"])
        # a bad query is explained, and keeps nothing
        bad = c.post("/api/v1/assistant/studies/%d/run" % st["id"],
                     json={"spec": dict(SPEC, query="SELECT coalesce(us, 0) FROM data")})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("never 0", bad.json()["detail"])
        self.assertEqual(len(c.get("/api/v1/assistant/studies/%d" % st["id"]).json()["study"]["runs"]), 1)

    def test_a_calculated_column_is_not_official(self):
        c = self.client()
        st = self.new(c, title="Calc")
        spec = dict(SPEC, query="SELECT date, yoy(japan) AS japan, gap FROM (SELECT date, japan, spread_pp(us, japan) AS gap FROM data)",
                    chart={"columns": ["japan", "gap"]})
        run = c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": dict(spec, summary=None)}).json()["run"]
        self.assertEqual([s["trust"] for s in run["chart"]["series"]], ["derived", "derived"])
        self.assertIn("yoy(japan)", run["chart"]["series"][0]["formula"])

    def test_owner_only_and_archive(self):
        c, other = self.client(), self.client("tok2")
        st = self.new(c, title="Mine")
        self.assertEqual(other.get("/api/v1/assistant/studies/%d" % st["id"]).status_code, 404)
        self.assertEqual(other.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC}).status_code, 404)
        self.assertNotIn(st["id"], [x["id"] for x in other.get("/api/v1/assistant/studies").json()["studies"]])
        self.assertEqual(c.delete("/api/v1/assistant/studies/%d" % st["id"]).status_code, 200)
        listed = c.get("/api/v1/assistant/studies").json()
        self.assertNotIn(st["id"], [x["id"] for x in listed["studies"]])
        self.assertGreaterEqual(listed["archived"], 1)
        c.post("/api/v1/assistant/studies/%d/restore" % st["id"])
        self.assertIn(st["id"], [x["id"] for x in c.get("/api/v1/assistant/studies").json()["studies"]])
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"title": "  "}).status_code, 400)
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"title": "Renamed"}).json()["study"]["title"], "Renamed")

    def test_runs_are_kept_unchanged(self):
        c = self.client()
        st = self.new(c, title="History")
        first = c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC}).json()["run"]
        second = c.post("/api/v1/assistant/studies/%d/run" % st["id"],
                        json={"spec": {"series": SPEC["series"],
                                       "query": "SELECT date, japan FROM data WHERE date >= '2026-01-01'"}})
        self.assertEqual(second.status_code, 200, second.text)
        again = c.get("/api/v1/assistant/studies/%d?run=%d" % (st["id"], first["id"])).json()["run"]
        self.assertEqual(again["columns"], first["columns"])
        self.assertEqual(again["rows"], first["rows"])
        newest = c.get("/api/v1/assistant/studies/%d" % st["id"]).json()
        self.assertEqual(len(newest["study"]["runs"]), 2)
        self.assertEqual(newest["run"]["columns"], ["date", "japan"])

    def test_csv_carries_inputs_query_and_trust(self):
        c = self.client()
        st = self.new(c, title="Yield gap CSV")
        run = c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC}).json()["run"]
        text = c.get("/api/v1/assistant/studies/%d/runs/%d/csv" % (st["id"], run["id"])).text
        self.assertIn("# input japan = jgb-yields:10Y", text)
        self.assertIn("# calculation: SELECT date, japan, us, spread_pp(us, japan) AS gap FROM data", text)
        self.assertIn("# trust: japan, us as published", text)
        body = [l for l in text.splitlines() if not l.startswith("#")]
        self.assertEqual(body[0], "date,japan,us,gap")
        self.assertTrue(body[4].endswith(",,"))                   # missing is blank, never 0

    def test_ask_runs_the_persons_model_and_keeps_the_result(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        st = self.new(c, question="How far apart are US and Japanese yields?")
        script = Script([[("run_analysis", dict(SPEC))],
                         "The gap is 0.89 pp now, from the two 10-year yields."])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"],
                   json={"question": "How far apart are US and Japanese yields?"})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "analyst")
        self.assertEqual(run["rows_total"], 30)
        self.assertIn("0.89 pp", run["note"])
        self.assertEqual(run["tokens"]["in"], 20)
        # the model was offered run_analysis and no get_series, and read a preview only
        self.assertIn("run_analysis", script.seen[0]["tools"])
        self.assertNotIn("get_series", script.seen[0]["tools"])
        shown = [m for m in script.seen[1]["messages"] if m["role"] == "tool"][-1]["content"]
        self.assertLess(len(json.loads(shown)["preview"]), 30)
        self.assertEqual(json.loads(shown)["rows"], 30)

    def test_ask_without_an_analysis_says_so(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        st = self.new(c, title="Nothing")
        runner.gateway.complete = Script(["I could not find that series."])
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Bitcoin price?"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("could not find that series", r.json()["detail"])
        self.assertEqual(c.get("/api/v1/assistant/studies/%d" % st["id"]).json()["run"], None)

    def test_outside_agent_saves_a_study(self):
        c = self.client()
        key = c.post("/api/v1/assistant/connections", json={"label": "Claude Code"}).json()["token"]
        anon = TestClient(self.app)

        def call(name, args):
            r = anon.post("/mcp/desk", headers={"Authorization": "Bearer " + key},
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": args}}).json()["result"]
            return json.loads(r["content"][0]["text"]), r["isError"]
        listed = anon.post("/mcp/desk", headers={"Authorization": "Bearer " + key},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
        names = dict((t["name"], t) for t in listed)
        self.assertTrue(names["run_analysis"]["annotations"]["readOnlyHint"])
        self.assertFalse(names["save_study"]["annotations"]["readOnlyHint"])
        got, err = call("run_analysis", SPEC)
        self.assertFalse(err)
        self.assertEqual(got["rows"], 30)
        _, err = call("save_study", SPEC)
        self.assertTrue(err)                                      # a new study needs a title
        got, err = call("save_study", dict(SPEC, title="Yield gap", note="Gap near its low."))
        self.assertFalse(err)
        sid = got["study"]["id"]
        view = c.get("/api/v1/assistant/studies/%d" % sid).json()
        self.assertEqual(view["run"]["by"], "ext:Claude Code")
        self.assertEqual(view["run"]["note"], "Gap near its low.")
        read, err = call("read_study", {"study_id": sid})
        self.assertEqual(read["newest_run"]["rows"], 30)
        other_key = self.client("tok2").post("/api/v1/assistant/connections", json={"label": "x"}).json()["token"]
        r = anon.post("/mcp/desk", headers={"Authorization": "Bearer " + other_key},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "read_study", "arguments": {"study_id": sid}}}).json()["result"]
        self.assertTrue(r["isError"])                             # another account's desk cannot read it


HINTS = [{"dataset": "ust-yields", "code": "10Y", "name": "10-year Treasury yield", "unit": "%"},
         {"dataset": "jgb-yields", "code": "10Y", "name": "10-year JGB yield", "unit": "%"}]


def user_text(call):
    """Every user message of one scripted call. The runner appends to the same
    list after the call, so the last entry may be a later tool result."""
    return "\n".join(m["content"] for m in call["messages"] if m["role"] == "user")


class RecipeTests(_Base):
    """Ready-made analyses (app/recipes.py) and the one-call match before the loop."""

    def setUp(self):
        self._suggest = analysis.suggest_series
        analysis.suggest_series = lambda q, cap=10, per_dataset=3: list(HINTS)
        self._search = aapi.tools_v2.search
        aapi.tools_v2.search = lambda q, dataset="", limit=5: json.dumps(
            {"series": [{"dataset": "jgb-yields", "code": "10Y", "name_en": "10-year JGB yield", "unit": "%"}]
             if "10-year" in q else [], "companies": []})

    def tearDown(self):
        analysis.suggest_series = self._suggest
        aapi.tools_v2.search = self._search
        super(RecipeTests, self).tearDown()

    def test_catalogue_builds_every_recipe(self):
        for r in recipes.catalogue():
            series = dict((sl["name"], "jgb-yields:10Y") for sl in r["slots"] if not sl["optional"])
            spec = recipes.build(r["id"], series)
            self.assertIn("query", spec, r["id"])
            self.assertTrue(analysis.expand(spec["query"]))          # every query expands to SQL
            self.assertTrue(recipes.reads_as(r["id"], dict((k, "X") for k in series)))

    def test_recipe_runs_with_no_ai(self):
        c = self.client()
        st = self.new(c, title="Yield gap, picked")
        r = c.post("/api/v1/assistant/studies/%d/recipe" % st["id"],
                   json={"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}, "start": "2020-01-01"})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "you")
        self.assertEqual(run["columns"], ["date", "a", "b", "gap"])
        self.assertEqual([x["name"] for x in run["chart"]["series"]], ["10-year JGB yield", "10-year Treasury yield", "gap"])
        self.assertIn("minus", run["note"])
        self.assertEqual(r.json()["study"]["spec"]["start"], "2020-01-01")
        bad = c.post("/api/v1/assistant/studies/%d/recipe" % st["id"], json={"recipe": "gap", "series": {"a": "ust-yields:10Y"}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("needs a series", bad.json()["detail"])
        self.assertEqual(c.post("/api/v1/assistant/studies/%d/recipe" % st["id"], json={"recipe": "nope", "series": {}}).status_code, 400)

    def test_series_picker_endpoint(self):
        c = self.client()
        self.assertEqual(c.get("/api/v1/assistant/studies/series?q=").json()["series"], [])
        got = c.get("/api/v1/assistant/studies/series?q=10-year").json()["series"]
        self.assertEqual(got[0]["code"], "10Y")
        self.assertEqual(TestClient(self.app).get("/api/v1/assistant/studies/series?q=x").status_code, 401)

    def test_ask_matches_a_recipe_in_one_call(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876", "study_model": "cheap-model"})
        st = self.new(c, question="How far apart are US and Japanese 10-year yields since 2020?")
        script = Script(['Here you go: {"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}, "start": "2020-01-01", "title": "US and Japan 10-year yields"}'])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "How far apart are US and Japanese 10-year yields since 2020?"})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "analyst")
        self.assertEqual(run["tokens"], {"in": 10, "out": 5, "cached": 0, "calls": 0, "tier": 1})
        self.assertEqual(run["columns"], ["date", "a", "b", "gap"])
        self.assertEqual(run["chart"]["title"], "US and Japan 10-year yields")
        self.assertIn("minus", run["note"])
        # one call, no tools, on the Studies model, with the hints in hand
        self.assertEqual(len(script.seen), 1)
        self.assertEqual(script.seen[0]["tools"], [])
        self.assertEqual(script.seen[0]["model"], "cheap-model")
        self.assertIn("ust-yields:10Y", user_text(script.seen[0]))
        self.assertLess(len(script.seen[0]["messages"][0]["content"]), 3000)

    def test_ask_with_a_list_for_an_option_goes_to_the_loop_not_a_500(self):
        # production, 2026-10-21: the matcher echoed the allowed values,
        # "measure": ["level", "yoy"], and recipes.build raised TypeError
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876", "study_model": "cheap-model"})
        q = "Is Japan's core inflation above or below America's?"
        st = self.new(c, question=q)
        script = Script(['{"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}, "options": {"measure": ["level", "yoy"]}}',
                         [("run_analysis", dict(SPEC))], "Done."])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": q})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["run"]["tokens"]["tier"], 2)
        # the matcher is told to pick one value, and that inflation is yoy
        system = script.seen[0]["messages"][0]["content"]
        self.assertIn("measure: one of level/yoy", system)
        self.assertIn("never the list", system)
        with self.assertRaises(recipes.RecipeError):
            recipes.build("gap", {"a": "x:1", "b": "x:2"}, options={"measure": ["level", "yoy"]})

    def test_matcher_replies_of_the_wrong_shape_are_misses_not_crashes(self):
        hints = [{"dataset": "ust-yields", "code": "10Y", "name": "10-year Treasury"},
                 {"dataset": "jgb-yields", "code": "10Y", "name": "10-year JGB"}]
        s = {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}
        house = {"provider": "anthropic", "key": "k", "model": "m"}
        for pick in ({"recipe": ["gap"]}, {"recipe": {"id": "gap"}},
                     {"recipe": "gap", "series": ["ust-yields:10Y", "jgb-yields:10Y"]},
                     {"recipe": "gap", "series": "ust-yields:10Y"},
                     {"recipe": "gap", "series": {"a": ["ust-yields:10Y"], "b": "jgb-yields:10Y"}},
                     {"recipe": "gap", "series": {"a": {"x": 1}, "b": "jgb-yields:10Y"}},
                     {"recipe": "gap", "series": s, "options": "yoy"},
                     {"recipe": "gap", "series": s, "options": ["yoy"]}):
            aapi.gateway.complete = Script([json.dumps(pick)])
            try:
                self.assertIn(aapi._match_recipe(None, {}, "the gap", hints, house), (None, aapi.NO_MATCH), pick)
            finally:
                aapi.gateway.complete = self._complete

    def test_ask_falls_back_to_a_lean_loop(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876", "study_model": "cheap-model"})
        st = self.new(c, question="Something no recipe does")
        script = Script(['{"recipe": null}', [("run_analysis", dict(SPEC))], "Done: the gap is 0.89 pp."])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Something no recipe does"})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["tokens"]["tier"], 2)
        self.assertEqual(run["tokens"]["calls"], 1)
        self.assertEqual(len(script.seen), 3)                     # match, then two loop turns
        loop = script.seen[1]
        self.assertEqual(loop["model"], "cheap-model")
        system = loop["messages"][0]["content"]
        self.assertNotIn("PRICES:", system)                       # no catalogue of every dataset
        self.assertNotIn("get_series", system)
        self.assertTrue(system.rstrip(".").endswith(__import__("datetime").date.today().isoformat()))  # the date last
        self.assertIn("Likely series", user_text(loop))
        self.assertLess(len(system), 3000)
        self.assertIn("run_analysis", loop["tools"])
        self.assertIn("search", loop["tools"])
        self.assertNotIn("get_series", loop["tools"])
        self.assertIn("rolling_mean", json.dumps([t for t in runner.tool_schemas(["run_analysis"])]))

    def test_follow_up_goes_to_the_loop_with_the_current_analysis(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        st = self.new(c, title="Follow-up")
        c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC})
        script = Script([[("run_analysis", dict(SPEC, monthly="average"))], "Monthly now."])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "now monthly averages"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(script.seen), 2)                     # no match call for a follow-up
        self.assertIn("current analysis", user_text(script.seen[0]))

    def test_loop_is_capped(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        st = self.new(c, question="Keep going")
        turns = ['{"recipe": null}'] + [[("run_analysis", dict(SPEC))]] * 9 + ["Finally."]
        runner.gateway.complete = Script(turns)
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Keep going"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertLessEqual(r.json()["run"]["tokens"]["calls"], 5)


class LowTokenTests(_Base):
    """Items 1–4 of PLAN-LOW-TOKEN-RESEARCH: a compact prompt for every role,
    lean lookups for the Study Analyst, a second try at matching, and runs
    that cost nothing: scheduled refreshes and the download for Python."""

    def test_every_role_gets_a_short_prompt_with_the_date_last(self):
        for spec in (specialists.ANALYST, specialists.STUDY, specialists.CATALOGUE[0]):
            s = runner._system_prompt(spec, [], None)
            self.assertLess(len(s), 9000, spec["slug"])
            self.assertTrue(s.rstrip(".").endswith(datetime.date.today().isoformat()), spec["slug"])
        self.assertNotIn("get_series", runner._system_prompt(specialists.STUDY, [], None))

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_compact_catalogue_names_every_dataset_in_a_line(self):
        # without the data the full catalogue is a stub, so the ratio says nothing
        full, short = self._orig[2](), self._orig[2](compact=True)
        self.assertLess(len(short), len(full) / 3)
        for ds in ("cpi-jp", "jgb-yields"):
            self.assertIn(ds, short)

    def test_lean_lookup_drops_what_a_query_writer_does_not_need(self):
        text = json.dumps({"id": "x", "summary": "s", "source": {"credit": "c"}, "cite": "u", "page": "p",
                           "series": [{"code": str(i), "name_en": "n" * 200} for i in range(20)]})
        normal, lean = json.loads(runner.compact_result(text)), json.loads(runner.compact_result(text, True))
        self.assertIn("source", normal)
        self.assertNotIn("source", lean)
        self.assertNotIn("cite", lean)
        self.assertEqual(len([r for r in lean["series"] if isinstance(r, dict) and "code" in r]), runner.LEAN_ROWS)
        self.assertLess(len(json.dumps(lean)), len(json.dumps(normal)))

    def test_matcher_tries_again_with_a_wider_search(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        st = self.new(c, question="How far apart are US and Japanese 10-year yields?")
        wider = HINTS + [{"dataset": "jgb-yields", "code": "2Y", "name": "2-year JGB yield", "unit": "%"}]
        self._sug = analysis.suggest_series
        analysis.suggest_series = lambda q, cap=10, per_dataset=3: list(HINTS[:1]) if cap == 10 else list(wider)
        self.addCleanup(setattr, analysis, "suggest_series", self._sug)
        script = Script(['{"recipe": null}', '{"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}}'])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "How far apart are US and Japanese 10-year yields?"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["run"]["tokens"]["tier"], 1)
        self.assertEqual(len(script.seen), 2)
        self.assertIn("2-year JGB yield", user_text(script.seen[1]))     # the second try saw more

    def test_refresh_setting_and_due_studies(self):
        c = self.client()
        st = self.new(c, title="Refreshing")
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"refresh": "hourly"}).status_code, 400)
        got = c.put("/api/v1/assistant/studies/%d" % st["id"], json={"refresh": "daily"}).json()["study"]
        self.assertEqual(got["refresh"], "daily")
        self.assertEqual([], [x["id"] for x in store.studies_due()])          # no analysis yet: nothing to run
        c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC})
        at = 1_760_000_000                                                    # a Thursday, 02:13 UTC
        self.assertIn(st["id"], [x["id"] for x in store.studies_due(at)])
        made = aapi.refresh_due_studies(at)
        self.assertEqual(made, 1)
        view = c.get("/api/v1/assistant/studies/%d" % st["id"]).json()
        self.assertEqual(view["run"]["by"], "schedule")
        self.assertEqual(view["run"]["rows_total"], 30)
        self.assertNotIn(st["id"], [x["id"] for x in store.studies_due(at + 3600)])   # done for today
        self.assertIn(st["id"], [x["id"] for x in store.studies_due(at + 86400)])     # due again tomorrow
        # weekly: not for seven days; off: never
        c.put("/api/v1/assistant/studies/%d" % st["id"], json={"refresh": "weekly"})
        self.assertNotIn(st["id"], [x["id"] for x in store.studies_due(at + 3 * 86400)])
        self.assertIn(st["id"], [x["id"] for x in store.studies_due(at + 8 * 86400)])
        c.put("/api/v1/assistant/studies/%d" % st["id"], json={"refresh": ""})
        self.assertNotIn(st["id"], [x["id"] for x in store.studies_due(at + 30 * 86400)])
        # a refresh that fails is kept as a run that says why, and is not retried that day
        c.put("/api/v1/assistant/studies/%d" % st["id"], json={"refresh": "daily"})
        store.update_study(self.ids["tok"], st["id"], spec=dict(SPEC, query="SELECT nope FROM data"))
        self.assertEqual(aapi.refresh_due_studies(at + 2 * 86400), 0)
        view = c.get("/api/v1/assistant/studies/%d" % st["id"]).json()
        self.assertEqual(view["run"]["by"], "schedule")
        self.assertIn("could not run", view["run"]["note"])
        self.assertNotIn(st["id"], [x["id"] for x in store.studies_due(at + 2 * 86400 + 60)])

    def test_download_for_python_runs_locally(self):
        import io
        import os
        import subprocess
        import sys
        import tempfile
        import zipfile
        c = self.client()
        st = self.new(c, title="Yield gap, to run at home")
        run = c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC}).json()["run"]
        r = c.get("/api/v1/assistant/studies/%d/runs/%d/python" % (st["id"], run["id"]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = sorted(n.split("/", 1)[1] for n in z.namelist())
        self.assertEqual(names, ["README.txt", "inputs.csv", "query.sql", "run.py"])
        folder = z.namelist()[0].split("/")[0]
        sql = z.read(folder + "/query.sql").decode()
        self.assertNotIn("spread_pp", sql)                                    # helpers expanded to plain SQL
        self.assertIn("AS gap", sql)
        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)
            here = os.path.join(tmp, folder)
            out = subprocess.run([sys.executable, "run.py"], cwd=here, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("30 rows -> result.csv", out.stdout)
            lines = open(os.path.join(here, "result.csv")).read().splitlines()
            self.assertEqual(lines[0], "date,japan,us,gap")
            self.assertEqual(lines[4].split(",")[2], "")                      # the gap stays blank


if __name__ == "__main__":
    unittest.main()


class StockRouteTests(_Base):
    """A stock question goes to the US stock box first (Your Plover's chat):
    answered with no key and no free try, kept as a run, and its follow-up
    hears the question before it. A data-series chat never asks it."""

    def setUp(self):
        self.asked = []
        self._env = os.environ.get("FREE_TRIES")
        os.environ["FREE_TRIES"] = "0"

        def fake(q, prev):
            self.asked.append((q, prev))
            if "Nvidia" not in q and "And" not in q:
                return None
            return {"headline": "Nvidia Corp's largest reported holder is Blackrock, Inc.", "answer": "5969 managers.",
                    "figures": [{"label": "Holders reporting", "value": 5969, "unit": "count"}],
                    "sources": [{"name": "13F holdings", "credit": "Source: SEC."}], "recipe": "company_holders"}
        aapi._stock_answer = fake

    def tearDown(self):
        aapi._stock_answer = lambda q, prev: None
        if self._env is None:
            os.environ.pop("FREE_TRIES", None)
        else:
            os.environ["FREE_TRIES"] = self._env
        super(StockRouteTests, self).tearDown()

    def test_a_stock_question_needs_no_key_and_is_kept(self):
        c = self.client()
        st = c.post("/api/v1/assistant/studies", json={"question": "Who owns Nvidia?"}).json()["study"]
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Who owns Nvidia?", "stocks": True})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertTrue(run["stock"]["headline"].startswith("Nvidia"))
        self.assertEqual(r.json()["study"]["title"][:3], "Who")
        # the follow-up hears the question before it
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "And Apple?", "stocks": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.asked[-1], ("And Apple?", "Who owns Nvidia?"))
        # reopened, both answers are there
        view = c.get("/api/v1/assistant/studies/%d" % st["id"]).json()
        self.assertEqual(len(view["study"]["runs"]), 2)

    def test_the_desks_studies_never_ask_the_stock_box(self):
        c = self.client()
        st = c.post("/api/v1/assistant/studies", json={"question": "Who owns Nvidia?"}).json()["study"]
        c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Who owns Nvidia?"})
        self.assertEqual(self.asked, [])

    def test_a_declined_question_falls_through_and_says_what_to_do(self):
        c = self.client("tok2")
        st = c.post("/api/v1/assistant/studies", json={"question": "Japan's core inflation?"}).json()["study"]
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": "Japan's core inflation?", "stocks": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("AI Key", r.json()["detail"])
        self.assertEqual(self.asked, [("Japan's core inflation?", None)])


class BudgetAndReuseTests(_Base):
    """A study's own token budget, a repeat question answered from a past
    study with no model, the Audit page's monthly line, and the chat
    Analyst's brief that stops the lookups once the figures are in hand."""

    def setUp(self):
        self.Q = "Something no recipe does, for " + self._testMethodName.replace("_", " ")
        self._suggest = analysis.suggest_series
        analysis.suggest_series = lambda q, cap=10, per_dataset=3: list(HINTS)

    def tearDown(self):
        analysis.suggest_series = self._suggest
        super(BudgetAndReuseTests, self).tearDown()

    def keyed(self):
        c = self.client()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        return c

    def test_budget_is_saved_and_checked(self):
        c = self.client()
        st = self.new(c, title="Budgeted")
        self.assertIsNone(st.get("token_budget"))
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"token_budget": 5000}).json()["study"]["token_budget"], 5000)
        self.assertEqual(c.get("/api/v1/assistant/studies/%d" % st["id"]).json()["study"]["token_budget"], 5000)
        r = c.put("/api/v1/assistant/studies/%d" % st["id"], json={"token_budget": 10})
        self.assertEqual(r.status_code, 400)
        self.assertIn("1,000", r.json()["detail"])
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"token_budget": 10 ** 9}).status_code, 400)
        self.assertIsNone(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"token_budget": 0}).json()["study"]["token_budget"])
        # a title change leaves the budget alone
        c.put("/api/v1/assistant/studies/%d" % st["id"], json={"token_budget": 2000})
        self.assertEqual(c.put("/api/v1/assistant/studies/%d" % st["id"], json={"title": "Renamed"}).json()["study"]["token_budget"], 2000)

    def test_budget_stops_the_loop_and_keeps_the_last_analysis(self):
        c = self.keyed()
        st = self.new(c, question=self.Q)
        store.update_study(self.ids["tok"], st["id"], token_budget=25)     # below the API's floor, to keep the script short
        # the match call spends 15 of the 25; the first loop turn spends 15 more, so a second turn is refused
        script = Script(['{"recipe": null}', [("run_analysis", dict(SPEC))], "never reached"])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": self.Q})
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "analyst")
        self.assertEqual(run["tokens"]["tier"], 2)
        self.assertEqual(run["tokens"]["matcher"], 15)
        self.assertEqual(run["rows_total"], 30)                           # the analysis that ran is kept
        self.assertIn("token budget", run["note"])
        self.assertEqual(len(script.seen), 2)

    def test_budget_hit_before_any_analysis_is_explained(self):
        c = self.keyed()
        st = self.new(c, question=self.Q)
        store.update_study(self.ids["tok"], st["id"], token_budget=25)
        runner.gateway.complete = Script(['{"recipe": null}', [("search", {"query": "yields"})], "never reached"])
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": self.Q})
        self.assertEqual(r.status_code, 400)
        self.assertIn("token budget (25 tokens", r.json()["detail"])
        self.assertIn("Raise the budget", r.json()["detail"])
        self.assertIsNone(c.get("/api/v1/assistant/studies/%d" % st["id"]).json()["run"])
        # a budget the match call alone used up stops before the loop starts
        store.update_study(self.ids["tok"], st["id"], token_budget=12)
        script = Script(['{"recipe": null}', "never reached"])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": self.Q})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(script.seen), 1)

    def test_no_budget_means_the_servers_cap(self):
        c = self.keyed()
        st = self.new(c, question=self.Q)
        script = Script(['{"recipe": null}', [("run_analysis", dict(SPEC))], "Done."])
        runner.gateway.complete = script
        r = c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": self.Q})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("token budget", r.json()["run"]["note"])
        self.assertEqual(len(script.seen), 3)

    def test_same_question_is_answered_from_a_past_study_with_no_model(self):
        c = self.keyed()
        q = "How far apart are US and Japanese 10-year yields since 2020?"
        first = self.new(c, question=q)
        runner.gateway.complete = Script(['{"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}, "start": "2020-01-01", "title": "Yields"}'])
        self.assertEqual(c.post("/api/v1/assistant/studies/%d/ask" % first["id"], json={"question": q}).json()["run"]["tokens"]["tier"], 1)
        # the same question, other casing and punctuation, in a new study
        again = "how far apart are the US and japanese 10-year yields, since 2020"
        second = self.new(c, question=again)
        script = Script([])
        runner.gateway.complete = script
        aapi.gateway.complete = script
        try:
            r = c.post("/api/v1/assistant/studies/%d/ask" % second["id"], json={"question": again})
        finally:
            aapi.gateway.complete = self._complete
        self.assertEqual(r.status_code, 200, r.text)
        run = r.json()["run"]
        self.assertEqual(run["by"], "library")
        self.assertEqual(run["tokens"]["tier"], 0)
        self.assertEqual(run["tokens"]["in"] + run["tokens"]["out"], 0)
        self.assertEqual(run["tokens"]["from_study"], first["id"])
        self.assertEqual(run["columns"], ["date", "a", "b", "gap"])
        self.assertIn("You asked this in “%s”" % first["title"], run["note"])
        self.assertIn("minus", run["note"])                                # the recipe's own reading comes first
        self.assertEqual(script.seen, [])                                  # no model was called
        # a different question is not a repeat
        self.assertIsNone(aapi._past_answer(self.ids["tok"], "How far apart are US and Japanese 10-year yields since 2018?"))
        self.assertIsNone(aapi._past_answer(self.ids["tok"], "How far apart are US and German 10-year yields since 2020?"))
        # another account's studies are never used
        self.assertIsNone(aapi._past_answer(self.ids["tok2"], q))
        # a follow-up inside a study with an analysis is never swapped for a past one
        self.assertTrue(c.get("/api/v1/assistant/studies/%d" % second["id"]).json()["study"]["spec"])

    def test_question_key(self):
        self.assertEqual(aapi.question_key("Show me the gap, please!"), "gap")
        self.assertEqual(aapi.question_key("US vs Japan 10-year"), aapi.question_key("10-year: Japan vs US"))
        self.assertNotEqual(aapi.question_key("core CPI since 2020"), aapi.question_key("core CPI since 2021"))

    def test_audit_counts_ai_use_by_month(self):
        c = self.keyed()
        st = self.new(c, question="How far apart are US and Japanese 10-year yields since 2019?")
        runner.gateway.complete = Script(['{"recipe": "gap", "series": {"a": "ust-yields:10Y", "b": "jgb-yields:10Y"}}'])
        self.assertEqual(c.post("/api/v1/assistant/studies/%d/ask" % st["id"], json={"question": st["question"]}).status_code, 200)
        before = c.get("/api/v1/assistant/audit").json()["months"]
        self.assertEqual(len(before), 6)
        self.assertEqual(before[0]["month"], datetime.datetime.utcnow().strftime("%Y-%m"))
        self.assertGreaterEqual(before[0]["questions"], 1)
        self.assertGreaterEqual(before[0]["tokens"], 15)                   # the one-call match counts, though it made no run row
        self.assertEqual(before[0]["per_question"], int(round(before[0]["tokens"] / float(before[0]["questions"]))))
        # a hand-run query costs nothing and is not a question to a model
        c.post("/api/v1/assistant/studies/%d/run" % st["id"], json={"spec": SPEC})
        after = c.get("/api/v1/assistant/audit").json()["months"]
        self.assertEqual(after[0]["questions"], before[0]["questions"])
        self.assertEqual(after[0]["tokens"], before[0]["tokens"])
        # the other account has its own, empty, line
        other = self.client("tok2").get("/api/v1/assistant/audit").json()["months"]
        self.assertEqual(other[0]["questions"], 0)
        self.assertIsNone(other[0]["per_question"])

    def test_chat_analyst_stops_once_the_figures_are_in_hand(self):
        brief = specialists.ANALYST["brief"]
        self.assertIn("stop looking and answer", brief)
        self.assertIn("get_series is for published statistics, never for a company", brief)
        self.assertEqual(specialists.ANALYST["budget"]["tool_calls"], 10)
        # the cap is enforced by the runner, not the prompt: an eleventh call is refused
        c = self.keyed()
        hire = aapi._analyst(self.ids["tok"])
        turns = [[("get_company", {"code": "7203", "dataset": "financials"})] for _ in range(11)] + ["Toyota's revenue is in the panel."]
        script = Script(turns)
        runner.gateway.complete = script
        out = runner.run(self.ids["tok"], hire, "message", "Toyota revenue", [], "thread", False)
        self.assertEqual(out["tool_calls"], 10)
        refused = [m for m in script.seen[-1]["messages"] if m["role"] == "tool"][-1]["content"]
        self.assertIn("budget exhausted", refused)
