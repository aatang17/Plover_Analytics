# -*- coding: utf-8 -*-
"""The MCP v2 surface: six generic tools, manifest resources, the toolset flag,
and parity with the v1 tools they replace.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_mcp_v2
Needs both databases under data/ (the equity checks skip without the file).
"""
import json
import os
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import equity_api, lvh_api, mcp, registry, tools, tools_v2
from app.main import app
from tests import _data

EQUITY = equity_api.DB_PATH.exists()


def rpc(client, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    r = client.post("/mcp", json=body)
    return r.status_code, r.json()


def call(client, name, **arguments):
    status, body = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert status == 200, body
    if "error" in body:
        return body["error"], True
    res = body["result"]
    return json.loads(res["content"][0]["text"]), res["isError"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load()
        registry.bind(app)
        cls.client = TestClient(app)

    def setUp(self):
        os.environ["MCP_TOOLSET"] = "both"
        # The contract test alone makes more calls than the per-IP limit allows
        # in a minute; the limiter is not what is under test here.
        mcp._HITS.clear()

    def tearDown(self):
        os.environ.pop("MCP_TOOLSET", None)


class ProtocolTest(Base):
    def test_openai_domain_challenge_is_the_bare_token(self):
        path = "/.well-known/openai-apps-challenge"
        os.environ.pop("OPENAI_APPS_CHALLENGE", None)
        if not mcp.CHALLENGE_FILE.exists():
            self.assertEqual(self.client.get(path).status_code, 404)
        # the environment wins over the checked-in file
        os.environ["OPENAI_APPS_CHALLENGE"] = " tok-123\n"
        try:
            r = self.client.get(path)
        finally:
            os.environ.pop("OPENAI_APPS_CHALLENGE", None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "tok-123")
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))

    def test_initialize_instructions_name_every_available_dataset(self):
        os.environ["MCP_TOOLSET"] = "v2"
        _, body = rpc(self.client, "initialize", {"protocolVersion": "2025-06-18"})
        text = body["result"]["instructions"]
        for mid in registry.ids():
            if registry.available(mid):
                self.assertIn(mid, text)
        self.assertIn("resources", body["result"]["capabilities"])
        self.assertIn("calc", text)

    @unittest.skipUnless(_data.MACRO and _data.EQUITY, _data.NO_BOTH)
    def test_toolset_flag_controls_the_list(self):
        counts = {}
        for ts in ("v1", "v2", "both"):
            os.environ["MCP_TOOLSET"] = ts
            _, body = rpc(self.client, "tools/list")
            counts[ts] = [t["name"] for t in body["result"]["tools"]]
        # v2 advertises exactly what it implements — the generic tools over
        # the registry plus the cohort pair, which are not per-dataset.
        self.assertEqual(sorted(counts["v2"]), sorted(tools_v2.IMPLS))
        self.assertNotIn("get_series", counts["v1"])
        # `both` is v2 plus only the v1 tools v2 cannot answer. The rest are
        # still DISPATCHED (see test_superseded_v1_names_still_answer) but
        # not advertised: a catalogue with a dozen spellings of the same
        # question costs the assistant accuracy on every call.
        self.assertEqual(set(counts["both"]),
                         set(counts["v2"]) | (set(counts["v1"]) - mcp.SUPERSEDED_BY_V2))
        self.assertTrue(set(counts["both"]) < set(counts["v1"]) | set(counts["v2"]))
        self.assertIn("check_claim", counts["both"])   # the one with no v2 equivalent
        # A name the surfaces share is advertised once. Duplicates in
        # tools/list make a client shadow or reject an entry, and the two
        # descriptors differ, so the wrong one can win.
        for ts in ("v1", "v2", "both"):
            self.assertEqual(len(counts[ts]), len(set(counts[ts])),
                             "duplicate tool name advertised under %s" % ts)
        self.assertIn("list_datasets", set(counts["v1"]) & set(counts["v2"]))

    @unittest.skipUnless(_data.MACRO and _data.EQUITY, _data.NO_BOTH)
    def test_superseded_v1_names_still_answer(self):
        """Dropped from the catalogue, kept on the wire.

        A connector that cached the old tool list must not break because the
        listing got shorter, so every superseded name still dispatches under
        `both` — it is only no longer advertised.
        """
        os.environ["MCP_TOOLSET"] = "both"
        advertised = set(t["name"] for t in mcp._tools_for("both"))
        for name in ("get_yield_curve", "get_boj_balance_sheet"):
            self.assertNotIn(name, advertised)
            data, is_err = call(self.client, name)
            self.assertFalse(is_err, data)

    @unittest.skipUnless(_data.MACRO and _data.EQUITY, _data.NO_BOTH)
    def test_v1_tools_refused_under_v2_and_vice_versa(self):
        os.environ["MCP_TOOLSET"] = "v2"
        # A v1-only name — get_overview is a v2 tool now, generic over every
        # series dataset rather than cpi-jp alone.
        err, _ = call(self.client, "get_boj_balance_sheet")
        self.assertEqual(err["code"], -32602)
        os.environ["MCP_TOOLSET"] = "v1"
        data, is_err = call(self.client, "list_datasets")  # a v1 name too — routes to v1
        self.assertFalse(is_err)
        self.assertIn("datasets", data)
        self.assertNotIn("tool", data)  # v1's shape, not the v2 envelope
        err, _ = call(self.client, "get_series", dataset="cpi-jp", series="0001")
        self.assertEqual(err["code"], -32602)

    def test_resources_list_and_read(self):
        _, body = rpc(self.client, "resources/list")
        uris = [r["uri"] for r in body["result"]["resources"]]
        self.assertEqual(len(uris), len(registry.ids()) + 2)
        for mid in registry.ids():
            self.assertIn("observatory://datasets/%s" % mid, uris)
        _, body = rpc(self.client, "resources/read", {"uri": "observatory://datasets/cpi-jp"})
        item = body["result"]["contents"][0]
        self.assertEqual(item["mimeType"], "application/json")
        self.assertEqual(json.loads(item["text"])["id"], "cpi-jp")
        _, body = rpc(self.client, "resources/read", {"uri": "observatory://sections"})
        self.assertIn("sections", json.loads(body["result"]["contents"][0]["text"]))
        _, body = rpc(self.client, "resources/read", {"uri": "observatory://nope"})
        self.assertEqual(body["error"]["code"], -32002)

    def test_descriptor_enums_are_the_live_ids(self):
        # Generated from the registry, never hardcoded — and narrowed to the
        # datasets this deployment can actually answer for. A registered
        # manifest with no data here (the US shelf without the SEC quarter
        # files) used to sit in every enum and reply "not published yet".
        d = dict((t["name"], t) for t in tools_v2.descriptors())
        live = [i for i in registry.ids() if registry.available(i)]
        self.assertEqual(d["describe_dataset"]["inputSchema"]["properties"]["dataset"]["enum"],
                         live)
        for t in d.values():
            enum = t["inputSchema"]["properties"].get("dataset", {}).get("enum")
            if enum:
                self.assertTrue(set(enum) <= set(live))
        for t in d.values():
            self.assertTrue(t["annotations"]["readOnlyHint"])
            # the ChatGPT directory needs all three hints as explicit booleans
            self.assertIs(t["annotations"]["destructiveHint"], False)
            self.assertIs(t["annotations"]["openWorldHint"], False)


class ToolTest(Base):
    def test_list_datasets(self):
        data, err = call(self.client, "list_datasets")
        self.assertFalse(err)
        self.assertEqual(data["count"], len(registry.ids()))
        self.assertTrue(all("capabilities" in d for d in data["datasets"]))
        data, err = call(self.client, "list_datasets", section="prices")
        self.assertEqual([d["id"] for d in data["datasets"]],
                         ["cpi-hk", "cpi-hk-rates",
                          "cpi-jp", "cpi-jp-goods-services", "cpi-jp-items", "cpi-jp-long",
                          "cpi-jp-sa", "cpi-tokyo", "cpi-tokyo-items",
                          "cpi-us", "cpi-us-areas", "cpi-us-sa", "cpi-us-weights",
                          "us-avg-prices", "us-wage-tracker", "us-wages"])
        data, err = call(self.client, "list_datasets", section="weather")
        self.assertTrue(err)
        self.assertIn("prices", data["error"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_describe_dataset(self):
        data, err = call(self.client, "describe_dataset", dataset="cpi-jp")
        self.assertFalse(err)
        self.assertEqual(data["id"], "cpi-jp")
        self.assertTrue(data["tools"]["get_series"])
        self.assertIn("to", data["coverage"])
        data, err = call(self.client, "describe_dataset", dataset="nope")
        self.assertTrue(err)
        self.assertIn("cpi-jp", data["error"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_series_matches_v1(self):
        data, err = call(self.client, "get_series", dataset="cpi-jp", series="0001",
                         measure="yoy")
        self.assertFalse(err, data)
        for key in ("tool", "dataset", "data", "provenance", "calc", "vintage", "cite",
                    "coverage"):
            self.assertIn(key, data)
        self.assertEqual(data["data"]["trust"], "derived")
        self.assertIn("yoy", data["calc"])
        v1 = json.loads(tools.get_series_values("0001", measure="yoy", dataset="cpi-jp"))
        self.assertEqual(data["data"]["series"][0]["points"][-1],
                         v1["series"][0]["points"][-1])
        self.assertEqual(data["vintage"]["latest_period"], v1["as_of_release"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_series_takes_a_list_of_codes(self):
        # a client may send a JSON list for the comma-separated codes
        listed, err = call(self.client, "get_series", dataset="cpi-jp",
                           series=["0001", "0161"], measure="yoy")
        self.assertFalse(err, listed)
        joined, _ = call(self.client, "get_series", dataset="cpi-jp",
                         series="0001,0161", measure="yoy")
        self.assertEqual(listed["data"]["series"], joined["data"]["series"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_series_as_of_and_refusals(self):
        data, err = call(self.client, "get_series", dataset="cpi-jp", series="0001",
                         measure="index", as_of="2026-08-20")
        self.assertFalse(err, data)
        self.assertEqual(data["vintage"]["as_of"], "2026-08-20")
        data, err = call(self.client, "get_series", dataset="cpi-jp", series="0001",
                         as_of="yesterday")
        self.assertTrue(err)
        data, err = call(self.client, "get_series", dataset="jgb-yields", series="10Y",
                         measure="yoy")
        self.assertTrue(err)  # daily dataset serves no monthly rates
        data, err = call(self.client, "get_series", dataset="cross-shareholdings",
                         series="x")
        self.assertTrue(err)
        self.assertIn("get_company", data["error"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_search_series(self):
        data, err = call(self.client, "search", query="electricity", dataset="cpi-jp")
        self.assertFalse(err)
        self.assertTrue(data["series"])
        self.assertEqual(data["series"][0]["dataset"], "cpi-jp")

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_search_by_dataset_name_and_common_label(self):
        # Series are named "All items", never "CPI": the word lives in the
        # dataset name and the page's labels, and a search must still find it.
        data, err = call(self.client, "search", query="CPI")
        self.assertFalse(err)
        self.assertEqual((data["series"][0]["dataset"], data["series"][0]["name_en"]),
                         ("cpi-jp", "All items"))
        data, err = call(self.client, "search", query="core CPI", dataset="cpi-jp")
        self.assertEqual(data["series"][0]["name_en"], "All items, less fresh food")
        data, err = call(self.client, "search", query="food CPI", dataset="cpi-jp")
        self.assertEqual(data["series"][0]["name_en"], "Food")

    @unittest.skipUnless(_data.EQUITY, _data.NO_EQUITY)
    def test_screen_rejects_unknown_sort_with_the_valid_list(self):
        # A company dataset answers with its own screen ids.
        data, err = call(self.client, "screen", dataset="cross-shareholdings",
                         sort="not-a-screen")
        self.assertTrue(err)
        self.assertIn("Unknown sort", data["error"])
        # A series dataset is ranked on the columns its listing publishes,
        # and an unknown one answers with those.
        data, err = call(self.client, "screen", dataset="cpi-jp", sort="not-a-column")
        self.assertTrue(err)
        self.assertIn("Unknown sort", data["error"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_screen_ranks_a_series_dataset(self):
        data, err = call(self.client, "screen", dataset="cpi-jp", limit=5)
        self.assertFalse(err, data)
        self.assertIn(data["data"]["sort"], data["data"]["ranked_on"])
        self.assertLessEqual(len(data["data"]["rows"]), 5)
        values = [r[data["data"]["sort"]] for r in data["data"]["rows"]]
        self.assertEqual(values, sorted(values, reverse=True))

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_overview_is_generic_over_series_datasets(self):
        data, err = call(self.client, "get_overview", dataset="cpi-jp")
        self.assertFalse(err, data)
        self.assertEqual(data["tool"], "get_overview")
        self.assertTrue(data["data"])
        # Not an index dataset, and still served — this is the gap the v1
        # cpi-only tool left.
        data, err = call(self.client, "get_overview", dataset="boj-assets")
        self.assertFalse(err, data)

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_vintages_lists_releases_and_revisions(self):
        data, err = call(self.client, "get_vintages", dataset="cpi-jp")
        self.assertFalse(err, data)
        self.assertEqual(data["data"]["mode"], "releases")
        self.assertTrue(data["data"]["releases"])
        data, err = call(self.client, "get_vintages", dataset="cpi-jp", series="0001")
        self.assertFalse(err, data)
        self.assertEqual(data["data"]["mode"], "revisions")

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_breakdown_lists_its_cuts_then_serves_one(self):
        data, err = call(self.client, "get_breakdown", dataset="cpi-jp")
        self.assertFalse(err, data)
        cuts = [c["cut"] for c in data["cuts"]]
        self.assertIn("contributions", cuts)
        data, err = call(self.client, "get_breakdown", dataset="cpi-jp",
                         cut="contributions")
        self.assertFalse(err, data)
        self.assertEqual(data["data"]["cut"], "contributions")
        # An argument the cut does not take is refused by name, never dropped.
        data, err = call(self.client, "get_breakdown", dataset="cpi-jp",
                         cut="contributions", area="13")
        self.assertTrue(err)
        self.assertIn("area", data["error"])

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_get_series_cites_the_dataset_page_not_the_cpi_explorer(self):
        data, err = call(self.client, "get_series", dataset="jgb-yields", series="10Y")
        self.assertFalse(err, data)
        self.assertNotIn("/explorer.html", data["cite"])
        self.assertIn(registry.get("jgb-yields")["page"].lstrip("/"), data["cite"])


@unittest.skipUnless(EQUITY, "equity database not present")
class EquityToolTest(Base):
    def test_lvh_404_is_a_404_not_a_typeerror(self):
        with self.assertRaises(HTTPException) as ctx:
            lvh_api.company("0000", history=50)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("5%", ctx.exception.detail)

    def test_search_company_lists_datasets(self):
        data, err = call(self.client, "search", query="7974")
        self.assertFalse(err, data)
        hit = data["companies"][0]
        self.assertEqual(hit["sec_code"], "7974")
        self.assertIn("cross-shareholdings", hit["datasets"])
        self.assertGreater(len(hit["datasets"]), 3)

    def test_get_company_single_matches_v1(self):
        data, err = call(self.client, "get_company", code="7974",
                         dataset="cross-shareholdings")
        self.assertFalse(err, data)
        v1 = json.loads(tools.get_company_holdings("7974"))
        self.assertEqual(len(data["data"]["holdings"]), len(v1["holdings"]))
        self.assertEqual(data["cite"], v1["cite"])
        self.assertEqual(data["vintage"]["unit"], "filing")
        self.assertIn("pct_of_equity", data["calc"])

    def test_get_company_composed_and_missing_is_not_an_error(self):
        data, err = call(self.client, "get_company", code="7974")
        self.assertFalse(err, data)
        self.assertIn("cross-shareholdings", data["coverage"]["present"])
        self.assertIn("boards-and-pay", data["coverage"]["present"])
        self.assertEqual(data["coverage"]["errors"], [])
        self.assertEqual(data["company"]["sec_code"], "7974")
        self.assertIn("board_size", data["datasets"]["boards-and-pay"]["facts"])
        # Whatever is absent is reported with a reason, never dropped silently.
        for block in data["coverage"]["missing"]:
            self.assertTrue(block["reason"])
        seen = set(data["coverage"]["present"]) | set(
            b["dataset"] for b in data["coverage"]["missing"])
        self.assertEqual(seen, set(i for i in registry.ids()
                                   if "company" in registry.get(i)["capabilities"]))

    def test_get_company_no_data_single_dataset(self):
        """No rows is an answer with a reason, not a JSON-RPC error."""
        data, err = call(self.client, "get_company", code="0000",
                         dataset="cross-shareholdings")
        self.assertFalse(err, data)
        self.assertIsNone(data["data"])
        self.assertIn("missing", data)
        self.assertIn("cite", data)

    def test_get_company_unknown_code_everywhere(self):
        data, err = call(self.client, "get_company", code="0000")
        self.assertFalse(err, data)
        self.assertEqual(data["coverage"]["present"], [])
        self.assertEqual(data["coverage"]["errors"], [])

    def test_screen_matches_v1_and_validates_sort(self):
        data, err = call(self.client, "screen", dataset="boards-and-pay",
                         sort="oldest_boards", limit=5)
        self.assertFalse(err, data)
        v1 = json.loads(tools.get_governance_screen(metric="oldest_boards", limit=5))
        self.assertEqual(data["data"]["rows"][0]["sec_code"], v1["rows"][0]["sec_code"])
        data, err = call(self.client, "screen", dataset="boards-and-pay", sort="tallest")
        self.assertTrue(err)
        self.assertIn("oldest_boards", data["error"])

    def test_every_dataset_capability_answers_with_the_envelope(self):
        """The contract test: no capability of any dataset raises, 500s, or
        returns a JSON-RPC error — including for a company with no rows."""
        keys = ("tool", "dataset", "data", "provenance", "calc", "vintage", "cite")
        for mid in registry.ids():
            m = registry.get(mid)
            if not registry.available(mid):
                continue
            # This one test makes several calls per dataset and the catalogue
            # has outgrown the per-minute limit a real client gets; the limit
            # is not what is under test here.
            mcp._HITS.clear()
            if "company" in m["capabilities"]:
                for code in ("7974", "0000"):
                    data, err = call(self.client, "get_company", code=code, dataset=mid)
                    self.assertFalse(err, (mid, code, data))
                    for k in keys:
                        self.assertIn(k, data, (mid, k))
            if m.get("screens"):
                for s in m["screens"]:
                    data, err = call(self.client, "screen", dataset=mid, sort=s["id"], limit=3)
                    self.assertFalse(err, (mid, s["id"], data))
                    self.assertIn("data", data)
            if m["shape"] == "series":
                hits, err = call(self.client, "search", query="", dataset=mid)
                self.assertTrue(err)  # empty query refused
                data, err = call(self.client, "describe_dataset", dataset=mid)
                self.assertFalse(err, (mid, data))


if __name__ == "__main__":
    unittest.main()
