# -*- coding: utf-8 -*-
u"""Adapter: margin balances — JPX 信用取引現在高, weekly since August 2002.

Source: Japan Exchange Group, the two long-run tables on the 過去推移表 page of
信用取引残高等. Every Friday (or the last trading day of the week) the exchange
publishes how much stock is held long on borrowed money and how much is held
short on borrowed stock, across the Tokyo and Nagoya markets.

Why it matters: this is the leverage in the Japanese market, and the only
weekly one published free. A margin-buy balance that keeps climbing into a
rally is the overhang that turns a setback into a liquidation; a short balance
that keeps climbing is the fuel under a squeeze. The ratio between them —
信用倍率 — is the single number a Japanese equity desk quotes about
positioning. Because both sides are reported on the same day in the same units,
they can be compared to each other, which is the whole point.

**Two files, one dataset, and they check each other.** The page publishes the
same balance twice, cut two ways:

  * 信用取引現在高 — by whose account it sits in: 委託 (customers) and 自己
    (the member firm's own book), which sum to 合計.
  * 信用取引現在高（一般信用取引・制度信用取引別） — by which kind of margin
    it is: 一般信用取引 (negotiable, terms set by the broker) and 制度信用取引
    (standardised, six months, eligible for securities-finance lending), which
    also sum to 合計.

Both carry the same 合計, so the two cuts reconcile against each other as well
as within themselves. Those identities are validation gates below, and they are
also what confirms the column mapping is right — these workbooks label their
columns with merged header cells that no parser can read unambiguously.

**Shares and value are different measures.** Every balance is published twice,
as a share count in 千株 and as a value in 百万円, and each is stored in the
unit it was published in and never rescaled. They are never summed or ranked
against one another: the value column is struck at contract prices for the
account balances and at the latest close for the securities-finance ones, so
it is not a mark-to-market of the share column.

**A definition break in July 2013, disclosed on the chart, never smoothed.**
Figures up to the 2013-07-12 application date cover Tokyo, Osaka and Nagoya;
from 2013-07-19 they cover Tokyo and Nagoya only, the Osaka cash equity market
having merged into Tokyo. JPX prints this note on the file and the series is
stored exactly as published either side of it — the level steps, and the step
is real.

**The week is dated by its application date** (申込日基準), the day the margin
transaction was applied for, which is how JPX dates the whole table. It is a
Friday in 1,187 of the 1,225 weeks, and a Thursday or earlier when the market
closed for a holiday; a week with no entry is absent rather than zero.

**JPX changed the page on 25 September 2026.** The two long-run workbooks were
frozen at the 2026-09-18 week and a third, one sheet in .xlsx, carries the
weeks since, cut finer: standardised margin by account (自己, 委託), negotiable
margin (委託 only — the member firms' own book uses standardised margin), and
the 合計. Of the eight series kept here six are in that file as published
(proprietary, negotiable, total — both sides); two are sums of its published
parts, exact to the unit — customers = standardised 委託 + negotiable 委託, and
standardised = 自己 + standardised 委託. JPX printed those two totals itself
until 2026-09-18; from 2026-09-25 they are added here from its own figures
(the identity that the three parts make the 合計 is checked every week), and
the methodology page says so. The old files are read from the page while
JPX leaves them there and from a copy kept under data/raw/ once it does not.

Not ingested here: the per-issue weekly balances (銘柄別信用取引週末残高), which
are published as a separate per-issue table, and the daily balances for the
日々公表銘柄 watchlist. Both are per-company data and belong with the equity
datasets, not in a market aggregate.
"""
import base64
import datetime
import json
import os
import re
import urllib.parse

from . import boj_ts, xls, xlsx


class ValidationError(Exception):
    pass


# The 過去推移表 page is found from the section index by its name: JPX
# renumbers these pages (06.html until 2026-09-25, 05.html after) and the
# number means nothing. PAGE is the last address known, used when the index
# cannot be read or names no such page.
INDEX = "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html"
PAGE = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
HISTORY_PAGE_NAME = u"過去推移表"
DOWNLOAD_URL = INDEX
RAW_SUFFIX = ".json"

_ANCHOR = re.compile(r'href="([^"]+\.xlsx?)"', re.I)
_PAGE_LINK = re.compile(r'<a\s[^>]*href="([^"]+/margin/[^"/]+\.html)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)

# Each workbook is told apart by what it is, never by its file name (an opaque
# CMS id) or its position on the page.
LAYOUT_ACCOUNT, LAYOUT_KIND, LAYOUT_COMBINED = "account", "kind", "combined"
CACHE_DIR_NAME = "jpx-margin-books"
# The combined file's columns, found by their stacked header labels (filled
# across merged cells), never by letter.
COMBINED_PATHS = {
    (u"制度信用取引", u"自己", u"売残高"): "proprietary.sales",
    (u"制度信用取引", u"自己", u"買残高"): "proprietary.purchases",
    (u"制度信用取引", u"委託", u"売残高"): "std-customer.sales",
    (u"制度信用取引", u"委託", u"買残高"): "std-customer.purchases",
    (u"一般信用取引", u"委託", u"売残高"): "negotiable.sales",
    (u"一般信用取引", u"委託", u"買残高"): "negotiable.purchases",
    (u"合計", u"売残高"): "total.sales",
    (u"合計", u"買残高"): "total.purchases",
}
# Sums made here from 2026-09-25, each from published parts (see docstring).
SUMMED_FROM = {
    "customer": ("std-customer", "negotiable"),
    "standardized": ("proprietary", "std-customer"),
}

# The sheet name is the only reliable identity for each workbook: the file
# names are opaque CMS ids that change whenever JPX republishes the page.
SHEET_ACCOUNT = u"信用取引現在高"
SHEET_KIND = u"信用取引現在高（一般信用取引・制度信用取引別）"

EPOCH = datetime.date(1899, 12, 30)     # Excel day zero

# (column pair, code stem, English name). Both workbooks lay their columns out
# as alternating 株数 / 金額 pairs across one row per week; the mapping is
# asserted by the sum gates in validate(), never trusted on its own.
ACCOUNT_COLUMNS = [
    ("B", "C", "customer.sales",      "Customers' account — shares sold short"),
    ("D", "E", "customer.purchases",  "Customers' account — shares bought on margin"),
    ("F", "G", "proprietary.sales",   "Members' own account — shares sold short"),
    ("H", "I", "proprietary.purchases", "Members' own account — shares bought on margin"),
    ("J", "K", "total.sales",         "Total — shares sold short"),
    ("L", "M", "total.purchases",     "Total — shares bought on margin"),
]

KIND_COLUMNS = [
    ("B", "C", "kind.total.sales",     "Total — shares sold short"),
    ("D", "E", "kind.total.purchases", "Total — shares bought on margin"),
    ("F", "G", "negotiable.sales",     "Negotiable margin — shares sold short"),
    ("H", "I", "standardized.sales",   "Standardised margin — shares sold short"),
    ("J", "K", "negotiable.purchases", "Negotiable margin — shares bought on margin"),
    ("L", "M", "standardized.purchases", "Standardised margin — shares bought on margin"),
]

# The 合計 columns of the second workbook restate the first workbook's total.
# They are read only to check that, and are not stored as series of their own.
KIND_CHECK_ONLY = ("kind.total.sales", "kind.total.purchases")

SHARES, VALUE = "shares", "value"
UNIT_SHARES, UNIT_VALUE = "thousand shares", "jpy_million"

# The market coverage changed on this application date; see the module
# docstring. Stored as published either side of it.
MARKET_BREAK = datetime.date(2013, 7, 19)

FIRST_WEEK = datetime.date(2002, 8, 2)

DATASET = {
    "slug": "margin-jp",
    "title": "Margin Balances — Japan",
    "country": "Japan",
    "agency": "Japan Exchange Group",
    "agency_ja": "日本取引所グループ",
    "base": None,
    "frequency": "weekly",
    "description": (
        "Outstanding margin balances on the Tokyo and Nagoya markets, weekly "
        "since August 2002: stock bought on margin and stock sold short, each "
        "as a share count in thousands and a value in ¥ million, split by "
        "whose account it sits in (customers or the member firm's own book) "
        "and by which kind of margin it is (negotiable or standardised). "
        "Dated by application date, as the exchange dates them."
    ),
}

SOURCE = {
    "source_id": "jpx:margin-history",
    "name": "JPX — Outstanding margin transactions, historical (信用取引現在高 過去推移表)",
    "name_ja": "日本取引所グループ 信用取引現在高 過去推移表",
    "url": PAGE,
    "license_note": (
        "Published by Japan Exchange Group for public reference. Figures up to "
        "the 2013-07-12 application date cover the Tokyo, Osaka and Nagoya "
        "markets; from 2013-07-19 they cover Tokyo and Nagoya. Account-balance "
        "values are struck at contract prices, not marked to market."
    ),
}

PRESENTATION = {
    "credit_line": "Source: Japan Exchange Group — Outstanding margin transactions "
                   "(信用取引現在高).",
    # Published weekly, on the second business day after the Friday it covers.
    # Three weeks allows a New Year closure plus a slow republication of the
    # long-run file, which lags the weekly release by a week or two.
    "stale_after_days": 21,
    "kinds": {},
    "unit_label": "¥mn / k shares",
}


# --- fetching ---------------------------------------------------------------

def history_page(index_html, index_url=INDEX, fetch_page=None):
    u"""The 過去推移表 page's address, from the section index.

    First by the words of each link; failing that, by the <title> of each
    linked page in the section (a handful of small pages); failing that, the
    last address known. The page number is never trusted.
    """
    links = []
    for href, text in _PAGE_LINK.findall(index_html or ""):
        url = urllib.parse.urljoin(index_url, href)
        if url not in links:
            links.append(url)
        if HISTORY_PAGE_NAME in _TAG.sub("", text):
            return url
    fetch_page = fetch_page or (lambda u: boj_ts.fetch_bytes(u).decode("utf-8", "replace"))
    for url in links:
        if url == index_url:
            continue
        try:
            m = _TITLE.search(fetch_page(url))
        except Exception:                                    # noqa: BLE001
            continue
        if m and HISTORY_PAGE_NAME in m.group(1):
            return url
    return PAGE


def workbook_urls(html, page_url):
    u"""The workbook links on the 過去推移表 page, absolute, in page order."""
    out = []
    for href in _ANCHOR.findall(html):
        url = urllib.parse.urljoin(page_url, href)
        if url not in out:
            out.append(url)
    if not out:
        raise ValidationError(
            "no workbooks linked from %s — the layout changed" % page_url)
    return out


def _book(raw):
    u"""{sheet name: grid} whichever of .xls / .xlsx the bytes are."""
    return xlsx.sheets(raw) if raw[:2] == b"PK" else xls.sheets(raw)


def _first_token(text):
    u"""The Japanese word of a bilingual header: '自己 Proprietary Account' -> 自己."""
    parts = (text or "").replace(u"\u3000", " ").split()
    return parts[0] if parts else ""


def layout_of(book):
    u"""Which table a workbook is, from its sheet name and its header row."""
    if not book:
        return None
    name = _squash(list(book.keys())[0])
    if name == _squash(SHEET_KIND):
        return LAYOUT_KIND
    if name != _squash(SHEET_ACCOUNT):
        return None
    grid = list(book.values())[0]
    for row in sorted(grid)[:8]:
        if _first_token(xls.cell_text(grid[row].get("A"))) == u"月日":
            head = _first_token(xls.cell_text(grid[row].get("B")))
            return LAYOUT_COMBINED if head == u"制度信用取引" else LAYOUT_ACCOUNT
    return None


def _cache_dir():
    root = os.environ.get("OBSERVATORY_DATA_DIR")
    if not root:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(here, "..", "..", "data")
    return os.path.join(root, "raw", CACHE_DIR_NAME)


def _cache_path(layout):
    return os.path.join(_cache_dir(), layout + ".book")


def _store(layout, raw):
    u"""Keep the newest copy of each table, so a file JPX takes down is not lost."""
    try:
        if not os.path.isdir(_cache_dir()):
            os.makedirs(_cache_dir())
        tmp = _cache_path(layout) + ".part"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, _cache_path(layout))
    except OSError:
        pass


def _stored(layout):
    try:
        with open(_cache_path(layout), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def fetch():
    u"""Every workbook on the 過去推移表 page, verbatim, each labelled with the
    table it is; a frozen table no longer on the page comes from the copy kept."""
    try:
        page = history_page(boj_ts.fetch_bytes(INDEX).decode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        page = PAGE
    html = boj_ts.fetch_bytes(page).decode("utf-8", "replace")
    books, have = [], set()
    for url in workbook_urls(html, page):
        raw = boj_ts.fetch_bytes(url)
        try:
            layout = layout_of(_book(raw))
        except Exception:                                    # noqa: BLE001
            layout = None
        if layout is None:
            continue                 # a workbook that is not one of the three tables
        _store(layout, raw)
        have.add(layout)
        books.append({"url": url, "layout": layout,
                      "b64": base64.b64encode(raw).decode("ascii")})
    if LAYOUT_COMBINED in have:
        # the long-run tables are frozen; if JPX has taken one down, the copy
        # kept the last time it was up is the only one there is
        for layout in (LAYOUT_ACCOUNT, LAYOUT_KIND):
            if layout in have:
                continue
            raw = _stored(layout)
            if raw is None:
                raise ValidationError(
                    "the %s table is no longer on %s and no copy was kept — the "
                    "history before 2026-09-25 cannot be rebuilt" % (layout, page))
            books.append({"url": "cached:%s" % layout, "layout": layout,
                          "b64": base64.b64encode(raw).decode("ascii")})
    missing = {LAYOUT_ACCOUNT, LAYOUT_KIND} - {b["layout"] for b in books}
    if missing:
        raise ValidationError(
            "%s does not carry the %s table(s) — the layout changed"
            % (page, ", ".join(sorted(missing))))
    books.sort(key=lambda b: (b["layout"], b["url"]))
    return json.dumps({"books": books}, sort_keys=True).encode("utf-8")


# --- parsing ----------------------------------------------------------------

def _squash(text):
    return re.sub(r"\s+", "", text or "")


def _number(text):
    u"""A published figure, or None. JPX writes a withheld week as '-'."""
    text = (text or "").strip().replace(",", "").replace(u"　", "")
    if text in ("", "-", u"－", u"ー", u"…", u"―"):
        return None
    text = text.replace(u"▲", "-")
    try:
        return float(text)
    except ValueError:
        return None


def _week(text):
    text = (text or "").strip()
    if not text.isdigit():
        return None
    serial = int(text)
    if serial < 30000 or serial > 80000:
        return None
    return EPOCH + datetime.timedelta(days=serial)


def _grids(books):
    u"""{layout: grid} for every table in the envelope. Envelopes written before
    the layout label existed are told apart the same way fetch() tells them."""
    out = {}
    for book in books:
        try:
            sheets = _book(base64.b64decode(book["b64"]))
        except Exception as exc:                                 # noqa: BLE001
            raise ValidationError("%s is not a readable workbook: %s"
                                  % (book["url"], exc))
        layout = book.get("layout") or layout_of(sheets)
        if layout is None or not sheets:
            continue
        if layout in out:
            raise ValidationError("two workbooks carry the %s table (%s)" % (layout, book["url"]))
        out[layout] = list(sheets.values())[0]
    for layout in (LAYOUT_ACCOUNT, LAYOUT_KIND):
        if layout not in out:
            raise ValidationError(
                "no workbook carries the %s table; found %r — JPX renamed a "
                "table or changed the page" % (layout, [b["url"] for b in books]))
    return out


def _colnum(col):
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def _fill_across(grid, row):
    u"""[(column number, label)] for a header row's labelled cells, in order. A
    merged header holds its label in its first cell only and nothing in the
    cells it spans, so a column's label is the nearest one at or to its left."""
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


def _combined_columns(grid):
    u"""{column: (code, measure)} for the combined table, from its headers."""
    rows = sorted(grid)
    head = None
    for row in rows[:10]:
        if _first_token(xls.cell_text(grid[row].get("A"))) == u"月日":
            head = row
            break
    if head is None:
        raise ValidationError("the combined table has no 月日 header row")
    unit_row = head + 3                      # 株数 / 金額 under three stacked label rows
    labels = [_fill_across(grid, r) for r in (head, head + 1, head + 2)]
    columns = {}
    for col, cell in grid.get(unit_row, {}).items():
        unit = _first_token(xls.cell_text(cell))
        measure = SHARES if unit.startswith(u"株数") else VALUE if unit.startswith(u"金額") else None
        if measure is None:
            continue
        path = tuple(_label_at(lab, col) for lab in labels)
        code = COMBINED_PATHS.get(path) or COMBINED_PATHS.get(path[:2])
        if code:
            columns[col] = (code, measure)
    found = {c for c, _m in columns.values()}
    want = set(COMBINED_PATHS.values())
    if found != want:
        raise ValidationError(
            "the combined table's headers do not name every column: missing %s"
            % sorted(want - found))
    return columns, head


def _read_combined(grid):
    u"""The one-sheet table JPX publishes from 2026-09-25 -> {code: {period: value}}.

    Six codes are the published figures; customer.* and standardized.* are
    added here from published parts (SUMMED_FROM), after the week's three
    parts are checked against its published 合計.
    """
    columns, head = _combined_columns(grid)
    values = {}
    for row in sorted(grid):
        if row <= head:
            continue
        cells = grid[row]
        period = _week(xls.cell_text(cells.get("A")) if "A" in cells else "")
        if period is None:
            continue
        for col, (code, measure) in columns.items():
            number = _number(xls.cell_text(cells.get(col)) if col in cells else "")
            if number is None:
                continue
            values.setdefault("%s.%s" % (code, measure), {})[period] = number
    if not values:
        raise ValidationError("the combined table carried no dated rows")
    for side in ("sales", "purchases"):
        for measure in (SHARES, VALUE):
            total = values.get("total.%s.%s" % (side, measure), {})
            for period, whole in total.items():
                parts = [values.get("%s.%s.%s" % (p, side, measure), {}).get(period)
                         for p in ("proprietary", "std-customer", "negotiable")]
                if any(p is None for p in parts):
                    continue
                if abs(sum(parts) - whole) > SUM_TOLERANCE:
                    raise ValidationError(
                        "%s %s %s: 自己 + 制度委託 + 一般委託 = %s but the published 合計 "
                        "is %s — the combined table's columns moved"
                        % (side, measure, period, sum(parts), whole))
            for whole_code, part_codes in SUMMED_FROM.items():
                for period in total:
                    parts = [values.get("%s.%s.%s" % (p, side, measure), {}).get(period)
                             for p in part_codes]
                    if any(p is None for p in parts):
                        continue
                    values.setdefault("%s.%s.%s" % (whole_code, side, measure), {})[period] = sum(parts)
    for code in [c for c in values if c.startswith("std-customer.")]:
        values.pop(code)                     # a part, not a series kept
    return values


def _read(grid, columns, label_prefix):
    u"""One workbook -> {code: {period: value}}, {code: series meta}."""
    values, meta = {}, {}
    for row in sorted(grid):
        cells = grid[row]
        period = _week(xls.cell_text(cells.get("A")) if "A" in cells else "")
        if period is None:
            continue
        for shares_col, value_col, stem, name in columns:
            for col, kind, unit, suffix in (
                    (shares_col, SHARES, UNIT_SHARES, "shares, thousands"),
                    (value_col, VALUE, UNIT_VALUE, "value, ¥ million")):
                code = "%s.%s" % (stem, kind)
                meta.setdefault(code, {
                    "code": code,
                    "name_en": "%s (%s)" % (name, suffix),
                    "name_ja": None,
                    "unit": unit,
                    "weight_per_10000": None,
                    "sort_order": 0,
                })
                number = _number(xls.cell_text(cells.get(col)) if col in cells else "")
                if number is None:
                    continue                 # a withheld week stays a gap
                previous = values.setdefault(code, {}).get(period)
                if previous is not None and previous != number:
                    raise ValidationError(
                        "%s %s is published twice with different values (%s and %s)"
                        % (code, period, previous, number))
                values[code][period] = number
    if not values:
        raise ValidationError("%s workbook carried no dated rows" % label_prefix)
    return values, meta


def parse(raw):
    envelope = json.loads(raw.decode("utf-8"))
    books = envelope["books"]

    grids = _grids(books)
    account_values, account_meta = _read(grids[LAYOUT_ACCOUNT], ACCOUNT_COLUMNS, "account")
    kind_values, kind_meta = _read(grids[LAYOUT_KIND], KIND_COLUMNS, "kind")
    if LAYOUT_COMBINED in grids:
        # the weeks since the long-run tables were frozen: a week in both
        # eras must agree, as the two old workbooks had to
        for code, weeks in _read_combined(grids[LAYOUT_COMBINED]).items():
            target = account_values if code.split(".")[0] in ("customer", "proprietary", "total") \
                else kind_values
            held = target.setdefault(code, {})
            for period, value in weeks.items():
                previous = held.get(period)
                if previous is not None and abs(previous - value) > SUM_TOLERANCE:
                    raise ValidationError(
                        "%s %s: the long-run table says %s, the combined table %s — "
                        "JPX revised the week; check which is current before storing either"
                        % (code, period, previous, value))
                held.setdefault(period, value)

    # The second workbook's 合計 columns restate the first's. They are checked
    # in validate() and then dropped: one number, one series.
    checks = {}
    for stem in KIND_CHECK_ONLY:
        for kind in (SHARES, VALUE):
            code = "%s.%s" % (stem, kind)
            checks[code] = kind_values.pop(code, {})
            kind_meta.pop(code, None)

    values = dict(account_values)
    values.update(kind_values)
    meta = dict(account_meta)
    meta.update(kind_meta)

    order = [c for _s, _v, c, _n in ACCOUNT_COLUMNS] + \
            [c for _s, _v, c, _n in KIND_COLUMNS if c not in KIND_CHECK_ONLY]
    for i, stem in enumerate(order):
        for j, kind in enumerate((SHARES, VALUE)):
            code = "%s.%s" % (stem, kind)
            if code in meta:
                meta[code]["sort_order"] = i * 2 + j

    series = sorted(meta.values(), key=lambda s: s["sort_order"])
    observations = [{"code": code, "period": period, "value": value}
                    for code in sorted(values)
                    for period, value in sorted(values[code].items())]
    # Carried to validate() through the observations themselves would be
    # impossible, so the cross-file check rides along on the module's own
    # contract: parse returns (series, observations) and validate re-derives
    # the totals from them. The restated 合計 is checked here, where it exists.
    _check_kind_split(kind_values, checks)
    _check_restated_total(values, checks)
    return series, observations


def _check_kind_split(kind_values, checks):
    u"""Within the kind workbook: 一般 + 制度 must equal its own 合計.

    Checked here rather than in validate() because that workbook's 合計 is not
    stored — the account workbook publishes the same total and is the one kept.
    Strict, because both sides come from one file and one rounding.
    """
    for side in ("sales", "purchases"):
        for kind in (SHARES, VALUE):
            whole = checks.get("kind.total.%s.%s" % (side, kind), {})
            for period, value in whole.items():
                parts = [kind_values["%s.%s.%s" % (p, side, kind)].get(period)
                         for p in ("negotiable", "standardized")]
                if any(p is None for p in parts):
                    continue
                if abs(sum(parts) - value) > SUM_TOLERANCE:
                    raise ValidationError(
                        "%s %s %s: negotiable + standardised = %s but that "
                        "workbook's own total is %s"
                        % (side, kind, period, sum(parts), value))


# The two workbooks restate the same 合計, and across 1,224 weeks × 4 measures
# they agree everywhere but four cells, all in 2003-2004 and all under half a
# percent (see KNOWN_DISAGREEMENTS). That is JPX's own inconsistency in two
# tables built from the same returns, not a parsing error, and it is disclosed
# rather than reconciled: both numbers stay as published, and the account cut
# is the one stored. The gate is therefore proportionate — a column mapped to
# the wrong field disagrees by whole multiples, never by a tenth of a percent.
# Both workbooks round independently to whole 千株 / 百万円, so a sum of two
# rounded parts can miss the rounded whole by a unit either way.
SUM_TOLERANCE = 2.0

RESTATEMENT_TOLERANCE_PCT = 0.5
RESTATEMENT_TOLERANCE_ABS = 2.0

KNOWN_DISAGREEMENTS = (
    "2003-06-13 (value, 0.43% on the short side and 0.22% on the long) and "
    "2004-03-12 (0.013% of the short balance in shares)")


def _check_restated_total(values, checks):
    u"""The second workbook's 合計 must equal the first workbook's."""
    for stem in KIND_CHECK_ONLY:
        mirror = stem[len("kind."):]
        for kind in (SHARES, VALUE):
            restated = checks.get("%s.%s" % (stem, kind), {})
            original = values.get("%s.%s" % (mirror, kind), {})
            for period, value in restated.items():
                other = original.get(period)
                if other is None:
                    continue
                allowed = max(RESTATEMENT_TOLERANCE_ABS,
                              abs(other) * RESTATEMENT_TOLERANCE_PCT / 100.0)
                if abs(value - other) > allowed:
                    raise ValidationError(
                        "%s %s %s: the two workbooks disagree on the total "
                        "(%s and %s, %.3f%%) — the column mapping is wrong or "
                        "a file was republished mid-revision"
                        % (mirror, kind, period, value, other,
                           abs(value - other) / abs(other) * 100 if other else 0))


# --- validation -------------------------------------------------------------

# The total margin-buy balance has run between roughly 1.5 and 4.5 million
# 千株 over the published history. A ceiling two orders above that catches a
# unit change without ever firing on a real market.
CEILING = 100_000_000.0

# Weeks are 7 days apart, 6 or 8 when a holiday moves the application date and
# 14 when a week is skipped entirely. Anything longer means the file lost rows.
MAX_WEEK_GAP_DAYS = 21


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
                "%s %s: a balance of %s cannot be negative — a margin balance "
                "is a level, not a flow" % (o["code"], o["period"], o["value"]))
        if o["value"] > CEILING:
            raise ValidationError(
                "%s %s: %s is outside the plausible range; check the published "
                "unit" % (o["code"], o["period"], o["value"]))
        by_code.setdefault(o["code"], {})[o["period"]] = o["value"]
        periods.add(o["period"])

    expected = set()
    for stem in [c for _s, _v, c, _n in ACCOUNT_COLUMNS] + \
                [c for _s, _v, c, _n in KIND_COLUMNS if c not in KIND_CHECK_ONLY]:
        expected.add("%s.%s" % (stem, SHARES))
        expected.add("%s.%s" % (stem, VALUE))
    codes = set(s["code"] for s in series)
    if codes != expected:
        raise ValidationError(
            "series set mismatch: %s" % sorted(codes.symmetric_difference(expected)))

    ordered = sorted(periods)
    if ordered[0] > FIRST_WEEK:
        raise ValidationError(
            "history starts %s; the published table reaches %s"
            % (ordered[0], FIRST_WEEK))
    for previous, current in zip(ordered, ordered[1:]):
        gap = (current - previous).days
        if gap > MAX_WEEK_GAP_DAYS:
            raise ValidationError(
                "%d days with no data at all between %s and %s"
                % (gap, previous, current))

    # The identity that confirms the account workbook's column mapping: the two
    # accounts add to the published total, in both units, every week. The
    # negotiable/standardised split is checked against its own workbook's total
    # in parse(), and the two workbooks against each other there too — those
    # totals are published separately and agree to within half a percent.
    for kind in (SHARES, VALUE):
        for side in ("sales", "purchases"):
            total = by_code["total.%s.%s" % (side, kind)]
            for period, whole in total.items():
                got = [by_code["%s.%s.%s" % (p, side, kind)].get(period)
                       for p in ("customer", "proprietary")]
                if any(g is None for g in got):
                    continue
                if abs(sum(got) - whole) > SUM_TOLERANCE:
                    raise ValidationError(
                        "%s %s %s: customers + members' own = %s but the "
                        "published total is %s"
                        % (side, kind, period, sum(got), whole))

    latest = ordered[-1]
    before = [p for p in ordered if p < MARKET_BREAK]
    return {
        "series": len(codes),
        "observations": len(observations),
        "weeks": len(ordered),
        "first_period": ordered[0].isoformat(),
        "latest_period": latest.isoformat(),
        "weeks_before_market_change": len(before),
        "latest_total_purchases_k_shares": by_code["total.purchases.shares"].get(latest),
        "latest_total_sales_k_shares": by_code["total.sales.shares"].get(latest),
    }


BREAK_NOTE = (
    "Figures up to the 2013-07-12 application date cover the Tokyo, Osaka and "
    "Nagoya markets; from 2013-07-19 they cover Tokyo and Nagoya, the Osaka "
    "cash equity market having merged into Tokyo. The level steps at that "
    "week and is stored exactly as published either side of it — the break is "
    "shown on the chart rather than smoothed away.")

UNIT_NOTE = (
    "Every balance is published twice, as a share count in thousands and as a "
    "value in ¥ million, and each is stored in the unit it was published in. "
    "They are different measures and are never summed or ranked against each "
    "other. The value of an account balance is struck at contract prices, so "
    "it is not a mark-to-market of the share count.")

DATE_NOTE = (
    "A week is dated by its application date (申込日), as the exchange dates "
    "the table — a Friday in all but about forty of the weeks, and earlier "
    "when the market closed for a holiday. A week the exchange did not publish "
    "is absent, never zero.")

CUT_NOTE = (
    "The same balance is published cut two ways: by whose account it sits in "
    "(委託 customers, 自己 the member firm's own book) and by which kind of "
    "margin it is (一般 negotiable, terms set by the broker; 制度 standardised, "
    "six months and eligible for securities-finance lending). Each cut sums to "
    "the same total, which is checked at every ingest.")

RESTATEMENT_NOTE = (
    "The two workbooks publish the same total, and they agree in every week "
    "but two: " + KNOWN_DISAGREEMENTS + ". Both figures are JPX's own; the "
    "account cut is the one stored, and the discrepancy is disclosed here "
    "rather than reconciled away. An ingest fails if any week's two totals "
    "differ by more than half a percent.")

SUMMED_NOTE = (
    "From the 2026-09-25 week JPX publishes one finer table in place of the two "
    "long-run ones and no longer prints the customers' total or the standardised "
    "total. Those two series continue here as exact sums of its published parts "
    "— customers = standardised 委託 + negotiable 委託; standardised = 自己 + "
    "standardised 委託 — after the week's three parts are checked against its "
    "published 合計. Every other series is the published figure. Weeks to "
    "2026-09-18 are the published totals, as before.")

SCOPE_NOTE = (
    "Per-issue margin balances — the weekly table by company and the daily one "
    "for the watchlist issues — are not in this dataset. It is the market "
    "aggregate only.")

MANIFEST = {
    "id": DATASET["slug"],
    "section": "market",
    "name": {"en": "Margin balances — outstanding margin transactions",
             "ja": "信用取引現在高"},
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
        "as_of_supported": True, "history_from": "2002-08-02",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Outstanding balance — value in ¥ million, or the "
                                 "share count in thousands, as published",
         "unit": "JPY_million", "trust": "official"},
        {"id": "shares", "label": "Outstanding balance in thousands of shares",
         "unit": "shares_thousand", "trust": "official"},
        {"id": "ratio", "label": "Margin ratio (信用倍率)", "unit": "x",
         "trust": "derived",
         "calc": ("ratio[t] = total shares bought on margin[t] ÷ total shares "
                  "sold short[t], from published share counts — the 信用倍率 a "
                  "Japanese desk quotes. Above one means more stock is held long "
                  "on borrowed money than is held short on borrowed stock. It is "
                  "the reciprocal of the exchange's own 取組比率, which is "
                  "published per issue and divides the other way round.")},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/margin.html",
    "page": "/margin.html",
    "notes": [CUT_NOTE, UNIT_NOTE, BREAK_NOTE, DATE_NOTE, RESTATEMENT_NOTE,
              SUMMED_NOTE, SCOPE_NOTE],
}
