# -*- coding: utf-8 -*-
"""The daily look at what the ask box refused.

Once a day, on the server, with no one asked to remember:

  1. Every question a reader typed and the box did not answer, over the last
     REVIEW_DAYS days, is asked again on today's data. One that now answers is
     marked fixed; the rest are sorted:
        fix      we hold what it asks for: the box is wrong, fix the box
        gap      we truly do not hold it (app/us_figures.GAPS): consider sourcing it
        look     neither is clear: read it
        fine     not a data question (advice, gibberish, another market)
  2. The figure matrix (app/ask_coverage.py): the code check whole, and a
     MATRIX_SAMPLE-question sample of the model's forms, moving on through the
     list each night.

The result is one JSON file, shown on the admin Traffic page's Ask Box tab, and
one Telegram message when something needs fixing. Fixes are ours to make; this
never changes an answer. Questions are as logged by app/ask_log.py, with nothing
about who asked. ASK_REVIEW=0 switches it off. It writes only its own files under
data/analytics/ and runs at most once a day however many servers are up.
"""
import datetime
import json
import os
import threading
import time

from fastapi import HTTPException

from . import ask_log, ask_miss, sec_api, us_figures

REVIEW_DAYS = 14
REPLAY_MAX = 40             # questions asked again per day (each can take a few model calls)
SWEEP_DAYS = 30             # the nightly sweep: every question readers typed in this window, answered or not
SWEEP_MAX = 120             # asked again per night, most-asked first
MATRIX_SAMPLE = 30          # model questions per day from the matrix
CATALOGUE_SAMPLE = 8        # economic datasets asked about per day, so the whole catalogue comes round about weekly
START_AFTER_SECONDS = 1800  # the first half hour after a boot belongs to readers
TAKE_OVER_SECONDS = 3 * 3600
_STARTED = time.time()
_RUNNING = threading.Lock()


def report_path():
    return ask_log.ANALYTICS_DIR / "ask-review.json"


def _lock_path(day):
    return ask_log.ANALYTICS_DIR / ("ask-review-%s.lock" % day)


def enabled():
    return os.environ.get("ASK_REVIEW", "").strip() != "0"


# "another market" is asked again: Japan's GDP and Hong Kong's CPI were filed here as fine, unread
_NOT_ASKED_AGAIN = ("policy", "unclear")


def sort_miss(question, kind):
    """Which pile a refused question goes on."""
    if kind in ("policy", "unclear"):
        return "fine"
    verdict = us_figures.judge("", question)["verdict"]
    if verdict == "held":
        return "fix"
    if verdict == "gap" or us_figures._GAP.search(question or ""):
        return "gap"
    return "look"


def refused(days=REVIEW_DAYS, today=None):
    """Distinct questions readers typed and the box did not answer, newest
    first: [{q, times, last, kind}]. Our own checks and example clicks are left out."""
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=days)
    seen = {}
    for e in ask_log._read(start, today):
        if e.get("answered") is not False or e.get("from") not in ("reader", None):
            continue
        k = (e.get("q") or "").strip().lower()
        if not k:
            continue
        row = seen.setdefault(k, {"q": e["q"], "times": 0, "last": "", "kind": e.get("route")})
        row["times"] += 1
        if str(e.get("at")) > row["last"]:
            row["last"], row["kind"] = str(e.get("at")), e.get("route")
    return sorted(seen.values(), key=lambda r: r["last"], reverse=True)


def asked(days=SWEEP_DAYS, today=None, froms=("reader",)):
    """Distinct questions readers typed in the window, answered or not, most
    asked first: [{q, times, last, kind, answered}]. `kind` is the route or
    miss of the latest asking. Questions answered from the web are left out:
    asking them again would pay for a search and compare news with news."""
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=days)
    seen = {}
    for e in ask_log._read(start, today):
        if e.get("from") not in froms or e.get("shape") == "web_news" or e.get("route") == "web":
            continue
        k = " ".join((e.get("q") or "").split()).lower()
        if not k:
            continue
        row = seen.setdefault(k, {"q": e["q"], "times": 0, "last": "", "kind": e.get("route"), "answered": e.get("answered")})
        row["times"] += 1
        if str(e.get("at")) > row["last"]:
            row.update(last=str(e.get("at")), kind=e.get("route"), answered=e.get("answered"))
    return sorted(seen.values(), key=lambda r: (-r["times"], r["last"]), reverse=False)


def _key(q):
    return " ".join((q or "").split()).lower()


def sweep(cur, today=None, previous=None, froms=("reader",), limit=SWEEP_MAX, ask=None):
    """The nightly sweep: every reader question of the last SWEEP_DAYS days is
    asked again on today's data and compared with the last sweep's answer.
    Returns {at, days, asked, answered, missed, regressed, changed, fixed,
    misses, results}; a question whose answer stopped, or whose headline
    changed with no new data behind it, is a miss to file before a reader
    finds it (2026-11-05: "what does Saba Capital own" went to the web for
    weeks with the filings on file, and nothing noticed)."""
    today = today or datetime.date.today()
    rows = asked(today=today, froms=froms)[:limit]
    done = replay(cur, rows, ask=ask)
    before = {_key(r["q"]): r for r in ((previous or {}).get("results") or [])}
    regressed, changed, fixed, misses = [], [], [], []
    for r in done:
        was = before.get(_key(r["q"]))
        answers = r["now"] == "answers"
        if answers and was and was.get("now") == "answers" and (was.get("said") or "") != (r.get("said") or ""):
            changed.append({"q": r["q"], "before": was.get("said"), "now": r.get("said"), "times": r["times"]})
        if not answers and was and was.get("now") == "answers":
            regressed.append({"q": r["q"], "before": was.get("said"), "now": r.get("said") or r["now"], "times": r["times"]})
        if answers and was and was.get("now") != "answers" and was.get("pile") != "fine":
            fixed.append({"q": r["q"], "now": r.get("said"), "times": r["times"]})
        if not answers and r.get("pile") != "fine":
            misses.append({"q": r["q"], "now": r["now"], "said": r.get("said"), "pile": r.get("pile"), "times": r["times"],
                           "new": was is None})
    return {"at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "days": SWEEP_DAYS, "asked": len(done), "answered": len([r for r in done if r["now"] == "answers"]),
            "missed": len(misses), "regressed": regressed, "changed": changed, "fixed": fixed, "misses": misses,
            "results": [{"q": r["q"], "now": r["now"], "said": r.get("said"), "pile": r.get("pile"), "times": r["times"]} for r in done]}


def _full_answer(cur, question):
    """What a reader gets: the box, then the fixed economy answers the page asks after it."""
    from . import stock_recipes
    from .assistant import preview
    try:
        return stock_recipes.answer(cur, question)
    except HTTPException as exc:
        if exc.detail != stock_recipes.MACRO_MISS:
            raise
        fixed = preview._macro(question)
        if not fixed:
            from . import ask_guard
            fixed = ask_guard._steps_last(cur, question, None, HTTPException(422, "economy: no fixed answer"))
        if not fixed:
            raise
        return fixed


def replay(cur, rows, ask=None):
    """Ask each question again. Each row gains: now ('answers' | the kind), said, pile."""
    from . import stock_recipes
    ask = ask or (lambda q: _full_answer(cur, q))
    out = []
    for r in rows:
        row = dict(r)
        if r.get("kind") in _NOT_ASKED_AGAIN:
            # advice, gibberish, another market: nothing data could change, and a "why" would spend a paid news search
            row.update(now=r["kind"], said="", pile="fine")
            out.append(row)
            continue
        try:
            a = ask(r["q"])
            row.update(now="answers", said=(a or {}).get("headline") or "", pile="fixed")
        except HTTPException as exc:
            kind = getattr(exc, "miss", None) or ask_miss.classify(exc.detail if isinstance(exc.detail, str) else "")
            row.update(now=kind, said=ask_miss.plain(exc.detail) if isinstance(exc.detail, str) else "", pile=sort_miss(r["q"], kind))
        except Exception as exc:                                   # noqa: BLE001 — one question never stops the review
            row.update(now="error", said=type(exc).__name__, pile="fix")
        out.append(row)
    return out


def _matrix_offset(today):
    return today.toordinal() * MATRIX_SAMPLE


def run(cur=None, today=None):
    """One whole review; writes and returns the report."""
    from . import ask_coverage, us_ask_model
    today = today or datetime.date.today()
    cur = cur or sec_api._require()
    rows = refused(today=today)
    done = replay(cur, rows[:REPLAY_MAX])
    code = ask_coverage.check_code(cur)
    catalogue = ask_coverage.check_catalogue()
    catalogue_model = {"asked": 0, "total": 0, "failed": []}
    model = {"asked": 0, "failed": [], "total": 0}
    if us_ask_model.provider() is not None:
        model = ask_coverage.check_model(cur, MATRIX_SAMPLE, _matrix_offset(today))
        catalogue_model = ask_coverage.check_catalogue_model(CATALOGUE_SAMPLE, today.toordinal() * CATALOGUE_SAMPLE)
    piles = {}
    for r in done:
        piles[r["pile"]] = piles.get(r["pile"], 0) + 1
    try:
        sw = sweep(cur, today, (latest() or {}).get("sweep"))
    except Exception as exc:                                       # noqa: BLE001 — the review still reports the rest
        print("ask review: sweep failed: %s" % exc)
        sw = None
    report = {"day": today.isoformat(), "at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
              "window_days": REVIEW_DAYS, "refused": len(rows), "replayed": len(done), "piles": piles, "questions": done,
              "matrix": {"code_asked": code["asked"], "code_failed": code["failed"], "model_asked": model["asked"],
                         "model_total": model["total"], "model_failed": model["failed"]},
              "catalogue": {"datasets": catalogue["asked"], "failed": catalogue["failed"], "model_asked": catalogue_model["asked"],
                            "model_failed": catalogue_model["failed"]},
              "sweep": sw}
    os.makedirs(str(ask_log.ANALYTICS_DIR), exist_ok=True)
    tmp = str(report_path()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False)
    os.replace(tmp, str(report_path()))
    _tell(report)
    return report


def latest():
    """The last report, or None."""
    try:
        with open(str(report_path()), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def summary_text(report):
    """The Telegram message, or None when nothing needs fixing."""
    m = report["matrix"]
    wrong = [r for r in report["questions"] if r["pile"] == "fix"]
    cat = report.get("catalogue") or {}
    sw = report.get("sweep") or {}
    if not wrong and not m["code_failed"] and not m["model_failed"] and not cat.get("failed") and not cat.get("model_failed") \
            and not sw.get("regressed") and not [x for x in sw.get("misses") or [] if x.get("new") and x.get("pile") == "fix"]:
        return None
    lines = ["Ask box review, %s:" % report["day"]]
    if sw.get("regressed"):
        lines.append("%d question%s answered last night and not tonight:" % (len(sw["regressed"]), "" if len(sw["regressed"]) == 1 else "s"))
        lines += ["- %s" % r["q"] for r in sw["regressed"][:6]]
    new_fix = [x for x in sw.get("misses") or [] if x.get("new") and x.get("pile") == "fix"]
    if new_fix:
        lines.append("%d new reader question%s we hold the data for and did not answer:" % (len(new_fix), "" if len(new_fix) == 1 else "s"))
        lines += ["- %s" % r["q"] for r in new_fix[:6]]
    if sw.get("changed"):
        lines.append("%d answer%s changed since last night (new data or a wobble; read them)." % (len(sw["changed"]), "" if len(sw["changed"]) == 1 else "s"))
    if wrong:
        lines.append("%d refused question%s we hold the data for:" % (len(wrong), "" if len(wrong) == 1 else "s"))
        lines += ["- %s" % r["q"] for r in wrong[:8]]
    if m["code_failed"]:
        lines.append("%d of %d figure checks failed without the AI." % (len(m["code_failed"]), m["code_asked"]))
    if m["model_failed"]:
        lines.append("%d of %d figure questions failed with the AI, e.g. %s" % (len(m["model_failed"]), m["model_asked"], m["model_failed"][0]["question"]))
    if cat.get("failed"):
        lines.append("%d of %d economic datasets the box cannot reach, e.g. %s" % (len(cat["failed"]), cat["datasets"], cat["failed"][0]["dataset"]))
    if cat.get("model_failed"):
        lines.append("%d of %d economic dataset questions failed with the AI, e.g. %s" % (len(cat["model_failed"]), cat["model_asked"], cat["model_failed"][0]["question"]))
    lines.append("Details: admin Traffic page, Ask Box tab.")
    return "\n".join(lines)


def _tell(report):
    text = summary_text(report)
    if not text:
        return
    try:
        from . import refresh
        refresh._send_telegram(text)
    except Exception as exc:                                       # noqa: BLE001 — a review never fails for its message
        print("ask review: message failed: %s" % exc)


def run_if_due(now=None):
    """Called from the health watch (app/refresh.py). Starts the day's review on
    its own thread and returns at once; the file made with O_EXCL means a second
    server, or a restart, never runs the same day's twice."""
    if not enabled() or time.time() - _STARTED < START_AFTER_SECONDS:
        return False
    day = (now or datetime.datetime.now(datetime.timezone.utc)).date().isoformat()
    try:
        os.makedirs(str(ask_log.ANALYTICS_DIR), exist_ok=True)
        os.close(os.open(str(_lock_path(day)), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        # a deploy can end a run halfway: with no report for the day after a few hours, this server takes it over
        try:
            stale = time.time() - os.path.getmtime(str(_lock_path(day))) > TAKE_OVER_SECONDS
        except OSError:
            return False
        if not stale or (latest() or {}).get("day") == day or _RUNNING.locked():
            return False
        os.utime(str(_lock_path(day)))
    except OSError:
        return False
    threading.Thread(target=_safely, name="ask-review", daemon=True).start()
    return True


def _safely():
    if not _RUNNING.acquire(blocking=False):
        return
    try:
        run()
    except Exception as exc:                                       # noqa: BLE001 — never takes the server with it
        print("ask review failed: %s" % exc)
    finally:
        _RUNNING.release()


if __name__ == "__main__":
    # python -m app.ask_review            the whole review, now, on this machine's data
    # python -m app.ask_review --sweep    the sweep alone, printed
    import sys
    _cur = sec_api._require()
    if "--sweep" in sys.argv:
        _froms = ("reader", "example", "us") if "--ours" in sys.argv else ("reader",)
        _sw = sweep(_cur, previous=(latest() or {}).get("sweep"), froms=_froms, limit=int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else SWEEP_MAX)
        print(json.dumps({k: v for k, v in _sw.items() if k != "results"}, ensure_ascii=False, indent=1))
        if "--save" in sys.argv:                               # into the report the admin page reads, keeping the rest of it
            _rep = dict(latest() or {"day": datetime.date.today().isoformat(), "at": _sw["at"], "window_days": REVIEW_DAYS, "refused": 0,
                                     "replayed": 0, "piles": {}, "questions": [], "matrix": {"code_asked": 0, "code_failed": [], "model_asked": 0,
                                                                                            "model_total": 0, "model_failed": []}})
            _rep["sweep"] = _sw
            os.makedirs(str(ask_log.ANALYTICS_DIR), exist_ok=True)
            with open(str(report_path()), "w", encoding="utf-8") as _f:
                json.dump(_rep, _f, ensure_ascii=False)
        for _r in _sw["results"]:
            print("%-8s %-3d %s\n         %s" % (_r["now"], _r["times"], _r["q"], (_r["said"] or "")[:150]))
    else:
        _rep = run(_cur)
        print(summary_text(_rep) or "nothing to fix")
