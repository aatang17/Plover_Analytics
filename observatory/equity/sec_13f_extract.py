# -*- coding: utf-8 -*-
"""US institutional holdings: the SEC's Form 13F data sets, loaded.

Every investment manager with over $100m in US-listed equities files a
Form 13F within 45 days of each quarter end, listing each position by
issuer, CUSIP, dollar value and shares. The SEC publishes all of them,
already parsed, as *Form 13F Data Sets* — one zip per filing window
(quarterly, from 2013q2; since 2026 named by date range, e.g.
``01jun2026-31aug2026_form13f.zip``) with SUBMISSION, COVERPAGE,
INFOTABLE (every position), SUMMARYPAGE, OTHERMANAGER and SIGNATURE
tables. us_capture.py banks the zips under us/{day}/sec-13f/; this loader
puts one window (about 12,000 filings and 3.8 million positions) into
data/sec.duckdb in under a minute.

Tables (all values as the SEC extracted them, never recomputed):

  sec_13f_batches    one vintage per zip: window, SHA-256, counts, status
  sec_13f_filings    one row per 13F filing: manager, report period, type,
                     amendment flag, the manager's own totals
  sec_13f_holdings   every position: issuer name and class as the manager
                     wrote them, CUSIP, FIGI where given, value, shares or
                     principal amount, put/call, discretion, voting authority

Identity is the CUSIP: the data sets carry no issuer CIK, and the SEC's
own name field is free text, so an issuer's holders are found by CUSIP
(and by name match as a convenience, never as the key). Values are in US
dollars for reports from 2023 on, in thousands before (the SEC changed the
form); the batch records which, and the API says so on every row.

A window is replaced wholesale inside one transaction. Amendments keep
their own accession and are flagged; nothing is merged.

Run from observatory/ against a copy of the served file:
    ./.venv/bin/python equity/sec_13f_extract.py --db data/sec.work.duckdb --last 2

Python 3.9; stdlib + duckdb (+ boto3 in S3 mode).
"""
import argparse
import datetime as dt
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

import duckdb

import sec_shelf

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
SOURCE = "sec-13f"
PARSER_VERSION = "sec13f-1"
EXTRACTOR = "sec-13f"
DEFAULT_LAST = int(os.environ.get("SEC_13F_BATCHES", "2"))

MEMBERS = ("SUBMISSION", "COVERPAGE", "INFOTABLE", "SUMMARYPAGE")
_RANGE = re.compile(r"^(\d{2})([a-z]{3})(\d{4})-(\d{2})([a-z]{3})(\d{4})_form13f", re.I)
_QUARTER = re.compile(r"^(\d{4})q([1-4])_form13f", re.I)
_MON = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                     "sep", "oct", "nov", "dec"), 1)}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_13f_batches (
    batch VARCHAR PRIMARY KEY, window_start DATE, window_end DATE, key VARCHAR, sha256 VARCHAR,
    content_hash VARCHAR, captured_at TIMESTAMP, loaded_at TIMESTAMP, parser_version VARCHAR,
    filings INTEGER, holdings INTEGER, value_unit VARCHAR, status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_13f_filings (
    accession VARCHAR PRIMARY KEY, batch VARCHAR, filed DATE, submission_type VARCHAR,
    manager_cik BIGINT, report_period DATE, amendment BOOLEAN, amendment_no INTEGER,
    amendment_type VARCHAR, manager_name VARCHAR, city VARCHAR, state VARCHAR,
    report_type VARCHAR, file_number VARCHAR, other_managers INTEGER,
    table_entries INTEGER, table_value DOUBLE, confidential_omitted BOOLEAN);
CREATE TABLE IF NOT EXISTS sec_13f_holdings (
    accession VARCHAR, batch VARCHAR, sk BIGINT, issuer_name VARCHAR, title_of_class VARCHAR,
    cusip VARCHAR, figi VARCHAR, value DOUBLE, shares DOUBLE, shares_type VARCHAR,
    put_call VARCHAR, discretion VARCHAR, other_manager VARCHAR,
    voting_sole DOUBLE, voting_shared DOUBLE, voting_none DOUBLE);
CREATE INDEX IF NOT EXISTS sec_13f_holdings_cusip ON sec_13f_holdings (cusip);
CREATE INDEX IF NOT EXISTS sec_13f_holdings_acc ON sec_13f_holdings (accession);
CREATE INDEX IF NOT EXISTS sec_13f_filings_mgr ON sec_13f_filings (manager_cik, report_period);
"""


class BatchError(Exception):
    pass


def batch_of(filename):
    """'01jun2026-31aug2026_form13f.zip' -> ('01jun2026-31aug2026', start, end);
    '2013q4_form13f.zip' -> ('2013q4', quarter start, quarter end). None if neither."""
    stem = (filename or "").rsplit("/", 1)[-1]
    m = _RANGE.match(stem)
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        try:
            start = dt.date(int(y1), _MON[m1.lower()], int(d1))
            end = dt.date(int(y2), _MON[m2.lower()], int(d2))
        except (KeyError, ValueError):
            return None
        return stem.split("_form13f")[0].lower(), start, end
    m = _QUARTER.match(stem)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        start = dt.date(y, (q - 1) * 3 + 1, 1)
        end = dt.date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - dt.timedelta(days=1)
        return "%dq%d" % (y, q), start, end
    return None


def unpack(zip_path, workdir):
    paths = {}
    with zipfile.ZipFile(zip_path) as z:
        names = {n.upper().rsplit(".", 1)[0]: n for n in z.namelist() if n.lower().endswith(".tsv")}
        for m in MEMBERS:
            if m not in names:
                raise BatchError("zip lacks %s.tsv (has %s)" % (m, ", ".join(sorted(names))))
            paths[m] = z.extract(names[m], workdir)
    return paths


def stage(con, paths):
    for m, p in paths.items():
        con.execute("CREATE OR REPLACE TEMP TABLE raw_%s AS SELECT * FROM read_csv(?, "
                    "delim='\\t', header=true, quote='', escape='', all_varchar=true, "
                    "ignore_errors=true, null_padding=true)" % m.lower(), [p])


D = "try_strptime(nullif(trim(%s), ''), '%%d-%%b-%%Y')::DATE"
N = "try_cast(nullif(trim(%s), '') AS DOUBLE)"
I = "try_cast(nullif(trim(%s), '') AS INTEGER)"
YN = "CASE WHEN upper(trim(%s)) IN ('Y','YES','TRUE','1') THEN TRUE WHEN upper(trim(%s)) IN ('N','NO','FALSE','0') THEN FALSE END"


def validate(con):
    n_sub = con.execute("SELECT count(*) FROM raw_submission").fetchone()[0]
    if n_sub < 100:
        raise BatchError("only %d submissions; not a real window" % n_sub)
    n_cov = con.execute("SELECT count(*) FROM raw_coverpage").fetchone()[0]
    if n_cov != n_sub:
        raise BatchError("%d cover pages for %d submissions" % (n_cov, n_sub))
    orphans = con.execute("""
        SELECT count(*) FROM raw_infotable t
        WHERE NOT EXISTS (SELECT 1 FROM raw_submission s WHERE s.ACCESSION_NUMBER = t.ACCESSION_NUMBER)
        """).fetchone()[0]
    if orphans:
        raise BatchError("%d positions name a filing the set lacks" % orphans)
    bad_cusip = con.execute(
        "SELECT count(*) FROM raw_infotable WHERE length(trim(CUSIP)) NOT IN (8, 9)").fetchone()[0]
    n_pos = con.execute("SELECT count(*) FROM raw_infotable").fetchone()[0]
    if n_pos and bad_cusip > n_pos * 0.02:
        raise BatchError("%d of %d CUSIPs are not 8 or 9 characters" % (bad_cusip, n_pos))
    return n_sub, n_pos


def load_batch(con, batch, window, rec, src, workdir):
    zip_path = os.path.join(workdir, "form13f.zip")
    t0 = time.time()
    src.fetch(rec, zip_path)
    paths = unpack(zip_path, workdir)
    stage(con, paths)
    n_sub, n_pos = validate(con)
    start, end = window
    # The SEC moved the value column from thousands to whole dollars for
    # reports on periods ending after 2022; a window is one or the other.
    value_unit = "USD" if start >= dt.date(2023, 1, 1) else "USD thousands"
    con.execute("BEGIN TRANSACTION")
    try:
        for t in ("sec_13f_filings", "sec_13f_holdings"):
            con.execute("DELETE FROM %s WHERE batch = ?" % t, [batch])
            con.execute("DELETE FROM %s WHERE accession IN (SELECT ACCESSION_NUMBER FROM raw_submission)" % t)
        con.execute("""
            INSERT INTO sec_13f_filings
            SELECT s.ACCESSION_NUMBER, ?, %s, nullif(trim(s.SUBMISSIONTYPE), ''),
                   try_cast(nullif(trim(s.CIK), '') AS BIGINT), %s,
                   coalesce(%s, trim(s.SUBMISSIONTYPE) LIKE '%%/A'), %s,
                   nullif(trim(c.AMENDMENTTYPE), ''), nullif(trim(c.FILINGMANAGER_NAME), ''),
                   nullif(trim(c.FILINGMANAGER_CITY), ''), nullif(trim(c.FILINGMANAGER_STATEORCOUNTRY), ''),
                   nullif(trim(c.REPORTTYPE), ''), nullif(trim(c.FORM13FFILENUMBER), ''),
                   %s, %s, %s, %s
            FROM raw_submission s
            JOIN raw_coverpage c ON c.ACCESSION_NUMBER = s.ACCESSION_NUMBER
            LEFT JOIN raw_summarypage p ON p.ACCESSION_NUMBER = s.ACCESSION_NUMBER
            """ % (D % "s.FILING_DATE", D % "s.PERIODOFREPORT",
                   YN % ("c.ISAMENDMENT", "c.ISAMENDMENT"), I % "c.AMENDMENTNO",
                   I % "p.OTHERINCLUDEDMANAGERSCOUNT", I % "p.TABLEENTRYTOTAL",
                   N % "p.TABLEVALUETOTAL", YN % ("p.ISCONFIDENTIALOMITTED", "p.ISCONFIDENTIALOMITTED")),
                    [batch])
        con.execute("""
            INSERT INTO sec_13f_holdings
            SELECT ACCESSION_NUMBER, ?, try_cast(INFOTABLE_SK AS BIGINT), nullif(trim(NAMEOFISSUER), ''),
                   nullif(trim(TITLEOFCLASS), ''), nullif(upper(trim(CUSIP)), ''), nullif(trim(FIGI), ''),
                   %s, %s, nullif(upper(trim(SSHPRNAMTTYPE)), ''), nullif(upper(trim(PUTCALL)), ''),
                   nullif(upper(trim(INVESTMENTDISCRETION)), ''), nullif(trim(OTHERMANAGER), ''),
                   %s, %s, %s
            FROM raw_infotable
            """ % (N % "VALUE", N % "SSHPRNAMT", N % "VOTING_AUTH_SOLE", N % "VOTING_AUTH_SHARED",
                   N % "VOTING_AUTH_NONE"), [batch])
        con.execute("DELETE FROM sec_13f_batches WHERE batch = ?", [batch])
        con.execute("INSERT INTO sec_13f_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [batch, start, end, rec.get("key"), rec.get("sha256"), rec.get("content_hash"),
                     rec.get("captured_at"), dt.datetime.utcnow(), PARSER_VERSION, n_sub, n_pos,
                     value_unit, "ok", None])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("  %s: %d filings, %d positions, values in %s, %.0fs"
          % (batch, n_sub, n_pos, value_unit, time.time() - t0))


def record_failure(con, batch, window, rec, err):
    con.execute("DELETE FROM sec_13f_batches WHERE batch = ?", [batch])
    con.execute("INSERT INTO sec_13f_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [batch, window[0], window[1], rec.get("key"), rec.get("sha256"),
                 rec.get("content_hash"), rec.get("captured_at"), dt.datetime.utcnow(),
                 PARSER_VERSION, 0, 0, None, "failed", err])


def choose(avail, loaded, last, explicit, everything):
    order = sorted(avail, key=lambda b: avail[b][0][1], reverse=True)   # by window end
    if explicit:
        return [b for b in order if b in explicit]
    changed = [b for b in order if b in loaded and (
        loaded[b][0] != avail[b][1].get("sha256") or loaded[b][1] != PARSER_VERSION
        or loaded[b][2] == "failed")]
    fresh = [b for b in order if b not in loaded]
    if everything:
        return changed + fresh
    newest = [b for b in order[:last] if b not in loaded]
    out, seen = [], set()
    for b in changed + newest:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--root", help="local shelf root (default US_ARCHIVE_ROOT or the laptop copy)")
    ap.add_argument("--last", type=int, default=DEFAULT_LAST)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--batches", help="comma-separated window names, e.g. 01jun2026-31aug2026")
    args = ap.parse_args()
    src = sec_shelf.make_source(args.source, SOURCE, args.root)
    avail = {}
    for filename, rec in src.pointers().items():
        b = batch_of(filename)
        if b:
            avail[b[0]] = ((b[1], b[2]), rec)
    if not avail:
        print("no 13F windows on the %s shelf; nothing to do" % src.name)
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    loaded = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT batch, sha256, parser_version, status FROM sec_13f_batches").fetchall()}
    explicit = {b.strip().lower() for b in (args.batches or "").split(",") if b.strip()}
    targets = choose(avail, loaded, args.last, explicit, args.all)
    print("13F shelf: %d windows; loaded: %d; to load: %s"
          % (len(avail), len(loaded), ", ".join(targets) or "nothing"))
    sys.stdout.flush()
    failed = []
    for b in targets:
        window, rec = avail[b]
        workdir = tempfile.mkdtemp(prefix="form13f-")
        try:
            load_batch(con, b, window, rec, src, workdir)
        except Exception as e:                                    # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, str(e)[:300])
            print("  %s FAILED: %s — previous load stays live" % (b, err))
            failed.append(b)
            try:
                record_failure(con, b, window, rec, err)
            except Exception as e2:                               # noqa: BLE001
                print("  (could not record the failure: %s)" % e2)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    con.close()
    if failed:
        print("ATTENTION 13F windows failed: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
