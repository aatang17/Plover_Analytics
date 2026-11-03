# -*- coding: utf-8 -*-
"""A signed-in reader's front-page answer is kept in Your Plover
(app/us_ask_api.py _keep): the first starts a chat titled by the question, a
follow-up naming that chat joins it, and a chat that is not the reader's own
stock chat is never written to — a new one starts instead. No network."""
import os
import tempfile
import unittest

os.environ["ACCOUNTS_ENABLED"] = "1"
os.environ["ASSISTANT_ENABLED"] = "1"
os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"

from app import us_ask_api  # noqa: E402
from app.assistant import store  # noqa: E402

ANSWER = {"recipe": "who_owns", "headline": "BlackRock is the largest reported holder.", "answer": "", "table": None}


class KeepChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(os.path.join(cls.tmp.name, "workspace.db"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_first_starts_a_chat_and_a_follow_up_joins_it(self):
        me = {"id": 7, "email": "reader@example.com"}
        a = us_ask_api._keep(me, 0, "Who owns Nvidia?", ANSWER)
        self.assertEqual(a["title"], "Who owns Nvidia?")
        self.assertEqual(a["url"], "/my.html#chat/%d" % a["chat"])
        b = us_ask_api._keep(me, a["chat"], "and Microsoft?", ANSWER)
        self.assertEqual(b["chat"], a["chat"])
        runs = store.study(7, a["chat"])["runs"]
        self.assertEqual([r["question"] for r in runs], ["and Microsoft?", "Who owns Nvidia?"])
        self.assertEqual({r["by"] for r in runs}, {"stocks"})

    def test_someone_elses_chat_is_never_written_to(self):
        theirs = us_ask_api._keep({"id": 8, "email": "a@example.com"}, 0, "Who owns Apple?", ANSWER)
        mine = us_ask_api._keep({"id": 9, "email": "b@example.com"}, theirs["chat"], "Who owns Tesla?", ANSWER)
        self.assertNotEqual(mine["chat"], theirs["chat"])
        self.assertEqual(len(store.study(8, theirs["chat"])["runs"]), 1)

    def test_a_data_series_study_is_left_alone(self):
        me = {"id": 10, "email": "c@example.com"}
        st = store.add_study(10, "Yields", question="US 10-year")
        store.update_study(10, st["id"], spec={"series": ["ust-yields:10Y"]})
        kept = us_ask_api._keep(me, st["id"], "Who owns Nvidia?", ANSWER)
        self.assertNotEqual(kept["chat"], st["id"])


if __name__ == "__main__":
    unittest.main()
