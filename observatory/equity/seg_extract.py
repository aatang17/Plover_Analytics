# -*- coding: utf-8 -*-
"""Segment note extractor — revenue by region, named customers, product segments.

What this reads
---------------
The segment-information note of the annual securities report (有価証券報告書,
【セグメント情報等】), from the same t1 package the facilities extractor opens.
The note is ONE text block in the XBRL instance —
`jpcrp_cor:NotesSegmentInformationEtcConsolidatedFinancialStatementsTextBlock`
for Japan-GAAP filers, `jpigp_cor:NotesSegmentInformationConsolidatedFinancial
StatementsIFRSTextBlock` for IFRS filers — carrying HTML tables. Nothing in it
is tagged as a number: the financials extractor's t5 facts hold zero region- or
segment-dimensioned contexts (measured on Tokyo Electron, 2026-09-04), which is
why this is a table parse and not a filter on facts.

The inline-XBRL .htm splits the same note across `ix:continuation` fragments,
so the .xbrl instance is read instead: it holds the whole block in one element.

What this keeps
---------------
- **Regions** (地域ごとの情報 / 地域別情報): revenue by the filer's own region
  labels, on the filer's own basis (顧客の所在地 for most; 販売仕向け先 for some),
  for the current and prior year; also property, plant and equipment or
  non-current assets by region where published. The filer's label is kept
  verbatim; a small lexicon maps it to a region key for cross-filer use, and an
  unmapped label keeps a NULL key rather than a guess. A sub-note such as
  「北米のうち、米国は242,795百万円」 becomes its own row, marked as a sub-note of
  its parent, and is never added to the regions it sits inside.
- **Customers** (主要な顧客ごとの情報): every customer the filer names with the
  revenue it books from them — the only company-to-company revenue link in the
  public record. From a table where there is one; from prose (IFRS filers often
  write "NVIDIAグループ及びTSMCに対する売上高は…それぞれ243,735百万円、124,922百万円")
  where there is not, and marked as such.
- **Product segments** (報告セグメント): external revenue, segment profit and
  segment assets per reportable segment, for multi-segment filers. Best effort:
  segment tables vary more than any other and a failure here is a note in
  `detail`, never a reason to drop the region rows.

Two table orientations, one parser: Japan-GAAP filers print regions ACROSS
(one header row, one value row, one table per year); IFRS filers print regions
DOWN with one column per year. Both are detected from content — which cells
are region labels and which are numbers — rather than from headings, because
headings vary (地域ごとの情報, 【地域別情報】, 外部顧客への売上高の地域別情報).

Units are whatever the filer states (単位:百万円 for nearly all, 千円 for some)
and every stored value is in yen. "－" is missing and is never stored as zero.
A filer that omits the region table because domestic revenue exceeds 90% says
so, and that reason is stored; the absence is then a fact, not a gap.

Gate
----
The current-year region revenues must sum to the filer's consolidated revenue
in `eq_fin_facts` within 0.5% (rounding to the display unit across a handful of
rows), or to the table's own 合計 where the financials are not yet extracted.
A filing that carries a region table which does not reconcile is `partial` with
the numbers in the reason; one that carries no region table and no stated
omission is `partial` too. Nothing is ever recomputed to make a gate pass.

Usage
-----
  python seg_extract.py --all --source local --workers 8 --db ../data/equity.duckdb
  python seg_extract.py --sec-codes 8035,6857 --dump      # show what was parsed
  python seg_extract.py --all --new-only                   # the nightly form
"""
import argparse
import datetime as _dt
import hashlib
import html
import io
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact, DB_PATH,
                     incremental_window, record_run, seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)
from facility_extract import grid_of, read_t1

PARSER_VERSION = "seg-1"
EXTRACTOR = "segments"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eq_seg_filings (
    doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
    filer_name VARCHAR, period_end DATE, filed_date DATE, sha256_t1 VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR,
    accounting_standard VARCHAR, note_element VARCHAR, single_segment BOOLEAN,
    basis_text VARCHAR, region_rows INTEGER, customer_rows INTEGER,
    product_rows INTEGER, region_omitted_reason VARCHAR, tables INTEGER,
    consolidated_revenue_yen DOUBLE, region_revenue_sum_yen DOUBLE,
    reconciliation VARCHAR);
CREATE TABLE IF NOT EXISTS eq_seg_regions (
    doc_id VARCHAR, year_offset INTEGER, measure VARCHAR, ord INTEGER,
    label_ja VARCHAR, region_key VARCHAR, value_yen DOUBLE,
    share_pct DOUBLE, is_subnote BOOLEAN, parent_label_ja VARCHAR);
CREATE TABLE IF NOT EXISTS eq_seg_customers (
    doc_id VARCHAR, year_offset INTEGER, ord INTEGER, customer_name VARCHAR,
    value_yen DOUBLE, segment_label VARCHAR, source VARCHAR);
CREATE TABLE IF NOT EXISTS eq_seg_products (
    doc_id VARCHAR, year_offset INTEGER, ord INTEGER, segment_label_ja VARCHAR,
    external_revenue_yen DOUBLE, total_revenue_yen DOUBLE,
    segment_profit_yen DOUBLE, segment_assets_yen DOUBLE);
"""
FILINGS_COLS = 22

# The note, by taxonomy. The largest matching block in the instance is the
# note; the smaller siblings (DescriptionOfReportableSegments…, Footnotes…) are
# fragments of it that some filers tag separately.
NOTE_RE = re.compile(
    r"<((?:jpcrp_cor|jpigp_cor):[A-Za-z]*SegmentInformation[A-Za-z]*TextBlock)\b[^>]*>(.*?)</\1>",
    re.S)
SINGLE_SEGMENT_MARK = "DescriptionOfFactThatCompanysBusinessComprisesSingleSegment"
STANDARD_RE = re.compile(r"<jpdei_cor:AccountingStandardsDEI[^>]*>([^<]*)<")
PERIOD_END_RE = re.compile(r"<jpdei_cor:CurrentFiscalYearEndDateDEI[^>]*>([^<]*)<")

# Consolidated revenue, in order of preference, from the financials extractor.
REVENUE_ELEMENTS = [
    "jppfs_cor:NetSales", "jpigp_cor:RevenueIFRS", "jpigp_cor:NetSalesIFRS",
    "jppfs_cor:OperatingRevenue1", "jppfs_cor:RevenueFromContractsWithCustomers",
    "jpcrp_cor:RevenuesFromExternalCustomers",
    "jpigp_cor:RevenueFromExternalCustomersIFRS",
    "jpcrp_cor:NetSalesSummaryOfBusinessResults",
    "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",
    "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",
    "jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults",
]
RECONCILE_TOLERANCE = 0.005

# Region labels -> keys. The filer's label is always kept; the key is what
# lets two filers' "China" be read together. Longer labels first so that
# アジア・太平洋地域 is matched before アジア.
REGION_LEXICON = [
    (u"アジア・太平洋地域", "AP"), (u"アジア太平洋", "AP"), (u"アジア・オセアニア", "AP"),
    (u"アジアパシフィック", "AP"), (u"アジア・パシフィック", "AP"),
    (u"北米・中南米", "AM"), (u"中南米", "LA"), (u"南米", "LA"),
    (u"中華人民共和国", "CN"), (u"中国", "CN"), (u"大中華圏", "CN"), (u"中華圏", "CN"),
    (u"アメリカ合衆国", "US"), (u"アメリカ", "US"), (u"米国", "US"),
    (u"北米", "NA"), (u"米州", "AM"),
    (u"ヨーロッパ", "EU"), (u"欧州", "EU"),
    (u"韓国", "KR"), (u"大韓民国", "KR"), (u"台湾", "TW"), (u"香港", "HK"),
    (u"シンガポール", "SG"), (u"タイ", "TH"), (u"フィリピン", "PH"), (u"ベトナム", "VN"),
    (u"マレーシア", "MY"), (u"インドネシア", "ID"), (u"インド", "IN"),
    (u"ドイツ", "DE"), (u"オランダ", "NL"), (u"英国", "GB"), (u"イギリス", "GB"),
    (u"フランス", "FR"), (u"イタリア", "IT"), (u"スペイン", "ES"),
    (u"オーストラリア", "AU"), (u"オセアニア", "OC"), (u"中東", "ME"), (u"アフリカ", "AF"),
    (u"東南アジア", "SEA"), (u"東アジア", "EA"), (u"アジア", "AS"),
    (u"メキシコ", "MX"), (u"カナダ", "CA"), (u"ブラジル", "BR"), (u"ニュージーランド", "NZ"),
    (u"アイルランド", "IE"), (u"スイス", "CH"), (u"ベルギー", "BE"), (u"ロシア", "RU"),
    (u"トルコ", "TR"), (u"ポーランド", "PL"), (u"チェコ", "CZ"), (u"ハンガリー", "HU"),
    (u"豪州", "AU"), (u"大洋州", "OC"), (u"中近東", "ME"), (u"北アメリカ", "NA"),
    (u"北中米", "NA"), (u"南北アメリカ", "AM"), (u"米国・カナダ", "NA"), (u"欧米", "EUUS"),
    (u"東・東南アジア", "SEA"), (u"東南・南アジア", "SEA"), (u"その他アジア", "AS"),
    (u"アセアン", "SEA"), (u"ASEAN", "SEA"), (u"EMEA", "EMEA"), (u"北米・南米", "AM"),
    (u"アジア地域", "AS"), (u"欧米等", "EUUS"), (u"アジア・パシフィック", "AP"),
    (u"イスラエル", "IL"), (u"サウジアラビア", "SA"), (u"アラブ首長国連邦", "AE"),
    (u"アジア他", "AS"), (u"欧州他", "EU"), (u"米州他", "AM"), (u"日本", "JP"),
    (u"国内", "JP"), (u"本邦", "JP"), (u"海外", "OV"),
    (u"その他の地域", "OT"), (u"その他地域", "OT"), (u"その他", "OT"),
]
TOTAL_LABELS = (u"合計", u"計", u"連結", u"総計")

# Row labels in a product-segment table.
SEG_EXTERNAL = (u"外部顧客への売上高", u"外部顧客に対する売上高", u"外部顧客からの収益",
                u"外部顧客への収益", u"外部顧客に対する収益")
SEG_TOTAL_REV = (u"計", u"売上高計", u"収益計", u"合計")
SEG_PROFIT = (u"セグメント利益", u"セグメント損益", u"セグメント利益又は損失",
              u"セグメント利益（損失）", u"セグメント利益(損失)", u"営業利益")
SEG_ASSETS = (u"セグメント資産",)
SEG_SKIP_COLS = (u"合計", u"計", u"調整額", u"連結財務諸表計上額", u"消去", u"全社",
                 u"調整", u"連結", u"報告セグメント", u"報告セグメント計", u"その他")

CJK = u"　-ヿ一-鿿＀-￯"


# ------------------------------------------------------------------ text

def norm(s):
    s = html.unescape(s or "")
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Rohm prints "日 本" and "中 国": spaces inside a CJK word are typography.
    return re.sub(u"(?<=[%s])\\s+(?=[%s])" % (CJK, CJK), "", s)


def strip_tags(s):
    return norm(re.sub(r"<[^>]+>", " ", s or ""))


NOTE_MARK_RE = re.compile(u"[（(]注[）)]\\s*\\d*|※\\s*\\d*|[（(]\\d+[）)]$")
SHARE_RE = re.compile(u"^[（(]\\s*([\\d.]+)\\s*%\\s*[）)]$")


def to_num(s):
    """A cell as a number, or None. Handles △ negatives, thousands separators,
    trailing note marks and units. Never turns a dash into a zero."""
    s = norm(s)
    s = NOTE_MARK_RE.sub(" ", s).strip()
    s = re.sub(u"(百万円|千円|円|%)$", "", s).strip()
    if not s or s in (u"-", u"−", u"―", u"ー", u"—", u"△", u"*", u"－"):
        return None
    s = s.replace(",", "")
    if re.fullmatch(u"[△▲-]?\\d+(?:\\.\\d+)?", s):
        return float(s.replace(u"△", "-").replace(u"▲", "-"))
    return None


SUBNOTE_PREFIX_RE = re.compile(u"^(?:うち|内、|内|\\(うち|（うち)\\s*")
GEOGRAPHIC_EXCLUDE = ("TOTAL", "OT", "OV")


def is_subnote_label(label):
    """「うち米国」/「内、中国」 as a column of its own: a breakdown of the column
    before it, not a region to be added to the others."""
    return bool(SUBNOTE_PREFIX_RE.match(norm(label)))


METRIC_LABEL_RE = re.compile(
    u"外部顧客|セグメント間|セグメント利益|セグメント損益|セグメント資産|セグメント負債|減価償却|"
    u"増加額|償却額|減損|のれん|持分法|顧客との契約|その他の収益|調整額|内部売上")
TOTAL_LABEL_RE = re.compile(u"(合計|総計|連結計|連結合計|^計$|売上高$|売上収益$|営業収益$|^連結売上|^海外売上)")


def is_metric_label(label):
    """A row of a segment table (外部顧客への売上高, セグメント資産…) that has
    strayed into a region-shaped read. Never a place."""
    return bool(METRIC_LABEL_RE.search(norm(label)))


def region_key(label):
    lab = NOTE_MARK_RE.sub("", norm(label)).strip()
    lab = re.sub(u"[（(].*?[）)]", "", lab).strip()
    lab = SUBNOTE_PREFIX_RE.sub("", lab).strip()
    if not lab:
        return None, None
    if lab in TOTAL_LABELS or TOTAL_LABEL_RE.search(lab):
        return lab, "TOTAL"
    for name, key in REGION_LEXICON:
        if lab == name:
            return lab, key
    for name, key in REGION_LEXICON:
        if lab.startswith(name):
            return lab, key
    return lab, None


def is_region_label(text):
    lab, key = region_key(text)
    return bool(lab) and key is not None


def unit_of(text):
    """Multiplier to yen from a 単位 statement, or None when none is stated."""
    t = norm(text)
    if u"百万円" in t:
        return 1e6
    if u"千円" in t:
        return 1e3
    if u"億円" in t:
        return 1e8
    if re.search(u"単位[:：]\\s*円", t):
        return 1.0
    return None


def year_marker(text):
    """0 for 当連結会計年度 / 当期 wording, 1 for 前…, else None."""
    t = norm(text)
    hits = [(m.start(), 1 if m.group(0).startswith(u"前") else 0)
            for m in re.finditer(u"[前当](?:連結)?(?:会計)?(?:年度|期|事業年度)", t)]
    return hits[-1][1] if hits else None


# --------------------------------------------------------------- sequencing

TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.S | re.I)
SECTION_KEYS = [
    ("customer", (u"主要な顧客",)),
    ("region", (u"地域ごとの情報", u"地域別情報", u"地域別", u"所在地別", u"地域に関する情報",
                u"国又は地域")),
    ("product", (u"製品及びサービスごとの情報", u"製品・サービス")),
    ("segment", (u"報告セグメント", u"セグメント情報")),
]
MEASURE_KEYS = [
    ("ppe", (u"有形固定資産",)),
    ("noncurrent", (u"非流動資産",)),
    ("revenue", (u"売上高", u"売上収益", u"収益", u"営業収益")),
]


def sequence(block_html):
    """[(table_html, ctx)] in document order, where ctx carries the state the
    text before each table established: section, measure, year, unit, and the
    text itself for basis/omission/sub-note sentences."""
    out = []
    pos = 0
    state = {"section": None, "measure": None, "year": None, "unit": None}
    for m in TABLE_RE.finditer(block_html):
        text = strip_tags(block_html[pos:m.start()])
        _advance(state, text)
        cells, origin = grid_of(m.group(0))
        flat = [c for row in cells for c in row if c]
        if len(flat) <= 1:
            # a one-cell table is a caption ("(単位：百万円)") not data
            _advance(state, " ".join(flat))
            pos = m.end()
            continue
        after = strip_tags(block_html[m.end():m.end() + 1200])
        out.append((cells, dict(state, before=text, after=after, origin=origin)))
        pos = m.end()
    tail = strip_tags(block_html[pos:])
    return out, tail


def _advance(state, text):
    if not text:
        return
    y = year_marker(text)
    if y is not None:
        state["year"] = y
    u = unit_of(text)
    if u is not None:
        state["unit"] = u
    # The last section keyword in the chunk wins: a chunk that closes the
    # customer section and opens the region one reads "…主要な顧客… 2.地域ごとの情報".
    best = None
    for sec, keys in SECTION_KEYS:
        for k in keys:
            i = text.rfind(k)
            if i >= 0 and (best is None or i > best[0]):
                best = (i, sec)
    if best:
        state["section"] = best[1]
        state["measure"] = None
    best = None
    for meas, keys in MEASURE_KEYS:
        for k in keys:
            i = text.rfind(k)
            if i >= 0 and (best is None or i > best[0]):
                best = (i, meas)
    if best:
        state["measure"] = best[1]


# ------------------------------------------------------------ table readers

def _numeric_row(row):
    nums = [to_num(c) for c in row]
    return sum(1 for n in nums if n is not None), nums


def read_region_table(cells, ctx, period_end):
    """Rows of (year_offset, measure, label, key, value, share). Handles both
    orientations; returns [] when the table is not a region table."""
    unit = ctx.get("unit") or 1e6
    rows = []
    if not cells or not cells[0]:
        return rows
    for row in cells:
        u = unit_of(" ".join(row))
        if u is not None:
            unit = u
            break
    ncols = max(len(r) for r in cells)
    cells = [r + [""] * (ncols - len(r)) for r in cells]

    # Orientation A — regions ACROSS: a row whose cells are mostly region
    # labels, then (possibly after a second header row carrying 「うち…」
    # breakdowns) a numeric row. Headers can be two rows deep — 北米・南米
    # above 内、ブラジル — and a spanning cell is expanded into every column
    # it covers, so values are read only at the cell's origin.
    origin = ctx.get("origin") or set()
    for r, row in enumerate(cells[:-1]):
        labels = [c for c in row if c]
        if len(labels) < 2:
            continue
        keys = [region_key(c)[1] for c in labels]
        hits = sum(1 for k in keys if k is not None)
        places = sum(1 for k in keys if k is not None and k not in GEOGRAPHIC_EXCLUDE)
        if hits < 2 or hits < 0.6 * len(labels) or places < 1:
            continue
        vr = None
        for cand in range(r + 1, min(r + 4, len(cells))):
            n_orig = sum(1 for c in range(ncols)
                         if (cand, c) in origin and to_num(cells[cand][c]) is not None)
            if n_orig >= 2:
                vr = cand
                break
        if vr is None:
            continue
        year = ctx.get("year")
        measure = ctx.get("measure") or "revenue"
        header_rows = cells[r:vr]
        ord_ = 0
        last_leaf = None
        for c in range(ncols):
            if (vr, c) not in origin:
                continue                       # a colspan duplicate of the cell before
            v = to_num(cells[vr][c])
            if v is None:
                continue
            stack = [norm(h[c]) for h in header_rows if norm(h[c])]
            if not stack:
                continue
            leaf = stack[-1]
            above = next((h for h in stack[:-1] if h != leaf), None)
            lab, key = region_key(leaf)
            if not lab:
                continue
            sub = is_subnote_label(leaf)
            parent = None
            if sub:
                parent = region_key(above)[0] if above else last_leaf
            rows.append([year, measure, ord_, lab, key, v * unit, None, sub, parent])
            if not sub:
                last_leaf = lab
            ord_ += 1
        # a 構成比 row in parentheses under the values (SCREEN)
        if vr + 1 < len(cells):
            for c in range(ncols):
                m = SHARE_RE.match(norm(cells[vr + 1][c]))
                stack = [norm(h[c]) for h in header_rows if norm(h[c])]
                lab = region_key(stack[-1])[0] if stack else None
                if m and lab:
                    for x in rows:
                        if x[3] == lab and x[0] == year and x[1] == measure:
                            x[6] = float(m.group(1))
        return rows

    # Orientation B — regions DOWN: first column mostly region labels, one or
    # two numeric columns headed by years or 前/当 wording.
    first = [r[0] for r in cells]
    first_keys = [region_key(c)[1] for c in first]
    hits = sum(1 for k in first_keys if k is not None)
    places = sum(1 for k in first_keys if k is not None and k not in GEOGRAPHIC_EXCLUDE)
    if hits < 2 or places < 1:
        return rows
    # Column -> year offset from the header rows above the first region row.
    col_year = {}
    header_rows = []
    for r, row in enumerate(cells):
        if is_region_label(row[0]) and _numeric_row(row[1:])[0] >= 1:
            break
        header_rows.append(row)
    for row in header_rows:
        for c, cell in enumerate(row):
            if c == 0:
                continue
            t = norm(cell)
            y = year_marker(t)
            if y is not None:
                col_year[c] = y
            m = re.search(r"(20\d\d)", t)
            if m:
                col_year.setdefault(c, ("Y", int(m.group(1))))
    # Year labels resolve by order: the later year is the current one.
    years = sorted({v[1] for v in col_year.values() if isinstance(v, tuple)})
    for c, v in list(col_year.items()):
        if isinstance(v, tuple):
            col_year[c] = 0 if v[1] == years[-1] else 1
    measure = ctx.get("measure") or "revenue"
    ord_ = 0
    parent = None
    for row in cells:
        head = norm(row[0])
        if not head:
            continue
        n_num, nums = _numeric_row(row[1:])
        if n_num == 0:
            # a label row inside the table switches the measure (Sony puts
            # 売上高 and 非流動資産 blocks in one table)
            for meas, keys in MEASURE_KEYS:
                if any(k in head for k in keys):
                    measure = meas
            continue
        lab, key = region_key(head)
        if not lab or is_metric_label(head):
            continue
        sub = is_subnote_label(head)
        numeric_cols = [c + 1 for c, v in enumerate(nums) if v is not None]
        if not numeric_cols:
            continue
        if len(numeric_cols) == 1:
            year = col_year.get(numeric_cols[0], ctx.get("year"))
            rows.append([year, measure, ord_, lab, key, nums[numeric_cols[0] - 1] * unit,
                         None, sub, parent if sub else None])
        else:
            # without headers, assume left = prior, right = current
            for i, c in enumerate(numeric_cols):
                year = col_year.get(c, 1 if i < len(numeric_cols) - 1 else 0)
                rows.append([year, measure, ord_, lab, key, nums[c - 1] * unit,
                             None, sub, parent if sub else None])
        if not sub:
            parent = lab
        ord_ += 1
    return rows


def read_customer_table(cells, ctx):
    """Rows of (year_offset, name, value, segment) from a 主要な顧客 table."""
    unit = ctx.get("unit") or 1e6
    out = []
    if not cells:
        return out
    # Many filers state the unit inside the column header itself —
    # 「売上高(千円)」 — rather than in a caption above the table. That wins:
    # reading it in 百万円 inflates the customer by a thousand, and the gate
    # below (a customer larger than the filer's whole revenue) is what caught it.
    for row in cells:
        u = unit_of(" ".join(row))
        if u is not None:
            unit = u
            break
    header = None
    for r, row in enumerate(cells):
        joined = " ".join(row)
        if u"顧客" in joined and (u"売上" in joined or u"収益" in joined):
            header = r
            break
    if header is None:
        return out
    hdr = [norm(c) for c in cells[header]]
    name_col = next((i for i, c in enumerate(hdr) if u"顧客" in c or u"名称" in c or u"相手先" in c), 0)
    val_cols = [i for i, c in enumerate(hdr) if u"売上" in c or u"収益" in c or u"金額" in c]
    seg_col = next((i for i, c in enumerate(hdr) if u"セグメント" in c), None)
    # two value columns = prior and current side by side
    for row in cells[header + 1:]:
        name = norm(row[name_col]) if name_col < len(row) else ""
        if not name or to_num(name) is not None:
            continue
        seg = norm(row[seg_col]) if seg_col is not None and seg_col < len(row) else None
        vals = [(c, to_num(row[c])) for c in val_cols if c < len(row)]
        vals = [(c, v) for c, v in vals if v is not None]
        if not vals:
            continue
        if len(vals) == 1:
            out.append([ctx.get("year"), name, vals[0][1] * unit, seg, "table"])
        else:
            for i, (_c, v) in enumerate(vals):
                out.append([1 if i < len(vals) - 1 else 0, name, v * unit, seg, "table"])
    return out


CUSTOMER_PROSE_RE = re.compile(
    u"(?P<names>[^。]{3,200}?)に対する(?:売上高|売上収益|収益)は、?(?P<year>当|前)連結会計年度において"
    u"(?:は)?それぞれ(?P<amts>(?:[\\d,]+百万円[、及びおよび]*)+)")
AMOUNT_RE = re.compile(u"([\\d,]+)百万円")
ONE_CUSTOMER_RE = re.compile(
    u"(?P<name>[^。、]{3,120}?)に対する(?:売上高|売上収益|収益)は、?(?P<year>当|前)連結会計年度において"
    u"(?:は)?(?P<amt>[\\d,]+)百万円")


def read_customer_prose(text):
    """IFRS filers write the customers out. Returns (year, name, value, None, 'prose')."""
    out = []
    for m in CUSTOMER_PROSE_RE.finditer(text):
        names = re.split(u"及び|および|、", m.group("names"))
        names = [n.strip() for n in names if n.strip()]
        amts = [float(a.replace(",", "")) * 1e6 for a in AMOUNT_RE.findall(m.group("amts"))]
        if len(names) == len(amts) and names:
            year = 0 if m.group("year") == u"当" else 1
            for n, a in zip(names, amts):
                out.append([year, n, a, None, "prose"])
    if not out:
        for m in ONE_CUSTOMER_RE.finditer(text):
            year = 0 if m.group("year") == u"当" else 1
            out.append([year, m.group("name").strip(),
                        float(m.group("amt").replace(",", "")) * 1e6, None, "prose"])
    return out


def read_segment_table(cells, ctx):
    """Reportable-segment table -> rows (year, label, external, total, profit, assets)."""
    unit = ctx.get("unit") or 1e6
    if not cells or len(cells) < 3:
        return []
    ncols = max(len(r) for r in cells)
    cells = [r + [""] * (ncols - len(r)) for r in cells]
    # the deepest header row: last row before the first numeric-bearing row
    first_num = next((r for r, row in enumerate(cells) if _numeric_row(row[1:])[0] >= 2), None)
    if first_num is None or first_num == 0:
        return []
    # The segment names are the last header row with two or more labels beyond
    # column 0. A section row such as ['売上高', '', '', …] sits between them
    # and the numbers and must not be mistaken for the header.
    header = None
    for r in range(first_num - 1, -1, -1):
        labels = [norm(c) for c in cells[r][1:] if norm(c)]
        if len(labels) >= 2 and all(to_num(c) is None for c in labels):
            header = [norm(c) for c in cells[r]]
            break
    if header is None:
        return []
    body = cells[first_num:]
    # A unit stated inside the table ("(単位:百万円)" in a corner cell) beats
    # whatever the running text last said.
    for row in cells[:first_num]:
        u = unit_of(" ".join(row))
        if u is not None:
            unit = u
            break
    labels = {}
    for c, h in enumerate(header[1:], start=1):
        if not h or any(h.startswith(s) or h == s for s in SEG_SKIP_COLS):
            continue
        if to_num(h) is not None or year_marker(h) is not None:
            continue
        labels[c] = h
    if len(labels) < 2:
        return []
    found = {}

    def pick(keys, row):
        head = norm(row[0])
        return any(head == k or head.startswith(k) for k in keys)

    for row in body:
        head = norm(row[0])
        if pick(SEG_EXTERNAL, row):
            found.setdefault("external", row)
        elif pick(SEG_PROFIT, row):
            found.setdefault("profit", row)
        elif pick(SEG_ASSETS, row):
            found.setdefault("assets", row)
        elif head in SEG_TOTAL_REV and "external" in found and "total" not in found:
            found["total"] = row
    if "external" not in found and "profit" not in found:
        return []
    out = []
    for ord_, (c, lab) in enumerate(sorted(labels.items())):
        def val(kind):
            row = found.get(kind)
            v = to_num(row[c]) if row is not None and c < len(row) else None
            return None if v is None else v * unit
        out.append([ctx.get("year"), ord_, lab, val("external"), val("total"),
                    val("profit"), val("assets")])
    if all(x[3] is None and x[5] is None for x in out):
        return []
    return out


SUBNOTE_RE = re.compile(
    u"(?P<parent>[^\\s、。()（）]{2,12})のうち、?(?P<child>[^\\s、。()（）]{2,20}?)は\\s*(?P<amt>[\\d,]+)\\s*(?P<unit>百万円|千円)")
# Three ways a filer says "there is nothing to show by region", all of which
# make the absence of a table a fact rather than a parse failure:
#   本邦以外の外部顧客への売上高がないため、該当事項はありません   (no overseas revenue)
#   本邦の外部顧客への売上高が…90%を超えるため、記載を省略           (domestic > 90%)
#   本邦の外部顧客への売上収益が…大部分を占めるため、記載を省略     (IFRS wording)
OMIT_RES = [
    (re.compile(u"(?:本邦以外|海外)(?:の国又は地域)?(?:の外部顧客への|の|)(?:売上高|売上収益|営業収益|収益|売上)が?(?:ない|ありません|存在しない)"),
     "no overseas revenue (filer states none)"),
    (re.compile(u"国内の(?:外部顧客への)?(?:売上高|売上収益|営業収益|収益)のみ"),
     "no overseas revenue (filer states domestic only)"),
    (re.compile(u"(?:海外|本邦以外)[^。]{0,30}?(?:売上高|売上収益|収益)[^。]{0,40}?10%未満[^。]{0,40}?省略"),
     "overseas revenue below 10% (filer omitted the table)"),
    (re.compile(u"(本邦|日本|国内)[^。]{0,40}?(売上高|売上収益|営業収益|収益)[^。]{0,60}?(90|９０)%[^。]{0,40}?省略"),
     "domestic revenue exceeds 90% (filer omitted the table)"),
    (re.compile(u"(本邦|日本|国内)[^。]{0,40}?(売上高|売上収益|営業収益|収益)[^。]{0,60}?大部分[^。]{0,40}?省略"),
     "domestic revenue is the large majority (filer omitted the table)"),
]
BASIS_RE = re.compile(u"(?:売上高|売上収益|収益)は、?(?P<basis>[^。]{2,40}?)を基礎")
BASIS_ALT_RE = re.compile(u"(?P<basis>顧客の所在[国地][^、。]{0,12}?)(?:別|に)(?:分類|区分)")


# ---------------------------------------------------------------- one filing

def parse_instance(t1_blob):
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        names = [n for n in z.namelist() if "PublicDoc" in n and n.endswith(".xbrl")]
        if not names:
            raise ValueError("no XBRL instance in t1 package")
        x = z.read(names[0]).decode("utf-8", "replace")
    m = STANDARD_RE.search(x)
    standard = norm(m.group(1)) if m else None
    m = PERIOD_END_RE.search(x)
    period_end = norm(m.group(1)) if m else None
    single = SINGLE_SEGMENT_MARK in x
    best, element = "", None
    for m in NOTE_RE.finditer(x):
        if len(m.group(2)) > len(best):
            best, element = html.unescape(m.group(2)), m.group(1)
    return standard, period_end, single, element, best


def choose_region_tables(candidates, consolidated):
    """One table per (year, measure). A table in the region section beats one
    found elsewhere; among several, the one whose own 合計 is nearest the
    consolidated revenue wins. Nothing is merged across tables."""
    by_key = defaultdict(list)
    for sec, rows in candidates:
        for year, measure in sorted({(r[0], r[1]) for r in rows}):
            subset = [r for r in rows if r[0] == year and r[1] == measure]
            total = next((r[5] for r in subset if r[4] == "TOTAL"), None)
            by_key[(year, measure)].append((sec, total, subset))
    out = []
    for key, options in by_key.items():
        def score(opt):
            sec, total, subset = opt
            in_section = 0 if sec == "region" else 1
            if key[1] == "revenue" and key[0] == 0 and consolidated and total:
                gap = abs(total - consolidated) / abs(consolidated)
            else:
                gap = 0.0
            return (in_section, gap)
        best = min(options, key=score)
        out.extend(best[2])
    return out


def extract(block_html, period_end, consolidated=None):
    tables, tail = sequence(block_html)
    regions, customers, products = [], [], []
    notes = []
    basis = None
    omitted = None
    full_text = strip_tags(block_html)
    m = BASIS_RE.search(full_text) or BASIS_ALT_RE.search(full_text)
    if m:
        basis = m.group("basis")
    for rx, reason in OMIT_RES:
        if rx.search(full_text):
            omitted = reason
            break

    candidates = []          # (section, rows) for every region-shaped table
    customer_tables = []     # one entry per 主要な顧客 table, in document order
    for cells, ctx in tables:
        sec = ctx.get("section")
        got = []
        if sec == "customer":
            got = read_customer_table(cells, ctx)
            if got:
                customer_tables.append(got)
                continue
        # In the reportable-segment section a table is a segment table first:
        # a filer whose segments are named 日本 and 中国 would otherwise be read
        # as regions and counted twice against the real region table.
        if sec == "segment":
            got = read_segment_table(cells, ctx)
            if got:
                products.extend(got)
                continue
        got = read_region_table(cells, ctx, period_end)
        if got:
            # sub-notes live in the text right after the table
            for sm in SUBNOTE_RE.finditer(ctx.get("after", "")[:400]):
                parent, child = region_key(sm.group("parent"))[0], sm.group("child")
                if not parent:
                    continue
                amt = float(sm.group("amt").replace(",", "")) * (
                    1e6 if sm.group("unit") == u"百万円" else 1e3)
                lab, key = region_key(child)
                got.append([got[0][0], got[0][1], len(got), lab or child, key, amt,
                            None, True, parent])
            candidates.append((sec, got))
            continue
        if sec in ("product", None):
            got = read_segment_table(cells, ctx)
            if got:
                products.extend(got)
                continue
    if omitted:
        # The filer said there is no region revenue to show; a region-shaped
        # table found elsewhere in the note (a PPE breakdown, a segment table
        # with places for names) must not stand in for it.
        candidates = [(sec, [r for r in rows if r[1] != "revenue"])
                      for sec, rows in candidates]
        candidates = [(sec, rows) for sec, rows in candidates if rows]
    regions = choose_region_tables(candidates, consolidated)
    # A filer prints the customer table once per year, prior first. Where the
    # running text did not mark the year — the 当連結会計年度 heading sits
    # between the tables and is easy to miss — assign by document order rather
    # than let both land on the current year and double the filer's revenue.
    if len({t[0][0] for t in customer_tables if t}) == 1 and len(customer_tables) > 1:
        for back, rows_ in enumerate(reversed(customer_tables)):
            for r in rows_:
                r[0] = back
        notes.append("customer tables had no year marker; assigned by document order")
    for t in customer_tables:
        customers.extend(t)

    # prose customers, only if no table gave any
    if not customers:
        customers = read_customer_prose(full_text)
    # One customer, one amount. A filer whose customer relates to several
    # segments stacks the segment names in a single cell, and expanding the
    # rowspan yields the same customer once per segment — summing those puts a
    # filer above 100% of its own revenue. Collapse to one row and keep every
    # segment the filer listed.
    collapsed, seen = [], {}
    for c in customers:
        key = (c[0], norm(c[1]), c[2])
        if key in seen:
            prev = seen[key]
            if c[3] and c[3] not in (prev[3] or ""):
                prev[3] = ((prev[3] + " · ") if prev[3] else "") + c[3]
            continue
        row = list(c)
        seen[key] = row
        collapsed.append(row)
    customers = collapsed
    # a table with no year context defaults to the current year, and says so
    for x in regions:
        if x[0] is None:
            x[0] = 0
            notes.append("region rows without a year marker taken as current")
            break
    for x in customers:
        if x[0] is None:
            x[0] = 0
    for x in products:
        if x[0] is None:
            x[0] = 0
    return regions, customers, products, basis, omitted, len(tables), sorted(set(notes))


def consolidated_revenue(con, doc_ids, years=None):
    """{doc_id: yen} from the financials extractor, best element available.

    `years` ({doc_id: (edinet_code, period_end)}) lets a filing the financials
    extractor has not read — a correction it skipped or failed — take the
    revenue of the newest one it read for the same company and year."""
    out = {}
    if not doc_ids:
        return out
    names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if "eq_fin_facts" not in names:
        return out
    rank = {e: i for i, e in enumerate(REVENUE_ELEMENTS)}
    placeholders = ",".join("?" * len(REVENUE_ELEMENTS))
    rows = con.execute(
        "SELECT doc_id, element, value FROM eq_fin_facts "
        "WHERE year_offset = 0 AND period_kind = 'duration' AND basis = 'consolidated' "
        "AND element IN (%s)" % placeholders, REVENUE_ELEMENTS).fetchall()
    best = {}
    for doc_id, element, value in rows:
        if doc_id not in doc_ids or value is None:
            continue
        r = rank[element]
        if doc_id not in best or r < best[doc_id][0]:
            best[doc_id] = (r, value)
    out = {d: v for d, (_r, v) in best.items()}
    for doc_id, (code, period) in (years or {}).items():
        if doc_id in out or not code or not period:
            continue
        row = con.execute(
            "SELECT doc_id FROM eq_fin_filings WHERE edinet_code = ? AND period_end = ? "
            "AND status IN ('clean','partial') ORDER BY filed_date DESC, doc_id DESC LIMIT 1",
            [code, period]).fetchone()
        if row and row[0] != doc_id:
            got = consolidated_revenue(con, {row[0]})
            if row[0] in got:
                out[doc_id] = got[row[0]]
    return out


def reconcile(regions, consolidated):
    """(sum, verdict) over current-year region revenue rows that are neither
    totals nor sub-notes."""
    cur = [x for x in regions if x[0] == 0 and x[1] == "revenue" and not x[7]]
    leaves = [x for x in cur if x[4] not in ("TOTAL", "OV")]
    overseas = [x for x in cur if x[4] == "OV"]
    # 海外 printed next to 北米/欧州/アジア is their subtotal, not another region;
    # printed next to 日本 alone it is the only overseas figure there is.
    if overseas and any(x[4] not in ("JP", "OT", None) for x in leaves):
        parts = [x[5] for x in leaves]
    else:
        parts = [x[5] for x in leaves] + [x[5] for x in overseas]
    totals = [x[5] for x in cur if x[4] == "TOTAL"]
    total = max(totals) if totals else None
    if not parts:
        return None, None
    s = sum(parts)
    if consolidated and consolidated > 0:
        gap = abs(s - consolidated) / abs(consolidated)
        if gap <= RECONCILE_TOLERANCE:
            return s, "ok"
        return s, "regions sum %.0f vs consolidated %.0f (%.2f%%)" % (s, consolidated, gap * 100)
    if total:
        gap = abs(s - total) / abs(total)
        return s, "ok (table total)" if gap <= RECONCILE_TOLERANCE else \
            "regions sum %.0f vs table total %.0f (%.2f%%)" % (s, total, gap * 100)
    return s, "no consolidated revenue to check against"


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every filer, not just listed")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs")
    ap.add_argument("--sec-codes", help="comma-separated 4-digit securities codes")
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--dump", action="store_true", help="print parsed rows per filing")
    ap.add_argument("--catch-up", type=int, default=CATCH_UP_DAYS,
                    metavar="DAYS",
                    help="on a --new-only run, also read the DAYS deepest archive "
                         "days below this extractor's own floor, so a "
                         "database built forward from a watermark fills in its "
                         "own history a slice at a time. 0 (default) is "
                         "forward only.")
    args = ap.parse_args()

    src = S3Source(args.workers) if args.source == "s3" else LocalSource()
    codelist = load_codelist()
    listed = {d[u"ＥＤＩＮＥＴコード"] for d in codelist if d[u"上場区分"] == u"上場"}

    since, have = (incremental_window(args.db, EXTRACTOR, "eq_seg_filings")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, EXTRACTOR))
    if since is not None:
        print("incremental: %d of %d archived filings are new since %s"
              % (len(pending), len(filings), since))
    meta = src.list_metadata(days=None if since is None else {r["date"] for r in pending.values()})
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
    if args.sec_codes:
        want = {c.strip()[:4] for c in args.sec_codes.split(",") if c.strip()}
        targets = [t for t in targets if (t[2].get("secCode") or "")[:4] in want]
    if args.limit:
        targets = targets[:args.limit]
    print("target filings: %d (source=%s)" % (len(targets), src.name))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    revenue = consolidated_revenue(con, {t[0] for t in targets},
                                   {t[0]: (t[2].get("edinetCode"), t[2].get("periodEnd")) for t in targets})

    def fetch_and_parse(t):
        doc_id, rec, m = t
        try:
            t1 = read_t1(src, doc_id, rec["date"])
            sha = hashlib.sha256(t1).hexdigest()
            standard, period_end, single, element, block = parse_instance(t1)
            if not block:
                return t, (standard, period_end, single, None, [], [], [], None, None, 0, []), sha, None
            regions, customers, products, basis, omitted, ntab, notes = extract(
                block, period_end, revenue.get(doc_id))
            return t, (standard, period_end, single, element, regions, customers,
                       products, basis, omitted, ntab, notes), sha, None
        except Exception as e:                                    # noqa: BLE001
            return t, None, None, "%s: %s" % (type(e).__name__, str(e)[:160])

    stats = defaultdict(int)
    counts = defaultdict(int)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha, err = fut.result()
            done += 1
            if done % 250 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"),
                    m.get("periodEnd") or None, rec["date"], sha, PARSER_VERSION]
            for tbl in ("eq_seg_filings", "eq_seg_regions", "eq_seg_customers", "eq_seg_products"):
                con.execute("DELETE FROM %s WHERE doc_id = ?" % tbl, [doc_id])
            if err:
                stats["failed"] += 1
                con.execute("INSERT INTO eq_seg_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                            base + ["failed", err] + [None] * (FILINGS_COLS - 10))
                continue
            (standard, period_end, single, element, regions, customers, products,
             basis, omitted, ntab, notes) = parsed
            cons = revenue.get(doc_id)
            reg_sum, verdict = reconcile(regions, cons)
            problems = list(notes)
            has_region_rev = any(x[0] == 0 and x[1] == "revenue" and not x[7] for x in regions)
            # A ">=10% customer" that exceeds the filer's whole revenue is a
            # unit misread, not a disclosure. Reported, never silently kept.
            named_total = sum(c[2] for c in customers if c[0] == 0 and c[2])
            if cons and named_total > cons * 1.05:
                problems.append(
                    "named customers total %.0f%% of consolidated revenue"
                    % (named_total / cons * 100))
            oversized = [c for c in customers
                         if cons and c[2] and c[2] > cons * 1.05]
            if oversized:
                problems.append(
                    "%d named customer(s) larger than consolidated revenue, e.g. %s at %.0fx"
                    % (len(oversized), oversized[0][1][:28], oversized[0][2] / cons))
            if element is None:
                problems.append("no segment note element in instance (%s)" % (standard or "standard unknown"))
            elif not has_region_rev and not omitted:
                problems.append("no region revenue table found and no omission stated")
            if verdict and not verdict.startswith("ok"):
                problems.append(verdict)
            status = "partial" if problems else "clean"
            stats[status] += 1
            con.execute("INSERT INTO eq_seg_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                        base + [status, "; ".join(problems) or None, standard, element, single,
                                basis, len(regions), len(customers), len(products), omitted,
                                ntab, cons, reg_sum, verdict])
            if regions:
                con.executemany("INSERT INTO eq_seg_regions VALUES (?,?,?,?,?,?,?,?,?,?)",
                                [[doc_id] + x for x in regions])
            if customers:
                con.executemany("INSERT INTO eq_seg_customers VALUES (?,?,?,?,?,?,?)",
                                [[doc_id, x[0], i, x[1], x[2], x[3], x[4]]
                                 for i, x in enumerate(customers)])
            if products:
                con.executemany("INSERT INTO eq_seg_products VALUES (?,?,?,?,?,?,?,?)",
                                [[doc_id] + x for x in products])
            counts["regions"] += len(regions)
            counts["customers"] += len(customers)
            counts["products"] += len(products)
            if args.dump:
                print("\n--- %s %s %s  status=%s  %s" % (
                    doc_id, base[2], (base[3] or "")[:24], status, "; ".join(problems)))
                print("    standard=%s single=%s basis=%s unit-tables=%d cons=%s"
                      % (standard, single, basis, ntab, cons))
                for x in regions:
                    print("    R y%s %-10s %-14s %-4s %16s%s%s" % (
                        x[0], x[1], x[3][:14], x[4] or "--",
                        "{:,.0f}".format(x[5]), " %s%%" % x[6] if x[6] else "",
                        "  (sub-note of %s)" % x[8] if x[7] else ""))
                for x in customers:
                    print("    C y%s %-46s %16s %s [%s]" % (
                        x[0], x[1][:46], "{:,.0f}".format(x[2]), x[3] or "", x[4]))
                for x in products:
                    print("    P y%s %-22s ext=%s tot=%s profit=%s assets=%s" % (
                        x[0], x[2][:22], *["{:,.0f}".format(v) if v is not None else "—"
                                          for v in x[3:7]]))

    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-10s %d" % (k, stats[k]))
    print("rows written: regions %d, customers %d, products %d"
          % (counts["regions"], counts["customers"], counts["products"]))
    con.close()
    record_run(args.db, EXTRACTOR, through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
