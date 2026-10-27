# -*- coding: utf-8 -*-
"""Admin console, Companies tab, Japan: edit a Japanese company's logo and name.

Writes app/jp_companies.py's store and nothing else; every change is in the
audit log and the company's own history. Needs the Classification
permission, like the US half of the tab (app/us_company_admin_api.py).

  GET    /admin/api/jp-companies?q=          search; without q, the work list
  GET    /admin/api/jp-companies/{code}      one company with its edits and history
  PUT    /admin/api/jp-companies/{code}      {"name": "..."}; blank clears
  POST   /admin/api/jp-companies/{code}/logo the image as the raw body
  POST   /admin/api/jp-companies/{code}/logo/reset   no edit (no logo)
  DELETE /admin/api/jp-companies/{code}/logo show no logo
  GET    /logos/jp/<code>-<sha>.<ext>        an uploaded logo, for every page
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from . import admin_api, equity_api, jp_companies, staff

router = APIRouter(prefix="/admin/api/jp-companies", include_in_schema=False)
logo_router = APIRouter(include_in_schema=False)
PERMISSION = "classification"
WORK_LIST = 100


class Change(BaseModel):
    name: Optional[str] = None


def _who(request):
    person = admin_api._require_admin(request, PERMISSION)
    return person, staff.actor_label(person)


def _logo_state(code):
    e = jp_companies.entry(code)
    return e["logo"] if e.get("logo") in ("uploaded", "hidden") else "none"


def _row(code, name_en, name_ja):
    e = jp_companies.entry(code)
    return {"code": code, "name_en": name_en, "name_ja": name_ja,
            "name": jp_companies.shown_name(code, name_en),
            "logo": jp_companies.logo(code), "logo_state": _logo_state(code),
            "edited": bool(e.get("name") or e.get("logo")),
            "updated_at": e.get("updated_at"), "updated_by": e.get("updated_by")}


def _search(q):
    return [(c["sec_code"], c.get("name_en"), c.get("name"))
            for c in equity_api.companies(q=q).get("companies") or []
            if jp_companies.code_of(c.get("sec_code"))]


def _company(value):
    code = jp_companies.code_of(value)
    if code:
        for c, en, ja in _search(code):
            if c == code:
                return code, en, ja
    raise HTTPException(404, "No Japanese company with securities code %r." % value)


# The largest companies by consolidated employees in their latest annual
# report: a fair stand-in for size where the platform holds no share prices.
_LARGEST = """
WITH n AS (SELECT left(sec_code, 4) AS code, max_by(filer_name_en, period_end) AS name_en,
                  max_by(filer_name, period_end) AS name_ja
           FROM eq_filings WHERE sec_code IS NOT NULL GROUP BY 1),
     y AS (SELECT left(sec_code, 4) AS code, max_by(employees_consolidated, period_end) AS emp
           FROM eq_company_year WHERE sec_code IS NOT NULL GROUP BY 1)
SELECT y.code, n.name_en, n.name_ja FROM y JOIN n USING (code)
WHERE y.emp IS NOT NULL ORDER BY y.emp DESC LIMIT ?"""


def _largest(limit):
    try:
        return equity_api._cur().execute(_LARGEST, [limit]).fetchall()
    except Exception:  # noqa: BLE001 — no equity database here: no work list, search still works
        return []


@router.get("")
def list_companies(request: Request, q: str = Query("", max_length=80)):
    """Search by code or name; with no search, what needs attention: the
    largest companies with no logo, then every company already edited."""
    _who(request)
    if q.strip():
        return {"mode": "search", "companies": [_row(*r) for r in _search(q.strip())]}
    largest = _largest(WORK_LIST)
    missing = [r for r in largest if jp_companies.logo(r[0]) is None and _logo_state(r[0]) != "hidden"]
    names = dict((r[0], r) for r in largest)
    edited = []
    for code in jp_companies.edited()[:100]:
        r = names.get(code)
        if r is None:
            try:
                r = _company(code)
            except HTTPException:
                r = (code, None, None)
        edited.append(_row(*r))
    return {"mode": "work", "missing_logo": [_row(*r) for r in missing], "edited": edited,
            "largest": len(largest)}


@router.get("/{company}")
def one(company: str, request: Request):
    _who(request)
    code, en, ja = _company(company)
    e = jp_companies.entry(code)
    return {"company": _row(code, en, ja), "logo_state": _logo_state(code),
            "edit": {k: e.get(k) for k in ("name", "logo", "logo_bytes", "updated_at", "updated_by")},
            "history": list(reversed(e.get("history") or []))[:50],
            "max_logo_kb": jp_companies.MAX_LOGO_BYTES // 1024}


def _done(request, by, action, code, detail, fn):
    try:
        fn()
    except jp_companies.EditError as exc:
        raise HTTPException(400, str(exc))
    admin_api.audit(action, "Code %s: %s" % (code, detail), admin_api._client_ip(request), by=by)
    return one(code, request)


@router.put("/{company}")
def change(company: str, body: Change, request: Request):
    _, by = _who(request)
    code = _company(company)[0]

    def fn():
        if body.name is not None:
            jp_companies.set_name(code, body.name, by)
    return _done(request, by, "jp_company_changed", code,
                 "name %r" % body.name if body.name is not None else "nothing", fn)


@router.post("/{company}/logo")
async def upload_logo(company: str, request: Request):
    _, by = _who(request)
    code = _company(company)[0]
    if int(request.headers.get("content-length") or 0) > jp_companies.MAX_LOGO_BYTES:
        raise HTTPException(413, "A logo can be up to %d KB. Save it smaller and try again."
                            % (jp_companies.MAX_LOGO_BYTES // 1024))
    data = await request.body()
    return _done(request, by, "jp_company_logo_uploaded", code, "%d bytes" % len(data),
                 lambda: jp_companies.set_logo(code, data, by))


@router.post("/{company}/logo/reset")
def reset_logo(company: str, request: Request):
    _, by = _who(request)
    code = _company(company)[0]
    return _done(request, by, "jp_company_logo_reset", code, "logo edit removed",
                 lambda: jp_companies.reset_logo(code, by))


@router.delete("/{company}/logo")
def hide_logo(company: str, request: Request):
    _, by = _who(request)
    code = _company(company)[0]
    return _done(request, by, "jp_company_logo_hidden", code, "no logo shown",
                 lambda: jp_companies.hide_logo(code, by))


@logo_router.get("/logos/jp/{name}")
def logo_file(name: str):
    got = jp_companies.logo_file(name)
    if got is None:
        raise HTTPException(404, "no such logo")
    data, ctype = got
    # the address carries the file's hash, so it never changes under a reader
    return Response(data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})
