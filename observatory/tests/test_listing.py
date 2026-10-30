# -*- coding: utf-8 -*-
"""Delisted status: both sources must agree, and a past view carries none.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_listing
"""
import unittest
from unittest import mock

import duckdb

from app import asof, listing


def _db(jpx_rows=True):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE eq_entities (sec_code VARCHAR, listed BOOLEAN)")
    con.execute("CREATE TABLE eq_class_vintages (vintage_id VARCHAR, as_of DATE, "
                "public BOOLEAN, source VARCHAR, fetched_at TIMESTAMP)")
    con.execute("CREATE TABLE eq_classification (vintage_id VARCHAR, sec_code VARCHAR)")
    con.execute("INSERT INTO eq_entities VALUES ('6588', TRUE), ('2928', TRUE), (NULL, FALSE)")
    if jpx_rows:
        con.execute("INSERT INTO eq_class_vintages VALUES "
                    "('old', '2026-01-31', TRUE, 'jpx-listed', now()), "
                    "('new', '2026-09-30', TRUE, 'jpx-listed', now())")
        con.execute("INSERT INTO eq_classification VALUES ('old', '6502'), ('new', '6588')")
    return con


class ListingTest(unittest.TestCase):
    def test_gone_only_when_both_sources_agree(self):
        listed = listing.listed_codes(_db())
        self.assertIn("6588", listed)     # on both
        self.assertIn("2928", listed)     # regional exchange: registry only
        self.assertNotIn("6502", listed)  # only an old JPX vintage had it

    def test_no_jpx_list_marks_nobody(self):
        self.assertIsNone(listing.listed_codes(_db(jpx_rows=False)))
        self.assertIsNone(listing.listed_codes(duckdb.connect(":memory:")))

    def test_mark_flags_and_moves_last(self):
        rows = [{"sec_code": "6502"}, {"sec_code": "6588"}, {"sec_code": None}]
        with mock.patch.object(listing, "_listed_now", return_value={"6588"}):
            out = listing.mark(rows)
        self.assertEqual([r["sec_code"] for r in out], ["6588", None, "6502"])
        self.assertTrue(out[2]["delisted"])
        self.assertNotIn("delisted", out[0])

    def test_unknown_status_changes_nothing(self):
        rows = [{"sec_code": "6502"}]
        with mock.patch.object(listing, "_listed_now", return_value=None):
            self.assertEqual(listing.mark(rows), [{"sec_code": "6502"}])
            self.assertFalse(listing.is_delisted("6502"))

    def test_past_view_carries_no_status(self):
        with asof.scope("2020-01-01"):
            self.assertIsNone(listing._listed_now())

    def test_a_feed_keeps_its_signature(self):
        import inspect
        from app import equity_api
        self.assertIn("q", inspect.signature(equity_api.companies).parameters)


if __name__ == "__main__":
    unittest.main()
