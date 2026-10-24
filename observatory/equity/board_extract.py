# -*- coding: utf-8 -*-
"""M2 — production boards-and-pay extractor (役員の状況・役員の報酬等).

Same source file as the cross-shareholding extractor — the annual report's
XBRL-to-CSV package (EDINET type=5) — so nothing new is downloaded. Filing
discovery, the EDINET code list and the archive readers are imported from
`extract.py` rather than reimplemented.

Writes four tables into the same equity DuckDB, in their own namespace:

    eq_board          one row per person on the board, per filing
    eq_pay_category   one row per officer category, per filing
    eq_pay_named      one row per individual whose 連結報酬等 is disclosed
    eq_company_year   one row per filing: board/pay/employee aggregates + gates

It deliberately does NOT write eq_filings: that table is the holdings
extractor's vintage record, and two extractors writing one row would fight over
`status`. eq_company_year carries this dataset's own provenance (sha256 of the
bytes parsed, parser version, extraction status) and joins to eq_filings on
doc_id when both datasets have seen a filing.

TWO RECONCILIATIONS ARE CARRIED IN THE DATA, not left to a footnote:

  1. 連結報酬等 is CONSOLIDATED. It includes pay from group companies, which is
     why an Arm executive appears in SoftBank's filing. It is a different basis
     from the officer-category totals (the filer's own 報酬等 table), so the two
     must never be netted. Every named row carries pay_basis='consolidated';
     every filing carries named_sum_yen alongside pay_category_total_yen and the
     flag named_exceeds_category, which is true whenever the arithmetic proves
     the bases differ.
  2. PAY COMPONENTS NEED NOT SUM TO THE FILED TOTAL. 非金銭報酬等 is additive for
     some filers and an "of which" memo for others, and components are printed
     rounded to ¥mn. The filed category total is the published number;
     components ship as filed with components_sum_yen and components_reconcile
     per row, rolled up per filing as pay_rows_reconciled / pay_rows_with_components.

Gates (see board_m1/README.md for why each is shaped the way it is):
    G1  board rows <= the filer's own 役員 headcount   (執行役 are not tagged)
    G2  recomputed female ratio reproduces the filed one, when the populations coincide
    G5  date of birth implies an age of 20-100
G3 (components) and G4 (¥100m threshold) are disclosure flags, not gates.

One writer at a time: stop the local `uvicorn app.main:app` first — DuckDB
counts its read-only connection as a conflicting lock — and never run this at
the same time as extract.py.

Usage:
    python board_extract.py --limit 200                  # local smoke test
    python board_extract.py --all --source s3 --workers 16   # full 5 years

Python 3.9.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, compact,
                     DB_PATH, incremental_window, record_run,
                     seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)

PARSER_VERSION = "board-m2-2"


class UnsupportedForm(Exception):
    """An annual report on a form that tags no governance section."""

C = "jpcrp_cor:"
DIR_SUFFIX = "InformationAboutDirectorsAndCorporateAuditors"
PERSON_FIELDS = {
    "name_ja": C + "Name" + DIR_SUFFIX,
    "title_ja": C + "OfficialTitleOrPosition" + DIR_SUFFIX,
    "dob": C + "DateOfBirth" + DIR_SUFFIX,
    "shares_held": C + "NumberOfSharesHeldOrdinaryShares" + DIR_SUFFIX,
}
NAMED_PAY = C + ("TotalAmountOfRemunerationEtcPaidByGroup"
                 "RemunerationEtcPaidByGroupToEachDirectorOrOtherOfficer")
NAMED_PAY_THRESHOLD = 100000000

PAY_FIELDS = {
    "total_yen": "TotalAmountOfRemunerationEtcRemunerationEtcByCategoryOfDirectorsAndOtherOfficers",
    "fixed_yen": "FixedRemunerationRemunerationByCategoryOfDirectorsAndOtherOfficers",
    "base_yen": "BaseRemunerationRemunerationEtcByCategoryOfDirectorsAndOtherOfficers",
    "performance_yen": "PerformanceBasedRemunerationRemunerationByCategoryOfDirectorsAndOtherOfficers",
    "bonus_yen": "BonusRemunerationEtcByCategoryOfDirectorsAndOtherOfficers",
    "non_monetary_yen": "NonMonetaryRemunerationRemunerationByCategoryOfDirectorsAndOtherOfficers",
    "retirement_yen": "RetirementBenefitsRemunerationEtcByCategoryOfDirectorsAndOtherOfficers",
    "headcount": "NumberOfDirectorsAndOtherOfficersRemunerationEtcByCategoryOfDirectorsAndOtherOfficers",
}
PAY_COMPONENTS = ["fixed_yen", "base_yen", "performance_yen", "bonus_yen",
                  "non_monetary_yen", "retirement_yen"]
# Filers invent components (RestrictedShareAwards…, ShareAwards…, Monthly…) and
# some are NEGATIVE — a forfeited restricted-share award is a reversal. Match
# the family by pattern; a fixed list silently loses them.
PAY_FAMILY = re.compile(r"RemunerationE?t?c?ByCategoryOfDirectorsAndOtherOfficers$")

# Gate thresholds, each set from the distribution of the whole archive rather
# than from a round number that felt safe. See G6-G8 in build().
MAX_OFFICERS = 100                  # largest real board + auditors seen: 52
SALARY_BAND = (500000, 100000000)   # average annual pay per employee, yen
MAX_PAY_PER_OFFICER = 3000000000    # yen per officer per year, one category

CATEGORY_EN = {
    "DirectorsExcludingOutsideDirectorsMember": "Directors (excl. outside)",
    "DirectorsExcludingAuditAndSupervisoryCommitteeMembersAndOutsideDirectorsMember":
        "Directors excl. audit cttee (excl. outside)",
    "DirectorsAppointedAsAuditAndSupervisoryCommitteeMembersExcludingOutsideDirectorsMember":
        "Audit-cttee directors (excl. outside)",
    "CorporateAuditorsExcludingOutsideCorporateAuditorsMember": "Statutory auditors (excl. outside)",
    "OutsideDirectorsAndOtherOfficersMember": "Outside directors and officers",
    "OutsideDirectorsMember": "Outside directors",
    "OutsideCorporateAuditorsMember": "Outside statutory auditors",
    "ExecutiveOfficersMember": "Executive officers",
}

COMPANY_FIELDS = {
    "male_officers": C + "NumberOfMaleDirectorsAndOtherOfficers",
    "female_officers": C + "NumberOfFemaleDirectorsAndOtherOfficers",
    "female_ratio_filed": C + "RatioOfFemaleDirectorsAndOtherOfficers",
    "avg_employee_age": C + "AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees",
    "avg_tenure_years": C + "AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees",
    "avg_salary_yen": C + "AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
    "female_managers_ratio": C + "RatioOfFemaleEmployeesInManagerialPositionsMetricsOfReportingCompany",
    "gender_pay_gap_all": C + "AllEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany",
    "male_childcare_leave": C + "AllEmployeesRatioOfMaleEmployeesTakingChildcareLeaveMetricsOfReportingCompany",
}
EMPLOYEES = C + "NumberOfEmployees"
MISSING = ("", "-", "－", "−", "―", "ー", "—")


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def to_num(s):
    """A missing value is None, never 0."""
    s = norm(s).replace(",", "")
    if s in MISSING:
        return None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def to_int(x):
    return int(round(x)) if x is not None else None


def stated_dp(x):
    s = ("%.10f" % x).rstrip("0")
    return len(s.split(".")[1]) if "." in s and s.split(".")[1] else 0


def romaji(member):
    """'E39089-000ShinyaAkitoMember' -> ('Shinya Akito', 'shinya-akito').

    The suffix is the filer's own context label, so this is a display name and a
    WEAK person key: it joins a pay row to a board row inside one filing. It is
    not evidence that two companies share a director — that needs a curated map.

    Word boundaries are the filer's, never ours. Two habits break the
    CamelCase read and are handled rather than split blindly:

      * **A name typed entirely in capitals** — SATOMASAHIKO — states no
        boundary at all. Splitting on capitals turned 639 directors into rows
        of single letters ("S A T O M A S A H I K O"), which is not a name in
        any language. The run is kept whole instead: every letter as filed,
        none invented. Finding "Sato Masahiko" inside it would need a kana
        table the filing does not carry.
      * **A trailing digit** — Okita Fumio09 — is the filer disambiguating two
        context ids, not part of the person's name.
    """
    s = re.sub(r"^(jpcrp\d+-asr_)?E\d+-\d+", "", member)
    s = re.sub(r"(?:Member)+$", "", s)
    s = re.sub(r"\d+$", "", s)
    if s and not re.search(r"[a-z]", s):
        name = s
    else:
        parts = re.findall(r"[A-Z][a-z0-9'\-\.]*|[A-Z]+(?![a-z])", s)
        name = " ".join(parts) if parts else s
    return name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def classify(title):
    """Role bucket. Outside status is NOT derivable here — only ~3.5% of 役職名
    strings contain 社外 — so inside/outside comes from the pay categories."""
    t = norm(title)
    if "監査等委員" in t:
        return "audit_committee_director"
    if "監査役" in t:
        return "statutory_auditor"
    if "取締役" in t or "執行役" in t:
        return "director"
    return "other"


def parse_filing(blob):
    """Bytes of a type=5 zip -> (company facts, people, pay, named pay)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = [n for n in z.namelist() if "jpcrp030000-asr" in n]
    if not names:
        # Foreign-issuer (jpcrp080000) and 特定 form (jpcrp030200) annual reports
        # carry no 役員の状況 or 報酬等 tagging at all — verified, not assumed. They
        # are out of scope, not extraction failures, and are labelled as such.
        raise UnsupportedForm(", ".join(sorted(
            set(re.sub(r"-\d+_.*", "", n.split("/")[-1]) for n in z.namelist()
                if n.endswith(".csv"))) or ["no csv in package"]))
    rows = list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-16")),
                           delimiter="\t"))[1:]
    inv_company = {v: k for k, v in COMPANY_FIELDS.items()}
    inv_person = {v: k for k, v in PERSON_FIELDS.items()}
    inv_pay = {C + v: k for k, v in PAY_FIELDS.items()}

    company, people, pay, named = {}, defaultdict(dict), defaultdict(dict), {}
    order = []
    for r in rows:
        if len(r) < 9:
            continue
        el, ctx, val = r[0], r[2], r[8]
        if el in inv_company:
            company.setdefault(inv_company[el], norm(val))
        elif el == EMPLOYEES and ctx == "CurrentYearInstant":
            company.setdefault("employees_consolidated", norm(val))
        elif el == EMPLOYEES and ctx == "CurrentYearInstant_NonConsolidatedMember":
            company.setdefault("employees_company", norm(val))
        elif el in inv_person and "Member" in ctx:
            key = ctx.split("_", 1)[1]
            if key not in people:
                order.append(key)
            people[key][inv_person[el]] = norm(val)
        elif ctx.startswith("CurrentYearDuration_") and PAY_FAMILY.search(el):
            key = ctx.split("_", 1)[1]
            if el in inv_pay:
                pay[key][inv_pay[el]] = norm(val)
            else:
                pay[key].setdefault("_other", {})[el.split(":")[-1]] = norm(val)
        elif el == NAMED_PAY and ctx.startswith("CurrentYearDuration_"):
            named[ctx.split("_", 1)[1]] = norm(val)
    return company, [(k, people[k]) for k in order], pay, named


SCHEMA_SQL = """
        -- One row per person in 役員の状況. This is the BOARD (取締役会); in a
        -- 指名委員会等設置会社 the filer's 役員 tally also counts 執行役 who are
        -- disclosed only in a text block, hence eq_company_year.officers_untagged.
        CREATE TABLE IF NOT EXISTS eq_board (
            doc_id VARCHAR, seat_no INTEGER, person_key VARCHAR, name_ja VARCHAR,
            name_en VARCHAR, title_ja VARCHAR, role VARCHAR, is_representative BOOLEAN,
            date_of_birth DATE, age_at_period_end INTEGER, shares_held BIGINT);
        -- 役員区分ごとの報酬等. total_yen is the FILED figure and the published
        -- one; the components are as filed and need not sum to it (see
        -- components_reconcile) because filers differ on whether 非金銭報酬等 is
        -- additive or an "of which" memo, and print components rounded to ¥mn.
        CREATE TABLE IF NOT EXISTS eq_pay_category (
            doc_id VARCHAR, category_key VARCHAR, category_en VARCHAR,
            is_custom_category BOOLEAN, headcount INTEGER, total_yen BIGINT,
            per_head_yen BIGINT, fixed_yen BIGINT, base_yen BIGINT,
            performance_yen BIGINT, bonus_yen BIGINT, non_monetary_yen BIGINT,
            retirement_yen BIGINT, other_components_yen BIGINT,
            other_components VARCHAR, components_sum_yen BIGINT,
            components_reconcile BOOLEAN);
        -- 役員ごとの連結報酬等: individuals paid ¥100m or more. pay_basis is
        -- always 'consolidated' — it includes pay from group companies and is a
        -- DIFFERENT BASIS from eq_pay_category. Never net the two.
        CREATE TABLE IF NOT EXISTS eq_pay_named (
            doc_id VARCHAR, person_key VARCHAR, name_en VARCHAR, pay_basis VARCHAR,
            consolidated_pay_yen BIGINT, voluntary_below_100m BOOLEAN,
            on_board_at_filing BOOLEAN);
        CREATE TABLE IF NOT EXISTS eq_company_year (
            doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
            filer_name VARCHAR, period_end DATE, filed_date DATE, sha256 VARCHAR,
            parser_version VARCHAR, status VARCHAR, detail VARCHAR,
            board_size INTEGER, officers_tagged INTEGER, officers_untagged INTEGER,
            male_officers INTEGER, female_officers INTEGER,
            female_ratio_filed DOUBLE, female_ratio_calc DOUBLE,
            avg_director_age DOUBLE, directors_70_plus INTEGER,
            employees_consolidated INTEGER, employees_company INTEGER,
            avg_employee_age DOUBLE, avg_tenure_years DOUBLE, avg_salary_yen BIGINT,
            female_managers_ratio DOUBLE, gender_pay_gap_all DOUBLE,
            male_childcare_leave DOUBLE, pay_category_total_yen BIGINT,
            pay_rows_with_components INTEGER, pay_rows_reconciled INTEGER,
            named_count INTEGER, named_sum_yen BIGINT, named_exceeds_category BOOLEAN);
"""


def build(doc_id, company, people, pay, named, period_end):
    """Facts -> (board rows, pay rows, named rows, aggregates, problems)."""
    problems = []
    pe = None
    if period_end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_end):
        pe = dt.date(*[int(x) for x in period_end.split("-")])

    board = []
    for i, (member, f) in enumerate(people, 1):
        if not f.get("name_ja"):
            continue          # a member context with no name is a totals row
        name_en, key = romaji(member)
        dob = f.get("dob", "")
        dob = dob if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob) else None
        age = None
        if dob and pe:
            b = dt.date(*[int(x) for x in dob.split("-")])
            age = pe.year - b.year - ((pe.month, pe.day) < (b.month, b.day))
            if not 20 <= age <= 100:                                        # G5
                problems.append("G5 implausible age %s (%s)" % (age, name_en))
                age = None
        board.append((doc_id, len(board) + 1, key, f.get("name_ja"), name_en,
                      f.get("title_ja") or None, classify(f.get("title_ja", "")),
                      "代表" in (f.get("title_ja") or ""), dob, age,
                      to_int(to_num(f.get("shares_held", "")))))

    pay_rows, with_comp, reconciled, cat_total = [], 0, 0, 0
    for member, f in sorted(pay.items()):
        cat = re.sub(r"^(jpcrp\d+-asr_)?E\d+-\d+", "", member)
        vals = {c: to_num(f.get(c, "")) for c in PAY_FIELDS}
        other = {k: to_num(v) for k, v in (f.get("_other") or {}).items()}
        other = {k: v for k, v in other.items() if v is not None}
        parts = [vals[c] for c in PAY_COMPONENTS if vals[c] is not None] + list(other.values())
        csum = sum(parts) if parts else None
        rec = None
        if csum is not None and vals["total_yen"]:
            rec = abs(csum - vals["total_yen"]) <= max(1e6 * len(parts),
                                                       vals["total_yen"] * 0.005)
            with_comp += 1
            reconciled += 1 if rec else 0
        # "Of which" rows (うち社外役員…) are a subset of the row above, not a
        # category of their own — adding them to the filing total double-counts
        # those officers. 106 filings use them.
        if vals["total_yen"] and not cat.lower().startswith("ofwhich"):
            cat_total += vals["total_yen"]
        per_head = (vals["total_yen"] / vals["headcount"]
                    if vals["total_yen"] and vals["headcount"] else None)
        pay_rows.append((doc_id, cat, CATEGORY_EN.get(cat), cat not in CATEGORY_EN,
                         to_int(vals["headcount"]), to_int(vals["total_yen"]),
                         to_int(per_head), to_int(vals["fixed_yen"]),
                         to_int(vals["base_yen"]), to_int(vals["performance_yen"]),
                         to_int(vals["bonus_yen"]), to_int(vals["non_monetary_yen"]),
                         to_int(vals["retirement_yen"]),
                         to_int(sum(other.values())) if other else None,
                         "|".join(sorted(other)) or None, to_int(csum), rec))

    seats = {r[2] for r in board}
    named_rows, named_sum = [], 0
    for member, val in sorted(named.items()):
        amt = to_num(val)
        if amt is None:
            continue
        name_en, key = romaji(member)
        named_sum += amt
        # ¥100m is the mandatory TRIGGER, not a floor: Takeda, Recruit and others
        # disclose directors voluntarily below it.                            G4
        named_rows.append((doc_id, key, name_en, "consolidated", to_int(amt),
                           amt < NAMED_PAY_THRESHOLD, key in seats))

    male = to_num(company.get("male_officers", ""))
    female = to_num(company.get("female_officers", ""))
    officers = (male + female) if (male is not None and female is not None) else None
    n_board = len(board)
    if n_board and officers is not None and n_board > officers:             # G1
        problems.append("G1 %d board rows > %d tagged officers" % (n_board, officers))
    filed = to_num(company.get("female_ratio_filed", ""))
    calc = (female / n_board) if (n_board and female is not None) else None
    if filed is not None and calc is not None and officers == n_board:      # G2
        if abs(calc - filed) >= 10.0 ** -stated_dp(filed):
            problems.append("G2 female ratio filed %s vs recomputed %.4f" % (filed, calc))

    # Three plausibility gates. Each one has a line drawn from what the whole
    # archive actually contains, not from taste, and each catches a filing that
    # states a number its own document cannot support. The number is still
    # stored exactly as filed -- the gate marks the filing, it never edits it.
    if officers is not None and officers > MAX_OFFICERS:                    # G6
        # The largest real board-plus-auditors in 21,000 filings is 52. A
        # filing claiming thousands has tagged a share count or a yen amount
        # into the officer-count element.
        problems.append("G6 %d tagged officers on a %d-seat board"
                        % (officers, n_board))
    salary = to_int(to_num(company.get("avg_salary_yen", "")))
    if salary is not None and not SALARY_BAND[0] <= salary <= SALARY_BAND[1]:  # G7
        # Average annual pay per employee. Below the band the filing has typed
        # a figure already in thousands; above it, one already in millions.
        problems.append("G7 average annual salary %d yen" % salary)
    worst = max([r[6] for r in pay_rows if r[6] is not None] or [0])
    if worst > MAX_PAY_PER_OFFICER:                                         # G8
        # Per officer, per year. The largest package ever disclosed in Japan is
        # about 13bn yen and that is one named person, so a whole CATEGORY
        # averaging more than 3bn has been filed on the wrong scale.
        problems.append("G8 pay of %d yen per officer in one category" % worst)

    ages = [r[9] for r in board if r[9] is not None]
    agg = {
        "board_size": n_board,
        "officers_tagged": to_int(officers),
        "officers_untagged": (to_int(officers) - n_board) if (officers is not None and n_board) else None,
        "male_officers": to_int(male), "female_officers": to_int(female),
        "female_ratio_filed": filed, "female_ratio_calc": round(calc, 4) if calc is not None else None,
        "avg_director_age": round(sum(ages) / len(ages), 1) if ages else None,
        "directors_70_plus": sum(1 for a in ages if a >= 70) if ages else None,
        "employees_consolidated": to_int(to_num(company.get("employees_consolidated", ""))),
        "employees_company": to_int(to_num(company.get("employees_company", ""))),
        "avg_employee_age": to_num(company.get("avg_employee_age", "")),
        "avg_tenure_years": to_num(company.get("avg_tenure_years", "")),
        "avg_salary_yen": to_int(to_num(company.get("avg_salary_yen", ""))),
        "female_managers_ratio": to_num(company.get("female_managers_ratio", "")),
        "gender_pay_gap_all": to_num(company.get("gender_pay_gap_all", "")),
        "male_childcare_leave": to_num(company.get("male_childcare_leave", "")),
        "pay_category_total_yen": to_int(cat_total) or None,
        "pay_rows_with_components": with_comp, "pay_rows_reconciled": reconciled,
        "named_count": len(named_rows), "named_sum_yen": to_int(named_sum) or None,
        # Proof in the data that the two are different bases: consolidated pay
        # for a handful of people exceeding the filer's whole officer-category
        # total can only happen because group-company pay is included.
        "named_exceeds_category": bool(named_rows and cat_total and named_sum > cat_total),
    }
    return board, pay_rows, named_rows, agg, problems


AGG_COLS = ["board_size", "officers_tagged", "officers_untagged", "male_officers",
            "female_officers", "female_ratio_filed", "female_ratio_calc",
            "avg_director_age", "directors_70_plus", "employees_consolidated",
            "employees_company", "avg_employee_age", "avg_tenure_years",
            "avg_salary_yen", "female_managers_ratio", "gender_pay_gap_all",
            "male_childcare_leave", "pay_category_total_yen",
            "pay_rows_with_components", "pay_rows_reconciled", "named_count",
            "named_sum_yen", "named_exceeds_category"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every archived filer")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs — re-extract just these "
                                   "(used to correct a subset without a full pass)")
    ap.add_argument("--new-only", action="store_true",
                    help="extract only filings archived since the last "
                         "recorded run (plus a lookback); what the nightly "
                         "refresh uses. A DB with no recorded run is built "
                         "in full, so this is always safe to pass.")
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--catch-up", type=int, default=CATCH_UP_DAYS,
                    metavar="DAYS",
                    help="on a --new-only run, also read the DAYS deepest archive "
                         "days below this extractor's own floor, so a "
                         "database built forward from a watermark fills in its "
                         "own history a slice at a time. 0 (default) is "
                         "forward only.")
    args = ap.parse_args()

    src = S3Source(args.workers) if args.source == "s3" else LocalSource()
    codelist = load_codelist()
    listed = {d["ＥＤＩＮＥＴコード"] for d in codelist if d["上場区分"] == "上場"}

    # Window first, then discovery: knowing `since` lets the bucket listing
    # seek to it instead of paging five years of keys.
    since, have = (incremental_window(args.db, "boards-and-pay", "eq_board")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, "boards-and-pay"))
    if since is not None:
        print("incremental: %d of %d archived filings are new since %s"
              % (len(pending), len(filings), since))
    meta = src.list_metadata(
        days=None if since is None else {r["date"] for r in pending.values()})
    targets = []
    for doc_id, rec in sorted(pending.items()):
        m = meta.get(doc_id) or {}
        if not annual(m, rec):
            continue
        if args.all or m.get("edinetCode") in listed:
            targets.append((doc_id, rec, m))
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.limit:
        targets = targets[:args.limit]
    years = sorted({(m.get("periodEnd") or "?")[:4] for _, _, m in targets})
    print("target filings: %d (source=%s) spanning %s"
          % (len(targets), src.name, "/".join(years)))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)

    def fetch_and_parse(t):
        doc_id, rec, m = t
        sha = None
        try:
            blob = src.read_zip(doc_id, rec["date"])
            # Hash the bytes actually read, so every row's provenance is verifiable
            # against the archive rather than trusting a manifest — including rows
            # we could not extract, which still record which artifact was examined.
            sha = hashlib.sha256(blob).hexdigest()
            return t, parse_filing(blob), sha, None
        except UnsupportedForm as e:
            return t, None, sha, ("unsupported_form", "form carries no governance "
                                  "section: %s" % e)
        except Exception as e:                                       # noqa: BLE001
            return t, None, sha, ("failed", "%s: %s" % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    tot_board = tot_pay = tot_named = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha, err = fut.result()
            done += 1
            if done % 1000 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            period_end = m.get("periodEnd") or None
            base = [doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"), period_end, rec["date"],
                    sha or rec.get("sha256"), PARSER_VERSION]
            for t in ("eq_board", "eq_pay_category", "eq_pay_named", "eq_company_year"):
                con.execute("DELETE FROM %s WHERE doc_id = ?" % t, [doc_id])
            if err:
                st, detail = err
                stats[st] += 1
                con.execute("INSERT INTO eq_company_year VALUES (%s)"
                            % ",".join(["?"] * (10 + len(AGG_COLS))),
                            base + [st, detail] + [None] * len(AGG_COLS))
                continue
            company, people, pay, named = parsed
            board, pay_rows, named_rows, agg, problems = build(
                doc_id, company, people, pay, named, period_end)
            if not board:
                status, detail = "no_tagged_board", "officers table is TextBlock only"
            elif problems:
                status, detail = "partial", "; ".join(problems[:4])
            else:
                status, detail = "clean", None
            stats[status] += 1
            con.execute("INSERT INTO eq_company_year VALUES (%s)"
                        % ",".join(["?"] * (10 + len(AGG_COLS))),
                        base + [status, detail] + [agg[c] for c in AGG_COLS])
            if board:
                con.executemany("INSERT INTO eq_board VALUES (?,?,?,?,?,?,?,?,?,?,?)", board)
                tot_board += len(board)
            if pay_rows:
                con.executemany("INSERT INTO eq_pay_category VALUES (%s)"
                                % ",".join(["?"] * 17), pay_rows)
                tot_pay += len(pay_rows)
            if named_rows:
                con.executemany("INSERT INTO eq_pay_named VALUES (?,?,?,?,?,?,?)", named_rows)
                tot_named += len(named_rows)
    con.close()
    record_run(args.db, "boards-and-pay", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)
    print("filings: %s" % dict(stats))
    print("rows: board %d, pay categories %d, named individuals %d"
          % (tot_board, tot_pay, tot_named))
    print("wrote", os.path.normpath(args.db))


if __name__ == "__main__":
    main()
