# -*- coding: utf-8 -*-
"""Ready-made analyses: the question shapes people ask most, as forms.

A recipe is one shape of question (the latest reading, a trend, the gap
between two rates, highest since …) with its blanks: which series, from when,
which measure. Filled in, it becomes an analysis spec (app/analysis.py) that
the server runs, so a person can answer most questions with pickers and no AI
at all, and a typed question costs one short AI call to match it to a recipe
(app/assistant/api.py, ask_study) instead of a loop that writes SQL.

Each recipe builds the same spec the Study Analyst would write by hand, so a
study made either way edits, re-runs and exports the same. Series are named
by their slot (a, b …) and the query refers to them by that name.
"""
import datetime
import re

from . import analysis

MEASURES = {
    "level": ("{x}", "as published"),
    "yoy": ("yoy({x})", "change on a year earlier, %"),
    "mom": ("mom({x})", "change on the month before, %"),
    "ann3m": ("ann3m({x})", "three-month change, annualised, %"),
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _years_ago(n):
    d = datetime.date.today()
    return "%d-%02d-01" % (d.year - n, d.month)


def _measure(opts, default="level"):
    m = (opts or {}).get("measure") or default
    return m if m in MEASURES else default


def _cols(names, measure):
    expr, _ = MEASURES[measure]
    return ", ".join("%s AS %s" % (expr.format(x=n), n) if measure != "level" else n for n in names)


RECIPES = [
    {"id": "latest", "label": "Latest reading", "asks": "What is core CPI now?",
     "slots": [("a", "Series")], "options": {"measure": ["level", "yoy", "mom"]},
     "default_start": lambda: _years_ago(5)},
    {"id": "trend", "label": "Trend over time", "asks": "Chart the 10-year JGB yield since 2015",
     "slots": [("a", "Series"), ("b", "Series (optional)"), ("c", "Series (optional)"), ("d", "Series (optional)")],
     "options": {"measure": ["level", "yoy", "mom", "ann3m"]}, "default_start": lambda: _years_ago(10)},
    {"id": "compare", "label": "Compare two series", "asks": "Japan vs US core inflation",
     "slots": [("a", "First series"), ("b", "Second series")],
     "options": {"measure": ["level", "yoy", "mom"]}, "default_start": lambda: _years_ago(10)},
    {"id": "gap", "label": "Gap between two rates", "asks": "How far apart are US and Japanese 10-year yields?",
     "slots": [("a", "First rate"), ("b", "Second rate")], "options": {"measure": ["level", "yoy"]},
     "default_start": lambda: _years_ago(6)},
    {"id": "growth", "label": "Growth rate", "asks": "Export growth, year on year",
     "slots": [("a", "Series")], "options": {"measure": ["yoy", "mom", "ann3m"]},
     "default_start": lambda: _years_ago(10)},
    {"id": "since", "label": "Highest or lowest since", "asks": "When was the JGB yield last this high?",
     "slots": [("a", "Series")], "options": {"measure": ["level", "yoy"]}, "default_start": None},
    {"id": "rebase", "label": "Relative performance", "asks": "Visitor arrivals from China, Korea and Taiwan, start = 100",
     "slots": [("a", "Series"), ("b", "Series"), ("c", "Series (optional)"), ("d", "Series (optional)")],
     "options": {}, "default_start": lambda: _years_ago(5)},
    {"id": "share", "label": "Share of a total", "asks": "China's share of visitor arrivals",
     "slots": [("a", "Part"), ("b", "Total")], "options": {}, "default_start": lambda: _years_ago(10)},
    {"id": "running_total", "label": "Running total this year", "asks": "Foreigners' net buying so far this year",
     "slots": [("a", "Flow (sum each period)"), ("b", "Subtract this flow (optional)")], "options": {},
     "default_start": lambda: "%d-01-01" % datetime.date.today().year},
    {"id": "rolling", "label": "Moving average", "asks": "Monthly CPI inflation, three-month average",
     "slots": [("a", "Series")], "options": {"measure": ["level", "yoy", "mom"], "periods": "number"},
     "default_start": lambda: _years_ago(5)},
    {"id": "change", "label": "Change over a period", "asks": "How much has the yield moved in 12 months?",
     "slots": [("a", "Series")], "options": {"periods": "number", "unit": ["pp", "%"]},
     "default_start": lambda: _years_ago(5)},
]
BY_ID = dict((r["id"], r) for r in RECIPES)


def catalogue():
    """The recipes as the page and the matcher see them: no functions."""
    out = []
    for r in RECIPES:
        out.append({"id": r["id"], "label": r["label"], "asks": r["asks"],
                    "slots": [{"name": n, "label": l, "optional": "optional" in l.lower()} for n, l in r["slots"]],
                    "options": r["options"]})
    return out


class RecipeError(ValueError):
    pass


def build(recipe_id, series, start=None, end=None, options=None, monthly=None, title=None):
    """The analysis spec for one filled-in recipe. ``series`` maps slot names
    (a, b …) to dataset:code."""
    r = BY_ID.get(recipe_id)
    if r is None:
        raise RecipeError("No ready-made analysis called %r. One of: %s." % (recipe_id, ", ".join(BY_ID)))
    series = dict((k, v.strip()) for k, v in (series or {}).items() if isinstance(v, str) and v.strip())
    needed = [n for n, l in r["slots"] if "optional" not in l.lower()]
    for n in needed:
        if n not in series:
            raise RecipeError("'%s' needs a series for %s." % (r["label"], dict(r["slots"])[n].lower()))
    names = [n for n, _ in r["slots"] if n in series]
    opts = options or {}
    m = _measure(opts, (r["options"].get("measure") or ["level"])[0])
    unit_pct = m != "level"
    spec = {"series": dict((n, series[n]) for n in names)}
    if start and _DATE.match(str(start)):
        spec["start"] = str(start)
    elif r["default_start"]:
        spec["start"] = r["default_start"]()
    if end and _DATE.match(str(end)):
        spec["end"] = str(end)
    if monthly in ("average", "end", "sum"):
        spec["monthly"] = monthly
    rid = r["id"]
    label = lambda n: n   # noqa: E731 — slot names stay the column names
    if rid in ("latest", "trend", "compare"):
        spec["query"] = "SELECT date, %s FROM data" % _cols(names, m)
        spec["summary"] = [{"column": n, "stats": ["latest"] + (["highest_since", "lowest_since"] if rid == "latest" else [])} for n in names]
        spec["chart"] = {"columns": names, "unit": "%" if unit_pct else ""}
    elif rid == "gap":
        a, b = MEASURES[m][0].format(x="a"), MEASURES[m][0].format(x="b")
        spec["query"] = "SELECT date, %s AS a, %s AS b, spread_pp(%s, %s) AS gap FROM data" % (a, b, a, b) \
            if m != "level" else "SELECT date, a, b, spread_pp(a, b) AS gap FROM data"
        spec["summary"] = [{"column": "gap", "stats": ["latest", "min", "max", "mean", "highest_since", "lowest_since"]},
                           {"column": "a", "stats": ["latest"]}, {"column": "b", "stats": ["latest"]}]
        spec["chart"] = {"columns": ["a", "b", "gap"], "unit": "%" if unit_pct else ""}
    elif rid == "growth":
        spec["query"] = "SELECT date, %s AS growth FROM data" % MEASURES[m][0].format(x="a")
        spec["summary"] = [{"column": "growth", "stats": ["latest", "max", "min", "highest_since"]}]
        spec["chart"] = {"columns": ["growth"], "unit": "%"}
    elif rid == "since":
        col = "a" if m == "level" else "rate"
        spec["query"] = "SELECT date, a FROM data" if m == "level" else \
            "SELECT date, %s AS rate FROM data QUALIFY rate IS NOT NULL" % MEASURES[m][0].format(x="a")
        spec["summary"] = [{"column": col, "stats": ["latest", "highest_since", "lowest_since", "max", "min"]}]
        spec["chart"] = {"columns": [col], "unit": "%" if unit_pct else ""}
    elif rid == "rebase":
        base = spec.get("start") or _years_ago(5)
        spec["start"] = base
        spec["query"] = "SELECT date, %s FROM data" % ", ".join("rebase(%s, '%s') AS %s" % (n, base, n) for n in names)
        spec["summary"] = [{"column": n, "stats": ["latest"]} for n in names]
        spec["chart"] = {"columns": names, "unit": "start = 100"}
    elif rid == "share":
        spec["query"] = "SELECT date, a, b, share(a, b) AS share FROM data"
        spec["summary"] = [{"column": "share", "stats": ["latest", "max", "min", "mean"]}]
        spec["chart"] = {"columns": ["share"], "unit": "%"}
    elif rid == "running_total":
        net = "a - b" if "b" in names else "a"
        spec["query"] = ("SELECT date, net, cumulative(net) AS running_total FROM "
                         "(SELECT date, %s AS net FROM data)" % net)
        spec["summary"] = [{"column": "running_total", "stats": ["latest"]}, {"column": "net", "stats": ["latest", "count"]}]
        spec["chart"] = {"columns": ["net", "running_total"]}
    elif rid == "rolling":
        n = _periods(opts, 3)
        x = MEASURES[m][0].format(x="a")
        spec["query"] = ("SELECT date, s AS a, rolling_mean(s, %d) AS average_%d FROM (SELECT date, %s AS s FROM data)"
                         % (n, n, x))
        spec["summary"] = [{"column": "average_%d" % n, "stats": ["latest"]}, {"column": "a", "stats": ["latest"]}]
        spec["chart"] = {"columns": ["a", "average_%d" % n], "unit": "%" if unit_pct else ""}
    elif rid == "change":
        n = _periods(opts, 12)
        if (opts.get("unit") or "pp") == "pp":
            spec["query"] = "SELECT date, a, diff_pp(a, %d) AS change_pp FROM data" % n
            col = "change_pp"
        else:
            spec["query"] = "SELECT date, a, pct_change(a, %d) AS change_pct FROM data" % n
            col = "change_pct"
        spec["summary"] = [{"column": col, "stats": ["latest", "max", "min"]}, {"column": "a", "stats": ["latest"]}]
        spec["chart"] = {"columns": [col], "unit": "pp" if col == "change_pp" else "%"}
    if title:
        spec["chart"]["title"] = str(title)[:140]
    spec["recipe"] = {"id": rid, "options": dict((k, v) for k, v in {
        "measure": opts.get("measure"), "periods": opts.get("periods"), "unit": opts.get("unit")}.items() if v)}
    return analysis.clean_spec(spec)


def _periods(opts, default):
    try:
        n = int(opts.get("periods") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 240))


def reads_as(recipe_id, series_titles, options=None):
    """One plain sentence saying what the recipe calculates."""
    r = BY_ID.get(recipe_id)
    if r is None:
        return ""
    m = _measure(options or {}, (r["options"].get("measure") or ["level"])[0])
    names = [series_titles.get(n) or n for n, _ in r["slots"] if n in series_titles]
    how = MEASURES[m][1]
    rid = r["id"]
    if rid == "gap":
        return "%s minus %s, in percentage points (%s)." % (names[0], names[1], how)
    if rid == "share":
        return "%s as a percentage of %s." % (names[0], names[1])
    if rid == "rebase":
        return "Each series rescaled so its first value is 100."
    if rid == "running_total":
        return "Each period's %s, added up from the start." % (" minus ".join(names))
    if rid == "rolling":
        return "%s, %s, and its %d-period average." % (names[0], how, _periods(options or {}, 3))
    if rid == "change":
        return "%s and its change over %d periods." % (names[0], _periods(options or {}, 12))
    if rid == "since":
        return "%s, %s: the last date it was at or beyond today's reading." % (names[0], how)
    return "%s, %s." % (", ".join(names), how)
