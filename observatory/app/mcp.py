"""MCP endpoint — lets an external AI assistant use the published data.

A remote Model Context Protocol server in its "Streamable HTTP" shape,
deliberately stateless: one POST /mcp route speaking JSON-RPC 2.0, no
sessions, no server-initiated streams. That subset is all a read-only tools
server needs, and it is what lets a user connect by pasting one URL into
Claude (or any MCP client) with no install and no login.

The tools served are the shared layer in tools.py — the same wrappers over
the same functions that serve /api/v1, so a connected assistant can only ever
see numbers the public API already publishes, with the same trust labels,
formulas, and cite URLs. No tool here may bypass that layer.

Implemented directly rather than via the official MCP SDK because the SDK
requires Python 3.10+ and app/ code must run on the 3.9 used locally. The
protocol methods a stateless tools-only server must answer are few:
initialize, the initialized notification, ping, tools/list, and tools/call.
Plus resources/list and resources/read, which serve the dataset manifests
(app/registry.py) as `observatory://datasets/{id}`.

Two tool surfaces coexist behind MCP_TOOLSET (v1 | v2 | both, default both):
the original per-dataset tools in tools.py, and the generic tools in
tools_v2.py whose `dataset` argument is resolved through the registry. A name
served by both surfaces is advertised once, by v2 — see _tools_for.

Kill switch: MCP_ENABLED — on by default (unlike ASK_ENABLED, nothing here
generates text or spends money; it serves the same bytes as the API), set it
to a falsy value to turn the endpoint off.
"""
import json
import os
import pathlib
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .tools import (EQUITY_TOOL_IMPLS, EQUITY_TOOL_SCHEMAS, SEC_TOOL_IMPLS,
                    SEC_TOOL_SCHEMAS, TOOL_IMPLS, TOOL_SCHEMAS, equity_available,
                    run_tool, sec_available)
from . import registry, tools_v2

# Newest first; initialize echoes the client's version when we support it and
# offers the newest otherwise, per the negotiation rules.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_INFO = {
    # Named for the platform, not the macro product alone — future datasets
    # (BOJ, equity holdings) join this same server.
    "name": "plover-analytics",
    "title": "Plover Analytics",
    "version": "1.0.0",
}

# Shown to the connected assistant at initialize time: the trust contract in
# the form an LLM can follow.
INSTRUCTIONS = """\
This server publishes Japanese consumer price statistics from Plover Analytics: \
cpi-jp (~80 category aggregates: headline CPI, cores, major \
expenditure groups) and cpi-jp-items (~740 individually priced items), \
monthly from 1970.

Ground every figure you state in a tool result from this conversation — the \
data is revised and extended monthly, so never answer from memory. Missing \
values are missing, never zero. Index levels (2020 = 100) are official \
statistics exactly as the Statistics Bureau of Japan published them; \
year-over-year, month-over-month, and 3-month annualized rates are calculated \
by this platform from those indices and each response carries the formula \
used in its `calc` field — keep official and calculated figures distinct \
when quoting them. Every response includes a `cite` URL, a permanent page \
showing the same view; link it when you present the numbers. Source credit: \
Statistics Bureau of Japan, via Plover Analytics.

It also publishes three other Japanese macro datasets, each in its own \
measure and never to be ranked or combined with the price indices or with \
each other. VISITOR ARRIVALS (jnto-visitors) — monthly counts of foreign \
visitors by market from 2003, published by the Japan National Tourism \
Organization and computed by it from Ministry of Justice immigration \
statistics; use get_arrivals_ranking to find market codes and to answer \
"which markets are up or down the most", and get_arrivals for history. The \
two most recent months are JNTO estimates: still official, but rounded to \
the nearest 100 and covering only the largest markets, so rankings and \
shares are served on the latest month with a complete breakdown and every \
response says which month that is. A region is the sum of its member \
markets — never add a parent to its children. Credit: Japan National \
Tourism Organization (JNTO). JGB YIELD CURVE (jgb-yields) — Ministry of \
Finance constant-maturity yields for 15 tenors, in percent per year, which \
can be genuinely negative; get_yield_curve. BANK OF JAPAN (boj-assets) — \
JGB holdings and monthly flows in yen, where a negative net flow is real \
balance-sheet runoff, not a missing value; get_boj_balance_sheet, whose \
response carries the Bank's required credit line."""

# Appended to INSTRUCTIONS when the cross-shareholding database is present.
EQUITY_INSTRUCTIONS = """

This server also publishes Japanese cross-shareholding (policy shareholding, \
政策保有株式) data extracted from companies' annual securities reports filed \
on EDINET: named holdings with share counts, yen book values, prior-year \
figures, and the stated purpose of each holding, plus the reverse view of \
who holds a company. Figures are exactly as filed, and every one of them is \
balance-sheet CARRYING AMOUNT at fiscal year end from an annual report. That \
basis is returned with each response and must be stated with any figure you \
quote. It is NOT the basis the press and IR decks usually use: reduction \
targets and progress figures are quoted on acquisition cost, often at \
commercial-bank level rather than group level, often listed shares only, and \
often at a half-year date this product does not hold. Those differences run \
to several times the figure, not a few percent. Before you set a number from \
here against a published one, call check_claim; never reconcile the two \
yourself by assuming a convention. The dataset accumulates \
filing by filing and does not yet cover the whole market — call \
get_holdings_summary first and state its coverage with any aggregate you \
quote. Yen book values are levels, a different measure from CPI indices; \
never rank or combine the two. Source credit: company filings on EDINET \
(Financial Services Agency), via the Plover Analytics.

From the same annual reports it publishes BOARDS AND PAY: every director with \
title, role, age and shareholding; officer remuneration by category; and the \
individuals whose consolidated remuneration is disclosed. Two rules an \
assistant must not break. First, named individual pay is 連結報酬等 — \
CONSOLIDATED, including pay from group companies — and is a different basis \
from the officer-category table, so the two are never netted, subtracted or \
divided into one another, and people appear who do not sit on the board. \
Second, the filed category total is the published number; its components \
often do not sum to it because filers disagree on whether non-monetary pay is \
additive or an 'of which' memo, so check components_reconcile before quoting a \
component. Call get_governance_summary first and state its coverage."""

TOOL_TITLES = {
    "list_datasets": "List datasets",
    "search_series": "Search series",
    "get_series_values": "Get series history",
    "get_overview": "Inflation overview",
    "get_contributions": "Contributions to headline",
    "get_breadth": "Inflation breadth",
    "get_holdings_summary": "Cross-shareholding coverage",
    "search_companies": "Search companies",
    "get_company_holdings": "Company holdings (both directions)",
    "get_unwind_ranking": "Unwind ranking",
    "check_claim": "Check a published figure",
    "get_governance_summary": "Boards and pay coverage",
    "get_company_board": "Company board and pay",
    "get_board_history": "Board and pay history",
    "get_governance_screen": "Board and pay screen",
    "get_top_paid_officers": "Highest-paid officers",
    "get_financials": "Company key indicators",
    "get_financial_statement": "Financial statement as filed",
    "get_financials_screen": "Financials screen",
    "get_financial_metrics": "Calculated ratios (ROE, ROA, margins)",
    "screen_financial_metrics": "Ratio screener",
    "search_us_companies": "Find a US SEC filer",
    "get_us_financials": "US company key indicators (SEC)",
    "get_us_financial_statement": "US financial statement as filed (SEC)",
    "get_us_facts": "US tag history (SEC)",
    "screen_us_financials": "US filers screen (SEC)",
}


def _descriptors(schemas):
    """Reshape tools.py's OpenAI-style schemas into MCP tool descriptors —
    one source of truth for what each tool does and takes."""
    return [{
        "name": entry["function"]["name"],
        "title": TOOL_TITLES.get(entry["function"]["name"],
                                 entry["function"]["name"]),
        "description": entry["function"]["description"],
        "inputSchema": entry["function"]["parameters"],
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    } for entry in schemas]


MCP_TOOLS = _descriptors(TOOL_SCHEMAS)
EQUITY_MCP_TOOLS = _descriptors(EQUITY_TOOL_SCHEMAS)
SEC_MCP_TOOLS = _descriptors(SEC_TOOL_SCHEMAS)


def _current_tools():
    """The tool list this server offers right now.

    The cross-shareholding group is listed only when its database is present,
    so a server that has not received the dataset never advertises tools that
    could only fail. Checked per request — the file can arrive between calls.
    """
    out = list(MCP_TOOLS)
    if equity_available():
        out += EQUITY_MCP_TOOLS
    if sec_available():
        out += SEC_MCP_TOOLS
    return out


def _toolset():
    """Which tool surface to advertise: v1 (the per-dataset tools), v2 (the
    generic tools over the registry), or both. `both` is the transition
    default — existing connectors keep their tool names while new ones get the
    generic surface; flip to v2 once nobody depends on the old names."""
    v = os.environ.get("MCP_TOOLSET", "both").strip().lower()
    return v if v in ("v1", "v2", "both") else "both"


# v1 tools the v2 surface now answers in full. They are still DISPATCHED —
# a connector that cached the old catalogue keeps working — but they are no
# longer advertised, because a list of thirty-odd tools where a dozen are
# other spellings of each other costs an assistant context and accuracy on
# every call.
#
#   search_series, search_companies        -> search
#   get_series_values                      -> get_series
#   get_overview                           -> get_overview (generic, all datasets)
#   get_contributions, get_breadth,
#   get_arrivals, get_arrivals_ranking,
#   get_yield_curve                        -> get_breakdown
#   get_boj_balance_sheet                  -> get_overview / get_series
#   get_holdings_summary, get_company_holdings,
#   get_unwind_ranking                     -> get_company / screen (cross-shareholdings)
#   get_governance_summary, get_company_board,
#   get_board_history, get_governance_screen,
#   get_top_paid_officers                  -> get_company / screen (boards-and-pay)
#   get_financials, get_financial_statement,
#   get_financials_screen, get_financial_metrics,
#   screen_financial_metrics               -> get_company / screen / compare_cohort
#
# check_claim is deliberately absent: it is the one v1 tool with no v2
# equivalent, so it stays on the advertised list.
SUPERSEDED_BY_V2 = frozenset((
    "search_series", "search_companies", "get_series_values", "get_overview",
    "get_contributions", "get_breadth", "get_arrivals", "get_arrivals_ranking",
    "get_yield_curve", "get_boj_balance_sheet",
    "get_holdings_summary", "get_company_holdings", "get_unwind_ranking",
    "get_governance_summary", "get_company_board", "get_board_history",
    "get_governance_screen", "get_top_paid_officers",
    "get_financials", "get_financial_statement", "get_financials_screen",
    "get_financial_metrics", "screen_financial_metrics",
    # The US shelf, the same way: search / get_company / screen reach it
    # through dataset="sec-financials" whenever this deployment has it.
    "search_us_companies", "get_us_financials", "get_us_financial_statement",
    "get_us_facts", "screen_us_financials",
))


def _tools_for(toolset):
    """The advertised tool list, with each name appearing exactly once.

    Under `both` the two surfaces overlap on `list_datasets`. A tools/list
    carrying the same name twice is not a valid catalogue: a client may
    shadow one entry, or reject the list outright. First wins, and v2 is
    listed first, so the descriptor advertised is the implementation
    tools/call would actually run for that name.

    Under `both` the v1 tools that v2 has replaced are also dropped from the
    listing — not from dispatch. Advertising and answering are separate
    questions: an old connector calling `get_yield_curve` still gets its
    answer, while a new one is not asked to choose between five tools that
    do the same thing.
    """
    out, seen = [], set()
    groups = []
    if toolset in ("v2", "both"):
        groups.append(tools_v2.descriptors())
    if toolset in ("v1", "both"):
        groups.append(_current_tools())
    for index, group in enumerate(groups):
        v1_group = toolset == "both" and index == len(groups) - 1
        for tool in group:
            if tool["name"] in seen:
                continue
            if v1_group and tool["name"] in SUPERSEDED_BY_V2:
                continue
            seen.add(tool["name"])
            out.append(tool)
    return out


def _instructions(toolset):
    if toolset == "v1":
        text = INSTRUCTIONS
        if equity_available():
            text += EQUITY_INSTRUCTIONS
        return text
    text = tools_v2.instructions()
    if toolset == "both":
        text += ("\n\nThe older per-dataset tools (get_overview, get_company_holdings, "
                 "get_governance_screen, …) remain available for compatibility; prefer "
                 "the generic tools above.")
    return text


# ---------------------------------------------------------------------------
# Resources: the dataset manifests, readable by any MCP client
# ---------------------------------------------------------------------------

METHODOLOGY_PATH = pathlib.Path(__file__).resolve().parent.parent / "web" / "methodology.html"


def _resources():
    out = [{"uri": "observatory://sections", "name": "sections",
            "title": "Dataset sections",
            "description": "The fixed section list and which datasets sit in each.",
            "mimeType": "application/json"}]
    for m in registry.datasets(with_availability=False):
        out.append({"uri": "observatory://datasets/%s" % m["id"], "name": m["id"],
                    "title": m["name"]["en"], "description": m["summary"],
                    "mimeType": "application/json"})
    out.append({"uri": "observatory://methodology", "name": "methodology",
                "title": "Methodology",
                "description": "Every formula, trust label and limitation, as published.",
                "mimeType": "text/html"})
    return out


def _read_resource(uri):
    """(contents list) or None when the uri is unknown."""
    if uri == "observatory://sections":
        return [{"uri": uri, "mimeType": "application/json",
                 "text": json.dumps({"sections": registry.by_section()}, ensure_ascii=False)}]
    if uri == "observatory://methodology":
        try:
            text = METHODOLOGY_PATH.read_text(encoding="utf-8")
        except OSError:
            return None
        return [{"uri": uri, "mimeType": "text/html", "text": text}]
    prefix = "observatory://datasets/"
    if uri.startswith(prefix):
        m = registry.get(uri[len(prefix):])
        if m is None:
            return None
        m["available"] = registry.available(m["id"])
        return [{"uri": uri, "mimeType": "application/json",
                 "text": json.dumps(m, ensure_ascii=False)}]
    return None


def _enabled():
    """Kill switch — on unless MCP_ENABLED is set to an explicit falsy value."""
    return os.environ.get("MCP_ENABLED", "1").strip().lower() in (
        "1", "true", "yes", "on")


# Per-IP rate limit, same in-process shape as /ask's. The tools are cheap
# reads, but an agent loop that goes off the rails should hit a wall.
WINDOW_SECONDS = 60
MAX_PER_WINDOW = 120
_HITS = {}


def _rate_limited(client_ip):
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS
    for ip in [ip for ip, hits in _HITS.items() if not hits or hits[-1] < cutoff]:
        del _HITS[ip]
    hits = [t for t in _HITS.get(client_ip, []) if t >= cutoff]
    if len(hits) >= MAX_PER_WINDOW:
        _HITS[client_ip] = hits
        return True
    hits.append(now)
    _HITS[client_ip] = hits
    return False


# ---------------------------------------------------------------------------
# JSON-RPC handling
# ---------------------------------------------------------------------------

def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def _handle_one(msg):
    """One JSON-RPC message in, one response dict out — or None for
    notifications and client responses, which get no reply."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        bad_id = msg.get("id") if isinstance(msg, dict) else None
        return _error(bad_id, -32600, "Invalid Request")

    method = msg.get("method")
    if method is None:
        return None  # a response from the client (e.g. to a ping); nothing to do
    if "id" not in msg:
        return None  # a notification (initialized, cancelled, …); none need action
    msg_id = msg["id"]
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": _instructions(_toolset()),
        })

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": _tools_for(_toolset())})

    if method == "tools/call":
        name = params.get("name")
        toolset = _toolset()
        arguments = params.get("arguments") or {}
        if toolset != "v1" and name in tools_v2.IMPLS:
            text, is_error = tools_v2.run_tool(name, arguments)
        elif toolset != "v2" and (name in TOOL_IMPLS or name in EQUITY_TOOL_IMPLS
                                  or name in SEC_TOOL_IMPLS):
            text, is_error = run_tool(name, arguments)
        else:
            return _error(msg_id, -32602, "Unknown tool: %s" % name)
        return _result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        })

    if method == "resources/list":
        return _result(msg_id, {"resources": _resources()})

    if method == "resources/templates/list":
        return _result(msg_id, {"resourceTemplates": []})

    if method == "resources/read":
        uri = params.get("uri") or ""
        contents = _read_resource(uri)
        if contents is None:
            return _error(msg_id, -32002, "Resource not found: %s" % uri)
        return _result(msg_id, {"contents": contents})

    return _error(msg_id, -32601, "Method not found: %s" % method)


def _handle(message):
    if isinstance(message, list):
        # Batches: sent by 2025-03-26 clients, removed again in 2025-06-18.
        # Answering them costs nothing and beats guessing the client version.
        if not message:
            return _error(None, -32600, "Invalid Request")
        replies = [r for r in (_handle_one(m) for m in message) if r is not None]
        return replies or None
    return _handle_one(message)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

router = APIRouter(include_in_schema=False)

_OFF = {"error": "The MCP endpoint is turned off on this server."}
_STATELESS = {"error": ("This MCP server is stateless. POST JSON-RPC 2.0 "
                        "messages to this URL; see /connect.html to set up "
                        "a client.")}


@router.post("/mcp")
async def mcp_post(request: Request):
    if not _enabled():
        return JSONResponse(_OFF, status_code=503)
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        return JSONResponse(
            _error(None, -32000, "Rate limit exceeded — try again in a minute."),
            status_code=429)
    try:
        message = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    # Tools run DuckDB queries synchronously; keep them off the event loop.
    reply = await run_in_threadpool(_handle, message)
    if reply is None:
        return Response(status_code=202)  # notifications get no body
    return JSONResponse(reply)


@router.get("/mcp")
def mcp_get():
    # No server-initiated stream to offer; 405 is the spec's answer for that.
    return JSONResponse(_STATELESS, status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
def mcp_delete():
    # No sessions exist, so there is nothing to terminate.
    return JSONResponse(_STATELESS, status_code=405, headers={"Allow": "POST"})
