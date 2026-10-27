# -*- coding: utf-8 -*-
"""Copies of the research desk and the staff accounts, off the volume.

Everything else on the volume can be rebuilt from its source — a dataset is
downloaded again, an extraction re-run. Articles and images cannot: the
volume holds the only copy of what the writers wrote. So once a day, and a
minute after every publication, this makes a consistent copy of the SQLite
files (SQLite's own backup API, safe while the server is writing) and:

* also copies the admin console's hand curation (data/admin/us_companies.json, jp_companies.json,
  logos included, and data/admin/parties.json);
* keeps the last 14 daily copies on the volume, under data/research/backups/,
  which covers a damaged database file;
* uploads them, and every image not yet uploaded, to the object store the
  EDINET archive already uses (``EDINET_S3_*``), under ``plover-backups/``,
  which covers losing the volume itself.

Without the object-store settings the local copies are still made and the
status says the off-site half is not configured. A failure is logged and
recorded in ``backup-status.json``, which the admin console shows; it never
raises into the server.
"""
import datetime
import json
import logging
import os
import shutil
import sqlite3
import threading
import time

from . import db, research, staff

log = logging.getLogger(__name__)

PREFIX = "plover-backups/"
ADMIN_DIR = db.DATA_DIR / "admin"
ADMIN_FILES = ("us_companies.json", "jp_companies.json", "parties.json")
KEEP_LOCAL = 14
INTERVAL_SECONDS = 24 * 3600
_run_lock = threading.Lock()
_pending = {"timer": None}


def status_path():
    return research.DATA_DIR / "backup-status.json"


def read_status():
    try:
        with open(str(status_path()), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_run": None}


def _write_status(status):
    try:
        research.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = status_path().with_suffix(".tmp")
        with open(str(tmp), "w", encoding="utf-8") as f:
            json.dump(status, f, indent=1)
        os.replace(str(tmp), str(status_path()))
    except OSError:
        log.warning("could not write backup status", exc_info=True)


def _copy_sqlite(src, dst):
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(tmp))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.replace(str(tmp), str(dst))
    return True


def offsite_enabled():
    """OFFSITE_BACKUPS=0 stops every upload to plover-backups/ (this module and
    app/data_backup.py). A rehearsal copy of production sets it: its older
    copies must never land on top of production's."""
    return os.environ.get("OFFSITE_BACKUPS", "1").strip().lower() not in ("0", "false", "no", "off", "")


def _s3():
    needed = ("EDINET_S3_BUCKET", "EDINET_S3_ENDPOINT", "EDINET_S3_KEY_ID", "EDINET_S3_SECRET")
    if not offsite_enabled() or not all(os.environ.get(k) for k in needed):
        return None, None
    import boto3
    client = boto3.client("s3", endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
                          aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
                          aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
                          region_name=os.environ.get("EDINET_S3_REGION", "auto"))
    return client, os.environ["EDINET_S3_BUCKET"]


def run():
    """One backup. Returns the status dict it recorded."""
    with _run_lock:
        started = time.time()
        day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        status = {"last_run": datetime.datetime.utcnow().isoformat() + "Z", "ok": False,
                  "local": None, "offsite": None, "error": None}
        try:
            folder = research.DATA_DIR / "backups" / day
            copied = []
            if _copy_sqlite(research.db_path(), folder / "research.db"):
                copied.append("research.db")
            if _copy_sqlite(staff.DB_PATH, folder / "staff.db"):
                copied.append("staff.db")
            # Hand curation kept as JSON under data/admin/: the US company
            # edits (with their uploaded logos) and the party profiles.
            for name in ADMIN_FILES:
                src = ADMIN_DIR / name
                if src.exists():
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(folder / name))
                    copied.append(name)
            status["local"] = {"folder": str(folder), "files": copied}
            days = sorted(p for p in (research.DATA_DIR / "backups").iterdir() if p.is_dir())
            for old in days[:-KEEP_LOCAL]:
                for f in old.iterdir():
                    f.unlink()
                old.rmdir()

            client, bucket = _s3()
            if client is None:
                status["offsite"] = {"configured": False}
            else:
                uploaded = []
                for name in copied:
                    key = PREFIX + "research/%s/%s" % (day, name)
                    client.upload_file(str(folder / name), bucket, key)
                    client.upload_file(str(folder / name), bucket,
                                       PREFIX + "research/latest/" + name)
                    uploaded.append(key)
                have = set()
                token = None
                while True:
                    kw = {"Bucket": bucket, "Prefix": PREFIX + "research/media/"}
                    if token:
                        kw["ContinuationToken"] = token
                    page = client.list_objects_v2(**kw)
                    for obj in page.get("Contents", []):
                        have.add(obj["Key"].rsplit("/", 1)[-1])
                    if not page.get("IsTruncated"):
                        break
                    token = page.get("NextContinuationToken")
                media = 0
                if research.media_dir().exists():
                    for f in research.media_dir().iterdir():
                        if f.suffix == ".tmp" or f.name in have:
                            continue
                        client.upload_file(str(f), bucket, PREFIX + "research/media/" + f.name)
                        media += 1
                status["offsite"] = {"configured": True, "bucket": bucket,
                                     "files": uploaded, "new_images": media}
            status["ok"] = True
        except Exception as exc:  # noqa: BLE001 — a backup never takes the server down
            log.exception("research backup failed")
            status["error"] = "%s: %s" % (type(exc).__name__, exc)
        status["seconds"] = round(time.time() - started, 1)
        _write_status(status)
        return status


def soon(delay=60):
    """Back up shortly, coalescing a burst of publications into one run."""
    t = _pending.get("timer")
    if t is not None and t.is_alive():
        return
    timer = threading.Timer(delay, run)
    timer.daemon = True
    _pending["timer"] = timer
    timer.start()


async def loop():
    """Daily, from ten minutes after boot. Started from main.lifespan."""
    import asyncio
    await asyncio.sleep(600)
    while True:
        await asyncio.get_event_loop().run_in_executor(None, run)
        await asyncio.sleep(INTERVAL_SECONDS)
