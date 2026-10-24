# -*- coding: utf-8 -*-
"""US insider trades the day they are filed: Forms 3, 4 and 5 from our own EDGAR archive.

The SEC's Insider Transactions Data Sets (equity/sec_insiders_extract.py) come
out a few weeks after each quarter, so between sets the newest insider trade on
the site was up to four months old while a Form 4 is due within two business
days of the trade. The EDGAR capture (equity/edgar_capture.py, Forms 3/4/5
added 2026-10-24) banks every one of those filings the day after it is filed;
this loader reads them from the archive, never from the SEC.

Each filing's own XML (the ownershipDocument inside the complete submission
file) is read into the same tables the quarterly sets fill, with
quarter = 'daily', so every page, API route and question that reads insider
trades sees them without a change. Values are as filed: a missing price stays
NULL, a code is the filer's code. When the SEC's quarterly set covering a
filing is loaded, sec_insiders_extract.py replaces the filing's rows with the
SEC's extraction (it deletes by accession), so 'daily' rows only ever cover the
days since the newest set.

Before parsing, each stored file is checked against the SHA-256 the capture
recorded; a file that does not match is not read. A filing already in the
tables is never read again. Each run is recorded in sec_ins_daily_runs, which
the health row reads.

Checked 2026-10-24 against the SEC's own extraction of the same filings from
the 2026q3 set (see tests/test_sec_insiders_daily.py and --compare).

Run from observatory/ against a copy of the served file:
    ./.venv/bin/python equity/sec_insiders_daily.py --db data/sec.work.duckdb
    ./.venv/bin/python equity/sec_insiders_daily.py --db … --since 2026-10-01
    ./.venv/bin/python equity/sec_insiders_daily.py --compare FILE.txt …   # parse and print, no database

Python 3.9; stdlib + duckdb (+ boto3 against the bucket).
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
PARSER_VERSION = "secins-daily-1"
QUARTER = "daily"
FORMS = {"3", "3/A", "4", "4/A", "5", "5/A"}
S3_PREFIX = "edgar"
MAX_DAYS = 120                       # a quarter's set is out within this; older days need no daily read

RUNS_SQL = """
CREATE TABLE IF NOT EXISTS sec_ins_daily_runs (
    run_at TIMESTAMP, days_from DATE, days_to DATE, read_through DATE, seen INTEGER,
    loaded INTEGER, transactions INTEGER, failed INTEGER, parser_version VARCHAR,
    status VARCHAR, detail VARCHAR);
"""


class FilingError(Exception):
    pass


# ---- parse ----------------------------------------------------------------------
_XML = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S | re.I)
_HEADER_VALUE = re.compile(r"^\s*([A-Z][A-Z \-]+):\s*(.*?)\s*$")


def _txt(node, path):
    """The text at path (a <value> child counts as the field's text), or None."""
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    t = (v.text if v is not None else el.text) or ""
    t = t.strip()
    return t or None


def _num(node, path):
    t = _txt(node, path)
    if t is None:
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _date(node, path):
    t = _txt(node, path)
    if not t:
        return None
    try:
        return dt.date.fromisoformat(t[:10])
    except ValueError:
        return None


def _bool(node, path):
    t = (_txt(node, path) or "").lower()
    if t in ("1", "true"):
        return True
    if t in ("0", "false"):
        return False
    return None


def _fn(node, path):
    """The footnote ids on one field, as the data sets write them: 'F2, F1'."""
    el = node.find(path) if node is not None else None
    if el is None:
        return None
    ids = [f.get("id") for f in el.iter("footnoteId") if f.get("id")]
    return ", ".join(ids) or None


def _fns(node, *paths):
    return " ".join(f for f in (_fn(node, p) for p in paths) if f) or None


def header(text):
    """(accession, filed date, {owner cik: SEC file number}) from the SEC header."""
    acc = filed = None
    numbers, owner_cik, in_owner = {}, None, False
    for line in text.split("\n"):
        if line.startswith("</SEC-HEADER>"):
            break
        # a section ("REPORTING-OWNER:", "ISSUER:") starts at the margin and holds
        # no value; its parts ("OWNER DATA:", "FILING VALUES:") are indented
        if line[:1].strip() and line.rstrip().endswith(":"):
            in_owner = line.startswith("REPORTING-OWNER")
            owner_cik = None
            continue
        m = _HEADER_VALUE.match(line)
        if not m:
            continue
        k, v = m.group(1).strip(), m.group(2)
        if k == "ACCESSION NUMBER":
            acc = v
        elif k == "FILED AS OF DATE" and len(v) == 8:
            filed = dt.date(int(v[:4]), int(v[4:6]), int(v[6:]))
        elif in_owner and k == "CENTRAL INDEX KEY":
            owner_cik = int(v)
        elif in_owner and k == "SEC FILE NUMBER" and owner_cik is not None:
            numbers.setdefault(owner_cik, v)
    return acc, filed, numbers


def parse(blob):
    """One complete submission file -> rows for the five tables, as dicts.

    Raises FilingError for a file with no ownership XML (a paper filing's
    cover, a filing withdrawn), which is recorded and not retried."""
    text = blob.decode("utf-8", "replace")
    acc, filed, file_numbers = header(text)
    m = _XML.search(blob)
    if not acc or not m:
        raise FilingError("no ownership XML" if acc else "no SEC header")
    try:
        doc = ET.fromstring(m.group(1))
    except ET.ParseError as e:
        raise FilingError("XML does not parse: %s" % e)
    if doc.tag != "ownershipDocument":
        raise FilingError("not an ownership document (%s)" % doc.tag)

    issuer = doc.find("issuer")
    dtype = _txt(doc, "documentType")
    filing = {
        "accession": acc, "quarter": QUARTER, "filed": filed, "period": _date(doc, "periodOfReport"),
        "orig_filed": _date(doc, "dateOfOriginalSubmission"), "document_type": dtype,
        "amendment": bool(dtype and dtype.endswith("/A")),
        "issuer_cik": int(_txt(issuer, "issuerCik")) if _txt(issuer, "issuerCik") else None,
        "issuer_name": _txt(issuer, "issuerName"),
        "issuer_symbol": (_txt(issuer, "issuerTradingSymbol") or "").upper() or None,
        "no_securities": _bool(doc, "noSecuritiesOwned"),
        "not_subject_16": _bool(doc, "notSubjectToSection16"),
        "plan_10b5_1": _bool(doc, "aff10b5One"),
        "remarks": _txt(doc, "remarks"),
    }

    owners = []
    for o in doc.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        flags = [(name, _bool(rel, tag)) for name, tag in (
            ("Director", "isDirector"), ("Officer", "isOfficer"),
            ("TenPercentOwner", "isTenPercentOwner"), ("Other", "isOther"))]
        cik = _txt(o, "reportingOwnerId/rptOwnerCik")
        cik = int(cik) if cik and cik.isdigit() else None
        owners.append({
            "accession": acc, "quarter": QUARTER, "owner_cik": cik,
            "owner_name": _txt(o, "reportingOwnerId/rptOwnerName"),
            "relationship": ",".join(n for n, f in flags if f) or None,
            "is_director": bool(flags[0][1]), "is_officer": bool(flags[1][1]),
            "is_ten_percent": bool(flags[2][1]), "is_other": bool(flags[3][1]),
            "title": _txt(rel, "officerTitle"), "other_text": _txt(rel, "otherText"),
            "state": _txt(o, "reportingOwnerAddress/rptOwnerState"),
            "file_number": file_numbers.get(cik),
        })

    trans, holds = [], []
    for derivative, table, t_tag, h_tag in ((False, "nonDerivativeTable", "nonDerivativeTransaction", "nonDerivativeHolding"),
                                            (True, "derivativeTable", "derivativeTransaction", "derivativeHolding")):
        tb = doc.find(table)
        if tb is None:
            continue
        for t in tb.findall(t_tag):
            trans.append({
                "accession": acc, "quarter": QUARTER, "sk": len(trans) + 1, "derivative": derivative,
                "security_title": _txt(t, "securityTitle"),
                "trans_date": _date(t, "transactionDate"), "deemed_date": _date(t, "deemedExecutionDate"),
                "form_type": _txt(t, "transactionCoding/transactionFormType"),
                "trans_code": (_txt(t, "transactionCoding/transactionCode") or "").upper() or None,
                "acquired_disposed": (_txt(t, "transactionAmounts/transactionAcquiredDisposedCode") or "").upper() or None,
                "shares": _num(t, "transactionAmounts/transactionShares"),
                "price": _num(t, "transactionAmounts/transactionPricePerShare"),
                "total_value": _num(t, "transactionAmounts/transactionTotalValue") if derivative else None,
                "shares_after": _num(t, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
                "value_after": _num(t, "postTransactionAmounts/valueOwnedFollowingTransaction"),
                "direct_indirect": (_txt(t, "ownershipNature/directOrIndirectOwnership") or "").upper() or None,
                "nature": _txt(t, "ownershipNature/natureOfOwnership"),
                "timeliness": _txt(t, "transactionTimeliness"),
                "equity_swap": _bool(t, "transactionCoding/equitySwapInvolved"),
                "exercise_price": _num(t, "conversionOrExercisePrice") if derivative else None,
                "exercise_date": _date(t, "exerciseDate") if derivative else None,
                "expiration_date": _date(t, "expirationDate") if derivative else None,
                "underlying_title": _txt(t, "underlyingSecurity/underlyingSecurityTitle") if derivative else None,
                "underlying_shares": _num(t, "underlyingSecurity/underlyingSecurityShares") if derivative else None,
                "footnotes": _fns(t, "transactionAmounts/transactionShares",
                                  "transactionAmounts/transactionPricePerShare",
                                  "transactionDate", "securityTitle"),
            })
        for h in tb.findall(h_tag):
            holds.append({
                "accession": acc, "quarter": QUARTER, "sk": len(holds) + 1, "derivative": derivative,
                "security_title": _txt(h, "securityTitle"),
                "form_type": _txt(h, "transactionCoding/transactionFormType"),
                "shares": _num(h, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
                "value": _num(h, "postTransactionAmounts/valueOwnedFollowingTransaction"),
                "direct_indirect": (_txt(h, "ownershipNature/directOrIndirectOwnership") or "").upper() or None,
                "nature": _txt(h, "ownershipNature/natureOfOwnership"),
                "exercise_price": _num(h, "conversionOrExercisePrice") if derivative else None,
                "exercise_date": _date(h, "exerciseDate") if derivative else None,
                "expiration_date": _date(h, "expirationDate") if derivative else None,
                "underlying_title": _txt(h, "underlyingSecurity/underlyingSecurityTitle") if derivative else None,
                "underlying_shares": _num(h, "underlyingSecurity/underlyingSecurityShares") if derivative else None,
                "footnotes": _fns(h, "postTransactionAmounts/sharesOwnedFollowingTransaction", "securityTitle"),
            })

    notes = [{"accession": acc, "quarter": QUARTER, "footnote_id": f.get("id"),
              "text": " ".join("".join(f.itertext()).split()) or None}
             for f in doc.findall("footnotes/footnote")]
    return {"filing": filing, "owners": owners, "transactions": trans, "holdings": holds, "footnotes": notes}


# ---- write ----------------------------------------------------------------------
_COLUMNS = {
    "sec_ins_filings": ["accession", "quarter", "filed", "period", "orig_filed", "document_type", "amendment",
                        "issuer_cik", "issuer_name", "issuer_symbol", "no_securities", "not_subject_16",
                        "plan_10b5_1", "remarks"],
    "sec_ins_owners": ["accession", "quarter", "owner_cik", "owner_name", "relationship", "is_director",
                       "is_officer", "is_ten_percent", "is_other", "title", "other_text", "state", "file_number"],
    "sec_ins_transactions": ["accession", "quarter", "sk", "derivative", "security_title", "trans_date",
                             "deemed_date", "form_type", "trans_code", "acquired_disposed", "shares", "price",
                             "total_value", "shares_after", "value_after", "direct_indirect", "nature",
                             "timeliness", "equity_swap", "exercise_price", "exercise_date", "expiration_date",
                             "underlying_title", "underlying_shares", "footnotes"],
    "sec_ins_holdings": ["accession", "quarter", "sk", "derivative", "security_title", "form_type", "shares",
                         "value", "direct_indirect", "nature", "exercise_price", "exercise_date",
                         "expiration_date", "underlying_title", "underlying_shares", "footnotes"],
    "sec_ins_footnotes": ["accession", "quarter", "footnote_id", "text"],
}
_PARTS = {"sec_ins_filings": "filing", "sec_ins_owners": "owners", "sec_ins_transactions": "transactions",
          "sec_ins_holdings": "holdings", "sec_ins_footnotes": "footnotes"}


def write(con, rows):
    """One filing's rows, all or nothing. The column lists name every column,
    so a later column added to a table by the quarterly loader cannot shift these."""
    con.execute("BEGIN TRANSACTION")
    try:
        for table, cols in _COLUMNS.items():
            part = rows[_PARTS[table]]
            for r in ([part] if isinstance(part, dict) else part):
                con.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))),
                            [r.get(c) for c in cols])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


# ---- the archive ----------------------------------------------------------------
class Archive(object):
    """The EDGAR shelf in the bucket: meta/{day}/ records, docs/{day}/ files."""

    def __init__(self):
        import boto3
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.c = boto3.client("s3", endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
                              aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
                              aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
                              region_name=os.environ.get("EDINET_S3_REGION", "auto"))

    def records(self, day, forms_wanted=FORMS):
        """The day's filings of these forms that the capture banked: the forms come
        from the day's SEC index (one object), what was banked from a listing of
        the day's records, so no filing is opened to learn its form."""
        try:
            idx = self.c.get_object(Bucket=self.bucket, Key="%s/lists/%s.idx" % (S3_PREFIX, day))["Body"].read()
        except self.c.exceptions.NoSuchKey:
            return []                                        # a weekend, a holiday, or not captured yet
        forms = {}
        for line in idx.decode("latin-1").splitlines():
            parts = line.split("|")
            if len(parts) == 5 and parts[2] in forms_wanted and parts[4].endswith(".txt"):
                forms[parts[4].rsplit("/", 1)[-1][:-4]] = parts[2]
        banked, kw = set(), {"Bucket": self.bucket, "Prefix": "%s/meta/%s/" % (S3_PREFIX, day)}
        while True:
            r = self.c.list_objects_v2(**kw)
            banked.update(o["Key"].rsplit("/", 1)[-1][:-5] for o in r.get("Contents", []))
            if not r.get("IsTruncated"):
                break
            kw["ContinuationToken"] = r["NextContinuationToken"]
        return [{"date": day, "accession": a, "form": f} for a, f in sorted(forms.items()) if a in banked]

    def filing(self, rec):
        meta = json.loads(self.c.get_object(
            Bucket=self.bucket, Key="%s/meta/%s/%s.json" % (S3_PREFIX, rec["date"], rec["accession"]))["Body"].read())
        if meta.get("status") == "fail":
            raise FilingError("the capture recorded a failed fetch")
        key = "%s/docs/%s/%s.txt.gz" % (S3_PREFIX, rec["date"], rec["accession"])
        blob = gzip.decompress(self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        if meta.get("sha256") and hashlib.sha256(blob).hexdigest() != meta["sha256"]:
            raise FilingError("stored file does not match the SHA-256 recorded at capture")
        return blob


def _days(con, since, today):
    """From the day after the newest quarterly set (or --since) through today."""
    if since:
        start = since
    else:
        start = today - dt.timedelta(days=MAX_DAYS)
        try:
            q = con.execute("SELECT max(quarter) FROM sec_ins_quarters WHERE status = 'ok'").fetchone()[0]
        except duckdb.Error:
            q = None
        if q:
            y, n = int(q[:4]), int(q[5])
            start = max(start, dt.date(y + (n == 4), 1 if n == 4 else n * 3 + 1, 1))
    return [start + dt.timedelta(days=i) for i in range((today - start).days + 1)]


def run(con, archive, days, log=print):
    con.execute(RUNS_SQL)
    have = {r[0] for r in con.execute("SELECT accession FROM sec_ins_filings").fetchall()}
    seen = loaded = n_tr = failed = 0
    through = None
    t0 = time.time()
    errors = []
    for day in days:
        recs = archive.records(day.isoformat())
        for rec in recs:
            seen += 1
            if rec["accession"] in have:
                continue
            try:
                rows = parse(archive.filing(rec))
                write(con, rows)
                have.add(rec["accession"])
                loaded += 1
                n_tr += len(rows["transactions"])
            except FilingError as e:
                failed += 1
                errors.append("%s: %s" % (rec["accession"], e))
        if recs:
            through = day
        log("  %s: %d Form 3/4/5 filings archived" % (day, len(recs)))
    status = "ok" if not failed or loaded else "failed"
    con.execute("INSERT INTO sec_ins_daily_runs VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [days[0] if days else None, days[-1] if days else None, through, seen, loaded, n_tr,
                 failed, PARSER_VERSION, status, "; ".join(errors[:5]) or None])
    log("insider filings: %d archived, %d loaded (%d trades), %d not readable, through %s, %.0fs"
        % (seen, loaded, n_tr, failed, through, time.time() - t0))
    return status


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=DB_PATH, help="a working copy of sec.duckdb, never the served file")
    ap.add_argument("--since", help="first filing day to read (YYYY-MM-DD); default the day after the newest quarterly set")
    ap.add_argument("--compare", nargs="+", metavar="FILE", help="parse submission files and print the rows")
    args = ap.parse_args(argv)
    if args.compare:
        for p in args.compare:
            with open(p, "rb") as f:
                blob = f.read()
            print(json.dumps(parse(gzip.decompress(blob) if p.endswith(".gz") else blob), default=str, indent=1))
        return 0
    if not os.environ.get("EDINET_S3_BUCKET"):
        print("no EDINET_S3_BUCKET: the EDGAR archive is in the bucket; nothing read")
        return 1
    con = duckdb.connect(args.db)
    try:
        if not con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'sec_ins_filings'").fetchone()[0]:
            print("no insider tables in %s: load a quarterly set first (sec_insiders_extract.py)" % args.db)
            return 1
        days = _days(con, dt.date.fromisoformat(args.since) if args.since else None, dt.date.today())
        status = run(con, Archive(), days)
    finally:
        con.close()
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
