# -*- coding: utf-8 -*-
"""US insider trades and holdings: the SEC's Form 3/4/5 data sets, loaded.

Every trade a director, officer or 10% holder reports in a US company is a
Form 4 (initial holdings a Form 3, late or exempt ones a Form 5). The SEC
publishes all of them, already parsed, as quarterly *Insider Transactions
Data Sets* (from 2006q1): a zip of tab-separated tables — SUBMISSION (one
row per filing: issuer, dates, 10b5-1 flag), REPORTINGOWNER (who filed and
in what capacity), NONDERIV_TRANS / DERIV_TRANS (each trade: date, code,
shares, price, holding afterwards), NONDERIV_HOLDING / DERIV_HOLDING and
FOOTNOTES. us_capture.py banks the zips under us/{day}/sec-insiders/; this
loader puts a quarter into data/sec.duckdb in a few seconds.

Tables (all values as the SEC extracted them, never recomputed):

  sec_ins_quarters      one vintage per zip: SHA-256, counts, status
  sec_ins_filings       one row per Form 3/4/5 (accession is the key)
  sec_ins_owners        the reporting owner(s) of a filing with relationship flags
  sec_ins_transactions  every trade, derivative or not, with the SEC's
                        transaction code (P open-market purchase, S sale,
                        A award, M option exercise, F tax withholding, G gift …)
  sec_ins_holdings      positions reported without a trade (Form 3, and
                        holdings lines on 4/5)
  sec_ins_footnotes     the filer's footnotes, by id, so a price or a plan can
                        be read in the filer's own words

A quarter is replaced wholesale inside one transaction, so a re-run of the
SEC's set is a clean vintage. Amendments (4/A) keep their own accession and
are flagged; nothing is merged or corrected. Missing is NULL, never 0.

Run from observatory/ against a copy of the served file:
    ./.venv/bin/python equity/sec_insiders_extract.py --db data/sec.work.duckdb --last 2

Python 3.9; stdlib + duckdb (+ boto3 in S3 mode).
"""
import argparse
import datetime as dt
import os
import shutil
import sys
import tempfile
import time
import zipfile

import duckdb

import sec_shelf

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
SOURCE = "sec-insiders"
PARSER_VERSION = "secins-1"
EXTRACTOR = "sec-insiders"
DEFAULT_LAST = int(os.environ.get("SEC_INSIDER_QUARTERS", "2"))

MEMBERS = ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS", "DERIV_TRANS",
           "NONDERIV_HOLDING", "DERIV_HOLDING", "FOOTNOTES")
KNOWN_CODES = set("PSAMFGDCXJWIEHOULZVK")   # SEC Form 4 transaction codes

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_ins_quarters (
    quarter VARCHAR PRIMARY KEY, key VARCHAR, sha256 VARCHAR, content_hash VARCHAR,
    captured_at TIMESTAMP, loaded_at TIMESTAMP, parser_version VARCHAR,
    filings INTEGER, transactions INTEGER, holdings INTEGER, unknown_codes INTEGER,
    status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ins_filings (
    accession VARCHAR PRIMARY KEY, quarter VARCHAR, filed DATE, period DATE, orig_filed DATE,
    document_type VARCHAR, amendment BOOLEAN, issuer_cik BIGINT, issuer_name VARCHAR,
    issuer_symbol VARCHAR, no_securities BOOLEAN, not_subject_16 BOOLEAN,
    plan_10b5_1 BOOLEAN, remarks VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ins_owners (
    accession VARCHAR, quarter VARCHAR, owner_cik BIGINT, owner_name VARCHAR,
    relationship VARCHAR, is_director BOOLEAN, is_officer BOOLEAN, is_ten_percent BOOLEAN,
    is_other BOOLEAN, title VARCHAR, other_text VARCHAR, state VARCHAR, file_number VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ins_transactions (
    accession VARCHAR, quarter VARCHAR, sk BIGINT, derivative BOOLEAN, security_title VARCHAR,
    trans_date DATE, deemed_date DATE, form_type VARCHAR, trans_code VARCHAR,
    acquired_disposed VARCHAR, shares DOUBLE, price DOUBLE, total_value DOUBLE,
    shares_after DOUBLE, value_after DOUBLE, direct_indirect VARCHAR, nature VARCHAR,
    timeliness VARCHAR, equity_swap BOOLEAN, exercise_price DOUBLE, exercise_date DATE,
    expiration_date DATE, underlying_title VARCHAR, underlying_shares DOUBLE,
    footnotes VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ins_holdings (
    accession VARCHAR, quarter VARCHAR, sk BIGINT, derivative BOOLEAN, security_title VARCHAR,
    form_type VARCHAR, shares DOUBLE, value DOUBLE, direct_indirect VARCHAR, nature VARCHAR,
    exercise_price DOUBLE, exercise_date DATE, expiration_date DATE, underlying_title VARCHAR,
    underlying_shares DOUBLE, footnotes VARCHAR);
CREATE TABLE IF NOT EXISTS sec_ins_footnotes (
    accession VARCHAR, quarter VARCHAR, footnote_id VARCHAR, text VARCHAR);
CREATE INDEX IF NOT EXISTS sec_ins_filings_issuer ON sec_ins_filings (issuer_cik, filed);
CREATE INDEX IF NOT EXISTS sec_ins_trans_acc ON sec_ins_transactions (accession);
CREATE INDEX IF NOT EXISTS sec_ins_owners_acc ON sec_ins_owners (accession);
"""


class QuarterError(Exception):
    pass


def unpack(zip_path, workdir):
    paths = {}
    with zipfile.ZipFile(zip_path) as z:
        names = {n.upper().rsplit(".", 1)[0]: n for n in z.namelist() if n.lower().endswith(".tsv")}
        for m in MEMBERS:
            if m not in names:
                raise QuarterError("zip lacks %s.tsv (has %s)" % (m, ", ".join(sorted(names))))
            paths[m] = z.extract(names[m], workdir)
    return paths


def stage(con, paths):
    """Raw tables as text; the SEC's TSVs carry no quoting and a few stray
    tabs inside free text, which read_csv tolerates with ignore_errors."""
    for m, p in paths.items():
        con.execute("CREATE OR REPLACE TEMP TABLE raw_%s AS SELECT * FROM read_csv(?, "
                    "delim='\\t', header=true, quote='', escape='', all_varchar=true, "
                    "ignore_errors=true, null_padding=true)" % m.lower(), [p])


D = "try_strptime(nullif(trim(%s), ''), '%%d-%%b-%%Y')::DATE"
N = "try_cast(nullif(trim(%s), '') AS DOUBLE)"
B1 = "CASE WHEN trim(%s) IN ('1','true','TRUE') THEN TRUE WHEN trim(%s) IN ('0','false','FALSE') THEN FALSE END"


def validate(con):
    n_sub = con.execute("SELECT count(*) FROM raw_submission").fetchone()[0]
    if n_sub < 100:
        raise QuarterError("only %d submissions; not a real quarter" % n_sub)
    bad_dates = con.execute(
        "SELECT count(*) FROM raw_submission WHERE %s IS NULL" % (D % "FILING_DATE")).fetchone()[0]
    if bad_dates > n_sub * 0.01:
        raise QuarterError("%d of %d filing dates do not parse" % (bad_dates, n_sub))
    orphans = con.execute("""
        SELECT count(*) FROM raw_nonderiv_trans t
        WHERE NOT EXISTS (SELECT 1 FROM raw_submission s WHERE s.ACCESSION_NUMBER = t.ACCESSION_NUMBER)
        """).fetchone()[0]
    if orphans:
        raise QuarterError("%d transactions name a filing the set lacks" % orphans)
    unknown = con.execute("""
        SELECT count(*) FROM raw_nonderiv_trans
        WHERE nullif(trim(TRANS_CODE), '') IS NOT NULL AND upper(trim(TRANS_CODE)) NOT IN (%s)
        """ % ",".join("'%s'" % c for c in sorted(KNOWN_CODES))).fetchone()[0]
    return n_sub, unknown


def load_quarter(con, quarter, rec, src, workdir):
    zip_path = os.path.join(workdir, "form345.zip")
    t0 = time.time()
    src.fetch(rec, zip_path)
    paths = unpack(zip_path, workdir)
    stage(con, paths)
    n_sub, unknown = validate(con)
    con.execute("BEGIN TRANSACTION")
    try:
        for t in ("sec_ins_filings", "sec_ins_owners", "sec_ins_transactions",
                  "sec_ins_holdings", "sec_ins_footnotes"):
            con.execute("DELETE FROM %s WHERE quarter = ?" % t, [quarter])
        con.execute("DELETE FROM sec_ins_filings WHERE accession IN (SELECT ACCESSION_NUMBER FROM raw_submission)")
        for t in ("sec_ins_owners", "sec_ins_transactions", "sec_ins_holdings", "sec_ins_footnotes"):
            con.execute("DELETE FROM %s WHERE accession IN (SELECT ACCESSION_NUMBER FROM raw_submission)" % t)
        con.execute("""
            INSERT INTO sec_ins_filings
            SELECT ACCESSION_NUMBER, ?, %s, %s, %s, nullif(trim(DOCUMENT_TYPE), ''),
                   trim(DOCUMENT_TYPE) LIKE '%%/A',
                   try_cast(nullif(trim(ISSUERCIK), '') AS BIGINT), nullif(trim(ISSUERNAME), ''),
                   nullif(upper(trim(ISSUERTRADINGSYMBOL)), ''), %s, %s, %s, nullif(trim(REMARKS), '')
            FROM raw_submission
            """ % (D % "FILING_DATE", D % "PERIOD_OF_REPORT", D % "DATE_OF_ORIG_SUB",
                   B1 % ("NO_SECURITIES_OWNED", "NO_SECURITIES_OWNED"),
                   B1 % ("NOT_SUBJECT_SEC16", "NOT_SUBJECT_SEC16"),
                   B1 % ("AFF10B5ONE", "AFF10B5ONE")), [quarter])
        con.execute("""
            INSERT INTO sec_ins_owners
            SELECT ACCESSION_NUMBER, ?, try_cast(nullif(trim(RPTOWNERCIK), '') AS BIGINT),
                   nullif(trim(RPTOWNERNAME), ''), nullif(trim(RPTOWNER_RELATIONSHIP), ''),
                   upper(coalesce(RPTOWNER_RELATIONSHIP, '')) LIKE '%%DIRECTOR%%',
                   upper(coalesce(RPTOWNER_RELATIONSHIP, '')) LIKE '%%OFFICER%%',
                   upper(coalesce(RPTOWNER_RELATIONSHIP, '')) LIKE '%%TENPERCENT%%',
                   upper(coalesce(RPTOWNER_RELATIONSHIP, '')) LIKE '%%OTHER%%',
                   nullif(trim(RPTOWNER_TITLE), ''), nullif(trim(RPTOWNER_TXT), ''),
                   nullif(trim(RPTOWNER_STATE), ''), nullif(trim(FILE_NUMBER), '')
            FROM raw_reportingowner""", [quarter])
        con.execute("""
            INSERT INTO sec_ins_transactions
            SELECT ACCESSION_NUMBER, ?, try_cast(NONDERIV_TRANS_SK AS BIGINT), FALSE,
                   nullif(trim(SECURITY_TITLE), ''), %s, %s, nullif(trim(TRANS_FORM_TYPE), ''),
                   nullif(upper(trim(TRANS_CODE)), ''), nullif(upper(trim(TRANS_ACQUIRED_DISP_CD)), ''),
                   %s, %s, NULL, %s, %s, nullif(upper(trim(DIRECT_INDIRECT_OWNERSHIP)), ''),
                   nullif(trim(NATURE_OF_OWNERSHIP), ''), nullif(trim(TRANS_TIMELINESS), ''),
                   %s, NULL, NULL, NULL, NULL, NULL,
                   nullif(concat_ws(' ', nullif(trim(TRANS_SHARES_FN), ''), nullif(trim(TRANS_PRICEPERSHARE_FN), ''),
                                    nullif(trim(TRANS_DATE_FN), ''), nullif(trim(SECURITY_TITLE_FN), '')), '')
            FROM raw_nonderiv_trans
            """ % (D % "TRANS_DATE", D % "DEEMED_EXECUTION_DATE", N % "TRANS_SHARES",
                   N % "TRANS_PRICEPERSHARE", N % "SHRS_OWND_FOLWNG_TRANS", N % "VALU_OWND_FOLWNG_TRANS",
                   B1 % ("EQUITY_SWAP_INVOLVED", "EQUITY_SWAP_INVOLVED")), [quarter])
        con.execute("""
            INSERT INTO sec_ins_transactions
            SELECT ACCESSION_NUMBER, ?, try_cast(DERIV_TRANS_SK AS BIGINT), TRUE,
                   nullif(trim(SECURITY_TITLE), ''), %s, %s, nullif(trim(TRANS_FORM_TYPE), ''),
                   nullif(upper(trim(TRANS_CODE)), ''), nullif(upper(trim(TRANS_ACQUIRED_DISP_CD)), ''),
                   %s, %s, %s, %s, %s, nullif(upper(trim(DIRECT_INDIRECT_OWNERSHIP)), ''),
                   nullif(trim(NATURE_OF_OWNERSHIP), ''), nullif(trim(TRANS_TIMELINESS), ''),
                   %s, %s, %s, %s, nullif(trim(UNDLYNG_SEC_TITLE), ''), %s,
                   nullif(concat_ws(' ', nullif(trim(TRANS_SHARES_FN), ''), nullif(trim(TRANS_PRICEPERSHARE_FN), ''),
                                    nullif(trim(TRANS_DATE_FN), ''), nullif(trim(SECURITY_TITLE_FN), '')), '')
            FROM raw_deriv_trans
            """ % (D % "TRANS_DATE", D % "DEEMED_EXECUTION_DATE", N % "TRANS_SHARES",
                   N % "TRANS_PRICEPERSHARE", N % "TRANS_TOTAL_VALUE", N % "SHRS_OWND_FOLWNG_TRANS",
                   N % "VALU_OWND_FOLWNG_TRANS", B1 % ("EQUITY_SWAP_INVOLVED", "EQUITY_SWAP_INVOLVED"),
                   N % "CONV_EXERCISE_PRICE", D % "EXCERCISE_DATE", D % "EXPIRATION_DATE",
                   N % "UNDLYNG_SEC_SHARES"), [quarter])
        con.execute("""
            INSERT INTO sec_ins_holdings
            SELECT ACCESSION_NUMBER, ?, try_cast(NONDERIV_HOLDING_SK AS BIGINT), FALSE,
                   nullif(trim(SECURITY_TITLE), ''), nullif(trim(TRANS_FORM_TYPE), ''), %s, %s,
                   nullif(upper(trim(DIRECT_INDIRECT_OWNERSHIP)), ''), nullif(trim(NATURE_OF_OWNERSHIP), ''),
                   NULL, NULL, NULL, NULL, NULL,
                   nullif(concat_ws(' ', nullif(trim(SHRS_OWND_FOLWNG_TRANS_FN), ''), nullif(trim(SECURITY_TITLE_FN), '')), '')
            FROM raw_nonderiv_holding
            """ % (N % "SHRS_OWND_FOLWNG_TRANS", N % "VALU_OWND_FOLWNG_TRANS"), [quarter])
        con.execute("""
            INSERT INTO sec_ins_holdings
            SELECT ACCESSION_NUMBER, ?, try_cast(DERIV_HOLDING_SK AS BIGINT), TRUE,
                   nullif(trim(SECURITY_TITLE), ''), nullif(trim(TRANS_FORM_TYPE), ''), %s, %s,
                   nullif(upper(trim(DIRECT_INDIRECT_OWNERSHIP)), ''), nullif(trim(NATURE_OF_OWNERSHIP), ''),
                   %s, %s, %s, nullif(trim(UNDLYNG_SEC_TITLE), ''), %s,
                   nullif(concat_ws(' ', nullif(trim(SHRS_OWND_FOLWNG_TRANS_FN), ''), nullif(trim(SECURITY_TITLE_FN), '')), '')
            FROM raw_deriv_holding
            """ % (N % "SHRS_OWND_FOLWNG_TRANS", N % "VALU_OWND_FOLWNG_TRANS", N % "CONV_EXERCISE_PRICE",
                   D % "EXERCISE_DATE", D % "EXPIRATION_DATE", N % "UNDLYNG_SEC_SHARES"), [quarter])
        con.execute("""
            INSERT INTO sec_ins_footnotes
            SELECT ACCESSION_NUMBER, ?, nullif(trim(FOOTNOTE_ID), ''), nullif(trim(FOOTNOTE_TXT), '')
            FROM raw_footnotes""", [quarter])
        n_tr, n_h = con.execute(
            "SELECT (SELECT count(*) FROM sec_ins_transactions WHERE quarter = ?), "
            "(SELECT count(*) FROM sec_ins_holdings WHERE quarter = ?)", [quarter, quarter]).fetchone()
        con.execute("DELETE FROM sec_ins_quarters WHERE quarter = ?", [quarter])
        con.execute("INSERT INTO sec_ins_quarters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [quarter, rec.get("key"), rec.get("sha256"), rec.get("content_hash"),
                     rec.get("captured_at"), dt.datetime.utcnow(), PARSER_VERSION, n_sub, n_tr, n_h,
                     unknown, "ok", None])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("  %s: %d filings, %d transactions (%d unknown codes), %d holdings, %.0fs"
          % (quarter, n_sub, n_tr, unknown, n_h, time.time() - t0))


def record_failure(con, quarter, rec, err):
    con.execute("DELETE FROM sec_ins_quarters WHERE quarter = ?", [quarter])
    con.execute("INSERT INTO sec_ins_quarters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [quarter, rec.get("key"), rec.get("sha256"), rec.get("content_hash"),
                 rec.get("captured_at"), dt.datetime.utcnow(), PARSER_VERSION, 0, 0, 0, 0,
                 "failed", err])


def choose(avail, loaded, last, explicit, everything):
    order = sorted(avail, key=sec_shelf.quarter_key, reverse=True)
    if explicit:
        return [q for q in order if q in explicit]
    changed = [q for q in order if q in loaded and (
        loaded[q][0] != avail[q].get("sha256") or loaded[q][1] != PARSER_VERSION
        or loaded[q][2] == "failed")]
    fresh = [q for q in order if q not in loaded]
    if everything:
        return changed + fresh
    newest = [q for q in order[:last] if q not in loaded]
    out, seen = [], set()
    for q in changed + newest:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--root", help="local shelf root (default US_ARCHIVE_ROOT or the laptop copy)")
    ap.add_argument("--last", type=int, default=DEFAULT_LAST)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--quarters", help="comma-separated, e.g. 2026q2,2026q3")
    args = ap.parse_args()
    src = sec_shelf.make_source(args.source, SOURCE, args.root)
    avail = {}
    for filename, rec in src.pointers().items():
        q = sec_shelf.quarter_of(filename)
        if q:
            avail[q] = rec
    if not avail:
        print("no insider quarters on the %s shelf; nothing to do" % src.name)
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    loaded = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT quarter, sha256, parser_version, status FROM sec_ins_quarters").fetchall()}
    explicit = {q.strip().lower() for q in (args.quarters or "").split(",") if q.strip()}
    targets = choose(avail, loaded, args.last, explicit, args.all)
    print("insider shelf: %d quarters (%s..%s); loaded: %d; to load: %s"
          % (len(avail), min(avail, key=sec_shelf.quarter_key), max(avail, key=sec_shelf.quarter_key),
             len(loaded), ", ".join(targets) or "nothing"))
    sys.stdout.flush()
    failed = []
    for q in targets:
        workdir = tempfile.mkdtemp(prefix="form345-%s-" % q)
        try:
            load_quarter(con, q, avail[q], src, workdir)
        except Exception as e:                                    # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, str(e)[:300])
            print("  %s FAILED: %s — previous load stays live" % (q, err))
            failed.append(q)
            try:
                record_failure(con, q, avail[q], err)
            except Exception as e2:                               # noqa: BLE001
                print("  (could not record the failure: %s)" % e2)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    con.close()
    if failed:
        print("ATTENTION insider quarters failed: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
