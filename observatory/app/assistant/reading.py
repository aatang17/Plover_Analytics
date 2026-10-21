# -*- coding: utf-8 -*-
"""Your reading: articles a signed-in person saves from their own browser.

How an article arrives
----------------------
The Plover browser extension (observatory/extension/) or the Save to Plover
bookmark reads the article in the person's own browser, where they are
signed in to the publisher, and opens /save.html with the text in the
address's #fragment. The fragment never travels in a request, so the text is
in no server or proxy log; save.html posts it here with the person's Plover
session. This is the Writer Desk's Send to Plover (app/research_clips.py),
kept per account instead of per writer, so it works for everyone who signs in.

What is kept, and who sees it
-----------------------------
Text only, as the browser extracted it, never rendered as HTML. A saved
article belongs to the account that saved it: only that account can list,
read, summarise or delete it. The same address saved again replaces the
earlier copy, and each account keeps its newest KEEP articles.

Summaries
---------
One short model call on the person's own AI key, the same key their desk
uses (Account › Settings). The summary is the article's points in its own
numbers; it adds nothing the article does not say, and it is labelled as a
summary of that article, not as Plover's data. Without a key the endpoint
says what to do; it never falls back to Plover's own key.
"""
import io
import json
import pathlib
import re
import time
import urllib.parse
import zipfile
from typing import List

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import accounts, research_clips
from . import codex, free, gateway, keychain, store

KEEP = 200
SUMMARY_INPUT_MAX = 14000      # characters of article sent to the model
SUMMARY_TOKENS = 450

SCHEMA = """
CREATE TABLE IF NOT EXISTS ia_reading (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    match_key TEXT NOT NULL,
    title TEXT NOT NULL,
    site TEXT NOT NULL,
    published TEXT NOT NULL,
    author TEXT NOT NULL,
    blocks TEXT NOT NULL,
    chars INTEGER NOT NULL,
    selection INTEGER NOT NULL,
    summary TEXT,
    summary_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_reading_by_account ON ia_reading (account_id, created_at);
"""

_ready = set()

NO_KEY = ("Summaries run on your own AI key. Add one in the AI Key section, then try again.")


class ReadingError(ValueError):
    """Worded for the reader."""


def _conn():
    c = store.conn()
    key = str(store.DB_PATH)
    if key not in _ready:
        c.executescript(SCHEMA)
        c.commit()
        _ready.add(key)
    return c


def reset_for_tests():
    _ready.clear()


def _shape(r, full=False):
    out = {"id": r["id"], "url": r["url"], "title": r["title"], "site": r["site"],
           "published": r["published"], "author": r["author"], "chars": r["chars"],
           "selection": bool(r["selection"]), "saved_at": r["created_at"],
           "summary": r["summary"], "summary_at": r["summary_at"]}
    if full:
        out["blocks"] = json.loads(r["blocks"])
    return out


def add(account_id, url, title, site, published, author, blocks, selection=False):
    try:
        url = research_clips._check_url(
            url, "The article's address did not come through. Click Save to Plover again on the page.")
    except research_clips.ClipError as exc:
        raise ReadingError(str(exc))
    kept, total = [], 0
    for b in (blocks or [])[:research_clips.BLOCKS_MAX]:
        kind = b.get("kind") if isinstance(b, dict) else None
        text = research_clips._clean(b.get("text") if isinstance(b, dict) else "", 20000)
        if kind not in research_clips.KINDS or not text:
            continue
        if total + len(text) > research_clips.TEXT_MAX:
            break
        kept.append({"kind": kind, "text": text})
        total += len(text)
    if not kept:
        raise ReadingError("No article text came through from that page. Open the article itself "
                           "(not a list of headlines), or select the passage you want and save again.")
    host = urllib.parse.urlsplit(url).hostname or ""
    key = research_clips.match_key(url)
    row = (account_id, url, key, research_clips._clean(title, 300) or url,
           research_clips._clean(site, 120) or host, research_clips._clean(published, 30),
           research_clips._clean(author, 120), json.dumps(kept, ensure_ascii=False),
           total, 1 if selection else 0, int(time.time()))
    c = _conn()
    with store._lock:
        c.execute("DELETE FROM ia_reading WHERE account_id = ? AND match_key = ?", (account_id, key))
        cur = c.execute(
            "INSERT INTO ia_reading (account_id, url, match_key, title, site, published, author, "
            "blocks, chars, selection, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
        new_id = cur.lastrowid
        c.execute("DELETE FROM ia_reading WHERE account_id = ? AND id NOT IN (SELECT id FROM "
                  "ia_reading WHERE account_id = ? ORDER BY created_at DESC, id DESC LIMIT ?)",
                  (account_id, account_id, KEEP))
        c.commit()
    return get(account_id, new_id)


def list_for(account_id):
    rows = _conn().execute("SELECT * FROM ia_reading WHERE account_id = ? "
                           "ORDER BY created_at DESC, id DESC", (account_id,)).fetchall()
    return [_shape(r) for r in rows]


def get(account_id, item_id):
    r = _conn().execute("SELECT * FROM ia_reading WHERE id = ? AND account_id = ?",
                        (item_id, account_id)).fetchone()
    return _shape(r, full=True) if r is not None else None


def delete(account_id, item_id):
    c = _conn()
    with store._lock:
        n = c.execute("DELETE FROM ia_reading WHERE id = ? AND account_id = ?",
                      (item_id, account_id)).rowcount
        c.commit()
    return n > 0


def _set_summary(account_id, item_id, text):
    c = _conn()
    with store._lock:
        c.execute("UPDATE ia_reading SET summary = ?, summary_at = ? WHERE id = ? AND account_id = ?",
                  (text, int(time.time()), item_id, account_id))
        c.commit()


SUMMARY_RULES = (
    "You summarise one news or research article for a finance professional. "
    "Write 3 to 5 bullet points, each one short sentence, in plain English, starting with '- '. "
    "Use only what the article says. Keep every number exactly as the article gives it, with its "
    "unit and period. Do not add context, opinions or anything the article does not state. "
    "If the text is not an article, say so in one line instead.")


def _article_text(item):
    parts, n = [], 0
    for b in item["blocks"]:
        t = b["text"]
        if n + len(t) > SUMMARY_INPUT_MAX:
            parts.append(t[:max(0, SUMMARY_INPUT_MAX - n)])
            break
        parts.append(("## " + t) if b["kind"] == "h" else t)
        n += len(t)
    return "\n\n".join(p for p in parts if p)


def summarise(account_id, item_id, house=None):
    """Summarise a saved article on the account's own model key, or on the
    server's for a free try (`house`, from free.take). Returns the item with
    its summary; raises ReadingError worded for the reader."""
    item = get(account_id, item_id)
    if item is None:
        raise LookupError(item_id)
    if house:
        provider, key, model = house["provider"], house["key"], house["model"]
    else:
        desk = store.desk(account_id)
        provider = desk.get("model_provider")
        if not provider or not desk.get("model_key_set"):
            raise ReadingError(NO_KEY)
        if provider == codex.PROVIDER:
            raise ReadingError("Summaries need an AI key; a ChatGPT plan through Codex cannot make "
                               "them yet. Add a key in the AI Key section.")
        try:
            key = keychain.open_(store.desk_secrets(account_id)["model_key_ct"])
        except (ValueError, TypeError):
            raise ReadingError("Your AI key could not be read. Add it again in the AI Key section.")
        model = desk.get("study_model") or desk.get("model_name") or gateway.default_model(provider)
    head = item["title"] + (" (" + item["site"] + ")" if item["site"] else "")
    messages = [{"role": "system", "content": SUMMARY_RULES},
                {"role": "user", "content": head + "\n\n" + _article_text(item)}]
    try:
        out = gateway.complete(provider, key, model, messages, tools=None, max_tokens=SUMMARY_TOKENS)
    except gateway.GatewayError as exc:
        raise ReadingError("The summary could not be made (%s). Try again in a moment."
                           % str(exc).rstrip(".")[:300])
    text = (out.get("text") or "").strip()
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l).strip()
             for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if l][:6]
    if not lines:
        raise ReadingError("The summary came back empty. Try again in a moment.")
    summary = "\n".join("- " + l for l in lines)
    _set_summary(account_id, item_id, summary)
    return get(account_id, item_id)


# ---------------------------------------------------------------------------
# the API: /api/v1/assistant/reading, mounted from app/assistant/api.py

router = APIRouter(prefix="/reading", tags=["assistant"])

GONE = "That article is no longer in your reading."


def _account(request):
    account = accounts.current_account(request)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    return account


class Block(BaseModel):
    kind: str
    text: str


class SaveBody(BaseModel):
    url: str
    title: str = ""
    site: str = ""
    published: str = ""
    author: str = ""
    selection: bool = False
    blocks: List[Block] = []


@router.post("")
def save_article(body: SaveBody, request: Request):
    aid = _account(request)["id"]
    try:
        item = add(aid, body.url, body.title, body.site, body.published, body.author,
                   [{"kind": b.kind, "text": b.text} for b in body.blocks], body.selection)
    except ReadingError as exc:
        raise HTTPException(400, str(exc))
    item.pop("blocks", None)
    return item


@router.get("")
def list_reading(request: Request):
    aid = _account(request)["id"]
    return {"reading": list_for(aid), "keep": KEEP}


EXTENSION_DIR = pathlib.Path(__file__).resolve().parents[2] / "extension" / "chrome"
EXTRACTOR = pathlib.Path(__file__).resolve().parents[2] / "web" / "assets" / "plover-extract.js"


def _origin(request):
    """The server the downloaded extension saves to: this one. Behind the
    production proxy the request arrives as plain http, so the public host
    is always https."""
    host = (request.headers.get("host") or "").strip()
    if host.endswith("ploveranalytics.com"):
        return "https://" + host
    return str(request.base_url).rstrip("/")


def extension_zip(origin):
    """The Chrome extension as a zip: its folder, the shared article reader as
    extract.js, and config.js pointing at origin."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("manifest.json", "background.js", "README.md"):
            if (EXTENSION_DIR / name).exists() or name != "README.md":
                z.write(str(EXTENSION_DIR / name), "plover-extension/" + name)
        z.writestr("plover-extension/extract.js", EXTRACTOR.read_text(encoding="utf-8"))
        z.writestr("plover-extension/config.js",
                   "/* Which Plover server the extension saves to. */\nvar PLOVER_ORIGIN = %s;\n"
                   % json.dumps(origin))
    return buf.getvalue()


@router.get("/extension.zip")
def download_extension(request: Request):
    """No sign-in needed: the extension holds nothing of the reader's."""
    return Response(extension_zip(_origin(request)), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="plover-extension.zip"'})


@router.get("/{item_id}")
def read_article(item_id: int, request: Request):
    aid = _account(request)["id"]
    item = get(aid, item_id)
    if item is None:
        raise HTTPException(404, GONE)
    return item


@router.delete("/{item_id}")
def delete_article(item_id: int, request: Request):
    aid = _account(request)["id"]
    if not delete(aid, item_id):
        raise HTTPException(404, GONE)
    return {"ok": True}


@router.post("/{item_id}/summary")
async def summarise_article(item_id: int, request: Request):
    aid = _account(request)["id"]
    house = None
    if not free.own_key(store.desk(aid)):
        if get(aid, item_id) is None:
            raise HTTPException(404, GONE)
        try:
            house = free.take(aid)
        except free.NoTries as exc:
            raise HTTPException(400, str(exc))
    try:
        item = await run_in_threadpool(summarise, aid, item_id, house)
    except LookupError:
        if house:
            free.give_back(aid)
        raise HTTPException(404, GONE)
    except ReadingError as exc:
        if house:
            free.give_back(aid)
        raise HTTPException(400, str(exc))
    except Exception:
        if house:
            free.give_back(aid)
        raise
    item.pop("blocks", None)
    return item
