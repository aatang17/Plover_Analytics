# -*- coding: utf-8 -*-
"""Free answers: a few full answers a day for a reader without an account.

Why
---
Until 2026-10-29 a signed-out reader saw only the start of an answer and was
asked for an email to see the rest. Four hundred people a week came and one
signed up. The order is now reversed: the first few answers of the day are
shown in full, and the email is asked for once they are used.

How a reader is told apart
--------------------------
The same way the visit counter does (app/visits.py): address, browser
description and the day, turned into a one-way code with the daily secret.
Nothing is stored on the reader's machine and no address is kept. The code
changes with the day, so the allowance is per day, and a restart forgets the
day's counts (a reader then gets the allowance again — the cheaper failure).

What counts
-----------
A full stock answer streamed to the front page (app/us_ask_api.py) by a
reader with no account, when accounts are switched on. Not counted: anyone
signed in, a click on one of the page's own examples (via=example), an answer
that failed, and every server where accounts are off (the whole answer shows
there, as before). The plain /api/v1/us/ask route counts too, but only when
the response cache did not answer for it; the page uses the stream. A
question typed into a box on a data page (web/assets/page-ask.js) counts
whatever answers it: an economy answer there is counted by POST
/api/v1/us/ask/free, since its route is cached. Once the allowance is used,
that box asks the reader to sign up, and Your Plover answers the question.

The page decides
----------------
The server only counts and reports; it never refuses an answer. The front
page reads the count and shows the start of the answer once the allowance is
used, as it did before for every signed-out question. The answer routes stay
public by URL, as they were.

Settings
--------
FREE_ANSWERS (default 1, from 2026-11-03; 3 before): full answers a day before the email is asked for.
0 switches the allowance off and the page gates every question as before.
"""
import datetime
import os
import threading

from . import accounts, visits

DEFAULT = 1
_lock = threading.Lock()
_counts = {}          # (day, visitor code) -> answers taken
_day = [None]         # the day the counts are for; another day empties them


def limit():
    try:
        n = int(str(os.environ.get("FREE_ANSWERS", "")).strip() or DEFAULT)
    except ValueError:
        n = DEFAULT
    return max(0, n)


def _code(request):
    """The reader's daily code, as the visit counter would compute it."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return day, visits._visitor(ip, request.headers.get("user-agent", ""), day)


def reader(request, via=""):
    """Who is asking, for the counter: None when the answer is not counted
    (accounts off, or signed in), else the reader's (day, code) and whether
    this answer is to be taken from the allowance."""
    if not accounts.enabled() or accounts.current_account(request) is not None:
        return None
    return {"key": _code(request), "take": via != "example"}


def _taken(key):
    if _day[0] != key[0]:
        _counts.clear()
        _day[0] = key[0]
    return _counts.get(key, 0)


def status(who):
    """{"free": n, "left": k} for a counted reader, else None (no limit)."""
    if who is None:
        return None
    n = limit()
    with _lock:
        taken = _taken(who["key"])
    return {"free": n, "left": max(0, n - taken)}


def take(who):
    """Count one answer given, when it counts, and say what is left."""
    if who is None:
        return None
    n = limit()
    with _lock:
        taken = _taken(who["key"])
        if who["take"] and taken < n:
            taken += 1
            _counts[who["key"]] = taken
    return {"free": n, "left": max(0, n - taken)}
