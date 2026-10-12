# -*- coding: utf-8 -*-
"""The desk's AI tab: a writer's own model working on the open draft.

What is proved: only a personal login can use it (the shared password is
nobody, and keys are personal); a key is checked before it is stored, stored
sealed and never sent back; a run changes the one draft it was started on and
no other, even when the model names another article; its saves are recorded
as "<email> via <provider>" after a "Before AI" copy that restores the draft
exactly; no tool offered can publish; one run per writer at a time; the stdio
server the CLIs use offers the same tools, marked read-only so Codex runs
them, and logs each call for the progress list.
"""
import io
import json
import os
import tempfile
import threading
import time

from fastapi.testclient import TestClient

from app import accounts, research, research_ai, research_ai_mcp, staff
from app.assistant import gateway, store as ia_store

from tests.test_research_mcp import Base


class Fake(object):
    """A scripted model: each turn returns the next reply."""

    def __init__(self, turns, gate=None):
        self.turns = list(turns)
        self.seen_tools = None
        self.gate = gate

    def __call__(self, provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
        self.seen_tools = [t["name"] for t in tools or []]
        if self.gate is not None:
            self.gate.wait(5)
        return self.turns.pop(0)


def call(name, **args):
    return {"text": "", "tool_calls": [{"id": "c%d" % time.time_ns(), "name": name, "args": args}],
            "usage": {}, "stop": "tool_use"}


def say(text):
    return {"text": text, "tool_calls": [], "usage": {}, "stop": "end"}


class AIBase(Base):
    def setUp(self):
        super(AIBase, self).setUp()
        # sign-in accounts, memory and conversations go to a file of the test's own
        saved = (accounts.DB_PATH, ia_store.DB_PATH)
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        ia_store.reset_for_tests(path)

        def put_back():
            accounts.DB_PATH = saved[0]
            accounts._conn = None
            ia_store.reset_for_tests(saved[1])
            tmp.cleanup()
        self.addCleanup(put_back)
        self.app.include_router(research_ai.router)
        self._secret = os.environ.get("ASSISTANT_SECRET")
        os.environ["ASSISTANT_SECRET"] = "test-secret-" + "x" * 32
        self._complete, self._ping = gateway.complete, gateway.ping
        gateway.ping = lambda provider, key, model, base_url=None: "OK"
        self.addCleanup(self._undo)
        research_ai._jobs.clear()
        research_ai._running.clear()
        r = self.w.post("/admin/api/research/articles", json={})
        self.aid = r.json()["id"]
        a = research.get(self.aid)
        d = dict(a["draft"], title="Yen and Rates")
        d["blocks"] = [{"type": "p", "html": "My first line."}]
        research.save(self.aid, d, a["revision"], "w@example.com")
        self.other = self.w.post("/admin/api/research/articles", json={}).json()["id"]

    def _undo(self):
        gateway.complete, gateway.ping = self._complete, self._ping
        if self._secret is None:
            os.environ.pop("ASSISTANT_SECRET", None)
        else:
            os.environ["ASSISTANT_SECRET"] = self._secret

    def connect(self):
        r = self.w.put("/admin/api/research/ai", json={"provider": "openai", "key": "sk-test-abcdef1234"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def wait(self, job_id):
        for _ in range(100):
            j = self.w.get("/admin/api/research/ai/jobs/" + job_id).json()
            if j["status"] != "running":
                return j
            time.sleep(0.05)
        self.fail("run did not finish")


class AITest(AIBase):
    def test_personal_login_only(self):
        self.assertEqual(self.admin.get("/admin/api/research/ai").status_code, 403)
        self.assertEqual(TestClient(self.app).get("/admin/api/research/ai").status_code, 401)

    def test_key_checked_sealed_never_returned(self):
        view = self.connect()
        self.assertEqual(view["provider"], "openai")
        self.assertEqual(view["key_last4"], "1234")
        self.assertNotIn("sk-test-abcdef1234", json.dumps(view))
        stored = staff.ai_settings(self.wid)["key_ct"]
        self.assertNotIn("abcdef", stored)

        def bad(*a, **k):
            raise gateway.GatewayError("401 invalid key")
        gateway.ping = bad
        r = self.w.put("/admin/api/research/ai", json={"provider": "anthropic", "key": "sk-ant-nope"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("did not work", r.json()["detail"])
        r = self.w.put("/admin/api/research/ai", json={"provider": "claude_code", "claude_token": "hello"})
        self.assertEqual(r.status_code, 400)

    def test_run_changes_only_its_draft_and_undoes(self):
        self.connect()
        before = research.get(self.aid)["draft"]
        fake = Fake([call("get_draft"),
                     call("append_to_draft", markdown="A paragraph from the model.",
                          article_id=self.other),                  # ignored: bound to this draft
                     call("set_details", dek="One sentence under the title.", article_id=self.other),
                     say("Added a paragraph and a standfirst.")])
        gateway.complete = fake
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": "Add a paragraph."})
        self.assertEqual(r.status_code, 200, r.text)
        j = self.wait(r.json()["id"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["reply"], "Added a paragraph and a standfirst.")
        self.assertTrue(j["changed"])
        self.assertEqual([s["text"] for s in j["steps"]],
                         ["Read the draft", "Added to the draft", "Updated dek"])
        for forbidden in ("publish", "withdraw", "delete_article", "create_draft", "list_articles"):
            self.assertNotIn(forbidden, fake.seen_tools)
        a = research.get(self.aid)
        self.assertEqual(a["draft"]["dek"], "One sentence under the title.")
        self.assertIn("A paragraph from the model.", json.dumps(a["draft"]["blocks"]))
        self.assertEqual(a["updated_by"], "w@example.com via OpenAI")
        other = research.get(self.other)
        self.assertEqual(other["revision"], 1)
        labels = [h["label"] for h in research.history(self.aid)]
        self.assertIn("Before AI", labels)
        restored = research.history_draft(self.aid, j["before_history_id"])
        self.assertEqual(restored, before)

    def test_one_run_at_a_time(self):
        self.connect()
        gate = threading.Event()
        gateway.complete = Fake([say("done")], gate=gate)
        first = self.w.post("/admin/api/research/articles/%d/ai" % self.aid, json={"instruction": "x"})
        self.assertEqual(first.status_code, 200)
        second = self.w.post("/admin/api/research/articles/%d/ai" % self.aid, json={"instruction": "y"})
        self.assertEqual(second.status_code, 400)
        self.assertIn("still working", second.json()["detail"])
        gate.set()
        self.assertEqual(self.wait(first.json()["id"])["status"], "done")
        # someone else cannot read my run
        other, _ = self.person("o@example.com", ["writing"])
        self.assertEqual(other.get("/admin/api/research/ai/jobs/" + first.json()["id"]).status_code, 404)

    def test_failed_model_says_why(self):
        self.connect()

        def boom(*a, **k):
            raise gateway.GatewayError("The model API refused the request (429).")
        gateway.complete = boom
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid, json={"instruction": "x"})
        j = self.wait(r.json()["id"])
        self.assertEqual(j["status"], "failed")
        self.assertIn("429", j["error"])
        self.assertFalse(j["changed"])

    def test_needs_a_provider_first(self):
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid, json={"instruction": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("connect a provider", r.json()["detail"])


class ModelListTest(AIBase):
    def models(self, provider, key=None):
        r = self.w.post("/admin/api/research/ai/models", json={"provider": provider, "key": key})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_lists_per_provider(self):
        ids = [m["id"] for m in self.models("claude_code")["models"]]
        self.assertEqual(ids, ["fable", "opus", "sonnet", "haiku"])
        self.assertIn("Connect ChatGPT first", self.models("codex")["error"])
        self.assertIn("Paste your key", self.models("openai")["error"])
        seen = []

        def fake_get(url, headers):
            seen.append(headers)
            return {"data": [{"id": "gpt-5.5", "created": 2}, {"id": "text-embedding-3-large", "created": 9},
                             {"id": "gpt-6", "created": 5}, {"id": "tts-1", "created": 7}]}
        real, research_ai._get_json = research_ai._get_json, fake_get
        try:
            listed = self.models("openai", key="sk-pasted-9999")
            self.assertEqual([m["id"] for m in listed["models"]], ["gpt-6", "gpt-5.5"])   # newest first
            self.assertEqual(seen[0]["Authorization"], "Bearer sk-pasted-9999")
            self.connect()
            self.models("openai")                               # the stored key is used, then cached
            self.models("openai")
            self.assertEqual(len(seen), 2)
        finally:
            research_ai._get_json = real
        self.assertEqual(self.w.post("/admin/api/research/ai/models",
                                     json={"provider": "nope"}).status_code, 400)


class StdioServerTest(AIBase):
    def test_cli_tool_server(self):
        log = os.path.join(str(research.DATA_DIR), "calls.jsonl")
        ctx = research_ai_mcp.Context({"staff_id": self.wid, "article_id": self.aid,
                                       "client": "Codex", "log": log})
        out = io.StringIO()
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "append_to_draft", "arguments": {"markdown": "From Codex."}}}]
        research_ai_mcp.serve(ctx, io.StringIO("\n".join(json.dumps(m) for m in msgs)), out)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 3)                          # no reply to a notification
        tools = replies[1]["result"]["tools"]
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
        self.assertIn("replace_draft", [t["name"] for t in tools])
        self.assertFalse(replies[2]["result"]["isError"], replies[2])
        self.assertEqual(research.get(self.aid)["updated_by"], "w@example.com via Codex")
        logged = [json.loads(l) for l in open(log)]
        self.assertEqual(logged[0]["name"], "append_to_draft")
        self.assertNotIn("markdown", logged[0]["args"])             # the log carries no draft text


if __name__ == "__main__":
    import unittest
    unittest.main()
