"""Staff accounts: one login per person for the admin console.

Why this exists
---------------
The console began as one shared password. That stopped being enough when a
second person started writing for the site: a shared password cannot say who
published an article, cannot take one person's access away without changing
everyone's, and — because its sessions are signed with a per-boot secret —
signs everybody out on every deploy, which for a writer mid-draft means lost
work. Each person now has an email, a password and a set of permissions.

Permissions
-----------
``team``            add people, change their permissions, issue setup links
``operations``      ingest health, release history, traffic, the audit log
``classification``  party profiles and the classification queue
``writing``         the research desk: write, edit and publish PloverResearch
                    articles (an editor of PloverResearch; with ``team``, its
                    owner). Other publications' writers are members there,
                    not permission holders — see app/writers.py

How a person gets in
--------------------
Somebody with ``team`` adds them and is shown a **setup link** — valid for
72 hours, used once. They send it themselves (chat, email, anything); the new
person opens it and chooses their own password. Nothing here sends mail, so
no mail provider is needed, and no password ever passes through a third
party. A forgotten password is the same move: a fresh setup link.

The shared password
-------------------
``ADMIN_PASSWORD`` keeps working, signed in as "Shared password" with every
permission. That is how the first real account gets created on a deployment
that has none, and it is the way back in if every owner is locked out. Once
the people who need access have their own accounts, remove the variable and
the shared login is gone. The Team page says whether it is still on.

Where it lives
--------------
A SQLite file of its own under ``data/admin/`` on the volume — never the
DuckDB datasets (ingest guardrail 5). Passwords are PBKDF2-SHA256 with a
per-password salt at 600,000 iterations, the stdlib function, so nothing new
is installed. Sessions and setup tokens are random and only their SHA-256 is
stored, so a copy of the file cannot be replayed into a login.
"""
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import sqlite3
import threading
import time

from . import db

DB_PATH = pathlib.Path(os.environ.get("STAFF_DB") or (db.DATA_DIR / "admin" / "staff.db"))

PERMISSIONS = [
    ("team", "Team"),
    ("operations", "Operations"),
    ("classification", "Classification"),
    ("writing", "Writing"),
]
PERMISSION_KEYS = [k for k, _ in PERMISSIONS]

PBKDF2_ITERATIONS = 600000
PASSWORD_MIN = 12
PASSWORD_MAX = 200
NAME_MAX = 80
EMAIL_MAX = 254
SETUP_TTL_SECONDS = 72 * 3600
# A writer leaves a tab open for days. The idle limit signs out a forgotten
# laptop; the absolute one makes every session end within a fortnight.
SESSION_IDLE_SECONDS = 3 * 24 * 3600
SESSION_MAX_SECONDS = 14 * 24 * 3600

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  password_hash TEXT,
  permissions TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at INTEGER NOT NULL,
  created_by TEXT,
  last_login_at INTEGER,
  password_set_at INTEGER
);
CREATE TABLE IF NOT EXISTS staff_sessions (
  token_hash TEXT PRIMARY KEY,
  staff_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  ip TEXT
);
CREATE INDEX IF NOT EXISTS staff_sessions_staff ON staff_sessions (staff_id);
CREATE TABLE IF NOT EXISTS staff_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  staff_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER,
  revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS staff_keys_staff ON staff_keys (staff_id);
CREATE TABLE IF NOT EXISTS staff_ai (
  staff_id INTEGER PRIMARY KEY,
  provider TEXT,
  model TEXT,
  key_ct TEXT,
  key_last4 TEXT,
  claude_token_ct TEXT,
  codex_auth_ct TEXT,
  codex_email TEXT,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS team_skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id INTEGER NOT NULL DEFAULT 1,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  writes INTEGER NOT NULL DEFAULT 1,
  position INTEGER NOT NULL DEFAULT 100,
  seed TEXT,
  created_at INTEGER,
  created_by TEXT,
  updated_at INTEGER,
  updated_by TEXT
);
CREATE TABLE IF NOT EXISTS team_connectors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  auth_kind TEXT NOT NULL DEFAULT 'none',
  header_name TEXT,
  secret_ct TEXT,
  secret_last4 TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  tools_json TEXT NOT NULL DEFAULT '[]',
  off_tools_json TEXT NOT NULL DEFAULT '[]',
  server_name TEXT,
  checked_at INTEGER,
  last_error TEXT,
  created_at INTEGER,
  created_by TEXT,
  updated_at INTEGER,
  updated_by TEXT
);
CREATE TABLE IF NOT EXISTS team_settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS staff_google (
  staff_id INTEGER PRIMARY KEY,
  email TEXT,
  scopes TEXT,
  ct TEXT NOT NULL,
  connected_at INTEGER,
  last_used_at INTEGER
);
CREATE TABLE IF NOT EXISTS staff_setup_tokens (
  token_hash TEXT PRIMARY KEY,
  staff_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  created_by TEXT,
  used_at INTEGER
);
"""

_lock = threading.Lock()
_conn = None
_local = threading.local()


class StaffError(ValueError):
    """A request that cannot be honoured, worded for the person who made it."""


# ---------------------------------------------------------------- the store

def _open(path):
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=4000")
    return c


def conn():
    """This thread's connection (one shared connection crashed libsqlite3
    under concurrent reads in accounts.py; the same fix applies here)."""
    global _conn
    with _lock:
        if _conn is None or getattr(_conn, "_path", None) != str(DB_PATH):
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            base = _open(DB_PATH)
            base.execute("PRAGMA journal_mode=WAL")
            base.executescript(SCHEMA)
            _add_columns(base)
            _skills_per_publication(base)
            base.commit()
            _conn = _Base(base, str(DB_PATH))
        base = _conn
    if getattr(_local, "base", None) is not base:
        _local.base, _local.conn = base, _open(DB_PATH)
    return _local.conn


# Columns added to a table after its first release: (table, column, definition).
LATER_COLUMNS = [
    ("team_skills", "writes", "INTEGER NOT NULL DEFAULT 1"),
    ("team_skills", "position", "INTEGER NOT NULL DEFAULT 100"),
    ("staff_ai", "length", "TEXT"),
    ("staff_ai", "style", "TEXT"),
    ("team_connectors", "publication_id", "INTEGER NOT NULL DEFAULT 1"),
]

# team_skills as it is now: a skill's slug is unique within its publication.
_SKILL_COLUMNS = ("id", "slug", "name", "description", "instructions", "enabled", "writes",
                  "position", "seed", "created_at", "created_by", "updated_at", "updated_by")


def _add_columns(c):
    for table, column, kind in LATER_COLUMNS:
        have = [r[1] for r in c.execute("PRAGMA table_info(%s)" % table).fetchall()]
        if column not in have:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))


def _skills_per_publication(c):
    """Skills became each publication's own (2026-10-10). A store from before
    has a slug unique across the site, which SQLite cannot drop, so the table
    is rebuilt once with every skill kept as PloverResearch's (publication 1)."""
    have = [r[1] for r in c.execute("PRAGMA table_info(team_skills)").fetchall()]
    if "publication_id" not in have:
        cols = ", ".join(_SKILL_COLUMNS)
        c.execute("ALTER TABLE team_skills RENAME TO team_skills_before_publications")
        c.executescript(SCHEMA)
        c.execute("INSERT INTO team_skills (%s, publication_id) SELECT %s, 1 "
                  "FROM team_skills_before_publications" % (cols, cols))
        c.execute("DROP TABLE team_skills_before_publications")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS team_skills_slug ON team_skills (publication_id, slug)")
    c.execute("CREATE INDEX IF NOT EXISTS team_connectors_pub ON team_connectors (publication_id)")


class _Base(object):
    """The schema-creating connection plus the path it was opened on, so a
    test that points DB_PATH elsewhere gets a fresh store."""

    def __init__(self, c, path):
        self.c = c
        self._path = path


def _now():
    return int(time.time())


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("ascii"), PBKDF2_ITERATIONS).hex()
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt, digest)


def verify_password(password, stored):
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        iterations = int(iterations)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt.encode("ascii"), iterations).hex()
    return hmac.compare_digest(test, digest)


# Verified against when the address is unknown, so a wrong email and a wrong
# password take the same time and the login cannot be used to list accounts.
_DUMMY_HASH = None


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


def normalise_email(email):
    return (email or "").strip().lower()


def _check_password(password, email=None):
    if not isinstance(password, str) or len(password) < PASSWORD_MIN:
        raise StaffError("Choose a password of at least %d characters." % PASSWORD_MIN)
    if len(password) > PASSWORD_MAX:
        raise StaffError("That password is longer than %d characters." % PASSWORD_MAX)
    if email and password.strip().lower() == normalise_email(email):
        raise StaffError("The password cannot be your email address.")


def _clean_permissions(perms):
    if not isinstance(perms, (list, tuple)):
        raise StaffError("Permissions must be a list.")
    unknown = [p for p in perms if p not in PERMISSION_KEYS]
    if unknown:
        raise StaffError("Unknown permission: %s." % ", ".join(map(str, unknown)))
    return [k for k in PERMISSION_KEYS if k in perms]


def _public(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "permissions": json.loads(row["permissions"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "last_login_at": row["last_login_at"],
        "password_set": bool(row["password_hash"]),
        "shared": False,
    }


def count():
    return conn().execute("SELECT count(*) FROM staff").fetchone()[0]


def get(staff_id):
    row = conn().execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return _public(row)


def get_by_email(email):
    row = conn().execute("SELECT * FROM staff WHERE email = ?",
                         (normalise_email(email),)).fetchone()
    return _public(row)


def list_all():
    rows = conn().execute("SELECT * FROM staff ORDER BY status, name COLLATE NOCASE").fetchall()
    return [_public(r) for r in rows]


def _active_team_ids(c):
    out = []
    for r in c.execute("SELECT id, permissions FROM staff WHERE status = 'active'"):
        if "team" in json.loads(r["permissions"]):
            out.append(r["id"])
    return out


def create(email, name, permissions, created_by):
    """A new person, with no password yet. Returns (person, setup_token)."""
    email = normalise_email(email)
    name = (name or "").strip()
    if not email or len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise StaffError("Enter a valid email address.")
    if not name or len(name) > NAME_MAX:
        raise StaffError("Enter a name of up to %d characters." % NAME_MAX)
    perms = _clean_permissions(permissions)
    c = conn()
    with _lock:
        if c.execute("SELECT 1 FROM staff WHERE email = ?", (email,)).fetchone():
            raise StaffError("%s already has an account." % email)
        cur = c.execute(
            "INSERT INTO staff (email, name, permissions, status, created_at, created_by) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (email, name, json.dumps(perms), _now(), created_by))
        staff_id = cur.lastrowid
        token = _issue_setup(c, staff_id, created_by)
        c.commit()
    return get(staff_id), token


def ensure_person(email, name, created_by):
    """The person with this address, adding them with no permissions and no
    password when they are new. This is how a writer invited to a publication
    gets a record of their own (their AI settings and keys hang off it); they
    sign in by email link, and nothing here opens the admin console to them."""
    email = normalise_email(email)
    if not email or len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise StaffError("Enter a valid email address.")
    existing = get_by_email(email)
    if existing is not None:
        if existing["status"] != "active":
            raise StaffError("%s has a disabled account. Turn it back on in the Admin Console's "
                             "Team page first." % email)
        return existing
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise StaffError("Enter a name of up to %d characters." % NAME_MAX)
    c = conn()
    with _lock:
        c.execute("INSERT OR IGNORE INTO staff (email, name, permissions, status, created_at, "
                  "created_by) VALUES (?, ?, '[]', 'active', ?, ?)",
                  (email, name, _now(), created_by))
        c.commit()
    return get_by_email(email)


def _issue_setup(c, staff_id, created_by):
    token = secrets.token_urlsafe(32)
    now = _now()
    # one live link per person: issuing a new one retires the old
    c.execute("UPDATE staff_setup_tokens SET used_at = ? "
              "WHERE staff_id = ? AND used_at IS NULL", (now, staff_id))
    c.execute("INSERT INTO staff_setup_tokens (token_hash, staff_id, created_at, expires_at, "
              "created_by) VALUES (?, ?, ?, ?, ?)",
              (_hash_token(token), staff_id, now, now + SETUP_TTL_SECONDS, created_by))
    return token


def issue_setup(staff_id, created_by):
    c = conn()
    with _lock:
        if not c.execute("SELECT 1 FROM staff WHERE id = ?", (staff_id,)).fetchone():
            raise StaffError("No such person.")
        token = _issue_setup(c, staff_id, created_by)
        c.commit()
    return token


def setup_target(token):
    """Who a setup link is for, or None if it is unknown, used or expired."""
    row = conn().execute(
        "SELECT s.* FROM staff_setup_tokens t JOIN staff s ON s.id = t.staff_id "
        "WHERE t.token_hash = ? AND t.used_at IS NULL AND t.expires_at > ? "
        "AND s.status = 'active'", (_hash_token(token or ""), _now())).fetchone()
    return _public(row)


def complete_setup(token, password):
    """Set the password a setup link was issued for. Ends the person's other
    sessions: a reset is also how a lost laptop is locked out."""
    person = setup_target(token)
    if person is None:
        raise StaffError("This setup link has expired or has already been used. "
                         "Ask for a new one.")
    _check_password(password, person["email"])
    c = conn()
    now = _now()
    with _lock:
        c.execute("UPDATE staff SET password_hash = ?, password_set_at = ? WHERE id = ?",
                  (hash_password(password), now, person["id"]))
        c.execute("UPDATE staff_setup_tokens SET used_at = ? WHERE staff_id = ? "
                  "AND used_at IS NULL", (now, person["id"]))
        c.execute("DELETE FROM staff_sessions WHERE staff_id = ?", (person["id"],))
        c.commit()
    return get(person["id"])


def authenticate(email, password):
    """The person, or None. Same work whether or not the address exists."""
    row = conn().execute("SELECT * FROM staff WHERE email = ?",
                         (normalise_email(email),)).fetchone()
    stored = row["password_hash"] if row is not None and row["password_hash"] else None
    ok = verify_password(password or "", stored or _dummy_hash())
    if row is None or stored is None or not ok or row["status"] != "active":
        return None
    return _public(row)


def change_password(staff_id, current, new, keep_session_hash=None):
    row = conn().execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if row is None or not verify_password(current or "", row["password_hash"] or _dummy_hash()):
        raise StaffError("Your current password is not right.")
    _check_password(new, row["email"])
    c = conn()
    now = _now()
    with _lock:
        c.execute("UPDATE staff SET password_hash = ?, password_set_at = ? WHERE id = ?",
                  (hash_password(new), now, staff_id))
        # other devices are signed out; this one stays in
        c.execute("DELETE FROM staff_sessions WHERE staff_id = ? AND token_hash != ?",
                  (staff_id, keep_session_hash or ""))
        c.commit()


def update(staff_id, actor_id, name=None, permissions=None, status=None):
    """Change a person. Refuses anything that would leave nobody able to
    manage the team."""
    c = conn()
    with _lock:
        row = c.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone()
        if row is None:
            raise StaffError("No such person.")
        new_name = row["name"] if name is None else (name or "").strip()
        if not new_name or len(new_name) > NAME_MAX:
            raise StaffError("Enter a name of up to %d characters." % NAME_MAX)
        perms = json.loads(row["permissions"]) if permissions is None \
            else _clean_permissions(permissions)
        new_status = row["status"] if status is None else status
        if new_status not in ("active", "disabled"):
            raise StaffError("Status must be active or disabled.")
        if staff_id == actor_id and new_status != "active":
            raise StaffError("You cannot disable your own account.")
        if staff_id == actor_id and "team" not in perms:
            raise StaffError("You cannot remove your own Team permission.")
        team = _active_team_ids(c)
        loses_team = staff_id in team and ("team" not in perms or new_status != "active")
        if loses_team and len(team) <= 1:
            raise StaffError("At least one active person must keep the Team permission.")
        c.execute("UPDATE staff SET name = ?, permissions = ?, status = ? WHERE id = ?",
                  (new_name, json.dumps(perms), new_status, staff_id))
        if new_status != "active":
            c.execute("DELETE FROM staff_sessions WHERE staff_id = ?", (staff_id,))
        c.commit()
    return get(staff_id)


# ------------------------------------------------------------------ sessions

def start_session(staff_id, ip):
    token = secrets.token_urlsafe(32)
    now = _now()
    c = conn()
    with _lock:
        c.execute("INSERT INTO staff_sessions (token_hash, staff_id, created_at, last_seen_at, ip) "
                  "VALUES (?, ?, ?, ?, ?)", (_hash_token(token), staff_id, now, now, ip))
        c.execute("UPDATE staff SET last_login_at = ? WHERE id = ?", (now, staff_id))
        c.commit()
    return token


def session_person(token):
    """The signed-in person for a session cookie, or None. Refreshes the idle
    clock at most once a minute, so reads do not turn into writes."""
    if not token:
        return None
    th = _hash_token(token)
    now = _now()
    row = conn().execute(
        "SELECT s.*, x.created_at AS session_created, x.last_seen_at AS session_seen "
        "FROM staff_sessions x JOIN staff s ON s.id = x.staff_id "
        "WHERE x.token_hash = ?", (th,)).fetchone()
    if row is None or row["status"] != "active":
        return None
    if now - row["session_seen"] > SESSION_IDLE_SECONDS or \
            now - row["session_created"] > SESSION_MAX_SECONDS:
        end_session(token)
        return None
    if now - row["session_seen"] > 60:
        c = conn()
        with _lock:
            c.execute("UPDATE staff_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, th))
            c.commit()
    person = _public(row)
    person["session_hash"] = th
    return person


def end_session(token):
    if not token:
        return
    c = conn()
    with _lock:
        c.execute("DELETE FROM staff_sessions WHERE token_hash = ?", (_hash_token(token),))
        c.commit()


# ------------------------------------------------------------- personal keys
#
# A key lets a person's own agent (Claude Code, Codex) reach the research
# drafting tools at /mcp/research as that person. Shown once at creation;
# only the SHA-256 is stored. A key works only while its person is active and
# holds the Writing permission, so disabling someone disables their agents.

KEYS_PER_PERSON = 10


def create_key(staff_id, label):
    label = (label or "").strip()[:60] or "Key"
    c = conn()
    with _lock:
        live = c.execute("SELECT count(*) FROM staff_keys WHERE staff_id = ? AND revoked_at IS NULL",
                         (staff_id,)).fetchone()[0]
        if live >= KEYS_PER_PERSON:
            raise StaffError("You already have %d keys. Revoke one you no longer use."
                             % KEYS_PER_PERSON)
        token = "plvr_" + secrets.token_urlsafe(32)
        cur = c.execute("INSERT INTO staff_keys (staff_id, label, token_hash, created_at) "
                        "VALUES (?, ?, ?, ?)", (staff_id, label, _hash_token(token), _now()))
        c.commit()
    return {"id": cur.lastrowid, "label": label, "token": token}


def list_keys(staff_id):
    rows = conn().execute("SELECT id, label, created_at, last_used_at FROM staff_keys "
                          "WHERE staff_id = ? AND revoked_at IS NULL ORDER BY id DESC",
                          (staff_id,)).fetchall()
    return [dict(r) for r in rows]


def revoke_key(staff_id, key_id):
    c = conn()
    with _lock:
        cur = c.execute("UPDATE staff_keys SET revoked_at = ? WHERE id = ? AND staff_id = ? "
                        "AND revoked_at IS NULL", (_now(), key_id, staff_id))
        c.commit()
    if not cur.rowcount:
        raise StaffError("No such key.")


def key_person(token):
    """The active person a key belongs to, or None."""
    if not token or not token.startswith("plvr_"):
        return None
    row = conn().execute(
        "SELECT s.*, k.id AS key_id, k.label AS key_label, k.last_used_at AS key_used "
        "FROM staff_keys k JOIN staff s ON s.id = k.staff_id "
        "WHERE k.token_hash = ? AND k.revoked_at IS NULL", (_hash_token(token),)).fetchone()
    if row is None or row["status"] != "active":
        return None
    now = _now()
    if not row["key_used"] or now - row["key_used"] > 60:
        c = conn()
        with _lock:
            c.execute("UPDATE staff_keys SET last_used_at = ? WHERE id = ?", (now, row["key_id"]))
            c.commit()
    person = _public(row)
    person["key_label"] = row["key_label"]
    return person


# ---------------------------------------------------------- AI provider settings
#
# Which model the research desk's AI tab uses for a person, and their own
# credentials for it: an API key, a Claude Code token or a Codex sign-in. Each
# secret is sealed with the assistant keychain (ASSISTANT_SECRET) before it is
# stored; only its last four characters are kept readable, for display.

AI_FIELDS = ("provider", "model", "key_ct", "key_last4", "claude_token_ct", "codex_auth_ct",
             "codex_email", "length", "style")


def ai_settings(staff_id):
    row = conn().execute("SELECT * FROM staff_ai WHERE staff_id = ?", (staff_id,)).fetchone()
    return dict(row) if row else {"staff_id": staff_id, "provider": None, "model": None,
                                  "key_ct": None, "key_last4": None, "claude_token_ct": None,
                                  "codex_auth_ct": None, "codex_email": None, "length": None,
                                  "style": None}


def set_ai(staff_id, **fields):
    bad = [k for k in fields if k not in AI_FIELDS]
    if bad:
        raise StaffError("Unknown setting: %s." % ", ".join(bad))
    c = conn()
    with _lock:
        if not c.execute("SELECT 1 FROM staff_ai WHERE staff_id = ?", (staff_id,)).fetchone():
            c.execute("INSERT INTO staff_ai (staff_id, updated_at) VALUES (?, ?)", (staff_id, _now()))
        if fields:
            c.execute("UPDATE staff_ai SET %s, updated_at = ? WHERE staff_id = ?"
                      % ", ".join("%s = ?" % k for k in fields),
                      list(fields.values()) + [_now(), staff_id])
        c.commit()
    return ai_settings(staff_id)


def team_setting(key, default=None):
    row = conn().execute("SELECT value FROM team_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_team_setting(key, value):
    c = conn()
    with _lock:
        c.execute("INSERT INTO team_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                  "SET value = excluded.value", (key, value))
        c.commit()


def shared_person():
    """The identity a shared-password session acts as."""
    return {"id": 0, "email": None, "name": "Shared password",
            "permissions": list(PERMISSION_KEYS), "status": "active", "shared": True}


def actor_label(person):
    """How a person is named in audit trails and article histories."""
    if not person:
        return "unknown"
    if person.get("shared"):
        return "shared password"
    return person.get("email") or "unknown"
