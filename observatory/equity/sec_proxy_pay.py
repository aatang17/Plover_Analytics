# -*- coding: utf-8 -*-
"""Executive pay from US proxy statements (DEF 14A), for every S&P 500 member.

What is read
------------
Since proxies for fiscal years ending after 16 December 2022, SEC Item 402(v)
("pay versus performance") requires a table tagged in Inline XBRL under the
SEC's own `ecd` taxonomy: the CEO's total pay as reported in the Summary
Compensation Table, the CEO's "compensation actually paid" (the SEC's measure
that marks equity awards to market), the same two figures averaged over the
other named executives, the company's total shareholder return (the value of
$100 invested at the start of the table), its peer group's, net income and a
measure the company chooses itself. EDGAR writes the tagged facts out as a
plain XBRL instance beside each filing (`<primary>_htm.xml`), a few hundred
kilobytes against megabytes for the document, and that instance is what this
reads. Every numeric and short-text fact in it is stored exactly as filed —
the ecd table, its adjustments, the insider-trading flags, and the filer's own
tags — so nothing needs re-fetching when a later view wants more of it.

Each proxy repeats up to five years of the table, so a fiscal year is filed
up to five times. That is point-in-time history for free: the API serves the
latest filing's value by default and every version on request.

Which companies
---------------
The S&P 500 members as held by State Street's SPDR S&P 500 ETF (SPY), from
the fund's daily holdings file. One snapshot per holdings date is stored with
the file's SHA-256; tickers are matched to SEC CIKs through the SEC's own
company_tickers.json, and share classes of one company (GOOGL/GOOG) collapse
to one CIK. A holding that matches no CIK (cash, a transient line) is listed
in the snapshot row, never guessed.

Which proxies
-------------
Every DEF 14A filed since 2023-01-01, found through EDGAR's form-filtered
company feed — the standard submissions file lists only about a year of a
large bank's filings, and JPMorgan's proxies are thousands of filings back.
A proxy without a tagged instance (a fiscal year before the rule, or a filer
that has not tagged) is recorded as 'no_xbrl' and not asked for again.
A revised definitive proxy (DEFR14A) is read like a DEF 14A: Intel filed its
2026 proxy untagged on 23 March and refiled it, tagged, as a DEFR14A the next
day, and fiscal 2025 was missing until this read it. The feed is asked once
per form, since its type filter matches by prefix and DEF 14A never lists a
DEFR14A. Amendments (DEF 14A/A) are not read.

Tables (data/sec.duckdb)
------------------------
    us_index_snapshots   one row per holdings date: source, SHA-256, counts,
                         unmatched lines
    us_index_members     one row per company per snapshot: CIK, tickers,
                         name as the fund lists it, weight (%)
    sec_px_filings       one row per proxy read: accession, filed date,
                         instance URL, SHA-256, raw path, status, facts
    sec_px_facts         one row per fact: prefix (ecd, us-gaap, filer's own),
                         tag, period, dimensions ('axis=member|…', '' for
                         none), numeric value or text, unit, decimals
    sec_px_checks        one row per company per run: when the SEC was
                         reached for it and what came back — the health row
                         dates freshness from these, so a company with no
                         new proxy is checked, not stale
    sec_px_docs          one row per document read for names: the latest
                         proxy's own HTML, or a 10-K CEO certification
                         (Exhibit 31); URL, SHA-256, raw path, status
    sec_px_names         one row per name read: for a proxy, each CEO name or
                         CEO identifier as tagged and the full name the
                         document spells out; for a 10-K, the certifying
                         officer, title and the fiscal year it covers

Names
-----
The tagged name is often a surname ("Mr. Davis"), a placeholder ("Peo1") or
absent (Microsoft tags no name). The proxy's own document spells every name
out, so the latest proxy is read once for the fullest form of each tagged
surname ("Robert M. Davis"). Where nothing is tagged, and to tell which CEO
was in office at the year end, the 10-K for that year is read for the
certifications its principal executive officer signs ("I, Satya Nadella,
certify that"). Names only — no figure comes from either document.

Insert-only. A stored filing is never re-read or corrected; a nil fact is
missing, never zero.

Usage (from observatory/):
    ./.venv/bin/python equity/sec_proxy_pay.py                     # members, then 100 companies
    ./.venv/bin/python equity/sec_proxy_pay.py --limit 0           # every member
    ./.venv/bin/python equity/sec_proxy_pay.py --tickers JPM,AAPL
    ./.venv/bin/python equity/sec_proxy_pay.py --db /tmp/copy.duckdb
    ./.venv/bin/python equity/sec_proxy_pay.py --names-only --limit 0  # names for every member

Companies are taken least-recently-checked first, so repeated runs of
--limit 100 walk the whole index. Needs EDGAR_USER_AGENT
("Company Name contact@domain"). Python 3.9; stdlib + duckdb.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import os
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from app import sec_gate                                          # noqa: E402 — one gate for every SEC request on the host

DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "sec-proxy")

SPY_URL = ("https://www.ssga.com/library-content/products/fund-data/etfs/us/"
           "holdings-daily-us-en-spy.xlsx")
INDEX_NAME = "S&P 500 (SPY holdings)"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%010d"
            "&type=DEF+14A&dateb=&owner=include&count=40&output=atom")
FOLDER_URL = "https://www.sec.gov/Archives/edgar/data/%d/%s/"
TENK_FEED_URL = FEED_URL.replace("type=DEF+14A", "type=10-K")

PARSER_VERSION = "px-1"
NAMES_VERSION = "px-names-3"
EXTRACTOR = "sec-proxy-pay"
SINCE = dt.date(2023, 1, 1)     # pay versus performance: fiscal years ending after 2022-12-16
RATE = 4.0                      # requests per second across all workers; the SEC's ceiling is 10
WORKERS = 2                     # SEC replies take one to ten seconds; fetch two at once
FORMS = ("DEF 14A", "DEFR14A")
MAX_TEXT = 2000                 # a longer text fact is a text block; the raw file keeps it

SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS us_index_snapshot_seq;
CREATE TABLE IF NOT EXISTS us_index_snapshots (
    snapshot_id INTEGER PRIMARY KEY, index_name VARCHAR, source_url VARCHAR, as_of DATE,
    fetched_at TIMESTAMP, sha256 VARCHAR, raw_path VARCHAR, holdings INTEGER,
    companies INTEGER, unmatched VARCHAR);
CREATE TABLE IF NOT EXISTS us_index_members (
    snapshot_id INTEGER, cik BIGINT, ticker VARCHAR, tickers VARCHAR, name VARCHAR,
    weight_pct DOUBLE);
CREATE TABLE IF NOT EXISTS sec_px_filings (
    cik BIGINT, accn VARCHAR, form VARCHAR, filed DATE, instance_url VARCHAR,
    fetched_at TIMESTAMP, sha256 VARCHAR, bytes BIGINT, raw_path VARCHAR,
    parser_version VARCHAR, status VARCHAR, facts INTEGER, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_px_facts (
    accn VARCHAR, cik BIGINT, prefix VARCHAR, tag VARCHAR, period_start DATE,
    period_end DATE, dims VARCHAR, value_num DOUBLE, value_text VARCHAR, unit VARCHAR,
    decimals VARCHAR);
CREATE TABLE IF NOT EXISTS sec_px_checks (
    cik BIGINT, checked_at TIMESTAMP, status VARCHAR, proxies_listed INTEGER,
    proxies_new INTEGER, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_px_docs (
    cik BIGINT, accn VARCHAR, kind VARCHAR, period_end DATE, url VARCHAR,
    fetched_at TIMESTAMP, sha256 VARCHAR, bytes BIGINT, raw_path VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_px_names (
    cik BIGINT, accn VARCHAR, kind VARCHAR, tagged VARCHAR, seq INTEGER, name VARCHAR,
    title VARCHAR, hits INTEGER, period_end DATE, filed DATE, parser_version VARCHAR);
"""

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
ATOM = "{http://www.w3.org/2005/Atom}"
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class PullError(Exception):
    pass


class Fetcher(object):
    """One rate for every worker: requests are spaced across threads, so
    several slow replies can be awaited at once without exceeding RATE.
    SEC requests also go through app/sec_gate.py, which holds the whole host
    to SEC_RATE and stops asking once the SEC refuses (PullError at once)."""

    def __init__(self, ua, rate=RATE):
        self.ua = ua
        self.gap = 1.0 / rate
        self.next = 0.0
        self.lock = threading.Lock()

    def _slot(self):
        with self.lock:
            now = time.time()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)

    def get(self, url, browser=False):
        for attempt in range(4):
            self._slot()
            # State Street serves its file to browsers; the SEC wants the
            # declared agent and refuses anything else.
            ua = "Mozilla/5.0 (compatible; data archive)" if browser else self.ua
            if sec_gate.is_sec(url):
                status, body, _ = sec_gate.get(url, timeout=60, headers={"User-Agent": ua})
                if sec_gate.is_refusal(status, body):
                    raise PullError("the SEC is refusing this server (%s): %s" % (
                        "held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status, url))
                if status == 200:
                    return body
                if status == 404:
                    raise PullError("not found (404): %s" % url)
                if status in (0, 500, 502, 503) and attempt < 3:
                    time.sleep(5 * 3 ** attempt)
                    continue
                raise PullError(("HTTP %s: %s" % (status, url)) if status else "unreachable: %s" % url)
            req = urllib.request.Request(url, headers={"User-Agent": ua,
                                                       "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise PullError("not found (404): %s" % url)
                # The SEC answers a busy spell with 503s for minutes at a
                # time (seen for 70 of 500 companies on 2026-09-25), so the
                # waits are long: 5, 15, 45 seconds.
                if e.code in (403, 429, 500, 502, 503) and attempt < 3:
                    time.sleep(5 * 3 ** attempt)
                    continue
                raise PullError("HTTP %s: %s" % (e.code, url))
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                # A read that times out is the same busy spell, not a fault.
                if attempt < 3:
                    time.sleep(5 * 3 ** attempt)
                    continue
                raise PullError("unreachable: %s" % getattr(e, "reason", e))
        raise PullError("gave up after retries: %s" % url)


def archive(body, kind, name, sha):
    d = os.path.join(RAW_ROOT, kind, dt.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.%s.gz" % (name, sha[:12]))
    if not os.path.exists(path):
        with gzip.open(path, "wb") as f:
            f.write(body)
    return os.path.relpath(path, os.path.join(HERE, ".."))


def _now():
    return dt.datetime.utcnow().replace(microsecond=0)


# ---- the member list ----------------------------------------------------------
def parse_holdings(body):
    """(as_of date, [(name, ticker, weight_pct)]) from the fund's xlsx.

    Read with the standard library: the workbook is one sheet of shared
    strings, header row 'Name | Ticker | … | Weight', the date in 'As of …'.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        strings = ["".join(t.text or "" for t in si.iter(SHEET + "t"))
                   for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(SHEET + "si")]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise PullError("holdings file is not the expected workbook: %s" % e)
    rows = []
    for row in sheet.iter(SHEET + "row"):
        cells = {}
        for c in row.iter(SHEET + "c"):
            v = c.find(SHEET + "v")
            col = "".join(ch for ch in c.get("r", "") if ch.isalpha())
            if v is None:
                continue
            cells[col] = strings[int(v.text)] if c.get("t") == "s" else v.text
        rows.append(cells)
    as_of = None
    for r in rows[:10]:
        m = re.search(r"As of (\d{1,2}-[A-Za-z]{3}-\d{4})", r.get("B") or "")
        if m:
            as_of = dt.datetime.strptime(m.group(1), "%d-%b-%Y").date()
    head = [i for i, r in enumerate(rows) if r.get("A") == "Name" and r.get("B") == "Ticker"]
    if not as_of or not head:
        raise PullError("holdings file has no 'As of' date or no Name/Ticker header")
    out = []
    for r in rows[head[0] + 1:]:
        name, ticker = (r.get("A") or "").strip(), (r.get("B") or "").strip()
        if not name or not ticker:
            continue
        try:
            weight = float(r.get("E")) if r.get("E") not in (None, "", "-") else None
        except ValueError:
            weight = None
        out.append((name, ticker, weight))
    return as_of, out


def refresh_members(con, fetcher):
    """Store today's member list if the fund has published a new date.
    Returns the snapshot id in force."""
    body = fetcher.get(SPY_URL, browser=True)
    sha = hashlib.sha256(body).hexdigest()
    as_of, holdings = parse_holdings(body)
    last = con.execute("SELECT snapshot_id, as_of, sha256 FROM us_index_snapshots WHERE index_name = ? "
                       "ORDER BY snapshot_id DESC LIMIT 1", [INDEX_NAME]).fetchone()
    if last and (last[1] == as_of or last[2] == sha):
        print("  members: holdings as of %s already stored (snapshot %d)" % (last[1], last[0]))
        return last[0]
    raw = archive(body, "spy", "spy-holdings-%s" % as_of.isoformat(), sha)
    tickers = json.loads(fetcher.get(TICKERS_URL).decode("utf-8"))
    cik_of = {v["ticker"].upper(): int(v["cik_str"]) for v in tickers.values()}
    members, unmatched = {}, []
    for name, ticker, weight in holdings:
        cik = cik_of.get(ticker.upper().replace(".", "-"))
        if cik is None:
            unmatched.append("%s (%s)" % (name, ticker))
            continue
        m = members.setdefault(cik, {"tickers": [], "name": name, "weight": 0.0})
        m["tickers"].append(ticker.replace(".", "-"))
        m["weight"] += weight or 0.0
    if len(members) < 450:
        raise PullError("holdings matched only %d companies; expected about 500" % len(members))
    # other index lists (equity/us_index_members.py) share this table: the next
    # id is the table's highest plus one, never a sequence that may lag it
    sid = con.execute("SELECT coalesce(max(snapshot_id), 0) + 1 FROM us_index_snapshots").fetchone()[0]
    con.execute("BEGIN")
    try:
        con.execute("INSERT INTO us_index_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [sid, INDEX_NAME, SPY_URL, as_of, _now(), sha, raw, len(holdings),
                     len(members), "; ".join(unmatched) or None])
        con.executemany("INSERT INTO us_index_members VALUES (?,?,?,?,?,?)",
                        [(sid, cik, m["tickers"][0], ",".join(m["tickers"]), m["name"],
                          round(m["weight"], 6)) for cik, m in members.items()])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("  members: snapshot %d, holdings as of %s — %d companies, unmatched: %s"
          % (sid, as_of, len(members), "; ".join(unmatched) or "none"))
    return sid


# ---- one proxy ------------------------------------------------------------------
def list_proxies(fetcher, cik):
    """[(accn, filed, form)] of this company's proxies (FORMS) since SINCE,
    newest first."""
    out = []
    for want in FORMS:
        out += _listed(fetcher.get(FEED_URL.replace("type=DEF+14A", "type=" + want.replace(" ", "+")) % cik))
    return sorted(set(out), key=lambda r: (r[1], r[0]), reverse=True)


def _listed(body):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise PullError("filing feed is not XML: %s" % e)
    out = []
    for entry in root.iter(ATOM + "entry"):
        content = entry.find(ATOM + "content")
        if content is None:
            continue
        form = (content.findtext(ATOM + "filing-type") or "").strip()
        accn = (content.findtext(ATOM + "accession-number") or "").strip()
        filed = (content.findtext(ATOM + "filing-date") or "").strip()
        if form not in FORMS or not accn or not filed:
            continue
        day = dt.date.fromisoformat(filed)
        if day >= SINCE:
            out.append((accn, day, form))
    return out


def find_instance(fetcher, cik, accn):
    """URL of the filing's extracted XBRL instance, or None if it has none."""
    folder = FOLDER_URL % (cik, accn.replace("-", ""))
    listing = json.loads(fetcher.get(folder + "index.json").decode("utf-8"))
    names = [i["name"] for i in listing.get("directory", {}).get("item", [])]
    hits = sorted(n for n in names if n.endswith("_htm.xml"))
    return folder + hits[0] if hits else None


def parse_instance(body):
    """Facts of an XBRL instance as rows; text blocks and nil facts are dropped."""
    prefixes = {}
    try:
        for event, item in ET.iterparse(io.BytesIO(body), events=("start-ns",)):
            prefix, uri = item
            prefixes.setdefault(uri, prefix)
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise PullError("instance is not XML: %s" % e)
    contexts = {}
    for c in root.findall(XBRLI + "context"):
        p = c.find(XBRLI + "period")
        if p is None:
            continue
        start = p.findtext(XBRLI + "startDate")
        end = p.findtext(XBRLI + "endDate") or p.findtext(XBRLI + "instant")
        dims = []
        for m in c.iter(XBRLDI + "explicitMember"):
            dims.append("%s=%s" % (m.get("dimension"), (m.text or "").strip()))
        for m in c.iter(XBRLDI + "typedMember"):
            val = "".join(m.itertext()).strip()
            dims.append("%s=%s" % (m.get("dimension"), val))
        contexts[c.get("id")] = (start, end, "|".join(sorted(dims)))
    rows = []
    for e in root:
        if not isinstance(e.tag, str) or not e.tag.startswith("{") or e.tag.startswith(XBRLI):
            continue
        uri, tag = e.tag[1:].split("}", 1)
        if uri.startswith("http://www.xbrl.org/") or tag.endswith("TextBlock"):
            continue
        if e.get(XSI_NIL) == "true":
            continue                                   # nil: missing, never zero
        ctx = contexts.get(e.get("contextRef"))
        if ctx is None:
            continue
        text = "".join(e.itertext()).strip()
        if len(text) > MAX_TEXT:
            continue
        num = None
        if e.get("unitRef") is not None:
            try:
                num = float(text)
            except ValueError:
                num = None
        rows.append((prefixes.get(uri, uri), tag, ctx[0], ctx[1], ctx[2], num,
                     None if num is not None else text, e.get("unitRef"), e.get("decimals")))
    # Inline XBRL lets one fact be tagged in several places of the document;
    # the instance then repeats it. One row per distinct fact.
    return list(dict.fromkeys(rows))


def fetch_proxy(fetcher, cik, accn, filed, form=FORMS[0]):
    """Network half of reading one proxy (safe in a worker thread): a dict
    the store half writes, or raises PullError."""
    url = find_instance(fetcher, cik, accn)
    if url is None:
        return {"accn": accn, "filed": filed, "form": form, "url": None}
    body = fetcher.get(url)
    sha = hashlib.sha256(body).hexdigest()
    return {"accn": accn, "filed": filed, "form": form, "url": url, "sha": sha, "bytes": len(body),
            "raw": archive(body, "instances", accn, sha), "rows": parse_instance(body)}


def store_proxy(con, cik, got):
    """Write one fetched proxy; returns its status. Main thread only."""
    if got["url"] is None:
        con.execute("INSERT INTO sec_px_filings (cik, accn, form, filed, fetched_at, "
                    "parser_version, status, facts, detail) VALUES (?,?,?,?,?,?,'no_xbrl',0,?)",
                    [cik, got["accn"], got.get("form", FORMS[0]), got["filed"], _now(), PARSER_VERSION,
                     "no tagged instance in the filing folder"])
        return "no_xbrl"
    rows = got["rows"]
    pvp = [r for r in rows if r[0] == "ecd"]
    con.execute("BEGIN")
    try:
        con.executemany("INSERT INTO sec_px_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [(got["accn"], cik) + r for r in rows])
        con.execute("INSERT INTO sec_px_filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [cik, got["accn"], got.get("form", FORMS[0]), got["filed"], got["url"], _now(), got["sha"],
                     got["bytes"], got["raw"], PARSER_VERSION, "ok", len(rows),
                     None if pvp else "tagged, but no pay-versus-performance facts"])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return "ok" if pvp else "ok_no_pvp"


def fetch_company(fetcher, cik, held):
    """Network half of checking one company: its proxy list and every proxy
    not yet held, fetched and parsed. Failures are returned, not raised."""
    listed = list_proxies(fetcher, cik)
    got = []
    for accn, filed, form in listed:
        if accn in held:
            continue
        try:
            got.append(fetch_proxy(fetcher, cik, accn, filed, form))
        except Exception as e:                                      # noqa: BLE001
            got.append({"accn": accn, "filed": filed, "form": form,
                        "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    return listed, got


def store_company(con, cik, listed, got):
    """Write what fetch_company brought back, and the check itself."""
    notes = []
    for g in got:
        if "error" in g:
            con.execute("INSERT INTO sec_px_filings (cik, accn, form, filed, fetched_at, "
                        "parser_version, status, facts, detail) VALUES (?,?,?,?,?,?,'failed',0,?)",
                        [cik, g["accn"], g.get("form", FORMS[0]), g["filed"], _now(), PARSER_VERSION, g["error"]])
            notes.append("%s FAILED %s" % (g["accn"], g["error"]))
        else:
            notes.append("%s %s" % (g["accn"], store_proxy(con, cik, g)))
    failed = any("FAILED" in n for n in notes)
    new = sum(1 for g in got if "error" not in g)
    con.execute("INSERT INTO sec_px_checks VALUES (?,?,?,?,?,?)",
                [cik, _now(), "failed" if failed else "ok", len(listed), new,
                 "; ".join(notes) or None])
    return len(listed), new, notes


def _held(con, cik):
    return {r[0] for r in con.execute(
        "SELECT accn FROM sec_px_filings WHERE cik = ? AND status IN ('ok', 'no_xbrl')",
        [cik]).fetchall()}


def check_company(con, fetcher, cik):
    """List the company's proxies and read any not yet held (one thread)."""
    listed, got = fetch_company(fetcher, cik, _held(con, cik))
    return store_company(con, cik, listed, got)


# ---- names -----------------------------------------------------------------------
# A CEO's name as tagged is often a surname with a title ("Mr. Davis"), a
# placeholder ("Peo1"), or missing. These read the full name from the
# documents themselves; no figure is ever read from them.
HONORIFIC = re.compile(r"^(?:Mr|Mrs|Ms|Miss|Dr|Messrs|Mses)\.?(?=[\s.]|[A-Z])\.?\s*")
PLACEHOLDER = re.compile(r"(?i)^(?:(?:first|second|third|fourth|former|current|interim)\s+)?"
                         r"(?:peo|ceo|principal executive officer)\s*\d*$|^peo\d")
SUFFIX = re.compile(r",?\s+(Jr\.?|Sr\.?|II|III|IV)$")
DEGREE = re.compile(r",?\s+(?:Ph\.?\s?D\.?|M\.?D\.?|CPA|Esq\.?)$")
NAME_WORD = r"[A-ZÀ-Þ](?:[^\W\d_]|['’\-])*[^\W\d_]"
NAME_TOKEN = r"(?:%s|[A-Z]\.(?:[A-Z]\.)?)" % NAME_WORD
NOT_A_NAME = set("""Mr Mrs Ms Dr Miss Sir Messrs Mses The Our Chairman Chairwoman Chair Chief
Executive Officer Officers President CEO PEO NEO NEOs Non Board Director Directors And Of
Since In By For With To As At On From Former Interim Lead Independent Vice Dear Prof
Professor Annual Meeting Proxy Statement Company Compensation Committee Total Summary Name
Title None Table Contents January February March April May June July August September
October November December""".split())
CEO_WORDS = re.compile(r"Chief Executive|\bCEO\b|Principal Executive|\bPEO\b")
CERTIFY = re.compile(r"\bI,\s+(.{3,80}?),\s+(?:hereby\s+)?certify", re.S | re.I)


def doc_text(body):
    """A filing document's visible text, one space between words."""
    t = body.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ").replace("’", "'")
    return " ".join(t.split())


def member_label(member):
    """'mrk:MrDavisMember' -> 'Mr Davis': the filer's identifier, spaced."""
    local = member.split(":", 1)[-1]
    if local.endswith("Member"):
        local = local[:-len("Member")]
    local = local.replace(".", ". ")
    return " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", local).split())


def tagged_names(raw):
    """The people a tagged name string names: [{"name", "surname_only"}].

    Drops footnote marks ("(1)Ms. Harris"), titles, degrees and placeholders
    ("Peo1", "First PEO"), splits "Mr. Armstrong and Mr. Zamarin", and mends
    an initial the tagging glued to the surname ("Travis DStice")."""
    s = " ".join((raw or "").replace("’", "'").split())
    s = re.sub(r"^\(?\d{1,2}\)?\.?\s*(?=\D)", "", s)
    s = re.sub(r"^(?:CEO|PEO)(?=[A-Z][a-z])", "", s)
    s = re.sub(r"^#\d+\s*-\s*", "", s)                     # "#5 - Marlene M. Santos"
    s = re.sub(r"\s*\([^)]*\)", "", s)                     # "Michele Buck (former)"
    s = re.sub(r"^.*Chief Executive Officer,\s*", "", s)    # "Reflects compensation for our CEO, …"
    s = re.split(r",\s*(?:respectively|who|each|based)\b", s)[0]
    if not s or len(s) > 80 or PLACEHOLDER.match(s):
        return []
    out, seen = [], set()
    # "•2023 and 2022: Michael J. Hennigan•2021: Mr. Hennigan", "Mr. Armstrong
    # and Mr. Zamarin", "David J. Rosa / Gary S. Guthart", "A, B"
    for part in re.split(r"\s*•\s*|\s+(?:and|&)\s+|\s*/\s*|,(?!\s*(?:Jr|Sr|II|III|IV|Ph|M\.?D)\b)\s*", s):
        part = re.sub(r"^\d{4}:\s*", "", part)
        part = part.strip(" ,;")
        bare = HONORIFIC.sub("", part)
        honor = bare != part
        while DEGREE.search(bare):
            bare = DEGREE.sub("", bare)
        bare = re.sub(r"\b([A-Z])(?=[A-Z][a-z]{2,})", r"\1. ", bare)
        bare = " ".join(bare.split()).strip(" ,")
        bare = re.sub(r",?\s+(Jr|Sr)\.?$", r", \1.", bare)
        if not SUFFIX.search(bare):
            bare = bare.rstrip(".")            # "Mr. Manifold."
        if not bare or PLACEHOLDER.match(bare) or not re.search(r"[a-z]", bare):
            continue
        words = SUFFIX.sub("", bare).split()
        if bare not in seen:
            seen.add(bare)
            out.append({"name": bare, "surname_only": honor or len(words) < 2})
    # "Michael J. Hennigan … Mr. Hennigan": one person, the fuller spelling.
    full = {SUFFIX.sub("", o["name"]).split()[-1] for o in out if not o["surname_only"]}
    return [o for o in out if not (o["surname_only"] and len(o["name"].split()) == 1
                                   and o["name"] in full)]


def spelled_out(text, surname, bare=False):
    """(fullest name ending in surname, times seen): the spelling written
    most often near the CEO's title, then most often overall. With `bare`
    (a key that already holds a given name, "Milan Galik"), the key written
    on its own counts too, so a list of names before it ("Earl H. Nemser
    Milan Galik") does not win."""
    pat = re.compile(r"((?:%s\s+){0,3})%s(?![^\W\d_])(,?\s+(?:Jr\.|Sr\.|II|III|IV)(?![^\W\d_]))?"
                     % (NAME_TOKEN, re.escape(surname)))
    counts, near, one = {}, {}, {}
    for m in pat.finditer(text):
        keep = []
        for t in reversed(m.group(1).split()):
            if t.rstrip(".") in NOT_A_NAME or (len(t) > 2 and t.isupper()):
                break
            keep.insert(0, t)
        if not any(re.fullmatch(NAME_WORD, t) for t in keep):
            # Initials alone are no given name; the key alone counts only as
            # a name in its own right, not after "Mr."
            if not bare or re.search(r"(?:Mr|Ms|Mrs|Dr|Messrs)\.?\s*$", text[max(0, m.start() - 8): m.start()]):
                continue
            keep = []
        name = " ".join(keep + [surname]) + (m.group(2) or "")
        counts[name] = counts.get(name, 0) + 1
        one[name] = len(keep) == 1
        if CEO_WORDS.search(text[max(0, m.start() - 300): m.end() + 300]):
            near[name] = near.get(name, 0) + 1
    if not counts:
        return None, 0
    pool = list(counts)
    if bare:
        # A two-word surname takes the one given name written before it again
        # and again ("Jon Vander Ark", "Michelle Johnston Holthaus").
        pool = [n for n in counts if one[n] and counts[n] >= 2] or pool
    best = max(pool, key=lambda n: (near.get(n, 0), counts[n], len(n)))
    return best, counts[best]


def resolve_name(text, item):
    """(display name, times seen in the document or None if not looked up)."""
    name = item["name"]
    if not item["surname_only"]:
        return name, None
    bare = SUFFIX.sub("", name)
    keys = [bare] + ([bare.split()[-1]] if len(bare.split()) > 1 else [])
    for key in keys:
        full, hits = spelled_out(text, key, bare=len(key.split()) > 1)
        if full:
            return full, hits
    return name, 0


TITLE_START = re.compile(r"\b(?:Co-|Interim|Chair|Chief|Principal|President|Executive|Senior|Vice)\b")
TITLE_END = re.compile(r"\s+(?:Date|Dated|CERTIFICATION|Certification|Exhibit|EXHIBIT)\b|\s*/s/|\s+(?:January|February|March|April|May|June|July|"
                       r"August|September|October|November|December)\s+\d")


def parse_certifications(text):
    """[(name, title)] of each officer signing a Section 302 certification
    in the document; one exhibit can hold two (Procter & Gamble's EX-31)."""
    out = []
    hits = list(CERTIFY.finditer(text))
    for k, m in enumerate(hits):
        name = HONORIFIC.sub("", " ".join(m.group(1).split())).strip(" ,")
        # "I, Ariane Gorin, Chief Executive Officer of Expedia Group, Inc., certify"
        name = re.split(r",(?!\s*(?:Jr|Sr|II|III|IV)\b)", name)[0].strip()
        if name.isupper():
            name = name.title()
        # The title follows this certification's own signature.
        stop = hits[k + 1].start() if k + 1 < len(hits) else len(text)
        sig = text.find("/s/", m.end(), stop)
        seg = text[sig + 3: sig + 300] if sig >= 0 else ""
        if sig < 0 and name.split():
            # Signed without "/s/": the title follows the last time the name is written.
            at = text.rfind(SUFFIX.sub("", name).split()[-1], m.end(), stop)
            seg = text[at: at + 300] if at >= 0 else ""
        start = TITLE_START.search(seg)
        title = None
        if start:
            seg = seg[start.start():]
            end = TITLE_END.search(seg)
            title = " ".join(seg[:end.start() if end else 160].replace("\u200b", " ").split()).strip(" ,") or None
        out.append((name, title))
    return out


def names_work(con, cik):
    """What the name readers still need for one company: its latest proxy's
    document (if not read) and the 10-K certifications for that proxy's
    latest year and any year it names no CEO in (if not read)."""
    # The latest proxy that tags the CEO's pay: American Tower's 2026 proxy
    # tags its table without it, and the list shows 2024 from the 2025 one.
    row = con.execute("""
        SELECT x.accn, x.filed, x.instance_url FROM sec_px_filings x
        WHERE x.cik = ? AND x.status = 'ok' AND x.detail IS NULL
          AND EXISTS (SELECT 1 FROM sec_px_facts f WHERE f.accn = x.accn
                      AND f.tag IN ('PeoTotalCompAmt', 'PeoActuallyPaidCompAmt'))
        ORDER BY x.filed DESC, x.accn DESC LIMIT 1""", [cik]).fetchone()
    if row is None:
        return None
    accn, filed, url = row
    done = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT kind, accn, period_end FROM sec_px_docs WHERE cik = ? AND status IN ('ok', 'none')",
        [cik]).fetchall()}
    tagged = [r[0] for r in con.execute("""
        SELECT DISTINCT value_text FROM sec_px_facts
        WHERE accn = ? AND tag = 'PeoName' AND value_text IS NOT NULL
          AND (dims = '' OR dims NOT LIKE '%%NonPeoNeoMember%%')""", [accn]).fetchall()]
    tagged += [p.split("ecd:IndividualAxis=", 1)[1] for (d,) in con.execute("""
        SELECT DISTINCT dims FROM sec_px_facts
        WHERE accn = ? AND tag IN ('PeoTotalCompAmt', 'PeoActuallyPaidCompAmt', 'PeoName')
          AND dims LIKE '%%ecd:IndividualAxis=%%' AND dims NOT LIKE '%%NonPeoNeoMember%%'""",
        [accn]).fetchall() for p in d.split("|") if p.startswith("ecd:IndividualAxis=")]
    years = con.execute("""
        SELECT period_start, period_end FROM sec_px_facts
        WHERE accn = ? AND tag IN ('PeoTotalCompAmt', 'PeoActuallyPaidCompAmt')
          AND date_diff('day', period_start, period_end) BETWEEN 300 AND 380
        GROUP BY ALL ORDER BY period_end""", [accn]).fetchall()
    # A year is unnamed only if no proxy names it: CSX names 2025 in its 2026
    # proxy and 2024 in its 2025 one. A name tagged once for the whole table
    # (Ford: 2021 to 2025) names every year it spans.
    named = con.execute("""
        SELECT DISTINCT coalesce(f.period_start, f.period_end), f.period_end
        FROM sec_px_facts f JOIN sec_px_filings x ON x.accn = f.accn AND x.status = 'ok'
        WHERE f.cik = ? AND f.tag = 'PeoName'""", [cik]).fetchall()
    want = set()
    for start, end in years:
        if end == years[-1][1] or not any(
                s <= end and start <= e + dt.timedelta(days=45) for s, e in named):
            want.add(end)
    # A document read by an earlier version of the readers is read again
    # from its archived copy, not fetched again.
    old = con.execute("""
        SELECT d.raw_path FROM sec_px_docs d
        WHERE d.cik = ? AND d.accn = ? AND d.kind = 'proxy' AND d.status = 'ok'
          AND EXISTS (SELECT 1 FROM sec_px_names n WHERE n.accn = d.accn AND n.kind = 'proxy')
          AND NOT EXISTS (SELECT 1 FROM sec_px_names n WHERE n.accn = d.accn AND n.kind = 'proxy'
                          AND n.parser_version = ?)""", [cik, accn, NAMES_VERSION]).fetchone()
    return {"accn": accn, "filed": filed,
            "doc_url": url.replace("_htm.xml", ".htm") if url else None,
            "need_doc": ("proxy", accn, None) not in done,
            "reread": old[0] if old else None,
            "tagged": sorted(set(tagged)),
            "certs": sorted(e for e in want
                            if not any(k == "cert" and p == e for k, _, p in done))}


def _tenk_for(fetcher, cik, ends):
    """{fiscal year end: (accn, filed, folder, [(type, filename)])} for the
    10-K covering each year end, from the form-filtered feed."""
    root = ET.fromstring(fetcher.get(TENK_FEED_URL % cik))
    entries = []
    for entry in root.iter(ATOM + "entry"):
        c = entry.find(ATOM + "content")
        if c is None or (c.findtext(ATOM + "filing-type") or "").strip() != "10-K":
            continue
        entries.append(((c.findtext(ATOM + "accession-number") or "").strip(),
                        dt.date.fromisoformat((c.findtext(ATOM + "filing-date") or "").strip())))
    out = {}
    for end in ends:
        near = sorted((f, a) for a, f in entries if end < f <= end + dt.timedelta(days=200))
        for filed, accn in near[:2]:
            folder = FOLDER_URL % (cik, accn.replace("-", ""))
            head = fetcher.get(folder + "%s-index-headers.html" % accn).decode("latin-1")
            m = re.search(r"CONFORMED PERIOD OF REPORT:\s*(\d{8})", head)
            period = m and dt.datetime.strptime(m.group(1), "%Y%m%d").date()
            if period and abs((period - end).days) <= 10:
                docs = re.findall(r"&lt;TYPE&gt;([^\n&<]+).*?&lt;FILENAME&gt;([^\n&<]+)", head, re.S)
                out[end] = (accn, filed, folder, [(t.strip(), n.strip()) for t, n in docs])
                break
    return out


def _doc_names(body, tagged):
    """[(as tagged, seq, full name, times seen)] for one proxy document."""
    text = doc_text(body)
    names = []
    for raw in tagged:
        label = member_label(raw) if re.fullmatch(r"[\w\-]+:[\w.\-]+", raw) else raw
        for i, item in enumerate(tagged_names(label)):
            full, hits = resolve_name(text, item)
            names.append((raw, i, full, hits))
    return names


def fetch_names(fetcher, cik, work):
    """Network half of the name readers (safe in a worker thread)."""
    got = {"doc": None, "certs": [], "reread": None}
    if not work["need_doc"] and work.get("reread"):
        with gzip.open(os.path.join(HERE, "..", work["reread"])) as f:
            got["reread"] = _doc_names(f.read(), work["tagged"])
    if work["need_doc"] and work["doc_url"]:
        try:
            body = fetcher.get(work["doc_url"])
            sha = hashlib.sha256(body).hexdigest()
            names = _doc_names(body, work["tagged"])
            got["doc"] = {"url": work["doc_url"], "sha": sha, "bytes": len(body),
                          "raw": archive(body, "documents", work["accn"], sha), "names": names}
        except Exception as e:                                      # noqa: BLE001
            got["doc"] = {"url": work["doc_url"], "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    if work["certs"]:
        try:
            tenks = _tenk_for(fetcher, cik, work["certs"])
        except Exception as e:                                      # noqa: BLE001
            tenks = None
            for end in work["certs"]:
                got["certs"].append({"end": end, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
        for end in (work["certs"] if tenks is not None else []):
            if end not in tenks:
                got["certs"].append({"end": end, "none": "no 10-K for this fiscal year in the feed"})
                continue
            accn, filed, folder, docs = tenks[end]
            for typ, name in docs:
                if not typ.startswith("EX-31"):
                    continue
                try:
                    body = fetcher.get(folder + name)
                    sha = hashlib.sha256(body).hexdigest()
                    who = parse_certifications(doc_text(body))
                    got["certs"].append({"end": end, "accn": accn, "filed": filed, "type": typ,
                                         "url": folder + name, "sha": sha, "bytes": len(body),
                                         "raw": archive(body, "certifications", accn + "." + typ, sha),
                                         "who": who})
                except Exception as e:                              # noqa: BLE001
                    got["certs"].append({"end": end, "url": folder + name,
                                         "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
            if not any(c["end"] == end for c in got["certs"]):
                got["certs"].append({"end": end, "none": "10-K %s lists no Exhibit 31" % accn})
    return got


def store_names(con, cik, work, got):
    """Write what fetch_names brought back; returns a short note."""
    notes = []
    con.execute("BEGIN")
    try:
        for raw, i, full, hits in got.get("reread") or []:
            con.execute("INSERT INTO sec_px_names VALUES (?,?,'proxy',?,?,?,NULL,?,NULL,?,?)",
                        [cik, work["accn"], raw, i, full, hits, work["filed"], NAMES_VERSION])
        if got.get("reread"):
            notes.append("%d names re-read" % len(got["reread"]))
        d = got["doc"]
        if d is not None:
            if "error" in d:
                con.execute("INSERT INTO sec_px_docs VALUES (?,?,'proxy',NULL,?,?,NULL,NULL,NULL,?,'failed',?)",
                            [cik, work["accn"], d["url"], _now(), NAMES_VERSION, d["error"]])
                notes.append("proxy document FAILED " + d["error"])
            else:
                con.execute("INSERT INTO sec_px_docs VALUES (?,?,'proxy',NULL,?,?,?,?,?,?,'ok',NULL)",
                            [cik, work["accn"], d["url"], _now(), d["sha"], d["bytes"], d["raw"],
                             NAMES_VERSION])
                for raw, i, full, hits in d["names"]:
                    con.execute("INSERT INTO sec_px_names VALUES (?,?,'proxy',?,?,?,NULL,?,NULL,?,?)",
                                [cik, work["accn"], raw, i, full, hits, work["filed"], NAMES_VERSION])
                notes.append("%d names" % len(d["names"]))
        ends = {}
        for c in got["certs"]:
            ends.setdefault(c["end"], []).append(c)
        for end, cs in ends.items():
            ok = [c for c in cs if "sha" in c]
            bad = [c for c in cs if "error" in c]
            for c in (ok if not bad else []):
                con.execute("INSERT INTO sec_px_docs VALUES (?,?,'cert',?,?,?,?,?,?,?,'ok',?)",
                            [cik, c["accn"], end, c["url"], _now(), c["sha"], c["bytes"], c["raw"],
                             NAMES_VERSION, c["type"]])
                for i, (who, title) in enumerate(c["who"]):
                    con.execute("INSERT INTO sec_px_names VALUES (?,?,'cert',?,?,?,?,NULL,?,?,?)",
                                [cik, c["accn"], c["type"], i, who, title, end,
                                 c["filed"], NAMES_VERSION])
            if bad:
                # A partial read stores nothing and is retried whole next time.
                con.execute("INSERT INTO sec_px_docs VALUES (?,NULL,'cert',?,?,?,NULL,NULL,NULL,?,'failed',?)",
                            [cik, end, bad[0].get("url"), _now(), NAMES_VERSION, bad[0]["error"]])
                notes.append("%s certification FAILED %s" % (end, bad[0]["error"]))
            elif not ok:
                con.execute("INSERT INTO sec_px_docs VALUES (?,NULL,'cert',?,NULL,?,NULL,NULL,NULL,?,'none',?)",
                            [cik, end, _now(), NAMES_VERSION, cs[0].get("none")])
                notes.append("%s no certification" % end)
            else:
                notes.append("%s %d certifications" % (end, len(ok)))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return notes


# ---- run -------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=100,
                    help="companies to check this run, least recently checked first (0 = all)")
    ap.add_argument("--tickers", help="comma-separated tickers from the member list")
    ap.add_argument("--workers", type=int, default=WORKERS,
                    help="companies fetched at once; the request rate is shared (default 2)")
    ap.add_argument("--skip-members", action="store_true",
                    help="use the stored member list; do not fetch the fund's file")
    ap.add_argument("--names-only", action="store_true",
                    help="skip the proxy checks; only read CEO names the stored proxies still need")
    args = ap.parse_args()
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        print("EDGAR_USER_AGENT is not set; the SEC refuses undeclared clients")
        return 2
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    fetcher = Fetcher(ua)
    if not args.skip_members and not args.names_only:
        try:
            refresh_members(con, fetcher)
        except Exception as e:                                      # noqa: BLE001
            print("  members: FAILED %s: %s — the stored list stays in force"
                  % (type(e).__name__, str(e)[:300]))
    snap = con.execute("SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = ?", [INDEX_NAME]).fetchone()[0]
    if snap is None:
        print("no member list stored; nothing to check")
        con.close()
        return 1
    # Never checked first, then those whose last check failed, then the
    # longest since a good check — so a busy day's failures are retried
    # before anything else, not a whole cycle later.
    rows = con.execute("""
        SELECT m.cik, m.ticker, max(c.checked_at) AS last
        FROM us_index_members m LEFT JOIN sec_px_checks c USING (cik)
        WHERE m.snapshot_id = ?
        GROUP BY m.cik, m.ticker, m.weight_pct
        ORDER BY last IS NULL DESC,
                 coalesce(arg_max(c.status, c.checked_at), '') = 'failed' DESC,
                 last, m.weight_pct DESC""", [snap]).fetchall()
    if args.tickers:
        want = {t.strip().upper() for t in args.tickers.split(",")}
        rows = [r for r in rows if r[1].upper() in want]
    elif args.limit:
        rows = rows[:args.limit]
    failed = []
    # Workers only fetch and parse; every write happens here, in one thread,
    # on the one connection.
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = {} if args.names_only else {
            pool.submit(fetch_company, fetcher, cik, _held(con, cik)): (cik, ticker)
            for cik, ticker, _ in rows}
        for job in as_completed(jobs):
            cik, ticker = jobs[job]
            try:
                listed, got = job.result()
                listed, new, notes = store_company(con, cik, listed, got)
                print("  %-6s %10d  %d proxies since %s, %d new%s" % (
                    ticker, cik, listed, SINCE, new,
                    (" — " + "; ".join(notes)) if notes else ""))
                if any("FAILED" in n for n in notes):
                    failed.append(ticker)
            except Exception as e:                                  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, str(e)[:300])
                print("  %-6s %10d  FAILED: %s — stored proxies unchanged" % (ticker, cik, err))
                con.execute("INSERT INTO sec_px_checks VALUES (?,?,?,?,?,?)",
                            [cik, _now(), "failed", None, 0, err])
                failed.append(ticker)
            sys.stdout.flush()
    # Names second, for the same companies: they need the proxies just stored.
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        work = {cik: (ticker, names_work(con, cik)) for cik, ticker, _ in rows}
        jobs = {pool.submit(fetch_names, fetcher, cik, w): cik for cik, (ticker, w) in work.items()
                if w and (w["need_doc"] or w["certs"] or w["reread"])}
        for job in as_completed(jobs):
            cik = jobs[job]
            ticker, w = work[cik]
            try:
                notes = store_names(con, cik, w, job.result())
                print("  %-6s %10d  names: %s" % (ticker, cik, "; ".join(notes) or "nothing new"))
                if any("FAILED" in n for n in notes):
                    failed.append(ticker)
            except Exception as e:                                  # noqa: BLE001
                print("  %-6s %10d  names FAILED: %s: %s" % (ticker, cik, type(e).__name__, str(e)[:300]))
                failed.append(ticker)
            sys.stdout.flush()
    con.close()
    if failed:
        print("ATTENTION proxy pay checks failed: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
