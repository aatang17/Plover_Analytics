"""Accounts: an email sign-in link, a session, and nothing else.

What an account is for
----------------------
It holds the companies a reader follows. Every dataset on this platform stays
readable without one, so nothing here gates data — a failure in this module
costs a reader their list, never the numbers.

What is deliberately absent
---------------------------
**Passwords.** Signing in and signing up are one action: the reader gives an
address, we email a single-use link, and clicking it both proves the address
and creates the session. There is no hash to leak, no reset flow to abuse, and
no second form for the reader to work out.

Where the data lives
--------------------
A **SQLite file of its own** on the `data/` volume, never the DuckDB datasets.
Ingest guardrail 5 says the serving process must not write the dataset files;
this is a different engine writing a different file, so both remain true, and a
dataset rebuild cannot touch a reader's account. WAL mode, because the API
process handles requests concurrently.

The kill switch
---------------
`ACCOUNTS_ENABLED` must be truthy or `main.py` does not mount this router at
all. Every account endpoint then 404s, and the sign-in page reads that as "not
switched on" and says so rather than showing a form that cannot work.

Delivery
--------
Resend's HTTP API over stdlib urllib — no new dependency. Without
`RESEND_API_KEY` the module is in **development delivery**: the link is written
to the server log instead of sent, so the whole flow is testable before any
mail leaves the building. `ACCOUNTS_DEV_LINKS` additionally returns the link in
the response, which is for a laptop and must never be set in production.

Tokens
------
32 bytes from `secrets`, single use, thirty minutes. Only the SHA-256 of a
token is stored, so the database cannot be replayed into a session. A session
cookie is the same shape: a random token, stored as a hash, HttpOnly and
SameSite=Lax, thirty days, refreshed as it is used.
"""
import hashlib
import logging
import os
import pathlib
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import mailer

router = APIRouter(prefix="/api/v1/account", tags=["account"])

DB_PATH = pathlib.Path(os.environ.get("WORKSPACE_DB") or
                       pathlib.Path(__file__).resolve().parent.parent / "data" / "workspace.db")

COOKIE = "pa_session"
LINK_TTL_SECONDS = 30 * 60
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
# A person asking for a link five times in an hour has a delivery problem, not
# an access problem; a host asking forty times is not a person.
LINKS_PER_EMAIL_HOUR = 5
LINKS_PER_IP_HOUR = 40
EMAIL_MAX = 254
# Deliberately loose: an address is proved by the link arriving, not by a
# regular expression's opinion of it. This only rejects what cannot be an
# address at all.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

_lock = threading.Lock()
_conn = None           # the connection that created the schema; None = not opened yet
_local = threading.local()


def enabled():
    """The kill switch. Nothing in this module runs unless it is set."""
    return str(os.environ.get("ACCOUNTS_ENABLED", "")).strip().lower() in (
        "1", "true", "yes", "on")


def allowed_emails():
    """`ACCOUNTS_ALLOWED_EMAILS`: a comma-separated invite list, or None when
    sign-in is open to anyone. While it is set, only these addresses get a
    link or keep a session, and the public header shows no "Sign in" — a
    visitor cannot use a feature they were not invited to."""
    raw = str(os.environ.get("ACCOUNTS_ALLOWED_EMAILS", "")).strip()
    if not raw:
        return None
    # plain lower-casing: a typo in the setting must not break every request
    return set(e.strip().lower() for e in raw.split(",") if e.strip())


# Other parts of the app that let people in besides the invite list: each is
# called with a lower-cased address and returns True to admit it. The research
# desk registers one so that a publication's members can sign in to write
# without a redeploy to change ACCOUNTS_ALLOWED_EMAILS (app/writers.py).
EXTRA_ALLOWED = []


def _allowed(email):
    allow = allowed_emails()
    if allow is None:
        return True
    email = (email or "").strip().lower()
    if email in allow:
        return True
    for admit in EXTRA_ALLOWED:
        try:
            if admit(email):
                return True
        except Exception:                                    # noqa: BLE001
            logging.getLogger(__name__).exception("sign-in allow hook failed")
    return False


def dev_links():
    """Return the link in the response — a laptop convenience, never production."""
    return str(os.environ.get("ACCOUNTS_DEV_LINKS", "")).strip().lower() in (
        "1", "true", "yes", "on")


# ---------------------------------------------------------------- the store

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER
);
CREATE TABLE IF NOT EXISTS signin_tokens (
  token_hash TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  return_to TEXT,
  ip TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);
CREATE INDEX IF NOT EXISTS signin_tokens_email ON signin_tokens (email, created_at);
CREATE INDEX IF NOT EXISTS signin_tokens_ip ON signin_tokens (ip, created_at);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen_at INTEGER
);
CREATE INDEX IF NOT EXISTS sessions_account ON sessions (account_id);
"""


def _open(path):
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=4000")
    return c


def conn():
    """This thread's connection. One connection shared by every request thread
    crashed the server inside libsqlite3 (segfault / abort) when two requests
    read at once, 24 Sep 2026; each thread now opens its own and WAL lets them
    read side by side. Writes still take _lock."""
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = _open(DB_PATH)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        base = _conn
    if getattr(_local, "base", None) is not base:
        _local.base, _local.conn = base, _open(DB_PATH)
    return _local.conn


def _now():
    return int(time.time())


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _normalise(email):
    email = (email or "").strip().lower()
    if len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise HTTPException(400, "Enter an email address we can send the link to.")
    return email


def _safe_return_to(raw):
    """Same-site relative paths only — never an absolute or protocol-relative URL."""
    raw = (raw or "").strip()
    if not raw or raw.startswith("//") or "://" in raw:
        return "index.html"
    if raw.startswith("/"):
        return raw
    if re.match(r"^[A-Za-z0-9._-]+\.html(\?[^#\s]*)?$", raw):
        return raw
    return "index.html"


# ------------------------------------------------------------------ mailing

def _base_url(request):
    """Where the emailed link points. The deployment may state it; otherwise
    the request says where the reader already is."""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/") + "/"
    return str(request.base_url)


def _send_email(to_address, link):
    """Hand the link to Resend, or log it when no key is configured.

    Returns "sent" or "logged". Raises HTTPException(503) if a key is present
    and delivery fails: a reader told to check an inbox that will never receive
    anything has no way back in and no way to tell us.
    """
    try:
        return mailer.send(
            to_address,
            "Your Plover Analytics sign-in link",
            ("Here is your sign-in link. It works once and expires in 30 minutes.\n\n"
             + link + "\n\nIf you did not ask to sign in, ignore this email — "
             "nothing has changed.\n\nPlover Analytics\n"),
            html=('<p>Here is your sign-in link. It works once and expires in 30 minutes.</p>'
                  '<p><a href="' + link + '">Sign in to Plover Analytics</a></p>'
                  '<p style="color:#64748b;font-size:13px">If you did not ask to sign in, '
                  'ignore this email — nothing has changed.</p>'))
    except Exception:                                        # noqa: BLE001
        # mailer has already logged the provider's reason.
        raise HTTPException(503, "We could not send the email just now. "
                                 "Try again in a few minutes.")


# A link handed over by a person instead of emailed: an owner invites a writer
# and sends the link themselves, which works before any mail provider is set
# up. Longer-lived than an emailed link because it travels by hand.
INVITE_TTL_SECONDS = 72 * 3600


def issue_link(email, return_to, base_url, ttl=INVITE_TTL_SECONDS):
    """A single-use sign-in link for ``email``, not sent anywhere. Like an
    emailed one it cancels the address's earlier unused links."""
    email = _normalise(email)
    token = secrets.token_urlsafe(32)
    return_to = _safe_return_to(return_to)
    now = _now()
    db = conn()
    with _lock:
        db.execute("UPDATE signin_tokens SET used_at = ? WHERE email = ? AND used_at IS NULL",
                   (now, email))
        db.execute("INSERT INTO signin_tokens (token_hash, email, return_to, ip, created_at, "
                   "expires_at) VALUES (?, ?, ?, 'invite', ?, ?)",
                   (_hash(token), email, return_to, now, now + ttl))
        db.commit()
    return (base_url.rstrip("/") + "/signin.html?token=" + urllib.parse.quote(token) +
            "&returnTo=" + urllib.parse.quote(return_to))


def base_url(request):
    return _base_url(request)


def last_seen(email):
    """When an address last used an email-link session, or None."""
    if not enabled():
        return None
    row = conn().execute("SELECT last_seen_at FROM accounts WHERE email = ?",
                         ((email or "").strip().lower(),)).fetchone()
    return row["last_seen_at"] if row else None


# ----------------------------------------------------------------- sessions

def _cookie_kwargs(request):
    secure = request.url.scheme == "https"
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}


def current_account(request):
    """The signed-in account as a dict, or None. The one function other
    routers should use; it also refreshes the session's sliding expiry."""
    if not enabled():
        return None
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    now = _now()
    cur = conn().execute(
        "SELECT s.token_hash, a.id, a.email FROM sessions s "
        "JOIN accounts a ON a.id = s.account_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?", (_hash(raw), now))
    row = cur.fetchone()
    if row is None:
        return None
    # an address taken off the invite list loses its open sessions too
    if not _allowed(row["email"]):
        return None
    # conn() takes the same lock, so the handle is taken before it is held:
    # threading.Lock is not reentrant and calling conn() inside `with _lock`
    # deadlocks the request instead of failing.
    db = conn()
    with _lock:
        db.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                   (now, now + SESSION_TTL_SECONDS, row["token_hash"]))
        db.execute("UPDATE accounts SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
        db.commit()
    return {"id": row["id"], "email": row["email"]}


# ------------------------------------------------------------------- routes

class LinkRequest(BaseModel):
    email: str
    returnTo: str = ""


class SessionRequest(BaseModel):
    token: str


@router.post("/signin-link")
def signin_link(payload: LinkRequest, request: Request):
    """Email a single-use sign-in link. Creating an account and signing in are
    the same action, so this endpoint does not care whether the address is
    already known — the link decides."""
    email = _normalise(payload.email)
    if not _allowed(email):
        raise HTTPException(403, "Sign-in is by invitation only for now.")
    ip = _client_ip(request)
    now = _now()
    hour_ago = now - 3600

    db = conn()
    by_email = db.execute("SELECT count(*) FROM signin_tokens WHERE email = ? AND created_at > ?",
                          (email, hour_ago)).fetchone()[0]
    if by_email >= LINKS_PER_EMAIL_HOUR:
        raise HTTPException(429, "That address has asked for several links in the last hour. "
                                 "Use the most recent one, or try again later.")
    by_ip = db.execute("SELECT count(*) FROM signin_tokens WHERE ip = ? AND created_at > ?",
                       (ip, hour_ago)).fetchone()[0]
    if by_ip >= LINKS_PER_IP_HOUR:
        raise HTTPException(429, "Too many sign-in requests from this network. "
                                 "Try again later.")

    token = secrets.token_urlsafe(32)
    return_to = _safe_return_to(payload.returnTo)
    with _lock:
        # Asking for a new link cancels the ones before it, as the page says.
        db.execute("UPDATE signin_tokens SET used_at = ? "
                   "WHERE email = ? AND used_at IS NULL", (now, email))
        db.execute("INSERT INTO signin_tokens "
                   "(token_hash, email, return_to, ip, created_at, expires_at) "
                   "VALUES (?, ?, ?, ?, ?, ?)",
                   (_hash(token), email, return_to, ip, now, now + LINK_TTL_SECONDS))
        db.commit()

    link = (_base_url(request) + "signin.html?token=" + urllib.parse.quote(token) +
            "&returnTo=" + urllib.parse.quote(return_to))
    delivery = _send_email(email, link)
    out = {"delivery": delivery, "expires_in_minutes": LINK_TTL_SECONDS // 60}
    if delivery == "logged" and dev_links():
        out["link"] = link
    return out


@router.post("/session")
def create_session(payload: SessionRequest, request: Request, response: Response):
    """Redeem a link. This is also where an account is created: the address is
    proved by the reader holding a token we only ever sent to it."""
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(400, "That sign-in link is missing its token.")
    now = _now()
    db = conn()
    row = db.execute("SELECT email, return_to, expires_at, used_at FROM signin_tokens "
                     "WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] <= now:
        raise HTTPException(401, "That sign-in link has been used already or has expired.")

    email = row["email"]
    session_token = secrets.token_urlsafe(32)
    with _lock:
        db.execute("UPDATE signin_tokens SET used_at = ? WHERE token_hash = ?",
                   (now, _hash(token)))
        db.execute("INSERT OR IGNORE INTO accounts (email, created_at) VALUES (?, ?)",
                   (email, now))
        account_id = db.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()["id"]
        db.execute("INSERT INTO sessions (token_hash, account_id, created_at, expires_at, last_seen_at) "
                   "VALUES (?, ?, ?, ?, ?)",
                   (_hash(session_token), account_id, now, now + SESSION_TTL_SECONDS, now))
        db.execute("UPDATE accounts SET last_seen_at = ? WHERE id = ?", (now, account_id))
        db.commit()

    response.set_cookie(COOKIE, session_token, max_age=SESSION_TTL_SECONDS,
                        **_cookie_kwargs(request))
    return {"email": email, "returnTo": row["return_to"] or "index.html"}


@router.get("/me")
def me(request: Request):
    """Who is signed in. 401 when nobody is — the front end reads 404 (this
    router absent) as "accounts are not switched on", which is a different
    thing and must stay distinguishable."""
    account = current_account(request)
    if account is None:
        # invite_only tells the header not to offer "Sign in" to the public
        return JSONResponse({"detail": "Not signed in.",
                             "invite_only": allowed_emails() is not None}, status_code=401)
    return dict({"email": account["email"]}, **_desks(account["email"]))


# Admin Console permissions; "writing" alone makes a writer, not an admin.
ADMIN_PERMISSIONS = ("team", "operations", "classification")


def _desks(email):
    """Which desks the header offers this reader beside Your Plover: the
    Writer Desk to someone who writes for a publication, the Admin Console to
    someone on the Plover team. Only links: each desk still checks for itself."""
    out = {"writer": False, "admin": False}
    try:
        from . import staff, writers
        person = staff.get_by_email(email)
        if person is None or person["status"] != "active":
            return out
        out["admin"] = any(p in ADMIN_PERMISSIONS for p in person["permissions"])
        out["writer"] = bool(writers.roles(person))
    except Exception:  # noqa: BLE001 — a missing link is never worth failing /me
        pass
    return out


@router.post("/signout")
def signout(request: Request, response: Response):
    """Drop this session. Other devices keep theirs."""
    raw = request.cookies.get(COOKIE)
    if raw:
        db = conn()
        with _lock:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(raw),))
            db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}
