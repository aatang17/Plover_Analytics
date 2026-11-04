# -*- coding: utf-8 -*-
"""The Ministry's annual births and fertility tables that carry
population-jp-history past the Statistics Bureau's last year.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_mhlw_annual
"""
import unittest

from app.adapters import mhlw_annual as m

PREFS = ["%02d" % i for i in range(1, 48)]


def _page(kind, rows, years=(2024, 2023)):
    """One getStatsData page: rows are (area, year, value-text)."""
    item = ({"@cat01": m.BIRTHS_CODE} if kind == "births" else {"@tab": "10060"})
    values = [dict(item, **{"@area": a, "@time": "%d000000" % y, "$": v}) for a, y, v in rows]
    return {"GET_STATS_DATA": {"STATISTICAL_DATA": {
        "CLASS_INF": {"CLASS_OBJ": [{"@id": "time", "CLASS": [
            {"@code": "%d000000" % y, "@name": "%d年" % y} for y in years]}]},
        "DATA_INF": {"VALUE": values}}}}


def _births_rows(year, each, abroad, national):
    rows = [("%s000" % p, year, str(each)) for p in PREFS]
    rows += [("00000", year, str(national)), ("51000", year, str(abroad)),
             ("52000", year, "・"), ("13100", year, "62310")]      # unknown, a city
    return rows


class EstatTables(unittest.TestCase):
    def test_births_reconcile_with_births_abroad(self):
        page = _page("births", _births_rows(2024, 1000, 18, 47 * 1000 + 18))
        out, reconciled = m.parse_estat({"pages": [page]}, "births", PREFS)
        self.assertEqual(reconciled, 1)
        self.assertEqual(out[("00", 2024)], 47018)
        self.assertEqual(out[("13", 2024)], 1000)        # the prefecture, not the city
        self.assertEqual(len(out), 48)                     # unknown and abroad not stored

    def test_births_that_do_not_add_up_are_refused(self):
        page = _page("births", _births_rows(2024, 1000, 18, 47 * 1000 + 19))
        with self.assertRaises(m.SourceError):
            m.parse_estat({"pages": [page]}, "births", PREFS)

    def test_fertility_is_kept_to_two_decimals_and_blanks_stay_blank(self):
        page = _page("tfr", [("00000", 2024, "1.15"), ("13000", 2024, "0.96"),
                             ("47000", 2024, "-")])
        out, _ = m.parse_estat({"pages": [page]}, "tfr", PREFS)
        self.assertEqual(out[("00", 2024)], 1.15)
        self.assertNotIn(("47", 2024), out)               # unpublished, never zero

    def test_a_year_label_that_disagrees_with_its_code_is_refused(self):
        page = _page("tfr", [("00000", 2024, "1.15")], years=(2023,))
        page["GET_STATS_DATA"]["STATISTICAL_DATA"]["CLASS_INF"]["CLASS_OBJ"][0]["CLASS"] = [
            {"@code": "2024000000", "@name": "2023年"}]
        with self.assertRaises(m.SourceError):
            m.parse_estat({"pages": [page]}, "tfr", PREFS)


if __name__ == "__main__":
    unittest.main()
