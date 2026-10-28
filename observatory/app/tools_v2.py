# -*- coding: utf-8 -*-
"""Six generic tools over the dataset registry — the MCP v2 surface.

The v1 tools in tools.py are one hand-written function per question per
dataset; every new dataset meant new tools, new names, new instructions. Here
the *dataset* is an argument, resolved through its manifest (registry.py), and
the six tools cover every dataset the registry knows:

    list_datasets      what is published, compactly
    describe_dataset   one dataset's card plus live coverage
    search             companies and series, across datasets, one ranked list
    get_company        one company in one dataset — or, with no dataset, a
                       compact profile across every dataset that knows it
    get_series         a series' history (series-shaped datasets), with as_of
    screen             a ranked cross-section (the dataset's own screens)

Same layer discipline as tools.py: every tool dispatches to the functions
that serve /api/v1, so an assistant can only ever see numbers the public API
publishes, from the same release. Dataset number seventeen needs a manifest
and a row in the dispatch tables below — not a tool.

Every result is the same envelope:

    tool · dataset · data · provenance · calc · vintage · cite · coverage
    (· truncated, when a row budget cut the data)

No-data is a valid answer, never a JSON-RPC error: a company with no rows in a
dataset comes back with `data: null` and a `missing` reason. Unknown dataset
ids and screen sorts answer with the valid list, so a caller can correct
itself in one step.
"""
import array
import bisect
import datetime
import importlib
import json
import re
import threading

from . import api, asof, db, place_en, registry
from .tools import (DEFAULT_MONTHS, POINT_BUDGET, _cite, _fail, _record,
                    _release_of, _trim_points, _window_start, call_api)

# Tool results are an assistant's input tokens. Lists longer than this are
# cut, and the envelope says so and by how much.
ROW_BUDGET = 50
MAX_LIMIT = 100
SEARCH_LIMIT = 25
# Long enough for a US filer written `cik:NNNNNNNNNN`; a Japanese code is 4.
COMPANY_CODE_MAX = 16


def _mod(name):
    return importlib.import_module("." + name, __package__)


def _fmt_date(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def _parse_as_of(as_of):
    """None, a date, or an error string."""
    if not as_of:
        return None, None
    try:
        return datetime.date.fromisoformat(str(as_of)), None
    except ValueError:
        return None, "as_of must be a date, YYYY-MM-DD (got %r)" % as_of


def _detail(exc):
    """The message of an HTTPException or any other failure."""
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else str(exc)


def _status(exc):
    return getattr(exc, "status_code", None)


# ---------------------------------------------------------------------------
# Dispatch — which function answers which capability for which dataset.
#
# Only equity datasets need a table: their read functions are module-specific.
# Every series dataset goes through the generic api.py surface. Each entry is
# a callable taking keyword arguments the tool already validated.
# ---------------------------------------------------------------------------

def _bound(mid, attr):
    return registry.bound(mid, attr)


def _company_fn(mid):
    return _bound(mid, "company")


def _summary_fn(mid):
    return _bound(mid, "summary")


def _search_fn(mid):
    return _bound(mid, "companies")


def _eq(name):
    return _mod(name)


def _screen_cross_shareholdings(sort, f, limit):
    if sort == "reclassified":
        return _eq("equity_api").reclassified(year=f.get("year", ""), limit=limit)
    return _eq("equity_api").unwind(year=f.get("year", ""))


def _screen_register(sort, f, limit):
    return call_api(_eq("ownership_api").screen,
        metric=sort, order=f.get("order", "desc"), year=f.get("year", ""),
        listed=f.get("listed", "true"), min_shareholders=int(f.get("min_shareholders", 0) or 0),
        cohort=str(f.get("cohort", "") or ""), limit=limit)


def _screen_stakes(sort, f, limit):
    return _eq("lvh_api").holders(
        limit=limit, by="entity" if sort == "entity" else "group",
        filer_type=f.get("filer_type", ""), group=f.get("group", ""),
        activist="true" if sort == "activist" or f.get("activist") else "")


def _screen_shorts(sort, f, limit):
    m = _eq("short_api")
    if sort == "holders":
        return m.holders(limit=limit)
    if sort == "recent":
        return call_api(m.recent, limit=limit,
                        min_ratio=float(f.get("min_ratio", 0) or 0),
                        min_change=float(f.get("min_change", 0) or 0),
                        closing=str(f.get("closing", "") or ""))
    return call_api(m.companies, q=f.get("q", ""),
                    sort=str(f.get("sort", "disclosed_pct") or "disclosed_pct"),
                    limit=limit)


def _screen_boards(sort, f, limit):
    return call_api(_eq("governance_api").screen, metric=sort, year=f.get("year", ""),
                    listed=f.get("listed", ""),
                    cohort=str(f.get("cohort", "") or ""), limit=limit)


def _screen_buybacks(sort, f, limit):
    return call_api(_eq("buyback_api").programs,
        lifecycle=f.get("lifecycle", ""), q=f.get("q", ""),
        min_authorised_yen=float(f.get("min_authorised_yen", 0) or 0), sort=sort, limit=limit)


def _screen_facilities(sort, f, limit):
    m = _eq("facility_api")
    if sort.startswith("rental_"):
        return m.rental_ranking(metric=sort[len("rental_"):], year=f.get("year", ""),
                                limit=limit)
    return m.ranking(metric=sort, year=f.get("year", ""), limit=limit)


# `cohort` is handled separately: it is not a financials filter but the shared
# peer-group narrowing every screen takes (app/cohorts.py).
_SCREENER_FILTERS = ("industry", "standard", "min_revenue_yen", "min_assets_yen",
                     "roe_min", "roe_max", "roa_min", "operating_margin_min",
                     "equity_ratio_min", "equity_ratio_max", "revenue_growth_min",
                     "pbr_implied_max", "dividend_yield_min", "cash_to_assets_min",
                     "net_cash_to_assets_min")


def _screen_financials(sort, f, limit):
    m = _eq("financials_api")
    if sort.startswith("filed:"):
        return m.screen(metric=sort[len("filed:"):], year=f.get("year", ""), limit=limit)
    kw = dict((k, str(f.get(k, "") or "")) for k in _SCREENER_FILTERS)
    return m.screener(sort=sort, order=f.get("order", "desc"),
                      cohort=str(f.get("cohort", "") or ""),
                      limit=limit, offset=0, **kw)


def _screen_agm(sort, f, limit):
    m = _eq("agm_api")
    if sort == "proposals":
        return m.proposals(category=f.get("category", ""),
                           shareholder="true" if f.get("shareholder") else "",
                           limit=limit, include_unverified="")
    kind = "dismissal" if sort == "dismissal" else "election"
    order = "highest" if sort in ("highest", "dismissal") else "lowest"
    return m.directors(limit=limit, order=order, max_pct=float(f.get("max_pct", 100) or 100),
                       sec_code=str(f.get("sec_code", "") or ""),
                       year=int(f.get("year", 0) or 0), kind=kind, include_unverified="")


def _screen_earnings(sort, f, limit):
    # One reporting period at a time: progress after three months and after
    # nine do not compare, so the period is a required part of the question.
    return _eq("earnings_api").screen(
        sort=sort, line=str(f.get("line", "operating_income") or "operating_income"),
        period=str(f.get("period", "q1") or "q1"),
        fiscal_period=str(f.get("fiscal_period", "") or ""),
        order=str(f.get("order", "desc") or "desc"), limit=limit)


def _screen_segments(sort, f, limit):
    m = _eq("segments_api")
    # One payload carries both views: filers ranked by their dependence on a
    # named customer, and customers by how many suppliers name them.
    return m.concentration(min_share=float(f.get("min_share", 0) or 0))


def _screen_banks(sort, f, limit):
    m = _eq("bank_api")
    if sort == "irrbb":
        return m.irrbb(fi_type=str(f.get("fi_type", "") or ""),
                       basis=str(f.get("basis", "consolidated") or "consolidated"), limit=limit)
    return m.ranking(metric=sort, year=f.get("year", ""), limit=limit)


def _screen_sec(sort, f, limit):
    m = _eq("sec_api")
    # Ratios (roe_pct, operating_margin_pct …) and the universe filters live on
    # /screener; a bare key-indicator rank by fiscal year stays on /screen.
    if sort not in m.SCREEN_FIELDS or any(k in f for k in ("sic", "industry", "exchange", "tickers")):
        return call_api(m.screener, sic=str(f.get("sic", "") or ""), industry=str(f.get("industry", "") or ""),
                          exchange=str(f.get("exchange", "") or ""),
                          tickers=str(f.get("tickers", "") or ""),
                          unit=str(f.get("unit", "USD") or "USD"),
                          min_revenue=str(f.get("min_revenue", "") or ""),
                          sort=sort, order=str(f.get("order", "desc") or "desc"), limit=limit)
    return m.screen(metric=sort, fy=str(f.get("fy", "") or ""),
                    order=str(f.get("order", "desc") or "desc"),
                    unit=str(f.get("unit", "USD") or "USD"), limit=limit)


def _screen_fns():
    return {
        "sec-financials": _screen_sec,
        "segments": _screen_segments,
        "cross-shareholdings": _screen_cross_shareholdings,
        "shareholder-register": _screen_register,
        "large-shareholdings": _screen_stakes,
        "short-positions": _screen_shorts,
        "boards-and-pay": _screen_boards,
        "buybacks": _screen_buybacks,
        "facilities": _screen_facilities,
        "financials": _screen_financials,
        "earnings-releases": _screen_earnings,
        "agm-votes": _screen_agm,
        "bank-balance": _screen_banks,
    }


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

def _provenance(m):
    src = m["source"]
    return {"trust": "official", "publisher": src["publisher"], "credit": src["credit"],
            "document": src["document"], "note": ("Official values are exactly as "
                                                    "published or filed; every calculated "
                                                    "figure is listed under `calc` with its "
                                                    "formula.")}


def _calc(m, only=None):
    out = {}
    for meas in m["measures"]:
        if meas["trust"] == "derived" and (only is None or meas["id"] in only):
            out[meas["id"]] = meas["calc"]
    return out


def _macro_vintage(dataset, as_of):
    con = api._con()
    try:
        rel = api._release(con, dataset, as_of=as_of)
    finally:
        con.close()
    return {"unit": "release", "basis": "release-in-force",
            "as_of": as_of.isoformat() if as_of else None,
            "release_id": rel["release_id"], "label": rel["label"],
            "latest_period": rel["latest_period"],
            "published_at": _fmt_date(rel["ingested_at"]),
            "source_sha256": rel["sha256"]}, rel


def _equity_vintage(as_of):
    return asof.vintage()


def _cite_for(m, **params):
    path = m["cite"]
    for k, v in params.items():
        path = path.replace("{%s}" % k, str(v))
        if k == "sec_code":                 # the US shelf keys on a CIK
            path = path.replace("{cik}", str(v).split(":")[-1])
    return _cite(path)


def _budget(data, limit):
    """Cut every top-level list to `limit` rows; report what was cut."""
    truncated = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list) and len(v) > limit:
                truncated[k] = {"returned": limit, "total": len(v)}
                data[k] = v[:limit]
    return truncated


def _envelope(tool, m, data, calc=None, vintage=None, cite=None, coverage=None,
              truncated=None, missing=None, **extra):
    out = {"tool": tool, "dataset": m["id"], "section": m["section"],
           "shape": m["shape"], "data": data,
           "provenance": _provenance(m),
           "calc": calc if calc is not None else _calc(m),
           "vintage": vintage, "cite": cite or _cite_for(m),
           "coverage": coverage}
    if truncated:
        out["truncated"] = truncated
    if missing:
        out["missing"] = missing
    out.update(extra)
    return out


def _unknown_dataset(dataset):
    return _fail("Unknown dataset %r. Valid ids: %s" % (dataset, ", ".join(registry.ids())))


def _manifest_or_fail(dataset):
    m = registry.get(dataset)
    if m is None:
        return None, _unknown_dataset(dataset)
    return m, None


def _unavailable(m):
    return _fail("The %s dataset is not published on this server yet." % m["id"])


# ---------------------------------------------------------------------------
# The six tools
# ---------------------------------------------------------------------------

def _compact_row(m):
    row = {"id": m["id"], "section": m["section"], "name": m["name"]["en"],
           "name_ja": m["name"]["ja"], "shape": m["shape"], "summary": m["summary"],
           "frequency": m["frequency"], "capabilities": m["capabilities"],
           "available": m.get("available", registry.available(m["id"])),
           "history_from": m["vintage"]["history_from"],
           "as_of_supported": m["vintage"]["as_of_supported"],
           "screens": [s["id"] for s in m.get("screens", [])]}
    if m["shape"] == "series" and row["available"]:
        try:
            row["latest_period"] = _release_of(m["id"])["latest_period"]
        except Exception:  # noqa: BLE001 — coverage is a convenience, not a gate
            pass
    return row


def list_datasets(section=""):
    """Every published dataset, compactly — ids, shapes, capabilities, coverage."""
    _record("list_datasets", {"section": section or None})
    section = (section or "").strip()
    if section and section not in registry.SECTION_IDS:
        return _fail("Unknown section %r. Valid sections: %s"
                     % (section, ", ".join(registry.SECTION_IDS)))
    rows = [_compact_row(m) for m in registry.datasets()
            if not section or m["section"] == section]
    return _dumps({"tool": "list_datasets", "count": len(rows),
                   "sections": registry.by_section() if not section else section,
                   "datasets": rows, "cite": _cite("/")})


def _scalars(d):
    return dict((k, _fmt_date(v)) for k, v in d.items()
                if not isinstance(v, (list, dict)) and not k.endswith("_note"))


def describe_dataset(dataset):
    """One dataset's manifest — source, measures, formulas, endpoints — plus
    what this server actually holds of it."""
    _record("describe_dataset", {"dataset": dataset})
    m, err = _manifest_or_fail(dataset)
    if err:
        return err
    m["available"] = registry.available(dataset)
    coverage = None
    if m["available"]:
        try:
            if m["shape"] == "series":
                rel = _release_of(dataset)
                coverage = {"from": rel.get("coverage_start"), "to": rel["latest_period"],
                            "release": rel["label"], "published_at": _fmt_date(rel["ingested_at"])}
            else:
                fn = _summary_fn(dataset) or _bound(dataset, "coverage")
                coverage = _scalars(call_api(fn)) if fn else None
        except Exception as exc:  # noqa: BLE001 — the card still describes the dataset
            coverage = {"error": _detail(exc)}
    m["coverage"] = coverage
    m["tools"] = {
        "get_series": m["shape"] == "series",
        "get_company": "company" in m["capabilities"],
        "screen": "screen" in m["capabilities"],
        "search": "search" in m["capabilities"] or m["shape"] == "series",
    }
    m["cite"] = _cite_for(m)
    return _dumps(m)


def _series_labels(dataset):
    """The plain names a dataset's page gives its key series ("Headline CPI",
    "Core CPI (less fresh food)"), keyed by code or official Japanese name."""
    pres = getattr(api.ADAPTERS.get(dataset), "PRESENTATION", None) or {}
    out = {}
    for r in pres.get("main_series") or []:
        key = r.get("code") or r.get("name_ja")
        if key and r.get("label"):
            out[key] = r["label"]
    return out


# A search that names only the dataset ("CPI", "unemployment") lists this
# many of its series, key series first.
DATASET_HIT_SERIES = 3


# Every dataset's series names, ready to search, held in memory until the
# database file changes (db.file_version, the same stamp the response cache
# uses). Reading them afresh on every search took ~2s here and over 10s on the
# live server: one list has ~585,000 series, ~1 GB as Python dicts.
#
# So each dataset is kept compact: one lowercased UTF-8 block of every
# series' searchable text (English name, Japanese name, the page's own label
# for it), one block of codes, and where each row starts in both. A search is
# a substring find in C; the full details (names as shown, unit) are read from
# the database only for the few series a search returns. Byte search on UTF-8
# is a correct character search: the encoding never matches mid-character.
#
# 2026-10-09: a changed file used to drop every block at once, and the next
# searches rebuilt them while the reader waited — after a deploy, when the
# backfill swaps the file again and again, searches timed out at 15s, and a
# search-as-you-type box sent several that each rebuilt the same blocks. Now
# the old block keeps answering while one background worker rebuilds the
# stale ones in turn; only a dataset never built is built on the spot, once,
# however many searches ask for it at the same time.
_SEARCHABLE = {"maps": {}, "building": {}, "refresher": None}
_SEARCHABLE_LOCK = threading.Lock()
# Rows a search looks at per dataset and rank before it stops (it needs the
# first few in each; the count it reports is then a floor, flagged truncated).
SCAN_CAP = 200


def _build_searchable(dataset):
    m = registry.get(dataset) or {}
    labels = _series_labels(dataset)
    text, codes = bytearray(b"\n"), bytearray(b"\n")
    starts, code_starts = array.array("q"), array.array("q")
    labelled = []
    con = api._con()
    try:
        cur = con.execute("SELECT code, name_en, name_ja FROM series WHERE dataset=? "
                          "ORDER BY sort_order", [dataset])
        i = 0
        while True:
            batch = cur.fetchmany(5000)
            if not batch:
                break
            for code, name_en, name_ja in batch:
                # the English label as every page shows it (see api._series_map)
                name_en = place_en.series_name_en(dataset, code, name_en)
                label = labels.get(code) or labels.get(name_ja) or ""
                if label:
                    labelled.append(i)
                starts.append(len(text))
                text += " ".join([name_en or "", name_ja or "", label]).lower().encode("utf-8") + b"\n"
                code_starts.append(len(codes))
                codes += code.lower().encode("utf-8") + b"\n"
                i += 1
    finally:
        con.close()
    name = m.get("name") or {}
    section = next((s["label"] for s in registry.SECTIONS if s["id"] == m.get("section")), "")
    # kept as bytearrays: find() works the same, and a bytes() copy of an
    # 80 MB block would double it while it is made
    return {"text": text, "starts": starts, "codes": codes,
            "code_starts": code_starts, "labelled": labelled,
            "keys": _key_rows(dataset, labelled, codes, code_starts),
            "market": _market(dataset, m),
            "about": " ".join([dataset.replace("-", " "), name.get("en") or "",
                               name.get("ja") or ""]).lower(),
            "section": " ".join([m.get("section") or "", section]).replace("-", " ").lower(),
            "summary": (m.get("summary") or "").lower()}


def _market(dataset, m):
    """The market a dataset covers: "us" for the US shelf, else "jp". There is
    no market field on a manifest; the US datasets all say so in their id or
    name ("ust-yields", "cpi-us", "US average retail prices")."""
    en = (m.get("name") or {}).get("en") or ""
    if (dataset.startswith(("us-", "ust-", "cpi-us")) or m.get("section") == "us-reference"
            or en.startswith(("US ", "Atlanta Fed"))):
        return "us"
    return "jp"


def _key_rows(dataset, labelled, codes, code_starts):
    """{row: place} for a dataset's key series: those its page labels, else a
    yield curve's history series with the 10-year first."""
    if labelled:
        return dict((r, i) for i, r in enumerate(labelled))
    curve = (getattr(api.ADAPTERS.get(dataset), "PRESENTATION", None) or {}).get("curve") or {}
    want = sorted(curve.get("history_series") or [], key=lambda c: c != "10Y")
    out = {}
    for i, code in enumerate(want):
        pos = codes.find(b"\n" + code.lower().encode("utf-8") + b"\n")
        if pos != -1:
            out[bisect.bisect_right(code_starts, pos + 1) - 1] = i
    return out


def _searchable(dataset):
    version = db.file_version()
    with _SEARCHABLE_LOCK:
        held = _SEARCHABLE["maps"].get(dataset)
        if held is not None:
            if held[0] != version and _SEARCHABLE["refresher"] is None:
                t = threading.Thread(target=_refresh_stale, name="search-refresh", daemon=True)
                _SEARCHABLE["refresher"] = t
                t.start()
            return held[1]
        job = _SEARCHABLE["building"].get(dataset)
        mine = job is None
        if mine:
            job = _SEARCHABLE["building"][dataset] = threading.Event()
    if not mine:
        job.wait(300)
        with _SEARCHABLE_LOCK:
            held = _SEARCHABLE["maps"].get(dataset)
        if held is not None:
            return held[1]
        # the build that was running failed: build here, so the error is ours
        return _build_searchable(dataset)
    try:
        entry = _build_searchable(dataset)
        with _SEARCHABLE_LOCK:
            _SEARCHABLE["maps"][dataset] = (version, entry)
        return entry
    finally:
        with _SEARCHABLE_LOCK:
            _SEARCHABLE["building"].pop(dataset, None)
        job.set()


def _refresh_stale():
    """Rebuild, one at a time, every block built from an older file, until
    none is. A block that fails to build keeps its old contents (stamped as
    current, so it is not retried until the file changes again)."""
    try:
        while True:
            version = db.file_version()
            with _SEARCHABLE_LOCK:
                stale = [d for d, (v, _) in _SEARCHABLE["maps"].items() if v != version]
                if not stale:
                    _SEARCHABLE["refresher"] = None
                    return
            for dataset in stale:
                try:
                    entry = _build_searchable(dataset)
                except Exception:  # noqa: BLE001 — a refresh must never stop the server
                    entry = None
                with _SEARCHABLE_LOCK:
                    old = _SEARCHABLE["maps"].get(dataset)
                    _SEARCHABLE["maps"][dataset] = (version, entry or (old and old[1]))
    finally:
        with _SEARCHABLE_LOCK:
            if _SEARCHABLE["refresher"] is threading.current_thread():
                _SEARCHABLE["refresher"] = None


def warm_search():
    """Build every dataset's search block once, so the first search after a
    start is fast. Run in a background thread at boot; a dataset that fails is
    left to build on first use."""
    for mid in registry.ids():
        m = registry.get(mid) or {}
        if m.get("shape") == "series" and registry.available(mid):
            try:
                _searchable(mid)
            except Exception:  # noqa: BLE001 — warming must never stop the server
                pass


# ---- reading a query
#
# 2026-10-09: "US rates" listed BoJ loan rates at trust banks — "us" was found
# inside "Trust", "rates" in the BoJ dataset's name — and "US CPI" listed
# Japanese housing ("Housing"). So words now match at the start of a word, a
# market word picks the market's datasets, and a reader's word also finds the
# word the data uses for it ("rates" → "yield").

# Words that name a market rather than a series.
MARKET_WORDS = {"us": "us", "u.s.": "us", "u.s": "us", "usa": "us", "america": "us",
                "american": "us", u"米国": "us", u"アメリカ": "us",
                "japan": "jp", "japanese": "jp", "jp": "jp", u"日本": "jp"}
# How a series name says the market itself (a trade partner, a US city average).
MARKET_ROW_FORMS = {"us": ("united states", "u.s."), "jp": ("japan",)}
# Words the data uses for what a reader types, keyed by the reader's word
# (singular): the curve pages say "yield" where a reader says "rates".
SEARCH_SYNONYMS = {
    "rate": ("yield",),
    "interest": ("rate", "yield"),
    "bond": ("jgb", "treasury", "yield"),
    "ust": ("treasury",),
    "tips": ("real yield",),
    "inflation": ("cpi", "consumer price"),
    "job": ("employment",),
    "wage": ("earnings",),
    "salary": ("wage", "earnings"),
    "pay": ("wage", "earnings"),
    "tourist": ("visitor", "guest"),
    "tourism": ("visitor", "guest"),
    "boj": ("bank of japan",),
    "gdp": ("gross domestic product",),
    "lending": ("loan",),
    "credit": ("loan",),
    "jobless": ("unemployment",),
    u"金利": (u"利回り", u"イールド"),
}
_QUERY_FILLER = {"the", "of", "and", "in", "for", "a", "an", "to", "on", "by", "vs",
                 "versus", "with", "from"}
# "10y", "10 yr", "10-year", "3m": a maturity, written as the names write it
_TENOR = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*(y|yr|yrs|year|years|m|mo|month|months)\b")
# a word after "less" or "excluding" is what a series leaves out: "food" is
# not a match for "All items, less fresh food"
_EXCLUSION = re.compile(rb"(?:^|[^a-z0-9])(?:less|excluding|except|ex)\s")
_CLAUSE_ENDS = (b",", b";", b"(", b")", b"  ", b"\n")
# finds a search looks at before it gives up on a word that is mostly found
# inside other words (two letters typed into a search-as-you-type box)
PROBE_CAP = SCAN_CAP * 100


def _stem(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")) \
            and word != "tips":
        return word[:-1]
    return word


def _read_query(needle):
    """A query as terms, each matched on its own: {forms, syn, code, market}.
    `forms` are the words themselves, `syn` the data's words for them, `code`
    a maturity's series code ("10y"), `market` "us" or "jp" for a market word."""
    q = " ".join(needle.lower().split())
    terms, content, markets = [], [], []
    if "united states" in q:
        q = q.replace("united states", " ")
        markets.append("us")

    def tenor(m):
        kind = "year" if m.group(2).startswith("y") else "month"
        terms.append({"forms": ("%s-%s" % (m.group(1), kind), "%s %s" % (m.group(1), kind)),
                      "syn": (), "code": m.group(1) + kind[0], "market": None})
        content.append("%s-%s" % (m.group(1), kind))
        return " "

    q = _TENOR.sub(tenor, q)
    for w in re.split(r"[\s,/]+", q):
        w = w.strip(".,;:()\"'") if w not in ("u.s.",) else w
        if not w or w in _QUERY_FILLER:
            continue
        if w in MARKET_WORDS:
            markets.append(MARKET_WORDS[w])
            continue
        s = _stem(w)
        terms.append({"forms": (s,), "syn": SEARCH_SYNONYMS.get(s, ()), "code": None,
                      "market": None})
        content.append(w)
    for mk in sorted(set(markets)):
        terms.append({"forms": MARKET_ROW_FORMS[mk], "syn": (), "code": None, "market": mk})
    return {"terms": terms, "code": needle.strip().lower(),
            "phrase": " ".join(content) if len(content) > 1 else ""}


def _word_at(text, needle, lo, hi):
    """True when needle (bytes) starts a word in text[lo:hi] somewhere other
    than after "less" or "excluding"."""
    pos = text.find(needle, lo, hi)
    while pos != -1:
        if _starts_word(text, pos, lo) and not _excluded(text, pos, lo):
            return True
        pos = text.find(needle, pos + 1, hi)
    return False


def _starts_word(text, pos, lo=0):
    # bytes of a Japanese character are >= 0x80: kana and kanji have no spaces,
    # so anything after them starts a word
    if pos <= lo:
        return True
    c = text[pos - 1]
    return not (48 <= c <= 57 or 97 <= c <= 122)


def _excluded(text, pos, lo):
    # back to the start of the clause, or of the field: a row is its English
    # name, its Japanese name and its label, so a Japanese character ends one
    seg = bytes(text[max(lo, pos - 60):pos])
    cut = max([seg.rfind(e) for e in _CLAUSE_ENDS] +
              [i for i in range(len(seg) - 1, -1, -1) if seg[i] >= 0x80][:1])
    return bool(_EXCLUSION.search(seg[cut + 1:] if cut != -1 else seg))


def _rows_with(entry, word, cap):
    """Indices of the first `cap` rows where `word` starts a word, in order."""
    text, starts = entry["text"], entry["starts"]
    needle = word.encode("utf-8")
    found = []
    probes = 0
    pos = text.find(needle)
    while pos != -1 and len(found) < cap and probes < PROBE_CAP:
        probes += 1
        row = bisect.bisect_right(starts, pos) - 1
        if _starts_word(text, pos) and not _excluded(text, pos, starts[row]):
            found.append(row)
            # carry on from the start of the next row: one hit per row
            pos = text.find(needle, starts[row + 1] if row + 1 < len(starts) else len(text))
        else:
            pos = text.find(needle, pos + 1)
    return found


def _row_text(entry, row):
    starts = entry["starts"]
    end = starts[row + 1] - 1 if row + 1 < len(starts) else len(entry["text"]) - 1
    return entry["text"][starts[row]:end]


def _row_code(entry, row):
    cs = entry["code_starts"]
    end = cs[row + 1] - 1 if row + 1 < len(cs) else len(entry["codes"]) - 1
    return entry["codes"][cs[row]:end].decode("utf-8")


def _dataset_weight(entry, term):
    """How well the dataset itself answers a term: 3 its section ("Rates"),
    2 its id or name, 1 its summary; one less for a synonym; 2 for a market
    word naming its market."""
    if term["market"]:
        return 2 if entry["market"] == term["market"] else 0
    best = 0
    for forms, less in ((term["forms"], 0), (term["syn"], 1)):
        for f in forms:
            f = f.encode("utf-8")
            for key, w in (("section", 3), ("about", 2), ("summary", 1)):
                hay = entry[key].encode("utf-8")
                if _word_at(hay, f, 0, len(hay)):
                    best = max(best, w - less)
                    break
    return best


def _row_weight(text, lo, hi, term):
    """How well one series' own name answers a term: 3 the word, 2 a synonym;
    a market named in a series (a trade partner) only 1."""
    for forms, w in ((term["forms"], 1 if term["market"] else 3), (term["syn"], 2)):
        for f in forms:
            if _word_at(text, f.encode("utf-8"), lo, hi):
                return w
    return 0


def _series_hits(dataset, query, limit, order=0):
    """Series answering a query read by _read_query, as (key, 0, dataset, row)
    with key sorting best first. Every term must be answered, by the series'
    name or by the dataset itself (its section, name or summary, or its
    market): series are "All items", not "All items CPI", so "CPI" lives in
    the dataset. A term answered by both scores more ("Treasury" in a
    Treasury curve beats the BoJ's "Treasury discount bills"). Ties go to the
    main dataset of a family (`order`), then its key series, then the
    publisher's own order, which puts totals first: "Food" before "Fresh
    food", all Japan before Mie. A dataset that answers the whole query
    by itself adds its key series only (DATASET_HIT_SERIES)."""
    entry = _searchable(dataset)
    terms = query["terms"]
    if not terms:
        return []
    text, starts = entry["text"], entry["starts"]
    n = len(starts)
    ds = [_dataset_weight(entry, t) for t in terms]

    code_row = None
    pos = entry["codes"].find(b"\n" + query["code"].encode("utf-8") + b"\n")
    if pos != -1:
        code_row = bisect.bisect_right(entry["code_starts"], pos + 1) - 1

    uncovered = [t for t, w in zip(terms, ds) if not w]
    seeds = [code_row] if code_row is not None else []
    if uncovered:
        # rows that answer the most specific term the dataset does not
        lead = next((t for t in uncovered if not t["market"]), uncovered[0])
        for f in lead["forms"] + lead["syn"]:
            seeds.extend(_rows_with(entry, f, SCAN_CAP * 10))
    else:
        seeds.extend(entry["keys"])
        seeds.extend(range(min(n, DATASET_HIT_SERIES * 4)))
        for t in terms:
            for f in t["forms"] + t["syn"]:
                seeds.extend(_rows_with(entry, f, SCAN_CAP))

    phrase = query["phrase"].encode("utf-8")
    named, loose = [], []
    for row in set(seeds):
        lo = starts[row]
        hi = starts[row + 1] - 1 if row + 1 < n else len(text) - 1
        score = in_row = 0
        for t, w in zip(terms, ds):
            rw = _row_weight(text, lo, hi, t)
            if not rw and not w:
                break
            in_row += bool(rw)
            # a market answered by the dataset counts once: "All Japan —
            # occupancy rate" is not more Japanese than the JGB curve
            score += max(rw, w) + (1 if rw and w and not t["market"] else 0)
            if t["code"] and _row_code(entry, row) == t["code"]:
                score += 2
        else:
            if row == code_row:
                score += 10
            if phrase and _word_at(text, phrase, lo, hi):
                score += 2
            key = (-score, order, entry["keys"].get(row, 99), row)
            (named if in_row or row == code_row else loose).append(key)
    loose.sort()
    hits = sorted(named + loose[:DATASET_HIT_SERIES])
    return [(k, 0, dataset, k[-1]) for k in hits[:max(limit, 1) * 4]]


def _spread(hits, per_dataset=5):
    """Best first, but among equally good hits no more than `per_dataset`
    from one dataset before the others have had their turn: "US rates" shows
    the real yields after five of the nominal curve, not after all fourteen."""
    seen = {}
    tiers = []
    for i, h in enumerate(hits):
        seen[(h[0][0], h[2])] = seen.get((h[0][0], h[2]), 0) + 1
        tiers.append((h[0][0], seen[(h[0][0], h[2])] > per_dataset, i))
    return [hits[t[2]] for t in sorted(tiers)]


def _series_details(picked):
    """Full rows, names as every page shows them, for (dataset, row) hits."""
    by_dataset = {}
    for _, _, dataset, row in picked:
        by_dataset.setdefault(dataset, []).append(row)
    found = {}
    con = api._con()
    try:
        for dataset, rows in by_dataset.items():
            entry = _searchable(dataset)
            codes = [_row_code(entry, r) for r in rows]
            for code, name_en, name_ja, unit in con.execute(
                    "SELECT code, name_en, name_ja, unit FROM series WHERE dataset=? AND "
                    "lower(code) IN (%s)" % ",".join("?" * len(codes)), [dataset] + codes).fetchall():
                found[(dataset, code.lower())] = {
                    "kind": "series", "dataset": dataset, "code": code,
                    "name_en": place_en.series_name_en(dataset, code, name_en),
                    "name_ja": name_ja, "unit": unit}
    finally:
        con.close()
    out = []
    for _, _, dataset, row in picked:
        hit = found.get((dataset, _row_code(_searchable(dataset), row)))
        if hit:
            out.append(hit)
    return out


def _company_hits(dataset, needle, limit):
    fn = _search_fn(dataset)
    if fn is None:
        return []
    raw = call_api(fn, q=needle)
    out = []
    for c in raw.get("companies", [])[:limit]:
        out.append({"kind": "company", "dataset": dataset,
                    "sec_code": c.get("sec_code"),
                    "name": c.get("name") or c.get("filer_name"),
                    "name_en": c.get("name_en") or c.get("filer_name_en")})
    return out


def search(query, dataset="", limit=SEARCH_LIMIT):
    """Find companies (by name or securities code) and series (by name or
    code) across datasets — one ranked list, each hit saying where it lives."""
    args = {"query": query, "dataset": dataset or None, "limit": limit}
    _record("search", args)
    needle = (query or "").strip()
    if not needle:
        return _fail("Give a query: a company name, a securities code, or a series name.")
    limit = max(1, min(int(limit or SEARCH_LIMIT), MAX_LIMIT))
    targets = registry.ids()
    if dataset:
        m, err = _manifest_or_fail(dataset)
        if err:
            return err
        targets = [dataset]
    companies = {}
    series = []
    failed = []
    query = _read_query(needle)
    # among equal hits, a family's main dataset first: cpi-jp before
    # cpi-jp-sa, ust-yields before ust-real-yields
    ranks = dict((mid, i) for i, mid in enumerate(sorted(targets, key=lambda d: (len(d), d))))
    for mid in targets:
        m = registry.get(mid)
        if not registry.available(mid):
            continue
        try:
            if m["shape"] == "series":
                series.extend(_series_hits(mid, query, limit, ranks[mid]))
            elif "search" in m["capabilities"]:
                for h in _company_hits(mid, needle, limit):
                    key = h["sec_code"] or h["name"]
                    row = companies.setdefault(key, {
                        "kind": "company", "sec_code": h["sec_code"], "name": h["name"],
                        "name_en": h["name_en"], "datasets": []})
                    row["datasets"].append(mid)
                    if not row["name_en"] and h["name_en"]:
                        row["name_en"] = h["name_en"]
        except Exception as exc:  # noqa: BLE001 — one dataset failing must not empty the list
            failed.append({"dataset": mid, "error": _detail(exc)})
    rows = sorted(companies.values(),
                  key=lambda r: (needle != (r["sec_code"] or ""), -len(r["datasets"]),
                                 r["name"] or ""))
    company_rows = rows[:limit]
    series = _spread(sorted(series, key=lambda h: h[0]))
    series_rows = _series_details(series[:limit])
    return _dumps({"tool": "search", "query": needle,
                   "companies": company_rows, "series": series_rows,
                   "count": {"companies": len(rows), "series": len(series)},
                   "truncated": len(rows) > limit or len(series) > limit,
                   "failed": failed or None,
                   "note": ("A company hit lists every dataset that knows it; pass one of "
                            "those ids to get_company. A series hit gives the code for "
                            "get_series."),
                   "cite": _cite("/")})


def _company_block(m, code, as_of, tool):
    """One dataset's answer for one company, as an envelope — or a missing block."""
    fn = _company_fn(m["id"])
    if fn is None:
        return None, "no company view"
    try:
        raw = call_api(fn, sec_code=code)
    except Exception as exc:  # noqa: BLE001
        status = _status(exc)
        if status == 404:
            return None, _detail(exc)
        if status == 503:
            return None, "not published on this server"
        raise
    return raw, None


def _compact(raw):
    """Facts only: scalars, plus how many rows each table has."""
    facts = _scalars(raw)
    tables = dict((k, len(v)) for k, v in raw.items() if isinstance(v, list))
    for k, v in raw.items():
        if isinstance(v, dict) and k not in ("calc", "provenance"):
            for kk, vv in v.items():
                if not isinstance(vv, (list, dict)):
                    facts["%s.%s" % (k, kk)] = _fmt_date(vv)
    return {"facts": facts, "tables": tables}


def get_company(code, dataset="", as_of="", limit=ROW_BUDGET):
    """One company. With a dataset: that dataset's full company view. Without:
    a compact profile from every dataset that knows the company, with a
    coverage matrix of which do and which do not."""
    args = {"code": code, "dataset": dataset or None, "as_of": as_of or None}
    _record("get_company", args)
    code = (code or "").strip()
    if not code or len(code) > COMPANY_CODE_MAX:
        return _fail("Give a securities code (e.g. 7974). Use search to find one.")
    p_as_of, err = _parse_as_of(as_of)
    if err:
        return _fail(err)
    limit = max(1, min(int(limit or ROW_BUDGET), MAX_LIMIT))

    if dataset:
        m, err = _manifest_or_fail(dataset)
        if err:
            return err
        if "company" not in m["capabilities"]:
            return _fail("%s has no company view; datasets with one: %s"
                         % (dataset, ", ".join(i for i in registry.ids()
                                               if "company" in registry.get(i)["capabilities"])))
        if not registry.available(dataset):
            return _unavailable(m)
        with asof.scope(p_as_of):
            raw, missing = _company_block(m, code, p_as_of, "get_company")
        if missing:
            return _dumps(_envelope("get_company", m, None, vintage=_equity_vintage(p_as_of),
                                    cite=_cite_for(m, sec_code=code), missing=missing,
                                    code=code))
        truncated = _budget(raw, limit)
        return _dumps(_envelope("get_company", m, raw, vintage=_equity_vintage(p_as_of),
                                cite=_cite_for(m, sec_code=code), truncated=truncated,
                                code=code))

    # Composed: the same document /api/v1/company/{code} serves, in its
    # compact form. One implementation, so the tool and the endpoint can never
    # disagree about what a company looks like.
    from . import company_api
    try:
        doc = company_api.compose(code, compact=True, as_of=p_as_of)
    except Exception as exc:  # noqa: BLE001
        return _fail(_detail(exc))
    doc["tool"] = "get_company"
    doc["cite"] = _cite(doc["cite"])
    for block in doc["datasets"].values():
        block["cite"] = _cite(block["cite"])
    return _dumps(doc)


def get_series(dataset, series, measure="index", start="", end="", as_of="",
               months=DEFAULT_MONTHS):
    """History for up to six series of a series-shaped dataset, as the
    published value or a rate of change, optionally as it stood on a date."""
    args = {"dataset": dataset, "series": series, "measure": measure,
            "start": start or None, "end": end or None, "as_of": as_of or None}
    m, err = _manifest_or_fail(dataset)
    if err:
        _record("get_series", args, note="unknown dataset")
        return err
    if m["shape"] != "series":
        _record("get_series", args, note="not a series dataset")
        return _fail("%s is %s-shaped; use get_company or screen. Series datasets: %s"
                     % (dataset, m["shape"],
                        ", ".join(i for i in registry.ids()
                                  if registry.get(i)["shape"] == "series")))
    if not registry.available(dataset):
        return _unavailable(m)
    codes = ",".join([c.strip() for c in (series or "").split(",") if c.strip()][:6])
    if not codes:
        return _fail("Give one to six series codes, comma-separated. Use search to find them.")
    measure = (measure or "index").strip()
    known = [x["id"] for x in m["measures"] if x["id"] in registry.GENERIC_MEASURES]
    if measure not in known:
        return _fail("Unknown measure %r for %s; one of %s" % (measure, dataset, ", ".join(known)))
    p_as_of, err = _parse_as_of(as_of)
    if err:
        return _fail(err)
    try:
        vintage, rel = _macro_vintage(dataset, p_as_of)
        from_month = start or _window_start(rel, int(months or DEFAULT_MONTHS))
        raw = call_api(api.observations, dataset=dataset, series=codes,
                       measure=measure, start=from_month, end=end or None,
                       as_of=p_as_of.isoformat() if p_as_of else None)
    except Exception as exc:  # noqa: BLE001
        _record("get_series", args, note="failed")
        return _fail(_detail(exc))
    dp = 4 if measure == "index" else 2
    out = [{"code": s["code"], "name_en": s["name_en"], "name_ja": s["name_ja"],
            "points": [[p, None if v is None else round(v, dp)] for p, v in s["points"]]}
           for s in raw["series"]]
    cut = _trim_points(out, POINT_BUDGET)
    _record("get_series", args, rel, note="%s, from %s" % (measure, from_month))
    trust = "official" if measure == "index" else "derived"
    return _dumps(_envelope(
        "get_series", m,
        {"measure": measure, "unit": raw["unit"], "trust": trust,
         "from": from_month, "to": end or rel["latest_period"], "series": out},
        calc={measure: raw["calc"]}, vintage=vintage,
        # The dataset's own page, from its manifest — /explorer.html was
        # hardcoded here and only ever knew the CPI item tables, so every
        # other series dataset cited a page that cannot render it.
        cite=_cite_for(m, dataset=dataset) if m.get("cite") else _cite("/"),
        coverage={"from": rel.get("coverage_start"), "to": rel["latest_period"]},
        truncated={"points": "trimmed to the most recent %d" % POINT_BUDGET} if cut else None))


def screen(dataset, sort="", filters=None, limit=ROW_BUDGET, as_of=""):
    """A ranked cross-section from one dataset's own screens — validated
    against the sorts its manifest declares."""
    args = {"dataset": dataset, "sort": sort or None, "filters": filters or None,
            "limit": limit, "as_of": as_of or None}
    _record("screen", args)
    m, err = _manifest_or_fail(dataset)
    if err:
        return err
    # A series dataset has no hand-written screens; it is ranked on the
    # numeric columns its own series listing publishes.
    if m["shape"] == "series":
        if not registry.available(dataset):
            return _unavailable(m)
        return _screen_series(m, dataset, sort, filters, limit)
    screens = m.get("screens", [])
    if "screen" not in m["capabilities"] or not screens:
        return _fail("%s has no screens; datasets with screens: %s"
                     % (dataset, ", ".join(i for i in registry.ids()
                                           if registry.get(i).get("screens"))))
    if not registry.available(dataset):
        return _unavailable(m)
    valid = [s["id"] for s in screens]
    sort = (sort or "").strip() or valid[0]
    if sort not in valid:
        return _fail("Unknown sort %r for %s. Valid sorts: %s"
                     % (sort, dataset, "; ".join("%s (%s)" % (s["id"], s["title"])
                                                 for s in screens)))
    if filters is not None and not isinstance(filters, dict):
        return _fail("filters must be an object of field → value.")
    p_as_of, err = _parse_as_of(as_of)
    if err:
        return _fail(err)
    limit = max(1, min(int(limit or ROW_BUDGET), MAX_LIMIT))
    fn = _screen_fns().get(dataset)
    if fn is None:
        return _fail("%s declares screens but none are bound yet." % dataset)
    try:
        with asof.scope(p_as_of):
            raw = fn(sort, filters or {}, limit)
    except Exception as exc:  # noqa: BLE001
        if _status(exc) == 503:
            return _unavailable(m)
        return _fail(_detail(exc))
    truncated = _budget(raw, limit)
    title = next(s["title"] for s in screens if s["id"] == sort)
    return _dumps(_envelope("screen", m, raw, vintage=_equity_vintage(p_as_of),
                            truncated=truncated, sort=sort, title=title,
                            filters=filters or {}))


# ---------------------------------------------------------------------------
# Cohorts — the seventh tool, and the only one that is not per-dataset
# ---------------------------------------------------------------------------
# A cohort is a peer group, not a dataset: the same TOPIX band or basket of
# codes narrows the financials screener, the board screen and the register
# screen alike. `screen` already takes it as filters.cohort. This adds the one
# thing the six generic tools cannot express — the shape of a cohort's
# distribution, which is what turns a company's number into a position.

def list_cohorts(as_of=""):
    """Every peer group that can be compared within, with member counts."""
    _record("list_cohorts", {"as_of": as_of or None})
    m = registry.get("financials") or {}
    try:
        raw = call_api(_mod("cohorts_api").catalogue, as_of=as_of or "")
    except Exception as exc:  # noqa: BLE001
        return _fail(_detail(exc))
    return _dumps({
        "tool": "list_cohorts",
        "data": raw,
        "how": ("Pass any `spec` as filters.cohort on `screen`, or as `cohort` on "
                "compare_cohort. A basket is a cohort too: codes:7203,6758,…"),
        "cite": "/cohorts.html",
    })


def compare_cohort(cohort, metric="roe_pct", highlight="", order="desc",
                   limit=ROW_BUDGET, as_of=""):
    """One metric across one peer group: every member ranked, with the
    cohort's own quartiles, and a named company's percentile within it."""
    _record("compare_cohort", {"cohort": cohort, "metric": metric,
                               "highlight": highlight or None, "order": order,
                               "limit": limit, "as_of": as_of or None})
    limit = max(1, min(int(limit or ROW_BUDGET), MAX_LIMIT))
    try:
        raw = call_api(_mod("cohorts_api").compare, cohort=cohort, metric=metric,
                       highlight=highlight or "", order=order or "desc",
                       limit=limit, as_of=as_of or "")
    except Exception as exc:  # noqa: BLE001
        return _fail(_detail(exc))
    return _dumps({
        "tool": "compare_cohort",
        "data": raw,
        "calc": raw.get("calc"),
        "provenance": raw.get("provenance"),
        "cite": "/cohorts.html?cohort=%s&metric=%s%s" % (
            cohort, metric, ("&c=" + highlight) if highlight else ""),
    })


# ---------------------------------------------------------------------------
# Registry of tools, and their MCP descriptors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Vintages, the headline reading, and the cuts a dataset serves beyond series
# ---------------------------------------------------------------------------

# PRESENTATION key that gates the surface -> (api function name, selector
# argument, one line saying what the cut answers). These are the endpoints
# under /api/v1/{dataset}/ that are not /observations: each is one dataset
# family's own extra cut of the same release, and each had no tool at all
# before — an assistant could read a trade dataset's series one code at a
# time but could not ask for a commodity's partner breakdown, which is the
# question the dataset exists to answer.
BREAKDOWNS = {
    "groups_ja": ("contributions", None,
                  "each major group's contribution, in percentage points, to the "
                  "headline year-on-year rate"),
    "breadth": ("breadth", None,
                "how widely prices are rising: the share of individually priced "
                "items above and below a year-on-year threshold"),
    "curve": ("curve", None,
              "the whole published yield curve — every date by every maturity"),
    "arrivals": ("arrivals", None,
                 "arrivals by market with the hierarchy that relates them"),
    "trade": ("trade", "commodity",
              "one commodity's trade with every partner country, plus world "
              "totals for every commodity"),
    "accommodation": ("accommodation", "area",
                      "one area's guest nights and occupancy in full, with the "
                      "backbone for all 58 areas"),
    "prefectures": ("prefectures", "prefecture",
                    "every prefecture by every measure and period, with the "
                    "geography a map needs"),
}
# Arguments a caller may pass through to a cut, by the api function that takes
# them. Anything else is refused by name rather than silently dropped.
_BREAKDOWN_ARGS = {
    "contributions": ("start", "end"),
    "breadth": ("threshold",),
    "curve": (),
    "arrivals": (),
    "trade": ("flow", "commodity"),
    "accommodation": ("area",),
    "prefectures": ("prefecture",),
}


def _breakdowns_for(dataset):
    """[(cut id, api function, what it answers)] the dataset actually serves."""
    adapter = api.ADAPTERS.get(dataset)
    if adapter is None:
        return []
    pres = adapter.PRESENTATION
    out = []
    for key, (fn, _sel, what) in BREAKDOWNS.items():
        if not pres.get(key):
            continue
        # Contributions need the headline as well as the groups.
        if fn == "contributions" and not pres.get("main_series"):
            continue
        out.append((fn, fn, what))
    return out


def get_vintages(dataset, series="", period=""):
    """The release history of a dataset, or how one series has been revised.

    The point-in-time store is the platform's reason to exist and it was the
    one thing the tool surface could not reach: `as_of` on get_series reads
    the data as it stood, but nothing could say which vintages exist or what
    changed between them — while every dataset card advertised /releases and
    /revisions as endpoints.
    """
    args = {"dataset": dataset, "series": series or None, "period": period or None}
    _record("get_vintages", args)
    m, err = _manifest_or_fail(dataset)
    if err:
        return err
    if not registry.available(dataset):
        return _unavailable(m)
    try:
        if series:
            raw = call_api(api.revisions, dataset=dataset, series=series.strip(),
                           period=period or None)
            data = {"mode": "revisions", "series": series.strip(),
                    "period": period or None, "revisions": raw}
            calc = raw.get("calc") if isinstance(raw, dict) else None
        else:
            raw = call_api(api.releases, dataset=dataset)
            rows = raw["releases"] if isinstance(raw, dict) and "releases" in raw else raw
            cut = _budget(rows, ROW_BUDGET)
            data = {"mode": "releases", "count": len(rows), "releases": rows}
            calc = None
    except Exception as exc:  # noqa: BLE001
        _record("get_vintages", args, note="failed")
        return _fail(_detail(exc))
    return _dumps(_envelope(
        "get_vintages", m, data,
        calc=calc if calc else {"releases": "Accepted releases as recorded at ingest, "
                                            "newest first. Not computed."},
        cite=_cite_for(m, dataset=dataset) if m.get("cite") else None))


def get_overview(dataset):
    """A dataset's headline reading — the tiles its own page leads with.

    The v1 tool of this name served cpi-jp alone; every other series dataset
    had no way to ask 'what is the current reading' short of pulling raw
    series and doing the arithmetic. This dispatches to the same function
    that serves /api/v1/{dataset}/overview, so it covers whichever shape the
    dataset is — index tiles or published levels.
    """
    args = {"dataset": dataset}
    _record("get_overview", args)
    m, err = _manifest_or_fail(dataset)
    if err:
        return err
    if m["shape"] != "series":
        return _fail("%s is a %s dataset; get_overview is for series datasets. "
                     "Use get_company or screen." % (dataset, m["shape"]))
    if not registry.available(dataset):
        return _unavailable(m)
    try:
        raw = call_api(api.overview, dataset=dataset)
    except Exception as exc:  # noqa: BLE001
        _record("get_overview", args, note="failed")
        return _fail(_detail(exc))
    rel = raw.get("release") or {}
    _record("get_overview", args, rel)
    data = dict((k, v) for k, v in raw.items() if k not in ("release", "dataset"))
    return _dumps(_envelope(
        "get_overview", m, data,
        calc=raw.get("calc") or _calc(m),
        vintage={"unit": "release", "basis": "release-in-force", "as_of": None,
                 "release_id": rel.get("release_id"), "label": rel.get("label"),
                 "latest_period": rel.get("latest_period"),
                 "published_at": rel.get("ingested_at"),
                 "source_sha256": rel.get("sha256")},
        cite=_cite_for(m, dataset=dataset) if m.get("cite") else None,
        coverage={"from": rel.get("coverage_start"), "to": rel.get("latest_period")}))


def get_breakdown(dataset, cut="", **params):
    """One of the dataset's own cuts: contributions, breadth, the curve,
    arrivals, a commodity's partners, an area's guest nights, the prefectures.

    With no `cut`, answers with the ones this dataset serves rather than
    failing, so a caller can correct itself in one step.
    """
    args = dict({"dataset": dataset, "cut": cut or None}, **params)
    _record("get_breakdown", args)
    m, err = _manifest_or_fail(dataset)
    if err:
        return err
    available = _breakdowns_for(dataset)
    if not available:
        return _fail("%s serves no breakdown beyond its series; use get_series." % dataset)
    names = [c for c, _fn, _w in available]
    if not cut:
        return _dumps({"tool": "get_breakdown", "dataset": dataset,
                       "cuts": [{"cut": c, "answers": w} for c, _fn, w in available],
                       "note": "Pass one of these as `cut`."})
    if cut not in names:
        return _fail("%s has no %r breakdown; it serves: %s"
                     % (dataset, cut, ", ".join(names)))
    if not registry.available(dataset):
        return _unavailable(m)
    allowed = _BREAKDOWN_ARGS.get(cut, ())
    extra = [k for k in params if k not in allowed]
    if extra:
        return _fail("%r takes %s; got %s"
                     % (cut, ", ".join(allowed) or "no arguments", ", ".join(sorted(extra))))
    kwargs = dict((k, v) for k, v in params.items() if v not in ("", None))
    try:
        raw = call_api(getattr(api, cut), dataset=dataset, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _record("get_breakdown", args, note="failed")
        return _fail(_detail(exc))
    rel = raw.get("release") if isinstance(raw, dict) else None
    data = dict((k, v) for k, v in raw.items()
                if k not in ("release", "dataset")) if isinstance(raw, dict) else raw
    _record("get_breakdown", args, rel or {})
    return _dumps(_envelope(
        "get_breakdown", m, dict(data, cut=cut) if isinstance(data, dict) else data,
        calc=(raw.get("calc") if isinstance(raw, dict) else None) or _calc(m),
        vintage={"unit": "release", "basis": "release-in-force", "as_of": None,
                 "release_id": (rel or {}).get("release_id"),
                 "label": (rel or {}).get("label"),
                 "latest_period": (rel or {}).get("latest_period"),
                 "published_at": (rel or {}).get("ingested_at"),
                 "source_sha256": (rel or {}).get("sha256")} if rel else None,
        cite=_cite_for(m, dataset=dataset) if m.get("cite") else None))


# Numeric columns a series listing carries, and so the sorts a series-shaped
# dataset can be ranked on. Which exist depends on the dataset's shape; the
# tool reports the ones actually present rather than guessing.
SERIES_SORTS = ("latest", "delta_1m", "delta_12m", "avg_12m", "sum_12m",
                "yoy", "mom", "ann3m", "weight")


def _screen_series(m, dataset, sort, filters, limit):
    """Rank every series in a series dataset by one of its own columns.

    `screen` covered the ten company and events datasets only, so 'which
    prefecture has the worst real balance' or 'which item is rising fastest'
    had no tool — the caller had to pull series one batch at a time and sort
    them itself, which is both slow and a place to get the arithmetic wrong.
    This ranks the same listing /api/v1/{dataset}/series publishes.
    """
    q = str((filters or {}).get("q") or "")
    order = str((filters or {}).get("order") or "desc").lower()
    if order not in ("desc", "asc"):
        return _fail("order must be desc or asc.")
    try:
        raw = call_api(api.series_list, dataset=dataset, q=q)
    except Exception as exc:  # noqa: BLE001
        return _fail(_detail(exc))
    rows = raw.get("series", [])
    present = [c for c in SERIES_SORTS
               if any(isinstance(r.get(c), (int, float)) for r in rows)]
    if not present:
        return _fail("%s publishes no numeric column to rank on." % dataset)
    sort = (sort or present[0]).strip()
    if sort not in present:
        return _fail("Unknown sort %r for %s; one of %s"
                     % (sort, dataset, ", ".join(present)))
    ranked = [r for r in rows if isinstance(r.get(sort), (int, float))]
    skipped = len(rows) - len(ranked)
    ranked.sort(key=lambda r: r[sort], reverse=(order == "desc"))
    limit = max(1, min(int(limit or ROW_BUDGET), MAX_LIMIT))
    out = ranked[:limit]
    keep = ("code", "name_en", "name_ja", "unit", "kind", "as_of", "discontinued")
    data = {"sort": sort, "order": order, "ranked_on": present,
            "count": len(ranked),
            "not_ranked": skipped or None,
            "rows": [dict([(k, r.get(k)) for k in keep if r.get(k) is not None],
                          **{sort: r[sort]}) for r in out]}
    rel = raw.get("release") or {}
    _record("screen", {"dataset": dataset, "sort": sort, "limit": limit}, rel)
    return _dumps(_envelope(
        "screen", m, data,
        calc=raw.get("calc") or _calc(m),
        vintage={"unit": "release", "basis": "release-in-force", "as_of": None,
                 "release_id": rel.get("release_id"), "label": rel.get("label"),
                 "latest_period": rel.get("latest_period"),
                 "published_at": rel.get("ingested_at"),
                 "source_sha256": rel.get("sha256")},
        cite=_cite_for(m, dataset=dataset) if m.get("cite") else None,
        truncated={"rows": "showing %d of %d" % (len(out), len(ranked))}
        if len(ranked) > len(out) else None,
        missing={"not_ranked": "%d series publish no %s and are excluded, never "
                               "counted as zero" % (skipped, sort)} if skipped else None))


IMPLS = {
    "list_cohorts": list_cohorts,
    "compare_cohort": compare_cohort,
    "list_datasets": list_datasets,
    "describe_dataset": describe_dataset,
    "search": search,
    "get_company": get_company,
    "get_series": get_series,
    "get_overview": get_overview,
    "get_breakdown": get_breakdown,
    "get_vintages": get_vintages,
    "screen": screen,
}


def run_tool(name, args):
    """Run one tool with a dict of arguments; never raises. Same contract as
    tools.run_tool: (text, is_error)."""
    impl = IMPLS.get(name)
    if impl is None:
        return _fail("Unknown tool '%s'." % name), True
    if not isinstance(args, dict):
        return _fail("Arguments for '%s' must be an object." % name), True
    try:
        text = impl(**args)
    except TypeError as exc:  # unexpected or missing argument names
        return _fail(str(exc)), True
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller, not raised
        return _fail(_detail(exc)), True
    return text, text.startswith('{"error":')


def _cohort_metric_ids():
    """Metric ids for the tool schema, read from the one registry that has them."""
    try:
        return [m[0] for m in _mod("cohorts_api").METRICS]
    except Exception:  # noqa: BLE001 — a schema is not worth failing tools/list over
        return []


def _str(desc, **extra):
    d = {"type": "string", "description": desc}
    d.update(extra)
    return d


def descriptors():
    """MCP tool descriptors, with enums drawn from the live registry so the
    assistant sees the real dataset ids."""
    # Only what this deployment can actually serve. A dataset whose manifest
    # is registered but which has no data here (the US shelf, when the SEC
    # quarter files are not on the volume) used to sit in every enum and
    # answer "not published on this server yet" — advertising a capability
    # the server does not have is worse than not advertising it.
    ids = [i for i in registry.ids() if registry.available(i)]
    series_ids = [i for i in ids if registry.get(i)["shape"] == "series"]
    company_ids = [i for i in ids if "company" in registry.get(i)["capabilities"]]
    screen_ids = [i for i in ids
                  if registry.get(i).get("screens")
                  or registry.get(i)["shape"] == "series"]
    overview_ids = [i for i in series_ids
                    if (api.ADAPTERS.get(i) and
                        (api.ADAPTERS[i].PRESENTATION.get("overview_tiles")
                         or api.ADAPTERS[i].PRESENTATION.get("main_series")))]
    breakdown_ids = [i for i in series_ids if _breakdowns_for(i)]
    all_cuts = sorted(set(c for i in breakdown_ids for c, _f, _w in _breakdowns_for(i)))
    ro = {"readOnlyHint": True, "openWorldHint": False}
    return [
        {"name": "list_datasets", "title": "List datasets",
         "description": ("Every dataset this server publishes, compactly: id, section, "
                         "shape (series / company / events), what it covers, which tools "
                         "apply, and its screens. Call this first when unsure which "
                         "dataset holds what a question needs."),
         "inputSchema": {"type": "object", "properties": {
             "section": _str("Restrict to one section.", enum=registry.SECTION_IDS)},
             "required": []},
         "annotations": ro},
        {"name": "describe_dataset", "title": "Describe a dataset",
         "description": ("One dataset's full card: source and credit line, every measure "
                         "with its trust label and — for calculated ones — its formula, "
                         "the endpoints, screens, notes an analyst must know, and what "
                         "this server holds of it."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Dataset id.", enum=ids)}, "required": ["dataset"]},
         "annotations": ro},
        {"name": "search", "title": "Search companies and series",
         "description": ("Find a company by name (Japanese or English) or securities code, "
                         "or a data series by name or code, across every dataset. Each "
                         "company hit lists the datasets that know it; each series hit "
                         "gives the code get_series needs."),
         "inputSchema": {"type": "object", "properties": {
             "query": _str("Company name, securities code, or series name/code."),
             "dataset": _str("Restrict to one dataset.", enum=ids),
             "limit": {"type": "integer", "description": "Max hits per kind (up to 100)."}},
             "required": ["query"]},
         "annotations": ro},
        {"name": "get_company", "title": "Get a company",
         "description": ("One company by securities code. With `dataset`, the full company "
                         "view from that dataset (holdings, register, board and pay, "
                         "buybacks, facilities, financials, AGM votes…). Without it, a "
                         "compact profile across every dataset that knows the company, "
                         "with a coverage list of which do not — use that to decide which "
                         "dataset to open next."),
         "inputSchema": {"type": "object", "properties": {
             "code": _str("Securities code, e.g. 7974."),
             "dataset": _str("Dataset id for the full view.", enum=company_ids),
             "as_of": _str("YYYY-MM-DD: the filings that existed on EDINET by "
                           "that date. Applies to the whole answer or none of it."),
             "limit": {"type": "integer",
                       "description": "Rows per table in the full view (default 50, max 100)."}},
             "required": ["code"]},
         "annotations": ro},
        {"name": "get_series", "title": "Get series history",
         "description": ("History for up to six series of a series-shaped dataset (prices, "
                         "BOJ, yields, arrivals, population, trade) as the published value "
                         "(measure=index) or a calculated rate (yoy, mom, ann3m where the "
                         "dataset serves them). `as_of` returns the data as it stood on "
                         "that date, from the vintage history."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Series dataset id.", enum=series_ids),
             "series": _str("One to six series codes, comma-separated (from search)."),
             "measure": _str("index (published value) or a rate the dataset serves.",
                             enum=list(registry.GENERIC_MEASURES)),
             "start": _str("YYYY-MM inclusive; default the last 36 months."),
             "end": _str("YYYY-MM inclusive."),
             "as_of": _str("YYYY-MM-DD: the data as published on that date."),
             "months": {"type": "integer", "description": "Window when start is empty."}},
             "required": ["dataset", "series"]},
         "annotations": ro},
        {"name": "screen", "title": "Ranked screen",
         "description": ("A ranked cross-section from a dataset's own screens — e.g. "
                         "oldest boards, largest unspent buybacks, cheapest land per m², "
                         "lowest director approval, highest ROE. For a SERIES dataset "
                         "it ranks every series on one of its own columns instead — "
                         "which prefecture settled the worst balance, which item is "
                         "rising fastest — with filters.q to narrow the list and "
                         "filters.order for direction. Sorts are the ids in "
                         "describe_dataset's `screens`; an unknown sort answers with the "
                         "valid ones. `filters` are the dataset's own query fields "
                         "(year, listed, order, industry, lifecycle, min_shareholders…)."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Dataset id with screens, or any series dataset.",
                             enum=screen_ids),
             "sort": _str("Screen id from describe_dataset, or for a series "
                          "dataset a column: latest, delta_12m, yoy, sum_12m…; "
                          "default the first."),
             "filters": {"type": "object", "description": "Field → value filters."},
             "limit": {"type": "integer", "description": "Rows (default 50, max 100)."},
             "as_of": _str("YYYY-MM-DD: the filings that existed on EDINET by that date.")},
             "required": ["dataset"]},
         "annotations": ro},
        {"name": "list_cohorts", "title": "List peer groups",
         "description": ("The peer groups a company can be compared within: the TOPIX "
                         "scale bands (Core30, Large70, Mid400, Small 1/2), JPX market "
                         "segments (Prime, Standard, Growth), the 33- and 17-industry "
                         "classifications, and — where the deployment is entitled to it "
                         "— index membership. Each comes back with a `spec` string you "
                         "pass to compare_cohort or to screen's filters.cohort."),
         "inputSchema": {"type": "object", "properties": {
             "as_of": _str("YYYY-MM-DD: the classification as it stood then.")},
             "required": []},
         "annotations": ro},
        {"name": "compare_cohort", "title": "Compare within a peer group",
         "description": ("One metric across one peer group, ranked, with the group's own "
                         "median and quartiles — the answer to 'is this normal for a "
                         "company like this?'. `cohort` is a spec from list_cohorts "
                         "(size:core30, ind33:3650, segment:prime, index:nk225) or a "
                         "basket you define: codes:7203,6758,9984. `highlight` returns "
                         "one company's rank and percentile inside the group. Companies "
                         "that do not report the metric are excluded from the ranking "
                         "and listed with the reason — never counted as zero."),
         "inputSchema": {"type": "object", "properties": {
             "cohort": _str("Cohort spec, e.g. size:core30 or codes:7203,6758."),
             "metric": _str("Metric id; see the metrics list on /api/v1/equity/cohorts/metrics.",
                            enum=_cohort_metric_ids()),
             "highlight": _str("Securities code to locate within the cohort."),
             "order": _str("desc (default) or asc.", enum=["desc", "asc"]),
             "limit": {"type": "integer", "description": "Rows (default 50, max 100)."},
             "as_of": _str("YYYY-MM-DD: the classification as it stood then.")},
             "required": ["cohort"]},
         "annotations": ro},
        {"name": "get_overview", "title": "Headline reading",
         "description": ("A series dataset's headline reading — the stat tiles its own "
                         "page leads with, each with its latest value, the change and "
                         "the trust label. The quickest answer to 'where is this now'; "
                         "use get_series for the history behind a tile."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Series dataset id.", enum=overview_ids)},
             "required": ["dataset"]},
         "annotations": ro},
        {"name": "get_breakdown", "title": "A dataset's own cut",
         "description": ("The cut a dataset exists to serve, beyond its raw series: "
                         "contributions to headline inflation, the breadth of price "
                         "rises, the whole yield curve, arrivals by market, one "
                         "commodity's trade with every partner, one area's guest nights, "
                         "every prefecture's counts. Call with no `cut` to see which "
                         "this dataset serves."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Dataset id.", enum=breakdown_ids),
             "cut": _str("Which cut; omit to list the ones this dataset serves.",
                         enum=all_cuts),
             "commodity": _str("trade: the published commodity code."),
             "flow": _str("trade: exp or imp.", enum=["exp", "imp"]),
             "area": _str("accommodation: two-digit JIS prefecture or region code."),
             "prefecture": _str("prefectures: two-digit JIS code, e.g. 13 for Tokyo."),
             "threshold": {"type": "number",
                           "description": "breadth: the YoY percent above which an item counts as rising."},
             "start": _str("contributions: first period, YYYY-MM."),
             "end": _str("contributions: last period, YYYY-MM.")},
             "required": ["dataset"]},
         "annotations": ro},
        {"name": "get_vintages", "title": "Releases and revisions",
         "description": ("The point-in-time record. With no `series`, every accepted "
                         "release of the dataset newest first — label, the period it "
                         "reached, when it was known, and the SHA-256 of the archived "
                         "source file. With a `series`, how that series has been revised "
                         "release by release, and by how much. Pair with get_series's "
                         "`as_of` to read the data as it stood on a date."),
         "inputSchema": {"type": "object", "properties": {
             "dataset": _str("Dataset id.", enum=ids),
             "series": _str("One series code — its revision history instead of the "
                            "release list."),
             "period": _str("With `series`: one period, YYYY-MM; omit for every "
                            "revised period.")},
             "required": ["dataset"]},
         "annotations": ro},
    ]


# ---------------------------------------------------------------------------
# Instructions — generated from the registry, so they name every dataset
# ---------------------------------------------------------------------------

_RULES = (
    "Ground every figure you state in a tool result from this conversation — the data "
    "is revised and extended continuously, so never answer from memory. Missing values "
    "are missing, never zero. Every measure is either OFFICIAL (exactly as published by "
    "the agency or filed by the company) or CALCULATED by this platform from official "
    "inputs; calculated figures carry their formula in `calc`, and you must keep the "
    "two distinct when quoting. Never rank, sum or chart across measure types: an index "
    "is not a yen level, a stock is not a flow, a count of people is not a price. Every "
    "result carries a `cite` URL — a permanent page showing the same view — link it when "
    "you present numbers, and give the `provenance.credit` line as the source."
)

_HOW = (
    "Start with list_datasets (or describe_dataset for one you already know), find "
    "codes with search, then get_series for series datasets, get_company for a company "
    "(without a dataset first, to see which datasets know it), and screen for ranked "
    "cross-sections using the sorts describe_dataset lists."
)


def instructions(compact=False):
    """What the data is, for a model. The full form (each dataset's summary
    and credit line) is for an outside client that reads it once at
    connection; the compact form names each dataset in a few words, for a
    prompt that is re-sent on every step of a run — describe_dataset gives
    the summary when one is needed."""
    parts = ["This server is Plover Analytics: official statistics, company disclosures "
             "and market data for Japan, the United States and Hong Kong. Datasets by "
             "section:"]
    by = dict((s["id"], s) for s in registry.by_section())
    for sec in registry.SECTIONS:
        ids = [i for i in by[sec["id"]]["datasets"] if registry.available(i)]
        if not ids:
            continue
        lines = []
        for i in ids:
            m = registry.get(i)
            if compact:
                lines.append("%s (%s%s)" % (i, m["name"]["en"],
                                            ", " + m["frequency"] if m.get("frequency") else ""))
            else:
                lines.append("%s — %s. %s Credit: %s" % (i, m["name"]["en"], m["summary"],
                                                        m["source"]["credit"]))
        parts.append("%s: %s" % (sec["label"].upper(), " | ".join(lines)))
    if compact:
        parts.append("describe_dataset tells what one dataset holds and its series codes; "
                     "search finds a series or company by name.")
    parts.append(_RULES)
    parts.append(_HOW)
    return "\n\n".join(parts)
