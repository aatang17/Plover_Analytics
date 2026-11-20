# -*- coding: utf-8 -*-
"""What the text copy of two pages leads with (app/readable.py): the investor
flows page with who bought, net, in ¥bn; the 5% filings page with the week's
largest new stakes and increases, not the archive's totals.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_readable_leads
"""
import datetime
import unittest
from unittest.mock import patch

from app import api, lvh_api, readable
from app.adapters import jpx_investor_type


class _Con(object):
    def close(self):
        pass


def _series(code, sid):
    return {"series_id": sid, "code": code, "name_en": code, "unit": "JPY_thousand",
            "sort_order": sid, "name_ja": None}


class NetFlowsTest(unittest.TestCase):
    def build(self, values):
        hint = jpx_investor_type.PRESENTATION["net"]
        rows, data = [], {}
        for i, (key, _) in enumerate(hint["investors"]):
            for j, side in enumerate(("purchases", "sales")):
                code = hint["code"].format(investor=key, side=side)
                sid = i * 2 + j + 1
                rows.append(_series(code, sid))
                data[sid] = values.get((key, side), {})
        with patch.object(api, "_con", return_value=_Con()), \
                patch.object(api, "_release", return_value={"label": "r"}), \
                patch.object(readable, "_by_codes", return_value=rows), \
                patch.object(api, "_values_bulk", return_value=data):
            return readable._net_block("investor-flows-jp", jpx_investor_type,
                                       jpx_investor_type.MANIFEST, "weekly",
                                       {"net": {"calc": "net = purchases − sales"}})

    def test_leads_with_the_net_in_billions(self):
        w1, w2 = datetime.date(2026, 10, 26), datetime.date(2026, 11, 2)
        b = self.build({
            ("foreigners", "purchases"): {w1: 32504604271, w2: 31659773035},
            ("foreigners", "sales"): {w1: 32298292622, w2: 31741534722},
            ("individuals", "purchases"): {w2: 5000000000},
            ("individuals", "sales"): {w2: 4900000000},
        })
        self.assertEqual(b["sentence"],
                         "In the week beginning 2 Nov 2026, foreigners net sold ¥81.8bn of "
                         "Japanese equities on the Tokyo and Nagoya markets, while individuals "
                         "net bought ¥100.0bn. Net purchases are calculated from the published "
                         "gross figures.")
        self.assertEqual(b["table"]["rows"][0][:3], ["2 Nov 2026", "−81.8", "100.0"])
        # A week missing one side is a dash, never zero.
        self.assertEqual(b["table"]["rows"][1][:3], ["26 Oct 2026", "206.3", "—"])
        self.assertTrue(b["calc"].startswith("Net: "))

    def test_no_data_no_block(self):
        self.assertIsNone(self.build({}))


def _filing(doc, code, kind, ratio, change, filer, filed="2026-11-18"):
    return {"doc_id": doc, "issuer_sec_code": code, "issuer_name_en": "Company " + code,
            "issuer_name_raw": None, "report_type": kind, "ratio_pct": ratio,
            "ratio_change_pp": change, "filer_name": filer, "filer_name_en": filer,
            "filed_date": datetime.date.fromisoformat(filed)}


class StakesWeekTest(unittest.TestCase):
    def run_week(self, rows):
        seen = {}

        def recent(**kw):
            seen.update(kw)
            return {"filings": rows, "total": len(rows), "next_offset": None}
        with patch.object(lvh_api, "recent", side_effect=recent):
            out = readable._stakes_week(datetime.date(2026, 11, 18))
        return out, seen

    def test_leads_with_the_largest_new_stake_and_increase(self):
        out, seen = self.run_week([
            _filing("A", "7203", "initial", 6.5, None, "Small Holder Inc."),
            _filing("B", "6758", "initial", 12.25, None, "Big Holder Ltd."),
            _filing("C", "9984", "change", 20.0, 3.1, "Mover Capital"),
            _filing("D", "9984", "change", 9.0, -1.0, "Seller Co."),
            _filing("E", "617", "initial", 99.98, None, "A Founder"),       # unlisted
            _filing("F", "4563", "change", 150.0, 140.0, "Typo Fund"),      # impossible
            _filing("G", "8306", "amendment", 30.0, 25.0, "Correction Co."),
            # The same holder refiling on the same company is one stake.
            _filing("H", "6758", "initial", 11.0, None, "Big Holder Ltd.", filed="2026-11-13"),
        ])
        sentence, table = out
        self.assertEqual(seen["since"], "2026-11-12")
        self.assertEqual(seen["until"], "2026-11-18")
        self.assertTrue(sentence.startswith(
            "In the seven days 12 Nov 2026 to 18 Nov 2026, 7 5% reports were filed on listed "
            "companies: 2 holders crossed 5% for the first time, 1 holder raised a stake and "
            "1 holder cut one."), sentence)
        self.assertIn("The largest new stake is Big Holder Ltd. with 12.25% of Company 6758.",
                      sentence)
        self.assertIn("The largest increase is Mover Capital in Company 9984, up 3.10 "
                      "percentage points to 20.00%.", sentence)
        holders = [r[3] for r in table["rows"]]
        self.assertEqual(holders, ["Big Holder Ltd.", "Small Holder Inc.", "Mover Capital"])
        self.assertNotIn("A Founder", sentence)
        self.assertNotIn("Typo Fund", sentence)

    def test_an_empty_week_says_nothing(self):
        out, _ = self.run_week([])
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
