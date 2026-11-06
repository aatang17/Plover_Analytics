"""Country and network operator for an address, from the DB-IP Lite tables.

Used by app/visits.py to answer "where are readers, and what kind of network
are they on" — the second being the more useful of the two for this product:
a read from a bank's corporate network means something a country code does
not.

The address is still never stored. A country code and a network name are
derived while the request is in flight and the address is discarded with the
rest of the scope, exactly as before.

Loading is deliberately timid. The tables are fetched once a month into
data/geo/, parsed on a background thread after the port is already open, and
published only when complete — so a request that arrives early, or on a boot
with no route to DB-IP, is recorded with no country rather than delayed or
failed. Nothing here can raise into a response.

Ranges are held as `array` integers rather than Python objects: the same data
as lists of ints and tuples costs about 180 MB, which is not a reasonable
thing to hold in a serving process for an analytics nicety. As arrays it is
about 25 MB.

IPv6 ranges are indexed on their top 64 bits. Allocations are /32 to /64 in
practice, so this resolves every realistic case; a range narrower than a /64
attributes its whole /64, which is immaterial at the resolution this feeds.

Data: DB-IP Lite, CC BY 4.0. Attribution is shown wherever the results are
(the Traffic page and its exports) — see https://db-ip.com.

Apple's iCloud Private Relay hands Safari readers an address belonging to
Cloudflare, Akamai or Fastly, which the traffic test reads as a data centre.
Apple publishes the addresses it uses, with the country each one serves
(RELAY_URL); an address on that list is reported as RELAY_NETWORK in that
country instead. Fetched weekly, loaded beside the DB-IP tables, and like
them never a reason for a request to wait or fail.
"""
import array
import bisect
import csv
import datetime
import gzip
import io
import ipaddress
import os
import threading
import time
import urllib.request

from . import db

GEO_DIR = db.DATA_DIR / "geo"
SOURCE = "DB-IP Lite (CC BY 4.0) — https://db-ip.com"
COUNTRY_URL = "https://download.db-ip.com/free/dbip-country-lite-%s.csv.gz"
ASN_URL = "https://download.db-ip.com/free/dbip-asn-lite-%s.csv.gz"

RELAY_URL = "https://mask-api.icloud.com/egress-ip-ranges.csv"
RELAY_NETWORK = "iCloud Private Relay"

DOWNLOAD_TIMEOUT = 120
# Yield to the event loop every so many rows: parsing 1.2m ranges is seconds
# of tight Python, and the serving thread must not queue behind it.
YIELD_EVERY = 20000

# DB-IP marks unallocated and reserved space ZZ; that is not a place.
NOT_A_COUNTRY = ("ZZ", "")

_LOCK = threading.Lock()
_STATE = {"tables": None, "edition": None, "started": False,
          "error": None, "ranges": 0, "relay": None, "relay_edition": None,
          "relay_error": None}


class _Ranges(object):
    """Sorted IP ranges mapped to a small table of distinct values."""

    __slots__ = ("v4_start", "v4_end", "v4_id", "v6_start", "v6_end", "v6_id",
                 "values")

    def __init__(self):
        self.v4_start = array.array("I")
        self.v4_end = array.array("I")
        self.v4_id = array.array("I")
        self.v6_start = array.array("Q")
        self.v6_end = array.array("Q")
        self.v6_id = array.array("I")
        self.values = []

    def __len__(self):
        return len(self.v4_start) + len(self.v6_start)

    def find(self, address):
        if address.version == 4:
            starts, ends, ids = self.v4_start, self.v4_end, self.v4_id
            key = int(address)
        else:
            starts, ends, ids = self.v6_start, self.v6_end, self.v6_id
            key = int(address) >> 64
        if not starts:
            return None
        # The last range beginning at or before the address; it matches only
        # if the address is also within its end.
        i = bisect.bisect_right(starts, key) - 1
        if i < 0 or key > ends[i]:
            return None
        return self.values[ids[i]]


def _parse(raw, value_of):
    """Build a range table from one DB-IP CSV. `value_of` turns a row into the
    string to store, or None to drop the row."""
    table = _Ranges()
    index = {}
    for n, row in enumerate(csv.reader(io.TextIOWrapper(
            gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8",
            errors="replace"))):
        if n % YIELD_EVERY == 0:
            time.sleep(0)  # hand the interpreter back to the serving thread
        if len(row) < 3:
            continue
        value = value_of(row)
        if value is None:
            continue
        try:
            low = ipaddress.ip_address(row[0])
            high = ipaddress.ip_address(row[1])
        except ValueError:
            continue
        if low.version != high.version:
            continue
        ident = index.get(value)
        if ident is None:
            ident = len(table.values)
            index[value] = ident
            table.values.append(value)
        if low.version == 4:
            table.v4_start.append(int(low))
            table.v4_end.append(int(high))
            table.v4_id.append(ident)
        else:
            table.v6_start.append(int(low) >> 64)
            table.v6_end.append(int(high) >> 64)
            table.v6_id.append(ident)
    return table


def _editions():
    """This month first, then last month: the new file appears a few days into
    the month, and asking for it early is a 404, not an error worth failing on."""
    today = datetime.date.today()
    first = today.replace(day=1)
    previous = (first - datetime.timedelta(days=1)).replace(day=1)
    return [first.strftime("%Y-%m"), previous.strftime("%Y-%m")]


def _cached_path(kind, edition):
    return GEO_DIR / ("dbip-%s-%s.csv.gz" % (kind, edition))


def _fetch(kind, url, edition):
    """The month's file, from the volume if it is already there."""
    path = _cached_path(kind, edition)
    if path.exists():
        with open(str(path), "rb") as f:
            return f.read()
    request = urllib.request.Request(
        url % edition, headers={"User-Agent": "plover-analytics/1.0"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        raw = response.read()
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".part"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, str(path))
    return raw


def _prune(keep):
    """Only the edition in use is worth the volume space."""
    try:
        for path in GEO_DIR.glob("dbip-*.csv.gz"):
            if keep not in path.name:
                path.unlink()
    except OSError:
        pass


def _country_of(row):
    code = (row[2] or "").strip().upper()
    return None if code in NOT_A_COUNTRY else code


def _network_of(row):
    name = (row[3] or "").strip() if len(row) > 3 else ""
    return name or None


def _build():
    last_error = None
    for edition in _editions():
        try:
            country = _parse(_fetch("country", COUNTRY_URL, edition), _country_of)
            asn = _parse(_fetch("asn", ASN_URL, edition), _network_of)
        except Exception as exc:  # noqa: BLE001 — geo is never worth a failure
            last_error = "%s: %s" % (edition, exc)
            continue
        with _LOCK:
            _STATE["tables"] = (country, asn)
            _STATE["edition"] = edition
            _STATE["ranges"] = len(country) + len(asn)
            _STATE["error"] = None
        _prune(edition)
        print("geoip: %s loaded (%d ranges)" % (edition, len(country) + len(asn)))
        return
    with _LOCK:
        _STATE["error"] = last_error or "no edition could be read"
    print("GEOIP UNAVAILABLE (%s): traffic will be counted without a location"
          % _STATE["error"])


def _relay_edition():
    """The ISO week: Apple changes its list often, and a week-old copy is
    close enough for telling readers from data centres."""
    year, week, _ = datetime.date.today().isocalendar()
    return "%d-W%02d" % (year, week)


def _relay_path(edition):
    return GEO_DIR / ("icloud-relay-%s.csv" % edition)


def _parse_relay(raw):
    """Apple's CSV, one range a line: `prefix,country,region,city,`. Sorted
    here, since bisect needs it and the file promises no order."""
    v4, v6 = [], []
    for n, line in enumerate(raw.decode("utf-8", "replace").splitlines()):
        if n % YIELD_EVERY == 0:
            time.sleep(0)
        parts = line.split(",")
        try:
            network = ipaddress.ip_network(parts[0].strip(), strict=False)
        except ValueError:
            continue
        # Blank, not None: find() returns None only for "not on the list".
        country = parts[1].strip().upper() if len(parts) > 1 else ""
        low, high = int(network.network_address), int(network.broadcast_address)
        if network.version == 4:
            v4.append((low, high, country))
        else:
            v6.append((low >> 64, high >> 64, country))
    table = _Ranges()
    index = {}
    for rows, starts, ends, ids in ((v4, table.v4_start, table.v4_end, table.v4_id),
                                    (v6, table.v6_start, table.v6_end, table.v6_id)):
        rows.sort()
        for low, high, country in rows:
            ident = index.get(country)
            if ident is None:
                ident = index[country] = len(table.values)
                table.values.append(country)
            starts.append(low)
            ends.append(high)
            ids.append(ident)
    return table


def _build_relay():
    edition = _relay_edition()
    path = _relay_path(edition)
    try:
        if path.exists():
            with open(str(path), "rb") as f:
                raw = f.read()
        else:
            try:
                request = urllib.request.Request(
                    RELAY_URL, headers={"User-Agent": "plover-analytics/1.0"})
                with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                    raw = response.read()
                GEO_DIR.mkdir(parents=True, exist_ok=True)
                tmp = str(path) + ".part"
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, str(path))
            except Exception:
                # Apple unreachable: last week's copy is far better than none.
                older = sorted(GEO_DIR.glob("icloud-relay-*.csv"))
                if not older:
                    raise
                path, edition = older[-1], older[-1].stem[len("icloud-relay-"):]
                with open(str(path), "rb") as f:
                    raw = f.read()
        table = _parse_relay(raw)
    except Exception as exc:  # noqa: BLE001 — the relay list is never worth a failure
        with _LOCK:
            _STATE["relay_error"] = str(exc)
        print("ICLOUD RELAY LIST UNAVAILABLE (%s): Private Relay readers will "
              "be taken for data centres" % exc)
        return
    with _LOCK:
        _STATE["relay"] = table
        _STATE["relay_edition"] = edition
        _STATE["relay_error"] = None
    try:
        for old in GEO_DIR.glob("icloud-relay-*.csv"):
            if old != path:
                old.unlink()
    except OSError:
        pass
    print("geoip: iCloud Private Relay %s loaded (%d ranges)" % (edition, len(table)))


def prepare():
    """Start loading, once per process, off the serving thread."""
    with _LOCK:
        if _STATE["started"]:
            return
        _STATE["started"] = True
    thread = threading.Thread(target=_build, name="geoip-load", daemon=True)
    thread.start()
    threading.Thread(target=_build_relay, name="geoip-relay", daemon=True).start()


def lookup(ip):
    """(country, network) for an address — (None, None) until the tables are
    loaded, for a private address, or for anything not in them."""
    with _LOCK:
        tables, relay = _STATE["tables"], _STATE["relay"]
    if tables is None or not ip:
        return None, None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None, None
    if (address.is_private or address.is_loopback or address.is_reserved
            or address.is_link_local):
        return None, None
    country, asn = tables
    if relay is not None:
        served = relay.find(address)
        if served is not None:
            return served or country.find(address), RELAY_NETWORK
    return country.find(address), asn.find(address)


def status():
    with _LOCK:
        return {"loaded": _STATE["tables"] is not None,
                "edition": _STATE["edition"],
                "ranges": _STATE["ranges"],
                "error": _STATE["error"],
                "source": SOURCE,
                "relay_edition": _STATE["relay_edition"],
                "relay_ranges": len(_STATE["relay"]) if _STATE["relay"] is not None else 0,
                "relay_error": _STATE["relay_error"]}
