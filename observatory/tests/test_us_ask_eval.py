# -*- coding: utf-8 -*-
"""The ask box against real questions (tests/us_ask_eval.json).

Each entry is a question people type and what a good answer must contain:
  route         a fixed shape's id, "steps", or "refuse" (a 422 with a reason)
  must_say      words the headline or answer must carry (case-insensitive)
  must_not_say  words it must not
  pct_range     [lo, hi] for every percentage in the headline
  min_rows      the table must have at least this many rows
  known_miss    true for a question the box is known to get wrong today;
                it runs and is reported, but does not fail the run
A miss found in use goes in here as a new entry before it is fixed, so the
set only grows and a change that fixes one question and breaks three fails.

Opt-in, because it asks the real model: US_ASK_EVAL=1 and data/sec.duckdb
with prices and index members (SEC_DB_PATH points it at a copy). As a
script it prints one line per question and the score:

    US_ASK_EVAL=1 ./.venv/bin/python -m tests.test_us_ask_eval
"""
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
EVAL_FILE = HERE / "us_ask_eval.json"

_PCT = re.compile(r"(−|-|\+)?(\d[\d,]*(?:\.\d+)?)\s*%")


def entries():
    with open(str(EVAL_FILE), encoding="utf-8") as f:
        return json.load(f)


def judge(entry, status, body):
    """[reasons the answer fails the entry]; empty when it passes."""
    fails = []
    # everything the reader sees in words: the notes under the answer included
    text = " ".join([str(body.get(k) or "") for k in ("headline", "answer", "detail")] + [str(n) for n in body.get("notes") or []])
    low = text.lower()
    route = entry.get("route")
    if route == "refuse":
        if status != 422:
            fails.append("expected a refusal, got %s: %s" % (status, (body.get("headline") or "")[:80]))
    elif status != 200:
        fails.append("HTTP %s: %s" % (status, (body.get("detail") or "")[:120]))
    elif route and body.get("recipe") != route:
        fails.append("route %s, expected %s" % (body.get("recipe"), route))
    for w in entry.get("must_say") or []:
        if w.lower() not in low:
            fails.append("missing %r" % w)
    for w in entry.get("must_not_say") or []:
        if w.lower() in low:
            fails.append("says %r" % w)
    rng = entry.get("pct_range")
    if rng and status == 200:
        for sign, num in _PCT.findall(body.get("headline") or ""):
            v = float(num.replace(",", "")) * (-1 if sign in ("−", "-") else 1)
            if not rng[0] <= v <= rng[1]:
                fails.append("%s%% outside %s" % (num, rng))
    if entry.get("min_rows") and status == 200:
        n = len(((body.get("table") or {}).get("rows")) or [])
        if n < entry["min_rows"] and not (entry["min_rows"] == 1 and body.get("headline")):
            fails.append("%d table rows, wanted %d" % (n, entry["min_rows"]))
    return fails


def run_all(client, verbose=False):
    """[(entry, fails, headline)] over the whole set."""
    out = []
    for e in entries():
        r = client.get("/api/v1/us/ask", params={"q": e["question"]})
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text[:200]}
        fails = judge(e, r.status_code, body)
        head = body.get("headline") or body.get("detail") or ""
        out.append((e, fails, head))
        if verbose:
            mark = "ok  " if not fails else ("miss" if e.get("known_miss") else "FAIL")
            print("%s %-70s %s" % (mark, e["question"][:70], (head or "")[:90]))
            for f in fails:
                print("       - %s" % f)
    return out


def score(results):
    passed = sum(1 for _, fails, _ in results if not fails)
    return passed, len(results)


def _ready():
    if os.environ.get("US_ASK_EVAL", "") not in ("1", "true", "yes"):
        return False
    try:
        from app import sec_api, us_steps
        cur = sec_api._cur()
        return us_steps.enabled() and all(sec_api._has(cur, t) for t in ("us_px_daily", "us_index_members", "sec_companies"))
    except Exception:                                             # noqa: BLE001
        return False


def load_tests(loader, tests, pattern):
    """Opt-in: collected only with US_ASK_EVAL=1, as it asks the real model.
    Not a skip, because a skip must be for want of data (tests/_data.py)."""
    if os.environ.get("US_ASK_EVAL", "") not in ("1", "true", "yes"):
        return unittest.TestSuite()
    return tests


@unittest.skipUnless(_ready(), "US_ASK_EVAL=1 with the model on and data/sec.duckdb holding prices and index members")
class EvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import us_steps
        from app.main import app
        us_steps.LOG_FILE = os.path.join(tempfile.mkdtemp(), "us_ask_log.jsonl")   # never the real log
        cls.c = TestClient(app)

    def test_every_question_not_marked_as_a_known_miss_passes(self):
        results = run_all(self.c, verbose=True)
        p, n = score(results)
        print("\nscore %d of %d" % (p, n))
        bad = ["%s: %s" % (e["question"], "; ".join(f)) for e, f, _ in results if f and not e.get("known_miss")]
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    os.environ.setdefault("US_ASK_EVAL", "1")
    if not _ready():
        print("not ready: needs the model on and a database with prices and index members")
        sys.exit(2)
    from fastapi.testclient import TestClient
    from app import us_steps
    from app.main import app
    us_steps.LOG_FILE = os.path.join(tempfile.mkdtemp(), "us_ask_log.jsonl")
    res = run_all(TestClient(app), verbose=True)
    p, n = score(res)
    print("\nscore %d of %d (%d known misses)" % (p, n, sum(1 for e, f, _ in res if f and e.get("known_miss"))))
