"""Adapter: long-run prefecture population panel — Regional Statistics (SSDS).

Source: 総務省統計局, 社会・人口統計体系 都道府県データ, table 「Ａ　人口・世帯」
(e-Stat statsDataId 0000010101), through the e-Stat JSON API. This is the
Statistics Bureau's compiled regional indicator system: it takes figures
from the census, the population estimates, vital statistics, the migration
report and the Basic Resident Register and republishes them on one
prefecture grid, back to 1975.

Why it exists alongside `population-jp`. The resident-register workbooks
are the deep current cut — every register flow, both nationalities, five-year
age bands — but the ministry deletes last year's file, so that dataset can
only accumulate history going forward. This table is the *back* history:
fewer measures, but fifty years of them, and maintained by an API rather
than by a page whose links change every July. The two line up: SSDS's
register population for survey year Y is the 1 January Y+1 figure, and
124,330,690 at 1 Jan 2025 here is exactly the 123,767,642 at 1 Jan 2026 in
`population-jp` plus that dataset's 563,048 decline during 2025.

**Reference dates are the whole difficulty here, and the API does not carry
them.** A getMetaInfo response gives an indicator's code, name and unit and
nothing else — no reference date, no source note. Dating 594 indicators by
guesswork would silently shift whole series by a year, so this adapter
ingests only indicators whose reference date has been checked against a
published figure, and pins the rule per indicator:

- register series are dated **1 January of the survey year + 1** (checked
  against `population-jp`, exact to the person);
- estimate and census series are dated **1 October of the survey year**
  (A1101 for 2024 is 123,802,000, the published 1 Oct 2024 estimate);
- flows are dated **1 January of the year they cover** (A4101 for 2023 is
  727,288 births and A4200 is 1,576,016 deaths, the calendar-2023 vital
  statistics), matching how `population-jp` dates its register flows.

Two properties of the published data that gates must allow for:

- Census-year figures reconcile exactly; estimate-year figures are rounded
  to thousands, so the 47 prefectures miss the national row by a thousand
  or two. The gate is exact for register series and tolerant for estimates.
- The Bureau's own ageing rate (A1306) is computed on a denominator that
  excludes people of unrecorded age, so in census years it differs from
  A1303 ÷ A1101 by real amounts — 28.7% against 28.0% in 2020. That is not
  an error to fix; A1306 is used only as a loose cross-check outside census
  years.

**One published rate is stored, and only one.** The rule everywhere else on
the platform is that rates are calculated from stored counts so the formula
travels with the number, and every rate this table publishes that we could
reconstruct — the ageing rate, the natural change rate, the crude birth and
death rates — is left out for that reason. The total fertility rate (A4103)
is different in kind: it is a sum of age-specific rates over single years of
mother's age, and its denominator — the female population by single year of
age — is not in this table or in any other the platform holds. It cannot be
recomputed, so it is ingested exactly as the Bureau publishes it and carries
the `Official Statistic` badge on that basis. It is a rate, not a count, so
the 47 prefectures do not sum to the nation and the reconciliation gate skips
it — see NO_SUM.

**Births and the fertility rate run past the Bureau's last year.** This table
republishes the Ministry of Health, Labour and Welfare's vital statistics
about two years late (the 2026 edition ends at 2023). For the years after
its last, A4101 and A4103 are read from the Ministry's own final figures
(確定数) — e-Stat's database tables, then the Ministry's workbook for the
newest year — through mhlw_annual.py. Where both publish a year the Bureau's
value is kept; they matched for every year compared, and any difference past
0.1% is recorded on the release as a data-quality note. Prefecture
fertility rates arrive a couple of months after the national one; until
then that year is missing for the prefectures.

Counts of people are levels, not indices: `weight_per_10000` stays NULL.
Coverage varies a lot by indicator — population runs 1975–2024, the foreign
register only from 2013, households only in census years. Missing is
missing.
"""
import datetime
import json

from . import estat_api, mhlw_annual

STATS_DATA_ID = "0000010101"


class ValidationError(Exception):
    pass


# Dating rules, verified individually — see the module docstring.
#   register : 1 January of (survey year + 1)
#   asof_oct : 1 October of the survey year
#   flow     : 1 January of the survey year, the year the flow covers
INDICATORS = [
    # (e-Stat code, English name, dating rule)
    ("A2301", "Registered residents, total", "register"),
    ("A2101", "Registered residents, Japanese", "register"),
    ("A2201", "Registered residents, foreign", "register"),
    ("A7103", "Registered households, Japanese", "register"),

    ("A1101", "Total population", "asof_oct"),
    ("A1102", "Japanese population", "asof_oct"),
    ("A1301", "Population under 15", "asof_oct"),
    ("A1302", "Population 15–64", "asof_oct"),
    ("A1303", "Population 65 and over", "asof_oct"),
    ("A1700", "Foreign population", "asof_oct"),
    ("A1701", "Foreign population, Korea", "asof_oct"),
    ("A1702", "Foreign population, China", "asof_oct"),
    ("A1703", "Foreign population, United States", "asof_oct"),
    ("A1706", "Foreign population, Philippines", "asof_oct"),
    ("A1707", "Foreign population, Brazil", "asof_oct"),
    ("A7101", "Households", "asof_oct"),

    ("A4101", "Births", "flow"),
    ("A4200", "Deaths", "flow"),
    ("A5103", "In-migrants", "flow"),
    ("A5104", "Out-migrants", "flow"),
    ("A5101", "In-migrants, Japanese", "flow"),
    ("A5102", "Out-migrants, Japanese", "flow"),
    ("A5302", "Net migration", "flow"),

    # The one published rate the platform stores; see the module docstring.
    ("A4103", "Total fertility rate", "flow"),
]

# Indicators that are not counts of anything: the 47 prefectures do not add
# up to the nation, and nothing may sum them.
NO_SUM = ("A4103",)
INDICATOR_NAME = dict((code, name) for code, name, _rule in INDICATORS)
INDICATOR_RULE = dict((code, rule) for code, _name, rule in INDICATORS)
INDICATOR_ORDER = dict((code, i) for i, (code, _n, _r) in enumerate(INDICATORS))

# Register series reconcile to the person; estimate series are published
# rounded to thousands, so the 47 prefectures can miss the national row.
REGISTER_RULES = ("register",)
SUM_TOLERANCE = {"register": 0, "asof_oct": 50_000, "flow": 50_000}

# The Bureau's units, as the API reports them. A4103 is served with U+2010
# HYPHEN as its unit, the table's way of saying "a ratio, not a count".
UNIT = {"人": "persons", "世帯": "households",
        "‐": "births per woman"}

# Reused from the resident-register adapter so both population datasets speak
# the same geography: two-digit JIS codes, "00" for the national row.
from .juki_population import PREFECTURES, NATIONAL, REGIONS, REGION_OF  # noqa: E402

PREFECTURE_EN = dict((code, en) for code, _ja, en in PREFECTURES)
PREFECTURE_JA = dict((code, ja) for code, ja, _en in PREFECTURES)

DATASET = {
    "slug": "population-jp-history",
    "title": "Prefecture Population, Long Run — Japan (Regional Statistics)",
    "country": "Japan",
    "agency": "Statistics Bureau of Japan (Ministry of Internal Affairs and Communications)",
    "agency_ja": "総務省統計局",
    "base": None,
    "frequency": "annual",
    "description": (
        "Population, age structure, foreign residents, households, births, "
        "deaths and migration for all 47 prefectures and the national total, "
        "back to 1975, from the System of Social and Demographic Statistics "
        "(社会・人口統計体系) table A, Population and Households. Compiled by "
        "the Statistics Bureau from the census, the population estimates, "
        "vital statistics and the Basic Resident Register, so reference "
        "dates differ by indicator: register counts are as of 1 January of "
        "the following year, estimates as of 1 October, and births, deaths "
        "and migration are calendar-year flows, as is the total fertility "
        "rate, the one published rate carried here. Births and the fertility "
        "rate for the years after the Bureau's newest come from the Ministry "
        "of Health, Labour and Welfare's final vital statistics. Coverage "
        "varies by indicator — foreign register counts begin in 2013, the "
        "fertility rate in 1980, and household counts appear only in census "
        "years."
    ),
}

SOURCE = {
    "source_id": "e-stat:0000010101",
    "name": ("System of Social and Demographic Statistics, prefectural data — "
             "A: Population and Households; births and fertility rate after its "
             "newest year from the MHLW Vital Statistics, final figures"),
    "name_ja": "社会・人口統計体系 都道府県データ Ａ　人口・世帯",
    "url": "https://www.e-stat.go.jp/dbview?sid=0000010101",
    "license_note": (
        "e-Stat terms of use: reuse permitted with attribution to the "
        "Statistics Bureau of Japan. Retrieved through the e-Stat API, which "
        "requires a free application ID."
    ),
}

DOWNLOAD_URL = ("https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
                "?statsDataId=" + STATS_DATA_ID)

RAW_SUFFIX = ".json"


# --- fetch ------------------------------------------------------------------

def fetch():
    """Every value of the pinned indicators, as the API returned them, and the
    Ministry of Health, Labour and Welfare's births and fertility tables that
    carry those two series past the Bureau's last year (mhlw_annual.py).

    The archived artifact is the sources' own responses, not a reshaped
    extract, so the evidence for a release is what the source actually said.
    """
    pages = estat_api.get_stats_data(
        STATS_DATA_ID, cdCat01=",".join(code for code, _n, _r in INDICATORS))
    try:
        mhlw = mhlw_annual.fetch()
    except mhlw_annual.SourceError as exc:
        raise ValidationError("vital statistics: %s" % exc)
    return json.dumps({"ssds": pages, "mhlw": mhlw},
                      ensure_ascii=False, sort_keys=True).encode("utf-8")


def canonical_bytes(raw):
    """The artifact with the API's served-at timestamps removed (the
    Ministry's workbook is a static file and is compared as it is).

    Every response carries RESULT.DATE, so two identical downloads never
    match byte for byte and the runner would publish a new release each
    boot. Comparing canonical content makes "nothing new" mean it.
    """
    return json.dumps(estat_api.strip_timestamps(json.loads(raw.decode("utf-8"))),
                      ensure_ascii=False, sort_keys=True).encode("utf-8")


# --- parse ------------------------------------------------------------------

def _geo(area_code):
    """e-Stat area code -> our two-digit geography. 00000 is the nation."""
    if area_code == "00000":
        return NATIONAL[0]
    if len(area_code) != 5 or not area_code.isdigit():
        raise ValidationError("unreadable area code %r" % area_code)
    if not area_code.endswith("000"):
        raise ValidationError(
            "area %s is below prefecture level; this table should be "
            "prefectural only" % area_code)
    return area_code[:2]


def _period(rule, year):
    if rule == "register":
        return datetime.date(year + 1, 1, 1)
    if rule == "asof_oct":
        return datetime.date(year, 10, 1)
    if rule == "flow":
        return datetime.date(year, 1, 1)
    raise ValidationError("unknown dating rule %r" % rule)


def _year(time_code, time_name):
    """The survey year. The name ("2024年度") is authoritative; the code is
    checked against it so a change in either is caught."""
    digits = "".join(c for c in time_name if c.isdigit())[:4]
    if len(digits) != 4:
        raise ValidationError("cannot read a year from %r" % time_name)
    year = int(digits)
    if time_code[:4] != digits:
        raise ValidationError(
            "time code %r disagrees with its name %r" % (time_code, time_name))
    if not (1900 < year < 2100):
        raise ValidationError("implausible survey year %d" % year)
    return year


# Births and the fertility rate, the two series the Ministry's tables extend.
MHLW_INDICATORS = {"births": "A4101", "tfr": "A4103"}
# A value both sources publish for the same year is the same figure, and
# should match; one that differs by more than this is recorded on the release.
DISCREPANCY_SHARE = 0.001
PARSE_NOTES = {}


def _mhlw_values(mhlw):
    """{(geo, indicator, year): (value, source)} from the Ministry's tables,
    the database ahead of the workbook, plus the checks they passed."""
    prefectures = [(code, ja) for code, ja, _en in PREFECTURES]
    codes = [c for c, _ja in prefectures]
    out, notes = {}, []
    try:
        tfr, _ = mhlw_annual.parse_estat(mhlw["estat"]["tfr"], "tfr", codes)
        births, reconciled = mhlw_annual.parse_estat(mhlw["estat"]["births"], "births", codes)
        wb_births, wb_tfr, wb_prior = mhlw_annual.parse_workbook(mhlw["workbook"], prefectures)
    except (mhlw_annual.SourceError, KeyError) as exc:
        raise ValidationError("vital statistics: %s" % exc)
    for (geo, year), v in tfr.items():
        out[(geo, "A4103", year)] = (v, "e-Stat %s" % mhlw["estat"]["tfr"]["id"])
    for (geo, year), v in births.items():
        out[(geo, "A4101", year)] = (v, "e-Stat %s" % mhlw["estat"]["births"]["id"])
    wb = "MHLW workbook %d" % mhlw["workbook"]["year"]
    for (geo, year), v in wb_births.items():
        out.setdefault((geo, "A4101", year), (v, wb))
    for (geo, year), v in wb_tfr.items():
        out.setdefault((geo, "A4103", year), (v, wb))
    # The workbook's prior-year column against the database's figure.
    newest = mhlw["workbook"]["year"]
    prior = ([(geo, "A4101", year, v) for (geo, year), v in wb_prior.items()]
             + [(geo, "A4103", year, v) for (geo, year), v in wb_tfr.items()
                if year != newest])
    for geo, ind, year, v in prior:
        have = out.get((geo, ind, year))
        if have and abs(have[0] - v) > DISCREPANCY_SHARE * abs(have[0]):
            notes.append("%s.%s %d: workbook %s, database %s" % (geo, ind, year, v, have[0]))
    return out, notes, reconciled


def parse(raw_bytes):
    payload = json.loads(raw_bytes.decode("utf-8"))
    # Artifacts archived before the Ministry's tables were added are the
    # Bureau's pages alone.
    if isinstance(payload, list):
        pages, mhlw = payload, None
    else:
        pages, mhlw = payload["ssds"], payload["mhlw"]
    if not pages:
        raise ValidationError("no pages in the artifact")

    series = {}
    observations = []
    times = {}
    seen_indicators = set()

    for page in pages:
        data = page["GET_STATS_DATA"]["STATISTICAL_DATA"]
        cat_names = estat_api.class_values(data, "cat01")
        area_names = estat_api.class_values(data, "area")
        time_names = estat_api.class_values(data, "time")
        values = data["DATA_INF"]["VALUE"]
        if isinstance(values, dict):
            values = [values]
        for v in values:
            code = v["@cat01"]
            if code not in INDICATOR_RULE:
                raise ValidationError(
                    "the API returned indicator %s, which was not requested" % code)
            seen_indicators.add(code)
            raw_value = v.get("$")
            # e-Stat marks a figure that does not exist with a symbol rather
            # than a number ("-", "…", "***"). Those are missing, never zero.
            try:
                value = float(str(raw_value).replace(",", ""))
            except (TypeError, ValueError):
                continue
            geo = _geo(v["@area"])
            year = _year(v["@time"], time_names[v["@time"]])
            times[year] = True
            rule = INDICATOR_RULE[code]
            unit = UNIT.get(v.get("@unit"))
            if unit is None:
                raise ValidationError(
                    "indicator %s has unit %r, which has no mapping — a unit "
                    "change means the indicator changed" % (code, v.get("@unit")))
            key = "%s.%s" % (geo, code)
            if key not in series:
                pref_en = (NATIONAL[2] if geo == NATIONAL[0]
                           else PREFECTURE_EN.get(geo))
                pref_ja = (NATIONAL[1] if geo == NATIONAL[0]
                           else PREFECTURE_JA.get(geo))
                if pref_en is None:
                    raise ValidationError("unknown prefecture code %s" % geo)
                # The API prefixes its own name with the code ("A2301_住民基本
                # 台帳人口（総数）"); the prefix is already the series code.
                label_ja = cat_names[code].split("_", 1)[-1]
                order = (int(geo) * 1000 + INDICATOR_ORDER[code])
                series[key] = {
                    "code": key,
                    "name_en": "%s — %s" % (pref_en, INDICATOR_NAME[code]),
                    "name_ja": "%s %s" % (pref_ja, label_ja),
                    "unit": unit,
                    "weight_per_10000": None,
                    "sort_order": order,
                }
            observations.append(
                {"code": key, "period": _period(rule, year), "value": value})

    missing = sorted(set(INDICATOR_RULE) - seen_indicators)
    if missing:
        raise ValidationError(
            "the table no longer carries %d requested indicators: %s"
            % (len(missing), missing))
    if not observations:
        raise ValidationError("no values parsed")

    # The Ministry's figures, for the years the Bureau's table does not yet
    # carry. Where both have a year the Bureau's value stays — it is the
    # same figure republished — and a difference past 0.1% is recorded on
    # the release as a data-quality note rather than resolved.
    PARSE_NOTES.clear()
    if mhlw is not None:
        held = dict(((o["code"], o["period"]), o["value"]) for o in observations)
        # Only years after the Bureau's newest for that indicator nationally:
        # the Ministry's tables reach further back too (the fertility rate
        # from 1960), but extending history backwards is not what they are
        # here for, and an earlier year is compared, not added.
        bureau_last = {}
        for (key, period) in held:
            geo, code = key.split(".", 1)
            if geo == NATIONAL[0] and code in MHLW_INDICATORS.values():
                bureau_last[code] = max(bureau_last.get(code, 0), period.year)
        extra, notes, reconciled = _mhlw_values(mhlw)
        added = {}
        for (geo, code, year), (value, source) in sorted(extra.items()):
            key = "%s.%s" % (geo, code)
            period = _period(INDICATOR_RULE[code], year)
            if (key, period) in held:
                bureau = held[(key, period)]
                if abs(bureau - value) > DISCREPANCY_SHARE * abs(bureau):
                    notes.append("%s %d: Statistics Bureau %s, Ministry (%s) %s"
                                 % (key, year, bureau, source, value))
                continue
            if year <= bureau_last.get(code, 0):
                continue
            if key not in series:
                raise ValidationError("the Ministry's tables carry %s, which the "
                                      "Bureau's table has no series for" % key)
            observations.append({"code": key, "period": period, "value": value})
            added.setdefault(code, set()).add(year)
        PARSE_NOTES.update({
            "vital_statistics_years_added": dict(
                (code, sorted(years)) for code, years in sorted(added.items())),
            "vital_statistics_sources": {
                "births": mhlw["estat"]["births"]["id"],
                "fertility": mhlw["estat"]["tfr"]["id"],
                "workbook": mhlw["workbook"]["url"]},
            "vital_statistics_births_years_reconciled": reconciled,
            "data_quality_notes": notes,
        })
    return sorted(series.values(), key=lambda s: s["sort_order"]), observations


# --- validate ---------------------------------------------------------------

FIRST_YEAR = 1975
POPULATION_MIN = 100_000_000
POPULATION_MAX = 140_000_000

# The fertility rate is published, not computed, so the gate can only ask
# whether it is a possible number. Japan's national rate was 1.75 in 1980
# and 1.14 in 2025, the lowest in the series; Tokyo has been under 1.0 and Okinawa
# over 2.3. The band is wide enough for a real move and narrow enough to
# catch a column read off by one.
TFR_MIN = 0.5
TFR_MAX = 3.0
TFR_MIN_YEARS = 40


def validate(series, observations):
    value = {}
    for o in observations:
        key = (o["code"], o["period"])
        if key in value:
            raise ValidationError("duplicate observation %s %s" % key)
        value[key] = o["value"]

    prefecture_codes = [code for code, _ja, _en in PREFECTURES]
    geographies = [NATIONAL[0]] + prefecture_codes

    # 1. Every prefecture and the national row are present for the anchor
    #    series — a table that quietly lost an area is worse than one that fails.
    for geo in geographies:
        if not any(k[0] == "%s.A1101" % geo for k in value):
            raise ValidationError("no total-population series for area %s" % geo)

    # 2. The 47 prefectures against the national row. Exact for the register
    #    series; estimate series are published rounded to thousands.
    checked = 0
    worst = {}
    for code, _name, rule in INDICATORS:
        if code in NO_SUM:
            continue      # a rate; the prefectures were never meant to add up
        tolerance = SUM_TOLERANCE[rule]
        periods = sorted(set(k[1] for k in value if k[0] == "%s.%s" % (NATIONAL[0], code)))
        for period in periods:
            national = value.get(("%s.%s" % (NATIONAL[0], code), period))
            parts = [value[("%s.%s" % (geo, code), period)] for geo in prefecture_codes
                     if ("%s.%s" % (geo, code), period) in value]
            if national is None or len(parts) != len(prefecture_codes):
                continue      # a year the prefectures do not all cover
            gap = abs(national - sum(parts))
            if gap > tolerance:
                raise ValidationError(
                    "%s %s: the 47 prefectures sum to %d, the national row "
                    "says %d (off by %d, tolerance %d)"
                    % (code, period, sum(parts), national, gap, tolerance))
            worst[rule] = max(worst.get(rule, 0), gap)
            checked += 1
    if not checked:
        raise ValidationError("no national/prefecture reconciliation was possible")

    # 3. The register identity, which holds to the person wherever all three
    #    series exist: total residents = Japanese + foreign.
    identity = 0
    for geo in geographies:
        for period in sorted(set(k[1] for k in value if k[0] == "%s.A2301" % geo)):
            total = value.get(("%s.A2301" % geo, period))
            japanese = value.get(("%s.A2101" % geo, period))
            foreign = value.get(("%s.A2201" % geo, period))
            if None in (total, japanese, foreign):
                continue
            if total != japanese + foreign:
                raise ValidationError(
                    "%s %s: registered total %d but Japanese %d + foreign %d"
                    % (geo, period, total, japanese, foreign))
            identity += 1
    if identity < 400:
        raise ValidationError(
            "only %d register identities could be checked; the overlapping "
            "years have shrunk unexpectedly" % identity)

    # 4. Age groups against the total population, on the same 1 October basis.
    #    Census years carry people of unrecorded age, so the parts may fall
    #    short of the whole — but they may never exceed it.
    for geo in geographies:
        for period in sorted(set(k[1] for k in value if k[0] == "%s.A1101" % geo)):
            total = value[("%s.A1101" % geo, period)]
            parts = [value.get(("%s.%s" % (geo, code), period))
                     for code in ("A1301", "A1302", "A1303")]
            if any(p is None for p in parts):
                continue
            if sum(parts) > total + SUM_TOLERANCE["asof_oct"]:
                raise ValidationError(
                    "%s %s: age groups total %d, above the population %d"
                    % (geo, period, sum(parts), total))

    # 5. The fertility rate. It is stored as published and cannot be checked
    #    against anything else in the table, so the gate is a plausibility
    #    band and a coverage floor: Japan's national rate has run between
    #    1.15 and 2.0 since 1980 and no prefecture has left 0.8-2.5, and a
    #    published series that suddenly loses its history is a fault.
    fertility = sorted((p, geo, v) for (c, p), v in value.items()
                       for geo in [c.split(".")[0]] if c.endswith(".A4103"))
    for period, geo, rate in fertility:
        if not (TFR_MIN <= rate <= TFR_MAX):
            raise ValidationError(
                "fertility rate %.2f for area %s at %s is outside the "
                "plausibility band %.1f-%.1f" % (rate, geo, period, TFR_MIN, TFR_MAX))
    national_fertility = [(p, v) for p, geo, v in fertility if geo == NATIONAL[0]]
    if len(national_fertility) < TFR_MIN_YEARS:
        raise ValidationError(
            "the national fertility rate has only %d years; at least %d were "
            "published before" % (len(national_fertility), TFR_MIN_YEARS))
    fertility_areas = set(geo for _p, geo, _v in fertility)
    if len(fertility_areas) < len(geographies):
        raise ValidationError(
            "only %d of %d areas carry a fertility rate"
            % (len(fertility_areas), len(geographies)))

    # 6. Sanity and coverage.
    national_population = [
        (p, v) for (c, p), v in value.items() if c == "%s.A1101" % NATIONAL[0]]
    national_population.sort()
    for period, level in national_population:
        if not (POPULATION_MIN <= level <= POPULATION_MAX):
            raise ValidationError(
                "national population %d at %s outside the sanity band"
                % (level, period))
    first, last = national_population[0][0], national_population[-1][0]
    if first.year > FIRST_YEAR:
        raise ValidationError(
            "history starts %s, expected %d — the table has been truncated"
            % (first, FIRST_YEAR))
    if last.year < datetime.date.today().year - 3:
        raise ValidationError(
            "the newest total population is %s, implausibly old" % last)

    latest = max(o["period"] for o in observations)
    return {
        "series": len(series),
        "observations": len(observations),
        "latest_period": latest.isoformat(),
        "indicators": len(INDICATORS),
        "areas": len(geographies),
        "population_first_year": first.year,
        "population_latest": latest.isoformat(),
        "reconciliations": checked,
        "register_identities": identity,
        "fertility_years": len(national_fertility),
        "fertility_first_year": national_fertility[0][0].year,
        "fertility_latest_year": national_fertility[-1][0].year,
        "worst_sum_gap": worst,
        **PARSE_NOTES,
    }


PRESENTATION = {
    "credit_line": ("Source: Statistics Bureau of Japan — System of Social and "
                    "Demographic Statistics; births and fertility rate after its "
                    "newest year, Ministry of Health, Labour and Welfare — Vital "
                    "Statistics, final figures."),
    # Annual, republished each June, and each edition's newest period is
    # already about eighteen months old when it lands: the June 2026 release
    # tops out at 1 January 2025. So the newest period is legitimately up to
    # 910 days old just before the next release. This allows that plus about
    # six weeks' grace, and flags a June release that never came.
    "stale_after_days": 950,
    "prefectures": {
        "headline": "A1101",
        "national": NATIONAL[0],
        "geographies": (
            [{"code": NATIONAL[0], "name_ja": NATIONAL[1], "name_en": NATIONAL[2],
              "region": None}]
            + [{"code": code, "name_ja": ja, "name_en": en,
                "region": REGION_OF[code]} for code, ja, en in PREFECTURES]),
        "regions": [{"key": key, "label": en, "label_ja": ja,
                     "prefectures": list(codes)}
                    for key, en, ja, codes in REGIONS],
        # `kind` separates the counts from the one published rate: a surface
        # that summed, ranked or aggregated a rate as if it were a count
        # would be wrong, and nothing should have to infer that from a code.
        "indicators": [{"code": code, "label": name, "basis": rule,
                        "kind": "rate" if code in NO_SUM else "count"}
                       for code, name, rule in INDICATORS],
        # What each dating rule means, so a surface can say it rather than
        # implying every series shares one reference date.
        "bases": {
            "register": "Basic Resident Register, as of 1 January of the year shown.",
            "asof_oct": "Census or population estimate, as of 1 October of the year shown.",
            "flow": "Count over the calendar year shown.",
        },
        "companion_dataset": "population-jp",
    },
}


# The dataset's card. Long-run counts with indicator-specific reference dates
# (served as `bases` on /prefectures) — a surface must say which applies.
MANIFEST = {
    "id": DATASET["slug"],
    "section": "demography",
    "name": {"en": "Prefecture population, long run — regional statistics",
             "ja": "社会・人口統計体系 都道府県データ（人口・世帯）"},
    "shape": "series",
    "summary": ("Population, age structure, foreign residents, households, "
                "births, deaths and migration for all 47 prefectures and the "
                "nation back to 1975, compiled by the Statistics Bureau from "
                "the census, population estimates, vital statistics and the "
                "Basic Resident Register."),
    "source": {
        "publisher": DATASET["agency"],
        "publisher_ja": DATASET["agency_ja"],
        "document": SOURCE["name"],
        "url": SOURCE["url"],
        "credit": PRESENTATION["credit_line"],
        "license_note": SOURCE["license_note"],
    },
    "keys": ["series_code", "period"],
    "frequency": DATASET["frequency"],
    "vintage": {
        "unit": "release", "as_of_basis": "release-in-force",
        "as_of_supported": True, "history_from": "1975",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published count (persons, households, or a calendar-year flow)",
         "unit": "persons", "trust": "official"},
        {"id": "yoy", "label": "Year over year", "unit": "%", "trust": "derived",
         "where": "annual series: t−12 months is the previous year",
         "calc": "(value[t] / value[t−12 months] − 1) × 100, from published values."},
        # Published, never recomputed — so it carries no formula, only the
        # note saying whose figure it is and why it is stored at all.
        {"id": "fertility", "label": "Total fertility rate (A4103)",
         "unit": "births_per_woman", "trust": "official",
         "where": ("the Statistics Bureau's own figure, the sum of birth rates "
                   "at each single year of a mother's age 15–49; its "
                   "denominator, the female population by single year of age, "
                   "is in no table held here, so it is stored as published "
                   "rather than calculated. A rate, never summed across areas. "
                   "Prefectures: 1980, 1985, then annually from 1986")},
        {"id": "birth_rate", "label": "Crude birth rate", "unit": "per_1000",
         "trust": "derived",
         "calc": ("births (A4101, calendar year) ÷ Japanese population "
                  "(A1102, 1 October of the same year) × 1,000")},
        {"id": "death_rate", "label": "Crude death rate", "unit": "per_1000",
         "trust": "derived",
         "calc": ("deaths (A4200, calendar year) ÷ Japanese population "
                  "(A1102, 1 October of the same year) × 1,000")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "prefectures": "/api/v1/%s/prefectures" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/population.html",
    "page": "/population.html",
    "notes": [
        "Reference dates differ by indicator: register counts are as of 1 January "
        "of the following year, estimates as of 1 October, and births, deaths and "
        "migration are calendar-year flows. /prefectures serves the rule per series.",
        "Coverage varies by indicator — foreign register counts begin in 2013 and "
        "household counts appear only in census years. Gaps are gaps, never zero.",
        "Shares and change rates are computed on the page from these counts and "
        "carry their formula there.",
        "One published rate is stored rather than calculated: the total fertility "
        "rate (A4103), whose denominator — female population by single year of age "
        "— is not in this table. It is served exactly as the Bureau publishes it, "
        "is a rate rather than a count, and must never be summed across areas.",
        "Births (A4101) and the fertility rate (A4103) for the years after the "
        "Bureau's newest — 2024 and 2025 at November 2026 — are the Ministry of "
        "Health, Labour and Welfare's final figures (確定数), read from its e-Stat "
        "tables and its own workbook. Where both sources publish a year the "
        "Bureau's value is kept; differences past 0.1% are listed on the release "
        "under data_quality_notes. Prefecture fertility rates for the newest year "
        "follow the national figure by a couple of months and are missing until "
        "then.",
    ],
}
