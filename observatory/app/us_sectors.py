# -*- coding: utf-8 -*-
"""The S&P's own sectors (GICS) for US companies, from the funds that track them.

Each of the eleven Select Sector SPDR funds holds exactly the S&P 500
companies in one sector, so a company's official sector is the fund that
holds it (equity/us_index_members.py loads the holdings; SPY's own file
leaves its Sector column blank). Only S&P 500 members have one here: the
sector list is the S&P's, and the S&P publishes it for its own index. A screen
or chart by sector says so.

The question box's broad sectors for rankings across every listed company
(app/us_steps.py _SECTORS) come from SEC industry codes instead and are
labelled as such; this module is the official list.
"""
import datetime
import re
import threading

from . import sec_api

# key, the S&P's sector name, the fund that holds it — the order the S&P lists them in
SECTORS = (
    ("tech", "Information Technology", "XLK"), ("health", "Health Care", "XLV"),
    ("financials", "Financials", "XLF"), ("consumer_disc", "Consumer Discretionary", "XLY"),
    ("communication", "Communication Services", "XLC"), ("industrials", "Industrials", "XLI"),
    ("staples", "Consumer Staples", "XLP"), ("energy", "Energy", "XLE"),
    ("utilities", "Utilities", "XLU"), ("real_estate", "Real Estate", "XLRE"),
    ("materials", "Materials", "XLB"),
)
NAMES = {k: n for k, n, _ in SECTORS}
FUNDS = {k: f for k, _, f in SECTORS}
# words a reader uses for each sector; an industry inside one (banks, chips) is the screener's SIC reading
WORDS = {
    "tech": r"\binfo(?:rmation)? tech(?:nology)?\b|\btech(?:nology)? (?:sector|stocks?|companies|names)\b|\btechnology\b|\btech\b",
    "health": r"\bhealth ?care\b|\bhealthcare\b",
    "financials": r"\bfinancials?\b(?! (?:statements?|results|year|data|figures))",
    "consumer_disc": r"\bconsumer discretionary\b|\bdiscretionary\b",
    "communication": r"\bcommunications? services?\b|\bcommunications? sector\b|\btelecoms?\b",
    "industrials": r"\bindustrials?\b",
    "staples": r"\bconsumer staples\b|\bstaples\b",
    "energy": r"\benergy\b",
    "utilities": r"\butilit(?:y|ies)\b",
    "real_estate": r"\breal estate\b|\breits?\b",
    "materials": r"\bmaterials\b",
}
CALC = ("A company's sector is the S&P's (GICS): the Select Sector SPDR fund that holds it "
        "(XLK, XLV, XLF, XLY, XLC, XLI, XLP, XLE, XLU, XLRE, XLB). Only S&P 500 members have one.")
STALE_AFTER_DAYS = 21          # refreshed weekly (app/backfill.py refresh_us_sectors)

_LOCK = threading.Lock()
_MEMO = {}


def index_name(key):
    return "S&P 500 %s (%s holdings)" % (NAMES[key], FUNDS[key])


def members(cur):
    """{cik: sector key} from each sector fund's latest holdings, and the
    holdings dates {key: date}. Empty before the funds are loaded."""
    v = sec_api.file_version()
    with _LOCK:
        if _MEMO.get("v") == v:
            return _MEMO["m"], _MEMO["d"]
    out, dates = {}, {}
    if sec_api._has(cur, "us_index_snapshots"):
        for key, _, _ in SECTORS:
            snap = cur.execute("SELECT snapshot_id, as_of FROM us_index_snapshots WHERE index_name = ? "
                               "ORDER BY snapshot_id DESC LIMIT 1", [index_name(key)]).fetchone()
            if not snap:
                continue
            dates[key] = snap[1]
            for (cik,) in cur.execute("SELECT cik FROM us_index_members WHERE snapshot_id = ?", [snap[0]]).fetchall():
                out.setdefault(int(cik), key)
    with _LOCK:
        _MEMO.update(v=v, m=out, d=dates)
    return out, dates


def sector_of(cur, cik):
    return members(cur)[0].get(int(cik))


def ciks(cur, keys):
    """The CIKs in any of the named sectors."""
    want = set(keys)
    return {c for c, k in members(cur)[0].items() if k in want}


def read(text):
    """The sector keys a request names, in the order the S&P lists them."""
    t = (text or "").lower()
    return [k for k, _, _ in SECTORS if re.search(WORDS[k], t)]


def health():
    try:
        cur = sec_api._cur()
    except Exception:  # noqa: BLE001 — no SEC database here
        return []
    _, dates = members(cur)
    if not dates and not sec_api._has(cur, "us_index_snapshots"):
        return []
    newest = max(dates.values()) if dates else None
    days = (datetime.date.today() - newest).days if newest else None
    missing = [NAMES[k] for k, _, _ in SECTORS if k not in dates]
    stale = days is None or days > STALE_AFTER_DAYS
    return [{"dataset": "us-sectors", "status": "attention" if (stale or missing) else "ok",
             "archive_read_through": newest.isoformat() if newest else None, "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale, "last_extracted_at": None,
             "archive_read_back_to": min(dates.values()).isoformat() if dates else None,
             "shape_flags": ["no holdings loaded for %s" % m for m in missing]}]
