# -*- coding: utf-8 -*-
"""M2 — EDINET daily capture (the compounding archive).

Capture != parse. This job archives raw disclosure documents before EDINET
deletes them (list API reaches back ~5 years; files are removed 10 years after
filing). It is idempotent and fail-safe: every run re-scans a trailing window
of days, downloads only what the manifest doesn't already record as archived,
verifies zip integrity, and records SHA-256. A failed document is logged and
retried on the next run; a failed day never blocks other days.

Usage:
    python capture.py                       # trailing 7 days through today
    python capture.py --days 30             # wider catch-up window
    python capture.py --start 2026-06-01 --end 2026-06-30   # backfill a range
    python capture.py --start 2016-10-01 --types 140,150    # only these doc types

Captured document types (verified empirically 2026-08-06/07):
    120 有価証券報告書 (annual report; cross-shareholding tables)
    130 訂正有価証券報告書 (corrections -> new vintage, never overwrite)
    160/170 半期報告書 + 訂正 (semiannual reports; the mid-year record since
            quarterlies were abolished)
    180/190 臨時報告書 + 訂正 (extraordinary reports: merger/share-exchange
            resolutions AND per-proposal AGM voting results — the activist scorecard)
    240/250 公開買付届出書 + 訂正 (tender offers: terms, price bumps, extensions)
    270 公開買付報告書 (tender offer results)
    290/300 意見表明報告書 + 訂正 (target board's opinion — hostile vs friendly)
    030/040 有価証券届出書 + 訂正 (capital raises incl. third-party allotments —
            the dilution/entrenchment red flag)
    350 大量保有報告書・変更報告書 (5% rule family; activist signal)
    360 訂正報告書 (大量保有・変更報告書)
    220 自己株券買付状況報告書 (buyback status reports)
Deliberately skipped: 135 確認書 (boilerplate confirmations), 235 内部統制報告書
(J-SOX attestations) — high volume, no analytical content for our products.

For 120/130 both the full XBRL package (type=1, canonical raw) and the
XBRL-to-CSV package (type=5, what the extractor reads) are archived; for the
small high-volume types only type=5 is skipped and type=1 kept.

Storage backends:
    local (default) — files under EDINET_ARCHIVE_ROOT, manifest.jsonl
    S3-compatible   — set EDINET_S3_BUCKET/_ENDPOINT/_KEY_ID/_SECRET (Railway
                      bucket in production); needs boto3, imported lazily so the
                      laptop's stdlib-only local mode is unaffected

Python 3.9. Key from EDINET_API_KEY or observatory/.env.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import heartbeat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("EDINET_ARCHIVE_ROOT",
                      os.path.join(HERE, "data", "raw", "edinet"))
LIST_URL = ("https://api.edinet-fsa.go.jp/api/v2/documents.json"
            "?date={date}&type=2&Subscription-Key={key}")
DOC_URL = ("https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
           "?type={dl_type}&Subscription-Key={key}")

DOC_TYPES = {"120", "130", "160", "170", "180", "190",
             "240", "250", "270", "290", "300",
             "030", "040", "350", "360", "220", "230"}
# 230 (a buyback report's correction) from 2026-10-24: equity/buyback.py now
# reads corrections, and EDINET keeps buyback reports only about a year.
# periodic reports: CSV package too. 140/150 (quarterly reports, abolished for
# periods from April 2024) are not in DOC_TYPES; a backfill asks for them with
# --types 140,150
CSV_ALSO = {"120", "130", "140", "150", "160", "170"}
THROTTLE_SECONDS = 0.7             # be a polite client; EDINET bans heavy users
RETRIES = 3
TIMEOUT = 90


def api_key():
    key = os.environ.get("EDINET_API_KEY")
    if key:
        return key
    env_path = os.path.join(HERE, "..", "observatory", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("EDINET_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("EDINET_API_KEY not set (env or observatory/.env)")


try:                                   # container has boto3 -> urllib3 available;
    import urllib3                     # keep-alive halves per-request TLS cost
    _POOL = urllib3.PoolManager(maxsize=2, timeout=urllib3.Timeout(total=TIMEOUT),
                                headers={"User-Agent": "observatory-capture/1.0"})
except ImportError:                    # laptop local mode: stdlib fallback
    _POOL = None


def fetch(url, attempts=RETRIES):
    last = None
    for i in range(attempts):
        try:
            if _POOL is not None:
                r = _POOL.request("GET", url)
                if r.status != 200:
                    raise RuntimeError("HTTP %d" % r.status)
                return r.data
            req = urllib.request.Request(url, headers={"User-Agent": "observatory-capture/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError("fetch failed after %d attempts: %s" % (attempts, last))


class LocalStore(object):
    """Filesystem archive under ROOT with an append-only manifest."""

    def __init__(self):
        os.makedirs(ROOT, exist_ok=True)
        self._manifest = os.path.join(ROOT, "manifest.jsonl")

    def archived(self):
        done = set()
        if os.path.exists(self._manifest):
            for line in open(self._manifest, encoding="utf-8"):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("status") == "ok":
                    done.add((rec["doc_id"], rec["dl_type"]))
        return done

    def put_list(self, day, raw):
        d = os.path.join(ROOT, "lists")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, day + ".json"), "wb") as f:
            f.write(raw)

    def put_doc(self, day, filename, blob):
        d = os.path.join(ROOT, "docs", day)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, filename)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)                # atomic: no truncated archives

    def record(self, rec):
        with open(self._manifest, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def acquire_lock(self):
        lock = os.path.join(ROOT, ".lock")
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 6 * 3600:
            sys.exit("another capture appears to be running (%s)" % lock)
        open(lock, "w").write(str(os.getpid()))

    def release_lock(self):
        try:
            os.remove(os.path.join(ROOT, ".lock"))
        except OSError:
            pass


class S3Store(object):
    """S3-compatible archive (Railway bucket). Same layout; the manifest is one
    small JSON object per archived document under meta/, so the archived-set is
    a prefix listing and S3's lack of append never matters. Failures are only
    logged (visible in service logs) — an unrecorded doc is retried next run,
    which is the behaviour we want anyway."""

    def __init__(self):
        import boto3                          # lazy: local mode stays stdlib-only
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.c = boto3.client(
            "s3",
            endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
            aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
            aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
            region_name=os.environ.get("EDINET_S3_REGION", "auto"))

    def archived(self):
        done = set()
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": "meta/"}
            if token:
                kw["ContinuationToken"] = token
            resp = self.c.list_objects_v2(**kw)
            for o in resp.get("Contents") or []:
                name = o["Key"].rsplit("/", 1)[-1]      # {doc_id}_t{n}.json
                if name.endswith(".json") and "_t" in name:
                    doc_id, t = name[:-5].rsplit("_t", 1)
                    done.add((doc_id, t))
            if not resp.get("IsTruncated"):
                return done
            token = resp.get("NextContinuationToken")

    def put_list(self, day, raw):
        self.c.put_object(Bucket=self.bucket, Key="lists/%s.json" % day, Body=raw)

    def put_doc(self, day, filename, blob):
        self.c.put_object(Bucket=self.bucket,
                          Key="docs/%s/%s" % (day, filename), Body=blob)

    def record(self, rec):
        if rec.get("status") != "ok":
            return                            # failures live in logs, not the bucket
        key = "meta/%s_t%s.json" % (rec["doc_id"], rec["dl_type"])
        self.c.put_object(Bucket=self.bucket, Key=key,
                          Body=json.dumps(rec, ensure_ascii=False).encode("utf-8"))

    def acquire_lock(self):
        pass                                  # idempotency is the concurrency guard

    def release_lock(self):
        pass


def make_store():
    if os.environ.get("EDINET_S3_BUCKET"):
        return S3Store()
    return LocalStore()


def capture_day(date, key, store, archived, stats):
    """Archive the day's list and its target documents. Never raises."""
    day = date.isoformat()
    try:
        raw = fetch(LIST_URL.format(date=day, key=key))
        payload = json.loads(raw)
        status = str(payload.get("metadata", {}).get("status"))
        if status != "200":
            stats["list_fail"] += 1
            print("  %s list status %s — skipped" % (day, status))
            return
    except Exception as e:
        stats["list_fail"] += 1
        print("  %s list fetch failed: %s" % (day, e))
        return
    # archive the full daily list (tiny; it is the metadata index for the day)
    store.put_list(day, raw)

    # fundCode marks investment-trust filings (ordinanceCode 030) — irrelevant to
    # corporate governance data and a large share of type-120 volume; skip them
    targets = [r for r in payload.get("results") or []
               if r.get("docTypeCode") in DOC_TYPES and not r.get("fundCode")]
    for r in targets:
        doc_id = r["docID"]
        # the list declares which packages exist; respect it (some foreign filers
        # submit PDF-only — xbrlFlag=0, csvFlag=0)
        dl_types = []
        if str(r.get("xbrlFlag")) == "1":
            dl_types.append("1")
        if str(r.get("csvFlag")) == "1" and r["docTypeCode"] in CSV_ALSO:
            dl_types.append("5")
        if not dl_types and str(r.get("pdfFlag")) == "1":
            dl_types.append("2")            # PDF as the only available raw
        for dl_type in dl_types:
            if (doc_id, dl_type) in archived:
                stats["skipped"] += 1
                continue
            time.sleep(THROTTLE_SECONDS)
            try:
                blob = fetch(DOC_URL.format(doc_id=doc_id, dl_type=dl_type, key=key))
                magic = b"%PDF" if dl_type == "2" else b"PK"
                if not blob.startswith(magic):   # API error payload, not a document
                    raise RuntimeError("bad magic (%d bytes)" % len(blob))
                ext = "pdf" if dl_type == "2" else "zip"
                store.put_doc(day, "%s_t%s.%s" % (doc_id, dl_type, ext), blob)
                store.record({"date": day, "doc_id": doc_id, "dl_type": dl_type,
                        "doc_type": r["docTypeCode"],
                        "filer": r.get("filerName"), "sec_code": r.get("secCode"),
                        "sha256": hashlib.sha256(blob).hexdigest(),
                        "bytes": len(blob), "status": "ok",
                        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()})
                archived.add((doc_id, dl_type))
                stats["ok"] += 1
            except Exception as e:
                store.record({"date": day, "doc_id": doc_id, "dl_type": dl_type,
                        "doc_type": r.get("docTypeCode"), "status": "fail",
                        "error": str(e)[:200],
                        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()})
                stats["fail"] += 1
                print("  %s %s t%s FAILED: %s" % (day, doc_id, dl_type, e))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7,
                   help="trailing window ending today (default 7; self-heals missed runs)")
    p.add_argument("--start", help="backfill start date YYYY-MM-DD")
    p.add_argument("--end", help="backfill end date YYYY-MM-DD")
    p.add_argument("--types", help="comma-separated docTypeCodes to capture "
                   "instead of the default set (e.g. 140,150 for quarterly reports)")
    args = p.parse_args()
    if args.types:
        DOC_TYPES.clear()
        DOC_TYPES.update(t.strip() for t in args.types.split(",") if t.strip())

    today = dt.date.today()
    if args.start:
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end) if args.end else today
    else:
        start, end = today - dt.timedelta(days=args.days - 1), today

    store = make_store()
    store.acquire_lock()
    try:
        key = api_key()
        archived = store.archived()
        print("store: %s | already archived: %d documents"
              % (type(store).__name__, len(archived)))
        stats = {"ok": 0, "fail": 0, "skipped": 0, "list_fail": 0}
        d = start
        while d <= end:
            if d.weekday() < 5:                 # EDINET publishes business days
                capture_day(d, key, store, archived, stats)
            d += dt.timedelta(days=1)
        summary = ("capture %s..%s  archived:%d  skipped(existing):%d  failed:%d  list-failures:%d"
                   % (start, end, stats["ok"], stats["skipped"], stats["fail"], stats["list_fail"]))
        print(summary)
        # a failed *list* means a whole day may be missing; individual document
        # failures are routine and heal on the next trailing-window run
        heartbeat.ping(summary, failed=stats["list_fail"] > 0)
    finally:
        store.release_lock()


if __name__ == "__main__":
    main()
