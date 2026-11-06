# -*- coding: utf-8 -*-
u"""Cohort comparison — a company read against its peer group, not the market.

Routes under ``/api/v1/equity/cohorts``:

    GET  /                      the cohorts that can be asked for, with counts
    GET  /metrics               the metrics a cohort can be compared on
    GET  /members?cohort=       who is in a cohort
    GET  /company/{sec_code}    the cohorts one company belongs to
    GET  /compare?cohort=&metric=   the distribution, ranked, with quartiles

What this adds over the existing screens is the denominator. `/compare` takes
the same numbers those screens already publish and ranks them **inside** a
cohort — a TOPIX scale band, a JPX industry, a market segment, the Nikkei 225
where the deployment is entitled to it, or a basket of codes the reader names
themselves — and returns the cohort's own distribution beside each row, so a
value can be read as a percentile rather than as a bare figure.

Trust contract: nothing here is a new measurement. Every metric is either a
figure as filed or one of the ratios `fin_metrics` already computes, and each
carries its formula on the response, exactly as it does on its own screen.
The cohort statistics (median, quartiles, percentile rank) are derived and
carry their formulas too. Companies with no value for a metric are counted and
disclosed, never imputed and never treated as zero.
"""
import math

from fastapi import APIRouter, HTTPException, Query

from . import cohorts, equity_api, fin_metrics, plausibility
from .equity_api import _cur, _rows

router = APIRouter(prefix="/api/v1/equity/cohorts", tags=["Peer groups"])

# ---- the metric registry ---------------------------------------------------
# (key, label, unit, higher_is_better, family, formula-or-None-if-as-filed).
# `higher_is_better` is None where the direction is a judgement rather than a
# fact: more cash is not obviously better, and neither is a bigger board.
METRICS = [
    ("revenue_yen",            u"Revenue",                    u"¥", None,  "financials", None),
    ("profit_yen",             u"Profit attributable to owners", u"¥", None, "financials", None),
    ("total_assets_yen",       u"Total assets",               u"¥", None,  "financials", None),
    ("equity_owners_yen",      u"Equity (owners of parent)",  u"¥", None,  "financials", None),
    ("roe_pct",                u"ROE",                        u"%", True,  "financials", None),
    ("roa_pct",                u"ROA",                        u"%", True,  "financials", None),
    ("operating_margin_pct",   u"Operating margin",           u"%", True,  "financials", None),
    ("net_margin_pct",         u"Net margin",                 u"%", True,  "financials", None),
    ("equity_ratio_pct",       u"Equity ratio",               u"%", None,  "financials", None),
    ("asset_turnover_x",       u"Asset turnover",             u"×", True,  "financials", None),
    ("revenue_growth_pct",     u"Revenue growth",             u"%", True,  "financials", None),
    ("profit_growth_pct",      u"Profit growth",              u"%", True,  "financials", None),
    ("fcf_margin_pct",         u"FCF margin",                 u"%", True,  "financials", None),
    ("cash_to_assets_pct",     u"Liquid cash / assets",       u"%", None,  "financials", None),
    ("net_cash_to_assets_pct", u"Net cash / assets",          u"%", None,  "financials", None),
    ("investment_securities_to_assets_pct", u"Investment securities / assets", u"%", None, "financials", None),
    ("board_size",             u"Board size",                 u"",  None,  "governance", None),
    ("female_officer_pct",     u"Female officers",            u"%", True,  "governance",
     u"the ratio of female officers as filed by the company × 100"),
    ("avg_director_age",       u"Average director age",       u"years", None, "governance", None),
    ("directors_70_plus_pct",  u"Directors aged 70+",         u"%", None,  "governance",
     u"directors aged 70 or over ÷ board size × 100"),
    ("avg_salary_yen",         u"Average employee salary",    u"¥", None,  "governance", None),
    ("gender_pay_gap_all",     u"Gender pay gap (all workers)", u"%", None, "governance", None),
    ("employees_consolidated", u"Employees (consolidated)",   u"",  None,  "governance", None),
    ("foreign_pct",            u"Foreign ownership",          u"%", None,  "ownership", None),
    ("financial_institutions_pct", u"Financial institutions", u"%", None,  "ownership", None),
    ("individuals_pct",        u"Individuals",                u"%", None,  "ownership", None),
    ("other_corporations_pct", u"Other corporations",         u"%", None,  "ownership", None),
    ("shareholders_total",     u"Shareholders on the register", u"", None, "ownership", None),
    ("cross_holdings_pct_of_equity", u"Cross-shareholdings / equity", u"%", False,
     "cross-shareholdings",
     u"total book value of policy shareholdings ÷ equity × 100, both from the "
     u"same annual report; left out where the filed total is flagged "
     u"implausible (larger than total assets, or 500× the named holdings)"),
    ("cross_holdings_yen",     u"Cross-shareholdings (book)", u"¥", False, "cross-shareholdings",
     u"sum of the book value of every policy shareholding in the filing; left "
     u"out of the comparison where that filed total is flagged implausible"),
    ("cross_holdings_count",   u"Named cross-shareholdings",  u"",  False, "cross-shareholdings",
     u"count of individually named policy shareholdings in the filing"),
]
METRIC_BY_KEY = dict((m[0], m) for m in METRICS)

FAMILY_LABELS = {
    "financials": u"Financials",
    "governance": u"Board, pay and people",
    "ownership": u"Shareholder register",
    "cross-shareholdings": u"Cross-shareholdings",
}


# ---- the panel -------------------------------------------------------------
# One row per security code, holding every metric from that company's most
# recent filing of each kind. Built once per database version and cached: the
# whole point of a cohort screen is that it re-slices the same cross-section
# many times over, and rebuilding it per request would read four extraction
# tables to answer a question about thirty companies.

_GOV_SQL = """
    SELECT sec_code, board_size, avg_director_age, directors_70_plus,
           female_ratio_filed, salary_checked AS avg_salary_yen, gender_pay_gap_all,
           employees_consolidated, period_end
    FROM (
        SELECT g.*, """ + plausibility.salary_ok("g.avg_salary_yen", "g") + """ AS salary_checked,
               row_number() OVER (PARTITION BY g.sec_code
                   ORDER BY g.period_end DESC, g.filed_date DESC) AS rn
        FROM eq_company_year g
        WHERE g.sec_code IS NOT NULL AND g.status IN ('clean','partial')
    ) WHERE rn = 1
"""
_OWN_SQL = """
    SELECT sec_code, foreign_pct, financial_institutions_pct, individuals_pct,
           other_corporations_pct, shareholders_total, period_end
    FROM (
        SELECT *, row_number() OVER (PARTITION BY sec_code
                   ORDER BY period_end DESC, filed_date DESC) AS rn
        FROM eq_own_filings
        WHERE sec_code IS NOT NULL AND status IN ('clean','partial')
    ) WHERE rn = 1
"""
_XS_SQL = """
    WITH""" + equity_api.POLICY_TOT_CTE + """,
         latest AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY sec_code
                       ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM eq_filings
            WHERE sec_code IS NOT NULL AND status IN ('clean','partial')
        ) WHERE rn = 1)
    SELECT f.sec_code, t.policy_total_yen AS cross_holdings_yen,
           t.policy_total_yen, t.listed_yen, t.named_yen, f.total_assets_yen,
           CASE WHEN f.equity_yen > 0
                THEN 100.0 * t.policy_total_yen / f.equity_yen END
                AS cross_holdings_pct_of_equity,
           (SELECT count(*) FROM eq_holdings h WHERE h.doc_id = f.doc_id)
                AS cross_holdings_count,
           f.period_end
    FROM latest f LEFT JOIN tot t USING (doc_id)
"""

_PANEL = {"version": None, "rows": None}


def _pct_of(part, whole):
    u"""A share as a percentage, or nothing. A missing part is missing, not 0."""
    if part is None or not whole:
        return None
    return 100.0 * part / whole


def panel(cur):
    u"""{sec_code: {metric: value, ...}} for every company we hold a filing for."""
    version = equity_api._version()
    if _PANEL["version"] == version and _PANEL["rows"] is not None:
        return _PANEL["rows"]

    out = {}

    def slot(code):
        return out.setdefault(code, {"sec_code": code})

    for r in fin_metrics.all_rows(cur):
        code = r.get("sec_code")
        if not code:
            continue
        row = slot(code)
        row["name"] = r.get("filer_name")
        row["name_en"] = r.get("filer_name_en")
        row["financials_period_end"] = r.get("period_end")
        row["accounting_standard"] = r.get("accounting_standard")
        # fin_metrics keeps the computed ratios and the size lines in two
        # nested dicts; flattening them here is the only reshaping this module
        # does to a number that module produced.
        values = dict(r.get("size") or {})
        values.update(r.get("metrics") or {})
        for key, _, _, _, family, _ in METRICS:
            if family == "financials" and values.get(key) is not None:
                row[key] = values[key]

    for r in _rows(cur, _GOV_SQL):
        row = slot(r["sec_code"])
        row.setdefault("name", None)
        row["governance_period_end"] = r["period_end"]
        # A filed figure that cannot be what it says (a salary of trillions, a
        # share of women above 1) is not compared, as with the policy totals
        # below; the company's own page shows it as filed and flagged.
        row["board_size"] = r["board_size"] or None
        row["avg_director_age"] = r["avg_director_age"]
        row["avg_salary_yen"] = plausibility.in_range(r["avg_salary_yen"], *plausibility.SALARY_BAND)
        gap = r["gender_pay_gap_all"]
        row["gender_pay_gap_all"] = gap if gap is not None and 0 < gap <= plausibility.PAY_GAP_MAX else None
        row["employees_consolidated"] = r["employees_consolidated"]
        share = plausibility.in_range(r["female_ratio_filed"], 0, 1)
        row["female_officer_pct"] = None if share is None else 100.0 * share
        row["directors_70_plus_pct"] = _pct_of(r["directors_70_plus"],
                                               r["board_size"])

    for r in _rows(cur, _OWN_SQL):
        row = slot(r["sec_code"])
        row["ownership_period_end"] = r["period_end"]
        for key in ("foreign_pct", "financial_institutions_pct",
                    "individuals_pct", "other_corporations_pct"):
            row[key] = plausibility.in_range(r[key], 0, 100)
        row["shareholders_total"] = r["shareholders_total"]

    for r in _rows(cur, _XS_SQL):
        row = slot(r["sec_code"])
        row["cross_period_end"] = r["period_end"]
        # A filed total that cannot be right is not compared: as filed it would
        # rank ソフト99 among the largest policy books in Japan. The company's
        # own page still shows it, as filed and flagged.
        reason = equity_api.check_policy_total(r)["total_implausible_reason"]
        row["cross_holdings_total_implausible"] = reason
        if reason:
            r["cross_holdings_yen"] = None
            r["cross_holdings_pct_of_equity"] = None
        for key in ("cross_holdings_yen", "cross_holdings_pct_of_equity",
                    "cross_holdings_count"):
            row[key] = r[key]

    _PANEL["version"], _PANEL["rows"] = version, out
    return out


# ---- distribution ----------------------------------------------------------

QUANTILE_FORMULA = (u"linear interpolation between the closest ranks of the "
                    u"sorted values (the conventional method, R type 7): for "
                    u"n values the p-quantile sits at position (n − 1) × p")
PERCENTILE_FORMULA = (u"companies in the cohort strictly below this value, "
                      u"plus half of those equal to it, ÷ companies with a "
                      u"value × 100 — so a company at the middle of the "
                      u"cohort reads 50 whichever way ties fall")


def quantile(sorted_values, p):
    u"""p-quantile of an already-sorted list, or None when it is empty."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * p
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def distribution(values):
    u"""Cohort statistics over the values that exist. Missing is excluded and
    counted, never imputed — a company that does not disclose a number is not
    a company with a low number."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"count": 0, "min": None, "p25": None, "median": None,
                "p75": None, "max": None, "mean": None}
    return {
        "count": len(vals),
        "min": vals[0],
        "p25": quantile(vals, 0.25),
        "median": quantile(vals, 0.50),
        "p75": quantile(vals, 0.75),
        "max": vals[-1],
        "mean": sum(vals) / len(vals),
    }


def percentile_rank(sorted_values, value):
    u"""Where `value` sits in the cohort, 0-100. Mid-rank on ties."""
    n = len(sorted_values)
    if n == 0 or value is None:
        return None
    below = sum(1 for v in sorted_values if v < value)
    equal = sum(1 for v in sorted_values if v == value)
    return 100.0 * (below + equal / 2.0) / n


# ---- routes ----------------------------------------------------------------

PROVENANCE = {
    "jpx": {
        "name": u"Tokyo Stock Exchange listed issues (東証上場銘柄一覧)",
        "publisher": u"Japan Exchange Group",
        "url": "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
        "note": u"Market segment, the 33- and 17-industry classifications and "
                u"the TOPIX scale band, as published. The issues carrying a "
                u"scale band are the TOPIX constituents.",
    },
    "companies": {
        "name": u"EDINET annual securities reports (有価証券報告書)",
        "publisher": u"Financial Services Agency",
        "note": u"Every metric compared here comes from the filings, on the "
                u"same basis as the screen it belongs to.",
    },
}


def _require():
    cur = _cur()
    if not cohorts.available(cur):
        raise HTTPException(503, "the classification has not been ingested yet; "
                                 "run equity/class_extract.py")
    return cur


def _resolve(cur, spec, as_of=None):
    try:
        return cohorts.resolve(cur, spec, as_of)
    except cohorts.CohortError as e:
        raise HTTPException(400, str(e))


@router.get("")
def catalogue(as_of: str = Query("", description="ISO date; default the newest vintage")):
    u"""Every cohort that can be asked for, with how many companies are in it."""
    cur = _require()
    out = cohorts.catalogue(cur, as_of.strip() or None)
    out["basket_note"] = (
        u"A basket is its own cohort: pass cohort=codes:7203,6758,… (up to %d "
        u"security codes). Baskets are not stored — the spec string is the "
        u"basket, which is what makes any comparison built on one a permanent, "
        u"citable URL." % cohorts.MAX_BASKET)
    out["restricted_note"] = (
        u"Index membership is served only where the deployment sets "
        u"INTERNAL_COHORTS: a published constituent list is the index "
        u"provider's copyrighted work and is not ours to redistribute.")
    out["provenance"] = PROVENANCE
    return out


@router.get("/metrics")
def metrics():
    u"""What a cohort can be compared on, grouped by where the number comes from."""
    families = {}
    for key, label, unit, better, family, formula in METRICS:
        families.setdefault(family, []).append({
            "metric": key, "label": label, "unit": unit,
            "higher_is_better": better,
            "formula": formula or fin_metrics.FORMULAS.get(key),
            "trust": "derived" if (formula or key in fin_metrics.FORMULAS)
                     else "as filed",
        })
    return {"families": [{"key": k, "label": FAMILY_LABELS.get(k, k),
                          "metrics": v}
                         for k, v in sorted(families.items(),
                                            key=lambda kv: list(FAMILY_LABELS).index(kv[0]))]}


@router.get("/members")
def members(cohort: str = Query(..., description="a cohort spec, e.g. size:core30"),
            as_of: str = Query("")):
    u"""Who is in a cohort, with the classification each member carries."""
    cur = _require()
    c = _resolve(cur, cohort, as_of.strip() or None)
    rows = []
    v = cohorts.vintage(cur, "jpx-listed", as_of.strip() or None)
    if c["members"] and v:
        marks = ", ".join("?" * len(c["members"]))
        rows = _rows(cur,
                     "SELECT sec_code, name_ja, segment, ind33_code, ind33_name,"
                     " ind17_code, ind17_name, size_code, size_name"
                     " FROM eq_classification WHERE vintage_id = ?"
                     " AND sec_code IN (%s) ORDER BY sec_code" % marks,
                     [v[0]] + list(c["members"]))
    if c["members"]:
        found = set(r["sec_code"] for r in rows)
        # A basket may name a code JPX does not classify — delisted, a fund, or
        # listed since the vintage. It stays in the cohort and says so.
        for code in c["members"]:
            if code not in found:
                rows.append({"sec_code": code, "name_ja": None,
                             "unclassified": True})
        rows.sort(key=lambda r: r["sec_code"])
    return {"cohort": {k: c[k] for k in ("spec", "label", "as_of", "public", "kind")},
            "count": len(c["members"]), "rows": rows,
            "provenance": PROVENANCE}


@router.get("/company/{sec_code}")
def company(sec_code: str, as_of: str = Query("")):
    u"""The cohorts one company belongs to, and its default peer group."""
    cur = _require()
    info = cohorts.classify(cur, sec_code.strip().upper(), as_of.strip() or None)
    if info is None:
        raise HTTPException(404, "no classification for %s — it may be "
                                 "delisted, a fund, or listed since the "
                                 "current vintage" % sec_code)
    info["default_cohort"] = cohorts.natural(cur, sec_code.strip().upper(),
                                             as_of.strip() or None)
    info["provenance"] = PROVENANCE
    return info


@router.get("/compare", openapi_extra={"x-example": "/api/v1/equity/cohorts/compare?cohort=size:core30&metric=roe_pct"})
def compare(cohort: str = Query(..., description="a cohort spec, e.g. size:core30 or codes:7203,6758"),
            metric: str = Query("roe_pct", description="one of /cohorts/metrics"),
            highlight: str = Query("", description="a security code to locate in the cohort"),
            order: str = Query("desc", description="'desc' (default) or 'asc'"),
            limit: int = Query(100, ge=1, le=1000),
            as_of: str = Query("")):
    u"""One metric across one cohort: every member ranked, with the cohort's
    own distribution beside them.

    The ranking is over members that HAVE the number. Members that do not are
    returned separately and counted, because "not disclosed" and "low" are
    different findings and collapsing them is the easiest way to publish a
    false league table.
    """
    cur = _require()
    if metric not in METRIC_BY_KEY:
        raise HTTPException(400, "unknown metric; see /api/v1/equity/cohorts/metrics")
    key, label, unit, better, family, formula = METRIC_BY_KEY[metric]
    c = _resolve(cur, cohort, as_of.strip() or None)
    rows_by_code = panel(cur)

    have, missing = [], []
    for code in c["members"]:
        row = rows_by_code.get(code)
        if row is None:
            missing.append({"sec_code": code, "reason": "no filing extracted"})
            continue
        entry = {"sec_code": code, "name": row.get("name"),
                 "name_en": row.get("name_en"), "value": row.get(key),
                 "period_end": row.get(_PERIOD_FIELD[family])}
        if entry["value"] is None:
            entry["reason"] = "not disclosed in the latest filing"
            missing.append(entry)
        else:
            have.append(entry)

    stats = distribution(e["value"] for e in have)
    ordered = sorted(e["value"] for e in have)
    for e in have:
        e["percentile"] = percentile_rank(ordered, e["value"])
    have.sort(key=lambda e: e["value"], reverse=(order != "asc"))
    for i, e in enumerate(have, 1):
        e["rank"] = i

    focus = None
    code = highlight.strip().upper()
    if code:
        hit = [e for e in have if e["sec_code"] == code]
        if hit:
            focus = dict(hit[0])
            focus["of"] = len(have)
            focus["vs_median"] = (None if stats["median"] is None
                                  else focus["value"] - stats["median"])
        elif code in set(c["members"]):
            focus = {"sec_code": code, "value": None,
                     "reason": "in the cohort, but no value for this metric"}
        else:
            focus = {"sec_code": code, "value": None,
                     "reason": "not a member of this cohort"}

    return {
        "cohort": {k: c[k] for k in ("spec", "label", "as_of", "public", "kind")},
        "metric": {"metric": key, "label": label, "unit": unit,
                   "higher_is_better": better, "family": family,
                   "family_label": FAMILY_LABELS.get(family, family),
                   "formula": formula or fin_metrics.FORMULAS.get(key),
                   "trust": ("derived" if (formula or key in fin_metrics.FORMULAS)
                             else "official")},
        "members": len(c["members"]),
        "with_value": len(have),
        "without_value": len(missing),
        "distribution": stats,
        # Every value, uncapped. `rows` is a page of a league table and can be
        # cut by `limit`; a distribution built from a cut list is a different
        # distribution, and would be wrong without ever looking wrong.
        "values": [e["value"] for e in have],
        "rows": have[:limit],
        "truncated": len(have) > limit,
        "no_value": missing[:limit],
        "highlight": focus,
        "calc": {
            "quartiles": QUANTILE_FORMULA,
            "percentile": PERCENTILE_FORMULA,
            "vs_median": u"the company's value minus the cohort median, in the "
                         u"metric's own unit",
        },
        "coverage_note": (
            u"%d of the %d companies in this cohort report a figure for %s. "
            u"The distribution and every rank are computed over those %d only; "
            u"the rest are listed under no_value with the reason, and are "
            u"never counted as zero."
            % (len(have), len(c["members"]), label, len(have))),
        "as_of_note": (
            u"Each company contributes its most recent annual report, and "
            u"Japanese fiscal year-ends are staggered, so reference periods "
            u"differ across the cohort — every row carries its own period_end."),
        "provenance": PROVENANCE,
    }


# Which "latest filing" each family's numbers came from — they are different
# extractions of different documents and can sit on different year-ends.
_PERIOD_FIELD = {
    "financials": "financials_period_end",
    "governance": "governance_period_end",
    "ownership": "ownership_period_end",
    "cross-shareholdings": "cross_period_end",
}
