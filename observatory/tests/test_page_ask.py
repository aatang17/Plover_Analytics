# -*- coding: utf-8 -*-
"""The question box on a data page (web/assets/page-ask.js): on the three busiest
US pages, with what it needs loaded before it, and able to carry an apostrophe.
"""
import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PAGES = ("us-pay.html", "us-prices.html", "us-treasury.html")


class PageAsk(unittest.TestCase):
    def test_each_page_has_the_box_and_what_it_needs(self):
        for name in PAGES:
            html = (WEB / name).read_text(encoding="utf-8")
            self.assertIn("data-page-ask", html, name)
            self.assertIn("assets/page-ask.css", html, name)
            order = [html.index("assets/%s" % s) for s in ("format.js", "charts.js", "us-format.js", "page-ask.js")]
            self.assertEqual(order, sorted(order), name)

    def test_each_box_offers_questions(self):
        for name in PAGES:
            html = (WEB / name).read_text(encoding="utf-8")
            for found in re.findall(r'data-examples="([^"]*)"', html):
                self.assertGreaterEqual(len(found.split("|")), 2, name)

    def test_the_company_box_is_filled_by_the_page(self):
        self.assertIn("data-company", (WEB / "us-pay.html").read_text(encoding="utf-8"))
        self.assertIn("pageAsk.company(", (WEB / "assets" / "us-pay.js").read_text(encoding="utf-8"))

    def test_an_apostrophe_cannot_end_an_attribute(self):
        js = (WEB / "assets" / "page-ask.js").read_text(encoding="utf-8")
        self.assertNotIn("href='/?q=\" + encodeURIComponent", js)
        self.assertNotIn("placeholder='e.g. \" + esc(", js)

    def test_used_allowance_sends_the_question_to_your_plover(self):
        # past the free answer, a typed question goes through sign-up to Your
        # Plover with the question before it and the way back to this page
        js = (WEB / "assets" / "page-ask.js").read_text(encoding="utf-8")
        self.assertIn("if (handOff(via)) return { handOff: true };", js)
        self.assertIn('"my.html?q=" + encodeURIComponent(q)', js)
        self.assertIn('"&back=" + encodeURIComponent(location.pathname + location.search)', js)
        self.assertIn("signin.html?returnTo=\" + attr(encodeURIComponent(ploverHref(q, before)))", js)
        # the page's own suggested questions are never counted or sent away
        self.assertIn('via !== "example"', js)

    def test_your_plover_takes_the_way_back_from_this_site_only(self):
        js = (WEB / "assets" / "my.js").read_text(encoding="utf-8")
        body = js[js.index("function safeBack(raw)"):js.index("function showBack(")]
        rule = re.search(r"/\^(.*?)\$/\.test\(raw\)", body).group(1)
        allowed = re.compile("^" + rule.replace("\\/", "/") + "$")
        for good in ("/us-treasury.html", "/us-company.html?t=AAPL", "/stakes.html?c=285A"):
            self.assertTrue(allowed.match(good), good)
        for bad in ("//evil.example/x", "https://evil.example/", "javascript:alert(1)", "us-treasury.html",
                    "/a b.html"):
            self.assertFalse(allowed.match(bad), bad)
        self.assertIn('raw.indexOf("..") >= 0', body)
        self.assertIn('id="my-back"', (WEB / "my.html").read_text(encoding="utf-8"))


class PageAskCount(unittest.TestCase):
    """POST /api/v1/us/ask/free: a page box counts an economy answer it showed."""

    def setUp(self):
        import os
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import free_answers, us_ask_api
        self.env = {k: os.environ.get(k) for k in ("ACCOUNTS_ENABLED", "FREE_ANSWERS")}
        os.environ["ACCOUNTS_ENABLED"] = "1"
        os.environ["FREE_ANSWERS"] = "1"
        free_answers._counts.clear()
        free_answers._day[0] = None
        app = FastAPI()
        app.include_router(us_ask_api.router)
        self.c = TestClient(app)

    def tearDown(self):
        import os
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_one_economy_answer_uses_the_free_one(self):
        ua = {"User-Agent": "Mozilla/5.0 (test)"}
        self.assertEqual(self.c.get("/api/v1/us/ask/free", headers=ua).json(), {"free": 1, "left": 1})
        r = self.c.post("/api/v1/us/ask/free", headers=ua)
        self.assertEqual(r.json(), {"free": 1, "left": 0})
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual(self.c.post("/api/v1/us/ask/free", headers=ua).json()["left"], 0)
        self.assertEqual(self.c.get("/api/v1/us/ask/free", headers=ua).json()["left"], 0)


if __name__ == "__main__":
    unittest.main()
