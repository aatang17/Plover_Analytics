"""Adapter: Federal Reserve Board H.10 — foreign exchange rates.

Source: the Board's G.5/H.10 release package (FRB_H10.zip from the Data
Download Program), one SDMX XML file holding every series the release
publishes. Four are loaded today:

- `USDJPY` — yen per US dollar (Fed series RXI_N.B.JA), daily from 1971
- `USDHKD` — Hong Kong dollars per US dollar (RXI_N.B.HK), daily from 1981
- `EURUSD` — US dollars per euro (RXI$US_N.B.EU), daily from 1999
- `BROAD`  — the Nominal Broad Dollar Index, January 2006 = 100
             (JRXWTFB_N.B), daily from 2006: the dollar against the
             currencies of 26 trading partners, weighted by trade

The rates are noon buying rates in New York for cable transfers, as the Board
publishes them, to four decimals. The release comes out on Mondays with the
previous week's days. A day the Board marks "ND" (no data: a US holiday) is a
gap, never zero; the file writes it as -9999, which is never read as a value.

The XML file carries the time it was prepared, so its bytes change every day
whether or not a rate did. canonical_bytes() reduces a file to the loaded
values, and the ingest compares those, so a day with no new rate publishes
nothing. Every file is still archived as downloaded.

The XML labels the Broad index "Index: 1997 Jan = 100"; the Board's release
page and its values (101.42 on 2 January 2006) say January 2006 = 100, which
is what is shown here.

The Board says its Data Download Program is being retired and points to FRED.
The package fetched here still serves; when it stops, fetch() fails, the
last good release stays live and the health report shows the data going
stale (ingest guardrails 1 and 6).

Work of the US Government, public domain.
"""
import datetime
import io
import json
import zipfile
import xml.etree.ElementTree as ET

from . import bls_flat


class ValidationError(Exception):
    pass


URL = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&filetype=zip"
XML_NAME = "H10_data.xml"

# (code, Fed series, name, unit, first date the history must reach)
SERIES = [
    ("USDJPY", "RXI_N.B.JA", "US dollar–yen exchange rate (yen per US dollar)",
     "JPY per USD", datetime.date(1971, 1, 4)),
    ("USDHKD", "RXI_N.B.HK", "US dollar–Hong Kong dollar exchange rate (HK dollars per US dollar)",
     "HKD per USD", datetime.date(1981, 1, 2)),
    ("EURUSD", "RXI$US_N.B.EU", "Euro–US dollar exchange rate (US dollars per euro)",
     "USD per EUR", datetime.date(1999, 1, 4)),
    ("BROAD", "JRXWTFB_N.B", "Nominal Broad Dollar Index (January 2006 = 100)",
     "index", datetime.date(2006, 1, 2)),
]
BY_FED = dict((fed, code) for code, fed, _n, _u, _d in SERIES)

DATASET = {
    "slug": "us-fx",
    "title": "US Dollar Exchange Rates (Federal Reserve H.10)",
    "country": "United States",
    "agency": "Board of Governors of the Federal Reserve System",
    "agency_ja": None,
    "base": None,
    "frequency": "daily",
    "description": (
        "Daily noon buying rates in New York from the Federal Reserve Board's "
        "H.10 release: yen per US dollar from 1971, Hong Kong dollars per US "
        "dollar from 1981, US dollars per euro from 1999, and the Nominal "
        "Broad Dollar Index (January 2006 = 100) from 2006. Published weekly, "
        "each Monday, with the previous week's days."
    ),
}

SOURCE = {
    "source_id": "frb:h10",
    "name": "Federal Reserve Board — G.5/H.10 Foreign Exchange Rates",
    "name_ja": None,
    "url": "https://www.federalreserve.gov/releases/h10/",
    "license_note": ("Work of the United States Government, in the public domain. "
                     "Noon buying rates in New York for cable transfers payable in "
                     "the listed currencies, as published by the Federal Reserve Board."),
}

DOWNLOAD_URL = URL
RAW_SUFFIX = ".zip"

PRESENTATION = {
    "credit_line": "Source: Board of Governors of the Federal Reserve System, H.10.",
    # Weekly release with the previous week's days: the newest date is up to
    # ten days old the morning before a release. Two weeks is a missed release.
    "stale_after_days": 14,
    "main_series": [
        {"role": "usdjpy", "code": "USDJPY", "label": "Dollar–Yen", "slot": 1},
        {"role": "usdhkd", "code": "USDHKD", "label": "Dollar–Hong Kong Dollar", "slot": 2},
        {"role": "eurusd", "code": "EURUSD", "label": "Euro–Dollar", "slot": 3},
        {"role": "broad", "code": "BROAD", "label": "Broad Dollar Index", "slot": 4},
    ],
    "kinds": {"USDJPY": "level", "USDHKD": "level", "EURUSD": "level", "BROAD": "level"},
}


def fetch():
    return bls_flat.fetch_url(URL, "frb-h10.zip", accept_prefix=b"PK")


def _values(raw_bytes):
    """{code: {date: value}} for the loaded series, read from the package."""
    try:
        member = zipfile.ZipFile(io.BytesIO(raw_bytes)).open(XML_NAME)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValidationError("not the H.10 package: %s" % exc)
    out = dict((code, {}) for code in BY_FED.values())
    cur = None
    try:
        for ev, el in ET.iterparse(member, events=("start", "end")):
            tag = el.tag.rsplit("}", 1)[-1]
            if ev == "start" and tag == "Series":
                cur = BY_FED.get(el.get("SERIES_NAME"))
            elif ev == "end" and tag == "Obs" and cur:
                status, value = el.get("OBS_STATUS"), el.get("OBS_VALUE")
                try:
                    day = datetime.date.fromisoformat(el.get("TIME_PERIOD") or "")
                except ValueError:
                    raise ValidationError("%s: bad date %r" % (cur, el.get("TIME_PERIOD")))
                if status == "ND":
                    continue                  # no data that day (a US holiday): a gap
                if status != "A":
                    raise ValidationError("%s %s: unknown status %r" % (cur, day, status))
                try:
                    out[cur][day] = float(value)
                except (TypeError, ValueError):
                    raise ValidationError("%s %s: non-numeric %r" % (cur, day, value))
            elif ev == "end" and tag == "Series":
                cur = None
                el.clear()
    except ET.ParseError as exc:
        raise ValidationError("unreadable XML: %s" % exc)
    return out


def canonical_bytes(raw_bytes):
    """The loaded values alone, so a re-prepared file with the same rates is
    'unchanged'. A file that cannot be read compares as itself."""
    try:
        vals = _values(raw_bytes)
    except ValidationError:
        return raw_bytes
    return json.dumps(dict((c, sorted((d.isoformat(), v) for d, v in pts.items()))
                           for c, pts in vals.items()), sort_keys=True).encode("utf-8")


def parse(raw_bytes):
    vals = _values(raw_bytes)
    series = [{"code": code, "name_en": name, "name_ja": None, "unit": unit,
               "weight_per_10000": None, "sort_order": i}
              for i, (code, _fed, name, unit, _d) in enumerate(SERIES)]
    observations = [{"code": code, "period": day, "value": v}
                    for code in sorted(vals) for day, v in sorted(vals[code].items())]
    return series, observations


# A value outside its band is a column read wrongly, not a market move. In the
# file of November 2026 the yen runs from 358.44 (1971) to 75.72 (2011), the HK
# dollar from 5.13 to 8.70 (it has been pegged near 7.80 since 1983), the euro
# from 0.83 to 1.60 dollars and the Broad index from 85.5 to 130.0.
BANDS = {"USDJPY": (60.0, 400.0), "USDHKD": (4.0, 10.0),
         "EURUSD": (0.7, 1.8), "BROAD": (70.0, 160.0)}
# Longer than any run of US holidays and a weekend; a longer gap is a file
# served short. The one real one: currency markets closed after the US ended
# the dollar's convertibility into gold on 15 August 1971, and the yen has no
# rate from 16 to 30 August.
MAX_GAP_DAYS = 10
KNOWN_GAPS = {("USDJPY", datetime.date(1971, 8, 13))}


def validate(series, observations):
    by_code = {}
    for o in observations:
        by_code.setdefault(o["code"], []).append(o)
    seen = set()
    for code, _fed, _n, _u, first in SERIES:
        obs = by_code.get(code) or []
        if len(obs) < 1000:
            raise ValidationError("%s: only %d values" % (code, len(obs)))
        lo, hi = BANDS[code]
        days = []
        for o in obs:
            key = (code, o["period"])
            if key in seen:
                raise ValidationError("duplicate %s %s" % key)
            seen.add(key)
            if not (lo <= o["value"] <= hi):
                raise ValidationError("%s %s: %r outside [%s, %s]" % (code, o["period"], o["value"], lo, hi))
            days.append(o["period"])
        days.sort()
        if days[0] > first + datetime.timedelta(days=7):
            raise ValidationError("%s: history starts %s, expected %s" % (code, days[0], first))
        for a, b in zip(days, days[1:]):
            if (b - a).days > MAX_GAP_DAYS and (code, a) not in KNOWN_GAPS:
                raise ValidationError("%s: gap of %d days after %s" % (code, (b - a).days, a))
    latest = max(o["period"] for o in observations)
    last = dict((o["code"], o["value"]) for o in observations if o["period"] == latest)
    if "USDJPY" not in last:
        raise ValidationError("no dollar-yen rate on the latest date %s" % latest)
    return {"series": len(series), "observations": len(observations),
            "latest_period": latest.isoformat(),
            "latest_usdjpy": last.get("USDJPY"), "latest_usdhkd": last.get("USDHKD"),
            "latest_eurusd": last.get("EURUSD"), "latest_broad": last.get("BROAD")}


MANIFEST = {
    "id": DATASET["slug"],
    "section": "rates",
    "name": {"en": "US dollar exchange rates (Federal Reserve H.10)",
             "ja": "米ドル為替レート（FRB H.10）"},
    "shape": "series",
    "summary": ("Daily noon buying rates in New York: dollar–yen from 1971, "
                "dollar–Hong Kong dollar from 1981, euro–dollar from 1999 and the "
                "Nominal Broad Dollar Index from 2006, from the Federal Reserve Board."),
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
        "as_of_supported": True, "history_from": "1971-01-04",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published value — each rate in its own quotation "
                                 "(yen per US dollar, US dollars per euro...), the Broad "
                                 "index January 2006 = 100",
         "unit": "index", "trust": "official"},
        {"id": "delta", "label": "Change from the previous published day / one year earlier",
         "unit": "index", "trust": "derived",
         "calc": ("change[t] = value[t] − value[b], where b is the previous published "
                  "day (or the latest published day on or before the same date a year "
                  "earlier); % change = change / value[b] × 100.")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/us-fx.html",
    "page": "/us-fx.html",
    "notes": [
        "Each pair is quoted as the Board publishes it: yen and Hong Kong dollars "
        "per US dollar, but US dollars per euro. A rise in USDJPY is a stronger "
        "dollar; a rise in EURUSD is a weaker one.",
        "Noon buying rates in New York. The Bank of Japan's Tokyo rate for the "
        "same day differs by the hours between the two fixes.",
        "A US holiday is a gap, never zero. Daily series: the monthly rate "
        "measures (yoy, mom, ann3m) are refused; request measure=index.",
        "The Broad index weighs the dollar against 26 trading partners' "
        "currencies by trade. It is not ICE's US Dollar Index (DXY), which is "
        "a different, proprietary index.",
    ],
}
