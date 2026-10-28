# -*- coding: utf-8 -*-
"""The box's model calls (app/us_ask_model.py): a reasoning model that refuses
temperature is called again its own way and remembered, the cached share of
the prompt is counted, and the provider setting picks between two keys."""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from app import us_ask_model


def _refusal(message):
    return urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", 400, "Bad Request", {},
                                  io.BytesIO(json.dumps({"error": {"message": message}}).encode("utf-8")))


def _reply(cached=0, prompt=100, out=20, deepseek=False):
    usage = {"prompt_tokens": prompt, "completion_tokens": out}
    if deepseek:
        usage["prompt_cache_hit_tokens"] = cached
    else:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"choices": [{"message": {"content": "{\"shape\": \"steps\"}"}}], "usage": usage}


class CompleteTest(unittest.TestCase):
    def setUp(self):
        us_ask_model._WAYS.clear()

    def test_a_reasoning_model_is_called_its_own_way_and_remembered(self):
        bodies = []

        def post(url, key, body, timeout):
            bodies.append(body)
            if "temperature" in body:
                raise _refusal("Unsupported parameter: 'temperature' is not supported with this model.")
            return _reply(cached=90)
        with mock.patch.object(us_ask_model, "_post", post):
            out = us_ask_model._complete("openai", "k", "luna-next", [{"role": "user", "content": "q"}],
                                         max_tokens=300, cache_key="plover-us-reader")
            self.assertEqual(out["usage"], {"in": 100, "out": 20, "cached": 90})
            self.assertEqual(len(bodies), 2)
            first, second = bodies
            self.assertEqual(first["temperature"], 0)
            self.assertEqual(first["max_completion_tokens"], 300)
            self.assertEqual(first["prompt_cache_key"], "plover-us-reader")
            self.assertNotIn("temperature", second)
            self.assertNotIn("max_tokens", second)
            self.assertEqual(second["reasoning_effort"], "low")
            self.assertEqual(second["max_completion_tokens"], 300 + us_ask_model.REASONING_HEADROOM)
            # the next call goes straight to the way that worked
            us_ask_model._complete("openai", "k", "luna-next", [{"role": "user", "content": "q"}])
            self.assertEqual(len(bodies), 3)
            self.assertNotIn("temperature", bodies[2])

    def test_a_model_without_a_reasoning_setting_drops_it(self):
        bodies = []

        def post(url, key, body, timeout):
            bodies.append(body)
            if "temperature" in body:
                raise _refusal("'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.")
            if "reasoning_effort" in body:
                raise _refusal("Unrecognized request argument supplied: reasoning_effort")
            return _reply()
        with mock.patch.object(us_ask_model, "_post", post):
            us_ask_model._complete("openai", "k", "o-something", [])
        self.assertEqual([("temperature" in b, "reasoning_effort" in b) for b in bodies],
                         [(True, False), (False, True), (False, False)])
        self.assertEqual(us_ask_model._WAYS["o-something"], {"temperature": False, "reasoning": False})

    def test_a_known_reasoning_model_is_called_its_way_from_the_first_call(self):
        bodies = []

        def post(url, key, body, timeout):
            bodies.append(body)
            return _reply()
        with mock.patch.object(us_ask_model, "_post", post):
            us_ask_model._complete("openai", "k", "gpt-6-luna", [], max_tokens=300, effort="none")
        self.assertEqual(len(bodies), 1, "no call wasted on a refusal")
        self.assertNotIn("temperature", bodies[0])
        self.assertEqual(bodies[0]["reasoning_effort"], "none")
        self.assertEqual(bodies[0]["max_completion_tokens"], 300 + us_ask_model.REASONING_HEADROOM)

    def test_an_ordinary_model_guessed_to_reason_still_answers(self):
        bodies = []

        def post(url, key, body, timeout):
            bodies.append(body)
            if "reasoning_effort" in body:
                raise _refusal("Unrecognized request argument supplied: reasoning_effort")
            return _reply()
        with mock.patch.object(us_ask_model, "_post", post):
            us_ask_model._complete("openai", "k", "gpt-5-chat-latest", [])
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("reasoning_effort", bodies[1])
        self.assertNotIn("temperature", bodies[1])

    def test_the_reader_asks_for_low_reasoning(self):
        seen = {}

        def fake(prov, key, model, messages, **kw):
            seen.update(kw)
            return {"text": "{\"shape\": \"steps\"}", "usage": {}}
        us_ask_model._CACHE.clear()
        with mock.patch.object(us_ask_model, "provider", return_value=("openai", "k", "gpt-6-luna")), \
                mock.patch.object(us_ask_model, "under_cap", return_value=True), \
                mock.patch.object(us_ask_model, "_tick"), mock.patch.object(us_ask_model, "_complete", side_effect=fake):
            us_ask_model.read("how many days did tesla fall more than 5% this year", "sys")
        us_ask_model._CACHE.clear()
        self.assertEqual(seen.get("effort"), "low")
        self.assertEqual(seen.get("cache_key"), "plover-us-reader")

    def test_other_readings_are_kept_only_when_real_and_common(self):
        """"Did you mean" (2026-10-28): the reader's other readings reach the
        answer only when each is a different, whole question many askers mean."""
        raw = [{"question": "Ford long-term debt", "differs": "long-term debt only", "share": 30},
               {"question": "ford debt", "differs": "same words", "share": 60},              # the question itself
               {"question": "Ford debt in 2019", "differs": "2019", "share": 5},             # few askers mean it
               {"question": "Ford long-term debt", "differs": "repeat", "share": 40},        # a repeat
               {"question": "Ford net debt", "share": 35}, {"question": "Ford total liabilities", "share": 30}, "junk"]
        out = us_ask_model.alternatives(raw, "Ford debt")
        self.assertEqual(out, [{"question": "Ford long-term debt", "label": "long-term debt only"},
                               {"question": "Ford net debt", "label": "Ford net debt"}])
        self.assertEqual(us_ask_model.alternatives(None, "Ford debt"), [])
        self.assertEqual(us_ask_model.alternatives("nonsense", "Ford debt"), [])

        def fake(prov, key, model, messages, **kw):
            return {"text": json.dumps({"shape": "company_figure", "alternatives": raw[:1]}), "usage": {}}
        us_ask_model._CACHE.clear()
        with mock.patch.object(us_ask_model, "provider", return_value=("openai", "k", "gpt-6-luna")), \
                mock.patch.object(us_ask_model, "under_cap", return_value=True), \
                mock.patch.object(us_ask_model, "_tick"), mock.patch.object(us_ask_model, "_complete", side_effect=fake):
            form = us_ask_model.read("Ford debt", "sys")
        us_ask_model._CACHE.clear()
        self.assertEqual(form["alternatives"], [{"question": "Ford long-term debt", "label": "long-term debt only"}])

    def test_the_page_can_ask_for_other_readings_after_a_quick_answer(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import us_ask_api
        app = FastAPI()
        app.include_router(us_ask_api.router)
        alts = [{"question": "biggest banks by total assets", "label": "by total assets"}]
        with mock.patch.object(us_ask_model, "read", return_value={"shape": "industry_rank", "alternatives": alts}):
            r = TestClient(app).get("/api/v1/us/ask/readings?q=biggest+banks").json()
        self.assertEqual(r["did_you_mean"], alts)
        with mock.patch.object(us_ask_model, "read", return_value=None):           # the model is off
            r = TestClient(app).get("/api/v1/us/ask/readings?q=biggest+banks").json()
        self.assertEqual(r["did_you_mean"], [])

    def test_any_other_refusal_is_raised(self):
        with mock.patch.object(us_ask_model, "_post", side_effect=_refusal("Invalid API key")):
            with self.assertRaises(urllib.error.HTTPError):
                us_ask_model._complete("openai", "k", "gpt-4.1-mini", [])

    def test_deepseek_keeps_its_own_fields_and_counts_its_cache(self):
        bodies = []

        def post(url, key, body, timeout):
            bodies.append(body)
            return _reply(cached=64, deepseek=True)
        with mock.patch.object(us_ask_model, "_post", post):
            out = us_ask_model._complete("deepseek", "k", "deepseek-chat", [], cache_key="plover-us-steps")
        self.assertEqual(out["usage"]["cached"], 64)
        self.assertEqual(bodies[0]["max_tokens"], 300)
        self.assertEqual(bodies[0]["temperature"], 0)
        self.assertNotIn("prompt_cache_key", bodies[0])

    def test_cached_tokens_are_counted(self):
        with mock.patch.dict(us_ask_model.STATS, {"day": None}):
            us_ask_model._tick("matched", {"in": 100, "out": 20, "cached": 90})
            self.assertEqual(us_ask_model.STATS["tokens_cached"], 90)
            self.assertEqual(us_ask_model.STATS["tokens_in"], 100)


class ProviderTest(unittest.TestCase):
    def test_provider_setting_picks_between_two_keys(self):
        env = {"DEEPSEEK_API_KEY": "d", "OPENAI_API_KEY": "o", "US_ASK_MODEL": "", "US_ASK_MODEL_NAME": "", "US_ASK_PROVIDER": ""}
        # a blank setting falls through to the .env file: keep the developer's own keys out of this test
        with mock.patch.dict(os.environ, env), mock.patch.object(us_ask_model, "_ENV_FILE", "/nonexistent/.env"):
            self.assertEqual(us_ask_model.provider()[0], "deepseek", "DeepSeek first when nothing is said")
            with mock.patch.dict(os.environ, {"US_ASK_PROVIDER": "openai", "US_ASK_MODEL_NAME": "gpt-6-luna"}):
                self.assertEqual(us_ask_model.provider(), ("openai", "o", "gpt-6-luna"))
            with mock.patch.dict(os.environ, {"US_ASK_PROVIDER": "openai", "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "d"}):
                # the chosen provider has no key: off, never a silent switch to the other
                self.assertIsNone(us_ask_model.provider())


if __name__ == "__main__":
    unittest.main()
