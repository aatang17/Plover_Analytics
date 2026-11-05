# -*- coding: utf-8 -*-
"""A publication's own topics (app/pub_topics.py).

What is proved: a publication starts with the default twelve; a writer adds
one from the editor and an editor removes one; the editor gets the list with
the article; an article keeps only its publication's topics; a published
article shows a topic's name on its page, even after the topic is removed;
another publication never sees it.
"""
from app import pub_topics, research_doc as rd, staff
from tests.test_publications import PubBase


class PubTopicsTest(PubBase):

    def setUp(self):
        PubBase.setUp(self)
        made = self.open_pub()
        self.pid = made["publication"]["id"]
        self.olive = self.sign_in(made["link"])
        inv = self.olive.post("/admin/api/write/publications/%d/members" % self.pid,
                              json={"email": "wendy@example.com", "name": "Wendy Writer", "role": "writer"})
        self.wendy = self.sign_in(inv.json()["link"])

    def url(self, tail=""):
        return "/admin/api/write/publications/%d/topics%s" % (self.pid, tail)

    def test_add_use_remove(self):
        self.assertEqual([t["key"] for t in pub_topics.topics(self.pid)], rd.TOPIC_KEYS)
        self.assertEqual(self.wendy.post(self.url(), json={"label": "  "}).status_code, 400)
        self.assertEqual(self.wendy.post(self.url(), json={"label": "円"}).status_code, 400)
        r = self.wendy.post(self.url(), json={"label": "Energy & Power"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["added"], "energy-power")
        self.assertFalse(r.json()["can_remove"])
        again = self.wendy.post(self.url(), json={"label": "energy  & power"}).json()
        self.assertEqual(len(again["topics"]), 13)                     # no second copy

        # the editor gets the list with the article; the article keeps only known topics
        a = self.olive.post("/admin/api/research/articles", json={"publication_id": self.pid}).json()
        self.assertIn("energy-power", [t["key"] for t in a["publication"]["topics"]] if "publication" in a
                      else [t["key"] for t in self.olive.get("/admin/api/research/articles/%d" % a["id"])
                            .json()["publication"]["topics"]])
        olive_id = staff.get_by_email("olive@example.com")["id"]
        saved = self.olive.put("/admin/api/research/articles/%d" % a["id"], json={
            "draft": self.complete_draft(authors=[olive_id], topics=["energy-power", "made-up", "banks"]),
            "base_revision": a["revision"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["draft"]["topics"], ["energy-power", "banks"])
        self.assertEqual(self.publish_as(self.olive, a["id"]).status_code, 200)
        page = self.anon.get("/p/asia-notes/regional-banks-rate-path").text
        self.assertIn("Energy &amp; Power", page)

        # only editors remove; the published page keeps the name
        self.assertEqual(self.wendy.delete(self.url("/energy-power")).status_code, 403)
        r = self.olive.delete(self.url("/energy-power"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("energy-power", [t["key"] for t in r.json()["topics"]])
        self.assertIn("Energy &amp; Power", self.anon.get("/p/asia-notes/regional-banks-rate-path").text)
        # adding it again brings it back
        back = self.olive.post(self.url(), json={"label": "Energy & Power"}).json()
        self.assertIn("energy-power", [t["key"] for t in back["topics"]])

        # another publication never sees it
        self.assertNotIn("energy-power", [t["key"] for t in pub_topics.topics(1)])
