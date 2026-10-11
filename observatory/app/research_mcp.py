# -*- coding: utf-8 -*-
"""/mcp/research — the PloverResearch desk for a writer's own agent.

A person with the Writing permission makes a personal key under Admin → My
Account and connects Claude Code or Codex with it. The agent can then read
articles, start and edit drafts in Markdown, and insert charts — as that
person, with every change recorded as "<email> via Claude" (or Codex) in the
draft's history and the admin audit trail.

What it cannot do, by design: publish, withdraw, delete, or change who can
sign in. A person presses Publish in the desk. A chart copied from a Plover
page can only be named here (the agent has no browser); the desk captures it
the next time a person opens the draft, and publishing waits until it has.

Numbers come from the same read tools the public /mcp serves (tools_v2), so an
agent writing a draft and a reader checking it see the same figures.

Transport: stateless JSON-RPC 2.0 over POST, the subset app/mcp.py and
app/assistant/desk_mcp.py speak. Auth: ``Authorization: Bearer plvr_…``.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import market_history, research, research_doc as rd, research_pages as rp, seo, staff, tools_v2, writers

router = APIRouter(include_in_schema=False)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "plover-research", "title": "PloverResearch drafting desk",
               "version": "1.0.0"}

RULES = (
    "You are drafting research notes in PloverResearch, Plover Analytics' publishing desk, on "
    "behalf of the person whose key you hold, in the publications they write for. Drafts "
    "only: you cannot publish; a person reviews and publishes in the desk.\n\n"
    "Markdown conventions (read_article returns them and create_draft / replace_draft read "
    "them):\n"
    "- '# Title' first; an italic line straight after it is the standfirst.\n"
    "- '## Heading' for sections, '### Subheading' below them.\n"
    "- Footnotes: 'text[^1]' with '[^1]: the note' at the end. Cite sources this way.\n"
    "- A live chart of Plover data: a line of its own, "
    "'![Chart title](plover:<dataset>?series=<code>,<code>&measure=index|yoy|mom|ann3m"
    "&start=YYYY-MM&end=YYYY-MM)'. Find datasets and codes with list_datasets and search. "
    "It is frozen with the data as it stands on the day of publication.\n"
    "- A chart copied from a Plover page (company pages included): "
    "'![Chart title](/financials.html?c=7203#chart-2)' — the page address with its view, "
    "and #chart-N for the Nth chart on the page. Use the 'cite' URLs the data tools return. "
    "The desk copies it when a person next opens the draft.\n"
    "- A chart of numbers from outside Plover (another publisher's data, a spreadsheet): "
    "'![Chart title](chart:line?unit=%25)' (or chart:bar for categories such as countries), "
    "then on the next lines a pipe table — first column the dates (YYYY-MM or YYYY-MM-DD) or "
    "labels, then up to six columns of numbers with the series names in the header row — "
    "then 'Source: …', which is required. Leave a missing value blank, never 0. The page "
    "says the values are as entered by the author.\n"
    "- An optional 'Note: …' line straight after a chart is printed under it.\n"
    "- Reminders the writer's approved plan left in the draft, each on a line of its own: "
    "'[[To show: …]]' is the point a section must make — keep it until the section's text "
    "makes that point, then delete the line; '[[Chart: …]]' is a chart the plan promised — "
    "replace that line with the chart. A draft with a reminder left cannot be published.\n"
    "- read_article also returns 'beside_the_draft': the approved plan and the writer's "
    "research notes (numbers, quotations and sources they collected). Write from them; they "
    "are not part of the Markdown and are never published.\n"
    "- Tables: a '**Table N — Caption**' line, the pipe table, then 'Source: …'. Every table "
    "needs a source.\n"
    "- Images already uploaded keep their /research/media/… address; do not invent one.\n\n"
    "Every number in a draft must come from a tool result or a cited source, with its "
    "period. Index levels are quoted as published; a rate you calculate says how. Missing "
    "is missing, never zero. Write plainly: short sentences, no hype, descriptive rather "
    "than causal ('coincides with', not 'caused').\n\n"
    "Name data the way a reader would, everywhere a person reads it: the draft, a plan, a "
    "reminder, a note and your reply. Say what the number is and for whom ('cross-"
    "shareholdings as a share of equity, the 30 largest TOPIX companies'), never a tool "
    "name (compare_cohort, get_series), a field or metric id, a series or dataset code, or "
    "filter syntax (size:core30, ind33:3650), and no code formatting. Chart addresses are "
    "the one exception: they sit inside '![title](…)' and readers never see them.")

_ID = {"type": "integer", "description": "Article id, from list_articles."}
_REV = {"type": "integer", "description": ("The revision you read (from read_article). "
                                           "Refused if someone saved since — read again.")}

TOOLS = [
    {"name": "list_articles",
     "description": "Every article you can open, in each publication you write for: id, "
                    "publication, title, status, published version, last edit and by whom, "
                    "and your publications with your role. Call this first.",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "annotations": {"readOnlyHint": True}},
    {"name": "read_article",
     "description": "One article's draft as editable Markdown, with its revision, summary, "
                    "web address, status, and any charts still waiting to be captured.",
     "inputSchema": {"type": "object", "properties": {"article_id": _ID},
                     "required": ["article_id"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "create_draft",
     "description": "Start a new draft from Markdown (conventions in the server instructions). "
                    "Returns its id and the desk address where a person reviews it.",
     "inputSchema": {"type": "object", "properties": {
         "markdown": {"type": "string", "description": "The whole article, '# Title' first."},
         "summary": {"type": "string", "description": "One or two sentences for search "
                     "results and the Substack lead (up to 700 characters)."},
         "slug": {"type": "string", "description": "Optional web address: lower-case words "
                  "joined by hyphens."},
         "publication": {"type": "string", "description": "Optional: which publication, by "
                         "the address name list_articles gives. Defaults to PloverResearch "
                         "if you write there, else your first publication."}},
         "required": ["markdown"]}},
    {"name": "replace_draft",
     "description": "Replace a draft's body with new Markdown. Charts already captured from "
                    "pages and uploaded images are kept when their lines are kept. A published "
                    "article stays as published until a person publishes the new draft.",
     "inputSchema": {"type": "object", "properties": {
         "article_id": _ID, "base_revision": _REV,
         "markdown": {"type": "string", "description": "The whole article, '# Title' first."},
         "summary": {"type": "string", "description": "Optional new summary."}},
         "required": ["article_id", "base_revision", "markdown"]}},
    {"name": "append_to_draft",
     "description": "Add Markdown to the end of a draft, or after the section whose heading "
                    "you name.",
     "inputSchema": {"type": "object", "properties": {
         "article_id": _ID, "base_revision": _REV,
         "markdown": {"type": "string", "description": "Blocks to add (no '# Title')."},
         "after_heading": {"type": "string", "description": "Optional: insert at the end of "
                           "the section with this heading text."}},
         "required": ["article_id", "base_revision", "markdown"]}},
    {"name": "set_details",
     "description": "Change a draft's title, standfirst, summary or web address. The address "
                    "is fixed once the article has been published.",
     "inputSchema": {"type": "object", "properties": {
         "article_id": _ID, "base_revision": _REV,
         "title": {"type": "string"}, "dek": {"type": "string", "description": "Standfirst."},
         "summary": {"type": "string"}, "slug": {"type": "string"}},
         "required": ["article_id", "base_revision"]}},
    {"name": "check_article",
     "description": "What still stops the draft being published (missing summary, sources, "
                    "chart data, uncaptured charts), and whether every live chart can be read. "
                    "Fix these; a person then publishes.",
     "inputSchema": {"type": "object", "properties": {"article_id": _ID},
                     "required": ["article_id"]},
     "annotations": {"readOnlyHint": True}},
]
TOOL_NAMES = set(t["name"] for t in TOOLS)


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


def _fail(msg):
    return _j({"error": msg})


_BASE = {"url": seo.SITE_BASE_URL}


def _desk_url(aid):
    return _BASE["url"] + "/desk.html?id=%d" % aid


def _actor(person, client):
    return "%s via %s" % (person["email"], client)


def _pending(draft):
    return [{"chart": n, "url": b["url"]} for b, n in _charts(draft) if b["type"] == "snapshot"
            and not b.get("cfg")]


def _charts(draft):
    nums, _ = rd.chart_numbering(draft["blocks"])
    return [(b, nums[b["id"]]) for b in draft["blocks"] if b["id"] in nums]


def _article(aid, person, action="view"):
    a = research.get(aid)
    if a is None or not writers.can(person, action, a):
        if a is not None and writers.can(person, "view", a):
            raise ValueError("Only an editor of the publication can do that.")
        raise LookupError("No article with id %s. Call list_articles." % aid)
    return a


def _url(a):
    return seo.SITE_BASE_URL + research.address(a["publication_id"], a["slug"])


def _publication_for(person, name):
    mine = writers.roles(person)
    if name:
        pub = research.publication_by_slug(name)
        if pub is None or pub["id"] not in mine:
            raise ValueError("You do not write for a publication called '%s'. Call "
                             "list_articles for yours." % name)
        return pub["id"]
    return research.HOME if research.HOME in mine else sorted(mine)[0]


def _markdown_for(a):
    d = a["draft"]
    head = "# %s\n\n" % (d["title"] or "Untitled")
    if d["dek"]:
        head += "*%s*\n\n" % d["dek"]
    return head + rd.render_markdown(d, editable=True)


def _desk_context(d):
    """The approved plan and the writer's research notes, which sit beside the
    draft in the desk and are never published: for reading, not for editing
    through the Markdown."""
    out = {}
    plan = d.get("plan")
    if plan:
        out["plan"] = rd.render_markdown({"blocks": plan["blocks"]}).strip()
        out["plan_approved"] = plan.get("approved_at") or ""
    notes = []
    for n in d.get("notes") or []:
        text = n["text"] or rd.inline_text(n["html"])
        if n.get("block"):
            text = text or n["block"].get("title") or "a chart"
        notes.append({"kind": n["kind"], "text": text, "source": n["source"], "url": n["url"],
                      "in_draft": n["used"]})
    if notes:
        out["notes"] = notes
    return out


def _keep_desk(old_draft, new_draft):
    """The plan and the notes are the desk's: a draft rewritten from Markdown
    keeps them."""
    for k in ("plan", "notes"):
        if old_draft.get(k):
            new_draft[k] = old_draft[k]
    return new_draft


def _keep_captured(old_draft, new_draft):
    """Blocks parsed from Markdown carry only what Markdown can say. Give a
    chart copied from a page back its captured data, and an image its size,
    when the same line is still there."""
    captured = {}
    images = {}
    for b in old_draft["blocks"]:
        if b["type"] == "snapshot" and b.get("cfg"):
            captured[(b["url"], b["chart"])] = b
        if b["type"] == "image" and b["media"]:
            images[b["media"]] = b
    for b in new_draft["blocks"]:
        if b["type"] == "snapshot":
            old = captured.get((b["url"], b["chart"]))
            if old:
                for k in ("kind", "cfg", "page_title", "source", "calc", "captured_at"):
                    b[k] = old[k]
        elif b["type"] == "image" and b["media"] in images:
            old = images[b["media"]]
            b["width"], b["height"] = old.get("width"), old.get("height")
            b["link"] = old.get("link", "")
    return new_draft


def _save(a, draft, base_revision, actor, label):
    try:
        saved = research.save(a["id"], draft, base_revision, actor, label=label)
    except research.Conflict as exc:
        raise ValueError("%s saved a newer revision (%d) at %s. Call read_article again and "
                         "redo your change on that." % (exc.current["updated_by"],
                                                        exc.current["revision"],
                                                        exc.current["updated_at"]))
    return {"article_id": a["id"], "revision": saved["revision"], "desk_url": _desk_url(a["id"]),
            "pending_charts": _pending(saved["draft"]),
            "note": "Saved as a draft. A person reviews and publishes it in the desk."}


def _apply_markdown(markdown, keep_title_from):
    draft = rd.import_markdown(markdown)
    if not draft["title"] and keep_title_from:
        draft["title"] = keep_title_from["title"]
        draft["dek"] = draft["dek"] or keep_title_from["dek"]
    return draft


def _slug_ok(slug, aid, publication_id=None):
    if slug and rd._SLUG.match(slug) and research.slug_taken(slug, aid,
                                                            publication_id=publication_id):
        raise ValueError("Another article in this publication already uses the address '%s'; "
                         "choose another." % slug)


def run(person, client, name, args):
    """One tool call. Returns (text, is_error)."""
    actor = _actor(person, client)
    try:
        if name == "list_articles":
            mine = writers.roles(person)
            pubs = dict((pid, research.publication(pid)) for pid in mine)
            return _j({"publications": [{"publication": pubs[pid]["slug"],
                                         "name": pubs[pid]["name"], "your_role": mine[pid]}
                                        for pid in sorted(mine)],
                       "articles": [{
                "id": a["id"], "publication": pubs[a["publication_id"]]["slug"],
                "title": a["title"], "status": a["status"],
                "published_version": a["published_version"], "updated_at": a["updated_at"],
                "updated_by": a["updated_by"],
                "url": _url(a) if a["published_version"] else None,
            } for a in research.list_articles() if a["publication_id"] in mine
                and writers.can(person, "view", a)]}), False
        if name == "read_article":
            a = _article(int(args["article_id"]), person)
            return _j({"article_id": a["id"], "revision": a["revision"], "status": a["status"],
                       "published_version": a["published_version"],
                       "unpublished_changes": a["unpublished_changes"],
                       "slug": a["slug"] or a["draft"]["slug"], "slug_fixed": a["slug_locked"],
                       "summary": a["draft"]["summary"], "markdown": _markdown_for(a),
                       "beside_the_draft": _desk_context(a["draft"]),
                       "pending_charts": _pending(a["draft"]), "desk_url": _desk_url(a["id"])}), False
        if name == "create_draft":
            draft = _apply_markdown(args.get("markdown") or "", None)
            if not draft["title"]:
                return _fail("Start the Markdown with '# Title'."), True
            if args.get("summary"):
                draft["summary"] = rd.plain(args["summary"], rd.SUMMARY_MAX)
            pid = _publication_for(person, args.get("publication"))
            if args.get("slug"):
                draft["slug"] = rd.slugify(args["slug"])
            _slug_ok(draft["slug"], 0, pid)
            draft["authors"] = [person["id"]]
            a = research.create(actor, draft, pid)
            from .admin_api import audit
            audit("mcp_draft_created", "article %d: %s" % (a["id"], draft["title"]), "mcp",
                  by=actor)
            return _j({"article_id": a["id"], "revision": a["revision"],
                       "desk_url": _desk_url(a["id"]), "pending_charts": _pending(a["draft"]),
                       "note": "Draft created. A person reviews and publishes it in the desk."}), False
        if name in ("replace_draft", "append_to_draft", "set_details"):
            a = _article(int(args["article_id"]), person, "edit")
            base = int(args["base_revision"])
            old = a["draft"]
            if name == "replace_draft":
                draft = _keep_desk(old, _keep_captured(
                    old, _apply_markdown(args.get("markdown") or "", old)))
                draft["summary"] = rd.plain(args.get("summary"), rd.SUMMARY_MAX) or old["summary"]
                draft["slug"] = old["slug"]
                draft["authors"] = sorted(set(old["authors"] + [person["id"]]))
                label = "Replaced via %s" % client
            elif name == "append_to_draft":
                extra = _keep_captured(old, _apply_markdown(args.get("markdown") or "", old))
                new_blocks = [b for b in extra["blocks"]
                              if not (b["type"] == "p" and not rd.inline_text(b["html"]).strip())]
                blocks = list(old["blocks"])
                at = len(blocks)
                heading = rd.plain(args.get("after_heading"), rd.TITLE_MAX).lower()
                if heading:
                    idx = [i for i, b in enumerate(blocks)
                           if b["type"] == "heading" and b["text"].lower() == heading]
                    if not idx:
                        return _fail("No heading '%s' in the draft." % args["after_heading"]), True
                    at = idx[0] + 1
                    while at < len(blocks) and not (blocks[at]["type"] == "heading" and
                                                    blocks[at]["level"] <= blocks[idx[0]]["level"]):
                        at += 1
                draft = dict(old)
                draft["blocks"] = blocks[:at] + new_blocks + blocks[at:]
                draft["authors"] = sorted(set(old["authors"] + [person["id"]]))
                label = "Added to via %s" % client
            else:
                draft = dict(old)
                for k, limit in (("title", rd.TITLE_MAX), ("dek", rd.TEXT_MAX),
                                 ("summary", rd.SUMMARY_MAX)):
                    if args.get(k) is not None:
                        draft[k] = rd.plain(args[k], limit)
                if args.get("slug") is not None:
                    if a["slug_locked"]:
                        return _fail("The web address is fixed: the article has been "
                                     "published at %s." % research.address(
                                         a["publication_id"], a["slug"])), True
                    draft["slug"] = rd.slugify(args["slug"])
                    _slug_ok(draft["slug"], a["id"])
                label = "Details via %s" % client
            out = _save(a, draft, base, actor, label)
            from .admin_api import audit
            audit("mcp_" + name, "article %d" % a["id"], "mcp", by=actor)
            return _j(out), False
        if name == "check_article":
            a = _article(int(args["article_id"]), person)
            draft = a["draft"]
            names = []
            for i in draft["authors"]:
                p = staff.get(i)
                if p:
                    names.append(p["name"])
            problems = rd.publish_problems(draft, names)
            snaps = rp.build_snapshots(draft, rd.today_iso())
            nums, _ = rd.chart_numbering(draft["blocks"])
            for b in draft["blocks"]:
                err = (snaps.get(b["id"]) or {}).get("error") if b["type"] == "chart" else None
                if err and b["dataset"] and b["series"]:
                    problems.append("Chart %d: %s" % (nums[b["id"]], err))
            return _j({"article_id": a["id"], "revision": a["revision"], "problems": problems,
                       "ready": not problems, "pending_charts": _pending(draft),
                       "note": "Ready means a person can publish it in the desk."}), False
    except (KeyError, TypeError) as exc:
        return _fail("Missing or malformed argument: %s." % exc), True
    except (LookupError, ValueError, rd.DocError, research.ResearchError) as exc:
        return _fail(str(exc)), True
    return _fail("Unknown tool '%s'." % name), True


def tools(person=None):
    private = [{"name": t["name"], "description": t["description"], "inputSchema": t["parameters"]}
               for t in market_history.tool_schemas(person)]
    return TOOLS + tools_v2.descriptors() + private


def call(person, client, name, args):
    if not isinstance(args, dict):
        return _fail("Arguments must be an object."), True
    if name in TOOL_NAMES:
        return run(person, client, name, args)
    if name in market_history.TOOL_NAMES:
        return market_history.run_tool(person, name, args)
    if name in tools_v2.IMPLS:
        return tools_v2.run_tool(name, args)
    return _fail("Unknown tool '%s'." % name), True


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle_one(person, client, msg):
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
        return _result(msg_id, {"tools": tools(person)})
    if method == "tools/call":
        text, is_err = call(person, client, params.get("name"), params.get("arguments") or {})
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_err})
    return _error(msg_id, -32601, "Method not found: %s" % method)


def _handle(person, client, message):
    if isinstance(message, list):
        if not message:
            return _error(None, -32600, "Invalid Request")
        replies = [r for r in (_handle_one(person, client, m) for m in message) if r is not None]
        return replies or None
    return _handle_one(person, client, message)


def _bearer(request):
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _client(request):
    ua = (request.headers.get("user-agent") or "").lower()
    if "codex" in ua:
        return "Codex"
    if "claude" in ua:
        return "Claude"
    return "MCP"


_NEED_KEY = {"error": ("This endpoint needs a personal key: sign in to the Writer Desk "
                       "(/write.html), open My Account, create a key under AI Connections, and "
                       "send it as 'Authorization: Bearer <key>'.")}


def _who(token):
    person = staff.key_person(token)
    if person is None:
        return None, 401
    if not writers.roles(person):
        return None, 403
    return person, 200


@router.post("/mcp/research")
async def research_post(request: Request):
    person, status = await run_in_threadpool(_who, _bearer(request))
    if person is None:
        body = _NEED_KEY if status == 401 else {
            "error": "You are not a member of any publication."}
        return JSONResponse(body, status_code=status,
                            headers={"WWW-Authenticate": 'Bearer realm="plover-research"'})
    try:
        message = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    # Desk links point at the host the agent called: production, or a laptop.
    _BASE["url"] = seo.SITE_BASE_URL if seo.is_https(request) else str(request.base_url).rstrip("/")
    reply = await run_in_threadpool(_handle, person, _client(request), message)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp/research")
def research_get():
    return JSONResponse({"error": "Stateless MCP endpoint: POST JSON-RPC 2.0 messages here."},
                        status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp/research")
def research_delete():
    return JSONResponse({"error": "No sessions to end."}, status_code=405,
                        headers={"Allow": "POST"})
