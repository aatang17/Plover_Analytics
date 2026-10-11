# -*- coding: utf-8 -*-
"""A publication's coverage list: the companies it follows, like a portfolio.

Its editors keep the list in the Writer Desk (Coverage tab). The list shows,
for each company, its next scheduled results date (JPX earnings calendar,
app/calendar_api.py) and its latest results release (TDnet,
app/earnings_api.py), so the people writing there see what is coming and what
has just come out. The writing assistant is told the list in every run on the
publication's articles, and reaches the details through Plover's own company
tools, never through anything this module stores.

The list itself is a small JSON array in staff.db's team_settings
("pub.coverage@<id>"), beside the rest of the publication's profile
(app/pub_profile.py). Codes are Tokyo securities codes (four characters).
Python 3.9: no 3.10+ syntax.
"""
import datetime
import json
import re

from . import staff

MAX_COMPANIES = 100
_CODE = re.compile(r"^[0-9A-Z]{4}$")
CALENDAR_DAYS = 119          # calendar_api allows at most 120 days per request
JST = datetime.timezone(datetime.timedelta(hours=9))


class CoverageError(ValueError):
    """A change refused, worded for the editor."""


def _key(publication_id):
    return "pub.coverage@%d" % int(publication_id)


def companies(publication_id):
    """[{code, name}] in the order they were added."""
    try:
        rows = json.loads(staff.team_setting(_key(publication_id)) or "[]")
    except ValueError:
        rows = []
    return [r for r in rows if isinstance(r, dict) and _CODE.match(str(r.get("code") or ""))]


def _save(publication_id, rows):
    staff.set_team_setting(_key(publication_id), json.dumps(rows, ensure_ascii=False))


def add(publication_id, code, name):
    code = (code or "").strip().upper()
    if not _CODE.match(code):
        raise CoverageError("A securities code is four characters, such as 7203.")
    if not name:
        raise CoverageError("No listed company has the code %s." % code)
    rows = companies(publication_id)
    if any(r["code"] == code for r in rows):
        return rows
    if len(rows) >= MAX_COMPANIES:
        raise CoverageError("A publication can follow up to %d companies." % MAX_COMPANIES)
    rows.append({"code": code, "name": name})
    _save(publication_id, rows)
    return rows


def remove(publication_id, code):
    code = (code or "").strip().upper()
    rows = [r for r in companies(publication_id) if r["code"] != code]
    _save(publication_id, rows)
    return rows


def prompt_line(publication_id):
    """One line for the writing assistant's instructions, or ""."""
    rows = companies(publication_id)
    if not rows:
        return ""
    return ("The publication covers these companies (Tokyo codes): %s. When the instruction is about "
            "its coverage, these are the companies meant; look them up with Plover's company tools.\n\n"
            % "; ".join("%s %s" % (r["code"], r["name"]) for r in rows))


# -------------------------------------------------------------- tracking

def _next_results(codes):
    """{code: {date, period}} for each company's next scheduled announcement."""
    from . import calendar_api
    today = datetime.datetime.now(JST).date()
    try:
        res = calendar_api.upcoming(start=today.isoformat(),
                                    end=(today + datetime.timedelta(days=CALENDAR_DAYS)).isoformat(),
                                    codes=",".join(codes), market="", as_of="")
    except Exception:  # noqa: BLE001 — no calendar on this server: the column shows "—"
        return {}
    out = {}
    for ev in res.get("events") or []:
        code = ev.get("sec_code")
        if code and code not in out:
            out[code] = {"date": str(ev.get("announce_date"))[:10],
                         "period": ev.get("period_type") or ev.get("period_type_raw")}
    return out


def _latest_results(codes):
    """{code: {date, title}} for each company's newest results release."""
    from . import earnings_api
    from .equity_api import _rows
    try:
        cur = earnings_api._require()
        rows = _rows(cur, earnings_api.releases_cte() + """
            SELECT sec_code, filed_date, title,
                   row_number() OVER (PARTITION BY sec_code
                                      ORDER BY filed_date DESC, filed_time DESC) AS k
            FROM rel WHERE sec_code IN (%s)""" % ",".join("?" for _ in codes), list(codes))
    except Exception:  # noqa: BLE001 — no earnings data on this server: "—"
        return {}
    return dict((r["sec_code"], {"date": str(r["filed_date"])[:10], "title": r["title"]})
                for r in rows if r["k"] == 1)


def view(publication_id):
    rows = companies(publication_id)
    codes = [r["code"] for r in rows]
    nxt = _next_results(codes) if codes else {}
    last = _latest_results(codes) if codes else {}
    return {"companies": [dict(r, next_results=nxt.get(r["code"]), latest_results=last.get(r["code"]))
                          for r in rows],
            "max": MAX_COMPANIES,
            "sources": {"next_results": "JPX earnings announcement schedule",
                        "latest_results": "TDnet earnings releases"}}
