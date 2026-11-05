#!/usr/bin/env python3
"""The done gate: runs every time Claude tries to end a turn in this repo.

Claude may not finish a turn in which it changed code until:

  1. every file it changed still parses (Python, JS, inline page scripts, JSON);
  2. ci/guards.py passes, if anything under observatory/ changed;
  3. the tests that name a changed module pass (the full suite runs on push);
  4. the browser checks pass for any page it changed that has a search box,
     and the one-click-one-request check if lock.js or the page injection
     changed (ci/search_examples.py);
  5. if it changed the product (observatory/app or observatory/web), its
     reply says what it tried as a user, on a line starting "Tried as a user:".

A failure blocks the stop and hands the failure back to Claude. The one way
past a failing check is to tell the user, in the reply, that the failure is
someone else's (another session works in this repo too), with a line starting
"GATE: not mine" naming the file. That keeps the escape visible to the user.

A turn that changed no code costs nothing: the gate returns at once.
See CLAUDE.md "Checks that run every time".
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[2]
OBS = REPO / "observatory"
PY = OBS / ".venv" / "bin" / "python"
CODE = (".py", ".js", ".mjs", ".html", ".css", ".json", ".sh")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# A Bash command that writes files: sed -i, a redirect into a code file, tee,
# or a Python snippet that opens a file for writing.
BASH_WRITES = re.compile(r"sed -i|>>?\s*[\w./-]+\.(py|js|mjs|html|css|json|sh)\b|\btee\b|"
                         r"open\([^)]*,\s*['\"]w|write_text\(")
PATHS = re.compile(r"[\w./~-]+\.(?:py|js|mjs|html|css|json|sh)\b")
MAX_BLOCKS = 6

CHECKLIST = """Before you finish, use what you changed the way a reader would, then say so.
For each page or feature you touched, try:
  - the obvious input a reader would type first (for a search: CPI, GDP, Toyota, 7203);
  - the example the screen itself shows (placeholder, hint, suggestion buttons);
  - clicking a button twice, and a slow reply;
  - an empty result, and an error (bad input, signed out, server down);
  - the real page at 1440 and 390 if it changed how anything looks;
  - could a first-time user do it with no help? Undo, delete and close in plain
    sight; no silent wait; every failure says what to do next; nothing half-made
    left behind (CLAUDE.md: the app must be VERY EASY TO USE).
Then add one line to your reply, starting "Tried as a user:", listing what you
tried and what happened. Cut what does not apply; never claim what you did not do."""


def load_turn(transcript_path):
    """The transcript entries since the person's last message."""
    entries = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        return []
    start = 0
    for i, e in enumerate(entries):
        if _is_prompt(e):
            start = i
    return entries[start:]


def _is_prompt(e):
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    content = (e.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    else:
        return False
    text = text.strip()
    return bool(text) and not text.startswith("Stop hook feedback")


def touched_files(turn, cwd):
    found = set()
    for e in turn:
        if e.get("type") != "assistant":
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            if b.get("name") in EDIT_TOOLS:
                p = inp.get("file_path") or inp.get("notebook_path")
                if p:
                    found.add(p)
            elif b.get("name") == "Bash":
                cmd = inp.get("command") or ""
                if BASH_WRITES.search(cmd):
                    found.update(PATHS.findall(cmd))
    out = set()
    for raw in found:
        p = pathlib.Path(os.path.expanduser(raw))
        candidates = [p] if p.is_absolute() else [pathlib.Path(cwd) / p, OBS / p, REPO / p]
        for c in candidates:
            if c.exists() and c.suffix.lower() in CODE:
                try:
                    c.resolve().relative_to(REPO)
                except ValueError:
                    break  # outside this repo: not ours to check
                out.add(c.resolve())
                break
    return sorted(out)


def reply_text(turn, data):
    parts = [data.get("last_assistant_message") or ""]
    for e in turn:
        if e.get("type") == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
    return "\n".join(parts)


def _syntax(path):
    """The same per-file check the after-edit hook runs (check-syntax.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_syntax", str(HERE.parent / "check-syntax.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.problems_in(path)


def run(cmd, timeout=600):
    try:
        r = subprocess.run(cmd, cwd=str(OBS), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, "timed out after %ds: %s" % (timeout, " ".join(cmd))
    return r.returncode, (r.stdout + r.stderr)


# Modules nearly every test names (app.main starts the site, "api" and "db" are words in
# every URL and setUp), so a name match picks the whole suite. 2026-11-05: two lines in
# main.py ran 115 test files, and a date-dependent test plus another session's unfinished
# ones blocked the gate on work that had nothing to do with them. A change to one of these
# runs the tests that are about it instead.
HUB_TESTS = {
    "main": ("test_mcp_sync.py", "test_prerender.py"),
    "api": ("test_manifests.py", "test_adapter_contract.py", "test_observations_csv.py"),
    "db": ("test_ingest_probe.py", "test_published_at.py"),
    "cache": ("test_cache_limits.py", "test_cache_warm.py", "test_edit_reads.py"),
    "registry": ("test_mcp_sync.py", "test_manifests.py"),
}


def related_tests(files):
    """Test files that name a changed app module, plus the page tests for a page."""
    tests_dir = OBS / "tests"
    modules = set()
    pages = False
    picked = set()
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        # only app/<hub>.py itself: app/assistant/api.py is an ordinary module that happens to share a name
        if (rel.startswith("observatory/app/") and f.suffix == ".py" and f.stem in HUB_TESTS
                and f.parent == OBS / "app"):
            picked.update(tests_dir / n for n in HUB_TESTS[f.stem] if (tests_dir / n).exists())
        elif rel.startswith("observatory/app/") and f.suffix == ".py":
            modules.add(f.stem)
        if rel.startswith("observatory/tests/") and f.name.startswith("test_"):
            modules.add("__test__" + f.name)
        if rel.startswith("observatory/web/"):
            pages = True
    for t in sorted(tests_dir.glob("test_*.py")):
        if "__test__" + t.name in modules:
            picked.add(t)
            continue
        text = t.read_text(encoding="utf-8", errors="replace")
        for m in modules:
            if not m.startswith("__test__") and re.search(r"\b%s\b" % re.escape(m), text):
                picked.add(t)
                break
    if pages:
        picked.add(tests_dir / "test_prerender.py")
    return sorted(picked)


def browser_parts(files):
    """Which boxes of ci/search_boxes.json to run, from the files changed."""
    try:
        manifest = json.loads((OBS / "ci" / "search_boxes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    stems = set(f.stem for f in files if "observatory/web" in f.as_posix())
    parts = set()
    for box in manifest.get("boxes", []):
        page = box["key"].split("#")[0]
        if pathlib.Path(page).stem in stems:
            parts.add(box["key"])
    names = set(f.name for f in files)
    if names & {"lock.js", "prerender.py", "research_pages.py", "search_examples.mjs",
                "search_examples.py", "search_boxes.json"}:
        parts.add("lock")
    if names & {"search_examples.mjs", "search_examples.py", "search_boxes.json"}:
        parts.update(b["key"] for b in manifest.get("boxes", []))
    return sorted(parts)


def block(reason, session):
    counter = pathlib.Path(tempfile.gettempdir()) / ("plover-done-gate-%s" % session)
    try:
        n = int(counter.read_text()) + 1
    except (OSError, ValueError):
        n = 1
    if n > MAX_BLOCKS:
        # A gate that can never be satisfied must not trap the session forever;
        # the failure has been shown six times, and Claude was told to report it.
        counter.unlink() if counter.exists() else None
        sys.stderr.write("done gate: gave up after %d blocks\n" % MAX_BLOCKS)
        return 0
    counter.write_text(str(n))
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    session = re.sub(r"[^\w-]", "", data.get("session_id") or "x")
    turn = load_turn(data.get("transcript_path") or "")
    files = touched_files(turn, data.get("cwd") or os.getcwd())
    if not files:
        return 0
    reply = reply_text(turn, data)
    failures = []

    for f in files:
        failures.extend(_syntax(f))

    in_obs = [f for f in files if f.as_posix().startswith(OBS.as_posix() + "/")]
    if in_obs:
        code, out = run([str(PY), "ci/guards.py"], timeout=120)
        if code:
            failures.append("ci/guards.py failed:\n" + "\n".join(
                l for l in out.splitlines() if l.startswith(("FAIL", "    "))))
        tests = related_tests(in_obs)
        if tests:
            code, out = run([str(PY), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
                            + [str(t.relative_to(OBS)) for t in tests], timeout=600)
            if code:
                failures.append("tests failed (%s):\n%s" % (
                    ", ".join(t.name for t in tests), "\n".join(out.splitlines()[-25:])))
        parts = browser_parts(in_obs)
        if parts:
            code, out = run([str(PY), "ci/search_examples.py", ",".join(parts)], timeout=600)
            if code:
                failures.append("reader checks failed (ci/search_examples.py %s):\n%s"
                                % (",".join(parts), "\n".join(out.splitlines()[-15:])))

    product = [f for f in in_obs if "/observatory/app/" in f.as_posix()
               or "/observatory/web/" in f.as_posix()]
    tried = re.search(r"(?im)^\W*tried as a user\W*:", reply)

    if failures and not re.search(r"(?im)^\W*GATE: not mine", reply):
        reason = ("The done gate found problems in this turn's changes. Fix them, then finish. "
                  "If a failure comes from a file you did not change (another session works in "
                  "this repo), say so to the user and add a line starting \"GATE: not mine\" "
                  "naming the file.\n\n" + "\n\n".join(failures))
        if product and not tried:
            reason += "\n\n" + CHECKLIST
        return block(reason, session)
    if product and not tried:
        return block(CHECKLIST, session)
    counter = pathlib.Path(tempfile.gettempdir()) / ("plover-done-gate-%s" % session)
    if counter.exists():
        counter.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
