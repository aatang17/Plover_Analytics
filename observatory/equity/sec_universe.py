# -*- coding: utf-8 -*-
"""The US company universe: every SEC filer's ticker, exchange and industry.

The statements shelf (sec_extract.py) knows a filer only by its CIK and the
name and SIC code printed on each filing. People know companies by ticker,
and they screen by industry in words. The SEC publishes both lookups as
files: ``company_tickers_exchange.json`` (every registrant with a listed
security: CIK, name, ticker, exchange — roughly 10,400 rows, a share class
per row) and the Division of Corporation Finance's SIC code list (about 440
codes, each with its reviewing office and industry title).

This loader fetches both, archives the raw files with their SHA-256 under
data/raw/sec-universe/{date}/, and rebuilds two reference tables in
data/sec.duckdb:

  sec_companies   one row per CIK: name, primary ticker (the first the SEC
                  lists), every ticker, exchange(s), and the SIC code of the
                  filer's latest statement on the shelf with its title
  sec_sic         one row per SIC code: office, industry title

Reference data, not a vintage: the tables are replaced wholesale on each
successful pull, inside one transaction, and the pull is logged in
sec_universe_pulls so the health report can say when the SEC was last
reached. A fetch or parse that fails leaves the previous tables in place.

Run from observatory/ against a COPY of the served database (another
process holds the live file open):

    ./.venv/bin/python equity/sec_universe.py --db data/sec.work.duckdb

Needs EDGAR_USER_AGENT ("Company Name contact@domain"); the SEC refuses
undeclared clients. Python 3.9; stdlib + duckdb.
"""
import argparse
import datetime as dt
import hashlib
import html as html_mod
import json
import os
import re
import sys
import time

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from app import sec_gate                                          # noqa: E402 — one gate for every SEC request on the host

DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.environ.get("SEC_UNIVERSE_RAW",
                          os.path.join(HERE, "..", "data", "raw", "sec-universe"))
PARSER_VERSION = "secu-1"

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SIC_URL = ("https://www.sec.gov/corpfin/division-of-corporation-finance-"
           "standard-industrial-classification-sic-code-list")
# The SIC page redirected once already (2026); follow redirects, and accept
# the rows wherever the table sits on the page.
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_universe_pulls (
    pull_id INTEGER PRIMARY KEY, fetched_at TIMESTAMP, source VARCHAR, url VARCHAR,
    sha256 VARCHAR, bytes BIGINT, raw_path VARCHAR, parser_version VARCHAR,
    status VARCHAR, rows INTEGER, detail VARCHAR);
CREATE SEQUENCE IF NOT EXISTS sec_universe_pull_seq;
CREATE TABLE IF NOT EXISTS sec_companies (
    cik BIGINT PRIMARY KEY, name VARCHAR, ticker VARCHAR, tickers VARCHAR,
    exchange VARCHAR, exchanges VARCHAR, sic INTEGER, industry VARCHAR, office VARCHAR,
    updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sec_sic (
    sic INTEGER PRIMARY KEY, office VARCHAR, industry VARCHAR);
"""


class UniverseError(Exception):
    pass


def user_agent():
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        env_path = os.path.join(HERE, "..", ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if line.startswith("EDGAR_USER_AGENT="):
                    ua = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not ua or "@" not in ua:
        raise UniverseError('EDGAR_USER_AGENT not set ("Company Name contact@domain")')
    return ua


def fetch(url, ua, attempts=4):
    """Bytes of a SEC file, with the back-off the SEC's throttling needs.
    Every request goes through app/sec_gate.py; a refusal stops at once."""
    last = None
    for n in range(attempts):
        status, body, _ = sec_gate.get(url, timeout=60, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
        if sec_gate.is_refusal(status, body):
            raise UniverseError("the SEC is refusing this server (%s): %s" % (
                "held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status, url))
        if status == 200:
            return body
        last = "HTTP %d" % status if status else "no answer"
        if status in (0, 503) and n + 1 < attempts:
            time.sleep((5, 15, 45)[min(n, 2)])
            continue
        break
    raise UniverseError("gave up on %s: %s" % (url, last))


def archive(raw, name, day):
    folder = os.path.join(RAW_ROOT, day)
    os.makedirs(folder, exist_ok=True)
    sha = hashlib.sha256(raw).hexdigest()
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(raw)
    return sha, path


def parse_tickers(raw):
    """[(cik, name, ticker, exchange)] in the SEC's own order."""
    doc = json.loads(raw.decode("utf-8"))
    fields = doc.get("fields") or []
    want = ["cik", "name", "ticker", "exchange"]
    if any(w not in fields for w in want):
        raise UniverseError("company_tickers_exchange.json fields changed: %r" % fields)
    idx = [fields.index(w) for w in want]
    rows = []
    for rec in doc.get("data") or []:
        cik, name, ticker, exchange = (rec[i] for i in idx)
        if cik is None or not ticker:
            continue
        rows.append((int(cik), (name or "").strip(), ticker.strip().upper(),
                     (exchange or "").strip() or None))
    if len(rows) < 5000:
        raise UniverseError("only %d ticker rows; the file is not what it was" % len(rows))
    return rows


def parse_sic(raw):
    """[(sic, office, industry)] from the Corporation Finance SIC page."""
    text = raw.decode("utf-8", "replace")
    rows = []
    for tr in _TR.findall(text):
        cells = [html_mod.unescape(_TAG.sub("", c)).strip() for c in _TD.findall(tr)]
        if len(cells) >= 3 and cells[0].isdigit():
            rows.append((int(cells[0]), cells[1], cells[2]))
    if len(rows) < 300:
        raise UniverseError("only %d SIC rows parsed; the page layout changed" % len(rows))
    return rows


def companies_from(ticker_rows):
    """Collapse share classes: one row per CIK, first-listed ticker primary."""
    out = {}
    for cik, name, ticker, exchange in ticker_rows:
        c = out.get(cik)
        if c is None:
            out[cik] = {"cik": cik, "name": name, "tickers": [ticker],
                        "exchanges": [exchange] if exchange else []}
        else:
            if ticker not in c["tickers"]:
                c["tickers"].append(ticker)
            if exchange and exchange not in c["exchanges"]:
                c["exchanges"].append(exchange)
    return list(out.values())


def record_pull(con, source, url, sha, nbytes, path, status, rows, detail=None):
    con.execute("""INSERT INTO sec_universe_pulls VALUES
        (nextval('sec_universe_pull_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [dt.datetime.utcnow(), source, url, sha, nbytes, path, PARSER_VERSION,
                 status, rows, detail])


def load(con, companies, sic_rows):
    """Replace both reference tables in one transaction. The SIC code and
    title on each company come from its latest statement on the shelf, so a
    filer with no statement loaded has none."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM sec_sic")
        con.executemany("INSERT INTO sec_sic VALUES (?, ?, ?)", sic_rows)
        con.execute("CREATE OR REPLACE TEMP TABLE st_companies (cik BIGINT, name VARCHAR, "
                    "ticker VARCHAR, tickers VARCHAR, exchange VARCHAR, exchanges VARCHAR)")
        con.executemany("INSERT INTO st_companies VALUES (?, ?, ?, ?, ?, ?)",
                        [(c["cik"], c["name"], c["tickers"][0], ",".join(c["tickers"]),
                          c["exchanges"][0] if c["exchanges"] else None,
                          ",".join(c["exchanges"]) or None) for c in companies])
        has_filings = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'sec_filings'").fetchone()[0]
        con.execute("DELETE FROM sec_companies")
        if has_filings:
            con.execute("""
                INSERT INTO sec_companies
                SELECT c.cik, c.name, c.ticker, c.tickers, c.exchange, c.exchanges,
                       f.sic, s.industry, s.office, ?
                FROM st_companies c
                LEFT JOIN (SELECT cik, arg_max(sic, filed) AS sic FROM sec_filings
                           WHERE sic IS NOT NULL GROUP BY cik) f USING (cik)
                LEFT JOIN sec_sic s ON s.sic = f.sic""", [dt.datetime.utcnow()])
        else:
            con.execute("""
                INSERT INTO sec_companies
                SELECT cik, name, ticker, tickers, exchange, exchanges,
                       NULL, NULL, NULL, ? FROM st_companies""", [dt.datetime.utcnow()])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def run(db_path, ua=None):
    ua = ua or user_agent()
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    con = duckdb.connect(db_path)
    try:
        con.execute(SCHEMA_SQL)
        t0 = time.time()
        raw_t = fetch(TICKERS_URL, ua)
        sha_t, path_t = archive(raw_t, "company_tickers_exchange.json", day)
        time.sleep(0.5)
        raw_s = fetch(SIC_URL, ua)
        sha_s, path_s = archive(raw_s, "sic-code-list.html", day)
        try:
            tickers = parse_tickers(raw_t)
            sic_rows = parse_sic(raw_s)
            companies = companies_from(tickers)
            load(con, companies, sic_rows)
        except Exception as e:                                    # noqa: BLE001
            record_pull(con, "universe", TICKERS_URL, sha_t, len(raw_t), path_t, "failed", 0,
                        "%s: %s" % (type(e).__name__, str(e)[:300]))
            raise
        record_pull(con, "tickers", TICKERS_URL, sha_t, len(raw_t), path_t, "ok", len(tickers))
        record_pull(con, "sic", SIC_URL, sha_s, len(raw_s), path_s, "ok", len(sic_rows))
        n_cos, n_sic, n_ind = con.execute(
            "SELECT (SELECT count(*) FROM sec_companies), (SELECT count(*) FROM sec_sic), "
            "(SELECT count(*) FROM sec_companies WHERE industry IS NOT NULL)").fetchone()
        print("sec-universe: %d tickers -> %d companies (%d with an industry from the shelf), "
              "%d SIC codes, %.1fs" % (len(tickers), n_cos, n_ind, n_sic, time.time() - t0))
        return 0
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    try:
        return run(args.db)
    except UniverseError as e:
        print("ATTENTION sec-universe: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
