# -*- coding: utf-8 -*-
"""M2 — production facilities extractor (主要な設備の状況), parser fac-1.

One row per disclosed facility: site name, city-level location, per-asset-class
book values (normalised to yen), land area (normalised to ㎡), employees —
geocoded to municipality centroids for the facilities map. Writes
eq_fac_filings and eq_facilities into the equity DuckDB.

Source is the t1 (full XBRL) honbun HTML, NOT the t5 CSV every other extractor
uses: the t5 CSV flattens text blocks to plain text and destroys the table.
The t5 package is still read — balance-sheet land (jppfs_cor:Land plus
filer-extension LandInTrust elements) lives there and feeds the gate. Both
packages are in the archive for every annual report; no new capture.

Gates (both recompute the filer's own published numbers):
  1. Row gate — where a table has a 合計 column, the asset cells must sum back
     to it (tolerance one rounded unit per summed column). Negative-total rows
     (eliminations print negatives in parentheses, indistinguishable from the
     annotation convention) are recorded, not gated.
  2. Balance-sheet gate — land across the tables must not exceed consolidated
     balance-sheet land (own + trust). "Major facilities" is a subset: ≤, not
     ==. IFRS adopters tag no consolidated JGAAP land → parent_only_bs.

Geocoding is a static lookup, no external API: gazetteer_municipalities.csv
(municipality centroids derived from Geolonia japanese-addresses, CC BY 4.0 —
attribution required wherever the coordinates are displayed). A location that
names no prefecture geocodes only if its municipality name is nationally
unique — 府中市 exists in both Tokyo and Hiroshima, and a coin-flip is not a
coordinate.

Traps inherited from the M1 (see facility_m1/extract.py for the full list):
multi-row facilities merged by anchor-cell origin; 面積 subcolumns are never
yen; sibling-column context decides whether a 土地 column is money or area;
the land cell is a grammar (leased-in area, owned area with inline units, book
value); summary 総括表 trump detail tables per scope and their own 合計 row
trumps re-summing; 信託建物/信託土地 are separate assets; 当社グループ means
the group.

Usage:
    python facility_extract.py                       # local archive
    python facility_extract.py --source s3 --workers 12
    python facility_extract.py --docs S100YC7N       # re-extract a subset

Stop the local API server first (DuckDB counts its reader as a lock):
    lsof -ti:8007 | xargs kill
Python 3.9.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact, DB_PATH,
                     ARCHIVE, incremental_window, record_run,
                     seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)

HERE = os.path.dirname(os.path.abspath(__file__))
GAZETTEER = os.path.join(HERE, "gazetteer_municipalities.csv")
PARSER_VERSION = "fac-6"

FACILITIES_MARK = "MajorFacilitiesTextBlock"
NOT_APPLICABLE_RE = re.compile(u"該当(?:事項)?\\s*(?:は)?\\s*(?:あり)?(?:ません|なし|無し)")

PREFS = (u"北海道", u"青森県", u"岩手県", u"宮城県", u"秋田県", u"山形県", u"福島県",
         u"茨城県", u"栃木県", u"群馬県", u"埼玉県", u"千葉県", u"東京都", u"神奈川県",
         u"新潟県", u"富山県", u"石川県", u"福井県", u"山梨県", u"長野県", u"岐阜県",
         u"静岡県", u"愛知県", u"三重県", u"滋賀県", u"京都府", u"大阪府", u"兵庫県",
         u"奈良県", u"和歌山県", u"鳥取県", u"島根県", u"岡山県", u"広島県", u"山口県",
         u"徳島県", u"香川県", u"愛媛県", u"高知県", u"福岡県", u"佐賀県", u"長崎県",
         u"熊本県", u"大分県", u"宮崎県", u"鹿児島県", u"沖縄県")
PREF_RE = re.compile(u"(%s)" % u"|".join(PREFS))

SUBTOTAL_RE = re.compile(u"^(小計|合計|計|総合計|消去\\s*又は\\s*全社|セグメント間消去|"
                         u"消去|全社|調整額|セグメント間取引消去|合\\s*計)$")


def norm(s):
    s = (s or "").replace("&#160;", " ").replace("&nbsp;", " ")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    s = unicodedata.normalize("NFKC", s).replace(u" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_tags(s):
    return norm(re.sub(r"<[^>]+>", " ", s))


ANNOT_RE = re.compile(r"<[^>]*>")  # a decoded &lt;36&gt; is an annotation, not data
NOTE_MARK_RE = re.compile(u"[（(]注[）)]\\s*\\d*|※\\s*\\d*")


def to_num(s):
    """Strip trailing annotations rather than reject the cell: Ajinomoto writes
    2,451 (6) and rejecting it silently drops the asset from the row sum."""
    s = ANNOT_RE.sub(" ", norm(s))
    s = NOTE_MARK_RE.sub(" ", s)   # 78,971 (注)3 — the footnote digit sits
    while True:                    # OUTSIDE the parens
        s2 = re.sub(u"\\s*[（(][^（()）]*[）)]\\s*$", "", s).strip()
        if s2 == s:
            break
        s = s2
    s = s.replace(",", "")
    s = re.sub(u"[%％株円㎡]+$", "", s).strip()
    if not s or s in ("-", u"−", u"―", u"ー", u"—", u"△", "*"):
        return None
    if re.fullmatch(u"[△-]?\\d+(?:\\.\\d+)?", s):
        return float(s.replace(u"△", "-"))
    return None


# ---------------------------------------------------------------- table grid

def grid_of(table_html):
    """Expand rowspan/colspan into a grid, remembering where each cell
    ORIGINATES — origin separates a new facility from the continuation row
    that carries only its land area."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I)
    grid, origin, taken, max_c = {}, set(), set(), 0
    for r, row in enumerate(rows):
        c = 0
        for m in re.finditer(r"<t([dh])\b([^>]*)>(.*?)</t\1>", row, re.S | re.I):
            attrs, content = m.group(2), m.group(3)
            while (r, c) in taken:
                c += 1
            rs = re.search(r'rowspan="?(\d+)', attrs)
            cs = re.search(r'colspan="?(\d+)', attrs)
            rs = int(rs.group(1)) if rs else 1
            cs = int(cs.group(1)) if cs else 1
            text = strip_tags(content)
            origin.add((r, c))
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = text
                    taken.add((r + dr, c + dc))
            c += cs
            max_c = max(max_c, c)
    cells = [[grid.get((r, c), "") for c in range(max_c)] for r in range(len(rows))]
    return cells, origin


# ------------------------------------------------------------- column typing

# Tested in order. segment before name: セグメントの名称 contains 名称 and must
# not steal the name slot. cip before buildings (建設仮勘定 contains 建).
COLUMN_TYPES = [
    ("location", (u"所在地",)),
    ("segment", (u"セグメント",)),
    ("name", (u"事業所名", u"事業所", u"店名", u"店舗名", u"設備等の名称", u"名称",
              u"会社名", u"物件名")),
    ("contents", (u"設備の内容", u"設備内容", u"設備の 内容")),
    ("kubun", (u"区分",)),
    ("cip", (u"建設仮勘定",)),
    ("buildings", (u"建物",)),
    ("structures", (u"構築物",)),
    ("machinery", (u"機械",)),
    ("vessels", (u"船舶",)),
    ("aircraft", (u"航空機",)),
    ("vehicles", (u"車両", u"運搬具")),
    ("land", (u"土地",)),
    ("lease", (u"リース", u"使用権")),
    ("tools", (u"工具", u"器具", u"備品")),
    ("software", (u"ソフト",)),
    ("movables", (u"動産",)),
    ("investment_property", (u"投資不動産",)),
    ("intangibles", (u"無形固定資産",)),
    ("deposits", (u"差入保証金", u"敷金及び保証金", u"敷金")),
    # minor classes some filers put inside their 合計 — folded into other
    ("other_assets", (u"一括償却資産",)),
    ("other_assets", (u"長期貸付金",)),
    ("other_assets", (u"その他",)),
    ("total", (u"合計", u"計")),
    ("employees", (u"従業員",)),
]

BASE_ASSETS = ("buildings", "structures", "machinery", "vessels", "aircraft",
               "vehicles", "land", "lease", "tools", "software", "movables",
               "investment_property", "intangibles", "deposits", "cip",
               "other_assets")
ASSET_KEYS = BASE_ASSETS + tuple(k + "2" for k in BASE_ASSETS)  # 信託建物 etc.

LABEL_COLS = ("name", "location", "segment", "contents", "total", "employees", "kubun")


def classify_column(label):
    """Filers letter-space their headers — 建設 仮勘定, 従業 員数, 長 期 貸付金 —
    so every match runs against the label with spaces stripped."""
    label = label.replace(" ", "")
    for key, kws in COLUMN_TYPES:
        if any(k in label for k in kws):
            return key
    return None


def header_depth(grid):
    """The first row with ≥2 numeric cells, or a prefecture in any cell, is
    data (overseas tables have no prefecture; the numeric test carries them)."""
    for r, row in enumerate(grid):
        nums = sum(1 for c in row if to_num(c) is not None)
        if nums >= 2 or any(PREF_RE.search(c) for c in row):
            return r
    return min(len(grid), 1)


def scope_of(heading):
    t = norm(heading)
    if u"及び連結子会社" in t or u"連結会社" in t or u"当社グループ" in t:
        return "group"
    if u"提出会社" in t or u"当社" in t:
        return "parent"
    if u"在外" in t:
        return "overseas_sub"
    if u"国内" in t or u"子会社" in t or u"連結" in t:
        return "domestic_sub"
    return t[:40] if t else ""


BOOK_UNITS = [(u"百万円", 1e6), (u"千円", 1e3), (u"億円", 1e8)]
# Tokyo Gas denominates its US-subsidiary table in 百万米ドル. No FX rate is
# invented: the table parses, its money stays missing (never 0), areas and
# employees are kept, and the unit gate does not fail over it.
FOREIGN_UNIT_RE = re.compile(u"(百万|千)?\\s*(米ドル|ドル|ユーロ|元|ウォン|ポンド)")
AREA_UNITS = [(u"千㎡", 1e3), (u"千m2", 1e3), (u"㎡", 1.0), (u"m2", 1.0)]


def unit_of(text, table):
    for label, mult in table:
        if label in text:
            return label, mult
    return None, None


PAREN_NUM_RE = re.compile(u"[（(]\\s*([\\d,]+(?:\\.\\d+)?)\\s*[）)]")
BRACKET_NUM_RE = re.compile(u"[〔\\[]\\s*([\\d,]+(?:\\.\\d+)?)\\s*[〕\\]]")
LAND_TOKEN_RE = re.compile(u"([（(])?\\s*([\\d,]+(?:\\.\\d+)?)\\s*(千?m2)?\\s*([）)])?")


def parse_land_cell(cell):
    # JR West files the land column of a segment as three physical rows —
    # 559,183 / 千m2 / (89,997) — which merge to "559,183 千m2 (89,997)":
    # the stray unit belongs to the parenthesised area, so move it inside.
    cell = re.sub(u"(千?(?:m2|㎡))\\s*[（(]\\s*([\\d,\\.]+)\\s*[）)]",
                  u"(\\2\\1)", cell or "")
    """The land cell is a grammar, not a number. Observed forms:
      2,322 (326,785) <36>            — book, area in parens (Toyota Boshoku)
      (13,035m2) 5,036,909m2 18,498   — leased-in area, owned area with inline
                                        ㎡ unit, then book (Kobe Steel)
      777 (231)                        — book, area in the column's 千㎡ unit
    A ㎡-suffixed number is area (parenthesised → leased-in); a bare
    parenthesised number is area; the first bare plain number is book. 〔 〕 is
    leased-in area. An inline unit means the value is already ㎡ — the caller
    must not apply the column unit again."""
    raw = ANNOT_RE.sub(" ", norm(cell)).replace(u"平方米", " ")
    raw = NOTE_MARK_RE.sub(" ", raw)
    m = BRACKET_NUM_RE.search(raw)
    leased = float(m.group(1).replace(",", "")) if m else None
    raw = BRACKET_NUM_RE.sub(" ", raw)
    book = area = None
    inline_unit = False
    for m in LAND_TOKEN_RE.finditer(raw):
        if not m.group(2):
            continue
        val = float(m.group(2).replace(",", ""))
        paren = bool(m.group(1) or m.group(4))
        unit = m.group(3)
        if unit:
            val *= 1e3 if u"千" in unit else 1.0
            if paren:
                leased = (leased or 0) + val
            else:
                area, inline_unit = val, True
        elif paren:
            if area is None:
                area = val
        elif book is None:
            book = val
    return book, area, leased, inline_unit


def parse_area_cell(cell):
    """A dedicated area column reads with the land grammar shifted one slot:
    the bare number is OWNED area — Seino writes 平方米 86,512 (11,999), owned
    with leased-in in parens — and a paren-only cell is owned area too."""
    b, a, l, _ = parse_land_cell(cell)
    owned = b if b is not None else a
    leased = (l or 0) + (a if (b is not None and a is not None) else 0)
    return owned, (leased or None)


# ---------------------------------------------------------------- table read

def is_pure_area(leaf):
    """A leaf header that is nothing but 面積 + a unit — 面積(m2), 総面積,
    賃貸面積(千m2) — names no asset and is never a book value."""
    return u"面積" in leaf and re.sub(u"[面積千m2㎡()（）:：総賃貸延床べ建物 ]", "", leaf) == ""


def map_columns(grid, depth):
    """Classify columns by joined header labels; the LEAF label decides what is
    an area column. Land gets a two-pass resolution because the same words
    mean opposite things by layout: Sojitz files 土地 面積(千m2) NEXT TO
    土地/帳簿価額 (the first is areas), while a single 土地(面積m2) column is
    book value with area in parentheses. With no explicit 価額/金額 column,
    non-pure-area candidates are combined book columns — a second one is a
    distinct asset (信託土地) and becomes land2."""
    ncols = len(grid[0]) if grid else 0
    cols, land_cands = {}, []
    land_area_col, land_area_mult = None, None
    for c in range(ncols):
        parts = [grid[r][c] for r in range(depth) if grid[r][c]]
        parts = list(dict.fromkeys(parts))
        label = " ".join(parts).replace(" ", "")
        leaf = (parts[-1] if parts else "").replace(" ", "")
        if u"土地" in label:
            land_cands.append((c, label, leaf))
            continue
        if is_pure_area(leaf):
            continue  # 建物/面積, 賃貸面積 … — never yen
        if re.fullmatch(u"千?(?:m2|㎡)", leaf):
            continue  # a bare-unit leaf under a money parent is the area
                      # half of a split 土地 pair, never a value column
        if u"面積" in label and u"価額" not in label and u"金額" not in label:
            continue  # 建物面積(m2)/築10年以内 (Sumitomo Realty) — an area
                      # group whose leaves name age brackets, never yen
        key = classify_column(label)
        if key is None and u"帳簿価額" in label and u"取得" not in label:
            # an asset class we have no keyword for (Ukai files 美術骨董品 —
            # art and antiques) still sums into the filer's 合計: fold it
            # into other rather than dropping it from the row gate
            key = "other_assets"
        if key:
            if key not in cols:
                cols[key] = c
            elif key == "name" and "name2" not in cols:
                cols["name2"] = c    # 会社名 + 店舗名: both halves of the name
            elif key + "2" not in cols and key not in LABEL_COLS:
                c1 = cols[key]
                if all(grid[r][c] == grid[r][c1]
                       for r in range(depth, len(grid))):
                    continue  # a colspan'd value expands into two identical
                              # grid columns — one asset, not two
                cols[key + "2"] = c  # 信託建物 next to 建物 — a distinct asset
    if len(land_cands) == 1:
        c, label, leaf = land_cands[0]
        body = [grid[r][c] for r in range(depth, len(grid))]
        if (u"面積" in label and u"価額" not in label and u"金額" not in label
                and not any(PAREN_NUM_RE.search(x) for x in body if x)):
            # 土地面積(m2) standing alone (Sumitomo Realty) is an area
            # column; a combined 土地(面積m2) book column always carries its
            # area in parentheses in the body
            land_area_col = c
            _, land_area_mult = unit_of(label, AREA_UNITS)
        else:
            cols["land"] = c
            _, land_area_mult = unit_of(label, AREA_UNITS)
    elif land_cands:
        # 価額/金額 in the LEAF makes a money column outright. In the full
        # label it counts only when the leaf differentiates this column from
        # its siblings — Keihan heads the money column 帳簿価額/土地 next to
        # 土地面積(m2) (distinct leaves: full label decides), while Seino's
        # two columns are both 帳簿価額/土地 (identical leaves: the data must
        # decide, below). Area-ish leaves never qualify.
        leaves = [t[2] for t in land_cands]

        def _booky(t):
            leaf = t[2]
            if is_pure_area(leaf) or re.search(u"面積|m2|㎡", leaf):
                return False
            if u"価額" in leaf or u"金額" in leaf:
                return True
            return ((u"価額" in t[1] or u"金額" in t[1])
                    and leaves.count(leaf) == 1)

        book_cands = [t for t in land_cands if _booky(t)]
        if book_cands:
            others = [t for t in land_cands if t not in book_cands]
        else:
            nonpure = [t for t in land_cands if not is_pure_area(t[2])]
            others = [t for t in land_cands if is_pure_area(t[2])]
            if (len(nonpure) == 2 and not others
                    and not any(u"信託" in t[1] or u"投資" in t[1] for t in nonpure[1:])):
                c1, c2 = nonpure[0][0], nonpure[1][0]
                n2 = any(to_num(grid[r][c2]) is not None
                         for r in range(depth, len(grid)))
                if not n2:
                    # colspan spill (Mitsubishi Estate 土地(面積) split into
                    # value + fragment): the second column holds no numbers —
                    # the first is the book column, the fragment rejoins it
                    book_cands = [nonpure[0]]
                    cols["_land_spill"] = c2
                else:
                    # Seino: two columns both headed 土地, sub-labels nowhere
                    # in the header — the data carries 平方米 in the first.
                    # Every observed split layout files area before book.
                    others, book_cands = [nonpure[0]], [nonpure[1]]
            else:
                book_cands = nonpure
        if book_cands:
            cols["land"] = book_cands[0][0]
            _, land_area_mult = unit_of(book_cands[0][1], AREA_UNITS)
        if len(book_cands) > 1:
            cols["land2"] = book_cands[1][0]
        for c, label, leaf in others:
            if land_area_col is None:
                land_area_col = c
                _, land_area_mult = unit_of(leaf + " " + label, AREA_UNITS)
    if "land2" in cols:
        # Colspan spill: 土地(面積) splits into a value column and a fragment
        # column holding only the tail of the parenthesised area — m2) —
        # (Mitsubishi Estate). A second land column with no numeric body cell
        # is that tail, not 信託土地; its text rejoins the land cell.
        c2 = cols["land2"]
        if not any(to_num(grid[r][c2]) is not None
                   for r in range(depth, len(grid))):
            cols["_land_spill"] = cols.pop("land2")
    return cols, land_area_col, land_area_mult


def logical_rows(grid, origin, depth, anchor_col, kubun_col=None, value_cols=(),
                 total_col=None, no_sum_cols=()):
    """Merge physical continuation rows into logical facilities: a facility
    starts where the anchor cell originates AND some value cell originates
    with it; other rows contribute their originating cells into the same
    record (Toyota Boshoku's area rows). The second condition exists because
    Kyoritsu Maintenance files the dormitory name and its address as two
    physical rows, both originating in the anchor column with every value
    rowspan-carried — anchor-origin alone duplicates every facility.

    Where a 合計 column exists, a facility ALSO needs its own originating
    total cell: Mitsui Fudosan lists three buildings on one shared parcel
    with land and 合計 rowspanned across them, and counting each row as a
    facility triples the shared land. The rowspan group is one facility;
    per-building bare numbers in the same value column sum.

    With a 区分 column (NEC), a facility is TWO parallel physical rows — a
    帳簿価額 row and a 面積 row — and blind merging concatenates their digits.
    The book row's cells win; the area row contributes only the land column,
    wrapped in parens so the land grammar reads it as area."""
    out = []
    nmerged = []
    anchor_cols = anchor_col if isinstance(anchor_col, (list, tuple)) else [anchor_col]
    if kubun_col is not None:
        anchor_cols = list(anchor_cols) + [kubun_col]
    for r in range(depth, len(grid)):
        # A new facility needs an originating value cell with a BARE number:
        # Kyoritsu's address row originates in the anchor column and in the
        # land column, but the land cell holds only the parenthesised area.
        # A row whose originating value cells are all EMPTY also starts —
        # Murakami Kaimeido opens each plant with a lease-bracket row whose
        # money arrives on the next physical row.
        anchored = any((r, c) in origin for c in anchor_cols)
        if kubun_col is not None and u"面積" in grid[r][kubun_col]:
            anchored = False        # an area half-row never starts a facility
        val_origins = [c for c in value_cols if (r, c) in origin and grid[r][c]]
        starts = anchored and (
            not value_cols
            or any(to_num(grid[r][c]) is not None for c in val_origins)
            or not val_origins) and (
            total_col is None or (r, total_col) in origin)
        if starts or not out:
            out.append(list(grid[r]))
            nmerged.append(1)
        elif kubun_col is not None and u"面積" in grid[r][kubun_col]:
            out[-1].append(("__area_row__", list(grid[r])))
        else:
            nmerged[-1] += 1
            merged = out[-1]
            for c in range(len(grid[r])):
                if (r, c) in origin and grid[r][c]:
                    a, b = to_num(merged[c]), to_num(grid[r][c])
                    if (c in value_cols and c not in no_sum_cols
                            and a is not None and b is not None):
                        # rowspan-grouped facility: per-row values are parts
                        # of one record, never digits to concatenate. Land
                        # cells are exempt — their second number is an area
                        # for the land grammar, never money to add.
                        v = a + b
                        merged[c] = "%.0f" % v if v == int(v) else repr(v)
                    else:
                        merged[c] = (merged[c] + " " + grid[r][c]).strip()
    return list(zip(out, nmerged))


def parse_table(table_html, scope):
    grid, origin = grid_of(table_html)
    if not grid or not grid[0]:
        return None
    depth = header_depth(grid)
    if depth == 0:
        return None
    cols, land_area_col, land_area_mult = map_columns(grid, depth)
    header_text = " ".join(" ".join(r) for r in grid[:depth])
    body_text = " ".join(grid[depth][c] for c in range(len(grid[depth]))) if depth < len(grid) else ""
    # Only tables pricing assets belong here — the same block carries track
    # sections, rolling stock and leased-line tables (JR East). NEC puts
    # 帳簿価額(百万円) in a 区分 DATA cell, so the body counts as evidence too.
    if (u"帳簿価額" not in header_text and u"帳簿価額" not in body_text) \
            or not any(k in cols for k in ASSET_KEYS):
        return None
    book_unit, book_mult = unit_of(header_text + " " + body_text, BOOK_UNITS)
    currency = "JPY"
    if book_mult is None:
        m_fx = FOREIGN_UNIT_RE.search(header_text)
        if m_fx:
            # figures as filed, in the filed currency — stored in that
            # currency's own units, displayed with its own symbol, and never
            # summed into a yen total
            book_unit, currency = m_fx.group(0), m_fx.group(2)
            book_mult = {u"百万": 1e6, u"千": 1e3}.get(m_fx.group(1), 1.0)
    if u"面積" in header_text and land_area_mult is None:
        m = re.search(u"面積[^）)]{0,6}", header_text)
        _, land_area_mult = unit_of(m.group(0) if m else "", AREA_UNITS)
    anchor = [cols[k] for k in ("name", "name2", "location", "segment", "contents")
              if k in cols] or [0]
    is_summary = "name" not in cols and "location" not in cols
    value_cols = tuple(cols[k] for k in cols
                       if k in ASSET_KEYS or k in ("total", "employees"))
    label_end = min(value_cols) if value_cols else len(grid[0])
    rows, subtotal_rows = [], []
    no_sum = tuple(cols[k] for k in ("land", "land2") if k in cols)
    for cells, nmerge in logical_rows(grid, origin, depth, anchor, cols.get("kubun"),
                                      value_cols, cols.get("total"), no_sum):
        area_cells = None
        if cells and isinstance(cells[-1], tuple):
            area_cells = cells.pop()[1]
        rec = {}
        for key in ("name", "location", "segment", "contents"):
            if key in cols:
                rec[key] = cells[cols[key]] or None
        if "name2" in cols:
            halves = [cells[cols[k]] for k in ("name", "name2") if k in cols]
            halves = [h for h in halves if h and norm(h) not in (u"―", "-", u"−", u"—")]
            rec["name"] = " ".join(dict.fromkeys(halves)) or rec.get("name")
        for key in ASSET_KEYS:
            if key not in cols:
                continue
            if key in ("land", "land2"):
                cell_text = cells[cols[key]]
                if key == "land" and "_land_spill" in cols:
                    cell_text = (cell_text + " " +
                                 cells[cols["_land_spill"]]).strip()
                book, area, leased, inline = parse_land_cell(cell_text)
                if key == "land" and area is None:
                    if land_area_col is not None:
                        area, more_leased = parse_area_cell(cells[land_area_col])
                        leased = (leased or 0) + (more_leased or 0) or None
                    elif area_cells is not None:
                        area, _ = parse_area_cell(area_cells[cols[key]])
                rec[key], rec[key + "_area"], rec[key + "_leased"] = book, area, leased
                rec["area_inline_unit"] = inline
            else:
                rec[key] = to_num(cells[cols[key]])
        if "total" in cols:
            rec["total"] = to_num(cells[cols["total"]])
        if "employees" in cols:
            rec["employees"] = to_num(
                re.split(u"[（(〔\\[<]", norm(cells[cols["employees"]]))[0])
        rec["nmerge"] = nmerge
        has_numbers = any(rec.get(k) is not None for k in ASSET_KEYS + ("total",))
        if not has_numbers and not rec.get("location"):
            continue
        if any(SUBTOTAL_RE.match(norm(c)) for c in cells[:label_end] if c) \
                or SUBTOTAL_RE.match(norm(rec.get("name") or "")):
            subtotal_rows.append(rec)
        else:
            rows.append(rec)
    return {"scope": scope, "book_unit": book_unit, "book_mult": book_mult,
            "currency": currency,
            "area_mult": land_area_mult or 1.0, "is_summary": is_summary,
            "rows": rows, "subtotal_rows": subtotal_rows}


TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.S | re.I)
SCOPE_RE = re.compile(u"(当社及び連結子会社|提出会社及び連結子会社|当社グループ|"
                      u"連結会社|提出会社|国内子会社|在外子会社|当社|連結子会社)")


def parse_block(blob_html):
    """Scope headings — (1)提出会社 (2)国内子会社 … — live BETWEEN tables, so
    the scope of a table is the trailing text of the gap before it."""
    tables, pos, scope = [], 0, ""
    for m in TABLE_RE.finditer(blob_html):
        found = SCOPE_RE.findall(strip_tags(blob_html[pos:m.start()]))
        if found:
            scope = found[-1]
        t = parse_table(m.group(0), scope_of(scope))
        if t and (t["rows"] or t["subtotal_rows"]):
            tables.append(t)
        pos = m.end()
    return tables


# --------------------------------------------------------------------- gates

def gate_rows(tables):
    ok = bad = unverified = 0
    examples = []
    for t in tables:
        for rec in t["rows"] + t["subtotal_rows"]:
            stated = rec.get("total")
            parts = [rec.get(k) for k in ASSET_KEYS if rec.get(k) is not None]
            if stated is None or stated < 0 or not parts:
                unverified += 1
                continue
            # each summed cell is independently rounded: a facility merged
            # from k physical rows can accumulate k roundings per column
            if abs(sum(parts) - stated) <= max(1.0, len(parts)) * max(1, rec.get("nmerge", 1)):
                ok += 1
            else:
                bad += 1
                if len(examples) < 3:
                    examples.append("%s: parts %.0f vs 合計 %.0f" % (
                        (rec.get("name") or rec.get("location") or "?")[:25],
                        sum(parts), stated))
    return ok, bad, unverified, examples


def stated_total_row(t):
    """A 総括表 often ends 計 → 消去又は全社 (negative) → 合計. The filer's own
    合計 row IS the total; re-summing segment rows overstates by the
    elimination."""
    for rec in reversed(t["subtotal_rows"]):
        label = norm(rec.get("name") or rec.get("segment") or rec.get("location") or "")
        if re.match(u"^(合\\s*計|総合計)$", label) and rec.get("land") is not None:
            return rec
    return None


def countable_tables(tables):
    """Per scope, a summary trumps its detail tables; a group-scope (or
    scope-less) summary covers every scope."""
    group = [t for t in tables if t["is_summary"] and t["scope"] in ("group", "")]
    if group:
        return group
    by_scope = {}
    for t in tables:
        by_scope.setdefault(t["scope"], []).append(t)
    keep = []
    for st in by_scope.values():
        summaries = [t for t in st if t["is_summary"]]
        keep.extend(summaries if summaries else st)
    return keep


def land_totals(tables):
    """Sum land across countable tables. Pagination trap (Sumitomo Realty):
    one building list split over N physical tables with a single 合計 row at
    the end of the last — a stated total that reproduces the run of preceding
    tables (within 1%) replaces the run instead of adding to it."""
    segs = []          # (book_yen, area_m2) contributions in table order
    for t in countable_tables(tables):
        bm = t["book_mult"] if t.get("currency", "JPY") == "JPY" else 0
        bm = bm or 0

        def _vals(rs):
            b = a = 0.0
            for r in rs:
                b += ((r.get("land") or 0) + (r.get("land2") or 0)) * bm
                am = 1.0 if r.get("area_inline_unit") else (t["area_mult"] or 1.0)
                a += ((r.get("land_area") or 0) + (r.get("land2_area") or 0)) * am
            return b, a

        raw_b, raw_a = _vals(t["rows"])
        stated = stated_total_row(t)
        if stated:
            st_b, st_a = _vals([stated])
            cum_b = sum(b for b, _ in segs) + raw_b
            if segs and abs(cum_b - st_b) <= max(cum_b * 0.01, 3 * bm):
                cum_a = sum(a for _, a in segs) + raw_a
                segs = [(st_b, st_a or cum_a)]
            else:
                segs.append((st_b, st_a))
        else:
            segs.append((raw_b, raw_a))
    return sum(b for b, _ in segs), sum(a for _, a in segs)


def gate_bs_land(tables, bs_land_yen, consolidated):
    if bs_land_yen is None:
        return "no_bs_land", None
    if not consolidated:
        return "parent_only_bs", None
    if any(not t["book_mult"] and t.get("currency", "JPY") == "JPY" for t in tables):
        return "no_unit", None  # a YEN table with no unit; filed currencies are fine
    tot, _ = land_totals(tables)
    if tot == 0:
        return "no_land_rows", None
    if tot <= bs_land_yen * 1.02:
        return "clean", None
    return "exceeds", "facilities %.1fbn > BS %.1fbn" % (tot / 1e9, bs_land_yen / 1e9)


# ----------------------------------------------------------------- geocoding

def _kn(s):
    return s.replace(u"\u30f6", u"\u30b1")   # ヶ -> ケ


class Gazetteer(object):
    """所在地 text -> municipality centroid, static lookup only.

    With a prefecture in the string, match the longest municipality name of
    that prefecture contained in it. Without one, match nationwide but only if
    the matched name is unique to one municipality — 府中市 is in both Tokyo
    and Hiroshima and stays un-geocoded rather than coin-flipped."""

    def __init__(self, path=GAZETTEER):
        rows = [r for r in csv.reader(
            io.open(path, encoding="utf-8")) if r and not r[0].startswith("#")]
        self.by_pref = defaultdict(list)
        self.by_name = defaultdict(list)
        for code, pref, city, lat, lng in rows[1:]:
            e = (code, pref, city, float(lat), float(lng))
            self.by_pref[pref].append(e)
            self.by_name[city].append(e)
        for pref in self.by_pref:
            self.by_pref[pref].sort(key=lambda e: -len(e[2]))
        self.national = sorted((e for es in self.by_name.values() for e in es),
                               key=lambda e: -len(e[2]))

    def geocode(self, location):
        # 袖ケ浦市 and 袖ヶ浦市 are the same place: filings and the gazetteer
        # disagree freely on the small ke, so both sides match through _kn().
        t = _kn(norm(location or "").replace(" ", ""))
        if not t:
            return None
        m = PREF_RE.search(t)
        if m:
            for e in self.by_pref[m.group(1)]:
                if _kn(e[2]) in t:
                    return e + ("pref_city",)
            # prefecture named but no municipality — the prefecture is still a
            # place; use the pref's first row? No: that invents precision.
            return None
        for e in self.national:
            if len(e[2]) >= 3 and _kn(e[2]) in t:
                if len(set(x[0] for x in self.by_name[e[2]])) == 1:
                    return e + ("city_unique",)
                return None
        return None


# ------------------------------------------------------------------- schema

SCHEMA_SQL = """
    -- One row per annual report examined for 主要な設備の状況. sha256_t1 is
    -- the hash of the t1 bytes actually parsed (the facilities source);
    -- sha256_t5 hashes the CSV package that supplied balance-sheet land.
    CREATE TABLE IF NOT EXISTS eq_fac_filings (
        doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
        filer_name VARCHAR, period_end DATE, filed_date DATE,
        sha256_t1 VARCHAR, sha256_t5 VARCHAR, parser_version VARCHAR,
        status VARCHAR, detail VARCHAR,
        n_tables INTEGER, n_rows INTEGER, n_land_rows INTEGER,
        n_area_rows INTEGER, n_geocoded INTEGER,
        row_gate_ok INTEGER, row_gate_bad INTEGER, row_gate_unverified INTEGER,
        bs_land_status VARCHAR, bs_land_yen BIGINT,
        fac_land_book_yen BIGINT, fac_land_area_m2 BIGINT);
    -- One row per facility (or per summary-table segment row: is_summary).
    -- Book values normalised to YEN, areas to ㎡. in_totals marks the rows a
    -- per-filing land total may sum without double counting (summary tables
    -- supersede their detail tables — the detail rows stay for the map).
    CREATE TABLE IF NOT EXISTS eq_facilities (
        doc_id VARCHAR, table_no INTEGER, row_no INTEGER,
        scope VARCHAR, is_summary BOOLEAN, in_totals BOOLEAN,
        name VARCHAR, location VARCHAR, segment VARCHAR, contents VARCHAR,
        buildings_yen BIGINT, structures_yen BIGINT, machinery_yen BIGINT,
        vehicles_yen BIGINT, vessels_yen BIGINT, aircraft_yen BIGINT,
        land_yen BIGINT, trust_land_yen BIGINT, lease_yen BIGINT,
        tools_yen BIGINT, software_yen BIGINT, intangibles_yen BIGINT,
        deposits_yen BIGINT, cip_yen BIGINT,
        other_yen BIGINT, total_yen BIGINT, employees INTEGER,
        land_area_m2 BIGINT, land_area_leased_m2 BIGINT,
        muni_code VARCHAR, muni_pref VARCHAR, muni_name VARCHAR,
        lat DOUBLE, lng DOUBLE, geocode_method VARCHAR,
        -- 'JPY' or the filed currency label (米ドル …); the *_yen columns
        -- then hold FILED-CURRENCY units and must never join a yen aggregate
        currency VARCHAR);
"""

FACT_COLS = [("buildings", "buildings_yen"), ("structures", "structures_yen"),
             ("machinery", "machinery_yen"), ("vehicles", "vehicles_yen"),
             ("vessels", "vessels_yen"), ("aircraft", "aircraft_yen"),
             ("land", "land_yen"), ("land2", "trust_land_yen"),
             ("lease", "lease_yen"), ("tools", "tools_yen"),
             ("software", "software_yen"), ("intangibles", "intangibles_yen"),
             ("deposits", "deposits_yen"), ("cip", "cip_yen"),
             ("other_assets", "other_yen"), ("total", "total_yen")]


def yen(v, mult):
    """No unit means no yen figure — a zero here would print a foreign-currency
    asset as worthless."""
    return None if v is None or not mult else int(round(v * mult))


# ------------------------------------------------------------------ per-file

def read_t1(src, doc_id, date):
    if src.name == "local":
        with open(os.path.join(ARCHIVE, "docs", date, doc_id + "_t1.zip"), "rb") as f:
            return f.read()
    key = "docs/%s/%s_t1.zip" % (date, doc_id)
    return src.c.get_object(Bucket=src.bucket, Key=key)["Body"].read()


def facilities_blocks(t1_blob):
    blocks = []
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        for n in z.namelist():
            if "PublicDoc" not in n or not n.endswith(".htm"):
                continue
            h = z.read(n).decode("utf-8", "replace")
            if FACILITIES_MARK not in h:
                continue
            blocks.extend(re.findall(
                r'<ix:nonNumeric[^>]*name="[^"]*%s"[^>]*>(.*?)</ix:nonNumeric>'
                % FACILITIES_MARK, h, re.S))
    return list(dict.fromkeys(blocks))


def bs_land_from_t5(t5_blob):
    bs = {}
    with zipfile.ZipFile(io.BytesIO(t5_blob)) as z:
        for n in z.namelist():
            if not n.endswith(".csv"):
                continue
            text = z.read(n).decode("utf-16", errors="replace")
            for row in csv.reader(io.StringIO(text), delimiter="\t"):
                if len(row) < 9:
                    continue
                element, context, value = row[0], row[2], row[-1]
                if context in ("CurrentYearInstant",
                               "CurrentYearInstant_NonConsolidatedMember") \
                        and (element == "jppfs_cor:Land" or "LandInTrust" in element):
                    v = to_num(value)
                    if v is not None:
                        bs[context] = bs.get(context, 0.0) + v
    land = bs.get("CurrentYearInstant",
                  bs.get("CurrentYearInstant_NonConsolidatedMember"))
    return land, ("CurrentYearInstant" in bs)


def build_rows(doc_id, tables, gaz):
    counted = set(id(t) for t in countable_tables(tables))
    rows, n_geo = [], 0
    for ti, t in enumerate(tables, 1):
        bm = t["book_mult"]
        stated = stated_total_row(t)
        for ri, rec in enumerate(t["rows"], 1):
            am = 1.0 if rec.get("area_inline_unit") else (t["area_mult"] or 1.0)
            geo = gaz.geocode(rec.get("location") or "") if rec.get("location") else None
            if geo:
                n_geo += 1
            area = rec.get("land_area")
            leased = (rec.get("land_leased") or 0) + (rec.get("land2_leased") or 0)
            row = [doc_id, ti, ri, t["scope"], t["is_summary"],
                   id(t) in counted and stated is None,
                   rec.get("name"), rec.get("location"), rec.get("segment"),
                   rec.get("contents")]
            row += [yen(rec.get(k), bm) for k, _ in FACT_COLS]
            row += [int(rec["employees"]) if rec.get("employees") is not None else None,
                    None if area is None else int(round(
                        (area + (rec.get("land2_area") or 0)) * am)),
                    int(round(leased * am)) if leased else None]
            row += list(geo[:5]) + [geo[5]] if geo else [None] * 6
            row.append(t.get("currency", "JPY"))
            rows.append(row)
        if stated and id(t) in counted:
            am = 1.0 if stated.get("area_inline_unit") else (t["area_mult"] or 1.0)
            area = stated.get("land_area")
            row = [doc_id, ti, len(t["rows"]) + 1, t["scope"], t["is_summary"], True,
                   u"合計", None, stated.get("segment"), None]
            row += [yen(stated.get(k), bm) for k, _ in FACT_COLS]
            row += [None, None if area is None else int(round(area * am)), None]
            row += [None] * 6 + [t.get("currency", "JPY")]
            rows.append(row)
    return rows, n_geo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every archived filer, not just listed")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs — re-extract just these")
    ap.add_argument("--new-only", action="store_true",
                    help="extract only filings archived since the last "
                         "recorded run (plus a lookback); what the nightly "
                         "refresh uses. A DB with no recorded run is built "
                         "in full, so this is always safe to pass.")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--catch-up", type=int, default=CATCH_UP_DAYS,
                    metavar="DAYS",
                    help="on a --new-only run, also read the DAYS deepest archive "
                         "days below this extractor's own floor, so a "
                         "database built forward from a watermark fills in its "
                         "own history a slice at a time. 0 (default) is "
                         "forward only.")
    args = ap.parse_args()

    src = S3Source(args.workers) if args.source == "s3" else LocalSource()
    gaz = Gazetteer()
    codelist = load_codelist()
    listed = {d[u"ＥＤＩＮＥＴコード"] for d in codelist if d[u"上場区分"] == u"上場"}

    # Window first, then discovery: knowing `since` lets the bucket listing
    # seek to it instead of paging five years of keys.
    since, have = (incremental_window(args.db, "facilities", "eq_fac_filings")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, "facilities"))
    if since is not None:
        print("incremental: %d of %d archived filings are new since %s"
              % (len(pending), len(filings), since))
    meta = src.list_metadata(
        days=None if since is None else {r["date"] for r in pending.values()})
    targets = []
    for doc_id, rec in sorted(pending.items()):
        m = meta.get(doc_id) or {}
        if not annual(m, rec):
            continue
        if args.all or m.get("edinetCode") in listed:
            targets.append((doc_id, rec, m))
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.limit:
        targets = targets[:args.limit]
    years = sorted({(m.get("periodEnd") or "?")[:4] for _, _, m in targets})
    print("target filings: %d (source=%s) spanning %s"
          % (len(targets), src.name, "/".join(years)))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)

    def fetch_and_parse(t):
        doc_id, rec, m = t
        sha1 = sha5 = None
        try:
            t1 = read_t1(src, doc_id, rec["date"])
            sha1 = hashlib.sha256(t1).hexdigest()
            blocks = facilities_blocks(t1)
            t5 = src.read_zip(doc_id, rec["date"])
            sha5 = hashlib.sha256(t5).hexdigest()
            bs_land, consolidated = bs_land_from_t5(t5)
            tables = []
            for b in blocks:
                tables.extend(parse_block(b))
            return t, (blocks, tables, bs_land, consolidated), (sha1, sha5), None
        except Exception as e:                                       # noqa: BLE001
            return t, None, (sha1, sha5), ("failed", "%s: %s"
                                           % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    tot_rows = tot_geo = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, (sha1, sha5), err = fut.result()
            done += 1
            if done % 500 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"),
                    m.get("periodEnd") or None, rec["date"],
                    sha1, sha5, PARSER_VERSION]
            con.execute("DELETE FROM eq_fac_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_facilities WHERE doc_id = ?", [doc_id])
            if err:
                stats[err[0]] += 1
                con.execute("INSERT INTO eq_fac_filings VALUES (%s)" % ",".join(["?"] * 23),
                            base + list(err) + [None] * 12)
                continue
            blocks, tables, bs_land, consolidated = parsed
            if not blocks:
                stats["no_text_block"] += 1
                con.execute("INSERT INTO eq_fac_filings VALUES (%s)" % ",".join(["?"] * 23),
                            base + ["no_text_block", None] + [None] * 12)
                continue
            if not tables:
                text = strip_tags(" ".join(blocks))
                st = ("not_applicable"
                      if NOT_APPLICABLE_RE.search(text) and len(text) < 200
                      else "no_table_parsed")
                stats[st] += 1
                con.execute("INSERT INTO eq_fac_filings VALUES (%s)" % ",".join(["?"] * 23),
                            base + [st, None] + [None] * 12)
                continue
            ok, bad, unv, examples = gate_rows(tables)
            bs_status, bs_detail = gate_bs_land(tables, bs_land, consolidated)
            book_yen, area_m2 = land_totals(tables)
            rows, n_geo = build_rows(doc_id, tables, gaz)
            flat = [r for t in tables for r in t["rows"]]
            if bad or bs_status == "exceeds":
                status = "partial"
                detail = "; ".join(([bs_detail] if bs_detail else []) + examples)[:300] or None
            else:
                status, detail = "clean", None
            stats[status] += 1
            con.execute("INSERT INTO eq_fac_filings VALUES (%s)" % ",".join(["?"] * 23),
                        base + [status, detail, len(tables), len(flat),
                                sum(1 for r in flat if r.get("land") not in (None, 0)),
                                sum(1 for r in flat if r.get("land_area")),
                                n_geo, ok, bad, unv, bs_status,
                                None if bs_land is None else int(bs_land),
                                int(round(book_yen)), int(round(area_m2))])
            if rows:
                con.executemany("INSERT INTO eq_facilities VALUES (%s)"
                                % ",".join(["?"] * 36), rows)
                tot_rows += len(rows)
                tot_geo += n_geo
    con.close()
    record_run(args.db, "facilities", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)
    print("filings: %s" % dict(stats))
    print("facility rows: %d, geocoded: %d" % (tot_rows, tot_geo))
    print("wrote", os.path.normpath(args.db))


if __name__ == "__main__":
    sys.exit(main())
