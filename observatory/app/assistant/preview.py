# -*- coding: utf-8 -*-
"""The front page's preview: a typed question in, the start of its answer out.

GET /api/v1/assistant/preview?q=...   — public, no account, no AI of ours.

The front page (web/index.html) shows a visitor the headline of the answer and
two figures; the full answer (chart, table, sources, follow-ups) is one sign-up
away. Two routes, tried in this order:

  1. US stocks: the US stock box (app/stock_recipes.answer, as served at
     /api/v1/us/ask). The full answer is the home page, /?q=..., signed in.
  2. US economy: the series a question names, found by words
     (app/analysis.suggest_series, no model), US datasets only, then one
     ready-made analysis run by code (app/recipes): a price index as its
     year-on-year rate, anything else as its latest reading. The full answer
     is the dataset's own page.

Nothing here computes a figure the pages do not: the stock answer is passed
through, and the one calculated rate (year on year) carries its formula. A
question neither route can answer says so, with what Plover does hold.
"""
import datetime
import re

from fastapi import APIRouter, HTTPException, Query

from .. import analysis, recipes, registry

router = APIRouter(prefix="/preview", tags=["assistant"])

# The US economy datasets the preview may answer from: the US pages' series,
# not the stock-price feed (us-prices), which the stock route covers.
US_MACRO = ("cpi-us", "cpi-us-sa", "cpi-us-areas", "us-avg-prices", "us-wages", "us-wage-tracker",
            "ust-yields", "ust-real-yields")

# A question must name a topic Plover holds, and the series must come from a
# dataset for that topic: without this, "US unemployment rate" (not held) was
# answered with the 10-year yield, the nearest series by words.
TOPICS = (
    (("inflation", "cpi", "consumer price", "consumer prices", "prices", "price index", "deflation"),
     ("cpi-us", "cpi-us-sa", "cpi-us-areas", "us-avg-prices")),
    (("yield", "yields", "treasury", "treasuries", "bond", "bonds", "tips", "yield curve"),
     ("ust-yields", "ust-real-yields")),
    (("wage", "wages", "pay", "earnings", "salary", "salaries", "hourly"),
     ("us-wages", "us-wage-tracker")),
)


def _allowed(q):
    ql = " " + re.sub(r"[^a-z0-9 ]+", " ", q.lower()) + " "
    out = set()
    for words, datasets in TOPICS:
        if any(" " + w + " " in ql for w in words):
            out.update(datasets)
    return out


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

HOLDS = ("Plover answers questions about US-listed companies (owners, earnings, buybacks, insider "
         "trades, prices and valuations) and US inflation, wages and Treasury yields.")


def _num(v, dp=1):
    s = ("%." + str(dp) + "f") % abs(v)
    return ("−" + s) if v < 0 and float(s) != 0 else s


def _when(iso, daily):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    if daily:
        return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)
    return "%s %d" % (MONTHS[d.month - 1], d.year)


def _first_sentence(text):
    m = re.match(r"(.+?[.!?])(\s|$)", (text or "").strip(), re.S)
    return (m.group(1) if m else (text or "")).strip()


def _stock(q, prev=None):
    """The stock box's answer as a preview, or None when it does not answer.
    `prev` is the reader's previous question, so a follow-up reads whole."""
    from .. import sec_api, stock_recipes
    try:
        cur = sec_api._require()
        a = stock_recipes.answer(cur, q, prev)
    except HTTPException as exc:
        return None, str(exc.detail).strip('"')
    except Exception:  # noqa: BLE001 — no SEC database here: try the next route
        return None, None
    if not a or not a.get("headline"):
        return None, None
    figs = []
    for f in (a.get("figures") or [])[:2]:
        figs.append({"label": f.get("label"), "value": f.get("value"), "unit": f.get("unit"),
                     "as_of": f.get("as_of")})
    more = []
    if a.get("chart"):
        more.append("The chart: " + (a["chart"].get("title") or "the full picture"))
    rows = len((a.get("table") or {}).get("rows") or [])
    if rows:
        more.append("The table: %d rows to scan and download" % rows)
    more.append("Every source, and how each figure was worked out")
    more.append("Follow-up questions on the same answer")
    return {"kind": "stock", "read_as": (a.get("match") or {}).get("read_as") or "",
            "headline": a["headline"], "lead": _first_sentence(a.get("answer")),
            "figures": figs, "sources": [s.get("credit") for s in a.get("sources") or [] if s.get("credit")],
            "calc": None, "more": more, "period": a.get("period"),
            # the question as the box read it (a follow-up made whole), for the next follow-up
            "question": a.get("question") or q,
            # the full answer is the home page itself, signed in (us-stocks.html redirects there)
            "full_url": "/?q=" + _quote(a.get("question") or q)}, None


def _quote(q):
    import urllib.parse
    return urllib.parse.quote(q, safe="")


def _dataset_name(ds):
    try:
        m = registry.get(ds)
        name = (m.get("name") or {}).get("en") if isinstance(m, dict) else None
        page = m.get("page") if isinstance(m, dict) else None
    except Exception:  # noqa: BLE001
        name, page = None, None
    return (name or ds).split(" — ")[0], (page or "/").lstrip("/")


def _macro(q):
    """A US economy series the question names, as a preview, or None."""
    try:
        hits = analysis.suggest_series(q)
    except Exception:  # noqa: BLE001
        return None
    allowed = _allowed(q) & set(US_MACRO)
    hits = [h for h in hits if h.get("dataset") in allowed]
    if not hits:
        return None
    h = hits[0]
    ref = "%s:%s" % (h["dataset"], h["code"])
    index = (h.get("unit") or "").lower() == "index"
    try:
        spec = recipes.build("growth" if index else "latest", {"a": ref}, "", "",
                             {"measure": "yoy"} if index else {}, None, "")
        res = analysis.execute(spec)
    except (recipes.RecipeError, analysis.AnalysisError):
        return None
    rows = [r for r in res.get("rows") or [] if r[1] is not None]
    if not rows:
        return None
    src = (res.get("sources") or [{}])[0]
    title = src.get("title") or h.get("name") or h["code"]
    unit = src.get("unit") or h.get("unit") or ""
    name, page = _dataset_name(h["dataset"])
    last_date, last = rows[-1][0], rows[-1][1]
    daily = len(rows) > 2 and str(rows[-1][0])[:7] == str(rows[-2][0])[:7]
    when = _when(last_date, daily)
    pct = unit in ("%", "percent", "pct") or index
    dp = 1 if index else min(2, len(("%.4f" % last).rstrip("0").split(".")[1]))
    shown = _num(last, dp) + ("%" if pct else (" " + unit if unit else ""))
    if index:
        headline = "%s, %s: %s on a year earlier in %s." % (
            name, title, ("up " if last > 0 else "down " if last < 0 else "") + _num(last, 1) + "%", when)
        calc = "Year on year = (index this period ÷ index a year earlier − 1) × 100, " \
               "from the published index; it can differ from the published rate by 0.1 pp."
    else:
        headline = "%s: %s on %s." % (title, shown, when) if daily else "%s: %s in %s." % (title, shown, when)
        calc = None
    # the figure carries the headline's own rounding, so the two never disagree (2.449: 2.4 in both)
    fig_dp = 1 if index else dp
    figs = [{"label": "Year on year" if index else "Latest", "value": round(last, fig_dp),
             "unit": "%" if pct else unit, "as_of": str(last_date)[:10], "as_of_label": when}]
    try:
        d = datetime.date.fromisoformat(str(last_date)[:10])
        year_ago = d.replace(year=d.year - 1).isoformat()
        prior = [r for r in rows if str(r[0])[:10] <= year_ago]
        if prior:
            figs.append({"label": "A year earlier", "value": round(prior[-1][1], fig_dp),
                         "unit": "%" if pct else unit, "as_of": str(prior[-1][0])[:10],
                         "as_of_label": _when(prior[-1][0], daily)})
    except ValueError:
        pass
    return {"kind": "macro", "read_as": "%s — %s" % (name, title), "headline": headline,
            "lead": "", "figures": figs, "sources": [c for c in [src.get("credit"), src.get("release")] if c],
            "calc": calc,
            "more": ["The chart of the whole history", "Every period in a table to download",
                     "The release it came from, and how each figure was worked out"],
            "full_url": page or "us-treasury.html"}


@router.get("")
def preview(q: str = Query(..., min_length=2, max_length=300),
            prev: str = Query("", max_length=300, description="the reader's previous question, for a follow-up")):
    """The start of an answer for the front page: headline, up to two figures,
    the source, and what the full answer adds."""
    q = " ".join(q.split())
    prev = " ".join(prev.split()) if isinstance(prev, str) else ""          # called directly, the default is a Query
    out, _refusal = _stock(q, prev or None)
    if out is None:
        out = _macro(q)
    from .. import ask_log
    if out is None:
        ask_log.note("/api/v1/assistant/preview", q, False)
        # the stock box's own refusal is worded for its page ("the tables ..."):
        # one plain sentence here, and what Plover does hold
        return {"kind": "none", "question": q, "message": "That is not in what Plover can answer yet.",
                "holds": HOLDS}
    ask_log.note("/api/v1/assistant/preview", q, True, out.get("kind"))
    out.setdefault("question", q)
    return out
