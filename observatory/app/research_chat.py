# -*- coding: utf-8 -*-
"""The research desk AI tab's conversations: one person, one article, many turns.

A conversation belongs to the sign-in account (found by email) of the person
who had it, and to one article; nobody else sees it, co-writers included. Each
run adds two turns: the writer's message, and the assistant's reply, which is
written as "running" when the run starts and filled in when it ends (so a
reload, or a restart mid-run, still shows what was asked). The next run in the
same conversation is sent the earlier turns (`history`), which is what makes a
follow-up work with every provider: the API keys as messages, Claude Code and
Codex as a transcript before the instruction.

Kept in the Investment Assistant's store file (ia_article_chats,
ia_article_turns), beside the person's memory and data sources.
"""
import json

from .assistant import store

MAX_HISTORY = 12            # turns sent back to the model: six exchanges
TURN_MAX = 6000             # characters of one earlier turn the model is sent
TITLE_MAX = 80


def _shape_turn(r):
    r = dict(r)
    r["meta"] = store._loads(r.pop("meta_json"), {})
    return r


def chats(account_id, article_id):
    return store._rows(store.conn().execute(
        "SELECT c.id, c.title, c.created_at, c.updated_at, "
        "(SELECT count(*) FROM ia_article_turns t WHERE t.chat_id = c.id AND t.role = 'user') AS turns "
        "FROM ia_article_chats c WHERE c.account_id = ? AND c.article_id = ? "
        "ORDER BY c.updated_at DESC, c.id DESC", (account_id, article_id)))


def chat(account_id, chat_id):
    c = store._row(store.conn().execute(
        "SELECT id, account_id, article_id, title, created_at, updated_at FROM ia_article_chats "
        "WHERE id = ? AND account_id = ?", (chat_id, account_id)))
    if c is None:
        return None
    c["turns"] = [_shape_turn(r) for r in store._rows(store.conn().execute(
        "SELECT id, role, status, text, meta_json, created_at, updated_at FROM ia_article_turns "
        "WHERE chat_id = ? ORDER BY id", (chat_id,)))]
    return c


def _title(text):
    t = " ".join(str(text or "").split())
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1].rstrip() + "…"


def start_chat(account_id, article_id, first_text):
    t = store.now()
    cid = store._write("INSERT INTO ia_article_chats (account_id, article_id, title, created_at, "
                       "updated_at) VALUES (?, ?, ?, ?, ?)",
                       (account_id, article_id, _title(first_text) or "Conversation", t, t))
    return chat(account_id, cid)


def add_turn(chat_id, role, status, text, meta=None):
    t = store.now()
    tid = store._write("INSERT INTO ia_article_turns (chat_id, role, status, text, meta_json, "
                       "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (chat_id, role, status, text or "", json.dumps(meta or {}, ensure_ascii=False), t, t))
    store._write("UPDATE ia_article_chats SET updated_at = ? WHERE id = ?", (t, chat_id))
    return tid


def finish_turn(turn_id, status, text, meta):
    store._write("UPDATE ia_article_turns SET status = ?, text = ?, meta_json = ?, updated_at = ? "
                 "WHERE id = ?", (status, text or "", json.dumps(meta or {}, ensure_ascii=False),
                                  store.now(), turn_id))


def turn_for_job(account_id, job_id):
    """The reply a run is writing (or wrote), found by the run's id: how a
    copy of the app that did not start the run (after a deploy) still knows
    it. None when this account has no such run."""
    if not account_id or not job_id:
        return None
    rows = store._rows(store.conn().execute(
        "SELECT t.id, t.chat_id, t.role, t.status, t.text, t.meta_json, t.created_at, t.updated_at, "
        "c.article_id FROM ia_article_turns t JOIN ia_article_chats c ON c.id = t.chat_id "
        "WHERE c.account_id = ? AND t.role = 'assistant' AND t.meta_json LIKE ?",
        (account_id, "%" + job_id + "%")))
    for r in rows:
        t = _shape_turn(r)
        if t["meta"].get("job_id") == job_id:
            return t
    return None


def delete_chat(account_id, chat_id):
    if chat(account_id, chat_id) is None:
        return False
    store._write("DELETE FROM ia_article_turns WHERE chat_id = ?", (chat_id,))
    store._write("DELETE FROM ia_article_chats WHERE id = ? AND account_id = ?", (chat_id, account_id))
    return True


def history(c):
    """The earlier exchanges the next run is sent, oldest first: each of the
    writer's messages with the reply it got. An exchange whose run failed or
    is still running is left out, so the model never sees two messages from
    the writer in a row."""
    out = []
    turns = (c or {}).get("turns") or []
    for i, t in enumerate(turns):
        if t["role"] != "user" or i + 1 >= len(turns):
            continue
        reply = turns[i + 1]
        if reply["role"] != "assistant" or reply["status"] != "done" or not reply["text"].strip():
            continue
        said = t["text"]
        if (t.get("meta") or {}).get("skill"):
            said = "(skill: %s) %s" % (t["meta"]["skill"], said)
        answer = reply["text"]
        if (reply.get("meta") or {}).get("changed"):
            answer += "\n\n[You changed the draft in this turn.]"
        out.append({"role": "user", "text": said[:TURN_MAX]})
        out.append({"role": "assistant", "text": answer[:TURN_MAX]})
    return out[-MAX_HISTORY:]


def transcript(hist):
    """The earlier exchanges as text, for a CLI that takes one prompt."""
    if not hist:
        return ""
    lines = ["Earlier in this conversation with the writer (oldest first):"]
    for h in hist:
        lines.append(("The writer: " if h["role"] == "user" else "You: ") + h["text"])
    return "\n\n".join(lines) + "\n\n---\n\n"
