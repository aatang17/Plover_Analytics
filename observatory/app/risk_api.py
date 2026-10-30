# -*- coding: utf-8 -*-
"""Business risks (事業等のリスク) — what each company says could hurt it.

The section of the annual securities report where management lists the risks
it believes could materially affect results, in its own words. Served as
filed: the text is the company's, verbatim, and nothing here summarises,
scores, classifies or translates it.

What IS derived, and says so:

  * **The items.** Filers number their risks — (1), ①, 1. — and the extractor
    cuts the section at those numbers (or, failing numbers, at short bracketed
    headings). The cut is ours; the words inside it are the filer's. A filing
    whose section could not be cut is served whole, status ``unsplit``.
  * **New / dropped.** An item is ``new`` when its heading, numbering removed,
    does not appear among the same company's headings in its previous annual
    report on file; ``dropped`` lists the previous report's headings missing
    from this one. A reworded heading shows as one new and one dropped — the
    comparison is of exact text, never of meaning. ``null`` where there is no
    earlier report to compare with.

Endpoints
---------
  /api/v1/equity/risks/summary               coverage by status
  /api/v1/equity/risks/companies?q=          search box feed
  /api/v1/equity/risks/company/{sec_code}    one filing's section, itemised
  /api/v1/equity/risks/search?q=             which companies' risks mention q

Same DuckDB file and reader as the other equity APIs; registered before them
so the literal /equity/risks/ paths win.
"""
import re

from fastapi import APIRouter, HTTPException, Query

from . import aliases, asof
from . import listing
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/risks", tags=["Business risks"])

PROVENANCE = {
    "trust": "official",
    "note": ("Text exactly as filed in each company's 事業等のリスク (business "
             "risks, annual securities report), EDINET. Spacing is tidied and "
             "tables are flattened to one line per row; the words are not "
             "changed. Raw filings archived with SHA-256; doc_id links to the "
             "source filing."),
}

CALC = {
    "items": ("Derived: the section cut at the filer's own numbered headings "
              "— (1), ①, 1., (ア), (a) — where each heading must carry the next "
              "number in its sequence; failing numbers, at short bracketed "
              "headings such as （為替変動について） that occur at least twice "
              "and never repeat. The preamble and items together reproduce the "
              "full section line for line."),
    "new": ("Derived: true when this item's heading, numbering removed, is not "
            "among the headings of the same company's previous annual report "
            "on file. Exact text comparison: a reworded heading counts as new. "
            "null when no previous report is on file."),
}

# The filer's numbering, stripped before headings are compared across years:
# (3) in one year is (4) the next when a risk is added above it.
_LABEL_RE = re.compile(u"^\\s*([（(]\\s*[0-9０-９a-zａ-ｚア-ン]{1,2}\\s*[）)]|"
                       u"[①-⑳㉑-㉟]|[0-9０-９]{1,2}\\s*[.．]?)\\s*")
_SPACE_RE = re.compile(u"[\\s　]+")

SEARCH_MIN = 2
SEARCH_MAX = 60


def _require():
    cur = _cur()
    got = _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_risk_filings','eq_risk_items')""")
    if len(got) != 2:
        raise HTTPException(503, "business-risks dataset not published on this server yet")
    return cur


def heading_key(heading):
    """A heading as compared across years: numbering and spacing removed."""
    return _SPACE_RE.sub("", _LABEL_RE.sub("", heading or "", count=1))


_LATEST = """
    WITH scoped AS (
        SELECT * FROM eq_risk_filings
        WHERE sec_code IS NOT NULL AND status IN ('clean','partial','unsplit')/*ASOF*/
    ),
    current_risk AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY sec_code
                                         ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM scoped
        ) WHERE rn = 1
    )
"""


def latest():
    """Latest filing per company, with the point-in-time ceiling in force."""
    return _LATEST.replace("/*ASOF*/", asof.clause("filed_date"))


@router.get("/summary")
def summary():
    """Coverage: filings and companies by extraction status."""
    cur = _require()
    ceiling = asof.clause("filed_date")
    by_status = _rows(cur, """
        SELECT status, count(*) AS filings, count(DISTINCT sec_code) AS companies
        FROM eq_risk_filings WHERE sec_code IS NOT NULL""" + ceiling + """
        GROUP BY status ORDER BY filings DESC""")
    span = _rows(cur, """
        SELECT min(period_end) AS first_period_end, max(period_end) AS last_period_end,
               max(filed_date) AS last_filed, count(DISTINCT sec_code) AS companies,
               count(*) AS filings, sum(n_items) AS items
        FROM eq_risk_filings WHERE sec_code IS NOT NULL""" + ceiling)[0]
    span["by_status"] = by_status
    span["status_note"] = (
        "clean: cut into the filer's own items. unsplit: the section has no "
        "numbered or bracketed headings and is served whole. partial: cut, but "
        "into a single top-level item. no_block: the report carries no tagged "
        "business-risks section. failed: the filing could not be read.")
    span["vintage"] = asof.vintage()
    span["provenance"] = PROVENANCE
    return span


@router.get("/companies")
@listing.marked
def companies(q: str = Query("", description="name or securities code substring")):
    """Search feed: companies with a business-risks section on file."""
    cur = _require()
    like = "%" + q.strip() + "%"
    alias_sql, alias_params = aliases.clause(cur, "f.sec_code", q)
    return {"companies": _rows(cur, latest() + NAME_CTES + """
        SELECT f.sec_code, f.filer_name AS name,
               coalesce(n.name_en, s.name_en) AS name_en,
               f.period_end, f.status, f.n_top_items, f.n_items
        FROM current_risk f
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        WHERE (f.sec_code LIKE ? OR f.filer_name LIKE ?
               OR lower(coalesce(n.name_en, s.name_en, '')) LIKE lower(?)"""
        + alias_sql + """)
        ORDER BY f.sec_code LIMIT 25""", [like, like, like] + alias_params)}


def _items(cur, doc_id):
    return _rows(cur, """
        SELECT item_no, level, parent_no, label, heading, heading_inline, body, n_chars
        FROM eq_risk_items WHERE doc_id = ? ORDER BY item_no""", [doc_id])


@router.get("/company/{sec_code}")
def company(sec_code: str, year: str = Query("", description="fiscal year the report "
                                             "covers, by the calendar year its period ends")):
    """One company's business-risks section, itemised, with what changed
    against its previous annual report on file."""
    cur = _require()
    code = sec_code.strip()[:4]
    ceiling = asof.clause("filed_date")
    filings = _rows(cur, """
        SELECT doc_id, edinet_code, sec_code, filer_name, period_end, filed_date,
               status, detail, split_by, n_chars, n_items, n_top_items,
               preamble, full_text, sha256_t1, parser_version
        FROM eq_risk_filings
        WHERE sec_code = ? AND status IN ('clean','partial','unsplit')""" + ceiling + """
        ORDER BY period_end DESC, filed_date DESC, doc_id DESC""", [code])
    if not filings:
        raise HTTPException(404, "no business-risks section on file for %s" % code)
    # One report per fiscal year: an amended or duplicate filing for the same
    # period is shadowed by the latest one filed.
    by_period, reports = set(), []
    for f in filings:
        if f["period_end"] not in by_period:
            by_period.add(f["period_end"])
            reports.append(f)
    want = (year or "").strip()
    if want:
        pick = [i for i, f in enumerate(reports)
                if f["period_end"] and str(f["period_end"].year) == want]
        if not pick:
            raise HTTPException(404, "no business-risks section for %s covering %s"
                                % (code, want))
        i = pick[0]
    else:
        i = 0
    f = dict(reports[i])
    prior = reports[i + 1] if i + 1 < len(reports) else None

    items = _items(cur, f["doc_id"])
    prior_keys = None
    if prior is not None:
        prior_items = _items(cur, prior["doc_id"])
        prior_keys = dict((heading_key(x["heading"]), x["heading"]) for x in prior_items)
    for it in items:
        it["new"] = (None if prior_keys is None or not prior_keys
                     else heading_key(it["heading"]) not in prior_keys)
    if prior_keys:
        here = set(heading_key(x["heading"]) for x in items)
        f["dropped"] = [h for k, h in prior_keys.items() if k not in here]
    else:
        f["dropped"] = None
    # The section is preamble + items when itemised, and full_text only when
    # it could not be cut — never both, so the composed company view and an
    # agent reading it do not carry every word twice.
    f["items"] = items
    f["compared_with"] = ({"doc_id": prior["doc_id"], "period_end": prior["period_end"],
                           "filed_date": prior["filed_date"], "status": prior["status"]}
                          if prior else None)
    f["reports"] = [{"doc_id": r["doc_id"], "period_end": r["period_end"],
                     "filed_date": r["filed_date"], "status": r["status"],
                     "n_top_items": r["n_top_items"]} for r in reports]
    en = _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT name_en FROM en_scode WHERE sec_code = ?""", [f["sec_code"]])
    f["name_en"] = en[0]["name_en"] if en else None
    f["source_url"] = ("https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?%s"
                       % f["doc_id"])
    if f["status"] == "unsplit":
        f["warning"] = ("This section has no numbered or bracketed headings, so it "
                        "is shown whole rather than item by item.")
    f["provenance"] = PROVENANCE
    f["calc"] = CALC
    f["vintage"] = asof.vintage()
    return f


def _snippet(text, q, width=70):
    i = text.find(q)
    if i < 0:
        return None
    a, b = max(0, i - width), min(len(text), i + len(q) + width)
    return ((u"…" if a > 0 else "") + text[a:b].replace("\n", " ")
            + (u"…" if b < len(text) else ""))


@router.get("/search")
def search(q: str = Query(..., description="text to find, e.g. 為替 or サイバー"),
           limit: int = Query(100, ge=1, le=500)):
    """Which companies' latest business-risks section mentions `q`, with the
    item it appears in. Exact substring match on the filed text — no stemming,
    no synonyms, no translation — so 為替 and 為替レート are different searches."""
    cur = _require()
    term = (q or "").strip()
    if len(term) < SEARCH_MIN or len(term) > SEARCH_MAX:
        raise HTTPException(400, "q must be %d to %d characters" % (SEARCH_MIN, SEARCH_MAX))
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    hits = _rows(cur, latest() + NAME_CTES + """,
        matched AS (
            SELECT f.doc_id, i.item_no, i.heading, i.body, NULL AS text
            FROM current_risk f JOIN eq_risk_items i USING (doc_id)
            WHERE i.heading LIKE ? ESCAPE '\\' OR i.body LIKE ? ESCAPE '\\'
            UNION ALL
            SELECT f.doc_id, NULL, NULL, NULL, coalesce(f.full_text, f.preamble)
            FROM current_risk f
            WHERE coalesce(f.full_text, f.preamble) LIKE ? ESCAPE '\\'
        )
        SELECT f.sec_code, f.filer_name AS name,
               coalesce(n.name_en, s.name_en) AS name_en, f.period_end, f.doc_id,
               m.item_no, m.heading, m.body, m.text
        FROM matched m JOIN current_risk f USING (doc_id)
        LEFT JOIN en_ecode n ON n.edinet_code = f.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = f.sec_code
        ORDER BY f.sec_code, m.item_no NULLS FIRST""", [like, like, like])
    companies, order = {}, []
    for h in hits:
        c = companies.get(h["sec_code"])
        if c is None:
            if len(order) >= limit:
                continue
            c = companies[h["sec_code"]] = {
                "sec_code": h["sec_code"], "name": h["name"], "name_en": h["name_en"],
                "period_end": h["period_end"], "doc_id": h["doc_id"], "matches": []}
            order.append(h["sec_code"])
        text = h["text"] if h["item_no"] is None else (
            (h["heading"] or "") + "\n" + (h["body"] or ""))
        c["matches"].append({"item_no": h["item_no"],
                             "heading": h["heading"],
                             "snippet": _snippet(text, term)})
    total = len(set(h["sec_code"] for h in hits))
    return {"q": term, "companies_matched": total, "returned": len(order),
            "companies": [companies[k] for k in order],
            "note": ("Latest business-risks section per company; exact substring "
                     "match on the filed Japanese text. A match in the preamble "
                     "(before the first item) has no item_no."),
            "vintage": asof.vintage(), "provenance": PROVENANCE}


MANIFEST = {
    "id": "business-risks",
    "section": "governance",
    "name": {"en": "Business risks", "ja": "事業等のリスク"},
    "shape": "company",
    "summary": ("The risks each listed company says could materially affect "
                "its business, verbatim from its annual report, cut into the "
                "company's own numbered items, with what is new against the "
                "previous year's report — searchable across companies."),
    "source": dict(_EDINET_SOURCE,
                   document=u"有価証券報告書 · 事業等のリスク (annual securities "
                            u"report, business risks)",
                   credit="Source: company filings on EDINET (Financial Services "
                          "Agency of Japan)."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2025", "stale_after_days": None,
    },
    "measures": [
        {"id": "risk_text", "label": "Business-risk text, as filed", "unit": "text",
         "trust": "official"},
        {"id": "items", "label": "The section cut into the filer's numbered items",
         "unit": "text", "trust": "derived", "calc": CALC["items"]},
        {"id": "new", "label": "Item not in the previous annual report",
         "unit": "boolean", "trust": "derived", "calc": CALC["new"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/risks/company/{sec_code}",
        "search": "/api/v1/equity/risks/companies",
        "summary": "/api/v1/equity/risks/summary",
        "text_search": "/api/v1/equity/risks/search",
    },
    "capabilities": ["company", "search", "summary"],
    "cite": "/risks.html?code={sec_code}",
    "page": "/risks.html",
    "notes": [PROVENANCE["note"], CALC["items"], CALC["new"]],
}
