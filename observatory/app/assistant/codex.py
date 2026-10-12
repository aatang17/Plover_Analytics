"""Codex as a model provider: a desk answered on its owner's ChatGPT plan.

OpenAI's Codex CLI runs on this server, signed in to the desk owner's ChatGPT
account through Codex's own device-code sign-in (the person opens OpenAI's
page and types a one-time code; this server never sees a password). The
resulting auth.json is sealed with the keychain like every other key and is
unsealed into a throwaway directory only for the length of one run; if Codex
refreshed its tokens during the run, the new file is sealed back.

What Codex may do is decided here, not by the prompt:

- its shell, browser, computer-use, image, plugin, app and sub-agent tools are
  switched off, web search is off, and the sandbox is read-only;
- it runs in an empty directory with an environment holding nothing but
  PATH and its own CODEX_HOME, so no server secret is within reach;
- its only tools are served by codex_mcp.py: the specialist's allowlisted data
  tools and draw_chart, each call audited under this run exactly as the other
  providers' are, so a Codex answer shows the same lookups beneath it.

One run at a time per desk: Codex rotates its refresh token, and two runs
writing different auth files back would sign the desk out.
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
import base64

from . import keychain, store

PROVIDER = "codex"
LABEL = "ChatGPT plan (Codex)"
BIN = os.environ.get("CODEX_BIN") or "codex"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGIN_TIMEOUT = 15 * 60          # Codex's one-time code expires in fifteen minutes

# Built-in Codex features switched off for every run: everything that could
# act on this machine or reach outside it. All exist in the pinned 0.160.0,
# which also turns on a background helper, automations, worktrees and reading
# image files by default — off here too. The code-mode host stays ON: from
# 0.160 every ChatGPT-plan model but gpt-5.5 calls tools by writing a little
# JavaScript that runs in Codex's V8 sandbox, whose only built-ins are the
# tools offered here, timers and a scratch store (no files, processes,
# network or imports). Off, those models see no tools at all.
DISABLE = ("shell_tool", "unified_exec", "shell_snapshot", "browser_use", "browser_use_external",
           "browser_use_full_cdp_access", "computer_use", "in_app_browser", "image_generation",
           "apps", "plugins", "remote_plugin", "plugin_sharing", "multi_agent", "goals", "hooks",
           "skill_mcp_dependency_install", "tool_suggest", "multi_agent_v2",
           "workspace_dependencies", "daemon_auto_start", "in_app_local_automation", "worktrees",
           "view_image", "skill_search", "realtime_conversation", "chronicle", "in_app_chat",
           "in_app_dictation", "in_app_updates")
# Settings the tool server needs to find the data; none of them is a secret.
MCP_ENV = ("WORKSPACE_DB", "OBSERVATORY_DATA_DIR", "OBSERVATORY_DB_PATH", "EQUITY_DB_PATH",
           "SEC_DB_PATH", "MCP_TOOLSET", "INTERNAL_COHORTS", "ACCOUNTS_ENABLED",
           "ASSISTANT_ENABLED", "PATH")

RULES = (
    "Your only tools are the `plover` tools. There is no shell, no file system and no web "
    "access: do not try them. Answer in plain Markdown for the reader; do not describe "
    "your tool calls, they are shown to the reader separately.")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_logins = {}
_logins_lock = threading.Lock()
_run_locks = {}


class CodexError(Exception):
    pass


def available():
    return shutil.which(BIN) is not None


def _env(home):
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home, "CODEX_HOME": home, "LANG": "C.UTF-8"}


def _email(auth_text):
    """The signed-in address, from the id_token Codex stored. Display only."""
    try:
        tok = json.loads(auth_text)["tokens"]["id_token"]
        part = tok.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return claims.get("email")
    except Exception:                                        # noqa: BLE001
        return None


def _store_auth(account_id, auth_text):
    store.update_desk(account_id, codex_auth_ct=keychain.seal(auth_text),
                      codex_email=_email(auth_text))


# ------------------------------------------------------------------ sign-in

def start_login(account_id):
    """Start Codex's device-code sign-in; returns the link and the code the
    person enters on OpenAI's page. The process keeps waiting in the
    background and seals the result when the person finishes."""
    if not available():
        raise CodexError("Codex is not installed on this server.")
    if not keychain.enabled():
        raise CodexError("ASSISTANT_SECRET is not set, so a sign-in could not be stored.")
    cancel_login(account_id)
    home = tempfile.mkdtemp(prefix="codex-login-")
    proc = subprocess.Popen([BIN, "login", "--device-auth"], env=_env(home), cwd=home,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    state = {"proc": proc, "home": home, "url": None, "code": None, "error": None,
             "done": False, "started": time.time()}
    with _logins_lock:
        _logins[account_id] = state
    ready = threading.Event()

    def watch():
        lines = []
        want_code = False
        for raw in proc.stdout:
            line = _ANSI.sub("", raw).strip()
            lines.append(line)
            if not state["url"]:
                m = re.search(r"https://\S+", line)
                if m:
                    state["url"] = m.group(0)
            if "one-time code" in line.lower():
                want_code = True
                continue
            if want_code and line and not state["code"]:
                state["code"] = line.split()[0]
            if state["url"] and state["code"]:
                ready.set()
        proc.wait()
        auth = os.path.join(home, "auth.json")
        if proc.returncode == 0 and os.path.exists(auth):
            with open(auth) as f:
                _store_auth(account_id, f.read())
        elif not state.get("cancelled"):
            state["error"] = "Sign-in did not finish: " + (" ".join(lines[-3:]) or "no answer")[:300]
        state["done"] = True
        shutil.rmtree(home, ignore_errors=True)
        ready.set()

    threading.Thread(target=watch, daemon=True).start()
    ready.wait(30)
    if not (state["url"] and state["code"]):
        cancel_login(account_id)
        raise CodexError(state["error"] or "Codex did not return a sign-in code. Try again.")
    return {"url": state["url"], "code": state["code"], "expires_in_minutes": LOGIN_TIMEOUT // 60}


def cancel_login(account_id):
    with _logins_lock:
        state = _logins.pop(account_id, None)
    if state and not state["done"]:
        state["cancelled"] = True
        try:
            state["proc"].kill()
        except Exception:                                    # noqa: BLE001
            pass


def status(account_id):
    d = store.desk(account_id)
    out = {"available": available(), "connected": d["codex_connected"],
           "email": d["codex_email"], "pending": None, "error": None}
    state = _logins.get(account_id)
    if state:
        if not state["done"] and time.time() - state["started"] > LOGIN_TIMEOUT:
            cancel_login(account_id)
            out["error"] = "The code expired. Start again."
        elif not state["done"]:
            out["pending"] = {"url": state["url"], "code": state["code"]}
        elif state["error"]:
            out["error"] = state["error"]
    return out


def logout(account_id):
    cancel_login(account_id)
    store.update_desk(account_id, codex_auth_ct=None, codex_email=None)
    if store.desk(account_id)["model_provider"] == PROVIDER:
        store.update_desk(account_id, model_provider=None)


# ---------------------------------------------------------------------- runs

def _toml(v):
    """A -c value: JSON strings and arrays are valid TOML."""
    return json.dumps(v, ensure_ascii=False)


def _prompt(messages):
    parts = []
    for m in messages:
        if m["role"] == "system":
            parts.append(m["content"] + "\n\n" + RULES)
        elif m["role"] == "user":
            parts.append("Reader: " + m["content"])
        elif m["role"] == "assistant" and m.get("content"):
            parts.append("You, earlier: " + m["content"])
    return "\n\n".join(parts)


def command(run, model, home, work, last_file):
    env_table = "{" + ", ".join("%s = %s" % (k, _toml(os.environ[k]))
                                for k in MCP_ENV if os.environ.get(k)) + \
        ", PYTHONUNBUFFERED = \"1\"}"
    cmd = [BIN, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
           "--ignore-rules", "-C", work, "-s", "read-only", "-o", last_file]
    for f in DISABLE:
        cmd += ["--disable", f]
    cmd += ["-c", 'web_search="disabled"', "-c", 'approval_policy="never"',
            "-c", "mcp_servers.plover.command=" + _toml(sys.executable),
            "-c", "mcp_servers.plover.args=" + _toml(
                ["-m", "app.assistant.codex_mcp", "--account", str(run.account_id),
                 "--hire", str(run.hire["id"]), "--run", str(run.run_id)]
                + (["--memory"] if getattr(run, "memory", False) else [])),
            "-c", "mcp_servers.plover.cwd=" + _toml(APP_ROOT),
            "-c", "mcp_servers.plover.env=" + env_table,
            "-c", "mcp_servers.plover.startup_timeout_sec=30",
            "-c", "mcp_servers.plover.tool_timeout_sec=90"]
    if model:
        cmd += ["-m", model]
    cmd.append("-")
    return cmd


def run(run, messages, model, seconds):
    """Drive one answer through Codex. Returns (text, outcome, error) and fills
    run.usage; the tool calls were recorded by the tool server as they ran."""
    aid = run.account_id
    lock = _run_locks.setdefault(aid, threading.Lock())
    if not available():
        return "", "failed", "Codex is not installed on this server."
    with lock:
        ct = (store.desk_secrets(aid) or {})["codex_auth_ct"]
        if not ct:
            return "", "failed", "ChatGPT is not connected for this desk. Open Settings."
        try:
            auth = keychain.open_(ct)
        except (ValueError, RuntimeError):
            return "", "failed", "The stored ChatGPT sign-in could not be read; connect again."
        home = tempfile.mkdtemp(prefix="codex-home-")
        work = tempfile.mkdtemp(prefix="codex-work-")
        last_file = os.path.join(home, "last.txt")
        try:
            path = os.path.join(home, "auth.json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(auth)
            try:
                p = subprocess.run(command(run, model, home, work, last_file),
                                   input=_prompt(messages), capture_output=True, text=True,
                                   env=_env(home), cwd=work, timeout=seconds)
                out, code, timed_out = p.stdout, p.returncode, False
            except subprocess.TimeoutExpired as exc:
                out, code, timed_out = (exc.stdout or b"").decode("utf-8", "replace") \
                    if isinstance(exc.stdout, bytes) else (exc.stdout or ""), -1, True
            errors = []
            for line in (out or "").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "turn.completed":
                    u = ev.get("usage") or {}
                    run.usage["in"] += int(u.get("input_tokens") or 0)
                    run.usage["out"] += int(u.get("output_tokens") or 0)
                elif t == "turn.failed":
                    errors.append(((ev.get("error") or {}).get("message") or "")[:300])
                elif t == "error":
                    errors.append((ev.get("message") or "")[:300])
            text = ""
            if os.path.exists(last_file):
                with open(last_file) as f:
                    text = f.read().strip()
            # Codex may have refreshed its tokens; keep the newest sign-in
            if os.path.exists(path):
                with open(path) as f:
                    now = f.read()
                if now and now != auth:
                    _store_auth(aid, now)
            if timed_out:
                return text, "budget", "The run hit its time budget before finishing."
            if code != 0 or (errors and not text):
                msg = next((e for e in errors if e), "") or "Codex stopped (exit %s)." % code
                if re.search(r"401|unauthori[sz]ed|sign in|log in|login", msg, re.I):
                    msg = "ChatGPT sign-in has expired; connect again in Settings. (" + msg + ")"
                return text, "failed", msg
            return text, "done", None
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)
