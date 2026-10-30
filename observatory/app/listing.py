# -*- coding: utf-8 -*-
u"""Is a Japanese company still listed?

A company that leaves the market keeps its filings here, and it should: its
history is the point-in-time record, and dropping it would bias every study
towards the survivors. But a reader looking for a company today has to be told
it is gone. Toshiba (6502) was the case that showed the gap: it was offered in
every search box, and could be added to a list, as if it still traded.

Delisted means two sources agree:

  * EDINET's filer registry no longer has a listed entity under the code
    (上場区分, kept in eq_entities). It is the only source that knows the regional
    exchanges, but the file is refreshed by hand, so it can be weeks behind.
  * JPX's list of Tokyo issues (eq_classification, newest vintage) does not have
    the code. It is monthly, but says nothing about Nagoya, Fukuoka or Sapporo.

JPX alone would call 142 regional-exchange companies delisted (RIZAP, on
Sapporo). The registry alone is right but could never be checked. With either
source missing nobody is marked: an unknown status is not a delisting.

This is today's status, so a view frozen at a past date (?as_of=) carries none.
"""
import functools
import logging

from . import asof, cohorts

log = logging.getLogger(__name__)


def listed_codes(cur):
    """Codes either source still has listed, or None when a source is missing."""
    if not cohorts.available(cur):
        return None
    v = cohorts.vintage(cur, "jpx-listed")
    if not v:
        return None
    registry = cur.execute("SELECT DISTINCT sec_code FROM eq_entities "
                           "WHERE listed AND sec_code IS NOT NULL").fetchall()
    jpx = cur.execute("SELECT DISTINCT sec_code FROM eq_classification "
                      "WHERE vintage_id = ?", [v[0]]).fetchall()
    if not registry or not jpx:
        return None
    return set(r[0] for r in registry) | set(r[0] for r in jpx)


def _listed_now():
    """listed_codes on the shared connection; None rather than an error, since
    a status we cannot read must never take a search box down with it."""
    if asof.current():
        return None
    try:
        from . import equity_api
        return listed_codes(equity_api._cur())
    except Exception as e:  # noqa: BLE001
        log.warning("listing status unavailable: %s", e)
        return None


def is_delisted(code):
    listed = _listed_now()
    return bool(listed and code and code not in listed)


def mark(rows, key="sec_code"):
    """Flag each delisted row `delisted: true` and move those rows last.
    Listed companies come first; the order is otherwise kept."""
    listed = _listed_now()
    if not listed:
        return rows
    for r in rows:
        if r.get(key) and r[key] not in listed:
            r["delisted"] = True
    return sorted(rows, key=lambda r: bool(r.get("delisted")))


def marked(fn):
    """For a search feed returning {"companies": [...]}: mark those rows."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        out = fn(*args, **kwargs)
        out["companies"] = mark(out.get("companies") or [])
        return out
    return wrapper
