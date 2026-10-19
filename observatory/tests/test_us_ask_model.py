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
            out = us_ask_model._complete("openai", "k", "gpt-6-luna", [{"role": "user", "content": "q"}],
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
            us_ask_model._complete("openai", "k", "gpt-6-luna", [{"role": "user", "content": "q"}])
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
