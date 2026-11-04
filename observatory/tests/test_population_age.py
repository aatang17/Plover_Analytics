# -*- coding: utf-8 -*-
"""Population by single year of age (population-age-jp) and the age-band
sums the API calculates from it.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_population_age
The endpoint tests skip where the dataset is not ingested.
"""
import datetime
import pathlib
import unittest

from app import api
from app.adapters import estat_population_age as pa

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "observatory.duckdb"


class BandSums(unittest.TestCase):
    def setUp(self):
        self.modern = dict((str(a), 10.0) for a in range(100))
        self.modern["100plus"] = 3.0
        self.old = dict((str(a), 10.0) for a in range(90))
        self.old["90plus"] = 50.0

    def test_a_closed_band(self):
        self.assertEqual(api._band_sum(self.modern, 25, 39), 150.0)
        self.assertEqual(api._band_sum(self.old, 25, 39), 150.0)

    def test_and_over_takes_the_open_band(self):
        self.assertEqual(api._band_sum(self.modern, 90, None), 103.0)
        self.assertEqual(api._band_sum(self.old, 90, None), 50.0)
        self.assertEqual(api._band_sum(self.old, 85, None), 100.0)

    def test_the_open_band_is_never_split(self):
        self.assertIsNone(api._band_sum(self.old, 90, 94))
        self.assertIsNone(api._band_sum(self.old, 95, None))
        self.assertIsNone(api._band_sum(self.modern, 95, 100))

    def test_a_missing_age_leaves_the_band_out(self):
        del self.modern["30"]
        self.assertIsNone(api._band_sum(self.modern, 25, 39))


class AgeLabels(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(pa._age("  0 歳"), "0")
        self.assertEqual(pa._age(" 85"), "85")
        self.assertEqual(pa._age(" 100 歳以上"), "100plus")
        self.assertEqual(pa._age("90 歳以上"), "90plus")
        self.assertEqual(pa._age("総     数"), "all")
        self.assertIsNone(pa._age("Age"))


def _year(nat, year, top="100plus", total_off=0.0):
    out = {}
    for sex, each in (("m", 400.0), ("f", 400.0), ("t", 800.0)):
        singles = 100 if top == "100plus" else 90
        for a in range(singles):
            out[(nat, sex, str(a), year)] = each
        out[(nat, sex, top, year)] = each
        out[(nat, sex, "all", year)] = each * (singles + 1) + (total_off if sex == "t" else 0)
    return out


def _obs(values):
    return [{"code": pa.code(n, s, a), "period": datetime.date(y, 10, 1), "value": v}
            for (n, s, a, y), v in values.items()]


class Gates(unittest.TestCase):
    def _panel(self, **kw):
        vals = {}
        last = datetime.date.today().year - 2
        for y in range(1980, last + 1):
            vals.update(_year("total", y, "90plus" if y < 2005 else "100plus"))
        for y in range(2000, last + 1):
            vals.update(_year("jp", y, "90plus" if y < 2005 else "100plus"))
        for key, v in kw.items():
            vals[key] = v
        return vals

    def test_a_consistent_panel_passes(self):
        summary = pa.validate([], _obs(self._panel()))
        self.assertEqual(summary["years_japanese"][:4], "2000")
        self.assertEqual(summary["japanese_women_25_39_latest"], 6000.0)

    def test_a_total_that_does_not_reconcile_fails(self):
        vals = self._panel()
        vals.update(_year("total", 2010, total_off=40.0))
        with self.assertRaises(pa.ValidationError):
            pa.validate([], _obs(vals))

    def test_japanese_above_total_fails(self):
        vals = self._panel()
        vals[("jp", "m", "30", 2010)] = 500.0
        vals[("jp", "f", "30", 2010)] = 300.0
        vals[("jp", "m", "31", 2010)] = 300.0
        vals[("jp", "f", "31", 2010)] = 500.0
        with self.assertRaises(pa.ValidationError):
            pa.validate([], _obs(vals))


@unittest.skipUnless(DATA.exists(), "no macro database")
class Endpoint(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)
        slugs = [d["slug"] for d in self.client.get("/api/v1/catalog/datasets").json()["datasets"]]
        if "population-age-jp" not in slugs:
            self.skipTest("population-age-jp not ingested here")

    def test_women_25_39(self):
        js = self.client.get("/api/v1/population-age-jp/age-bands"
                             "?nationality=jp&sex=f&from=25&to=39").json()
        pts = dict(js["series"][0]["points"])
        self.assertEqual(pts["2024-10-01"], 9002.0)
        self.assertLess(pts["2024-10-01"], pts["2023-10-01"])
        self.assertEqual(js["trust"], "derived")

    def test_bad_input_says_what_to_do(self):
        r = self.client.get("/api/v1/population-age-jp/age-bands?nationality=jp&sex=f&bands=39-25")
        self.assertEqual(r.status_code, 400)
        self.assertIn("25-39", r.json()["detail"])
        r = self.client.get("/api/v1/cpi-jp/age-bands?nationality=jp&sex=f")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
