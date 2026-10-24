# -*- coding: utf-8 -*-
"""US 5% holders (equity/sec_13dg_extract.py, app/sec_13dg_api.py): real
Schedule 13D and 13G/A filings parse to their cover-page figures as filed,
the loader reads each filing once and takes the first catch-up in slices, and
the API ranks holders by stake without adding affiliates' percents together.
No network, no data/ files."""
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
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
EQ = str(ROOT / "equity")
if EQ not in sys.path:
    sys.path.insert(0, EQ)
_spec = importlib.util.spec_from_file_location("sec_13dg_extract", str(ROOT / "equity" / "sec_13dg_extract.py"))
x = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(x)

FIX = ROOT / "tests" / "fixtures"
D13 = "0001213900-26-111068"      # TEG Overseas, a new 13D: one individual, 31.14%, purpose stated
G13A = "0000892712-26-000370"     # i3 Verticals, 13G/A no. 5: Geneva Capital at 0%, out under 5%


def _blob(name):
    return gzip.decompress((FIX / name).read_bytes())


class Parse(unittest.TestCase):
    def test_a_13d_cover_page_as_filed(self):
        r = x.parse(_blob("sc13d_%s.txt.gz" % D13))
        f, p = r["filing"], r["persons"]
        self.assertEqual((f["form"], f["amendment"], f["issuer_cik"], f["cusip"]),
                         ("SCHEDULE 13D", False, 1999261, "85254A101"))
        self.assertEqual(f["event_date"], datetime.date(2026, 10, 13))           # MM/DD/YYYY on the form
        self.assertTrue(f["purpose"].startswith("On October 6, 2026"))
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["person_cik"], p[0]["person_type"], p[0]["shares"], p[0]["percent"], p[0]["fund_type"]),
                         (2160471, "IN", 58591499.0, 31.14, "OO"))
        self.assertEqual((p[0]["sole_vote"], p[0]["shared_vote"]), (58591499.0, 0.0))

    def test_a_13g_amendment_reports_the_exit(self):
        r = x.parse(_blob("sc13ga_%s.txt.gz" % G13A))
        f, p = r["filing"], r["persons"]
        self.assertEqual((f["form"], f["amendment_no"], f["rule"], f["five_percent_or_less"]),
                         ("SCHEDULE 13G/A", 5, "Rule 13d-1(b)", True))
        self.assertIsNone(f["purpose"])                                          # a 13G states none
        self.assertEqual((p[0]["name"], p[0]["person_type"], p[0]["percent"]),
                         ("GENEVA CAPITAL MANAGEMENT LLC", "IA", 0.0))           # 0 as filed, not missing

    def test_a_filing_with_no_schedule_xml_is_refused(self):
        with self.assertRaises(x.FilingError):
            x.parse(b"<SEC-HEADER>\nACCESSION NUMBER:\t0000000000-26-000001\n</SEC-HEADER>\n<TEXT>SC 13D</TEXT>")


class FakeArchive(object):
    def __init__(self, recs, blobs):
        self.recs, self.blobs = recs, blobs

    def records(self, day, forms):
        return [r for r in self.recs if r["date"] == day and r["form"] in forms]

    def filing(self, rec):
        return self.blobs[rec["accession"]]


def _db():
    con = duckdb.connect(os.path.join(tempfile.mkdtemp(), "sec.duckdb"))
    con.execute(x.SCHEMA_SQL)
    return con


class Loader(unittest.TestCase):
    def setUp(self):
        self.con = _db()
        self.archive = FakeArchive(
            [{"date": "2026-10-20", "accession": D13, "form": "SCHEDULE 13D"},
             {"date": "2026-10-20", "accession": G13A, "form": "SCHEDULE 13G/A"}],
            {D13: _blob("sc13d_%s.txt.gz" % D13), G13A: _blob("sc13ga_%s.txt.gz" % G13A)})

    def test_each_filing_is_read_once(self):
        days = [datetime.date(2026, 10, 19), datetime.date(2026, 10, 20)]
        self.assertEqual(x.run(self.con, self.archive, days, log=lambda *a: None), "ok")
        x.run(self.con, self.archive, days, log=lambda *a: None)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sec_13dg_filings").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sec_13dg_persons").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT max(day) FROM sec_13dg_filings").fetchone()[0], datetime.date(2026, 10, 20))

    def test_the_first_catch_up_comes_in_slices_and_resumes(self):
        today = datetime.date(2026, 10, 24)
        first = x._days(self.con, None, today)
        self.assertEqual((first[0], len(first)), (x.STRUCTURED_FROM, x.MAX_DAYS_PER_RUN))
        x.run(self.con, self.archive, first, log=lambda *a: None)
        second = x._days(self.con, None, today)
        self.assertEqual(second[0], first[-1] - datetime.timedelta(days=x.LOOKBACK_DAYS))


class Api(unittest.TestCase):
    """The routes over a small database: two holders of one company, one of
    them a filing with three affiliated reporting persons."""

    @classmethod
    def setUpClass(cls):
        cls.con = _db()
        cls.con.execute("CREATE TABLE sec_companies (cik BIGINT, name VARCHAR, ticker VARCHAR, tickers VARCHAR)")
        cls.con.execute("INSERT INTO sec_companies VALUES (1000, 'Acme Corp', 'ACME', 'ACME')")
        f = ("INSERT INTO sec_13dg_filings (accession, day, filed, form, amendment, issuer_cik, issuer_name, cusip, "
             "filer_cik, purpose) VALUES (?, ?, ?, ?, ?, 1000, 'Acme Corp', '000000AA1', ?, ?)")
        cls.con.execute(f, ["a-1", datetime.date(2026, 9, 1), datetime.date(2026, 9, 1), "SCHEDULE 13D", False, 77, "To seek board seats."])
        cls.con.execute(f, ["a-2", datetime.date(2026, 10, 1), datetime.date(2026, 10, 1), "SCHEDULE 13D/A", True, 77, None])
        cls.con.execute(f, ["b-1", datetime.date(2026, 10, 2), datetime.date(2026, 10, 2), "SCHEDULE 13G", False, 88, None])
        p = "INSERT INTO sec_13dg_persons (accession, seq, person_cik, name, shares, percent) VALUES (?, ?, ?, ?, ?, ?)"
        cls.con.execute(p, ["a-1", 1, 77, "Activist LP", 600.0, 6.0])
        for seq, name in ((1, "Activist LP"), (2, "Activist GP"), (3, "Activist Partner")):
            cls.con.execute(p, ["a-2", seq, 77 if seq == 1 else None, name, 900.0, 9.0])     # one stake, three filers
        cls.con.execute(p, ["b-1", 1, None, "Index Fund Adviser", 500.0, 5.1])
        cls.con.execute("INSERT INTO sec_13dg_runs (run_at, read_through, status) VALUES (now(), current_date, 'ok')")
        cls.patch = mock.patch("app.sec_api._cur", side_effect=lambda: cls.con.cursor())
        cls.patch.start()
        from app.main import app
        cls.c = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.con.close()

    def test_holders_latest_filing_each_largest_first_never_summed(self):
        d = self.c.get("/api/v1/us/stakes/company/ACME").json()
        self.assertEqual(d["cik"], 1000)
        self.assertEqual([(h["lead_name"], h["percent"], h["persons"]) for h in d["holders"]],
                         [("Activist LP", 9.0, 3), ("Index Fund Adviser", 5.1, 1)])      # 9%, not 27%
        self.assertEqual(d["filings_total"], 3)
        self.assertTrue(d["holders"][0]["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/1000/"))

    def test_recent_new_13ds_and_errors(self):
        d = self.c.get("/api/v1/us/stakes/recent?kind=13D&days=800").json()
        self.assertEqual([f["accession"] for f in d["filings"]], ["a-1"])                # the amendment is left out
        self.assertEqual(self.c.get("/api/v1/us/stakes/recent?kind=13X").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/stakes/holder/abc").status_code, 400)

    def test_holder_and_health(self):
        d = self.c.get("/api/v1/us/stakes/holder/77").json()
        self.assertEqual([c["accession"] for c in d["companies"]], ["a-2"])
        from app import sec_13dg_api
        h = sec_13dg_api.health()[0]
        self.assertEqual((h["dataset"], h["archive_read_through"]), ("sec-13dg", "2026-10-02"))
        self.assertEqual(h["status"], "attention")       # the newest filing read is weeks old: stale


if __name__ == "__main__":
    unittest.main()
