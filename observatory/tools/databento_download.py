#!/usr/bin/env python3
"""Quote first; download futures bars only after explicit local confirmation.

Python 3.9+, standard library only. Credentials come from observatory/.env.
No account keys, HTTP headers, or response error bodies are logged.
"""
import argparse
import base64
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / ".env"
OUTPUT = ROOT / "data" / "databento"
GATEWAY = "https://hist.databento.com/v0/"
PRODUCTS = (
    ("brent", "ICE Brent", "IFEU.IMPACT", "BRN"),
    ("nikkei-yen", "CME Nikkei 225 (yen)", "GLBX.MDP3", "NIY"),
    ("nasdaq-100", "CME E-mini Nasdaq-100", "GLBX.MDP3", "NQ"),
)
ROLLS = {"calendar": "c", "volume": "v", "open-interest": "n"}


class DownloadError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    # Do not forward the authentication header to a redirected destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_key(path=SETTINGS):
    if not path.exists():
        raise DownloadError("Add DATABENTO_API_KEY to observatory/.env privately.")
    keys = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = raw.strip().partition("=")
        if separator and name.strip() == "DATABENTO_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            keys.append(value)
    if len(keys) != 1 or not re.fullmatch(r"db-[A-Za-z0-9_-]{29}", keys[0]):
        raise DownloadError("Put one valid DATABENTO_API_KEY entry in observatory/.env; never paste it in chat.")
    return keys[0]


class Client:
    def __init__(self, key):
        self._auth = "Basic " + base64.b64encode((key + ":").encode()).decode()
        self._opener = build_opener(NoRedirects())

    def open(self, endpoint, params, post=False):
        encoded = urlencode(params).encode("ascii")
        url = GATEWAY + endpoint
        headers = {"Authorization": self._auth, "User-Agent": "plover-databento-download/1"}
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            request = Request(url, data=encoded, headers=headers, method="POST")
        else:
            request = Request(url + "?" + encoded.decode(), headers=headers)
        try:
            return self._opener.open(request, timeout=120)
        except HTTPError as error:
            messages = {401: "Check the key privately.", 403: "Check dataset access and licensing in the portal.",
                        422: "Check the symbols, date range, and schema.", 429: "Wait before trying again."}
            raise DownloadError("Databento HTTP %d. %s No automatic retry was made."
                                % (error.code, messages.get(error.code, "Check Databento's service status."))) from None
        except (URLError, OSError):
            raise DownloadError("Databento connection failed. No automatic retry was made.") from None

    def metadata(self, endpoint, params):
        post = endpoint in {"metadata.get_cost", "metadata.get_record_count",
                            "metadata.get_billable_size", "symbology.resolve"}
        with self.open(endpoint, params, post=post) as response:
            try:
                return json.load(response)
            except (ValueError, UnicodeError):
                raise DownloadError("Databento returned unexpected metadata.") from None


def make_plan(start, end, scope, roll, selected, schema="ohlcv-1h"):
    plan = []
    for slug, label, dataset, root in PRODUCTS:
        if slug not in selected:
            continue
        symbol = root + (".FUT" if scope == "all" else ".%s.0" % ROLLS[roll])
        cursor = start
        while cursor < end:
            stop = min(date(cursor.year + 1, 1, 1), end)
            params = {"dataset": dataset, "schema": schema, "symbols": symbol,
                      "stype_in": "parent" if scope == "all" else "continuous",
                      "start": cursor.isoformat(), "end": stop.isoformat()}
            suffix = "" if schema == "ohlcv-1m" else "_" + schema
            filename = "%s_%s_%s_%s%s.csv.gz" % (slug, symbol, cursor, stop, suffix)
            plan.append({"product": label, "filename": filename, "request": params})
            cursor = stop
    return plan


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def completed(output, item):
    path = output / item["filename"]
    receipt = path.with_name(path.name + ".json")
    if path.with_name(path.name + ".partial").exists():
        raise DownloadError("An interrupted download needs review: %s.partial. It may already have incurred charges."
                            % item["filename"])
    if not path.exists() and not receipt.exists():
        return False
    if path.exists() and receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved.get("request") == item["request"] and saved.get("sha256") == sha256(path):
            return True
    raise DownloadError("Existing output failed its receipt/checksum check: " + item["filename"])


def quote(client, plan):
    for dataset, schema in sorted({(item["request"]["dataset"], item["request"]["schema"])
                                   for item in plan}):
        available = client.metadata("metadata.get_dataset_range", {"dataset": dataset})
        span = available.get("schema", {}).get(schema)
        if not span:
            raise DownloadError("%s coverage unavailable for %s" % (schema, dataset))
        for item in plan:
            request = item["request"]
            if request["dataset"] == dataset and request["schema"] == schema:
                # Compare UTC dates with nanosecond timestamps without truncating coverage.
                begin = request["start"] + "T00:00:00.000000000Z"
                stop = request["end"] + "T00:00:00.000000000Z"
                if begin < span["start"] or stop > span["end"]:
                    raise DownloadError("Requested dates exceed %s coverage for %s: %s to %s."
                                        % (schema, dataset, span["start"], span["end"]))
    total = Decimal("0")
    totals = {}
    for item in plan:
        request = item["request"]
        amount = Decimal(str(client.metadata("metadata.get_cost", request)))
        if not amount.is_finite() or amount < 0:
            raise DownloadError("Databento returned an invalid cost estimate.")
        item["estimated_usd"] = str(amount)
        if request["stype_in"] == "continuous":
            mapping_params = {"dataset": request["dataset"], "symbols": request["symbols"],
                              "stype_in": "continuous", "stype_out": "instrument_id",
                              "start_date": request["start"], "end_date": request["end"]}
            mappings = client.metadata("symbology.resolve", mapping_params)
            intervals = mappings.get("result", {}).get(request["symbols"], [])
            if not intervals or mappings.get("not_found"):
                raise DownloadError("Rolling contract could not be resolved for " + request["symbols"])
            ids = sorted({str(row["s"]) for row in intervals})
            names = client.metadata("symbology.resolve", dict(mapping_params,
                                    symbols=",".join(ids), stype_in="instrument_id",
                                    stype_out="raw_symbol"))
            item["contract_mappings"] = {"rolling_to_id": mappings, "id_to_raw_symbol": names}
        totals[item["product"]] = totals.get(item["product"], Decimal("0")) + amount
        total += amount
    for label, amount in totals.items():
        print("%s: US$%.4f" % (label, amount))
    print("TOTAL ESTIMATE: US$%.4f" % total)
    print("Credits are not deducted from this estimate; verify your remaining balance in Billing.")
    return total


def download(client, output, item):
    path = output / item["filename"]
    partial = path.with_name(path.name + ".partial")
    params = dict(item["request"], encoding="csv", compression="none", pretty_px="true",
                  pretty_ts="true", map_symbols="false", stype_out="instrument_id")
    # Reserve before a billable request; interruption blocks accidental repeat billing.
    with partial.open("xb") as reserved:
        with client.open("timeseries.get_range", params, post=True) as response:
            header = response.readline(8192)
            columns = next(csv.reader([header.decode("utf-8")]))
            if not {"ts_event", "instrument_id", "publisher_id", "open", "high", "low",
                    "close", "volume"}.issubset(columns):
                raise DownloadError("Unexpected CSV header; review the partial file before retrying.")
            size, lines, last = len(header), 0, b""
            with gzip.GzipFile(fileobj=reserved, mode="wb", mtime=0) as archive:
                archive.write(header)
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    archive.write(block)
                    size += len(block)
                    lines += block.count(b"\n")
                    last = block[-1:]
                if last and last != b"\n":
                    lines += 1
    if not lines:
        raise DownloadError("No bars returned; review the partial file. It was not marked complete.")
    partial.rename(path)
    receipt = dict(item, sha256=sha256(path), rows=lines, csv_bytes=size,
                   downloaded_at=datetime.now(timezone.utc).isoformat())
    with path.with_name(path.name + ".json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    print("Saved %s (%d bars)" % (path.name, lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2019, 10, 11))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 10),
                        help="Exclusive UTC end date; default includes all of 9 October 2026.")
    parser.add_argument("--schema", choices=("ohlcv-1h", "ohlcv-1m"), default="ohlcv-1h",
                        help="Hourly bars for the initial trial; choose ohlcv-1m for minute bars.")
    parser.add_argument("--scope", choices=("all", "continuous"), default="continuous",
                        help="all = every expiry and futures spread; continuous = one rolling outright.")
    parser.add_argument("--roll", choices=tuple(ROLLS), default="volume")
    parser.add_argument("--products", nargs="+", choices=[p[0] for p in PRODUCTS],
                        default=[p[0] for p in PRODUCTS])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--plan", action="store_true", help="Show requests without credentials or network access.")
    parser.add_argument("--download", action="store_true", help="Quote, then ask for local purchase confirmation.")
    parser.add_argument("--max-cost-usd", type=Decimal, help="Maximum acceptable estimate, required for download.")
    args = parser.parse_args(argv)
    if args.start >= args.end:
        parser.error("--start must be earlier than --end")
    if args.max_cost_usd is not None and (not args.max_cost_usd.is_finite() or args.max_cost_usd < 0):
        parser.error("--max-cost-usd must be a finite, non-negative dollar amount")
    if args.download and (args.max_cost_usd is None or args.plan):
        parser.error("Downloading requires --max-cost-usd and cannot be combined with --plan")
    plan = make_plan(args.start, args.end, args.scope, args.roll, args.products, args.schema)
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0
    print("UTC range: %s inclusive to %s exclusive; scope: %s" % (args.start, args.end, args.scope))
    print("Bar schema: %s" % args.schema)
    if args.scope == "all":
        print("Includes all expiries and futures spreads. Contracts stay separate.")
    else:
        print("Roll rule: %s. Prices are unadjusted; contract changes can cause jumps." % args.roll)
    pending = [item for item in plan if not completed(args.output, item)]
    if not pending:
        print("All files are complete and checksums match; no requests made.")
        return 0
    client = Client(read_key())
    total = quote(client, pending)
    if not args.download:
        print("Estimate only. No paid data requested.")
        return 0
    if total > args.max_cost_usd:
        raise DownloadError("Estimate exceeds --max-cost-usd. No paid data requested.")
    print("Actual charges depend on delivered data. Use portal usage limits for an account spending limit.")
    if not sys.stdin.isatty():
        raise DownloadError("Download requires confirmation in an interactive terminal. No paid data requested.")
    if input("Type DOWNLOAD to approve US$%.4f estimated for the files above: " % total) != "DOWNLOAD":
        print("Cancelled. No paid data requested.")
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    for item in pending:
        if not completed(args.output, item):
            print("Downloading %s ..." % item["filename"], flush=True)
            download(client, args.output, item)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("Stopped. Review any partial file before retrying; charges may already have occurred.")
    except DownloadError as error:
        sys.exit(str(error))
    except Exception as error:
        # An upstream error could contain authentication material; never print its text.
        sys.exit("Operation failed (%s). Review local output before retrying; no automatic retry was made."
                 % type(error).__name__)
