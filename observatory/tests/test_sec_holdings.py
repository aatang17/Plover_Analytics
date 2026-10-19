# -*- coding: utf-8 -*-
"""US insider trades (equity/sec_insiders_extract.py, app/sec_insiders_api.py)
and 13F holdings (equity/sec_13f_extract.py, app/sec_13f_api.py).

The loader tests build tiny SEC-shaped zips on a throwaway shelf, so they
need no archive. The API tests use data/sec.duckdb when it holds the tables
and skip cleanly when it does not.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_sec_holdings
"""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import zipfile

import duckdb
from fastapi.testclient import TestClient

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
EQ = str(ROOT / "equity")
if EQ not in sys.path:
    sys.path.insert(0, EQ)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "equity" / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sec_shelf = _load("sec_shelf")
ins = _load("sec_insiders_extract")
f13 = _load("sec_13f_extract")


def _tsv(header, rows):
    out = ["\t".join(header)]
    for r in rows:
        out.append("\t".join("" if v is None else str(v) for v in r))
    return "\n".join(out) + "\n"


class Shelf(object):
    def __init__(self, root):
        self.root = pathlib.Path(root)
        (self.root / "latest").mkdir(parents=True)

    def put(self, source, filename, members):
        day = "2026-10-18"
        p = self.root / day / source / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(p), "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in members.items():
                z.writestr(name, text)
        rec = {"date": day, "source": source, "file": filename, "key": "us/%s/%s/%s" % (day, source, filename),
               "sha256": "sha-" + filename, "content_hash": "zip:x", "status": "ok",
               "captured_at": "2026-10-18T10:00:00+00:00"}
        with open(str(self.root / "latest" / ("%s__%s.json" % (source, filename))), "w") as f:
            json.dump(rec, f)


def form345_members(n_sub=120):
    acc = ["0000000001-26-%06d" % i for i in range(n_sub)]
    sub = [["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
            "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED", "FORM4_TRANS_REPORTED", "DOCUMENT_TYPE", "ISSUERCIK",
            "ISSUERNAME", "ISSUERTRADINGSYMBOL", "REMARKS", "AFF10B5ONE"]]
    rows = []
    for i, a in enumerate(acc):
        rows.append([a, "15-SEP-2026", "12-SEP-2026", "", "0", "", "", "1", "4/A" if i == 1 else "4",
                     "0000320193", "Apple Inc.", "AAPL", "", "1" if i == 0 else "0"])
    own = [["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
            "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY", "RPTOWNER_STATE", "RPTOWNER_ZIPCODE",
            "RPTOWNER_STATE_DESC", "FILE_NUMBER"]]
    own_rows = [[a, "0001214128", "COOK TIMOTHY D", "Officer,Director", "CEO", "", "", "", "", "CA", "", "", ""]
                for a in acc]
    nd = [["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
           "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN", "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
           "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS", "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
           "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
           "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
           "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]]
    nd_rows = []
    for i, a in enumerate(acc):
        code = "S" if i % 3 else "P"
        price = "" if i == 2 else "200.5"        # one trade with no price
        nd_rows.append([a, 1000 + i, "Common Stock", "", "12-SEP-2026", "", "", "", "4", code, "0", "", "", "",
                        "100", "", price, "", "D" if code == "S" else "A", "", "5000", "", "", "", "D", "", "", ""])
    empty_deriv_trans = [["ACCESSION_NUMBER", "DERIV_TRANS_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN", "CONV_EXERCISE_PRICE",
                          "CONV_EXERCISE_PRICE_FN", "TRANS_DATE", "TRANS_DATE_FN", "DEEMED_EXECUTION_DATE",
                          "DEEMED_EXECUTION_DATE_FN", "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                          "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS", "TRANS_TIMELINESS_FN", "TRANS_SHARES",
                          "TRANS_SHARES_FN", "TRANS_TOTAL_VALUE", "TRANS_TOTAL_VALUE_FN", "TRANS_PRICEPERSHARE",
                          "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN", "EXCERCISE_DATE",
                          "EXCERCISE_DATE_FN", "EXPIRATION_DATE", "EXPIRATION_DATE_FN", "UNDLYNG_SEC_TITLE",
                          "UNDLYNG_SEC_TITLE_FN", "UNDLYNG_SEC_SHARES", "UNDLYNG_SEC_SHARES_FN", "UNDLYNG_SEC_VALUE",
                          "UNDLYNG_SEC_VALUE_FN", "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                          "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN", "DIRECT_INDIRECT_OWNERSHIP",
                          "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]]
    ndh = [["ACCESSION_NUMBER", "NONDERIV_HOLDING_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN", "TRANS_FORM_TYPE",
            "TRANS_FORM_TYPE_FN", "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS",
            "VALU_OWND_FOLWNG_TRANS_FN", "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
            "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]]
    dh = [["ACCESSION_NUMBER", "DERIV_HOLDING_SK", "SECURITY_TITLE", "SECURITY_TITLE_FN", "CONV_EXERCISE_PRICE",
           "CONV_EXERCISE_PRICE_FN", "TRANS_FORM_TYPE", "TRANS_FORM_TYPE_FN", "EXERCISE_DATE", "EXERCISE_DATE_FN",
           "EXPIRATION_DATE", "EXPIRATION_DATE_FN", "UNDLYNG_SEC_TITLE", "UNDLYNG_SEC_TITLE_FN", "UNDLYNG_SEC_SHARES",
           "UNDLYNG_SEC_SHARES_FN", "UNDLYNG_SEC_VALUE", "UNDLYNG_SEC_VALUE_FN", "SHRS_OWND_FOLWNG_TRANS",
           "SHRS_OWND_FOLWNG_TRANS_FN", "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
           "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]]
    fn = [["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"], [acc[0], "F1", "Sold under a 10b5-1 plan."]]
    return {"SUBMISSION.tsv": _tsv(sub[0], rows), "REPORTINGOWNER.tsv": _tsv(own[0], own_rows),
            "NONDERIV_TRANS.tsv": _tsv(nd[0], nd_rows), "DERIV_TRANS.tsv": _tsv(empty_deriv_trans[0], []),
            "NONDERIV_HOLDING.tsv": _tsv(ndh[0], []), "DERIV_HOLDING.tsv": _tsv(dh[0], []),
            "FOOTNOTES.tsv": _tsv(fn[0], fn[1:]), "FORM_345_readme.htm": "<html/>"}


def form13f_members(n=120, value="1000"):
    acc = ["0000000002-26-%06d" % i for i in range(n)]
    sub = ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"]
    sub_rows = [[a, "14-AUG-2026", "13F-HR", "%010d" % (1067983 + i), "30-JUN-2026"] for i, a in enumerate(acc)]
    cov = ["ACCESSION_NUMBER", "REPORTCALENDARORQUARTER", "ISAMENDMENT", "AMENDMENTNO", "AMENDMENTTYPE",
           "CONFDENIEDEXPIRED", "DATEDENIEDEXPIRED", "DATEREPORTED", "REASONFORNONCONFIDENTIALITY",
           "FILINGMANAGER_NAME", "FILINGMANAGER_STREET1", "FILINGMANAGER_STREET2", "FILINGMANAGER_CITY",
           "FILINGMANAGER_STATEORCOUNTRY", "FILINGMANAGER_ZIPCODE", "REPORTTYPE", "FORM13FFILENUMBER",
           "CRDNUMBER", "SECFILENUMBER", "PROVIDEINFOFORINSTRUCTION5", "ADDITIONALINFORMATION"]
    cov_rows = [[a, "30-JUN-2026", "N", "", "", "", "", "", "", "Manager %d" % i if i else "BERKSHIRE HATHAWAY INC",
                 "", "", "OMAHA", "NE", "", "13F HOLDINGS REPORT", "028-%05d" % i, "", "", "N", ""]
                for i, a in enumerate(acc)]
    info = ["ACCESSION_NUMBER", "INFOTABLE_SK", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "FIGI", "VALUE", "SSHPRNAMT",
            "SSHPRNAMTTYPE", "PUTCALL", "INVESTMENTDISCRETION", "OTHERMANAGER", "VOTING_AUTH_SOLE",
            "VOTING_AUTH_SHARED", "VOTING_AUTH_NONE"]
    info_rows = []
    for i, a in enumerate(acc):
        info_rows.append([a, 50000 + i, "APPLE INC" if i % 2 else "Apple Inc", "COM", "037833100", "", value, "10",
                          "SH", "", "SOLE", "", "10", "0", "0"])
    summ = ["ACCESSION_NUMBER", "OTHERINCLUDEDMANAGERSCOUNT", "TABLEENTRYTOTAL", "TABLEVALUETOTAL", "ISCONFIDENTIALOMITTED"]
    summ_rows = [[a, "0", "1", value, "N"] for a in acc]
    return {"SUBMISSION.tsv": _tsv(sub, sub_rows), "COVERPAGE.tsv": _tsv(cov, cov_rows),
            "INFOTABLE.tsv": _tsv(info, info_rows), "SUMMARYPAGE.tsv": _tsv(summ, summ_rows),
            "FORM13F_readme.htm": "<html/>"}


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sec-hold-")
        self.shelf = Shelf(os.path.join(self.tmp, "us"))
        self.db = os.path.join(self.tmp, "sec.duckdb")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, mod, *extra):
        argv = sys.argv
        sys.argv = [mod.__name__, "--source", "local", "--db", self.db,
                    "--root", os.path.join(self.tmp, "us")] + list(extra)
        try:
            return mod.main()
        finally:
            sys.argv = argv

    def test_insiders_load_codes_prices_and_amendment_flag(self):
        self.shelf.put("sec-insiders", "2026q3_form345.zip", form345_members())
        self.assertEqual(self._run(ins), 0)
        con = duckdb.connect(self.db, read_only=True)
        q = con.execute("SELECT quarter, filings, transactions, status FROM sec_ins_quarters").fetchall()
        self.assertEqual(q, [("2026q3", 120, 120, "ok")])
        self.assertEqual(con.execute("SELECT count(*) FROM sec_ins_filings WHERE amendment").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT count(*) FROM sec_ins_filings WHERE plan_10b5_1").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT count(*) FROM sec_ins_transactions WHERE price IS NULL").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT trans_date FROM sec_ins_transactions LIMIT 1").fetchone()[0].isoformat(),
                         "2026-09-12")
        self.assertEqual(con.execute("SELECT is_officer, is_director, is_ten_percent FROM sec_ins_owners LIMIT 1").fetchone(),
                         (True, True, False))
        codes = dict(con.execute("SELECT trans_code, count(*) FROM sec_ins_transactions GROUP BY 1").fetchall())
        self.assertEqual(codes["P"], 40)
        con.close()
        # a second run with the same shelf loads nothing new
        self.assertEqual(self._run(ins), 0)

    def test_insiders_refuses_a_broken_set_and_keeps_the_old(self):
        self.shelf.put("sec-insiders", "2026q3_form345.zip", form345_members())
        self.assertEqual(self._run(ins), 0)
        bad = form345_members()
        bad["NONDERIV_TRANS.tsv"] = bad["NONDERIV_TRANS.tsv"].replace("0000000001-26-000005", "9999999999-26-000005", 1)
        self.shelf.put("sec-insiders", "2026q2_form345.zip", bad)
        self.assertEqual(self._run(ins), 1)
        con = duckdb.connect(self.db, read_only=True)
        self.assertEqual(dict(con.execute("SELECT quarter, status FROM sec_ins_quarters").fetchall()),
                         {"2026q3": "ok", "2026q2": "failed"})
        self.assertEqual(con.execute("SELECT count(*) FROM sec_ins_transactions WHERE quarter='2026q2'").fetchone()[0], 0)
        con.close()

    def test_13f_load_window_unit_and_cusip(self):
        self.shelf.put("sec-13f", "01jun2026-31aug2026_form13f.zip", form13f_members())
        self.assertEqual(self._run(f13), 0)
        con = duckdb.connect(self.db, read_only=True)
        b = con.execute("SELECT batch, window_start, window_end, filings, holdings, value_unit, status "
                        "FROM sec_13f_batches").fetchone()
        self.assertEqual(b[0], "01jun2026-31aug2026")
        self.assertEqual((b[1].isoformat(), b[2].isoformat()), ("2026-06-01", "2026-08-31"))
        self.assertEqual((b[3], b[4], b[5], b[6]), (120, 120, "USD", "ok"))
        self.assertEqual(con.execute("SELECT count(DISTINCT cusip) FROM sec_13f_holdings").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT manager_name FROM sec_13f_filings WHERE manager_cik = 1067983").fetchone()[0],
                         "BERKSHIRE HATHAWAY INC")
        con.close()

    def test_13f_old_quarter_names_are_thousands(self):
        self.assertEqual(f13.batch_of("2013q4_form13f.zip")[0], "2013q4")
        self.assertEqual(f13.batch_of("2013q4_form13f.zip")[2].isoformat(), "2013-12-31")
        self.assertIsNone(f13.batch_of("readme.htm"))
        self.shelf.put("sec-13f", "2022q4_form13f.zip", form13f_members())
        self.assertEqual(self._run(f13), 0)
        con = duckdb.connect(self.db, read_only=True)
        self.assertEqual(con.execute("SELECT value_unit FROM sec_13f_batches").fetchone()[0], "USD thousands")
        con.close()


def _live_tables():
    path = ROOT / "data" / "sec.duckdb"
    if not path.exists():
        return set()
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            return {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        finally:
            con.close()
    except Exception:                                             # noqa: BLE001
        return set()


_LIVE = _live_tables()


@unittest.skipUnless({"sec_ins_filings", "sec_13f_filings", "sec_companies"} <= _LIVE,
                     "data/sec.duckdb without insider, 13F and universe tables")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.c = TestClient(app)

    def test_insiders_by_ticker_keeps_buys_and_sells_apart(self):
        r = self.c.get("/api/v1/us/insiders/company/NVDA?months=12&limit=20")
        self.assertEqual(r.status_code, 200, r.text[:300])
        d = r.json()
        self.assertEqual(d["cik"], 1045810)
        s = d["summary"]
        self.assertIn("open_market_buys", s)
        self.assertTrue(all(t["trans_code"] for t in d["trades"]))
        for t in d["trades"]:
            if t["price"] is None:
                self.assertIsNone(t["value"])
        self.assertEqual(d["provenance"]["trust"], "official")

    def test_recent_purchases_have_a_value_and_no_amendments(self):
        d = self.c.get("/api/v1/us/insiders/recent?code=P&min_value=1000000&days=400&limit=20").json()
        self.assertTrue(d["trades"])
        self.assertTrue(all(t["value"] >= 1e6 and t["trans_code"] == "P" and not t["amendment"] for t in d["trades"]))
        self.assertEqual(self.c.get("/api/v1/us/insiders/recent?code=ZZ").status_code, 400)

    def test_13f_holders_by_cusip_and_by_name(self):
        d = self.c.get("/api/v1/us/holdings/issuer?cusip=037833100&limit=5").json()
        self.assertEqual(d["cusip"], "037833100")
        self.assertGreater(d["holders"], 100)
        vals = [r["value"] for r in d["rows"]]
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertTrue(all(0 < r["share_of_reported_pct"] <= 100 for r in d["rows"]))
        n = self.c.get("/api/v1/us/holdings/issuer?q=nvidia&limit=2").json()
        self.assertTrue(n["candidates"])
        self.assertEqual(self.c.get("/api/v1/us/holdings/issuer").status_code, 400)
        self.assertEqual(self.c.get("/api/v1/us/holdings/issuer?cusip=12").status_code, 400)

    def test_manager_and_search(self):
        m = self.c.get("/api/v1/us/holdings/managers?q=berkshire").json()
        self.assertTrue(m["managers"])
        d = self.c.get("/api/v1/us/holdings/manager/%d?limit=3" % m["managers"][0]["manager_cik"]).json()
        self.assertEqual(len(d["rows"]), 3)
        self.assertTrue(all(r["share_of_portfolio_pct"] is not None for r in d["rows"]))

    def test_health_and_manifests(self):
        rows = self.c.get("/api/v1/catalog/health").json().get("equity_extractors") or []
        names = {r["dataset"] for r in rows}
        self.assertTrue({"sec-insiders", "sec-13f"} <= names, names)
        for ds in ("sec-insiders", "sec-13f"):
            r = self.c.get("/api/v1/catalog/manifests/%s" % ds)
            self.assertEqual(r.status_code, 200, ds)


if __name__ == "__main__":
    unittest.main()
