"""Adapter: population by single year of age and sex, Japan (人口推計).

Source: 総務省統計局 人口推計, population at 1 October by single year of age
and sex, for the total population and for Japanese nationals, in thousands
(千人) as published. These are the denominators for births: Japanese women
aged 25–39 were 9,002 thousand at 1 October 2024, 1.6% fewer than a year
before.

Three sources, joined on the year, each used only where it is the best one:

- 1980–1999, total population only: the long-run table 我が国の推計人口
  （大正9年～平成12年） 第4表 (e-Stat file 000000090264), single ages to 89 and
  one band for 90 and over.
- 2000–2020, total and Japanese: the long-run table 長期時系列（平成12年～
  令和2年） 第4表 (e-Stat file 000013168604), adjusted between censuses (補間
  補正), single ages to 99 and 100 and over (90 and over until 2004).
- 2021 onwards: the newest database table titled 年齢（各歳），男女別人口及び
  人口性比－総人口，日本人人口, found by title and newest publication, never
  pinned — each census base gets a new table id.

**Japanese nationals by single year of age start in 2000 here.** e-Stat holds
them from 1991 in five more tables in two layouts (1991–94 one table a year,
1995–99 one table), estimated on the census base of the day and never
adjusted to the next census; the 1974–1989 editions are PDF only. They are
not loaded.

Facts the parser and the gates rely on:

- The top band moves: 90 and over until 2004, 100 and over from 2005. Each
  is its own series (ages.90plus, ages.100plus); nothing is split or summed
  to fake the other.
- In the 2000–2015 sheet the 90-and-over figure for 2000–2004 is printed on
  the row labelled 95, with 90–94 blank. A year whose 90–94 are blank and has
  one figure further down is read as 90 and over; the reconciliation below
  proves it (126,228 + 702 = 126,930 against the published 126,926 for 2000).
- Every age is rounded to the thousand, so single ages sum to the published
  total within a few thousand, never exactly. Each year must reconcile to
  within 15 thousand, and male + female must equal both sexes at each age
  to within one thousand. Japanese nationals never exceed the total.
- Where two sources carry a year (2000 and 2015 in the long run, 2020 in the
  database and the long run) the later source wins, and any difference is
  recorded on the release.

The derived bands (five-year bands, any range such as women 25–39) are sums
of these published single years, calculated by /api/v1/{dataset}/age-bands
with their formula; nothing derived is stored.
"""
import datetime
import json
import re
import unicodedata
import urllib.request

from . import bls_flat, estat_api, xlsx


class ValidationError(Exception):
    pass


STATS_CODE = "00200524"
# e-Stat file ids and the fileKind each is served under (the 2000–2020 file
# answers 404 to kind 0 and is served as kind 4).
LONG_RUN_1920 = ("000000090264", 0)
LONG_RUN_2000 = ("000013168604", 4)
FILE_URL = "https://www.e-stat.go.jp/stat-search/file-download?statInfId=%s&fileKind=%d"
TABLE_TITLE = "年齢（各歳），男女別人口及び人口性比－総人口，日本人人口"
USER_AGENT = "ObservatoryIngest/0.1 (data pipeline; contact: repo owner)"

DATASET = {
    "slug": "population-age-jp",
    "title": "Population by Single Year of Age and Sex — Japan (Population Estimates)",
    "country": "Japan",
    "agency": "Statistics Bureau of Japan (Ministry of Internal Affairs and Communications)",
    "agency_ja": "総務省統計局",
    "base": None,
    "frequency": "annual",
    "description": (
        "Japan's population at 1 October by single year of age and sex, total "
        "population from 1980 and Japanese nationals from 2000, in thousands, "
        "from the Statistics Bureau's population estimates (人口推計): the "
        "long-run tables, adjusted between censuses, then the current annual "
        "table. Five-year bands and any age range — women aged 25–39, the "
        "denominator for births — are calculated from the single years."
    ),
}

SOURCE = {
    "source_id": "e-stat:00200524-age",
    "name": ("Population Estimates — population by age (single year) and sex, "
             "1 October: long-run tables 1920–2000 and 2000–2020, and the current "
             "annual table"),
    "name_ja": "人口推計 年齢（各歳），男女別人口（各年10月1日現在）－総人口，日本人人口",
    "url": "https://www.e-stat.go.jp/stat-search/files?toukei=00200524",
    "license_note": (
        "e-Stat terms of use: reuse permitted with attribution to the "
        "Statistics Bureau of Japan. The current table is located through the "
        "e-Stat API, which requires a free application ID."
    ),
}

DOWNLOAD_URL = SOURCE["url"]
RAW_SUFFIX = ".zip"

NATIONALITIES = [("total", "Total population", "総人口"),
                 ("jp", "Japanese nationals", "日本人人口")]
SEXES = [("t", "both sexes", "男女計"), ("m", "male", "男"), ("f", "female", "女")]
SEX_BY_JA = dict((ja, key) for key, _en, ja in SEXES)
AGES = [str(a) for a in range(100)] + ["90plus", "100plus", "all"]
FIRST_YEAR = {"total": 1980, "jp": 2000}


def _norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def code(nat, sex, age):
    return "%s.%s.%s" % (nat, sex, age)


# --- fetch ------------------------------------------------------------------

def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def _current_table():
    """statsDataId of the database table with the single-age title that reaches
    the latest year.

    Several tables share the title, one per census base, and the newest to
    be *published* is not the one that reaches furthest: the intercensal
    revision of 2016–2020 opened after the table that runs to 2024. So each
    candidate's time axis is read and the furthest wins.
    """
    payload = estat_api.call("getStatsList", statsCode=STATS_CODE,
                             searchWord=TABLE_TITLE, limit=100)
    tables = payload["GET_STATS_LIST"]["DATALIST_INF"].get("TABLE_INF") or []
    if isinstance(tables, dict):
        tables = [tables]
    best = None
    for t in tables:
        title = t["TITLE"]["$"] if isinstance(t["TITLE"], dict) else t["TITLE"]
        if _norm(title) != _norm(TABLE_TITLE):
            continue
        meta = estat_api.call("getMetaInfo", statsDataId=t["@id"])
        years = []
        for obj in meta["GET_META_INFO"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"]:
            if obj["@id"] == "time":
                entries = obj["CLASS"] if isinstance(obj["CLASS"], list) else [obj["CLASS"]]
                years = [int(m.group(1)) for e in entries
                         for m in [re.match(r"^(\d{4})年10月1日", e["@name"])] if m]
        if not years:
            continue
        key = (max(years), str(t.get("OPEN_DATE")), t["@id"])
        if best is None or key > best:
            best = key
    if best is None:
        raise ValidationError("e-Stat lists no table titled %s" % TABLE_TITLE)
    return best[2]


def fetch():
    files = {}
    for ref, name in ((LONG_RUN_1920, "long_run_1920.xlsx"),
                      (LONG_RUN_2000, "long_run_2000.xlsx")):
        raw = _get(FILE_URL % ref)
        if raw[:2] != b"PK":
            raise ValidationError("file %s is not an .xlsx workbook" % ref[0])
        files[name] = raw
    table = _current_table()
    pages = estat_api.get_stats_data(table, cdCat01="001,002,003")
    files["current.json"] = json.dumps(
        {"id": table, "pages": estat_api.strip_timestamps(pages)},
        ensure_ascii=False, sort_keys=True).encode("utf-8")
    files["edition.json"] = json.dumps(
        {"long_run_1920": FILE_URL % LONG_RUN_1920,
         "long_run_2000": FILE_URL % LONG_RUN_2000,
         "current_table": table}, sort_keys=True).encode("utf-8")
    return bls_flat.bundle(files)


# --- parse ------------------------------------------------------------------

def _age(label):
    t = _norm(label)
    if t == "総数":
        return "all"
    m = re.match(r"^(\d+)歳以上$", t)
    if m:
        return "%splus" % m.group(1)
    m = re.match(r"^(\d+)(歳)?$", t)
    if m and int(m.group(1)) < 100:
        return m.group(1)
    return None


def parse_long_run(raw, sheet_filter, nat, name):
    """{(nat, sex, age, year): thousands} from the long-run sheets whose name
    contains `sheet_filter` (総人口 / 日本人人口, or the 1980 sheet)."""
    sheets = xlsx.sheets(raw)
    out = {}
    for sname, grid in sheets.items():
        if not sheet_filter(sname):
            continue
        text = lambda r, c: xlsx.cell_text(grid.get(r, {}).get(c))
        years, sexes, rows = {}, {}, {}
        for r in sorted(grid):
            cells = dict((c, _norm(xlsx.cell_text(v))) for c, v in grid[r].items())
            if not years:
                found = dict((c, int(m.group(1))) for c, v in cells.items() if c != "A"
                             for m in [re.search(r"(\d{4})", v)] if m)
                if len(found) >= 5:
                    years = found
                continue
            if not sexes:
                if "男女計" in cells.values():
                    sexes = dict((c, SEX_BY_JA[v]) for c, v in cells.items() if v in SEX_BY_JA)
                continue
            age = _age(text(r, "A"))
            if age is not None:
                rows[age] = r
        if not years or not sexes or "all" not in rows:
            raise ValidationError("%s / %s: no year, sex or total heading" % (name, sname))
        letters = sorted(sexes, key=lambda c: (len(c), c))
        for ycol, year in years.items():
            # A year's block is its own column and the two after it: 男女計, 男, 女.
            block = letters[letters.index(ycol): letters.index(ycol) + 3]
            if [sexes[c] for c in block] != ["t", "m", "f"]:
                raise ValidationError("%s / %s: %d block is %s" % (
                    name, sname, year, [sexes[c] for c in block]))
            for col, sex in zip(block, ("t", "m", "f")):
                vals = {}
                for age, r in rows.items():
                    v = text(r, col).strip()
                    if v:
                        try:
                            vals[age] = float(v.replace(",", ""))
                        except ValueError:
                            raise ValidationError("%s / %s: %r at %s%d" % (name, sname, v, col, r))
                # The 2000–2004 trap: 90 and over printed on a lower row.
                if ("90plus" not in vals and "100plus" not in vals
                        and not any(str(a) in vals for a in range(90, 95))):
                    lower = [a for a in [str(x) for x in range(95, 100)] if a in vals]
                    if len(lower) == 1:
                        vals["90plus"] = vals.pop(lower[0])
                for age, v in vals.items():
                    out[(nat, sex, age, year)] = v
    if not out:
        raise ValidationError("%s: no values" % name)
    return out


def parse_current(blob):
    out = {}
    for page in blob["pages"]:
        data = page["GET_STATS_DATA"]["STATISTICAL_DATA"]
        names = dict((k, estat_api.class_values(data, k)) for k in ("cat01", "cat02", "cat03", "time"))
        values = data["DATA_INF"]["VALUE"]
        for v in [values] if isinstance(values, dict) else values:
            sex = SEX_BY_JA.get(names["cat01"].get(v["@cat01"]))
            nat = {"総人口": "total", "日本人人口": "jp"}.get(names["cat02"].get(v["@cat02"]))
            age = _age(names["cat03"].get(v["@cat03"], ""))
            m = re.match(r"^(\d{4})年10月1日現在$", names["time"].get(v["@time"], ""))
            if sex is None or nat is None or age is None or m is None:
                raise ValidationError("current table: unreadable cell %r" % v)
            try:
                out[(nat, sex, age, int(m.group(1)))] = float(v["$"])
            except (TypeError, ValueError):
                continue
    if not out:
        raise ValidationError("current table: no values")
    return out


PARSE_NOTES = {}


def parse(raw_bytes):
    try:
        files = bls_flat.unbundle(raw_bytes)
        current = json.loads(files["current.json"].decode("utf-8"))
    except Exception as exc:
        raise ValidationError("not a bundle: %s" % exc)
    layers = [
        ("long run 1920–2000", parse_long_run(files["long_run_1920.xlsx"],
                                             lambda s: "昭和55" in s, "total", "1920")),
        ("long run 2000–2015", dict(
            list(parse_long_run(files["long_run_2000.xlsx"],
                                lambda s: s.startswith("総人口") and "2000" in s, "total",
                                "2000").items())
            + list(parse_long_run(files["long_run_2000.xlsx"],
                                  lambda s: s.startswith("日本人人口") and "2000" in s, "jp",
                                  "2000").items()))),
        ("long run 2015–2020", dict(
            list(parse_long_run(files["long_run_2000.xlsx"],
                                lambda s: s.startswith("総人口") and "2015年～" in s, "total",
                                "2015").items())
            + list(parse_long_run(files["long_run_2000.xlsx"],
                                  lambda s: s.startswith("日本人人口") and "2015年～" in s, "jp",
                                  "2015").items()))),
        ("table %s" % current["id"], parse_current(current)),
    ]
    merged, source_of, overlaps, differences = {}, {}, 0, []
    for label, values in layers:
        # A year a later layer carries is that layer's, whole: no year is
        # stitched from two sources.
        years = set((k[0], k[3]) for k in values)
        for key in [k for k in merged if (k[0], k[3]) in years]:
            later = values.get(key)
            if later is not None:
                overlaps += 1
                if later != merged[key]:
                    differences.append("%s %d: %s %s, %s %s" % (
                        code(*key[:3]), key[3], source_of[(key[0], key[3])],
                        merged[key], label, later))
            del merged[key]
        for key, v in values.items():
            merged[key] = v
        for ny in years:
            source_of[ny] = label
    PARSE_NOTES.clear()
    PARSE_NOTES.update({
        "sources_by_year": dict(("%s %d" % ny, lab) for ny, lab in sorted(source_of.items())
                                if ny[1] in (1980, 1999, 2000, 2004, 2005, 2015, 2020,
                                             max(y for _n, y in source_of))),
        "overlapping_values_compared": overlaps,
        "overlap_differences": len(differences),
        "overlap_difference_examples": differences[:10],
    })

    series, observations = [], []
    present = set((n, s, a) for n, s, a, _y in merged)
    order = 0
    for nat, nat_en, nat_ja in NATIONALITIES:
        for sex, sex_en, sex_ja in SEXES:
            for age in AGES:
                if (nat, sex, age) in present:
                    age_en = ("all ages" if age == "all" else
                              "aged %s and over" % age[:-4] if age.endswith("plus")
                              else "aged %s" % age)
                    age_ja = ("総数" if age == "all" else
                              "%s歳以上" % age[:-4] if age.endswith("plus") else "%s歳" % age)
                    series.append({
                        "code": code(nat, sex, age),
                        "name_en": "%s, %s, %s" % (nat_en, sex_en, age_en),
                        "name_ja": "%s %s %s" % (nat_ja, sex_ja, age_ja),
                        "unit": "persons_1000", "weight_per_10000": None,
                        "sort_order": order,
                    })
                order += 1
    for (nat, sex, age, year), v in sorted(merged.items()):
        if year < FIRST_YEAR[nat]:
            continue
        observations.append({"code": code(nat, sex, age),
                             "period": datetime.date(year, 10, 1), "value": v})
    return series, observations


# --- validate ---------------------------------------------------------------

RECONCILE_TOLERANCE = 15.0     # thousand: ~100 ages each rounded to the thousand
STALE_AFTER_DAYS = 800         # a census year's single ages come about 18 months on


def validate(series, observations):
    val = {}
    for o in observations:
        nat, sex, age = o["code"].split(".")
        key = (nat, sex, age, o["period"].year)
        if key in val:
            raise ValidationError("duplicate observation %s %s" % (o["code"], o["period"]))
        val[key] = o["value"]
    years = {}
    for nat, _s, _a, y in val:
        years.setdefault(nat, set()).add(y)
    latest = max(years["total"])
    for nat, first in FIRST_YEAR.items():
        ys = sorted(years.get(nat, ()))
        if not ys or ys[0] != first or ys != list(range(first, ys[-1] + 1)):
            raise ValidationError("%s years run %s, expected unbroken from %d"
                                  % (nat, ys[:3] + ["…"] + ys[-3:] if ys else ys, first))
        if ys[-1] != latest:
            raise ValidationError("%s ends %d, total ends %d" % (nat, ys[-1], latest))
    if latest < datetime.date.today().year - 3:
        raise ValidationError("newest year %d is implausibly old" % latest)

    reconciled = 0
    for nat in years:
        for year in years[nat]:
            for sex, *_rest in SEXES:
                total = val.get((nat, sex, "all", year))
                if total is None:
                    raise ValidationError("%s %s %d: no published total" % (nat, sex, year))
                singles = [val.get((nat, sex, str(a), year)) for a in range(90)]
                if None in singles:
                    raise ValidationError("%s %s %d: a single age under 90 is missing"
                                          % (nat, sex, year))
                if (nat, sex, "100plus", year) in val:
                    top = [val.get((nat, sex, str(a), year)) for a in range(90, 100)]
                    if None in top:
                        raise ValidationError("%s %s %d: 100+ published but an age "
                                              "90–99 is missing" % (nat, sex, year))
                    tail = sum(top) + val[(nat, sex, "100plus", year)]
                elif (nat, sex, "90plus", year) in val:
                    tail = val[(nat, sex, "90plus", year)]
                else:
                    raise ValidationError("%s %s %d: no open top band" % (nat, sex, year))
                gap = abs(sum(singles) + tail - total)
                if gap > RECONCILE_TOLERANCE:
                    raise ValidationError(
                        "%s %s %d: ages sum to %.0f thousand, published total %.0f — "
                        "a column or band is misread" % (nat, sex, year, sum(singles) + tail, total))
                reconciled += 1
    # male + female = both sexes, and Japanese never above the total.
    for (nat, sex, age, year), v in val.items():
        if sex == "t":
            m, f = val.get((nat, "m", age, year)), val.get((nat, "f", age, year))
            if m is not None and f is not None and abs(m + f - v) > 1.0:
                raise ValidationError("%s age %s %d: male %s + female %s != %s"
                                      % (nat, age, year, m, f, v))
        if nat == "jp":
            t = val.get(("total", sex, age, year))
            if t is not None and v > t + 1.0:
                raise ValidationError("age %s %s %d: Japanese %s above total %s"
                                      % (age, sex, year, v, t))
    women = sum(val[("jp", "f", str(a), latest)] for a in range(25, 40))
    if not 5000 <= women <= 15000:
        raise ValidationError("Japanese women 25–39 in %d: %.0f thousand is implausible"
                              % (latest, women))
    return {
        "series": len(series), "observations": len(observations),
        "latest_period": datetime.date(latest, 10, 1).isoformat(),
        "years_total": "%d–%d" % (min(years["total"]), latest),
        "years_japanese": "%d–%d" % (min(years["jp"]), latest),
        "years_reconciled": reconciled,
        "japanese_women_25_39_latest": women,
        **PARSE_NOTES,
    }


# --- presentation -----------------------------------------------------------

PRESENTATION = {
    "credit_line": "Source: Statistics Bureau of Japan — Population Estimates (人口推計).",
    "stale_after_days": STALE_AFTER_DAYS,
    "kind_default": "stock",
    # What /age-bands needs to sum single years into bands: the series code
    # pattern and the open top bands, so the endpoint knows nothing about
    # this dataset beyond what it declares.
    "ages": {
        "nationalities": [{"key": k, "label": en} for k, en, _ja in NATIONALITIES],
        "sexes": [{"key": k, "label": en} for k, en, _ja in SEXES],
        "code": "{nationality}.{sex}.{age}",
        "single_max": 99,
        "open_bands": ["90plus", "100plus"],
    },
    "overview_tiles": [
        {"key": "jp_women_total", "code": "jp.f.all",
         "label": "Japanese women, all ages", "type": "level"},
        {"key": "total", "code": "total.t.all", "label": "Total population",
         "type": "level"},
    ],
}


MANIFEST = {
    "id": DATASET["slug"],
    "section": "demography",
    "name": {"en": "Population by single year of age and sex — population estimates",
             "ja": "人口推計 年齢（各歳）男女別人口"},
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
        "as_of_supported": True, "history_from": "1980",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published population at 1 October (thousands)",
         "unit": "persons_1000", "trust": "official"},
        {"id": "age_band", "label": "Population in an age band (thousands)",
         "unit": "persons_1000", "trust": "derived",
         "calc": ("sum of the published single-year counts from age a to age b "
                  "(and the open top band where the range reaches it); a year "
                  "whose published ages do not cover the whole range is left out")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "summary": "/api/v1/%s/overview" % DATASET["slug"],
        "age_bands": "/api/v1/%s/age-bands" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search", "summary"],
    "cite": "/population.html?dataset=population-age-jp#h-ages",
    "page": "/population.html",
    "notes": [
        "Codes are nationality.sex.age: total or jp (Japanese nationals); t, m or f; "
        "an age 0–99, all, or an open top band 90plus (to 2004) or 100plus (from "
        "2005). Values are thousands of people at 1 October, as published.",
        "Total population runs from 1980, Japanese nationals from 2000. Japanese "
        "single ages for 1991–1999 exist on e-Stat in older tables on the census "
        "base of the day and are not loaded.",
        "Every age is rounded to the thousand, so a band summed from single years "
        "can differ from a band the Bureau publishes by a few thousand.",
        "/age-bands?nationality=jp&sex=f&from=25&to=39 gives women aged 25–39; "
        "without from/to it gives five-year bands. Both are calculated and carry "
        "their formula.",
    ],
}
