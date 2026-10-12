# -*- coding: utf-8 -*-
"""PloverResearch: the store behind the research desk and the public pages.

Drafts and versions
-------------------
An article has one **draft** — what the writers are editing — and, once
published, a numbered series of **versions**. Publishing turns the current
draft into the next version: its text, its rendered HTML, its Markdown and the
data of every chart, read as it stood that day. A version is never changed
afterwards, by this code or by anyone with a SQLite shell — triggers on the
table refuse UPDATE and DELETE. A correction is a new version with a note
saying what changed, and the old one stays at its own address. This is the
platform's vintage rule applied to its own writing: a citation to version 1
must keep returning version 1.

Withdrawing an article hides it from the list and replaces its page with a
notice. The versions stay stored; reinstating brings the page back.

Publications
------------
Every article belongs to one **publication**. PloverResearch is publication 1
(``HOME``) and keeps its addresses under ``/research``; every other
publication lives under ``/p/<publication>``. An article's address is unique
within its publication, not across them. Who may do what in a publication
(owner, editor, writer) is stored in ``members`` and decided in
``app/writers.py``.

Two writers, one draft
----------------------
Every save carries the revision it was based on. A save based on a revision
that is no longer current is refused with who saved the newer one, so two
people editing the same article can never silently overwrite each other.

Where it lives
--------------
``data/research/research.db`` (SQLite, WAL) and uploaded images under
``data/research/media/`` named by their SHA-256 — on the volume, never in the
DuckDB datasets. These are the only copies of original writing, so
``research_backup.py`` copies them to the object store every night.
"""
import datetime
import hashlib
import json
import os
import pathlib
import sqlite3
import struct
import threading
import time

from . import db, research_doc as rd

DATA_DIR = pathlib.Path(os.environ.get("RESEARCH_DIR") or (db.DATA_DIR / "research"))
MEDIA_MAX_BYTES = 8 * 1024 * 1024
HISTORY_KEEP = 300               # draft snapshots kept per article
HISTORY_EVERY_SECONDS = 120      # at most one automatic snapshot per writer per 2 min

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id INTEGER NOT NULL DEFAULT 1,
  slug TEXT,
  draft TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  updated_by TEXT NOT NULL,
  published_version INTEGER,
  withdrawn_at INTEGER,
  withdrawn_by TEXT,
  withdrawn_reason TEXT,
  review_requested_at INTEGER,
  review_requested_by TEXT,
  UNIQUE (publication_id, slug)
);
CREATE TABLE IF NOT EXISTS article_versions (
  article_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  slug TEXT NOT NULL,
  published_at TEXT NOT NULL,
  published_by TEXT NOT NULL,
  title TEXT NOT NULL,
  dek TEXT NOT NULL,
  summary TEXT NOT NULL,
  authors TEXT NOT NULL,
  doc TEXT NOT NULL,
  snapshots TEXT NOT NULL,
  body_html TEXT NOT NULL,
  markdown TEXT NOT NULL,
  change_note TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  PRIMARY KEY (article_id, version)
);
CREATE TRIGGER IF NOT EXISTS article_versions_no_update
BEFORE UPDATE ON article_versions
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS article_versions_no_delete
BEFORE DELETE ON article_versions
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;
CREATE TABLE IF NOT EXISTS draft_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL,
  revision INTEGER NOT NULL,
  saved_at INTEGER NOT NULL,
  saved_by TEXT NOT NULL,
  draft TEXT NOT NULL,
  label TEXT
);
CREATE INDEX IF NOT EXISTS draft_history_article ON draft_history (article_id, id);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  tagline TEXT NOT NULL DEFAULT '',
  about TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  updated_at INTEGER,
  updated_by TEXT
);
CREATE TABLE IF NOT EXISTS members (
  publication_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  role TEXT NOT NULL,
  added_at INTEGER NOT NULL,
  added_by TEXT NOT NULL,
  PRIMARY KEY (publication_id, email)
);
CREATE INDEX IF NOT EXISTS members_email ON members (email);
CREATE TABLE IF NOT EXISTS media (
  sha256 TEXT PRIMARY KEY,
  ext TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  name TEXT,
  uploaded_at INTEGER NOT NULL,
  uploaded_by TEXT NOT NULL
);
"""

HOME = 1                         # PloverResearch: its articles live under /research
HOME_SLUG = "ploverresearch"
HOME_NAME = "PloverResearch"
HOME_TAGLINE = ("Research notes on Japan and the United States, built on official statistics "
                "and company filings.")
ROLES = ("owner", "editor", "writer")
PUB_NAME_MAX = 80
PUB_TAGLINE_MAX = 200
PUB_ABOUT_MAX = 2000
PUB_SLUG_MIN, PUB_SLUG_MAX = 3, 40
# addresses a publication cannot take: /p/<slug> must never shadow something else
RESERVED_SLUGS = {HOME_SLUG, "research", "plover", "admin", "api", "new", "feed", "media",
                  "write", "mcp", "assets", "static", "help", "about", "login", "signin"}

_lock = threading.Lock()
_base = None
_local = threading.local()


class ResearchError(ValueError):
    """A request that cannot be honoured, worded for the writer."""


class Conflict(Exception):
    def __init__(self, current):
        Exception.__init__(self, "conflict")
        self.current = current


def db_path():
    return DATA_DIR / "research.db"


def media_dir():
    return DATA_DIR / "media"


def _open(path):
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    return c


def conn():
    """This thread's connection; the schema is created once per path."""
    global _base
    path = str(db_path())
    with _lock:
        if _base is None or _base[1] != path:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            c = _open(path)
            c.execute("PRAGMA journal_mode=WAL")
            _add_publications(c)
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO publications (id, slug, name, tagline, created_at, "
                      "created_by) VALUES (?, ?, ?, ?, ?, 'system')",
                      (HOME, HOME_SLUG, HOME_NAME, HOME_TAGLINE, _now()))
            c.commit()
            _base = (c, path)
        base = _base
    if getattr(_local, "base", None) is not base:
        _local.base, _local.conn = base, _open(path)
    return _local.conn


def _add_publications(c):
    """Bring a store from before publications up to date: rebuild ``articles``
    with a publication (every existing article is PloverResearch's) and an
    address that is unique per publication. SQLite cannot change a UNIQUE
    constraint in place, so the table is copied, in one transaction. The
    published versions are a different table and are not touched."""
    have = [r[1] for r in c.execute("PRAGMA table_info(articles)").fetchall()]
    if not have or "publication_id" in have:
        return
    seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name = 'articles'").fetchone()
    cols = ("id, slug, draft, revision, status, created_at, created_by, updated_at, updated_by, "
            "published_version, withdrawn_at, withdrawn_by, withdrawn_reason")
    create = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS articles ("):
                    SCHEMA.index("CREATE TABLE IF NOT EXISTS article_versions")]
    create = create.replace("IF NOT EXISTS articles (", "articles_new (")
    c.execute("BEGIN IMMEDIATE")
    try:
        c.execute(create)
        c.execute("INSERT INTO articles_new (publication_id, %s) SELECT %d, %s FROM articles"
                  % (cols, HOME, cols))
        c.execute("DROP TABLE articles")
        c.execute("ALTER TABLE articles_new RENAME TO articles")
        if seq is not None:
            # a deleted draft's number is never handed out again
            c.execute("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'articles'",
                      (seq[0],))
        c.commit()
    except Exception:
        c.rollback()
        raise


def _now():
    return int(time.time())


def _iso(ts):
    if ts is None:
        return None
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


# ---------------------------------------------------------------------------
# articles

def _summary(row, versions=None):
    draft = json.loads(row["draft"])
    out = {
        "id": row["id"],
        "publication_id": row["publication_id"],
        "slug": row["slug"] or draft.get("slug") or "",
        "title": draft.get("title") or "",
        "status": row["status"],
        "revision": row["revision"],
        "created_at": _iso(row["created_at"]),
        "created_by": row["created_by"],
        "updated_at": _iso(row["updated_at"]),
        "updated_by": row["updated_by"],
        "published_version": row["published_version"],
        "withdrawn_at": _iso(row["withdrawn_at"]),
        "withdrawn_by": row["withdrawn_by"],
        "withdrawn_reason": row["withdrawn_reason"],
        "slug_locked": row["published_version"] is not None,
        "review_requested_at": _iso(row["review_requested_at"]),
        "review_requested_by": row["review_requested_by"],
        "authors": draft.get("authors") or [],
    }
    if versions is not None:
        out["versions"] = versions
    return out


def list_articles(publication_id=None):
    """Every article, or one publication's, most recently edited first."""
    c = conn()
    if publication_id is None:
        rows = c.execute("SELECT * FROM articles ORDER BY updated_at DESC").fetchall()
    else:
        rows = c.execute("SELECT * FROM articles WHERE publication_id = ? ORDER BY updated_at DESC",
                         (publication_id,)).fetchall()
    out = []
    for r in rows:
        s = _summary(r)
        if r["published_version"]:
            v = c.execute("SELECT published_at FROM article_versions WHERE article_id = ? "
                          "AND version = ?", (r["id"], r["published_version"])).fetchone()
            s["published_at"] = v["published_at"] if v else None
            s["unpublished_changes"] = _differs(r)
        out.append(s)
    return out


def _published_doc(c, article_id, version):
    row = c.execute("SELECT doc FROM article_versions WHERE article_id = ? AND version = ?",
                    (article_id, version)).fetchone()
    return json.loads(row["doc"]) if row else None


def _differs(row):
    """Whether the draft has changed since the version on the site."""
    if not row["published_version"]:
        return False
    doc = _published_doc(conn(), row["id"], row["published_version"])
    if doc is None:
        return True
    return _comparable(json.loads(row["draft"])) != _comparable(doc)


def _comparable(doc):
    keep = dict((k, doc.get(k)) for k in ("title", "dek", "summary", "slug", "authors",
                                          "market", "topics", "lead", "feature"))
    keep["blocks"] = [dict((k, v) for k, v in b.items() if k != "snapshot")
                      for b in doc.get("blocks", [])]
    return json.dumps(keep, sort_keys=True)


def get(article_id):
    c = conn()
    row = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        return None
    versions = [dict(r) for r in c.execute(
        "SELECT version, published_at, published_by, title, change_note FROM article_versions "
        "WHERE article_id = ? ORDER BY version DESC", (article_id,))]
    out = _summary(row, versions)
    out["draft"] = json.loads(row["draft"])
    out["unpublished_changes"] = _differs(row)
    return out


def create(actor, draft=None, publication_id=HOME):
    draft = rd.clean_draft(draft or rd.new_draft())
    now = _now()
    c = conn()
    if publication(publication_id) is None:
        raise ResearchError("No such publication.")
    with _lock:
        cur = c.execute(
            "INSERT INTO articles (publication_id, draft, revision, status, created_at, created_by, "
            "updated_at, updated_by) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?)",
            (publication_id, json.dumps(draft, ensure_ascii=False), now, actor, now, actor))
        aid = cur.lastrowid
        c.execute("INSERT INTO draft_history (article_id, revision, saved_at, saved_by, draft, "
                  "label) VALUES (?, 1, ?, ?, ?, 'Created')",
                  (aid, now, actor, json.dumps(draft, ensure_ascii=False)))
        c.commit()
    return get(aid)


def save(article_id, draft, base_revision, actor, label=None):
    """Store a draft. Raises Conflict when someone saved since base_revision."""
    draft = rd.clean_draft(draft)
    now = _now()
    c = conn()
    with _lock:
        row = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            raise ResearchError("No such article.")
        if row["revision"] != base_revision:
            raise Conflict({"revision": row["revision"], "updated_by": row["updated_by"],
                            "updated_at": _iso(row["updated_at"]),
                            "draft": json.loads(row["draft"])})
        if row["published_version"]:
            # the address is part of every citation: fixed at first publication
            draft["slug"] = row["slug"]
        rev = row["revision"] + 1
        payload = json.dumps(draft, ensure_ascii=False)
        c.execute("UPDATE articles SET draft = ?, revision = ?, updated_at = ?, updated_by = ? "
                  "WHERE id = ?", (payload, rev, now, actor, article_id))
        last = c.execute("SELECT saved_at, saved_by FROM draft_history WHERE article_id = ? "
                         "ORDER BY id DESC LIMIT 1", (article_id,)).fetchone()
        if label or last is None or last["saved_by"] != actor or \
                now - last["saved_at"] >= HISTORY_EVERY_SECONDS:
            c.execute("INSERT INTO draft_history (article_id, revision, saved_at, saved_by, "
                      "draft, label) VALUES (?, ?, ?, ?, ?, ?)",
                      (article_id, rev, now, actor, payload, label))
            c.execute("DELETE FROM draft_history WHERE article_id = ? AND id NOT IN (SELECT id "
                      "FROM draft_history WHERE article_id = ? ORDER BY id DESC LIMIT ?)",
                      (article_id, article_id, HISTORY_KEEP))
        c.commit()
    return {"revision": rev, "updated_at": _iso(now), "updated_by": actor, "draft": draft}


def history(article_id):
    rows = conn().execute(
        "SELECT id, revision, saved_at, saved_by, label FROM draft_history WHERE article_id = ? "
        "ORDER BY id DESC", (article_id,)).fetchall()
    return [{"id": r["id"], "revision": r["revision"], "saved_at": _iso(r["saved_at"]),
             "saved_by": r["saved_by"], "label": r["label"]} for r in rows]


def mark(article_id, actor, label):
    """Copy the current draft into its history under a label, without saving a
    new revision — the restore point before an AI run. Returns the history id.
    When the newest history entry is already this label over the same draft
    (a conversation's next message, nothing changed since), that entry is the
    restore point and no copy is added."""
    c = conn()
    with _lock:
        row = c.execute("SELECT revision, draft FROM articles WHERE id = ?",
                        (article_id,)).fetchone()
        if row is None:
            raise ResearchError("No such article.")
        last = c.execute("SELECT id, revision, draft, label FROM draft_history WHERE article_id = ? "
                         "ORDER BY id DESC LIMIT 1", (article_id,)).fetchone()
        if last is not None and last["label"] == label and last["revision"] == row["revision"] \
                and last["draft"] == row["draft"]:
            return last["id"]
        cur = c.execute("INSERT INTO draft_history (article_id, revision, saved_at, saved_by, "
                        "draft, label) VALUES (?, ?, ?, ?, ?, ?)",
                        (article_id, row["revision"], _now(), actor, row["draft"], label))
        c.commit()
    return cur.lastrowid


def history_draft(article_id, history_id):
    row = conn().execute("SELECT draft FROM draft_history WHERE article_id = ? AND id = ?",
                         (article_id, history_id)).fetchone()
    return json.loads(row["draft"]) if row else None


def delete_draft(article_id):
    """Only an article that has never been published can be deleted."""
    c = conn()
    with _lock:
        row = c.execute("SELECT published_version FROM articles WHERE id = ?",
                        (article_id,)).fetchone()
        if row is None:
            raise ResearchError("No such article.")
        if row["published_version"]:
            raise ResearchError("A published article cannot be deleted; withdraw it instead.")
        c.execute("DELETE FROM draft_history WHERE article_id = ?", (article_id,))
        c.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        c.commit()


def slug_taken(slug, article_id, c=None, publication_id=None):
    """Whether another article in the same publication has (or had) this
    address. The publication is the article's own unless one is given."""
    # pass the connection when already holding _lock: conn() takes it too
    c = c or conn()
    if publication_id is None:
        row = c.execute("SELECT publication_id FROM articles WHERE id = ?", (article_id,)).fetchone()
        publication_id = row["publication_id"] if row else HOME
    row = c.execute("SELECT id FROM articles WHERE slug = ? AND id != ? AND publication_id = ?",
                    (slug, article_id, publication_id)).fetchone()
    if row:
        return True
    row = c.execute("SELECT 1 FROM article_versions v JOIN articles a ON a.id = v.article_id "
                    "WHERE v.slug = ? AND v.article_id != ? AND a.publication_id = ?",
                    (slug, article_id, publication_id)).fetchone()
    return row is not None


def request_review(article_id, actor):
    """A writer asks an editor to publish: shown on the article list until a
    version is published."""
    c = conn()
    with _lock:
        cur = c.execute("UPDATE articles SET review_requested_at = ?, review_requested_by = ? "
                        "WHERE id = ?", (_now(), actor, article_id))
        c.commit()
    if not cur.rowcount:
        raise ResearchError("No such article.")


# ---------------------------------------------------------------------------
# publishing

def publish(article_id, base_revision, actor, change_note, draft, author_names,
            snapshots, body_html, markdown):
    """Insert the next version. The caller has already validated the draft and
    built the snapshots and renderings from it; this only stores them, inside
    one transaction, after checking nobody saved in between."""
    now = _now()
    c = conn()
    with _lock:
        row = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            raise ResearchError("No such article.")
        if row["revision"] != base_revision:
            raise Conflict({"revision": row["revision"], "updated_by": row["updated_by"],
                            "updated_at": _iso(row["updated_at"]),
                            "draft": json.loads(row["draft"])})
        slug = row["slug"] or draft["slug"]
        if row["slug"] is None and slug_taken(slug, article_id, c, row["publication_id"]):
            raise ResearchError("Another article already uses the address %s."
                                % address(row["publication_id"], slug))
        version = (row["published_version"] or 0) + 1
        if version > 1 and not change_note:
            raise ResearchError("Say what changed in this version — readers see the note.")
        published_at = datetime.datetime.utcfromtimestamp(now).isoformat() + "Z"
        # the plan and the research notes stay in the desk, never in a version
        doc = json.dumps(rd.public_draft(draft), ensure_ascii=False, sort_keys=True)
        snaps = json.dumps(snapshots, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256((doc + "\n" + snaps + "\n" + body_html).encode("utf-8")).hexdigest()
        c.execute(
            "INSERT INTO article_versions (article_id, version, slug, published_at, published_by, "
            "title, dek, summary, authors, doc, snapshots, body_html, markdown, change_note, "
            "sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (article_id, version, slug, published_at, actor, draft["title"], draft["dek"],
             draft["summary"], json.dumps(author_names, ensure_ascii=False), doc, snaps,
             body_html, markdown, change_note or "First published.", digest))
        c.execute("UPDATE articles SET slug = ?, status = 'published', published_version = ?, "
                  "withdrawn_at = NULL, withdrawn_by = NULL, withdrawn_reason = NULL, "
                  "review_requested_at = NULL, review_requested_by = NULL "
                  "WHERE id = ?", (slug, version, article_id))
        c.execute("INSERT INTO draft_history (article_id, revision, saved_at, saved_by, draft, "
                  "label) VALUES (?, ?, ?, ?, ?, ?)",
                  (article_id, row["revision"], now, actor, row["draft"],
                   "Published as version %d" % version))
        c.commit()
    return {"version": version, "slug": slug, "published_at": published_at, "sha256": digest}


def withdraw(article_id, actor, reason):
    reason = rd.plain(reason, 400)
    if not reason:
        raise ResearchError("Give the reason readers will see.")
    c = conn()
    with _lock:
        row = c.execute("SELECT published_version FROM articles WHERE id = ?",
                        (article_id,)).fetchone()
        if row is None or not row["published_version"]:
            raise ResearchError("Only a published article can be withdrawn.")
        c.execute("UPDATE articles SET status = 'withdrawn', withdrawn_at = ?, withdrawn_by = ?, "
                  "withdrawn_reason = ? WHERE id = ?", (_now(), actor, reason, article_id))
        c.commit()


def reinstate(article_id):
    c = conn()
    with _lock:
        row = c.execute("SELECT status FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None or row["status"] != "withdrawn":
            raise ResearchError("Only a withdrawn article can be reinstated.")
        c.execute("UPDATE articles SET status = 'published', withdrawn_at = NULL, "
                  "withdrawn_by = NULL, withdrawn_reason = NULL WHERE id = ?", (article_id,))
        c.commit()


# ---------------------------------------------------------------------------
# reading published work

def _version_dict(r):
    d = dict(r)
    d["authors"] = json.loads(d["authors"])
    d["doc"] = json.loads(d["doc"])
    d["snapshots"] = json.loads(d["snapshots"])
    return d


def by_slug(slug, publication_id=HOME):
    """The article row for a public address, or None."""
    return conn().execute("SELECT * FROM articles WHERE slug = ? AND publication_id = ?",
                          (slug, publication_id)).fetchone()


def version(article_id, number):
    r = conn().execute("SELECT * FROM article_versions WHERE article_id = ? AND version = ?",
                       (article_id, number)).fetchone()
    return _version_dict(r) if r else None


def versions_meta(article_id):
    return [dict(r) for r in conn().execute(
        "SELECT version, published_at, published_by, change_note FROM article_versions "
        "WHERE article_id = ? ORDER BY version", (article_id,))]


def published(publication_id=HOME):
    """A publication's live articles (every publication's with None), each at
    its latest version, newest first by first publication."""
    c = conn()
    where, args = "a.status = 'published'", ()
    if publication_id is not None:
        where, args = where + " AND a.publication_id = ?", (publication_id,)
    rows = c.execute(
        "SELECT a.id, a.publication_id, a.slug, v.version, v.title, v.dek, v.summary, v.authors, "
        "v.published_at, v.doc, v.snapshots, "
        "(SELECT published_at FROM article_versions f WHERE f.article_id = a.id AND f.version = 1) "
        "AS first_published_at "
        "FROM articles a JOIN article_versions v ON v.article_id = a.id "
        "AND v.version = a.published_version WHERE " + where + " "
        "ORDER BY first_published_at DESC, a.id DESC", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = json.loads(d["authors"])
        d["doc"] = json.loads(d["doc"])
        d["snapshots"] = json.loads(d["snapshots"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# publications and their members

def _pub(row):
    if row is None:
        return None
    d = dict(row)
    d["home"] = d["id"] == HOME
    d["base"] = base_path(d)
    return d


def base_path(pub):
    """Where a publication's pages live: /research for PloverResearch."""
    if pub is None or pub["id"] == HOME:
        return "/research"
    return "/p/" + pub["slug"]


def address(publication_id, slug):
    """The public path of an article: /research/<slug> or /p/<pub>/<slug>."""
    return base_path(publication(publication_id)) + "/" + slug


def publication(publication_id):
    return _pub(conn().execute("SELECT * FROM publications WHERE id = ?",
                               (publication_id,)).fetchone())


def publication_by_slug(slug):
    return _pub(conn().execute("SELECT * FROM publications WHERE slug = ?",
                               ((slug or "").lower(),)).fetchone())


def list_publications():
    return [_pub(r) for r in conn().execute("SELECT * FROM publications ORDER BY id")]


def _pub_text(name, tagline, about):
    name = rd.plain(name, PUB_NAME_MAX + 1)
    if not name or len(name) > PUB_NAME_MAX:
        raise ResearchError("Give the publication a name of up to %d characters." % PUB_NAME_MAX)
    tagline = rd.plain(tagline, PUB_TAGLINE_MAX)
    about = rd.plain(about, PUB_ABOUT_MAX)
    return name, tagline, about


def create_publication(slug, name, tagline, actor, about=""):
    slug = (slug or "").strip().lower()
    if not (PUB_SLUG_MIN <= len(slug) <= PUB_SLUG_MAX) or not rd._SLUG.match(slug):
        raise ResearchError("The address must be %d to %d letters, digits or hyphens, such as "
                            "asia-macro-notes." % (PUB_SLUG_MIN, PUB_SLUG_MAX))
    if slug in RESERVED_SLUGS:
        raise ResearchError("The address /p/%s is reserved. Choose another." % slug)
    name, tagline, about = _pub_text(name, tagline, about)
    c = conn()
    with _lock:
        if c.execute("SELECT 1 FROM publications WHERE slug = ?", (slug,)).fetchone():
            raise ResearchError("Another publication already uses /p/%s." % slug)
        cur = c.execute("INSERT INTO publications (slug, name, tagline, about, created_at, "
                        "created_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (slug, name, tagline, about, _now(), actor))
        c.commit()
    return publication(cur.lastrowid)


def update_publication(publication_id, actor, name, tagline, about):
    """Name, tagline and description. The address never changes: it is in
    every citation of every article."""
    name, tagline, about = _pub_text(name, tagline, about)
    c = conn()
    with _lock:
        cur = c.execute("UPDATE publications SET name = ?, tagline = ?, about = ?, updated_at = ?, "
                        "updated_by = ? WHERE id = ?",
                        (name, tagline, about, _now(), actor, publication_id))
        c.commit()
    if not cur.rowcount:
        raise ResearchError("No such publication.")
    return publication(publication_id)


def members(publication_id):
    return [dict(r) for r in conn().execute(
        "SELECT * FROM members WHERE publication_id = ? ORDER BY added_at, email",
        (publication_id,))]


def member_role(publication_id, email):
    row = conn().execute("SELECT role FROM members WHERE publication_id = ? AND email = ?",
                         (publication_id, (email or "").strip().lower())).fetchone()
    return row["role"] if row else None


def memberships(email):
    """{publication id: role} for one person's stored memberships."""
    return dict((r["publication_id"], r["role"]) for r in conn().execute(
        "SELECT publication_id, role FROM members WHERE email = ?",
        ((email or "").strip().lower(),)))


def set_member(publication_id, email, role, actor):
    email = (email or "").strip().lower()
    if role not in ROLES:
        raise ResearchError("A role is owner, editor or writer.")
    if publication(publication_id) is None:
        raise ResearchError("No such publication.")
    c = conn()
    with _lock:
        c.execute("INSERT INTO members (publication_id, email, role, added_at, added_by) "
                  "VALUES (?, ?, ?, ?, ?) ON CONFLICT(publication_id, email) DO UPDATE SET "
                  "role = excluded.role", (publication_id, email, role, _now(), actor))
        c.commit()


def remove_member(publication_id, email):
    c = conn()
    with _lock:
        cur = c.execute("DELETE FROM members WHERE publication_id = ? AND email = ?",
                        (publication_id, (email or "").strip().lower()))
        c.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# media

def _image_info(data):
    """(ext, width, height) for a PNG, JPEG or WebP, else None. Read from the
    file's own header, never from the name or the declared type."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD,
                          0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpg", w, h
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
        return "jpg", None, None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        try:
            if kind == b"VP8X":
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return "webp", w, h
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return "webp", w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                b = data[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return "webp", w, h
        except (struct.error, IndexError):
            pass
        return "webp", None, None
    return None


def add_media(data, name, actor):
    if not data:
        raise ResearchError("The file is empty.")
    if len(data) > MEDIA_MAX_BYTES:
        raise ResearchError("Images can be up to %d MB." % (MEDIA_MAX_BYTES // (1024 * 1024)))
    info = _image_info(data)
    if info is None:
        raise ResearchError("Upload a PNG, JPEG or WebP image.")
    ext, w, h = info
    sha = hashlib.sha256(data).hexdigest()
    folder = media_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("%s.%s" % (sha, ext))
    if not path.exists():
        tmp = folder / ("%s.%s.tmp" % (sha, ext))
        with open(str(tmp), "wb") as f:
            f.write(data)
        os.replace(str(tmp), str(path))
    c = conn()
    with _lock:
        c.execute("INSERT OR IGNORE INTO media (sha256, ext, bytes, width, height, name, "
                  "uploaded_at, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (sha, ext, len(data), w, h, rd.plain(name, 200), _now(), actor))
        c.commit()
    return {"media": sha, "ext": ext, "width": w, "height": h, "bytes": len(data)}


def media_file(sha, ext):
    if not rd._SHA.match(sha or "") or ext not in rd.IMAGE_EXTS:
        return None
    path = media_dir() / ("%s.%s" % (sha, ext))
    return path if path.is_file() else None
