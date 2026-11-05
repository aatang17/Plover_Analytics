# -*- coding: utf-8 -*-
u"""The named policy-holding table read from a filing's HTML, for filers whose
XBRL tags too few of its rows.

WHY THIS EXISTS
---------------
extract.py reads the named 特定投資株式 rows from the type=5 CSV, which holds
only what the filer tagged. Almost every filer tags every row. A few tag a
handful: Sumitomo Mitsui Financial Group's FY2025 report (S100W0S7) tags 9
rows for its bank, with names cut in half ("三井物産" and "株式会社" as two
holdings), against the filing's own 785 listed issues worth ¥3.17tn. Every
other year it tags 56 to 59. Read as tagged, the bank's named holdings fell to
¥1.1tn in 2025 and rebounded a year later, which never happened. The full
table is in the filing's HTML (the honbun files of the type=1 package, which
the archive already holds for every annual report), so that is where the
fallback reads it.

THE RULE FOR "TOO FEW"
----------------------
The disclosure rules require each holding company's table to name every issue
above 1% of capital and at least the largest 60 (the largest 10 for the second
largest holding company), or all of them when there are fewer. Measured on
21,432 filings from 2021 to 2026: of the 13,856 per-company tables with a
tagged listed-issue count, 13,155 name at least that minimum and the
shortfalls thin out quickly; below half the minimum sit only a few tables,
and at issue counts of 60 or more there is a clean gap — every table names 50
or more, except SMFG FY2025 (9 named) and Meiji's second-largest company (13,
which meets its own minimum of 10). So a table is short when its own tagged
listed-issue count is at least 10 and it names fewer than half of
min(issues, 60) — min(issues, 10) for the second largest holding company.

WHAT THE FALLBACK TRUSTS
------------------------
Only a table the HTML reads completely: more named rows than the XBRL, every
book value in a declared unit, and a total no larger than the filing's own
tagged listed carrying amount. Values are as filed, in yen; a cell written
"-" or "*" (omitted) stays missing, never zero. Rows read this way carry
row_source = 'html' and the filing says so in its detail.

Standard library only, on purpose: edinet_honbun imports facility_extract,
which imports extract, which imports this.
Python 3.9.
"""
import html as _html
import io
import re
import unicodedata
import zipfile

# Minimum number of named issues the disclosure rules ask of each table.
MIN_NAMED = {"largest": 60, "reporting": 60, "second_largest": 10}
# Below this many tagged listed issues a table is too small to call short.
MIN_ISSUES = 10

VARIANTS = [("SecondLargestHoldingCompany", "second_largest"),
            ("LargestHoldingCompany", "largest"),
            ("ReportingCompany", "reporting")]
SPECIFIED_MARK = "DetailsOfSpecifiedInvestment"
DEEMED_MARK = "DetailsOfDeemedHoldings"


def required(holder_table, issue_count):
    u"""The fewest named rows the rules allow for this table, or None."""
    if issue_count is None or holder_table not in MIN_NAMED:
        return None
    return min(int(issue_count), MIN_NAMED[holder_table])


def is_short(holder_table, issue_count, named):
    u"""True when a table names fewer than half the rows the rules require."""
    req = required(holder_table, issue_count)
    if req is None or issue_count < MIN_ISSUES:
        return False
    return named * 2 < req


def shortfalls(holdings, total_rows):
    u"""[(holder_table, listed issue count, named rows)] for every short table.

    `holdings` and `total_rows` are what extract.parse_filing returns (the
    rows, and extra["totals"])."""
    issues = {}
    for t in total_rows:
        if t.get("share_class") == "listed" and t.get("issue_count") is not None:
            issues[t["holder_table"]] = issues.get(t["holder_table"], 0) + int(t["issue_count"])
    named = {}
    for h in holdings:
        named[h["holder_table"]] = named.get(h["holder_table"], 0) + 1
    # Rows filed under a company the totals do not name. KYORITSU (S100W53O)
    # tags its totals to the reporting company and its 19 named rows to the
    # largest holding company: the rows are there, only the label differs.
    orphan = sum(n for v, n in named.items() if v not in issues)
    return [(v, n, named.get(v, 0)) for v, n in sorted(issues.items())
            if is_short(v, n, named.get(v, 0) + orphan)]


# The same rule over a database, for the scan. Kept beside the Python rule so
# the two cannot drift apart; the numbers come from MIN_NAMED and MIN_ISSUES.
SCAN_SQL = """
WITH t AS (
    SELECT doc_id, holder_table, sum(issue_count) AS issues,
           sum(book_value_yen) AS listed_yen
    FROM eq_filing_totals
    WHERE share_class = 'listed' AND issue_count IS NOT NULL
    GROUP BY 1, 2),
h AS (
    SELECT doc_id, holder_table, count(*) AS named,
           sum(book_value_yen) AS named_yen%(src)s
    FROM eq_holdings GROUP BY 1, 2),
o AS (
    SELECT h.doc_id, sum(h.named) AS orphan
    FROM h ANTI JOIN t USING (doc_id, holder_table)
    GROUP BY 1)
SELECT f.doc_id, f.edinet_code, f.sec_code, f.filer_name, f.period_end,
       t.holder_table, t.issues, coalesce(h.named, 0) AS named,
       t.listed_yen, h.named_yen, f.status, f.detail%(src_col)s
FROM t JOIN eq_filings f USING (doc_id)
LEFT JOIN h USING (doc_id, holder_table)
LEFT JOIN o USING (doc_id)
WHERE t.issues >= %(min_issues)d
  AND t.holder_table IN (%(tables)s)
  AND (coalesce(h.named, 0) + coalesce(o.orphan, 0)) * 2 < least(t.issues, CASE t.holder_table %(cases)s END)
ORDER BY f.period_end, f.doc_id, t.holder_table
"""


def scan_sql(has_source):
    u"""SQL listing every short table in a cross-shareholding database. A table
    already read from HTML counts by its HTML rows, so after a re-run it drops
    out of the list; one the HTML could not fill, or whose HTML names no more
    than its XBRL, stays listed with its filing's detail saying which."""
    cases = " ".join("WHEN '%s' THEN %d" % kv for kv in sorted(MIN_NAMED.items()))
    return SCAN_SQL % {
        "src": (",\n           string_agg(DISTINCT row_source, ',') AS sources"
                if has_source else ""),
        "src_col": ", h.sources" if has_source else "",
        "min_issues": MIN_ISSUES,
        "cases": cases,
        "tables": ", ".join("'%s'" % k for k in sorted(MIN_NAMED)),
    }


# ---------------------------------------------------------------- reading HTML

def honbun_html(t1_blob):
    u"""Every honbun file of a type=1 package, in package order, as one string."""
    with zipfile.ZipFile(io.BytesIO(t1_blob) if isinstance(t1_blob, bytes) else t1_blob) as z:
        parts = [z.read(n) for n in sorted(z.namelist())
                 if "PublicDoc/" in n and "honbun" in n and n.endswith(".htm")]
    return b"".join(parts).decode("utf-8", "ignore")


def _key(s):
    u"""For matching labels only: no tags, NFKC, no whitespace."""
    s = _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def cell_text(raw):
    u"""A cell's text as filed. Filers wrap long names over several <p>
    lines ("大和ハウス" / "工業株式会社"); the pieces are joined with no space
    unless both sides are Latin ("Ares Management" / "Corporation",
    "CME GROUP INC." / "CLASS A")."""
    s = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>", "\n", raw or "")
    s = _html.unescape(re.sub(r"<[^>]+>", "", s))
    pieces = [p for p in (re.sub(r"[ \t\r\xa0]+", " ", x).strip()
                          for x in re.split(r"\n+", s)) if p]
    out = ""
    for p in pieces:
        if (out and ord(out[-1]) < 128 and ord(p[0]) < 128 and p[0].isalnum()
                and out[-1] not in "(/-"):
            out += " "
        out += p
    return out


_NOTE = re.compile(u"[（(]注[）)]\\s*\\d*|※\\s*\\d*")


def number(raw):
    u"""A filed number, or None for a blank, a dash or an omitted (*) cell."""
    s = _key(re.sub(r"<[^>]+>", " ", raw or ""))
    s = _NOTE.sub("", s)
    s = re.sub(r"\([^()]*\)$", "", s)
    s = s.replace(",", "")
    s = re.sub(u"[株円]+$", "", s)
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    return None


def grid(table_html):
    u"""The table as rows x columns of raw cell HTML, rowspan and colspan
    expanded; `origin` holds the cells where a span starts."""
    rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table_html)
    cells, origin, taken, width = {}, set(), set(), 0
    for r, row in enumerate(rows):
        c = 0
        for m in re.finditer(r"(?is)<t([dh])\b([^>]*)>(.*?)</t\1>", row):
            while (r, c) in taken:
                c += 1
            rs = re.search(r'rowspan=["\']?(\d+)', m.group(2))
            cs = re.search(r'colspan=["\']?(\d+)', m.group(2))
            rs = int(rs.group(1)) if rs else 1
            cs = int(cs.group(1)) if cs else 1
            origin.add((r, c))
            for dr in range(rs):
                for dc in range(cs):
                    cells[(r + dr, c + dc)] = m.group(3)
                    taken.add((r + dr, c + dc))
            c += cs
            width = max(width, c)
    return [[cells.get((r, c), "") for c in range(width)] for r in range(len(rows))], origin


def _unit(text):
    t = _key(text)
    if u"百万円" in t:
        return 10 ** 6
    if u"千円" in t:
        return 10 ** 3
    if u"円" in t:
        return 1
    return None


def layout(g):
    u"""Column roles of a named-holdings table, from its header rows; None if
    the table is not one.

    Two layouts are in use. Most filers follow the form: one holding is two
    rows, shares above book value, current year left of prior year. Some
    (SMFG) put shares and book value side by side, one row per holding."""
    head = 0
    while head < len(g) and head < 4:
        keys = [_key(c) for c in g[head]]
        if not any(k for k in keys if any(w in k for w in (
                u"株式数", u"貸借対照表計上額", u"当事業年度", u"前事業年度", u"銘柄"))):
            break
        if keys and keys[0] not in (u"銘柄", u"") and not keys[0].startswith(u"銘柄"):
            break
        if any(number(c) is not None for c in g[head][1:]):
            break
        head += 1
    if head == 0:
        return None
    hk = [[_key(c) for c in row] for row in g[:head]]
    flat = "".join("".join(r) for r in hk)
    if u"銘柄数" in flat or u"株式数" not in flat or u"貸借対照表計上額" not in flat:
        return None
    width = len(g[0])
    cols = {}
    unit = None
    for c in range(1, width):
        texts = [hk[i][c] for i in range(head)]
        period = ("cur" if any(u"当事業年度" in t for t in texts)
                  else "prior" if any(u"前事業年度" in t for t in texts) else None)
        if any(u"保有目的" in t for t in texts):
            cols.setdefault("purpose", c)
        elif any(u"保有の有無" in t for t in texts):
            cols.setdefault("reciprocal", c)
        elif period:
            cols.setdefault(period, []).append(c)
        for t in texts:
            if u"貸借対照表計上額" in t and unit is None:
                unit = _unit(t[t.find(u"貸借対照表計上額"):])
    if "cur" not in cols:
        return None
    # Which header row names which measure, over the year columns only: the
    # purpose column's own header ("…及び株式数が増加した理由") spans every
    # header row and says 株式数 too.
    period_cols = cols.get("cur", []) + cols.get("prior", [])
    rows_s = [i for i in range(head) if any(
        u"株式数" in hk[i][c] and u"貸借対照表計上額" not in hk[i][c] for c in period_cols)]
    rows_b = [i for i in range(head) if any(
        u"貸借対照表計上額" in hk[i][c] for c in period_cols)]
    both = sorted(set(rows_s) & set(rows_b))
    if both:
        i = both[0]
        measure = {}
        for c in period_cols:
            k = hk[i][c]
            measure[c] = ("bv" if u"貸借対照表計上額" in k
                          else "shares" if u"株式数" in k else None)
        span, order = 1, None
    else:
        if not rows_s or not rows_b:
            return None
        measure = None
        span = 2
        order = ["shares", "bv"] if min(rows_s) < min(rows_b) else ["bv", "shares"]
    return {"head": head, "width": width, "cols": cols, "measure": measure,
            "span": span, "order": order, "unit": unit}


def _holding(g, origin, r0, lay):
    span = lay["span"]
    rows = list(range(r0, min(r0 + span, len(g))))

    def joined(c):
        seen = []
        for r in rows:
            if (r, c) in origin or r == r0:
                t = cell_text(g[r][c])
                if t and t not in seen:
                    seen.append(t)
        return "".join(seen) if c == 0 else " ".join(seen)

    vals = {}
    for period in ("cur", "prior"):
        for c in lay["cols"].get(period, []):
            if span == 1:
                m = lay["measure"].get(c)
                if m:
                    vals[(period, m)] = number(g[r0][c])
            else:
                for j, r in enumerate(rows):
                    vals[(period, lay["order"][j])] = number(g[r][c])
    pc, rc = lay["cols"].get("purpose"), lay["cols"].get("reciprocal")
    return {"name": joined(0), "vals": vals,
            "purpose": joined(pc) if pc is not None else "",
            "reciprocal": joined(rc) if rc is not None else ""}


def last_heading(text):
    u"""Which table the last heading in `text` announces: 'specified'
    (特定投資株式), 'deemed' (みなし保有株式), 'pure' (純投資目的である), or
    None. Phrases naming both kinds at once are not headings."""
    t = text.replace(u"特定投資株式及びみなし保有株式", "").replace(
        u"特定投資株式とみなし保有株式", "")
    at = [(t.rfind(w), k) for w, k in ((u"特定投資株式", "specified"),
                                        (u"みなし保有株式", "deemed"),
                                        (u"純投資目的である", "pure"))]
    pos, kind = max(at)
    return kind if pos >= 0 else None


def _variant(names):
    for n in names:
        for suffix, label in VARIANTS:
            if n.endswith(suffix) or suffix in n:
                return label
    return None


def read_tables(html):
    u"""{holder_table: [holding rows]} of the 特定投資株式 tables in a filing's
    HTML, plus a list of notes on what could not be read.

    A table belongs to a company by the inline-XBRL element names its cells
    carry, and is a specified or deemed table by the heading printed above it
    (the tags as a fallback); a continuation table (page break, no tags)
    inherits the previous table's company and layout when nothing but layout
    lies between them. An untagged table in a filing with one company is the
    reporting company's.
    Deemed holdings (みなし保有株式) and pure-investment tables are not read,
    as the XBRL path does not read them either."""
    start = html.find(u"株式の保有状況】")
    if start < 0:
        start = html.find(u"株式の保有状況")
    if start < 0:
        return {}, ["no 株式の保有状況 section"]
    # The item ends where the financial statements begin; their notes hold
    # other tables of names and share counts that are not this disclosure.
    end = html.find(u"【経理の状況】", start)
    section = html[start:end] if end > start else html[start:start + 2000000]
    multi = any(w in _key(section)
                for w in (u"最大保有会社", u"次に大きい会社"))
    out, notes = {}, []
    prev = None          # (kind, variant, layout) of the last named table
    prev_end = 0
    for m in re.finditer(r"(?is)<table[^>]*>.*?</table>", section):
        between = _key(re.sub(r"<[^>]+>", " ", section[prev_end:m.start()]))
        prev_end = m.end()
        t = m.group(0)
        g, origin = grid(t)
        if not g:
            continue
        lay = layout(g)
        names = re.findall(r'name="[\w.-]+:([A-Za-z0-9_]+)"', t)
        if any(DEEMED_MARK in n for n in names):
            kind = "deemed"
        elif any(SPECIFIED_MARK in n for n in names):
            kind = "specified"
        else:
            kind = None
        tagged = kind is not None
        variant = _variant([n for n in names if SPECIFIED_MARK in n or DEEMED_MARK in n])
        # The printed heading outranks the tags: Hosokawa Micron (S100XB4O)
        # tags its 特定投資株式 table with the deemed-holdings elements.
        heading = last_heading(between)
        if heading is not None:
            kind = None if heading == "pure" else heading
        company_change = u"保有する株式" in between or u"○" in between
        if kind is None and heading is None and prev and not company_change \
                and (lay is None or lay["width"] == prev[2]["width"]):
            # a continuation over a page break: same company, same table
            kind, variant = prev[0], prev[1]
            if lay is None and len(g[0]) == prev[2]["width"]:
                lay = dict(prev[2], head=0)
        elif kind is not None and variant is None:
            if prev and not company_change:
                variant = prev[1]
            elif not multi:
                variant = "reporting"
        if lay is None:
            if tagged and prev is None:
                notes.append("a tagged table whose header could not be read")
            prev = None          # a table that is neither a holdings table nor
            continue             # its continuation ends the run
        if kind is None:
            prev = None
            continue
        prev = (kind, variant, lay)
        if kind != "specified":
            continue
        if variant is None:
            notes.append("a named table whose company could not be told")
            continue
        if lay["unit"] is None:
            notes.append("%s: book value with no unit" % variant)
            out.setdefault(variant, None)
            continue
        rows = out.setdefault(variant, [])
        if rows is None:
            continue
        r = lay["head"]
        while r < len(g):
            h = _holding(g, origin, r, lay)
            r += lay["span"]
            if not h["name"]:
                if any(v is not None for v in h["vals"].values()):
                    notes.append("%s: a row with figures but no name" % variant)
                    out[variant] = None
                    break
                continue
            h["unit"] = lay["unit"]
            rows.append(h)
    return {v: rows for v, rows in out.items() if rows is not None}, notes


def _yen(v, unit):
    return "" if v is None or unit is None else str(int(round(v * unit)))


def holdings_from_html(t1_blob):
    u"""Holdings rows in parse_filing's shape, per holder table, from the
    filing's HTML: ({holder_table: [row]}, notes)."""
    html = honbun_html(t1_blob)
    tables, notes = read_tables(html)
    return _rows(tables), notes


def _rows(tables):
    u"""Each row converted at its own table's unit: a continuation table
    inherits the unit of the table it continues."""
    out = {}
    for variant, rows in tables.items():
        conv = []
        for i, h in enumerate(rows, 1):
            unit = h.get("unit")
            v = h["vals"]
            conv.append({
                "holder_table": variant, "row": str(i), "held_name": h["name"],
                "shares": _int(v.get(("cur", "shares"))),
                "book_value": _yen(v.get(("cur", "bv")), unit),
                "prior_shares": _int(v.get(("prior", "shares"))),
                "prior_book_value": _yen(v.get(("prior", "bv")), unit),
                "purpose": h["purpose"], "reciprocal": h["reciprocal"],
                "source": "html",
            })
        out[variant] = conv
    return out


def _int(v):
    return "" if v is None else str(int(round(v)))


def fallback(xbrl_holdings, total_rows, short, t1_blob, missing="HTML not archived"):
    u"""Replace each short table's XBRL rows with its HTML rows where the HTML
    reads the table completely. Returns (holdings, problems, remarks): the
    holdings to store, and one plain sentence per short table saying what was
    done — a problem when the stored rows are not the filing's full table (or
    could not be checked), a remark when the HTML names exactly the rows the
    XBRL tagged, i.e. the filing itself names no more."""
    listed_yen = {}
    for t in total_rows:
        if t.get("share_class") == "listed" and t.get("book_value_yen") is not None:
            listed_yen[t["holder_table"]] = (listed_yen.get(t["holder_table"], 0)
                                             + int(t["book_value_yen"]))
    try:
        html_rows, notes = (holdings_from_html(t1_blob) if t1_blob is not None
                            else ({}, [missing]))
    except Exception as e:                       # a bad package keeps the XBRL rows
        html_rows, notes = {}, ["HTML unreadable: %s" % str(e)[:80]]
    keep = list(xbrl_holdings)
    problems, remarks = [], []
    for variant, issues, named in short:
        got = html_rows.get(variant) or []
        total = sum(int(r["book_value"]) for r in got if r["book_value"])
        # rows still held at year end; the table also lists issues held only
        # in the prior year, with the current columns blank
        held = [r for r in got if r["shares"] or r["book_value"]]
        why = None
        if not got:
            mine = [n for n in notes if n.startswith(variant + ":")
                    or not any(n.startswith(v + ":") for v in MIN_NAMED)]
            why = "; ".join(mine) or "no named table found in the HTML"
        elif len(got) == named:
            remarks.append("the filing names %d of %d listed issues for %s, in its "
                           "HTML as in its XBRL" % (named, issues, variant))
            continue
        elif len(got) < named:
            why = "the HTML read gives %d, fewer than the XBRL" % len(got)
        elif not any(r["book_value"] for r in got):
            why = "the HTML table has no book values"
        elif variant in listed_yen and total > listed_yen[variant]:
            why = ("the HTML rows total %d yen, above the tagged listed total %d yen"
                   % (total, listed_yen[variant]))
        elif len(held) > issues:
            why = "the HTML names %d holdings against %d listed issues" % (len(held), issues)
        if why:
            problems.append(
                "XBRL names %d of %d listed issues for %s; HTML fallback not used (%s)"
                % (named, issues, variant, why))
            continue
        keep = [h for h in keep if h["holder_table"] != variant] + got
        problems.append(
            "XBRL names %d of %d listed issues for %s; %d named rows read from the "
            "filing's HTML instead" % (named, issues, variant, len(got)))
    return keep, problems, remarks
