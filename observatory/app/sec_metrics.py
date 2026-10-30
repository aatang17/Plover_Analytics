# -*- coding: utf-8 -*-
"""Platform-calculated ratios for every US filer — the US screener's numbers.

Everything under /us/financials/ is as the SEC extracted it. This module is
the one place a US number is *computed*, and it follows the same rule as the
Japanese ratios (fin_metrics.py) and the macro side: no badge, a formula
instead, and every input named with the tag it came from.

The inputs are the standardised key indicators sec_api.FIELDS resolves on
each filing (revenue, operating and net income, assets, equity, cash, the
cash-flow totals, capex, dividends, buybacks), read for the filing's own
period AND for the comparative period the filer tagged in the same filing.
So averages (ROE, ROA, asset turnover) and growth rates use one filing's own
two columns, never two filings stitched together — if a filer did not tag
the opening balance, the ratio is withheld rather than computed on the
closing balance.

Conventions, stated on the Methodology page:

  * Return ratios use the AVERAGE of opening and closing balances, and are
    withheld when that average is not positive (a return on negative equity
    inverts its own sign and would rank an insolvent company first); such a
    row is flagged `negative_equity`, which is the finding.
  * Free cash flow = operating cash flow − purchases of property, plant and
    equipment, the one capex tag the data sets carry consistently; withheld
    for banks, insurers, brokers and other financial filers (SIC 6000–6499
    and 6700–6799), whose cash flow statements do not mean the same thing.
  * Payout and buyback ratios divide cash paid to shareholders by net
    income and are withheld for a loss.
  * No price ratios. This platform holds no US market data, so there is no
    market value, yield or multiple here; the filer's own figures are not
    used to imply one either.

Only USD filings are ranked on size fields; a 20-F filer reporting in CNY or
JPY keeps its unit on the row and its ratios (which are unit-free), but is
never ranked by revenue against a USD filer.
"""
import datetime
import threading

from . import asof
from . import sec_api

FORMULAS = {
    "roe_pct": ("net income ÷ average(stockholders' equity, opening and closing, from the same "
                "filing) × 100; withheld when that average is not positive"),
    "roa_pct": "net income ÷ average(total assets, opening and closing) × 100; withheld when not positive",
    "operating_margin_pct": "operating income ÷ revenue × 100",
    "net_margin_pct": "net income ÷ revenue × 100",
    "equity_ratio_pct": "stockholders' equity ÷ total assets × 100, closing balances",
    "asset_turnover_x": "revenue ÷ average(total assets, opening and closing)",
    "revenue_growth_pct": "(revenue ÷ prior-year revenue − 1) × 100, both from the same filing",
    "profit_growth_pct": "(net income ÷ prior-year net income − 1) × 100; withheld when the prior year was a loss",
    "cash_conversion_x": "operating cash flow ÷ net income; withheld for a loss",
    "fcf": ("operating cash flow − purchases of property, plant and equipment; withheld where no "
            "capex line is tagged, and for financial filers (SIC 6000–6499, 6700–6799)"),
    "fcf_margin_pct": "free cash flow ÷ revenue × 100",
    "cash_to_assets_pct": "cash and cash equivalents ÷ total assets × 100, closing balances; withheld for financial filers",
    "payout_ratio_pct": "dividends paid ÷ net income × 100; withheld for a loss",
    "buyback_ratio_pct": "common stock repurchased ÷ net income × 100; withheld for a loss",
    "shareholder_return": "dividends paid + common stock repurchased, in the filing's unit",
}
METRIC_LABELS = {
    "roe_pct": ("ROE", "%"), "roa_pct": ("ROA", "%"),
    "operating_margin_pct": ("Operating margin", "%"), "net_margin_pct": ("Net margin", "%"),
    "equity_ratio_pct": ("Equity ratio", "%"), "asset_turnover_x": ("Asset turnover", "×"),
    "revenue_growth_pct": ("Revenue growth", "%"), "profit_growth_pct": ("Profit growth", "%"),
    "cash_conversion_x": ("Cash conversion", "×"), "fcf": ("Free cash flow", "unit"),
    "fcf_margin_pct": ("FCF margin", "%"), "cash_to_assets_pct": ("Cash / assets", "%"),
    "payout_ratio_pct": ("Payout ratio", "%"), "buyback_ratio_pct": ("Buybacks / net income", "%"),
    "shareholder_return": ("Dividends + buybacks", "unit"),
}
METRIC_ORDER = ["roe_pct", "roa_pct", "operating_margin_pct", "net_margin_pct", "equity_ratio_pct",
                "asset_turnover_x", "revenue_growth_pct", "profit_growth_pct", "cash_conversion_x",
                "fcf", "fcf_margin_pct", "cash_to_assets_pct", "payout_ratio_pct",
                "buyback_ratio_pct", "shareholder_return"]
SIZE_FIELDS = ["revenue", "operating_income", "net_income", "total_assets", "equity", "cash",
               "cf_operating", "capex", "dividends_paid", "buybacks"]

FINANCIAL_SIC = tuple(range(6000, 6500)) + tuple(range(6700, 6800))

# Price-based figures, added only when a close is on file for the ticker.
# They are the second computed layer: the filing supplies shares and
# earnings, the price feed supplies the close, and each row names both.
PRICE_FORMULAS = {
    "market_cap": ("latest close × shares outstanding as stated on the cover of the company's newest 10-K or 10-Q; "
                   "with several share classes, the sum over classes of each class's shares × its own close, an unlisted "
                   "class counted at its conversion ratio into a listed one; where no cover count is on file, the "
                   "year-end balance-sheet count. Where the US listing is a depositary share, the count is first divided "
                   "by the number of shares one depositary share stands for, as the company's own filing states it; "
                   "in US dollars whatever currency the filing reports in"),
    "pe_x": ("latest close ÷ diluted EPS of the latest annual report (EPS × the shares one depositary share stands for, "
             "where the listing is one); withheld for a loss, for a filing not in US dollars, and below 1× (a tagging "
             "slip, not a bargain)"),
    "buyback_yield_pct": "common stock repurchased in the latest fiscal year ÷ market cap × 100",
    "dividend_yield_pct": "dividends paid in the latest fiscal year ÷ market cap × 100",
    "price_change_1y_pct": ("(latest close ÷ close one year earlier − 1) × 100, nearest trading day; the earlier close is "
                            "adjusted for any split in between (a day whose previous close ÷ close is a whole number)"),
    "price_change_1m_pct": ("(latest close ÷ close one month earlier − 1) × 100, nearest trading day; the earlier close is "
                            "adjusted for any split in between"),
}
PRICE_LABELS = {
    "market_cap": ("Market value", "USD"), "pe_x": ("Price / earnings", "×"),
    "buyback_yield_pct": ("Buyback yield", "%"), "dividend_yield_pct": ("Dividend yield", "%"),
    "price_change_1y_pct": ("Price change, 1 year", "%"), "price_change_1m_pct": ("Price change, 1 month", "%"),
}
PRICE_ORDER = ["market_cap", "pe_x", "buyback_yield_pct", "dividend_yield_pct", "price_change_1y_pct",
               "price_change_1m_pct"]


def with_prices(row, close, close_1y, price_date, basis=None, close_1m=None):
    """Attach the price-based figures to one metrics row, in place. `close`
    is the latest close for the row's ticker, `close_1y` the close nearest
    one year earlier and `close_1m` the one nearest a month earlier (None
    when the feed does not reach back that far).
    `basis` is app/sec_shares.basis(): the cover-page share count and, for a
    company with several share classes, each class priced on its own; without
    one, the year-end balance-sheet count is used."""
    ps = row.get("per_share") or {}
    shares, eps = ps.get("shares_outstanding"), ps.get("eps_diluted")
    bb, divs = row["size"].get("buybacks"), row["size"].get("dividends_paid")
    usd = row.get("unit") == "USD"
    per = 1.0
    if basis is not None:
        mcap = basis.get("market_cap")
        per = basis.get("per_listed_share") or 1.0
    else:
        mcap = (close * shares) if (close is not None and shares and shares > 0) else None
        if mcap is not None:
            basis = {"method": "balance sheet", "shares": shares, "listed_shares": shares, "per_listed_share": 1.0,
                     "listing": None, "shares_date": row.get("period_end"), "market_cap": mcap, "classes": [],
                     "note": "no cover-page count on file; the year-end balance-sheet count is used"}
    row["market_value_basis"] = basis
    row["price"] = {"close": close, "date": price_date, "close_1y": close_1y, "close_1m": close_1m}
    # earnings per listed share: a depositary share earns what the shares it stands for earn
    eps_listed = (eps * per) if eps is not None else None
    row["metrics"].update({
        # the close is in dollars whatever currency the filing reports in, so market value always is
        "market_cap": _r(mcap, 0),
        # a P/E under 1 is a tagging slip (EPS in cents, a stale share count), not a bargain: withheld
        "pe_x": (_r(_div(close, eps_listed)) if (usd and eps_listed is not None and eps_listed > 0 and close is not None
                                                 and close / eps_listed >= 1.0) else None),
        "buyback_yield_pct": _r(_pct(_div(bb, mcap))) if (usd and mcap) else None,
        "dividend_yield_pct": _r(_pct(_div(divs, mcap))) if (usd and mcap) else None,
        "price_change_1y_pct": _r(_pct(_div(close, close_1y) - 1.0)) if (close is not None and close_1y) else None,
        "price_change_1m_pct": _r(_pct(_div(close, close_1m) - 1.0)) if (close is not None and close_1m) else None,
    })
    return row

_LOCK = threading.Lock()
_CACHE = {}        # (db version, as-of ceiling) -> rows
_CACHE_MAX = 6

# Every indicator fact a filer tagged on its own period end or on the prior
# year's, for its latest accepted annual report. The prior period end is
# whatever instant/duration end the filer tagged between 330 and 400 days
# before its own period end — 52/53-week fiscal years make it drift.
CROSS_SQL = """
    WITH latest AS (
        SELECT * FROM (
            SELECT s.adsh, s.cik, s.name, s.sic, s.country_ba, s.form, s.period, s.fy, s.fp,
                   s.filed, s.status,
                   row_number() OVER (PARTITION BY s.cik ORDER BY s.period DESC, s.filed DESC) AS rn
            FROM sec_filings s
            WHERE s.form IN (%s) AND s.status IN ('clean','partial')/*ASOF*/
        ) WHERE rn = 1
    )
    SELECT l.adsh, l.cik, l.name, l.sic, l.country_ba, l.form, l.period, l.fy, l.filed, l.status,
           f.tag, f.ddate, f.qtrs, f.uom, f.value
    FROM latest l
    JOIN sec_facts f ON f.adsh = l.adsh AND f.segment_id IS NULL AND f.coreg IS NULL
                    AND f.tag IN (%s) AND f.qtrs IN (0, 4)
                    AND f.ddate <= l.period AND f.ddate >= l.period - INTERVAL 400 DAY
    ORDER BY l.cik, f.tag, f.ddate
""" % (",".join("?" * len(sec_api.ANNUAL_FORMS)), ",".join("?" * len(sec_api.FIELD_TAGS)))


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _avg(a, b):
    if a is None or b is None:
        return None
    return (a + b) / 2.0


def _pct(x):
    return None if x is None else x * 100.0


def _r(x, dp=4):
    return None if x is None else round(x, dp)


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _resolve(facts, period):
    """Per field: (closing value, tag, unit, opening value) from one filing's
    facts, taking the first candidate tag the filer carries on its own
    period end; the opening value is the same tag on the prior period end."""
    by_tag = {}
    for tag, ddate, qtrs, uom, value in facts:
        by_tag.setdefault((tag, qtrs), {}).setdefault(ddate, {})[uom] = value
    out = {}
    lo, hi = period - datetime.timedelta(days=400), period - datetime.timedelta(days=330)
    for field, kind, candidates in sec_api.FIELDS:
        q = 0 if kind == "instant" else 4
        cur = prior = unit = tag_used = None
        for tag in candidates:
            dates = by_tag.get((tag, q))
            if not dates or period not in dates:
                continue
            # a 20-F filer may tag a line in its own currency and in USD (its
            # convenience translation); USD is kept, and the comparative must
            # be in the same unit or it is no comparative at all
            units = dates[period]
            unit = "USD" if "USD" in units else sorted(units)[0]
            cur = units[unit]
            tag_used = tag
            earlier = [d for d in dates if lo <= d <= hi and unit in dates[d]]
            if earlier:
                prior = dates[max(earlier)][unit]
            break
        out[field] = (cur, tag_used, unit, prior)
    return out


BANK_SIC = tuple(range(6020, 6037)) + tuple(range(6099, 6200))


def composite_revenue(values, tags, sic, priors=None):
    """Revenue for the filers whose "revenue" tag is not their revenue, in
    place. A bank tags its fee income as revenue from contracts with
    customers and its interest income separately, so the first-match rule
    took Cullen/Frost's $122mn of fees for its $2.9bn of revenue (found
    2026-10-19); a REIT tags its rental income as lease income beside a small
    contract line. Banks (SIC 6020-6036, 6099-6199): total interest and
    dividend income plus non-interest income. Any filer whose lease income
    exceeds the revenue line found: lease income plus that line. The tag
    recorded names both parts; a filer's own "Revenues" line is never
    replaced. Returns True when the revenue was composed."""
    tag = tags.get("revenue")
    if tag == "Revenues":
        return False
    bank = sic is not None and int(sic) in BANK_SIC
    ii, ni, li = values.get("interest_income"), values.get("noninterest_income"), values.get("lease_income")
    rev = values.get("revenue")
    if bank and ii is not None and (tag in (None, "InterestAndDividendIncomeOperating") or (tag or "").startswith("RevenueFromContract")):
        values["revenue"] = ii + (ni or 0)
        tags["revenue"] = "InterestAndDividendIncomeOperating" + (" + NoninterestIncome" if ni is not None else "")
        if priors is not None:
            pi, pn = priors.get("interest_income"), priors.get("noninterest_income")
            priors["revenue"] = (pi + (pn or 0)) if (pi is not None and (ni is None) == (pn is None)) else None
        return True
    if li is not None and (tag is None or (tag or "").startswith("RevenueFromContract")) and li > (rev or 0):
        values["revenue"] = li + (rev or 0)
        tags["revenue"] = "OperatingLeaseLeaseIncome" + ((" + " + tag) if (tag and rev is not None) else "")
        if priors is not None:
            pl, pr = priors.get("lease_income"), priors.get("revenue")
            priors["revenue"] = (pl + (pr or 0)) if (pl is not None and (rev is None) == (pr is None)) else None
        return True
    return False


def compute_row(head, facts):
    """One company's metrics from its latest annual filing. `head` is the
    filing row; `facts` the (tag, ddate, qtrs, uom, value) tuples."""
    res = _resolve(facts, head["period"])
    v = {k: res[k][0] for k in res}
    p = {k: res[k][3] for k in res}
    tags_used = {k: res[k][1] for k in res}
    composite_revenue(v, tags_used, head.get("sic"), p)
    # The filing's reporting currency is the unit on its core monetary lines;
    # a 20-F filer may tag a USD convenience figure beside its own currency,
    # so "any USD fact" would mislabel a VND or INR filer as a dollar one.
    unit = next((res[k][2] for k in ("revenue", "total_assets", "net_income", "equity")
                 if res[k][2]), None)
    sic = head.get("sic")
    financial = sic is not None and int(sic) in FINANCIAL_SIC
    flags, checks = [], {}

    rev, oi, ni = v["revenue"], v["operating_income"], v["net_income"]
    assets, equity, cash = v["total_assets"], v["equity"], v["cash"]
    cfo, capex, divs, bb = v["cf_operating"], v["capex"], v["dividends_paid"], v["buybacks"]
    avg_eq, avg_as = _avg(equity, p["equity"]), _avg(assets, p["total_assets"])

    m = {}
    if equity is not None and p["equity"] is None:
        flags.append("no_opening_equity")
    if avg_eq is not None and avg_eq <= 0:
        flags.append("negative_equity")
    m["roe_pct"] = _pct(_div(ni, avg_eq)) if (avg_eq is not None and avg_eq > 0) else None
    m["roa_pct"] = _pct(_div(ni, avg_as)) if (avg_as is not None and avg_as > 0) else None
    m["operating_margin_pct"] = _pct(_div(oi, rev)) if (rev is not None and rev > 0) else None
    m["net_margin_pct"] = _pct(_div(ni, rev)) if (rev is not None and rev > 0) else None
    m["equity_ratio_pct"] = _pct(_div(equity, assets)) if (assets is not None and assets > 0) else None
    m["asset_turnover_x"] = _div(rev, avg_as) if (avg_as is not None and avg_as > 0) else None
    m["revenue_growth_pct"] = (_pct(_div(rev, p["revenue"]) - 1.0)
                               if (rev is not None and p["revenue"] and p["revenue"] > 0) else None)
    m["profit_growth_pct"] = (_pct(_div(ni, p["net_income"]) - 1.0)
                              if (ni is not None and p["net_income"] and p["net_income"] > 0) else None)
    m["cash_conversion_x"] = _div(cfo, ni) if (ni is not None and ni > 0) else None
    if financial:
        flags.append("financial_company")
        m["fcf"] = m["fcf_margin_pct"] = m["cash_to_assets_pct"] = None
    else:
        m["fcf"] = (cfo - capex) if (cfo is not None and capex is not None) else None
        m["fcf_margin_pct"] = _pct(_div(m["fcf"], rev)) if (rev is not None and rev > 0) else None
        m["cash_to_assets_pct"] = _pct(_div(cash, assets)) if (assets is not None and assets > 0) else None
    m["payout_ratio_pct"] = _pct(_div(divs, ni)) if (ni is not None and ni > 0) else None
    m["buyback_ratio_pct"] = _pct(_div(bb, ni)) if (ni is not None and ni > 0) else None
    m["shareholder_return"] = ((divs or 0) + (bb or 0)) if (divs is not None or bb is not None) else None
    metrics = {k: _r(m[k]) for k in METRIC_ORDER}
    checks["opening_balances_from"] = "same filing, prior period end" if any(
        p[k] is not None for k in ("equity", "total_assets")) else None

    return {
        "cik": head["cik"], "sec_code": "cik:%d" % head["cik"], "name": head["name"],
        "sic": sic, "country": head.get("country_ba"), "form": head["form"],
        "period_end": _d(head["period"]), "fy": head["fy"], "filed": _d(head["filed"]),
        "status": head["status"], "adsh": head["adsh"], "unit": unit,
        "size": {k: v[k] for k in SIZE_FIELDS},
        "per_share": {"shares_outstanding": v["shares_outstanding"], "eps_diluted": v["eps_diluted"],
                      "eps_basic": v["eps_basic"]},
        "tags": {k: tags_used[k] for k in SIZE_FIELDS},
        "prior": {k: p[k] for k in ("revenue", "net_income", "total_assets", "equity")},
        "metrics": metrics, "checks": checks, "flags": flags,
        "source_url": "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (
            head["cik"], head["adsh"].replace("-", "")),
    }


def _compute_all(cur):
    sql = CROSS_SQL.replace("/*ASOF*/", asof.clause("filed", "s"))
    cur.execute(sql, list(sec_api.ANNUAL_FORMS) + list(sec_api.FIELD_TAGS))
    cols = [d[0] for d in cur.description]
    rows, head, facts = [], None, []
    for rec in cur.fetchall():
        r = dict(zip(cols, rec))
        if head is None or r["adsh"] != head["adsh"]:
            if head is not None:
                rows.append(compute_row(head, facts))
            head = {k: r[k] for k in ("adsh", "cik", "name", "sic", "country_ba", "form",
                                      "period", "fy", "filed", "status")}
            facts = []
        facts.append((r["tag"], r["ddate"], r["qtrs"], r["uom"], r["value"]))
    if head is not None:
        rows.append(compute_row(head, facts))
    return rows


def all_rows(cur):
    """Metrics for every filer's latest annual report, memoised per database
    version and as-of ceiling (a few ceilings at most)."""
    key = (sec_api.file_version(), asof.current())
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    from . import sec_shares
    rows = sec_shares.merge_successors(_compute_all(cur))
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = rows
    return rows


def metric_defs(prices=False):
    out = [{"metric": k, "label": METRIC_LABELS[k][0], "unit": METRIC_LABELS[k][1],
            "formula": FORMULAS[k]} for k in METRIC_ORDER]
    if prices:
        out += [{"metric": k, "label": PRICE_LABELS[k][0], "unit": PRICE_LABELS[k][1],
                 "formula": PRICE_FORMULAS[k]} for k in PRICE_ORDER]
    return out
