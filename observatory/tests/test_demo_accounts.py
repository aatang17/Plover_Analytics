# -*- coding: utf-8 -*-
"""Demo accounts (app/demo_accounts.py): a tester's Your Plover account with
more AI questions a day for 30 days, made in the Admin Console.

What is proved: making one needs a Team sign-in, gives a working sign-in link
and an audit line; a second demo for the same address is refused while one
runs; while it runs the account gets DEMO_TRIES questions a day and the
site-wide cap does not stop it; after 30 days, or when ended early, the
account is back to the normal allowance; a running demo is let past an invite
list and a finished one is not.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_demo_accounts
"""
import os
import pathlib
import tempfile
import unittest
import urllib.parse
from unittest import mock

os.environ["ACCOUNTS_ENABLED"] = "1"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import accounts, admin_api, demo_accounts, staff  # noqa: E402
from app.assistant import free, store  # noqa: E402

ENV = ("FREE_TRIES", "FREE_TRIES_PER_DAY", "DEMO_TRIES", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
       "ANTHROPIC_API_KEY", "FREE_TRIES_PROVIDER", "ACCOUNTS_ALLOWED_EMAILS", "ADMIN_PASSWORD")


class DemoAccounts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved_env = dict((k, os.environ.get(k)) for k in ENV)

        def restore():
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k in ENV:
            os.environ.pop(k, None)
        os.environ.update(FREE_TRIES="2", FREE_TRIES_PER_DAY="3", DEMO_TRIES="5",
                          DEEPSEEK_API_KEY="sk-house-key")

        path = os.path.join(tmp.name, "workspace.db")
        saved = (accounts.DB_PATH, staff.DB_PATH, admin_api.ADMIN_DIR, admin_api.AUDIT_PATH)
        self.addCleanup(lambda: (setattr(accounts, "DB_PATH", saved[0]), setattr(accounts, "_conn", None),
                                 setattr(staff, "DB_PATH", saved[1]), setattr(admin_api, "ADMIN_DIR", saved[2]),
                                 setattr(admin_api, "AUDIT_PATH", saved[3])))
        accounts.DB_PATH = pathlib.Path(path)
        accounts._conn = None
        demo_accounts._ready[0] = None
        store.reset_for_tests(path)
        staff.DB_PATH = pathlib.Path(tmp.name) / "staff.db"
        admin_api.ADMIN_DIR = pathlib.Path(tmp.name)
        admin_api.AUDIT_PATH = admin_api.ADMIN_DIR / "audit.jsonl"
        admin_api._LOGIN_HITS.clear()

        app = FastAPI()
        app.include_router(admin_api.router)
        app.include_router(accounts.router)
        self.c = TestClient(app)

    def sign_in(self):
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.post("/admin/api/login", json={"password": "shared-secret"}).status_code, 200)

    def account(self, email):
        db = accounts.conn()
        db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, accounts._now()))
        db.commit()
        return db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]

    def take_all(self, aid):
        n = 0
        while True:
            try:
                free.take(aid)
                n += 1
            except free.NoTries as exc:
                return n, str(exc)

    def test_making_one_needs_a_team_sign_in_and_gives_a_working_link(self):
        self.assertEqual(self.c.get("/admin/api/demo-accounts").status_code, 503)  # admin off
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.post("/admin/api/demo-accounts", json={"email": "t@fund.com"}).status_code, 401)
        self.sign_in()
        r = self.c.post("/admin/api/demo-accounts", json={"email": " T@Fund.com ", "note": "PM, Tokyo"})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual((out["demo"]["email"], out["demo"]["status"], out["demo"]["days_left"]),
                         ("t@fund.com", "active", 30))
        self.assertFalse(out["emailed"])                      # no mailbox in tests: link handed back
        self.assertIn("/signin.html?token=", out["signin_url"])
        self.assertIn("demo_created", admin_api.AUDIT_PATH.read_text())
        # the link signs the tester in
        token = out["signin_url"].split("token=")[1].split("&")[0]
        v = self.c.post("/api/v1/account/session", json={"token": urllib.parse.unquote(token)})
        self.assertEqual(v.status_code, 200, v.text)
        listed = self.c.get("/admin/api/demo-accounts").json()
        self.assertEqual([d["email"] for d in listed["demos"]], ["t@fund.com"])
        self.assertIsNotNone(listed["demos"][0]["last_seen"])
        # a second one while it runs is refused, with what to do instead
        again = self.c.post("/admin/api/demo-accounts", json={"email": "t@fund.com"})
        self.assertEqual(again.status_code, 400)
        self.assertIn("New Sign-in Link", again.json()["detail"])
        self.assertEqual(self.c.post("/admin/api/demo-accounts", json={"email": "nope"}).status_code, 400)

    def test_a_demo_gets_more_questions_and_the_site_cap_does_not_stop_it(self):
        demo_aid, plain_aid = self.account("demo@fund.com"), self.account("plain@x.com")
        demo_accounts.create("demo@fund.com", "", "test")
        n, _ = self.take_all(demo_aid)
        self.assertEqual(n, 5)                                # DEMO_TRIES, past the site cap of 3
        s = free.status(demo_aid)
        self.assertEqual((s["limit"], s["left"], s["demo"]["days_left"]), (5, 0, 30))
        n, msg = self.take_all(plain_aid)
        self.assertEqual(n, 0)                                # the day's 3 were used by the demo
        self.assertIn("all been used", msg)
        self.assertIsNone(free.status(plain_aid)["demo"])

    def test_after_the_month_or_ended_early_it_is_a_normal_account(self):
        aid = self.account("late@fund.com")
        demo = demo_accounts.create("late@fund.com", "", "test")
        later = accounts._now() + demo_accounts.DEMO_DAYS * 86400 + 1
        with mock.patch.object(demo_accounts, "_now", return_value=later):
            self.assertIsNone(demo_accounts.demo_info(aid))
            self.assertEqual(free.status(aid)["limit"], 2)
            self.assertEqual(demo_accounts.list_all()[0]["status"], "expired")
            demo_accounts.create("late@fund.com", "", "test")  # a new month may then be given
        aid2 = self.account("early@fund.com")
        d2 = demo_accounts.create("early@fund.com", "", "test")
        demo_accounts.end(d2["id"], "test")
        self.assertEqual(free.status(aid2)["limit"], 2)
        with self.assertRaises(demo_accounts.DemoError):
            demo_accounts.end(d2["id"], "test")
        self.assertEqual(demo["status"], "active")

    def test_a_running_demo_is_let_past_an_invite_list(self):
        os.environ["ACCOUNTS_ALLOWED_EMAILS"] = "boss@plover.com"
        self.assertFalse(accounts._allowed("guest@fund.com"))
        d = demo_accounts.create("guest@fund.com", "", "test")
        self.assertTrue(accounts._allowed("guest@fund.com"))
        demo_accounts.end(d["id"], "test")
        self.assertFalse(accounts._allowed("guest@fund.com"))


if __name__ == "__main__":
    unittest.main()
