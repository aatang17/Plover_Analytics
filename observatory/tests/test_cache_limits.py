# -*- coding: utf-8 -*-
"""The response cache's bounds (app/cache.py).

64 entries was set in August 2026 for two datasets; by October start-up
pre-built 497 paths and all but the last 64 were evicted as soon as they were
made. The cache is now bounded by count and by total size, so it keeps every
pre-built answer and still cannot be grown without limit by query strings.
"""
import unittest

from app import cache


def entry(size, version=1):
    return cache._Entry(version, "e", b"application/json", b"x" * size, b"")


class Bounds(unittest.TestCase):
    def setUp(self):
        self.rc = cache.ResponseCache(app=None, version=lambda: 1)

    def test_every_prebuilt_answer_fits(self):
        for i in range(497):
            self.rc._put("/api/v1/d%d/series?" % i, entry(100))
        self.assertEqual(len(self.rc._entries), 497)

    def test_count_and_total_size_are_both_bounded(self):
        for i in range(cache.MAX_ENTRIES + 50):
            self.rc._put("/k%d" % i, entry(10))
        self.assertEqual(len(self.rc._entries), cache.MAX_ENTRIES)
        big = cache.MAX_TOTAL_BYTES // 4 + 1
        for i in range(6):
            self.rc._put("/big%d" % i, entry(big))
        self.assertLessEqual(self.rc._bytes, cache.MAX_TOTAL_BYTES)
        self.assertEqual(self.rc._bytes, sum(cache._size(e) for e in self.rc._entries.values()))

    def test_replacing_and_expiring_keep_the_byte_count_right(self):
        self.rc._put("/a", entry(100))
        self.rc._put("/a", entry(40))
        self.assertEqual(self.rc._bytes, 40)
        self.assertIsNone(self.rc._get("/a", version=2))   # the database file changed
        self.assertEqual(self.rc._bytes, 0)


if __name__ == "__main__":
    unittest.main()
