# -*- coding: utf-8 -*-
"""M2 — production buyback extractor (自己株券買付状況報告書, EDINET type 220).

The monthly report a company files while a share buyback runs: the authorising
resolution, what was bought in the month, the cumulative total against the
authorisation, and the filer's own progress percentage.

Parser bb-2 reads the whole lifecycle out of the one filing: the authorising
resolution AND its acquisition window (取得期間), the month's buying, and the
【株式の処理状況及び保有状況】 block — shares retired (消却), shares disposed, and
the month-end treasury holding against shares outstanding. Announcement →
execution → cancellation, all from a source that publishes its own arithmetic.

These filings are NOT individually XBRL-tagged — the numbers live in HTML
tables inside a handful of TextBlock facts (see buyback_m1/README.md for the
source verification and the three traps this parser exists to survive). The
gate is the filer's own arithmetic: recompute cumulative / authorised and
require the published 進捗状況 back.

Coverage is capped by EDINET, not by us: these filings are purged after roughly
a year, so nothing before 2025-08-12 is retrievable. Annual buyback spend for
earlier years is tagged in the annual reports instead (see buyback_annual.py).

    lsof -ti:8007 | xargs kill          # DuckDB counts the API reader as a lock
    python buyback.py --source s3 --workers 12     # whole archive; needs EDINET_S3_*
    python buyback.py --source local               # only what the laptop already has
Python 3.9.
"""
import argparse
import collections
import datetime as dt
import glob
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (S3Source, incremental_window, record_run, seek_key,
                     sec_code_of)

HERE = os.path.dirname(os.path.abspath(__file__))
_ARCHIVE = os.environ.get(
    "EDINET_ARCHIVE_ROOT",
    os.path.join(HERE, "..", "..", "equity", "data", "raw", "edinet"))
LISTS = os.path.join(_ARCHIVE, "lists")
DB_PATH = os.environ.get(
    "EQUITY_DB_PATH", os.path.join(HERE, "..", "data", "equity.duckdb"))
PARSER_VERSION = "bb-2"
DOC_TYPE = "220"
# A correction (訂正自己株券買付状況報告書, 230) carries the whole corrected
# report and names the one it corrects (parentDocID). It is stored as a filing
# of its own and linked in eq_buyback_corrections; every rollup reads only the
# newest of a report and its corrections (CURRENT below). Not "newest per
# month": the as_of a report states repeats across months for some filers
# (住友不動産, ハリマビステム), so a month is not one report.
DOC_TYPES = (DOC_TYPE, "230")
DOCS = os.path.join(_ARCHIVE, "docs")

ROW_AUTHORISED, ROW_MONTH_TOTAL = "決議状況", "計"
ROW_CUMULATIVE, ROW_PROGRESS = "累計", "進捗状況"
BLOCKS = {"board": "AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock",
          "agm": "AcquisitionsByResolutionOfShareholdersMeetingTextBlock"}
EXTRA = {"disposal": "DisposalsOfTreasurySharesTextBlock",
         "holding": "HoldingOfTreasurySharesTextBlock"}
# 消却 / 募集 / 移転 / その他, then 合計. その他 is NOT one row: filers open a
# separate その他(…) block per reason (Sony files three), so it must accumulate.
CATEGORIES = (("offer", "引き受ける者の募集"), ("cancel", "消却"),
              ("transfer", "移転"), ("other", "その他"))
# filers write this several ways: 該当事項はありません / 該当事項なし / 該当なし
NOT_APPLICABLE_RE = re.compile(r"該当(?:事項)?\s*(?:は)?\s*(?:あり)?(?:ません|なし|無し)")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace(" ", " ").replace("&#160;", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_tags(s):
    return norm(re.sub(r"<[^>]+>", " ", s))


def to_num(s):
    """Strip the unit rather than reject the cell: a rejected cell silently
    shifts the trailing-pair read onto the wrong column (see M1 traps)."""
    s = norm(s).replace(",", "")
    # A trailing bracketed qualifier — Toyota files its authorisation as
    # 4,341,277,243,820(上限) — annotates the number, it is not part of it.
    s = re.sub(r"\([^()]*\)\s*$", "", s).strip()
    s = re.sub(r"[%％株円]+$", "", s).strip()
    if not s or s in ("-", "−", "―", "ー", "—"):
        return None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def cells_of(row_html):
    return [strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def last_two_numbers(cells):
    """Daily rows are (label, date, shares, yen); summary rows (label, shares,
    yen). Never key on position — read the trailing numeric pair."""
    nums = [v for v in (to_num(c) for c in cells) if v is not None]
    if len(nums) < 2:
        return (nums[0] if nums else None), None
    return nums[-2], nums[-1]


# ~1% of filers date the resolution in the Reiwa era (令和8年 = 2026). bb-1
# matched 西暦 only and stored a null resolution_date for them — and the
# programme rollup keys on that field.
DATE_RE = re.compile(r"(?:(令和)\s*)?(\d{1,4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def scan_dates(text):
    """Filers type dates that do not exist — 2025年11月31日 is filed as the
    as-of date of a November report. There is no honest way to store one, and
    guessing month-end would invent a fact, so it is dropped from the dates and
    returned separately to be recorded against the filing."""
    good, bad = [], []
    for era, y, mo, d in DATE_RE.findall(norm(text)):
        year = 2018 + int(y) if era else int(y)
        if year < 1900:
            continue
        try:
            good.append(dt.date(year, int(mo), int(d)).isoformat())
        except ValueError:
            bad.append("%04d-%02d-%02d" % (year, int(mo), int(d)))
    return good, bad


def all_dates(text):
    return scan_dates(text)[0]


def jdate(text):
    d = all_dates(text)
    return d[0] if d else None


def rows_of(seg):
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [c for c in cells_of(row) if c]
        if cells:
            yield cells


def parse_window(seg):
    """The acquisition window lives in the 取得期間 fragment — usually inside the
    決議状況 label, but Idemitsu puts it in a row of its own, so read it from the
    block text. Never substitute a nearby date: MonotaRO files the form with the
    resolution date blank, and borrowing the as-of date would invent a fact."""
    m = re.search(r"取得期間[^)）]*", strip_tags(seg))
    win = all_dates(m.group(0)) if m else []
    if len(win) >= 2:
        return win[0], win[1]
    return None, (win[0] if win else None)


def parse_block(seg):
    out = dict(authorised_shares=None, authorised_yen=None, resolution_date=None,
               month_shares=None, month_yen=None, cumulative_shares=None,
               cumulative_yen=None, progress_shares_pct=None,
               progress_yen_pct=None, daily_rows=0,
               window_start=None, window_end=None)
    out["window_start"], out["window_end"] = parse_window(seg)
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日現在", norm(seg))
    out["as_of"] = ("%04d-%02d-%02d" % tuple(int(x) for x in m.groups())) if m else None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [c for c in cells_of(row) if c]
        if not cells:
            continue
        label, (a, b) = cells[0], last_two_numbers(cells)
        if ROW_AUTHORISED in label:
            out["authorised_shares"], out["authorised_yen"] = a, b
            out["resolution_date"] = jdate(label)
        elif ROW_PROGRESS in label:
            out["progress_shares_pct"], out["progress_yen_pct"] = a, b
        elif ROW_CUMULATIVE in label:
            out["cumulative_shares"], out["cumulative_yen"] = a, b
        elif label.startswith(ROW_MONTH_TOTAL):
            out["month_shares"], out["month_yen"] = a, b
        elif re.search(r"\d+月\s*\d+日", " ".join(cells)):
            out["daily_rows"] += 1
    return out


def parse_disposal(seg):
    """Category totals come from each category's 計 row, never the header row —
    the header carries the disposal DATE in the same cell run."""
    out = dict((k, (None, None)) for k, _ in CATEGORIES)
    out["total"] = (None, None)
    current = None
    for cells in rows_of(seg):
        label = cells[0]
        if label.startswith("合計"):
            out["total"], current = last_two_numbers(cells), None
        elif label.startswith("計"):
            if current:
                a, b = last_two_numbers(cells)
                have = out[current]
                out[current] = (add(have[0], a), add(have[1], b))
            current = None
        else:
            # Only ever SET the category here, never clear it: the disposal date
            # sits in its own row between the category label and its 計 row, and
            # clearing on that row loses the total (Sony's 消却 among them).
            current = next((k for k, kw in CATEGORIES if kw in label), current)
    return out


def add(x, y):
    return None if x is None and y is None else (x or 0) + (y or 0)


def check_disposal(d):
    """The block publishes its own 合計, so it gets the same end-to-end gate as
    the acquisition block. A ― here means no disposal occurred — the form's own
    convention, not our imputation — so it sums as zero."""
    if d is None:
        return "no_block", None
    if all(v == (None, None) for k, v in d.items() if k != "total"):
        return "no_rows", None
    if d["total"] == (None, None):
        return "unverified", "filing published no 合計 row"
    probs = []
    for i, unit in ((0, "shares"), (1, "yen")):
        calc = sum(d[k][i] or 0 for k, _ in CATEGORIES)
        if abs(calc - (d["total"][i] or 0)) > 1:
            probs.append("%s: categories sum to %.0f vs stated 合計 %.0f"
                         % (unit, calc, d["total"][i] or 0))
    return ("partial", "; ".join(probs)) if probs else ("clean", None)


def parse_holding(seg):
    out = {}
    for cells in rows_of(seg):
        v = to_num(cells[-1])
        if v is None:
            continue
        if "発行済株式総数" in cells[0]:
            out["shares_outstanding"] = v
        elif "保有自己株式数" in cells[0]:
            out["treasury_shares"] = v
    return out


TABLE_RE = re.compile(r"<table.*?</table>", re.S | re.I)


def parse_tables(seg):
    """One filing can report SEVERAL live authorisations. TOPPAN's May 2026
    filing carries two tables — a ¥30bn programme finishing at 100% and a ¥50bn
    one four days old. bb-1 read the whole block as one record, so last-row-wins
    kept only the second and silently dropped the first; the figures it
    published were internally consistent and passed the progress gate, which is
    exactly why this survived a 6,221-filing run. One record per table."""
    recs = []
    for tb in TABLE_RE.findall(seg):
        rows = list(rows_of(tb))
        if not any(ROW_AUTHORISED in c[0] or ROW_CUMULATIVE in c[0] for c in rows):
            continue
        recs.append(parse_block(tb))
    return recs or [parse_block(seg)]


def parse_filing(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    hon = [n for n in z.namelist() if "honbun" in n and n.endswith(".htm")]
    if not hon:
        raise ValueError("no honbun ixbrl document in package")
    html = z.read(hon[0]).decode("utf-8", "replace")
    out, extra = {}, {}
    seg = {}
    for key, element in dict(BLOCKS, **EXTRA).items():
        m = re.search(r'<ix:nonNumeric[^>]*name="[^"]*%s"[^>]*>(.*?)</ix:nonNumeric>'
                      % re.escape(element), html, re.S)
        seg[key] = m.group(1) if m else None
    extra["disposal"] = parse_disposal(seg["disposal"]) if seg["disposal"] else None
    extra["holding"] = parse_holding(seg["holding"]) if seg["holding"] else {}
    extra["as_of"] = next((d for k in EXTRA if seg[k]
                           for d in all_dates(re.split(r"現在", strip_tags(seg[k]))[0])), None)
    extra["bad_dates"] = sorted(set(b for k in seg if seg[k]
                                    for b in scan_dates(strip_tags(seg[k]))[1]))
    for key in BLOCKS:
        if not seg[key]:
            continue
        text = strip_tags(seg[key])
        if NOT_APPLICABLE_RE.search(text) and len(text) < 60:
            out[key] = None
            continue
        # Some filers write the section as prose with no table at all — usually
        # a longer way of saying there was nothing to report. That is an absent
        # table, not a failed parse, and must not be graded as an extraction
        # defect: mis-classifying it hides real defects in the same bucket.
        if not re.search(r"<table", seg[key], re.I):
            out[key] = None
            continue
        out[key] = parse_tables(seg[key])
    return out, extra


def stated_dp(x):
    s = ("%.10f" % x).rstrip("0")
    return len(s.split(".")[1]) if "." in s and s.split(".")[1] else 0


def check(rec):
    """Filers truncate OR round, inconsistently, so the tolerance is one unit
    in the last decimal place the filer actually printed."""
    if rec is None:
        return "no_table", "filing states 該当事項なし"
    probs = []
    for unit, cum, auth, stated in (
            ("shares", rec["cumulative_shares"], rec["authorised_shares"], rec["progress_shares_pct"]),
            ("yen", rec["cumulative_yen"], rec["authorised_yen"], rec["progress_yen_pct"])):
        if stated is None or not auth or cum is None:
            continue
        calc = 100.0 * cum / auth
        if abs(calc - stated) >= 10.0 ** -stated_dp(stated):
            probs.append("%s: recomputed %.4f%% vs stated %s" % (unit, calc, stated))
    if probs:
        return "partial", "; ".join(probs)
    if rec["authorised_shares"] is None and rec["cumulative_shares"] is None:
        return "partial", "no authorisation or cumulative row found"
    # 'clean' must mean the gate RAN and passed. Roughly one filing in fifteen
    # omits 進捗状況 entirely, leaving nothing to reconcile against; calling
    # that clean would claim a verification that never happened.
    if rec["progress_shares_pct"] is None and rec["progress_yen_pct"] is None:
        return "unverified", "filer published no 進捗状況; figures extracted but not reconciled"
    return "clean", None


def targets_from_s3(src, since, have):
    """Buyback filings in the bucket, narrowed to the incremental window.

    The daily lists are the authority on document type, but they are the
    expensive thing to fetch, so discovery runs off the object listing first
    and only the surviving days' lists are read.
    """
    filings = {}
    for key in src._keys("docs/", seek_key(since)):
        parts = key.split("/")                      # docs/YYYY-MM-DD/DOCID_t1.zip
        if len(parts) != 3 or not parts[2].endswith("_t1.zip"):
            continue
        filings[parts[2][:-len("_t1.zip")]] = parts[1]
    through = max(filings.values()) if filings else None
    pending = (filings if since is None else
               {d: day for d, day in filings.items()
                if day >= since and d not in have})
    if since is not None:
        print("incremental: %d of %d archived documents are new since %s"
              % (len(pending), len(filings), since))
    meta = src.list_metadata(days=None if since is None else set(pending.values()))
    out = {}
    for doc_id, day in pending.items():
        r = meta.get(doc_id) or {}
        if r.get("docTypeCode") in DOC_TYPES and not r.get("fundCode"):
            out[doc_id] = (day, r)
    return out, through, len(filings)


def targets_from_lists():
    out = {}
    for f in sorted(glob.glob(os.path.join(LISTS, "*.json"))):
        day = os.path.basename(f)[:10]
        try:
            rows = json.load(open(f)).get("results") or []
        except ValueError:
            continue
        for r in rows:
            if r.get("docTypeCode") in DOC_TYPES and not r.get("fundCode"):
                out[r["docID"]] = (day, r)
    return out


def s3_client(workers):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
                        aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
                        aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
                        region_name=os.environ.get("EDINET_S3_REGION", "auto"),
                        config=Config(max_pool_connections=workers + 4,
                                      retries={"max_attempts": 5, "mode": "standard"}))


DDL = """
CREATE TABLE IF NOT EXISTS eq_buyback_filings (
    doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
    filer_name VARCHAR, submitted DATE, as_of DATE, sha256 VARCHAR,
    parser_version VARCHAR, status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS eq_buyback_programs (
    doc_id VARCHAR, resolution_type VARCHAR, resolution_date DATE,
    window_start DATE, window_end DATE,
    authorised_shares BIGINT, authorised_yen BIGINT,
    month_shares BIGINT, month_yen BIGINT,
    cumulative_shares BIGINT, cumulative_yen BIGINT,
    progress_shares_pct DOUBLE, progress_yen_pct DOUBLE,
    daily_rows INTEGER, status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS eq_buyback_treasury (
    doc_id VARCHAR PRIMARY KEY, as_of DATE,
    cancelled_shares BIGINT, cancelled_yen BIGINT,
    offer_shares BIGINT, offer_yen BIGINT,
    transfer_shares BIGINT, transfer_yen BIGINT,
    other_shares BIGINT, other_yen BIGINT,
    total_shares BIGINT, total_yen BIGINT,
    shares_outstanding BIGINT, treasury_shares BIGINT,
    status VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS eq_buyback_corrections (
    doc_id VARCHAR PRIMARY KEY, corrects_doc_id VARCHAR);
"""

# The filing aliased f is the newest of its report and that report's
# corrections. app/buyback_api.py builds the same test, with a date ceiling.
CURRENT = """NOT EXISTS (
    SELECT 1 FROM eq_buyback_corrections c JOIN eq_buyback_filings n ON n.doc_id = c.doc_id
    WHERE c.corrects_doc_id = coalesce((SELECT c2.corrects_doc_id FROM eq_buyback_corrections c2
                                        WHERE c2.doc_id = f.doc_id), f.doc_id)
      AND n.doc_id <> f.doc_id AND n.status <> 'failed'
      AND (n.submitted > f.submitted OR (n.submitted = f.submitted AND n.doc_id > f.doc_id)))"""

# One row per authorisation, from its latest filing. A view, not a table: the
# rollup is derived, so it can never go stale against the filings behind it.
VIEWS = """
CREATE OR REPLACE VIEW eq_buyback_lifecycle AS
WITH ranked AS (
    SELECT f.edinet_code, f.sec_code, f.filer_name, f.as_of, f.doc_id,
           p.resolution_type, p.resolution_date, p.window_start, p.window_end,
           p.authorised_shares, p.authorised_yen,
           p.cumulative_shares, p.cumulative_yen, p.status,
           row_number() OVER (PARTITION BY f.edinet_code, p.resolution_type,
                                           p.resolution_date
                              ORDER BY f.as_of DESC, f.submitted DESC) AS rn,
           count(*) OVER (PARTITION BY f.edinet_code, p.resolution_type,
                                       p.resolution_date) AS filings
    FROM eq_buyback_programs p JOIN eq_buyback_filings f USING (doc_id)
    WHERE """ + CURRENT + """)
SELECT edinet_code, sec_code, filer_name, resolution_type, resolution_date,
       window_start, window_end, authorised_shares, authorised_yen,
       cumulative_shares, cumulative_yen, filings,
       as_of AS last_as_of, doc_id AS last_doc_id, status AS last_status,
       -- Unknown is not zero: a filing that states no cumulative has not been
       -- shown to have spent nothing, and calling the whole authorisation
       -- unspent would invent the most eye-catching number on the page.
       CASE WHEN authorised_yen IS NOT NULL AND cumulative_yen IS NOT NULL
            THEN authorised_yen - cumulative_yen END AS unspent_yen,
       -- A resolution cannot post-date the acquisition window it authorises.
       -- Where the filing's own dates say it does, the row is published as
       -- filed and flagged — filers mistype the year (Sony filed 2026年11月11日
       -- for a 2025 resolution), which also splits one programme in two here.
       resolution_date > window_start AS dates_inconsistent,
       CASE WHEN authorised_yen > 0 AND cumulative_yen IS NOT NULL
            THEN 100.0 * cumulative_yen / authorised_yen END AS completion_pct,
       CASE WHEN authorised_yen IS NULL OR authorised_yen = 0
                 OR cumulative_yen IS NULL THEN 'unknown'
            WHEN 100.0 * cumulative_yen / authorised_yen >= 99.5 THEN 'completed'
            WHEN window_end IS NOT NULL AND window_end < current_date
                 -- the monthly report covering the final month may simply not
                 -- be filed (or captured) yet; that is not an unspent programme
                 THEN CASE WHEN as_of >= window_end THEN 'expired_unspent'
                           ELSE 'awaiting_final' END
            ELSE 'running' END AS lifecycle
FROM ranked WHERE rn = 1;

CREATE OR REPLACE VIEW eq_buyback_cancellations AS
SELECT f.edinet_code, f.sec_code, f.filer_name, t.as_of, t.doc_id,
       t.cancelled_shares, t.cancelled_yen,
       t.shares_outstanding, t.treasury_shares, t.status
FROM eq_buyback_treasury t JOIN eq_buyback_filings f USING (doc_id)
WHERE t.cancelled_shares > 0 AND """ + CURRENT + """;
"""


def migrate(con):
    """bb-1 shipped eq_buyback_programs without the acquisition window."""
    have = set(r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'eq_buyback_programs'").fetchall())
    for col in ("window_start", "window_end"):
        if col not in have:
            con.execute("ALTER TABLE eq_buyback_programs ADD COLUMN %s DATE" % col)


def big(v):
    return None if v is None else int(round(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--docs", help="comma-separated docIDs, as the other readers take")
    ap.add_argument("--source", choices=("s3", "local"), default="s3")
    ap.add_argument("--new-only", action="store_true",
                    help="extract only filings archived since the last "
                         "recorded run (plus a lookback); what the nightly "
                         "refresh uses. A DB with no recorded run is built "
                         "in full, so this is always safe to pass.")
    args = ap.parse_args()

    local = args.source == "local"
    s3 = bucket = None
    since, have = (incremental_window(DB_PATH, "buybacks", "eq_buyback_filings")
                   if args.new_only else (None, set()))
    if local:
        s3src = None
        found = targets_from_lists()
        through = max((day for day, _ in found.values()), default=None)
        seen = len(found)
        targets = sorted(found.items())
        skipped = len(targets)
        targets = [t for t in targets
                   if os.path.exists(os.path.join(DOCS, t[1][0], "%s_t1.zip" % t[0]))
                   and (since is None or (t[1][0] >= since and t[0] not in have))]
        print("local archive holds %d of %d known buyback filings"
              % (len(targets), skipped))
    else:
        s3, bucket = s3_client(args.workers), os.environ["EDINET_S3_BUCKET"]
        s3src = S3Source(args.workers)
        found, through, seen = targets_from_s3(s3src, since, have)
        targets = sorted(found.items())
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.limit:
        targets = targets[:args.limit]
    print("buyback filings to extract: %d" % len(targets))

    con = duckdb.connect(os.path.abspath(DB_PATH))
    con.execute(DDL)
    migrate(con)

    def work(item):
        doc_id, (day, meta) = item
        try:
            if local:
                blob = open(os.path.join(DOCS, day, "%s_t1.zip" % doc_id), "rb").read()
            else:
                blob = s3.get_object(Bucket=bucket,
                                     Key="docs/%s/%s_t1.zip" % (day, doc_id))["Body"].read()
            return item, parse_filing(blob), hashlib.sha256(blob).hexdigest(), None
        except Exception as e:
            return item, None, None, "%s: %s" % (type(e).__name__, str(e)[:150])

    stats, done = collections.Counter(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(work, t) for t in targets]):
            (doc_id, (day, meta)), result, sha, err = fut.result()
            parsed, extra = result if result else (None, None)
            done += 1
            if done % 500 == 0:
                print("  %d/%d" % (done, len(targets)))
                sys.stdout.flush()
            base = (doc_id, meta.get("edinetCode"), sec_code_of(meta),
                    meta.get("filerName"), day)
            if meta.get("docTypeCode") == "230" and meta.get("parentDocID"):
                con.execute("INSERT OR REPLACE INTO eq_buyback_corrections VALUES (?, ?)",
                            [doc_id, meta["parentDocID"]])
            if err:
                con.execute("INSERT OR REPLACE INTO eq_buyback_filings VALUES (?,?,?,?,?,?,?,?,?,?)",
                            base + (None, sha, PARSER_VERSION, "failed", err))
                stats["failed"] += 1
                continue
            as_of = next((r["as_of"] for recs in parsed.values() if recs
                          for r in recs if r.get("as_of")), extra.get("as_of"))
            worst, detail = "no_table", None
            con.execute("DELETE FROM eq_buyback_programs WHERE doc_id = ?", [doc_id])

            # ---- cancellation and treasury ------------------------------
            disp, hold = extra["disposal"], extra["holding"]
            dstatus, ddetail = check_disposal(disp)
            stats["disposal:" + dstatus] += 1
            cat = dict((k, disp[k] if disp else (None, None))
                       for k in ("cancel", "offer", "transfer", "other", "total"))
            if cat["cancel"][0]:
                stats["cancellations"] += 1
            out_sh, tre_sh = hold.get("shares_outstanding"), hold.get("treasury_shares")
            if out_sh is None or tre_sh is None:
                stats["holding:incomplete"] += 1
            elif tre_sh > out_sh:
                # the filer's own two numbers cannot both be right; publish as
                # filed and mark it, never silently swap them
                stats["holding:impossible"] += 1
                ddetail = ddetail or "treasury shares exceed shares outstanding as filed"
            else:
                stats["holding:clean"] += 1
            con.execute("INSERT OR REPLACE INTO eq_buyback_treasury "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, as_of,
                         big(cat["cancel"][0]), big(cat["cancel"][1]),
                         big(cat["offer"][0]), big(cat["offer"][1]),
                         big(cat["transfer"][0]), big(cat["transfer"][1]),
                         big(cat["other"][0]), big(cat["other"][1]),
                         big(cat["total"][0]), big(cat["total"][1]),
                         big(out_sh), big(tre_sh), dstatus, ddetail))
            for kind, recs in sorted(parsed.items()):
                if recs is None:
                    stats["%s:no_table" % kind] += 1
                    continue
                if len(recs) > 1:
                    stats["multi_resolution_filings"] += 1
                for rec in recs:
                    status, d = check(rec)
                    stats["%s:%s" % (kind, status)] += 1
                    rank = {"clean": 0, "unverified": 1, "partial": 2}
                    worst = max([status, worst if worst != "no_table" else "clean"],
                                key=lambda s: rank.get(s, 0))
                    detail = detail or d
                    con.execute(
                        "INSERT INTO eq_buyback_programs "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, kind, rec["resolution_date"],
                         rec["window_start"], rec["window_end"],
                         big(rec["authorised_shares"]), big(rec["authorised_yen"]),
                         big(rec["month_shares"]), big(rec["month_yen"]),
                         big(rec["cumulative_shares"]), big(rec["cumulative_yen"]),
                         rec["progress_shares_pct"], rec["progress_yen_pct"],
                         rec["daily_rows"], status, d))
            if extra["bad_dates"]:
                stats["impossible_dates"] += 1
                note = "filer wrote impossible date(s): " + ", ".join(extra["bad_dates"])
                detail = (detail + "; " + note) if detail else note
            con.execute("INSERT OR REPLACE INTO eq_buyback_filings VALUES (?,?,?,?,?,?,?,?,?,?)",
                        base + (as_of, sha, PARSER_VERSION, worst, detail))
            stats["filing:" + worst] += 1
    con.execute(VIEWS)
    lifecycle = con.execute(
        "SELECT lifecycle, count(*) FROM eq_buyback_lifecycle GROUP BY 1 ORDER BY 1").fetchall()
    retired = con.execute(
        "SELECT count(*), sum(cancelled_shares), sum(cancelled_yen) "
        "FROM eq_buyback_cancellations").fetchone()
    con.close()
    record_run(DB_PATH, "buybacks", through, seen, PARSER_VERSION)
    print("\n--- buyback extraction (%s) ---" % PARSER_VERSION)
    for k in sorted(stats):
        print("  %-24s %d" % (k, stats[k]))
    passed = stats["board:clean"] + stats["agm:clean"]
    checked = passed + stats["board:partial"] + stats["agm:partial"]
    if checked:
        print("  gate pass (of rows the gate could check): %d/%d = %.1f%%"
              % (passed, checked, 100.0 * passed / checked))
    print("  unverified (filer published no 進捗状況): %d"
          % (stats["board:unverified"] + stats["agm:unverified"]))
    dpassed, dchecked = stats["disposal:clean"], stats["disposal:clean"] + stats["disposal:partial"]
    if dchecked:
        print("  disposal gate (categories recompute the filer's 合計): %d/%d = %.1f%%"
              % (dpassed, dchecked, 100.0 * dpassed / dchecked))
    print("\n--- programmes (eq_buyback_lifecycle) ---")
    for name, n in lifecycle:
        print("  %-24s %d" % (name, n))
    print("  cancellations: %d filing-months, %s shares, %s yen"
          % (retired[0], "{:,}".format(retired[1] or 0), "{:,}".format(retired[2] or 0)))


if __name__ == "__main__":
    main()
