# -*- coding: utf-8 -*-
"""Step-by-step answers: the model writes the steps, the server runs them.

The fixed question shapes in app/stock_recipes.py answer the common questions
fast and with finished charts. Everything else, the counts, shares, averages
and lists filtered on several figures that anyone could work out with a
spreadsheet, is answered here the way an analyst would: break the question
into steps, run them on the data, read off the result.

  1. The model is given the question, today's date, a plain description of
     two tables (``stocks``: one row per listed company; ``benchmarks``: the
     index funds), the house rules (app/us_steps_rules.txt) and the worked
     examples closest to the question (app/us_steps_examples.json).
  2. It replies with its steps in words, the price period the question
     means, one SQL query, and a headline whose numbers are {placeholders}
     naming the query's result columns. It never sees a figure from the data
     and never writes one: every number in the answer is read off the result
     by code.
  3. The server builds the two tables from the same functions that serve
     /api/v1 (the period's price changes included), loads them into a fresh
     in-memory DuckDB that can reach no file, network or other database, and
     runs the query under the Studies runner's guard (app/analysis.py): one
     read-only statement, no blank turned into zero, a time limit.
  Before its final query the model may look: send one exploratory query
     ("look") and see up to twenty rows of its result, at most three times,
     to learn how a segment is spelt, which months a stock has, what a
     column holds. What it saw never reaches the answer: the headline is
     filled only from the final query's result, and a final query that
     copies a figure it saw as a number is sent back.
  4. A query that fails, or finds nothing, goes back to the model once with
     the error. What it still cannot answer is logged (data/us_ask_log.jsonl)
     so each miss can become a new worked example.

Teaching it more is editing two files: a rule in us_steps_rules.txt, a
worked example in us_steps_examples.json (tests/test_us_steps.py runs every
example's query against the data, so a broken example fails the build).
"""
import datetime
import json
import os
import re
import threading

from fastapi import HTTPException

from . import analysis, ask_progress, sec_api, us_benchmarks, us_checks, us_link, us_prices_api, us_tables

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(HERE, "us_steps_rules.txt")
EXAMPLES_FILE = os.path.join(HERE, "us_steps_examples.json")
LOG_FILE = os.path.join(HERE, "..", "data", "us_ask_log.jsonl")
EXAMPLES_SHOWN = 6
RESULT_ROWS_MAX = 200
SECONDS_MAX = 8
LOOKS_MAX = 3
LOOK_ROWS_SHOWN = 20


class StepsError(Exception):
    """The model's steps could not be run."""


# ---------------------------------------------------------------- sectors
# A broad sector from the SEC's industry code (SIC). Approximate: SIC is not
# GICS, so Alphabet and Meta are Technology here, not Communication.
_SECTORS = [
    ((6324, 6324), "Health Care"), ((6798, 6798), "Real Estate"), ((6500, 6553), "Real Estate"),
    ((6000, 6799), "Financials"),
    ((1200, 1399), "Energy"), ((2900, 2999), "Energy"), ((4610, 4619), "Energy"), ((4922, 4925), "Energy"),
    ((5170, 5172), "Energy"),
    ((4900, 4991), "Utilities"),
    ((2830, 2836), "Health Care"), ((3841, 3851), "Health Care"), ((8000, 8099), "Health Care"),
    ((5047, 5047), "Health Care"), ((5122, 5122), "Health Care"), ((8731, 8731), "Health Care"),
    ((3570, 3579), "Technology"), ((3661, 3679), "Technology"), ((3695, 3695), "Technology"),
    ((3825, 3829), "Technology"), ((7370, 7379), "Technology"),
    ((4800, 4899), "Communication"), ((2710, 2749), "Communication"), ((7810, 7841), "Communication"),
    ((2000, 2199), "Consumer Staples"), ((2840, 2844), "Consumer Staples"), ((5140, 5149), "Consumer Staples"),
    ((5400, 5499), "Consumer Staples"), ((5912, 5912), "Consumer Staples"), ((5331, 5331), "Consumer Staples"),
    ((100, 999), "Consumer Staples"),
    ((2200, 2399), "Consumer Discretionary"), ((2500, 2599), "Consumer Discretionary"),
    ((3010, 3021), "Consumer Discretionary"), ((3140, 3149), "Consumer Discretionary"),
    ((3630, 3639), "Consumer Discretionary"), ((3651, 3651), "Consumer Discretionary"),
    ((3711, 3716), "Consumer Discretionary"), ((3751, 3751), "Consumer Discretionary"),
    ((3942, 3949), "Consumer Discretionary"), ((4700, 4700), "Consumer Discretionary"),
    ((5200, 5999), "Consumer Discretionary"), ((7000, 7099), "Consumer Discretionary"),
    ((7200, 7299), "Consumer Discretionary"), ((7900, 7999), "Consumer Discretionary"),
    ((8200, 8299), "Consumer Discretionary"),
    ((1000, 1099), "Materials"), ((1400, 1499), "Materials"), ((2400, 2499), "Materials"),
    ((2600, 2699), "Materials"), ((2800, 2829), "Materials"), ((2850, 2899), "Materials"),
    ((3200, 3399), "Materials"), ((3410, 3412), "Materials"),
    ((1500, 1799), "Industrials"), ((2000, 3999), "Industrials"), ((4000, 4799), "Industrials"),
    ((5000, 5199), "Industrials"), ((7300, 7399), "Industrials"), ((8700, 8799), "Industrials"),
    ((7400, 8999), "Industrials"),
]
SECTOR_NAMES = ["Technology", "Health Care", "Financials", "Real Estate", "Energy", "Utilities", "Materials",
                "Industrials", "Consumer Discretionary", "Consumer Staples", "Communication"]


def sector_for(sic):
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return None
    for (lo, hi), name in _SECTORS:
        if lo <= s <= hi:
            return name
    return None


# ---------------------------------------------------------------- tables
_FUNDISH = re.compile(r"\b(FUND|TRUST|ETF|BITCOIN|ETHEREUM|ISHARES|SPDR|GRAYSCALE|PORTFOLIO|INCOME SECURITIES|PROSHARES|"
                      r"DIREXION|ULTRA|LEVERAGED|INVERSE|SPROTT|INVESCO DB|COMMODITY)\b", re.I)

# (column, SQL type, what it means) — the description is what the model reads
STOCK_COLUMNS = [
    ("ticker", "VARCHAR", "the main US ticker"),
    ("name", "VARCHAR", "company name"),
    ("sector", "VARCHAR", "broad sector, approximate, from the SEC industry code: " + ", ".join(SECTOR_NAMES)),
    ("industry", "VARCHAR", "the SEC's industry title in capitals, e.g. 'NATIONAL COMMERCIAL BANKS', "
                            "'SEMICONDUCTORS & RELATED DEVICES', 'SERVICES-PREPACKAGED SOFTWARE'; match with ILIKE"),
    ("exchange", "VARCHAR", "'Nasdaq', 'NYSE', 'CBOE' or 'OTC'"),
    ("country", "VARCHAR", "two-letter country of the company's business address as filed (US, CN, IL, JP, CA ...); blank for some foreign filers"),
    ("in_sp500", "BOOLEAN", "a member of the S&P 500 (from the SPY fund's holdings)"),
    ("sp500_weight_pct", "DOUBLE", "weight in the S&P 500, % (blank when not a member)"),
    ("in_dow", "BOOLEAN", "a member of the Dow Jones Industrial Average (from the DIA fund's holdings)"),
    ("dow_weight_pct", "DOUBLE", "weight in the Dow, % (blank when not a member)"),
    ("market_cap_usd", "DOUBLE", "market value, US$ (latest close x shares)"),
    ("price_usd", "DOUBLE", "latest close, US$"),
    ("change_period_pct", "DOUBLE", "price change over the period you name in \"period\", %, split-adjusted, before dividends"),
    ("change_ytd_pct", "DOUBLE", "price change since the first close of this year, %"),
    ("change_1y_pct", "DOUBLE", "price change over the past year, %"),
    ("revenue_usd", "DOUBLE", "revenue in the latest annual report, US$"),
    ("revenue_prior_usd", "DOUBLE", "revenue the year before, US$ (a growth rate from a tiny base is noise: require it above 5e7)"),
    ("revenue_growth_pct", "DOUBLE", "revenue growth on the year before, %"),
    ("operating_income_usd", "DOUBLE", "operating income, US$"),
    ("operating_margin_pct", "DOUBLE", "operating income / revenue, %"),
    ("net_income_usd", "DOUBLE", "net income, US$ (negative = a loss)"),
    ("net_margin_pct", "DOUBLE", "net income / revenue, %"),
    ("profit_growth_pct", "DOUBLE", "net income growth on the year before, %"),
    ("roe_pct", "DOUBLE", "return on equity, %"),
    ("fcf_usd", "DOUBLE", "free cash flow (operating cash flow - capital spending), US$"),
    ("fcf_margin_pct", "DOUBLE", "free cash flow / revenue, %"),
    ("cash_usd", "DOUBLE", "cash and equivalents, US$"),
    ("borrowings_usd", "DOUBLE", "debt: borrowings as filed (short- and long-term debt), US$; blank when not tagged"),
    ("total_assets_usd", "DOUBLE", "total assets, US$"),
    ("equity_usd", "DOUBLE", "shareholders' equity, US$"),
    ("eps_usd", "DOUBLE", "diluted earnings per share, US$"),
    ("pe_x", "DOUBLE", "price / earnings, times (blank for a loss or below 1x)"),
    ("dividend_yield_pct", "DOUBLE", "dividends paid / market value, %"),
    ("buyback_yield_pct", "DOUBLE", "buybacks / market value, %"),
    ("payout_ratio_pct", "DOUBLE", "dividends / net income, %"),
    ("fiscal_year_end", "DATE", "end of the fiscal year the annual figures cover"),
]
BENCH_COLUMNS = [
    ("key", "VARCHAR", "short key: " + ", ".join(us_benchmarks.ORDER)),
    ("name", "VARCHAR", "the index or fund's name"),
    ("fund_ticker", "VARCHAR", "the fund that tracks it (its price is the benchmark)"),
    ("has_members", "BOOLEAN", "true when the stocks table flags its members (sp500, dow); others are fund prices only"),
    ("change_period_pct", "DOUBLE", "price change over the period, %"),
    ("change_ytd_pct", "DOUBLE", "price change this year, %"),
    ("change_1y_pct", "DOUBLE", "price change over the past year, %"),
]
_FIGURE_SOURCES = {
    "revenue_usd": ("size", "revenue"), "operating_income_usd": ("size", "operating_income"),
    "net_income_usd": ("size", "net_income"), "cash_usd": ("size", "cash"),
    "total_assets_usd": ("size", "total_assets"), "equity_usd": ("size", "equity"),
    "fcf_usd": ("metrics", "fcf"), "eps_usd": ("per_share", "eps_diluted"),
}
_RATIOS = ["revenue_growth_pct", "operating_margin_pct", "net_margin_pct", "profit_growth_pct", "roe_pct",
           "fcf_margin_pct", "pe_x", "dividend_yield_pct", "buyback_yield_pct", "payout_ratio_pct"]

_BASE = {}
_BASE_LOCK = threading.Lock()


def _sym(t):
    return (t or "").replace("-", ".")


def _ok_change(w, last_day, closed):
    """A window change worth using: the stock still trades (or the period is
    over) and it did not start the period below $1 (reverse-split debris)."""
    if not w or w[1] is None or w[1] < 1.0:
        return None
    if not closed and last_day and (last_day - w[2]).days > 7:
        return None
    return round(w[4], 4)


def base_rows(cur):
    """[(column values except change_period_pct)], one per listed company;
    memoised per database version."""
    from . import stock_recipes
    v = sec_api.file_version()
    with _BASE_LOCK:
        if _BASE.get("v") == v:
            return _BASE["rows"]
    rows = list(stock_recipes.metric_rows(cur))
    have = {r["cik"] for r in rows}
    members = {}
    for key in ("sp500", "dow"):
        ciks, weights, _ = us_benchmarks.members(cur, key)
        members[key] = weights or {}
        for r in stock_recipes.all_metric_rows(cur):     # a member keeps its row even when its report is old
            if ciks and r["cik"] in ciks and r["cik"] not in have:
                rows.append(r)
                have.add(r["cik"])
    today = datetime.date.today()
    last_day = us_prices_api.latest_date(cur)
    debt = stock_recipes.figure_cross(cur, stock_recipes.FIGURE_BY_ID["debt"])
    ytd = us_prices_api.window_changes(cur, datetime.date(today.year, 1, 1))
    one_y = us_prices_api.window_changes(cur, today - datetime.timedelta(days=365))
    out = []
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        size = r.get("size") or {}
        if r.get("sic") in (6221, 6189, 6199, 6770) or (_FUNDISH.search(r.get("name") or "") and not (size.get("revenue") or 0) > 0):
            continue                                           # funds, trusts and blank-check companies are not stocks
        usd = r.get("unit") == "USD"
        m = r.get("metrics") or {}
        hit = debt.get(r["cik"]) if usd else None
        rec = {"ticker": t, "name": us_link.display_name(r.get("name")), "sector": sector_for(r.get("sic")),
               "industry": r.get("industry"), "exchange": r.get("exchange"), "country": r.get("country"),
               "borrowings_usd": hit[0] if hit else None,
               "revenue_prior_usd": ((r.get("prior") or {}).get("revenue")) if usd else None,
               "in_sp500": r["cik"] in members["sp500"], "sp500_weight_pct": members["sp500"].get(r["cik"]),
               "in_dow": r["cik"] in members["dow"], "dow_weight_pct": members["dow"].get(r["cik"]),
               "market_cap_usd": m.get("market_cap"), "price_usd": (r.get("price") or {}).get("close"),
               "change_ytd_pct": _ok_change(ytd.get(_sym(t)) or ytd.get(t), last_day, False),
               "change_1y_pct": _ok_change(one_y.get(_sym(t)) or one_y.get(t), last_day, False),
               "fiscal_year_end": r.get("period_end")}
        for col, (part, field) in _FIGURE_SOURCES.items():
            rec[col] = ((r.get(part) or {}).get(field)) if usd else None   # a 20-F filer's own currency is not US$
        for col in _RATIOS:
            rec[col] = m.get(col)
        out.append(rec)
    with _BASE_LOCK:
        _BASE.update(v=v, rows=out)
    return out


def period_changes(cur, start, end):
    closed = bool(end) and end < (us_prices_api.latest_date(cur) or datetime.date.today())
    ch = us_prices_api.window_changes(cur, start, end)
    last_day = us_prices_api.latest_date(cur)
    return ch, (lambda t: _ok_change(ch.get(_sym(t)) or ch.get(t), last_day, closed))


def bench_rows(cur, start, end):
    today = datetime.date.today()
    _, pc = period_changes(cur, start, end)
    ytd = us_prices_api.window_changes(cur, datetime.date(today.year, 1, 1))
    one_y = us_prices_api.window_changes(cur, today - datetime.timedelta(days=365))
    out = []
    for key in us_benchmarks.ORDER:
        b = us_benchmarks.BENCHMARKS[key]
        f = b["etf"]
        out.append({"key": key, "name": b["name"], "fund_ticker": f, "has_members": bool(b["members"]),
                    "change_period_pct": pc(f), "change_ytd_pct": (ytd.get(f) or (None,) * 5)[4],
                    "change_1y_pct": (one_y.get(f) or (None,) * 5)[4]})
    return out


def _load(con, table, defs, rows, temp=False):
    """One table in one call: the rows go in as a single JSON parameter and
    are cast column by column (row-by-row inserts took 11 s for 5,900 rows)."""
    payload = json.dumps([[(r.get(c).isoformat() if isinstance(r.get(c), (datetime.date, datetime.datetime)) else r.get(c))
                           for c, _ in defs] for r in rows])
    sel = ", ".join('(j->>%d)::%s AS "%s"' % (i, ty, c) for i, (c, ty) in enumerate(defs))
    con.execute("CREATE %sTABLE %s AS SELECT %s FROM (SELECT unnest(from_json(?::JSON, '[\"JSON\"]')) AS j)"
                % ("TEMP " if temp else "", table, sel), [payload])


def _connect(stocks, benches, cur=None):
    """The query's database. With `cur`, a cursor on the shared one holding
    pay, quarters, segments, insider trades and fund holdings
    (app/us_tables.py), which is already locked down; this question's
    `stocks` and `benchmarks` are its own temporary tables on that cursor."""
    import duckdb
    base = None
    if cur is not None:
        try:
            base = us_tables.sandbox(cur)
        except Exception:                                         # noqa: BLE001 — the extra tables are a bonus, never a failure
            base = None
    con = base.cursor() if base is not None else duckdb.connect(":memory:")
    for table, cols, rows in (("stocks", STOCK_COLUMNS + [("change_period_pct", "DOUBLE", "")], stocks),
                              ("benchmarks", BENCH_COLUMNS, benches)):
        seen, defs = set(), []
        for c, t, _ in cols:
            if c not in seen:
                seen.add(c)
                defs.append((c, t))
        _load(con, table, defs, rows, temp=base is not None)
    if base is None:
        # nothing outside these two tables from here on, and the query cannot undo it
        con.execute("SET memory_limit='256MB'")
        con.execute("SET threads=2")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
    return con


def run(cur, sql, start, end=None, look=False):
    """(rows, columns) of the model's query over this period's tables. A look
    returns its first rows and never fails for having more."""
    try:
        sql = analysis._check_query(sql)
    except analysis.AnalysisError as exc:
        raise StepsError(str(exc))
    if re.search(r"\bdata\b", sql) and not re.search(r"\b(stocks|benchmarks|%s)\b" % "|".join(us_tables.TABLES), sql):
        raise StepsError("Query the tables stocks, benchmarks, %s." % ", ".join(us_tables.TABLES))
    _, pc = period_changes(cur, start, end)
    stocks = [dict(r, change_period_pct=pc(r["ticker"])) for r in base_rows(cur)]
    con = _connect(stocks, bench_rows(cur, start, end), cur)
    timer = threading.Timer(SECONDS_MAX, con.interrupt)
    timer.start()
    try:
        res = con.execute(sql)
        cols = [d[0] for d in res.description]
        rows = res.fetchmany((LOOK_ROWS_SHOWN if look else RESULT_ROWS_MAX) + 1)
    except Exception as exc:                                      # noqa: BLE001 — DuckDB's words are what the model needs
        msg = str(exc).split("\n")[0]
        if "interrupt" in msg.lower():
            msg = "The query took longer than %d seconds." % SECONDS_MAX
        raise StepsError("The query failed: %s" % msg[:300])
    finally:
        timer.cancel()
        con.close()
    if len(rows) > RESULT_ROWS_MAX and not look:
        raise StepsError("The query returns more than %d rows; add LIMIT or aggregate." % RESULT_ROWS_MAX)
    return [[analysis._plain(v) for v in r] for r in rows], cols


# ---------------------------------------------------------------- looking first
def _look(cur, plan):
    """Run the plan's exploratory query: (what to tell the model, the numbers it saw, a record for the reader)."""
    start, end = _period(plan)
    sql = str(plan.get("look") or "")
    try:
        rows, cols = run(cur, sql, start, end, look=True)
    except StepsError as exc:
        return "%s Fix it, or answer without looking." % exc, [], {"query": sql, "rows": None, "error": str(exc)}
    more = len(rows) > LOOK_ROWS_SHOWN
    rows = rows[:LOOK_ROWS_SHOWN]
    seen = [v for r in rows for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
    text = ("Result of your look (%s%d rows):\n%s\nNow reply with the JSON object again: another \"look\" if you "
            "must, or the final \"sql\" and headline. Compute every figure inside the final query; never copy a "
            "number you see here into it." % ("first " if more else "", len(rows),
                                              json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False, default=str)[:6000]))
    return text, seen, {"query": sql, "rows": len(rows), "more": more}


_LITERAL = re.compile(r"(?<![\w.'-])-?\d+(?:\.\d+)?(?![\w.'-])")


def copied_figures(sql, seen, question=""):
    """Numbers in the final query that are figures the model saw in a look
    (three or more significant digits, not a year, not in the question)."""
    if not seen or not sql:
        return []
    asked = set(re.findall(r"\d+(?:\.\d+)?", question or ""))
    out = []
    for lit in _LITERAL.findall(re.sub(r"'[^']*'", "''", sql)):        # dates and names in quotes are not figures
        digits = lit.lstrip("-").replace(".", "").lstrip("0")
        if len(digits.rstrip("0") if "." not in lit else digits) < 3 or lit.lstrip("-") in asked:
            continue
        v = float(lit)
        if v.is_integer() and 1900 <= v <= 2100:
            continue
        k = len(lit.split(".")[1]) if "." in lit else 0
        if any(abs(round(float(x), k) - v) < 1e-9 for x in seen):
            out.append(lit)
    return out


# ---------------------------------------------------------------- the prompt
def _table_doc():
    lines = ["stocks: one row per US-listed company (funds left out)."]
    lines += ["  %s %s: %s" % (c, t, d) for c, t, d in STOCK_COLUMNS]
    lines.append("benchmarks: one row per index or fund we can compare against.")
    lines += ["  %s %s: %s" % (c, t, d) for c, t, d in BENCH_COLUMNS]
    lines.append(us_tables.doc())
    return "\n".join(lines)


def _dates(today=None):
    today = today or datetime.date.today()
    return {"YEAR": str(today.year), "LAST_YEAR": str(today.year - 1), "TODAY": today.isoformat(),
            "ONE_YEAR_AGO": (today - datetime.timedelta(days=365)).isoformat()}


def _fill_dates(text, today=None):
    d = _dates(today)
    return re.sub(r"\b(YEAR|LAST_YEAR|TODAY|ONE_YEAR_AGO)\b", lambda m: d[m.group(1)], text)


def examples(today=None):
    with open(EXAMPLES_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return [json.loads(_fill_dates(json.dumps(x), today)) for x in raw]


_STOP = set("the a an of in on for to and or is are was were did do does how what which who with by than this that "
            "these those it its me my there their them as at be been from has have had".split())


def _words(text):
    return {w for w in re.findall(r"[a-z0-9&]+", (text or "").lower()) if w not in _STOP}


def closest_examples(question, n=EXAMPLES_SHOWN, today=None):
    q = _words(question)
    scored = []
    for i, ex in enumerate(examples(today)):
        w = _words(ex["question"])
        score = len(q & w) / float(len(q | w) or 1)
        scored.append((score, -i, ex))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [ex for _, _, ex in scored[:n]]


def prompt(question, last_price_date=None, today=None):
    today = today or datetime.date.today()
    with open(RULES_FILE, encoding="utf-8") as f:
        rules = _fill_dates(f.read(), today)
    shown = []
    for ex in closest_examples(question, today=today):
        reply = {k: ex.get(k) for k in ("steps", "period", "sql", "headline", "chart", "cannot")}
        shown.append("Q: %s\nA: %s" % (ex["question"], json.dumps(reply, ensure_ascii=False)))
    return ("You answer questions about US-listed stocks the way a careful analyst would: break the question into "
            "steps and write them as one DuckDB SQL query over the tables below (join them on ticker). You never write a number "
            "from the data yourself: the query finds every number, and your headline names the query's result "
            "columns in {braces} for the server to fill in.\n"
            "Today is %s. Daily prices run to %s.\n\n"
            "Reply with one JSON object:\n"
            "{\"look\": null, or one exploratory SELECT to run before you answer; leave \"sql\" empty and you are "
            "shown up to %d rows of its result, then you reply again (at most %d looks)\n"
            " \"steps\": [2 to 5 short plain-English steps],\n"
            " \"period\": {\"from\": \"YYYY-MM-DD\", \"to\": \"YYYY-MM-DD\" or null} (the price period the question "
            "means; it fills change_period_pct),\n"
            " \"sql\": \"one SELECT or WITH ... SELECT\",\n"
            " \"headline\": \"one sentence answering the question, numbers only as {result_column}, the period as {period}\",\n"
            " \"chart\": null or {\"label\": <result column>, \"value\": <result column>} for a list worth drawing as bars,\n"
            " \"cannot\": null, or one sentence for the reader saying what Plover's data does not hold (never \"the tables\"), "
            "or \"unclear\" when the message is not a question}\n\n"
            "TABLES\n%s\n\nRULES\n%s\n\nWORKED EXAMPLES\n%s"
            % (today.isoformat(), (last_price_date or today).isoformat(), LOOK_ROWS_SHOWN, LOOKS_MAX, _table_doc(), rules,
               "\n\n".join(shown)))


# ---------------------------------------------------------------- the model
def _ask_model(messages):
    from . import us_ask_model
    cfg = us_ask_model.provider()
    if cfg is None or not us_ask_model.under_cap():
        raise StepsError("off")
    prov, key, model = cfg
    us_ask_model._tick("calls")
    try:
        out = us_ask_model._complete(prov, key, model, messages, max_tokens=900, timeout=25, cache_key="plover-us-steps")
    except Exception as exc:                                      # noqa: BLE001 — a timeout, a bad reply
        us_ask_model._tick("failed")
        raise StepsError("the model did not answer (%s)" % type(exc).__name__)
    m = re.search(r"\{.*\}", out.get("text") or "", re.S)
    try:
        plan = json.loads(m.group(0)) if m else None
    except ValueError:
        plan = None
    if not isinstance(plan, dict):
        us_ask_model._tick("failed", out.get("usage"))
        raise StepsError("the model's reply was not readable")
    us_ask_model._tick("matched", out.get("usage"))
    return plan, out.get("text") or ""


def enabled():
    from . import us_ask_model
    return us_ask_model.provider() is not None


# ---------------------------------------------------------------- the answer
def _period(plan):
    today = datetime.date.today()
    p = plan.get("period") or {}
    try:
        start = datetime.date.fromisoformat(str(p.get("from"))[:10]) if p.get("from") else datetime.date(today.year, 1, 1)
    except ValueError:
        start = datetime.date(today.year, 1, 1)
    try:
        end = datetime.date.fromisoformat(str(p.get("to"))[:10]) if p.get("to") else None
    except ValueError:
        end = None
    if end and end >= today:
        end = None
    if start > today:
        start = datetime.date(today.year, 1, 1)
    return start, end


def period_words(start, end, today=None, last=None):
    """The period in words; with `last` (the latest close on file) both dates
    are given in brackets, so the reader sees which days are compared."""
    return _period_words(start, end, today)


def _period_words(start, end, today=None):
    from .stock_recipes import date_words
    today = today or datetime.date.today()
    if end and start.month == 1 and start.day == 1 and end.month == 12 and end.day == 31 and start.year == end.year:
        return "in %d" % start.year
    if end:
        return "from %s to %s" % (date_words(start), date_words(end))
    if start == datetime.date(today.year, 1, 1):
        return "so far in %d" % today.year
    if abs((today - start).days - 365) <= 3:
        return "over the past year"
    return "since %s" % date_words(start)


_SIGNED = re.compile(r"change|diff|gap|return|vs|minus|excess|relative")


def fmt_value(col, v):
    """A result value in words, by the column's unit suffix."""
    from .stock_recipes import date_words, money, mult, pct
    if v is None:
        return "—"
    c = col.lower()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if c.endswith("_pct"):
            return pct(v, 1, sign=bool(_SIGNED.search(c)))
        if c.endswith("_pp"):
            return ("+" if v > 0 else "−" if v < 0 else "") + "%.1f pp" % abs(v)
        if c.endswith("_usd"):
            return money(v)
        if c.endswith("_x"):
            return mult(v)
        if isinstance(v, int) or float(v).is_integer():
            return "{:,}".format(int(v))
        return "{:,.2f}".format(v)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(v)):
        return date_words(str(v))
    if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", str(v)):                 # a month: "2026-09" -> "September 2026"
        return datetime.date(int(str(v)[:4]), int(str(v)[5:]), 1).strftime("%B %Y")
    return str(v)


_ALLOWED_NUMBERS = {"500", "100", "2000", "400", "30", "1", "2", "10"}


def _query_numbers(sql):
    """The thresholds a query applies, in the forms a headline may write
    them: 5e7 -> 50 (mn), 2e9 -> 2 (bn), 20 -> 20."""
    out = set()
    for lit in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?:e\d+)?(?![\w.])", sql or "", re.I):
        try:
            v = float(lit)
        except ValueError:
            continue
        for x in (v, v / 1e3, v / 1e6, v / 1e9, v / 1e12):
            if x >= 1 and abs(x - round(x)) < 1e-9:
                out.add(str(int(round(x))))
            elif 0 < x < 1000:
                out.add(("%g" % x))
    return out


def fill_headline(template, cols, row, words, question, sql=""):
    """The headline with its {placeholders} filled from the first result row;
    None when it names a column the result lacks or writes a number of its
    own that neither the question nor the query's own thresholds gave."""
    if not template or not isinstance(template, str):
        return None
    bare = re.sub(r"\{[^}]*\}", " ", template)
    allowed = (set(_ALLOWED_NUMBERS) | set(re.findall(r"\d+(?:\.\d+)?", question or "")) | set(re.findall(r"\d{4}", words))
               | _query_numbers(sql))
    for n in re.findall(r"\d+(?:[.,]\d+)?", bare):
        if n.replace(",", "") not in allowed:
            return None
    out, ok = [], True

    def sub(m):
        nonlocal ok
        k = m.group(1).strip()
        if k == "period":
            return words
        if k not in cols or row is None:
            ok = False
            return ""
        return fmt_value(k, row[cols.index(k)])
    text = re.sub(r"\{([^}]*)\}", sub, template)
    return text if ok else None


_LABELS = {"ticker": "Ticker", "name": "Company", "sector": "Sector", "industry": "Industry", "exchange": "Exchange",
           "key": "Key", "fund_ticker": "Fund", "n": "Count", "members": "Members"}


def label(col):
    if col in _LABELS:
        return _LABELS[col]
    c = col.lower()
    for suf, unit in (("_pct", " %"), ("_pp", " (pp)"), ("_usd", " (USD)"), ("_x", " (×)")):
        if c.endswith(suf):
            base = col[:-len(suf)]
            return (base.replace("_", " ").strip().capitalize() or "Value") + unit
    return col.replace("_", " ").strip().capitalize()


def _unit(col):
    c = col.lower()
    return "%" if c.endswith("_pct") else "pp" if c.endswith("_pp") else "USD" if c.endswith("_usd") else \
        "×" if c.endswith("_x") else ""


def log(question, outcome, detail=None):
    """One line per question the box could not answer, or answered step by
    step: what to teach it next. Questions only; nothing about the reader."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                                "question": (question or "")[:300], "outcome": outcome,
                                "detail": (str(detail)[:400] if detail else None)}, ensure_ascii=False) + "\n")
    except OSError:
        pass


_PLANS = {}
_PLANS_MAX = 3000


def _plan_key(question):
    return (datetime.date.today().isoformat(), re.sub(r"\s+", " ", (question or "").strip().lower()).strip(" ?.!"))


OTHER_MARKET_PREFIX = "This box covers US-listed companies only."
OTHER_MARKETS = [
    (r"\bjapan(ese)?\b|\btokyo\b|\bnikkei\b|\btopix\b", "Japanese companies are under the Japan tab (Companies, Banks and the screener there)"),
    (r"\bhong\s*kong\b|\bhang\s*seng\b|\bhk\b", "Hong Kong is under the Asia tab"),
]


def other_market(question):
    """A pointer when the question names a market this box does not cover, else None."""
    ql = (question or "").lower()
    for pat, where in OTHER_MARKETS:
        if re.search(pat, ql):
            return (OTHER_MARKET_PREFIX + " %s. A Japanese or Hong Kong company appears here only if it "
                    "is also listed in the US (its depositary shares)." % where)
    return None


def answer(cur, question):
    """Answer by steps, or raise StepsError (the model is off or failed) or
    HTTPException (it says the data cannot answer)."""
    from . import stock_recipes
    if not sec_api._has(cur, "us_px_daily"):
        raise StepsError("no prices")
    last = us_prices_api.latest_date(cur)
    messages = [{"role": "system", "content": prompt(question, last)},
                {"role": "user", "content": (question or "").strip()[:300]}]
    plan, rows, cols, start, end, headline = None, None, None, None, None, None
    findings, verdict, reviewed = [], None, False
    looks, seen = [], []
    cached = _PLANS.get(_plan_key(question))
    # Plan, run, check, revise: a result that comes back is checked the way
    # an analyst reads a printout before quoting it (app/us_checks.py), and
    # the model is sent back with what to change, at most twice. The review
    # call, when the code checks pass, asks once whether the headline answers
    # the question that was asked.
    for attempt in (1, 2, 3):
        if cached and attempt == 1:
            plan, raw = dict(cached), json.dumps(cached)      # the same question today: the same steps, fresh numbers
        else:
            if attempt == 1:
                ask_progress.say("Writing the steps to answer it…")
            plan, raw = _ask_model(messages)
            while plan.get("look") and not plan.get("sql") and len(looks) < LOOKS_MAX:
                ask_progress.say("Looking at the data first (%d of at most %d)…" % (len(looks) + 1, LOOKS_MAX))
                told, saw, rec = _look(cur, plan)
                seen += saw
                looks.append(rec)
                if len(looks) == LOOKS_MAX:
                    told += " That was your last look: reply with the final sql now."
                messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": told}]
                plan, raw = _ask_model(messages)
        if plan.get("cannot") and not plan.get("sql"):
            from . import ask_miss
            log(question, "cannot", plan["cannot"])
            if str(plan["cannot"]).strip().lower().strip(".") == "unclear":
                raise ask_miss.miss(ask_miss.UNCLEAR, "unclear")
            # the questions offered instead are the fixed ones in app/ask_miss.py: a model's own suggestion
            # was found not to answer ("Toyota's reported revenue from India, if available")
            raise ask_miss.miss(str(plan["cannot"])[:400], "not_held")
        start, end = _period(plan)
        todo = [str(x) for x in (plan.get("steps") or []) if x]
        if todo:
            ask_progress.say("Running %s: %s" % ("the steps" if len(todo) > 1 else "it",
                                                 " ".join("%d. %s" % (i + 1, x) for i, x in enumerate(todo))[:320]))
        try:
            copied = copied_figures(plan.get("sql"), seen, question)
            if copied:
                raise StepsError("The query writes %s, a figure from what you looked at. Work it out inside the query "
                                 "instead." % ", ".join(copied[:3]))
            rows, cols = run(cur, plan.get("sql"), start, end)
        except StepsError as exc:
            if attempt == 3:
                log(question, "query failed", "%s | %s" % (exc, plan.get("sql")))
                raise
            ask_progress.say("That attempt did not run; fixing it and trying again…")
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "%s Fix it and reply with the whole JSON object again." % exc}]
            continue
        if not rows and attempt == 1:
            ask_progress.say("That found nothing; checking the filters and trying again…")
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "The query returned no rows. Check each filter against the table "
                                                      "descriptions (industry titles are SEC capitals, matched with ILIKE; "
                                                      "blank figures are left out). If no stock truly matches, reply with "
                                                      "the same JSON again."}]
            continue
        words = period_words(start, end, last=last)
        headline = fill_headline(plan.get("headline"), cols, rows[0], words, question, plan.get("sql") or "") if rows else None
        if not checks_on():
            break
        findings = us_checks.check(question, plan, rows, cols, headline) if rows else []
        refused = [f for f in findings if f["severity"] == us_checks.REFUSE]
        if refused:
            log(question, "refused", refused[0]["check"])
            raise HTTPException(422, refused[0]["note"])
        fix = us_checks.to_revise(findings)
        if fix and attempt < 3:
            log(question, "revised", "; ".join(f["check"] for f in findings if f["severity"] == us_checks.REVISE))
            first = next((f for f in findings if f["severity"] == us_checks.REVISE), None)
            ask_progress.say("A check sent it back%s; redoing it…" % ((": " + str(first["note"]).rstrip(".")[:200]) if first and first.get("note") else ""))
            messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": fix}]
            continue
        if rows and not fix and not reviewed:
            reviewed = True
            ask_progress.say("Checking the answer matches the question…")
            verdict = us_checks.review(question, plan, headline, rows, cols)
            if verdict["verdict"] == "revise" and verdict.get("reason") and attempt < 3:
                log(question, "revised by review", verdict["reason"])
                ask_progress.say("The check asked for a change: %s. Redoing it…" % str(verdict["reason"]).rstrip(".")[:200])
                messages += [{"role": "assistant", "content": raw},
                             {"role": "user", "content": "A reviewer read your answer: %s Redo the steps and reply with the "
                                                          "whole JSON object again." % verdict["reason"]}]
                continue
        break
    if rows or attempt == 3:
        if len(_PLANS) >= _PLANS_MAX:
            _PLANS.clear()
        _PLANS[_plan_key(question)] = {k: plan.get(k) for k in ("steps", "period", "sql", "headline", "chart", "cannot")}
    words = period_words(start, end, last=last)
    steps = [str(s) for s in (plan.get("steps") or []) if s][:6]
    row0 = rows[0] if rows else None
    if not rows:
        headline = "No stock matches that %s." % words if "change_period" in (plan.get("sql") or "") else "No stock matches that."
    elif headline is None:
        if len(rows) == 1 and len(cols) <= 3:
            headline = "; ".join("%s: %s" % (label(c), fmt_value(c, v)) for c, v in zip(cols, row0)) + "."
        else:
            headline = "%d results %s." % (len(rows), words) if "change_period" in (plan.get("sql") or "") else "%d results." % len(rows)
    figures, table, chart = [], None, None
    as_of = _d(last)
    if len(rows) == 1 and len(cols) <= 4:
        figures = [stock_recipes.fig(re.sub(r" (%|\(pp\)|\(USD\)|\(\u00d7\))$", "", label(c)), v,
                                     _unit(c) or ("count" if isinstance(v, int) else ""), as_of)
                   for c, v in zip(cols, row0) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if rows and (len(rows) > 1 or len(cols) > 4):
        # a count the headline already gives, repeated on every row (beat, months), is left out of the table
        named = set(re.findall(r"\{([^}]*)\}", plan.get("headline") or ""))
        keep = [i for i, c in enumerate(cols)
                if not (len(rows) > 1 and c in named and len({json.dumps(r[i], default=str) for r in rows}) == 1)] or list(range(len(cols)))
        table = {"columns": [label(cols[i]) for i in keep], "rows": [[r[i] for i in keep] for r in rows]}
    ch = plan.get("chart") or {}
    if isinstance(ch, dict) and ch.get("label") in cols and ch.get("value") in cols and len(rows) >= 2:
        li, vi = cols.index(ch["label"]), cols.index(ch["value"])
        pts = [[str(r[li]), r[vi]] for r in rows[:30] if isinstance(r[vi], (int, float))]
        if len(pts) >= 2:
            chart = {"kind": "bars", "title": "%s by %s" % (label(ch["value"]).replace(" %", "").replace(" (USD)", ""),
                                                             label(ch["label"]).lower()),
                     "unit": _unit(ch["value"]), "series": [{"name": label(ch["value"]), "points": pts}]}
    sql = plan.get("sql") or ""
    # what the checks and the reviewer want the reader told, first
    notes = us_checks.notes(findings)
    if verdict and verdict.get("verdict") == "note" and verdict.get("reason"):
        notes.append(verdict["reason"])
    if re.search(r"change_(period|ytd|1y)", sql):
        notes.append("Price changes run from the first to the last close in the period, adjusted for splits, before dividends.")
    if "sector" in sql:
        notes.append("Sectors are approximate, grouped from the SEC's industry codes, not the index providers' sectors.")
    if "ticker" in cols and rows:
        # figures in this result that rest on a doubtful count or an unconfirmed split say so
        ti = cols.index("ticker")
        care = stock_recipes.cautions_for(cur, [r[ti] for r in rows[:50]])
        if care:
            notes.append(care)
    srcs = [stock_recipes.PRICE_SOURCE, stock_recipes.SEC_SOURCE]
    # each extra table the query read names its own source
    for name, src in us_tables.sources().items():
        if re.search(r"\b%s\b" % name, sql) and src not in srcs:
            srcs.insert(0, src)
    for key, word in (("sp500", "in_sp500"), ("dow", "in_dow")):
        if word in sql or ("'%s'" % key) in sql:
            _, _, mas = us_benchmarks.members(cur, key)
            b = us_benchmarks.BENCHMARKS[key]
            if mas:
                srcs.append({"name": "%s members" % b["name"],
                             "credit": "Holdings of the %s fund (State Street), %s." % (b["etf"], stock_recipes.date_words(mas))})
    calc = {"steps": " ".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps)),
            "query": sql, "period": "%s (%s to %s)" % (words, _d(start), _d(end or last))}
    out = stock_recipes.pkg("steps", question, headline, "", figures, table, chart, srcs, calc,
                            {"steps": steps, "query": sql, "period": {"from": _d(start), "to": _d(end)}}, {}, notes)
    out["steps"] = steps
    out["query"] = sql
    out["looks"] = looks          # the exploratory queries run first, shown so the reader sees every query
    if re.search(r"change_(period|ytd|1y)", sql):
        out["period"] = stock_recipes.period_span(start, end or last)
    out["checks"] = [{"check": f["check"], "severity": f["severity"], "note": f["note"]} for f in findings]
    out["review"] = verdict
    out["match"] = {"recipe": "steps", "label": "Worked out step by step", "score": None, "alternatives": [], "via": "steps"}
    log(question, "answered by steps", sql if not findings else
        "%s | checks: %s" % (sql, "; ".join("%s (%s)" % (f["check"], f["severity"]) for f in findings)))
    return out


def checks_on():
    """The result checks and the review pass; US_ASK_CHECKS=0 switches them
    off (to measure the box without them, or in an emergency)."""
    return os.environ.get("US_ASK_CHECKS", "").strip() != "0"


def _d(v):
    return v.isoformat()[:10] if isinstance(v, (datetime.date, datetime.datetime)) else v
