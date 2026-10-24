# -*- coding: utf-8 -*-
"""The static P0 checks — the rules in CLAUDE.md, made mechanical.

Every check here exists because something already went wrong, or because the
rule it enforces is one whose breach is invisible until a customer finds it.
Each one was verified against the code as it stood on 2026-09-16 before it
became a gate: a guard that has never passed is a guard nobody trusts.

Run from observatory/:  ./.venv/bin/python ci/guards.py
Exit code 0 if every check passes, 1 with a list of what failed.

See docs/plans/PLAN-CI-CD.md §3.
"""
from __future__ import print_function

import os
import pathlib
import re
import sys
import warnings

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKS = []


def check(title):
    def wrap(fn):
        CHECKS.append((title, fn))
        return fn
    return wrap


# ---------------------------------------------------------------------------
# what ships is what was tested
# ---------------------------------------------------------------------------

# The web stack that diverged. On 2026-09-05 `fastapi>=0.110,<1` resolved to a
# version in the image that the laptop had never run, and every one of the
# nineteen dataset manifests was rejected in production while the suite passed
# locally. These four move together or not at all.
EXACT_PINS = ("fastapi", "starlette", "pydantic", "uvicorn")

# INSERT OR REPLACE semantics around foreign keys changed after 1.5, and an
# unpinned install made production diverge from local.
DUCKDB_PIN = "duckdb>=1.4,<1.6"


def _requirements():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]


@check("the web stack is pinned exactly, and the DuckDB pin is intact")
def pins_are_exact():
    lines = _requirements()
    problems = []
    for name in EXACT_PINS:
        found = [l for l in lines if re.match(r"^%s(\[[^\]]*\])?==" % name, l)]
        if not found:
            problems.append("requirements.txt: %s is not pinned with == " % name
                            + "(found: %s)" % (
                                [l for l in lines if l.startswith(name)] or "nothing"))
    if DUCKDB_PIN not in [l.replace(" ", "") for l in lines]:
        problems.append("requirements.txt: the DuckDB pin is no longer %s — "
                        "don't loosen it" % DUCKDB_PIN)
    return problems


@check("the installed versions are the pinned versions")
def installed_matches_pins():
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover — 3.7 and below
        return []
    problems = []
    for line in _requirements():
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;]+)", line)
        if not m:
            continue
        name, want = m.group(1), m.group(3)
        try:
            have = metadata.version(name)
        except Exception:  # noqa: BLE001
            problems.append("%s is pinned to %s but is not installed" % (name, want))
            continue
        if have != want:
            problems.append("%s is pinned to %s but %s is installed — this is the "
                            "2026-09-05 defect" % (name, want, have))
    return problems


# ---------------------------------------------------------------------------
# the container must always come up serving something
# ---------------------------------------------------------------------------

# Ingest Guardrail: an unreachable source or a failed validation publishes
# nothing and the last good data stays live. A `set -e` here, or one ingest
# call without a fallback, turns a bad morning at e-Stat into a site outage.
SUPERVISED = ("-m app.ingest", "-m app.vintages", "-m app.refresh",
              "equity/refresh_equity.py", "equity/sec_extract.py")


def _logical_lines(text):
    """Shell lines with comments dropped and continuations joined."""
    out, buf = [], ""
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((buf + line).strip())
        buf = ""
    if buf:
        out.append(buf.strip())
    return out


@check("start.sh cannot let an ingest failure take the site down")
def start_sh_is_fail_safe():
    text = (ROOT / "start.sh").read_text(encoding="utf-8")
    problems = []
    for line in _logical_lines(text):
        if re.match(r"^set\s+-[a-z]*e", line):
            problems.append("start.sh: `%s` makes a failed ingest fatal to boot"
                            % line)
        for pattern in SUPERVISED:
            if pattern in line and "||" not in line and not line.endswith("&"):
                problems.append("start.sh: `%s` has no `|| echo` fallback — a "
                                "failure here would stop the boot" % line[:70])
    return problems


# ---------------------------------------------------------------------------
# one writer, and it is never the server
# ---------------------------------------------------------------------------

def _call_args(text, start):
    """The text of a call's arguments, parens balanced."""
    depth, i = 0, start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


@check("no API module opens a database for writing")
def serving_opens_read_only():
    problems = []
    for path in sorted(ROOT.glob("app/*_api.py")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"duckdb\.connect\s*\(", text):
            args = _call_args(text, m.end() - 1)
            if "read_only=True" not in args.replace(" ", ""):
                line = text[:m.start()].count("\n") + 1
                problems.append("%s:%d: duckdb.connect() without "
                                "read_only=True — the serving process must "
                                "never hold a write handle" % (path.name, line))
    return problems


# ---------------------------------------------------------------------------
# the trust contract
# ---------------------------------------------------------------------------

@check("only official statistics and model estimates carry a badge")
def trust_labels_are_two():
    path = ROOT / "web" / "assets" / "format.js"
    text = path.read_text(encoding="utf-8")
    m = re.search(r"const TRUST_LABELS\s*=\s*\{([^}]*)\}", text)
    if not m:
        return ["format.js: TRUST_LABELS is gone — the trust contract is "
                "rendered from it"]
    keys = set(re.findall(r"(\w+)\s*:", m.group(1)))
    extra = sorted(keys - {"official", "model"})
    if extra:
        return ["format.js: TRUST_LABELS has %s. A calculated rate carries its "
                "formula, never a badge." % ", ".join(extra)]
    return []


# ---------------------------------------------------------------------------
# no build step, no CDN, no new runtime dependency
# ---------------------------------------------------------------------------

@check("no page loads a script or stylesheet from the internet")
def no_external_assets():
    problems = []
    for path in sorted(ROOT.glob("web/*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"<(script[^>]*\bsrc|link[^>]*\bhref)\s*=\s*[\"']"
                             r"(https?:)?//([^\"']+)", text):
            problems.append("%s:%d: loads %s from the internet — vendor it "
                            "instead" % (path.name,
                                         text[:m.start()].count("\n") + 1,
                                         m.group(3)[:50]))
    for path in sorted(ROOT.glob("web/assets/*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for host in ("cdn.jsdelivr", "unpkg.com", "cdnjs.", "esm.sh"):
            if host in text:
                problems.append("%s: references %s" % (path.name, host))
    return problems


# ---------------------------------------------------------------------------
# one click, one request
# ---------------------------------------------------------------------------
#
# 2026-10-03: Create Key had no lock, the reply was slow, and ten clicks made
# ten live keys. web/assets/lock.js now switches off any control whose click
# sends a change until the server answers. It works only if every page loads
# it and every write goes through window.fetch, which it wraps.

def _front_end_scripts():
    """(name, text) for every script we wrote: asset files and inline blocks."""
    out = []
    for path in sorted(ROOT.glob("web/assets/*.js")):
        if path.name.startswith("echarts") or path.name == "lock.js":
            continue
        out.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    for path in sorted(ROOT.glob("web/*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"(?is)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text):
            out.append((path.name, m.group(1)))
    return out


@check("every page the server sends carries the one-click-one-request lock")
def pages_carry_the_lock():
    problems = []
    lock = ROOT / "web" / "assets" / "lock.js"
    if not lock.exists() or "window.fetch = function" not in lock.read_text(encoding="utf-8"):
        problems.append("web/assets/lock.js is missing or no longer wraps window.fetch")
    pre = (ROOT / "app" / "prerender.py").read_text(encoding="utf-8")
    body = pre[pre.find("def inject("):pre.find("def _inject(")]
    if "LOCK_SCRIPT" not in body:
        problems.append("app/prerender.py: inject() no longer adds LOCK_SCRIPT to every page")
    if "/assets/lock.js" not in (ROOT / "app" / "research_pages.py").read_text(encoding="utf-8"):
        problems.append("app/research_pages.py: the research pages no longer load lock.js")
    return problems


# Ways to send a request that lock.js cannot see.
_AROUND_THE_LOCK = [
    (re.compile(r"\bXMLHttpRequest\b"), "uses XMLHttpRequest — use fetch, which lock.js wraps"),
    (re.compile(r"\bfetch\s*\.\s*(bind|call|apply)\b"), "keeps its own copy of fetch"),
    (re.compile(r"(?<![=!<>])(=|:)\s*(window\.)?fetch\b(?!\s*\()"), "keeps its own copy of fetch"),
    (re.compile(r"\$\.(ajax|post)\b|\baxios\b"), "uses a request library — use fetch"),
]


@check("no front-end code sends a change around the lock")
def writes_go_through_the_lock():
    problems = []
    for name, text in _front_end_scripts():
        for pat, why in _AROUND_THE_LOCK:
            m = pat.search(text)
            if m:
                problems.append("%s:%d: %s" % (name, text[:m.start()].count("\n") + 1, why))
    return problems


# ---------------------------------------------------------------------------
# every search box is tried the way a reader uses it
# ---------------------------------------------------------------------------
#
# 2026-10-03: the desk's search box said "e.g. core CPI" and found nothing for
# it. ci/search_examples.py types every box's own examples into a real browser
# before each push; this makes sure no box is missing from its list.

_INPUT = re.compile(r"<input\b[^>]*>", re.I | re.S)


def _search_box_key(file_name, tag):
    for attr in ("id", "data-f", "class"):
        m = re.search(r"\b%s=\\?[\"']([^\"'\\ ]*)" % attr, tag)
        if m and m.group(1):
            return "%s#%s" % (file_name, m.group(1) if attr == "id" else "%s=%s" % (attr, m.group(1)))
    return "%s#?" % file_name


def search_box_keys():
    keys = set()
    for path in sorted(list(ROOT.glob("web/*.html")) + list(ROOT.glob("web/assets/*.js"))):
        if path.name.startswith("echarts"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _INPUT.finditer(text):
            tag = m.group(0)
            ph = re.search(r"placeholder=\\?[\"']([^\"'\\]*)", tag)
            ph = ph.group(1) if ph else ""
            if (re.search(r"type=\\?[\"']search", tag) or re.search(r"\be\.g\.", ph, re.I)
                    or re.match(r"\s*(search|filter|find)\b", ph, re.I)):
                keys.add(_search_box_key(path.name, tag))
    return keys


@check("every search box is listed in ci/search_boxes.json")
def search_boxes_listed():
    import json
    manifest = json.loads((ROOT / "ci" / "search_boxes.json").read_text(encoding="utf-8"))
    listed = set(b["key"] for b in manifest["boxes"])
    for section in ("elsewhere", "not_run", "not_searches"):
        listed.update(manifest.get(section, {}))
    problems = []
    for key in sorted(search_box_keys() - listed):
        problems.append("%s: a search box with no reader check. Add it to ci/search_boxes.json "
                        "'boxes' (url, input, area, hits), or to 'not_run' / 'not_searches' "
                        "with the reason" % key)
    for key in sorted(listed - search_box_keys()):
        problems.append("%s: listed in ci/search_boxes.json but no longer on the site" % key)
    return problems


# ---------------------------------------------------------------------------
# the laptop is 3.9 and the container is 3.12
# ---------------------------------------------------------------------------

@check("every module compiles on the running interpreter, without warnings")
def syntax_compiles():
    r"""Not just parses — compiles, with warnings promoted to errors.

    The two interpreters disagree about how loudly they complain: an invalid
    escape sequence like ``"\s"`` in a plain string is a DeprecationWarning on
    3.9 and a SyntaxWarning on 3.12, and a future version will make it an
    error. Four of these were sitting in equity/ until the first 3.12 run
    printed them. Divergence between the laptop and the container is the
    defect class this whole plan exists for, so it fails the build here.
    """
    problems = []
    for base in ("app", "equity", "ci", "tests"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            source = path.read_text(encoding="utf-8")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    compile(source, str(path), "exec")
            except SyntaxWarning as exc:
                problems.append("%s: %s" % (path.relative_to(ROOT), exc))
            except DeprecationWarning as exc:
                problems.append("%s: %s" % (path.relative_to(ROOT), exc))
            except SyntaxError as exc:
                problems.append("%s:%s: %s (Python %s)" % (
                    path.relative_to(ROOT), exc.lineno, exc.msg,
                    "%d.%d" % sys.version_info[:2]))
    return problems


# ---------------------------------------------------------------------------
# the golden rule: a dataset is an adapter plus two registry entries
# ---------------------------------------------------------------------------

@check("every dataset is registered in both registries and honours the "
       "adapter contract")
def adapters_are_registered():
    sys.path.insert(0, str(ROOT))
    from app import api, ingest
    problems = []
    only_ingest = sorted(set(ingest.ADAPTERS) - set(api.ADAPTERS))
    only_api = sorted(set(api.ADAPTERS) - set(ingest.ADAPTERS))
    if only_ingest:
        problems.append("registered in app/ingest.py but not app/api.py: %s — "
                        "it will ingest and never serve" % ", ".join(only_ingest))
    if only_api:
        problems.append("registered in app/api.py but not app/ingest.py: %s — "
                        "it will serve and never refresh" % ", ".join(only_api))
    contract = ("DATASET", "SOURCE", "DOWNLOAD_URL", "fetch", "parse",
                "validate", "PRESENTATION", "ValidationError")
    for slug in sorted(ingest.ADAPTERS):
        missing = [n for n in contract
                   if not hasattr(ingest.ADAPTERS[slug], n)]
        if missing:
            problems.append("adapter %s is missing %s" % (slug, ", ".join(missing)))
    return problems


# ---------------------------------------------------------------------------
# one subject per address says which subject it is
# ---------------------------------------------------------------------------

# The query parameters that pick a page's subject — a company, a holder, a
# ticker, a series — rather than how it is shown.
_SUBJECT_PARAMS = ("c", "h", "t", "code", "company", "series", "bank", "cohort",
                   "ticker", "holder", "cik")
_PARAM_READ = re.compile(r"\.get\(\s*[\"']([a-z_]+)[\"']\s*\)")
_PAGE_SCRIPT = re.compile(r"<script[^>]+src=[\"']/?assets/([^\"']+\.js)[\"']")


@check("every page that serves one company, holder or series per address "
       "gives each its own title")
def subject_pages_are_titled():
    """2026-10-23: every company's 5% filings page went out titled "5% Filings"
    with an empty heading, Google treated them as one page, and a search for
    one company landed on Cosmo Energy. Eight more pages had the same gap.

    A page whose script reads a subject parameter must be in
    app/prerender.py's ENTITY_PAGES (it gets a title naming its subject) or
    list that parameter in FILTER_PARAMS (it only picks something inside one
    page), with the reason beside it."""
    sys.path.insert(0, str(ROOT))
    from app import prerender
    problems = []
    for path in sorted(ROOT.glob("web/*.html")):
        page = path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        sources = [text]
        for script in _PAGE_SCRIPT.findall(text):
            js = ROOT / "web" / "assets" / script
            if js.exists() and not script.startswith("echarts"):  # vendored, not ours
                sources.append(js.read_text(encoding="utf-8", errors="replace"))
        read = set()
        for source in sources:
            read.update(p for p in _PARAM_READ.findall(source) if p in _SUBJECT_PARAMS)
        if page in prerender.ENTITY_PAGES:
            continue
        loose = sorted(read - prerender.FILTER_PARAMS.get(page, set()))
        if loose:
            problems.append(
                "web/%s reads ?%s= but gives each value no title of its own. Add a view "
                "to ENTITY_PAGES in app/prerender.py, or list the parameter in "
                "FILTER_PARAMS there if it only picks something inside the page"
                % (page, "=, ?".join(loose)))
    return problems


# ---------------------------------------------------------------------------

def main():
    failed = []
    for title, fn in CHECKS:
        try:
            problems = fn()
        except Exception as exc:  # noqa: BLE001 — a guard that crashes is a fail
            problems = ["the check itself raised %s: %s"
                        % (type(exc).__name__, exc)]
        if problems:
            failed.append((title, problems))
            print("FAIL  %s" % title)
            for p in problems:
                print("        %s" % p)
        else:
            print("ok    %s" % title)
    print("")
    if failed:
        print("%d of %d guards failed. These are P0 rules — fix the code, not "
              "the guard." % (len(failed), len(CHECKS)))
        return 1
    print("%d guards passed." % len(CHECKS))
    return 0


if __name__ == "__main__":
    os.chdir(str(ROOT))
    sys.exit(main())
