# -*- coding: utf-8 -*-
u"""M2 — ownership extractor: 大株主の状況 + 所有者別状況.

The other half of the ownership graph. `extract.py` reads what a company
HOLDS (policy shareholdings); this reads who holds IT — the register — out of
the same annual-report CSV package (EDINET type=5) that is already archived,
so nothing new is downloaded and no capture changes.

Three tables, in the equity namespace:

    eq_major_shareholders  one row per named holder in 大株主の状況
    eq_own_category        one row per investor category in 所有者別状況,
                           per share class
    eq_own_filings         one row per filing: register aggregates, the
                           denominators, and this dataset's own gates

Like board_extract.py it deliberately does NOT write eq_filings — that row is
the holdings extractor's vintage record, and two writers would fight over
`status`. eq_own_filings carries this dataset's own provenance (the SHA-256 of
the bytes it parsed, parser version, extraction status) and joins on doc_id.

WHAT THE NUMBERS MEAN, and the two things a reader must not be allowed to
assume:

  1. THE REGISTER IS NOT BENEFICIAL OWNERSHIP. Japan's top-ten is dominated by
     two nominee trust banks — 日本マスタートラスト信託銀行（信託口） and
     株式会社日本カストディ銀行（信託口） — which hold for index funds and
     pension money and own none of it. Ranked naively, the same two names are
     the largest shareholder of almost every listed company in Japan, which is
     true of the register and false of the market. Every row therefore carries
     a `holder_kind`, and anything derived from the register states whether
     nominee rows are in it. holder_kind is OUR classification, not a filed
     field: it is derived, and labelled as such wherever it is shown.
  2. THE RATIO'S DENOMINATOR EXCLUDES TREASURY SHARES. 大株主の状況 states
     発行済株式（自己株式を除く。）の総数に対する割合, while 所有者別状況
     percentages are of all issued shares. The two columns are close but not
     the same measure and must never be netted or compared row for row.

Percentages are stored as PERCENT. The XBRL carries them as pure fractions
(0.0918) while the filing prints 9.18 under a （％） header; ×100 is a unit
conversion of the filed figure, not a recomputation, and the printed precision
is preserved.

Gates (each recomputes a number the filer published, never one we invented):
    G1  category unit counts sum to the filer's own 計 row            (exact)
    G2  category percentages sum to 100                (rounding-aware)
    G3  named holders' ratios sum to the filer's own 計 ratio (rounding-aware)
    G4  a holder's shares do not exceed shares in issue
G2/G3 allow rows x half of the last printed digit, taken from the filing's own
precision — the only honest tolerance, since the filer rounds each row.

One writer at a time: stop the local `uvicorn app.main:app` first — DuckDB
counts its read-only connection as a conflicting lock — and never run this at
the same time as extract.py or board_extract.py.

Usage:
    python ownership_extract.py --limit 200                   # local smoke test
    python ownership_extract.py --all --source s3 --workers 16   # full 5 years

Python 3.9.
"""
import argparse
import csv
import hashlib
import io
import os
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, annual, load_codelist, build_index, pick,
                     filing_evidence, norm, core_name, base_name, is_foreign,
                     compact, DB_PATH, incremental_window, record_run,
                     seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor,
                     sec_code_of)

PARSER_VERSION = "own-1"

C = "jpcrp_cor:"


class UnsupportedForm(Exception):
    u"""An annual report on a form that tags no ownership section."""


# ---- the register (大株主の状況) -------------------------------------------
#
# Row facts hang off numbered member contexts. Past row 15 filers switch to a
# context id prefixed with their own extension namespace
# (CurrentYearInstant_jpcrp030000-asr_E03738-000No16MajorShareholdersMember),
# so the prefix is stripped before the row number is read — the same trap the
# holdings and boards extractors hit on their own tables.
MAJOR_MEMBER = re.compile(r"^No(\d+)MajorShareholdersMember$")
FILER_NS = re.compile(r"^jpcrp\d+-asr_E\d+-\d+")
MAJOR_NAME = C + "NameMajorShareholders"
MAJOR_ADDRESS = C + "AddressMajorShareholders"
# Deliberately bare: the qualified names below are shared with the 議決権上位者
# variant table and with the policy-holdings tables, which is why the member
# context, not the element, decides what a row is.
MAJOR_SHARES = C + "NumberOfSharesHeld"
MAJOR_RATIO = C + "ShareholdingRatio"

ISSUED_MARK = C + "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
TREASURY_MARK = C + "TotalNumberOfSharesHeldTreasurySharesEtc"

# ---- the register by investor category (所有者別状況) ----------------------
#
# The category is in the ELEMENT NAME, not in a dimension, and the taxonomy is
# not consistent across the three families: a foreign institution is
# ForeignInvestorsOtherThanIndividuals in the count and unit elements and
# ForeignersOtherThanIndividuals in the percentage one. Each category therefore
# names its three suffixes explicitly rather than deriving them.
CATEGORIES = [
    ("government", "National and local governments",
     "NationalAndLocalGovernments", "NationalAndLocalGovernments", "NationalAndLocalGovernments"),
    ("financial_institutions", "Financial institutions",
     "FinancialInstitutions", "FinancialInstitutions", "FinancialInstitutions"),
    ("financial_service_providers", "Securities firms",
     "FinancialServiceProviders", "FinancialServiceProviders", "FinancialServiceProviders"),
    ("other_corporations", "Other corporations",
     "OtherCorporations", "OtherCorporations", "OtherCorporations"),
    ("foreign_institutions", "Foreign institutions",
     "ForeignInvestorsOtherThanIndividuals", "ForeignInvestorsOtherThanIndividuals",
     "ForeignersOtherThanIndividuals"),
    ("foreign_individuals", "Foreign individuals",
     "ForeignIndividualInvestors", "ForeignIndividualInvestors", "ForeignIndividuals"),
    ("individuals_and_others", "Individuals and others",
     "IndividualsAndOthers", "IndividualsAndOthers", "IndividualsAndOthers"),
]
COUNT_PREFIX = C + "NumberOfShareholders"
UNITS_PREFIX = C + "NumberOfSharesHeldNumberOfUnits"
PCT_PREFIX = C + "PercentageOfShareholdings"
LESS_THAN_UNIT = C + "NumberOfSharesHeldSharesLessThanOneUnit"

CAT_BY_ELEMENT = {}
for _key, _en, _count, _units, _pct in CATEGORIES:
    CAT_BY_ELEMENT[COUNT_PREFIX + _count] = (_key, "shareholders")
    CAT_BY_ELEMENT[UNITS_PREFIX + _units] = (_key, "units")
    CAT_BY_ELEMENT[PCT_PREFIX + _pct] = (_key, "pct")
CAT_TOTALS = {COUNT_PREFIX + "Total": "shareholders",
              UNITS_PREFIX + "Total": "units",
              PCT_PREFIX + "Total": "pct"}
CATEGORY_EN = dict((k, en) for k, en, _c, _u, _p in CATEGORIES)

MISSING = (u"", u"-", u"－", u"−", u"―", u"ー", u"—")


# ---- holder classification (DERIVED — never presented as filed) ------------
#
# What filers put in brackets after a holder's name is never part of the
# company's name. Two kinds, both stripped before entity matching (the registry
# holds the bank, not the account) and both kept in `account_raw`:
#   - a trust or omnibus account: （信託口）, （信託口９）, （投信口）, （共有口）
#   - a standing proxy: （常任代理人　シティバンク、エヌ・エイ東京支店） — the
#     custodian that acts for a foreign holder, not the holder itself.
ACCOUNT_SUFFIX = re.compile(
    u"[（(]\\s*([^（）()]*(?:信託口|投信口|口座|常任代理人|口)[^（）()]*)\\s*[）)]\\s*$")
# The same account written without brackets: 楽天証券株式会社共有口.
BARE_ACCOUNT = re.compile(u"(信託口|投信口|共有口|証券口)\\d*$")
# The four custody banks exist ONLY as nominees — every share they hold is
# held for somebody else, and the register cannot say for whom. (The last two
# are the pre-2020 names that merged into 日本カストディ銀行; five years of
# filings still carry them.) A securities firm is deliberately NOT here: it may
# hold as principal, and calling its position a nominee one would be a claim
# the filing does not make.
CUSTODY_BANKS = (u"日本マスタートラスト信託銀行", u"日本カストディ銀行",
                 u"日本トラスティ・サービス信託銀行", u"資産管理サービス信託銀行")
# Global custodians and street-name accounts, as filers spell them. Matched on
# the normalised name, so spacing and full/half width do not matter.
FOREIGN_NOMINEES = (u"STATESTREET", u"SSBTC", u"JPMORGANCHASEBANK", u"JPMORGANSECURITIES",
                    u"CHASEMANHATTANBANK", u"MOXLEYANDCO", u"OMNIBUSACCOUNT",
                    u"NOMINEESLIMITED", u"NOMINEESLTD",
                    u"JPMSPLC", u"BANKOFNEWYORK", u"BNYM", u"BNYGCM", u"BNYMELLON",
                    u"MELLONBANK", u"CITIBANK", u"HSBC", u"BNPPARIBAS",
                    u"NORTHERNTRUST", u"BROWNBROTHERSHARRIMAN", u"CACEIS",
                    u"NOMURAPBNOMINEES", u"SGCLIENTS", u"MSIP", u"MSCO",
                    u"GOLDMANSACHSINTERNATIONAL", u"MLPFS", u"UBSAG",
                    u"CREDITSUISSE", u"BARCLAYS", u"CLEARSTREAM", u"EUROCLEAR")
# The same custodians written in katakana, which is how a large minority of
# filers spell them (ビーエヌワイエム　アズ　エージーテイ　クライアンツ). Matched on
# the space-stripped name, so the filer's spacing does not matter.
FOREIGN_NOMINEES_KANA = (u"ステートストリート", u"ビーエヌワイエム",
                         u"ザバンクオブニユーヨーク", u"ザバンクオブニューヨーク",
                         u"ジェーピーモルガン", u"ジェイピーモルガン",
                         u"シティバンク", u"エイチエスビーシー", u"バークレイズ",
                         u"ノーザントラスト", u"ブラウンブラザーズハリマン",
                         u"メロンバンク", u"ビーエヌピーパリバ")
# 退職給付信託: a company parks its own cross-holdings in a pension trust. The
# settlor is named inside the account ("退職給付信託　丸紅口"), and that is the
# economic holder — the one fact in this table nobody else assembles.
# 退職給付信託　みずほ銀行口 · （退職給付信託口・株式会社紀陽銀行口） ·
# （退職給付信託三菱UFJ銀行口） — the settlor's name sits between the words and
# the account marker, sometimes after a second 口 and a separator.
RETIREMENT_TRUST = re.compile(
    u"退職給付信託\\s*口?\\s*[・･,、]?\\s*([^\\s　（）()・･,、]+?)\\s*口")
EMPLOYEE_ASSOC = (u"従業員持株会", u"社員持株会", u"職員持株会", u"取引先持株会",
                  u"役員持株会", u"持株会")
TREASURY_NAMES = (u"自己株式", u"自己名義")
# A corporate marker anywhere in the name rules out "individual". The list is
# deliberately wide: a false "individual" is a factual claim about a person.
CORPORATE_MARK = re.compile(
    u"(株式会社|有限会社|合同会社|合資会社|合名会社|株式會社|（株）|\\(株\\)|"
    u"財団|社団|法人|組合|基金|銀行|信託|保険|証券|商事|産業|工業|建設|"
    u"ホールディングス|グループ|会社|LTD|LIMITED|INC|CORP|CO\\.|L\\.P|LP|LLC|"
    u"BANK|TRUST|FUND|COMPANY|HOLDINGS|GMBH|S\\.A|N\\.V|PLC|PTE|AG$)",
    re.IGNORECASE)
# How filings write a natural person: Japanese characters only, short, and
# spaced — usually 姓␣名, but filers also pad every character (久　保　哲　夫),
# so the test runs on the de-spaced name and merely requires that a space was
# there. Deliberately conservative: calling a company an individual is
# harmless, calling a person a company attributes a corporate holding to them.
PERSON_NAME = re.compile(u"^[぀-ヿ㐀-鿿]{2,8}$")
HAS_SPACE = re.compile(u"[\\s　]")


def split_account(name):
    u"""('日本マスタートラスト信託銀行株式会社（信託口）') -> (base, '信託口')."""
    name = (name or "").strip()
    m = ACCOUNT_SUFFIX.search(name)
    if m:
        return name[:m.start()].strip(), m.group(1).strip()
    m = BARE_ACCOUNT.search(name)
    if m:
        return name[:m.start()].strip(), m.group(0).strip()
    return name, None


def classify_holder(name_raw, base, account=None):
    u"""(holder_kind, beneficiary_raw). Derived, disclosed as derived.

    Ordered strongest-evidence-first: a retirement-benefit trust is held
    through a nominee bank, so it must be recognised before the bank is.

    The custodian tests run on the name with its bracketed qualifier REMOVED,
    because that bracket usually names the holder's 常任代理人 — its standing
    proxy in Japan — and the proxy is not the holder. Norway's sovereign fund
    appears as GOVERNMENT OF NORWAY（常任代理人 シティバンク）: matching the
    raw string files the country's own money under "custodian account".
    """
    n = norm(base or name_raw)
    whole = norm(name_raw)
    m = RETIREMENT_TRUST.search(name_raw or "")
    if m:
        return "retirement_benefit_trust", m.group(1)
    if any(w in whole for w in TREASURY_NAMES):
        return "treasury", None
    if any(w in whole for w in EMPLOYEE_ASSOC):
        return "employee_association", None
    if any(w in n for w in CUSTODY_BANKS):
        return "trust_bank_nominee", None
    # Any other trust bank is a nominee only when the name carries an ACCOUNT —
    # 三井住友信託銀行株式会社 in its own name is a principal holder,
    # みずほ信託銀行株式会社（信託口） and 野村信託銀行株式会社（投信口） are not.
    if u"信託銀行" in n and account:
        return "trust_bank_nominee", None
    flat = re.sub(u"[^0-9A-Za-z]", "", n).upper()
    if any(w in flat for w in FOREIGN_NOMINEES):
        return "foreign_nominee", None
    if any(w in n for w in FOREIGN_NOMINEES_KANA):
        return "foreign_nominee", None
    if (not CORPORATE_MARK.search(n) and HAS_SPACE.search(name_raw or "")
            and PERSON_NAME.match(n)):
        return "individual", None
    if is_foreign(base or name_raw or ""):
        return "foreign_entity", None
    return "entity", None


# Abbreviations the register uses that the EDINET registry spells out. Applied
# only to register names before matching; the raw name is stored untouched.
REGISTER_ALIASES = ((u"(相)", u"相互会社"), (u"（相）", u"相互会社"),
                    (u"(有)", u"有限会社"), (u"（有）", u"有限会社"))


def for_matching(name):
    for a, b in REGISTER_ALIASES:
        name = name.replace(a, b)
    return name


def to_num(s):
    u"""A missing value is None, never 0."""
    if isinstance(s, (int, float)):
        return float(s)
    s = (s or "").strip().replace(",", "")
    if s in MISSING:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(x):
    return int(round(x)) if x is not None else None


def stated_dp(s):
    u"""Decimal places the filer actually printed — the tolerance's basis."""
    s = (s or "").strip()
    return len(s.split(".")[1]) if "." in s else 0


def tolerance(dps, n):
    u"""(down, up) allowance for summing n printed percentages.

    Filers do not agree on how to round, and the filings prove both habits
    against their own share counts: most TRUNCATE (小野薬品 prints 1.80 for an
    exact 1.8064, and its 計 row is the truncated sum), a minority round half
    up (Toyota's ten rows sum two hundredths ABOVE its 計). So the window is
    asymmetric — a full last digit per row below the filed total, half a digit
    above it — rather than a symmetric band twice as wide as either habit
    needs.

    The last digit is the coarsest one the filing states, because XBRL drops
    trailing zeros: a printed 96.30 arrives as 0.963 and claims only one
    decimal. Zero values state no precision at all and are ignored. The result
    is clamped to one or two decimals, which is what the form prints.
    """
    real = [d for d in dps if d is not None]
    dp = min(2, max(1, min(real))) if real else 2
    ulp = 10.0 ** -dp
    return n * ulp + 1e-9, n * ulp * 0.5 + 1e-9


def pct_of(raw):
    u"""Fraction as filed -> percent, keeping the filed precision.

    0.0918 (4 dp) -> 9.18 (2 dp). A unit conversion of the filed figure, so
    the stored number still matches what the filing prints.
    """
    v = to_num(raw)
    if v is None:
        return None, 0
    dp = max(0, stated_dp(raw) - 2)
    return round(v * 100.0, dp), dp


def parse_filing(blob):
    u"""Bytes of a type=5 zip -> (majors, categories, filing-level facts)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = [n for n in z.namelist() if "jpcrp030000-asr" in n]
    if not names:
        # Foreign-issuer (jpcrp080000) and 特定 (jpcrp030200) annual reports tag
        # no 株式等の状況 section at all — out of scope, not a failure. Verified
        # against the archive, exactly as board_extract.py records it.
        raise UnsupportedForm(", ".join(sorted(set(
            re.sub(r"-\d+_.*", "", n.split("/")[-1]) for n in z.namelist()
            if n.endswith(".csv"))) or ["no csv in package"]))
    rows = list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-16")),
                           delimiter="\t"))[1:]

    majors = defaultdict(dict)        # row no -> fields
    stray = set()                     # member contexts we could not number
    filed_total = {}                  # the register's own 計 row
    cats = defaultdict(lambda: defaultdict(dict))   # share class -> cat -> field
    cat_totals = defaultdict(dict)    # share class -> field
    less_than_unit = {}
    issued, treasury = {}, None

    for r in rows:
        if len(r) < 9:
            continue
        el, ctx, val = r[0], r[2], r[8]
        member = FILER_NS.sub("", ctx.split("_", 1)[1]) if "_" in ctx else ""
        if el in (MAJOR_NAME, MAJOR_ADDRESS, MAJOR_SHARES, MAJOR_RATIO):
            if member:
                m = MAJOR_MEMBER.match(member)
                if m:
                    field = {MAJOR_NAME: "name", MAJOR_ADDRESS: "address",
                             MAJOR_SHARES: "shares", MAJOR_RATIO: "ratio"}[el]
                    majors[int(m.group(1))][field] = val
                elif "MajorShareholders" in member:
                    # A register row we cannot place: never dropped silently.
                    stray.add(member)
            elif ctx == "CurrentYearInstant" and el in (MAJOR_SHARES, MAJOR_RATIO):
                filed_total["shares" if el == MAJOR_SHARES else "ratio"] = val
        elif el in CAT_BY_ELEMENT and ctx.startswith("CurrentYearInstant"):
            key, field = CAT_BY_ELEMENT[el]
            cats[member or "unclassified"][key][field] = val
        elif el in CAT_TOTALS and ctx.startswith("CurrentYearInstant"):
            cat_totals[member or "unclassified"][CAT_TOTALS[el]] = val
        elif el == LESS_THAN_UNIT and ctx.startswith("CurrentYearInstant"):
            less_than_unit[member or "unclassified"] = val
        elif el.startswith(ISSUED_MARK) and ctx.startswith("FilingDateInstant"):
            issued[ctx] = val
        elif el == TREASURY_MARK and ctx == "CurrentYearInstant":
            treasury = val

    facts = {
        "filed_total": filed_total,
        "stray": sorted(stray),
        "less_than_unit": less_than_unit,
        # Shares in issue is stated per share class as at the FILING date. The
        # ordinary-share context is the denominator a register ratio uses; the
        # bare context is the all-classes total, and THAT is what a holder's
        # position has to be tested against — TEPCO's rescue fund and Chiyoda's
        # Mitsubishi Corp stake are held largely in preferred shares, and
        # measuring them against the ordinary count alone reports a holder
        # owning more of a company than exists.
        "issued": (issued.get("FilingDateInstant_OrdinaryShareMember")
                   or issued.get("FilingDateInstant")),
        "issued_all": (issued.get("FilingDateInstant")
                       or (sum(int(v.replace(",", "")) for v in issued.values()
                               if v.replace(",", "").isdigit()) or None)),
        "treasury": treasury,
    }
    return majors, (cats, cat_totals), facts


SCHEMA_SQL = """
        -- 大株主の状況: the top of the shareholder REGISTER, as filed. Ratios
        -- are of shares in issue EXCLUDING treasury -- the filing's own
        -- denominator, and not the same one as eq_own_category.pct.
        -- holder_kind and beneficiary_raw are OURS (derived), everything else
        -- is as filed.
        CREATE TABLE IF NOT EXISTS eq_major_shareholders (
            doc_id VARCHAR, rank INTEGER, name_raw VARCHAR, name_base VARCHAR,
            name_key VARCHAR,
            account_raw VARCHAR, address_raw VARCHAR, shares BIGINT,
            ratio_pct DOUBLE, holder_kind VARCHAR, beneficiary_raw VARCHAR,
            holder_edinet_code VARCHAR, holder_sec_code VARCHAR,
            match_status VARCHAR, beneficiary_edinet_code VARCHAR,
            beneficiary_sec_code VARCHAR);
        -- 所有者別状況: the whole register by investor category, per share
        -- class. Percentages are of ALL issued shares (treasury included, as
        -- its own line in the filing's 単元 arithmetic).
        CREATE TABLE IF NOT EXISTS eq_own_category (
            doc_id VARCHAR, share_class VARCHAR, category VARCHAR,
            category_en VARCHAR, shareholders BIGINT, units BIGINT,
            pct DOUBLE);
        CREATE TABLE IF NOT EXISTS eq_own_filings (
            doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
            filer_name VARCHAR, period_end DATE, filed_date DATE, sha256 VARCHAR,
            parser_version VARCHAR, status VARCHAR, detail VARCHAR,
            majors_rows INTEGER, majors_ratio_sum_pct DOUBLE,
            majors_ratio_filed_pct DOUBLE, majors_shares_sum BIGINT,
            nominee_rows INTEGER, nominee_ratio_pct DOUBLE,
            matched_rows INTEGER, issued_shares BIGINT,
            issued_shares_all_classes BIGINT, treasury_shares BIGINT,
            share_classes INTEGER, shareholders_total BIGINT,
            foreign_pct DOUBLE, financial_institutions_pct DOUBLE,
            other_corporations_pct DOUBLE, individuals_pct DOUBLE,
            securities_firms_pct DOUBLE, government_pct DOUBLE,
            shares_less_than_unit BIGINT);
"""

AGG_COLS = ["majors_rows", "majors_ratio_sum_pct", "majors_ratio_filed_pct",
            "majors_shares_sum", "nominee_rows", "nominee_ratio_pct",
            "matched_rows", "issued_shares", "issued_shares_all_classes",
            "treasury_shares", "share_classes",
            "shareholders_total", "foreign_pct", "financial_institutions_pct",
            "other_corporations_pct", "individuals_pct", "securities_firms_pct",
            "government_pct", "shares_less_than_unit"]

NOMINEE_KINDS = ("trust_bank_nominee", "foreign_nominee")


def build(doc_id, majors, categories, facts, resolve):
    u"""Facts -> (register rows, category rows, aggregates, gate problems)."""
    problems = []
    cats, cat_totals = categories

    # ---- the register ------------------------------------------------------
    rows, ratio_sum, shares_sum, dps = [], 0.0, 0, []
    nominee_rows, nominee_ratio, matched = 0, 0.0, 0
    issued = to_int(to_num(facts.get("issued")))
    issued_all = to_int(to_num(facts.get("issued_all"))) or issued
    for rank in sorted(majors):
        f = majors[rank]
        name_raw = (f.get("name") or "").strip()
        if not name_raw:
            continue                      # a numbered context with no name
        base, account = split_account(name_raw)
        kind, beneficiary = classify_holder(name_raw, base, account)
        ratio, dp = pct_of(f.get("ratio"))
        shares = to_int(to_num(f.get("shares")))
        if ratio is not None:
            ratio_sum += ratio
            if ratio:                      # a zero states no precision
                dps.append(dp)
        if shares:
            shares_sum += shares
            if issued_all and shares > issued_all:                          # G4
                problems.append("G4 %s holds %d of %d shares in issue"
                                % (name_raw[:20], shares, issued_all))
        if kind in NOMINEE_KINDS:
            nominee_rows += 1
            nominee_ratio += ratio or 0.0
        # A person is never entity-matched. EDINET does issue codes to
        # individuals who file large-holding reports, so a name lookup WOULD
        # return a hit — and a name collision would then attribute one
        # person's holdings to another. The register gives no key that makes
        # that safe, so the link is not attempted.
        mstat, ec, sc = (("individual", None, None) if kind == "individual"
                         else resolve(for_matching(base)))
        if mstat == "matched":
            matched += 1
        bec = bsc = None
        if beneficiary:
            _bst, bec, bsc = resolve(beneficiary)
        # A grouping key for holders that never resolved to an entity code.
        # The same company is written half-width by one filer and full-width by
        # another (光通信ＫＫ投資事業有限責任組合 / 光通信KK…), which split one
        # holder into two rows of every ranking; norm() folds width, spacing
        # and old character forms exactly as the entity resolver does.
        rows.append((doc_id, rank, name_raw, base, norm(base), account,
                     (f.get("address") or "").strip() or None, shares, ratio,
                     kind, beneficiary, ec, sc, mstat, bec, bsc))

    filed_ratio, filed_dp = pct_of((facts.get("filed_total") or {}).get("ratio"))
    ratio_sum = round(ratio_sum, 6) if rows else None
    if rows and filed_ratio is not None and ratio_sum is not None:
        # Each row is printed rounded or truncated by the filer, so the sum of
        # the rows is checked against the filer's own 計 row inside the window
        # that its own printing precision allows — see tolerance().
        down, up = tolerance(dps or [filed_dp], len(dps))
        if not (filed_ratio - down) <= ratio_sum <= (filed_ratio + up):      # G3
            problems.append("G3 register ratios sum to %.4f%% vs filed %.4f%%"
                            % (ratio_sum, filed_ratio))
    if facts.get("stray"):
        problems.append("register rows in unrecognised contexts: %s"
                        % ", ".join(facts["stray"][:3]))

    # ---- the register by category -----------------------------------------
    cat_rows = []
    for cls in sorted(cats):
        unit_sum, pct_sum, pct_dps = 0, 0.0, []
        for key, en in [(k, CATEGORY_EN[k]) for k, _e, _c, _u, _p in CATEGORIES]:
            f = cats[cls].get(key) or {}
            if not f:
                continue
            units = to_int(to_num(f.get("units")))
            pct, dp = pct_of(f.get("pct"))
            if units:
                unit_sum += units
            if pct is not None:
                pct_sum += pct
                if pct:                    # a zero states no precision
                    pct_dps.append(dp)
            cat_rows.append((doc_id, cls, key, en,
                             to_int(to_num(f.get("shareholders"))), units, pct))
        tot = cat_totals.get(cls) or {}
        filed_units = to_int(to_num(tot.get("units")))
        if filed_units is not None and unit_sum and unit_sum != filed_units:  # G1
            problems.append("G1 %s unit counts sum to %d vs filed %d"
                            % (cls, unit_sum, filed_units))
        if pct_dps:                                                          # G2
            down, up = tolerance(pct_dps, len(pct_dps))
            if not (100.0 - down) <= pct_sum <= (100.0 + up):
                problems.append("G2 %s category percentages sum to %.3f%%"
                                % (cls, pct_sum))

    # The headline splits come from ordinary shares where the filing separates
    # classes: a preferred class has its own register and mixing them would
    # invent a number the filing never states.
    head = ("OrdinaryShareMember" if "OrdinaryShareMember" in cats
            else (sorted(cats)[0] if cats else None))
    hc = cats.get(head, {})

    def cat_pct(*keys):
        vals = [pct_of((hc.get(k) or {}).get("pct"))[0] for k in keys]
        vals = [v for v in vals if v is not None]
        return round(sum(vals), 4) if vals else None

    agg = {
        "majors_rows": len(rows) or None,
        "majors_ratio_sum_pct": ratio_sum,
        "majors_ratio_filed_pct": filed_ratio,
        "majors_shares_sum": shares_sum or None,
        "nominee_rows": nominee_rows if rows else None,
        "nominee_ratio_pct": round(nominee_ratio, 4) if nominee_rows else None,
        "matched_rows": matched if rows else None,
        "issued_shares": issued,
        "issued_shares_all_classes": issued_all,
        "treasury_shares": to_int(to_num(facts.get("treasury"))),
        "share_classes": len(cats) or None,
        "shareholders_total": to_int(to_num((cat_totals.get(head) or {}).get("shareholders"))),
        "foreign_pct": cat_pct("foreign_institutions", "foreign_individuals"),
        "financial_institutions_pct": cat_pct("financial_institutions"),
        "other_corporations_pct": cat_pct("other_corporations"),
        "individuals_pct": cat_pct("individuals_and_others"),
        "securities_firms_pct": cat_pct("financial_service_providers"),
        "government_pct": cat_pct("government"),
        "shares_less_than_unit": to_int(to_num((facts.get("less_than_unit") or {}).get(head))),
    }
    return rows, cat_rows, agg, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every archived filer")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--docs", help="comma-separated docIDs — re-extract just these")
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
    tiers = build_index(codelist)
    listed = {d[u"ＥＤＩＮＥＴコード"] for d in codelist if d[u"上場区分"] == u"上場"}

    # Window first, then discovery: knowing `since` lets the bucket listing
    # seek to it instead of paging five years of keys.
    since, have = (incremental_window(args.db, "shareholder-register", "eq_own_filings")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, "shareholder-register"))
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
    # Which registration of a company actually filed, and over which years —
    # the tie-break when one name resolves to two EDINET registrations. It
    # comes from the holdings extractor's table, which a scratch DB used to
    # verify a parser change need not have.
    try:
        evidence = filing_evidence(con)
    except duckdb.CatalogException:
        evidence = {}

    def resolve(nm):
        u"""Holder name -> (match_status, edinet_code, sec_code).

        The same resolver the holdings extractor uses, so a company resolves to
        one entity from either side of the graph. Strongest key that names
        exactly one company wins; an ambiguous key resolves to nothing.
        """
        if not nm:
            return "unmatched", None, None
        for tier, key in zip(tiers, (norm(nm), core_name(nm), base_name(nm))):
            entries = tier.get(key)
            if not entries:
                continue
            hit = pick(entries, evidence, None)
            if hit:
                return "matched", hit[0], hit[1] or None
        return ("foreign" if is_foreign(nm) else "unmatched"), None, None

    def fetch_and_parse(t):
        doc_id, rec, m = t
        sha = None
        try:
            blob = src.read_zip(doc_id, rec["date"])
            # Hash the bytes actually parsed, so every row's provenance is
            # verifiable against the archive rather than trusting a manifest.
            sha = hashlib.sha256(blob).hexdigest()
            return t, parse_filing(blob), sha, None
        except UnsupportedForm as e:
            return t, None, sha, ("unsupported_form",
                                  "form tags no ownership section: %s" % e)
        except Exception as e:                                       # noqa: BLE001
            return t, None, sha, ("failed", "%s: %s" % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    tot_major = tot_cat = tot_matched = tot_nominee = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha, err = fut.result()
            done += 1
            if done % 1000 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            base = [doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"), m.get("periodEnd") or None,
                    rec["date"], sha or rec.get("sha256"), PARSER_VERSION]
            for t in ("eq_major_shareholders", "eq_own_category", "eq_own_filings"):
                con.execute("DELETE FROM %s WHERE doc_id = ?" % t, [doc_id])
            if err:
                st, detail = err
                stats[st] += 1
                con.execute("INSERT INTO eq_own_filings VALUES (%s)"
                            % ",".join(["?"] * (10 + len(AGG_COLS))),
                            base + [st, detail] + [None] * len(AGG_COLS))
                continue
            majors, categories, facts = parsed
            rows, cat_rows, agg, problems = build(doc_id, majors, categories,
                                                  facts, resolve)
            if not rows and not cat_rows:
                status, detail = "no_ownership_tables", "neither table is tagged"
            elif problems:
                status, detail = "partial", "; ".join(problems[:4])
            else:
                status, detail = "clean", None
            stats[status] += 1
            con.execute("INSERT INTO eq_own_filings VALUES (%s)"
                        % ",".join(["?"] * (10 + len(AGG_COLS))),
                        base + [status, detail] + [agg[c] for c in AGG_COLS])
            if rows:
                con.executemany("INSERT INTO eq_major_shareholders VALUES (%s)"
                                % ",".join(["?"] * 16), rows)
                tot_major += len(rows)
                tot_matched += agg["matched_rows"] or 0
                tot_nominee += agg["nominee_rows"] or 0
            if cat_rows:
                con.executemany("INSERT INTO eq_own_category VALUES (?,?,?,?,?,?,?)",
                                cat_rows)
                tot_cat += len(cat_rows)
    con.close()
    record_run(args.db, "shareholder-register", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)
    print("filings: %s" % dict(stats))
    print("rows: register %d (%d entity-matched, %d nominee), categories %d"
          % (tot_major, tot_matched, tot_nominee, tot_cat))
    print("wrote", os.path.normpath(args.db))


if __name__ == "__main__":
    main()
