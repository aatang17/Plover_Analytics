# -*- coding: utf-8 -*-
"""Equity product API — boards and pay (役員の状況・役員の報酬等).

Product surface #3, served from the same DuckDB file and the same reader as the
cross-shareholding API next door (equity_api.py): one connection, one writer
discipline, one set of English company names. Registered BEFORE the core router
for the same reason equity_api is — its literal /equity/ paths must beat the
core's /{dataset}/ catch-alls.

Two disclosures are carried in the DATA, not in prose, and every response that
touches the numbers repeats them (see METHODOLOGY-BOARDS-AND-PAY.md §2):

  1. Named individual pay is 連結報酬等 — CONSOLIDATED, including pay from group
     companies. It is a different basis from the officer-category table, so the
     two must never be netted, subtracted or divided into one another. Rows say
     so (`pay_basis`), and `named_exceeds_category` marks the filings where the
     arithmetic itself proves the bases differ.
  2. Pay components need not sum to the filed category total, because filers
     disagree on whether 非金銭報酬等 is additive or an "of which" memo and print
     components rounded to ¥mn. `total_yen` is the published number;
     `components_reconcile` says whether that filing's own components add up.

Cross-sections run against ONE filing per company (its latest, or the one
covering ?year=), never one row per filing: the archive holds five fiscal years
and a company would otherwise appear five times.
"""
import re

from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import listing
from . import person_en
from . import plausibility
from . import equity_api

from . import aliases
from .equity_api import (INDUSTRY_EN, NAMES_NOTE, NAME_CTES, PROVENANCE, _cur,
                         _rows)

router = APIRouter(prefix="/api/v1/equity/governance", tags=["Boards and pay"])

# One filing per company, mirroring equity_api.LATEST_FILINGS but over this
# dataset's own status column — 'no_tagged_board' and 'unsupported_form' filings
# are real archive rows, and they are excluded from every aggregate rather than
# counted as companies with no directors.
_LATEST_GOV = """
    WITH scoped AS (
        SELECT g.*, coalesce(g.sec_code, g.edinet_code, g.doc_id) AS filer_key
        FROM eq_company_year g
        LEFT JOIN eq_entities ent ON ent.edinet_code = g.edinet_code
        WHERE g.status IN ('clean','partial')/*ASOF*/
          AND (CAST(? AS VARCHAR) IS NULL
               OR CAST(year(g.period_end) AS VARCHAR) = CAST(? AS VARCHAR))
          AND (CAST(? AS VARCHAR) IS NULL
               OR coalesce(ent.listed, FALSE) = (CAST(? AS VARCHAR) = 'true'))
    ),
    current_gov AS (
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY filer_key
                                         ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM scoped
        ) WHERE rn = 1
    )
"""


def latest_gov():
    """Latest Gov, with the point-in-time ceiling in force (app/asof.py)."""
    return _LATEST_GOV.replace("/*ASOF*/", asof.clause("filed_date", "g"))

def _gov_params(year="", listed=""):
    """(year, listed) -> the four bind values latest_gov() expects."""
    y = (year or "").strip() or None
    l = (listed or "").strip().lower()
    l = l if l in ("true", "false") else None
    return [y, y, l, l]


# "Inside directors" is one concept under two board structures: a company with a
# 監査等委員会 splits its directors across two categories, and a screen that
# picked only one key would silently drop every company of the other type.
INSIDE_DIRECTOR_KEYS = (
    "DirectorsExcludingOutsideDirectorsMember",
    "DirectorsExcludingAuditAndSupervisoryCommitteeMembersAndOutsideDirectorsMember",
)
# Interpolated with .replace(), never %-formatting: these SQL fragments contain
# LIKE patterns whose % would be read as a format spec.
INSIDE_KEYS_SQL = str(INSIDE_DIRECTOR_KEYS)

# Pay per officer across ALL categories. The strict inside-director metric is
# only defined where the filer used one of the two standard category tags (92.4%
# of pay tables); Toyota and others invent their own, and a metric that silently
# returned null for them would read as "no data" rather than "different tagging".
# "Of which" sub-rows (うち社外役員…) are a subset of the row above them, not a
# category of their own: 106 filings carry them, and summing the table without
# excluding them double-counts those officers.
NOT_OF_WHICH = "p.category_key NOT ILIKE 'OfWhich%'"

# A filer-side scale error puts a nonsense number at the top of a pay screen, and
# there is a legally grounded cross-check for it: anyone paid ¥100m or more MUST
# be disclosed individually. So a filing whose per-head pay implies such a person
# while naming nobody is internally inconsistent. The figures are still published
# exactly as filed — the flag travels with them.
PAY_FLAG_SQL = """
    CASE WHEN (SELECT max(p.per_head_yen) FROM eq_pay_category p
                WHERE p.doc_id = g.doc_id AND """ + NOT_OF_WHICH + """) >= 100000000
              AND coalesce(g.named_count, 0) = 0
         THEN 'per-head pay implies an officer above the 100m yen individual-'
              || 'disclosure threshold, but this filing names no individual: the '
              || 'filer''s own figures are inconsistent. Figures are as filed.'
    END AS pay_consistency_flag"""

# eq_company_year.pay_category_total_yen sums every category row the filing
# tags, including うち社外役員 "of which" sub-rows — for the 106 filings that use
# them that double-counts. The served total is therefore computed from the rows,
# which is also the only definition that agrees with pay_per_officer_yen.
PAY_TOTAL_SQL = """
    (SELECT sum(p.total_yen) FROM eq_pay_category p
      WHERE p.doc_id = g.doc_id AND """ + NOT_OF_WHICH + """)"""

PAY_FLAG_NOTE = (
    "Anyone paid \u00a5100m or more must be disclosed individually, so a filing "
    "whose per-head officer pay implies such a person while naming nobody "
    "contradicts itself \u2014 almost always a filer scale error in the pay table. "
    "Those filings carry pay_consistency_flag. Their figures are published exactly "
    "as filed and are never corrected here; rank pay screens with the flag visible.")

INSIDE_PAY_NOTE = (
    "inside_director_pay_per_head_yen uses only the two standard XBRL officer "
    "categories that explicitly exclude outside directors, so it is comparable "
    "across companies but null for the ~8% of filers who define their own "
    "categories. pay_per_officer_yen divides the whole filed pay table by the "
    "officers it covers — defined for nearly every filer, but it mixes inside, "
    "outside and audit roles and is therefore not comparable in the same way.")

CONSOLIDATED_NOTE = (
    "Named individual pay is 連結報酬等 — CONSOLIDATED total remuneration, "
    "including pay from group companies. It is a different basis from the "
    "officer-category table on the same filing: people appear who do not sit on "
    "this board (an operating subsidiary's chief executive named in the parent's "
    "report), and the named total can exceed the whole officer-category total. "
    "Never net, subtract or divide one figure by the other.")

COMPONENTS_NOTE = (
    "The category total (報酬等の総額) is the filed, published figure. The "
    "components beside it are also as filed and frequently do not sum to it: "
    "filers differ on whether 非金銭報酬等 is additive or an 'of which' memo, "
    "components are printed rounded to ¥mn, and a forfeited share award can be "
    "negative. components_reconcile says whether this row's own components add "
    "up; it is disclosed, never corrected.")

BOARD_NOTE = (
    "The tagged table is the board (取締役会). In a 指名委員会等設置会社 the "
    "filer's own 役員 headcount also counts 執行役 who are disclosed only in a "
    "text block — officers_untagged is that gap. Pay-table headcounts count "
    "officers PAID during the year, including mid-year leavers, so they "
    "legitimately differ from board_size.")

NAMES_EN_NOTE = (
    "Director names in English are the filer's OWN romanisation, taken from the "
    "XBRL context it tagged each person with — not a translation by this "
    "platform. person_key is filer-authored: it joins a pay row to a board row "
    "inside one filing, and is NOT evidence that two companies share a director.")

CALC = {
    "age_at_period_end": "fiscal period end − date of birth, whole years",
    "female_ratio_calc": "female officers ÷ tagged board rows × 100",
    "per_head_yen": "category total ÷ officers paid in that category",
    "directors_70_plus_pct": "directors aged 70 or over ÷ tagged board rows × 100",
}


# The extractor maps the eight standard category tags. These are the filer-defined
# ones common enough to be worth a hand-written English label; anything else is
# de-camel-cased from the filer's own tag and marked as such.
EXTRA_LABELS = {
    "DirectorsMember": "Directors",
    "CorporateAuditorsMember": "Statutory auditors",
    "DirectorsExcludingAuditAndSupervisoryCommitteeMembersMember":
        "Directors excl. audit committee members",
    "DirectorsExcludingAuditCommitteeMembersMember":
        "Directors excl. audit committee members",
    "OutsideDirectorAuditAndSupervisoryCommitteeMemberMember":
        "Outside directors on the audit committee",
    "OutsideDirectorExcludingAuditAndSupervisoryCommitteeMemberMember":
        "Outside directors excl. audit committee members",
    "AuditAndSupervisoryCommitteeMembersMember": "Audit committee members",
    "ExecutiveOfficersAndDirectorsMember": "Executive officers and directors",
}


def _label(category_key, category_en):
    """English label for an officer category.

    Roughly one row in six uses a filer-defined category member that no fixed
    map can cover (Toyota's are all bespoke), so an unmapped key is de-camel-cased
    from the filer's own tag rather than returned as null — and the response says
    which of the two it is, so a reader never mistakes our formatting for a
    published label.
    """
    if category_en:
        return category_en, "mapped"
    if category_key in EXTRA_LABELS:
        return EXTRA_LABELS[category_key], "mapped"
    # A filer's tag is already English, in CamelCase; re-spacing it is not a
    # translation. "Member" repeats where a filer appended the suffix to a name
    # that ended in it (…CommitteeMemberMember), and a trailing digit is a
    # filer's own disambiguator (OutsideDirectors2) that needs its own space.
    key = re.sub(r"(?:Member)+$", "", category_key or "")
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key)
    words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", words)   # ESOPDirectors
    words = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", words).strip()
    parts = words.split()
    if not parts:
        return None, "derived_from_filer_tag"
    # Sentence case, so a derived label sits beside a curated one without
    # shouting. An all-capital run is an acronym the filer wrote and is kept.
    out = [parts[0][:1].upper() + parts[0][1:]]
    out += [w if (w.isupper() and len(w) > 1) or w.isdigit() else w.lower()
            for w in parts[1:]]
    return " ".join(out), "derived_from_filer_tag"


def _tables_present():
    cur = _cur()
    got = {r["table_name"] for r in _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_company_year','eq_board','eq_pay_category','eq_pay_named')""")}
    return len(got) == 4


def _require():
    if not _tables_present():
        raise HTTPException(503, "boards and pay dataset not published on this server yet")
    return _cur()


@router.get("/years")
def years():
    """Fiscal years available, newest first — the feed for a year picker."""
    cur = _require()
    return {"years": _rows(cur, """
        SELECT CAST(year(period_end) AS VARCHAR) AS year,
               count(DISTINCT coalesce(edinet_code, doc_id)) AS filings,  -- a correction is not another filer
               min(period_end) AS first_period_end, max(period_end) AS last_period_end
        FROM eq_company_year
        WHERE status IN ('clean','partial') AND period_end IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC""")}


@router.get("/summary")
def summary(year: str = Query("", description="fiscal year, e.g. 2026; default latest"),
            listed: str = Query("", description="'true' for listed filers only, "
                                                "'false' for unlisted; default both")):
    """Coverage first, then the market aggregates — one filing per company."""
    cur = _require()
    p = _gov_params(year, listed)
    head = _rows(cur, latest_gov() + """
        SELECT count(*)                                   AS companies,
               sum(board_size)                            AS board_seats,
               median(board_size)                         AS median_board_size,
               avg(avg_director_age)                      AS avg_director_age,
               100.0 * sum(directors_70_plus) / nullif(sum(board_size), 0)
                                                          AS directors_70_plus_pct,
               100.0 * avg(female_ratio_filed)            AS avg_female_officer_pct,
               sum(CASE WHEN female_officers = 0 THEN 1 ELSE 0 END) AS boards_with_no_women,
               median(avg_salary_yen)                     AS median_employee_salary_yen,
               median(gender_pay_gap_all)                 AS median_gender_pay_gap,
               sum(named_count)                           AS named_individuals,
               sum(CASE WHEN named_exceeds_category THEN 1 ELSE 0 END)
                                                          AS filings_named_exceeds_category,
               sum(pay_rows_reconciled)                   AS pay_rows_reconciled,
               sum(pay_rows_with_components)              AS pay_rows_with_components,
               min(period_end) AS earliest_period_end, max(period_end) AS latest_period_end
        FROM current_gov""", p)[0]
    pay = _rows(cur, latest_gov() + """
        SELECT median(CASE WHEN p.category_key IN {INSIDE} THEN p.per_head_yen END)
                   AS median_inside_director_pay_yen,
               count(DISTINCT CASE WHEN p.category_key IN {INSIDE} THEN g.doc_id END)
                   AS companies_with_inside_category,
               count(DISTINCT g.doc_id) AS companies_with_pay_table
        FROM current_gov g JOIN eq_pay_category p USING (doc_id)
        WHERE p.per_head_yen IS NOT NULL"""
        .replace("{INSIDE}", INSIDE_KEYS_SQL), p)[0]
    head.update(pay)
    head["median_pay_per_officer_yen"] = _rows(cur, latest_gov() + """
        SELECT median(v) AS v FROM (
            SELECT sum(p.total_yen) / nullif(sum(p.headcount), 0) AS v
            FROM current_gov g JOIN eq_pay_category p USING (doc_id)
            WHERE """ + NOT_OF_WHICH + """
              AND p.total_yen IS NOT NULL AND p.headcount IS NOT NULL
            GROUP BY g.doc_id)""", p)[0]["v"]
    head["filings_pay_inconsistent"] = _rows(cur, latest_gov() + """
        SELECT count(*) AS n FROM current_gov g
        WHERE coalesce(g.named_count, 0) = 0
          AND (SELECT max(p.per_head_yen) FROM eq_pay_category p
                WHERE p.doc_id = g.doc_id AND """ + NOT_OF_WHICH + """)
              >= 100000000""", p)[0]["n"]
    head["pay_consistency_note"] = PAY_FLAG_NOTE
    head["listed_scope"] = {"true": "listed filers only", "false": "unlisted filers only"}.get(
        (listed or "").strip().lower(), "every filer with an archived annual report")
    status = _rows(cur, "SELECT status, count(*) AS n FROM eq_company_year GROUP BY 1")
    head["extraction_status"] = {r["status"]: r["n"] for r in status}
    head["filings_total_all_years"] = sum(r["n"] for r in status)
    head["scope"] = ("fiscal year %s" % year.strip()) if year.strip() else \
        "each company's latest filing"
    head["coverage_note"] = (
        "Every archived annual report is parsed, but a filing whose officers "
        "table exists only as a text block (no_tagged_board — overwhelmingly "
        "unlisted filers) and one on a form that carries no governance section "
        "at all (unsupported_form) contribute no directors. Aggregates here use "
        "only clean and partial filings, one per company.")
    head["as_of_composition"] = _rows(cur, latest_gov() + """
        SELECT CAST(period_end AS VARCHAR)[1:4] AS year, count(*) AS companies
        FROM current_gov GROUP BY 1 ORDER BY 1 DESC""", p)
    head["as_of_note"] = (
        "Japanese fiscal year-ends are staggered and a delisted or merged "
        "company stops filing, so a latest-filing cross-section mixes reference "
        "periods — see as_of_composition. Request ?year= for one fiscal year.")
    head["consolidated_pay_note"] = CONSOLIDATED_NOTE
    head["components_note"] = COMPONENTS_NOTE
    head["board_note"] = BOARD_NOTE
    head["inside_pay_note"] = INSIDE_PAY_NOTE
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    return head


@router.get("/company/{sec_code}")
def company(sec_code: str,
            year: str = Query("", description="fiscal year; default the latest filing")):  # noqa: E501
    """One company, one filing: the board, the pay table, the named individuals."""
    cur = _require()
    code = (sec_code or "").strip()
    rows = _rows(cur, latest_gov() + NAME_CTES + """
        SELECT g.* EXCLUDE (pay_category_total_yen),
               """ + PAY_TOTAL_SQL + """ AS pay_category_total_yen,
               coalesce(n.name_en, s.name_en) AS filer_name_en, e.industry,""" + PAY_FLAG_SQL + """
        FROM current_gov g
        LEFT JOIN eq_entities e ON e.edinet_code = g.edinet_code
        LEFT JOIN en_ecode n ON n.edinet_code = g.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = g.sec_code
        WHERE g.sec_code = ?""", _gov_params(year) + [code])
    if not rows:
        raise HTTPException(404, "no extracted board or pay filing for %s" % code)
    f = rows[0]
    f.pop("rn", None)
    f.pop("filer_key", None)
    f["industry_en"] = INDUSTRY_EN.get(f.get("industry"))
    plausibility.company_year(f)
    doc = f["doc_id"]

    f["board"] = person_en.fix_rows(_rows(cur, """
        SELECT seat_no, person_key, name_ja, name_en, title_ja, role,
               is_representative, date_of_birth, age_at_period_end, shares_held
        FROM eq_board WHERE doc_id = ? ORDER BY seat_no""", [doc]))
    pay = _rows(cur, """
        SELECT category_key, category_en, is_custom_category, headcount, total_yen,
               per_head_yen, fixed_yen, base_yen, performance_yen, bonus_yen,
               non_monetary_yen, retirement_yen, other_components_yen,
               other_components, components_sum_yen, components_reconcile
        FROM eq_pay_category WHERE doc_id = ? ORDER BY total_yen DESC NULLS LAST""", [doc])
    for r in pay:
        r["category_label_en"], r["category_label_source"] = _label(
            r["category_key"], r.pop("category_en"))
    f["pay_by_category"] = plausibility.pay_rows(pay)
    f["pay_named"] = person_en.fix_rows(_rows(cur, """
        SELECT person_key, name_en, pay_basis, consolidated_pay_yen,
               voluntary_below_100m, on_board_at_filing
        FROM eq_pay_named WHERE doc_id = ? ORDER BY consolidated_pay_yen DESC""", [doc]))
    f["available_years"] = [r["year"] for r in _rows(cur, """
        SELECT CAST(year(y.period_end) AS VARCHAR) AS year FROM eq_company_year y
        WHERE y.sec_code = ? AND y.status IN ('clean','partial')""" + asof.newest(
            "y", "eq_company_year", ok="newer.status IN ('clean','partial')") + """
        ORDER BY y.period_end DESC""", [code])]
    f["consolidated_pay_note"] = CONSOLIDATED_NOTE
    f["pay_consistency_note"] = PAY_FLAG_NOTE
    f["components_note"] = COMPONENTS_NOTE
    f["board_note"] = BOARD_NOTE
    f["names_note"] = NAMES_EN_NOTE
    f["company_names_note"] = NAMES_NOTE
    f["calc"] = CALC
    f["provenance"] = PROVENANCE
    return f


@router.get("/history")
def history(sec_code: str = Query(..., description="4-digit securities code")):
    """One company across every fiscal year it has been extracted for."""
    cur = _require()
    code = sec_code.strip()
    series = _rows(cur, ("""
        SELECT CAST(year(g.period_end) AS VARCHAR) AS year, g.period_end, g.doc_id,
               g.status, g.board_size, g.avg_director_age, g.directors_70_plus,
               g.female_officers, g.female_ratio_filed, g.avg_salary_yen,
               g.employees_consolidated, g.named_count, g.named_sum_yen,
               (SELECT median(p.per_head_yen) FROM eq_pay_category p
                 WHERE p.doc_id = g.doc_id AND p.category_key IN {INSIDE})
                   AS inside_director_pay_per_head_yen,
               (SELECT sum(p.total_yen) / nullif(sum(p.headcount), 0)
                  FROM eq_pay_category p WHERE p.doc_id = g.doc_id
                   AND {NOTOFWHICH}
                   AND p.total_yen IS NOT NULL AND p.headcount IS NOT NULL)
                   AS pay_per_officer_yen
        FROM eq_company_year g
        WHERE g.sec_code = ? AND g.status IN ('clean','partial')""" + asof.newest(
            "g", "eq_company_year", ok="newer.status IN ('clean','partial')") + """
        ORDER BY g.period_end""")
        .replace("{INSIDE}", INSIDE_KEYS_SQL)
        .replace("{NOTOFWHICH}", NOT_OF_WHICH), [code])
    if not series:
        raise HTTPException(404, "no extracted board or pay filings for %s" % code)
    return {"sec_code": code, "series": series,
            "panel_note": ("A trend must compare the same company to itself. "
                           "Missing years mean the filing was not extractable, "
                           "not that the company stopped filing."),
            "consolidated_pay_note": CONSOLIDATED_NOTE,
            "inside_pay_note": INSIDE_PAY_NOTE,
            "calc": CALC, "provenance": PROVENANCE}


SCREENS = {
    "oldest_boards": ("avg_director_age DESC", "avg_director_age IS NOT NULL",
                      "Highest average director age"),
    "youngest_boards": ("avg_director_age ASC", "avg_director_age IS NOT NULL",
                        "Lowest average director age"),
    "no_women": ("board_size DESC", "female_officers = 0",
                 "Boards with no women, largest first"),
    "most_female": ("female_ratio_filed DESC", "female_ratio_filed IS NOT NULL",
                    "Highest female officer ratio"),
    "largest_boards": ("board_size DESC", "board_size IS NOT NULL", "Largest boards"),
    "oldest_directors": ("directors_70_plus DESC", "directors_70_plus > 0",
                         "Most directors aged 70 or over"),
    "highest_paid_boards": ("pay_category_total_yen DESC",
                            "g.pay_category_total_yen IS NOT NULL",
                            "Largest total officer remuneration, as filed"),
    "highest_pay_per_officer": ("pay_per_officer_yen DESC",
                                "g.pay_category_total_yen IS NOT NULL",
                                "Highest filed pay per officer (all categories)"),
    "highest_employee_pay": ("avg_salary_yen DESC", "avg_salary_yen IS NOT NULL",
                             "Highest average employee salary"),
    "widest_gender_pay_gap": ("gender_pay_gap_all ASC", "gender_pay_gap_all IS NOT NULL",
                              "Lowest female-to-male wage ratio"),
}


@router.get("/companies")
@listing.marked
def companies(q: str = Query("", description="name or code substring")):
    """Search feed for this dataset — a company is here if a board was extracted.

    Deliberately not the holdings search: a company can have a board and pay
    table extracted while disclosing no policy holdings at all, and searching the
    other dataset would tell that user their company is missing.
    """
    cur = _require()
    like = "%" + q.strip() + "%"
    alias_sql, alias_params = aliases.clause(cur, "g.sec_code", q)
    return {"companies": _rows(cur, latest_gov() + NAME_CTES + """
        SELECT g.sec_code, g.filer_name AS name,
               coalesce(n.name_en, s.name_en) AS name_en, e.industry,
               CAST(year(g.period_end) AS VARCHAR) AS year, g.board_size,
               g.avg_director_age, g.named_count
        FROM current_gov g
        LEFT JOIN eq_entities e ON e.edinet_code = g.edinet_code
        LEFT JOIN en_ecode n ON n.edinet_code = g.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = g.sec_code
        WHERE g.sec_code IS NOT NULL
          AND (g.sec_code LIKE ? OR g.filer_name LIKE ?
               OR lower(coalesce(n.name_en, s.name_en, '')) LIKE lower(?)"""
        + alias_sql + """)
        ORDER BY g.board_size DESC LIMIT 25""",
        _gov_params("", "") + [like, like, like] + alias_params),
        "names_note": NAMES_NOTE}


@router.get("/trend")
def trend(years_back: int = Query(5, ge=2, le=6),
          listed: str = Query("true", description="'true' (default) for listed filers")):
    """Matched panel: the same companies in every year, or the trend is an artefact.

    Coverage differs by fiscal year — the archive's window opens mid-2021 and
    closes mid-2026 — so a simple per-year average would move because the
    population moved. This restricts to companies with a clean filing in EVERY
    year of the window and says how many that is.
    """
    cur = _require()
    avail = [int(r["year"]) for r in years()["years"]]
    # Drop the two ends: the earliest year is a partial window and the latest is
    # still filling as June filings land.
    usable = sorted(y for y in avail if y not in (min(avail), max(avail)))[-years_back:]         if len(avail) > 2 else sorted(avail)
    if not usable:
        raise HTTPException(503, "not enough fiscal years to build a panel")
    lo, hi = min(usable), max(usable)
    listed_flag = (listed or "").strip().lower()
    listed_flag = listed_flag if listed_flag in ("true", "false") else None
    rows = _rows(cur, """
        WITH scoped AS (
            SELECT g.*, year(g.period_end) AS fy
            FROM eq_company_year g
            LEFT JOIN eq_entities ent ON ent.edinet_code = g.edinet_code
            WHERE g.status = 'clean' AND g.board_size > 0""" + asof.newest(
                "g", "eq_company_year", ok="newer.status IN ('clean','partial')") + """
              AND year(g.period_end) BETWEEN ? AND ?
              AND (CAST(? AS VARCHAR) IS NULL
                   OR coalesce(ent.listed, FALSE) = (CAST(? AS VARCHAR) = 'true'))
        ),
        -- One filing per company per fiscal year. A filer that changed its
        -- year-end can have two in one year, which would weight it double and
        -- make the panel size wobble between years.
        y AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (PARTITION BY edinet_code, fy
                                             ORDER BY period_end DESC, filed_date DESC) AS rn
                FROM scoped) WHERE rn = 1
        ),
        panel AS (
            SELECT edinet_code FROM y GROUP BY 1
            HAVING count(DISTINCT fy) = ?
        )
        SELECT CAST(y.fy AS VARCHAR) AS year, count(*) AS companies,
               avg(y.avg_director_age) AS avg_director_age,
               100.0 * sum(y.directors_70_plus) / nullif(sum(y.board_size), 0)
                   AS directors_70_plus_pct,
               100.0 * avg(y.female_ratio_filed) AS female_officer_pct,
               median(y.board_size) AS median_board_size,
               median(y.avg_salary_yen) AS median_employee_salary_yen,
               median((SELECT sum(p.total_yen) / nullif(sum(p.headcount), 0)
                         FROM eq_pay_category p WHERE p.doc_id = y.doc_id
                          AND {NOTOFWHICH}
                          AND p.total_yen IS NOT NULL AND p.headcount IS NOT NULL))
                   AS median_pay_per_officer_yen
        FROM y JOIN panel USING (edinet_code)
        GROUP BY 1 ORDER BY 1""".replace("{NOTOFWHICH}", NOT_OF_WHICH),
        [lo, hi, listed_flag, listed_flag, hi - lo + 1])
    return {
        "series": rows,
        "panel_companies": rows[0]["companies"] if rows else 0,
        "first_year": str(lo), "last_year": str(hi),
        "panel_note": (
            "A matched panel: the same %d companies in every fiscal year from %d to "
            "%d, each with a clean extraction. Coverage differs by year — the "
            "archive window opens and closes mid-year — so an unmatched average "
            "would move because the population moved, not because boards changed. "
            "The most recent and earliest fiscal years in the archive are excluded "
            "for that reason." % (rows[0]["companies"] if rows else 0, lo, hi)),
        "listed_scope": {"true": "listed filers only", "false": "unlisted filers only"}.get(
            listed_flag, "every filer with an archived annual report"),
        "calc": dict(CALC, **{
            "directors_70_plus_pct": "directors aged 70 or over ÷ all board seats × 100",
            "female_officer_pct": "mean of each company's filed female officer ratio × 100",
            "median_pay_per_officer_yen": ("median across companies of (sum of filed "
                                           "category totals ÷ sum of officers paid)"),
        }),
        "provenance": PROVENANCE,
    }


@router.get("/screen")
def screen(metric: str = Query("oldest_boards", description="one of /screen/metrics"),
           year: str = Query("", description="fiscal year; default latest filing"),
           listed: str = Query("", description="'true' for listed filers only"),
           cohort: str = Query("", description="restrict to a cohort, e.g. size:core30 "
                                               "or codes:7203,6758 — see "
                                               "/api/v1/equity/cohorts"),
           limit: int = Query(50, ge=1, le=500)):
    """Ranked cross-section, one filing per company.

    ?cohort= ranks inside a peer group instead of across the whole market —
    a board that looks ordinary among 3,700 filers can be an outlier in its
    own TOPIX band."""
    cur = _require()
    if metric not in SCREENS:
        raise HTTPException(400, "unknown metric; choose one of %s"
                            % ", ".join(sorted(SCREENS)))
    order, where, title = SCREENS[metric]
    codes, cohort_info = equity_api.cohort_filter(cohort)
    cohort_sql, cohort_params = equity_api.cohort_sql(codes, "g")
    rows = _rows(cur, latest_gov() + NAME_CTES + """
        SELECT g.sec_code, g.filer_name, coalesce(n.name_en, s.name_en) AS filer_name_en,
               e.industry, CAST(year(g.period_end) AS VARCHAR) AS year, g.doc_id,
               g.status, g.board_size, g.avg_director_age, g.directors_70_plus,
               g.female_officers, g.female_ratio_filed,
               """ + PAY_TOTAL_SQL + """ AS pay_category_total_yen,
               g.avg_salary_yen, g.gender_pay_gap_all, g.named_count,
               (SELECT sum(p.total_yen) / nullif(sum(p.headcount), 0)
                  FROM eq_pay_category p WHERE p.doc_id = g.doc_id
                   AND """ + NOT_OF_WHICH + """
                   AND p.total_yen IS NOT NULL AND p.headcount IS NOT NULL)
                   AS pay_per_officer_yen,""" + PAY_FLAG_SQL + """
        FROM current_gov g
        LEFT JOIN eq_entities e ON e.edinet_code = g.edinet_code
        LEFT JOIN en_ecode n ON n.edinet_code = g.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = g.sec_code
        WHERE g.sec_code IS NOT NULL AND {WHERE}{COHORT}
        ORDER BY {ORDER} NULLS LAST LIMIT ?"""
        .replace("{WHERE}", where).replace("{COHORT}", cohort_sql)
        .replace("{ORDER}", order),
        _gov_params(year, listed) + cohort_params + [limit])
    for r in rows:
        r["industry_en"] = INDUSTRY_EN.get(r.get("industry"))
    return {"metric": metric, "title": title, "rows": rows,
            "cohort": cohort_info and {k: cohort_info[k] for k in
                                       ("spec", "label", "as_of", "public", "kind")},
            "inside_pay_note": INSIDE_PAY_NOTE,
            "pay_consistency_note": PAY_FLAG_NOTE,
            "scope": ("fiscal year %s" % year.strip()) if year.strip()
                     else "each company's latest filing",
            "board_note": BOARD_NOTE, "calc": CALC,
            "company_names_note": NAMES_NOTE, "provenance": PROVENANCE}


@router.get("/screen/metrics")
def screen_metrics():
    """The metrics /screen accepts, each with its title."""
    return {"metrics": [{"metric": k, "title": v[2]} for k, v in sorted(SCREENS.items())]}


@router.get("/named")
def named(year: str = Query("", description="fiscal year; default latest filing"),
          listed: str = Query("", description="'true' for listed filers only"),
          limit: int = Query(50, ge=1, le=500),
          min_yen: int = Query(0, ge=0, description="floor on consolidated pay")):
    """Highest-paid named individuals — consolidated basis, see the note."""
    cur = _require()
    rows = _rows(cur, latest_gov() + NAME_CTES + """
        SELECT p.name_en, p.person_key, p.consolidated_pay_yen, p.pay_basis,
               p.on_board_at_filing, p.voluntary_below_100m,
               g.sec_code, g.filer_name, coalesce(n.name_en, s.name_en) AS filer_name_en,
               CAST(year(g.period_end) AS VARCHAR) AS year, g.doc_id,
               g.pay_category_total_yen, g.named_exceeds_category
        FROM current_gov g JOIN eq_pay_named p USING (doc_id)
        LEFT JOIN en_ecode n ON n.edinet_code = g.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = g.sec_code
        WHERE p.consolidated_pay_yen >= ?
        ORDER BY p.consolidated_pay_yen DESC LIMIT ?""",
        _gov_params(year, listed) + [min_yen, limit])
    person_en.fix_rows(rows)
    return {"rows": rows, "unit": "yen, as filed",
            "scope": ("fiscal year %s" % year.strip()) if year.strip()
                     else "each company's latest filing",
            "consolidated_pay_note": CONSOLIDATED_NOTE,
            "threshold_note": ("¥100m is the mandatory disclosure trigger, not a "
                               "floor: some filers disclose officers voluntarily "
                               "below it — voluntary_below_100m marks those."),
            "names_note": NAMES_EN_NOTE, "provenance": PROVENANCE}


# The dataset's card (app/registry.py). Named pay is consolidated; the
# category table is not — the two bases are never netted (CONSOLIDATED_NOTE).
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "boards-and-pay",
    "section": "governance",
    "name": {"en": "Boards and pay", "ja": "役員の状況・役員の報酬等"},
    "shape": "company",
    "summary": ("Every director as filed — name, role, age, shares held — with "
                "officer remuneration by category and the individually "
                "disclosed pay of anyone paid ¥100m or more, plus average "
                "employee salary and the gender pay ratio, one filing per "
                "company per fiscal year from FY2021."),
    "source": dict(_EDINET_SOURCE,
                   document="有価証券報告書 · 役員の状況 / 役員の報酬等 (annual "
                            "securities report, officer and remuneration sections)",
                   credit="Source: company filings on EDINET (Financial Services "
                          "Agency of Japan)."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2021", "stale_after_days": None,
    },
    "measures": [
        {"id": "board_size", "label": "Tagged board rows", "unit": "count",
         "trust": "official"},
        {"id": "female_officers", "label": "Female officers", "unit": "count",
         "trust": "official"},
        {"id": "female_ratio_filed", "label": "Female officer ratio, as filed", "unit": "%",
         "trust": "official"},
        {"id": "pay_category_total_yen", "label": "Officer remuneration by category (報酬等の総額)",
         "unit": "JPY", "trust": "official"},
        {"id": "named_pay_yen", "label": "Individually disclosed pay (連結報酬等, consolidated)",
         "unit": "JPY", "trust": "official"},
        {"id": "avg_salary_yen", "label": "Average employee salary", "unit": "JPY",
         "trust": "official"},
        {"id": "gender_pay_gap_all", "label": "Female-to-male wage ratio, as filed",
         "unit": "%", "trust": "official"},
        {"id": "age_at_period_end", "label": "Director's age at period end", "unit": "years",
         "trust": "derived", "calc": CALC["age_at_period_end"]},
        {"id": "avg_director_age", "label": "Average director age", "unit": "years",
         "trust": "derived",
         "calc": "mean of age_at_period_end over the tagged board rows of the filing"},
        {"id": "female_ratio_calc", "label": "Female officer ratio, computed", "unit": "%",
         "trust": "derived", "calc": CALC["female_ratio_calc"]},
        {"id": "per_head_yen", "label": "Pay per officer in a category", "unit": "JPY",
         "trust": "derived", "calc": CALC["per_head_yen"]},
        {"id": "directors_70_plus_pct", "label": "Directors aged 70 or over", "unit": "%",
         "trust": "derived", "calc": CALC["directors_70_plus_pct"]},
        {"id": "inside_director_pay_per_head_yen", "label": "Inside-director pay per head",
         "unit": "JPY", "trust": "derived",
         "calc": ("total of the two standard XBRL officer categories that exclude "
                  "outside directors ÷ officers paid in them; null for filers using "
                  "their own categories")},
        {"id": "pay_per_officer_yen", "label": "Pay per officer, all categories",
         "unit": "JPY", "trust": "derived",
         "calc": ("whole filed officer pay table ÷ the officers it covers — mixes "
                  "inside, outside and audit roles")},
        {"id": "pay_consistency_flag", "label": "Pay table contradicts the named-pay rule",
         "unit": "boolean", "trust": "derived",
         "calc": ("raised when per-head officer pay implies someone paid ¥100m or more "
                  "while no individual is named — almost always a filer scale error")},
    ],
    "endpoints": {
        "company": "/api/v1/equity/governance/company/{sec_code}",
        "search": "/api/v1/equity/governance/companies",
        "summary": "/api/v1/equity/governance/summary",
        "screen": "/api/v1/equity/governance/screen",
        "screen_metrics": "/api/v1/equity/governance/screen/metrics",
        "history": "/api/v1/equity/governance/history",
        "trend": "/api/v1/equity/governance/trend",
        "named": "/api/v1/equity/governance/named",
        "years": "/api/v1/equity/governance/years",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [{"id": k, "title": v[2]} for k, v in sorted(SCREENS.items())],
    "cite": "/governance.html?c={sec_code}",
    "page": "/governance.html",
    "notes": [CONSOLIDATED_NOTE, COMPONENTS_NOTE, BOARD_NOTE, PAY_FLAG_NOTE, NAMES_EN_NOTE],
}
