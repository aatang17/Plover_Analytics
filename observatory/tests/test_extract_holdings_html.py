# -*- coding: utf-8 -*-
"""The named-holdings fallback: a table whose XBRL tags too few rows is read
from the filing's HTML (equity/holdings_html.py, used by equity/extract.py).

2026-11-05: Sumitomo Mitsui Financial Group's FY2025 report (S100W0S7) tagged 9
rows for its bank against 785 listed issues, and the site showed its holdings
falling to ¥1.1tn for one year. The fixtures here are small hand-written copies
of the two table layouts filers use. No network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_extract_holdings_html
"""
import io
import os
import sys
import unittest
import zipfile

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "equity"))

import extract  # noqa: E402
import holdings_html as hh  # noqa: E402

P = "jpcrp_cor:"
LARGEST = "DetailsOfSpecifiedInvestmentEquitySecuritiesHeldForPurposesOtherThanPureInvestmentLargestHoldingCompany"
DEEMED = "DetailsOfDeemedHoldingsOfEquitySecuritiesHeldForPurposesOtherThanPureInvestmentLargestHoldingCompany"
REPORTING = "DetailsOfSpecifiedInvestmentEquitySecuritiesHeldForPurposesOtherThanPureInvestmentReportingCompany"


def tag(element, text):
    return u'<ix:nonNumeric name="%s%s" contextRef="CurrentYearInstant">%s</ix:nonNumeric>' % (
        P, element, text)


# SMFG's layout: shares and book value side by side, one row per holding, a
# name wrapped over two <p> lines, tags on the first table only.
SIDE_HEAD = (u"<tr><td rowspan='2'>銘柄</td><td colspan='2'>当事業年度</td>"
             u"<td colspan='2'>前事業年度</td><td rowspan='2'>保有目的、業務提携等の概要、"
             u"定量的な保有効果及び株式数が増加した理由</td><td rowspan='2'>当社の株式の保有の有無</td></tr>"
             u"<tr><td>株式数(株)</td><td>貸借対照表計上額<br/>(百万円)</td>"
             u"<td>株式数(株)</td><td>貸借対照表計上額<br/>(百万円)</td></tr>")


def side_row(name, s, b, ps, pb, recip="有"):
    return (u"<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            u"<td><p>取引関係の維持・強化</p></td><td>%s</td></tr>" % (name, s, b, ps, pb, recip))


# The form's own layout: two rows per holding, shares above book value.
TWO_HEAD = (u"<tr><td>銘柄</td><td>当事業年度</td><td>前事業年度</td>"
            u"<td rowspan='3'>保有目的、定量的な保有効果及び株式数が増加した理由</td>"
            u"<td rowspan='3'>当社の株式の保有の有無</td></tr>"
            u"<tr><td>銘柄</td><td>株式数(株)</td><td>株式数(株)</td></tr>"
            u"<tr><td>銘柄</td><td>貸借対照表計上額(千円)</td><td>貸借対照表計上額(千円)</td></tr>")


def two_rows(name, s, ps, b, pb):
    return (u"<tr><td rowspan='2'>%s</td><td>%s</td><td>%s</td><td rowspan='2'>営業取引</td>"
            u"<td rowspan='2'>無</td></tr><tr><td>%s</td><td>%s</td></tr>" % (name, s, ps, b, pb))


SMFG_LIKE = u"""<html><body>
<p>(5)【株式の保有状況】</p>
<p>ロ.銘柄数及び貸借対照表計上額 ○株式会社三井住友銀行が保有する株式</p>
<table><tr><td></td><td>銘柄数(銘柄)</td><td>貸借対照表計上額の合計額(百万円)</td></tr>
<tr><td>上場株式</td><td>785</td><td>3,172,877</td></tr></table>
<p>ハ.特定投資株式及びみなし保有株式の銘柄ごとの株式数、貸借対照表計上額等に関する情報</p>
<p>○株式会社三井住友銀行が保有する株式 (特定投資株式)</p>
<table>""" + SIDE_HEAD + side_row(
    tag("NameOfSecurities" + LARGEST, u"トヨタ自動車株式会社"),
    "146,004,000", "381,946", "188,057,475", "713,113", u"無 (※2)") + side_row(
    u"<p>%s</p><p>工業株式会社</p>" % tag("NameOfSecurities" + LARGEST, u"大和ハウス"),
    "12,087,842", "59,689", "12,893,742", "58,382") + u"""</table>
<p class="pagebreak"></p>
<table>""" + SIDE_HEAD + side_row(
    u"<p>Ares Management</p><p>Corporation</p>", "13,364,740", "292,989", "13,364,740", "268,950",
    u"無") + side_row(
    u"積水ハウス株式会社", "4,315,706", "14,414", "*", "*") + side_row(
    u"株式会社アシックス", "-", "-", "6,607,913", "48,244", u"無") + u"""</table>
<p>(みなし保有株式)</p>
<table>""" + SIDE_HEAD.replace(u"当社の株式の保有の有無", u"当社の株式の保有の有無") + side_row(
    tag("NameOfSecurities" + DEEMED, u"オリンパス株式会社"),
    "39,509,300", "76,944", "39,509,300", "87,414", u"無") + u"""</table>
<p>3 保有目的が純投資目的である投資株式</p>
<table><tr><td>区分</td><td>銘柄数(銘柄)</td></tr><tr><td>上場株式</td><td>1</td></tr></table>
<p>第5【経理の状況】</p>
<table>""" + SIDE_HEAD + side_row(u"注記の表", "1", "1", "1", "1") + u"""</table>
</body></html>"""

ONE_COMPANY = u"""<html><body><p>(5)【株式の保有状況】</p>
<p>c.特定投資株式及びみなし保有株式の銘柄ごとの株式数、貸借対照表計上額等に関する情報</p>
<p>特定投資株式</p>
<table>""" + TWO_HEAD + two_rows(u"(株)ココカラファイン", "4,273", "4,273", "33,000", "23,000") + \
    two_rows(u"東洋炭素株式会社", "108,500", "108,500", "471,000", "-") + u"""</table>
<p>みなし保有株式</p><p>該当事項はありません。</p>
<p>第5【経理の状況】</p></body></html>"""


def package(html):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRL/PublicDoc/0104020_honbun_jpcrp030000-asr-001_X_ixbrl.htm",
                   html.encode("utf-8"))
    return buf.getvalue()


class ReadHtml(unittest.TestCase):
    def test_side_by_side_layout_with_continuation(self):
        rows, notes = hh.holdings_from_html(package(SMFG_LIKE))
        self.assertEqual(notes, [])
        self.assertEqual(sorted(rows), ["largest"])      # deemed + notes table left out
        got = rows["largest"]
        self.assertEqual([r["held_name"] for r in got], [
            u"トヨタ自動車株式会社", u"大和ハウス工業株式会社", u"Ares Management Corporation",
            u"積水ハウス株式会社", u"株式会社アシックス"])
        toyota = got[0]
        self.assertEqual((toyota["shares"], toyota["book_value"], toyota["prior_shares"],
                          toyota["prior_book_value"]),
                         ("146004000", "381946000000", "188057475", "713113000000"))
        self.assertEqual(toyota["reciprocal"], u"無 (※2)")
        self.assertEqual(toyota["source"], "html")
        # an omitted (*) prior year and a sold-out (-) current year stay missing
        self.assertEqual((got[3]["prior_shares"], got[3]["prior_book_value"]), ("", ""))
        self.assertEqual((got[4]["shares"], got[4]["book_value"]), ("", ""))
        self.assertEqual(got[4]["prior_book_value"], "48244000000")

    def test_two_row_layout_single_company_untagged(self):
        rows, notes = hh.holdings_from_html(package(ONE_COMPANY))
        got = rows["reporting"]
        self.assertEqual(len(got), 2)
        self.assertEqual((got[0]["held_name"], got[0]["shares"], got[0]["book_value"],
                          got[0]["prior_shares"], got[0]["prior_book_value"]),
                         (u"(株)ココカラファイン", "4273", "33000000", "4273", "23000000"))
        self.assertEqual(got[1]["prior_book_value"], "")
        self.assertEqual(got[1]["reciprocal"], u"無")

    def test_heading_outranks_the_tags(self):
        # Hosokawa Micron tagged its 特定投資株式 table with deemed elements
        html = ONE_COMPANY.replace(
            u"(株)ココカラファイン",
            tag("NameOfSecurities" + DEEMED.replace("LargestHoldingCompany", "ReportingCompany"),
                u"(株)ココカラファイン"))
        rows, _ = hh.holdings_from_html(package(html))
        self.assertEqual(len(rows["reporting"]), 2)


class ShortRule(unittest.TestCase):
    def totals(self, **issues):
        return [{"holder_table": v, "share_class": "listed", "issue_count": n,
                 "book_value_yen": 3172877000000} for v, n in issues.items()]

    def rows(self, variant, n):
        return [{"holder_table": variant} for _ in range(n)]

    def test_rule(self):
        self.assertEqual(hh.shortfalls(self.rows("largest", 9), self.totals(largest=785)),
                         [("largest", 785, 9)])
        self.assertEqual(hh.shortfalls(self.rows("largest", 30), self.totals(largest=785)), [])
        # second largest company: the rules ask for its top 10
        self.assertEqual(hh.shortfalls(self.rows("second_largest", 13),
                                       self.totals(second_largest=72)), [])
        # too few issues to call
        self.assertEqual(hh.shortfalls([], self.totals(reporting=9)), [])
        # rows filed under a company the totals do not name still count
        self.assertEqual(hh.shortfalls(self.rows("largest", 19), self.totals(reporting=19)), [])

    def test_fallback_replaces_only_a_complete_read(self):
        xbrl = [{"holder_table": "largest", "row": "1", "held_name": u"三井物産",
                 "book_value": "53890000000"}]
        tot = self.totals(largest=785)
        held, problems, remarks = hh.fallback(xbrl, tot, [("largest", 785, 1)],
                                              package(SMFG_LIKE))
        self.assertEqual(len(held), 5)
        self.assertTrue(all(h["source"] == "html" for h in held))
        self.assertIn(u"5 named rows read from the filing's HTML", problems[0])
        self.assertEqual(remarks, [])
        # a read that totals above the filing's own listed total is refused
        small = [dict(t, book_value_yen=1000) for t in tot]
        held, problems, _ = hh.fallback(xbrl, small, [("largest", 785, 1)], package(SMFG_LIKE))
        self.assertEqual(held, xbrl)
        self.assertIn("HTML fallback not used", problems[0])
        # no archived HTML: the XBRL rows stay, and the filing says why
        held, problems, _ = hh.fallback(xbrl, tot, [("largest", 785, 1)], None)
        self.assertEqual(held, xbrl)
        self.assertIn("HTML not archived", problems[0])

    def test_html_naming_no_more_is_a_remark(self):
        xbrl = [{"holder_table": "reporting"}, {"holder_table": "reporting"}]
        tot = [{"holder_table": "reporting", "share_class": "listed", "issue_count": 17,
                "book_value_yen": 10 ** 12}]
        held, problems, remarks = hh.fallback(xbrl, tot, [("reporting", 17, 2)],
                                              package(ONE_COMPANY))
        self.assertEqual(held, xbrl)
        self.assertEqual(problems, [])
        self.assertIn("in its HTML as in its XBRL", remarks[0])


class Scan(unittest.TestCase):
    def test_scan_sql_lists_the_short_table(self):
        con = duckdb.connect()
        con.execute(extract.SCHEMA_SQL)
        con.execute("INSERT INTO eq_filings (doc_id, edinet_code, filer_name, period_end, status) "
                    "VALUES ('S100W0S7', 'E03614', 'SMFG', '2025-03-31', 'clean'), "
                    "('S100YERK', 'E03614', 'SMFG', '2026-03-31', 'clean')")
        con.execute("INSERT INTO eq_filing_totals VALUES "
                    "('S100W0S7', 'largest', 'listed', 3172877000000, 785), "
                    "('S100YERK', 'largest', 'listed', 3457578000000, 663)")
        rows = [(d, "largest", str(i)) for d, n in (("S100W0S7", 9), ("S100YERK", 59))
                for i in range(n)]
        con.executemany("INSERT INTO eq_holdings (doc_id, holder_table, row_no) VALUES (?,?,?)",
                        rows)
        got = con.execute(hh.scan_sql(True)).fetchall()
        self.assertEqual([(r[0], r[5], r[6], r[7]) for r in got],
                         [("S100W0S7", "largest", 785, 9)])
        self.assertEqual(con.execute(
            "SELECT DISTINCT row_source FROM eq_holdings").fetchall(), [("xbrl",)])


if __name__ == "__main__":
    unittest.main()
