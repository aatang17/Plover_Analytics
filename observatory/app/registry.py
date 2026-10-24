# -*- coding: utf-8 -*-
"""The dataset registry: one manifest per dataset, validated, then served.

Every dataset module — a macro adapter in ``app/adapters/`` or an equity API
module — exports a ``MANIFEST``: a plain, JSON-serialisable dict that says
what the dataset is, where its numbers come from, which of its measures are
official and which are calculated (and by what formula), where to fetch it,
and where to cite it. This module collects those manifests, checks them, and
is the single source the catalog endpoints, the MCP resources and (later) the
composed company view read from.

Manifests are DATA, never code. They hold no callables: the read functions
behind a capability are bound here, by name, so an adapter never has to
import the API layer that imports it. That is also what keeps
``/api/v1/catalog/manifests`` a straight ``JSONResponse(manifest)``.

Two tiers of validation:

  Tier A — structural, at import, no database and no app object: required
  keys, closed vocabularies, and the trust contract — ``trust`` is
  ``official`` or ``derived`` (``model`` is reserved and refused), ``calc``
  is required exactly when a measure is derived, and for the series datasets
  the formula text must equal what ``api.py`` actually computes, so a card
  can never describe arithmetic the platform does not do.

  Tier B — route resolution, at start-up, from ``main.lifespan``: every path
  a manifest names must be a real FastAPI route, parameter names included.

Failure policy: a bad manifest is QUARANTINED, not fatal. The serving process
drops that one card, keeps serving every other dataset, and reports the fault
on ``/api/v1/catalog/health`` (which the admin console and the alert webhook
already watch). The same rule the ingest follows — a fault in one dataset is
never a reason for the site to be down. Under the test suite, under
``python -m app.registry --check``, and with ``MANIFEST_STRICT=1`` in the
environment, any fault is an error instead.

Usage:  python -m app.registry --check              # validate everything, exit 1 on a fault
        python -m app.registry --scaffold <slug>    # draft a MANIFEST for a macro adapter
"""
import copy
import importlib
import json
import os
import pathlib
import re
import sys

from . import api

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

# ---------------------------------------------------------------------------
# Vocabularies. Closed on purpose: a manifest can only say things the platform
# has a meaning for, so a typo is a validation error rather than a new category.
# ---------------------------------------------------------------------------

# Display order. These are public ids — they appear in the catalog, the MCP
# resource list and eventually the company page — so they are hard to rename.
SECTIONS = [
    {"id": "prices", "label": "Prices"},
    {"id": "labour", "label": "Labour"},
    {"id": "agriculture", "label": "Agriculture"},
    {"id": "monetary", "label": "Monetary"},
    {"id": "banking", "label": "Banking"},
    {"id": "rates", "label": "Rates"},
    {"id": "tourism", "label": "Tourism"},
    {"id": "demography", "label": "Demography"},
    {"id": "national-accounts", "label": "National accounts"},
    {"id": "fiscal", "label": "Public finance"},
    {"id": "corporate", "label": "Corporate"},
    {"id": "trade", "label": "Trade"},
    {"id": "ownership", "label": "Ownership"},
    {"id": "governance", "label": "Governance"},
    {"id": "capital-returns", "label": "Capital returns"},
    # Market activity: what the whole market is doing, as opposed to what one
    # company filed. Margin balances and investor-type flows live here.
    {"id": "market", "label": "Market activity"},
    {"id": "assets", "label": "Assets"},
    {"id": "financials", "label": "Financials"},
    # The US comparison shelf: not Japan, not a product surface, but served in
    # the same shape so a Japanese figure has a counterpart to stand beside.
    {"id": "us-reference", "label": "US reference"},
]
SECTION_IDS = [s["id"] for s in SECTIONS]

SHAPES = ("series", "company", "events")
TRUST = ("official", "derived")          # "model" is reserved; refused today
FREQUENCIES = ("daily", "weekly", "monthly", "quarterly", "semiannual", "annual",
               "per-filing", "per-event")
VINTAGE_UNITS = ("release", "filing")
# "captured_at" is deliberately absent: capture time was never recorded,
# so no dataset can honestly claim it. See app/asof.py.
AS_OF_BASES = ("release-in-force", "filed_date")
CAPABILITIES = ("series", "company", "screen", "search", "summary")

# Units a measure may carry. "%" is a share of a level; "pp" is a change of a
# percentage — the distinction the design rules insist on, enforced here.
# "person_nights" is a count of nights slept, not of people: one visitor
# staying three nights is three person-nights and one person. Keeping it
# distinct from "persons" stops an arrivals count and a guest-night count
# being ranked or summed against each other.
# "JPY_per_60kg" is its own unit, not a JPY level: rice is contracted and
# published by the 60kg bag of brown rice, and a price per bag must never be
# ranked against, or summed with, a yen amount that means something else.
# Bank statements are published in 百万円 and the FSA's bad-loan flows in
# 兆円; each is stored in the unit it was released in, never rescaled.
# "per_1000" is the demographers' unit: a crude birth or death rate is per
# thousand of the mid-year population, and is not a percentage of anything.
# "births_per_woman" is the total fertility rate's own unit — a count of
# children, not a rate of a population — and must never be ranked against
# either of the above.
# "JPY_billion" is the Cabinet Office's unit for the national accounts
# (10億円) and is stored as released, like every other yen unit here.
# "USD" is the US shelf's own unit (sec_api.py); a filing reporting in another
# currency carries that currency on the row and is never ranked against USD.
UNITS = ("index", "%", "pp", "USD", "per_10000", "per_1000", "births_per_woman",
         "JPY", "JPY_per_60kg", "JPY_billion",
         "JPY_thousand", "JPY_million", "JPY_100mn", "JPY_trillion", "tonnes_10k",
         "persons", "person_nights", "count", "quantity", "shares",
         # The Labour Force Survey counts people in 万人 (ten thousand) and
         # is stored so; a 万人 count is never ranked against "persons".
         "persons_10k",
         # Japanese market statistics are published in 千株; a thousand-share
         # count is stored and shown in that unit, never rescaled to shares.
         "shares_thousand",
         "voting_rights", "m2", "JPY_per_m2", "x", "years", "days", "date",
         "category", "boolean", "text")

REQUIRED = ("id", "section", "name", "shape", "summary", "source", "keys",
            "frequency", "vintage", "measures", "endpoints", "capabilities",
            "cite", "page", "notes")
OPTIONAL = ("screens",)
SOURCE_REQUIRED = ("publisher", "document", "url", "credit", "license_note")
VINTAGE_REQUIRED = ("unit", "as_of_basis", "as_of_supported", "history_from",
                    "stale_after_days")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MEASURE_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

# The measures /observations serves for every series dataset. Their formulas
# live in api.py; a manifest restates them for readability and is checked
# against the original, so the two can never drift.
GENERIC_MEASURES = ("index", "yoy", "mom", "ann3m")

# Manifest unit → the unit key api.py uses to word the generic formulas.
_API_UNIT = {"index": "index", "JPY_100mn": "jpy_100mn", "%": "percent",
             "persons": "persons", "person_nights": "person_nights",
             "JPY_thousand": "jpy_1000", "JPY_per_60kg": "jpy_per_60kg",
             "JPY_million": "jpy_million", "JPY_trillion": "jpy_trillion",
             "tonnes_10k": "t10k_brown_rice", "JPY": "jpy",
             "JPY_billion": "jpy_billion",
             # US dollars as published by the BLS (average prices, earnings);
             # constant 1982-84 dollars share the wording, not the unit label.
             "USD": "usd",
             "persons_10k": "persons_10k",
             # A ratio of counts (job openings per applicant, 倍).
             "x": "ratio"}

# Equity API modules, in registration order. Macro modules come from
# api.ADAPTERS, so a new adapter is registered here by being registered there.
EQUITY_MODULES = ("equity_api", "ownership_api", "lvh_api", "short_api",
                  "governance_api",
                  "buyback_api", "facility_api", "financials_api", "agm_api",
                  "segments_api", "earnings_api", "sec_api", "sec_insiders_api", "sec_13f_api", "sec_13dg_api", "us_prices_api", "us_events_api", "bank_api",
                  "risk_api")


class RegistryError(Exception):
    """Raised only in strict mode; the serving process quarantines instead."""


def strict():
    return os.environ.get("MANIFEST_STRICT", "").strip().lower() in (
        "1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Tier A — structural validation
# ---------------------------------------------------------------------------

def _central_formulas():
    """Formulas api.py computes for more than one dataset, by measure id.

    A manifest using one of these ids must carry the API's own wording.
    """
    out = {
        "contrib_pp": api.CONTRIB_CALC,
        "breadth_pct": api.BREADTH_CALC,
        "notes": api.NOTES_CALC,
        "world_value": api.TRADE_WORLD_CALC,
    }
    out.update(api.LEVEL_TILE_CALCS)
    out.update((k, v) for k, v in api.LEVEL_SERIES_CALCS.items() if k != "latest")
    return out


def _check_measures(m, errors):
    measures = m.get("measures")
    if not isinstance(measures, list) or not measures:
        errors.append("measures: must be a non-empty list")
        return
    seen = set()
    central = _central_formulas()
    for i, meas in enumerate(measures):
        where = "measures[%d]" % i
        if not isinstance(meas, dict):
            errors.append("%s: must be a dict" % where)
            continue
        mid = meas.get("id")
        if not isinstance(mid, str) or not _MEASURE_RE.match(mid):
            errors.append("%s.id: missing or not [a-z0-9_]" % where)
            continue
        where = "measures.%s" % mid
        if mid in seen:
            errors.append("%s: duplicate measure id" % where)
        seen.add(mid)
        if not isinstance(meas.get("label"), str) or not meas["label"].strip():
            errors.append("%s.label: missing" % where)
        if meas.get("unit") not in UNITS:
            errors.append("%s.unit: %r is not one of %s" % (where, meas.get("unit"), UNITS))
        trust = meas.get("trust")
        if trust not in TRUST:
            errors.append("%s.trust: %r must be one of %s (\"model\" is reserved)"
                          % (where, trust, TRUST))
        calc = meas.get("calc")
        if trust == "derived":
            if not isinstance(calc, str) or not calc.strip():
                errors.append("%s: derived measures must state their formula in "
                              "`calc`" % where)
        elif trust == "official" and calc is not None:
            errors.append("%s: an official measure carries no `calc` — it is "
                          "published, not computed" % where)
        extra = set(meas) - {"id", "label", "unit", "trust", "calc", "where"}
        if extra:
            errors.append("%s: unknown keys %s" % (where, sorted(extra)))
        if mid in central and calc is not None and calc != central[mid]:
            errors.append("%s.calc: must equal the API's formula for %s"
                          % (where, mid))


def _check_series_dataset(m, module, errors):
    """Rules that bind a series manifest to the adapter and the API it describes."""
    mid = m.get("id")
    adapter = api.ADAPTERS.get(mid)
    if adapter is None:
        errors.append("id: a series-shaped manifest must be a registered macro "
                      "dataset; %r is not in api.ADAPTERS" % mid)
        return
    if module is not None and adapter is not module:
        errors.append("id: %r is registered to %s, not to this module"
                      % (mid, adapter.__name__))
    by_id = dict((x.get("id"), x) for x in m.get("measures", []) if isinstance(x, dict))
    index = by_id.get("index")
    if index is None:
        errors.append("measures: a series dataset must declare the `index` measure "
                      "(the published value /observations serves)")
        return
    unit_key = _API_UNIT.get(index.get("unit"))
    if unit_key is None:
        errors.append("measures.index.unit: %r has no api.py unit wording"
                      % index.get("unit"))
        return
    for gen in GENERIC_MEASURES:
        meas = by_id.get(gen)
        if meas is None:
            continue
        want_trust = api.TRUST[gen]
        want_calc = api._calc_for(gen, unit_key)
        if meas.get("trust") != want_trust:
            errors.append("measures.%s.trust: api.py serves it as %r" % (gen, want_trust))
        if gen == "index":
            # The published value's "calc" is api.py's wording of "as released";
            # a manifest may leave it off (official) or restate it exactly.
            if meas.get("calc") not in (None, want_calc):
                errors.append("measures.index.calc: must be omitted or equal api.py's")
        elif meas.get("calc") != want_calc:
            errors.append("measures.%s.calc: must equal api.py's formula: %r"
                          % (gen, want_calc))
    vint = m.get("vintage") or {}
    if vint.get("unit") != "release":
        errors.append("vintage.unit: a series dataset is versioned by release")
    if vint.get("as_of_supported") is not True:
        errors.append("vintage.as_of_supported: series datasets serve ?as_of= today")
    pres = getattr(adapter, "PRESENTATION", {})
    if vint.get("stale_after_days") != pres.get("stale_after_days"):
        errors.append("vintage.stale_after_days: must equal PRESENTATION[\"stale_after_days\"] "
                      "(%r) — one copy, read by health()" % pres.get("stale_after_days"))


def validate(m, module=None):
    """Tier A. Returns a list of problems — empty means the manifest is sound."""
    errors = []
    if not isinstance(m, dict):
        return ["MANIFEST must be a dict"]
    for key in REQUIRED:
        if key not in m:
            errors.append("%s: missing" % key)
    unknown = set(m) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        errors.append("unknown keys %s" % sorted(unknown))
    if errors:
        return errors

    mid = m["id"]
    if not isinstance(mid, str) or not _ID_RE.match(mid):
        errors.append("id: must match [a-z0-9-]")
    if m["section"] not in SECTION_IDS:
        errors.append("section: %r is not one of %s" % (m["section"], SECTION_IDS))
    if m["shape"] not in SHAPES:
        errors.append("shape: %r is not one of %s" % (m["shape"], SHAPES))
    name = m["name"]
    if not (isinstance(name, dict) and all(
            isinstance(name.get(k), str) and name[k].strip() for k in ("en", "ja"))):
        errors.append("name: needs non-empty `en` and `ja`")
    if not isinstance(m["summary"], str) or not m["summary"].strip():
        errors.append("summary: missing")

    src = m["source"]
    if not isinstance(src, dict):
        errors.append("source: must be a dict")
    else:
        for k in SOURCE_REQUIRED:
            if not isinstance(src.get(k), str) or not src[k].strip():
                errors.append("source.%s: missing" % k)
        if isinstance(src.get("url"), str) and not src["url"].startswith("http"):
            errors.append("source.url: must be an http(s) URL")

    if not (isinstance(m["keys"], list) and m["keys"]
            and all(isinstance(k, str) for k in m["keys"])):
        errors.append("keys: must be a non-empty list of strings")
    if m["frequency"] not in FREQUENCIES:
        errors.append("frequency: %r is not one of %s" % (m["frequency"], FREQUENCIES))

    vint = m["vintage"]
    if not isinstance(vint, dict):
        errors.append("vintage: must be a dict")
    else:
        for k in VINTAGE_REQUIRED:
            if k not in vint:
                errors.append("vintage.%s: missing" % k)
        if vint.get("unit") not in VINTAGE_UNITS:
            errors.append("vintage.unit: %r is not one of %s" % (vint.get("unit"), VINTAGE_UNITS))
        if vint.get("as_of_basis") not in AS_OF_BASES:
            errors.append("vintage.as_of_basis: %r is not one of %s"
                          % (vint.get("as_of_basis"), AS_OF_BASES))
        if not isinstance(vint.get("as_of_supported"), bool):
            errors.append("vintage.as_of_supported: must be true or false")
        if not isinstance(vint.get("history_from"), str) or not vint["history_from"]:
            errors.append("vintage.history_from: missing")
        sad = vint.get("stale_after_days")
        if sad is not None and not (isinstance(sad, int) and sad > 0):
            errors.append("vintage.stale_after_days: must be a positive int or null")
        if m["shape"] != "series" and vint.get("unit") == "release":
            errors.append("vintage.unit: only series datasets are versioned by release")

    _check_measures(m, errors)

    eps = m["endpoints"]
    if not isinstance(eps, dict) or not eps:
        errors.append("endpoints: must be a non-empty dict of role → path")
    else:
        for role, path in eps.items():
            if not isinstance(path, str) or not path.startswith("/api/v1/"):
                errors.append("endpoints.%s: must be a path under /api/v1/" % role)
    caps = m["capabilities"]
    if not isinstance(caps, list):
        errors.append("capabilities: must be a list")
    else:
        for c in caps:
            if c not in CAPABILITIES:
                errors.append("capabilities: %r is not one of %s" % (c, CAPABILITIES))
            elif isinstance(eps, dict) and c not in eps:
                errors.append("capabilities: %r declared but endpoints has no %r"
                              % (c, c))

    screens = m.get("screens", [])
    if not isinstance(screens, list):
        errors.append("screens: must be a list")
    else:
        for i, s in enumerate(screens):
            if not (isinstance(s, dict) and isinstance(s.get("id"), str)
                    and isinstance(s.get("title"), str)):
                errors.append("screens[%d]: needs `id` and `title`" % i)

    for key in ("cite", "page"):
        val = m[key]
        if not isinstance(val, str) or not val.startswith("/"):
            errors.append("%s: must be a site-relative path starting with /" % key)
    if isinstance(m["page"], str) and m["page"].startswith("/"):
        if not (WEB_DIR / m["page"].lstrip("/").split("?")[0]).is_file():
            errors.append("page: %s is not a file under web/" % m["page"])
    if isinstance(m["cite"], str) and isinstance(m["keys"], list):
        for ph in _PLACEHOLDER_RE.findall(m["cite"]):
            if ph not in m["keys"]:
                errors.append("cite: placeholder {%s} is not one of keys" % ph)

    if not (isinstance(m["notes"], list) and all(isinstance(n, str) for n in m["notes"])):
        errors.append("notes: must be a list of strings")

    if m["shape"] == "series":
        _check_series_dataset(m, module, errors)

    try:
        text = json.dumps(m, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        errors.append("manifest is not JSON-serialisable: %s" % exc)
    else:
        if "TODO" in text:
            errors.append("contains TODO — a scaffold is not a manifest until it is filled in")
    return errors


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

_REGISTRY = {}      # id -> manifest (validated)
_MODULES = {}       # id -> the module that exported it, for the availability gate
_ORDER = []         # ids in registration order
_ERRORS = []        # [{"module", "id", "errors": [...]}]
_BOUND = False


def _sources():
    """(label, module) for every dataset module, macro first."""
    out = []
    for slug in sorted(api.ADAPTERS):
        mod = api.ADAPTERS[slug]
        out.append(("adapters." + mod.__name__.rsplit(".", 1)[-1], mod))
    for name in EQUITY_MODULES:
        try:
            mod = importlib.import_module("." + name, package=__package__)
        except Exception as exc:  # noqa: BLE001 — a broken module is reported, not fatal
            _ERRORS.append({"module": name, "id": None,
                            "errors": ["import failed: %s" % exc]})
            continue
        out.append((name, mod))
    return out


def load():
    """Collect and validate every MANIFEST. Idempotent; called at import."""
    del _ORDER[:]
    del _ERRORS[:]
    _REGISTRY.clear()
    _MODULES.clear()
    for label, mod in _sources():
        m = getattr(mod, "MANIFEST", None)
        if m is None:
            _ERRORS.append({"module": label, "id": None,
                            "errors": ["no MANIFEST — every registered dataset needs one"]})
            continue
        problems = validate(m, module=mod)
        mid = m.get("id") if isinstance(m, dict) else None
        if not problems and mid in _REGISTRY:
            problems = ["id: %r is already registered by another module" % mid]
        if problems:
            _ERRORS.append({"module": label, "id": mid, "errors": problems})
            continue
        _REGISTRY[mid] = copy.deepcopy(m)
        _MODULES[mid] = mod
        _ORDER.append(mid)
    if strict() and _ERRORS:
        raise RegistryError(report())


def bind(app):
    """Tier B: every endpoint a manifest names must be a live route.

    Called from the app's lifespan, once the routers are included. A manifest
    naming a path that does not exist is quarantined; in strict mode it raises.
    """
    global _BOUND, _ROUTES_SEEN
    seen = route_paths(app)
    _ROUTES_SEEN = len(seen)
    faults = {}
    for mid in list(_ORDER):
        m = _REGISTRY[mid]
        bad = []
        for role, path in m["endpoints"].items():
            if not resolves(app, path):
                bad.append("endpoints.%s: %s is not a route on this app" % (role, path))
        if bad:
            faults[mid] = bad
    # Quarantine is for one dataset whose card is wrong. When nearly every
    # card fails at once the fault is in the resolver, not in nineteen
    # manifests — and dropping the whole catalogue is never the better answer,
    # because the endpoints demonstrably exist and are serving. Report it
    # loudly and keep serving. (This is not hypothetical: a Starlette version
    # difference between the laptop and the container did exactly that.)
    if faults and len(faults) > max(1, len(_ORDER) // 2):
        _ERRORS.append({
            "module": "registry", "id": None,
            "errors": ["route resolution failed for %d of %d datasets — treating "
                       "this as a resolver fault, not %d bad manifests; every "
                       "dataset stays registered. First: %s. The app offered "
                       "%d leaf routes, e.g. %s"
                       % (len(faults), len(_ORDER), len(faults),
                          "; ".join(sorted(faults)[:1] + faults[sorted(faults)[0]][:1]),
                          len(seen), ", ".join(seen[:5]) or "none")]})
    else:
        for mid, bad in faults.items():
            _quarantine(mid, bad)
    _BOUND = True
    if strict() and _ERRORS:
        raise RegistryError(report())
    return _ERRORS


_TEMPLATE_PARAM = re.compile(r"\{[^{}]+\}")
_TEMPLATE_CACHE = {}


def _template_regex(template):
    """A route template as a regex: {param} matches one path segment."""
    rx = _TEMPLATE_CACHE.get(template)
    if rx is None:
        parts = [re.escape(x) for x in _TEMPLATE_PARAM.split(template)]
        rx = _TEMPLATE_CACHE[template] = re.compile("^" + "[^/]+".join(parts) + "$")
    return rx


def route_paths(app):
    """Every route template the app documents, from its OpenAPI schema.

    The schema is a public, versioned API and lists exactly the routes the app
    serves — no framework internals, and no static mount (a mount matches every
    path there is, so counting it would make this check vacuous).

    Two earlier attempts read the router table directly and both failed inside
    the container while passing on the laptop: `Route.matches()` with a
    hand-built scope, then `isinstance`/`hasattr` duck-typing that found four
    routes out of a hundred. Neither FastAPI nor Starlette is pinned, so
    nothing here may depend on how routes happen to be represented.
    """
    try:
        paths = app.openapi().get("paths") or {}
    except Exception:  # noqa: BLE001 — a schema that will not build is not fatal
        paths = {}
    if paths:
        return sorted(paths)
    # Last resort, so a schema failure degrades rather than rejecting everything.
    out = set()
    for r in getattr(app, "routes", []):
        path = getattr(r, "path", None)
        if isinstance(path, str) and hasattr(r, "endpoint"):
            out.add(path)
    return sorted(out)


def resolves(app, path):
    """Whether a manifest path routes to a real handler on this app.

    Exact match on the template, else the concrete URL against the template's
    regex — which is what covers a manifest naming `/api/v1/cpi-jp/observations`
    where the route is `/api/v1/{dataset}/observations`.
    """
    template = path.split("?")[0]
    concrete = _PLACEHOLDER_RE.sub("0000", template)
    for route in route_paths(app):
        if route == template or _template_regex(route).match(concrete):
            return True
    return False


def _quarantine(mid, problems):
    m = _REGISTRY.pop(mid)
    _ORDER.remove(mid)
    _ERRORS.append({"module": "registry", "id": m["id"], "errors": problems})


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def ids():
    return list(_ORDER)


def get(mid):
    m = _REGISTRY.get(mid)
    return copy.deepcopy(m) if m is not None else None


def _sorted_ids():
    rank = dict((s, i) for i, s in enumerate(SECTION_IDS))
    return sorted(_ORDER, key=lambda i: (rank[_REGISTRY[i]["section"]], _ORDER.index(i)))


def datasets(with_availability=True):
    """Every manifest, in section order, each stamped with `available`."""
    out = []
    for mid in _sorted_ids():
        m = copy.deepcopy(_REGISTRY[mid])
        if with_availability:
            m["available"] = available(mid)
        out.append(m)
    return out


def by_section():
    groups = dict((s["id"], []) for s in SECTIONS)
    for mid in _sorted_ids():
        groups[_REGISTRY[mid]["section"]].append(mid)
    return [dict(s, datasets=groups[s["id"]]) for s in SECTIONS]


def bound(mid, attr):
    """A dataset's own read function, by convention.

    Every equity module names them the same way — `company`, `summary`,
    `companies` — so a capability a manifest declares resolves to a function on
    the module that exported it. Nothing to keep in step: registering a
    manifest is all a new dataset needs.
    """
    mod = _MODULES.get(mid)
    fn = getattr(mod, attr, None) if mod is not None else None
    return fn if callable(fn) else None


def errors():
    return copy.deepcopy(_ERRORS)


_ROUTES_SEEN = 0


def status():
    """The block /catalog/health carries.

    `routes_seen` is deliberately permanent. The production incident of
    2026-09-05 was route discovery finding four routes out of a hundred inside
    the container while finding all of them on the laptop, and it took three
    deploys to see that because nothing reported it. A number that should be in
    the hundreds and reads single digits now says so on the health endpoint.
    """
    return {"registered": len(_ORDER), "bound": _BOUND,
            "routes_seen": _ROUTES_SEEN,
            "quarantined": sorted(e["id"] or e["module"] for e in _ERRORS),
            "errors": errors()}


def report():
    lines = []
    for e in _ERRORS:
        head = e["id"] or e["module"]
        for p in e["errors"]:
            lines.append("%s: %s" % (head, p))
    return "\n".join(lines) or "ok"


# ---------------------------------------------------------------------------
# Availability — is the dataset actually present on this server?
#
# Bound here by id, using each module's own gate, so the registry never
# re-implements a check the API already makes. Unknown ids are unavailable.
# ---------------------------------------------------------------------------

def _macro_available(slug):
    try:
        from . import db
        cur = db.read_cursor()
        try:
            row = cur.execute("SELECT 1 FROM releases WHERE dataset=? AND status='published'",
                              [slug]).fetchone()
        finally:
            cur.close()
        return row is not None
    except Exception:  # noqa: BLE001 — no database is "not available", not a fault
        return False


def _gate(fn):
    try:
        fn()
        return True
    except Exception:  # noqa: BLE001 — the gates raise HTTPException(503)
        return False


def available(mid):
    """Whether this server actually holds the dataset.

    Each module answers for itself — its own `_require()` (which raises 503
    when its tables are absent), else the shared reader. Derived from the
    module that exported the manifest, so a new dataset needs no entry in any
    table here: registering the manifest is enough.
    """
    m = _REGISTRY.get(mid)
    if m is None:
        return False
    if m["shape"] == "series":
        return _macro_available(mid)
    mod = _MODULES.get(mid)
    if mod is None:
        return False
    gate = getattr(mod, "_require", None) or getattr(mod, "_cur", None)
    return _gate(gate) if gate else False


# ---------------------------------------------------------------------------
# Scaffold — a draft manifest for a macro adapter
# ---------------------------------------------------------------------------

def scaffold(slug):
    """Python source for a draft MANIFEST, built from what the adapter already
    declares. Everything the adapter cannot tell us is a TODO, and a manifest
    containing TODO fails validation — so a draft can never reach the shelf."""
    adapter = api.ADAPTERS.get(slug)
    if adapter is None:
        raise KeyError("unknown dataset %r; one of %s" % (slug, sorted(api.ADAPTERS)))
    ds = adapter.DATASET
    pres = getattr(adapter, "PRESENTATION", {})
    unit_key = None
    main = (pres.get("main_series") or [{}])[0]
    if "name_ja" in main:
        unit_key, unit = "index", "index"
    else:
        unit_key, unit = "TODO", "TODO"
    lines = []
    w = lines.append
    w("MANIFEST = {")
    w('    "id": DATASET["slug"],')
    w('    "section": "TODO",  # one of %s' % SECTION_IDS)
    w('    "name": {"en": "TODO", "ja": "TODO"},')
    w('    "shape": "series",')
    w('    "summary": "TODO — one plain sentence.",')
    w('    "source": {')
    w('        "publisher": DATASET["agency"],')
    w('        "publisher_ja": DATASET.get("agency_ja"),')
    w('        "document": SOURCE["name"],')
    w('        "url": SOURCE["url"],')
    if pres.get("credit_line"):
        w('        "credit": PRESENTATION["credit_line"],')
    else:
        w('        "credit": "TODO — Source: %s.",' % ds["agency"])
    w('        "license_note": SOURCE["license_note"],')
    w('    },')
    w('    "keys": ["series_code", "period"],')
    w('    "frequency": DATASET["frequency"],')
    w('    "vintage": {')
    w('        "unit": "release", "as_of_basis": "release-in-force",')
    w('        "as_of_supported": True,')
    w('        "history_from": "TODO — first period served, e.g. 1970-01",')
    w('        "stale_after_days": PRESENTATION["stale_after_days"],')
    w('    },')
    w('    "measures": [')
    w('        {"id": "index", "label": "TODO", "unit": "%s", "trust": "official"},' % unit)
    if unit_key != "TODO":
        for gen in ("yoy", "mom", "ann3m"):
            w('        {"id": "%s", "label": "TODO", "unit": "%%", "trust": "derived",' % gen)
            w('         "calc": %r},' % api._calc_for(gen, unit_key))
    w('        # TODO: every other number this dataset serves — official ones without')
    w('        # calc, calculated ones with the exact formula the API or the page uses.')
    w('    ],')
    w('    "endpoints": {')
    for role, leaf in (("series", "observations"), ("search", "series"),
                       ("releases", "releases"), ("revisions", "revisions")):
        w('        "%s": "/api/v1/%s/%s",' % (role, slug, leaf))
    w('    },')
    w('    "capabilities": ["series", "search"],')
    w('    "cite": "/TODO.html",')
    w('    "page": "/TODO.html",')
    w('    "notes": ["TODO"],')
    w("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

load()


def _main(argv):
    if argv[:1] == ["--check"]:
        # Tier B needs the built app; importing main builds it without serving.
        from .main import app
        os.environ.pop("MANIFEST_STRICT", None)
        try:
            bind(app)
        except RegistryError:
            pass
        if _ERRORS:
            print(report())
            print("\n%d manifest(s) registered, %d fault(s)" % (len(_ORDER), len(_ERRORS)))
            return 1
        print("%d manifests registered, every endpoint resolves: ok" % len(_ORDER))
        for mid in _sorted_ids():
            m = _REGISTRY[mid]
            print("  %-24s %-16s %-8s %2d measures  %s" % (
                mid, m["section"], m["shape"], len(m["measures"]),
                "available" if available(mid) else "not on this server"))
        return 0
    if argv[:1] == ["--scaffold"] and len(argv) == 2:
        print(scaffold(argv[1]))
        return 0
    print(__doc__.split("Usage:")[-1])
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
