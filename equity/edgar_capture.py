# -*- coding: utf-8 -*-
"""EDGAR daily capture — the US comparison archive (internal, not a product).

The product is Japan-deep; this job exists so that when a Japanese filing
needs a US counterpart to compare against — a 13D against a 大量保有報告書, a
proxy statement against a 有価証券報告書's pay tables, a 10-K's buyback
footnote against a 自己株券買付状況報告書 — the US document is already on our
shelf, in the same bucket, in the same shape. Nothing here is parsed or served.

Unlike EDINET, EDGAR never deletes: this archive is convenience and rate-limit
insurance, not the moat. It follows capture.py's discipline anyway, because a
half-kept archive is worse than none: idempotent (a per-filing manifest object
says what is already banked), fail-safe (a failed filing is logged and retried
on the next trailing-window run; a failed day never blocks the rest), verified
(the complete-submission file must start with the SEC header, never an HTML
error page), and hashed (SHA-256 of the bytes as served, before compression).

What is archived, per filing: the *complete submission text file*
(edgar/data/{cik}/{accession}.txt) — every document in the filing, exhibits and
XBRL included, concatenated by the SEC. It is the one canonical raw object per
filing, like EDINET's type=1 package. Submission files are 5–10x smaller
gzipped (a 10-K is ~10MB raw, ~1.5MB gzipped), so they are stored as .txt.gz;
the manifest records both the raw hash and the stored size.

Form types (the US counterparts of what capture.py takes from EDINET):
    10-K 10-K/A 10-KT 10-Q 10-Q/A 20-F 20-F/A 40-F 40-F/A   periodic reports (120/130/160/170)
    8-K 8-K/A 6-K 6-K/A                                    current reports (180/190); 6-K is
                                                            how Japanese ADR issuers file in the US
    DEF 14A DEFA14A DEFC14A DEFM14A PREC14A                 proxy statements: boards, pay, AGM
                                                            proposals; DEFC14A/PREC14A = contested
    SC 13D SC 13D/A SC 13G SC 13G/A (+ the "SCHEDULE 13D" spelling the index
                                     switched to in 2025)  5% family (350/360)
    SC TO-T SC TO-T/A SC TO-I SC TO-I/A SC 14D9 SC 14D9/A   tender offers (240–300)
    S-1 S-1/A S-3 S-3/A S-4 S-4/A F-1 F-1/A F-3 F-4          registration statements (030/040)
    424B1 424B3 424B4 424B5                                 prospectuses actually used (424B2 is
                                                            skipped: ~800/day of bank structured notes)
    13F-HR 13F-HR/A                                         institutional holdings — no EDINET
                                                            analogue, but the obvious comparison set
                                                            for the cross-shareholding work
Skipped by default: Forms 3/4/5 (insider transactions, ~400k/year of 10KB
files — add with --add-forms 4), everything investment-company (485*, 497*,
N-*), Form D, 144, FWP. Measured on the 2025 Q2 index: the default set is
~280k filings and ~50–70GB gzipped per year.

Usage:
    python edgar_capture.py                       # trailing 7 days through today
    python edgar_capture.py --days 30
    python edgar_capture.py --start 2021-01-01 --end 2021-12-31 --workers 6
    python edgar_capture.py --forms "10-K,10-K/A" --add-forms 4

Two index sources. The trailing window (--days) reads the SEC's *daily* index,
which is what is complete for yesterday. A backfill (--start/--end) reads the
*quarterly* full index instead: one request per quarter rather than one per
day, and it sidesteps the daily index's 1994–1998 filenames (two-digit years:
master.970102.idx). Either way filings are keyed by their filing date.

Horizon: electronic filing began in 1993 and was universal by mid-1996; the
quarterly index runs from 1993 Q1. Measured 2026-09-10/11: the core set is
~100–160k filings a year through 2000 and ~250–300k a year since 2005; mean
stored size ~24KB (2000), ~150KB (2010), ~180KB (2020). The whole 1994–2026
shelf is therefore ~7.5M filings and roughly 1TB stored; ~11 days of fetching
at the rate cap, so backfill a year at a time (~10 hours each).

Layout (local: EDGAR_ARCHIVE_ROOT, default equity/data/raw/edgar; S3: the
EDINET bucket under the edgar/ prefix — same EDINET_S3_* variables):
    lists/YYYY-MM-DD.idx                the SEC's daily master index, as served
    lists/full/YYYY-QTRn.idx            the quarterly full index (backfill runs)
    docs/YYYY-MM-DD/{accession}.txt.gz  the complete submission file, gzipped
    meta/YYYY-MM-DD/{accession}.json    manifest object (S3); manifest.jsonl (local)
Manifest objects are sharded by day so a run lists only the days in its window
— a five-year archive is a million objects, and listing all of them every night
is the mistake the EDINET listing cache exists to paper over.

SEC fair-access rules: at most 10 requests/second and a declared User-Agent of
the form "Company Name contact@domain" — anything else is answered with a 403
page. EDGAR_USER_AGENT is therefore required (env or observatory/.env). This
job stays under 8 requests/second across all workers.

Python 3.9; stdlib in local mode, boto3 (lazy) in S3 mode.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import heartbeat

HERE = os.path.dirname(os.path.abspath(__file__))
# Run from the repository, every SEC request goes through observatory/app/
# sec_gate.py: one rate (SEC_RATE) and one refusal hold for the whole host.
# The capture image ships only equity/, so there the job's own pacing stands.
sys.path.append(os.path.join(HERE, "..", "observatory"))
try:
    from app import sec_gate
except ImportError:
    sec_gate = None
ROOT = os.environ.get("EDGAR_ARCHIVE_ROOT", os.path.join(HERE, "data", "raw", "edgar"))
S3_PREFIX = "edgar"
ARCHIVES = "https://www.sec.gov/Archives/"
INDEX_URL = ARCHIVES + "edgar/daily-index/{year}/QTR{qtr}/master.{ymd}.idx"
FULL_INDEX_URL = ARCHIVES + "edgar/full-index/{year}/QTR{qtr}/master.idx"

CORE_FORMS = {
    "10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A",
    "8-K", "8-K/A", "6-K", "6-K/A",
    "DEF 14A", "DEFA14A", "DEFC14A", "DEFM14A", "PREC14A",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
    "SC TO-T", "SC TO-T/A", "SC TO-I", "SC TO-I/A", "SC 14D9", "SC 14D9/A",
    "S-1", "S-1/A", "S-3", "S-3/A", "S-4", "S-4/A", "F-1", "F-1/A", "F-3", "F-4",
    "424B1", "424B3", "424B4", "424B5",
    "13F-HR", "13F-HR/A",
}
# A complete submission file always opens with the SEC header; anything else
# (an HTML "undeclared automated tool" page, a 404 page) is not a filing.
SUBMISSION_MAGIC = (b"<SEC-HEADER>", b"<SEC-DOCUMENT>",
                    b"-----BEGIN PRIVACY-ENHANCED MESSAGE-----")
# requests/second across all workers. The SEC cap is 10 per client *address*,
# and the daily job and a backfill run side by side from the same egress, so
# each service is given its share via EDGAR_MAX_RATE (backfill 7, daily 2).
MAX_RATE = float(os.environ.get("EDGAR_MAX_RATE", "8"))
RETRIES = 4
TIMEOUT = 120


def user_agent():
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        env_path = os.path.join(HERE, "..", "observatory", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("EDGAR_USER_AGENT="):
                    ua = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not ua or "@" not in ua:
        sys.exit('EDGAR_USER_AGENT not set. The SEC requires "Company Name '
                 'contact@domain" (env or observatory/.env).')
    return ua


class RateLimiter(object):
    """Global token gate: no more than MAX_RATE requests/second across threads."""

    def __init__(self, per_second):
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._interval


class NotFound(Exception):
    pass


def throttled(body):
    """True for the SEC's HTML refusal pages (undeclared client, rate limit)."""
    head = body[:400].lower()
    return (b"<html" in head or b"<!doctype" in head
            or b"automated tool" in body or b"rate threshold" in body)


class Client(object):
    """One HTTP client for the run: keep-alive pool when urllib3 is around
    (the container has boto3), stdlib otherwise; gzip transfer either way."""

    def __init__(self, ua, workers):
        self.headers = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        self.rate = RateLimiter(MAX_RATE)
        try:
            import urllib3
            self._pool = urllib3.PoolManager(
                maxsize=workers + 2, timeout=urllib3.Timeout(total=TIMEOUT),
                headers=self.headers)
        except ImportError:
            self._pool = None

    def _once(self, url):
        """(status, body) — body already de-gzipped."""
        if sec_gate is not None:
            status, body, _ = sec_gate.get(url, timeout=TIMEOUT, headers=self.headers)
            if status == 0:
                raise IOError("no answer from %s" % url)
            return status, body
        if self._pool is not None:
            r = self._pool.request("GET", url, retries=False)
            return r.status, r.data
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return r.status, body
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return e.code, body

    def get(self, url, attempts=RETRIES):
        last = None
        for i in range(attempts):
            self.rate.wait()
            try:
                status, body = self._once(url)
            except Exception as e:                      # connection-level
                last = e
                time.sleep(2 ** i)
                continue
            if status == 200:
                return body
            if status == 404:
                raise NotFound(url)
            if sec_gate is not None and sec_gate.is_refusal(status, body):
                # the gate now holds every SEC job on this host: waiting here changes nothing
                raise RuntimeError("the SEC is refusing this server (%s): %s" % (
                    "held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status, url))
            if status == 403 and not throttled(body):
                # a daily index that does not exist (SEC holiday) is a 403 with
                # a short AccessDenied body, not a 404 — verified 2026-09-10
                raise NotFound(url)
            last = RuntimeError("HTTP %d" % status)
            # a 403 HTML page is how the SEC answers an undeclared client and a
            # client over its rate limit; back off hard so one hot minute
            # doesn't poison the run
            time.sleep((10 if status in (403, 429) else 2) * (2 ** i))
        raise RuntimeError("fetch failed after %d attempts: %s" % (attempts, last))


# ---- storage (edgar/ prefix; mirrors capture.py's two backends) ------------
class LocalStore(object):
    def __init__(self):
        os.makedirs(ROOT, exist_ok=True)
        self._manifest = os.path.join(ROOT, "manifest.jsonl")
        self._lock = threading.Lock()
        self._done = None

    def _load(self):
        done = set()
        if os.path.exists(self._manifest):
            for line in open(self._manifest, encoding="utf-8"):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("status") == "ok":
                    done.add((rec["date"], rec["accession"]))
        return done

    def archived(self, day):
        if self._done is None:
            self._done = self._load()
        return set(a for d, a in self._done if d == day)

    def put(self, rel_key, blob):
        path = os.path.join(ROOT, rel_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)                       # atomic: no truncated archives

    def record(self, rec):
        with self._lock:
            with open(self._manifest, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def acquire_lock(self):
        lock = os.path.join(ROOT, ".lock")
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 12 * 3600:
            sys.exit("another edgar capture appears to be running (%s)" % lock)
        open(lock, "w").write(str(os.getpid()))

    def release_lock(self):
        try:
            os.remove(os.path.join(ROOT, ".lock"))
        except OSError:
            pass


class S3Store(object):
    """Same bucket as the EDINET archive, everything under edgar/. The manifest
    is one small JSON object per filing under meta/{day}/, so "what do we have
    for this day" is one short prefix listing however large the archive gets.
    Failures are only logged; an unrecorded filing is retried next run."""

    def __init__(self, workers):
        import boto3
        from botocore.config import Config
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.c = boto3.client(
            "s3",
            endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
            aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
            aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
            region_name=os.environ.get("EDINET_S3_REGION", "auto"),
            config=Config(max_pool_connections=workers + 4,
                          retries={"max_attempts": 5, "mode": "standard"}))

    def archived(self, day):
        done, token = set(), None
        prefix = "%s/meta/%s/" % (S3_PREFIX, day)
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.c.list_objects_v2(**kw)
            for o in resp.get("Contents") or []:
                name = o["Key"].rsplit("/", 1)[-1]
                if name.endswith(".json"):
                    done.add(name[:-5])
            if not resp.get("IsTruncated"):
                return done
            token = resp.get("NextContinuationToken")

    def put(self, rel_key, blob, content_type=None):
        kw = {"Bucket": self.bucket, "Key": S3_PREFIX + "/" + rel_key, "Body": blob}
        if content_type:
            kw["ContentType"] = content_type
        self.c.put_object(**kw)

    def record(self, rec):
        if rec.get("status") != "ok":
            return                                  # failures live in logs, not the bucket
        self.put("meta/%s/%s.json" % (rec["date"], rec["accession"]),
                 json.dumps(rec, ensure_ascii=False).encode("utf-8"),
                 content_type="application/json")

    def acquire_lock(self):
        pass                                        # idempotency is the concurrency guard

    def release_lock(self):
        pass


def make_store(workers):
    return S3Store(workers) if os.environ.get("EDINET_S3_BUCKET") else LocalStore()


# ---- index --------------------------------------------------------------------
def parse_index(raw):
    """Rows of the SEC master index: (cik, name, form, filed, path). The file has
    a free-text preamble ending in a dashed rule; rows are pipe-separated."""
    rows = []
    started = False
    for line in raw.decode("latin-1").splitlines():
        if not started:
            started = line.startswith("-----")
            continue
        parts = line.split("|")
        if len(parts) == 5 and parts[4].startswith("edgar/data/"):
            rows.append(tuple(p.strip() for p in parts))
    return rows


def targets_for(rows, forms):
    """One job per accession (a 13D filed by a group is listed once per filer);
    the first filer is recorded, the rest counted."""
    jobs = {}
    for cik, name, form, filed, path in rows:
        if form not in forms:
            continue
        accession = path.rsplit("/", 1)[-1]
        if not accession.endswith(".txt"):
            continue
        accession = accession[:-4]
        filed = filed.replace("-", "")       # daily index: 20260909; quarterly: 2026-09-09
        j = jobs.get(accession)
        if j is None:
            jobs[accession] = {"accession": accession, "cik": cik, "filer": name,
                               "form": form, "filed": filed, "path": path,
                               "co_filers": 0}
        else:
            j["co_filers"] += 1
    return list(jobs.values())


# ---- capture -------------------------------------------------------------------
def capture_filing(day, job, client, store, stats, lock):
    """Fetch, verify, compress, store, record. Never raises."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        blob = client.get(ARCHIVES + job["path"])
        if not blob.startswith(SUBMISSION_MAGIC):
            raise RuntimeError("not a submission file (%d bytes: %r)"
                               % (len(blob), blob[:40]))
        packed = gzip.compress(blob, compresslevel=6, mtime=0)
        store.put("docs/%s/%s.txt.gz" % (day, job["accession"]), packed)
        store.record({"date": day, "accession": job["accession"], "cik": job["cik"],
                      "filer": job["filer"], "form": job["form"], "filed": job["filed"],
                      "co_filers": job["co_filers"], "source": ARCHIVES + job["path"],
                      "sha256": hashlib.sha256(blob).hexdigest(),
                      "bytes": len(blob), "stored_bytes": len(packed),
                      "status": "ok", "captured_at": now})
        with lock:
            stats["ok"] += 1
            stats["bytes"] += len(blob)
            stats["stored"] += len(packed)
    except Exception as e:
        store.record({"date": day, "accession": job["accession"], "cik": job["cik"],
                      "form": job["form"], "status": "fail", "error": str(e)[:200],
                      "captured_at": now})
        with lock:
            stats["fail"] += 1
        print("  %s %s %s FAILED: %s" % (day, job["accession"], job["form"], e))


def capture_jobs(day, jobs, client, store, stats, workers):
    """Fetch every filing of one day that the manifest does not have yet."""
    have = store.archived(day)
    todo = [j for j in jobs if j["accession"] not in have]
    stats["skipped"] += len(jobs) - len(todo)
    if not todo:
        return
    print("  %s: %d targets, %d to fetch" % (day, len(jobs), len(todo)))
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for j in todo:
            ex.submit(capture_filing, day, j, client, store, stats, lock)


def fetch_index(label, url, client, stats):
    """Rows of one index file, or None (counted) when absent or unusable."""
    try:
        raw = client.get(url)
    except NotFound:                                # holiday / not yet published
        stats["no_index"] += 1
        return None, None
    except Exception as e:
        stats["list_fail"] += 1
        print("  %s index fetch failed: %s" % (label, e))
        return None, None
    rows = parse_index(raw)
    if not rows:                                    # an HTML page, not an index
        stats["list_fail"] += 1
        print("  %s index unparseable (%d bytes)" % (label, len(raw)))
        return None, None
    return raw, rows


def capture_day(date, forms, client, store, stats, workers):
    """Trailing-window mode: the day's own index, then its filings. Never raises."""
    day = date.isoformat()
    url = INDEX_URL.format(year=date.year, qtr=(date.month - 1) // 3 + 1,
                           ymd=date.strftime("%Y%m%d"))
    raw, rows = fetch_index(day, url, client, stats)
    if rows is None:
        return
    store.put("lists/%s.idx" % day, raw)
    capture_jobs(day, targets_for(rows, forms), client, store, stats, workers)


def quarters(start, end):
    y, q = start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        yield y, q
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)


def capture_range(start, end, forms, client, store, stats, workers):
    """Backfill mode: one quarterly index per quarter, filings grouped by
    their filing date so the layout is identical to the daily runs."""
    for year, q in quarters(start, end):
        label = "%d-QTR%d" % (year, q)
        raw, rows = fetch_index(label, FULL_INDEX_URL.format(year=year, qtr=q),
                                client, stats)
        if rows is None:
            continue
        store.put("lists/full/%s.idx" % label, raw)
        by_day = {}
        for job in targets_for(rows, forms):
            f = job["filed"]
            if len(f) != 8 or not f.isdigit():
                continue
            day = "%s-%s-%s" % (f[:4], f[4:6], f[6:])
            if start.isoformat() <= day <= end.isoformat():
                by_day.setdefault(day, []).append(job)
        print("%s: %d filings listed, %d target days in window"
              % (label, len(rows), len(by_day)))
        for day in sorted(by_day):
            capture_jobs(day, by_day[day], client, store, stats, workers)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7,
                   help="trailing window ending today (default 7; self-heals missed runs)")
    p.add_argument("--start", help="backfill start date YYYY-MM-DD")
    p.add_argument("--end", help="backfill end date YYYY-MM-DD")
    p.add_argument("--forms", help="comma-separated form types (default: the core set)")
    p.add_argument("--add-forms", help="comma-separated form types to add to the set")
    p.add_argument("--workers", type=int, default=4,
                   help="parallel downloads (rate-capped at %d/s regardless)" % MAX_RATE)
    args = p.parse_args()

    today = dt.date.today()
    if args.start:
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end) if args.end else today
    else:
        start, end = today - dt.timedelta(days=args.days - 1), today
    forms = set(CORE_FORMS)
    if args.forms:
        forms = set(f.strip() for f in args.forms.split(",") if f.strip())
    if args.add_forms:
        forms |= set(f.strip() for f in args.add_forms.split(",") if f.strip())

    ua = user_agent()
    store = make_store(args.workers)
    store.acquire_lock()
    try:
        client = Client(ua, args.workers)
        print("store: %s | forms: %d | window %s..%s | workers %d"
              % (type(store).__name__, len(forms), start, end, args.workers))
        stats = {"ok": 0, "fail": 0, "skipped": 0, "list_fail": 0, "no_index": 0,
                 "bytes": 0, "stored": 0}
        if args.start:
            capture_range(start, end, forms, client, store, stats, args.workers)
        else:
            d = start
            while d <= end:
                if d.weekday() < 5:                 # EDGAR disseminates on business days
                    capture_day(d, forms, client, store, stats, args.workers)
                d += dt.timedelta(days=1)
        summary = ("edgar capture %s..%s  archived:%d (%.1fMB raw, %.1fMB stored)  "
                   "skipped(existing):%d  failed:%d  index-failures:%d  no-index(holiday or not yet published):%d"
                   % (start, end, stats["ok"], stats["bytes"] / 1e6, stats["stored"] / 1e6,
                      stats["skipped"], stats["fail"], stats["list_fail"], stats["no_index"]))
        print(summary)
        # a failed *index* means a whole day may be missing; single-filing
        # failures are routine and heal on the next trailing-window run
        heartbeat.ping(summary, failed=stats["list_fail"] > 0)
    finally:
        store.release_lock()
    # a missing index is a missing quarter or day: exit non-zero so a backfill
    # service with an on-failure restart policy comes back and retries it
    # (everything already banked is skipped by the manifest)
    sys.exit(1 if stats["list_fail"] else 0)


if __name__ == "__main__":
    main()
