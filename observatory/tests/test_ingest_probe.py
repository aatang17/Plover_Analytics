"""The nightly refresh asks a source whether it changed before copying the database.

ingest --probe fetches, compares with the served file opened read-only and
writes nothing to it; only a new file earns the copy, and that file is then
ingested from the bytes the probe kept, so the source is downloaded once.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_ingest_probe
"""
import datetime
import pathlib
import shutil
import tempfile
import types
import unittest
from unittest import mock

from app import backfill, db, ingest

SLUG = "probe-test"


class _Invalid(Exception):
    pass


def _adapter(payload):
    """A one-series adapter whose source currently serves `payload`."""
    def parse(raw):
        value = float(raw.decode().strip())
        return ([{"code": "x", "name_en": "X", "name_ja": "X", "unit": "index",
                  "weight_per_10000": None, "sort_order": 1}],
                [{"code": "x", "period": datetime.date(2026, 1, 1), "value": value}])
    return types.SimpleNamespace(
        DATASET={"slug": SLUG, "title": "Probe test", "country": "JP", "agency": "Test",
                 "agency_ja": "Test", "base": None, "frequency": "monthly",
                 "description": "Test"},
        SOURCE={"source_id": SLUG, "name": "Test", "name_ja": "Test",
                "url": "https://example.test/", "license_note": "test"},
        DOWNLOAD_URL="https://example.test/file.csv",
        RAW_SUFFIX=".csv",
        ValidationError=_Invalid,
        fetch=lambda: payload["bytes"],
        parse=parse,
        validate=lambda series, obs: {"latest_period": "2026-01-01"},
    )


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        self.source = {"bytes": b"100.0\n"}
        for name, value in (("DB_PATH", self.dir / "observatory.duckdb"),
                            ("RAW_DIR", self.dir / "raw"), ("DATA_DIR", self.dir)):
            patcher = mock.patch.object(db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(ingest.ADAPTERS, {SLUG: _adapter(self.source)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = self.dir / "fetched"
        self.assertEqual(ingest.run(SLUG), 0)

    def _db_state(self):
        st = db.DB_PATH.stat()
        return st.st_mtime_ns, st.st_size

    def test_an_unchanged_source_writes_nothing_anywhere(self):
        before = self._db_state()
        self.assertEqual(ingest.probe(SLUG, str(self.out)), 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(self._db_state(), before)

    def test_a_new_file_is_kept_for_the_ingest(self):
        self.source["bytes"] = b"101.0\n"
        before = self._db_state()
        self.assertEqual(ingest.probe(SLUG, str(self.out)), ingest.CHANGED)
        self.assertEqual(self.out.read_bytes(), b"101.0\n")
        self.assertEqual(self._db_state(), before)

    def test_the_kept_file_publishes_under_the_sources_address(self):
        self.source["bytes"] = b"101.0\n"
        ingest.probe(SLUG, str(self.out))
        self.source["bytes"] = b"999.0\n"   # not fetched again
        self.assertEqual(ingest.run(SLUG, prefetched=str(self.out)), 0)
        con = db.connect(read_only=True)
        try:
            value = con.execute(
                "SELECT o.value FROM observations o JOIN series s USING(series_id) "
                "WHERE s.dataset=?", [SLUG]).fetchone()[0]
            url = con.execute("SELECT url FROM source_artifacts WHERE source_id=? "
                              "ORDER BY retrieved_at DESC LIMIT 1", [SLUG]).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(value, 101.0)
        self.assertEqual(url, "https://example.test/file.csv")

    def test_a_dataset_the_database_never_held_counts_as_changed(self):
        with mock.patch.dict(ingest.ADAPTERS, {"other": _adapter(self.source)}):
            ingest.ADAPTERS["other"].SOURCE = dict(ingest.ADAPTERS["other"].SOURCE,
                                                   source_id="other")
            self.assertEqual(ingest.probe("other", str(self.out)), ingest.CHANGED)

    def test_an_unreadable_database_counts_as_changed(self):
        with mock.patch.object(db, "DB_PATH", self.dir / "missing" / "nope.duckdb"):
            with mock.patch.object(db, "connect", side_effect=OSError("gone")):
                self.assertEqual(ingest.probe(SLUG, str(self.out)), ingest.CHANGED)

    def test_the_exit_code_backfill_expects(self):
        self.assertEqual(backfill.INGEST_CHANGED, ingest.CHANGED)


class BackfillUsesTheProbe(unittest.TestCase):
    """backfill_macro with the child processes and the copy faked."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        self.calls, self.copies, self.checks = [], [], []
        for target, value in (
                ("DATA_DIR", self.dir),
                ("fresh_copy", self._copy),
                ("_published_mark", lambda path, ds: None),
                ("record_check", lambda ds, outcome, detail=None:
                    self.checks.append((ds, outcome))),
                ("_discard", lambda work: None),
                ("swap", lambda work, live: None),
                ("log", lambda msg: None)):
            patcher = mock.patch.object(backfill, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _copy(self, live, work):
        self.copies.append(work)
        return work

    def _children(self, probe_rc):
        def run(cmd, env, cwd):
            self.calls.append(cmd[3:])
            if "--probe" in cmd:
                if probe_rc == backfill.INGEST_CHANGED:
                    pathlib.Path(cmd[cmd.index("--probe") + 1]).write_bytes(b"new")
                return probe_rc
            return 0
        return mock.patch.object(backfill, "_run", run)

    def test_unchanged_takes_no_copy(self):
        with self._children(0):
            backfill.backfill_macro(["cpi-jp", "cpi-us"])
        self.assertEqual(self.copies, [])
        self.assertEqual(self.checks, [("cpi-jp", "unchanged"), ("cpi-us", "unchanged")])
        self.assertTrue(all("--probe" in c for c in self.calls))

    def test_changed_ingests_the_probed_bytes_then_drops_them(self):
        with self._children(backfill.INGEST_CHANGED):
            backfill.backfill_macro(["cpi-jp"])
        self.assertEqual(len(self.copies), 1)
        ingest_call = self.calls[1]
        self.assertIn("--prefetched", ingest_call)
        self.assertFalse(pathlib.Path(ingest_call[-1]).exists())

    def test_a_failed_probe_falls_back_to_the_full_ingest(self):
        with self._children(1):
            backfill.backfill_macro(["cpi-jp"])
        self.assertEqual(len(self.copies), 1)
        self.assertEqual(self.calls[1], ["cpi-jp"])


if __name__ == "__main__":
    unittest.main()
