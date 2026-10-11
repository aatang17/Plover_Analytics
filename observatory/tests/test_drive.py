# -*- coding: utf-8 -*-
"""The desk's Drive: My drive (uploads, folders, Trash) and the read-only
Google Drive connection, with Google replaced by a canned HTTP function.

What is proved: uploads keep their bytes and never collide on a name; Trash
hides a folder's contents and restore brings them back; delete-forever works
only from Trash; one desk cannot see another's files; the Google sign-in
state is signed and tied to the account; the refresh token is stored sealed;
Google Docs are read as exported text; specialists get the drive tools only
when there is something to read.
"""
import json
import os
import tempfile
import unittest
import urllib.parse

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import accounts  # noqa: E402
from app.assistant import api as aapi  # noqa: E402
from app.assistant import drive, keychain, store  # noqa: E402


class FakeGoogle(object):
    """Answers the handful of Google URLs drive.py calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers=None, data=None, timeout=30):
        self.calls.append((method, url))
        if url.startswith(drive.TOKEN_URL):
            form = urllib.parse.parse_qs((data or b"").decode())
            if form.get("grant_type") == ["authorization_code"]:
                idt = "x." + __import__("base64").urlsafe_b64encode(
                    json.dumps({"email": "pm@gmail.example"}).encode()).decode().rstrip("=") + ".y"
                return 200, json.dumps({"access_token": "AT1", "expires_in": 3600, "refresh_token": "RT-secret",
                                        "scope": drive.SCOPES, "id_token": idt}).encode(), "application/json"
            return 200, json.dumps({"access_token": "AT2", "expires_in": 3600}).encode(), "application/json"
        if url.startswith(drive.REVOKE_URL):
            return 200, b"", ""
        if url.startswith(drive.DRIVE_URL + "/doc1/export"):
            return 200, "Q3 thesis: buybacks.".encode(), "text/plain"
        if url.startswith(drive.DRIVE_URL + "/doc1"):
            return 200, json.dumps({"id": "doc1", "name": "Thesis", "mimeType": "application/vnd.google-apps.document",
                                    "webViewLink": "https://docs.google.com/document/d/doc1"}).encode(), "application/json"
        if url.startswith(drive.DRIVE_URL + "?"):
            return 200, json.dumps({"files": [
                {"id": "doc1", "name": "Thesis", "mimeType": "application/vnd.google-apps.document",
                 "modifiedTime": "2026-09-20T01:02:03Z", "webViewLink": "https://docs.google.com/document/d/doc1",
                 "owners": [{"displayName": "Me", "me": True}]},
                {"id": "fold1", "name": "Models", "mimeType": drive.FOLDER_MIME, "modifiedTime": "2026-09-01T00:00:00Z"}]}).encode(), "application/json"
        return 404, b"{}", ""


class DriveTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # put the shared sign-in and desk stores back afterwards: later test
        # files in the same process must not inherit a deleted temp file
        cls._saved = (accounts.DB_PATH, store.DB_PATH)
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        cls.app = app
        db = accounts.conn()
        now = accounts._now()
        ids = []
        for email, tok in (("pm@example.com", "tok"), ("other@example.com", "tok2")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (accounts._hash(tok), aid, now, now + 3600))
            ids.append(aid)
        db.commit()
        cls.account_id, cls.other_id = ids
        cls._http = drive._http

    @classmethod
    def tearDownClass(cls):
        drive._http = cls._http
        accounts.DB_PATH = cls._saved[0]
        accounts._conn = None
        store.reset_for_tests(cls._saved[1])
        cls.tmp.cleanup()

    def setUp(self):
        db = drive._db()
        db.execute("DELETE FROM ia_drive_items")
        db.execute("DELETE FROM ia_google")
        db.commit()
        drive._access.clear()
        for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
            os.environ.pop(k, None)

    def client(self, tok="tok"):
        c = TestClient(self.app)
        c.cookies.set(accounts.COOKIE, tok)
        return c

    def google_on(self):
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "cid.apps.googleusercontent.com"
        os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "csecret"
        fake = FakeGoogle()
        drive._http = fake
        return fake

    # ---------------------------------------------------------- My drive

    def test_upload_folder_download_and_names(self):
        c = self.client()
        self.assertEqual(TestClient(self.app).get("/api/v1/assistant/drive").status_code, 401)
        f = c.post("/api/v1/assistant/drive/folders", json={"name": "Research"}).json()
        up = c.post("/api/v1/assistant/drive/upload?name=notes.md&parent_id=%d" % f["id"],
                    content="# Toyota\nbuyback 84%".encode(), headers={"Content-Type": "text/markdown"}).json()
        again = c.post("/api/v1/assistant/drive/upload?name=notes.md&parent_id=%d" % f["id"],
                       content=b"second", headers={"Content-Type": "text/markdown"}).json()
        self.assertEqual(again["name"], "notes (2).md")
        d = c.get("/api/v1/assistant/drive?folder=%d" % f["id"]).json()
        self.assertEqual([i["name"] for i in d["items"]], ["notes (2).md", "notes.md"])
        self.assertEqual(d["path"], [{"id": f["id"], "name": "Research"}])
        got = c.get("/api/v1/assistant/drive/items/%d/download" % up["id"])
        self.assertEqual(got.content, "# Toyota\nbuyback 84%".encode())
        self.assertIn("attachment", got.headers["content-disposition"])
        self.assertEqual(c.get("/api/v1/assistant/drive").json()["usage"]["files"], 2)
        # another desk sees none of it
        o = self.client("tok2")
        self.assertEqual(o.get("/api/v1/assistant/drive").json()["items"], [])
        self.assertEqual(o.get("/api/v1/assistant/drive/items/%d/download" % up["id"]).status_code, 404)
        self.assertEqual(c.post("/api/v1/assistant/drive/upload?name=empty.txt", content=b"").status_code, 400)

    def test_trash_restore_and_delete_forever(self):
        c = self.client()
        f = c.post("/api/v1/assistant/drive/folders", json={"name": "Old"}).json()
        up = c.post("/api/v1/assistant/drive/upload?name=a.csv&parent_id=%d" % f["id"], content=b"x,y").json()
        self.assertEqual(c.delete("/api/v1/assistant/drive/items/%d" % f["id"]).status_code, 400)  # not in Trash yet
        c.post("/api/v1/assistant/drive/items/%d/trash" % f["id"])
        self.assertEqual(c.get("/api/v1/assistant/drive").json()["items"], [])
        self.assertEqual(c.get("/api/v1/assistant/drive?view=recent").json()["items"], [])
        self.assertEqual([i["name"] for i in c.get("/api/v1/assistant/drive?view=trash").json()["items"]], ["Old"])
        c.post("/api/v1/assistant/drive/items/%d/restore" % f["id"])
        self.assertEqual([i["name"] for i in c.get("/api/v1/assistant/drive?view=recent").json()["items"]], ["a.csv"])
        c.post("/api/v1/assistant/drive/items/%d/trash" % f["id"])
        self.assertEqual(c.delete("/api/v1/assistant/drive/items/%d" % f["id"]).status_code, 200)
        self.assertIsNone(drive.content(self.account_id, up["id"]))  # its contents went with it

    # ------------------------------------------------------------ Google

    def test_google_not_configured_says_so(self):
        c = self.client()
        s = c.get("/api/v1/assistant/google/status").json()
        self.assertFalse(s["configured"])
        self.assertTrue(s["redirect_uri"].endswith("/api/v1/assistant/google/callback"))
        r = c.get("/api/v1/assistant/google/connect", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("google_error=", r.headers["location"])

    def test_google_connect_list_read_disconnect(self):
        fake = self.google_on()
        c = self.client()
        r = c.get("/api/v1/assistant/google/connect", follow_redirects=False)
        loc = r.headers["location"]
        self.assertTrue(loc.startswith(drive.AUTH_URL))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        self.assertIn("drive.readonly", q["scope"][0])
        self.assertNotIn("auth/drive ", q["scope"][0] + " ")  # never the full read-write scope
        state = q["state"][0]
        # someone else's browser cannot finish this sign-in
        bad = self.client("tok2").get("/api/v1/assistant/google/callback?code=c&state=" + state, follow_redirects=False)
        self.assertIn("google_error=", bad.headers["location"])
        ok = c.get("/api/v1/assistant/google/callback?code=c&state=" + state, follow_redirects=False)
        self.assertIn("google=connected", ok.headers["location"])
        s = c.get("/api/v1/assistant/google/status").json()
        self.assertTrue(s["connected"])
        self.assertEqual(s["email"], "pm@gmail.example")
        ct = drive._row("SELECT ct FROM ia_google WHERE account_id = ?", (self.account_id,))["ct"]
        self.assertNotIn("RT-secret", ct)
        self.assertEqual(keychain.open_(ct), "RT-secret")
        files = c.get("/api/v1/assistant/google/files").json()["items"]
        self.assertEqual([(f["name"], f["kind"]) for f in files], [("Thesis", "file"), ("Models", "folder")])
        self.assertIsNone(files[0]["size"])  # a Google Doc has no size: shown as —, not 0
        # a specialist reads the Doc as exported text
        text, err = drive.run_tool(self.account_id, "drive_read", {"ref": "google:doc1"})
        self.assertFalse(err)
        self.assertEqual(json.loads(text)["content"], "Q3 thesis: buybacks.")
        self.assertTrue(all(m == "GET" for m, u in fake.calls if u.startswith(drive.DRIVE_URL)))
        c.delete("/api/v1/assistant/google")
        self.assertFalse(c.get("/api/v1/assistant/google/status").json()["connected"])
        self.assertTrue(any(u.startswith(drive.REVOKE_URL) for _, u in fake.calls))

    def test_specialist_tools_offered_only_with_content(self):
        self.assertFalse(drive.available(self.account_id))
        drive.upload(self.account_id, "memo.txt", "Mitsubishi UFJ: rate risk note".encode())
        drive.upload(self.account_id, "chart.png", b"\x89PNG....", "image/png")
        self.assertTrue(drive.available(self.account_id))
        hits = json.loads(drive.run_tool(self.account_id, "drive_search", {"query": "memo"})[0])["files"]
        self.assertEqual([h["name"] for h in hits], ["memo.txt"])
        text, err = drive.run_tool(self.account_id, "drive_read", {"ref": hits[0]["ref"]})
        self.assertFalse(err)
        self.assertIn("rate risk", json.loads(text)["content"])
        png = [i for i in drive.recent(self.account_id) if i["name"] == "chart.png"][0]
        self.assertTrue(drive.run_tool(self.account_id, "drive_read", {"ref": png["ref"]})[1])
        # another desk's ref does not open
        self.assertTrue(drive.run_tool(self.other_id, "drive_read", {"ref": hits[0]["ref"]})[1])


if __name__ == "__main__":
    unittest.main()
