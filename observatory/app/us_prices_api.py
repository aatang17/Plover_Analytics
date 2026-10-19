# -*- coding: utf-8 -*-
"""US daily stock prices — Databento's consolidated end-of-day bars, served.

equity/us_prices_databento.py archives and loads EQUS.SUMMARY (one bar per
symbol per trading day, every US exchange, from July 2024) into
data/sec.duckdb, with older history from EQUS.MINI (every exchange, from
28 March 2023) and, from 1 May 2018 to 27 March 2023, each stock's listing
exchange (XNAS.ITCH for Nasdaq, XNYS.PILLAR for NYSE): close as that
exchange's closing auction sets it, volume that exchange's alone. This module serves a symbol's daily series and gives the
screener its latest closes.

Trust contract: a bar is the vendor's figure as delivered, never adjusted
for splits or dividends here, labelled with its source. Prices are licensed
market data, not public statistics: the source line names Databento, and the
dataset card carries the licence note. Missing is missing — a day with no
bar has no row.
"""
import datetime
import math

from fastapi import APIRouter, HTTPException, Query

from . import sec_api

router = APIRouter(prefix="/api/v1/us/prices", tags=["US prices"])

STALE_AFTER_DAYS = 4        # a trading day's bar is posted the next morning; a long weekend is 3 days
SPLIT_RULE = ("closes before a split day are divided by the split ratio. A split day is one where the company's "
              "own share count (its cover-page counts, filed each quarter) rose by about a whole number from 2 to "
              "100 (within 25%), or fell by its reciprocal, and the previous close ÷ the close moved by about the "
              "same (within 35%); the share count's ratio is used. A day whose share count barely moved is no "
              "split, however far the price fell. Where no share count is on file, the price decides alone: the "
              "previous close ÷ the close within 6% of a whole number or within 8% of its reciprocal. The bars "
              "themselves stay as delivered.")

PROVENANCE = {
    "trust": "official",
    "note": ("End-of-day bars exactly as Databento delivers them: open, high, low, close in US dollars, "
             "volume in shares, one row per listed symbol per trading day. From 28 March 2023, consolidated "
             "across every exchange (EQUS.MINI, then EQUS.SUMMARY from 1 July 2024); from 1 May 2018 to "
             "27 March 2023, the stock's listing exchange only (Nasdaq or NYSE), whose closing auction sets "
             "the official close but whose volume is that exchange's share alone. The bars are as delivered; "
             "charts and price changes use closes adjusted for splits (the rule is under calc), not for "
             "dividends. Licensed data: shown with a one-day delay under Databento's redistribution terms."),
    "credit": "Source: Databento (EQUS.SUMMARY, EQUS.MINI, XNAS.ITCH, XNYS.PILLAR). Market data provided by Databento.",
    "url": "https://databento.com/equities",
}


def _require():
    cur = sec_api._cur()
    if not sec_api._has(cur, "us_px_daily"):
        raise HTTPException(503, "US prices not loaded on this server yet")
    return cur


def _d(v):
    return sec_api._d(v)


# ---- splits ----------------------------------------------------------------
def _split_notes(cur, cik, splits):
    """Each split read from the prices, with how far the company's own share
    count bears it out ('confirmed', 'unconfirmed', 'contradicted') and one
    plain sentence saying so (app/sec_shares.split_sentence)."""
    from . import sec_shares
    out = []
    for d, ratio in splits:
        note, status = sec_shares.split_sentence(cur, cik, d, ratio)
        out.append({"date": _d(d), "ratio": ratio, "status": status, "note": note})
    return out



# The vendor's bars are unadjusted, so a 10-for-1 split reads as a 90% fall.
# A split leaves a signature no market day does: the close divided by the
# previous close lands on a whole number (or its reciprocal). Those days are
# found once per database version and used to adjust history for figures that
# span them — the one-year change, the chart line — while the bars themselves
# stay as delivered.
NICE_RATIOS = [2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50, 100]


def _nice(r):
    """The split ratio a day ratio means, or None: within 6% of a whole number
    (forward split) or within 8% of its reciprocal (reverse split — those land
    on volatile, thin stocks whose price moves on the day too)."""
    for n in NICE_RATIOS:
        if abs(r - n) <= 0.06 * n:
            return float(n)
    for n in NICE_RATIOS:
        if abs(r - 1.0 / n) <= 0.08 / n:
            return 1.0 / n
    return None


# ---- splits checked against the share count -----------------------------------
# The price alone cannot tell a split from a crash: a 50% fall reads as a
# 2-for-1 split, and a split day on which the stock also moved misses the band
# (Tesla, 31 August 2020: 5-for-1, closing up 12.6%, a day ratio of 4.44). The
# company's own share count settles it: the cover-page counts the SEC files
# per quarter (sec_cover_shares, from 2018) jump by the split ratio across a
# split and barely move across a crash.
def _share_counts(cur):
    """({symbol: cik}, {cik: [(count date, shares)]}) for the split check."""
    from . import sec_shares
    cik_of = {}
    if sec_api._has(cur, "sec_companies"):
        for cik, tickers in cur.execute("SELECT cik, tickers FROM sec_companies WHERE tickers IS NOT NULL").fetchall():
            for t in tickers.split(","):
                cik_of[t.strip().upper().replace("-", ".")] = cik
    try:
        totals = sec_shares._cover_totals(cur)
    except Exception:  # noqa: BLE001 — no cover counts on this server: the price rule alone
        totals = {}
    return cik_of, totals


def _share_ratio(totals, cik, day):
    """The share count just after `day` ÷ the one just before, or None."""
    counts = totals.get(cik) or [] if cik is not None else []
    before = [n for d, n in counts if d < day]
    after = [n for d, n in counts if d >= day]
    if not before or not after or not before[-1]:
        return None
    return after[0] / before[-1]


def _split_ratio(r, q):
    """(split ratio or None, 'confirmed' | 'contradicted' | 'price') for a day
    whose previous close ÷ close is r, given the share-count ratio q across it.
    Confirmed: the count moved by about a split ratio (within 25%) and the
    price by about the same (within 35%) — the count's ratio is used. A count
    that barely moved (within 25%) says it was no split. With no count on
    file, the price band alone decides, as before."""
    if q is not None and q > 0 and r > 0:
        fits = [n for k in NICE_RATIOS for n in (float(k), 1.0 / k)
                if abs(q / n - 1) <= 0.25 and abs(r / n - 1) <= 0.35]
        if fits:
            # the ratio both the count and the price sit closest to (Nvidia,
            # 10 June 2024: a count ratio of 9.9 is within 25% of 8 and of 10)
            return min(fits, key=lambda n: abs(math.log(q / n)) + abs(math.log(r / n))), "confirmed"
        if abs(q - 1) <= 0.25:
            return None, "contradicted"
    return _nice(r), "price"


# ---- the clean series --------------------------------------------------------
# The vendor's bars come by ticker, and a ticker is not a company. Three things
# are repaired here, once, for every reader (charts, rankings, one-year change):
#   * a close of 0 is a missing bar (the feed writes 0 on a dead ticker's last day);
#   * a split leaves a whole-number day ratio and is adjusted for;
#   * a jump of more than threefold that is not a split ratio is a BREAK: the
#     ticker passed to a different security (BNY Mellon took "BNY" from a $10
#     fund on 21 May 2026, after trading as "BK"). If another ticker stopped
#     trading the day before at a close within 5% of the first close after the
#     break, it is the same company under its old ticker and the two are joined;
#     otherwise the series starts at the break.
_EVENTS = {}


def _events(cur):
    v = sec_api.file_version()
    if _EVENTS.get("v") == v:
        return _EVENTS
    rows = cur.execute("""
        WITH p AS (
            SELECT symbol, date, close, lag(close) OVER (PARTITION BY symbol ORDER BY date) AS prev
            FROM us_px_daily WHERE close > 0)
        SELECT symbol, date, prev, close FROM p
        WHERE prev IS NOT NULL AND (prev / close >= 1.8 OR prev / close <= 0.56)
        ORDER BY symbol, date""").fetchall()
    splits, breaks = {}, {}
    cik_of, totals = _share_counts(cur)
    for sym, d, prev, close in rows:
        r = prev / close
        ratio, verdict = _split_ratio(r, _share_ratio(totals, cik_of.get(sym.replace("-", ".")), d))
        if ratio is not None:
            splits.setdefault(sym, []).append((d, ratio))
        elif r > 3 or r < 1.0 / 3:
            breaks[sym] = (d, close)                  # the latest break wins
    links = {}
    if breaks:
        last = {s_: (d, c) for s_, d, c in cur.execute(
            "SELECT symbol, max(date), arg_max(close, date) FROM us_px_daily WHERE close > 0 GROUP BY symbol").fetchall()}
        days = [r[0] for r in cur.execute("SELECT DISTINCT date FROM us_px_daily ORDER BY date").fetchall()]
        import bisect
        by_last = {}
        for s_, (d, c) in last.items():
            by_last.setdefault(d, []).append((s_, c))
        for sym, (d, c) in breaks.items():
            i = bisect.bisect_left(days, d)
            if i == 0:
                continue
            prevday = days[i - 1]
            cands = [s_ for s_, lc in by_last.get(prevday, []) if s_ != sym and lc > 0 and abs(lc / c - 1) < 0.05]
            if len(cands) == 1:
                links[sym] = (cands[0], d)
    # A ticker change to a new symbol (PSKY -> SKYD, 6 October 2026): its bars
    # begin the trading day after the old symbol's end, and the SEC lists both
    # tickers under one company. It is joined like a reused ticker: a break on
    # its first day, linked to the old symbol.
    renames = set()
    if sec_api._has(cur, "sec_companies"):
        cik_of = {}
        for cik, tickers in cur.execute("SELECT cik, tickers FROM sec_companies WHERE tickers IS NOT NULL").fetchall():
            for t in tickers.split(","):
                cik_of[t.strip().upper().replace("-", ".")] = cik
        span = cur.execute("""
            SELECT symbol, min(date), arg_min(close, date), max(date), arg_max(close, date)
            FROM us_px_daily WHERE close > 0 GROUP BY symbol""").fetchall()
        start_all = min((r[1] for r in span), default=None)
        days = [r[0] for r in cur.execute("SELECT DISTINCT date FROM us_px_daily ORDER BY date").fetchall()]
        ended = {}
        for s_, d0, c0, d1, c1 in span:
            ended.setdefault(d1, []).append((s_, c1))
        import bisect
        for s_, d0, c0, d1, c1 in span:
            if d0 == start_all or s_ in breaks or cik_of.get(s_.replace("-", ".")) is None:
                continue
            i = bisect.bisect_left(days, d0)
            if i == 0:
                continue
            cik = cik_of[s_.replace("-", ".")]
            cands = [o for o, lc in ended.get(days[i - 1], [])
                     if o != s_ and cik_of.get(o.replace("-", ".")) == cik and abs(lc / c0 - 1) < 0.25]
            if len(cands) == 1:
                breaks[s_] = (d0, c0)
                links[s_] = (cands[0], d0)
                renames.add(s_)
    _EVENTS.clear()
    _EVENTS.update(v=v, splits=splits, breaks=breaks, links=links, renames=renames)
    return _EVENTS


def split_events(cur):
    """{symbol: [(date, ratio)]}: ratio = previous close ÷ close on the split day
    (10.0 for a 10-for-1 split, 0.1 for a 1-for-10 reverse split)."""
    return _events(cur)["splits"]


def adjust_factor(events, start, end):
    """Divide a close on `start` by this to compare it with a close on `end`."""
    f = 1.0
    for d, ratio in events or []:
        if start < d <= end:
            f *= ratio
    return f


def _spellings(symbol):
    sym = (symbol or "").upper()
    return sorted({sym, sym.replace(".", "-"), sym.replace("-", ".")})


def resolve_symbol(cur, symbol):
    """The spelling the feed uses for a ticker (BRK-B -> BRK.B), or None."""
    sp = _spellings(symbol)
    hit = cur.execute("SELECT symbol FROM us_px_daily WHERE symbol IN (%s) AND close > 0 ORDER BY date DESC LIMIT 1"
                      % ",".join("?" * len(sp)), sp).fetchone()
    return hit[0] if hit else None


def clean_closes(cur, symbol, start=None, end=None):
    """([(date, close, adjusted_close)], info) for one ticker, repaired as above.
    info: {"symbol", "splits", "linked_from": (old ticker, date) or None,
    "starts_at": date or None (a break with no predecessor)}."""
    sym = resolve_symbol(cur, symbol)
    info = {"symbol": sym, "splits": [], "linked_from": None, "starts_at": None}
    if not sym:
        return [], info
    ev = _events(cur)
    pieces = [(sym, None, None)]
    brk = ev["breaks"].get(sym)
    if brk:
        if sym in ev["links"]:
            old, d = ev["links"][sym]
            pieces = [(old, None, d), (sym, d, None)]
            info["linked_from"] = (old, d)
        else:
            pieces = [(sym, brk[0], None)]
            info["starts_at"] = brk[0]
    rows, splits = [], []
    for s_, lo, hi in pieces:
        where, params = "symbol = ? AND close > 0", [s_]
        for cond, val in (("date >= ?", lo), ("date < ?", hi), ("date >= ?", start), ("date <= ?", end)):
            if val is not None:
                where += " AND " + cond
                params.append(val)
        rows += cur.execute("SELECT date, close FROM us_px_daily WHERE %s ORDER BY date" % where, params).fetchall()
        splits += [e for e in ev["splits"].get(s_, []) if (lo is None or e[0] >= lo) and (hi is None or e[0] < hi)]
    if not rows:
        return [], info
    splits = [e for e in splits if rows[0][0] < e[0] <= rows[-1][0]]
    info["splits"] = splits
    out = []
    for d, c in rows:
        f = 1.0
        for sd, ratio in splits:
            if d < sd:
                f *= ratio
        out.append((d, c, c / f if f != 1.0 else c))
    return out, info


def adjusted_closes(cur, symbol, start=None, end=None):
    """[(date, close, adjusted_close)] and the split events in the window."""
    rows, info = clean_closes(cur, symbol, start, end)
    return rows, info["splits"]


def latest_closes(cur):
    """{symbol: (date, close, close about one year earlier)} for every symbol
    with a bar in the last ten days. One query, cached per database version;
    the year-earlier close is split-adjusted and taken across a ticker change."""
    if not sec_api._has(cur, "us_px_daily"):
        return {}
    v = sec_api.file_version()
    if _LATEST.get("v") == v and _LATEST.get("rows"):
        return _LATEST["rows"]
    rows = cur.execute("""
        WITH last AS (
            SELECT symbol, arg_max(close, date) AS close, max(date) AS date
            FROM us_px_daily WHERE close > 0 AND date >= (SELECT max(date) FROM us_px_daily) - INTERVAL 10 DAY
            GROUP BY symbol),
        ago AS (
            SELECT p.symbol, arg_max(p.close, p.date) AS close_1y
            FROM us_px_daily p JOIN last l USING (symbol)
            WHERE p.close > 0 AND p.date <= l.date - INTERVAL 1 YEAR AND p.date >= l.date - INTERVAL 1 YEAR - INTERVAL 10 DAY
            GROUP BY p.symbol)
        SELECT l.symbol, l.date, l.close, a.close_1y FROM last l LEFT JOIN ago a USING (symbol)""").fetchall()
    ev = _events(cur)
    out = {}
    for sym, d, close, close_1y in rows:
        brk = ev["breaks"].get(sym)
        if brk and brk[0] > d - datetime.timedelta(days=366):
            series, _ = clean_closes(cur, sym, d - datetime.timedelta(days=376), d)
            first = [r for r in series if r[0] <= d - datetime.timedelta(days=365)]
            close_1y = first[-1][2] if first else None
        elif close_1y is not None and sym in ev["splits"]:
            close_1y = close_1y / adjust_factor(ev["splits"][sym], d - datetime.timedelta(days=366), d)
        out[sym] = (d, close, close_1y)
        if "." in sym:
            out.setdefault(sym.replace(".", "-"), out[sym])
    _LATEST["v"], _LATEST["rows"] = v, out
    return out


_LATEST = {}
_WINDOWS = {}


def window_changes(cur, start, end=None):
    """{symbol: (first_date, first_close, last_date, last_close, change_pct)} for
    every symbol with closes inside [start, end], from the clean series: zero
    closes skipped, splits adjusted, a ticker change followed. A symbol whose
    history starts after the window opens (an unlinked break) is left out.
    Memoised per database version and window."""
    v = sec_api.file_version()
    key = (v, start, end)
    if key in _WINDOWS:
        return _WINDOWS[key]
    where, params = "close > 0 AND date >= ?", [start]
    if end:
        where += " AND date <= ?"
        params.append(end)
    rows = cur.execute("""
        SELECT symbol, min(date) AS d0, arg_min(close, date) AS c0, max(date) AS d1, arg_max(close, date) AS c1
        FROM us_px_daily WHERE %s GROUP BY symbol""" % where, params).fetchall()
    ev = _events(cur)
    out = {}
    for sym, d0, c0, d1, c1 in rows:
        if not c0 or c1 is None or d0 == d1:
            continue
        brk = ev["breaks"].get(sym)
        # a renamed ticker's bars begin at its break, so a window opening
        # earlier than that needs the old ticker's part too
        if brk and (d0 < brk[0] or (sym in ev["renames"] and start < brk[0])) and brk[0] <= d1:
            if sym not in ev["links"]:
                continue                                  # history starts mid-window: not comparable
            series, _ = clean_closes(cur, sym, start, end)
            if not series:
                continue
            d0, c0, a0 = series[0][0], series[0][1], series[0][2]
            out[sym] = (d0, c0, d1, c1, (c1 / a0 - 1) * 100)
        else:
            adj = c0 / adjust_factor(ev["splits"].get(sym, []), d0, d1)
            out[sym] = (d0, c0, d1, c1, (c1 / adj - 1) * 100)
        if "." in sym:
            out.setdefault(sym.replace(".", "-"), out[sym])
    if len(_WINDOWS) > 12:
        _WINDOWS.clear()
    _WINDOWS[key] = out
    return out


def latest_date(cur):
    return cur.execute("SELECT max(date) FROM us_px_daily WHERE close > 0").fetchone()[0]


def first_date(cur):
    return cur.execute("SELECT min(date) FROM us_px_daily WHERE close > 0").fetchone()[0]


@router.get("/{symbol}", openapi_extra={"x-example": "/api/v1/us/prices/AAPL?start=2026-01-01"})
def series(symbol: str,
           start: str = Query("", description="YYYY-MM-DD; default one year back"),
           end: str = Query("", description="YYYY-MM-DD; default latest"),
           limit: int = Query(3000, ge=1, le=5000)):
    """One symbol's daily bars, oldest first, with the latest close and the
    change over the window."""
    cur = _require()
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) > 12:
        raise HTTPException(400, "give a US ticker, e.g. AAPL")
    try:
        e = datetime.date.fromisoformat(end) if end.strip() else None
        s = datetime.date.fromisoformat(start) if start.strip() else None
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD")
    feed = resolve_symbol(cur, sym)
    if feed is None:
        raise HTTPException(404, "no bars for %s in the loaded history" % sym)
    if e is None:
        e = cur.execute("SELECT max(date) FROM us_px_daily WHERE symbol = ? AND close > 0", [feed]).fetchone()[0]
    if s is None:
        s = e - datetime.timedelta(days=365)
    clean, info = clean_closes(cur, feed, s, e)
    if not clean:
        raise HTTPException(404, "no bars for %s between %s and %s" % (sym, s, e))
    clean = clean[-limit:]
    # open/high/low/volume from the same ticker pieces the clean series used
    ohlc = {}
    tickers = [feed] + ([info["linked_from"][0]] if info["linked_from"] else [])
    for t in tickers:
        for d, o, h, l_, v in cur.execute("SELECT date, open, high, low, volume FROM us_px_daily WHERE symbol = ? AND date >= ? "
                                          "AND date <= ? AND close > 0", [t, clean[0][0], clean[-1][0]]).fetchall():
            if t == feed and info["linked_from"] and d < info["linked_from"][1]:
                continue
            ohlc[d] = (o, h, l_, v)
    rows = [{"date": _d(d), "open": (ohlc.get(d) or (None,) * 4)[0], "high": (ohlc.get(d) or (None,) * 4)[1],
             "low": (ohlc.get(d) or (None,) * 4)[2], "close": c, "volume": (ohlc.get(d) or (None,) * 4)[3],
             "adjusted_close": a} for d, c, a in clean]
    splits = info["splits"]
    first, last = rows[0], rows[-1]
    change = ((last["adjusted_close"] / first["adjusted_close"] - 1) * 100) if first["adjusted_close"] else None
    sym = feed
    meta = {}
    if sec_api._has(cur, "sec_companies"):
        m = cur.execute("SELECT cik, name, exchange FROM sec_companies WHERE ticker = ? OR "
                        "list_contains(string_split(tickers, ','), ?) LIMIT 1", [sym, sym]).fetchone()
        if m:
            meta = {"cik": m[0], "sec_code": "cik:%d" % m[0], "name": m[1], "exchange": m[2]}
    return {"symbol": sym, **meta, "start": _d(s), "end": _d(e), "bars": rows, "count": len(rows),
            "latest": {"date": last["date"], "close": last["close"]},
            "change_pct": round(change, 4) if change is not None else None,
            "splits": _split_notes(cur, meta.get("cik"), splits),
            "ticker_change": ({"from": info["linked_from"][0], "on": _d(info["linked_from"][1])} if info["linked_from"] else None),
            "history_starts": _d(info["starts_at"]) if info["starts_at"] else None,
            "calc": {"change_pct": "(last adjusted close ÷ first adjusted close in the window − 1) × 100",
                     "adjusted_close": SPLIT_RULE},
            "provenance": PROVENANCE}


@router.get("/", include_in_schema=False)
@router.get("", openapi_extra={"x-example": "/api/v1/us/prices"})
def summary():
    """What is loaded: date range, symbols, pulls and what they cost."""
    cur = _require()
    tot = cur.execute("SELECT count(*), count(DISTINCT symbol), min(date), max(date) FROM us_px_daily").fetchone()
    pulls = sec_api._rows(cur, "SELECT pull_id, requested_at, start_date, end_date, quote_usd, status, rows, "
                               "csv_bytes, detail FROM us_px_pulls ORDER BY pull_id DESC LIMIT 30")
    for p in pulls:
        for k in ("requested_at", "start_date", "end_date"):
            p[k] = _d(p[k])
    spent = cur.execute("SELECT coalesce(sum(quote_usd), 0) FROM us_px_pulls WHERE status = 'ok'").fetchone()[0]
    return {"bars": tot[0], "symbols": tot[1], "first_date": _d(tot[2]), "last_date": _d(tot[3]),
            "pulls": pulls, "quoted_total_usd": round(spent, 2), "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    try:
        cur = sec_api._cur()
    except HTTPException:
        return []
    if not sec_api._has(cur, "us_px_pulls"):
        return []
    last_date, first_date = cur.execute("SELECT max(date), min(date) FROM us_px_daily").fetchone()
    last_ok = cur.execute("SELECT max(requested_at) FROM us_px_pulls WHERE status = 'ok'").fetchone()[0]
    failed = cur.execute("SELECT count(*) FROM us_px_pulls WHERE status <> 'ok' AND pull_id > "
                         "coalesce((SELECT max(pull_id) FROM us_px_pulls WHERE status = 'ok'), 0)").fetchone()[0]
    days = (datetime.date.today() - last_date).days if last_date else None
    stale = days is None or days > STALE_AFTER_DAYS
    return [{"dataset": "us-prices", "status": "attention" if (stale or failed) else "ok",
             "archive_read_through": _d(last_date), "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
             "archive_read_back_to": _d(first_date),
             "shape_flags": (["latest pull refused or failed"] if failed else [])}]


MANIFEST = {
    "id": "us-prices",
    "section": "us-reference",
    "name": {"en": "US daily stock prices (Databento)", "ja": "米国株式日次株価（Databento）"},
    "shape": "company",
    "summary": ("One end-of-day bar per listed US symbol per trading day — open, high, low, close and "
                "volume — from May 2018, delivered the next morning: across every exchange from March "
                "2023, the listing exchange alone before. The price layer under market value, P/E and "
                "yields in the US screener."),
    "source": {
        "publisher": "Databento",
        "document": ("EQUS.SUMMARY (from 2024-07-01), EQUS.MINI (2023-03-28 to 2024-06-30), XNAS.ITCH and "
                     "XNYS.PILLAR (2018-05-01 to 2023-03-27) — Databento, ohlcv-1d"),
        "url": "https://databento.com/equities",
        "credit": "Source: Databento (EQUS.SUMMARY, EQUS.MINI, XNAS.ITCH, XNYS.PILLAR). Market data provided by Databento.",
        "license_note": ("Licensed market data bought by the byte. Databento permits external "
                         "redistribution of its datasets after 24 hours; shown here with at least a "
                         "one-day delay. Not public-domain; the raw archive is never redistributed."),
    },
    "keys": ["symbol", "date"],
    "frequency": "daily",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": False,
                "history_from": "2018-05-01 (listing exchange to 2023-03-27; every exchange from 2023-03-28)",
                "stale_after_days": STALE_AFTER_DAYS},
    "measures": [
        {"id": "close", "label": "Close", "unit": "USD", "trust": "official"},
        {"id": "open", "label": "Open", "unit": "USD", "trust": "official"},
        {"id": "high", "label": "High", "unit": "USD", "trust": "official"},
        {"id": "low", "label": "Low", "unit": "USD", "trust": "official"},
        {"id": "volume", "label": "Volume", "unit": "shares", "trust": "official"},
    ],
    "endpoints": {"series": "/api/v1/us/prices/{symbol}", "summary": "/api/v1/us/prices"},
    "capabilities": ["summary"],
    "cite": "/api/v1/us/prices/{symbol}",
    "page": "/api.html",
    "notes": [
        "Bars are as delivered: not adjusted for splits or dividends.",
        "Every pull is quoted first and refused above a dollar cap; the quote, size and SHA-256 of each archive are recorded.",
        "Market value, P/E and yields in the US screener combine these closes with the filing's own shares and earnings; each names its formula.",
    ],
}
