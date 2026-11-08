"""Adapter: Labour Force Survey — by age and sex, Japan.

Source: 総務省統計局 労働力調査 基本集計, long-run tables 1 a-2, a-6, a-9 and
b-1, b-2, b-4, b-5, b-6 「【年齢階級（10歳階級）別】 － 全国、月別結果」: the
whole-Japan monthly results by ten-year age group, for both sexes, men and
women, read from the Excel files e-Stat serves under fixed statInfIds. The
Bureau replaces each file in place on every release, the same day as table
1 a-1 (lfs_unemployment.py), so the ids do not move.

Eight measures, each for nine age groups and three sexes (216 series):
population aged 15 and over, labour force, employed, unemployed and people
not in the labour force (headcounts), and the unemployment, participation
and employment rates. Researchers asked for the labour market by age and
sex; the a-1 table has sex only.

Table 1 b-3 「雇用者【年齢階級、雇用形態別】」 adds employees by type of
employment from January 2013 (273 series): employees with and without
executives, regular and non-regular staff, the non-regular groups (part-time,
arbeit, dispatched, contract, entrusted, other) and the regular and
non-regular shares, for seven age groups and three sexes. One sheet per sex,
each age group thirteen columns wide.

Facts the parser and the gates rely on:

- Only the original series (原数値) exists for every age group, so that is
  the sheet taken, from July 1972, when Okinawa joined the survey. The
  1968–1972 sheets without Okinawa and the seasonally adjusted sheets some
  of these files carry (totals and a few groups only) are not taken.
- The age columns are 総数 (all aged 15 and over), 15～64歳, the ten-year
  groups 15～24 to 55～64, 65歳以上, and 20～69歳, which the Bureau prints
  as a 再掲 (a subtotal shown again): it overlaps the others and is never
  added to them.
- Headcounts are in 万人 as published and stored in that unit; rates are
  percentages to one decimal. Months before July 1972 are ``…``, missing.
- March–August 2011 are the Bureau's supplementary estimates, published in
  the series and kept; October 2005 – December 2021 are its re-benchmarked
  figures, where components need not add up exactly (see lfs_unemployment).

Levels and rates, not indices: ``weight_per_10000`` stays NULL.
"""
import datetime

from . import bls_flat, estat_csv, lfs_unemployment, xlsx

# The same exception as table a-1's adapter, whose date and value readers
# this one shares, so the ingest runner catches either as a failed validation.
ValidationError = lfs_unemployment.ValidationError

DATASET = {
    "slug": "labour-force-age-jp",
    "title": "Labour Force Survey — By Age and Sex, Japan (Statistics Bureau)",
    "country": "Japan",
    "agency": "Statistics Bureau of Japan",
    "agency_ja": "総務省統計局",
    "base": None,
    "frequency": "monthly",
    "description": (
        "Japan's labour force by ten-year age group and sex from the Statistics "
        "Bureau's Labour Force Survey: population aged 15 and over, labour force, "
        "employed, unemployed and people outside the labour force, with the "
        "unemployment, participation and employment rates, monthly from July "
        "1972; and employees by type of employment (regular and non-regular "
        "staff, part-time, arbeit, dispatched, contract and entrusted) with the "
        "non-regular share, monthly from January 2013. Original series, for both "
        "sexes, men and women. Headcounts in units of ten thousand persons, "
        "exactly as published."
    ),
}

SOURCE = {
    "source_id": "e-stat:lfs-long-run-1-age",
    "name": ("Labour Force Survey, Basic Tabulation — historical data 1 a-2, a-6, "
             "a-9, b-1, b-2, b-3, b-4, b-5, b-6, by age group, whole Japan, monthly"),
    "name_ja": ("労働力調査 基本集計 長期時系列表１ ａ－２・ａ－６・ａ－９・ｂ－１・"
                "ｂ－２・ｂ－３・ｂ－４・ｂ－５・ｂ－６ 【年齢階級（10歳階級）別】（全国、月別結果）"),
    "url": "https://www.e-stat.go.jp/stat-search/files?tclass=000001226526&cycle=0",
    "license_note": (
        "e-Stat / Statistics Bureau terms of use: reuse permitted with attribution "
        "to the Statistics Bureau of Japan."
    ),
}

FILE_URL = "https://www.e-stat.go.jp/stat-search/file-download?statInfId=%s&fileKind=0"

# (table, statInfId, key, English, Japanese name in the table title, unit, kind)
TABLES = [
    ("ｂ－１", "000031831366", "population_15plus", "Population",
     "15歳以上人口", "persons_10k", "stock"),
    ("ｂ－２", "000031831367", "labour_force", "Labour force",
     "労働力人口", "persons_10k", "stock"),
    ("ａ－２", "000031831359", "employed", "Employed persons",
     "就業者", "persons_10k", "stock"),
    ("ａ－６", "000031831362", "unemployed", "Unemployed persons",
     "完全失業者", "persons_10k", "stock"),
    ("ｂ－４", "000031831369", "not_in_labour_force", "Not in labour force",
     "非労働力人口", "persons_10k", "stock"),
    ("ａ－９", "000031831365", "unemployment_rate", "Unemployment rate",
     "完全失業率", "percent", "rate"),
    ("ｂ－５", "000031897991", "participation_rate", "Labour force participation rate",
     "労働力人口比率", "percent", "rate"),
    ("ｂ－６", "000031897992", "employment_rate", "Employment rate",
     "就業率", "percent", "rate"),
]
DOWNLOAD_URL = FILE_URL % TABLES[0][1]
RAW_SUFFIX = ".zip"
SHEET = "原数値"

# (column in the both-sexes block, key, English, header row, header text)
AGES = [
    ("E", "total", "aged 15 and over", 7, "総数"),
    ("F", "15-64", "aged 15–64", 7, "15～64歳"),
    ("G", "15-24", "aged 15–24", 8, "15～24"),
    ("H", "25-34", "aged 25–34", 8, "25～34"),
    ("I", "35-44", "aged 35–44", 8, "35～44"),
    ("J", "45-54", "aged 45–54", 8, "45～54"),
    ("K", "55-64", "aged 55–64", 8, "55～64"),
    ("L", "65plus", "aged 65 and over", 7, "65歳以上"),
    ("M", "20-69", "aged 20–69", 8, "20～69歳"),
]
# The ten-year groups that make up the total, oldest last.
PARTS = ["15-24", "25-34", "35-44", "45-54", "55-64", "65plus"]
# Each sex block is nine columns wide.
SEXES = [("total", "both sexes", "男女計", 0), ("male", "men", "男", 9),
         ("female", "women", "女", 18)]

# Table 1 b-3, employees by type of employment: one sheet per sex, seven age
# groups thirteen columns wide, monthly from January 2013.
EMPLOYMENT = ("ｂ－３", "000031831368", "employment_type")
EMPLOYMENT_FROM = datetime.date(2013, 1, 1)
EMPLOYMENT_SHEETS = [("total", "both sexes", "男女計"), ("male", "men", "男"),
                     ("female", "women", "女")]
# (first column, key, English, the header the block starts with)
EMPLOYMENT_AGES = [
    ("D", "total", "aged 15 and over", "総数"),
    ("Q", "15-24", "aged 15–24", "15～24歳"),
    ("AD", "25-34", "aged 25–34", "25～34歳"),
    ("AQ", "35-44", "aged 35–44", "35～44歳"),
    ("BD", "45-54", "aged 45–54", "45～54歳"),
    ("BQ", "55-64", "aged 55–64", "55～64歳"),
    ("CD", "65plus", "aged 65 and over", "65歳以上"),
]
# (offset in the block, key, English as the Bureau translates it, header row,
#  Japanese header, unit, kind). Shares are of employees excluding executives.
EMPLOYMENT_MEASURES = [
    (0, "employees", "Employees", 9, "雇用者", "persons_10k", "stock"),
    (1, "employees_excl_executives", "Employees excluding executives", 10,
     "役員を除く雇用者", "persons_10k", "stock"),
    (2, "regular", "Regular staff", 11, "正規の職員・従業員", "persons_10k", "stock"),
    (3, "non_regular", "Non-regular staff", 11, "非正規の職員・従業員", "persons_10k", "stock"),
    (4, "part_time_and_arbeit", "Part-time workers and arbeit (temporary workers)", 12,
     "パート・アルバイト", "persons_10k", "stock"),
    (5, "part_time", "Part-time workers", 13, "パート", "persons_10k", "stock"),
    (6, "arbeit", "Arbeit (temporary workers)", 13, "アルバイト", "persons_10k", "stock"),
    (7, "dispatched", "Dispatched workers from temporary labour agencies", 12,
     "労働者派遣事業所の派遣社員", "persons_10k", "stock"),
    (8, "contract", "Contract employees", 12, "契約社員", "persons_10k", "stock"),
    (9, "entrusted", "Entrusted employees", 12, "嘱託", "persons_10k", "stock"),
    (10, "other", "Other", 12, "その他", "persons_10k", "stock"),
    (11, "regular_share", "Regular staff, share of employees excluding executives", 9,
     "正規の職員・従業員", "percent", "rate"),
    (12, "non_regular_share", "Non-regular staff, share of employees excluding executives",
     9, "非正規の職員・従業員", "percent", "rate"),
]


def _col(letter, n):
    """Column `letter` shifted n columns right ('W' + 8 = 'AE')."""
    i = 0
    for ch in letter:
        i = i * 26 + ord(ch) - 64
    i += n
    out = ""
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _code(measure, age, sex):
    return "%s.%s.%s" % (measure, age, sex)


# --- fetch ------------------------------------------------------------------

def fetch():
    files = {}
    for table, sid, key, _en, _ja, _u, _k in TABLES:
        raw = estat_csv.fetch_bytes(FILE_URL % sid)
        # A moved file comes back as an HTML page with status 200.
        if raw[:2] != b"PK":
            raise ValidationError("table %s (%s) is not an .xlsx workbook (got %r…)"
                                  % (table, sid, raw[:16]))
        files[key + ".xlsx"] = raw
    table, sid, key = EMPLOYMENT
    raw = estat_csv.fetch_bytes(FILE_URL % sid)
    if raw[:2] != b"PK":
        raise ValidationError("table %s (%s) is not an .xlsx workbook (got %r…)"
                              % (table, sid, raw[:16]))
    files[key + ".xlsx"] = raw
    return bls_flat.bundle(files)


# --- parse ------------------------------------------------------------------

_text = lfs_unemployment._text


def _norm(text):
    return text.strip().replace("(", "（").replace(")", "）")


def _check_header(table, ja, unit, rows):
    title = _text(rows.get(1, {}), "E")
    if table not in title or ja not in title or "10歳階級" not in title:
        raise ValidationError("table %s: title %r is not the %s by age group" % (table, title, ja))
    want = "万人" if unit == "persons_10k" else "％"
    if want not in _text(rows.get(4, {}), "E"):
        raise ValidationError("table %s: unit is no longer %s" % (table, want))
    if "原数値" not in _text(rows.get(5, {}), "E"):
        raise ValidationError("table %s: sheet %s does not hold the original series" % (table, SHEET))
    for sex, _en, sex_ja, off in SEXES:
        got = _text(rows.get(6, {}), _col("E", off)).strip()
        if got != sex_ja:
            raise ValidationError("table %s column %s: expected %r, found %r"
                                  % (table, _col("E", off), sex_ja, got))
        for col, _key, _en, row, label in AGES:
            got = _norm(_text(rows.get(row, {}), _col(col, off)))
            if got != label:
                raise ValidationError("table %s column %s: expected %r, found %r"
                                      % (table, _col(col, off), label, got))
        got = _norm(_text(rows.get(7, {}), _col("M", off)))
        if got != "（再掲）":
            raise ValidationError("table %s: 20～69歳 is no longer a 再掲 subtotal" % table)


def parse(raw_bytes):
    try:
        files = bls_flat.unbundle(raw_bytes)
    except Exception as exc:
        raise ValidationError("not a readable bundle: %s" % exc)
    series, observations = [], []
    order = 0
    for table, _sid, key, en, ja, unit, _kind in TABLES:
        name = key + ".xlsx"
        if name not in files:
            raise ValidationError("bundle has no %s" % name)
        try:
            sheets = xlsx.sheets(files[name])
        except Exception as exc:
            raise ValidationError("table %s is not a readable workbook: %s" % (table, exc))
        if SHEET not in sheets:
            raise ValidationError("table %s has no sheet %r (sheets: %s)"
                                  % (table, SHEET, ", ".join(sheets)))
        rows = sheets[SHEET]
        _check_header(table, ja, unit, rows)
        dated = lfs_unemployment._dated_rows("%s %s" % (table, SHEET), rows)
        for sex, sex_en, sex_ja, off in SEXES:
            for col, age, age_en, _r, label in AGES:
                c = _col(col, off)
                code = _code(key, age, sex)
                n = 0
                for r, period in dated:
                    v = lfs_unemployment._value(_text(rows[r], c))
                    if v is None:
                        continue
                    observations.append({"code": code, "period": period, "value": v})
                    n += 1
                if n:
                    series.append({
                        "code": code,
                        "name_en": "%s, %s, %s" % (en, sex_en, age_en),
                        "name_ja": "%s %s %s" % (ja, sex_ja, label),
                        "unit": unit,
                        "weight_per_10000": None,
                        "sort_order": order,
                    })
                order += 1
    _parse_employment(files, series, observations, order)
    if not observations:
        raise ValidationError("no values parsed")
    return series, observations


def _flat(text):
    return "".join(text.split())


def _parse_employment(files, series, observations, order):
    """Table 1 b-3 into the same lists: employees by type of employment."""
    table, _sid, key = EMPLOYMENT
    name = key + ".xlsx"
    if name not in files:
        raise ValidationError("bundle has no %s" % name)
    try:
        sheets = xlsx.sheets(files[name])
    except Exception as exc:
        raise ValidationError("table %s is not a readable workbook: %s" % (table, exc))
    for sex, sex_en, sheet in EMPLOYMENT_SHEETS:
        if sheet not in sheets:
            raise ValidationError("table %s has no sheet %r (sheets: %s)"
                                  % (table, sheet, ", ".join(sheets)))
        rows = sheets[sheet]
        title = _text(rows.get(1, {}), "D")
        if table not in title or "雇用形態" not in title:
            raise ValidationError("table %s: title %r is not employees by type" % (table, title))
        if "万人" not in _text(rows.get(8, {}), "L") or "％" not in _text(rows.get(8, {}), "P"):
            raise ValidationError("table %s: units are no longer 万人 and ％" % table)
        for start, age, _en, label in EMPLOYMENT_AGES:
            got = _text(rows.get(7, {}), start)
            if not got.strip().startswith(label):
                raise ValidationError("table %s sheet %s column %s: expected %r, found %r"
                                      % (table, sheet, start, label, got))
            for off, _k, _e, row, ja, _u, _kind in EMPLOYMENT_MEASURES:
                got = _flat(_text(rows.get(row, {}), _col(start, off)))
                if got != ja:
                    raise ValidationError("table %s sheet %s column %s: expected %r, found %r"
                                          % (table, sheet, _col(start, off), ja, got))
        dated = lfs_unemployment._dated_rows("%s %s" % (table, sheet), rows)
        for start, age, age_en, label in EMPLOYMENT_AGES:
            for off, mkey, en, _row, ja, unit, _kind in EMPLOYMENT_MEASURES:
                c = _col(start, off)
                code = _code(mkey, age, sex)
                n = 0
                for r, period in dated:
                    v = lfs_unemployment._value(_text(rows[r], c))
                    if v is None:
                        continue
                    observations.append({"code": code, "period": period, "value": v})
                    n += 1
                if n:
                    series.append({
                        "code": code,
                        "name_en": "%s, %s, %s" % (en, sex_en, age_en),
                        "name_ja": "%s %s %s" % (ja, sheet, label),
                        "unit": unit,
                        "weight_per_10000": None,
                        "sort_order": order,
                    })
                order += 1


# --- validate ---------------------------------------------------------------

STALE_AFTER_DAYS = 100    # a month is published ~4 weeks after it ends
START = datetime.date(1972, 7, 1)
REBENCHMARKED = lfs_unemployment.REBENCHMARKED
# Tolerances below were measured on the 2026-11-08 files, in 万人 (counts)
# and percentage points (rates), and are the worst case seen plus nothing:
# outside the re-benchmarked window, sums of rounded components miss by at
# most 4 and every rate matches its counts within rounding; inside it the
# Bureau re-estimated each cell on its own (sums miss by up to 5, rates by up
# to 0.7 points beyond rounding).
SUM_TOLERANCE = (4.0, 5.0)        # (outside, inside the window)
# Population counts people whose labour status is unknown, so it is never
# less than labour force plus not in labour force, give or take rounding.
POPULATION_SHORTFALL = (5.0, 8.0)
RATE_EXCESS = (0.0, 1.0)
# The Bureau leaves a rate blank where the unemployed in a group are too few
# to publish (women 65 and over before 1975, now and then women 55–64).
GAPS_ALLOWED = "unemployment_rate"
# Table b-3 (from 2013): measured the same way. Outside the window every sum
# misses by at most 2; inside it by up to 6, and employees excluding
# executives misses regular + non-regular staff by 9 in January 2013, the
# first month of the tabulation. Shares add to 100 within their rounding.
B3_SUM = (2.0, 6.0)
B3_SPLIT = (2.0, 9.0)
SHARE_PAIR = 0.11
# The detailed non-regular groups go blank where they are too small to
# publish (entrusted employees, men 15–24, now and then).
B3_GAPS_ALLOWED = ("part_time", "arbeit", "part_time_and_arbeit", "dispatched",
                   "contract", "entrusted", "other")


def _window(period, pair):
    return pair[1] if REBENCHMARKED[0] <= period <= REBENCHMARKED[1] else pair[0]


def _rate_bound(num, den):
    """How far a published rate can sit from num / den × 100 when the rate is
    printed to 0.1 and both counts are rounded to whole 万人."""
    return 0.05 + 100 * (0.5 / den + num * 0.5 / den / den)


def validate(series, observations):
    col = {}
    for o in observations:
        held = col.setdefault(o["code"], {})
        if o["period"] in held:
            raise ValidationError("duplicate observation %s %s" % (o["code"], o["period"]))
        held[o["period"]] = o["value"]
    expected = set(_code(t[2], a[1], s[0]) for t in TABLES for a in AGES for s in SEXES)
    missing = expected - set(col)
    if missing:
        raise ValidationError("%d series missing, e.g. %s"
                              % (len(missing), ", ".join(sorted(missing)[:5])))

    # 1. Every series ends in the same recent month; all but the small rates
    #    run unbroken from July 1972.
    def months_between(a, b):
        return (b.year - a.year) * 12 + b.month - a.month + 1

    latest = max(col["labour_force.total.total"])
    if (datetime.date.today() - latest).days > 400:
        raise ValidationError("newest month %s is implausibly old" % latest)
    for code in expected:
        ps = col[code]
        if max(ps) != latest:
            raise ValidationError("%s ends %s, the release runs to %s" % (code, max(ps), latest))
        if code.startswith(GAPS_ALLOWED + "."):
            continue
        if min(ps) != START or len(ps) != months_between(START, latest):
            raise ValidationError("%s is not unbroken from %s: %d months, first %s"
                                  % (code, START, len(ps), min(ps)))

    # 2. Ranges: rates are percentages; headcounts are millions, not people.
    units = dict((t[2], t[5]) for t in TABLES)
    units.update((m[1], m[5]) for m in EMPLOYMENT_MEASURES)
    for code, ps in col.items():
        rate = units[code.split(".")[0]] == "percent"
        for p, v in ps.items():
            if rate and not (0 <= v <= 100):
                raise ValidationError("%s %s: rate %.1f%% out of range" % (code, p, v))
            if not rate and not (0 <= v <= 12000):
                raise ValidationError("%s %s: %.0f (万人) out of range" % (code, p, v))

    # 3. Identities: labour force = employed + unemployed; population covers
    #    labour force + not in labour force; both sexes = men + women; the six
    #    ten-year groups add to the total; each rate matches its counts.
    get = lambda m, a, s: col[_code(m, a, s)]
    checked = 0
    for a in [x[1] for x in AGES]:
        for s in [x[0] for x in SEXES]:
            lf, emp, un = get("labour_force", a, s), get("employed", a, s), get("unemployed", a, s)
            pop, nilf = get("population_15plus", a, s), get("not_in_labour_force", a, s)
            for p, v in lf.items():
                if p in emp and p in un and abs(v - (emp[p] + un[p])) > _window(p, SUM_TOLERANCE):
                    raise ValidationError("%s %s %s: labour force %.0f ≠ employed %.0f + "
                                          "unemployed %.0f" % (a, s, p, v, emp[p], un[p]))
                if p in pop and p in nilf and pop[p] - (v + nilf[p]) < -_window(p, POPULATION_SHORTFALL):
                    raise ValidationError("%s %s %s: population %.0f < labour force %.0f + "
                                          "not in labour force %.0f" % (a, s, p, pop[p], v, nilf[p]))
                checked += 1
            for rate, num, den in (("unemployment_rate", un, lf),
                                   ("participation_rate", lf, pop),
                                   ("employment_rate", emp, pop)):
                for p, v in get(rate, a, s).items():
                    n, d = num.get(p), den.get(p)
                    if n is None or not d:
                        continue
                    gap = abs(v - n / d * 100) - _rate_bound(n, d)
                    if gap > _window(p, RATE_EXCESS):
                        raise ValidationError("%s %s %s %s: %.1f%% but the counts give %.2f%%"
                                              % (rate, a, s, p, v, n / d * 100))
                    checked += 1
    for m in [t[2] for t in TABLES if t[5] == "persons_10k"]:
        for p in col[_code(m, "total", "total")]:
            for a in [x[1] for x in AGES]:
                t, men, women = (get(m, a, s).get(p) for s in ("total", "male", "female"))
                if None not in (t, men, women) and abs(t - (men + women)) > _window(p, SUM_TOLERANCE):
                    raise ValidationError("%s %s %s: both sexes %.0f ≠ men %.0f + women %.0f"
                                          % (m, a, p, t, men, women))
            for s in [x[0] for x in SEXES]:
                parts = [get(m, a, s).get(p) for a in PARTS]
                t = get(m, "total", s).get(p)
                if t is not None and None not in parts and abs(t - sum(parts)) > _window(p, SUM_TOLERANCE):
                    raise ValidationError("%s %s %s: total %.0f ≠ sum of age groups %.0f"
                                          % (m, s, p, t, sum(parts)))
            checked += 1
    checked += _validate_employment(col, latest, months_between)
    # About 113 a month: 27 group sums, 81 rates and 5 age and sex totals,
    # and over 200 a month from 2013 for table b-3.
    if checked < 100 * months_between(START, latest):
        raise ValidationError("only %d identities checked" % checked)

    last = lambda code: col[code][latest]
    return {
        "series": len(series),
        "observations": len(observations),
        "latest_period": latest.isoformat(),
        "identities_checked": checked,
        "unemployment_rate_15_24": last("unemployment_rate.15-24.total"),
        "participation_rate_women_25_34": last("participation_rate.25-34.female"),
        "non_regular_share_women": last("non_regular_share.total.female"),
    }


def _validate_employment(col, latest, months_between):
    """Table b-3's gates; returns how many identities it checked."""
    expected = set(_code(m[1], a[1], s[0]) for m in EMPLOYMENT_MEASURES
                   for a in EMPLOYMENT_AGES for s in EMPLOYMENT_SHEETS)
    missing = expected - set(col)
    if missing:
        raise ValidationError("%d employment-type series missing, e.g. %s"
                              % (len(missing), ", ".join(sorted(missing)[:5])))
    for code in expected:
        ps = col[code]
        if max(ps) != latest:
            raise ValidationError("%s ends %s, the release runs to %s" % (code, max(ps), latest))
        if code.split(".")[0] in B3_GAPS_ALLOWED:
            continue
        if min(ps) != EMPLOYMENT_FROM or len(ps) != months_between(EMPLOYMENT_FROM, latest):
            raise ValidationError("%s is not unbroken from %s: %d months, first %s"
                                  % (code, EMPLOYMENT_FROM, len(ps), min(ps)))
    get = lambda m, a, s: col[_code(m, a, s)]
    ages = [x[1] for x in EMPLOYMENT_AGES]
    sexes = [x[0] for x in EMPLOYMENT_SHEETS]
    sums = [("employees_excl_executives", ["regular", "non_regular"], B3_SPLIT),
            ("non_regular", ["part_time_and_arbeit", "dispatched", "contract",
                             "entrusted", "other"], B3_SUM),
            ("part_time_and_arbeit", ["part_time", "arbeit"], B3_SUM)]
    checked = 0
    for a in ages:
        for s in sexes:
            for whole, parts, tol in sums:
                for p, v in get(whole, a, s).items():
                    vals = [get(x, a, s).get(p) for x in parts]
                    if None in vals:
                        continue
                    if abs(v - sum(vals)) > _window(p, tol):
                        raise ValidationError("%s %s %s: %s %.0f ≠ %s %.0f"
                                              % (a, s, p, whole, v, " + ".join(parts), sum(vals)))
                    checked += 1
            excl = get("employees_excl_executives", a, s)
            for p, v in get("employees", a, s).items():
                if p in excl and v < excl[p] - 1:
                    raise ValidationError("%s %s %s: employees %.0f < employees excluding "
                                          "executives %.0f" % (a, s, p, v, excl[p]))
            reg, non = get("regular_share", a, s), get("non_regular_share", a, s)
            for p, v in reg.items():
                if p in non and abs(v + non[p] - 100) > SHARE_PAIR:
                    raise ValidationError("%s %s %s: shares %.1f + %.1f ≠ 100"
                                          % (a, s, p, v, non[p]))
                n, d = get("regular", a, s).get(p), excl.get(p)
                if n is not None and d:
                    if abs(v - n / d * 100) - _rate_bound(n, d) > _window(p, RATE_EXCESS):
                        raise ValidationError("regular share %s %s %s: %.1f%% but the counts "
                                              "give %.2f%%" % (a, s, p, v, n / d * 100))
                checked += 1
    for m in [x[1] for x in EMPLOYMENT_MEASURES if x[5] == "persons_10k"]:
        for p in get(m, "total", "total"):
            for a in ages:
                t, men, women = (get(m, a, s).get(p) for s in sexes)
                if None not in (t, men, women) and abs(t - (men + women)) > _window(p, B3_SUM):
                    raise ValidationError("%s %s %s: both sexes %.0f ≠ men %.0f + women %.0f"
                                          % (m, a, p, t, men, women))
            for s in sexes:
                parts = [get(m, a, s).get(p) for a in ages[1:]]
                t = get(m, "total", s).get(p)
                if t is not None and None not in parts and abs(t - sum(parts)) > _window(p, B3_SUM):
                    raise ValidationError("%s %s %s: total %.0f ≠ sum of age groups %.0f"
                                          % (m, s, p, t, sum(parts)))
            checked += 1
    return checked


# --- presentation -----------------------------------------------------------

KINDS = dict((_code(t[2], a[1], s[0]), t[6]) for t in TABLES for a in AGES for s in SEXES)
KINDS.update((_code(m[1], a[1], s[0]), m[6]) for m in EMPLOYMENT_MEASURES
             for a in EMPLOYMENT_AGES for s in EMPLOYMENT_SHEETS)

PRESENTATION = {
    "credit_line": "Source: Statistics Bureau of Japan, Labour Force Survey.",
    "stale_after_days": STALE_AFTER_DAYS,
    "overview_tiles": [
        {"key": "young", "code": "unemployment_rate.15-24.total",
         "label": "Unemployment rate, aged 15–24", "type": "level"},
        {"key": "women", "code": "participation_rate.25-34.female",
         "label": "Participation rate, women 25–34", "type": "level"},
        {"key": "older", "code": "employment_rate.65plus.total",
         "label": "Employment rate, aged 65 and over", "type": "level"},
        {"key": "non_regular", "code": "non_regular_share.total.female",
         "label": "Non-regular share, women employees", "type": "level"},
    ],
    # One unit on one chart: the unemployment rate for each ten-year group.
    "main_series": [
        {"role": "15-24", "code": "unemployment_rate.15-24.total",
         "label": "Aged 15–24", "slot": 1},
        {"role": "25-34", "code": "unemployment_rate.25-34.total",
         "label": "Aged 25–34", "slot": 2},
        {"role": "35-44", "code": "unemployment_rate.35-44.total",
         "label": "Aged 35–44", "slot": 3},
        {"role": "45-54", "code": "unemployment_rate.45-54.total",
         "label": "Aged 45–54", "slot": 4},
        {"role": "55-64", "code": "unemployment_rate.55-64.total",
         "label": "Aged 55–64", "slot": 5},
        {"role": "65plus", "code": "unemployment_rate.65plus.total",
         "label": "Aged 65 and over", "slot": 6},
    ],
    "kinds": KINDS,
}


MANIFEST = {
    "id": DATASET["slug"],
    "section": "labour",
    "name": {"en": "Labour force and employment type by age and sex — Labour Force Survey",
             "ja": "労働力調査 年齢階級別（10歳階級）"},
    "shape": "series",
    "summary": DATASET["description"],
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
        "as_of_supported": True, "history_from": "1972-07",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published value (headcounts in ten thousand persons; "
                                 "rates in %)",
         "unit": "persons_10k", "trust": "official"},
        {"id": "yoy", "label": "Change on a year earlier", "unit": "%", "trust": "derived",
         "where": "headcounts only; a rate is already a percentage and its change is "
                  "read in points",
         "calc": "(value[t] / value[t−12 months] − 1) × 100, from published values."},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "summary": "/api/v1/%s/overview" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search", "summary"],
    "cite": "/labour.html?dataset=labour-force-age-jp",
    "page": "/labour.html",
    "notes": [
        "Codes are measure.age.sex: measures population_15plus, labour_force, employed, "
        "unemployed, not_in_labour_force, unemployment_rate, participation_rate, "
        "employment_rate; ages total, 15-64, 15-24, 25-34, 35-44, 45-54, 55-64, 65plus "
        "and 20-69; sexes total, male, female.",
        "Original series only (not seasonally adjusted), monthly from July 1972; for "
        "annual figures average the twelve months or use the Bureau's annual tables.",
        "20-69 is the Bureau's 再掲 subtotal: it overlaps the other age groups and is "
        "never added to them.",
        "Headcounts are in ten thousand persons (万人), as published.",
        "Population includes people whose labour status is unknown, so it can exceed "
        "labour force plus not in labour force.",
        "A rate is left blank where the unemployed in a group are too few to publish "
        "(women 65 and over before 1975).",
        "Employment type (from January 2013): codes employees, employees_excl_executives, "
        "regular, non_regular, part_time_and_arbeit, part_time, arbeit, dispatched, "
        "contract, entrusted, other, and regular_share and non_regular_share, which are "
        "percentages of employees excluding executives. Ages total, 15-24 to 55-64 and "
        "65plus. A small group is left blank where the Bureau does not publish it.",
        "March–August 2011 are the Bureau's supplementary estimates after the Great "
        "East Japan Earthquake; October 2005 – December 2021 are its re-benchmarked "
        "figures, where components need not add up exactly.",
    ],
}
