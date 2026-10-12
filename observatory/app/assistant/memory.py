"""Memory: what a person's assistant has learned about them, across conversations.

One list of short notes per sign-in account (found by email), shared by every
place that person talks to an assistant: the research desk's AI tab and the
Investment Assistant's chat. A note is a fact about the person — what they
cover, what they expect, how they like things written — never a figure: every
number still comes from a tool in the run that states it.

The assistant saves on its own (remember) and the person sees each save under
the reply, with Undo. They can also say "remember …" or "forget …". Under My
Account (Writer Desk) and Memory (Investment Assistant) they can read, edit and
delete every note, clear them all, or switch memory off; while it is off a run
gets no notes and no memory tools. Nobody else can see or use a person's notes.

A note records where it was saved (`place`) and by what (`origin`: "desk:<job
id>", "run:<run id>", or "you"), so a reply can show the notes it saved.
"""
import datetime
import json
import re

from . import store

MAX_NOTES = 60
NOTE_MAX = 280

DESK = "Writer Desk"
ASSISTANT = "Investment Assistant"
YOU = "You"

TOOL_NAMES = ("remember", "forget_memory")

TOOL_SCHEMAS = [
    {"name": "remember",
     "description": "Save one durable fact about the person you work for, so later conversations "
                    "can use it: their role and coverage, the companies and themes they follow, "
                    "a view or expectation they hold (with today's date), how they like things "
                    "written. One fact per call, under %d characters, written about them "
                    "('Covers Japanese regional banks.'). They see each save and can undo it." % NOTE_MAX,
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "The fact, one sentence."}}, "required": ["text"]}},
    {"name": "forget_memory",
     "description": "Remove one note from memory by its id (the #number in your instructions), "
                    "when the person asks you to forget it or it is no longer true.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "integer", "description": "The note's id."}}, "required": ["id"]}},
]


class NoteError(ValueError):
    pass


def _clean(text):
    return " ".join(str(text or "").split())


def _key(text):
    return re.sub(r"[^\w]+", " ", _clean(text).lower()).strip()


def enabled(account_id):
    if not account_id:
        return False
    r = store._row(store.conn().execute(
        "SELECT enabled FROM ia_memory_prefs WHERE account_id = ?", (account_id,)))
    return True if r is None else bool(r["enabled"])


def set_enabled(account_id, on):
    store._write("INSERT INTO ia_memory_prefs (account_id, enabled, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (account_id) DO UPDATE SET enabled = excluded.enabled, "
                 "updated_at = excluded.updated_at", (account_id, 1 if on else 0, store.now()))


def notes(account_id):
    if not account_id:
        return []
    return store._rows(store.conn().execute(
        "SELECT id, text, place, origin, created_at, updated_at FROM ia_memories "
        "WHERE account_id = ? ORDER BY id", (account_id,)))


def note(account_id, note_id):
    return store._row(store.conn().execute(
        "SELECT id, text, place, origin, created_at, updated_at FROM ia_memories "
        "WHERE account_id = ? AND id = ?", (account_id, note_id)))


def saved_by(account_id, origin):
    """The notes one run saved that are still kept, oldest first."""
    if not account_id or not origin:
        return []
    return store._rows(store.conn().execute(
        "SELECT id, text FROM ia_memories WHERE account_id = ? AND origin = ? ORDER BY id",
        (account_id, origin)))


def view(account_id):
    return {"enabled": enabled(account_id), "notes": notes(account_id),
            "max_notes": MAX_NOTES, "note_max": NOTE_MAX}


def add(account_id, text, origin, place):
    """The saved note as {id, text, existing}; NoteError worded for the
    person (or the model) when it cannot be kept."""
    text = _clean(text)
    if not text:
        raise NoteError("A note needs some text.")
    if len(text) > NOTE_MAX:
        raise NoteError("Keep a note under %d characters; it is %d." % (NOTE_MAX, len(text)))
    have = notes(account_id)
    for n in have:
        if _key(n["text"]) == _key(text):
            return {"id": n["id"], "text": n["text"], "existing": True}
    if len(have) >= MAX_NOTES:
        raise NoteError("Memory is full (%d notes). Remove some under My Account, Memory."
                           % MAX_NOTES)
    t = store.now()
    nid = store._write("INSERT INTO ia_memories (account_id, text, origin, place, created_at, "
                       "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (account_id, text, origin, place, t, t))
    return {"id": nid, "text": text, "existing": False}


def edit(account_id, note_id, text):
    text = _clean(text)
    if not text:
        raise NoteError("A note needs some text. To remove it, press Delete.")
    if len(text) > NOTE_MAX:
        raise NoteError("Keep a note under %d characters; it is %d." % (NOTE_MAX, len(text)))
    if note(account_id, note_id) is None:
        return None
    store._write("UPDATE ia_memories SET text = ?, updated_at = ? WHERE id = ? AND account_id = ?",
                 (text, store.now(), note_id, account_id))
    return note(account_id, note_id)


def delete(account_id, note_id):
    if note(account_id, note_id) is None:
        return False
    store._write("DELETE FROM ia_memories WHERE id = ? AND account_id = ?", (note_id, account_id))
    return True


def clear(account_id):
    store._write("DELETE FROM ia_memories WHERE account_id = ?", (account_id,))


def _day(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%-d %b %Y")


def prompt_text(account_id, name=None):
    """The memory section of a run's instructions; "" while memory is off."""
    if not enabled(account_id):
        return ""
    who = name or "the person you work for"
    have = notes(account_id)
    if have:
        head = ("What you remember about %s from earlier conversations (each note has an id; a "
                "later note wins over an earlier one):\n" % who +
                "\n".join("- #%d (%s) %s" % (n["id"], _day(n["updated_at"]), n["text"]) for n in have))
    else:
        head = "You have no notes about %s yet." % who
    return (head + "\n\n"
            "Use these notes to fit your help to them. They are facts about the person, never a "
            "source of figures: every number still comes from a tool in this run.\n\n"
            "Memory: when you learn something durable about them that will help in later "
            "conversations, save it with remember, on your own, without asking: their role and "
            "coverage, companies and themes they follow, views and expectations they state (with "
            "today's date), how they like things written, projects they are working on. Save it "
            "in the same turn you learn it, in their own terms: write only what they told you, "
            "with nothing guessed or added. They see each save under your reply and can undo it. "
            "Do not save figures or data, one-off "
            "requests, anything about other people, passwords or keys, or anything they ask you "
            "not to keep. When they ask you to remember or forget something, do it "
            "(forget_memory takes the note's id). Mention memory in your reply only when you "
            "saved or removed something.\n\n")


def run_tool(account_id, name, args, origin, place):
    """(text, is_error) for remember / forget_memory in a run."""
    args = args if isinstance(args, dict) else {}
    if not account_id or not enabled(account_id):
        return json.dumps({"error": "Memory is switched off for this person."}), True
    if name == "remember":
        try:
            n = add(account_id, args.get("text"), origin, place)
        except NoteError as exc:
            return json.dumps({"error": str(exc)}), True
        return json.dumps({"saved": n["id"], "already_saved": n["existing"]}), False
    if name == "forget_memory":
        try:
            nid = int(args.get("id"))
        except (TypeError, ValueError):
            return json.dumps({"error": "forget_memory needs the note's id (a number)."}), True
        if not delete(account_id, nid):
            return json.dumps({"error": "No note #%s." % args.get("id")}), True
        return json.dumps({"removed": nid}), False
    return json.dumps({"error": "Unknown tool '%s'." % name}), True


# ---- the Memory page's endpoints, mounted by the Writer Desk (/write/memory)
# and the Investment Assistant (/memory) on their own routers; `account_of`
# turns a request into the signed-in person's account id.

def mount(router, account_of):
    from fastapi import HTTPException, Request
    from pydantic import BaseModel

    class NoteBody(BaseModel):
        text: str = ""

    class SwitchBody(BaseModel):
        enabled: bool = True

    @router.get("/memory")
    def memory_view(request: Request):
        return view(account_of(request))

    @router.post("/memory")
    def memory_add(body: NoteBody, request: Request):
        aid = account_of(request)
        try:
            add(aid, body.text, "you", YOU)
        except NoteError as exc:
            raise HTTPException(400, str(exc))
        return view(aid)

    @router.put("/memory/settings")
    def memory_switch(body: SwitchBody, request: Request):
        aid = account_of(request)
        set_enabled(aid, body.enabled)
        return view(aid)

    @router.patch("/memory/{note_id}")
    def memory_edit(note_id: int, body: NoteBody, request: Request):
        aid = account_of(request)
        try:
            if edit(aid, note_id, body.text) is None:
                raise HTTPException(404, "That note is no longer in memory.")
        except NoteError as exc:
            raise HTTPException(400, str(exc))
        return view(aid)

    @router.delete("/memory/{note_id}")
    def memory_delete(note_id: int, request: Request):
        aid = account_of(request)
        if not delete(aid, note_id):
            raise HTTPException(404, "That note is already gone from memory.")
        return view(aid)

    @router.delete("/memory")
    def memory_clear(request: Request):
        aid = account_of(request)
        clear(aid)
        return view(aid)
