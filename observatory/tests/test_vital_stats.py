# -*- coding: utf-8 -*-
"""Monthly vital statistics (vital-stats-jp) and the two sum measures it
brought: year-to-date and 12-month totals of monthly counts.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_vital_stats
The endpoint tests skip without data/observatory.duckdb.
"""
import datetime
import pathlib
import unittest

from app import api
from app.adapters import mhlw_vital_stats as vs

D = datetime.date
DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "observatory.duckdb"


class SumMeasures(unittest.TestCase):
    def test_ytd_sums_january_to_the_month(self):
        vals = dict((D(2026, m, 1), 10.0 * m) for m in range(1, 7))
        vals[D(2025, 12, 1)] = 999.0
        pts = dict(api._measure_points(vals, "ytd"))
        self.assertEqual(pts[D(2026, 1, 1)], 10.0)
        self.assertEqual(pts[D(2026, 6, 1)], 210.0)
        # December stands on its own year: 2025's January–November are absent.
        self.assertIsNone(pts[D(2025, 12, 1)])

    def test_a_missing_month_leaves_the_total_out(self):
        vals = dict((D(2026, m, 1), 1.0) for m in (1, 2, 4, 5))
        pts = dict(api._measure_points(vals, "ytd"))
        self.assertEqual(pts[D(2026, 2, 1)], 2.0)
        self.assertIsNone(pts[D(2026, 4, 1)])     # March unpublished: never summed short
        self.assertIsNone(pts[D(2026, 5, 1)])

    def test_sum_12m_needs_twelve_months(self):
        vals = dict((api._months_ago(D(2026, 8, 1), k), 2.0) for k in range(13))
        pts = dict(api._measure_points(vals, "sum_12m"))
        self.assertEqual(pts[D(2026, 8, 1)], 24.0)
        self.assertEqual(pts[D(2026, 7, 1)], 24.0)
        self.assertIsNone(pts[D(2025, 8, 1)])

    def test_sums_carry_the_series_unit_and_a_formula(self):
        self.assertEqual(api._unit_for("ytd", "persons"), "persons")
        self.assertEqual(api._unit_for("sum_12m", "couples"), "couples")
        self.assertEqual(api.TRUST["ytd"], "derived")
        self.assertIn("never summed short", api._calc_for("ytd", "persons"))


class Parsing(unittest.TestCase):
    def test_era_years(self):
        self.assertEqual(vs._era_year("平成19年"), 2007)
        self.assertEqual(vs._era_year("令和元年"), 2019)
        self.assertEqual(vs._era_year("（2026）"), 2026)
        self.assertEqual(vs._era_year("令和８年"), 2026)
        self.assertIsNone(vs._era_year("当月"))

    def _csv(self, title, label_col, rows):
        head = ["", "出生数", "死亡数", "死亡数", "死亡数", "自然増減数", "死産数", "死産数",
                "周産期死亡数", "婚姻件数", "離婚件数", "出生率"]
        if label_col:
            head = [""] + head
        lines = [title, "第２表人口動態総覧，月別", ",".join(head)]
        for row in rows:
            if len(row) == 1:                 # an unpublished month: every cell blank
                cells = [row[0]] + [""] * (len(head) - 1 - (1 if label_col else 0))
                lines.append(",".join(([""] + cells) if label_col else cells))
                continue
            label, b, d, m, v = row
            cells = [label, str(b), str(d), "1", "1", str(b - d), "1", "1", "1", str(m), str(v), "5.1"]
            lines.append(",".join(([""] + cells) if label_col else cells))
        return "\n".join(lines).encode("cp932")

    def test_csv_edition_with_previous_and_current_year(self):
        raw = self._csv("人口動態統計月報（概数）令和８（2026）年６月分", False, [
            ("前年１月", 51816, 172287, 32401, 14045),
            ("当年１月", 51874, 145818, 32192, 13678),
            ("当年７月",),
        ])
        # The blank July row is unpublished — absent, never zero.
        out = vs.parse_gaisu(raw, 202606, "gaisu/202606.csv")
        self.assertEqual(out[("births", D(2025, 1, 1))], 51816)
        self.assertEqual(out[("births", D(2026, 1, 1))], 51874)
        self.assertEqual(out[("natural_change", D(2026, 1, 1))], 51874 - 145818)
        self.assertEqual(out[("divorces", D(2026, 1, 1))], 13678)
        self.assertNotIn(("births", D(2026, 7, 1)), out)

    def test_december_edition_labels_carry_the_year(self):
        raw = self._csv("人口動態統計月報（概数）令和７（2025）年12月分", True, [
            ("2024年1月", 54624, 151988, 39572, 14602),
            ("2025年12月", 61098, 147609, 45418, 16106),
        ])
        out = vs.parse_gaisu(raw, 202512, "gaisu/202512.csv")
        self.assertEqual(out[("births", D(2024, 1, 1))], 54624)
        self.assertEqual(out[("marriages", D(2025, 12, 1))], 45418)

    def test_a_wrong_edition_is_refused(self):
        raw = self._csv("人口動態統計月報（概数）令和８（2026）年５月分", False, [
            ("当年１月", 51874, 145818, 32192, 13678)])
        with self.assertRaises(vs.ValidationError):
            vs.parse_gaisu(raw, 202606, "gaisu/202606.csv")

    def test_units_inside_headings_are_ignored(self):
        self.assertEqual(vs._heading("出　生　数\n（人）"), "出生数")
        self.assertEqual(vs._heading("自　　然\n増 減 数\n（人）"), "自然増減数")


def _series_and_obs(break_identity=False, december_low=False, other_low=False):
    """Two complete releases ending in a recent month."""
    end = D(datetime.date.today().year, datetime.date.today().month, 1)
    end = api._months_ago(end, 3)
    obs = []
    for release, _en, _ja, first in vs.RELEASES:
        last = end if release == "sokuhou" else api._months_ago(end, 2)
        p = first
        while p <= last:
            base = 60000 if release == "sokuhou" else 57000
            b, d = base, base * 2
            if release == "sokuhou" and p.month == 12 and december_low:
                b = 56000
            if release == "sokuhou" and p == D(2015, 5, 1) and other_low:
                b = 56000
            n = b - d + (1 if break_identity and p == D(2010, 3, 1) else 0)
            for key, v in (("births", b), ("deaths", d), ("natural_change", n),
                           ("marriages", 40000), ("divorces", 15000)):
                obs.append({"code": vs._code(release, key), "period": p, "value": float(v)})
            p = D(p.year + p.month // 12, p.month % 12 + 1, 1)
    series = [{"code": vs._code(r[0], m[0])} for r in vs.RELEASES for m in vs.MEASURES]
    return series, obs


class Gates(unittest.TestCase):
    def test_complete_releases_pass(self):
        summary = vs.validate(*_series_and_obs())
        self.assertEqual(summary["series"], 10)
        self.assertGreater(summary["identities_checked"], 400)

    def test_natural_change_must_be_births_less_deaths(self):
        with self.assertRaises(vs.ValidationError):
            vs.validate(*_series_and_obs(break_identity=True))

    def test_december_may_be_below_but_no_other_month(self):
        vs.validate(*_series_and_obs(december_low=True))
        with self.assertRaises(vs.ValidationError):
            vs.validate(*_series_and_obs(other_low=True))

    def test_a_gap_fails(self):
        series, obs = _series_and_obs()
        obs = [o for o in obs if not (o["code"] == "gaisu.deaths"
                                      and o["period"] == D(2012, 7, 1))]
        with self.assertRaises(vs.ValidationError):
            vs.validate(series, obs)


@unittest.skipUnless(DATA.exists(), "no macro database")
class Endpoints(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    def test_sums_are_refused_where_months_do_not_add_up(self):
        r = self.client.get("/api/v1/cpi-jp/observations?series=0001&measure=ytd")
        self.assertEqual(r.status_code, 400)
        self.assertIn("measure=index", r.json()["detail"])

    def test_more_than_eight_series_says_which_were_left_out(self):
        listing = self.client.get("/api/v1/cpi-jp/series").json()["series"]
        codes = [s["code"] for s in listing][:9]
        if len(codes) < 9:
            self.skipTest("fewer than nine cpi-jp series here")
        url = "/api/v1/cpi-jp/observations?series=%s&start=2026-01" % ",".join(codes)
        js = self.client.get(url).json()
        self.assertEqual(len(js["series"]), 8)
        self.assertEqual(js["series_not_returned"], codes[8:])
        csv = self.client.get(url + "&format=csv").text
        self.assertIn("# Note: At most 8 series per request: %s" % codes[8], csv)

    def test_half_year_is_one_query(self):
        if "vital-stats-jp" not in [d["slug"] for d in
                                    self.client.get("/api/v1/catalog/datasets").json()["datasets"]]:
            self.skipTest("vital-stats-jp not ingested here")
        js = self.client.get("/api/v1/vital-stats-jp/observations?series=sokuhou.births"
                             "&measure=ytd&start=2025-06&end=2026-06").json()
        pts = dict(js["series"][0]["points"])
        self.assertEqual(pts["2026-06-01"], 342068.0)
        self.assertEqual(pts["2025-06-01"], 339280.0)
        self.assertEqual(js["trust"], "derived")


if __name__ == "__main__":
    unittest.main()
