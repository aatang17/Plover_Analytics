# -*- coding: utf-8 -*-
"""The written parts of US annual reports: what a company does, its risks, its
legal cases and management's own account of the year.

Why this reads the documents
----------------------------
Every US figure on the site comes from the SEC's Financial Statement Data Sets
(equity/sec_extract.py), which hold the tagged numbers and nothing else. The
narrative of a 10-K is untagged prose, so "what does Nvidia do" or "what risks
does Apple list" had no answer. This reads each listed company's latest annual
report itself and keeps the sections people ask about:

    10-K / 10-KT                         20-F (foreign private issuers)
    Item 1   Business      -> business       Item 4   Information on the Company
    Item 1A  Risk Factors  -> risk_factors   Item 3.D Risk Factors
    Item 1C  Cybersecurity -> cybersecurity  Item 16K Cybersecurity
    Item 2   Properties    -> properties     —
    Item 3   Legal         -> legal          —
    Item 7   MD&A          -> mdna           Item 5   Operating and Financial Review
    Item 7A  Market risk   -> market_risk    Item 11  Market risk

The document is the filing's primary HTML, named after the XBRL instance the
SEC lists for it (`nvda-20260125_htm.xml` -> `nvda-20260125.htm`): one request
per filing. Each is archived gzipped with its SHA-256 before it is parsed, so
a new parser re-reads the archive and never asks the SEC again.

How a document is cut
---------------------
The HTML becomes a list of blocks (paragraphs, headings, table rows), with
how much of each is bold or italic. Running page headers and footers ("35
Table of Contents", "Apple Inc. | 2025 Form 10-K | 20") are short blocks that
repeat; they are dropped, as are rows of figure tables (the figures are in the
data sets already). Every short block that starts "Item 1A" etc. is a
candidate heading. The table of contents names every item too, so the
headings kept are the chain, in item order, whose sections hold the most text:
the contents page gives a chain of empty sections and loses. A 10-K that
refers a section elsewhere ("Refer to Note 30") keeps that sentence as the
section; JPMorgan's MD&A lives in its annual report exhibit and is not read.

Risk Factors are also cut into single risks: a styled (bold or italic) block
that reads as a sentence is one risk's headline, a short styled block without
a full stop is the category above it ("Risks Related to Our Industry").

An amendment (10-K/A, 20-F/A) filed for the same year after that report is
read too, and kept as a filing of its own: a restated 10-K/A carries the whole
document again, and the API serves the newest filing of the year in which
Business and Risk Factors were found. Most amendments add only the Part III
pay and board items; those find no sections and change nothing shown.

Status per filing: ok (business and risks found), partial (some sections),
unsplit (no item headings found: nothing stored), failed (not fetched).

Tables (data/sec_text.duckdb — its own file; the text is large and the
serving process only reads it)
    sec_text_filings   one row per annual report read: CIK, form, period,
                       filed, URL, SHA-256, raw path, parser, status, note
    sec_text_sections  one row per section: key, item, the filer's title,
                       text_z (the text, paragraphs separated by a blank line,
                       zlib-compressed: 3.7x smaller, ~400MB for every listed
                       company; DuckDB stores long strings uncompressed),
                       headings (indexes of the paragraphs that are headings)
    sec_text_risks     one row per risk: order, category, headline, and the
                       range of Risk Factors paragraphs that are its text
    sec_text_checks    one row per company per run: when the SEC was asked
                       and what came back, for the health report

Insert-only per parser version: a filing is read once; a new PARSER_VERSION
re-parses the archived document and replaces that filing's derived rows.

Usage (from observatory/):
    ./.venv/bin/python equity/sec_text_extract.py                    # 300 companies, least recently checked first
    ./.venv/bin/python equity/sec_text_extract.py --limit 0          # every listed company
    ./.venv/bin/python equity/sec_text_extract.py --tickers NVDA,TSM
    ./.venv/bin/python equity/sec_text_extract.py --db /tmp/copy.duckdb --sec-db data/sec.duckdb

Needs EDGAR_USER_AGENT ("Company Name contact@domain"). Python 3.9; stdlib + duckdb.
"""
import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import re
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sec_proxy_pay import Fetcher, PullError  # noqa: E402  — the shared, rate-limited SEC client

DB_PATH = os.environ.get("SEC_TEXT_DB_PATH", os.path.join(HERE, "..", "data", "sec_text.duckdb"))
SEC_DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "sec-text")
DOC_URL = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s"
PARSER_VERSION = "txt-3"
EXTRACTOR = "sec-annual-text"
FORMS = ("10-K", "10-KT", "20-F")
AMENDMENTS = ("10-K/A", "10-KT/A", "20-F/A")
RECENT_DAYS = 550               # an annual report older than this is not the company's latest
RATE = 4.0
WORKERS = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_text_filings (
    adsh VARCHAR PRIMARY KEY, cik BIGINT, form VARCHAR, period DATE, filed DATE,
    url VARCHAR, sha256 VARCHAR, raw_path VARCHAR, parser VARCHAR, status VARCHAR,
    note VARCHAR, read_at TIMESTAMP, chars BIGINT);
CREATE TABLE IF NOT EXISTS sec_text_sections (
    adsh VARCHAR, section VARCHAR, item VARCHAR, title VARCHAR, ord INTEGER,
    text_z BLOB, headings VARCHAR, chars INTEGER, PRIMARY KEY (adsh, section));
CREATE TABLE IF NOT EXISTS sec_text_risks (
    adsh VARCHAR, n INTEGER, category VARCHAR, headline VARCHAR,
    para_from INTEGER, para_to INTEGER, PRIMARY KEY (adsh, n));
CREATE TABLE IF NOT EXISTS sec_text_checks (
    cik BIGINT, checked_at TIMESTAMP, status VARCHAR, adsh VARCHAR, note VARCHAR);
"""

# section key -> item, per form
SECTIONS = {
    "10-K": [("business", "1"), ("risk_factors", "1A"), ("cybersecurity", "1C"), ("properties", "2"),
             ("legal", "3"), ("mdna", "7"), ("market_risk", "7A")],
    "20-F": [("business", "4"), ("risk_factors", "3"), ("cybersecurity", "16K"), ("mdna", "5"),
             ("market_risk", "11")],
}
SECTIONS["10-KT"] = SECTIONS["10-K"]
ITEM_ORDER = {
    "10-K": ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
             "10", "11", "12", "13", "14", "15", "16"],
    "20-F": ["1", "2", "3", "4", "4A", "5", "6", "7", "8", "9", "10", "11", "12", "12D", "13", "14", "15",
             "16", "16A", "16B", "16C", "16D", "16E", "16F", "16G", "16H", "16I", "16J", "16K", "17", "18", "19"],
}
ITEM_ORDER["10-KT"] = ITEM_ORDER["10-K"]
HEAD = re.compile(r"^\s*item\s*(\d{1,2}[a-k]?)(?![0-9a-z])\s*[\.:\-–—]?\s*(.*)$", re.I)


def _now():
    return dt.datetime.utcnow().replace(microsecond=0)


# ---- HTML to blocks -----------------------------------------------------------
_BLOCK = {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "center", "ul", "ol",
          "section", "article", "body", "html", "blockquote", "dt", "dd"}
_HIDE = {"script", "style", "head", "title", "ix:header"}


class _Blocks(HTMLParser):
    """[text, bold chars, italic chars, in a table, leading styled run] per
    block of the page. The leading run is the bold or italic text a block
    opens with (Microsoft states each risk as a bold first sentence)."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack, self.out, self.cur = [], [], []
        self.bold = self.ital = self.table = 0
        self.lead, self.lead_open = [], True

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.cur).replace("\xa0", " ")).strip()
        if t:
            lead = re.sub(r"\s+", " ", "".join(self.lead).replace("\xa0", " ")).strip()
            self.out.append([t, self.bold, self.ital, self.table > 0, lead])
        self.cur, self.bold, self.ital = [], 0, 0
        self.lead, self.lead_open = [], True

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.cur.append(" ")
            return
        st = (dict(attrs).get("style") or "").lower().replace(" ", "")
        hidden = "display:none" in st or tag in _HIDE
        bold = tag in ("b", "strong") or "font-weight:bold" in st or re.search(r"font-weight:[6-9]00", st) is not None
        ital = tag in ("i", "em") or "font-style:italic" in st
        if tag in _BLOCK:
            self.flush()
        if tag == "table":
            self.table += 1
        if tag in ("td", "th"):
            self.cur.append("  ")
        up = self.stack[-1] if self.stack else (None, False, False, False)
        if tag in ("img", "hr", "meta", "link", "input"):
            return                                       # void elements never close
        self.stack.append((tag, hidden or up[1], bold or up[2], ital or up[3]))

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                del self.stack[k:]
                break
        else:
            return
        if tag in _BLOCK:
            self.flush()
        if tag == "table":
            self.table = max(0, self.table - 1)

    def handle_data(self, data):
        top = self.stack[-1] if self.stack else (None, False, False, False)
        if top[1]:
            return
        self.cur.append(data)
        n = len(data.strip())
        if top[2]:
            self.bold += n
        if top[3]:
            self.ital += n
        if self.lead_open:
            if top[2] or top[3]:
                self.lead.append(data)
            elif n:
                self.lead_open = False


def blocks(body):
    p = _Blocks()
    p.feed(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
    p.flush()
    out = p.out
    # running headers and footers: short blocks that recur with only their numbers changed
    key = lambda t: re.sub(r"\d+", "#", t.lower())            # noqa: E731
    seen = {}
    for t in (b[0] for b in out):
        if len(t) <= 90:
            seen[key(t)] = seen.get(key(t), 0) + 1
    keep = []
    for b in out:
        t = b[0]
        if re.match(r"^(page )?[\divxlc\-–. ]{1,8}$", t, re.I):
            continue                                       # a page number
        if re.match(r"^(\d+\.?\s*)?table of contents(\s|$)", t, re.I) and len(t) <= 90:
            continue
        if len(t) <= 90 and seen.get(key(t), 0) >= 4 and not HEAD.match(t):
            continue
        keep.append(b)
    return _rejoin(keep)


_END = re.compile(r"[.:;!?](?:[”\"’)\]]*)$")


def _rejoin(bl):
    """Join a document typeset line by line (each printed line its own
    block, as in TSMC's 20-F) back into paragraphs: a line that does not end
    a sentence runs on into the next line of the same style. Only for such
    documents: elsewhere a block without a full stop is a heading."""
    prose = [b for b in bl if not b[3] and len(b[0]) >= 40]
    cut = [b for b in prose if 60 <= len(b[0]) <= 140 and not _END.search(b[0])]
    if len(prose) < 50 or len(cut) < 0.35 * len(prose):
        return bl
    out = []
    for b in bl:
        if (out and not b[3] and not out[-1][3] and not _END.search(out[-1][0]) and _styled(out[-1]) == _styled(b)
                and not HEAD.match(b[0]) and not HEAD.match(out[-1][0]) and len(out[-1][0]) >= 40
                and not _RISK_START.match(b[0])):
            a = out[-1]
            out[-1] = [a[0] + " " + b[0], a[1] + b[1], a[2] + b[2], False, a[4] if len(a) > 4 else ""]
        else:
            out.append(list(b))
    return out


def _styled(b):
    n = len(b[0].replace(" ", ""))
    return n > 0 and (b[1] / n > 0.8 or b[2] / n > 0.8)


def _figure_row(b):
    """A row of a figure table: in a table and mostly digits."""
    if not b[3]:
        return False
    t = b[0]
    digits = sum(c.isdigit() for c in t)
    return digits >= 3 and digits / max(1, len(t.replace(" ", ""))) > 0.15


# ---- the items ---------------------------------------------------------------
def items(bl, form):
    """[(item, title, first block, end block)] — the chain of item headings,
    in item order, whose sections hold the most text."""
    order = {it: n for n, it in enumerate(ITEM_ORDER[form])}
    cands = []
    for i, b in enumerate(bl):
        if len(b[0]) > 200:
            continue
        m = HEAD.match(b[0])
        if m and m.group(1).upper() in order:
            cands.append((i, m.group(1).upper(), m.group(2).strip()))
    if not cands:
        return []
    # a section runs to the next heading of a different item: a page header
    # repeating "Item 1" on every page (Microsoft's) does not end it
    size = []
    for k, (i, item, _) in enumerate(cands):
        j = next((c[0] for c in cands[k + 1:] if c[1] != item), len(bl))
        size.append(sum(len(bl[x][0]) for x in range(i + 1, j)))
    best, prev = list(size), [-1] * len(cands)
    for k in range(len(cands)):
        for q in range(k):
            if order[cands[q][1]] < order[cands[k][1]] and best[q] + size[k] > best[k]:
                best[k], prev[k] = best[q] + size[k], q
    k = max(range(len(cands)), key=lambda x: best[x])
    chain = []
    while k >= 0:
        chain.append(k)
        k = prev[k]
    chain.reverse()
    out = []
    for n, k in enumerate(chain):
        i, item, title = cands[k]
        j = cands[chain[n + 1]][0] if n + 1 < len(chain) else len(bl)
        nxt = next((c[0] for c in cands if c[0] > i and c[1] != item), len(bl))
        j = min(j, nxt) if n + 1 < len(chain) else j
        if not re.search(r"[A-Za-z]{3}", title) and i + 1 < j and len(bl[i + 1][0]) < 120:
            title, i = bl[i + 1][0], i + 1             # "ITEM 1." on one line, "BUSINESS" on the next
        out.append((item, title.strip(" .:"), i + 1, j))
    return out


def _caps(t):
    """A short line in capitals with no full stop: a heading in plain type."""
    letters = [c for c in t if c.isalpha()]
    return 3 <= len(t) <= 150 and len(letters) >= 3 and all(c.isupper() for c in letters) and not t.endswith(".")


def _paragraphs(bl):
    """(paragraphs, heading indexes, lead-in runs) for a run of blocks; a
    lead-in is the bold first sentence a paragraph opens with."""
    paras, heads, leads = [], [], {}
    for b in bl:
        if _figure_row(b):
            continue
        t = b[0]
        if re.match(r"^(part\s+[ivx]+|item\s*\d{1,2}[a-k]?)\b\.?\s*$", t, re.I):
            continue                                   # a running "Part I" or "Item 1A" header
        if (_styled(b) or _caps(t)) and len(t) <= 300:
            heads.append(len(paras))
        elif len(b) > 4 and len(b[4]) >= 30 and re.search(r"[.:]$", b[4]):
            leads[len(paras)] = b[4]
        paras.append(t)
    return paras, heads, leads


_RISK_START = re.compile(r"^(?:item\s*)?(?:3\.?\s*)?(?:d\.?\s*)?risk factors\.?$", re.I)


_NOT_CATEGORY = re.compile(r"(?i)^(risk factors?( summary)?|(summary|summary of) (of )?risk factors|summary risk factors|"
                           r"summary|part [ivx]+|item\s.*|general)\.?:?$")


def risks(paras, heads, leads):
    """[(category, headline, para_from, para_to)]. A heading followed by text
    is a risk's headline ("We Face Intense Competition", "MANAGED CARE
    TRENDS", a full sentence); one followed by another heading is the category
    above them ("Business and Industry Risks"). A paragraph opening with a
    bold sentence is a risk too, its headline that sentence."""
    hs = set(heads)
    starts = sorted(hs | set(leads))
    sentence = lambda t: bool(re.search(r"[.!?][”\"’)]*$", t)) and len(t) >= 40          # noqa: E731
    # where most risks are bold opening sentences (Microsoft), a heading
    # that is not a sentence is a topic or category above them
    lead_mode = len(leads) >= 5 and len(leads) >= sum(1 for i in hs if sentence(paras[i]))
    intro = re.compile(r"(?i)^(the following|you should|in addition to|investing in|our business is subject|"
                       r"this section|carefully consider|please carefully|certain factors may|the above summary|"
                       r"the risks (and uncertainties )?described)")
    out, category = [], None
    for n, i in enumerate(starts):
        t = paras[i].strip()
        nxt = starts[n + 1] if n + 1 < len(starts) else None
        if i in hs:
            if not sentence(t) and (nxt == i + 1 or lead_mode):
                if not _NOT_CATEGORY.match(t) and len(t) <= 150:
                    category = t.strip(" :")
                continue
            if i + 1 >= len(paras) or _NOT_CATEGORY.match(t) or len(t) < 12:
                continue
            head, body_from = t, i + 1
        else:
            head, body_from = leads[i].strip(), i
        if intro.match(head):
            continue                                   # the section's own introduction
        end = body_from
        while end < len(paras) and end not in hs and (end == body_from or end not in leads):
            end += 1
        if end == body_from and body_from != i:
            continue                                   # a heading with nothing under it
        out.append((category, head, i, end))
    # a summary at the top lists the categories before the risks: a "risk"
    # whose headline is a later category is that summary line
    norm = lambda t: re.sub(r"[^a-z]", "", (t or "").lower())                  # noqa: E731
    cats = {norm(c) for c, _, _, _ in out if c}
    return [r for r in out if norm(r[1]) not in cats]


def parse(body, form):
    """{"status", "note", "sections": [(key, item, title, paras, heads)], "risks": [...]}."""
    form = form[:-2] if form.endswith("/A") else form          # an amendment is cut like its form
    bl = blocks(body)
    found = items(bl, form)
    if not found:
        return {"status": "unsplit", "note": "no item headings found", "sections": [], "risks": []}
    by_item = {}
    for item, title, i, j in found:
        by_item.setdefault(item, (title, i, j))
    secs, rk = [], []
    for ordn, (key, item) in enumerate(SECTIONS[form]):
        if item not in by_item:
            continue
        title, i, j = by_item[item]
        part = bl[i:j]
        if form == "20-F" and key == "risk_factors":
            start = next((n for n, b in enumerate(part) if len(b[0]) < 80 and _RISK_START.match(b[0])), None)
            if start is None:
                continue                               # Item 3 without a Risk Factors part: not risks
            title, part = "Risk Factors", part[start + 1:]
        paras, heads, leads = _paragraphs(part)
        if not paras:
            continue
        secs.append((key, item, title, paras, heads))
        if key == "risk_factors":
            rk = risks(paras, heads, leads)
    keys = {s[0] for s in secs}
    big = lambda k: any(s[0] == k and sum(map(len, s[3])) >= 500 for s in secs)  # noqa: E731
    if big("business") and big("risk_factors"):
        status, note = "ok", None
    elif secs:
        status = "partial"
        note = "missing or short: " + ", ".join(k for k in ("business", "risk_factors") if not big(k))
    else:
        status, note = "unsplit", "item headings found, none of the sections kept"
    rtext = " ".join(" ".join(s[3][:3]) for s in secs if s[0] == "risk_factors")[:1500]
    if re.search(r"(?i)smaller reporting company", rtext) and re.search(r"(?i)not required", rtext):
        note = ((note + "; ") if note else "") + "a smaller reporting company: risk factors not required"
    elif re.search(r"(?i)incorporated (herein )?by reference|(prospectus|registration statement)", rtext) and len(rtext) < 1200:
        note = ((note + "; ") if note else "") + "risk factors given by reference to another document"
    elif "risk_factors" in keys and not rk:
        note = ((note + "; ") if note else "") + "risk headlines not found"
    return {"status": status, "note": note, "sections": secs, "risks": rk}


# ---- fetch and store -------------------------------------------------------------
def archive(body, adsh, sha):
    d = os.path.join(RAW_ROOT, dt.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.%s.htm.gz" % (adsh, sha[:12]))
    if not os.path.exists(path):
        with gzip.open(path, "wb") as f:
            f.write(body)
    return os.path.relpath(path, os.path.join(HERE, ".."))


def doc_url(cik, adsh, instance):
    if not instance or not instance.endswith("_htm.xml"):
        return None                                    # no inline XBRL document to name the HTML by
    return DOC_URL % (cik, adsh.replace("-", ""), instance[:-len("_htm.xml")] + ".htm")


def archived(adsh):
    """(body, sha, path) of this filing's document already in the archive, or
    None. A filing's document never changes, so a database rebuilt or lost
    is read again from the archive, not the SEC."""
    for path in sorted(glob.glob(os.path.join(RAW_ROOT, "*", "%s.*.htm.gz" % adsh))):
        try:
            with gzip.open(path, "rb") as f:
                body = f.read()
        except (OSError, EOFError):
            continue                                   # a half-written file: fetch again
        sha = hashlib.sha256(body).hexdigest()
        if os.path.basename(path).split(".")[1] == sha[:12]:
            return body, sha, os.path.relpath(path, os.path.join(HERE, ".."))
    return None


def fetch(fetcher, target):
    cik, adsh, form, period, filed, instance = target
    url = doc_url(cik, adsh, instance)
    if url is None:
        return target, url, None, None, "no inline XBRL instance named for this filing"
    have = archived(adsh)
    if have:
        body, sha, path = have
        return target, url, sha, path, parse(body, form)
    body = fetcher.get(url)
    if not body.lstrip()[:200].lower().startswith((b"<", b"\xef\xbb\xbf<")):
        raise PullError("not an HTML document: %s" % url)
    sha = hashlib.sha256(body).hexdigest()
    path = archive(body, adsh, sha)
    return target, url, sha, path, parse(body, form)


def store(con, target, url, sha, path, got):
    cik, adsh, form, period, filed, instance = target
    con.execute("BEGIN")
    try:
        for t in ("sec_text_sections", "sec_text_risks", "sec_text_filings"):
            con.execute("DELETE FROM %s WHERE adsh = ?" % t, [adsh])
        if isinstance(got, str):                       # nothing to read
            con.execute("INSERT INTO sec_text_filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [adsh, cik, form, period, filed, url, sha, path, PARSER_VERSION, "no_doc", got, _now(), 0])
        else:
            chars = 0
            for ordn, (key, item, title, paras, heads) in enumerate(got["sections"]):
                text = "\n\n".join(paras)
                chars += len(text)
                con.execute("INSERT INTO sec_text_sections VALUES (?,?,?,?,?,?,?,?)",
                            [adsh, key, item, title[:300], ordn, zlib.compress(text.encode("utf-8"), 6),
                             json.dumps(heads), len(text)])
            for n, (cat, head, a, b) in enumerate(got["risks"]):
                con.execute("INSERT INTO sec_text_risks VALUES (?,?,?,?,?,?)",
                            [adsh, n + 1, (cat or None) and cat[:300], head[:2000], a, b])
            con.execute("INSERT INTO sec_text_filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [adsh, cik, form, period, filed, url, sha, path, PARSER_VERSION, got["status"],
                         got["note"], _now(), chars])
        con.execute("INSERT INTO sec_text_checks VALUES (?,?,?,?,?)",
                    [cik, _now(), "no_doc" if isinstance(got, str) else got["status"], adsh,
                     got if isinstance(got, str) else got["note"]])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def migrate(con):
    """txt-2 stored section text uncompressed: drop the derived rows so
    reparse() cuts every archived document again into the compressed table."""
    cols = {r[0] for r in con.execute("SELECT column_name FROM duckdb_columns() WHERE table_name = 'sec_text_sections'").fetchall()}
    if "text" in cols:
        con.execute("DROP TABLE sec_text_sections")
        con.execute("DROP TABLE IF EXISTS sec_text_risks")
        con.execute("UPDATE sec_text_filings SET parser = 'txt-2-uncompressed'")


def text_of(blob):
    return zlib.decompress(blob).decode("utf-8")


def reparse(con):
    """Re-cut every archived document read by an older parser; no request to the SEC."""
    rows = con.execute("""SELECT adsh, cik, form, period, filed, url, sha256, raw_path FROM sec_text_filings
                          WHERE parser <> ? AND raw_path IS NOT NULL""", [PARSER_VERSION]).fetchall()
    root = os.path.join(HERE, "..")
    for adsh, cik, form, period, filed, url, sha, path in rows:
        try:
            with gzip.open(os.path.join(root, path), "rb") as f:
                body = f.read()
        except OSError:
            continue                                   # archive not on this volume: the next fetch re-reads it
        store(con, (cik, adsh, form, period, filed, None), url, sha, path, parse(body, form))
    return len(rows)


def targets(sec_db, con, limit, tickers=None):
    """Each listed company's latest annual report, least recently checked first."""
    sec = duckdb.connect(sec_db, read_only=True)
    try:
        rows = sec.execute("""
            SELECT f.cik, arg_max(f.adsh, f.filed), arg_max(f.form, f.filed), arg_max(f.period, f.filed),
                   max(f.filed), arg_max(f.instance, f.filed), any_value(c.ticker)
            FROM sec_filings f JOIN sec_companies c USING (cik)
            WHERE c.ticker IS NOT NULL AND f.form IN (%s) AND f.filed >= current_date - %d
            GROUP BY f.cik""" % (",".join("'%s'" % f for f in FORMS), RECENT_DAYS)).fetchall()
        # a company under a new filer number (ExxonMobil Holdings) is read from its predecessor's reports
        try:
            from app.us_link import PREDECESSOR_CIKS
        except Exception:                                         # noqa: BLE001
            PREDECESSOR_CIKS = {}
        have_cik = {r[0] for r in rows}
        for new, old in PREDECESSOR_CIKS.items():
            if new in have_cik:
                continue
            old_rows = sec.execute("""
                SELECT f.cik, arg_max(f.adsh, f.filed), arg_max(f.form, f.filed), arg_max(f.period, f.filed),
                       max(f.filed), arg_max(f.instance, f.filed), any_value(c.ticker)
                FROM sec_filings f JOIN sec_companies c ON c.cik = ?
                WHERE f.cik = ? AND f.form IN (%s) AND f.filed >= current_date - %d
                GROUP BY f.cik""" % (",".join("'%s'" % f for f in FORMS), RECENT_DAYS), [new, old]).fetchall()
            rows += old_rows                                      # fetched from the old number's archive
        # amendments of each latest report, for the same year and filed after it
        latest = {r[0]: (r[3], r[4]) for r in rows}
        for cik, adsh, form, period, filed, instance in sec.execute("""
                SELECT cik, adsh, form, period, filed, instance FROM sec_filings
                WHERE form IN (%s) AND filed >= current_date - %d""" % (
                ",".join("'%s'" % f for f in AMENDMENTS), RECENT_DAYS)).fetchall():
            if cik in latest and period == latest[cik][0] and filed >= latest[cik][1]:
                rows.append((cik, adsh, form, period, filed, instance, next(r[6] for r in rows if r[0] == cik)))
    finally:
        sec.close()
    have = {a: s for a, s in con.execute("SELECT adsh, parser FROM sec_text_filings WHERE status <> 'failed'").fetchall()}
    last = dict(con.execute("SELECT cik, max(checked_at) FROM sec_text_checks GROUP BY cik").fetchall())
    if tickers:
        want = {t.strip().upper() for t in tickers.split(",")}
        rows = [r for r in rows if (r[6] or "").upper() in want]
    else:
        rows = [r for r in rows if r[1] not in have]  # a filing read once is not asked for again
        rows.sort(key=lambda r: (last.get(r[0]) is not None, last.get(r[0]) or dt.datetime.min, -(r[0] or 0)))
        if limit:
            rows = rows[:limit]
    return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows], {r[0]: r[6] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--sec-db", default=SEC_DB_PATH)
    ap.add_argument("--limit", type=int, default=300, help="companies this run (0 = all)")
    ap.add_argument("--tickers", help="comma-separated tickers; re-reads their latest report")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        print("EDGAR_USER_AGENT is not set; the SEC refuses undeclared clients")
        return 2
    con = duckdb.connect(args.db)
    migrate(con)
    con.execute(SCHEMA_SQL)
    n = reparse(con)
    if n:
        print("re-cut %d archived documents with parser %s" % (n, PARSER_VERSION))
    todo, ticker = targets(args.sec_db, con, args.limit, args.tickers)
    print("%d annual reports to read" % len(todo))
    fetcher = Fetcher(ua, RATE)
    failed = 0
    # workers fetch and parse; every write happens here, on the one connection
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = {pool.submit(fetch, fetcher, t): t for t in todo}
        for job in as_completed(jobs):
            t = jobs[job]
            try:
                target, url, sha, path, got = job.result()
                store(con, target, url, sha, path, got)
                if isinstance(got, str):
                    print("  %-6s %s  no document: %s" % (ticker.get(t[0]), t[1], got))
                else:
                    print("  %-6s %s  %-5s %-8s %d sections, %d risks%s" % (
                        ticker.get(t[0]), t[1], t[2], got["status"], len(got["sections"]), len(got["risks"]),
                        (" — " + got["note"]) if got["note"] else ""))
            except Exception as e:                                  # noqa: BLE001 — one filing never stops the run
                failed += 1
                err = "%s: %s" % (type(e).__name__, str(e)[:300])
                con.execute("INSERT INTO sec_text_checks VALUES (?,?,?,?,?)", [t[0], _now(), "failed", t[1], err])
                print("  %-6s %s  FAILED: %s" % (ticker.get(t[0]), t[1], err))
            sys.stdout.flush()
    con.execute("CHECKPOINT")
    con.close()
    print("done: %d read, %d failed" % (len(todo) - failed, failed))
    return 1 if failed and failed == len(todo) else 0


if __name__ == "__main__":
    sys.exit(main())
