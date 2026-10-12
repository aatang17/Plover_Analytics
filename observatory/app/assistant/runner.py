"""The runner: one specialist, one task, one audited run.

Builds the prompt from the specialist's brief and the desk's context, drives
the tool loop on the customer's model key, enforces budgets and the tool
allowlist, and writes every call to the audit. Read tools are the same
functions that serve /api/v1 (tools_v2.run_tool). Action tools — post to the
desk, ask for approval, write a workspace file — are the only way a run
affects anything, and none of them sends anything outward: an approval row
waits for a person, and delivery.py acts only on a decided row.

Runs synchronously and is safe to call from a request thread or from the CLI
(`python -m app.assistant.runner --all` for scheduled runs).
"""
import argparse
import datetime
import json
import sys
import time

from .. import tools_v2
from . import (charts, codex, digest, drive, gateway, keychain, market_data, mcp_client,
               memory, specialists, store)

# Tokens are counted cumulatively: every turn re-sends the conversation so far,
# so a run over five companies reads its early results five times. 60k (the
# first setting) stopped a three-company Coverage Monitor run on 23 Sep 2026
# before it posted anything. 250k is still cents on DeepSeek and well under a
# dollar on Claude Sonnet, on the customer's own key.
BUDGET = {"tool_calls": 30, "tokens": 250000, "seconds": 300, "iterations": 40}
MAX_TOOL_RESULT_CHARS = 8000
MAX_HISTORY = 12

RULES = (
    "Rules that apply to every figure. Use only numbers returned by a tool in this run; "
    "never answer from memory. A missing value is missing, never zero. Keep official "
    "figures (as published or as filed) distinct from calculated ones, and quote the "
    "formula from `calc` for anything calculated. Never rank or sum across measure types. "
    "Give the document id, release or `cite` URL beside every number you state. Never "
    "send anything outward yourself: the only outward path is request_approval, and a "
    "person decides it. Write plainly and briefly: lead with the finding, at most five "
    "short sentences or one small table. Never describe your own tool calls, post ids or "
    "process; the reader sees those separately. Give amounts in readable units (¥684.4bn, "
    "¥4.34tn) and cite each source once. When a trend answers the question better than "
    "a sentence and draw_chart is among your tools, draw it rather than listing the values."
)

ACTION_SCHEMAS = [
    {"name": "my_coverage",
     "description": "The companies this desk follows: securities code and name.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "post_to_desk",
     "description": ("Post a short note to the desk's feed. Stays inside the desk. "
                     "Requires at least one source (a document id, release id or cite URL)."),
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "The note, one to four sentences."},
         "sources": {"type": "array", "items": {"type": "string"},
                     "description": "Document ids, release ids or cite URLs the note rests on."},
         "charts": {"type": "array", "items": {"type": "integer"},
                    "description": "Chart ids from draw_chart to show under this note."}},
         "required": ["text", "sources"]}},
    {"name": "request_approval",
     "description": ("Ask a person to approve an outward action. Nothing is sent until they "
                     "do. Supported action: 'slack' (post the text to the desk's channel)."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["slack"]},
         "text": {"type": "string", "description": "Exactly what would be sent."},
         "sources": {"type": "array", "items": {"type": "string"}}},
         "required": ["action", "text", "sources"]}},
    {"name": "write_workspace_file",
     "description": "Write a text file into this specialist's workspace (notes, CSV, rules).",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Relative path such as notes/2026-09.md"},
         "content": {"type": "string"}},
         "required": ["path", "content"]}},
    charts.SCHEMA,
]
ACTION_BY_NAME = dict((s["name"], s) for s in ACTION_SCHEMAS)


class RunnerError(Exception):
    pass


EXT_PREFIX = "ext_"


def _ext_slug(server):
    keep = [c if (c.isalnum() or c == "_") else "_" for c in (server["label"] or "").lower()]
    return ("".join(keep).strip("_") or "server")[:20] + str(server["id"])


def external_tools(account_id):
    """Tools from the desk's own MCP servers, named so they cannot collide
    with ours, with the server they belong to. Cached on the server record by
    the last connection check: a run never waits on a tools/list round trip."""
    out = []
    for srv in store.mcp_servers(account_id, enabled_only=True):
        slug = _ext_slug(srv)
        for t in srv["tools"]:
            out.append({"server": srv, "remote_name": t["name"],
                        "schema": {"name": EXT_PREFIX + slug + "_" + t["name"],
                                   "description": ("[%s] " % srv["label"]) + (t.get("description") or ""),
                                   "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
    return out


def _read_schemas():
    out = {}
    for d in tools_v2.descriptors():
        out[d["name"]] = {"name": d["name"], "description": d["description"],
                          "parameters": d.get("inputSchema") or {"type": "object", "properties": {}}}
    return out


def tool_schemas(allowlist):
    """The tools this run may call, in the gateway's neutral shape."""
    read = _read_schemas()
    out = []
    for name in allowlist:
        if name in read:
            out.append(read[name])
        elif name in ACTION_BY_NAME:
            out.append(ACTION_BY_NAME[name])
    return out


def _system_prompt(spec, coverage, last_summary):
    today = datetime.date.today().isoformat()
    lines = ["You are %s, a specialist on an investor's desk. Today is %s." % (spec["name"], today),
             "", "Your brief: " + spec["brief"], "", RULES, "",
             "Where the data comes from: " + tools_v2.instructions()]
    if coverage:
        lines.append("")
        lines.append("The desk's coverage list: " + "; ".join(
            "%s (%s)" % (c.get("name") or "?", c["sec_code"]) for c in coverage))
    else:
        lines.append("")
        lines.append("The desk's coverage list is empty.")
    if last_summary:
        lines.append("")
        lines.append("What you found last time: " + last_summary[:1500])
    return "\n".join(lines)


# ---- compacting tool results for a model's eyes
# The public tools explain themselves in every result: notes, formula prose,
# vintage and truncation metadata, raw XBRL facts. A reader needs that once;
# a model paying per token on every turn does not. Values, ids, dates, cite
# URLs and the short `calc` labels stay; explanations and bulk go.
DROP_KEYS = ("vintage", "truncated", "tool", "section", "shape", "sha256", "parser_version",
             "facts", "notes", "note", "daily_rows", "lifecycle_labels", "names_note")
DROP_SUFFIX = "_note"
MAX_ROWS = 12
MAX_STR = 240


def _compact(obj, depth=0):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP_KEYS or k.endswith(DROP_SUFFIX):
                continue
            # A financials row states each value under `values`, then again
            # as the raw filed lines with their XBRL element names: the same
            # numbers twice, at five times the length. The values stay.
            if k == "elements" or (k == "lines" and "values" in obj):
                continue
            if k == "provenance" and isinstance(v, dict):
                v = dict((kk, vv) for kk, vv in v.items() if kk in ("trust", "document", "credit"))
            cv = _compact(v, depth + 1)
            if cv is None or cv == {} or cv == []:
                continue
            out[k] = cv
        return out
    if isinstance(obj, list):
        rows = [_compact(x, depth + 1) for x in obj[:MAX_ROWS]]
        rows = [r for r in rows if r is not None]
        # A column that says the same thing on every row (the filer's name,
        # its code) is said once, at the top, not once per row.
        if len(rows) >= 2 and all(isinstance(r, dict) for r in rows):
            same = [k for k in rows[0] if all(k in r and r[k] == rows[0][k] for r in rows[1:])
                    and not isinstance(rows[0][k], (dict, list))]
            if same:
                hoisted = dict((k, rows[0][k]) for k in same)
                rows = [dict((k, v) for k, v in r.items() if k not in same) for r in rows]
                rows.insert(0, {"same_on_every_row": hoisted})
        if len(obj) > MAX_ROWS:
            rows.append("... %d more rows not shown; narrow the request" % (len(obj) - MAX_ROWS))
        return rows
    if obj is None or obj == "":
        return None
    if isinstance(obj, str) and len(obj) > MAX_STR and depth > 0:
        return obj[:MAX_STR] + "..."
    return obj


def compact_result(text):
    """A tool result with the explanations stripped. Never changes a number."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(data, dict) or "error" in data:
        return text
    return json.dumps(_compact(data), ensure_ascii=False)


def _clip(text):
    if text is None:
        return ""
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + '... (result truncated at %d characters; ask for less)' % MAX_TOOL_RESULT_CHARS
    return text


def _fail(msg):
    return json.dumps({"error": msg})


class Run(object):
    """State for one run; `execute` drives it."""

    def __init__(self, account_id, hire, trigger, task, history=None, mode="desk", remember=False):
        self.account_id = account_id
        self.hire = hire
        self.spec = specialists.get(hire["slug"])
        if self.spec is None:
            raise RunnerError("Unknown specialist '%s'." % hire["slug"])
        self.trigger = trigger
        self.task = task
        self.history = history or []
        self.mode = mode
        # The person's own chat reads and adds to their memory (memory.py);
        # scheduled runs and outside connections never do.
        self.memory = bool(remember) and memory.enabled(account_id)
        self.allow = set(self.spec["tools"])
        # The desk's own MCP servers are offered to every specialist on it:
        # they were added by the person whose desk this is, for their agents
        # to use. Each call is audited like any other.
        self.ext = dict((e["schema"]["name"], e) for e in external_tools(account_id))
        # The desk's Drive, likewise: the person put those files there (or
        # connected their Google Drive, read-only) for the desk to use.
        self.drive = set(drive.TOOL_NAMES) if drive.available(account_id) else set()
        # The desk's own market-data accounts, likewise: offered only when one
        # is connected and switched on.
        self.market = market_data.tool_schemas(account_id)
        self.posts = []
        self.approvals = []
        self.files = []
        self.charts = []
        # Full results of this run's data calls, by call number: what a chart
        # is read from. The model sees a shortened copy; a chart never does.
        self.data_calls = {}
        self.usage = {"in": 0, "out": 0}
        self.n_calls = 0
        self.run_id = None

    # ---- action tools

    def _sources_ok(self, sources):
        return isinstance(sources, list) and any(str(s).strip() for s in sources)

    def _act(self, name, args):
        if name == "my_coverage":
            return json.dumps({"coverage": store.coverage(self.account_id, (self.hire.get("config") or {}).get("list_id"))}, ensure_ascii=False)
        if name == "post_to_desk":
            text = (args.get("text") or "").strip()
            if not text:
                return _fail("post_to_desk needs text.")
            if not self._sources_ok(args.get("sources")):
                return _fail("post_to_desk needs at least one source (document id, release or cite URL).")
            p = store.add_post(self.account_id, self.spec["slug"], text, hire_id=self.hire["id"],
                               run_id=self.run_id, refs=[str(s) for s in args["sources"]])
            self.posts.append(p)
            want = [c for c in (args.get("charts") or []) if c in self.charts]
            store.attach_charts(self.account_id, want, p["id"])
            return json.dumps({"posted": True, "post_id": p["id"]})
        if name == "draw_chart":
            try:
                spec = charts.build(args, self.data_calls)
            except charts.ChartError as exc:
                return _fail(str(exc))
            cid = store.add_chart(self.account_id, self.run_id, spec)
            self.charts.append(cid)
            return json.dumps({"chart_id": cid, "plotted": charts.summary(spec),
                               "note": ("Drawn under your reply from the full result, which "
                                        "may cover more than you were shown. Describe it by "
                                        "what was plotted; do not list its values again.")},
                              ensure_ascii=False)
        if name == "request_approval":
            action = args.get("action")
            text = (args.get("text") or "").strip()
            if action != "slack":
                return _fail("Only the 'slack' action exists.")
            if not text:
                return _fail("request_approval needs the exact text.")
            if not self._sources_ok(args.get("sources")):
                return _fail("request_approval needs at least one source.")
            a = store.add_approval(self.account_id, self.run_id, self.hire["id"], action,
                                   {"text": text, "sources": [str(s) for s in args["sources"]]})
            self.approvals.append(a)
            return json.dumps({"approval_id": a["id"], "status": "pending",
                               "note": "A person will decide. Nothing has been sent."})
        if name == "write_workspace_file":
            path = (args.get("path") or "").strip().lstrip("/")
            content = args.get("content")
            if not path or ".." in path or not isinstance(content, str):
                return _fail("write_workspace_file needs a relative path and text content.")
            f = store.write_file(self.account_id, self.hire["id"], path[:200], content[:200000])
            self.files.append(f)
            return json.dumps({"written": f["path"], "sha256": f["sha256"]})
        return _fail("Unknown tool '%s'." % name)

    def _call(self, name, args):
        """Dispatch one call through the allowlist. Returns (text, is_error)."""
        if name in memory.TOOL_NAMES and self.memory:
            return memory.run_tool(self.account_id, name, args, "run:%s" % self.run_id,
                                   memory.ASSISTANT)
        market = set(t["name"] for t in self.market)
        if name not in self.allow and name not in self.ext and name not in self.drive \
                and name not in market:
            return _fail("'%s' is not in this specialist's tool list." % name), True
        if not isinstance(args, dict):
            return _fail("Arguments must be an object."), True
        if name in self.ext:
            e = self.ext[name]
            srv = e["server"]
            secret = None
            if srv.get("secret_id"):
                try:
                    secret = keychain.open_(store.secret_ct(self.account_id, srv["secret_id"]) or "")
                except (ValueError, RuntimeError):
                    return _fail("The key stored for '%s' could not be read." % srv["label"]), True
            text, is_err = mcp_client.call_tool(srv, e["remote_name"], args, secret)
            return _clip(text), is_err
        if name in self.drive:
            text, is_err = drive.run_tool(self.account_id, name, args)
            return _clip(text), is_err
        if name in market:
            text, is_err = market_data.run_tool(self.account_id, args)
            return _clip(text), is_err
        if name in ACTION_BY_NAME:
            text = self._act(name, args)
            return text, text.startswith('{"error"')
        text, is_err = tools_v2.run_tool(name, args)
        if is_err:
            return text, is_err
        return self._numbered(name, args, text), is_err

    def _numbered(self, name, args, text):
        """The shortened result, stamped with its call number, and the full
        result kept for draw_chart. The number is the audit trail's own."""
        try:
            full = json.loads(text)
        except (TypeError, ValueError):
            return compact_result(text)
        if not isinstance(full, dict):
            return compact_result(text)
        seq = self.n_calls + 1
        self.data_calls[seq] = {"seq": seq, "name": name, "args": args, "result": full}
        short = json.loads(compact_result(text))
        return json.dumps(dict([("call", seq)] + list(short.items())), ensure_ascii=False)

    # ---- the loop

    def execute(self):
        desk = store.desk(self.account_id)
        provider = desk["model_provider"]
        # Monitors take the desk's monitor model when one is set (a cheaper,
        # faster model is enough once the bookkeeping is done in code);
        # research specialists take the main model.
        tier = self.spec.get("tier", "research")
        model = (desk.get("monitor_model") if tier == "monitor" else None) or \
            desk["model_name"] or gateway.default_model(provider or "")
        self.run_id = store.start_run(self.account_id, self.hire["id"], self.spec["slug"],
                                      self.hire["version"], self.trigger, self.task, model)
        try:
            key = self._model_key(desk)
        except RunnerError as exc:
            store.end_run(self.run_id, "failed", error=str(exc))
            return self._result("failed", "", str(exc))

        # A prepared run: code fetches and diffs, the model only writes.
        if self.spec.get("prepare") == "coverage" and self.mode == "desk" \
                and self.task == self.spec["task"]:
            return self._prepared_coverage(desk, provider, key, model)

        # A chat stands alone: its context is its own thread, never the
        # answer to someone else's question.
        last = None
        for r in ([] if self.spec.get("chat") else store.runs(self.account_id, limit=20)):
            if r["hire_id"] == self.hire["id"] and r["outcome"] in ("done", "approval_waiting") \
                    and r["summary"]:
                last = r["summary"]
                break
        system = _system_prompt(self.spec, store.coverage(self.account_id, (self.hire.get("config") or {}).get("list_id")), last)
        if self.memory:
            system += "\n\n" + memory.prompt_text(self.account_id).strip()
        messages = [{"role": "system", "content": system}]
        for turn in self.history[-MAX_HISTORY:]:
            if turn.get("role") in ("user", "assistant") and turn.get("text"):
                messages.append({"role": turn["role"], "content": turn["text"][:4000]})
        messages.append({"role": "user", "content": self.task})
        tools = (tool_schemas(self.spec["tools"]) + [e["schema"] for e in self.ext.values()]
                 + [t for t in drive.TOOL_SCHEMAS if t["name"] in self.drive] + self.market
                 + (memory.TOOL_SCHEMAS if self.memory else []))

        # Codex on the desk's ChatGPT plan: the same prompt and history, its
        # tool calls served and audited by codex_mcp under this run.
        if provider == codex.PROVIDER:
            text, outcome, error = codex.run(self, messages, model or None,
                                             BUDGET["seconds"])
            self.n_calls = len(store.calls(self.run_id))
            self.charts = [c["id"] for c in store.charts_for_run(self.run_id)]
            return self._finish(outcome, text, error)

        started = time.time()
        text = ""
        outcome = "done"
        error = None
        for _ in range(BUDGET["iterations"]):
            if time.time() - started > BUDGET["seconds"]:
                outcome, error = "budget", "The run hit its time budget before finishing."
                break
            if self.usage["in"] + self.usage["out"] > BUDGET["tokens"]:
                outcome, error = "budget", "The run hit its token budget before finishing."
                break
            try:
                turn = gateway.complete(provider, key, model, messages, tools)
            except gateway.GatewayError as exc:
                outcome, error = "failed", str(exc)
                break
            self.usage["in"] += turn["usage"]["in"]
            self.usage["out"] += turn["usage"]["out"]
            if not turn["tool_calls"]:
                text = turn["text"]
                break
            messages.append({"role": "assistant", "content": turn["text"],
                             "tool_calls": turn["tool_calls"]})
            for tc in turn["tool_calls"]:
                if self.n_calls >= BUDGET["tool_calls"]:
                    result, is_err = _fail("Tool-call budget exhausted; finish with what you have."), True
                else:
                    t0 = time.time()
                    result, is_err = self._call(tc["name"], tc.get("args") or {})
                    self.n_calls += 1
                    store.record_call(self.run_id, self.n_calls, tc["name"], tc.get("args") or {},
                                      result, (time.time() - t0) * 1000, is_err)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["name"],
                                 "content": _clip(result)})
        else:
            outcome, error = "budget", "The run needed more turns than allowed."
        return self._finish(outcome, text, error)

    def _finish(self, outcome, text, error):
        """Post, attach, record: the end of every run, whichever model ran it."""
        if outcome == "done" and self.approvals:
            outcome = "approval_waiting"
        # On the desk, a run that already posted its notes says nothing more:
        # the closing reply would only narrate them ("posted the verdict…").
        # A run that posted nothing has its reply posted instead.
        if outcome in ("done", "approval_waiting") and self.mode == "desk" and text \
                and not self.posts:
            self.posts.append(store.add_post(self.account_id, self.spec["slug"], text,
                                             hire_id=self.hire["id"], run_id=self.run_id))
        # A chart the run drew but did not attach goes under its last note,
        # so nothing drawn is lost from the desk.
        if self.mode == "desk" and self.charts and self.posts:
            store.attach_charts(self.account_id, self.charts, self.posts[-1]["id"])
        # A run that stops early says so on the desk rather than going quiet:
        # silence reads as "nothing changed", which is a different claim.
        if outcome in ("budget", "failed") and self.mode == "desk":
            store.add_post(self.account_id, "system",
                           "%s stopped before finishing: %s" % (self.spec["name"], error or outcome),
                           hire_id=self.hire["id"], run_id=self.run_id)
        store.end_run(self.run_id, outcome, summary=(text or error or "")[:2000], error=error,
                      tokens_in=self.usage["in"], tokens_out=self.usage["out"],
                      tool_calls=self.n_calls)
        return self._result(outcome, text, error)

    def _prepared_coverage(self, desk, provider, key, model):
        coverage = store.coverage(self.account_id, (self.hire.get("config") or {}).get("list_id"))
        if not coverage:
            msg = "The coverage list is empty; add a company first."
            store.end_run(self.run_id, "done", summary=msg)
            self.posts.append(store.add_post(self.account_id, self.spec["slug"], msg,
                                             hire_id=self.hire["id"], run_id=self.run_id))
            return self._result("done", msg, None)
        t0 = time.time()
        cur = digest.build(coverage)
        prev_row = store.read_file(self.account_id, self.hire["id"], digest.STATE_PATH)
        prev = None
        if prev_row:
            try:
                prev = json.loads(prev_row["content"])
            except ValueError:
                prev = None
        changes = digest.diff(prev, cur)
        # the fetch-and-diff is audited as one call, so the trail shows it
        self.n_calls += 1
        store.record_call(self.run_id, self.n_calls, "coverage_digest",
                          {"companies": len(coverage), "changes": len(changes)},
                          json.dumps(cur, ensure_ascii=False), (time.time() - t0) * 1000, False)
        store.write_file(self.account_id, self.hire["id"], digest.STATE_PATH,
                         json.dumps(cur, ensure_ascii=False, indent=1))
        refs = digest.sources(cur, changes)

        if prev is not None and not changes and self.trigger != "manual":
            # Nothing to write about: say so without spending a token.
            text = "No new filings on %d compan%s since %s." % (
                len(coverage), "y" if len(coverage) == 1 else "ies", prev.get("as_of", "the last run")[:10])
            self.posts.append(store.add_post(self.account_id, self.spec["slug"], text,
                                             hire_id=self.hire["id"], run_id=self.run_id, refs=refs))
            store.end_run(self.run_id, "done", summary=text, tool_calls=self.n_calls)
            return self._result("done", text, None)

        brief = digest.render(cur, changes, first_run=prev is None)
        messages = [
            {"role": "system", "content": "You are %s, a specialist on an investor's desk. %s\n\n%s" % (
                self.spec["name"], self.spec["brief"], RULES)},
            {"role": "user", "content": "Write the desk note from this digest. Every figure below came from "
                                        "the filings named in brackets; use only these, and put the document "
                                        "id beside each figure you quote.\n\n" + brief}]
        try:
            if provider == codex.PROVIDER:
                t, outcome, err = codex.run(self, messages, model or None,
                                            BUDGET["seconds"])
                if outcome != "done":
                    raise gateway.GatewayError(err or outcome)
                turn = {"text": t, "usage": {"in": 0, "out": 0}}
            else:
                turn = gateway.complete(provider, key, model, messages, tools=None, max_tokens=1200)
        except gateway.GatewayError as exc:
            store.add_post(self.account_id, "system", "%s stopped before finishing: %s" % (self.spec["name"], exc),
                           hire_id=self.hire["id"], run_id=self.run_id)
            store.end_run(self.run_id, "failed", error=str(exc), tool_calls=self.n_calls)
            return self._result("failed", "", str(exc))
        self.usage["in"] += turn["usage"]["in"]
        self.usage["out"] += turn["usage"]["out"]
        text = turn["text"].strip() or brief
        self.posts.append(store.add_post(self.account_id, self.spec["slug"], text,
                                         hire_id=self.hire["id"], run_id=self.run_id, refs=refs))
        store.end_run(self.run_id, "done", summary=text[:2000], tokens_in=self.usage["in"],
                      tokens_out=self.usage["out"], tool_calls=self.n_calls)
        return self._result("done", text, None)

    def _model_key(self, desk):
        if not desk["model_provider"]:
            raise RunnerError("No model provider is set for this desk. Open Settings.")
        if desk["model_provider"] == codex.PROVIDER:
            return None          # codex.run unseals the ChatGPT sign-in itself
        if not desk["model_key_set"]:
            raise RunnerError("No model key is stored for this desk. Open Settings.")
        if not keychain.enabled():
            raise RunnerError("ASSISTANT_SECRET is not set on the server, so stored keys "
                              "cannot be read.")
        try:
            return keychain.open_(store.desk_secrets(self.account_id)["model_key_ct"])
        except ValueError:
            raise RunnerError("The stored model key could not be read; store it again.")

    def _result(self, outcome, text, error):
        return {"run_id": self.run_id, "outcome": outcome, "text": text, "error": error,
                "posts": self.posts, "approvals": self.approvals, "files": self.files,
                "charts": store.charts_for_run(self.run_id) if self.run_id else [],
                "usage": self.usage, "tool_calls": self.n_calls,
                "calls": store.calls(self.run_id) if self.run_id else []}


def run(account_id, hire, trigger, task, history=None, mode="desk", remember=False):
    return Run(account_id, hire, trigger, task, history=history, mode=mode, remember=remember).execute()


# --------------------------------------------------------------------- CLI

def _cli(argv):
    ap = argparse.ArgumentParser(description="Run specialists outside the API process.")
    ap.add_argument("--all", action="store_true",
                    help="run every enabled hire on every desk with its default task")
    ap.add_argument("--account", type=int, help="account id (with --slug)")
    ap.add_argument("--slug", help="specialist slug")
    ap.add_argument("--task", help="override the task text")
    args = ap.parse_args(argv)
    jobs = []
    if args.all:
        rows = store.conn().execute("SELECT DISTINCT account_id FROM ia_hires WHERE enabled = 1")
        for r in rows.fetchall():
            for h in store.hires(r["account_id"]):
                if h["enabled"] and not specialists.builtin(h["slug"]):
                    jobs.append((r["account_id"], h))
    elif args.account and args.slug:
        h = store.hire_by_slug(args.account, args.slug)
        if h is None:
            print("no such hire", file=sys.stderr)
            return 2
        jobs.append((args.account, h))
    else:
        ap.print_help()
        return 2
    for account_id, h in jobs:
        spec = specialists.get(h["slug"])
        task = args.task or spec["task"]
        out = run(account_id, h, "schedule", task)
        print("account %s %s: %s (%d tool calls, %d tokens)%s" % (
            account_id, h["slug"], out["outcome"], out["tool_calls"],
            out["usage"]["in"] + out["usage"]["out"],
            " — " + out["error"] if out["error"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
