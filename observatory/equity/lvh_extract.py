# -*- coding: utf-8 -*-
u"""M1 — 5%-rule extractor: 大量保有報告書・変更報告書 (EDINET types 350/360).

Who is buying whom, in the tape the market actually watches. Anyone crossing
5% of a listed company's voting shares must file within five business days,
and file again on every 1-point move — so this is the only public dataset in
Japan that names an accumulating holder BEFORE the annual report does.
Everything here is already in the archive (the daily capture has taken types
350 and 360 since 2021), so nothing new is downloaded.

Unlike every other extractor in this repo the source is the t1 INLINE-XBRL
package, not a type=5 CSV: EDINET publishes no CSV rendition for this form.
The facts are tagged all the same (jplvh_cor:*), spread across the header and
body documents of the package, which are read together.

Two tables:

    eq_lvh_filings   one row per report: issuer, group position, why it was
                     filed, and this dataset's gates
    eq_lvh_holders   one row per filer or joint holder named in it

WHAT MAKES THIS DATASET WORTH THE TROUBLE, and the traps under each:

  1. THE ISSUER IS IDENTIFIED BY SECURITIES CODE (SecurityCodeOfIssuer), so
     the target resolves without name matching — the reverse view ("who has
     filed 5% on this company") is exact, not fuzzy.
  2. EACH HOLDER CARRIES ITS OWN EDINET CODE, so a holder resolves to one
     entity across every filing it has ever made. Names never have to be
     matched for the holder side either.
  3. 重要提案行為 (ActOfMakingImportantProposalEtc) IS A FILED FIELD. It is
     what separates a campaign from an index position, and it is disclosed
     rather than inferred. The special-reporting form (jplvh030000) is the
     exemption for institutions that undertake NOT to make such proposals, and
     it does not carry the field at all — absence there is meaningful, and is
     recorded as such rather than as a missing value. It is jplvh030000, not
     jplvh020000: measured on the archive, every one of 27,015 jplvh030000
     packages lacks the field and its filers are Nomura, the trust banks, the
     megabanks and FMR, while jplvh020000 packages ask the question and are
     filed by Evo Fund, City Index Eleventh and the like (lvh-1 had the two
     the wrong way round).
     The answer is free text, and "none" comes in dozens of wordings —
     特にありません, 当該事項はございません, 現時点において重要提案行為等を
     行う予定はありません — so it is read by proposal_stance(), not by
     testing for one phrase. lvh-1 treated anything that did not open with
     該当事項なし as a stated act, and so counted those as campaigns.
  4. A JOINT HOLDER WHO HAS LEFT THE GROUP IS STILL DESCRIBED IN THE FILING.
     Nomura's report on Nissui details three holders, and its group total of
     33,338,274 shares is the sum of TWO of them: Nomura International plc had
     just ceased to be a joint holder and appears with last-report figures
     only. Summing every holder in the document overstates the position, so a
     holder counts toward the group only where the filing gives it a CURRENT
     figure (`in_group_total`).
  5. A CHANGE REPORT OFTEN RESTATES THE ORIGINAL OBLIGATION DATE. 提出義務発生日
     on a 変更報告書 is frequently the date the holder first crossed 5%, years
     earlier, not the date of the change being reported: 14% of change reports
     in the archive state a date more than 40 days before filing, and the
     oldest reaches back to 1990. requirement_date is as filed, and the gap
     between it and the filing date is NOT a measure of lateness.
  6. FILERS MIS-TAG THE FREE-TEXT FIELDS. A funding amount routinely arrives
     with a whole table's text wrapped inside the tag, and the one number in
     that text may be a different line of the form (Nomura's borrowings tag
     contains its total funding). A numeric fact is therefore taken ONLY when
     the tagged text is itself a number; anything else is missing, and counted
     in `messy_facts`. A plausible-looking number in the wrong column is worse
     than a gap.

Money is stated in 千円 on this form and stored in yen.

Gates (each recomputes a number the filing itself publishes):
    G1  holders' share counts sum to the filed group total          (exact)
    G2  holders' ratios sum to the filed group ratio      (rounding-aware)
    G3  the filed group ratio equals held / outstanding   (rounding-aware)
    G4  the ratio is between 0 and 100
    G7  the holding does not exceed the shares in issue on the same form

One writer at a time: stop the local `uvicorn app.main:app` first.

Usage:
    python lvh_extract.py --limit 200                      # local smoke test
    python lvh_extract.py --all --source s3 --workers 16   # the whole archive

Python 3.9.
"""
import argparse
import glob
import hashlib
import html as html_mod
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed

import duckdb

from extract import (LocalSource, S3Source, load_codelist, build_index, pick,
                     norm, core_name, base_name, is_foreign, compact,
                     DB_PATH, ARCHIVE, incremental_window, record_run,
                     seek_key,
                     select_pending, catch_up_start, CATCH_UP_DAYS,
                     recorded_floor)

# lvh-2: alphanumeric securities codes kept whole (130A, not 130), the
# special-reporting form identified correctly, and the 重要提案行為 answer read
# by proposal_stance(). Stored lvh-1 rows are brought up to date by
# reclassify() and, for the truncated codes, by re-extraction (see main()).
PARSER_VERSION = "lvh-2"

# The special-reporting form: institutions that undertake to make no important
# proposals (trap 3).
SPECIAL_FORM = "jplvh030000"

L = "jplvh_cor:"
DEI = "jpdei_cor:"
GROUP_CTX = "FilingDateInstant"
# FilingDateInstant_jplvh010000-lvh_E03810-000FilerLargeVolumeHolder2Member
HOLDER_CTX = re.compile(r"FilerLargeVolumeHolder(\d+)Member$")

# Both ix element kinds are read: EDINET tags several numeric fields as
# nonNumeric and vice versa depending on the form generator, so the kind is not
# a reliable filter — the VALUE is checked instead (see clean_number).
#
# The scan CANNOT be a regular expression. Inline XBRL nests: a text-block fact
# wraps the tagged facts inside it, and a non-greedy regex closes the outer
# element on the inner element's end tag, then resumes past it — truncating the
# outer value and swallowing the inner facts whole. Measured on the archive
# that lost the holding ratio outright in 36% of filings. The parser below
# keeps a stack, which is what nesting requires.
IX_TAGS = ("ix:nonnumeric", "ix:nonfraction")
NUMERIC = re.compile(r"^-?[0-9,．.]+$")

# How filers write "none" in the 重要提案行為 box, verbatim from the archive.
NONE_STATED = (u"該当事項なし", u"該当事項無し", u"該当事項ありません",
               u"該当事項はありません", u"該当事項ございません",
               u"該当事項はございません", u"該当なし", u"該当無し",
               u"該当ありません", u"該当事項", u"ありません", u"ございません",
               u"なし", u"無し", u"無", u"-")
YEN_THOUSANDS = 1000

ERA = ((u"令和", 2018), (u"平成", 1988), (u"昭和", 1925))
DATE_RE = re.compile(u"(令和|平成|昭和)?\\s*([0-9０-９元]+)\\s*年\\s*([0-9０-９]+)\\s*月"
                     u"\\s*([0-9０-９]+)\\s*日")


class NoFacts(Exception):
    u"""A package with no inline-XBRL document — the amendment cover sheets
    ship a plain HTML rendering alongside the tagged one, and a handful of
    filings carry nothing else."""


def zen(s):
    u"""Full-width digits to ASCII. Filers use both, in the same document."""
    return (s or "").translate(dict(zip(range(0xFF10, 0xFF1A),
                                        range(ord("0"), ord("9") + 1))))


def jp_date(text):
    u"""'令和8年7月28日' or '2026年7月31日' -> '2026-07-28'. Both appear on
    this form, sometimes in the same filing."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    era, y, mo, d = m.group(1), zen(m.group(2)), zen(m.group(3)), zen(m.group(4))
    y = 1 if y == u"元" else int(y)
    if era:
        y += dict(ERA)[era]
    if not (1900 <= y <= 2100):
        return None
    try:
        return "%04d-%02d-%02d" % (y, int(mo), int(d))
    except ValueError:
        return None


class Fact(object):
    u"""One tagged value, with the two attributes that change what it means.

    `scale` is the filing's own statement of the value's magnitude: money on
    this form is printed in 千円 and tagged scale="3", ratios are printed in
    percent and tagged scale="-2". So the printed text is what a reader sees,
    and text x 10**scale is the value in the declared unit. Money is stored in
    yen (scaled); a ratio is stored as the printed percent, because that is
    the figure the filing publishes and the one every other percentage on this
    platform is expressed in.
    """

    __slots__ = ("text", "scale", "sign")

    def __init__(self, text, scale, sign):
        self.text = text
        self.scale = scale
        self.sign = sign

    def number(self):
        u"""The printed number, or None when the tag holds anything else."""
        v = clean_number(self.text)
        if v is not None and self.sign == "-":
            v = -v
        return v

    def scaled(self):
        u"""The number in its declared unit — for money, yen."""
        v = self.number()
        if v is None:
            return None
        try:
            return v * (10.0 ** int(self.scale)) if self.scale not in (None, "") else None
        except ValueError:
            return None


def clean_number(text):
    u"""A number, or None when the tag holds anything else.

    Deliberately strict — see trap 5 in the module docstring. A tag wrapping a
    whole table is not a value, even when exactly one number can be fished out
    of it, because that number is routinely a different line of the form.
    """
    s = zen(html_mod.unescape(text or "")).strip().replace(u"，", ",")
    s = re.sub(r"\s+", "", s)
    if not s or not NUMERIC.match(s):
        return None
    s = s.replace(",", "").replace(u"．", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _days(iso):
    u"""ISO date -> a day number, for comparing two dates without a datetime."""
    y, m, d = [int(x) for x in iso.split("-")]
    return y * 372 + m * 31 + d


# A filer that states no English name sometimes writes a rule of dashes rather
# than leaving the field empty. That is "not stated", not a name, and it must
# never reach a page as a company's English name.
DASHES_ONLY = re.compile(u"^[-\u2010\u2011\u2012\u2013\u2014\u2015\u2500\uFF0D\\s\u3000]+$")


def clean_english_name(value):
    t = clean_text(value)
    return None if (t is None or DASHES_ONLY.match(t)) else t


def clean_text(value):
    u"""Filed free text, whitespace collapsed, or None. Filers pad these fields
    with newlines and full-width spaces from the form's own layout."""
    t = re.sub(r"[\s\u3000]+", " ", (value or "")).strip()
    return t or None


def to_int(x):
    return int(round(x)) if x is not None else None


def yen_from_thousands(x):
    return int(round(x * YEN_THOUSANDS)) if x is not None else None


class IxScanner(HTMLParser):
    u"""Collects every ix fact in one document, nesting included.

    Text is pushed into every open frame, so a text block keeps the words of
    the facts inside it while those facts are still recorded in their own
    right — which is the point of the stack.
    """

    def __init__(self, facts):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.facts = facts
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in IX_TAGS:
            a = dict(attrs)
            self.stack.append([a.get("name", ""), a.get("contextref", ""),
                               a.get("scale"), a.get("sign"), []])

    def handle_startendtag(self, tag, attrs):
        if tag in IX_TAGS:
            a = dict(attrs)
            self.facts[a.get("name", "")][a.get("contextref", "")].append(
                Fact("", a.get("scale"), a.get("sign")))

    def handle_data(self, data):
        for frame in self.stack:
            frame[4].append(data)

    def handle_endtag(self, tag):
        if tag in IX_TAGS and self.stack:
            name, ctx, scale, sign, parts = self.stack.pop()
            self.facts[name][ctx].append(
                Fact("".join(parts).strip(), scale, sign))


def parse_package(blob):
    u"""t1 zip -> ({element: {context: [Fact]}}, form code).

    Every inline document in the package is read and merged: this form spreads
    its facts across the header, the holder sections (one file per joint holder
    in some packages) and the body.
    """
    facts = defaultdict(lambda: defaultdict(list))
    form = None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        docs = [n for n in z.namelist() if n.endswith("_ixbrl.htm")]
        if not docs:
            raise NoFacts(", ".join(sorted(
                os.path.basename(n) for n in z.namelist())[:4] or ["empty"]))
        for n in sorted(docs):
            if form is None:
                m = re.search(r"(jplvh\d+)", n)
                form = m.group(1) if m else None
            scanner = IxScanner(facts)
            scanner.feed(z.read(n).decode("utf-8", "replace"))
            scanner.close()
    return facts, form


def ctx_key(ctx):
    u"""Context id -> 'group' or a holder number. The filer's own extension
    namespace sits in the middle of the id and is ignored."""
    if ctx == GROUP_CTX:
        return 0
    m = HOLDER_CTX.search(ctx or "")
    return int(m.group(1)) if m else None


def fact(facts, element, ctx_no):
    u"""The first fact for an element in one context, or None.

    A filing states the same fact more than once — section 1 and the joint-
    holding breakdown carry the same numbers — and the first stated wins.

    Two namespaces are searched because the same element name lives in both:
    the cover page's EDINET code is jpdei_cor:EDINETCodeDEI while each
    holder's is jplvh_cor:EDINETCodeDEI. Searching only the form namespace
    left every filing without a filer code.
    """
    for prefix in (L, DEI):
        for ctx, vals in (facts.get(prefix + element) or {}).items():
            if ctx_key(ctx) == ctx_no and vals and vals[0].text:
                return vals[0]
    return None


def first(facts, element, ctx_no):
    u"""The first value of an element in one context, as text."""
    f = fact(facts, element, ctx_no)
    return f.text if f else ""


def holder_numbers(facts):
    nums = set()
    for element in ("Name", "IndividualOrCorporation", "EDINETCodeDEI",
                    "HoldingRatioOfShareCertificatesEtc"):
        for ctx in list(facts.get(L + element) or {}) + list(facts.get(DEI + element) or {}):
            k = ctx_key(ctx)
            if k:
                nums.add(k)
    return sorted(nums)


def stated_none(text):
    u"""Does the answer begin by saying "none"?

    Not an equality test: filers routinely leave the ix tag open over the rest
    of the section, so the value arrives as 該当事項なし followed by the next
    table. The answer is the first line either way, and anything that does not
    open with a none-phrase is treated as a stated act.
    """
    t = zen(text or "").strip()
    head = re.split(r"[\n\r。]", t, 1)[0]
    head = re.sub(r"[\s。．.、,]+", "", head)
    return (not head) or any(head.startswith(re.sub(r"[\s。．.]+", "", n))
                             for n in NONE_STATED)


# ---- reading the 重要提案行為 answer ----------------------------------------
#
# Three readings of a filed answer, plus None when nothing was filed:
#
#   will_or_may   the holder states it will, may or has made important
#                 proposals (可能性がある, 行うことがある, 取締役2名の指名提案)
#   none_planned  the holder states none, in any of the archive's wordings
#   unclear       text that does not answer the question — a lock-up
#                 agreement or a pledge typed into the wrong box, 純投資,
#                 現時点において未定
#
# important_proposal is True for will_or_may, False for none_planned and None
# otherwise: a misplaced lock-up clause is not a campaign, and it is not a
# "no" either.
STANCE_FLAG = {"will_or_may": True, "none_planned": False}

# A plan stated and then denied: 行う予定はありません, 予定していない.
# Not a bare 行わない: a lock-up typed into this box reads 売買等を行わない旨を
# 合意, which denies a sale, not a proposal.
NO_PLAN = re.compile(u"予定(は|も)?(ありません|ございません|ない|無い|なし|無し)|"
                     u"予定していない|予定しておりません|提案[^。]{0,15}(行わない|行いません)|"
                     u"意図はない|考えていない|目的と(し|は)(ない|ていない|しない|"
                     u"しておりません|していません)|行うことは(ない|ありません)")
# Hedges that turn a denial back into a reservation: 予定はないが、行う可能性がある.
HEDGE = re.compile(u"可能性|ことがある|ことがあります|場合があ|ありうる|あり得る|検討")
# A short answer that ends by saying none, typos included: 特にありません,
# 当該事業はありません, 該当時効無し.
NONE_TAIL = re.compile(u"(なし|無し|無|ありません|ございません|ない|無い)$")
# "Yes": 該当あり, 可能性　有. Not ありません.
SAYS_YES = re.compile(u"(あり|有り)(?!ませ)|有$")
PROPOSAL_WORDS = re.compile(u"提案|提言|選任|選解任|解任|指名|招集|助言|役員の派遣|"
                            u"非公開化|該当あり|可能性(有|あり)")
SEE_PURPOSE = re.compile(u"保有目的")
# What in a 保有目的 counts as the holder reserving proposals of its own. Not
# 他の株主が提案した議案 — voting on somebody else's proposal is not making one.
PURPOSE_PROPOSAL = re.compile(u"重要提案|重要事項提案|提案行為|提案(等|など)?を(行|検討)|"
                              u"指名提案|(助言|提言)(、|又は|または|及び|および|や)[^。]{0,6}提案")
CLAUSES = re.compile(u"[。\n\r]|が、|但し|ただし|なお、|しかし")


def _fold(text):
    u"""Width folded (NFKC turns the half-width ｡ some filers type into 。)."""
    return unicodedata.normalize("NFKC", html_mod.unescape(text or ""))


def _compact(text):
    return re.sub(u"[\\s。.、,]+", "", _fold(text))


def purpose_reserves_proposal(purpose):
    u"""Does the 保有目的 reserve important proposals, clause by clause?

    Derived, and deliberately kept apart from important_proposal: the large
    asset managers write 状況に応じて経営陣への助言、重要提案行為等を行うこと
    into the purpose of nearly every report as boilerplate, so this is true of
    Nomura and Mizuho as often as of any activist. A clause that denies a plan
    (重要提案行為等を行う予定はありません) does not count; a denial followed by
    a reservation (予定はありませんが、…提案を検討) does.
    """
    t = _fold(purpose)
    if not t.strip():
        return None
    for clause in CLAUSES.split(t):
        c = _compact(clause)
        if PURPOSE_PROPOSAL.search(c) and not NO_PLAN.search(c):
            return True
    return False


def proposal_stance(answer, purpose=None):
    u"""The filed 重要提案行為 answer -> will_or_may | none_planned | unclear,
    or None when nothing was filed. See the block comment above."""
    raw = _fold(answer).strip()
    if not raw:
        return None
    head = _compact(re.split(u"[\n\r。]", raw, 1)[0])
    if NO_PLAN.search(head) and not HEDGE.search(head):
        return "none_planned"
    if stated_none(raw) and not SAYS_YES.search(head):
        return "none_planned"
    if len(head) <= 40 and NONE_TAIL.search(head) and not HEDGE.search(head):
        return "none_planned"
    full = _compact(raw)
    if PROPOSAL_WORDS.search(full):
        return "will_or_may"
    # 上記（２）保有目的に記載のとおり: the answer is the purpose.
    if SEE_PURPOSE.search(full):
        return "will_or_may" if purpose_reserves_proposal(purpose) else "unclear"
    return "unclear"


def group_stance(stances):
    u"""A filing's stance from its holders': any will_or_may wins, then any
    none_planned, then unclear."""
    for s in ("will_or_may", "none_planned", "unclear"):
        if s in stances:
            return s
    return None


def issuer_code_of(text):
    u"""SecurityCodeOfIssuer -> the four-character code, letters kept.

    Codes issued since January 2024 may carry a letter (130A, 285A). lvh-1
    kept digits only and stored 130A as '130', which joins to nothing. Filers
    write the code in full or half width and with or without the trailing
    check digit, so: width folded, separators dropped, first four kept.
    """
    t = re.sub(u"[^0-9A-Z]", "", unicodedata.normalize("NFKC", text or "").upper())
    m = re.search(u"[0-9][0-9A-Z]{3}", t)       # a code opens with a digit
    return m.group(0) if m else None


REPORT_TYPES = ((u"訂正報告書", "amendment"), (u"変更報告書", "change"),
                (u"大量保有報告書", "initial"))
CHANGE_NO = re.compile(u"(?:No|NO|ＮＯ|Ｎｏ|№)?\\s*[.．]?\\s*([0-9０-９]+)")


# No Japanese share has ever traded near a million yen; a million yen per
# share is a ceiling nothing real can cross, not an estimate of anything.
MAX_YEN_PER_SHARE = 1000000


def implausible_funding(holders, group):
    u"""True when the stated acquisition cost cannot be a cost."""
    money, shares = 0, 0
    for h in holders:
        money += (h[17] or 0) + (h[18] or 0)
        shares += h[10] or 0
    money = money or ((group.get("own") or 0) + (group.get("borrow") or 0))
    shares = shares or (group.get("shares") or 0)
    if not money or not shares:
        return None
    return money / float(shares) > MAX_YEN_PER_SHARE


def report_type_of(title, doc_type):
    u"""(report_type, change_no). The cover-page title is the authority; the
    EDINET document type only says whether this is a correction of one."""
    t = zen(title or "")
    kind = next((k for mark, k in REPORT_TYPES if mark in t), None)
    if kind is None:
        kind = "amendment" if doc_type == "360" else "initial"
    no = None
    if kind == "change":
        m = CHANGE_NO.search(t.split(u"変更報告書", 1)[-1])
        if m:
            no = int(zen(m.group(1)))
    return kind, no


SCHEMA_SQL = """
        -- One row per 大量保有報告書 / 変更報告書 / 訂正報告書. The position is
        -- the GROUP's (filer plus joint holders) as the filing states it, not
        -- our sum; eq_lvh_holders carries the split.
        CREATE TABLE IF NOT EXISTS eq_lvh_filings (
            doc_id VARCHAR PRIMARY KEY, doc_type VARCHAR, form VARCHAR,
            report_type VARCHAR, change_no INTEGER, is_special_form BOOLEAN,
            filer_edinet_code VARCHAR, filer_name VARCHAR,
            issuer_name_raw VARCHAR, issuer_sec_code VARCHAR,
            issuer_edinet_code VARCHAR, issuer_match_status VARCHAR,
            listing VARCHAR, exchange VARCHAR,
            requirement_date DATE, filed_date DATE, cover_date DATE,
            holders_declared INTEGER, holders_rows INTEGER, holders_in_total INTEGER,
            shares_held BIGINT, shares_outstanding BIGINT,
            ratio_pct DOUBLE, prior_ratio_pct DOUBLE, ratio_change_pp DOUBLE,
            important_proposal BOOLEAN, proposal_asked BOOLEAN,
            filing_reason_ja VARCHAR,
            purpose_ja VARCHAR, own_funds_yen BIGINT, borrowings_yen BIGINT,
            funding_implausible BOOLEAN,
            sha256 VARCHAR, parser_version VARCHAR, status VARCHAR,
            detail VARCHAR, messy_facts INTEGER,
            -- The holders' 重要提案行為 answers read as one stance, and
            -- whether any holder's 保有目的 reserves proposals (derived; see
            -- proposal_stance and purpose_reserves_proposal).
            proposal_stance VARCHAR, purpose_mentions_proposal BOOLEAN);
        -- One row per filer or joint holder named in the report. in_group_total
        -- is false for a holder the filing describes but excludes from the
        -- group position — typically one that has just left the group.
        CREATE TABLE IF NOT EXISTS eq_lvh_holders (
            doc_id VARCHAR, holder_no INTEGER, name_raw VARCHAR, name_en VARCHAR,
            address_raw VARCHAR, holder_type_ja VARCHAR, is_individual BOOLEAN,
            holder_edinet_code VARCHAR, holder_sec_code VARCHAR,
            match_status VARCHAR, shares_held BIGINT, ratio_pct DOUBLE,
            prior_ratio_pct DOUBLE, in_group_total BOOLEAN, purpose_ja VARCHAR,
            important_proposal_ja VARCHAR, important_proposal BOOLEAN,
            own_funds_yen BIGINT, borrowings_yen BIGINT, total_funding_yen BIGINT,
            -- A stable identity for a holder that has no EDINET code of its
            -- own (or whose code was the issuer's — see the guard in build()).
            -- Width, spacing and old character forms are folded exactly as the
            -- entity resolver folds them, so one holder is one row of a
            -- ranking however the filer spelled it.
            name_key VARCHAR,
            -- 事業内容 and 職業, exactly as filed. What kind of institution a
            -- holder is gets read from these at serve time; storing the filer's
            -- own words rather than our reading of them means a change to that
            -- reading never rewrites a stored row.
            business_ja VARCHAR, occupation_ja VARCHAR,
            -- This holder's 重要提案行為 answer read as will_or_may /
            -- none_planned / unclear, and whether its 保有目的 reserves
            -- proposals. Re-derived from the stored text on every run by
            -- reclassify(), so a change to the reading reaches every row.
            proposal_stance VARCHAR, purpose_mentions_proposal BOOLEAN);
"""

# Columns added after lvh-1, for a database built by it: CREATE TABLE IF NOT
# EXISTS will not add a column to a table that is already there.
ADDED_COLUMNS = {
    "eq_lvh_filings": (("proposal_asked", "BOOLEAN"),
                       ("funding_implausible", "BOOLEAN"),
                       ("cover_date", "DATE"),
                       ("proposal_stance", "VARCHAR"),
                       ("purpose_mentions_proposal", "BOOLEAN")),
    "eq_lvh_holders": (("proposal_stance", "VARCHAR"),
                       ("purpose_mentions_proposal", "BOOLEAN")),
}

HOLDER_COLUMNS = ("doc_id", "holder_no", "name_raw", "name_en", "address_raw",
                  "holder_type_ja", "is_individual", "holder_edinet_code",
                  "holder_sec_code", "match_status", "shares_held", "ratio_pct",
                  "prior_ratio_pct", "in_group_total", "purpose_ja",
                  "important_proposal_ja", "important_proposal", "own_funds_yen",
                  "borrowings_yen", "total_funding_yen", "name_key",
                  "business_ja", "occupation_ja", "proposal_stance",
                  "purpose_mentions_proposal")
HOLDER_INSERT = "INSERT INTO eq_lvh_holders (%s) VALUES (%s)" % (
    ", ".join(HOLDER_COLUMNS), ", ".join(["?"] * len(HOLDER_COLUMNS)))

FILING_COLUMNS = ("doc_id", "doc_type", "form", "report_type", "change_no",
                  "is_special_form", "filer_edinet_code", "filer_name",
                  "issuer_name_raw", "issuer_sec_code", "issuer_edinet_code",
                  "issuer_match_status", "listing", "exchange",
                  "requirement_date", "filed_date", "cover_date", "holders_declared",
                  "holders_rows", "holders_in_total", "shares_held",
                  "shares_outstanding", "ratio_pct", "prior_ratio_pct",
                  "ratio_change_pp", "important_proposal", "proposal_asked",
                  "filing_reason_ja",
                  "purpose_ja", "own_funds_yen", "borrowings_yen",
                  "funding_implausible", "sha256",
                  "parser_version", "status", "detail", "messy_facts",
                  "proposal_stance", "purpose_mentions_proposal")
FILING_INSERT = "INSERT INTO eq_lvh_filings (%s) VALUES (%s)" % (
    ", ".join(FILING_COLUMNS), ", ".join(["?"] * len(FILING_COLUMNS)))


def build(doc_id, doc_type, facts, form, submitted, resolve_issuer, resolve_holder):
    u"""Parsed facts -> (filing row dict, holder rows, gate problems)."""
    problems = []
    messy = 0

    def take(element, ctx_no, money=False):
        u"""A numeric fact as printed, or the sentinel "messy" when the tag
        holds something else (trap 5). Money is returned in yen, using the
        filing's own scale where it states one and the form's 千円 otherwise."""
        f = fact(facts, element, ctx_no)
        if f is None or not f.text:
            return None
        v = f.scaled() if money else f.number()
        if v is None and money:
            v = f.number()
            v = v * YEN_THOUSANDS if v is not None else None
        return "messy" if v is None else v

    special = form == SPECIAL_FORM
    title = first(facts, "DocumentTitleCoverPage", 0)
    report_type, change_no = report_type_of(title, doc_type)

    sec = issuer_code_of(first(facts, "SecurityCodeOfIssuer", 0))
    issuer_name = first(facts, "NameOfIssuer", 0).strip() or None
    imstat, iec = resolve_issuer(sec, issuer_name)
    issuer_code = iec

    holders, in_total = [], 0
    for no in holder_numbers(facts):
        name = first(facts, "Name", no).strip()
        if not name:
            name = first(facts, "FilerNameInJapaneseDEI", no).strip()
        if not name:
            continue
        vals = {}
        for key, element, money in (
                ("shares", "TotalNumberOfStocksEtcHeld", False),
                ("ratio", "HoldingRatioOfShareCertificatesEtc", False),
                ("prior", "HoldingRatioOfShareCertificatesEtcPerLastReport", False),
                ("own", "AmountOfOwnFund", True),
                ("borrow", "TotalAmountOfBorrowings", True),
                ("funding", "TotalAmountOfFundingForAcquisition", True)):
            v = take(element, no, money)
            if v == "messy":
                messy += 1
                v = None
            vals[key] = v
        # The answer lives in one of TWO elements. When the holder states an
        # act it is tagged 重要提案行為等; when it states none, most form
        # generators leave that element empty and put 該当事項なし in the
        # …NA twin. Reading only the first left 89% of holders as "not
        # stated" — an unknown where the filing gives a plain no, which
        # makes an activist filter useless.
        business_ja = clean_text(first(facts, "DescriptionOfBusiness", no))
        occupation_ja = clean_text(first(facts, "Occupation", no))
        proposal_ja = (first(facts, "ActOfMakingImportantProposalEtc", no).strip()
                       or first(facts, "ActOfMakingImportantProposalEtcNA", no).strip())
        kind_ja = first(facts, "IndividualOrCorporation", no).strip()
        ecode = (first(facts, "EDINETCodeDEI", no) or "").strip() or None
        # A holder that has no EDINET registration of its own is sometimes
        # given the ISSUER's code by the filer's own XBRL tool. Be Brave, an
        # activist vehicle, files on three companies and carries a different
        # target's code each time — so its stakes split three ways and each
        # one lands on the code of the company it is challenging. The code is
        # only an identity when it is not the issuer's.
        if ecode and issuer_code and ecode == issuer_code and norm(name) != norm(issuer_name or ""):
            problems.append("holder %d filed under the issuer's own EDINET code "
                            "(%s); identified by name instead" % (no, ecode))
            ecode = None
        mstat, ec, sc = resolve_holder(ecode, name)
        # A holder counts toward the group position only where the filing gives
        # it a CURRENT figure (trap 4).
        current = vals["ratio"] is not None or vals["shares"] is not None
        in_total += 1 if current else 0
        purpose = first(facts, "PurposeOfHolding", no).strip() or None
        stance = None if special else proposal_stance(proposal_ja, purpose)
        holders.append((doc_id, no, name, clean_english_name(first(facts, "FilerNameInEnglishDEI", no)),
                        first(facts, "ResidentialAddressOrAddressOfRegisteredHeadquarter", no).strip() or None,
                        kind_ja or None, (u"個人" in kind_ja) if kind_ja else None,
                        ec, sc, mstat, to_int(vals["shares"]), vals["ratio"],
                        vals["prior"], current, purpose,
                        proposal_ja or None, STANCE_FLAG.get(stance),
                        to_int(vals["own"]), to_int(vals["borrow"]),
                        to_int(vals["funding"]), norm(name), business_ja,
                        occupation_ja, stance, purpose_reserves_proposal(purpose)))

    # ---- the group position ------------------------------------------------
    group = {}
    for key, element, money in (
            ("shares", "TotalNumberOfStocksEtcHeld", False),
            ("ratio", "HoldingRatioOfShareCertificatesEtc", False),
            ("prior", "HoldingRatioOfShareCertificatesEtcPerLastReport", False),
            ("outstanding", "TotalNumberOfOutstandingStocksEtc", False),
            ("own", "AmountOfOwnFund", True),
            ("borrow", "TotalAmountOfBorrowings", True)):
        v = take(element, 0, money)
        if v == "messy":
            messy += 1
            v = None
        group[key] = v
    # A single-holder filing states its position once, in the holder's own
    # context; there is no separate group line to read.
    singles = [h for h in holders if h[13]]
    if group["shares"] is None and len(singles) == 1:
        group["shares"] = singles[0][10]
    if group["ratio"] is None and len(singles) == 1:
        group["ratio"] = singles[0][11]
    if group["prior"] is None and len(singles) == 1:
        group["prior"] = singles[0][12]
    if group["outstanding"] is None:
        # Stated per holder context in single-filer reports. It is the issuer's
        # figure, so every context should carry the same number; where they
        # disagree the most-stated one is taken rather than the first seen.
        seen = defaultdict(int)
        for h in holders:
            v = take("TotalNumberOfOutstandingStocksEtc", h[1])
            if v not in (None, "messy"):
                seen[v] += 1
        if seen:
            group["outstanding"] = max(sorted(seen), key=lambda v: seen[v])

    # ---- gates -------------------------------------------------------------
    # Every gate here is ONE-SIDED, and each side is the form's own arithmetic
    # rather than a symmetric band chosen for convenience:
    #
    #   G1/G2  The group line is the holders' positions MINUS the form's own
    #          deduction for claims between joint holders (共同保有者間で引渡
    #          請求権等の権利が存在するものとして控除する株券等の数). So the
    #          holders can legitimately sum to more than the group total, and
    #          a group total the holders cannot cover is the defect.
    #   G3     The statutory ratio divides by 発行済株式等総数 PLUS the
    #          holder's own potential shares, and subtracts underwritten
    #          shares from the numerator — so the filed ratio is at or below
    #          held/outstanding by construction, and only a filed ratio ABOVE
    #          that identity is evidence of a wrong figure. Checking equality
    #          would have failed one filing in six on the statute, not on the
    #          data.
    share_rows = [h[10] for h in holders if h[13] and h[10] is not None]
    if len(share_rows) > 1 and group["shares"] is not None:
        if sum(share_rows) < group["shares"]:                               # G1
            problems.append("G1 holder shares sum to %d, short of the filed "
                            "group total %d" % (sum(share_rows), group["shares"]))
    ratio_rows = [h[11] for h in holders if h[13] and h[11] is not None]
    if len(ratio_rows) > 1 and group["ratio"] is not None:
        # Each holder's ratio is printed to two decimals.
        if sum(ratio_rows) < group["ratio"] - 0.005 * len(ratio_rows) - 1e-9:  # G2
            problems.append("G2 holder ratios sum to %.2f%%, short of the filed "
                            "%.2f%%" % (sum(ratio_rows), group["ratio"]))
    if group["ratio"] is not None and group["shares"] and group["outstanding"]:
        calc = 100.0 * group["shares"] / group["outstanding"]
        if group["ratio"] > calc + 0.01 + 1e-9:                             # G3
            problems.append("G3 filed ratio %.2f%% exceeds %d/%d = %.2f%%"
                            % (group["ratio"], group["shares"],
                               group["outstanding"], calc))
    if group["ratio"] is not None and not 0 <= group["ratio"] <= 100:        # G4
        problems.append("G4 holding ratio %.2f%% is outside 0-100" % group["ratio"])
    if group["shares"] and group["outstanding"] \
            and group["shares"] > group["outstanding"]:                      # G7
        # One-sided for the same reason G3 is. The statutory denominator
        # 発行済株式等総数 already ADDS the holder's own potential shares to the
        # shares in issue, and the numerator is a subset of that same total, so
        # a position larger than the denominator cannot arise from the statute
        # -- it means one of the two figures is on the wrong scale or was
        # copied from a different date. Both stay exactly as filed.
        problems.append("G7 holding of %d exceeds the %d shares in issue "
                        "stated on the same form"
                        % (group["shares"], group["outstanding"]))
    req = jp_date(first(facts, "DateWhenFilingRequirementAroseCoverPage", 0))
    if req and submitted and req > submitted:                                # G5
        # An obligation cannot arise after the report that discloses it. Both
        # dates stay exactly as filed; this says the filer typed one wrong
        # (Asahicho wrote 2027 for 2026).
        problems.append("G5 obligation date %s is after the submission date %s"
                        % (req, submitted))
    cover = jp_date(first(facts, "FilingDateCoverPage", 0))
    if cover and submitted and cover > submitted:                            # G6
        # A document cannot be dated after the day EDINET received it. The
        # test is one-sided on purpose: a cover page dated days or months
        # BEFORE submission is ordinary — the filer dates the document when it
        # is drawn up, and a correction restates the original report's date
        # (482 filings are more than 60 days apart on that account alone).
        # Only the impossible direction is a defect, and it caught one filing
        # in 3,893: a Trusco corrector dated a 2026 filing 2028.
        problems.append("G6 cover page dated %s, after EDINET received it on %s"
                        % (cover, submitted))

    proposals = [h[16] for h in holders if h[16] is not None]

    row = {
        "doc_id": doc_id, "doc_type": doc_type, "form": form,
        "report_type": report_type, "change_no": change_no,
        "is_special_form": special,
        "filer_edinet_code": (first(facts, "EDINETCodeDEI", 0) or "").strip() or None,
        "filer_name": (holders[0][2] if holders else None),
        "issuer_name_raw": issuer_name, "issuer_sec_code": sec,
        "issuer_edinet_code": iec, "issuer_match_status": imstat,
        "listing": first(facts, "ListedOrOTC", 0).strip() or None,
        "exchange": first(facts, "StockListing", 0).strip() or None,
        "requirement_date": jp_date(first(facts, "DateWhenFilingRequirementAroseCoverPage", 0)),
        # The cover page's own filing date is typed by the filer and is
        # sometimes years out (a correction filed in 2026 dated 2028). The
        # authoritative publication date is EDINET's own submitDateTime, set by
        # the system that received the document, and main() supplies it;
        # cover_date keeps what the filer wrote so the two can be compared.
        "cover_date": jp_date(first(facts, "FilingDateCoverPage", 0)),
        "holders_declared": to_int(clean_number(
            first(facts, "TotalNumberOfFilersAndJointHoldersCoverPage", 0))),
        "holders_rows": len(holders), "holders_in_total": in_total,
        "shares_held": to_int(group["shares"]),
        "shares_outstanding": to_int(group["outstanding"]),
        "ratio_pct": group["ratio"], "prior_ratio_pct": group["prior"],
        "ratio_change_pp": (round(group["ratio"] - group["prior"], 2)
                            if group["ratio"] is not None and group["prior"] is not None
                            else None),
        # True when ANY holder states an important-proposal act. The special
        # form does not carry the field, and NULL there says "not asked", not
        # "none" — the exemption itself is the undertaking.
        "important_proposal": (None if not proposals else any(proposals)),
        "proposal_stance": group_stance([h[23] for h in holders]),
        "purpose_mentions_proposal": (
            None if all(h[24] is None for h in holders)
            else any(h[24] for h in holders)),
        # Whether the form put the question at all. The special-reporting
        # form (jplvh030000) carries no 重要提案行為 field, so a null there is
        # "not asked". The general form asks it on initial, change and
        # amendment reports alike. Without this a reader cannot tell a silent
        # filer from a form that never asked.
        "proposal_asked": bool(facts.get(L + "ActOfMakingImportantProposalEtc")
                               or facts.get(L + "ActOfMakingImportantProposalEtcNA")),
        "filing_reason_ja": (first(facts, "ReasonForFilingChangeReportCoverPage", 0).strip()
                             or None),
        "purpose_ja": (holders[0][14] if holders else None),
        "own_funds_yen": to_int(group["own"]),
        "borrowings_yen": to_int(group["borrow"]),
        # 取得資金 is printed in 千円 and a minority of filers type YEN into
        # that box while still tagging scale="3" — Kobe Bussan's ¥1,677,200,000
        # of own funds becomes ¥1.68tn for 1.4m shares, a thousand times any
        # price that has ever existed. The amounts stay exactly as filed; this
        # flag says where the implied cost per share is impossible, so a page
        # can decline to print a number rather than print a wrong one.
        "funding_implausible": implausible_funding(holders, group),
        "messy_facts": messy,
    }
    return row, holders, problems


def _bulk_temp(con, name, columns, rows, chunk=500):
    u"""A temp table holding `rows`, inserted a chunk of rows per statement
    (row-at-a-time executemany is minutes on a first full pass)."""
    con.execute("CREATE OR REPLACE TEMP TABLE %s (%s)" % (name, ", ".join(columns)))
    width = len(columns)
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        con.execute("INSERT INTO %s VALUES %s" % (
            name, ", ".join(["(%s)" % ", ".join(["?"] * width)] * len(part))),
            [v for r in part for v in r])


def reclassify(con):
    u"""Re-apply the readings of stored text to every stored row.

    The stance, the flag and the purpose reading are functions of text the
    database already holds (important_proposal_ja, purpose_ja) and of the
    stored form code, so a change to how they are read reaches the whole
    archive on the next run without downloading anything. Only rows whose
    reading changes are written. Returns (holder rows changed, filings changed).
    """
    rows = con.execute("""
        SELECT h.doc_id, h.holder_no, h.important_proposal_ja, h.purpose_ja,
               h.important_proposal, h.proposal_stance,
               h.purpose_mentions_proposal, f.form
        FROM eq_lvh_holders h JOIN eq_lvh_filings f USING (doc_id)""").fetchall()
    per_doc = defaultdict(list)
    changed = []
    for doc_id, no, answer, purpose, flag, stance, mentions, form in rows:
        new_stance = None if form == SPECIAL_FORM else proposal_stance(answer, purpose)
        new = (STANCE_FLAG.get(new_stance), new_stance, purpose_reserves_proposal(purpose))
        per_doc[doc_id].append(new)
        if new != (flag, stance, mentions):
            changed.append((doc_id, no) + new)
    if changed:
        _bulk_temp(con, "lvh_reread",
                   ("doc_id VARCHAR", "holder_no INTEGER", "flag BOOLEAN",
                    "stance VARCHAR", "mentions BOOLEAN"), changed)
        con.execute("""
            UPDATE eq_lvh_holders h SET important_proposal = r.flag,
                   proposal_stance = r.stance, purpose_mentions_proposal = r.mentions
            FROM lvh_reread r
            WHERE h.doc_id = r.doc_id AND h.holder_no = r.holder_no""")

    filings = con.execute("""
        SELECT doc_id, form, important_proposal, proposal_stance,
               purpose_mentions_proposal, is_special_form
        FROM eq_lvh_filings WHERE status IN ('clean','partial')""").fetchall()
    fchanged = []
    for doc_id, form, flag, stance, mentions, special in filings:
        hs = per_doc.get(doc_id, [])
        flags = [h[0] for h in hs if h[0] is not None]
        ments = [h[2] for h in hs if h[2] is not None]
        new = ((any(flags) if flags else None), group_stance([h[1] for h in hs]),
               (any(ments) if ments else None), form == SPECIAL_FORM)
        if new != (flag, stance, mentions, special):
            fchanged.append((doc_id,) + new)
    if fchanged:
        _bulk_temp(con, "lvh_reread_f",
                   ("doc_id VARCHAR", "flag BOOLEAN", "stance VARCHAR",
                    "mentions BOOLEAN", "special BOOLEAN"), fchanged)
        con.execute("""
            UPDATE eq_lvh_filings f SET important_proposal = r.flag,
                   proposal_stance = r.stance, purpose_mentions_proposal = r.mentions,
                   is_special_form = r.special
            FROM lvh_reread_f r WHERE f.doc_id = r.doc_id""")
    return len(changed), len(fchanged)


def truncated_code_docs(db_path):
    u"""Stored filings whose issuer code lvh-1 cut to three characters, with
    their archive day: they are re-extracted from the archive (the full code
    is not stored anywhere else). Only rows written by an older parser are
    picked, so a filing that genuinely yields a short code is not re-read
    every night."""
    if not os.path.exists(db_path):
        return {}
    con = duckdb.connect(db_path, read_only=True)
    try:
        return {r[0]: {"date": r[1].isoformat(), "doc_type": r[2]}
                for r in con.execute("""
                    SELECT doc_id, filed_date, doc_type FROM eq_lvh_filings
                    WHERE length(issuer_sec_code) < 4
                      AND coalesce(parser_version, '') <> ?""",
                    [PARSER_VERSION]).fetchall()}
    except Exception:                                            # noqa: BLE001
        return {}
    finally:
        con.close()


# ---- filing discovery ------------------------------------------------------
LVH_TYPES = ("350", "360")


def local_t1_filings():
    out = {}
    with open(os.path.join(ARCHIVE, "manifest.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if (r.get("status") == "ok" and r.get("doc_type") in LVH_TYPES
                    and str(r.get("dl_type")) == "1"):
                out[r["doc_id"]] = r
    return out


def s3_t1_filings(src, start_after=None):
    out = {}
    for key in src._keys("docs/", start_after):
        parts = key.split("/")                    # docs/YYYY-MM-DD/DOCID_t1.zip
        if len(parts) != 3 or not parts[2].endswith("_t1.zip"):
            continue
        out[parts[2][:-len("_t1.zip")]] = {"date": parts[1]}
    return out


def read_t1(src, doc_id, date):
    if src.name == "local":
        with open(os.path.join(ARCHIVE, "docs", date, doc_id + "_t1.zip"), "rb") as f:
            return f.read()
    key = "docs/%s/%s_t1.zip" % (date, doc_id)
    return src.c.get_object(Bucket=src.bucket, Key=key)["Body"].read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("local", "s3"), default="local")
    ap.add_argument("--all", action="store_true",
                    help="kept for symmetry with the other extractors; this "
                         "form has no listed/unlisted filter to apply")
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
    by_sec, by_code = {}, {}
    for d in codelist:
        code = d.get(u"ＥＤＩＮＥＴコード")
        if not code:
            continue
        by_code[code] = d
        sec = (d.get(u"証券コード") or "")[:4]
        if sec:
            by_sec.setdefault(sec, []).append(d)

    # Window first, so the bucket listing can seek to it.
    since, have = (incremental_window(args.db, "5pct-filings", "eq_lvh_filings")
                   if args.new_only else (None, set()))
    filings = (local_t1_filings() if src.name == "local"
               else s3_t1_filings(src, catch_up_start(since, args.catch_up if args.new_only else 0)))
    through = max((r["date"] for r in filings.values()), default=None)
    pending, catch_up_floor = select_pending(
        filings, since, have,
        args.catch_up if args.new_only else 0,
        recorded_floor(args.db, "5pct-filings"))
    if since is not None:
        print("incremental: %d of %d archived filings are new since %s"
              % (len(pending), len(filings), since))
    # Filings whose issuer code an older parser truncated are read again. The
    # archive day is the one the listing gives where it has the filing, and
    # the filed date otherwise; a filing that cannot be fetched keeps its
    # stored row rather than being overwritten with a failure.
    redo = set()
    for doc_id, rec in truncated_code_docs(args.db).items():
        if doc_id not in pending:
            pending[doc_id] = filings.get(doc_id) or rec
            redo.add(doc_id)
    if redo:
        print("re-reading %d filings stored with a truncated issuer code" % len(redo))
    meta = src.list_metadata(
        days=None if since is None else {r["date"] for r in pending.values()})
    targets = []
    for doc_id, rec in sorted(pending.items()):
        m = meta.get(doc_id) or {}
        doc_type = m.get("docTypeCode") or rec.get("doc_type")
        if doc_type not in LVH_TYPES:
            continue
        targets.append((doc_id, rec, m, doc_type))
    if args.docs:
        want = {d.strip() for d in args.docs.split(",") if d.strip()}
        targets = [t for t in targets if t[0] in want]
    if args.limit:
        targets = targets[:args.limit]
    dates = sorted({rec["date"][:4] for _, rec, _, _ in targets})
    print("target filings: %d (source=%s) filed %s"
          % (len(targets), src.name, "/".join(dates)))

    con = duckdb.connect(args.db)
    con.execute(SCHEMA_SQL)
    # A database written by an earlier parser has the table already, and
    # CREATE TABLE IF NOT EXISTS will not add a column to it. Add rather than
    # rebuild, so a re-extraction stays a re-extraction.
    for table, added in ADDED_COLUMNS.items():
        have = {r[1] for r in con.execute("PRAGMA table_info('%s')" % table).fetchall()}
        for col, typ in added:
            if col not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, typ))

    def resolve_issuer(sec, name):
        u"""The issuer's securities code is filed, so this is a lookup, not a
        match. The name is a fallback for the handful of filings that state an
        issuer with no code (unlisted OTC issuers)."""
        if sec:
            hits = by_sec.get(sec) or []
            listed = [d for d in hits if d.get(u"上場区分") == u"上場"]
            pool = listed or hits
            if len(set(d[u"ＥＤＩＮＥＴコード"] for d in pool)) == 1:
                return "matched", pool[0][u"ＥＤＩＮＥＴコード"]
            if pool:
                return "ambiguous", None
        if name:
            for tier, key in zip(tiers, (norm(name), core_name(name), base_name(name))):
                hit = pick(tier.get(key) or [], None, None)
                if hit:
                    return "matched_by_name", hit[0]
        return ("foreign" if name and is_foreign(name) else "unmatched"), None

    def resolve_holder(ecode, name):
        u"""The holder files under its own EDINET code, so the code is the
        match. Name matching is a fallback only."""
        if ecode and ecode in by_code:
            d = by_code[ecode]
            return "matched", ecode, (d.get(u"証券コード") or "")[:4] or None
        if ecode:
            return "code_not_in_registry", ecode, None
        for tier, key in zip(tiers, (norm(name), core_name(name), base_name(name))):
            hit = pick(tier.get(key) or [], None, None)
            if hit:
                return "matched_by_name", hit[0], hit[1] or None
        return ("foreign" if is_foreign(name or "") else "unmatched"), None, None

    def fetch_and_parse(t):
        doc_id, rec, m, doc_type = t
        sha = None
        try:
            blob = read_t1(src, doc_id, rec["date"])
            sha = hashlib.sha256(blob).hexdigest()
            return t, parse_package(blob), sha, None
        except NoFacts as e:
            return t, None, sha, ("no_inline_xbrl", str(e)[:160])
        except Exception as e:                                       # noqa: BLE001
            return t, None, sha, ("failed", "%s: %s" % (type(e).__name__, str(e)[:160]))

    stats = defaultdict(int)
    tot_holders = tot_proposal = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(fetch_and_parse, t) for t in targets]
        for fut in as_completed(futures):
            (doc_id, rec, m, doc_type), parsed, sha, err = fut.result()
            done += 1
            if done % 2000 == 0:
                print("  %d/%d filings" % (done, len(targets)))
                sys.stdout.flush()
            if err and doc_id in redo:
                stats["redo_unreadable"] += 1
                continue
            con.execute("DELETE FROM eq_lvh_holders WHERE doc_id = ?", [doc_id])
            con.execute("DELETE FROM eq_lvh_filings WHERE doc_id = ?", [doc_id])
            if err:
                st, detail = err
                stats[st] += 1
                blank = dict((c, None) for c in FILING_COLUMNS)
                blank.update({"doc_id": doc_id, "doc_type": doc_type,
                              "filer_name": rec.get("filer") or m.get("filerName"),
                              "filed_date": rec["date"], "sha256": sha,
                              "parser_version": PARSER_VERSION,
                              "status": st, "detail": detail})
                con.execute(FILING_INSERT, [blank[c] for c in FILING_COLUMNS])
                continue
            facts, form = parsed
            submitted = (m.get("submitDateTime") or "")[:10] or rec["date"]
            row, holders, problems = build(doc_id, doc_type, facts, form, submitted,
                                           resolve_issuer, resolve_holder)
            if not holders or row["ratio_pct"] is None:
                status = "partial"
                problems.insert(0, "no holder rows" if not holders
                                else "no holding ratio stated")
            elif problems:
                status = "partial"
            else:
                status = "clean"
            stats[status] += 1
            row.update({"sha256": sha, "parser_version": PARSER_VERSION,
                        "status": status,
                        "detail": "; ".join(problems[:4]) or None})
            if not row.get("filer_name"):
                row["filer_name"] = rec.get("filer") or m.get("filerName")
            # EDINET's receipt date is the publication date, always.
            row["filed_date"] = submitted
            con.execute(FILING_INSERT, [row[c] for c in FILING_COLUMNS])
            if holders:
                con.executemany(HOLDER_INSERT, holders)
                tot_holders += len(holders)
                tot_proposal += sum(1 for h in holders if h[16])
    reread = reclassify(con)
    print("reclassified: %d holder rows, %d filings" % reread)
    con.close()
    record_run(args.db, "5pct-filings", through, len(filings), PARSER_VERSION,
               back_to=catch_up_floor)
    if not args.no_compact:
        compact(args.db)
    print("filings: %s" % dict(stats))
    print("holder rows: %d (%d stating an important-proposal act)"
          % (tot_holders, tot_proposal))
    print("wrote", os.path.normpath(args.db))


if __name__ == "__main__":
    main()
