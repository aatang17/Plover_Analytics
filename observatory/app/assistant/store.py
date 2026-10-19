"""The Investment Assistant's workspace store.

One SQLite file on the `data/` volume — the same one `accounts.py` keeps its
sign-in tables in — and never the macro or equity DuckDB files. Ingest
guardrail 5 says the serving process must not write a dataset file; this is a
different engine writing a different file, and a dataset rebuild cannot touch
a desk. Every table here is prefixed `ia_` so it cannot collide with the
account tables it shares the file with.

What it holds, in the plan's words (docs/plans/ARCH-INVESTMENT-ASSISTANT-AGENT.md §6):
desks (per-account configuration and encrypted keys), hires (a specialist on
a desk), coverage (the names a desk follows), runs and tool calls (the audit),
posts (#desk), approvals (anything outward), threads and messages (research),
files (the specialist's workspace). Also kept per account: memory (what the
assistant has learned about its person, app/assistant/memory.py) and the
research desk AI tab's conversations (app/research_chat.py).

Every function takes plain values and returns plain dicts, so the API and the
runner — which runs as a separate process — share one code path and no ORM.
Python 3.9 on the laptop, 3.12 in the container: no 3.10+ syntax.
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import threading
import time

from .. import accounts

# The file the account tables already live in. Overridable for tests.
DB_PATH = pathlib.Path(os.environ.get("WORKSPACE_DB") or accounts.DB_PATH)

_lock = threading.Lock()
_conn = None           # the connection that created the schema; None = not opened yet
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS ia_desks (
  account_id INTEGER PRIMARY KEY,
  model_provider TEXT,
  model_name TEXT,
  model_key_ct TEXT,
  model_key_last4 TEXT,
  slack_webhook_ct TEXT,
  slack_label TEXT,
  policy_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ia_hires (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  slug TEXT NOT NULL,
  version TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  UNIQUE (account_id, slug)
);
CREATE TABLE IF NOT EXISTS ia_coverage (
  account_id INTEGER NOT NULL,
  sec_code TEXT NOT NULL,
  name TEXT,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (account_id, sec_code)
);
CREATE TABLE IF NOT EXISTS ia_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  hire_id INTEGER,
  slug TEXT NOT NULL,
  version TEXT NOT NULL,
  trigger TEXT NOT NULL,
  task TEXT,
  model TEXT,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL DEFAULT 'running',
  summary TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS ia_runs_account ON ia_runs (account_id, started_at);
CREATE TABLE IF NOT EXISTS ia_tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  args_json TEXT NOT NULL,
  result_sha256 TEXT,
  ms INTEGER NOT NULL,
  error INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ia_tool_calls_run ON ia_tool_calls (run_id, seq);
CREATE TABLE IF NOT EXISTS ia_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  hire_id INTEGER,
  run_id INTEGER,
  author TEXT NOT NULL,
  text TEXT NOT NULL,
  refs_json TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_posts_account ON ia_posts (account_id, created_at);
CREATE TABLE IF NOT EXISTS ia_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  run_id INTEGER,
  hire_id INTEGER,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL,
  decided_at INTEGER,
  expires_at INTEGER NOT NULL,
  result TEXT
);
CREATE INDEX IF NOT EXISTS ia_approvals_account ON ia_approvals (account_id, status);
CREATE TABLE IF NOT EXISTS ia_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  hire_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ia_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  run_id INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_messages_thread ON ia_messages (thread_id, id);
CREATE TABLE IF NOT EXISTS ia_lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_lists_account ON ia_lists (account_id);
CREATE TABLE IF NOT EXISTS ia_list_items (
  list_id INTEGER NOT NULL,
  sec_code TEXT NOT NULL,
  name TEXT,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (list_id, sec_code)
);
CREATE TABLE IF NOT EXISTS ia_secrets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'api_key',
  ct TEXT NOT NULL,
  last4 TEXT NOT NULL,
  note TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_used_at INTEGER
);
CREATE INDEX IF NOT EXISTS ia_secrets_account ON ia_secrets (account_id);
CREATE TABLE IF NOT EXISTS ia_mcp_servers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  auth_kind TEXT NOT NULL DEFAULT 'none',
  header_name TEXT,
  secret_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  checked_at INTEGER,
  status TEXT,
  detail TEXT,
  tools_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ia_mcp_account ON ia_mcp_servers (account_id);
CREATE TABLE IF NOT EXISTS ia_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  last4 TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER,
  revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS ia_tokens_account ON ia_tokens (account_id);
CREATE TABLE IF NOT EXISTS ia_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  hire_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  content TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (hire_id, path)
);
CREATE TABLE IF NOT EXISTS ia_charts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  run_id INTEGER NOT NULL,
  post_id INTEGER,
  spec_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_charts_run ON ia_charts (run_id);
CREATE TABLE IF NOT EXISTS ia_data_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  ct TEXT NOT NULL,
  last4 TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  checked_at INTEGER,
  status TEXT,
  detail TEXT,
  last_used_at INTEGER,
  UNIQUE (account_id, provider)
);
CREATE TABLE IF NOT EXISTS ia_pub_data_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  ct TEXT NOT NULL,
  last4 TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  checked_at INTEGER,
  status TEXT,
  detail TEXT,
  last_used_at INTEGER,
  UNIQUE (publication_id, provider)
);
CREATE TABLE IF NOT EXISTS ia_memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  origin TEXT NOT NULL,
  place TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_memories_account ON ia_memories (account_id, id);
CREATE TABLE IF NOT EXISTS ia_memory_prefs (
  account_id INTEGER PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ia_article_chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  article_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_article_chats_owner ON ia_article_chats (account_id, article_id, updated_at);
CREATE TABLE IF NOT EXISTS ia_article_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  text TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_article_turns_chat ON ia_article_turns (chat_id, id);
CREATE TABLE IF NOT EXISTS ia_studies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  spec_json TEXT NOT NULL DEFAULT '{}',
  archived_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_studies_owner ON ia_studies (account_id, updated_at);
CREATE TABLE IF NOT EXISTS ia_study_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id INTEGER NOT NULL,
  account_id INTEGER NOT NULL,
  by TEXT NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  spec_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  rows INTEGER NOT NULL,
  run_id INTEGER,
  tokens_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_study_runs_study ON ia_study_runs (study_id, id);
CREATE TABLE IF NOT EXISTS ia_analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  run_id INTEGER,
  result_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_analyses_run ON ia_analyses (run_id, id);
"""

APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60


def conn():
    """This thread's connection (see accounts.conn for why not one shared)."""
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = accounts._open(DB_PATH)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            # Columns added after the first release of these tables. SQLite has
            # no ADD COLUMN IF NOT EXISTS, so each is tried and a duplicate
            # column is the expected, harmless outcome on an upgraded file.
            for ddl in ("ALTER TABLE ia_posts ADD COLUMN approval_id INTEGER",
                        "ALTER TABLE ia_desks ADD COLUMN monitor_model TEXT",
                        "ALTER TABLE ia_list_items ADD COLUMN name_ja TEXT",
                        # a Codex sign-in (ChatGPT plan): auth.json, sealed
                        "ALTER TABLE ia_desks ADD COLUMN codex_auth_ct TEXT",
                        "ALTER TABLE ia_desks ADD COLUMN codex_email TEXT",
                        # a cheaper model for Studies, which only writes SQL
                        "ALTER TABLE ia_desks ADD COLUMN study_model TEXT",
                        # a study that re-runs itself: 'daily' or 'weekly', and when it last did
                        "ALTER TABLE ia_studies ADD COLUMN refresh TEXT",
                        "ALTER TABLE ia_studies ADD COLUMN refreshed_at INTEGER"):
                try:
                    _conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            _conn.commit()
        base = _conn
    if getattr(_local, "base", None) is not base:
        _local.base, _local.conn = base, accounts._open(DB_PATH)
    return _local.conn


def reset_for_tests(path):
    """Point the store at another file and drop the open handle."""
    global _conn, DB_PATH
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        DB_PATH = pathlib.Path(path)


def now():
    return int(time.time())


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _row(cur):
    r = cur.fetchone()
    return dict(r) if r is not None else None


def _write(sql, params=()):
    db = conn()
    with _lock:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- desks

def desk(account_id):
    """The desk's configuration, created on first sight. Never returns a key."""
    row = _row(conn().execute("SELECT * FROM ia_desks WHERE account_id = ?", (account_id,)))
    if row is None:
        t = now()
        _write("INSERT INTO ia_desks (account_id, policy_json, created_at, updated_at) "
               "VALUES (?, '{}', ?, ?)", (account_id, t, t))
        row = _row(conn().execute("SELECT * FROM ia_desks WHERE account_id = ?", (account_id,)))
    return {
        "account_id": row["account_id"],
        "model_provider": row["model_provider"],
        "model_name": row["model_name"],
        "monitor_model": row["monitor_model"],
        "study_model": row["study_model"],
        "model_key_last4": row["model_key_last4"],
        "model_key_set": bool(row["model_key_ct"]),
        "slack_set": bool(row["slack_webhook_ct"]),
        "slack_label": row["slack_label"],
        "policy": _loads(row["policy_json"], {}),
        "codex_connected": bool(row["codex_auth_ct"]),
        "codex_email": row["codex_email"],
    }


def desk_secrets(account_id):
    """The ciphertexts, for the keychain only. Not for any API response."""
    desk(account_id)
    return _row(conn().execute(
        "SELECT model_key_ct, slack_webhook_ct, codex_auth_ct FROM ia_desks WHERE account_id = ?",
        (account_id,)))


def update_desk(account_id, **fields):
    desk(account_id)
    allowed = ("model_provider", "model_name", "monitor_model", "study_model", "model_key_ct", "model_key_last4",
               "slack_webhook_ct", "slack_label", "policy_json", "codex_auth_ct", "codex_email")
    sets = [k for k in fields if k in allowed]
    if not sets:
        return desk(account_id)
    sql = "UPDATE ia_desks SET " + ", ".join("%s = ?" % k for k in sets) + \
          ", updated_at = ? WHERE account_id = ?"
    _write(sql, tuple(fields[k] for k in sets) + (now(), account_id))
    return desk(account_id)


# ------------------------------------------------------------------- hires

def hires(account_id):
    rows = _rows(conn().execute(
        "SELECT * FROM ia_hires WHERE account_id = ? ORDER BY id", (account_id,)))
    for r in rows:
        r["config"] = _loads(r.pop("config_json"), {})
        r["enabled"] = bool(r["enabled"])
    return rows


def hire(account_id, hire_id):
    for h in hires(account_id):
        if h["id"] == int(hire_id):
            return h
    return None


def hire_by_slug(account_id, slug):
    for h in hires(account_id):
        if h["slug"] == slug:
            return h
    return None


def add_hire(account_id, slug, version, config=None):
    existing = hire_by_slug(account_id, slug)
    if existing:
        return existing
    _write("INSERT INTO ia_hires (account_id, slug, version, config_json, enabled, created_at) "
           "VALUES (?, ?, ?, ?, 1, ?)",
           (account_id, slug, version, json.dumps(config or {}), now()))
    return hire_by_slug(account_id, slug)


def update_hire(account_id, hire_id, version=None, config=None, enabled=None):
    h = hire(account_id, hire_id)
    if h is None:
        return None
    _write("UPDATE ia_hires SET version = ?, config_json = ?, enabled = ? "
           "WHERE id = ? AND account_id = ?",
           (version or h["version"],
            json.dumps(config if config is not None else h["config"]),
            1 if (h["enabled"] if enabled is None else enabled) else 0,
            hire_id, account_id))
    return hire(account_id, hire_id)


def remove_hire(account_id, hire_id):
    _write("DELETE FROM ia_hires WHERE id = ? AND account_id = ?", (hire_id, account_id))


# ---------------------------------------------------------------- coverage

# A desk has named coverage lists. One is the default ("Coverage"): what a
# monitor watches unless its hire names another list. The first desks kept a
# single list in ia_coverage; it is moved into the default list the first time
# the desk's lists are read, once, and ia_coverage is left empty for it.

DEFAULT_LIST_NAME = "Coverage"


def lists(account_id):
    """Every list on the desk with its size, the default first."""
    default_list_id(account_id)
    return _rows(conn().execute(
        "SELECT l.id, l.name, l.is_default, l.created_at, "
        "(SELECT count(*) FROM ia_list_items i WHERE i.list_id = l.id) AS count "
        "FROM ia_lists l WHERE l.account_id = ? ORDER BY l.is_default DESC, l.id", (account_id,)))


def list_row(account_id, list_id):
    return _row(conn().execute("SELECT id, name, is_default FROM ia_lists WHERE id = ? AND account_id = ?",
                               (list_id, account_id)))


def default_list_id(account_id):
    r = _row(conn().execute("SELECT id FROM ia_lists WHERE account_id = ? AND is_default = 1",
                            (account_id,)))
    if r:
        return r["id"]
    lid = _write("INSERT INTO ia_lists (account_id, name, is_default, created_at) VALUES (?, ?, 1, ?)",
                 (account_id, DEFAULT_LIST_NAME, now()))
    old = _rows(conn().execute("SELECT sec_code, name, added_at FROM ia_coverage WHERE account_id = ?",
                               (account_id,)))
    for o in old:
        _write("INSERT OR IGNORE INTO ia_list_items (list_id, sec_code, name, added_at) VALUES (?, ?, ?, ?)",
               (lid, o["sec_code"], o["name"], o["added_at"]))
    if old:
        _write("DELETE FROM ia_coverage WHERE account_id = ?", (account_id,))
    return lid


def _resolve(account_id, list_id):
    """A list id this account owns, or its default list."""
    if list_id:
        r = list_row(account_id, list_id)
        if r:
            return r["id"]
    return default_list_id(account_id)


def create_list(account_id, name):
    name = (name or "").strip()[:60] or "Untitled list"
    default_list_id(account_id)
    lid = _write("INSERT INTO ia_lists (account_id, name, is_default, created_at) VALUES (?, ?, 0, ?)",
                 (account_id, name, now()))
    return list_row(account_id, lid)


def rename_list(account_id, list_id, name):
    name = (name or "").strip()[:60]
    if not name:
        return list_row(account_id, list_id)
    _write("UPDATE ia_lists SET name = ? WHERE id = ? AND account_id = ?", (name, list_id, account_id))
    return list_row(account_id, list_id)


def delete_list(account_id, list_id):
    """Delete a list and its members. The default list cannot be deleted."""
    r = list_row(account_id, list_id)
    if r is None or r["is_default"]:
        return False
    _write("DELETE FROM ia_list_items WHERE list_id = ?", (list_id,))
    _write("DELETE FROM ia_lists WHERE id = ? AND account_id = ?", (list_id, account_id))
    return True


def coverage(account_id, list_id=None):
    """The companies on one list (the default list when none is named)."""
    lid = _resolve(account_id, list_id)
    return _rows(conn().execute(
        "SELECT sec_code, name, name_ja, added_at FROM ia_list_items WHERE list_id = ? "
        "ORDER BY added_at, sec_code", (lid,)))


def add_coverage(account_id, sec_code, name=None, list_id=None, name_ja=None):
    lid = _resolve(account_id, list_id)
    _write("INSERT OR IGNORE INTO ia_list_items (list_id, sec_code, name, name_ja, added_at) "
           "VALUES (?, ?, ?, ?, ?)", (lid, sec_code, name, name_ja, now()))
    if name:
        _write("UPDATE ia_list_items SET name = ? WHERE list_id = ? AND sec_code = ? "
               "AND (name IS NULL OR name = '')", (name, lid, sec_code))
    if name_ja:
        set_name_ja(lid, sec_code, name_ja)
    return coverage(account_id, lid)


def set_name_ja(list_id, sec_code, name_ja):
    _write("UPDATE ia_list_items SET name_ja = ? WHERE list_id = ? AND sec_code = ? "
           "AND (name_ja IS NULL OR name_ja = '')", (name_ja, list_id, sec_code))


def remove_coverage(account_id, sec_code, list_id=None):
    lid = _resolve(account_id, list_id)
    _write("DELETE FROM ia_list_items WHERE list_id = ? AND sec_code = ?", (lid, sec_code))
    return coverage(account_id, lid)


# -------------------------------------------------------------------- runs

def start_run(account_id, hire_id, slug, version, trigger, task, model):
    return _write(
        "INSERT INTO ia_runs (account_id, hire_id, slug, version, trigger, task, model, "
        "started_at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
        (account_id, hire_id, slug, version, trigger, task, model, now()))


def end_run(run_id, outcome, summary=None, error=None, tokens_in=0, tokens_out=0,
            tool_calls=0):
    _write("UPDATE ia_runs SET ended_at = ?, outcome = ?, summary = ?, error = ?, "
           "tokens_in = ?, tokens_out = ?, tool_calls = ? WHERE id = ?",
           (now(), outcome, summary, error, tokens_in, tokens_out, tool_calls, run_id))


def record_call(run_id, seq, name, args, result_text, ms, error):
    digest = hashlib.sha256((result_text or "").encode("utf-8")).hexdigest()
    _write("INSERT INTO ia_tool_calls (run_id, seq, name, args_json, result_sha256, ms, error) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)",
           (run_id, seq, name, json.dumps(args, ensure_ascii=False, sort_keys=True),
            digest, int(ms), 1 if error else 0))


def run(account_id, run_id):
    r = _row(conn().execute("SELECT * FROM ia_runs WHERE id = ? AND account_id = ?",
                            (run_id, account_id)))
    if r is None:
        return None
    r["calls"] = calls(run_id)
    return r


def runs(account_id, limit=50):
    return _rows(conn().execute(
        "SELECT * FROM ia_runs WHERE account_id = ? ORDER BY id DESC LIMIT ?",
        (account_id, int(limit))))


def calls(run_id):
    rows = _rows(conn().execute(
        "SELECT seq, name, args_json, result_sha256, ms, error FROM ia_tool_calls "
        "WHERE run_id = ? ORDER BY seq", (run_id,)))
    for r in rows:
        r["args"] = _loads(r.pop("args_json"), {})
        r["error"] = bool(r["error"])
    return rows


def tool_usage(account_id):
    """Calls per tool across the desk, with who made them — the Audit page."""
    rows = _rows(conn().execute(
        "SELECT c.name AS name, r.slug AS slug, count(*) AS n "
        "FROM ia_tool_calls c JOIN ia_runs r ON r.id = c.run_id "
        "WHERE r.account_id = ? GROUP BY c.name, r.slug", (account_id,)))
    by = {}
    for r in rows:
        e = by.setdefault(r["name"], {"name": r["name"], "calls": 0, "used_by": []})
        e["calls"] += r["n"]
        e["used_by"].append(r["slug"])
    out = sorted(by.values(), key=lambda e: -e["calls"])
    for e in out:
        e["used_by"] = sorted(set(e["used_by"]))
    return out


def last_success_at():
    """When any desk's runner last finished a run cleanly — the health row."""
    r = _row(conn().execute(
        "SELECT max(ended_at) AS t FROM ia_runs WHERE outcome IN ('done', 'approval_waiting')"))
    return r["t"] if r else None


# ------------------------------------------------------------------- posts

def add_post(account_id, author, text, hire_id=None, run_id=None, refs=None, approval_id=None):
    pid = _write("INSERT INTO ia_posts (account_id, hire_id, run_id, author, text, refs_json, "
                 "created_at, approval_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (account_id, hire_id, run_id, author, text,
                  json.dumps(refs or [], ensure_ascii=False), now(), approval_id))
    return post(account_id, pid)


def post(account_id, post_id):
    r = _row(conn().execute("SELECT * FROM ia_posts WHERE id = ? AND account_id = ?",
                            (post_id, account_id)))
    return _shape_post(r) if r else None


def posts(account_id, limit=100):
    rows = _rows(conn().execute(
        "SELECT * FROM ia_posts WHERE account_id = ? ORDER BY id DESC LIMIT ?",
        (account_id, int(limit))))
    rows.reverse()
    return [_shape_post(r) for r in rows]


def _shape_post(r):
    r["refs"] = _loads(r.pop("refs_json"), [])
    return r


# --------------------------------------------------------------- approvals

def add_approval(account_id, run_id, hire_id, action, payload):
    t = now()
    aid = _write("INSERT INTO ia_approvals (account_id, run_id, hire_id, action, payload_json, "
                 "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                 (account_id, run_id, hire_id, action,
                  json.dumps(payload, ensure_ascii=False), t, t + APPROVAL_TTL_SECONDS))
    return approval(account_id, aid)


def approval(account_id, approval_id):
    r = _row(conn().execute("SELECT * FROM ia_approvals WHERE id = ? AND account_id = ?",
                            (approval_id, account_id)))
    return _shape_approval(r) if r else None


def approvals(account_id, status=None):
    if status:
        cur = conn().execute("SELECT * FROM ia_approvals WHERE account_id = ? AND status = ? "
                             "ORDER BY id DESC", (account_id, status))
    else:
        cur = conn().execute("SELECT * FROM ia_approvals WHERE account_id = ? ORDER BY id DESC",
                             (account_id,))
    return [_shape_approval(r) for r in _rows(cur)]


def decide_approval(account_id, approval_id, status, result=None):
    _write("UPDATE ia_approvals SET status = ?, decided_at = ?, result = ? "
           "WHERE id = ? AND account_id = ? AND status = 'pending'",
           (status, now(), result, approval_id, account_id))
    return approval(account_id, approval_id)


def _shape_approval(r):
    r["payload"] = _loads(r.pop("payload_json"), {})
    return r


# ----------------------------------------------------------------- threads

def add_thread(account_id, hire_id, title):
    t = now()
    tid = _write("INSERT INTO ia_threads (account_id, hire_id, title, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?)", (account_id, hire_id, title[:120], t, t))
    return thread(account_id, tid)


def threads(account_id):
    rows = _rows(conn().execute(
        "SELECT t.*, (SELECT count(*) FROM ia_messages m WHERE m.thread_id = t.id) AS messages "
        "FROM ia_threads t WHERE account_id = ? ORDER BY updated_at DESC", (account_id,)))
    return rows


def thread(account_id, thread_id):
    r = _row(conn().execute("SELECT * FROM ia_threads WHERE id = ? AND account_id = ?",
                            (thread_id, account_id)))
    if r is None:
        return None
    r["messages"] = _rows(conn().execute(
        "SELECT id, role, text, run_id, created_at FROM ia_messages WHERE thread_id = ? "
        "ORDER BY id", (thread_id,)))
    for m in r["messages"]:
        m["calls"] = calls(m["run_id"]) if m["run_id"] else []
        m["charts"] = charts_for_run(m["run_id"]) if m["run_id"] else []
        # the notes this reply's run saved to the person's memory, while kept
        m["memory"] = _rows(conn().execute(
            "SELECT id, text FROM ia_memories WHERE account_id = ? AND origin = ? ORDER BY id",
            (account_id, "run:%s" % m["run_id"]))) if m["run_id"] and m["role"] == "assistant" else []
    return r


def add_message(thread_id, role, text, run_id=None):
    t = now()
    _write("INSERT INTO ia_messages (thread_id, role, text, run_id, created_at) "
           "VALUES (?, ?, ?, ?, ?)", (thread_id, role, text, run_id, t))
    _write("UPDATE ia_threads SET updated_at = ? WHERE id = ?", (t, thread_id))


# ------------------------------------------------------------------ charts
# A chart belongs to the run that drew it. On the desk it is shown under the
# post it was attached to; in a thread, under the reply of its run.

def add_chart(account_id, run_id, spec):
    cid = _write("INSERT INTO ia_charts (account_id, run_id, spec_json, created_at) "
                 "VALUES (?, ?, ?, ?)",
                 (account_id, run_id, json.dumps(spec, ensure_ascii=False), now()))
    return cid


def attach_charts(account_id, chart_ids, post_id):
    for cid in chart_ids:
        _write("UPDATE ia_charts SET post_id = ? WHERE id = ? AND account_id = ? AND post_id IS NULL",
               (post_id, cid, account_id))


def charts_for_run(run_id):
    rows = _rows(conn().execute("SELECT id, post_id, spec_json FROM ia_charts WHERE run_id = ? "
                                "ORDER BY id", (run_id,)))
    out = []
    for r in rows:
        c = _loads(r["spec_json"], {})
        c["id"] = r["id"]
        c["post_id"] = r["post_id"]
        out.append(c)
    return out


# ------------------------------------------------------------------- files

def write_file(account_id, hire_id, path, content):
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _write("INSERT INTO ia_files (account_id, hire_id, path, content, sha256, updated_at) "
           "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(hire_id, path) DO UPDATE SET "
           "content = excluded.content, sha256 = excluded.sha256, updated_at = excluded.updated_at",
           (account_id, hire_id, path, content, digest, now()))
    return {"path": path, "sha256": digest}


def files(account_id, hire_id=None):
    if hire_id is None:
        cur = conn().execute("SELECT id, hire_id, path, sha256, updated_at FROM ia_files "
                             "WHERE account_id = ? ORDER BY hire_id, path", (account_id,))
    else:
        cur = conn().execute("SELECT id, hire_id, path, sha256, updated_at FROM ia_files "
                             "WHERE account_id = ? AND hire_id = ? ORDER BY path",
                             (account_id, hire_id))
    return _rows(cur)


def read_file(account_id, hire_id, path):
    return _row(conn().execute(
        "SELECT path, content, sha256, updated_at FROM ia_files "
        "WHERE account_id = ? AND hire_id = ? AND path = ?", (account_id, hire_id, path)))


# ------------------------------------------------------------ connections
# Personal keys for an outside agent (Claude Code, Codex, any MCP client) to
# reach this desk over /mcp/desk. Shown once at creation; only the SHA-256 is
# kept, like a session token, so the file cannot be replayed into access.

import secrets as _secrets  # noqa: E402


def _token_hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_token(account_id, label):
    raw = "pad_" + _secrets.token_urlsafe(32)
    tid = _write("INSERT INTO ia_tokens (account_id, label, token_hash, last4, created_at) "
                 "VALUES (?, ?, ?, ?, ?)",
                 (account_id, (label or "Connection").strip()[:60], _token_hash(raw), raw[-4:], now()))
    out = token(account_id, tid)
    out["token"] = raw
    return out


def token(account_id, token_id):
    return _row(conn().execute(
        "SELECT id, label, last4, created_at, last_used_at, revoked_at FROM ia_tokens "
        "WHERE id = ? AND account_id = ?", (token_id, account_id)))


def tokens(account_id):
    return _rows(conn().execute(
        "SELECT id, label, last4, created_at, last_used_at, revoked_at FROM ia_tokens "
        "WHERE account_id = ? ORDER BY id DESC", (account_id,)))


def revoke_token(account_id, token_id):
    _write("UPDATE ia_tokens SET revoked_at = ? WHERE id = ? AND account_id = ? "
           "AND revoked_at IS NULL", (now(), token_id, account_id))
    return token(account_id, token_id)


def token_owner(raw):
    """(account_id, token row) for a live key, or None. Stamps last use."""
    if not raw:
        return None
    r = _row(conn().execute(
        "SELECT t.id, t.account_id, t.label, a.email FROM ia_tokens t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.token_hash = ? AND t.revoked_at IS NULL", (_token_hash(raw),)))
    if r is None:
        return None
    _write("UPDATE ia_tokens SET last_used_at = ? WHERE id = ?", (now(), r["id"]))
    return r


def connection_run(account_id, label):
    """The audit run an outside connection's calls are filed under: one per
    connection per UTC day, so the Audit page reads as sessions, not noise."""
    day_start = now() - now() % 86400
    r = _row(conn().execute(
        "SELECT id FROM ia_runs WHERE account_id = ? AND slug = 'connection' AND trigger = ? "
        "AND started_at >= ? ORDER BY id DESC LIMIT 1", (account_id, label, day_start)))
    if r:
        return r["id"]
    return _write("INSERT INTO ia_runs (account_id, hire_id, slug, version, trigger, task, model, "
                  "started_at, ended_at, outcome) VALUES (?, NULL, 'connection', '-', ?, ?, ?, ?, ?, 'done')",
                  (account_id, label, "Calls from " + label, "external", now(), now()))


def touch_connection_run(run_id, add_calls=1):
    _write("UPDATE ia_runs SET tool_calls = tool_calls + ?, ended_at = ? WHERE id = ?",
           (add_calls, now(), run_id))


def next_seq(run_id):
    r = _row(conn().execute("SELECT max(seq) AS m FROM ia_tool_calls WHERE run_id = ?", (run_id,)))
    return (r["m"] or 0) + 1 if r else 1


# ---------------------------------------------------------------- secrets
# Named API keys a desk stores once and refers to by name: the key an external
# MCP server needs, a webhook, anything an integration asks for. The plaintext
# is sealed with keychain.seal before it arrives here and is never returned by
# any read below — only its last four characters, so a person can tell two
# keys apart.


def add_secret(account_id, name, ct, last4, kind="api_key", note=None):
    t = now()
    sid = _write("INSERT INTO ia_secrets (account_id, name, kind, ct, last4, note, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (account_id, (name or "Key").strip()[:60], kind, ct, last4, note, t, t))
    return secret(account_id, sid)


def update_secret(account_id, secret_id, name=None, ct=None, last4=None, note=None):
    cur = secret(account_id, secret_id)
    if cur is None:
        return None
    sets, params = [], []
    if name:
        sets.append("name = ?"); params.append(name.strip()[:60])
    if ct:
        sets.append("ct = ?"); params.append(ct)
        sets.append("last4 = ?"); params.append(last4 or "")
    if note is not None:
        sets.append("note = ?"); params.append(note)
    if not sets:
        return cur
    sets.append("updated_at = ?"); params.append(now())
    _write("UPDATE ia_secrets SET " + ", ".join(sets) + " WHERE id = ? AND account_id = ?",
           tuple(params) + (secret_id, account_id))
    return secret(account_id, secret_id)


def secret(account_id, secret_id):
    """One secret without its ciphertext."""
    return _row(conn().execute(
        "SELECT id, name, kind, last4, note, created_at, updated_at, last_used_at "
        "FROM ia_secrets WHERE id = ? AND account_id = ?", (secret_id, account_id)))


def secrets(account_id):
    rows = _rows(conn().execute(
        "SELECT id, name, kind, last4, note, created_at, updated_at, last_used_at "
        "FROM ia_secrets WHERE account_id = ? ORDER BY id", (account_id,)))
    used = {}
    for m in mcp_servers(account_id):
        if m["secret_id"]:
            used.setdefault(m["secret_id"], []).append(m["label"])
    for r in rows:
        r["used_by"] = used.get(r["id"], [])
    return rows


def secret_ct(account_id, secret_id):
    """The sealed value, for the one caller that must open it. Stamps use."""
    r = _row(conn().execute("SELECT ct FROM ia_secrets WHERE id = ? AND account_id = ?",
                            (secret_id, account_id)))
    if r:
        _write("UPDATE ia_secrets SET last_used_at = ? WHERE id = ?", (now(), secret_id))
    return r["ct"] if r else None


def delete_secret(account_id, secret_id):
    """Refused while a server still refers to it, so nothing breaks silently."""
    if [m for m in mcp_servers(account_id) if m["secret_id"] == secret_id]:
        return False
    _write("DELETE FROM ia_secrets WHERE id = ? AND account_id = ?", (secret_id, account_id))
    return True


# ------------------------------------------------------------ MCP servers

def add_mcp_server(account_id, label, url, auth_kind="none", header_name=None, secret_id=None):
    mid = _write("INSERT INTO ia_mcp_servers (account_id, label, url, auth_kind, header_name, "
                 "secret_id, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                 (account_id, (label or "Server").strip()[:60], url.strip(), auth_kind,
                  header_name, secret_id, now()))
    return mcp_server(account_id, mid)


def update_mcp_server(account_id, server_id, **fields):
    allowed = ("label", "url", "auth_kind", "header_name", "secret_id", "enabled",
               "checked_at", "status", "detail", "tools_json")
    sets = [k for k in fields if k in allowed]
    if not sets:
        return mcp_server(account_id, server_id)
    _write("UPDATE ia_mcp_servers SET " + ", ".join("%s = ?" % k for k in sets) +
           " WHERE id = ? AND account_id = ?",
           tuple(fields[k] for k in sets) + (server_id, account_id))
    return mcp_server(account_id, server_id)


def _shape_server(r):
    if r is None:
        return None
    r["tools"] = _loads(r.pop("tools_json"), [])
    r["enabled"] = bool(r["enabled"])
    return r


def mcp_server(account_id, server_id):
    return _shape_server(_row(conn().execute(
        "SELECT * FROM ia_mcp_servers WHERE id = ? AND account_id = ?", (server_id, account_id))))


def mcp_servers(account_id, enabled_only=False):
    sql = "SELECT * FROM ia_mcp_servers WHERE account_id = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    return [_shape_server(r) for r in _rows(conn().execute(sql + " ORDER BY id", (account_id,)))]


def delete_mcp_server(account_id, server_id):
    _write("DELETE FROM ia_mcp_servers WHERE id = ? AND account_id = ?", (server_id, account_id))
    return True


# ------------------------------------------------------------ data sources
# Market-data accounts (market_data.py): one key per vendor, sealed like every
# other key here and never returned by a read. An owner is a person (their
# sign-in account id, an int) or a publication (("pub", id)): each keeps its
# own in its own table, and one never reaches the other's.

_SOURCE_COLS = ("id, %s, provider, last4, enabled, created_at, checked_at, "
                "status, detail, last_used_at")


def pub_owner(publication_id):
    return ("pub", int(publication_id))


def _src(owner):
    """(table, owner column, owner id) for a person or a publication."""
    if isinstance(owner, tuple) and owner and owner[0] == "pub":
        return "ia_pub_data_sources", "publication_id", int(owner[1])
    return "ia_data_sources", "account_id", int(owner)


def _shape_source(r):
    if r is not None:
        r["enabled"] = bool(r["enabled"])
        r.pop("publication_id", None)
    return r


def data_source(owner, source_id):
    table, col, oid = _src(owner)
    return _shape_source(_row(conn().execute(
        "SELECT " + _SOURCE_COLS % col + " FROM " + table + " WHERE id = ? AND " + col + " = ?",
        (source_id, oid))))


def data_sources(owner):
    table, col, oid = _src(owner)
    return [_shape_source(r) for r in _rows(conn().execute(
        "SELECT " + _SOURCE_COLS % col + " FROM " + table + " WHERE " + col + " = ? ORDER BY id",
        (oid,)))]


def save_data_source(owner, provider, ct, last4, status, detail):
    """Connect a vendor, or replace its key: one row per owner and vendor."""
    table, col, oid = _src(owner)
    t = now()
    _write("INSERT INTO " + table + " (" + col + ", provider, ct, last4, enabled, created_at, "
           "checked_at, status, detail) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?) "
           "ON CONFLICT (" + col + ", provider) DO UPDATE SET ct = excluded.ct, "
           "last4 = excluded.last4, checked_at = excluded.checked_at, "
           "status = excluded.status, detail = excluded.detail",
           (oid, provider, ct, last4, t, t, status, detail))
    r = _row(conn().execute("SELECT id FROM " + table + " WHERE " + col + " = ? AND provider = ?",
                            (oid, provider)))
    return data_source(owner, r["id"])


def update_data_source(owner, source_id, **fields):
    table, col, oid = _src(owner)
    allowed = ("enabled", "checked_at", "status", "detail")
    sets = [k for k in fields if k in allowed]
    if sets:
        _write("UPDATE " + table + " SET " + ", ".join("%s = ?" % k for k in sets) +
               " WHERE id = ? AND " + col + " = ?",
               tuple(fields[k] for k in sets) + (source_id, oid))
    return data_source(owner, source_id)


def data_source_ct(owner, source_id):
    table, col, oid = _src(owner)
    r = _row(conn().execute("SELECT ct FROM " + table + " WHERE id = ? AND " + col + " = ?",
                            (source_id, oid)))
    return r["ct"] if r else None


def touch_data_source(owner, source_id):
    table, col, oid = _src(owner)
    _write("UPDATE " + table + " SET last_used_at = ? WHERE id = ? AND " + col + " = ?",
           (now(), source_id, oid))


def delete_data_source(owner, source_id):
    table, col, oid = _src(owner)
    _write("DELETE FROM " + table + " WHERE id = ? AND " + col + " = ?", (source_id, oid))
    return True


# ----------------------------------------------------------------- studies
# A study is one person's saved analysis (app/analysis.py): a title, the
# question asked and the spec that answers it. Every run of it is kept, never
# changed: re-running, editing the query or asking the Analyst again adds a
# run, so earlier evidence stays exactly as it was. Archived studies are
# hidden, not deleted, and come back with restore.

STUDY_ROWS_MAX = 20000      # rows kept with a run; beyond this the result says so


def _study_out(r):
    r["spec"] = _loads(r.pop("spec_json", None), {})
    return r


def add_study(account_id, title, question=""):
    t = now()
    sid = _write("INSERT INTO ia_studies (account_id, title, question, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?)", (account_id, (title or "Untitled study")[:160],
                                             (question or "")[:2000], t, t))
    return study(account_id, sid)


def studies(account_id, archived=False):
    rows = _rows(conn().execute(
        "SELECT s.id, s.title, s.question, s.created_at, s.updated_at, s.archived_at, "
        "(SELECT count(*) FROM ia_study_runs r WHERE r.study_id = s.id) AS runs, "
        "(SELECT max(created_at) FROM ia_study_runs r WHERE r.study_id = s.id) AS last_run_at "
        "FROM ia_studies s WHERE s.account_id = ? AND s.archived_at IS " +
        ("NOT NULL" if archived else "NULL") + " ORDER BY s.updated_at DESC", (account_id,)))
    return rows


def study(account_id, study_id):
    r = _row(conn().execute("SELECT * FROM ia_studies WHERE id = ? AND account_id = ?",
                            (study_id, account_id)))
    if r is None:
        return None
    r = _study_out(r)
    r["runs"] = _rows(conn().execute(
        "SELECT id, by, question, rows, run_id, tokens_json, created_at FROM ia_study_runs "
        "WHERE study_id = ? ORDER BY id DESC", (study_id,)))
    for x in r["runs"]:
        x["tokens"] = _loads(x.pop("tokens_json", None), {})
    return r


REFRESH_CHOICES = ("daily", "weekly")


def update_study(account_id, study_id, title=None, question=None, spec=None, refresh=False):
    """``refresh`` False leaves the setting alone; None or a choice sets it."""
    cur = study(account_id, study_id)
    if cur is None:
        return None
    keep = cur.get("refresh") if refresh is False else (refresh if refresh in REFRESH_CHOICES else None)
    _write("UPDATE ia_studies SET title = ?, question = ?, spec_json = ?, refresh = ?, updated_at = ? "
           "WHERE id = ? AND account_id = ?",
           ((title if title is not None else cur["title"])[:160] or "Untitled study",
            (question if question is not None else cur["question"])[:2000],
            json.dumps(spec if spec is not None else cur["spec"], ensure_ascii=False),
            keep, now(), study_id, account_id))
    return study(account_id, study_id)


def studies_due(at=None):
    """Studies whose refresh is due: a daily one not refreshed since the last
    14:00 UTC (after the day's ingest), a weekly one a week after its last.
    Archived studies and ones with no analysis yet are left alone."""
    at = at if at is not None else now()
    day = 24 * 3600
    today_1400 = (at // day) * day + 14 * 3600
    last_1400 = today_1400 if at >= today_1400 else today_1400 - day
    rows = _rows(conn().execute(
        "SELECT * FROM ia_studies WHERE refresh IN ('daily', 'weekly') AND archived_at IS NULL "
        "AND spec_json != '{}'"))
    due = []
    for r in rows:
        last = r["refreshed_at"] or 0
        if (r["refresh"] == "daily" and last < last_1400) or \
                (r["refresh"] == "weekly" and last < at - 7 * day):
            due.append(_study_out(r))
    return due


def mark_refreshed(study_id, at=None):
    _write("UPDATE ia_studies SET refreshed_at = ? WHERE id = ?",
           (at if at is not None else now(), study_id))


def archive_study(account_id, study_id, archived=True):
    _write("UPDATE ia_studies SET archived_at = ? WHERE id = ? AND account_id = ?",
           (now() if archived else None, study_id, account_id))
    return study(account_id, study_id)


def _keepable(result):
    """A result as a run keeps it: every row up to STUDY_ROWS_MAX."""
    out = dict(result)
    rows = out.get("rows") or []
    out["rows_total"] = len(rows)
    if len(rows) > STUDY_ROWS_MAX:
        out["rows"] = rows[:STUDY_ROWS_MAX]
    return out


def add_study_run(account_id, study_id, by, result, question="", note="", run_id=None, tokens=None):
    """Keep one run of a study, and make its spec the study's current one."""
    if study(account_id, study_id) is None:
        return None
    kept = _keepable(result)
    rid = _write(
        "INSERT INTO ia_study_runs (study_id, account_id, by, question, note, spec_json, result_json, "
        "rows, run_id, tokens_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (study_id, account_id, by[:80], (question or "")[:2000], (note or "")[:8000],
         json.dumps(result.get("spec") or {}, ensure_ascii=False),
         json.dumps(kept, ensure_ascii=False, default=str), kept["rows_total"], run_id,
         json.dumps(tokens or {}), now()))
    update_study(account_id, study_id, spec=result.get("spec") or {})
    return rid


def study_run(account_id, study_id, run_row_id=None):
    """One kept run in full (the newest when no id is given), or None."""
    sql = ("SELECT * FROM ia_study_runs WHERE study_id = ? AND account_id = ? " +
           ("AND id = ? " if run_row_id else "") + "ORDER BY id DESC LIMIT 1")
    params = (study_id, account_id) + ((run_row_id,) if run_row_id else ())
    r = _row(conn().execute(sql, params))
    if r is None:
        return None
    r["spec"] = _loads(r.pop("spec_json", None), {})
    r["result"] = _loads(r.pop("result_json", None), {})
    r["tokens"] = _loads(r.pop("tokens_json", None), {})
    return r


def add_analysis(account_id, run_id, result):
    """An analysis an AI run made (its spec and full result), kept under the run
    so the request that started the run can take it, whichever process ran it."""
    return _write("INSERT INTO ia_analyses (account_id, run_id, result_json, created_at) "
                  "VALUES (?, ?, ?, ?)", (account_id, run_id,
                                          json.dumps(_keepable(result), ensure_ascii=False,
                                                     default=str), now()))


def analyses_for_run(account_id, run_id):
    rows = _rows(conn().execute("SELECT id, result_json FROM ia_analyses WHERE account_id = ? "
                                "AND run_id = ? ORDER BY id", (account_id, run_id)))
    return [dict(id=r["id"], result=_loads(r["result_json"], {})) for r in rows]
