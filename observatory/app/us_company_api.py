# -*- coding: utf-8 -*-
"""One US company's record: who it is, and everything the site holds for it.

A US company is its SEC number (CIK). Every US dataset keys its rows by it
(prices by ticker, 13F holdings by CUSIP, each mapped to the CIK here), so
the record is the one place that says, for a company, which items exist, how
much of each, how recent, and where each is read: CEO pay, financial
statements, the full filing history, the annual report's own words, insider
trades, the managers holding it, its results days, press releases, prices and
shares outstanding.

The name and logo come from app/us_companies.py (the admin console's edits)
first, then the filings and equity/us_logos.py. Counts are counts of what is
on file here, not statistics; each item names its API address.

  GET /api/v1/us/company?q=apple        search: ticker or name, up to 20
  GET /api/v1/us/company/AAPL           the record (ticker or CIK)
  GET /logos/us/<cik>-<sha>.<ext>       a logo uploaded in the admin console
"""
import json
import pathlib

from fastapi import APIRouter, HTTPException, Query, Response

from . import sec_api, us_companies, us_link

router = APIRouter(prefix="/api/v1/us/company", tags=["US company record"])
logo_router = APIRouter(include_in_schema=False)

_SOURCES = pathlib.Path(__file__).resolve().parent.parent / "web" / "assets" / "logos" / "us" / "sources.json"
_SITES = {}

PROVENANCE = ("Identity from the SEC's company tickers file and filer records; each item is "
              "a count of what this site has read from the SEC, the company's own newsroom "
              "or the price feed, with the date of the newest. Names and logos can be "
              "edited by the site's staff; the name as filed is always given beside it.")


def _found_site(cik):
    """The website equity/us_logos.py found (Wikidata's official site)."""
    if not _SITES and _SOURCES.exists():
        try:
            for k, v in json.loads(_SOURCES.read_text()).items():
                _SITES[int(k)] = v.get("domain")
        except (OSError, ValueError):
            pass
        _SITES.setdefault(0, None)
    return _SITES.get(int(cik))


def identity(cur, cik):
    rec = us_link.company_by_cik(cur, cik) or {}
    sec_name = rec.get("sec_name")
    edit = us_companies.entry(cik)
    member = None
    if sec_api._has(cur, "us_index_members"):
        member = cur.execute("""
            SELECT weight_pct FROM us_index_members WHERE cik = ? AND snapshot_id =
              (SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)')""",
                             [cik]).fetchone()
    return {
        "cik": int(cik), "ticker": rec.get("ticker"), "tickers": rec.get("tickers") or [],
        "name": us_link.company_name(cik, sec_name, cur) if sec_name else (edit.get("name") or None),
        "sec_name": sec_name, "logo": us_link.logo(cik),
        "website": us_companies.website(cik) or _found_site(cik),
        "exchange": rec.get("exchange"), "sic": rec.get("sic"),
        # the SEC's industry titles are in capitals: "ELECTRONIC COMPUTERS"
        "industry": (rec.get("industry") or "").capitalize() or None,
        "sp500_weight_pct": member[0] if member else None,
        "predecessor_cik": us_link.PREDECESSOR_CIKS.get(int(cik)),
        "edited": {k: edit.get(k) for k in ("name", "website", "logo", "updated_at") if edit.get(k)},
    }


def _one(cur, sql, params):
    try:
        return cur.execute(sql, params).fetchone()
    except Exception:                                     # noqa: BLE001 — a missing table is "not held"
        return None


_ONE = {"filings": "filing", "proxy statements": "proxy statement", "annual reports": "annual report",
        "managers": "manager", "days": "day", "releases": "release", "trading days": "trading day",
        "filing covers": "filing cover"}


def _item(key, label, count, unit, latest, latest_kind, page=None, api=None):
    held = bool(count)
    return {"key": key, "label": label, "held": held, "count": int(count) if count else 0,
            "unit": unit, "unit_one": _ONE.get(unit, unit), "latest": sec_api._d(latest) if held else None,
            "latest_kind": latest_kind, "page": page if held else None, "api": api}


def items(cur, ident):
    cik, t = ident["cik"], ident["ticker"] or str(ident["cik"])
    ciks = [cik] + ([ident["predecessor_cik"]] if ident["predecessor_cik"] else [])
    ph = ",".join("?" * len(ciks))
    out = []
    r = _one(cur, "SELECT count(*), max(filed) FROM sec_filings WHERE cik IN (%s) "
                  "AND status IN ('clean','partial')" % ph, ciks) or (0, None)
    out.append(_item("financials", "Financial statements", r[0], "filings", r[1], "filed",
                     "index.html?t=" + t, "/api/v1/us/financials/company/%d" % cik))
    r = _one(cur, "SELECT sum(filings), max(last_filed) FROM sec_cf_companies "
                  "WHERE cik = ? OR successor_cik = ?", [cik, cik]) or (0, None)
    out.append(_item("history", "Every filed figure since 2009", r[0], "filings", r[1], "filed",
                     "us-companies.html?t=" + t, "/api/v1/us/financials/deep/" + t))
    r = _one(cur, "SELECT count(*) FILTER (WHERE status = 'ok'), max(filed) FILTER (WHERE status = 'ok') "
                  "FROM sec_px_filings WHERE cik = ?", [cik]) or (0, None)
    out.append(_item("pay", "Executive pay", r[0] if ident["sp500_weight_pct"] is not None else 0,
                     "proxy statements", r[1], "filed", "us-pay.html?t=" + t, "/api/v1/us/pay/" + t))
    from . import sec_text_api
    tc = sec_text_api.cur()
    r = (_one(tc, "SELECT count(*), max(filed) FROM sec_text_filings WHERE cik IN (%s) "
                  "AND status IN ('ok','partial')" % ph, ciks) if tc else None) or (0, None)
    out.append(_item("text", "Annual report text", r[0], "annual reports", r[1], "filed",
                     None, "/api/v1/us/text/" + t))
    r = _one(cur, "SELECT count(*), max(filed) FROM sec_ins_filings WHERE issuer_cik = ?", [cik]) or (0, None)
    out.append(_item("insiders", "Insider trades", r[0], "filings", r[1], "filed",
                     "index.html?t=" + t, "/api/v1/us/insiders/company/" + t))
    cusips = [c.get("cusip") for c in us_link.cusips_for(cur, cik) if c.get("cusip")]
    r = (0, None)
    if cusips:
        r = _one(cur, """
            WITH b AS (SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 1)
            SELECT count(DISTINCT f.manager_cik), max(f.report_period)
            FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
            WHERE h.batch = (SELECT batch FROM b) AND h.cusip IN (%s) AND NOT f.amendment"""
                 % ",".join("?" * len(cusips)), cusips) or (0, None)
    out.append(_item("holders", "Fund managers holding it (13F)", r[0], "managers", r[1], "quarter ended",
                     "index.html?t=" + t, "/api/v1/us/holdings/issuer?cusip=" + (cusips[0] if cusips else "")))
    r = _one(cur, "SELECT count(*), max(date) FILTER (WHERE NOT scheduled) FROM us_events "
                  "WHERE cik = ? AND kind = 'results' AND is_earnings", [cik]) or (0, None)
    out.append(_item("results", "Results days", r[0], "days", r[1], "last",
                     None, "/api/v1/us/events?kind=results&ticker=" + t))
    from . import us_press
    pc = us_press.connect(readonly=True)
    try:
        r = (pc.execute("SELECT count(*), max(published) FROM press_releases WHERE cik = ?", [cik]).fetchone()
             if pc else None) or (0, None)
    except Exception:                                     # noqa: BLE001
        r = (0, None)
    finally:
        if pc:
            pc.close()
    out.append(_item("press", "Press releases", r[0], "releases", r[1], "published",
                     None, "/api/v1/us/press/" + t))
    symbols = [s for s in ident["tickers"] or [ident["ticker"]] if s]
    r = (0, None)
    if symbols:
        spell = sorted(set(symbols + [s.replace("-", ".") for s in symbols]))
        r = _one(cur, "SELECT count(*), max(date) FROM us_px_daily WHERE symbol IN (%s) AND close > 0"
                 % ",".join("?" * len(spell)), spell) or (0, None)
    out.append(_item("prices", "Daily prices", r[0], "trading days", r[1], "last close",
                     "index.html?t=" + t, "/api/v1/us/prices/" + t))
    r = _one(cur, "SELECT count(DISTINCT accn), max(end_date) FROM sec_cover_shares WHERE cik = ?",
             [cik]) or (0, None)
    out.append(_item("shares", "Shares outstanding", r[0], "filing covers", r[1], "as of",
                     "index.html?t=" + t, "/api/v1/us/financials/metrics/" + t))
    return out


@router.get("", openapi_extra={"x-example": "/api/v1/us/company?q=apple"})
def search(q: str = Query(..., min_length=1, max_length=80, description="ticker or name")):
    """Companies whose ticker or name matches, ticker matches first, up to 20."""
    cur = sec_api._cur()
    idx = us_link.companies(cur)
    term = q.strip()
    up = term.upper().replace(".", "-")
    hits, seen = [], set()

    def add(rec):
        if rec and rec["cik"] not in seen:
            seen.add(rec["cik"])
            hits.append(rec)
    add(idx["by_ticker"].get(up))
    alias = us_link.ALIASES.get(term.lower())
    if alias:
        add(idx["by_ticker"].get(alias))
    if term.isdigit():
        add(idx["by_cik"].get(int(term)))
    low = term.lower()
    # a name that starts with the words typed first, then a listed company, then the shorter name
    for rec in sorted(idx["by_cik"].values(), key=lambda r: (not (r.get("name") or "").lower().startswith(low),
                                                             r.get("ticker") is None, len(r.get("name") or ""))):
        if len(hits) >= 20:
            break
        names = (rec.get("name") or "", rec.get("sec_name") or "", us_companies.name(rec["cik"]) or "")
        if any(low in n.lower() for n in names) or up in (rec.get("tickers") or []):
            add(rec)
    return {"companies": [{"cik": r["cik"], "ticker": r.get("ticker"),
                           "name": us_link.company_name(r["cik"], r.get("sec_name") or r.get("name"), cur),
                           "exchange": r.get("exchange"), "logo": us_link.logo(r["cik"])} for r in hits[:20]],
            "count": len(hits[:20])}


@router.get("/{company}", openapi_extra={"x-example": "/api/v1/us/company/AAPL"})
def company(company: str):
    """Who the company is, and each item the site holds for it with how much and how recent."""
    cur = sec_api._cur()
    cik = sec_api.resolve_company(cur, company)
    ident = identity(cur, cik)
    if not ident["sec_name"] and not ident["ticker"]:
        raise HTTPException(404, "%r is not a US company this site knows; search with "
                                 "/api/v1/us/company?q=" % company)
    got = items(cur, ident)
    return {"company": ident, "items": got, "held": sum(1 for i in got if i["held"]),
            "provenance": PROVENANCE}


@logo_router.get("/logos/us/{name}")
def logo_file(name: str):
    got = us_companies.logo_file(name)
    if got is None:
        raise HTTPException(404, "no such logo")
    data, ctype = got
    # the address carries the file's hash, so it never changes under a reader
    return Response(data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})
