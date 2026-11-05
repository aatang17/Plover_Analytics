# -*- coding: utf-8 -*-
"""Bank balance-sheet notes — bond maturities, unrealised gains, deposits by term.

What this reads
---------------
Three tables in the financial-instruments and securities notes of a bank's
annual securities report (有価証券報告書, 【注記事項】), from the t1 package
the other annual-report extractors open:

- **Maturity ladder** (金銭債権及び満期のある有価証券の連結決算日後の償還予定額):
  securities and loans by remaining term in six buckets — within 1 year, 1–3,
  3–5, 5–7, 7–10 and over 10 years — with the securities split into
  held-to-maturity and available-for-sale and each of those into JGBs,
  municipal bonds, corporate bonds, foreign bonds and other. This is the only
  public, per-bank statement of how long the bond book runs, which is what
  sizes the loss when yields rise.
- **Funding by term** (有利子負債の連結決算日後の返済予定額): deposits,
  negotiable CDs, borrowings and bonds in the same buckets. Demand deposits
  sit in "within 1 year" by the note's own rule.
- **Unrealised gains and losses** (（有価証券関係） その他有価証券): for
  available-for-sale securities, book value, cost and the difference by type
  (equities, bonds — JGBs, municipal, corporate — and other, of which
  foreign bonds), in the two blocks the note uses (above cost / below cost)
  and the total. Held-to-maturity bonds' book value, fair value and the
  difference are read from the neighbouring table where one exists.

Nothing here is tagged as a number in the XBRL instance — these notes are text
blocks — so this is a table parse, like the facilities and segment
extractors, and the ORDER of the tables carries their meaning: the note
prints the prior year first, then the current year, and the consolidated note
comes before the non-consolidated one. The heading immediately above each
table (前連結会計年度 / 当連結会計年度 / 前事業年度 / 当事業年度) says which,
and is what is used; position is only the fallback.

What is kept
------------
Every row of every bucket, verbatim label plus a key, in yen. Consolidated
where the filer consolidates; the non-consolidated tables otherwise (a bank
with no subsidiaries files only those). "－" is missing and never zero.

Gates
-----
- the current-year maturity table must carry a securities row and a loans
  row, and its 合計 row must equal the sum of its top-level rows in every
  bucket (rounding to the display unit across a handful of rows);
- the available-for-sale table's 合計 must equal the sum of its two blocks,
  and its difference must equal book value less cost, within rounding.
A filing that fails a gate is `partial` with the reason; nothing is ever
recomputed to make a gate pass.

Also read, best effort: the banking-book value-at-risk sentence in the
market-risk note (バンキング取引のVaRは…百万円), which is the bank's own
one-year loss estimate and the nearest thing to a rate-risk figure the
report carries. The Basel III interest-rate-risk figures (ΔEVE, ΔNII) are
NOT in the securities report; they live in each bank's Pillar 3 disclosure
and are collected separately (irrbb_collect.py).

Usage
-----
  python bank_extract.py --source api --docs S100YDSE --dump      # one filing, show the parse
  python bank_extract.py --source s3 --new-only                    # the nightly form
  python bank_extract.py --source api --all --db ../data/equity.duckdb
"""
import argparse
import datetime as _dt
import hashlib
import io
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, DB_PATH, compact, record_run)
from facility_extract import grid_of, norm, read_t1, strip_tags, to_num

PARSER_VERSION = "bank-1"
EXTRACTOR = "bank-balance"
INDUSTRY = u"銀行業"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eq_bank_filings (
    doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
    filer_name VARCHAR, period_end DATE, filed_date DATE, sha256_t1 VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR,
    basis VARCHAR, unit_label VARCHAR,
    maturity_rows INTEGER, funding_rows INTEGER, securities_rows INTEGER,
    securities_yen DOUBLE, loans_yen DOUBLE, deposits_yen DOUBLE,
    afs_book_yen DOUBLE, afs_cost_yen DOUBLE, afs_diff_yen DOUBLE,
    afs_bond_diff_yen DOUBLE, afs_jgb_diff_yen DOUBLE,
    var_banking_yen DOUBLE, var_banking_prior_yen DOUBLE, var_text VARCHAR);
CREATE TABLE IF NOT EXISTS eq_bank_maturity (
    doc_id VARCHAR, year_offset INTEGER, basis VARCHAR, side VARCHAR,
    ord INTEGER, label_ja VARCHAR, item_key VARCHAR, parent_key VARCHAR,
    within_1y_yen DOUBLE, y1_3_yen DOUBLE, y3_5_yen DOUBLE, y5_7_yen DOUBLE,
    y7_10_yen DOUBLE, over_10y_yen DOUBLE, bucket_scheme VARCHAR);
CREATE TABLE IF NOT EXISTS eq_bank_securities (
    doc_id VARCHAR, year_offset INTEGER, basis VARCHAR, category VARCHAR,
    block VARCHAR, ord INTEGER, label_ja VARCHAR, type_key VARCHAR,
    book_yen DOUBLE, cost_yen DOUBLE, fair_yen DOUBLE, diff_yen DOUBLE);
"""
FILINGS_COLS = 26

# A few filers (Suruga, the megabanks' own statements) print five buckets
# with an open-ended "7年超" instead of 7–10 and over 10. That column is
# stored in y7_10 with bucket_scheme = '5_open7' so nobody reads it as 7–10.
BUCKETS = [
    ("within_1y", (u"1年以内",)),
    ("y1_3", (u"1年超3年以内",)),
    ("y3_5", (u"3年超5年以内",)),
    ("y5_7", (u"5年超7年以内",)),
    ("y7_10", (u"7年超10年以内", u"7年超")),
    ("over_10y", (u"10年超",)),
]
UNITS = ((u"百万円", 1e6), (u"千円", 1e3), (u"億円", 1e8))

# Maturity-ladder row keys. Order matters: the more specific first.
ITEM_KEYS = [
    ("securities", (u"有価証券",)),                # generic, refined below
    ("htm", (u"満期保有目的の債券", u"満期保有目的")),
    ("afs", (u"その他有価証券のうち満期があるもの", u"その他有価証券のうち満期",
             u"その他有価証券")),
    ("loans", (u"貸出金",)),
    ("due_from_banks", (u"預け金",)),
    ("call_loans", (u"コールローン",)),
    ("purchased_receivables", (u"買入金銭債権",)),
    ("money_trusts", (u"金銭の信託",)),
    ("trading", (u"特定取引",)),
    ("ncd", (u"譲渡性預金",)),                  # before 預金, which it contains
    ("deposits", (u"預金", u"貯金")),
    ("call_money", (u"コールマネー",)),
    ("repos", (u"売現先",)),
    ("securities_lending", (u"債券貸借取引受入担保金",)),
    ("borrowings", (u"借用金", u"借入金")),
    ("bonds_payable", (u"社債",)),
    ("total", (u"合計", u"計")),
]
SUB_KEYS = [
    ("foreign_equities", (u"外国株式",)),        # before 株式-free 債券 tests: MUFG lists it
    ("bonds", (u"債券",)),                     # a filer's own subtotal of the bond lines
    ("jgb", (u"国債",)),
    ("municipal", (u"地方債",)),
    ("short_corporate", (u"短期社債",)),
    ("corporate", (u"社債",)),
    ("foreign", (u"外国債券", u"外国証券")),
    ("other", (u"その他の証券", u"その他")),
]
# Rows that are members of a parent (うち…) rather than lines of their own.
SUB_LABEL_RE = re.compile(u"^(うち|内)")

# Securities-note row keys (the AFS / HTM tables).
SEC_TYPE_KEYS = [
    ("equities", (u"株式",)),
    ("foreign", (u"外国債券", u"外国証券")),      # before 債券: 外国債券 contains it
    ("investment_trusts", (u"投資信託",)),
    ("jgb", (u"国債",)),
    ("municipal", (u"地方債",)),
    ("short_corporate", (u"短期社債",)),
    ("corporate", (u"社債",)),
    ("bonds", (u"債券",)),
    ("subtotal", (u"小計",)),
    ("total", (u"合計",)),
    ("other", (u"その他",)),
]

HEAD_RE = re.compile(u"(前|当)(連結会計年度|事業年度|中間連結会計期間|中間会計期間)")
VAR_RE = re.compile(u"バンキング(?:取引|勘定|業務)[^。]{0,120}?VaR[^。]{0,60}?([\\d,]+)百万円"
                    u"(?:[^。]{0,40}?現在\\s*([\\d,]+)百万円)?")


# ------------------------------------------------------------------ helpers

def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def compact_label(s):
    """A cell label with whitespace, footnote marks and brackets removed."""
    s = nfkc(strip_tags(s))
    s = re.sub(u"[（(][^（()）]*[）)]", "", s)
    s = re.sub(u"[※*＊]\\s*\\d*", "", s)
    return re.sub(r"\s+", "", s)


def key_of(label, table):
    lab = compact_label(label)
    for key, needles in table:
        for n in needles:
            if n in lab:
                return key
    return None


def bucket_columns(cells):
    """(header row index, {bucket key: column index}) or (None, None)."""
    for r, row in enumerate(cells[:4]):
        found = {}
        for c, cell in enumerate(row):
            lab = compact_label(cell)
            for key, needles in BUCKETS:
                if lab in needles and key not in found:
                    found[key] = c
        if len(found) >= 5:
            return r, found
    return None, None


def unit_of(text):
    t = nfkc(text)
    for label, mult in UNITS:
        if label in t:
            return label, mult
    return None, None


def preceding_heading(html_text, pos, window=1500):
    """Which year and basis the table under `pos` belongs to, from the
    nearest 前/当 heading above it: (year_offset, basis) or (None, None)."""
    text = strip_tags(html_text[max(0, pos - window):pos])
    hits = list(HEAD_RE.finditer(text))
    if not hits:
        return None, None
    m = hits[-1]
    offset = -1 if m.group(1) == u"前" else 0
    basis = "consolidated" if u"連結" in m.group(2) else "non-consolidated"
    return offset, basis


def label_cells(row, first_value_col):
    return [c for c in row[:first_value_col] if compact_label(c)]


# ---------------------------------------------------------- maturity ladder

def parse_maturity(cells, hdr, cols):
    """Rows of a maturity table -> [(label, key, parent_key, {bucket: value})]."""
    first_value_col = min(cols.values())
    rows = []
    parent = None
    for row in cells[hdr + 1:]:
        labels = label_cells(row, first_value_col)
        if not labels:
            continue
        label = nfkc(strip_tags(labels[-1]))
        values = {}
        for key, c in cols.items():
            values[key] = to_num(row[c]) if c < len(row) else None
        if all(v is None for v in values.values()) and not label:
            continue
        lab = compact_label(label)
        # Under 有価証券 / HTM / AFS, any row that is not itself a top-level
        # line is a member of that parent — with or without an うち prefix,
        # and whether or not its label is one we name (MUFG prints 外国株式).
        is_sub = bool(SUB_LABEL_RE.match(lab)) or (parent in ("securities", "htm", "afs")
                                                    and key_of(lab, ITEM_KEYS) in (None, "bonds_payable"))
        if is_sub and parent in ("securities", "htm", "afs"):
            key = key_of(re.sub(u"^(うち|内)", "", lab), SUB_KEYS) or "other_sub"
            rows.append((label, key, parent, values))
            continue
        key = key_of(lab, ITEM_KEYS)
        if key == "securities" and (u"満期保有" in lab or u"のうち" in lab):
            key = "htm" if u"満期保有" in lab else "afs"
        if key in ("htm", "afs"):
            parent = key
            rows.append((label, key, "securities", values))
            continue
        parent = key
        rows.append((label, key, None, values))
    return rows


def top_value(row, rows, key):
    """A top-level row's value in one bucket. Some filers leave the 有価証券
    line blank and print only its held-to-maturity and available-for-sale
    parts; the parts are then what the 合計 adds, so the gate — and the
    headline securities figure — take their sum. The stored rows stay as
    printed."""
    v = row[3].get(key)
    if v is not None or row[1] != "securities":
        return v
    parts = [r[3].get(key) for r in rows if r[2] == "securities" and r[3].get(key) is not None]
    return sum(parts) if parts else None


def maturity_gate(rows):
    """合計 == sum of top-level rows per bucket, within rounding. Returns a
    reason or None."""
    total = [r for r in rows if r[1] == "total"]
    if not total:
        return "no_total_row"
    total = total[-1][3]
    tops = [r for r in rows if r[2] is None and r[1] not in ("total",)]
    if not any(r[1] == "securities" for r in tops):
        return "no_securities_row"
    for key, _n in BUCKETS:
        s = sum((top_value(r, rows, key) or 0.0) for r in tops)
        t = total.get(key)
        if t is None:
            if s:
                return "total_missing_%s" % key
            continue
        if abs(s - t) > max(2.0, 1.0 * len(tops)):
            return "total_mismatch_%s:%s_vs_%s" % (key, int(s), int(t))
    return None


# ------------------------------------------------------ securities note

def parse_securities(cells):
    """An AFS (book/cost/diff) or HTM (book/fair/diff) table ->
    (category, [(block, label, key, book, cost, fair, diff)])."""
    head = [compact_label(c) for c in cells[0]] if cells else []
    head_text = " ".join(head)
    if u"取得原価" in head_text:
        category = "afs"
    elif u"時価" in head_text:
        category = "htm"
    else:
        return None, []
    # column roles from the header
    col = {}
    for c, h in enumerate(head):
        if (u"計上額" in h or u"貸借対照表" in h) and "book" not in col:
            col["book"] = c
        elif u"取得原価" in h:
            col["cost"] = c
        elif u"時価" in h and "fair" not in col:
            col["fair"] = c
        elif u"差額" in h:
            col["diff"] = c
    if "book" not in col or "diff" not in col:
        return None, []
    type_col = next((c for c, h in enumerate(head) if u"種類" in h), 0)
    out = []
    block = None
    for row in cells[1:]:
        blk = compact_label(row[0]) if row else ""
        if u"超えるもの" in blk or u"上回る" in blk:
            block = "above"
        elif u"超えないもの" in blk or u"下回る" in blk:
            block = "below"
        label = nfkc(strip_tags(row[type_col])) if type_col < len(row) else ""
        lab = compact_label(label)
        if not lab and blk:
            label, lab = nfkc(strip_tags(row[0])), blk
        key = key_of(lab, SEC_TYPE_KEYS)
        if key is None:
            continue
        if key == "total":
            block = "total"
        vals = {}
        for role, c in col.items():
            vals[role] = to_num(row[c]) if c < len(row) else None
        out.append((block, label, key, vals.get("book"), vals.get("cost"),
                    vals.get("fair"), vals.get("diff")))
    return category, out


def securities_gate(rows):
    """The AFS table's 合計 equals the two blocks' 小計 and diff == book − cost."""
    subs = [r for r in rows if r[2] == "subtotal"]
    tot = [r for r in rows if r[2] == "total"]
    if not tot:
        return "no_total_row"
    book, cost, _fair, diff = tot[-1][3], tot[-1][4], tot[-1][5], tot[-1][6]
    if len(subs) == 2 and book is not None:
        s = sum((r[3] or 0.0) for r in subs)
        if abs(s - book) > 2.0:
            return "blocks_mismatch:%s_vs_%s" % (int(s), int(book))
    if None not in (book, cost, diff) and abs((book - cost) - diff) > 2.0:
        return "diff_mismatch:%s_vs_%s" % (int(book - cost), int(diff))
    return None


# ---------------------------------------------------------------- document

def honbun_parts(t1_blob):
    out = []
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        for n in sorted(z.namelist()):
            if "PublicDoc" in n and n.endswith(".htm"):
                out.append((n, z.read(n).decode("utf-8", "replace")))
    return out


def parse_document(t1_blob):
    """-> dict(maturity=[...], securities=[...], var=..., unit=...)."""
    maturity, securities = [], []
    var = None
    unit_seen = set()
    for _name, h in honbun_parts(t1_blob):
        if u"償還予定額" not in h and u"取得原価" not in h and u"VaR" not in nfkc(h):
            continue
        for m in re.finditer(r"<table[^>]*>.*?</table>", h, re.S | re.I):
            t = m.group(0)
            txt = nfkc(strip_tags(t))
            if u"年以内" in txt and u"年超" in txt:
                cells, _origin = grid_of(t)
                hdr, cols = bucket_columns(cells)
                if hdr is None:
                    continue
                offset, basis = preceding_heading(h, m.start())
                unit_label, mult = unit_of(txt + strip_tags(h[max(0, m.start() - 400):m.start()]))
                if mult is None:
                    unit_label, mult = u"百万円", 1e6   # the statutory unit for banks
                rows = parse_maturity(cells, hdr, cols)
                scheme = "standard6" if "over_10y" in cols else "5_open7"
                keys = set(r[1] for r in rows)
                if keys & {"deposits", "ncd", "borrowings", "call_money"} and not keys & {"securities", "loans"}:
                    side = "funding"
                elif keys & {"securities", "loans"}:
                    side = "assets"
                else:
                    continue
                unit_seen.add(unit_label)
                maturity.append({"offset": offset, "basis": basis, "side": side,
                                 "rows": rows, "mult": mult, "unit": unit_label,
                                 "scheme": scheme})
            elif u"取得原価" in txt or (u"時価" in txt and u"差額" in txt):
                if u"差額" not in txt or not (u"株式" in txt or u"国債" in txt or u"債券" in txt):
                    continue
                cells, _origin = grid_of(t)
                category, rows = parse_securities(cells)
                if not rows:
                    continue
                if category == "htm" and not any(r[2] in ("bonds", "jgb", "corporate") for r in rows):
                    continue
                offset, basis = preceding_heading(h, m.start())
                unit_label, mult = unit_of(txt)
                if mult is None:
                    unit_label, mult = u"百万円", 1e6
                head_text = compact_label(" ".join(cells[0]))
                if basis is None:
                    basis = "consolidated" if u"連結" in head_text else "non-consolidated"
                securities.append({"offset": offset, "basis": basis, "category": category,
                                   "rows": rows, "mult": mult, "unit": unit_label})
        if var is None:
            text = nfkc(strip_tags(h))
            vm = VAR_RE.search(text)
            if vm:
                cur = to_num(vm.group(1))
                prior = to_num(vm.group(2)) if vm.group(2) else None
                var = {"current": cur * 1e6 if cur is not None else None,
                       "prior": prior * 1e6 if prior is not None else None,
                       "text": vm.group(0)[:300]}
    # Position fallback for tables whose heading could not be read: within a
    # (basis, side) group the note prints prior year first.
    for group in (maturity, securities):
        by = defaultdict(list)
        for t in group:
            by[(t["basis"], t.get("side") or t.get("category"))].append(t)
        for items in by.values():
            unknown = [t for t in items if t["offset"] is None]
            if unknown and len(items) == 2:
                items[0]["offset"], items[1]["offset"] = -1, 0
            elif unknown and len(items) == 1:
                items[0]["offset"] = 0
    return {"maturity": maturity, "securities": securities, "var": var,
            "units": sorted(unit_seen)}


def choose_basis(parsed):
    bases = set(t["basis"] for t in parsed["maturity"] if t["basis"])
    if "consolidated" in bases:
        return "consolidated"
    if "non-consolidated" in bases:
        return "non-consolidated"
    sb = set(t["basis"] for t in parsed["securities"] if t["basis"])
    return "consolidated" if "consolidated" in sb else ("non-consolidated" if sb else None)


# ------------------------------------------------------------------ sources

class ApiSource(object):
    """EDINET's own document API — for a laptop with no archive but a key.

    The bucket is the archive of record; this exists so the extractor can be
    developed and run against the live register without it. The zip is the
    same t1 package the capture job stores."""

    name = "api"

    def __init__(self):
        self.key = os.environ.get("EDINET_API_KEY")
        if not self.key:
            raise SystemExit("EDINET_API_KEY is not set")

    def read_t1(self, doc_id):
        import urllib.request
        cache = os.environ.get("EDINET_API_CACHE")
        path = os.path.join(cache, doc_id + ".zip") if cache else None
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        url = ("https://api.edinet-fsa.go.jp/api/v2/documents/%s?type=1&Subscription-Key=%s"
               % (doc_id, self.key))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            blob = resp.read()
        if blob[:2] != b"PK":
            raise ValueError("EDINET returned a non-zip for %s: %r" % (doc_id, blob[:80]))
        if path:
            os.makedirs(cache, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, path)
        return blob


def read_package(src, doc_id, date):
    if src.name == "api":
        return src.read_t1(doc_id)
    return read_t1(src, doc_id, date)


# --------------------------------------------------------------------- main

def targets_from_db(db_path, all_filings, docs, limit):
    """Every archived annual report by a bank-industry filer, from the
    register extract.py keeps — this extractor never discovers filings on
    its own, so its universe is exactly the bank filers the platform knows."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SELECT 1 FROM eq_filings LIMIT 1")
        have = set()
        names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        if "eq_bank_filings" in names and not all_filings:
            have = {r[0] for r in con.execute(
                "SELECT doc_id FROM eq_bank_filings WHERE parser_version = ?",
                [PARSER_VERSION]).fetchall()}
        rows = con.execute(
            "SELECT f.doc_id, f.edinet_code, f.sec_code, f.filer_name, f.period_end, "
            "       f.filed_date "
            "FROM eq_filings f JOIN eq_entities e USING (edinet_code) "
            "WHERE e.industry = ? ORDER BY f.filed_date, f.doc_id", [INDUSTRY]).fetchall()
    finally:
        con.close()
    out = [r for r in rows if r[0] not in have]
    if docs:
        want = {d.strip() for d in docs.split(",") if d.strip()}
        out = [r for r in rows if r[0] in want]
    if limit:
        out = out[:limit]
    return out, (max(r[5] for r in rows) if rows else None), len(rows)


def dump(doc_id, parsed):
    print("== %s  units=%s  var=%s" % (doc_id, parsed["units"],
                                       (parsed["var"] or {}).get("text")))
    for t in parsed["maturity"]:
        print("  maturity %s %s offset=%s unit=%s" % (t["basis"], t["side"], t["offset"], t["unit"]))
        for label, key, parent, values in t["rows"]:
            print("    %-28s %-12s %-10s %s" % (label[:28], key, parent or "",
                                                [values[k] for k, _n in BUCKETS]))
    for t in parsed["securities"]:
        print("  securities %s %s offset=%s" % (t["basis"], t["category"], t["offset"]))
        for block, label, key, book, cost, fair, diff in t["rows"]:
            print("    %-7s %-14s %-16s book=%s cost=%s fair=%s diff=%s"
                  % (block, label[:14], key, book, cost, fair, diff))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="re-read every bank filing, not only those without rows "
                         "from this parser version")
    ap.add_argument("--source", choices=("local", "s3", "api"), default="local")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs")
    ap.add_argument("--new-only", action="store_true",
                    help="accepted for uniformity with the other extractors; the "
                         "default already reads only filings not yet stored")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--dump", action="store_true", help="print what was parsed")
    ap.add_argument("--catch-up", type=int, default=0, help="accepted; unused")
    args = ap.parse_args()

    if args.source == "api":
        src = ApiSource()
    elif args.source == "s3":
        src = S3Source(args.workers)
    else:
        src = LocalSource()

    targets, through, universe = targets_from_db(args.db, args.all, args.docs, args.limit)
    print("bank filings known: %d; to read: %d (source=%s)" % (universe, len(targets), src.name))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)

    def fetch_and_parse(t):
        doc_id, _e, _s, _n, _pe, filed = t
        sha1 = None
        try:
            blob = read_package(src, doc_id, filed.isoformat() if hasattr(filed, "isoformat") else str(filed))
            sha1 = hashlib.sha256(blob).hexdigest()
            return t, parse_document(blob), sha1, None
        except Exception as e:                                    # noqa: BLE001
            return t, None, sha1, ("failed", "%s: %s" % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            t, parsed, sha1, err = fut.result()
            doc_id, edinet_code, sec_code, filer_name, period_end, filed = t
            done += 1
            if done % 50 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, edinet_code, sec_code, filer_name, period_end, filed,
                    sha1, PARSER_VERSION]
            con.execute("DELETE FROM eq_bank_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_bank_maturity WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_bank_securities WHERE doc_id = ?", [doc_id])

            def write_filing(status, detail, extra=None):
                extra = extra or {}
                con.execute(
                    "INSERT INTO eq_bank_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                    base + [status, detail, extra.get("basis"), extra.get("unit"),
                            extra.get("maturity_rows", 0), extra.get("funding_rows", 0),
                            extra.get("securities_rows", 0),
                            extra.get("securities_yen"), extra.get("loans_yen"),
                            extra.get("deposits_yen"),
                            extra.get("afs_book"), extra.get("afs_cost"), extra.get("afs_diff"),
                            extra.get("afs_bond_diff"), extra.get("afs_jgb_diff"),
                            extra.get("var"), extra.get("var_prior"), extra.get("var_text")])

            if err:
                stats[err[0]] += 1
                write_filing(*err)
                continue
            if args.dump:
                dump(doc_id, parsed)
            basis = choose_basis(parsed)
            if basis is None:
                stats["no_tables"] += 1
                write_filing("no_tables", None)
                continue
            mats = [m for m in parsed["maturity"] if m["basis"] == basis]
            secs = [s for s in parsed["securities"] if s["basis"] == basis]
            bad = []
            cur_assets = [m for m in mats if m["side"] == "assets" and m["offset"] == 0]
            if not cur_assets:
                bad.append("no_current_maturity_table")
            else:
                reason = maturity_gate(cur_assets[-1]["rows"])
                if reason:
                    bad.append("maturity:" + reason)
            cur_afs = [s for s in secs if s["category"] == "afs" and s["offset"] == 0]
            if not cur_afs:
                bad.append("no_current_afs_table")
            else:
                reason = securities_gate(cur_afs[-1]["rows"])
                if reason:
                    bad.append("afs:" + reason)
            if len(parsed["units"]) > 1:
                bad.append("mixed_units:%s" % ",".join(parsed["units"]))

            extra = {"basis": basis, "unit": ",".join(parsed["units"]) or None}
            n_m = n_f = n_s = 0
            for m in mats:
                for i, (label, key, parent, values) in enumerate(m["rows"]):
                    vals = [None if values.get(k) is None else values[k] * m["mult"]
                            for k, _n in BUCKETS]
                    con.execute(
                        "INSERT INTO eq_bank_maturity VALUES (%s)" % ",".join(["?"] * 15),
                        [doc_id, m["offset"], m["basis"], m["side"], i, label, key, parent] + vals + [m["scheme"]])
                    if m["side"] == "assets":
                        n_m += 1
                    else:
                        n_f += 1
                    if m["offset"] == 0:
                        if key == "securities" and parent is None:
                            svals = [top_value((label, key, parent, values), m["rows"], k) for k, _n in BUCKETS]
                            svals = [None if v is None else v * m["mult"] for v in svals]
                            total = sum(v for v in svals if v is not None) if any(v is not None for v in svals) else None
                        else:
                            total = sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
                        if key == "securities" and parent is None:
                            extra["securities_yen"] = total
                        elif key == "loans" and parent is None:
                            extra["loans_yen"] = total
                        elif key == "deposits" and parent is None:
                            extra["deposits_yen"] = total
            for s in secs:
                for i, (block, label, key, book, cost, fair, diff) in enumerate(s["rows"]):
                    mult = s["mult"]
                    con.execute(
                        "INSERT INTO eq_bank_securities VALUES (%s)" % ",".join(["?"] * 12),
                        [doc_id, s["offset"], s["basis"], s["category"], block, i, label, key,
                         None if book is None else book * mult,
                         None if cost is None else cost * mult,
                         None if fair is None else fair * mult,
                         None if diff is None else diff * mult])
                    n_s += 1
                    if s["offset"] == 0 and s["category"] == "afs":
                        if key == "total":
                            extra["afs_book"] = None if book is None else book * mult
                            extra["afs_cost"] = None if cost is None else cost * mult
                            extra["afs_diff"] = None if diff is None else diff * mult
                        elif key == "bonds" and diff is not None:
                            extra["afs_bond_diff"] = (extra.get("afs_bond_diff") or 0.0) + diff * mult
                        elif key == "jgb" and diff is not None:
                            extra["afs_jgb_diff"] = (extra.get("afs_jgb_diff") or 0.0) + diff * mult
            if parsed["var"]:
                extra["var"] = parsed["var"]["current"]
                extra["var_prior"] = parsed["var"]["prior"]
                extra["var_text"] = parsed["var"]["text"]
            extra.update({"maturity_rows": n_m, "funding_rows": n_f, "securities_rows": n_s})
            status = "partial" if bad else "clean"
            stats[status] += 1
            write_filing(status, ";".join(bad) or None, extra)

    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-16s %d" % (k, stats[k]))
    con.close()
    # Recorded whether or not there was anything to read: banks file twice a
    # year, so most nights there is nothing, and a run stamped only when a
    # filing landed looked like an extractor that had stopped (2026-11-05).
    if through is not None and not args.docs:
        record_run(args.db, EXTRACTOR, through, universe, PARSER_VERSION)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
