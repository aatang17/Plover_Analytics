# -*- coding: utf-8 -*-
"""The written parts of US annual reports, as filed: what a company says it
does, the risks it lists, its legal cases and management's account of the
year (equity/sec_text_extract.py reads them into data/sec_text.duckdb).

  GET /api/v1/us/text/{ticker}                  the latest report: sections, risks, a short summary
  GET /api/v1/us/text/{ticker}/section/{key}    one section in full (business, risk_factors, legal ...)
  GET /api/v1/us/text/{ticker}/search?q=        the paragraphs of that report that mention a phrase

Text is quoted exactly as the company wrote it; nothing is summarised by a
model. Every reply names the filing, its date and its link on EDGAR.
"""
import datetime
import json
import os
import pathlib
import re
import threading
import zlib

import duckdb
from fastapi import APIRouter, HTTPException, Query

from . import db, sec_api

router = APIRouter(prefix="/api/v1/us/text", tags=["US annual report text"])

DB_PATH = pathlib.Path(os.environ.get(
    "SEC_TEXT_DB_PATH", str(pathlib.Path(__file__).resolve().parent.parent / "data" / "sec_text.duckdb")))
STALE_AFTER_DAYS = 3          # app/backfill.py reads new annual reports every day
SECTION_NAMES = {"business": "Business", "risk_factors": "Risk Factors", "cybersecurity": "Cybersecurity",
                 "properties": "Properties", "legal": "Legal Proceedings",
                 "mdna": "Management's Discussion and Analysis", "market_risk": "Market Risk"}
PROVENANCE = {
    "trust": "official",
    "note": ("Text exactly as written in the company's latest annual report (10-K, or 20-F for a "
             "foreign filer), cut at the report's own item headings. Figure tables are left out; "
             "the figures are in the financial statements. Every document is archived with its SHA-256."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (annual reports).",
}

_READER, _READER_VERSION = None, None
_LOCK = threading.Lock()


def _version():
    try:
        st = DB_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def cur():
    """A cursor on the text database, or None when it is not on this server."""
    global _READER, _READER_VERSION
    version = _version()
    if version is None:
        return None
    with _LOCK:
        if _READER is None or _READER_VERSION != version:
            if _READER is not None:
                try:
                    _READER.close()
                except Exception:                              # noqa: BLE001
                    pass
            try:
                # capped like the other readers: left alone, DuckDB takes 80% of the
                # container for this file's buffer (2026-10-29: the web server was
                # 1.2 GB into swap)
                _READER = db.limit_memory(duckdb.connect(str(DB_PATH), read_only=True))
            except duckdb.Error:
                _READER = None                                 # being written: the next call tries again
                return None
            _READER_VERSION = version
        return _READER.cursor()


def _require():
    c = cur()
    if c is None:
        raise HTTPException(503, "Annual report text is not loaded on this server yet.")
    return c


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


# ---- reading -----------------------------------------------------------------
def latest(c, cik):
    """The company's latest annual report read, as a dict, or None."""
    row = c.execute("""SELECT adsh, form, period, filed, url, status, note FROM sec_text_filings
                       WHERE cik = ? AND status NOT IN ('failed', 'no_doc')
                       -- the year's newest filing with Business and Risk Factors: a
                       -- 10-K/A that adds only the Part III items does not hide them
                       ORDER BY period DESC NULLS LAST, status = 'ok' DESC, filed DESC
                       LIMIT 1""", [cik]).fetchone()
    if not row:
        return None
    return dict(zip(("adsh", "form", "period", "filed", "url", "status", "note"), row))


def section(c, adsh, key):
    """(title, [paragraphs], set of heading indexes) or None."""
    row = c.execute("SELECT title, text_z, headings FROM sec_text_sections WHERE adsh = ? AND section = ?",
                    [adsh, key]).fetchone()
    if not row:
        return None
    return row[0], _text(row[1]).split("\n\n"), set(json.loads(row[2] or "[]"))


def _text(blob):
    """Section text is stored zlib-compressed (equity/sec_text_extract.py)."""
    return zlib.decompress(bytes(blob)).decode("utf-8")


_BOILER = re.compile(
    r"(?i)forward[- ]looking|safe harbor|in this (annual )?report,? (the terms|references)|unless the context|"
    r"“we,” “us”|\"we,\" \"us\"|“company,” “we”|references (in this|to) |incorporated (herein )?by reference|"
    r"available (free of charge|on our website)|our (internet )?website|this annual report on form|"
    r"should be read in conjunction|as used in this|refers? to [a-z ]+ and (its|our) (consolidated )?subsidiaries")


_OVERVIEW = re.compile(r"(?i)^(?:\d\.?\s*)?(?:[a-z]\.?\s+)?(business overview|overview of (our|the) business)\b")
_HISTORY = re.compile(r"(?i)legal and commercial name|incorporated (in|under)|joint[- ]stock company|registered office|"
                      r"principal (executive )?offices? (is|are)|telephone number|was (established|incorporated|organized) (in|under|on)|"
                      r"^(see|refer to|please see)\b|for a description of")


def summary(c, adsh, chars=900):
    """The opening of the Business section in the company's own words: its
    first substantial paragraphs, skipping legal boilerplate and headings."""
    got = section(c, adsh, "business")
    if not got:
        return None
    _, paras, heads = got
    # a 20-F opens Item 4 with the company's history; what it does starts at "Business Overview"
    start = next((i for i in sorted(heads) if _OVERVIEW.search(paras[i]) and len(paras[i]) < 80), 0)
    out, n = [], 0
    for i, p in enumerate(paras):
        if i < start or i in heads or len(p) < 150 or _BOILER.search(p[:400]) or _HISTORY.search(p[:300]):
            continue
        out.append(p)
        n += len(p)
        if n >= chars or len(out) >= 2:
            break
    return out or None


def risk_list(c, adsh):
    return [{"n": n, "category": cat, "headline": head, "paragraphs": [a, b]}
            for n, cat, head, a, b in c.execute(
                "SELECT n, category, headline, para_from, para_to FROM sec_text_risks WHERE adsh = ? ORDER BY n",
                [adsh]).fetchall()]


def mentions(c, adsh, phrase, sections=None, limit=8):
    """[(section, paragraph)] of the report that mention the phrase (every
    word of it, in any order), most mentions first."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9&'\-]+", (phrase or "").lower()) if len(w) > 2][:6]
    if not words:
        return []
    rows = c.execute("SELECT section, text_z FROM sec_text_sections WHERE adsh = ?%s ORDER BY ord" % (
        " AND section IN (%s)" % ",".join("?" * len(sections)) if sections else ""),
        [adsh] + list(sections or [])).fetchall()
    hits = []
    for key, blob in rows:
        for i, p in enumerate(_text(blob).split("\n\n")):
            low = p.lower()
            if len(p) >= 80 and all(re.search(r"\b%s" % re.escape(w[:max(4, len(w) - 2)]), low) for w in words):
                score = sum(low.count(w[:max(4, len(w) - 2)]) for w in words)
                hits.append((score, key, i, p))
    hits.sort(key=lambda h: (-h[0], h[2]))
    return [(k, p) for _, k, _, p in hits[:limit]]


def _company(code):
    """(cik, ticker, name) from a ticker or a CIK."""
    sc = sec_api._require()
    code = (code or "").strip()
    if code.isdigit():
        row = sc.execute("SELECT cik, ticker, name FROM sec_companies WHERE cik = ?", [int(code)]).fetchone()
    else:
        row = sc.execute("SELECT cik, ticker, name FROM sec_companies WHERE upper(ticker) = ? "
                         "OR list_contains(string_split(upper(coalesce(tickers, '')), ','), ?) LIMIT 1",
                         [code.upper(), code.upper()]).fetchone()
    if not row:
        raise HTTPException(404, "No US-listed company with ticker or CIK %s." % code)
    return row


def _filing_out(f):
    return {"accession": f["adsh"], "form": f["form"], "period_end": _d(f["period"]), "filed": _d(f["filed"]),
            "url": f["url"], "status": f["status"], "note": f["note"]}


# ---- routes --------------------------------------------------------------------
@router.get("/{code}", openapi_extra={"x-example": "/api/v1/us/text/NVDA"})
def company(code: str):
    """The latest annual report: its sections, its risks and the opening of its Business section."""
    cik, ticker, name = _company(code)
    c = _require()
    f = latest(c, cik)
    if not f:
        raise HTTPException(404, "%s's annual report has not been read yet." % name)
    secs = [{"section": k, "item": it, "title": t, "chars": n, "name": SECTION_NAMES.get(k, k)}
            for k, it, t, n in c.execute("SELECT section, item, title, chars FROM sec_text_sections WHERE adsh = ? ORDER BY ord",
                                         [f["adsh"]]).fetchall()]
    return {"company": {"cik": cik, "ticker": ticker, "name": name}, "filing": _filing_out(f),
            "summary": summary(c, f["adsh"]), "sections": secs, "risks": risk_list(c, f["adsh"]),
            "provenance": PROVENANCE}


@router.get("/{code}/section/{key}")
def company_section(code: str, key: str):
    """One section in full, paragraph by paragraph; headings are flagged."""
    if key not in SECTION_NAMES:
        raise HTTPException(404, "Sections are: %s." % ", ".join(SECTION_NAMES))
    cik, ticker, name = _company(code)
    c = _require()
    f = latest(c, cik)
    got = section(c, f["adsh"], key) if f else None
    if not got:
        raise HTTPException(404, "%s's latest annual report has no %s section that could be read." % (name, SECTION_NAMES[key]))
    title, paras, heads = got
    return {"company": {"cik": cik, "ticker": ticker, "name": name}, "filing": _filing_out(f), "section": key,
            "title": title, "paragraphs": [{"text": p, "heading": i in heads} for i, p in enumerate(paras)],
            "provenance": PROVENANCE}


@router.get("/{code}/search")
def company_search(code: str, q: str = Query(..., min_length=3, max_length=100, description="a word or phrase, e.g. tariffs")):
    """The paragraphs of the latest annual report that mention a phrase."""
    cik, ticker, name = _company(code)
    c = _require()
    f = latest(c, cik)
    if not f:
        raise HTTPException(404, "%s's annual report has not been read yet." % name)
    hits = mentions(c, f["adsh"], q, limit=20)
    return {"company": {"cik": cik, "ticker": ticker, "name": name}, "filing": _filing_out(f), "query": q,
            "results": [{"section": k, "section_name": SECTION_NAMES.get(k, k), "text": p} for k, p in hits],
            "provenance": PROVENANCE}


# ---- health ---------------------------------------------------------------------
def health():
    """One entry in the shape /catalog/health lists the equity extractors in."""
    c = cur()
    if c is None:
        return []
    last, latest_filed, first_filed, read, failed = c.execute("""
        SELECT (SELECT max(checked_at) FROM sec_text_checks),
               (SELECT max(filed) FROM sec_text_filings WHERE status IN ('ok', 'partial')),
               (SELECT min(filed) FROM sec_text_filings WHERE status IN ('ok', 'partial')),
               (SELECT count(*) FROM sec_text_filings),
               (SELECT count(DISTINCT cik) FROM (SELECT cik, arg_max(status, checked_at) s FROM sec_text_checks GROUP BY cik)
                 WHERE s = 'failed')""").fetchone()
    shape = dict(c.execute("SELECT status, count(*) FROM sec_text_filings GROUP BY status").fetchall())
    days = (datetime.datetime.utcnow() - last).days if last else None
    stale = days is None or days > STALE_AFTER_DAYS
    flags = []
    if failed:
        flags.append("%d companies' latest read failed" % failed)
    if read and shape.get("unsplit", 0) > 0.05 * read:
        flags.append("%d of %d reports could not be cut into sections" % (shape["unsplit"], read))
    return [{
        "dataset": "sec-annual-text",
        "status": "attention" if (stale or failed) else "ok",
        "archive_read_through": _d(latest_filed),
        "days_behind": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": last.isoformat() + "Z" if last else None,
        "archive_read_back_to": _d(first_filed),
        "shape_flags": flags,
    }]
