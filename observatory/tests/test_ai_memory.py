# -*- coding: utf-8 -*-
"""Conversations in the desk's AI tab, and the memory a person's assistant keeps.

What is proved: a follow-up is sent the earlier exchanges (as messages to an
API model, as a transcript to the CLIs), a failed exchange is left out, and a
conversation survives the run and a restart (a lost run reads as stopped);
nobody sees another person's conversation; the model saves notes to the
writer's memory on its own, each save shows under its reply and Undo removes
it; the next conversation starts with the notes; memory switched off means no
notes and no memory tools; notes are capped, deduplicated and editable from
My Account; the Investment Assistant's own chat reads and writes the same
memory, and its scheduled runs do not.
"""
from app import research, research_ai, research_ai_mcp, research_chat
from app.assistant import gateway, memory, runner, store as ia_store
from app import writer_api

from tests.test_research_ai import AIBase, Fake, call, say


class Recorder(Fake):
    """A scripted model that also keeps the messages of each call."""

    def __init__(self, turns, gate=None):
        super(Recorder, self).__init__(turns, gate)
        self.calls = []

    def __call__(self, provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
        self.calls.append([dict(m) for m in messages])
        return super(Recorder, self).__call__(provider, key, model, messages, tools, max_tokens, base_url)


class ConversationTest(AIBase):
    def run_ai(self, text, chat_id=None, skill_id=None):
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": text, "chat_id": chat_id, "skill_id": skill_id})
        self.assertEqual(r.status_code, 200, r.text)
        return self.wait(r.json()["id"])

    def test_follow_up_carries_the_earlier_exchange(self):
        self.connect()
        fake = Recorder([say("1. Bank margins\n2. Yen carry\n3. JGB auctions"), say("More on JGB auctions.")])
        gateway.complete = fake
        first = self.run_ai("Give me three angles.")
        self.assertTrue(first["chat_id"])
        second = self.run_ai("Expand the third one.", chat_id=first["chat_id"])
        self.assertEqual(second["chat_id"], first["chat_id"])
        # nothing changed between the two: one "Before AI" copy in History, not two
        self.assertEqual(second["before_history_id"], first["before_history_id"])
        self.assertEqual([h["label"] for h in research.history(self.aid)].count("Before AI"), 1)
        sent = fake.calls[1]
        roles = [m["role"] for m in sent]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(sent[1]["content"], "Give me three angles.")
        self.assertIn("JGB auctions", sent[2]["content"])
        self.assertEqual(sent[3]["content"], "Expand the third one.")
        self.assertIn("continues a conversation", sent[0]["content"])
        # the first run was a fresh start
        self.assertEqual([m["role"] for m in fake.calls[0]], ["system", "user"])
        self.assertNotIn("continues a conversation", fake.calls[0][0]["content"])

        view = self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()
        self.assertEqual(len(view["chats"]), 1)
        self.assertEqual(view["chats"][0]["title"], "Give me three angles.")
        turns = view["chat"]["turns"]
        self.assertEqual([(t["role"], t["status"]) for t in turns],
                         [("user", "done"), ("assistant", "done")] * 2)
        self.assertEqual(turns[3]["text"], "More on JGB auctions.")

    def test_a_new_conversation_starts_fresh_and_failures_are_left_out(self):
        self.connect()

        def boom(*a, **k):
            raise gateway.GatewayError("The model API refused the request (429).")
        gateway.complete = boom
        failed = self.run_ai("First try.")
        self.assertEqual(failed["status"], "failed")
        fake = Recorder([say("Answer.")])
        gateway.complete = fake
        self.run_ai("Second try.", chat_id=failed["chat_id"])
        # the failed exchange is not sent: no two writer messages in a row
        self.assertEqual([m["role"] for m in fake.calls[0]], ["system", "user"])
        gateway.complete = Recorder([say("Fresh.")])
        fresh = self.run_ai("Something else.")
        self.assertNotEqual(fresh["chat_id"], failed["chat_id"])
        self.assertEqual(len(self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()["chats"]), 2)

    def test_a_run_survives_a_change_of_server_copy(self):
        """After a deploy the new copy of the app never started the run: it
        reads the reply the conversation keeps, and only a reply past any
        run's time limit is called stopped."""
        self.connect()
        gateway.complete = Recorder([say("Done.")])
        j = self.run_ai("Hello.")
        aid = research_ai._account_id(staff_person(self))
        turn = research_chat.chat(aid, j["chat_id"])["turns"][1]
        research_ai._jobs.clear()                    # the copy that ran it is gone

        # finished on the old copy: the new one reports the reply, not a 404
        got = self.w.get("/admin/api/research/ai/jobs/" + j["id"])
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()["status"], got.json()["reply"]), ("done", "Done."))

        # still running on the old copy: the new one says so and keeps waiting
        research_chat.finish_turn(turn["id"], "running", "", {"job_id": j["id"]})
        got = self.w.get("/admin/api/research/ai/jobs/" + j["id"]).json()
        self.assertEqual(got["status"], "running")
        self.assertTrue(got["elsewhere"])
        view = self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()
        self.assertEqual(view["chat"]["turns"][1]["status"], "running")

        # past any run's time limit: it was cut off, and says what to do
        ia_store._write("UPDATE ia_article_turns SET created_at = created_at - ? WHERE id = ?",
                        (research_ai.LOST_AFTER + 1, turn["id"]))
        view = self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()
        self.assertEqual(view["chat"]["turns"][1]["status"], "failed")
        self.assertIn("Send your message again", view["chat"]["turns"][1]["text"])
        # someone else cannot read it either way
        other, _ = self.person("o@example.com", ["writing"])
        self.assertEqual(other.get("/admin/api/research/ai/jobs/" + j["id"]).status_code, 404)

    def test_conversations_are_private_and_deletable(self):
        self.connect()
        gateway.complete = Recorder([say("Mine.")])
        j = self.run_ai("Private question.")
        other, _ = self.person("o@example.com", ["writing"])
        theirs = other.get("/admin/api/research/articles/%d/ai/chats" % self.aid)
        if theirs.status_code == 200:
            self.assertEqual(theirs.json()["chats"], [])
        r = other.post("/admin/api/research/articles/%d/ai" % self.aid,
                       json={"instruction": "x", "chat_id": j["chat_id"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(other.delete("/admin/api/research/ai/chats/%d" % j["chat_id"]).status_code, 404)
        self.assertEqual(self.w.delete("/admin/api/research/ai/chats/%d" % j["chat_id"]).status_code, 200)
        self.assertEqual(self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()["chats"], [])

    def test_cli_prompt_carries_a_transcript(self):
        hist = [{"role": "user", "text": "Three angles?"}, {"role": "assistant", "text": "A, B, C."}]
        text = research_ai._prompt("SYSTEM", "Expand C.", hist)
        self.assertLess(text.index("The writer: Three angles?"), text.index("You: A, B, C."))
        self.assertLess(text.index("You: A, B, C."), text.index("Instruction from the writer:\nExpand C."))
        self.assertEqual(research_ai._prompt("SYSTEM", "Hi."), "SYSTEM\n\nInstruction from the writer:\nHi.")


def staff_person(test):
    from app import staff
    return staff.get(test.wid)


class MemoryTest(AIBase):
    def run_ai(self, text, chat_id=None):
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": text, "chat_id": chat_id})
        self.assertEqual(r.status_code, 200, r.text)
        return self.wait(r.json()["id"])

    def test_saves_on_its_own_shows_it_and_undo_removes_it(self):
        self.connect()
        fake = Recorder([call("remember", text="Covers Japanese regional banks."),
                         say("Here are three bank angles.")])
        gateway.complete = fake
        j = self.run_ai("I cover regional banks. Ideas?")
        self.assertIn("remember", fake.seen_tools)
        self.assertEqual([n["text"] for n in j["memory"]], ["Covers Japanese regional banks."])
        self.assertIn("Saved to memory: Covers Japanese regional banks.", [s["text"] for s in j["steps"]])
        view = self.w.get("/admin/api/research/articles/%d/ai/chats" % self.aid).json()
        self.assertEqual([n["text"] for n in view["chat"]["turns"][1]["memory"]], ["Covers Japanese regional banks."])

        # a new conversation starts with the note
        fake2 = Recorder([say("Bank ideas.")])
        gateway.complete = fake2
        self.run_ai("Any ideas?")
        self.assertIn("Covers Japanese regional banks.", fake2.calls[0][0]["content"])

        # Undo under the reply
        nid = j["memory"][0]["id"]
        r = self.w.delete("/admin/api/research/ai/memory/%d" % nid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["memory"]["notes"], 0)
        self.assertEqual(self.w.delete("/admin/api/research/ai/memory/%d" % nid).status_code, 404)
        fake3 = Recorder([say("Ideas.")])
        gateway.complete = fake3
        self.run_ai("Any ideas?")
        self.assertNotIn("regional banks", fake3.calls[0][0]["content"])

    def test_switched_off_means_no_notes_and_no_tools(self):
        self.connect()
        self.app.include_router(writer_api.router)
        r = self.w.post("/admin/api/write/memory", json={"text": "Prefers short pieces."})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.w.put("/admin/api/write/memory/settings", json={"enabled": False}).json()["enabled"], False)
        fake = Recorder([say("Ok.")])
        gateway.complete = fake
        self.run_ai("Hello.")
        self.assertNotIn("remember", fake.seen_tools)
        self.assertNotIn("Prefers short pieces", fake.calls[0][0]["content"])
        self.assertFalse(self.w.get("/admin/api/research/ai").json()["memory"]["enabled"])

    def test_my_account_memory_page(self):
        self.app.include_router(writer_api.router)
        base = "/admin/api/write/memory"
        v = self.w.post(base, json={"text": "  Expects a BOJ hike in December.  "}).json()
        self.assertEqual([n["text"] for n in v["notes"]], ["Expects a BOJ hike in December."])
        self.assertEqual(v["notes"][0]["place"], "You")
        again = self.w.post(base, json={"text": "expects a BOJ hike in December"}).json()
        self.assertEqual(len(again["notes"]), 1)                       # the same fact once
        self.assertEqual(self.w.post(base, json={"text": "x" * 400}).status_code, 400)
        nid = v["notes"][0]["id"]
        e = self.w.patch("%s/%d" % (base, nid), json={"text": "Expects a BOJ hike in January."}).json()
        self.assertEqual(e["notes"][0]["text"], "Expects a BOJ hike in January.")
        other, _ = self.person("o@example.com", ["writing"])
        self.assertEqual(other.get(base).json()["notes"], [])          # nobody else's
        self.assertEqual(other.delete("%s/%d" % (base, nid)).status_code, 404)
        self.assertEqual(self.w.delete("%s/%d" % (base, nid)).json()["notes"], [])
        self.w.post(base, json={"text": "One."})
        self.w.post(base, json={"text": "Two."})
        self.assertEqual(self.w.delete(base).json()["notes"], [])
        self.assertEqual(self.admin.get(base).status_code, 403)        # the shared password is nobody

    def test_cap(self):
        aid = 1
        for i in range(memory.MAX_NOTES):
            memory.add(aid, "Note number %d." % i, "you", memory.YOU)
        with self.assertRaises(memory.NoteError):
            memory.add(aid, "One too many.", "you", memory.YOU)
        text, err = memory.run_tool(aid, "remember", {"text": "One too many."}, "desk:x", memory.DESK)
        self.assertTrue(err)
        self.assertIn("full", text)

    def test_cli_tool_server_offers_memory_when_on(self):
        self.connect()
        from app.assistant import market_data
        aid = market_data.account_for("w@example.com", create=True)
        spec = {"staff_id": self.wid, "article_id": self.aid, "client": "Codex", "log": None,
                "account_id": aid, "memory": True, "origin": "desk:abc"}
        ctx = research_ai_mcp.Context(spec)
        self.assertIn("remember", [t["name"] for t in ctx.tools])
        text, err = ctx.call("remember", {"text": "Writes for institutional readers."})
        self.assertFalse(err, text)
        self.assertEqual([n["text"] for n in memory.saved_by(aid, "desk:abc")], ["Writes for institutional readers."])
        spec["memory"] = False
        self.assertNotIn("remember", [t["name"] for t in research_ai_mcp.Context(spec).tools])


class AssistantMemoryTest(AIBase):
    """The Investment Assistant's chat shares the person's memory."""

    def test_chat_reads_and_writes_the_same_memory(self):
        from app.assistant import market_data
        aid = market_data.account_for("w@example.com", create=True)
        memory.add(aid, "Covers Japanese regional banks.", "desk:old", memory.DESK)
        hire = {"id": 1, "slug": "analyst", "version": "1.0.0", "config": {}}
        seen = {}

        def fake(provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
            seen.setdefault("system", messages[0]["content"])
            seen.setdefault("tools", [t["name"] for t in tools or []])
            if "saved" not in seen:
                seen["saved"] = True
                out = call("remember", text="Expects a BOJ hike in December.")
            else:
                out = say("Noted.")
            out["usage"] = {"in": 0, "out": 0}
            return out
        gateway.complete = fake
        r = runner.Run(aid, hire, "message", "I expect a BOJ hike in December.", mode="thread", remember=True)
        r._model_key = lambda desk: "sk-test"
        ia_store.desk(aid)
        ia_store._write("UPDATE ia_desks SET model_provider = 'openai' WHERE account_id = ?", (aid,))
        out = r.execute()
        self.assertEqual(out["outcome"], "done", out)
        self.assertIn("Covers Japanese regional banks.", seen["system"])
        self.assertIn("remember", seen["tools"])
        texts = [n["text"] for n in memory.notes(aid)]
        self.assertIn("Expects a BOJ hike in December.", texts)
        self.assertEqual(memory.notes(aid)[-1]["place"], memory.ASSISTANT)

        # a scheduled run neither reads nor writes it
        seen.clear()
        seen["saved"] = True
        gateway.complete = fake
        r2 = runner.Run(aid, hire, "schedule", "Check coverage.", mode="desk")
        r2._model_key = lambda desk: "sk-test"
        r2.execute()
        self.assertNotIn("Covers Japanese regional banks", seen["system"])
        self.assertNotIn("remember", seen["tools"])
