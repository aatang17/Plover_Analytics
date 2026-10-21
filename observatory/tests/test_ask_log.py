# -*- coding: utf-8 -*-
"""The Ask Box log (app/ask_log.py): every question the box is asked is
written once, with how it was answered and nothing about who asked; a
repeat served from the cache keeps its first answer's note; the summary
counts searches, people and questions for the admin Traffic page."""
import datetime
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from app import ask_log, visits


def _scope(path, q, client=("203.0.113.9", 4000), ua=b"Mozilla/5.0 (Macintosh)", **extra):
    from urllib.parse import urlencode
    return {"type": "http", "method": "GET", "path": path, "query_string": urlencode(dict({"q": q}, **extra)).encode("latin-1"),
            "client": client, "headers": [(b"user-agent", ua), (b"host", b"ploveranalytics.com")]}


class LogTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(ask_log, "ANALYTICS_DIR", self.dir)
        self.patch.start()
        ask_log._NOTES.clear()

    def tearDown(self):
        self.patch.stop()
        ask_log._NOTES.clear()

    def lines(self):
        out = []
        for p in sorted(self.dir.glob("ask-*.jsonl")):
            out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return out

    def test_a_question_is_written_with_its_answer_and_nothing_about_who_asked(self):
        ask_log.note("/api/v1/us/ask/stream", "How many months has Microsoft outperformed S&P in 2026", True, "steps", "steps")
        ask_log.record(_scope("/api/v1/us/ask/stream", "How many months has Microsoft  outperformed S&P in 2026"), 200, 14.26)
        (line,) = self.lines()
        self.assertEqual(line["q"], "How many months has Microsoft outperformed S&P in 2026")
        self.assertEqual((line["where"], line["answered"], line["route"], line["seconds"]), ("full", True, "steps", 14.3))
        for k in ("visitor", "country", "network", "ip", "client"):
            self.assertNotIn(k, line)

    def test_a_cached_repeat_keeps_the_first_note(self):
        ask_log.note("/api/v1/assistant/preview", "who owns apple", True, "stock")
        for _ in range(3):
            ask_log.record(_scope("/api/v1/assistant/preview", "Who owns Apple"), 200, 0.01)
        self.assertEqual([(x["answered"], x["route"], x["where"]) for x in self.lines()], [(True, "stock", "preview")] * 3)

    def test_without_a_note_only_the_status_of_the_full_answer_is_read(self):
        ask_log.record(_scope("/api/v1/us/ask", "which companies have the most employees"), 422)
        ask_log.record(_scope("/api/v1/us/ask/stream", "a stream that never noted"), 200)
        self.assertEqual([x["answered"] for x in self.lines()], [False, None])

    def test_other_paths_and_the_test_suite_are_not_written(self):
        ask_log.record(_scope("/api/v1/us/ask/recipes", "x y"), 200)
        ask_log.record(_scope("/api/v1/us/ask", "from the test suite", client=("testclient", 50000)), 200)
        ask_log.record(_scope("/api/v1/us/ask", ""), 200)
        self.assertEqual(self.lines(), [])

    def test_the_visit_counter_writes_it_and_leaves_robots_out(self):
        real = visits.ANALYTICS_DIR, visits.SALT_PATH
        visits.ANALYTICS_DIR, visits.SALT_PATH = self.dir, self.dir / "salt"
        try:
            visits.observe(_scope("/api/v1/us/ask", "what is apple worth"), 200, 2.0)
            visits.observe(_scope("/api/v1/us/ask", "what is nvidia worth", ua=b"Googlebot/2.1"), 200, 2.0)
            visits.flush()
        finally:
            visits.ANALYTICS_DIR, visits.SALT_PATH = real
        self.assertEqual([x["q"] for x in self.lines()], ["what is apple worth"])

    def test_summary(self):
        day = datetime.date(2026, 10, 21)
        at = lambda h: datetime.datetime(2026, 10, 21, h, 0, 0)
        ask_log.note("/api/v1/us/ask", "what is apple worth", True, "reader", "company_value")
        ask_log.record(_scope("/api/v1/us/ask", "what is apple worth"), 200, 2.0, now=at(1))
        ask_log.record(_scope("/api/v1/us/ask", "What is Apple worth"), 200, 4.0, now=at(2))
        ask_log.record(_scope("/api/v1/us/ask", "most employees"), 422, 6.0, now=at(3))
        ask_log.record(_scope("/api/v1/us/ask", "an old one"), 200, 1.0, now=datetime.datetime(2026, 9, 1))
        hits = [("2026-10-21T01:00:00Z", "a"), ("2026-10-21T02:00:00Z", "b"), ("2026-10-21T03:00:00Z", "a"),
                ("2026-10-20T09:00:00Z", "c")]       # before the first logged question: not counted
        s = ask_log.summary(7, day - datetime.timedelta(days=6), day, hits)
        self.assertEqual((s["searches"], s["people"], s["answered"], s["judged"], s["distinct_questions"]), (3, 2, 2, 3, 2))
        self.assertEqual(s["median_seconds"], 4.0)
        self.assertEqual(s["daily"][-1], {"day": "2026-10-21", "searches": 3, "answered": 2, "not_answered": 1, "people": 2})
        self.assertEqual(len(s["daily"]), 7, "every day in the window, a quiet day as a zero row")
        self.assertEqual([(r["q"], r["times"], r["answered"]) for r in s["most_asked"]], [("What is Apple worth", 2, 2)])
        self.assertEqual([r["q"] for r in s["recent"]], ["most employees", "What is Apple worth", "what is apple worth"])

    def test_each_line_says_whether_a_reader_typed_it_clicked_an_example_or_we_asked(self):
        real = visits.ANALYTICS_DIR, visits.SALT_PATH
        visits.ANALYTICS_DIR, visits.SALT_PATH = self.dir, self.dir / "salt"
        try:
            with mock.patch.dict("os.environ", {"OUR_ADDRESSES": "198.51.100.0/24, not-an-address"}):
                visits.observe(_scope("/api/v1/us/ask", "typed by a reader"), 200, 1.0)
                visits.observe(_scope("/api/v1/assistant/preview", "Who owns Nvidia?", via="example"), 200, 1.0)
                visits.observe(_scope("/api/v1/us/ask", "from our machine", client=("198.51.100.7", 1)), 200, 1.0)
                visits.observe(_scope("/api/v1/us/ask", "inside the server", client=("127.0.0.1", 1)), 200, 1.0)
                visits.observe(_scope("/api/v1/us/ask", "container network", client=("172.18.0.4", 1)), 200, 1.0)
                visits.observe(_scope("/api/v1/us/ask", "our example click", client=("198.51.100.7", 1), via="example"), 200, 1.0)
                visits.flush()
        finally:
            visits.ANALYTICS_DIR, visits.SALT_PATH = real
        self.assertEqual([(x["q"], x["from"]) for x in self.lines()],
                         [("typed by a reader", "reader"), ("Who owns Nvidia?", "example"), ("from our machine", "us"),
                          ("inside the server", "us"), ("container network", "us"), ("our example click", "us")])
        for line in self.lines():
            for k in ("visitor", "country", "network", "ip", "client", "via"):
                self.assertNotIn(k, line)

    def test_is_ours_without_the_setting(self):
        with mock.patch.dict("os.environ", {"OUR_ADDRESSES": ""}):
            self.assertEqual([ask_log.is_ours(x) for x in ("8.8.8.8", "203.0.113.9", "::1", "10.1.2.3", "unknown", "")],
                             [False, False, True, True, False, False])

    def test_weekly_counts_typed_questions_apart(self):
        day = datetime.date(2026, 10, 28)                  # a Wednesday
        def put(when, q, answered=True, **kw):
            ask_log.note("/api/v1/us/ask", q, answered)
            ask_log.record(_scope("/api/v1/us/ask", q, **kw), 200, 1.0, now=when)
        # last week: one unmarked line from before the marking began, one typed
        with open(str(self.dir / "ask-2026-10.jsonl"), "w") as f:
            f.write(json.dumps({"at": "2026-10-20T16:09:43Z", "q": "old", "where": "full", "answered": True}) + "\n")
        put(datetime.datetime(2026, 10, 22, 9), "nvidia margins")
        # this week: two typed (one not answered), one example click, one of ours
        put(datetime.datetime(2026, 10, 26, 1), "apple buyback 2026")
        put(datetime.datetime(2026, 10, 27, 1), "most employees", answered=False)
        put(datetime.datetime(2026, 10, 27, 2), "Apple buybacks", via="example")
        ask_log.note("/api/v1/us/ask", "our check", True)
        ask_log.record(_scope("/api/v1/us/ask", "our check"), 200, 1.0, now=datetime.datetime(2026, 10, 28, 3), ours=True)
        rows = ask_log.weekly(day)
        self.assertEqual([r["week"] for r in rows], ["2026-10-26", "2026-10-19"],
                         "weeks before the first logged question are left out, not zeros")
        self.assertEqual(rows[0], {"week": "2026-10-26", "partial": True, "reader": 2, "reader_answered": 1,
                                   "example": 1, "us": 1, "unmarked": 0})
        self.assertEqual(rows[1], {"week": "2026-10-19", "partial": False, "reader": 1, "reader_answered": 1,
                                   "example": 0, "us": 0, "unmarked": 1})
        s = ask_log.summary(7, day - datetime.timedelta(days=6), day)
        self.assertEqual(s["weekly"], rows)
        self.assertEqual([r["from"] for r in s["recent"]], ["us", "example", "reader", "reader", "reader"])



class EndpointNoteTest(unittest.TestCase):
    def setUp(self):
        ask_log._NOTES.clear()

    def test_the_answer_and_a_refusal_leave_notes(self):
        from fastapi import HTTPException
        from app import us_ask_api
        us_ask_api._noted("/api/v1/us/ask", "who owns apple",
                          lambda: {"headline": "Vanguard holds most.", "recipe": "company_holders", "match": {"via": "reader"}})
        self.assertEqual(ask_log._NOTES[("/api/v1/us/ask", "who owns apple")],
                         {"answered": True, "route": "reader", "shape": "company_holders"})

        def refuse():
            raise HTTPException(422, "not held")
        with self.assertRaises(HTTPException):
            us_ask_api._noted("/api/v1/us/ask", "most employees", refuse)
        self.assertFalse(ask_log._NOTES[("/api/v1/us/ask", "most employees")]["answered"])
        ask_log._NOTES.clear()


if __name__ == "__main__":
    unittest.main()
