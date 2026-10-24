# -*- coding: utf-8 -*-
"""M3 — rental-property fair value extractor (賃貸等不動産関係), parser rent-1.

The one place a Japanese filer must put a MARKET value on its real estate:
the 賃貸等不動産 note discloses the balance-sheet carrying amount and the
fiscal-year-end fair value (時価) of rental/investment property, mostly from
appraisals. Combined with the facilities dataset (book values, per site) this
turns "book is not market" from a caveat into a number per company.

Source is the t1 honbun HTML (the note is a text block; the t5 CSV flattens
its table and glues the numbers together). Element:
``jpcrp_cor:NotesRealEstateForLeaseEtc[Consolidated]FinancialStatementsTextBlock``
— consolidated preferred where both exist. IFRS adopters disclose investment
property in IFRS notes this extractor does not read; they record ``no_note``.

Two table layouts in the wild, detected per marker cell:

- **vertical** (Tokyo Gas, Tobu, Fuji Media): rows labelled 期首残高 /
  期中増減額 / 期末残高 / 期末時価, one column per fiscal year — the current
  year is the rightmost, the prior year comes free.
- **horizontal** (Isetan Mitsukoshi, Mitsui Fudosan): those labels are column
  headers, one table per fiscal year. Consecutive-year tables are recognised
  because the year-end balance rolls: table N's 期首残高 equals table N-1's
  期末残高 — the earlier table is folded in as the prior year.

A filer can split the note into 賃貸等不動産 proper and 賃貸等不動産として
使用される部分を含む不動産 (dual-use property, e.g. an HQ partly let out —
disclosed at the WHOLE property's amounts); categories are kept apart in
eq_rental_tables and summed for the filing headline.

Gate (recomputes the filer's own numbers): where a table shows opening
balance, movement and closing balance, they must roll (期首 + 増減 = 期末,
tolerance 2 units for the two roundings involved). A filing with any
non-rolling table is ``partial`` and excluded from cross-company surfaces.
Fair value is stored exactly as filed; unrealized gain (fair − carrying) is
derived downstream and carries its formula.

Usage:
    python rental_extract.py                      # local archive
    python rental_extract.py --source s3 --workers 12
    python rental_extract.py --docs S100YH8W      # subset

Stop the local API server first (DuckDB counts its reader as a lock):
    lsof -ti:8007 | xargs kill
Python 3.9.
"""
import argparse
import hashlib
import io
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact,
                     DB_PATH, incremental_window, record_run,
                     seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)
from facility_extract import grid_of, norm, read_t1, strip_tags, to_num

PARSER_VERSION = "rent-1"

MARK = "NotesRealEstateForLeaseEtc"

UNITS = ((u"百万円", 1e6), (u"千円", 1e3), (u"百万", 1e6), (u"千", 1e3))

BEGIN_RE = re.compile(u"期首残高")
CHANGE_RE = re.compile(u"増減")
END_RE = re.compile(u"(期末|年度末)残高")
FAIR_RE = re.compile(u"時価")
TOTAL_RE = re.compile(u"^(合計|計)$")
DUAL_RE = re.compile(u"使用される部分を含む")
COMBINED_RE = re.compile(u"賃貸等不動産及び|賃貸等不動産並びに")
IMMATERIAL_RE = re.compile(u"重要性が乏しい|重要性がない|該当事項(は|)あり")


def note_blocks(t1_blob):
    """(element_name, inner_html) for every rental-note text block."""
    out = []
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        for n in z.namelist():
            if "PublicDoc" not in n or not n.endswith(".htm"):
                continue
            h = z.read(n).decode("utf-8", "replace")
            if MARK not in h:
                continue
            for m in re.finditer(
                    r'<ix:nonNumeric[^>]*name="([^"]*%s[^"]*)"[^>]*>(.*?)'
                    r'</ix:nonNumeric>' % MARK, h, re.S):
                out.append((m.group(1), m.group(2)))
    # consolidated block wins; otherwise whatever exists
    cons = [b for b in out if "Consolidated" in b[0]]
    chosen = cons or out
    return list(dict.fromkeys(chosen)), bool(cons)


def row_numbers(cells, r, c):
    return [to_num(cells[r][j]) for j in range(c + 1, len(cells[r]))
            if to_num(cells[r][j]) is not None]


def col_numbers(cells, r, c):
    return [to_num(cells[i][c]) for i in range(r + 1, len(cells))
            if c < len(cells[i]) and to_num(cells[i][c]) is not None]


def find_marker(cells, rx):
    """Last cell matching rx (space-stripped) — deepest header row wins."""
    hit = None
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            if rx.search(cell.replace(" ", "")):
                hit = (r, c)
    return hit


def parse_note_table(table_html):
    """One table -> dict of begin/change/end/fair (unit-less floats), with
    prior-year values where the layout carries them. None if the table is not
    a balance/fair-value table (rental P&L tables have no 時価)."""
    cells, _ = grid_of(table_html)
    if not cells:
        return None
    end_m = find_marker(cells, END_RE)
    fair_m = find_marker(cells, FAIR_RE)
    if not end_m or not fair_m:
        return None

    def read(marker):
        if marker is None:
            return None, None, None          # current, prior, orientation
        r, c = marker
        rn = row_numbers(cells, r, c)
        if rn:
            return rn[-1], (rn[-2] if len(rn) > 1 else None), "v"
        cn = col_numbers(cells, r, c)
        if cn:
            # multiple body rows: a stated 合計 row wins, else their sum
            if len(cn) > 1:
                for i in range(len(cells) - 1, r, -1):
                    label = (cells[i][0] if cells[i] else "").replace(" ", "")
                    v = to_num(cells[i][c]) if c < len(cells[i]) else None
                    if v is not None and TOTAL_RE.match(label):
                        return v, None, "h_total"
                return sum(cn), None, "h_summed"
            return cn[0], None, "h"
        return None, None, None

    end, end_prior, how = read(end_m)
    fair, fair_prior, _ = read(fair_m)
    begin, begin_prior, _ = read(find_marker(cells, BEGIN_RE))
    change, change_prior, _ = read(find_marker(cells, CHANGE_RE))
    if end is None or fair is None:
        return None
    roll_ok = None
    if begin is not None and change is not None:
        roll_ok = abs(begin + change - end) <= 2
    return {"begin": begin, "change": change, "end": end, "fair": fair,
            "end_prior": end_prior, "fair_prior": fair_prior,
            "method": how or "v", "roll_ok": roll_ok}


_UNIT_MULT = {u"百万円": 1e6, u"億円": 1e8, u"千円": 1e3}
# A unit statement — 単位：百万円 — or a bare unit in a header cell,
# 期首残高(百万円). A bare unit preceded by a digit is a prose amount
# (賃貸損益は851百万円) and never the table's unit.
_UNIT_STATED_RE = re.compile(u"単位\\s*[:：]?\\s*(百万円|億円|千円)")
_UNIT_BARE_RE = re.compile(u"(?<![0-9,.．，])(百万円|億円|千円)")


def unit_before(block_text_upto, table_html):
    """The unit governing a table: inside the table first (単位 statement,
    then a bare header-cell unit), else in the block text before it. No
    default — no unit is a real failure."""
    table_text = strip_tags(table_html)
    for hay, rx in ((table_text, _UNIT_STATED_RE),
                    (table_text, _UNIT_BARE_RE),
                    (block_text_upto, _UNIT_STATED_RE),
                    (block_text_upto, _UNIT_BARE_RE)):
        last = None
        for m in rx.finditer(hay):
            last = m.group(1)
        if last:
            return last, _UNIT_MULT[last]
    return None, None


def parse_block(block_html):
    """All balance/fair-value tables in one note block, categorised and with
    consecutive-year duplicates folded into prior-year values."""
    tables = []
    pos = 0
    for m in re.finditer(r"<table[^>]*>.*?</table>", block_html, re.S | re.I):
        preceding = strip_tags(block_html[pos:m.start()])
        parsed = parse_note_table(m.group(0))
        if parsed:
            label, mult = unit_before(strip_tags(block_html[:m.start()]),
                                      m.group(0))
            parsed["category"] = (
                "combined" if COMBINED_RE.search(preceding)
                else "dual_use" if DUAL_RE.search(preceding)
                else "rental")
            parsed["unit_label"] = label
            parsed["mult"] = mult
            tables.append(parsed)
        pos = m.start()
    # fold consecutive fiscal years: N's opening balance == N-1's closing
    folded = []
    for t in tables:
        prev = folded[-1] if folded else None
        if (prev is not None and prev["category"] == t["category"]
                and t.get("begin") is not None
                and prev.get("end") is not None
                and abs(t["begin"] - prev["end"]) <= 2
                and t.get("end_prior") is None):
            t["end_prior"] = prev["end"]
            t["fair_prior"] = prev["fair"]
            folded[-1] = t
        else:
            folded.append(t)
    return folded


SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS eq_rental_filings (
        doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
        filer_name VARCHAR, period_end DATE, filed_date DATE,
        sha256_t1 VARCHAR, parser_version VARCHAR,
        status VARCHAR, detail VARCHAR, consolidated BOOLEAN,
        n_tables INTEGER,
        carrying_yen BIGINT, fair_value_yen BIGINT,
        carrying_prior_yen BIGINT, fair_value_prior_yen BIGINT);
    CREATE TABLE IF NOT EXISTS eq_rental_tables (
        doc_id VARCHAR, table_no INTEGER, category VARCHAR,
        unit_label VARCHAR,
        begin_yen BIGINT, change_yen BIGINT,
        carrying_yen BIGINT, fair_value_yen BIGINT,
        carrying_prior_yen BIGINT, fair_value_prior_yen BIGINT,
        roll_ok BOOLEAN, method VARCHAR,
        PRIMARY KEY (doc_id, table_no));
"""


def yen(v, mult):
    return None if v is None or not mult else int(round(v * mult))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs")
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
    codelist = load_codelist()
    listed = {d[u"ＥＤＩＮＥＴコード"] for d in codelist
              if d[u"上場区分"] == u"上場"}

    # Window first, then discovery: knowing `since` lets the bucket listing
    # seek to it instead of paging five years of keys.
    since, have = (incremental_window(args.db, "rental-property", "eq_rental_filings")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, "rental-property"))
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
    print("target filings: %d (source=%s)" % (len(targets), src.name))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)

    def fetch_and_parse(t):
        doc_id, rec, m = t
        sha1 = None
        try:
            t1 = read_t1(src, doc_id, rec["date"])
            sha1 = hashlib.sha256(t1).hexdigest()
            blocks, consolidated = note_blocks(t1)
            tables = []
            for _, b in blocks:
                tables.extend(parse_block(b))
            text = strip_tags(" ".join(b for _, b in blocks))[:400]
            return t, (blocks, tables, consolidated, text), sha1, None
        except Exception as e:                                    # noqa: BLE001
            return t, None, sha1, ("failed", "%s: %s"
                                   % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha1, err = fut.result()
            done += 1
            if done % 500 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"),
                    sec_code_of(m),
                    rec.get("filer") or m.get("filerName"),
                    m.get("periodEnd") or None, rec["date"],
                    sha1, PARSER_VERSION]
            con.execute("DELETE FROM eq_rental_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_rental_tables WHERE doc_id = ?", [doc_id])

            def write_filing(status, detail, consolidated=None, n=0,
                             cy=None, fv=None, cyp=None, fvp=None):
                con.execute(
                    "INSERT INTO eq_rental_filings VALUES (%s)" % ",".join(["?"] * 16),
                    base + [status, detail, consolidated, n, cy, fv, cyp, fvp])

            if err:
                stats[err[0]] += 1
                write_filing(*err)
                continue
            blocks, tables, consolidated, text = parsed
            if not blocks:
                stats["no_note"] += 1
                write_filing("no_note", None)
                continue
            if not tables:
                st = "immaterial" if IMMATERIAL_RE.search(text) else "no_table_parsed"
                stats[st] += 1
                write_filing(st, None, consolidated)
                continue

            bad = []
            if any(t["mult"] is None for t in tables):
                bad.append("no_unit")
            if any(t["roll_ok"] is False for t in tables):
                bad.append("roll_mismatch")
            cy = fv = 0
            cyp = fvp = 0
            has_prior = True
            for t in tables:
                cy += t["end"] * (t["mult"] or 0)
                fv += t["fair"] * (t["mult"] or 0)
                if t.get("end_prior") is None or t.get("fair_prior") is None:
                    has_prior = False
                else:
                    cyp += t["end_prior"] * (t["mult"] or 0)
                    fvp += t["fair_prior"] * (t["mult"] or 0)
            status = "partial" if bad else "clean"
            stats[status] += 1
            write_filing(status, ";".join(bad) or None, consolidated,
                         len(tables), int(cy) or None, int(fv) or None,
                         int(cyp) if has_prior and cyp else None,
                         int(fvp) if has_prior and fvp else None)
            for i, t in enumerate(tables):
                con.execute(
                    "INSERT INTO eq_rental_tables VALUES (%s)" % ",".join(["?"] * 12),
                    [doc_id, i, t["category"], t["unit_label"],
                     yen(t.get("begin"), t["mult"]), yen(t.get("change"), t["mult"]),
                     yen(t["end"], t["mult"]), yen(t["fair"], t["mult"]),
                     yen(t.get("end_prior"), t["mult"]),
                     yen(t.get("fair_prior"), t["mult"]),
                     t["roll_ok"], t["method"]])

    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-16s %d" % (k, stats[k]))
    con.close()
    record_run(args.db, "rental-property", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
