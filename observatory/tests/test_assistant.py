# -*- coding: utf-8 -*-
"""The Investment Assistant's framework, driven end to end with a scripted
model and canned tools, so it needs no key, no network and no data.

What is proved: the keychain seals and detects tampering; the API refuses an
unsigned caller; a desk can hire, cover and configure; the runner enforces
the allowlist and the source rule, stops at its budget, and never sends
anything outward itself; approval is the only path to delivery; threads
carry their history; the audit records every call.
"""
import json
import os
import tempfile
import unittest

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import accounts  # noqa: E402
from app.assistant import api as aapi  # noqa: E402
from app.assistant import (delivery, desk_mcp, gateway, keychain, mcp_client, runner,  # noqa: E402
                           specialists, store)

TOOLS = [
    {"name": "get_company", "description": "one company", "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_vintages", "description": "vintages", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search", "description": "search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "describe_dataset", "description": "card", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "screen", "description": "screen", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_overview", "description": "overview", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_series", "description": "series", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_breakdown", "description": "breakdown", "inputSchema": {"type": "object", "properties": {}}},
]


EARNINGS = {"data": {"release": {"doc_key": "0812A", "filed_date": "2026-08-04", "title": "Q1", "fiscal_period": "2027-03",
                                 "period_label": "First quarter", "is_correction": False},
                     "results": [{"line": "revenue", "label": "Revenue", "value": 13.5e12, "prior_value": 12.2e12, "yoy_pct_published": 10.4}],
                     "forecast": [{"line": "profit", "progress_pct": 24.0}],
                     "wire_note": "long explanatory note " * 30},
            "cite": "https://ploveranalytics.com/earnings.html?c=7203", "vintage": {"note": "x"}, "truncated": {"results": False}}
BUYBACKS = {"data": {"programs": [{"resolution_date": "2025-06-03", "window_start": "2026-03-31", "window_end": "2026-06-30",
                                   "authorised_yen": 4.34e12, "cumulative_yen": 3.66e12, "completion_pct": 84.2, "unspent_yen": 6.8e11,
                                   "lifecycle": "expired_unspent", "lifecycle_label": "Closed unspent", "last_doc_id": "S100YQ7I", "last_as_of": "2026-06-30"}],
                     "measure_note": "long " * 100},
            "cite": "https://ploveranalytics.com/buyback.html?c=7203"}


SERIES = {"tool": "get_series", "dataset": "cpi-jp",
          "data": {"measure": "yoy", "unit": "%", "trust": "derived",
                   "series": [{"code": "0001", "name_en": "All items",
                               "points": [["2025-%02d-01" % m, 2.0 + m / 10.0] for m in range(1, 13)]
                               + [["2026-%02d-01" % m, None if m == 3 else 1.0 + m / 10.0] for m in range(1, 9)]},
                              {"code": "0161", "name_en": "Core", "points": [["2026-01-01", 1.2]]}]},
          "provenance": {"trust": "official", "credit": "Source: Statistics Bureau of Japan.",
                         "document": "CPI Japan, national, Table 1"},
          "calc": {"yoy": "(index[t] / index[t-12 months] - 1) x 100"},
          "vintage": {"label": "Data through August 2026", "published_at": "2026-09-19T00:00:00Z"},
          "cite": "https://ploveranalytics.com/cpi.html"}
PANEL = {"tool": "get_company", "data": {"panel": [
    {"fiscal_year_end": "2025-03-31", "basis": "consolidated", "values": {"revenue": 48.0e12, "fcf": 1.0e12}},
    {"fiscal_year_end": "2024-03-31", "basis": "consolidated", "values": {"revenue": 45.1e12, "fcf": None}},
    {"fiscal_year_end": "2025-03-31", "basis": "parent", "values": {"revenue": 15.0e12, "fcf": 0.5e12}},
    {"fiscal_year_end": "2026-03-31", "basis": "consolidated", "values": {"revenue": 50.7e12, "fcf": "n/a"}}]},
    "provenance": {"trust": "official", "credit": "Source: EDINET annual reports."},
    "calc": {"fcf": "cf_operating + capex"}, "cite": "https://ploveranalytics.com/financials.html?c=7203"}


def fake_run_tool(name, args):
    if name == "get_series":
        return json.dumps(SERIES), False
    if name == "get_company" and args.get("dataset") == "financials":
        return json.dumps(PANEL), False
    if name == "get_company" and args.get("dataset") == "earnings-releases":
        return json.dumps(EARNINGS), False
    if name == "get_company" and args.get("dataset") == "buybacks":
        return json.dumps(BUYBACKS), False
    if name == "get_company":
        return json.dumps({"tool": "get_company", "code": args.get("code"),
                           "buybacks": {"programs": [{"authorised_yen": 100, "cumulative_yen": 60,
                                                      "last_doc_id": "S100TEST"}]}}), False
    return json.dumps({"tool": name, "ok": True}), False


def fake_search(query, dataset="", limit=5):
    if query == "7203":
        return json.dumps({"companies": [{"sec_code": "7203", "name_en": "TOYOTA MOTOR CORPORATION"}]})
    return json.dumps({"companies": []})


# A stand-in for the Codex CLI: `login --device-auth` prints Codex's prompt and
# writes an auth.json; `exec` records what it was given and answers.
FAKE_CODEX = r"""#!%s
import json, os, sys, time
home = os.environ["CODEX_HOME"]
if sys.argv[1:3] == ["login", "--device-auth"]:
    print("\x1b[1mWelcome to Codex\x1b[0m")
    print("1. Open this link in your browser and sign in to your account")
    print("   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m")
    print("2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m")
    print("   \x1b[94mABCD-12345\x1b[0m", flush=True)
    time.sleep(0.5)
    import base64
    claims = base64.urlsafe_b64encode(json.dumps({"email": "pm@example.com"}).encode()).decode().rstrip("=")
    json.dump({"tokens": {"id_token": "h." + claims + ".s", "refresh_token": "r1"}},
              open(os.path.join(home, "auth.json"), "w"))
    sys.exit(0)
if sys.argv[1] == "exec":
    args = sys.argv[1:]
    out = args[args.index("-o") + 1]
    seen = {"args": args, "env": dict(os.environ), "prompt": sys.stdin.read(),
            "auth": json.load(open(os.path.join(home, "auth.json")))}
    json.dump(seen, open(%r, "w"))   # a fixed path: Codex gets no environment to carry one
    # Codex rotates its refresh token while it works
    a = seen["auth"]; a["tokens"]["refresh_token"] = "r2"
    json.dump(a, open(os.path.join(home, "auth.json"), "w"))
    open(out, "w").write("Answer from Codex.")
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 30}}))
    sys.exit(0)
sys.exit(2)
"""


class Script(object):
    """A scripted model: each call pops the next reply."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def __call__(self, provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
        self.seen.append({"provider": provider, "key": key, "model": model,
                          "messages": messages, "tools": [t["name"] for t in (tools or [])]})
        if not self.turns:
            return {"text": "done", "tool_calls": [], "usage": {"in": 10, "out": 5}, "stop": "end"}
        t = self.turns.pop(0)
        if isinstance(t, str):
            return {"text": t, "tool_calls": [], "usage": {"in": 10, "out": 5}, "stop": "end"}
        return {"text": "", "tool_calls": [{"id": "c%d" % i, "name": c[0], "args": c[1]}
                                            for i, c in enumerate(t)],
                "usage": {"in": 10, "out": 5}, "stop": "tool_use"}


class AssistantTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        # canned tools: no DuckDB needed
        cls._orig = (runner.tools_v2.descriptors, runner.tools_v2.run_tool,
                     runner.tools_v2.instructions, aapi.tools_v2.search)
        # runner.gateway IS the gateway module, so a patch replaces the real
        # function; keep the originals aside to restore from.
        cls._complete = gateway.complete
        cls._slack = delivery.slack
        runner.tools_v2.descriptors = lambda: TOOLS
        runner.tools_v2.run_tool = fake_run_tool
        runner.tools_v2.instructions = lambda compact=False: "canned instructions"
        aapi.tools_v2.search = fake_search
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        app.include_router(desk_mcp.router)
        cls.app = app
        # a signed-in account, made directly
        db = accounts.conn()
        now = accounts._now()
        db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", ("pm@example.com", now))
        cls.account_id = db.execute("SELECT id FROM accounts WHERE email = ?", ("pm@example.com",)).fetchone()[0]
        db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                   (accounts._hash("tok"), cls.account_id, now, now + 3600))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        (runner.tools_v2.descriptors, runner.tools_v2.run_tool,
         runner.tools_v2.instructions, aapi.tools_v2.search) = cls._orig
        cls.tmp.cleanup()

    def client(self, signed=True):
        c = TestClient(self.app)
        if signed:
            c.cookies.set(accounts.COOKIE, "tok")
        return c

    def script(self, turns):
        s = Script(turns)
        runner.gateway.complete = s
        return s

    def setUp(self):
        # every test starts from an empty desk; the account and session stay
        db = store.conn()
        for t in ("ia_desks", "ia_hires", "ia_coverage", "ia_runs", "ia_tool_calls",
                  "ia_posts", "ia_approvals", "ia_threads", "ia_messages", "ia_files", "ia_tokens",
                  "ia_lists", "ia_list_items", "ia_secrets", "ia_mcp_servers", "ia_charts"):
            db.execute("DELETE FROM " + t)
        db.commit()

    def tearDown(self):
        runner.gateway.complete = self._complete
        aapi.delivery.slack = self._slack

    # ---- keychain

    def test_keychain_roundtrip_and_tamper(self):
        token = keychain.seal("sk-ant-secret-1234")
        self.assertEqual(keychain.open_(token), "sk-ant-secret-1234")
        self.assertEqual(keychain.last4("sk-ant-secret-1234"), "1234")
        bad = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
        with self.assertRaises(ValueError):
            keychain.open_(bad)

    # ---- auth and setup

    def test_unsigned_caller_is_refused(self):
        c = self.client(signed=False)
        self.assertEqual(c.get("/api/v1/assistant/status").json()["signed_in"], False)
        self.assertEqual(c.get("/api/v1/assistant/desk").status_code, 401)
        self.assertEqual(c.post("/api/v1/assistant/feed", json={"text": "hi"}).status_code, 401)

    def test_hire_cover_and_settings(self):
        c = self.client()
        cat = c.get("/api/v1/assistant/specialists").json()["specialists"]
        self.assertEqual(len(cat), len(specialists.CATALOGUE))
        h = c.post("/api/v1/assistant/specialists/buyback-monitor/hire").json()
        self.assertEqual(h["slug"], "buyback-monitor")
        self.assertTrue(c.post("/api/v1/assistant/specialists/buyback-monitor/hire").json()["id"] == h["id"])
        self.assertEqual(c.post("/api/v1/assistant/specialists/nope/hire").status_code, 404)
        self.assertEqual(c.post("/api/v1/assistant/coverage", json={"code": "12"}).status_code, 400)
        self.assertEqual(c.post("/api/v1/assistant/coverage", json={"code": "9999"}).status_code, 404)
        cov = c.post("/api/v1/assistant/coverage", json={"code": "7203"}).json()["coverage"]
        self.assertEqual(cov[0]["name"], "Toyota Motor Corporation")   # app/jp_names.py
        d = c.get("/api/v1/assistant/desk").json()
        self.assertEqual(d["email"], "pm@example.com")
        self.assertTrue(any(x["slug"] == "buyback-monitor" for x in d["hires"]))
        self.assertFalse(d["desk"]["model_key_set"])
        # a run without a key fails cleanly and is recorded
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual(run["outcome"], "failed")
        self.assertIn("provider", run["error"])
        s = c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"}).json()
        self.assertEqual(s["model_key_last4"], "9876")
        self.assertTrue(s["model_key_set"])
        self.assertNotIn("model_key_ct", s)
        self.assertEqual(s["model_name"], gateway.default_model("anthropic"))
        self.assertEqual(c.post("/api/v1/assistant/settings", json={"slack_webhook": "http://x"}).status_code, 400)

    # ---- the runner through the feed

    def test_feed_mention_runs_with_allowlist_and_approval(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/buyback-monitor/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        script = self.script([
            [("get_company", {"code": "7203"}), ("screen", {})],          # screen is not allowed
            [("post_to_desk", {"text": "no source", "sources": []})],      # refused: no source
            [("post_to_desk", {"text": "Toyota bought 60 of 100.", "sources": ["S100TEST"]}),
             ("request_approval", {"action": "slack", "text": "Toyota: 60 of 100 bought.", "sources": ["S100TEST"]})],
            "Toyota has bought 60 of the 100 authorised (S100TEST).",
        ])
        out = c.post("/api/v1/assistant/feed", json={"text": "@buyback-monitor how is Toyota doing?"}).json()
        run = out["run"]
        self.assertEqual(run["outcome"], "approval_waiting")
        self.assertEqual(run["tool_calls"], 5)
        names = [x["name"] for x in run["calls"]]
        self.assertEqual(names, ["get_company", "screen", "post_to_desk", "post_to_desk", "request_approval"])
        self.assertEqual([x["error"] for x in run["calls"]], [False, True, True, False, False])
        # the model was given the key and only the allowlisted tools
        self.assertEqual(script.seen[0]["key"], "sk-ant-abcd-9876")
        self.assertEqual(set(script.seen[0]["tools"]), set(specialists.get("buyback-monitor")["tools"]))
        # the refusal text reached the model
        tool_msgs = [m for m in script.seen[1]["messages"] if m["role"] == "tool"]
        self.assertIn("not in this specialist", tool_msgs[1]["content"])
        # the desk got the note, once — the closing reply that narrates it is
        # not posted again — and the approval sits on it; nothing went outward
        feed = c.get("/api/v1/assistant/feed").json()["posts"]
        by = [p for p in feed if p["author"] == "buyback-monitor"]
        self.assertEqual(len(by), 1)
        self.assertEqual(by[0]["refs"], ["S100TEST"])
        self.assertEqual(by[0]["approval"]["status"], "pending")
        inbox = c.get("/api/v1/assistant/inbox").json()
        self.assertEqual(len(inbox["pending"]), 1)
        self.assertEqual(inbox["pending"][0]["payload"]["text"], "Toyota: 60 of 100 bought.")
        # approval: no webhook stored -> 502 and still pending; with one -> sent
        sent = []

        def fake_slack(hook, text):
            if not hook:
                raise delivery.DeliveryError("No Slack webhook is stored for this desk.")
            sent.append((hook, text))
            return {"status": 200}
        aapi.delivery.slack = fake_slack
        aid = inbox["pending"][0]["id"]
        r = c.post("/api/v1/assistant/approvals/%d/approve" % aid)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(sent, [])
        c.post("/api/v1/assistant/settings", json={"slack_webhook": "https://hooks.slack.com/services/T/B/X", "slack_label": "#desk"})
        r = c.post("/api/v1/assistant/approvals/%d/approve" % aid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "approved")
        self.assertEqual(sent, [("https://hooks.slack.com/services/T/B/X", "Toyota: 60 of 100 bought.")])
        self.assertEqual(c.post("/api/v1/assistant/approvals/%d/approve" % aid).status_code, 404)
        # audit
        audit = c.get("/api/v1/assistant/audit").json()
        self.assertEqual(audit["counts"]["sent"], 1)
        tools = dict((t["name"], t) for t in audit["tools"])
        self.assertEqual(tools["post_to_desk"]["calls"], 2)
        self.assertEqual(tools["get_company"]["used_by"], ["buyback-monitor"])
        # health row
        self.assertIsNotNone(store.last_success_at())

    def test_thread_keeps_history_and_budget_stops(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/coverage-monitor/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_key": "sk-openai-0001"})
        c.post("/api/v1/assistant/coverage", json={"code": "7203"})
        script = self.script(["First answer.", "Second answer."])
        t = c.post("/api/v1/assistant/threads", json={"hire_id": h["id"], "text": "What changed?"}).json()
        self.assertEqual([m["role"] for m in t["messages"]], ["user", "assistant"])
        self.assertEqual(t["messages"][1]["text"], "First answer.")
        t2 = c.post("/api/v1/assistant/threads/%d/messages" % t["id"], json={"text": "And Toyota?"}).json()
        self.assertEqual(len(t2["messages"]), 4)
        roles = [m["role"] for m in script.seen[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(script.seen[1]["provider"], "openai")
        # the coverage list is in the system prompt
        self.assertIn("7203", script.seen[0]["messages"][0]["content"])
        # a model that never stops calling tools is stopped by the budget
        endless = self.script([[("get_company", {"code": "7203"})]] * 100)
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={"task": "loop"}).json()
        self.assertEqual(run["outcome"], "budget")
        self.assertEqual(run["tool_calls"], runner.BUDGET["tool_calls"])
        self.assertTrue(len(endless.seen) <= runner.BUDGET["iterations"])
        # a gateway failure is a failed run, recorded, not an exception
        def boom(*a, **k):
            raise gateway.GatewayError("The model API answered 401.")
        runner.gateway.complete = boom
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual(run["outcome"], "failed")
        self.assertIn("401", run["error"])
        rec = c.get("/api/v1/assistant/runs/%d" % run["run_id"]).json()
        self.assertEqual(rec["outcome"], "failed")

    # ---- invite-only sign-in

    def test_invite_list_limits_signin_and_hides_header_link(self):
        c = self.client(signed=False)
        os.environ["ACCOUNTS_ALLOWED_EMAILS"] = "PM@example.com, other@example.com"
        try:
            r = c.post("/api/v1/account/signin-link", json={"email": "stranger@example.com"})
            self.assertEqual(r.status_code, 403)
            self.assertIn("invitation", r.json()["detail"])
            self.assertEqual(c.post("/api/v1/account/signin-link",
                                    json={"email": "pm@example.com"}).status_code, 200)
            me = c.get("/api/v1/account/me")
            self.assertEqual(me.status_code, 401)
            self.assertTrue(me.json()["invite_only"])
            # the invited keep their session
            self.assertEqual(self.client().get("/api/v1/account/me").json()["email"], "pm@example.com")
            # taken off the list, the open session stops working
            os.environ["ACCOUNTS_ALLOWED_EMAILS"] = "other@example.com"
            self.assertEqual(self.client().get("/api/v1/account/me").status_code, 401)
            self.assertEqual(self.client().get("/api/v1/assistant/desk").status_code, 401)
        finally:
            del os.environ["ACCOUNTS_ALLOWED_EMAILS"]
        me = c.get("/api/v1/account/me")
        self.assertFalse(me.json()["invite_only"])
        self.assertEqual(self.client().get("/api/v1/account/me").json()["email"], "pm@example.com")

    def test_me_names_the_desks_the_header_offers(self):
        from app import staff, writers
        old_path, old_roles = staff.DB_PATH, writers.roles
        staff.DB_PATH = staff.pathlib.Path(self.tmp.name) / "staff-me.db"
        try:
            # a reader with no staff record: Your Plover only
            me = self.client().get("/api/v1/account/me").json()
            self.assertEqual((me["writer"], me["admin"]), (False, False))
            # "writing" alone is a writer, not an admin
            person, _ = staff.create("pm@example.com", "PM", ["writing"], "test")
            writers.roles = lambda p: {1: "writer"}
            me = self.client().get("/api/v1/account/me").json()
            self.assertEqual((me["writer"], me["admin"]), (True, False))
            staff.update(person["id"], 0, permissions=["operations"])
            writers.roles = lambda p: {}
            me = self.client().get("/api/v1/account/me").json()
            self.assertEqual((me["writer"], me["admin"]), (False, True))
            # a disabled record offers neither
            staff.update(person["id"], 0, status="disabled")
            me = self.client().get("/api/v1/account/me").json()
            self.assertEqual((me["writer"], me["admin"]), (False, False))
        finally:
            staff.DB_PATH, writers.roles = old_path, old_roles

    # ---- charts

    def test_chart_is_read_from_the_result_not_typed(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/macro-brief/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_key": "sk-openai-0001"})
        script = self.script([
            [("get_series", {"dataset": "cpi-jp", "series": "0001", "measure": "yoy"})],
            [("draw_chart", {"title": "CPI inflation", "kind": "line",
                             "series": [{"call": 1, "series": "0001"}]})],
            "Inflation has eased."])
        t = c.post("/api/v1/assistant/threads", json={"hire_id": h["id"], "text": "Chart CPI"}).json()
        self.assertIn("draw_chart", script.seen[0]["tools"])
        # the model is told the call number, and sees only a shortened result
        shown = json.loads([m for m in script.seen[1]["messages"]
                            if m.get("name") == "get_series"][0]["content"])
        self.assertEqual(shown["call"], 1)
        charts = t["messages"][1]["charts"]
        self.assertEqual(len(charts), 1)
        ch = charts[0]
        pts = ch["series"][0]["points"]
        # all twenty points, though the model was shown twelve; the gap stays a gap
        self.assertEqual(len(pts), 20)
        self.assertEqual(pts[0], ["2025-01-01", 2.1])
        self.assertEqual(pts[14], ["2026-03-01", None])
        self.assertEqual(ch["series"][0]["trust"], "derived")
        self.assertIn("index[t]", ch["series"][0]["formula"])
        self.assertEqual(ch["unit"], "%")
        self.assertEqual(ch["axis"], "time")
        self.assertEqual(ch["sources"][0]["credit"], "Source: Statistics Bureau of Japan.")
        self.assertEqual(ch["sources"][0]["vintage"], "Data through August 2026")

    def test_chart_refusals(self):
        from app.assistant import charts
        calls = {1: {"seq": 1, "name": "get_series", "args": {}, "result": SERIES},
                 2: {"seq": 2, "name": "get_company", "args": {}, "result": PANEL}}
        def build(series, kind="line"):
            return charts.build({"title": "t", "kind": kind, "series": series}, calls)
        rev = {"call": 2, "rows": "panel", "x": "fiscal_year_end", "y": "values.revenue", "unit": "¥"}
        cases = [
            ([{"call": 9, "series": "0001"}], "no data result numbered 9"),
            ([{"call": 1, "series": "9999"}], "no series with code 9999"),
            ([rev], "Two rows share"),                                   # consolidated beside parent
            ([dict(rev, where={"basis": "consolidated"}), {"call": 1, "series": "0001"}], "different units"),
            ([dict(rev, unit="")], "needs a `unit`"),
            ([dict(rev, y="values.fcf", where={"basis": "consolidated"})], "not a number"),
            ([{"call": 1, "series": "0001"}] * 7, "At most 6"),
        ]
        for series, msg in cases:
            with self.assertRaises(charts.ChartError) as cm:
                build(series)
            self.assertIn(msg, str(cm.exception))
        ok = build([dict(rev, where={"basis": "consolidated"})], kind="bar")
        self.assertEqual(ok["axis"], "category")
        self.assertTrue(ok["fiscal"])
        self.assertEqual([p[0] for p in ok["series"][0]["points"]],
                         ["2024-03-31", "2025-03-31", "2026-03-31"])
        self.assertEqual(ok["series"][0]["trust"], "official")
        # a full path to the rows works as well as the bare key
        self.assertEqual(build([dict(rev, rows="data.panel", where={"basis": "consolidated"})])["series"],
                         ok["series"])
        self.assertEqual(charts.summary(ok)[0]["to"], "2026-03-31")
        # a calculated field carries its formula
        panel2 = json.loads(json.dumps(PANEL))
        panel2["data"]["panel"][3]["values"]["fcf"] = None
        calls[3] = {"seq": 3, "name": "get_company", "args": {}, "result": panel2}
        fcf = charts.build({"title": "t", "kind": "line", "series": [dict(
            rev, call=3, y="values.fcf", where={"basis": "consolidated"})]}, calls)
        self.assertEqual(fcf["series"][0]["trust"], "derived")
        self.assertEqual(fcf["series"][0]["formula"], "cf_operating + capex")
        self.assertEqual(fcf["series"][0]["points"][0], ["2024-03-31", None])   # missing, not zero

    def test_desk_chart_lands_under_the_post(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/macro-brief/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_key": "sk-openai-0001"})
        self.script([
            [("get_series", {"dataset": "cpi-jp", "series": "0001"})],
            [("draw_chart", {"title": "CPI", "kind": "line", "series": [{"call": 1, "series": "0001"}]})],
            [("post_to_desk", {"text": "CPI eased.", "sources": ["cpi-jp release 25"], "charts": [1]})],
            "done"])
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual(len(run["charts"]), 1)
        feed = c.get("/api/v1/assistant/feed").json()["posts"]
        self.assertEqual(len(feed[-1]["charts"]), 1)
        self.assertEqual(feed[-1]["charts"][0]["title"], "CPI")

    # ---- Codex on the desk's ChatGPT plan

    def _fake_codex(self):
        from app.assistant import codex
        import stat, sys as _sys
        d = tempfile.mkdtemp()
        path = os.path.join(d, "codex")
        log = os.path.join(d, "seen.json")
        with open(path, "w") as f:
            f.write(FAKE_CODEX % (_sys.executable, log))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.addCleanup(setattr, codex, "BIN", codex.BIN)
        codex.BIN = path
        return codex, log

    def test_codex_signin_and_locked_down_run(self):
        import time as _time
        codex, log = self._fake_codex()
        c = self.client()
        s = c.get("/api/v1/assistant/settings").json()
        self.assertIn("codex", [p["id"] for p in s["providers"]])
        self.assertFalse(s["codex"]["connected"])
        # not connected: cannot be chosen
        self.assertEqual(c.post("/api/v1/assistant/settings",
                                json={"model_provider": "codex"}).status_code, 400)
        r = c.post("/api/v1/assistant/settings/codex/login").json()
        self.assertEqual(r["url"], "https://auth.openai.com/codex/device")
        self.assertEqual(r["code"], "ABCD-12345")
        for _ in range(50):
            st = c.get("/api/v1/assistant/settings/codex").json()
            if st["connected"]:
                break
            _time.sleep(0.1)
        self.assertTrue(st["connected"])
        self.assertEqual(st["email"], "pm@example.com")
        # the sign-in is sealed, never returned
        self.assertNotIn("codex_auth_ct", c.get("/api/v1/assistant/settings").json())
        self.assertNotIn("r1", json.dumps(c.get("/api/v1/assistant/settings").json()))
        self.assertEqual(c.post("/api/v1/assistant/settings",
                                json={"model_provider": "codex"}).json()["model_provider"], "codex")

        h = c.post("/api/v1/assistant/specialists/macro-brief/hire").json()
        t = c.post("/api/v1/assistant/threads", json={"hire_id": h["id"], "text": "What did CPI print?"}).json()
        self.assertEqual(t["messages"][1]["text"], "Answer from Codex.")
        seen = json.load(open(log))
        args = seen["args"]
        # shell off, read-only, no user config, our tools the only tools
        for f in ("shell_tool", "unified_exec", "browser_use", "computer_use"):
            self.assertIn(f, args)
        self.assertEqual(args[args.index("-s") + 1], "read-only")
        self.assertIn("--ignore-user-config", args)
        self.assertIn('web_search="disabled"', args)
        self.assertTrue(any(a.startswith("mcp_servers.plover.args=") and "codex_mcp" in a for a in args))
        # no server secret reaches Codex
        self.assertNotIn("ASSISTANT_SECRET", seen["env"])
        self.assertNotIn("test-secret-not-for-production", json.dumps(args))
        # (macOS adds its own __CF_USER_TEXT_ENCODING to every process)
        self.assertEqual(sorted(k for k in seen["env"] if not k.startswith("__CF")),
                         sorted(["PATH", "HOME", "CODEX_HOME", "LANG"]))
        # same brief and question as any other provider
        self.assertIn("Macro Release Brief", seen["prompt"])
        self.assertIn("What did CPI print?", seen["prompt"])
        # the rotated refresh token is sealed back for the next run
        auth = json.loads(keychain.open_(store.desk_secrets(self.account_id)["codex_auth_ct"]))
        self.assertEqual(auth["tokens"]["refresh_token"], "r2")
        run = store.run(self.account_id, t["run"]["run_id"])
        self.assertEqual((run["tokens_in"], run["tokens_out"]), (120, 30))
        # disconnect clears the sign-in and the provider
        st = c.delete("/api/v1/assistant/settings/codex").json()
        self.assertFalse(st["connected"])
        self.assertIsNone(store.desk(self.account_id)["model_provider"])

    def test_codex_tool_server_is_the_run_allowlist(self):
        from app.assistant import codex_mcp
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/macro-brief/hire").json()
        run_id = store.start_run(self.account_id, h["id"], "macro-brief", h["version"], "message", "q", "")
        ctx = codex_mcp.Context(self.account_id, h["id"], run_id)
        rpc = lambda method, params=None, i=[0]: codex_mcp.handle(ctx, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        self.assertIn("tools", rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]["capabilities"])
        tools = rpc("tools/list")["result"]["tools"]
        names = set(t["name"] for t in tools)
        self.assertIn("get_series", names)
        self.assertIn("draw_chart", names)
        self.assertNotIn("post_to_desk", names)          # desk actions stay with the runner
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
        res = rpc("tools/call", {"name": "get_series", "arguments": {"dataset": "cpi-jp", "series": "0001"}})["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(json.loads(res["content"][0]["text"])["call"], 1)
        ch = rpc("tools/call", {"name": "draw_chart", "arguments": {
            "title": "CPI", "kind": "line", "series": [{"call": 1, "series": "0001"}]}})["result"]
        self.assertFalse(ch["isError"])
        bad = rpc("tools/call", {"name": "post_to_desk", "arguments": {"text": "x", "sources": ["y"]}})["result"]
        self.assertTrue(bad["isError"])
        # every call is in the run's audit, and the chart is under the run
        self.assertEqual([x["name"] for x in store.calls(run_id)], ["get_series", "draw_chart", "post_to_desk"])
        self.assertEqual(len(store.charts_for_run(run_id)), 1)

    def test_workspace_files(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/financials-extract/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "deepseek", "model_key": "sk-ds-0002"})
        self.script([[("write_workspace_file", {"path": "notes/x.md", "content": "hello"}),
                      ("write_workspace_file", {"path": "../etc/passwd", "content": "no"})], "Written."])
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual([x["error"] for x in run["calls"]], [False, True])
        files = c.get("/api/v1/assistant/files").json()["files"]
        self.assertEqual([f["path"] for f in files if f["hire_id"] == h["id"]], ["notes/x.md"])
        f = c.get("/api/v1/assistant/files/%d/notes/x.md" % h["id"]).json()
        self.assertEqual(f["content"], "hello")
        # a "Run now" is desk mode: the reply is posted to the feed
        mine = [p for p in c.get("/api/v1/assistant/feed").json()["posts"]
                if p["run_id"] == run["run_id"] and p["author"] == "financials-extract"]
        self.assertEqual([p["text"] for p in mine], ["Written."])


    # ---- outside agents over /mcp/desk

    def rpc(self, c, key, method, params=None, msg_id=1):
        headers = {"Authorization": "Bearer " + key} if key else {}
        return c.post("/mcp/desk", headers=headers,
                      json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})

    def call(self, c, key, name, args):
        r = self.rpc(c, key, "tools/call", {"name": name, "arguments": args}).json()["result"]
        return json.loads(r["content"][0]["text"]), r["isError"]

    def test_connection_key_and_desk_mcp(self):
        c = self.client()
        c.post("/api/v1/assistant/specialists/buyback-monitor/hire")
        made = c.post("/api/v1/assistant/connections", json={"label": "Claude Code"}).json()
        key = made["token"]
        self.assertTrue(key.startswith("pad_"))
        self.assertTrue(made["endpoint"].endswith("/mcp/desk"))
        listed = c.get("/api/v1/assistant/connections").json()["connections"]
        self.assertEqual([x["label"] for x in listed], ["Claude Code"])
        self.assertNotIn("token", listed[0])
        self.assertNotIn("token_hash", listed[0])
        # no key, a wrong key: refused
        anon = self.client(signed=False)
        self.assertEqual(self.rpc(anon, "", "tools/list").status_code, 401)
        self.assertEqual(self.rpc(anon, "pad_wrong", "tools/list").status_code, 401)
        # the right key, with no browser session at all
        init = self.rpc(anon, key, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
        self.assertEqual(init["serverInfo"]["name"], "plover-desk")
        names = [t["name"] for t in self.rpc(anon, key, "tools/list").json()["result"]["tools"]]
        for n in ("desk_overview", "read_feed", "post_to_desk", "request_approval", "get_company"):
            self.assertIn(n, names)
        self.assertFalse([n for n in names if "send" in n or "slack" in n or "order" in n])
        ov, err = self.call(anon, key, "desk_overview", {})
        self.assertFalse(err)
        self.assertEqual(ov["specialists"][0]["handle"], "buyback-monitor")
        got, err = self.call(anon, key, "add_to_coverage", {"code": "7203"})
        self.assertEqual(got["coverage"][0]["sec_code"], "7203")
        _, err = self.call(anon, key, "get_company", {"code": "7203"})
        self.assertFalse(err)
        _, err = self.call(anon, key, "post_to_desk", {"text": "No source.", "sources": []})
        self.assertTrue(err)
        got, err = self.call(anon, key, "post_to_desk", {"text": "Toyota: 60 of 100 bought.", "sources": ["S100TEST"]})
        self.assertFalse(err)
        got, err = self.call(anon, key, "request_approval", {"text": "Toyota: 60 of 100.", "sources": ["S100TEST"]})
        self.assertEqual(got["status"], "pending")
        # on the desk: two posts by the connection, the approval on the second only
        feed = c.get("/api/v1/assistant/feed").json()["posts"]
        ext = [p for p in feed if p["author"] == "ext:Claude Code"]
        self.assertEqual([bool(p["approval"]) for p in ext], [False, True])
        self.assertEqual([p["calls"] for p in ext], [[], []])
        pend = c.get("/api/v1/assistant/inbox").json()["pending"]
        self.assertEqual(pend[0]["payload"]["from"], "Claude Code")
        # audited under one run for the connection
        audit = c.get("/api/v1/assistant/audit").json()
        runs = [r for r in audit["runs"] if r["slug"] == "connection"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["tool_calls"], 6)
        seqs = [x["seq"] for x in c.get("/api/v1/assistant/runs/%d" % runs[0]["id"]).json()["calls"]]
        self.assertEqual(seqs, [1, 2, 3, 4, 5, 6])
        # ask_specialist runs on the desk's key through the normal runner
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        self.script(["Mizuho has bought 35.7% (S100YVPX)."])
        got, err = self.call(anon, key, "ask_specialist", {"specialist": "@buyback-monitor", "question": "Mizuho?"})
        self.assertEqual(got["outcome"], "done")
        self.assertIn("35.7%", got["answer"])
        # revoked: refused at once
        c.delete("/api/v1/assistant/connections/%d" % made["id"])
        self.assertEqual(self.rpc(anon, key, "tools/list").status_code, 401)
        self.assertEqual(c.get("/api/v1/assistant/connections").json()["connections"], [])

    def test_desk_run_does_not_repeat_its_own_notes(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/buyback-monitor/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "anthropic", "model_key": "sk-ant-abcd-9876"})
        self.script([[("post_to_desk", {"text": "Note one.", "sources": ["S1"]}),
                      ("post_to_desk", {"text": "Note two.", "sources": ["S2"]})],
                     "Posted two notes to the desk (post ids 1 and 2)."])
        c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={})
        mine = [p for p in c.get("/api/v1/assistant/feed").json()["posts"] if p["author"] == "buyback-monitor"]
        self.assertEqual([p["text"] for p in mine], ["Note one.", "Note two."])
        self.assertEqual([len(p["calls"]) for p in mine], [0, 2])


    # ---- prepared runs: code fetches and diffs, the model only writes

    def test_coverage_monitor_prepared_run(self):
        c = self.client()
        h = c.post("/api/v1/assistant/specialists/coverage-monitor/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_name": "gpt-big",
                                                   "monitor_model": "gpt-small", "model_key": "sk-1"})
        c.post("/api/v1/assistant/coverage", json={"code": "7203"})
        # first run: one model call, no tools offered, digest in the prompt, sources from code
        script = self.script(["Toyota filed Q1 (0812A); its buyback closed unspent (S100YQ7I)."])
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual(run["outcome"], "done")
        self.assertEqual(len(script.seen), 1)
        self.assertEqual(script.seen[0]["model"], "gpt-small")          # the monitor tier
        self.assertEqual(script.seen[0]["tools"], [])
        prompt = script.seen[0]["messages"][-1]["content"]
        self.assertIn("First run", prompt)
        self.assertIn("0812A", prompt)
        self.assertIn("\u00a513.50tn", prompt)
        self.assertLess(len(prompt), 2500)
        posts = [p for p in c.get("/api/v1/assistant/feed").json()["posts"] if p["author"] == "coverage-monitor"]
        self.assertEqual(posts[-1]["refs"], ["0812A", "S100YQ7I"])
        self.assertEqual([x["name"] for x in run["calls"]], ["coverage_digest"])
        state = c.get("/api/v1/assistant/files/%d/state/coverage.json" % h["id"]).json()
        self.assertIn("0812A", state["content"])
        # scheduled run with nothing new: no model call at all
        script = self.script(["should not be called"])
        out = runner.run(self.account_id, store.hire(self.account_id, h["id"]), "schedule", specialists.get("coverage-monitor")["task"])
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(script.seen, [])
        self.assertIn("No new filings on 1 company", out["text"])
        # a new earnings release: one model call, the change named in the prompt
        EARNINGS["data"]["release"]["doc_key"] = "0812B"
        try:
            script = self.script(["Toyota filed again (0812B)."])
            out = runner.run(self.account_id, store.hire(self.account_id, h["id"]), "schedule", specialists.get("coverage-monitor")["task"])
            self.assertEqual(len(script.seen), 1)
            self.assertIn("new earnings release", script.seen[0]["messages"][-1]["content"])
            self.assertEqual(out["posts"][0]["refs"][0], "0812B")
        finally:
            EARNINGS["data"]["release"]["doc_key"] = "0812A"
        # a direct question still uses the tool loop, and research tier takes the main model
        script = self.script(["Answer."])
        c.post("/api/v1/assistant/feed", json={"text": "@coverage-monitor anything on Toyota?"})
        self.assertEqual(script.seen[0]["model"], "gpt-small")
        self.assertTrue(script.seen[0]["tools"])
        h2 = c.post("/api/v1/assistant/specialists/disclosure-verification/hire").json()
        script = self.script(["Checked."])
        c.post("/api/v1/assistant/hires/%d/run" % h2["id"], json={"task": "check"})
        self.assertEqual(script.seen[0]["model"], "gpt-big")

    def test_compact_result_strips_prose_not_numbers(self):
        raw = json.dumps(EARNINGS)
        out = runner.compact_result(raw)
        self.assertLess(len(out), len(raw) / 2)
        d = json.loads(out)
        self.assertEqual(d["data"]["results"][0]["value"], 13.5e12)
        self.assertEqual(d["cite"], EARNINGS["cite"])
        self.assertNotIn("wire_note", d["data"])
        self.assertNotIn("vintage", d)
        self.assertEqual(runner.compact_result('{"error": "x"}'), '{"error": "x"}')
        self.assertEqual(runner.compact_result("not json"), "not json")
        big = json.dumps({"data": {"rows": [{"i": i} for i in range(40)]}})
        rows = json.loads(runner.compact_result(big))["data"]["rows"]
        self.assertEqual(len(rows), runner.MAX_ROWS + 1)
        self.assertIn("more rows", rows[-1])


    # ---- coverage search by name, Japanese name, code or nickname

    def test_company_search_ranks_and_marks_covered(self):
        from app import equity_api
        rows = [{"sec_code": "8015", "name": "豊田通商株式会社", "name_en": "TOYOTA TSUSHO CORPORATION"},
                {"sec_code": "7203", "name": "トヨタ自動車（株）", "name_en": "TOYOTA MOTOR CORPORATION"},
                {"sec_code": "7267", "name": "本田技研工業株式会社", "name_en": "HONDA MOTOR CO., LTD."},
                {"sec_code": "cik:1", "name": "x", "name_en": "US filer"}]
        orig = equity_api.companies
        seen = []
        equity_api.companies = lambda q="": seen.append(q) or {"companies": [r for r in rows if
            q.lower() in (r["sec_code"] + r["name"] + r["name_en"]).lower() or q == "MUFG"]}
        try:
            c = self.client()
            self.assertEqual(c.get("/api/v1/assistant/companies?q=").json()["companies"], [])
            got = c.get("/api/v1/assistant/companies?q=toyota").json()["companies"]
            self.assertEqual([x["code"] for x in got], ["8015", "7203"])
            self.assertEqual(got[1]["name_ja"], "トヨタ自動車（株）")
            # an exact code outranks everything; a code prefix outranks names
            self.assertEqual(c.get("/api/v1/assistant/companies?q=7203").json()["companies"][0]["code"], "7203")
            self.assertEqual([x["code"] for x in c.get("/api/v1/assistant/companies?q=72").json()["companies"]][:2],
                             ["7203", "7267"])
            # Japanese name; US filers never offered
            self.assertEqual([x["code"] for x in c.get("/api/v1/assistant/companies?q=トヨタ").json()["companies"]], ["7203"])
            self.assertEqual(c.get("/api/v1/assistant/companies?q=US%20filer").json()["companies"], [])
            # covered companies are marked
            c.post("/api/v1/assistant/coverage", json={"code": "7203"})
            got = dict((x["code"], x["covered"]) for x in c.get("/api/v1/assistant/companies?q=toyota").json()["companies"])
            self.assertEqual(got, {"8015": False, "7203": True})
        finally:
            equity_api.companies = orig
        self.assertEqual(self.client(signed=False).get("/api/v1/assistant/companies?q=toyota").status_code, 401)

    def test_outside_agent_adds_by_name(self):
        from app import equity_api
        rows = [{"sec_code": "8015", "name": "豊田通商", "name_en": "TOYOTA TSUSHO CORPORATION"},
                {"sec_code": "7203", "name": "トヨタ自動車", "name_en": "TOYOTA MOTOR CORPORATION"}]
        orig = equity_api.companies
        equity_api.companies = lambda q="": {"companies": [r for r in rows if q.lower() in (r["sec_code"] + r["name"] + r["name_en"]).lower()]}
        try:
            c = self.client()
            key = c.post("/api/v1/assistant/connections", json={"label": "Codex"}).json()["token"]
            anon = self.client(signed=False)
            got, err = self.call(anon, key, "add_to_coverage", {"code": "toyota"})
            self.assertTrue(err)
            self.assertEqual([x["code"] for x in got["candidates"]], ["8015", "7203"])
            got, err = self.call(anon, key, "add_to_coverage", {"code": "toyota motor"})
            self.assertFalse(err)
            self.assertEqual(got["added"]["code"], "7203")
            got, err = self.call(anon, key, "add_to_coverage", {"code": "nothing like this"})
            self.assertTrue(err)
        finally:
            equity_api.companies = orig


    # ---- named coverage lists

    def test_a_delisted_company_cannot_be_followed(self):
        from unittest import mock
        from app import listing
        c = self.client()
        cov = c.post("/api/v1/assistant/coverage", json={"code": "7203"}).json()["coverage"]
        self.assertTrue(cov)
        with mock.patch.object(listing, "is_delisted", lambda code: code == "7203"):
            c.delete("/api/v1/assistant/coverage/7203")
            r = c.post("/api/v1/assistant/coverage", json={"code": "7203"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("no longer listed", r.json()["detail"])
            # one that delisted after it was added stays, and says so
            store.add_coverage(self.account_id, "7203", "TOYOTA", None, None)
            row = c.get("/api/v1/assistant/coverage").json()["coverage"][0]
            self.assertTrue(row["delisted"])
        c.delete("/api/v1/assistant/coverage/7203")

    def test_coverage_lists(self):
        c = self.client()
        # an old single-list desk moves into the main list, once
        store.conn().execute("INSERT INTO ia_coverage (account_id, sec_code, name, added_at) VALUES (?, '7203', 'TOYOTA MOTOR CORPORATION', 1)",
                             (self.account_id,))
        store.conn().commit()
        lists = c.get("/api/v1/assistant/lists").json()["lists"]
        self.assertEqual([(l["name"], l["is_default"], l["count"]) for l in lists], [("Coverage", 1, 1)])
        self.assertEqual(store.conn().execute("SELECT count(*) FROM ia_coverage").fetchone()[0], 0)
        main = lists[0]["id"]
        # a custom list, kept apart from the main one
        self.assertEqual(c.post("/api/v1/assistant/lists", json={"name": "  "}).status_code, 400)
        banks = c.post("/api/v1/assistant/lists", json={"name": "Banks"}).json()
        c.post("/api/v1/assistant/coverage", json={"code": "7203", "list_id": banks["id"]})
        self.assertEqual([x["sec_code"] for x in c.get("/api/v1/assistant/coverage?list_id=%d" % banks["id"]).json()["coverage"]], ["7203"])
        self.assertEqual([x["sec_code"] for x in c.get("/api/v1/assistant/coverage").json()["coverage"]], ["7203"])
        c.delete("/api/v1/assistant/coverage/7203?list_id=%d" % banks["id"])
        self.assertEqual(c.get("/api/v1/assistant/coverage?list_id=%d" % banks["id"]).json()["coverage"], [])
        self.assertEqual(len(c.get("/api/v1/assistant/coverage").json()["coverage"]), 1)     # main list untouched
        # rename; the main list cannot be deleted; unknown lists 404
        self.assertEqual(c.patch("/api/v1/assistant/lists/%d" % banks["id"], json={"name": "Megabanks"}).json()["name"], "Megabanks")
        self.assertEqual(c.delete("/api/v1/assistant/lists/%d" % main).status_code, 400)
        self.assertEqual(c.get("/api/v1/assistant/coverage?list_id=99999").status_code, 404)
        self.assertEqual(c.post("/api/v1/assistant/coverage", json={"code": "7203", "list_id": 99999}).status_code, 404)
        # a monitor can watch the custom list; the prepared run reads it
        h = c.post("/api/v1/assistant/specialists/coverage-monitor/hire").json()
        c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_key": "sk-1"})
        c.post("/api/v1/assistant/coverage", json={"code": "7203", "list_id": banks["id"]})
        self.assertEqual(c.post("/api/v1/assistant/hires/%d/config" % h["id"], json={"list_id": 99999}).status_code, 404)
        c.post("/api/v1/assistant/hires/%d/config" % h["id"], json={"list_id": banks["id"]})
        self.assertEqual(c.get("/api/v1/assistant/lists").json()["watched_by"][str(h["id"])], banks["id"])
        store.remove_coverage(self.account_id, "7203")          # main list now empty
        script = self.script(["Note."])
        run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={}).json()
        self.assertEqual(run["outcome"], "done")
        self.assertEqual(len(script.seen), 1)                    # it read Megabanks, not the empty main list
        # deleting the watched list sends the monitor back to the main list
        self.assertEqual(c.delete("/api/v1/assistant/lists/%d" % banks["id"]).json()["deleted"], banks["id"])
        self.assertNotIn("list_id", store.hire(self.account_id, h["id"])["config"])
        self.assertEqual([l["name"] for l in c.get("/api/v1/assistant/lists").json()["lists"]], ["Coverage"])


    # ---- stored keys and the desk's own MCP servers

    def test_secrets_are_never_returned(self):
        c = self.client()
        self.assertEqual(c.post("/api/v1/assistant/secrets", json={"name": "", "value": "x"}).status_code, 400)
        self.assertEqual(c.post("/api/v1/assistant/secrets", json={"name": "Firm", "value": " "}).status_code, 400)
        k = c.post("/api/v1/assistant/secrets", json={"name": "Firm research", "value": "sk-secret-value-7788"}).json()
        self.assertEqual(k["last4"], "7788")
        body = c.get("/api/v1/assistant/secrets").text
        self.assertNotIn("sk-secret-value-7788", body)
        self.assertNotIn("ct", c.get("/api/v1/assistant/secrets").json()["secrets"][0])
        # the sealed value is readable only through the keychain, by the server
        self.assertEqual(keychain.open_(store.secret_ct(self.account_id, k["id"])), "sk-secret-value-7788")
        # replace and delete
        c.patch("/api/v1/assistant/secrets/%d" % k["id"], json={"value": "sk-new-value-9900"})
        self.assertEqual(c.get("/api/v1/assistant/secrets").json()["secrets"][0]["last4"], "9900")
        self.assertEqual(keychain.open_(store.secret_ct(self.account_id, k["id"])), "sk-new-value-9900")
        self.assertEqual(c.delete("/api/v1/assistant/secrets/%d" % k["id"]).json()["deleted"], k["id"])
        self.assertEqual(c.get("/api/v1/assistant/secrets").json()["secrets"], [])
        self.assertEqual(c.delete("/api/v1/assistant/secrets/%d" % k["id"]).status_code, 404)
        self.assertEqual(self.client(signed=False).get("/api/v1/assistant/secrets").status_code, 401)

    def test_mcp_server_url_is_checked(self):
        c = self.client()
        for bad in ("http://example.com/mcp", "https://127.0.0.1/mcp", "https://localhost/mcp",
                    "https://10.0.0.5/mcp", "https://192.168.1.10/mcp", "https://169.254.169.254/latest",
                    "not a url", ""):
            r = c.post("/api/v1/assistant/mcp-servers", json={"label": "X", "url": bad})
            self.assertEqual(r.status_code, 400, bad)
        with self.assertRaises(mcp_client.RemoteError):
            mcp_client.check_url("https://[::1]/mcp")
        # a public address passes the check (no connection is made here)
        self.assertEqual(mcp_client.check_url("https://example.com/mcp"), "https://example.com/mcp")

    def test_desk_mcp_server_tools_reach_a_run(self):
        c = self.client()
        calls = []
        srv_tools = [{"name": "firm_note", "description": "Read an internal note.",
                      "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}}]
        orig_connect, orig_call, orig_check = mcp_client.connect, mcp_client.call_tool, mcp_client.check_url
        mcp_client.check_url = lambda u: u
        mcp_client.connect = lambda srv, secret=None: (calls.append(("connect", srv["url"], secret)),
                                                       ({"name": "firm"}, srv_tools))[1]
        mcp_client.call_tool = lambda srv, name, args, secret=None: (calls.append(("call", name, args, secret)),
                                                                     ('{"note": "ok"}', False))[1]
        try:
            k = c.post("/api/v1/assistant/secrets", json={"name": "Firm", "value": "sk-firm-0001"}).json()
            # authentication needs a stored key
            self.assertEqual(c.post("/api/v1/assistant/mcp-servers", json={
                "label": "Firm", "url": "https://tools.firm.com/mcp", "auth_kind": "bearer"}).status_code, 400)
            srv = c.post("/api/v1/assistant/mcp-servers", json={
                "label": "Firm research", "url": "https://tools.firm.com/mcp",
                "auth_kind": "bearer", "secret_id": k["id"]}).json()
            self.assertEqual(srv["status"], "ok")
            self.assertEqual([t["name"] for t in srv["tools"]], ["firm_note"])
            self.assertEqual(calls[0], ("connect", "https://tools.firm.com/mcp", "sk-firm-0001"))
            # the key cannot be deleted while the server uses it
            self.assertEqual(c.delete("/api/v1/assistant/secrets/%d" % k["id"]).status_code, 400)
            # a run is offered the tool, and calling it reaches the server with the key
            h = c.post("/api/v1/assistant/specialists/disclosure-verification/hire").json()
            c.post("/api/v1/assistant/settings", json={"model_provider": "openai", "model_key": "sk-1"})
            name = [t for t in runner.external_tools(self.account_id)][0]["schema"]["name"]
            self.assertTrue(name.startswith("ext_"))
            script = self.script([[(name, {"id": "n1"})], "Read it."])
            run = c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={"task": "check"}).json()
            self.assertIn(name, script.seen[0]["tools"])
            self.assertEqual(run["outcome"], "done")
            self.assertEqual([x["name"] for x in run["calls"]], [name])
            self.assertFalse(run["calls"][0]["error"])
            self.assertEqual(calls[-1], ("call", "firm_note", {"id": "n1"}, "sk-firm-0001"))
            # switched off, the tool is gone from the next run
            c.patch("/api/v1/assistant/mcp-servers/%d" % srv["id"], json={"enabled": False})
            script = self.script(["Nothing."])
            c.post("/api/v1/assistant/hires/%d/run" % h["id"], json={"task": "check"})
            self.assertFalse([t for t in script.seen[0]["tools"] if t.startswith("ext_")])
            # removing the server frees the key
            c.delete("/api/v1/assistant/mcp-servers/%d" % srv["id"])
            self.assertEqual(c.delete("/api/v1/assistant/secrets/%d" % k["id"]).json()["deleted"], k["id"])
        finally:
            mcp_client.connect, mcp_client.call_tool, mcp_client.check_url = orig_connect, orig_call, orig_check

    def test_unreachable_server_is_recorded_not_raised(self):
        c = self.client()
        orig = mcp_client.connect
        mcp_client.connect = lambda srv, secret=None: (_ for _ in ()).throw(mcp_client.RemoteError("nope"))
        try:
            srv = c.post("/api/v1/assistant/mcp-servers", json={"label": "Down", "url": "https://example.com/mcp"}).json()
            self.assertEqual(srv["status"], "error")
            self.assertEqual(srv["detail"], "nope")
            self.assertEqual(srv["tools"], [])
        finally:
            mcp_client.connect = orig


class GatewayShapeTests(unittest.TestCase):
    """The two wire formats, built from the neutral messages."""

    MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "get_company", "args": {"code": "7203"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "get_company", "content": "{}"}]

    def test_anthropic_shape(self):
        system, msgs = gateway._anthropic_messages(self.MSGS)
        self.assertEqual(system, "sys")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"][0]["type"], "tool_use")
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")

    def test_openai_shape(self):
        msgs = gateway._openai_messages(self.MSGS)
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool"])
        self.assertEqual(json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]), {"code": "7203"})
        self.assertEqual(msgs[3]["tool_call_id"], "1")


if __name__ == "__main__":
    unittest.main()
