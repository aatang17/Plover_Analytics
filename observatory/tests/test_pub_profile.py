# -*- coding: utf-8 -*-
"""Each publication as its own workspace: its AI account, article defaults,
look, market-data keys and coverage list (app/pub_profile.py,
app/pub_coverage.py), with the vendors and the model replaced.

What is proved: only the owner provides the AI and the look, editors set the
defaults and the coverage, writers read; a writer with no AI account of their
own runs on the publication's, and stops when it is withdrawn; the
publication's length replaces the writer's own; a new article starts with the
starting text; the accent and logo reach the public pages and the feed; the
publication's market-data key is used on its articles before a writer's own,
which fills in other vendors, and never reaches another publication; the
coverage list is told to the assistant.
"""
import base64
import os

from app import pub_coverage, pub_profile, research_ai, staff
from app.assistant import gateway, market_data, store
from tests.test_market_data import EOD_GOOD, JQ_GOOD, FakeVendors
from tests.test_publications import PubBase

# a 1x1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class PubProfileTest(PubBase):

    def setUp(self):
        PubBase.setUp(self)
        self._store = store.DB_PATH
        store.reset_for_tests(str(__import__("app").accounts.DB_PATH))
        self._secret = os.environ.get("ASSISTANT_SECRET")
        os.environ["ASSISTANT_SECRET"] = "test-secret-not-for-production"
        self._ping, self._http = gateway.ping, market_data._http
        self.pings = []
        gateway.ping = lambda provider, key, model, base_url=None: self.pings.append((provider, key)) or "OK"
        market_data._http = FakeVendors()
        self.addCleanup(self._back)
        made = self.open_pub()
        self.pid = made["publication"]["id"]
        self.olive = self.sign_in(made["link"])
        inv = self.olive.post("/admin/api/write/publications/%d/members" % self.pid,
                              json={"email": "wendy@example.com", "name": "Wendy Writer", "role": "writer"})
        self.wendy = self.sign_in(inv.json()["link"])
        self.wendy_p = staff.get_by_email("wendy@example.com")

    def _back(self):
        gateway.ping, market_data._http = self._ping, self._http
        store.reset_for_tests(str(self._store))
        if self._secret is None:
            os.environ.pop("ASSISTANT_SECRET", None)
        else:
            os.environ["ASSISTANT_SECRET"] = self._secret

    def url(self, tail):
        return "/admin/api/write/publications/%d/%s" % (self.pid, tail)

    # ---- AI account

    def test_publication_ai_runs_writers_without_their_own(self):
        r = self.wendy.put(self.url("profile/ai"), json={"provider": "openai", "key": "sk-pub-1234"})
        self.assertEqual(r.status_code, 403)
        r = self.olive.put(self.url("profile/ai"), json={"provider": "claude_code", "key": "x"})
        self.assertEqual(r.status_code, 400)                      # a plan is one person's
        r = self.olive.put(self.url("profile/ai"), json={"provider": "openai", "key": "sk-pub-1234"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("sk-pub-1234", r.text)
        self.assertEqual(r.json()["ai"]["key_last4"], "1234")
        s = research_ai.run_settings(self.wendy_p, self.pid)
        self.assertEqual((s["provider"], s["ai_from"]), ("openai", "publication"))
        view = research_ai.settings_view(self.wendy_p, self.pid)
        self.assertEqual(view["publication_ai"]["provider"], "openai")
        # on another publication the writer is on their own
        self.assertEqual(research_ai.run_settings(self.wendy_p, 1)["ai_from"], "writer")
        self.assertEqual(self.olive.delete(self.url("profile/ai")).status_code, 200)
        self.assertEqual(research_ai.run_settings(self.wendy_p, self.pid)["ai_from"], "writer")

    # ---- article defaults

    def test_defaults_length_template_and_skill(self):
        prof = self.olive.get(self.url("profile")).json()
        skill = prof["skills"][0]["id"]
        self.assertEqual(self.wendy.put(self.url("profile/defaults"), json={"length": "short"}).status_code, 403)
        self.assertEqual(self.olive.put(self.url("profile/defaults"), json={"length": "epic"}).status_code, 400)
        r = self.olive.put(self.url("profile/defaults"), json={
            "length": "short", "template": "## Summary\n\nWhat changed.", "default_skill": skill})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(research_ai.run_settings(self.wendy_p, self.pid)["length"], "short")
        self.assertEqual(research_ai.settings_view(self.wendy_p, self.pid)["default_skill"], skill)
        a = self.wendy.post("/admin/api/research/articles", json={"publication_id": self.pid}).json()
        self.assertIn("Summary", str(a["draft"]["blocks"]))
        b = self.wendy.post("/admin/api/research/articles", json={"publication_id": 1})
        if b.status_code == 200:                                  # not a member of PloverResearch
            self.assertNotIn("Summary", str(b.json()["draft"]["blocks"]))

    # ---- look

    def test_look_reaches_pages_and_feed(self):
        up = self.olive.post("/admin/api/research/media", content=PNG, headers={"X-File-Name": "logo.png"})
        self.assertEqual(up.status_code, 200, up.text)
        logo = up.json()["media"] + "." + up.json()["ext"]
        self.assertEqual(self.olive.put(self.url("profile/look"), json={"accent": "neon"}).status_code, 400)
        self.assertEqual(self.wendy.put(self.url("profile/look"), json={"accent": "teal"}).status_code, 403)
        r = self.olive.put(self.url("profile/look"), json={"accent": "teal", "logo": logo})
        self.assertEqual(r.status_code, 200, r.text)
        page = self.olive.get("/p/asia-notes").text
        self.assertIn("var(--obs-pub-teal)", page)
        self.assertIn("/research/media/" + logo, page)
        self.assertIn("<image><url>", self.olive.get("/p/asia-notes/feed.xml").text)
        self.assertNotIn("--obs-pub-teal", self.olive.get("/research").text)   # its own pages only

    # ---- market-data keys

    def test_publication_keys_first_then_the_writers_own(self):
        r = self.wendy.post(self.url("data-sources"), json={"provider": "jquants", "key": JQ_GOOD})
        self.assertEqual(r.status_code, 403)
        r = self.olive.post(self.url("data-sources"), json={"provider": "jquants", "key": JQ_GOOD})
        self.assertEqual(r.status_code, 200, r.text)
        listing = self.wendy.get(self.url("data-sources"))
        self.assertEqual([s["provider"] for s in listing.json()["sources"]], ["jquants"])
        self.assertNotIn(JQ_GOOD, listing.text)
        self.assertFalse(listing.json()["can_manage"])
        self.wendy.post("/admin/api/write/data-sources", json={"provider": "eodhd", "key": EOD_GOOD})
        owners = research_ai._data_owners(self.wendy_p, self.pid)
        m = market_data.merged(owners)
        self.assertEqual(m["jquants"][0], store.pub_owner(self.pid))
        self.assertIsInstance(m["eodhd"][0], int)                 # the writer's own fills in
        self.assertEqual(market_data.keys_for_run(owners), {"jquants": JQ_GOOD, "eodhd": EOD_GOOD})
        # on another publication, only the writer's own
        self.assertEqual(list(market_data.merged(research_ai._data_owners(self.wendy_p, 1))), ["eodhd"])

    # ---- coverage

    def test_coverage_list_and_the_assistant(self):
        from app.assistant import api as aapi
        real = aapi._company
        aapi._company = lambda code: ("Toyota Motor", None) if code == "7203" else (None, None)
        self.addCleanup(lambda: setattr(aapi, "_company", real))
        self.assertEqual(self.wendy.post(self.url("coverage"), json={"code": "7203"}).status_code, 403)
        self.assertEqual(self.olive.post(self.url("coverage"), json={"code": "99"}).status_code, 400)
        self.assertEqual(self.olive.post(self.url("coverage"), json={"code": "0000"}).status_code, 400)
        r = self.olive.post(self.url("coverage"), json={"code": "7203"})
        self.assertEqual(r.status_code, 200, r.text)
        rows = self.wendy.get(self.url("coverage")).json()["companies"]
        self.assertEqual([(x["code"], x["name"]) for x in rows], [("7203", "Toyota Motor")])
        self.assertIn("7203 Toyota Motor", pub_coverage.prompt_line(self.pid))
        self.assertEqual(pub_coverage.prompt_line(1), "")
        self.olive.delete(self.url("coverage/7203"))
        self.assertEqual(pub_coverage.companies(self.pid), [])
