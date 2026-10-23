# -*- coding: utf-8 -*-
"""S&P 500 press releases (app/us_press.py, app/us_press_api.py).

What is proved, with no network: RSS and Atom feeds are read; the figures in
a buyback announcement are read with the sentence each came from, and a
dollar amount that is not a programme is ignored; a poll keeps new releases
once (a kept release is never rewritten), reads and archives only the
capital-return ones, and records the poll; the API serves a company's
releases and figures and refuses a company it does not watch; the health
entry goes stale when no poll has finished.
"""
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import us_press, us_press_api

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>
<item><title>NVIDIA Announces a $150 Billion Share Repurchase Authorization Increase</title>
<link>https://news.example.com/buyback</link><pubDate>Mon, 28 Sep 2026 20:05:00 GMT</pubDate></item>
<item><title>NVIDIA RTX Spark PCs Available Today</title>
<link>https://news.example.com/rtx</link><pubDate>Fri, 16 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>NVIDIA Announces Financial Results for Second Quarter</title>
<link>https://news.example.com/q2</link><pubDate>Wed, 26 Aug 2026 20:20:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>
<entry><title>Declares Quarterly Dividend</title><link href="https://ir.example.com/d"/>
<updated>2026-10-01T09:00:00Z</updated><id>d</id></entry></feed>"""

RELEASE = b"""<html><head><script>var x=1;</script></head><body><nav><p>Menu</p></nav>
<article><h2>NVIDIA Announces Repurchase Increase</h2>
<p>SANTA CLARA, Calif., Sept. 28, 2026 -- NVIDIA today announced that its board of directors approved an
additional $150 billion share repurchase authorization, without expiration.</p>
<p>Including this, NVIDIA has approximately $235 billion remaining under its share repurchase authorization.
The company paid $5 to a vendor.</p></article></body></html>"""

PAGES = {"https://feeds.example.com/nvda.xml": RSS, "https://news.example.com/buyback": RELEASE}


def fake_fetch(url, timeout=None, limit=None):
    body = PAGES.get(url)
    return (url, 200, "text/xml", body) if body is not None else (url, 404, "", b"")


class Reading(unittest.TestCase):
    def test_rss_and_atom(self):
        items = us_press.parse_feed(RSS)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["published"], "2026-09-28")
        atom = us_press.parse_feed(ATOM)
        self.assertEqual(atom[0]["link"], "https://ir.example.com/d")
        self.assertEqual(atom[0]["published"], "2026-10-01")
        self.assertIsNone(us_press.parse_feed(b"<html><body>no feed</body></html>"))

    def test_buyback_figures_come_with_their_sentence(self):
        facts = us_press.read_facts(us_press.page_text(RELEASE))
        got = dict((f["kind"], f) for f in facts)
        self.assertEqual(got["authorization_added"]["value"], 150e9)
        self.assertIn("additional $150 billion", got["authorization_added"]["sentence"])
        self.assertEqual(got["authorization_remaining"]["value"], 235e9)
        self.assertNotIn("Menu", " ".join(f["sentence"] for f in facts))
        self.assertTrue(all(f["value"] >= 1e6 for f in facts if f["unit"] == "USD"))

    def test_dividend_per_share(self):
        facts = us_press.read_facts("The board declared a quarterly cash dividend of $0.25 per share, payable "
                                    "on October 1.")
        self.assertEqual([(f["kind"], f["value"]) for f in facts], [("dividend_per_share", 0.25)])

    def test_capital_return_titles(self):
        self.assertTrue(us_press.CAPITAL_RETURN.search("Announces $10 Billion Share Repurchase Program"))
        self.assertTrue(us_press.CAPITAL_RETURN.search("Declares Quarterly Dividend"))
        self.assertFalse(us_press.CAPITAL_RETURN.search("RTX Spark PCs Available Today"))


class FiledReleases(unittest.TestCase):
    """The 8-K route: exhibit rows on a filing's index page, and headlines."""

    INDEX = """<table class="tableFile"><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
    <tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/1045810/000104581026000073/nvda-20260826.htm">nvda-20260826.htm</a></td><td>8-K</td></tr>
    <tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm">q2fy27pr.htm</a></td><td>EX-99.1</td></tr>
    <tr><td>3</td><td></td><td><a href="/ix?doc=/Archives/edgar/data/1/2/ex992.htm">ex992.htm</a></td><td>EX-99.2</td></tr>
    </table>"""

    def test_exhibit_rows_only_and_viewer_links_unwrapped(self):
        got = us_press._exhibit_links(self.INDEX)
        self.assertEqual(got, [("/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm", "EX-99.1"),
                               ("/Archives/edgar/data/1/2/ex992.htm", "EX-99.2")])

    def test_headlines(self):
        t = us_press._release_title
        self.assertEqual(t("EX-99.1 Document NVIDIA Announces Financial Results for Second Quarter Fiscal 2027 "
                           "\u2022 Revenue of $96.2 billion"),
                         "NVIDIA Announces Financial Results for Second Quarter Fiscal 2027")
        self.assertTrue(t("JPMORGAN CHASE & CO. JPMorgan Chase Reports Third-Quarter 2026 Net Income of $15.2 "
                          "billion. More.").startswith("JPMorgan Chase Reports"))


class _Gated(unittest.TestCase):
    """Every SEC request goes through app/sec_gate.py: its state in a temp
    folder, its network replaced, no pacing to wait for."""

    def setUp(self):
        from app import sec_gate
        self.gate = sec_gate
        self.tmp_gate = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_gate.cleanup)
        for p in (mock.patch.object(sec_gate, "STATE_DIR", pathlib.Path(self.tmp_gate.name)),
                  mock.patch.dict(os.environ, {"SEC_RATE": "1000"})):
            p.start()
            self.addCleanup(p.stop)
        us_press._SEC_STATE.update(refused=False)
        self.addCleanup(lambda: us_press._SEC_STATE.update(refused=False))

    def opener(self, fn):
        p = mock.patch.object(self.gate, "_urlopen", fn)
        p.start()
        self.addCleanup(p.stop)


class _Resp(object):
    def __init__(self, body=b"{}"):
        self.body, self.status, self.headers = body, 200, {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return self.body


class SecPoliteness(_Gated):
    """The SEC refuses a fast reader with an HTML 403 page: the whole host
    stops asking; a filing whose exhibits cannot all be read is not kept
    (tried next poll)."""

    def test_a_refusal_stops_this_run_and_holds_the_host(self):
        import io
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"<html>Undeclared</html>"))
            return _Resp(b"{}")
        self.opener(fake_urlopen)
        self.assertEqual(us_press._sec_get("https://data.sec.gov/x", "UA a@b.c"), (0, b""))
        self.assertTrue(us_press.sec_refused())
        us_press._SEC_STATE.update(refused=False)                  # a new run, in this or another process
        self.assertEqual(us_press._sec_get("https://data.sec.gov/y", "UA a@b.c"), (0, b""))
        self.assertEqual(len(calls), 1)                            # held: the SEC was not asked again

    def test_a_missing_daily_index_is_not_retried(self):
        import io
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"\x1f\x8bAccessDenied"))
        self.opener(fake_urlopen)
        self.assertEqual(us_press._sec_get("https://www.sec.gov/idx", "UA a@b.c")[0], 403)
        self.assertEqual(len(calls), 1)

    def test_a_partly_read_filing_is_not_kept(self):
        index = FiledReleases.INDEX
        with mock.patch.object(us_press, "_sec_get",
                               lambda url, ua: (200, index.encode()) if url.endswith("-index.htm") else (0, b"")), \
                mock.patch.object(us_press.time, "sleep", lambda s: None):
            self.assertIsNone(us_press._sec_exhibits({"cik": 1}, {"accession": "0001-26-000001"}, "UA"))


class Poll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        feeds = {"1045810": {"cik": 1045810, "ticker": "NVDA", "name": "NVIDIA CORP",
                             "feed": "https://feeds.example.com/nvda.xml", "how": "found"},
                 "1": {"cik": 1, "ticker": "NOPE", "name": "No Feed", "feed": None, "how": "not found"}}
        (d / "feeds.json").write_text(json.dumps(feeds))
        self.patches = [mock.patch.object(us_press, "FEEDS_PATH", d / "feeds.json"),
                        mock.patch.object(us_press, "DB_PATH", d / "press.sqlite"),
                        mock.patch.object(us_press, "RAW_ROOT", d / "raw"),
                        mock.patch.object(us_press, "ROOT", d),
                        mock.patch.object(us_press, "fetch", fake_fetch),
                        # never reach the SEC from a test, whatever EDGAR_USER_AGENT the shell holds
                        mock.patch.object(us_press, "_edgar_ua", lambda: "")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_health_is_stale_before_any_poll(self):
        h = us_press.health()[0]
        self.assertEqual(h["dataset"], "us-press-releases")
        self.assertTrue(h["stale"])
        self.assertIn("1 of 2 S&P 500 members have a news feed", h["notes"][0])
        self.assertEqual(h["shape_flags"], [])

    def test_poll_keeps_releases_once_and_reads_capital_return(self):
        out = us_press.poll(log=lambda *a: None)
        self.assertEqual((out["ok"], out["new_releases"], out["read_releases"]), (1, 3, 1))
        again = us_press.poll(log=lambda *a: None)
        self.assertEqual((again["new_releases"], again["read_releases"]), (0, 0))
        c = us_press.company("NVDA")
        self.assertEqual(len(c["releases"]), 3)
        self.assertEqual(c["releases"][0]["title"], "NVIDIA RTX Spark PCs Available Today")
        kinds = [f["kind"] for f in c["capital_return"]]
        self.assertIn("authorization_added", kinds)
        latest = us_press.latest_capital_return("nvda")
        self.assertEqual(latest["release"]["published"], "2026-09-28")
        self.assertTrue(latest["release"]["sha256"])
        archived = list((pathlib.Path(self.tmp.name) / "raw").rglob("*.html.gz"))
        self.assertEqual(len(archived), 1)
        self.assertFalse(us_press.health()[0]["stale"])

    def test_api(self):
        us_press.poll(log=lambda *a: None)
        app = FastAPI()
        app.include_router(us_press_api.router)
        c = TestClient(app)
        got = c.get("/api/v1/us/press/NVDA")
        self.assertEqual(got.status_code, 200)
        body = got.json()
        self.assertEqual(body["provenance"]["trust"], "company")
        self.assertTrue(any(f["value"] == 235e9 for f in body["capital_return"]))
        self.assertEqual(c.get("/api/v1/us/press/ZZZZ").status_code, 404)
        self.assertEqual(c.get("/api/v1/us/press").json()["with_feed"], 1)

    def test_a_failed_poll_shows_in_health(self):
        with mock.patch.object(us_press, "STATUS_PATH", pathlib.Path(self.tmp.name) / "status.json"), \
                mock.patch.object(us_press, "poll", side_effect=RuntimeError("feeds file unreadable")), \
                mock.patch.dict(os.environ, {"US_PRESS_POLL": "1"}):
            us_press._last_poll["at"] = 0
            with self.assertRaises(RuntimeError):
                us_press.poll_if_due()
            h = us_press.health()[0]
        self.assertTrue(any("last poll failed" in f and "feeds file unreadable" in f for f in h["notes"]), h)
        self.assertEqual(h["status"], "attention")

    def test_poll_if_due_waits_for_its_interval(self):
        with mock.patch.dict(os.environ, {"US_PRESS_POLL": "1"}), \
                mock.patch.object(us_press, "STATUS_PATH", pathlib.Path(self.tmp.name) / "status.json"):
            us_press._last_poll["at"] = 0
            self.assertIsNotNone(us_press.poll_if_due())
            self.assertIsNone(us_press.poll_if_due())
        with mock.patch.dict(os.environ, {"US_PRESS_POLL": "0"}):
            us_press._last_poll["at"] = 0
            self.assertIsNone(us_press.poll_if_due())


if __name__ == "__main__":
    unittest.main()


class SecPacing(_Gated):
    """Every SEC request goes through one gate; a refusal stops the run (2026-10-19 block)."""

    def test_requests_from_many_threads_stay_under_the_rate(self):
        import threading
        times = []

        def ok(req, timeout=None):
            times.append(time.time())
            return _Resp()
        self.opener(ok)
        with mock.patch.dict(os.environ, {"SEC_RATE": "20"}):
            ts = [threading.Thread(target=lambda: [us_press._sec_get("https://data.sec.gov/x", "ua") for _ in range(5)])
                  for _ in range(4)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        times.sort()
        self.assertEqual(len(times), 20)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 1.0 / 20 - 0.01)        # never two requests closer than the gate allows

    def test_refused_stops_asking(self):
        import io
        import urllib.error
        calls = []

        def refuse(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b"<HTML><TITLE>Access Denied</TITLE></HTML>"))
        self.opener(refuse)
        self.assertEqual(us_press._sec_get("https://data.sec.gov/a", "ua"), (0, b""))
        self.assertTrue(us_press.sec_refused())
        self.assertEqual(us_press._sec_get("https://data.sec.gov/b", "ua"), (0, b""))
        self.assertEqual(len(calls), 1)                              # the second address was never asked

    def test_a_missing_daily_index_is_not_a_refusal(self):
        import io
        import urllib.error

        def missing(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"NoSuchKey"))
        self.opener(missing)
        self.assertEqual(us_press._sec_get("https://www.sec.gov/idx", "ua"), (403, b""))
        self.assertFalse(us_press.sec_refused())
        self.assertFalse(self.gate.held())
