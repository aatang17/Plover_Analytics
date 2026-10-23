# -*- coding: utf-8 -*-
"""US deep coverage — every number a chosen company has ever filed, point in time.

Why company facts, and why only some companies
----------------------------------------------
sec_extract.py loads the SEC's quarterly Financial Statement Data Sets: every
filer, but one quarter of filings per 600MB zip, so full history for the
whole market is ~9.5GB. For a chosen set of companies the SEC publishes a
better cut: `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` carries
every non-dimensional XBRL fact the company has filed since 2009, and every
fact names the filing it came from (accession number, form, filed date).
So a period's number appears once per filing that reported it — as first
reported in the 10-Q, again in the 10-K, again as a comparative a year
later, and again restated. That is point-in-time history for free: the
value "as known on date D" is the one from the latest filing filed on or
before D. Nothing here recomputes, rescales or picks a winner.

What it does not carry: dimensional facts (segments, product lines) and the
statement layout (line order, the filer's own labels). Those stay with the
data-set shelf in sec_extract.py.

The universe lives in us_universe.json (ticker, CIK, GICS sector, and any
predecessor registrant — Exxon's 2026 holding company, Alphabet's 2015 one,
Disney's 2019 one — whose facts load under its own CIK and are linked, never
merged).

Tables (data/sec.duckdb, beside the data-set tables):
    sec_cf_companies   one row per CIK: ticker, sector, entity name, the
                       successor it rolls up to, first/last filed, counts
    sec_cf_pulls       one row per fetch of one company's file: SHA-256,
                       raw archive path, facts in file, facts new to us,
                       conflicts, vanished facts, balance-sheet verdict
    sec_cf_facts       one row per (fact × filing): taxonomy, tag, unit,
                       period start (blank for balances), period end, value,
                       accession, fiscal year/period, form, filed, frame,
                       and the pull that first brought it in
    sec_cf_tags        the taxonomy's own label and description per tag
    sec_cf_conflicts   a fact the SEC now reports with a different value
                       for the same filing than the one we stored

Vintages
--------
Insert-only. A stored fact is never updated or deleted: a later pull adds
the facts of newly filed reports and nothing else. If the SEC's file ever
carries a different value for a (tag, period, filing) we already hold, the
stored value stays and the pair is written to sec_cf_conflicts; if a stored
fact is missing from a new file it stays too and the pull counts it as
vanished. An unchanged file (same SHA-256 as the last good pull) loads nothing; the
pull is still recorded, status 'unchanged', so freshness means "last reached
the SEC", not "last found something new".

Gate
----
Per pull: the file must name the CIK we asked for and carry facts. Then the
balance-sheet identity — Assets = LiabilitiesAndStockholdersEquity — is
checked for every 10-K and 10-Q on its own period end; filings that miss by
more than one basis point of assets are counted and listed in the pull row
(the filer's numbers are stored as filed either way; the verdict is
disclosure, not correction). A pull that fails the gate loads nothing.

Usage (from observatory/):
    ./.venv/bin/python equity/sec_companyfacts.py                 # whole universe
    ./.venv/bin/python equity/sec_companyfacts.py --tickers AAPL,XOM
    ./.venv/bin/python equity/sec_companyfacts.py --db /tmp/sec.copy.duckdb

Needs EDGAR_USER_AGENT ("Company Name contact@domain"); the SEC refuses
undeclared clients. Python 3.9; stdlib + duckdb.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from app import sec_gate                                          # noqa: E402 — one gate for every SEC request on the host

DB_PATH = os.environ.get("SEC_DB_PATH",
                         os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "sec-companyfacts")
UNIVERSE = os.path.join(HERE, "us_universe.json")
URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"

PARSER_VERSION = "cf-1"
EXTRACTOR = "sec-companyfacts"
RATE = 4.0                      # requests per second; the SEC's ceiling is 10
BS_TOLERANCE = 1e-4
PERIODIC = ("10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A",
            "20-F", "20-F/A", "40-F", "40-F/A")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_cf_companies (
    cik BIGINT PRIMARY KEY, ticker VARCHAR, sector VARCHAR, successor_cik BIGINT,
    entity_name VARCHAR, first_filed DATE, last_filed DATE, facts BIGINT,
    filings INTEGER, last_pull_id INTEGER, updated_at TIMESTAMP);
CREATE SEQUENCE IF NOT EXISTS sec_cf_pull_seq;
CREATE TABLE IF NOT EXISTS sec_cf_pulls (
    pull_id INTEGER PRIMARY KEY, cik BIGINT, fetched_at TIMESTAMP, url VARCHAR,
    sha256 VARCHAR, bytes BIGINT, raw_path VARCHAR, parser_version VARCHAR,
    status VARCHAR, facts_in_file BIGINT, new_facts BIGINT, conflicts INTEGER,
    vanished BIGINT, bs_checked INTEGER, bs_failed INTEGER, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_cf_facts (
    cik BIGINT, taxonomy VARCHAR, tag VARCHAR, unit VARCHAR,
    period_start DATE, period_end DATE, value DOUBLE, accn VARCHAR,
    fy INTEGER, fp VARCHAR, form VARCHAR, filed DATE, frame VARCHAR,
    pull_id INTEGER);
CREATE TABLE IF NOT EXISTS sec_cf_tags (
    taxonomy VARCHAR, tag VARCHAR, label VARCHAR, description VARCHAR,
    PRIMARY KEY (taxonomy, tag));
CREATE TABLE IF NOT EXISTS sec_cf_conflicts (
    pull_id INTEGER, cik BIGINT, taxonomy VARCHAR, tag VARCHAR, unit VARCHAR,
    period_start DATE, period_end DATE, accn VARCHAR,
    stored_value DOUBLE, new_value DOUBLE);
"""
# The identity of one fact: the same (tag, unit, period) reported by the same
# filing. `frame` is the SEC's calendar alignment label, reassigned as later
# filings arrive, so it is carried but is not part of the identity.
KEY = ("cik", "taxonomy", "tag", "unit", "period_start", "period_end", "accn")
COLS = KEY + ("value", "fy", "fp", "form", "filed", "frame")


class PullError(Exception):
    pass


def load_universe(path=UNIVERSE):
    """[(cik, ticker, sector, successor_cik)] — each company, then its predecessors."""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    out = []
    for c in spec["companies"]:
        out.append((int(c["cik"]), c["ticker"], c["sector"], None))
        for p in c.get("predecessors", []):
            out.append((int(p), c["ticker"], c["sector"], int(c["cik"])))
    return out


class Fetcher(object):
    """Every request goes through app/sec_gate.py (the host's SEC_RATE, and a
    hold once the SEC refuses this server); RATE here only ever slows it."""

    def __init__(self, ua, rate=RATE):
        self.ua = ua
        self.gap = 1.0 / rate
        self.last = 0.0

    def get(self, url):
        for attempt in range(4):
            wait = self.last + self.gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            status, body, _ = sec_gate.get(url, timeout=60, headers={"User-Agent": self.ua})
            if sec_gate.is_refusal(status, body):
                raise PullError("the SEC is refusing this server (%s)"
                                % ("held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status))
            if status == 200:
                return body
            if status == 404:
                raise PullError("SEC has no company-facts file (404)")
            if status in (0, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            raise PullError("HTTP %s" % status if status else "unreachable")
        raise PullError("gave up after retries")


def archive(body, cik, sha):
    day = dt.date.today().isoformat()
    d = os.path.join(RAW_ROOT, day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "CIK%010d.%s.json.gz" % (cik, sha[:12]))
    if not os.path.exists(path):
        with gzip.open(path, "wb") as f:
            f.write(body)
    return os.path.relpath(path, os.path.join(HERE, ".."))


def parse(doc, cik):
    """Flatten the file into fact rows and tag rows. Refuses a file for the wrong CIK."""
    if int(doc.get("cik", -1)) != cik:
        raise PullError("file is for CIK %s, asked for %s" % (doc.get("cik"), cik))
    facts, tags = [], []
    for tax, block in (doc.get("facts") or {}).items():
        for tag, v in block.items():
            tags.append((tax, tag, v.get("label"), v.get("description")))
            for unit, arr in (v.get("units") or {}).items():
                for f in arr:
                    if f.get("val") is None:          # nil fact: missing, never zero
                        continue
                    facts.append((cik, tax, tag, unit, f.get("start") or "", f["end"],
                                  f["accn"], f["val"], f.get("fy") if f.get("fy") is not None else "",
                                  f.get("fp") or "", f.get("form") or "", f["filed"],
                                  f.get("frame") or ""))
    if not facts:
        raise PullError("file carries no facts")
    return doc.get("entityName"), facts, tags


def stage(con, facts):
    """Write the rows to a temp TSV and read them into a TEMP table in one pass."""
    fd, path = tempfile.mkstemp(suffix=".tsv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
            w.writerows(facts)
        con.execute("DROP TABLE IF EXISTS cf_stage")
        con.execute("""
            CREATE TEMP TABLE cf_stage AS
            SELECT DISTINCT CAST(column00 AS BIGINT) AS cik, column01 AS taxonomy,
                   column02 AS tag, column03 AS unit,
                   TRY_CAST(NULLIF(column04,'') AS DATE) AS period_start,
                   CAST(column05 AS DATE) AS period_end, column06 AS accn,
                   CAST(column07 AS DOUBLE) AS value,
                   TRY_CAST(NULLIF(column08,'') AS INTEGER) AS fy,
                   NULLIF(column09,'') AS fp, NULLIF(column10,'') AS form,
                   CAST(column11 AS DATE) AS filed, NULLIF(column12,'') AS frame
            FROM read_csv(?, delim='\t', header=false, quote='"', all_varchar=true,
                          null_padding=false)""", [path])
    finally:
        os.unlink(path)
    # One filing can list the same fact twice with different frames; keep one
    # row per identity (the framed one when there is a choice).
    con.execute("""
        CREATE OR REPLACE TEMP TABLE cf_stage AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY cik, taxonomy, tag, unit, period_start, period_end, accn, value
                ORDER BY frame NULLS LAST) AS rn
            FROM cf_stage) WHERE rn = 1""")


def _join(a, b):
    return " AND ".join("%s.%s IS NOT DISTINCT FROM %s.%s" % (a, k, b, k) for k in KEY)


def balance_check(con):
    """(checked, [failures]) for every periodic filing in the stage."""
    rows = con.execute("""
        WITH a AS (SELECT accn, form, period_end, value FROM cf_stage
                   WHERE taxonomy='us-gaap' AND tag='Assets' AND unit='USD'),
             le AS (SELECT accn, period_end, value FROM cf_stage
                   WHERE taxonomy='us-gaap' AND tag='LiabilitiesAndStockholdersEquity'
                     AND unit='USD'),
             own AS (SELECT accn, max(period_end) AS pe FROM a GROUP BY accn)
        SELECT a.accn, a.form, a.period_end, a.value, le.value
        FROM a JOIN own ON own.accn=a.accn AND own.pe=a.period_end
        JOIN le ON le.accn=a.accn AND le.period_end=a.period_end
        WHERE a.form IN (%s)""" % ",".join("'%s'" % f for f in PERIODIC)).fetchall()
    fails = [r for r in rows if r[3] and abs(r[3] - r[4]) > BS_TOLERANCE * abs(r[3])]
    return len(rows), fails


def load_company(con, fetcher, cik, ticker, sector, successor):
    url = URL % cik
    now = dt.datetime.utcnow().replace(microsecond=0)
    body = fetcher.get(url)
    sha = hashlib.sha256(body).hexdigest()
    last = con.execute("""SELECT sha256, raw_path FROM sec_cf_pulls WHERE cik=? AND status='ok'
                          ORDER BY pull_id DESC LIMIT 1""", [cik]).fetchone()
    if last and last[0] == sha:
        # Recorded, not just skipped: the health row dates the last pull that
        # reached the SEC, and a quiet fortnight with no new filings is a
        # working pipeline, not a stale one. Nothing else is written.
        pull_id = con.execute("SELECT nextval('sec_cf_pull_seq')").fetchone()[0]
        con.execute("""INSERT INTO sec_cf_pulls (pull_id, cik, fetched_at, url, sha256, bytes,
                       raw_path, parser_version, status) VALUES (?,?,?,?,?,?,?,?,'unchanged')""",
                    [pull_id, cik, now, url, sha, len(body), last[1], PARSER_VERSION])
        return "unchanged"
    raw_path = archive(body, cik, sha)
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise PullError("not JSON: %s" % e)
    name, facts, tags = parse(doc, cik)
    stage(con, facts)
    checked, fails = balance_check(con)

    pull_id = con.execute("SELECT nextval('sec_cf_pull_seq')").fetchone()[0]
    con.execute("BEGIN")
    try:
        conflicts = con.execute("""
            INSERT INTO sec_cf_conflicts
            SELECT ?, s.cik, s.taxonomy, s.tag, s.unit, s.period_start, s.period_end,
                   s.accn, f.value, s.value
            FROM cf_stage s JOIN sec_cf_facts f ON %s
            WHERE f.value <> s.value""" % _join("s", "f"), [pull_id]).fetchone()[0]
        new = con.execute("""
            INSERT INTO sec_cf_facts
            SELECT s.cik, s.taxonomy, s.tag, s.unit, s.period_start, s.period_end,
                   s.value, s.accn, s.fy, s.fp, s.form, s.filed, s.frame, ?
            FROM cf_stage s
            WHERE NOT EXISTS (SELECT 1 FROM sec_cf_facts f WHERE %s)""" % _join("s", "f"),
                          [pull_id]).fetchone()[0]
        vanished = con.execute("""
            SELECT count(*) FROM sec_cf_facts f WHERE f.cik=? AND f.pull_id<>?
              AND NOT EXISTS (SELECT 1 FROM cf_stage s WHERE %s)""" % _join("f", "s"),
                               [cik, pull_id]).fetchone()[0]
        con.executemany("""INSERT INTO sec_cf_tags VALUES (?,?,?,?)
                           ON CONFLICT DO NOTHING""", tags)
        detail = "; ".join("%s %s %s: assets %.0f vs L+E %.0f" % (r[0], r[1], r[2], r[3], r[4])
                           for r in fails[:10])
        con.execute("""INSERT INTO sec_cf_pulls VALUES (?,?,?,?,?,?,?,?,'ok',?,?,?,?,?,?,?)""",
                    [pull_id, cik, now, url, sha, len(body), raw_path, PARSER_VERSION,
                     len(facts), new, conflicts, vanished, checked, len(fails), detail or None])
        con.execute("""
            INSERT OR REPLACE INTO sec_cf_companies
            SELECT ?, ?, ?, ?, ?, min(filed), max(filed), count(*), count(DISTINCT accn), ?, ?
            FROM sec_cf_facts WHERE cik=?""",
                    [cik, ticker, sector, successor, name, pull_id, now, cik])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return "loaded %s facts new of %s; conflicts %s; vanished %s; balance sheet %d/%d ok" % (
        new, len(facts), conflicts, vanished, checked - len(fails), checked)


def record_failure(con, cik, err):
    pull_id = con.execute("SELECT nextval('sec_cf_pull_seq')").fetchone()[0]
    con.execute("""INSERT INTO sec_cf_pulls (pull_id, cik, fetched_at, url, parser_version,
                   status, detail) VALUES (?,?,?,?,?,'failed',?)""",
                [pull_id, cik, dt.datetime.utcnow().replace(microsecond=0), URL % cik,
                 PARSER_VERSION, err])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--tickers", help="comma-separated subset of the universe")
    args = ap.parse_args()
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        print("EDGAR_USER_AGENT is not set; the SEC refuses undeclared clients")
        return 2
    universe = load_universe()
    if args.tickers:
        want = {t.strip().upper() for t in args.tickers.split(",")}
        universe = [u for u in universe if u[1] in want]
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    fetcher = Fetcher(ua)
    failed = []
    for cik, ticker, sector, successor in universe:
        label = "%-6s %10d%s" % (ticker, cik, " (predecessor)" if successor else "")
        try:
            print("  %s  %s" % (label, load_company(con, fetcher, cik, ticker, sector, successor)))
        except Exception as e:                                    # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, str(e)[:300])
            print("  %s  FAILED: %s — stored facts unchanged" % (label, err))
            failed.append(ticker)
            try:
                record_failure(con, cik, err)
            except Exception as e2:                               # noqa: BLE001
                print("  (could not record the failure: %s)" % e2)
        sys.stdout.flush()
    con.close()
    if failed:
        print("ATTENTION company-facts pulls failed: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
