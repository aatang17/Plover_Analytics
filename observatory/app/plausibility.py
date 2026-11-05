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
"""

MAX_OFFICERS = 100
SALARY_BAND = (500000, 100000000)
MAX_PAY_PER_OFFICER = 3000000000


def _add(row, field, reason):
    row.setdefault("flags", [])
    entry = {"field": field, "reason": reason}
    if entry not in row["flags"]:
        row["flags"].append(entry)


def company_year(row):
    u"""Flag a governance row against the board it describes."""
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
    return rows


def pct_ok(col):
    u"""SQL: the percentage where it is one (0-100), else NULL — for ranking
    and averaging. The filed value itself is published unchanged."""
    return "CASE WHEN %s BETWEEN 0 AND 100 THEN %s END" % (col, col)


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
