# -*- coding: utf-8 -*-
"""The research desk's AI tab: a writer's own model working on the open draft.

A writer picks a provider once — their Claude plan through Claude Code, their
ChatGPT plan through Codex, or an OpenAI or Anthropic API key — and then gives
the open article an instruction ("draft this from my notes", "add a chart of
core CPI", "check every number"). The model works on that one draft only:

* tools bound to the article: read the draft, replace or add to it, set its
  title / standfirst / summary, run the publish checklist;
* the platform's own read-only data tools (the public MCP set, tools_v2), so
  every number comes from the same functions as /api/v1;
* read_page, the desk's guarded page reader — or the writer's own copy of a
  page they sent from their browser (research_clips.py), which is how a paid
  article reaches the run; Codex and Claude Code may also search the web with
  their own built-in search;
* search_past_articles and read_past_article (app/article_search.py), so a
  run can link to and keep to what was published before;
* calculate (app/calc.py), so every change, ratio or share it writes is
  worked out by code with its formula, not in the model's head.

A run may carry one picture the writer pasted — a screenshot of another
publisher's chart — which the model sees with the instruction. The desk uses
it to rebuild such a chart from the public data behind it, never from numbers
read off the picture.

It cannot publish, withdraw, delete, or touch another article, and every save
is recorded as "<email> via <provider>" in the draft history, after a "Before
AI" copy that the editor's Undo restores. One run per writer at a time.

A run may follow one of its publication's skills (app/skills.py), and gets
that publication's house style and connectors: each publication keeps its
own in Assistant Settings, and a run never sees another's. A skill that may
not change the draft — Brainstorm, Review, Headlines — is run without the
draft-changing tools, and the writer keeps editing while it works. Any run
can end by offering next steps (offer_next_step), shown as buttons under its
reply: "Plan This" on a brainstormed angle, "Draft It" under a plan.

Each writer picks a length once (Short, Standard, Detailed); every run, the
one-click next steps included, is told it, and a plan over its word limit is
sent back to the AI to shorten before it reaches the draft. A writer may also
keep a writing style of their own — a description and samples of their
writing (app/writing_style.py) — which every run they start follows for
voice, under the publication's house style for format.

Secrets — API keys, the Claude Code token, the Codex sign-in — are sealed with
the assistant keychain (ASSISTANT_SECRET) and opened only for the length of a
run. The CLIs run with their shell, file and browser tools switched off, in an
empty directory, with an environment holding nothing but what they need.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import (article_search, calc, connectors, market_history, pub_coverage, pub_profile, research, research_clips, research_doc as rd, research_mcp,
               research_web, skills, staff, staff_google, tools_v2, writers,
               writing_style)
from .assistant import codex as codex_mod, gateway, keychain, market_data, store as ia_store

PROVIDERS = [
    ("claude_code", "Claude plan (Claude Code)"),
    ("codex", "ChatGPT plan (Codex)"),
    ("openai", "OpenAI API key"),
    ("anthropic", "Anthropic API key"),
]
PROVIDER_LABEL = dict(PROVIDERS)
CLIENT = {"claude_code": "Claude", "codex": "Codex", "openai": "OpenAI", "anthropic": "Claude API"}
DEFAULT_MODEL = {"openai": "gpt-5.5", "anthropic": "claude-sonnet-5-5"}
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or "claude"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_STEPS = 20
RUN_SECONDS = 600
TOOL_TEXT_MAX = 24000
# Settings the tool server needs to open the same stores; none is a secret.
CHILD_ENV = ("STAFF_DB", "RESEARCH_DIR", "WORKSPACE_DB", "OBSERVATORY_DATA_DIR",
             "OBSERVATORY_DB_PATH", "EQUITY_DB_PATH", "SEC_DB_PATH", "SITE_BASE_URL")

_jobs = {}
_jobs_lock = threading.Lock()
_running = {}          # staff id -> job id


class AIError(ValueError):
    """Worded for the writer."""


# ---------------------------------------------------------------------------
# settings

def _open(ct):
    try:
        return keychain.open_(ct) if ct else None
    except (ValueError, RuntimeError):
        return None


def settings_view(person, publication_id=None):
    """The writer's AI settings, with the skills and connections of one
    publication: the open article's, else the writer's own."""
    pid = writers.own_publication(person) if publication_id is None else publication_id
    s = staff.ai_settings(person["id"])
    return {
        "providers": [{"key": k, "label": v, "ready": _ready(k, s)} for k, v in PROVIDERS],
        "provider": s["provider"], "model": s["model"] or "",
        "default_model": DEFAULT_MODEL.get(s["provider"] or "", ""),
        "key_last4": s["key_last4"], "claude_token": bool(s["claude_token_ct"]),
        "codex": codex_status(person), "keychain": keychain.enabled(),
        "claude_available": shutil.which(CLAUDE_BIN) is not None,
        "codex_available": codex_mod.available(),
        "length": _length(s)["key"],
        "lengths": [{"key": x["key"], "label": x["label"], "hint": x["hint"]} for x in LENGTHS],
        "skills": [{"id": k["id"], "name": k["name"], "description": k["description"],
                    "writes": k["writes"]}
                   for k in skills.list_skills(enabled_only=True, publication_id=pid)],
        "reach": _reach(person, pid),
        "publication_id": pid,
        "can_edit_setup": writers.at_least(person, pid, "editor"),
        # what the publication sets for everyone writing in it (pub_profile)
        "publication_ai": pub_profile.ai(pid) if pid is not None else None,
        "publication_length": (pub_profile.defaults(pid)["length"] or None) if pid is not None else None,
        "default_skill": pub_profile.defaults(pid)["default_skill"] if pid is not None else None,
        "style": writing_style.summary(person),
    }


def _reach(person, publication_id):
    """What a run can use beyond the draft, named for the writer."""
    out = ["Plover data", "Web pages"]
    if staff_google.usable(person["id"]):
        out.append("Google Drive")
    out += [market_data.PROVIDERS[p]["name"] for p in market_data.merged(_data_owners(person, publication_id))]
    out += [c["label"] for c in connectors.list_connectors(enabled_only=True, publication_id=publication_id)
            if any(t["on"] for t in c["tools"])]
    return out


def _ready(provider, s):
    if provider in ("openai", "anthropic"):
        return bool(s["key_ct"]) and s["provider"] == provider
    if provider == "claude_code":
        return bool(s["claude_token_ct"])
    if provider == "codex":
        return bool(s["codex_auth_ct"])
    return False


def save_settings(person, provider, model=None, key=None, claude_token=None):
    if provider not in PROVIDER_LABEL:
        raise AIError("Choose one of the providers listed.")
    if (key or claude_token) and not keychain.enabled():
        raise AIError("This server cannot store a key yet: ASSISTANT_SECRET is not set on it.")
    fields = {"provider": provider, "model": (model or "").strip()[:80] or None}
    if provider in ("openai", "anthropic"):
        current = staff.ai_settings(person["id"])
        if key:
            key = key.strip()
            try:
                gateway.ping(provider, key, fields["model"] or DEFAULT_MODEL[provider])
            except gateway.GatewayError as exc:
                raise AIError(_key_problem(provider, str(exc)))
            fields["key_ct"] = keychain.seal(key)
            _models.pop((person["id"], provider), None)
            fields["key_last4"] = keychain.last4(key)
        elif not current["key_ct"] or current["provider"] != provider:
            raise AIError("Paste your %s." % PROVIDER_LABEL[provider])
    if provider == "claude_code" and claude_token:
        token = claude_token.strip()
        if not re.match(r"^sk-ant-[A-Za-z0-9_\-]{20,}$", token):
            raise AIError("That does not look like a Claude Code token. Run `claude setup-token` "
                          "and paste the token it prints (it starts with sk-ant-).")
        fields["claude_token_ct"] = keychain.seal(token)
    return staff.set_ai(person["id"], **fields)


def _key_problem(provider, detail):
    """A failed key check in words, without the provider's raw reply."""
    who = "OpenAI" if provider == "openai" else "Anthropic"
    if re.search(r"\b(401|403)\b", detail):
        return ("That key did not work: %s did not accept it. Check you copied all of it, "
                "and that the account has API access." % who)
    if re.search(r"\b429\b", detail):
        return "That key did not work: %s says the account is out of credit or over its limit." % who
    if re.search(r"\b404\b|model.{0,40}(not found|does not exist|not supported)", detail, re.I):
        return "That key did not work with the model named. Clear the model box to use the default."
    return "That key could not be checked just now (%s). Try again in a minute." % detail.split("{")[0].strip()[:120]


def forget(person, what):
    for p in PROVIDER_LABEL:
        _models.pop((person["id"], p), None)
    if what == "key":
        staff.set_ai(person["id"], key_ct=None, key_last4=None)
    elif what == "claude":
        staff.set_ai(person["id"], claude_token_ct=None)
    elif what == "codex":
        staff.set_ai(person["id"], codex_auth_ct=None, codex_email=None)


# ---------------------------------------------------------------------------
# how long the writer wants what the AI writes

LENGTHS = [
    {"key": "short", "label": "Short", "plan_words": 150,
     "hint": "Plans up to 150 words, articles 400–600 words.",
     "rule": "a plan of at most 150 words; an article of 400 to 600 words; replies as brief "
             "as the skill's format allows, one line per list item"},
    {"key": "standard", "label": "Standard", "plan_words": 250,
     "hint": "Plans up to 250 words, articles 700–1,000 words.",
     "rule": "a plan of at most 250 words; an article of 700 to 1,000 words; replies at the "
             "length the skill asks for"},
    {"key": "detailed", "label": "Detailed", "plan_words": 500,
     "hint": "Plans up to 500 words, articles 1,200–1,800 words.",
     "rule": "a plan of at most 500 words; an article of 1,200 to 1,800 words; replies may "
             "add a sentence of reasoning to each item"},
]
DEFAULT_LENGTH = "short"
# a plan this much over its limit is sent back; a few words over is let through
PLAN_SLACK = 1.2


def _length(s):
    key = (s or {}).get("length") or DEFAULT_LENGTH
    return next((x for x in LENGTHS if x["key"] == key), LENGTHS[0])


def set_length(person, key):
    if key not in [x["key"] for x in LENGTHS]:
        raise AIError("Choose Short, Standard or Detailed.")
    return staff.set_ai(person["id"], length=key)


def plan_words(markdown):
    """Words in a draft's plan: from its '## Plan' heading to the writer's
    notes (or the end). None when the markdown holds no plan."""
    lines = (markdown or "").splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+plan\s*$", l.strip(), re.I)), None)
    if start is None:
        return None
    body = []
    for l in lines[start + 1:]:
        if re.match(r"^##\s+writer[’']?s notes\s*$", l.strip(), re.I):
            break
        body.append(l)
    return len(re.findall(r"\w[\w’'.,%/-]*", "\n".join(body), re.U))


# ---------------------------------------------------------------------------
# the models each provider offers this writer

# Claude Code's own names; each points at the newest model of its family for
# the pinned Claude Code version (2.1.278: Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5).
CLAUDE_MODELS = [
    {"id": "fable", "label": "Fable", "note": "Most capable. Not on every Claude plan."},
    {"id": "opus", "label": "Opus", "note": "Strong writing and reasoning."},
    {"id": "sonnet", "label": "Sonnet", "note": "Balanced speed and quality."},
    {"id": "haiku", "label": "Haiku", "note": "Fastest; for small edits."},
]
MODELS_TTL = 6 * 3600
_models = {}           # (staff id, provider) -> (fetched at, [models])
# OpenAI lists every model the key can reach; these are not for writing.
_OPENAI_SKIP = re.compile(r"embed|tts|whisper|audio|realtime|transcribe|image|dall-e|moderation|"
                          r"search|babbage|davinci|instruct|computer-use|codex", re.I)


def _get_json(url, headers):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise gateway.GatewayError("The model API answered %s." % exc.code)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        raise gateway.GatewayError("The model API could not be reached (%s)." % exc)


def _openai_models(key):
    body = _get_json(gateway.PROVIDERS["openai"]["base_url"] + "/models",
                     {"Authorization": "Bearer " + key})
    rows = [m for m in body.get("data") or []
            if re.match(r"^(gpt-|o\d|chatgpt-)", m.get("id") or "") and not _OPENAI_SKIP.search(m["id"])]
    rows.sort(key=lambda m: (-(m.get("created") or 0), m["id"]))
    return [{"id": m["id"], "label": m["id"], "note": ""} for m in rows]


def _anthropic_models(key):
    body = _get_json(gateway.PROVIDERS["anthropic"]["base_url"] + "/v1/models?limit=100",
                     {"x-api-key": key, "anthropic-version": gateway.ANTHROPIC_VERSION})
    return [{"id": m["id"], "label": m.get("display_name") or m["id"], "note": m["id"]}
            for m in body.get("data") or [] if m.get("id")]


def _codex_models(staff_id, auth):
    """What Codex offers this ChatGPT account: its own catalog, in its order."""
    if not codex_mod.available():
        raise AIError("Codex is not installed on this server.")
    home = tempfile.mkdtemp(prefix="codex-models-")
    try:
        path = os.path.join(home, "auth.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(auth)
        out = subprocess.run([codex_mod.BIN, "debug", "models"], env=codex_mod._env(home), cwd=home,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45)
        with open(path) as f:
            now = f.read()
        if now and now != auth:          # a refreshed sign-in is kept, as after a run
            staff.set_ai(staff_id, codex_auth_ct=keychain.seal(now))
        try:
            rows = json.loads(out.stdout).get("models") or []
        except (ValueError, AttributeError):
            raise AIError("Codex did not return its model list.")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    rows = [m for m in rows if m.get("visibility") == "list" and m.get("slug")]
    rows.sort(key=lambda m: m.get("priority") or 0)
    return [{"id": m["slug"], "label": m.get("display_name") or m["slug"],
             "note": m.get("description") or ""} for m in rows]


def models(person, provider, key=None):
    """{models: [{id, label, note}], default, error}. A pasted key is listed
    with that key (which also proves it works); otherwise the stored one."""
    if provider not in PROVIDER_LABEL:
        raise AIError("Choose one of the providers listed.")
    out = {"provider": provider, "models": [], "default": DEFAULT_MODEL.get(provider, ""),
           "error": None}
    if provider == "claude_code":
        out["models"] = CLAUDE_MODELS
        return out
    s = staff.ai_settings(person["id"])
    cache_key = (person["id"], provider)
    if not key:
        hit = _models.get(cache_key)
        if hit and time.time() - hit[0] < MODELS_TTL:
            out["models"] = hit[1]
            return out
    try:
        if provider == "codex":
            auth = _open(s["codex_auth_ct"])
            if not auth:
                out["error"] = "Connect ChatGPT first to see the models your plan offers."
                return out
            rows = _codex_models(person["id"], auth)
        else:
            use = (key or "").strip() or (_open(s["key_ct"]) if s["provider"] == provider else None)
            if not use:
                out["error"] = "Paste your key to see the models it can use."
                return out
            rows = _openai_models(use) if provider == "openai" else _anthropic_models(use)
    except (AIError, gateway.GatewayError, subprocess.SubprocessError, OSError) as exc:
        detail = str(exc)
        if provider in ("openai", "anthropic") and re.search(r"\b(401|403)\b", detail):
            out["error"] = _key_problem(provider, detail)
        else:
            out["error"] = "The model list could not be fetched just now. %s" % detail[:160]
        return out
    if not key:
        _models[cache_key] = (time.time(), rows)
    out["models"] = rows
    return out


# ---------------------------------------------------------------------------
# Codex sign-in (its own device code, the same flow as the assistant's)

_logins = {}


def codex_status(person):
    s = staff.ai_settings(person["id"])
    out = {"connected": bool(s["codex_auth_ct"]), "email": s["codex_email"],
           "pending": None, "error": None}
    st = _logins.get(person["id"])
    if st:
        if not st["done"] and time.time() - st["started"] > codex_mod.LOGIN_TIMEOUT:
            _cancel_login(person["id"])
            out["error"] = "The code expired. Start again."
        elif not st["done"]:
            out["pending"] = {"url": st["url"], "code": st["code"]}
        elif st["error"]:
            out["error"] = st["error"]
    return out


def _cancel_login(staff_id):
    st = _logins.pop(staff_id, None)
    if st and not st["done"]:
        st["cancelled"] = True
        try:
            st["proc"].kill()
        except Exception:  # noqa: BLE001
            pass


def codex_login(person):
    if not codex_mod.available():
        raise AIError("Codex is not installed on this server.")
    if not keychain.enabled():
        raise AIError("This server cannot store a sign-in yet: ASSISTANT_SECRET is not set.")
    sid = person["id"]
    _cancel_login(sid)
    home = tempfile.mkdtemp(prefix="codex-login-")
    proc = subprocess.Popen([codex_mod.BIN, "login", "--device-auth"], env=codex_mod._env(home),
                            cwd=home, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    st = {"proc": proc, "url": None, "code": None, "error": None, "done": False,
          "started": time.time()}
    _logins[sid] = st
    ready = threading.Event()

    def watch():
        lines, want = [], False
        for raw in proc.stdout:
            line = codex_mod._ANSI.sub("", raw).strip()
            lines.append(line)
            if not st["url"]:
                m = re.search(r"https://\S+", line)
                if m:
                    st["url"] = m.group(0)
            if "one-time code" in line.lower():
                want = True
                continue
            if want and line and not st["code"]:
                st["code"] = line.split()[0]
            if st["url"] and st["code"]:
                ready.set()
        proc.wait()
        auth = os.path.join(home, "auth.json")
        if proc.returncode == 0 and os.path.exists(auth):
            with open(auth) as f:
                text = f.read()
            staff.set_ai(sid, codex_auth_ct=keychain.seal(text), codex_email=codex_mod._email(text))
            _models.pop((sid, "codex"), None)
        elif not st.get("cancelled"):
            st["error"] = "Sign-in did not finish: " + (" ".join(lines[-3:]) or "no answer")[:300]
        st["done"] = True
        shutil.rmtree(home, ignore_errors=True)
        ready.set()

    threading.Thread(target=watch, daemon=True).start()
    ready.wait(30)
    if not (st["url"] and st["code"]):
        _cancel_login(sid)
        raise AIError(st["error"] or "Codex did not return a sign-in code. Try again.")
    return {"url": st["url"], "code": st["code"],
            "expires_in_minutes": codex_mod.LOGIN_TIMEOUT // 60}


# ---------------------------------------------------------------------------
# the tools

def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


ARTICLE_TOOLS = [
    {"name": "get_draft",
     "description": "The open article's draft as editable Markdown, with its title, summary, "
                    "web address and any charts still waiting to be copied. Call this first.",
     "parameters": _schema({})},
    {"name": "replace_draft",
     "description": "Replace the whole draft with new Markdown ('# Title' first; conventions in "
                    "the instructions). Charts already copied from pages and uploaded images are "
                    "kept when their lines are kept.",
     "parameters": _schema({"markdown": {"type": "string"},
                            "summary": {"type": "string", "description": "Optional new summary."}},
                           ["markdown"])},
    {"name": "append_to_draft",
     "description": "Add Markdown to the end of the draft, or to the end of the section whose "
                    "heading you name.",
     "parameters": _schema({"markdown": {"type": "string"},
                            "after_heading": {"type": "string"}}, ["markdown"])},
    {"name": "set_details",
     "description": "Change the title, standfirst (dek) or summary.",
     "parameters": _schema({"title": {"type": "string"}, "dek": {"type": "string"},
                            "summary": {"type": "string"}})},
    {"name": "check_article",
     "description": "What still stops the draft being published, and whether every chart can "
                    "be read.",
     "parameters": _schema({})},
    {"name": "read_page",
     "description": "Read a web page by its address and return its text, title, site and date, "
                    "to quote or cite as a footnote. A page the writer sent from their own "
                    "browser (a paid article they subscribe to: FT, WSJ, Nikkei) is read from "
                    "that copy.",
     "parameters": _schema({"url": {"type": "string"}}, ["url"])},
    {"name": "list_sent_pages",
     "description": "The pages the writer sent from their own browser with Send to Plover, "
                    "newest first: address, title, site and when. Read one with read_page.",
     "parameters": _schema({})},
]
ARTICLE_TOOL_NAMES = set(t["name"] for t in ARTICLE_TOOLS)
WRITE_TOOLS = ("replace_draft", "append_to_draft", "set_details")
NEXT_MAX = 8
LABEL_MAX = 48

NEXT_TOOL = {
    "name": "offer_next_step",
    "description": "Put a button under your reply that starts another run on this draft when the "
                   "writer clicks it: one per brainstormed angle, 'Draft It' under a plan, one per "
                   "proposed chart. Call it once per button, at most %d times." % NEXT_MAX,
    "parameters": _schema({
        "label": {"type": "string", "description": "The button's text, up to %d characters, "
                                                  "e.g. 'Plan This: Bank Margins'." % LABEL_MAX},
        "instruction": {"type": "string", "description": "Everything the next run needs, written "
                                                        "as an instruction to it."},
        "skill": {"type": "string", "description": "Optional: the name of the team skill the next "
                                                  "run follows, e.g. 'Plan'."}},
        ["label", "instruction"])}


SKILL_TOOL = {
    "name": "read_skill",
    "description": "Open one of the team's skills (listed in your instructions) and get its steps. "
                   "Call it when the writer's instruction matches a skill, then follow the steps.",
    "parameters": _schema({"name": {"type": "string", "description": "The skill's name."}}, ["name"])}

DRIVE_TOOLS = [
    {"name": "drive_search",
     "description": "Search the writer's own Google Drive by file name and text. Returns ids, "
                    "names, types and links, newest first.",
     "parameters": _schema({"query": {"type": "string"}})},
    {"name": "drive_read",
     "description": "Read one file from the writer's Google Drive as text (a Doc as text, a Sheet "
                    "as CSV, a PDF's text). Give its id from drive_search, or its link.",
     "parameters": _schema({"file": {"type": "string", "description": "File id or link."}}, ["file"])},
]


def tool_list(ctx=None):
    """Everything a run may call: the draft tools, the team's skills, Plover's
    data, the writer's Google Drive when connected, and every connector tool
    the team has switched on."""
    data = [{"name": d["name"], "description": d["description"], "parameters": d["inputSchema"]}
            for d in tools_v2.descriptors()]
    read_only = (ctx or {}).get("read_only")
    out = [t for t in ARTICLE_TOOLS if not (read_only and t["name"] in WRITE_TOOLS)]
    out += [SKILL_TOOL, NEXT_TOOL] + data + article_search.TOOLS + [calc.TOOL]
    person = (ctx or {}).get("person")
    if person and staff_google.usable(person["id"]):
        out += DRIVE_TOOLS
    pid = _publication(ctx)
    if person:
        out += market_data.tool_schemas(_data_owners(person, pid))
        out += market_history.tool_schemas(person)
    if pid is not None:
        out += [dict((k, t[k]) for k in ("name", "description", "parameters"))
                for t in connectors.run_tools(pid)]
    return out


def _account_id(person):
    """The sign-in account the writer's own data sources are kept under
    (by email; see market_data.account_for). None when there is none yet."""
    return market_data.account_for(person.get("email")) if person and person.get("email") else None


def _data_owners(person, publication_id):
    """Whose market-data keys a run may use, first winning a vendor both
    have: the publication's, then the writer's own."""
    return [ia_store.pub_owner(publication_id) if publication_id is not None else None,
            _account_id(person)]


def run_settings(person, publication_id):
    """The writer's AI settings as a run in this publication uses them: the
    publication's own AI key and length win where its owner set them."""
    s = dict(staff.ai_settings(person["id"]))
    s["ai_from"] = "writer"
    if publication_id is not None:
        pub_ai = pub_profile.ai_for_run(publication_id)
        if pub_ai:
            s.update(provider=pub_ai["provider"], model=pub_ai["model"], key_ct=pub_ai["key_ct"],
                     ai_from="publication")
        length = pub_profile.defaults(publication_id)["length"]
        if length:
            s["length"] = length
            s["length_from"] = "publication"
    return s


def _publication(ctx):
    """The publication whose skills, house style and connectors a run uses:
    its article's (None once the article is gone), else the writer's own.
    A connector carries its publication's keys, so this is also the line
    between one publication's data and another's."""
    ctx = ctx or {}
    if ctx.get("publication_id") is not None:
        return ctx["publication_id"]
    aid = ctx.get("article_id")
    if aid is not None:
        a = research.get(aid)
        return a["publication_id"] if a else None
    person = ctx.get("person")
    return writers.own_publication(person) if person else research.HOME


def _skill_names(publication_id):
    return ", ".join(x["name"] for x in skills.list_skills(True, publication_id)) or "none"


def call_tool(ctx, name, args):
    """(text, is_error) for one tool call within a job context."""
    args = args if isinstance(args, dict) else {}
    person, client, aid = ctx["person"], ctx["client"], ctx["article_id"]
    if name in WRITE_TOOLS and ctx.get("read_only"):
        return json.dumps({"error": "This run answers only: it cannot change the draft. "
                                    "Put your answer in your reply."}), True
    if name == "offer_next_step":
        try:
            offer = clean_offer(args, _publication(ctx))
        except AIError as exc:
            return json.dumps({"error": str(exc)}), True
        offers = ctx.get("offers")
        if offers is not None:
            if len(offers) >= NEXT_MAX:
                return json.dumps({"error": "That is %d buttons already, the most a reply shows." % NEXT_MAX}), True
            offers.append(offer)
        return json.dumps({"ok": True, "button": offer["label"]}), False
    if name in ARTICLE_TOOL_NAMES:
        if name == "list_sent_pages":
            return json.dumps({"pages": [dict((k, c[k]) for k in ("url", "title", "site", "published", "sent_at"))
                                         for c in research_clips.list_for(person["id"])]},
                              ensure_ascii=False), False
        if name == "read_page":
            url = str(args.get("url") or "")
            page = research_clips.find(person["id"], url) if url.strip() else None
            if page is None:
                try:
                    page = research_web.read(url)
                except research_web.WebError as exc:
                    return json.dumps({"error": str(exc)}), True
            text = "\n".join(b["text"] for b in page["blocks"])[:TOOL_TEXT_MAX]
            out = {"url": page["url"], "title": page["title"], "site": page["site"],
                   "published": page["published"], "text": text}
            if page.get("sent"):
                out["sent_from_browser"] = True
                out["accessed"] = time.strftime("%Y-%m-%d", time.gmtime(page["sent_at"]))
            return json.dumps(out, ensure_ascii=False), False
        limit = ctx.get("plan_words")
        n = plan_words(args.get("markdown")) if name == "replace_draft" and limit else None
        if n is not None and n > limit * PLAN_SLACK:
            return json.dumps({"error": "Nothing was saved: the plan is %d words and the writer chose "
                                        "at most %d. Cut it to one line per item, with no lists of "
                                        "values, and call replace_draft again." % (n, limit)}), True
        cur = research.get(aid)
        if cur is None:
            return json.dumps({"error": "The article no longer exists."}), True
        mapped = {"get_draft": "read_article", "check_article": "check_article"}.get(name, name)
        call_args = {"article_id": aid}
        if name in ("replace_draft", "append_to_draft", "set_details"):
            call_args["base_revision"] = cur["revision"]
            call_args.update(dict((k, v) for k, v in args.items()
                                  if k in ("markdown", "summary", "after_heading", "title", "dek")))
        return research_mcp.run(person, client, mapped, call_args)
    if name in market_history.TOOL_NAMES:
        return market_history.run_tool(person, name, args)
    if name in tools_v2.IMPLS:
        text, err = tools_v2.run_tool(name, args)
        return text[:TOOL_TEXT_MAX], err
    if name in article_search.TOOL_NAMES:
        return article_search.run_tool(person, _publication(ctx), name, args)
    if name == calc.TOOL["name"]:
        return calc.run_tool(args)
    if name == "read_skill":
        pid = _publication(ctx)
        sk = skills.get(str(args.get("name") or ""), pid) if pid is not None else None
        if sk is None or not sk["enabled"]:
            return json.dumps({"error": "No skill called '%s'. The skills are: %s."
                               % (args.get("name"), _skill_names(pid) if pid is not None else "none")}), True
        return json.dumps({"name": sk["name"], "steps": sk["instructions"]}, ensure_ascii=False), False
    if name in ("drive_search", "drive_read"):
        if not staff_google.usable(person["id"]):
            return json.dumps({"error": "Google Drive is not connected for this writer."}), True
        try:
            if name == "drive_search":
                return json.dumps({"files": staff_google.search(person["id"], args.get("query"))},
                                  ensure_ascii=False), False
            return json.dumps(staff_google.read(person["id"], str(args.get("file") or "")),
                              ensure_ascii=False)[:TOOL_TEXT_MAX * 2], False
        except staff_google.GoogleError as exc:
            return json.dumps({"error": str(exc)}), True
    if name == market_data.TOOL_NAME:
        keys = (ctx.get("creds") or {}).get("data_source_keys")
        text, err = market_data.run_tool(_data_owners(person, _publication(ctx)), args, keys=keys)
        return text[:TOOL_TEXT_MAX], err
    pid = _publication(ctx)
    for t in (connectors.run_tools(pid) if pid is not None else []):
        if t["name"] == name:
            keys = (ctx.get("creds") or {}).get("connector_keys")
            text, err = connectors.call(t["connector_id"], t["remote"], args,
                                        key=None if keys is None else keys.get(str(t["connector_id"])),
                                        key_given=keys is not None, publication_id=pid)
            return text[:TOOL_TEXT_MAX], err
    return json.dumps({"error": "Unknown tool '%s'." % name}), True


def clean_offer(args, publication_id=research.HOME):
    """A next step as the writer will see it, or AIError worded for the model.
    Its skill must be one of the run's publication's."""
    label = " ".join(str(args.get("label") or "").split())
    instruction = str(args.get("instruction") or "").strip()
    if not label or not instruction:
        raise AIError("A next step needs a label and an instruction.")
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    out = {"label": label, "instruction": instruction[:4000], "skill_id": None, "skill": None}
    name = str(args.get("skill") or "").strip()
    if name:
        sk = skills.get(name, publication_id) if publication_id is not None else None
        if sk is None or not sk["enabled"]:
            raise AIError("No skill called '%s'. The skills are: %s. Leave skill empty to "
                          "use none." % (name, _skill_names(publication_id) if publication_id is not None
                                         else "none"))
        out["skill_id"], out["skill"] = sk["id"], sk["name"]
    return out


# ---------------------------------------------------------------------------
# jobs

def _system(article, person, skill=None, length=None):
    d = article["draft"]
    read_only = bool(skill) and not skill["writes"]
    pid = article["publication_id"]
    menu = skills.list_skills(enabled_only=True, publication_id=pid)
    extra = "House style for everything you write in the draft:\n" + skills.house_style(pid) + "\n\n"
    extra += writing_style.prompt(person)
    extra += ("Arithmetic: work out every number you put in the draft that no tool returned as "
              "is (a change, a growth rate, a ratio, a share, a difference) with calculate, never "
              "in your head. Earlier articles: search_past_articles finds what this publication "
              "or the writer published before; link to one by its address when it helps the "
              "reader, and do not contradict it without saying what changed.\n\n")
    extra += pub_coverage.prompt_line(pid)
    ln = _length({"length": length})
    extra += ("Length: the writer chose %s — %s. This overrides any length a skill gives, except "
              "a fixed format such as Data Flash or Headlines. Shorter is better: no preamble, "
              "no repetition.\n\n" % (ln["label"], ln["rule"]))
    if menu:
        extra += ("The publication's skills (open one with read_skill when the instruction matches it):\n"
                  + skills.menu_text(menu) + "\n\n")
    if skill:
        extra += ("The writer chose the skill \"%s\" for this run. Follow its steps:\n\n%s\n\n"
                  % (skill["name"], skill["instructions"]))
    lines = [c["label"] for c in connectors.list_connectors(enabled_only=True, publication_id=pid) if c["tools"]]
    if staff_google.usable(person["id"]):
        lines.insert(0, "the writer's Google Drive (drive_search, drive_read)")
    own = [market_data.PROVIDERS[p]["name"] for p in market_data.merged(_data_owners(person, pid))]
    if own:
        lines.insert(0, "market data from %s (%s)" % (", ".join(own), market_data.TOOL_NAME))
    if lines:
        extra += ("Also connected: %s. Use them when the instruction needs them; say in your "
                  "reply which you used.\n\n" % ", ".join(lines))
    if own:
        extra += ("Prices from %s come from the publication's or the writer's own account, under "
                  "their licence with the vendor, not from Plover. Wherever you put one in the draft, name the vendor "
                  "as its source beside it, and never paste a table of them into the draft; for a "
                  "chart, use Plover's own data.\n\n" % market_data.TOOL_NAME)
    head = ("You are the writing assistant inside the PloverResearch editor, working for %s on "
            "one draft: article %d, \"%s\". You cannot publish; the writer does.\n\n"
            % (person["name"], article["id"], d.get("title") or "Untitled"))
    if read_only:
        head += ("This run answers only: you cannot change the draft, and the writer may keep "
                 "editing it while you work. Call get_draft first. Your reply is what the writer "
                 "reads, in the panel beside the draft: follow the skill's format. Numbered and "
                 "bulleted lists, pipe tables and **bold** are shown as such.\n\n")
    else:
        head += ("The writer gives you an instruction; carry it out on this draft with the tools, "
                 "then reply to the writer in two to four plain sentences (unless the skill sets "
                 "another format): what you changed and anything they should check.\n\n"
                 "Call get_draft first. Change the draft only with replace_draft, append_to_draft and "
                 "set_details. Keep the writer's own text unless the instruction asks you to change "
                 "it.\n\n")
    head += ("offer_next_step puts a button under your reply that starts another run with the "
             "instruction (and skill) you give. Offer one when the skill says to, or when there is "
             "an obvious next step; never more than %d.\n\n" % NEXT_MAX)
    return head + extra + research_mcp.RULES


def _log(job, kind, text):
    job["steps"].append({"at": time.time(), "kind": kind, "text": text[:300]})


# The data tools as the writer sees them in the AI tab's list of steps.
_PLAIN_STEPS = {
    "list_datasets": "Listed Plover's datasets",
    "describe_dataset": "Looked up what a dataset holds",
    "get_company": "Read a company's figures",
    "get_series": "Read a data series",
    "screen": "Ranked companies on a measure",
    "list_cohorts": "Listed the peer groups",
    "compare_cohort": "Compared companies within a peer group",
    "get_overview": "Read the latest headline numbers",
    "get_breakdown": "Read a breakdown",
    "get_vintages": "Checked releases and revisions",
}


def _describe(name, args, failed=False):
    args = args or {}
    if failed and name in ("replace_draft", "append_to_draft", "set_details"):
        return "Draft not changed: sent back to the AI to fix (too long, or out of date)"
    if name == "get_draft":
        return "Read the draft"
    if name == "replace_draft":
        return "Rewrote the draft"
    if name == "append_to_draft":
        return "Added to the draft" + ((" under \"%s\"" % args["after_heading"])
                                       if args.get("after_heading") else "")
    if name == "set_details":
        return "Updated " + ", ".join(k for k in ("title", "dek", "summary") if args.get(k))
    if name == "check_article":
        return "Ran the publish checklist"
    if name == "read_page":
        return "Read %s" % (args.get("url") or "a page")
    if name == "list_sent_pages":
        return "Listed the pages you sent from your browser"
    if name == "search":
        return "Searched Plover for \"%s\"" % (args.get("query") or "")
    if name in _PLAIN_STEPS:
        return _PLAIN_STEPS[name]
    if name == "search_past_articles":
        q = args.get("query") or ""
        return ("Searched past articles for \"%s\"" % q) if q else "Listed recent articles"
    if name == "read_past_article":
        return "Read a past article"
    if name == "calculate":
        return "Calculated %s" % (args.get("expression") or "")
    if name == "read_skill":
        return "Opened the skill \"%s\"" % (args.get("name") or "")
    if name == "offer_next_step":
        return "Offered a next step: %s" % (args.get("label") or "")
    if name == "drive_search":
        return "Searched Google Drive for \"%s\"" % (args.get("query") or "")
    if name == "drive_read":
        return "Read a file from Google Drive"
    m = re.match(r"^x(\d+)_(.+)$", name)
    if m:
        c = connectors.get(int(m.group(1)))
        return "Used %s: %s" % (c["label"] if c else "a connector", m.group(2).replace("_", " "))
    return "Used %s" % name.replace("_", " ")


# Sent with every run that carries a picture: the picture shows what to
# rebuild; the numbers always come from the data.
PICTURE_RULES = """\
A picture is attached: a screenshot of a chart, usually from another publisher (FT, Nikkei,
a broker). Rebuild it from the public data behind it:
1. Say what it shows: each series, the agency that publishes the data (its source line,
   if the picture has one), the frequency, the units and the dates covered.
2. Find the same data in Plover (search, describe_dataset, get_series). If Plover has it,
   use a live Plover chart of those series and dates.
3. If Plover does not have it, open the publishing agency's own release (read_page, or
   your web search) and build a chart of numbers from outside Plover with the numbers as
   that agency publishes them, and a Source line naming the agency and the table.
4. Never take numbers from the picture itself, and never copy the other publisher's chart
   or wording. Title the chart with its message, in our house style.
5. If you cannot find the data, change nothing: reply with what the chart shows and where
   the data is published, so the writer can fetch it.
"""


def _image(ref):
    """A picture the writer uploaded to the desk, as {path, media_type}, from
    its "<sha256>.<ext>" name; AIError when there is no such upload."""
    m = re.match(r"^([0-9a-f]{64})\.(png|jpg|webp)$", ref or "")
    path = research.media_file(m.group(1), m.group(2)) if m else None
    if path is None:
        raise AIError("The picture could not be found; paste it again.")
    return {"path": str(path), "media_type": rd.IMAGE_EXTS[m.group(2)]}


def _image_data(image):
    import base64
    with open(image["path"], "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def start(person, article_id, instruction, skill_id=None, image=None):
    instruction = (instruction or "").strip()
    image = _image(image) if image else None
    article = research.get(article_id)
    if article is None or not writers.can(person, "edit", article):
        raise AIError("No such article.")
    skill = None
    if skill_id:
        # the article's publication's skill: another's is not there, whatever its id
        skill = skills.get(int(skill_id), article["publication_id"])
        if skill is None or not skill["enabled"]:
            raise AIError("That skill is no longer available.")
        instruction = instruction or "Follow the skill on this draft."
    if not instruction:
        raise AIError("Tell the AI what to do.")
    if len(instruction) > 8000:
        raise AIError("Keep the instruction under 8,000 characters.")
    if image:
        instruction += "\n\n" + PICTURE_RULES
    s = run_settings(person, article["publication_id"])
    provider = s["provider"]
    if s["ai_from"] != "publication" and (not provider or not _ready(provider, s)):
        raise AIError("Choose and connect a provider first.")
    writes = not skill or skill["writes"]
    with _jobs_lock:
        busy = _running.get(person["id"])
        if busy and _jobs.get(busy, {}).get("status") == "running":
            raise AIError("The AI is still working on your last instruction.")
        before = research.mark(article_id, staff.actor_label(person), "Before AI") if writes else None
        job = {"id": uuid.uuid4().hex[:16], "status": "running", "steps": [], "reply": "",
               "error": None, "article_id": article_id, "staff_id": person["id"],
               "provider": provider, "publication_id": article["publication_id"],
               "started": time.time(), "finished": None,
               "before_history_id": before, "revision_before": article["revision"],
               "skill": skill["name"] if skill else None, "writes": writes, "next": []}
        _jobs[job["id"]] = job
        _running[person["id"]] = job["id"]
    ctx = {"person": person, "client": CLIENT[provider], "article_id": article_id,
           "publication_id": article["publication_id"], "read_only": not writes, "offers": job["next"], "plan_words": _length(s)["plan_words"]}
    threading.Thread(target=_run, args=(job, ctx, s, instruction, article, skill, image),
                     daemon=True).start()
    return public(job)


def public(job):
    a = research.get(job["article_id"])
    return {"id": job["id"], "status": job["status"], "steps": job["steps"], "reply": job["reply"],
            "error": job["error"], "provider": job["provider"],
            "provider_label": PROVIDER_LABEL.get(job["provider"]), "skill": job.get("skill"),
            "before_history_id": job["before_history_id"],
            "revision_after": job.get("revision_after"),
            "writes": job.get("writes", True), "next": job.get("next") or [],
            # an answer-only run changes nothing; the writer's own saves are theirs
            "changed": job.get("writes", True) and bool(a) and a["revision"] != job["revision_before"],
            "revision": a["revision"] if a else None,
            "seconds": round((job["finished"] or time.time()) - job["started"])}


def get_job(person, job_id):
    job = _jobs.get(job_id)
    if job is None or job["staff_id"] != person["id"]:
        return None
    return public(job)


def _run(job, ctx, s, instruction, article, skill=None, image=None):
    try:
        provider = s["provider"]
        system = _system(article, ctx["person"], skill, s.get("length"))
        if provider in ("openai", "anthropic"):
            reply = _run_api(job, ctx, provider, _open(s["key_ct"]),
                             s["model"] or DEFAULT_MODEL[provider], system, instruction, image)
        elif provider == "codex":
            reply = _run_codex(job, ctx, s, system, instruction, image)
        else:
            reply = _run_claude(job, ctx, s, system, instruction, image)
        job["reply"] = (reply or "").strip() or "Done."
        job["status"] = "done"
    except (AIError, gateway.GatewayError) as exc:
        job["error"] = str(exc)
        job["status"] = "failed"
    except Exception as exc:  # noqa: BLE001 — a failed run must say why, not hang
        job["error"] = "The AI run stopped: %s" % exc
        job["status"] = "failed"
    finally:
        a = research.get(job["article_id"])
        job["revision_after"] = a["revision"] if a else None
        job["finished"] = time.time()


def _run_api(job, ctx, provider, key, model, system, instruction, image=None):
    if not key:
        raise AIError("The stored key could not be read; paste it again.")
    tools = tool_list(ctx)
    user = {"role": "user", "content": instruction}
    if image:
        user["images"] = [{"media_type": image["media_type"], "data": _image_data(image)}]
    messages = [{"role": "system", "content": system}, user]
    for _ in range(MAX_STEPS):
        out = gateway.complete(provider, key, model, messages, tools, max_tokens=8000)
        messages.append({"role": "assistant", "content": out["text"],
                         "tool_calls": out["tool_calls"]})
        if not out["tool_calls"]:
            return out["text"]
        for tc in out["tool_calls"]:
            text, err = call_tool(ctx, tc["name"], tc.get("args") or {})
            _log(job, "error" if err else "tool", _describe(tc["name"], tc.get("args"), err))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["name"],
                             "content": text})
    return "I stopped after %d steps; the draft has what I finished." % MAX_STEPS


# ---- the CLIs: one stdio tool server, the same tools ----------------------

def _spec_file(job, ctx, work):
    path = os.path.join(work, "spec.json")
    log = os.path.join(work, "calls.jsonl")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"staff_id": ctx["person"]["id"], "article_id": ctx["article_id"],
                   "client": ctx["client"], "log": log, "read_only": bool(ctx.get("read_only")),
                   "plan_words": ctx.get("plan_words"),
                   "creds": _run_creds(ctx["person"], _publication(ctx))}, f)
    return path, log


def _run_creds(person, publication_id):
    """What the CLI's tool server needs to reach Drive and the run's
    publication's connectors, without the keychain's master secret: an
    hour-long Google access token and each of those connectors' keys, in a
    0600 file deleted with the run. No other publication's key is in it."""
    out = {"google_token": None, "connector_keys": {},
           # the writer's own market-data keys, opened here for the same reason
           "data_source_keys": market_data.keys_for_run(_data_owners(person, publication_id))}
    if staff_google.usable(person["id"]):
        try:
            out["google_token"] = staff_google._token(person["id"])
        except staff_google.GoogleError:
            pass
    for c in (connectors.list_connectors(enabled_only=True, with_secret=True, publication_id=publication_id)
              if publication_id is not None else []):
        if c.get("_secret_ct"):
            try:
                out["connector_keys"][str(c["id"])] = connectors._secret(c)
            except connectors.ConnectorError:
                pass
    return out


def _child_env():
    env = dict((k, os.environ[k]) for k in CHILD_ENV if os.environ.get(k))
    env["PYTHONUNBUFFERED"] = "1"
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _follow(job, log_path, stop):
    """Copy the tool server's call log into the job's steps as it grows."""
    seen = 0
    while True:
        last = stop.is_set()
        try:
            with open(log_path) as f:
                lines = f.readlines()
        except OSError:
            lines = []
        for line in lines[seen:]:
            try:
                ev = json.loads(line)
                _log(job, "error" if ev.get("error") else "tool",
                     _describe(ev["name"], ev.get("args"), ev.get("error")))
                if ev["name"] == "offer_next_step" and not ev.get("error") and len(job["next"]) < NEXT_MAX:
                    job["next"].append(clean_offer(ev.get("args") or {}, job.get("publication_id", research.HOME)))
            except (ValueError, KeyError):         # AIError is a ValueError
                pass
        seen = len(lines)
        if last:
            return
        stop.wait(0.7)


def _prompt(system, instruction):
    return system + "\n\nInstruction from the writer:\n" + instruction


def _run_codex(job, ctx, s, system, instruction, image=None):
    if not codex_mod.available():
        raise AIError("Codex is not installed on this server.")
    auth = _open(s["codex_auth_ct"])
    if not auth:
        raise AIError("Codex is not signed in; connect it again in the AI tab.")
    home = tempfile.mkdtemp(prefix="codex-home-")
    work = tempfile.mkdtemp(prefix="codex-work-")
    stop = threading.Event()
    follower = None
    try:
        auth_path = os.path.join(home, "auth.json")
        fd = os.open(auth_path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(auth)
        spec, log = _spec_file(job, ctx, work)
        last = os.path.join(home, "last.txt")
        child = _child_env()
        child["PYTHONPATH"] = APP_ROOT
        env_table = "{" + ", ".join("%s = %s" % (k, json.dumps(v))
                                    for k, v in child.items()) + "}"
        cmd = [codex_mod.BIN, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
               "--ignore-user-config", "--ignore-rules", "-C", work, "-s", "read-only", "-o", last]
        for feat in codex_mod.DISABLE:
            cmd += ["--disable", feat]
        cmd += ["-c", 'web_search="live"', "-c", 'approval_policy="never"',
                "-c", "mcp_servers.plover.command=" + json.dumps(sys.executable),
                "-c", "mcp_servers.plover.args=" + json.dumps(
                    ["-m", "app.research_ai_mcp", "--spec", spec]),
                "-c", "mcp_servers.plover.cwd=" + json.dumps(APP_ROOT),
                "-c", "mcp_servers.plover.env=" + env_table,
                "-c", "mcp_servers.plover.startup_timeout_sec=30",
                "-c", "mcp_servers.plover.tool_timeout_sec=120"]
        if s["model"]:
            cmd += ["-m", s["model"]]
        if image:
            pic = os.path.join(work, "picture" + os.path.splitext(image["path"])[1])
            shutil.copyfile(image["path"], pic)
            cmd += ["-i", pic]
        cmd.append("-")
        follower = threading.Thread(target=_follow, args=(job, log, stop), daemon=True)
        follower.start()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=codex_mod._env(home), cwd=work)
        proc.stdin.write(_prompt(system, instruction))
        proc.stdin.close()
        errors = []
        deadline = time.time() + RUN_SECONDS
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            item = ev.get("item") or {}
            if ev.get("type") == "item.completed" and item.get("type") == "web_search":
                _log(job, "web", "Searched the web for \"%s\"" % (item.get("query") or ""))
            if ev.get("type") in ("turn.failed", "error"):
                errors.append(((ev.get("error") or {}).get("message") or ev.get("message") or "")[:300])
            if time.time() > deadline:
                proc.kill()
                errors.append("The run took longer than %d minutes." % (RUN_SECONDS // 60))
                break
        proc.wait()
        if os.path.exists(auth_path):
            with open(auth_path) as f:
                now = f.read()
            if now and now != auth:
                staff.set_ai(ctx["person"]["id"], codex_auth_ct=keychain.seal(now))
        text = open(last).read().strip() if os.path.exists(last) else ""
        if proc.returncode != 0 and not text:
            msg = next((e for e in errors if e), "") or "Codex stopped (exit %s)." % proc.returncode
            if re.search(r"model .*not supported", msg, re.I):
                msg += " Set a model your ChatGPT plan offers in the AI tab (for example gpt-5.6-sol)."
            elif re.search(r"401|unauthori[sz]ed|sign in|log in|login", msg, re.I):
                msg = "The ChatGPT sign-in has expired; connect Codex again. (" + msg + ")"
            raise AIError(msg)
        return text
    finally:
        stop.set()
        if follower is not None:
            follower.join(5)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def _claude_env(home, token):
    """Only what Claude Code needs: its token, an empty home and config
    directory of its own, and no updates, telemetry or error reports."""
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": home,
            "CLAUDE_CONFIG_DIR": os.path.join(home, ".claude"),
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
            "LANG": "C.UTF-8"}


def _run_claude(job, ctx, s, system, instruction, image=None):
    if shutil.which(CLAUDE_BIN) is None:
        raise AIError("Claude Code is not installed on this server.")
    token = _open(s["claude_token_ct"])
    if not token:
        raise AIError("The stored Claude Code token could not be read; paste it again.")
    home = tempfile.mkdtemp(prefix="claude-home-")
    work = tempfile.mkdtemp(prefix="claude-work-")
    stop = threading.Event()
    follower = None
    try:
        spec, log = _spec_file(job, ctx, work)
        env = _child_env()
        env["PYTHONPATH"] = APP_ROOT
        mcp = {"mcpServers": {"plover": {
            "type": "stdio", "command": sys.executable,
            "args": ["-m", "app.research_ai_mcp", "--spec", spec], "env": env}}}
        mcp_path = os.path.join(work, "mcp.json")
        with open(mcp_path, "w") as f:
            json.dump(mcp, f)
        # Built-in tools cut to web search and page fetch; everything else is
        # the plover server. dontAsk refuses anything not allowed here.
        cmd = [CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--setting-sources", "",
               "--mcp-config", mcp_path, "--strict-mcp-config",
               "--tools", "WebSearch,WebFetch",
               "--allowedTools", "mcp__plover__*,WebSearch,WebFetch",
               "--permission-mode", "dontAsk",
               "--append-system-prompt", system]
        if s["model"]:
            cmd += ["--model", s["model"]]
        if image:
            # a picture goes in as a message of its own, which needs stream-json input
            cmd += ["--input-format", "stream-json"]
        follower = threading.Thread(target=_follow, args=(job, log, stop), daemon=True)
        follower.start()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=_claude_env(home, token), cwd=work)
        if image:
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "Instruction from the writer:\n" + instruction},
                {"type": "image", "source": {"type": "base64", "media_type": image["media_type"],
                                             "data": _image_data(image)}}]}}) + "\n")
        else:
            proc.stdin.write("Instruction from the writer:\n" + instruction)
        proc.stdin.close()
        result, errors = None, []
        deadline = time.time() + RUN_SECONDS
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                if line.strip():
                    errors.append(line.strip()[:300])
                continue
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use" and b.get("name") == "WebSearch":
                        _log(job, "web", "Searched the web for \"%s\""
                             % ((b.get("input") or {}).get("query") or ""))
                    elif b.get("type") == "tool_use" and b.get("name") == "WebFetch":
                        _log(job, "web", "Read %s" % ((b.get("input") or {}).get("url") or "a page"))
            elif ev.get("type") == "result":
                result = ev
            if time.time() > deadline:
                proc.kill()
                errors.append("The run took longer than %d minutes." % (RUN_SECONDS // 60))
                break
        proc.wait()
        if result and not result.get("is_error"):
            return result.get("result") or ""
        msg = ((result or {}).get("result") or "") or next((e for e in reversed(errors) if e), "") \
            or "Claude Code stopped (exit %s)." % proc.returncode
        if re.search(r"401|invalid.*token|oauth|log ?in|authenticat", msg, re.I):
            msg = ("Claude Code did not accept the token; run `claude setup-token` again and "
                   "paste the new one. (" + msg[:200] + ")")
        raise AIError(msg[:500])
    finally:
        stop.set()
        if follower is not None:
            follower.join(5)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# endpoints, under the desk's own prefix

router = APIRouter(prefix="/admin/api/research", include_in_schema=False)


def _person(request):
    person = writers.require_writer(request)
    if person.get("shared") or not person.get("id"):
        raise HTTPException(403, "Sign in with your own account to use the AI tab: "
                                 "its keys and sign-ins are personal.")
    return person


def _audit(request, person, action, detail):
    from .admin_api import _client_ip, audit
    audit(action, detail, _client_ip(request), by=staff.actor_label(person))


class SettingsBody(BaseModel):
    provider: str
    model: Optional[str] = None
    key: Optional[str] = None
    claude_token: Optional[str] = None


class ForgetBody(BaseModel):
    what: str


class ModelsBody(BaseModel):
    provider: str
    key: Optional[str] = None


class LengthBody(BaseModel):
    length: str


class RunBody(BaseModel):
    instruction: str = ""
    skill_id: Optional[int] = None
    image: Optional[str] = None     # "<sha256>.<ext>" of a picture uploaded to the desk


def _setup_of(person, article):
    """The publication whose skills the AI tab shows: the open article's
    (when the writer may see it), else the writer's own."""
    if article is None:
        return None
    a = research.get(article)
    return a["publication_id"] if a and writers.can(person, "view", a) else None


@router.get("/ai")
def ai_get(request: Request, article: Optional[int] = None):
    person = _person(request)
    return settings_view(person, _setup_of(person, article))


@router.put("/ai")
def ai_put(body: SettingsBody, request: Request, article: Optional[int] = None):
    person = _person(request)
    try:
        save_settings(person, body.provider, body.model, body.key, body.claude_token)
    except AIError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "research_ai_settings", "provider %s%s" % (
        body.provider, " (new key)" if (body.key or body.claude_token) else ""))
    return settings_view(person, _setup_of(person, article))


@router.put("/ai/length")
def ai_length(body: LengthBody, request: Request, article: Optional[int] = None):
    person = _person(request)
    try:
        set_length(person, body.length)
    except AIError as exc:
        raise HTTPException(400, str(exc))
    return settings_view(person, _setup_of(person, article))


@router.post("/ai/forget")
def ai_forget(body: ForgetBody, request: Request, article: Optional[int] = None):
    person = _person(request)
    if body.what not in ("key", "claude", "codex"):
        raise HTTPException(400, "Nothing to forget by that name.")
    forget(person, body.what)
    _audit(request, person, "research_ai_forget", body.what)
    return settings_view(person, _setup_of(person, article))


@router.post("/ai/models")
def ai_models(body: ModelsBody, request: Request):
    """The models this writer can choose for a provider: Claude Code's own
    names, their ChatGPT plan's Codex catalog, or what their API key lists."""
    person = _person(request)
    try:
        return models(person, body.provider, body.key)
    except AIError as exc:
        raise HTTPException(400, str(exc))


@router.post("/ai/codex/login")
def ai_codex_login(request: Request):
    person = _person(request)
    try:
        return codex_login(person)
    except AIError as exc:
        raise HTTPException(400, str(exc))


@router.get("/ai/codex")
def ai_codex_status(request: Request):
    return codex_status(_person(request))


@router.post("/articles/{article_id}/ai")
def ai_run(article_id: int, body: RunBody, request: Request):
    person = _person(request)
    try:
        job = start(person, article_id, body.instruction, body.skill_id, body.image)
    except AIError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "research_ai_run", "article %d via %s" % (
        article_id, PROVIDER_LABEL.get(job["provider"])))
    return job


@router.get("/ai/jobs/{job_id}")
def ai_job(job_id: str, request: Request):
    job = get_job(_person(request), job_id)
    if job is None:
        raise HTTPException(404, "No such run")
    return job
