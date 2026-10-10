# -*- coding: utf-8 -*-
"""Publications on PloverResearch: more than one, each with its own writers.

What is proved: a store from before publications is upgraded in place with
every article, number and published version kept; the Plover team opens a
publication and its owner gets a sign-in link that works without any email
provider, even while sign-in is invitation-only; a writer sees and edits only
their own articles, cannot publish, and can ask an editor to; an article
outside a person's publications does not exist for them (404), over the API
and over their AI key; each publication has its own addresses, front page,
feed and Markdown, says the views are its writers' own, and never appears on
PloverResearch's pages; the same address can be used in two publications;
a publication always keeps an owner. Each publication's own skills, house
style and connectors are proved in test_publication_assistant.py.
"""
import os
import pathlib
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import accounts, research, staff, writer_api, writers
from tests.test_research import Base


class PubBase(Base):
    def setUp(self):
        Base.setUp(self)
        root = pathlib.Path(tempfile.mkdtemp())
        self._acct = (accounts.DB_PATH, os.environ.get("ACCOUNTS_ENABLED"),
                      os.environ.get("ACCOUNTS_ALLOWED_EMAILS"))
        accounts.DB_PATH = root / "workspace.db"
        accounts._conn = None
        os.environ["ACCOUNTS_ENABLED"] = "1"
        # invitation-only, as in production: only this address is on the list
        os.environ["ACCOUNTS_ALLOWED_EMAILS"] = "someone-else@example.com"
        self.addCleanup(self._restore_accounts)
        self.app.include_router(writer_api.router)
        self.app.include_router(accounts.router)
        from app import research_mcp
        self.app.include_router(research_mcp.router)

    def _restore_accounts(self):
        accounts.DB_PATH = self._acct[0]
        accounts._conn = None
        for key, val in (("ACCOUNTS_ENABLED", self._acct[1]),
                         ("ACCOUNTS_ALLOWED_EMAILS", self._acct[2])):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def open_pub(self, slug="asia-notes", owner="olive@example.com", name="Olive Owner"):
        r = self.w.post("/admin/api/write/publications", json={
            "slug": slug, "name": "Asia Notes", "tagline": "Notes on Asian markets.",
            "owner_email": owner, "owner_name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def sign_in(self, link):
        """Follow a sign-in link the way signin.html does."""
        token = link.split("token=")[1].split("&")[0]
        c = TestClient(self.app)
        r = c.post("/api/v1/account/session", json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["returnTo"], "write.html")
        return c

    def publish_as(self, client, aid):
        a = client.get("/admin/api/research/articles/%d" % aid).json()
        return client.post("/admin/api/research/articles/%d/publish" % aid,
                           json={"base_revision": a["revision"], "change_note": ""})


class _Req(object):
    """Enough of a request for a link's base address."""
    base_url = "http://testserver/"
    headers = {}

    class url(object):
        scheme = "http"


class MigrationTest(unittest.TestCase):
    def test_old_store_upgrades_in_place(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_dir = research.DATA_DIR
        self.addCleanup(lambda: setattr(research, "DATA_DIR", old_dir))
        research.DATA_DIR = pathlib.Path(tmp.name)
        research.DATA_DIR.mkdir(exist_ok=True)
        c = sqlite3.connect(str(research.db_path()))
        # the schema as released before publications
        c.executescript("""
CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE, draft TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft', created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL, updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL, published_version INTEGER,
  withdrawn_at INTEGER, withdrawn_by TEXT, withdrawn_reason TEXT);
CREATE TABLE article_versions (article_id INTEGER NOT NULL, version INTEGER NOT NULL, slug TEXT NOT NULL,
  published_at TEXT NOT NULL, published_by TEXT NOT NULL, title TEXT NOT NULL, dek TEXT NOT NULL,
  summary TEXT NOT NULL, authors TEXT NOT NULL, doc TEXT NOT NULL, snapshots TEXT NOT NULL,
  body_html TEXT NOT NULL, markdown TEXT NOT NULL, change_note TEXT NOT NULL, sha256 TEXT NOT NULL,
  PRIMARY KEY (article_id, version));
CREATE TRIGGER article_versions_no_update BEFORE UPDATE ON article_versions
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;
CREATE TRIGGER article_versions_no_delete BEFORE DELETE ON article_versions
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;
INSERT INTO articles VALUES (1, 'kept', '{"title": "Kept"}', 4, 'published', 1, 'a@x', 2, 'a@x', 1, NULL, NULL, NULL);
INSERT INTO articles VALUES (2, NULL, '{"title": "Draft"}', 1, 'draft', 1, 'a@x', 2, 'a@x', NULL, NULL, NULL, NULL);
INSERT INTO articles VALUES (3, NULL, '{"title": "Gone"}', 1, 'draft', 1, 'a@x', 2, 'a@x', NULL, NULL, NULL, NULL);
DELETE FROM articles WHERE id = 3;
INSERT INTO article_versions VALUES (1, 1, 'kept', '2026-10-01T00:00:00Z', 'a@x', 'Kept', '', 's', '[]',
  '{}', '{}', '<p>x</p>', 'x', 'First published.', 'abc');
""")
        c.commit()
        c.close()
        research._base = None
        self.addCleanup(lambda: setattr(research, "_base", None))
        rows = research.conn().execute("SELECT id, publication_id, slug, revision FROM articles "
                                       "ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, 1, "kept", 4), (2, 1, None, 1)])
        self.assertEqual(research.version(1, 1)["body_html"], "<p>x</p>")
        with self.assertRaises(sqlite3.DatabaseError):          # still immutable
            research.conn().execute("UPDATE article_versions SET title = 'x'")
        # the deleted draft's number is not handed out again
        a = research.create("a@x")
        self.assertEqual(a["id"], 4)
        self.assertEqual(research.publication(research.HOME)["name"], "PloverResearch")


class PublicationTest(PubBase):
    def test_team_opens_a_publication_and_its_owner_signs_in_by_link(self):
        made = self.open_pub()
        self.assertEqual(made["publication"]["base"], "/p/asia-notes")
        self.assertIn("signin.html?token=", made["link"])
        self.assertIn("returnTo=write.html", made["link"])
        owner = self.sign_in(made["link"])           # not on ACCOUNTS_ALLOWED_EMAILS: admitted as a member
        me = owner.get("/admin/api/write/me").json()
        self.assertTrue(me["writer"])
        self.assertEqual([(p["name"], p["role"]) for p in me["publications"]], [("Asia Notes", "owner")])
        self.assertFalse(me["can_create_publications"])
        # an owner of their own publication is nobody in the admin console
        self.assertEqual(owner.get("/admin/api/research/articles?publication_id=1").status_code, 403)
        self.assertNotIn(1, writers.roles(staff.get_by_email("olive@example.com")))

    def test_an_owner_opens_no_second_and_addresses_are_checked(self):
        made = self.open_pub()
        owner = self.sign_in(made["link"])
        r = owner.post("/admin/api/write/publications", json={
            "slug": "mine", "name": "Mine", "owner_email": "olive@example.com"})
        self.assertEqual(r.status_code, 403)
        bad = self.w.post("/admin/api/write/publications", json={
            "slug": "research", "name": "X", "owner_email": "w@example.com"})
        self.assertEqual(bad.status_code, 400)                        # reserved address
        dup = self.w.post("/admin/api/write/publications", json={
            "slug": "asia-notes", "name": "X", "owner_email": "w@example.com"})
        self.assertEqual(dup.status_code, 400)

    def test_writer_writes_own_cannot_publish_and_asks(self):
        made = self.open_pub()
        pub = made["publication"]
        olive = self.sign_in(made["link"])
        inv = olive.post("/admin/api/write/publications/%d/members" % pub["id"],
                         json={"email": "Wendy@Example.com", "name": "Wendy Writer", "role": "writer"})
        self.assertEqual(inv.status_code, 200, inv.text)
        wendy = self.sign_in(inv.json()["link"])

        own = olive.post("/admin/api/research/articles", json={"publication_id": pub["id"]}).json()
        mine = wendy.post("/admin/api/research/articles", json={"publication_id": pub["id"]}).json()
        self.assertEqual(mine["publication"]["base"], "/p/asia-notes")
        self.assertEqual(mine["can"], {"edit": True, "publish": False, "delete": True})
        listed = [a["id"] for a in wendy.get("/admin/api/research/articles").json()["articles"]]
        self.assertEqual(listed, [mine["id"]])                        # not the owner's article
        self.assertEqual(wendy.get("/admin/api/research/articles/%d" % own["id"]).status_code, 404)

        r = wendy.put("/admin/api/research/articles/%d" % mine["id"],
                      json={"draft": self.complete_draft(authors=[mine["authors"][0]]),
                            "base_revision": mine["revision"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.publish_as(wendy, mine["id"]).status_code, 403)
        asked = wendy.post("/admin/api/research/articles/%d/review" % mine["id"]).json()
        self.assertIn("wendy@example.com", asked["review_requested_by"])
        done = self.publish_as(olive, mine["id"])
        self.assertEqual(done.status_code, 200, done.text)
        self.assertTrue(done.json()["url"].endswith("/p/asia-notes/regional-banks-rate-path"))
        self.assertIsNone(research.get(mine["id"])["review_requested_at"])   # cleared on publish
        # Plover's own articles are not hers, and PloverResearch's pages do not carry hers
        self.assertNotIn("regional-banks-rate-path", self.anon.get("/research").text)

    def test_pages_of_a_publication(self):
        made = self.open_pub()
        pub = made["publication"]
        olive = self.sign_in(made["link"])
        a = olive.post("/admin/api/research/articles", json={"publication_id": pub["id"]}).json()
        olive_id = staff.get_by_email("olive@example.com")["id"]
        olive.put("/admin/api/research/articles/%d" % a["id"],
                  json={"draft": self.complete_draft(authors=[olive_id]), "base_revision": a["revision"]})
        self.assertEqual(self.publish_as(olive, a["id"]).status_code, 200)
        # the same address in PloverResearch is still free
        b = self.new_article()
        self.save(b["id"], self.complete_draft(), b["revision"])
        self.assertEqual(self.publish_as(self.w, b["id"]).json()["version"], 1)

        page = self.anon.get("/p/asia-notes/regional-banks-rate-path")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Olive Owner", page.text)
        self.assertIn("The views are the authors", page.text)
        self.assertIn('href="/p/asia-notes"', page.text)
        self.assertIn("/p/asia-notes/regional-banks-rate-path/v1", page.text)
        self.assertNotIn("Subscribe on Substack", page.text)          # PloverResearch's list, not theirs
        home = self.anon.get("/p/asia-notes")
        self.assertIn("Asia Notes", home.text)
        self.assertIn("Notes on Asian markets.", home.text)
        self.assertIn("regional-banks-rate-path", self.anon.get("/p/asia-notes/feed.xml").text)
        md = self.anon.get("/p/asia-notes/regional-banks-rate-path.md").text
        self.assertIn("Asia Notes, version 1", md)
        self.assertEqual(self.anon.get("/p/asia-notes/regional-banks-rate-path/v1").status_code, 200)
        self.assertEqual(self.anon.get("/p/nope").status_code, 404)
        self.assertEqual(self.anon.get("/p/asia-notes/nope").status_code, 404)
        r = self.anon.get("/p/ploverresearch", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (301, "/research"))
        sitemap = self.anon.get("/sitemap-research.xml").text
        self.assertIn("/p/asia-notes/regional-banks-rate-path", sitemap)
        self.assertIn("/research/regional-banks-rate-path", sitemap)
        self.assertNotIn("Olive", self.anon.get("/research/regional-banks-rate-path").text)

    def test_a_publication_keeps_an_owner(self):
        made = self.open_pub()
        url = "/admin/api/write/publications/%d/members" % made["publication"]["id"]
        # the Plover team opened it but is not a member of it
        self.assertEqual(self.w.delete(url + "?email=olive@example.com").status_code, 404)
        olive = self.sign_in(made["link"])
        self.assertEqual(olive.delete(url + "?email=olive@example.com").status_code, 400)
        self.assertEqual(olive.put(url, json={"email": "olive@example.com", "role": "writer"}).status_code,
                         400)
        r = olive.post(url, json={"email": "e@example.com", "name": "Ed", "role": "owner"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(olive.put(url, json={"email": "olive@example.com", "role": "editor"}).status_code,
                         200)
        # now an editor, she cannot manage members; the new owner can, and a
        # fresh link for a member who lost theirs works
        self.assertEqual(olive.post(url + "/link", json={"email": "e@example.com"}).status_code, 403)
        ed = self.sign_in(r.json()["link"])
        again = ed.post(url + "/link", json={"email": "olive@example.com"})
        self.assertEqual(self.sign_in(again.json()["link"]).get("/admin/api/write/me").json()["email"],
                         "olive@example.com")
        self.assertEqual(ed.post(url + "/link", json={"email": "nobody@example.com"}).status_code, 404)

    def test_outside_writer_ai_key_sees_only_their_publication(self):
        from app import research_mcp
        pub = self.open_pub()["publication"]
        olive = self.sign_in(writers.signin_link(_Req(), "olive@example.com"))
        mine = olive.post("/admin/api/research/articles", json={"publication_id": pub["id"]}).json()
        plover = self.new_article()
        key = olive.post("/admin/api/write/keys", json={"label": "laptop"}).json()["token"]
        h = {"Authorization": "Bearer " + key}

        def call(name, args):
            r = self.anon.post("/mcp/research", headers=h, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": args}})
            return r.json()["result"]

        listed = call("list_articles", {})
        self.assertIn('"publication": "asia-notes"', listed["content"][0]["text"])
        self.assertNotIn('"id": %d,' % plover["id"], listed["content"][0]["text"])
        self.assertTrue(call("read_article", {"article_id": plover["id"]})["isError"])
        self.assertFalse(call("read_article", {"article_id": mine["id"]})["isError"])
        made = call("create_draft", {"markdown": "# A note\n\nText."})
        self.assertFalse(made["isError"], made)
        self.assertIn("A note", [a["title"] for a in research.list_articles(pub["id"])])
        del research_mcp

    def test_signin_allow_list_admits_members_only(self):
        self.assertFalse(accounts._allowed("stranger@example.com"))
        self.open_pub()
        self.assertTrue(accounts._allowed("olive@example.com"))
        self.assertTrue(accounts._allowed("w@example.com"))           # PloverResearch via the Team page


class EmailedInviteTest(PubBase):
    """With a mailbox configured, invitations go by email and the owner never
    handles the link; the writer later signs in from the site with their
    address alone."""

    def setUp(self):
        PubBase.setUp(self)
        from app import mailer
        self.sent = []
        old = (mailer.send, os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM"))

        def fake(to, subject, text, html=None, timeout=10, reply_to=None):
            self.sent.append({"to": to, "subject": subject, "text": text, "html": html,
                              "reply_to": reply_to})
            return "sent"

        def restore():
            mailer.send = old[0]
            for key, val in (("RESEND_API_KEY", old[1]), ("RESEND_FROM", old[2])):
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
        self.addCleanup(restore)
        mailer.send = fake
        os.environ["RESEND_API_KEY"] = "test"
        os.environ["RESEND_FROM"] = "Plover <noreply@example.com>"

    def link_in(self, mail):
        return [w for w in mail["text"].split() if "signin.html?token=" in w][0]

    def test_invitation_is_emailed_and_the_writer_signs_in_again_from_the_site(self):
        made = self.open_pub()
        self.assertTrue(made["emailed"])
        self.assertIsNone(made["link"])                               # not shown to the opener
        self.assertEqual(self.sent[0]["to"], "olive@example.com")
        olive = self.sign_in(self.link_in(self.sent[0]))
        self.assertEqual(olive.get("/admin/api/write/me").json()["sign_in"]["email_delivery"], True)

        pub = made["publication"]
        inv = olive.post("/admin/api/write/publications/%d/members" % pub["id"],
                         json={"email": "wendy@example.com", "name": "Wendy Writer", "role": "writer"})
        self.assertEqual(inv.status_code, 200, inv.text)
        self.assertEqual((inv.json()["emailed"], inv.json()["link"]), (True, None))
        mail = self.sent[-1]
        self.assertEqual(mail["to"], "wendy@example.com")
        self.assertEqual(mail["reply_to"], "olive@example.com")      # replies reach the inviter
        self.assertIn("Olive Owner invited you to write for Asia Notes", mail["subject"])
        self.assertIn("as a writer", mail["text"])
        self.assertIn("write.html and enter this email address", mail["text"])
        self.assertIn('<a href="http://testserver/signin.html?token=', mail["html"])
        wendy = self.sign_in(self.link_in(mail))
        self.assertTrue(wendy.get("/admin/api/write/me").json()["writer"])

        # later, from the site: the Writer Desk's "Email Me a Sign-In Link"
        r = TestClient(self.app).post("/api/v1/account/signin-link",
                                      json={"email": "wendy@example.com", "returnTo": "write.html"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.sent[-1]["to"], "wendy@example.com")
        self.assertTrue(self.sign_in(self.link_in(self.sent[-1]))
                        .get("/admin/api/write/me").json()["writer"])

        # a fresh link from the Members page is emailed too
        again = olive.post("/admin/api/write/publications/%d/members/link" % pub["id"],
                           json={"email": "wendy@example.com"})
        self.assertEqual((again.json()["emailed"], again.json()["link"]), (True, None))
        self.assertEqual(self.sent[-1]["subject"], "Your sign-in link for Asia Notes")

    def test_a_failed_email_hands_the_link_back(self):
        from app import mailer

        def down(*a, **k):
            raise OSError("provider down")
        mailer.send = down
        made = self.open_pub()
        self.assertFalse(made["emailed"])
        self.sign_in(made["link"])                                    # the owner can still pass it on


if __name__ == "__main__":
    unittest.main()
