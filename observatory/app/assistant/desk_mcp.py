"""/mcp/desk — a signed-in reader's desk, over MCP, for their own agent.

Lets a person connect Claude Code, Codex or any MCP client to *their* desk
with a personal key made in Settings (store.add_token). The public /mcp
endpoint serves only published data and needs no key; this one adds the
desk: coverage, the #desk feed, threads, specialists, and three write
actions that stay inside the desk.

The rules do not bend for an outside agent:
  * Numbers come only from the same read tools that serve /api/v1
    (tools_v2.run_tool) — the agent has no other route to data.
  * Nothing goes outward. `request_approval` writes a pending row to the
    Inbox; a person approves it there. There is no send tool.
  * Every call is audited, filed under one run per connection per day, and a
    note without a source is refused, exactly as for a specialist.

Stateless JSON-RPC 2.0 over POST, the same subset app/mcp.py speaks, so no
SDK and Python 3.9 compatible. Auth is `Authorization: Bearer <key>`.
"""
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import analysis, tools_v2
from . import runner, specialists, store

router = APIRouter(include_in_schema=False)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "plover-desk", "title": "Plover Analytics — your desk", "version": "1.0.0"}

_OBJ = {"type": "object", "properties": {}, "required": []}

DESK_TOOLS = [
    {"name": "desk_overview",
     "description": ("This desk at a glance: the companies it covers, the specialists on it, "
                     "and how many posts wait for the owner's approval. Call this first."),
     "inputSchema": _OBJ},
    {"name": "read_feed",
     "description": "The latest posts on the desk's #desk feed, oldest first.",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "How many posts (default 20, max 100)."}},
         "required": []}},
    {"name": "list_threads", "description": "The desk's research threads, newest first.",
     "inputSchema": _OBJ},
    {"name": "read_thread", "description": "One research thread with every message.",
     "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "integer"}},
                     "required": ["thread_id"]}},
    {"name": "add_to_coverage",
     "description": ("Add a company to the desk's coverage list. Give its four-character "
                     "securities code, or a name (English or Japanese) that matches one company."),
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "A code such as 7203, or a company name."}},
         "required": ["code"]}},
    {"name": "post_to_desk",
     "description": ("Post a short note to the desk's #desk feed. It stays inside the desk. "
                     "Needs at least one source: a document id, release id or cite URL "
                     "returned by a read tool."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "One to four plain sentences."},
         "sources": {"type": "array", "items": {"type": "string"}}},
         "required": ["text", "sources"]}},
    {"name": "request_approval",
     "description": ("Ask the desk's owner to approve posting a line to their Slack channel. "
                     "Nothing is sent until they approve it in the Inbox."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Exactly what would be posted."},
         "sources": {"type": "array", "items": {"type": "string"}}},
         "required": ["text", "sources"]}},
    {"name": "ask_specialist",
     "description": ("Hand a question to one of the desk's specialists. It runs on the "
                     "owner's model key with its own tools and returns its answer. Slow: "
                     "up to a few minutes."),
     "inputSchema": {"type": "object", "properties": {
         "specialist": {"type": "string", "description": "Its handle, e.g. buyback-monitor."},
         "question": {"type": "string"}},
         "required": ["specialist", "question"]}},
    # Studies: the analysis runs on Plover's server, so the agent reads a
    # preview and the figures it asks for, not the history (app/analysis.py).
    dict(analysis.tool_for("study"), inputSchema=analysis.tool_for("study")["parameters"]),
    {"name": "save_study",
     "description": ("Run an analysis (the same arguments as run_analysis) and keep it as a study "
                     "on the owner's Studies page, with every row, its chart and a CSV. Give a "
                     "title; give study_id instead to add a new run to an existing study. Returns "
                     "the same preview as run_analysis and the study's address."),
     "inputSchema": {"type": "object", "properties": dict(
         analysis.TOOL["parameters"]["properties"],
         title={"type": "string", "description": "The study's title (a new study)."},
         study_id={"type": "integer", "description": "An existing study to add this run to."},
         note={"type": "string", "description": "One or two sentences on what the run shows."}),
         "required": ["series"]}},
    {"name": "list_studies", "description": "The owner's studies, newest first.",
     "inputSchema": _OBJ},
    {"name": "read_study",
     "description": "One study: its question, the analysis it keeps and a preview of its newest run.",
     "inputSchema": {"type": "object", "properties": {"study_id": {"type": "integer"}},
                     "required": ["study_id"]}},
]
for _t in DESK_TOOLS:
    _t.pop("parameters", None)
DESK_TOOL_NAMES = set(t["name"] for t in DESK_TOOLS)

# MCP annotations. Clients use them to decide what needs the person's OK:
# Codex runs a readOnlyHint tool without asking and asks before anything else,
# which is exactly the split wanted — reading is free, writing to the desk is
# the person's call in their own terminal, and outward still needs the Inbox.
_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
_WRITES = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
           "openWorldHint": False}
for _t in DESK_TOOLS:
    _t["annotations"] = dict(_READ_ONLY if _t["name"] in (
        "desk_overview", "read_feed", "list_threads", "read_thread", "run_analysis",
        "list_studies", "read_study") else _WRITES)

RULES = (
    "This is the owner's private desk on Plover Analytics (Japanese official statistics and "
    "company filings). Use only numbers returned by a tool; missing is missing, never zero; "
    "keep official figures distinct from calculated ones and quote the formula for the latter; "
    "put the document id or cite URL beside every number. Anything meant for outside the desk "
    "goes through request_approval and waits for the owner."
)


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


def _fail(msg):
    return _j({"error": msg})


def tools():
    read = [d for d in tools_v2.descriptors()]
    return DESK_TOOLS + read


def _sources_ok(sources):
    return isinstance(sources, list) and any(str(s).strip() for s in sources)


def _call_desk(owner, name, args, run_id):
    aid = owner["account_id"]
    author = "ext:" + owner["label"]
    if name == "desk_overview":
        return _j({"coverage": store.coverage(aid),
                   "specialists": [{"handle": h["slug"], "name": (specialists.get(h["slug"]) or {}).get("name"),
                                    "version": h["version"]} for h in store.hires(aid)
                                   if not specialists.builtin(h["slug"])],
                   "pending_approvals": len(store.approvals(aid, "pending"))})
    if name == "read_feed":
        try:
            limit = max(1, min(100, int(args.get("limit") or 20)))
        except (TypeError, ValueError):
            limit = 20
        posts = store.posts(aid, limit=limit)
        return _j({"posts": [{"id": p["id"], "author": p["author"], "text": p["text"],
                              "sources": p["refs"], "created_at": p["created_at"]} for p in posts]})
    if name == "list_threads":
        return _j({"threads": [{"id": t["id"], "title": t["title"], "messages": t["messages"],
                                "updated_at": t["updated_at"]} for t in store.threads(aid)]})
    if name == "read_thread":
        t = store.thread(aid, args.get("thread_id"))
        if t is None:
            return _fail("No such thread on this desk.")
        return _j({"id": t["id"], "title": t["title"],
                   "messages": [{"role": m["role"], "text": m["text"]} for m in t["messages"]]})
    if name == "add_to_coverage":
        from .api import find_companies
        q = str(args.get("code") or "").strip()
        hits = find_companies(q, limit=5)
        exact = [h for h in hits if h["code"].upper() == q.upper()]
        if exact:
            pick = exact[0]
        elif len(hits) == 1:
            pick = hits[0]
        elif not hits:
            return _fail("No company on the platform matches '%s'." % q)
        else:
            return _j({"error": "'%s' matches more than one company; call again with a code." % q,
                       "candidates": hits})
        return _j({"added": pick, "coverage": store.add_coverage(aid, pick["code"], pick["name_en"] or pick["name_ja"])})
    if name == "post_to_desk":
        text = (args.get("text") or "").strip()
        if not text:
            return _fail("post_to_desk needs text.")
        if not _sources_ok(args.get("sources")):
            return _fail("post_to_desk needs at least one source (document id, release or cite URL).")
        p = store.add_post(aid, author, text, run_id=run_id, refs=[str(s) for s in args["sources"]])
        return _j({"posted": True, "post_id": p["id"]})
    if name == "request_approval":
        text = (args.get("text") or "").strip()
        if not text:
            return _fail("request_approval needs the exact text.")
        if not _sources_ok(args.get("sources")):
            return _fail("request_approval needs at least one source.")
        refs = [str(s) for s in args["sources"]]
        a = store.add_approval(aid, run_id, None, "slack",
                               {"text": text, "sources": refs, "from": owner["label"]})
        store.add_post(aid, author, text, run_id=run_id, refs=refs, approval_id=a["id"])
        return _j({"approval_id": a["id"], "status": "pending",
                   "note": "Waiting for the owner in the Inbox. Nothing has been sent."})
    if name == "ask_specialist":
        h = store.hire_by_slug(aid, str(args.get("specialist") or "").lstrip("@"))
        q = (args.get("question") or "").strip()
        if h is None:
            return _fail("No specialist with that handle on this desk. Call desk_overview.")
        if not q:
            return _fail("ask_specialist needs a question.")
        out = runner.run(aid, h, "connection:" + owner["label"], q, mode="thread")
        return _j({"outcome": out["outcome"], "answer": out["text"], "error": out["error"],
                   "tool_calls": out["tool_calls"], "run_id": out["run_id"]})
    if name in ("run_analysis", "save_study"):
        spec = dict((k, v) for k, v in args.items() if k not in ("title", "study_id", "note"))
        try:
            result = analysis.execute(spec)
        except analysis.AnalysisError as exc:
            return _fail(str(exc))
        out = analysis.brief(result)
        if name == "save_study":
            sid = args.get("study_id")
            if sid:
                if store.study(aid, int(sid)) is None:
                    return _fail("No study %s on this desk. Call list_studies." % sid)
            else:
                title = (args.get("title") or "").strip()
                if not title:
                    return _fail("save_study needs a title (or a study_id to add a run to).")
                sid = store.add_study(aid, title)["id"]
            store.add_study_run(aid, int(sid), author, result, note=(args.get("note") or "").strip(),
                                run_id=run_id)
            out["study"] = {"id": int(sid), "url": "/assistant.html#/studies/%d" % int(sid)}
        return _j(out)
    if name == "list_studies":
        return _j({"studies": [dict((k, s[k]) for k in ("id", "title", "question", "runs", "last_run_at"))
                               for s in store.studies(aid)]})
    if name == "read_study":
        st = store.study(aid, int(args.get("study_id") or 0))
        if st is None:
            return _fail("No such study on this desk. Call list_studies.")
        run = store.study_run(aid, st["id"])
        return _j({"id": st["id"], "title": st["title"], "question": st["question"], "spec": st["spec"],
                   "newest_run": analysis.brief(run["result"]) if run and run["result"].get("rows") is not None
                   else None})
    return _fail("Unknown tool '%s'." % name)


def call(owner, name, args):
    """Run one tool for a connection and audit it. Returns (text, is_error)."""
    if not isinstance(args, dict):
        return _fail("Arguments must be an object."), True
    run_id = store.connection_run(owner["account_id"], owner["label"])
    t0 = time.time()
    if name in DESK_TOOL_NAMES:
        text = _call_desk(owner, name, args, run_id)
        is_err = text.startswith('{"error"')
    elif name in tools_v2.IMPLS:
        text, is_err = tools_v2.run_tool(name, args)
    else:
        return _fail("Unknown tool '%s'." % name), True
    store.record_call(run_id, store.next_seq(run_id), name, args, text,
                      (time.time() - t0) * 1000, is_err)
    store.touch_connection_run(run_id)
    return text, is_err


def _handle_one(owner, msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method = msg.get("method")
    if method is None or "id" not in msg:
        return None
    msg_id = msg["id"]
    params = msg.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(msg_id, {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": RULES + "\n\n" + tools_v2.instructions(),
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tools()})
    if method == "tools/call":
        text, is_err = call(owner, params.get("name"), params.get("arguments") or {})
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_err})
    return _error(msg_id, -32601, "Method not found: %s" % method)


def _handle(owner, message):
    if isinstance(message, list):
        if not message:
            return _error(None, -32600, "Invalid Request")
        replies = [r for r in (_handle_one(owner, m) for m in message) if r is not None]
        return replies or None
    return _handle_one(owner, message)


def _bearer(request):
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


_NEED_KEY = {"error": ("This endpoint needs a personal desk key: create one under Settings "
                       "> Connections and send it as 'Authorization: Bearer <key>'.")}


@router.post("/mcp/desk")
async def desk_post(request: Request):
    owner = await run_in_threadpool(store.token_owner, _bearer(request))
    if owner is None:
        return JSONResponse(_NEED_KEY, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="plover-desk"'})
    try:
        message = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    reply = await run_in_threadpool(_handle, owner, message)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp/desk")
def desk_get():
    return JSONResponse({"error": "Stateless MCP endpoint: POST JSON-RPC 2.0 messages here."},
                        status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp/desk")
def desk_delete():
    return JSONResponse({"error": "No sessions to end."}, status_code=405, headers={"Allow": "POST"})
