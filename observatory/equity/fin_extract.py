# -*- coding: utf-8 -*-
"""Financial facts from annual securities reports (有価証券報告書) — every tagged
number, kept long, so the statements and the key-indicators panel are both
views over one table and neither needs a rebuild when the other grows.

What this reads
---------------
The same t5 CSV package (jpcrp030000-asr) the holdings and board extractors
open. Every fact in the XBRL instance is one row: element, context, value.
The balance sheet, income statement and cash-flow statement (taxonomy
jppfs_cor; jpigp_cor for IFRS filers), the filer's own five-year summary of
business results (主要な経営指標等の推移, jpcrp_cor:*SummaryOfBusinessResults)
and the filer-specific extension elements are all in it.

The full-XBRL package (t1) is read too, for one thing only: the filer's own
presentation linkbase, which says exactly which elements make up each
statement and in what order — the five-year summary, the consolidated
balance sheet, income statement, comprehensive income, changes in equity and
cash flows, and the parent-only set. That is what lets a statement be printed as filed rather
than reconstructed by guessing from element names.

What this keeps
---------------
Every numeric fact in a plain year context — CurrentYear / Prior1Year …
Prior4Year, Instant or Duration, consolidated or the _NonConsolidatedMember
twin. Row<N>Member contexts (the holdings, board and facilities tables, which
their own extractors own) and every other dimensional context (the
statement of changes in equity, segment breakdowns) are skipped for now.
Adding them later is a filter change, not a schema change.

Nothing is recomputed. Ratios arrive as the filer printed them (fractions in
unit `pure`), yen figures at full precision. "－" is a missing value and is
never stored as zero.

Gate
----
The one identity every balance sheet has to satisfy: assets = liabilities +
net assets, checked per basis (consolidated, parent) on the current-year
instant. A filing that tags the three totals and fails it, or whose summary
prints net assets above total assets, is `partial` with the reason; a filing
that tags no balance-sheet totals at all (US GAAP filers tag their statements
as text) is `partial` too, so a cross-section never silently includes a
company whose numbers were never checked. `failed` means the package could
not be read.

Usage (from observatory/equity/):
    ../.venv/bin/python fin_extract.py --limit 20                # smoke test
    ../.venv/bin/python fin_extract.py --sec-codes 7203,8306,6758
    ../.venv/bin/python fin_extract.py --all --source s3 --new-only   # nightly
"""
import argparse
import csv
import hashlib
import io
import os
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact, DB_PATH,
                     incremental_window, record_run, seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)

import taxonomy_labels

PARSER_VERSION = "fin-1"
EXTRACTOR = "financials"

# Plain year contexts only. Group 1 = Current | Prior<N>, 2 = Instant |
# Duration, 3 = the parent-only marker.
CTX_RE = re.compile(r"^(Current|Prior([1-9]))Year(Instant|Duration)"
                    r"(_NonConsolidatedMember)?$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

# Balance-sheet identity, per taxonomy. (assets, liabilities, equity)
BS_TOTALS = [
    ("jppfs_cor:Assets", "jppfs_cor:Liabilities", "jppfs_cor:NetAssets"),
    ("jpigp_cor:AssetsIFRS", "jpigp_cor:LiabilitiesIFRS", "jpigp_cor:EquityIFRS"),
]
# One basis point of total assets: the slack that per-line rounding to the
# filer's display unit can legitimately leave in the printed totals.
BS_TOLERANCE = 1e-4

SUMMARY = "SummaryOfBusinessResults"
STANDARD_DEI = "jpdei_cor:AccountingStandardsDEI"
CONSOLIDATED_DEI = "jpdei_cor:WhetherConsolidatedFinancialStatementsArePreparedDEI"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eq_fin_filings (
    doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
    filer_name VARCHAR, period_end DATE, filed_date DATE, sha256 VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR,
    accounting_standard VARCHAR, consolidated BOOLEAN, facts INTEGER,
    bs_consolidated VARCHAR, bs_parent VARCHAR,
    total_assets_yen DOUBLE, net_assets_yen DOUBLE,
    statements VARCHAR, sha256_t1 VARCHAR);
CREATE TABLE IF NOT EXISTS eq_fin_facts (
    doc_id VARCHAR, ord INTEGER, element VARCHAR, context VARCHAR,
    year_offset INTEGER, period_kind VARCHAR, basis VARCHAR, scope_ja VARCHAR,
    unit VARCHAR, value DOUBLE);
CREATE TABLE IF NOT EXISTS eq_fin_lines (
    doc_id VARCHAR, statement VARCHAR, basis VARCHAR, ord INTEGER,
    element VARCHAR, depth INTEGER, label_role VARCHAR);
CREATE TABLE IF NOT EXISTS eq_fin_elements (
    element VARCHAR PRIMARY KEY, namespace VARCHAR, label_ja VARCHAR,
    label_en VARCHAR);
"""

FILINGS_COLS = 19

# Presentation roles -> statement codes. The role name carries the basis
# ("Consolidated" or not), the standard ("IFRS" suffix) and the cash-flow
# method ("-indirect"); none of those change what the statement is.
STATEMENTS = [
    ("StatementOfFinancialPosition", "bs"),
    ("BalanceSheet", "bs"),
    ("StatementOfIncomeAndComprehensiveIncome", "pl"),
    ("StatementOfProfitOrLossAndOtherComprehensiveIncome", "pl"),
    ("StatementOfProfitOrLoss", "pl"),
    ("StatementOfIncome", "pl"),
    ("StatementOfComprehensiveIncome", "ci"),
    ("StatementOfChangesInEquity", "ss"),
    ("StatementOfChangesInNetAssets", "ss"),
    ("StatementOfCashFlows", "cf"),
]
LINK_RE = re.compile(r'<link:presentationLink\b[^>]*xlink:role="([^"]+)"[^>]*>(.*?)'
                     r'</link:presentationLink>', re.S)
LOC_RE = re.compile(r'<link:loc\b[^>]*xlink:href="[^"#]*#([^"]+)"[^>]*xlink:label="([^"]+)"')
ARC_RE = re.compile(r'<link:presentationArc\b([^>]*)/?>')
ATTR_RE = re.compile(r'([A-Za-z:]+)="([^"]*)"')
# Hypercube scaffolding in a presentation tree: never a line on the page.
SCAFFOLD_RE = re.compile(r"(Heading|Table|Axis|Member|Domain|LineItems)$")


def statement_of(role):
    """(statement, basis) for a presentation role, or None for notes and
    everything that is not a primary statement."""
    name = role.rsplit("/", 1)[-1]
    if not name.startswith("rol_") or "Notes" in name:
        return None
    name = re.sub(r"-\d+$", "", name[4:])
    # The filer's own five-year summary (主要な経営指標等の推移) is a role too:
    # one for the group, one for the reporting company alone.
    if name == "BusinessResultsOfGroup":
        return "summary", "consolidated"
    if name == "BusinessResultsOfReportingCompany":
        return "summary", "parent"
    basis = "consolidated" if name.startswith("Consolidated") else "parent"
    core = name.replace("Consolidated", "").replace("IFRS", "").replace("USGAAP", "")
    core = re.sub(r"-(indirect|direct)$", "", core)
    for mark, code in STATEMENTS:
        if core == mark:
            return code, basis
    return None


def element_of(fragment):
    """'jppfs_cor_Assets' -> 'jppfs_cor:Assets'; extension ids keep their
    long prefix ('jpcrp030000-asr_E01570-000_Foo' -> '…-000:Foo')."""
    head, _, local = fragment.rpartition("_")
    return head + ":" + local if head else fragment


def fill_standard_english(con):
    u"""English labels for the standard taxonomy elements.

    A filing's lab-en linkbase names only the filer's own extension elements,
    so every element the taxonomy defines -- which is every element the
    statements are actually made of -- arrived with no English label at all.
    The FSA publishes those labels; `taxonomy_labels.py` keeps a copy.

    Fills gaps only. A label a filer supplied for its own extension element is
    never overwritten, and neither is one already filled by an earlier run.
    """
    labels = taxonomy_labels.load()
    if not labels:
        print("no taxonomy_en.csv; standard elements keep their Japanese label")
        return
    gaps = [r[0] for r in con.execute(
        "SELECT element FROM eq_fin_elements "
        "WHERE label_en IS NULL OR trim(label_en) = ''").fetchall()]
    rows = [(labels[e], e) for e in gaps if e in labels]
    if rows:
        con.executemany(
            "UPDATE eq_fin_elements SET label_en = ? WHERE element = ?", rows)
    print("standard element labels: %d filled, %d still unlabelled"
          % (len(rows), len(gaps) - len(rows)))


def parse_presentation(blob):
    """Statement line items in display order, from the t1 package's
    presentation linkbase: [(statement, basis, ord, element, depth,
    label_role)]. Also the filer's English labels for its extension
    elements, from its own lab-en file."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        pres = [n for n in names if n.endswith("_pre.xml") and "PublicDoc" in n]
        if not pres:
            raise ValueError("no presentation linkbase in t1 package")
        text = z.read(pres[0]).decode("utf-8")
        lab_en = [n for n in names if n.endswith("_lab-en.xml") and "PublicDoc" in n]
        en_text = z.read(lab_en[0]).decode("utf-8") if lab_en else ""

    lines = []
    for role, body in LINK_RE.findall(text):
        st = statement_of(role)
        if not st:
            continue
        statement, basis = st
        by_label = {lbl: element_of(frag) for frag, lbl in LOC_RE.findall(body)}
        children = defaultdict(list)
        has_parent = set()
        for attrs in ARC_RE.findall(body):
            a = dict(ATTR_RE.findall(attrs))
            src, dst = a.get("xlink:from"), a.get("xlink:to")
            if not src or not dst:
                continue
            try:
                order = float(a.get("order", "0"))
            except ValueError:
                order = 0.0
            role_kind = (a.get("preferredLabel") or "").rsplit("/", 1)[-1] or None
            children[src].append((order, dst, role_kind))
            has_parent.add(dst)
        roots = [n for n in children if n not in has_parent]
        ord_no = [0]

        def walk(node, depth, kind):
            el = by_label.get(node, element_of(node))
            scaffold = bool(SCAFFOLD_RE.search(el))
            if not scaffold:
                lines.append((statement, basis, ord_no[0], el, depth, kind))
                ord_no[0] += 1
            # Scaffolding is skipped but not descended past: a child of the
            # LineItems node is a top-level section of the statement.
            for _, child, k in sorted(children.get(node, []), key=lambda c: c[0]):
                walk(child, depth if scaffold else depth + 1, k)
        for root in sorted(roots):
            walk(root, 0, None)

    labels_en = {}
    if en_text:
        # loc (href#id -> loc label) -> labelArc (loc label -> label label)
        # -> label resource (label label -> text); standard role only.
        locs = {lbl: element_of(frag) for frag, lbl in LOC_RE.findall(en_text)}
        res = {}
        for attrs, txt in re.findall(r'<link:label\b([^>]*)>([^<]*)</link:label>', en_text):
            a = dict(ATTR_RE.findall(attrs))
            if a.get("xlink:role", "").endswith("/label"):
                res[a.get("xlink:label")] = txt.strip()
        for attrs in re.findall(r'<link:labelArc\b([^>]*)/?>', en_text):
            a = dict(ATTR_RE.findall(attrs))
            el, txt = locs.get(a.get("xlink:from")), res.get(a.get("xlink:to"))
            if el and txt:
                labels_en[el] = txt
    return lines, labels_en


def read_t1(src, doc_id, date):
    """The full-XBRL package, from whichever archive the run reads."""
    if src.name == "s3":
        key = "docs/%s/%s_t1.zip" % (date, doc_id)
        return src.c.get_object(Bucket=src.bucket, Key=key)["Body"].read()
    from extract import ARCHIVE
    with open(os.path.join(ARCHIVE, "docs", date, doc_id + "_t1.zip"), "rb") as f:
        return f.read()


def parse_facts(blob):
    """(facts, labels, dei) for one t5 package.

    facts: list of (ord, element, context, year_offset, period_kind, basis,
    scope_ja, unit, value). labels: element -> Japanese label. dei: the two
    document-level flags this extractor reads."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        members = [m for m in z.namelist() if "jpcrp030000-asr" in m]
        if len(members) != 1:
            raise ValueError("expected 1 jpcrp030000-asr csv, got %d" % len(members))
        text = z.read(members[0]).decode("utf-16")
    body = list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]

    facts = []
    labels = {}
    seen = set()
    dei = {"standard": None, "consolidated": None}
    for i, row in enumerate(body):
        if len(row) != 9:
            continue
        eid, item, ctx, relyr, cons, pit, unit_id, unit, val = row
        if eid == STANDARD_DEI:
            dei["standard"] = val.strip() or None
            continue
        if eid == CONSOLIDATED_DEI:
            dei["consolidated"] = val.strip().lower() == "true"
            continue
        m = CTX_RE.match(ctx)
        if not m:
            continue
        v = val.strip().replace(",", "")
        if not NUM_RE.match(v):
            continue                     # "－", blank, text: missing, never zero
        # The instance repeats a fact wherever the document prints it (net
        # sales in the income statement and again in the segment note); the
        # first occurrence is the statement's, and one row per fact is what
        # a join must see.
        if (eid, ctx) in seen:
            continue
        seen.add((eid, ctx))
        offset = 0 if m.group(1) == "Current" else -int(m.group(2))
        kind = "instant" if m.group(3) == "Instant" else "duration"
        basis = "parent" if m.group(4) else "consolidated"
        facts.append((i, eid, ctx, offset, kind, basis, cons or None,
                      unit_id or None, float(v)))
        if eid not in labels:
            labels[eid] = item
    return facts, labels, dei


def balance_check(facts):
    """{basis: 'ok' | 'off by <x>' | None} on the current-year instant."""
    cur = {}
    for _, eid, _, offset, kind, basis, _, _, value in facts:
        if offset == 0 and kind == "instant":
            cur[(basis, eid)] = value
    out = {}
    for basis in ("consolidated", "parent"):
        verdict = None
        for a, l, e in BS_TOTALS:
            if (basis, a) in cur and (basis, l) in cur and (basis, e) in cur:
                assets = cur[(basis, a)]
                gap = abs(assets - cur[(basis, l)] - cur[(basis, e)])
                if gap <= BS_TOLERANCE * abs(assets):
                    verdict = "ok"
                else:
                    verdict = "off by %.0f yen" % gap
                break
        out[basis] = verdict
    return out, cur


def summary_check(facts):
    """(total_assets, net_assets, ok) from the filer's own summary, current
    year, consolidated first — the cross-check the key-indicators panel
    relies on. ok is None when the summary does not carry both."""
    vals = {}
    for _, eid, _, offset, _, basis, _, _, value in facts:
        if offset != 0 or SUMMARY not in eid:
            continue
        local = eid.split(":")[-1]
        core = local.replace(SUMMARY, "").replace("IFRS", "").replace("USGAAP", "")
        if core == "TotalAssets":
            vals[(basis, "assets")] = value
        elif core in ("NetAssets", "EquityAttributableToOwnersOfParent",
                      "TotalEquity", "Equity"):
            vals.setdefault((basis, "equity"), value)
    for basis in ("consolidated", "parent"):
        a, e = vals.get((basis, "assets")), vals.get((basis, "equity"))
        if a is not None and e is not None:
            return a, e, e <= a
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every filer, not just listed")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs")
    ap.add_argument("--sec-codes", help="comma-separated 4-digit securities codes")
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

    since, have = (incremental_window(args.db, EXTRACTOR, "eq_fin_filings")
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
    if args.sec_codes:
        want = {c.strip()[:4] for c in args.sec_codes.split(",") if c.strip()}
        targets = [t for t in targets if (t[2].get("secCode") or "")[:4] in want]
    if args.limit:
        targets = targets[:args.limit]
    print("target filings: %d (source=%s)" % (len(targets), src.name))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    known = {r[0] for r in con.execute("SELECT element FROM eq_fin_elements").fetchall()}
    new_labels = {}

    def fetch_and_parse(t):
        doc_id, rec, m = t
        try:
            blob = src.read_zip(doc_id, rec["date"])
            sha = hashlib.sha256(blob).hexdigest()
            facts, labels, dei = parse_facts(blob)
            # The presentation linkbase is a want, not a need: a filing whose
            # t1 package is missing or unreadable still yields its facts and
            # says so in `statements`.
            try:
                t1 = read_t1(src, doc_id, rec["date"])
                lines, labels_en = parse_presentation(t1)
                sha_t1 = hashlib.sha256(t1).hexdigest()
            except Exception as e:                                # noqa: BLE001
                lines, labels_en, sha_t1 = [], {}, None
                dei["t1_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
            return t, (facts, labels, dei, lines, labels_en, sha_t1), sha, None
        except Exception as e:                                    # noqa: BLE001
            return t, None, None, "%s: %s" % (type(e).__name__, str(e)[:160])

    stats = defaultdict(int)
    n_facts = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha, err = fut.result()
            done += 1
            if done % 250 == 0:
                print("  %d/%d filings, %d facts" % (done, len(targets), n_facts))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"),
                    sec_code_of(m),
                    rec.get("filer") or m.get("filerName"),
                    m.get("periodEnd") or None, rec["date"], sha, PARSER_VERSION]
            con.execute("DELETE FROM eq_fin_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_fin_facts WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_fin_lines WHERE doc_id = ?", [doc_id])
            if err:
                stats["failed"] += 1
                con.execute("INSERT INTO eq_fin_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                            base + ["failed", err] + [None] * (FILINGS_COLS - 10))
                continue
            facts, labels, dei, lines, labels_en, sha_t1 = parsed
            found = sorted({(b, st) for st, b, _, _, _, _ in lines})
            statements = ";".join("%s:%s" % (b, ",".join(st for bb, st in found if bb == b))
                                  for b in ("consolidated", "parent")
                                  if any(bb == b for bb, _ in found)) or None
            checks, cur = balance_check(facts)
            s_assets, s_equity, s_ok = summary_check(facts)
            problems = []
            for basis in ("consolidated", "parent"):
                if checks[basis] and checks[basis] != "ok":
                    problems.append("%s balance sheet %s" % (basis, checks[basis]))
            if not any(checks.values()):
                problems.append("no balance-sheet totals tagged")
            if s_ok is False:
                problems.append("summary net assets exceed total assets")
            if not facts:
                problems.append("no numeric facts in year contexts")
            if dei.get("t1_error"):
                problems.append("presentation linkbase unread (%s)" % dei["t1_error"])
            elif not lines:
                problems.append("no primary statements in presentation linkbase")
            status = "partial" if problems else "clean"
            stats[status] += 1
            con.execute("INSERT INTO eq_fin_filings VALUES (%s)" % ",".join(["?"] * FILINGS_COLS),
                        base + [status, "; ".join(problems) or None,
                                dei["standard"], dei["consolidated"], len(facts),
                                checks["consolidated"], checks["parent"],
                                s_assets, s_equity, statements, sha_t1])
            if lines:
                con.executemany(
                    "INSERT INTO eq_fin_lines VALUES (?,?,?,?,?,?,?)",
                    [(doc_id,) + ln for ln in lines])
            if facts:
                con.executemany(
                    "INSERT INTO eq_fin_facts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(doc_id,) + f for f in facts])
                n_facts += len(facts)
            for eid, label in labels.items():
                if eid not in known and eid not in new_labels:
                    new_labels[eid] = (label, labels_en.get(eid))

    if new_labels:
        con.executemany("INSERT INTO eq_fin_elements VALUES (?,?,?,?)",
                        [(eid, eid.split(":")[0], label, en)
                         for eid, (label, en) in new_labels.items()])
    fill_standard_english(con)
    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-10s %d" % (k, stats[k]))
    print("facts written: %d; elements known: %d"
          % (n_facts, len(known) + len(new_labels)))
    con.close()
    record_run(args.db, EXTRACTOR, through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
