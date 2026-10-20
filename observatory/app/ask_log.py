# -*- coding: utf-8 -*-
"""What people ask the front page's box, for the Ask Box section of the
admin Traffic page.

Two records that never meet:

  * the visit log (app/visits.py) already notes every request with its
    anonymous daily visitor, country and network, but only the address, not
    the question. From it come searches and people per day.
  * this log notes every question as typed, with when, whether it was
    answered, how, and how long it took, and nothing about who asked: no
    visitor, no address, no country, no network. A question cannot be traced
    back to a reader, and a reader cannot be listed with their questions.

A repeated question is served from the response cache (app/cache.py) and
never reaches the routers, so the line is written by the visit counter,
which sees every request. The routers leave a note of how they answered
(note()), and the counter picks it up when it writes the line (record());
a cached repeat finds the note its first asking left.

Append-only JSONL under data/analytics/, one file a month, no DuckDB write
path (CLAUDE.md, Ingest Guardrails 5); a failure to write never reaches a
response. Robots are left out, as in the visit counts.
"""
import datetime
import json
import os
import threading
import urllib.parse
from collections import OrderedDict

from . import db

ANALYTICS_DIR = db.DATA_DIR / "analytics"
QUESTION_CHARS = 300

# the box's three doors: the full answer, its streamed form, and the signed-out preview
PATHS = {"/api/v1/us/ask": "full", "/api/v1/us/ask/stream": "full", "/api/v1/assistant/preview": "preview"}

_LOCK = threading.Lock()
_NOTES = OrderedDict()
NOTES_MAX = 5000
MAX_LINES = 200000


def _norm(q):
    return " ".join((q or "").split())[:QUESTION_CHARS]


def note(path, question, answered, route=None, shape=None):
    """How a router answered a question (answered: True/False; route: which
    part of the box answered; shape: the question shape), for record()."""
    key = (path, _norm(question).lower())
    with _LOCK:
        _NOTES[key] = {"answered": bool(answered), "route": route, "shape": shape}
        _NOTES.move_to_end(key)
        while len(_NOTES) > NOTES_MAX:
            _NOTES.popitem(last=False)


def _question(scope):
    raw = scope.get("query_string") or b""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    values = urllib.parse.parse_qs(raw).get("q") or []
    return _norm(values[0]) if values else ""


def _path_of(month):
    return ANALYTICS_DIR / ("ask-%s.jsonl" % month)


def record(scope, status, seconds=None, now=None):
    """Write one line for a question the box was asked. Called by the visit
    counter for a request on one of PATHS that is not a robot's."""
    path = scope.get("path") or ""
    if path not in PATHS:
        return
    if (scope.get("client") or ("",))[0] == "testclient":
        return                                  # the test suite asking, not a reader
    q = _question(scope)
    if not q:
        return
    with _LOCK:
        got = _NOTES.get((path, q.lower()))
    if got:
        answered = got["answered"]
    elif path == "/api/v1/us/ask" and status:
        answered = status < 400                 # the full answer refuses with a 422
    else:
        answered = None                         # not known: shown as such, never guessed
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    line = {"at": now.isoformat(timespec="seconds") + "Z", "q": q, "where": PATHS[path],
            "answered": answered, "route": (got or {}).get("route"), "shape": (got or {}).get("shape"),
            "status": status, "seconds": round(seconds, 1) if isinstance(seconds, (int, float)) else None}
    try:
        os.makedirs(str(ANALYTICS_DIR), exist_ok=True)
        with _LOCK:
            with open(str(_path_of(now.strftime("%Y-%m"))), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("ASK LOG WRITE FAILED (%s)" % exc)


def _median(values):
    v = sorted(x for x in values if isinstance(x, (int, float)))
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def summary(days, start, today, hits=None, recent=50, top=15):
    """The Ask Box section: totals, one row per day, the latest questions and
    the ones asked most. `hits` comes from the visit log: [(at, visitor)] for
    each request to the box; only those from the first logged question on are
    counted, so people are never counted for searches this log never saw."""
    months = set()
    d = start
    while d <= today:
        months.add(d.strftime("%Y-%m"))
        d += datetime.timedelta(days=1)
    lines = []
    for month in sorted(months):
        p = _path_of(month)
        if not p.exists():
            continue
        try:
            with open(str(p), encoding="utf-8") as f:
                for raw in f:
                    if len(lines) >= MAX_LINES:
                        break
                    try:
                        e = json.loads(raw)
                    except ValueError:
                        continue
                    if str(e.get("at", ""))[:10] >= start.isoformat():
                        lines.append(e)
        except OSError:
            continue
    since = min((e["at"] for e in lines), default=None)
    people = {}
    for at, visitor in hits or []:
        if since and at >= since:
            people.setdefault(at[:10], set()).add(visitor)
    people_by_day = {k: len(v) for k, v in people.items()}
    daily = {}
    for e in lines:
        row = daily.setdefault(e["at"][:10], {"searches": 0, "answered": 0, "not_answered": 0})
        row["searches"] += 1
        if e.get("answered") is True:
            row["answered"] += 1
        elif e.get("answered") is False:
            row["not_answered"] += 1
    series = []
    d = start
    while d <= today:
        k = d.isoformat()
        row = daily.get(k, {"searches": 0, "answered": 0, "not_answered": 0})
        series.append(dict(row, day=k, people=people_by_day.get(k, 0)))
        d += datetime.timedelta(days=1)
    asked = {}
    for e in lines:
        key = e["q"].lower()
        row = asked.setdefault(key, {"q": e["q"], "times": 0, "answered": 0, "last": ""})
        row["times"] += 1
        row["answered"] += 1 if e.get("answered") is True else 0
        if e["at"] > row["last"]:
            row["last"], row["q"] = e["at"], e["q"]
    most = sorted((r for r in asked.values() if r["times"] > 1), key=lambda r: (-r["times"], r["q"]))[:top]
    known = [e for e in lines if e.get("answered") in (True, False)]
    return {
        "since": since,
        "searches": len(lines),
        "people": sum(people_by_day.get(r["day"], 0) for r in series),
        "answered": sum(1 for e in known if e["answered"]),
        "judged": len(known),
        "previews": sum(1 for e in lines if e.get("where") == "preview"),
        "median_seconds": _median([e.get("seconds") for e in lines]),
        "distinct_questions": len(asked),
        "daily": series,
        "recent": [{k: e.get(k) for k in ("at", "q", "where", "answered", "route", "shape", "seconds")}
                   for e in sorted(lines, key=lambda e: e["at"], reverse=True)[:recent]],
        "most_asked": most,
    }
