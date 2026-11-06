# -*- coding: utf-8 -*-
"""Impossible filed figures are flagged and kept out of rankings and answers.

2026-11-07, live: the Boards & Pay ranking of women on boards was led by
I-PEX at 2,000% (a filed 20 for 20%) and Eurasia Travel at 1,430%; the ask box
led a list with Sportsfield's 5,860% foreign ownership; 12,385 company-years
had a board size of 0 where the officers table was text only.
"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "equity"))

from app import plausibility as pl  # noqa: E402
from app import us_catalog, us_checks  # noqa: E402
import board_extract  # noqa: E402


def test_share_of_women_above_one_is_flagged():
    row = pl.company_year({"female_ratio_filed": 20.0, "female_managers_ratio": 0.05})
    assert [f["field"] for f in row["flags"]] == ["female_ratio_filed"]


def test_real_figures_are_not_flagged():
    row = pl.company_year({"female_ratio_filed": 0.2, "gender_pay_gap_all": 0.7,
                           "avg_salary_yen": 6500000})
    assert "flags" not in row


def test_pay_gap_typed_as_percentage_is_flagged():
    row = pl.company_year({"gender_pay_gap_all": 71.4})
    assert row["flags"][0]["field"] == "gender_pay_gap_all"


def test_stake_above_100_flagged_but_a_negative_broker_line_is_not():
    rows = pl.lvh_rows([{"holder_prior_ratio_pct": 600000.0}, {"ratio_pct": -4.82}])
    assert rows[0]["flags"][0]["field"] == "holder_prior_ratio_pct"
    assert "flags" not in rows[1]


def test_sql_helpers_blank_impossible_values():
    con = duckdb.connect()
    got = con.execute("SELECT %s, %s, %s, %s, %s, %s, %s, %s FROM (SELECT 5860.0::DOUBLE AS a, 58.3::DOUBLE AS b, 20.0::DOUBLE AS r, "
                      "0.2::DOUBLE AS r2, 9.4e12::DOUBLE AS s, 6.5e6::DOUBLE AS s2, 71.4::DOUBLE AS g, 0.7::DOUBLE AS g2)"
                      % (pl.pct_ok("a"), pl.pct_ok("b"), pl.ratio_ok("r"), pl.ratio_ok("r2"),
                         pl.salary_ok("s"), pl.salary_ok("s2"), pl.pay_gap_ok("g"), pl.pay_gap_ok("g2"))).fetchone()
    assert got == (None, 58.3, None, 0.2, None, 6.5e6, None, 0.7)


def test_shareholder_mix_view_blanks_a_count_typed_as_a_percentage():
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS jp")
    con.execute("""CREATE TABLE jp.eq_own_filings (sec_code VARCHAR, edinet_code VARCHAR, filer_name VARCHAR,
                   period_end DATE, filed_date DATE, status VARCHAR, shareholders_total DOUBLE,
                   foreign_pct DOUBLE, financial_institutions_pct DOUBLE, other_corporations_pct DOUBLE,
                   individuals_pct DOUBLE, securities_firms_pct DOUBLE, government_pct DOUBLE)""")
    con.execute("""INSERT INTO jp.eq_own_filings VALUES
                   ('7080', 'E1', 'Sportsfield', '2026-03-31', '2026-06-20', 'partial', 1220, 5860.012, 4.2, 0.75, 86.6, 2.6, NULL),
                   ('6758', 'E2', 'Sony', '2026-03-31', '2026-06-18', 'clean', 900000, 58.26, 20.0, 5.0, 12.0, 4.0, 0.0)""")
    sql = us_catalog.VIEWS["jp_shareholder_mix"][1].format(sec="sec", jp="jp", macro="macro")
    got = dict(con.execute("SELECT sec_code, foreign_pct FROM (%s)" % sql).fetchall())
    assert got == {"7080": None, "6758": 58.26}


def test_ask_box_check_sends_back_an_answer_led_by_an_impossible_share():
    f = us_checks.impossible([["7080", "Sportsfield", 5860.0]], ["sec_code", "name_en", "foreign_pct"])
    assert f and f["severity"] == us_checks.REVISE
    assert us_checks.impossible([["6758", "Sony", 58.3]], ["sec_code", "name_en", "foreign_pct"]) is None
    assert us_checks.impossible([["Nomura", -4.82]], ["holder", "ratio_pct"]) is None


def test_untagged_board_is_unknown_not_zero():
    board, pay, named, agg, problems = board_extract.build("S1", {}, [], {}, {}, "2026-03-31")
    assert board == [] and agg["board_size"] is None


def test_salary_tenfold_against_own_years_is_flagged():
    # 加藤製作所 FY2026: ¥60.0m after ¥4.9m-¥5.9m every earlier year
    row = pl.company_year({"avg_salary_yen": 60010000}, [4850000, 4900000, 5690000, 5920000])
    assert row["flags"][0]["field"] == "avg_salary_yen"
    # 光通信's real rise from ¥24m to ¥35m is not
    assert "flags" not in pl.company_year({"avg_salary_yen": 35175984}, [16823338, 21018028, 24086850])


def test_salary_sql_tests_own_years():
    con = duckdb.connect()
    con.execute("CREATE TABLE eq_company_year (doc_id VARCHAR, edinet_code VARCHAR, avg_salary_yen BIGINT)")
    con.execute("INSERT INTO eq_company_year VALUES ('a','E1',4900000),('b','E1',5920000),('c','E1',60010000),"
                "('d','E2',24086850),('e','E2',35175984)")
    got = dict(con.execute("SELECT doc_id, %s FROM eq_company_year g" % pl.salary_ok("g.avg_salary_yen", "g")).fetchall())
    assert got == {"a": 4900000, "b": 5920000, "c": None, "d": 24086850, "e": 35175984}
