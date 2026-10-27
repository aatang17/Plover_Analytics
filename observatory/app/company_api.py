# -*- coding: utf-8 -*-
"""One company, every dataset — the composed view.

`/api/v1/equity/...` answers "what does this dataset say about this company",
one router at a time. This answers the question a reader actually has: what do
we know about 7203? It walks the registry, calls each dataset that declares a
company view, and returns one document — holdings, register, board and pay,
buybacks, facilities, financials, AGM votes, segments — in section order.

Composition rules, all of them consequences of the trust contract:

  * **Every block is independent.** One dataset raising must never cost the
    other eight, so each is wrapped: a failure becomes an `error` block and the
    response is still a 200 with the rest of the company in it.
  * **Absence is reported, never omitted.** A dataset with no rows for this
    company appears under `coverage.missing` with the reason it gave. A caller
    can tell "we have no facilities filing for this company" from "facilities
    are not published on this server", and neither looks like zero.
  * **Nothing is recomputed here.** Each block carries its own dataset's
    `calc`, `provenance` and `cite`; this module joins blocks, it does not do
    arithmetic across them. Yen book values, index levels and voting rights
    never meet in a total.
  * **Identity comes from the filings.** The company's name is whatever the
    blocks filed, preferred in section order — this module has no name table
    of its own to drift from them.

Registered before the core router so `/api/v1/company/{code}` beats the
`/api/v1/{dataset}/...` catch-alls.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from . import asof, registry

router = APIRouter(prefix="/api/v1/company", tags=["Company"])

# Rows per table in a block. A company page shows a table's head and links to
# the dataset's own endpoint for the rest; without a cap, one filer with 400
# facilities would define the payload for everyone.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
CODE_MAX = 8

def _fmt(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _scalars(raw):
    """The block's own scalar fields — the facts, without the tables or the
    long prose notes that repeat on every response."""
    out = {}
    for k, v in raw.items():
        if isinstance(v, (list, dict)) or k.endswith("_note") or k in ("calc", "provenance"):
            continue
        out[k] = _fmt(v)
    return out


def _nested_facts(raw):
    """Scalars one level down, e.g. cross-shareholdings' `entity` and `scale`."""
    out = {}
    for k, v in raw.items():
        if not isinstance(v, dict) or k in ("calc", "provenance", "lifecycle_labels"):
            continue
        for kk, vv in v.items():
            if not isinstance(vv, (list, dict)):
                out["%s.%s" % (k, kk)] = _fmt(vv)
    return out


def _tables(raw, limit):
    """Row tables, capped, with the full count kept so nothing looks complete
    when it is not."""
    tables, truncated = {}, {}
    for k, v in raw.items():
        if not isinstance(v, list):
            continue
        tables[k] = v[:limit]
        if len(v) > limit:
            truncated[k] = {"returned": limit, "total": len(v)}
    return tables, truncated


def _block(mid, manifest, raw, compact, limit):
    facts = _scalars(raw)
    facts.update(_nested_facts(raw))
    block = {
        "dataset": mid,
        "name": manifest["name"]["en"],
        "section": manifest["section"],
        "shape": manifest["shape"],
        "facts": facts,
        "calc": dict((m["id"], m["calc"]) for m in manifest["measures"]
                     if m["trust"] == "derived"),
        "provenance": {"trust": "official",
                       "publisher": manifest["source"]["publisher"],
                       "credit": manifest["source"]["credit"],
                       "document": manifest["source"]["document"]},
        "notes": manifest["notes"],
        "api": manifest["endpoints"].get("company"),
        "cite": manifest["cite"],
    }
    tables, truncated = _tables(raw, limit)
    block["table_counts"] = dict((k, len(v)) for k, v in raw.items()
                                 if isinstance(v, list))
    if not compact:
        block["tables"] = tables
        if truncated:
            block["truncated"] = truncated
    return block


def _fill(template, code):
    return template.replace("{sec_code}", code).replace("{code}", code)


# Fields a block may carry the company's own name in, best first.
_NAME_EN = ("entity.name_en", "filer_name_en", "name_en", "company")
_NAME_JA = ("entity.name_ja", "filer_name", "name", "issuer_name")
_INDUSTRY = ("entity.industry_en", "industry_en")


def _identity(code, blocks):
    """The company, as the filings name it. First block to state a field wins,
    in section order — no name table here to drift from what was filed."""
    out = {"sec_code": code}
    for keys, field in ((_NAME_EN, "name_en"), (_NAME_JA, "name"),
                        (_INDUSTRY, "industry")):
        for block in blocks:
            for key in keys:
                value = block["facts"].get(key)
                if value and field not in out:
                    out[field] = value
                    break
            if field in out:
                break
    if out.get("name_en"):
        # name_en stays as filed; pages show it written as people write it
        from .jp_names import display_name
        out["display_name"] = display_name(out["name_en"])
    return out


def _read(code, mid, manifest, limit, compact):
    """(block, missing_reason). Never raises for a company that simply has no
    rows — that is an answer."""
    from .tools import call_api
    fn = registry.bound(mid, "company")
    if fn is None:
        return None, "no company view"
    raw = call_api(fn, sec_code=code)
    return _block(mid, manifest, raw, compact, limit), None


def compose(code, datasets=None, sections=None, compact=False,
            limit=DEFAULT_LIMIT, coverage_only=False, as_of=None):
    """The composed document. Every block independent; absence reported.

    `as_of` applies to the whole document or to none of it: a company view
    answering half its blocks as of a past date and half as of today would be
    worse than refusing, so the ceiling is set once here and every block reads
    it (app/asof.py).
    """
    with asof.scope(as_of):
        return _compose(code, datasets, sections, compact, limit, coverage_only)


def _compose(code, datasets, sections, compact, limit, coverage_only):
    # EDINET writes a placeholder security code for a filer that has none, and
    # one filing was archived under "0000" before the extractors learned to read
    # that as "no code" (`extract.sec_code_of`). Until those rows are rewritten
    # by the next extraction, a lookup of a plainly invalid code would answer
    # with that filing's data. All zeros is not a code here either.
    if code and not code.strip().strip("0"):
        code = ""
    wanted = [i for i in registry.ids()
              if "company" in registry.get(i)["capabilities"]]
    if datasets:
        unknown = [d for d in datasets if d not in registry.ids()]
        if unknown:
            raise HTTPException(
                404, "Unknown dataset(s) %s. Company datasets: %s"
                     % (", ".join(unknown), ", ".join(wanted)))
        wanted = [i for i in wanted if i in datasets]
    if sections:
        bad = [s for s in sections if s not in registry.SECTION_IDS]
        if bad:
            raise HTTPException(404, "Unknown section(s) %s. Valid: %s"
                                     % (", ".join(bad), ", ".join(registry.SECTION_IDS)))
        wanted = [i for i in wanted if registry.get(i)["section"] in sections]

    blocks, present, missing, errors = {}, [], [], []
    for mid in wanted:
        manifest = registry.get(mid)
        if not registry.available(mid):
            missing.append({"dataset": mid, "reason": "not published on this server"})
            continue
        try:
            block, why = _read(code, mid, manifest, limit, compact)
        except HTTPException as exc:
            if exc.status_code == 404:
                missing.append({"dataset": mid, "reason": exc.detail})
            elif exc.status_code == 503:
                missing.append({"dataset": mid, "reason": "not published on this server"})
            else:
                errors.append({"dataset": mid, "error": exc.detail})
            continue
        except Exception as exc:  # noqa: BLE001 — one block's fault is not the response's
            errors.append({"dataset": mid, "error": str(exc)})
            continue
        if why:
            missing.append({"dataset": mid, "reason": why})
            continue
        block["cite"] = _fill(block["cite"], code)
        block["api"] = _fill(block["api"] or "", code) or None
        blocks[mid] = block
        present.append(mid)

    ordered = [blocks[i] for i in present]
    identity = _identity(code, ordered)
    coverage = {"present": present, "missing": missing, "errors": errors,
                "datasets_with_a_company_view": len(wanted)}
    if coverage_only:
        return {"code": code, "company": identity, "coverage": coverage,
                "vintage": asof.vintage()}

    by_section = []
    for section in registry.SECTIONS:
        ids = [i for i in present if registry.get(i)["section"] == section["id"]]
        if ids:
            by_section.append(dict(section, datasets=ids))
    return {
        "code": code,
        "company": identity,
        "sections": by_section,
        "datasets": blocks,
        "coverage": coverage,
        "vintage": asof.vintage(),
        "provenance": {
            "trust": "official",
            "note": ("Every figure is as filed. Values calculated by this platform are "
                     "listed per block under `calc` with their formula. Blocks are "
                     "different measures — yen book values, voting rights, square "
                     "metres — and are never summed or ranked against one another."),
        },
        "cite": "/company.html?code=%s" % code,
    }


def _csv(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()] or None


def _check(code, as_of):
    code = (code or "").strip()
    if not code or len(code) > CODE_MAX:
        raise HTTPException(400, "Give a securities code, e.g. 7203.")
    return code, asof.parse(as_of)


@router.get("/{code}", openapi_extra={"x-example": "/api/v1/company/7203"})
def company(code: str,
            datasets: str = Query("", description="comma-separated dataset ids"),
            sections: str = Query("", description="comma-separated section ids"),
            compact: int = Query(0, ge=0, le=1,
                                 description="1 = facts and row counts only, no tables"),
            limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                               description="rows per table"),
            as_of: str = Query("", description="YYYY-MM-DD: the filings that "
                                                "existed on EDINET by that date")):
    """Every dataset's view of one company, in section order.

    A dataset with no rows for this company is listed under `coverage.missing`
    with its reason, never dropped and never zero; a dataset that fails is an
    `errors` entry and costs the others nothing.
    """
    code, ceiling = _check(code, as_of)
    return compose(code, datasets=_csv(datasets), sections=_csv(sections),
                   compact=bool(compact), limit=limit, as_of=ceiling)


@router.get("/{code}/coverage", openapi_extra={"x-example": "/api/v1/company/7203/coverage"})
def coverage(code: str,
             as_of: str = Query("", description="YYYY-MM-DD: coverage as it "
                                                 "stood on that date")):
    """Which datasets hold this company and which do not — the matrix alone,
    for deciding what to fetch before fetching it."""
    code, ceiling = _check(code, as_of)
    return compose(code, coverage_only=True, as_of=ceiling)
