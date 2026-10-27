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

Each line says who it came from in one word, never more ("from"): "us" for
our own checks (an address in OUR_ADDRESSES, or a private one, which no
reader behind the proxy ever has; the address itself is not kept), "example"
for a click on one of the page's own example questions (the page adds
via=example), and "reader" for a question somebody typed. The admin page
counts the last, week by week: the one number that says whether strangers use
the box. Lines written before 2026-10-21 carry no "from" and are shown as
not marked.

Append-only JSONL under data/analytics/, one file a month, no DuckDB write
path (CLAUDE.md, Ingest Guardrails 5); a failure to write never reaches a
response. Robots are left out, as in the visit counts.
"""
import datetime
import ipaddress
import json
import os
import threading
import time
import urllib.parse
from collections import OrderedDict

from . import db

ANALYTICS_DIR = db.DATA_DIR / "analytics"
QUESTION_CHARS = 300

# the box's three doors: the full answer, its streamed form, and the signed-out preview
PATHS = {"/api/v1/us/ask": "full", "/api/v1/us/ask/stream": "full", "/api/v1/assistant/preview": "preview"}

# Addresses that are never a reader: the loopback and the private ranges (a
# check run inside the server or the container network; a reader behind the
# proxy always arrives with a public address).
_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
_OURS_CACHE = {}

_LOCK = threading.Lock()
_NOTES = OrderedDict()

# A question from anyone but us is also sent to the Telegram chat the site's
# alarms use (app/refresh.py), with nothing about who asked. The same question
# again within a minute is not sent twice (a signed-in reader's full answer can
# fall back to the preview), and a flood stops at ALERTS_PER_HOUR.
ALERT_REPEAT_SECONDS = 60
ALERTS_PER_HOUR = 30
_ALERTED = OrderedDict()            # lower-cased question -> when it was sent
_ALERT_HOUR = [None, 0]             # the hour being counted, messages sent in it
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


def _our_networks():
    """OUR_ADDRESSES: comma-separated addresses or ranges we ask from (the
    team's own machines). A malformed entry is skipped, never fatal."""
    raw = os.environ.get("OUR_ADDRESSES", "")
    nets = _OURS_CACHE.get(raw)
    if nets is None:
        nets = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                print("OUR_ADDRESSES: skipped %r, not an address or range" % part)
        _OURS_CACHE.clear()
        _OURS_CACHE[raw] = nets
    return nets


def is_ours(ip):
    """True when a request came from us. Takes the address only to decide
    this; nothing about it is written."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    if addr.is_loopback or any(addr in n for n in _PRIVATE if n.version == addr.version):
        return True
    return any(addr in n for n in _our_networks() if n.version == addr.version)


def _via(scope):
    raw = scope.get("query_string") or b""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    return (urllib.parse.parse_qs(raw).get("via") or [""])[0]


def _path_of(month):
    return ANALYTICS_DIR / ("ask-%s.jsonl" % month)


def record(scope, status, seconds=None, now=None, ours=False):
    """Write one line for a question the box was asked. Called by the visit
    counter for a request on one of PATHS that is not a robot's; `ours` is
    is_ours() of the request's address, worked out there."""
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
            "status": status, "seconds": round(seconds, 1) if isinstance(seconds, (int, float)) else None,
            "from": "us" if ours else ("example" if _via(scope) == "example" else "reader")}
    try:
        os.makedirs(str(ANALYTICS_DIR), exist_ok=True)
        with _LOCK:
            with open(str(_path_of(now.strftime("%Y-%m"))), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("ASK LOG WRITE FAILED (%s)" % exc)
    if line["from"] != "us":
        _alert(line, now)


def _alert_text(line):
    who = "Example click" if line["from"] == "example" else "Typed by a reader"
    result = {True: "answered", False: "not answered"}.get(line["answered"], "result not known")
    where = "signed-out preview" if line["where"] == "preview" else "full answer"
    return "Ask box: %s\n%s · %s · %s" % (line["q"], who, result, where)


def _alert(line, now):
    """Send the question to Telegram on a thread of its own, so a slow
    Telegram never holds up the reader's response. Never raises."""
    key = line["q"].lower()
    stamp = time.time()
    hour = now.strftime("%Y-%m-%dT%H")
    with _LOCK:
        last = _ALERTED.get(key)
        if last is not None and stamp - last < ALERT_REPEAT_SECONDS:
            return
        if _ALERT_HOUR[0] != hour:
            _ALERT_HOUR[0], _ALERT_HOUR[1] = hour, 0
        _ALERT_HOUR[1] += 1
        count = _ALERT_HOUR[1]
        if count > ALERTS_PER_HOUR:
            return
        _ALERTED[key] = stamp
        _ALERTED.move_to_end(key)
        while len(_ALERTED) > 500:
            _ALERTED.popitem(last=False)
    text = _alert_text(line)
    if count == ALERTS_PER_HOUR:
        text += "\n(No more this hour. The rest are on the admin Traffic page, Ask Box tab.)"
    _deliver(text)


def _deliver(text):
    from . import refresh
    threading.Thread(target=refresh._send_telegram, args=(text,), daemon=True).start()


def _median(values):
    v = sorted(x for x in values if isinstance(x, (int, float)))
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def _read(start, today):
    """Every logged line from `start` to `today`, oldest month first."""
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
                    if start.isoformat() <= str(e.get("at", ""))[:10] <= today.isoformat():
                        lines.append(e)
        except OSError:
            continue
    return _one_per_question(lines)


SAME_QUESTION_SECONDS = 30


def _seconds_at(at):
    try:
        return datetime.datetime.strptime(str(at)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _one_per_question(lines):
    """One line per question asked, though one question can reach the box as
    two requests: signed in, a question the full answer does not hold goes on
    to the economy answers a second later; and a click on an example puts the
    question in the box, where Ask or Enter sends it again (2026-10-22: a
    reader in Japan clicked 'Apple buybacks', pressed Ask three seconds later,
    and showed up as an example click and a typed question). The same
    question within SAME_QUESTION_SECONDS of the last request for it is folded
    into its first line: that line keeps when it was asked and who it came
    from, the last request says how it was answered. The file keeps every
    request; only what is counted and shown is folded."""
    out, last = [], {}
    for e in lines:
        key = _norm(e.get("q")).lower()
        when = _seconds_at(e.get("at"))
        seen = last.get(key)
        if seen and when and 0 <= (when - seen[1]).total_seconds() <= SAME_QUESTION_SECONDS:
            first = out[seen[0]]
            fell_back = first.get("where") == "full" and first.get("answered") is False and e.get("where") == "preview"
            merged = dict(e, at=first["at"], **{"from": first.get("from")})
            if fell_back:
                # the reader waited for both; they were signed in, so it was the full answer they saw
                spent = [s for s in (first.get("seconds"), e.get("seconds")) if isinstance(s, (int, float))]
                merged.update(where="full", seconds=round(sum(spent), 1) if spent else None)
            out[seen[0]] = merged
            last[key] = (seen[0], when)
            continue
        last[key] = (len(out), when)
        out.append(e)
    return out


FROM_KEYS = ("reader", "example", "us")


def weekly(today, weeks=8):
    """Questions by week (weeks start on Monday, UTC), newest first: how many
    readers typed, how many of those were answered, example clicks, our own
    checks, and lines from before the marking began. Weeks before the first
    logged question are left out rather than shown as zero."""
    monday = today - datetime.timedelta(days=today.weekday())
    first = monday - datetime.timedelta(weeks=weeks - 1)
    lines = _read(first, today)
    if not lines:
        return []
    begun = min(e["at"] for e in lines)[:10]
    rows = {}
    for e in lines:
        d = datetime.date.fromisoformat(e["at"][:10])
        wk = (d - datetime.timedelta(days=d.weekday())).isoformat()
        row = rows.setdefault(wk, {"reader": 0, "reader_answered": 0, "example": 0, "us": 0, "unmarked": 0})
        who = e.get("from")
        row[who if who in FROM_KEYS else "unmarked"] += 1
        if who == "reader" and e.get("answered") is True:
            row["reader_answered"] += 1
    out = []
    wk = monday
    while wk >= first:
        end = wk + datetime.timedelta(days=6)
        if end.isoformat() < begun:
            break
        row = rows.get(wk.isoformat(), {"reader": 0, "reader_answered": 0, "example": 0, "us": 0, "unmarked": 0})
        out.append(dict(row, week=wk.isoformat(), partial=wk == monday))
        wk -= datetime.timedelta(weeks=1)
    return out


def summary(days, start, today, hits=None, recent=50, top=15):
    """The Ask Box section: totals, one row per day, the latest questions and
    the ones asked most. `hits` comes from the visit log: [(at, visitor)] for
    each request to the box; only those from the first logged question on are
    counted, so people are never counted for searches this log never saw."""
    lines = _read(start, today)
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
    # which route answered, how often, how fast: the quick lane (code, no model), the reader (one model
    # call, code answers), the step route (the model writes SQL), the economy answers, and the misses
    by_route = {}
    for e in lines:
        r = e.get("route") or ("miss:" + str(e.get("route") or "")) if e.get("answered") is False else (e.get("route") or "unknown")
        if e.get("answered") is False:
            r = "miss"
        row = by_route.setdefault(r, {"route": r, "searches": 0, "answered": 0, "seconds": []})
        row["searches"] += 1
        row["answered"] += 1 if e.get("answered") is True else 0
        if e.get("seconds") is not None:
            row["seconds"].append(e["seconds"])
    routes = []
    for r in sorted(by_route.values(), key=lambda x: -x["searches"]):
        routes.append({"route": r["route"], "searches": r["searches"], "answered": r["answered"],
                       "median_seconds": _median(r["seconds"]), "share": round(100.0 * r["searches"] / len(lines), 1) if lines else None})
    return {
        "since": since,
        "searches": len(lines),
        "people": sum(people_by_day.get(r["day"], 0) for r in series),
        "answered": sum(1 for e in known if e["answered"]),
        "judged": len(known),
        "previews": sum(1 for e in lines if e.get("where") == "preview"),
        "median_seconds": _median([e.get("seconds") for e in lines]),
        "distinct_questions": len(asked),
        "routes": routes,
        "daily": series,
        "weekly": weekly(today),
        "recent": [{k: e.get(k) for k in ("at", "q", "where", "answered", "route", "shape", "seconds", "from")}
                   for e in sorted(lines, key=lambda e: e["at"], reverse=True)[:recent]],
        "most_asked": most,
    }
