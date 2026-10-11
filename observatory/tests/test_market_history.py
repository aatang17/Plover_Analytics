"""Private archive durability, immutable imports, publishers and Research access."""
import csv
import gzip
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from app import market_history as mh, research_doc as rd, research_mcp
from tools import import_databento as importer
from tests.test_research_mcp import Base


def source(root, price="70.125000000"):
    root.mkdir(exist_ok=True)
    file = root / "brent.csv.gz"
    rows = [
        ["2026-10-09T09:00:00.000000000Z", "34", "57", "100", price, price, price, price, "12"],
        ["2026-10-09T09:00:00.000000000Z", "34", "84", "100", "69.5", "69.5", "69.5", "69.5", "1"],
        ["2026-10-09T10:00:00.000000000Z", "34", "57", "100", "", "", "", "", "0"],
        ["2026-10-10T09:00:00.000000000Z", "34", "57", "101", "71", "71", "71", "71", "15"],
    ]
    with gzip.open(file, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_event", "rtype", "publisher_id", "instrument_id", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    spans = [{"d0": "2026-10-09", "d1": "2026-10-10", "s": "100"},
             {"d0": "2026-10-10", "d1": "2026-10-11", "s": "101"}]
    receipt = {"filename": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "rows": 4,
               "request": {"dataset": "IFEU.IMPACT", "symbols": "BRN.v.0", "schema": "ohlcv-1h", "stype_in": "continuous",
                           "start": "2026-10-09", "end": "2026-10-11"},
               "contract_mappings": {"rolling_to_id": {"result": {"BRN.v.0": spans}},
                  "id_to_raw_symbol": {"result": {
                      "100": [dict(spans[0], s="BRN Z26")], "101": [dict(spans[1], s="BRN F27")]}}}}
    (root / "brent.json").write_text(json.dumps(receipt))
    product = {"slug": "brent", "dataset": "IFEU.IMPACT", "symbol": "BRN.v.0", "rows": 4,
               "first_bar_utc": rows[0][0], "last_bar_utc": rows[-1][0], "requested_start_utc": "2026-10-09",
               "publisher_rows": {"57": 3, "84": 1}, "missing_ohlc_rows_by_publisher": {"57": 1},
               "contract_count": 2, "original_receipts": ["brent.json"]}
    (root / "inventory.json").write_text(json.dumps({"all_downloads_complete": True,
        "schema": "ohlcv-1h", "end_exclusive_utc": "2026-10-11", "products": [product]}))
    return file


class Archive(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.src = root / "download"
        self.file = source(self.src)
        self.db = root / "history/history.sqlite3"
        self.patch = patch.object(mh, "DB_PATH", self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.person = {"email": "owner@example.com"}
        importer.ingest(self.src, ["OWNER@example.com", "second@example.com"], self.db)

    def test_durable_idempotent_import_and_separate_publishers(self):
        self.assertEqual(importer.ingest(self.src, ["owner@example.com"], self.db)[0]["result"], "already imported")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM bars").fetchone()[0], 4)
            self.assertEqual(c.execute("SELECT count(*) FROM releases").fetchone()[0], 1)
        rows = mh.history(self.person, "brent")["rows"]
        self.assertEqual(rows[0]["close"], "70.125000000")
        self.assertIsNone(rows[1]["close"])
        self.assertEqual(rows[1]["volume"], 0)
        self.assertEqual(mh.history(self.person, "brent", publisher=84)["rows"][0]["close"], "69.5")
        self.file.unlink()
        self.assertEqual(len(mh.history(self.person, "brent")["rows"]), 3)
        self.assertTrue((self.db.parent / "raw" / (rows[0]["artifact_sha"] + ".csv.gz")).exists())

    def test_corrupt_import_publishes_nothing(self):
        before = self.db.read_bytes()
        self.file.write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "checksum"):
            importer.ingest(self.src, ["intruder@example.com"], self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(mh.catalog({"email": "intruder@example.com"}), [])

    def test_revision_keeps_prior_prices(self):
        old = mh.catalog(self.person)[0]["revision"]
        source(self.src, "72.125000000")
        importer.ingest(self.src, ["owner@example.com"], self.db)
        self.assertEqual(mh.history(self.person, "brent")["rows"][0]["close"], "72.125000000")
        self.assertEqual(mh.history(self.person, "brent", revision=old)["rows"][0]["close"], "70.125000000")

    def test_pagination_csv_chart_missing_and_rolls(self):
        first = mh.history(self.person, "brent", limit=1)
        self.assertEqual(first["next_offset"], 1)
        self.assertIsNone(mh.history(self.person, "brent", limit=10)["next_offset"])
        exported = "".join(mh.export(self.person, "brent"))
        self.assertIn("70.125000000", exported)
        self.assertIn("100,BRN Z26,,,,,0,", exported)
        block = mh.chart(self.person, "brent", start="2026-10-09")
        self.assertEqual(block["origin"], "database")
        self.assertIn(["2026-10-09T10:00:00Z", ""], block["rows"])
        self.assertIn(["2026-10-09T11:00:00Z", ""], block["rows"])
        at_roll = [r for r in block["rows"] if r[0] == "2026-10-10T09:00:00Z"]
        self.assertEqual(at_roll, [["2026-10-10T09:00:00Z", ""], ["2026-10-10T09:00:00Z", "71"]])
        cleaned = rd.clean_draft({"blocks": [block]})["blocks"][0]
        self.assertIn("saved Plover data", rd.datachart_source_line(cleaned))
        self.assertEqual(cleaned["rows"], block["rows"])
        with self.assertRaisesRegex(mh.HistoryError, "120 days"):
            mh.chart(self.person, "brent", start="2010-01-01")
        with self.assertRaises(mh.HistoryError):
            mh.history(self.person, "brent", publisher=1)

    def test_private_access_and_health(self):
        for p in (None, {"email": "unrelated@example.com"}, dict(self.person, shared=True)):
            self.assertEqual(mh.catalog(p), [])
            self.assertEqual(mh.tool_schemas(p), [])
            with self.assertRaises(mh.HistoryError):
                mh.history(p, "brent")
        self.assertEqual(len(mh.catalog({"email": "second@example.com"})), 1)
        self.assertEqual(len(mh.catalog(self.person, "crude")), 1)
        self.assertEqual(mh.catalog(self.person, "CPI"), [])
        public = json.dumps(mh.health())
        self.assertNotIn("owner@example.com", public)
        self.assertNotIn("70.125", public)
        self.assertIn('"automatic_updates": false', public)
        moved = self.db.with_suffix(".missing")
        self.db.rename(moved)
        self.assertEqual(mh.health()[0]["status"], "attention")

    def test_csv_stream_survives_worker_changes(self):
        # Starlette resumes a streaming iterator on different pool threads.
        # Keep the cursor open between chunks to reproduce that real path.
        with patch.object(mh, "CSV_CHUNK_BYTES", 1):
            it = mh.export(self.person, "brent")
            parts = []
            with ThreadPoolExecutor(max_workers=1) as a, ThreadPoolExecutor(max_workers=1) as b:
                while True:
                    executor = a if len(parts) % 2 else b
                    try:
                        parts.append(executor.submit(next, it).result())
                    except StopIteration:
                        break
        self.assertIn("70.125000000", "".join(parts))


class ResearchAccess(Base):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        src = root / "download"
        source(src)
        p = patch.object(mh, "DB_PATH", root / "history.sqlite3")
        p.start()
        self.addCleanup(p.stop)
        importer.ingest(src, ["w@example.com"], mh.DB_PATH)
        self.other, _ = self.person("other@example.com", ["writing"])

    def test_api_search_catalog_csv_auth_and_cache(self):
        for route in ("history", "chart", "csv"):
            url = "/admin/api/research/market/brent/" + route
            self.assertEqual(self.w.get(url).status_code, 200)
            self.assertEqual(self.w.get(url).headers["cache-control"], "private, no-store")
            self.assertNotEqual(self.other.get(url).status_code, 200)
            self.assertNotEqual(self.admin.get(url).status_code, 200)
        self.assertEqual(len(self.w.get("/admin/api/research/market").json()["market"]), 1)
        self.assertEqual(self.other.get("/admin/api/research/market").json()["market"], [])
        with patch("app.tools_v2.search", return_value=json.dumps({"series": [], "companies": []})):
            self.assertEqual(len(self.w.get("/admin/api/research/data/search?q=Brent").json()["market"]), 1)
            self.assertEqual(self.other.get("/admin/api/research/data/search?q=Brent").json()["market"], [])

    def test_research_mcp_and_ai_use_personal_grants(self):
        tools = self.mcp("tools/list").json()["result"]["tools"]
        self.assertIn("get_saved_futures", [t["name"] for t in tools])
        rows, err = self.tool("get_saved_futures", slug="brent", limit=1)
        self.assertFalse(err)
        self.assertEqual(rows["rows"][0]["close"], "70.125000000")
        other = {"email": "other@example.com"}
        text, err = research_mcp.call(other, "Test", "get_saved_futures", {"slug": "brent"})
        self.assertTrue(err)
        from app import research_ai
        person = {"id": self.wid, "email": "w@example.com"}
        text, err = research_ai.call_tool({"person": person, "client": "Test", "article_id": 1},
                                         "get_saved_futures", {"slug": "brent"})
        self.assertFalse(err)
        self.assertIn("70.125000000", text)
