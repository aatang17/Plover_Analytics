# -*- coding: utf-8 -*-
"""Past articles for the writing assistant: search and read what is published.

Only published work is reachable, at its latest version, never a draft: a
published version is public and never changes, so a run that links to an
earlier piece, or keeps to what it said, points at something every reader
can see. Withdrawn articles are left out.

Three scopes: the run's own publication (the default), the writer's own
articles wherever they appeared ("mine", by the draft's author list), and
every publication on Plover ("all").

The search is plain word matching over title, standfirst, summary and text,
best match first: there are tens of articles, not millions, and a writer
looking for "the piece on bank margins" types the words it used. A run of
Japanese characters counts as one word and matches as a substring.
"""
import json
import re

from . import research, seo

SCOPES = ("publication", "mine", "all")
LIMIT_DEFAULT = 8
LIMIT_MAX = 20
SNIPPET = 240
TEXT_MAX = 14000
STOP = set("a an and are as at be by for from has have in is it its of on or that the this "
           "to was were what when which who will with".split())


def _rows(publication_id=None, article_id=None):
    """Live articles at their latest version, newest first by first publication."""
    where, args = ["a.status = 'published'"], []
    if publication_id is not None:
        where.append("a.publication_id = ?")
        args.append(publication_id)
    if article_id is not None:
        where.append("a.id = ?")
        args.append(article_id)
    rows = research.conn().execute(
        "SELECT a.id, a.publication_id, v.slug, v.version, v.title, v.dek, v.summary, v.authors, "
        "v.doc, v.markdown, v.published_at, "
        "(SELECT published_at FROM article_versions f WHERE f.article_id = a.id AND f.version = 1) "
        "AS first_published_at "
        "FROM articles a JOIN article_versions v ON v.article_id = a.id "
        "AND v.version = a.published_version WHERE " + " AND ".join(where) + " "
        "ORDER BY first_published_at DESC, a.id DESC", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = json.loads(d["authors"])
        d["author_ids"] = json.loads(d.pop("doc")).get("authors") or []
        out.append(d)
    return out


def by_author(person_id):
    """A writer's own published articles, every publication's, newest first."""
    return [r for r in _rows() if person_id in r["author_ids"]]


def published_article(article_id):
    """One live article at its latest version, or None."""
    try:
        aid = int(article_id)
    except (TypeError, ValueError):
        return None
    rows = _rows(article_id=aid)
    return rows[0] if rows else None


def _words(query):
    words = []
    for w in re.findall(r"\w+", (query or "").lower()):
        if w in STOP or (w.isascii() and len(w) < 2 and not w.isdigit()):
            continue
        if w not in words:
            words.append(w)
    return words[:12]


def _plain(markdown):
    """The text of an article's Markdown without its markup, for snippets."""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown or "")
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[#>*_`|]+", " ", t)
    return " ".join(t.split())


def _score(words, r):
    title, lead = r["title"].lower(), (r["dek"] + " " + r["summary"]).lower()
    body = r["_text"].lower()
    hits = score = 0
    for w in words:
        t, l, b = title.count(w), lead.count(w), body.count(w)
        if t or l or b:
            hits += 1
        score += 6 * min(t, 2) + 3 * min(l, 3) + min(b, 10)
    return hits, score


def _snippet(words, text):
    low = text.lower()
    at = min([low.find(w) for w in words if low.find(w) >= 0] or [0])
    start = max(0, at - SNIPPET // 3)
    s = text[start:start + SNIPPET].strip()
    return ("…" if start else "") + s + ("…" if start + SNIPPET < len(text) else "")


def _url(r):
    return seo.SITE_BASE_URL + research.address(r["publication_id"], r["slug"])


def _day(iso):
    return (iso or "")[:10]


def _card(r, words):
    pub = research.publication(r["publication_id"])
    out = {"id": r["id"], "title": r["title"], "dek": r["dek"],
           "publication": pub["name"] if pub else "",
           "authors": r["authors"], "published": _day(r["first_published_at"]),
           "url": _url(r), "snippet": _snippet(words, r["_text"])}
    if r["version"] > 1:
        out["updated"] = _day(r["published_at"])
        out["version"] = r["version"]
    return out


def search(person, publication_id, query="", scope="publication", limit=LIMIT_DEFAULT):
    scope = scope if scope in SCOPES else "publication"
    try:
        limit = max(1, min(LIMIT_MAX, int(limit or LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        limit = LIMIT_DEFAULT
    if scope == "publication" and publication_id is None:
        scope = "mine"
    rows = _rows(publication_id if scope == "publication" else None)
    if scope == "mine":
        rows = [r for r in rows if person.get("id") in r["author_ids"]]
    for r in rows:
        r["_text"] = _plain(r["markdown"])
    words = _words(query)
    if words:
        ranked = []
        for i, r in enumerate(rows):
            hits, score = _score(words, r)
            if hits:
                ranked.append((-hits, -score, i, r))
        ranked.sort(key=lambda x: x[:3])
        rows = [x[3] for x in ranked]
    out = {"scope": scope, "query": query or "", "found": len(rows),
           "articles": [_card(r, words) for r in rows[:limit]]}
    if not rows:
        out["note"] = ("Nothing published matches." if words else "Nothing is published here yet.") + (
            " Try scope 'all' for every publication on Plover." if scope != "all" else "")
    return out


def read(article_id):
    r = published_article(article_id)
    if r is None:
        return None
    pub = research.publication(r["publication_id"])
    md = r["markdown"] or ""
    out = {"id": r["id"], "title": r["title"], "dek": r["dek"], "summary": r["summary"],
           "publication": pub["name"] if pub else "", "authors": r["authors"],
           "published": _day(r["first_published_at"]), "version": r["version"],
           "url": _url(r), "markdown": md[:TEXT_MAX]}
    if r["version"] > 1:
        out["updated"] = _day(r["published_at"])
    if len(md) > TEXT_MAX:
        out["truncated"] = True
    return out


TOOLS = [
    {"name": "search_past_articles",
     "description": "Search articles already published on Plover, at their latest version: this "
                    "publication's (scope 'publication', the default), only the writer's own "
                    "wherever they appeared ('mine'), or every publication's ('all'). Returns "
                    "id, title, date, address and a snippet, best match first; with no query, "
                    "the newest. Use it to link to an earlier piece, keep to what was said "
                    "before, or avoid repeating it.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Words the article would use. Empty for the newest."},
         "scope": {"type": "string", "enum": list(SCOPES)},
         "limit": {"type": "integer", "description": "At most %d." % LIMIT_MAX}},
         "required": []}},
    {"name": "read_past_article",
     "description": "Read one published article (its latest version) as Markdown, by the id "
                    "search_past_articles gave. Cite it by its address.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}},
                    "required": ["id"]}},
]
TOOL_NAMES = set(t["name"] for t in TOOLS)


def run_tool(person, publication_id, name, args):
    """(text, is_error) for one call."""
    args = args if isinstance(args, dict) else {}
    if name == "search_past_articles":
        return json.dumps(search(person, publication_id, str(args.get("query") or ""),
                                 str(args.get("scope") or "publication"), args.get("limit")),
                          ensure_ascii=False), False
    out = read(args.get("id"))
    if out is None:
        return json.dumps({"error": "No published article has the id %s. Find one with "
                                    "search_past_articles." % args.get("id")}), True
    return json.dumps(out, ensure_ascii=False), False
