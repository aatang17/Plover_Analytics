# -*- coding: utf-8 -*-
"""Free answers for a reader without an account (app/free_answers.py).

The allowance is per reader per day: three full answers (FREE_ANSWERS), then
the page gates. Signed in, or with accounts off, nothing is counted. A click
on the page's own examples is reported but not taken. The count is kept in
memory and read through /api/v1/us/ask/free, which must never be cached.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_free_answers
"""
import os
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import accounts, free_answers

UA = {"User-Agent": "Mozilla/5.0 (test)"}


def _app():
    app = FastAPI()

    @app.get("/status")
    def status(request: Request, via: str = ""):
        return free_answers.status(free_answers.reader(request, via)) or {"free": None, "left": None}

    @app.get("/take")
    def take(request: Request, via: str = ""):
        return free_answers.take(free_answers.reader(request, via)) or {"free": None, "left": None}

    return app


class FreeAnswers(unittest.TestCase):
    def setUp(self):
        self.env = {k: os.environ.get(k) for k in ("ACCOUNTS_ENABLED", "FREE_ANSWERS")}
        os.environ["ACCOUNTS_ENABLED"] = "1"
        os.environ.pop("FREE_ANSWERS", None)
        free_answers._counts.clear()
        free_answers._day[0] = None
        self.c = TestClient(_app())

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_three_then_none(self):
        self.assertEqual(self.c.get("/status", headers=UA).json(), {"free": 3, "left": 3})
        self.assertEqual(self.c.get("/take", headers=UA).json()["left"], 2)
        self.assertEqual(self.c.get("/take", headers=UA).json()["left"], 1)
        self.assertEqual(self.c.get("/take", headers=UA).json()["left"], 0)
        # a fourth is counted at zero, never below: the server reports, the page gates
        self.assertEqual(self.c.get("/take", headers=UA).json(), {"free": 3, "left": 0})
        self.assertEqual(self.c.get("/status", headers=UA).json()["left"], 0)

    def test_example_clicks_are_not_taken(self):
        self.assertEqual(self.c.get("/take?via=example", headers=UA).json()["left"], 3)
        self.assertEqual(self.c.get("/take", headers=UA).json()["left"], 2)

    def test_readers_are_told_apart(self):
        self.c.get("/take", headers=UA)
        other = dict(UA, **{"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(self.c.get("/status", headers=other).json()["left"], 3)
        # and the first hop of X-Forwarded-For is the reader, as for the visit counter
        self.assertEqual(self.c.get("/status", headers=dict(other, **{"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})).json()["left"], 3)
        self.c.get("/take", headers=other)
        self.assertEqual(self.c.get("/status", headers=other).json()["left"], 2)
        self.assertEqual(self.c.get("/status", headers=UA).json()["left"], 2)

    def test_signed_in_and_accounts_off_are_not_counted(self):
        os.environ["ACCOUNTS_ENABLED"] = "0"
        self.assertEqual(self.c.get("/take", headers=UA).json(), {"free": None, "left": None})
        os.environ["ACCOUNTS_ENABLED"] = "1"
        real = accounts.current_account
        accounts.current_account = lambda request: {"id": 1, "email": "reader@example.com"}
        try:
            self.assertEqual(self.c.get("/take", headers=UA).json(), {"free": None, "left": None})
        finally:
            accounts.current_account = real
        self.assertEqual(self.c.get("/status", headers=UA).json()["left"], 3)

    def test_setting_and_off(self):
        os.environ["FREE_ANSWERS"] = "1"
        self.assertEqual(self.c.get("/take", headers=UA).json(), {"free": 1, "left": 0})
        os.environ["FREE_ANSWERS"] = "0"
        self.assertEqual(self.c.get("/status", headers=UA).json(), {"free": 0, "left": 0})
        os.environ["FREE_ANSWERS"] = "lots"
        self.assertEqual(self.c.get("/status", headers=UA).json()["free"], 3)

    def test_a_new_day_starts_clean(self):
        self.c.get("/take", headers=UA)
        self.assertEqual(self.c.get("/status", headers=UA).json()["left"], 2)
        free_answers._day[0] = "1999-12-31"           # the counts are from another day
        for key in list(free_answers._counts):
            free_answers._counts[("1999-12-31", key[1])] = free_answers._counts.pop(key)
        self.assertEqual(self.c.get("/status", headers=UA).json()["left"], 3)
        self.assertFalse(any(k[0] == "1999-12-31" for k in free_answers._counts))


if __name__ == "__main__":
    unittest.main()
