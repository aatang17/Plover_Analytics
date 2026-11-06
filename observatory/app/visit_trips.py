"""Latest Visits: each recent visitor, and the trip they took through the site.

The Traffic page's other tabs add readers up. This one lists them: when each
arrived, from where and through which link, every page they opened in order
and how long each stayed open, and the organisation their network names. It
answers "who was that?" without anybody reading the log by hand.

Read straight off the end of the visit log (app/visits.py), the last day or
so, plus whatever is still waiting in memory to be written, so a visit shows
within seconds. The same reader test as every other figure decides who is a
person; everything that fails it is listed apart, grouped by what it is
(LinkedIn's link preview, a search engine's crawler, a script). One exception:
a visit from a network that names an organisation is listed with the readers
even when its page never reported in, marked unconfirmed, because a bank's or
a fund's network often blocks that report.

A visitor here is the daily identity, so a trip ends at midnight UTC: the same
person tomorrow is a new row, unless they accepted the cookie (then they show
as returning) or are signed in (then their email domain shows).
"""
import datetime
import os
import time

from . import ask_log, visit_orgs, visits

# How far back the list reaches, and at most how many visitors it shows.
DEFAULT_HOURS = 24
MAX_HOURS = 72
MAX_VISITORS = 100
MAX_STEPS = 60
# Bytes read from the end of a log file at a time, walking back to the window.
_CHUNK = 256 * 1024

# Machines, named by what they are. A link preview is a site fetching the page
# to draw the card when somebody pastes the link into a post or a message: it
# says the link was shared there, and nothing more.
_MACHINE_NETWORKS = (
    ("linkedin", "LinkedIn link preview"),
    ("twitter", "X (Twitter) link preview"),
    ("facebook", "Facebook link preview"),
    ("meta platforms", "Facebook link preview"),
    ("slack", "Slack link preview"),
    ("telegram", "Telegram link preview"),
    ("discord", "Discord link preview"),
    ("yandex", "Yandex search crawler"),
    ("baidu", "Baidu search crawler"),
    ("google llc", "Google"),
    ("microsoft", "Microsoft (Bing and others)"),
    ("apple", "Apple"),
    ("amazon", "A machine on Amazon's cloud"),
)

# A referring site as a reader would name it. App referrers (com.linkedin.android)
# are the same source as the site.
_SOURCES = (
    ("linkedin", "LinkedIn"), ("lnkd.in", "LinkedIn"),
    ("t.co", "X (Twitter)"), ("twitter", "X (Twitter)"), ("x.com", "X (Twitter)"),
    ("gemini.google", "Gemini"), ("youtube", "YouTube"), ("google", "Google"),
    ("bing.com", "Bing"), ("duckduckgo", "DuckDuckGo"), ("yahoo", "Yahoo"),
    ("baidu", "Baidu"), ("yandex", "Yandex"), ("facebook", "Facebook"),
    ("reddit", "Reddit"), ("substack", "Substack"), ("chatgpt.com", "ChatGPT"),
    ("perplexity", "Perplexity"), ("claude.ai", "Claude"), ("telegram", "Telegram"),
    ("whatsapp", "WhatsApp"), ("slack", "Slack"), ("instagram", "Instagram"),
    ("threads", "Threads"), ("news.ycombinator", "Hacker News"),
)


def source_name(host):
    """"com.linkedin.android" -> "LinkedIn"; an unknown host stays as it is."""
    if not host:
        return None
    name = host.lower()
    for marker, label in _SOURCES:
        if name == marker or marker in name.split("/")[0]:
            if marker == "t.co" and name != "t.co":
                continue
            if marker == "x.com" and not (name == "x.com" or name.endswith(".x.com")):
                continue
            return label
    return host


def _machine_name(event, network):
    if event.get("agent"):
        purpose = {"search": "search index", "training": "training crawler",
                   "asked": "fetched for a question"}.get(event.get("agent_purpose"), "")
        return event["agent"] + (" " + purpose if purpose else "")
    lowered = (network or "").lower()
    for marker, name in _MACHINE_NETWORKS:
        if marker in lowered:
            return name
    return None


def _tail(path, cutoff):
    """Lines of `path` at or after `cutoff` (an ISO timestamp), oldest first.
    Walks back from the end in chunks, so a month's log is not read whole."""
    try:
        size = os.path.getsize(str(path))
    except OSError:
        return []
    out = []
    with open(str(path), "rb") as f:
        end, rest = size, b""
        while end > 0:
            start = max(0, end - _CHUNK)
            f.seek(start)
            block = f.read(end - start) + rest
            end = start
            lines = block.split(b"\n")
            # The first piece may be a partial line unless the start of the file was reached.
            rest = lines.pop(0) if start > 0 else b""
            done = False
            for raw in reversed(lines):
                raw = raw.strip()
                if not raw:
                    continue
                at = raw[8:28].decode("ascii", "replace") if raw.startswith(b'{"at": "') else None
                if at is not None and at < cutoff:
                    done = True
                    break
                out.append(raw)
            if done:
                break
        if end == 0 and rest.strip():
            out.append(rest.strip())
    out.reverse()
    return out


def _events(cutoff):
    import json
    files = [p for p in visits._log_files()
             if p.name[len("visits-"):-len(".jsonl")] >= cutoff[:7]]
    raw = []
    for path in sorted(files):
        raw.extend(_tail(path, cutoff))
    events = []
    for line in raw:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (event.get("at") or "") >= cutoff:
            events.append(event)
    with visits._LOCK:
        waiting = [dict(e) for e in visits._BUFFER]
    events.extend(e for e in waiting if (e.get("at") or "") >= cutoff)
    events.sort(key=lambda e: e.get("at") or "")
    return events


def _is_read(event):
    if event.get("kind") != "page":
        return False
    status = event.get("status") or 0
    if status >= 400 or (300 <= status < 400 and status != 304):
        return False
    return not (event.get("path") or "").lower().endswith(visits.ASSET_SUFFIXES)


def _trip(events):
    """A reader's page reads in order, each with the time it stayed open."""
    steps = []
    for event in events:
        if _is_read(event):
            step = {"at": event["at"], "path": event.get("path") or "/", "seconds": None}
            if event.get("ref"):
                step["from"] = source_name(event["ref"])
            if event.get("src"):
                step["tag"] = event["src"]
            steps.append(step)
        elif event.get("path") in ask_log.PATHS and event.get("kind") == "api":
            steps.append({"at": event["at"], "path": event["path"], "asked": True})
    # Each page view reports under one id, often more than once (every time it
    # is hidden), carrying the running total: the longest report is the read.
    views = {}
    for event in events:
        # An early report says a browser showed the page, not for how long.
        if event.get("kind") != "dwell" or event.get("early"):
            continue
        key = event.get("view") or (event.get("path"), event.get("at"))
        held = views.get(key)
        if held is None:
            views[key] = [event.get("at"), event.get("path"), event.get("seconds") or 0]
        elif (event.get("seconds") or 0) > held[2]:
            held[2] = event["seconds"]
    taken = set()
    for first, path, seconds in sorted(views.values(), key=lambda v: v[0] or ""):
        for i in range(len(steps) - 1, -1, -1):
            step = steps[i]
            if i in taken or step.get("asked") or step["path"] != path or step["at"] > first:
                continue
            step["seconds"] = seconds
            taken.add(i)
            break
    return steps


def recent(hours=DEFAULT_HOURS, limit=MAX_VISITORS):
    """The last `hours` of visits: readers with their trips, and machines."""
    visits.flush()
    hours = max(1, min(int(hours), MAX_HOURS))
    limit = max(1, min(int(limit), MAX_VISITORS))
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    cutoff = (now - datetime.timedelta(hours=hours)).isoformat(timespec="seconds") + "Z"

    by_visitor, drawn = {}, set()
    for event in _events(cutoff):
        if event.get("kind") == "browser":
            drawn.add(event.get("visitor"))
            continue
        visitor = event.get("visitor")
        if visitor:
            by_visitor.setdefault(visitor, []).append(event)

    with visits._LOCK:
        live = dict((v, d["at"]) for v, d in visits._LIVE.items())
    here = time.time() - visits.LIVE_SECONDS

    readers, machines = [], {}
    for visitor, events in by_visitor.items():
        network = next((e["network"] for e in reversed(events) if e.get("network")), None)
        country = next((e["country"] for e in reversed(events) if e.get("country")), None)
        robot = any(e.get("bot") for e in events) or visits.is_google_machine(network)
        scripted = any(e.get("kind") == "dwell" for e in events)
        member = any(e.get("member") for e in events)
        confirmed = not robot and visits._is_reader(scripted, network, member)
        # A bank's or a fund's network often stops the page's own report, so a
        # visit from a network that names an organisation is listed even
        # unconfirmed, and marked so. Anywhere else an unconfirmed visit is
        # far more often a script, and is listed with the machines.
        named = visit_orgs.kind(network) in (visit_orgs.ORGANISATION, visit_orgs.SECURITY)
        # The .md copies of pages are read by scripts and assistants, not people.
        opened = any(_is_read(e) and not (e.get("path") or "").endswith(".md") for e in events)
        if confirmed or (not robot and named and opened):
            steps = _trip(events)
            reads = [s for s in steps if not s.get("asked")]
            if not reads:
                continue  # a page report with no page: nothing to show
            first = reads[0]
            if first.get("tag"):
                # Ours (?ref=yt), or one a site adds itself (utm_source=chatgpt.com).
                named = source_name(first["tag"])
                arrived = named if named != first["tag"] else "Tagged link (" + first["tag"] + ")"
            elif first.get("from"):
                arrived = first["from"]
            elif next(e for e in events if _is_read(e)).get("internal"):
                # Their first page today was reached from one of ours: the
                # visit began before the window, or before midnight UTC.
                arrived = "Another Plover page"
            else:
                arrived = "No link (typed, bookmark or an app)"
            timed = [s["seconds"] for s in reads if s.get("seconds")]
            readers.append({
                "visitor": visitor,
                "first": steps[0]["at"],
                "last": events[-1]["at"],
                "country": country,
                "network": visit_orgs.label(network),
                "network_kind": visit_orgs.kind(network),
                "organisation": visit_orgs.organisation(network),
                "member": member,
                "member_domain": next((e["member_domain"] for e in events
                                       if e.get("member_domain")), None),
                "returning_since": next((e["since"] for e in events if e.get("since")), None),
                "arrived": arrived,
                "pages": len(reads),
                "seconds": sum(timed) if timed else None,
                "asked": sum(1 for s in steps if s.get("asked")),
                "here_now": live.get(visitor, 0) >= here,
                "confirmed": confirmed,
                # A browser drew a page (it fetched the page's styles or
                # pixel); for an unconfirmed visit, a person whose scripts
                # were blocked rather than a script reading the page.
                "drawn": visitor in drawn,
                "steps": steps[:MAX_STEPS],
            })
            continue
        name = _machine_name(next((e for e in events if e.get("agent")), events[0]), network)
        if name is None:
            if robot:
                name = "Other robot on " + (visit_orgs.label(network) or "an unplaced network")
            elif visit_orgs.kind(network) == visit_orgs.HOSTING:
                name = "Unconfirmed: from a data centre or VPN"
            else:
                name = "Unconfirmed: the page never reported from a browser"
        row = machines.setdefault(name, {"name": name, "requests": 0, "visitors": 0,
                                         "pages": {}, "countries": {}, "last": None})
        row["visitors"] += 1
        for event in events:
            row["requests"] += 1
            if _is_read(event):
                path = event.get("path") or "/"
                row["pages"][path] = row["pages"].get(path, 0) + 1
            if event.get("country"):
                row["countries"][event["country"]] = row["countries"].get(event["country"], 0) + 1
            if row["last"] is None or event["at"] > row["last"]:
                row["last"] = event["at"]

    readers.sort(key=lambda r: r["last"], reverse=True)
    confirmed_total = sum(1 for r in readers if r["confirmed"])
    machine_rows = []
    for row in machines.values():
        pages = sorted(row["pages"].items(), key=lambda kv: (-kv[1], kv[0]))
        countries = sorted(row["countries"].items(), key=lambda kv: (-kv[1], kv[0]))
        machine_rows.append({"name": row["name"], "requests": row["requests"],
                             "visitors": row["visitors"], "last": row["last"],
                             "pages": [p for p, _ in pages[:3]],
                             "countries": [c for c, _ in countries[:3]]})
    machine_rows.sort(key=lambda r: r["last"] or "", reverse=True)
    return {
        "at": now.isoformat(timespec="seconds") + "Z",
        "hours": hours,
        "from": cutoff,
        "readers": readers[:limit],
        "readers_total": confirmed_total,
        "unconfirmed_total": len(readers) - confirmed_total,
        "machines": machine_rows,
    }

