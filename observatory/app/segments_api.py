# -*- coding: utf-8 -*-
"""Segment notes and the Company Profile: what a company reports by region and
customer, beside the customs flows its business sits in.

Three surfaces, one rule. Everything a filer states — revenue by region on its
own basis, the customers it names, its reportable segments — is served as
filed. Everything that joins a filer to a customs line is editorial and says
so: customs records carry no company identity, so the mapping in
``app/curation/semi_supply_chain.json`` is platform classification, never a source
classification, and the ratio of filed revenue to customs exports is an
indicator with a formula, never an accounting identity.

Endpoints
---------
  /api/v1/equity/segments/companies             search box feed (all filers)
  /api/v1/equity/segments/company/{sec_code}   the filed note, both years
  /api/v1/equity/segments/supply-chain          the mapping, names resolved
  /api/v1/equity/segments/customers             who names whom (the edge list)
  /api/v1/equity/segments/lens/{sec_code}       filed + customs, fiscal periods
  /api/v1/equity/segments/lens/{sec_code}.csv   the same as one wide table

The lens reads the macro database for customs values through the same
functions ``/api/v1/trade-semis`` serves from, then sums them into the
company's own fiscal periods with ``app.fiscal`` — the roll-up that refuses
to sum anything that is not a monthly flow, and refuses to sum a period short.
"""
import datetime
import io
import json
import os
import pathlib
import re
import unicodedata

from fastapi import APIRouter, HTTPException, Query

from . import asof
from fastapi.responses import PlainTextResponse

from . import aliases, api, company_labels, fiscal
from .adapters import mof_trade
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/segments", tags=["Segments and customers"])

CREDIT_LINE = "Source: company filings on EDINET (Financial Services Agency of Japan)."

CURATION_PATH = pathlib.Path(__file__).resolve().parent / "curation" / "semi_supply_chain.json"
TRADE_DATASET = "trade-semis"
HS_DATASET = "trade-inputs"

# A filer's region key -> the customs partners that region means. Only the
# keys where the correspondence is a place, not a grouping a filer draws its
# own way. "Asia" is deliberately absent: one filer's Asia includes Japan,
# another's excludes China, and a sum over a guessed list would be a number
# with no owner.
REGION_PARTNERS = {
    "CN": ["50105"], "TW": ["50106"], "KR": ["50103"], "HK": ["50108"],
    "SG": ["50112"], "US": ["50304"], "NA": ["50304", "50302"],
    "DE": ["50213"], "GB": ["50205"], "NL": ["50207"], "FR": ["50210"],
    # Europe = every partner the Ministry files under region 2.
    "EU": "region:2",
}

REGION_LABEL = {
    "JP": "Japan", "CN": "China", "TW": "Taiwan", "KR": "Korea", "HK": "Hong Kong",
    "SG": "Singapore", "US": "United States", "NA": "North America", "AM": "Americas",
    "LA": "Latin America", "EU": "Europe", "EUUS": "Europe & US", "AS": "Asia",
    "AP": "Asia-Pacific", "SEA": "Southeast Asia", "EA": "East Asia", "OC": "Oceania",
    "ME": "Middle East", "AF": "Africa", "OT": "Other", "OV": "Overseas",
    "DE": "Germany", "NL": "Netherlands", "GB": "United Kingdom", "FR": "France",
    "IT": "Italy", "ES": "Spain", "AU": "Australia", "IN": "India", "TH": "Thailand",
    "PH": "Philippines", "VN": "Viet Nam", "MY": "Malaysia", "ID": "Indonesia",
    "MX": "Mexico", "CA": "Canada", "BR": "Brazil", "NZ": "New Zealand", "IE": "Ireland",
    "CH": "Switzerland", "BE": "Belgium", "RU": "Russia", "TR": "Turkey", "PL": "Poland",
    "CZ": "Czech Republic", "HU": "Hungary", "EMEA": "EMEA", "IL": "Israel",
    "SA": "Saudi Arabia", "AE": "UAE", "TOTAL": "Total",
}

CALC = {
    "filed": ("Figures exactly as the company filed them in the segment note of its "
              "annual securities report: revenue by region on the filer's own basis "
              "(stated with each filing), customers the filer names, and reportable "
              "segments. Values are stored in yen from the filer's stated unit."),
    "customs_fy": ("customs[commodity, region, FY] = Σ over the fiscal year's twelve "
                   "months of the published customs value for every partner country "
                   "the region key maps to (the mapping is listed with the response). "
                   "Values published in thousands of yen are stated in yen. A fiscal "
                   "year with an unpublished month is left out, never summed short."),
    "ratio": ("implied share[FY] = revenue filed for the region / customs exports to "
              "that region's partners over the same fiscal year, × 100. An indicator "
              "of direction and size, not an accounting identity: the company also "
              "ships from overseas plants, sells domestically, and books revenue on "
              "acceptance rather than shipment; customs includes its competitors and "
              "trading houses; and the company's region and the customs partner list "
              "are different definitions that share a name."),
    "mapping": ("Which listed companies' disclosed businesses produce each customs "
                "commodity is a platform classification, reviewed in git, not an official "
                "source label. A company appears only where its own annual report "
                "describes the product as a reportable segment or principal product."),
}



# --- English names for the companies filers name as customers -----------------
#
# A filer writes its customer's name in Japanese, in its own house style:
# 「トヨタ自動車(株)」 and 「トヨタ自動車株式会社」 are the same buyer written twice.
# An English-language product has to show an English name, and the honest way
# to get one is to look the buyer up rather than translate it:
#
#   1. the EDINET company registry, where the buyer is itself a filer — which
#      also yields a securities code, so the buyer's own page can be linked;
#   2. a small curated file, for the government bodies, unlisted subsidiaries
#      and foreign affiliates the registry cannot cover;
#   3. nothing, where no English name exists. A name is never machine-
#      translated and never guessed.
#
# The as-filed name is the identity everywhere and is always shown. The English
# name is a label attached to it, marked with where it came from, and two
# spellings of one buyer stay two rows.

_SUFFIX_RE = re.compile(
    u"(株式会社|有限会社|合同会社|合資会社|\\(株\\)|（株）|㈱|㈲|\\(有\\)|"
    u"Co\\.?,?\\s*Ltd\\.?|Corporation|Corp\\.?|Inc\\.?|Limited|Ltd\\.?|"
    u"Company|K\\.K\\.|Holdings?|グループ|ホールディングス)", re.I)
_PAREN_RE = re.compile(u"[（(][^）)]*[）)]")
_PUNCT_RE = re.compile(u"[\\s,．.・、'’\"\\-]")
_CJK_RE = re.compile(u"[぀-ヿ一-鿿]")


def _match_key(name):
    """A comparison key only — never a display name, and never stored."""
    s = unicodedata.normalize("NFKC", name or "")
    s = _PAREN_RE.sub("", s)
    s = _SUFFIX_RE.sub("", s)
    return _PUNCT_RE.sub("", s).lower().strip()


def _registry_index(cur):
    index = {}
    for r in _rows(cur, "SELECT edinet_code, sec_code, name_ja, name_en FROM eq_entities"):
        for nm in (r["name_ja"], r["name_en"]):
            k = _match_key(nm)
            if k and k not in index:
                index[k] = r
    return index


def _curated_names():
    """Deprecated shim: the curated list now lives in app/company_labels.py,
    the hand-edited module, so there is one place to edit and it is checkable
    with `python -m app.company_labels --check`."""
    return {}


def resolve_customer(name, index, curated=None):
    """{name_en, sec_code, group, tags, source} for a customer name as filed.

    Order: the hand-edited company list first — it is the only thing that knows
    two spellings are one buyer, and it carries the family and the themes — then
    the EDINET registry, then nothing. `source` says which answered, so a
    reader can see whether a name was looked up or curated.
    """
    curated_hit = company_labels.lookup(name)
    hit = index.get(_match_key(name))
    sec = (curated_hit or {}).get("sec_code") or (hit["sec_code"] if hit else None)
    if curated_hit and curated_hit.get("name_en"):
        return {"name_en": curated_hit["name_en"], "sec_code": sec,
                "group": curated_hit.get("group"), "tags": curated_hit.get("tags") or [],
                "slug": curated_hit["slug"], "source": "curated"}
    if hit and hit["name_en"]:
        return {"name_en": hit["name_en"], "sec_code": sec, "group": None,
                "tags": [], "slug": None, "source": "registry"}
    if hit or curated_hit:
        return {"name_en": None, "sec_code": sec,
                "group": (curated_hit or {}).get("group"),
                "tags": (curated_hit or {}).get("tags") or [],
                "slug": (curated_hit or {}).get("slug"),
                "source": "curated" if curated_hit else "registry"}
    if not _CJK_RE.search(name or ""):
        return {"name_en": None, "sec_code": None, "group": None, "tags": [],
                "slug": None, "source": "as filed"}
    return {"name_en": None, "sec_code": None, "group": None, "tags": [],
            "slug": None, "source": None}


def _curation():
    with open(str(CURATION_PATH), encoding="utf-8") as f:
        return json.load(f)


def _tables(cur):
    return {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def _need(cur):
    if "eq_seg_filings" not in _tables(cur):
        raise HTTPException(503, "segment notes not extracted yet")


def _entity(cur, sec_code):
    rows = _rows(cur, "SELECT edinet_code, sec_code, name_ja, name_en, industry, listed "
                      "FROM eq_entities WHERE sec_code = ? LIMIT 1", [sec_code])
    if rows:
        return rows[0]
    rows = _rows(cur, "SELECT edinet_code, sec_code, filer_name AS name_ja, NULL AS name_en, "
                      "NULL AS industry, NULL AS listed FROM eq_seg_filings "
                      "WHERE sec_code = ?" + asof.clause("filed_date")
                      + " ORDER BY period_end DESC, filed_date DESC, doc_id DESC LIMIT 1", [sec_code])
    if rows:
        return rows[0]
    raise HTTPException(404, "no company with securities code %s" % sec_code)


def _latest_filing(cur, sec_code):
    rows = _rows(cur, """
        SELECT doc_id, edinet_code, sec_code, filer_name, period_end, filed_date,
               parser_version, status, detail, accounting_standard, single_segment,
               basis_text, region_rows, customer_rows, product_rows,
               region_omitted_reason, consolidated_revenue_yen, region_revenue_sum_yen,
               reconciliation, sha256_t1
        FROM eq_seg_filings WHERE sec_code = ? AND status IN ('clean', 'partial')"""
        + asof.clause("filed_date") + """
        ORDER BY period_end DESC, filed_date DESC, doc_id DESC LIMIT 1""", [sec_code])
    return rows[0] if rows else None


def _fy_end(period_end, year_offset):
    """The filing's period end moved back `year_offset` whole years (a
    365-day step lands on 1 September across a leap day)."""
    if period_end is None or year_offset is None:
        return None
    try:
        return period_end.replace(year=period_end.year - year_offset)
    except ValueError:                      # 29 Feb
        return period_end.replace(year=period_end.year - year_offset, day=28)


def _fy_label(period_end, year_offset):
    """'FY Aug-2025': the company year named by the month it ends."""
    return fiscal.company_year_label(_fy_end(period_end, year_offset))


def _label_rows(rows, period_end):
    for r in rows:
        end = _fy_end(period_end, r["year_offset"])
        r["fiscal_label"] = fiscal.company_year_label(end)
        r["fiscal_year_end"] = end.isoformat() if end else None


def _iso(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


@router.get("/company/{sec_code}")
def company(sec_code: str):
    """The segment note as filed: regions, named customers, reportable segments."""
    cur = _cur()
    try:
        _need(cur)
        ent = _entity(cur, sec_code)
        filing = _latest_filing(cur, sec_code)
        if filing is None:
            raise HTTPException(404, "no segment note extracted for %s" % sec_code)
        doc = filing["doc_id"]
        regions = _rows(cur, """
            SELECT year_offset, measure, ord, label_ja, region_key, value_yen, share_pct,
                   is_subnote, parent_label_ja
            FROM eq_seg_regions WHERE doc_id = ? ORDER BY measure, year_offset DESC, ord""", [doc])
        customers = _rows(cur, """
            SELECT year_offset, ord, customer_name, value_yen, segment_label, source
            FROM eq_seg_customers WHERE doc_id = ? ORDER BY year_offset DESC, ord""", [doc])
        products = _rows(cur, """
            SELECT year_offset, ord, segment_label_ja, external_revenue_yen,
                   total_revenue_yen, segment_profit_yen, segment_assets_yen
            FROM eq_seg_products WHERE doc_id = ? ORDER BY year_offset DESC, ord""", [doc])
        for r in regions:
            r["region_label_en"] = REGION_LABEL.get(r["region_key"])
        _label_rows(regions + customers + products, filing["period_end"])
        filing = dict((k, _iso(v)) for k, v in filing.items())
        return {
            "company": ent, "filing": filing, "trust": "official", "calc": CALC["filed"],
            "credit_line": CREDIT_LINE,
            "regions": regions, "customers": customers, "products": products,
        }
    finally:
        cur.close()


@router.get("/companies")
def companies(q: str = Query("", description="name or code substring"),
              limit: int = Query(25, ge=1, le=100)):
    """Search box feed: every company whose annual report has an accepted
    segment note. The Company Profile reaches all of them, not only the ones
    the semiconductor mapping happens to name."""
    cur = _cur()
    try:
        _need(cur)
        like = "%" + q.strip() + "%"
        alias_sql, alias_params = aliases.clause(cur, "l.sec_code", q)
        return {"companies": _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """,
            latest AS (
                SELECT sec_code, max(edinet_code) AS edinet_code,
                       max(filer_name) AS name, max(period_end) AS period_end,
                       count(DISTINCT period_end) AS filings  -- a correction is not another year
                FROM eq_seg_filings
                WHERE sec_code IS NOT NULL AND status IN ('clean','partial')"""
            + asof.clause("filed_date") + """
                GROUP BY 1)
            SELECT l.sec_code, l.name, coalesce(n.name_en, s.name_en) AS name_en,
                   l.period_end, l.filings, e.industry
            FROM latest l
            LEFT JOIN en_ecode n ON n.edinet_code = l.edinet_code
            LEFT JOIN en_scode s ON s.sec_code = l.sec_code
            LEFT JOIN eq_entities e ON e.edinet_code = l.edinet_code
            WHERE l.sec_code LIKE ? OR l.name LIKE ?
               OR lower(coalesce(n.name_en, s.name_en, '')) LIKE lower(?)"""
            + alias_sql + """
            ORDER BY l.filings DESC, l.sec_code LIMIT ?""",
            [like, like, like] + alias_params + [limit])}
    finally:
        cur.close()


@router.get("/supply-chain")
def supply_chain():
    """The commodity -> company mapping, with names and filing status resolved."""
    cur = _cur()
    try:
        cur_ = _curation()
        have_seg = "eq_seg_filings" in _tables(cur)
        out = []
        for key, entry in cur_["commodities"].items():
            companies = []
            for c in entry["companies"]:
                ent = _rows(cur, "SELECT edinet_code, sec_code, name_ja, name_en FROM eq_entities "
                                 "WHERE sec_code = ? LIMIT 1", [c["sec_code"]])
                filing = _latest_filing(cur, c["sec_code"]) if have_seg else None
                companies.append({
                    "sec_code": c["sec_code"], "edinet_code": c.get("edinet_code"),
                    "role": c["role"], "why": c.get("why"),
                    "name_en": ent[0]["name_en"] if ent else None,
                    "name_ja": ent[0]["name_ja"] if ent else None,
                    "latest_filing": None if filing is None else {
                        "period_end": _iso(filing["period_end"]), "status": filing["status"],
                        "region_rows": filing["region_rows"],
                        "customer_rows": filing["customer_rows"]},
                })
            out.append({"key": key, "label": entry["label"], "companies": companies})
        return {"about": cur_["_about"], "trust": "derived", "calc": CALC["mapping"],
                "commodities": out, "retired": cur_.get("retired", [])}
    finally:
        cur.close()


@router.get("/customers", openapi_extra={"x-example": "/api/v1/equity/segments/customers?name=Taiwan"})
def customers(name: str = Query(None, max_length=80, description="customer name contains"),
              sec_code: str = Query(None, description="filer's securities code"),
              limit: int = Query(200, ge=1, le=1000)):
    """Who names whom: every ≥10% customer any filer discloses, with the revenue.

    A directed edge list — filer → customer — in the current fiscal year of
    the filer's latest note. Names are as the filer wrote them, so the same
    customer can appear under several spellings; nothing here is normalised.
    """
    cur = _cur()
    try:
        _need(cur)
        where, params = ["c.year_offset = 0"], []
        if name:
            where.append("c.customer_name ILIKE ?")
            params.append("%" + name + "%")
        if sec_code:
            where.append("f.sec_code = ?")
            params.append(sec_code)
        rows = _rows(cur, """
            WITH latest AS (
                SELECT doc_id FROM (
                    SELECT doc_id, row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code)
                                                      ORDER BY period_end DESC, filed_date DESC) rn
                    FROM eq_seg_filings WHERE status IN ('clean','partial')"""
                    + asof.clause("filed_date") + """) WHERE rn = 1)
            SELECT f.sec_code, f.edinet_code, f.filer_name, e.name_en AS filer_name_en,
                   f.period_end, c.customer_name, c.value_yen, c.segment_label, c.source,
                   f.consolidated_revenue_yen
            FROM eq_seg_customers c
            JOIN eq_seg_filings f USING(doc_id)
            JOIN latest USING(doc_id)
            LEFT JOIN eq_entities e ON e.edinet_code = f.edinet_code
            WHERE %s ORDER BY c.value_yen DESC NULLS LAST LIMIT %d""" % (" AND ".join(where), limit),
                    params)
        for r in rows:
            r["period_end"] = _iso(r["period_end"])
            cons = r.pop("consolidated_revenue_yen")
            r["share_of_filer_revenue_pct"] = (
                None if not cons or r["value_yen"] is None else r["value_yen"] / cons * 100)
        return {"edges": rows, "trust": "official",
                "calc": ("Revenue from each named customer, exactly as the filer states it. "
                         "share_of_filer_revenue_pct = customer revenue / the filer's "
                         "consolidated revenue × 100, from the financials extractor."),
                "credit_line": CREDIT_LINE}
    finally:
        cur.close()


# ---------------------------------------------------------------- the lens

def _partner_codes(region_key, smap_codes):
    spec = REGION_PARTNERS.get(region_key)
    if spec is None:
        return []
    if isinstance(spec, str) and spec.startswith("region:"):
        digit = spec.split(":")[1]
        return sorted({c.split(".")[2] for c in smap_codes if c.split(".")[2][2] == digit})
    return list(spec)


def _partners_label(region_key, partners):
    """'China' / 'United States, Canada' / 'every partner in Ministry region 2 (Europe), 45 countries'."""
    spec = REGION_PARTNERS.get(region_key)
    if region_key == "WORLD":
        return "every partner the Ministry publishes (%d)" % len(partners)
    if isinstance(spec, str) and spec.startswith("region:"):
        digit = spec.split(":")[1]
        return "every partner in Ministry region %s (%s), %d countries" % (
            digit, mof_trade.REGION_LABEL.get(digit, digit), len(partners))
    return ", ".join(mof_trade.PARTNER_EN.get(p, p) for p in partners)


def _dataset_for(key):
    """A mapping key names its dataset: exp./imp. are trade-semis, hs. is the
    HS-detail layer (exports)."""
    if key.startswith("hs."):
        return HS_DATASET, "exp", key[3:]
    flow, commodity = key.split(".")
    return TRADE_DATASET, flow, commodity


def _customs_by_fiscal(fy_end_month, flow, commodity, partners, granularity, dataset=None):
    """Summed customs value (yen) for a set of partners, in fiscal periods."""
    con = api._con()
    try:
        smap = {s["code"]: s for s in api._series_map(con, dataset or TRADE_DATASET)}
        codes = ["%s.%s.%s.val" % (flow, commodity, p) for p in partners]
        codes = [c for c in codes if c in smap]
        if not codes:
            return [], []
        vals = api._values_bulk(con, [smap[c]["series_id"] for c in codes])
        monthly = {}
        for sid, series in vals.items():
            for period, v in series.items():
                monthly[period] = monthly.get(period, 0.0) + v
        pts = sorted(monthly.items())
        rolled, dropped = fiscal.roll_up(pts, "jpy_1000", fy_end_month, granularity)
        # published in thousands of yen; the lens speaks yen throughout. Labels
        # are the company's own year names, so they match the filed rows.
        return [(p, v * 1000.0, fiscal.company_period_label(p, fy_end_month, granularity))
                for p, v, _l in rolled], dropped
    finally:
        con.close()


def _lens(sec_code):
    cur = _cur()
    try:
        _need(cur)
        ent = _entity(cur, sec_code)
        filing = _latest_filing(cur, sec_code)
        if filing is None:
            raise HTTPException(404, "no segment note extracted for %s" % sec_code)
        fy_end = filing["period_end"].month
        doc = filing["doc_id"]
        regions = _rows(cur, """
            SELECT year_offset, label_ja, region_key, value_yen, share_pct, is_subnote,
                   parent_label_ja FROM eq_seg_regions
            WHERE doc_id = ? AND measure = 'revenue' ORDER BY year_offset DESC, ord""", [doc])
        customers_ = _rows(cur, """
            SELECT year_offset, customer_name, value_yen, segment_label, source
            FROM eq_seg_customers WHERE doc_id = ? ORDER BY year_offset DESC, ord""", [doc])
        products = _rows(cur, """
            SELECT year_offset, segment_label_ja, external_revenue_yen, segment_profit_yen
            FROM eq_seg_products WHERE doc_id = ? ORDER BY year_offset DESC, ord""", [doc])
    finally:
        cur.close()

    for r in regions:
        r["region_label_en"] = REGION_LABEL.get(r["region_key"])
    _label_rows(regions + customers_ + products, filing["period_end"])
    cur2 = _cur()
    try:
        index, curated = _registry_index(cur2), _curated_names()
    finally:
        cur2.close()
    for r in customers_:
        res = resolve_customer(r["customer_name"], index, curated)
        r["customer_name_en"] = res["name_en"]
        r["customer_sec_code"] = res["sec_code"]
        r["customer_group"] = res["group"]
        r["customer_tags"] = res["tags"]
        r["name_source"] = res["source"]

    # the commodities this company is mapped to
    mapping = []
    for key, entry in _curation()["commodities"].items():
        for c in entry["companies"]:
            if c["sec_code"] == sec_code:
                mapping.append({"key": key, "label": entry["label"], "role": c["role"],
                                "why": c.get("why")})

    # customs side, in this company's fiscal periods
    con = api._con()
    try:
        codes_by_dataset = {}
        releases = {}
        for ds in (TRADE_DATASET, HS_DATASET):
            try:
                codes_by_dataset[ds] = [s["code"] for s in api._series_map(con, ds)]
                releases[ds] = api._release(con, ds)
            except HTTPException:
                codes_by_dataset[ds] = []          # a dataset not ingested yet is simply absent
        rel = releases.get(TRADE_DATASET)
    finally:
        con.close()
    customs = []
    relationship = []
    filed_by = {}
    for r in regions:
        if r["year_offset"] is not None and not r["is_subnote"] and r["region_key"] not in (None, "TOTAL"):
            filed_by[(r["fiscal_label"], r["region_key"])] = r["value_yen"]
    for m in mapping:
        if not m["key"].startswith(("exp.", "imp.", "hs.")):
            continue
        dataset, flow, commodity = _dataset_for(m["key"])
        smap_codes = codes_by_dataset.get(dataset) or []
        if not smap_codes or dataset not in releases:
            continue                     # the dataset behind this key is not ingested
        block = {"key": m["key"], "label": m["label"], "role": m["role"], "dataset": dataset,
                 "flow": flow, "commodity": commodity, "regions": {},
                 "release": {"label": releases[dataset]["label"],
                             "latest_period": releases[dataset]["latest_period"]}}
        # world total for the commodity, plus each mapped region
        world_partners = sorted({c.split(".")[2] for c in smap_codes
                                 if c.startswith("%s.%s." % (flow, commodity))})
        if not world_partners:
            continue                     # a mapped line the dataset does not carry yet
        targets = [("WORLD", world_partners)]
        for key in sorted({r["region_key"] for r in regions if r["region_key"] in REGION_PARTNERS}):
            targets.append((key, _partner_codes(key, smap_codes)))
        for key, partners in targets:
            fy, dropped_y = _customs_by_fiscal(fy_end, flow, commodity, partners, "fiscal_year", dataset)
            fq, dropped_q = _customs_by_fiscal(fy_end, flow, commodity, partners, "fiscal_quarter", dataset)
            block["regions"][key] = {
                "partners": partners,
                "partners_label": _partners_label(key, partners),
                "fiscal_years": [{"label": l, "period_end_month": p.isoformat(), "value_yen": v}
                                 for p, v, l in fy],
                "fiscal_quarters": [{"label": l, "period_end_month": p.isoformat(), "value_yen": v}
                                    for p, v, l in fq],
                "months_not_in_a_year": [d.isoformat() for d in dropped_y],
                "months_not_in_a_quarter": [d.isoformat() for d in dropped_q],
            }
            if key != "WORLD":
                for p, v, l in fy:
                    filed = filed_by.get((l, key))
                    if filed is not None and v:
                        relationship.append({
                            "commodity_key": m["key"], "region_key": key,
                            "region_label_en": REGION_LABEL.get(key), "fiscal_label": l,
                            "filed_revenue_yen": filed, "customs_value_yen": v,
                            "implied_share_pct": filed / v * 100.0,
                            "upper_bound": not bool(filing["single_segment"])})
        customs.append(block)

    if rel is None:
        rel = releases.get(HS_DATASET)
    if rel is None:
        raise HTTPException(503, "no customs dataset ingested yet")
    filing_out = dict((k, _iso(v)) for k, v in filing.items())
    return {
        "company": ent,
        "fy_end_month": fy_end,
        "filing": filing_out,
        "filed": {"regions": regions, "customers": customers_, "products": products,
                  "trust": "official", "calc": CALC["filed"]},
        "mapping": {"entries": mapping, "trust": "derived", "calc": CALC["mapping"]},
        "customs": {"blocks": customs, "release": rel, "trust": "derived",
                    "calc": CALC["customs_fy"], "region_partners": REGION_PARTNERS},
        "relationship": {"rows": relationship, "trust": "derived", "calc": CALC["ratio"],
                         "upper_bound_note": (None if filing["single_segment"] else
                             "This filer reports more than one segment and its regional revenue "
                             "spans all of them, so against a single customs line the ratio is an "
                             "upper bound on the company's share, not a share.")},
        "credit_lines": [CREDIT_LINE,
                         "Source: Ministry of Finance, Japan — Trade Statistics of Japan."],
    }


def _label_order(label):
    """'FY Aug-2025' sorts by year then month, not alphabetically."""
    m = re.match(r"FY (\w{3})-(\d{4})", label or "")
    if not m:
        return (0, 0)
    return (int(m.group(2)), fiscal.MONTH_ABBR.index(m.group(1)))


@router.get("/lens/{sec_code}.csv", response_class=PlainTextResponse,
            openapi_extra={"x-example": "/api/v1/equity/segments/lens/8035.csv"})
def lens_csv(sec_code: str):
    """The lens as one wide table a model can paste: one row per fiscal year,
    filed regions and customs series side by side, every header line stating
    source, vintage and trust."""
    d = _lens(sec_code)
    name = d["company"].get("name_en") or d["company"].get("name_ja") or sec_code
    years = []
    region_keys = []
    for r in d["filed"]["regions"]:
        if r["is_subnote"] or r["region_key"] in (None, "TOTAL"):
            continue
        if r["fiscal_label"] not in years:
            years.append(r["fiscal_label"])
        if r["region_key"] not in region_keys:
            region_keys.append(r["region_key"])
    customs_cols = []
    for b in d["customs"]["blocks"]:
        for key, block in b["regions"].items():
            customs_cols.append((b["key"], key, block))
            for fy in block["fiscal_years"]:
                if fy["label"] not in years:
                    years.append(fy["label"])
    years.sort(key=_label_order)
    out = io.StringIO()
    w = lambda line: out.write(line + "\n")
    w("# Plover Analytics — Company Profile: %s (%s)" % (name, sec_code))
    w("# Filed figures: %s" % d["credit_lines"][0])
    w("# Customs figures: %s Release %s (sha256 %s), retrieved %s"
      % (d["credit_lines"][1], d["customs"]["release"]["label"],
         d["customs"]["release"]["sha256"], d["customs"]["release"]["retrieved_at"]))
    w("# Filing: %s, period end %s, filed %s, status %s%s"
      % (d["filing"]["doc_id"], d["filing"]["period_end"], d["filing"]["filed_date"],
         d["filing"]["status"], (" — " + d["filing"]["detail"]) if d["filing"].get("detail") else ""))
    w("# Region basis as filed: %s" % (d["filing"].get("basis_text") or "not stated"))
    w("# Fiscal year ends in month %d; FY labels name the month and year the fiscal year ends" % d["fy_end_month"])
    w("# filed_* columns: %s" % CALC["filed"])
    w("# customs_* columns: %s" % CALC["customs_fy"])
    w("# Region -> customs partners: %s" % json.dumps(
        {k: v for k, v in REGION_PARTNERS.items() if k in region_keys}))
    w("# All values in yen. Blank = not published or not a complete period; never zero.")
    header = ["fiscal_year"] + ["filed_%s_yen" % k for k in region_keys] + \
             ["customs_%s_%s_yen" % (ck.replace(".", "_"), rk) for ck, rk, _b in customs_cols]
    w(",".join(header))
    filed = {}
    for r in d["filed"]["regions"]:
        if not r["is_subnote"] and r["region_key"] not in (None, "TOTAL"):
            filed[(r["fiscal_label"], r["region_key"])] = r["value_yen"]
    for y in years:
        row = [y]
        for k in region_keys:
            v = filed.get((y, k))
            row.append("" if v is None else "%.0f" % v)
        for ck, rk, block in customs_cols:
            v = next((fy["value_yen"] for fy in block["fiscal_years"] if fy["label"] == y), None)
            row.append("" if v is None else "%.0f" % v)
        w(",".join(row))
    return PlainTextResponse(out.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":
                                      'attachment; filename="company-lens-%s.csv"' % sec_code})


@router.get("/lens/{sec_code}", openapi_extra={"x-example": "/api/v1/equity/segments/lens/8035"})
def lens(sec_code: str):
    """What the company files by region, beside the customs flows it is mapped
    to, both in the company's own fiscal periods — and the ratio between them,
    with its formula and its caveat."""
    return _lens(sec_code)




CONCENTRATION_CALC = (
    "Every listed company must name any customer worth 10% or more of its "
    "revenue, and state the amount. dependence[filer, customer] = customer "
    "revenue / the filer's consolidated revenue × 100, both as filed for the "
    "same fiscal year. combined dependence = the sum over every customer that "
    "filer names — the share of its revenue that comes from relationships it "
    "had to disclose. A filer that names nobody has no customer at or above "
    "the threshold and is absent, which is not the same as being unconcentrated."
)

CONCENTRATION_NOTE = (
    "Customer names are exactly as each filer wrote them and are never "
    "normalised here: the same buyer appears as 'Samsung Electronics Co., "
    "Ltd.', 'サムスングループ' and 'Samsung Display Co., LTD' because three "
    "filers wrote it three ways. Searching matches the text as filed, so a "
    "search for one spelling will not find the others."
)


@router.get("/concentration")
def concentration(min_share: float = Query(
        0.0, ge=0, le=100, description="only filers whose combined named-customer "
                                       "dependence reaches this percent")):
    """Who depends on whom, and by how much — the whole disclosed graph.

    One payload: every filer that names a customer, with the amount and the
    share of its own revenue, plus the reverse view aggregated by the customer
    name as written. Everything here is a filed figure; the shares are
    arithmetic on two filed figures and carry their formula.
    """
    cur = _cur()
    try:
        _need(cur)
        rows = _rows(cur, """
            WITH latest AS (
                SELECT doc_id FROM (
                    SELECT doc_id, row_number() OVER (
                        PARTITION BY coalesce(sec_code, edinet_code)
                        ORDER BY period_end DESC, filed_date DESC) rn
                    FROM eq_seg_filings WHERE status IN ('clean','partial')"""
                    + asof.clause("filed_date") + """) WHERE rn = 1)
            SELECT f.sec_code, f.edinet_code, f.filer_name, e.name_en AS filer_name_en,
                   e.industry, f.period_end, f.status, f.consolidated_revenue_yen,
                   f.single_segment, c.customer_name, c.value_yen, c.segment_label, c.source
            FROM eq_seg_customers c
            JOIN eq_seg_filings f USING(doc_id)
            JOIN latest USING(doc_id)
            LEFT JOIN eq_entities e ON e.edinet_code = f.edinet_code
            WHERE c.year_offset = 0 AND c.value_yen IS NOT NULL
            ORDER BY c.value_yen DESC""")

        index, curated = _registry_index(cur), _curated_names()
        filers, by_customer = {}, {}
        for r in rows:
            key = r["sec_code"] or r["edinet_code"]
            rev = r["consolidated_revenue_yen"]
            share = (r["value_yen"] / rev * 100.0) if rev else None
            f = filers.get(key)
            if f is None:
                f = filers[key] = {
                    "sec_code": r["sec_code"], "edinet_code": r["edinet_code"],
                    "name_en": r["filer_name_en"], "name_ja": r["filer_name"],
                    "industry": r["industry"], "period_end": _iso(r["period_end"]),
                    "status": r["status"], "revenue_yen": rev,
                    "single_segment": r["single_segment"], "customers": []}
            res = resolve_customer(r["customer_name"], index, curated)
            f["customers"].append({
                "customer_name": r["customer_name"], "value_yen": r["value_yen"],
                "share_pct": share, "segment_label": r["segment_label"],
                "source": r["source"],
                "customer_name_en": res["name_en"],
                "customer_sec_code": res["sec_code"],
                "customer_group": res["group"], "customer_tags": res["tags"],
                "customer_slug": res["slug"], "name_source": res["source"]})
            c = by_customer.setdefault(r["customer_name"], {
                "customer_name": r["customer_name"],
                "customer_name_en": res["name_en"],
                "customer_sec_code": res["sec_code"],
                "customer_group": res["group"], "customer_tags": res["tags"],
                "customer_slug": res["slug"], "name_source": res["source"], "suppliers": 0,
                "total_yen": 0.0, "max_share_pct": None})
            c["suppliers"] += 1
            c["total_yen"] += r["value_yen"]
            if share is not None and (c["max_share_pct"] is None or share > c["max_share_pct"]):
                c["max_share_pct"] = share

        out = []
        for f in filers.values():
            named = sum(c["value_yen"] for c in f["customers"])
            f["named_total_yen"] = named
            f["named_share_pct"] = (named / f["revenue_yen"] * 100.0) if f["revenue_yen"] else None
            f["customer_count"] = len(f["customers"])
            if f["named_share_pct"] is None or f["named_share_pct"] >= min_share:
                out.append(f)
        out.sort(key=lambda f: -(f["named_share_pct"] or 0))
        customers = sorted(by_customer.values(), key=lambda c: -c["total_yen"])
        named_en = sum(1 for c in customers if c["customer_name_en"])
        latin = sum(1 for c in customers if c["name_source"] == "as filed")
        return {
            "filers": out, "customers": customers,
            "trust": "official", "calc": CONCENTRATION_CALC,
            "note": CONCENTRATION_NOTE,
            "credit_line": CREDIT_LINE,
            "coverage": {"filers_naming_a_customer": len(filers),
                         "relationships": len(rows),
                         "distinct_customer_names": len(by_customer),
                         "names_with_english": named_en,
                         "names_already_latin": latin,
                         "names_without_english": len(by_customer) - named_en - latin},
            "themes": [
                {"key": t, "customers": sum(1 for c in customers if t in (c["customer_tags"] or [])),
                 "suppliers": sum(c["suppliers"] for c in customers if t in (c["customer_tags"] or []))}
                for t in company_labels.tags()],
            "name_note": (company_labels.NOTE + " ").replace("  ", " ") + (
                "A filer writes its customer's name in its own house style. The English "
                "name shown beside it is looked up, never translated: from the EDINET "
                "company registry where the buyer files its own annual report, or from a "
                "small curated list for government bodies and unlisted subsidiaries. Where "
                "no English name exists, none is invented and the filed name stands alone."),
        }
    finally:
        cur.close()


@router.get("/coverage")
def coverage():
    """How much of the archive the segment extractor has turned into rows."""
    cur = _cur()
    try:
        _need(cur)
        row = _rows(cur, """
            SELECT count(*) AS filings,
                   sum(CASE WHEN status='clean' THEN 1 ELSE 0 END) AS clean,
                   sum(CASE WHEN region_revenue_sum_yen IS NOT NULL THEN 1 ELSE 0 END) AS with_region_table,
                   sum(CASE WHEN reconciliation LIKE 'ok%' THEN 1 ELSE 0 END) AS reconciled,
                   sum(CASE WHEN region_omitted_reason IS NOT NULL THEN 1 ELSE 0 END) AS omitted,
                   sum(customer_rows) AS customer_rows, sum(product_rows) AS product_rows,
                   min(period_end) AS first_period, max(period_end) AS last_period
            FROM eq_seg_filings""")[0]
        return dict((k, _iso(v)) for k, v in row.items())
    finally:
        cur.close()


MANIFEST = {
    "id": "segments",
    "section": "financials",
    "name": {"en": "Segment notes and Company Profile", "ja": "セグメント情報・地域別売上・主要顧客"},
    "shape": "company",
    "summary": ("Revenue by region on the filer's own basis, the customers each "
                "company names with the revenue booked from them, and reportable "
                "segments — from the segment note of every annual securities report, "
                "current and prior year. The Company Profile puts the filed figures "
                "beside Japan's customs flows for the commodities the company is "
                "mapped to, in the company's own fiscal periods."),
    "source": dict(_EDINET_SOURCE,
                   document="有価証券報告書 · セグメント情報等 (annual securities report, "
                            "segment information note: 地域ごとの情報 / 主要な顧客ごとの情報 / "
                            "報告セグメント)",
                   credit="Source: company filings on EDINET (Financial Services Agency "
                          "of Japan). Customs figures in the lens: Ministry of Finance, "
                          "Trade Statistics of Japan."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2024", "stale_after_days": None,
    },
    "measures": [
        {"id": "region_revenue_yen", "label": "Revenue by region, as filed (filer's own basis)",
         "unit": "JPY", "trust": "official"},
        {"id": "region_assets_yen", "label": "Property, plant and equipment or non-current assets by region, as filed",
         "unit": "JPY", "trust": "official"},
        {"id": "customer_revenue_yen", "label": "Revenue from each named major customer, as filed",
         "unit": "JPY", "trust": "official"},
        {"id": "segment_revenue_yen", "label": "External revenue, profit and assets by reportable segment, as filed",
         "unit": "JPY", "trust": "official"},
        {"id": "customs_fiscal_yen", "label": "Customs value summed into the company's fiscal periods",
         "unit": "JPY", "trust": "derived", "calc": CALC["customs_fy"]},
        {"id": "implied_share_pct", "label": "Filed regional revenue as a share of customs exports to that region",
         "unit": "%", "trust": "derived", "calc": CALC["ratio"]},
        {"id": "customer_dependence_pct",
         "label": "Named customer revenue as a share of the filer's own revenue",
         "unit": "%", "trust": "derived", "calc": CONCENTRATION_CALC},
        {"id": "supply_chain_mapping", "label": "Commodity to company mapping",
         "unit": "text", "trust": "derived", "calc": CALC["mapping"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/segments/company/{sec_code}",
        "lens": "/api/v1/equity/segments/lens/{sec_code}",
        "lens_csv": "/api/v1/equity/segments/lens/{sec_code}.csv",
        "customers": "/api/v1/equity/segments/customers",
        "supply_chain": "/api/v1/equity/segments/supply-chain",
        "coverage": "/api/v1/equity/segments/coverage",
        "concentration": "/api/v1/equity/segments/concentration",
        "screen": "/api/v1/equity/segments/concentration",
    },
    "capabilities": ["company", "screen"],
    "screens": [
        {"id": "concentration", "title": "Most dependent on a single named customer"},
        {"id": "customer_reach", "title": "Customers with the most Japanese suppliers"},
    ],
    "cite": "/company.html?code={sec_code}",
    "page": "/company.html",
    "notes": [
        "The segment note is a text block, not tagged numbers: every figure here is "
        "parsed from the filer's HTML tables, and a filing whose region revenues do "
        "not sum to its consolidated revenue within 0.5% is marked partial with the "
        "numbers, never adjusted.",
        "Customs records carry no company identity. The mapping between a company and a "
        "customs line is a platform classification (app/curation/semi_supply_chain.json), and "
        "the implied share is an indicator with a formula, never an accounting identity.",
        CONCENTRATION_NOTE,
        "A filer's region (customer location, on its own definition) and a customs "
        "partner (declared destination) are different concepts that share a name; "
        "only regions that name a place are joined, and Asia is never one of them.",
    ],
}
