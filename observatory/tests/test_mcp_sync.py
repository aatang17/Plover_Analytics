# -*- coding: utf-8 -*-
"""The MCP stays in step with the site, and speaks both protocol eras.

Three groups:

  SyncTest       every public GET route under /api/v1 is a view some manifest
                 names (so get_view can read it) or is listed in
                 registry.NOT_DATASETS with a reason; every view reaches its
                 handler; every company view takes the code. No data needed:
                 this runs on every push.
  ModernTest     2026-07-28 requests (no handshake, _meta + headers) and the
                 legacy initialize flow, side by side on one endpoint.
  UsDataTest     the US views an assistant could not reach before 2026-11-04,
                 against the real shelf.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_mcp_sync
"""
import base64
import inspect
import json
import os
import unittest

from fastapi.testclient import TestClient

from app import mcp, registry, tools_v2
from app.main import app
from tests import _data

MODERN = "2026-07-28"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load()
        registry.bind(app)
        cls.client = TestClient(app)

    def setUp(self):
        os.environ["MCP_TOOLSET"] = "both"
        mcp._HITS.clear()

    def tearDown(self):
        os.environ.pop("MCP_TOOLSET", None)


class SyncTest(Base):
    def _gets(self):
        paths = app.openapi()["paths"]
        return sorted(p for p, ops in paths.items() if "get" in ops and p.startswith("/api/v1/"))

    def test_every_public_route_is_a_view_or_says_why_not(self):
        loose = [p for p in self._gets()
                 if not registry.named_by_a_manifest(p) and not registry.not_a_dataset(p)]
        self.assertEqual(loose, [], (
            "These routes are on the site but no manifest names them, so a connected "
            "assistant cannot read them. Add each to its dataset's MANIFEST endpoints "
            "(or a new MANIFEST), or to registry.NOT_DATASETS with the reason it is "
            "not data."))

    def test_every_exemption_still_matches_a_route(self):
        gets = self._gets()
        for prefix in registry.NOT_DATASETS:
            self.assertTrue(any(p == prefix.rstrip("/") or p.startswith(prefix) for p in gets),
                            "%s is in NOT_DATASETS but no route matches it any more" % prefix)

    def test_every_manifest_view_reaches_its_handler(self):
        for mid in registry.ids():
            for role in registry.get(mid)["endpoints"]:
                found = registry.handler(mid, role)
                self.assertIsNotNone(found, "%s/%s has no GET handler" % (mid, role))
                fn, params, _fixed = found
                names = set(inspect.signature(fn).parameters)
                for placeholder, arg in params.items():
                    self.assertIn(arg, names, "%s/%s: {%s} fills %r, which %s does not take"
                                  % (mid, role, placeholder, arg, fn.__name__))

    def test_every_company_view_takes_the_code_where_it_expects_it(self):
        for mid in registry.ids():
            if "company" not in registry.get(mid)["capabilities"]:
                continue
            reader = registry.company_reader(mid)
            self.assertIsNotNone(reader, "%s declares a company view and has no reader" % mid)
            fn, arg = reader
            self.assertIn(arg, inspect.signature(fn).parameters, mid)
        # the bug that hid every 5% holder: company(company=...) was called with sec_code
        self.assertEqual(registry.company_reader("sec-13dg")[1], "company")

    def test_a_helper_named_like_a_capability_is_not_bound(self):
        # sec_text_api.summary(c, adsh) cuts a paragraph; it is not a summary view
        self.assertIsNone(registry.bound("sec-text", "summary"))

    def test_get_view_lists_views_and_refuses_unknown_params(self):
        out = json.loads(tools_v2.get_view("us-pay"))
        self.assertIn("company", [v["view"] for v in out["views"]])
        out = json.loads(tools_v2.get_view("us-pay", "company", {"ticker": "AAPL", "bogus": 1}))
        self.assertIn("bogus", out["error"])
        self.assertIn("get_view", [t["name"] for t in tools_v2.descriptors()])


def _post(client, body, headers=None):
    r = client.post("/mcp", json=body, headers=headers or {})
    return r.status_code, (r.json() if r.content else None)


def _modern(method, params=None, msg_id=1, version=MODERN):
    params = dict(params or {})
    params["_meta"] = {"io.modelcontextprotocol/protocolVersion": version,
                       "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                       "io.modelcontextprotocol/clientCapabilities": {}}
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
    headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    if method == "resources/read":
        headers["Mcp-Name"] = params["uri"]
    return body, headers


class ModernTest(Base):
    def test_discover_names_every_version_and_the_server(self):
        status, body = _post(self.client, *_modern("server/discover"))
        self.assertEqual(status, 200)
        res = body["result"]
        self.assertEqual(res["resultType"], "complete")
        self.assertEqual(res["supportedVersions"][0], MODERN)
        self.assertIn("2025-11-25", res["supportedVersions"])
        self.assertEqual(res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "plover-analytics")
        self.assertTrue(res["instructions"])
        self.assertEqual((res["cacheScope"], res["ttlMs"]), ("public", mcp.LIST_TTL_MS))

    def test_tools_list_and_call_without_a_handshake(self):
        status, body = _post(self.client, *_modern("tools/list"))
        self.assertEqual(status, 200)
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertIn("get_view", names)
        self.assertIn("ttlMs", body["result"])
        status, body = _post(self.client, *_modern("tools/call", {"name": "list_datasets",
                                                                   "arguments": {}}))
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["resultType"], "complete")
        self.assertFalse(body["result"]["isError"])

    def test_headers_must_mirror_the_body(self):
        body, headers = _modern("tools/list")
        headers["Mcp-Method"] = "tools/call"
        status, out = _post(self.client, body, headers)
        self.assertEqual((status, out["error"]["code"]), (400, mcp.HEADER_MISMATCH))
        body, headers = _modern("tools/call", {"name": "list_datasets", "arguments": {}})
        headers.pop("Mcp-Name")
        status, out = _post(self.client, body, headers)
        self.assertEqual((status, out["error"]["code"]), (400, mcp.HEADER_MISMATCH))
        # a modern header over a body without the _meta it must mirror
        status, out = _post(self.client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"})
        self.assertEqual((status, out["error"]["code"]), (400, mcp.HEADER_MISMATCH))

    def test_a_base64_name_header_is_decoded_before_comparing(self):
        body, headers = _modern("tools/call", {"name": "list_datasets", "arguments": {}})
        headers["Mcp-Name"] = "=?base64?%s?=" % base64.b64encode(b"list_datasets").decode()
        status, _out = _post(self.client, body, headers)
        self.assertEqual(status, 200)

    def test_unknown_version_lists_what_is_spoken(self):
        status, out = _post(self.client, *_modern("tools/list", version="2099-01-01"))
        self.assertEqual(status, 400)
        self.assertEqual(out["error"]["code"], mcp.UNSUPPORTED_VERSION)
        self.assertEqual(out["error"]["data"]["requested"], "2099-01-01")
        self.assertIn(MODERN, out["error"]["data"]["supported"])

    def test_removed_methods_are_404_and_notifications_202(self):
        for method in ("ping", "initialize", "prompts/list"):
            status, out = _post(self.client, *_modern(method))
            self.assertEqual((status, out["error"]["code"]), (404, -32601), method)
        body, headers = _modern("notifications/cancelled")
        del body["id"]
        status, out = _post(self.client, body, headers)
        self.assertEqual((status, out), (202, None))

    def test_missing_resource_is_invalid_params_in_the_modern_era(self):
        status, out = _post(self.client, *_modern("resources/read", {"uri": "observatory://nope"}))
        self.assertEqual(out["error"]["code"], -32602)
        _status, legacy = _post(self.client, {"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                                              "params": {"uri": "observatory://nope"}})
        self.assertEqual(legacy["error"]["code"], -32002)

    def test_legacy_clients_still_handshake(self):
        _s, body = _post(self.client, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", body["result"])
        _s, body = _post(self.client, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2030-01-01"}})
        self.assertEqual(body["result"]["protocolVersion"], "2025-11-25")
        # a legacy client after its handshake sends its own version in the header
        status, body = _post(self.client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                             {"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["tools"])
        _s, body = _post(self.client, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(body["result"], {})


@unittest.skipUnless(_data.SEC, "data/sec.duckdb not built")
class UsDataTest(Base):
    def test_a_ticker_opens_the_us_financials(self):
        out = json.loads(tools_v2.get_company("AAPL", "sec-financials"))
        self.assertEqual(out["data"]["cik"], 320193)

    def test_apple_is_apple_inc(self):
        out = json.loads(tools_v2.search("Apple"))
        self.assertEqual(out["companies"][0]["sec_code"], "cik:320193")
        self.assertEqual(out["companies"][0]["ticker"], "AAPL")

    def test_ceo_pay_annual_report_text_and_deep_history_are_views(self):
        if registry.available("us-pay"):
            out = json.loads(tools_v2.get_company("AAPL", "us-pay"))
            self.assertEqual(out["data"]["company"]["ticker"], "AAPL")
            self.assertTrue(out["data"]["years"])
        if registry.available("sec-text"):
            out = json.loads(tools_v2.get_view("sec-text", "section",
                                               {"ticker": "NVDA", "section": "risk_factors"}))
            self.assertNotIn("error", out)
            self.assertIn("/api/v1/us/text/NVDA/section/risk_factors", out["cite"])
        if registry.available("sec-deep"):
            out = json.loads(tools_v2.get_view("sec-deep", "series",
                                               {"ticker": "AAPL", "tag": "Revenues"}))
            self.assertNotIn("error", out)

    def test_a_japanese_code_finds_no_us_filer(self):
        out = json.loads(tools_v2.get_company("7203"))
        us = [d for d in out["coverage"]["present"] if registry.get(d)["section"] == "us-reference"]
        self.assertEqual(us, [])


if __name__ == "__main__":
    unittest.main()
