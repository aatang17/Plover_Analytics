# -*- coding: utf-8 -*-
"""Insider Buying (/api/v1/us/insiders/buying, app/sec_insiders_api.py): names
and roles read as filed, and on this machine's data, when it is here, only
exchange-listed companies, largest total first, with the totals adding up."""
import unittest

from app import sec_insiders_api as ins


class Words(unittest.TestCase):
    def test_person_out_of_capitals(self):
        self.assertEqual(ins._person("HUANG JEN HSUN"), "Huang Jen Hsun")
        self.assertEqual(ins._person("667, L.P."), "667, L.P.")
        self.assertEqual(ins._person("de Oliveira Andre"), "de Oliveira Andre")

    def test_role(self):
        self.assertEqual(ins._role("Chief Executive Officer", False, True, False), "Chief Executive Officer")
        self.assertEqual(ins._role(None, True, False, True), "Director")
        self.assertEqual(ins._role(None, False, False, True), "10% owner")
        self.assertEqual(ins._role(None, False, True, False), "Officer")


class Endpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        from app import sec_api
        if not sec_api.DB_PATH.exists():
            raise unittest.SkipTest("data/sec.duckdb not built")
        cls.client = TestClient(app)
        r = cls.client.get("/api/v1/us/insiders/buying?days=400&limit=50")
        if r.status_code == 503:
            raise unittest.SkipTest("data/sec.duckdb without insider, 13F and universe tables")
        cls.d = r.json()

    def test_largest_first_and_listed(self):
        rows = self.d["companies"]
        self.assertEqual([r["value"] for r in rows], sorted([r["value"] for r in rows], reverse=True))
        for r in rows:
            self.assertTrue(r["ticker"] and r["ticker"] != "NONE", r)
            self.assertGreaterEqual(r["insiders"], 1)
        self.assertLessEqual(sum(r["value"] for r in rows), self.d["totals"]["value"] + 1)

    def test_bosses_never_more_than_everyone(self):
        allw = self.client.get("/api/v1/us/insiders/buying?days=400&who=all&limit=1").json()["totals"]
        self.assertLessEqual(self.d["totals"]["value"], allw["value"] + 1)
        self.assertLessEqual(self.d["totals"]["companies"], allw["companies"])

    def test_bad_who(self):
        self.assertEqual(self.client.get("/api/v1/us/insiders/buying?who=funds").status_code, 400)


if __name__ == "__main__":
    unittest.main()
