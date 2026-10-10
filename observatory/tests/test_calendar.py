# -*- coding: utf-8 -*-
u"""Earnings calendar (JPX 決算発表予定日): reading the lists and serving them.

Self-contained: workbooks are built in memory and the API runs over a small
database of its own.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_calendar
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
from xml.sax.saxutils import escape

import duckdb
from fastapi import HTTPException

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cal = _load("calendar_extract", ROOT / "equity" / "calendar_extract.py")

from app import calendar_api  # noqa: E402

EPOCH = datetime.date(1899, 12, 30)
HEADER = [u"決算発表予定日\nScheduled Dates for Earnings Announcements", u"コード\nCode",
          u"会社名", u"Issue Name", u"決算期末\nFiscal Year-end", u"業種名", u"Industry",
          u"種別", u"Fiscal Year/Quarter", u"市場区分", u"Market Segment"]


def serial(iso):
    return str((datetime.date.fromisoformat(iso) - EPOCH).days)


def workbook(month, as_of, rows, header=HEADER):
    u"""A JPX-shaped list: title, as-of lines, header on row 5, data, a footnote.

    `rows` are (date or '未定', code, name, fy_end, 種別, market). Numbers are
    written as numeric cells and text as inline strings, as Excel would.
    """
    lines = [[u"%d月に四半期末又は期末を迎えた決算発表予定会社の一覧" % month],
             ["List of companies scheduled to announce earnings"]]
    if as_of:
        y, m, d = as_of.split("-")
        lines += [[u"%s年%d月%d日 現在" % (y, int(m), int(d))],
                  ["As of %s/%d/%d" % (y, int(m), int(d))]]
    else:
        lines += [[], []]
    lines.append(header)
    for date, code, name, fy_end, kind, market in rows:
        lines.append([date if date.startswith(u"未定") else serial(date), code, name,
                      name.upper(), serial(fy_end), u"小売業", "Retail Trade", kind,
                      "", market, ""])
    lines.append([u"※ 決算期変更による変則決算期中の会社を含む場合があります。"])

    def cell(ref, value):
        if value is None or value == "":
            return ""
        if value.isdigit():
            return '<c r="%s"><v>%s</v></c>' % (ref, value)
        return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(value))

    body = "".join(
        '<row r="%d">%s</row>' % (i + 1, "".join(
            cell("%s%d" % ("ABCDEFGHIJK"[j], i + 1), v) for j, v in enumerate(line)))
        for i, line in enumerate(lines))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    # A fixed timestamp on every entry: zip records the current time (to two
    # seconds), so two builds of the same list straddling a two-second tick
    # came out different, and test_the_same_bytes_are_stored_once failed on
    # GitHub's slower machines on and off (2026-10-10).
    def entry(name):
        return zipfile.ZipInfo(name, date_time=(2026, 9, 1, 0, 0, 0))
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(entry("xl/workbook.xml"), '<workbook %s %s><sheets><sheet name="List" '
                   'sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, rel))
        z.writestr(entry("xl/_rels/workbook.xml.rels"),
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/relationships"><Relationship Id="rId1" Target="worksheets/'
                   'sheet1.xml"/></Relationships>')
        z.writestr(entry("xl/worksheets/sheet1.xml"),
                   '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, body))
    return buf.getvalue()


def filler(n, date="2026-10-01"):
    return [(date, "9%03d" % i, u"会社%d" % i, "2027-02-28", u"第２四半期", u"スタンダード")
            for i in range(n)]


TODAY = datetime.date(2026, 9, 24)


class ParseTest(unittest.TestCase):
    def parse(self, raw, name="kessan08_0918", month=8, fetched=TODAY):
        meta, rows = cal.parse(raw, name, month, fetched)
        return meta, rows, cal.check(meta, rows)

    def test_a_monthly_list_reads_as_published(self):
        rows = [("2026-10-08", "3382", u"セブン＆アイ", "2027-02-28", u"第２四半期", u"プライム"),
                (u"未定_Undecided", "8244", u"近鉄百貨店", "2027-02-28", u"第２四半期", u"スタンダード"),
                ("2026-10-14", "3309", u"積水ハウス・リート", "2027-02-28", "-", "REIT")] + filler(20)
        meta, got, stats = self.parse(workbook(8, "2026-09-17", rows))
        self.assertEqual(meta["period_month"], datetime.date(2026, 8, 1))
        self.assertEqual(meta["as_of_date"], datetime.date(2026, 9, 17))
        self.assertEqual((meta["kind"], meta["as_of_basis"]), ("monthly", "stated"))
        self.assertEqual(len(got), 23)                 # the footnote is not a row
        seven = got[0]
        self.assertEqual((seven["announce_date"], seven["period_type"], seven["fy_end"]),
                         (datetime.date(2026, 10, 8), "Q2", datetime.date(2027, 2, 28)))
        self.assertIsNone(got[1]["announce_date"])     # 未定 is missing, not a date
        self.assertIsNone(got[2]["period_type"])       # a REIT period is no quarter
        self.assertEqual(got[2]["period_type_raw"], "-")
        self.assertEqual(stats["undecided"], 1)

    def test_a_december_list_read_in_january_belongs_to_last_year(self):
        meta, _, _ = self.parse(workbook(12, "2027-01-08", filler(20, "2027-01-20")),
                                name="kessan12_0108", month=12,
                                fetched=datetime.date(2027, 1, 9))
        self.assertEqual(meta["period_month"], datetime.date(2026, 12, 1))

    def test_the_next_day_file_takes_the_day_it_was_read(self):
        raw = workbook(8, None, filler(3, "2026-09-25"))
        meta, got, _ = self.parse(raw, name="kessan", month=None)
        self.assertEqual((meta["kind"], meta["as_of_basis"], meta["as_of_date"]),
                         ("next-day", "fetched", TODAY))
        self.assertEqual(len(got), 3)                  # small is fine for a next-day file

    def test_a_renamed_column_stops_the_file(self):
        header = list(HEADER)
        header[7] = u"区分"
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(8, "2026-09-17", filler(20), header=header))

    def test_filename_and_title_must_name_the_same_month(self):
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(7, "2026-09-17", filler(20)))

    def test_a_short_monthly_list_is_refused(self):
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(8, "2026-09-17", filler(5)))

    def test_a_date_far_from_its_period_is_refused(self):
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(8, "2026-09-17", filler(20, "2027-06-01")))

    def test_the_same_company_and_period_twice_is_refused(self):
        rows = filler(20) + [filler(1)[0]]
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(8, "2026-09-17", rows))

    def test_an_as_of_date_in_the_future_is_refused(self):
        with self.assertRaises(cal.SourceError):
            self.parse(workbook(8, "2026-10-30", filler(20)))


def put(con, raw, name, month, fetched):
    meta, rows = cal.parse(raw, name, month, fetched)
    stats = cal.check(meta, rows)
    sha = cal.hashlib.sha256(raw).hexdigest()
    return cal.store(con, meta, sha, rows, stats, "https://example/" + name, name)


class ApiTest(unittest.TestCase):
    u"""Two issues of the August list, a July list and a next-day file.

    Seven & i moves from 7 to 8 October between the August issues; 9999 is on
    the first issue and dropped from the second; 2222 is moved again by the
    next-day file.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        path = os.path.join(cls.tmp, "eq.duckdb")
        con = duckdb.connect(path)
        con.execute(cal.SCHEMA_SQL)
        seven = ("2026-10-07", "3382", u"セブン＆アイ", "2027-02-28", u"第２四半期", u"プライム")
        gone = ("2026-10-02", "9999", u"消える", "2027-02-28", u"第２四半期", u"グロース")
        two = ("2026-10-09", "2222", u"二二", "2027-05-31", u"第１四半期", u"スタンダード")
        undecided = (u"未定_Undecided", "8244", u"近鉄百貨店", "2027-02-28", u"第２四半期",
                     u"スタンダード")
        put(con, workbook(7, "2026-09-03", filler(20, "2026-09-10")), "kessan07_0904", 7,
            datetime.date(2026, 9, 4))
        put(con, workbook(8, "2026-09-03", [seven, gone, two] + filler(20)),
            "kessan08_0904", 8, datetime.date(2026, 9, 4))
        moved = ("2026-10-08",) + seven[1:]
        cls.first = put(con, workbook(8, "2026-09-17", [moved, two, undecided] + filler(20)),
                        "kessan08_0918", 8, datetime.date(2026, 9, 18))
        cls.again = put(con, workbook(8, "2026-09-17", [moved, two, undecided] + filler(20)),
                        "kessan08_0918", 8, datetime.date(2026, 9, 19))
        put(con, workbook(8, None, [("2026-10-06",) + two[1:]]), "kessan", None,
            datetime.date(2026, 9, 20))
        con.close()
        cls.con = duckdb.connect(path, read_only=True)
        cls._cur = calendar_api._cur
        calendar_api._cur = lambda: cls.con.cursor()

    @classmethod
    def tearDownClass(cls):
        calendar_api._cur = cls._cur
        cls.con.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def up(self, **kw):
        args = dict(start="2026-09-01", end="2026-10-31", codes="", market="", as_of="")
        args.update(kw)
        return calendar_api.upcoming(**args)

    def event(self, d, code):
        return next((e for e in d["events"] if e["sec_code"] == code), None)

    def test_the_same_bytes_are_stored_once(self):
        self.assertGreater(self.first[1], 0)
        self.assertEqual(self.again, (self.first[0], 0))

    def test_the_newest_list_wins_and_says_what_it_replaced(self):
        e = self.event(self.up(), "3382")
        self.assertEqual(str(e["announce_date"]), "2026-10-08")
        self.assertEqual(str(e["previous_date"]), "2026-10-07")
        self.assertEqual(str(e["list_as_of"]), "2026-09-17")
        self.assertIsNone(self.event(self.up(), "9999"))   # dropped by the newer issue

    def test_a_newer_next_day_file_moves_a_date(self):
        e = self.event(self.up(), "2222")
        self.assertEqual(str(e["announce_date"]), "2026-10-06")
        self.assertEqual(str(e["previous_date"]), "2026-10-09")

    def test_as_of_gives_the_calendar_as_it_was_known(self):
        d = self.up(as_of="2026-09-10")
        e = self.event(d, "3382")
        self.assertEqual(str(e["announce_date"]), "2026-10-07")
        self.assertIsNone(e["previous_date"])
        self.assertIsNotNone(self.event(d, "9999"))
        self.assertEqual(d["vintage"]["as_of"], "2026-09-10")

    def test_undecided_is_listed_apart_with_no_date(self):
        d = self.up()
        self.assertIsNone(self.event(d, "8244"))
        u = [r for r in d["undecided"] if r["sec_code"] == "8244"]
        self.assertEqual(len(u), 1)
        self.assertIsNone(u[0]["announce_date"])

    def test_codes_and_market_filter(self):
        d = self.up(codes="33820, 2222")               # TDnet's five-character form too
        self.assertEqual(sorted(e["sec_code"] for e in d["events"]), ["2222", "3382"])
        d = self.up(market="prime")
        self.assertEqual([e["sec_code"] for e in d["events"]], ["3382"])
        self.assertEqual(sum(x["companies"] for x in d["days"]), len(d["events"]))

    def test_horizon_names_the_month_not_yet_listed(self):
        h = self.up()["horizon"]
        # How far the lists reach, as published — 2222's 9 October on the
        # August list — whatever a next-day file later moved.
        self.assertEqual(str(h["known_through"]), "2026-10-09")
        self.assertEqual(h["next_list_pending"], "2026-09")

    def test_company_trail_keeps_every_version(self):
        d = calendar_api.company("3382", as_of="")
        dates = [str(t["announce_date"]) for t in d["trail"]]
        self.assertEqual(dates, ["2026-10-08", "2026-10-07"])
        self.assertEqual(len(d["current"]), 1)

    def test_bad_input_is_a_400(self):
        for kw in ({"codes": "abc"}, {"market": "tse"}, {"start": "soon"},
                   {"start": "2026-01-01", "end": "2026-12-31"},
                   {"start": "2026-10-02", "end": "2026-10-01"}):
            with self.assertRaises(HTTPException) as ctx:
                self.up(**kw)
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
