# -*- coding: utf-8 -*-
"""Equity product API — facilities and land (主要な設備の状況).

One row per facility a company discloses in its annual report: site, city-level
location, per-asset-class book values, land area. The dataset behind the
facilities map, and the screen for the classic hidden-land question: who sits
on large landholdings carried at decades-old cost?

Three things to know before reading any number here:

  1. **Book value is not market value — that gap is the point.** Land is
     carried at historical cost under JGAAP; a 1960s purchase is still on the
     books at 1960s prices. This API returns the filed figures and, where the
     filing discloses land area, the derived yen-per-㎡ — it never estimates a
     market value.
  2. **Coordinates are derived, not filed.** Filers disclose city-level
     addresses (東京都渋谷区). Each is matched to its municipality and plotted
     at the municipality's centroid — good for a national map, meaningless for
     a parcel. Rows that name no unambiguous municipality carry no
     coordinates rather than a guessed pin. Centroids derive from Geolonia
     Japanese-addresses (CC BY 4.0); credit that source wherever they render.
  3. **"Major facilities" is the filer's cut, not a register.** The section
     lists what the filer deems major; totals here are a floor, not the
     balance sheet. The extraction is gated both ways — rows must recompute
     the filer's own 合計, and land must not exceed consolidated
     balance-sheet land (own + trust) — and cross-sectional surfaces use only
     filings that passed ('clean'); a company page states the status of
     whatever filing it shows.

Same DuckDB file and reader as the equity APIs next door; registered before
them so the literal /equity/facilities/ paths win.
"""
from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import listing

from . import aliases
from . import facility_labels
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/facilities", tags=["Facilities"])

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as filed in each company's 主要な設備の状況 "
             "(major facilities, annual securities report), EDINET. Book "
             "values are historical cost, not market value. Raw filings "
             "archived with SHA-256; doc_id links to the source filing."),
    "coordinates": ("Derived: city-level filed address matched to its "
                    "municipality and plotted at the municipality centroid "
                    "(Geolonia Japanese-addresses, CC BY 4.0). Not a parcel "
                    "location. Rows with no unambiguous municipality carry "
                    "no coordinates."),
}

CALC = {
    "yen_per_m2": "land book value (yen) ÷ disclosed land area (m2)",
    "location_en": ("Derived: the geocoded municipality's official Hepburn "
                    "romanization (Japan Post romanized address data), so an "
                    "English location exists exactly where a geocode exists."),
    "use": ("Derived: coarse use category classified from the filer's own "
            "text (site name, 設備の内容, segment) by keyword rules, first "
            "match wins. A row matching no rule is unclassified — never "
            "guessed. Categories rental / housing / idle are flagged "
            "non-core. The filed segment and contents text is always "
            "returned unaltered next to the category."),
    "unrealized_yen": ("fair value (yen) − carrying amount (yen), both "
                       "exactly as disclosed in the 賃貸等不動産 note. The "
                       "fair value is the filer's own year-end 時価, mostly "
                       "appraisal-based; this platform estimates nothing."),
    "unlisted_land_yen": ("consolidated balance-sheet land (yen) − land "
                          "disclosed in 主要な設備の状況 (yen). Computed "
                          "only where the balance-sheet gate reconciled; "
                          "IFRS filers (parent-only figure) are excluded."),
    "note": ("Derived from the filed figures. Land area is disclosed for "
             "owned land only where the filer prints it; missing area stays "
             "missing and is never treated as zero."),
}

# One filing per company — the archive holds several fiscal years and summing
# across them would overstate everything. Cross-sections use the latest clean
# filing (or the one covering ?year=).
_LATEST_FAC = """
    WITH scoped AS (
        SELECT *, coalesce(sec_code, edinet_code, doc_id) AS filer_key
        FROM eq_fac_filings fac
        WHERE status = 'clean'/*ASOF*//*NEWEST*/
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
    ),
    current_fac AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY filer_key
                                         ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM scoped
        ) WHERE rn = 1
    )
"""


def latest_fac():
    """Latest Fac, with the point-in-time ceiling in force (app/asof.py)."""
    # A clean report a correction has since replaced is out, even where the
    # correction itself only parsed in part: aggregates never use the stale one.
    return (_LATEST_FAC.replace("/*ASOF*/", asof.clause("filed_date", ""))
            .replace("/*NEWEST*/", asof.newest("fac", "eq_fac_filings",
                                               ok="newer.status IN ('clean','partial')")))


def _year_params(year):
    y = (year or "").strip() or None
    return [y, y]


def _require():
    cur = _cur()
    got = _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_fac_filings','eq_facilities')""")
    if len(got) != 2:
        raise HTTPException(503, "facilities dataset not published on this server yet")
    return cur


@router.get("/years")
def years():
    """Fiscal years available, newest first — the feed for a year picker."""
    cur = _require()
    return {"years": _rows(cur, """
        SELECT CAST(year(period_end) AS VARCHAR) AS year,
               count(DISTINCT coalesce(edinet_code, doc_id)) AS filings  -- a correction is not another filer
        FROM eq_fac_filings
        WHERE status = 'clean' AND period_end IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC""")}


@router.get("/summary")
def summary(year: str = Query("", description="fiscal year, e.g. 2026; default latest")):
    """Coverage first, then the aggregates — one clean filing per company."""
    cur = _require()
    p = _year_params(year)
    head = _rows(cur, latest_fac() + """
        SELECT count(*)                     AS companies,
               sum(n_rows)                  AS facility_rows,
               sum(n_geocoded)              AS facility_rows_geocoded,
               sum(fac_land_book_yen)       AS land_book_yen,
               sum(fac_land_area_m2)        AS land_area_m2,
               min(period_end)              AS first_period_end,
               max(period_end)              AS last_period_end
        FROM current_fac""", p * 1)[0]
    coverage = _rows(cur, """
        SELECT status, count(*) AS filings FROM eq_fac_filings
        GROUP BY 1 ORDER BY 2 DESC""")
    head["note"] = ("Aggregates cover one clean filing per company. 'partial' "
                    "filings (a gate failed) are extracted and inspectable on "
                    "the company page but excluded from every aggregate.")
    return {"summary": head, "filings_by_status": coverage,
            "provenance": PROVENANCE, "calc": CALC}


@router.get("/map")
def map_points(year: str = Query("", description="fiscal year; default latest")):
    """Every geocoded facility as a compact point list for the map.

    columns/rows layout to keep the payload small; land_yen and land_area_m2
    can be null — missing is missing, never zero."""
    cur = _require()
    p = _year_params(year)
    pts = _rows(cur, latest_fac() + NAME_CTES + """
        SELECT f.sec_code, coalesce(es.name_en, f.filer_name) AS company,
               x.name, x.location, x.muni_name, x.muni_code, x.lat, x.lng,
               x.land_yen, x.trust_land_yen, x.land_area_m2, x.total_yen,
               x.employees, x.scope, x.segment, x.contents,
               x.doc_id, x.table_no, x.row_no
        FROM eq_facilities x
        JOIN current_fac f USING (doc_id)
        LEFT JOIN en_scode es ON es.sec_code = f.sec_code
        WHERE x.lat IS NOT NULL AND NOT x.is_summary
        ORDER BY coalesce(x.land_yen, 0) + coalesce(x.trust_land_yen, 0) DESC""", p)
    # Ditto marks resolve downward in filing order, so classify in that order,
    # then emit in the display (largest-land-first) order.
    filing_order = sorted(range(len(pts)),
                          key=lambda i: (pts[i]["doc_id"], pts[i]["table_no"],
                                         pts[i]["row_no"]))
    uses = facility_labels.classify_rows([pts[i] for i in filing_order])
    for i, u in zip(filing_order, uses):
        pts[i]["use"] = u
    for r in pts:
        r["location_en"] = facility_labels.location_en(r.pop("muni_code"))
        for k in ("contents", "segment", "doc_id", "table_no", "row_no"):
            r.pop(k)
    cols = ["sec_code", "company", "name", "location", "muni_name",
            "location_en", "use", "lat", "lng",
            "land_yen", "trust_land_yen", "land_area_m2", "total_yen",
            "employees", "scope"]
    return {"columns": cols, "rows": [[r[c] for c in cols] for r in pts],
            "count": len(pts), "provenance": PROVENANCE, "calc": CALC}


@router.get("/ranking")
def ranking(metric: str = Query("land_area",
                                description="land_area | land_book | yen_per_m2 | bs_gap"),
            year: str = Query(""),
            limit: int = Query(50, ge=1, le=500)):
    """Companies ranked on their disclosed land — the hidden-land screen.

    yen_per_m2 ranks cheapest first: large area at negligible book value is
    the pattern worth finding. Only companies whose filing discloses BOTH
    area and book value can appear there. bs_gap ranks by the land the filing
    did NOT itemise: balance-sheet land minus disclosed facilities land,
    computable only where the balance-sheet gate reconciled."""
    cur = _require()
    order = {"land_area": "fac_land_area_m2 DESC",
             "land_book": "fac_land_book_yen DESC",
             "yen_per_m2": "yen_per_m2 ASC",
             "bs_gap": "unlisted_land_yen DESC"}.get(metric)
    if not order:
        raise HTTPException(400,
                            "metric must be land_area, land_book, yen_per_m2 or bs_gap")
    where = ("f.bs_land_status = 'clean' AND f.bs_land_yen IS NOT NULL"
             if metric == "bs_gap" else
             "f.fac_land_area_m2 > 0" +
             (" AND f.fac_land_book_yen > 0" if metric == "yen_per_m2" else ""))
    rows = _rows(cur, latest_fac() + NAME_CTES + """
        SELECT f.sec_code, coalesce(es.name_en, f.filer_name) AS company,
               f.period_end, f.fac_land_book_yen AS land_book_yen,
               f.fac_land_area_m2 AS land_area_m2,
               CASE WHEN f.fac_land_area_m2 > 0 AND f.fac_land_book_yen > 0
                    THEN round(f.fac_land_book_yen * 1.0 / f.fac_land_area_m2, 1)
               END AS yen_per_m2,
               f.bs_land_yen, f.bs_land_status,
               CASE WHEN f.bs_land_status = 'clean' AND f.bs_land_yen IS NOT NULL
                    THEN f.bs_land_yen - coalesce(f.fac_land_book_yen, 0)
               END AS unlisted_land_yen,
               f.doc_id
        FROM current_fac f
        LEFT JOIN en_scode es ON es.sec_code = f.sec_code
        WHERE %s
        ORDER BY %s LIMIT ?""" % (where, order),
        _year_params(year) + [limit])
    return {"metric": metric, "companies": rows,
            "provenance": PROVENANCE, "calc": CALC}


# ------------------------------------------------------- rental fair value

RENTAL_PROVENANCE = {
    "trust": "official",
    "note": ("Carrying amounts and year-end fair values (時価) exactly as "
             "disclosed in each company's 賃貸等不動産 note (annual "
             "securities report, EDINET). The fair value is the filer's own "
             "disclosure, mostly appraisal-based; dual-use property is "
             "disclosed at the whole property's amounts. IFRS adopters "
             "disclose investment property elsewhere and are not covered."),
}

_LATEST_RENT = """
    WITH scoped_r AS (
        SELECT *, coalesce(sec_code, edinet_code, doc_id) AS filer_key
        FROM eq_rental_filings rent
        WHERE status = 'clean'/*ASOF*//*NEWEST*/
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
    ),
    current_rent AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY filer_key
                                         ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM scoped_r
        ) WHERE rn = 1
    )
"""


def latest_rent():
    """Latest Rent, with the point-in-time ceiling in force (app/asof.py)."""
    return (_LATEST_RENT.replace("/*ASOF*/", asof.clause("filed_date", ""))
            .replace("/*NEWEST*/", asof.newest("rent", "eq_rental_filings",
                                               ok="newer.status IN ('clean','partial')")))


def _require_rental():
    cur = _cur()
    got = _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_rental_filings','eq_rental_tables')""")
    if len(got) != 2:
        raise HTTPException(503, "rental-property dataset not published on this server yet")
    return cur


@router.get("/rental/summary")
def rental_summary(year: str = Query("")):
    """Coverage and totals for the rental-property fair-value dataset."""
    cur = _require_rental()
    head = _rows(cur, latest_rent() + """
        SELECT count(*)               AS companies,
               sum(carrying_yen)      AS carrying_yen,
               sum(fair_value_yen)    AS fair_value_yen,
               sum(fair_value_yen) - sum(carrying_yen) AS unrealized_yen,
               min(period_end)        AS first_period_end,
               max(period_end)        AS last_period_end
        FROM current_rent""", _year_params(year))[0]
    coverage = _rows(cur, """
        SELECT status, count(*) AS filings FROM eq_rental_filings
        GROUP BY 1 ORDER BY 2 DESC""")
    head["note"] = ("One clean filing per company. 'no_note' includes both "
                    "companies with no material rental property and IFRS "
                    "adopters; 'immaterial' filings state the note is "
                    "omitted for immateriality.")
    return {"summary": head, "filings_by_status": coverage,
            "provenance": RENTAL_PROVENANCE, "calc": CALC}


@router.get("/rental/ranking")
def rental_ranking(metric: str = Query("unrealized",
                                       description="unrealized | fair_value | ratio"),
                   year: str = Query(""),
                   limit: int = Query(50, ge=1, le=500)):
    """Companies ranked on the market value their rental property discloses.

    unrealized = fair value − carrying amount, the officially disclosed gap
    between market and book. ratio = fair ÷ carrying, the multiple — small
    carrying amounts at large multiples are the classic forgotten assets."""
    cur = _require_rental()
    order = {"unrealized": "unrealized_yen DESC",
             "fair_value": "fair_value_yen DESC",
             "ratio": "fair_to_book DESC"}.get(metric)
    if not order:
        raise HTTPException(400, "metric must be unrealized, fair_value or ratio")
    rows = _rows(cur, latest_rent() + NAME_CTES + """
        SELECT r.sec_code, coalesce(es.name_en, r.filer_name) AS company,
               r.period_end, r.consolidated,
               r.carrying_yen, r.fair_value_yen,
               r.fair_value_yen - r.carrying_yen AS unrealized_yen,
               CASE WHEN r.carrying_yen > 0
                    THEN round(r.fair_value_yen * 1.0 / r.carrying_yen, 2)
               END AS fair_to_book,
               r.carrying_prior_yen, r.fair_value_prior_yen, r.doc_id
        FROM current_rent r
        LEFT JOIN en_scode es ON es.sec_code = r.sec_code
        WHERE r.carrying_yen IS NOT NULL AND r.fair_value_yen IS NOT NULL
          AND (? <> 'ratio' OR r.carrying_yen > 0)
        ORDER BY %s LIMIT ?""" % order,
        _year_params(year) + [metric, limit])
    return {"metric": metric, "companies": rows,
            "provenance": RENTAL_PROVENANCE, "calc": CALC}


@router.get("/companies")
@listing.marked
def companies(q: str = Query("", description="name or securities code substring")):
    """Search feed for this dataset — a company is here if a facilities filing
    was extracted, clean OR partial: someone searching for a specific company
    must find it and see its status, not be told it is missing because a gate
    failed. Aggregates still exclude partials; this list labels them."""
    cur = _require()
    like = "%" + q.strip() + "%"
    alias_sql, alias_params = aliases.clause(cur, "f.sec_code", q)
    return {"companies": _rows(cur, """
        WITH scoped AS (
            SELECT *, coalesce(sec_code, edinet_code, doc_id) AS filer_key
            FROM eq_fac_filings WHERE status IN ('clean','partial')
        ),
        current_fac AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (PARTITION BY filer_key
                                             ORDER BY period_end DESC, filed_date DESC) AS rn
                FROM scoped
            ) WHERE rn = 1
        )""" + NAME_CTES + """
        SELECT f.sec_code, f.filer_name AS name,
               coalesce(n.name_en, s.name_en) AS name_en,
               CAST(year(f.period_end) AS VARCHAR) AS year, f.status,
               f.n_rows, f.fac_land_book_yen AS land_book_yen,
               f.fac_land_area_m2 AS land_area_m2
        FROM current_fac f
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        WHERE f.sec_code IS NOT NULL
          AND (f.sec_code LIKE ? OR f.filer_name LIKE ?
               OR lower(coalesce(n.name_en, s.name_en, '')) LIKE lower(?)"""
        + alias_sql + """)
        ORDER BY coalesce(f.fac_land_book_yen, 0) DESC LIMIT 25""",
        [like, like, like] + alias_params)}


@router.get("/company/{sec_code}")
def company(sec_code: str, year: str = Query("")):
    """Every facility one company disclosed, with the filing's gate status."""
    cur = _require()
    p = _year_params(year)
    filings = _rows(cur, """
        SELECT doc_id, filer_name, sec_code, period_end, filed_date, status,
               bs_land_status, bs_land_yen, fac_land_book_yen, fac_land_area_m2,
               n_rows, n_geocoded, row_gate_ok, row_gate_bad, row_gate_unverified,
               sha256_t1, parser_version
        FROM eq_fac_filings
        WHERE sec_code = ? AND status IN ('clean','partial')"""
          + asof.clause("filed_date") + """
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
        ORDER BY period_end DESC, filed_date DESC, doc_id DESC LIMIT 1""", [sec_code[:4]] + p)
    if not filings:
        raise HTTPException(404, "no facilities filing for %s" % sec_code)
    f = filings[0]
    en = _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT name_en FROM en_scode WHERE sec_code = ?""", [f["sec_code"]])
    f["name_en"] = en[0]["name_en"] if en else None
    f["facilities"] = _rows(cur, """
        SELECT table_no, row_no, scope, is_summary, in_totals, name, location,
               segment, contents, buildings_yen, structures_yen, machinery_yen,
               vehicles_yen, vessels_yen, aircraft_yen, land_yen, trust_land_yen,
               lease_yen, tools_yen, software_yen, intangibles_yen, deposits_yen,
               cip_yen, other_yen, total_yen,
               employees, land_area_m2, land_area_leased_m2,
               muni_code, muni_pref, muni_name, lat, lng, geocode_method,
               currency
        FROM eq_facilities WHERE doc_id = ?
        ORDER BY table_no, row_no""", [f["doc_id"]])
    uses = facility_labels.classify_rows(f["facilities"])
    for x, u in zip(f["facilities"], uses):
        x["location_en"] = facility_labels.location_en(x["muni_code"])
        x["use"] = u
        x["noncore"] = u in facility_labels.NONCORE
    if f["status"] == "partial":
        f["warning"] = ("A validation gate failed on this filing; figures are "
                       "as filed but excluded from cross-company aggregates.")
    # The same company's 賃貸等不動産 note, when it extracted cleanly — the
    # one officially disclosed market value on this page.
    rent = _rows(cur, """
        SELECT doc_id, period_end, status, consolidated, n_tables,
               carrying_yen, fair_value_yen,
               carrying_prior_yen, fair_value_prior_yen
        FROM eq_rental_filings
        WHERE sec_code = ? AND status = 'clean'""" + asof.clause("filed_date") + """
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
        ORDER BY period_end DESC, filed_date DESC, doc_id DESC LIMIT 1""", [sec_code[:4]] + p)
    f["rental"] = rent[0] if rent else None
    f["provenance"] = PROVENANCE
    f["calc"] = CALC
    return f


# The dataset's card (app/registry.py). Book value is historical cost, not
# market value — that gap is the point, and nothing here estimates it.
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "facilities",
    "section": "assets",
    "name": {"en": "Facilities and land", "ja": "主要な設備の状況・賃貸等不動産"},
    "shape": "company",
    "summary": ("Every facility a company discloses in its annual report — "
                "site, municipality, book value by asset class and land area "
                "— with the rental-property note's carrying amount and fair "
                "value, one filing per company from FY2025."),
    "source": dict(_EDINET_SOURCE,
                   document="有価証券報告書 · 主要な設備の状況 / 賃貸等不動産 (annual "
                            "securities report, facilities and rental property notes)",
                   credit="Source: company filings on EDINET (Financial Services "
                          "Agency of Japan). Municipality centroids: Geolonia "
                          "Japanese-addresses (CC BY 4.0)."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2025", "stale_after_days": None,
    },
    "measures": [
        {"id": "book_value_yen", "label": "Book value by asset class (land, buildings, machinery…)",
         "unit": "JPY", "trust": "official"},
        {"id": "land_area_m2", "label": "Disclosed land area", "unit": "m2",
         "trust": "official"},
        {"id": "employees", "label": "Employees at the site", "unit": "count",
         "trust": "official"},
        {"id": "carrying_amount_yen", "label": "Rental property carrying amount",
         "unit": "JPY", "trust": "official"},
        {"id": "fair_value_yen", "label": "Rental property year-end fair value (時価), as disclosed",
         "unit": "JPY", "trust": "official"},
        {"id": "yen_per_m2", "label": "Land book value per square metre",
         "unit": "JPY_per_m2", "trust": "derived", "calc": CALC["yen_per_m2"]},
        {"id": "coordinates", "label": "Map position (municipality centroid)",
         "unit": "text", "trust": "derived", "calc": PROVENANCE["coordinates"]},
        {"id": "location_en", "label": "Location in English", "unit": "text",
         "trust": "derived", "calc": CALC["location_en"]},
        {"id": "use", "label": "Use category", "unit": "category", "trust": "derived",
         "calc": CALC["use"]},
        {"id": "unrealized_yen", "label": "Unrealised gain on rental property",
         "unit": "JPY", "trust": "derived", "calc": CALC["unrealized_yen"]},
        {"id": "unlisted_land_yen", "label": "Balance-sheet land not itemised in the facilities table",
         "unit": "JPY", "trust": "derived", "calc": CALC["unlisted_land_yen"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/facilities/company/{sec_code}",
        "search": "/api/v1/equity/facilities/companies",
        "summary": "/api/v1/equity/facilities/summary",
        "screen": "/api/v1/equity/facilities/ranking",
        "map": "/api/v1/equity/facilities/map",
        "rental_summary": "/api/v1/equity/facilities/rental/summary",
        "rental_screen": "/api/v1/equity/facilities/rental/ranking",
        "years": "/api/v1/equity/facilities/years",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [
        {"id": "land_area", "title": "Largest disclosed land area"},
        {"id": "land_book", "title": "Largest land book value"},
        {"id": "yen_per_m2", "title": "Cheapest land per square metre on the books"},
        {"id": "bs_gap", "title": "Most balance-sheet land not itemised as facilities"},
        {"id": "rental_unrealized", "title": "Largest unrealised gain on rental property"},
        {"id": "rental_fair_value", "title": "Largest rental property fair value"},
        {"id": "rental_ratio", "title": "Highest fair value to carrying amount"},
    ],
    "cite": "/facilities.html?code={sec_code}",
    "page": "/facilities.html",
    "notes": [PROVENANCE["note"], CALC["note"], RENTAL_PROVENANCE["note"]],
}
