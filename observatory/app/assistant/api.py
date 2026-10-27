"""The desk's API: /api/v1/assistant/…

Every route is per-account: it reads the sign-in cookie through
accounts.current_account and answers 401 without one. The namespace is
excluded from the shared response cache by prefix (app/cache.py), for the same
reason /api/v1/account/ is.

Runs started from here (an @mention in the feed, a thread question, "Run now")
execute in a worker thread and the request waits, up to the runner's budget.
Scheduled runs belong to `python -m app.assistant.runner --all`, outside the
serving process.
"""
import json
import datetime
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import accounts, analysis, jp_names, recipes, tools_v2
from . import (codex, delivery, drive, free, gateway, keychain, market_data, mcp_client, memory, runner,
               specialists, store, study_export)

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

_MENTION = re.compile(r"@([a-z][a-z0-9-]*)")
_CODE = re.compile(r"^[0-9A-Z]{4}$")


def _account(request):
    account = accounts.current_account(request)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    return account


def _hire_or_404(account_id, hire_id):
    h = store.hire(account_id, hire_id)
    if h is None:
        raise HTTPException(404, "No such specialist on this desk.")
    return h


def _analyst(account_id):
    """The desk's built-in Analyst, created on first use and kept on the current
    version: it comes with the desk, so nobody hires or updates it."""
    spec = specialists.ANALYST
    h = store.add_hire(account_id, spec["slug"], spec["version"])
    if h["version"] != spec["version"]:
        h = store.update_hire(account_id, h["id"], version=spec["version"])
    return h


def _desk_runs(account_id, limit=10):
    """Specialists' runs, newest first. Chat answers are left out: they are in
    their thread (and the audit), not the desk's record of work done."""
    return [r for r in store.runs(account_id, limit=limit * 5)
            if not specialists.builtin(r["slug"])][:limit]


def _with_spec(h):
    spec = specialists.get(h["slug"])
    out = dict(h)
    out["spec"] = specialists.public(spec) if spec else None
    out["update_available"] = bool(spec and spec["version"] != h["version"])
    return out


# Company-type words and filing marks that are not part of what a person types:
# 株式会社 / （株） / ㈱ before or after the name, and footnote marks such as
# （注）５ that some filers append to a holding's name.
_JA_FORM = re.compile(r"株式会社|（株）|\(株\)|㈱|有限会社|合同会社")
_JA_NOTE = re.compile(r"[（(]注[）)]\s*[0-9０-９]*")


def _clean_ja(name):
    return _JA_NOTE.sub("", str(name or "")).strip()


def _bare(name):
    """A name reduced to what a person would type first: no company-type
    words, no footnote marks, no spaces, lower case."""
    s = _JA_FORM.sub("", _clean_ja(name))
    return re.sub(r"[\s\u3000]+", "", s).lower()


def find_companies(q, limit=8):
    """Companies matching a code, an English or Japanese name, or a market
    nickname (MUFG), best match first. Backed by the equity company index
    (~20 ms), not the all-dataset search (~1.6 s), so it can run per keystroke.

    Ranked: exact code, code prefix, name starting with the query, then the
    index's own order (most-connected companies first)."""
    q = (q or "").strip()
    if not q:
        return []
    try:
        from .. import equity_api
        rows = equity_api.companies(q=q).get("companies") or []
    except Exception:  # noqa: BLE001 — no equity database: fall back below
        rows = []
    if not rows:
        try:
            hits = json.loads(tools_v2.search(q, limit=limit)).get("companies") or []
        except Exception:  # noqa: BLE001
            hits = []
        rows = [{"sec_code": h.get("sec_code"), "name": h.get("name"), "name_en": h.get("name_en")}
                for h in hits if h.get("sec_code") and not str(h.get("sec_code")).startswith("cik:")]
    ql = _bare(q)

    def rank(i_r):
        i, r = i_r
        code = str(r.get("sec_code") or "").lower()
        names = [_bare(r.get("name_en")), _bare(r.get("name"))]
        if code == ql:
            return (0, i)
        if code.startswith(ql):
            return (1, i)
        if any(n.startswith(ql) for n in names):
            return (2, i)
        return (3, i)
    ordered = [r for _, r in sorted(enumerate(rows), key=rank)]
    out = []
    for r in ordered:
        code = r.get("sec_code")
        if not code or len(str(code)) != 4:
            continue
        out.append({"code": code, "name_en": jp_names.display_name((r.get("name_en") or "").strip()) or None,
                    "name_ja": _clean_ja(r.get("name")) or None})
        if len(out) >= limit:
            break
    return out


_US_CODE = re.compile(r"^cik:(\d{1,10})$", re.I)
_US_FORMS = set("inc corp corporation co company ltd plc holdings group sa nv ag the".split())


def find_us_companies(q, limit=8):
    """US listed companies by ticker or name, from the SEC filings the
    platform holds (sec_api.companies), best match first: the exact ticker,
    then a name starting with the query, then the rest. Each is keyed by
    its cik: code, the platform's US namespace. Empty when there is no SEC
    database or no match."""
    q = (q or "").strip()
    if not q:
        return []
    try:
        from .. import sec_api
        rows = sec_api.companies(q=q, exchange="", sic="", limit=60).get("companies") or []
    except Exception:  # noqa: BLE001 — no SEC database here
        return []
    qt, ql = q.upper().replace(".", "-"), _bare(q)

    def plain(name):
        # "APPLE INC" and "NVIDIA CORP/" are what a person calls Apple and Nvidia
        words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
        while words and words[-1] in _US_FORMS:
            words.pop()
        return "".join(words)

    def rank(i_r):
        i, r = i_r
        if (r.get("ticker") or "").upper() == qt or r.get("sec_code") == q.lower():
            return (0, i)
        if plain(r.get("name")) == ql:
            return (0.5, i)
        if _bare(r.get("name")).startswith(ql):
            return (1, -(r.get("filings") or 0), i)
        return (2, -(r.get("filings") or 0), i)
    out = []
    for i_r in sorted(enumerate(rows), key=rank):
        r = i_r[1]
        if not r.get("ticker"):
            continue                      # a filer with no listed shares
        out.append({"code": r["sec_code"], "name_en": (r.get("name") or "").strip() or None,
                    "name_ja": None, "ticker": r["ticker"], "exchange": r.get("exchange"),
                    "market": "US", "exact": rank(i_r)[0] <= 0.5})
        if len(out) >= limit:
            break
    return out


def _company_name(code):
    """The English name for a securities code. None when the platform does
    not know the code."""
    for c in find_companies(code, limit=3):
        if c["code"] == code:
            return c["name_en"] or c["name_ja"]
    try:
        hits = json.loads(tools_v2.search(code, limit=3)).get("companies") or []
    except Exception:  # noqa: BLE001 — a name is a nicety, never a blocker
        return None
    for h in hits:
        if h.get("sec_code") == code:
            return h.get("name_en") or h.get("name")
    return None


# ------------------------------------------------------------------- desk

@router.get("/status")
def status(request: Request):
    """Whether the caller has a desk. 200 always: the page reads 404 on this
    path as 'the assistant is not switched on', which is a different thing."""
    account = accounts.current_account(request)
    return {"enabled": True, "signed_in": account is not None,
            "email": account["email"] if account else None,
            "keychain": keychain.enabled()}


@router.get("/desk")
def desk(request: Request):
    account = _account(request)
    aid = account["id"]
    return {"email": account["email"],
            "desk": store.desk(aid),
            "hires": [_with_spec(h) for h in store.hires(aid) if not specialists.builtin(h["slug"])],
            "analyst": _with_spec(_analyst(aid)),
            "coverage": store.coverage(aid),
            "lists": store.lists(aid),
            "pending_approvals": len(store.approvals(aid, "pending")),
            "runs": _desk_runs(aid),
            "keychain": keychain.enabled()}


# ------------------------------------------------------------ specialists

@router.get("/specialists")
def catalogue(request: Request):
    aid = _account(request)["id"]
    mine = dict((h["slug"], h) for h in store.hires(aid))
    out = []
    for spec in specialists.CATALOGUE:
        card = specialists.public(spec)
        h = mine.get(spec["slug"])
        card["hired"] = h is not None
        card["hire_id"] = h["id"] if h else None
        card["hired_version"] = h["version"] if h else None
        card["update_available"] = bool(h and h["version"] != spec["version"])
        out.append(card)
    return {"specialists": out}


@router.post("/specialists/{slug}/hire")
def hire(slug: str, request: Request):
    aid = _account(request)["id"]
    spec = specialists.get(slug)
    if spec is None:
        raise HTTPException(404, "No such specialist.")
    h = store.add_hire(aid, slug, spec["version"])
    store.add_post(aid, "system", "%s joined the desk (v%s)." % (spec["name"], spec["version"]),
                   hire_id=h["id"])
    return _with_spec(h)


@router.post("/hires/{hire_id}/update")
def accept_update(hire_id: int, request: Request):
    aid = _account(request)["id"]
    h = _hire_or_404(aid, hire_id)
    spec = specialists.get(h["slug"])
    return _with_spec(store.update_hire(aid, hire_id, version=spec["version"]))


@router.delete("/hires/{hire_id}")
def remove_hire(hire_id: int, request: Request):
    aid = _account(request)["id"]
    h = _hire_or_404(aid, hire_id)
    if specialists.builtin(h["slug"]):
        raise HTTPException(400, "The Analyst comes with every desk and cannot be removed.")
    store.remove_hire(aid, hire_id)
    return {"removed": hire_id}


class RunRequest(BaseModel):
    task: str = ""


@router.post("/hires/{hire_id}/run")
async def run_now(hire_id: int, payload: RunRequest, request: Request):
    aid = _account(request)["id"]
    h = _hire_or_404(aid, hire_id)
    spec = specialists.get(h["slug"])
    task = (payload.task or "").strip() or spec["task"]
    return await run_in_threadpool(runner.run, aid, h, "manual", task)


# --------------------------------------------------------------- coverage

class CoverageRequest(BaseModel):
    code: str
    list_id: int = 0


@router.get("/companies")
def companies(request: Request, q: str = "", list_id: int = 0, markets: str = ""):
    """Type-ahead for the coverage box: name (English or Japanese), code or
    nickname in, up to eight companies out, each marked if already on the list.
    markets=all adds US listed companies (Your Plover's box); the desk's own
    coverage tools stay Japanese."""
    aid = _account(request)["id"]
    covered = set(c["sec_code"] for c in store.coverage(aid, list_id or None))
    out = find_companies(q)
    if markets == "all":
        for c in out:
            c["market"] = "JP"
        us = find_us_companies(q)
        exact = [c for c in us if c.pop("exact")]
        for c in us:
            c.pop("exact", None)
        # the US company the words name exactly (AAPL, "Apple") first, then
        # Japan's best, then the rest of the US
        out = (exact + out + [c for c in us if c not in exact])[:8]
    for c in out:
        c["covered"] = c["code"] in covered
    return {"companies": out}


def _list_or_404(aid, list_id):
    if not list_id:
        return None
    if store.list_row(aid, list_id) is None:
        raise HTTPException(404, "No such list on this desk.")
    return list_id


def _company(code):
    """(English name, Japanese name) for a code, or (None, None)."""
    for c in find_companies(code, limit=3):
        if c["code"] == code:
            return c["name_en"] or c["name_ja"], c["name_ja"]
    name = _company_name(code)
    return name, None


@router.get("/coverage")
def coverage(request: Request, list_id: int = 0):
    aid = _account(request)["id"]
    lid = store._resolve(aid, _list_or_404(aid, list_id))
    rows = store.coverage(aid, lid)
    # Entries saved before Japanese names were kept get theirs once, here.
    for r in rows:
        if not str(r["sec_code"]).startswith("cik:"):
            r["name"] = jp_names.display_name(r.get("name"))   # saved as filed, often in capitals
        if not r.get("name_ja") and not str(r["sec_code"]).startswith("cik:"):
            _en, ja = _company(r["sec_code"])
            if ja:
                store.set_name_ja(lid, r["sec_code"], ja)
                r["name_ja"] = ja
    return {"coverage": rows, "list_id": lid}


@router.post("/coverage")
def add_coverage(payload: CoverageRequest, request: Request):
    aid = _account(request)["id"]
    lid = _list_or_404(aid, payload.list_id)
    code = (payload.code or "").strip().upper()
    us = _US_CODE.match(code)
    if us:
        code = "cik:%d" % int(us.group(1))
        hit = [c for c in find_us_companies(code, limit=3) if c["code"] == code]
        if not hit:
            raise HTTPException(404, "The platform holds no SEC filings for that company.")
        return {"coverage": store.add_coverage(aid, code, hit[0]["name_en"], lid, None)}
    if not _CODE.match(code):
        raise HTTPException(400, "A securities code is four characters, such as 7203.")
    name, name_ja = _company(code)
    if name is None:
        raise HTTPException(404, "The platform holds no filings for %s." % code)
    return {"coverage": store.add_coverage(aid, code, name, lid, name_ja)}


@router.delete("/coverage/{code}")
def remove_coverage(code: str, request: Request, list_id: int = 0):
    aid = _account(request)["id"]
    us = _US_CODE.match(code.strip())
    code = "cik:%d" % int(us.group(1)) if us else code.upper()
    return {"coverage": store.remove_coverage(aid, code, _list_or_404(aid, list_id))}


# ---------------------------------------------------------- coverage lists

class ListRequest(BaseModel):
    name: str = ""


@router.get("/lists")
def lists(request: Request):
    aid = _account(request)["id"]
    return {"lists": store.lists(aid),
            "watched_by": dict((h["id"], (h["config"] or {}).get("list_id")) for h in store.hires(aid))}


@router.post("/lists")
def create_list(payload: ListRequest, request: Request):
    aid = _account(request)["id"]
    if not (payload.name or "").strip():
        raise HTTPException(400, "Give the list a name.")
    if len(store.lists(aid)) >= 50:
        raise HTTPException(400, "A desk can hold up to 50 lists.")
    return store.create_list(aid, payload.name)


@router.patch("/lists/{list_id}")
def rename_list(list_id: int, payload: ListRequest, request: Request):
    aid = _account(request)["id"]
    _list_or_404(aid, list_id)
    if not (payload.name or "").strip():
        raise HTTPException(400, "Give the list a name.")
    return store.rename_list(aid, list_id, payload.name)


@router.delete("/lists/{list_id}")
def delete_list(list_id: int, request: Request):
    aid = _account(request)["id"]
    row = store.list_row(aid, list_id)
    if row is None:
        raise HTTPException(404, "No such list on this desk.")
    if row["is_default"]:
        raise HTTPException(400, "The main Coverage list cannot be deleted.")
    # a monitor watching the list falls back to the main list
    for h in store.hires(aid):
        if (h["config"] or {}).get("list_id") == list_id:
            cfg = dict(h["config"]); cfg.pop("list_id", None)
            store.update_hire(aid, h["id"], config=cfg)
    store.delete_list(aid, list_id)
    return {"deleted": list_id}


class HireConfigRequest(BaseModel):
    list_id: int = 0


@router.post("/hires/{hire_id}/config")
def configure_hire(hire_id: int, payload: HireConfigRequest, request: Request):
    """Which list a specialist watches; 0 means the main Coverage list."""
    aid = _account(request)["id"]
    h = _hire_or_404(aid, hire_id)
    cfg = dict(h["config"] or {})
    if payload.list_id:
        _list_or_404(aid, payload.list_id)
        cfg["list_id"] = payload.list_id
    else:
        cfg.pop("list_id", None)
    return _with_spec(store.update_hire(aid, hire_id, config=cfg))


# ------------------------------------------------------------------- feed

class MessageRequest(BaseModel):
    text: str


@router.get("/feed")
def feed(request: Request):
    aid = _account(request)["id"]
    posts = store.posts(aid)
    every = store.approvals(aid)
    by_id = dict((a["id"], a) for a in every)
    # A specialist's approval belongs to its run; an outside connection's to
    # the one post it made alongside it (its day-long run holds many).
    by_run = dict((a["run_id"], a) for a in every if a["run_id"] and a["hire_id"])
    # A run's approval and tool trail are shown once, on its last post.
    last_of_run = {}
    for p in posts:
        if p["run_id"] and not p["author"].startswith("ext:"):
            last_of_run[p["run_id"]] = p["id"]
    for p in posts:
        if p.get("approval_id"):
            a = by_id.get(p["approval_id"])
        elif last_of_run.get(p["run_id"]) == p["id"]:
            a = by_run.get(p["run_id"])
        else:
            a = None
        p["approval"] = ({"id": a["id"], "status": a["status"], "text": a["payload"].get("text")}
                         if a else None)
        p["calls"] = (store.calls(p["run_id"])
                      if p["run_id"] and last_of_run.get(p["run_id"]) == p["id"] else [])
        p["charts"] = []
    by_post = {}
    for rid in set(p["run_id"] for p in posts if p["run_id"]):
        for c in store.charts_for_run(rid):
            if c["post_id"]:
                by_post.setdefault(c["post_id"], []).append(c)
    for p in posts:
        p["charts"] = by_post.get(p["id"], [])
    return {"posts": posts}


@router.post("/feed")
async def post_to_feed(payload: MessageRequest, request: Request):
    aid = _account(request)["id"]
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Say something.")
    mine = store.add_post(aid, "me", text)
    m = _MENTION.search(text)
    h = store.hire_by_slug(aid, m.group(1)) if m else None
    if h is None:
        return {"post": mine, "run": None,
                "note": "No specialist on the desk was mentioned. Type @ to bring one in."}
    task = _MENTION.sub("", text, count=1).strip() or specialists.get(h["slug"])["task"]
    result = await run_in_threadpool(runner.run, aid, h, "message", task)
    return {"post": mine, "run": result}


# ------------------------------------------------------------------ inbox

@router.get("/inbox")
def inbox(request: Request):
    aid = _account(request)["id"]
    return {"pending": store.approvals(aid, "pending"),
            "decided": [a for a in store.approvals(aid) if a["status"] != "pending"][:20],
            "runs": _desk_runs(aid)}


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: int, request: Request):
    aid = _account(request)["id"]
    a = store.approval(aid, approval_id)
    if a is None or a["status"] != "pending":
        raise HTTPException(404, "No pending approval with that id.")
    if a["action"] != "slack":
        raise HTTPException(400, "Unknown action.")
    secrets = store.desk_secrets(aid)
    try:
        hook = keychain.open_(secrets["slack_webhook_ct"]) if secrets["slack_webhook_ct"] else ""
        delivery.slack(hook, a["payload"]["text"])
    except (delivery.DeliveryError, ValueError, RuntimeError) as exc:
        raise HTTPException(502, "Approved, but delivery failed: %s" % exc)
    out = store.decide_approval(aid, approval_id, "approved", result="sent to slack")
    store.add_post(aid, "system", "You approved a post; it was sent to Slack.",
                   hire_id=a["hire_id"], run_id=a["run_id"])
    return out


@router.post("/approvals/{approval_id}/decline")
def decline(approval_id: int, request: Request):
    aid = _account(request)["id"]
    a = store.approval(aid, approval_id)
    if a is None or a["status"] != "pending":
        raise HTTPException(404, "No pending approval with that id.")
    return store.decide_approval(aid, approval_id, "declined", result="nothing sent")


# ---------------------------------------------------------------- threads

class ThreadRequest(BaseModel):
    hire_id: int
    text: str


@router.get("/threads")
def threads(request: Request):
    return {"threads": store.threads(_account(request)["id"])}


@router.get("/threads/{thread_id}")
def thread(thread_id: int, request: Request):
    t = store.thread(_account(request)["id"], thread_id)
    if t is None:
        raise HTTPException(404, "No such thread.")
    return t


@router.post("/threads")
async def new_thread(payload: ThreadRequest, request: Request):
    aid = _account(request)["id"]
    h = _hire_or_404(aid, payload.hire_id)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Ask something.")
    t = store.add_thread(aid, h["id"], text.rstrip("?.!"))
    return await _ask(aid, h, t["id"], text)


@router.post("/threads/{thread_id}/messages")
async def ask_in_thread(thread_id: int, payload: MessageRequest, request: Request):
    aid = _account(request)["id"]
    t = store.thread(aid, thread_id)
    if t is None:
        raise HTTPException(404, "No such thread.")
    h = _hire_or_404(aid, t["hire_id"])
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Ask something.")
    return await _ask(aid, h, thread_id, text)


# A series search matches a company's name to places and funds of the same
# name (Toyota: Toyota City's guest nights), so the model is told when to
# ignore the list.
CHAT_HINTS_NOTE = ("If the question is about a company, ignore the list below and use get_company. ")


async def _ask(aid, h, thread_id, text):
    before = store.thread(aid, thread_id)
    history = [{"role": m["role"], "text": m["text"]} for m in before["messages"]]
    store.add_message(thread_id, "user", text)
    task = text
    if h["slug"] == specialists.ANALYST["slug"]:
        # The series the question seems to name, found by code before the
        # model starts, as Studies do: the lookups a model spends finding a
        # code (2026-10-23: three of six calls on "core inflation") re-send the
        # whole conversation each time.
        hints = await run_in_threadpool(analysis.suggest_series, text, 6, 2)
        if hints:
            task = text + "\n\n" + CHAT_HINTS_NOTE + analysis.hints_text(hints)
    result = await run_in_threadpool(runner.run, aid, h, "message", task, history, "thread", True)
    reply = result["text"] or ("I could not finish: %s" % (result["error"] or result["outcome"]))
    store.add_message(thread_id, "assistant", reply, run_id=result["run_id"])
    out = store.thread(aid, thread_id)
    out["run"] = result
    return out


# ------------------------------------------------------------ runs, audit

@router.get("/runs")
def runs(request: Request):
    return {"runs": store.runs(_account(request)["id"], limit=100)}


@router.get("/runs/{run_id}")
def one_run(run_id: int, request: Request):
    r = store.run(_account(request)["id"], run_id)
    if r is None:
        raise HTTPException(404, "No such run.")
    return r


@router.get("/audit")
def audit(request: Request):
    aid = _account(request)["id"]
    rs = store.runs(aid, limit=200)
    return {"tools": store.tool_usage(aid),
            "runs": rs[:50],
            "months": store.monthly_usage(aid),
            "counts": {"runs": len(rs),
                       "tool_calls": sum(r["tool_calls"] for r in rs),
                       "pending": len(store.approvals(aid, "pending")),
                       "sent": len([a for a in store.approvals(aid) if a["status"] == "approved"])}}


@router.get("/files")
def files(request: Request):
    return {"files": store.files(_account(request)["id"])}


@router.get("/files/{hire_id}/{path:path}")
def one_file(hire_id: int, path: str, request: Request):
    f = store.read_file(_account(request)["id"], hire_id, path)
    if f is None:
        raise HTTPException(404, "No such file.")
    return f


# --------------------------------------------------------------- settings

class SettingsRequest(BaseModel):
    model_provider: str = ""
    model_name: str = ""
    monitor_model: str = ""
    study_model: str = ""
    model_key: str = ""
    model_base_url: str = ""
    slack_webhook: str = ""
    slack_label: str = ""
    policy: dict = {}


@router.get("/settings")
def settings(request: Request):
    aid = _account(request)["id"]
    d = store.desk(aid)
    d["providers"] = [{"id": k, "label": v["label"], "default_model": v["default_model"]}
                      for k, v in gateway.PROVIDERS.items()]
    # Codex runs on the desk's own ChatGPT sign-in rather than a key; offered
    # only where the binary is installed.
    d["codex"] = codex.status(aid)
    if d["codex"]["available"]:
        d["providers"].append({"id": codex.PROVIDER, "label": codex.LABEL, "default_model": ""})
    d["keychain"] = keychain.enabled()
    d["free"] = free.status(aid)
    return d


@router.post("/settings")
def save_settings(payload: SettingsRequest, request: Request):
    aid = _account(request)["id"]
    fields = {}
    if payload.model_provider == codex.PROVIDER:
        if not store.desk(aid)["codex_connected"]:
            raise HTTPException(400, "Connect your ChatGPT account first.")
    if payload.model_provider:
        if payload.model_provider not in gateway.PROVIDERS and payload.model_provider != codex.PROVIDER:
            raise HTTPException(400, "Unknown provider.")
        fields["model_provider"] = payload.model_provider
        fields["model_name"] = (payload.model_name or "").strip() or \
            gateway.default_model(payload.model_provider)
    elif payload.model_name:
        fields["model_name"] = payload.model_name.strip()
    # saving the model section sets the monitor model too; empty clears it
    if "model_provider" in fields or payload.monitor_model:
        fields["monitor_model"] = (payload.monitor_model or "").strip() or None
    if "model_provider" in fields or payload.study_model:
        fields["study_model"] = (payload.study_model or "").strip() or None
    if payload.model_key:
        if not keychain.enabled():
            raise HTTPException(503, "ASSISTANT_SECRET is not set on this server, so a key "
                                     "cannot be stored.")
        fields["model_key_ct"] = keychain.seal(payload.model_key.strip())
        fields["model_key_last4"] = keychain.last4(payload.model_key)
    if payload.slack_webhook:
        if not keychain.enabled():
            raise HTTPException(503, "ASSISTANT_SECRET is not set on this server.")
        if not payload.slack_webhook.startswith("https://hooks.slack.com/"):
            raise HTTPException(400, "A Slack incoming webhook starts with https://hooks.slack.com/")
        fields["slack_webhook_ct"] = keychain.seal(payload.slack_webhook.strip())
        fields["slack_label"] = (payload.slack_label or "").strip() or "Slack"
    elif payload.slack_label:
        fields["slack_label"] = payload.slack_label.strip()
    if payload.policy:
        fields["policy_json"] = json.dumps(payload.policy)
    return store.update_desk(aid, **fields)


@router.get("/settings/codex")
def codex_status(request: Request):
    return codex.status(_account(request)["id"])


@router.post("/settings/codex/login")
def codex_login(request: Request):
    """Start Codex's sign-in: a link and a one-time code for OpenAI's page."""
    try:
        return codex.start_login(_account(request)["id"])
    except codex.CodexError as exc:
        raise HTTPException(503, str(exc))


@router.delete("/settings/codex")
def codex_logout(request: Request):
    aid = _account(request)["id"]
    codex.logout(aid)
    return codex.status(aid)


class KeyTestRequest(BaseModel):
    model_provider: str = ""
    model_key: str = ""


@router.post("/settings/test")
async def test_model(request: Request, payload: KeyTestRequest = None):
    """Checks the stored key, or — with a provider and key in the body — a
    key not yet stored, so a wrong one is never saved."""
    aid = _account(request)["id"]
    if payload and payload.model_key.strip():
        if payload.model_provider not in gateway.PROVIDERS:
            raise HTTPException(400, "Unknown provider.")
        try:
            reply = await run_in_threadpool(gateway.ping, payload.model_provider, payload.model_key.strip(),
                                            gateway.default_model(payload.model_provider))
        except gateway.GatewayError as exc:
            raise HTTPException(502, str(exc))
        return {"ok": True, "reply": reply}
    d = store.desk(aid)
    if d["model_provider"] == codex.PROVIDER:
        raise HTTPException(400, "ChatGPT has no key to test; ask the Analyst a question instead.")
    if not d["model_provider"] or not d["model_key_set"]:
        raise HTTPException(400, "Choose a provider and store a key first.")
    try:
        key = keychain.open_(store.desk_secrets(aid)["model_key_ct"])
        reply = await run_in_threadpool(gateway.ping, d["model_provider"], key,
                                        d["model_name"] or gateway.default_model(d["model_provider"]))
    except (gateway.GatewayError, ValueError, RuntimeError) as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "reply": reply}


# ------------------------------------------------------------ connections

class ConnectionRequest(BaseModel):
    label: str = ""


def _endpoint(request):
    import os
    base = (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")
    return base + "/mcp/desk"


@router.get("/connections")
def connections(request: Request):
    aid = _account(request)["id"]
    return {"endpoint": _endpoint(request),
            "connections": [t for t in store.tokens(aid) if not t["revoked_at"]]}


@router.post("/connections")
def create_connection(payload: ConnectionRequest, request: Request):
    """A new personal key. The raw key is in this response and nowhere else."""
    aid = _account(request)["id"]
    label = (payload.label or "").strip() or "Connection"
    t = store.add_token(aid, label)
    t["endpoint"] = _endpoint(request)
    return t


@router.delete("/connections/{token_id}")
def revoke_connection(token_id: int, request: Request):
    aid = _account(request)["id"]
    t = store.revoke_token(aid, token_id)
    if t is None:
        raise HTTPException(404, "No such connection.")
    return t


# ------------------------------------------------------------------- keys
# Named API keys, sealed by the keychain. Nothing here ever returns a key: a
# stored key leaves this server only inside a request to the service it
# belongs to.


class SecretRequest(BaseModel):
    name: str = ""
    value: str = ""
    note: str = ""


def _need_keychain():
    if not keychain.enabled():
        raise HTTPException(503, "This server cannot store keys yet: ASSISTANT_SECRET is not set.")


@router.get("/secrets")
def secrets(request: Request):
    aid = _account(request)["id"]
    d = store.desk(aid)
    return {"secrets": store.secrets(aid),
            "keychain": keychain.enabled(),
            # the two keys Settings owns, shown here so one page lists them all
            "model_key": {"set": d["model_key_set"], "last4": d["model_key_last4"],
                          "provider": d["model_provider"]},
            "slack": {"set": d["slack_set"], "label": d["slack_label"]},
            "data_sources": [dict(s, name=market_data.PROVIDERS.get(s["provider"], {}).get("name", s["provider"]))
                             for s in store.data_sources(aid)]}


@router.post("/secrets")
def add_secret(payload: SecretRequest, request: Request):
    aid = _account(request)["id"]
    _need_keychain()
    value = (payload.value or "").strip()
    if not (payload.name or "").strip():
        raise HTTPException(400, "Give the key a name, so you can tell it apart later.")
    if not value:
        raise HTTPException(400, "Paste the key.")
    if len(store.secrets(aid)) >= 50:
        raise HTTPException(400, "A desk can hold up to 50 keys.")
    return store.add_secret(aid, payload.name, keychain.seal(value), keychain.last4(value),
                            note=(payload.note or "").strip() or None)


@router.patch("/secrets/{secret_id}")
def update_secret(secret_id: int, payload: SecretRequest, request: Request):
    aid = _account(request)["id"]
    if store.secret(aid, secret_id) is None:
        raise HTTPException(404, "No such key.")
    ct = last4 = None
    if (payload.value or "").strip():
        _need_keychain()
        ct = keychain.seal(payload.value.strip())
        last4 = keychain.last4(payload.value)
    return store.update_secret(aid, secret_id, name=(payload.name or "").strip() or None,
                               ct=ct, last4=last4,
                               note=payload.note if payload.note != "" else None)


@router.delete("/secrets/{secret_id}")
def delete_secret(secret_id: int, request: Request):
    aid = _account(request)["id"]
    if store.secret(aid, secret_id) is None:
        raise HTTPException(404, "No such key.")
    if not store.delete_secret(aid, secret_id):
        raise HTTPException(400, "A server still uses this key. Change that server first.")
    return {"deleted": secret_id}


# ------------------------------------------------------------ MCP servers


class McpServerRequest(BaseModel):
    label: str = ""
    url: str = ""
    auth_kind: str = "none"
    header_name: str = ""
    secret_id: int = 0
    enabled: bool = True


def _check_server(aid, srv):
    """Connect, remember the tools, record what happened. Never raises."""
    secret = None
    if srv.get("secret_id"):
        try:
            secret = keychain.open_(store.secret_ct(aid, srv["secret_id"]) or "")
        except (ValueError, RuntimeError):
            return store.update_mcp_server(aid, srv["id"], checked_at=store.now(), status="error",
                                           detail="The stored key could not be read.")
    try:
        info, tools = mcp_client.connect(srv, secret)
    except mcp_client.RemoteError as exc:
        return store.update_mcp_server(aid, srv["id"], checked_at=store.now(), status="error",
                                       detail=str(exc))
    return store.update_mcp_server(
        aid, srv["id"], checked_at=store.now(), status="ok",
        detail=(info.get("name") or "") + (" " + info.get("version") if info.get("version") else ""),
        tools_json=json.dumps(tools, ensure_ascii=False))


@router.get("/mcp-servers")
def mcp_servers(request: Request):
    aid = _account(request)["id"]
    return {"servers": store.mcp_servers(aid), "secrets": store.secrets(aid),
            "keychain": keychain.enabled()}


@router.post("/mcp-servers")
async def add_mcp_server(payload: McpServerRequest, request: Request):
    aid = _account(request)["id"]
    if not (payload.label or "").strip():
        raise HTTPException(400, "Give the server a name.")
    try:
        url = mcp_client.check_url(payload.url)
    except mcp_client.RemoteError as exc:
        raise HTTPException(400, str(exc))
    if payload.auth_kind not in ("none", "bearer", "header"):
        raise HTTPException(400, "Unknown authentication kind.")
    if payload.auth_kind != "none":
        if not payload.secret_id or store.secret(aid, payload.secret_id) is None:
            raise HTTPException(400, "Choose a stored key for this server, or set no authentication.")
    if len(store.mcp_servers(aid)) >= 20:
        raise HTTPException(400, "A desk can hold up to 20 servers.")
    srv = store.add_mcp_server(aid, payload.label, url, payload.auth_kind,
                               (payload.header_name or "").strip() or None,
                               payload.secret_id or None)
    return await run_in_threadpool(_check_server, aid, srv)


@router.patch("/mcp-servers/{server_id}")
def update_mcp_server(server_id: int, payload: McpServerRequest, request: Request):
    aid = _account(request)["id"]
    if store.mcp_server(aid, server_id) is None:
        raise HTTPException(404, "No such server.")
    return store.update_mcp_server(aid, server_id, enabled=1 if payload.enabled else 0)


@router.post("/mcp-servers/{server_id}/check")
async def check_mcp_server(server_id: int, request: Request):
    aid = _account(request)["id"]
    srv = store.mcp_server(aid, server_id)
    if srv is None:
        raise HTTPException(404, "No such server.")
    return await run_in_threadpool(_check_server, aid, srv)


@router.delete("/mcp-servers/{server_id}")
def delete_mcp_server(server_id: int, request: Request):
    aid = _account(request)["id"]
    if store.mcp_server(aid, server_id) is None:
        raise HTTPException(404, "No such server.")
    store.delete_mcp_server(aid, server_id)
    return {"deleted": server_id}


# ----------------------------------------------------------- data sources
# The person's own market-data accounts (market_data.py), the same ones the
# Writer Desk shows: they belong to the sign-in account, not to this desk.


class DataSourceRequest(BaseModel):
    provider: str = ""
    key: str = ""


# what the person's assistant remembers about them (shared with the Writer Desk)
memory.mount(router, lambda request: _account(request)["id"])


class DataSourceUpdate(BaseModel):
    enabled: bool = True


class TryRequest(BaseModel):
    symbol: str = ""
    start: str = ""
    end: str = ""


def _source_or_404(aid, source_id):
    src = store.data_source(aid, source_id)
    if src is None:
        raise HTTPException(404, "No such data source.")
    return src


@router.get("/data-sources")
def data_sources(request: Request):
    return market_data.view(_account(request)["id"])


@router.post("/data-sources")
async def connect_data_source(payload: DataSourceRequest, request: Request):
    aid = _account(request)["id"]
    try:
        return await run_in_threadpool(market_data.connect, aid, (payload.provider or "").strip().lower(),
                                       payload.key)
    except market_data.SourceError as exc:
        raise HTTPException(503 if "ASSISTANT_SECRET" in str(exc) else 400, str(exc))


@router.patch("/data-sources/{source_id}")
def update_data_source(source_id: int, payload: DataSourceUpdate, request: Request):
    aid = _account(request)["id"]
    _source_or_404(aid, source_id)
    return store.update_data_source(aid, source_id, enabled=1 if payload.enabled else 0)


@router.post("/data-sources/{source_id}/try")
async def try_data_source(source_id: int, payload: TryRequest, request: Request):
    """Prices for one symbol, so a person sees the connection work."""
    aid = _account(request)["id"]
    src = _source_or_404(aid, source_id)
    try:
        return await run_in_threadpool(market_data.try_source, aid, src, payload.symbol,
                                       payload.start, payload.end)
    except market_data.SourceError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/data-sources/{source_id}")
def delete_data_source(source_id: int, request: Request):
    aid = _account(request)["id"]
    _source_or_404(aid, source_id)
    store.delete_data_source(aid, source_id)
    return {"deleted": source_id}


# ------------------------------------------------------------------ drive
# My drive (files kept on the desk) and the person's Google Drive, read-only.
# See drive.py for what is stored and what Google is asked for.

def _drive_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except drive.DriveError as exc:
        raise HTTPException(400, str(exc))


def _base(request):
    import os
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")


@router.get("/drive")
def drive_view(request: Request, view: str = "mydrive", folder: int = 0):
    aid = _account(request)["id"]
    out = {"view": view, "usage": drive.usage(aid), "google": drive.google_status(aid)}
    if view == "recent":
        out["items"] = drive.recent(aid)
    elif view == "trash":
        out["items"] = drive.trash(aid)
    else:
        out["items"] = _drive_call(drive.children, aid, folder or None)
        out["path"] = _drive_call(drive.path_of, aid, folder or None)
    return out


class FolderRequest(BaseModel):
    name: str
    parent_id: int = 0


@router.post("/drive/folders")
def drive_new_folder(payload: FolderRequest, request: Request):
    aid = _account(request)["id"]
    return _drive_call(drive.make_folder, aid, payload.name, payload.parent_id or None)


@router.post("/drive/upload")
async def drive_upload(request: Request, name: str, parent_id: int = 0):
    """The file is the raw request body; its name comes in the query."""
    aid = _account(request)["id"]
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > drive.MAX_FILE_BYTES:
        raise HTTPException(413, "Files can be up to 25 MB.")
    data = await request.body()
    return await run_in_threadpool(_drive_call, drive.upload, aid, name, data,
                                   request.headers.get("content-type"), parent_id or None)


class RenameRequest(BaseModel):
    name: str


@router.patch("/drive/items/{item_id}")
def drive_rename(item_id: int, payload: RenameRequest, request: Request):
    aid = _account(request)["id"]
    return _drive_call(drive.rename, aid, item_id, payload.name)


@router.post("/drive/items/{item_id}/trash")
def drive_trash(item_id: int, request: Request):
    aid = _account(request)["id"]
    _drive_call(drive.move_to_trash, aid, item_id)
    return {"trashed": item_id}


@router.post("/drive/items/{item_id}/restore")
def drive_restore(item_id: int, request: Request):
    aid = _account(request)["id"]
    _drive_call(drive.restore, aid, item_id)
    return {"restored": item_id}


@router.delete("/drive/items/{item_id}")
def drive_delete(item_id: int, request: Request):
    aid = _account(request)["id"]
    _drive_call(drive.delete_forever, aid, item_id)
    return {"deleted": item_id}


@router.get("/drive/items/{item_id}/download")
def drive_download(item_id: int, request: Request):
    from urllib.parse import quote
    aid = _account(request)["id"]
    r = drive.content(aid, item_id)
    if r is None:
        raise HTTPException(404, "No such file.")
    return Response(bytes(r["content"] or b""), media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(r["name"]),
                             "X-Content-Type-Options": "nosniff"})


@router.get("/google/status")
def google_status(request: Request):
    aid = _account(request)["id"]
    out = drive.google_status(aid)
    out["redirect_uri"] = drive.redirect_uri(_base(request))
    return out


@router.get("/google/connect")
def google_connect(request: Request):
    aid = _account(request)["id"]
    try:
        return RedirectResponse(drive.auth_url(aid, _base(request)), status_code=302)
    except drive.DriveError as exc:
        return RedirectResponse("/assistant.html?google_error=" + _quote(str(exc)) + "#/drive/cloud", status_code=302)


def _quote(s):
    from urllib.parse import quote
    return quote(s[:300], safe="")


@router.get("/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    account = accounts.current_account(request)
    back = "/assistant.html%s#/drive/cloud"
    if account is None:
        return RedirectResponse(back % ("?google_error=" + _quote("Sign in to your desk first, then connect again.")), status_code=302)
    if error:
        msg = "You cancelled the Google sign-in." if error == "access_denied" else "Google said: " + error
        return RedirectResponse(back % ("?google_error=" + _quote(msg)), status_code=302)
    if not drive.check_state(state, account["id"]):
        return RedirectResponse(back % ("?google_error=" + _quote("That sign-in link expired or was not yours. Try again.")), status_code=302)
    try:
        await run_in_threadpool(drive.finish, account["id"], code, _base(request))
    except drive.DriveError as exc:
        return RedirectResponse(back % ("?google_error=" + _quote(str(exc))), status_code=302)
    return RedirectResponse(back % "?google=connected", status_code=302)


@router.delete("/google")
async def google_disconnect(request: Request):
    aid = _account(request)["id"]
    await run_in_threadpool(drive.disconnect, aid)
    return drive.google_status(aid)


@router.get("/google/files")
async def google_files(request: Request, view: str = "mydrive", folder: str = "", q: str = "", page: str = ""):
    aid = _account(request)["id"]
    return await run_in_threadpool(_drive_call, drive.google_list, aid, view, folder or None, q or None, page or None)


# ----------------------------------------------------------------- studies
# A study is an analysis the person keeps (store.add_study): asked in plain
# English of the Study Analyst, or written and run by hand. Either way the
# work happens on the server (app/analysis.py): the AI reads a preview and the
# figures it asked for, never the full rows, and a hand-made run costs no AI
# at all. Every run is kept; a new one never changes an earlier one.

class StudyRequest(BaseModel):
    title: str = ""
    question: str = ""


class StudyEdit(BaseModel):
    title: Optional[str] = None
    question: Optional[str] = None
    refresh: Optional[str] = None       # "daily", "weekly" or "" for never
    # the most tokens one question on this study may spend; 0 for the server's cap
    token_budget: Optional[int] = None


BUDGET_MIN = 1000


class StudyRun(BaseModel):
    spec: dict


class StudyAsk(BaseModel):
    question: str
    # Your Plover's chat asks the US stock box first; the desk's Studies,
    # which draw a query's rows, do not
    stocks: bool = False


class StudyRecipe(BaseModel):
    recipe: str
    series: dict = {}
    start: str = ""
    end: str = ""
    monthly: str = ""
    options: dict = {}
    title: str = ""


def _title_from(question, limit=100):
    """A question as a title: whole words, with … when it had to be cut."""
    q = " ".join((question or "").split())
    if len(q) <= limit:
        return q
    cut = q[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "\u2026"


def _study_or_404(aid, study_id):
    st = store.study(aid, study_id)
    if st is None:
        raise HTTPException(404, "No such study.")
    return st


def _study_hire(aid):
    """The Study Analyst, made on first use like the chat Analyst."""
    spec = specialists.STUDY
    h = store.add_hire(aid, spec["slug"], spec["version"])
    if h["version"] != spec["version"]:
        h = store.update_hire(aid, h["id"], version=spec["version"])
    return h


def _run_view(run):
    """A kept run as the page shows it, with its chart worked out from the rows."""
    if run is None:
        return None
    res = run["result"]
    out = {"id": run["id"], "by": run["by"], "question": run["question"], "note": run["note"],
           "created_at": run["created_at"], "tokens": run["tokens"], "spec": run["spec"],
           "columns": res.get("columns") or [], "rows": res.get("rows") or [],
           "rows_total": res.get("rows_total", len(res.get("rows") or [])),
           "sources": [dict((k, s.get(k)) for k in ("name", "series", "title", "unit", "release", "credit"))
                       for s in res.get("sources") or []],
           "summary": res.get("summary"), "ran_at": res.get("ran_at"), "chart": None, "chart_error": None,
           "stock": res.get("stock")}
    try:
        out["chart"] = analysis.chart_spec(res) if out["rows"] else None
    except analysis.AnalysisError as exc:
        out["chart_error"] = str(exc)
    return out


def _study_view(aid, study_id, run_id=None):
    st = _study_or_404(aid, study_id)
    run = store.study_run(aid, study_id, run_id)
    if run_id and run is None:
        raise HTTPException(404, "No such run of this study.")
    return {"study": st, "run": _run_view(run)}


@router.get("/studies/series")
def find_series(request: Request, q: str = ""):
    """Series by name or code, for the pickers on a study: the public search,
    series only. Under /studies/ because /api/v1/{dataset}/series is the public
    API's own route and would take /assistant/series first."""
    _account(request)
    q = (q or "").strip()
    if not q:
        return {"series": []}
    try:
        d = json.loads(tools_v2.search(q, limit=20))
    except Exception:  # noqa: BLE001
        return {"series": []}
    return {"series": [{"dataset": s["dataset"], "code": s["code"], "name": s.get("name_en") or s["code"],
                        "unit": s.get("unit")} for s in d.get("series") or []]}


@router.get("/studies/recipes")
def study_recipes(request: Request):
    _account(request)
    return {"recipes": recipes.catalogue()}


@router.get("/studies")
def list_studies(request: Request, archived: int = 0):
    aid = _account(request)["id"]
    return {"studies": store.studies(aid, archived=bool(archived)),
            "archived": len(store.studies(aid, archived=True))}


@router.post("/studies")
def new_study(payload: StudyRequest, request: Request):
    aid = _account(request)["id"]
    title = (payload.title or "").strip() or _title_from(payload.question) or "Untitled study"
    return {"study": store.add_study(aid, title, (payload.question or "").strip())}


@router.get("/studies/{study_id}")
def get_study(study_id: int, request: Request, run: int = 0):
    return _study_view(_account(request)["id"], study_id, run or None)


@router.put("/studies/{study_id}")
def edit_study(study_id: int, payload: StudyEdit, request: Request):
    aid = _account(request)["id"]
    _study_or_404(aid, study_id)
    title = payload.title.strip() if payload.title is not None else None
    if title is not None and not title:
        raise HTTPException(400, "A study needs a title.")
    refresh = False
    if payload.refresh is not None:
        refresh = payload.refresh.strip().lower() or None
        if refresh is not None and refresh not in store.REFRESH_CHOICES:
            raise HTTPException(400, "refresh is daily, weekly, or empty for never.")
    budget = False
    if payload.token_budget is not None:
        budget = int(payload.token_budget) or None
        if budget is not None and not BUDGET_MIN <= budget <= runner.BUDGET["tokens"]:
            raise HTTPException(400, "A token budget is between %s and %s, or 0 for the server's cap."
                                % ("{:,}".format(BUDGET_MIN), "{:,}".format(runner.BUDGET["tokens"])))
    return {"study": store.update_study(aid, study_id, title=title, question=payload.question,
                                        refresh=refresh, token_budget=budget)}


@router.delete("/studies/{study_id}")
def archive_study(study_id: int, request: Request):
    aid = _account(request)["id"]
    _study_or_404(aid, study_id)
    return {"study": store.archive_study(aid, study_id, True)}


@router.post("/studies/{study_id}/restore")
def restore_study(study_id: int, request: Request):
    aid = _account(request)["id"]
    _study_or_404(aid, study_id)
    return {"study": store.archive_study(aid, study_id, False)}


@router.post("/studies/{study_id}/run")
async def run_study(study_id: int, payload: StudyRun, request: Request):
    """Run a spec as written: no AI, nothing but the server's own work."""
    aid = _account(request)["id"]
    _study_or_404(aid, study_id)
    try:
        result = await run_in_threadpool(analysis.execute, payload.spec)
    except analysis.AnalysisError as exc:
        raise HTTPException(400, str(exc))
    rid = store.add_study_run(aid, study_id, "you", result)
    return _study_view(aid, study_id, rid)


@router.post("/studies/{study_id}/recipe")
async def run_recipe(study_id: int, payload: StudyRecipe, request: Request):
    """A ready-made analysis, filled in with pickers: built and run by code, no AI."""
    aid = _account(request)["id"]
    _study_or_404(aid, study_id)
    try:
        spec = recipes.build(payload.recipe, payload.series, payload.start, payload.end,
                             payload.options, payload.monthly or None, payload.title)
        result = await run_in_threadpool(analysis.execute, spec)
    except (recipes.RecipeError, analysis.AnalysisError) as exc:
        raise HTTPException(400, str(exc))
    rid = store.add_study_run(aid, study_id, "you", result,
                              note=_recipe_note(payload.recipe, result, payload.options))
    return _study_view(aid, study_id, rid)


def _recipe_note(recipe_id, result, options):
    titles = dict((s["name"], s.get("title") or s["series"]) for s in result.get("sources") or [])
    return recipes.reads_as(recipe_id, titles, options)


MATCH_RULES = (
    "You match a question about economic or market data to one ready-made analysis, or say "
    "none fits. Answer with one JSON object and nothing else: {\"recipe\": <id or null>, "
    "\"series\": {<slot>: \"dataset:code\"}, \"start\": \"YYYY-MM-DD\" or null, "
    "\"end\": null or a date, \"options\": {<option>: <one value>}, \"title\": <short chart title>}. "
    "Each option takes exactly one of its listed values, e.g. {\"measure\": \"yoy\"}, never the list. "
    "Inflation, or the growth of any index, is measure yoy, never level. Use only series "
    "codes from the list given; if the question names a series that is not listed, or asks "
    "for something no recipe computes — volatility, a standard deviation, a correlation, a "
    "regression, a ranking, a count of periods, contributions, a forecast — answer "
    "{\"recipe\": null}: a recipe only draws and summarises the series it is given. A question comparing two "
    "rates is 'gap' when it asks about the difference and 'compare' otherwise; 'since' is for "
    "'highest/lowest since' or 'last this high'. Dates: 'since 2020' is start 2020-01-01."
)


# Words that ask for a statistic no recipe computes: such a question goes
# straight to the Study Analyst, which can write the query for it.
_NOT_A_RECIPE = re.compile(r"volatil|standard deviation|std ?dev|variance|correlat|regress|beta\b|"
                           r"rank|contribut|breadth|forecast|predict|elasticit|how many (months|days|"
                           r"weeks|quarters|years)|count of|percentile|z-score|drawdown", re.I)


NO_MATCH = "no"


def _match_recipe(aid, desk, question, hints, house=None, spent=None):
    """One short AI call, no tools: the question matched to a recipe and its
    blanks. Returns (spec, tokens, matched_id); NO_MATCH when the model said
    nothing fits; None when the match was not tried (no usable model, no
    hints, or a question no recipe computes). `house` is a free try's key
    (free.take), used instead of the desk's. `spent`, a list, is given the
    tokens the call used whatever it answered, so a study's budget counts
    a miss too."""
    if _NOT_A_RECIPE.search(question or "") or not hints:
        return None
    if house:
        provider, key, model = house["provider"], house["key"], house["model"]
    else:
        provider = desk.get("model_provider")
        if not provider or provider == codex.PROVIDER or not desk.get("model_key_set"):
            return None
        try:
            key = keychain.open_(store.desk_secrets(aid)["model_key_ct"])
        except (ValueError, TypeError):
            return None
        model = desk.get("study_model") or desk["model_name"] or gateway.default_model(provider)
    cat = "; ".join("%s (%s; slots %s%s)" % (r["id"], r["label"], ", ".join(
        s["name"] + ("?" if s["optional"] else "") for s in r["slots"]),
        "; options " + ", ".join("%s: one of %s" % (k, "/".join(v)) if isinstance(v, list) else "%s: a %s" % (k, v)
                                 for k, v in r["options"].items()) if r["options"] else "") for r in recipes.catalogue())
    messages = [{"role": "system", "content": MATCH_RULES + "\n\nRecipes: " + cat},
                {"role": "user", "content": question + "\n\n" + analysis.hints_text(hints)}]
    try:
        out = gateway.complete(provider, key, model, messages, tools=None, max_tokens=300)
    except gateway.GatewayError:
        return None
    tokens = dict({"in": 0, "out": 0, "cached": 0}, **(out.get("usage") or {}))
    tokens.update(calls=0, tier=1)
    if spent is not None:
        spent.append(tokens["in"] + tokens["out"])
    m = re.search(r"\{.*\}", out.get("text") or "", re.S)
    if not m:
        return None
    try:
        pick = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(pick, dict) or not isinstance(pick.get("recipe"), str) or pick["recipe"] not in recipes.BY_ID:
        return NO_MATCH
    allowed = set("%s:%s" % (h["dataset"], h["code"]) for h in hints)
    # the model's JSON is not trusted for shape: a list or a string where a
    # mapping belongs is a failed match, not a crash
    series = pick.get("series")
    series = dict((k, v) for k, v in series.items() if isinstance(v, str) and v in allowed) \
        if isinstance(series, dict) else {}
    try:
        spec = recipes.build(pick["recipe"], series, pick.get("start"), pick.get("end"),
                             pick.get("options") or {}, None, pick.get("title"))
    except recipes.RecipeError:
        return None
    return spec, tokens, pick["recipe"]


@router.post("/studies/{study_id}/ask")
async def ask_study(study_id: int, payload: StudyAsk, request: Request):
    """A question in plain English. The cheapest route that answers it wins:
    the series are found by search first; one short AI call tries to match
    the question to a ready-made analysis, which code then runs; only a
    question no recipe fits goes to the Study Analyst, which writes a query.
    Either way the newest run is kept with its token count."""
    aid = _account(request)["id"]
    st = _study_or_404(aid, study_id)
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(400, "Ask something: e.g. how far apart are US and Japanese 10-year yields?")
    desk = store.desk(aid)
    # A stock question goes to the US stock box first (stock_recipes, as the
    # front page serves it): answered by code, on the server's own reader,
    # free and without a key, so it takes no free try. A chat that is already
    # a data-series study keeps to that; a declined question falls through.
    if payload.stocks and not st["spec"]:
        runs = [r for r in st.get("runs") or [] if r.get("question")]
        prev = runs[0]["question"] if runs else None
        a = await run_in_threadpool(_stock_answer, q, prev)
        if a:
            if not st["question"] or st["title"] == "Untitled study":
                store.update_study(aid, study_id, question=q,
                                   title=st["title"] if st["title"] != "Untitled study" else _title_from(q))
            rid = store.add_study_run(aid, study_id, "stocks", {"stock": a, "spec": {}, "rows": [], "columns": []},
                                      question=q, note=a.get("headline") or "")
            return _study_view(aid, study_id, rid)
    # no key of their own: one of the free tries on the server's key, given
    # back if no answer comes of it
    house = None
    if not free.own_key(desk):
        try:
            house = free.take(aid)
        except free.NoTries as exc:
            raise HTTPException(400, str(exc))
    try:
        return await _answer_study(aid, study_id, st, q, desk, house)
    except BaseException:
        if house:
            free.give_back(aid)
        raise


def _stock_answer(q, prev):
    """The US stock box's answer to a question, or None when it declines
    (not a stock question, or no SEC database here)."""
    try:
        from .. import sec_api, stock_recipes
        a = stock_recipes.answer(sec_api._require(), q, prev)
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 — a failure here must not stop the data-series route
        return None
    return a if a and a.get("headline") else None


# Words that do not change what a question asks: two questions that differ
# only in these, in order, case or punctuation are the same question.
_Q_FILLER = frozenset(("the", "a", "an", "of", "and", "is", "are", "was", "were", "please", "me",
                       "show", "chart", "plot", "can", "you", "tell", "give", "what", "whats"))


def question_key(q):
    """A question reduced to its content words, sorted, so the same question
    asked again in other casing, order or punctuation is found."""
    words = re.findall(r"[a-z0-9][a-z0-9.\-]*", (q or "").lower().replace("'", ""))
    return " ".join(sorted(set(w.strip(".-") for w in words) - _Q_FILLER))


def _past_answer(aid, q):
    """A kept run of the person's that answered this same question, newest
    first, or None. 'Nothing is asked twice': the run's analysis is run again
    by code on today's data instead of asking a model."""
    key = question_key(q)
    if len(key.split()) < 2:
        return None
    for r in store.past_runs(aid):
        if r["spec"].get("series") and question_key(r["question"]) == key:
            return r
    return None


def _budget_left(st, spent):
    """What a study's own token budget leaves for the loop after ``spent``
    tokens, as runner caps; None when the study has none. Zero means the
    budget is already gone."""
    cap = st.get("token_budget")
    if not cap:
        return None
    return {"tokens": max(0, int(cap) - int(spent))}


def _budget_message(st):
    return ("This study's token budget (%s tokens for one question) was reached before the Analyst "
            "finished. Raise the budget beside Refresh, or ask a narrower question."
            % "{:,}".format(st["token_budget"]))


async def _answer_study(aid, study_id, st, q, desk, house):
    if not st["spec"]:
        # Asked before: the same analysis runs again on today's data, by code
        past = await run_in_threadpool(_past_answer, aid, q)
        if past is not None:
            try:
                result = await run_in_threadpool(analysis.execute, past["spec"])
            except analysis.AnalysisError:
                result = None
            if result is not None:
                rc = past["spec"].get("recipe") or {}
                when = datetime.datetime.utcfromtimestamp(past["created_at"]).strftime("%d %B %Y").lstrip("0")
                note = (_recipe_note(rc["id"], result, rc.get("options")) + " " if rc.get("id") else "") + \
                    "You asked this in \u201c%s\u201d on %s; the same analysis, run again on today's data." \
                    % (past["title"], when)
                if not st["question"] or st["title"] == "Untitled study":
                    store.update_study(aid, study_id, question=q,
                                       title=st["title"] if st["title"] != "Untitled study" else _title_from(q))
                rid = store.add_study_run(aid, study_id, "library", result, question=q, note=note,
                                          tokens={"in": 0, "out": 0, "cached": 0, "calls": 0, "tier": 0,
                                                  "from_study": past["study_id"]})
                if house:
                    free.give_back(aid)         # no model ran
                return _study_view(aid, study_id, rid)
    used = []                               # tokens the match call(s) spent, hit or miss
    hints = await run_in_threadpool(analysis.suggest_series, q)
    if not st["spec"]:                      # a follow-up changes a query; a fresh question may match a recipe
        matched = await run_in_threadpool(_match_recipe, aid, desk, q, hints, house, used)
        if matched == NO_MATCH:
            # a miss is usually a missing series: once more, with a wider search
            wider = await run_in_threadpool(analysis.suggest_series, q, 16, 5)
            matched = await run_in_threadpool(_match_recipe, aid, desk, q, wider, house, used) if len(wider) > len(hints) else None
            if matched == NO_MATCH:
                matched = None
            elif matched:
                hints = wider
        if matched:
            spec, tokens, recipe_id = matched
            try:
                result = await run_in_threadpool(analysis.execute, spec)
            except analysis.AnalysisError:
                result = None
            if result is not None:
                if not st["question"] or st["title"] == "Untitled study":
                    store.update_study(aid, study_id, question=q,
                                       title=st["title"] if st["title"] != "Untitled study" else _title_from(q))
                rid = store.add_study_run(aid, study_id, "analyst", result, question=q,
                                          note=_recipe_note(recipe_id, result, spec.get("options")),
                                          tokens=tokens)
                return _study_view(aid, study_id, rid)
    task = q
    if hints:
        task += "\n\n" + analysis.hints_text(hints)
    if st["spec"]:
        task += ("\n\nThe study's current analysis, to change or build on if the question is a "
                 "follow-up:\n" + json.dumps(st["spec"], ensure_ascii=False))
    spent = sum(used)
    left = _budget_left(st, spent)
    if left is not None and left["tokens"] <= 0:
        raise HTTPException(400, _budget_message(st))
    h = _study_hire(aid)
    out = await run_in_threadpool(runner.run, aid, h, "study", task, None, "thread", False, house, left)
    made = store.analyses_for_run(aid, out["run_id"]) if out.get("run_id") else []
    at_budget = out.get("outcome") == "budget" and "token budget" in (out.get("error") or "") and left is not None
    if not made:
        if at_budget:
            raise HTTPException(400, _budget_message(st))
        why = out.get("error") or out.get("text") or out.get("outcome") or "it stopped early"
        raise HTTPException(400, "The Analyst did not produce an analysis: %s" % str(why)[:400])
    if not st["question"] or st["title"] == "Untitled study":
        store.update_study(aid, study_id, question=q,
                           title=st["title"] if st["title"] != "Untitled study" else _title_from(q))
    note = out.get("text") or ""
    if at_budget:
        note = (note + "\n\n" if note else "") + \
            "The Analyst stopped at this study's token budget; the last analysis that ran is kept."
    rid = store.add_study_run(aid, study_id, "analyst", made[-1]["result"], question=q,
                              note=note, run_id=out.get("run_id"),
                              tokens=dict(out.get("usage") or {}, calls=out.get("tool_calls"), tier=2,
                                          matcher=spent))
    return _study_view(aid, study_id, rid)


def refresh_due_studies(at=None):
    """Re-run every study whose refresh is due, as the server: no AI, the
    study's own query on the newest data, kept as a run by "schedule". Called
    from the refresh loop (app/refresh.py) and `runner --studies`. Returns
    the number of runs made; one failure never stops the rest."""
    made = 0
    for st in store.studies_due(at):
        store.mark_refreshed(st["id"], at)          # once per period, even if it fails
        rc = st["spec"].get("recipe") or {}
        try:
            result = analysis.execute(st["spec"])
        except analysis.AnalysisError as exc:
            store.add_study_run(st["account_id"], st["id"], "schedule",
                                {"spec": st["spec"], "columns": [], "rows": [], "sources": [],
                                 "ran_at": None, "error": str(exc)},
                                note="The scheduled refresh could not run: %s" % exc)
            continue
        note = _recipe_note(rc["id"], result, rc.get("options")) if rc.get("id") else "Refreshed on schedule."
        store.add_study_run(st["account_id"], st["id"], "schedule", result, note=note)
        made += 1
    return made


@router.get("/studies/{study_id}/runs/{run_id}/python")
def study_python(study_id: int, run_id: int, request: Request):
    """The study to run on the person's own machine (study_export.py)."""
    aid = _account(request)["id"]
    st = _study_or_404(aid, study_id)
    run = store.study_run(aid, study_id, run_id)
    if run is None:
        raise HTTPException(404, "No such run of this study.")
    try:
        data, name = study_export.build(st["title"], run["spec"])
    except analysis.AnalysisError as exc:
        raise HTTPException(400, str(exc))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="%s.zip"' % name})


@router.get("/studies/{study_id}/runs/{run_id}/csv")
def study_csv(study_id: int, run_id: int, request: Request):
    """Every kept row, with the sources, the formula and the trust of each
    column above them, so the file stands on its own."""
    aid = _account(request)["id"]
    st = _study_or_404(aid, study_id)
    run = store.study_run(aid, study_id, run_id)
    if run is None:
        raise HTTPException(404, "No such run of this study.")
    res = run["result"]
    cols = res.get("columns") or []
    import csv
    import io
    buf = io.StringIO()
    buf.write("# %s\n" % st["title"].replace("\n", " "))
    for s in res.get("sources") or []:
        buf.write("# input %s = %s (%s), %s, %s\n" % (s.get("name"), s.get("series"), s.get("title"),
                                                     s.get("release") or "latest", s.get("credit") or ""))
    buf.write("# calculation: %s\n" % " ".join((run["spec"].get("query") or "SELECT * FROM data").split()))
    official = [c for c in cols[1:] if analysis._official(c, run["spec"])]
    buf.write("# trust: %s as published; every other column calculated by the query above\n"
              % (", ".join(official) or "no column"))
    buf.write("# ran %s; missing values are blank, never zero\n" % (res.get("ran_at") or ""))
    if res.get("rows_total", 0) > len(res.get("rows") or []):
        buf.write("# kept the first %d of %d rows\n" % (len(res["rows"]), res["rows_total"]))
    w = csv.writer(buf)
    w.writerow(cols)
    for r in res.get("rows") or []:
        w.writerow(["" if v is None else v for v in r])
    name = re.sub(r"[^a-z0-9]+", "-", st["title"].lower()).strip("-")[:60] or "study"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s.csv"' % name})


# ---------------------------------------------------------- your reading
# Articles saved from the person's own browser (the extension and the Save to
# Plover bookmark): /api/v1/assistant/reading. See app/assistant/reading.py.
from . import reading as _reading  # noqa: E402

router.include_router(_reading.router)

# The front page's public answer preview: /api/v1/assistant/preview?q=...
from . import preview as _preview  # noqa: E402

router.include_router(_preview.router)
