"""The front page's task log (app/task_log.py): what is kept, who is counted,
and the summary the admin Traffic page shows."""
import datetime
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import task_log, visits
from app.main import app

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
HOME = ("JP", "NTT Communications")
CLOUD = ("US", "Amazon.com, Inc.")


class TaskLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.real = (visits.ANALYTICS_DIR, visits.SALT_PATH)
        visits.ANALYTICS_DIR = pathlib.Path(self.dir)
        visits.SALT_PATH = visits.ANALYTICS_DIR / "salt"
        self.client = TestClient(app)

    def tearDown(self):
        visits.ANALYTICS_DIR, visits.SALT_PATH = self.real
        shutil.rmtree(self.dir, ignore_errors=True)

    def send(self, body, network=HOME, ua=UA):
        with mock.patch.object(visits.geoip, "lookup", return_value=network):
            got = self.client.post("/api/v1/visit/ping", headers={"user-agent": ua},
                                   content=json.dumps(body))
        self.assertEqual(got.status_code, 204)

    def logged(self):
        out = []
        for path in sorted(visits.ANALYTICS_DIR.glob("tasks-*.jsonl")):
            out += [json.loads(line) for line in path.read_text().splitlines()]
        return out

    def test_a_task_step_is_logged_and_not_counted_as_a_heartbeat(self):
        self.send({"path": "/", "task": "earnings", "step": "open", "from": "menu"})
        self.send({"path": "/", "task": "earnings", "step": 0})
        self.send({"path": "/", "task": "earnings", "step": "result", "pick": "aapl"})
        got = self.logged()
        self.assertEqual([e["step"] for e in got], ["open", 0, "result"])
        self.assertEqual(got[0]["from"], "menu")
        self.assertEqual(got[2]["pick"], "AAPL")
        self.assertTrue(all(e["visitor"] == got[0]["visitor"] for e in got))
        self.assertNotIn("network", got[0])

    def test_only_known_words_pass(self):
        for body in ({"task": "nope", "step": 0},
                     {"task": "earnings", "step": 1},          # the answer step comes as result
                     {"task": "earnings", "step": True},
                     {"task": "earnings", "step": "anything"},
                     {"task": "demo", "step": "research"}):    # there is no research demo
            self.assertIsNone(task_log.clean(body), body)
        self.assertNotIn("pick", task_log.clean({"task": "earnings", "step": "result",
                                                 "pick": "<script>"}))
        self.assertNotIn("from", task_log.clean({"task": "chart", "step": "open", "from": "x"}))
        self.assertEqual(task_log.clean({"task": "demo", "step": "chart"}),
                         {"task": "demo", "step": "chart"})

    def test_robots_data_centres_and_the_team_are_left_out(self):
        self.send({"task": "chart", "step": 0}, ua="Googlebot/2.1")
        self.send({"task": "chart", "step": 0}, network=CLOUD)
        with mock.patch.object(visits.geoip, "lookup", return_value=HOME):
            self.client.post("/api/v1/visit/ping", content=json.dumps({"task": "chart", "step": 0}),
                             headers={"user-agent": UA, "cookie": visits.TEAM_COOKIE + "=1"})
        self.assertEqual(self.logged(), [])

    def test_summary_counts_readers_steps_answers_and_demos(self):
        day = datetime.date(2026, 11, 4)
        lines = [
            ("a", "screen", "open", {"from": "menu"}), ("a", "screen", 0, {}), ("a", "screen", 1, {}),
            ("a", "screen", 2, {}), ("a", "screen", "result", {}), ("a", "screen", "result", {}),
            ("b", "screen", "open", {"from": "demo"}), ("b", "screen", 0, {}),
            ("c", "screen", "result", {}),                      # a link straight to the answer
            ("c", "earnings", "result", {"pick": "NVDA"}), ("a", "earnings", "result", {"pick": "NVDA"}),
            ("a", "earnings", "failed", {}),
            ("a", "demo", "chart", {}), ("b", "demo", "chart", {}), ("b", "demo", "chart", {}),
        ]
        visits.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(visits.ANALYTICS_DIR / "tasks-2026-11.jsonl"), "w") as f:
            for who, task, step, extra in lines:
                event = {"at": "2026-11-04T01:00:00Z", "visitor": who, "task": task, "step": step}
                event.update(extra)
                f.write(json.dumps(event) + "\n")
            f.write("not json\n")
            f.write(json.dumps({"at": "2026-09-01T00:00:00Z", "visitor": "z", "task": "screen", "step": 0}) + "\n")
        out = task_log.summary(7, today=day)
        screen = [t for t in out["tasks"] if t["key"] == "screen"][0]
        self.assertEqual(screen["readers"], 3)
        self.assertEqual(screen["result_readers"], 2)
        self.assertEqual(screen["results"], 3)
        self.assertEqual([s["readers"] for s in screen["steps"]], [2, 1, 1, 2])
        self.assertEqual(screen["from"], {"menu": 1, "demo": 1, "link": 0})
        earnings = [t for t in out["tasks"] if t["key"] == "earnings"][0]
        self.assertEqual(earnings["picks"], [{"key": "NVDA", "results": 2}])
        self.assertEqual(earnings["failed"], 1)
        chart = [d for d in out["demos"] if d["key"] == "chart"][0]
        self.assertEqual((chart["clicks"], chart["readers"]), (3, 2))
        self.assertEqual(out["since"], "2026-11-04")

    def test_the_admin_summary_carries_the_tasks(self):
        from app import admin_api
        with mock.patch.dict("os.environ", {"ADMIN_PASSWORD": "pw-for-test"}), \
                mock.patch.object(admin_api, "AUDIT_PATH", pathlib.Path(self.dir) / "audit.jsonl"):
            client = TestClient(app)
            client.post("/admin/api/login", json={"password": "pw-for-test"})
            got = client.get("/admin/api/visits?days=7")
        self.assertEqual(got.status_code, 200, got.text[:200])
        self.assertEqual([t["key"] for t in got.json()["tasks"]["tasks"]],
                         ["research", "earnings", "chart", "screen", "insiders"])


if __name__ == "__main__":
    unittest.main()
