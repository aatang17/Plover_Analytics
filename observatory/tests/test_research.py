# -*- coding: utf-8 -*-
"""PloverResearch: the article format, the desk API and the public pages.

What is proved: whatever a writer types or pastes is reduced to an allowlist
(no script, handler or javascript: link survives to the page); a save based
on a stale revision is refused rather than overwriting a co-writer; publishing
refuses an incomplete article and lists why; a published version is immutable
even to a direct SQL UPDATE or DELETE, and version 1 is byte-identical after
version 2; the address is fixed at first publication; charts are frozen with
the data as of the publication date and the CSV link names that date;
withdrawal replaces the page with a notice; images are checked by their
bytes; the research pipeline's Markdown imports; the public pages carry a
Content-Security-Policy; only people with the Writing permission can write.
"""
import json
import os
import pathlib
import shutil
import sqlite3
import struct
import tempfile
import unittest
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import admin_api, research, research_api, research_doc as rd, research_pages as rp, staff

GOOD = "correct horse battery"
REPO = pathlib.Path(__file__).resolve().parents[2]


def png(w=3, h=2):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FAKE_OBS = {
    "dataset": "cpi-jp", "measure": "yoy", "unit": "%", "trust": "derived",
    "calc": "(index[t] / index[t-12 months] - 1) x 100", "release": {
        "release_id": 7, "label": "2026-08", "source_name": "Statistics Bureau", "latest_period": "2026-08-01"},
    "series": [{"code": "0001", "name_en": "All items", "name_ja": "総合",
                "points": [["2026-06-01", 3.1], ["2026-07-01", None], ["2026-08-01", 2.7]]}],
}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        staff.DB_PATH = root / "staff.db"
        research.DATA_DIR = root / "research"
        admin_api.ADMIN_DIR = root
        admin_api.AUDIT_PATH = root / "audit.jsonl"
        admin_api._LOGIN_HITS.clear()
        self._iters = staff.PBKDF2_ITERATIONS
        staff.PBKDF2_ITERATIONS = 1000
        staff._DUMMY_HASH = None
        self._pw = os.environ.get("ADMIN_PASSWORD")
        os.environ["ADMIN_PASSWORD"] = "shared-secret"
        self.addCleanup(self._restore)
        # chart data without a database: the observations function is stubbed
        # with a canned response, and the call it received is recorded
        self.calls = []
        from app import api

        def fake_observations(dataset, **kw):
            self.calls.append((dataset, kw))
            body = dict(FAKE_OBS)
            body["as_of"] = kw.get("as_of")
            return body
        self._obs = api.observations
        api.observations = fake_observations
        self._announce = rp.announce
        rp.announce = lambda slug: None
        self._soon = research_api.research_backup.soon
        research_api.research_backup.soon = lambda delay=60: None

        app = FastAPI()
        app.include_router(admin_api.router)
        app.include_router(research_api.router)
        app.include_router(rp.router)
        self.app = app
        admin = TestClient(app)
        admin.post("/admin/api/login", json={"password": "shared-secret"})
        r = admin.post("/admin/api/team", json={"email": "w@example.com", "name": "Wren Writer",
                                                "permissions": ["writing", "team"]})
        tok = r.json()["setup_url"].split("#setup=")[1]
        self.w = TestClient(app)
        self.w.post("/admin/api/setup", json={"token": tok, "password": GOOD})
        self.writer_id = r.json()["person"]["id"]
        self.anon = TestClient(app)

    def _restore(self):
        from app import api
        api.observations = self._obs
        rp.announce = self._announce
        research_api.research_backup.soon = self._soon
        staff.PBKDF2_ITERATIONS = self._iters
        staff._DUMMY_HASH = None
        if self._pw is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self._pw

    def new_article(self):
        r = self.w.post("/admin/api/research/articles")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def complete_draft(self, **over):
        d = {
            "title": "Regional banks and the rate path",
            "dek": "Most realised more in bond losses than they gained.",
            "summary": "Net interest income rose 31.4% while realised bond losses were larger.",
            "slug": "regional-banks-rate-path",
            "authors": [self.writer_id],
            "market": "jp", "topics": ["banks", "rates"],
            "blocks": [
                {"id": "b1", "type": "p", "html": "Net income fell -3.2% in <strong>FY2025</strong>."
                                                    '<sup data-note="JBA aggregates, non-consolidated."></sup>'},
                {"id": "b2", "type": "heading", "level": 2, "text": "The rate path"},
                {"id": "c1", "type": "chart", "dataset": "cpi-jp", "series": ["0001"], "measure": "yoy",
                 "start": "2026-01", "end": "", "title": "Headline inflation", "note": ""},
                {"id": "t1", "type": "table", "rows": [["Bank", "FY2024", "FY2025"],
                                                        ["First-tier", "3,402.1", "4,030.7"],
                                                        ["Second-tier", "-103.9", "—"]],
                 "header": True, "align": ["auto", "auto", "auto"], "caption": "Net interest income",
                 "source": "Japanese Bankers Association"},
            ],
        }
        d.update(over)
        return d

    def save(self, aid, draft, base):
        return self.w.put("/admin/api/research/articles/%d" % aid, json={"draft": draft, "base_revision": base})


class FormatTest(unittest.TestCase):
    def test_inline_allowlist(self):
        dirty = ('<p onclick="x()">Hi <script>alert(1)</script><img src=x onerror=alert(1)>'
                 '<a href="javascript:alert(1)">bad</a> <a href="https://e.gov/x?a=1&b=2" '
                 'onmouseover="y()">ok</a> <b style="font-weight:normal" id="docs-internal-guid">'
                 '<span style="font-weight:700">bold</span> <span style="font-style:italic">it</span>'
                 '</b><iframe src="https://evil"></iframe><style>p{}</style><sup data-note="A &quot;note&quot;">9</sup></p>')
        out = rd.clean_inline(dirty)
        for bad in ("script", "onerror", "onclick", "onmouseover", "javascript", "iframe", "img", "style"):
            self.assertNotIn(bad, out)
        self.assertIn('<a href="https://e.gov/x?a=1&amp;b=2">ok</a>', out)
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn("<em>it</em>", out)
        self.assertIn("bad", out)                       # the text survives, the link does not
        self.assertIn('<sup data-note="A &quot;note&quot;"></sup>', out)
        self.assertEqual(rd.clean_inline(out), out)     # idempotent

    def test_unclosed_and_misnested_markup_is_closed(self):
        out = rd.clean_inline("<strong><em>a</strong> b")
        self.assertEqual(out, "<strong><em>a</em></strong> b")

    def test_relative_site_links_kept_other_schemes_dropped(self):
        self.assertEqual(rd.safe_href("cpi.html?series=0001"), "cpi.html?series=0001")
        self.assertEqual(rd.safe_href("/research/x"), "/research/x")
        for bad in ("data:text/html,x", "vbscript:x", "JaVaScRiPt:alert(1)", "//evil.com", "x.html\"><script>"):
            self.assertIsNone(rd.safe_href(bad), bad)

    def test_render_numbers_notes_and_tables(self):
        d = rd.clean_draft({"blocks": [
            {"type": "p", "html": 'Fell -3.2% and (-1.0)<sup data-note="First."></sup> then<sup data-note="Second."></sup>.'},
            {"type": "table", "rows": [["Item", "Value"], ["A", "1,234.5"], ["B", "-2.0%"], ["C", "—"]],
             "header": True, "align": ["auto", "auto"], "caption": "", "source": "X"}]})
        html = rd.render_body(d)
        self.assertIn("−3.2%", html)
        self.assertIn("(−1.0)", html)
        self.assertIn('href="#fn-1"', html)
        self.assertIn('<li id="fn-2">Second.', html)
        self.assertIn('<td class="num">1,234.5</td>', html)
        self.assertIn('<th class="num" scope="col">Value</th>', html)
        self.assertIn('<td>A</td>', html)
        md = rd.render_markdown(d)
        self.assertIn("[^1]", md)
        self.assertIn("| --- | --: |", md)

    # The shape the research-report pipeline writes: title, italic dek, prose,
    # a chart placeholder, a section heading and a right-aligned table. Made
    # up here so the test runs on every checkout; the real drafts are not in
    # the repository.
    PIPELINE_DRAFT = (
        "# Japan's regional banks and the rate increases: a test draft\n\n"
        "*Asia Economics Observations \u00b7 1 January 2026. All figures as of 2026-01-01.*\n\n"
        "Net interest income rose while bond losses were realised.\n\n"
        "**[Chart 1 \u2014 Loan rates rose with the call rate]**\n\n"
        "## Net interest income against realised bond losses\n\n"
        "| | FY2023 | FY2024 |\n"
        "| --- | --: | --: |\n"
        "| Net interest income | 3,066.7 | 3,402.1 |\n"
        "| Realised bond result | \u2212374.6 | \u2212368.2 |\n"
    )

    def test_import_research_pipeline_markdown(self):
        d = rd.import_markdown(self.PIPELINE_DRAFT)
        self.assertTrue(d["title"].startswith("Japan's regional banks"))
        self.assertTrue(d["dek"].startswith("Asia Economics Observations"))
        types = [b["type"] for b in d["blocks"]]
        self.assertIn("table", types)
        self.assertIn("heading", types)
        placeholders = [b for b in d["blocks"] if b["type"] == "image"]
        self.assertTrue(placeholders and all(not b["media"] and b["pending"] for b in placeholders))
        table = [b for b in d["blocks"] if b["type"] == "table"][0]
        self.assertEqual(table["align"][1], "right")
        self.assertTrue(d["slug"].startswith("japan-s-regional-banks"))


class DeskTest(Base):
    def test_writing_permission_required(self):
        self.assertEqual(self.anon.get("/admin/api/research/articles").status_code, 401)
        admin = TestClient(self.app)
        admin.post("/admin/api/login", json={"password": "shared-secret"})
        r = admin.post("/admin/api/team", json={"email": "ops@example.com", "name": "Ops",
                                                "permissions": ["operations"]})
        ops = TestClient(self.app)
        ops.post("/admin/api/setup", json={"token": r.json()["setup_url"].split("#setup=")[1], "password": GOOD})
        self.assertEqual(ops.get("/admin/api/research/articles").status_code, 403)
        self.assertEqual(ops.post("/admin/api/research/articles").status_code, 403)

    def test_stale_save_is_refused_not_overwritten(self):
        a = self.new_article()
        first = self.save(a["id"], self.complete_draft(title="Mine"), a["revision"])
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.save(a["id"], self.complete_draft(title="Theirs"), a["revision"])
        self.assertEqual(stale.status_code, 409)
        body = stale.json()
        self.assertEqual(body["current"]["draft"]["title"], "Mine")
        self.assertEqual(body["current"]["updated_by"], "w@example.com")
        self.assertEqual(self.w.get("/admin/api/research/articles/%d" % a["id"]).json()["draft"]["title"], "Mine")

    def test_publish_refuses_incomplete_and_lists_why(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(summary="", blocks=[
            {"type": "image", "media": "", "alt": "", "caption": "x", "source": "", "pending": "c1.png"},
            {"type": "p", "html": "Text."}]), a["revision"])
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        r = self.w.post("/admin/api/research/articles/%d/publish" % a["id"], json={"base_revision": art["revision"]})
        self.assertEqual(r.status_code, 400)
        problems = " ".join(r.json()["problems"])
        self.assertIn("summary", problems)
        self.assertIn("waiting for its image", problems)
        self.assertIn("source line", problems)
        self.assertEqual(self.anon.get("/research/regional-banks-rate-path").status_code, 404)

    def publish(self, aid, note=""):
        art = self.w.get("/admin/api/research/articles/%d" % aid).json()
        r = self.w.post("/admin/api/research/articles/%d/publish" % aid,
                        json={"base_revision": art["revision"], "change_note": note})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_publish_freezes_and_serves(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        done = self.publish(a["id"])
        self.assertEqual(done["version"], 1)
        self.assertEqual(self.calls[-1][1]["as_of"], done["published_at"][:10])
        page = self.anon.get("/research/regional-banks-rate-path")
        self.assertEqual(page.status_code, 200)
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])
        html = page.text
        self.assertIn("Regional banks and the rate path", html)
        self.assertIn('<p class="rs-author">Wren Writer</p>', html)
        self.assertIn('<base href="/">', html)
        self.assertIn("as_of=%s" % done["published_at"][:10], html)       # CSV link is frozen
        self.assertIn('id="rs-data"', html)
        self.assertIn('"points"', html)                                    # chart data embedded
        self.assertIn("Show calculation", html)                            # derived rate: formula, no badge
        self.assertIn("How to Cite", html)
        self.assertIn("/research/regional-banks-rate-path/v1", html)
        listing = self.anon.get("/research").text
        self.assertIn("/research/regional-banks-rate-path", listing)
        md = self.anon.get("/research/regional-banks-rate-path.md")
        self.assertEqual(md.status_code, 200)
        self.assertIn("# Regional banks and the rate path", md.text)
        self.assertIn("Cite as:", md.text)
        self.assertIn("regional-banks-rate-path", self.anon.get("/research/feed.xml").text)
        self.assertIn("regional-banks-rate-path", self.anon.get("/sitemap-research.xml").text)
        self.assertEqual(self.anon.get("/research/regional-banks-rate-path/v1").status_code, 200)
        self.assertEqual(self.anon.get("/research/regional-banks-rate-path/v9").status_code, 404)
        self.assertEqual(self.anon.get("/research/nope").status_code, 404)

    def test_versions_are_immutable(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        self.publish(a["id"])
        v1_before = research.version(a["id"], 1)
        con = sqlite3.connect(str(research.db_path()))
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE article_versions SET title = 'changed'")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("DELETE FROM article_versions")
        con.close()
        # a correction is version 2 — and needs a note
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        d = art["draft"]
        d["blocks"][0]["html"] = "Corrected text."
        d["slug"] = "a-different-address"
        saved = self.save(a["id"], d, art["revision"]).json()
        self.assertEqual(saved["draft"]["slug"], "regional-banks-rate-path")  # fixed at first publication
        r = self.w.post("/admin/api/research/articles/%d/publish" % a["id"],
                        json={"base_revision": saved["revision"], "change_note": ""})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.publish(a["id"], "Corrected the opening sentence.")["version"], 2)
        v1_after = research.version(a["id"], 1)
        self.assertEqual(v1_before, v1_after)
        self.assertIn("Corrected text.", self.anon.get("/research/regional-banks-rate-path").text)
        old = self.anon.get("/research/regional-banks-rate-path/v1").text
        self.assertIn("Net income fell", old)
        self.assertIn("You are reading version 1 of 2", old)
        self.assertIn("noindex", old)

    def test_homepage_lead_chart_of_week_and_filters(self):
        listing = self.anon.get("/research")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("No notes published yet", listing.text)
        self.assertIn("substack.com/@ploverresearch", listing.text)
        made = []
        for n, (slug, market, topics) in enumerate((("zeroth-note", "global", ["growth"]),
                                                     ("first-note", "jp", ["banks"]),
                                                     ("second-note", "us", ["inflation"]),
                                                     ("third-note", "jp", ["rates", "banks"]))):
            a = self.new_article()
            d = self.complete_draft(slug=slug, title="Note %d" % (n + 1), market=market,
                                    topics=topics + ["not-a-topic"])
            self.save(a["id"], d, a["revision"])
            self.publish(a["id"])
            made.append(a["id"])
        html = self.anon.get("/research").text
        lead = html.split('class="rh-hero', 1)[1].split("</article>", 1)[0]
        self.assertIn("Note 4", lead)                                  # newest leads
        self.assertIn("Japan", lead)
        self.assertIn('data-fig="lead"', lead)                         # with its chart
        cotw = html.split('aria-label="Chart of the Week"', 1)[1].split("</aside>", 1)[0]
        self.assertIn("/research/second-note", cotw)                   # never the lead's own chart
        self.assertIn('class="rh-card" data-market="us"', html)        # boxed cards
        self.assertIn('class="rh-tabs"', html)                         # market tabs
        self.assertNotIn("not-a-topic", html)
        data = json.loads(html.split('id="rs-data">', 1)[1].split("</script>", 1)[0].replace("<\\/", "</"))
        self.assertIn("lead", data["figures"])
        self.assertIn("cotw", data["figures"])
        # an article ends with more notes and the subscribe panel
        art = self.anon.get("/research/first-note").text
        self.assertIn("More From PloverResearch", art)
        self.assertIn("/research/third-note", art)
        self.assertNotIn('href="/research/first-note">Note', art.split("More From", 1)[1])
        self.assertIn("Subscribe on Substack", art)
        self.assertIn('<p class="rs-kicker">Japan <span aria-hidden="true">\u00b7</span> Banks</p>', art)
        self.assertIn('class="rs-share"', art)

    def test_market_required(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(market=""), a["revision"])
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        r = self.w.post("/admin/api/research/articles/%d/publish" % a["id"], json={"base_revision": art["revision"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("market", " ".join(r.json()["problems"]))

    def test_withdraw_and_reinstate(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        self.publish(a["id"])
        self.assertEqual(self.w.post("/admin/api/research/articles/%d/withdraw" % a["id"], json={"reason": ""}).status_code, 400)
        r = self.w.post("/admin/api/research/articles/%d/withdraw" % a["id"], json={"reason": "Data error in Table 1."})
        self.assertEqual(r.status_code, 200, r.text)
        page = self.anon.get("/research/regional-banks-rate-path")
        self.assertEqual(page.status_code, 410)
        self.assertIn("Data error in Table 1.", page.text)
        self.assertNotIn("Net income fell", page.text)
        self.assertNotIn("regional-banks-rate-path", self.anon.get("/research").text)
        self.assertEqual(self.w.delete("/admin/api/research/articles/%d" % a["id"]).status_code, 400)
        self.w.post("/admin/api/research/articles/%d/reinstate" % a["id"])
        self.assertEqual(self.anon.get("/research/regional-banks-rate-path").status_code, 200)

    def test_delete_only_before_publication(self):
        a = self.new_article()
        self.assertEqual(self.w.delete("/admin/api/research/articles/%d" % a["id"]).status_code, 200)
        self.assertEqual(self.w.get("/admin/api/research/articles/%d" % a["id"]).status_code, 404)

    def test_slug_must_be_unique(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        self.publish(a["id"])
        b = self.new_article()
        r = self.save(b["id"], self.complete_draft(), b["revision"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("already uses", r.json()["detail"])

    def test_script_cannot_reach_the_page(self):
        a = self.new_article()
        evil = self.complete_draft(title="<script>alert(1)</script>T", blocks=[
            {"type": "p", "html": '<img src=x onerror=alert(1)><a href="javascript:alert(2)">x</a>'
                                  '<svg onload=alert(3)></svg>"</p><script>alert(4)</script>'},
            {"type": "table", "rows": [["<b onclick=z()>h</b>"]], "header": False, "align": ["auto"],
             "caption": "<script>", "source": "S"},
        ])
        self.save(a["id"], evil, a["revision"])
        self.publish(a["id"])
        html = self.anon.get("/research/regional-banks-rate-path").text
        # what the writer controls: the headline, standfirst and body (the share
        # bar's own icons are ours and are drawn as SVG on purpose)
        body = html.split('<header class="rs-head">', 1)[1].split("</article>", 1)[0]
        body = body.split('<div class="rs-share"', 1)[0] + body.split('<div class="rs-body">', 1)[1]
        for bad in ("<script", "onerror", "onclick", "onload", "javascript:", "<svg", "<img"):
            self.assertNotIn(bad, body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;T", body)

    def test_images_checked_by_content(self):
        r = self.w.post("/admin/api/research/media", content=png(5, 4),
                        headers={"Content-Type": "image/png", "X-File-Name": "c.png"})
        self.assertEqual(r.status_code, 200, r.text)
        info = r.json()
        self.assertEqual((info["ext"], info["width"], info["height"]), ("png", 5, 4))
        bad = self.w.post("/admin/api/research/media", content=b"<svg onload=alert(1)>",
                          headers={"Content-Type": "image/png", "X-File-Name": "x.png"})
        self.assertEqual(bad.status_code, 400)
        served = self.anon.get("/research/media/%s.png" % info["media"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/png")
        self.assertIn("immutable", served.headers["cache-control"])
        self.assertEqual(served.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.anon.get("/research/media/%s.jpg" % info["media"]).status_code, 404)
        self.assertEqual(self.anon.get("/research/media/../../staff.db").status_code, 404)

    def test_preview_and_check(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        check = self.w.get("/admin/api/research/articles/%d/check" % a["id"]).json()
        self.assertEqual(check["problems"], [])
        self.assertEqual(check["version"], 1)
        pv = self.w.get("/admin/api/research/articles/%d/preview" % a["id"])
        self.assertEqual(pv.status_code, 200)
        self.assertIn("noindex", pv.text)
        self.assertIn("not published", pv.text)
        self.assertEqual(self.anon.get("/admin/api/research/articles/%d/preview" % a["id"]).status_code, 401)

    def test_history_and_substack(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        self.assertEqual(self.w.get("/admin/api/research/articles/%d/substack" % a["id"]).status_code, 400)
        self.publish(a["id"])
        hist = self.w.get("/admin/api/research/articles/%d/history" % a["id"]).json()["history"]
        self.assertTrue(any((h["label"] or "").startswith("Published as version 1") for h in hist))
        sub = self.w.get("/admin/api/research/articles/%d/substack" % a["id"]).json()
        self.assertIn("https://", sub["url"])
        self.assertIn(sub["url"], sub["text"])
        self.assertEqual(len(sub["charts"]), 1)
        self.assertIn("points", sub["charts"][0]["snap"]["series"][0])

    def test_import_endpoint_creates_draft(self):
        r = self.w.post("/admin/api/research/import", json={"markdown": "# Title here\n\n*A standfirst.*\n\nBody **bold** [^1].\n\n[^1]: A note.\n\n| a | b |\n|---|--:|\n| x | 1 |\n"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["draft"]
        self.assertEqual(d["title"], "Title here")
        self.assertEqual(d["dek"], "A standfirst.")
        self.assertIn('<sup data-note="A note."></sup>', d["blocks"][0]["html"])
        self.assertEqual(d["blocks"][1]["align"], ["auto", "right"])
        self.assertEqual(d["authors"], [self.writer_id])

    def test_backup_copies_both_databases(self):
        from app import research_backup
        a = self.new_article()
        self.save(a["id"], self.complete_draft(), a["revision"])
        for k in ("EDINET_S3_BUCKET",):
            os.environ.pop(k, None)
        # the admin console's hand curation is carried too (2026-10-24)
        admin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, admin, True)
        open(os.path.join(admin, "us_companies.json"), "w").write('{"schema": 1, "companies": {}}')
        saved = research_backup.ADMIN_DIR
        self.addCleanup(setattr, research_backup, "ADMIN_DIR", saved)
        research_backup.ADMIN_DIR = pathlib.Path(admin)
        status = research_backup.run()
        self.assertTrue(status["ok"], status)
        self.assertEqual(sorted(status["local"]["files"]), ["research.db", "staff.db", "us_companies.json"])
        self.assertFalse(status["offsite"]["configured"])
        copy = sqlite3.connect(os.path.join(status["local"]["folder"], "research.db"))
        self.assertEqual(copy.execute("SELECT count(*) FROM articles").fetchone()[0], 1)
        copy.close()


class FootnoteMarkdownTest(unittest.TestCase):
    def test_formatting_in_a_note_stays_out_of_its_markup(self):
        from app import research_doc as rd
        d = rd.import_markdown("# T\n\nText *x*[^1].\n\n[^1]: Plover, *CPI*, <em>Cat</em>, "
                               "[BoJ](https://boj.or.jp).")
        html = d["blocks"][0]["html"]
        self.assertIn('<sup data-note="Plover, CPI, Cat, BoJ (https://boj.or.jp)."></sup>', html)
        self.assertIn("<em>x</em>", html)
        self.assertNotIn("&lt;em", html)


if __name__ == "__main__":
    unittest.main()


DATACHART = {"id": "d1", "type": "datachart", "kind": "line", "unit": "%",
             "title": "Policy rates", "note": "", "source": "BIS central bank policy rates",
             "rows": [["Date", "Japan", "United States"], ["2025-01", "0.5", "4.5"],
                      ["2025-02", "", "4.5"], ["2025-03", "0.5", "(n/a)"]]}


class DataChartTest(Base):
    """A chart of the writer's own numbers (pasted from a spreadsheet or another
    publisher): kept as pasted, published only with a source, missing cells
    never read as zero, and the page says the values are the author's."""

    def test_numbers_are_read_and_missing_is_never_zero(self):
        for cell, want in (("1,234.5", 1234.5), ("(2.1)", -2.1), (u"−3%", -3.0),
                           ("$40", 40.0), ("-$40", -40.0), ("", None), ("-", None),
                           (u"—", None), ("n/a", None), ("abc", None)):
            with self.subTest(cell=cell):
                self.assertEqual(rd.data_number(cell), want)
        t = rd.data_table(DATACHART["rows"])
        self.assertTrue(t["header"])
        self.assertEqual(t["names"], ["Japan", "United States"])
        self.assertEqual(t["values"], [[0.5, None, 0.5], [4.5, 4.5, None]])
        self.assertEqual(t["bad"], 1)   # "(n/a)" is not a number and is not a known blank
        # no header row: the series are named, nothing is lost
        self.assertEqual(rd.data_table([["2025", "1"], ["2026", "2"]])["names"], ["Series 1"])

    def test_cleaning_keeps_cells_and_refuses_too_many_series(self):
        c = rd.clean_draft({"blocks": [dict(DATACHART, kind="pie", extra="x")]})["blocks"][0]
        self.assertEqual(c["rows"], DATACHART["rows"])
        self.assertEqual(c["kind"], "line")
        self.assertNotIn("extra", c)
        wide = [["Date"] + ["S%d" % i for i in range(7)]]
        with self.assertRaises(rd.DocError):
            rd.clean_draft({"blocks": [dict(DATACHART, rows=wide)]})

    def test_publish_needs_a_source_and_numbers(self):
        draft = rd.clean_draft(self.complete_draft(blocks=[
            {"type": "p", "html": "Text."},
            dict(DATACHART, source=""),
            dict(DATACHART, id="d2", rows=[["Date", "A"], ["2025-01", ""]])]))
        problems = " ".join(rd.publish_problems(draft, ["Wren Writer"]))
        self.assertIn("Chart 1 needs a source line", problems)
        self.assertIn("Chart 2 has no numbers", problems)

    def test_published_page_draws_from_the_cells_with_the_source(self):
        a = self.new_article()
        self.save(a["id"], self.complete_draft(blocks=[{"type": "p", "html": "Text."}, DATACHART]),
                  a["revision"])
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        r = self.w.post("/admin/api/research/articles/%d/publish" % a["id"],
                        json={"base_revision": art["revision"]})
        self.assertEqual(r.status_code, 200, r.text)
        html = self.anon.get("/research/regional-banks-rate-path").text
        self.assertIn("Chart 1 — Policy rates", html)
        self.assertIn("Source: BIS central bank policy rates · Values as entered by the author.", html)
        self.assertNotIn("Official Statistic", html)
        data = json.loads(html.split('type="application/json" id="rs-data">')[1].split("</script>")[0])
        self.assertTrue(data["charts"]["d1"]["data"])
        self.assertEqual(data["charts"]["d1"]["block"]["rows"], DATACHART["rows"])
        md = self.anon.get("/research/regional-banks-rate-path.md").text
        self.assertIn("| 2025-02 |  | 4.5 |", md)   # the gap stays a gap

    def test_editable_markdown_round_trips(self):
        doc = rd.clean_draft({"blocks": [{"type": "p", "html": "Text."}, DATACHART]})
        md = rd.render_markdown(doc, editable=True)
        self.assertIn("![Policy rates](chart:line?unit=%25)", md)
        back = [b for b in rd.import_markdown(md)["blocks"] if b["type"] == "datachart"][0]
        for k in ("rows", "kind", "unit", "title", "source"):
            self.assertEqual(back[k], doc["blocks"][1][k], k)


class PlanAndNotesTest(Base):
    """The plan-to-draft flow: reminders the plan leaves in the draft block
    publication and survive the AI's Markdown; the approved plan and the
    research notes are kept with the draft and never reach a published
    version; a pasted picture reaches the model only as a stored upload."""

    REMINDERS = [{"id": "h1", "type": "heading", "level": 2, "text": "Hotel Rates"},
                 {"id": "r1", "type": "placeholder", "role": "text",
                  "text": "Hotel charges went from +9.25% to -1.39%."},
                 {"id": "r2", "type": "placeholder", "role": "chart", "text": "Hotel charges, year-on-year"}]

    publish = DeskTest.publish

    def test_reminders_round_trip_and_block_publishing(self):
        doc = rd.clean_draft({"blocks": [{"type": "p", "html": "Text."}] + self.REMINDERS})
        md = rd.render_markdown(doc, editable=True)
        self.assertIn("[[To show: Hotel charges went from +9.25% to -1.39%.]]", md)
        self.assertIn("[[Chart: Hotel charges, year-on-year]]", md)
        back = [(b["type"], b.get("role"), b.get("text")) for b in rd.import_markdown(md)["blocks"]]
        self.assertIn(("placeholder", "chart", "Hotel charges, year-on-year"), back)
        self.assertIn(("placeholder", "text", "Hotel charges went from +9.25% to -1.39%."), back)
        self.assertNotIn("Hotel charges went", rd.render_body(doc))           # never shown to readers
        self.assertNotIn("[[", rd.render_markdown(doc))
        a = self.new_article()
        self.save(a["id"], self.complete_draft(blocks=self.complete_draft()["blocks"] + self.REMINDERS),
                  a["revision"])
        problems = self.w.get("/admin/api/research/articles/%d/check" % a["id"]).json()["problems"]
        self.assertTrue(any(p.startswith("From the plan, the point still to be made in “Hotel Rates”")
                            for p in problems), problems)
        self.assertTrue(any("a chart is still to be added" in p for p in problems), problems)
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        r = self.w.post("/admin/api/research/articles/%d/publish" % a["id"],
                        json={"base_revision": art["revision"]})
        self.assertEqual(r.status_code, 400, r.text)

    def test_plan_and_notes_kept_but_never_published(self):
        a = self.new_article()
        plan = {"blocks": [{"type": "heading", "level": 2, "text": "Plan"},
                           {"type": "p", "html": "Expected answer: a China event."},
                           {"type": "chart", "dataset": "cpi-jp"}],           # not a plan type: dropped
                "approved_at": "2026-10-08T10:00:00Z", "approved_by": "Wren Writer"}
        notes = [{"id": "n1", "kind": "quote", "text": "PAYWALLED SENTENCE",
                  "html": 'PAYWALLED SENTENCE<sup data-note="FT, 8 October 2026."></sup><script>x()</script>',
                  "source": "FT", "url": "javascript:alert(1)", "section": "b2", "used": True},
                 {"kind": "nonsense", "text": "a thought"},
                 {"kind": "chart", "block": {"type": "chart", "dataset": "cpi-jp", "series": ["0001"],
                                             "title": "Headline"}},
                 {"kind": "number", "text": ""}]                                # empty: dropped
        r = self.save(a["id"], self.complete_draft(plan=plan, notes=notes), a["revision"])
        self.assertEqual(r.status_code, 200, r.text)
        d = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()["draft"]
        self.assertEqual([b["type"] for b in d["plan"]["blocks"]], ["heading", "p"])
        self.assertEqual(d["plan"]["approved_by"], "Wren Writer")
        self.assertEqual([n["kind"] for n in d["notes"]], ["quote", "mine", "chart"])
        self.assertEqual(d["notes"][0]["url"], "")
        self.assertNotIn("script", d["notes"][0]["html"])
        self.assertEqual(d["notes"][0]["section"], "b2")
        self.assertEqual(d["notes"][2]["block"]["series"], ["0001"])
        done = self.publish(a["id"])
        self.assertEqual(done["version"], 1)
        html = self.anon.get("/research/regional-banks-rate-path").text
        self.assertNotIn("PAYWALLED", html)
        self.assertNotIn("PAYWALLED", self.anon.get("/research/regional-banks-rate-path.md").text)
        with sqlite3.connect(str(research.db_path())) as c:
            doc = json.loads(c.execute("SELECT doc FROM article_versions").fetchone()[0])
        self.assertNotIn("plan", doc)
        self.assertNotIn("notes", doc)
        # a new note after publication is not an unpublished change to the article
        art = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()
        d2 = dict(art["draft"], notes=art["draft"]["notes"] + [{"kind": "mine", "text": "later"}])
        self.assertEqual(self.save(a["id"], d2, art["revision"]).status_code, 200)
        self.assertFalse(self.w.get("/admin/api/research/articles/%d" % a["id"]).json()["unpublished_changes"])

    def test_ai_rewrite_keeps_plan_and_notes_and_sees_them(self):
        from app import research_mcp
        a = self.new_article()
        draft = self.complete_draft(blocks=self.REMINDERS, notes=[{"kind": "mine", "text": "check flights"}],
                                    plan={"blocks": [{"type": "p", "html": "The plan."}]})
        self.save(a["id"], draft, a["revision"])
        person = staff.get(self.writer_id)
        text, err = research_mcp.run(person, "Claude", "read_article", {"article_id": a["id"]})
        self.assertFalse(err, text)
        got = json.loads(text)
        self.assertIn("[[Chart: Hotel charges, year-on-year]]", got["markdown"])
        self.assertEqual(got["beside_the_draft"]["plan"], "The plan.")
        self.assertEqual(got["beside_the_draft"]["notes"][0]["text"], "check flights")
        md = got["markdown"].replace("[[Chart: Hotel charges, year-on-year]]",
                                     "![Hotel charges](plover:cpi-jp?series=0001&measure=yoy)")
        text, err = research_mcp.run(person, "Claude", "replace_draft",
                                     {"article_id": a["id"], "base_revision": got["revision"], "markdown": md})
        self.assertFalse(err, text)
        d = self.w.get("/admin/api/research/articles/%d" % a["id"]).json()["draft"]
        self.assertEqual(d["plan"]["blocks"][0]["html"], "The plan.")
        self.assertEqual(d["notes"][0]["text"], "check flights")
        self.assertEqual([b["type"] for b in d["blocks"]], ["heading", "placeholder", "chart"])

    def test_a_picture_reaches_the_model_only_as_an_upload(self):
        from app import research_ai
        from app.assistant import gateway
        with self.assertRaises(research_ai.AIError):
            research_ai._image("../../etc/passwd")
        with self.assertRaises(research_ai.AIError):
            research_ai._image("0" * 64 + ".png")                             # never uploaded
        info = research.add_media(png(), "shot.png", "w@example.com")
        img = research_ai._image(info["media"] + ".png")
        self.assertEqual(img["media_type"], "image/png")
        data = research_ai._image_data(img)
        msg = [{"role": "user", "content": "Rebuild it.", "images": [{"media_type": "image/png", "data": data}]}]
        _, anth = gateway._anthropic_messages(msg)
        self.assertEqual([b["type"] for b in anth[0]["content"]], ["text", "image"])
        oai = gateway._openai_messages(msg)
        self.assertTrue(oai[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(gateway._openai_messages([{"role": "user", "content": "Hi"}])[0]["content"], "Hi")
        self.assertIn("Never take numbers from the picture", research_ai.PICTURE_RULES)
