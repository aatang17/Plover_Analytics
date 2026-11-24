"""The signed-out preview of data dashboards, separate from answer limits."""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import time
from contextlib import closing
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from . import accounts, seo, visits

LIMIT = 3
COOKIE = "pa_page_preview"
MAX_AGE = 365 * 24 * 60 * 60
PRIVATE = "private, no-store"

# Account screens, documentation and shared research material do not use a preview.
PUBLIC_PAGES = {
    "admin.html", "api.html", "assistant.html", "clip.html", "connect.html",
    "contact.html", "datasets.html", "desk.html", "index.html", "manual.html",
    "methodology.html", "my.html", "privacy.html", "researchers.html", "save.html",
    "signin.html", "snapshot.html", "terms.html", "write.html",
}


def applies(page):
    return (accounts.enabled() and accounts.allowed_emails() is None
            and page.endswith(".html") and page not in PUBLIC_PAGES
            and not seo._VERIFICATION.fullmatch(page))


def _open_preview(cookie, page):
    """Return (allowed, new cookie). A transaction shares the limit across tabs and servers."""
    path = accounts.DB_PATH.with_name("page-previews.sqlite")
    now = int(time.time())
    cookie = cookie if re.fullmatch(r"[\w-]{32}", cookie or "", flags=re.ASCII) else ""
    visitor = hashlib.sha256(cookie.encode()).hexdigest()
    with closing(sqlite3.connect(str(path), timeout=2)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS previews ("
                     "visitor TEXT PRIMARY KEY, pages TEXT NOT NULL, expires_at INTEGER NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS previews_expiry ON previews (expires_at)")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT pages FROM previews WHERE visitor = ? AND expires_at > ?",
                           (visitor, now)).fetchone() if cookie else None
        if row is None:
            cookie = secrets.token_urlsafe(24)
            visitor = hashlib.sha256(cookie.encode()).hexdigest()
            conn.execute("DELETE FROM previews WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO previews VALUES (?, ?, ?)",
                         (visitor, json.dumps([page]), now + MAX_AGE))
            return True, cookie

        pages = json.loads(row[0])
        if page in pages:
            return True, None
        if len(pages) >= LIMIT:
            return False, None
        pages.append(page)
        conn.execute("UPDATE previews SET pages = ? WHERE visitor = ?",
                     (json.dumps(pages), visitor))
        return True, None


def apply(page, scope, response):
    response.headers["Cache-Control"] = PRIVATE
    request = Request(scope)
    if request.method != "GET" or visits._is_bot(request.headers.get("user-agent", "")):
        return response
    if accounts.session_is_live(request.cookies.get(accounts.COOKIE)):
        return response
    try:
        allowed, new_cookie = _open_preview(request.cookies.get(COOKIE), page)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Could not check data page previews")
        return response

    if not allowed:
        target = "/" + page
        if request.url.query:
            target += "?" + request.url.query
        query = urlencode({"reason": "page-preview", "returnTo": target})
        return RedirectResponse("/signin.html?" + query, status_code=302,
                                headers={"Cache-Control": PRIVATE})
    if new_cookie:
        response.set_cookie(COOKIE, new_cookie, max_age=MAX_AGE, httponly=True,
                            samesite="lax", secure=seo.is_https(request))
    return response
