# -*- coding: utf-8 -*-
"""M3/M4 — production cross-shareholding extractor.

Reads annual-report CSV packages (EDINET type=5), extracts the 政策保有株式
tables, entity-matches held names against the EDINET code list, validates
against the filings' own tagged totals, and writes the eq_* tables into a
dedicated DuckDB file (separate from the macro product's DB).

One writer at a time, and DuckDB counts a *reader* as a conflicting lock: a
local `uvicorn app.main:app` holding its read-only connection makes this script
fail on connect. Stop the local server before extracting, then start it again —
production never hits this because the API and the extractor are separate
processes on separate boxes.

Two sources. `--source local` reads the laptop archive; `--source s3` reads the
cloud bucket, which is the authoritative one (the laptop copy is a partial
backup). Multi-year needs no schema change: each filing is its own doc_id with
its own period_end, so five fiscal years is simply five times the filings.

Usage:
    python extract.py                                 # financial sector, local
    python extract.py --all                           # every archived filer, local
    python extract.py --all --source s3               # FULL UNIVERSE, 5 years
    python extract.py --all --source s3 --workers 16  # tune fetch concurrency

Inherits the M1 lessons (see m1/README.md): Deemed-table context collision,
element-name variants by filer structure, purpose/reciprocity element families,
filing-era names needing an alias map, missing values stay missing.
Python 3.9.
"""
import argparse
import csv
import datetime as _dt
import glob
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
# The measurement-basis vocabulary is defined once, in the serving code, and
# projected into the database from there — so the table and the API can never
# disagree about what "carrying amount" means.
sys.path.insert(0, os.path.join(HERE, ".."))
from app import basis  # noqa: E402

# Both paths are environment-overridable because these extractors now run in
# two places: on the laptop against the local archive, and inside the serving
# container against the bucket, where the only writable disk is the mounted
# volume. Defaults are the laptop's, so nothing about a local run changes.
# The raw archive is NOT in the image: it is 9.4GB of zips on the laptop and a
# bucket in production, so the default points back out of the repo's app
# directory to where a laptop keeps it. In the container EDINET_ARCHIVE_ROOT
# names a directory on the mounted volume and only the daily-list cache and
# the bucket listing ever land there.
ARCHIVE = os.environ.get("EDINET_ARCHIVE_ROOT",
                         os.path.join(HERE, "..", "..", "equity", "data", "raw", "edinet"))
CODELIST = os.path.join(HERE, "m1", "EdinetcodeDlInfo.csv")
DB_PATH = os.environ.get(
    "EQUITY_DB_PATH", os.path.join(HERE, "..", "data", "equity.duckdb"))
PARSER_VERSION = "m6-0"

FIN_INDUSTRIES = ("銀行業", "保険業", "証券", "その他金融業")

DETAIL_MARK = "DetailsOfSpecifiedInvestmentEquitySecurities"
VARIANTS = [("SecondLargestHoldingCompany", "second_largest"),
            ("LargestHoldingCompany", "largest"),
            ("ReportingCompany", "reporting")]

# The purpose-change tables (M5). A filer that moves a holding out of the
# policy bucket and into 純投資目的 keeps the shares but drops the row from the
# named table, so a naive read scores the reclassification as a disposal. The
# filings tag the move explicitly, in both directions, and these are the
# element families that carry it. Both use Row<N>Member contexts that collide
# with the holdings table's own row numbers — the same trap as Deemed (§4.3) —
# so they are keyed separately and never merged into `tables`.
RECLASS = [("ReclassifiedFromHeldForPurposesOtherThanPureInvestmentToHeldForPureInvestment", "to_pure"),
           ("ReclassifiedFromHeldForPureInvestmentToHeldForPurposesOtherThanPureInvestment", "to_policy")]

# Footnotes to the named table. Row<N>Member here numbers the *footnote*, not
# the holding — (注)1, (注)2, … — so they are a per-table numbered list, not a
# per-position field. `FootnotesDeemedHoldings…` is a different table and must
# not be picked up.
FOOTNOTE_MARK = "FootnotesSpecifiedInvestmentShares"

# The filing's own count of issues that moved and the yen that changed hands.
# This is the honest denominator for "did they actually sell?": sale proceeds
# against positions that merely left the table.
FLOW_FIELDS = [("NumberOfIssuesWhoseNumberOfSharesIncreased", "issues_increased"),
               ("TotalAcquisitionCostForIncreasedShares", "acquisition_cost_yen"),
               ("NumberOfIssuesWhoseNumberOfSharesDecreased", "issues_decreased"),
               ("TotalSaleAmountForDecreasedShares", "sale_proceeds_yen")]

ISSUED_MARK = "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
TREASURY_MARK = "TotalNumberOfSharesHeldTreasurySharesEtc"

# The filing's own total carrying amount and issue count for the whole policy
# bucket, listed and unlisted, per disclosing entity (M6). The named table
# lists only the largest issues, so its sum is not the filer's policy total:
# measured over seven big financials the named rows carry 74.5% of the tagged
# total, from 55.5% (Mizuho) to 98.5% (Dai-ichi Life). Anything expressed as a
# share of the filer's own balance sheet must use the tagged total.
#
# The issue-count elements exist for the 純投資目的 bucket as well, so the
# purpose guard is load-bearing here; the carrying-amount elements do not
# (checked: 0 pure-investment carrying amounts in 523 annual reports), which
# is why the pre-existing reconciliation gate is unaffected by it.
POLICY_MARK = "PurposesOtherThanPureInvestment"
TOTAL_VALUE_MARK = "CarryingAmountShares"
TOTAL_COUNT_MARK = "NumberOfIssuesShares"

# The denominator for "how big are these holdings relative to the filer".
#
# Filers report equity under one of three standards, each tagging it under its
# own element name. An IFRS or US-GAAP adopter STOPS tagging the JGAAP
# consolidated figure for the current year but leaves the prior years in place
# — so the element is present and merely stale, and a naive read falls through
# to the parent-only figure. For a holding company that is a near-empty shell:
# Sompo read that way shows 90% of equity in policy shares against a true 30%,
# because the shareholdings sit in the operating subsidiary while the equity
# read was the holdco's own. Resolution is therefore ORDERED, and the rung
# used is recorded and displayed — a group figure and a parent-only figure are
# different measures and must never be silently mixed in one column.
#
# JGAAP 純資産 includes non-controlling interests. IFRS and US-GAAP each tag
# both an including-minorities total and a parent-share-only figure, so the
# including-minorities one is preferred to keep the measure comparable, and
# the basis label distinguishes them when only the parent share is tagged —
# a difference the page shows rather than papers over. Measured over 2,460
# annual reports: JGAAP 100%, IFRS parent-share 7.8%, IFRS total equity 0.2%,
# US-GAAP 0.4% combined.
#
# Element names are matched WITHOUT their namespace, which is load-bearing:
# Ajinomoto tags TotalEquityIFRS… in a filer-specific extension namespace
# (jpcrp030000-asr_E00436-000), not in a standard taxonomy prefix. Matching
# on the qualified name would have missed it and dropped that filing to the
# parent-only rung — ¥332bn instead of ¥844bn, a ratio overstated 2.5x.
EQUITY_SOURCES = [
    ("NetAssetsSummaryOfBusinessResults",
     "CurrentYearInstant", "jgaap_consolidated"),
    ("TotalEquityIFRSSummaryOfBusinessResults",
     "CurrentYearInstant", "ifrs_consolidated"),
    ("EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
     "CurrentYearInstant", "ifrs_consolidated_excl_nci"),
    ("EquityIncludingPortionAttributableToNonControllingInterestUSGAAPSummaryOfBusinessResults",
     "CurrentYearInstant", "usgaap_consolidated"),
    ("EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
     "CurrentYearInstant", "usgaap_consolidated_excl_nci"),
    # Equity is a balance, so it belongs in an instant context — but some
    # filers stamp it as a duration. Tosei (8923) tags its consolidated IFRS
    # equity at CurrentYearDuration in all five of its filings, and reading
    # only the instant contexts dropped it to the parent-only rung: ¥92.5bn
    # against a true consolidated ¥102.8bn. Tried only after every instant
    # context has missed, and always ahead of the parent-only fallback,
    # because a mis-stamped group figure still beats a different measure.
    ("TotalEquityIFRSSummaryOfBusinessResults",
     "CurrentYearDuration", "ifrs_consolidated"),
    ("EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
     "CurrentYearDuration", "ifrs_consolidated_excl_nci"),
    ("NetAssetsSummaryOfBusinessResults",
     "CurrentYearDuration", "jgaap_consolidated"),
    ("NetAssetsSummaryOfBusinessResults",
     "CurrentYearInstant_NonConsolidatedMember", "parent_only"),
]
ASSETS_SOURCES = [
    ("TotalAssetsSummaryOfBusinessResults",
     "CurrentYearInstant", "jgaap_consolidated"),
    ("TotalAssetsIFRSSummaryOfBusinessResults",
     "CurrentYearInstant", "ifrs_consolidated"),
    ("TotalAssetsUSGAAPSummaryOfBusinessResults",
     "CurrentYearInstant", "usgaap_consolidated"),
    ("TotalAssetsIFRSSummaryOfBusinessResults",
     "CurrentYearDuration", "ifrs_consolidated"),
    ("TotalAssetsSummaryOfBusinessResults",
     "CurrentYearDuration", "jgaap_consolidated"),
    ("TotalAssetsSummaryOfBusinessResults",
     "CurrentYearInstant_NonConsolidatedMember", "parent_only"),
]
FINANCIAL_KEYS = {(el, ctx) for el, ctx, _ in EQUITY_SOURCES + ASSETS_SOURCES}


def resolve_financial(found, sources):
    """First rung of `sources` the filing actually tags -> (yen, basis).

    Ordered, not best-effort: see EQUITY_SOURCES. Equity can legitimately be
    negative, so the digit test allows a leading minus."""
    for el, ctx, basis in sources:
        val = found.get((el, ctx))
        if val is not None and val.lstrip("-").isdigit():
            return int(val), basis
    return None, None


def reclass_field_of(local):
    """Field label for a row in one of the purpose-change tables."""
    if local.startswith(("NameOfSecurities", "SecurityName")):
        return "name"
    if local.startswith(("NumberOfSharesHeld", "NumberOfSharesAtTimeOfClassificationChange")):
        return "shares"
    if local.startswith("BookValue"):
        return "book_value"
    if local.startswith("FiscalYearOfChange"):
        return "fy_of_change"
    if local.startswith("ReasonForChange"):
        return "reason"
    if local.startswith("PolicyOnHoldingOrSale"):
        return "policy"
    return None


def share_class_of(local):
    """Listed vs unlisted. Order matters: the listed element name ends in
    …SharesOtherThanThoseNotListed and so also contains 'SharesNotListed'."""
    if "SharesOtherThanThoseNotListed" in local:
        return "listed"
    if "SharesNotListed" in local:
        return "unlisted"
    return None


def variant_of(local):
    for suffix, label in VARIANTS:
        if suffix in local:
            return label
    return "unspecified"


def field_of(local):
    for prefix, label in [("NameOfSecurities", "name"),
                          ("NumberOfSharesHeld", "shares"),
                          ("BookValue", "book_value"),
                          ("PurposeOfShareholding", "purpose"),
                          ("WhetherIssuer", "reciprocal")]:
        if local.startswith(prefix):
            return label
    return None


# ---- entity naming (M1 lessons) -------------------------------------------
LEGAL = re.compile(r"(株式会社|株式會社|（株）|\(株\)|ホールディングス|グループ本社)")
# footnote markers filers glue onto names: (※5), （注）３, （注４）, (注)4 …
FOOTNOTE = re.compile(r"[（(]\s*(?:※|注)?\s*[0-9０-９]+\s*[）)]|[（(]注[）)][0-9０-９]*")
ALIASES = {  # filing-era base name -> current registered base name (eq_name_map seed)
    # NOTE: hand-curation works at sector scale; M4 (3,829 filers) needs a
    # systematic rename feed keyed on securities codes instead.
    "日本碍子": "NGK",
    "大阪ガス": "大阪瓦斯",
    "パナソニックHD": "パナソニック",
    "第一生命": "第一ライフグループ",
    "サッポロ": "サッポロビール",
    "南海電気鉄道": "NANKAI",
    "上新電機": "Joshin",
    "大和冷機工業": "だいわ",
}
KYUJITAI = str.maketrans("會條檢國櫻眞壽鐵髙﨑", "会条検国桜真寿鉄高崎")


# The cover page states the filer's own English name (【英訳名】). EDINET's
# filer registry leaves that field blank for roughly one listed filer in ten —
# Murata Manufacturing among them — so the filing is both the fuller source and
# the as-filed one.
NAME_EN_MARK = "CompanyNameInEnglishCoverPage"

# Filers append Japanese annotations to it: a former English name, a rename
# note, a footnote marker. Everything from the first Japanese SCRIPT character
# on is annotation. Only kana and ideographs count. Two characters are
# deliberately NOT cut on, because filers use them inside the English name
# itself: the full-width space (ＴＨＥ　ＳＨＩＧＡ　ＢＡＮＫ，ＬＴＤ．) and the
# middle dot U+30FB (Ａｉ・Ｐａｒｔｎｅｒｓ, ＧＯＬＦ・ＤＯ) — cutting on those
# left "ＴＨＥ" and "ＧＯＬＦ".
JAPANESE_RUN = re.compile(u"[぀-ゟ゠-ヺー-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")

# Some filers state no English name by writing a rule of dashes rather than
# leaving the field empty. That is "not stated", not a name.
DASHES_ONLY = re.compile(u"^[-‐‑‒–—―─－\\s]+$")


def clean_english_name(value):
    """The English name alone, or None if the filer stated none."""
    if not value:
        return None
    import html
    v = html.unescape(value).strip().strip('"')   # filings carry &amp; etc.
    if v in (u"－", u"-", u"—"):
        return None
    m = JAPANESE_RUN.search(v)
    if m:
        v = v[:m.start()]
    v = v.strip().strip(u"（(").strip()
    if not v or DASHES_ONLY.match(v):
        return None
    return latin(v) or v


def sec_code_of(meta):
    u"""EDINET document metadata -> a security code, or None.

    EDINET's `secCode` is a five-digit field whose first four digits are the
    exchange ticker: Toyota files "72030" and is 7203. A filer with no listed
    security gets a placeholder rather than an empty field, and "0000" then
    became a code you could look up -- one unlisted chemical company's facility
    table was served under it, and a query for the plainly invalid code "0000"
    came back with data.

    A code that is all zeros is EDINET saying "none", so it reads as None. No
    other shape is rejected: "130A" is a real ticker under the current numbering
    and a stricter test would drop it.
    """
    code = ((meta or {}).get("secCode") or "").strip()[:4]
    return code if code and code.strip("0") else None


def latin(value):
    u"""A published English name in half-width letters, or None.

    EDINET's registry and its cover pages both carry English names typed in
    full-width Latin -- ＤＯＭＹ ＣＯ.,ＬＴＤ. -- because the form is a Japanese one.
    NFKC maps those to the same letters the company prints on its own English
    materials. This is a change of character width, not a translation: no name
    is invented, romanized or looked up. A value that still holds Japanese
    script after the fold was never an English name and comes back None, with
    the one exception of the katakana middle dot, which a handful of companies
    (M・H GROUP, KAPPA・CREATE) genuinely print inside their English name.
    """
    if not value:
        return None
    v = unicodedata.normalize("NFKC", value).replace(u"　", " ")
    v = re.sub(r"\s+", " ", v).strip()
    if not v or DASHES_ONLY.match(v):
        return None
    if JAPANESE_RUN.search(v.replace(u"・", "")):
        return None
    return v


def fill_entity_english(con):
    u"""Give an entity its own filed English name where the registry has none.

    EDINET's code list leaves 提出者名（英字） empty for 7,000 filers, 400 of
    them listed. But a company that files an annual report prints its English
    name on the cover of it, and `eq_filings.filer_name_en` already holds what
    it printed. Preferring the registry and falling back to the cover page
    keeps the rule intact -- an English name is the company's own published
    one, or it is absent -- while recovering nearly every listed company.

    Runs after eq_filings is rewritten, so it reads this run's cover pages.
    """
    con.execute("""
        UPDATE eq_entities AS e
           SET name_en = (SELECT max(f.filer_name_en) FROM eq_filings f
                           WHERE f.edinet_code = e.edinet_code
                             AND f.filer_name_en IS NOT NULL
                             AND trim(f.filer_name_en) <> '')
         WHERE e.name_en IS NULL OR trim(e.name_en) = ''
    """)


def norm(s):
    import html
    s = html.unescape(s or "")               # filings carry &amp; etc.
    s = unicodedata.normalize("NFKC", s).strip()
    s = FOOTNOTE.sub("", s)
    return s.replace(" ", "").replace("　", "").translate(KYUJITAI)


# Two strengths of stripping, and the order between them is the whole point.
# CORP_FORM removes only the legal form (株式会社, ㈱), which filers write in any
# position and which never distinguishes two companies. LEGAL additionally
# removes ホールディングス / グループ本社, which DOES distinguish them:
# ヤマトホールディングス (9064, 360mn shares) and 株式会社ヤマト (1967, 27mn
# shares) are unrelated companies that collapse to the same base key. Matching
# on the base key alone put Toyota's Yamato Holdings stake against the
# construction firm's share count and published 25.7% for a 1.8% stake. So the
# base key is the LAST resort, used only where it resolves to one company.
CORP_FORM = re.compile(u"(株式会社|株式會社|（株）|\\(株\\))")


def core_name(s):
    """Name less its legal form. Keeps ホールディングス — a holdco is not its opco."""
    return CORP_FORM.sub("", norm(s))


def base_name(s):
    b = LEGAL.sub("", norm(s))
    b = ALIASES.get(b, b)
    return LEGAL.sub("", b)


def is_foreign(name):
    if not re.search(r"[぀-ヿ一-鿿]", name):
        return True
    return bool(re.search(r"(有限公司|股份有限公司|銀行有限公司)", name))


def load_codelist():
    text = open(CODELIST, "rb").read().decode("cp932")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[1]
    return [dict(zip(header, r)) for r in rows[2:] if len(r) == len(header)]


def build_index(codelist):
    """Three keyed views of the registry, strongest first.

    Each tier maps a key to the set of registry entries that produce it, so the
    resolver can tell "one company" from "several companies that happen to
    normalise alike" instead of silently taking whichever the registry listed
    first. A tier that resolves to more than one EDINET code is no evidence at
    all and the resolver falls through it.
    """
    tiers = [{}, {}, {}]                     # exact · legal-form-stripped · base
    for d in codelist:
        nm = d.get("提出者名", "")
        entry = (d.get("ＥＤＩＮＥＴコード", ""),
                 (d.get("証券コード", "") or "")[:4],
                 d.get("上場区分", ""), nm)
        if not entry[0]:
            continue
        for tier, key in zip(tiers, (norm(nm), core_name(nm), base_name(nm))):
            if key:
                tier.setdefault(key, []).append(entry)
    return tiers


LISTED = "上場"


def pick(entries, evidence=None, when=None):
    """One registry entry from a tier's candidates, or None if it is ambiguous.

    Three things can make a tier's key match several registry rows, and only
    one of them is a real ambiguity:

    1. A company re-registers and EDINET leaves the old row behind as a bare
       entry with no listing status, which never files. Drop those.
    2. Two unrelated companies share a name — 株式会社アルファ is both 3434
       (metal products) and 4760 (services); 株式会社バッファロー is both 6676
       and 3352. Two listed companies with different securities codes are two
       companies, and no name-based rule can choose between them. Return
       nothing: a suppressed percentage is honest, a coin-flipped one is not.
    3. The same company holds two registrations covering different years —
       ＪＳＲ株式会社 filed under E01003 through FY2024 and E39283 after. Pick
       the registration that actually filed over this holding's period, since
       that is the one that can supply a denominator at all.
    """
    if len(set(e[0] for e in entries)) == 1:
        return entries[0]

    real = [e for e in entries if e[2]]
    if len(set(e[0] for e in real)) == 1:
        return real[0]
    if real:
        entries = real

    listed = [e for e in entries if e[2] == LISTED and e[1]]
    if len(set(e[1] for e in listed)) > 1:
        return None
    if len(set(e[0] for e in listed)) == 1:
        return listed[0]

    if evidence:
        covering = [e for e in entries if _covers(evidence.get(e[0]), when)]
        if len(set(e[0] for e in covering)) == 1:
            return covering[0]
        ranked = sorted(entries, key=lambda e: -(evidence.get(e[0]) or (0,))[0])
        if (evidence.get(ranked[0][0]) or (0,))[0]:
            return ranked[0]
    return None


def _covers(span, when):
    """Did this registration file over `when`? (count, first, last) or None.

    Compared as ISO strings: the span comes from DuckDB as `datetime.date`
    while `when` is the period end off the EDINET daily list, which is a
    string. ISO dates order lexicographically exactly as they order
    chronologically, so str() on both sides is a coercion, not a heuristic.
    """
    if not span or not when or not span[0]:
        return False
    return str(span[1]) <= str(when) <= str(span[2])


def filing_evidence(con):
    """edinet_code -> (filings held, first period, last period).

    Registry rows say who exists; this says who actually filed, which is what
    decides between two registrations of one company.
    """
    return dict((r[0], (r[1], r[2], r[3])) for r in con.execute(
        "SELECT edinet_code, count(DISTINCT period_end), min(period_end), max(period_end) "
        "FROM eq_filings WHERE edinet_code IS NOT NULL GROUP BY 1").fetchall())


# ---- filing discovery ------------------------------------------------------
# ---- incremental extraction -------------------------------------------------
# Until now every extractor re-read the entire archive on every run: 21k annual
# reports, 3.9k 5% filings, and so on, each one downloaded and re-parsed to
# produce rows identical to the ones already stored. That is affordable as an
# occasional offline rebuild and impossible as a nightly job, which is why the
# equity data went four weeks stale in August 2026 while the archive itself
# kept growing. `--new-only` makes a daily run proportional to a day of
# filings instead of five years of them.
#
# The watermark lives in the database rather than on disk, so it travels with
# the file: ship the DB as a seed, and the container it lands in knows exactly
# where extraction got to. It is deliberately NOT a per-document ledger — a
# date plus a lookback is enough, and it stays small.
STATE_DDL = """
CREATE TABLE IF NOT EXISTS eq_extract_runs (
    extractor VARCHAR PRIMARY KEY,
    through_date DATE,
    docs_seen BIGINT,
    parser_version VARCHAR,
    ran_at TIMESTAMP)
"""

# Re-examine this many archive days behind the watermark on every run. EDINET
# back-fills: a document can appear in the bucket days after its filing date
# (a capture that failed and healed on a later trailing-window run), and a
# watermark alone would step straight over it. Ten days costs a few hundred
# already-stored doc_ids to skip in memory and buys immunity to that.
LOOKBACK_DAYS = 10


def incremental_window(db_path, extractor, table, lookback=LOOKBACK_DAYS):
    """(since, have) for a `--new-only` run.

    `since` is the earliest archive date still worth looking at, or None to
    mean "everything" — a database that has never run this extractor, or has
    no rows for it, is rebuilt in full rather than silently half-filled.
    `have` is the doc_ids already stored, so the lookback overlap costs a set
    membership test rather than a download.
    """
    if not os.path.exists(db_path):
        return None, set()
    con = duckdb.connect(db_path, read_only=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        if "eq_extract_runs" not in names or table not in names:
            return None, set()
        row = con.execute(
            "SELECT through_date FROM eq_extract_runs WHERE extractor = ?",
            [extractor]).fetchone()
        if not row or not row[0]:
            return None, set()
        have = {r[0] for r in con.execute(
            "SELECT DISTINCT doc_id FROM \"%s\"" % table).fetchall()}
        if not have:                    # a watermark with no rows behind it is
            return None, set()          # not a resumable state; start over
        since = (row[0] - _dt.timedelta(days=lookback)).isoformat()
        return since, have
    finally:
        con.close()


def record_run(db_path, extractor, through_date, docs_seen, parser_version,
               back_to=None):
    """Stamp how far this extractor has read the archive, forward and back.

    Written only after the extraction loop completes, so a run that dies
    half-way leaves the previous watermarks standing and the next run redoes
    the work rather than skipping it.

    `back_to` is the earliest archive day this extractor has now read whole.
    It is recorded rather than derived from the stored rows, because the two
    drift: a day the run read may hold nothing this extractor keeps, so the
    deepest STORED row can sit above the deepest day READ, and a floor taken
    from the rows then steps over the days in between. That is not a
    theoretical worry — deriving it left eight March days unread in testing.
    """
    if not through_date:
        return
    con = duckdb.connect(db_path)
    try:
        con.execute(STATE_DDL)
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info('eq_extract_runs')").fetchall()}
        if "back_to_date" not in cols:      # additive: an older database gains
            con.execute(                    # the column and keeps its rows
                "ALTER TABLE eq_extract_runs ADD COLUMN back_to_date DATE")
        if back_to is None:                 # forward-only run: leave the floor
            back_to = con.execute(          # exactly where it was
                "SELECT back_to_date FROM eq_extract_runs WHERE extractor = ?",
                [extractor]).fetchone()
            back_to = back_to[0] if back_to else None
        con.execute(
            "INSERT OR REPLACE INTO eq_extract_runs "
            "(extractor, through_date, docs_seen, parser_version, ran_at, "
            " back_to_date) VALUES (?,?,?,?,?,?)",
            [extractor, through_date, docs_seen, parser_version,
             _dt.datetime.now(), back_to])
    finally:
        con.close()


def recorded_floor(db_path, extractor):
    """The earliest archive day this extractor has read whole, or None."""
    if not os.path.exists(db_path):
        return None
    con = duckdb.connect(db_path, read_only=True)
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        if "eq_extract_runs" not in names:
            return None
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info('eq_extract_runs')").fetchall()}
        if "back_to_date" not in cols:
            return None
        row = con.execute(
            "SELECT back_to_date FROM eq_extract_runs WHERE extractor = ?",
            [extractor]).fetchone()
        return row[0].isoformat() if row and row[0] else None
    except Exception:                                        # noqa: BLE001
        return None
    finally:
        con.close()


def seek_key(since):
    """The S3 StartAfter for an incremental window, or None for a full pass."""
    return ("docs/%s" % since) if since else None


# How many archive DAYS a nightly run may read BELOW its own floor, to fill in
# history it was never given. Zero — the default, and what a laptop run does —
# means "forward only", exactly as before.
#
# Why this exists. Every extractor here resumes from a watermark, so it only
# ever walks forward. A database built forward from the day the extractor was
# written is therefore permanently as shallow as its first run, however deep
# the archive behind it is, and nothing complains: the 5% filings sat at
# fourteen weeks with 74,000 filings in the bucket, and the register and the
# financials at one fiscal year with five in the bucket. The alternative —
# extract the history offline and ship the database in the image — is what
# left the equity data four weeks stale in August 2026, because it only
# happens when somebody remembers. This makes depth something the container
# earns on its own, a slice at a time, on the same clock as everything else.
#
# Days, not documents, because the archive is keyed by day and the work per
# day is roughly constant per extractor: about 14 annual reports and 58 5%
# filings a day across the five years held. Forty days a night is therefore
# ~10 minutes of the refresh window for every extractor put together, and
# reaches the floor of a five-year archive in about a month.
CATCH_UP_DAYS = int(os.environ.get("EQUITY_CATCH_UP_DAYS", "0"))


def catch_up_start(since, catch_up_days):
    """StartAfter for the object listing.

    An incremental run seeks straight to its forward window. A catch-up run
    cannot: the days it wants are BELOW that window, so it needs the whole
    listing. That is 182k keys, but EDINET_LISTING_CACHE holds the answer for
    an hour and every extractor in the nightly refresh reuses it, so the cost
    is paid once a night rather than once an extractor.
    """
    return None if catch_up_days else seek_key(since)


def select_pending(filings, since, have, catch_up_days=0, floor=None):
    """Which archived filings this run should process, and the new floor.

    Forward: everything archived on or after `since` that is not already
    stored — the nightly incremental, unchanged.

    Backward: the `catch_up_days` deepest archive days at or below `floor`,
    this extractor's own recorded floor. WHOLE DAYS, so a floor never lands in
    the middle of one, and the floor day itself is re-offered every run on
    purpose: a run that died part-way through it stored some of its documents,
    and if the floor simply stepped past, nothing would come back for the
    rest. Documents already stored cost a set lookup, so re-offering a
    finished day is free and a day holding nothing this extractor keeps costs
    nothing.

    Returns (pending, new_floor). `new_floor` is None when nothing was read
    backward, which tells record_run to leave the recorded floor alone.
    """
    if since is None:
        days = sorted({r["date"] for r in filings.values()}, reverse=True)
        if not catch_up_days:                  # full pass: everything, as before
            return dict(filings), (days[-1] if days else None)
        # COLD START WITH A BUDGET. A database with no watermark for this
        # extractor used to mean "read the whole archive", and start.sh does
        # not bind the port until the refresh returns: that is precisely what
        # took the site down on 2026-09-03, when financials met an empty table
        # and set off through 1,315 daily lists with the door shut. With a
        # catch-up budget the first night takes the newest slice and the
        # nights after it walk backwards, so a cold start costs the same
        # bounded minutes as any other night.
        take = set(days[:catch_up_days])
        first = {d: r for d, r in filings.items() if r["date"] in take}
        print("cold start: no watermark, taking the newest %d archive days "
              "(%d filings); history follows a slice a night"
              % (len(take), len(first)))
        return first, (min(take) if take else None)
    pending = {d: r for d, r in filings.items()
               if r["date"] >= since and d not in have}
    if not catch_up_days or not have:
        return pending, None
    if floor is None:                          # first catch-up on a database
        mine = [r["date"] for d, r in filings.items() if d in have]
        if not mine:
            return pending, None
        floor = min(mine)
    days = sorted({r["date"] for r in filings.values() if r["date"] <= floor},
                  reverse=True)
    take = days[:catch_up_days]
    if not take:
        print("catch-up: nothing below the floor %s — this dataset is now as "
              "deep as the archive" % floor)
        return pending, floor
    take_set = set(take)
    added = 0
    for d, r in filings.items():
        if r["date"] in take_set and d not in have:
            pending[d] = r
            added += 1
    new_floor = min(take)
    print("catch-up: %d archive days below the floor %s, back to %s "
          "(%d filings not yet read)" % (len(take), floor, new_floor, added))
    return pending, new_floor


# An annual report (有価証券報告書, 120) and its correction (訂正有価証券報告書,
# 130). A correction carries the whole corrected report in the original's
# layout and is stored as a filing of its own, beside the original, whose rows
# are never touched; the APIs serve the latest filed for each year. Until
# 2026-10-24 every reader took 120 only, and the ~650 corrections a year from
# listed companies never reached a page.
ANNUAL = ("120", "130")
_ERA = {"令和": 2018, "平成": 1988, "昭和": 1925}
_DAY = r"(?:(\d{4})/(\d{1,2})/(\d{1,2})|(令和|平成|昭和)(\d{1,2}|元)年(\d{1,2})月(\d{1,2})日)"
_PERIOD = re.compile(r"[(（]" + _DAY + r"\s*[－\-~〜―ー]\s*" + _DAY + r"[)）]")
_PARENTS = {}


def _iso(g):
    if g[0]:
        return "%04d-%02d-%02d" % (int(g[0]), int(g[1]), int(g[2]))
    return "%04d-%02d-%02d" % (_ERA[g[3]] + (1 if g[4] == "元" else int(g[4])), int(g[5]), int(g[6]))


def description_period(desc):
    """(start, end) from a filing's description, '…第99期(2025/04/01－2026/03/31)'
    or in era dates, '…(平成29年4月1日－平成30年3月31日)'; None if it names none."""
    m = _PERIOD.search(desc or "")
    if not m:
        return None
    g = m.groups()
    return _iso(g[:7]), _iso(g[7:])


def _parent_periods():
    """docID -> (periodStart, periodEnd) of every annual report in the cached
    daily lists: read once, only for a correction whose description names no
    period and whose original is not among the lists already loaded."""
    if not _PARENTS:
        for f in glob.glob(os.path.join(ARCHIVE, "lists", "*.json")):
            try:
                rows = json.load(open(f)).get("results") or []
            except ValueError:
                continue
            for r in rows:
                if r.get("docTypeCode") == "120" and r.get("periodEnd"):
                    _PARENTS[r["docID"]] = (r.get("periodStart"), r["periodEnd"])
        _PARENTS.setdefault(None, None)
    return _PARENTS


def dated_corrections(meta):
    """Give each correction in `meta` the period EDINET's list leaves blank:
    from its description (6,577 of 6,762 cached corrections; every one checked
    against its original agreed), else from its original (parentDocID). One
    with neither keeps no period, and annual() turns it away."""
    for r in meta.values():
        if r.get("docTypeCode") != "130" or r.get("periodEnd"):
            continue
        got = description_period(r.get("docDescription"))
        if got is None:
            parent = meta.get(r.get("parentDocID")) or {}
            got = ((parent.get("periodStart"), parent["periodEnd"]) if parent.get("periodEnd")
                   else _parent_periods().get(r.get("parentDocID")))
        if got:
            r["periodStart"], r["periodEnd"] = got
    return meta


def annual(m, rec):
    """True for a filing an annual-report reader takes: an annual report, or a
    correction whose period is known (a row with no year would sit nowhere)."""
    kind = m.get("docTypeCode") or rec.get("doc_type")
    if kind == "130" and not m.get("periodEnd"):
        print("  skipped correction %s: no period in its description or original"
              % (m.get("docID") or "?"))
        return False
    return kind in ANNUAL


class LocalSource(object):
    """The laptop archive: manifest.jsonl + whatever daily lists it holds."""

    name = "local"

    def filings(self, start_after=None):
        out = {}
        for line in open(os.path.join(ARCHIVE, "manifest.jsonl"), encoding="utf-8"):
            r = json.loads(line)
            if r.get("status") == "ok" and r.get("doc_type") in ANNUAL and r.get("dl_type") == "5":
                out[r["doc_id"]] = r
        return out

    def list_metadata(self, days=None):
        meta = {}
        for f in glob.glob(os.path.join(ARCHIVE, "lists", "*.json")):
            if days is not None and os.path.basename(f)[:-5] not in days:
                continue
            for r in json.load(open(f)).get("results") or []:
                meta[r["docID"]] = r
        return dated_corrections(meta)

    def read_zip(self, doc_id, date):
        with open(os.path.join(ARCHIVE, "docs", date, doc_id + "_t5.zip"), "rb") as f:
            return f.read()


class S3Source(object):
    """The cloud bucket — authoritative, and the only copy with all 5 years.

    Filings are discovered from the object listing (one pass over docs/, far
    cheaper than reading 128k per-document manifest objects). The daily lists
    carry edinetCode/secCode/periodEnd and are cached locally on first use,
    since every future extractor wants them too.
    """

    name = "s3"

    def __init__(self, workers=12):
        import boto3
        from botocore.config import Config
        self.bucket = os.environ["EDINET_S3_BUCKET"]
        self.c = boto3.client(
            "s3",
            endpoint_url=os.environ["EDINET_S3_ENDPOINT"],
            aws_access_key_id=os.environ["EDINET_S3_KEY_ID"],
            aws_secret_access_key=os.environ["EDINET_S3_SECRET"],
            region_name=os.environ.get("EDINET_S3_REGION", "auto"),
            config=Config(max_pool_connections=workers + 4,
                          retries={"max_attempts": 5, "mode": "standard"}))
        self.workers = workers

    # Listing docs/ is 182k keys over ~180 paginated calls — 26 seconds, and
    # every extractor needs the same answer. Run seven of them back to back on
    # a nightly schedule and three minutes of the window is spent asking the
    # bucket the same question. EDINET_LISTING_CACHE points at a directory
    # where the answer is kept for EDINET_LISTING_TTL seconds (default one
    # hour: comfortably longer than one refresh, far shorter than the gap
    # between two, so a night never reuses the previous night's listing).
    # Unset by default, so a laptop run is exactly as it was.
    def _cache_path(self, prefix, start_after):
        root = os.environ.get("EDINET_LISTING_CACHE")
        if not root:
            return None
        name = prefix.strip("/").replace("/", "-")
        if start_after:
            name += "-from-" + start_after.strip("/").replace("/", "-")
        return os.path.join(root, "keys-%s.txt" % name)

    def _keys(self, prefix, start_after=None):
        """Object keys under `prefix`, optionally starting after `start_after`.

        Document keys are docs/YYYY-MM-DD/DOCID_tN.zip and S3 returns keys in
        lexicographic order, so an incremental run can seek straight to its
        window instead of paging the whole archive. That is the difference
        between 182 round trips and two — 261 seconds and about one — for a
        container in US West reading a bucket in Singapore, and the nightly
        refresh happens with nothing serving, so it is outage time.
        """
        path = self._cache_path(prefix, start_after)
        ttl = int(os.environ.get("EDINET_LISTING_TTL", "3600"))
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    yield line.rstrip("\n")
            return
        keys = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if start_after:
                kw["StartAfter"] = start_after
            if token:
                kw["ContinuationToken"] = token
            r = self.c.list_objects_v2(**kw)
            for o in r.get("Contents") or []:
                keys.append(o["Key"])
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")
        if path:
            # written whole then renamed: a listing truncated by a crash would
            # silently hide filings from every extractor that read it after
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(keys))
            os.replace(tmp, path)
        for k in keys:
            yield k

    def filings(self, start_after=None):
        """doc_id -> {date} for every archived CSV package. Type filtering is
        done later against the daily list, which is the authority on doc type."""
        out = {}
        for key in self._keys("docs/", start_after):
            parts = key.split("/")                     # docs/YYYY-MM-DD/DOCID_t5.zip
            if len(parts) != 3 or not parts[2].endswith("_t5.zip"):
                continue
            out[parts[2][:-len("_t5.zip")]] = {"date": parts[1]}
        return out

    def list_metadata(self, days=None):
        """Daily lists as docID -> record, caching each one on first use.

        `days` narrows it to the archive dates a caller actually needs. The
        full set is 1,300-odd files and ~330MB; an incremental run needs one
        or two of them, and downloading the rest nightly would dwarf the
        extraction it exists to serve.
        """
        cache = os.path.join(ARCHIVE, "lists")
        os.makedirs(cache, exist_ok=True)
        remote = [k for k in self._keys("lists/") if k.endswith(".json")]
        if days is not None:
            remote = [k for k in remote if k.split("/")[-1][:-5] in days]
        todo = [k for k in remote if not os.path.exists(os.path.join(cache, k.split("/")[-1]))]
        if todo:
            print("caching %d daily lists from the bucket…" % len(todo))

            def grab(key):
                blob = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
                path = os.path.join(cache, key.split("/")[-1])
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(blob)
                os.replace(tmp, path)

            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                for i, _ in enumerate(ex.map(grab, todo), 1):
                    if i % 200 == 0:
                        print("  %d/%d" % (i, len(todo)))
        meta = {}
        for key in remote:
            path = os.path.join(cache, key.split("/")[-1])
            try:
                for r in json.load(open(path)).get("results") or []:
                    meta[r["docID"]] = r
            except ValueError:
                continue
        return dated_corrections(meta)

    def read_zip(self, doc_id, date):
        key = "docs/%s/%s_t5.zip" % (date, doc_id)
        return self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()


# ---- per-filing extraction (M1 core, hardened) -----------------------------
def parse_filing(blob):
    """Return (holdings, tagged totals, extra) for a CSV-package zip.

    `extra` carries the M5 additions: the purpose-change tables, the table
    footnotes, the filing's own increased/decreased issue counts and yen, and
    the filer's own issued and treasury share counts (which make it an issuer
    for someone else's ownership-percentage calculation)."""
    with zipfile.ZipFile(io.BytesIO(blob) if isinstance(blob, bytes) else blob) as z:
        members = [m for m in z.namelist() if "jpcrp030000-asr" in m]
        if len(members) != 1:
            raise ValueError("expected 1 jpcrp030000-asr csv, got %d" % len(members))
        text = z.read(members[0]).decode("utf-16")
    body = list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]

    tables = defaultdict(dict)
    name_en = None       # the filer's own English name, off the cover page
    totals = {}          # (variant) -> tagged listed carrying amount, current yr
    reclass = defaultdict(dict)   # (variant, direction, row) -> fields
    notes = {}                    # (variant, row) -> footnote text
    flows = defaultdict(dict)     # (variant, share_class) -> fields
    issued = {}                   # context -> issued shares at fiscal year end
    treasury = None
    pol_totals = defaultdict(dict)  # (variant, share_class) -> total + count
    financials = {}                 # (element, context) -> equity / total assets
    for row in body:
        if len(row) != 9:
            continue
        eid, item, ctx, relyr, cons, pit, unit_id, unit, val = row
        local = eid.split(":")[-1]
        in_row = bool(re.search(r"Row\d+Member", ctx))
        if local == NAME_EN_MARK:
            name_en = clean_english_name(val)
            continue
        recl = next((d for mark, d in RECLASS if mark in local), None)
        if recl:
            # Checked before the holdings branch: these reuse the holdings
            # table's Row<N>Member contexts and must never reach `tables`.
            f = reclass_field_of(local)
            if f and in_row:
                reclass[(variant_of(local), recl, row_no_of(ctx))][f] = val
        elif local.startswith(FOOTNOTE_MARK) and in_row:
            notes[(variant_of(local), row_no_of(ctx))] = val
        elif in_row and "Deemed" not in local:
            f = field_of(local)
            if f and (DETAIL_MARK in local
                      or local.startswith(("PurposeOfShareholding", "WhetherIssuer"))):
                tables[(variant_of(local), ctx)][f] = val
        elif not in_row and (local, ctx) in FINANCIAL_KEYS:
            financials[(local, ctx)] = val
        elif (not in_row and relyr == "当期末" and val.isdigit()
              and POLICY_MARK in local
              and local.startswith((TOTAL_VALUE_MARK, TOTAL_COUNT_MARK))):
            cls = share_class_of(local)
            if cls:
                field = ("book_value_yen" if local.startswith(TOTAL_VALUE_MARK)
                         else "issue_count")
                pol_totals[(variant_of(local), cls)][field] = int(val)
                # The gate below predates the per-class totals and compares the
                # named sum against the listed carrying amount only. Kept
                # byte-for-byte equivalent: never weaken a validation gate.
                if field == "book_value_yen" and cls == "listed":
                    totals[variant_of(local)] = int(val)
        elif not in_row and local.startswith(ISSUED_MARK):
            issued[ctx] = val
        elif not in_row and local == TREASURY_MARK and ctx == "CurrentYearInstant":
            treasury = val
        elif not in_row and ctx == "CurrentYearDuration":
            f = next((lbl for mark, lbl in FLOW_FIELDS if local.startswith(mark)), None)
            cls = share_class_of(local) if f else None
            if cls:
                flows[(variant_of(local), cls)][f] = val

    prior = {(v, row_no_of(c)): f for (v, c), f in tables.items()
             if c.startswith("Prior1YearInstant") and row_no_of(c)}
    holdings = []
    for (variant, ctx), f in sorted(tables.items()):
        if not ctx.startswith("CurrentYearInstant") or "name" not in f:
            continue
        p = prior.get((variant, row_no_of(ctx)), {})
        holdings.append({
            "holder_table": variant, "row": row_no_of(ctx),
            "held_name": f.get("name", ""),
            "shares": f.get("shares", ""), "book_value": f.get("book_value", ""),
            "prior_shares": p.get("shares", ""), "prior_book_value": p.get("book_value", ""),
            "purpose": f.get("purpose", ""), "reciprocal": f.get("reciprocal", ""),
        })

    reclass_rows = []
    for (variant, direction, rno), f in sorted(reclass.items(), key=_reclass_sort):
        if not f.get("name"):
            continue
        # Some filers split the narrative across a reason element and a
        # separate "policy after the change" element; keep both, in order.
        reason = " ".join(x for x in (f.get("reason"), f.get("policy")) if x)
        reclass_rows.append({
            "holder_table": variant, "row": rno, "direction": direction,
            "held_name": f["name"], "shares": f.get("shares", ""),
            "book_value": f.get("book_value", ""),
            "fy_of_change": f.get("fy_of_change", ""), "reason": reason,
        })

    note_rows = [{"holder_table": v, "row": r, "text": t}
                 for (v, r), t in sorted(notes.items(), key=_note_sort) if (t or "").strip()]

    flow_rows = [dict(f, holder_table=v, share_class=c)
                 for (v, c), f in sorted(flows.items())]

    # Ordinary shares are what a policy holding is; the bare context is the
    # all-classes total and is only a fallback. Counting the class contexts
    # tells a consumer when an ownership percentage is less than clean.
    class_ctx = [c for c in issued if c != "FilingDateInstant"]
    shares_out = {
        "issued": issued.get("FilingDateInstant_OrdinaryShareMember")
                  or issued.get("FilingDateInstant"),
        "treasury": treasury,
        "share_classes": len(class_ctx) or (1 if issued else 0),
    }
    total_rows = [{"holder_table": v, "share_class": c,
                   "book_value_yen": f.get("book_value_yen"),
                   "issue_count": f.get("issue_count")}
                  for (v, c), f in sorted(pol_totals.items())]

    equity_yen, equity_basis = resolve_financial(financials, EQUITY_SOURCES)
    assets_yen, assets_basis = resolve_financial(financials, ASSETS_SOURCES)

    extra = {"reclass": reclass_rows, "notes": note_rows, "flows": flow_rows,
             "shares_out": shares_out, "name_en": name_en,
             "totals": total_rows,
             "equity_yen": equity_yen, "equity_basis": equity_basis,
             "total_assets_yen": assets_yen, "assets_basis": assets_basis}
    return holdings, totals, extra


def row_no_of(ctx):
    m = re.search(r"Row(\d+)Member", ctx)
    return m.group(1) if m else None


def _reclass_sort(kv):
    """Row numbers are strings in the context id; sort them as numbers so
    row 10 does not land between row 1 and row 2."""
    (variant, direction, rno), _ = kv
    return (variant, direction, int(rno) if (rno or "").isdigit() else 0)


def _note_sort(kv):
    """Footnotes are numbered (注)1, (注)2, …; sort them numerically, not
    lexically, so note 10 does not land between 1 and 2."""
    (variant, rno), _ = kv
    return (variant, int(rno) if (rno or "").isdigit() else 0)


def to_int(s):
    s = (s or "").replace(",", "")
    return int(s) if s.isdigit() else None


SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS eq_entities (
            edinet_code VARCHAR PRIMARY KEY, sec_code VARCHAR, name_ja VARCHAR,
            name_en VARCHAR, industry VARCHAR, listed BOOLEAN);
        CREATE TABLE IF NOT EXISTS eq_filings (
            doc_id VARCHAR PRIMARY KEY, edinet_code VARCHAR, sec_code VARCHAR,
            filer_name VARCHAR, period_end DATE, filed_date DATE,
            sha256 VARCHAR, parser_version VARCHAR, status VARCHAR, detail VARCHAR,
            issued_shares BIGINT, treasury_shares BIGINT, share_classes INTEGER,
            filer_name_en VARCHAR,
            equity_yen BIGINT, equity_basis VARCHAR,
            total_assets_yen BIGINT, assets_basis VARCHAR);
        -- Holdings the filer moved between the policy bucket and 純投資目的.
        -- `direction` = to_pure (out of the named table, shares retained) or
        -- to_policy (the reverse). As filed, including the filer's own reason.
        CREATE TABLE IF NOT EXISTS eq_reclassified (
            doc_id VARCHAR, holder_table VARCHAR, row_no VARCHAR, direction VARCHAR,
            held_name_raw VARCHAR, held_edinet_code VARCHAR, held_sec_code VARCHAR,
            match_status VARCHAR, shares BIGINT, book_value_yen BIGINT,
            fy_of_change_ja VARCHAR, reason_ja VARCHAR);
        -- The filing's own numbered footnotes to the named table, verbatim.
        CREATE TABLE IF NOT EXISTS eq_filing_notes (
            doc_id VARCHAR, holder_table VARCHAR, note_no VARCHAR, text_ja VARCHAR);
        -- The filing's own tally of what actually moved, and for how much yen.
        CREATE TABLE IF NOT EXISTS eq_filing_flows (
            doc_id VARCHAR, holder_table VARCHAR, share_class VARCHAR,
            issues_increased BIGINT, acquisition_cost_yen BIGINT,
            issues_decreased BIGINT, sale_proceeds_yen BIGINT);
        -- The filer's own total for the WHOLE policy bucket, per disclosing
        -- entity and share class -- not our sum of the named rows, which
        -- covers only the largest issues. Stored at the finest grain the
        -- filing carries so the API can sum it without another extraction.
        CREATE TABLE IF NOT EXISTS eq_filing_totals (
            doc_id VARCHAR, holder_table VARCHAR, share_class VARCHAR,
            book_value_yen BIGINT, issue_count INTEGER);
        -- The measurement-basis vocabulary, one row per field value. Registry
        -- data, not vintage data: definitions of how a figure is measured, no
        -- figures. Rewritten wholesale each run like eq_entities. app/basis.py
        -- is the source of truth; this table exists so the vocabulary is
        -- queryable in SQL, and nothing in the API depends on it.
        CREATE TABLE IF NOT EXISTS eq_basis_labels (
            field VARCHAR, value VARCHAR, note VARCHAR);
        CREATE TABLE IF NOT EXISTS eq_holdings (
            doc_id VARCHAR, holder_table VARCHAR, row_no VARCHAR,
            held_name_raw VARCHAR, held_edinet_code VARCHAR, held_sec_code VARCHAR,
            match_status VARCHAR, shares BIGINT, book_value_yen BIGINT,
            prior_shares BIGINT, prior_book_value_yen BIGINT,
            purpose_ja VARCHAR, reciprocal VARCHAR);
"""

# The column list `base + (...)` is built against, in order. Named explicitly in
# the INSERT below because a positional INSERT ties the code to the table's
# PHYSICAL column order — and that order depends on which ALTER ran first when a
# database was migrated. Two features adding columns in different sessions leave
# different layouts, and the values then land in the wrong columns (an English
# filer name into share_classes INTEGER, which is how this was found).
FILINGS_COLUMNS = ("doc_id", "edinet_code", "sec_code", "filer_name", "period_end",
                   "filed_date", "sha256", "parser_version", "status", "detail",
                   "issued_shares", "treasury_shares", "share_classes",
                   "filer_name_en", "equity_yen", "equity_basis",
                   "total_assets_yen", "assets_basis")
FILINGS_INSERT = "INSERT INTO eq_filings (%s) VALUES (%s)" % (
    ", ".join(FILINGS_COLUMNS), ", ".join(["?"] * len(FILINGS_COLUMNS)))


def compact(db_path):
    """Rewrite the DB into a fresh file to reclaim space.

    Extraction deletes and reinserts each filing's rows so a run can be
    repeated or narrowed, and DuckDB does not return that space to the file.
    Over 21k filings the serving copy grew to 75MB against 29MB of actual
    data — and this file ships with the image as the seed, so the bloat would
    land in git forever. Rebuild-and-swap, not VACUUM, which does not shrink.
    """
    db_path = os.path.abspath(db_path)
    tmp = db_path + ".compact"
    for p in (tmp, tmp + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    before = os.path.getsize(db_path)
    con = duckdb.connect(tmp)
    con.execute("ATTACH '%s' AS old (READ_ONLY)" % db_path.replace("'", "''"))
    # Copy EVERY table the source holds, recreating it from its own stored DDL.
    # Not a hardcoded list: this file also carries the boards-and-pay tables
    # written by board_extract.py, and a list would silently drop whatever the
    # running build had not heard of. CREATE TABLE AS SELECT is not an option —
    # it drops primary keys, and the next extraction then fails on
    # INSERT OR REPLACE with "no UNIQUE/PRIMARY KEY constraints refer to this
    # table".
    tables = con.execute(
        "SELECT table_name, sql FROM duckdb_tables() WHERE database_name = 'old' "
        "ORDER BY table_name").fetchall()
    for t, ddl in tables:
        con.execute(ddl)
        con.execute('INSERT INTO "%s" SELECT * FROM old."%s"' % (t, t))
    # Views too, after every table exists: buyback.py publishes its rollups
    # (eq_buyback_lifecycle, eq_buyback_cancellations) as views, and a compact
    # that copied tables alone silently dropped them — the API then answered
    # 503 "not published" over data that was in fact all there.
    views = con.execute(
        "SELECT view_name, sql FROM duckdb_views() "
        "WHERE database_name = 'old' AND NOT internal ORDER BY view_name").fetchall()
    for v, ddl in views:
        con.execute(ddl)
    con.execute("DETACH old")
    con.close()
    os.replace(tmp, db_path)
    wal = db_path + ".wal"
    if os.path.exists(wal):
        os.remove(wal)
    print("compacted %.1fMB -> %.1fMB" % (before / 1e6, os.path.getsize(db_path) / 1e6))


# ---- main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every archived filer")
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--workers", type=int, default=12, help="parallel fetches (s3)")
    ap.add_argument("--limit", type=int, help="stop after N filings (smoke test)")
    ap.add_argument("--docs", help="comma-separated docIDs, as the other readers take")
    ap.add_argument("--db", default=DB_PATH,
                    help="target DuckDB file (default: the served equity DB); "
                         "point it elsewhere to verify a parser change without "
                         "touching the published one")
    ap.add_argument("--no-compact", action="store_true",
                    help="skip the rebuild-and-swap; the nightly refresh "
                         "compacts once after every extractor instead of "
                         "seven times")
    ap.add_argument("--new-only", action="store_true",
                    help="extract only filings archived since the last "
                         "recorded run (plus a lookback); what the nightly "
                         "refresh uses. A DB with no recorded run is built in "
                         "full, so this is always safe to pass.")
    ap.add_argument("--catch-up", type=int, default=CATCH_UP_DAYS,
                    metavar="DAYS",
                    help="on a --new-only run, also read the DAYS deepest "
                         "archive days below this extractor's own floor, so a "
                         "database built forward from a watermark fills in its "
                         "own history a slice at a time. 0 (default) is "
                         "forward only.")
    args = ap.parse_args()
    db_path = args.db

    src = S3Source(args.workers) if args.source == "s3" else LocalSource()

    codelist = load_codelist()
    tiers = build_index(codelist)
    ecode_info = {d["ＥＤＩＮＥＴコード"]: d for d in codelist}
    fin_codes = {d["ＥＤＩＮＥＴコード"] for d in codelist
                 if d["上場区分"] == "上場"
                 and any(k in d["提出者業種"] for k in FIN_INDUSTRIES)}

    # Window first, then discovery, then metadata for only the days the window
    # leaves standing. Both the bucket listing and the daily lists are the
    # expensive part of an incremental run, and neither is worth paying five
    # years of to extract one day of filings.
    since, have = (incremental_window(db_path, "cross-shareholdings",
                                      "eq_company_year")
                   if args.new_only else (None, set()))
    filings = src.filings(catch_up_start(since, args.catch_up if args.new_only else 0))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(db_path, "cross-shareholdings"))
    if since is not None:
        print("incremental: %d of %d archived filings are new since %s"
              % (len(pending), len(filings), since))
    meta = src.list_metadata(
        days=None if since is None else {r["date"] for r in pending.values()})
    targets = []
    for doc_id, rec in sorted(pending.items()):
        m = meta.get(doc_id) or {}
        # the daily list is the authority on doc type; the local manifest also
        # records it, but S3 discovery only knows the key exists
        if not annual(m, rec):
            continue
        if args.all or m.get("edinetCode") in fin_codes:
            targets.append((doc_id, rec, m))
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.limit:
        targets = targets[:args.limit]
    years = sorted({(m.get("periodEnd") or "?")[:4] for _, _, m in targets})
    print("target filings: %d (%s, source=%s) spanning %s"
          % (len(targets), "all" if args.all else "financial sector",
             src.name, "/".join(years)))

    con = duckdb.connect(db_path)
    con.execute(SCHEMA_SQL)

    # M4 -> M5: an already-populated DB has eq_filings without the share
    # counts. Add them rather than rebuilding, so a re-extraction is still a
    # re-extraction and not a drop; the rows themselves are rewritten anyway.
    have = {r[1] for r in con.execute("PRAGMA table_info('eq_filings')").fetchall()}
    for col, typ in (("issued_shares", "BIGINT"), ("treasury_shares", "BIGINT"),
                     ("share_classes", "INTEGER"), ("filer_name_en", "VARCHAR"),
                     ("equity_yen", "BIGINT"), ("equity_basis", "VARCHAR"),
                     ("total_assets_yen", "BIGINT"), ("assets_basis", "VARCHAR")):
        if col not in have:
            con.execute("ALTER TABLE eq_filings ADD COLUMN %s %s" % (col, typ))

    # The basis vocabulary, and a view that projects the basis tuple onto the
    # fact rows. Both are derived from app/basis.py, so a schema that has drifted
    # is corrected by the next run rather than migrated. entity_scope and
    # share_scope are READ from holder_table and share_class, which the filings
    # have always carried -- there is nothing to backfill and no stored row is
    # written to.
    con.execute("DELETE FROM eq_basis_labels")
    con.executemany("INSERT INTO eq_basis_labels VALUES (?,?,?)",
                    basis.label_rows())
    con.execute("""
        CREATE OR REPLACE VIEW v_holdings_basis AS
        SELECT h.doc_id, h.holder_table, h.row_no, h.held_sec_code,
               h.shares, h.book_value_yen,
               '%s' AS measurement,
               CASE h.holder_table WHEN 'reporting' THEN 'parent_only'
                                   WHEN 'largest' THEN 'largest_holding_company'
                                   WHEN 'second_largest'
                                        THEN 'second_largest_holding_company'
                                   ELSE 'holdco_consolidated' END AS entity_scope,
               'both' AS share_scope,
               '%s' AS trust_included,
               f.period_end AS as_of,
               '%s' AS period_type
        FROM eq_holdings h JOIN eq_filings f USING (doc_id)
    """ % (basis.EDINET_MEASUREMENT, basis.EDINET_TRUST_INCLUDED,
           basis.EDINET_PERIOD_TYPE))

    # entities (refresh wholesale — registry data, not vintage data)
    con.execute("DELETE FROM eq_entities")
    con.executemany(
        "INSERT INTO eq_entities VALUES (?,?,?,?,?,?)",
        [(d["ＥＤＩＮＥＴコード"], (d["証券コード"] or "")[:4] or None, d["提出者名"],
          latin(d.get("提出者名（英字）")), d.get("提出者業種") or None,
          d["上場区分"] == "上場") for d in codelist if d.get("ＥＤＩＮＥＴコード")])

    # Which registration of a company actually filed, and over which years.
    # Read once, before this run rewrites eq_filings, so the tie-break between
    # two registrations of one company is stable across the run.
    evidence = filing_evidence(con)

    stats = {"clean": 0, "partial": 0, "failed": 0}
    matched = domestic = foreign = reclassified = 0

    def fetch_and_parse(t):
        """Runs in a worker thread: network + CPU only, never touches DuckDB."""
        doc_id, rec, m = t
        try:
            blob = src.read_zip(doc_id, rec["date"])
            # Hash the bytes we actually parsed rather than trusting a manifest
            # field — the provenance claim on every row is then verifiable, and
            # S3 discovery has no manifest to read in the first place.
            return t, parse_filing(blob), hashlib.sha256(blob).hexdigest(), None
        except Exception as e:
            return t, None, None, str(e)[:200]

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m), parsed, sha, err = fut.result()
            done += 1
            if done % 500 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            period_end = m.get("periodEnd") or None
            base = (doc_id, m.get("edinetCode"), sec_code_of(m),
                    rec.get("filer") or m.get("filerName"), period_end, rec["date"],
                    sha or rec.get("sha256"), PARSER_VERSION)
            # DELETE then INSERT rather than INSERT OR REPLACE: a DB written by
            # an older build was compacted with CREATE TABLE AS SELECT and so
            # carries no primary key, which INSERT OR REPLACE requires.
            if err:
                con.execute("DELETE FROM eq_filings WHERE doc_id = ?", [doc_id])
                con.execute(FILINGS_INSERT,
                            base + ("failed", err) + (None,) * 8)
                stats["failed"] += 1
                continue
            holdings, totals, extra = parsed
            so = extra["shares_out"]

            # reconciliation gate: named sum must not exceed the filing's own total
            sums = defaultdict(int)
            for h in holdings:
                v = to_int(h["book_value"])
                if v:
                    sums[h["holder_table"]] += v
            breaches = [v for v in sums if v in totals and sums[v] > totals[v]]
            problems = (["named sum exceeds tagged total: %s" % breaches]
                        if breaches else [])

            # A second gate against the filer's own balance sheet. A company
            # cannot hold a portfolio of other companies' shares worth more
            # than everything it owns, so a policy-holdings table that totals
            # above total assets has a row on the wrong scale — 388 shares
            # booked at 869bn yen by a filer whose whole balance sheet is 139bn,
            # for one. Every figure is still stored exactly as filed; this only
            # says the filing does not add up against itself.
            portfolio = sum(sums.values())
            assets = extra["total_assets_yen"]
            if portfolio and assets and portfolio > assets:
                problems.append(
                    "policy holdings total %d yen against %d yen of total assets"
                    % (portfolio, assets))

            status = "partial" if problems else "clean"
            detail = "; ".join(problems) or None
            con.execute("DELETE FROM eq_filings WHERE doc_id = ?", [doc_id])
            con.execute(FILINGS_INSERT,
                        base + (status, detail, to_int(so["issued"]),
                                to_int(so["treasury"]), so["share_classes"] or None,
                                extra["name_en"], extra["equity_yen"],
                                extra["equity_basis"], extra["total_assets_yen"],
                                extra["assets_basis"]))
            stats[status] += 1

            def resolve(nm):
                """Held name -> (match_status, edinet_code, sec_code).

                Strongest key that names exactly one company wins. A weaker key
                is never allowed to override a stronger one, and an ambiguous
                key is skipped rather than resolved by registry order.
                """
                for tier, key in zip(tiers, (norm(nm), core_name(nm), base_name(nm))):
                    entries = tier.get(key)
                    if not entries:
                        continue
                    hit = pick(entries, evidence, period_end)
                    if hit:
                        return "matched", hit[0], hit[1] or None
                return ("foreign" if is_foreign(nm) else "unmatched"), None, None

            con.execute("DELETE FROM eq_holdings WHERE doc_id = ?", [doc_id])
            rows = []
            for h in holdings:
                nm = h["held_name"]
                mstat, ec, sc = resolve(nm)
                # Match-rate stats stay on the named positions only, so the
                # published rate keeps meaning the same thing across versions.
                if mstat == "matched":
                    matched += 1
                    domestic += 1
                elif mstat == "foreign":
                    foreign += 1
                else:
                    domestic += 1
                rows.append((doc_id, h["holder_table"], h["row"], nm, ec, sc, mstat,
                             to_int(h["shares"]), to_int(h["book_value"]),
                             to_int(h["prior_shares"]), to_int(h["prior_book_value"]),
                             h["purpose"] or None, (h["reciprocal"] or "").strip() or None))
            if rows:
                con.executemany("INSERT INTO eq_holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

            con.execute("DELETE FROM eq_reclassified WHERE doc_id = ?", [doc_id])
            rc = []
            for r in extra["reclass"]:
                mstat, ec, sc = resolve(r["held_name"])
                rc.append((doc_id, r["holder_table"], r["row"], r["direction"],
                           r["held_name"], ec, sc, mstat,
                           to_int(r["shares"]), to_int(r["book_value"]),
                           (r["fy_of_change"] or "").strip() or None,
                           (r["reason"] or "").strip() or None))
            if rc:
                con.executemany(
                    "INSERT INTO eq_reclassified VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rc)
                reclassified += len(rc)

            con.execute("DELETE FROM eq_filing_notes WHERE doc_id = ?", [doc_id])
            nt = [(doc_id, n["holder_table"], n["row"], n["text"].strip())
                  for n in extra["notes"]]
            if nt:
                con.executemany("INSERT INTO eq_filing_notes VALUES (?,?,?,?)", nt)

            con.execute("DELETE FROM eq_filing_flows WHERE doc_id = ?", [doc_id])
            fl = [(doc_id, f["holder_table"], f["share_class"],
                   to_int(f.get("issues_increased")), to_int(f.get("acquisition_cost_yen")),
                   to_int(f.get("issues_decreased")), to_int(f.get("sale_proceeds_yen")))
                  for f in extra["flows"]]
            if fl:
                con.executemany("INSERT INTO eq_filing_flows VALUES (?,?,?,?,?,?,?)", fl)

            con.execute("DELETE FROM eq_filing_totals WHERE doc_id = ?", [doc_id])
            tt = [(doc_id, t["holder_table"], t["share_class"],
                   t["book_value_yen"], t["issue_count"]) for t in extra["totals"]]
            if tt:
                con.executemany("INSERT INTO eq_filing_totals VALUES (?,?,?,?,?)", tt)

    fill_entity_english(con)

    con.close()
    n = con = None
    record_run(db_path, "cross-shareholdings", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(db_path)
    print("filings: clean %(clean)d, partial %(partial)d, failed %(failed)d" % stats)
    print("purpose-change rows: %d" % reclassified)
    if domestic:
        print("entity matching: %d/%d domestic (%.1f%%), %d foreign"
              % (matched, domestic, 100.0 * matched / domestic, foreign))
    print("wrote", os.path.normpath(db_path))


if __name__ == "__main__":
    main()
