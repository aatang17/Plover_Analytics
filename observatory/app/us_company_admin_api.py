# -*- coding: utf-8 -*-
"""Admin console, US Companies tab: edit a company's logo, name and website.

Writes app/us_companies.py's store and nothing else; every change is in the
audit log and the company's own history. Needs the Classification
permission, like the party profiles it sits beside.

  GET    /admin/api/us-companies?q=          search; without q, the work list
  GET    /admin/api/us-companies/{cik}       one company with its edits and history
  PUT    /admin/api/us-companies/{cik}       {"name": "...", "website": "..."}; blank clears
  POST   /admin/api/us-companies/{cik}/logo  the image as the raw body
  POST   /admin/api/us-companies/{cik}/logo/reset   back to the built-in logo
  DELETE /admin/api/us-companies/{cik}/logo  show no logo
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from . import admin_api, sec_api, staff, us_companies, us_company_api, us_link

router = APIRouter(prefix="/admin/api/us-companies", include_in_schema=False)
PERMISSION = "classification"


class Change(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None


def _who(request):
    person = admin_api._require_admin(request, PERMISSION)
    return person, staff.actor_label(person)


def _logo_state(cik):
    e = us_companies.entry(cik)
    if e.get("logo") in ("uploaded", "hidden"):
        return e["logo"]
    return "built_in" if us_link.builtin_logo(cik) else "none"


def _row(cur, cik):
    rec = us_link.company_by_cik(cur, cik) or {}
    e = us_companies.entry(cik)
    return {"cik": int(cik), "ticker": rec.get("ticker"), "sec_name": rec.get("sec_name"),
            "name": us_link.company_name(cik, rec.get("sec_name") or "", cur),
            "logo": us_link.logo(cik), "logo_state": _logo_state(cik),
            "edited": bool(e.get("name") or e.get("website") or e.get("logo")),
            "updated_at": e.get("updated_at"), "updated_by": e.get("updated_by")}


def _cik(cur, value):
    try:
        cik = sec_api.resolve_company(cur, value)
    except HTTPException:
        raise HTTPException(404, "No US company with SEC number or ticker %r." % value)
    if us_link.company_by_cik(cur, cik) is None:
        raise HTTPException(404, "No US company with SEC number %s." % cik)
    return cik


@router.get("")
def list_companies(request: Request, q: str = Query("", max_length=80)):
    """Search by ticker or name; with no search, what needs attention: S&P 500
    members with no logo, then every company already edited."""
    _who(request)
    cur = sec_api._cur()
    if q.strip():
        ciks = [c["cik"] for c in us_company_api.search(q)["companies"]]
        return {"mode": "search", "companies": [_row(cur, c) for c in ciks]}
    members = [r[0] for r in cur.execute("""
        SELECT cik FROM us_index_members WHERE snapshot_id =
          (SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)')
        ORDER BY weight_pct DESC""").fetchall()] if sec_api._has(cur, "us_index_members") else []
    missing = [c for c in members if us_link.logo(c) is None]
    edited = [c for c in us_companies.edited() if c not in missing]
    return {"mode": "work", "missing_logo": [_row(cur, c) for c in missing],
            "edited": [_row(cur, c) for c in edited[:100]], "sp500_members": len(members)}


@router.get("/{company}")
def one(company: str, request: Request):
    _who(request)
    cur = sec_api._cur()
    cik = _cik(cur, company)
    ident = us_company_api.identity(cur, cik)
    e = us_companies.entry(cik)
    return {"company": ident, "logo_state": _logo_state(cik), "builtin_logo": us_link.builtin_logo(cik),
            "found_website": us_company_api._found_site(cik),
            "edit": {k: e.get(k) for k in ("name", "website", "logo", "logo_bytes", "updated_at", "updated_by")},
            "history": list(reversed(e.get("history") or []))[:50],
            "max_logo_kb": us_companies.MAX_LOGO_BYTES // 1024}


def _done(request, by, action, cik, detail, fn):
    try:
        fn()
    except us_companies.EditError as exc:
        raise HTTPException(400, str(exc))
    admin_api.audit(action, "CIK %s: %s" % (cik, detail), admin_api._client_ip(request), by=by)
    return one(str(cik), request)


@router.put("/{company}")
def change(company: str, body: Change, request: Request):
    _, by = _who(request)
    cik = _cik(sec_api._cur(), company)

    def fn():
        if body.name is not None:
            us_companies.set_name(cik, body.name, by)
        if body.website is not None:
            us_companies.set_website(cik, body.website, by)
    detail = ", ".join("%s %r" % (k, v) for k, v in (("name", body.name), ("website", body.website))
                       if v is not None)
    return _done(request, by, "us_company_changed", cik, detail or "nothing", fn)


@router.post("/{company}/logo")
async def upload_logo(company: str, request: Request):
    _, by = _who(request)
    cik = _cik(sec_api._cur(), company)
    if int(request.headers.get("content-length") or 0) > us_companies.MAX_LOGO_BYTES:
        raise HTTPException(413, "A logo can be up to %d KB. Save it smaller and try again."
                            % (us_companies.MAX_LOGO_BYTES // 1024))
    data = await request.body()
    return _done(request, by, "us_company_logo_uploaded", cik, "%d bytes" % len(data),
                 lambda: us_companies.set_logo(cik, data, by))


@router.post("/{company}/logo/reset")
def reset_logo(company: str, request: Request):
    _, by = _who(request)
    cik = _cik(sec_api._cur(), company)
    return _done(request, by, "us_company_logo_reset", cik, "back to the built-in logo",
                 lambda: us_companies.reset_logo(cik, by))


@router.delete("/{company}/logo")
def hide_logo(company: str, request: Request):
    _, by = _who(request)
    cik = _cik(sec_api._cur(), company)
    return _done(request, by, "us_company_logo_hidden", cik, "no logo shown",
                 lambda: us_companies.hide_logo(cik, by))
