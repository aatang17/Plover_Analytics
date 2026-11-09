# -*- coding: utf-8 -*-
"""Borrowings from the IFRS financial-liabilities note — the debt a balance
sheet does not print on a line of its own.

Why this exists
---------------
Net cash (app/fin_metrics.cash_position) is liquid cash less the borrowing
lines on the face of the balance sheet. Some IFRS filers print no borrowing
line at all and keep their bonds and loans inside 「その他の金融負債」 beside
deposits received and guarantee deposits: Fast Retailing's ¥211bn of bonds,
NSK's ¥301bn of loans and bonds, Miura's ¥102bn of loans. Read from the face
alone, those companies look debt-free; withheld, a genuinely debt-free one
(Kakaku.com, Cookpad) gets a dash it does not deserve. The note that itemises
the line settles both.

What this reads
---------------
The IFRS note blocks about (other) financial liabilities and borrowings —
`jpigp_cor:NotesOtherFinancialLiabilities…IFRSTextBlock`,
`NotesOtherFinancialAssetsAndOtherFinancialLiabilities…`, `NotesBorrowings…`,
`NotesBondsAndBorrowings…` and filer-named variants (Santen's
`NotesFinancialLiabilitiesNonCurrentAndOtherFinancialLiabilitiesCurrent…`) —
from the t1 package's XBRL instance, which holds each block whole. Every
table in them is read on its current-year column, found from its header
(当連結会計年度, or the fiscal year-end date); a table whose header names no
current year is not read.

What this keeps
---------------
Each table that ties to the balance sheet — its 合計, or its current and
non-current subtotals, equal (to the display unit) a liability line printed
on the consolidated face — with every item row: label as filed, kind and
value in yen. Kinds: `debt` (借入金, 社債, コマーシャル・ペーパー),
`interest_bearing` (有利子負債, which some filers use with leases inside),
`lease`, `other`. A table is cut into sections at each 合計 and each section
ties on its own; a section listing assets never ties. A table that ties to nothing on the face (a maturity
schedule, a list of bond issues, a movement table) is not stored: its rows
are a second view of numbers a tied table already holds, and adding them
would count a bond twice.

Gate
----
A tied table's item rows must add up to the face lines it ties to, within
rounding to the display unit. A table that ties but whose rows do not add up
(a parent row and its children both printed, a row read in the wrong column)
is dropped and the filing is `partial` with the numbers. `clean` means at
least one table tied and added up. `none` means the filing has no such note.
Nothing is recomputed: values are the filer's, scaled by the stated unit.

The net-cash rule that uses this (fin_metrics.cash_position) takes the debt
only when every catch-all financial-liability line on the face is covered by
a clean tied table, so a half-itemised balance sheet stays withheld.

Usage (from observatory/equity/):
    ../.venv/bin/python debt_note_extract.py --source s3 --sec-codes 9983,6471
    ../.venv/bin/python debt_note_extract.py --source s3 --new-only   # nightly
"""
import argparse
import hashlib
import html
import io
import re
import sys
import zipfile
from collections import defaultdict
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, compact, DB_PATH,
                     incremental_window, record_run,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor, sec_code_of)
from facility_extract import read_t1
from seg_extract import norm, sequence, to_num, unit_of, year_marker

PARSER_VERSION = "debt-1"
EXTRACTOR = "debt-notes"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eq_debt_note_filings (
    doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
    filer_name VARCHAR, period_end DATE, filed_date DATE, sha256_t1 VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR,
    source VARCHAR, note_elements VARCHAR, tied_lines VARCHAR, debt_yen DOUBLE,
    debt_rows VARCHAR, tables_read INTEGER, tables_tied INTEGER);
CREATE TABLE IF NOT EXISTS eq_debt_note_rows (
    doc_id VARCHAR, table_no INTEGER, ord INTEGER, label_ja VARCHAR,
    kind VARCHAR, value_yen DOUBLE, tied_lines VARCHAR);
"""
FILINGS_COLS = 17

NOTE_RE = re.compile(
    r"<([A-Za-z0-9_-]+:Notes[A-Za-z]*(?:FinancialLiabilit|Borrowing|BondsAndBorrowing)"
    r"[A-Za-z]*IFRSTextBlock)\b[^>]*>(.*?)</\1>", re.S)
# Fair-value and risk notes print borrowings again, at fair value or by
# maturity: a second view, never the itemisation of a balance-sheet line.
NOTE_SKIP = re.compile(r"FairValue|RiskManagement|FinancialInstruments|Maturity")
# IAS 7 makes every IFRS filer reconcile the liabilities its financing cash
# flows come from. One that lists leases and nothing else has no borrowings —
# NS Solutions and M&A Capital Partners say so only here.
RECON_RE = re.compile(
    r"<([A-Za-z0-9_-]+:NotesChangesInLiabilitiesFromFinancingActivities[A-Za-z]*TextBlock)"
    r"\b[^>]*>(.*?)</\1>", re.S)
PERIOD_END_RE = re.compile(r"<jpdei_cor:CurrentFiscalYearEndDateDEI[^>]*>([^<]*)<")

DEBT_LABEL = re.compile(u"借入|社債|コマーシャル|ＣＰ|^CP|ローン")
IB_LABEL = re.compile(u"有利子負債")
LEASE_LABEL = re.compile(u"リース")
NOT_DEBT_LABEL = re.compile(u"預り|デリバティブ|未払|保証|利息|返済予定額|償還予定額")
TOTAL_LABEL = re.compile(u"^(合計|計|総計)$|^合計|合計$")
SUBTOTAL_LABEL = re.compile(u"^(流動|非流動|短期|長期)(負債|金融負債)?(合計|計)?$|"
                            u"金融負債[(（](流動|非流動)[)）]$|合計[（(]|短期金融負債|長期金融負債")
SUBNOTE = re.compile(u"^(うち|内、|内訳|\\(うち|（うち)")
DATE_RE = re.compile(u"(\\d{4})年\\s*(\\d{1,2})月\\s*(\\d{1,2})日")
# A table's 合計 may equal several face lines together (BBS itemises its other
# financial liabilities and its leases in one table). Up to this many lines.
MAX_TIE_LINES = 4


def label_kind(label):
    lab = norm(label)
    if SUBTOTAL_LABEL.search(lab):
        return "subtotal"
    if TOTAL_LABEL.search(lab):
        return "total"
    if LEASE_LABEL.search(lab):
        return "lease"
    if NOT_DEBT_LABEL.search(lab):
        return "other"
    if IB_LABEL.search(lab):
        return "interest_bearing"
    if DEBT_LABEL.search(lab):
        return "debt"
    return "other"


def note_blocks(t1_blob):
    """(period_end, [(element, block_html)], [reconciliation block_html])
    from the XBRL instance, which holds each block whole."""
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        names = [n for n in z.namelist() if "PublicDoc" in n and n.endswith(".xbrl")]
        if not names:
            raise ValueError("no XBRL instance in t1 package")
        x = z.read(names[0]).decode("utf-8", "replace")
    m = PERIOD_END_RE.search(x)
    period_end = norm(m.group(1)) if m else None
    blocks = [(m.group(1), html.unescape(m.group(2))) for m in NOTE_RE.finditer(x)
              if not NOTE_SKIP.search(m.group(1))]
    recon = [html.unescape(m.group(2)) for m in RECON_RE.finditer(x)]
    return period_end, blocks, recon


def _is_current(header, period_end):
    """True / False for a column header naming the current / another year,
    None when it names no year."""
    t = norm(header)
    dates = ["%04d-%02d-%02d" % tuple(int(g) for g in d) for d in DATE_RE.findall(t)]
    if dates and period_end:
        return period_end in dates
    y = year_marker(t)
    if y is not None:
        return y == 0
    return None


def _first_numeric(cells):
    return next((i for i, row in enumerate(cells)
                 if any(to_num(c) is not None for c in row[1:])), None)


def current_column(cells, period_end):
    """Index of the current-year column, or None. The header is every row
    above the first one carrying a number; a colspan header covers several
    columns, and the one that holds numbers in the body is taken."""
    first = _first_numeric(cells)
    if not first:
        return None
    width = max(len(r) for r in cells)
    marks = {}
    for c in range(1, width):
        head = " ".join(cells[r][c] for r in range(first) if c < len(cells[r]))
        cur = _is_current(head, period_end)
        if cur is not None:
            marks[c] = cur
    cur_cols = [c for c, v in marks.items() if v]
    if not cur_cols:
        return None
    filled = {c: sum(1 for row in cells[first:] if c < len(row) and to_num(row[c]) is not None)
              for c in cur_cols}
    best = max(cur_cols, key=lambda c: (filled[c], -c))
    return best if filled[best] else None


def logical_tables(tables):
    """[(cells, unit)] with a header-less piece joined to the table before it:
    some filers cut one table in two (NSK's non-current half, TS Tech's
    current / non-current split) and print the header once."""
    out = []
    for cells, ctx in tables:
        width = max(len(r) for r in cells)
        if out and _first_numeric(cells) == 0 and max(len(r) for r in out[-1][0]) == width:
            out[-1][0].extend(cells)
            continue
        out.append([list(cells), ctx.get("unit")])
    return out


def read_table(cells, unit, period_end):
    """([(label, kind, value_yen)], unit) for one table on its current-year
    column, or (None, reason)."""
    col = current_column(cells, period_end)
    if not unit:
        # a caption row inside the table: ['(単位:百万円)', '(単位:百万円)', …]
        unit = next((unit_of(" ".join(r)) for r in cells[:3] if unit_of(" ".join(r))), None)
    if not unit:
        return None, "no unit stated"
    if col is None:
        return None, "no current-year column"
    rows = []
    for row in cells[_first_numeric(cells):]:
        label = norm(row[0]) if row else ""
        if not label or SUBNOTE.match(label) or col >= len(row):
            continue
        v = to_num(row[col])
        rows.append((label, label_kind(label), None if v is None else v * unit))
    return rows, unit


def sections(rows, unit):
    """A table cut at each 合計: [{"items", "total", "subs"}].

    Nikon prints its current and non-current borrowings as two sections of
    one table, each closed by its own 合計. A section whose 合計 repeats the
    one before it re-splits that total (current / non-current: TS Tech,
    Globe-ing), and a closing run of subtotals after a 合計 does the same
    (Fast Retailing): both lend that section their subtotals and are never
    read as rows of their own, which would count every yen twice."""
    parts, cur = [], []
    for r in rows:
        cur.append(r)
        if r[1] == "total" and r[2] is not None:
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)
    out = []
    for part in parts:
        total = part[-1][2] if part[-1][1] == "total" else None
        subs = [v for _, k, v in part if k == "subtotal" and v is not None]
        items = [r for r in part if r[1] not in ("total", "subtotal")]
        prev = out[-1] if out else None
        resplit = prev is not None and prev["total"] is not None and (
            (total is not None and abs(total - prev["total"]) < 1)
            or (total is None and subs and not any(v for _, _, v in items)
                and abs(sum(subs) - prev["total"]) <= 2 * unit * len(subs)))
        if resplit:
            prev["subs"] = prev["subs"] or subs
            continue
        out.append({"items": items, "total": total, "subs": subs})
    return out


ASSET_LABEL = re.compile(u"資産|現金|預金|有価証券|受取|売掛|棚卸|土地|建物")


def tie(section, face, unit):
    """The face lines a section itemises: its subtotals each equal to a
    distinct line, its 合計 equal to one line, or its 合計 equal to a unique
    set of lines that includes a financial-liability line. [] when it ties to
    nothing — and always for a section listing assets (a table of collateral
    can equal a borrowing line to the yen: Helios's ¥450m)."""
    if any(ASSET_LABEL.search(l) for l, _, v in section["items"] if v is not None):
        return []
    tol = 2 * unit
    lines = [(el, v) for el, v in face.items() if v]

    def match(value, used):
        hits = [el for el, v in lines if el not in used and abs(v - value) <= tol]
        return hits[0] if len(hits) == 1 else None

    total, subs = section["total"], section["subs"]
    if subs:
        used = []
        for v in subs:
            el = match(v, used)
            if el is None:
                break
            used.append(el)
        else:
            if total is None or abs(sum(subs) - total) <= tol * len(subs):
                return used
    if total is None:
        return []
    el = match(total, [])
    if el:
        return [el]
    sets = [list(c) for n in range(2, MAX_TIE_LINES + 1) for c in combinations(lines, n)
            if any("FinancialLiabilit" in e for e, _ in c)
            and abs(sum(v for _, v in c) - total) <= tol * n]
    return [e for e, _ in sets[0]] if len(sets) == 1 else []


def lease_only(recon_blocks):
    """True when a financing-activities reconciliation names leases and no
    borrowing of any kind; None when there is no reconciliation."""
    if not recon_blocks:
        return None
    labels = [norm(c) for b in recon_blocks for cells, _ in sequence(b)[0] for r in cells for c in r]
    if any(DEBT_LABEL.search(l) or IB_LABEL.search(l) for l in labels):
        return False
    return any(LEASE_LABEL.search(l) for l in labels)


def read_filing(t1_blob, face):
    """Parse one filing against its consolidated face.

    face: {element: current-year yen} for the consolidated balance-sheet
    liability lines. Returns a dict: status, detail, source, elements,
    tied_lines, debt_yen, debt_rows, tables_read, tables_tied, rows
    [(table_no, ord, label, kind, value_yen, tied)]."""
    period_end, blocks, recon = note_blocks(t1_blob)
    only_leases = lease_only(recon)
    out = {"status": "none", "detail": None, "source": None,
           "elements": ",".join(e for e, _ in blocks) or None,
           "tied_lines": None, "debt_yen": None, "debt_rows": None,
           "tables_read": 0, "tables_tied": 0, "rows": []}
    problems, covered, table_no = [], [], 0
    debt, debt_labels, debt_seen = 0.0, [], False
    for element, block in blocks:
        for cells, unit in logical_tables(sequence(block)[0]):
            rows, unit = read_table(cells, unit, period_end)
            if rows is None:
                continue
            out["tables_read"] += 1
            debt_seen = debt_seen or any(k in ("debt", "interest_bearing") and v
                                         for _, k, v in rows)
            for sec in sections(rows, unit):
                lines = tie(sec, face, unit)
                if not lines or set(lines) & set(covered):
                    continue                # a second view of a line already read
                tied_total = sum(face[el] for el in lines)
                got = sum(v for _, _, v in sec["items"] if v is not None)
                if abs(got - tied_total) > unit * (len(sec["items"]) + 2):
                    problems.append("a table tied to %s but its rows add to %.0f, not %.0f"
                                    % ("+".join(l.split(":")[-1] for l in lines), got, tied_total))
                    continue
                table_no += 1
                out["tables_tied"] += 1
                covered.extend(lines)
                for i, (label, kind, v) in enumerate(sec["items"]):
                    out["rows"].append((table_no, i, label, kind, v, ",".join(lines)))
                    if kind in ("debt", "interest_bearing") and v is not None:
                        debt += v
                        debt_labels.append(label)
    if out["tables_tied"]:
        out["status"], out["source"] = "clean", "note"
        out["tied_lines"] = ",".join(covered)
        out["debt_yen"] = debt
        out["debt_rows"] = " + ".join(debt_labels) or None
        if problems:
            out["status"] = "partial"
        if only_leases and debt:
            out["status"] = "partial"
            problems.append("the financing-activities reconciliation lists only leases, "
                            "but the note shows borrowings")
    elif only_leases and not debt_seen:
        out["status"], out["source"], out["debt_yen"] = "clean", "reconciliation", 0.0
    else:
        out["status"] = "partial" if blocks or recon else "none"
        problems.append("no note table ties to a balance-sheet line" if blocks
                        else "no financial-liability or borrowing note")
    out["detail"] = "; ".join(problems) or None
    return out


FACE_SQL = """
    SELECT l.doc_id, l.element, f.value
    FROM eq_fin_lines l
    JOIN eq_fin_facts f ON f.doc_id = l.doc_id AND f.element = l.element AND f.basis = l.basis
         AND f.year_offset = 0 AND f.period_kind = 'instant'
    WHERE l.statement = 'bs' AND l.basis = 'consolidated' AND f.value IS NOT NULL
      AND regexp_matches(l.element, 'Liabilit|Borrowing|Bond|Loan|Debt|CommercialPaper')
      AND NOT regexp_matches(l.element, 'Total|Asset|Receivable|Abstract')
      AND l.element NOT IN ('jpigp_cor:LiabilitiesIFRS', 'jpigp_cor:LiabilitiesAndEquityIFRS')
"""


def ifrs_faces(con, doc_ids):
    """{doc_id: face} for the IFRS filings among doc_ids that the financials
    extractor has read; a filing it has not read yet is left for a later run."""
    names = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if "eq_fin_filings" not in names or not doc_ids:
        return {}
    ifrs = {r[0] for r in con.execute(
        "SELECT doc_id FROM eq_fin_filings WHERE accounting_standard = 'IFRS' "
        "AND status IN ('clean', 'partial')").fetchall()} & set(doc_ids)
    faces = {d: {} for d in ifrs}
    for d, el, v in con.execute(FACE_SQL).fetchall():
        if d in faces:
            faces[d][el] = v
    return faces


def latest_ifrs(db_path):
    """doc_ids of each company's latest IFRS annual report in eq_fin_filings."""
    try:
        con = duckdb.connect(db_path, read_only=True)
    except duckdb.Error:
        return []
    try:
        if not con.execute("SELECT count(*) FROM duckdb_tables() "
                           "WHERE table_name = 'eq_fin_filings'").fetchone()[0]:
            return []
        return [r[0] for r in con.execute("""
            SELECT doc_id FROM (
                SELECT doc_id, accounting_standard, row_number() OVER (
                    PARTITION BY sec_code ORDER BY period_end DESC, filed_date DESC, doc_id DESC) AS rn
                FROM eq_fin_filings
                WHERE sec_code IS NOT NULL AND status IN ('clean', 'partial'))
            WHERE rn = 1 AND accounting_standard = 'IFRS'""").fetchall()]
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs")
    ap.add_argument("--sec-codes", help="comma-separated 4-digit securities codes")
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--dump", action="store_true", help="print parsed rows per filing")
    ap.add_argument("--catch-up", type=int, default=CATCH_UP_DAYS, metavar="DAYS",
                    help="on a --new-only run, also read the DAYS deepest archive days "
                         "below this extractor's own floor (see extract.select_pending)")
    args = ap.parse_args()

    src = S3Source(args.workers) if args.source == "s3" else LocalSource()
    since, have = (incremental_window(args.db, EXTRACTOR, "eq_debt_note_filings")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have, args.catch_up if args.new_only else 0,
        recorded_floor(args.db, EXTRACTOR))
    # Every company's latest IFRS report, whatever its date: that is the one
    # net cash reads, so a cold start that takes only the newest slice still
    # settles every company on its first night. Free once read.
    for d in latest_ifrs(args.db):
        if d in filings and d not in have:
            pending.setdefault(d, filings[d])
    meta = src.list_metadata(days=None if since is None else {r["date"] for r in pending.values()})
    targets = [(d, rec, meta.get(d) or {}) for d, rec in sorted(pending.items())
               if annual(meta.get(d) or {}, rec)]
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.sec_codes:
        want = {c.strip()[:4] for c in args.sec_codes.split(",") if c.strip()}
        targets = [t for t in targets if (t[2].get("secCode") or "")[:4] in want]

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    faces = ifrs_faces(con, [t[0] for t in targets])
    targets = [t for t in targets if t[0] in faces]
    if args.limit:
        targets = targets[:args.limit]
    print("target IFRS filings: %d (source=%s)" % (len(targets), src.name))

    def fetch_and_parse(t):
        doc_id, rec, m = t
        try:
            t1 = read_t1(src, doc_id, rec["date"])
            return t, read_filing(t1, faces[doc_id]), hashlib.sha256(t1).hexdigest(), None
        except Exception as e:                                    # noqa: BLE001
            return t, None, None, "%s: %s" % (type(e).__name__, str(e)[:160])

    stats = defaultdict(int)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for done, fut in enumerate(as_completed([ex.submit(fetch_and_parse, t) for t in targets]), 1):
            (doc_id, rec, m), got, sha, err = fut.result()
            if done % 250 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"),
                    m.get("periodEnd") or None, rec["date"], sha, PARSER_VERSION]
            con.execute("DELETE FROM eq_debt_note_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_debt_note_rows WHERE doc_id = ?", [doc_id])
            if err:
                stats["failed"] += 1
                con.execute("INSERT INTO eq_debt_note_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                            base + ["failed", err] + [None] * (FILINGS_COLS - 10))
                continue
            stats[got["status"]] += 1
            con.execute("INSERT INTO eq_debt_note_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                        base + [got["status"], got["detail"], got["source"], got["elements"],
                                got["tied_lines"],
                                got["debt_yen"], got["debt_rows"], got["tables_read"],
                                got["tables_tied"]])
            if got["rows"]:
                con.executemany("INSERT INTO eq_debt_note_rows VALUES (?,?,?,?,?,?,?)",
                                [[doc_id] + list(r) for r in got["rows"]])
            if args.dump:
                print("\n--- %s %s %s status=%s debt=%s %s" % (
                    doc_id, base[2], (base[3] or "")[:24], got["status"],
                    None if got["debt_yen"] is None else "{:,.0f}".format(got["debt_yen"]),
                    got["detail"] or ""))
                for r in got["rows"]:
                    print("    t%d %-10s %-36s %18s  -> %s" % (
                        r[0], r[3], r[2][:36], "—" if r[4] is None else "{:,.0f}".format(r[4]), r[5]))

    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-10s %d" % (k, stats[k]))
    con.close()
    record_run(args.db, EXTRACTOR, through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
