"""Data sources: price data from a desk owner's own vendor accounts.

A person pastes the API key of a market-data service they already pay for (or
use free), and the specialists on their desk can read daily prices through it.
Read-only by construction: every provider here is asked for price history and
nothing else. There is no order, account or position call anywhere in this
module, and none is to be added (docs/plans/PLAN-PLATFORM-VISION.md §7).

The data is the customer's, under the customer's licence with the vendor, so it
stays on their desk: it is fetched per call, never written to the macro or
equity databases, never cached across desks, and never shown on a public page.
Every result names the vendor as its source; none of it carries the Official
Statistic badge.

A provider is one entry in PROVIDERS: a name, what it covers, how to get a key,
and two functions, `daily(key, symbol, start, end)` returning bars oldest
first, and `check(key)` returning None when the key works or a sentence saying
what to do. Adding a key-based vendor is one more entry. Vendors that need a
program running on the customer's own machine (Bloomberg Terminal, IBKR's
gateway) or a vendor approval do not fit here and are not attempted.

HTTP goes through `_http` so tests can swap it. Python 3.9: no 3.10+ syntax.
"""
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

from . import keychain, store

TIMEOUT_SECONDS = 20
MAX_BARS = 260           # about a year of trading days per call
MAX_PAGES = 20
# Cloudflare and CloudFront fronts refuse urllib's default 'Python-urllib'
# agent (see the Resend fix of 2026-10-11), so the desk names itself.
USER_AGENT = "PloverAnalytics-Desk/1.0 (+https://ploveranalytics.com)"
BAR_FIELDS = ("date", "open", "high", "low", "close", "adj_close", "volume")


class SourceError(Exception):
    """The vendor refused, could not be reached, or answered badly. The
    message is written for the person reading it and says what to do."""


def _http(url, headers=None):
    """(status, body bytes). Never raises for an HTTP status."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError("The service could not be reached (%s). Try again in a minute."
                          % getattr(exc, "reason", exc))


def _json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _num(v):
    """A number, or None. A blank or missing value is missing, never zero."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(d):
    """'2026-10-09' from '2026-10-09' or '20261009'; None if neither."""
    s = str(d or "").strip()
    if len(s) == 8 and s.isdigit():
        s = s[:4] + "-" + s[4:6] + "-" + s[6:]
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _bar(date, o, h, l, c, adj, vol):
    v = _num(vol)
    if v is not None and v == int(v):
        v = int(v)
    return {"date": date, "open": _num(o), "high": _num(h), "low": _num(l),
            "close": _num(c), "adj_close": _num(adj), "volume": v}


# ---------------------------------------------------------------- J-Quants
# JPX's own API (V2, API-key authentication). Tokyo-listed shares, daily bars.
# Codes are five digits (72030); a four-character code gets the trailing 0.
# The free plan's data ends 12 weeks before today.

JQ_BASE = "https://api.jquants.com/v2"


def _jq_code(symbol):
    s = str(symbol or "").strip().upper()
    for suffix in (".T", ".TSE", ".JP"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    if len(s) == 4 and s.isalnum():
        s += "0"
    if len(s) != 5 or not s.isalnum():
        raise SourceError("J-Quants takes a Tokyo stock code such as 7203 or 285A.")
    return s


def _jq_get(key, path, params):
    query = dict(params)
    out = []
    for _ in range(MAX_PAGES):
        status, body = _http(JQ_BASE + path + "?" + urllib.parse.urlencode(query),
                             {"x-api-key": key})
        data = _json(body)
        if status in (401, 403):
            raise SourceError("J-Quants refused the key. Copy it again from the J-Quants "
                              "dashboard (it may have been reissued) and replace it here.")
        if status == 429:
            raise SourceError("J-Quants says too many requests. Wait a minute and try again.")
        if status != 200 or not isinstance(data, dict):
            msg = (data or {}).get("message") if isinstance(data, dict) else None
            raise SourceError("J-Quants answered %s%s." % (status, ": " + msg if msg else ""))
        rows = data.get("data")
        if isinstance(rows, list):
            out.extend(rows)
        nxt = data.get("pagination_key")
        if not nxt:
            break
        query["pagination_key"] = nxt
    return out


def _jq_daily(key, symbol, start, end):
    code = _jq_code(symbol)
    rows = _jq_get(key, "/equities/bars/daily", {"code": code, "from": start, "to": end})
    bars = []
    for r in rows:
        d = _iso(r.get("Date"))
        if d:
            bars.append(_bar(d, r.get("O"), r.get("H"), r.get("L"), r.get("C"),
                             r.get("AdjC"), r.get("Vo")))
    return bars


def _jq_check(key):
    _jq_get(key, "/equities/master", {"code": "72030"})


# ------------------------------------------------------------------- EODHD
# End-of-day prices for most exchanges. Symbols are TICKER.EXCHANGE (AAPL.US);
# EODHD treats a bare US ticker as .US. A bad key is 401 'Unauthenticated'; a
# ticker the plan does not cover, or does not exist, is 403/404.

EODHD_BASE = "https://eodhd.com/api"


def _eodhd_symbol(symbol):
    s = str(symbol or "").strip().upper()
    if not s or len(s) > 40 or any(c in s for c in "/?#& "):
        raise SourceError("EODHD takes a symbol such as AAPL.US.")
    return s


def _eodhd_daily(key, symbol, start, end):
    sym = _eodhd_symbol(symbol)
    url = "%s/eod/%s?%s" % (EODHD_BASE, urllib.parse.quote(sym), urllib.parse.urlencode(
        {"api_token": key, "fmt": "json", "from": start, "to": end, "period": "d", "order": "a"}))
    status, body = _http(url)
    if status == 401:
        raise SourceError("EODHD refused the key. Copy it again from your EODHD account "
                          "page and replace it here.")
    if status in (403, 404):
        raise SourceError("EODHD has no prices for %s on this key's plan. Check the symbol "
                          "(it looks like AAPL.US) and what your plan covers." % sym)
    if status == 402 or status == 429:
        raise SourceError("EODHD says this key has used its requests for today.")
    data = _json(body)
    if status != 200 or not isinstance(data, list):
        raise SourceError("EODHD answered %s." % status)
    bars = []
    for r in data:
        d = _iso(r.get("date")) if isinstance(r, dict) else None
        if d:
            bars.append(_bar(d, r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                             r.get("adjusted_close"), r.get("volume")))
    return bars


def _eodhd_check(key):
    end = dt.date.today()
    _eodhd_daily(key, "AAPL.US", (end - dt.timedelta(days=10)).isoformat(), end.isoformat())


# --------------------------------------------------------------- registry

PROVIDERS = {
    "jquants": {
        "name": "J-Quants",
        "vendor": "JPX Market Innovation & Research (J-Quants API)",
        "covers": "Tokyo-listed shares, daily prices",
        "symbol_hint": "a Tokyo stock code, such as 7203",
        "example": "7203",
        "signup_url": "https://jpx-jquants.com/",
        "key_help": "Sign in at J-Quants, open the dashboard and copy your API key.",
        "notes": ["The free plan's prices end 12 weeks before today; paid plans are current."],
        "daily": _jq_daily,
        "check": _jq_check,
    },
    "eodhd": {
        "name": "EODHD",
        "vendor": "EODHD (End-of-Day Historical Data)",
        "covers": "Shares, funds, FX and crypto on most exchanges, daily prices",
        "symbol_hint": "TICKER.EXCHANGE, such as AAPL.US",
        "example": "AAPL.US",
        "signup_url": "https://eodhd.com/",
        "key_help": "Sign in at EODHD and copy the API token from your account page.",
        "notes": ["The free plan allows 20 requests a day and one year of history."],
        "daily": _eodhd_daily,
        "check": _eodhd_check,
    },
}
ORDER = ("jquants", "eodhd")


def catalogue():
    """What the Data sources page lists, without the functions."""
    out = []
    for p in ORDER:
        row = dict((k, v) for k, v in PROVIDERS[p].items() if k not in ("daily", "check"))
        row["slug"] = p
        out.append(row)
    return out


def check(provider, key):
    """None when the key works, else a sentence saying what is wrong."""
    try:
        PROVIDERS[provider]["check"](key)
    except SourceError as exc:
        return str(exc)
    return None


def _dates(start, end):
    """Clean (start, end) ISO dates; the defaults are the last year."""
    today = dt.date.today()
    e = _iso(end) or today.isoformat()
    s = _iso(start) or (dt.date.fromisoformat(e) - dt.timedelta(days=365)).isoformat()
    if s > e:
        s, e = e, s
    return s, e


def fetch(account_id, source, symbol, start=None, end=None):
    """Daily bars from one of this desk's sources, oldest first, with where
    they came from. Raises SourceError with a readable message."""
    p = PROVIDERS[source["provider"]]
    try:
        key = keychain.open_(store.data_source_ct(account_id, source["id"]) or "")
    except (ValueError, RuntimeError):
        raise SourceError("The key stored for %s could not be read. Replace it on the "
                          "Data sources page." % p["name"])
    s, e = _dates(start, end)
    bars = p["daily"](key, symbol, s, e)
    bars.sort(key=lambda b: b["date"])
    notes = list(p["notes"])
    total = len(bars)
    if total > MAX_BARS:
        bars = bars[-MAX_BARS:]
        notes.insert(0, "Showing the latest %d of %d days; ask for a shorter range for the rest."
                     % (MAX_BARS, total))
    if not bars:
        notes.insert(0, "No prices for %s between %s and %s." % (symbol, s, e))
    return {"provider": source["provider"], "symbol": str(symbol).strip().upper(),
            "from": s, "to": e, "fields": list(BAR_FIELDS), "bars": bars,
            "source": "%s, through this desk's own account" % p["vendor"],
            "licence": ("Fetched with the desk owner's own key, under their agreement with "
                        "the vendor. For use on this desk; not Plover data."),
            "notes": notes}


# ------------------------------------------------------ for specialists

TOOL_NAME = "vendor_prices"


def connected(account_id):
    return [s for s in store.data_sources(account_id) if s["enabled"]]


def tool_schemas(account_id):
    """The price tool, described for the sources this desk has switched on;
    an empty list when it has none, so no specialist sees a dead tool."""
    srcs = connected(account_id)
    if not srcs:
        return []
    lines = ["%s (%s): %s; symbol is %s" % (s["provider"], PROVIDERS[s["provider"]]["name"],
                                             PROVIDERS[s["provider"]]["covers"],
                                             PROVIDERS[s["provider"]]["symbol_hint"])
             for s in srcs if s["provider"] in PROVIDERS]
    return [{"name": TOOL_NAME,
             "description": ("Daily prices (open, high, low, close, adjusted close, volume) from "
                             "a market-data account the desk owner connected. Sources: "
                             + "; ".join(lines) + ". Missing values are null, never zero. "
                             "Cite the vendor named in 'source'; these are not Plover data."),
             "parameters": {"type": "object", "properties": {
                 "source": {"type": "string", "enum": [s["provider"] for s in srcs],
                            "description": "Which connected source to ask."},
                 "symbol": {"type": "string", "description": "The symbol in that source's form."},
                 "start": {"type": "string", "description": "First date, YYYY-MM-DD. Default: a year before end."},
                 "end": {"type": "string", "description": "Last date, YYYY-MM-DD. Default: today."}},
                 "required": ["source", "symbol"]}}]


def run_tool(account_id, args):
    """(text, is_error) for a specialist's call."""
    want = str(args.get("source") or "").strip().lower()
    src = next((s for s in connected(account_id) if s["provider"] == want), None)
    if src is None:
        return json.dumps({"error": "'%s' is not a connected data source on this desk." % want}), True
    try:
        out = fetch(account_id, src, args.get("symbol"), args.get("start"), args.get("end"))
    except SourceError as exc:
        return json.dumps({"error": str(exc)}), True
    store.touch_data_source(account_id, src["id"])
    return json.dumps(compact(out), ensure_ascii=False), False


TOOL_BARS = 90    # a tool result is clipped at 8,000 characters (runner)


def compact(out):
    """The specialist's copy: column names once, the latest TOOL_BARS rows,
    and the whole range's first and last close and extremes, so a long range
    still reads correctly instead of being cut mid-row."""
    bars = out["bars"]
    res = dict((k, v) for k, v in out.items() if k not in ("bars", "fields"))
    if bars:
        closes = [b for b in bars if b["close"] is not None]
        highs = [b["high"] for b in bars if b["high"] is not None]
        lows = [b["low"] for b in bars if b["low"] is not None]
        res["range"] = {"days": len(bars),
                        "first": {"date": closes[0]["date"], "close": closes[0]["close"]} if closes else None,
                        "last": {"date": closes[-1]["date"], "close": closes[-1]["close"]} if closes else None,
                        "highest_high": max(highs) if highs else None,
                        "lowest_low": min(lows) if lows else None}
    shown = bars[-TOOL_BARS:]
    if len(bars) > len(shown):
        res["notes"] = ["Rows show the latest %d of %d days; 'range' covers all of them."
                        % (len(shown), len(bars))] + list(res.get("notes") or [])
    res["columns"] = list(BAR_FIELDS)
    res["rows"] = [[b[f] for f in BAR_FIELDS] for b in shown]
    return res
