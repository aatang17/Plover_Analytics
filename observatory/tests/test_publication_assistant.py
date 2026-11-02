# -*- coding: utf-8 -*-
"""Each publication's own Assistant Settings: skills, house style, connectors.

What is proved: a staff store from before publications had their own skills
is upgraded in place with every skill kept as PloverResearch's; a new
publication starts with its own nine starter skills; a writer in it sees that
publication's skills in the AI tab and its house style reaches the model, not
PloverResearch's; editing or deleting a skill in one publication leaves the
other alone, and an id from another publication is refused; writers cannot
change the setup, editors change skills and the house style, only owners
change connectors; a run on one publication's article is offered only its
connectors, and its credentials file holds no other publication's key; a
writer who owns no publication opens one of their own, for themselves only,
and an owner opens no second.
"""
import json
import os
import sqlite3
import tempfile
import pathlib

from app import connectors, research, research_ai, skills, staff
from app.assistant import mcp_client, store

from tests.test_publications import PubBase

STARTERS = ["Brainstorm", "Plan", "Draft", "Find Charts", "Edit", "Review", "Fact Check",
            "Headlines", "Data Flash"]


class AssistantBase(PubBase):
    def setUp(self):
        PubBase.setUp(self)
        # the writer's memory and past runs live here: never the real file, or
        # one another test file left behind
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_store = store.DB_PATH
        store.reset_for_tests(os.path.join(tmp.name, "workspace.db"))
        self.addCleanup(store.reset_for_tests, old_store)
        self.app.include_router(research_ai.router)
        from app import ai_setup_api
        self.app.include_router(ai_setup_api.router)
        self._secret = os.environ.get("ASSISTANT_SECRET")
        os.environ["ASSISTANT_SECRET"] = "test-secret-" + "x" * 32
        self._client = (mcp_client.connect, mcp_client.call_tool, mcp_client.check_url)
        mcp_client.check_url = lambda u: u
        mcp_client.connect = lambda server, secret=None: ({"name": server["url"]}, [
            {"name": "search", "description": "Search.", "inputSchema": {"type": "object"}}])
        mcp_client.call_tool = lambda server, name, args, secret=None: (json.dumps({"key": secret}), False)
        self.addCleanup(self._undo)
        skills._refreshed.clear()
        made = self.open_pub()
        self.pub = made["publication"]
        self.olive = self.sign_in(made["link"])

    def _undo(self):
        mcp_client.connect, mcp_client.call_tool, mcp_client.check_url = self._client
        if self._secret is None:
            os.environ.pop("ASSISTANT_SECRET", None)
        else:
            os.environ["ASSISTANT_SECRET"] = self._secret

    def path(self, tail="", pub=None):
        return "/admin/api/write/publications/%d/assistant%s" % ((pub or self.pub)["id"], tail)

    def member(self, email, name, role):
        r = self.olive.post("/admin/api/write/publications/%d/members" % self.pub["id"],
                            json={"email": email, "name": name, "role": role})
        self.assertEqual(r.status_code, 200, r.text)
        return self.sign_in(r.json()["link"])


class SetupTest(AssistantBase):
    def test_new_publication_has_its_own_starters(self):
        got = self.olive.get(self.path()).json()
        self.assertEqual([s["name"] for s in got["skills"]], STARTERS)
        self.assertTrue(got["can_edit"] and got["can_manage"])
        self.assertEqual(got["house_style"], skills.HOUSE_STYLE)
        home_ids = set(s["id"] for s in skills.list_skills(publication_id=research.HOME))
        self.assertFalse(home_ids & set(s["id"] for s in got["skills"]))      # copies, not shared rows

    def test_skill_changes_stay_in_their_publication(self):
        mine = dict((s["name"], s) for s in self.olive.get(self.path()).json()["skills"])
        r = self.olive.put(self.path("/skills/%d" % mine["Plan"]["id"]), json={"instructions": "Asia steps."})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.olive.delete(self.path("/skills/%d" % mine["Review"]["id"])).status_code, 200)
        made = self.olive.post(self.path("/skills"), json={
            "name": "Yen Note", "description": "A short yen note.", "instructions": "Steps.", "writes": False})
        self.assertEqual(made.status_code, 200, made.text)
        home = dict((s["name"], s) for s in skills.list_skills(publication_id=research.HOME))
        self.assertIn("Review", home)
        self.assertNotIn("Yen Note", home)
        self.assertNotEqual(home["Plan"]["instructions"], "Asia steps.")
        # the same name may exist in two publications
        self.assertEqual(self.w.post("/admin/api/ai/skills", json={
            "name": "Yen Note", "description": "Ours.", "instructions": "Ours."}).status_code, 200)
        # another publication's skill is not there, whatever its id
        theirs = home["Plan"]["id"]
        self.assertEqual(self.olive.put(self.path("/skills/%d" % theirs), json={"instructions": "x"}).status_code, 400)
        self.assertEqual(self.olive.delete(self.path("/skills/%d" % theirs)).status_code, 404)
        self.assertIsNotNone(skills.get(theirs, research.HOME))
        self.assertEqual(self.w.delete("/admin/api/ai/skills/%d" % mine["Plan"]["id"]).status_code, 404)

    def test_house_style_reaches_its_own_runs(self):
        r = self.olive.put(self.path("/house-style"), json={"text": "Two decimals for yields."})
        self.assertEqual(r.status_code, 200, r.text)
        wendy = self.member("wendy@example.com", "Wendy Writer", "writer")
        a = wendy.post("/admin/api/research/articles", json={"publication_id": self.pub["id"]}).json()
        view = wendy.get("/admin/api/research/ai?article=%d" % a["id"]).json()
        self.assertEqual(view["publication_id"], self.pub["id"])
        self.assertFalse(view["can_edit_setup"])
        self.assertEqual([s["id"] for s in view["skills"]],
                         [s["id"] for s in skills.list_skills(True, self.pub["id"])])
        person = staff.get_by_email("wendy@example.com")
        system = research_ai._system(research.get(a["id"]), person)
        self.assertIn("Two decimals for yields.", system)
        self.assertNotIn(skills.HOUSE_STYLE[:60], system)
        ours = research_ai._system(research.get(self.new_article()["id"]), staff.get_by_email("w@example.com"))
        self.assertIn(skills.HOUSE_STYLE[:60], ours)
        # a run cannot pick another publication's skill by its id
        home_plan = skills.get("plan", research.HOME)
        with self.assertRaises(research_ai.AIError):
            research_ai.start(person, a["id"], "Go.", skill_id=home_plan["id"])

    def test_who_may_change_what(self):
        wendy = self.member("wendy@example.com", "Wendy Writer", "writer")
        eddie = self.member("eddie@example.com", "Eddie Editor", "editor")
        plan = skills.get("plan", self.pub["id"])
        body = {"name": "X", "description": "X.", "instructions": "X."}
        self.assertTrue(wendy.get(self.path()).json()["skills"])               # writers see them
        self.assertEqual(wendy.post(self.path("/skills"), json=body).status_code, 403)
        self.assertEqual(wendy.put(self.path("/skills/%d" % plan["id"]), json={"instructions": "x"}).status_code, 403)
        self.assertEqual(wendy.put(self.path("/house-style"), json={"text": "x"}).status_code, 403)
        self.assertEqual(eddie.post(self.path("/skills"), json=body).status_code, 200)
        self.assertEqual(eddie.put(self.path("/house-style"), json={"text": "Ours."}).status_code, 200)
        conn = {"label": "Notes", "url": "https://notes.example.com/mcp"}
        self.assertEqual(eddie.post(self.path("/connectors"), json=conn).status_code, 403)
        self.assertEqual(wendy.post(self.path("/connectors"), json=conn).status_code, 403)
        self.assertEqual(self.olive.post(self.path("/connectors"), json=conn).status_code, 200)
        # a non-member is told nothing
        self.assertEqual(self.anon.get(self.path()).status_code, 401)


class ConnectorTest(AssistantBase):
    def test_runs_reach_only_their_publications_connectors(self):
        home = self.w.post("/admin/api/ai/connectors", json={
            "label": "Plover Notes", "url": "https://plover.example.com/mcp", "auth_kind": "bearer",
            "key": "plover-secret-key"}).json()
        r = self.olive.post(self.path("/connectors"), json={
            "label": "Olive Notes", "url": "https://olive.example.com/mcp", "auth_kind": "bearer",
            "key": "olive-secret-key"})
        self.assertEqual(r.status_code, 200, r.text)
        mine = r.json()
        self.assertNotIn("olive-secret-key", r.text)
        self.assertEqual([c["label"] for c in self.olive.get(self.path()).json()["connectors"]], ["Olive Notes"])
        self.assertEqual([c["label"] for c in self.w.get("/admin/api/ai/setup").json()["connectors"]],
                         ["Plover Notes"])

        theirs = research.create("olive@example.com", None, self.pub["id"])
        ours = self.new_article()
        olive = staff.get_by_email("olive@example.com")
        ctx = {"person": olive, "article_id": theirs["id"]}
        names = [t["name"] for t in research_ai.tool_list(ctx)]
        self.assertIn(connectors.tool_name(mine["id"], "search"), names)
        self.assertNotIn(connectors.tool_name(home["id"], "search"), names)
        creds = research_ai._run_creds(olive, research_ai._publication(ctx))
        self.assertEqual(creds["connector_keys"], {str(mine["id"]): "olive-secret-key"})
        # calling the other publication's tool by name finds nothing
        text, err = research_ai.call_tool(dict(ctx, client="Codex"), connectors.tool_name(home["id"], "search"), {})
        self.assertTrue(err)
        text, err = research_ai.call_tool(dict(ctx, client="Codex"), connectors.tool_name(mine["id"], "search"), {})
        self.assertFalse(err, text)
        self.assertEqual(json.loads(text)["key"], "olive-secret-key")
        self.assertTrue(connectors.call(home["id"], "search", {}, publication_id=self.pub["id"])[1])
        # PloverResearch's runs see only Plover's
        w = staff.get_by_email("w@example.com")
        ours_names = [t["name"] for t in research_ai.tool_list({"person": w, "article_id": ours["id"]})]
        self.assertIn(connectors.tool_name(home["id"], "search"), ours_names)
        self.assertNotIn(connectors.tool_name(mine["id"], "search"), ours_names)
        # neither page reaches the other's connector
        self.assertEqual(self.olive.delete(self.path("/connectors/%d" % home["id"])).status_code, 404)
        self.assertEqual(self.olive.post(self.path("/connectors/%d/check" % home["id"])).status_code, 404)
        self.assertEqual(self.w.delete("/admin/api/ai/connectors/%d" % mine["id"]).status_code, 404)
        self.assertIsNotNone(connectors.get(home["id"]))
        self.assertIsNotNone(connectors.get(mine["id"]))


class OpenOwnTest(AssistantBase):
    def test_a_writer_opens_one_of_their_own(self):
        wendy = self.member("wendy@example.com", "Wendy Writer", "writer")
        me = wendy.get("/admin/api/write/me").json()
        self.assertTrue(me["can_create_publications"])
        self.assertFalse(me["opens_for_others"])
        other = wendy.post("/admin/api/write/publications", json={
            "slug": "for-bob", "name": "Bob", "owner_email": "bob@example.com"})
        self.assertEqual(other.status_code, 403)
        r = wendy.post("/admin/api/write/publications", json={"slug": "wendy-notes", "name": "Wendy Notes"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["publication"]["role"], "owner")
        self.assertIsNone(r.json()["link"])
        again = wendy.post("/admin/api/write/publications", json={"slug": "second", "name": "Second"})
        self.assertEqual(again.status_code, 403)
        new = r.json()["publication"]
        self.assertEqual([s["name"] for s in wendy.get(self.path(pub=new)).json()["skills"]], STARTERS)
        self.assertEqual(research_ai._publication({"person": staff.get_by_email("wendy@example.com")}), new["id"])


class StoreUpgradeTest(AssistantBase):
    def test_old_staff_store_keeps_its_skills_as_ploverresearchs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "staff.db"
        c = sqlite3.connect(str(path))
        # team_skills and team_connectors as released before publications had their own
        c.executescript("""
CREATE TABLE team_skills (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  writes INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 100, seed TEXT, created_at INTEGER,
  created_by TEXT, updated_at INTEGER, updated_by TEXT);
CREATE TABLE team_connectors (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, url TEXT NOT NULL,
  auth_kind TEXT NOT NULL DEFAULT 'none', header_name TEXT, secret_ct TEXT, secret_last4 TEXT,
  enabled INTEGER NOT NULL DEFAULT 1, tools_json TEXT NOT NULL DEFAULT '[]', off_tools_json TEXT NOT NULL DEFAULT '[]',
  server_name TEXT, checked_at INTEGER, last_error TEXT, created_at INTEGER, created_by TEXT, updated_at INTEGER,
  updated_by TEXT);
CREATE TABLE team_settings (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO team_skills (id, slug, name, description, instructions, writes, position, updated_by)
  VALUES (7, 'yen-note', 'Yen Note', 'Ours.', 'Our steps.', 0, 5, 'w@example.com');
INSERT INTO team_connectors (id, label, url) VALUES (3, 'Notes', 'https://notes.example.com/mcp');
INSERT INTO team_settings VALUES ('house_style', 'Our style.');
""")
        c.commit()
        c.close()
        old = staff.DB_PATH
        staff.DB_PATH = path
        self.addCleanup(lambda: setattr(staff, "DB_PATH", old))
        kept = skills.get("yen-note", research.HOME)
        self.assertEqual((kept["id"], kept["instructions"], kept["writes"]), (7, "Our steps.", False))
        self.assertEqual(skills.house_style(research.HOME), "Our style.")
        self.assertEqual(connectors.get(3)["publication_id"], research.HOME)
        self.assertIsNone(skills.get("yen-note", 2))
        self.assertEqual(skills.house_style(2), skills.HOUSE_STYLE)
        self.assertFalse(staff.conn().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'team_skills_before_publications'").fetchone())
