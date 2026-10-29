# -*- coding: utf-8 -*-
"""The event calendar (equity/us_events.py, app/us_events_api.py): the
parsers read the Fed, BLS and EDGAR pages as they are laid out, the
quarterly-results rule marks the right Item 2.02 filing, and the API serves
rows and a health line from a small database. No network, no data/ files."""
import datetime
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("us_events", str(ROOT / "equity" / "us_events.py"))
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

D = datetime.date

FOMC_PAGE = """
<div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
<div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
<div class="fomc-meeting__date col-xs-4">27-28</div>
<a href="/newsevents/pressreleases/monetary20260128a.htm">HTML</a>
<a href="/newsevents/pressreleases/monetary20260128a1.htm">Implementation Note</a>
<div class="fomc-meeting__month col-xs-5"><strong>March</strong></div>
<div class="fomc-meeting__date col-xs-4">17-18*</div>
<a href="/newsevents/pressreleases/monetary20260318a.htm">HTML</a>
<div class="fomc-meeting__month col-xs-5"><strong>December</strong></div>
<div class="fomc-meeting__date col-xs-4">8-9</div>
<div class="panel-heading"><h4><a id="2">2027 FOMC Meetings</a></h4></div>
<div class="fomc-meeting__month col-xs-5"><strong>January/February</strong></div>
<div class="fomc-meeting__date col-xs-4">31-1</div>
"""

CPI_ARCHIVE = """
<a href="/news.release/archives/cpi_10142026.htm">September 2026 Consumer Price Index</a> (HTML)
<a href="/news.release/archives/cpi_09112026.htm">August 2026 Consumer Price Index</a> (HTML)
<a href="/news.release/archives/cpi_02302026.htm">bad date</a>
"""

CPI_SCHEDULE = """
<tr class="release-list-odd-row"><td>October 2026</td><td>Nov. 10, 2026</td><td>08:30 AM</td></tr>
<tr class="release-list-even-row"><td>November 2026</td><td>Dec. 10, 2026</td><td>08:30 AM</td></tr>
"""


class Parsers(unittest.TestCase):
    def test_fomc_statement_days_and_scheduled_meetings(self):
        self.assertEqual(ev.parse_fomc_statements(FOMC_PAGE), {D(2026, 1, 28), D(2026, 3, 18)})
        sched = ev.parse_fomc_scheduled(FOMC_PAGE)
        # a two-day meeting's decision day is its last day; "31-1" over January/February ends on 1 February
        self.assertEqual(sched, {D(2026, 1, 28), D(2026, 3, 18), D(2026, 12, 9), D(2027, 2, 1)})

    def test_cpi_archive_and_schedule(self):
        self.assertEqual(ev.parse_cpi_archive(CPI_ARCHIVE),
                         [(D(2026, 10, 14), "September 2026"), (D(2026, 9, 11), "August 2026")])
        self.assertEqual(ev.parse_cpi_schedule(CPI_SCHEDULE),
                         [(D(2026, 11, 10), "October 2026"), (D(2026, 12, 10), "November 2026")])

    def test_shutdown_dates_are_checked_against_the_article(self):
        rows = ev.shutdown_rows("... began on December 22, 2018, and ... October 1, 2025 ...")
        by_start = {r[0]: r[3] for r in rows}
        self.assertEqual(by_start[D(2018, 12, 22)], "start date found in the article")
        self.assertIn("unverified", by_start[D(2018, 1, 20)])
        for s, e, _label in ev.SHUTDOWNS:
            self.assertLessEqual(D.fromisoformat(s), D.fromisoformat(e))

    def test_results_filings_are_item_2_02_eight_ks_only(self):
        recent = {"form": ["8-K", "10-Q", "8-K", "8-K/A"], "filingDate": ["2026-10-21", "2026-07-23", "2026-10-02", "2026-05-01"],
                  "items": ["2.02,9.01", "", "7.01,9.01", "2.02"], "accessionNumber": ["a", "b", "c", "d"]}
        self.assertEqual(ev.results_from_submissions(recent),
                         [(D(2026, 10, 21), "2.02,9.01", "a"), (D(2026, 5, 1), "2.02", "d")])


class EarningsRule(unittest.TestCase):
    def test_the_first_filing_from_seven_days_after_quarter_end_is_the_results(self):
        # Tesla: delivery numbers two days after quarter end, results three weeks after, both under Item 2.02
        filings = [(D(2026, 10, 2), "2.02,9.01", "1"), (D(2026, 10, 21), "2.02,9.01", "2"),
                   (D(2026, 7, 2), "2.02,9.01", "3"), (D(2026, 7, 22), "2.02,9.01", "4")]
        marks = ev.mark_earnings(filings, "1231")
        self.assertEqual(marks[D(2026, 10, 21)], (True, D(2026, 9, 30)))
        self.assertEqual(marks[D(2026, 10, 2)][0], False)
        self.assertEqual(marks[D(2026, 7, 22)], (True, D(2026, 6, 30)))
        self.assertEqual(marks[D(2026, 7, 2)][0], False)

    def test_one_results_filing_per_quarter_and_a_late_one_is_not_results(self):
        filings = [(D(2026, 4, 22), "2.02", "1"), (D(2026, 5, 15), "2.02,7.01", "2"), (D(2026, 9, 1), "2.02", "3")]
        marks = ev.mark_earnings(filings, "1231")
        self.assertTrue(marks[D(2026, 4, 22)][0])
        self.assertFalse(marks[D(2026, 5, 15)][0])          # the quarter to March already has its results
        self.assertEqual(marks[D(2026, 9, 1)], (True, D(2026, 6, 30)))   # the first filing after June, 63 days on, is June's results
        late = ev.mark_earnings([(D(2026, 9, 20), "2.02", "9")], "1231")    # 82 days after June: too late to be its results
        self.assertEqual(late[D(2026, 9, 20)], (False, D(2026, 6, 30)))

    def test_quarter_ends_follow_the_fiscal_year_end(self):
        self.assertEqual(ev.quarter_ends("0930", D(2025, 1, 1), D(2025, 12, 31)),
                         [D(2025, 3, 31), D(2025, 6, 30), D(2025, 9, 30), D(2025, 12, 31)])
        self.assertEqual(ev.quarter_ends("0131", D(2025, 1, 1), D(2025, 6, 30)), [D(2025, 1, 31), D(2025, 4, 30)])
        self.assertEqual(ev.quarter_ends(None, D(2025, 11, 1), D(2025, 12, 31)), [D(2025, 12, 31)])


def _db():
    """A sec.duckdb with a few events and pulls, for the API and the health line."""
    path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
    con = ev._connect(path)
    con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO sec_companies VALUES (1318605, 'TSLA', 'Tesla, Inc.')")
    rows = [
        ("fomc", D(2026, 9, 16), None, None, None, "FOMC statement", "x", False, None, "Federal Reserve Board", "u"),
        ("fomc", D(2026, 10, 28), None, None, None, "FOMC meeting (scheduled)", "x", True, None, "Federal Reserve Board", "u"),
        ("cpi", D(2026, 10, 14), None, None, None, "CPI release", "x", False, None, "BLS", "u"),
        ("shutdown", D(2025, 10, 1), D(2025, 11, 12), None, None, "Federal government shutdown, 43 days", "x", False, None, "Wikipedia", "u"),
        ("results", D(2026, 10, 21), None, 1318605, "TSLA", "Results announced (8-K)", "x", False, True, "SEC", "u"),
        ("results", D(2026, 10, 2), None, 1318605, "TSLA", "Results-related 8-K", "x", False, False, "SEC", "u"),
    ]
    ev._replace(con, "fomc", [r for r in rows if r[0] == "fomc"])
    ev._replace(con, "cpi", [r for r in rows if r[0] == "cpi"])
    ev._replace(con, "shutdown", [r for r in rows if r[0] == "shutdown"])
    ev._replace(con, "results", [r for r in rows if r[0] == "results"], cik=1318605)
    for k in ("fomc", "cpi", "shutdown", "results"):
        ev._record(con, k, "ok", 1, 1, None, None)
    con.close()
    return path


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = _db()
        from app import sec_api
        cls._patch = mock.patch.object(sec_api, "DB_PATH", pathlib.Path(cls.path))
        cls._patch.start()
        sec_api._READER, sec_api._READER_VERSION = None, None
        from fastapi.testclient import TestClient
        from app.main import app
        cls.c = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        from app import sec_api
        cls._patch.stop()
        sec_api._READER, sec_api._READER_VERSION = None, None

    def test_events_by_kind_ticker_and_date_with_their_sources(self):
        r = self.c.get("/api/v1/us/events", params={"kind": "fomc"}).json()
        self.assertEqual([e["date"] for e in r["events"]], ["2026-10-28", "2026-09-16"])
        self.assertTrue(r["events"][0]["scheduled"])
        self.assertEqual(r["provenance"]["trust"], "official")
        r = self.c.get("/api/v1/us/events", params={"ticker": "tsla"}).json()
        self.assertEqual([e["date"] for e in r["events"]], ["2026-10-21"])          # the quarterly results only
        r = self.c.get("/api/v1/us/events", params={"ticker": "TSLA", "earnings_only": 0}).json()
        self.assertEqual(len(r["events"]), 2)
        r = self.c.get("/api/v1/us/events", params={"since": "2026-10-10", "until": "2026-10-20"}).json()
        self.assertEqual([e["kind"] for e in r["events"]], ["cpi"])
        self.assertEqual(self.c.get("/api/v1/us/events", params={"kind": "moon"}).status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/events", params={"since": "2026-13-01"}).status_code, 400)

    def test_summary_and_health(self):
        s = self.c.get("/api/v1/us/events/summary").json()
        self.assertEqual({k["kind"]: k["rows"] for k in s["kinds"]}, {"fomc": 2, "cpi": 1, "shutdown": 1, "results": 2})
        self.assertEqual(len(s["pulls"]), 4)
        from app import us_events_api
        h = us_events_api.health()
        self.assertEqual(h[0]["dataset"], "us-events")
        self.assertEqual(h[0]["shape_flags"], [])
        self.assertEqual(h[0]["status"], "ok")
        self.assertEqual(h[0]["archive_read_through"], "2026-10-21")     # scheduled rows do not count

    def test_the_steps_table_leaves_nothing_out(self):
        from app import sec_api, us_tables
        rows = us_tables.event_rows(sec_api._cur())
        self.assertEqual(len(rows), 6)
        self.assertEqual((rows[-1]["kind"], rows[-1]["date"]), ("fomc", D(2026, 10, 28)))


class Refused(unittest.TestCase):
    def test_a_source_refusing_every_request_stops_the_run(self):
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, form VARCHAR, filed DATE, fye VARCHAR, period DATE)")
        con.execute("INSERT INTO sec_companies SELECT i, 'T' || i FROM range(1, 301) t(i)")
        calls = []

        def refuse(client, item):
            calls.append(item[0])
            return item, None, 0, None, "HTTP 403"
        with mock.patch.object(ev, "_fetch_one", refuse):
            with self.assertRaises(ev.EventsError):
                ev.load_results(con, mock.Mock(pages=0), log=lambda *a: None, today=D(2026, 10, 23), workers=1)
        self.assertLessEqual(len(calls), ev.REFUSED_AFTER + 4)   # it stopped knocking within one batch
        self.assertEqual(con.execute("SELECT status FROM us_event_pulls ORDER BY pull_id DESC LIMIT 1").fetchone()[0], "failed")
        con.close()


class LoaderGuards(unittest.TestCase):
    def test_the_first_run_reads_every_company_and_later_runs_take_turns(self):
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, form VARCHAR, filed DATE, fye VARCHAR, period DATE)")
        con.execute("INSERT INTO sec_companies VALUES (1, 'A'), (2, 'B'), (3, 'C'), (4, NULL)")
        today = D(2026, 10, 23)
        self.assertEqual([c for c, *_ in ev._companies_due(con, 2, today)], [1, 2, 3])     # nothing read yet: all
        con.execute("INSERT INTO us_event_filers VALUES (1, TIMESTAMP '2026-10-22 01:00:00', 'ok', 1, NULL, NULL)")
        con.execute("INSERT INTO us_event_filers VALUES (2, TIMESTAMP '2026-10-01 01:00:00', 'ok', 1, NULL, NULL)")
        self.assertEqual([c for c, *_ in ev._companies_due(con, 1, today)], [3])           # never read comes first
        self.assertEqual([c for c, *_ in ev._companies_due(con, 5, today)], [3, 2])        # 1 was read this week
        con.close()

    def test_the_sp500_is_read_every_night_and_a_failed_read_the_next(self):
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, form VARCHAR, filed DATE, fye VARCHAR, period DATE)")
        con.execute("CREATE TABLE us_index_snapshots (snapshot_id INTEGER, index_name VARCHAR)")
        con.execute("CREATE TABLE us_index_members (snapshot_id INTEGER, cik BIGINT, ticker VARCHAR)")
        con.execute("INSERT INTO us_index_snapshots VALUES (1, 'S&P 500 (SPY holdings)'), (2, 'S&P 500 (SPY holdings)')")
        con.execute("INSERT INTO us_index_members VALUES (1, 4, 'OLD'), (2, 10, 'TSLA'), (2, 11, 'AAPL')")
        con.execute("INSERT INTO sec_companies VALUES (1, 'A'), (2, 'B'), (3, 'C'), (4, 'OLD'), (10, 'TSLA'), (11, 'AAPL')")
        today = D(2026, 10, 24)
        for cik, at, status in [(1, "2026-10-01", "ok"), (2, "2026-10-23", "failed"), (3, "2026-10-23", "ok"),
                                (4, "2026-10-01", "ok"), (10, "2026-10-23", "ok"), (11, "2026-10-23", "failed")]:
            con.execute("INSERT INTO us_event_filers VALUES (?, ?, ?, 1, NULL, NULL)", [cik, at + " 03:00:00", status])
        due = [c for c, *_ in ev._companies_due(con, 1, today)]
        # members first, both of them (read yesterday or not), then one of the rest: the failed read before the old one
        self.assertEqual(due, [11, 10, 2])
        self.assertEqual([c for c, *_ in ev._companies_due(con, 5, today)], [11, 10, 2, 1, 4])
        con.execute("UPDATE us_event_filers SET fetched_at = TIMESTAMP '2026-10-24 02:00:00', status = 'ok' WHERE cik IN (10, 11)")
        self.assertEqual([c for c, *_ in ev._companies_due(con, 5, today)][:2], [2, 1])     # read tonight already
        con.close()

    def test_a_heavy_filer_is_read_deep_once_and_nightly_reads_keep_its_history(self):
        """2026-10-29: JPMorgan's six pages of filings reached back only to 2025, so it had 6
        results days, not 35. A company cut short is read deep once; a later read that stops
        short keeps the stored rows before it instead of deleting them."""
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, form VARCHAR, filed DATE, fye VARCHAR, period DATE)")
        con.execute("INSERT INTO sec_companies VALUES (1, 'JPM'), (2, 'AAPL')")
        con.execute("INSERT INTO sec_filings VALUES (1, '10-Q', DATE '2025-08-01', '1231', DATE '2025-06-30')")
        con.execute("INSERT INTO us_event_filers VALUES (1, TIMESTAMP '2026-10-01 01:00:00', 'ok', 6, DATE '2025-04-30', NULL), "
                    "(2, TIMESTAMP '2026-10-01 01:00:00', 'ok', 1, DATE '2015-10-02', NULL)")
        today = D(2026, 10, 29)
        self.assertEqual(ev._short_history(con), {1})                       # stopped at the limit, nothing older stored
        self.assertEqual([(c, deep) for c, _, _, _, deep in ev._companies_due(con, 5, today)], [(1, True), (2, False)])
        pages = []

        def deep_read(client, item):
            pages.append(ev.DEEP_PAGES if item[4] else ev.SHALLOW_PAGES)
            if item[0] != 1:
                return item, [], 1, "2015-10-02", None
            return item, [(D(2018, 1, 12), "2.02,9.01", "a1"), (D(2025, 7, 15), "2.02,9.01", "a2")], 54, "2017-11-02", None
        with mock.patch.object(ev, "_fetch_one", deep_read):
            ev.load_results(con, mock.Mock(pages=0), log=lambda *a: None, today=today, workers=1)
        self.assertEqual(pages, [ev.DEEP_PAGES, ev.SHALLOW_PAGES])
        self.assertEqual(ev._short_history(con), set())                     # now holds 2018: never read deep again

        def shallow_read(client, item):                                     # the next night: six pages, back to May 2025
            return item, [(D(2025, 7, 15), "2.02,9.01", "a2"), (D(2026, 10, 13), "2.02,9.01", "a3")], 6, "2025-05-02", None
        with mock.patch.object(ev, "_fetch_one", shallow_read):
            ev.load_results(con, mock.Mock(pages=0), log=lambda *a: None, today=D(2026, 10, 30), workers=1, only=["JPM"])
        dates = [r[0] for r in con.execute("SELECT date FROM us_events WHERE kind = 'results' AND cik = 1 ORDER BY date").fetchall()]
        self.assertEqual(dates, [D(2018, 1, 12), D(2025, 7, 15), D(2026, 10, 13)])   # 2018 kept, no row twice
        con.close()

    def test_no_member_list_leaves_the_turns_as_they_were(self):
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        con.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR)")
        con.execute("CREATE TABLE sec_filings (cik BIGINT, form VARCHAR, filed DATE, fye VARCHAR, period DATE)")
        con.execute("INSERT INTO sec_companies VALUES (1, 'A'), (2, 'B')")
        con.execute("INSERT INTO us_event_filers VALUES (1, TIMESTAMP '2026-10-01 01:00:00', 'ok', 1, NULL, NULL)")
        self.assertEqual([c for c, *_ in ev._companies_due(con, 5, D(2026, 10, 24))], [2, 1])
        con.close()


    def test_too_few_statement_dates_fails_the_kind_and_leaves_the_table(self):
        path = os.path.join(tempfile.mkdtemp(), "sec.duckdb")
        con = ev._connect(path)
        ev._replace(con, "fomc", [("fomc", D(2026, 1, 28), None, None, None, "FOMC statement", "x", False, None, "Fed", "u")])

        class Thin(object):
            pages = 0

            def get(self, url, **kw):
                return FOMC_PAGE.encode("utf-8")

            def archive(self, kind, name, body):
                return "/dev/null", "0" * 64
        with self.assertRaises(ev.EventsError):
            ev.load_fomc(con, Thin(), log=lambda *a: None, today=D(2026, 10, 23))
        self.assertEqual(con.execute("SELECT count(*) FROM us_events WHERE kind = 'fomc'").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT status FROM us_event_pulls ORDER BY pull_id DESC LIMIT 1").fetchone()[0], "failed")
        con.close()


if __name__ == "__main__":
    unittest.main()
