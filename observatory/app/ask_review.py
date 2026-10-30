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
MATRIX_SAMPLE = 30          # model questions per day from the matrix
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


_NOT_ASKED_AGAIN = ("policy", "unclear", "other_market")


def sort_miss(question, kind):
    """Which pile a refused question goes on."""
    if kind in ("policy", "unclear", "other_market"):
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


def replay(cur, rows, ask=None):
    """Ask each question again. Each row gains: now ('answers' | the kind), said, pile."""
    from . import stock_recipes
    ask = ask or (lambda q: stock_recipes.answer(cur, q))
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
    model = {"asked": 0, "failed": [], "total": 0}
    if us_ask_model.provider() is not None:
        model = ask_coverage.check_model(cur, MATRIX_SAMPLE, _matrix_offset(today))
    piles = {}
    for r in done:
        piles[r["pile"]] = piles.get(r["pile"], 0) + 1
    report = {"day": today.isoformat(), "at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
              "window_days": REVIEW_DAYS, "refused": len(rows), "replayed": len(done), "piles": piles, "questions": done,
              "matrix": {"code_asked": code["asked"], "code_failed": code["failed"], "model_asked": model["asked"],
                         "model_total": model["total"], "model_failed": model["failed"]}}
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
    if not wrong and not m["code_failed"] and not m["model_failed"]:
        return None
    lines = ["Ask box review, %s:" % report["day"]]
    if wrong:
        lines.append("%d refused question%s we hold the data for:" % (len(wrong), "" if len(wrong) == 1 else "s"))
        lines += ["- %s" % r["q"] for r in wrong[:8]]
    if m["code_failed"]:
        lines.append("%d of %d figure checks failed without the AI." % (len(m["code_failed"]), m["code_asked"]))
    if m["model_failed"]:
        lines.append("%d of %d figure questions failed with the AI, e.g. %s" % (len(m["model_failed"]), m["model_asked"], m["model_failed"][0]["question"]))
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
