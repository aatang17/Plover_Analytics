# -*- coding: utf-8 -*-
"""A publication's own email list (app/newsletter.py).

What is proved: a reader signs up on the publication's page and gets nothing
but a confirmation until they press its link; an address already subscribed
gets the same reply as a new one; PloverResearch takes no sign-ups (its
readers use Substack); the owner imports a Substack export, which must be
ticked as consented, and an address that unsubscribed stays unsubscribed;
only the owner sees or changes the list; an editor emails a published
version to the active subscribers only, from the publication's name, with
replies to the sender and an unsubscribe link and headers in every message;
a version is emailed once; an interrupted send resumes without sending
anyone a second copy; the unsubscribe link shows a button (a mail scanner
opening it changes nothing) and the button, or a provider's one-click post,
unsubscribes.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_newsletter
"""
import os
import time

from app import mailer, newsletter, research, staff
from tests.test_publications import PubBase

SUBSTACK_CSV = (
    "email,active_subscription,expiry,plan,email_disabled,created_at\n"
    "ann@example.com,false,,,false,2025-01-01\n"
    "Bob@Example.com,true,,monthly,false,2025-02-01\n"
    "gone@example.com,false,,,true,2025-03-01\n"
    "not-an-address,false,,,false,2025-03-02\n")


class NewsletterTest(PubBase):
    def setUp(self):
        PubBase.setUp(self)
        self.links, self.batches = [], []
        self._saved = (mailer.send_link, mailer.send_batch, mailer.configured,
                       newsletter.PAUSE_SECONDS)
        mailer.send_link = self._fake_link
        mailer.send_batch = lambda msgs, timeout=30: self.batches.append(msgs) or "sent"
        mailer.configured = lambda: True
        newsletter.PAUSE_SECONDS = 0
        newsletter._ip_hits.clear()
        self._from = os.environ.get("RESEND_FROM")
        os.environ["RESEND_FROM"] = "Plover Analytics <hello@ploveranalytics.com>"
        self.addCleanup(self._restore_mail)
        made = self.open_pub()
        self.pub = made["publication"]
        self.olive = self.sign_in(made["link"])

    def _fake_link(self, to, subject, paras, link, button, *rest, **kw):
        """Confirmation emails are caught here; any other email (the owner's
        invitation) is reported unsent, so its link comes back as before."""
        if not subject.startswith("Confirm your subscription"):
            return False
        self.links.append((to, subject, link))
        return True

    def _restore_mail(self):
        (mailer.send_link, mailer.send_batch, mailer.configured,
         newsletter.PAUSE_SECONDS) = self._saved
        if self._from is None:
            os.environ.pop("RESEND_FROM", None)
        else:
            os.environ["RESEND_FROM"] = self._from

    def signup(self, email, client=None):
        return (client or self.anon).post("/p/asia-notes/email/subscribe", json={"email": email})

    def confirm_last(self):
        token = self.links[-1][2].split("t=")[1]
        return self.anon.get("/p/asia-notes/email/confirm?t=" + token)

    def published_article(self):
        a = self.olive.post("/admin/api/research/articles", json={"publication_id": self.pub["id"]}).json()
        olive_id = staff.get_by_email("olive@example.com")["id"]
        self.olive.put("/admin/api/research/articles/%d" % a["id"],
                       json={"draft": self.complete_draft(authors=[olive_id]),
                             "base_revision": a["revision"]})
        self.assertEqual(self.publish_as(self.olive, a["id"]).status_code, 200)
        return a["id"]

    def wait_done(self, aid, version=1):
        for _ in range(200):
            s = newsletter.send_for(aid, version)
            if s and s["status"] == "done":
                return s
            time.sleep(0.02)
        self.fail("the send did not finish")

    def test_signup_confirms_before_anything_is_sent(self):
        r = self.signup("reader@example.com")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Check your inbox", r.json()["message"])
        self.assertEqual(self.links[-1][0], "reader@example.com")
        self.assertIn("/p/asia-notes/email/confirm?t=", self.links[-1][2])
        self.assertEqual(newsletter.counts(self.pub["id"])["pending"], 1)
        page = self.confirm_last()
        self.assertIn("You Are Subscribed", page.text)
        self.assertEqual(newsletter.counts(self.pub["id"])["active"], 1)
        # an address already on the list: same reply, no second email
        sent = len(self.links)
        self.assertEqual(self.signup("Reader@Example.com").json()["message"], r.json()["message"])
        self.assertEqual(len(self.links), sent)
        self.assertEqual(self.signup("not an address").status_code, 400)
        self.assertIn("Link No Longer Valid", self.anon.get("/p/asia-notes/email/confirm?t=nope").text)

    def test_a_refused_confirmation_says_so(self):
        mailer.send_link = lambda *a, **kw: False
        r = self.signup("reader@example.com")
        self.assertEqual(r.status_code, 503)
        self.assertIn("could not be sent", r.json()["message"])
        self.assertEqual(newsletter.counts(self.pub["id"])["pending"], 0)   # not left on the list
        mailer.send_link = self._fake_link
        self.assertEqual(self.signup("reader@example.com").status_code, 200)   # no cooldown left behind

    def test_signup_form_works_without_the_script(self):
        r = self.anon.post("/p/asia-notes/email/subscribe", data={"email": "plain@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Almost Done", r.text)
        self.assertIn("noindex", r.text)

    def test_page_has_the_box_and_plover_research_takes_no_signups(self):
        home = self.anon.get("/p/asia-notes").text
        self.assertIn('action="/p/asia-notes/email/subscribe"', home)
        self.assertIn('href="/p/asia-notes#subscribe"', home)
        self.assertEqual(self.anon.post("/p/ploverresearch/email/subscribe",
                                        json={"email": "x@example.com"}).status_code, 404)
        self.assertNotIn("email/subscribe", self.anon.get("/research").text)

    def test_owner_imports_a_substack_export(self):
        url = "/admin/api/write/publications/%d/subscribers" % self.pub["id"]
        r = self.olive.post(url + "/import", json={"csv": SUBSTACK_CSV})
        self.assertEqual(r.status_code, 400)                       # consent box not ticked
        r = self.olive.post(url + "/import", json={"csv": SUBSTACK_CSV, "confirmed": True})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual((out["added"], out["skipped"]), (2, 2))
        emails = sorted(s["email"] for s in self.olive.get(url).json()["subscribers"])
        self.assertEqual(emails, ["ann@example.com", "bob@example.com"])
        # an address that unsubscribed is never put back by an import
        token = research.conn().execute("SELECT token FROM subscribers WHERE email = 'ann@example.com'"
                                        ).fetchone()[0]
        self.anon.post("/p/asia-notes/email/unsubscribe?t=" + token)
        again = self.olive.post(url + "/import", json={"csv": "ann@example.com\n", "confirmed": True}).json()
        self.assertEqual(again["kept_unsubscribed"], 1)
        self.assertEqual(newsletter.counts(self.pub["id"])["active"], 1)
        csv_text = self.olive.get(url + ".csv").text
        self.assertTrue(csv_text.startswith("email,status,source"))
        self.assertIn("ann@example.com,unsubscribed,import", csv_text)
        r = self.olive.delete(url + "?email=bob@example.com")
        self.assertEqual(r.json()["counts"]["active"], 0)

    def test_only_the_owner_sees_the_list(self):
        url = "/admin/api/write/publications/%d/subscribers" % self.pub["id"]
        # the Plover team member who opened it is not a member of it
        self.assertEqual(self.w.get(url).status_code, 404)
        self.assertEqual(self.anon.get(url).status_code, 401)
        self.assertEqual(self.w.get("/admin/api/write/publications/1/subscribers").status_code, 400)

    def test_email_a_version_to_active_subscribers_once(self):
        self.signup("pending@example.com")                          # never confirmed
        self.signup("reader@example.com")
        self.confirm_last()
        self.olive.post("/admin/api/write/publications/%d/subscribers/import" % self.pub["id"],
                        json={"csv": "imported@example.com\n", "confirmed": True})
        aid = self.published_article()
        st = self.olive.get("/admin/api/research/articles/%d/email" % aid).json()
        self.assertEqual((st["available"], st["subscribers"], st["send"]), (True, 2, None))
        r = self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        self.assertEqual(r.status_code, 200, r.text)
        done = self.wait_done(aid)
        self.assertEqual((done["sent"], done["failed"], done["total"]), (2, 0, 2))
        msgs = [m for b in self.batches for m in b]
        self.assertEqual(sorted(m["to"][0] for m in msgs), ["imported@example.com", "reader@example.com"])
        m = msgs[0]
        self.assertEqual(m["from"], "Asia Notes <hello@ploveranalytics.com>")
        self.assertEqual(m["reply_to"], "olive@example.com")
        self.assertEqual(m["subject"], "Regional banks and the rate path")
        self.assertIn("/p/asia-notes/regional-banks-rate-path", m["html"])
        self.assertIn("/p/asia-notes/email/unsubscribe?t=", m["headers"]["List-Unsubscribe"])
        self.assertEqual(m["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("Unsubscribe: ", m["text"])
        again = self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        self.assertEqual(again.status_code, 400)
        self.assertIn("emailed already", again.json()["detail"])

    def test_interrupted_send_resumes_without_repeats(self):
        url = "/admin/api/write/publications/%d/subscribers/import" % self.pub["id"]
        self.olive.post(url, json={"csv": "a@example.com\nb@example.com\nc@example.com\n",
                                   "confirmed": True})
        aid = self.published_article()
        c = research.conn()
        old = int(time.time()) - 3600
        c.execute("INSERT INTO newsletter_sends (publication_id, article_id, version, subject, created_at, "
                  "created_by, reply_to, status, total, sent, progress_at) "
                  "VALUES (?, ?, 1, 't', ?, 'x', '', 'sending', 3, 1, ?)", (self.pub["id"], aid, old, old))
        sid = c.execute("SELECT id FROM newsletter_sends").fetchone()[0]
        c.execute("INSERT INTO newsletter_items VALUES (?, 'a@example.com', 'sent', ?, NULL)", (sid, old))
        c.commit()
        st = self.olive.get("/admin/api/research/articles/%d/email" % aid).json()
        self.assertEqual(st["send"]["status"], "interrupted")
        self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        done = self.wait_done(aid)
        self.assertEqual(sorted(m["to"][0] for b in self.batches for m in b),
                         ["b@example.com", "c@example.com"])
        self.assertEqual((done["sent"], done["total"]), (3, 3))

    def test_a_refused_key_stops_the_send_and_it_resumes(self):
        import urllib.error
        self.olive.post("/admin/api/write/publications/%d/subscribers/import" % self.pub["id"],
                        json={"csv": "a@example.com\nb@example.com\n", "confirmed": True})
        aid = self.published_article()

        def refuse(msgs, timeout=30):
            raise urllib.error.HTTPError("u", 401, "API key is invalid", {}, None)
        mailer.send_batch = refuse
        self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        for _ in range(200):
            s = newsletter.send_for(aid, 1)
            if s["status"] != "sending":
                break
            time.sleep(0.02)
        self.assertEqual((s["status"], s["sent"], s["failed"]), ("stopped", 0, 0))
        self.assertIn("refused", s["error"])
        mailer.send_batch = lambda msgs, timeout=30: self.batches.append(msgs) or "sent"
        self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        self.assertEqual(self.wait_done(aid)["sent"], 2)

    def test_writer_cannot_send_and_nobody_to_send_to_says_so(self):
        aid = self.published_article()
        r = self.olive.post("/admin/api/research/articles/%d/email" % aid, json={"version": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no subscribers", r.json()["detail"])

    def test_unsubscribe_link_asks_then_the_button_unsubscribes(self):
        self.signup("reader@example.com")
        self.confirm_last()
        token = self.links[-1][2].split("t=")[1]
        page = self.anon.get("/p/asia-notes/email/unsubscribe?t=" + token)
        self.assertIn("Stop sending new articles", page.text)
        self.assertEqual(newsletter.counts(self.pub["id"])["active"], 1)    # opening it changes nothing
        done = self.anon.post("/p/asia-notes/email/unsubscribe?t=" + token,
                              data={"List-Unsubscribe": "One-Click"})
        self.assertIn("You Are Unsubscribed", done.text)
        self.assertEqual(newsletter.counts(self.pub["id"])["unsubscribed"], 1)
        self.assertIn("Link Not Recognised", self.anon.get("/p/asia-notes/email/unsubscribe?t=x").text)


class ParseImportTest(PubBase.__bases__[0]):
    def test_plain_list_and_header_variants(self):
        self.assertEqual(newsletter.parse_import("a@x.com\nb@x.com\na@x.com\n")[0], ["a@x.com", "b@x.com"])
        self.assertEqual(newsletter.parse_import("Name,E-mail\nAnn,ann@x.com\n")[0], ["ann@x.com"])
        with self.assertRaises(newsletter.NewsletterError):
            newsletter.parse_import("no addresses here\n")
