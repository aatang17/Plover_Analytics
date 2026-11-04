"""Which front-page tasks readers use, and how far they get.

The visit log cannot see this. A task (Research a Stock, Earnings Preview, Draw
Charts, Screen Stocks, Insider Buying) runs inside the front page: choosing a
step changes the address in the browser, and the server is never asked for a
page. So the page says what happened, through the same ping that reports
reading time (app/visits_api.py), and this module writes it down:

  * open    - a reader opened a task: from the menu under the box, from the
              demonstration's Try button, or from an address that already
              named it (a shared link, a bookmark);
  * a step  - the step they reached (0 is the first choice);
  * result  - the task showed its answer;
  * failed  - the answer could not be made;
  * a demo  - a reader picked one of the demonstrations by hand (the ones that
              play by themselves are not counted).

Real readers only, by the same test as every figure on the Traffic page
(visits._is_reader), with the same daily identity, so the counts agree with
the visit log. Nothing a reader typed is kept: the one choice stored is the
company ticker of a result, or which figure a chart shows. Append-only JSONL
beside the visit log, one file a month; no database write path.
"""
import datetime
import json
import re
import threading

from . import geoip, visits

# The steps of each task, in order, as the panel names them (home-tasks.js
# STEPS). The last one is where the answer appears.
TASKS = (
    ("research", "Research a Stock", ("Choose a Company", "Choose What Goes In", "Read and Download")),
    ("earnings", "Earnings Preview", ("Choose a Company", "Read the Preview")),
    ("chart", "Draw Charts", ("What to Chart", "Companies", "Period", "The Chart")),
    ("screen", "Screen Stocks", ("Company Size", "Sector", "What to Look For", "Results")),
    ("insiders", "Insider Buying", ("Who Is Buying", "Period and Size", "Results")),
)
_STEPS = dict((key, steps) for key, _, steps in TASKS)
DEMOS = (("chart", "Draw Charts"), ("screen", "Screen Stocks"),
         ("earnings", "Earnings Preview"), ("insiders", "Insider Buying"))
_DEMOS = dict(DEMOS)
OPENED_FROM = ("menu", "demo", "link")

_PICK = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
_LOCK = threading.Lock()
TOP_PICKS = 10


def _path(month):
    return visits.ANALYTICS_DIR / ("tasks-%s.jsonl" % month)


def clean(data):
    """The event a page sent, reduced to what is kept, or None if it is not
    one of ours. Only fixed words pass; the pick is a ticker or a short id."""
    task, step = data.get("task"), data.get("step")
    if task == "demo":
        return {"task": "demo", "step": step} if step in _DEMOS else None
    steps = _STEPS.get(task)
    if steps is None:
        return None
    if isinstance(step, bool):
        return None
    if isinstance(step, int):
        # the last step is the answer: the page says result or failed there
        if not 0 <= step < len(steps) - 1:
            return None
    elif step not in ("open", "result", "failed"):
        return None
    event = {"task": task, "step": step}
    if step == "open":
        if data.get("from") in OPENED_FROM:
            event["from"] = data["from"]
    elif step == "result":
        pick = data.get("pick")
        if isinstance(pick, str) and _PICK.match(pick):
            event["pick"] = pick.upper() if task in ("research", "earnings") else pick.lower()
    return event


def record(scope, data):
    """One task event from a page. Silent whatever happens: the caller is a
    beacon, and counting must never break a page."""
    event = clean(data)
    if event is None:
        return
    headers = [(k.lower(), v) for k, v in scope.get("headers") or ()]
    user_agent = visits._header(headers, b"user-agent")
    if visits._is_bot(user_agent):
        return
    ip = visits._client_ip(scope, visits._header(headers, b"x-forwarded-for"))
    if visits._is_ours(ip, headers):
        return
    geoip.prepare()
    country, network = geoip.lookup(ip)
    member = visits._signed_in(headers)
    # The page's own script sent this, so the browser test is met; the network
    # test is the one that can still fail.
    if not visits._is_reader(True, network, member):
        return
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    day = now.strftime("%Y-%m-%d")
    sender = visits.identify(ip, user_agent, headers, day)[0]
    out = {"at": now.isoformat(timespec="seconds") + "Z",
           "visitor": visits._rejoin(sender, "/", country, network, day),
           "country": country}
    if member:
        out["member"] = True
    out.update(event)
    line = json.dumps(out, ensure_ascii=False) + "\n"
    try:
        visits.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(str(_path(day[:7])), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        print("TASK LOG WRITE FAILED (%s)" % exc)


def _events(start, end):
    """Every logged event with start <= day <= end, oldest first."""
    months, cursor = [], datetime.date(start.year, start.month, 1)
    while cursor <= end:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor + datetime.timedelta(days=32)).replace(day=1)
    lo, hi = start.isoformat(), end.isoformat()
    for month in months:
        try:
            with open(str(_path(month)), encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and lo <= str(event.get("at", ""))[:10] <= hi:
                        yield event
        except OSError:
            continue


def since():
    """The first day anything was logged, or None."""
    files = sorted(visits.ANALYTICS_DIR.glob("tasks-*.jsonl")) if visits.ANALYTICS_DIR.exists() else []
    for path in files:
        try:
            with open(str(path), encoding="utf-8") as f:
                for line in f:
                    try:
                        return json.loads(line)["at"][:10]
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            continue
    return None


def summary(days=30, today=None):
    """Each task's use over the last `days` days. A reader is one visitor on
    one day, as on the rest of the Traffic page."""
    end = today or datetime.datetime.now(datetime.timezone.utc).date()
    start = end - datetime.timedelta(days=max(1, days) - 1)
    tasks = dict((key, {"opened": set(), "steps": {}, "results": 0, "result_readers": set(),
                        "failed": 0, "failed_readers": set(), "from": {}, "picks": {}})
                 for key in _STEPS)
    demos = dict((key, {"clicks": 0, "readers": set()}) for key in _DEMOS)
    for event in _events(start, end):
        reader = (str(event.get("at", ""))[:10], event.get("visitor"))
        task, step = event.get("task"), event.get("step")
        if task == "demo":
            if step in demos:
                demos[step]["clicks"] += 1
                demos[step]["readers"].add(reader)
            continue
        t = tasks.get(task)
        if t is None:
            continue
        t["opened"].add(reader)
        if step == "open":
            how = event.get("from") or "menu"
            t["from"].setdefault(how, set()).add(reader)
        elif step == "result":
            t["results"] += 1
            t["result_readers"].add(reader)
            if event.get("pick"):
                t["picks"][event["pick"]] = t["picks"].get(event["pick"], 0) + 1
        elif step == "failed":
            t["failed"] += 1
            t["failed_readers"].add(reader)
        elif isinstance(step, int):
            t["steps"].setdefault(step, set()).add(reader)

    rows = []
    for key, name, steps in TASKS:
        t = tasks[key]
        reached = [len(t["steps"].get(i, ())) for i in range(len(steps) - 1)]
        reached.append(len(t["result_readers"]))
        rows.append({
            "key": key,
            "name": name,
            "readers": len(t["opened"]),
            "results": t["results"],
            "result_readers": len(t["result_readers"]),
            "failed": t["failed"],
            "failed_readers": len(t["failed_readers"]),
            "steps": [{"name": n, "readers": r} for n, r in zip(steps, reached)],
            "from": dict((how, len(t["from"].get(how, ()))) for how in OPENED_FROM),
            "picks": [{"key": k, "results": n} for k, n in
                      sorted(t["picks"].items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_PICKS]],
        })
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "since": since(),
        "tasks": rows,
        "demos": [{"key": key, "name": name, "clicks": demos[key]["clicks"],
                   "readers": len(demos[key]["readers"])} for key, name in DEMOS],
    }
