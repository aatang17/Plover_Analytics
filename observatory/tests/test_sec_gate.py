# -*- coding: utf-8 -*-
"""The host-wide SEC gate (app/sec_gate.py) and what reads it.

What is proved, with no network: two processes asking at once stay under
SEC_RATE between them; a refusal (the HTML 403 page) holds every caller,
in this process and another, without asking the SEC, except one probe a
window, and an answer to the probe lifts the hold; a missing daily index
(a short non-HTML 403) is not a refusal; SEC_VIA carries SEC hosts only;
the SEC jobs stop at once when held; /catalog/health marks the SEC rows
while the refusal is on record.
"""
import importlib.util
import io
import json
import multiprocessing
import os
import pathlib
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from app import sec_gate

ROOT = pathlib.Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Plover Analytics test@example.com"}
REFUSAL = (b"<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access Denied</H1>"
           b"You don't have permission to access this server.<P>"
           b"Reference&#32;&#35;18&#46;6f2c1302&#46;1760889000&#46;2b3c4d5e</BODY></HTML>")


class _Resp(object):
    def __init__(self, body=b"{}"):
        self.body, self.status, self.headers = body, 200, {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return self.body


def _refuse(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(REFUSAL))


def _child_requests(state_dir, out_path, n, rate):
    """In another process: n gated requests, the time of each written to out_path."""
    os.environ["SEC_RATE"] = str(rate)
    sec_gate.STATE_DIR = pathlib.Path(state_dir)
    times = []

    def ok(req, timeout=None):
        times.append(time.time())
        return _Resp()
    sec_gate._urlopen = ok
    for _ in range(n):
        sec_gate.get("https://data.sec.gov/x", headers=UA)
    with open(out_path, "w") as f:
        json.dump(times, f)


def _child_one_get(state_dir, out_path):
    """In another process: one request; writes (status, whether the network was reached)."""
    sec_gate.STATE_DIR = pathlib.Path(state_dir)
    reached = []

    def ok(req, timeout=None):
        reached.append(1)
        return _Resp()
    sec_gate._urlopen = ok
    status, _, _ = sec_gate.get("https://www.sec.gov/y", headers=UA)
    with open(out_path, "w") as f:
        json.dump([status, bool(reached)], f)


class _Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        for p in (mock.patch.object(sec_gate, "STATE_DIR", self.dir / "gate"),
                  mock.patch.dict(os.environ, {"SEC_RATE": "1000", "SEC_REFUSED_HOLD_MINUTES": "60"})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("SEC_VIA", None)
        sec_gate._last_ok_written[0] = 0.0

    def opener(self, fn):
        p = mock.patch.object(sec_gate, "_urlopen", fn)
        p.start()
        self.addCleanup(p.stop)

    def spawn(self, target, *args):
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=target, args=a) for a in args]
        [p.start() for p in procs]
        [p.join(60) for p in procs]
        for p in procs:
            self.assertEqual(p.exitcode, 0)


class Rate(_Gate):
    def test_two_processes_stay_under_the_rate_between_them(self):
        rate, n = 10, 6
        outs = [str(self.dir / ("t%d.json" % i)) for i in range(2)]
        self.spawn(_child_requests, *[(str(self.dir / "gate"), o, n, rate) for o in outs])
        times = sorted(t for o in outs for t in json.load(open(o)))
        self.assertEqual(len(times), 2 * n)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 1.0 / rate - 0.01)     # never two requests closer than SEC_RATE allows

    def test_retries_are_requests(self):
        calls = []

        def busy(req, timeout=None):
            calls.append(time.time())
            raise urllib.error.HTTPError(req.full_url, 503, "Busy", {}, io.BytesIO(b""))
        self.opener(busy)
        with mock.patch.dict(os.environ, {"SEC_RATE": "20"}):
            for _ in range(4):
                self.assertEqual(sec_gate.get("https://data.sec.gov/x", headers=UA)[0], 503)
        self.assertGreaterEqual(min(b - a for a, b in zip(calls, calls[1:])), 1.0 / 20 - 0.01)


class Hold(_Gate):
    def test_a_refusal_holds_every_caller_without_asking(self):
        self.opener(_refuse)
        self.assertEqual(sec_gate.get("https://data.sec.gov/a", headers=UA)[0], 403)
        st = sec_gate.status()
        self.assertEqual(st["refused"]["status"], 403)
        self.assertEqual(st["refused"]["reference"], "18.6f2c1302.1760889000.2b3c4d5e")
        self.assertTrue(st["held"])
        calls = []
        self.opener(lambda req, timeout=None: calls.append(1) or _Resp())
        self.assertEqual(sec_gate.get("https://www.sec.gov/b", headers=UA), (sec_gate.HELD, b"", {}))
        self.assertEqual(calls, [])
        out = str(self.dir / "child.json")
        self.spawn(_child_one_get, (str(self.dir / "gate"), out))
        self.assertEqual(json.load(open(out)), [sec_gate.HELD, False])     # another process is held too

    def test_one_probe_a_window_and_an_answer_lifts_the_hold(self):
        self.opener(_refuse)
        sec_gate.get("https://data.sec.gov/a", headers=UA)
        marker = self.dir / "gate" / "refused.json"
        rec = json.loads(marker.read_text())
        rec["at"] = time.time() - 0.6 * 3600             # past half the window: one probe may go
        marker.write_text(json.dumps(rec))
        calls = []

        def down(req, timeout=None):
            calls.append(1)
            raise OSError("timed out")
        self.opener(down)
        self.assertEqual(sec_gate.get("https://data.sec.gov/b", headers=UA)[0], 0)     # the probe
        self.assertEqual(sec_gate.get("https://data.sec.gov/c", headers=UA)[0], sec_gate.HELD)
        self.assertEqual(len(calls), 1)                   # one probe, not two
        rec = json.loads(marker.read_text())
        rec["at"] = time.time() - 0.6 * 3600
        rec["probe_at"] = None                            # a new window
        marker.write_text(json.dumps(rec))
        self.opener(lambda req, timeout=None: _Resp(b"ok"))
        self.assertEqual(sec_gate.get("https://data.sec.gov/d", headers=UA)[:2], (200, b"ok"))
        self.assertFalse(marker.exists())
        st = sec_gate.status()
        self.assertIsNone(st["refused"])
        self.assertIsNotNone(st["last_ok_at"])
        self.assertEqual(sec_gate.get("https://data.sec.gov/e", headers=UA)[0], 200)

    def test_a_refused_probe_starts_a_new_window(self):
        self.opener(_refuse)
        sec_gate.get("https://data.sec.gov/a", headers=UA)
        marker = self.dir / "gate" / "refused.json"
        rec = json.loads(marker.read_text())
        since = rec["since"]
        rec["at"] = time.time() - 0.6 * 3600
        marker.write_text(json.dumps(rec))
        self.assertEqual(sec_gate.get("https://data.sec.gov/b", headers=UA)[0], 403)
        rec = json.loads(marker.read_text())
        self.assertEqual(rec["since"], since)             # the block is dated from its first refusal
        self.assertLess(time.time() - rec["at"], 60)
        self.assertEqual(sec_gate.get("https://data.sec.gov/c", headers=UA)[0], sec_gate.HELD)

    def test_429_is_a_refusal(self):
        self.opener(lambda req, timeout=None: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b""))))
        sec_gate.get("https://data.sec.gov/a", headers=UA)
        self.assertTrue(sec_gate.held())

    def test_a_missing_daily_index_is_not_a_refusal(self):
        def missing(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"\x1f\x8bAccessDenied"))
        self.opener(missing)
        self.assertEqual(sec_gate.get("https://www.sec.gov/Archives/edgar/daily-index/x.idx", headers=UA)[0], 403)
        self.assertFalse(sec_gate.held())
        self.assertIsNone(sec_gate.status()["refused"])
        self.assertFalse(sec_gate.is_refusal(403, b"<?xml version='1.0'?><Error><Code>AccessDenied</Code></Error>"))
        self.assertTrue(sec_gate.is_refusal(403, REFUSAL))

    def test_other_hosts_are_never_gated(self):
        self.opener(_refuse)
        sec_gate.get("https://data.sec.gov/a", headers=UA)
        self.opener(lambda req, timeout=None: _Resp(b"fed"))
        self.assertEqual(sec_gate.get("https://www.federalreserve.gov/x")[:2], (200, b"fed"))


class Proxy(_Gate):
    def _proxies(self, url):
        return [h.proxies for h in sec_gate._opener(url).handlers if isinstance(h, urllib.request.ProxyHandler)]

    def test_the_proxy_carries_sec_hosts_only(self):
        with mock.patch.dict(os.environ, {"SEC_VIA": "http://10.0.0.5:3128"}):
            for url in ("https://www.sec.gov/a", "https://data.sec.gov/b", "https://efts.sec.gov/c", "http://sec.gov/d"):
                self.assertIn({"http": "http://10.0.0.5:3128", "https": "http://10.0.0.5:3128"}, self._proxies(url), url)
            # (urllib reads any *_proxy variable as a proxy for that scheme, so SEC_VIA shows up
            # in the default handler as a proxy for "sec://" addresses, which nothing uses)
            for url in ("https://www.federalreserve.gov/a", "https://notsec.gov/b", "https://sec.gov.example.com/c"):
                self.assertFalse(any("10.0.0.5" in str(p.get("http")) + str(p.get("https"))
                                     for p in self._proxies(url)), url)

    def test_no_proxy_when_unset(self):
        self.assertFalse(any("10.0.0.5" in str(p) for p in self._proxies("https://www.sec.gov/a")))

    def test_hosts(self):
        self.assertTrue(sec_gate.is_sec("https://www.sec.gov/x"))
        self.assertTrue(sec_gate.is_sec("https://data.sec.gov/x"))
        self.assertFalse(sec_gate.is_sec("https://evilsec.gov/x"))
        self.assertFalse(sec_gate.is_sec("https://www.sec.gov.attacker.com/x"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "equity" / ("%s.py" % name)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Jobs(_Gate):
    """Each SEC job gives up at once while the gate holds, without asking."""

    def setUp(self):
        super().setUp()
        self.opener(_refuse)
        sec_gate.get("https://data.sec.gov/a", headers=UA)
        self.calls = []
        self.opener(lambda req, timeout=None: self.calls.append(1) or _Resp())

    def tearDown(self):
        self.assertEqual(self.calls, [])

    def test_events(self):
        ev = _load("us_events")
        with mock.patch.dict(os.environ, {"EDGAR_USER_AGENT": UA["User-Agent"]}):
            with self.assertRaises(ev.Refused):
                ev.Client(str(self.dir / "raw"), pause=0).get(ev.EDGAR_SUBMISSIONS % 320193)

    def test_proxy_pay_fetcher(self):
        px = _load("sec_proxy_pay")
        with self.assertRaises(px.PullError) as e:
            px.Fetcher(UA["User-Agent"], rate=1000).get(px.TICKERS_URL)
        self.assertIn("refusing", str(e.exception))

    def test_companyfacts_fetcher(self):
        cf = _load("sec_companyfacts")
        with self.assertRaises(cf.PullError):
            cf.Fetcher(UA["User-Agent"], rate=1000).get(cf.URL % 320193)

    def test_universe(self):
        su = _load("sec_universe")
        with self.assertRaises(su.UniverseError):
            su.fetch(su.TICKERS_URL, UA["User-Agent"])


class Health(unittest.TestCase):
    def test_sec_rows_need_attention_while_refused(self):
        from app import api
        rows = [{"dataset": "sec-insiders", "status": "ok"}, {"dataset": "sec-13f", "status": "ok"},
                {"dataset": "us-events", "status": "ok"}, {"dataset": "boards", "status": "ok"}]
        gate = {"last_ok_at": "2026-10-19T16:00:00Z", "held": True, "proxy": False,
                "refused": {"since": "2026-10-19T16:08:00Z", "at": "2026-10-23T02:00:00Z", "status": 403,
                            "url": "https://data.sec.gov/x", "reference": "18.6f2c1302"}}
        extra = api.sec_access_rows(rows, gate)
        self.assertEqual([r["status"] for r in rows], ["attention", "attention", "attention", "ok"])
        self.assertEqual(rows[0]["sec_access"],
                         "the SEC is refusing this server since 2026-10-19T16:08:00Z (HTTP 403, reference 18.6f2c1302)")
        self.assertEqual(extra[0]["dataset"], "sec-access")
        self.assertEqual(extra[0]["status"], "attention")
        self.assertEqual(extra[0]["shape_flags"], [rows[0]["sec_access"]])

    def test_ok_when_answered_and_absent_when_never_asked(self):
        from app import api
        rows = [{"dataset": "sec-13f", "status": "ok"}]
        self.assertEqual(api.sec_access_rows(rows, {"last_ok_at": None, "refused": None}), [])
        extra = api.sec_access_rows(rows, {"last_ok_at": "2026-10-23T01:00:00Z", "refused": None, "held": False})
        self.assertEqual((rows[0]["status"], extra[0]["status"], extra[0]["shape_flags"]), ("ok", "ok", []))


if __name__ == "__main__":
    unittest.main()
