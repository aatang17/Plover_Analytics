# -*- coding: utf-8 -*-
"""Per-person admin logins (app/staff.py, app/admin_api.py).

What is proved: with no shared password and no accounts the console is off;
the shared password still signs in and can create the first account; a setup
link works once and lets the person choose a password; permissions gate every
endpoint; nobody can lock the team out of itself; a disabled person's session
dies; a person's session survives a restart while the shared password's does
not; changing a password signs out other devices; the audit trail names who
did what.
"""
import json
import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_api, staff


def fresh_store(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    staff.DB_PATH = staff.pathlib.Path(tmp.name) / "staff.db"
    admin_api.ADMIN_DIR = staff.pathlib.Path(tmp.name)
    admin_api.AUDIT_PATH = admin_api.ADMIN_DIR / "audit.jsonl"
    admin_api._LOGIN_HITS.clear()
    return tmp.name


def app_client():
    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app)


def setup_token(url):
    return url.split("#setup=", 1)[1]


GOOD = "correct horse battery"


class StaffTest(unittest.TestCase):
    def setUp(self):
        fresh_store(self)
        self._pw = os.environ.pop("ADMIN_PASSWORD", None)
        self.addCleanup(self._restore)
        # speed: the stored hash format carries its own iteration count
        self._iters = staff.PBKDF2_ITERATIONS
        staff.PBKDF2_ITERATIONS = 1000
        staff._DUMMY_HASH = None

    def _restore(self):
        staff.PBKDF2_ITERATIONS = self._iters
        staff._DUMMY_HASH = None
        if self._pw is not None:
            os.environ["ADMIN_PASSWORD"] = self._pw
        else:
            os.environ.pop("ADMIN_PASSWORD", None)

    def shared_client(self):
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        c = app_client()
        r = c.post("/admin/api/login", json={"password": "shared-secret"})
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def add_person(self, client, email, perms, name="Person"):
        r = client.post("/admin/api/team", json={"email": email, "name": name, "permissions": perms})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def signed_in_person(self, admin_client, email, perms):
        made = self.add_person(admin_client, email, perms)
        c = app_client()
        r = c.post("/admin/api/setup", json={"token": setup_token(made["setup_url"]), "password": GOOD})
        self.assertEqual(r.status_code, 200, r.text)
        return c, made["person"]

    def test_setup_link_is_emailed_when_a_mailbox_is_configured(self):
        from app import mailer
        sent = []
        old = (mailer.send, os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM"))

        def restore():
            mailer.send = old[0]
            for key, val in (("RESEND_API_KEY", old[1]), ("RESEND_FROM", old[2])):
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
        self.addCleanup(restore)
        mailer.send = lambda to, subject, text, html=None, timeout=10, reply_to=None: (
            sent.append((to, subject, text, reply_to)) or "sent")
        os.environ["RESEND_API_KEY"] = "test"
        os.environ["RESEND_FROM"] = "Plover <noreply@example.com>"
        c = self.shared_client()
        self.assertTrue(c.get("/admin/api/team").json()["email_delivery"])
        made = self.add_person(c, "new@example.com", ["writing"], "New Person")
        self.assertEqual((made["emailed"], made["setup_url"]), (True, None))
        to, subject, text, reply_to = sent[-1]
        self.assertEqual(to, "new@example.com")
        self.assertIsNone(reply_to)                                   # the shared password is nobody
        self.assertIn("/admin.html and use this email address", text)
        url = [w for w in text.split() if "#setup=" in w][0]
        r = app_client().post("/admin/api/setup", json={"token": setup_token(url), "password": GOOD})
        self.assertEqual(r.status_code, 200, r.text)

    def test_off_without_password_or_accounts(self):
        c = app_client()
        self.assertEqual(c.get("/admin/api/session").json(), {"enabled": False, "authenticated": False})
        self.assertEqual(c.get("/admin/api/overview").status_code, 503)

    def test_shared_password_bootstraps_first_account(self):
        c = self.shared_client()
        s = c.get("/admin/api/session").json()
        self.assertTrue(s["user"]["shared"])
        self.assertEqual(set(s["user"]["permissions"]), set(staff.PERMISSION_KEYS))
        made = self.add_person(c, "Owner@Example.com", ["team", "writing"], "Owner")
        self.assertEqual(made["person"]["email"], "owner@example.com")
        self.assertIn("/admin.html#setup=", made["setup_url"])
        tok = setup_token(made["setup_url"])
        anon = app_client()
        self.assertEqual(anon.post("/admin/api/setup/check", json={"token": tok}).json()["name"], "Owner")
        short = anon.post("/admin/api/setup", json={"token": tok, "password": "short"})
        self.assertEqual(short.status_code, 400)
        ok = anon.post("/admin/api/setup", json={"token": tok, "password": GOOD})
        self.assertEqual(ok.status_code, 200, ok.text)
        me = anon.get("/admin/api/session").json()["user"]
        self.assertEqual(me["email"], "owner@example.com")
        self.assertFalse(me["shared"])
        # the link is spent
        again = app_client().post("/admin/api/setup", json={"token": tok, "password": GOOD + "x"})
        self.assertEqual(again.status_code, 400)
        # and the console stays on without the shared password
        os.environ.pop("ADMIN_PASSWORD")
        self.assertTrue(app_client().get("/admin/api/session").json()["enabled"])

    def test_permissions_gate_endpoints(self):
        admin = self.shared_client()
        writer, _ = self.signed_in_person(admin, "writer@example.com", ["writing"])
        self.assertEqual(writer.get("/admin/api/overview").status_code, 403)
        self.assertEqual(writer.get("/admin/api/team").status_code, 403)
        self.assertEqual(writer.get("/admin/api/parties").status_code, 403)
        self.assertIn("Operations", writer.get("/admin/api/audit").json()["detail"])

    def test_email_login_and_lockout(self):
        admin = self.shared_client()
        self.signed_in_person(admin, "a@example.com", ["writing"])
        c = app_client()
        self.assertEqual(c.post("/admin/api/login", json={"email": "a@example.com", "password": "nope-nope-nope"}).status_code, 401)
        self.assertEqual(c.post("/admin/api/login", json={"email": "nobody@example.com", "password": GOOD}).status_code, 401)
        r = c.post("/admin/api/login", json={"email": "A@example.com", "password": GOOD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["email"], "a@example.com")
        admin_api._LOGIN_HITS.clear()
        # successes do not count towards the limit
        for _ in range(8):
            self.assertEqual(c.post("/admin/api/login", json={"email": "a@example.com", "password": GOOD}).status_code, 200)
        for _ in range(5):
            c.post("/admin/api/login", json={"email": "a@example.com", "password": "wrong-wrong-wrong"})
        self.assertEqual(c.post("/admin/api/login", json={"email": "a@example.com", "password": GOOD}).status_code, 429)

    def test_team_cannot_lock_itself_out(self):
        admin = self.shared_client()
        owner, person = self.signed_in_person(admin, "owner@example.com", ["team"])
        r = owner.put("/admin/api/team/%d" % person["id"], json={"permissions": []})
        self.assertEqual(r.status_code, 400)
        r = owner.put("/admin/api/team/%d" % person["id"], json={"status": "disabled"})
        self.assertEqual(r.status_code, 400)
        # the shared password cannot remove the last team member either
        r = admin.put("/admin/api/team/%d" % person["id"], json={"permissions": ["writing"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("At least one", r.json()["detail"])

    def test_disabled_person_is_signed_out(self):
        admin = self.shared_client()
        self.signed_in_person(admin, "owner@example.com", ["team"])
        writer, person = self.signed_in_person(admin, "w@example.com", ["writing"])
        self.assertTrue(writer.get("/admin/api/session").json()["authenticated"])
        r = admin.put("/admin/api/team/%d" % person["id"], json={"status": "disabled"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(writer.get("/admin/api/session").json()["authenticated"])
        self.assertEqual(app_client().post("/admin/api/login", json={"email": "w@example.com", "password": GOOD}).status_code, 401)

    def test_new_setup_link_replaces_old(self):
        admin = self.shared_client()
        made = self.add_person(admin, "x@example.com", ["writing"])
        old = setup_token(made["setup_url"])
        new = setup_token(admin.post("/admin/api/team/%d/setup-link" % made["person"]["id"]).json()["setup_url"])
        self.assertEqual(app_client().post("/admin/api/setup/check", json={"token": old}).status_code, 410)
        self.assertEqual(app_client().post("/admin/api/setup/check", json={"token": new}).status_code, 200)

    def test_person_session_survives_restart_shared_does_not(self):
        admin = self.shared_client()
        writer, _ = self.signed_in_person(admin, "w@example.com", ["writing"])
        admin_api._SECRET = os.urandom(32)   # what a restart does
        self.assertTrue(writer.get("/admin/api/session").json()["authenticated"])
        self.assertFalse(admin.get("/admin/api/session").json()["authenticated"])

    def test_password_change_signs_out_other_devices(self):
        admin = self.shared_client()
        first, _ = self.signed_in_person(admin, "w@example.com", ["writing"])
        second = app_client()
        self.assertEqual(second.post("/admin/api/login", json={"email": "w@example.com", "password": GOOD}).status_code, 200)
        bad = first.post("/admin/api/me/password", json={"current": "wrong-wrong-wrong", "new": GOOD + "2"})
        self.assertEqual(bad.status_code, 400)
        ok = first.post("/admin/api/me/password", json={"current": GOOD, "new": GOOD + "2"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(first.get("/admin/api/session").json()["authenticated"])
        self.assertFalse(second.get("/admin/api/session").json()["authenticated"])

    def test_audit_names_the_person(self):
        admin = self.shared_client()
        self.signed_in_person(admin, "w@example.com", ["writing", "operations"])
        c = app_client()
        c.post("/admin/api/login", json={"email": "w@example.com", "password": GOOD})
        entries = c.get("/admin/api/audit").json()["entries"]
        bys = [e.get("by") for e in entries]
        self.assertIn("w@example.com", bys)
        self.assertIn("shared password", bys)
        added = [e for e in entries if e["action"] == "staff_added"]
        self.assertEqual(added[0]["by"], "shared password")

    def test_passwords_are_hashed(self):
        admin = self.shared_client()
        self.signed_in_person(admin, "w@example.com", ["writing"])
        raw = open(str(staff.DB_PATH), "rb").read()
        self.assertNotIn(GOOD.encode(), raw)
        row = staff.conn().execute("SELECT password_hash FROM staff").fetchone()[0]
        self.assertTrue(row.startswith("pbkdf2_sha256$"))
        self.assertEqual(json.loads(staff.conn().execute("SELECT permissions FROM staff").fetchone()[0]), ["writing"])


if __name__ == "__main__":
    unittest.main()
