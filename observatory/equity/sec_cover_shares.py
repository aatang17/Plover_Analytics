# -*- coding: utf-8 -*-
"""Shares outstanding as each company states it on the cover of its 10-K or 10-Q.

Why this exists
---------------
Market value is close × shares outstanding, and the share count belongs to the
cover page: every 10-K and 10-Q states the shares outstanding at a date a few
weeks before it is filed, tagged dei:EntityCommonStockSharesOutstanding. The
SEC's Financial Statement Data Sets (equity/sec_extract.py) leave the cover
page out — 44 such facts in the whole shelf — so until this loader the only
count on file was the balance-sheet one at the fiscal year end: up to a year
old, and missing for any company that tags it per share class or not at all.
Rankings by market value covered 298 of the S&P 500.

Two sources, both the SEC's own
-------------------------------
1. The XBRL frames API: one file per calendar quarter listing, for every
   filer, the cover count nearest that quarter's end
   (data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/
   shares/CY2026Q2I.json — about 4,400 companies a quarter). Six quarters are
   read each run, so the newest count of every company that files one figure
   is on file from six requests.
2. The filing's own XBRL instance, for a priced company the frames leave out.
   The frames carry only facts without dimensions, and a company with several
   share classes (Alphabet, Meta, Berkshire, Visa, Comcast …) states a count
   per class. The instance (`<primary>_htm.xml`, beside each filing) gives
   each class's count and, from the cover's Section 12(b) table, the ticker
   each class trades under — the company's own mapping, so nothing is guessed.
   An unlisted class (Alphabet's Class B) carries no ticker; how it is valued
   is decided in app/sec_shares.py, never here.

Tables (data/sec.duckdb)
------------------------
    sec_cover_frames   one row per frames file read: frame, URL, SHA-256, raw
                       path, rows, when
    sec_cover_filings  one row per filing instance read: CIK, accession, form,
                       filed date, URL, SHA-256, raw path, status, classes
    sec_cover_checks   one row per company per run that was asked of the SEC:
                       when, what came back (health dates freshness from here)
    sec_cover_rates    one row per share class per filing whose rate of
                       conversion into the listed class the filer tags
                       (CommonStockConversionRate — Visa's B-1, B-2, C …)
    sec_cover_runs     one row per run: when, ok or failed, what it added
    sec_cover_shares   one row per count: CIK, accession, the count's date,
                       class ('' for the whole company), the class's ticker as
                       the cover lists it, shares, source ('frames' or
                       'instance')

Insert-only. A count once stored is never changed: a later filing is a new
row, and the reader takes the newest. Raw files are archived gzip'd under
data/raw/sec-cover/ with their SHA-256 before they are parsed.

Usage (from observatory/):
    ./.venv/bin/python equity/sec_cover_shares.py                  # frames, then 300 companies
    ./.venv/bin/python equity/sec_cover_shares.py --limit 0        # every company the frames miss
    ./.venv/bin/python equity/sec_cover_shares.py --tickers GOOGL,META
    ./.venv/bin/python equity/sec_cover_shares.py --db /tmp/copy.duckdb

Needs EDGAR_USER_AGENT ("Company Name contact@domain"). Python 3.9; stdlib +
duckdb.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sec_proxy_pay as px  # noqa: E402 — the SEC fetcher and instance parser are shared

DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "sec-cover")
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/%s.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK%010d.json"
FRAMES_BACK = 6                 # calendar quarters read each run
FRESH_DAYS = 200                # a frames count older than this sends the company to its instance
PERIODIC = ("10-K", "10-Q", "10-KT", "10-QT")
PARSER_VERSION = "cover-4"
COVER_TAGS = ("EntityCommonStockSharesOutstanding", "TradingSymbol", "NoTradingSymbolFlag",
              "Security12bTitle")
CLASS_AXIS = "us-gaap:StatementClassOfStockAxis"
IFRS_CLASS_AXIS = "ifrs-full:ClassesOfShareCapitalAxis"      # a 20-F filer on the IFRS taxonomy (Sony, Toyota, Novo)
EXCHANGE_AXIS = "dei:EntityListingsExchangeAxis"
# Holding companies that replaced a filer on the exchange and have no 10-K of
# their own yet; kept in step with SUCCESSORS in app/sec_shares.py.
SUCCESSOR_CIKS = {2115436}      # ExxonMobil Holdings Corp (XOM since 1 July 2026)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_cover_frames (
    frame VARCHAR, url VARCHAR, sha256 VARCHAR, raw_path VARCHAR, n_rows INTEGER,
    fetched_at TIMESTAMP, PRIMARY KEY (frame, sha256));
CREATE TABLE IF NOT EXISTS sec_cover_filings (
    accn VARCHAR PRIMARY KEY, cik BIGINT, form VARCHAR, filed DATE, url VARCHAR,
    sha256 VARCHAR, raw_path VARCHAR, status VARCHAR, n_classes INTEGER,
    parser_version VARCHAR, fetched_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sec_cover_checks (
    cik BIGINT, checked_at TIMESTAMP, outcome VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_cover_runs (
    run_at TIMESTAMP, status VARCHAR, frames_new INTEGER, filings_read INTEGER,
    failed INTEGER, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_cover_rates (
    cik BIGINT, accn VARCHAR, as_of DATE, class_key VARCHAR, rate DOUBLE, tag VARCHAR,
    first_seen TIMESTAMP, PRIMARY KEY (cik, accn, class_key));
CREATE TABLE IF NOT EXISTS sec_cover_shares (
    cik BIGINT, accn VARCHAR, end_date DATE, class_key VARCHAR, dims VARCHAR,
    ticker VARCHAR, shares DOUBLE, source VARCHAR, first_seen TIMESTAMP,
    PRIMARY KEY (cik, accn, class_key, source));
"""


def _now():
    return dt.datetime.utcnow().replace(microsecond=0)


def archive(body, kind, name, sha):
    d = os.path.join(RAW_ROOT, kind, dt.date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.%s.gz" % (name, sha[:12]))
    if not os.path.exists(path):
        import gzip
        with gzip.open(path, "wb") as f:
            f.write(body)
    return os.path.relpath(path, os.path.join(HERE, ".."))


def recent_frames(today=None, n=FRAMES_BACK):
    """['CY2026Q3I', 'CY2026Q2I', …] — the current calendar quarter and the n-1 before it."""
    today = today or dt.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append("CY%dQ%dI" % (y, q))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return out


# ---- 1. the frames ---------------------------------------------------------------
def load_frames(con, fetcher):
    added = 0
    for frame in recent_frames():
        url = FRAMES_URL % frame
        try:
            body = fetcher.get(url)
        except px.PullError as e:
            # the current quarter's frame does not exist until a filer reports in it
            print("  frames %s: %s" % (frame, e))
            continue
        sha = hashlib.sha256(body).hexdigest()
        if con.execute("SELECT 1 FROM sec_cover_frames WHERE frame = ? AND sha256 = ?", [frame, sha]).fetchone():
            print("  frames %s: unchanged" % frame)
            continue
        raw = archive(body, "frames", frame, sha)
        rows = json.loads(body.decode("utf-8")).get("data") or []
        now = _now()
        before = con.execute("SELECT count(*) FROM sec_cover_shares").fetchone()[0]
        con.execute("BEGIN")
        try:
            con.executemany(
                "INSERT OR IGNORE INTO sec_cover_shares VALUES (?,?,?,?,?,?,?,?,?)",
                [(int(r["cik"]), r["accn"], r["end"], "", "", None, float(r["val"]), "frames", now)
                 for r in rows if r.get("val") is not None and r.get("accn") and r.get("end")])
            con.execute("INSERT INTO sec_cover_frames VALUES (?,?,?,?,?,?)",
                        [frame, url, sha, raw, len(rows), now])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        n = con.execute("SELECT count(*) FROM sec_cover_shares").fetchone()[0] - before
        added += n
        print("  frames %s: %d companies, %d new counts" % (frame, len(rows), n))
    return added


# ---- 2. one filing's cover ---------------------------------------------------------
def _norm(t):
    return (t or "").strip().upper().replace("-", ".").replace("/", ".").replace(" ", ".")


def priced_targets(con, tickers=None):
    """[(cik, ticker)] of companies with a close in the last ten days whose
    newest frames count is missing or older than FRESH_DAYS, least recently
    checked first. With `tickers`, just those companies."""
    have_px = con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'us_px_daily'").fetchone()[0]
    if not have_px:
        return []
    priced = {_norm(s) for (s,) in con.execute(
        "SELECT DISTINCT symbol FROM us_px_daily WHERE date >= (SELECT max(date) FROM us_px_daily) - INTERVAL 10 DAY").fetchall()}
    comps = con.execute("SELECT cik, ticker, coalesce(tickers, ticker) FROM sec_companies WHERE ticker IS NOT NULL").fetchall()
    if tickers:
        want = {_norm(t) for t in tickers}
        return [(cik, t) for cik, t, ts in comps if want & {_norm(x) for x in ts.split(",")}]
    fresh = {c for (c,) in con.execute(
        "SELECT DISTINCT cik FROM sec_cover_shares WHERE end_date >= current_date - INTERVAL %d DAY" % FRESH_DAYS).fetchall()}
    last = dict(con.execute("SELECT cik, max(checked_at) FROM sec_cover_checks GROUP BY cik").fetchall())
    # Only companies a market-value ranking can list: a 10-K for a year ended
    # in the last 18 months (funds and trusts file none; a 20-F filer's price
    # is per ADS, not per share), or the successor of one.
    ranked = {c for (c,) in con.execute(
        "SELECT DISTINCT cik FROM sec_filings WHERE form IN ('10-K', '10-K/A', '10-KT') "
        "AND period >= current_date - INTERVAL 550 DAY").fetchall()}
    ranked |= SUCCESSOR_CIKS
    out = [(cik, t) for cik, t, ts in comps
           if cik not in fresh and cik in ranked and priced & {_norm(x) for x in ts.split(",")}]
    out.sort(key=lambda x: (last.get(x[0]) or dt.datetime(1970, 1, 1), x[0]))
    return out


def latest_periodic(fetcher, cik):
    """(accn, form, filed date) of the company's newest 10-K or 10-Q, or None."""
    d = json.loads(fetcher.get(SUBMISSIONS_URL % cik).decode("utf-8"))
    r = d.get("filings", {}).get("recent", {})
    for i, form in enumerate(r.get("form", [])):
        if form in PERIODIC:
            return r["accessionNumber"][i], form, dt.date.fromisoformat(r["filingDate"][i])
    return None


def class_key(dims):
    """The share-class member of a context's dimensions, '' for none. A
    class beside an exchange (one class listed on two exchanges) is still that
    class; any other dimension (a co-registrant, a legal entity) is kept whole
    so it is never mistaken for a class."""
    if not dims:
        return ""
    parts = dims.split("|")
    axes = (CLASS_AXIS + "=", IFRS_CLASS_AXIS + "=")
    cls = [p.split("=", 1)[1] for p in parts if p.startswith(axes)]
    rest = [p for p in parts if not p.startswith(axes) and not p.startswith(EXCHANGE_AXIS + "=")]
    if len(cls) == 1 and not rest:
        return cls[0]
    return dims


def _local(member):
    """us-gaap:CommonClassAMember -> commonclassa."""
    m = member.split(":", 1)[-1]
    return (m[:-6] if m.endswith("Member") else m).lower()


def _class_for_title(title, classes):
    """The one count class a Section 12(b) title names ("Class A Common
    Stock" -> CommonClassA; "Common Stock" -> CommonStock), or None."""
    m = re.search(r"\bClass\s+([A-Z0-9][A-Z0-9-]*)\b", title or "", re.I)
    if m:
        want = m.group(1).replace("-", "").lower()
        hits = [c for c in classes if _local(c).endswith("class" + want)]
    elif re.search(r"common stock|ordinary share|common share", title or "", re.I):
        hits = [c for c in classes if "class" not in _local(c) or _local(c).endswith("classundefined")]
    else:
        hits = []
    return hits[0] if len(hits) == 1 else None


def parse_cover(body):
    """[(end, class_key, dims, ticker, shares)] from one instance. A class's
    ticker is the one the cover's Section 12(b) table lists for it: by the
    class dimension when the filer tags one, else by the security's title."""
    facts = [r for r in px.parse_instance(body) if r[0] == "dei" and r[1] in COVER_TAGS]
    # a depositary-share member counts the ADSs in issue, a part of a class already counted, never a class
    counts = [(end, class_key(dims), dims, num) for prefix, tag, start, end, dims, num, text, unit, dec in facts
              if tag == "EntityCommonStockSharesOutstanding" and num is not None
              and not re.search(r"preferred|preference|warrant|adr|ads|depositary|depository", _local(class_key(dims)))]
    titles, symbols = {}, []
    for prefix, tag, start, end, dims, num, text, unit, dec in facts:
        if tag == "Security12bTitle" and text:
            titles.setdefault(dims, text.strip())
        elif tag == "TradingSymbol" and text:
            symbols.append((dims, text.strip().upper()))
    classes = sorted({k for _, k, _, _ in counts if k and "=" not in k})
    total = sum(n for _, k, _, n in counts if k in classes) or 0
    # classes of real size: Interactive Brokers' 400 Class B shares beside 453mn Class A are not a listing
    sizable = [k for k in classes if sum(n for _, kk, _, n in counts if kk == k) >= 1e-5 * total]
    tick = {}
    for dims, sym in symbols:
        k = class_key(dims)
        if k in classes:
            tick.setdefault(k, sym)
            continue
        if not k and len(symbols) == 1 and len(classes) <= 1:
            tick.setdefault(classes[0] if classes else "", sym)
            continue
        # untagged, or tagged under the filer's own member (ares:ClassACommonStock…):
        # the class the security's title names
        named = _class_for_title(titles.get(dims), classes)
        if named is None and not k and len(symbols) == 1 and len(sizable) == 1:
            named = sizable[0]
        if named:
            tick.setdefault(named, sym)
    return [(end, k, dims, tick.get(k), num) for end, k, dims, num in counts]


def parse_rates(body):
    """[(as_of, class_key, rate, tag)]: each class's conversion rate into the
    listed class, where the filer tags one — the newest date per class."""
    best = {}
    for prefix, tag, start, end, dims, num, text, unit, dec in px.parse_instance(body):
        if tag != "CommonStockConversionRate" or num is None or num <= 0 or start:
            continue
        k = class_key(dims)
        if k and "=" not in k and (k not in best or end > best[k][0]):
            best[k] = (end, k, num, "%s:%s" % (prefix, tag))
    return list(best.values())


def fetch_cover(fetcher, cik, held):
    """Network half for one company (safe in a worker thread)."""
    hit = latest_periodic(fetcher, cik)
    if hit is None:
        return {"outcome": "no_periodic"}
    accn, form, filed = hit
    if accn in held:
        return {"outcome": "unchanged", "accn": accn}
    url = px.find_instance(fetcher, cik, accn)
    if url is None:
        return {"outcome": "no_xbrl", "accn": accn, "form": form, "filed": filed}
    body = fetcher.get(url)
    return {"outcome": "read", "accn": accn, "form": form, "filed": filed, "url": url, "body": body}


def store_cover(con, cik, got):
    now = _now()
    con.execute("INSERT INTO sec_cover_checks VALUES (?,?,?,?)",
                [cik, now, got["outcome"], got.get("accn")])
    if got["outcome"] not in ("read", "no_xbrl"):
        return 0
    if got["outcome"] == "no_xbrl":
        con.execute("INSERT OR IGNORE INTO sec_cover_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [got["accn"], cik, got["form"], got["filed"], None, None, None, "no_xbrl", 0,
                     PARSER_VERSION, now])
        return 0
    body = got["body"]
    sha = hashlib.sha256(body).hexdigest()
    raw = archive(body, "instance", "%d-%s" % (cik, got["accn"]), sha)
    try:
        rows, found = parse_cover(body), parse_rates(body)
        status = "ok" if rows else "no_count"
    except px.PullError as e:
        rows, found, status = [], [], "unreadable: %s" % e
    con.execute("BEGIN")
    try:
        con.execute("INSERT OR IGNORE INTO sec_cover_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [got["accn"], cik, got["form"], got["filed"], got["url"], sha, raw, status,
                     len({r[1] for r in rows}), PARSER_VERSION, now])
        keep = [(cik, got["accn"], end, k, dims, t, n, "instance", now) for end, k, dims, t, n in rows if end]
        if keep:
            con.executemany("INSERT OR IGNORE INTO sec_cover_shares VALUES (?,?,?,?,?,?,?,?,?)", keep)
        rates = [(cik, got["accn"], d, k, r, tag, now) for d, k, r, tag in found if d]
        if rates:
            con.executemany("INSERT OR IGNORE INTO sec_cover_rates VALUES (?,?,?,?,?,?,?)", rates)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def load_instances(con, fetcher, targets, workers):
    held = {a for (a,) in con.execute("SELECT accn FROM sec_cover_filings").fetchall()}
    read = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(fetch_cover, fetcher, cik, held): (cik, t) for cik, t in targets}
        for job in as_completed(jobs):
            cik, t = jobs[job]
            try:
                got = job.result()
            except px.PullError as e:
                failed += 1
                con.execute("INSERT INTO sec_cover_checks VALUES (?,?,?,?)", [cik, _now(), "failed", str(e)[:300]])
                print("  %-6s %10d  FAILED: %s" % (t, cik, e))
                continue
            n = store_cover(con, cik, got)
            if got["outcome"] == "read":
                read += 1
                print("  %-6s %10d  %s %s: %d counts" % (t, cik, got["form"], got["accn"], n))
    return read, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=300, help="companies read from their filings; 0 for all")
    ap.add_argument("--tickers", help="comma-separated tickers: read just these companies' covers")
    ap.add_argument("--workers", type=int, default=px.WORKERS)
    ap.add_argument("--skip-frames", action="store_true")
    args = ap.parse_args()
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        print("EDGAR_USER_AGENT is not set; the SEC refuses undeclared clients")
        return 2
    fetcher = px.Fetcher(ua)
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    run_at, frames_new = _now(), 0
    if not args.skip_frames and not args.tickers:
        frames_new = load_frames(con, fetcher)
    targets = priced_targets(con, args.tickers.split(",") if args.tickers else None)
    if args.limit and not args.tickers:
        targets = targets[:args.limit]
    print("  covers to read: %d companies" % len(targets))
    read, failed = load_instances(con, fetcher, targets, args.workers)
    print("  done: %d filings read, %d failed" % (read, failed))
    bad = failed and not read
    con.execute("INSERT INTO sec_cover_runs VALUES (?,?,?,?,?,?)",
                [run_at, "failed" if bad else "ok", frames_new, read, failed,
                 "%d of %d companies failed" % (failed, len(targets)) if failed else None])
    con.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
