# -*- coding: utf-8 -*-
"""US labour (app/adapters/us_labour.py): the BLS counts queries per day — 25
without a key — so each window's answer is kept for the day and a key widens
the windows. On 2026-11-05 the probe-then-ingest pair ran the allowance out."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from app.adapters import us_labour as a


class Windows(unittest.TestCase):
    def test_keyless_ten_year_windows(self):
        with mock.patch.dict(os.environ, {"BLS_API_KEY": ""}):
            self.assertEqual(a.windows(1939, 2026)[:2], [(1939, 1948), (1949, 1958)])
            self.assertEqual(a.windows(1939, 2026)[-1], (2019, 2026))
            self.assertEqual(len(a.windows(1939, 2026)), 9)

    def test_a_key_halves_the_queries(self):
        with mock.patch.dict(os.environ, {"BLS_API_KEY": "k"}):
            self.assertEqual(len(a.windows(1939, 2026)), 5)


class DayCache(unittest.TestCase):
    def test_a_window_is_asked_for_once_a_day(self):
        tmp = tempfile.mkdtemp()
        answer = json.dumps({"status": "REQUEST_SUCCEEDED", "Results": {"series": []}}).encode("utf-8")
        calls = []

        class Resp(object):
            def __init__(self, body):
                self.body = body
            def read(self):
                return self.body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=0):
            calls.append(req.full_url)
            return Resp(answer)

        today = datetime.date(2026, 11, 5)
        with mock.patch.dict(os.environ, {"OBSERVATORY_DATA_DIR": tmp, "BLS_API_KEY": ""}):
            with mock.patch.object(a.urllib.request, "urlopen", urlopen):
                a._post(2019, 2026, today)
                a._post(2019, 2026, today)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].startswith("https://api.bls.gov/publicAPI/v1/"))
                a._post(2019, 2026, today + datetime.timedelta(days=1))
                self.assertEqual(len(calls), 2)
            with mock.patch.dict(os.environ, {"BLS_API_KEY": "k"}):
                with mock.patch.object(a.urllib.request, "urlopen", urlopen):
                    a._post(2009, 2028, today)
                    self.assertTrue(calls[-1].startswith("https://api.bls.gov/publicAPI/v2/"))

    def test_a_refused_answer_is_not_cached(self):
        tmp = tempfile.mkdtemp()
        body = json.dumps({"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold"]}).encode("utf-8")

        class Resp(object):
            def read(self):
                return body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch.dict(os.environ, {"OBSERVATORY_DATA_DIR": tmp, "BLS_API_KEY": ""}):
            with mock.patch.object(a.urllib.request, "urlopen", lambda req, timeout=0: Resp()):
                with self.assertRaises(a.FetchError):
                    a._post(2019, 2026, datetime.date(2026, 11, 5))
        self.assertEqual(os.listdir(os.path.join(tmp, "bls-cache")) if os.path.isdir(os.path.join(tmp, "bls-cache")) else [], [])


if __name__ == "__main__":
    unittest.main()
