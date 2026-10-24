# -*- coding: utf-8 -*-
"""Business-risk extractor (事業等のリスク), parser risk-1.

What this reads
---------------
Item 2-3 of the annual securities report (有価証券報告書, 第2【事業の状況】
3【事業等のリスク】): the risks management says could materially affect the
business, in the company's own words. Every filer tags the section as ONE
text block, ``jpcrp_cor:BusinessRisksTextBlock``. The .xbrl instance is read
rather than the inline .htm, because the .htm may split a long block into
``ix:continuation`` fragments and the instance holds it whole.

What this keeps
---------------
The section's text, verbatim, one line per paragraph, and the same text cut
into the filer's own numbered items — (1) 為替変動リスク, ① 原材料価格 … —
so one risk can be followed across years. Nothing is summarised, classified or
translated: the text is the filing's, and a reader who wants the original
layout follows doc_id to EDINET.

How the items are cut
---------------------
Filers number their risks in one of a handful of styles: (1), ①, 1., (ア),
(a), or a bare number and a space. The first line numbered 1 in any style sets
the top level; the first different style numbered 1 inside a top-level item
sets the second level. A line opens an item only if it carries the NEXT
number in its level's sequence (1, 2, 3 …), so a body paragraph that happens
to begin "(1)" in a nested list never splits an item. Text before the first
item is the preamble (usually the "forward-looking statements" caveat, often
the risk-management framework too).

A section with no numbered items is tried once more for unnumbered headings:
a short line in brackets, （競合について） or 【為替変動】 — whichever style
occurs most, if it occurs at least twice and never repeats a heading.
``split_by`` records which rule cut the filing ('number' or 'heading').

Gate (nothing may be lost): the preamble and the items, joined, must equal the
section's full text line for line. A filing that fails is ``partial`` and
stores the full text only. A filing with text but no items by either rule is
``unsplit`` — the text is kept whole and is still searchable.

Tables inside the section (some filers grade risks by likelihood and impact
in a table) are flattened to one line per row, cells joined by " | ".

Usage:
    python risk_extract.py                      # local archive
    python risk_extract.py --source s3 --workers 12
    python risk_extract.py --docs S100YH8W      # subset
Python 3.9.
"""
import argparse
import hashlib
import html
import io
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact,
                     DB_PATH, incremental_window, record_run,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor, sec_code_of)
from facility_extract import read_t1

PARSER_VERSION = "risk-1"
EXTRACTOR = "business-risks"

BLOCK_RE = re.compile(
    r"<jpcrp_cor:BusinessRisksTextBlock\b[^>]*>(.*?)</jpcrp_cor:BusinessRisksTextBlock>",
    re.S)
# Block-level boundaries become line breaks; a table cell becomes a separator.
BREAK_RE = re.compile(r"</?(p|div|h[1-6]|li|ul|ol|table|tbody|thead|br)\b[^>]*>", re.I)
ROW_END_RE = re.compile(r"</tr\s*>", re.I)
CELL_END_RE = re.compile(r"</t[dh]\s*>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(u"[ \t\r 　]+")
TITLE_RE = re.compile(u"事業等のリスク\\s*】?\\s*$")

# Full-width digits, letters and brackets read as ASCII for numbering only;
# the stored text is never normalised.
_FW = dict((0xFF10 + i, 0x30 + i) for i in range(10))
_FW.update((0xFF41 + i, 0x61 + i) for i in range(26))
_FW.update({0xFF08: 0x28, 0xFF09: 0x29, 0xFF0E: 0x2E})

KANA = u"アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
CIRCLED = u"①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟"

# (style, pattern on the ASCII-folded line, number of the match). Order is
# only a tie-break; a line matches at most one style in practice.
STYLES = (
    ("paren", re.compile(r"^\(\s*(\d{1,2})\s*\)"), int),
    ("circled", re.compile(u"^([%s])" % CIRCLED), lambda s: CIRCLED.index(s) + 1),
    ("dot", re.compile(r"^(\d{1,2})\.(?!\d)"), int),
    ("kana", re.compile(u"^\\(\\s*([%s])\\s*\\)" % KANA), lambda s: KANA.index(s) + 1),
    ("alpha", re.compile(r"^\(\s*([a-z])\s*\)"), lambda s: ord(s) - 96),
    ("bare", re.compile(r"^(\d{1,2})\s+(?=\D)"), int),
)

# A numbered line up to this long, not ending in 。, is a heading; a longer
# one is a heading with its first paragraph run on (kept whole, flagged).
HEADING_MAX = 100

# Unnumbered headings, tried only when a section has no numbered items: a
# short line wrapped in brackets — （競合について）, 【為替変動】. A line counts
# only if it is short and does not end in 。; a style must occur at least
# twice, and never with the same text twice — one filer labels every risk
# （リスク） and every response （対応策）, which are labels, not headings.
# Bullets (・ ● ■) are not headings: filers use them for lists inside a risk
# as often as for the risks themselves (measured on FY2026 filings).
UNNUMBERED = (
    ("bracketed", re.compile(u"^[（(][^（）()]{2,60}[）)]$")),
    ("lenticular", re.compile(u"^【[^【】]{2,60}】$")),
)
UNNUMBERED_MAX = 80


def block_html(t1_blob):
    """The section's HTML from the XBRL instance, or None when untagged."""
    with zipfile.ZipFile(io.BytesIO(t1_blob)) as z:
        names = [n for n in z.namelist()
                 if "PublicDoc" in n and n.endswith(".xbrl")]
        if not names:
            raise ValueError("no XBRL instance in t1 package")
        x = z.read(names[0]).decode("utf-8", "replace")
    best = None
    for m in BLOCK_RE.finditer(x):
        if best is None or len(m.group(1)) > len(best):
            best = m.group(1)
    return html.unescape(best) if best is not None else None


def lines_of(block):
    """The section as a list of non-empty lines, verbatim but for spacing."""
    s = ROW_END_RE.sub("\n", block)
    s = CELL_END_RE.sub(" | ", s)
    s = BREAK_RE.sub("\n", s)
    s = html.unescape(TAG_RE.sub("", s))
    out = []
    for raw in s.split("\n"):
        line = SPACE_RE.sub(" ", raw).strip()
        line = re.sub(r"(\s*\|\s*)+$", "", line).strip()      # trailing cell bars
        if line and line != "|":
            out.append(line)
    # Drop the section's own title line (３【事業等のリスク】).
    if out and TITLE_RE.search(out[0]) and len(out[0]) < 30:
        out = out[1:]
    return out


def numbering(line):
    """(style, number, label) if the line opens with a list number, else None."""
    folded = line.translate(_FW)
    for style, rx, num in STYLES:
        m = rx.match(folded)
        if m:
            return style, num(m.group(1)), line[:m.end()].strip()
    return None


def split_items(lines):
    """(preamble_lines, items). Each item: dict(level, label, heading,
    heading_inline, lines, parent). Every input line lands exactly once."""
    top = sub = None
    next_top = 1
    next_sub = 1
    preamble, items = [], []
    for line in lines:
        n = numbering(line)
        opened = None
        if n:
            style, number, label = n
            if top is None and number == 1:
                top = style
            if style == top and number == next_top:
                opened = 1
            elif items and style != top and (
                    (sub is None and number == 1) or
                    (style == sub and number == next_sub)):
                opened = 2
        if opened == 1:
            items.append({"level": 1, "label": n[2], "lines": [line]})
            next_top += 1
            next_sub = 1
        elif opened == 2:
            sub = n[0]
            items.append({"level": 2, "label": n[2], "lines": [line]})
            next_sub += 1
        elif items:
            items[-1]["lines"].append(line)
        else:
            preamble.append(line)
    return preamble, _finish(items)


def unnumbered_style(line):
    if len(line) > UNNUMBERED_MAX or line.endswith(u"。"):
        return None
    for style, rx in UNNUMBERED:
        if rx.match(line):
            return style
    return None


def split_unnumbered(lines):
    """(preamble_lines, items) cut at the most frequent unnumbered heading
    style, or ([...], []) when no style occurs twice."""
    seen = defaultdict(list)
    for line in lines:
        s = unnumbered_style(line)
        if s:
            seen[s].append(line)
    counts = dict((s, len(v)) for s, v in seen.items()
                  if len(v) >= 2 and len(set(v)) == len(v))
    if not counts:
        return lines, []
    style = max(counts, key=lambda s: counts[s])
    preamble, items = [], []
    for line in lines:
        if unnumbered_style(line) == style:
            items.append({"level": 1, "label": None, "lines": [line]})
        elif items:
            items[-1]["lines"].append(line)
        else:
            preamble.append(line)
    return preamble, _finish(items)


def _finish(items):
    parent = None
    for i, it in enumerate(items):
        if it["level"] == 1:
            parent = i
            it["parent"] = None
        else:
            it["parent"] = parent
        first = it["lines"][0]
        it["heading_inline"] = len(first) > HEADING_MAX or first.endswith(u"。")
        it["heading"] = first
    return items


SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS eq_risk_filings (
        doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
        filer_name VARCHAR, period_end DATE, filed_date DATE,
        sha256_t1 VARCHAR, parser_version VARCHAR,
        status VARCHAR, detail VARCHAR, split_by VARCHAR,
        n_chars INTEGER, n_items INTEGER, n_top_items INTEGER,
        -- full_text only where there are no items; otherwise the section is
        -- preamble + items in item_no order, and storing it twice would
        -- double the largest text table in the database.
        preamble VARCHAR, full_text VARCHAR);
    CREATE TABLE IF NOT EXISTS eq_risk_items (
        doc_id VARCHAR, item_no INTEGER, level INTEGER, parent_no INTEGER,
        label VARCHAR, heading VARCHAR, heading_inline BOOLEAN,
        body VARCHAR, n_chars INTEGER,
        PRIMARY KEY (doc_id, item_no));
"""


def parse(t1_blob):
    """-> (status, detail, split_by, lines, preamble, items)."""
    block = block_html(t1_blob)
    if block is None:
        return "no_block", None, None, [], [], []
    lines = lines_of(block)
    if not lines:
        return "empty", None, None, [], [], []
    split_by = "number"
    preamble, items = split_items(lines)
    if not items:
        split_by = "heading"
        preamble, items = split_unnumbered(lines)
    if not items:
        return "unsplit", None, None, lines, lines, []
    rebuilt = preamble + [l for it in items for l in it["lines"]]
    if rebuilt != lines:
        return "partial", "reconstruction_mismatch", split_by, lines, lines, []
    if sum(1 for it in items if it["level"] == 1) < 2:
        return "partial", "one_top_item", split_by, lines, preamble, items
    return "clean", None, split_by, lines, preamble, items


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

    since, have = (incremental_window(args.db, EXTRACTOR, "eq_risk_filings")
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
        # Listed today, or carrying a securities code on the filing itself —
        # so a company since delisted keeps its history and the record is
        # not a list of survivors. --all adds unlisted filers (bond issuers).
        if args.all or m.get("edinetCode") in listed or sec_code_of(m):
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
            return t, parse(t1), sha1
        except Exception as e:                                    # noqa: BLE001
            return t, ("failed", "%s: %s" % (type(e).__name__, str(e)[:160]),
                       None, [], [], []), sha1

    stats = defaultdict(int)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), (status, detail, split_by, lines, preamble, items), sha1 = fut.result()
            done += 1
            if done % 500 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            stats[status] += 1
            # Securities code only for a company that has one: a fund's or a
            # non-listed issuer's annual report is kept, but never keyed as a
            # company.
            full = "\n".join(lines) or None
            con.execute("DELETE FROM eq_risk_filings WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_risk_items WHERE doc_id = ?", [doc_id])
            con.execute(
                "INSERT INTO eq_risk_filings VALUES (%s)" % ",".join(["?"] * 16),
                [doc_id, m.get("edinetCode"), sec_code_of(m),
                 rec.get("filer") or m.get("filerName"),
                 m.get("periodEnd") or None, rec["date"], sha1, PARSER_VERSION,
                 status, detail, split_by, len(full) if full else None,
                 len(items), sum(1 for it in items if it["level"] == 1),
                 None if not items else ("\n".join(preamble) or None),
                 None if items else full])
            for i, it in enumerate(items):
                body = "\n".join(it["lines"][1:]) or None
                con.execute(
                    "INSERT INTO eq_risk_items VALUES (?,?,?,?,?,?,?,?,?)",
                    [doc_id, i, it["level"], it["parent"], it["label"],
                     it["heading"], it["heading_inline"], body,
                     sum(len(l) for l in it["lines"])])

    print("\nstatus counts:")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print("  %-16s %d" % (k, stats[k]))
    con.close()
    record_run(args.db, EXTRACTOR, through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)


if __name__ == "__main__":
    main()
