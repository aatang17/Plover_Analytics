# -*- coding: utf-8 -*-
"""US reference snapshots — the internal shelf of US official data.

Not a product surface. The product is Japan-deep; this job keeps the main US
official data series on the same shelf as the EDGAR filings, so that a US
number is always at hand for reference and for revision history. Nothing here
is parsed or served.

It is a vintage collector on the boj_capture.py pattern, with one refinement:
a file is stored only when its content has changed. Every run asks each source
for its current file (a conditional request where the server supports it, so
an unchanged BLS file costs one round trip and no download), compares the
content hash with the last stored copy, and banks a new dated copy only on a
change. A stored copy is never overwritten — a revision is a new object under a
new date — so the archive is the revision history of every file, at the
granularity of the run schedule, at the cost of the changes alone.

Sources (all free, bulk, keyless; verified 2026-09-11):
    fed       Federal Reserve Data Download Program full-release zips: H.4.1
              balance sheet, H.15 rates, H.8 bank assets, H.6 money stock,
              H.3 reserves, H.10 FX, G.17 industrial production, G.19 consumer
              credit, G.20 finance companies, E.2 business lending, CP
              commercial paper, PRATES policy rates, Z.1 flow of funds
    nyfed     SOMA holdings (summary history, latest Treasury and agency
              holdings by CUSIP, monthly file) and the reference rates
    treasury  daily par yield curve and real yield curve, one file per year;
              TIC major foreign holders of Treasuries
    fiscal    Fiscal Data API: debt to the penny, daily Treasury statement cash
              balance, auction results, average interest rates — paged CSV
    bls       the bulk flat files behind CPI-U, CPI-W, chained CPI, average
              prices, PPI commodity and industry, import/export prices, JOLTS,
              productivity, ECI, employment (CES), labour force (CPS), OES
    bea       NIPA flat files (annual, quarterly, monthly) and their registers
    cftc      Commitments of Traders: financial futures, disaggregated, legacy
    sec       Financial Statement Data Sets (the SEC's own quarterly extraction
              of every XBRL number filed) — sits beside the EDGAR filings
    fomc      the FOMC calendar page and every statement, implementation note,
              minutes and projections document it links to
    fdic      BankFind: every institution ever insured, and the aggregates

Usage:
    python us_capture.py                # today's snapshot of every source
    python us_capture.py --sources fed,bls
    python us_capture.py --all-years    # one-off: every historic year file
                                        # (Treasury 1990+, CFTC 1986+, SEC 2009+)

Layout (local: US_ARCHIVE_ROOT, default equity/data/raw/us; S3: the EDINET
bucket under the us/ prefix — same EDINET_S3_* variables):
    us/YYYY-MM-DD/{source}/{filename}[.gz]   a stored copy (text gzipped)
    us/meta/YYYY-MM-DD/{source}__{filename}.json   manifest per stored copy
    us/latest/{source}__{filename}.json            pointer: last content hash,
                                                   ETag and Last-Modified
The pointer is the one mutable object; it is bookkeeping, not data.

Fair use: one request at a time, half a second apart, a declared User-Agent
(the BLS and SEC refuse undeclared clients; EDGAR_USER_AGENT is reused).
Python 3.9; stdlib in local mode, boto3 (lazy) in S3 mode.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import heartbeat

HERE = os.path.dirname(os.path.abspath(__file__))
# Run from the repository, every SEC request (the statement data sets) goes
# through observatory/app/sec_gate.py: one rate (SEC_RATE) and one refusal
# hold for the whole host. The capture image ships only equity/, so there the
# job's own pacing stands. Other sources never touch the gate.
sys.path.append(os.path.join(HERE, "..", "observatory"))
try:
    from app import sec_gate
except ImportError:
    sec_gate = None
ROOT = os.environ.get("US_ARCHIVE_ROOT", os.path.join(HERE, "data", "raw", "us"))
S3_PREFIX = "us"
THROTTLE_SECONDS = 0.5
RETRIES = 3
TIMEOUT = 300
COMPRESSED_EXT = (".zip", ".pdf", ".gz")

FED_RELEASES = ["H41", "H15", "H8", "H6", "H3", "H10", "G17", "G19", "G20",
                "E2", "CP", "PRATES", "Z1"]
FED_URL = "https://www.federalreserve.gov/datadownload/Output.aspx?rel={rel}&filetype=zip"
NYFED = "https://markets.newyorkfed.org/api/"
TSY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           "daily-treasury-rates.csv/{year}/all?type={kind}&field_tdr_date_value={year}"
           "&page&_format=csv")
FISCAL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
FISCAL_ENDPOINTS = {
    "debt_to_penny": "v2/accounting/od/debt_to_penny",
    "dts_operating_cash_balance": "v1/accounting/dts/operating_cash_balance",
    "auctions": "v1/accounting/od/auctions_query",
    "avg_interest_rates": "v2/accounting/od/avg_interest_rates",
}
BLS = "https://download.bls.gov/pub/time.series/"
BLS_DIRS = {                      # directory -> main data file (the fallback when
    "cu": "cu.data.0.Current",    # the listing cannot be read)
    "cw": "cw.data.0.Current",
    "su": "su.data.0.Current",
    "ap": "ap.data.0.Current",
    "wp": "wp.data.0.Current",
    "pc": "pc.data.0.Current",
    "ei": "ei.data.0.Current",
    "jt": "jt.data.0.Current",
    "pr": "pr.data.0.Current",
    "ci": "ci.data.0.Current",
    "ec": "ec.data.0.Current",
    "ce": "ce.data.0.AllCESSeries",
    "ln": "ln.data.1.AllData",
    "oe": "oe.data.0.Current",
}
BEA = "https://apps.bea.gov/national/Release/TXT/"
BEA_FILES = ["NipaDataA.txt", "NipaDataQ.txt", "NipaDataM.txt",
             "SeriesRegister.txt", "TablesRegister.txt"]
CFTC = "https://www.cftc.gov/files/dea/history/"
SEC_FSDS = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{y}q{q}.zip"
# Two more of the SEC's own structured data sets, published quarterly as zips
# of tab-separated tables: every Form 3/4/5 (insider holdings and trades,
# from 2006q1) and every Form 13F holdings report (from 2013q2). Their zip
# paths changed folder in 2026 (structureddata -> datastandardsinnovation),
# so the listing page is read for the links instead of guessing a pattern.
SEC_INSIDERS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
SEC_13F_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
SEC_ZIP_LINK = re.compile(r'href="(/files/[^"]+?/([^"/]+_form(?:345|13f)\.zip))"')
# Four more, each listed on its own page and read the same way (pages, file
# names and sizes checked 2026-10-24):
#   sec-ftd    fails-to-deliver, twice a month: cnsfailsYYYYMM{a,b}.zip, ~1.5MB, from 2004
#   sec-formd  Form D private-offering notices, quarterly: YYYYqN_d.zip, ~4MB
#   sec-nport  fund portfolio holdings from Form N-PORT, quarterly: YYYYqN_nport.zip, ~460MB
#   sec-notes  financial statements with their notes, monthly: YYYY_MM_notes.zip, ~60MB
SEC_LISTED = {
    "sec-ftd": ("https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data",
                r"cnsfails\d{6}[ab]\.zip"),
    "sec-formd": ("https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets",
                  r"\d{4}q[1-4]_d\.zip"),
    "sec-nport": ("https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets",
                  r"\d{4}q[1-4]_nport\.zip"),
    "sec-notes": ("https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets",
                  r"\d{4}_\d{2}_notes\.zip"),
}
FOMC_CAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_LINK = re.compile(r'href="(/(?:newsevents/pressreleases/monetary\d{8}[a-z]\d?\.htm'
                       r'|monetarypolicy/fomcminutes\d{8}\.htm'
                       r'|monetarypolicy/files/[A-Za-z0-9_\-]+\.pdf))"')
FDIC = "https://banks.data.fdic.gov/api/"
PAGE = 10000


def user_agent():
    ua = os.environ.get("EDGAR_USER_AGENT")
    if not ua:
        env_path = os.path.join(HERE, "..", "observatory", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("EDGAR_USER_AGENT="):
                    ua = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not ua or "@" not in ua:
        sys.exit('EDGAR_USER_AGENT not set ("Company Name contact@domain"); '
                 'the BLS and the SEC refuse undeclared clients.')
    return ua


class NotFound(Exception):
    pass


class NotModified(Exception):
    pass


class Refused(Exception):
    """The SEC refused this server, or the host's gate is holding after a refusal."""


class Client(object):
    """Streams every response to a temp file so a 400MB BLS file never sits
    in memory. Returns (path, sha256, headers). urllib3 when present (the
    container has boto3), stdlib otherwise."""

    def __init__(self, ua):
        self.ua = ua
        try:
            import urllib3
            self._pool = urllib3.PoolManager(maxsize=2, timeout=urllib3.Timeout(total=TIMEOUT))
        except ImportError:
            self._pool = None

    def _headers(self, cond):
        h = {"User-Agent": self.ua}
        if cond.get("etag"):
            h["If-None-Match"] = cond["etag"]
        if cond.get("last_modified"):
            h["If-Modified-Since"] = cond["last_modified"]
        if self._pool is not None:
            h["Accept-Encoding"] = "gzip, deflate"
        return h

    def _once(self, url, cond, out):
        sha = hashlib.sha256()
        if sec_gate is not None and sec_gate.is_sec(url):
            status, body, headers = sec_gate.get(url, timeout=TIMEOUT, headers=self._headers(cond))
            if status == 0:
                raise IOError("no answer from %s" % url)
            if sec_gate.is_refusal(status, body):
                raise Refused("the SEC is refusing this server (%s): %s" % (
                    "held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status, url))
            if status == 200:
                out.write(body)
            return status, headers
        if self._pool is not None:
            for _ in range(5):                     # follow redirects by hand
                r = self._pool.request("GET", url, headers=self._headers(cond),
                                       preload_content=False, retries=False)
                if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    r.read()
                    r.release_conn()
                    url = urllib.parse.urljoin(url, r.headers["Location"])
                    continue
                break
            try:
                if r.status != 200:
                    r.read()
                    return r.status, dict(r.headers)
                for chunk in r.stream(1 << 16, decode_content=True):
                    out.write(chunk)
                    sha.update(chunk)
                return 200, dict(r.headers)
            finally:
                r.release_conn()
        req = urllib.request.Request(url, headers=self._headers(cond))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                return 200, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    def get(self, url, cond=None, attempts=RETRIES):
        """Download to a temp file. Raises NotFound / NotModified."""
        cond = cond or {}
        last = None
        for i in range(attempts):
            time.sleep(THROTTLE_SECONDS)
            fd, path = tempfile.mkstemp(prefix="us-capture-")
            out = os.fdopen(fd, "wb")
            try:
                status, headers = self._once(url, cond, out)
                out.close()
                if status == 200:
                    return path, headers
                os.remove(path)
                if status == 304:
                    raise NotModified(url)
                if status in (404, 400):
                    raise NotFound(url)
                last = RuntimeError("HTTP %d" % status)
                time.sleep((10 if status in (403, 429) else 2) * (2 ** i))
            except (NotFound, NotModified):
                raise
            except Refused:
                # waiting changes nothing: the gate holds every SEC job on this host
                out.close()
                os.remove(path)
                raise
            except Exception as e:
                out.close()
                if os.path.exists(path):
                    os.remove(path)
                last = e
                time.sleep(2 ** i)
        raise RuntimeError("fetch failed after %d attempts: %s" % (attempts, last))

    def text(self, url):
        """Small text resource, in memory (listings, calendars, API metadata)."""
        path, _ = self.get(url)
        try:
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.remove(path)


# ---- storage (us/ prefix; mirrors boj_capture.py's two backends) -----------
def pointer_name(source, filename):
    return "%s__%s" % (source, filename)


class LocalStore(object):
    def __init__(self):
        os.makedirs(ROOT, exist_ok=True)
        self._manifest = os.path.join(ROOT, "manifest.jsonl")

    def exists(self, rel_key):
        return os.path.exists(os.path.join(ROOT, rel_key))

    def put_file(self, rel_key, path):
        dest = os.path.join(ROOT, rel_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest + ".part")
        os.replace(dest + ".part", dest)

    def get_pointer(self, source, filename):
        p = os.path.join(ROOT, "latest", pointer_name(source, filename) + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return None

    def set_pointer(self, source, filename, rec):
        d = os.path.join(ROOT, "latest")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, pointer_name(source, filename) + ".json")
        with open(p + ".part", "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(p + ".part", p)

    def record(self, rec):
        with open(self._manifest, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def acquire_lock(self):
        lock = os.path.join(ROOT, ".lock")
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 12 * 3600:
            sys.exit("another us capture appears to be running (%s)" % lock)
        open(lock, "w").write(str(os.getpid()))

    def release_lock(self):
        try:
            os.remove(os.path.join(ROOT, ".lock"))
        except OSError:
            pass


class S3Store(object):
    def __init__(self):
        import boto3
        from botocore.config import Config
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.c = boto3.client(
            "s3",
            endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
            aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
            aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
            region_name=os.environ.get("EDINET_S3_REGION", "auto"),
            config=Config(retries={"max_attempts": 5, "mode": "standard"}))

    def _key(self, rel_key):
        return S3_PREFIX + "/" + rel_key

    def exists(self, rel_key):
        try:
            self.c.head_object(Bucket=self.bucket, Key=self._key(rel_key))
            return True
        except self.c.exceptions.ClientError:
            return False

    def put_file(self, rel_key, path):
        self.c.upload_file(path, self.bucket, self._key(rel_key))
        os.remove(path)

    def get_pointer(self, source, filename):
        try:
            r = self.c.get_object(Bucket=self.bucket,
                                  Key=self._key("latest/%s.json" % pointer_name(source, filename)))
            return json.loads(r["Body"].read())
        except self.c.exceptions.NoSuchKey:
            return None
        except self.c.exceptions.ClientError:
            return None

    def set_pointer(self, source, filename, rec):
        self.c.put_object(Bucket=self.bucket,
                          Key=self._key("latest/%s.json" % pointer_name(source, filename)),
                          Body=json.dumps(rec).encode("utf-8"),
                          ContentType="application/json")

    def record(self, rec):
        if rec.get("status") != "ok":
            return
        self.c.put_object(Bucket=self.bucket,
                          Key=self._key("meta/%s/%s.json"
                                        % (rec["date"], pointer_name(rec["source"], rec["file"]))),
                          Body=json.dumps(rec, ensure_ascii=False).encode("utf-8"),
                          ContentType="application/json")

    def acquire_lock(self):
        pass

    def release_lock(self):
        pass


def make_store():
    return S3Store() if os.environ.get("EDINET_S3_BUCKET") else LocalStore()


# ---- one file --------------------------------------------------------------
def content_hash(path, filename):
    """What "unchanged" means. A zip built on request (the Fed's) differs
    byte-for-byte every day in its timestamps, so a zip is compared by its
    members' names, sizes and CRCs; anything else by its bytes."""
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(path) as z:
                parts = sorted("%s|%d|%08x" % (i.filename, i.file_size, i.CRC)
                               for i in z.infolist())
            return "zip:" + hashlib.sha256("\n".join(parts).encode()).hexdigest()
        except zipfile.BadZipFile:
            return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def capture_file(source, filename, url, client, store, day, stats, expect_absent=False):
    """Fetch one file; store it only if its content changed. Never raises."""
    label = "%s/%s" % (source, filename)
    pointer = store.get_pointer(source, filename) or {}
    try:
        path, headers = client.get(url, {"etag": pointer.get("etag"),
                                         "last_modified": pointer.get("last_modified")})
    except NotModified:
        stats["unchanged"] += 1
        return
    except NotFound:
        stats["absent"] += 1
        if not expect_absent:
            print("  %s: not found" % label)
        return
    except Exception as e:
        stats["fail"] += 1
        print("  %s FAILED: %s" % (label, e))
        store.record({"date": day, "source": source, "file": filename, "url": url,
                      "status": "fail", "error": str(e)[:200]})
        return
    try:
        size = os.path.getsize(path)
        digest = content_hash(path, filename)
        if digest is None or size == 0:
            raise RuntimeError("not a usable file (%d bytes)" % size)
        if digest == pointer.get("content_hash"):
            stats["unchanged"] += 1
            os.remove(path)
            return
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        stored_name = filename
        if not filename.lower().endswith(COMPRESSED_EXT):
            gz = path + ".gz"
            with open(path, "rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out, 1 << 20)
            os.remove(path)
            path, stored_name = gz, filename + ".gz"
        rel_key = "%s/%s/%s" % (day, source, stored_name)
        if store.exists(rel_key):            # same day, changed again: new object, never overwrite
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
            rel_key = "%s/%s/%s.%s" % (day, source, stamp, stored_name)
        stored_bytes = os.path.getsize(path)
        store.put_file(rel_key, path)
        rec = {"date": day, "source": source, "file": filename, "url": url,
               "key": S3_PREFIX + "/" + rel_key, "sha256": sha.hexdigest(),
               "content_hash": digest, "bytes": size, "stored_bytes": stored_bytes,
               "etag": headers.get("ETag") or headers.get("etag"),
               "last_modified": headers.get("Last-Modified") or headers.get("last-modified"),
               "previous": pointer.get("key"), "status": "ok",
               "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        store.record(rec)
        store.set_pointer(source, filename, rec)
        stats["stored"] += 1
        stats["bytes"] += size
        print("  %s: stored (%.1fMB)" % (label, size / 1e6))
    except Exception as e:
        stats["fail"] += 1
        print("  %s FAILED: %s" % (label, e))
        if os.path.exists(path):
            os.remove(path)


# ---- sources ---------------------------------------------------------------
def src_fed(ctx):
    for rel in FED_RELEASES:
        ctx.take("fed", rel + ".zip", FED_URL.format(rel=rel))


def src_nyfed(ctx):
    ctx.take("nyfed", "soma_summary.csv", NYFED + "soma/summary.csv")
    ctx.take("nyfed", "rates_latest.csv", NYFED + "rates/all/latest.csv")
    ctx.take("nyfed", "soma_tsy_monthly.csv", NYFED + "soma/tsy/get/monthly.csv")
    try:
        asof = ctx.client.text(NYFED + "soma/asofdates/latest.csv").decode("utf-8", "replace")
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", asof)
        if not m:
            raise RuntimeError("no as-of date in %r" % asof[:60])
        date = "%s-%s-%s" % (m.group(3), m.group(1), m.group(2))
    except Exception as e:
        ctx.stats["fail"] += 1
        print("  nyfed as-of date FAILED: %s" % e)
        return
    for kind in ("tsy", "agency"):
        ctx.take("nyfed", "soma_%s_%s.csv" % (kind, date),
                 NYFED + "soma/%s/get/all/asof/%s.csv" % (kind, date))


def src_treasury(ctx):
    years = [ctx.today.year]
    if ctx.today.month == 1:
        years.append(ctx.today.year - 1)
    kinds = {"daily_treasury_yield_curve": ("yield_curve", 1990),
             "daily_treasury_real_yield_curve": ("real_yield_curve", 2003)}
    for kind, (name, first) in kinds.items():
        ys = list(range(first, ctx.today.year + 1)) if ctx.all_years else years
        for y in ys:
            ctx.take("treasury", "%s_%d.csv" % (name, y), TSY_URL.format(year=y, kind=kind))
    ctx.take("treasury", "tic_mfh.txt", "https://ticdata.treasury.gov/Publish/mfh.txt")
    ctx.take("treasury", "tic_mfhhis01.txt", "https://ticdata.treasury.gov/Publish/mfhhis01.txt")


def src_fiscal(ctx):
    for name, ep in FISCAL_ENDPOINTS.items():
        base = FISCAL + ep + "?page[size]=%d&sort=record_date" % PAGE
        try:
            meta = json.loads(ctx.client.text(base + "&format=json&page[number]=1")).get("meta", {})
            pages = int(meta.get("total-pages") or 1)
        except Exception as e:
            ctx.stats["fail"] += 1
            print("  fiscal/%s page count FAILED: %s" % (name, e))
            continue
        for p in range(1, pages + 1):
            ctx.take("fiscal", "%s_p%03d.csv" % (name, p), base + "&format=csv&page[number]=%d" % p)


def bls_listing(ctx, d):
    """Files in one BLS directory: the metadata files plus the main data
    file(s). The per-item data.N files duplicate data.0 and are left out."""
    try:
        html = ctx.client.text(BLS + d + "/").decode("utf-8", "replace")
    except Exception:
        return [BLS_DIRS[d]]
    names = set(re.findall(r'href="[^"]*?(%s\.[^"/<>]+)"' % d, html, re.I))
    names = set(n for n in names if not n.lower().endswith((".txt.bak", ".zip", ".tar", ".gz")))
    if not names:
        return [BLS_DIRS[d]]
    keep = []
    for n in sorted(names):
        if ".data." in n and not (".data.0." in n or "AllData" in n or "AllCESSeries" in n):
            continue
        keep.append(n)
    if BLS_DIRS[d] not in keep:
        keep.append(BLS_DIRS[d])
    return keep


def src_bls(ctx):
    for d in BLS_DIRS:
        for name in bls_listing(ctx, d):
            ctx.take("bls", name, BLS + d + "/" + name)


def src_bea(ctx):
    for name in BEA_FILES:
        ctx.take("bea", name, BEA + name)


def src_cftc(ctx):
    y = ctx.today.year
    fin = range(2006, y + 1) if ctx.all_years else [y]
    for yy in fin:
        ctx.take("cftc", "fut_fin_txt_%d.zip" % yy, CFTC + "fut_fin_txt_%d.zip" % yy)
        ctx.take("cftc", "fut_disagg_txt_%d.zip" % yy, CFTC + "fut_disagg_txt_%d.zip" % yy)
    legacy = range(2017, y + 1) if ctx.all_years else [y]
    if ctx.all_years:
        ctx.take("cftc", "deacot1986_2016.zip", CFTC + "deacot1986_2016.zip")
    for yy in legacy:
        ctx.take("cftc", "deacot%d.zip" % yy, CFTC + "deacot%d.zip" % yy)


def src_sec(ctx):
    """The data set for a quarter appears a month or two after it ends: the
    last two quarters are always tried and a 404 is silent."""
    y, q = ctx.today.year, (ctx.today.month - 1) // 3 + 1
    quarters = []
    for _ in range(2):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        quarters.append((y, q))
    if ctx.sec_quarters:
        quarters = list(ctx.sec_quarters)
    elif ctx.all_years:
        quarters = [(yy, qq) for yy in range(2009, ctx.today.year + 1) for qq in range(1, 5)
                    if (yy, qq) <= quarters[0]]
    for yy, qq in quarters:
        ctx.take("sec", "fsds_%dq%d.zip" % (yy, qq), SEC_FSDS.format(y=yy, q=qq),
                 expect_absent=True)


def _sec_listed_zips(ctx, page, label, link=SEC_ZIP_LINK):
    """The data-set zips a SEC listing page links to, newest first as the
    page orders them. The last two are taken unless --all-years, and a file
    already on the shelf costs one conditional request."""
    try:
        html = ctx.client.text(page).decode("utf-8", "replace")
    except Exception as e:
        ctx.stats["fail"] += 1
        print("  %s listing FAILED: %s" % (label, e))
        return []
    seen, links = set(), []
    for path, filename in link.findall(html):
        if filename not in seen:
            seen.add(filename)
            links.append((filename, "https://www.sec.gov" + path))
    if not links:
        ctx.stats["fail"] += 1
        print("  %s listing: no data-set links found (page changed?)" % label)
    return links if ctx.all_years else links[:2]


def src_sec_insiders(ctx):
    for filename, url in _sec_listed_zips(ctx, SEC_INSIDERS_PAGE, "sec-insiders"):
        ctx.take("sec-insiders", filename, url)


def src_sec_13f(ctx):
    for filename, url in _sec_listed_zips(ctx, SEC_13F_PAGE, "sec-13f"):
        ctx.take("sec-13f", filename, url)


def _src_sec_listed(source):
    page, name = SEC_LISTED[source]
    link = re.compile(r'href="(/files/[^"]+?/(%s))"' % name)

    def src(ctx):
        for filename, url in _sec_listed_zips(ctx, page, source, link):
            ctx.take(source, filename, url)
    return src


def src_fomc(ctx):
    try:
        html = ctx.client.text(FOMC_CAL).decode("utf-8", "replace")
    except Exception as e:
        ctx.stats["fail"] += 1
        print("  fomc calendar FAILED: %s" % e)
        return
    ctx.take("fomc", "fomccalendars.htm", FOMC_CAL)
    for path in sorted(set(FOMC_LINK.findall(html))):
        ctx.take("fomc", path.rsplit("/", 1)[-1], "https://www.federalreserve.gov" + path)


def src_fdic(ctx):
    for name in ("institutions", "summary"):
        offset, page = 0, 1
        while True:
            url = FDIC + "%s?format=csv&limit=%d&offset=%d" % (name, PAGE, offset)
            try:
                path, _ = ctx.client.get(url)
            except Exception as e:
                ctx.stats["fail"] += 1
                print("  fdic/%s page %d FAILED: %s" % (name, page, e))
                break
            with open(path, "rb") as f:
                lines = sum(1 for _ in f)
            os.remove(path)
            if lines <= 1:
                break
            ctx.take("fdic", "%s_p%03d.csv" % (name, page), url)
            if lines < PAGE:                       # header + fewer than a page: last one
                break
            offset, page = offset + PAGE, page + 1


SOURCES = {"fed": src_fed, "nyfed": src_nyfed, "treasury": src_treasury,
           "fiscal": src_fiscal, "bls": src_bls, "bea": src_bea, "cftc": src_cftc,
           "sec": src_sec, "sec-insiders": src_sec_insiders, "sec-13f": src_sec_13f,
           "fomc": src_fomc, "fdic": src_fdic}
SOURCES.update((s, _src_sec_listed(s)) for s in SEC_LISTED)


class Context(object):
    def __init__(self, client, store, day, stats, all_years, sec_quarters=None):
        self.client, self.store, self.day, self.stats = client, store, day, stats
        self.all_years = all_years
        self.sec_quarters = sec_quarters or []
        self.today = dt.date.fromisoformat(day)

    def take(self, source, filename, url, expect_absent=False):
        capture_file(source, filename, url, self.client, self.store, self.day,
                     self.stats, expect_absent)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources", help="comma-separated subset of: " + ",".join(SOURCES))
    p.add_argument("--all-years", action="store_true",
                   help="also fetch every historic year file (one-off backfill)")
    p.add_argument("--sec-quarters", help="explicit financial-statement quarters for the "
                   "sec source, e.g. 2025q3,2025q4 (default: the last two)")
    args = p.parse_args()
    sec_quarters = []
    for item in (args.sec_quarters or "").split(","):
        item = item.strip().lower()
        if item:
            m = re.match(r"^(\d{4})q([1-4])$", item)
            if not m:
                sys.exit("--sec-quarters wants YYYYqN items, got %r" % item)
            sec_quarters.append((int(m.group(1)), int(m.group(2))))
    wanted = list(SOURCES)
    if args.sources:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
        bad = [s for s in wanted if s not in SOURCES]
        if bad:
            sys.exit("unknown source(s): %s" % ", ".join(bad))

    ua = user_agent()
    store = make_store()
    store.acquire_lock()
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    stats = {"stored": 0, "unchanged": 0, "absent": 0, "fail": 0, "bytes": 0}
    try:
        ctx = Context(Client(ua), store, day, stats, args.all_years, sec_quarters)
        print("store: %s | snapshot %s | sources: %s%s"
              % (type(store).__name__, day, ",".join(wanted),
                 " | all years" if args.all_years else ""))
        for name in wanted:
            print("%s:" % name)
            try:
                SOURCES[name](ctx)
            except Exception as e:                  # a broken source never stops the rest
                stats["fail"] += 1
                print("  %s FAILED: %s" % (name, e))
        summary = ("us capture %s  stored:%d (%.1fMB raw)  unchanged:%d  absent:%d  failed:%d"
                   % (day, stats["stored"], stats["bytes"] / 1e6, stats["unchanged"],
                      stats["absent"], stats["fail"]))
        print(summary)
        heartbeat.ping(summary, failed=stats["fail"] > 0)
    finally:
        store.release_lock()


if __name__ == "__main__":
    main()
