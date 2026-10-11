# -*- coding: utf-8 -*-
"""The AI tab's past-article search, its calculator and the writer's own style.

What is proved: calculate does exact arithmetic from named values, shows the
formula with the values filled in, and refuses anything that is not
arithmetic; search_past_articles finds published work only (never a draft,
never a withdrawn piece), best match first, in the run's publication unless
asked wider, and read_past_article returns the live version; a writer's style
is theirs alone, takes only their own published articles or pasted pieces as
samples, and reaches the run's instructions under the house style, with
samples marked as voice and never as a source of facts.
"""
import json

from app import article_search, calc, research, research_ai, writer_api, writing_style
from app.assistant import gateway

from tests.test_research_ai import AIBase, Fake, call, say


class CalcTest(AIBase):
    def test_exact_with_formula(self):
        out = calc.calculate("(now / year_ago - 1) * 100", {"now": 112.4, "year_ago": 108.9}, 2)
        self.assertAlmostEqual(out["result"], (112.4 / 108.9 - 1) * 100, places=9)
        self.assertEqual(out["rounded"], "3.21")
        self.assertTrue(out["with_values"].startswith("(112.4 / 108.9 - 1) * 100 = "))
        self.assertEqual(calc.calculate("mean(x)", {"x": [1, 2, 3, 4]})["result"], 2.5)
        self.assertEqual(calc.calculate("(b / a) ^ (1 / 5) - 1", {"a": 100, "b": 100})["result"], 0)
        self.assertEqual(calc.calculate("2 × 3 − 1")["result"], 5)

    def test_refuses_what_is_not_arithmetic(self):
        for expr, values in (("__import__('os').system('ls')", None), ("a.b", {"a": 1}),
                             ("1 / 0", None), ("x", None), ("10 ^ 5000", None),
                             ("[1, 2]", None), ("x + 1", {"x": [1, 2]}), ("lambda: 1", None),
                             ("'a' * 3", None), ("sum(x, key=1)", {"x": [1]})):
            with self.assertRaises(calc.CalcError, msg=expr):
                calc.calculate(expr, values)
        with self.assertRaises(calc.CalcError):
            calc.calculate("1", {"bad name": 1})
        text, err = calc.run_tool({"expression": "1 +"})
        self.assertTrue(err)
        self.assertIn("could not be read", json.loads(text)["error"])


class PastArticlesBase(AIBase):
    def setUp(self):
        super(PastArticlesBase, self).setUp()
        self.app.include_router(writer_api.router)

    def publish(self, aid, title, body, slug):
        a = research.get(aid)
        d = dict(a["draft"], title=title, dek="Standfirst for " + title, summary="Summary.",
                 slug=slug)
        research.save(aid, d, a["revision"], "w@example.com")
        a = research.get(aid)
        research.publish(aid, a["revision"], "w@example.com", None, a["draft"], ["w"], {},
                         "<p>%s</p>" % body, "# %s\n\n%s" % (title, body))
        return aid

    def new_article(self, client=None):
        return (client or self.w).post("/admin/api/research/articles", json={}).json()["id"]


class SearchTest(PastArticlesBase):
    def test_finds_published_only_best_first(self):
        me = {"id": self.wid}
        banks = self.publish(self.new_article(), "Regional Bank Margins",
                             "Net interest margins at regional banks widened as rates rose. "
                             "日本銀行 raised its policy rate.", "bank-margins")
        self.publish(self.new_article(), "Rice Prices", "Rice prices doubled; banks were not "
                     "involved.", "rice-prices")
        gone = self.publish(self.new_article(), "Bank Margins Withdrawn", "bank margins", "gone")
        research.withdraw(gone, "w@example.com", "Wrong data.")
        # a draft that mentions it is never found
        a = research.get(self.aid)
        research.save(self.aid, dict(a["draft"], title="Bank margins draft"), a["revision"],
                      "w@example.com")

        out = article_search.search(me, research.HOME, "bank margins")
        self.assertEqual([x["title"] for x in out["articles"]], ["Regional Bank Margins", "Rice Prices"])
        top = out["articles"][0]
        self.assertEqual(top["id"], banks)
        self.assertTrue(top["url"].endswith("/research/bank-margins"))
        self.assertIn("margins", top["snippet"].lower())
        self.assertEqual(article_search.search(me, research.HOME, "日本銀行")["found"], 1)
        self.assertEqual(article_search.search(me, research.HOME, "")["found"], 2)
        self.assertIn("Nothing published matches",
                      article_search.search(me, research.HOME, "semiconductors")["note"])

        got = article_search.read(banks)
        self.assertEqual(got["title"], "Regional Bank Margins")
        self.assertIn("Net interest margins", got["markdown"])
        self.assertIsNone(article_search.read(gone))
        self.assertIsNone(article_search.read(self.aid))       # never published
        text, err = article_search.run_tool(me, research.HOME, "read_past_article", {"id": 99999})
        self.assertTrue(err)

    def test_mine_is_by_author(self):
        mine = self.publish(self.new_article(), "Mine", "yen", "mine")
        other_client, other_id = self.person("o@example.com", ["writing"])
        theirs = self.publish(self.new_article(other_client), "Theirs", "yen", "theirs")
        ids = [x["id"] for x in article_search.search({"id": self.wid}, None, "yen", "mine")["articles"]]
        self.assertEqual(ids, [mine])
        ids = [x["id"] for x in article_search.search({"id": self.wid}, None, "yen", "all")["articles"]]
        self.assertEqual(sorted(ids), sorted([mine, theirs]))

    def test_run_offers_and_uses_the_tools(self):
        self.publish(self.new_article(), "Regional Bank Margins", "Margins widened.", "bank-margins")
        self.connect()
        fake = Fake([call("search_past_articles", query="bank margins"),
                     call("calculate", expression="(a / b - 1) * 100", values={"a": 110, "b": 100}),
                     say("Found the earlier piece; growth is 10%.")])
        gateway.complete = fake
        r = self.w.post("/admin/api/research/articles/%d/ai" % self.aid,
                        json={"instruction": "Link the bank piece."})
        j = self.wait(r.json()["id"])
        self.assertEqual(j["status"], "done", j)
        for name in ("search_past_articles", "read_past_article", "calculate"):
            self.assertIn(name, fake.seen_tools)
        steps = [s["text"] for s in j["steps"]]
        self.assertIn("Searched past articles for \"bank margins\"", steps)
        self.assertIn("Calculated (a / b - 1) * 100", steps)


class StyleTest(PastArticlesBase):
    PIECE = ("I write short. I lead with the number. " * 12).strip()

    def test_style_saved_checked_and_personal(self):
        mine = self.publish(self.new_article(), "Yen Note", "The yen fell. Exporters gain.", "yen-note")
        other_client, _ = self.person("o@example.com", ["writing"])
        theirs = self.publish(self.new_article(other_client), "Their Note", "Not mine.", "their-note")

        v = self.w.get("/admin/api/write/style").json()
        self.assertEqual(v["samples"], [])
        self.assertEqual([c["article_id"] for c in v["choices"]], [mine])

        r = self.w.put("/admin/api/write/style", json={"text": "Short sentences.",
                                                       "samples": [{"article_id": theirs}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Only articles you wrote", r.json()["detail"])
        r = self.w.put("/admin/api/write/style", json={"text": "x", "samples": [{"title": "T", "text": "too short"}]})
        self.assertEqual(r.status_code, 400)
        r = self.w.put("/admin/api/write/style", json={"text": "Short sentences.", "samples": [
            {"article_id": mine}, {"article_id": mine}, {"title": "My Substack post", "text": self.PIECE}]})
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()
        self.assertEqual([x["kind"] for x in v["samples"]], ["article", "pasted"])
        self.assertEqual(v["samples"][1]["words"], len(self.PIECE.split()))

        # nobody else sees it; the shared password is nobody
        self.assertEqual(other_client.get("/admin/api/write/style").json()["text"], "")
        self.assertEqual(self.admin.get("/admin/api/write/style").status_code, 403)

        # it reaches the run's instructions, after the house style
        article = research.get(self.aid)
        person = {"id": self.wid, "name": "w", "email": "w@example.com"}
        system = research_ai._system(article, person)
        self.assertIn("Short sentences.", system)
        self.assertIn("The yen fell. Exporters gain.", system)
        self.assertIn("My Substack post", system)
        self.assertIn("never copy their sentences, facts or figures", system)
        self.assertLess(system.index("House style"), system.index("The writer's own style"))
        self.assertTrue(self.w.get("/admin/api/research/ai").json()["style"]["on"])

        # a sample withdrawn later is kept on the list but left out of runs
        research.withdraw(mine, "w@example.com", "Old.")
        v = self.w.get("/admin/api/write/style").json()
        self.assertFalse(v["samples"][0]["live"])
        self.assertNotIn("The yen fell.", research_ai._system(research.get(self.aid), person))
        r = self.w.put("/admin/api/write/style", json={"text": "Short sentences.", "samples": [
            {"article_id": mine}, {"title": "My Substack post", "text": self.PIECE}]})
        self.assertEqual(r.status_code, 200, r.text)

        # emptied, it is gone from runs
        self.w.put("/admin/api/write/style", json={"text": "", "samples": []})
        self.assertEqual(writing_style.prompt(person), "")
        self.assertFalse(self.w.get("/admin/api/research/ai").json()["style"]["on"])

    def test_limits(self):
        r = self.w.put("/admin/api/write/style", json={"text": "x" * (writing_style.TEXT_MAX + 1)})
        self.assertEqual(r.status_code, 400)
        r = self.w.put("/admin/api/write/style", json={"samples": [
            {"title": str(i), "text": self.PIECE} for i in range(writing_style.SAMPLES_MAX + 1)]})
        self.assertEqual(r.status_code, 400)
        r = self.w.put("/admin/api/write/style", json={"samples": [
            {"title": str(i), "text": self.PIECE * 3} for i in range(writing_style.SAMPLES_MAX)]})
        self.assertEqual(r.status_code, 200)
        person = {"id": self.wid}
        self.assertLess(len(writing_style.prompt(person)), writing_style.PROMPT_TOTAL + 2000)
