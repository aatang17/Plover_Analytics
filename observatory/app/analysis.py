# -*- coding: utf-8 -*-
"""run_analysis: the AI sends a short query, the server runs it on the data.

A model that reads every number of a long series pays for each one, on every
step of its run, and pays again to type a chart's rows back into a draft. This
tool turns that round: the model names the series and writes a few lines of
SQL, the server loads the series, runs the query, and sends back only a
preview and the summary figures asked for. A chart is drawn into the draft
from the full result by code, so no number of it passes through the model.
(docs/plans/PLAN-AI-DATA-WORKSPACE.md.)

The series are loaded through the same function that serves
/api/v1/{dataset}/observations, so a figure here is the figure on the page,
and into a fresh in-memory DuckDB that can reach no file, no network and no
other database: its configuration is locked before the query runs, and a
query may only read (one SELECT or WITH statement).

The query reads one table, ``data``: a ``date`` column and one column per
series, named as the call names them. Plain SQL works as usual; on top of it
are the helper words below, expanded into SQL before the query runs, each
with one fixed formula, so the same words always mean the same calculation:

  yoy(x)  mom(x)  ann3m(x)      change on 12 / 1 / 3 months earlier, % (ann3m
                                annualised); blank when that month is missing
  pct_change(x, n)              % change on n rows earlier
  change(x, n)  diff_pp(x, n)   difference from n rows earlier (diff_pp for
                                rates: the answer is in percentage points)
  spread_pp(a, b)  ratio(a, b)  share(part, total)
  rebase(x, 'YYYY-MM-DD')       that date = 100
  rolling_mean(x, n)  rolling_sum(x, n)   over the last n rows; blank unless
                                all n are present
  cumulative(x)                 running total, for flows

Missing stays missing: no helper fills a gap, a division by zero is blank,
and a query that turns blanks into zero (coalesce or ifnull with 0) is
refused. Summary figures (latest, highest since …) are worked out by code
over the full result.
"""
import datetime
import json
import math
import re
import threading

from . import api, registry
from .tools import call_api

SERIES_MAX = 6          # a chart's six colours
ROWS_MAX = 200000       # rows loaded into the table
RESULT_MAX = 50000      # rows a query may return
PREVIEW_ROWS = 20
CHART_ROWS_MAX = 3000   # research_doc.DATA_ROWS_MAX
SECONDS_MAX = 10
MEMORY = "256MB"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
STATS = ("latest", "min", "max", "mean", "median", "count", "highest_since", "lowest_since")


class AnalysisError(ValueError):
    pass


# --------------------------------------------------------------- the words

def _months(n):
    # the value exactly n months earlier: a RANGE frame of one point
    return ("max({x}) OVER (ORDER BY date RANGE BETWEEN INTERVAL %d MONTH PRECEDING "
            "AND INTERVAL %d MONTH PRECEDING)" % (n, n))


HELPERS = {
    # name: (argument count, SQL template)
    "yoy": (1, "(({x}) / nullif(" + _months(12) + ", 0) - 1) * 100"),
    "mom": (1, "(({x}) / nullif(" + _months(1) + ", 0) - 1) * 100"),
    "ann3m": (1, "(power(({x}) / nullif(" + _months(3) + ", 0), 4) - 1) * 100"),
    "pct_change": (2, "(({x}) / nullif(lag({x}, {n}) OVER (ORDER BY date), 0) - 1) * 100"),
    "change": (2, "(({x}) - lag({x}, {n}) OVER (ORDER BY date))"),
    "diff_pp": (2, "(({x}) - lag({x}, {n}) OVER (ORDER BY date))"),
    "spread_pp": (2, "(({x}) - ({n}))"),
    "ratio": (2, "(({x}) / nullif({n}, 0))"),
    "share": (2, "(({x}) / nullif({n}, 0) * 100)"),
    "rebase": (2, "(({x}) / nullif(max(CASE WHEN date = CAST({n} AS DATE) THEN {x} END) "
                  "OVER (), 0) * 100)"),
    "rolling_mean": (2, "(CASE WHEN count({x}) OVER (ORDER BY date ROWS BETWEEN {n} - 1 "
                        "PRECEDING AND CURRENT ROW) = {n} THEN avg({x}) OVER (ORDER BY date "
                        "ROWS BETWEEN {n} - 1 PRECEDING AND CURRENT ROW) END)"),
    "rolling_sum": (2, "(CASE WHEN count({x}) OVER (ORDER BY date ROWS BETWEEN {n} - 1 "
                       "PRECEDING AND CURRENT ROW) = {n} THEN sum({x}) OVER (ORDER BY date "
                       "ROWS BETWEEN {n} - 1 PRECEDING AND CURRENT ROW) END)"),
    "cumulative": (1, "sum({x}) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING "
                      "AND CURRENT ROW)"),
}
_WORD = re.compile(r"\b(%s)\s*\(" % "|".join(sorted(HELPERS, key=len, reverse=True)), re.I)


def _close(text, start):
    """Index of the bracket closing the one opened just before ``start``, and
    the top-level argument strings inside."""
    depth, quote, args, last = 1, None, [], start
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append(text[last:i])
                return i, [a.strip() for a in args]
        elif ch == "," and depth == 1:
            args.append(text[last:i])
            last = i + 1
        i += 1
    raise AnalysisError("A bracket is not closed in the query.")


def expand(sql):
    """The query with every helper word replaced by its SQL, innermost first."""
    out, pos = [], 0
    while True:
        m = _WORD.search(sql, pos)
        if not m:
            out.append(sql[pos:])
            return "".join(out)
        name = m.group(1).lower()
        end, args = _close(sql, m.end())
        want, template = HELPERS[name]
        if len(args) != want or not all(args):
            raise AnalysisError("%s takes %d argument%s." % (name, want, "" if want == 1 else "s"))
        args = [expand(a) for a in args]
        if want == 2 and name in ("pct_change", "change", "diff_pp", "rolling_mean", "rolling_sum") \
                and not re.match(r"^\d{1,4}$", args[1]):
            raise AnalysisError("%s needs a whole number of rows as its second argument." % name)
        out.append(sql[pos:m.start()])
        out.append(template.format(x=args[0], n=args[1] if want == 2 else ""))
        pos = end + 1


_FILL_ZERO = re.compile(r"\b(coalesce|ifnull|nvl)\s*\([^()]*,\s*0+(\.0*)?\s*\)", re.I)
_FIRST = re.compile(r"^\s*(select|with)\b", re.I)


def _check_query(sql):
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return "SELECT * FROM data ORDER BY date"
    if len(sql) > 4000:
        raise AnalysisError("The query is too long; keep it under 4,000 characters.")
    if not _FIRST.match(sql) or ";" in sql:
        raise AnalysisError("The query must be one SELECT (or WITH … SELECT) statement.")
    if _FILL_ZERO.search(sql):
        raise AnalysisError("A missing value cannot be turned into zero: missing is blank, "
                            "never 0. Leave the gap, or filter the rows out with WHERE.")
    return sql


# ------------------------------------------------------------ loading data

def _parse_series(series):
    if not isinstance(series, dict) or not series:
        raise AnalysisError("Name each series: {\"japan\": \"jgb-yields:10Y\", "
                            "\"us\": \"ust-yields:10Y\"}. Find codes with search.")
    if len(series) > SERIES_MAX:
        raise AnalysisError("Up to %d series in one analysis." % SERIES_MAX)
    out = []
    for name, ref in series.items():
        name = str(name).strip().lower()
        if not NAME.match(name) or name == "date":
            raise AnalysisError("Series names are short lowercase words (letters, digits, "
                                "underscore), and not 'date': %r." % name)
        ds, _, code = str(ref or "").partition(":")
        ds, code = ds.strip(), code.strip()
        if not ds or not code:
            raise AnalysisError("Give each series as dataset:code, e.g. cpi-jp:0001 (got %r)." % ref)
        m = registry.get(ds)
        if m is None:
            raise AnalysisError("Unknown dataset %r. Use list_datasets or search." % ds)
        if m.get("shape") != "series":
            raise AnalysisError("%s holds companies, not series; this tool reads series." % ds)
        if not registry.available(ds):
            raise AnalysisError("%s is not available on this server right now." % ds)
        out.append((name, ds, code, m))
    return out


def _month_key(period):
    return period[:7] + "-01"


def _to_monthly(points, rule):
    groups = {}
    for p, v in points:
        groups.setdefault(_month_key(p), []).append(v)
    out = []
    for k in sorted(groups):
        vals = [v for v in groups[k] if v is not None]
        if not vals:
            out.append((k, None))
        elif rule == "end":
            last = [v for v in groups[k]][-1]
            out.append((k, last))          # the month's last reading; blank if it is blank
        elif rule == "sum":
            out.append((k, None if len(vals) < len(groups[k]) else sum(vals)))
        else:
            out.append((k, sum(vals) / len(vals)))
    return out


def load(series, start=None, end=None, as_of=None, join="both", monthly=None):
    """(rows, columns, sources): one row per date, a column per series."""
    picked = _parse_series(series)
    if join not in ("both", "any"):
        raise AnalysisError("join is 'both' (dates every series has) or 'any'.")
    if monthly not in (None, "", "average", "end", "sum"):
        raise AnalysisError("monthly is 'average' (rates), 'end' (stocks) or 'sum' (flows).")
    by_name, sources = {}, []
    for name, ds, code, m in picked:
        try:
            raw = call_api(api.observations, dataset=ds, series=code, measure="index",
                           start=start or None, end=end or None, as_of=as_of or None)
        except Exception as exc:  # noqa: BLE001 — the API's own words
            detail = getattr(exc, "detail", None) or str(exc)
            raise AnalysisError("%s:%s could not be read: %s" % (ds, code, detail))
        s = (raw.get("series") or [None])[0]
        if not s:
            raise AnalysisError("%s has no series %r." % (ds, code))
        pts = [(str(p)[:10], v) for p, v in s["points"]]
        if monthly:
            pts = _to_monthly(pts, monthly)
        by_name[name] = dict(pts)
        rel = raw.get("release") or {}
        credit = ((m.get("source") or {}).get("credit") or "").strip()
        sources.append({"name": name, "series": "%s:%s" % (ds, code), "title": s.get("name_en"),
                        "unit": raw.get("unit"), "release": rel.get("label"),
                        "release_id": rel.get("release_id"), "credit": credit})
    names = [p[0] for p in picked]
    dates = set()
    for n in names:
        dates |= set(d for d, v in by_name[n].items())
    rows = []
    for d in sorted(dates):
        vals = [by_name[n].get(d) for n in names]
        if join == "both" and any(v is None for v in vals):
            continue
        if join == "any" and all(v is None for v in vals):
            continue
        rows.append([d] + vals)
    if len(rows) > ROWS_MAX:
        raise AnalysisError("That is %d rows; narrow the dates or use monthly." % len(rows))
    return rows, ["date"] + names, sources


# ---------------------------------------------------------------- running

def _connect(rows, columns):
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE data (date DATE, %s)" % ", ".join('"%s" DOUBLE' % c for c in columns[1:]))
    if rows:
        con.executemany("INSERT INTO data VALUES (%s)" % ", ".join("?" * len(columns)), rows)
    # nothing outside this table from here on, and the query cannot undo it
    con.execute("SET memory_limit='%s'" % MEMORY)
    con.execute("SET threads=2")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return con


def _plain(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:10]
    if isinstance(v, float):
        return v if math.isfinite(v) else None      # a division by zero is blank, not infinity
    if hasattr(v, "__float__") and not isinstance(v, (bool, int)):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


def run_query(rows, columns, sql):
    """(result rows, result columns) of the expanded query, within the limits."""
    sql = expand(_check_query(sql))
    con = _connect(rows, columns)
    timer = threading.Timer(SECONDS_MAX, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        out = cur.fetchmany(RESULT_MAX + 1)
    except Exception as exc:  # noqa: BLE001 — DuckDB's message is what the model needs
        msg = str(exc).split("\n")[0]
        if "interrupt" in msg.lower():
            msg = "The query took longer than %d seconds." % SECONDS_MAX
        elif "cannot be nested" in msg:
            msg = ("a helper that looks back in time cannot hold another one. Work it out in "
                   "two steps: WITH s AS (SELECT date, yoy(a) AS g FROM data) "
                   "SELECT date, diff_pp(g, 1) AS d FROM s")
        raise AnalysisError("The query failed: %s" % msg[:300])
    finally:
        timer.cancel()
        con.close()
    if len(out) > RESULT_MAX:
        raise AnalysisError("The query returns more than %d rows; aggregate or filter it." % RESULT_MAX)
    return [[_plain(v) for v in r] for r in out], cols


# --------------------------------------------------------------- summaries

def _numeric(rows, cols, column):
    if column not in cols:
        raise AnalysisError("No column %r in the result (it has %s)." % (column, ", ".join(cols)))
    i = cols.index(column)
    label = 0
    return [(r[label], r[i]) for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool)]


def _since(points, want):
    """The last earlier reading at or beyond the latest, as answers._since
    finds it: the latest value, and the date it was last matched (None when
    it never was in the result)."""
    if not points:
        return None
    lp, lv = points[-1]
    for i in range(len(points) - 2, -1, -1):
        p, v = points[i]
        if (v >= lv) if want == "highest" else (v <= lv):
            return {"latest": lv, "latest_at": lp, "since": p, "that_value": v,
                    "readings_between": len(points) - 2 - i}
    return {"latest": lv, "latest_at": lp, "since": None,
            "note": "the %s in the result, which begins %s" % (want, points[0][0])}


def _round(x):
    """Six decimals: enough for any published figure, without float noise."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return dict((k, _round(v)) for k, v in x.items())
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def summarise(rows, cols, asks):
    out = {}
    for ask in asks or []:
        if not isinstance(ask, dict):
            continue
        column = str(ask.get("column") or "")
        stats = ask.get("stats") or ["latest"]
        pts = _numeric(rows, cols, column)
        got = {}
        for st in stats:
            if st not in STATS:
                raise AnalysisError("Unknown summary %r; use %s." % (st, ", ".join(STATS)))
            vals = [v for _, v in pts]
            if st == "count":
                got[st] = len(vals)
            elif not pts:
                got[st] = None
            elif st == "latest":
                got[st] = {"value": pts[-1][1], "at": pts[-1][0]}
            elif st in ("min", "max"):
                p, v = (min if st == "min" else max)(pts, key=lambda x: x[1])
                got[st] = {"value": v, "at": p}
            elif st == "mean":
                got[st] = sum(vals) / len(vals)
            elif st == "median":
                s = sorted(vals)
                n = len(s)
                got[st] = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
            else:
                got[st] = _since(pts, "highest" if st == "highest_since" else "lowest")
        out[column] = got
    return out


# ------------------------------------------------------------------ charts

def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


def chart_markdown(rows, cols, chart, sources, sql):
    """The draft's Markdown for a chart of these columns: the chart marker,
    the table of numbers (written by code), its source line and the formula."""
    want = chart.get("columns") or cols[1:]
    if not isinstance(want, list) or not want:
        raise AnalysisError("chart.columns lists the result columns to draw.")
    if len(want) > SERIES_MAX:
        raise AnalysisError("A chart shows up to %d series." % SERIES_MAX)
    idx = []
    for c in want:
        if c not in cols:
            raise AnalysisError("No column %r to chart (the result has %s)." % (c, ", ".join(cols)))
        idx.append(cols.index(c))
    body = [r for r in rows if any(r[i] is not None for i in idx)]
    if len(body) > CHART_ROWS_MAX:
        raise AnalysisError("A chart holds up to %d points per series; that is %d. Use monthly "
                            "or a shorter period." % (CHART_ROWS_MAX, len(body)))
    kind = chart.get("kind") if chart.get("kind") in ("line", "bar") else "line"
    unit = str(chart.get("unit") or "")[:40]
    title = str(chart.get("title") or "")[:160].replace("]", ")")
    ref = "chart:%s" % kind + ("?unit=" + _quote(unit) if unit else "")
    lines = ["![%s](%s)" % (title, ref), "",
             "| %s | %s |" % (cols[0], " | ".join(cols[i] for i in idx)),
             "| --- |" + " --: |" * len(idx)]
    for r in body:
        lines.append("| %s | %s |" % (_cell(r[0]), " | ".join(_cell(r[i]) for i in idx)))
    credits = []
    for s in sources:
        c = re.sub(r"^\s*Source:\s*", "", s["credit"] or "").rstrip(".")
        if c and c not in credits:
            credits.append(c)
    lines += ["", "Source: %s. Calculated by Plover from the published series." % "; ".join(credits)
              if credits else "Source: Plover, calculated from the published series."]
    lines += ["", "Note: %s" % _formula(sources, sql)]
    return "\n".join(lines), len(body)


def _cellnum(v):
    """A number as the article stores it: up to twelve significant digits, so
    a float's noise (0.30000000000000004) is not frozen into the page."""
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return "%.12g" % v


def datachart_block(result, study_title="", ran_on=""):
    """A kept study run as an article's ``datachart`` block (research_doc):
    the columns the study charts, every row that has a value, frozen into the
    article. Published columns keep their publisher's names; anything
    calculated carries its inputs and query in the note, as the trust
    contract asks. Raises AnalysisError when the run cannot be a chart."""
    from . import research_doc
    ch = chart_spec(result)
    names = [s["name"] for s in ch["series"]]
    labels = [p[0] for p in ch["series"][0]["points"]] if ch["series"] else []
    body = []
    for i, label in enumerate(labels):
        vals = [s["points"][i][1] for s in ch["series"]]
        if all(v is None for v in vals):
            continue
        body.append([_cellnum(label)] + [_cellnum(v) for v in vals])
    if not body:
        raise AnalysisError("This study's latest run has no numbers to chart. Run it again in "
                            "Studies, then insert it.")
    if len(body) > research_doc.DATA_ROWS_MAX:
        raise AnalysisError("This study has %s rows; a chart in an article holds up to %s. In "
                            "Studies, make it monthly or shorten the period, then insert it again."
                            % ("{:,}".format(len(body)), "{:,}".format(research_doc.DATA_ROWS_MAX)))
    first = (result["columns"][0] if result["columns"] else "") or ""
    head = ["Date" if ch["axis"] == "time" else first[:1].upper() + first[1:]] + names
    derived = any(s["trust"] == "derived" for s in ch["series"])
    credit = (ch["sources"][0]["credit"] if ch["sources"] else "") or "Plover"
    source = credit + ("; calculated by Plover" if derived else "")
    origin = "From the study \u201c%s\u201d%s." % (study_title or ch["title"], ", run " + ran_on if ran_on else "")
    if derived:
        # the note holds 400 characters; the formula comes first, so the
        # study's name goes, then the release dates, before the formula is cut
        sql = re.sub(r"\s+", " ", result["spec"].get("query") or "SELECT * FROM data")
        short = "From a study%s." % (" run " + ran_on if ran_on else "")
        bare = "Inputs: %s. Calculation: %s" % ("; ".join(
            "%s = %s" % (x["name"], x.get("title") or x["series"]) for x in result["sources"]), sql)
        tries = [origin + " " + _formula(result["sources"], sql), short + " " + _formula(result["sources"], sql),
                 short + " " + bare]
        note = next((t for t in tries if len(t) <= research_doc.TEXT_MAX), None)
        if note is None:
            note = tries[-1][:research_doc.TEXT_MAX - 1] + "\u2026"
    else:
        note = origin + " Values as published: " + ch["sources"][0]["document"] if ch["sources"] else origin
    return {"type": "datachart", "rows": [head] + body, "kind": ch["kind"], "unit": ch["unit"],
            "title": ch["title"] or study_title, "source": source[:research_doc.TEXT_MAX],
            "note": note[:research_doc.TEXT_MAX], "origin": "database"}


def _quote(s):
    from urllib.parse import quote
    return quote(s, safe="")


def _formula(sources, sql):
    ins = "; ".join("%s = %s (%s)" % (s["name"], s["title"] or s["series"], s["release"] or "latest")
                    for s in sources)
    return ("Inputs: %s. Calculation: %s" % (ins, re.sub(r"\s+", " ", sql or "SELECT * FROM data")))[:1200]


# -------------------------------------------------------------------- tool

TOOL = {
    "name": "run_analysis",
    "description": (
        "Work on data without reading it: name up to six series, write one SQL query over them, "
        "and get back a short preview and the summary figures you ask for. The server loads the "
        "full history (no point limit) and runs the query; a chart is drawn into the draft from "
        "the full result by code, so never type a chart's numbers yourself. Use this for any "
        "comparison, gap, growth rate, 'highest since', or chart that combines series or "
        "datasets. The query reads table `data` (column `date` plus one column per series, "
        "named as you named them). Besides SQL you may use: yoy(x), mom(x), ann3m(x), "
        "pct_change(x, n), change(x, n), diff_pp(x, n), spread_pp(a, b), ratio(a, b), "
        "share(part, total), rebase(x, 'YYYY-MM-DD'), rolling_mean(x, n), rolling_sum(x, n), "
        "cumulative(x). Missing values stay blank; never turn them into 0. Example: series "
        "{\"japan\": \"jgb-yields:10Y\", \"us\": \"ust-yields:10Y\"}, start '2020-01-01', query "
        "\"SELECT date, japan, us, spread_pp(us, japan) AS gap FROM data\", summary "
        "[{\"column\": \"gap\", \"stats\": [\"latest\", \"min\", \"max\"]}], chart "
        "{\"columns\": [\"japan\", \"us\"], \"title\": \"US and Japan 10-year yields\", \"unit\": \"%\"}."),
    "parameters": {"type": "object", "properties": {
        "series": {"type": "object", "additionalProperties": {"type": "string"},
                   "description": "name: dataset:code, up to six. Codes come from search."},
        "start": {"type": "string", "description": "First date, YYYY-MM-DD (optional)."},
        "end": {"type": "string", "description": "Last date, YYYY-MM-DD (optional)."},
        "as_of": {"type": "string", "description": "YYYY-MM-DD: the data as it stood that day."},
        "join": {"type": "string", "enum": ["both", "any"],
                 "description": "both (default): dates every series has; any: all dates, blanks kept."},
        "monthly": {"type": "string", "enum": ["average", "end", "sum"],
                    "description": "Convert to monthly first: average for rates and prices, end for "
                                   "stocks, sum for flows. Needed to combine daily with monthly data."},
        "query": {"type": "string", "description": "One SELECT over table data. Default: every row."},
        "summary": {"type": "array", "items": {"type": "object", "properties": {
            "column": {"type": "string"},
            "stats": {"type": "array", "items": {"type": "string", "enum": list(STATS)}}}},
            "description": "Figures worked out over the full result, by column."},
        "chart": {"type": "object", "properties": {
            "columns": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string"}, "unit": {"type": "string"},
            "kind": {"type": "string", "enum": ["line", "bar"]}},
            "description": "Draw these result columns into the draft as a chart."}},
        "required": ["series"]},
}


STUDY_TOOL_DESCRIPTION = (
    "Run one SQL query over up to six series on the server; you get back a preview and the "
    "summary figures you ask for, and the study keeps every row and draws the chart. Table "
    "`data`: column `date` plus one column per series, named as you name them. Helpers: "
    "yoy(x), mom(x), ann3m(x), pct_change(x, n), change(x, n), diff_pp(x, n), spread_pp(a, b), "
    "ratio(a, b), share(part, total), rebase(x, 'YYYY-MM-DD'), rolling_mean(x, n), "
    "rolling_sum(x, n), cumulative(x). Missing stays blank, never 0. Daily with monthly data "
    "needs `monthly`. summary stats: " + ", ".join(STATS) + ".")


def tool_for(where):
    """TOOL worded for where its chart goes: the Writer Desk draws it into the
    draft, a study keeps every row and draws it on the study. The study's copy
    is the short one: it is sent on every step of a run."""
    if where != "study":
        return TOOL
    t = json.loads(json.dumps(TOOL))
    t["description"] = STUDY_TOOL_DESCRIPTION
    p = t["parameters"]["properties"]
    p["series"]["description"] = "name: dataset:code, up to six."
    p["monthly"]["description"] = "average for rates and prices, end for stocks, sum for flows."
    p["summary"]["description"] = "Figures over the full result, by column."
    p["chart"]["description"] = "Chart these result columns on the study."
    return t


# ------------------------------------------------------------ pre-search
# Before a question reaches the model, the server looks up the series it
# seems to name, so the model starts with codes in hand instead of spending a
# step (and a re-send of everything) on search.

_STOP = set("""how far apart are the and when was it its is of in on to a an at by for from with
since than that this these those last as now what which who why does do did have has had been
be were vs versus against between over under ago currently still per each every any all more
most less least much many very only also just about into out up down off again once here there
where both few some such no nor not own same so too can will should would could now today
year years month months week weeks day days came come moved move risen rise fallen fall grown
grow went gone got get been being compared compare""".split())
# words that name a measure or a time, not a series: in a name they prove little
_GENERIC = set("""share total value volume rate level index change growth last latest high low
highest lowest since data number net gross all average mean""".split())
_PLACE = {"us": "united states treasury", "u.s.": "united states treasury", "usa": "united states",
          "american": "united states", "america": "united states", "japan": "japan jgb",
          "japanese": "japan jgb", "japan's": "japan jgb", "hong": "hong kong", "kong": "hong kong",
          "hk": "hong kong", "tokyo": "tokyo", "china": "china", "chinese": "china"}
_MARKET = set("us u.s. usa american america japan japanese japan's hong kong hk".split())
_SINGULAR = {"yields": "yield", "rates": "rate", "prices": "price", "arrivals": "arrival",
             "holdings": "holding", "exports": "export", "imports": "import", "sales": "sale",
             "deposits": "deposit", "loans": "loan", "stocks": "stock"}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-'.]*", (text or "").lower())]


def _phrases(question):
    words = [w for w in _words(question)]
    out = []
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            ph = words[i:i + n]
            if ph[0] in _STOP or ph[-1] in _STOP:
                continue
            # a market name alone ("us", "japan vs us") names no series; a
            # country that is a series (visitors from China) is searched
            if all(w in _MARKET or w in _STOP or w in ("vs", "versus", "v", "and", "or") for w in ph):
                continue
            if n == 1 and (len(ph[0]) < 4 or ph[0].isdigit()):
                continue
            out.append(" ".join(ph))
    return out


def suggest_series(question, cap=10, per_dataset=3):
    """The series a question seems to name: [{dataset, code, name, unit}],
    best first. A hint for the model, never the final word."""
    from . import registry, tools_v2
    content = set()
    for w in _words(question):
        if w in _STOP:
            continue
        content.update(_PLACE.get(w, _SINGULAR.get(w, w)).split())
    seen = {}
    for ph in _phrases(question):
        try:
            d = json.loads(tools_v2.search(ph, limit=8))
        except Exception:  # noqa: BLE001 — a hint, not an answer
            continue
        # a longer phrase is a surer match than one word
        phrase_score = 2 * len(ph.split())
        for s in d.get("series") or []:
            k = (s["dataset"], s["code"])
            if k in seen:
                continue
            m = registry.get(s["dataset"]) or {}
            name = m.get("name") or {}
            own = (s.get("name_en") or "").lower()
            about = " ".join([name.get("en", "") if isinstance(name, dict) else str(name),
                              str(m.get("summary") or ""), str(m.get("section") or ""),
                              s["dataset"].replace("-", " ")]).lower()
            # a word in the series' own name counts for three times one in its dataset's,
            # and a series the question names outright ("China") most of all
            score = phrase_score + sum(0 if w in _GENERIC else 3 if w in own else (1 if w in about else 0)
                                       for w in content)
            if own and (own == ph or own in content):
                score += 6
            seen[k] = (score, len(seen), {"dataset": s["dataset"], "code": s["code"],
                                          "name": s.get("name_en") or s["code"], "unit": s.get("unit")})
        if len(seen) >= cap * 4:
            break
    # A series named by one word ("China") can be buried under another
    # family's hits (every trade partner called China): look for the single
    # words again inside the datasets the question's other phrases pointed to.
    families = []
    for _, _, hit in sorted(seen.values(), key=lambda x: (-x[0], x[1])):
        if hit["dataset"] not in families:
            families.append(hit["dataset"])
    for ds in families[:4]:
        for ph in (p for p in _phrases(question) if " " not in p):
            try:
                d = json.loads(tools_v2.search(ph, dataset=ds, limit=4))
            except Exception:  # noqa: BLE001
                continue
            for s in d.get("series") or []:
                k = (s["dataset"], s["code"])
                if k in seen:
                    continue
                own = (s.get("name_en") or "").lower()
                score = 2 + sum(0 if w in _GENERIC else 3 if w in own else 0 for w in content)
                if own and (own == ph or own in content):
                    score += 6
                seen[k] = (score, len(seen), {"dataset": s["dataset"], "code": s["code"],
                                              "name": s.get("name_en") or s["code"], "unit": s.get("unit")})
    ranked = sorted(seen.values(), key=lambda x: (-x[0], x[1]))
    out, per = [], {}
    for score, _, hit in ranked:
        if score == 0 or per.get(hit["dataset"], 0) >= per_dataset:
            continue
        per[hit["dataset"]] = per.get(hit["dataset"], 0) + 1
        out.append(hit)
        if len(out) >= cap:
            break
    return out


def hints_text(hits):
    if not hits:
        return ""
    return "Likely series (from a search of your question; confirm with search if unsure): " + \
        "; ".join("%s:%s = %s%s" % (h["dataset"], h["code"], h["name"], " (%s)" % h["unit"] if h.get("unit") else "")
                  for h in hits)


# "recipe" records which ready-made analysis built the spec (app/recipes.py)
# and its settings; the engine ignores it, the page reopens the same pickers.
SPEC_KEYS = ("series", "start", "end", "as_of", "join", "monthly", "query", "summary", "chart", "recipe")


def clean_spec(args):
    """The parts of a call that define an analysis, and nothing else: what a
    study saves, and what a re-run repeats exactly."""
    args = args if isinstance(args, dict) else {}
    out = {}
    for k in SPEC_KEYS:
        v = args.get(k)
        if v in (None, "", [], {}):
            continue
        if k in ("start", "end", "as_of", "join", "monthly", "query"):
            v = str(v).strip()
            if not v:
                continue
        out[k] = v
    if not isinstance(out.get("series"), dict):
        raise AnalysisError("Name each series: {\"japan\": \"jgb-yields:10Y\"}. Find codes with search.")
    if "chart" in out and not isinstance(out["chart"], dict):
        raise AnalysisError("chart is an object: {\"columns\": [...], \"title\": ..., \"unit\": ...}.")
    if "summary" in out and not isinstance(out["summary"], list):
        raise AnalysisError("summary is a list: [{\"column\": \"gap\", \"stats\": [\"latest\"]}].")
    if "recipe" in out and not isinstance(out["recipe"], dict):
        del out["recipe"]
    return out


def execute(args):
    """One analysis in full: the spec it ran, every result row, its sources and
    the summary figures. Nothing is sent anywhere; callers decide what to keep."""
    spec = clean_spec(args)
    rows, columns, sources = load(spec.get("series"), spec.get("start"), spec.get("end"),
                                  spec.get("as_of"), spec.get("join") or "both", spec.get("monthly"))
    if not rows:
        raise AnalysisError("No dates in common for these series in that period. Try join 'any', "
                            "a longer period, or monthly.")
    res, cols = run_query(rows, columns, spec.get("query"))
    out = {"spec": spec, "columns": cols, "rows": res, "sources": sources,
           "ran_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}
    if spec.get("summary"):
        out["summary"] = _round(summarise(res, cols, spec["summary"]))
    if spec.get("chart"):
        chart_spec(out)              # a chart that cannot be drawn is refused now, not later
    return out


def brief(result):
    """What the model is shown of a result: its size, a preview and the figures
    asked for — never the full rows."""
    res = result["rows"]
    preview = res if len(res) <= PREVIEW_ROWS else res[:5] + [["…"]] + res[-(PREVIEW_ROWS - 6):]
    out = {"rows": len(res), "columns": result["columns"],
           "preview": [[round(v, 4) if isinstance(v, float) else v for v in r] for r in preview],
           "sources": [dict((k, s[k]) for k in ("name", "series", "title", "unit", "release"))
                       for s in result["sources"]]}
    if result.get("summary"):
        out["summary"] = result["summary"]
    return out


def _official(column, spec):
    """A column is the published value itself when it is an input series
    selected bare, not redefined under its own name."""
    names = set((spec.get("series") or {}).keys())
    if column not in names:
        return False
    sql = spec.get("query") or ""
    return not re.search(r"\bAS\s+\"?%s\"?(?![a-z0-9_])" % re.escape(column), sql, re.I)


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def chart_spec(result):
    """The result as the Assistant's chart (app/assistant/charts.py's shape):
    the columns asked for, every row, the sources and, for anything
    calculated, the query as its formula."""
    spec, cols, rows = result["spec"], result["columns"], result["rows"]
    ch = spec.get("chart") or {}
    numeric = [c for i, c in enumerate(cols) if i and any(isinstance(r[i], (int, float))
                                                            and not isinstance(r[i], bool) for r in rows)]
    want = ch.get("columns") or numeric[:SERIES_MAX]
    if not isinstance(want, list) or not want:
        raise AnalysisError("chart.columns lists the result columns to draw.")
    if len(want) > SERIES_MAX:
        raise AnalysisError("A chart shows up to %d series." % SERIES_MAX)
    for c in want:
        if c not in cols or cols.index(c) == 0:
            raise AnalysisError("No column %r to chart (the result has %s)." % (c, ", ".join(cols[1:])))
    kind = ch.get("kind") if ch.get("kind") in ("line", "bar") else "line"
    labels = [r[0] for r in rows]
    timed = bool(labels) and all(isinstance(x, str) and _DAY.match(x) for x in labels)
    if kind == "bar" and len(rows) > 40:
        raise AnalysisError("%d rows is too many bars; use a line, or fewer rows." % len(rows))
    units = sorted(set(s["unit"] for s in result["sources"] if s.get("unit")))
    unit = str(ch.get("unit") or (units[0] if len(units) == 1 and all(
        _official(c, spec) for c in want) else ""))[:40]
    formula = re.sub(r"\s+", " ", spec.get("query") or "SELECT * FROM data")
    credits = []
    for s in result["sources"]:
        c = re.sub(r"^\s*Source:\s*", "", s.get("credit") or "").rstrip(".")
        if c and c not in credits:
            credits.append(c)
    titles = dict((s["name"], s.get("title") or s["series"]) for s in result["sources"])
    series = []
    for c in want:
        i = cols.index(c)
        official = _official(c, spec)
        # a published series is named as its publisher names it, not by its slot
        series.append({"name": titles.get(c, c) if official else c, "unit": unit,
                       "trust": "official" if official else "derived",
                       "formula": None if official else formula, "source": 0,
                       "points": [[r[0], r[i] if isinstance(r[i], (int, float)) else None] for r in rows]})
    return {"title": str(ch.get("title") or ", ".join(want))[:140], "kind": kind,
            "axis": "time" if timed else "category", "unit": unit,
            "daily": timed and any(not x.endswith("-01") for x in labels), "fiscal": False,
            "series": series,
            "sources": [{"credit": "; ".join(credits), "document": "; ".join(
                "%s (%s)" % (s.get("title") or s["series"], s.get("release") or "latest")
                for s in result["sources"]), "vintage": None, "cite": None}]}


def run(args, add_chart=None):
    """The reply for one run_analysis call in the Writer Desk, as a dict.
    ``add_chart(markdown)`` puts a chart in the draft and returns an error
    string or None; without it a chart is refused."""
    args = dict(args) if isinstance(args, dict) else {}
    chart = args.pop("chart", None)
    if chart and not isinstance(chart, dict):
        raise AnalysisError("chart is an object: {\"columns\": [...], \"title\": ..., \"unit\": ...}.")
    if chart and add_chart is None:
        raise AnalysisError("This run cannot change the draft, so it cannot add a chart.")
    result = execute(args)
    out = brief(result)
    if chart:
        md, n = chart_markdown(result["rows"], result["columns"], chart, result["sources"],
                               result["spec"].get("query"))
        problem = add_chart(md)
        if problem:
            raise AnalysisError(problem)
        out["chart"] = ("Added to the end of the draft: %d points per series, with its source "
                        "line and formula. Do not type its numbers." % n)
    return out


def run_tool(args, add_chart=None):
    """(text, is_error) for the AI tools."""
    try:
        return json.dumps(run(args, add_chart), ensure_ascii=False, default=str), False
    except AnalysisError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False), True
