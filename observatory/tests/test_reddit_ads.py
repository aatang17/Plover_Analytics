"""Advertising consent, verified-account semantics and durable delivery.

All data and credentials are synthetic. No calls to Reddit or email delivery.
"""
import json
import pathlib
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts, reddit_ads


class RedditSignupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reddit-ads-test-")
        self.saved = accounts.DB_PATH, accounts._conn
        accounts.DB_PATH = pathlib.Path(self.temp.name) / "workspace.db"
        accounts._conn = None
        self.env = mock.patch.dict("os.environ", {
            "REDDIT_ADS_ENABLED": "1", "REDDIT_ADS_PIXEL_ID": "a2_test123",
            "REDDIT_ADS_ACCESS_TOKEN": "synthetic-test-credential", "ACCOUNTS_DEV_LINKS": "1",
            "ACCOUNTS_ALLOWED_EMAILS": ""})
        self.env.start()
        self.mail = mock.patch.object(accounts, "_send_email", return_value="logged")
        self.mail.start()
        api = FastAPI()
        api.include_router(accounts.router)
        api.include_router(reddit_ads.router)
        self.client = TestClient(api)

    def tearDown(self):
        self.client.close()
        if accounts._conn is not None and getattr(accounts._local, "base", None) is accounts._conn:
            accounts._local.conn.close()
            accounts._local.base = None
        if accounts._conn:
            accounts._conn.close()
        accounts.DB_PATH, accounts._conn = self.saved
        self.mail.stop()
        self.env.stop()
        self.temp.cleanup()

    def consent(self, choice="granted", click="synthetic_click_123", **headers):
        return self.client.post("/api/v1/ads/reddit-consent",
                                headers=dict({"Origin": "http://testserver"}, **headers),
                                json={"choice": choice, "click_id": click})

    def link(self, email="new@example.com"):
        result = self.client.post("/api/v1/account/signin-link", json={"email": email})
        self.assertEqual(result.status_code, 200, result.text)
        return parse_qs(urlsplit(result.json()["link"]).query)["token"][0]

    def redeem(self, token, **headers):
        result = self.client.post("/api/v1/account/session", json={"token": token}, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)

    def events(self):
        return accounts.conn().execute("SELECT * FROM reddit_signup_events").fetchall()

    def test_default_off_and_origin_protection(self):
        with mock.patch.dict("os.environ", {"REDDIT_ADS_ENABLED": ""}):
            self.assertEqual(self.consent().status_code, 404)
        result = self.client.post("/api/v1/ads/reddit-consent", headers={"Origin": "https://evil.example"},
                                  json={"choice": "granted", "click_id": "abc"})
        self.assertEqual(result.status_code, 403)
        self.assertNotIn(reddit_ads.CLICK, self.client.cookies)
        self.assertEqual(self.consent(click="bad?id=secret").status_code, 400)

    def test_no_event_for_requesting_link_or_returning_signin(self):
        result = self.consent()
        self.assertEqual(result.status_code, 200)
        self.assertIn("HttpOnly", result.headers.get("set-cookie"))
        token = self.link()
        self.assertEqual(len(self.events()), 0)
        self.redeem(token)
        self.assertEqual(len(self.events()), 1)
        first = dict(self.events()[0])
        self.redeem(self.link())
        self.assertEqual(dict(self.events()[0]), first)
        self.assertEqual(self.client.post("/api/v1/account/session", json={"token": token}).status_code, 401)

    def test_no_consent_forgery_tamper_expiry_or_gpc(self):
        self.redeem(self.link("none@example.com"))
        self.client.cookies.set(reddit_ads.CHOICE, "granted")
        self.client.cookies.set(reddit_ads.CLICK, "forged.9999999999.invalid")
        self.redeem(self.link("forged@example.com"))
        self.client.cookies.clear()
        self.consent()
        self.redeem(self.link("gpc@example.com"), **{"Sec-GPC": "1"})
        self.assertEqual(len(self.events()), 0)
        self.client.cookies.clear()
        with mock.patch.object(reddit_ads.time, "time", return_value=100):
            self.consent()
        self.redeem(self.link("expired@example.com"))
        self.assertEqual(len(self.events()), 0)

    def test_withdrawal_including_gpc_removes_queued_events(self):
        self.consent()
        self.redeem(self.link())
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.consent(**{"Sec-GPC": "1"}).json()["consent"], "denied")
        self.assertEqual(len(self.events()), 0)
        self.assertNotIn(reddit_ads.CLICK, self.client.cookies)

    def test_failed_delivery_survives_restart_and_retry_uses_same_id(self):
        self.consent()
        self.redeem(self.link())
        with mock.patch.object(reddit_ads.urllib.request, "urlopen", side_effect=OSError("offline")) as send:
            reddit_ads.deliver()
            first = json.loads(send.call_args[0][0].data)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]["attempts"], 1)
        # Reopen the durable SQLite store, as a deploy does.
        accounts._local.conn.close()
        accounts._conn.close()
        accounts._conn = None
        db = accounts.conn()
        db.execute("UPDATE reddit_signup_events SET retry_at = 0")
        db.commit()
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(reddit_ads.urllib.request, "urlopen", return_value=response) as send:
            reddit_ads.deliver()
            request = send.call_args[0][0]
            second = json.loads(request.data)
        self.assertEqual(first, second)
        event = second["data"]["events"][0]
        self.assertEqual(set(event), {"event_at", "action_source", "event_source_url", "type", "click_id", "metadata"})
        self.assertEqual(event["type"], {"tracking_type": "SIGN_UP"})
        self.assertEqual(event["event_source_url"], "https://ploveranalytics.com/signin.html")
        self.assertNotIn("new@example.com", request.data.decode())
        self.assertEqual(len(self.events()), 0)

    def test_old_events_expire_without_delivery(self):
        self.consent()
        self.redeem(self.link())
        db = accounts.conn()
        db.execute("UPDATE reddit_signup_events SET created_at = 0")
        db.commit()
        with mock.patch.object(reddit_ads.urllib.request, "urlopen") as send:
            reddit_ads.deliver()
        send.assert_not_called()
        self.assertEqual(len(self.events()), 0)
