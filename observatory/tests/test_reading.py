# -*- coding: utf-8 -*-
"""Your reading (app/assistant/reading.py): articles saved from a person's own
browser, kept per account, summarised on that person's own AI key.

What is proved: a signed-out caller is refused; the text is cleaned and an
empty page is refused with what to do; saving the same address again
replaces the copy; one account never sees another's articles; delete works
once; a summary without a key says how to add one and never calls a model;
with a key the model's bullets are kept, and nothing it returns is stored as
anything but a summary of that article.
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
from app.assistant import gateway, keychain, reading, store  # noqa: E402

ARTICLE = {
    "url": "https://news.example.com/markets/japan-cpi?utm_source=x",
    "title": "Japan's core inflation holds near 2%",
    "site": "Example News",
    "published": "2026-10-17",
    "author": "A. Writer",
    "blocks": [
        {"kind": "h", "text": "Prices"},
        {"kind": "p", "text": "  Japan's core inflation   is running at about 2%,  the data show. "},
        {"kind": "script", "text": "not a block kind"},
        {"kind": "p", "text": ""},
    ],
}


class Reading(unittest.TestCase):
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
        for email, tok in (("reader@example.com", "tok-a"), ("other@example.com", "tok-b")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) "
                       "VALUES (?, ?, ?, ?)", (accounts._hash(tok), aid, now, now + 3600))
            ids.append(aid)
        db.commit()
        cls.aid, cls.other = ids
        cls._complete = gateway.complete

    @classmethod
    def tearDownClass(cls):
        gateway.complete = cls._complete
        cls.tmp.cleanup()

    def setUp(self):
        db = store.conn()
        reading._conn()
        db.execute("DELETE FROM ia_reading")
        db.execute("DELETE FROM ia_desks")
        db.commit()
        gateway.complete = self._complete

    def client(self, token="tok-a"):
        c = TestClient(self.app)
        if token:
            c.cookies.set(accounts.COOKIE, token)
        return c

    def save(self, body=None, token="tok-a"):
        return self.client(token).post("/api/v1/assistant/reading", json=body or ARTICLE)

    def test_signed_out_is_refused(self):
        c = self.client(token=None)
        self.assertEqual(c.get("/api/v1/assistant/reading").status_code, 401)
        self.assertEqual(c.post("/api/v1/assistant/reading", json=ARTICLE).status_code, 401)

    def test_text_is_cleaned_and_empty_pages_refused(self):
        r = self.save()
        self.assertEqual(r.status_code, 200, r.text)
        item = self.client().get("/api/v1/assistant/reading/%d" % r.json()["id"]).json()
        self.assertEqual(item["blocks"], [{"kind": "h", "text": "Prices"},
                                          {"kind": "p", "text": "Japan's core inflation is running at about 2%, the data show."}])
        self.assertIsNone(item["summary"])
        empty = dict(ARTICLE, blocks=[{"kind": "p", "text": "   "}])
        r = self.save(empty)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Open the article itself", r.json()["detail"])
        r = self.save(dict(ARTICLE, url="javascript:alert(1)"))
        self.assertEqual(r.status_code, 400)

    def test_same_address_replaces_and_accounts_stay_apart(self):
        self.save()
        self.save(dict(ARTICLE, url="https://news.example.com/markets/japan-cpi", title="Updated"))
        mine = self.client().get("/api/v1/assistant/reading").json()["reading"]
        self.assertEqual([i["title"] for i in mine], ["Updated"])
        self.assertNotIn("blocks", mine[0])
        theirs = self.client("tok-b").get("/api/v1/assistant/reading").json()["reading"]
        self.assertEqual(theirs, [])
        self.assertEqual(self.client("tok-b").get("/api/v1/assistant/reading/%d" % mine[0]["id"]).status_code, 404)
        self.assertEqual(self.client("tok-b").delete("/api/v1/assistant/reading/%d" % mine[0]["id"]).status_code, 404)

    def test_delete_once(self):
        iid = self.save().json()["id"]
        self.assertEqual(self.client().delete("/api/v1/assistant/reading/%d" % iid).status_code, 200)
        r = self.client().delete("/api/v1/assistant/reading/%d" % iid)
        self.assertEqual(r.status_code, 404)
        self.assertIn("no longer in your reading", r.json()["detail"])

    def test_summary_without_a_key_says_what_to_do(self):
        iid = self.save().json()["id"]
        calls = []
        gateway.complete = lambda *a, **k: calls.append(a) or {"text": "- x"}
        r = self.client().post("/api/v1/assistant/reading/%d/summary" % iid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Settings", r.json()["detail"])
        self.assertEqual(calls, [])

    def test_summary_with_a_key_is_kept(self):
        store.update_desk(self.aid, model_provider="openai", model_key_ct=keychain.seal("sk-test-1234"),
                          model_key_last4="1234")
        iid = self.save().json()["id"]
        seen = {}

        def fake(provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
            seen.update(provider=provider, key=key, text=messages[-1]["content"])
            return {"text": "1. Core inflation is about 2%.\n\n* The data were out on Friday.", "usage": {}}
        gateway.complete = fake
        r = self.client().post("/api/v1/assistant/reading/%d/summary" % iid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["summary"], "- Core inflation is about 2%.\n- The data were out on Friday.")
        self.assertEqual(seen["key"], "sk-test-1234")
        self.assertIn("about 2%", seen["text"])
        listed = self.client().get("/api/v1/assistant/reading").json()["reading"][0]
        self.assertTrue(listed["summary"].startswith("- Core"))
        self.assertEqual(self.client("tok-b").post("/api/v1/assistant/reading/%d/summary" % iid).status_code, 404)

    def test_the_extension_download_points_at_this_server(self):
        import io
        import zipfile
        r = self.client(token=None).get("/api/v1/assistant/reading/extension.zip")
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        for n in ("manifest.json", "background.js", "extract.js", "config.js"):
            self.assertIn("plover-extension/" + n, names)
        self.assertIn('"http://testserver"', z.read("plover-extension/config.js").decode())
        self.assertIn("function ploverExtract", z.read("plover-extension/extract.js").decode())
        manifest = json.loads(z.read("plover-extension/manifest.json"))
        self.assertEqual(manifest["permissions"], ["activeTab", "scripting"])

    def test_a_model_failure_is_worded_for_the_reader(self):
        store.update_desk(self.aid, model_provider="openai", model_key_ct=keychain.seal("sk-test-1234"),
                          model_key_last4="1234")
        iid = self.save().json()["id"]

        def boom(*a, **k):
            raise gateway.GatewayError("The provider refused the key.")
        gateway.complete = boom
        r = self.client().post("/api/v1/assistant/reading/%d/summary" % iid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Try again", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
