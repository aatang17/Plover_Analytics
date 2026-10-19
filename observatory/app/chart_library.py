# -*- coding: utf-8 -*-
"""The editor's chart library: charts a writer inserts in one click.

Searching every series is the slow way to the chart most notes need. This
module holds the quick ways, all in the editor's Research tab:

* **Common charts** — a short list per topic (Japan prices, rates, markets …),
  each a ready `chart` block: dataset, series, measure, start. The same list
  answers a search first: "CPI" or "10-year" gives the chart people mean, not
  fifteen series to choose from. An entry whose dataset or series is not on
  this server is left out, never shown broken.
* **Company charts** — for a listed company, the charts its Plover pages draw
  (Financials: revenue and profit, returns, cash flows, dividends; Ownership:
  largest holders), as page addresses the editor copies exactly as the page
  draws them. Only the ones its filings have figures for are offered.
* **Saved charts** — any chart a writer saves from their draft, kept for
  them (by person, like every personal setting: CLAUDE.md's Substack rule).
* **Most used** — how often each common chart has been inserted, across all
  writers, counts only; nobody's draft or name is kept.

Saved charts and counts live in staff.db's team_settings ("saved_charts@<staff
id>", "chart_uses"), small JSON values beside the publications' profiles.
Python 3.9: no 3.10+ syntax.
"""
import json
import re
import threading

from . import staff

TOPICS = (
    ("jp-prices", "Japan Prices"),
    ("jp-growth", "Japan Growth and Business"),
    ("rates", "Interest Rates"),
    ("jp-labour", "Japan Jobs and Wages"),
    ("jp-markets", "Japan Stock Market"),
    ("banks", "Banks"),
    ("fiscal", "Government Finances"),
    ("tourism", "Tourism"),
    ("people", "Population"),
    ("us", "United States"),
)

# id, topic, title, dataset, series, measure, start, words people type for it
_C = [
    ("jp-cpi", "jp-prices", "Japan Inflation: Headline, Core and Core-Core", "cpi-jp",
     ["0001", "0161", "0178"], "yoy", "2015-01",
     "cpi; inflation; consumer prices; core cpi; core core; japan inflation; prices"),
    ("jp-cpi-gs", "jp-prices", "Goods and Services Inflation", "cpi-jp-goods-services",
     ["0202", "0220"], "yoy", "2015-01", "goods inflation; services inflation; goods and services"),
    ("tokyo-cpi", "jp-prices", "Tokyo Inflation: Headline and Core", "cpi-tokyo",
     ["0001", "0161"], "yoy", "2015-01", "tokyo cpi; tokyo inflation"),
    ("jp-cpi-long", "jp-prices", "Japan Inflation Since 1946", "cpi-jp-long",
     ["0163"], "yoy", "", "inflation history; long run inflation; postwar inflation; cpi since 1946"),
    ("jp-gdp", "jp-growth", "Real GDP Growth", "gdp-jp",
     ["gdp.real_sa"], "yoy", "2010-01", "gdp; growth; economy; real gdp; japan gdp"),
    ("jp-consumption", "jp-growth", "Real Private Consumption", "gdp-jp",
     ["private_consumption.real_sa"], "yoy", "2010-01", "consumption; consumer spending; household spending"),
    ("jp-profits", "jp-growth", "Corporate Ordinary Profits", "corporate-finance-jp",
     ["ordinary_profit.all.all"], "yoy", "2010-01", "corporate profits; profits; ordinary profit; earnings"),
    ("jp-capex", "jp-growth", "Corporate Capital Investment", "corporate-finance-jp",
     ["capex.all.all"], "yoy", "2010-01", "capex; capital spending; investment; capital investment"),
    # wages-jp publishes the year-on-year rates themselves: drawn as published
    ("jp-wages", "jp-labour", "Japan Wages: Nominal and Real, Year on Year", "wages-jp",
     ["cash_earnings_yoy.all.5plus.all", "real_wage_yoy.all.5plus.all"], "index", "2015-01",
     "wages; japan wages; pay; real wages; earnings growth; salaries; shunto"),
    ("jp-unemployment", "jp-labour", "Japan Unemployment Rate", "unemployment-jp",
     ["sa.unemployment_rate.total"], "index", "2005-01",
     "unemployment; unemployment rate; jobless; jobless rate; labour market; labor market"),
    ("jp-job-openings", "jp-labour", "Jobs-to-Applicants Ratio", "job-openings-jp",
     ["sa.active.incl_part"], "index", "2005-01",
     "job openings; jobs to applicants; job offers; labour shortage; labor shortage; jobs"),
    ("jgb", "rates", "JGB Yields: 2-Year and 10-Year", "jgb-yields",
     ["2Y", "10Y"], "index", "2015-01", "jgb; jgb yields; 10-year; 10 year; japan yields; bond yields; japan rates"),
    ("ust", "rates", "US Treasury Yields: 2-Year and 10-Year", "ust-yields",
     ["2Y", "10Y"], "index", "2015-01", "treasury; treasuries; us yields; us rates; us 10-year; ust"),
    ("loan-rates", "rates", "Bank Lending Rates on New Loans", "boj-loan-rates",
     ["DLLR2CIDBNL1"], "index", "2010-01", "lending rates; loan rates; bank lending; lending; loans"),
    ("foreign-flows", "jp-markets", "Foreign Investors: Buying and Selling", "investor-flows-jp",
     ["prime.foreigners.purchases.value", "prime.foreigners.sales.value"], "index", "2022-04",
     "foreign investors; foreign flows; investor flows; foreigners; foreign buying"),
    ("margin", "jp-markets", "Margin Buying and Short Selling", "margin-jp",
     ["customer.purchases.value", "customer.sales.value"], "index", "2015-01",
     "margin; margin trading; short selling; shorts"),
    ("boj-etf", "jp-markets", "Bank of Japan ETF Holdings", "boj-assets",
     ["MA030210316S"], "index", "2010-01", "boj etf; etf holdings; etf purchases; bank of japan etf"),
    ("npl", "banks", "City Banks' Bad-Loan Ratio", "fsa-npl",
     ["city.npl-ratio"], "index", "", "bad loans; npl; non-performing loans; bad-loan ratio"),
    ("govt-debt", "fiscal", "Central Government Debt", "govt-debt-jp",
     ["total"], "index", "", "government debt; public debt; national debt; debt"),
    ("consumption-tax", "fiscal", "Consumption Tax Collected", "tax-receipts-jp",
     ["general.consumption-tax.settlement"], "index", "", "consumption tax; tax receipts; tax revenue; taxes"),
    ("visitors", "tourism", "Visitor Arrivals: Total and China", "jnto-visitors",
     ["total", "cn"], "index", "2015-01", "visitors; tourists; inbound; tourism; arrivals; chinese tourists"),
    ("guest-nights", "tourism", "Hotel Guest Nights, All Japan", "accommodation-jp",
     ["nights.jp"], "index", "2015-01", "guest nights; hotels; hotel stays; accommodation"),
    ("population", "people", "Japan's Population", "population-jp",
     ["00.all.population"], "index", "", "population; demographics; residents"),
    ("us-cpi", "us", "US Inflation: Headline and Core", "cpi-us",
     ["CUUR0000SA0", "CUUR0000SA0L1E"], "yoy", "2015-01", "us cpi; us inflation; american inflation"),
    ("us-wages", "us", "US Average Hourly Earnings", "us-wages",
     ["CES0500000003"], "yoy", "2015-01", "us wages; hourly earnings; us pay; wages"),
]
COMMON = [{"id": c[0], "topic": c[1], "title": c[2], "dataset": c[3], "series": c[4],
           "measure": c[5], "start": c[6],
           "words": [w.strip() for w in c[7].split(";") if w.strip()]} for c in _C]
BY_ID = dict((c["id"], c) for c in COMMON)

# A company's charts: what its Plover pages draw, as the address that draws
# it (the editor copies it from there, so "Copy Again" gets the same chart).
# The fields say which filed figures it needs; with none, it is not offered.
COMPANY_CHARTS = (
    ("revenue_profit", "Revenue and Profit", "/financials.html?c=%s", ("revenue", "profit")),
    ("returns", "Return on Equity and Equity Ratio", "/financials.html?c=%s&chart=returns",
     ("roe_pct", "equity_ratio_pct")),
    ("cf", "Cash Flows", "/financials.html?c=%s&chart=cf",
     ("cf_operating", "cf_investing", "cf_financing")),
    ("dividends", "Dividends and Payout Ratio", "/financials.html?c=%s&chart=dividends",
     ("dps", "payout_ratio_pct")),
)
OWNERSHIP_CHART = ("owners", "Largest Shareholders", "/ownership.html?c=%s")

SAVED_MAX = 60
TITLE_MAX = 200
_lock = threading.Lock()
_CODE = re.compile(r"^[0-9A-Z]{4}$")


# ------------------------------------------------------------------ common

_present = {}


def _available(entry):
    """True when the entry's dataset and every one of its series are on this
    server. Remembered per process: datasets do not lose series."""
    key = entry["id"]
    if _present.get(key):
        return True
    from . import api, registry
    try:
        if not registry.available(entry["dataset"]):
            return False
        con = api._con()
        n = con.execute("SELECT count(*) FROM series WHERE dataset = ? AND code IN (%s)"
                        % ",".join("?" for _ in entry["series"]),
                        [entry["dataset"]] + list(entry["series"])).fetchone()[0]
    except Exception:  # noqa: BLE001 — no macro database here: nothing to offer
        return False
    ok = n == len(entry["series"])
    if ok:
        _present[key] = True
    return ok


def block(entry):
    """The editor's `chart` block for a common chart (research_doc's shape)."""
    return {"type": "chart", "dataset": entry["dataset"], "series": list(entry["series"]),
            "measure": entry["measure"], "start": entry["start"], "end": "",
            "title": entry["title"], "note": ""}


def _card(entry):
    return {"id": entry["id"], "title": entry["title"], "topic": entry["topic"],
            "block": block(entry)}


def topics():
    out = []
    for key, label in TOPICS:
        charts = [_card(c) for c in COMMON if c["topic"] == key and _available(c)]
        if charts:
            out.append({"key": key, "label": label, "charts": charts})
    return out


def _norm(text):
    return " ".join(re.sub(r"[^a-z0-9぀-ヿ一-鿿]+", " ", (text or "").lower()).split())


def match(q, limit=3):
    """Common charts a search means, best first: the whole query is one of an
    entry's words ("cpi"), or every word of the query is in one of them
    ("japan yields"). Short or vague queries match nothing."""
    q = _norm(q)
    if len(q) < 2:
        return []
    qw = q.split()
    scored = []
    for i, c in enumerate(COMMON):
        words = [_norm(w) for w in c["words"]] + [_norm(c["title"])]
        if q in words:
            score = 3
        elif any(w.startswith(q) for w in words) and len(q) >= 3:
            score = 2
        elif all(any(x == w or (len(x) >= 3 and w.startswith(x)) for w in " ".join(words).split())
                 for x in qw) and len(qw) > 1:
            score = 1
        else:
            continue
        scored.append((-score, i, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [_card(c) for _, _, c in scored if _available(c)][:limit]


# ---------------------------------------------------------------- companies

def company_charts(code):
    """[{key, title, url}] for one listed company: the charts its filings can
    draw. Empty when the company has no annual reports on this server."""
    code = (code or "").strip().upper()
    if not _CODE.match(code):
        return []
    out = []
    try:
        from . import financials_api
        d = financials_api.company(code, basis="consolidated", as_filed_in="")
        panel = d.get("panel") or []
    except Exception:  # noqa: BLE001 — no filings here (404) or no equity data
        panel = []
    for key, title, url, fields in COMPANY_CHARTS:
        if any(r["values"].get(f) is not None for r in panel for f in fields):
            out.append({"key": key, "title": title, "url": url % code})
    try:
        from . import ownership_api
        has_owners = bool(ownership_api.company(code, year=""))
    except Exception:  # noqa: BLE001
        has_owners = False
    if has_owners:
        out.append({"key": OWNERSHIP_CHART[0], "title": OWNERSHIP_CHART[1],
                    "url": OWNERSHIP_CHART[2] % code})
    return out


# ------------------------------------------------------------------ usage

def _json_setting(key, default):
    try:
        v = json.loads(staff.team_setting(key) or "null")
    except ValueError:
        v = None
    return v if isinstance(v, type(default)) else default


def used(chart_id):
    """Count one insert of a common chart. Unknown ids are ignored."""
    if chart_id not in BY_ID:
        return
    with _lock:
        counts = _json_setting("chart_uses", {})
        counts[chart_id] = int(counts.get(chart_id, 0)) + 1
        staff.set_team_setting("chart_uses", json.dumps(counts))


def popular(limit=6):
    counts = _json_setting("chart_uses", {})
    ranked = sorted((c for c in COMMON if counts.get(c["id"])),
                    key=lambda c: (-int(counts[c["id"]]), c["title"]))
    return [dict(_card(c), uses=int(counts[c["id"]])) for c in ranked if _available(c)][:limit]


# ------------------------------------------------------------------ saved

class LibraryError(ValueError):
    """A save refused, worded for the writer."""


def _saved_key(staff_id):
    return "saved_charts@%d" % int(staff_id)


def saved(staff_id):
    return [s for s in _json_setting(_saved_key(staff_id), []) if isinstance(s, dict)]


def _clean_block(b):
    """Only the two kinds of chart a library can hold, with their own fields:
    a live `chart` (dataset, series, measure, dates) or a page `snapshot`
    (its address and which chart), never a copied picture or data."""
    if not isinstance(b, dict):
        raise LibraryError("That is not a chart.")
    title = " ".join(str(b.get("title") or "").split())[:TITLE_MAX]
    if b.get("type") == "chart":
        series = [str(s)[:80] for s in (b.get("series") or [])][:6]
        if not b.get("dataset") or not series:
            raise LibraryError("Pick a series for the chart before saving it.")
        from . import research_doc as rd
        return {"type": "chart", "dataset": str(b["dataset"])[:60], "series": series,
                "measure": b.get("measure") if b.get("measure") in rd.MEASURES else "index",
                "start": str(b.get("start") or "")[:10], "end": str(b.get("end") or "")[:10],
                "title": title, "note": ""}
    if b.get("type") == "snapshot":
        from . import research_doc as rd
        url = rd.page_path(str(b.get("url") or ""))
        if not url:
            raise LibraryError("Only charts copied from a Plover page can be saved.")
        try:
            chart = max(0, int(b.get("chart") or 0))
        except (TypeError, ValueError):
            chart = 0
        return {"type": "snapshot", "url": url, "chart": chart, "title": title, "note": ""}
    raise LibraryError("Only charts can be saved.")


def save(staff_id, b):
    blk = _clean_block(b)
    with _lock:
        rows = saved(staff_id)
        same = [r for r in rows if r.get("block") == blk]
        if same:
            return same[0]
        if len(rows) >= SAVED_MAX:
            raise LibraryError("You can keep up to %d saved charts. Remove one first." % SAVED_MAX)
        sid = max([int(r.get("id") or 0) for r in rows] + [0]) + 1
        row = {"id": sid, "title": blk["title"] or "Untitled chart", "block": blk}
        rows.insert(0, row)
        staff.set_team_setting(_saved_key(staff_id), json.dumps(rows, ensure_ascii=False))
    return row


def unsave(staff_id, saved_id):
    with _lock:
        rows = [r for r in saved(staff_id) if int(r.get("id") or 0) != int(saved_id)]
        staff.set_team_setting(_saved_key(staff_id), json.dumps(rows, ensure_ascii=False))
    return rows


def view(staff_id):
    """Everything the library shows one writer."""
    return {"topics": topics(), "popular": popular(), "saved": saved(staff_id)}
