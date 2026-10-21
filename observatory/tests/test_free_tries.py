# -*- coding: utf-8 -*-
"""Free tries (app/assistant/free.py): a few answers and summaries on the
server's own AI key for someone who has not added theirs.

What is proved: a summary without a key runs on the server's key and uses one
try; the tries stop at FREE_TRIES with a message that says to add a key; a
failed summary gives its try back; the day's cap across everyone holds; a
person with their own key uses it and no try; with no server key the old
"add your own key" message stands; /settings reports the count and never the
key.
"""
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
from app.assistant import free, gateway, keychain, reading, store  # noqa: E402

ARTICLE = {"url": "https://news.example.com/a", "title": "Yields rise", "site": "Example News",
           "blocks": [{"kind": "p", "text": "The ten-year yield rose to 4.1%."}]}
ENV = ("FREE_TRIES", "FREE_TRIES_PER_DAY", "FREE_TRIES_PROVIDER", "FREE_TRIES_MODEL",
       "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


class FreeTries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        reading.reset_for_tests()
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        cls.app = app
        db = accounts.conn()
        now = accounts._now()
        ids = []
        for email, tok in (("new@example.com", "tok-a"), ("keyed@example.com", "tok-b")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) "
                       "VALUES (?, ?, ?, ?)", (accounts._hash(tok), aid, now, now + 3600))
            ids.append(aid)
        db.commit()
        cls.aid, cls.keyed = ids
        cls._complete = gateway.complete
        cls._env = dict((k, os.environ.get(k)) for k in ENV)

    @classmethod
    def tearDownClass(cls):
        gateway.complete = cls._complete
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        for k in ENV:
            os.environ.pop(k, None)
        os.environ["FREE_TRIES"] = "2"
        os.environ["DEEPSEEK_API_KEY"] = "sk-house-key"
        db = store.conn()
        reading._conn()
        for t in ("ia_reading", "ia_desks", "ia_free_uses", "ia_free_days"):
            db.execute("DELETE FROM " + t)
        db.commit()
        self.calls = []

        def fake(provider, key, model, messages, tools=None, max_tokens=None):
            self.calls.append((provider, key, model))
            return {"text": "- The ten-year yield rose.", "usage": {"in": 1, "out": 1}}
        gateway.complete = fake

    def client(self, token="tok-a"):
        c = TestClient(self.app)
        c.cookies.set(accounts.COOKIE, token)
        return c

    def summarise(self, token="tok-a"):
        c = self.client(token)
        iid = c.post("/api/v1/assistant/reading", json=ARTICLE).json()["id"]
        return c.post("/api/v1/assistant/reading/%d/summary" % iid)

    def test_tries_run_on_the_server_key_and_stop_at_the_limit(self):
        self.assertEqual(self.summarise().status_code, 200)
        self.assertEqual(self.calls[-1][:2], ("deepseek", "sk-house-key"))
        self.assertEqual(self.summarise().status_code, 200)
        r = self.summarise()
        self.assertEqual(r.status_code, 400)
        self.assertIn("free tries are used up", r.json()["detail"])
        self.assertEqual(len(self.calls), 2)
        s = self.client().get("/api/v1/assistant/settings").json()
        self.assertEqual((s["free"]["on"], s["free"]["used"], s["free"]["left"]), (True, 2, 0))
        self.assertNotIn("sk-house-key", str(s))

    def test_a_failed_summary_gives_its_try_back(self):
        def broken(*a, **k):
            raise gateway.GatewayError("timed out")
        gateway.complete = broken
        self.assertEqual(self.summarise().status_code, 400)
        self.assertEqual(free.used(self.aid), 0)

    def test_the_days_cap_holds_across_everyone(self):
        os.environ["FREE_TRIES_PER_DAY"] = "1"
        self.assertEqual(self.summarise().status_code, 200)
        r = self.summarise()
        self.assertEqual(r.status_code, 400)
        self.assertIn("try again tomorrow", r.json()["detail"])
        self.assertEqual(free.used(self.aid), 1)

    def test_own_key_is_used_and_no_try_taken(self):
        store.update_desk(self.keyed, model_provider="openai", model_key_ct=keychain.seal("sk-own-9999"),
                          model_key_last4="9999")
        self.assertEqual(self.summarise("tok-b").status_code, 200)
        self.assertEqual(self.calls[-1][:2], ("openai", "sk-own-9999"))
        self.assertEqual(free.used(self.keyed), 0)

    def test_no_server_key_means_add_your_own(self):
        os.environ.pop("DEEPSEEK_API_KEY")
        r = self.summarise()
        self.assertEqual(r.status_code, 400)
        self.assertIn("own AI key", r.json()["detail"])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.client().get("/api/v1/assistant/settings").json()["free"]["on"])


if __name__ == "__main__":
    unittest.main()
