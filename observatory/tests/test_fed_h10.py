# -*- coding: utf-8 -*-
"""US dollar exchange rates from the Federal Reserve Board's H.10 (app/adapters/fed_h10.py).

The package is built here in the Board's SDMX shape, so no test calls the Fed.
"""
import datetime
import io
import unittest
import zipfile

from app.adapters import fed_h10


def _series(name, obs):
    rows = "".join('<frb:Obs OBS_STATUS="%s" OBS_VALUE="%s" TIME_PERIOD="%s" />' % (st, v, d)
                   for d, v, st in obs)
    return '<kf:Series SERIES_NAME="%s" UNIT="Currency">%s</kf:Series>' % (name, rows)


def package(series, prepared="2026-11-02T16:52:42"):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<message:MessageGroup xmlns:message="m" xmlns:frb="f" xmlns:kf="k">'
           '<message:Header><message:Prepared>%s</message:Prepared></message:Header>'
           '<frb:DataSet>%s</frb:DataSet></message:MessageGroup>') % (
        prepared, "".join(_series(n, o) for n, o in series))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("H10_data.xml", xml)
    return buf.getvalue()


SMALL = [
    ("RXI_N.B.JA", [("2026-10-29", "157.8300", "A"), ("2026-10-30", "157.4700", "A"),
                    ("2026-11-26", "-9999", "ND")]),
    ("RXI_N.B.CA", [("2026-10-30", "1.3957", "A")]),             # not loaded
    ("JRXWTFB_N.B", [("2026-10-30", "121.6656", "A")]),
]


class Parse(unittest.TestCase):
    def test_loads_the_four_rates_and_nothing_else(self):
        series, obs = fed_h10.parse(package(SMALL))
        self.assertEqual([s["code"] for s in series], ["USDJPY", "USDHKD", "EURUSD", "BROAD"])
        self.assertEqual(sorted((o["code"], o["period"].isoformat(), o["value"]) for o in obs), [
            ("BROAD", "2026-10-30", 121.6656),
            ("USDJPY", "2026-10-29", 157.83), ("USDJPY", "2026-10-30", 157.47)])

    def test_a_holiday_is_a_gap_never_zero_or_minus_9999(self):
        _s, obs = fed_h10.parse(package(SMALL))
        self.assertFalse([o for o in obs if o["period"] == datetime.date(2026, 11, 26)])

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(fed_h10.ValidationError):
            fed_h10.parse(package([("RXI_N.B.JA", [("2026-10-30", "157.47", "X")])]))

    def test_not_the_package_is_refused(self):
        with self.assertRaises(fed_h10.ValidationError):
            fed_h10.parse(b"<html>retired</html>")

    def test_a_file_prepared_again_with_the_same_rates_is_unchanged(self):
        a, b = package(SMALL), package(SMALL, prepared="2026-11-03T16:52:42")
        self.assertNotEqual(a, b)
        self.assertEqual(fed_h10.canonical_bytes(a), fed_h10.canonical_bytes(b))
        moved = [("RXI_N.B.JA", [("2026-10-30", "157.4800", "A")])] + SMALL[1:]
        self.assertNotEqual(fed_h10.canonical_bytes(a), fed_h10.canonical_bytes(package(moved)))


END = datetime.date(2010, 12, 31)


def _days(first):
    """Every weekday from `first` to END: each rate ends on the same day."""
    out, d = [], first
    while d <= END:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


class Validate(unittest.TestCase):
    def history(self, **change):
        obs = []
        for code, _fed, _n, _u, first in fed_h10.SERIES:
            value = {"USDJPY": 150.0, "USDHKD": 7.8, "EURUSD": 1.1, "BROAD": 120.0}[code]
            obs += [{"code": code, "period": d, "value": value} for d in _days(first)]
        for o in obs:
            if (o["code"], o["period"]) in change:
                o["value"] = change[(o["code"], o["period"])]
        return fed_h10.parse(package([]))[0], obs

    def test_a_full_history_passes(self):
        out = fed_h10.validate(*self.history())
        self.assertEqual(out["series"], 4)

    def test_a_rate_outside_its_band_is_refused(self):
        series, obs = self.history()
        obs[5]["value"] = 1575.0                    # a misplaced decimal on the yen
        with self.assertRaises(fed_h10.ValidationError):
            fed_h10.validate(series, obs)

    def test_a_long_gap_is_refused_but_august_1971_is_known(self):
        series, obs = self.history()
        cut = [o for o in obs if not (o["code"] == "EURUSD" and o["period"].year == 1999 and o["period"].month == 6)]
        with self.assertRaises(fed_h10.ValidationError):
            fed_h10.validate(series, cut)
        yen = [o for o in obs if not (o["code"] == "USDJPY" and
                                      datetime.date(1971, 8, 16) <= o["period"] <= datetime.date(1971, 8, 30))]
        fed_h10.validate(series, yen)


if __name__ == "__main__":
    unittest.main()
