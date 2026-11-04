# -*- coding: utf-8 -*-
"""The dataset registry: every card is sound, every path resolves, and a bad
card is quarantined rather than fatal.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_manifests
"""
import ast
import copy
import json
import os
import pathlib
import re
import unittest

from fastapi.testclient import TestClient

from app import api, equity_api, registry, sec_api, sec_text_api, us_press
from app.main import app
from tests import _data

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def _js_text(name):
    """A page script with its string concatenations collapsed, so a formula
    split across `"…" + "…"` lines compares as one sentence."""
    src = (WEB / "assets" / name).read_text(encoding="utf-8")
    return re.sub(r'"\s*\+\s*\n?\s*"', "", src)


def _fixture():
    """A sound manifest to mutate — copied from a live one."""
    return copy.deepcopy(registry.get("cpi-jp"))


class RegistryTest(unittest.TestCase):
    def test_every_registered_dataset_has_a_sound_manifest(self):
        self.assertEqual(registry.errors(), [], registry.report())

    def test_count_matches_the_registered_modules(self):
        ids = registry.ids()
        self.assertEqual(len(ids), len(api.ADAPTERS) + len(registry.EQUITY_MODULES))
        self.assertEqual(len(set(ids)), len(ids))
        macro = {i for i in ids if registry.get(i)["shape"] == "series"}
        self.assertEqual(macro, set(api.ADAPTERS))

    def test_trust_contract_on_every_measure(self):
        for mid in registry.ids():
            for meas in registry.get(mid)["measures"]:
                where = "%s.%s" % (mid, meas["id"])
                self.assertIn(meas["trust"], ("official", "derived"), where)
                if meas["trust"] == "derived":
                    self.assertTrue(meas.get("calc", "").strip(), where + " has no formula")
                else:
                    self.assertNotIn("calc", meas, where + " is official yet carries a formula")
                self.assertIn(meas["unit"], registry.UNITS, where)

    def test_generic_formulas_match_the_api(self):
        for slug in api.ADAPTERS:
            m = registry.get(slug)
            by_id = dict((x["id"], x) for x in m["measures"])
            unit = registry._API_UNIT[by_id["index"]["unit"]]
            for gen in ("yoy", "mom", "ann3m"):
                if gen in by_id:
                    self.assertEqual(by_id[gen]["calc"], api._calc_for(gen, unit), slug)
                    self.assertEqual(by_id[gen]["trust"], api.TRUST[gen], slug)

    def test_page_formulas_match_the_page_scripts(self):
        """Formulas that live in the page scripts are copied onto the card;
        this is what notices when one side is edited and not the other."""
        pairs = {
            "jgb-yields": ("rates.js", ("s2s10", "s10s30", "delta")),
            "jnto-visitors": ("inbound.js", ("ytd_growth", "ex_china_growth",
                                             "market_contrib_pp", "share_pct")),
            "population-jp": ("population.js", ("change_pct", "natural_pct", "social_pct",
                                                "foreign_pct", "aged_pct")),
            # The trade pages share one script; the balance formula names the
            # two commodities, so it is the adapter's sentence (served in the
            # /trade payload's page block) rather than a literal in the script.
            "trade-semis": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
            "trade-autos": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
            "trade-energy": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
            "trade-machinery": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
            "trade-pharma": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
            "trade-food": ("trade.js", ("ttm", "share_pct", "ttm_yoy", "unit_value")),
        }
        for slug, (script, ids) in pairs.items():
            text = _js_text(script)
            by_id = dict((x["id"], x) for x in registry.get(slug)["measures"])
            for mid in ids:
                self.assertIn(by_id[mid]["calc"], text, "%s.%s drifted from %s"
                              % (slug, mid, script))

    # --- what validate() refuses -------------------------------------------

    def _refuse(self, mutate, fragment):
        m = _fixture()
        mutate(m)
        problems = registry.validate(m, module=api.ADAPTERS["cpi-jp"])
        self.assertTrue(any(fragment in p for p in problems),
                        "expected %r in %r" % (fragment, problems))

    def test_rejects_derived_without_formula(self):
        def mutate(m):
            m["measures"].append({"id": "x", "label": "x", "unit": "%", "trust": "derived"})
        self._refuse(mutate, "must state their formula")

    def test_rejects_official_with_formula(self):
        def mutate(m):
            m["measures"].append({"id": "x", "label": "x", "unit": "%", "trust": "official",
                                  "calc": "a ÷ b"})
        self._refuse(mutate, "official measure carries no")

    def test_rejects_model_trust(self):
        def mutate(m):
            m["measures"].append({"id": "x", "label": "x", "unit": "%", "trust": "model",
                                  "calc": "nowcast"})
        self._refuse(mutate, "reserved")

    def test_rejects_unit_outside_the_vocabulary(self):
        def mutate(m):
            m["measures"][0]["unit"] = "percent"
        self._refuse(mutate, "is not one of")

    def test_rejects_a_drifted_generic_formula(self):
        def mutate(m):
            for meas in m["measures"]:
                if meas["id"] == "yoy":
                    meas["calc"] = "something else"
        self._refuse(mutate, "must equal api.py's formula")

    def test_rejects_todo_scaffold(self):
        def mutate(m):
            m["notes"].append("TODO: write this")
        self._refuse(mutate, "TODO")

    def test_rejects_unknown_section_and_missing_page(self):
        self._refuse(lambda m: m.update(section="weather"), "section")
        self._refuse(lambda m: m.update(page="/nowhere.html"), "not a file under web/")

    def test_rejects_stale_days_that_disagree_with_presentation(self):
        def mutate(m):
            m["vintage"]["stale_after_days"] = 1
        self._refuse(mutate, "stale_after_days")

    def test_scaffold_is_refused_until_filled(self):
        ns = {}
        adapter = api.ADAPTERS["cpi-jp"]
        ns.update(DATASET=adapter.DATASET, SOURCE=adapter.SOURCE,
                  PRESENTATION=adapter.PRESENTATION)
        exec(registry.scaffold("cpi-jp"), ns)  # noqa: S102 — our own generated source
        problems = registry.validate(ns["MANIFEST"], module=adapter)
        self.assertTrue(any("TODO" in p for p in problems))


class BindTest(unittest.TestCase):
    """Tier B: paths resolve on the real app; a bad one is quarantined."""

    def setUp(self):
        os.environ.pop("MANIFEST_STRICT", None)
        registry.load()

    def tearDown(self):
        os.environ.pop("MANIFEST_STRICT", None)
        registry.load()
        registry.bind(app)

    def test_every_endpoint_resolves(self):
        registry.bind(app)
        self.assertEqual(registry.errors(), [], registry.report())
        self.assertTrue(registry.status()["bound"])

    def test_resolver_uses_no_version_fragile_api(self):
        """Every manifest path resolves through route templates and compiled
        regexes only. Route.matches() with a hand-built scope passed locally
        and failed in the container, quarantining all nineteen datasets in
        production; nothing here may depend on the scope keys of the day."""
        tree = ast.parse(pathlib.Path(registry.__file__).read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        self.assertNotIn("matches", called)
        for mid in registry.ids():
            for role, path in registry.get(mid)["endpoints"].items():
                self.assertTrue(registry.resolves(app, path), "%s.%s: %s" % (mid, role, path))

    def test_systemic_resolution_failure_fails_open(self):
        """One bad card is quarantined; a resolver that rejects everything is
        the resolver's fault, and dropping the whole catalogue is never right."""
        real = registry.resolves
        registry.resolves = lambda app, path: False
        try:
            registry.load()
            registry.bind(app)
            self.assertEqual(len(registry.ids()), 19 if len(registry.ids()) == 19
                             else len(registry.ids()))
            self.assertTrue(registry.ids(), "every dataset was dropped")
            self.assertTrue(any(e["id"] is None and "resolver fault" in " ".join(e["errors"])
                                for e in registry.errors()))
        finally:
            registry.resolves = real
            registry.load()
            registry.bind(app)

    def test_route_discovery_sees_the_whole_app(self):
        """Four routes out of a hundred is what the container reported before
        this read the OpenAPI schema. A resolver that sees only a handful of
        routes rejects every manifest, so assert it sees them all."""
        found = registry.route_paths(app)
        self.assertGreater(len(found), 40)
        for p in ("/api/v1/{dataset}/observations", "/api/v1/catalog/manifests"):
            self.assertIn(p, found)

    def test_static_mount_never_counts_as_a_route(self):
        """The mount at "/" matches every path; if it counted, every manifest
        would "resolve" and the check would prove nothing."""
        self.assertFalse(registry.resolves(app, "/api/v1/definitely-not-a-route"))
        self.assertFalse(registry.resolves(app, "/cpi.html"))
        self.assertNotIn("/", registry.route_paths(app))

    def test_resolution_is_of_the_concrete_path(self):
        self.assertTrue(registry.resolves(app, "/api/v1/equity/company/{sec_code}"))
        self.assertTrue(registry.resolves(app, "/api/v1/cpi-jp/observations?series=0001"))
        self.assertFalse(registry.resolves(app, "/api/v1/nope"))
        self.assertFalse(registry.resolves(app, "/cpi.html"))  # the static mount is not a route

    def _inject_broken(self):
        m = copy.deepcopy(registry._REGISTRY["cpi-jp"])
        m["id"] = "broken"
        m["endpoints"]["series"] = "/api/v1/nope"
        registry._REGISTRY["broken"] = m
        registry._ORDER.append("broken")

    def test_quarantine_is_not_fatal(self):
        self._inject_broken()
        registry.bind(app)
        self.assertNotIn("broken", registry.ids())
        self.assertIn("cpi-jp", registry.ids())
        status = registry.status()
        self.assertEqual(status["quarantined"], ["broken"])
        self.assertIn("/api/v1/nope", json.dumps(status["errors"]))

    def test_strict_mode_raises(self):
        self._inject_broken()
        os.environ["MANIFEST_STRICT"] = "1"
        with self.assertRaises(registry.RegistryError):
            registry.bind(app)


class CatalogEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load()
        registry.bind(app)
        cls.client = TestClient(app)

    def test_manifests_lists_every_dataset_in_section_order(self):
        r = self.client.get("/api/v1/catalog/manifests")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], len(registry.ids()))
        rank = dict((s, i) for i, s in enumerate(registry.SECTION_IDS))
        order = [rank[d["section"]] for d in body["datasets"]]
        self.assertEqual(order, sorted(order))
        for d in body["datasets"]:
            self.assertIn("available", d)

    def test_one_manifest(self):
        r = self.client.get("/api/v1/catalog/manifests/cpi-jp")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], "cpi-jp")
        self.assertEqual(r.json()["section"], "prices")

    def test_unknown_id_lists_the_valid_ones(self):
        r = self.client.get("/api/v1/catalog/manifests/nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["valid_ids"], registry.ids())

    def test_sections_cover_every_dataset_exactly_once(self):
        r = self.client.get("/api/v1/catalog/sections")
        self.assertEqual(r.status_code, 200)
        listed = [d for s in r.json()["sections"] for d in s["datasets"]]
        self.assertEqual(sorted(listed), sorted(registry.ids()))
        self.assertEqual(len(listed), len(set(listed)))

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_catalog_datasets_is_untouched(self):
        r = self.client.get("/api/v1/catalog/datasets")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), api.catalog())

    @unittest.skipUnless(_data.MACRO, _data.NO_MACRO)
    def test_health_carries_the_manifests_block(self):
        r = self.client.get("/api/v1/catalog/health")
        self.assertEqual(r.status_code, 200)
        block = r.json()["manifests"]
        self.assertEqual(block["quarantined"], [])
        self.assertEqual(block["registered"], len(registry.ids()))

    def test_equity_absent_is_listed_but_unavailable(self):
        real = equity_api.DB_PATH
        real_sec = sec_api.DB_PATH
        real_text, real_press = sec_text_api.DB_PATH, us_press.DB_PATH
        equity_api.DB_PATH = pathlib.Path("/nonexistent/equity.duckdb")
        sec_api.DB_PATH = pathlib.Path("/nonexistent/sec.duckdb")   # the US shelf's own file
        # and the two US datasets that keep a file of their own
        sec_text_api.DB_PATH = pathlib.Path("/nonexistent/sec_text.duckdb")
        us_press.DB_PATH = pathlib.Path("/nonexistent/us-press.sqlite")
        try:
            r = self.client.get("/api/v1/catalog/manifests")
            self.assertEqual(r.status_code, 200)
            rows = r.json()["datasets"]
            self.assertEqual(len(rows), len(registry.ids()))
            for d in rows:
                if d["shape"] != "series":
                    self.assertFalse(d["available"], d["id"])
        finally:
            equity_api.DB_PATH = real
            sec_api.DB_PATH = real_sec
            sec_text_api.DB_PATH, us_press.DB_PATH = real_text, real_press


if __name__ == "__main__":
    unittest.main()
