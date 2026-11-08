# -*- coding: utf-8 -*-
"""Code Runs: data fetched by a command copied from a chart's Copy Code.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_code_runs

The commands carry &ref=code and are run from Stata, R or pandas, which look
like scripts to the visit counter. A run must still be counted, and a page
read, an untagged API call or a different tag must not be.
"""
import pathlib
import shutil
import tempfile
import unittest

from app import visits

_REAL_DIR = [None, None]


def setUpModule():
    """Write the test's own events to a temporary directory. The visit log on
    the data volume is a product record, not a scratch file."""
    _REAL_DIR[0] = visits.ANALYTICS_DIR
    _REAL_DIR[1] = tempfile.mkdtemp(prefix="code-runs-test-")
    visits.ANALYTICS_DIR = pathlib.Path(_REAL_DIR[1])
    visits.SALT_PATH = visits.ANALYTICS_DIR / "salt"


def tearDownModule():
    visits.flush()
    visits.ANALYTICS_DIR = _REAL_DIR[0]
    visits.SALT_PATH = visits.ANALYTICS_DIR / "salt"
    shutil.rmtree(_REAL_DIR[1], ignore_errors=True)


def scope(path, query, ua, ip="203.0.113.21"):
    return {"type": "http", "method": "GET", "path": path, "client": (ip, 1),
            "headers": [(b"user-agent", ua.encode())], "query_string": query.encode()}


OBS = "/api/v1/job-openings-jp/observations"
TAGGED = "series=sa.active.incl_part&format=csv&notes=0&ref=code"


class CodeRunsTest(unittest.TestCase):
    def test_a_tagged_fetch_from_a_script_is_a_code_run(self):
        before = visits.summary(1)["code_runs"]["runs"]
        visits.observe(scope(OBS, TAGGED, "R (4.4.1 aarch64-apple-darwin20)"), 200)
        visits.observe(scope(OBS, TAGGED, "Python-urllib/3.12", ip="203.0.113.22"), 200)
        visits.observe(scope(OBS, TAGGED.replace("&ref=code", ""), "Stata/18"), 200)
        visits.observe(scope(OBS, TAGGED.replace("ref=code", "ref=yt"), "Stata/18"), 200)
        visits.observe(scope("/labour.html", "ref=code", "Mozilla/5.0 Chrome/128.0"), 200)
        visits.flush()
        got = visits.summary(1)["code_runs"]
        self.assertEqual(got["runs"] - before, 2, "only the two tagged API fetches")
        self.assertGreaterEqual(got["users"], 2)
        self.assertTrue(all("key" in r and "runs" in r for r in got["networks"]))

    def test_the_tag_is_kept_on_api_events(self):
        visits.observe(scope(OBS, TAGGED, "Stata/18", ip="203.0.113.23"), 200)
        visits.flush()
        path = visits._month_path(__import__("datetime").datetime.utcnow())
        lines = path.read_text(encoding="utf-8").splitlines()
        tagged = [l for l in lines if '"src": "code"' in l and '"kind": "api"' in l]
        self.assertTrue(tagged)
