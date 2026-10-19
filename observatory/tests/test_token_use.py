# -*- coding: utf-8 -*-
"""Token use of the AI runs: prompt caching asked for, and every run counted.

What is proved: every Anthropic request asks for prompt caching (a marker at
the end of the system prompt and automatic caching of the conversation behind
it); usage is read the same way from every provider, "in" being all input
read and "cached" the part served from the cache; and an AI-tab run adds up
its steps' usage and keeps the count with its reply.
"""
import time
import unittest

from app import research_ai
from app.assistant import gateway

from tests.test_research_ai import AIBase, Fake, call


class Posts(object):
    """Stands in for the HTTP call: keeps each body, answers with a reply."""

    def __init__(self, reply):
        self.reply = reply
        self.bodies = []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self.reply


# Other test files swap gateway.complete for a scripted model, so these tests
# call the provider functions it dispatches to.
def _complete(provider, key, model, messages, tools=None):
    if provider == "anthropic":
        return gateway._anthropic(key, model, messages, tools or [], 1500, None)
    return gateway._openai(key, model, messages, tools or [], 1500,
                           gateway.PROVIDERS[provider]["base_url"])


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self._post = gateway._post
        self.addCleanup(setattr, gateway, "_post", self._post)

    def test_anthropic_asks_for_caching_and_counts_cache(self):
        gateway._post = posts = Posts({
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 40, "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 9000, "output_tokens": 25},
            "stop_reason": "end_turn"})
        out = _complete("anthropic", "sk-ant-x", "claude-sonnet-4-5",
                               [{"role": "system", "content": "You write about Japan."},
                                {"role": "user", "content": "Hi"}],
                               tools=[{"name": "t", "description": "d", "parameters": {"type": "object"}}])
        body = posts.bodies[0]
        self.assertEqual(body["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["system"], [{"type": "text", "text": "You write about Japan.",
                                           "cache_control": {"type": "ephemeral"}}])
        # all input read, of which 9,000 from the cache
        self.assertEqual(out["usage"], {"in": 10040, "out": 25, "cached": 9000})

    def test_anthropic_without_system_still_caches_the_conversation(self):
        gateway._post = posts = Posts({"content": [], "usage": {}, "stop_reason": "end_turn"})
        out = _complete("anthropic", "sk-ant-x", "m", [{"role": "user", "content": "Hi"}])
        self.assertNotIn("system", posts.bodies[0])
        self.assertEqual(posts.bodies[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(out["usage"], {"in": 0, "out": 0, "cached": 0})

    def test_openai_and_deepseek_report_their_cache(self):
        gateway._post = Posts({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 5000, "completion_tokens": 10,
                                         "prompt_tokens_details": {"cached_tokens": 4096}}})
        out = _complete("openai", "sk-x", "gpt", [{"role": "user", "content": "Hi"}])
        self.assertEqual(out["usage"], {"in": 5000, "out": 10, "cached": 4096})
        gateway._post = Posts({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 3000, "completion_tokens": 7,
                                         "prompt_cache_hit_tokens": 2944}})
        out = _complete("deepseek", "sk-x", "deepseek-chat", [{"role": "user", "content": "Hi"}])
        self.assertEqual(out["usage"], {"in": 3000, "out": 7, "cached": 2944})


class CountTest(unittest.TestCase):
    def test_count_adds_steps_and_tolerates_gaps(self):
        job = {}
        research_ai._count(job, {"in": 1000, "out": 50, "cached": 0})
        research_ai._count(job, {"in": 1200, "out": 40, "cached": 990})
        research_ai._count(job, {})                       # a provider that said nothing
        research_ai._count(job, None)
        research_ai._count(job, {"in": "x", "out": None})  # nonsense is not a crash
        self.assertEqual(job["tokens"], {"in": 2200, "out": 90, "cached": 990, "steps": 5})
        research_ai._count(job, {"in": 300, "steps": 7})   # a CLI run reports its own turns
        self.assertEqual(job["tokens"]["steps"], 12)


def _used(turn, n_in, n_out, cached):
    turn["usage"] = {"in": n_in, "out": n_out, "cached": cached}
    return turn


class RunCountTest(AIBase):
    def test_run_counts_its_tokens_and_keeps_them_with_the_reply(self):
        self.connect()
        gateway.complete = Fake([
            _used(call("get_draft"), 3000, 40, 0),
            _used({"text": "Read it.", "tool_calls": [], "usage": {}, "stop": "end"}, 3500, 20, 2900)])
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": "Read the draft."})
        self.assertEqual(r.status_code, 200, r.text)
        j = self.wait(r.json()["id"])
        self.assertEqual(j["status"], "done", j)
        want = {"in": 6500, "out": 60, "cached": 2900, "steps": 2}
        self.assertEqual(j["tokens"], want)
        # the conversation keeps the count with the reply, so a reload shows it
        research_ai._jobs.clear()
        for _ in range(20):
            again = self.w.get("/admin/api/research/ai/jobs/" + j["id"])
            if again.status_code == 200:
                break
            time.sleep(0.05)
        self.assertEqual(again.json()["tokens"], want)


if __name__ == "__main__":
    unittest.main()
