# -*- coding: utf-8 -*-
"""Insider filings read the day after they are filed (equity/sec_insiders_daily.py):
a real Form 4 parses to the values the SEC's own extraction holds for it, the
loader reads each filing once, refuses a file that does not match its recorded
hash, the quarterly set replaces the daily rows, and the health row is held to
the daily window once daily filings exist. No network, no data/ files."""
import datetime
import gzip
import hashlib
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
EQ = str(ROOT / "equity")
if EQ not in sys.path:
    sys.path.insert(0, EQ)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "equity" / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


daily = _load("sec_insiders_daily")
quarterly = _load("sec_insiders_extract")

# Dell Technologies, 16 September 2026: Silver Lake and four affiliates, one
# option exercise and fourteen sales. The SEC's 2026q3 Insider Transactions
# Data Set holds the values asserted below for this accession.
FIXTURE = ROOT / "tests" / "fixtures" / "form4_0001193125-26-393129.txt.gz"
ACC = "0001193125-26-393129"


def _blob():
    return gzip.decompress(FIXTURE.read_bytes())


class Parse(unittest.TestCase):
    def setUp(self):
        self.r = daily.parse(_blob())

    def test_filing(self):
        f = self.r["filing"]
        self.assertEqual(f["accession"], ACC)
        self.assertEqual(f["filed"], datetime.date(2026, 9, 16))
        self.assertEqual(f["period"], datetime.date(2026, 9, 14))
        self.assertEqual((f["document_type"], f["issuer_cik"], f["issuer_symbol"]), ("4", 1571996, "DELL"))
        self.assertEqual(f["issuer_name"], "Dell Technologies Inc.")
        self.assertIs(f["plan_10b5_1"], False)
        self.assertEqual(f["quarter"], "daily")

    def test_owners_relationships_and_file_numbers(self):
        o = {x["owner_cik"]: x for x in self.r["owners"]}
        self.assertEqual(len(o), 5)
        self.assertEqual(o[1418226]["relationship"], "Director,TenPercentOwner")
        self.assertEqual(o[1651403]["relationship"], "Director")
        self.assertEqual(o[1651403]["owner_name"], "Durban Egon")
        self.assertTrue(all(x["file_number"] == "001-37867" for x in o.values()))

    def test_trades_as_filed(self):
        t = self.r["transactions"]
        self.assertEqual((len(t), sum(x["derivative"] for x in t)), (15, 1))
        first = [x for x in t if not x["derivative"]][:2]
        self.assertEqual((first[0]["trans_code"], first[0]["acquired_disposed"], first[0]["shares"],
                          first[0]["price"], first[0]["shares_after"], first[0]["direct_indirect"]),
                         ("M", "A", 20434.0, None, 38479.0, "I"))      # an exercise reports no price: NULL, never 0
        self.assertEqual((first[1]["trans_code"], first[1]["shares"], first[1]["price"]), ("S", 2588.0, 530.67))
        self.assertEqual(len(self.r["footnotes"]), 24)

    def test_a_file_without_ownership_xml_is_refused(self):
        with self.assertRaises(daily.FilingError):
            daily.parse(b"<SEC-HEADER>\nACCESSION NUMBER:\t0000000000-26-000001\n</SEC-HEADER>\n<DOCUMENT>paper</DOCUMENT>")


class FakeArchive(object):
    def __init__(self, recs, blobs):
        self.recs, self.blobs = recs, blobs

    def records(self, day):
        return [r for r in self.recs if r["date"] == day]

    def filing(self, rec):
        blob = self.blobs[rec["accession"]]
        if hashlib.sha256(blob).hexdigest() != rec["sha256"]:
            raise daily.FilingError("stored file does not match the SHA-256 recorded at capture")
        return blob


class Loader(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        self.con = duckdb.connect(self.path)
        self.con.execute(quarterly.SCHEMA_SQL)
        blob = _blob()
        self.rec = {"date": "2026-09-16", "accession": ACC, "form": "4", "sha256": hashlib.sha256(blob).hexdigest()}
        self.archive = FakeArchive([self.rec], {ACC: blob})
        self.days = [datetime.date(2026, 9, 16)]

    def tearDown(self):
        self.con.close()

    def test_a_filing_is_read_once(self):
        self.assertEqual(daily.run(self.con, self.archive, self.days, log=lambda *a: None), "ok")
        self.assertEqual(self.con.execute("SELECT count(*) FROM sec_ins_transactions WHERE quarter = 'daily'").fetchone()[0], 15)
        daily.run(self.con, self.archive, self.days, log=lambda *a: None)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sec_ins_filings").fetchone()[0], 1)
        runs = self.con.execute("SELECT seen, loaded, failed, read_through FROM sec_ins_daily_runs ORDER BY run_at").fetchall()
        self.assertEqual([r[:3] for r in runs], [(1, 1, 0), (1, 0, 0)])
        self.assertEqual(runs[0][3], datetime.date(2026, 9, 16))

    def test_a_file_that_does_not_match_its_hash_is_not_read(self):
        self.rec["sha256"] = "0" * 64
        self.assertEqual(daily.run(self.con, self.archive, self.days, log=lambda *a: None), "failed")
        self.assertEqual(self.con.execute("SELECT count(*) FROM sec_ins_filings").fetchone()[0], 0)

    def test_the_quarterly_set_replaces_the_daily_rows(self):
        daily.run(self.con, self.archive, self.days, log=lambda *a: None)
        # what load_quarter does first for every accession in the set it loads
        self.con.execute("CREATE TEMP TABLE raw_submission AS SELECT ? AS ACCESSION_NUMBER", [ACC])
        self.con.execute("DELETE FROM sec_ins_filings WHERE accession IN (SELECT ACCESSION_NUMBER FROM raw_submission)")
        for t in ("sec_ins_owners", "sec_ins_transactions", "sec_ins_holdings", "sec_ins_footnotes"):
            self.con.execute("DELETE FROM %s WHERE accession IN (SELECT ACCESSION_NUMBER FROM raw_submission)" % t)
        left = sum(self.con.execute("SELECT count(*) FROM %s WHERE quarter = 'daily'" % t).fetchone()[0]
                   for t in daily._COLUMNS)
        self.assertEqual(left, 0)

    def test_days_start_after_the_newest_set(self):
        self.con.execute("INSERT INTO sec_ins_quarters (quarter, status) VALUES ('2026q3', 'ok')")
        days = daily._days(self.con, None, datetime.date(2026, 10, 24))
        self.assertEqual((days[0], days[-1]), (datetime.date(2026, 10, 1), datetime.date(2026, 10, 24)))


class Health(unittest.TestCase):
    def test_daily_filings_hold_the_row_to_the_daily_window(self):
        from app import sec_insiders_api as api
        con = duckdb.connect(os.path.join(tempfile.mkdtemp(), "sec.duckdb"))
        con.execute(quarterly.SCHEMA_SQL)
        con.execute("INSERT INTO sec_ins_quarters (quarter, loaded_at, status) VALUES ('2026q3', now(), 'ok')")
        today = datetime.date.today()
        with mock.patch.object(api.sec_api, "_cur", return_value=con.cursor()):
            q = api.health()[0]
            self.assertEqual((q["archive_read_through"], q["stale_after_days"]), ("2026-09-30", api.STALE_AFTER_DAYS))
            con.execute("INSERT INTO sec_ins_filings (accession, quarter, filed) VALUES ('x', 'daily', ?)",
                        [today - datetime.timedelta(days=1)])
            r = api.health()[0]
            self.assertEqual((r["days_behind"], r["stale_after_days"], r["status"]), (1, api.DAILY_STALE_AFTER_DAYS, "ok"))
            con.execute("UPDATE sec_ins_filings SET filed = ?", [today - datetime.timedelta(days=9)])
            self.assertEqual(api.health()[0]["status"], "attention")    # the daily read stopped
        con.close()


if __name__ == "__main__":
    unittest.main()
