# -*- coding: utf-8 -*-
"""The S&P's sectors (app/us_sectors.py): the loader's sector funds carry the
same names the app reads, a request's sector words are read (and an industry
inside a sector is not), the screener keeps to a sector, and the screen
reader turns "technology companies…" into a sector tag, never dropping it.
The endpoint checks run on this machine's data when the sector funds are
loaded there, and are skipped otherwise. No network."""
import importlib.util
import os
import pathlib
import unittest

os.environ["US_ASK_MODEL"] = "0"           # word patterns only: no key, no network

from app import us_build_api, us_sectors  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("us_index_members", str(ROOT / "equity" / "us_index_members.py"))
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)


class Names(unittest.TestCase):
    def test_the_loader_and_the_app_name_the_same_eleven(self):
        self.assertEqual(len(us_sectors.SECTORS), 11)
        for key, name, fund in us_sectors.SECTORS:
            self.assertEqual(loader.FUNDS["sector_" + key], (fund.lower(), us_sectors.index_name(key), loader.FUNDS["sector_" + key][2]))

    def test_sector_words(self):
        r = us_sectors.read
        self.assertEqual(r("technology companies with revenue growth above 20%"), ["tech"])
        self.assertEqual(r("healthcare stocks"), ["health"])
        self.assertEqual(r("energy stocks with a dividend yield above 4%"), ["energy"])
        self.assertEqual(r("financials and utilities paying dividends"), ["financials", "utilities"])
        self.assertEqual(r("REITs"), ["real_estate"])
        self.assertEqual(r("banks"), [])                       # an industry: the SIC reading
        self.assertEqual(r("semiconductor companies"), [])
        self.assertEqual(r("companies with strong financial results"), [])


class ScreenReader(unittest.TestCase):
    def chips(self, q):
        spec = us_build_api._with_sectors(us_build_api._screen_by_words(None, q), q)
        return us_build_api._finish_screen(spec)

    def test_a_sector_is_a_tag_not_dropped(self):
        texts, params = [c["text"] for c in self.chips("technology companies with revenue growth above 20%")[0]], \
            self.chips("technology companies with revenue growth above 20%")[1]
        self.assertEqual(params.get("sector"), "tech")
        self.assertNotIn("sic", params)                       # the sector, not SIC technology codes
        self.assertIn("Sector: Information Technology (S&P 500 companies)", texts)


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import sec_api
        from app.main import app
        try:
            members, _ = us_sectors.members(sec_api._cur())
        except Exception:  # noqa: BLE001
            members = {}
        if not members:
            raise unittest.SkipTest("the sector funds' holdings are not loaded on this machine")
        cls.client = TestClient(app)

    def test_screener_keeps_to_the_sector(self):
        d = self.client.get("/api/v1/us/financials/screener?sector=communication&sort=market_cap&limit=20").json()
        self.assertGreater(d["matched"], 5)
        self.assertTrue(all(r["sector"] == "Communication Services" for r in d["rows"]))
        self.assertIn("GOOGL", [r["ticker"] for r in d["rows"]])     # the S&P's list: Alphabet is not Technology
        self.assertIn("sector", d["calc"])

    def test_bad_sector(self):
        self.assertEqual(self.client.get("/api/v1/us/financials/screener?sector=crypto").status_code, 400)


if __name__ == "__main__":
    unittest.main()
