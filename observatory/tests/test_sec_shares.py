# -*- coding: utf-8 -*-
"""Market value from cover-page share counts (equity/sec_cover_shares.py,
app/sec_shares.py) and ticker changes in the price layer (app/us_prices_api.py).

Everything is built in memory: a small XBRL instance for the cover parser, a
throwaway DuckDB for the valuation. No network, no data/ files.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_sec_shares
"""
import datetime
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "equity"))
_spec = importlib.util.spec_from_file_location("sec_cover_shares", str(ROOT / "equity" / "sec_cover_shares.py"))
cover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cover)

from app import sec_shares, us_prices_api  # noqa: E402

D = datetime.date


def instance(counts, symbols, rates=()):
    """A minimal XBRL instance: counts [(member or None, n)], symbols
    [(member or None, title, ticker)], rates [(member, rate)]."""
    ctx, facts = [], []

    def context(cid, member, period):
        seg = ("<segment><xbrldi:explicitMember dimension=\"us-gaap:StatementClassOfStockAxis\">%s"
               "</xbrldi:explicitMember></segment>" % member) if member else ""
        ctx.append("<context id=\"%s\"><entity><identifier scheme=\"http://www.sec.gov/CIK\">1</identifier>%s"
                   "</entity><period>%s</period></context>" % (cid, seg, period))

    dur = "<startDate>2026-01-01</startDate><endDate>2026-06-30</endDate>"
    for i, (member, n) in enumerate(counts):
        context("n%d" % i, member, "<instant>2026-07-20</instant>")
        facts.append("<dei:EntityCommonStockSharesOutstanding contextRef=\"n%d\" unitRef=\"shares\" "
                     "decimals=\"0\">%d</dei:EntityCommonStockSharesOutstanding>" % (i, n))
    for i, (member, title, tick) in enumerate(symbols):
        context("s%d" % i, member, dur)
        facts.append("<dei:Security12bTitle contextRef=\"s%d\">%s</dei:Security12bTitle>" % (i, title))
        facts.append("<dei:TradingSymbol contextRef=\"s%d\">%s</dei:TradingSymbol>" % (i, tick))
    for i, (member, rate) in enumerate(rates):
        context("r%d" % i, member, "<instant>2026-06-30</instant>")
        facts.append("<v:CommonStockConversionRate contextRef=\"r%d\" unitRef=\"pure\" decimals=\"4\">%s"
                     "</v:CommonStockConversionRate>" % (i, rate))
    return ("<?xml version=\"1.0\"?><xbrl xmlns=\"http://www.xbrl.org/2003/instance\" "
            "xmlns:xbrldi=\"http://xbrl.org/2006/xbrldi\" xmlns:dei=\"http://xbrl.sec.gov/dei/2024\" "
            "xmlns:us-gaap=\"http://fasb.org/us-gaap/2024\" xmlns:v=\"http://visa.com/20260630\">"
            + "".join(ctx) + "".join(facts) + "</xbrl>").encode("utf-8")


A, B, C = "us-gaap:CommonClassAMember", "us-gaap:CommonClassBMember", "us-gaap:CommonClassCMember"


class CoverParse(unittest.TestCase):
    def test_tagged_classes_carry_their_own_tickers(self):
        rows = cover.parse_cover(instance([(A, 5868), (B, 835), (C, 5527)],
                                          [(A, "Class A Common Stock", "GOOGL"), (C, "Class C Capital Stock", "GOOG")]))
        self.assertEqual({k: t for _, k, _, t, _ in rows}, {A: "GOOGL", B: None, C: "GOOG"})

    def test_untagged_ticker_goes_to_the_class_its_title_names(self):
        rows = cover.parse_cover(instance([(A, 281), (B, 1203)], [(None, "Class B Common Stock", "NKE")]))
        self.assertEqual({k: t for _, k, _, t, _ in rows}, {A: None, B: "NKE"})

    def test_untagged_ticker_beside_a_token_class(self):
        # Interactive Brokers: "Common Stock" names no class; Class B is 400 shares
        rows = cover.parse_cover(instance([(A, 453000000), (B, 400)], [(None, "Common Stock", "IBKR")]))
        self.assertEqual({k: t for _, k, _, t, _ in rows}, {A: "IBKR", B: None})

    def test_one_count_one_ticker(self):
        rows = cover.parse_cover(instance([(None, 14594180000)], [(None, "Common Stock", "AAPL")]))
        self.assertEqual(rows, [("2026-07-20", "", "", "AAPL", 14594180000.0)])

    def test_ifrs_classes_and_depositary_members(self):
        # Sony: ordinary shares on the IFRS class axis, and an ADS count that is a part of them, not a class
        body = instance([(None, 1)], []).replace(b"</xbrl>", b"")  # a blank instance to extend by hand
        body = (b'<?xml version="1.0"?><xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
                b'xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full">'
                b'<context id="o"><entity><identifier scheme="http://www.sec.gov/CIK">1</identifier><segment>'
                b'<xbrldi:explicitMember dimension="ifrs-full:ClassesOfShareCapitalAxis">ifrs-full:OrdinarySharesMember</xbrldi:explicitMember>'
                b'</segment></entity><period><instant>2026-03-31</instant></period></context>'
                b'<context id="a"><entity><identifier scheme="http://www.sec.gov/CIK">1</identifier><segment>'
                b'<xbrldi:explicitMember dimension="ifrs-full:ClassesOfShareCapitalAxis">dei:AdrMember</xbrldi:explicitMember>'
                b'</segment></entity><period><instant>2026-03-31</instant></period></context>'
                b'<dei:EntityCommonStockSharesOutstanding contextRef="o" unitRef="shares" decimals="0">5907667254</dei:EntityCommonStockSharesOutstanding>'
                b'<dei:EntityCommonStockSharesOutstanding contextRef="a" unitRef="shares" decimals="0">523381332</dei:EntityCommonStockSharesOutstanding>'
                b'</xbrl>')
        rows = cover.parse_cover(body)
        self.assertEqual([(k, n) for _, k, _, _, n in rows], [("ifrs-full:OrdinarySharesMember", 5907667254.0)])

    def test_preferred_is_not_a_common_count(self):
        rows = cover.parse_cover(instance([(A, 224), ("ares:SeriesBMandatoryConvertiblePreferredStockMember", 30)], []))
        self.assertEqual([k for _, k, _, _, _ in rows], [A])

    def test_tagged_conversion_rates(self):
        body = instance([(A, 1704), ("v:CommonClassB1Member", 2)], [(A, "Class A", "V")],
                        rates=[(A, 0), ("v:CommonClassB1Member", 1.5445)])
        self.assertEqual([(k, r) for _, k, r, _ in cover.parse_rates(body)], [("v:CommonClassB1Member", 1.5445)])

    def test_recent_frames(self):
        self.assertEqual(cover.recent_frames(D(2026, 1, 5), 3), ["CY2026Q1I", "CY2025Q4I", "CY2025Q3I"])


SCHEMA = """
CREATE TABLE sec_cover_shares (cik BIGINT, accn VARCHAR, end_date DATE, class_key VARCHAR, dims VARCHAR,
    ticker VARCHAR, shares DOUBLE, source VARCHAR, first_seen TIMESTAMP);
CREATE TABLE sec_cover_filings (accn VARCHAR, cik BIGINT, form VARCHAR, filed DATE, url VARCHAR,
    sha256 VARCHAR, raw_path VARCHAR, status VARCHAR, n_classes INTEGER, parser_version VARCHAR, fetched_at TIMESTAMP);
CREATE TABLE sec_filings (adsh VARCHAR, filed DATE, form VARCHAR);
CREATE TABLE sec_cover_rates (cik BIGINT, accn VARCHAR, as_of DATE, class_key VARCHAR, rate DOUBLE, tag VARCHAR,
    first_seen TIMESTAMP);
CREATE TABLE us_px_daily (symbol VARCHAR, date DATE, close DOUBLE);
CREATE TABLE sec_ads_filings (accn VARCHAR, cik BIGINT, form VARCHAR, filed DATE, instance_url VARCHAR, doc_url VARCHAR,
    doc_sha256 VARCHAR, doc_raw_path VARCHAR, status VARCHAR, parser_version VARCHAR, fetched_at TIMESTAMP);
CREATE TABLE sec_ads_ratios (cik BIGINT, accn VARCHAR, ticker VARCHAR, listing VARCHAR, ratio DOUBLE, title VARCHAR,
    quote VARCHAR, source VARCHAR, first_seen TIMESTAMP);
"""


class Valuation(unittest.TestCase):
    def setUp(self):
        self.cur = duckdb.connect(":memory:")
        self.cur.execute(SCHEMA)
        self.version = 0
        sec_shares._MEMO.clear()
        sec_shares._ADS_MEMO.clear()
        sec_shares._SPLIT_MEMO.clear()
        us_prices_api._EVENTS.clear()

    def listing(self, cik, accn, kind, ratio, form="20-F"):
        self.version += 1
        self.cur.execute("INSERT INTO sec_ads_filings VALUES (?,?,?,?,NULL,NULL,NULL,NULL,'ok','t',now())",
                         [accn, cik, form, D(2026, 4, 30)])
        self.cur.execute("INSERT INTO sec_ads_ratios VALUES (?,?,?,?,?,?,?,?,now())",
                         [cik, accn, "T", kind, ratio, "title", "quote", "cover title"])

    def counts(self, cik, accn, end, rows, source="instance"):
        self.version += 1
        self.cur.execute("INSERT INTO sec_cover_filings VALUES (?,?,?,?,NULL,NULL,NULL,'ok',0,'t',now())",
                         [accn, cik, "10-Q", end])
        for k, t, n in rows:
            self.cur.execute("INSERT INTO sec_cover_shares VALUES (?,?,?,?,?,?,?,?,now())",
                             [cik, accn, end, k, k, t, n, source])

    def value(self, row, closes):
        with mock.patch("app.sec_api.file_version", return_value=("t", self.version)):
            return sec_shares.basis(self.cur, row, {sec_shares._norm(k): (D(2026, 10, 16), v, None)
                                                    for k, v in closes.items()})

    def test_one_count(self):
        self.counts(320193, "a1", D(2026, 7, 17), [("", None, 100.0)])
        b = self.value({"cik": 320193, "ticker": "AAPL", "form": "10-K"}, {"AAPL": 2.0})
        self.assertEqual((b["method"], b["market_cap"]), ("one count", 200.0))

    def test_listed_classes_at_their_own_prices_and_unlisted_through_a_ratio(self):
        self.counts(922621, "e1", D(2026, 7, 24), [(A, "ERIE", 46.0), (B, None, 2.0)])
        b = self.value({"cik": 922621, "ticker": "ERIE", "form": "10-K"}, {"ERIE": 10.0})
        self.assertEqual(b["market_cap"], 46.0 * 10 + 2.0 * 2400 * 10)     # Class B converts 2,400 to 1

    def test_unlisted_class_with_no_known_ratio_leaves_it_blank(self):
        self.counts(999, "x1", D(2026, 7, 1), [(A, "XX", 100.0), (B, None, 50.0)])
        b = self.value({"cik": 999, "ticker": "XX", "form": "10-K"}, {"XX": 1.0})
        self.assertIsNone(b["market_cap"])
        self.assertIn("CommonClassB", b["note"])

    def test_token_class_is_left_out_with_a_note(self):
        self.counts(998, "x2", D(2026, 7, 1), [(A, "XX", 100000000.0), (B, None, 1.0)])
        b = self.value({"cik": 998, "ticker": "XX", "form": "10-K"}, {"XX": 1.0})
        self.assertEqual(b["market_cap"], 100000000.0)
        self.assertIn("negligible", b["note"])

    def test_filer_tagged_rate_is_used(self):
        self.counts(1403161, "v1", D(2026, 7, 21), [(A, "V", 10.0), ("v:CommonClassB1Member", None, 2.0)])
        self.cur.execute("INSERT INTO sec_cover_rates VALUES (1403161,'v1','2026-06-30','v:CommonClassB1Member',1.5,'v:x',now())")
        b = self.value({"cik": 1403161, "ticker": "V", "form": "10-K"}, {"V": 4.0})
        self.assertEqual(b["market_cap"], 10 * 4.0 + 2 * 1.5 * 4.0)

    def test_dotted_ticker_written_without_the_dot(self):
        self.counts(14693, "b1", D(2026, 8, 31), [(A, "BFA", 1.0), ("us-gaap:NonvotingCommonStockMember", "BFB", 2.0)])
        b = self.value({"cik": 14693, "ticker": "BF-B", "form": "10-K"}, {"BF.A": 30.0, "BF.B": 29.0})
        self.assertEqual(b["market_cap"], 30.0 + 58.0)

    def test_unexplained_jump_is_held_back_and_a_split_explains_one(self):
        self.counts(5, "j0", D(2026, 2, 13), [("", None, 77.0)], source="frames")
        self.counts(5, "j1", D(2026, 6, 22), [("", None, 5413.0)], source="frames")
        b = self.value({"cik": 5, "ticker": "MODD", "form": "10-K"}, {"MODD": 1.0})
        self.assertEqual(b["method"], "held back")
        self.assertIsNone(b["market_cap"])
        # a 1-for-25 reverse split in the price history explains a 25x fall
        self.counts(6, "k0", D(2026, 1, 30), [("", None, 400.0)], source="frames")
        self.counts(6, "k1", D(2026, 4, 30), [("", None, 16.0)], source="frames")
        with mock.patch("app.us_prices_api.split_events", return_value={"AGL": [(D(2026, 3, 31), 0.04)]}):
            b = self.value({"cik": 6, "ticker": "AGL", "form": "10-K"}, {"AGL": 1.0})
        self.assertEqual((b["method"], b["market_cap"]), ("one count", 16.0))

    def test_depositary_share_divides_the_count(self):
        # HDFC Bank: 15.4bn equity shares, one ADS = three of them
        self.counts(7, "f1", D(2026, 3, 31), [("", None, 15393368328.0)])
        self.listing(7, "f1", "ads", 3.0)
        b = self.value({"cik": 7, "ticker": "HDB", "form": "20-F"}, {"HDB": 23.84})
        self.assertEqual(b["per_listed_share"], 3.0)
        self.assertAlmostEqual(b["listed_shares"], 15393368328.0 / 3, places=2)
        self.assertAlmostEqual(b["market_cap"], 15393368328.0 / 3 * 23.84, places=0)
        self.assertEqual(b["shares"], 15393368328.0)
        self.assertIn("depositary share", b["note"])

    def test_depositary_share_without_a_cover_count_uses_the_balance_sheet_count(self):
        self.listing(9, "g1", "ads", 8.0)
        b = self.value({"cik": 9, "ticker": "BABA", "form": "20-F", "per_share": {"shares_outstanding": 800.0},
                        "period_end": "2026-03-31"}, {"BABA": 10.0})
        self.assertEqual((b["method"], b["market_cap"]), ("balance sheet", 1000.0))

    def test_unread_or_unstated_listing_is_held_back(self):
        self.counts(10, "h1", D(2026, 3, 31), [("", None, 100.0)])
        b = self.value({"cik": 10, "ticker": "ADR", "form": "20-F"}, {"ADR": 1.0})
        self.assertEqual((b["method"], b["market_cap"]), ("held back", None))
        self.assertIn("not been read", b["note"])
        self.assertEqual(b["expect"], "The next data refresh reads it.")
        self.listing(10, "h1", "ads", None)
        b = self.value({"cik": 10, "ticker": "ADR", "form": "20-F"}, {"ADR": 1.0})
        self.assertEqual((b["method"], b["market_cap"]), ("held back", None))
        self.assertIn("does not say how many company shares", b["note"])
        self.assertIn("later annual report", b["expect"])

    def test_direct_foreign_listing_and_a_10k_depositary_share(self):
        self.counts(11, "i1", D(2026, 3, 31), [("", None, 100.0)])
        self.listing(11, "i1", "direct", 1.0)
        self.assertEqual(self.value({"cik": 11, "ticker": "SPOT", "form": "20-F"}, {"SPOT": 2.0})["market_cap"], 200.0)
        # BeOne files a 10-K; its ADS stands for 13 shares
        self.counts(12, "j1", D(2026, 7, 31), [("", None, 1300.0)])
        self.listing(12, "j1", "ads", 13.0, form="10-K")
        self.assertEqual(self.value({"cik": 12, "ticker": "ONC", "form": "10-K"}, {"ONC": 2.0})["market_cap"], 200.0)

    def test_cover_count_a_power_of_ten_off_falls_back_to_the_balance_sheet(self):
        # Alibaba's cover tagged 1.86bn shares against 18.58bn on its balance sheet; no split explains it
        self.counts(14, "m1", D(2026, 3, 31), [("", None, 1858037427.0)], source="frames")
        self.listing(14, "m1", "ads", 8.0)
        row = {"cik": 14, "ticker": "BABA", "form": "20-F", "per_share": {"shares_outstanding": 18580374278.0},
               "period_end": "2026-03-31"}
        b = self.value(row, {"BABA": 8.0})
        self.assertEqual(b["method"], "balance sheet")
        self.assertAlmostEqual(b["market_cap"], 18580374278.0 / 8 * 8.0, places=0)
        self.assertIn("a tenth of", b["note"])
        # a tenfold fall explained by a reverse split keeps the cover count
        with mock.patch("app.us_prices_api.split_events", return_value={"BABA": [(D(2026, 5, 1), 0.1)]}):
            self.counts(14, "m2", D(2026, 6, 30), [("", None, 1858037427.0)], source="frames")
            b = self.value(row, {"BABA": 8.0})
        self.assertEqual(b["method"], "one count")

    def test_foreign_whole_count_beats_the_sum_of_its_classes(self):
        # Youdao: 120mn in all, then 32mn Class A and 88mn Class B tagged beside it
        self.counts(15, "n1", D(2025, 12, 31), [("", None, 120.0), (A, "DAO", 32.0), (B, None, 88.0)])
        self.listing(15, "n1", "ads", 1.0)
        b = self.value({"cik": 15, "ticker": "DAO", "form": "20-F"}, {"DAO": 2.0})
        self.assertEqual((b["method"], b["market_cap"]), ("one count", 240.0))
        # the same total tagged as the plain common-stock member rather than the whole company
        self.counts(16, "n2", D(2025, 12, 31), [("us-gaap:CommonStockMember", None, 120.0), (A, "DAO", 32.0), (B, None, 88.0)])
        self.listing(16, "n2", "ads", 1.0)
        b = self.value({"cik": 16, "ticker": "DAO", "form": "20-F"}, {"DAO": 2.0})
        self.assertEqual((b["method"], b["market_cap"]), ("one count", 240.0))

    def test_uncorroborated_count_is_held_back(self):
        rows = [{"cik": 21, "ticker": "BIG", "metrics": {"market_cap": 4.17e11, "dividend_yield_pct": 1.0},
                 "per_share": {"shares_outstanding": None},
                 "market_value_basis": {"method": "all classes, one price", "market_cap": 4.17e11, "note": None}},
                {"cik": 22, "ticker": "OK", "metrics": {"market_cap": 1e10}, "per_share": {"shares_outstanding": None},
                 "market_value_basis": {"method": "one count", "market_cap": 1e10, "note": None}},
                {"cik": 23, "ticker": "NONE", "metrics": {"market_cap": 1e10}, "per_share": {"shares_outstanding": None},
                 "market_value_basis": {"method": "one count", "market_cap": 1e10, "note": None}}]
        with mock.patch("app.sec_shares.institutional_value", return_value={21: 1e6, 22: 2e9}):
            n = sec_shares.hold_back_uncorroborated(self.cur, rows)
        self.assertEqual(n, 1)
        self.assertIsNone(rows[0]["metrics"]["market_cap"])
        self.assertEqual(rows[0]["market_value_basis"]["method"], "held back")
        self.assertIn("under 0.1%", rows[0]["market_value_basis"]["note"])
        self.assertEqual(rows[1]["metrics"]["market_cap"], 1e10)     # holders corroborate it
        self.assertEqual(rows[2]["metrics"]["market_cap"], 1e10)     # no holder names it at all: left alone

    def test_units_listing_says_why(self):
        # Coca-Cola FEMSA: "each representing 10 units", a unit being several share classes
        self.counts(17, "u1", D(2025, 12, 31), [("", None, 100.0)])
        self.version += 1
        self.cur.execute("INSERT INTO sec_ads_filings VALUES ('u1',17,'20-F','2026-04-30',NULL,NULL,NULL,NULL,'ok','t',now())")
        self.cur.execute("INSERT INTO sec_ads_ratios VALUES (17,'u1','KOF','unknown',NULL,"
                         "'American Depositary Shares, each representing 10 units',NULL,'cover title',now())")
        b = self.value({"cik": 17, "ticker": "KOF", "form": "20-F"}, {"KOF": 1.0})
        self.assertEqual((b["method"], b["market_cap"]), ("held back", None))
        self.assertIn("10 units", b["note"])
        self.assertIn("make-up of a unit", b["expect"])

    def test_an_old_count_is_shown_with_a_caution(self):
        # Elevra: the count predates a consolidation and is over a year old
        self.counts(18, "o1", D(2025, 6, 30), [("", None, 1000.0)])
        self.listing(18, "o1", "ads", 10.0)
        b = self.value({"cik": 18, "ticker": "ELVR", "form": "20-F", "period_end": "2025-06-30"}, {"ELVR": 2.0})
        self.assertEqual(b["market_cap"], 200.0)
        self.assertIn("30 June 2025", b["caution"])
        self.assertIn("next annual report", b["expect"])

    def test_stale_count_due_dates(self):
        t = D(2026, 10, 19)
        self.assertEqual(sec_shares.stale_count({"period_end": "2025-12-31", "form": "10-K"}, "2026-07-01", t), (None, None))
        c, e = sec_shares.stale_count({"period_end": "2025-06-30", "form": "20-F"}, "2025-06-30", t)
        self.assertIn("due by 31 October 2026", e)
        c, e = sec_shares.stale_count({"period_end": "2024-06-30", "form": "20-F"}, "2024-06-30", t)
        self.assertIn("has not reached us yet", e)

    def test_split_evidence_from_the_share_count(self):
        # a 4-for-1 split: the count quadruples across the day
        self.counts(19, "s0", D(2026, 3, 31), [("", None, 100.0)])
        self.counts(19, "s1", D(2026, 7, 31), [("", None, 400.0)])
        sec_shares._SPLIT_MEMO.clear()
        self.assertEqual(sec_shares.split_evidence(self.cur, 19, D(2026, 5, 1), 4.0), "confirmed")
        # Corteva: a sixfold fall in the price, the count unchanged after it
        self.counts(20, "c0", D(2026, 7, 24), [("", None, 667.0)])
        sec_shares._SPLIT_MEMO.clear()
        self.assertEqual(sec_shares.split_evidence(self.cur, 20, D(2026, 10, 1), 6.0), "unconfirmed")
        self.counts(20, "c1", D(2026, 10, 25), [("", None, 668.0)])
        sec_shares._SPLIT_MEMO.clear()                 # a new count on file: the cache is per database version
        self.assertEqual(sec_shares.split_evidence(self.cur, 20, D(2026, 10, 1), 6.0), "contradicted")
        note, status = sec_shares.split_sentence(self.cur, 20, D(2026, 10, 1), 6.0)
        self.assertIn("was not a split", note)
        note, status = sec_shares.split_sentence(self.cur, 19, D(2026, 5, 1), 4.0)
        self.assertIn("confirms", note)

    def test_foreign_share_classes_all_at_the_one_price(self):
        # JD.com: Class A and Class B counted alike, the ADS being two Class A shares
        self.counts(13, "k1", D(2026, 3, 31), [(A, None, 2400.0), (B, None, 300.0)])
        self.listing(13, "k1", "ads", 2.0)
        b = self.value({"cik": 13, "ticker": "JD", "form": "20-F"}, {"JD": 10.0})
        self.assertEqual((b["method"], b["market_cap"]), ("all classes, one price", 2700.0 / 2 * 10))
        self.assertIn("equal economic rights assumed", b["note"])

    def test_as_of_ceiling_ignores_later_filings(self):
        from app import asof
        self.counts(8, "p0", D(2026, 1, 20), [("", None, 100.0)])
        self.counts(8, "p1", D(2026, 7, 20), [("", None, 110.0)])
        with asof.scope("2026-03-01"):
            b = self.value({"cik": 8, "ticker": "P", "form": "10-K"}, {"P": 1.0})
        self.assertEqual(b["shares"], 100.0)


class Successors(unittest.TestCase):
    def test_replaced_filer_is_rekeyed_until_the_successor_reports(self):
        rows = [{"cik": 34088, "sec_code": "cik:34088", "name": "EXXON MOBIL CORP"}]
        out = sec_shares.merge_successors(rows)
        self.assertEqual(out[0]["cik"], 2115436)
        self.assertEqual(out[0]["succession"]["from_cik"], 34088)
        both = sec_shares.merge_successors(rows + [{"cik": 2115436, "sec_code": "cik:2115436", "name": "XOM HOLDCO"}])
        self.assertEqual([r["cik"] for r in both], [2115436])


class TickerChange(unittest.TestCase):
    def test_new_symbol_is_joined_to_the_old_one_of_the_same_company(self):
        cur = duckdb.connect(":memory:")
        cur.execute("CREATE TABLE us_px_daily (symbol VARCHAR, date DATE, close DOUBLE)")
        cur.execute("CREATE TABLE sec_companies (cik BIGINT, ticker VARCHAR, tickers VARCHAR)")
        cur.execute("INSERT INTO sec_companies VALUES (2041610, 'SKYD', 'SKYD,PSKY'), (9, 'ZZZ', 'ZZZ')")
        days = [D(2026, 10, 1), D(2026, 10, 2), D(2026, 10, 5), D(2026, 10, 6), D(2026, 10, 7)]
        for d, c in zip(days[:3], (9.3, 9.5, 9.8)):
            cur.execute("INSERT INTO us_px_daily VALUES ('PSKY', ?, ?), ('ZZZ', ?, 5)", [d, c, d])
        for d, c in zip(days[3:], (9.5, 8.9)):
            cur.execute("INSERT INTO us_px_daily VALUES ('SKYD', ?, ?), ('ZZZ', ?, 5)", [d, c, d])
        us_prices_api._EVENTS.clear()
        with mock.patch("app.sec_api.file_version", return_value=("tc", 1)):
            ev = us_prices_api._events(cur)
        self.assertEqual(ev["links"].get("SKYD"), ("PSKY", D(2026, 10, 6)))
        self.assertIn("SKYD", ev["renames"])
        us_prices_api._EVENTS.clear()


if __name__ == "__main__":
    unittest.main()
