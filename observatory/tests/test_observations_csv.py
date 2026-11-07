# -*- coding: utf-8 -*-
"""The observations endpoint as CSV: same numbers as the JSON, under a
metadata block that states source, release, formula and vintage.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_observations_csv
Skips without data/observatory.duckdb.
"""
import pathlib
import unittest

from fastapi.testclient import TestClient

from app.main import app

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "observatory.duckdb"
URL = "/api/v1/cpi-jp/observations?series=0001,0161&measure=yoy&start=2024-01"


@unittest.skipUnless(DATA.exists(), "no macro database")
class ObservationsCsv(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_csv_matches_json(self):
        js = self.client.get(URL).json()
        r = self.client.get(URL + "&format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertIn('filename="cpi-jp-yoy.csv"', r.headers["content-disposition"])
        meta = [l for l in r.text.splitlines() if l.startswith("#")]
        rows = [l.split(",") for l in r.text.splitlines() if not l.startswith("#")]
        self.assertEqual(rows[0], ["period", "0001", "0161"])
        # Trust contract in file form: formula, release, source, vintage note.
        text = "\n".join(meta)
        self.assertIn(js["calc"], text)
        self.assertIn(js["release"]["sha256"], text)
        self.assertIn("Source: Statistics Bureau of Japan", text)
        self.assertIn("as_of=", text)
        want = {p[:7]: ("" if v is None else repr(v)) for p, v in js["series"][0]["points"]}
        self.assertEqual({r[0]: r[1] for r in rows[1:]}, want)

    def test_as_of_is_stated(self):
        r = self.client.get("/api/v1/cpi-jp/observations?series=0001&as_of=2026-08-20&format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("# Point in time: the data as it stood on 2026-08-20", r.text)
        self.assertIn("asof-2026-08-20.csv", r.headers["content-disposition"])

    def test_notes_off_is_stata_ready(self):
        # Stata's import delimited reads the first line as names and cannot
        # skip comments: notes=0 drops the block and makes the names legal,
        # while the numbers stay exactly those of the annotated file.
        full = self.client.get(URL + "&format=csv").text
        r = self.client.get(URL + "&format=csv&notes=0")
        self.assertEqual(r.status_code, 200)
        lines = r.text.splitlines()
        self.assertFalse(any(l.startswith("#") for l in lines))
        self.assertEqual(lines[0], "period,s0001,s0161")
        self.assertEqual(lines[1:], [l for l in full.splitlines()
                                     if not l.startswith("#")][1:])
        r = self.client.get("/api/v1/job-openings-jp/observations"
                            "?series=sa.active.incl_part&format=csv&notes=0")
        if r.status_code == 200:
            self.assertEqual(r.text.splitlines()[0], "period,sa_active_incl_part")
        self.assertEqual(self.client.get(URL + "&format=csv&notes=2").status_code, 422)

    def test_stata_names(self):
        from app.api import _stata_names
        self.assertEqual(_stata_names(["0001", "a.b", "a-b", "_x", "y" * 40]),
                         ["s0001", "a_b", "a_b_2", "_x", "y" * 32])

    def test_bad_format(self):
        r = self.client.get("/api/v1/cpi-jp/observations?series=0001&format=xlsx")
        self.assertEqual(r.status_code, 400)

    def test_daily_bounds(self):
        # A full date is accepted as a start, and a month as an end covers the
        # whole month — a daily series is not cut at the 1st.
        r = self.client.get("/api/v1/jgb-yields/observations?series=10Y"
                            "&start=2026-08-10&end=2026-08")
        self.assertEqual(r.status_code, 200)
        days = [p[0] for p in r.json()["series"][0]["points"]]
        self.assertTrue(days)
        self.assertGreaterEqual(days[0], "2026-08-10")
        self.assertTrue(all(d[:7] == "2026-08" for d in days))
        self.assertGreater(days[-1], "2026-08-20")

    def test_monthly_accepts_full_date(self):
        r = self.client.get("/api/v1/cpi-jp/observations?series=0001"
                            "&start=2026-01-01&end=2026-03")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p[0] for p in r.json()["series"][0]["points"]],
                         ["2026-01-01", "2026-02-01", "2026-03-01"])

    def test_bad_date_is_400(self):
        for q in ("start=2026-13", "end=2026-09-31", "start=last-year"):
            r = self.client.get("/api/v1/cpi-jp/observations?series=0001&" + q)
            self.assertEqual(r.status_code, 400, q)
        r = self.client.get("/api/v1/cpi-jp/contributions?start=2026-13")
        self.assertEqual(r.status_code, 400)
