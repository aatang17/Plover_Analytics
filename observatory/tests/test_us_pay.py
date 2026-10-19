# -*- coding: utf-8 -*-
"""US executive pay: the proxy extractor and the API's reading of it.

Every case here is a pattern found in real S&P 500 proxies (September 2026):
duplicate facts, nil cells, a CEO who left in April, a CEO tagged only by
name, a former CEO kept on with zeros, names that match their member only
by surname, and a shareholder return whose base moves with every proxy.
No network.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_us_pay
"""
import datetime
import importlib.util
import io
import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile

import duckdb

from app import backfill, heartbeat, sec_api, us_pay_api

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "sec_proxy_pay", str(ROOT / "equity" / "sec_proxy_pay.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PX = load_script()


def holdings_xlsx(rows, as_of="22-Sep-2026"):
    """A minimal workbook in the fund's layout."""
    strings = ["Fund Name:", "SPY", "Holdings:", "As of " + as_of, "Name", "Ticker", "Weight"]
    cells = [[("A1", 0), ("B1", 1)], [("A3", 2), ("B3", 3)], [("A5", 4), ("B5", 5), ("E5", 6)]]
    for i, (name, ticker, weight) in enumerate(rows):
        strings += [name, ticker]
        cells.append([("A%d" % (6 + i), len(strings) - 2), ("B%d" % (6 + i), len(strings) - 1),
                      ("E%d" % (6 + i), None, weight)])
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sst = "<sst %s>%s</sst>" % (ns, "".join("<si><t>%s</t></si>" % s.replace("&", "&amp;")
                                          for s in strings))
    body = ""
    for row in cells:
        body += "<row>"
        for c in row:
            if len(c) == 3:
                body += '<c r="%s"><v>%s</v></c>' % (c[0], c[2])
            else:
                body += '<c r="%s" t="s"><v>%d</v></c>' % c
        body += "</row>"
    sheet = "<worksheet %s><sheetData>%s</sheetData></worksheet>" % (ns, body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


INSTANCE = b"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ecd="http://xbrl.sec.gov/ecd/2025" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:dei="http://xbrl.sec.gov/dei/2025">
  <context id="fy"><entity><identifier scheme="x">1</identifier></entity>
    <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>
  <context id="fy_ceo"><entity><identifier scheme="x">1</identifier>
    <segment><xbrldi:explicitMember dimension="ecd:IndividualAxis">cat:Mr.CreedMember</xbrldi:explicitMember></segment>
    </entity><period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <ecd:PeoTotalCompAmt contextRef="fy" unitRef="usd" decimals="0">40632724</ecd:PeoTotalCompAmt>
  <ecd:PeoTotalCompAmt contextRef="fy" unitRef="usd" decimals="0">40632724</ecd:PeoTotalCompAmt>
  <ecd:PeoActuallyPaidCompAmt contextRef="fy" unitRef="usd" decimals="0" xsi:nil="true"/>
  <ecd:PeoTotalCompAmt contextRef="fy_ceo" unitRef="usd" decimals="0">-5</ecd:PeoTotalCompAmt>
  <ecd:PeoName contextRef="fy">James  Dimon</ecd:PeoName>
  <ecd:PvpTableTextBlock contextRef="fy">&lt;table&gt;big&lt;/table&gt;</ecd:PvpTableTextBlock>
  <dei:EntityRegistrantName contextRef="fy">JPMorgan Chase &amp; Co.</dei:EntityRegistrantName>
</xbrl>"""


class ExtractorParseTest(unittest.TestCase):
    def test_holdings_file_reads_date_and_members(self):
        as_of, rows = PX.parse_holdings(holdings_xlsx(
            [("NVIDIA CORP", "NVDA", "8.27"), ("BERKSHIRE HATHAWAY INC CL B", "BRK.B", "1.6"),
             ("US DOLLAR", "-", "0.21")]))
        self.assertEqual(as_of, datetime.date(2026, 9, 22))
        self.assertEqual(rows[0], ("NVIDIA CORP", "NVDA", 8.27))
        self.assertEqual(rows[1][1], "BRK.B")

    def test_not_a_workbook_is_refused(self):
        with self.assertRaises(PX.PullError):
            PX.parse_holdings(b"<html>moved</html>")

    def test_instance_facts(self):
        rows = PX.parse_instance(INSTANCE)
        tags = [(r[0], r[1], r[4], r[5], r[6]) for r in rows]
        # duplicates collapse to one row
        self.assertEqual(tags.count(("ecd", "PeoTotalCompAmt", "", 40632724.0, None)), 1)
        # a nil fact is missing, never zero
        self.assertFalse([t for t in tags if t[1] == "PeoActuallyPaidCompAmt"])
        # text blocks stay in the raw file only
        self.assertFalse([t for t in tags if t[1].endswith("TextBlock")])
        # dimensions and negatives as filed; text as filed
        self.assertIn(("ecd", "PeoTotalCompAmt", "ecd:IndividualAxis=cat:Mr.CreedMember", -5.0, None), tags)
        self.assertIn(("ecd", "PeoName", "", None, "James  Dimon"), tags)
        self.assertIn(("dei", "EntityRegistrantName", "", None, "JPMorgan Chase & Co."), tags)


def fact(cik, tag, start, end, value=None, text=None, dims="", accn="A2", filed="2026-04-01",
         prefix="ecd"):
    return {"cik": cik, "prefix": prefix, "tag": tag, "period_start": datetime.date.fromisoformat(start),
            "period_end": datetime.date.fromisoformat(end), "dims": dims, "value_num": value,
            "value_text": text, "unit": "usd", "accn": accn,
            "filed": datetime.date.fromisoformat(filed)}


class YearsTest(unittest.TestCase):
    """How the API reads CEOs out of the picked facts."""

    def year(self, rows, cik=1, end="2025-12-31", names=None):
        return us_pay_api._years(rows, names)[cik][datetime.date.fromisoformat(end)]

    def test_single_ceo(self):
        y = self.year([fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 40632724),
                       fact(1, "PeoName", "2025-01-01", "2025-12-31", text="James Dimon")])
        self.assertEqual(y["values"]["ceo_total_pay"], 40632724)
        self.assertEqual(y["ceo_names"], ["James Dimon"])
        self.assertEqual(y["ceo_count"], 1)

    def test_two_ceos_listed_separately_never_summed(self):
        """Caterpillar 2025: Umpleby to 30 April, Creed after — names and pay
        dated to part of the year, pay tagged by person."""
        y = self.year([
            fact(1, "PeoName", "2025-01-01", "2025-04-30", text="Mr. Umpleby"),
            fact(1, "PeoName", "2025-05-01", "2025-12-31", text="Mr. Creed"),
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 22191496,
                 dims="ecd:IndividualAxis=CAT:Mr.UmplebyMember"),
            fact(1, "PeoTotalCompAmt", "2025-05-01", "2025-12-31", 17008077,
                 dims="ecd:IndividualAxis=CAT:Mr.CreedMember"),
            fact(1, "NonPeoNeoAvgTotalCompAmt", "2025-01-01", "2025-12-31", 7573910)])
        self.assertEqual(y["ceo_count"], 2)
        self.assertIsNone(y["values"]["ceo_total_pay"])
        self.assertEqual({c["name"]: c["total_pay"] for c in y["ceos"]},
                         {"Umpleby": 22191496, "Creed": 17008077})
        # Umpleby's name is dated to April: Creed was CEO at the year end.
        self.assertEqual(y["year_end_ceos"], ["Creed"])

    def test_names_match_members_by_surname(self):
        """UnitedHealth: 'Mr. Hemsley' against unh:StephenHemsleyMember."""
        y = self.year([
            fact(1, "PeoName", "2025-05-13", "2025-12-31", text="Mr.\xa0Hemsley"),
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 60938062,
                 dims="ecd:IndividualAxis=unh:StephenHemsleyMember")])
        self.assertEqual(y["ceos"][0]["name"], "Hemsley")

    def test_an_unnamed_member_gets_its_own_label(self):
        y = self.year([fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 1,
                            dims="ecd:IndividualAxis=unh:StephenHemsleyMember")])
        self.assertEqual(y["ceos"][0]["name"], "Stephen Hemsley")

    def test_other_executives_names_are_not_ceos(self):
        """Apple tags every named executive's name with PeoName."""
        y = self.year([
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 74294811),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Mr. Cook",
                 dims="ecd:ExecutiveCategoryAxis=ecd:PeoMember|ecd:IndividualAxis=AAPL:CookMember"),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Kate Adams",
                 dims="ecd:ExecutiveCategoryAxis=ecd:NonPeoNeoMember|ecd:IndividualAxis=AAPL:KateAdamsMember")])
        self.assertEqual(y["ceo_names"], ["Cook"])

    def test_former_ceo_with_zeros_is_dropped(self):
        """P&G keeps a column for David Taylor with zeros after he left."""
        y = self.year([
            fact(1, "PeoName", "2024-07-01", "2025-06-30", text="Mr. Moeller",
                 dims="ecd:IndividualAxis=pg:Mr.MoellerMember"),
            fact(1, "PeoTotalCompAmt", "2024-07-01", "2025-06-30", 21909816,
                 dims="ecd:IndividualAxis=pg:Mr.MoellerMember"),
            fact(1, "PeoTotalCompAmt", "2024-07-01", "2025-06-30", 0,
                 dims="ecd:IndividualAxis=pg:DavidTaylorMember"),
            fact(1, "PeoActuallyPaidCompAmt", "2024-07-01", "2025-06-30", 0,
                 dims="ecd:IndividualAxis=pg:DavidTaylorMember")], end="2025-06-30")
        self.assertEqual([c["name"] for c in y["ceos"]], ["Moeller"])
        self.assertEqual(y["ceo_count"], 1)


class TaggingQuirksTest(YearsTest):
    """Three more patterns from the full S&P 500 load."""

    def test_named_former_ceos_with_zeros_are_dropped(self):
        """Starbucks names three former CEOs for the year, each with zeros."""
        rows = [fact(1, "PeoTotalCompAmt", "2024-09-30", "2025-09-28", 30992773,
                     dims="ecd:IndividualAxis=sbux:BrianNiccolMember"),
                fact(1, "PeoName", "2024-09-30", "2025-09-28", text="Brian Niccol",
                     dims="ecd:IndividualAxis=sbux:BrianNiccolMember")]
        for who in ("HowardSchultz", "KevinJohnson"):
            rows += [fact(1, "PeoTotalCompAmt", "2024-09-30", "2025-09-28", 0,
                          dims="ecd:IndividualAxis=sbux:%sMember" % who),
                     fact(1, "PeoName", "2024-09-30", "2025-09-28", text=who,
                          dims="ecd:IndividualAxis=sbux:%sMember" % who)]
        y = self.year(rows, end="2025-09-28")
        self.assertEqual(y["ceo_names"], ["Brian Niccol"])
        self.assertEqual(y["ceo_count"], 1)

    def test_one_ceo_tagged_twice_counts_once(self):
        """Trane: 'First PEO' and 'Second PEO' with identical figures."""
        y = self.year([
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 27262157,
                 dims="ecd:IndividualAxis=tt:FirstPEOMember"),
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 27262157,
                 dims="ecd:IndividualAxis=tt:SecondPEOMember"),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Mr. Regnery")])
        self.assertEqual(y["ceo_count"], 1)

    def test_one_ceo_named_two_ways_counts_once(self):
        """Arista: 'Ms. Ullal' and 'Jayshree Ullal' for the same year."""
        y = self.year([
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 2872145),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Ms. Ullal"),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Jayshree Ullal",
                 dims="ecd:ExecutiveCategoryAxis=ecd:PeoMember")])
        self.assertEqual(y["ceo_names"], ["Jayshree Ullal"])
        self.assertEqual(y["ceo_count"], 1)


class NameCleanupTest(YearsTest):
    def test_footnote_marks_and_tagged_tables_are_not_names(self):
        y = self.year([
            fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 1),
            fact(1, "PeoName", "2025-01-01", "2025-12-31", text="(1)Ms. Harris"),
            fact(1, "PeoName", "2025-01-01", "2025-12-31",
                 text="YearPEONon-PEO NEOs2025 " + "Steven J. Moskowitz, Daniel K. Schlanger " * 4)])
        self.assertEqual(y["ceo_names"], ["Harris"])


class DocumentNamesTest(YearsTest):
    """Names spelled out from the proxy document and the 10-K certifications."""

    END = datetime.date(2025, 12, 31)

    def test_surname_takes_the_documents_full_name(self):
        """Merck tags "Mr. Davis"; the proxy writes Robert M. Davis."""
        names = {1: {"tagged": {"Mr. Davis": ["Robert M. Davis"]}, "certs": {}}}
        y = self.year([fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 1),
                       fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Mr. Davis")], names=names)
        self.assertEqual(y["ceo_names"], ["Robert M. Davis"])

    def test_no_name_takes_the_10k_signer(self):
        """Microsoft tags no name; Satya Nadella signs the CEO certification."""
        names = {1: {"tagged": {}, "certs": {self.END: [
            ("Satya Nadella", "Chief Executive Officer"),
            ("Amy E. Hood", "Executive Vice President and Chief Financial Officer")]}}}
        y = self.year([fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 1)], names=names)
        self.assertEqual(y["ceo_names"], ["Satya Nadella"])

    def test_placeholder_member_takes_the_one_tagged_name(self):
        """Constellation: "Mr. Dominguez" undimensioned, pay under ceg:Peo1Member."""
        y = self.year([fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Mr. Dominguez"),
                       fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 5,
                            dims="ecd:IndividualAxis=ceg:Peo1Member")])
        self.assertEqual(y["ceos"][0]["name"], "Dominguez")

    def two(self, certs, prev=False):
        rows = [fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 10,
                     dims="ecd:IndividualAxis=x:WittyMember"),
                fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 20,
                     dims="ecd:IndividualAxis=x:HemsleyMember")]
        names = {1: {"tagged": {"x:WittyMember": ["Andrew Witty"],
                                "x:HemsleyMember": ["Stephen Hemsley"]}, "certs": certs}}
        return self.year(rows, names=names)

    def test_successor_is_the_10k_signer(self):
        y = self.two({self.END: [("Stephen Hemsley", "Chair and Chief Executive Officer"),
                                 ("Wayne DeVeydt", "Chief Financial Officer")]})
        self.assertEqual(y["year_end_ceos"], ["Stephen Hemsley"])
        self.assertEqual([c["name"] for c in y["ceos"] if not c["at_year_end"]], ["Andrew Witty"])

    def test_co_ceos_both_sign(self):
        y = self.two({self.END: [("Andrew Witty", "Co-Chief Executive Officer"),
                                 ("Stephen Hemsley", "Co-Chief Executive Officer")]})
        self.assertEqual(sorted(y["year_end_ceos"]), ["Andrew Witty", "Stephen Hemsley"])

    def test_no_evidence_lists_every_ceo(self):
        """Never a guess: with nothing to tell them apart, both stay."""
        self.assertEqual(len(self.two({})["year_end_ceos"]), 2)

    def test_one_spelling_per_person_the_signed_one(self):
        names = {1: {"tagged": {"Mr. Creed": ["Joe Creed"]},
                     "certs": {self.END: [("Joseph E. Creed", "Chief Executive Officer")]}}}
        y = self.year([fact(1, "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 1),
                       fact(1, "PeoName", "2025-01-01", "2025-12-31", text="Mr. Creed")], names=names)
        self.assertEqual(y["ceo_names"], ["Joseph E. Creed"])


class NameReadersTest(unittest.TestCase):
    """The extractor's readers of names in documents (no network)."""

    def test_tagged_names(self):
        self.assertEqual(PX.tagged_names("Mr.\xa0Davis"), [{"name": "Davis", "surname_only": True}])
        self.assertEqual([n["name"] for n in PX.tagged_names("Mr. Armstrong and Mr. Zamarin")],
                         ["Armstrong", "Zamarin"])
        self.assertEqual(PX.tagged_names("H. Lawrence Culp, Jr."),
                         [{"name": "H. Lawrence Culp, Jr.", "surname_only": False}])
        self.assertEqual(PX.tagged_names("Travis DStice")[0]["name"], "Travis D. Stice")
        self.assertEqual([n["name"] for n in PX.tagged_names("David J. Rosa / Gary S. Guthart, Ph.D.")],
                         ["David J. Rosa", "Gary S. Guthart"])
        self.assertEqual([n["name"] for n in PX.tagged_names(
            "\u20222023 and 2022: Michael J. Hennigan\u20222021: Mr. Hennigan")], ["Michael J. Hennigan"])
        for placeholder in ("Peo1", "PEO1", "First PEO", "Peo"):
            self.assertEqual(PX.tagged_names(placeholder), [])
        self.assertEqual(PX.tagged_names(PX.member_label("pcg:PEO4PacificGasElectricCoMember")), [])

    def test_spelled_out_prefers_the_spelling_beside_the_title(self):
        text = ("Robert M. Davis, Chairman and Chief Executive Officer, said. " * 3 +
                "Dear shareholders, Mr. Davis thanks you. Officers Robert M. Davis.")
        self.assertEqual(PX.spelled_out(text, "Davis"), ("Robert M. Davis", 4))
        self.assertEqual(PX.resolve_name("In May the board met. John C. May, CEO.",
                                         {"name": "May", "surname_only": True})[0], "John C. May")

    def test_certifications(self):
        text = ("CERTIFICATION I, Shailesh Jejurikar, certify that: 1. I have reviewed this report "
                "/s/ Shailesh Jejurikar Shailesh Jejurikar President and Chief Executive Officer "
                "August 4, 2026 I, Andre Schulten, certify that: 1. I have reviewed "
                "/s/ Andre Schulten Andre Schulten Chief Financial Officer August 4, 2026")
        self.assertEqual(PX.parse_certifications(text), [
            ("Shailesh Jejurikar", "President and Chief Executive Officer"),
            ("Andre Schulten", "Chief Financial Officer")])


class ApiTest(unittest.TestCase):
    """The endpoints over a small database built the way the extractor builds it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.con = duckdb.connect(os.path.join(self.dir, "sec.duckdb"))
        self.addCleanup(self.con.close)
        self.con.execute(PX.SCHEMA_SQL)
        self._cur = sec_api._cur
        sec_api._cur = lambda: self.con.cursor()
        self.addCleanup(setattr, sec_api, "_cur", self._cur)
        c = self.con
        c.execute("INSERT INTO us_index_snapshots VALUES (1,'S&P 500 (SPY holdings)','u','2026-09-22',"
                  "now(),'s','r',3,2,'US DOLLAR (-)')")
        c.execute("INSERT INTO us_index_members VALUES (1,18230,'CAT','CAT','CATERPILLAR INC',0.5),"
                  "(1,19617,'JPM','JPM','JPMORGAN CHASE & CO',1.5)")
        for accn, filed in (("A25", "2025-04-30"), ("A26", "2026-04-30")):
            c.execute("INSERT INTO sec_px_filings (cik, accn, form, filed, status, facts) "
                      "VALUES (18230, ?, 'DEF 14A', ?, 'ok', 1)", [accn, filed])
        c.execute("INSERT INTO sec_px_checks VALUES (18230, now(), 'ok', 2, 2, NULL)")
        rows = [
            # the 2025 proxy: base 2020
            ("A25", "TotalShareholderRtnAmt", "2020-01-01", "2024-12-31", 274.14, ""),
            ("A25", "TotalShareholderRtnAmt", "2020-01-01", "2023-12-31", 219.91, ""),
            ("A25", "PeoTotalCompAmt", "2024-01-01", "2024-12-31", 25261296, ""),
            ("A25", "PeoTotalCompAmt", "2023-01-01", "2023-12-31", 25800000, ""),
            # the 2026 proxy: base 2021
            ("A26", "TotalShareholderRtnAmt", "2021-01-01", "2025-12-31", 346.12, ""),
            ("A26", "TotalShareholderRtnAmt", "2021-01-01", "2024-12-31", 215.91, ""),
            ("A26", "PeoTotalCompAmt", "2024-01-01", "2024-12-31", 25300000, ""),
            ("A26", "PeoTotalCompAmt", "2025-01-01", "2025-12-31", 22191496,
             "ecd:IndividualAxis=CAT:Mr.UmplebyMember"),
            ("A26", "PeoTotalCompAmt", "2025-05-01", "2025-12-31", 17008077,
             "ecd:IndividualAxis=CAT:Mr.CreedMember"),
            ("A26", "NonPeoNeoAvgTotalCompAmt", "2025-01-01", "2025-12-31", 7573910, ""),
        ]
        for accn, tag, start, end, value, dims in rows:
            c.execute("INSERT INTO sec_px_facts VALUES (?,18230,'ecd',?,?,?,?,?,NULL,'usd','2')",
                      [accn, tag, start, end, dims, value])

    def test_returns_come_from_one_proxy_with_its_base(self):
        d = us_pay_api.company("CAT", basis="latest", as_of="")
        by = {y["fiscal_year_end"]: y for y in d["years"]}
        self.assertEqual(by["2025-12-31"]["values"]["tsr"], 346.12)
        self.assertEqual(by["2024-12-31"]["values"]["tsr"], 215.91)   # not 274.14 on the old base
        self.assertIsNone(by["2023-12-31"]["values"]["tsr"])            # not in the latest proxy
        self.assertEqual(by["2025-12-31"]["tsr_base"], "2021-01-01")

    def test_latest_and_first_basis(self):
        latest = {y["fiscal_year_end"]: y for y in us_pay_api.company("CAT", basis="latest", as_of="")["years"]}
        first = {y["fiscal_year_end"]: y for y in us_pay_api.company("CAT", basis="first", as_of="")["years"]}
        self.assertEqual(latest["2024-12-31"]["values"]["ceo_total_pay"], 25300000)
        self.assertEqual(first["2024-12-31"]["values"]["ceo_total_pay"], 25261296)

    def test_as_of_drops_later_proxies(self):
        d = us_pay_api.company("CAT", basis="latest", as_of="2025-12-31")
        ends = [y["fiscal_year_end"] for y in d["years"]]
        self.assertNotIn("2025-12-31", ends)
        self.assertEqual(d["years"][0]["values"]["tsr"], 274.14)

    def test_index_without_evidence_lists_both_ceos_and_counts_status(self):
        d = us_pay_api.index(basis="latest", as_of="")
        cat = [c for c in d["companies"] if c["ticker"] == "CAT"][0]
        jpm = [c for c in d["companies"] if c["ticker"] == "JPM"][0]
        self.assertEqual(cat["ceo_count"], 2)
        self.assertIsNone(cat["values"]["ceo_total_pay"])
        self.assertEqual({p["name"]: p["total_pay"] for p in cat["ceos"]},
                         {"Umpleby": 22191496, "Creed": 17008077})
        self.assertEqual(cat["values"]["neo_avg_total_pay"], 7573910)
        self.assertEqual(jpm["status"], "not_checked")
        self.assertEqual(d["coverage"], {"with_pay": 1, "no_pay_table": 0, "not_checked": 1, "failed": 0})

    def test_index_shows_the_10k_signer_with_their_own_pay(self):
        self.con.execute("INSERT INTO sec_px_names VALUES (18230,'K26','cert','EX-31.1',0,"
                         "'Joseph E. Creed','Chief Executive Officer',NULL,'2025-12-31','2026-02-13','t')")
        self.con.execute("INSERT INTO sec_px_names VALUES (18230,'A26','proxy','CAT:Mr.UmplebyMember',0,"
                         "'Jim Umpleby',NULL,3,NULL,'2026-04-30','t')")
        d = us_pay_api.index(basis="latest", as_of="")
        cat = [c for c in d["companies"] if c["ticker"] == "CAT"][0]
        self.assertEqual(cat["ceo_names"], ["Joseph E. Creed"])
        self.assertEqual(cat["values"]["ceo_total_pay"], 17008077)
        self.assertEqual(cat["ceos"], [])
        self.assertEqual(cat["left_in_year"], ["Jim Umpleby"])

    def test_unknown_ticker_is_404(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            us_pay_api.company("ZZZZ", basis="latest", as_of="")
        self.assertEqual(cm.exception.status_code, 404)

    def test_health_flags_members_not_checked(self):
        row = us_pay_api.health()[0]
        self.assertEqual(row["dataset"], "sec-proxy-pay")
        self.assertFalse(row["stale"])
        self.assertTrue(any("1 of 2 members not checked" in f for f in row["shape_flags"]))


class RefreshUsPayTest(unittest.TestCase):
    """app/backfill.refresh_us_pay: slices on a copy until every member is checked."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        saved = {n: getattr(backfill, n) for n in ("LIVE_SEC", "DATA_DIR", "_run")}
        self.addCleanup(lambda: [setattr(backfill, n, v) for n, v in saved.items()])
        backfill.LIVE_SEC = self.dir / "sec.duckdb"
        backfill.DATA_DIR = self.dir
        real = heartbeat.JOURNAL_PATH
        heartbeat.JOURNAL_PATH = self.dir / "journal.json"
        self.addCleanup(setattr, heartbeat, "JOURNAL_PATH", real)
        ua = os.environ.get("EDGAR_USER_AGENT")
        os.environ["EDGAR_USER_AGENT"] = "Test test@example.com"
        self.addCleanup(lambda: os.environ.__setitem__("EDGAR_USER_AGENT", ua) if ua
                        else os.environ.pop("EDGAR_USER_AGENT", None))
        backfill._stopping = False
        con = duckdb.connect(str(backfill.LIVE_SEC))
        con.execute(PX.SCHEMA_SQL)
        con.execute("INSERT INTO us_index_snapshots (snapshot_id, index_name) VALUES (1, 'S&P 500 (SPY holdings)')")
        con.executemany("INSERT INTO us_index_members (snapshot_id, cik) VALUES (1, ?)",
                        [(i,) for i in range(1, 6)])
        con.close()
        self.calls = []

    def fake_run(self, per_call):
        """Check `per_call` unchecked members per run, like --limit does."""
        def run(cmd, env, cwd):
            self.calls.append(cmd)
            con = duckdb.connect(cmd[cmd.index("--db") + 1])
            todo = [r[0] for r in con.execute(
                "SELECT cik FROM us_index_members WHERE cik NOT IN "
                "(SELECT cik FROM sec_px_checks) ORDER BY cik LIMIT ?", [per_call]).fetchall()]
            for cik in todo:
                con.execute("INSERT INTO sec_px_checks VALUES (?, now()::TIMESTAMP, 'ok', 0, 0, NULL)", [cik])
            con.close()
            return 0
        return run

    def checked(self):
        con = duckdb.connect(str(backfill.LIVE_SEC), read_only=True)
        try:
            return con.execute("SELECT count(DISTINCT cik) FROM sec_px_checks").fetchone()[0]
        finally:
            con.close()

    def test_slices_until_every_member_is_checked(self):
        backfill._run = self.fake_run(2)
        backfill.refresh_us_pay(per_slice=2)
        self.assertEqual(self.checked(), 5)
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("--skip-members", self.calls[0])
        self.assertIn("--skip-members", self.calls[1])
        self.assertEqual(heartbeat.journal()["sec-proxy-pay"]["outcome"], "published")

    def test_a_slice_that_records_nothing_stops_and_leaves_the_file(self):
        backfill._run = lambda cmd, env, cwd: 1
        before = backfill.LIVE_SEC.stat().st_mtime_ns
        backfill.refresh_us_pay(per_slice=2)
        self.assertEqual(backfill.LIVE_SEC.stat().st_mtime_ns, before)
        self.assertEqual(heartbeat.journal()["sec-proxy-pay"]["outcome"], "failed")

    def test_no_user_agent_means_no_run(self):
        os.environ.pop("EDGAR_USER_AGENT", None)
        backfill._run = self.fake_run(2)
        backfill.refresh_us_pay()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
