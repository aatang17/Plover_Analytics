"""Annual births and total fertility rate from the Ministry of Health, Labour
and Welfare's vital statistics (人口動態統計 確定数), nation and prefectures.

Not a dataset of its own: `population-jp-history` (ssds_population.py) uses
it to carry births (A4101) and the total fertility rate (A4103) past the year
where the Statistics Bureau's regional system stops. That system republishes
these very figures from the Ministry, about two years late — its 2026 edition
ends at 2023 — so the same numbers are read here at their source:

- e-Stat's database tables of the final figures, one edition a year under a
  new id, found by title and newest survey date, never pinned:
  上巻 都道府県別にみた年次別合計特殊出生率 (fertility rate, nation and 47
  prefectures, 1960 onwards) and 上巻 都道府県（特別区－指定都市再掲）別にみた
  人口動態総覧 (births, nation, prefectures, born abroad, cities; ten years).
- For the one year the database does not yet hold, the Ministry's own final
  workbook (確定数の概況, 統計表 xlsx): 第１表 national births and fertility
  rate, 第４表 births by prefecture. The prefecture fertility rates come a
  couple of months after the national one; until the database carries them
  they are missing, never estimated.

Every value is a final figure (確定数). Approximate figures (概数) are never
used here — the monthly ones are in vital-stats-jp.

Checks made here, independent of the gates in ssds_population:
- births: the 47 prefectures plus births abroad equal the nation exactly, in
  every year of the database table and in the workbook;
- the workbook's national births equal its own prefecture table's 全国 row,
  and its prior-year column equals the database where both have the year.
"""
import base64
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import estat_api, xlsx

STATS_CODE = "00450011"
TITLES = {
    "tfr": "上巻 都道府県別にみた年次別合計特殊出生率",
    "births": "上巻 都道府県（特別区－指定都市再掲）別にみた人口動態総覧",
}
INDEX_URL = "https://www.mhlw.go.jp/toukei/list/81-1a.html"
USER_AGENT = "ObservatoryIngest/0.1 (data pipeline; contact: repo owner)"
BIRTHS_CODE = "00110"      # 出生数, in the 人口動態総覧 table
ABROAD_AREA = "51000"      # 外国: births to Japanese abroad, counted in the nation


class SourceError(Exception):
    pass


def _norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
        except (urllib.error.URLError, IOError) as exc:
            last = exc
        if attempt < 3:
            time.sleep(5 * (attempt + 1))
    raise last


def _html(raw):
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", "replace")


# --- fetch ------------------------------------------------------------------

def _newest_table(title):
    """(statsDataId, survey date) of the newest edition with exactly this title."""
    payload = estat_api.call("getStatsList", statsCode=STATS_CODE, searchWord=title,
                             limit=100)
    info = payload["GET_STATS_LIST"]["DATALIST_INF"]
    tables = info.get("TABLE_INF") or []
    if isinstance(tables, dict):
        tables = [tables]
    best = None
    for t in tables:
        name = t["TITLE"]["$"] if isinstance(t["TITLE"], dict) else t["TITLE"]
        if _norm(name) != _norm(title):
            continue
        key = str(t.get("SURVEY_DATE"))
        if best is None or key > best[1]:
            best = (t["@id"], key)
    if best is None:
        raise SourceError("e-Stat lists no table titled %s" % title)
    return best


def _newest_workbook():
    """(year, page url, workbook url, bytes) of the newest final-figures workbook."""
    pages = {}
    for m in re.finditer(r'href="(/toukei/saikin/hw/jinkou/kakutei(\d{2})/index\.html)"',
                         _html(_get(INDEX_URL))):
        nn = int(m.group(2))
        pages[(2000 + nn) if nn < 90 else (1900 + nn)] = urllib.parse.urljoin(
            INDEX_URL, m.group(1))
    if not pages:
        raise SourceError("the Ministry's index links no final-figures (確定数) page")
    year = max(pages)
    links = [h for h in re.findall(r'href="([^"]+\.xlsx)"', _html(_get(pages[year])))
             if "_hyo" in h]
    if len(links) != 1:
        raise SourceError("the %d final-figures page links %d statistical-table "
                          "workbooks, expected one" % (year, len(links)))
    url = urllib.parse.urljoin(pages[year], links[0])
    raw = _get(url)
    if raw[:2] != b"PK":
        raise SourceError("%s is not an .xlsx workbook" % url)
    return year, pages[year], url, raw


def fetch():
    """Everything the merge needs, JSON-serialisable for the archived artifact."""
    out = {"estat": {}}
    for key, title in TITLES.items():
        sid, survey = _newest_table(title)
        params = {"cdCat01": BIRTHS_CODE} if key == "births" else {}
        out["estat"][key] = {"id": sid, "survey_date": survey, "title": title,
                             "pages": estat_api.get_stats_data(sid, **params)}
    year, page, url, raw = _newest_workbook()
    out["workbook"] = {"year": year, "page": page, "url": url,
                       "xlsx_base64": base64.b64encode(raw).decode("ascii")}
    return out


# --- parse ------------------------------------------------------------------

def _year(time_code, time_name):
    digits = "".join(c for c in time_name if c.isdigit())[:4]
    if len(digits) != 4 or time_code[:4] != digits:
        raise SourceError("time %r / %r is not a year" % (time_code, time_name))
    return int(digits)


def _number(text):
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None          # "・", "-", "…": not published, never zero


def parse_estat(block, kind, prefecture_codes):
    """{(geo, year): value} from one database table, plus the years whose
    births reconciled. geo is "00" for the nation or a two-digit prefecture."""
    out, abroad = {}, {}
    for page in block["pages"]:
        data = page["GET_STATS_DATA"]["STATISTICAL_DATA"]
        names = estat_api.class_values(data, "time")
        values = data["DATA_INF"]["VALUE"]
        for v in [values] if isinstance(values, dict) else values:
            if kind == "births" and v.get("@cat01") != BIRTHS_CODE:
                raise SourceError("births table returned item %s" % v.get("@cat01"))
            if kind == "tfr" and v.get("@tab") != "10060":
                raise SourceError("fertility table returned item %s" % v.get("@tab"))
            area = v["@area"]
            year = _year(v["@time"], names[v["@time"]])
            value = _number(v.get("$"))
            if value is None:
                continue
            if area == ABROAD_AREA:
                abroad[year] = value
                continue
            if area == "00000":
                geo = "00"
            elif area.endswith("000") and area[:2] in prefecture_codes:
                geo = area[:2]
            else:
                continue          # cities (再掲) and unknown (不詳)
            out[(geo, year)] = value
    if kind == "tfr":
        out = dict((k, round(v, 2)) for k, v in out.items())
        return out, 0
    reconciled = 0
    for year in sorted(set(y for _g, y in out)):
        parts = [out.get((g, year)) for g in prefecture_codes]
        if None in parts or ("00", year) not in out:
            continue
        if sum(parts) + abroad.get(year, 0) != out[("00", year)]:
            raise SourceError("births %d: 47 prefectures %d + abroad %d != nation %d"
                              % (year, sum(parts), abroad.get(year, 0), out[("00", year)]))
        reconciled += 1
    if not reconciled:
        raise SourceError("no year of the births table reconciled")
    return out, reconciled


def _sheet(sheets, prefix):
    for name, grid in sheets.items():
        if _norm(name).startswith(_norm(prefix)):
            return grid
    raise SourceError("the workbook has no sheet %s (sheets: %s)" % (prefix, ", ".join(sheets)))


def _cells(grid, r):
    return dict((c, _norm(xlsx.cell_text(v))) for c, v in grid.get(r, {}).items())


def parse_workbook(block, prefectures):
    """({(geo, year): births}, {("00", year): tfr}) from the final-figures workbook.

    `prefectures` is [(code, Japanese name)] in JIS order.
    """
    year = int(block["year"])
    sheets = xlsx.sheets(base64.b64decode(block["xlsx_base64"]))

    # 第１表: column headings carry the year, "令和７年(2025)". The births row
    # and the fertility-rate row each sit under their own heading row.
    t1 = _sheet(sheets, "第１表")
    births, tfr, cols = {}, {}, {}
    for r in sorted(t1):
        row = _cells(t1, r)
        years = dict((c, int(m.group(1))) for c, v in row.items()
                     for m in [re.search(r"\((\d{4})\)", v)] if m)
        label = row.get("C", "")
        if years and not label:
            cols = years
            continue
        if label.startswith("出生(") or label.startswith("出生（"):
            # Only the 実数 (count) columns: the 率 columns to their right
            # carry the same year headings.
            for c, y in sorted(cols.items()):
                v = _number(xlsx.cell_text(t1[r].get(c)))
                if v is not None and y not in births:
                    births[y] = v
        if label == "合計特殊出生率":
            for c, y in cols.items():
                v = _number(xlsx.cell_text(t1[r].get(c)))
                if v is not None:
                    tfr[("00", y)] = round(v, 2)
    if year not in births or ("00", year) not in tfr:
        raise SourceError("第１表 has no %d births or fertility rate" % year)

    # 第４表: births by prefecture in the 出生数 総数 column.
    t4 = _sheet(sheets, "第４表")
    col, out, abroad, national = None, {}, None, None
    names = dict((ja, code) for code, ja in prefectures)
    short = dict((ja[:-1], code) for code, ja in prefectures if ja[-1] in "都府県")
    for r in sorted(t4):
        row = _cells(t4, r)
        joined = "".join(row.values())
        if col is None:
            for c, v in row.items():
                if v.startswith("出生数"):
                    col = c
            continue
        label = row.get("B", "")
        value = _number(xlsx.cell_text(t4[r].get(col)))
        if label == "全国":
            national = value
        elif label == "外国":
            abroad = value
        elif label in names or label in short:
            code = names.get(label) or short[label]
            if code not in out and value is not None:
                out[code] = value
        if "再掲" in joined:
            break
    if not any(re.search(r"\(%d\)" % year, _norm(xlsx.cell_text(v)))
               for r in list(t4)[:4] for v in t4[r].values()):
        raise SourceError("第４表 is not for %d" % year)
    if col is None or len(out) != len(prefectures):
        raise SourceError("第４表: births for %d of %d prefectures" % (len(out), len(prefectures)))
    if national != births[year]:
        raise SourceError("第４表 全国 %s != 第１表 births %s" % (national, births[year]))
    if sum(out.values()) + (abroad or 0) != national:
        raise SourceError("第４表: prefectures %d + abroad %s != nation %d"
                          % (sum(out.values()), abroad, national))
    result = dict(((code, year), v) for code, v in out.items())
    result[("00", year)] = births[year]
    prior = dict((("00", y), v) for y, v in births.items() if y != year)
    return result, tfr, prior
