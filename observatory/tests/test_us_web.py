# -*- coding: utf-8 -*-
"""Why a stock moved: the news answer from a web search (app/us_web.py).

No test here calls the search: the reply is a recorded Responses API shape,
and the routing tests mock us_web.search.
"""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import cache, us_web

REPLY = {
    "output": [
        {"type": "web_search_call", "action": {"type": "search", "queries": ["Tesla stock October 2026 news"]}},
        {"type": "message", "content": [{
            "type": "output_text",
            "text": ("Tesla rose about **10%** in October after strong deliveries. "
                     "Reuters said investors also weighed weaker profit. "
                     "([reuters.com](https://www.reuters.com/a?utm_source=openai)) "
                     "([reuters.com](https://www.reuters.com/a?utm_source=openai))"),
            "annotations": [
                {"type": "url_citation", "title": "Tesla shares jump", "url": "https://www.reuters.com/a?utm_source=openai"},
                {"type": "url_citation", "title": "Tesla shares jump", "url": "https://www.reuters.com/a?utm_source=openai"},
                {"type": "url_citation", "title": "", "url": "https://finance.yahoo.com/b?id=7&utm_medium=x"},
                {"type": "url_citation", "title": "bad", "url": "javascript:alert(1)"},
            ]}]},
    ],
    "usage": {"input_tokens": 9000, "output_tokens": 120},
}


class ReadResponse(unittest.TestCase):
    def test_text_is_plain_and_every_report_listed_once(self):
        got = us_web.read_response(REPLY)
        self.assertEqual(got["text"], "Tesla rose about 10% in October after strong deliveries. "
                                      "Reuters said investors also weighed weaker profit.")
        self.assertEqual([s["url"] for s in got["sources"]], ["https://www.reuters.com/a", "https://finance.yahoo.com/b?id=7"])
        self.assertEqual(got["sources"][0]["site"], "reuters.com")
        self.assertEqual(got["sources"][1]["title"], "finance.yahoo.com")    # no title: the site stands in
        self.assertEqual(got["searches"], ["Tesla stock October 2026 news"])

    def test_off_with_the_box_models_off(self):
        with mock.patch.dict(os.environ, {"US_ASK_MODEL": "0", "OPENAI_API_KEY": "k"}):
            self.assertIsNone(us_web.config())
        with mock.patch.dict(os.environ, {"US_ASK_WEB": "0", "US_ASK_MODEL": "", "OPENAI_API_KEY": "k"}):
            self.assertIsNone(us_web.config())

    def test_one_search_serves_every_wording_of_a_shared_question(self):
        share = ("company", 1, "lately %s" % id(self))
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web.us_ask_model, "_post", return_value=REPLY) as post:
            a = us_web.search("Why has Tesla stock moved over the past month?", share=share)
            b = us_web.search("Why has Tesla stock moved over the past month?", "how did tesla do", share=share)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(a["text"], b["text"])

    def test_two_readers_asking_at_once_pay_for_one_search(self):
        import threading
        import time
        share = ("company", 2, "lately %s" % id(self))

        def slow(*a, **k):
            time.sleep(0.3)
            return REPLY
        got = []
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web.us_ask_model, "_post", side_effect=slow) as post:
            ts = [threading.Thread(target=lambda: got.append(us_web.search("q", share=share))) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(got), 3)

    def test_a_shared_answer_lasts_for_one_part_of_the_new_york_day(self):
        import datetime
        utc = datetime.timezone.utc
        slot = lambda h, m=0: us_web.market_slot(datetime.datetime(2026, 10, 29, h, m, tzinfo=utc))
        self.assertEqual(slot(13, 0)[1], "before the open")      # 9:00 in New York
        self.assertEqual(slot(13, 30)[1], "trading")             # 9:30
        self.assertEqual(slot(19, 59)[1], "trading")             # 15:59
        self.assertEqual(slot(20, 0)[1], "after the close")      # 16:00

    def test_a_reply_with_no_report_is_not_shown(self):
        bare = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Shares fell.", "annotations": []}]}]}
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web.us_ask_model, "_post", return_value=bare):
            with self.assertRaises(us_web.WebError):
                us_web.search("why did zzz fall %s" % id(self))


FOUND = {"text": "Tesla fell on Thursday after weaker profit. Reuters said margins shrank.",
         "sources": [{"title": "Tesla profit falls", "url": "https://www.reuters.com/t", "site": "reuters.com"}],
         "searches": ["Tesla shares fall"], "model": "m", "asked_at": "2026-10-29T09:00+00:00"}


def _live():
    from tests.test_us_ask import _live as live
    return live()


@unittest.skipUnless(_live(), "data/sec.duckdb without the full US set")
class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def setUp(self):
        os.environ["US_ASK_MODEL"] = "0"
        self.addCleanup(os.environ.pop, "US_ASK_MODEL", None)

    def test_why_gets_the_news_with_links_and_is_never_cached(self):
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web, "search", return_value=dict(FOUND)) as s:
            r = self.c.get("/api/v1/us/ask", params={"q": "Why did the market fall today?"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("no-store", r.headers.get("cache-control", ""))
        d = r.json()
        self.assertEqual(d["recipe"], "web_news")
        self.assertEqual(d["headline"], "Tesla fell on Thursday after weaker profit.")
        self.assertEqual(d["answer"], "Reuters said margins shrank.")
        self.assertEqual([x.get("url") for x in d["sources"] if x.get("url")], ["https://www.reuters.com/t"])
        self.assertTrue(any("web search" in n for n in d["notes"]))
        self.assertTrue(any("Tesla shares fall" in st for st in d["steps"]))
        s.assert_called_once()
        # "the market today" is a question every reader shares: one search, asked the same way for all
        self.assertEqual(s.call_args.kwargs["share"], ("index", "sp500", "today"))
        self.assertEqual(s.call_args.args[0], "What moved the S&P 500 today? Give the biggest moves, their dates and the reasons the reports give.")

    def test_advice_is_still_refused_and_a_failed_search_refuses(self):
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web, "search", side_effect=us_web.WebError("x")):
            r = self.c.get("/api/v1/us/ask", params={"q": "Why should I buy the market now?"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("investment advice", r.json()["detail"])
            r = self.c.get("/api/v1/us/ask", params={"q": "Why did the market fall today?"})
            self.assertEqual(r.status_code, 422)
            self.assertIn("why a price", r.json()["detail"])

    def test_news_words(self):
        from app.stock_recipes import _NEWS_ASK
        for q in ("why did tesla fall", "any news on apple?", "what caused the selloff", "what's going on with nvidia"):
            self.assertTrue(_NEWS_ASK.search(q), q)
        for q in ("news corp revenue", "what drove the s&p 500 this year?", "who owns nvidia"):
            self.assertFalse(_NEWS_ASK.search(q), q)


@unittest.skipUnless(_live(), "data/sec.duckdb without the full US set")
class OwnRecord(unittest.TestCase):
    """Why it moved, from what Plover holds, before any search is paid for."""

    @classmethod
    def setUpClass(cls):
        from app import sec_api
        cls.cur = sec_api._require()

    def setUp(self):
        os.environ["US_ASK_MODEL"] = "0"
        self.addCleanup(os.environ.pop, "US_ASK_MODEL", None)

    def test_every_wording_of_a_plain_why_is_one_question(self):
        from app.stock_recipes import _news_topic
        a = _news_topic(self.cur, "Why did Tesla fall in July 2026?")
        b = _news_topic(self.cur, "why did tesla shares move in july 2026")
        self.assertEqual(a["share"], b["share"])
        self.assertEqual(_news_topic(self.cur, "any news on apple?")["company"]["ticker"], "AAPL")   # not News Corp
        for q in ("Why did Apple drop its car project?", "Why did Tesla and Ford fall?", "Why did Tesla fall in 2024 and 2025?"):
            self.assertIsNone(_news_topic(self.cur, q), q)

    def test_a_move_on_results_day_is_answered_without_a_search(self):
        from app import stock_recipes as sr
        with mock.patch.object(us_web, "search") as s:
            out = sr.answer(self.cur, "Why did Tesla fall in July 2026?", None, "")
        s.assert_not_called()
        self.assertEqual(out["recipe"], "why_record")
        self.assertIn("after it reported quarterly results", out["headline"])
        self.assertTrue(any("8-K" in (x.get("credit") or "") and x.get("url") for x in out["sources"]))
        self.assertEqual(out["read"]["switches"][0]["key"], "web")
        # the period change in the words is the one in the chart's figures
        change = next(f["value"] for f in out["figures"] if f["label"] == "Change")
        self.assertIn(sr.pct(abs(change)), out["answer"])

    def test_the_switch_searches_the_news_instead(self):
        from app import stock_recipes as sr
        with mock.patch.object(us_web, "config", return_value=("k", "m")), \
                mock.patch.object(us_web, "search", return_value=dict(FOUND)) as s:
            out = sr.answer(self.cur, "Why did Tesla fall in July 2026?", None, "web")
        s.assert_called_once()
        self.assertEqual(out["recipe"], "web_news")


class NoStore(unittest.TestCase):
    def test_a_no_store_answer_is_not_kept(self):
        import asyncio

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b"{}"})

        rc = cache.ResponseCache(app, version=lambda: 1)
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {"type": "http.request"}

        scope = {"type": "http", "method": "GET", "path": "/api/v1/us/ask", "query_string": b"q=why"}
        loop = asyncio.new_event_loop()          # its own loop: asyncio.run would unset the one other tests share
        try:
            loop.run_until_complete(rc(scope, receive, send))
        finally:
            loop.close()
        self.assertEqual(len(rc._entries), 0)
        self.assertEqual(sent[-1]["body"], b"{}")


if __name__ == "__main__":
    unittest.main()
