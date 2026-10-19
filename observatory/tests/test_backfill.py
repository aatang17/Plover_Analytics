"""The backfill's two silent failures of September 2026, pinned down.

Both were the same shape: a guard that looked careful, did nothing visible
wrong, and quietly stopped the pipeline landing anything for six days while
every surface reported health. Neither had a test. These are those tests.
"""
import datetime
import os
import pathlib
import shutil
import tempfile
import unittest

import duckdb

from app import backfill, heartbeat, refresh

UTC = datetime.timezone.utc

# The two columns backfill reads, and the supersede-on-publish behaviour that
# made counting rows useless. Deliberately a hand-built miniature rather than
# the real schema: the point under test is how publishing MOVES, and the real
# table brings a dozen foreign keys that have nothing to do with it.
SCHEMA = """
CREATE SEQUENCE seq_release START 1;
CREATE TABLE releases (
    release_id    BIGINT PRIMARY KEY DEFAULT nextval('seq_release'),
    dataset       TEXT NOT NULL,
    latest_period DATE NOT NULL,
    status        TEXT NOT NULL
);
"""


def publish(path, dataset, period):
    """Publish a release the way ingest.py does: supersede, then insert."""
    con = duckdb.connect(str(path))
    try:
        con.execute("BEGIN")
        con.execute("UPDATE releases SET status='superseded' "
                    "WHERE dataset=? AND status='published'", [dataset])
        con.execute("INSERT INTO releases (dataset, latest_period, status) "
                    "VALUES (?,?,'published')", [dataset, period])
        con.execute("COMMIT")
    finally:
        con.close()


class PublishedMarkTest(unittest.TestCase):
    """The bug that served July CPI through six days of August CPI ingests."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.db = self.dir / "observatory.duckdb"
        con = duckdb.connect(str(self.db))
        con.execute(SCHEMA)
        con.close()
        self.addCleanup(shutil.rmtree, str(self.dir), True)

    def _published_count(self):
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            return con.execute("SELECT count(*) FROM releases "
                               "WHERE status='published'").fetchone()[0]
        finally:
            con.close()

    def test_a_republish_moves_the_mark(self):
        publish(self.db, "cpi-jp", datetime.date(2026, 7, 1))
        before = backfill._published_mark(self.db, "cpi-jp")
        publish(self.db, "cpi-jp", datetime.date(2026, 8, 1))
        after = backfill._published_mark(self.db, "cpi-jp")
        self.assertIsNotNone(before)
        self.assertNotEqual(before, after, "a new release must read as a change")

    def test_counting_published_rows_cannot_see_a_republish(self):
        """Why the old test failed: the count is one per dataset, forever.

        This is the actual defect, asserted directly so nobody reintroduces
        the cheaper-looking check.
        """
        publish(self.db, "cpi-jp", datetime.date(2026, 7, 1))
        before = self._published_count()
        publish(self.db, "cpi-jp", datetime.date(2026, 8, 1))
        self.assertEqual(before, self._published_count())

    def test_first_publish_is_a_change_from_nothing(self):
        self.assertIsNone(backfill._published_mark(self.db, "cpi-jp"))
        publish(self.db, "cpi-jp", datetime.date(2026, 7, 1))
        self.assertIsNotNone(backfill._published_mark(self.db, "cpi-jp"))

    def test_one_dataset_publishing_does_not_look_like_another_moving(self):
        publish(self.db, "cpi-jp", datetime.date(2026, 7, 1))
        before = backfill._published_mark(self.db, "cpi-jp")
        publish(self.db, "jgb-yields", datetime.date(2026, 9, 18))
        self.assertEqual(before, backfill._published_mark(self.db, "cpi-jp"))


class FreshCopyTest(unittest.TestCase):
    """The write-ahead log that froze all eleven EDINET extractors."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.live = self.dir / "equity.duckdb"
        self.work = self.dir / "equity.backfill.duckdb"
        self.addCleanup(shutil.rmtree, str(self.dir), True)

    def _live_with_uncheckpointed_rows(self):
        """A database whose newest rows are only in its .wal.

        A normal close checkpoints, so the pragma is how a killed writer's
        state is reproduced deliberately — the extractor that died on
        2026-09-13 left exactly this pair behind.
        """
        con = duckdb.connect(str(self.live))
        con.execute("CREATE TABLE t (n INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.execute("CHECKPOINT")
        con.execute("PRAGMA disable_checkpoint_on_shutdown")
        con.execute("INSERT INTO t VALUES (2)")
        con.close()
        wal = self.live.with_name(self.live.name + ".wal")
        assert wal.exists() and wal.stat().st_size > 0, "no write-ahead log to test"

    def test_copies_a_database_with_no_log(self):
        con = duckdb.connect(str(self.live))
        con.execute("CREATE TABLE t (n INTEGER)")
        con.close()
        self.assertEqual(backfill.fresh_copy(self.live, self.work), self.work)
        self.assertTrue(self.work.exists())

    def test_a_log_no_longer_refuses_the_copy(self):
        """The deadlock: nothing in the running system can clear that log.

        The API holds the served file open read-only and guardrail 5 forbids
        it writing, so refusing meant refusing forever.
        """
        self._live_with_uncheckpointed_rows()
        self.assertEqual(backfill.fresh_copy(self.live, self.work), self.work)

    def test_the_logs_rows_survive_into_the_copy(self):
        """Copying the main file alone would silently drop them."""
        self._live_with_uncheckpointed_rows()
        backfill.fresh_copy(self.live, self.work)
        con = duckdb.connect(str(self.work), read_only=True)
        try:
            rows = {r[0] for r in con.execute("SELECT n FROM t").fetchall()}
        finally:
            con.close()
        self.assertEqual(rows, {1, 2})

    def test_missing_file_is_refused(self):
        self.assertIsNone(backfill.fresh_copy(self.live, self.work))


class FullVolumeTest(unittest.TestCase):
    """The full volume that killed the refresh on 2026-09-17 and 2026-09-20.

    A swap frees the old file's space only once the server lets go of it, and
    the next copy starts immediately; the OSError that followed ended the whole
    process. A full disk must be waited out, and failing that, skipped — never
    raised.
    """

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.live = self.dir / "observatory.duckdb"
        self.work = self.dir / "observatory.backfill.duckdb"
        con = duckdb.connect(str(self.live))
        con.execute("CREATE TABLE t (n INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.close()
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        self._waits = backfill.COPY_RETRY_WAITS
        backfill.COPY_RETRY_WAITS = (0.01, 0.01, 0.01)
        self.addCleanup(setattr, backfill, "COPY_RETRY_WAITS", self._waits)
        self._copy = shutil.copyfile
        self.addCleanup(setattr, shutil, "copyfile", self._copy)

    def _fail(self, times, err=None):
        real, calls = self._copy, {"n": 0}
        err = err if err is not None else errno_enospc()

        def copyfile(src, dst, *a, **k):
            calls["n"] += 1
            if calls["n"] <= times:
                with open(dst, "wb") as fh:          # a partial copy is left
                    fh.write(b"partial")
                raise err
            return real(src, dst, *a, **k)
        shutil.copyfile = copyfile
        return calls

    def test_space_that_comes_back_is_waited_for(self):
        calls = self._fail(2)
        self.assertEqual(backfill.fresh_copy(self.live, self.work), self.work)
        self.assertEqual(calls["n"], 3)
        con = duckdb.connect(str(self.work), read_only=True)
        self.assertEqual(con.execute("SELECT n FROM t").fetchall(), [(1,)])
        con.close()

    def test_space_that_never_comes_back_is_skipped_not_raised(self):
        self._fail(99)
        self.assertIsNone(backfill.fresh_copy(self.live, self.work))
        self.assertFalse(self.work.exists(), "a partial copy was left on the volume")

    def test_other_copy_errors_are_refused_without_waiting(self):
        calls = self._fail(99, err=OSError(13, "Permission denied"))
        self.assertIsNone(backfill.fresh_copy(self.live, self.work))
        self.assertEqual(calls["n"], 1)
        self.assertTrue(self.live.exists())


def errno_enospc():
    import errno
    return OSError(errno.ENOSPC, "No space left on device")


class SwapTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.live = self.dir / "equity.duckdb"
        self.work = self.dir / "equity.backfill.duckdb"
        self.addCleanup(shutil.rmtree, str(self.dir), True)

    def test_a_stale_log_beside_the_served_file_is_dropped(self):
        """A log left next to a file it no longer matches is replayed into it
        by the next reader — the served database, corrupted."""
        for path in (self.live, self.work):
            con = duckdb.connect(str(path))
            con.execute("CREATE TABLE t (n INTEGER)")
            con.close()
        stale = self.live.with_name(self.live.name + ".wal")
        stale.write_bytes(b"not this file's log")
        backfill.swap(self.work, self.live)
        self.assertFalse(stale.exists())
        self.assertTrue(self.live.exists())


class JournalTest(unittest.TestCase):
    """Per-dataset contact: did the pipeline REACH this, not did data move."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self._real = heartbeat.JOURNAL_PATH
        heartbeat.JOURNAL_PATH = self.dir / "refresh_journal.json"
        self.addCleanup(setattr, heartbeat, "JOURNAL_PATH", self._real)
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        os.environ.pop("REFRESH_CHECK_MAX_AGE_HOURS", None)

    def test_unknown_dataset_is_unknown_not_healthy_and_not_a_fault(self):
        """Every dataset is unknown until the first cycle after this ships.

        Forty-three faults on the first boot would be as useless as none.
        """
        status = heartbeat.check_status("cpi-jp")
        self.assertIsNone(status["checked_overdue"])
        self.assertIsNone(status["last_checked_at"])

    def test_a_recent_check_is_not_overdue(self):
        heartbeat.record("cpi-jp", "unchanged")
        self.assertFalse(heartbeat.check_status("cpi-jp")["checked_overdue"])

    def test_unchanged_counts_as_contact(self):
        """The source having nothing new is not the pipeline failing. CPI is
        monthly; an alarm that fired on 'no new data today' would be noise."""
        heartbeat.record("cpi-jp", "unchanged")
        self.assertEqual(heartbeat.check_status("cpi-jp")["last_check_outcome"],
                         "unchanged")

    def test_a_day_without_contact_is_overdue(self):
        heartbeat.record("cpi-jp", "published",
                         now=datetime.datetime(2026, 9, 14, 13, tzinfo=UTC))
        status = heartbeat.check_status(
            "cpi-jp", now=datetime.datetime(2026, 9, 19, 13, tzinfo=UTC))
        self.assertTrue(status["checked_overdue"])

    def test_each_dataset_keeps_its_own_entry(self):
        heartbeat.record("cpi-jp", "published", "release 65")
        heartbeat.record("jgb-yields", "failed", "ingest exited 1")
        self.assertEqual(heartbeat.check_status("cpi-jp")["last_check_outcome"],
                         "published")
        self.assertEqual(heartbeat.check_status("jgb-yields")["last_check_outcome"],
                         "failed")

    def test_recording_never_raises(self):
        """Bookkeeping for the alarm must not break the refresh it watches."""
        heartbeat.JOURNAL_PATH = pathlib.Path("/nonexistent-dir/journal.json")
        heartbeat.record("cpi-jp", "published")  # must not raise


class ProblemsTest(unittest.TestCase):
    """What the watch actually turns into an alarm."""

    def test_an_unreached_dataset_is_a_problem_even_when_its_data_looks_fine(self):
        """The exact September failure: fresh-looking data, dead pipeline."""
        found = refresh.problems({"datasets": [{
            "dataset": "cpi-jp", "published": True, "stale": False,
            "checked_overdue": True, "hours_since_checked": 140.0,
            "check_max_age_hours": 26.0,
        }]})
        self.assertIn("cpi-jp:unchecked", [k for k, _ in found])

    def test_a_failing_ingest_is_reported_immediately(self):
        found = refresh.problems({"datasets": [{
            "dataset": "ust-yields", "published": True, "stale": False,
            "checked_overdue": False, "last_check_outcome": "failed",
            "last_check_detail": "ingest exited 1",
        }]})
        self.assertIn("ust-yields:failing", [k for k, _ in found])

    def test_a_dataset_the_journal_has_never_seen_is_not_a_problem(self):
        found = refresh.problems({"datasets": [{
            "dataset": "cpi-jp", "published": True, "stale": False,
            "checked_overdue": None, "last_check_outcome": None,
        }]})
        self.assertEqual(found, [])

    def test_a_frozen_equity_extractor_is_a_problem(self):
        """problems() ignored this half of the report entirely until
        2026-09-20, which is why eleven frozen extractors alarmed nobody."""
        old = (datetime.datetime.now(UTC) - datetime.timedelta(days=7))
        found = refresh.problems({"equity_extractors": [{
            "dataset": "5pct-filings", "stale": True,
            "archive_read_through": "2026-09-11", "days_behind": 8,
            "stale_after_days": 7,
            "last_extracted_at": old.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }]})
        keys = [k for k, _ in found]
        self.assertIn("equity/5pct-filings:unchecked", keys)
        self.assertIn("equity/5pct-filings:stale", keys)

    def test_a_running_equity_extractor_is_not_a_problem(self):
        now = datetime.datetime.now(UTC)
        found = refresh.problems({"equity_extractors": [{
            "dataset": "5pct-filings", "stale": False,
            "last_extracted_at": now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }]})
        self.assertEqual(found, [])

    def test_an_unreadable_stamp_neither_raises_nor_alarms(self):
        self.assertIsNone(refresh._age_hours("not a timestamp"))
        self.assertIsNone(refresh._age_hours(None))


class DeliveryTest(unittest.TestCase):
    """Whether an alarm can leave the building at all."""

    def setUp(self):
        for key in ("ALERT_WEBHOOK_URL", "ALERT_EMAIL_TO",
                    "RESEND_API_KEY", "RESEND_FROM"):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

    def test_nothing_configured_reports_undeliverable(self):
        """Production's actual state for the whole outage."""
        self.assertFalse(refresh.delivery()["alerts_deliverable"])

    def test_an_address_without_a_mail_key_is_still_undeliverable(self):
        """The trap: an address set, no credential, and a false sense of cover."""
        os.environ["ALERT_EMAIL_TO"] = "someone@example.com"
        self.assertFalse(refresh.delivery()["alerts_deliverable"])

    def test_address_plus_credential_is_deliverable(self):
        os.environ["ALERT_EMAIL_TO"] = "someone@example.com"
        os.environ["RESEND_API_KEY"] = "re_test"
        os.environ["RESEND_FROM"] = "alerts@example.com"
        self.assertEqual(refresh.delivery()["alert_channels"], ["email"])


if __name__ == "__main__":
    unittest.main()


class TelegramTest(unittest.TestCase):
    """The Telegram channel: configured only as a pair, and never one refused message."""

    def setUp(self):
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALERT_WEBHOOK_URL",
                    "ALERT_EMAIL_TO", "RESEND_API_KEY", "RESEND_FROM"):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)
        self._post = refresh._post
        self.addCleanup(setattr, refresh, "_post", self._post)
        self.sent = []
        refresh._post = lambda url, payload: self.sent.append((url, payload))

    def test_token_without_chat_is_not_a_channel(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
        self.assertFalse(refresh.delivery()["alerts_deliverable"])

    def test_token_and_chat_is_a_channel(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
        os.environ["TELEGRAM_CHAT_ID"] = "42"
        self.assertEqual(refresh.delivery()["alert_channels"], ["telegram"])

    def test_alarm_reaches_every_chat_as_plain_text(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
        os.environ["TELEGRAM_CHAT_ID"] = "42, 43"
        refresh._last_alert.clear()
        refresh.alert([("cpi-jp:unchecked", "cpi_jp [odd] name has not been refreshed")])
        self.assertEqual([p["chat_id"] for _, p in self.sent], ["42", "43"])
        self.assertTrue(all(u.endswith("/bot123:abc/sendMessage") for u, _ in self.sent))
        self.assertTrue(all("parse_mode" not in p for _, p in self.sent))

    def test_a_long_alarm_is_split_under_telegrams_limit(self):
        text = "\n".join("• dataset-%03d has not been refreshed for 144 hours" % i
                         for i in range(300))
        pieces = refresh._chunks(text)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= refresh.TELEGRAM_LIMIT for p in pieces))
        self.assertEqual("\n".join(pieces), text)

    def test_a_telegram_failure_never_raises(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"
        os.environ["TELEGRAM_CHAT_ID"] = "42"

        def boom(url, payload):
            raise OSError("network down")
        refresh._post = boom
        refresh._send_telegram("hello")          # must not raise


class PhaseIsolationTest(unittest.TestCase):
    """The NameError of 2026-09-21: one phase crashing took every later one down."""

    def setUp(self):
        # every phase is stubbed: an unstubbed one runs for real against data/ and the
        # SEC (2026-10-19: a test run pulled 400 filings and swapped data/sec.duckdb)
        self._saved = {n: getattr(backfill, n) for n in (
            "backfill_macro", "stamp_cycle", "backfill_gdp_vintages",
            "backfill_equity", "backfill_sec", "refresh_us_companies", "refresh_us_pay",
            "refresh_us_holdings", "refresh_us_prices", "refresh_us_shares", "refresh_us_listings",
            "refresh_us_text")}
        self.addCleanup(lambda: [setattr(backfill, n, f) for n, f in self._saved.items()])
        # a phase added to main() without a stub here runs for real (2026-10-19:
        # refresh_us_text swapped a half-copied data/sec_text.duckdb over the live one)
        import inspect
        import re
        phases = set(re.findall(r"_phase\(\s*\"[^\"]+\",\s*(\w+)", inspect.getsource(backfill.main)))
        missing = phases - set(self._saved)
        if missing:
            self.fail("backfill.main() runs phases this test does not stub: %s" % ", ".join(sorted(missing)))
        self.ran = []
        backfill.backfill_macro = lambda ds: self.ran.append("macro")
        backfill.stamp_cycle = lambda: self.ran.append("stamp")
        backfill.refresh_us_companies = lambda: self.ran.append("us")
        backfill.refresh_us_pay = lambda: self.ran.append("pay")
        backfill.refresh_us_text = lambda: self.ran.append("text")
        backfill.refresh_us_holdings = lambda: self.ran.append("holdings")
        backfill.refresh_us_prices = lambda: self.ran.append("prices")
        backfill.refresh_us_shares = lambda: self.ran.append("shares")
        backfill.refresh_us_listings = lambda: self.ran.append("listings")
        backfill.backfill_equity = lambda d, m: self.ran.append("equity")
        backfill.backfill_sec = lambda q: self.ran.append("sec")
        for key in ("BACKFILL_DATASETS", "BACKFILL_GDP_VINTAGES",
                    "BACKFILL_CATCH_UP_DAYS", "BACKFILL_SEC_QUARTERS"):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)
        backfill._stopping = False

    def test_a_crashing_phase_does_not_stop_the_rest(self):
        def boom():
            raise NameError("name '_release_count' is not defined")
        backfill.backfill_gdp_vintages = boom
        self.assertEqual(backfill.main(), 0)
        self.assertIn("equity", self.ran)
        self.assertIn("sec", self.ran)

    def test_us_companies_run_before_the_long_history_phases(self):
        """The US pull is current data and takes a minute; the equity catch-up
        can run until the daily restart and would starve it."""
        backfill.backfill_gdp_vintages = lambda: None
        backfill.main()
        self.assertLess(self.ran.index("us"), self.ran.index("equity"))

    def test_us_pay_runs_before_the_long_history_phases(self):
        backfill.backfill_gdp_vintages = lambda: None
        backfill.main()
        self.assertLess(self.ran.index("pay"), self.ran.index("equity"))

    def test_us_companies_can_be_switched_off(self):
        os.environ["BACKFILL_US_COMPANIES"] = "0"
        self.addCleanup(os.environ.pop, "BACKFILL_US_COMPANIES", None)
        backfill.backfill_gdp_vintages = lambda: None
        backfill.main()
        self.assertNotIn("us", self.ran)

    def test_listings_follow_share_counts_before_history(self):
        backfill.backfill_gdp_vintages = lambda: None
        backfill.main()
        self.assertLess(self.ran.index("shares"), self.ran.index("listings"))
        self.assertLess(self.ran.index("listings"), self.ran.index("equity"))

    def test_equity_runs_before_the_gdp_archive(self):
        backfill.backfill_gdp_vintages = lambda: self.ran.append("gdp")
        backfill.main()
        self.assertLess(self.ran.index("equity"), self.ran.index("gdp"))


class GdpVintageCountTest(unittest.TestCase):
    """The loader's measure must see 'archived' releases, which never publish."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.db = self.dir / "observatory.duckdb"
        con = duckdb.connect(str(self.db))
        con.execute(SCHEMA)
        con.close()
        self.addCleanup(shutil.rmtree, str(self.dir), True)

    def test_an_archived_release_counts(self):
        publish(self.db, "gdp-jp", datetime.date(2026, 4, 1))
        before = backfill._release_total(self.db, "gdp-jp")
        con = duckdb.connect(str(self.db))
        con.execute("INSERT INTO releases (dataset, latest_period, status) "
                    "VALUES ('gdp-jp', DATE '2003-01-01', 'archived')")
        con.close()
        self.assertEqual(backfill._release_total(self.db, "gdp-jp"), before + 1)

    def test_other_datasets_do_not_count(self):
        publish(self.db, "cpi-jp", datetime.date(2026, 8, 1))
        self.assertEqual(backfill._release_total(self.db, "gdp-jp"), 0)

    def test_the_gdp_loader_runs_without_a_name_error(self):
        """Exercise the real function end to end, with the child process stubbed.

        This is the test that would have caught the rename: the function was
        never called by any test, so a name it used could vanish unnoticed.
        """
        live, saved = backfill.LIVE_MACRO, backfill._run
        self.addCleanup(setattr, backfill, "LIVE_MACRO", live)
        self.addCleanup(setattr, backfill, "_run", saved)
        self.addCleanup(setattr, backfill, "DATA_DIR", backfill.DATA_DIR)
        backfill.LIVE_MACRO = self.db
        backfill.DATA_DIR = self.dir
        backfill._run = lambda cmd, env, cwd: 0       # loader found nothing new
        backfill.backfill_gdp_vintages(max_slices=1)   # must not raise
