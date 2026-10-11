"""The tools a Codex or Claude Code run from the desk's AI tab may call, over stdio MCP.

app/research_ai.py starts the CLI with this as its only MCP server and its
shell, file and browser tools switched off, so the run can do only this: read
and change the one draft it was started on (as the writer, "via Codex" or "via
Claude" in the history), run the publish checklist, read a web page through
the desk's guarded reader, and read the platform's data through the same
functions that serve /api/v1. A run on an answer-only skill (spec read_only)
is not offered the tools that change the draft. The spec file names the writer, the article and
a call log the desk reads to show progress.

Every tool is annotated read-only: Codex in exec mode cancels any tool not so
annotated because it cannot ask a person. The draft tools do write — to that
one draft, with a "Before AI" copy in its history for Undo — and nothing else.

    python -m app.research_ai_mcp --spec /tmp/codex-work-x/spec.json
"""
import argparse
import json
import sys
import time

from . import research_ai, staff, staff_google

PROTOCOL = "2025-06-18"


class Context(object):
    def __init__(self, spec):
        person = staff.get(spec["staff_id"])
        if person is None:
            raise SystemExit("no such person")
        creds = spec.get("creds") or {}
        if creds.get("google_token"):
            staff_google.hand_token(person["id"], creds["google_token"])
        self.ctx = {"person": person, "client": spec["client"], "article_id": spec["article_id"],
                    "read_only": bool(spec.get("read_only")),
                    "plan_words": spec.get("plan_words"),
                    "creds": {"connector_keys": creds.get("connector_keys") or {},
                              "data_source_keys": creds.get("data_source_keys") or {}}}
        self.log = spec.get("log")
        self.tools = [{"name": t["name"], "description": t["description"],
                       "inputSchema": t["parameters"],
                       "annotations": {"readOnlyHint": True, "openWorldHint": False}}
                      for t in research_ai.tool_list(self.ctx)]

    def call(self, name, args):
        args = args if isinstance(args, dict) else {}
        text, is_err = research_ai.call_tool(self.ctx, name, args)
        if self.log:
            shown = dict((k, v) for k, v in args.items() if k != "markdown")
            with open(self.log, "a") as f:
                f.write(json.dumps({"at": time.time(), "name": name, "args": shown,
                                    "error": bool(is_err)}, ensure_ascii=False) + "\n")
        return text, is_err


def handle(ctx, msg):
    """One JSON-RPC message in, the reply out (None for a notification)."""
    mid = msg.get("id")
    method = msg.get("method")
    if mid is None:
        return None
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": want, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "plover", "version": "1.0.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": ctx.tools}
    elif method == "tools/call":
        p = msg.get("params") or {}
        text, is_err = ctx.call(p.get("name") or "", p.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": bool(is_err)}
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "Method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(ctx, inp, out):
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            reply = handle(ctx, msg)
        except Exception as exc:                              # noqa: BLE001
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": "Tool failed: %s" % exc}}
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    a = ap.parse_args(argv)
    with open(a.spec) as f:
        spec = json.load(f)
    serve(Context(spec), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
