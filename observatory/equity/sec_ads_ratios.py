# -*- coding: utf-8 -*-
"""How many shares one US-listed depositary share stands for, from the filing.

Why this exists
---------------
A foreign company's US listing is usually an American Depositary Share (ADS),
and one ADS can stand for any number of the company's own shares: one for
Arm, three for HDFC Bank, eight for Alibaba, thirteen for BeOne, four
thousand for Scinai. The price feed quotes the ADS; the filing counts the
underlying shares. Multiplying one by the other without the ratio overstated
HDFC Bank's market value three times and Alibaba's eight times (found
2026-10-19). The ratio is nowhere in the SEC's structured data, so this
loader reads it from the filing itself, for every company whose latest
annual report is a 20-F and every 10-K filer incorporated outside the US.

Where the ratio is read, in order
---------------------------------
1. The cover page's Section 12(b) table, tagged dei:Security12bTitle in the
   filing's XBRL instance: "American Depositary Shares, each representing
   eight Ordinary Shares". Most filers state it there.
2. The filing's own text, when the title only says "American Depositary
   Shares" (Toyota, Sony, JD.com, Baidu) or lists the underlying share
   alone (TSMC): the first sentence of the form "each ADS represents five
   common shares". The sentence is kept as the source.
3. Nothing: the filing names a depositary share but never states the ratio
   (or states several and no one of them prevails). The company's market
   value is then left blank with a note, never guessed.

A filing whose titles name no depositary share and whose text never mentions
one is a direct listing: the US ticker is the share itself, ratio one.

Tables (data/sec.duckdb), insert-only
-------------------------------------
    sec_ads_filings  one row per filing read: accession, form, filed, the
                     instance and document URLs, the document's SHA-256 and
                     archive path, status
    sec_ads_ratios   one row per filing: listing ('ads', 'direct',
                     'unknown'), ratio (shares per listed share; NULL when
                     unknown), the cover title, the sentence it came from,
                     where it was read ('cover title', 'document', 'no
                     depositary share named')
    sec_ads_checks   one row per company per run that was asked of the SEC
    sec_ads_runs     one row per run

A later filing is a new row; the reader (app/sec_shares.py) takes the newest
filing. A filing read again (--reread) replaces its own earlier reading, the
parser having improved; the archived document is what it was read from. The
cover share counts of a 20-F filer the frames files miss (per-class or IFRS
counts) are stored into sec_cover_shares from the same instance, since it has
been fetched anyway. Documents are archived gzip'd under data/raw/sec-ads/ with their
SHA-256 before they are parsed.

Usage (from observatory/):
    ./.venv/bin/python equity/sec_ads_ratios.py                    # 400 least recently checked
    ./.venv/bin/python equity/sec_ads_ratios.py --limit 0          # every foreign filer
    ./.venv/bin/python equity/sec_ads_ratios.py --tickers HDB,BABA
    ./.venv/bin/python equity/sec_ads_ratios.py --db /tmp/copy.duckdb

Needs EDGAR_USER_AGENT ("Company Name contact@domain"). Python 3.9; stdlib +
duckdb.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sec_proxy_pay as px  # noqa: E402 — the SEC fetcher and instance parser are shared
import sec_cover_shares as cover  # noqa: E402 — the cover-count parser and tables, for 20-F filers the frames miss

DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "sec-ads")
FOLDER_URL = "https://www.sec.gov/Archives/edgar/data/%d/%s/"
PARSER_VERSION = "ads-1"
ANNUAL = ("20-F", "20-F/A", "10-K", "10-K/A", "10-KT")
DOC_MAX = 40 * 1024 * 1024          # a filing document longer than this is not read

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_ads_filings (
    accn VARCHAR PRIMARY KEY, cik BIGINT, form VARCHAR, filed DATE, instance_url VARCHAR, doc_url VARCHAR,
    doc_sha256 VARCHAR, doc_raw_path VARCHAR, status VARCHAR, parser_version VARCHAR, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sec_ads_ratios (
    cik BIGINT, accn VARCHAR, ticker VARCHAR, listing VARCHAR, ratio DOUBLE, title VARCHAR, quote VARCHAR,
    source VARCHAR, first_seen TIMESTAMP, PRIMARY KEY (cik, accn));
CREATE TABLE IF NOT EXISTS sec_ads_checks (cik BIGINT, checked_at TIMESTAMP, outcome VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ads_runs (run_at TIMESTAMP, status VARCHAR, read INTEGER, failed INTEGER, note VARCHAR);
"""


class RatioError(Exception):
    pass


def _now():
    return dt.datetime.utcnow().replace(microsecond=0)


def archive(body, name, sha):
    d = os.path.join(RAW_ROOT, dt.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.%s.htm.gz" % (name, sha[:12]))
    if not os.path.exists(path):
        import gzip
        with gzip.open(path, "wb") as f:
            f.write(body)
    return os.path.relpath(path, os.path.join(HERE, ".."))


# ---------------------------------------------------------------- numbers
_SMALL = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
          "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_FRACTION = {"half": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5, "sixth": 6, "eighth": 8, "tenth": 10,
             "twentieth": 20, "fortieth": 40, "fiftieth": 50, "hundredth": 100, "thousandth": 1000}
NUM = (r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|\d+/\d+|"
       r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
       r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
       r"(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?(?:\s+hundred|\s+thousand)?(?:\s+and\s+[a-z]+)?|"
       r"one[\s-](?:half|third|quarter|fourth|fifth|sixth|eighth|tenth|twentieth|fortieth|fiftieth|hundredth|thousandth)|"
       r"an?\s+(?:half|third|quarter|fourth|fifth|sixth|eighth|tenth)|half\s+(?:of\s+)?an?|half|"
       r"a)")


def number(text):
    """'eight' -> 8.0, '4,000' -> 4000.0, 'one-half' -> 0.5, '1/40' -> 0.025; None when unreadable."""
    t = (text or "").strip().lower().replace("‑", "-")
    if not t:
        return None
    if t in ("a", "an", "an interest in"):
        return 1.0
    if t in ("half", "half a", "half an", "half of a", "half of an"):
        return 0.5
    m = re.match(r"^(?:one|an?)[\s-](half|third|quarter|fourth|fifth|sixth|eighth|tenth|twentieth|fortieth|fiftieth|hundredth|thousandth)$", t)
    if m:
        return 1.0 / _FRACTION[m.group(1)]
    m = re.match(r"^(\d+)/(\d+)$", t)
    if m and int(m.group(2)):
        return float(m.group(1)) / float(m.group(2))
    m = re.match(r"^\d{1,3}(?:,\d{3})+$|^\d+(?:\.\d+)?$", t)
    if m:
        return float(t.replace(",", ""))
    words = re.split(r"[\s-]+", t)
    total, mult = 0.0, 1.0
    for w in words:
        if w == "and":
            continue
        if w in _SMALL:
            total += _SMALL[w]
        elif w == "hundred":
            total = (total or 1) * 100
        elif w == "thousand":
            total = (total or 1) * 1000
        else:
            return None
    return total or None


# ---------------------------------------------------------------- the ratio in words
_SHARE = (r"(?:\((?:[\d,.]+|[a-z]+)\)\s+)?(?:of\s+)?(?:our\s+|the\s+|the\s+company'?s\s+|its\s+)?"
          r"(?:\d+(?:\.\d+)?p\s+)?"                                   # "four 25p ordinary shares"
          r"(?:[a-z][a-z0-9&.'-]*\s+){0,2}?"                             # "one RELX PLC ordinary share"
          r"(?:(?:fully[\s-]paid|class\s+[a-z0-9]+|[abc]|ordinary|common|equity|voting|non[\s-]voting|deferred|"
          r"registered|bearer|subordinate|limited\s+voting|series\s+[a-z0-9]+|new)\s+)*"
          r"(?:shares?|stock|units?)")
_HISTORICAL = re.compile(r"prior\s+to|previously|formerly|before\s+(?:the|that|this|\w+\s+\d)|until|used\s+to|"
                         r"was\s+changed|were\s+changed|changed\s+from|effective\s+(?:date\s+)?of\s+the\s+change|no\s+longer", re.I)
# "each representing eight Ordinary Shares", "each of which represents one share",
# "each represented by one Equity Share", "representing the right to receive 25 ordinary shares"
_REPRESENT = re.compile(
    r"(?:each\s+(?:of\s+which\s+)?|one\s+(?:american\s+deposit[ao]ry\s+(?:share|receipt)|ads|adr)\s+)?"
    r"represent(?:s|ing|ed)?(?:\s*by)?\s*(?:the\s+right\s+to\s+receive\s+)?(?:an?\s+interest\s+in\s+)?"
    r"(" + NUM + r")\s+(?:\((?:[\d,.]+|[a-z]+)\)\s+)?(?:of\s+)?(?:an?\s+|one\s+)?" + _SHARE, re.I)
# "five common shares per ADS", "2 ordinary shares for each ADS", "ADS to ordinary share ratio of 1:8"
_PER_ADS = re.compile(r"(" + NUM + r")\s+" + _SHARE + r"\s+(?:for|per)\s+(?:each\s+|one\s+)?(?:american\s+deposit[ao]ry\s+(?:share|receipt)|ads|adr)", re.I)
_RATIO = re.compile(r"(?:ads|adr|american\s+deposit[ao]ry\s+(?:share|receipt))s?\s*(?:to|:|/)\s*(?:ordinary\s+|common\s+|class\s+[a-z]\s+|equity\s+)?shares?\s+ratio\s+"
                    r"(?:of|is|was|to|at|from\s+[\d:]+\s+to)?\s*(?:one|1)\s*(?:ads|adr)?\s*(?::|to|for)\s*(" + NUM + r")", re.I)
_ADS_WORDS = re.compile(r"american\s+deposit[ao]ry|\bADSs?\b|\bADRs?\b|global\s+deposit[ao]ry", re.I)
_PREFERRED = re.compile(r"preferred|preference|\bnotes?\b|debenture|warrant|\bunits?\s+each\s+consisting", re.I)


def ratio_in_title(title):
    """Shares per depositary share stated in a Section 12(b) title, or None."""
    t = html.unescape(title or "")
    if not _ADS_WORDS.search(t) or _PREFERRED.search(t):
        return None
    for rx in (_REPRESENT, _PER_ADS, _RATIO):
        m = rx.search(t)
        if m:
            if re.search(r"\bunits?\s*$", m.group(0).strip(), re.I):
                return None        # "each representing 10 units": a unit is several shares (Coca-Cola FEMSA: 3 B + 5 L)
            return number(m.group(1))
    return None


def _text(body):
    """The filing document as plain text, entities decoded, tags dropped."""
    s = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s)


_LEAD = re.compile(r"(?:each|one|every|an?)\s+(?:of\s+(?:our\s+|the\s+)?)?(?:american\s+deposit[ao]ry\s+(?:share|receipt)|ads|adr)s?"
                   r"(?:\s*\([^)]{0,40}\))?\s+(?:will\s+|shall\s+|currently\s+|now\s+)?represent(?:s|ing|ed)?(?:\s*by)?\s*"
                   r"(?:the\s+right\s+to\s+receive\s+)?(?:an?\s+interest\s+in\s+)?(" + NUM + r")\s+(?:\((?:[\d,.]+|[a-z]+)\)\s+)?(?:of\s+)?(?:an?\s+|one\s+)?" + _SHARE, re.I)
# the cover table's own wording, repeated in the document: "American Depositary Shares, each representing eight ..."
_LEAD0 = re.compile(r"(?:american\s+deposit[ao]ry\s+(?:share|receipt)|ads|adr)s?[^.;:]{0,60}?\beach\s+(?:of\s+which\s+)?represent(?:s|ing|ed)?(?:\s*by)?\s*"
                    r"(?:the\s+right\s+to\s+receive\s+)?(?:an?\s+interest\s+in\s+)?(" + NUM + r")\s+(?:\((?:[\d,.]+|[a-z]+)\)\s+)?(?:of\s+)?(?:an?\s+|one\s+)?" + _SHARE, re.I)
_LEAD2 = re.compile(r"(" + NUM + r")\s+" + _SHARE + r"\s+(?:for|per|represented\s+by|underlying)\s+(?:each\s+|one\s+|every\s+)?(?:american\s+deposit[ao]ry\s+(?:share|receipt)|ads|adr)\b", re.I)


def ratios_in_document(body):
    """[(ratio, sentence)] for every ratio statement in a filing's text."""
    text = _text(body)
    out = []
    for rx in (_LEAD, _LEAD0, _LEAD2, _RATIO):
        for m in rx.finditer(text):
            if _PREFERRED.search(m.group(0)):
                continue
            lead = text[max(0, m.start() - 80):m.start()]
            if _HISTORICAL.search(lead) or _HISTORICAL.search(m.group(0)):
                continue                                   # "prior to 2014, each ADS represented ..." is history
            if re.search(r"\bunits?\s*$", m.group(0).strip(), re.I):
                continue                                   # units are bundles of shares, not shares
            n = number(m.group(1))
            if n:
                a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
                out.append((n, text[a:b].strip()))
    return out


def decide(ratios):
    """(ratio, quote) from the document's statements: the ratio most often
    stated; None when two ratios tie or nothing was found."""
    if not ratios:
        return None, None
    c = Counter(r for r, _ in ratios)
    top = c.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None, "; ".join(q for _, q in ratios[:3])
    r = top[0][0]
    return r, next(q for rr, q in ratios if rr == r)


# ---------------------------------------------------------------- one company
def cover_titles(body):
    """[(dims, title, symbol)] from the instance's cover facts."""
    titles, symbols = {}, {}
    for prefix, tag, start, end, dims, num, text, unit, dec in px.parse_instance(body):
        if prefix != "dei" or not text:
            continue
        if tag == "Security12bTitle":
            titles.setdefault(dims or "", text.strip())
        elif tag == "TradingSymbol":
            symbols.setdefault(dims or "", text.strip().upper())
    return [(d, t, symbols.get(d)) for d, t in titles.items()]


def read_cover(titles, ticker):
    """What the cover says about the ticker's listing: ('ads', ratio, title)
    with ratio None when the title names no number, ('direct', 1.0, title),
    or (None, None, None) when the cover names nothing usable."""
    tk = (ticker or "").upper().replace(".", "-")
    ads = [(d, t, s) for d, t, s in titles if _ADS_WORDS.search(html.unescape(t)) and not _PREFERRED.search(t)]
    mine = [x for x in ads if (x[2] or "").replace(".", "-") == tk]
    pick = mine[0] if mine else (ads[0] if len(ads) == 1 or (ads and not any(s for _, _, s in ads)) else None)
    if pick is None and ads:
        pick = ads[0]
    if pick is not None:
        return "ads", ratio_in_title(pick[1]), pick[1]
    plain = [(d, t, s) for d, t, s in titles if not _PREFERRED.search(t)]
    direct = [x for x in plain if (x[2] or "").replace(".", "-") == tk] or plain
    if direct:
        return "direct", 1.0, direct[0][1]
    return None, None, None


def find_docs(fetcher, cik, accn):
    """(instance URL, primary document URL) of a filing, either None."""
    folder = FOLDER_URL % (cik, accn.replace("-", ""))
    listing = json.loads(fetcher.get(folder + "index.json").decode("utf-8"))
    names = [i["name"] for i in listing.get("directory", {}).get("item", [])]
    inst = sorted(n for n in names if n.endswith("_htm.xml"))
    doc = None
    if inst:
        stem = inst[0][:-len("_htm.xml")]
        if stem + ".htm" in names:
            doc = folder + stem + ".htm"
    if doc is None:
        htm = [n for n in names if n.lower().endswith((".htm", ".html")) and not re.match(r"^R\d+\.htm$", n)
               and "ex-" not in n.lower() and "exhibit" not in n.lower() and not n.lower().startswith("0001")]
        if htm:
            doc = folder + sorted(htm, key=len)[0]
    return (folder + inst[0]) if inst else None, doc


def fetch_company(fetcher, cik, accn, form, ticker):
    """Network half (safe in a worker thread): the cover, then the document
    only when the cover does not settle it."""
    inst_url, doc_url = find_docs(fetcher, cik, accn)
    got = {"accn": accn, "form": form, "instance_url": inst_url, "doc_url": doc_url, "doc": None}
    listing, ratio, title = None, None, None
    if inst_url:
        got["instance"] = fetcher.get(inst_url)
        listing, ratio, title = read_cover(cover_titles(got["instance"]), ticker)
    got.update(listing=listing, ratio=ratio, title=title, source="cover title" if ratio and listing == "ads" else None)
    settled = listing == "ads" and ratio is not None
    # a 20-F whose cover lists the share alone can still be an ADS listing (TSMC): the text decides
    if not settled and doc_url and (listing != "direct" or form.startswith("20-F")):
        body = fetcher.get(doc_url)
        if len(body) <= DOC_MAX:
            got["doc"] = body
    return got


def settle(got):
    """(listing, ratio, title, quote, source, status) from what was fetched.
    A ratio stated in the text wins over a cover that lists the share alone
    (TSMC's cover says "Common Shares"; its text says each ADS is five). A
    cover that lists the share alone, with no ratio in the text, is a direct
    listing however often the text mentions depositary shares in passing
    (Spotify)."""
    listing, ratio, title = got.get("listing"), got.get("ratio"), got.get("title")
    if listing == "ads" and ratio is not None:
        return "ads", ratio, title, title, "cover title", "ok"
    body = got.get("doc")
    if body is not None:
        found = ratios_in_document(body)
        r, quote = decide(found)
        if r is not None:
            return "ads", r, title, quote, "document", "ok"
        if listing == "direct":
            return "direct", 1.0, title, None, "no depositary share named", "ok"
        if listing == "ads":
            return "unknown", None, title, quote, "document", "ratios differ" if found else "ratio not stated"
        if _ADS_WORDS.search(_text(body[:3000000])):
            return "unknown", None, None, quote, "document", "ratio not stated"
        return "direct", 1.0, None, None, "no depositary share named", "ok"
    if listing == "direct":
        return "direct", 1.0, title, None, "no depositary share named", "ok"
    if listing == "ads":
        return "unknown", None, title, None, "cover title", "ratio not stated"
    return "unknown", None, None, None, None, "no cover read"


def store_counts(con, cik, got, filed):
    """The cover's share counts, for a filing the frames do not carry (a
    20-F filer counting per class, or on the IFRS taxonomy): the same rows
    equity/sec_cover_shares.py stores, so app/sec_shares.py reads them alike."""
    body, accn = got.get("instance"), got["accn"]
    if body is None or not _has(con, "sec_cover_shares"):
        return 0
    if con.execute("SELECT count(*) FROM sec_cover_shares WHERE accn = ?", [accn]).fetchone()[0]:
        return 0
    try:
        rows = cover.parse_cover(body)
    except px.PullError:
        return 0
    keep = [(cik, accn, end, k, dims, t, n, "instance", _now()) for end, k, dims, t, n in rows if end and n is not None]
    if not keep:
        return 0
    sha = hashlib.sha256(body).hexdigest()
    raw = cover.archive(body, "instance", "%d-%s" % (cik, accn), sha)
    con.execute("INSERT OR IGNORE INTO sec_cover_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [accn, cik, got["form"], filed, got.get("instance_url"), sha, raw, "ok", len({r[1] for r in rows}),
                 cover.PARSER_VERSION, _now()])
    con.executemany("INSERT OR IGNORE INTO sec_cover_shares VALUES (?,?,?,?,?,?,?,?,?)", keep)
    return len(keep)


def _has(con, table):
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]).fetchone()[0])


def store(con, cik, ticker, got, filed):
    now = _now()
    listing, ratio, title, quote, source, status = settle(got)
    sha = raw = None
    if got.get("doc") is not None:
        sha = hashlib.sha256(got["doc"]).hexdigest()
        raw = archive(got["doc"], "%d-%s" % (cik, got["accn"]), sha)
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM sec_ads_ratios WHERE cik = ? AND accn = ?", [cik, got["accn"]])
        con.execute("DELETE FROM sec_ads_filings WHERE accn = ?", [got["accn"]])
        con.execute("INSERT INTO sec_ads_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [got["accn"], cik, got["form"], filed, got.get("instance_url"), got.get("doc_url"), sha, raw,
                     status, PARSER_VERSION, now])
        con.execute("INSERT INTO sec_ads_ratios VALUES (?,?,?,?,?,?,?,?,?)",
                    [cik, got["accn"], ticker, listing, ratio, (title or "")[:300] or None, (quote or "")[:600] or None,
                     source, now])
        con.execute("INSERT INTO sec_ads_checks VALUES (?,?,?,?)", [cik, now, status, "%s %s" % (listing, ratio)])
        store_counts(con, cik, got, filed)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return listing, ratio, status


# ---------------------------------------------------------------- targets
def targets(con, tickers=None, limit=400, reread=None):
    """[(cik, ticker, accn, form, filed)]: foreign filers with a ticker whose
    newest annual report has not been read, least recently checked first.
    With `tickers`, those companies whatever their form."""
    rows = con.execute("""
        WITH latest AS (
            SELECT cik, adsh, form, filed, country_inc, row_number() OVER (PARTITION BY cik ORDER BY period DESC, filed DESC) AS rn
            FROM sec_filings WHERE form IN ('20-F','20-F/A','10-K','10-K/A','10-KT') AND period >= current_date - INTERVAL 550 DAY)
        SELECT l.cik, c.ticker, l.adsh, l.form, l.filed, l.country_inc
        FROM latest l JOIN sec_companies c USING (cik)
        WHERE l.rn = 1 AND c.ticker IS NOT NULL""").fetchall()
    done = {a for (a,) in con.execute("SELECT accn FROM sec_ads_filings").fetchall()}
    if reread == "unknown":           # read again the filings whose listing could not be settled
        done -= {a for (a,) in con.execute("SELECT accn FROM sec_ads_ratios WHERE listing = 'unknown'").fetchall()}
    elif reread == "nocount":         # and those whose cover counts are not on file
        counted = {a for (a,) in con.execute("SELECT DISTINCT accn FROM sec_cover_shares").fetchall()} if _has(con, "sec_cover_shares") else set()
        done = {a for a in done if a in counted}
    last = dict(con.execute("SELECT cik, max(checked_at) FROM sec_ads_checks GROUP BY cik").fetchall())
    out = []
    for cik, ticker, accn, form, filed, country in rows:
        if tickers:
            if ticker.upper() not in tickers:
                continue
        else:
            foreign = form.startswith("20-F") or (country and country != "US")
            if not foreign or accn in done:
                continue
        out.append((cik, ticker, accn, form, filed))
    out.sort(key=lambda x: (last.get(x[0]) or dt.datetime(1970, 1, 1), x[0]))
    return out[:limit] if limit else out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=400, help="companies read this run; 0 for all")
    ap.add_argument("--tickers", help="comma-separated tickers: read just these companies")
    ap.add_argument("--workers", type=int, default=px.WORKERS)
    ap.add_argument("--reread", choices=["unknown", "nocount"], help="read again filings already read: those whose "
                    "listing is unknown, or whose cover counts are not on file")
    args = ap.parse_args(argv)
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        print("EDGAR_USER_AGENT is not set; the SEC refuses undeclared clients")
        return 2
    fetcher = px.Fetcher(ua)
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    run_at = _now()
    want = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    todo = targets(con, want, 0 if want else args.limit, args.reread)
    print("  listings to read: %d companies" % len(todo))
    read = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(fetch_company, fetcher, cik, accn, form, ticker): (cik, ticker, filed)
                for cik, ticker, accn, form, filed in todo}
        for job in as_completed(jobs):
            cik, ticker, filed = jobs[job]
            try:
                got = job.result()
            except (px.PullError, ValueError) as e:
                failed += 1
                con.execute("INSERT INTO sec_ads_checks VALUES (?,?,?,?)", [cik, _now(), "failed", str(e)[:300]])
                print("  %-6s %10d  FAILED: %s" % (ticker, cik, e))
                continue
            listing, ratio, status = store(con, cik, ticker, got, filed)
            read += 1
            print("  %-6s %10d  %s %-8s %-7s %s  %s" % (ticker, cik, got["form"], status, listing,
                                                      ("%g" % ratio) if ratio else "-",
                                                      "doc" if got.get("doc") is not None else "cover"))
    bad = failed and not read
    con.execute("INSERT INTO sec_ads_runs VALUES (?,?,?,?,?)",
                [run_at, "failed" if bad else "ok", read, failed,
                 "%d of %d companies failed" % (failed, len(todo)) if failed else None])
    con.close()
    print("  done: %d read, %d failed" % (read, failed))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
