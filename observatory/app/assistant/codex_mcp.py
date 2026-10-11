"""The tools a Codex run may call, served to Codex over stdio MCP.

Codex (app/assistant/codex.py) starts this as its only MCP server, with its
shell and every other built-in tool switched off, so these are the only things
it can do: read the platform's data through the same functions that serve
/api/v1, and draw a chart from what it read. Each call goes through the same
Run the other providers use — the specialist's allowlist, the tool-call
budget, the audit trail (ia_tool_calls under the run this process was started
for) and the chart builder, which reads the full results kept in this
process.

Every tool is annotated read-only. That is true of the data tools; draw_chart
writes one chart record under this run and nothing outward, and Codex in exec
mode cancels any tool not so annotated because it cannot ask a person.

    python -m app.assistant.codex_mcp --account 1 --hire 3 --run 42
"""
import argparse
import json
import sys

from . import runner, specialists, store

PROTOCOL = "2025-06-18"
# What a Codex run may use, before the specialist's own allowlist narrows it.
# Desk actions (posting, approvals, files) stay with the other providers for
# now: a Codex run's reply is posted or threaded by the runner itself.
ALLOWED = set(specialists.READ_TOOLS) | {"my_coverage", "draw_chart"}


class Context(object):
    def __init__(self, account_id, hire_id, run_id):
        hire = store.hire(account_id, hire_id)
        if hire is None:
            raise SystemExit("no such hire")
        self.run = runner.Run(account_id, hire, "codex", "", mode="thread")
        self.run.run_id = run_id
        self.run.ext = {}           # the desk's outside MCP servers are not offered to Codex
        self.run.drive = set()
        self.run.market = []
        self.run.allow = set(self.run.spec["tools"]) & ALLOWED
        self.tools = []
        for s in runner.tool_schemas([t for t in self.run.spec["tools"] if t in self.run.allow]):
            self.tools.append({"name": s["name"], "description": s["description"],
                               "inputSchema": s["parameters"],
                               "annotations": {"readOnlyHint": True, "openWorldHint": False}})

    def call(self, name, args):
        run = self.run
        if run.n_calls >= runner.BUDGET["tool_calls"]:
            return runner._fail("Tool-call budget exhausted; finish with what you have."), True
        import time
        t0 = time.time()
        text, is_err = run._call(name, args if isinstance(args, dict) else {})
        run.n_calls += 1
        store.record_call(run.run_id, run.n_calls, name, args or {}, text,
                          (time.time() - t0) * 1000, is_err)
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
        name = p.get("name") or ""
        text, is_err = ctx.call(name, p.get("arguments") or {})
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
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--hire", type=int, required=True)
    ap.add_argument("--run", type=int, required=True)
    a = ap.parse_args(argv)
    serve(Context(a.account, a.hire, a.run), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
