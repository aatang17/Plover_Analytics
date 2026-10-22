"""Adapter: US jobs and unemployment (BLS Employment Situation and JOLTS).

Source: U.S. Bureau of Labor Statistics, through its public data API, version
1 (https://api.bls.gov/publicAPI/v1/timeseries/data/). Version 1 needs no key
and allows 25 queries a day, each of up to 25 series and 10 years; the whole
history is fetched in ten-year windows, one query a window, so a nightly
ingest spends nine of the twenty-five. The bulk flat files were not used
because the household-survey file (ln.data.1.AllData) is 390 MB for the
handful of headline series held here.

Three surveys, thirteen series, monthly, all as the BLS publishes them:

    Current Population Survey (household survey, from January 1948)
        unemployment rate, seasonally adjusted and not; U-6 (from 1994);
        participation rate; employment-population ratio; the labor force,
        employed and unemployed in thousands
    Current Employment Statistics (payroll survey, from January 1939)
        total nonfarm and total private payrolls, seasonally adjusted, thousands
    JOLTS (from December 2000)
        job openings, hires and quits, seasonally adjusted, thousands

Codes are the BLS series ids: LNS14000000 is the headline unemployment rate,
CES0000000001 total nonfarm payrolls. Levels are in thousands of people as
the BLS gives them; nothing is rescaled here.

**Revised.** Payrolls are revised in each of the two releases after a month's
first print and re-benchmarked every year; the household-survey rates are
re-seasonalised each January; JOLTS revises the prior month. Each revision
is a new vintage, so the first print stays retrievable with as_of.
"""
import datetime
import json
import urllib.error
import urllib.request


class ValidationError(Exception):
    pass


class FetchError(Exception):
    pass


DATASET = {
    "slug": "us-labour",
    "title": "US Jobs and Unemployment (BLS Employment Situation and JOLTS)",
    "country": "United States",
    "agency": "U.S. Bureau of Labor Statistics",
    "agency_ja": None,
    "base": None,
    "frequency": "monthly",
    "description": (
        "The headline US labour-market series as the BLS publishes them: the "
        "unemployment rate (seasonally adjusted and not, and the broad U-6 "
        "measure), participation and employment-population rates, the labor "
        "force, employed and unemployed in thousands, total nonfarm and private "
        "payrolls, and job openings, hires and quits from JOLTS. Monthly, from "
        "1939 for payrolls and 1948 for the household survey. Revised after "
        "first print and re-benchmarked yearly; each revision is a new vintage."
    ),
}

SOURCE = {
    "source_id": "bls:api:labour",
    "name": "BLS Employment Situation (CPS, CES) and Job Openings and Labor Turnover Survey (JOLTS), via the BLS public API",
    "name_ja": None,
    "url": "https://www.bls.gov/bls/newsrels.htm#OEUS",
    "license_note": ("Work of the United States Government, in the public domain "
                     "(17 U.S.C. §105). Cite the Bureau of Labor Statistics as the source."),
}

DOWNLOAD_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
RAW_SUFFIX = ".json"

# (code, name, unit, first year the BLS publishes)
SERIES = [
    ("LNS14000000", "Unemployment rate, seasonally adjusted", "percent", 1948),
    ("LNU04000000", "Unemployment rate, not seasonally adjusted", "percent", 1948),
    ("LNS13327709", "U-6: unemployed, marginally attached and part time for economic reasons, as a share of the labor force plus marginally attached, seasonally adjusted", "percent", 1994),
    ("LNS11300000", "Labor force participation rate, seasonally adjusted", "percent", 1948),
    ("LNS12300000", "Employment-population ratio, seasonally adjusted", "percent", 1948),
    ("LNS11000000", "Civilian labor force, seasonally adjusted (thousands)", "thousands", 1948),
    ("LNS12000000", "Employment level, seasonally adjusted (thousands)", "thousands", 1948),
    ("LNS13000000", "Unemployment level, seasonally adjusted (thousands)", "thousands", 1948),
    ("CES0000000001", "Total nonfarm payrolls, seasonally adjusted (thousands)", "thousands", 1939),
    ("CES0500000001", "Total private payrolls, seasonally adjusted (thousands)", "thousands", 1939),
    ("JTS000000000000000JOL", "Job openings, total nonfarm, seasonally adjusted (thousands)", "thousands", 2000),
    ("JTS000000000000000HIL", "Hires, total nonfarm, seasonally adjusted (thousands)", "thousands", 2000),
    ("JTS000000000000000QUL", "Quits, total nonfarm, seasonally adjusted (thousands)", "thousands", 2000),
]
CODES = [s[0] for s in SERIES]
FIRST_YEAR = min(s[3] for s in SERIES)
WINDOW_YEARS = 10                      # the API's limit per query, keyless

TILES = [
    ("unemployment", "LNS14000000", "Unemployment rate"),
    ("payrolls", "CES0000000001", "Nonfarm payrolls (thousands)"),
    ("participation", "LNS11300000", "Participation rate"),
    ("openings", "JTS000000000000000JOL", "Job openings (thousands)"),
]

PRESENTATION = {
    "credit_line": "Source: U.S. Bureau of Labor Statistics.",
    "stale_after_days": 45,        # the jobs report lands in the first week of the next month
    "overview_tiles": [{"key": k, "code": c, "label": l, "type": "level"} for k, c, l in TILES],
    "main_series": [{"role": k, "code": c, "label": l, "slot": i + 1} for i, (k, c, l) in enumerate(TILES)],
}


def _post(start, end):
    body = json.dumps({"seriesid": CODES, "startyear": str(start), "endyear": str(end)}).encode("utf-8")
    req = urllib.request.Request(DOWNLOAD_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Plover Analytics ingest (keyless BLS API v1)")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise FetchError("BLS API %d-%d: %s" % (start, end, exc))
    if out.get("status") != "REQUEST_SUCCEEDED":
        raise FetchError("BLS API %d-%d: %s %s" % (start, end, out.get("status"), "; ".join(out.get("message") or [])))
    return out


def fetch():
    """Every window of ten years from the first year to now, one query each,
    bundled as one JSON document whose bytes depend only on the data."""
    today = datetime.date.today()
    windows = []
    start = FIRST_YEAR
    while start <= today.year:
        end = min(start + WINDOW_YEARS - 1, today.year)
        got = _post(start, end)
        series = []
        for s in got.get("Results", {}).get("series", []):
            data = sorted(({"year": d.get("year"), "period": d.get("period"), "value": d.get("value"),
                            "footnotes": sorted(f.get("code") for f in (d.get("footnotes") or []) if f.get("code"))}
                           for d in s.get("data", [])), key=lambda d: (d["year"], d["period"]))
            series.append({"seriesID": s.get("seriesID"), "data": data})
        series.sort(key=lambda s: s["seriesID"])
        windows.append({"start": start, "end": end, "series": series})
        start = end + 1
    return json.dumps({"source": DOWNLOAD_URL, "series": CODES, "windows": windows},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse(raw_bytes):
    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("not the BLS bundle: %s" % exc)
    names = {c: (n, u) for c, n, u, _ in SERIES}
    values = {}
    for w in doc.get("windows", []):
        for s in w.get("series", []):
            code = s.get("seriesID")
            if code not in names:
                continue
            for d in s.get("data", []):
                p = d.get("period") or ""
                if not p.startswith("M") or p == "M13":
                    continue                       # months only; M13 is the BLS's annual average
                try:
                    period = datetime.date(int(d["year"]), int(p[1:]), 1)
                    v = float(d["value"])
                except (TypeError, ValueError):
                    continue                       # '-' is not available
                values.setdefault(code, {})[period] = v
    series, observations = [], []
    for i, (code, name, unit, _first) in enumerate(SERIES):
        if code not in values:
            continue
        series.append({"code": code, "name_en": name, "name_ja": None, "unit": unit,
                       "weight_per_10000": None, "sort_order": i})
        for period in sorted(values[code]):
            observations.append({"code": code, "period": period, "value": values[code][period]})
    return series, observations


def validate(series, observations):
    codes = {s["code"] for s in series}
    missing = [c for _k, c, _l in TILES if c not in codes]
    if missing:
        raise ValidationError("headline series missing: %s" % ", ".join(missing))
    if len(series) < 10:
        raise ValidationError("only %d series parsed" % len(series))
    first_by, last_by, last_val = {}, {}, {}
    for o in observations:
        code, p, v = o["code"], o["period"], o["value"]
        unit = next(u for c, _n, u, _f in SERIES if c == code)
        # unemployment rates run 2.5-25%; participation and the employment-population ratio 50-70%
        if unit == "percent" and not (0.5 < v < 75.0):
            raise ValidationError("%s %s: %r out of range for a rate" % (code, p, v))
        if code in ("LNS14000000", "LNU04000000") and not (2.0 < v < 26.0):
            raise ValidationError("%s %s: %r out of range for an unemployment rate" % (code, p, v))
        if unit == "thousands" and not (500.0 < v < 250000.0):
            raise ValidationError("%s %s: %r out of range for a count in thousands" % (code, p, v))
        first_by[code] = min(first_by.get(code, p), p)
        if p >= last_by.get(code, datetime.date.min):
            last_by[code], last_val[code] = p, v
    if first_by["LNS14000000"] > datetime.date(1948, 1, 1):
        raise ValidationError("unemployment rate starts %s, expected 1948" % first_by["LNS14000000"])
    if first_by["CES0000000001"] > datetime.date(1939, 1, 1):
        raise ValidationError("payrolls start %s, expected 1939" % first_by["CES0000000001"])
    latest = last_by["LNS14000000"]
    if last_by["CES0000000001"] != latest:
        raise ValidationError("payrolls end %s but the unemployment rate runs to %s" % (last_by["CES0000000001"], latest))
    if (datetime.date.today() - latest).days > 120:
        raise ValidationError("latest month %s is more than four months old" % latest)
    return {
        "series": len(series),
        "observations": len(observations),
        "latest_period": latest.isoformat(),
        "unemployment_rate_sa": last_val.get("LNS14000000"),
        "nonfarm_payrolls_thousands": last_val.get("CES0000000001"),
        "job_openings_thousands": last_val.get("JTS000000000000000JOL"),
    }


MANIFEST = {
    "id": DATASET["slug"],
    "section": "labour",
    "name": {"en": "US jobs and unemployment", "ja": "米国雇用統計"},
    "shape": "series",
    "summary": ("The US unemployment rate, participation, the labor force, nonfarm payrolls and JOLTS "
                "job openings, hires and quits, monthly from 1939, as the BLS publishes them."),
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
        "as_of_supported": True, "history_from": "1939-01",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "As published (a rate in percent, or a count in thousands of people, per the series)",
         "unit": "index", "trust": "official"},
        {"id": "yoy", "label": "Year over year (a relative change; for a rate, not percentage points)", "unit": "%", "trust": "derived",
         "calc": "(index[t] / index[t−12 months] − 1) × 100, from published index values."},
        {"id": "mom", "label": "Month over month (a relative change)", "unit": "%", "trust": "derived",
         "calc": "(index[t] / index[t−1 month] − 1) × 100, from published index values."},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "summary": "/api/v1/%s/overview" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search", "summary"],
    "cite": "/us-labour.html",
    "page": "/us-labour.html",
    "notes": [
        "Codes are BLS series ids: LNS = household survey, seasonally adjusted (LNU not); "
        "CES = payroll survey; JTS = JOLTS. Levels are in thousands of people as published.",
        "The payroll survey counts jobs, the household survey people; the two can move apart "
        "in any one month.",
        "Payrolls are revised in the two releases after first print and re-benchmarked every "
        "year; JOLTS revises the prior month; each revision is a new vintage and the first "
        "print stays retrievable with as_of.",
        "Fetched through the BLS public API, version 1 (keyless, 25 queries a day); nine "
        "ten-year windows are read each night.",
    ],
}
