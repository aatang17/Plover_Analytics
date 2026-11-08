# -*- coding: utf-8 -*-
"""Labour force by age and sex (labour-force-age-jp): the gates that keep a
broken release from being published.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_lfs_age

A consistent synthetic release passes; each kind of damage the Bureau's
files could carry (a component that no longer adds up, a rate that does not
match its counts, a missing or gapped series) is refused.
"""
import datetime
import unittest

from app.adapters import lfs_age

D = datetime.date
START, END = D(1990, 7, 1), D(2026, 9, 1)

# Per ten-year group and sex, in 万人: (population, employed, unemployed).
BASE = {
    "15-24": {"male": (600, 280, 15), "female": (580, 290, 12)},
    "25-34": {"male": (650, 600, 20), "female": (640, 520, 18)},
    "35-44": {"male": (700, 670, 15), "female": (690, 540, 14)},
    "45-54": {"male": (900, 860, 18), "female": (890, 720, 15)},
    "55-64": {"male": (800, 700, 20), "female": (820, 560, 12)},
    "65plus": {"male": (1600, 560, 15), "female": (2000, 400, 8)},
}
WORKING = ["15-24", "25-34", "35-44", "45-54", "55-64"]


def _months():
    p = START
    while p <= END:
        yield p
        p = D(p.year + (p.month == 12), p.month % 12 + 1, 1)


def _counts():
    """{(age, sex): (pop, emp, un, nilf)} with every total built from parts,
    and 1 unknown-status person per group so population > lf + nilf."""
    out = {}
    for age, by in BASE.items():
        for sex, (pop, emp, un) in by.items():
            out[(age, sex)] = (pop, emp, un, pop - emp - un - 1)
    for age in list(BASE):
        out[(age, "total")] = tuple(a + b for a, b in zip(out[(age, "male")], out[(age, "female")]))
    for sex in ("male", "female", "total"):
        out[("total", sex)] = tuple(sum(out[(a, sex)][i] for a in BASE) for i in range(4))
        out[("15-64", sex)] = tuple(sum(out[(a, sex)][i] for a in WORKING) for i in range(4))
        out[("20-69", sex)] = out[("15-64", sex)]
    return out


def release():
    counts = _counts()
    series, observations = [], []
    for table, _sid, key, _en, _ja, unit, _k in lfs_age.TABLES:
        for (age, sex), (pop, emp, un, nilf) in counts.items():
            lf = emp + un
            value = {"population_15plus": pop, "labour_force": lf, "employed": emp,
                     "unemployed": un, "not_in_labour_force": nilf,
                     "unemployment_rate": round(un / lf * 100, 1),
                     "participation_rate": round(lf / pop * 100, 1),
                     "employment_rate": round(emp / pop * 100, 1)}[key]
            code = lfs_age._code(key, age, sex)
            series.append({"code": code, "unit": unit})
            for p in _months():
                observations.append({"code": code, "period": p, "value": float(value)})
    _employment(series, observations)
    return series, observations


# Table b-3 per ten-year group and sex, in 万人: regular, part-time, arbeit,
# dispatched, contract, entrusted, other, executives.
B3 = {"male": (300, 20, 30, 8, 25, 6, 5, 3), "female": (150, 90, 25, 10, 20, 4, 4, 1)}


def _employment(series, observations):
    rows = {}
    for age in BASE:
        for sex, parts in B3.items():
            rows[(age, sex)] = parts
    for age in list(BASE):
        rows[(age, "total")] = tuple(a + b for a, b in zip(rows[(age, "male")], rows[(age, "female")]))
    for sex in ("male", "female", "total"):
        rows[("total", sex)] = tuple(sum(rows[(a, sex)][i] for a in BASE) for i in range(8))
    for (age, sex), (reg, pt, ab, dis, con, ent, oth, execs) in rows.items():
        pta = pt + ab
        non = pta + dis + con + ent + oth
        excl = reg + non
        share = round(reg / excl * 100, 1)
        values = {"employees": excl + execs, "employees_excl_executives": excl, "regular": reg,
                  "non_regular": non, "part_time_and_arbeit": pta, "part_time": pt,
                  "arbeit": ab, "dispatched": dis, "contract": con, "entrusted": ent,
                  "other": oth, "regular_share": share,
                  "non_regular_share": round(100 - share, 1)}
        for key, value in values.items():
            code = lfs_age._code(key, age, sex)
            series.append({"code": code})
            for p in _months():
                if p >= lfs_age.EMPLOYMENT_FROM:
                    observations.append({"code": code, "period": p, "value": float(value)})


def damaged(change):
    series, observations = release()
    return series, [dict(o, value=change(o)) for o in observations]


class Gates(unittest.TestCase):
    def setUp(self):
        self.start = lfs_age.START
        lfs_age.START = START   # a shorter synthetic history

    def tearDown(self):
        lfs_age.START = self.start

    def test_a_consistent_release_passes(self):
        series, observations = release()
        got = lfs_age.validate(series, observations)
        self.assertEqual(got["series"], 216 + 273)
        self.assertEqual(got["latest_period"], END.isoformat())

    def test_labour_force_must_be_employed_plus_unemployed(self):
        bad = lambda o: o["value"] + 20 if (o["code"] == "labour_force.25-34.female"
                                           and o["period"] == D(2026, 1, 1)) else o["value"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(*damaged(bad))

    def test_age_groups_must_add_to_the_total(self):
        bad = lambda o: o["value"] + 30 if (o["code"] == "employed.total.male"
                                           and o["period"] == D(2000, 6, 1)) else o["value"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(*damaged(bad))

    def test_a_rate_must_match_its_counts(self):
        bad = lambda o: o["value"] + 2.0 if (o["code"] == "participation_rate.65plus.total"
                                            and o["period"] == D(2026, 9, 1)) else o["value"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(*damaged(bad))

    def test_a_missing_series_is_refused(self):
        series, observations = release()
        observations = [o for o in observations if o["code"] != "employment_rate.20-69.male"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(series, observations)

    def test_a_gap_is_refused_except_in_small_rates(self):
        series, observations = release()
        hole = D(2010, 3, 1)
        gapped = [o for o in observations
                  if not (o["code"] == "employed.45-54.female" and o["period"] == hole)]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(series, gapped)
        # The Bureau leaves small unemployment rates blank; that stays a gap.
        rate_gap = [o for o in observations
                    if not (o["code"] == "unemployment_rate.65plus.female" and o["period"] == hole)]
        lfs_age.validate(series, rate_gap)


    def test_employment_type_must_add_up(self):
        bad = lambda o: o["value"] + 25 if (o["code"] == "regular.35-44.female"
                                           and o["period"] == D(2024, 2, 1)) else o["value"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(*damaged(bad))

    def test_regular_and_non_regular_shares_make_100(self):
        bad = lambda o: o["value"] + 1.5 if (o["code"] == "non_regular_share.total.male"
                                            and o["period"] == D(2026, 9, 1)) else o["value"]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(*damaged(bad))

    def test_small_employment_groups_may_go_blank(self):
        series, observations = release()
        hole = D(2020, 5, 1)
        blank = [o for o in observations
                 if not (o["code"] == "entrusted.15-24.male" and o["period"] == hole)]
        lfs_age.validate(series, blank)
        regular = [o for o in observations
                   if not (o["code"] == "regular.15-24.male" and o["period"] == hole)]
        with self.assertRaises(lfs_age.ValidationError):
            lfs_age.validate(series, regular)


class Columns(unittest.TestCase):
    def test_sex_blocks_cross_column_z(self):
        self.assertEqual(lfs_age._col("E", 9), "N")
        self.assertEqual(lfs_age._col("W", 8), "AE")
        self.assertEqual(lfs_age._col("M", 18), "AE")
