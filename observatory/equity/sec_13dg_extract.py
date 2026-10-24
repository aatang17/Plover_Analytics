# -*- coding: utf-8 -*-
"""US 5% holders: Schedules 13D and 13G, from our own EDGAR archive.

Anyone who comes to own more than 5% of a class of a US-registered company's
voting shares files a Schedule 13D (an active holder, who must say what they
intend: Item 4) or a 13G (a passive investor, an institution filing under Rule
13d-1(b), or an exempt holder), and amends it as the stake moves. It is the US
counterpart of Japan's 大量保有報告書 (stakes.html). Since 18 December 2024
both are filed as structured XML (form types SCHEDULE 13D, SCHEDULE 13G and
their /A); this loader reads those, as filed, from the EDGAR capture's archive
(equity/edgar_capture.py banks every one the day after it is filed). The
older SC 13D / SC 13G filings are free text and are not read here.

Tables (data/sec.duckdb, insert-only: an amendment is a new row, nothing is
merged or corrected; missing is NULL, never 0):

  sec_13dg_filings   one row per filing: form, filed and event dates, issuer
                     (CIK, name, CUSIP), the rule a 13G is filed under, and a
                     13D's stated purpose (Item 4) and source of funds (Item 3)
                     in the filer's words
  sec_13dg_persons   each reporting person on the cover pages: name, CIK, type
                     (IA adviser, IN individual, CO company …), voting and
                     dispositive power, shares and percent of class as filed
  sec_13dg_runs      one row per run, which the health row reads

Each file is checked against the SHA-256 the capture recorded before it is
read, and a filing already loaded is never read again.

Run from observatory/ against a copy of the served file:
    ./.venv/bin/python equity/sec_13dg_extract.py --db data/sec.work.duckdb            # since the last run
    ./.venv/bin/python equity/sec_13dg_extract.py --db … --since 2024-12-18             # the structured era, once
    ./.venv/bin/python equity/sec_13dg_extract.py --compare FILE.txt …                  # parse and print

Python 3.9; stdlib + duckdb (+ boto3 against the bucket).
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sec_insiders_daily as archive_reader                      # noqa: E402 — the archive reader is shared

DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
PARSER_VERSION = "sec13dg-1"
FORMS = {"SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
STRUCTURED_FROM = dt.date(2024, 12, 18)       # the first day 13D/G were filed as XML
LOOKBACK_DAYS = 10                            # a routine run re-reads this far back for late banking
# The first catch-up (from 18 December 2024, ~47,000 filings, ~2 hours) is taken
# in slices so a refresh cut off by the daily restart keeps what it read: each
# run reads at most this many days and the next one resumes after them.
MAX_DAYS_PER_RUN = 150

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_13dg_filings (
    accession VARCHAR PRIMARY KEY, day DATE, filed DATE, form VARCHAR, amendment BOOLEAN,
    amendment_no INTEGER, previous_accession VARCHAR, event_date DATE, issuer_cik BIGINT,
    issuer_name VARCHAR, cusip VARCHAR, cusips VARCHAR, security_title VARCHAR, filer_cik BIGINT,
    rule VARCHAR, five_percent_or_less BOOLEAN, purpose VARCHAR, funds_source VARCHAR,
    schema_version VARCHAR, parser_version VARCHAR, loaded_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS sec_13dg_persons (
    accession VARCHAR, seq INTEGER, person_cik BIGINT, name VARCHAR, person_type VARCHAR,
    citizenship VARCHAR, fund_type VARCHAR, sole_vote DOUBLE, shared_vote DOUBLE,
    sole_dispose DOUBLE, shared_dispose DOUBLE, shares DOUBLE, percent DOUBLE,
    excludes_certain_shares BOOLEAN, comments VARCHAR);
CREATE TABLE IF NOT EXISTS sec_13dg_runs (
    run_at TIMESTAMP, days_from DATE, days_to DATE, read_through DATE, seen INTEGER,
    loaded INTEGER, failed INTEGER, parser_version VARCHAR, status VARCHAR, detail VARCHAR);
CREATE INDEX IF NOT EXISTS sec_13dg_issuer ON sec_13dg_filings (issuer_cik, filed);
CREATE INDEX IF NOT EXISTS sec_13dg_cusip ON sec_13dg_filings (cusip);
CREATE INDEX IF NOT EXISTS sec_13dg_persons_acc ON sec_13dg_persons (accession);
"""

FilingError = archive_reader.FilingError
_XML = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S | re.I)


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _t(node, path):
    if node is None:
        return None
    el = node.find(path)
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _first(node, *paths):
    for p in paths:
        v = _t(node, p)
        if v is not None:
            return v
    return None


def _n(node, *paths):
    t = _first(node, *paths)
    if t is None:
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _yn(node, *paths):
    t = (_first(node, *paths) or "").upper()
    return True if t in ("Y", "TRUE", "1") else False if t in ("N", "FALSE", "0") else None


def _mdy(t):
    """The cover page writes dates MM/DD/YYYY."""
    if not t:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(t.strip()[:10], fmt).date()
        except ValueError:
            pass
    return None


def _cik(t):
    t = (t or "").strip()
    return int(t) if t.isdigit() and int(t) else None


def parse(blob):
    """One complete submission file -> {"filing": {...}, "persons": [...]}."""
    acc, filed, _ = archive_reader.header(blob.decode("utf-8", "replace"))
    m = _XML.search(blob)
    if not acc or not m:
        raise FilingError("no schedule XML" if acc else "no SEC header")
    try:
        doc = _strip_ns(ET.fromstring(m.group(1)))
    except ET.ParseError as e:
        raise FilingError("XML does not parse: %s" % e)
    form = _t(doc, "headerData/submissionType")
    if form not in FORMS:
        raise FilingError("not a structured Schedule 13D/G (%s)" % form)
    d13 = "13D" in form
    fd = doc.find("formData")
    cover = fd.find("coverPageHeader") if fd is not None else None
    issuer = cover.find("issuerInfo") if cover is not None else None
    cusips = [c.text.strip() for c in (issuer.findall("issuerCusips/issuerCusipNumber") if issuer is not None else [])
              if c.text and c.text.strip()]
    rules = [r.text.strip() for r in (cover.findall(
        "designateRulesPursuantThisScheduleFiled/designateRulePursuantThisScheduleFiled") if cover is not None else [])
        if r.text and r.text.strip()]
    filing = {
        "accession": acc, "filed": filed, "form": form, "amendment": form.endswith("/A"),
        "amendment_no": int(_first(cover, "amendmentNo")) if (_first(cover, "amendmentNo") or "").isdigit() else None,
        "previous_accession": _first(doc, "headerData/previousAccessionNumber") or _first(cover, "previousAccessionNumber"),
        "event_date": _mdy(_first(cover, "dateOfEvent", "eventDateRequiresFilingThisStatement")),
        "issuer_cik": _cik(_first(issuer, "issuerCIK", "issuerCik")),
        "issuer_name": _first(issuer, "issuerName"),
        "cusip": cusips[0] if cusips else None, "cusips": ",".join(cusips) or None,
        "security_title": _first(cover, "securitiesClassTitle"),
        "filer_cik": _cik(_t(doc, "headerData/filerInfo/filer/filerCredentials/cik")),
        "rule": "; ".join(rules) or None,
        "five_percent_or_less": _yn(fd, "items/item5/classOwnership5PercentOrLess") if not d13 else None,
        "purpose": _t(fd, "items1To7/item4/transactionPurpose") if d13 else None,
        "funds_source": _t(fd, "items1To7/item3/fundsSource") if d13 else None,
        "schema_version": _t(doc, "schemaVersion"), "parser_version": PARSER_VERSION,
    }
    persons = []
    blocks = (fd.findall("reportingPersons/reportingPersonInfo") if d13
              else fd.findall("coverPageHeaderReportingPersonDetails")) if fd is not None else []
    for i, p in enumerate(blocks, 1):
        own = p.find("reportingPersonBeneficiallyOwnedNumberOfShares")
        src = own if own is not None else p
        persons.append({
            "accession": acc, "seq": i,
            "person_cik": _cik(_first(p, "reportingPersonCIK")),
            "name": _first(p, "reportingPersonName"),
            "person_type": _first(p, "typeOfReportingPerson"),
            "citizenship": _first(p, "citizenshipOrOrganization"),
            "fund_type": _first(p, "fundType"),
            "sole_vote": _n(src, "soleVotingPower"), "shared_vote": _n(src, "sharedVotingPower"),
            "sole_dispose": _n(src, "soleDispositivePower"), "shared_dispose": _n(src, "sharedDispositivePower"),
            "shares": _n(p, "aggregateAmountOwned", "reportingPersonBeneficiallyOwnedAggregateNumberOfShares"),
            "percent": _n(p, "percentOfClass", "classPercent"),
            "excludes_certain_shares": _yn(p, "isAggregateExcludeShares", "aggregateAmountExcludesCertainSharesFlag"),
            "comments": _first(p, "commentContent", "comments"),
        })
    if not persons:
        raise FilingError("no reporting person on the cover pages")
    return {"filing": filing, "persons": persons}


_F_COLS = ["accession", "day", "filed", "form", "amendment", "amendment_no", "previous_accession", "event_date",
           "issuer_cik", "issuer_name", "cusip", "cusips", "security_title", "filer_cik", "rule",
           "five_percent_or_less", "purpose", "funds_source", "schema_version", "parser_version", "loaded_at"]
_P_COLS = ["accession", "seq", "person_cik", "name", "person_type", "citizenship", "fund_type", "sole_vote",
           "shared_vote", "sole_dispose", "shared_dispose", "shares", "percent", "excludes_certain_shares", "comments"]


def write(con, rows, day):
    f = dict(rows["filing"], day=day, loaded_at=dt.datetime.utcnow())
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("INSERT INTO sec_13dg_filings (%s) VALUES (%s)" % (",".join(_F_COLS), ",".join("?" * len(_F_COLS))),
                    [f.get(c) for c in _F_COLS])
        for p in rows["persons"]:
            con.execute("INSERT INTO sec_13dg_persons (%s) VALUES (%s)" % (",".join(_P_COLS), ",".join("?" * len(_P_COLS))),
                        [p.get(c) for c in _P_COLS])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _days(con, since, today):
    """--since, or from LOOKBACK_DAYS before the newest day read, or from the
    first structured day on a first run."""
    if since:
        start = since
    else:
        last = con.execute("SELECT max(read_through) FROM sec_13dg_runs WHERE status = 'ok'").fetchone()[0]
        start = (last - dt.timedelta(days=LOOKBACK_DAYS)) if last else STRUCTURED_FROM
    start = max(start, STRUCTURED_FROM)
    n = min((today - start).days + 1, MAX_DAYS_PER_RUN)
    return [start + dt.timedelta(days=i) for i in range(n)]


def run(con, archive, days, log=print):
    con.execute(SCHEMA_SQL)
    have = {r[0] for r in con.execute("SELECT accession FROM sec_13dg_filings").fetchall()}
    seen = loaded = failed = 0
    through, errors, t0 = None, [], time.time()
    for day in days:
        recs = archive.records(day.isoformat(), FORMS)
        for rec in recs:
            seen += 1
            if rec["accession"] in have:
                continue
            try:
                write(con, parse(archive.filing(rec)), day)
                have.add(rec["accession"])
                loaded += 1
            except FilingError as e:
                failed += 1
                errors.append("%s: %s" % (rec["accession"], e))
        through = day                            # every day in the list was read, filings or none
        if recs or day.day == 1:
            log("  %s: %d structured 13D/G filings archived" % (day, len(recs)))
    status = "ok" if not failed or loaded else "failed"
    con.execute("INSERT INTO sec_13dg_runs VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [days[0] if days else None, days[-1] if days else None, through, seen, loaded, failed,
                 PARSER_VERSION, status, "; ".join(errors[:5]) or None])
    log("13D/G: %d archived, %d loaded, %d not readable, through %s, %.0fs"
        % (seen, loaded, failed, through, time.time() - t0))
    return status


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=DB_PATH, help="a working copy of sec.duckdb, never the served file")
    ap.add_argument("--since", help="first filing day to read (YYYY-MM-DD)")
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
        con.execute(SCHEMA_SQL)
        days = _days(con, dt.date.fromisoformat(args.since) if args.since else None, dt.date.today())
        status = run(con, archive_reader.Archive(), days)
    finally:
        con.close()
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
