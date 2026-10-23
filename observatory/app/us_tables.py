# -*- coding: utf-8 -*-
"""The step-by-step route's other tables: CEO pay, quarterly results,
business segments, insider trades, 13F fund holdings and daily prices.

app/us_steps.py answers open questions with one SQL query over a table of
every stock (`stocks`). These tables sit beside it, each keyed by the
same ticker, so a question can join them: "the ten best-paid CEOs", "S&P 500
companies whose revenue grew fastest last quarter", "companies whose cloud
segment is over half of revenue", "where insiders bought most in the past
three months", "stocks funds added most last quarter", "how many months Microsoft beat the
S&P 500".

Every number comes from the same data the API serves, and nothing here is
written by a model:

  pay              us_pay_api.index() — each S&P 500 member's latest proxy
                   (pay versus performance table, SEC Item 402(v))
  quarters         10-Q and 10-K facts for single quarters, as last filed;
                   a fourth quarter no filing reports alone is the full year
                   less the three quarters, and is marked derived
  segments         the latest annual report's revenue (and operating income)
                   by business segment, geography and product line, each
                   segment on one dimension only
  insider_trades   Forms 4: open-market purchases and sales over the past
                   year, one row per transaction
  fund_holdings    13F: per stock, the latest quarter's holders, value and
                   shares against the quarter before
  prices           every trading day's close for each stock and benchmark
                   fund, split-adjusted like /api/v1/us/prices (monthly
                   returns, worst days, drawdowns, "how many months ...")

The tables are built once per database version into a shared in-memory
DuckDB (sandbox()); each question gets its own cursor on it, so the query
reaches these tables and its own temporary `stocks` and `benchmarks`, and
nothing else: external access is off and the configuration is locked before
any question runs.
"""
import datetime
import json
import re
import threading

from . import sec_api

_LOCK = threading.Lock()
_STATE = {}
MEMORY_LIMIT = "512MB"

# ---------------------------------------------------------------- columns
# (column, SQL type, what it means) — the descriptions are what the model reads
TABLES = {
    "pay": ("CEO pay at S&P 500 members, from each company's latest proxy statement (one row per company).", [
        ("ticker", "VARCHAR", "joins stocks.ticker"),
        ("ceo", "VARCHAR", "the CEO's name as the proxy gives it; two names joined by ' / ' when the CEO changed in the year"),
        ("fiscal_year_end", "DATE", "the fiscal year the pay is for; companies' years differ"),
        ("ceo_total_pay_usd", "DOUBLE", "the CEO's total pay as reported in the Summary Compensation Table (stock and options at grant value), US$"),
        ("ceo_actually_paid_usd", "DOUBLE", "the SEC's 'compensation actually paid': equity marked to market over the year, can be negative, US$"),
        ("other_execs_avg_pay_usd", "DOUBLE", "average total pay of the other named executives, US$"),
        ("tsr_value_of_100", "DOUBLE", "total shareholder return: what $100 invested at tsr_since is worth at fiscal_year_end, dividends reinvested"),
        ("peer_tsr_value_of_100", "DOUBLE", "the same for the company's own peer group"),
        ("tsr_since", "DATE", "the start of the shareholder-return measurement"),
        ("proxy_filed", "DATE", "when the proxy statement was filed"),
    ]),
    "quarters": ("Quarterly results, the last eight quarters per company (one row per company per quarter).", [
        ("ticker", "VARCHAR", "joins stocks.ticker"),
        ("quarter_end", "DATE", "the last day of the fiscal quarter"),
        ("fiscal_year", "INTEGER", "the company's fiscal year"),
        ("fiscal_quarter", "VARCHAR", "'Q1', 'Q2', 'Q3' or 'Q4' of that fiscal year"),
        ("revenue_usd", "DOUBLE", "revenue in the quarter, US$"),
        ("operating_income_usd", "DOUBLE", "operating income in the quarter, US$"),
        ("net_income_usd", "DOUBLE", "net income in the quarter, US$ (negative = a loss)"),
        ("eps_usd", "DOUBLE", "diluted earnings per share in the quarter, US$"),
        ("revenue_year_ago_usd", "DOUBLE", "revenue in the same quarter a year earlier, US$"),
        ("revenue_growth_yoy_pct", "DOUBLE", "revenue against the same quarter a year earlier, % (require revenue_year_ago_usd >= 5e7 when ranking)"),
        ("net_income_year_ago_usd", "DOUBLE", "net income in the same quarter a year earlier, US$"),
        ("is_latest", "BOOLEAN", "the company's most recent quarter on file"),
        ("q4_derived", "BOOLEAN", "a fourth quarter worked out as the full year less the first three (no filing reports it alone)"),
        ("filed", "DATE", "when the figures were filed"),
    ]),
    "segments": ("Revenue by segment from each company's latest annual report (one row per company per segment).", [
        ("ticker", "VARCHAR", "joins stocks.ticker"),
        ("fiscal_year_end", "DATE", "the fiscal year the figures cover"),
        ("kind", "VARCHAR", "'business' (the company's reporting segments), 'geography' (countries or regions) or 'product' (product and service lines)"),
        ("segment", "VARCHAR", "the segment's name from the company's own tag (e.g. 'Intelligent Cloud', 'iPhone', 'Greater China', 'China (CN)'); match with ILIKE"),
        ("revenue_usd", "DOUBLE", "the segment's revenue in the year, US$"),
        ("revenue_prior_usd", "DOUBLE", "the year before, as given in the same report, US$"),
        ("revenue_growth_pct", "DOUBLE", "revenue growth on the year before, %"),
        ("share_of_revenue_pct", "DOUBLE", "the segment's revenue as a share of the company's total revenue, %; segments of one kind may overlap or leave a remainder"),
        ("operating_income_usd", "DOUBLE", "the segment's operating income, US$ (business segments only, where reported)"),
    ]),
    "insider_trades": ("Open-market purchases and sales by company insiders over the past year, from Forms 4 (one row per trade).", [
        ("ticker", "VARCHAR", "joins stocks.ticker"),
        ("trade_date", "DATE", "the day of the trade"),
        ("filed", "DATE", "when the Form 4 was filed"),
        ("insider", "VARCHAR", "the insider's name as filed"),
        ("title", "VARCHAR", "officer title as filed (CEO, CFO, ...); blank for a director or holder without one"),
        ("is_director", "BOOLEAN", "a director"),
        ("is_officer", "BOOLEAN", "an officer"),
        ("is_ten_percent_owner", "BOOLEAN", "a holder of 10% or more"),
        ("side", "VARCHAR", "'buy' (open-market purchase, code P) or 'sell' (open-market sale, code S)"),
        ("shares", "DOUBLE", "shares traded"),
        ("price_usd", "DOUBLE", "price per share, US$"),
        ("value_usd", "DOUBLE", "shares x price, US$"),
        ("planned", "BOOLEAN", "made under a pre-arranged 10b5-1 trading plan (as flagged on the filing)"),
    ]),
    "events": ("Days things happened, from 2018: the Fed's FOMC statement days, CPI release mornings, federal shutdowns, "
               "and the day each company announced its results (its 8-K under Item 2.02). One row per event; a results "
               "row names its ticker.", [
        ("kind", "VARCHAR", "'fomc' (the FOMC's statement day), 'cpi' (the morning the CPI came out), 'shutdown' (a federal shutdown), 'results' (a company's results filing)"),
        ("date", "DATE", "the event day; for a shutdown, its first day"),
        ("end_date", "DATE", "a shutdown's last day; blank otherwise"),
        ("ticker", "VARCHAR", "the company, results rows only; joins stocks.ticker"),
        ("label", "VARCHAR", "what happened, in words"),
        ("scheduled", "BOOLEAN", "a date on a published calendar that has not happened yet (no prices for it)"),
        ("is_earnings", "BOOLEAN", "results rows: the quarter's results announcement (true), or another Item 2.02 filing such as delivery numbers (false)"),
    ]),
    "fund_holdings": ("13F holdings by investment managers: per stock, the latest quarter against the one before (one row per stock).", [
        ("ticker", "VARCHAR", "joins stocks.ticker"),
        ("quarter_end", "DATE", "the quarter-end the latest holdings are reported for"),
        ("holders", "INTEGER", "managers reporting a position"),
        ("holders_prior", "INTEGER", "managers reporting a position the quarter before"),
        ("new_holders", "INTEGER", "managers with a position now and none the quarter before"),
        ("exited_holders", "INTEGER", "managers with a position the quarter before and none now"),
        ("reported_value_usd", "DOUBLE", "value of all reported positions at the quarter-end, US$"),
        ("shares_held", "DOUBLE", "shares in all reported positions"),
        ("shares_held_prior", "DOUBLE", "the same the quarter before, adjusted for any stock split in between"),
        ("shares_change_pct", "DOUBLE", "change in shares held across all managers, % (positive = funds added); blank when the security's identifier changed between the quarters"),
    ]),
    "prices": ("Daily closing prices, one row per stock per trading day, for every ticker in stocks and every "
               "benchmarks.fund_ticker (SPY for the S&P 500, DIA for the Dow ...).", [
        ("ticker", "VARCHAR", "joins stocks.ticker, or benchmarks.fund_ticker"),
        ("date", "DATE", "the trading day"),
        ("close_usd", "DOUBLE", "the close as traded that day, US$"),
        ("adj_close_usd", "DOUBLE", "the close adjusted for later stock splits, US$: use this for any change between two days"),
        ("volume", "BIGINT", "shares traded that day, as traded (not split-adjusted)"),
    ]),
}
# tables built by their own route (not from row dicts): see price_table()
_DIRECT = {"prices"}


def sources():
    """{table: source} for the answer's source line, from each dataset's own provenance."""
    from . import sec_13f_api, sec_insiders_api, us_pay_api
    return {"pay": {"name": "CEO pay (proxy statements)", "credit": us_pay_api.PROVENANCE["credit"]},
            "quarters": {"name": "Quarterly results (10-Q and 10-K)", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (10-Q and 10-K)."},
            "segments": {"name": "Segment revenue (10-K)", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (10-K segment disclosures)."},
            "insider_trades": {"name": "Insider trades", "credit": sec_insiders_api.PROVENANCE["credit"]},
            "fund_holdings": {"name": "13F holdings", "credit": sec_13f_api.PROVENANCE["credit"]}}
    # prices: every step answer already names the daily-price source (stock_recipes.PRICE_SOURCE)


def doc():
    """The tables, described for the model, after stocks and benchmarks."""
    lines = []
    for name, (what, cols) in TABLES.items():
        lines.append("%s: %s" % (name, what))
        lines += ["  %s %s: %s" % (c, t, d) for c, t, d in cols]
    return "\n".join(lines)


# ---------------------------------------------------------------- helpers
def _has(cur, table):
    return sec_api._has(cur, table)


def _tickers(cur):
    """{cik: ticker} from the SEC's own list, the same tickers `stocks` uses."""
    if not _has(cur, "sec_companies"):
        return {}
    return {cik: t for cik, t in cur.execute("SELECT cik, ticker FROM sec_companies WHERE ticker IS NOT NULL").fetchall()}


def _num(v):
    return None if v is None else float(v)


_SMALL = {"And", "Of", "The", "For", "In", "On", "To", "With", "Or", "By", "At"}
_DROP = re.compile(r"(?i)elimination|intersegment|reconcil|consolidat|^segments?$|reportablesegments?$|operatingsegments?$|"
                   r"segmentcontinuingoperations|totalsegments?$")


_UPPER = {"Us": "US", "Uk": "UK", "Eu": "EU", "Emea": "EMEA", "Apac": "APAC", "Usa": "USA"}


# a geography tagged by ISO country code ("country:CN") reads as its name, the code kept for matching
COUNTRIES = {"US": "United States", "CN": "China", "JP": "Japan", "GB": "United Kingdom", "DE": "Germany",
             "FR": "France", "IE": "Ireland", "NL": "Netherlands", "CH": "Switzerland", "TW": "Taiwan",
             "KR": "South Korea", "IN": "India", "SG": "Singapore", "HK": "Hong Kong", "CA": "Canada",
             "MX": "Mexico", "BR": "Brazil", "AU": "Australia", "IL": "Israel", "IT": "Italy", "ES": "Spain",
             "SE": "Sweden", "BE": "Belgium", "LU": "Luxembourg", "MY": "Malaysia", "VN": "Vietnam",
             "TH": "Thailand", "PH": "Philippines", "ID": "Indonesia", "SA": "Saudi Arabia", "AE": "United Arab Emirates",
             "ZA": "South Africa", "PR": "Puerto Rico", "BM": "Bermuda", "KY": "Cayman Islands", "DK": "Denmark",
             "NO": "Norway", "FI": "Finland", "AT": "Austria", "PL": "Poland", "CZ": "Czech Republic", "CL": "Chile",
             "CO": "Colombia", "PE": "Peru", "AR": "Argentina", "NZ": "New Zealand", "TR": "Turkey"}


def segment_name(member, kind=None):
    """'msft:ProductivityAndBusinessProcessesMember' -> 'Productivity and Business Processes';
    'IPhone' -> 'iPhone'; 'NonUs' -> 'Non-US'."""
    prefix, _, m = member.rpartition(":")
    if (prefix == "country" or kind == "geography") and m in COUNTRIES:
        return "%s (%s)" % (COUNTRIES[m], m)
    m = re.sub(r"(Member|Segment)$", "", m) or m
    m = re.sub(r"\bI(Phone|Pad|Pod|Cloud|Mac)", r"i\1", m)               # Apple's own spelling
    m = re.sub(r"(?<=[a-z])(?<![lrmphbstwg])and(?=[A-Z])", "And", m)    # "WearablesHomeandAccessories", not "Land", "Brand"
    words = re.findall(r"i(?:Phone|Pad|Pod|Cloud|Mac)|[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", m)
    out = []
    for i, w in enumerate(words):
        w = _UPPER.get(w, w)
        out.append(w.lower() if i and w in _SMALL else w)
    text = " ".join(out) or m
    text = re.sub(r"\bMc (?=[A-Z])", "Mc", text)                         # "McLane", not "Mc Lane"
    text = text.replace("Three Six Five", "365").replace("Linked in", "LinkedIn")
    text = re.sub(r"^Product and Service (?=[A-Z])", "", text)            # Realty Income's "ProductAndServiceRetail"
    return re.sub(r"\bNon (?=[A-Z])", "Non-", text)


# ---------------------------------------------------------------- pay
def pay_rows(cur):
    from . import us_pay_api
    from .tools import call_api
    if not _has(cur, "sec_px_facts"):
        return []
    try:
        d = call_api(us_pay_api.index, basis="latest", as_of="")
    except Exception:                                             # noqa: BLE001 — no pay data: no pay table
        return []
    out = []
    for r in d.get("companies") or []:
        v = r.get("values") or {}
        # a proxy whose pay table leaves the CEO columns blank (Tesla, 2024) keeps its row: the name is known
        if not r.get("ticker") or (v.get("ceo_total_pay") is None and not r.get("ceo_names")):
            continue
        out.append({"ticker": r["ticker"], "ceo": " / ".join(r.get("ceo_names") or []) or None,
                    "fiscal_year_end": r.get("fiscal_year_end"),
                    "ceo_total_pay_usd": _num(v.get("ceo_total_pay")), "ceo_actually_paid_usd": _num(v.get("ceo_actually_paid")),
                    "other_execs_avg_pay_usd": _num(v.get("neo_avg_total_pay")),
                    "tsr_value_of_100": _num(v.get("tsr")), "peer_tsr_value_of_100": _num(v.get("peer_tsr")),
                    "tsr_since": r.get("tsr_base"), "proxy_filed": (r.get("source") or {}).get("filed")})
    return out


# ---------------------------------------------------------------- quarters
_Q_FIELDS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
}
QUARTERS_KEPT = 8


def quarter_rows(cur, today=None):
    """The last eight quarters per company, as last filed; a missing fourth
    quarter is the year less its three quarters."""
    if not _has(cur, "sec_facts"):
        return []
    today = today or datetime.date.today()
    tags = sorted({t for ts in _Q_FIELDS.values() for t in ts})
    rows = cur.execute("""
        SELECT s.cik, f.tag, f.ddate, f.qtrs, f.value, s.filed, s.fy, s.fp, s.period
        FROM sec_facts f JOIN sec_filings s USING (adsh)
        WHERE s.form IN ('10-Q', '10-K', '10-Q/A', '10-K/A') AND s.status IN ('clean', 'partial')
          AND f.segment_id IS NULL AND f.coreg IS NULL AND f.qtrs IN (1, 4) AND f.uom IN ('USD', 'USD/shares')
          AND f.tag IN (%s) AND f.ddate >= ? AND f.ddate <= ?""" % ",".join("?" * len(tags)),
        tags + [today - datetime.timedelta(days=365 * 3 + 120), today]).fetchall()
    rank = {t: i for ts in _Q_FIELDS.values() for i, t in enumerate(ts)}
    field_of = {t: f for f, ts in _Q_FIELDS.items() for t in ts}
    # (cik, ddate, qtrs) -> field -> (tag rank, filed, value): the best tag, as last filed
    facts, label = {}, {}
    for cik, tag, ddate, qtrs, value, filed, fy, fp, period in rows:
        f = field_of[tag]
        key = (cik, ddate, qtrs)
        best = facts.setdefault(key, {}).get(f)
        cand = (rank[tag], -(filed.toordinal()), value, filed)
        if best is None or cand[:2] < best[:2]:
            facts[key][f] = cand
        if ddate == period and fy and fp:
            label[(cik, ddate)] = (int(fy), "Q4" if fp == "FY" else fp, filed)
    by_co = {}
    for (cik, ddate, qtrs), fields in facts.items():
        by_co.setdefault(cik, {}).setdefault(qtrs, {})[ddate] = {k: (v[2], v[3]) for k, v in fields.items()}
    tickers = _tickers(cur)
    out = []
    for cik, parts in by_co.items():
        t = tickers.get(cik)
        if not t:
            continue
        qs = {d: dict(v, derived=False) for d, v in (parts.get(1) or {}).items()}
        # a fourth quarter: the year (qtrs=4) less the three quarters inside it
        for yend, yv in (parts.get(4) or {}).items():
            if yend in qs:
                continue
            # the three reported quarters of this fiscal year: after the year before ended (about 365 days
            # back, a few days either way for 52/53-week years), never a fourth quarter worked out already
            inside = sorted(d for d in qs if not qs[d]["derived"]
                            and yend - datetime.timedelta(days=340) < d < yend - datetime.timedelta(days=20))
            if len(inside) != 3:
                continue
            q4 = {"derived": True}
            for f in ("revenue", "operating_income", "net_income"):
                if f in yv and all(f in qs[d] for d in inside):
                    q4[f] = (yv[f][0] - sum(qs[d][f][0] for d in inside), yv[f][1])
            if len(q4) > 1:
                qs[yend] = q4
        ends = sorted(qs)
        for i, d in enumerate(ends):
            if d < ends[-1] - datetime.timedelta(days=QUARTERS_KEPT * 92):
                continue
            q = qs[d]
            ago = next((e for e in reversed(ends[:i]) if 345 <= (d - e).days <= 385), None)
            qa = qs.get(ago) if ago else None
            rev = q.get("revenue", (None,))[0]
            rev_ago = qa.get("revenue", (None,))[0] if qa else None
            fy, fq, filed = label.get((cik, d), (None, "Q4" if q["derived"] else None, None))
            filed = filed or max((v[1] for k, v in q.items() if k != "derived"), default=None)
            out.append({"ticker": t, "quarter_end": d, "fiscal_year": fy, "fiscal_quarter": fq,
                        "revenue_usd": rev, "operating_income_usd": q.get("operating_income", (None,))[0],
                        "net_income_usd": q.get("net_income", (None,))[0],
                        "eps_usd": None if q["derived"] else q.get("eps", (None,))[0],
                        "revenue_year_ago_usd": rev_ago,
                        "revenue_growth_yoy_pct": round((rev / rev_ago - 1) * 100, 4) if (rev is not None and rev_ago and rev_ago > 0) else None,
                        "net_income_year_ago_usd": qa.get("net_income", (None,))[0] if qa else None,
                        "is_latest": d == ends[-1], "q4_derived": q["derived"], "filed": filed})
    return out


# ---------------------------------------------------------------- segments
_SEG_AXES = {"BusinessSegments": "business", "Geographical": "geography", "ProductOrService": "product"}
_SEG_REVENUE = _Q_FIELDS["revenue"]


def segment_rows(cur):
    """Revenue by single-dimension segment in each company's latest annual report."""
    if not (_has(cur, "sec_facts") and _has(cur, "sec_segments")):
        return []
    from . import sec_metrics
    cutoff = datetime.date.today() - datetime.timedelta(days=550)
    latest = {}
    for r in sec_metrics.all_rows(cur):
        if r.get("unit") == "USD" and r.get("period_end") and r["period_end"] >= cutoff.isoformat():
            latest[r["adsh"]] = (r["cik"], datetime.date.fromisoformat(r["period_end"]), (r.get("size") or {}).get("revenue"))
    if not latest:
        return []
    cur.execute("CREATE OR REPLACE TEMP TABLE _seg_adsh (adsh VARCHAR, period DATE)")
    cur.executemany("INSERT INTO _seg_adsh VALUES (?, ?)", [(a, v[1]) for a, v in latest.items()])
    tags = _SEG_REVENUE + ["OperatingIncomeLoss"]
    rows = cur.execute("""
        SELECT f.adsh, g.segments, f.tag, f.ddate, f.value
        FROM sec_facts f JOIN _seg_adsh a USING (adsh) JOIN sec_segments g USING (segment_id)
        WHERE f.qtrs = 4 AND f.coreg IS NULL AND f.uom = 'USD' AND f.tag IN (%s)
          AND f.ddate <= a.period AND f.ddate >= a.period - INTERVAL 400 DAY
          AND (length(g.segments) - length(replace(g.segments, '=', '')) = 1
               -- reporting segments tagged since ASU 2023-07 carry the operating-segments marker beside them
               OR (length(g.segments) - length(replace(g.segments, '=', '')) = 2
                   AND g.segments LIKE '%%ConsolidationItems=OperatingSegments;%%'))""" % ",".join("?" * len(tags)), tags).fetchall()
    cur.execute("DROP TABLE IF EXISTS _seg_adsh")
    tickers = _tickers(cur)
    rank = {t: i for i, t in enumerate(_SEG_REVENUE)}
    seg = {}
    for adsh, segs, tag, ddate, value in rows:
        segs = segs.replace("ConsolidationItems=OperatingSegments;", "")
        if segs.count("=") != 1:
            continue
        axis, _, member = segs.rstrip(";").partition("=")
        kind = _SEG_AXES.get(axis)
        if not kind or _DROP.search(member):
            continue
        cik, period, total = latest[adsh]
        cur_year = ddate == period
        prior = (period - ddate).days >= 330
        if not (cur_year or prior):
            continue
        k = (adsh, kind, member)
        rec = seg.setdefault(k, {})
        if tag == "OperatingIncomeLoss":
            if cur_year and kind == "business":
                rec["oi"] = value
            continue
        slot = "rev" if cur_year else "rev_prior"
        have = rec.get(slot + "_rank")
        if have is None or rank[tag] < have:
            rec[slot], rec[slot + "_rank"] = value, rank[tag]
    out = []
    for (adsh, kind, member), rec in seg.items():
        cik, period, total = latest[adsh]
        t = tickers.get(cik)
        rev = rec.get("rev")
        if not t or (rev is None and rec.get("oi") is None):
            continue                      # Alphabet tags operating income by segment but no revenue line: kept, revenue blank
        prior = rec.get("rev_prior")
        out.append({"ticker": t, "fiscal_year_end": period, "kind": kind, "segment": segment_name(member, kind),
                    "revenue_usd": rev, "revenue_prior_usd": prior,
                    "revenue_growth_pct": round((rev / prior - 1) * 100, 4) if (rev is not None and prior and prior > 0) else None,
                    "share_of_revenue_pct": round(rev / total * 100, 4) if (rev is not None and total and total > 0) else None,
                    "operating_income_usd": rec.get("oi")})
    return out


# ---------------------------------------------------------------- insiders
def insider_rows(cur, today=None):
    """Open-market purchases and sales over the past year, one row per trade."""
    if not _has(cur, "sec_ins_transactions"):
        return []
    today = today or datetime.date.today()
    rows = cur.execute("""
        WITH own AS (
            SELECT accession, arg_min(owner_name, owner_cik) AS owner_name, arg_min(title, owner_cik) AS title,
                   bool_or(is_director) AS is_director, bool_or(is_officer) AS is_officer,
                   bool_or(is_ten_percent) AS is_ten_percent
            FROM sec_ins_owners GROUP BY accession)
        SELECT f.issuer_cik, f.issuer_symbol, t.trans_date, f.filed, o.owner_name, o.title,
               o.is_director, o.is_officer, o.is_ten_percent, t.trans_code, t.shares, t.price, f.plan_10b5_1
        FROM sec_ins_transactions t JOIN sec_ins_filings f USING (accession) LEFT JOIN own o USING (accession)
        WHERE NOT t.derivative AND NOT f.amendment AND t.trans_code IN ('P', 'S')
          AND t.trans_date BETWEEN ? AND ?""", [today - datetime.timedelta(days=365), today]).fetchall()
    tickers = _tickers(cur)
    out = []
    for cik, sym, td, filed, name, title, is_dir, is_off, is_ten, code, shares, price, plan in rows:
        t = tickers.get(cik) or (sym or "").strip().upper() or None
        if not t:
            continue
        # a filer's typo (a price of $1,062,500 a share, a $3.7tn "sale") must never lead a ranking: no open-market
        # insider trade has topped $20bn, and no listed share trades above $1mn
        if price is not None and (price > 1e6 or price <= 0) or (shares is not None and price is not None and shares * price > 2e10):
            continue
        out.append({"ticker": t, "trade_date": td, "filed": filed, "insider": name, "title": (title or None),
                    "is_director": bool(is_dir), "is_officer": bool(is_off), "is_ten_percent_owner": bool(is_ten),
                    "side": "buy" if code == "P" else "sell", "shares": _num(shares), "price_usd": _num(price),
                    "value_usd": round(shares * price, 2) if (shares is not None and price is not None) else None,
                    "planned": bool(plan)})
    return out


# ---------------------------------------------------------------- 13F
def fund_rows(cur):
    """Per stock: holders, value and shares in the latest 13F quarter against
    the one before. A line whose value / shares is more than three times off
    the security's median implied price is a units slip: its shares are set
    aside, its value kept (the same rule as /api/v1/us/holdings/issuer)."""
    if not (_has(cur, "sec_13f_holdings") and _has(cur, "sec_13f_batches")):
        return []
    from . import us_link
    batches = cur.execute("SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 2").fetchall()
    if not batches:
        return []
    by_cik = us_link._build_cusips_memo(cur)["by_cik"]
    primary = {v[0]["cusip"]: cik for cik, v in by_cik.items() if v}
    if not primary:
        return []

    def window(batch):
        rows = cur.execute("""
            WITH latest_report AS (
                SELECT manager_cik, max(report_period) AS report_period
                FROM sec_13f_filings WHERE batch = ? AND NOT amendment GROUP BY manager_cik),
            raw AS (
                SELECT h.cusip, f.manager_cik, f.report_period, h.value, h.shares
                FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
                JOIN latest_report l ON l.manager_cik = f.manager_cik AND l.report_period = f.report_period
                WHERE h.batch = ? AND NOT f.amendment AND h.put_call IS NULL AND h.cusip IS NOT NULL),
            med AS (SELECT cusip, median(value / shares) AS px FROM raw WHERE shares > 0 AND value > 0 GROUP BY cusip)
            SELECT r.cusip, max(r.report_period), count(DISTINCT r.manager_cik), sum(r.value),
                   sum(CASE WHEN r.shares > 0 AND r.value > 0 AND m.px > 0
                            AND (r.value / r.shares) / m.px BETWEEN 1.0/3 AND 3 THEN r.shares END),
                   list(DISTINCT r.manager_cik)
            FROM raw r LEFT JOIN med m USING (cusip)
            GROUP BY r.cusip""", [batch, batch]).fetchall()
        return {r[0]: r[1:] for r in rows if r[0] in primary}
    now = window(batches[0][0])
    before = window(batches[1][0]) if len(batches) > 1 else {}
    tickers = _tickers(cur)
    splits = _splits(cur)
    out = []
    for cusip, (period, holders, value, shares, mgrs) in now.items():
        t = tickers.get(primary[cusip])
        if not t:
            continue
        p = before.get(cusip)
        prior_mgrs = set(p[4]) if p else set()
        mgrs = set(mgrs)
        prior_shares = p[3] if p else None
        # the earlier count in today's shares: a 10-for-1 split between the two quarter-ends multiplies it by 10
        if prior_shares and p[0] and period:
            for d, ratio in splits.get(_sym(t), []):
                if p[0] < d <= period:
                    prior_shares *= ratio
        change = round((shares / prior_shares - 1) * 100, 4) if (shares and prior_shares) else None
        # most holders "new" at once is a new CUSIP (a restructuring, a share-class change), not buying
        if p and holders and p[1] is not None and p[1] < 0.5 * holders:
            change = None
        out.append({"ticker": t, "quarter_end": period, "holders": holders, "holders_prior": p[1] if p else None,
                    "new_holders": len(mgrs - prior_mgrs) if p else None, "exited_holders": len(prior_mgrs - mgrs) if p else None,
                    "reported_value_usd": _num(value), "shares_held": _num(shares),
                    "shares_held_prior": _num(prior_shares), "shares_change_pct": change})
    return out


def _sym(t):
    return (t or "").strip().upper().replace("-", ".")


def _splits(cur):
    """{symbol: [(date, ratio)]} from the price history (us_prices_api), ratio = shares after ÷ before."""
    try:
        from . import us_prices_api
        return {_sym(k): v for k, v in us_prices_api.split_events(cur).items()}
    except Exception:                                             # noqa: BLE001 — no prices: no adjustment
        return {}


# ---------------------------------------------------------------- the sandbox
def event_rows(cur):
    """Every event day loaded by equity/us_events.py, as the box's `events` table."""
    if not _has(cur, "us_events"):
        return []
    cols = ("kind", "date", "end_date", "ticker", "label", "scheduled", "is_earnings")
    return [dict(zip(cols, r)) for r in cur.execute("""
        SELECT kind, date, end_date, ticker, label, coalesce(scheduled, false), is_earnings
        FROM us_events ORDER BY date, kind, ticker""").fetchall()]


BUILDERS = {"pay": pay_rows, "quarters": quarter_rows, "segments": segment_rows,
            "insider_trades": insider_rows, "fund_holdings": fund_rows, "events": event_rows}


def _load(con, table, cols, rows):
    """One table in one call, its rows as one JSON parameter cast column by column."""
    defs = [(c, t) for c, t, _ in cols]
    payload = json.dumps([[(r.get(c).isoformat() if isinstance(r.get(c), (datetime.date, datetime.datetime)) else r.get(c))
                           for c, _ in defs] for r in rows])
    sel = ", ".join('(j->>%d)::%s AS "%s"' % (i, ty, c) for i, (c, ty) in enumerate(defs))
    con.execute("CREATE TABLE %s AS SELECT %s FROM (SELECT unnest(from_json(?::JSON, '[\"JSON\"]')) AS j)" % (table, sel),
                [payload])


def build(cur):
    """{table: rows} for the row-built tables; a table whose source is missing is empty."""
    return {name: (BUILDERS[name](cur) or []) for name in TABLES if name not in _DIRECT}


# ---------------------------------------------------------------- prices
def _price_pieces(cur, tickers):
    """[(feed symbol, ticker, from, before)] and [(ticker, split day, ratio)]:
    which bars make up each ticker's clean history (app/us_prices_api.py:
    a reused ticker starts at its break, a ticker change is joined to the old
    symbol) and the splits inside it."""
    from . import us_prices_api
    feed = {r[0] for r in cur.execute("SELECT DISTINCT symbol FROM us_px_daily").fetchall()}
    ev = us_prices_api._events(cur)
    pieces, splits = [], []
    for t in sorted(set(tickers)):
        sym = next((s for s in (_sym(t), t, t.replace(".", "-")) if s in feed), None)
        if not sym:
            continue
        parts = [(sym, None, None)]
        brk = ev["breaks"].get(sym)
        if brk:
            if sym in ev["links"]:
                old, d = ev["links"][sym]
                parts = [(old, None, d), (sym, d, None)]
            else:
                parts = [(sym, brk[0], None)]
        for src, lo, hi in parts:
            pieces.append((src, t, lo, hi))
            splits += [(t, d, ratio) for d, ratio in ev["splits"].get(src, [])
                       if (lo is None or d >= lo) and (hi is None or d < hi)]
    return pieces, splits


def price_table(cur, db, tickers):
    """The `prices` table in `db`: one row per ticker per trading day,
    split-adjusted the same way as /api/v1/us/prices. Three million rows do not
    travel as JSON, so they go through a temporary Parquet file, deleted
    straight after. Returns the row count."""
    import os
    import tempfile
    pieces, splits = _price_pieces(cur, tickers)
    if not pieces:
        _empty_prices(db)
        return 0
    cur.execute("CREATE OR REPLACE TEMP TABLE _px_pieces (src VARCHAR, ticker VARCHAR, lo DATE, hi DATE)")
    cur.executemany("INSERT INTO _px_pieces VALUES (?, ?, ?, ?)", pieces)
    cur.execute("CREATE OR REPLACE TEMP TABLE _px_splits (ticker VARCHAR, d DATE, ratio DOUBLE)")
    if splits:
        cur.executemany("INSERT INTO _px_splits VALUES (?, ?, ?)", splits)
    fd, path = tempfile.mkstemp(suffix=".parquet", prefix="plover-prices-")
    os.close(fd)
    try:
        cur.execute("""
            COPY (
                WITH bars AS (
                    SELECT pc.ticker, p.date, p.close, p.volume
                    FROM us_px_daily p JOIN _px_pieces pc ON p.symbol = pc.src
                     AND (pc.lo IS NULL OR p.date >= pc.lo) AND (pc.hi IS NULL OR p.date < pc.hi)
                    WHERE p.close > 0),
                f AS (
                    SELECT b.ticker, b.date, exp(sum(ln(s.ratio))) AS factor
                    FROM bars b JOIN _px_splits s ON s.ticker = b.ticker AND s.d > b.date
                    GROUP BY b.ticker, b.date)
                SELECT b.ticker, b.date, b.close AS close_usd, b.close / coalesce(f.factor, 1.0) AS adj_close_usd,
                       b.volume::BIGINT AS volume
                FROM bars b LEFT JOIN f USING (ticker, date)
                ORDER BY b.ticker, b.date
            ) TO '%s' (FORMAT PARQUET)""" % path.replace("'", "''"))
        db.execute("CREATE TABLE prices AS SELECT * FROM read_parquet('%s')" % path.replace("'", "''"))
    finally:
        cur.execute("DROP TABLE IF EXISTS _px_pieces")
        cur.execute("DROP TABLE IF EXISTS _px_splits")
        try:
            os.remove(path)
        except OSError:
            pass
    return db.execute("SELECT count(*) FROM prices").fetchone()[0]


def _empty_prices(db):
    db.execute("CREATE TABLE prices AS SELECT %s WHERE false" % ", ".join(
        "NULL::%s AS %s" % (t, c) for c, t, _ in TABLES["prices"][1]))


def _price_tickers(cur):
    """The tickers of `stocks` (app/us_steps.py) and every benchmark's fund."""
    from . import us_benchmarks, us_steps
    return [r["ticker"] for r in us_steps.base_rows(cur)] + [b["etf"] for b in us_benchmarks.BENCHMARKS.values()]


def sandbox(cur):
    """The shared in-memory database holding the tables, built once per
    database version. Each question opens its own cursor on it."""
    import duckdb
    v = sec_api.file_version()
    with _LOCK:
        if _STATE.get("v") == v and _STATE.get("db") is not None:
            return _STATE["db"]
    data = build(cur)
    db = duckdb.connect(":memory:")
    for name, (_, cols) in TABLES.items():
        if name not in _DIRECT:
            _load(db, name, cols, data.get(name) or [])
    n_prices = 0
    if cur is not None and _has(cur, "us_px_daily"):
        n_prices = price_table(cur, db, _price_tickers(cur))
    else:
        _empty_prices(db)
    # nothing outside these tables from here on, and no query can undo it
    db.execute("SET memory_limit='%s'" % MEMORY_LIMIT)
    db.execute("SET threads=2")
    db.execute("SET enable_external_access=false")
    db.execute("SET lock_configuration=true")
    with _LOCK:
        _STATE.update(v=v, db=db, counts=dict({k: len(r) for k, r in data.items()}, prices=n_prices))
    return db


def counts():
    """Rows per table in the current sandbox, for the health of the route."""
    with _LOCK:
        return dict(_STATE.get("counts") or {})
