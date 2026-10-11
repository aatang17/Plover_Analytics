# -*- coding: utf-8 -*-
"""Data sources: a desk owner's own market-data keys (J-Quants, EODHD), with
the vendors replaced by a canned HTTP function.

What is proved: a key the vendor refuses is never stored; a stored key is
sealed and never returned; one desk cannot see or use another's source;
J-Quants codes, pagination and blank cells are read correctly (blank is
missing, never zero); EODHD's refusals come back as sentences that say what
to do; specialists get the price tool only while a source is switched on, and
a year of prices fits the runner's result limit without being cut mid-row.
"""
import json
import os
import tempfile
import unittest
import urllib.parse

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import accounts  # noqa: E402
from app.assistant import api as aapi  # noqa: E402
from app.assistant import market_data, runner, store  # noqa: E402

JQ_GOOD, EOD_GOOD = "jq-key-good-1234", "eod-key-good-5678"


def _jq_rows(n, start_day=1):
    out = []
    for i in range(n):
        out.append({"Date": "2026-%02d-%02d" % (1 + (i // 28) % 12, 1 + i % 28), "Code": "72030",
                    "O": 3300.0 + i, "H": 3350.5, "L": 3290.0, "C": 3325.0 + i,
                    "AdjC": 3325.0 + i, "Vo": 12345600.0})
    return out


class FakeVendors(object):
    """Answers the URLs market_data.py calls, the way the real services do."""

    def __init__(self):
        self.calls = []
        self.jq_days = 3

    def __call__(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if url.startswith(market_data.JQ_BASE):
            if (headers or {}).get("x-api-key") != JQ_GOOD:
                return 403, b'{"message": "The incoming api key is invalid or expired."}'
            if parts.path.endswith("/equities/master"):
                return 200, json.dumps({"data": [{"Code": "72030", "CoName": "トヨタ自動車"}]}).encode()
            rows = _jq_rows(self.jq_days)
            if q.get("code") != "72030":
                return 200, b'{"data": []}'
            if self.jq_days == 3:
                rows[1]["C"] = ""          # a blank cell: missing, not zero
                rows[1]["AdjC"] = None
            # two pages, the way J-Quants splits a long answer
            half = len(rows) // 2
            if "pagination_key" not in q:
                return 200, json.dumps({"data": rows[:half], "pagination_key": "p2"}).encode()
            return 200, json.dumps({"data": rows[half:]}).encode()
        if url.startswith(market_data.EODHD_BASE):
            if q.get("api_token") != EOD_GOOD:
                return 401, b"Unauthenticated"
            if "/eod/ZZZZ.US" in parts.path:
                return 404, b"Ticker Not Found"
            return 200, json.dumps([
                {"date": "2026-10-08", "open": 330, "high": 332.48, "low": 325.81, "close": 330.32,
                 "adjusted_close": 330.32, "volume": 36306300},
                {"date": "2026-10-09", "open": 333.26, "high": 334.54, "low": 330.61, "close": 333.69,
                 "adjusted_close": 333.69, "volume": 33278600}]).encode()
        return 404, b""


class MarketDataTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "workspace.db")
        accounts.DB_PATH = type(accounts.DB_PATH)(path)
        accounts._conn = None
        store.reset_for_tests(path)
        app = FastAPI()
        app.include_router(accounts.router)
        app.include_router(aapi.router)
        cls.app = app
        db = accounts.conn()
        now = accounts._now()
        ids = []
        for email, tok in (("pm@example.com", "tok"), ("other@example.com", "tok2")):
            db.execute("INSERT INTO accounts (email, created_at) VALUES (?, ?)", (email, now))
            aid = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()[0]
            db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                       (accounts._hash(tok), aid, now, now + 3600))
            ids.append(aid)
        db.commit()
        cls.account_id, cls.other_id = ids
        cls._http = market_data._http

    @classmethod
    def tearDownClass(cls):
        market_data._http = cls._http
        cls.tmp.cleanup()

    def setUp(self):
        db = store.conn()
        db.execute("DELETE FROM ia_data_sources")
        db.commit()
        self.fake = FakeVendors()
        market_data._http = self.fake

    def client(self, tok="tok"):
        c = TestClient(self.app)
        c.cookies.set(accounts.COOKIE, tok)
        return c

    def connect(self, provider="jquants", key=JQ_GOOD, tok="tok"):
        return self.client(tok).post("/api/v1/assistant/data-sources",
                                     json={"provider": provider, "key": key})

    # ---- connecting

    def test_refused_key_is_not_stored(self):
        r = self.connect(key="wrong-key")
        self.assertEqual(r.status_code, 400)
        self.assertIn("refused the key", r.json()["detail"])
        self.assertEqual(self.client().get("/api/v1/assistant/data-sources").json()["sources"], [])

    def test_empty_key_and_unknown_provider(self):
        self.assertEqual(self.connect(key="  ").status_code, 400)
        self.assertEqual(self.connect(provider="bloomberg").status_code, 400)
        self.assertEqual(self.fake.calls, [])

    def test_key_is_sealed_and_never_returned(self):
        r = self.connect()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["last4"], "1234")
        listing = self.client().get("/api/v1/assistant/data-sources").text
        keys = self.client().get("/api/v1/assistant/secrets").text
        for text in (r.text, listing, keys):
            self.assertNotIn(JQ_GOOD, text)
        raw = store.conn().execute("SELECT ct FROM ia_data_sources").fetchone()[0]
        self.assertNotIn(JQ_GOOD, raw)
        self.assertIn("J-Quants", keys)   # the Keys page lists it

    def test_replacing_a_key_keeps_one_row(self):
        self.connect()
        self.connect()
        self.assertEqual(len(self.client().get("/api/v1/assistant/data-sources").json()["sources"]), 1)

    def test_other_desk_cannot_see_or_use_it(self):
        sid = self.connect().json()["id"]
        other = self.client("tok2")
        self.assertEqual(other.get("/api/v1/assistant/data-sources").json()["sources"], [])
        self.assertEqual(other.post("/api/v1/assistant/data-sources/%d/try" % sid,
                                    json={"symbol": "7203"}).status_code, 404)
        self.assertEqual(other.delete("/api/v1/assistant/data-sources/%d" % sid).status_code, 404)
        self.assertEqual(market_data.tool_schemas(self.other_id), [])

    def test_signed_out_is_401(self):
        self.assertEqual(TestClient(self.app).get("/api/v1/assistant/data-sources").status_code, 401)

    # ---- reading prices

    def test_jquants_code_pages_and_blank_cells(self):
        sid = self.connect().json()["id"]
        r = self.client().post("/api/v1/assistant/data-sources/%d/try" % sid, json={"symbol": "7203"})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual([b["date"] for b in out["bars"]], ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertIsNone(out["bars"][1]["close"])
        self.assertIsNone(out["bars"][1]["adj_close"])
        self.assertEqual(out["bars"][0]["volume"], 12345600)
        self.assertIn("J-Quants", out["source"])
        sent = [c for c in self.fake.calls if "/bars/daily" in c[0]]
        self.assertEqual(len(sent), 2)                       # both pages fetched
        self.assertIn("code=72030", sent[0][0])              # 7203 -> five digits
        self.assertNotIn(JQ_GOOD, sent[0][0])                # key in a header, not the URL

    def test_bad_symbol_says_what_to_type(self):
        sid = self.connect().json()["id"]
        r = self.client().post("/api/v1/assistant/data-sources/%d/try" % sid, json={"symbol": "TOYOTA MOTOR"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("such as 7203", r.json()["detail"])
        r = self.client().post("/api/v1/assistant/data-sources/%d/try" % sid, json={"symbol": ""})
        self.assertEqual(r.status_code, 400)

    def test_eodhd_prices_and_refusals(self):
        r = self.connect("eodhd", "nope")
        self.assertEqual(r.status_code, 400)
        self.assertIn("EODHD refused the key", r.json()["detail"])
        sid = self.connect("eodhd", EOD_GOOD).json()["id"]
        out = self.client().post("/api/v1/assistant/data-sources/%d/try" % sid,
                                 json={"symbol": "aapl.us"}).json()
        self.assertEqual(out["symbol"], "AAPL.US")
        self.assertEqual(out["bars"][-1]["close"], 333.69)
        r = self.client().post("/api/v1/assistant/data-sources/%d/try" % sid, json={"symbol": "ZZZZ.US"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no prices for ZZZZ.US", r.json()["detail"])
        src = self.client().get("/api/v1/assistant/data-sources").json()["sources"][0]
        self.assertEqual(src["status"], "error")

    # ---- specialists

    def test_tool_offered_only_while_switched_on(self):
        self.assertEqual(market_data.tool_schemas(self.account_id), [])
        sid = self.connect().json()["id"]
        schemas = market_data.tool_schemas(self.account_id)
        self.assertEqual([s["name"] for s in schemas], ["vendor_prices"])
        self.assertEqual(schemas[0]["parameters"]["properties"]["source"]["enum"], ["jquants"])
        self.client().patch("/api/v1/assistant/data-sources/%d" % sid, json={"enabled": False})
        self.assertEqual(market_data.tool_schemas(self.account_id), [])
        text, err = market_data.run_tool(self.account_id, {"source": "jquants", "symbol": "7203"})
        self.assertTrue(err)

    def test_runner_dispatches_and_year_fits(self):
        self.connect()
        self.fake.jq_days = 260
        hire = store.add_hire(self.account_id, "coverage-monitor", 1)
        run = runner.Run(self.account_id, hire, "test", "prices")
        self.assertIn("vendor_prices", [t["name"] for t in run.market])
        text, err = run._call("vendor_prices", {"source": "jquants", "symbol": "7203",
                                                "start": "2026-01-01", "end": "2026-12-31"})
        self.assertFalse(err, text)
        self.assertLessEqual(len(text), runner.MAX_TOOL_RESULT_CHARS)
        out = json.loads(text)                       # whole, not cut mid-row
        self.assertEqual(out["range"]["days"], 260)
        self.assertEqual(len(out["rows"]), market_data.TOOL_BARS)
        self.assertEqual(out["columns"][0], "date")
        self.assertEqual(out["range"]["last"]["close"], 3325.0 + 259)
        self.assertIn("not Plover data", out["licence"])

    def test_remove(self):
        sid = self.connect().json()["id"]
        self.assertEqual(self.client().delete("/api/v1/assistant/data-sources/%d" % sid).status_code, 200)
        self.assertEqual(self.client().get("/api/v1/assistant/data-sources").json()["sources"], [])


if __name__ == "__main__":
    unittest.main()
