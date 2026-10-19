# -*- coding: utf-8 -*-
"""What the stock box is doing right now, told to the reader while it works.

The answer code calls say("Reading Nvidia's revenue split ...") at each real
step: the question read, the company found, the steps written, a check that
sent it back. The streaming endpoint (GET /api/v1/us/ask/stream) passes each
line to the page as it happens, so a slow answer shows what it is waiting
on instead of one fixed "working" line. Outside a stream say() does nothing,
and the plain endpoint answers exactly as before.

A listener belongs to the thread running one question, so two readers'
questions never see each other's lines.
"""
import threading

_LOCAL = threading.local()


def listen(fn):
    """Send this thread's say() lines to fn(text); None stops."""
    _LOCAL.fn = fn


def say(text):
    fn = getattr(_LOCAL, "fn", None)
    if fn and text:
        try:
            fn(str(text))
        except Exception:                                          # noqa: BLE001 — a closed page must not stop the answer
            pass
