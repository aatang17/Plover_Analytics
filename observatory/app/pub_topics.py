# -*- coding: utf-8 -*-
"""A publication's topics: the tick boxes under Topics in the editor, and the
words on its article pages and homepage cards.

A publication starts with the twelve in research_doc.TOPICS. Anyone who
writes for it adds one from the editor (type a name, Add); its editors and
owners remove one there too. A removed topic stays known by name, so a
published article that carries it still shows it; it only leaves the tick
boxes. Adding the same name again brings it back.

Stored as a JSON array in staff.db's team_settings ("pub.topics@<id>"),
[{key, label, removed?}], beside the rest of the publication's profile
(app/pub_profile.py). Nothing is stored until the first change, so a
publication nobody has changed follows the default list. Python 3.9.
"""
import json
import re

from . import research_doc as rd
from . import staff

LABEL_MAX = 30
MAX_TOPICS = 40
_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TopicError(ValueError):
    """A change refused, worded for the writer."""


def _k(publication_id):
    return "pub.topics@%d" % int(publication_id)


def _rows(publication_id):
    raw = staff.team_setting(_k(publication_id))
    if not raw:
        return [{"key": k, "label": label} for k, label in rd.TOPICS]
    try:
        rows = json.loads(raw)
    except ValueError:
        rows = []
    return [r for r in rows if isinstance(r, dict) and _KEY.match(str(r.get("key") or ""))
            and r.get("label")]


def _save(publication_id, rows):
    staff.set_team_setting(_k(publication_id), json.dumps(rows, ensure_ascii=False))


def topics(publication_id):
    """[{key, label}] the editor offers, in order."""
    return [{"key": r["key"], "label": r["label"]} for r in _rows(publication_id)
            if not r.get("removed")]


def labels(publication_id):
    """{key: label} for every topic the publication has ever had."""
    out = dict(rd.TOPICS)
    out.update((r["key"], r["label"]) for r in _rows(publication_id))
    return out


def keep(publication_id, keys):
    """The keys of an article's topics that the publication knows, in order."""
    if not keys:
        return []
    known = labels(publication_id)
    return [k for k in keys if k in known][:rd.TOPICS_MAX]


def _clean_label(label):
    label = re.sub(r"\s+", " ", label or "").strip()
    if not label:
        raise TopicError("Type a name for the topic.")
    if len(label) > LABEL_MAX:
        raise TopicError("Keep a topic under %d characters." % LABEL_MAX)
    key = re.sub(r"[^a-z0-9]+", "-", label.lower())[:40].strip("-")
    if not key:
        raise TopicError("Use letters or numbers in the topic's name.")
    return label, key


def add(publication_id, label):
    """Returns (topics, key of the one added)."""
    label, key = _clean_label(label)
    rows = _rows(publication_id)
    for r in rows:
        if r["key"] == key:
            if r.get("removed"):
                r.pop("removed", None)
                r["label"] = label
                _save(publication_id, rows)
            return topics(publication_id), key
    if len([r for r in rows if not r.get("removed")]) >= MAX_TOPICS:
        raise TopicError("A publication can have up to %d topics. Remove one first." % MAX_TOPICS)
    rows.append({"key": key, "label": label})
    _save(publication_id, rows)
    return topics(publication_id), key


def remove(publication_id, key):
    rows = _rows(publication_id)
    for r in rows:
        if r["key"] == key:
            r["removed"] = True
    _save(publication_id, rows)
    return topics(publication_id)
