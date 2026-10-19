# -*- coding: utf-8 -*-
"""US insider trades — Form 3/4/5 filings, served.

The loader (equity/sec_insiders_extract.py) puts the SEC's quarterly Insider
Transactions Data Sets into data/sec.duckdb. This module answers the three
questions people ask of them:

  * **What have insiders at this company done?** `/company/{cik or ticker}`:
    the filings of the last N months, each trade with the SEC's transaction
    code, and a summary that keeps open-market purchases (code P) and sales
    (code S) apart from awards (A), option exercises (M), tax withholding (F)
    and gifts (G). "Insiders bought" means code P here and nowhere else.
  * **Who is buying, across the market?** `/recent`: the latest trades of one
    code above a dollar size, newest first.
  * **What has this person done?** `/owner/{cik}`: one insider across every
    issuer they report for.

Trust contract: every row is Official — the shares, price and date exactly
as the filer reported and the SEC extracted; nothing is recomputed. The one
derived figure, a trade's dollar value, is shares × reported price, labelled
as such and null when the filer reported no price (awards, gifts, many
option exercises). Amendments (4/A) are shown with their flag, never merged
into the original. Missing is null, never 0.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import sec_api

router = APIRouter(prefix="/api/v1/us/insiders", tags=["US insiders"])

CODES = {
    "P": "Open-market or private purchase", "S": "Open-market or private sale",
    "A": "Grant, award or other acquisition from the issuer", "M": "Exercise or conversion of a derivative",
    "F": "Shares withheld to pay tax on an award or exercise", "G": "Gift",
    "D": "Disposition to the issuer (e.g. forfeiture, tender)", "C": "Conversion of a derivative",
    "X": "Exercise of an in-the-money or at-the-money derivative", "J": "Other (see footnote)",
    "W": "Acquisition or disposition by will or the laws of descent", "I": "Discretionary transaction under a plan",
    "E": "Expiration of a short derivative position", "H": "Expiration of a long derivative position",
    "O": "Exercise of an out-of-the-money derivative", "U": "Disposition under a tender of shares in a change of control",
    "L": "Small acquisition under Rule 16a-6", "Z": "Deposit into or withdrawal from a voting trust",
    "V": "Transaction voluntarily reported earlier than required", "K": "Equity swap or similar instrument",
}
STALE_AFTER_DAYS = 130      # a quarter's set is on the SEC site within weeks of quarter end

PROVENANCE = {
    "trust": "official",
    "note": ("Each trade exactly as the reporting person filed it on Form 3, 4 or 5 and the SEC "
             "extracted it (Insider Transactions Data Sets). Nothing is recomputed. `value` is the "
             "one platform figure: shares × the filer's reported price, null where no price was "
             "reported. Amendments are flagged and never merged."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Forms 3, 4 and 5).",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/",
}


def _require():
    cur = sec_api._cur()
    if not sec_api._has(cur, "sec_ins_filings"):
        raise HTTPException(503, "US insider filings not loaded on this server yet")
    return cur


def _rows(cur, sql, params=()):
    return sec_api._rows(cur, sql, params)


def _d(v):
    return sec_api._d(v)


def _url(cik, accession):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik or 0, accession.replace("-", ""))


def _since(months):
    return datetime.date.today() - datetime.timedelta(days=int(months) * 30)


TRADE_SQL = """
    SELECT t.accession, f.filed, f.document_type, f.amendment, f.plan_10b5_1,
           f.issuer_cik, f.issuer_name, f.issuer_symbol,
           o.owner_cik, o.owner_name, o.relationship, o.title,
           o.is_director, o.is_officer, o.is_ten_percent,
           t.derivative, t.security_title, t.trans_date, t.trans_code, t.acquired_disposed,
           t.shares, t.price, t.shares_after, t.direct_indirect, t.nature, t.footnotes,
           t.exercise_price, t.underlying_title, t.underlying_shares
    FROM sec_ins_transactions t
    JOIN sec_ins_filings f ON f.accession = t.accession
    LEFT JOIN sec_ins_owners o ON o.accession = t.accession
    WHERE %s/*ASOF*/
    ORDER BY t.trans_date DESC, f.filed DESC, t.sk
    LIMIT ?
"""


def _trades(cur, where, params):
    """Trades matching `where`, under the as-of ceiling when one is in force
    (a filing made after the ceiling is not yet known)."""
    sql = (TRADE_SQL % where).replace("/*ASOF*/", asof.clause("filed", "f"))
    return [_trade_out(r) for r in _rows(cur, sql, params)]


def _trade_out(r):
    out = {k: _d(v) for k, v in r.items()}
    out["value"] = (round(r["shares"] * r["price"], 2)
                    if r["shares"] is not None and r["price"] is not None else None)
    out["code_meaning"] = CODES.get(r["trans_code"])
    out["source_url"] = _url(r["issuer_cik"], r["accession"])
    return out


@router.get("/company/{sec_code}", openapi_extra={"x-example": "/api/v1/us/insiders/company/NVDA?months=12"})
def company(sec_code: str,
            months: int = Query(12, ge=1, le=120),
            code: str = Query("", description="one transaction code, e.g. P or S; default all"),
            include_derivative: bool = Query(False, description="also list option and RSU lines"),
            limit: int = Query(200, ge=1, le=2000)):
    """Insider filings at one issuer over the last N months, with a summary
    that counts open-market purchases (P) and sales (S) separately from
    awards, exercises and withholding."""
    cur = _require()
    if not (sec_code or "").strip():
        # The composed company page asks every dataset about a securities code
        # it has normalised, sometimes to nothing: that is "not here", not a bad request.
        raise HTTPException(404, "no US filer under an empty code — this shelf is keyed by SEC CIK or ticker")
    cik = sec_api.resolve_company(cur, sec_code)
    since = _since(months)
    where = "f.issuer_cik = ? AND t.trans_date >= ?"
    params = [cik, since]
    if (code or "").strip():
        if code.strip().upper() not in CODES:
            raise HTTPException(400, "code must be one of %s" % ", ".join(sorted(CODES)))
        where += " AND t.trans_code = ?"
        params.append(code.strip().upper())
    if not include_derivative:
        where += " AND NOT t.derivative"
    head = cur.execute("SELECT arg_max(issuer_name, filed), arg_max(issuer_symbol, filed), max(filed) "
                       "FROM sec_ins_filings f WHERE issuer_cik = ?" + asof.clause("filed", "f"), [cik]).fetchone()
    if not head or head[2] is None:
        raise HTTPException(404, "no insider filings for CIK %d in the loaded quarters" % cik)
    trades = _trades(cur, where, params + [limit])
    summary = _rows(cur, """
        SELECT t.trans_code AS code, count(*) AS trades, count(DISTINCT o.owner_cik) AS insiders,
               sum(t.shares) AS shares, sum(t.shares * t.price) AS value,
               count(*) FILTER (WHERE t.price IS NULL) AS without_price
        FROM sec_ins_transactions t JOIN sec_ins_filings f USING (accession)
        LEFT JOIN sec_ins_owners o USING (accession)
        WHERE f.issuer_cik = ? AND t.trans_date >= ? AND NOT t.derivative AND NOT f.amendment%s
        GROUP BY t.trans_code ORDER BY trades DESC""" % asof.clause("filed", "f"), [cik, since])
    for s in summary:
        s["meaning"] = CODES.get(s["code"])
    by = {s["code"]: s for s in summary}
    buys, sells = by.get("P") or {}, by.get("S") or {}
    insiders = _rows(cur, """
        SELECT o.owner_cik, arg_max(o.owner_name, f.filed) AS owner_name,
               arg_max(o.relationship, f.filed) AS relationship, arg_max(o.title, f.filed) AS title,
               count(DISTINCT f.accession) AS filings, max(f.filed) AS last_filed_date
        FROM sec_ins_owners o JOIN sec_ins_filings f USING (accession)
        WHERE f.issuer_cik = ? AND f.filed >= ?%s
        GROUP BY o.owner_cik ORDER BY last_filed_date DESC""" % asof.clause("filed", "f"), [cik, since])
    for i in insiders:
        i["last_filed_date"] = _d(i["last_filed_date"])
    meta = sec_api._company_meta(cur, cik)
    return {
        "cik": cik, "sec_code": "cik:%d" % cik, "name": head[0],
        "ticker": meta.get("ticker") or head[1], "industry": meta.get("industry"),
        "months": months, "since": since.isoformat(), "last_filed_date": _d(head[2]),
        "as_of": asof.current().isoformat() if asof.current() else None,
        "summary": {
            "open_market_buys": {"trades": buys.get("trades", 0), "insiders": buys.get("insiders", 0),
                                 "shares": buys.get("shares"), "value": buys.get("value")},
            "open_market_sells": {"trades": sells.get("trades", 0), "insiders": sells.get("insiders", 0),
                                  "shares": sells.get("shares"), "value": sells.get("value")},
            "net_open_market_shares": ((buys.get("shares") or 0) - (sells.get("shares") or 0))
            if (buys or sells) else None,
            "by_code": summary,
        },
        "insiders": insiders, "trades": trades, "trades_shown": len(trades),
        "codes": CODES, "provenance": PROVENANCE,
    }


@router.get("/recent", openapi_extra={"x-example": "/api/v1/us/insiders/recent?code=P&min_value=1000000"})
def recent(code: str = Query("P", description="transaction code; P = open-market purchase"),
           min_value: float = Query(0, ge=0, description="shares × price at least this, in dollars"),
           days: int = Query(30, ge=1, le=400),
           relationship: str = Query("", description="director | officer | ten_percent"),
           limit: int = Query(100, ge=1, le=1000)):
    """The latest trades of one code across every issuer, newest first.
    Amendments are left out here so a corrected filing is not counted twice."""
    cur = _require()
    c = (code or "P").strip().upper()
    if c not in CODES:
        raise HTTPException(400, "code must be one of %s" % ", ".join(sorted(CODES)))
    where = ("t.trans_code = ? AND NOT t.derivative AND NOT f.amendment AND t.trans_date >= ? "
             "AND t.shares IS NOT NULL AND t.price IS NOT NULL AND t.shares * t.price >= ?")
    params = [c, datetime.date.today() - datetime.timedelta(days=days), min_value]
    rel = (relationship or "").strip().lower()
    if rel:
        col = {"director": "o.is_director", "officer": "o.is_officer", "ten_percent": "o.is_ten_percent"}.get(rel)
        if not col:
            raise HTTPException(400, "relationship must be director, officer or ten_percent")
        where += " AND %s" % col
    rows = _trades(cur, where, params + [limit])
    return {"code": c, "meaning": CODES[c], "days": days, "min_value": min_value,
            "relationship": rel or None, "trades": rows, "count": len(rows), "provenance": PROVENANCE}


@router.get("/owner/{owner_cik}", openapi_extra={"x-example": "/api/v1/us/insiders/owner/1214128"})
def owner(owner_cik: str, months: int = Query(24, ge=1, le=240), limit: int = Query(300, ge=1, le=2000)):
    """One reporting person's trades across every issuer, newest first."""
    cur = _require()
    n = sec_api.parse_cik(owner_cik)
    rows = _trades(cur, "o.owner_cik = ? AND t.trans_date >= ?", [n, _since(months), limit])
    if not rows:
        raise HTTPException(404, "no filings by reporting person CIK %d in the loaded quarters" % n)
    issuers = sorted({(r["issuer_cik"], r["issuer_name"], r["issuer_symbol"]) for r in rows},
                     key=lambda x: x[1] or "")
    return {"owner_cik": n, "owner_name": rows[0]["owner_name"], "months": months,
            "issuers": [{"cik": a, "name": b, "ticker": c} for a, b, c in issuers],
            "trades": rows, "provenance": PROVENANCE}


@router.get("/summary", openapi_extra={"x-example": "/api/v1/us/insiders/summary"})
def summary():
    """What is loaded: quarters, filings, trades by code."""
    cur = _require()
    quarters = _rows(cur, "SELECT quarter, filings, transactions, holdings, unknown_codes, loaded_at, "
                          "status, detail FROM sec_ins_quarters ORDER BY quarter DESC")
    for q in quarters:
        q["loaded_at"] = _d(q["loaded_at"])
    codes = _rows(cur, "SELECT trans_code AS code, count(*) AS trades FROM sec_ins_transactions "
                       "WHERE NOT derivative GROUP BY 1 ORDER BY 2 DESC")
    for c in codes:
        c["meaning"] = CODES.get(c["code"])
    tot = cur.execute("SELECT count(*), count(DISTINCT issuer_cik), min(filed), max(filed) "
                      "FROM sec_ins_filings").fetchone()
    return {"quarters": quarters, "filings": tot[0], "issuers": tot[1],
            "earliest_filed": _d(tot[2]), "latest_filed": _d(tot[3]), "codes": codes,
            "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    try:
        cur = sec_api._cur()
    except HTTPException:
        return []
    if not sec_api._has(cur, "sec_ins_quarters"):
        return []
    rows = cur.execute("SELECT quarter, loaded_at, status FROM sec_ins_quarters ORDER BY quarter").fetchall()
    good = [r for r in rows if r[2] == "ok"]
    failed = [r[0] for r in rows if r[2] == "failed"]
    if not good:
        return [{"dataset": "sec-insiders", "status": "attention", "archive_read_through": None,
                 "days_behind": None, "stale_after_days": STALE_AFTER_DAYS, "stale": True,
                 "last_extracted_at": None, "archive_read_back_to": None,
                 "shape_flags": ["no quarter loaded"] + ["quarter %s failed" % f for f in failed]}]
    newest = max(good, key=lambda r: (int(r[0][:4]), int(r[0][5])))
    oldest = min(good, key=lambda r: (int(r[0][:4]), int(r[0][5])))
    y, q = int(newest[0][:4]), int(newest[0][5])
    through = datetime.date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - datetime.timedelta(days=1)
    back_to = datetime.date(int(oldest[0][:4]), (int(oldest[0][5]) - 1) * 3 + 1, 1)
    days = (datetime.date.today() - through).days
    stale = days > STALE_AFTER_DAYS
    ran = max((r[1] for r in good if r[1]), default=None)
    return [{"dataset": "sec-insiders", "status": "attention" if (stale or failed) else "ok",
             "archive_read_through": through.isoformat(), "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": ran.isoformat() + "Z" if ran else None,
             "archive_read_back_to": back_to.isoformat(),
             "shape_flags": ["quarter %s failed to load" % f for f in failed]}]


MANIFEST = {
    "id": "sec-insiders",
    "section": "us-reference",
    "name": {"en": "US insider trades (Forms 3, 4 and 5)", "ja": "米国インサイダー取引（Form 3・4・5）"},
    "shape": "company",
    "summary": ("Every trade and holding a director, officer or 10% holder reports in a US "
                "company, from the SEC's quarterly Insider Transactions Data Sets: by company "
                "with open-market buys and sells kept apart from awards and exercises, across "
                "the market by transaction code, and by reporting person."),
    "source": {
        "publisher": "U.S. Securities and Exchange Commission",
        "document": "Insider Transactions Data Sets (quarterly; every Form 3, 4 and 5 since 2006)",
        "url": "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
        "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (Forms 3, 4 and 5).",
        "license_note": "US government work; public domain.",
    },
    "keys": ["issuer_cik", "accession"],
    "frequency": "per-filing",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
                "history_from": "the newest quarterly sets loaded (SEC_INSIDER_QUARTERS); the SEC's run from 2006 Q1",
                "stale_after_days": STALE_AFTER_DAYS},
    "measures": [
        {"id": "shares", "label": "Shares traded", "unit": "shares", "trust": "official"},
        {"id": "price", "label": "Price per share, as reported", "unit": "count", "trust": "official"},
        {"id": "shares_after", "label": "Shares owned after the trade", "unit": "shares", "trust": "official"},
        {"id": "value", "label": "Trade value (shares × reported price)", "unit": "USD", "trust": "derived",
         "calc": "shares × the price per share the filer reported; null when no price was reported"},
    ],
    "endpoints": {
        "company": "/api/v1/us/insiders/company/{cik_or_ticker}",
        "recent": "/api/v1/us/insiders/recent",
        "owner": "/api/v1/us/insiders/owner/{owner_cik}",
        "summary": "/api/v1/us/insiders/summary",
    },
    "capabilities": ["company", "summary"],
    "cite": "/api/v1/us/insiders/company/{issuer_cik}",
    "page": "/api.html",
    "notes": [
        "\"Insiders bought\" means transaction code P (open-market or private purchase); awards (A), "
        "option exercises (M), tax withholding (F) and gifts (G) are counted separately and never as buying or selling.",
        "A trade's value is shares × the price the filer reported; awards and many exercises carry no "
        "price and have no value, never 0.",
        "Amendments (4/A) keep their own row and flag; the original is not changed.",
        "Form 4 is due within two business days of the trade, so this lags the market by days, not months.",
    ],
}
