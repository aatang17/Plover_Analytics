"""Adapter: Japan population by prefecture — Basic Resident Register.

Source: 総務省 (Ministry of Internal Affairs and Communications), 住民基本台帳に
基づく人口、人口動態及び世帯数 — the count of everyone actually on a
municipality's resident register as of 1 January, published each July.
This is administrative-register data, not a survey: no sampling error, and
it is the only annual source that splits Japanese and foreign residents on
the same definition, at the same date, for every prefecture.

Six prefecture-level workbooks make one picture, and one ingest takes all
six as a single artifact: three resident segments (総計 all / 日本人住民
Japanese / 外国人住民 foreign) × two tables (population, households and
the year's register flows; population by five-year age band and sex).

Facts the parser and the gates rely on:

- **The ministry keeps only the current year online.** Last year's
  workbook is deleted — the same /main_content/ id is overwritten, and
  older ids 404. e-Stat's file catalog is the archive of prior years, and
  the population-and-flows workbooks from 2014 (flows for calendar 2013)
  are read from it every ingest; age bands are the current year only.
  The archived years use an older layout — headings below the 団体コード
  row, 増加 for 増減 in 2014 — and pass every gate the current year does,
  plus one only a history allows: each year's population plus its net
  change equals the next year's population, exactly. Before 2014 the
  register counted at 31 March with fiscal-year flows; not taken.
- Content ids change without notice (000494956 in 2017, 000633315 in
  2019, 000892948 since 2023), so `fetch()` resolves the links off the
  ministry's page rather than pinning URLs.
- **Column layout differs by segment** — the Japanese-resident workbook
  splits households into all-Japanese and mixed-nationality; naturalisation
  is an *addition* there and a *deletion* in the foreign workbook. Columns
  are therefore resolved by their two header rows, never by position, and
  an unrecognised column fails the ingest rather than being dropped.
- **Stocks and flows are dated differently, because they are.** The
  workbook stamped 令和8年1月1日 carries the population on 1 Jan 2026 and
  the register flows for calendar 2025. Stocks take 1 Jan of the stock
  year; flows take 1 Jan of the year they cover, so that
  population(Y+1) − population(Y) equals the flows dated Y.
- 社会増減 ("social change") is net change *minus* natural change, which
  is not the same as in-migrants minus out-migrants: it also carries
  naturalisations and other register corrections. The identity
  in_total − out_total = social_change fails for all 48 areas, by design.
  Never label it "migration".
- A handful of records carry no usable birth date or sex, so the age bands
  fall a few dozen short of the published total (43 people nationally in
  the 2026 file, out of 123.8 million). The residual is real, is left in
  the published totals exactly as released, and is reported in the
  validation summary so a surface can disclose it rather than hide it.

Published rates (増減率 etc.) are deliberately *not* stored: rates are
calculated on the platform and carry their formula, per the trust
contract. They are used here as a cross-check — our recomputed rate must
match the ministry's to within a rounding tolerance, or the ingest fails.

Counts of people are levels, not indices: `weight_per_10000` is
meaningless and stays NULL, and flows go negative.
"""
import base64
import datetime
import json
import re
import urllib.request

from . import estat_api, xls, xlsx


class ValidationError(Exception):
    pass


PAGE_URL = ("https://www.soumu.go.jp/main_sosiki/jichi_gyousei/daityo/"
            "jinkou_jinkoudoutai-setaisuu.html")
SITE_ROOT = "https://www.soumu.go.jp"
DOWNLOAD_URL = PAGE_URL

USER_AGENT = "ObservatoryIngest/0.1 (data pipeline; contact: repo owner)"
RAW_SUFFIX = ".json"

# (JIS prefecture code, name as the ministry writes it, English, 6-digit
# local-government code including its check digit). English names are the
# ones already used by the site's own map asset (web/assets/japan.geo.json),
# with the 県/府/都 suffix dropped; the Japanese names are gated against the
# workbook on every ingest, so a boundary or naming change fails loudly.
PREFECTURES = [
    ("01", "北海道", "Hokkaido"), ("02", "青森県", "Aomori"),
    ("03", "岩手県", "Iwate"), ("04", "宮城県", "Miyagi"),
    ("05", "秋田県", "Akita"), ("06", "山形県", "Yamagata"),
    ("07", "福島県", "Fukushima"), ("08", "茨城県", "Ibaraki"),
    ("09", "栃木県", "Tochigi"), ("10", "群馬県", "Gunma"),
    ("11", "埼玉県", "Saitama"), ("12", "千葉県", "Chiba"),
    ("13", "東京都", "Tokyo"), ("14", "神奈川県", "Kanagawa"),
    ("15", "新潟県", "Niigata"), ("16", "富山県", "Toyama"),
    ("17", "石川県", "Ishikawa"), ("18", "福井県", "Fukui"),
    ("19", "山梨県", "Yamanashi"), ("20", "長野県", "Nagano"),
    ("21", "岐阜県", "Gifu"), ("22", "静岡県", "Shizuoka"),
    ("23", "愛知県", "Aichi"), ("24", "三重県", "Mie"),
    ("25", "滋賀県", "Shiga"), ("26", "京都府", "Kyoto"),
    ("27", "大阪府", "Osaka"), ("28", "兵庫県", "Hyogo"),
    ("29", "奈良県", "Nara"), ("30", "和歌山県", "Wakayama"),
    ("31", "鳥取県", "Tottori"), ("32", "島根県", "Shimane"),
    ("33", "岡山県", "Okayama"), ("34", "広島県", "Hiroshima"),
    ("35", "山口県", "Yamaguchi"), ("36", "徳島県", "Tokushima"),
    ("37", "香川県", "Kagawa"), ("38", "愛媛県", "Ehime"),
    ("39", "高知県", "Kochi"), ("40", "福岡県", "Fukuoka"),
    ("41", "佐賀県", "Saga"), ("42", "長崎県", "Nagasaki"),
    ("43", "熊本県", "Kumamoto"), ("44", "大分県", "Oita"),
    ("45", "宮崎県", "Miyazaki"), ("46", "鹿児島県", "Kagoshima"),
    ("47", "沖縄県", "Okinawa"),
]

# The national row the ministry prints as 合計. It is published, not summed
# by us — and the gates check our sum of 47 against it.
NATIONAL = ("00", "合計", "Japan")

# The Statistics Bureau's eight-region division (地方区分), for grouping on
# the front end only. Nothing in the data depends on it.
REGIONS = [
    ("hokkaido", "Hokkaido", "北海道", ("01",)),
    ("tohoku", "Tohoku", "東北", ("02", "03", "04", "05", "06", "07")),
    ("kanto", "Kanto", "関東", ("08", "09", "10", "11", "12", "13", "14")),
    ("chubu", "Chubu", "中部",
     ("15", "16", "17", "18", "19", "20", "21", "22", "23")),
    ("kinki", "Kinki", "近畿", ("24", "25", "26", "27", "28", "29", "30")),
    ("chugoku", "Chugoku", "中国", ("31", "32", "33", "34", "35")),
    ("shikoku", "Shikoku", "四国", ("36", "37", "38", "39")),
    ("kyushu", "Kyushu & Okinawa", "九州・沖縄",
     ("40", "41", "42", "43", "44", "45", "46", "47")),
]
REGION_OF = dict((code, key) for key, _en, _ja, codes in REGIONS
                 for code in codes)

# (segment code, the bracket the ministry puts in the link text, English).
SEGMENTS = [
    ("all", "総計", "All residents"),
    ("jp", "日本人住民", "Japanese residents"),
    ("fgn", "外国人住民", "Foreign residents"),
]
SEGMENT_JA = dict((code, ja) for code, ja, _en in SEGMENTS)
SEGMENT_EN = dict((code, en) for code, _ja, en in SEGMENTS)

# (group header, sub header) -> measure code. Covers all three segments'
# layouts; a pair absent from here fails the ingest.
COLUMN_MEASURE = {
    ("人口", "男"): "population_male",
    ("人口", "女"): "population_female",
    ("人口", "計"): "population",
    ("世帯数", "世帯数"): "households",
    ("世帯数", "計"): "households",
    ("世帯数", "日本人住民"): "households_japanese_only",
    ("世帯数", "複数国籍"): "households_multi_nationality",
    ("住民票記載数", "転入者数（国内）"): "in_domestic",
    ("住民票記載数", "転入者数（国外）"): "in_overseas",
    ("住民票記載数", "転入者数（計）"): "in_total",
    ("住民票記載数", "出生者数"): "births",
    ("住民票記載数", "その他（帰化等）"): "additions_naturalisation",
    ("住民票記載数", "その他（国籍喪失）"): "additions_loss_of_nationality",
    ("住民票記載数", "その他（その他）"): "additions_other_misc",
    ("住民票記載数", "その他（計）"): "additions_other",
    ("住民票記載数", "計（Ａ）"): "additions_total",
    ("住民票消除数", "転出者数（国内）"): "out_domestic",
    ("住民票消除数", "転出者数（国外）"): "out_overseas",
    ("住民票消除数", "転出者数（計）"): "out_total",
    ("住民票消除数", "死亡者数"): "deaths",
    ("住民票消除数", "その他（帰化等）"): "deletions_naturalisation",
    ("住民票消除数", "その他（国籍喪失）"): "deletions_loss_of_nationality",
    ("住民票消除数", "その他（その他）"): "deletions_other_misc",
    ("住民票消除数", "その他（計）"): "deletions_other",
    ("住民票消除数", "計（Ｂ）"): "deletions_total",
    ("増減数(Ａ)-(Ｂ)", "増減数(Ａ)-(Ｂ)"): "net_change",
    ("自然増減数", "自然増減数"): "natural_change",
    ("社会増減数", "社会増減数"): "social_change",
    # Published rates. Not stored — the platform calculates rates and shows
    # the formula. Kept here so the columns are recognised, and used as a
    # cross-check against our own arithmetic.
    ("増減率", "増減率"): "~rate_net",
    ("自然増減率", "自然増減率"): "~rate_natural",
    ("社会増減率", "社会増減率"): "~rate_social",
}

# Display order, English label, and whether the number is a level on 1 Jan
# (stock) or a count over the preceding calendar year (flow).
MEASURES = [
    ("population", "Population", "stock", "persons"),
    ("population_male", "Population, male", "stock", "persons"),
    ("population_female", "Population, female", "stock", "persons"),
    ("households", "Households", "stock", "households"),
    ("households_japanese_only", "Households, all-Japanese", "stock", "households"),
    ("households_multi_nationality", "Households, mixed-nationality", "stock", "households"),
    ("in_domestic", "In-migrants from within Japan", "flow", "persons"),
    ("in_overseas", "In-migrants from abroad", "flow", "persons"),
    ("in_total", "In-migrants, total", "flow", "persons"),
    ("births", "Births", "flow", "persons"),
    ("additions_naturalisation", "Registrations: naturalisation", "flow", "persons"),
    ("additions_loss_of_nationality",
     "Registrations: loss of Japanese nationality", "flow", "persons"),
    ("additions_other_misc", "Registrations: other", "flow", "persons"),
    ("additions_other", "Registrations: other, total", "flow", "persons"),
    ("additions_total", "Register additions, total (A)", "flow", "persons"),
    ("out_domestic", "Out-migrants within Japan", "flow", "persons"),
    ("out_overseas", "Out-migrants abroad", "flow", "persons"),
    ("out_total", "Out-migrants, total", "flow", "persons"),
    ("deaths", "Deaths", "flow", "persons"),
    ("deletions_naturalisation", "Deletions: naturalisation", "flow", "persons"),
    ("deletions_loss_of_nationality",
     "Deletions: loss of Japanese nationality", "flow", "persons"),
    ("deletions_other_misc", "Deletions: other", "flow", "persons"),
    ("deletions_other", "Deletions: other, total", "flow", "persons"),
    ("deletions_total", "Register deletions, total (B)", "flow", "persons"),
    ("net_change", "Net change (A − B)", "flow", "persons"),
    ("natural_change", "Natural change (births − deaths)", "flow", "persons"),
    ("social_change", "Social change (net − natural)", "flow", "persons"),
]
# The ministry's own wording for each measure, so a Japanese series name is
# Japanese all the way through. Taken from the workbook's two header rows.
MEASURE_JA = {
    "population": "人口",
    "population_male": "人口（男）",
    "population_female": "人口（女）",
    "households": "世帯数",
    "households_japanese_only": "世帯数（日本人住民）",
    "households_multi_nationality": "世帯数（複数国籍）",
    "in_domestic": "転入者数（国内）",
    "in_overseas": "転入者数（国外）",
    "in_total": "転入者数（計）",
    "births": "出生者数",
    "additions_naturalisation": "住民票記載数 その他（帰化等）",
    "additions_loss_of_nationality": "住民票記載数 その他（国籍喪失）",
    "additions_other_misc": "住民票記載数 その他（その他）",
    "additions_other": "住民票記載数 その他（計）",
    "additions_total": "住民票記載数 計（Ａ）",
    "out_domestic": "転出者数（国内）",
    "out_overseas": "転出者数（国外）",
    "out_total": "転出者数（計）",
    "deaths": "死亡者数",
    "deletions_naturalisation": "住民票消除数 その他（帰化等）",
    "deletions_loss_of_nationality": "住民票消除数 その他（国籍喪失）",
    "deletions_other_misc": "住民票消除数 その他（その他）",
    "deletions_other": "住民票消除数 その他（計）",
    "deletions_total": "住民票消除数 計（Ｂ）",
    "net_change": "増減数(Ａ)-(Ｂ)",
    "natural_change": "自然増減数",
    "social_change": "社会増減数",
}

MEASURE_ORDER = dict((code, i) for i, (code, _l, _k, _u) in enumerate(MEASURES))
MEASURE_LABEL = dict((code, label) for code, label, _k, _u in MEASURES)
MEASURE_KIND = dict((code, kind) for code, _l, kind, _u in MEASURES)
MEASURE_UNIT = dict((code, unit) for code, _l, _k, unit in MEASURES)

# Age band header -> measure stem. The five-year bands as the ministry
# publishes them, open-ended at 100.
AGE_BANDS = [("総数", "age_total", "Total")] + [
    ("%d歳～%d歳" % (lo, lo + 4), "age_%d_%d" % (lo, lo + 4),
     "Age %d–%d" % (lo, lo + 4))
    for lo in range(0, 100, 5)
] + [("100歳以上", "age_100_plus", "Age 100+")]
AGE_STEM = dict((ja, stem) for ja, stem, _en in AGE_BANDS)
AGE_LABEL = dict((stem, en) for _ja, stem, en in AGE_BANDS)
AGE_ORDER = dict((stem, i) for i, (_ja, stem, _en) in enumerate(AGE_BANDS))

SEXES = [("計", "total", "all"), ("男", "male", "male"), ("女", "female", "female")]
SEX_SUFFIX = dict((ja, suffix) for ja, suffix, _en in SEXES)
SEX_LABEL = dict((suffix, en) for _ja, suffix, en in SEXES)

# Elderly bands, for the aging measures a surface computes. Listed here so
# the definition of "65 and over" lives with the data, not in the front end.
AGE_65_PLUS = ["age_%d_%d" % (lo, lo + 4) for lo in range(65, 100, 5)] + \
    ["age_100_plus"]
AGE_UNDER_15 = ["age_0_4", "age_5_9", "age_10_14"]
AGE_WORKING = ["age_%d_%d" % (lo, lo + 4) for lo in range(15, 65, 5)]

DATASET = {
    "slug": "population-jp",
    "title": "Population by Prefecture — Japan (Basic Resident Register)",
    "country": "Japan",
    "agency": "Ministry of Internal Affairs and Communications",
    "agency_ja": "総務省",
    "base": None,
    "frequency": "annual",
    "description": (
        "Population, households and the year's register flows for all 47 "
        "prefectures and the national total, from the Basic Resident "
        "Register as of 1 January, split three ways — all residents, "
        "Japanese residents and foreign residents — with population by "
        "five-year age band and sex for the latest year. Population from 2014 "
        "and flows from 2013, from e-Stat's archive of the ministry's "
        "workbooks. Administrative counts of registered residents, not a "
        "survey estimate."
    ),
}

SOURCE = {
    "source_id": "soumu:juki-jinko",
    "name": ("Population, Vital Statistics and Number of Households Based on "
             "the Basic Resident Register"),
    "name_ja": "住民基本台帳に基づく人口、人口動態及び世帯数",
    "url": PAGE_URL,
    "license_note": (
        "Government of Japan Standard Terms of Use (compatible with "
        "CC BY 4.0): free to use with attribution to the Ministry of "
        "Internal Affairs and Communications."
    ),
}


# --- fetch ------------------------------------------------------------------

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


_ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _resolve_links(page_html):
    """(segment, table) -> absolute URL, from the ministry's index page.

    Matched on what the link text says rather than on its position or its
    content id: the ids change between years, and the text has carried
    typos (令和8住民基本台帳, missing the 年) that a strict pattern would
    trip over.
    """
    found = {}
    for href, label in _ANCHOR.findall(page_html):
        if not href.lower().split("?")[0].endswith((".xls", ".xlsx")):
            continue
        text = re.sub(r"<[^>]+>", "", label)
        if "都道府県別" not in text or "市区町村別" in text:
            continue
        segment = None
        for code, ja, _en in SEGMENTS:
            if "【%s】" % ja in text:
                segment = code
                break
        if segment is None:
            continue
        if "年齢階級別" in text:
            table = "age"
        elif "人口動態" in text or "人口・世帯数" in text:
            table = "dynamics"
        else:
            continue
        key = (segment, table)
        url = href if href.startswith("http") else SITE_ROOT + href
        if key in found and found[key] != url:
            raise ValidationError(
                "two different files claim to be %s/%s: %s and %s"
                % (segment, table, found[key], url))
        found[key] = url

    missing = [(s, t) for s, _ja, _en in SEGMENTS for t in ("dynamics", "age")
               if (s, t) not in found]
    if missing:
        raise ValidationError(
            "ministry page is missing %d of the 6 prefecture workbooks: %s"
            % (len(missing), missing))
    return found


def fetch():
    """All six prefecture workbooks, verbatim, in one archived artifact.

    The envelope holds only the file bytes, keyed by segment and table, so
    it is byte-identical whenever the published data is — the ingest
    runner's SHA-256 comparison is then a true "nothing new" test even
    though the ministry renumbers the URLs. Where the files came from is
    recorded in the validation summary instead.
    """
    page = _get(PAGE_URL).decode("cp932", "replace")
    links = _resolve_links(page)
    envelope = {}
    for (segment, table), url in sorted(links.items()):
        envelope["%s.%s" % (segment, table)] = base64.b64encode(
            _get(url)).decode("ascii")
    global _RESOLVED
    _RESOLVED = dict(("%s.%s" % k, v) for k, v in links.items())
    for (year, segment), url in sorted(_archive_links().items()):
        envelope["archive.%d.%s.dynamics" % (year, segment)] = base64.b64encode(
            _get(url)).decode("ascii")
        _RESOLVED["archive.%d.%s.dynamics" % (year, segment)] = url
    return json.dumps(envelope, sort_keys=True).encode("utf-8")


# The register moved to a 1 January reference date in 2014; the 2013 survey
# and earlier count at 31 March with fiscal-year flows, a different basis,
# and are not taken. The 2014 file is the first whose flows cover a calendar
# year (2013).
ARCHIVE_FIRST_YEAR = 2014
ARCHIVE_STATS_CODE = "00200241"


def _archive_links():
    """{(survey year, segment): download URL} of every archived prefecture
    population-and-flows workbook on e-Stat, from 2014.

    The ministry deletes each year's workbooks when the next arrive; e-Stat's
    file catalog keeps them all. Matched on the table's name, never its id.
    """
    entries, key = [], 1
    while key:
        payload = estat_api.call("getDataCatalog", statsCode=ARCHIVE_STATS_CODE,
                                 limit=100, startPosition=key)
        inf = payload["GET_DATA_CATALOG"]["DATA_CATALOG_LIST_INF"]
        found = inf.get("DATA_CATALOG_INF") or []
        entries.extend([found] if isinstance(found, dict) else found)
        key = (inf.get("RESULT_INF") or {}).get("NEXT_KEY")
    out = {}
    for entry in entries:
        title = entry["DATASET"]["TITLE"]
        try:
            year = int(str(title.get("SURVEY_DATE"))[:4])
        except ValueError:
            continue
        if year < ARCHIVE_FIRST_YEAR or "調査の結果" not in title.get("NAME", ""):
            continue
        resources = entry["RESOURCES"]["RESOURCE"]
        for r in [resources] if isinstance(resources, dict) else resources:
            name = r["TITLE"].get("NAME", "")
            if "都道府県別人口、人口動態及び世帯数" not in name:
                continue
            for segment, ja, _en in SEGMENTS:
                if "【%s】" % ja in name:
                    if (year, segment) in out:
                        raise ValidationError("e-Stat lists two %d %s workbooks"
                                              % (year, segment))
                    out[(year, segment)] = r["URL"]
    years = sorted(set(y for y, _s in out))
    if len(years) < 10 or any(len([1 for y2, _s in out if y2 == y]) != len(SEGMENTS)
                              for y in years):
        raise ValidationError("e-Stat's archive lists an incomplete set of register "
                              "workbooks: %s" % sorted(out))
    return out


# Where fetch() got each workbook, and what parse() checked but does not
# store. Both end up in the release's validation summary, which is the
# record of what a vintage actually was.
_RESOLVED = {}
_AUDIT = {}


# --- parse ------------------------------------------------------------------

_ERA_START = {"令和": 2018, "平成": 1988, "昭和": 1925}
_ERA_YEAR = re.compile(r"(令和|平成|昭和)\s*(\d+|元)\s*年")


def _era_years(title):
    """Every era year named in a workbook's title cell, as AD years."""
    out = []
    for era, digits in _ERA_YEAR.findall(title):
        n = 1 if digits == "元" else int(digits)
        out.append(_ERA_START[era] + n)
    return out


def _sheet(raw, label):
    # e-Stat's archive holds the older years as .xls (BIFF), the newer as .xlsx.
    sheets = (xlsx if raw[:2] == b"PK" else xls).sheets(raw)
    if len(sheets) != 1:
        raise ValidationError(
            "%s: expected one sheet, found %d" % (label, len(sheets)))
    return list(sheets.values())[0]


def _header_row(grid, label):
    for row in sorted(grid):
        if xlsx.cell_text(grid[row].get("A")).strip() == "団体コード":
            return row
    raise ValidationError("%s: no 団体コード header row" % label)


def _geo_code(cell_code, cell_name, label, row):
    """The workbook's local-government code -> our two-digit geography."""
    code = cell_code.strip()
    if code in ("", "-", "－"):
        if cell_name != NATIONAL[1]:
            raise ValidationError(
                "%s row %d: a code-less row named %r, expected %r"
                % (label, row, cell_name, NATIONAL[1]))
        return NATIONAL[0]
    if not code[:2].isdigit():
        raise ValidationError(
            "%s row %d: unreadable local-government code %r" % (label, row, code))
    return code[:2]


def _number(text, label, row, column):
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        raise ValidationError(
            "%s row %d column %s: %r is not a number" % (label, row, column, text))


# The archived 2014 workbook says 増加 where every later one says 増減.
_HEADING_SPELLING = {"増加率": "増減率", "自然増加数": "自然増減数",
                     "自然増加率": "自然増減率", "社会増加数": "社会増減数",
                     "社会増加率": "社会増減率"}


def _column_number(column):
    n = 0
    for ch in column:
        n = n * 26 + ord(ch) - 64
    return n


def _parse_dynamics(raw, segment, edition=None):
    """One population/households/flows workbook -> (stock year, flow year,
    {geo: {measure: value}}) with the published rates alongside."""
    label = "%s dynamics workbook%s" % (segment, (" " + edition) if edition else "")
    grid = _sheet(raw, label)
    header = _header_row(grid, label)
    # Two layouts. Since 2025 the two heading rows sit above the 団体コード
    # row; in the archived years 2014–2024 they sit below it, with merged
    # group cells (人口 over 男/女/計) and single-row headings for the change
    # columns. Both are read the same way once located.
    above = [xlsx.cell_text(v).strip() for v in grid.get(header - 2, {}).values()]
    if header >= 3 and ("人口" in above or "住民票記載数" in above):
        group, sub, data_start = grid[header - 2], grid[header - 1], header + 1
    else:
        group, sub = grid.get(header + 1, {}), grid.get(header + 2, {})
        data_start = header + 3
        if not any(xlsx.cell_text(v).strip() == "住民票記載数" for v in group.values()):
            raise ValidationError("%s: no heading rows found around the 団体コード "
                                  "row %d" % (label, header))
    title = xlsx.cell_text(grid.get(1, {}).get("A"))
    years = _era_years(title)
    if len(years) < 2:
        raise ValidationError(
            "%s: title %r does not name both a stock year and a flow year"
            % (label, title[:60]))
    stock_year, flow_year = years[0], years[1]
    if flow_year != stock_year - 1:
        raise ValidationError(
            "%s: flow year %d is not the year before stock year %d — the "
            "reference basis has changed" % (label, flow_year, stock_year))

    columns = {}
    current_group = ""
    for column in sorted(set(group) | set(sub), key=_column_number):
        if _column_number(column) < 3:
            continue          # 団体コード, 都道府県名
        g = xlsx.cell_text(group.get(column)).strip()
        g = _HEADING_SPELLING.get(g, g)
        current_group = g or current_group
        sb = xlsx.cell_text(sub.get(column)).strip()
        sb = _HEADING_SPELLING.get(sb, sb)
        if not sb and g:
            sb = g        # a one-row heading (増減数, 世帯数) in the old layout
        pair = (current_group, sb)
        if not pair[1]:
            continue
        if pair not in COLUMN_MEASURE:
            raise ValidationError(
                "%s column %s: unrecognised header %r — the workbook layout "
                "has changed" % (label, column, pair))
        columns[column] = COLUMN_MEASURE[pair]

    values = {}
    for row in sorted(grid):
        if row < data_start:
            continue
        cells = grid[row]
        name = xlsx.cell_text(cells.get("B")).strip()
        if not name:
            continue
        geo = _geo_code(xlsx.cell_text(cells.get("A")), name, label, row)
        row_values = {}
        for column, measure in columns.items():
            text = xlsx.cell_text(cells.get(column)).strip()
            if not text:
                continue          # blank is missing, never zero
            row_values[measure] = _number(text, label, row, column)
        values[geo] = (name, row_values)
    return stock_year, flow_year, values


def _parse_age(raw, segment):
    """One age-band workbook -> (stock year, {geo: (name, {measure: value})})."""
    label = "%s age workbook" % segment
    grid = _sheet(raw, label)
    header = _header_row(grid, label)
    title = xlsx.cell_text(grid.get(1, {}).get("A"))
    years = _era_years(title)
    if not years:
        raise ValidationError(
            "%s: title %r names no year" % (label, title[:60]))
    stock_year = years[0]

    bands = grid[header - 1]
    columns = {}
    for column in sorted(bands):
        text = xlsx.cell_text(bands.get(column)).strip()
        if not text:
            continue
        if text not in AGE_STEM:
            raise ValidationError(
                "%s column %s: unrecognised age band %r" % (label, column, text))
        columns[column] = AGE_STEM[text]
    if len(columns) != len(AGE_BANDS):
        raise ValidationError(
            "%s: %d age columns, expected %d"
            % (label, len(columns), len(AGE_BANDS)))

    values = {}
    for row in sorted(grid):
        if row <= header:
            continue
        cells = grid[row]
        name = xlsx.cell_text(cells.get("B")).strip()
        if not name:
            continue
        sex_ja = xlsx.cell_text(cells.get("C")).strip()
        if sex_ja not in SEX_SUFFIX:
            raise ValidationError(
                "%s row %d: unrecognised sex %r" % (label, row, sex_ja))
        suffix = SEX_SUFFIX[sex_ja]
        geo = _geo_code(xlsx.cell_text(cells.get("A")), name, label, row)
        row_values = values.setdefault(geo, (name, {}))[1]
        for column, stem in columns.items():
            text = xlsx.cell_text(cells.get(column)).strip()
            if not text:
                continue
            row_values["%s_%s" % (stem, suffix)] = _number(
                text, label, row, column)
    return stock_year, values


# Our recomputed change rate must match the ministry's published one. Both
# are the same arithmetic on the same integers, so the only slack needed is
# for the ministry's own rounding.
RATE_TOLERANCE_PP = 0.0005


def _check_published_rates(segment, geo_values):
    """Recompute 増減率 and compare with the published column.

    The rate is the change over the year against the population at the
    start of it, which the workbook does not print — it is the published
    population minus the published net change.
    """
    worst = 0.0
    for geo, (_name, values) in geo_values.items():
        published = values.get("~rate_net")
        if published is None:
            continue
        opening = values["population"] - values["net_change"]
        if opening <= 0:
            raise ValidationError(
                "%s %s: implied opening population %s" % (segment, geo, opening))
        ours = values["net_change"] / opening * 100.0
        gap = abs(ours - published)
        if gap > RATE_TOLERANCE_PP:
            raise ValidationError(
                "%s %s: our change rate %.6f%% differs from the published "
                "%.6f%% by %.6f pp" % (segment, geo, ours, published, gap))
        worst = max(worst, gap)
    return worst


def parse(raw_bytes):
    envelope = json.loads(raw_bytes.decode("utf-8"))
    expected = set("%s.%s" % (s, t) for s, _ja, _en in SEGMENTS
                   for t in ("dynamics", "age"))
    current_keys = set(k for k in envelope if not k.startswith("archive."))
    if current_keys != expected:
        raise ValidationError(
            "artifact holds %s, expected %s"
            % (sorted(current_keys), sorted(expected)))

    names = {}                    # geo -> the name the ministry printed
    stock_years = set()
    flow_years = set()
    rate_gap = 0.0
    observations = []
    series = {}                   # code -> series dict

    def add(geo, segment, measure, order, name_en, name_ja, unit, period, value):
        code = "%s.%s.%s" % (geo, segment, measure)
        if code not in series:
            series[code] = {
                "code": code,
                "name_en": name_en,
                "name_ja": name_ja,
                "unit": unit,
                "weight_per_10000": None,
                "sort_order": order,
            }
        observations.append({"code": code, "period": period, "value": value})

    geo_index = dict((code, i) for i, (code, _ja, _en)
                     in enumerate([NATIONAL] + PREFECTURES))
    segment_index = dict((code, i) for i, (code, _ja, _en) in enumerate(SEGMENTS))

    for segment, _ja, _en in SEGMENTS:
        stock_year, flow_year, dynamics = _parse_dynamics(
            base64.b64decode(envelope["%s.dynamics" % segment]), segment)
        stock_years.add(stock_year)
        flow_years.add(flow_year)
        rate_gap = max(rate_gap, _check_published_rates(segment, dynamics))

        age_year, ages = _parse_age(
            base64.b64decode(envelope["%s.age" % segment]), segment)
        if age_year != stock_year:
            raise ValidationError(
                "%s: age workbook is for %d but the population workbook is "
                "for %d" % (segment, age_year, stock_year))

        stock_period = datetime.date(stock_year, 1, 1)
        flow_period = datetime.date(flow_year, 1, 1)

        for geo, (name, values) in dynamics.items():
            names.setdefault(geo, name)
            if names[geo] != name:
                raise ValidationError(
                    "%s names area %s %r, elsewhere %r"
                    % (segment, geo, name, names[geo]))
            base = geo_index.get(geo)
            if base is None:
                raise ValidationError(
                    "%s: unknown area code %s (%s)" % (segment, geo, name))
            for measure, value in values.items():
                if measure.startswith("~"):
                    continue      # published rate: checked, never stored
                order = (base * 10000 + segment_index[segment] * 1000
                         + MEASURE_ORDER[measure])
                add(geo, segment, measure, order,
                    "%s — %s (%s)" % (_geo_en(geo), MEASURE_LABEL[measure],
                                      SEGMENT_EN[segment]),
                    "%s %s（%s）" % (name, MEASURE_JA[measure],
                                    SEGMENT_JA[segment]),
                    MEASURE_UNIT[measure],
                    stock_period if MEASURE_KIND[measure] == "stock"
                    else flow_period,
                    value)

        for geo, (name, values) in ages.items():
            base = geo_index.get(geo)
            if base is None:
                raise ValidationError(
                    "%s age: unknown area code %s (%s)" % (segment, geo, name))
            for measure, value in values.items():
                stem, suffix = measure.rsplit("_", 1)
                order = (base * 10000 + segment_index[segment] * 1000
                         + 100 + AGE_ORDER[stem] * 3
                         + ["total", "male", "female"].index(suffix))
                add(geo, segment, measure, order,
                    "%s — %s, %s (%s)"
                    % (_geo_en(geo), AGE_LABEL[stem], SEX_LABEL[suffix],
                       SEGMENT_EN[segment]),
                    "%s 年齢階級別人口 %s（%s・%s）"
                    % (name, _age_ja(stem), _sex_ja(suffix),
                       SEGMENT_JA[segment]),
                    "persons", stock_period, value)

    if len(stock_years) != 1 or len(flow_years) != 1:
        raise ValidationError(
            "the three segments disagree on the reference years: stock %s, "
            "flow %s" % (sorted(stock_years), sorted(flow_years)))

    # Earlier years, from e-Stat's archive of the same workbooks: population
    # and households at 1 January, the register flows of the year before.
    # Age bands are the current year's only. The archive also holds the
    # current year; that copy is compared with the ministry's, not added.
    current_stock = sorted(stock_years)[0]
    archive_years = sorted(set(int(k.split(".")[1]) for k in envelope
                               if k.startswith("archive.")))
    added_years, current_compared, current_differences = [], 0, 0
    current_values = dict(((o["code"], o["period"]), o["value"]) for o in observations)
    for year in archive_years:
        if year > current_stock:
            raise ValidationError("e-Stat's archive holds %d, after the ministry's "
                                  "current %d" % (year, current_stock))
        for segment, _ja, _en in SEGMENTS:
            key = "archive.%d.%s.dynamics" % (year, segment)
            if key not in envelope:
                raise ValidationError("the archive has no %d %s workbook" % (year, segment))
            a_stock, a_flow, dynamics = _parse_dynamics(
                base64.b64decode(envelope[key]), segment, str(year))
            if a_stock != year:
                raise ValidationError("archived %d %s workbook is for %d"
                                      % (year, segment, a_stock))
            rate_gap = max(rate_gap, _check_published_rates(segment, dynamics))
            stock_period = datetime.date(a_stock, 1, 1)
            flow_period = datetime.date(a_flow, 1, 1)
            for geo, (name, values) in dynamics.items():
                if names.get(geo) != name:
                    raise ValidationError(
                        "archived %d %s names area %s %r, the current files %r"
                        % (year, segment, geo, name, names.get(geo)))
                for measure, value in values.items():
                    if measure.startswith("~"):
                        continue
                    period = (stock_period if MEASURE_KIND[measure] == "stock"
                              else flow_period)
                    if year == current_stock:
                        code = "%s.%s.%s" % (geo, segment, measure)
                        current_compared += 1
                        if current_values.get((code, period)) != value:
                            current_differences += 1
                        continue
                    base = geo_index[geo]
                    order = (base * 10000 + segment_index[segment] * 1000
                             + MEASURE_ORDER[measure])
                    add(geo, segment, measure, order,
                        "%s — %s (%s)" % (_geo_en(geo), MEASURE_LABEL[measure],
                                          SEGMENT_EN[segment]),
                        "%s %s（%s）" % (name, MEASURE_JA[measure], SEGMENT_JA[segment]),
                        MEASURE_UNIT[measure], period, value)
        if year != current_stock:
            added_years.append(year)
    if current_differences:
        raise ValidationError(
            "e-Stat's copy of the %d workbooks differs from the ministry's in %d "
            "of %d values" % (current_stock, current_differences, current_compared))

    _AUDIT.clear()
    _AUDIT.update({
        "stock_year": sorted(stock_years)[0],
        "flow_year": sorted(flow_years)[0],
        "max_rate_gap_pp": round(rate_gap, 8),
        "area_names": names,
        "archive_stock_years": added_years,
        "archive_current_year_values_compared": current_compared,
    })
    return sorted(series.values(), key=lambda s: s["sort_order"]), observations


def _geo_en(geo):
    if geo == NATIONAL[0]:
        return NATIONAL[2]
    for code, _ja, en in PREFECTURES:
        if code == geo:
            return en
    return geo


def _age_ja(stem):
    for ja, s, _en in AGE_BANDS:
        if s == stem:
            return ja
    return stem


def _sex_ja(suffix):
    for ja, s, _en in SEXES:
        if s == suffix:
            return ja
    return suffix


# --- validate ---------------------------------------------------------------

# Sanity bands. Registered residents of Japan have been between 125 and 130
# million this decade and are falling; foreign residents passed 3 million in
# 2023 and are rising fast. Wide enough that a real move never trips them,
# tight enough that a parsing fault does.
POPULATION_MIN = 100_000_000
POPULATION_MAX = 140_000_000
FOREIGN_MIN = 1_000_000
FOREIGN_MAX = 15_000_000

# Residents whose record carries no usable birth date or sex. Real, published
# that way, and very unevenly spread: 43 people in 123.8 million nationally in
# the 2026 file, but 1,258 of Hokkaido's 75,392 foreign residents and 200 of
# Kochi's 3,335 foreign women. The ceilings are set well above what the data
# does and well below what a mis-parsed column would do — a dropped band
# shows up as a double-digit share, a shifted one as a negative residual.
MAX_UNRECORDED_SHARE_NATIONAL = 0.01     # per cent of the national total
MAX_UNRECORDED_SHARE_AREA = 15.0         # per cent of one area's total

# The prefecture age bands do not add up to the national age bands, and are
# not meant to: for foreign residents, about 6,200 people whose age is not
# banded in their prefecture's row are banded in the national row. The
# difference is one-directional and small; a surface that adds the 47
# prefectures has to disclose it rather than present it as the national
# figure. (The Japanese-resident workbook reconciles exactly.)
MAX_BAND_EXCESS_SHARE = 1.0              # per cent of the national population


def _check_identities(value, prefecture_codes, segments):
    """Checks 2–4 on one year's values; returns the reconciliations made."""
    # 2. The 47 prefectures add to the ministry's own national row, exactly.
    #    These are integer counts of people: near enough is not good enough.
    reconciled = 0
    for segment in segments:
        for measure, _l, _k, _u in MEASURES:
            national_code = "%s.%s.%s" % (NATIONAL[0], segment, measure)
            if national_code not in value:
                continue
            total = value[national_code]
            summed = sum(value["%s.%s.%s" % (geo, segment, measure)]
                         for geo in prefecture_codes
                         if "%s.%s.%s" % (geo, segment, measure) in value)
            if total != summed:
                raise ValidationError(
                    "%s %s: 47 prefectures sum to %d, the national row says %d"
                    % (segment, measure, summed, total))
            reconciled += 1

    # 3. All residents = Japanese + foreign, everywhere, exactly.
    for geo in [NATIONAL[0]] + prefecture_codes:
        for measure in ("population", "population_male", "population_female",
                        "households", "births", "deaths", "net_change"):
            keys = ["%s.%s.%s" % (geo, s, measure) for s in ("all", "jp", "fgn")]
            if not all(k in value for k in keys):
                continue
            if value[keys[0]] != value[keys[1]] + value[keys[2]]:
                raise ValidationError(
                    "%s %s: all residents %d but Japanese %d + foreign %d"
                    % (geo, measure, value[keys[0]], value[keys[1]], value[keys[2]]))

    # 4. The register's own arithmetic. Additions less deletions is the net
    #    change; births less deaths is the natural change; the two parts of
    #    the change add back to it. (In-migrants less out-migrants is *not*
    #    the social change — see the module docstring.)
    for segment in segments:
        for geo in [NATIONAL[0]] + prefecture_codes:
            def v(measure):
                return value["%s.%s.%s" % (geo, segment, measure)]
            if v("additions_total") - v("deletions_total") != v("net_change"):
                raise ValidationError(
                    "%s %s: additions less deletions is not the net change" % (segment, geo))
            if v("births") - v("deaths") != v("natural_change"):
                raise ValidationError(
                    "%s %s: births less deaths is not the natural change" % (segment, geo))
            if v("natural_change") + v("social_change") != v("net_change"):
                raise ValidationError(
                    "%s %s: natural and social change do not add to the net change"
                    % (segment, geo))

    return reconciled


def validate(series, observations):
    if not _AUDIT:
        raise ValidationError("validate() called before parse()")

    by_code = {}
    for o in observations:
        key = (o["code"], o["period"])
        if key in by_code:
            raise ValidationError("duplicate observation %s %s" % key)
        by_code[key] = o["value"]
    def edition(stock_year):
        """{code: value} of one year's workbooks: stocks at 1 January of
        stock_year, flows over the year before."""
        stock, flow = datetime.date(stock_year, 1, 1), datetime.date(stock_year - 1, 1, 1)
        return dict((code, v) for (code, period), v in by_code.items()
                    if period == (stock if MEASURE_KIND.get(code.split(".", 2)[2], "stock")
                                  == "stock" else flow))
    value = edition(_AUDIT["stock_year"])

    prefecture_codes = [code for code, _ja, _en in PREFECTURES]
    segments = [code for code, _ja, _en in SEGMENTS]

    # 1. Every prefecture is present, named exactly as we expect it.
    names = _AUDIT["area_names"]
    for code, ja, _en in [NATIONAL] + PREFECTURES:
        if code not in names:
            raise ValidationError("area %s (%s) missing from the workbooks" % (code, ja))
        if names[code] != ja:
            raise ValidationError(
                "area %s is named %r, expected %r" % (code, names[code], ja))
    unexpected = sorted(set(names) - set([NATIONAL[0]] + prefecture_codes))
    if unexpected:
        raise ValidationError("unexpected areas in the workbooks: %s" % unexpected)

    reconciled = _check_identities(value, prefecture_codes, segments)
    # The same identities in every archived year, and the bridge between
    # years: a year's population is the year before's plus its net change.
    bridged = 0
    for year in _AUDIT.get("archive_stock_years", []):
        reconciled += _check_identities(edition(year), prefecture_codes, segments)
    for year in _AUDIT.get("archive_stock_years", []):
        before, after = edition(year), edition(year + 1)
        for code, pop in before.items():
            if not code.endswith(".population"):
                continue
            nxt = after.get(code)
            net = after.get(code[:-len("population")] + "net_change")
            if nxt is None or net is None:
                continue
            if pop + net != nxt:
                raise ValidationError(
                    "%s: %d at 1 January %d plus the %d net change %d is %d, but "
                    "1 January %d says %d" % (code, pop, year, year, net, pop + net,
                                              year + 1, nxt))
            bridged += 1

    # 5. Age bands against their own total, and against the population.
    #    The shortfall is people with no usable birth date; it is real and
    #    stays in the published totals, but it has to stay negligible.
    unrecorded_age = {}
    unrecorded_sex = {}
    worst_unrecorded = 0.0
    band_excess = {}
    for segment in segments:
        for geo in [NATIONAL[0]] + prefecture_codes:
            for suffix in ("total", "male", "female"):
                total = value["%s.%s.age_total_%s" % (geo, segment, suffix)]
                banded = sum(
                    value.get("%s.%s.%s_%s" % (geo, segment, stem, suffix), 0.0)
                    for stem in AGE_ORDER if stem != "age_total")
                gap = total - banded
                if gap < 0:
                    raise ValidationError(
                        "%s %s %s: age bands exceed the published total by %d"
                        % (segment, geo, suffix, -gap))
                share = gap / total * 100.0 if total else 0.0
                ceiling = (MAX_UNRECORDED_SHARE_NATIONAL
                           if geo == NATIONAL[0] else MAX_UNRECORDED_SHARE_AREA)
                if share > ceiling:
                    raise ValidationError(
                        "%s %s %s: %d of %d have no age band (%.4f%%) — above "
                        "the %.4f%% ceiling"
                        % (segment, geo, suffix, gap, total, share, ceiling))
                worst_unrecorded = max(worst_unrecorded, share)
                if geo == NATIONAL[0]:
                    unrecorded_age["%s.%s" % (segment, suffix)] = int(gap)
            head = value["%s.%s.age_total_total" % (geo, segment)]
            if head != value["%s.%s.population" % (geo, segment)]:
                raise ValidationError(
                    "%s %s: the age workbook totals %d, the population "
                    "workbook %d" % (segment, geo, head,
                                     value["%s.%s.population" % (geo, segment)]))
            sex_gap = head - (value["%s.%s.age_total_male" % (geo, segment)]
                              + value["%s.%s.age_total_female" % (geo, segment)])
            if sex_gap < 0:
                raise ValidationError(
                    "%s %s: male plus female exceeds the total by %d"
                    % (segment, geo, -sex_gap))
            sex_share = sex_gap / head * 100.0 if head else 0.0
            sex_ceiling = (MAX_UNRECORDED_SHARE_NATIONAL
                           if geo == NATIONAL[0] else MAX_UNRECORDED_SHARE_AREA)
            if sex_share > sex_ceiling:
                raise ValidationError(
                    "%s %s: %d of %d have no recorded sex (%.4f%%) — above "
                    "the %.4f%% ceiling"
                    % (segment, geo, sex_gap, head, sex_share, sex_ceiling))
            if geo == NATIONAL[0]:
                unrecorded_sex[segment] = int(sex_gap)

        # The national row's age bands against the sum of the 47. They do
        # not have to match, but the national row may only ever hold *more*
        # people than the prefectures band, and not many more.
        excess = 0.0
        for suffix in ("total", "male", "female"):
            for stem in AGE_ORDER:
                if stem == "age_total":
                    continue
                national = value["%s.%s.%s_%s" % (NATIONAL[0], segment, stem, suffix)]
                summed = sum(
                    value["%s.%s.%s_%s" % (geo, segment, stem, suffix)]
                    for geo in prefecture_codes)
                if national < summed:
                    raise ValidationError(
                        "%s %s %s: the 47 prefectures band %d people, more "
                        "than the national row's %d"
                        % (segment, stem, suffix, summed, national))
                if suffix == "total":
                    excess += national - summed
        national_population = value["%s.%s.population" % (NATIONAL[0], segment)]
        if excess / national_population * 100.0 > MAX_BAND_EXCESS_SHARE:
            raise ValidationError(
                "%s: the national age bands hold %d more people than the 47 "
                "prefectures do (%.4f%%)"
                % (segment, excess, excess / national_population * 100.0))
        band_excess[segment] = int(excess)

    # 6. Sanity on the two headline levels.
    national_all = value["%s.all.population" % NATIONAL[0]]
    national_foreign = value["%s.fgn.population" % NATIONAL[0]]
    if not (POPULATION_MIN <= national_all <= POPULATION_MAX):
        raise ValidationError(
            "national population %d outside the sanity band" % national_all)
    if not (FOREIGN_MIN <= national_foreign <= FOREIGN_MAX):
        raise ValidationError(
            "foreign residents %d outside the sanity band" % national_foreign)

    stock_year = _AUDIT["stock_year"]
    latest = max(o["period"] for o in observations)
    if latest != datetime.date(stock_year, 1, 1):
        raise ValidationError(
            "latest period %s does not match the stock year %d"
            % (latest, stock_year))
    this_year = datetime.date.today().year
    if not (this_year - 1 <= stock_year <= this_year + 1):
        raise ValidationError(
            "stock year %d is implausible for a file fetched in %d"
            % (stock_year, this_year))

    elderly = sum(value["%s.all.%s_total" % (NATIONAL[0], stem)]
                  for stem in AGE_65_PLUS)
    return {
        "series": len(series),
        "observations": len(observations),
        "latest_period": latest.isoformat(),
        "stock_year": stock_year,
        "flow_year": _AUDIT["flow_year"],
        "areas": len(names),
        "identities_reconciled": reconciled,
        "history_stock_years": ([min(_AUDIT["archive_stock_years"]), stock_year]
                                if _AUDIT.get("archive_stock_years") else [stock_year]),
        "population_bridges_checked": bridged,
        "max_rate_gap_pp": _AUDIT["max_rate_gap_pp"],
        "population_total": int(national_all),
        "population_foreign": int(national_foreign),
        "share_65_plus_pct": round(elderly / national_all * 100.0, 2),
        "unrecorded_age": unrecorded_age,
        "unrecorded_sex": unrecorded_sex,
        "worst_unrecorded_age_share_pct": round(worst_unrecorded, 4),
        "national_band_excess": band_excess,
        "resolved_urls": dict(_RESOLVED),
    }


# --- presentation -----------------------------------------------------------
#
# What a surface needs to know about this dataset's shape: which areas
# exist, how they group, what each measure means and whether it is a level
# or a flow. The API stays dataset-agnostic; this is the dataset's own
# vocabulary.

PRESENTATION = {
    "credit_line": ("Source: Ministry of Internal Affairs and Communications, "
                    "Japan — Basic Resident Register."),
    # Annual, as of 1 January, published in late July: about eighteen months
    # before the next release is even due.
    "stale_after_days": 600,
    "prefectures": {
        "headline": "population",
        "national": NATIONAL[0],
        "geographies": (
            [{"code": NATIONAL[0], "name_ja": NATIONAL[1], "name_en": NATIONAL[2],
              "region": None}]
            + [{"code": code, "name_ja": ja, "name_en": en,
                "region": REGION_OF[code]} for code, ja, en in PREFECTURES]),
        "regions": [{"key": key, "label": en, "label_ja": ja,
                     "prefectures": list(codes)}
                    for key, en, ja, codes in REGIONS],
        "segments": [{"code": code, "label": en, "label_ja": ja}
                     for code, ja, en in SEGMENTS],
        "measures": [{"code": code, "label": label, "kind": kind, "unit": unit}
                     for code, label, kind, unit in MEASURES],
        "age_bands": [{"code": stem, "label": en, "label_ja": ja}
                      for ja, stem, en in AGE_BANDS if stem != "age_total"],
        "age_groups": {
            "under_15": AGE_UNDER_15,
            "working_15_64": AGE_WORKING,
            "aged_65_plus": AGE_65_PLUS,
        },
        "sexes": [{"code": suffix, "label": en} for _ja, suffix, en in SEXES],
    },
}


# The dataset's card. Administrative counts; the shares and change rates on
# the population page are computed there and their formulas are recorded here.
MANIFEST = {
    "id": DATASET["slug"],
    "section": "demography",
    "name": {"en": "Population by prefecture — Basic Resident Register",
             "ja": "住民基本台帳に基づく人口・世帯数（都道府県別）"},
    "shape": "series",
    "summary": ("Registered residents, households and the year's register "
                "flows for all 47 prefectures and the nation, as of 1 January, "
                "split into all, Japanese and foreign residents with five-year "
                "age bands by sex — administrative counts, not survey estimates."),
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
        "as_of_supported": True, "history_from": "2013-01-01",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Registered residents, households or register flows, as published",
         "unit": "persons", "trust": "official"},
        {"id": "yoy", "label": "Year over year", "unit": "%", "trust": "derived",
         "where": "annual series: t−12 months is the previous 1 January",
         "calc": "(value[t] / value[t−12 months] − 1) × 100, from published values."},
        {"id": "change_pct", "label": "Change during the year", "unit": "%",
         "trust": "derived",
         "calc": "change % = net change ÷ (population − net change) × 100"},
        {"id": "natural_pct", "label": "Natural change", "unit": "%", "trust": "derived",
         "calc": "natural % = (births − deaths) ÷ (population − net change) × 100"},
        {"id": "social_pct", "label": "Social change", "unit": "%", "trust": "derived",
         "calc": "social % = (net change − natural change) ÷ (population − net change) × 100"},
        {"id": "foreign_pct", "label": "Foreign residents' share", "unit": "%",
         "trust": "derived",
         "calc": "foreign share % = foreign residents ÷ all residents × 100"},
        {"id": "aged_pct", "label": "Aged 65 and over", "unit": "%", "trust": "derived",
         "calc": ("aged 65+ % = (sum of the five-year bands from 65 upward) ÷ all "
                  "residents × 100")},
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
        "Population and households run from 1 January 2014 and the register "
        "flows from calendar 2013, read from e-Stat's archive of the "
        "ministry's workbooks (the ministry keeps only the current year). Age "
        "bands are the current year only. The long run before 2014 is "
        "population-jp-history.",
        "Counts are as of 1 January; flows cover the preceding calendar year.",
    ],
}
