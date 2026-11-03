# -*- coding: utf-8 -*-
"""Which register rows are custody accounts (equity/ownership_extract.py).

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_ownership_classify
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "equity"))
import ownership_extract as own  # noqa: E402


class CustodyTest(unittest.TestCase):

    def test_adr_depositary_nominee_in_every_spelling(self):
        # 2026-11-03: Honda's register files it in katakana, and 21 rows read as
        # a company; seven more spelled "MOXLEY & CO LLC" read as an overseas owner.
        for name in ("モックスレイ・アンド・カンパニー・エルエルシー", "MOXLEY & CO LLC",
                     "MOXLEY AND COMPANY LLC"):
            self.assertEqual(own.classify_holder(name, name)[0], "foreign_nominee", name)

    def test_an_owner_named_through_its_custodian_is_still_an_owner(self):
        self.assertEqual(own.classify_holder("GOVERNMENT OF NORWAY（常任代理人 シティバンク）",
                                             "GOVERNMENT OF NORWAY")[0], "foreign_entity")
        self.assertEqual(own.classify_holder("株式会社豊田自動織機", "株式会社豊田自動織機")[0], "entity")



class StaleTest(unittest.TestCase):
    """Which stored filings the current rules would read differently."""

    def test_finds_only_rows_whose_type_the_rules_now_read_differently(self):
        import tempfile, os, duckdb
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "eq.duckdb")
            con = duckdb.connect(path)
            con.execute("CREATE TABLE eq_major_shareholders (doc_id VARCHAR, name_raw VARCHAR, "
                        "name_base VARCHAR, account_raw VARCHAR, holder_kind VARCHAR)")
            con.executemany("INSERT INTO eq_major_shareholders VALUES (?,?,?,?,?)", [
                ("S1", "モックスレイ・アンド・カンパニー・エルエルシー",
                 "モックスレイ・アンド・カンパニー・エルエルシー", None, "entity"),
                ("S2", "株式会社豊田自動織機", "株式会社豊田自動織機", None, "entity"),
                ("S3", "MOXLEY & CO LLC", "MOXLEY & CO LLC", None, "foreign_nominee"),
            ])
            con.close()
            self.assertEqual(own.stale_classifications(path), ["S1"])


class ReclassifyRunTest(unittest.TestCase):
    """app/reclassify.py: copy, re-read, swap only when something was re-read."""

    def _run(self, rc):
        import os
        from unittest.mock import patch
        from app import backfill, reclassify
        calls = []
        with patch.dict(os.environ, {"EDINET_S3_BUCKET": "x"}), \
             patch.object(backfill, "LIVE_EQUITY", __import__("pathlib").Path(__file__)), \
             patch.object(backfill, "fresh_copy", lambda live, work: work), \
             patch.object(backfill, "_run", lambda cmd, env, cwd: rc), \
             patch.object(backfill, "swap", lambda work, live: calls.append("swap")), \
             patch.object(backfill, "_discard", lambda work: calls.append("discard")), \
             patch.object(backfill, "log", lambda msg: None):
            out = reclassify.run()
        return out, calls

    def test_nothing_to_do_discards_the_copy(self):
        self.assertEqual(self._run(3), (0, ["discard"]))

    def test_a_failed_re_read_leaves_the_served_file_alone(self):
        self.assertEqual(self._run(1), (1, ["discard"]))

    def test_a_re_read_is_swapped_in(self):
        self.assertEqual(self._run(0), (0, ["swap"]))


if __name__ == "__main__":
    unittest.main()
