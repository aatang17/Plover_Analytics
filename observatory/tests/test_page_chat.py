# -*- coding: utf-8 -*-
"""Plover Assistant (app/page_chat.py): the word rules, the company finder, and the
route answering from the page's own data with the model switched off.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_page_chat
The database checks skip without data/equity.duckdb carrying board data.
"""
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import equity_api, governance_api, page_chat, visits
from app.main import app


def _has_boards():
    if not equity_api.DB_PATH.exists():
        return False
    try:
        return bool(governance_api.companies(q="Toyota")["companies"])
    except Exception:                                             # noqa: BLE001
        return False


HAS = _has_boards()


class WordsTest(unittest.TestCase):
    """What the panel does when the model is off: plain word rules, no database."""

    def test_screens(self):
        for q, metric in (("Which boards have no women?", "no_women"),
                          ("youngest boards", "youngest_boards"),
                          ("widest gender pay gap", "widest_gender_pay_gap"),
                          ("companies with the most directors over 70", "oldest_directors")):
            self.assertEqual(page_chat._gov_words(q, None), {"action": "screen", "metric": metric, "listed": True}, q)

    def test_top_paid(self):
        self.assertEqual(page_chat._gov_words("Who are the highest-paid executives?", None), {"action": "top_paid"})

    def test_company_view_keeps_the_company(self):
        plan = page_chat._gov_words("how many women?", "Sony Group Corporation")
        self.assertEqual(plan, {"action": "company", "company": "Sony Group Corporation", "topic": "women"})
        plan = page_chat._gov_words("what was the CEO paid?", "Sony Group Corporation")
        self.assertEqual(plan["topic"], "named")

    def test_nothing_matched(self):
        self.assertIsNone(page_chat._gov_words("share price", None))


class FormatTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(page_chat._yen_m(2436000000), "¥2,436m")
        self.assertEqual(page_chat._yen_m(None), "—")
        self.assertEqual(page_chat._num(-1.25, 1), "−1.2")
        self.assertEqual(page_chat._pct_ratio(0.222), "22.2%")

    def test_names_as_the_page_writes_them(self):
        self.assertEqual(page_chat._name({"filer_name_en": "TOEI ANIMATION CO.,LTD."}), "Toei Animation Co., Ltd.")


@unittest.skipUnless(HAS, "needs data/equity.duckdb with board data")
class RouteTest(unittest.TestCase):
    """The route, model off, against the local database: every number comes from the page's functions."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"US_ASK_MODEL": "0"})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = mock.patch.object(visits, "ANALYTICS_DIR", __import__("pathlib").Path(self.tmp.name))
        self.dir.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.dir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def ask(self, q, **extra):
        r = self.client.get("/api/v1/page-chat", params=dict(page="governance", q=q, **extra))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_screen_sets_the_page(self):
        d = self.ask("Which boards have no women?")
        self.assertEqual(d["page_action"], {"type": "screen", "metric": "no_women", "listed": True})
        s = governance_api.summary(year="", listed="true")
        self.assertIn("{:,}".format(s["boards_with_no_women"]), d["reply"])
        self.assertTrue(d["table"]["rows"])

    def test_company_follow_up_uses_its_figures(self):
        code = governance_api.companies(q="Toyota Motor")["companies"][0]["sec_code"]
        co = governance_api.company(sec_code=code, year="")
        d = self.ask("how many women?", c=code)
        self.assertEqual(d["page_action"]["type"], "company")
        self.assertEqual(d["page_action"]["code"], code)
        self.assertIn("%d women" % co["female_officers"], d["reply"])

    def test_not_on_the_page_says_so(self):
        d = self.ask("What is the share price?")
        self.assertFalse(d["answered"])
        self.assertIsNone(d["page_action"])

    def test_question_is_logged_and_never_cached(self):
        self.ask("youngest boards")
        self.ask("youngest boards")
        lines = []
        for name in os.listdir(self.tmp.name):
            if name.startswith("pagechat-"):
                with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
                    lines += [json.loads(x) for x in f]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["q"], "youngest boards")
        self.assertEqual(lines[0]["action"], "screen")
        self.assertTrue(re.match(r"^[0-9a-f]+$", lines[0]["visitor"]))

    def test_unknown_page(self):
        r = self.client.get("/api/v1/page-chat", params={"page": "nowhere", "q": "hi"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
