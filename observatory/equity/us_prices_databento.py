# -*- coding: utf-8 -*-
"""US daily stock prices from Databento, archived and loaded.

Dataset EQUS.SUMMARY ("Databento Equities Summary", publisher 90): one
consolidated end-of-day bar per listed US symbol per trading day — open,
high, low, close, volume across every exchange — from 1 July 2024, posted
the morning after. Databento sells it by the byte (about $4.50 for a year of
every symbol) and states that its datasets may be redistributed externally
after 24 hours, so yesterday's close can be shown to readers; confirm that
reading in writing with Databento before a public page relies on it.

Money is the thing to be careful with here, so the loader behaves like the
futures downloader (tools/databento_download.py):

  1. ask Databento what the request will cost (metadata.get_cost);
  2. refuse to download when the quote exceeds --max-cost-usd, or when no
     cap was given; nothing is billed by a quote;
  3. download once, streaming to a .partial file so an interrupted run
     cannot be repeated by accident, then archive the gzipped CSV under data/raw/us-prices/ with the
     SHA-256 of the CSV as Databento states it and record the pull (quote, bytes,
     rows) in us_px_pulls;
  4. load the archive into us_px_daily: the days covered are replaced
     wholesale inside one transaction, so a re-pull of a day is a clean
     replacement, never a duplicate.

Values are stored exactly as delivered (prices in dollars to nine decimals
as Databento prints them, volume in shares) under the symbol Databento maps
for the day. Missing is missing: a symbol with no bar on a day has no row.

Run from observatory/ against a COPY of the served database:
    ./.venv/bin/python equity/us_prices_databento.py --db data/sec.work.duckdb --quote
    ./.venv/bin/python equity/us_prices_databento.py --db data/sec.work.duckdb --max-cost-usd 12

Without --start/--end the loader asks for the days after the last one it
holds, up to yesterday — the nightly top-up. Python 3.9; stdlib + duckdb.
"""
import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.environ.get("US_PRICES_RAW", os.path.join(HERE, "..", "data", "raw", "us-prices"))
SETTINGS = os.path.join(HERE, "..", ".env")
GATEWAY = "https://hist.databento.com/v0/"
DATASET = "EQUS.SUMMARY"
SCHEMA = "ohlcv-1d"
PUBLISHER = 90
PARSER_VERSION = "uspx-1"
EXTRACTOR = "us-prices"
FIRST_DAY = dt.date(2024, 7, 1)          # the dataset's own start

# Older history (added 2026-10-19). Databento's combined feed EQUS.MINI starts
# 28 March 2023; before that only single exchanges go back, to 1 May 2018. From
# those, each stock's bar comes from its listing exchange (sec_companies.exchange,
# the SEC's company_tickers_exchange): that exchange's closing auction sets the
# official close, but its volume is that exchange's share only. The nightly
# top-up stays on EQUS.SUMMARY.
#   dataset -> (first day, listing exchange whose stocks are kept or None for all)
DATASETS = {
    "EQUS.SUMMARY": (dt.date(2024, 7, 1), None),
    "EQUS.MINI": (dt.date(2023, 3, 28), None),
    "XNAS.ITCH": (dt.date(2018, 5, 1), "Nasdaq"),
    "XNYS.PILLAR": (dt.date(2018, 5, 1), "NYSE"),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS us_px_pulls (
    pull_id INTEGER PRIMARY KEY, requested_at TIMESTAMP, start_date DATE, end_date DATE,
    quote_usd DOUBLE, max_cost_usd DOUBLE, status VARCHAR, csv_bytes BIGINT, rows BIGINT,
    sha256 VARCHAR, raw_path VARCHAR, parser_version VARCHAR, detail VARCHAR);
CREATE SEQUENCE IF NOT EXISTS us_px_pull_seq;
CREATE TABLE IF NOT EXISTS us_px_daily (
    symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume BIGINT, instrument_id BIGINT, publisher_id INTEGER, pull_id INTEGER);
CREATE INDEX IF NOT EXISTS us_px_daily_sym ON us_px_daily (symbol, date);
CREATE INDEX IF NOT EXISTS us_px_daily_date ON us_px_daily (date);
"""


class PriceError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_key(path=SETTINGS):
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key and os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            name, sep, value = raw.strip().partition("=")
            if sep and name.strip() == "DATABENTO_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not re.fullmatch(r"db-[A-Za-z0-9_-]{29}", key or ""):
        raise PriceError("DATABENTO_API_KEY missing or malformed (set it in observatory/.env; never in chat)")
    return key


class Client(object):
    def __init__(self, key):
        self._auth = "Basic " + base64.b64encode((key + ":").encode()).decode()
        self._opener = build_opener(NoRedirects())

    def open(self, endpoint, params, post=False, timeout=600):
        encoded = urlencode(params).encode("ascii")
        headers = {"Authorization": self._auth, "User-Agent": "plover-us-prices/1"}
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = Request(GATEWAY + endpoint, data=encoded, headers=headers, method="POST")
        else:
            req = Request(GATEWAY + endpoint + "?" + encoded.decode(), headers=headers)
        try:
            return self._opener.open(req, timeout=timeout)
        except HTTPError as e:
            body = ""
            try:
                body = e.read(2000).decode("utf-8", "replace")
            except Exception:                                     # noqa: BLE001
                pass
            raise PriceError("Databento HTTP %d on %s: %s" % (e.code, endpoint, body[:300]))
        except (URLError, OSError) as e:
            raise PriceError("Databento connection failed: %s" % e)

    def json(self, endpoint, params, post=False, timeout=300):
        with self.open(endpoint, params, post=post, timeout=timeout) as r:
            return json.load(r)


def request_params(start, end):
    """Databento's `end` is exclusive, so a run through yesterday asks for end = today."""
    return {"dataset": DATASET, "schema": SCHEMA, "symbols": "ALL_SYMBOLS",
            "stype_in": "raw_symbol", "start": start.isoformat(),
            "end": (end + dt.timedelta(days=1)).isoformat()}


def available_end(client):
    """The last trading day the dataset holds (Databento's range end is exclusive)."""
    rng = client.json("metadata.get_dataset_range", {"dataset": DATASET})
    end = rng.get("schema", {}).get(SCHEMA, {}).get("end") or rng.get("end")
    if not end:
        raise PriceError("Databento gave no range for %s" % DATASET)
    return dt.date.fromisoformat(end[:10]) - dt.timedelta(days=1)


def quote(client, start, end):
    raw = client.json("metadata.get_cost", request_params(start, end), post=True)
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError):
        raise PriceError("Databento returned an unreadable cost estimate: %r" % (raw,))


def last_loaded(con):
    if not con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'us_px_daily'").fetchone()[0]:
        return None
    return con.execute("SELECT max(date) FROM us_px_daily").fetchone()[0]


def _jobs(client):
    out = client.json("batch.list_jobs", {"states": "queued,processing,done"})
    if isinstance(out, dict):
        out = out.get("jobs") or out.get("data") or []
    return [j for j in out if isinstance(j, dict)]


def download(client, start, end, dest_gz, poll_seconds=10, max_wait=3600, job_id=None):
    """Submit a batch job for every symbol with symbols mapped (the streaming
    route refuses ALL_SYMBOLS + map_symbols), wait for it, then stream the CSV
    into dest_gz (via a .partial file) and check Databento's SHA-256.
    Returns (rows, bytes, sha256, job_id)."""
    params = dict(request_params(start, end), encoding="csv", compression="none",
                  pretty_px="true", pretty_ts="true", map_symbols="true",
                  split_duration="none", delivery="download")
    os.makedirs(os.path.dirname(dest_gz), exist_ok=True)
    partial = dest_gz + ".partial"
    with open(partial, "xb") as reserved:          # 'x': a repeat cannot bill twice by accident
        if job_id is None:
            # A gateway timeout on submit can still create the job, so look for
            # one with this exact request before submitting a second, billable copy.
            want_start, want_end = request_params(start, end)["start"], request_params(start, end)["end"]
            for j in _jobs(client):
                if (j.get("dataset") == DATASET and j.get("schema") == SCHEMA and j.get("symbols") == "ALL_SYMBOLS"
                        and str(j.get("start", ""))[:10] == want_start and str(j.get("end", ""))[:10] == want_end
                        and j.get("state") in ("received", "queued", "processing", "done")):
                    job_id = j["id"]
                    print("us-prices: reusing batch job %s already on Databento's queue" % job_id)
                    break
        if job_id is None:
            job = client.json("batch.submit_job", params, post=True)
            job_id = job.get("id")
            if not job_id:
                raise PriceError("Databento accepted no batch job: %r" % (job,))
            print("us-prices: batch job %s submitted; waiting" % job_id)
        sys.stdout.flush()
        files, waited, hiccups = None, 0, 0
        while waited <= max_wait:
            time.sleep(poll_seconds)
            waited += poll_seconds
            try:
                state = next((j.get("state") for j in _jobs(client) if j.get("id") == job_id), None)
                if state == "done":
                    files = client.json("batch.list_files", {"job_id": job_id})
                    break
            except PriceError as e:
                # Databento's gateway times out now and then while a job runs;
                # a transient 5xx is not a reason to abandon a job already paid for.
                hiccups += 1
                print("us-prices: poll hiccup %d (%s); still waiting on %s" % (hiccups, str(e)[:80], job_id))
                sys.stdout.flush()
                if hiccups > 30:
                    raise PriceError("Databento kept failing while job %s ran; rerun with --job-id %s" % (job_id, job_id))
                continue
            if state in ("expired",):
                raise PriceError("batch job %s expired before download" % job_id)
        if files is None:
            raise PriceError("batch job %s not done after %ds; the .partial file is kept" % (job_id, max_wait))
        csv_files = [f for f in files if str(f.get("filename", "")).endswith(".csv")]
        if len(csv_files) != 1:
            raise PriceError("expected one CSV in job %s, got %r" % (job_id, [f.get("filename") for f in files]))
        f = csv_files[0]
        url = (f.get("urls") or {}).get("https")
        want = str(f.get("hash") or "").split(":")[-1].lower()
        if not url:
            raise PriceError("job %s file has no https url" % job_id)
        rows, size = 0, 0
        sha = hashlib.sha256()
        req = Request(url, headers={"Authorization": client._auth, "User-Agent": "plover-us-prices/1"})
        with client._opener.open(req, timeout=1800) as resp:
            header = resp.readline(8192)
            cols = next(csv.reader([header.decode("utf-8")]))
            need = {"ts_event", "publisher_id", "instrument_id", "open", "high", "low", "close", "volume", "symbol"}
            if not need.issubset(cols):
                raise PriceError("unexpected CSV header %r; the .partial file is kept for inspection" % cols)
            sha.update(header)
            size += len(header)
            with gzip.GzipFile(fileobj=reserved, mode="wb", mtime=0) as gz:
                gz.write(header)
                last = b""
                for block in iter(lambda: resp.read(1 << 20), b""):
                    gz.write(block)
                    sha.update(block)
                    size += len(block)
                    rows += block.count(b"\n")
                    last = block[-1:]
                if last and last != b"\n":
                    rows += 1
    if want and sha.hexdigest() != want:
        os.replace(partial, dest_gz + ".badhash")
        raise PriceError("SHA-256 of the downloaded CSV does not match Databento's; file kept as .badhash")
    if not rows:
        os.replace(partial, dest_gz + ".empty")
        raise PriceError("no bars returned for %s..%s; nothing loaded" % (start, end))
    os.replace(partial, dest_gz)
    return rows, size, sha.hexdigest(), job_id


def _listed_only(con, exchange):
    """Keep in raw_px only the stocks listed on `exchange`. SEC tickers write a
    share class with a hyphen (BRK-B) where the exchanges use a dot (BRK.B)."""
    con.execute("""CREATE OR REPLACE TEMP TABLE listed AS
        SELECT DISTINCT s AS symbol FROM (
            SELECT ticker AS s FROM sec_companies WHERE exchange = ?
            UNION ALL SELECT replace(ticker, '-', '.') FROM sec_companies WHERE exchange = ?
        ) WHERE s IS NOT NULL""", [exchange, exchange])
    con.execute("DELETE FROM raw_px WHERE symbol NOT IN (SELECT symbol FROM listed)")


def load(con, gz_path, start, end, pull_id):
    con.execute("""CREATE OR REPLACE TEMP TABLE raw_px AS
        SELECT * FROM read_csv(?, header=true, all_varchar=true)""", [gz_path])
    listing = DATASETS.get(DATASET, (None, None))[1]
    if listing:
        _listed_only(con, listing)
    dupes = con.execute("""SELECT count(*) FROM (SELECT symbol, substr(ts_event, 1, 10) d FROM raw_px
        GROUP BY 1, 2 HAVING count(*) > 1)""").fetchone()[0]
    if dupes:
        raise PriceError("%d symbol-days carry more than one bar in %s; nothing loaded" % (dupes, DATASET))
    bad = con.execute("""SELECT count(*) FROM raw_px
        WHERE try_cast(close AS DOUBLE) IS NULL OR try_cast(substr(ts_event, 1, 10) AS DATE) IS NULL""").fetchone()[0]
    total = con.execute("SELECT count(*) FROM raw_px").fetchone()[0]
    if total and bad > total * 0.001:
        raise PriceError("%d of %d rows have no readable close or date" % (bad, total))
    con.execute("BEGIN TRANSACTION")
    try:
        if listing:
            # replace only this exchange's stocks: the other exchange's file
            # covers the same days
            con.execute("DELETE FROM us_px_daily WHERE date >= ? AND date <= ? "
                        "AND symbol IN (SELECT symbol FROM listed)", [start, end])
        else:
            con.execute("DELETE FROM us_px_daily WHERE date >= ? AND date <= ?", [start, end])
        con.execute("""
            INSERT INTO us_px_daily
            SELECT symbol, CAST(substr(ts_event, 1, 10) AS DATE), try_cast(open AS DOUBLE),
                   try_cast(high AS DOUBLE), try_cast(low AS DOUBLE), try_cast(close AS DOUBLE),
                   try_cast(volume AS BIGINT), try_cast(instrument_id AS BIGINT),
                   try_cast(publisher_id AS INTEGER), ?
            FROM raw_px WHERE try_cast(close AS DOUBLE) IS NOT NULL AND symbol IS NOT NULL""", [pull_id])
        n = con.execute("SELECT count(*), count(DISTINCT symbol), min(date), max(date) FROM us_px_daily "
                        "WHERE pull_id = ?", [pull_id]).fetchone()
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


def record(con, pull_id, start, end, q, cap, status, csv_bytes=None, rows=None, sha=None, path=None, detail=None):
    con.execute("DELETE FROM us_px_pulls WHERE pull_id = ?", [pull_id])
    con.execute("INSERT INTO us_px_pulls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [pull_id, dt.datetime.utcnow(), start, end, float(q) if q is not None else None,
                 float(cap) if cap is not None else None, status, csv_bytes, rows, sha, path,
                 PARSER_VERSION, detail])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--start", help="YYYY-MM-DD; default the day after the last loaded (or the dataset start)")
    ap.add_argument("--end", help="YYYY-MM-DD inclusive; default yesterday (UTC)")
    ap.add_argument("--quote", action="store_true", help="print the cost estimate and stop; bills nothing")
    ap.add_argument("--max-cost-usd", type=Decimal, help="download only if the estimate is at or under this")
    ap.add_argument("--job-id", help="download an existing Databento batch job instead of submitting one")
    ap.add_argument("--dataset", default="EQUS.SUMMARY", choices=sorted(DATASETS),
                    help="EQUS.SUMMARY (the nightly feed); the others are older history, with --start and --end")
    args = ap.parse_args(argv)
    global DATASET, FIRST_DAY
    DATASET, FIRST_DAY = args.dataset, DATASETS[args.dataset][0]
    if DATASET != "EQUS.SUMMARY" and not (args.start and args.end):
        ap.error("older history (--dataset %s) needs --start and --end" % DATASET)
    if not args.quote and args.max_cost_usd is None:
        ap.error("give --max-cost-usd to download, or --quote to only ask the price")
    if args.max_cost_usd is not None and (not args.max_cost_usd.is_finite() or args.max_cost_usd < 0):
        ap.error("--max-cost-usd must be a finite, non-negative dollar amount")

    con = duckdb.connect(args.db)
    try:
        con.execute(SCHEMA_SQL)
        yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        end = dt.date.fromisoformat(args.end) if args.end else yesterday
        if args.start:
            start = dt.date.fromisoformat(args.start)
        else:
            last = last_loaded(con)
            start = (last + dt.timedelta(days=1)) if last else FIRST_DAY
        if start > end:
            print("us-prices: already holds %s; nothing to request" % (start - dt.timedelta(days=1)))
            return 0
        client = Client(read_key())
        avail = available_end(client)
        if end > avail:
            end = avail                      # a weekend or this morning: nothing newer exists yet
        if start > end:
            print("us-prices: holds everything Databento has (through %s); nothing to request" % end)
            return 0
        q = quote(client, start, end)
        print("us-prices: %s..%s, %s %s ALL_SYMBOLS — Databento quotes $%s" % (start, end, DATASET, SCHEMA, q))
        if args.quote:
            return 0
        pull_id = con.execute("SELECT nextval('us_px_pull_seq')").fetchone()[0]
        if q > args.max_cost_usd:
            record(con, pull_id, start, end, q, args.max_cost_usd, "refused",
                   detail="quote above --max-cost-usd; nothing downloaded")
            print("ATTENTION us-prices: quote $%s exceeds the cap $%s; nothing downloaded" % (q, args.max_cost_usd))
            return 1
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        dest = os.path.join(RAW_ROOT, day, "%s_%s_%s_%s.csv.gz" % (
            DATASET.lower().replace(".", "-"), start.isoformat(), end.isoformat(),
            dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")))
        t0 = time.time()
        rows, csv_bytes, sha, job_id = download(client, start, end, dest, job_id=args.job_id)
        n = load(con, dest, start, end, pull_id)
        record(con, pull_id, start, end, q, args.max_cost_usd, "ok", csv_bytes, rows, sha, dest,
               detail="%s, batch job %s" % (DATASET, job_id))
        print("us-prices: %d bars for %d symbols, %s..%s, %.1fMB CSV, %.0fs; quoted $%s"
              % (n[0], n[1], n[2], n[3], csv_bytes / 1e6, time.time() - t0, q))
        return 0
    except PriceError as e:
        print("ATTENTION us-prices: %s" % e)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
