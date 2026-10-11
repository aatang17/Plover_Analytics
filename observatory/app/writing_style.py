# -*- coding: utf-8 -*-
"""A writer's own writing style, which every AI-tab run they start follows.

A writer describes how they write in a few lines ("Short sentences. Lead
with the number.") and adds up to five samples of their writing: articles
they published on Plover, or pieces pasted in from elsewhere (their own
Substack, say). People rarely describe their voice well, so the samples do
most of the work.

The style is the writer's, not a publication's: it is kept with their AI
settings (staff_ai.style) and follows them into every publication they write
for. Nobody else sees it. The publication's house style still decides format
(numbers, units, headings, anything it states); the writer's style decides
voice (tone, sentence length, structure). Where they disagree on something
the house style states, the house style wins, so a publication stays
consistent while each writer sounds like themselves.

Samples go into the run's instructions as excerpts, a bounded amount of
text, marked as showing how the writer writes and never as a source of facts
or figures.
"""
import json

from . import article_search, staff

TEXT_MAX = 2000
SAMPLES_MAX = 5
TITLE_MAX = 120
PASTE_MIN = 200
PASTE_MAX = 12000
PROMPT_TOTAL = 8000        # characters of samples a run is sent, shared between them
PROMPT_EACH = 2500


class StyleError(ValueError):
    pass


def _stored(person):
    raw = staff.ai_settings(person["id"]).get("style")
    try:
        s = json.loads(raw) if raw else {}
    except ValueError:
        s = {}
    return {"text": s.get("text") or "", "samples": s.get("samples") or []}


def _choice(r):
    return {"article_id": r["id"], "title": r["title"], "published": r["first_published_at"][:10],
            "url": article_search._url(r)}


def view(person):
    """The writer's style as My Account shows it, with the articles they may
    pick as samples."""
    s = _stored(person)
    samples = []
    for x in s["samples"]:
        if x.get("article_id"):
            r = article_search.published_article(x["article_id"])
            samples.append(dict(_choice(r), kind="article", live=True) if r else
                           {"kind": "article", "article_id": x["article_id"],
                            "title": x.get("title") or "", "live": False})
        else:
            samples.append({"kind": "pasted", "title": x.get("title") or "",
                            "text": x.get("text") or "", "words": len((x.get("text") or "").split())})
    return {"text": s["text"], "samples": samples,
            "choices": [_choice(r) for r in article_search.by_author(person["id"])],
            "limits": {"text": TEXT_MAX, "samples": SAMPLES_MAX, "paste_min": PASTE_MIN,
                       "paste_max": PASTE_MAX, "title": TITLE_MAX}}


def save(person, text, samples):
    """Replace the writer's style. Every sample is checked: an article must be
    a live one the writer wrote; a pasted piece must be long enough to show
    a voice and short enough to send with a run."""
    text = (text or "").strip()
    if len(text) > TEXT_MAX:
        raise StyleError("Keep the description under %d characters; the samples show the rest."
                         % TEXT_MAX)
    samples = samples or []
    if not isinstance(samples, list):
        raise StyleError("Samples must be a list.")
    if len(samples) > SAMPLES_MAX:
        raise StyleError("Use at most %d samples." % SAMPLES_MAX)
    mine = dict((r["id"], r) for r in article_search.by_author(person["id"]))
    keep, seen = [], set()
    for x in samples:
        x = x if isinstance(x, dict) else {}
        if x.get("article_id") is not None:
            try:
                aid = int(x["article_id"])
            except (TypeError, ValueError):
                raise StyleError("That sample article was not found.")
            if aid in seen:
                continue
            r = mine.get(aid)
            if r is None:
                old = [s for s in _stored(person)["samples"] if s.get("article_id") == aid]
                if not old:
                    raise StyleError("Only articles you wrote and published can be samples.")
                keep.append(old[0])         # withdrawn since it was picked: kept, skipped in runs
            else:
                keep.append({"article_id": aid, "title": r["title"]})
            seen.add(aid)
            continue
        body = (x.get("text") or "").strip()
        title = " ".join((x.get("title") or "").split())[:TITLE_MAX] or "Pasted piece"
        if len(body) < PASTE_MIN:
            raise StyleError("Paste at least a few paragraphs (%d characters or more) so the "
                             "assistant can hear your voice." % PASTE_MIN)
        if len(body) > PASTE_MAX:
            raise StyleError("\"%s\" is too long: paste up to %d characters, a few paragraphs "
                             "are enough." % (title, PASTE_MAX))
        keep.append({"title": title, "text": body})
    value = json.dumps({"text": text, "samples": keep}, ensure_ascii=False) if (text or keep) else None
    staff.set_ai(person["id"], style=value)
    return view(person)


def summary(person):
    """For the AI tab: whether the writer has a style, and how many samples."""
    s = _stored(person)
    return {"on": bool(s["text"] or s["samples"]), "samples": len(s["samples"])}


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    cut = text.rfind("\n\n", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + "\n[…]"


def prompt(person):
    """The part of a run's instructions that carries the writer's style, or ""."""
    s = _stored(person)
    pieces = []
    for x in s["samples"]:
        if x.get("article_id"):
            r = article_search.published_article(x["article_id"])
            if r is None:
                continue
            md = r["markdown"] or ""
            if md.startswith("# "):
                md = md.split("\n", 1)[1] if "\n" in md else ""
            pieces.append(("\"%s\" (published %s)" % (r["title"], r["first_published_at"][:10]),
                           md.strip()))
        elif x.get("text"):
            pieces.append(("\"%s\" (pasted by the writer)" % x.get("title"), x["text"]))
    if not s["text"] and not pieces:
        return ""
    out = ("The writer's own style. Write everything that goes in the draft in their voice: "
           "tone, sentence length, structure and word choice. The house style above still decides "
           "numbers, units, headings and anything else it states; where the two disagree, follow "
           "the house style.\n")
    if s["text"]:
        out += "In the writer's words:\n" + s["text"] + "\n"
    if pieces:
        each = min(PROMPT_EACH, PROMPT_TOTAL // len(pieces))
        out += ("Samples of the writer's writing. They show how the writer writes, not what is "
                "true now: never copy their sentences, facts or figures into the draft.\n")
        for head, body in pieces:
            out += "--- %s\n%s\n" % (head, _excerpt(body, each))
        out += "--- end of samples\n"
    return out + "\n"
