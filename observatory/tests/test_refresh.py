"""The daily refresh and its watchdog.

These cover the parts that are hard to see failing in production: a scheduler
that must fire exactly once a day and never in a loop, and a staleness signal
whose whole job is to notice that nothing is happening.
"""
import asyncio
import datetime
import os
import signal
import unittest

from app import heartbeat, refresh

UTC = datetime.timezone.utc


def at(day, hour, minute=0):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=UTC)


class ClockTest(unittest.TestCase):
    def setUp(self):
        os.environ["REFRESH_AT"] = "09:00"

    def test_boot_after_target_does_not_refire_the_same_day(self):
        """The restart *was* the refresh; today's slot is already served."""
        clock = refresh._Clock(at(3, 10))
        self.assertFalse(clock.due(at(3, 10, 1)))
        self.assertFalse(clock.due(at(3, 23, 59)))

    def test_fires_next_day(self):
        clock = refresh._Clock(at(3, 10))
        self.assertTrue(clock.due(at(4, 9)))

    def test_boot_before_target_fires_at_the_target(self):
        clock = refresh._Clock(at(3, 8))
        self.assertFalse(clock.due(at(3, 8, 59)))
        self.assertTrue(clock.due(at(3, 9)))

    def test_fires_once_only(self):
        clock = refresh._Clock(at(3, 8))
        self.assertTrue(clock.due(at(3, 9)))
        clock.fired(at(3, 9))
        self.assertFalse(clock.due(at(3, 9, 1)))
        self.assertFalse(clock.due(at(3, 18)))
        self.assertTrue(clock.due(at(4, 9)))

    def test_bad_schedule_falls_back_instead_of_raising(self):
        fallback = (int(refresh.DEFAULT_AT[:2]), int(refresh.DEFAULT_AT[3:]))
        os.environ["REFRESH_AT"] = "not-a-time"
        self.assertEqual(refresh.scheduled_at(), fallback)
        os.environ["REFRESH_AT"] = "25:00"
        self.assertEqual(refresh.scheduled_at(), fallback)


class LoopTest(unittest.TestCase):
    """The loop must end the process when due — and only when supervised."""

    def _run(self, supervised, times):
        sent = []
        original = (refresh._utcnow, refresh.TICK_SECONDS,
                    refresh.MIN_UPTIME_SECONDS, os.kill)
        clock_times = list(times)

        def fake_now():
            return clock_times.pop(0) if len(clock_times) > 1 else clock_times[0]

        refresh._utcnow = fake_now
        refresh.TICK_SECONDS = 0.01
        refresh.MIN_UPTIME_SECONDS = 0
        os.kill = lambda pid, sig: sent.append(sig)
        os.environ["REFRESH_AT"] = "09:00"
        os.environ["REFRESH_SUPERVISED"] = "1" if supervised else ""
        try:
            asyncio.get_event_loop().run_until_complete(
                asyncio.wait_for(refresh.run(), timeout=2))
        except asyncio.TimeoutError:
            pass
        finally:
            (refresh._utcnow, refresh.TICK_SECONDS,
             refresh.MIN_UPTIME_SECONDS, os.kill) = original
            os.environ.pop("REFRESH_SUPERVISED", None)
        return sent

    def test_supervised_and_due_ends_the_process(self):
        sent = self._run(True, [at(3, 8, 59), at(3, 9, 0)])
        self.assertEqual(sent, [signal.SIGTERM])

    def test_unsupervised_never_ends_the_process(self):
        """A development server must survive its own scheduler."""
        sent = self._run(False, [at(3, 8, 59), at(3, 9, 0)])
        self.assertEqual(sent, [])


class HeartbeatTest(unittest.TestCase):
    def test_overdue_after_the_limit(self):
        stamped = at(1, 9)
        heartbeat.write(now=stamped)
        try:
            fresh = heartbeat.status(now=at(1, 20))
            self.assertFalse(fresh["refresh_overdue"])
            self.assertEqual(fresh["last_ingest_at"], "2026-09-01T09:00:00Z")
            # 47 hours: the real 1-3 September outage.
            late = heartbeat.status(now=at(3, 8))
            self.assertTrue(late["refresh_overdue"])
            self.assertEqual(late["hours_since_ingest"], 47.0)
        finally:
            heartbeat.write()

    def test_missing_stamp_is_unknown_not_healthy(self):
        path = heartbeat.PATH
        saved = path.read_bytes() if path.exists() else None
        try:
            if path.exists():
                path.unlink()
            self.assertIsNone(heartbeat.status()["refresh_overdue"])
        finally:
            if saved is not None:
                path.write_bytes(saved)


class ProblemsTest(unittest.TestCase):
    def test_each_fault_is_reported_once_under_its_own_key(self):
        found = dict(refresh.problems({
            "refresh_overdue": True, "hours_since_ingest": 47.0,
            "refresh_max_age_hours": 26.0,
            "datasets": [
                {"dataset": "jgb-yields", "published": True, "stale": True,
                 "latest_period": "2026-08-27", "days_since_latest_period": 8,
                 "stale_after_days": 7, "unpublished_artifact": False},
                {"dataset": "cpi-jp", "published": True, "stale": False,
                 "unpublished_artifact": True},
                {"dataset": "boj-assets", "published": False},
            ]}))
        self.assertIn("refresh", found)
        self.assertIn("jgb-yields:stale", found)
        self.assertIn("cpi-jp:orphan", found)
        self.assertIn("boj-assets:unpublished", found)

    def test_healthy_report_has_no_problems(self):
        self.assertEqual(refresh.problems(
            {"refresh_overdue": False,
             "datasets": [{"dataset": "cpi-jp", "published": True, "stale": False,
                           "unpublished_artifact": False}]}), [])

    def test_alert_throttles_a_repeat_but_not_a_new_fault(self):
        refresh._last_alert.clear()
        os.environ.pop("ALERT_WEBHOOK_URL", None)  # log-only path
        first = refresh.alert([("a", "one")], now=1000)
        self.assertEqual(first, ["a"])
        self.assertEqual(refresh.alert([("a", "one")], now=1100), [])
        self.assertEqual(refresh.alert([("b", "two")], now=1100), ["b"])
        self.assertEqual(
            refresh.alert([("a", "one")],
                          now=1000 + refresh.ALERT_REPEAT_SECONDS + 1), ["a"])


if __name__ == "__main__":
    unittest.main()


class ShapeFlagSentences(unittest.TestCase):
    """2026-10-19: a health entry with a sentence in shape_flags crashed every
    watch tick, which stopped alerts, study refreshes and the press poll."""

    def test_a_sentence_flag_is_reported_not_fatal(self):
        report = {"equity_extractors": [{"dataset": "x", "shape_flags": [
            "500 of 500 members not checked", {"table": "t", "column": "c", "detail": "gone"}]}]}
        found = refresh.problems(report)
        keys = [k for k, _ in found]
        self.assertTrue(any(k.startswith("equity/x:flag:") for k in keys), keys)
        self.assertIn("equity/x:shape:t.c", keys)

