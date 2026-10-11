#!/usr/bin/env python3
"""Offline, atomic import of verified Databento receipts into private Plover data.

No API credentials or network calls. A changed source creates a new immutable
version. An unchanged import grants the supplied accounts access without
duplicating bars. All validation completes before the database transaction.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import re
import shutil
import sqlite3
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import market_history as mh  # noqa: E402


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def child(root, name):
    p = (root / name).resolve()
    if not p.is_file() or root.resolve() not in p.parents:
        raise ValueError("Missing or unsafe source file: " + name)
    return p


def mapped(mapping, instrument, date):
    matches = {m["s"] for m in mapping.get(str(instrument), []) if m["d0"] <= date < m["d1"]}
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous contract mapping")
    return matches.pop()


def validate_product(root, product):
    slug = product["slug"]
    spec = mh.PRODUCTS[slug]
    if (product["dataset"], product["symbol"]) != (spec["dataset"], spec["symbol"]):
        raise ValueError("Product metadata does not match " + slug)
    files, records, pubs, missing, contracts = [], [], {}, {}, set()
    last_end = product["requested_start_utc"]
    receipts = product["original_receipts"]
    if not receipts:
        raise ValueError("No original receipts for " + slug)
    for name in receipts:
        receipt_path = child(root, name)
        receipt = json.loads(receipt_path.read_text())
        file = child(root, receipt["filename"])
        sha = digest(file)
        if sha != receipt["sha256"]:
            raise ValueError("Source checksum mismatch: " + file.name)
        req = receipt["request"]
        if (req["dataset"], req["symbols"], req["schema"], req["stype_in"]) != (
                spec["dataset"], spec["symbol"], "ohlcv-1h", "continuous"):
            raise ValueError("Unexpected request in " + name)
        if req["start"] != last_end or req["end"] <= req["start"]:
            raise ValueError("Noncontiguous source ranges")
        last_end = req["end"]
        maps = receipt["contract_mappings"]
        rolling = maps["rolling_to_id"]["result"][spec["symbol"]]
        raw = maps["id_to_raw_symbol"]["result"]
        count = 0
        with gzip.open(file, "rt", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["ts_event", "rtype", "publisher_id", "instrument_id",
                                     "open", "high", "low", "close", "volume"]:
                raise ValueError("Unexpected source columns")
            for row in reader:
                stamp = row["ts_event"]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00\.000000000Z", stamp):
                    raise ValueError("Non-hourly UTC timestamp")
                dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
                date = stamp[:10]
                if not req["start"] <= date < req["end"] or row["rtype"] != "34":
                    raise ValueError("Source row outside request or wrong schema")
                pub, instrument, volume = int(row["publisher_id"]), int(row["instrument_id"]), int(row["volume"])
                allowed = (57, 84) if slug == "brent" else (1,)
                if pub not in allowed or volume < 0:
                    raise ValueError("Unexpected publisher or negative volume")
                ids = {m["s"] for m in rolling if m["d0"] <= date < m["d1"]}
                if str(instrument) not in ids:
                    raise ValueError("Contract does not match the continuous roll")
                contract = mapped(raw, instrument, date)
                vals = [row[k] or None for k in ("open", "high", "low", "close")]
                nums = [Decimal(v) if v is not None else None for v in vals]
                if any(n is not None and not n.is_finite() for n in nums):
                    raise ValueError("Nonfinite price")
                if any(n is None for n in nums) and not all(n is None for n in nums):
                    raise ValueError("Partially missing OHLC")
                if all(n is not None for n in nums):
                    o, h, l, c = nums
                    if not l <= min(o, c) <= max(o, c) <= h:
                        raise ValueError("OHLC relationship failed")
                else:
                    missing[str(pub)] = missing.get(str(pub), 0) + 1
                pubs[str(pub)] = pubs.get(str(pub), 0) + 1
                # The inventory counts vendor instrument IDs. A raw symbol
                # may be reused under more than one vendor identifier.
                contracts.add(instrument)
                records.append((stamp[:19] + "Z", pub, instrument, contract, *vals, volume, sha))
                count += 1
        if count != receipt["rows"]:
            raise ValueError("Receipt row count mismatch")
        files.append((sha, file, receipt_path.read_text()))
    if len({r[:3] for r in records}) != len(records):
        raise ValueError("Duplicate hour/publisher/contract")
    if len(records) != product["rows"] or pubs != product["publisher_rows"] or missing != product["missing_ohlc_rows_by_publisher"]:
        raise ValueError("Inventory counts do not reconcile")
    if not records:
        raise ValueError("No source observations")
    first, last = min(r[0] for r in records), max(r[0] for r in records)
    if first[:19] != product["first_bar_utc"][:19] or last[:19] != product["last_bar_utc"][:19]:
        raise ValueError("Inventory timestamp bounds do not reconcile")
    if len(contracts) != product["contract_count"]:
        raise ValueError("Inventory contract count does not reconcile")
    revision = hashlib.sha256(json.dumps([f[0] for f in files]).encode()).hexdigest()
    meta = dict(product, first_bar_utc=first, last_bar_utc=last, schema="ohlcv-1h",
                prices="unadjusted", roll="previous-day volume")
    return slug, revision, meta, files, records, last_end


def ingest(source, owners, database=None):
    root = pathlib.Path(source).resolve()
    inventory = json.loads(child(root, "inventory.json").read_text())
    if inventory.get("all_downloads_complete") is not True or inventory.get("schema") != "ohlcv-1h":
        raise ValueError("Only completed hourly inventories can be imported")
    emails = sorted({str(e).strip().lower() for e in owners})
    if not emails or any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", e) for e in emails):
        raise ValueError("Supply valid account emails")
    products = inventory["products"]
    if len({p["slug"] for p in products}) != len(products):
        raise ValueError("Duplicate inventory products")
    accepted = [validate_product(root, p) for p in products]
    if any(p[-1] != inventory["end_exclusive_utc"] for p in accepted):
        raise ValueError("Inventory end does not reconcile")
    path = pathlib.Path(database or mh.DB_PATH)
    rawdir = path.parent / "raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    # Content-addressed originals and receipts survive removal of the download
    # folder. Existing archived bytes are checked, never overwritten.
    for _, _, _, files, _, _ in accepted:
        for sha, original, receipt in files:
            dst = rawdir / (sha + ".csv.gz")
            if dst.exists():
                if digest(dst) != sha:
                    raise ValueError("Archived checksum mismatch")
            else:
                temp = rawdir / (sha + ".partial")
                shutil.copyfile(original, temp)
                temp.replace(dst)
            rp = rawdir / (sha + ".receipt.json")
            if rp.exists() and rp.read_text() != receipt:
                raise ValueError("Archived receipt changed")
            if not rp.exists():
                rp.write_text(receipt)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(mh.SCHEMA)
    results = []
    try:
        with con:
            for slug, revision, meta, files, records, _ in accepted:
                old = con.execute("SELECT id FROM releases WHERE slug=? AND revision=?", (slug, revision)).fetchone()
                if old is None:
                    now = dt.datetime.now(dt.timezone.utc).isoformat()
                    rid = con.execute("INSERT INTO releases(slug,revision,imported_at,metadata) VALUES(?,?,?,?)",
                                      (slug, revision, now, json.dumps(meta))).lastrowid
                    for sha, file, receipt in files:
                        con.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?)", (sha, file.name, receipt))
                    con.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    [(rid,) + row for row in records])
                else:
                    rid = old[0]
                    n = con.execute("SELECT count(*) FROM bars WHERE release_id=?", (rid,)).fetchone()[0]
                    if n != len(records):
                        raise ValueError("Stored version row count changed")
                con.executemany("INSERT OR IGNORE INTO access VALUES(?,?)", [(slug, e) for e in emails])
                results.append({"slug": slug, "rows": len(records), "revision": revision,
                                "result": "already imported" if old else "imported"})
    finally:
        con.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/databento")
    parser.add_argument("--owner", action="append", required=True)
    parser.add_argument("--database", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(ingest(args.source, args.owner, args.database), indent=2))


if __name__ == "__main__":
    main()
