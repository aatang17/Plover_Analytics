# -*- coding: utf-8 -*-
"""Demo accounts: a Your Plover account with more AI questions, for one month.

What a demo is
--------------
An ordinary reader account (app/accounts.py: one email, a sign-in link, no
password) plus a record here saying that, until ``ends_at``, the account gets
DEMO_TRIES questions a day on Plover's AI key instead of the usual FREE_TRIES
(app/assistant/free.py). The demo's questions are not stopped by the site-wide
daily cap, so a tester is never told "come back tomorrow" because strangers
used the day's questions; they are still counted in it.

When the month is over nothing is deleted: the account carries on as a normal
free account, with its watchlist, chats and reading kept. If the server has an
invite list (ACCOUNTS_ALLOWED_EMAILS), a demo address is let in only while the
demo runs, and after that only if the list names it.

Made from the Admin Console (Members page, Team permission). The record is
keyed by address, not account, so a demo can be made before the person has
ever signed in; at most one runs per address at a time.

Where it lives
--------------
A table in the accounts store (data/workspace.db), the same SQLite file and
lock as the accounts themselves.
"""
import datetime
import os
import time

from fastapi import HTTPException

from . import accounts

DEMO_DAYS = 30
DEFAULT_TRIES = 100
NOTE_MAX = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  note TEXT,
  created_by TEXT,
  created_at INTEGER NOT NULL,
  ends_at INTEGER NOT NULL,
  ended_at INTEGER,
  ended_by TEXT
);
CREATE INDEX IF NOT EXISTS demo_accounts_email ON demo_accounts (email, ends_at);
"""

# the accounts store's base connection the table was made on; a new one (another
# file, as in tests) makes it again
_ready = [None]


class DemoError(Exception):
    """str() is worded for the person in the Admin Console."""


def tries():
    """Questions a day on Plover's key while a demo runs (DEMO_TRIES, default 100)."""
    try:
        return max(0, int(str(os.environ.get("DEMO_TRIES", "")).strip() or DEFAULT_TRIES))
    except ValueError:
        return DEFAULT_TRIES


def _db():
    db = accounts.conn()
    if _ready[0] is not accounts._conn:
        with accounts._lock:
            db.executescript(SCHEMA)
            db.commit()
        _ready[0] = accounts._conn
    return db


def _now():
    return int(time.time())


def _iso(epoch):
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(row, now):
    if row["ended_at"]:
        return "ended"
    return "active" if row["ends_at"] > now else "expired"


def _public(row, now):
    status = _status(row, now)
    left = max(0, row["ends_at"] - now)
    return {"id": row["id"], "email": row["email"], "note": row["note"] or "",
            "created_by": row["created_by"], "created_at": _iso(row["created_at"]),
            "ends_at": _iso(row["ended_at"] if status == "ended" else row["ends_at"]),
            "status": status,
            # whole days, rounded up: a demo with three hours left says 1 day
            "days_left": (left + 86399) // 86400 if status == "active" else 0}


def _normalise(email):
    try:
        return accounts._normalise(email)
    except HTTPException:
        raise DemoError("Enter the tester's email address, like name@company.com.")


def active_for_email(email, now=None):
    """The running demo for an address, as a row, or None."""
    if not accounts.enabled():
        return None
    now = now or _now()
    return _db().execute("SELECT * FROM demo_accounts WHERE email = ? AND ended_at IS NULL "
                         "AND ends_at > ? ORDER BY ends_at DESC LIMIT 1",
                         ((email or "").strip().lower(), now)).fetchone()


def active_for_account(account_id):
    """The running demo for a reader account id, or None."""
    if not accounts.enabled() or not account_id:
        return None
    row = accounts.conn().execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return active_for_email(row["email"]) if row else None


def demo_info(account_id):
    """{"ends_at", "days_left", "tries"} while the account's demo runs, else None."""
    row = active_for_account(account_id)
    if row is None:
        return None
    pub = _public(row, _now())
    return {"ends_at": pub["ends_at"], "days_left": pub["days_left"], "tries": tries()}


def create(email, note, by):
    """Start a 30-day demo for an address. Refuses a second one while one runs."""
    email = _normalise(email)
    note = (note or "").strip()[:NOTE_MAX]
    now = _now()
    db = _db()
    with accounts._lock:
        running = db.execute("SELECT ends_at FROM demo_accounts WHERE email = ? AND ended_at IS NULL "
                             "AND ends_at > ?", (email, now)).fetchone()
        if running:
            raise DemoError("%s already has a demo, running until %s. Use New Sign-in Link to send "
                            "them another link." % (email, _iso(running["ends_at"])[:10]))
        cur = db.execute("INSERT INTO demo_accounts (email, note, created_by, created_at, ends_at) "
                         "VALUES (?, ?, ?, ?, ?)", (email, note, by, now, now + DEMO_DAYS * 86400))
        db.commit()
        row = db.execute("SELECT * FROM demo_accounts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _public(row, now)


def get(demo_id):
    row = _db().execute("SELECT * FROM demo_accounts WHERE id = ?", (demo_id,)).fetchone()
    return _public(row, _now()) if row else None


def end(demo_id, by):
    """Stop a running demo now. The account stays, as a normal free one."""
    now = _now()
    db = _db()
    with accounts._lock:
        row = db.execute("SELECT * FROM demo_accounts WHERE id = ?", (demo_id,)).fetchone()
        if row is None:
            raise DemoError("That demo account no longer exists. Reload the page.")
        if _status(row, now) != "active":
            raise DemoError("That demo has already finished.")
        db.execute("UPDATE demo_accounts SET ended_at = ?, ended_by = ? WHERE id = ?", (now, by, demo_id))
        db.commit()
        row = db.execute("SELECT * FROM demo_accounts WHERE id = ?", (demo_id,)).fetchone()
    return _public(row, now)


def list_all():
    """Every demo, newest first, with whether the person has signed in yet."""
    now = _now()
    rows = _db().execute(
        "SELECT d.*, a.last_seen_at AS last_seen_at FROM demo_accounts d "
        "LEFT JOIN accounts a ON a.email = d.email ORDER BY d.created_at DESC, d.id DESC").fetchall()
    out = []
    for r in rows:
        pub = _public(r, now)
        # a sign-in during this demo, not one from before it was made
        seen = r["last_seen_at"]
        pub["last_seen"] = _iso(seen) if seen and seen >= r["created_at"] else None
        out.append(pub)
    return out


def _admit(email):
    """accounts.EXTRA_ALLOWED hook: a running demo may sign in past an invite list."""
    return active_for_email(email) is not None


if _admit not in accounts.EXTRA_ALLOWED:
    accounts.EXTRA_ALLOWED.append(_admit)
