"""Private, archived hourly futures. Serving only reads this SQLite database.

This namespace is separate from the DATE-based public macro adapters. Decimal
prices, UTC hours, publishers, contract mappings and immutable source receipts
are retained. Permissions follow the signed-in person's email, never a query
parameter or the shared administrator password.
"""
import csv
import datetime as dt
import io
import json
import os
import pathlib
import re
import sqlite3
from contextlib import closing

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = pathlib.Path(os.environ.get("MARKET_HISTORY_DB") or
                       str(ROOT / "data/market-history/history.sqlite3"))
SOURCE_URL = "https://databento.com/docs/schemas-and-data-formats/ohlcv"
CSV_CHUNK_BYTES = 65536
PRODUCTS = {
    "brent": {"name": "ICE Brent Futures", "unit": "USD/barrel", "publisher": 57,
              "dataset": "IFEU.IMPACT", "symbol": "BRN.v.0",
              "terms": "brent crude oil ice brn futures databento"},
    "nikkei-yen": {"name": "CME Nikkei 225 Futures (Yen)", "unit": "Index points", "publisher": 1,
                   "dataset": "GLBX.MDP3", "symbol": "NIY.v.0",
                   "terms": "nikkei japan yen cme niy futures databento"},
    "nasdaq-100": {"name": "CME E-mini Nasdaq-100 Futures", "unit": "Index points", "publisher": 1,
                   "dataset": "GLBX.MDP3", "symbol": "NQ.v.0",
                   "terms": "nasdaq nasdaq100 nq e-mini cme futures databento"},
}
PUBLISHERS = {1: "CME Globex", 57: "ICE Exchange Trades", 84: "ICE Off-Market Trades"}
NOTE = ("Hourly UTC bars; previous-day volume roll; unadjusted futures prices. "
        "Contract changes can cause price jumps. Missing bars are not zero. "
        "Saved history; automatic updates are not enabled.")
SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL, revision TEXT NOT NULL,
 imported_at TEXT NOT NULL, metadata TEXT NOT NULL, UNIQUE(slug,revision));
CREATE TABLE IF NOT EXISTS artifacts (
 sha TEXT PRIMARY KEY, filename TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bars (
 release_id INTEGER NOT NULL REFERENCES releases(id), ts TEXT NOT NULL,
 publisher INTEGER NOT NULL, instrument INTEGER NOT NULL, contract TEXT NOT NULL,
 open TEXT, high TEXT, low TEXT, close TEXT, volume INTEGER NOT NULL,
 artifact_sha TEXT NOT NULL REFERENCES artifacts(sha),
 PRIMARY KEY(release_id,ts,publisher,instrument));
CREATE TABLE IF NOT EXISTS access (
 slug TEXT NOT NULL, email TEXT NOT NULL, PRIMARY KEY(slug,email));
"""


class HistoryError(ValueError):
    pass


def reader():
    # StreamingResponse advances a synchronous iterator on different workers.
    # Each connection belongs to one request and next() is serialized, so
    # allowing worker changes does not share a cursor concurrently.
    con = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _email(person):
    return str((person or {}).get("email") or "").strip().lower() if not (person or {}).get("shared") else ""


def catalog(person, query=""):
    if not _email(person) or not DB_PATH.exists():
        return []
    words = re.findall(r"[\w-]+", str(query).lower())
    with closing(reader()) as con:
        rows = con.execute("SELECT r.* FROM releases r JOIN access a ON a.slug=r.slug "
                           "WHERE a.email=? AND r.id=(SELECT max(x.id) FROM releases x WHERE x.slug=r.slug) "
                           "ORDER BY r.slug", (_email(person),)).fetchall()
    out = []
    for row in rows:
        spec = PRODUCTS[row["slug"]]
        if not all(w in spec["terms"] + " " + row["slug"] for w in words):
            continue
        meta = json.loads(row["metadata"])
        out.append(dict(meta, slug=row["slug"], name=spec["name"], unit=spec["unit"],
                        default_publisher=spec["publisher"], revision=row["revision"],
                        imported_at=row["imported_at"], release_id=row["id"],
                        source="Databento", source_url=SOURCE_URL, frequency="hourly", note=NOTE))
    return out


def _selection(person, slug, publisher=None, start="", end="", revision=""):
    card = next((c for c in catalog(person) if c["slug"] == slug), None)
    if card is None:
        raise HistoryError("This saved dataset is not available to your account.")
    if revision and revision != card["revision"]:
        with closing(reader()) as con:
            row = con.execute("SELECT * FROM releases WHERE slug=? AND revision=?", (slug, revision)).fetchone()
        if row is None:
            raise HistoryError("That saved data version is unavailable.")
        card.update(json.loads(row["metadata"]), release_id=row["id"], revision=revision,
                    imported_at=row["imported_at"])
    try:
        pub = int(publisher) if publisher is not None else card["default_publisher"]
    except (TypeError, ValueError):
        raise HistoryError("Choose one of this dataset's trade feeds.")
    if str(pub) not in card["publisher_rows"]:
        raise HistoryError("Choose one of this dataset's trade feeds.")
    def day(value, fallback):
        if not value:
            return fallback
        try:
            return dt.datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            raise HistoryError("Use dates in YYYY-MM-DD format.")
    lo = day(start, card["first_bar_utc"][:10])
    hi = day(end, card["last_bar_utc"][:10])
    if lo > hi:
        raise HistoryError("The end date must be on or after the start date.")
    until = (dt.date.fromisoformat(hi) + dt.timedelta(days=1)).isoformat()
    return card, pub, lo, hi, (card["release_id"], pub, lo, until)


_SELECT = ("SELECT ts,publisher AS publisher_id,instrument AS instrument_id,contract,"
           "open,high,low,close,volume,artifact_sha FROM bars "
           "WHERE release_id=? AND publisher=? AND ts>=? AND ts<? ORDER BY ts,instrument")


def history(person, slug, start="", end="", publisher=None, limit=250, offset=0, revision=""):
    card, pub, lo, hi, params = _selection(person, slug, publisher, start, end, revision)
    try:
        limit, offset = int(limit), int(offset)
    except (ValueError, TypeError):
        raise HistoryError("Use whole numbers for the page size and offset.")
    if not 1 <= limit <= 1000 or offset < 0:
        raise HistoryError("Choose a page size between 1 and 1,000 and a nonnegative offset.")
    with closing(reader()) as con:
        rows = [dict(r) for r in con.execute(_SELECT + " LIMIT ? OFFSET ?", params + (limit + 1, offset))]
    more = len(rows) > limit
    return {"dataset": card, "publisher": PUBLISHERS[pub], "start": lo, "end": hi,
            "rows": rows[:limit], "next_offset": offset + limit if more else None}


def chart(person, slug, start="", end="", publisher=None, field="close", revision=""):
    if field not in ("open", "high", "low", "close", "volume"):
        raise HistoryError("Choose Open, High, Low, Close or Volume.")
    card, pub, lo, hi, params = _selection(person, slug, publisher, start, end, revision)
    # One click uses the last 30 calendar days of saved history, not today's
    # empty window. Full history remains available through paginated data/CSV.
    if not start:
        lo = max(lo, (dt.date.fromisoformat(hi) - dt.timedelta(days=29)).isoformat())
        params = (params[0], params[1], lo, params[3])
    days = (dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days + 1
    if days > 120:
        raise HistoryError("Hourly charts show up to 120 days. Choose a shorter range; CSV has the full history.")
    with closing(reader()) as con:
        data = [dict(r) for r in con.execute(_SELECT, params)]
    if not data:
        raise HistoryError("No bars in that range. Choose dates within the saved history.")
    by_hour = {}
    for r in data:
        if r["ts"] in by_hour:
            raise HistoryError("More than one contract in an hour. Export the contract-level CSV to inspect it.")
        by_hour[r["ts"]] = r
    unit = "Contracts" if field == "volume" else card["unit"]
    rows = [["Hour (UTC)", field.title()]]
    cursor = dt.datetime.strptime(lo, "%Y-%m-%d")
    stop = dt.datetime.strptime(hi, "%Y-%m-%d") + dt.timedelta(days=1)
    previous_contract = None
    while cursor < stop:
        stamp = cursor.strftime("%Y-%m-%dT%H:00:00Z")
        r = by_hour.get(stamp)
        # A blank at a roll prevents a line implying continuous, adjusted
        # prices across two different contracts. Raw export keeps every bar.
        if r and previous_contract and previous_contract != r["contract"]:
            rows.append([stamp, ""])
        rows.append([stamp, "" if not r or r[field] is None else str(r[field])])
        if r:
            previous_contract = r["contract"]
        cursor += dt.timedelta(hours=1)
    if len(rows) > 3000:
        raise HistoryError("Too many chart rows. Choose a shorter range.")
    src = "Databento · %s · %s · %s · version %s · %s to %s UTC" % (
        card["dataset"], card["symbol"], PUBLISHERS[pub], card["revision"], lo, hi)
    return {"type": "datachart", "rows": rows, "kind": "line", "unit": unit,
            "title": card["name"] + ": Hourly " + field.title(), "source": src,
            "origin": "database", "note": NOTE}


def export(person, slug, start="", end="", publisher=None, revision=""):
    """Validate before streaming; a consumer never gets an error inside a CSV."""
    card, pub, lo, hi, params = _selection(person, slug, publisher, start, end, revision)
    def stream():
        yield "# Source: Databento; %s; %s; %s\n" % (card["dataset"], card["symbol"], PUBLISHERS[pub])
        yield "# Version: %s; imported %s; hourly UTC; %s to %s inclusive\n" % (card["revision"], card["imported_at"], lo, hi)
        yield "# Prices: %s; volume: contracts; empty price: missing; %s\n" % (card["unit"], NOTE)
        yield "ts_event,publisher_id,instrument_id,contract,open,high,low,close,volume,artifact_sha256\n"
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        with closing(reader()) as con:
            for r in con.execute(_SELECT, params):
                writer.writerow(list(r))
                if buf.tell() >= CSV_CHUNK_BYTES:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
        if buf.tell():
            yield buf.getvalue()
    return stream()


def health():
    """Public operational metadata only: no observations or access principals."""
    if not DB_PATH.exists():
        if os.environ.get("MARKET_HISTORY_DB") or (DB_PATH.parent / "raw").exists():
            return [{"dataset": "private-market-history", "visibility": "private",
                     "status": "attention", "reason": "Saved database unavailable"}]
        return []
    with closing(reader()) as con:
        rows = con.execute("SELECT r.* FROM releases r WHERE id=(SELECT max(id) FROM releases x WHERE x.slug=r.slug)").fetchall()
    today = dt.datetime.now(dt.timezone.utc).date()
    out = []
    for r in rows:
        latest = json.loads(r["metadata"])["last_bar_utc"]
        age = (today - dt.date.fromisoformat(latest[:10])).days
        out.append({"dataset": "private-futures-" + r["slug"], "visibility": "private",
                    "status": "attention" if age > 7 else "ok", "stale": age > 7,
                    "latest_period": latest, "days_since_latest_period": age,
                    "stale_after_days": 7, "automatic_updates": False,
                    "last_published_at": r["imported_at"], "reason": "Saved history; manual import"})
    return out


TOOL_NAMES = ("search_saved_futures", "get_saved_futures")


def tool_schemas(person):
    if not catalog(person):
        return []
    return [
        {"name": TOOL_NAMES[0], "description": "Search your saved Databento hourly futures. Private account access; no vendor requests.",
         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}},
        {"name": TOOL_NAMES[1], "description": "Read your saved hourly futures OHLCV with exact decimal prices, UTC hours, contracts and source hashes. Page through next_offset. Dates are inclusive; never interpret unadjusted contract rolls as returns. No vendor requests.",
         "parameters": {"type": "object", "properties": {
             "slug": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
             "publisher": {"type": "integer"}, "offset": {"type": "integer"},
             "limit": {"type": "integer", "maximum": 100}, "revision": {"type": "string"}},
             "required": ["slug"], "additionalProperties": False}},
    ]


def run_tool(person, name, args):
    try:
        if name == TOOL_NAMES[0]:
            out = {"datasets": catalog(person, str(args.get("query") or "")[:200])}
        else:
            args = dict(args)
            args["limit"] = min(int(args.get("limit", 25)), 100)
            out = history(person, **args)
        return json.dumps(out, ensure_ascii=False), False
    except (HistoryError, TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc)}), True
