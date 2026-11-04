"""Adapter: monthly vital statistics, Japan (MHLW 人口動態統計).

Births, deaths, natural change, marriages and divorces, month by month, in
the two monthly releases the Ministry of Health, Labour and Welfare
publishes. They count different people, so they are kept as separate series
and never merged:

- **速報 (preliminary)**, about two months after the month: every event
  reported in Japan, *including foreign residents*, plus Japanese abroad.
  The press's "births fell again" headline is usually this one. From the
  Ministry's own site (人口動態統計速報, one workbook a month), January 2005
  onwards: each workbook carries its own year and the two before it.
- **概数 (approximate)**, about five months after the month: *Japanese
  nationals in Japan only*, the population the annual 確定数 and the total
  fertility rate are built on. From e-Stat's file catalog (人口動態統計月報
  （概数）, table 2, 人口動態総覧 月別), January 2008 onwards: each edition
  carries its own year to date and the whole year before.

The 速報 count is the larger — about 21,600 more births in the first half of
2026 (342,068 against 320,458) — except in December, when it has been the
smaller in every year on file. Read December against December.

What fetch() takes, and why so little: a December workbook or edition holds
the whole year (速報: and the two before it), so one per year plus the newest
of each release covers every month with about forty downloads. A month that
appears in more than one file takes the newest file's value — the Ministry's
latest word on it. Which files were read, and from where, is recorded in
``edition.json`` inside the archived bundle.

Facts the parser and the gates rely on:

- Layouts drift. The 速報 workbook moved from .xls to .xlsx, its year label
  from 平成19年 to 令和８年 + （2026）, its month columns one place right, and
  自然増加 became 自然増減. The 概数 table was .xls with Heisei year labels
  until 2019 and is CSV with 前年／当年 or 2025年1月 labels since. Columns are
  found by their headings and months by their labels, never by position, and
  every year a file carries is checked against the year it says it is.
- natural change = births − deaths, exactly, in both releases; every month
  is checked.
- The 速報 workbook also publishes 12-month totals. The parser recomputes
  each from the monthly counts in the same workbook and refuses the file if
  any differs, which catches a shifted column or a misread year outright.
- Counts are persons; marriages and divorces are couples (組). Stillbirths
  and the per-1,000 rates in the same tables are not taken.

Counts, not indices: ``weight_per_10000`` stays NULL. The year-to-date and
12-month totals the page shows are calculated by the API (measure=ytd,
sum_12m) from these published months and carry their formula.
"""
import csv
import datetime
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import bls_flat, estat_api, xls, xlsx


class ValidationError(Exception):
    pass


STATS_CODE = "00450011"
INDEX_URL = "https://www.mhlw.go.jp/toukei/list/81-1a.html"
SOKUHOU_BASE = "https://www.mhlw.go.jp/toukei/saikin/hw/jinkou/geppo/"

DATASET = {
    "slug": "vital-stats-jp",
    "title": "Births, Deaths and Marriages, Monthly — Japan (MHLW Vital Statistics)",
    "country": "Japan",
    "agency": "Ministry of Health, Labour and Welfare",
    "agency_ja": "厚生労働省",
    "base": None,
    "frequency": "monthly",
    "description": (
        "Japan's monthly births, deaths, natural change, marriages and divorces "
        "from the Ministry of Health, Labour and Welfare's vital statistics, in "
        "its two monthly releases kept apart: the preliminary count (速報, about "
        "two months after the month, foreign residents and Japanese abroad "
        "included) from 2005 and the approximate count (概数, about five months "
        "after, Japanese nationals in Japan only) from 2008."
    ),
}

SOURCE = {
    "source_id": "mhlw:jinkou-dotai-monthly",
    "name": ("Vital Statistics — monthly preliminary report (速報) and monthly "
             "report, approximate figures (月報（概数）), table 2"),
    "name_ja": "人口動態統計速報・人口動態統計月報（概数） 第２表 人口動態総覧，月別",
    "url": INDEX_URL,
    "license_note": (
        "Government of Japan Standard Terms of Use: reuse permitted with "
        "attribution to the Ministry of Health, Labour and Welfare. The "
        "approximate figures are located through the e-Stat API, which "
        "requires a free application ID."
    ),
}

DOWNLOAD_URL = INDEX_URL
RAW_SUFFIX = ".zip"
USER_AGENT = "ObservatoryIngest/0.1 (data pipeline; contact: repo owner)"

# (key, English, Japanese, unit, label the 速報 workbook uses, 概数 column)
MEASURES = [
    ("births", "Births", "出生数", "persons", "出生", "出生数"),
    ("deaths", "Deaths", "死亡数", "persons", "死亡", "死亡数"),
    ("natural_change", "Natural change (births less deaths)", "自然増減数",
     "persons", "自然増", "自然増減数"),
    ("marriages", "Marriages", "婚姻件数", "couples", "婚姻", "婚姻件数"),
    ("divorces", "Divorces", "離婚件数", "couples", "離婚", "離婚件数"),
]
# (key, English qualifier, Japanese qualifier, first month)
RELEASES = [
    ("sokuhou", "preliminary (速報): all events in Japan incl. foreign residents, "
                "plus Japanese abroad", "速報", datetime.date(2005, 1, 1)),
    ("gaisu", "approximate (概数): Japanese nationals in Japan",
     "月報（概数）", datetime.date(2008, 1, 1)),
]


def _code(release, measure):
    return "%s.%s" % (release, measure)


def _norm(text):
    """NFKC (full-width digits and spaces to ASCII) with every space removed."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


# --- fetch ------------------------------------------------------------------

RETRY_ATTEMPTS = 4


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
        except (urllib.error.URLError, IOError) as exc:    # DNS, reset, short read
            last = exc
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(5 * (attempt + 1))
    raise last


def _html(raw):
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", "replace")


def _sokuhou_pages(index_html):
    """{year: (month, page url)} — the newest 速報 page of each year."""
    best = {}
    for m in re.finditer(r'href="(/toukei/saikin/hw/jinkou/geppo/s(\d{4})/(\d{2})\.html)"',
                         index_html):
        year, month = int(m.group(2)), int(m.group(3))
        if year not in best or month > best[year][0]:
            best[year] = (month, urllib.parse.urljoin(INDEX_URL, m.group(1)))
    return best


def _sokuhou_files():
    """[(year, month, page url, workbook url, bytes)] — one workbook per year."""
    pages = _sokuhou_pages(_html(_get(INDEX_URL)))
    if len(pages) < 15:
        raise ValidationError("the Ministry's index lists 速報 pages for only %d years"
                              % len(pages))
    out = []
    for year in sorted(pages):
        month, page = pages[year]
        links = re.findall(r'href="([^"]+\.xlsx?)"', _html(_get(page)))
        if not links:
            raise ValidationError("速報 page %s links no workbook" % page)
        url = urllib.parse.urljoin(page, links[0])
        raw = _get(url)
        if raw[:2] != b"PK" and raw[:4] != b"\xd0\xcf\x11\xe0":
            raise ValidationError("%s is not an Excel workbook (got %r…)" % (url, raw[:16]))
        out.append((year, month, page, url, raw))
    return out


def _gaisu_catalog():
    """Every 月報（概数） edition in e-Stat's file catalog, walked to the end."""
    entries, key = [], 1
    while key:
        payload = estat_api.call("getDataCatalog", statsCode=STATS_CODE,
                                 searchWord="月報", limit=100, startPosition=key)
        inf = payload["GET_DATA_CATALOG"]["DATA_CATALOG_LIST_INF"]
        found = inf.get("DATA_CATALOG_INF") or []
        entries.extend([found] if isinstance(found, dict) else found)
        key = (inf.get("RESULT_INF") or {}).get("NEXT_KEY")
    return entries


def _gaisu_editions(entries):
    """{year: (survey month, table 2 url, format)} — the newest edition of each year."""
    best = {}
    for entry in entries:
        title = entry["DATASET"]["TITLE"]
        if "月報（概数）" not in title.get("NAME", "") or title.get("CYCLE") != "月次":
            continue
        survey = int(title["SURVEY_DATE"])
        resources = entry["RESOURCES"]["RESOURCE"]
        if isinstance(resources, dict):
            resources = [resources]
        table = None
        for r in resources:
            t = r["TITLE"]
            if (str(t.get("TABLE_NO")).split("-")[-1] == "2"
                    and "人口動態総覧" in t.get("NAME", "") and "月別" in t.get("NAME", "")):
                table = (r["URL"], r.get("FORMAT"))
        if table is None:
            continue
        year = survey // 100
        if year not in best or survey > best[year][0]:
            best[year] = (survey, table[0], table[1])
    return best


def fetch():
    files, manifest = {}, {"sokuhou": [], "gaisu": []}
    for year, month, page, url, raw in _sokuhou_files():
        name = "sokuhou/%d%02d%s" % (year, month, ".xlsx" if raw[:2] == b"PK" else ".xls")
        files[name] = raw
        manifest["sokuhou"].append({"file": name, "edition": "%d-%02d" % (year, month),
                                    "page": page, "url": url})
    editions = _gaisu_editions(_gaisu_catalog())
    if len(editions) < 15:
        raise ValidationError("e-Stat's catalog lists 月報（概数） table 2 for only %d years"
                              % len(editions))
    for year in sorted(editions):
        survey, url, fmt = editions[year]
        raw = _get(url)
        ext = ".csv" if fmt == "CSV" else ".xls"
        name = "gaisu/%d%s" % (survey, ext)
        files[name] = raw
        manifest["gaisu"].append({"file": name, "edition": "%d-%02d" % (survey // 100,
                                                                         survey % 100),
                                  "url": url, "format": fmt})
    files["edition.json"] = json.dumps(manifest, sort_keys=True, indent=1).encode("utf-8")
    return bls_flat.bundle(files)


# --- parse: 速報 workbook ----------------------------------------------------

_ERAS = {"昭和": 1925, "平成": 1988, "令和": 2018}


def _era_year(text):
    """'平成19年' -> 2007, '令和元年' -> 2019, '（2026）' -> 2026, else None."""
    t = _norm(text)
    m = re.search(r"\((\d{4})\)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(昭和|平成|令和)(\d+|元)年", t)
    if m:
        n = 1 if m.group(2) == "元" else int(m.group(2))
        return _ERAS[m.group(1)] + n
    return None


def _count(text, where):
    t = _norm(text).replace(",", "")
    if t in ("", "-", "…", "･", "・"):
        return None
    try:
        v = float(t)
    except ValueError:
        raise ValidationError("%s: unreadable count %r" % (where, text))
    if v != int(v):
        raise ValidationError("%s: count %r is not a whole number" % (where, text))
    return int(v)


def _sokuhou_measure(label):
    t = _norm(label)
    for key, _en, _ja, _unit, sk, _gc in MEASURES:
        if t.startswith(sk):
            return key
    return None


def parse_sokuhou(raw, edition_year, name):
    """One 速報 workbook -> ({(measure, date): value}, [check notes]).

    The sheet グラフ数値 holds two blocks: the month's own counts (当月) and
    the 12-month totals ending in each month (当月を含む過去1年間). Both are
    read; the totals are recomputed from the counts as a check and not stored.
    """
    reader = xlsx if raw[:2] == b"PK" else xls
    sheets = reader.sheets(raw)
    sheet = next((s for n, s in sheets.items() if "グラフ" in n), None)
    if sheet is None:
        raise ValidationError("%s: no グラフ数値 sheet (sheets: %s)" % (name, ", ".join(sheets)))
    text = lambda row, col: reader.cell_text(row.get(col)) if row.get(col) else ""
    month_cols, block, measure = None, None, None
    counts, totals, years_seen = {}, {}, {}
    for r in sorted(sheet):
        row = sheet[r]
        cells = dict((c, _norm(text(row, c))) for c in row)
        if month_cols is None:
            cols = [(c, v) for c, v in cells.items() if re.match(r"^\d{1,2}月$", v)]
            if len(cols) == 12:
                month_cols = dict((c, int(v[:-1])) for c, v in cols)
                if sorted(month_cols.values()) != list(range(1, 13)):
                    raise ValidationError("%s: month header %r" % (name, cols))
                block = "month"
            continue
        joined = "".join(cells.values())
        if "年換算率" in joined:
            break
        if "過去1年間" in cells.get("A", ""):
            block = "total"
        # Column B names the event; a name that is not one of ours
        # (死産, stillbirths) ends the previous one rather than extending it.
        if cells.get("B"):
            measure = _sokuhou_measure(cells["B"])
        if measure is None:
            continue
        year = None
        for c in ("C", "D"):
            year = year or _era_year(text(row, c))
        if year is None:
            continue
        seen = years_seen.setdefault((block, measure), [])
        seen.append(year)
        target = counts if block == "month" else totals
        for c, month in month_cols.items():
            v = _count(text(row, c), "%s row %d col %s" % (name, r, c))
            if v is not None:
                target[(measure, datetime.date(year, month, 1))] = v
    if month_cols is None:
        raise ValidationError("%s: no row of month headings 1月…12月" % name)
    want = [edition_year, edition_year - 1, edition_year - 2]
    for key, _en, _ja, _unit, _sk, _gc in MEASURES:
        for block_name in ("month", "total"):
            if years_seen.get((block_name, key)) != want:
                raise ValidationError(
                    "%s: %s %s rows are for years %s, expected %s"
                    % (name, block_name, key, years_seen.get((block_name, key)), want))
    # The published 12-month totals, recomputed from the counts beside them.
    checked = 0
    for (measure, period), total in totals.items():
        window = [counts.get((measure, _months_back(period, k))) for k in range(12)]
        if any(v is None for v in window):
            continue
        if sum(window) != total:
            raise ValidationError(
                "%s: the published 12-month %s to %s is %d; the months beside it sum "
                "to %d — a column or year is misread" % (name, measure, period, total,
                                                          sum(window)))
        checked += 1
    return counts, checked


def _months_back(period, n):
    y, m = period.year, period.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return datetime.date(y, m, 1)


# --- parse: 概数 table 2 ------------------------------------------------------

def _heading(cell):
    """A column heading without the unit some editions put inside it:
    '出　生　数\n（人）' -> '出生数'."""
    return re.sub(r"\([^)]*\)$", "", _norm(cell))


def _gaisu_columns(header):
    """{measure: column index} from the heading row that names 出生数."""
    cols = {}
    for key, _en, _ja, _unit, _sk, heading in MEASURES:
        for i, cell in enumerate(header):
            t = _heading(cell)
            # The .xls heading for natural change is split over two rows
            # (自然 / 増減数); the CSV heading is whole.
            if t == heading or (key == "natural_change" and t == "自然"):
                cols[key] = i
                break
    return cols


def _gaisu_rows_csv(raw, survey, name):
    """A CSV edition -> [(year, month, row cells)]."""
    rows = list(csv.reader(io.StringIO(raw.decode("cp932"))))
    edition_year = survey // 100
    title = _norm(rows[0][0]) if rows and rows[0] else ""
    m = re.search(r"\((\d{4})\)年(\d+)月分", title)
    if not m or int(m.group(1)) != edition_year or int(m.group(2)) != survey % 100:
        raise ValidationError("%s: title %r is not the %d edition" % (name, title, survey))
    header = next((r for r in rows if "出生数" in [_heading(c) for c in r]), None)
    if header is None:
        raise ValidationError("%s: no heading row naming 出生数" % name)
    out = []
    for row in rows:
        for cell in row[:2]:
            t = _norm(cell)
            mm = re.match(r"^(前年|当年)(\d{1,2})月$", t)
            if mm:
                year = edition_year - (1 if mm.group(1) == "前年" else 0)
                out.append((year, int(mm.group(2)), row))
                break
            mm = re.match(r"^(\d{4})年(\d{1,2})月$", t)
            if mm:
                out.append((int(mm.group(1)), int(mm.group(2)), row))
                break
    return header, out


def _gaisu_rows_xls(raw, survey, name):
    """An .xls edition -> [(year, month, row cells)], years anchored on the edition.

    Month labels are '20年 1月' then '2月' … with a Heisei year that a 2019
    edition may write as 31年 or 元年; rather than trust it, the Januaries are
    counted from the edition's own year backwards (the table holds the year
    before and the year of the edition), and a label's era year, where one can
    be read, must agree.
    """
    # e-Stat labels both Excel formats "XLS"; the bytes say which it is.
    reader = xlsx if raw[:2] == b"PK" else xls
    sheets = reader.sheets(raw)
    edition_year = survey // 100
    for sname, sheet in sheets.items():
        rows = [[reader.cell_text(sheet[r].get(c)) if sheet[r].get(c) else ""
                 for c in _letters(sheet[r])] for r in sorted(sheet)]
        header = next((r for r in rows if "出生数" in [_heading(c) for c in r]), None)
        if header is None:
            continue
        labelled = []
        for row in rows:
            for cell in row[:3]:
                t = _norm(cell)
                mm = re.match(r"^(?:(?:昭和|平成|令和)?(\d+|元)年)?(\d{1,2})月$", t)
                if mm:
                    labelled.append((mm.group(1), int(mm.group(2)), row))
                    break
        if not labelled:
            continue
        januaries = sum(1 for _y, mo, _r in labelled if mo == 1)
        year = edition_year - januaries + 1
        out = []
        for era_n, month, row in labelled:
            if month == 1 and out:
                year += 1
            if era_n is not None:
                n = 1 if era_n == "元" else int(era_n)
                if n + 1988 != year and n + 2018 != year:
                    raise ValidationError("%s: month label year %s does not fit %d"
                                          % (name, era_n, year))
            out.append((year, month, row))
        if year != edition_year:
            raise ValidationError("%s: months end in %d, edition is %d"
                                  % (name, year, edition_year))
        return header, out
    raise ValidationError("%s: no sheet with monthly rows under a 出生数 heading" % name)


def _letters(cells):
    """Row cells {column letter: cell} -> letters A.. through the last used one."""
    def num(col):
        n = 0
        for ch in col:
            n = n * 26 + ord(ch) - 64
        return n

    def letter(n):
        s = ""
        while n:
            n, rem = divmod(n - 1, 26)
            s = chr(65 + rem) + s
        return s
    last = max(num(c) for c in cells) if cells else 0
    return [letter(i) for i in range(1, last + 1)]


def parse_gaisu(raw, survey, name):
    if name.endswith(".csv"):
        header, rows = _gaisu_rows_csv(raw, survey, name)
    else:
        header, rows = _gaisu_rows_xls(raw, survey, name)
    cols = _gaisu_columns(header)
    missing = [m[0] for m in MEASURES if m[0] not in cols]
    if missing:
        raise ValidationError("%s: no column for %s (headings %r)" % (name, missing, header))
    out = {}
    for year, month, row in rows:
        for key, col in cols.items():
            v = _count(row[col] if col < len(row) else "", "%s %d-%02d %s" % (
                name, year, month, key))
            if v is not None:
                out[(key, datetime.date(year, month, 1))] = v
    if not out:
        raise ValidationError("%s: no monthly values" % name)
    return out


# --- parse ------------------------------------------------------------------

def parse(raw_bytes):
    try:
        files = bls_flat.unbundle(raw_bytes)
        manifest = json.loads(files["edition.json"].decode("utf-8"))
    except Exception as exc:
        raise ValidationError("not a bundle: %s" % exc)
    merged = {"sokuhou": {}, "gaisu": {}}
    checked = 0
    # Oldest file first, so a month carried by several files ends with the
    # newest file's value.
    for entry in sorted(manifest["sokuhou"], key=lambda e: e["edition"]):
        counts, n = parse_sokuhou(files[entry["file"]], int(entry["edition"][:4]),
                                  entry["file"])
        merged["sokuhou"].update(counts)
        checked += n
    for entry in sorted(manifest["gaisu"], key=lambda e: e["edition"]):
        survey = int(entry["edition"].replace("-", ""))
        merged["gaisu"].update(parse_gaisu(files[entry["file"]], survey, entry["file"]))
    if checked < 200:
        raise ValidationError("only %d published 12-month totals could be checked" % checked)

    series, observations = [], []
    order = 0
    for release, rel_en, rel_ja, first in RELEASES:
        for key, en, ja, unit, _sk, _gc in MEASURES:
            code = _code(release, key)
            points = sorted((p, v) for (m, p), v in merged[release].items()
                            if m == key and p >= first)
            for p, v in points:
                observations.append({"code": code, "period": p, "value": float(v)})
            series.append({
                "code": code,
                "name_en": "%s — %s" % (en, rel_en),
                "name_ja": "%s（%s）" % (ja, rel_ja),
                "unit": unit,
                "weight_per_10000": None,
                "sort_order": order,
            })
            order += 1
    PARSE_NOTES["totals_checked"] = checked
    return series, observations


PARSE_NOTES = {}


# --- validate ---------------------------------------------------------------

STALE_AFTER_DAYS = 130   # 速報 lands ~8 weeks after its month; ~4 weeks between releases
RANGES = {"births": (35000, 200000), "deaths": (60000, 250000),
          "marriages": (15000, 150000), "divorces": (8000, 40000),
          "natural_change": (-200000, 120000)}


def validate(series, observations):
    col = {}
    for o in observations:
        c = col.setdefault(o["code"], {})
        if o["period"] in c:
            raise ValidationError("duplicate observation %s %s" % (o["code"], o["period"]))
        c[o["period"]] = o["value"]
    expected = [_code(r[0], m[0]) for r in RELEASES for m in MEASURES]
    missing = [c for c in expected if c not in col]
    if missing:
        raise ValidationError("series missing: %s" % ", ".join(missing))

    # 1. Each release runs unbroken from its first month, and its five
    #    series end together.
    latest = {}
    for release, _en, _ja, first in RELEASES:
        end = None
        for key, *_rest in MEASURES:
            ps = sorted(col[_code(release, key)])
            span = (ps[-1].year - ps[0].year) * 12 + ps[-1].month - ps[0].month + 1
            if ps[0] != first or len(ps) != span:
                raise ValidationError("%s runs %s–%s with %d of %d months"
                                      % (_code(release, key), ps[0], ps[-1], len(ps), span))
            if end is not None and ps[-1] != end:
                raise ValidationError("%s ends %s, its release %s" % (
                    _code(release, key), ps[-1], end))
            end = ps[-1]
        latest[release] = end
    today = datetime.date.today()
    if (today - latest["sokuhou"]).days > 200 or (today - latest["gaisu"]).days > 330:
        raise ValidationError("newest months %s / %s are implausibly old"
                              % (latest["sokuhou"], latest["gaisu"]))
    if latest["gaisu"] > latest["sokuhou"]:
        raise ValidationError("概数 runs to %s, past 速報's %s — the releases are crossed"
                              % (latest["gaisu"], latest["sokuhou"]))

    # 2. natural change is births less deaths, exactly, every month.
    identities = 0
    for release, *_rest in RELEASES:
        b, d, n = (col[_code(release, k)] for k in ("births", "deaths", "natural_change"))
        for p in n:
            if b[p] - d[p] != n[p]:
                raise ValidationError("%s %s: natural change %d is not births %d less "
                                      "deaths %d" % (release, p, n[p], b[p], d[p]))
            identities += 1

    # 3. Ranges: a monthly count outside these is a column read as a rate or
    #    a total read as a month.
    for code, c in col.items():
        lo, hi = RANGES[code.split(".", 1)[1]]
        for p, v in c.items():
            if not lo <= v <= hi:
                raise ValidationError("%s %s: %d is outside %d–%d" % (code, p, v, lo, hi))

    # 4. The preliminary count includes foreign residents and Japanese abroad,
    #    so for births and deaths it is above the Japanese-nationals count —
    #    in every month but December. December's preliminary count has been
    #    below the approximate one in every year on file (births by 1,500 to
    #    2,100, deaths by 200 to 1,800), as published: the 12-month totals in
    #    the same workbooks confirm the reading. December is held to within
    #    5%; any other month below is crossed series.
    crossed = 0
    for key in ("births", "deaths"):
        s, g = col[_code("sokuhou", key)], col[_code("gaisu", key)]
        for p in g:
            if p in s:
                crossed += 1
                floor = g[p] * (0.95 if p.month == 12 else 1.0)
                if s[p] < floor:
                    raise ValidationError("%s %s: preliminary %d is below approximate %d"
                                          % (key, p, s[p], g[p]))

    return {
        "series": len(series),
        "observations": len(observations),
        "latest_period": latest["sokuhou"].isoformat(),
        "latest_preliminary": latest["sokuhou"].isoformat(),
        "latest_approximate": latest["gaisu"].isoformat(),
        "identities_checked": identities,
        "published_12m_totals_checked": PARSE_NOTES.get("totals_checked"),
        "release_pairs_compared": crossed,
        "births_preliminary_latest": col["sokuhou.births"][latest["sokuhou"]],
        "births_approximate_latest": col["gaisu.births"][latest["gaisu"]],
    }


# --- presentation -----------------------------------------------------------

PRESENTATION = {
    "credit_line": ("Source: Ministry of Health, Labour and Welfare, Vital "
                    "Statistics (人口動態統計)."),
    "stale_after_days": STALE_AFTER_DAYS,
    # Monthly counts: months add up to a year, so the API offers the
    # year-to-date and 12-month totals (measure=ytd, sum_12m) on these series.
    "additive": True,
    # Natural change is negative most months; a percentage change across a
    # sign change is noise, so rates are refused on it.
    "kinds": dict((_code(r[0], m[0]), "flow" if m[0] == "natural_change" else "count")
                  for r in RELEASES for m in MEASURES),
    "overview_tiles": [
        {"key": "births", "code": "sokuhou.births", "label": "Births (preliminary)",
         "type": "level", "compare_months": 12},
        {"key": "deaths", "code": "sokuhou.deaths", "label": "Deaths (preliminary)",
         "type": "level", "compare_months": 12},
        {"key": "natural_change", "code": "sokuhou.natural_change",
         "label": "Natural change (preliminary)", "type": "level", "compare_months": 12},
        {"key": "marriages", "code": "sokuhou.marriages",
         "label": "Marriages (preliminary)", "type": "level", "compare_months": 12},
    ],
    "main_series": [
        {"role": "headline", "code": "sokuhou.births",
         "label": "Births, preliminary", "slot": 1},
        {"role": "japanese", "code": "gaisu.births",
         "label": "Births, Japanese nationals (approximate)", "slot": 2},
    ],
}


MANIFEST = {
    "id": DATASET["slug"],
    "section": "demography",
    "name": {"en": "Births, deaths and marriages, monthly — vital statistics",
             "ja": "人口動態統計 月次（速報・概数）"},
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
        "as_of_supported": True, "history_from": "2005-01",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published monthly count (persons; couples for "
                                 "marriages and divorces)",
         "unit": "persons", "trust": "official"},
        {"id": "yoy", "label": "Change on the same month a year earlier", "unit": "%",
         "trust": "derived",
         "calc": ("(value[t] / value[t−12 months] − 1) × 100, from published values.")},
        {"id": "ytd", "label": "Total so far this year", "unit": "persons",
         "trust": "derived",
         "calc": ("sum(value[January] … value[t]) of t's year, from published monthly "
                  "values; a month with any earlier month of its year unpublished is "
                  "left out, never summed short.")},
        {"id": "sum_12m", "label": "Total over the last 12 months", "unit": "persons",
         "trust": "derived",
         "calc": ("sum(value[t−11] … value[t]) over the trailing 12 published monthly "
                  "values.")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "summary": "/api/v1/%s/overview" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search", "summary"],
    "cite": "/population.html?dataset=vital-stats-jp#h-vital",
    "page": "/population.html",
    "notes": [
        "Two releases, never merged. Codes starting sokuhou. are the preliminary "
        "count (速報): every birth, death, marriage and divorce reported in Japan, "
        "foreign residents included, plus Japanese abroad, about two months after "
        "the month. Codes starting gaisu. are the approximate count (概数): Japanese "
        "nationals in Japan only, about five months after the month — the population "
        "behind the annual final figures and the total fertility rate.",
        "Codes are release.measure — births, deaths, natural_change (births less "
        "deaths), marriages, divorces. Marriages and divorces count couples.",
        "measure=ytd gives the total so far in each year and sum_12m the last 12 "
        "months; both are sums of the published months. For a January–June "
        "comparison read the June values of ytd in the two years.",
        "The approximate monthly figures stay approximate: the Ministry does not "
        "republish months once the year's final figures (確定数) are out, so the "
        "monthly approximate figures and the annual final total differ slightly.",
    ],
}
