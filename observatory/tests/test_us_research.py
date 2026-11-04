# -*- coding: utf-8 -*-
"""Research a Stock (app/us_research_api.py, app/doc_render.py).

What is proved: the reader's choices are checked and fall back to the usual
brief; figures by period carry growth against the same period a year earlier,
margins and free cash flow, and a missing input stays missing; a missing part
is named, never filled in; every download format is a well-formed file made
from the same document the page draws, with a missing value an empty Excel
cell; and, on this machine's data, the brief agrees with the screener's row,
its peers are the reader's own or five others in the same industry, a topic's
paragraph is quoted once, and keeping usual choices needs an account. No network.
"""
import io
import statistics
import unittest
import xml.dom.minidom
import zipfile

from app import doc_render
from app import us_research_api as rs


class Choices(unittest.TestCase):
    def test_empty_is_the_usual_brief(self):
        ch = rs.choices()
        self.assertEqual(ch["sections"], rs.DEFAULT_SECTIONS)
        self.assertEqual((ch["per"], ch["n"]), ("a", 5))
        self.assertEqual(ch["figures"], rs.DEFAULT_FIGURES)
        self.assertEqual(ch["peers"], [])

    def test_unknown_values_are_dropped(self):
        ch = rs.choices(s="peers,nonsense,business", per="x", n="7", f="revenue,bogus", h="99", r="0")
        self.assertEqual(ch["sections"], ["peers", "business"])          # the reader's order is kept
        self.assertEqual((ch["per"], ch["n"]), ("a", 5))
        self.assertEqual(ch["figures"], ["revenue"])
        self.assertEqual((ch["holders"], ch["risks"]), (5, 0))

    def test_quarters_have_their_own_counts(self):
        self.assertEqual(rs.choices(per="q")["n"], 8)
        self.assertEqual(rs.choices(per="q", n="12")["n"], 12)
        self.assertEqual(rs.choices(per="q", n="10")["n"], 8)

    def test_topics_and_questions_joined_or_repeated(self):
        ch = rs.choices(k=["China|tariffs", "ai", "CHINA"], ask=["Who owns AMD?|AMD revenue by segment"])
        self.assertEqual(ch["topics"], ["China", "tariffs"])               # under 3 letters, or a repeat, is dropped
        self.assertEqual(ch["questions"], ["Who owns AMD?", "AMD revenue by segment"])
        self.assertEqual(len(rs.choices(ask=["a?|b?|c?|d?"])["questions"]), rs.MAX_QUESTIONS)

    def test_peer_tickers_are_cleaned(self):
        self.assertEqual(rs.choices(p=" intc, brk.b ,INTC,<x>")["peers"], ["INTC", "BRK-B", "X"])


class Periods(unittest.TestCase):
    def test_growth_margins_and_fcf(self):
        rows = [("2025-12-31", {"revenue": 120.0, "operating_income": 12.0, "net_income": 6.0,
                                "cf_operating": 20.0, "capex": 5.0, "eps_diluted": 1.5}),
                ("2024-12-31", {"revenue": 100.0, "operating_income": None, "net_income": -1.0,
                                "cf_operating": 9.0, "capex": None})]
        y = rs.periods(rows, 5)
        self.assertAlmostEqual(y[0]["revenue_growth"], 20.0)
        self.assertAlmostEqual(y[0]["operating_margin"], 10.0)
        self.assertAlmostEqual(y[0]["net_margin"], 5.0)
        self.assertEqual(y[0]["fcf"], 15.0)
        self.assertAlmostEqual(y[0]["fcf_margin"], 12.5)
        # no year before it on file, and a missing input: missing, never zero
        self.assertIsNone(y[1]["revenue_growth"])
        self.assertIsNone(y[1]["operating_margin"])
        self.assertIsNone(y[1]["fcf"])

    def test_quarter_growth_is_against_the_same_quarter_a_year_earlier(self):
        # no fourth quarters on file: the row four back is not a year back
        rows = [("2026-06-30", {"revenue": 130.0}), ("2026-03-31", {"revenue": 90.0}),
                ("2025-09-30", {"revenue": 80.0}), ("2025-06-30", {"revenue": 100.0}),
                ("2025-03-31", {"revenue": 75.0})]
        y = rs.periods(rows, 2)
        self.assertAlmostEqual(y[0]["revenue_growth"], 30.0)                # Jun 2026 against Jun 2025
        self.assertAlmostEqual(y[1]["revenue_growth"], 20.0)                # Mar 2026 against Mar 2025
        self.assertEqual(len(y), 2)


class Formatting(unittest.TestCase):
    def test_numbers_read_as_the_page_shows_them(self):
        self.assertEqual(rs.money(34639000000.0), "$34.6bn")
        self.assertEqual(rs.money(-498000000.0), u"−$498.0mn")
        self.assertEqual(rs.pct(-3.9, True), u"−3.9%")
        self.assertEqual(rs.pct(0.04, True), "0.0%")                        # no "+0.0%"
        self.assertEqual(rs.pct(None), u"—")
        self.assertEqual(rs.count(-409533, True), u"−409,533")


class MissingPart(unittest.TestCase):
    def test_a_failing_part_is_named_not_raised(self):
        from fastapi import HTTPException
        missing = {}

        def gone():
            raise HTTPException(404, "x")
        self.assertIsNone(rs._part(missing, "news", "kept for S&P 500 companies only", gone))
        self.assertEqual(missing, {"news": "kept for S&P 500 companies only"})

        def broken():
            raise ValueError("boom")
        self.assertIsNone(rs._part(missing, "holders", "none", broken))
        self.assertEqual(missing["holders"], "could not be read just now")


SAMPLE = {
    "title": "Example Corp (EXM)", "subtitle": u"Revenue grew 5.0% — a test.", "meta": ["Nasdaq"],
    "blocks": [
        {"t": "h", "text": "Results by Year"},
        {"t": "stats", "items": [["Share Price", "$10.00", "close"], ["Market Value", "$1.0bn", ""]]},
        {"t": "table", "name": "Results by Year", "title": "Results by year", "source": "SEC EDGAR (10-K).",
         "cols": [["Year Ended", "l"], ["Revenue", "r"], ["Growth", "r"]],
         "rows": [["Dec 2025", "$1.0bn", u"—"]], "values": [["2025-12-31", 1e9, None]],
         "formats": [None, "#,##0", "0.0"], "units": [None, "USD", "%"], "strong": [0], "note": "A note."},
        {"t": "p", "style": "sub", "text": "China"},
        {"t": "quote", "text": "We sell in China.", "cite": "Risk Factors, 10-K"},
        {"t": "list", "ordered": True, "items": ["One risk", u"Another ≥ risk"]},
        {"t": "links", "items": [["3 November 2026", "Results", "https://example.com/a?b=1&c=(2)"]]},
        {"t": "links", "style": "note", "items": [[None, "Read the annual report.", "https://example.com/10k"]]},
        {"t": "p", "style": "note", "text": "Notes & <things>"},
    ],
    "calc": [["Growth", "(a ÷ b − 1) × 100"]], "sources": ["SEC EDGAR"], "footer": "Plover Analytics",
}


class Files(unittest.TestCase):
    def test_pdf_is_a_pdf_with_every_page_object(self):
        data = doc_render.to_pdf(rs.finish(SAMPLE))
        self.assertTrue(data.startswith(b"%PDF-1.7"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/FontFile2", data)                 # the font travels with the file
        self.assertIn(b"/ToUnicode", data)                 # so its words can be copied and searched
        self.assertIn(b"/URI (https://example.com/a?b=1&c=\\(2\\))", data)

    def test_word_and_excel_are_well_formed(self):
        for fmt in ("docx", "xlsx"):
            z = zipfile.ZipFile(io.BytesIO(doc_render.RENDER[fmt](rs.finish(SAMPLE))))
            for name in z.namelist():
                if name.endswith((".xml", ".rels")):
                    xml.dom.minidom.parseString(z.read(name))
            self.assertIn("[Content_Types].xml", z.namelist())

    def test_word_carries_the_house_style(self):
        z = zipfile.ZipFile(io.BytesIO(doc_render.to_docx(rs.finish(SAMPLE))))
        names = z.namelist()
        self.assertIn("word/media/plover-mark.png", names)                  # the masthead's logo
        self.assertIn("word/fonts/font1.odttf", names)                      # IBM Plex Sans, embedded
        fonts = z.read("word/fontTable.xml").decode("utf-8")
        self.assertIn('w:embedRegular', fonts)
        self.assertIn('w:embedBold', fonts)
        # an embedded font is the font file with its first 32 bytes XORed with its key (ECMA-376 17.8.1)
        raw = (doc_render.ASSETS / "IBMPlexSans-400.ttf").read_bytes()
        key = bytes.fromhex(doc_render._FONT_KEYS[0].strip("{}").replace("-", ""))[::-1]
        odttf = z.read("word/fonts/font1.odttf")
        self.assertEqual(bytes(b ^ key[i % 16] for i, b in enumerate(odttf[:32])) + odttf[32:], raw)
        body = z.read("word/document.xml").decode("utf-8")
        self.assertIn("Exhibit 1: ", body)
        self.assertIn("Source: SEC EDGAR (10-K).", body)
        self.assertIn("NUMPAGES", z.read("word/footer1.xml").decode("utf-8"))

    def test_excel_keeps_numbers_as_numbers_and_missing_empty(self):
        z = zipfile.ZipFile(io.BytesIO(doc_render.to_xlsx(rs.finish(SAMPLE))))
        sheet = z.read("xl/worksheets/sheet2.xml").decode("utf-8")      # sheet 1 is the Brief, 2 the exhibit
        self.assertIn("Revenue (USD)", sheet)
        self.assertIn("<v>1000000000.0</v>", sheet)
        self.assertNotIn("<v>0.0</v>", sheet)              # the missing growth is an empty cell, not 0
        self.assertIn('<sheet name="Ex 1 Results by Year"', z.read("xl/workbook.xml").decode("utf-8"))

    def test_notes_go_where_the_reader_put_them(self):
        doc = dict(SAMPLE, blocks=[{"t": "h", "text": "A"}, {"t": "notes"}, {"t": "h", "text": "B"}])
        out = rs.with_notes(doc, "First line.\n\nSecond line.")
        self.assertEqual([b.get("text") for b in out["blocks"]],
                         ["A", "Your Notes", "Written by the reader, not by Plover.", "First line.", "Second line.", "B"])
        self.assertEqual([b["t"] for b in rs.with_notes(doc, "  ")["blocks"]], ["h", "h"])


class Endpoint(unittest.TestCase):
    """On this machine's data, when it is here."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        from app import sec_api
        if not sec_api.DB_PATH.exists():
            raise unittest.SkipTest("data/sec.duckdb not built")
        cls.client = TestClient(app)
        r = cls.client.get("/api/v1/us/research/AMD")
        if r.status_code == 503:
            raise unittest.SkipTest("data/sec.duckdb without the full US set")
        cls.brief = r.json()
        if not cls.brief.get("market") or not cls.brief.get("peers"):
            raise unittest.SkipTest("data/sec.duckdb without the full US set")

    def test_agrees_with_the_screener_row(self):
        b = self.brief
        m = self.client.get("/api/v1/us/financials/metrics/AMD").json()
        self.assertEqual(b["latest"]["metrics"]["revenue_growth_pct"], m["metrics"]["revenue_growth_pct"])
        self.assertEqual(b["market"]["market_cap"], m["metrics"]["market_cap"])
        self.assertEqual(b["peers"]["company"]["values"]["pe_x"], m["metrics"]["pe_x"])
        if b["years"]:
            self.assertEqual(b["years"][0]["revenue"], b["latest"]["size"]["revenue"])

    def test_automatic_peers_are_five_others_in_its_industry(self):
        p = self.brief["peers"]
        tickers = [x["ticker"] for x in p["peers"]]
        self.assertTrue(p["auto"])
        self.assertLessEqual(len(tickers), 5)
        self.assertNotIn("AMD", tickers)
        pes = [x["values"]["pe_x"] for x in p["peers"] if x["values"]["pe_x"] is not None]
        if len(pes) and p["median"]:
            self.assertAlmostEqual(p["median"]["pe_x"], statistics.median(pes))

    def test_the_readers_own_peers_and_columns(self):
        d = self.client.get("/api/v1/us/research/AMD?s=peers&p=INTC,ZZZZZ&pc=pe_x,roe_pct").json()
        p = d["peers"]
        self.assertFalse(p["auto"])
        self.assertEqual([x["ticker"] for x in p["peers"]], ["INTC"])
        self.assertEqual(p["unknown"], ["ZZZZZ"])
        table = [b for b in d["doc"]["blocks"] if b["t"] == "table"][0]
        self.assertEqual([c[0] for c in table["cols"]], ["Company", "Price / Earnings", "Return on Equity"])
        self.assertEqual([b["text"] for b in d["doc"]["blocks"] if b["t"] == "h"], ["Against Its Peers"])

    def test_every_part_is_there_or_named(self):
        b = self.brief
        for key in ("business", "years", "peers", "holders", "insiders", "risks", "news", "results"):
            self.assertTrue(b.get(key) is not None or key in b["missing"], key)

    def test_a_topics_paragraph_is_quoted_once(self):
        d = self.client.get("/api/v1/us/research/AMD?s=topics&k=China|export%20controls").json()
        quotes = [b["text"] for b in d["doc"]["blocks"] if b["t"] == "quote"]
        self.assertEqual(len(quotes), len(set(quotes)))

    def test_questions_need_an_account_and_are_never_cached(self):
        r = self.client.get("/api/v1/us/research/AMD?s=questions&ask=Who%20owns%20AMD%3F")
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        d = r.json()
        self.assertFalse(d["signed_in"])
        self.assertIsNone(d["questions"])
        self.assertIn("questions", d["missing"])

    def test_downloads_match_the_page(self):
        q = "s=years,peers,notes&per=q&n=4"
        page = self.client.get("/api/v1/us/research/AMD?" + q).json()["doc"]
        for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("xlsx", b"PK")):
            r = self.client.post("/api/v1/us/research/AMD/download", json={"format": fmt, "query": q, "notes": "Mine."})
            self.assertEqual(r.status_code, 200, r.text[:200])
            self.assertTrue(r.content.startswith(magic))
            self.assertIn("attachment", r.headers["content-disposition"])
        z = zipfile.ZipFile(io.BytesIO(self.client.post("/api/v1/us/research/AMD/download",
                                                        json={"format": "docx", "query": q, "notes": "Mine."}).content))
        text = z.read("word/document.xml").decode("utf-8")
        for b in page["blocks"]:
            if b["t"] == "table":
                for row in b["rows"]:
                    for cell in row:
                        self.assertIn(doc_render._x(cell), text)
        self.assertIn("Mine.", text)
        self.assertEqual(self.client.post("/api/v1/us/research/AMD/download", json={"format": "pptx"}).status_code, 400)

    def test_keeping_usual_choices_needs_an_account(self):
        self.assertEqual(self.client.get("/api/v1/us/research/layout").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/us/research/layout", json={"layout": {"s": "peers"}}).status_code, 401)

    def test_unknown_ticker(self):
        self.assertEqual(self.client.get("/api/v1/us/research/ZZZZZ").status_code, 404)

    def test_front_page_title(self):
        html = self.client.get("/?task=research&c=AMD").text
        self.assertIn("Research · Plover Analytics</title>", html)


if __name__ == "__main__":
    unittest.main()
