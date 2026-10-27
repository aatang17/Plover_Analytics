# -*- coding: utf-8 -*-
"""The admin console's Companies tab, Japan: a Japanese company's logo and name.

The store (app/jp_companies.py) in a temporary folder; the equity database is
two fake companies in memory; no network.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_jp_companies
"""
import os
import pathlib
import tempfile
import unittest

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_api, equity_api, jp_companies, jp_company_admin_api, staff

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = jp_companies.STORE_PATH
        self.addCleanup(lambda: (setattr(jp_companies, "STORE_PATH", saved), setattr(jp_companies, "_CACHE", None)))
        jp_companies.STORE_PATH = pathlib.Path(tmp.name) / "jp_companies.json"
        jp_companies._CACHE = None
        self.tmp = tmp.name


class Store(Base):
    def test_codes_are_read_the_way_people_and_edinet_write_them(self):
        self.assertEqual([jp_companies.code_of(x) for x in ("7203", "72030", "285a", "12", "ABCD", None)],
                         ["7203", "7203", "285A", None, None, None])

    def test_the_name_shown_is_an_edit_else_the_filed_name_made_readable(self):
        self.assertEqual(jp_companies.shown_name("7203", "TOYOTA MOTOR CORPORATION"), "Toyota Motor Corporation")
        jp_companies.set_name("7203", "  Toyota  ", "test")
        self.assertEqual(jp_companies.shown_name("7203", "TOYOTA MOTOR CORPORATION"), "Toyota")
        jp_companies.set_name("7203", "", "test")
        self.assertEqual(jp_companies.shown_name("7203", "TOYOTA MOTOR CORPORATION"), "Toyota Motor Corporation")
        self.assertEqual([h["changed"] for h in jp_companies.entry("7203")["history"]], [["name"], ["name"]])
        self.assertEqual(jp_companies.edited(), [])

    def test_a_logo_is_served_by_its_hash_and_can_be_removed(self):
        self.assertIsNone(jp_companies.logo("6758"))
        jp_companies.set_logo("6758", PNG, "test")
        url = jp_companies.logo("6758")
        self.assertTrue(url.startswith("/logos/jp/6758-") and url.endswith(".png"))
        self.assertEqual(jp_companies.logo_file(url.rsplit("/", 1)[1]), (PNG, "image/png"))
        self.assertIsNone(jp_companies.logo_file("6758-0000000000000000.png"))
        self.assertEqual(jp_companies.edited(), ["6758"])
        jp_companies.reset_logo("6758", "test")
        self.assertIsNone(jp_companies.logo("6758"))

    def test_bad_input_is_refused_with_what_to_do(self):
        with self.assertRaises(jp_companies.EditError):
            jp_companies.set_logo("6758", b"<svg/>", "test")
        with self.assertRaises(jp_companies.EditError):
            jp_companies.set_name("6758", "x" * 200, "test")
        with self.assertRaises(jp_companies.EditError):
            jp_companies.set_name("12", "x", "test")


class Api(Base):
    def setUp(self):
        super().setUp()
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE eq_filings (sec_code VARCHAR, filer_name VARCHAR, filer_name_en VARCHAR, period_end DATE)")
        con.execute("INSERT INTO eq_filings VALUES ('72030', 'トヨタ自動車株式会社', 'TOYOTA MOTOR CORPORATION', '2026-03-31'), "
                    "('67580', 'ソニーグループ株式会社', 'SONY GROUP CORPORATION', '2026-03-31')")
        con.execute("CREATE TABLE eq_company_year (sec_code VARCHAR, employees_consolidated INTEGER, period_end DATE)")
        con.execute("INSERT INTO eq_company_year VALUES ('72030', 380000, '2026-03-31'), ('67580', 110000, '2026-03-31')")
        fake = [{"sec_code": "7203", "name": "トヨタ自動車（株）", "name_en": "TOYOTA MOTOR CORPORATION"},
                {"sec_code": "6758", "name": "ソニーグループ（株）", "name_en": "SONY GROUP CORPORATION"}]
        saved = (equity_api._cur, equity_api.companies)
        self.addCleanup(lambda: (setattr(equity_api, "_cur", saved[0]), setattr(equity_api, "companies", saved[1])))
        equity_api._cur = lambda: con.cursor()
        equity_api.companies = lambda q="": {"companies": [c for c in fake if q.lower() in
                                                            (c["sec_code"] + c["name_en"]).lower() or q in c["name"]]}
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
        for r in (admin_api.router, jp_company_admin_api.router, jp_company_admin_api.logo_router):
            app.include_router(r)
        self.c = TestClient(app)

    def sign_in(self):
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.post("/admin/api/login", json={"password": "shared-secret"}).status_code, 200)

    def test_admin_edits_need_a_sign_in(self):
        self.assertEqual(self.c.get("/admin/api/jp-companies").status_code, 503)   # admin off
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.assertEqual(self.c.get("/admin/api/jp-companies").status_code, 401)
        self.assertEqual(self.c.put("/admin/api/jp-companies/7203", json={"name": "x"}).status_code, 401)

    def test_admin_changes_reach_the_store_the_logo_address_and_the_audit_log(self):
        self.sign_in()
        work = self.c.get("/admin/api/jp-companies").json()
        self.assertEqual([x["code"] for x in work["missing_logo"]], ["7203", "6758"])   # largest first
        self.assertEqual(work["missing_logo"][0]["name"], "Toyota Motor Corporation")
        r = self.c.put("/admin/api/jp-companies/7203", json={"name": "Toyota"})
        self.assertEqual((r.status_code, r.json()["company"]["name"]), (200, "Toyota"))
        r = self.c.post("/admin/api/jp-companies/7203/logo", content=PNG)
        self.assertEqual(r.json()["logo_state"], "uploaded")
        got = self.c.get(r.json()["company"]["logo"])
        self.assertEqual((got.status_code, got.content, got.headers["content-type"]), (200, PNG, "image/png"))
        self.assertEqual(self.c.post("/admin/api/jp-companies/7203/logo", content=b"GIF").status_code, 400)
        work = self.c.get("/admin/api/jp-companies").json()
        self.assertEqual([x["code"] for x in work["missing_logo"]], ["6758"])
        self.assertEqual([x["code"] for x in work["edited"]], ["7203"])
        self.assertEqual([x["code"] for x in self.c.get("/admin/api/jp-companies?q=sony").json()["companies"]], ["6758"])
        self.assertEqual(self.c.post("/admin/api/jp-companies/7203/logo/reset").json()["logo_state"], "none")
        log = (pathlib.Path(self.tmp) / "audit.jsonl").read_text()
        for action in ("jp_company_changed", "jp_company_logo_uploaded", "jp_company_logo_reset"):
            self.assertIn(action, log)
        self.assertEqual(self.c.get("/admin/api/jp-companies/9999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
