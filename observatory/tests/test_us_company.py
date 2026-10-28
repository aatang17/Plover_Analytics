# -*- coding: utf-8 -*-
"""The US company record and the admin console's US Companies tab.

A US company is its SEC number (CIK): /api/v1/us/company/{t} says who it is
and what the site holds for it, and the admin tab edits the logo, name and
website every US page shows (app/us_companies.py). Fake companies in memory;
the store and the audit log in a temporary folder; no network.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_company
"""
import os
import pathlib
import tempfile
import unittest

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_api, sec_api, staff, us_companies, us_company_admin_api, us_company_api, us_link

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CIK, OTHER = 9900001, 9900002


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = (us_companies.STORE_PATH, us_companies._CACHE, us_companies._CACHE_VERSION)
        self.addCleanup(lambda: (setattr(us_companies, "STORE_PATH", saved[0]),
                                 setattr(us_companies, "_CACHE", None)))
        us_companies.STORE_PATH = pathlib.Path(tmp.name) / "us_companies.json"
        us_companies._CACHE = None
        self.tmp = tmp.name


class Store(Base):
    def test_a_name_and_website_are_kept_with_their_history(self):
        us_companies.set_name(CIK, "  Acme   Widgets ", "a@example.com")
        us_companies.set_website(CIK, "https://www.Acme.com/about", "a@example.com")
        self.assertEqual(us_companies.name(CIK), "Acme Widgets")
        self.assertEqual(us_companies.website(CIK), "acme.com")
        hist = us_companies.entry(CIK)["history"]
        self.assertEqual([h["changed"] for h in hist], [["name"], ["website"]])
        us_companies.set_name(CIK, "", "b@example.com")              # blank goes back to the filed name
        self.assertIsNone(us_companies.name(CIK))
        self.assertEqual(len(us_companies.entry(CIK)["history"]), 3)

    def test_bad_input_is_refused_with_what_to_do(self):
        with self.assertRaises(us_companies.EditError):
            us_companies.set_website(CIK, "not a website", "x")
        with self.assertRaises(us_companies.EditError):
            us_companies.set_logo(CIK, b"<svg onload='alert(1)'></svg>", "x")
        with self.assertRaises(us_companies.EditError):
            us_companies.set_logo(CIK, PNG + b"\x00" * us_companies.MAX_LOGO_BYTES, "x")
        self.assertEqual(us_companies.entry(CIK), {})

    def test_logo_states_win_over_the_built_in_one(self):
        self.assertIsNone(us_companies.logo_url(CIK))                # no edit: built-in (if any)
        us_companies.set_logo(CIK, PNG, "x")
        url = us_companies.logo_url(CIK)
        self.assertTrue(url.startswith("/logos/us/%d-" % CIK) and url.endswith(".png"))
        self.assertEqual(us_companies.logo_file(url.rsplit("/", 1)[1]), (PNG, "image/png"))
        self.assertEqual(us_link.logo(CIK), url)
        us_companies.hide_logo(CIK, "x")
        self.assertEqual(us_companies.logo_url(CIK), "")
        self.assertIsNone(us_link.logo(CIK))
        self.assertIsNone(us_companies.logo_file(url.rsplit("/", 1)[1]))  # the old file is gone
        us_companies.reset_logo(CIK, "x")
        self.assertIsNone(us_companies.logo_url(CIK))
        self.assertEqual(us_companies.edited(), [])                  # nothing in force, history kept
        self.assertEqual(len(us_companies.entry(CIK)["history"]), 3)

    def test_an_edited_name_is_the_one_every_page_shows(self):
        self.assertEqual(us_link.company_name(CIK, "ACME WIDGETS INC"), "Acme Widgets Inc")
        us_companies.set_name(CIK, "Acme", "x")
        self.assertEqual(us_link.company_name(CIK, "ACME WIDGETS INC"), "Acme")


class Api(Base):
    def setUp(self):
        super().setUp()
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE sec_companies (cik BIGINT, name VARCHAR, ticker VARCHAR, tickers VARCHAR, "
                    "exchange VARCHAR, sic VARCHAR, industry VARCHAR)")
        con.execute("INSERT INTO sec_companies VALUES (?, 'ACME WIDGETS INC', 'ACMW', 'ACMW', 'NYSE', '3571', "
                    "'Electronic computers'), (?, 'Other Co', 'OTHC', 'OTHC', 'Nasdaq', NULL, NULL)", [CIK, OTHER])
        con.execute("CREATE TABLE sec_filings (cik BIGINT, filed DATE, status VARCHAR)")
        con.execute("INSERT INTO sec_filings VALUES (?, '2026-02-01', 'clean'), (?, '2025-02-01', 'clean')", [CIK, CIK])
        saved = (sec_api._cur, us_link._version)
        self.addCleanup(lambda: (setattr(sec_api, "_cur", saved[0]), setattr(us_link, "_version", saved[1]),
                                 us_link._MEMO.clear()))
        sec_api._cur = lambda: con.cursor()
        us_link._version = lambda: "test"
        us_link._MEMO.clear()
        # admin: a shared-password session, the audit log in the temporary folder
        paths = (staff.DB_PATH, admin_api.ADMIN_DIR, admin_api.AUDIT_PATH)
        self.addCleanup(lambda: (setattr(staff, "DB_PATH", paths[0]), setattr(admin_api, "ADMIN_DIR", paths[1]),
                                 setattr(admin_api, "AUDIT_PATH", paths[2])))
        staff.DB_PATH = pathlib.Path(self.tmp) / "staff.db"
        admin_api.ADMIN_DIR = pathlib.Path(self.tmp)
        admin_api.AUDIT_PATH = admin_api.ADMIN_DIR / "audit.jsonl"
        admin_api._LOGIN_HITS.clear()
        pw = os.environ.pop("ADMIN_PASSWORD", None)
        self.addCleanup(lambda: os.environ.__setitem__("ADMIN_PASSWORD", pw) if pw is not None
                        else os.environ.pop("ADMIN_PASSWORD", None))
        app = FastAPI()
        for r in (admin_api.router, us_company_admin_api.router, us_company_api.router, us_company_api.logo_router):
            app.include_router(r)
        self.c = TestClient(app)

    def sign_in(self):
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.post("/admin/api/login", json={"password": "shared-secret"}).status_code, 200)

    def test_the_record_names_the_company_and_says_what_is_held(self):
        d = self.c.get("/api/v1/us/company/ACMW").json()
        self.assertEqual(d["company"]["cik"], CIK)
        self.assertEqual(d["company"]["name"], "Acme Widgets Inc")
        fin = next(i for i in d["items"] if i["key"] == "financials")
        self.assertEqual((fin["count"], fin["latest"], fin["page"]), (2, "2026-02-01", "us-stock.html?t=ACMW"))
        pay = next(i for i in d["items"] if i["key"] == "pay")
        self.assertFalse(pay["held"])                       # not an S&P 500 member: nothing, never zero shown
        self.assertEqual(self.c.get("/api/v1/us/company/%d" % CIK).json()["company"]["ticker"], "ACMW")
        self.assertEqual(self.c.get("/api/v1/us/company/NOPE").status_code, 404)

    def test_search_finds_by_ticker_and_by_name(self):
        self.assertEqual([x["ticker"] for x in self.c.get("/api/v1/us/company?q=acmw").json()["companies"]], ["ACMW"])
        self.assertEqual([x["ticker"] for x in self.c.get("/api/v1/us/company?q=widgets").json()["companies"]], ["ACMW"])
        self.assertEqual(self.c.get("/api/v1/us/company?q=zzzz").json()["companies"], [])

    def test_admin_edits_need_a_sign_in(self):
        self.assertEqual(self.c.get("/admin/api/us-companies").status_code, 503)   # admin off
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.get("/admin/api/us-companies").status_code, 401)
        self.assertEqual(self.c.put("/admin/api/us-companies/ACMW", json={"name": "x"}).status_code, 401)

    def test_admin_changes_reach_the_record_and_the_audit_log(self):
        self.sign_in()
        r = self.c.put("/admin/api/us-companies/ACMW", json={"name": "Acme", "website": "acme.com"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["edit"]["name"], "Acme")
        r = self.c.post("/admin/api/us-companies/ACMW/logo", content=PNG)
        self.assertEqual(r.json()["logo_state"], "uploaded")
        rec = self.c.get("/api/v1/us/company/ACMW").json()["company"]
        self.assertEqual((rec["name"], rec["website"]), ("Acme", "acme.com"))
        got = self.c.get(rec["logo"])
        self.assertEqual((got.status_code, got.content, got.headers["content-type"]), (200, PNG, "image/png"))
        self.assertEqual(self.c.post("/admin/api/us-companies/ACMW/logo", content=b"GIF").status_code, 400)
        self.assertEqual(self.c.delete("/admin/api/us-companies/ACMW/logo").json()["logo_state"], "hidden")
        self.assertIsNone(self.c.get("/api/v1/us/company/ACMW").json()["company"]["logo"])
        work = self.c.get("/admin/api/us-companies").json()
        self.assertEqual([x["cik"] for x in work["edited"]], [CIK])
        self.assertEqual([x["ticker"] for x in self.c.get("/admin/api/us-companies?q=acme").json()["companies"]], ["ACMW"])
        log = (pathlib.Path(self.tmp) / "audit.jsonl").read_text()
        for action in ("us_company_changed", "us_company_logo_uploaded", "us_company_logo_hidden"):
            self.assertIn(action, log)
        self.assertEqual(self.c.get("/admin/api/us-companies/NOPE").status_code, 404)


if __name__ == "__main__":
    unittest.main()
