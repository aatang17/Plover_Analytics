import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts, page_preview
from app.main import RevalidatedStatic, WEB_DIR


class PagePreviewTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for override in (
            patch.dict(os.environ, ACCOUNTS_ENABLED="1", ACCOUNTS_ALLOWED_EMAILS=""),
            patch.object(accounts, "DB_PATH", Path(directory.name) / "workspace.db"),
            patch.object(accounts, "_conn", None),
            patch.object(accounts, "_LIVE_SESSIONS", {}),
        ):
            override.start()
            self.addCleanup(override.stop)

    def test_preview_survives_prerender_and_returns_to_requested_page(self):
        app = FastAPI()
        app.include_router(accounts.router)
        app.mount("/", RevalidatedStatic(directory=str(WEB_DIR), html=True))
        client = TestClient(app, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False)
        self.addCleanup(client.close)

        first = client.get("/cpi.html")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["cache-control"], "private, no-store")
        self.assertIn(page_preview.COOKIE, client.cookies)
        self.assertEqual(client.get("/cpi.html?ref=newsletter").status_code, 200)
        self.assertEqual(client.get("/gdp.html").status_code, 200)
        self.assertEqual(client.get("/labour.html").status_code, 200)

        target = "/us-treasury.html?maturity=10y&ref=test"
        blocked = client.get(target)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked.headers["cache-control"], "private, no-store")
        location = urlsplit(blocked.headers["location"])
        self.assertEqual(location.path, "/signin.html")
        self.assertEqual(parse_qs(location.query), {"reason": ["page-preview"], "returnTo": [target]})
        self.assertEqual(client.get("/cpi.html").status_code, 200)
        self.assertEqual(client.get("/manual.html").status_code, 200)
        self.assertEqual(client.get(target, headers={"User-Agent": "Googlebot"}).status_code, 200)

        link = accounts.issue_link("reader@example.com", target, "http://testserver/")
        token = parse_qs(urlsplit(link).query)["token"][0]
        session = client.post("/api/v1/account/session", json={"token": token})
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["returnTo"], target)
        self.assertEqual(client.get(target).status_code, 200)
        self.assertEqual(client.post("/api/v1/account/signout").status_code, 200)
        self.assertEqual(client.get(target).status_code, 302)

        with patch.dict(os.environ, ACCOUNTS_ALLOWED_EMAILS="invited@example.com"):
            self.assertEqual(client.get(target).status_code, 200)

    def test_concurrent_requests_share_the_remaining_allowance(self):
        _, cookie = page_preview._open_preview(None, "cpi.html")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda page: page_preview._open_preview(cookie, page),
                                    ["boj.html", "labour.html", "gdp.html"]))
        self.assertEqual(sum(allowed for allowed, _ in results), 2)
