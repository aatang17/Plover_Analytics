"""Adapter: Federal Reserve Bank of Atlanta Wage Growth Tracker.

Source: the Atlanta Fed's wage-growth-data.xlsx (atlantafed.org, research
and data, Wage Growth Tracker). The tracker follows the same people in the
Census Bureau's Current Population Survey twelve months apart and reports
the median of their individual wage changes, 100 × ln(wage[t] / wage[t−12])
— wage growth for people who stayed employed, free of the shift in who is
working that moves an average-earnings figure.

Every value is already a rate in percent, published by the Atlanta Fed; a
percentage change of one is refused, and changes are in percentage points.

What is loaded, by sheet:

- `data_overall` — the headline and eleven cuts (full-time, job stayers
  and switchers, paid hourly, weighted medians, weekly basis, trimmed
  mean), **3-month moving averages**, from 1997.
- `data_chart1`–`data_chart3` — the unsmoothed median, the 25th and 75th
  percentiles, the mean, and the share of zero wage changes.
- `WGT_1983` — the headline back to 1983.
- The demographic and job sheets (age, sex, race, education, occupation,
  industry, census division, wage quartile, ...) — **12-month moving
  averages**, because the samples are small.

The `Alternative WGT` sheet (a methodological variant described on the Atlanta
Fed's blog) and the recession shading columns are not loaded. A column that
repeats the overall median on a demographic sheet is not loaded twice.

Series codes are built from the sheet and the column and are this platform's
own; the smoothing window is part of every series name.
"""
import datetime
import re
import urllib.parse
import urllib.request

from . import bls_flat, xlsx


class ValidationError(Exception):
    pass


# The Atlanta Fed moves this file when it reorganises its site (2026-11-05:
# the datafiles/chcs/ path answered 404 and the workbook sat under
# research-and-data/data/). fetch() finds the current link on the tracker's
# own page and uses this only when the page names none.
PAGE = "https://www.atlantafed.org/research-and-data/data/wage-growth-tracker"
URL = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
       "research-and-data/data/wage-growth-tracker/wage-growth-data.xlsx")
_DATA_LINK = re.compile(r'href="([^"]*wage-growth-data\.xlsx[^"]*)"', re.I)

DATASET = {
    "slug": "us-wage-tracker",
    "title": "Atlanta Fed Wage Growth Tracker (median wage growth, United States)",
    "country": "United States",
    "agency": "Federal Reserve Bank of Atlanta",
    "agency_ja": None,
    "base": None,
    "frequency": "monthly",
    "description": (
        "Median 12-month wage growth of individuals observed a year apart in "
        "the Current Population Survey, computed by the Federal Reserve Bank "
        "of Atlanta: overall and by job, demographic, industry and region, in "
        "percent, monthly from 1997 (overall from 1983). Headline cuts are "
        "3-month moving averages; demographic cuts 12-month moving averages."
    ),
}

SOURCE = {
    "source_id": "frbatl:wage-growth-tracker",
    "name": "Federal Reserve Bank of Atlanta — Wage Growth Tracker data",
    "name_ja": None,
    "url": "https://www.atlantafed.org/research-and-data/data/wage-growth-tracker",
    "license_note": ("Published by the Federal Reserve Bank of Atlanta from Current "
                     "Population Survey microdata (BLS/Census). Cite the Federal "
                     "Reserve Bank of Atlanta, Wage Growth Tracker."),
}

DOWNLOAD_URL = URL
RAW_SUFFIX = ".xlsx"

# sheet -> (code prefix, smoothing written into the name, header row search)
THREE_MONTH = "3-month moving average"
TWELVE_MONTH = "12-month moving average"
SHEETS = {
    "data_overall": ("3M", THREE_MONTH),
    "data_chart1": ("3M", THREE_MONTH),
    "data_chart2": ("3M", THREE_MONTH),
    "data_chart3": ("3M", THREE_MONTH),
    "WGT_1983": ("1983", THREE_MONTH),
    "Race": ("12M-RACE", TWELVE_MONTH),
    "Education": ("12M-EDUCATION", TWELVE_MONTH),
    "Age": ("12M-AGE", TWELVE_MONTH),
    "Sex": ("12M-SEX", TWELVE_MONTH),
    "Occupation": ("12M-OCCUPATION", TWELVE_MONTH),
    "Industry": ("12M-INDUSTRY", TWELVE_MONTH),
    "Census Divisions": ("12M-DIVISION", TWELVE_MONTH),
    "Full-Time or Part-Time": ("12M-HOURS", TWELVE_MONTH),
    "Job Switcher": ("12M-JOB", TWELVE_MONTH),
    "MSA or non-MSA": ("12M-MSA", TWELVE_MONTH),
    "Average Wage Quartile": ("12M-QUARTILE", TWELVE_MONTH),
    "Paid Hourly": ("12M-PAY", TWELVE_MONTH),
    "Overall 12ma": ("12M", TWELVE_MONTH),
}

HEADLINE = "3M-OVERALL"

PRESENTATION = {
    "credit_line": "Source: Federal Reserve Bank of Atlanta, Wage Growth Tracker.",
    "stale_after_days": 75,
    "overview_tiles": [
        {"key": "overall", "code": "3M-OVERALL", "label": "Median wage growth", "type": "level"},
        {"key": "stayer", "code": "3M-JOB-STAYER", "label": "Job stayers", "type": "level"},
        {"key": "switcher", "code": "3M-JOB-SWITCHER", "label": "Job switchers", "type": "level"},
        {"key": "hourly", "code": "3M-PAID-HOURLY", "label": "Paid hourly", "type": "level"},
    ],
    "main_series": [
        {"role": "overall", "code": "3M-OVERALL", "label": "Median wage growth", "slot": 1},
        {"role": "stayer", "code": "3M-JOB-STAYER", "label": "Job stayers", "slot": 2},
        {"role": "switcher", "code": "3M-JOB-SWITCHER", "label": "Job switchers", "slot": 3},
    ],
    # Every value is already a percent; a percentage change of it is refused.
    "kind_default": "rate",
}


def data_url(html, page_url=PAGE):
    """The workbook's current address from the tracker page, or None."""
    m = _DATA_LINK.search(html or "")
    return urllib.parse.urljoin(page_url, m.group(1).replace("&amp;", "&")) if m else None


def current_url():
    try:
        req = urllib.request.Request(PAGE, headers={"User-Agent": bls_flat.user_agent()})
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001
        return URL              # the page is down; the last known address may still answer
    return data_url(html) or URL


def fetch():
    return bls_flat.fetch_url(current_url(), "atlfed-wage-growth-data.xlsx", accept_prefix=b"PK")


def _slug(text):
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


# Columns that are not medians get their own wording; everything else is
# "Median wage growth: <cut>".
NOT_MEDIAN = {"25th percentile": "Wage growth, 25th percentile",
              "75th percentile": "Wage growth, 75th percentile",
              "Mean": "Wage growth, mean",
              "25/20 trimmed mean": "Wage growth, 25/20 trimmed mean",
              "Percent of zero wage changes": "Share of workers with no wage change"}


def _label(prefix, label, smoothing):
    """(code, name) for one column, or (None, None) for one not loaded."""
    if not label or label == "Recession":
        return None, None
    # The overall median repeats on every demographic sheet, and chart 2's
    # "Median" is the headline again.
    if label == "Overall" and prefix.startswith("12M-"):
        return None, None
    if label == "Overall: Median":
        return None, None
    core = label.replace(": From 1983", "")
    if core.startswith("Overall: "):
        core = core[len("Overall: "):]
    code = "%s-%s" % (prefix, _slug(core))
    since = ", from 1983" if prefix == "1983" else ""
    if core == "Non-smoothed":
        return code, "Median wage growth (monthly, not smoothed%s)" % since
    if core in NOT_MEDIAN:
        name = NOT_MEDIAN[core]
    elif core == "Overall":
        name = "Median wage growth"
    else:
        name = "Median wage growth: %s" % core
    return code, "%s (%s%s)" % (name, smoothing, since)


_EXCEL_EPOCH = datetime.date(1899, 12, 30)


def _month(cell):
    """An Excel serial date on the first of a month -> that date."""
    try:
        d = _EXCEL_EPOCH + datetime.timedelta(days=int(float(cell)))
    except (TypeError, ValueError):
        return None
    if d.day != 1:
        raise ValidationError("date %s is not the first of a month" % d)
    return d


def parse(raw_bytes):
    try:
        sheets = xlsx.sheets(raw_bytes)
    except Exception as exc:
        raise ValidationError("not a workbook: %s" % exc)
    missing = [s for s in SHEETS if s not in sheets]
    if missing:
        raise ValidationError("sheets missing: %s (have %s)" % (missing, list(sheets)))

    series, observations = [], []
    names = {}
    for sheet, (prefix, smoothing) in SHEETS.items():
        grid = sheets[sheet]
        rows = sorted(grid)
        # The header is the last row before the first dated row.
        first_data = next((r for r in rows if _month(xlsx.cell_text(grid[r].get("A")))), None)
        if first_data is None:
            raise ValidationError("%s: no dated rows" % sheet)
        header_row = max(r for r in rows if r < first_data and
                         any(c != "A" for c in grid[r]))
        header = dict((col, xlsx.cell_text(cell).strip())
                      for col, cell in grid[header_row].items() if col != "A")
        columns = {}
        for col, label in header.items():
            code, name = _label(prefix, label, smoothing)
            if code is None or code in names:
                continue           # skipped, or the same series on a second chart sheet
            names[code] = name
            columns[col] = code
            series.append({"code": code, "name_en": name, "name_ja": None,
                           "unit": "percent", "weight_per_10000": None,
                           "sort_order": len(series)})
        for r in rows:
            if r < first_data:
                continue
            period = _month(xlsx.cell_text(grid[r].get("A")))
            if period is None:
                continue
            for col, code in columns.items():
                cell = xlsx.cell_text(grid[r].get(col)).strip()
                if not cell or cell in ("NA", "#N/A", "."):
                    continue       # not published for that month: a gap
                try:
                    v = round(float(cell), 1)   # published to one decimal
                except ValueError:
                    raise ValidationError("%s %s %s: non-numeric %r" % (sheet, period, code, cell))
                observations.append({"code": code, "period": period, "value": v})
    return series, observations


def validate(series, observations):
    codes = set(s["code"] for s in series)
    for c in ("3M-OVERALL", "3M-JOB-STAYER", "3M-JOB-SWITCHER", "3M-PAID-HOURLY",
              "1983-OVERALL", "12M-OVERALL"):
        if c not in codes:
            raise ValidationError("series %s missing (have %s)" % (c, sorted(codes)[:12]))
    if len(series) < 50:
        raise ValidationError("only %d series parsed" % len(series))
    seen = set()
    latest, first = None, None
    for o in observations:
        key = (o["code"], o["period"])
        if key in seen:
            raise ValidationError("duplicate %s %s" % key)
        seen.add(key)
        # Median wage growth has run from about 0 to 7%; the percentiles,
        # the mean and the share of zero changes reach further. Beyond ±60
        # is a column read wrongly.
        if not (-60 <= o["value"] <= 60):
            raise ValidationError("%s %s: %r out of range" % (o["code"], o["period"], o["value"]))
        p = o["period"]
        latest = p if latest is None or p > latest else latest
        first = p if first is None or p < first else first
    if first > datetime.date(1983, 12, 1):
        raise ValidationError("history starts %s, expected 1983" % first)
    head = dict((o["period"], o["value"]) for o in observations if o["code"] == HEADLINE)
    if latest not in head:
        raise ValidationError("headline has no value for %s" % latest)
    return {"series": len(series), "observations": len(observations),
            "latest_period": latest.isoformat(), "median_wage_growth_pct": head[latest]}


MANIFEST = {
    "id": DATASET["slug"],
    "section": "prices",
    "name": {"en": "Atlanta Fed Wage Growth Tracker", "ja": "アトランタ連銀賃金上昇率トラッカー"},
    "shape": "series",
    "summary": ("Median 12-month wage growth of the same US workers a year "
                "apart, from the Atlanta Fed: overall, job stayers and "
                "switchers, and by age, sex, education, industry and region, "
                "monthly from 1997 (overall from 1983)."),
    "source": {
        "publisher": DATASET["agency"],
        "publisher_ja": None,
        "document": SOURCE["name"],
        "url": SOURCE["url"],
        "credit": PRESENTATION["credit_line"],
        "license_note": SOURCE["license_note"],
    },
    "keys": ["series_code", "period"],
    "frequency": DATASET["frequency"],
    "vintage": {
        "unit": "release", "as_of_basis": "release-in-force",
        "as_of_supported": True, "history_from": "1983-01",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Median wage growth, percent (as published)",
         "unit": "%", "trust": "official"},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "summary": "/api/v1/%s/overview" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search", "summary"],
    "cite": "/us-wages.html?dataset=us-wage-tracker",
    "page": "/us-wages.html",
    "notes": [
        "Every value is a rate in percent as the Atlanta Fed publishes it; a "
        "percentage change of it is refused, and changes are in percentage points.",
        "The smoothing is in every series name: headline cuts are 3-month "
        "moving averages, demographic and job cuts 12-month moving averages.",
        "An individual's wage change is 100 × ln(wage[t] / wage[t−12]) for the "
        "same person a year apart; the series is the median across people.",
        "Series codes are this platform's own, built from the Atlanta Fed's "
        "sheet and column names. The 'Alternative WGT' sheet is not loaded.",
    ],
}
