# -*- coding: utf-8 -*-
u"""Flags for a filed number the filing itself cannot support.

Every figure we publish is as filed, and that is the point of the product. But
a handful of filings state a number that is arithmetically impossible against
another number on the same document, or that no company has ever reported:
an average employee salary of 9.5 trillion yen, a board of 8,000 officers, a
5% holding larger than the shares in issue printed beside it.

Those numbers stay exactly as filed. What changes is that they no longer arrive
unlabelled. Each rule here recomputes something the filing publishes and, where
the two disagree, attaches a `flags` entry saying which figure and why.

Two places apply the same rules, deliberately:

  * the extractors (`board_extract` G6-G8, `lvh_extract` G7, `extract`'s
    portfolio gate), which mark the filing `partial` when it is parsed;
  * here, at serve time, because the extractors run incrementally and will not
    revisit a filing from 2022 that was parsed before those gates existed.

A row that was already flagged by its extractor gets the same flag from both,
which is harmless -- `flags` is a set of reasons, not a count.

Thresholds are not round numbers chosen for comfort. Each is set outside the
whole archive's observed range, and the constant carries the observation:

    officers          largest real board + auditors in 21,176 filings:     52
    salary            1st..99th percentile of average pay:  3.6m .. 13.8m yen
    pay per officer   largest package ever disclosed in Japan:        ~13.4bn
    share of women    a share of a board or of managers is 0..1; the ten
                      filings above 1 (2026-11-07) typed the percentage
                      itself (I-PEX's 20.0 for 20%, so 2,000%)
    pay gap           women's pay as a share of men's, 0.07..1.47 across
                      8,036 filings except one at 71.4 (a percentage)

Where a number is used to rank, average or answer a question, the SQL
helpers below (pct_ok, ratio_ok, salary_ok, pay_gap_ok) give NULL for an
impossible figure, so it is left out instead of leading the list. Pages that
show one company still show the filed figure, with its flag.
"""

MAX_OFFICERS = 100
SALARY_BAND = (500000, 100000000)
MAX_PAY_PER_OFFICER = 3000000000
PAY_GAP_MAX = 3.0
# An average salary eight times above or below the same company's other years.
# Every jump of 4x or more in the archive (8 of ~25,000, 2026-11-07) is a
# tenfold slip that reverts the next year: 加藤製作所 ¥5.9m, then ¥60.0m.
SALARY_OWN_YEARS_FACTOR = 8.0

# Shares of a whole, filed as a fraction (0.2 = 20%).
RATIO_FIELDS = ("female_ratio_filed", "female_ratio_calc", "female_managers_ratio")


def _add(row, field, reason):
    row.setdefault("flags", [])
    entry = {"field": field, "reason": reason}
    if entry not in row["flags"]:
        row["flags"].append(entry)


def company_year(row, other_salaries=None):
    u"""Flag a governance row against the board it describes (and, given the
    company's other years' average salaries, against those)."""
    if not row:
        return row
    officers = row.get("officers_tagged")
    if officers is not None and officers > MAX_OFFICERS:
        _add(row, "officers_tagged",
             "%d officers tagged on a %s-seat board; the largest real board in "
             "the archive is 52, so the filing has tagged something that is not "
             "a headcount" % (officers, row.get("board_size")))
    salary = row.get("avg_salary_yen")
    if salary is not None and not SALARY_BAND[0] <= salary <= SALARY_BAND[1]:
        _add(row, "avg_salary_yen",
             "average annual pay of %d yen per employee is outside every "
             "reported range; the filing has stated it on the wrong scale"
             % salary)
    else:
        why = salary_vs_own_years(salary, other_salaries)
        if why:
            _add(row, "avg_salary_yen", why)
    for field in RATIO_FIELDS:
        v = row.get(field)
        if v is not None and not 0 <= v <= 1:
            _add(row, field,
                 "%s is %s; a share of a board or of managers is between 0 and "
                 "1, so the filing has typed the percentage (%s%%) in its place"
                 % (field, v, v))
    gap = row.get("gender_pay_gap_all")
    if gap is not None and not 0 < gap <= PAY_GAP_MAX:
        _add(row, "gender_pay_gap_all",
             "women's pay as a share of men's is filed as %s; every other "
             "filing is below 1.5, so this one has typed the percentage" % gap)
    return row


def pay_rows(rows):
    u"""Flag an officer-pay category whose per-head figure cannot be pay."""
    for row in rows or ():
        per_head = row.get("per_head_yen")
        if per_head is None and row.get("total_yen") and row.get("headcount"):
            per_head = row["total_yen"] / row["headcount"]
        if per_head and per_head > MAX_PAY_PER_OFFICER:
            _add(row, "total_yen",
                 "%d yen per officer; the largest package ever disclosed in "
                 "Japan is about 13bn for one named person, so a category "
                 "averaging more than 3bn is filed on the wrong scale"
                 % int(per_head))
    return rows


def lvh_rows(rows):
    u"""Flag a 5% holding larger than the shares in issue on the same form."""
    for row in rows or ():
        held, out = row.get("shares_held"), row.get("shares_outstanding")
        if held and out and held > out:
            _add(row, "shares_held",
                 "holding of %d exceeds the %d shares in issue stated on the "
                 "same form; the statutory denominator already includes the "
                 "holder's potential shares, so one of the two is wrong"
                 % (held, out))
        # A joint holder's line can be negative as filed (a broker netting
        # shares it must deliver), so only the top of the range is impossible.
        for field in ("ratio_pct", "prior_ratio_pct", "holder_ratio_pct", "holder_prior_ratio_pct"):
            v = row.get(field)
            if v is not None and v > 100:
                _add(row, field,
                     "%s is %s%%; a holding cannot exceed the shares in issue, "
                     "so the filing has put a count in the percentage" % (field, v))
    return rows


def pct_ok(col):
    u"""SQL: the percentage where it is one (0-100), else NULL — for ranking
    and averaging. The filed value itself is published unchanged."""
    return "CASE WHEN %s BETWEEN 0 AND 100 THEN %s END" % (col, col)


def ratio_ok(col):
    u"""SQL: a share filed as a fraction where it is one (0-1), else NULL."""
    return "CASE WHEN %s BETWEEN 0 AND 1 THEN %s END" % (col, col)


def salary_ok(col, row=None, table="eq_company_year"):
    u"""SQL: an average salary inside the band any filing has reported, else NULL.

    With `row` (the alias of the eq_company_year row the column comes from) it
    is also tested against the median of the same company's other years."""
    test = "%s BETWEEN %d AND %d" % (col, SALARY_BAND[0], SALARY_BAND[1])
    if row:
        test += (" AND coalesce(%s / nullif((SELECT median(o.avg_salary_yen) FROM %s o"
                 " WHERE o.edinet_code = %s.edinet_code AND o.doc_id <> %s.doc_id"
                 " AND o.avg_salary_yen BETWEEN %d AND %d), 0) BETWEEN %s AND %s, TRUE)"
                 % (col, table, row, row, SALARY_BAND[0], SALARY_BAND[1],
                    1 / SALARY_OWN_YEARS_FACTOR, SALARY_OWN_YEARS_FACTOR))
    return "CASE WHEN %s THEN %s END" % (test, col)


def salary_vs_own_years(salary, others):
    u"""Why an average salary cannot be right against the company's other
    years (a list of their salaries), or None."""
    vals = sorted(v for v in others or () if v is not None and SALARY_BAND[0] <= v <= SALARY_BAND[1])
    if salary is None or not vals:
        return None
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2.0
    if mid and not 1 / SALARY_OWN_YEARS_FACTOR <= salary / float(mid) <= SALARY_OWN_YEARS_FACTOR:
        return ("average annual pay of %d yen per employee is %.0f times the company's "
                "other years (median %d yen); the filing has stated it on the wrong scale"
                % (salary, max(salary / float(mid), mid / float(salary)), mid))
    return None


def pay_gap_ok(col):
    u"""SQL: women's pay as a share of men's where it can be one, else NULL."""
    return "CASE WHEN %s > 0 AND %s <= %s THEN %s END" % (col, col, PAY_GAP_MAX, col)


def per_officer_ok(col):
    u"""SQL: pay per officer below the wrong-scale line, else NULL."""
    return "CASE WHEN %s <= %d THEN %s END" % (col, MAX_PAY_PER_OFFICER, col)


def in_range(v, lo, hi):
    u"""Python twin of the SQL helpers: v where lo <= v <= hi, else None."""
    return v if v is not None and lo <= v <= hi else None


# The register's shareholder-category percentages, each a share of one total.
OWN_PCT_FIELDS = ("foreign_pct", "individuals_pct", "financial_institutions_pct",
                  "other_corporations_pct", "securities_firms_pct",
                  "government_pct", "nominee_ratio_pct", "majors_ratio_filed_pct")


def own_rows(rows):
    u"""Flag a register percentage outside 0-100.

    Seven registers in the archive state category percentages that sum to
    thousands of percent (スポーツフィールド's foreign ownership is filed as
    5,860.012%): units or shares typed into the percentage column. The
    extractor already marks those filings partial; this says which figure."""
    for row in rows or ():
        for field in OWN_PCT_FIELDS:
            v = row.get(field)
            if v is not None and not 0 <= v <= 100:
                _add(row, field,
                     "%s is %.3f%%; a share of the register cannot exceed 100%%, "
                     "so the filing has put a count in the percentage column"
                     % (field, v))
    return rows


def holdings_total(row, portfolio_yen, total_assets_yen):
    u"""Flag a cross-holding portfolio worth more than the whole balance sheet."""
    if portfolio_yen and total_assets_yen and portfolio_yen > total_assets_yen:
        _add(row, "book_value_yen",
             "policy holdings total %d yen against %d yen of total assets; a "
             "company cannot hold more than it owns, so a row is on the wrong "
             "scale" % (int(portfolio_yen), int(total_assets_yen)))
    return row


# A filing's tagged policy total, tested against two other numbers on the same
# document. Measured on 16,039 filings with a tagged total (FY2021-FY2026):
#   * the largest honest total is 84% of the filer's total assets (Fisco,
#     FY2025); 15 filings exceed 100%, and every one is an error -- 13 carry
#     a listed total 1,000x their named rows (a yen figure entered where the
#     filing works in thousands); 2 name no rows and are 122x and 352x total
#     assets;
#   * the listed total against the named listed rows -- which must include
#     every listed holding above 1% of capital, and at least the 60 largest --
#     tops out at 66x for an honest filer; the next value is 1,000x.
# Equity alone is no test: Japan Securities Finance's honest total is 12.6x
# its equity, and 13% of its assets.
TOTAL_ABOVE_ASSETS = 1.0
TOTAL_NAMED_MULTIPLE = 500
# The second test also needs the total to matter against the balance sheet.
# Without it a filer whose ROWS are the wrong figure (光陽社 FY2026 names a
# holding of 11,532 shares at 3,000 yen, beside a correct 4.7m-yen total) would
# have its correct total flagged instead.
TOTAL_NAMED_MIN_SHARE_OF_ASSETS = 0.10


def policy_total_reason(total_yen, listed_yen, named_yen, total_assets_yen):
    u"""Why a filing's tagged policy total cannot be right, or None.

    named_yen is the sum of the filing's named rows, excluding rows already
    flagged implausible. The total itself is never changed: the caller shows it
    as filed and leaves it out of the percentages."""
    if not total_yen or not total_assets_yen or total_assets_yen <= 0:
        return None
    if total_yen > TOTAL_ABOVE_ASSETS * total_assets_yen:
        return ("the tagged policy total of %s yen is larger than the filer's "
                "total assets of %s yen in the same filing; a portfolio carried "
                "on the balance sheet cannot exceed the balance sheet, so the "
                "total is filed on the wrong scale"
                % ("{:,}".format(int(total_yen)),
                   "{:,}".format(int(total_assets_yen))))
    if (named_yen and named_yen > 0 and listed_yen
            and listed_yen >= TOTAL_NAMED_MULTIPLE * named_yen
            and total_yen >= TOTAL_NAMED_MIN_SHARE_OF_ASSETS * total_assets_yen):
        return ("the tagged listed total of %s yen is %s times the %s yen of "
                "named listed holdings; the named table must include the "
                "largest holdings, so the total is filed on the wrong scale"
                % ("{:,}".format(int(listed_yen)),
                   "{:,}".format(int(round(listed_yen / float(named_yen)))),
                   "{:,}".format(int(named_yen))))
    return None


def _scope(basis):
    u"""'ifrs_consolidated_excl_nci' -> 'consolidated'; None stays None."""
    if not basis:
        return None
    return "parent" if basis.startswith("parent") else "consolidated"


def scale_bases(row):
    u"""Note a filing whose equity and assets are on different scopes.

    A filer can tag consolidated equity and parent-only total assets in the
    same document -- six do -- and the two are then not comparable with each
    other, which is how Fast Retailing ends up with equity larger than assets.
    Each figure is right on its own basis and each ratio beside it uses its own
    denominator, so nothing is recomputed; this only stops a reader reading the
    two against each other.
    """
    if not row:
        return row
    eq, ast = _scope(row.get("equity_basis")), _scope(row.get("assets_basis"))
    if eq and ast and eq != ast:
        _add(row, "equity_yen",
             "equity is %s and total assets are %s in the same filing, so the "
             "two are not comparable with each other; each ratio beside them "
             "uses its own denominator"
             % (row.get("equity_basis"), row.get("assets_basis")))
    return row
