"""Adapter: rice contract prices — MAFF 米の相対取引価格.

Source: 農林水産省, 「米の相対取引価格・数量」. Every month MAFF surveys the
shippers that handle 5,000 tonnes of rice a year or more and publishes the
volume-weighted average price actually contracted between them and wholesalers,
separately for each 産地品種銘柄 — origin-and-variety brand, about 120 of them.
The figure is yen per 60kg of brown rice, inclusive of freight, packaging and
consumption tax, for first-grade table rice.

This is the benchmark price of Japanese rice. It is what the 2025-26 rice
crisis was measured in, it is the number farm policy is argued about, and no
English-language product carries it at brand level.

**Shape of the source.** One workbook per crop year, each a grid of brands
down the side and the crop year's twelve months across the top — September
of the crop year through the following August, which is how the rice trade
counts a year. Crop years tile without overlap, so a calendar month belongs
to exactly one workbook; a gate below checks that stays true. Prices are
provisional (速報) and MAFF does not revise them once the crop year closes.

**Two file formats, one history.** The 2019 crop year onward is .xlsx; the
eleven crop years before it are legacy .xls, and the 2018 file is served 340
bytes short of a whole sector. Reading only the modern half would throw away
two thirds of the history, so `xls.py` exists and both readers return the same
shape. See its module docstring.

**A brand's identity survived a layout change.** Through the 2017 crop year
the table carried a third key column, 地域区分, so Uonuma Koshihikari was
(新潟, コシヒカリ, 魚沼); from 2019 the region moved inside the brand name as
コシヒカリ（魚沼）. Both are folded to the same series here, because they are
the same series — the alternative is every regional Koshihikari chart breaking
in half at 2018.

**Brand names are curated, and an unknown brand fails the ingest.** MAFF
publishes no code for a 産地品種銘柄; the Japanese name is the identity. Series
codes are built from a checked romanisation (`price.15.koshihikari.uonuma`), so
a brand that is not in `BRANDS` cannot be given a code without guessing. It
raises instead. That is deliberate: ingest is fail-safe, so the previous
release stays live and `/api/v1/catalog/health` reports an unpublished
artifact the same day — which is the signal to add the new variety, not a
reason to invent a code for it. New varieties appear once or twice a year.

**Missing is missing.** A dash means either that no contract was struck for
that brand that month or that the month's contracted volume was under 100
tonnes and MAFF suppresses the price. Neither is zero, and the two are not
distinguishable in the file, so both stay gaps.
"""
import base64
import datetime
import json
import os
import re
import urllib.parse

from . import boj_ts, xls, xlsx  # boj_ts only for its shared fetch_bytes
from .juki_population import PREFECTURES


class ValidationError(Exception):
    pass


BASE = "https://www.maff.go.jp/j/seisan/keikaku/soukatu/"
# The current crop year's table is published on the monthly release page; the
# closed crop years live on the price archive. Both are read, so a new crop
# year appears on its own without a code change.
PAGE_CURRENT = BASE + "aitaikakaku.html"
PAGE_ARCHIVE = BASE + "kakaku.html"
DOWNLOAD_URL = PAGE_CURRENT

RAW_SUFFIX = ".json"

# The crop-year workbooks, as linked from those two pages. The label is the
# only thing that says which crop year a file is, and it always reads
# "◯年産米の相対取引価格（速報）（◯年9月～◯年8月）" — the monthly releases on
# the same page say 「相対取引価格・数量」 and a single month, so they are not
# mistaken for one of these.
_CROP_YEAR_LABEL = re.compile(
    r"(平成|令和)\s*(元|[0-9０-９]+)\s*年産米の相対取引価格（速報）"
    r"（[^）]*?年\s*9\s*月\s*[~〜～]\s*[^）]*?年\s*8\s*月）")
_ANCHOR = re.compile(r"<a\b[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S | re.I)
_CHUNK = re.compile(r"</(?:li|p)>", re.I)
_TAGS = re.compile(r"<[^>]+>")

# Era → the Gregorian year of its year 1. 平成1 = 1989, 令和1 = 2019.
_ERA_BASE = {"平成": 1988, "令和": 2018}

# The oldest crop year MAFF keeps on the archive page. Used only as a gate:
# if the pages stop listing back this far, the history has silently shrunk.
FIRST_CROP_YEAR = 2008

_FULLWIDTH_DIGITS = dict(zip("０１２３４５６７８９", "0123456789"))

# 産地品種銘柄 romanisations, checked against MAFF's own English variety lists
# and the prefecture brand councils. The slug (lower-case, ASCII) is what the
# series code is built from and must never change once published; the label is
# what a reader sees. Adding a brand is the maintenance task this adapter
# raises for — see the module docstring.
BRANDS = {
    "あいちのかおり": ("aichinokaori", "Aichi no Kaori"),
    "あいちのこころ": ("aichinokokoro", "Aichi no Kokoro"),   # first priced 2026-09
    "ひなたまる": ("hinatamaru", "Hinatamaru"),                # first priced 2026-09
    "くまさんの輝き": ("kumasannokagayaki", "Kumasan no Kagayaki"),  # Kumamoto; first priced 2026-09
    "あきさかり": ("akisakari", "Akisakari"),
    "あきたこまち": ("akitakomachi", "Akitakomachi"),
    "あきほなみ": ("akihonami", "Akihonami"),
    "あきろまん": ("akiroman", "Akiroman"),
    "あさひの夢": ("asahinoyume", "Asahi no Yume"),
    "いわてっこ": ("iwatekko", "Iwatekko"),
    "おいでまい": ("oidemai", "Oidemai"),
    "きぬむすめ": ("kinumusume", "Kinumusume"),
    "きらら３９７": ("kirara397", "Kirara 397"),
    "こしいぶき": ("koshiibuki", "Koshiibuki"),
    "さがびより": ("sagabiyori", "Sagabiyori"),
    "つがるロマン": ("tsugaruroman", "Tsugaru Roman"),
    "つや姫": ("tsuyahime", "Tsuyahime"),
    "てんたかく": ("tentakaku", "Tentakaku"),
    "とちぎの星": ("tochiginohoshi", "Tochigi no Hoshi"),
    "なすひかり": ("nasuhikari", "Nasuhikari"),
    "なつほのか": ("natsuhonoka", "Natsuhonoka"),
    "ななつぼし": ("nanatsuboshi", "Nanatsuboshi"),
    "にこまる": ("nikomaru", "Nikomaru"),
    "にじのきらめき": ("nijinokirameki", "Niji no Kirameki"),
    "はえぬき": ("haenuki", "Haenuki"),
    "はれわたり": ("harewatari", "Harewatari"),
    "ひとめぼれ": ("hitomebore", "Hitomebore"),
    "ひめの凜": ("himenorin", "Himeno Rin"),
    "ひゃくまん穀": ("hyakumangoku", "Hyakumangoku"),
    "ふくまる": ("fukumaru", "Fukumaru"),
    "ふさおとめ": ("fusaotome", "Fusaotome"),
    "ふさこがね": ("fusakogane", "Fusakogane"),
    "ほしじるし": ("hoshijirushi", "Hoshijirushi"),
    "ほしのゆめ": ("hoshinoyume", "Hoshinoyume"),
    "まっしぐら": ("masshigura", "Masshigura"),
    "まなむすめ": ("manamusume", "Manamusume"),
    "みずかがみ": ("mizukagami", "Mizukagami"),
    "むつほまれ": ("mutsuhomare", "Mutsuhomare"),
    "めんこいな": ("menkoina", "Menkoina"),
    "ゆめひたち": ("yumehitachi", "Yumehitachi"),
    "ゆめぴりか": ("yumepirika", "Yumepirika"),
    "ゆめまつり": ("yumematsuri", "Yumematsuri"),
    "ゆめみづほ": ("yumemizuho", "Yumemizuho"),
    "アケボノ": ("akebono", "Akebono"),
    "キヌヒカリ": ("kinuhikari", "Kinuhikari"),
    "コシヒカリ": ("koshihikari", "Koshihikari"),
    "ゴロピカリ": ("goropikari", "Goropikari"),
    "ササニシキ": ("sasanishiki", "Sasanishiki"),
    "ハツシモ": ("hatsushimo", "Hatsushimo"),
    "ハナエチゼン": ("hanaechizen", "Hanaechizen"),
    "ヒノヒカリ": ("hinohikari", "Hinohikari"),
    "ミルキークイーン": ("milkyqueen", "Milky Queen"),
    "元気つくし": ("genkitsukushi", "Genki Tsukushi"),
    "夢しずく": ("yumeshizuku", "Yume Shizuku"),
    "夢つくし": ("yumetsukushi", "Yume Tsukushi"),
    "大地の風": ("daichinokaze", "Daichi no Kaze"),
    "天のつぶ": ("tennotsubu", "Ten no Tsubu"),
    "富富富": ("fufufu", "Fufufu"),
    "彩のかがやき": ("sainokagayaki", "Sai no Kagayaki"),
    "彩のきずな": ("sainokizuna", "Sai no Kizuna"),
    "日本晴": ("nihonbare", "Nihonbare"),
    "森のくまさん": ("morinokumasan", "Mori no Kumasan"),
    "銀河のしずく": ("gingamoshizuku", "Ginga no Shizuku"),
    "雪若丸": ("yukiwakamaru", "Yukiwakamaru"),
}

# 地域区分 — the sub-prefectural growing area, carried in its own column until
# the 2017 crop year and inside the brand name from 2019.
REGIONS = {
    "一般": ("general", "general"),
    "中通り": ("nakadori", "Nakadōri"),
    "会津": ("aizu", "Aizu"),
    "浜通り": ("hamadori", "Hamadōri"),
    "魚沼": ("uonuma", "Uonuma"),
    "岩船": ("iwafune", "Iwafune"),
    "佐渡": ("sado", "Sado"),
    "伊賀": ("iga", "Iga"),
}

# The all-brand average MAFF prints as the last row: the volume-weighted mean
# across every reported brand, weighted by the *previous* crop year's inspected
# quantity. It is published, not recomputed here.
ALL_BRANDS_JA = "全銘柄平均価格"
ALL_BRANDS_CODE = "price.00.all-brands"

# MAFF writes 産地 without the 都/道/府/県 suffix — 京都, not 京都府 — so both
# spellings map to the same JIS code. Exactly one trailing character is
# dropped: 京都府 is 京都, never 京.
_PREF_JIS = {}
for _code, _ja, _en in PREFECTURES:
    _PREF_JIS[_ja] = (_code, _en)
    if _ja != "北海道" and _ja[-1] in "都道府県":
        _PREF_JIS[_ja[:-1]] = (_code, _en)

DATASET = {
    "slug": "rice-prices-jp",
    "title": "Rice Contract Prices — by Origin and Variety (Japan)",
    "country": "Japan",
    "agency": "Ministry of Agriculture, Forestry and Fisheries",
    "agency_ja": "農林水産省",
    "base": None,
    "frequency": "monthly",
    "description": (
        "Monthly volume-weighted average contract prices for Japanese table "
        "rice, in yen per 60kg of brown rice including freight, packaging and "
        "consumption tax, for each 産地品種銘柄 origin-and-variety brand and "
        "for the published all-brand average. Surveyed by MAFF among shippers "
        "handling 5,000 tonnes a year or more, monthly from September 2008. "
        "A brand-month with no contract, or with under 100 tonnes contracted, "
        "is not published and stays missing."
    ),
}

SOURCE = {
    "source_id": "maff:aitaikakaku",
    "name": "MAFF — Rice contract prices and volumes (relative transaction prices)",
    "name_ja": "農林水産省 米の相対取引価格・数量",
    "url": PAGE_CURRENT,
    "license_note": (
        "Government of Japan Standard Terms of Use (compatible with CC BY "
        "4.0): free to use with attribution to the Ministry of Agriculture, "
        "Forestry and Fisheries. Figures are provisional (速報) and are the "
        "volume-weighted average of contracts actually struck, not a quote."
    ),
}

PRESENTATION = {
    "credit_line": ("Source: Ministry of Agriculture, Forestry and Fisheries — "
                    "Rice contract prices (米の相対取引価格)."),
    # Published about the 20th of the following month, so the newest period is
    # normally 7-8 weeks old. This allows a release to slip by three weeks
    # before the dataset is called stale.
    "stale_after_days": 95,
    "main_series": [
        {"role": "headline", "code": ALL_BRANDS_CODE,
         "label": "All-brand average", "slot": 1},
        {"role": "premium", "code": "price.15.koshihikari.uonuma",
         "label": "Uonuma Koshihikari", "slot": 2},
        {"role": "volume-brand", "code": "price.01.nanatsuboshi",
         "label": "Hokkaido Nanatsuboshi", "slot": 3},
    ],
    # The strip answers: what is rice trading at, how far off its record, and
    # what do the ends of the range look like. Every brand is a price level,
    # so nothing here is a flow and the rate measures all apply.
    "overview_tiles": [
        {"key": "all_brands", "type": "level", "code": ALL_BRANDS_CODE,
         "label": "All Brands"},
        {"key": "from_peak", "type": "drawdown", "code": ALL_BRANDS_CODE,
         "label": "From Peak"},
        {"key": "uonuma", "type": "level", "code": "price.15.koshihikari.uonuma",
         "label": "Uonuma Koshi"},
        {"key": "nanatsuboshi", "type": "level", "code": "price.01.nanatsuboshi",
         "label": "Nanatsuboshi"},
    ],
    "unit_label": "¥ / 60kg",
}

class _EveryCodeIsALevel(dict):
    """The measure kind of every series here, without enumerating them.

    MAFF adds and drops 産地品種銘柄 every year, so there is no static list of
    brand codes to write down. The API asks this map two things — whether a
    series is a flow, so that percentage changes can be refused where they
    would be arithmetic noise, and whether the dataset is levels-shaped at all
    — and for this dataset both answers are the same for every code: a
    contract price is a level, never a flow.
    """

    def get(self, key, default=None):
        return "level"


PRESENTATION["kinds"] = _EveryCodeIsALevel({ALL_BRANDS_CODE: "level"})


# --- fetching ---------------------------------------------------------------

def _text(html):
    return re.sub(r"\s+", "", _TAGS.sub("", html).replace("&nbsp;", " "))


def _era_year(era, number):
    digits = "".join(_FULLWIDTH_DIGITS.get(c, c) for c in number)
    n = 1 if digits == "元" else int(digits)
    return _ERA_BASE[era] + n


def crop_year_files(html, page_url):
    """{crop year: absolute URL} for every monthly price table on one page.

    A crop year and its workbook always sit in the same list item or
    paragraph — the label is on the PDF link and the spreadsheet follows it —
    so binding within a chunk keeps a stray spreadsheet in the next section
    from being adopted by the last crop year of this one.
    """
    found = {}
    for chunk in _CHUNK.split(html):
        label = _CROP_YEAR_LABEL.search(_text(chunk))
        if not label:
            continue
        year = _era_year(label.group(1), label.group(2))
        for href, _label in _ANCHOR.findall(chunk):
            if href.lower().split("?")[0].endswith((".xls", ".xlsx")):
                found[year] = urllib.parse.urljoin(page_url, href)
                break
    return found


def _discover():
    """{crop year: URL}, newest page first so it wins any duplicate."""
    files = {}
    for page in (PAGE_ARCHIVE, PAGE_CURRENT):
        html = boj_ts.fetch_bytes(page).decode("utf-8", "replace")
        files.update(crop_year_files(html, page))
    if not files:
        raise ValidationError(
            "no crop-year price tables found on %s or %s — the page layout "
            "changed" % (PAGE_ARCHIVE, PAGE_CURRENT))
    return files


def _raw_dir():
    """The ingest's raw archive (app/db.py RAW_DIR): every envelope ever fetched."""
    root = os.environ.get("OBSERVATORY_DATA_DIR")
    if not root:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(here, "..", "..", "data")
    return os.path.join(root, "raw")


def remembered_crop_years(raw_dir=None, slug="rice-prices-jp"):
    """{crop year: {url, b64}} from the newest archived envelope that holds
    each year — the copy this ingest itself fetched when MAFF still linked it.

    MAFF's two pages do not list every closed crop year all the time: between
    21 September and 4 November 2026 the 2025 table left the current page
    before reaching the archive page, and the 2009 table left the archive
    page. A closed year's table does not change, and the raw archive on the
    volume keeps every envelope (failed fetches included, so newest-first
    and per year), so a year the pages have dropped is read from there.
    """
    found = {}
    directory = raw_dir or _raw_dir()
    try:
        names = sorted((n for n in os.listdir(directory)
                        if n.startswith(slug + "-") and n.endswith(RAW_SUFFIX)), reverse=True)
    except OSError:
        return found
    for name in names:
        try:
            with open(os.path.join(directory, name), "rb") as fh:
                years = json.loads(fh.read().decode("utf-8")).get("crop_years") or {}
        except (OSError, ValueError):
            continue
        for year, entry in years.items():
            if year.isdigit() and int(year) not in found and entry.get("b64"):
                found[int(year)] = {"url": entry.get("url"), "b64": entry["b64"]}
    return found


def fetch():
    """Every crop year's workbook, verbatim, in one archived artifact.

    The envelope is deterministic — no timestamps, keys sorted — so the
    ingest runner's SHA-256 comparison means "some crop year's table
    changed", which is exactly when there is something new to publish. A
    closed crop year the pages no longer link comes from the raw archive
    (remembered_crop_years), under the address it was fetched from, so the
    bytes are the ones that would have been there had MAFF kept the link.
    """
    files = _discover()
    envelope = {}
    for year, url in sorted(files.items()):
        envelope[str(year)] = {
            "url": url,
            "b64": base64.b64encode(boj_ts.fetch_bytes(url)).decode("ascii"),
        }
    missing = [y for y in range(FIRST_CROP_YEAR, max(files)) if str(y) not in envelope]
    if missing:
        remembered = remembered_crop_years()
        for year in missing:
            if year in remembered:
                envelope[str(year)] = remembered[year]
    return json.dumps({"crop_years": envelope}, sort_keys=True).encode("utf-8")


# --- parsing ----------------------------------------------------------------

def _read_workbook(raw):
    """One crop-year workbook -> its single sheet's grid, either format."""
    reader = xlsx if raw[:2] == b"PK" else xls
    sheets = reader.sheets(raw)
    if len(sheets) != 1:
        raise ValidationError("expected one sheet, found %s" % sorted(sheets))
    return list(sheets.values())[0]


def _header_row(grid):
    for row in sorted(grid):
        if any("産地" in xlsx.cell_text(c) for c in grid[row].values()):
            return row
    raise ValidationError("no header row containing 産地")


_MONTH = re.compile(r"(\d+)月")


def _columns(grid, header):
    """(key columns, [(column, month number)]) for one sheet's header row."""
    labels = dict((col, re.sub(r"\s+", "", xlsx.cell_text(cell)))
                  for col, cell in grid[header].items())
    keys = {}
    months = []
    for col, label in labels.items():
        if label == "産地":
            keys["origin"] = col
        elif label == "品種銘柄":
            keys["brand"] = col
        elif label == "地域区分":
            keys["region"] = col
        else:
            m = _MONTH.search(label)
            if m:
                months.append((col, int(m.group(1))))
    if "origin" not in keys or "brand" not in keys:
        raise ValidationError("header row lacks 産地 and 品種銘柄: %r" % labels)
    months.sort(key=lambda cm: (len(cm[0]), cm[0]))     # spreadsheet order
    got = [m for _c, m in months]
    if got != [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]:
        raise ValidationError(
            "month columns are %s, not September through August" % got)
    return keys, months


def _brand_and_region(brand_ja, region_ja):
    """Fold the two layouts into one (brand, region) identity.

    Until the 2017 crop year the region was its own column; from 2019 it is
    written inside the brand name as コシヒカリ（魚沼）. Same series.
    """
    inline = re.match(r"^(.*?)（(.*?)）$", brand_ja)
    if inline and inline.group(2) in REGIONS:
        brand_ja, region_ja = inline.group(1), inline.group(2)
    return brand_ja.strip(), (region_ja or "").strip()


# Hepburn romanisation of kana, for a brand name written entirely in hiragana
# or katakana (ひなたまる, にじのきらめき). Kana spell their own sound, so the
# slug is fixed by the name itself — the same every time, whoever runs it —
# and a brand MAFF adds in kana no longer stops the ingest for want of a hand
# entry (2026-11-05: あいちのこころ and ひなたまる did, for a week). A name with
# any kanji still needs a checked entry in BRANDS: kanji have readings, not
# spellings. BRANDS always wins: an entry there keeps its published slug
# (ミルキークイーン is milkyqueen, not the kana's mirukiikuiin), and an entry
# added for a kana name after it has been published must keep this slug and
# may only improve the label (word breaks, capitals).
_KANA = {
    u"あ": "a", u"い": "i", u"う": "u", u"え": "e", u"お": "o",
    u"か": "ka", u"き": "ki", u"く": "ku", u"け": "ke", u"こ": "ko",
    u"さ": "sa", u"し": "shi", u"す": "su", u"せ": "se", u"そ": "so",
    u"た": "ta", u"ち": "chi", u"つ": "tsu", u"て": "te", u"と": "to",
    u"な": "na", u"に": "ni", u"ぬ": "nu", u"ね": "ne", u"の": "no",
    u"は": "ha", u"ひ": "hi", u"ふ": "fu", u"へ": "he", u"ほ": "ho",
    u"ま": "ma", u"み": "mi", u"む": "mu", u"め": "me", u"も": "mo",
    u"や": "ya", u"ゆ": "yu", u"よ": "yo",
    u"ら": "ra", u"り": "ri", u"る": "ru", u"れ": "re", u"ろ": "ro",
    u"わ": "wa", u"ゐ": "i", u"ゑ": "e", u"を": "o", u"ん": "n",
    u"が": "ga", u"ぎ": "gi", u"ぐ": "gu", u"げ": "ge", u"ご": "go",
    u"ざ": "za", u"じ": "ji", u"ず": "zu", u"ぜ": "ze", u"ぞ": "zo",
    u"だ": "da", u"ぢ": "ji", u"づ": "zu", u"で": "de", u"ど": "do",
    u"ば": "ba", u"び": "bi", u"ぶ": "bu", u"べ": "be", u"ぼ": "bo",
    u"ぱ": "pa", u"ぴ": "pi", u"ぷ": "pu", u"ぺ": "pe", u"ぽ": "po",
    u"ぁ": "a", u"ぃ": "i", u"ぅ": "u", u"ぇ": "e", u"ぉ": "o", u"ゔ": "vu",
}
_SMALL_Y = {u"ゃ": "ya", u"ゅ": "yu", u"ょ": "yo"}
_YOON_STEM = {"ki": "k", "gi": "g", "ni": "n", "hi": "h", "bi": "b", "pi": "p",
              "mi": "m", "ri": "r", "shi": "sh", "ji": "j", "chi": "ch"}


def kana_romaji(text):
    u"""Hepburn romanisation of an all-kana name, or None if any character is
    not kana (kanji, a bracket, a Latin letter). Katakana are read as hiragana;
    っ doubles the next consonant, ー lengthens the last vowel, ・ and spaces
    are dropped."""
    out, sokuon = [], False
    chars = [chr(ord(c) - 0x60) if u"ァ" <= c <= u"ヶ" else c for c in text or ""]
    for i, c in enumerate(chars):
        if c in (u"・", u" ", u"　"):
            continue
        if c == u"っ":
            sokuon = True
            continue
        if c == u"ー":
            if not out or not out[-1] or out[-1][-1] not in "aiueo":
                return None
            out.append(out[-1][-1])
            continue
        if c in _SMALL_Y:
            if not out or out[-1] not in _YOON_STEM:
                return None
            stem = _YOON_STEM[out.pop()]
            syll = _SMALL_Y[c]
            out.append(stem + (syll[1:] if stem in ("sh", "j", "ch") else syll))
            continue
        romaji = _KANA.get(c)
        if romaji is None:
            return None
        if sokuon:
            romaji = ("t" if romaji.startswith("ch") else romaji[0]) + romaji
            sokuon = False
        out.append(romaji)
    if sokuon or not out:
        return None
    return "".join(out)


def brand_slug_label(brand_ja):
    u"""(slug, English label) for a 産地品種銘柄: the checked entry in BRANDS, or
    the kana romanisation; a ValidationError for a kanji name nobody has checked."""
    if brand_ja in BRANDS:
        return BRANDS[brand_ja]
    romaji = kana_romaji(brand_ja)
    if romaji:
        return romaji, romaji.capitalize()
    raise ValidationError(
        "unknown 産地品種銘柄 %r. MAFF publishes no code for a brand, so a "
        "series code cannot be assigned without a checked romanisation: "
        "add it to BRANDS in maff_rice_price.py. Nothing is published "
        "until then and the previous release stays live." % brand_ja)


def series_code(pref_code, brand_ja, region_ja):
    slug = brand_slug_label(brand_ja)[0]
    if region_ja:
        if region_ja not in REGIONS:
            raise ValidationError("unknown 地域区分 %r; add it to REGIONS" % region_ja)
        slug += "." + REGIONS[region_ja][0]
    return "price.%s.%s" % (pref_code, slug)


def _number(text):
    """A published price, or None. Missing is missing; a dash is not zero."""
    text = (text or "").strip().replace(",", "").replace("　", "")
    if text in ("", "-", "－", "ー", "…", "‐", "―", "*", "**"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse(raw):
    """The archived envelope -> (series, observations), the ingest contract."""
    envelope = json.loads(raw.decode("utf-8"))["crop_years"]
    meta, values, from_crop = {}, {}, {}

    for crop_year_text in sorted(envelope, key=int):
        crop_year = int(crop_year_text)
        grid = _read_workbook(base64.b64decode(envelope[crop_year_text]["b64"]))
        header = _header_row(grid)
        keys, months = _columns(grid, header)

        for row in sorted(grid):
            if row <= header:
                continue
            cells = grid[row]
            origin = re.sub(r"[\s\u3000]+", "",
                            xlsx.cell_text(cells.get(keys["origin"])))
            if not origin or origin.startswith("注"):
                continue
            brand_ja = xlsx.cell_text(cells.get(keys["brand"])).strip()
            region_ja = (xlsx.cell_text(cells.get(keys["region"])).strip()
                         if "region" in keys else "")

            if "平均" in origin and not brand_ja:
                code = ALL_BRANDS_CODE
                entry = {"code": code,
                         "name_en": "All brands — average contract price",
                         "name_ja": ALL_BRANDS_JA, "unit": "jpy_per_60kg",
                         "weight_per_10000": None, "sort_order": 0}
            else:
                if not brand_ja:
                    continue
                if origin not in _PREF_JIS:
                    raise ValidationError(
                        "unknown 産地 %r in the %d crop-year table"
                        % (origin, crop_year))
                pref_code, pref_en = _PREF_JIS[origin]
                brand_ja, region_ja = _brand_and_region(brand_ja, region_ja)
                code = series_code(pref_code, brand_ja, region_ja)
                label = brand_slug_label(brand_ja)[1]
                if region_ja:
                    label += " (%s)" % REGIONS[region_ja][1]
                entry = {
                    "code": code,
                    "name_en": "%s — %s" % (pref_en, label),
                    "name_ja": origin + " " + brand_ja
                               + ("（%s）" % region_ja if region_ja else ""),
                    "unit": "jpy_per_60kg",
                    "weight_per_10000": None,
                    "sort_order": int(pref_code) * 1000,
                }
            meta.setdefault(code, entry)

            for col, month in months:
                value = _number(xlsx.cell_text(cells.get(col)))
                if value is None:
                    continue
                year = crop_year if month >= 9 else crop_year + 1
                period = datetime.date(year, month, 1)
                seen = from_crop.get((code, period))
                if seen is not None and seen != crop_year:
                    raise ValidationError(
                        "%s %s appears in both the %d and %d crop-year tables; "
                        "crop years are read as covering one month each"
                        % (code, period, seen, crop_year))
                from_crop[(code, period)] = crop_year
                values.setdefault(code, {})[period] = value

    # Sort by prefecture, then by brand within it, with the national average
    # first. Codes sort brands stably because the slug is fixed per brand.
    for rank, code in enumerate(sorted(meta)):
        meta[code]["sort_order"] += rank

    series = sorted(meta.values(), key=lambda s: (s["sort_order"], s["code"]))
    observations = [{"code": code, "period": period, "value": value}
                    for code in sorted(values)
                    for period, value in sorted(values[code].items())]
    return series, observations


# --- validation -------------------------------------------------------------

# The all-brand average has run from about ¥11,900 (2014 crop) to ¥32,500
# (2026); a single brand-month reaches wider but not by an order of magnitude.
# A figure outside this band is a unit error — a yen-per-kilogram column, or a
# thousand-yen one — not a market move.
PRICE_FLOOR, PRICE_CEILING = 5_000.0, 80_000.0
MIN_SERIES = 100
MIN_MONTHS = 190          # September 2008 to the present, less a little slack


def validate(series, observations):
    if not observations:
        raise ValidationError("no observations parsed")

    codes = set(s["code"] for s in series)
    if ALL_BRANDS_CODE not in codes:
        raise ValidationError(
            "the published all-brand average row (%s) is missing" % ALL_BRANDS_JA)
    if len(codes) < MIN_SERIES:
        raise ValidationError(
            "only %d series; expected at least %d origin-variety brands"
            % (len(codes), MIN_SERIES))

    seen, periods, by_period = set(), set(), {}
    for o in observations:
        key = (o["code"], o["period"])
        if key in seen:
            raise ValidationError("duplicate observation %s %s" % key)
        seen.add(key)
        periods.add(o["period"])
        if not (PRICE_FLOOR <= o["value"] <= PRICE_CEILING):
            raise ValidationError(
                "%s %s: ¥%s per 60kg is outside the ¥%s-¥%s sanity band"
                % (o["code"], o["period"], o["value"], PRICE_FLOOR, PRICE_CEILING))
        by_period.setdefault(o["period"], {})[o["code"]] = o["value"]

    ordered = sorted(periods)
    if len(ordered) < MIN_MONTHS:
        raise ValidationError(
            "only %d months of history; expected at least %d — a crop-year "
            "table stopped being listed?" % (len(ordered), MIN_MONTHS))
    if ordered[0] > datetime.date(FIRST_CROP_YEAR, 9, 1):
        raise ValidationError(
            "history starts %s; the archive should reach %d-09"
            % (ordered[0], FIRST_CROP_YEAR))

    # Consecutive months, no holes: a missing month means a whole crop-year
    # workbook went unread rather than that nobody traded rice that month.
    for previous, current in zip(ordered, ordered[1:]):
        months = ((current.year - previous.year) * 12
                  + current.month - previous.month)
        if months != 1:
            raise ValidationError(
                "no data at all between %s and %s" % (previous, current))

    # The published average must lie inside the spread of the brands it
    # averages. It is weighted by the previous crop year's inspected quantity,
    # so it is never recomputed here — only checked for consistency.
    for period in ordered[-24:]:
        month = by_period[period]
        average = month.get(ALL_BRANDS_CODE)
        brands = [v for code, v in month.items() if code != ALL_BRANDS_CODE]
        if average is None or len(brands) < 10:
            continue
        if not (min(brands) <= average <= max(brands)):
            raise ValidationError(
                "%s: published all-brand average ¥%s lies outside the brand "
                "range ¥%s-¥%s" % (period, average, min(brands), max(brands)))

    latest = ordered[-1]
    return {
        "series": len(codes),
        "observations": len(observations),
        "months": len(ordered),
        "first_period": ordered[0].isoformat(),
        "latest_period": latest.isoformat(),
        "latest_all_brand_average_jpy_60kg": by_period[latest].get(ALL_BRANDS_CODE),
        "brands_priced_in_latest_month": len(by_period[latest]) - 1,
    }


MANIFEST = {
    "id": DATASET["slug"],
    "section": "agriculture",
    "name": {"en": "Rice contract prices — by origin and variety",
             "ja": "米の相対取引価格（産地品種銘柄別）"},
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
        "as_of_supported": True, "history_from": "2008-09",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Contract price, ¥ per 60kg of brown rice",
         "unit": "JPY_per_60kg", "trust": "official"},
        {"id": "yoy", "label": "Change on a year earlier", "unit": "%",
         "trust": "derived",
         "calc": "(value[t] / value[t−12 months] − 1) × 100, from published values."},
        {"id": "mom", "label": "Change on the previous month", "unit": "%",
         "trust": "derived",
         "calc": "(value[t] / value[t−1 month] − 1) × 100, from published values."},
        {"id": "ann3m", "label": "3-month change, annualised", "unit": "%",
         "trust": "derived",
         "calc": ("((value[t] / value[t−3 months]) ^ 4 − 1) × 100, from "
                  "published values.")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/rice.html?dataset=rice-prices-jp",
    "page": "/rice.html",
    "notes": [
        "Prices are the volume-weighted average of contracts actually struck "
        "between shippers handling 5,000 tonnes a year or more and wholesalers "
        "— not a quote and not an auction clear. They include freight, "
        "packaging and consumption tax, and are for first-grade table rice.",
        "A brand-month is left missing when no contract was struck or when the "
        "month's contracted volume was under 100 tonnes, which MAFF suppresses. "
        "The file does not distinguish the two, and neither is zero.",
        "The all-brand average is published by MAFF, weighted by the previous "
        "crop year's inspected quantity by brand. It is stored as released and "
        "never recomputed from the brand series shown alongside it.",
        "A crop year runs September to August. Regional Koshihikari appeared as "
        "a separate 地域区分 column until the 2017 crop year and inside the brand "
        "name from 2019; both are stored as one series, so the history does not "
        "break at the layout change.",
        "Figures are provisional (速報) as published; MAFF issues no revised "
        "series, so a vintage changes only when MAFF restates a month.",
    ],
}
