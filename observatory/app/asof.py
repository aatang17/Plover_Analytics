# -*- coding: utf-8 -*-
"""Point-in-time reads of the filing datasets: what EDINET held on a date.

The macro side has served `?as_of=` since the vintage store landed — ask for a
CPI series as of last July and you get the numbers a reader would have had
then, from the append-only vintage history. The filing datasets could not
answer the same question, and this is what lets them.

**The basis is the filed date, and that is a decision, not a shortcut.** The
plan specified "what the platform had captured on that date", falling back to
the filed date. No capture timestamp exists: `eq_filings`, `eq_own_filings`,
`eq_lvh_filings`, `eq_fac_filings`, `eq_fin_filings`, `eq_seg_filings` and
`eq_agm_meetings` record when a document was FILED, and `eq_buyback_filings`
when it was SUBMITTED, and nothing anywhere records when we fetched it. So
capture-time semantics are not reconstructible for a single filing already in
the archive, and pretending otherwise would be a fiction dressed as a vintage.
`as_of` here means: **the filings that existed publicly on EDINET by the end of
that day.** Every response says so.

That is the weaker of the two claims and the honest one. It is also the more
useful one for a backtest — what the market could read — while "what we had
captured" only ever describes this platform's own plumbing.

How the ceiling travels
-----------------------
A ceiling applies to a whole request, not to one query: a company view that
answered half its blocks as of a past date and half as of today would be worse
than refusing. It is therefore set once at the request boundary — see
`scope()` — and read by every filing-selection query through `clause()`.

The obvious risk of that design is a query that forgets to ask. Nothing about
a contextvar makes a stale query fail loudly; it just quietly returns today's
filing. So the guarantee is not that every query was found by reading the code
— it is `tests/test_asof.py`, which runs every company view under a ceiling
and fails if ANY date anywhere in the response is later than it. A query that
forgets the clause fails that test, which is the only form of assurance worth
having here.
"""
import contextlib
import contextvars
import datetime

from fastapi import HTTPException

# The ceiling in force for the current request, or None for "latest".
_CEILING = contextvars.ContextVar("equity_as_of", default=None)

BASIS = "filed_date"
BASIS_NOTE = (
    "as_of selects the filings that existed publicly on EDINET by the end of "
    "that day, by their filed date. It is not a record of what this platform "
    "had captured by then — capture time was never recorded, so that view "
    "cannot be reconstructed for filings already in the archive."
)


def parse(value):
    """A query parameter to a date, or None. Raises 400 on anything else."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        raise HTTPException(
            400, "as_of must be a date, YYYY-MM-DD (got %r)" % value)


def current():
    return _CEILING.get()


@contextlib.contextmanager
def scope(value):
    """Apply a ceiling to every filing read inside this block."""
    ceiling = parse(value)
    token = _CEILING.set(ceiling)
    try:
        yield ceiling
    finally:
        _CEILING.reset(token)


def literal(date):
    """A date as SQL. Built here, never interpolated from user text: the value
    has already been through `parse` and is a datetime.date, so this cannot
    carry anything but eight digits and two hyphens."""
    return "DATE '%04d-%02d-%02d'" % (date.year, date.month, date.day)


def clause(column="filed_date", alias=""):
    """` AND <col> <= DATE '...'` when a ceiling is in force, else ''.

    Returns a fragment that is safe to concatenate into a WHERE, and empty
    when there is no ceiling — so a query carrying it reads and performs
    exactly as before for the ordinary latest-filing case.
    """
    ceiling = _CEILING.get()
    if ceiling is None:
        return ""
    prefix = (alias + ".") if alias else ""
    return " AND %s%s <= %s" % (prefix, column, literal(ceiling))


def newest(alias, table, key="edinet_code", ok="", period="period_end", filed="filed_date"):
    """` AND NOT EXISTS (…)`: the row aliased `alias` is the newest filing of
    its company and fiscal year that existed by the ceiling — a correction
    (訂正有価証券報告書) shadows the report it corrects, and before the
    correction was filed the report stands. `ok` is a condition on the other
    filing (alias newer), e.g. "newer.status IN ('clean','partial')", so a correction
    that failed to parse shadows nothing. For per-year series; a latest view
    already ranks by period_end, filed_date. `period` and `filed` name the
    table's own columns (a buyback report: as_of, submitted)."""
    a = alias
    return (" AND NOT EXISTS (SELECT 1 FROM {t} newer WHERE newer.{k} = {a}.{k} AND newer.{p} = {a}.{p}"
            " AND (newer.{f} > {a}.{f} OR (newer.{f} = {a}.{f} AND newer.doc_id > {a}.doc_id)){ok}{asof})"
            .format(t=table, k=key, a=a, p=period, f=filed, ok=(" AND " + ok) if ok else "",
                    asof=clause(filed, "newer")))


def vintage(unit="filing"):
    """The `vintage` block a point-in-time response carries."""
    ceiling = _CEILING.get()
    return {
        "unit": unit,
        "basis": BASIS,
        "as_of": ceiling.isoformat() if ceiling else None,
        "note": BASIS_NOTE if ceiling else (
            "The latest accepted filing per company. Pass ?as_of=YYYY-MM-DD "
            "for the filings that existed on EDINET by that date."),
    }
