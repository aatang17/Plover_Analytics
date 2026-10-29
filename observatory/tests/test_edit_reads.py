# -*- coding: utf-8 -*-
"""A company edit clears only the answers that read that company (app/edit_reads.py).

On 2026-10-29 one new logo for Alphabet emptied the whole response cache, and
rebuilding the ~360 answers readers had used held the server for seven
minutes: the proxy took it out of rotation and readers saw errors.
"""
import asyncio
import pathlib
import tempfile
import unittest

from fastapi import FastAPI

from app import cache, edit_reads, jp_companies, us_companies


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class EditsClearOnlyTheirAnswers(unittest.TestCase):
    def setUp(self):
        del cache._LIVE[:]
        tmp = pathlib.Path(tempfile.mkdtemp())
        saved = (us_companies.STORE_PATH, jp_companies.STORE_PATH, us_companies._CACHE, jp_companies._CACHE)
        us_companies.STORE_PATH, jp_companies.STORE_PATH = tmp / "us.json", tmp / "jp.json"
        us_companies._CACHE = jp_companies._CACHE = None

        def restore():
            us_companies.STORE_PATH, jp_companies.STORE_PATH = saved[0], saved[1]
            us_companies._CACHE, jp_companies._CACHE = saved[2], saved[3]
        self.addCleanup(restore)
        us_companies.set_name(1, "One Inc", "test")
        us_companies.set_name(2, "Two Inc", "test")

        self.calls = calls = []
        app = FastAPI()

        # plain `def` routes run in a worker thread, as the real ones do
        @app.get("/api/v1/co/{cik}")
        def co(cik: int):
            calls.append("co%d" % cik)
            return {"name": us_companies.name(cik), "logo": us_companies.logo_url(cik)}

        @app.get("/api/v1/edited")
        def edited():
            calls.append("edited")
            return {"ciks": us_companies.edited()}

        @app.get("/api/v1/many")
        def many():
            calls.append("many")
            return {"n": sum(1 for c in range(edit_reads.MAX_KEYS + 5) if us_companies.name(c + 10))}

        @app.get("/api/v1/jp/{code}")
        def jp(code: str):
            calls.append("jp" + code)
            return {"name": jp_companies.name(code)}

        @app.get("/api/v1/plain")
        def plain():
            calls.append("plain")
            return {"x": 1}

        app.add_middleware(cache.ResponseCache, version=lambda: 1, edits=(us_companies, jp_companies))
        self.app = app
        self.paths = ["/api/v1/co/1", "/api/v1/co/2", "/api/v1/edited", "/api/v1/many",
                      "/api/v1/jp/7203", "/api/v1/plain"]
        run(cache.warm(app, self.paths))
        self.assertEqual(len(calls), len(self.paths))
        del calls[:]

    def rebuilt_after(self, edit):
        edit()
        run(cache.warm(self.app, self.paths))
        out = sorted(self.calls)
        del self.calls[:]
        return out

    def test_a_logo_rebuilds_that_company_and_whole_store_readers_only(self):
        self.assertEqual(self.rebuilt_after(lambda: us_companies.hide_logo(1, "test")),
                         ["co1", "edited", "many"])

    def test_the_new_name_is_served(self):
        us_companies.set_name(2, "Two Corp", "test")
        rc = cache._LIVE[-1]
        run(cache.warm(self.app, ["/api/v1/co/2"]))
        self.assertIn(b"Two Corp", rc._entries["/api/v1/co/2?"].body)

    def test_a_company_no_answer_read_rebuilds_only_whole_store_readers(self):
        self.assertEqual(self.rebuilt_after(lambda: us_companies.set_name(99, "Ninety-Nine", "test")),
                         ["edited", "many"])

    def test_a_japanese_edit_leaves_us_answers_alone(self):
        self.assertEqual(self.rebuilt_after(lambda: jp_companies.set_name("7203", "Toyota", "test")),
                         ["jp7203"])

    def test_nothing_rebuilt_without_an_edit(self):
        self.assertEqual(self.rebuilt_after(lambda: None), [])

    def test_keep_warm_rebuilds_only_the_edited_answers(self):
        old = (cache.CHECK_EVERY, cache.PAUSE)
        cache.CHECK_EVERY, cache.PAUSE = 0.01, 0
        self.addCleanup(lambda: setattr(cache, "CHECK_EVERY", old[0]) or setattr(cache, "PAUSE", old[1]))

        async def edit_then_stop():
            task = asyncio.ensure_future(cache.keep_warm(self.app, "/nonexistent/k.json"))
            await asyncio.sleep(0.05)
            us_companies.set_name(2, "Two Corp", "test")
            await asyncio.sleep(0.3)
            task.cancel()
        run(edit_then_stop())
        self.assertEqual(sorted(self.calls), ["co2", "edited", "many"])


if __name__ == "__main__":
    unittest.main()
