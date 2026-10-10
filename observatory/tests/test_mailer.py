# -*- coding: utf-8 -*-
"""Outbound email: what is proved is that a real send names itself. Resend's
Cloudflare front refuses urllib's default user agent (403, error code 1010),
which silently failed every email the site sent until 2026-10-11."""
import os
import unittest
from unittest import mock

from app import mailer


class _Resp(object):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


class MailerTest(unittest.TestCase):
    def test_send_names_itself_and_carries_reply_to(self):
        seen = {}

        def urlopen(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            seen["body"] = req.data
            return _Resp()

        env = {"RESEND_API_KEY": "k", "RESEND_FROM": "Plover <noreply@example.com>"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(mailer.send("a@example.com", "s", "t", reply_to="b@example.com"), "sent")
        self.assertTrue(seen["ua"].startswith("PloverAnalytics/"))
        self.assertIn(b'"reply_to": "b@example.com"', seen["body"])

    def test_send_link_never_raises(self):
        env = {"RESEND_API_KEY": "k", "RESEND_FROM": "Plover <noreply@example.com>"}

        def down(req, timeout=None):
            raise OSError("down")
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", down):
            self.assertFalse(mailer.send_link("a@example.com", "s", ["p"], "https://x/l", "Open"))


if __name__ == "__main__":
    unittest.main()
