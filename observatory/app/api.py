"""Dataset-scoped JSON API.

Paths are generic (/api/v1/{dataset}/...); cpi-jp is the first dataset.
Measures other than the published index are computed here from official
values and labelled 'derived'; the API sends full-precision floats and
the client applies display precision.

Trust labels: 'official' = value as published by the agency;
'derived' = deterministic calculation from official inputs (formula in
the 'calc' field of the response).
"""
import datetime
import io
import json
import math
import os
import time
from typing import List

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import db, fiscal, heartbeat, place_en, vintages
from .adapters import (boj_assets, cpi_jp, cpi_jp_goods_services, cpi_jp_items,
                       cpi_jp_long, cpi_jp_sa, cpi_tokyo, cpi_tokyo_items,
                       jnto_visitors,
                       jta_accommodation,
                       juki_municipal, juki_population, maff_agri_prices,
                       maff_ja_coops,
                       maff_rice_cost,
                       maff_rice_price,
                       maff_rice_stock,
                       mof_debt, mof_fiscal, mof_fiscal_expenditure,
                       mof_fiscal_ministry, mof_fiscal_revenue,
                       mof_jgb, mof_tax_receipts,
                       mof_trade, mof_trade_autos, mof_trade_energy,
                       mof_trade_food, mof_trade_hs, mof_trade_machinery,
                       mof_trade_pharma,
                       soumu_local_finance, ssds_population,
                       ust_real_yields, ust_yields,
                       estat_gdp, estat_gfs, mof_hojin,
                       fsa_npl, fsa_bank_results, jba_banks,
                       boj_loan_rates, boj_deposit_rates, fsa_fi_list,
                       jpx_margin, jpx_investor_type,
                       cpi_us, cpi_us_sa, cpi_us_areas, cpi_us_weights,
                       us_avg_prices, us_wages, us_wage_tracker,
                       lfs_unemployment, mhlw_job_openings, mhlw_monthly_labour,
                       cpi_hk, cpi_hk_rates, gdp_hk, labour_hk, retail_hk, trade_hk)

# The agent is optional: without the openai package installed the data API
# and the site keep working, and /ask reports itself as unavailable.
try:
    from . import agent
except ImportError:  # pragma: no cover — depends on the install
    agent = None


def _ask_enabled():
    """Kill switch. Off unless ASK_ENABLED is an explicit truthy value.

    Credentials alone are not enough: production ships with the ask box
    hidden until we decide to turn it on.
    """
    return os.environ.get("ASK_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")

ADAPTERS = {"cpi-jp": cpi_jp, "cpi-jp-items": cpi_jp_items, "boj-assets": boj_assets,
            "cpi-jp-goods-services": cpi_jp_goods_services, "cpi-jp-sa": cpi_jp_sa,
            "cpi-jp-long": cpi_jp_long,
            "cpi-tokyo": cpi_tokyo, "cpi-tokyo-items": cpi_tokyo_items,
            "fiscal-jp": mof_fiscal,
            "tax-receipts-jp": mof_tax_receipts,
            "govt-debt-jp": mof_debt,
            "fiscal-jp-revenue": mof_fiscal_revenue,
            "fiscal-jp-expenditure": mof_fiscal_expenditure,
            "fiscal-jp-ministry": mof_fiscal_ministry,
            "govt-accounts-jp": estat_gfs,
            "local-finance-jp": soumu_local_finance,
            "jgb-yields": mof_jgb, "jnto-visitors": jnto_visitors,
            "ust-yields": ust_yields,
            "ust-real-yields": ust_real_yields,
            "population-jp": juki_population,
            "population-jp-history": ssds_population,
            "gdp-jp": estat_gdp,
            "corporate-finance-jp": mof_hojin,
            "population-jp-municipal": juki_municipal,
            "trade-semis": mof_trade, "trade-inputs": mof_trade_hs,
            "trade-autos": mof_trade_autos, "trade-energy": mof_trade_energy,
            "trade-machinery": mof_trade_machinery, "trade-pharma": mof_trade_pharma,
            "trade-food": mof_trade_food,
            "accommodation-jp": jta_accommodation,
            "rice-prices-jp": maff_rice_price,
            "rice-inventory-jp": maff_rice_stock,
            "agri-prices": maff_agri_prices,
            "rice-production-cost": maff_rice_cost,
            "ja-statistics": maff_ja_coops,
            "fsa-npl": fsa_npl, "fsa-bank-results": fsa_bank_results,
            "jba-banks": jba_banks,
            "boj-loan-rates": boj_loan_rates,
            "boj-deposit-rates": boj_deposit_rates,
            "fsa-regional-fi": fsa_fi_list,
            "margin-jp": jpx_margin,
            "investor-flows-jp": jpx_investor_type,
            "cpi-us": cpi_us, "cpi-us-sa": cpi_us_sa,
            "cpi-us-areas": cpi_us_areas, "cpi-us-weights": cpi_us_weights,
            "us-avg-prices": us_avg_prices, "us-wages": us_wages,
            "us-wage-tracker": us_wage_tracker,
            "unemployment-jp": lfs_unemployment,
            "job-openings-jp": mhlw_job_openings,
            "wages-jp": mhlw_monthly_labour,
            # Hong Kong: the Census and Statistics Department (censtatd.py).
            "cpi-hk": cpi_hk, "cpi-hk-rates": cpi_hk_rates, "gdp-hk": gdp_hk,
            "labour-hk": labour_hk, "retail-hk": retail_hk, "trade-hk": trade_hk}

router = APIRouter(prefix="/api/v1", tags=["Datasets"])

# What the pages fetch on load, primed at startup. Not every dataset serves
# every one of these — the misses 404 harmlessly and simply aren't cached.
WARM_ENDPOINTS = ("overview", "series", "contributions", "breadth", "curve",
                  "arrivals", "trade", "accommodation")


def warm_paths():
    return ["/api/v1/%s/%s" % (slug, endpoint)
            for slug in ADAPTERS for endpoint in WARM_ENDPOINTS]


CALC = {
    "index": "Published index value (base year = 100), as released.",
    "yoy": "(index[t] / index[t−12 months] − 1) × 100, from published index values.",
    "mom": "(index[t] / index[t−1 month] − 1) × 100, from published index values.",
    "ann3m": "((index[t] / index[t−3 months]) ^ 4 − 1) × 100, from published index values.",
}
TRUST = {"index": "official", "yoy": "derived", "mom": "derived", "ann3m": "derived"}
UNIT = {"index": "index", "yoy": "%", "mom": "%", "ann3m": "%"}

# Published units, human form. The 'index' measure returns the value exactly as
# published, so its unit is the series' own — an index point for CPI, a yen
# level for the BOJ balance sheet. Labelling a ¥100mn level "index (2020 = 100)"
# would be a trust-contract breach, not a cosmetic slip.
# "pct" is the older spelling of "percent"; both are kept so a series stored
# before the two were reconciled still renders while its dataset waits for its
# next ingest.
UNIT_LABEL = {"index": "index", "jpy_100mn": "¥100mn", "pct": "%",
              "persons": "persons", "jpy_1000": "¥1,000",
              # Bank balance sheets are published in 百万円; the FSA's
              # bad-loan flows in 兆円. Stored as released, never rescaled.
              "jpy_million": "¥mn", "jpy_trillion": "¥tn",
              # The national accounts are published in 10億円.
              "jpy_billion": "¥bn", "count": "count",
              # A night slept, not a person: one visitor staying three nights
              # is three person-nights. Never interchangeable with "persons".
              "person_nights": "person-nights", "percent": "%",
              # Rice is contracted by the 60kg bag of brown rice, and the
              # price is published per bag — never per kilogram or per tonne.
              "jpy_per_60kg": "¥/60kg",
              # Rice stocks are published in 万玄米トン and stored in it.
              "t10k_brown_rice": "10k t brown rice",
              # Farm-cost survey units. A cost, a harvested weight and a
              # labour input share a table but never a series.
              "jpy": "¥", "kg": "kg", "hours": "hours",
              # Customs quantity units, published full-width. Stored exactly
              # as released; only the label is normalised.
              "ＮＯ": "units", "ＫＧ": "kg",
              # Fuels: crude and refined products by the kilolitre, coal and
              # gas by the metric tonne, as the Ministry publishes them.
              "ＫＬ": "kilolitre", "ＭＴ": "tonne", "ＧＲ": "gram",
              # US dollars as published; the per-unit quantity (per lb., per
              # gallon, per hour) is in the series name. "1982-84 $" is the
              # BLS's constant-dollar real earnings, never current dollars.
              "usd": "$", "usd_1982_84": "1982-84 $",
              # The Labour Force Survey counts people in 万人 and is stored
              # in it: 177 is 1.77 million, never 177 people. Job openings
              # ratios are openings per applicant (倍), not a percentage.
              "persons_10k": "10k persons", "ratio": "ratio", "days": "days",
              # Hong Kong's C&SD publishes money in HK$ million and head
              # counts in thousands; both are stored as released.
              "hkd_million": "HK$mn", "persons_1000": "'000 persons"}


def _calc_for(measure, unit):
    if unit in (None, "index"):
        return CALC[measure]                     # CPI wording, unchanged
    if measure == "index":
        return "Published value as released, in %s. Not recomputed." % UNIT_LABEL.get(unit, unit)
    return (CALC[measure].replace("index[", "value[")
            .replace("from published index values", "from published values"))


def _unit_for(measure, unit):
    return (UNIT_LABEL.get(unit, unit) if unit else UNIT[measure]) \
        if measure == "index" else UNIT[measure]


def _index_key(pres):
    """How an index dataset names its headline and group series, or None for
    a dataset that is not an index.

    The Statistics Bureau's tables are named by their Japanese labels, because
    their codes are not stable across a rebase; those declare main_series by
    `name_ja` and groups by `groups_ja`. A source whose codes are stable (the
    BLS series ids) declares `measure_type: "index"`, main_series by `code`
    and groups by `group_codes`. Either way the surfaces below compute the
    same measures; only the lookup key differs.
    """
    main = (pres.get("main_series") or [{}])[0]
    if "name_ja" in main:
        return "name_ja"
    if pres.get("measure_type") == "index" and "code" in main:
        return "code"
    return None


def _group_keys(pres):
    return pres.get("groups_ja") or pres.get("group_codes") or []


def _kind(pres, code):
    """A series' kind (stock, flow, rate...), or the dataset's declared default
    — a dataset whose every series is a rate declares it once."""
    return (pres.get("kinds") or {}).get(code) or pres.get("kind_default")


def _index_shaped_or_404(adapter, dataset):
    """Guard the index-and-weight surfaces (overview, series, contributions,
    breadth). They compute YoY tiles, weighted contributions and breadth — all
    of which presuppose an index with weights. A dataset of yen levels and
    flows has no honest answer here, so it 404s rather than returning a number
    that mixes measure types."""
    if _index_key(adapter.PRESENTATION) is None:
        raise HTTPException(
            404, "'%s' does not serve this surface: it is not an index dataset. "
                 "Use /observations for its published values." % dataset)


def _con():
    """A cursor on the shared reader; closing it releases the cursor only."""
    return db.read_cursor()


def _dataset_or_404(slug):
    if slug not in ADAPTERS:
        raise HTTPException(404, "Unknown dataset '%s'" % slug)
    return ADAPTERS[slug]


def _period_bound(value, name, end=False):
    """A start/end query value as a date: YYYY-MM or YYYY-MM-DD.

    A month as an ``end`` covers the whole month (its last day), so a daily
    series asked for through 2026-09 is not silently cut at 1 September;
    monthly, quarterly and annual periods are dated to their first day and
    fall inside it either way. Anything else is a 400, not a crash.
    """
    if not value:
        return None
    try:
        if len(value) == 7:
            first = datetime.date.fromisoformat(value + "-01")
            if not end:
                return first
            return (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) \
                - datetime.timedelta(days=1)
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "%s must be YYYY-MM or YYYY-MM-DD (got '%s')" % (name, value))


def _months_ago(period, n):
    y, m = period.year, period.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return datetime.date(y, m, 1)


# Months between consecutive periods of a dataset. The level surfaces below
# compare a reading with the one before it and with the one a year earlier;
# on a monthly series those are one and twelve months back, on a quarterly
# one three and twelve. A quarterly dataset read with monthly lags would
# report every "previous period" as missing and a trailing "12-month" sum
# over three years. Rates (yoy, ann3m) are unaffected: their lags are in
# months by definition and read correctly on any period dated by its first
# month.
PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


def _period_months(adapter):
    return PERIOD_MONTHS.get(adapter.DATASET.get("frequency"), 1)


def _period_label(period, step):
    """'Aug 2026' for a month; 'Q3 2026' for a quarter; '2026' for a year."""
    if step == 3:
        return "Q%d %d" % ((period.month - 1) // 3 + 1, period.year)
    if step == 12:
        return str(period.year)
    return period.strftime("%b %Y")


def _series_map(con, dataset):
    rows = con.execute(
        "SELECT series_id, code, name_en, name_ja, weight_per_10000, sort_order, unit "
        "FROM series WHERE dataset=? ORDER BY sort_order", [dataset]).fetchall()
    out = [dict(zip(("series_id", "code", "name_en", "name_ja", "weight", "sort_order",
                     "unit"), r))
           for r in rows]
    # Two sources name their areas only in Japanese (see app/place_en.py). The
    # English label is derived here, at the one place every series-shaped
    # endpoint loads from, so no caller has to know which datasets need it.
    for s in out:
        s["name_en"] = place_en.series_name_en(dataset, s["code"], s["name_en"])
    return out


def _values(con, series_id):
    """{period: value} for one series, official index values."""
    return dict(con.execute(
        "SELECT period, value FROM observations WHERE series_id=? ORDER BY period",
        [series_id]).fetchall())


def _values_bulk(con, series_ids):
    """{series_id: {period: value}} for many series in one query."""
    if not series_ids:
        return {}
    placeholders = ",".join("?" * len(series_ids))
    out = {}
    for sid, period, value in con.execute(
            "SELECT series_id, period, value FROM observations "
            "WHERE series_id IN (%s)" % placeholders, list(series_ids)).fetchall():
        out.setdefault(sid, {})[period] = value
    return out


def _measure_points(values, measure):
    """[(period, float|None), ...] — None marks a gap, never zero."""
    periods = sorted(values)
    out = []
    for p in periods:
        v = values[p]
        if measure == "index":
            out.append((p, v))
            continue
        lagn = {"yoy": 12, "mom": 1, "ann3m": 3}[measure]
        prev = values.get(_months_ago(p, lagn))
        if prev is None or prev == 0:
            out.append((p, None))
        elif measure == "ann3m":
            out.append((p, ((v / prev) ** 4 - 1) * 100))
        else:
            out.append((p, (v / prev - 1) * 100))
    return out


# --- flags on the latest reading ---------------------------------------------
# Calculated here, not published by the agency. Thresholds are fixed constants so
# that a marker means exactly the same thing on every row of every table.
STEP_MIN_PCT = 10.0    # the month must move the index at least this much, and...
STEP_MIN_SHARE = 0.70  # ...account for at least this share of the 12-month move
LOW_BASE_INDEX = 5.0   # below this level percent changes are arithmetically unstable

NOTES_CALC = (
    "step: the 12-month move is decomposed into its 12 monthly log changes; raised when "
    "the largest single month is at least 70% of the summed absolute change and moved the "
    "index by at least 10%. low_base: raised when the latest index level is below 5.0 "
    "(base year = 100). Both are calculated from published index values."
)


def _row_notes(values, as_of):
    """Flags on the latest reading — [] when nothing is unusual, never None.

    'step' isolates a one-month level shift from a move that accumulated over the
    year: the share test is what keeps a spike that later reverts (fresh food)
    from being flagged, because the reversion enters the denominator.
    """
    notes = []
    legs = []
    for k in range(11, -1, -1):
        p = _months_ago(as_of, k)
        cur, prev = values.get(p), values.get(_months_ago(p, 1))
        if cur is None or prev is None or cur <= 0 or prev <= 0:
            legs = []  # an incomplete year cannot be decomposed; skip the flag
            break
        legs.append((p, math.log(cur / prev)))
    if legs:
        total = sum(abs(d) for _, d in legs)
        p, d = max(legs, key=lambda leg: abs(leg[1]))
        pct = (math.exp(d) - 1) * 100
        if total > 0 and abs(d) / total >= STEP_MIN_SHARE and abs(pct) >= STEP_MIN_PCT:
            notes.append({"id": "step", "period": p.isoformat(),
                          "pct": round(pct, 4), "share": round(abs(d) / total, 4)})

    level = values.get(as_of)
    if level is not None and level < LOW_BASE_INDEX:
        notes.append({"id": "low_base", "index": level})
    return notes


def _release(con, dataset, as_of=None):
    """The live release, or — given as_of — the one in force on that date.

    An as_of release is looked up regardless of status — the point of a
    vintage is what a reader would have seen then, and every release except
    the newest is 'superseded' today (a backfilled archive is 'archived').
    It is looked up by COALESCE(published_at, ingested_at), the same
    expression the vintage store orders by: a release we fetched sits at our
    fetch, a release the agency published years ago sits where the agency
    put it.
    """
    at, published = vintages.known_at(con), vintages.published_col(con)
    sql = (
        "SELECT r.release_id, r.label, r.latest_period, r.ingested_at, "
        "       a.sha256, a.url, a.retrieved_at, s.name, s.url AS source_page, s.source_id, "
        "       d.base, d.frequency, " + published + " AS published_at, r.status "
        "FROM releases r JOIN source_artifacts a USING(artifact_id) "
        "JOIN sources s ON s.source_id = a.source_id "
        "JOIN datasets d ON d.slug = r.dataset "
        "WHERE r.dataset=? ")
    if as_of is None:
        row = con.execute(sql + "AND r.status='published'", [dataset]).fetchone()
    else:
        row = con.execute(
            sql + "AND " + at + " <= ? ORDER BY " + at + " DESC LIMIT 1",
            [dataset, vintages.cutoff(as_of)]).fetchone()
        if row is None:
            first = con.execute(
                "SELECT min(" + vintages.known_at(con) + ") FROM releases r "
                "WHERE r.dataset=?", [dataset]).fetchone()[0]
            raise HTTPException(
                400, "No release of '%s' existed on %s; the first release is %s"
                     % (dataset, as_of.isoformat(),
                        first.date().isoformat() if first else "not yet recorded"))
    if row is None:
        raise HTTPException(503, "No published release for dataset '%s'" % dataset)
    keys = ("release_id", "label", "latest_period", "ingested_at", "sha256",
            "download_url", "retrieved_at", "source_name", "source_page", "source_id",
            "base", "frequency", "published_at", "status")
    rel = dict(zip(keys, row))
    rel["latest_period"] = rel["latest_period"].isoformat()
    rel["ingested_at"] = rel["ingested_at"].isoformat() + "Z"
    # Where this release sits in history, and whether that is the agency's
    # own date or ours. A surface that cites a vintage must be able to say
    # which, so both travel rather than one collapsed field.
    rel["published_at"] = (rel["published_at"].isoformat() + "Z"
                           if rel["published_at"] else None)
    rel["known_at"] = rel["published_at"] or rel["ingested_at"]
    rel["known_at_basis"] = "agency publication" if rel["published_at"] else "our fetch"
    # The base year lives on the dataset, which names the base the *current*
    # source is on. A vintage taken from an earlier source (the 2020-base CPI
    # files before the Bureau rebased) is not on that base, and claiming it
    # would put a wrong label on a right number: leave it unstated instead.
    adapter = ADAPTERS.get(dataset)
    if adapter is not None and rel["source_id"] != adapter.SOURCE["source_id"]:
        rel["base"] = None
    rel["retrieved_at"] = rel["retrieved_at"].isoformat() + "Z"
    coverage_start = con.execute(
        "SELECT MIN(o.period) FROM observations o JOIN series s USING(series_id) "
        "WHERE s.dataset=?", [dataset]).fetchone()[0]
    rel["coverage_start"] = coverage_start.isoformat() if coverage_start else None
    return rel


@router.get("/catalog/datasets", tags=["Catalog"],
            openapi_extra={"x-example": "/api/v1/catalog/datasets"})
def catalog():
    """Every dataset on the server with its title, publisher, base, frequency
    and a one-paragraph description. The ids here are the `{dataset}` values
    the dataset endpoints take; the fuller card is /catalog/manifests."""
    con = _con()
    try:
        rows = con.execute(
            "SELECT slug, title, country, agency, base, frequency, description FROM datasets").fetchall()
        return {"datasets": [dict(zip(
            ("slug", "title", "country", "agency", "base", "frequency", "description"), r))
            for r in rows]}
    finally:
        con.close()


def health():
    """Whether each dataset is current, and whether an ingest went quiet.

    Fail-safe ingest is the right design — a bad file must never replace good
    data — but it fails *silently*, and silence looks exactly like success. In
    August 2026 the July CPI file was downloaded, archived and then never
    published; the site served June for three weeks and nothing said so.

    Two signals catch that. `stale` means the newest period we serve is older
    than the dataset says it should ever be. `unpublished_artifact` is the
    sharper one: we fetched a file and no release came out of it, which is
    always either a validation failure or a crash.
    """
    today = datetime.date.today()
    con = _con()
    try:
        out = []
        for slug in sorted(ADAPTERS):
            pres = ADAPTERS[slug].PRESENTATION
            row = con.execute(
                "SELECT r.latest_period, r.ingested_at, a.retrieved_at "
                "FROM releases r JOIN source_artifacts a USING(artifact_id) "
                "WHERE r.dataset=? AND r.status='published'", [slug]).fetchone()
            # "Fetched but never published": an artifact that produced no
            # accepted release — a failed validation or a crash. An artifact
            # that DID become a release is not orphaned, whatever its status,
            # which is what keeps a backfilled archive (134 files fetched in
            # one afternoon, each one an accepted 'archived' release) from
            # reading as 134 failures.
            newest_fetch = con.execute(
                "SELECT max(a.retrieved_at) FROM source_artifacts a "
                "JOIN sources s USING(source_id) WHERE s.dataset=? "
                "AND NOT EXISTS (SELECT 1 FROM releases r "
                "                WHERE r.artifact_id = a.artifact_id "
                "                  AND r.status <> 'rejected')", [slug]).fetchone()[0]
            vintage_count = con.execute(
                "SELECT count(*) FROM releases WHERE dataset=?", [slug]).fetchone()[0]
            # When the refresh last REACHED this dataset, as opposed to when
            # the data last moved. The two come apart exactly when something
            # is wrong, so they are reported side by side.
            checked = heartbeat.check_status(slug)
            if row is None:
                entry = {"dataset": slug, "status": "attention", "published": False,
                         "reason": "no published release"}
                entry.update(checked)
                out.append(entry)
                continue
            latest_period, ingested_at, published_fetch = row
            days = (today - latest_period).days
            stale = days > pres["stale_after_days"]
            orphan = newest_fetch is not None and newest_fetch > published_fetch
            entry = {
                "dataset": slug,
                "status": "attention" if (stale or orphan or checked["checked_overdue"]
                                          or checked["last_check_outcome"] == "failed")
                          else "ok",
                "published": True,
                "latest_period": latest_period.isoformat(),
                "days_since_latest_period": days,
                "stale_after_days": pres["stale_after_days"],
                "stale": stale,
                "last_published_at": ingested_at.isoformat() + "Z",
                # A file arrived after the one we published: the ingest that
                # read it produced nothing, and nobody was told.
                "unpublished_artifact": orphan,
                "last_fetch_at": newest_fetch.isoformat() + "Z" if newest_fetch else None,
                "vintages": vintage_count,
            }
            entry.update(checked)
            out.append(entry)
        # The EDINET-derived datasets keep their own clock: they are not
        # ingests and have no release table, so they report how far each
        # extractor has read the archive instead. Defensive because the equity
        # database is optional — a deploy without it must still answer here.
        try:
            from . import equity_api
            equity = equity_api.health()
        except Exception:                                    # noqa: BLE001
            equity = []
        # The US shelf keeps a quarterly clock of its own (the SEC's data
        # sets), reported in the same shape so the console needs no new column.
        try:
            from . import sec_api
            equity = equity + sec_api.health()
        except Exception:                                    # noqa: BLE001
            pass
        try:
            from . import sec_deep_api
            equity = equity + sec_deep_api.health()
        except Exception:                                    # noqa: BLE001
            pass
        try:
            from . import us_pay_api
            equity = equity + us_pay_api.health()
        except Exception:                                    # noqa: BLE001
            pass

        # The dataset manifests. A card that fails validation is quarantined
        # rather than fatal (see registry.py), so this is where it is noticed.
        try:
            from . import registry
            manifests = registry.status()
        except Exception:                                    # noqa: BLE001
            manifests = {"registered": 0, "bound": False, "quarantined": [],
                         "errors": []}

        # The machinery's own signal, independent of any dataset's threshold:
        # a refresh that has stopped running is a fault even while every
        # series is still comfortably inside its staleness allowance.
        machine = heartbeat.status()
        attention = (any(d["status"] == "attention" for d in out)
                     or any(d["status"] == "attention" for d in equity)
                     or bool(manifests["quarantined"])
                     or bool(machine["refresh_overdue"]))
        report = {"checked_at": datetime.datetime.now(datetime.timezone.utc)
                                .replace(microsecond=0).isoformat()
                                .replace("+00:00", "Z"),
                  "status": "attention" if attention else "ok",
                  "datasets": out,
                  "equity_extractors": equity,
                  "manifests": manifests}
        # Only freshness metadata is public; prices and account grants stay
        # behind Research's personal-account checks.
        try:
            from . import market_history
            report["private_market_data"] = market_history.health()
            if any(d["status"] == "attention" for d in report["private_market_data"]):
                report["status"] = "attention"
        except Exception:                                    # noqa: BLE001
            report["private_market_data"] = [{"status": "attention", "reason": "Monitoring unavailable"}]
            report["status"] = "attention"
        report.update(machine)
        # Whether an alarm raised here could actually reach anyone. Reported
        # because for six days in September 2026 the answer was no and
        # nothing said so.
        try:
            from . import refresh
            report.update(refresh.delivery())
        except Exception:                                    # noqa: BLE001
            pass
        # The Investment Assistant's runner, as a dataset-style row: absent
        # from here, a desk whose runs stopped would look healthy forever
        # (Ingest Guardrail 6). Only when the feature is switched on.
        try:
            from . import assistant as _assistant
            if _assistant.enabled():
                from .assistant import store as _store
                last = _store.last_success_at()
                report["assistant"] = {
                    "enabled": True,
                    "last_successful_run_at": (
                        datetime.datetime.fromtimestamp(last, datetime.timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
                        if last else None)}
        except Exception:                                    # noqa: BLE001
            pass
        return report
    finally:
        con.close()


@router.get("/catalog/health", tags=["Catalog"],
            openapi_extra={"x-example": "/api/v1/catalog/health"})
def health_endpoint(strict: int = Query(
        0, description="1: return 503 instead of 200 when anything needs attention, "
                       "so an uptime monitor can alert on data going stale or the "
                       "refresh stopping.")):
    """Is every dataset current, and did an ingest go quiet?

    One row per dataset: the newest period served, whether it is `stale`
    against the dataset's own tolerance, and whether a newer source file was
    fetched but never published (`unpublished_artifact` — a validation failure
    or a crash). Never cached, so it always reflects the running server.

    Two shapes, one report. Default: always 200, for anyone reading it.
    `?strict=1`: 503 when the report says attention, which is the only form a
    plain uptime check can act on. The same URL then covers
    both failure modes — stale data answers 503, and a service that is down
    answers nothing at all.
    """
    report = health()
    if strict and report["status"] != "ok":
        return JSONResponse(report, status_code=503)
    return report

# --- natural-language questions -------------------------------------------
#
# Unlike every other endpoint here, /ask spends money per call, so it carries a
# per-IP rate limit. The window is in-process: it bounds one worker, not a
# fleet, which is the right shape for the single-process deployment.

ASK_WINDOW_SECONDS = 60
ASK_MAX_PER_WINDOW = 10
_ASK_HITS = {}


def _rate_limited(client_ip):
    now = time.monotonic()
    cutoff = now - ASK_WINDOW_SECONDS
    for ip in [ip for ip, hits in _ASK_HITS.items() if not hits or hits[-1] < cutoff]:
        del _ASK_HITS[ip]
    hits = [t for t in _ASK_HITS.get(client_ip, []) if t >= cutoff]
    if len(hits) >= ASK_MAX_PER_WINDOW:
        _ASK_HITS[client_ip] = hits
        return True
    hits.append(now)
    _ASK_HITS[client_ip] = hits
    return False


class AskTurn(BaseModel):
    """One visible turn of the conversation, replayed by the browser."""
    role: str
    content: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    # The thread so far, oldest first. Validated and clamped in the handler
    # rather than by field constraints, so the rules read the same on either
    # pydantic major version.
    history: List[AskTurn] = []


@router.get("/agent/info", include_in_schema=False)
def agent_info():
    """Whether natural-language questions are available on this server."""
    if not _ask_enabled():
        return {"enabled": False,
                "reason": "Question answering is turned off on this server."}
    if agent is None:
        return {"enabled": False,
                "reason": "Question answering is not installed on this server."}
    if not agent.available():
        return {"enabled": False, "reason": agent.unavailable_reason()}
    return {"enabled": True, "model": agent.model_name(),
            "max_question_chars": 500,
            "rate_limit": "%d questions per %d seconds"
                          % (ASK_MAX_PER_WINDOW, ASK_WINDOW_SECONDS)}


@router.post("/{dataset}/ask", include_in_schema=False)
def ask(dataset, body: AskRequest, request: Request):
    """Answer a question in prose, with the data lookups that produced it."""
    _dataset_or_404(dataset)
    if not _ask_enabled():
        raise HTTPException(503, "Question answering is turned off on this server.")
    if agent is None:
        raise HTTPException(503, "Question answering is not installed on this server.")
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(429, "Too many questions — try again in a minute.")
    con = _con()
    try:
        release = _release(con, dataset)
    finally:
        con.close()
    history = [{"role": t.role, "content": t.content} for t in body.history]
    try:
        result = agent.ask(body.question.strip(), dataset=dataset,
                           history=history)
    except agent.AgentUnavailable as exc:
        raise HTTPException(503, str(exc))
    result.update({"dataset": dataset, "question": body.question.strip(),
                   "release": release})
    return result


@router.get("/{dataset}/releases", openapi_extra={"x-example": "/api/v1/cpi-jp/releases"})
def releases(dataset):
    """Every accepted release of this dataset, newest first: label, latest
    period, ingest time, validation result and the SHA-256 of the archived
    source file. A release is a vintage; `as_of` on /observations reads them.

    `known_at` is where the release sits in history and is what `as_of`
    compares against. It is our fetch time for a release we learned of by
    fetching it, and the agency's own publication date for a backfilled
    archive — `known_at_basis` says which, on every row.
    """
    _dataset_or_404(dataset)
    con = _con()
    try:
        at = vintages.known_at(con)
        rows = con.execute(
            "SELECT r.release_id, r.label, r.latest_period, r.ingested_at, r.status, "
            "       r.validation, a.sha256, a.bytes, a.path, "
            + vintages.published_col(con) + " AS published_at "
            "FROM releases r JOIN source_artifacts a USING(artifact_id) "
            "WHERE r.dataset=? ORDER BY " + at + " DESC, r.release_id DESC",
            [dataset]).fetchall()
        out = []
        for r in rows:
            rel = dict(zip(
                ("release_id", "label", "latest_period", "ingested_at", "status",
                 "validation", "sha256", "bytes", "archived_path", "published_at"),
                [c.isoformat() if isinstance(c, (datetime.date, datetime.datetime)) else c
                 for c in r]))
            rel["known_at"] = rel["published_at"] or rel["ingested_at"]
            rel["known_at_basis"] = ("agency publication" if rel["published_at"]
                                     else "our fetch")
            out.append(rel)
        return {"releases": out}
    finally:
        con.close()


# Formulas for the tiles a levels-and-flows dataset declares in
# PRESENTATION["overview_tiles"]. Generic calculations on published values —
# nothing BOJ-specific lives here.
LEVEL_TILE_CALCS = {
    "drawdown": (
        "value[latest] − max(value[m]) over all published months m; "
        "percent: (value[latest] / max − 1) × 100. From published values."
    ),
    "rolling_avg": (
        "mean(value[t−(w−1)] … value[t]) over the trailing w published "
        "monthly values (w in the tile's 'window' field)."
    ),
}


def _level_overview(adapter, dataset):
    """Overview for a dataset of published levels and flows.

    No index math: tiles are the latest published value, a drawdown from the
    all-time peak, or a trailing average — each declared by the adapter, so
    this stays dataset-agnostic. Published values are official; the peak
    distance and trailing average are derived and carry their formula.
    """
    pres = adapter.PRESENTATION
    step = _period_months(adapter)
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = {s["code"]: s for s in _series_map(con, dataset)}
        latest = datetime.date.fromisoformat(rel["latest_period"])
        prior = _months_ago(latest, step)

        tiles = []
        for spec in pres["overview_tiles"]:
            s = smap.get(spec["code"])
            if s is None:
                continue
            vals = _values(con, s["series_id"])
            if not vals:
                continue
            t = {"key": spec["key"], "label": spec["label"], "series_code": s["code"],
                 "series_name": s["name_en"], "type": spec["type"],
                 "unit": UNIT_LABEL.get(s["unit"], s["unit"])}
            if spec["type"] == "level":
                # A series that is not seasonally adjusted (monthly wages,
                # with bonuses in June and December) is read against the same
                # month a year earlier; the tile may ask for that comparison.
                back = spec.get("compare_months")
                base_p = _months_ago(latest, back) if back else prior
                cur, prev = vals.get(latest), vals.get(base_p)
                t.update({
                    "value": cur,
                    "delta": None if cur is None or prev is None else cur - prev,
                    "comparison": "vs " + _period_label(base_p, step),
                    "trust": "official", "calc": _calc_for("index", s["unit"]),
                })
            elif spec["type"] == "drawdown":
                cur = vals.get(latest)
                peak_p = max(vals, key=lambda p: (vals[p], p))
                peak_v = vals[peak_p]
                t.update({
                    "value": None if cur is None else cur - peak_v,
                    "pct": None if cur is None or not peak_v else (cur / peak_v - 1) * 100,
                    "peak_period": peak_p.isoformat(), "peak_value": peak_v,
                    "delta": None,
                    "comparison": "vs peak " + _period_label(peak_p, step),
                    "trust": "derived", "calc": LEVEL_TILE_CALCS["drawdown"],
                })
            elif spec["type"] == "rolling_avg":
                w = spec.get("window", 12)

                def _avg(end):
                    xs = [vals.get(_months_ago(end, k * step)) for k in range(w)]
                    return None if any(x is None for x in xs) else sum(xs) / w

                cur, prev_avg = _avg(latest), _avg(prior)
                t.update({
                    "value": cur, "window": w,
                    "delta": None if cur is None or prev_avg is None else cur - prev_avg,
                    "comparison": "vs the %d-period window ending %s"
                                  % (w, _period_label(prior, step)),
                    "trust": "derived", "calc": LEVEL_TILE_CALCS["rolling_avg"],
                })
            else:
                continue  # unknown tile type: skip rather than 500
            tiles.append(t)

        today = datetime.date.today()
        payload = {
            "dataset": dataset, "release": rel, "tiles": tiles,
            "main_series": [
                {"role": m["role"], "label": m["label"], "slot": m["slot"],
                 "code": m["code"]}
                for m in pres.get("main_series", []) if m.get("code") in smap],
            "credit_line": pres.get("credit_line"),
            "stale": (today - latest).days > pres["stale_after_days"],
            "period_months": _period_months(adapter),
        }
        # Whatever vocabulary the dataset declares for its axes — the
        # industries and capital classes of a corporate survey, the demand
        # components of the national accounts — so a page builds its
        # controls from the payload and never repeats the list.
        for key in ("items", "industries", "sizes", "components", "reference_lines"):
            if key in pres:
                payload[key] = pres[key]
        return payload
    finally:
        con.close()


@router.get("/{dataset}/overview", openapi_extra={"x-example": "/api/v1/cpi-jp/overview"})
def overview(dataset):
    """The dataset's headline reading: the main series with their latest
    value and rates of change, and the release they come from. What the
    dataset's page shows in its stat tiles."""
    adapter = _dataset_or_404(dataset)
    # An index dataset gets the YoY-tile overview below; a levels-and-flows
    # dataset that declares overview_tiles gets the level overview; anything
    # else keeps the honest 404.
    key = _index_key(adapter.PRESENTATION)
    if key is None:
        if adapter.PRESENTATION.get("overview_tiles"):
            return _level_overview(adapter, dataset)
    _index_shaped_or_404(adapter, dataset)
    pres = adapter.PRESENTATION
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = {s[key]: s for s in _series_map(con, dataset)}
        latest = datetime.date.fromisoformat(rel["latest_period"])
        prior = _months_ago(latest, 1)

        tiles = []

        def tile(key, label, series, measure, comparison_lag=1):
            vals = _values(con, series["series_id"])
            pts = dict(_measure_points(vals, measure))
            cur = pts.get(latest)
            prev = pts.get(_months_ago(latest, comparison_lag))
            delta = None if (cur is None or prev is None) else cur - prev
            tiles.append({
                "key": key, "label": label, "series_code": series["code"],
                "series_name": series["name_en"], "measure": measure,
                "unit": UNIT[measure], "value": cur, "delta_pp": delta,
                "comparison": "vs " + prior.strftime("%b %Y"),
                "trust": TRUST[measure], "calc": CALC[measure],
            })

        for m in pres["main_series"]:
            s = smap.get(m[key])
            if s:
                tile(m["role"] + "_yoy", m["label"] + " · YoY", s, "yoy")
        first = pres["main_series"][0]
        headline = smap.get(first[key])
        if headline:
            tile("headline_mom", first["label"] + " · MoM", headline, "mom")
            tile("headline_ann3m", first["label"] + " · 3m Annualized", headline, "ann3m")

        # A table published without weights (seasonally adjusted, the 1946
        # series) declares no groups and gets no decomposition.
        groups = []
        for g in _group_keys(pres):
            s = smap.get(g)
            if not s:
                continue
            vals = _values(con, s["series_id"])
            yoy = dict(_measure_points(vals, "yoy"))
            mom = dict(_measure_points(vals, "mom"))
            groups.append({
                "code": s["code"], "name_en": s["name_en"], "name_ja": s["name_ja"],
                "weight": s["weight"], "yoy": yoy.get(latest), "mom": mom.get(latest),
            })

        today = datetime.date.today()
        stale = (today - latest).days > pres["stale_after_days"]
        return {
            "dataset": dataset, "release": rel, "tiles": tiles, "groups": groups,
            # The page's source line. Without it a series page fell back to its
            # own default wording, and Hong Kong's CPI was credited to the BLS.
            "credit_line": pres.get("credit_line"),
            "main_series": [
                {"role": m["role"], "label": m["label"], "slot": m["slot"],
                 "code": smap[m[key]]["code"]}
                for m in pres["main_series"] if m[key] in smap],
            "stale": stale,
        }
    finally:
        con.close()


# Formulas for the derived columns of a levels-and-flows series listing.
LEVEL_SERIES_CALCS = {
    "latest": "Published value as released, in the series' own unit.",
    "delta_1m": "value[t] − value[t−1 month], from published values.",
    "delta_12m": "value[t] − value[t−12 months], from published values.",
    "avg_12m": "mean(value[t−11] … value[t]) over the trailing 12 published monthly values.",
    "sum_12m": "sum(value[t−11] … value[t]) over the trailing 12 published monthly values.",
}


def _level_series(adapter, dataset, q):
    """Series listing for a dataset of published levels and flows.

    No index math and no weights: each row is the latest published value with
    simple derived comparisons (1m/12m change, trailing-12m average and sum —
    the latter two are what a flow series is read by). A row whose series
    ended before the release's latest month is marked discontinued, so a
    frozen line is never mistaken for a current one.
    """
    step = _period_months(adapter)
    per_year = 12 // step
    con = _con()
    try:
        rel = _release(con, dataset)
        latest = datetime.date.fromisoformat(rel["latest_period"])
        needle = q.strip().lower()
        smap = _series_map(con, dataset)
        matched = [s for s in smap
                   if not needle
                   or needle in s["name_en"].lower()
                   or needle == s["code"].lower()]
        all_vals = _values_bulk(con, [s["series_id"] for s in matched])

        out = []
        for s in matched:
            vals = all_vals.get(s["series_id"])
            if not vals:
                continue
            as_of = max(vals)
            cur = vals.get(as_of)
            prev1 = vals.get(_months_ago(as_of, step))
            prev12 = vals.get(_months_ago(as_of, 12))
            window = [vals.get(_months_ago(as_of, k * step)) for k in range(per_year)]
            complete = not any(v is None for v in window)
            spark_from = _months_ago(as_of, 60)
            out.append({
                "code": s["code"], "name_en": s["name_en"],
                "kind": _kind(adapter.PRESENTATION, s["code"]),
                "unit": UNIT_LABEL.get(s["unit"], s["unit"]),
                "as_of": as_of.isoformat(),
                "latest": cur,
                "delta_1m": None if cur is None or prev1 is None else cur - prev1,
                "delta_12m": None if cur is None or prev12 is None else cur - prev12,
                "avg_12m": sum(window) / float(per_year) if complete else None,
                "sum_12m": sum(window) if complete else None,
                "discontinued": as_of < latest,
                "spark": [[p.isoformat(), v] for p, v in sorted(vals.items())
                          if p >= spark_from],
            })
        return {"dataset": dataset, "release": rel, "count": len(out), "query": q,
                "period_months": step,
                "calc": LEVEL_SERIES_CALCS if step == 1 else dict(
                    (k, v.replace("1 month", "%d months" % step)
                          .replace("12 published monthly values",
                                   "%d published quarterly values" % per_year
                                   if step == 3 else "one year of published values"))
                    for k, v in LEVEL_SERIES_CALCS.items()),
                "series": out}
    finally:
        con.close()


# This is the explorer's first fetch and the largest payload the API serves;
# repeat hits are absorbed by the release cache in app/cache.py, which stores
# the encoded body rather than re-running the work below.
@router.get("/{dataset}/series", openapi_extra={"x-example": "/api/v1/cpi-jp/series?q=electricity"})
def series_list(dataset, q: str = Query("", max_length=200,
                                        description="Search by English name, Japanese name or code; empty lists every series where the dataset allows it")):
    """Every series in the dataset with its latest reading, or the ones
    matching `q`. Use it to find the codes that /observations takes."""
    adapter = _dataset_or_404(dataset)
    key = _index_key(adapter.PRESENTATION)
    if key is None:
        # Every levels-and-flows dataset serves this listing. It used to be
        # gated on the dataset also declaring PRESENTATION["kinds"], which is
        # only a per-series label for the front end: thirteen datasets —
        # every trade slice, the yield curve, arrivals, the three population
        # datasets, the bank statements — answered 404 here purely for not
        # having filled that dict in, while the same series were findable
        # through the MCP search tool, which reads the table directly. The
        # kind is optional and comes back null where it is not declared.
        #
        # A dataset of tens of thousands of series (every bank's every
        # statement line) declares series_requires_query: the listing is a
        # search, not a dump, and the unqualified form is refused rather
        # than served as a multi-megabyte page nobody can read.
        if adapter.PRESENTATION.get("series_requires_query") and not q.strip():
            raise HTTPException(
                422, "'%s' has too many series to list at once; pass ?q= to search "
                     "by name or code." % dataset)
        return _level_series(adapter, dataset, q)
    _index_shaped_or_404(adapter, dataset)
    # A code-keyed index dataset may be too large to list whole (every item
    # in every US metro area); like a levels dataset it then asks for ?q=.
    if adapter.PRESENTATION.get("series_requires_query") and not q.strip():
        raise HTTPException(
            422, "'%s' has too many series to list at once; pass ?q= to search "
                 "by name or code." % dataset)
    con = _con()
    try:
        rel = _release(con, dataset)
        latest = datetime.date.fromisoformat(rel["latest_period"])
        needle = q.strip().lower()
        smap = _series_map(con, dataset)
        matched = [s for s in smap
                   if not needle
                   or needle in s["name_en"].lower()
                   or needle in (s["name_ja"] or "").lower()
                   or needle == s["code"].lower()]
        all_vals = _values_bulk(con, [s["series_id"] for s in matched])

        # Denominator for the per-row contribution column. Same formula as
        # /contributions — the headline index a year before the row's own
        # reference month — so the two surfaces can never disagree.
        head_key = adapter.PRESENTATION["main_series"][0][key]
        head = next((s for s in smap if s[key] == head_key), None)
        head_vals = {}
        if head is not None:
            head_vals = all_vals.get(head["series_id"]) or _values(con, head["series_id"])
        total_w = (head["weight"] if head and head["weight"] else 10000.0)

        out = []
        for s in matched:
            vals = all_vals.get(s["series_id"])
            if not vals:
                continue
            as_of = max(vals)
            year_ago = _months_ago(as_of, 12)
            yoy = dict(_measure_points(vals, "yoy"))
            mom = dict(_measure_points(vals, "mom"))
            ann = dict(_measure_points(vals, "ann3m"))
            # None, never 0: a series with no year-ago base has no contribution
            base = head_vals.get(year_ago)
            prior_index = vals.get(year_ago)
            contrib = None
            if (s["weight"] is not None and base and prior_index is not None
                    and vals.get(as_of) is not None):
                contrib = s["weight"] * (vals[as_of] - prior_index) / (total_w * base) * 100
            spark_from = _months_ago(latest, 60)
            spark = [[p.isoformat(), round(v, 4)] for p, v in sorted(vals.items())
                     if p >= spark_from]
            out.append({
                "code": s["code"], "name_en": s["name_en"], "name_ja": s["name_ja"],
                "weight": s["weight"], "as_of": as_of.isoformat(),
                "index": vals.get(as_of), "prev_index": vals.get(_months_ago(as_of, 1)),
                "yoy": yoy.get(as_of), "mom": mom.get(as_of), "ann3m": ann.get(as_of),
                "yoy_prior": yoy.get(year_ago), "contrib_pp": contrib,
                "discontinued": as_of < latest, "spark": spark,
                "notes": _row_notes(vals, as_of),
            })
        return {"dataset": dataset, "release": rel, "count": len(out), "query": q,
                "notes_calc": NOTES_CALC, "contrib_calc": CONTRIB_CALC, "series": out}
    finally:
        con.close()


CONTRIB_CALC = (
    "contribution[g,t] = weight[g] × (index[g,t] − index[g,t−12]) "
    "/ (10000 × headline_index[t−12]) × 100, in percentage points. "
    "Group contributions sum to headline YoY up to a small residual from "
    "the rounding of published indices and weights."
)
BREADTH_CALC = (
    "For each month, over the individually priced items with an index value "
    "in both t and t−12: share with YoY above the threshold, share rising "
    "(YoY > 0), and share falling (YoY < 0), each as % of those items."
)


@router.get("/{dataset}/contributions", openapi_extra={"x-example": "/api/v1/cpi-jp/contributions?start=2023-01"})
def contributions(dataset,
                  start: str = Query(None, description="First period, YYYY-MM"),
                  end: str = Query(None, description="Last period, YYYY-MM")):
    """Percentage-point decomposition of headline YoY by major group."""
    adapter = _dataset_or_404(dataset)
    pres = adapter.PRESENTATION
    if not pres.get("groups_ja") or not pres.get("main_series"):
        raise HTTPException(404, "Dataset '%s' has no group decomposition" % dataset)
    p_start = _period_bound(start, "start")
    p_end = _period_bound(end, "end", end=True)
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = {s["name_ja"]: s for s in _series_map(con, dataset)}
        head = smap.get(pres["main_series"][0]["name_ja"])
        groups = [smap[ja] for ja in pres["groups_ja"] if ja in smap]
        if head is None or not groups:
            raise HTTPException(503, "Headline or group series missing")
        vals = _values_bulk(con, [head["series_id"]] + [g["series_id"] for g in groups])
        hv = vals[head["series_id"]]
        total_w = head["weight"] or 10000.0

        periods = [p for p in sorted(hv)
                   if hv.get(_months_ago(p, 12)) is not None
                   and (p_start is None or p >= p_start) and (p_end is None or p <= p_end)]

        out_groups = []
        contrib_by_period = {p: [] for p in periods}
        for g in groups:
            gv = vals.get(g["series_id"], {})
            pts = []
            for p in periods:
                cur, prev = gv.get(p), gv.get(_months_ago(p, 12))
                if cur is None or prev is None or g["weight"] is None:
                    pts.append([p.isoformat(), None])
                else:
                    c = g["weight"] * (cur - prev) / (total_w * hv[_months_ago(p, 12)]) * 100
                    pts.append([p.isoformat(), round(c, 6)])
                    contrib_by_period[p].append(c)
            out_groups.append({"code": g["code"], "name_en": g["name_en"],
                               "name_ja": g["name_ja"], "weight": g["weight"], "points": pts})

        headline_pts, residual_pts = [], []
        for p in periods:
            yoy = (hv[p] / hv[_months_ago(p, 12)] - 1) * 100
            headline_pts.append([p.isoformat(), round(yoy, 6)])
            if len(contrib_by_period[p]) == len(groups):
                residual_pts.append([p.isoformat(), round(yoy - sum(contrib_by_period[p]), 6)])
            else:
                residual_pts.append([p.isoformat(), None])

        return {"dataset": dataset, "release": rel, "unit": "pp", "trust": "derived",
                "calc": CONTRIB_CALC,
                "headline": {"code": head["code"], "name_en": head["name_en"],
                             "points": headline_pts},
                "groups": out_groups,
                "residual": {"points": residual_pts}}
    finally:
        con.close()


@router.get("/{dataset}/breadth", openapi_extra={"x-example": "/api/v1/cpi-jp-items/breadth?threshold=2"})
def breadth(dataset, threshold: float = Query(2.0, ge=0.0, le=50.0,
                                             description="YoY rate, in percent, above which an item counts as rising")):
    """Share of individually priced items rising/falling year over year."""
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("breadth")
    if not cfg:
        raise HTTPException(404, "Dataset '%s' has no item-breadth definition" % dataset)
    con = _con()
    try:
        rel = _release(con, dataset)
        prefix = cfg["exclude_code_prefix"]
        leaves = [s for s in _series_map(con, dataset) if not s["code"].startswith(prefix)]
        vals = _values_bulk(con, [s["series_id"] for s in leaves])

        by_period = {}
        for s in leaves:
            sv = vals.get(s["series_id"], {})
            for p, v in sv.items():
                prev = sv.get(_months_ago(p, 12))
                if prev:
                    by_period.setdefault(p, []).append((v / prev - 1) * 100)

        points = []
        for p in sorted(by_period):
            yoys = by_period[p]
            n = len(yoys)
            points.append({
                "period": p.isoformat(),
                "n": n,
                "above_pct": round(sum(1 for y in yoys if y >= threshold) / n * 100, 4),
                "rising_pct": round(sum(1 for y in yoys if y > 0) / n * 100, 4),
                "falling_pct": round(sum(1 for y in yoys if y < 0) / n * 100, 4),
            })

        return {"dataset": dataset, "release": rel, "unit": "%", "trust": "derived",
                "calc": BREADTH_CALC, "threshold": threshold,
                "item_universe": len(leaves), "points": points}
    finally:
        con.close()


CURVE_CALC = (
    "Published constant-maturity yields exactly as released, in percent. "
    "Not recomputed, not interpolated, not smoothed. A maturity missing on "
    "a date (not yet issued) is null, never zero."
)


@router.get("/{dataset}/curve", openapi_extra={"x-example": "/api/v1/jgb-yields/curve"})
def curve(dataset):
    """The full published curve history: every date × every maturity.

    One payload serves the animated curve, the spread history and the
    maturity table — the page slices it client-side. Values are official
    yields only; anything derived from them is calculated on the page
    and carries its formula there, per the trust contract.
    """
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("curve")
    if not cfg:
        raise HTTPException(404, "Dataset '%s' has no curve definition" % dataset)
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = {s["code"]: s for s in _series_map(con, dataset)}
        codes = [m["code"] for m in cfg["maturities"] if m["code"] in smap]
        vals = _values_bulk(con, [smap[c]["series_id"] for c in codes])

        dates = sorted(set(
            p for sid in vals for p in vals[sid]))
        date_pos = dict((p, i) for i, p in enumerate(dates))

        values = {}
        maturities = []
        for m in cfg["maturities"]:
            code = m["code"]
            s = smap.get(code)
            if s is None:
                continue
            sv = vals.get(s["series_id"], {})
            col = [None] * len(dates)
            first = None
            for p, v in sv.items():
                col[date_pos[p]] = v
                if first is None or p < first:
                    first = p
            values[code] = col
            maturities.append({
                "code": code, "years": m["years"], "name_en": s["name_en"],
                "first_period": first.isoformat() if first else None,
            })

        latest = datetime.date.fromisoformat(rel["latest_period"])
        today = datetime.date.today()
        return {
            "dataset": dataset, "release": rel,
            "unit": "%", "trust": "official", "calc": CURVE_CALC,
            "credit_line": adapter.PRESENTATION.get("credit_line"),
            "stale": (today - latest).days
                     > adapter.PRESENTATION["stale_after_days"],
            "maturities": maturities,
            "spreads": cfg.get("spreads", []),
            "history_series": cfg.get("history_series", []),
            "dates": [p.isoformat() for p in dates],
            "values": values,
        }
    finally:
        con.close()


ARRIVALS_CALC = (
    "Published visitor arrivals exactly as released, in persons. Not "
    "recomputed, not seasonally adjusted, not annualised. A market not "
    "published for a month is null, never zero — the two most recent "
    "months are estimates covering a subset of markets."
)


@router.get("/{dataset}/arrivals", openapi_extra={"x-example": "/api/v1/jnto-visitors/arrivals"})
def arrivals(dataset):
    """Every market × every month, plus the hierarchy that relates them.

    One payload serves the whole inbound page — headline history, recovery
    against a baseline year, market mix and the contribution decomposition
    — because each of those is a different slice of the same counts. Only
    published counts cross the wire; shares, growth rates, recovery indices
    and contributions are calculated on the page and carry their formula
    there, exactly as the curve surface treats spreads.

    `hierarchy` is served rather than assumed: a market's parent has changed
    twice in the published history (the Middle East grouping from 2020, the
    Nordic one from 2023), so the page must be told the structure, never
    infer it from the codes.
    """
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("arrivals")
    if not cfg:
        raise HTTPException(404, "Dataset '%s' has no arrivals definition" % dataset)
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = _series_map(con, dataset)
        vals = _values_bulk(con, [s["series_id"] for s in smap])

        periods = sorted(set(p for sid in vals for p in vals[sid]))
        period_pos = dict((p, i) for i, p in enumerate(periods))

        hierarchy = cfg.get("hierarchy") or {}
        kinds = cfg.get("kinds") or {}
        markets = []
        values = {}
        for s in smap:
            col = [None] * len(periods)
            for p, v in vals.get(s["series_id"], {}).items():
                col[period_pos[p]] = v
            values[s["code"]] = col
            markets.append({
                "code": s["code"],
                "name_en": s["name_en"],
                "name_ja": s["name_ja"],
                "parent": hierarchy.get(s["code"]),
                "kind": _kind(adapter.PRESENTATION, s["code"]),
            })

        # Which months are still estimates is recorded by the ingest that
        # published them, in the release's validation summary. It is a
        # revision status, not a trust level: these counts are official,
        # rounded to the nearest 100 and covering a subset of markets.
        row = con.execute(
            "SELECT validation FROM releases "
            "WHERE dataset=? AND status='published'", [dataset]).fetchone()
        provisional = []
        if row and row[0]:
            try:
                provisional = json.loads(row[0]).get("provisional_periods") or []
            except ValueError:
                provisional = []

        latest = datetime.date.fromisoformat(rel["latest_period"])
        today = datetime.date.today()
        return {
            "dataset": dataset, "release": rel,
            "unit": "persons", "trust": "official", "calc": ARRIVALS_CALC,
            "credit_line": adapter.PRESENTATION.get("credit_line"),
            "stale": (today - latest).days
                     > adapter.PRESENTATION["stale_after_days"],
            "headline": cfg.get("headline"),
            "regions": cfg.get("regions", []),
            "feature_markets": cfg.get("feature_markets", []),
            "baseline_year": cfg.get("baseline_year"),
            "provisional_periods": provisional,
            "markets": markets,
            "periods": [p.isoformat() for p in periods],
            "values": values,
        }
    finally:
        con.close()


ACCOMMODATION_CALC = (
    "Published guest nights (person-nights) and room occupancy rates exactly "
    "as the Agency released them. Not recomputed, not seasonally adjusted, not "
    "annualised. Growth, shares, recovery against a baseline year and "
    "contributions are calculated on the page and carry their formula there. "
    "A figure the survey did not publish is null, never zero."
)

# The three series every area carries for the whole run of history. They are
# what the regions view ranks and maps on, so they ship for every area at once
# while the rest of the cube is served only for the area being read.
_ACC_BACKBONE = ("nights.%s", "nights.%s.fx", "occ.%s")


@router.get("/{dataset}/accommodation", openapi_extra={"x-example": "/api/v1/accommodation-jp/accommodation?area=13"})
def accommodation(dataset, area: str = Query(None, max_length=4,
                                             description="Two-digit JIS prefecture code (13 = Tokyo) or a region code; omit for All Japan")):
    """Guest nights and occupancy: every area's backbone, one area's detail.

    The cube is area x measure x category x month, and the categories differ
    per measure — 25 nationalities, 6 hotel types, 5 size bands, 2 purposes, 2
    residences. Shipping all of it would be several megabytes for a page that
    draws one area at a time, so this follows the trade surface rather than the
    arrivals one: the backbone (guest nights, foreign guest nights, room
    occupancy) rides along for all 58 areas, and the dimensional detail is
    served for the area actually being read. A citable URL therefore names
    exactly one view, and the regions ranking needs no second round trip.

    The municipality series ship in full because they are small: three metrics
    for 210 towns, and only from 2026, when the survey began publishing them.

    Areas are served, not inferred. The 47 prefectures partition the country;
    the 10 transport-bureau regions are a republication of the same nights and
    are marked `kind: "region"` so nothing adds them to the prefectures.
    """
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("accommodation")
    if not cfg:
        raise HTTPException(
            404, "Dataset '%s' has no accommodation definition" % dataset)

    national = cfg["national"]
    prefectures, bureaus = cfg["prefectures"], cfg["bureaus"]
    known = [national] + list(prefectures) + list(bureaus)
    area = area or national
    if area not in known:
        raise HTTPException(404, "Unknown area '%s' for %s" % (area, dataset))

    con = _con()
    try:
        rel = _release(con, dataset)
        smap = _series_map(con, dataset)
        by_code = dict((s["code"], s) for s in smap)

        periods = [p for (p,) in con.execute(
            "SELECT DISTINCT o.period FROM observations o "
            "JOIN series s USING(series_id) WHERE s.dataset=? ORDER BY o.period",
            [dataset]).fetchall()]
        pos = dict((p, i) for i, p in enumerate(periods))

        def columns(codes):
            ids = dict((by_code[c]["series_id"], c) for c in codes if c in by_code)
            out = {}
            for sid, values in _values_bulk(con, list(ids)).items():
                column = [None] * len(periods)
                for period, value in values.items():
                    column[pos[period]] = value
                out[ids[sid]] = column
            return out

        backbone = columns([template % code
                            for code in known for template in _ACC_BACKBONE])

        # Everything else the chosen area publishes, minus what already rode
        # along in the backbone.
        already = set(template % area for template in _ACC_BACKBONE)
        detail_codes = [s["code"] for s in smap
                        if s["code"] not in already
                        and not s["code"].startswith("muni.")
                        and s["code"].split(".")[1] == area]
        detail = columns(detail_codes)

        # The municipality tables begin at the stratification break, so their
        # columns would otherwise be 97% nulls against the full period axis.
        # They get their own shorter axis instead — the same values, a third of
        # a megabyte less of them.
        muni_all = columns([s["code"] for s in smap
                            if s["code"].startswith("muni.")])
        muni_first = min((i for column in muni_all.values()
                          for i, v in enumerate(column) if v is not None),
                         default=len(periods))
        municipal = dict((code, column[muni_first:])
                         for code, column in muni_all.items())

        def label(code):
            s = by_code.get(code)
            return {"name_en": s["name_en"], "name_ja": s["name_ja"],
                    "unit": s["unit"]} if s else None

        areas = [{"code": code,
                  "name_en": cfg["area_names"].get(code, code),
                  "kind": ("national" if code == national
                           else "prefecture" if code in prefectures else "region")}
                 for code in known]

        latest = datetime.date.fromisoformat(rel["latest_period"])
        today = datetime.date.today()
        return {
            "dataset": dataset, "release": rel,
            "trust": "official", "calc": ACCOMMODATION_CALC,
            "credit_line": adapter.PRESENTATION.get("credit_line"),
            "stale": (today - latest).days
                     > adapter.PRESENTATION["stale_after_days"],
            "area": area,
            "areas": areas,
            "national": national,
            "baseline_year": cfg.get("baseline_year"),
            "break_period": cfg.get("break_period"),
            "break_note": cfg.get("break_note"),
            "feature_areas": cfg.get("feature_areas", []),
            "feature_nationalities": cfg.get("feature_nationalities", []),
            "dimensions": {
                "facility_types": cfg.get("facility_types", []),
                "room_bands": cfg.get("room_bands", []),
                "nationalities": cfg.get("nationalities", []),
                "purposes": cfg.get("purposes", []),
                "residence": cfg.get("residence", []),
            },
            "municipalities": cfg.get("municipalities", []),
            "periods": [p.isoformat() for p in periods],
            "backbone": backbone,
            "detail": detail,
            "municipal": municipal,
            "municipal_periods": [p.isoformat() for p in periods[muni_first:]],
            "labels": dict((code, label(code))
                           for code in list(backbone) + list(detail)),
        }
    finally:
        con.close()


PREFECTURES_CALC = (
    "Published figures, exactly as released — counts of people, except where "
    "a dataset's `indicators` mark a series `kind: \"rate\"`, which is a "
    "published rate that could not be rebuilt from the counts and is stored "
    "as issued. A rate is never summed or aggregated across areas. Shares, "
    "change rates, crude birth and death rates and age-group aggregates are "
    "calculated on the page from these figures and carry their formula there."
)


@router.get("/{dataset}/prefectures", openapi_extra={"x-example": "/api/v1/population-jp/prefectures"})
def prefectures(dataset, prefecture: str = Query(None, max_length=2,
                                                 description="Two-digit JIS prefecture code, e.g. 13 for Tokyo; omit for every prefecture")):
    """Every area × every measure × every period, plus the geography.

    One payload serves a whole population surface — the map, the rankings
    and any one prefecture's history are three slices of the same counts,
    and a map needs all 47 areas at once, which /observations (capped at
    eight series) cannot give it.

    Only published figures cross the wire. A prefecture's share of foreign
    residents, its change rate, its crude birth and death rates and its
    65-and-over share are arithmetic on those figures, done on the page so
    the formula travels with the number, exactly as the curve surface treats
    spreads. Almost everything served here is a count; the exception is a
    series the dataset marks `kind: "rate"` in `indicators` — a published
    rate that cannot be rebuilt from the counts (the total fertility rate is
    the only one today). Such a series is stored as issued and must never be
    summed across areas.

    `geographies`, `measures` and `bases` are served rather than assumed:
    the two population datasets carry different measures and, in the
    historical one, different reference dates per measure. A surface that
    guessed either would mislabel a year.
    """
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("prefectures")
    if not cfg:
        raise HTTPException(
            404, "Dataset '%s' has no prefecture definition" % dataset)
    # A municipality dataset is two orders of magnitude larger than a
    # prefecture one — about 585,000 series — so it is served one prefecture
    # at a time rather than as one payload nobody can use. The dataset says
    # whether it needs the filter; the endpoint does not guess from a count.
    if cfg.get("requires_area_filter") and not prefecture:
        raise HTTPException(
            400, "'%s' covers every municipality in Japan and must be asked "
                 "for one prefecture at a time: add ?prefecture=NN, a "
                 "two-digit JIS code (13 = Tokyo)." % dataset)
    if prefecture is not None and (len(prefecture) != 2 or not prefecture.isdigit()):
        raise HTTPException(400, "prefecture must be a two-digit JIS code")
    con = _con()
    try:
        rel = _release(con, dataset)
        smap = _series_map(con, dataset)
        if prefecture:
            national = cfg.get("national") or ""
            smap = [s for s in smap
                    if s["code"].startswith(prefecture)
                    or s["code"].startswith(national + ".")]
        vals = _values_bulk(con, [s["series_id"] for s in smap])

        periods = sorted(set(p for sid in vals for p in vals[sid]))
        period_pos = dict((p, i) for i, p in enumerate(periods))

        values = {}
        units = {}
        for s in smap:
            column = [None] * len(periods)
            for p, v in vals.get(s["series_id"], {}).items():
                column[period_pos[p]] = v
            # A series the source has stopped publishing still has history;
            # one that never had a value is not sent at all.
            if any(v is not None for v in column):
                values[s["code"]] = column
                units[s["code"]] = s["unit"]

        latest = datetime.date.fromisoformat(rel["latest_period"])
        today = datetime.date.today()
        payload = {
            "dataset": dataset, "release": rel,
            "trust": "official", "calc": PREFECTURES_CALC,
            "credit_line": adapter.PRESENTATION.get("credit_line"),
            "stale": (today - latest).days
                     > adapter.PRESENTATION["stale_after_days"],
            "periods": [p.isoformat() for p in periods],
            "values": values,
            "units": units,
        }
        # Whatever vocabulary this dataset defines: geographies and regions
        # for both, segments and age bands for the register one, dating
        # bases for the historical one.
        for key in ("headline", "national", "geographies", "regions",
                    "segments", "measures", "indicators", "age_bands",
                    "age_groups", "sexes", "bases", "levels",
                    "requires_area_filter", "companion_dataset"):
            if key in cfg:
                payload[key] = cfg[key]
        # A dataset whose areas come from the file rather than from a pinned
        # list carries them in the release it was published from. Serve only
        # the ones in scope, so a filtered request stays small.
        if "geographies" not in payload:
            row = con.execute(
                "SELECT validation FROM releases "
                "WHERE dataset=? AND status='published'", [dataset]).fetchone()
            if row and row[0]:
                try:
                    geos = json.loads(row[0]).get("geographies") or []
                except ValueError:
                    geos = []
                if prefecture:
                    geos = [g for g in geos
                            if g["code"].startswith(prefecture)
                            or g["code"] == cfg.get("national")]
                # The release stores each area's filed Japanese name. Attach the
                # published romanization beside it so the picker is navigable in
                # English; the filed name is never replaced.
                for g in geos:
                    if "name_en" not in g:
                        en = place_en.municipality_en(g.get("code"))
                        if en:
                            g["name_en"] = en
                payload["geographies"] = geos
        return payload
    finally:
        con.close()


TRADE_CALC = (
    "Customs-declared values and quantities exactly as published by the "
    "Ministry of Finance. Value is in thousands of yen; quantity is in the "
    "commodity's published unit. Growth rates, shares, world totals and "
    "average unit values are calculated from these published figures and "
    "carry their formula wherever they are shown."
)

TRADE_WORLD_CALC = (
    "world[commodity, t] = Σ value[partner, commodity, t] over every partner "
    "country the Ministry publishes for that commodity, including the "
    "non-country entries (For Order, Unknown, bonded areas). The Ministry "
    "publishes no world total in this table, so it is summed here rather "
    "than read off."
)


@router.get("/{dataset}/trade", openapi_extra={"x-example": "/api/v1/trade-semis/trade?flow=exp&commodity=70323050"})
def trade(dataset,
          flow: str = Query(None, description="'exp' or 'imp'"),
          commodity: str = Query(None, description="published commodity code")):
    """One commodity's trade with every partner, plus world totals for all of them.

    The cube here is commodity × partner × month × (value, quantity) in two
    directions — far too much to ship in one payload the way /arrivals does, so
    the partner detail is served for the commodity actually being read while the
    world totals for every commodity ride along. That is what the page needs to
    draw its tiles and its commodity comparison without a second round trip, and
    it keeps a citable URL pointing at exactly one view.

    Only published values cross the wire. World totals are the one exception and
    say so: this table has no world row, so the sum is labelled derived and
    carries its formula, exactly as a spread or a contribution does.

    Export and import commodity codes are separate vocabularies — `70311000` is
    semiconductors on the import side and audio equipment on the export side —
    so the commodity is always validated against the requested direction's list
    rather than against a shared one.
    """
    adapter = _dataset_or_404(dataset)
    cfg = adapter.PRESENTATION.get("trade")
    if not cfg:
        raise HTTPException(404, "Dataset '%s' has no trade definition" % dataset)

    flow = flow or cfg["default_flow"]
    if flow not in cfg["commodities"]:
        raise HTTPException(400, "Unknown flow '%s'; one of %s"
                                 % (flow, sorted(cfg["commodities"])))
    catalogue = cfg["commodities"][flow]
    commodity = commodity or cfg["default_commodity"][flow]
    chosen = next((c for c in catalogue if c["code"] == commodity), None)
    if chosen is None:
        raise HTTPException(
            404, "Commodity '%s' is not published for %s; one of %s"
                 % (commodity, flow, ", ".join(c["code"] for c in catalogue)))

    con = _con()
    try:
        rel = _release(con, dataset)

        periods = [p for (p,) in con.execute(
            "SELECT DISTINCT o.period FROM observations o "
            "JOIN series s USING(series_id) WHERE s.dataset=? ORDER BY o.period",
            [dataset]).fetchall()]
        pos = dict((p, i) for i, p in enumerate(periods))

        # The slice being read: one commodity, one direction, every partner,
        # both measures.
        values, quantities, qty_unit = {}, {}, None
        rows = con.execute(
            "SELECT s.code, s.unit, o.period, o.value FROM observations o "
            "JOIN series s USING(series_id) "
            "WHERE s.dataset=? AND s.code LIKE ?",
            [dataset, "%s.%s.%%" % (flow, commodity)]).fetchall()
        for code, unit, period, value in rows:
            _flow, _com, partner, measure = code.split(".")
            target = values if measure == "val" else quantities
            if measure == "qty" and qty_unit is None:
                qty_unit = unit
            column = target.get(partner)
            if column is None:
                column = target[partner] = [None] * len(periods)
            column[pos[period]] = value

        present = sorted(set(values) | set(quantities))
        partners = [{
            "code": code,
            "name_en": adapter.PARTNER_EN.get(code, code),
            "name_ja": adapter.PARTNER_JA.get(code),
            "region": adapter.partner_region(code),
            # The Ministry's non-country entries are published partners and are
            # part of the total, but they are not places and a map or a country
            # ranking must be able to tell them apart.
            "is_country": adapter.partner_region(code) != "7",
        } for code in present]

        # World totals for every commodity in both directions, summed in the
        # database rather than shipped as partner detail nobody asked for.
        world = {}
        for f, c, period, total in con.execute(
                "SELECT split_part(s.code,'.',1), split_part(s.code,'.',2), "
                "       o.period, sum(o.value) "
                "FROM observations o JOIN series s USING(series_id) "
                "WHERE s.dataset=? AND split_part(s.code,'.',4)='val' "
                "GROUP BY 1,2,3", [dataset]).fetchall():
            key = "%s.%s" % (f, c)
            column = world.get(key)
            if column is None:
                column = world[key] = [None] * len(periods)
            column[pos[period]] = total

        latest = datetime.date.fromisoformat(rel["latest_period"])
        today = datetime.date.today()
        return {
            "dataset": dataset, "release": rel,
            "trust": "official", "calc": TRADE_CALC,
            "credit_line": adapter.PRESENTATION.get("credit_line"),
            "stale": (today - latest).days
                     > adapter.PRESENTATION["stale_after_days"],
            "flow": flow,
            "flows": cfg["flows"],
            "commodity": chosen,
            "commodities": cfg["commodities"],
            "feature_partners": cfg.get("feature_partners", []),
            # What the shared trade page cannot know on its own: subtitle,
            # fixed partner colours, first-load partners and the four tiles.
            "page": cfg.get("page", {}),
            "regions": [{"key": k, "label": l} for k, l in adapter.REGIONS],
            "units": {"value": UNIT_LABEL.get(cfg["value_unit"], cfg["value_unit"]),
                      "quantity": UNIT_LABEL.get(qty_unit, qty_unit)},
            "periods": [p.isoformat() for p in periods],
            "partners": partners,
            "values": values,
            "quantities": quantities,
            "world": world,
            "world_trust": "derived",
            "world_calc": TRADE_WORLD_CALC,
        }
    finally:
        con.close()


FISCAL_CALC = (
    "Σ value[m] over the %d months of each %s of a fiscal year ending in "
    "month %d, from published monthly values. Only complete periods are "
    "shown: a period with an unpublished month is left out, never summed "
    "short. Fiscal years are named for the calendar year in which they begin."
)


@router.get("/{dataset}/observations", openapi_extra={"x-example": "/api/v1/cpi-jp/observations?series=0001,0161&measure=yoy&start=2020-01"})
def observations(dataset,
                 series: str = Query(..., max_length=200,
                                     description="Comma-separated series codes, up to eight; find them with /series"),
                 measure: str = Query("index", description="index (as published) or a calculated rate the dataset offers: yoy, mom, ann3m, ... — see the dataset's measures"),
                 start: str = Query(None, description="First period, YYYY-MM (YYYY-MM-DD for daily series)"),
                 end: str = Query(None, description="Last period, inclusive"),
                 as_of: str = Query(None, description="YYYY-MM-DD: the data as it stood on that date, from the vintage history"),
                 period: str = Query(
                     None, description="'fiscal_quarter' or 'fiscal_year': sum monthly "
                                       "flows into the fiscal periods of a company whose "
                                       "year ends in month fy_end. Flows only."),
                 fy_end: int = Query(3, ge=1, le=12,
                                     description="Month the fiscal year ends (3 = March)"),
                 format: str = Query("json", description="'json' or 'csv'"),
                 request: Request = None):
    """Published values for up to eight series.

    With ``as_of=YYYY-MM-DD`` the response is the data *as it stood on that
    date* — the numbers a reader would have had then, before any later
    revision. That is what makes a chart citable and a backtest honest, and it
    is served from the append-only vintage history, not reconstructed.

    With ``period=fiscal_quarter`` (or ``fiscal_year``) and ``fy_end``, monthly
    flows are summed into a company's own fiscal periods — what a model built
    on that company's reported quarters needs. Only published values that are
    monthly amounts qualify (customs values and quantities, visitor counts);
    an index, a stock or a rate is refused rather than summed into nonsense,
    and a period missing any month is left out rather than summed short.

    With ``format=csv`` the same numbers come back as one wide table — one
    row per period, one column per series — under a ``#`` metadata block
    that states the source, the release, the formula and the vintage, so a
    file saved from this URL is citable on its own. pandas reads it with
    ``read_csv(url, comment="#")``, R with ``read.csv(url, comment.char="#")``.
    """
    adapter = _dataset_or_404(dataset)
    if format not in ("json", "csv"):
        raise HTTPException(400, "format must be 'json' or 'csv' (got '%s')" % format)
    if measure not in CALC:
        raise HTTPException(400, "Unknown measure '%s'; one of %s" % (measure, sorted(CALC)))
    if period is not None and period not in fiscal.GRANULARITIES:
        raise HTTPException(400, "period must be one of %s" % ", ".join(fiscal.GRANULARITIES))
    if period is not None and measure != "index":
        raise HTTPException(
            400, "Fiscal roll-ups sum published values; request measure=index with period=.")
    # Monthly-lag rates presuppose monthly periods; on a daily series the
    # lag lookup would mostly miss and a %-change of a rate is not a
    # meaningful measure anyway.
    if measure != "index" and adapter.DATASET.get("frequency") == "daily":
        raise HTTPException(
            400, "'%s' is a daily dataset; monthly rate measures do not "
                 "apply. Request measure=index for published values." % dataset)
    codes = [c.strip() for c in series.split(",") if c.strip()][:8]
    if not codes:
        raise HTTPException(400, "No series codes given")
    p_start = _period_bound(start, "start")
    p_end = _period_bound(end, "end", end=True)
    try:
        p_as_of = datetime.date.fromisoformat(as_of) if as_of else None
    except ValueError:
        raise HTTPException(400, "as_of must be a date, YYYY-MM-DD (got '%s')" % as_of)
    con = _con()
    try:
        rel = _release(con, dataset, as_of=p_as_of)
        as_of_values = (vintages.values_as_of(con, dataset, p_as_of, codes)
                        if p_as_of else None)
        smap = {s["code"]: s for s in _series_map(con, dataset)}
        out = []
        units = set()
        for code in codes:
            s = smap.get(code)
            if s is None:
                raise HTTPException(404, "Unknown series code '%s'" % code)
            units.add(s.get("unit"))
            # A flow crosses zero (redemptions are negative, and a net flow is
            # negative during runoff). A percentage change across a sign change
            # is arithmetic noise, so rates are refused on flow series rather
            # than served with a caveat.
            if measure != "index" and _kind(adapter.PRESENTATION, code) == "flow":
                raise HTTPException(
                    400, "'%s' is a flow series and crosses zero; percentage "
                         "changes are not meaningful. Request measure=index." % code)
            # A rate is already a percentage. A percentage change OF a
            # percentage (a loan rate going from 1% to 2% is "+100%") is the
            # unit confusion the trust contract exists to prevent: the change
            # in a rate is in percentage points, and the tiles carry that.
            # A ratio of counts (job openings per applicant) is refused the
            # same way, and its change is in points, not percentage points.
            if measure != "index" and _kind(adapter.PRESENTATION, code) == "rate":
                if s.get("unit") == "ratio":
                    raise HTTPException(
                        400, "'%s' is a ratio; its change is read in points, not as "
                             "a percentage of itself. Request measure=index and "
                             "difference the values." % code)
                raise HTTPException(
                    400, "'%s' is a rate in percent; a percentage change of a "
                         "rate is not meaningful. Request measure=index and "
                         "difference the values in percentage points." % code)
            raw = (as_of_values.get(code, {}) if as_of_values is not None
                   else _values(con, s["series_id"]))
            pts = _measure_points(raw, measure)
            pts = [(p, v) for p, v in pts
                   if (p_start is None or p >= p_start) and (p_end is None or p <= p_end)]
            entry = {"code": code, "name_en": s["name_en"], "name_ja": s["name_ja"]}
            if period is not None:
                try:
                    rolled, dropped = fiscal.roll_up(pts, s.get("unit"), fy_end, period)
                except fiscal.NotAFlow as exc:
                    raise HTTPException(400, "'%s': %s" % (code, exc))
                entry["points"] = [[p.isoformat(), round(v, 6)] for p, v, _l in rolled]
                entry["labels"] = [l for _p, _v, l in rolled]
                entry["months_not_summed"] = [d.isoformat() for d in dropped]
            else:
                entry["points"] = [[p.isoformat(), None if v is None else round(v, 6)]
                                   for p, v in pts]
            out.append(entry)
        # one response, one measure type — never a yen level and an index
        # sharing an axis (they would be silently incomparable)
        if measure == "index" and len(units) > 1:
            raise HTTPException(
                400, "Requested series have different published units (%s); "
                     "published levels of different kinds must not share an axis."
                     % ", ".join(sorted(UNIT_LABEL.get(u, str(u)) for u in units)))
        unit = units.pop() if len(units) == 1 else None
        body = {"dataset": dataset, "measure": measure, "unit": _unit_for(measure, unit),
                "trust": TRUST[measure], "calc": _calc_for(measure, unit),
                "release": rel, "series": out}
        if period is not None:
            # A sum of published values is derived, and says which calendar it used.
            body["trust"] = "derived"
            body["period"] = period
            body["fy_end"] = fy_end
            body["calc"] = FISCAL_CALC % (
                3 if period == "fiscal_quarter" else 12,
                "quarter" if period == "fiscal_quarter" else "year", fy_end)
        if p_as_of:
            # The vintage is part of the citation: the same URL must return the
            # same numbers next year, so the response says which one it read.
            body["as_of"] = p_as_of.isoformat()
        if format == "csv":
            return _observations_csv(body, adapter, request)
        return body
    finally:
        con.close()


def _csv_cell(text):
    text = "" if text is None else str(text)
    return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\n') else text


def _observations_csv(body, adapter, request):
    """The observations response as a wide CSV under a ``#`` metadata block.

    The block is the trust contract in file form: a reader who saves this
    file and nothing else can still see where every column came from, which
    release it was read from, how a rate was calculated and which vintage
    it reflects. Column headers are the series codes (stable, unambiguous);
    the metadata names each one. Blank cells are missing values, never zero.
    """
    rel = body["release"]
    manifest = getattr(adapter, "MANIFEST", None) or {}
    title = (manifest.get("name") or {}).get("en") or adapter.DATASET.get("title") \
        or body["dataset"]
    credit = (manifest.get("source") or {}).get("credit") \
        or adapter.PRESENTATION.get("credit_line") \
        or "Source: %s." % adapter.DATASET.get("agency", "")
    lines = []
    w = lines.append
    w("# Plover Analytics — %s (%s)" % (title, body["dataset"]))
    w("# %s" % credit)
    w("# Source document: %s — %s" % (rel["source_name"], rel["source_page"]))
    w("# Source file: %s, retrieved %s, sha256 %s"
      % (rel["download_url"], rel["retrieved_at"], rel["sha256"]))
    w("# Release: %s; latest period %s; ingested %s"
      % (rel["label"], rel["latest_period"], rel["ingested_at"]))
    if body.get("as_of"):
        w("# Point in time: the data as it stood on %s. This URL returns the same "
          "numbers at any later date." % body["as_of"])
        release = body.get("release") or {}
        if release.get("known_at"):
            # Which release answered, and whether its place in history is the
            # agency's own publication date or the moment we fetched it. A
            # citation that does not say this cannot be checked.
            w("# Vintage: release \u201c%s\u201d, in force from %s (%s)"
              % (release.get("label", ""), release["known_at"][:10],
                 release.get("known_at_basis", "our fetch")))
    else:
        w("# Vintage: live release. Add &as_of=YYYY-MM-DD to freeze this view for citation.")
    trust = ("Official statistic — published values, exactly as released"
             if body["trust"] == "official" else "Derived — calculated from published values")
    w("# Measure: %s; unit: %s; trust: %s" % (body["measure"], body["unit"], trust))
    w("# Calculation: %s" % body["calc"])
    if body.get("period"):
        w("# Period: %s, fiscal year ending in month %d; a period missing any month "
          "is left out rather than summed short" % (body["period"], body["fy_end"]))
    for s in body["series"]:
        w("# Column %s: %s%s" % (s["code"], s["name_en"],
                                 (" / " + s["name_ja"]) if s.get("name_ja") else ""))
        if s.get("months_not_summed"):
            w("# Column %s: months not summed (incomplete period): %s"
              % (s["code"], ", ".join(s["months_not_summed"])))
    w("# Blank = not published or not available; never zero.")
    if request is not None:
        w("# Retrieved from %s on %s" % (str(request.url),
                                        datetime.date.today().isoformat()))
    fiscal_labels = body.get("period") is not None
    daily = rel.get("frequency") == "daily"
    periods = set()
    labels = {}
    cols = {}
    for s in body["series"]:
        cols[s["code"]] = {p: v for p, v in s["points"]}
        periods.update(cols[s["code"]])
        if fiscal_labels:
            for (p, _v), label in zip(s["points"], s.get("labels") or []):
                labels[p] = label
    header = ["period"] + (["label"] if fiscal_labels else []) + \
             [s["code"] for s in body["series"]]
    out = io.StringIO()
    out.write("\n".join(lines) + "\n")
    out.write(",".join(_csv_cell(h) for h in header) + "\n")
    for p in sorted(periods):
        # daily series keep the full date; monthly keep YYYY-MM, as the
        # chart download does
        row = [p if daily or fiscal_labels else p[:7]]
        if fiscal_labels:
            row.append(_csv_cell(labels.get(p, "")))
        for s in body["series"]:
            v = cols[s["code"]].get(p)
            row.append("" if v is None else repr(v))
        out.write(",".join(row) + "\n")
    name = "%s-%s" % (body["dataset"], body["measure"])
    if body.get("period"):
        name += "-" + body["period"]
    if body.get("as_of"):
        name += "-asof-" + body["as_of"]
    return PlainTextResponse(out.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":
                                      'attachment; filename="%s.csv"' % name})


@router.get("/{dataset}/revisions", openapi_extra={"x-example": "/api/v1/cpi-jp/revisions?series=0001&period=2026-07"})
def revisions(dataset,
              series: str = Query(..., max_length=40, description="One series code"),
              period: str = Query(None, description="The period whose revision history to show, YYYY-MM; omit for every revised period")):
    """How one series has been revised, release by release.

    Only releases that actually changed a value appear: the vintage store is
    change-only, so two consecutive entries for a period are always a real
    revision, never a republished number. `change` is the difference from the
    value the previous release carried — a derived figure, so it travels with
    its formula rather than an Official Statistic badge.
    """
    _dataset_or_404(dataset)
    code = series.strip()
    try:
        p_period = datetime.date.fromisoformat(period + "-01") if period else None
    except ValueError:
        raise HTTPException(400, "period must be YYYY-MM (got '%s')" % period)
    con = _con()
    try:
        row = con.execute(
            "SELECT series_id, name_en, name_ja, unit FROM series WHERE dataset=? AND code=?",
            [dataset, code]).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown series code '%s'" % code)
        row = (row[0], place_en.series_name_en(dataset, code, row[1]), row[2], row[3])
        rows = vintages.revisions(con, dataset, code, p_period)
        by_period = {}
        for obs_period, value, release_id, label, at, published_at in rows:
            entry = by_period.setdefault(obs_period.isoformat(), [])
            change = None
            if entry and entry[-1]["value"] is not None and value is not None:
                change = round(value - entry[-1]["value"], 6)
            entry.append({
                "value": value,                       # None = withdrawn
                "release_id": release_id,
                "release_label": label,
                "known_at": at.isoformat() + "Z",
                # Whether that stamp is the agency's own publication date or
                # the moment we fetched the file.
                "known_at_basis": "agency publication" if published_at else "our fetch",
                "change": change,
                "first": not entry,
            })
        revised = {p: v for p, v in by_period.items() if len(v) > 1}
        return {
            "dataset": dataset,
            "series": {"code": code, "name_en": row[1], "name_ja": row[2], "unit": row[3]},
            "trust": "derived",
            "calc": "change = value[this release] − value[previous release carrying "
                    "this period]. Values themselves are official, exactly as published "
                    "in each release; only the difference between them is calculated.",
            "periods_recorded": len(by_period),
            "periods_revised": len(revised),
            "history": by_period,
        }
    finally:
        con.close()
