# -*- coding: utf-8 -*-
u"""Adapter: weekly trading by investor type — JPX 投資部門別売買状況.

Source: Japan Exchange Group. Every Thursday the exchange publishes what each
kind of investor bought and sold in Japanese equities the week before, from the
returns of the trading participants with ¥3bn or more of capital.

Why it matters: "foreigners bought ¥X hundred billion of Japanese equities last
week" is the single most quoted flow number in this market, and this table is
where it comes from. Nothing else says who is on the other side of a move —
whether a rally is foreign money, trust banks rebalancing pension assets,
retail on margin, or companies buying back their own shares.

**JPX keeps only the current month of weekly files.** Five workbooks sit on the
page at a time; older weeks are deleted, the monthly page holds the current year
only, and the annual page holds ten yearly totals. So a weekly history of this
series does not exist to be downloaded — it only exists if somebody keeps the
files as they appear. This adapter therefore CACHES every weekly workbook it
sees under ``data/raw/jpx-investor-weeks/`` (the mounted volume in production)
and rebuilds the full history from that cache at every ingest, the same way
``ust_yields`` keeps the Treasury's closed years. The artifact the ingest
archives is the index of that cache — name and SHA-256 per file — and the
workbooks themselves are the archive.

**Each workbook carries two weeks**, the one it reports and the one before, so
consecutive files overlap by a week. That overlap is not redundancy: it is a
free check that two independently published copies of the same week agree, and
it is a validation gate below.

**The table is self-checking, four levels deep**, and every level is a gate:

    自己 + 委託            = 総計          (proprietary + brokerage = total)
    法人 + 個人 + 海外投資家 + 証券会社 = 委託計
    投資信託 + 事業法人 + その他法人等 + 金融機関 = 法人
    生保損保 + 都銀地銀等 + 信託銀行 + その他金融機関 = 金融機関

**Sales and purchases are stored; net is calculated.** JPX prints a 差引き
balance column, and it is exactly purchases − sales — so the two gross figures
are stored as published and the net carries its formula instead of a badge. The
published ratios (比率) are not stored: their denominator changes by level —
the proprietary and brokerage ratios are shares of 総計, the investor-category
ratios shares of 委託計 — and a column whose meaning changes down the page is
worse than no column.

**Value and volume are different measures.** Every figure is published twice,
in ¥ thousand and in thousands of shares, each stored in its published unit and
never rescaled or ranked against the other.

**JPX changed the file in October 2026.** Until the 2026-09-07 week each week
came as two .xls workbooks (value and volume), four market sheets each, two
weeks a sheet, with every aggregate printed. From the 2026-09-28 week it is
one .xlsx a week, one sheet, all four markets and both units, cut finer —
proprietary and individuals each split into cash and margin trades, foreigners
into institutions and individuals — and the aggregates are no longer printed.
The six new splits are kept as series of their own, as published. The seven
aggregates this dataset has always carried (total, brokerage, proprietary,
institutions, individuals, foreigners, financial institutions) continue as
exact sums of the published parts, by the same four identities the old files
asserted and this adapter checked at every ingest; the methodology page says
so. Each new file is checked on its own terms: in every block the printed
差引 must equal purchases − sales and the printed 合計 sales + purchases.
Weeks 2026-09-14 and 2026-09-21 fell off the site between the two formats
and are a gap, as the design accepts.

**What the survey covers.** Domestic common stocks only — no preferred shares —
including ToSTNeT off-auction trades, from participants with at least ¥3bn of
capital. It is not the whole market, and JPX says so on the file.
"""
import base64
import datetime
import hashlib
import json
import os
import re

from . import boj_ts, xls, xlsx


class ValidationError(Exception):
    pass


PAGE = "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html"
DOWNLOAD_URL = PAGE
BASE = "https://www.jpx.co.jp"
RAW_SUFFIX = ".json"

# stock_val_1_260901.xls / stock_vol_1_260901.xls — value and volume of the
# week beginning in that YYMMDD's month, numbered within it (to September 2026).
_LINK = re.compile(r'href="([^"]*stock_(val|vol)_1_(\d{6})\.xls)"', re.I)
# stock_1_w_20261019_20261023.xlsx — one workbook for the week those dates
# bound, both units, all markets (from October 2026).
_LINK_WEEK = re.compile(r'href="([^"]*stock_1_w_(\d{8})_(\d{8})\.xlsx)"', re.I)
_WEEK_NAME = re.compile(r"^stock_1_w_(\d{8})_(\d{8})\.xlsx$")
_PAIR_NAME = re.compile(r"^stock_(val|vol)_(\d{6})\.xls$")

VALUE, VOLUME = "value", "volume"
UNIT_VALUE, UNIT_VOLUME = "jpy_1000", "thousand shares"

# Sheet name -> (code, English market name). All four are required: a file
# missing one is a file we do not recognise.
MARKETS = [
    ("TSE Prime",      "prime",        "TSE Prime"),
    ("TSE Standard",   "standard",     "TSE Standard"),
    ("TSE Growth",     "growth",       "TSE Growth"),
    ("Tokyo & Nagoya", "two-markets",  "Tokyo and Nagoya markets"),
]

# (Japanese label exactly as column A carries it, code, English name). Matched
# on the squashed label, so the section headings above each block — 委託内訳,
# 法人内訳, 金融機関内訳 — never collide with the category rows inside them.
CATEGORIES = [
    (u"自己計",        "proprietary",       "Proprietary (member firms' own account)"),
    (u"委託計",        "brokerage",         "Brokerage (customer orders)"),
    (u"総計",          "total",             "Total"),
    (u"法人",          "institutions",      "Institutions"),
    (u"個人",          "individuals",       "Individuals"),
    (u"海外投資家",    "foreigners",        "Foreigners"),
    (u"証券会社",      "securities-cos",    "Securities companies"),
    (u"投資信託",      "investment-trusts", "Investment trusts"),
    (u"事業法人",      "business-cos",      "Business corporations"),
    (u"その他法人等",  "other-cos",         "Other corporations"),
    (u"金融機関",      "financials",        "Financial institutions"),
    (u"生保・損保",    "insurers",          "Life and non-life insurers"),
    (u"都銀・地銀等",  "banks",             "City and regional banks"),
    (u"信託銀行",      "trust-banks",       "Trust banks"),
    (u"その他金融機関", "other-financials", "Other financial institutions"),
]
CATEGORY_BY_LABEL = dict((re.sub(r"\s+", "", ja), (code, name))
                         for ja, code, name in CATEGORIES)

# The weekly .xlsx (from October 2026): each four-column block is named by the
# Japanese words of the header rows stacked above it, read down and with
# repeats dropped — ('委託', '海外投資家', '法人') is foreign institutions. Six
# of the fourteen blocks are finer than anything the old files printed and
# become series of their own; the other eight are the leaves the old files
# carried under the same codes.
WEEK_BLOCKS = [
    ((u"自己", u"現金取引"),                       "proprietary-cash",        "Proprietary — cash trades"),
    ((u"自己", u"信用取引"),                       "proprietary-margin",      "Proprietary — margin trades"),
    ((u"委託", u"個人", u"現金取引"),              "individuals-cash",        "Individuals — cash trades"),
    ((u"委託", u"個人", u"信用取引"),              "individuals-margin",      "Individuals — margin trades"),
    ((u"委託", u"海外投資家", u"法人"),            "foreigners-institutions", "Foreigners — institutions"),
    ((u"委託", u"海外投資家", u"個人"),            "foreigners-individuals",  "Foreigners — individuals"),
    ((u"委託", u"証券会社"),                       "securities-cos",          None),
    ((u"委託", u"法人", u"投資信託"),              "investment-trusts",       None),
    ((u"委託", u"法人", u"事業法人"),              "business-cos",            None),
    ((u"委託", u"法人", u"その他法人等"),          "other-cos",               None),
    ((u"委託", u"法人", u"金融機関", u"生保・損保"),     "insurers",         None),
    ((u"委託", u"法人", u"金融機関", u"都銀・地銀等"),   "banks",            None),
    ((u"委託", u"法人", u"金融機関", u"信託銀行"),       "trust-banks",      None),
    ((u"委託", u"法人", u"金融機関", u"その他金融機関"), "other-financials", None),
]
WEEK_PATHS = dict((path, code) for path, code, _n in WEEK_BLOCKS)
SPLIT_CATEGORIES = [(code, name) for _p, code, name in WEEK_BLOCKS if name]
# The aggregates the old files printed and the new one does not, each the sum
# of published parts; in dependency order. The same identities SUM_RULES
# checks on the old files, plus the three splits.
SUMMED_RULES = [
    ("proprietary",  ("proprietary-cash", "proprietary-margin")),
    ("individuals",  ("individuals-cash", "individuals-margin")),
    ("foreigners",   ("foreigners-institutions", "foreigners-individuals")),
    ("financials",   ("insurers", "banks", "trust-banks", "other-financials")),
    ("institutions", ("investment-trusts", "business-cos", "other-cos", "financials")),
    ("brokerage",    ("institutions", "individuals", "foreigners", "securities-cos")),
    ("total",        ("proprietary", "brokerage")),
]
WEEK_MARKETS = {u"東証プライム": "prime", u"東証スタンダード": "standard",
                u"東証グロース": "growth", u"二市場": "two-markets"}
WEEK_MEASURES = {u"株数": "volume", u"金額": "value"}
BALANCE, TURNOVER = "balance", "turnover"          # printed checks, not stored
WEEK_SIDES = {u"売": "sales", u"買": "purchases", u"差引": BALANCE, u"合計": TURNOVER}

SALES, PURCHASES = "sales", "purchases"
SIDE_BY_LABEL = {u"売り": SALES, u"買い": PURCHASES}

# The identities the table asserts about itself, whole -> parts.
SUM_RULES = [
    ("total", ("proprietary", "brokerage")),
    ("brokerage", ("institutions", "individuals", "foreigners", "securities-cos")),
    ("institutions", ("investment-trusts", "business-cos", "other-cos", "financials")),
    ("financials", ("insurers", "banks", "trust-banks", "other-financials")),
]

# Where in the sheet each week sits. The workbook prints the week it reports
# and the week before it, side by side; D and H carry the date ranges, E and I
# the figures.
WEEK_ROW = 11
PRIOR_LABEL_COL, PRIOR_VALUE_COL = "D", "E"
LATEST_LABEL_COL, LATEST_VALUE_COL = "H", "I"
TITLE_ROW = 4                       # 2026年9月第1週 ... ( 8/31 - 9/4 )

_RANGE = re.compile(r"^(\d{1,2})/(\d{1,2})[^\d]+(\d{1,2})/(\d{1,2})$")
_TITLE_YEAR = re.compile(r"(\d{4})年")

CACHE_DIR_NAME = "jpx-investor-weeks"

DATASET = {
    "slug": "investor-flows-jp",
    "title": "Weekly Trading by Investor Type — Japan",
    "country": "Japan",
    "agency": "Japan Exchange Group",
    "agency_ja": "日本取引所グループ",
    "base": None,
    "frequency": "weekly",
    "description": (
        "What each kind of investor bought and sold in Japanese equities each "
        "week — foreigners, individuals, trust banks, investment trusts, "
        "business corporations and the rest — in ¥ thousand and in thousands "
        "of shares, for TSE Prime, Standard and Growth and the Tokyo and "
        "Nagoya markets combined. Sales and purchases as published; the net is "
        "calculated. Weeks are dated by the Monday they begin."
    ),
}

SOURCE = {
    "source_id": "jpx:investor-type-weekly",
    "name": "JPX — Trading by type of investors, weekly (投資部門別売買状況)",
    "name_ja": "日本取引所グループ 投資部門別売買状況（週間）",
    "url": PAGE,
    "license_note": (
        "Published by Japan Exchange Group for public reference. Survey of "
        "trading participants with capital of at least ¥3bn; domestic common "
        "stocks only, ToSTNeT trades included. JPX keeps only the current "
        "month of weekly files on its site, so history here begins when "
        "capture began."
    ),
}

PRESENTATION = {
    "credit_line": "Source: Japan Exchange Group — Trading by type of investors "
                   "(投資部門別売買状況).",
    # Published weekly, on the Thursday after the week it covers. Three weeks
    # allows the New Year closure without crying stale.
    "stale_after_days": 21,
    "kinds": {},
    "unit_label": "¥k / k shares",
}


# --- fetching ---------------------------------------------------------------

def _cache_dir():
    u"""Where the weekly workbooks are kept.

    Under the data directory, because on Railway that is the mounted volume and
    the only disk that survives a redeploy. A cache lost here is history lost:
    JPX cannot supply the deleted weeks again.
    """
    root = os.environ.get("OBSERVATORY_DATA_DIR")
    if not root:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(here, "..", "..", "data")
    return os.path.join(root, "raw", CACHE_DIR_NAME)


def listing(html):
    u"""{file name: url} for every weekly workbook still on the page, in either
    of the two shapes JPX has used. The file name is kept as JPX names it."""
    out = {}
    for href, kind, stamp in _LINK.findall(html):
        url = href if href.startswith("http") else BASE + href
        out["stock_%s_%s.xls" % (kind.lower(), stamp)] = url   # the cache's name since 2026-08
    for href, start, end in _LINK_WEEK.findall(html):
        url = href if href.startswith("http") else BASE + href
        out["stock_1_w_%s_%s.xlsx" % (start, end)] = url
    return out


def _entry(name):
    u"""The index record for a cached file, or None for a file that is not one
    of ours. `sort` orders both shapes by the week they belong to."""
    m = _WEEK_NAME.match(name)
    if m:
        return {"file": name, "start": m.group(1), "end": m.group(2), "sort": m.group(1)}
    m = _PAIR_NAME.match(name)
    if m:
        return {"file": name, "stamp": m.group(2),
                "kind": VALUE if m.group(1) == "val" else VOLUME, "sort": "20" + m.group(2)}
    return None


def _store(name, raw):
    directory = _cache_dir()
    if not os.path.isdir(directory):
        os.makedirs(directory)
    path = os.path.join(directory, name)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:     # written whole then renamed: a crash must
        fh.write(raw)               # never leave half a workbook in the cache
    os.replace(tmp, path)


def cached():
    u"""[(index record, path)] for every workbook held, oldest week first."""
    directory = _cache_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        entry = _entry(name)
        if entry:
            out.append((entry, os.path.join(directory, name)))
    return sorted(out, key=lambda e: (e[0]["sort"], e[0]["file"]))


def fetch():
    u"""Top the cache up from the page, then return an index of everything held.

    The envelope is deterministic — sorted names with the SHA-256 of each
    file's bytes — so the ingest runner's own checksum comparison means a run
    that finds no new week publishes nothing, and a run that finds one
    republishes the whole history with that week added.

    The weeks currently on the page are always re-read: JPX does revise this
    table, and a revision it makes while a file is still up is picked up here.
    A revision to a week that has already fallen off the page cannot reach us,
    and nothing pretends otherwise.
    """
    html = boj_ts.fetch_bytes(PAGE).decode("utf-8", "replace")
    live = listing(html)
    if not live:
        raise ValidationError(
            "no weekly workbooks linked from %s — the page layout changed" % PAGE)
    for name, url in sorted(live.items()):
        _store(name, boj_ts.fetch_bytes(url))

    index = []
    for entry, path in cached():
        with open(path, "rb") as fh:
            record = dict(entry)
            record.pop("sort")
            record["sha256"] = hashlib.sha256(fh.read()).hexdigest()
            index.append(record)
    if not index:
        raise ValidationError("the weekly cache is empty after fetching")
    return json.dumps({"weeks": index}, sort_keys=True).encode("utf-8")


# --- parsing ----------------------------------------------------------------

def _squash(text):
    return re.sub(r"\s+", "", text or "")


def _number(text):
    text = (text or "").strip().replace(",", "").replace(u"　", "")
    if text in ("", "-", u"－", u"ー", u"…", u"―"):
        return None
    text = text.replace(u"▲", "-")
    try:
        return float(text)
    except ValueError:
        return None


def _week_start(range_text, year):
    u"""'08/31～09/04' -> the Monday it begins, in `year`.

    A workbook published in January carries December's week beside it, so a
    range whose month is later than the file's own rolls back a year.
    """
    m = _RANGE.match(_squash(range_text).replace(u"～", "~").replace("-", "~")
                     .replace("~", "~"))
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _sheet_year(grid):
    text = xls.cell_text(grid.get(TITLE_ROW, {}).get("A")) if TITLE_ROW in grid else ""
    m = _TITLE_YEAR.search(text or "")
    return int(m.group(1)) if m else None


def _read_sheet(grid, market_code):
    u"""One market sheet -> {(period, category, side): value}.

    Both weeks the sheet carries are read. Their figures sit in fixed columns;
    the row's category comes from the last recognised Japanese label in column
    A, and its side from 売り / 買い in column B.
    """
    year = _sheet_year(grid)
    if year is None:
        raise ValidationError(
            "%s sheet states no year in row %d" % (market_code, TITLE_ROW))
    header = grid.get(WEEK_ROW, {})
    weeks = []
    for label_col, value_col in ((PRIOR_LABEL_COL, PRIOR_VALUE_COL),
                                 (LATEST_LABEL_COL, LATEST_VALUE_COL)):
        text = xls.cell_text(header.get(label_col)) if label_col in header else ""
        start = _week_start(text, year)
        if start is None:
            raise ValidationError(
                "%s: cell %s%d is %r, not a week range"
                % (market_code, label_col, WEEK_ROW, text))
        weeks.append((start, value_col))
    # The prior week cannot post-date the week the file reports; when it does,
    # the file rolled over a new year and the earlier week belongs to the last.
    if weeks[0][0] > weeks[1][0]:
        weeks[0] = (weeks[0][0].replace(year=year - 1), weeks[0][1])

    out = {}
    category = None
    for row in sorted(grid):
        if row <= WEEK_ROW:
            continue
        cells = grid[row]
        label = _squash(xls.cell_text(cells.get("A")) if "A" in cells else "")
        if label in CATEGORY_BY_LABEL:
            category = CATEGORY_BY_LABEL[label][0]
        side = SIDE_BY_LABEL.get(
            _squash(xls.cell_text(cells.get("B")) if "B" in cells else ""))
        if category is None or side is None:
            continue
        for period, column in weeks:
            value = _number(xls.cell_text(cells.get(column))
                            if column in cells else "")
            if value is None:
                continue
            key = (period, category, side)
            previous = out.get(key)
            if previous is not None and previous != value:
                raise ValidationError(
                    "%s %s %s %s appears twice with different values (%s and %s)"
                    % (market_code, period, category, side, previous, value))
            out[key] = value
    return out


def _book(raw):
    u"""{sheet name: grid} whichever of .xls / .xlsx the bytes are."""
    return xlsx.sheets(raw) if raw[:2] == b"PK" else xls.sheets(raw)


def _first_token(text):
    u"""The Japanese word of a bilingual header: '売 Sales' -> 売."""
    parts = (text or "").replace(u"\u3000", " ").replace(u"\xa0", " ").split()
    return parts[0] if parts else ""


def _colnum(col):
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def _fill_across(grid, row):
    u"""[(column number, label)] for a header row's labelled cells, in order. A
    merged header holds its label in its first cell and nothing in the cells it
    spans, so a column's label is the nearest one at or to its left."""
    out = []
    for col, cell in grid.get(row, {}).items():
        text = _first_token(xls.cell_text(cell))
        if text:
            out.append((_colnum(col), text))
    return sorted(out)


def _label_at(filled, col):
    n, label = _colnum(col), ""
    for colnum, text in filled:
        if colnum > n:
            break
        label = text
    return label


def _week_columns(grid, head):
    u"""{column: (category code, side)} for the weekly .xlsx, from its headers."""
    stacked = [_fill_across(grid, r) for r in range(max(1, head - 4), head)]
    columns = {}
    for col, cell in grid.get(head, {}).items():
        side = WEEK_SIDES.get(_first_token(xls.cell_text(cell)))
        if side is None or col in ("A", "B", "C"):
            continue
        path, last = [], None
        for filled in stacked:
            label = _label_at(filled, col)
            if label and label != last:
                path.append(label)
            last = label or last
        code = WEEK_PATHS.get(tuple(path))
        if code is None:
            raise ValidationError(
                "column %s sits under %r, which is not a block this adapter knows — "
                "JPX changed the table" % (col, tuple(path)))
        columns[col] = (code, side)
    for code in WEEK_PATHS.values():
        for side in (SALES, PURCHASES):
            if (code, side) not in columns.values():
                raise ValidationError("the weekly file has no %s column for %s" % (side, code))
    return columns


def _read_week_book(book, named, filename):
    u"""One weekly .xlsx -> {(period, market, category, side, kind): value}.

    `named` is the first date in the file's name; the week is dated by the
    Monday it begins, as the old files' weeks were (JPX's first new-format
    file was named 20260924_20260925 for the holiday-shortened week of
    2026-09-21). The fourteen published blocks, each checked against its own
    printed 差引 and 合計; then the seven aggregates as sums of those parts
    (SUMMED_RULES).
    """
    start = named - datetime.timedelta(days=named.weekday())
    if len(book) != 1:
        raise ValidationError("%s has %d sheets; the weekly file has one" % (filename, len(book)))
    grid = list(book.values())[0]
    rows = sorted(grid)
    head = None
    for r in rows[:15]:
        if _first_token(xls.cell_text(grid[r].get("A"))) == u"年月週":
            head = r
            break
    if head is None:
        raise ValidationError("%s has no 年月週 header row" % filename)
    columns = _week_columns(grid, head)
    out, market, markets = {}, None, set()
    for r in rows:
        if r <= head:
            continue
        cells = grid[r]
        label = _first_token(xls.cell_text(cells.get("B")) if "B" in cells else "")
        if label:
            market = WEEK_MARKETS.get(label)
            if market is None:
                raise ValidationError("%s row %d: market %r is not one of the four" % (filename, r, label))
        kind = WEEK_MEASURES.get(_first_token(xls.cell_text(cells.get("C")) if "C" in cells else ""))
        if kind is None or market is None:
            continue
        # 2026103 = 2026, October, 3rd week: the file's own word on which
        # month it belongs to, checked against the dates in its name
        stamp = _squash(xls.cell_text(cells.get("A")) if "A" in cells else "")
        if stamp and not (stamp[:4].isdigit() and stamp[4:-1].isdigit()
                          and int(stamp[:4]) == named.year and int(stamp[4:-1]) == named.month):
            raise ValidationError(
                "%s row %d says %r but the file is named for the week of %s" % (filename, r, stamp, named))
        got = {}
        for col, (code, side) in columns.items():
            number = _number(xls.cell_text(cells.get(col)) if col in cells else "")
            if number is not None:
                got[(code, side)] = number
        for code in WEEK_PATHS.values():
            sales, purchases = got.get((code, SALES)), got.get((code, PURCHASES))
            if sales is None or purchases is None:
                raise ValidationError("%s: %s %s %s has no sales or purchases figure"
                                      % (filename, market, kind, code))
            balance, turnover = got.get((code, BALANCE)), got.get((code, TURNOVER))
            if balance is not None and abs((purchases - sales) - balance) > SUM_TOLERANCE:
                raise ValidationError(
                    "%s: %s %s %s: purchases − sales = %s but the printed 差引 is %s — the "
                    "columns are misread" % (filename, market, kind, code, purchases - sales, balance))
            if turnover is not None and abs((purchases + sales) - turnover) > SUM_TOLERANCE:
                raise ValidationError(
                    "%s: %s %s %s: sales + purchases = %s but the printed 合計 is %s"
                    % (filename, market, kind, code, purchases + sales, turnover))
            out[(start, market, code, SALES, kind)] = sales
            out[(start, market, code, PURCHASES, kind)] = purchases
        for whole, parts in SUMMED_RULES:
            for side in (SALES, PURCHASES):
                out[(start, market, whole, side, kind)] = sum(
                    out[(start, market, part, side, kind)] for part in parts)
        markets.add((market, kind))
    want = set((m, k) for _s, m, _n in MARKETS for k in (VALUE, VOLUME))
    if markets != want:
        raise ValidationError("%s carries %s, not every market in both units"
                              % (filename, sorted(want - markets) or sorted(markets - want)))
    return out


def parse(raw):
    index = json.loads(raw.decode("utf-8"))["weeks"]
    directory = _cache_dir()

    values, meta, overlaps = {}, {}, []
    market_names = dict((c, n) for _s, c, n in MARKETS)
    category_names = dict((c, n) for _j, c, n in CATEGORIES)
    category_names.update(dict(SPLIT_CATEGORIES))
    for entry in index:
        path = os.path.join(directory, entry["file"])
        if not os.path.exists(path):
            raise ValidationError(
                "%s is in the index but missing from the cache — the volume "
                "lost a week that JPX has already deleted" % entry["file"])
        with open(path, "rb") as fh:
            book = _book(fh.read())
        points = []                     # (period, market, category, side, kind, value)
        if entry["file"].endswith(".xlsx"):
            start = datetime.datetime.strptime(entry["start"], "%Y%m%d").date()
            for (period, market_code, category, side, kind), value in \
                    _read_week_book(book, start, entry["file"]).items():
                points.append((period, market_code, category, side, kind, value))
        else:
            kind = entry["kind"]
            for sheet_name, market_code, _market_name in MARKETS:
                grid = book.get(sheet_name)
                if not grid:
                    raise ValidationError(
                        "%s has no %r sheet; found %s"
                        % (entry["file"], sheet_name, sorted(book)))
                for (period, category, side), value in \
                        _read_sheet(grid, market_code).items():
                    points.append((period, market_code, category, side, kind, value))
        for period, market_code, category, side, kind, value in points:
            code = "%s.%s.%s.%s" % (market_code, category, side, kind)
            meta.setdefault(code, {
                "code": code,
                "name_en": "%s — %s, %s (%s)" % (
                    market_names[market_code], category_names[category], side,
                    "¥ thousand" if kind == VALUE else "thousand shares"),
                "name_ja": None,
                "unit": UNIT_VALUE if kind == VALUE else UNIT_VOLUME,
                "weight_per_10000": None,
                "sort_order": 0,
            })
            previous = values.setdefault(code, {}).get(period)
            if previous is not None:
                overlaps.append((code, period, previous, value))
            values[code][period] = value

    if not values:
        raise ValidationError("no weeks parsed from the cache")
    _check_overlap(overlaps)

    market_order = dict((c, i) for i, (_s, c, _n) in enumerate(MARKETS))
    category_order = dict((c, i) for i, (_j, c, _n) in enumerate(CATEGORIES))
    for i, (c, _n) in enumerate(SPLIT_CATEGORIES):
        category_order[c] = len(CATEGORIES) + i
    for code, m in meta.items():
        market, category, side, kind = code.split(".")
        m["sort_order"] = (market_order[market] * 1000
                           + category_order[category] * 10
                           + (0 if side == SALES else 1) * 2
                           + (0 if kind == VALUE else 1))

    series = sorted(meta.values(), key=lambda s: s["sort_order"])
    observations = [{"code": code, "period": period, "value": value}
                    for code in sorted(values)
                    for period, value in sorted(values[code].items())]
    return series, observations


# Two workbooks publish the same week — one reports it, the next carries it as
# its prior week. They are independently typed copies of one figure and agree
# exactly; a disagreement means JPX revised the week, which is worth failing
# for rather than silently taking whichever file was read last.
def _check_overlap(overlaps):
    for code, period, first, second in overlaps:
        if first != second:
            raise ValidationError(
                "%s %s: two workbooks publish this week differently (%s and "
                "%s) — JPX revised it, so check which file is current before "
                "storing either" % (code, period, first, second))


# --- validation -------------------------------------------------------------

# Each figure is published to the whole ¥ thousand or thousand shares, so four
# parts summed can miss their published whole by the rounding on each.
SUM_TOLERANCE = 4.0

# A week's turnover on the two markets runs to some tens of trillions of yen,
# which is tens of billions of ¥ thousand. Three orders above the largest week
# ever printed catches a unit change and nothing else.
CEILING = 1e15


def validate(series, observations):
    if not observations:
        raise ValidationError("no observations parsed")

    by_code, periods, seen = {}, set(), set()
    for o in observations:
        key = (o["code"], o["period"])
        if key in seen:
            raise ValidationError("duplicate observation %s %s" % key)
        seen.add(key)
        if o["value"] < 0:
            raise ValidationError(
                "%s %s: %s — sales and purchases are gross figures and cannot "
                "be negative. Only the net can be, and the net is calculated, "
                "not stored." % (o["code"], o["period"], o["value"]))
        if o["value"] > CEILING:
            raise ValidationError(
                "%s %s: %s is outside the plausible range; check the published "
                "unit" % (o["code"], o["period"], o["value"]))
        by_code.setdefault(o["code"], {})[o["period"]] = o["value"]
        periods.add(o["period"])

    expected = set()
    for _s, market, _n in MARKETS:
        for _j, category, _cn in CATEGORIES:
            for side in (SALES, PURCHASES):
                for kind in (VALUE, VOLUME):
                    expected.add("%s.%s.%s.%s" % (market, category, side, kind))
    allowed = set()
    for _s, market, _n in MARKETS:
        for category, _cn in SPLIT_CATEGORIES:
            for side in (SALES, PURCHASES):
                for kind in (VALUE, VOLUME):
                    allowed.add("%s.%s.%s.%s" % (market, category, side, kind))
    codes = set(s["code"] for s in series)
    missing = expected - codes
    if missing:
        raise ValidationError(
            "%d series the table should carry are absent: %s"
            % (len(missing), sorted(missing)[:6]))
    extra = codes - expected - allowed
    if extra:
        raise ValidationError("unexpected series: %s" % sorted(extra)[:6])
    # The six finer splits (from October 2026) must add to the aggregate they
    # split, where both are present.
    for whole, parts in SUMMED_RULES[:3]:
        if not all("%s.%s.%s.%s" % ("prime", p, SALES, VALUE) in codes for p in parts):
            continue
        for _s, market, _n in MARKETS:
            for kind in (VALUE, VOLUME):
                for side in (SALES, PURCHASES):
                    totals = by_code.get("%s.%s.%s.%s" % (market, whole, side, kind), {})
                    for period, value in totals.items():
                        got = [by_code.get("%s.%s.%s.%s" % (market, p, side, kind), {}).get(period)
                               for p in parts]
                        if any(g is None for g in got):
                            continue
                        if abs(sum(got) - value) > SUM_TOLERANCE:
                            raise ValidationError(
                                "%s %s %s %s: %s = %s but %s is %s"
                                % (market, kind, side, period, " + ".join(parts), sum(got), whole, value))

    ordered = sorted(periods)
    for previous, current in zip(ordered, ordered[1:]):
        gap = (current - previous).days
        if gap > 28:
            raise ValidationError(
                "%d days with no week at all between %s and %s — the cache "
                "lost weeks JPX has since deleted" % (gap, previous, current))

    # The four identities the table asserts about itself. These are what prove
    # the category labels were read onto the right rows.
    breaks = 0
    for _s, market, _n in MARKETS:
        for kind in (VALUE, VOLUME):
            for side in (SALES, PURCHASES):
                for whole, parts in SUM_RULES:
                    totals = by_code["%s.%s.%s.%s" % (market, whole, side, kind)]
                    for period, value in totals.items():
                        got = [by_code["%s.%s.%s.%s" % (market, p, side, kind)]
                               .get(period) for p in parts]
                        if any(g is None for g in got):
                            continue
                        if abs(sum(got) - value) > SUM_TOLERANCE:
                            breaks += 1
                            raise ValidationError(
                                "%s %s %s %s: %s = %s but the published %s is "
                                "%s" % (market, kind, side, period,
                                        " + ".join(parts), sum(got), whole,
                                        value))

    latest = ordered[-1]
    net = (by_code["two-markets.foreigners.purchases.value"].get(latest, 0)
           - by_code["two-markets.foreigners.sales.value"].get(latest, 0))
    return {
        "series": len(codes),
        "observations": len(observations),
        "weeks": len(ordered),
        "first_period": ordered[0].isoformat(),
        "latest_period": latest.isoformat(),
        "identity_breaks": breaks,
        "latest_foreign_net_jpy_thousand": net,
    }


COVERAGE_NOTE = (
    "JPX keeps only the current month of weekly files on its site: the monthly "
    "page holds this year, the annual page ten yearly totals, and there is no "
    "weekly back-file anywhere. This history therefore begins when capture "
    "began and grows a week at a time; it cannot be extended backwards.")

NET_NOTE = (
    "Sales and purchases are stored exactly as published. The net — what a "
    "reader means by 'foreigners bought ¥X' — is purchases minus sales, "
    "calculated here, and matches the 差引き column JPX prints. It is the only "
    "figure on this dataset that can be negative.")

RATIO_NOTE = (
    "The published 比率 columns are not stored. Their denominator changes down "
    "the page — the proprietary and brokerage ratios are shares of the grand "
    "total, the investor-category ratios shares of brokerage alone — and one "
    "column meaning two things is worse than no column. Compute a share "
    "against whichever total you mean.")

UNIT_NOTE = (
    "Every figure is published twice, in ¥ thousand and in thousands of "
    "shares, and each is stored in its published unit. They are different "
    "measures and are never summed or ranked against each other.")

SURVEY_NOTE = (
    "The survey covers trading participants with capital of ¥3bn or more, in "
    "domestic common stocks only (no preferred shares), including ToSTNeT "
    "off-auction trades. It is most of the market's turnover but not all of "
    "it, and JPX states so on the file.")

IDENTITY_NOTE = (
    "The table adds up four levels deep — proprietary plus brokerage make the "
    "total; institutions, individuals, foreigners and securities companies "
    "make brokerage; investment trusts, business corporations, other "
    "corporations and financial institutions make institutions; insurers, "
    "banks, trust banks and other financials make financial institutions. "
    "Every one of those identities is checked at every ingest, in both units "
    "and on both sides, and a file that breaks one is not published.")

WEEK_NOTE = (
    "A week is dated by the Monday it begins. To September 2026 each workbook "
    "carried the week it reports plus the week before, so two files published "
    "every week and the two copies had to agree exactly or the ingest stopped; "
    "from October 2026 each workbook carries one week.")

FORMAT_NOTE = (
    "From the week of 2026-09-28 JPX publishes one workbook a week, cut finer "
    "and without the aggregates it used to print. Proprietary and individuals "
    "are each split into cash and margin trades, foreigners into institutions "
    "and individuals; those six splits are kept as series of their own, as "
    "published. Total, brokerage, proprietary, institutions, individuals, "
    "foreigners and financial institutions continue as exact sums of the "
    "published parts, by the identities the old files asserted and this "
    "adapter has always checked. In every block of the new file the printed "
    "差引 must equal purchases − sales and the printed 合計 sales + purchases, or "
    "the ingest stops. The weeks of 2026-09-14 and 2026-09-21 left the site "
    "between the two formats and are a gap.")

MANIFEST = {
    "id": DATASET["slug"],
    "section": "market",
    "name": {"en": "Trading by investor type — weekly",
             "ja": "投資部門別売買状況（週間）"},
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
        "as_of_supported": True,
        "history_from": "2026-08 (week beginning; capture began then)",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Weekly sales or purchases — ¥ thousand, or "
                                 "thousands of shares, as published",
         "unit": "JPY_thousand", "trust": "official"},
        {"id": "volume", "label": "Weekly sales or purchases in thousands of shares",
         "unit": "shares_thousand", "trust": "official"},
        {"id": "net", "label": "Net purchases", "unit": "JPY_thousand",
         "trust": "derived",
         "calc": ("net[investor, t] = purchases[investor, t] − sales[investor, t], "
                  "from published gross figures. Equals the 差引き column JPX "
                  "prints. Negative means net selling.")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/flows.html",
    "page": "/flows.html",
    "notes": [COVERAGE_NOTE, NET_NOTE, IDENTITY_NOTE, UNIT_NOTE, RATIO_NOTE,
              WEEK_NOTE, FORMAT_NOTE, SURVEY_NOTE],
}
