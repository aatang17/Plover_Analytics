#!/usr/bin/env python3
"""Adapt Codex hook events to the repository's existing Claude checks.

Keep turn-local edit paths in a private temporary directory, avoiding dependence
on either agent's transcript format. Also provides explicit syntax/done commands.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
# The Mac's shared Python cache may be outside the workspace sandbox.
os.environ["PYTHONPYCACHEPREFIX"] = str(
    Path(tempfile.gettempdir()) / "plover-codex-pycache")
REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "claude_done_gate", str(REPO / ".claude/hooks/done-gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def edits(paths):
    return [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": str(p)}}
        for p in paths]}}]


def changed(data):
    inp = data.get("tool_input") or {}
    if isinstance(inp, str):
        inp = {"command": inp}
    cwd = inp.get("workdir") or inp.get("cwd") or data.get("cwd") or str(REPO)
    name = data.get("tool_name", "")
    if name in ("apply_patch", "Edit", "Write"):
        command = inp.get("command") or inp.get("patch") or ""
        paths = re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
                           command, re.M)
        if inp.get("file_path"):
            paths.append(inp["file_path"])
        return gate.touched_files(edits(paths), cwd)
    if name in ("Bash", "exec_command"):
        turn = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {
                "command": inp.get("command") or inp.get("cmd") or ""}}]}}]
        return gate.touched_files(turn, cwd)
    return []


def syntax(paths):
    failures = [problem for path in paths for problem in gate._syntax(Path(path))]
    if failures:
        sys.stderr.write("The file you just saved no longer parses. Fix it before "
                         "anything else:\n" + "\n".join(failures) + "\n")
        return 2
    return 0


def done(paths, reply, session):
    # Translate the recorded edit events, not the unstable Codex transcript.
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8") as f:
        for entry in edits(paths):
            f.write(json.dumps(entry) + "\n")
        f.flush()
        payload = {"session_id": session, "cwd": str(REPO),
                   "transcript_path": f.name, "last_assistant_message": reply}
        original = sys.stdin
        output = io.StringIO()
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            with contextlib.redirect_stdout(output):
                result = gate.main()
        finally:
            sys.stdin = original
        feedback = output.getvalue().strip()
        # Stop expects JSON; the shared gate is silent when all checks pass.
        print(feedback or "{}")
        return 2 if feedback else result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("post", "stop", "syntax", "done"))
    parser.add_argument("--reply-file", type=Path)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_intermixed_args()
    if args.mode in ("syntax", "done"):
        if not args.paths:
            parser.error("provide this turn's changed paths")
        paths = []
        for raw in args.paths:
            path = (REPO / raw).resolve()
            path.relative_to(REPO)
            if not path.is_file():
                parser.error("file does not exist: " + raw)
            paths.append(path)
        if args.mode == "syntax":
            return syntax(paths)
        reply = args.reply_file.read_text() if args.reply_file else ""
        return done(paths, reply, "codex-manual-" + str(os.getpid()))

    data = json.load(sys.stdin)
    identity = str(REPO) + ":" + data["session_id"] + ":" + data["turn_id"]
    key = hashlib.sha256(identity.encode()).hexdigest()
    state_dir = Path(tempfile.gettempdir()) / "plover-codex-hooks"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state = state_dir / (key + ".json")
    # Hook processes for parallel tool calls can overlap.
    import fcntl
    with (state_dir / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        paths = json.loads(state.read_text()) if state.exists() else []
        if args.mode == "post":
            current = changed(data)
            paths = sorted(set(paths) | {str(p) for p in current})
            state.write_text(json.dumps(paths))
            return syntax(current)
        done(paths, data.get("last_assistant_message") or "", "codex-" + key)
        # The shared gate's JSON decision drives Stop continuation.
        return 0


if __name__ == "__main__":
    sys.exit(main())
