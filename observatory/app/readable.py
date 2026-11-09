# -*- coding: utf-8 -*-
"""What a page says when nothing runs: a sentence, a table, a credit line.

Every page is a shell that draws its numbers with JavaScript. A crawler that
does not run scripts — most of the ones behind AI assistants — reads the nav
and a heading and leaves. This module builds, for each page, the part of the
page's content that can be written as plain HTML on the way out:

  * one sentence carrying the latest reading (the same figures the stat
    tiles show), and
  * one small table of the latest values, as published, for the series the
    page leads with,

plus the credit line and the addresses of the JSON, CSV and Markdown behind
them. prerender.inject() writes the block into the page; seo.py serves the
same block as /{page}.md.

Everything is drawn from the functions that serve /api/v1, never from a
second query of our own, so the served text and the rendered page cannot
disagree. Everything is best-effort: a dataset without a release, an equity
database mid-swap, a page with no data behind it — each yields no block and
the page is served without one. Nothing here may raise into a request.

Numbers are written the way the visible tiles write them: tabular digits,
grouped thousands, a true minus sign, a stated unit, and "—" for a missing
value — never zero.
"""
import datetime
import logging
import re
import threading
from urllib.parse import quote

MINUS = "\u2212"
DASH = "\u2014"

_lock = threading.Lock()
_cache = {}
_CACHE_MAX = 600

# How many latest periods the table shows, by the dataset's own frequency.
ROWS_BY_FREQUENCY = {"monthly": 13, "quarterly": 8, "semiannual": 6,
                     "annual": 10, "daily": 10, "weekly": 8}

# Datasets with no declared main series and no shape hint to derive one
# from. Codes named here are checked against the series table before use.
FALLBACK_HEADLINES = {
    "investor-flows-jp": ["prime.foreigners.purchases.value",
                          "prime.foreigners.sales.value"],
    "margin-jp": ["customer.purchases.value", "customer.sales.value"],
}

# Units whose values are already rates or ratios.
_RATE_UNITS = ("percent", "pct", "%", "ratio", "births per woman")

_TRADE_PARTNER = re.compile(r"\u2014 (?:exports to|imports from) (.+?) \((?:value|quantity)\)\s*$")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def fmt(value, decimals=1):
    """A number as a reader writes it; None is a dash, never zero."""
    if value is None:
        return DASH
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value != value:  # NaN
        return DASH
    text = "{:,.{p}f}".format(abs(value), p=decimals)
    return (MINUS if value < 0 else "") + text


def fmt_int(value):
    return fmt(value, 0)


def yen(value):
    """A yen amount at the scale a sentence can carry: ¥59.9tn, ¥1.5bn."""
    if value is None:
        return DASH
    value = float(value)
    mag = abs(value)
    if mag >= 1e12:
        return "\u00a5" + fmt(value / 1e12, 1) + "tn"
    if mag >= 1e8:
        return "\u00a5" + fmt(value / 1e9, 1) + "bn"
    if mag >= 1e6:
        return "\u00a5" + fmt(value / 1e6, 1) + "mn"
    return "\u00a5" + fmt(value, 0)


def _month_name(period):
    try:
        return datetime.date.fromisoformat(period[:10]).strftime("%B %Y")
    except (TypeError, ValueError):
        return period or ""


def _label(raw):
    """A filed enum as a heading word: 'audit_committee_election' →
    'Audit committee election'. Never a raw slug on a surface."""
    text = str(raw or "").replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else DASH


def _column_decimals(values, curve=False):
    """One precision per column: 0 for magnitudes, 1 for rates and indices,
    3 for yields, where the column's own values decide."""
    present = [abs(float(v)) for v in values if v is not None]
    if not present:
        return 1
    # The precision the values were published to, where they show one: a
    # ratio filed as 1.19 is not 1.2. Values with more places than any
    # publisher prints fall back to the rule below.
    if max(present) >= 1000:
        return 0
    for places in range(4):
        if all(abs(v - round(v, places)) < 1e-9 for v in present):
            return max(places, 3 if curve else 1)
    return 3 if curve else 1


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _versions():
    from . import db
    try:
        macro = db.file_version()
    except Exception:  # noqa: BLE001 — a missing database is "no version"
        macro = None
    try:
        from . import equity_api
        equity = equity_api.file_version()
    except Exception:  # noqa: BLE001
        equity = None
    return macro, equity


def _cached(key, build):
    full = key + _versions()
    with _lock:
        if full in _cache:
            return _cache[full]
    try:
        value = build()
    except Exception:  # noqa: BLE001 — see module docstring: never into a request
        logging.getLogger(__name__).debug("no readable block for %r", key, exc_info=True)
        value = None
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[full] = value
    return value


# ---------------------------------------------------------------------------
# macro datasets
# ---------------------------------------------------------------------------

def _series_where(con, dataset, clause, params):
    rows = con.execute(
        "SELECT series_id, code, name_en, unit, sort_order, name_ja FROM series "
        "WHERE dataset = ? AND " + clause + " ORDER BY sort_order", [dataset] + params
    ).fetchall()
    return [dict(zip(("series_id", "code", "name_en", "unit", "sort_order", "name_ja"), r))
            for r in rows]


def _by_codes(con, dataset, codes):
    if not codes:
        return []
    marks = ",".join("?" * len(codes))
    found = {s["code"]: s for s in _series_where(
        con, dataset, "code IN (%s)" % marks, list(codes))}
    return [found[c] for c in codes if c in found]


def _headline_series(con, adapter, dataset, default_flow=None):
    """[(series row, column label), ...] — what the page leads with.

    Read from the adapter's own PRESENTATION, the same object the page
    script reads: main_series where declared, otherwise the shape hint
    (curve, arrivals, prefectures, trade) the page is built from.
    """
    pres = adapter.PRESENTATION
    main = pres.get("main_series") or []
    if main and "code" in main[0]:
        rows = _by_codes(con, dataset, [m["code"] for m in main[:4]])
        labels = dict((m["code"], m.get("label") or m.get("name_en")) for m in main)
        return [(s, labels.get(s["code"]) or s["name_en"]) for s in rows], None
    if main and "name_ja" in main[0]:
        names = [m["name_ja"] for m in main[:4]]
        marks = ",".join("?" * len(names))
        rows = _series_where(con, dataset, "name_ja IN (%s)" % marks, names)
        by_ja = {}
        for s in rows:
            by_ja.setdefault(s["name_ja"], s)
        out = []
        for m in main[:4]:
            s = by_ja.get(m["name_ja"])
            if s:
                out.append((s, m.get("label") or s["name_en"]))
        return out, None
    if "curve" in pres:
        rows = _by_codes(con, dataset, ["2Y", "10Y", "30Y"])
        return [(s, s["name_en"]) for s in rows], None
    if "arrivals" in pres:
        hint = pres["arrivals"]
        codes = [hint.get("headline") or "total"] + list(hint.get("regions") or [])[:3]
        rows = _by_codes(con, dataset, codes)
        return [(s, s["name_en"]) for s in rows], None
    if "accommodation" in pres:
        rows = _by_codes(con, dataset, [pres["accommodation"].get("headline") or "nights.jp"])
        return [(s, s["name_en"]) for s in rows], None
    if "prefectures" in pres:
        hint = pres["prefectures"]
        nat, head = hint.get("national") or "00", hint.get("headline") or "population"
        candidates = ["%s.all.%s" % (nat, head), "%s.%s" % (nat, head),
                      "%s.jp.%s" % (nat, head), "%s.fgn.%s" % (nat, head)]
        rows = _by_codes(con, dataset, candidates)
        return [(s, s["name_en"]) for s in rows], None
    if "trade" in pres:
        return _trade_headlines(con, pres["trade"], dataset, default_flow)
    if dataset in FALLBACK_HEADLINES:
        rows = _by_codes(con, dataset, FALLBACK_HEADLINES[dataset])
        return [(s, s["name_en"]) for s in rows], None
    if pres.get("series_requires_query"):
        return [], None
    rows = _series_where(con, dataset, "1=1 LIMIT 3", [])
    return [(s, s["name_en"]) for s in rows], None


def _trade_headlines(con, hint, dataset, default_flow):
    """The page's default flow and its top-level commodity, cut by partner:
    the five largest partners in the latest month, which is the chart the
    page opens on."""
    from . import api
    flows = [f["key"] for f in hint.get("flows") or []]
    flow = default_flow if default_flow in flows else (flows[0] if flows else "exp")
    commodities = (hint.get("commodities") or {}).get(flow) or []
    if not commodities:
        return [], None
    group = commodities[0]
    rows = _series_where(con, dataset, "code LIKE ?",
                         ["%s.%s.%%.val" % (flow, group["code"])])
    if not rows:
        return [], None
    values = api._values_bulk(con, [s["series_id"] for s in rows])
    latest = max((max(v) for v in values.values() if v), default=None)
    if latest is None:
        return [], None
    ranked = sorted(rows, key=lambda s: -(values.get(s["series_id"], {}).get(latest) or 0))
    out = []
    for s in ranked[:5]:
        m = _TRADE_PARTNER.search(s["name_en"] or "")
        out.append((s, m.group(1) if m else s["name_en"]))
    flow_label = dict((f["key"], f["label"]) for f in hint.get("flows") or []).get(flow, flow)
    caption = "%s of %s by partner, largest five in the latest month" % (
        flow_label, group.get("label") or group["code"])
    return out, caption


def _period_text(period, frequency):
    from . import api
    if frequency in ("daily", "weekly"):
        return period.isoformat()
    step = {"monthly": 1, "quarterly": 3, "annual": 12}.get(frequency)
    if step is None:
        return period.strftime("%b %Y")
    return api._period_label(period, step)


def macro_block(dataset, default_flow=None):
    """The block for one statistical dataset, or None."""
    return _cached(("macro", dataset, default_flow),
                   lambda: _macro_block(dataset, default_flow))


def _macro_block(dataset, default_flow):
    from . import api, prerender, registry
    manifest = registry.get(dataset) or {}
    adapter = api._dataset_or_404(dataset)
    frequency = manifest.get("frequency") or adapter.DATASET.get("frequency")
    measures = dict((m["id"], m) for m in manifest.get("measures") or [])
    con = api._con()
    try:
        release = api._release(con, dataset)
        heads, caption = _headline_series(con, adapter, dataset, default_flow)
        if not heads:
            return None
        values = api._values_bulk(con, [s["series_id"] for s, _ in heads])
    finally:
        con.close()

    periods = set()
    for s, _ in heads:
        periods.update(values.get(s["series_id"], {}).keys())
    if not periods:
        return None
    periods = sorted(periods)[-ROWS_BY_FREQUENCY.get(frequency, 10):]
    curve = "curve" in adapter.PRESENTATION

    columns = [{"label": "Period", "num": False}]
    cells = []  # one list per column, of raw values
    for s, label in heads:
        unit = api.UNIT_LABEL.get(s["unit"], s["unit"]) or ""
        columns.append({"label": label + (" (%s)" % unit if unit else ""), "num": True})
        cells.append([values.get(s["series_id"], {}).get(p) for p in periods])
    calc = None
    yoy = measures.get("yoy")
    # A change in percent of a rate or a ratio is not a figure anyone uses:
    # the unemployment rate going from 2.6% to 2.4% is not "−7.7%", and a
    # published growth rate's own growth is noise.
    if heads and heads[0][0].get("unit") in _RATE_UNITS:
        yoy = None
    if yoy and frequency in ("monthly", "quarterly", "semiannual", "annual"):
        head_series, head_label = heads[0]
        points = dict(api._measure_points(values.get(head_series["series_id"], {}), "yoy"))
        column = [points.get(p) for p in periods]
        if any(v is not None for v in column):
            columns.append({"label": head_label + " \u00b7 YoY (%)", "num": True})
            cells.append(column)
            calc = "YoY: " + (yoy.get("calc") or "")
    decimals = [_column_decimals(col, curve) for col in cells]
    if yoy and calc:
        decimals[-1] = 1
    rows = []
    for i, p in enumerate(periods):
        row = [_period_text(p, frequency)]
        for col, d in zip(cells, decimals):
            row.append(fmt(col[i], d))
        rows.append(row)
    rows.reverse()  # newest first, the way the page's own tables read

    sentence = prerender.summary_text(dataset)
    if not sentence:
        latest = periods[-1]
        parts = []
        for (s, label), col, d in zip(heads, cells, decimals):
            v = col[-1]
            if v is None:
                continue
            unit = api.UNIT_LABEL.get(s["unit"], s["unit"]) or ""
            parts.append("%s %s%s" % (label, fmt(v, d), (" " + unit) if unit else ""))
        if parts:
            sentence = "Latest reading, %s: %s." % (_period_text(latest, frequency),
                                                  "; ".join(parts))

    endpoints = manifest.get("endpoints") or {}
    links = []
    json_path = endpoints.get("summary") or endpoints.get("series")
    if json_path:
        links.append(("JSON", json_path))
    csv_path = None
    if endpoints.get("series"):
        csv_path = "/api/v1/%s/observations?series=%s&format=csv" % (
            dataset, quote(heads[0][0]["code"], safe=""))
        links.append(("CSV", csv_path))
    source = manifest.get("source") or {}
    return {
        "heading": (manifest.get("name") or {}).get("en") or dataset,
        "sentence": sentence,
        "table": {"caption": caption or "Latest values, as published",
                  "columns": columns, "rows": rows},
        "calc": calc,
        "credit": source.get("credit") or "",
        "release": (release or {}).get("label") or "",
        "links": links,
        "csv": csv_path,
    }


# ---------------------------------------------------------------------------
# equity datasets
# ---------------------------------------------------------------------------

_CREDIT = ("Source: company filings on EDINET "
           "(Financial Services Agency of Japan).")


def _table(caption, columns, records, fields, decimals=None):
    """A small table from a list of dicts. `fields` are (key, kind) pairs where
    kind is 'raw' (as filed), 'text' (a slug made readable), 'int', 'pct',
    'pct2' (two decimals, as a register prints them), 'pp' (a signed change
    in percentage points), 'yen_bn', 'yen_mn' or 'num'."""
    if not records:
        return None
    cols = [{"label": c, "num": kind not in ("text", "raw")}
            for c, (_, kind) in zip(columns, fields)]
    rows = []
    for rec in records:
        row = []
        for key, kind in fields:
            v = rec.get(key)
            if kind == "raw":
                row.append(DASH if v in (None, "") else str(v))
            elif kind == "text":
                row.append(_label(v))
            elif kind == "int":
                row.append(fmt_int(v))
            elif kind == "pct":
                row.append(fmt(v, 1))
            elif kind == "pct2":
                row.append(fmt(v, 2))
            elif kind == "pp":
                row.append(("+" if v is not None and float(v) > 0 else "") + fmt(v, 2))
            elif kind == "yen_bn":
                row.append(fmt(None if v is None else float(v) / 1e9, 1))
            elif kind == "yen_mn":
                row.append(fmt(None if v is None else float(v) / 1e6, 0))
            else:
                row.append(fmt(v, decimals if decimals is not None else 1))
        rows.append(row)
    return {"caption": caption, "columns": cols, "rows": rows}


def _equity(page_name):
    from . import (agm_api, buyback_api, equity_api, facility_api, financials_api,
                   governance_api, lvh_api, ownership_api, short_api)
    if page_name == "holdings.html":
        s = equity_api.summary(year="")
        sentence = (
            "Cross-shareholdings as filed, each company's latest annual report: "
            "%s filers disclosing %s named holdings with a book value of %s; "
            "%s positions were increased and %s reduced against the prior year, "
            "and %s holdings are reciprocal. Latest fiscal year-end covered: %s."
            % (fmt_int(s.get("filers")), fmt_int(s.get("named_holdings")),
               yen(s.get("total_book_value_yen")), fmt_int(s.get("positions_increased")),
               fmt_int(s.get("positions_reduced")), fmt_int(s.get("reciprocal_pairs")),
               s.get("latest_period_end") or DASH))
        table = _table("Filers by fiscal year of their latest report",
                       ["Fiscal year", "Filers"], s.get("as_of_composition") or [],
                       [("year", "raw"), ("filers", "int")])
        return "Cross-shareholdings", sentence, table, "/api/v1/equity/summary"
    if page_name == "ownership.html":
        s = ownership_api.summary(year="", listed="")
        sentence = (
            "Shareholder registers: %s companies and %s top-ten register rows. On "
            "average the ten largest holders own %s%% of shares, nominee accounts "
            "%s%% and foreign investors %s%%. Latest fiscal year-end covered: %s."
            % (fmt_int(s.get("companies")), fmt_int(s.get("register_rows")),
               fmt(s.get("avg_top_holders_pct"), 1), fmt(s.get("avg_nominee_pct"), 1),
               fmt(s.get("avg_foreign_pct"), 1), s.get("latest_period_end") or DASH))
        table = _table("Register rows by holder type",
                       ["Holder type", "Rows", "Companies", "Average holding (%)"],
                       s.get("holders_by_kind") or [],
                       [("holder_kind", "text"), ("rows", "int"), ("companies", "int"),
                        ("avg_ratio_pct", "pct")])
        return "Shareholder register", sentence, table, "/api/v1/equity/ownership/summary"
    if page_name == "stakes.html":
        s = lvh_api.summary()
        cur = s.get("current_positions") or {}
        sentence = (
            "5%% shareholding reports: %s reports by %s filers in %s issuers, filed "
            "%s to %s; %s state that the filer may make important proposals; the "
            "median report is filed %s days after its trigger. Current positions at "
            "or above 5%%: %s, in %s issuers."
            % (fmt_int(s.get("filings")), fmt_int(s.get("filers")), fmt_int(s.get("issuers")),
               s.get("earliest_filed") or DASH, s.get("latest_filed") or DASH,
               fmt_int(s.get("activist_filings")), fmt(s.get("median_days_to_file"), 0),
               fmt_int(cur.get("at_or_above_5pct")), fmt_int(cur.get("issuers"))))
        table = _table("Reports by type", ["Report type", "Reports"],
                       s.get("by_report_type") or [],
                       [("report_type", "text"), ("n", "int")])
        return "5% shareholding reports", sentence, table, "/api/v1/equity/stakes/summary"
    if page_name == "governance.html":
        s = governance_api.summary(year="", listed="")
        sentence = (
            "Boards and pay: %s companies and %s board seats, a median board of %s; "
            "average director age %s, with %s%% aged 70 or over. Women hold %s%% of "
            "officer seats on average and %s boards have none. Median pay per officer "
            "%s; median employee salary %s; %s individuals disclosed at \u00a5100mn or "
            "more. Latest fiscal year-end covered: %s."
            % (fmt_int(s.get("companies")), fmt_int(s.get("board_seats")),
               fmt(s.get("median_board_size"), 0), fmt(s.get("avg_director_age"), 1),
               fmt(s.get("directors_70_plus_pct"), 1), fmt(s.get("avg_female_officer_pct"), 1),
               fmt_int(s.get("boards_with_no_women")), yen(s.get("median_pay_per_officer_yen")),
               yen(s.get("median_employee_salary_yen")), fmt_int(s.get("named_individuals")),
               s.get("latest_period_end") or DASH))
        table = _table("Companies by fiscal year of their latest report",
                       ["Fiscal year", "Companies"], s.get("as_of_composition") or [],
                       [("year", "raw"), ("companies", "int")])
        return "Boards and pay", sentence, table, "/api/v1/equity/governance/summary"
    if page_name == "buyback.html":
        s = buyback_api.summary()
        sentence = (
            "Buybacks: %s programmes at %s companies from %s monthly reports filed %s "
            "to %s; %s authorised and %s bought; %s shares retired by %s companies."
            % (fmt_int(s.get("authorisations")), fmt_int(s.get("companies")),
               fmt_int(s.get("filings")), s.get("first_submitted") or DASH,
               s.get("last_submitted") or DASH, yen(s.get("authorised_yen")),
               yen(s.get("acquired_yen")), fmt_int(s.get("shares_retired")),
               fmt_int(s.get("companies_retiring"))))
        table = _table("Programmes by status",
                       ["Status", "Programmes", "Authorised (\u00a5bn)", "Acquired (\u00a5bn)"],
                       s.get("lifecycle") or [],
                       [("lifecycle", "text"), ("authorisations", "int"),
                        ("authorised_yen", "yen_bn"), ("acquired_yen", "yen_bn")])
        return "Buybacks", sentence, table, "/api/v1/equity/buyback/summary"
    if page_name == "facilities.html":
        s = (facility_api.summary(year="") or {}).get("summary") or {}
        sentence = (
            "Facilities and land: %s companies disclosing %s facilities, %s of them "
            "located to a municipality; land at book value %s over %s m\u00b2. "
            "Filings from %s to %s."
            % (fmt_int(s.get("companies")), fmt_int(s.get("facility_rows")),
               fmt_int(s.get("facility_rows_geocoded")), yen(s.get("land_book_yen")),
               fmt_int(s.get("land_area_m2")), s.get("first_period_end") or DASH,
               s.get("last_period_end") or DASH))
        return "Facilities and land", sentence, None, "/api/v1/equity/facilities/summary"
    if page_name == "financials.html":
        s = financials_api.summary()
        t = s.get("totals") or {}
        sentence = (
            "Financial statements: %s companies, %s tagged facts across %s statement "
            "lines; latest filing %s."
            % (fmt_int(t.get("companies")), fmt_int(t.get("facts")), fmt_int(t.get("lines")),
               t.get("latest_filed") or DASH))
        table = _table("Filings by fiscal year", ["Fiscal year", "Filings", "Companies"],
                       (s.get("years") or [])[:6],
                       [("year", "raw"), ("filings", "int"), ("companies", "int")])
        return "Financial statements and ratios", sentence, table, \
            "/api/v1/equity/financials/summary"
    if page_name == "agm.html":
        s = agm_api.summary()
        d = s.get("director_approval") or {}
        sp = s.get("shareholder_proposals") or {}
        sentence = (
            "AGM votes: %s meetings at %s companies, %s to %s; %s proposals and %s "
            "director elections. Median director approval %s%%, with %s directors under "
            "80%% and %s under 50%%; %s shareholder proposals, %s rejected."
            % (fmt_int(s.get("meetings")), fmt_int(s.get("companies")),
               s.get("earliest_meeting") or DASH, s.get("latest_meeting") or DASH,
               fmt_int(s.get("proposals")), fmt_int(s.get("director_results")),
               fmt(d.get("median_pct"), 1), fmt_int(d.get("below_80")),
               fmt_int(d.get("below_50")), fmt_int(sp.get("n")), fmt_int(sp.get("rejected"))))
        cats = sorted(s.get("by_category") or [], key=lambda r: -(r.get("n") or 0))[:8]
        table = _table("Proposals by category", ["Category", "Proposals", "Median approval (%)"],
                       cats, [("category", "text"), ("n", "int"), ("median_approval_pct", "pct")])
        return "AGM voting results", sentence, table, "/api/v1/equity/agm/summary"
    if page_name == "shorts.html":
        s = short_api.summary()
        if not isinstance(s, dict) or "detail" in s:
            return None
        facts = []
        for key, value in s.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                continue
            if key.endswith("_note") or key in ("scope", "coverage_note"):
                continue
            if isinstance(value, str) and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                continue
            facts.append("%s %s" % (_label(key).lower(),
                                    value if isinstance(value, str) else fmt_int(value)))
            if len(facts) >= 8:
                break
        if not facts:
            return None
        return "Short positions", "Short positions as disclosed: " + "; ".join(facts) + ".", \
            None, "/api/v1/equity/shorts/summary"
    return None


def equity_block(page_name):
    """The block for one company-filings page, or None."""
    def build():
        found = _equity(page_name)
        if not found:
            return None
        heading, sentence, table, json_path = found
        return {"heading": heading, "sentence": sentence, "table": table, "calc": None,
                "credit": _CREDIT, "release": "", "links": [("JSON", json_path)],
                "csv": None}
    return _cached(("equity", page_name), build)


# ---------------------------------------------------------------------------
# one company
# ---------------------------------------------------------------------------

def company_blocks(code):
    """Two tables for one company page: the filer's own five-year summary and
    its ten largest cross-shareholdings. Names and figures as filed."""
    return _cached(("company", code), lambda: _company_blocks(code)) or []


def _company_blocks(code):
    from . import company_api
    doc = company_api.compose(code, datasets=["financials", "cross-shareholdings"],
                              limit=200)
    blocks = []
    fin = ((doc.get("datasets") or {}).get("financials") or {})
    panel = (fin.get("tables") or {}).get("panel") or []
    best = {}
    for row in panel:
        fy = row.get("fiscal_year_end")
        if not fy:
            continue
        rank = ((row.get("source") or {}).get("filed_date") or "",
                1 if row.get("basis") == "consolidated" else 0)
        if fy not in best or rank > best[fy][0]:
            best[fy] = (rank, row)
    years = [best[fy][1] for fy in sorted(best)][-5:]
    if years:
        records = []
        for row in years:
            v = row.get("values") or {}
            records.append({"fy": row.get("fiscal_year_end"), "revenue": v.get("revenue"),
                            "profit": v.get("profit"), "assets": v.get("total_assets"),
                            "eps": v.get("eps"), "dps": v.get("dps"),
                            "employees": v.get("employees")})
        table = _table("Five-year summary as filed, latest filing per fiscal year",
                       ["Fiscal year end", "Revenue (\u00a5bn)", "Profit (\u00a5bn)",
                        "Total assets (\u00a5bn)", "EPS (\u00a5)", "DPS (\u00a5)", "Employees"],
                       records,
                       [("fy", "raw"), ("revenue", "yen_bn"), ("profit", "yen_bn"),
                        ("assets", "yen_bn"), ("eps", "num"), ("dps", "num"),
                        ("employees", "int")], decimals=2)
        blocks.append({"heading": "Financial summary", "sentence": "", "table": table,
                       "calc": None, "credit": _CREDIT, "release": "",
                       "links": [("JSON", "/api/v1/equity/financials/company/%s" % code)],
                       "csv": None})
    xs = ((doc.get("datasets") or {}).get("cross-shareholdings") or {})
    holdings = (xs.get("tables") or {}).get("holdings") or []
    held = sorted([h for h in holdings if h.get("book_value_yen") is not None],
                  key=lambda h: -float(h["book_value_yen"]))[:10]
    if held:
        records = [{"name": h.get("held_name_en") or h.get("held_name_raw"),
                    "shares": h.get("shares"), "book": h.get("book_value_yen"),
                    "pct": h.get("pct_outstanding")} for h in held]
        table = _table("Ten largest cross-shareholdings by book value, latest annual report",
                       ["Company held", "Shares", "Book value (\u00a5bn)", "% of shares outstanding"],
                       records,
                       [("name", "raw"), ("shares", "int"), ("book", "yen_bn"), ("pct", "pct")])
        calc = (xs.get("calc") or {}).get("pct_outstanding")
        blocks.append({"heading": "Cross-shareholdings", "sentence": "", "table": table,
                       "calc": ("% of shares outstanding: " + calc) if calc else None,
                       "credit": _CREDIT, "release": "",
                       "links": [("JSON", "/api/v1/equity/company/%s" % code)], "csv": None})
    return blocks


# ---------------------------------------------------------------------------
# the landing page
# ---------------------------------------------------------------------------

def home_counts():
    """The four coverage figures the landing page counts up to, from the
    endpoint the page itself reads."""
    def build():
        from . import catalog_api
        c = catalog_api.coverage()
        return {"datasets": c.get("datasets"), "companies": c.get("companies"),
                "filings": c.get("filings"), "sources": c.get("sources")}
    return _cached(("home",), build) or {}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))


def _table_html(table):
    if not table or not table.get("rows"):
        return ""
    head = "".join('<th%s>%s</th>' % (' class="num"' if c["num"] else "", _esc(c["label"]))
                   for c in table["columns"])
    body = []
    for row in table["rows"]:
        cells = []
        for c, value in zip(table["columns"], row):
            cells.append('<td%s>%s</td>' % (' class="num"' if c["num"] else "", _esc(value)))
        body.append("<tr>" + "".join(cells) + "</tr>")
    caption = ('<caption>%s</caption>' % _esc(table["caption"])) if table.get("caption") else ""
    return ('<div class="table-wrap"><table class="data" data-no-enhance>%s'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (caption, head, "".join(body)))


def html(blocks, markdown_path=None):
    """The visible block for a page: collapsed, at the foot of <main>, in the
    same voice as the page's 'Show calculation' disclosures."""
    blocks = [b for b in (blocks or []) if b]
    if not blocks:
        return ""
    parts = []
    for i, b in enumerate(blocks):
        last = i == len(blocks) - 1
        parts.append("<h3>%s</h3>" % _esc(b["heading"]))
        if b.get("sentence"):
            parts.append("<p>%s</p>" % _esc(b["sentence"]))
        parts.append(_table_html(b.get("table")))
        if b.get("calc"):
            parts.append('<p class="source-line">%s</p>' % _esc(b["calc"]))
        foot = []
        if b.get("credit"):
            foot.append(_esc(b["credit"]))
        if b.get("release"):
            foot.append(_esc(b["release"]))
        for label, path in b.get("links") or []:
            foot.append('<a href="%s">%s</a>' % (_esc(path), _esc(label)))
        if markdown_path and last:
            foot.append('<a href="%s">Markdown</a>' % _esc(markdown_path))
        if foot:
            parts.append('<p class="source-line">%s</p>' % " \u00b7 ".join(foot))
    return ('<details class="calc readable" id="readable">'
            '<summary>Latest values as text and table</summary>'
            '<div class="calc-body">%s</div></details>' % "".join(parts))


def _md_cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table_md(table):
    if not table or not table.get("rows"):
        return ""
    cols = table["columns"]
    lines = []
    if table.get("caption"):
        lines.append("_%s_" % table["caption"])
        lines.append("")
    lines.append("| " + " | ".join(_md_cell(c["label"]) for c in cols) + " |")
    lines.append("|" + "|".join("---:" if c["num"] else "---" for c in cols) + "|")
    for row in table["rows"]:
        lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
    return "\n".join(lines)


def markdown(title, description, canonical, blocks, base_url):
    """The whole page as Markdown — what /{page}.md serves."""
    out = ["# " + title.strip(), ""]
    if description:
        out += ["> " + description.strip(), ""]
    if canonical:
        out += ["Page: " + canonical, ""]
    for b in [b for b in (blocks or []) if b]:
        out += ["## " + b["heading"], ""]
        if b.get("sentence"):
            out += [b["sentence"], ""]
        table = _table_md(b.get("table"))
        if table:
            out += [table, ""]
        if b.get("calc"):
            out += [b["calc"], ""]
        foot = []
        if b.get("credit"):
            foot.append(b["credit"])
        if b.get("release"):
            foot.append(b["release"])
        for label, path in b.get("links") or []:
            foot.append("%s: %s" % (label, base_url + path))
        if foot:
            out += [" \u00b7 ".join(foot), ""]
    out += ["---", "",
            "Every figure above is either as published by its source (official) or "
            "calculated by this platform with the formula stated beside it. Missing is "
            "shown as \u2014, never as zero. Guidance for quoting: %s/llms.txt" % base_url,
            ""]
    return "\n".join(out)
