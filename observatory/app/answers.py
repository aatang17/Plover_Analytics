# -*- coding: utf-8 -*-
"""Answer pages: one permanent address per question people ask an assistant.

An AI answer engine reads a page as text and quotes the page whose first
lines state the answer plainly, dated and sourced. The data pages are built
for exploring; these are built for being quoted. Each is a static shell in
``web/`` (``japan-inflation-rate.html``) whose first paragraph, comparison
line, table and citation are written in by ``prerender`` at serve time from
the functions that serve ``/api/v1`` — so the quoted number is the number the
chart draws, and it changes when the ingest publishes a release, never by
hand. ``answer.js`` draws the chart and fills the same elements only when the
server left them empty.

A question is a registry entry here: which dataset and series, what kind of
figure it is (a rate of change, a stock level, a count, a yield, a growth
rate), and the nouns the sentence needs. Adding a question is an entry plus a
shell page; nothing else changes.

Every sentence follows the trust contract: an index level or a published
count is stated as published, a rate is calculated from published values and
the page says so under "Show calculation". Missing is a dash, never zero, and
a sentence that cannot be completed honestly is not written at all — the page
then serves without a lead and the script fills it, or it stays blank.
"""
import datetime
import logging
import threading

from fastapi import APIRouter, HTTPException

from .readable import DASH, MINUS, fmt, fmt_int

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# the questions
# ---------------------------------------------------------------------------
#
# kind:
#   rate    a percentage change of a published index, over twelve months
#   growth  a percentage change of a published level: annualised quarter on
#           quarter (measure ann3m on a quarterly series) with the YoY beside it
#   level   a stock in yen, stated at the end of the period
#   count   a published count of people or nights, in the period or on a date
#   yield   a percentage per year, daily, compared in percentage points
#   reading a rate or ratio the publisher states itself (the unemployment
#           rate, the jobs-to-applicants ratio, the published growth of
#           real wages), compared with the month before and a year earlier
#           in its own units. ``percent`` says whether it is a percentage;
#           ``change`` that it is the publisher's own change on a year
#           earlier, which the sentence words as higher or lower.
#
# subject       what the sentence is about ("Japan's consumer prices")
# series_name   what the figure is, for the clause after the number
# also          further series stated in a second sentence, for a rate
# window        rows in the table; the frequency's own default when absent

QUESTIONS = [
    {
        "page": "japan-inflation-rate.html",
        "question": "What is Japan's inflation rate?",
        "short": "Japan inflation rate",
        "description": "Japan's latest consumer price inflation: headline CPI, all items, "
                       "year over year, from the Statistics Bureau's monthly release, with "
                       "the core rates, the last twelve months and the series since 1970.",
        "dataset": "cpi-jp", "code": "0001", "kind": "rate",
        "subject": "Japan's consumer prices",
        "series_name": "the headline Consumer Price Index (all items) published by the Statistics Bureau of Japan",
        "also": [("0161", "core CPI (all items less fresh food)"),
                 ("0178", "core-core CPI (less fresh food and energy)")],
        "chart_title": "Headline CPI, Year over Year",
        "section": "macro", "nav_page": "inflation",
    },
    {
        "page": "japan-core-inflation-rate.html",
        "question": "What is Japan's core inflation rate?",
        "short": "Japan core inflation rate",
        "description": "Japan's core CPI — all items less fresh food, the Bank of Japan's "
                       "reference measure — year over year from the Statistics Bureau's "
                       "monthly release, with core-core and headline beside it.",
        "dataset": "cpi-jp", "code": "0161", "kind": "rate",
        "subject": "Japan's core consumer prices",
        "series_name": "the Consumer Price Index excluding fresh food published by the "
                       "Statistics Bureau of Japan, the measure the Bank of Japan calls core CPI",
        "also": [("0178", "core-core CPI (less fresh food and energy as well)"),
                 ("0001", "headline CPI (all items)")],
        "chart_title": "Core CPI (Less Fresh Food), Year over Year",
        "section": "macro", "nav_page": "inflation",
    },
    {
        "page": "tokyo-cpi.html",
        "question": "What is Tokyo's CPI inflation rate?",
        "short": "Tokyo CPI",
        "description": "Tokyo ward-area consumer price inflation, published about three "
                       "weeks before the national figure: headline and core CPI, year over "
                       "year, from the Statistics Bureau's advance release.",
        "dataset": "cpi-tokyo", "code": "0001", "kind": "rate",
        "subject": "Consumer prices in Tokyo's 23 wards",
        "series_name": "the Tokyo ward-area Consumer Price Index (all items), the advance "
                       "reading the Statistics Bureau of Japan publishes about three weeks ahead "
                       "of the national index",
        "also": [("0161", "core CPI (all items less fresh food)"),
                 ("0178", "core-core CPI (less fresh food and energy)")],
        "chart_title": "Tokyo Headline CPI, Year over Year",
        "section": "macro", "nav_page": "inflation",
    },
    {
        "page": "boj-jgb-holdings.html",
        "question": "How many Japanese government bonds does the Bank of Japan hold?",
        "short": "BOJ JGB holdings",
        "description": "The Bank of Japan's holdings of Japanese government bonds, in yen, "
                       "at the latest month-end from the BOJ's own accounts, with the change "
                       "on the month and on the year and the distance from the peak.",
        "dataset": "boj-assets", "code": "MA03021034S", "kind": "level",
        "subject": "The Bank of Japan's holdings of Japanese government bonds",
        "series_name": "the Bank of Japan's published accounts (the JGB line)",
        "column": "Holdings",
        "chart_title": "BOJ JGB Holdings, Month-End",
        "section": "macro", "nav_page": "boj",
    },
    {
        "page": "japan-gdp-growth.html",
        "question": "What is Japan's GDP growth rate?",
        "short": "Japan GDP growth",
        "description": "Japan's real GDP growth: the annualised quarter-on-quarter rate and "
                       "the year-on-year rate from the Cabinet Office's quarterly estimates, "
                       "seasonally adjusted, with the last eight quarters.",
        "dataset": "gdp-jp", "code": "gdp.real_sa", "kind": "growth",
        "subject": "Japan's economy",
        "series_name": "the Cabinet Office's quarterly estimate of real gross domestic product, "
                       "seasonally adjusted, at chained 2020 prices",
        "chart_title": "Real GDP, Annualised Quarter on Quarter",
        "section": "macro", "nav_page": "gdp",
    },
    {
        "page": "japan-foreign-visitors.html",
        "question": "How many foreign visitors came to Japan last month?",
        "short": "Japan foreign visitor arrivals",
        "description": "Foreign visitor arrivals to Japan in the latest month, from the "
                       "Japan National Tourism Organization's monthly estimate, with the "
                       "change on a year earlier and the last thirteen months.",
        "dataset": "jnto-visitors", "code": "total", "kind": "count",
        "subject": "foreign visitors arrived in Japan",
        "series_name": "the Japan National Tourism Organization's monthly estimate of "
                       "visitor arrivals",
        "column": "Arrivals",
        "chart_title": "Foreign Visitor Arrivals, Monthly",
        "section": "macro", "nav_page": "inbound",
    },
    {
        "page": "japan-hotel-guest-nights.html",
        "question": "How many nights did guests stay in Japanese hotels last month?",
        "short": "Japan guest nights",
        "description": "Guest nights at Japanese hotels, inns and other lodging in the latest "
                       "month, from the Japan Tourism Agency's Accommodation Survey, with the "
                       "change on a year earlier and the last thirteen months.",
        "dataset": "accommodation-jp", "code": "nights.jp", "kind": "count",
        "subject": "guest nights were spent in Japanese lodging",
        "series_name": "the Japan Tourism Agency's Accommodation Survey, all facility types, "
                       "Japanese and foreign guests together",
        "column": "Guest nights",
        # The Agency re-stratified the survey from January 2026; a change on
        # a year earlier across that point measures the method, not demand.
        "break": datetime.date(2026, 1, 1),
        "break_note": "The survey changed its sampling from January 2026, so a comparison "
                      "with a year earlier is not on the same basis and is not made here.",
        "chart_title": "Guest Nights, All Japan, Monthly",
        "section": "macro", "nav_page": "accommodation",
    },
    {
        "page": "japan-government-debt.html",
        "question": "How large is Japan's government debt?",
        "short": "Japan government debt",
        "description": "Japan's central government debt — bonds, financing bills and "
                       "borrowings — at the latest quarter-end from the Ministry of Finance, "
                       "with the change on the quarter and on the year.",
        "dataset": "govt-debt-jp", "code": "total", "kind": "level",
        "subject": "Japan's central government debt",
        "series_name": "the Ministry of Finance's quarterly statement of government bonds, "
                       "borrowings and financing bills outstanding",
        "column": "Debt outstanding",
        "chart_title": "Central Government Debt Outstanding, Quarter-End",
        "section": "macro", "nav_page": "fiscal",
    },
    {
        "page": "jgb-10-year-yield.html",
        "question": "What is the yield on Japan's 10-year government bond?",
        "short": "JGB 10-year yield",
        "description": "The 10-year Japanese government bond yield at the latest close, "
                       "from the Ministry of Finance's daily yield curve, with the change "
                       "on the day and on the year and the last ten trading days.",
        "dataset": "jgb-yields", "code": "10Y", "kind": "yield",
        "subject": "The yield on Japan's 10-year government bond",
        "series_name": "the Ministry of Finance's daily reference yield curve",
        "chart_title": "10-Year JGB Yield, Daily",
        "section": "macro", "nav_page": "rates",
    },
    {
        "page": "japan-unemployment-rate.html",
        "question": "What is Japan's unemployment rate?",
        "short": "Japan unemployment rate",
        "description": "Japan's unemployment rate in the latest month, seasonally adjusted, "
                       "from the Statistics Bureau's Labour Force Survey, with the month "
                       "before, a year earlier and the last thirteen months.",
        "dataset": "unemployment-jp", "code": "sa.unemployment_rate.total", "kind": "reading",
        "percent": True, "decimals": 1,
        "subject": "Japan's unemployment rate",
        "series_name": "the Statistics Bureau of Japan's Labour Force Survey, seasonally adjusted",
        "also": [("sa.unemployment_rate.male", "the rate for men", "Men"),
                 ("sa.unemployment_rate.female", "the rate for women", "Women")],
        "column": "Unemployment rate",
        "chart_title": "Unemployment Rate, Seasonally Adjusted, Monthly",
        "section": "macro", "nav_page": "labour",
    },
    {
        "page": "japan-jobs-to-applicants-ratio.html",
        "question": "What is Japan's jobs-to-applicants ratio?",
        "short": "Japan jobs-to-applicants ratio",
        "description": "Japan's active job openings-to-applicants ratio in the latest month, "
                       "seasonally adjusted, from the Ministry of Health, Labour and Welfare's "
                       "public employment offices, with the new-openings ratio beside it.",
        "dataset": "job-openings-jp", "code": "sa.active.incl_part", "kind": "reading",
        "percent": False, "decimals": 2,
        "subject": "Japan's jobs-to-applicants ratio",
        "series_name": "the Ministry of Health, Labour and Welfare's count of active job "
                       "openings per active applicant at public employment offices, part-time "
                       "included, seasonally adjusted",
        "also": [("sa.new.incl_part", "the ratio of new openings to new applications",
                  "New openings per new application")],
        "column": "Openings per applicant",
        "chart_title": "Active Openings-to-Applicants Ratio, Seasonally Adjusted",
        "section": "macro", "nav_page": "labour",
    },
    {
        "page": "japan-real-wages.html",
        "question": "Are Japan's real wages rising?",
        "short": "Japan real wages",
        "description": "Japan's real wages, total cash earnings adjusted for prices, against a "
                       "year earlier, as published in the Ministry of Health, Labour and "
                       "Welfare's Monthly Labour Survey (final figures), with nominal earnings "
                       "beside it.",
        "dataset": "wages-jp", "code": "real_wage_yoy.all.5plus.all", "kind": "reading",
        "percent": True, "decimals": 1, "change": True,
        "subject": "Japan's real wages",
        "series_name": "the Ministry of Health, Labour and Welfare's Monthly Labour Survey",
        "also": [("cash_earnings_yoy.all.5plus.all",
                  "nominal total cash earnings, before adjusting for prices,",
                  "Nominal, change on a year")],
        "column": "Real, change on a year",
        "chart_title": "Real Wages, Change on a Year Earlier, Monthly",
        "section": "macro", "nav_page": "labour",
    },
    {
        "page": "japan-rice-prices.html",
        "question": "How have rice prices in Japan changed over the past year?",
        "short": "Japan rice prices",
        "description": "The change in Japanese consumer prices for rice against a year "
                       "earlier, from the rice index in the Statistics Bureau's national CPI, "
                       "with the last twelve months and the series since 1970.",
        "dataset": "cpi-jp-items", "code": "0004", "kind": "rate",
        "subject": "Rice prices in Japan",
        "series_name": "the rice index in the national Consumer Price Index published by the "
                       "Statistics Bureau of Japan",
        "also": [("0001", "headline CPI (all items)")],
        "chart_title": "Rice in the CPI, Year over Year",
        "section": "macro", "nav_page": "inflation",
    },
]

BY_PAGE = dict((q["page"], q) for q in QUESTIONS)

# Rows in the table, by the dataset's frequency.
ROWS = {"monthly": 13, "quarterly": 8, "annual": 10, "daily": 10}

# How far back a "highest since" must reach before the sentence says it. A
# rate that is merely the highest of the last three months is not news, and
# saying so would be the kind of filler the pages exist to avoid.
SINCE_MIN = {"monthly": 12, "quarterly": 4, "annual": 3, "daily": 120}

# ---------------------------------------------------------------------------
# cache — keyed by the macro database version, like readable's
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache = {}


def _version():
    from . import db
    try:
        return db.file_version()
    except Exception:  # noqa: BLE001 — no database is "no version"
        return None


def answer(page):
    """The answer block for one shell page, or None when it cannot be built
    honestly (no release, no values, an unknown page). Never raises."""
    spec = BY_PAGE.get(page)
    if spec is None:
        return None
    key = (page, _version())
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        value = _build(spec)
    except Exception:  # noqa: BLE001 — a page that renders beats an annotated one
        log.debug("no answer for %s", page, exc_info=True)
        value = None
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = value
    return value


def pages_for_dataset(dataset):
    """The shell pages whose answer a dataset feeds — what changes when it
    publishes a release."""
    return [q["page"] for q in QUESTIONS if q["dataset"] == dataset]


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

def _month(period):
    return period.strftime("%B %Y")


def _day(period):
    return "%d %s" % (period.day, period.strftime("%B %Y"))


def _quarter(period):
    return "Q%d %d" % ((period.month - 1) // 3 + 1, period.year)


def _when(period, frequency, kind):
    """The period as a sentence says it."""
    if frequency == "daily":
        return "on " + _day(period)
    if frequency == "quarterly":
        return ("at the end of " if kind == "level" else "in ") + _quarter(period)
    if frequency == "annual":
        return "on " + _day(period) if kind == "count" else "in " + str(period.year)
    if kind == "level":
        return "at the end of " + _month(period)
    return "in " + _month(period)


def _label(period, frequency):
    """The period as a table row names it."""
    if frequency == "daily":
        return period.isoformat()
    if frequency == "quarterly":
        return _quarter(period)
    if frequency == "annual":
        return str(period.year)
    return period.strftime("%b %Y")


def _pct(v):
    return fmt(v, 1) + "%"


def _signed_pct(v):
    return ("+" if v > 0 else "") + fmt(v, 1) + "%"


def _yen_tn(v100mn=None, vbn=None):
    """A stock in trillions of yen from the unit it was published in."""
    if v100mn is not None:
        return "¥" + fmt(v100mn / 1e4, 1) + " trillion"
    return "¥" + fmt(vbn / 1e3, 1) + " trillion"


def _people(v):
    if abs(v) >= 1e6:
        return "%s million (%s)" % (fmt(v / 1e6, 1), fmt_int(v))
    return fmt_int(v)


def _direction(delta, up="up", down="down", flat="unchanged"):
    if delta is None:
        return None
    if abs(delta) < 0.05:
        return flat
    return up if delta > 0 else down


def _since(points, frequency, want):
    """'the highest since March 2025' — or the highest on record — for the
    latest point, or "" when the run is too short to be worth saying.

    ``points`` is the full history as [(period, value)], oldest first, gaps
    as None. The anchor is the last earlier period at or beyond the latest
    value; the run is what lies between.
    """
    present = [(p, v) for p, v in points if v is not None]
    if len(present) < 2:
        return ""
    latest_p, latest_v = present[-1]
    minimum = SINCE_MIN.get(frequency, 12)
    run = 0
    for p, v in reversed(present[:-1]):
        beats = v >= latest_v if want == "highest" else v <= latest_v
        if beats:
            if run < minimum:
                return ""
            if frequency == "daily":
                since = _day(p)
            elif frequency == "quarterly":
                since = _quarter(p)
            elif frequency == "annual":
                since = str(p.year)
            else:
                since = _month(p)
            return "the %s since %s" % (want, since)
        run += 1
    if run < minimum:
        return ""
    first = present[0][0]
    return "the %s in the series, which begins in %s" % (
        want, str(first.year) if frequency != "daily" else _day(first))


def _superlative(points, frequency):
    return _since(points, frequency, "highest") or _since(points, frequency, "lowest")


# ---------------------------------------------------------------------------
# the block
# ---------------------------------------------------------------------------

def _series(con, dataset, code):
    row = con.execute(
        "SELECT series_id, code, name_en, unit FROM series WHERE dataset=? AND code=?",
        [dataset, code]).fetchone()
    if row is None:
        return None
    return dict(zip(("series_id", "code", "name_en", "unit"), row))


def _year_ago(points, period, frequency):
    from . import api
    if frequency == "daily":
        target = period - datetime.timedelta(days=365)
        earlier = [(p, v) for p, v in points if p <= target and v is not None]
        return earlier[-1] if earlier else (None, None)
    values = dict(points)
    p = api._months_ago(period, 12)
    return (p, values.get(p))


def _build(spec):
    from . import api, registry
    dataset, kind = spec["dataset"], spec["kind"]
    adapter = api._dataset_or_404(dataset)
    manifest = registry.get(dataset) or {}
    frequency = manifest.get("frequency") or adapter.DATASET.get("frequency")
    con = api._con()
    try:
        release = api._release(con, dataset)
        if not release:
            return None
        main = _series(con, dataset, spec["code"])
        if main is None:
            return None
        raw = api._values(con, main["series_id"])
        also = []
        columns_also = []
        for item in spec.get("also") or []:
            s = _series(con, dataset, item[0])
            if s:
                also.append((item[1], api._values(con, s["series_id"])))
                columns_also.append(item[2] if len(item) > 2 else item[1])
    finally:
        con.close()

    levels = sorted((p, v) for p, v in raw.items())
    if not levels or levels[-1][1] is None:
        return None
    latest_p, latest_v = levels[-1]
    when = _when(latest_p, frequency, kind)
    unit = api.UNIT_LABEL.get(main["unit"], main["unit"]) or ""
    columns = [{"label": "Period", "num": False}]
    cells = []
    decimals = []
    calc = None
    trust = "official"
    sentence = context = ""
    chart_measure = "index"

    if kind == "rate":
        yoy = api._measure_points(raw, "yoy")
        latest_r = yoy[-1][1] if yoy and yoy[-1][0] == latest_p else None
        if latest_r is None:
            return None
        trust, chart_measure = "derived", "yoy"
        calc = "YoY: " + api.CALC["yoy"]
        word = _direction(latest_r, "higher", "lower", None)
        if word is None:
            sentence = "%s were unchanged %s from a year earlier, on %s." % (
                spec["subject"], when, spec["series_name"])
        else:
            sentence = "%s were %s %s %s than a year earlier, on %s." % (
                spec["subject"], _pct(abs(latest_r)), word, when, spec["series_name"])
        prev = yoy[-2] if len(yoy) > 1 else (None, None)
        parts = []
        if prev[1] is not None:
            parts.append("That compares with %s in %s" % (
                _pct(prev[1]), _label(prev[0], frequency) if frequency != "monthly"
                else _month(prev[0])))
        sup = _superlative(yoy, frequency)
        if sup:
            parts.append(("and is " if parts else "It is ") + sup)
        context = (" ".join(parts) + ".") if parts else ""
        extras = []
        for label, values in also:
            pts = api._measure_points(values, "yoy")
            v = pts[-1][1] if pts and pts[-1][0] == latest_p else None
            if v is not None:
                extras.append("%s was %s" % (label, _pct(v)))
        if extras:
            extra = " and ".join(extras)
            context = (context + " " if context else "") + extra[:1].upper() + extra[1:] + "."
        columns += [{"label": "Index (%s)" % (release.get("base") or "index"), "num": True},
                    {"label": "YoY (%)", "num": True}]
        cells = [[v for _, v in levels], [v for _, v in yoy]]
        decimals = [1, 1]

    elif kind == "growth":
        ann = api._measure_points(raw, "ann3m")
        yoy = api._measure_points(raw, "yoy")
        latest_a = ann[-1][1] if ann and ann[-1][0] == latest_p else None
        if latest_a is None:
            return None
        trust, chart_measure = "derived", "ann3m"
        calc = ("Annualised QoQ: ((level[t] / level[t−1 quarter]) ^ 4 − 1) × 100; "
                "YoY: (level[t] / level[t−4 quarters] − 1) × 100, "
                "from published values.")
        word = _direction(latest_a, "grew", "shrank", None)
        if word is None:
            sentence = "%s was flat %s, on %s." % (
                spec["subject"], when, spec["series_name"])
        else:
            sentence = "%s %s at an annualised rate of %s %s, quarter on quarter, on %s." % (
                spec["subject"], word, _pct(abs(latest_a)), when, spec["series_name"])
        parts = []
        prev = ann[-2] if len(ann) > 1 else (None, None)
        if prev[1] is not None:
            parts.append("That compares with %s in %s." % (
                _pct(prev[1]), _quarter(prev[0])))
        latest_y = yoy[-1][1] if yoy and yoy[-1][0] == latest_p else None
        if latest_y is not None:
            parts.append("Compared with the same quarter a year earlier, real GDP was %s %s." % (
                _pct(abs(latest_y)), "higher" if latest_y >= 0 else "lower"))
        context = " ".join(parts)
        columns += [{"label": "Real GDP (%s)" % unit, "num": True},
                    {"label": "Annualised QoQ (%)", "num": True},
                    {"label": "YoY (%)", "num": True}]
        cells = [[v for _, v in levels], [v for _, v in ann], [v for _, v in yoy]]
        decimals = [1, 1, 1]

    elif kind == "level":
        is_100mn = main["unit"] == "jpy_100mn"
        tn = (lambda v: _yen_tn(v100mn=v)) if is_100mn else (lambda v: _yen_tn(vbn=v))
        sentence = "%s stood at %s %s, on %s." % (
            spec["subject"], tn(latest_v), when, spec["series_name"])
        parts = []
        prev = levels[-2] if len(levels) > 1 else (None, None)
        if prev[1] is not None:
            d = latest_v - prev[1]
            d_tn = d / (1e4 if is_100mn else 1e3)
            earlier = _label(prev[0], frequency) if frequency != "monthly" else _month(prev[0])
            if abs(d_tn) < 0.05:
                parts.append("Little changed from %s" % earlier)
            else:
                parts.append("%s %s from %s" % ("Up" if d > 0 else "Down", tn(abs(d)), earlier))
        ya_p, ya_v = _year_ago(levels, latest_p, frequency)
        if ya_v is not None:
            d = latest_v - ya_v
            parts.append("%s %s (%s) from a year earlier" % (
                _direction(d, "up", "down", "unchanged"), tn(abs(d)),
                _signed_pct((latest_v / ya_v - 1) * 100) if ya_v else DASH))
        if parts:
            context = " and ".join(p.strip() for p in parts if p.strip()) + "."
        peak_p, peak_v = max(levels, key=lambda pv: pv[1] if pv[1] is not None else float("-inf"))
        if peak_v is not None and peak_v > latest_v:
            context += " The peak was %s in %s." % (
                tn(peak_v), _label(peak_p, frequency) if frequency != "monthly" else _month(peak_p))
        elif peak_v == latest_v:
            context += " That is the highest in the series, which begins in %d." % levels[0][0].year
        yoy = api._measure_points(raw, "yoy")
        columns += [{"label": "%s (%s)" % (spec.get("column", "Level"), unit), "num": True},
                    {"label": "YoY (%)", "num": True}]
        cells = [[v for _, v in levels], [v for _, v in yoy]]
        decimals = [0, 1]
        calc = "YoY: " + api.CALC["yoy"].replace("index", "value")

    elif kind == "count":
        sentence = "%s %s %s, on %s." % (
            _people(latest_v), spec["subject"], when, spec["series_name"])
        parts = []
        ya_p, ya_v = _year_ago(levels, latest_p, frequency)
        brk = spec.get("break")
        if brk and ya_p is not None and ya_p < brk <= latest_p:
            parts.append(spec.get("break_note") or "")
            ya_v = None
        if ya_v:
            r = (latest_v / ya_v - 1) * 100
            parts.append("That is %s %s from a year earlier (%s %s)" % (
                _direction(r, "up", "down", "unchanged"), _pct(abs(r)), _people(ya_v),
                _when(ya_p, frequency, kind)))
        sup = _superlative(levels, frequency)
        if sup:
            parts.append(("and " if parts and not brk else "It is ") + sup)
        context = " ".join(p if p.endswith(".") else p + "." for p in parts if p)
        yoy = api._measure_points(raw, "yoy")
        if brk:
            yoy = [(p, None if p >= brk and api._months_ago(p, 12) < brk else v)
                   for p, v in yoy]
        columns += [{"label": "%s (%s)" % (spec.get("column", "Count"), unit), "num": True},
                    {"label": "YoY (%)", "num": True}]
        cells = [[v for _, v in levels], [v for _, v in yoy]]
        decimals = [0, 1]
        calc = "YoY: " + api.CALC["yoy"].replace("index", "value")

    elif kind == "yield":
        sentence = "%s was %s %s, on %s." % (
            spec["subject"], fmt(latest_v, 3) + "%", when, spec["series_name"])
        parts = []
        prev = levels[-2] if len(levels) > 1 else (None, None)
        if prev[1] is not None:
            d = latest_v - prev[1]
            parts.append("That is %s from the previous trading day (%s)" % (
                ("unchanged" if abs(d) < 0.0005 else
                 "%s %s percentage points" % ("up" if d > 0 else "down", fmt(abs(d), 3))),
                _day(prev[0])))
        ya_p, ya_v = _year_ago(levels, latest_p, frequency)
        if ya_v is not None:
            d = latest_v - ya_v
            parts.append("%s %s percentage points from a year earlier" % (
                "up" if d >= 0 else "down", fmt(abs(d), 2)))
        sup = _superlative(levels, frequency)
        if sup:
            parts.append(("and " if parts else "It is ") + sup)
        context = (" and ".join(parts[:2]) + (" " + parts[2] if len(parts) > 2 else "") + "."
                   if parts else "")
        columns += [{"label": "Yield (% per year)", "num": True}]
        cells = [[v for _, v in levels]]
        decimals = [3]

    elif kind == "reading":
        d = spec.get("decimals", 1)
        pct = spec.get("percent", True)

        def show(v):
            return fmt(v, d) + ("%" if pct else "")

        def earlier(p):
            return _month(p) if frequency == "monthly" else _label(p, frequency)
        if spec.get("change"):
            word = _direction(latest_v, "higher", "lower", None)
            if word is None:
                sentence = "%s were unchanged %s from a year earlier, as published in %s." % (
                    spec["subject"], when, spec["series_name"])
            else:
                sentence = "%s were %s %s %s than a year earlier, as published in %s." % (
                    spec["subject"], show(abs(latest_v)), word, when, spec["series_name"])
        else:
            sentence = "%s was %s %s, on %s." % (
                spec["subject"], show(latest_v), when, spec["series_name"])
        parts = []
        prev = levels[-2] if len(levels) > 1 else (None, None)
        if prev[1] is not None:
            parts.append("That compares with %s in %s" % (show(prev[1]), earlier(prev[0])))
        ya_p, ya_v = _year_ago(levels, latest_p, frequency)
        if ya_v is not None:
            parts.append("%s a year earlier" % show(ya_v))
        context = (" and ".join(parts) + ".") if parts else ""
        sup = _superlative(levels, frequency)
        if sup:
            context = (context + " " if context else "") + "It is " + sup + "."
        extras = []
        for label, values in also:
            v = values.get(latest_p)
            if v is not None:
                if spec.get("change"):
                    extras.append("%s %s on a year earlier" % (
                        label, ("rose " if v > 0 else "fell ") + show(abs(v)) if v
                        else "were unchanged"))
                else:
                    extras.append("%s was %s" % (label, show(v)))
        if extras:
            extra = " and ".join(extras)
            context = (context + " " if context else "") + extra[:1].upper() + extra[1:] + "."
        unit_label = "(%)" if pct else ""
        columns += [{"label": ("%s %s" % (spec.get("column", "Value"), unit_label)).strip(),
                     "num": True}]
        cells = [[v for _, v in levels]]
        decimals = [d]
        for column, (_, values) in zip(columns_also, also):
            columns.append({"label": column + (" (%)" if pct else ""), "num": True})
            cells.append([values.get(p) for p, _ in levels])
            decimals.append(d)

    else:
        return None

    n = ROWS.get(frequency, 10)
    periods = [p for p, _ in levels][-n:]
    rows = []
    for i, p in enumerate(periods):
        idx = len(levels) - n + i if len(levels) >= n else i
        row = [_label(p, frequency)]
        for col, d in zip(cells, decimals):
            v = col[idx] if idx < len(col) else None
            row.append(fmt(v, d))
        rows.append(row)
    rows.reverse()

    from .seo import SITE_BASE_URL
    page_url = SITE_BASE_URL + "/" + spec["page"]
    csv = "/api/v1/%s/observations?series=%s&measure=%s&format=csv" % (
        dataset, main["code"], chart_measure)
    json_path = "/api/v1/%s/observations?series=%s&measure=%s" % (
        dataset, main["code"], chart_measure)
    today = datetime.date.today().isoformat()
    credit = (manifest.get("source") or {}).get("credit") or ""
    cite = "Plover Analytics, “%s”, %s, retrieved %s. %s" % (
        spec["short"], page_url, today, credit)
    through = (_day(latest_p) if frequency == "daily" else _quarter(latest_p)
               if frequency == "quarterly" else str(latest_p.year)
               if frequency == "annual" else _month(latest_p))
    asof = "Data through %s. Retrieved from the source on %s. %s" % (
        through, (release.get("known_at") or "")[:10],
        "Calculated from published values; see the calculation below."
        if trust == "derived" else "As published.")
    return {
        "page": spec["page"],
        "question": spec["question"],
        "heading": spec["question"],
        "sentence": sentence,
        "context": context,
        "asof": asof,
        "trust": trust,
        "table": {"caption": "Latest readings" + (
            ", as published" if trust == "official" else
            ", levels as published and rates calculated from them"),
                  "columns": columns, "rows": rows},
        "calc": calc,
        "credit": credit,
        "release": "Data through " + through,
        "links": [("JSON", json_path), ("CSV", csv),
                  ("Data page", manifest.get("page") or "/")],
        "csv": csv,
        "cite": cite,
        "dataset": dataset,
        "code": main["code"],
        "measure": chart_measure,
        "unit": "%" if trust == "derived" or kind == "yield" else unit,
        "frequency": frequency,
        "source": {"name": release.get("source_name"), "page": release.get("source_page"),
                   "id": release.get("source_id"), "retrieved": release.get("retrieved_at"),
                   "ingested": release.get("ingested_at")},
    }


# ---------------------------------------------------------------------------
# the API: what answer.js reads when the server left the shell unfilled
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/api/v1/answers", tags=["answers"], summary="Every answer page")
def list_answers():
    from .seo import SITE_BASE_URL
    return {"answers": [{"page": SITE_BASE_URL + "/" + q["page"], "question": q["question"],
                         "dataset": q["dataset"]} for q in QUESTIONS]}


@router.get("/api/v1/answers/{slug}", tags=["answers"], summary="One answer, as the page states it")
def get_answer(slug: str):
    block = answer(slug + ".html")
    if block is None:
        if slug + ".html" not in BY_PAGE:
            raise HTTPException(404, "No answer page named %s" % slug)
        raise HTTPException(503, "No published release behind %s yet" % slug)
    return block
