# -*- coding: utf-8 -*-
"""Keeping the response cache warm (app/cache.py).

Until 2026-10-27 only the macro datasets' answers were built at start-up, and
any data swap or company edit emptied the cache with nothing rebuilding it:
the first reader of the CEO pay page waited 4-5 s on the server. Now the
answers readers used are rebuilt after a swap and saved for the next copy.
"""
import asyncio
import json
import pathlib
import tempfile
import unittest

from fastapi import FastAPI

from app import cache


def make_app(stamp):
    calls = []
    inner = FastAPI()

    @inner.get("/api/v1/x")
    def x(a: str = ""):
        calls.append(a)
        return {"a": a}

    inner.add_middleware(cache.ResponseCache, version=lambda: stamp[0])
    return inner, calls


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class Warm(unittest.TestCase):
    def setUp(self):
        del cache._LIVE[:]

    def test_query_string_reaches_the_app_and_the_key(self):
        stamp = [1]
        app, calls = make_app(stamp)
        run(cache.warm(app, ["/api/v1/x?a=7", "/api/v1/x"]))
        self.assertEqual(calls, ["7", ""])
        self.assertEqual(cache._LIVE[-1].keys(), ["/api/v1/x?", "/api/v1/x?a=7"])
        self.assertEqual(json.loads(cache._LIVE[-1]._entries["/api/v1/x?a=7"].body), {"a": "7"})

    def test_a_new_stamp_rebuilds_what_readers_used(self):
        stamp = [1]
        app, calls = make_app(stamp)
        run(cache.warm(app, ["/api/v1/x?a=1", "/api/v1/x?a=2"]))
        del calls[:]
        old = (cache.CHECK_EVERY, cache.PAUSE)
        cache.CHECK_EVERY, cache.PAUSE = 0.01, 0
        try:
            async def swap_then_stop():
                task = asyncio.ensure_future(cache.keep_warm(app, "/nonexistent/k.json"))
                await asyncio.sleep(0.05)
                stamp[0] = 2            # a data file swapped in
                await asyncio.sleep(0.1)
                task.cancel()
            run(swap_then_stop())
        finally:
            cache.CHECK_EVERY, cache.PAUSE = old
        self.assertEqual(sorted(calls), ["1", "2"])          # each rebuilt once
        entries = cache._LIVE[-1]._entries
        self.assertTrue(all(e.version == 2 for e in entries.values()))

    def test_the_list_survives_a_restart(self):
        stamp = [1]
        app, _ = make_app(stamp)
        run(cache.warm(app, ["/api/v1/x?a=1", "/api/v1/x?a=2"]))
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "warm-keys.json"
            cache.remember(path)
            self.assertEqual(cache.remembered(path), ["/api/v1/x?a=2", "/api/v1/x?a=1"])
            path.write_text("not json")
            self.assertEqual(cache.remembered(path), [])
            path.write_text(json.dumps(["/admin/secret", "/api/v1/x?"]))
            self.assertEqual(cache.remembered(path), ["/api/v1/x?"])
        self.assertEqual(cache.remembered("/nonexistent/k.json"), [])


if __name__ == "__main__":
    unittest.main()
