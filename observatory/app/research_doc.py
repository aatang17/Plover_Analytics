# -*- coding: utf-8 -*-
"""The research article format: what a draft may contain, how it is cleaned,
and how it is rendered.

An article is a list of blocks, not a blob of HTML. The editor in the admin
console produces it, this module is the only thing that turns it into HTML,
and the same function renders the editor's preview and the published page —
so what a writer previews is byte for byte what a reader gets.

Blocks
------
``p``        a paragraph of inline text
``heading``  level 2 or 3, plain text
``list``     bullet or number, one inline text per item
``quote``    a block quotation, inline text
``table``    rows of inline cells, an optional header row, per-column
             alignment, a caption and a source line
``chart``    a live Plover chart: dataset, up to six series, one measure,
             an optional date range and a title. At publication the data is
             read as it stood that day and frozen into the article.
``snapshot`` a chart copied from a Plover page (any page that draws with
             charts.js, company pages included): the page address, the chart's
             kind and its full config with the data, captured in the desk on a
             given day. Until the desk has captured it (an agent can only name
             the page) it is "pending" and the article cannot be published.
``datachart`` a chart of numbers the writer pasted in (from a spreadsheet, a
             CSV, another publisher's download): the cells as pasted, a line
             or bars, a unit, and a source line the writer must give. The
             numbers live in the article itself, so they never change; the
             page says they are as entered by the author.
``image``    an uploaded PNG/JPEG/WebP with alt text, caption, a source and,
             for an image copied from a Plover page, that page's address
``divider``  a rule
``placeholder`` a reminder left by the plan: the point a section must make
             (role "text") or a chart it promised (role "chart"). Never
             rendered; an article cannot be published while one is left.

Beside the blocks, a draft carries two things readers never see: the
approved **plan** (its blocks, moved out of the page when the writer starts
writing) and the writer's research **notes** — numbers, quotations, charts
and thoughts collected while researching, each with its source. Both are
stripped from every published version (``public_draft``).

Inline text
-----------
The only markup kept is ``strong``, ``em``, ``a`` (web, mail and site-relative
links only), ``code``, ``sup``, ``sub``, ``br`` and the footnote marker
``<sup data-note="...">``. Everything else a writer pastes — a Word style, a
Google Docs span, a script — is reduced to its text. Nothing a writer types
can run in a reader's browser: the cleaner is an allowlist, never a blocklist.
"""
import datetime
import html
import json
import re
import uuid
from html.parser import HTMLParser
from urllib.parse import quote, unquote

INLINE_MAX = 20000
TEXT_MAX = 400
TITLE_MAX = 200
SUMMARY_MAX = 700
NOTE_MAX = 1500
BLOCKS_MAX = 600
TABLE_ROWS_MAX = 80
TABLE_COLS_MAX = 14
CHART_SERIES_MAX = 6
DATA_ROWS_MAX = 3000
DATA_COLS_MAX = CHART_SERIES_MAX + 1   # the date or label column, then the series
DATA_CELL_MAX = 80
DATA_KINDS = ("line", "bar")
SLUG_MAX = 80

MEASURES = ("index", "yoy", "mom", "ann3m")
ALIGNS = ("auto", "left", "right", "center")
IMAGE_EXTS = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PERIOD = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_CODE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_DATASET = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
_SAFE_HREF = re.compile(r"^(https?://[^\s<>\"]+|mailto:[^\s<>\"]+|/(?!/)[^\s<>\"]*|#[A-Za-z0-9_\-]*)$",
                        re.IGNORECASE)
# A relative link to one of our own pages: "cpi.html?series=0001". No scheme
# (no colon before the first slash), so it cannot be javascript: or data:.
_RELATIVE_HREF = re.compile(r"^[A-Za-z0-9_\-]+\.html(?:[?#][^\s<>\"]*)?$")

# Where an article sits on the PloverResearch homepage. Fixed lists, so a
# filter never has two spellings of one topic.
MARKETS = [("jp", "Japan"), ("us", "United States"), ("global", "Global")]
MARKET_KEYS = [k for k, _ in MARKETS]
TOPICS = [("inflation", "Inflation"), ("monetary-policy", "Monetary Policy"), ("rates", "Rates"),
          ("banks", "Banks"), ("equities", "Equities"), ("companies", "Companies"),
          ("governance", "Governance"), ("growth", "Growth"), ("labour", "Labour"),
          ("trade", "Trade"), ("public-finance", "Public Finance"), ("tourism", "Tourism")]
TOPIC_KEYS = [k for k, _ in TOPICS]
TOPICS_MAX = 3
FIGURE_TYPES = ("chart", "snapshot", "datachart", "image")

TYPES = ("p", "heading", "list", "quote", "table", "chart", "snapshot", "datachart", "image",
         "divider", "placeholder")
PLACEHOLDER_ROLES = ("text", "chart")
PLAN_TYPES = ("p", "heading", "list", "quote", "table", "divider")
PLAN_BLOCKS_MAX = 200
NOTES_MAX = 300
NOTE_KINDS = ("number", "quote", "chart", "page", "mine")
SNAPSHOT_KINDS = ("line", "stack", "cols", "dist", "rank", "bar")
SNAPSHOT_MAX_BYTES = 1500000
# A Plover page a chart can be copied from: one of our .html pages with its
# view state, never the console or the desk themselves.
_PAGE_URL = re.compile(r"^/(?!admin|desk)[A-Za-z0-9_\-]+\.html(\?[^\s<>\"#]*)?$")
SITE_HOSTS = ("ploveranalytics.com", "www.ploveranalytics.com")


class DocError(ValueError):
    """A draft that cannot be stored, worded for the writer."""


def page_path(url):
    """A Plover page address as a site-relative path and query, or "" when it
    is not one: "https://ploveranalytics.com/financials.html?c=7203" and
    "/financials.html?c=7203" both give the latter."""
    url = (url or "").strip()
    if not url:
        return ""
    m = re.match(r"^https?://([^/]+)(/.*)?$", url, re.IGNORECASE)
    if m:
        host = m.group(1).lower().split(":")[0]
        if host not in SITE_HOSTS and host not in ("localhost", "127.0.0.1"):
            return ""
        url = m.group(2) or "/"
    url = url.split("#", 1)[0]
    if url in ("", "/"):
        url = "/index.html"
    return url if _PAGE_URL.match(url) else ""


def esc(text):
    return html.escape("" if text is None else str(text), quote=True)


def safe_href(href):
    href = (href or "").strip()
    if _SAFE_HREF.match(href) or _RELATIVE_HREF.match(href):
        return href
    return None


# ---------------------------------------------------------------------------
# inline cleaning

_INLINE_TAGS = ("strong", "em", "a", "code", "sup", "sub")
_RENAME = {"b": "strong", "i": "em"}
_DROP_WITH_CONTENT = ("script", "style", "template", "noscript", "iframe", "object",
                      "svg", "math", "head", "title")
# A paste from Google Docs or Word arrives as <span style="font-weight:700">.
_BOLD_STYLE = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)", re.IGNORECASE)
_ITALIC_STYLE = re.compile(r"font-style\s*:\s*italic", re.IGNORECASE)


class _Inline(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.out = []
        self.stack = []       # what each open source tag emitted: a list of tags
        self.drop = 0         # inside a dropped element
        self.in_note = 0      # inside a footnote marker: its text is the note's

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            self.drop += 1
            self.stack.append((tag, []))
            return
        if self.drop or self.in_note:
            self.stack.append((tag, []))
            return
        a = dict((k.lower(), v or "") for k, v in attrs)
        if tag == "br":
            self.out.append("<br>")
            return
        tag = _RENAME.get(tag, tag)
        emitted = []
        if tag == "a":
            href = safe_href(a.get("href"))
            if href:
                self.out.append('<a href="%s">' % esc(href))
                emitted.append("a")
        elif tag == "sup" and "data-note" in a:
            note = re.sub(r"\s+", " ", a.get("data-note") or "").strip()[:NOTE_MAX]
            if note:
                self.out.append('<sup data-note="%s"></sup>' % esc(note))
            self.in_note += 1
            self.stack.append(("sup-note", []))
            return
        elif tag in _INLINE_TAGS:
            self.out.append("<%s>" % tag)
            emitted.append(tag)
        elif tag == "span" or tag == "font":
            style = a.get("style", "")
            # Google Docs wraps a whole paste in <b style="font-weight:normal">;
            # the style wins over the tag.
            if _BOLD_STYLE.search(style):
                self.out.append("<strong>")
                emitted.append("strong")
            if _ITALIC_STYLE.search(style):
                self.out.append("<em>")
                emitted.append("em")
        if tag == "strong" and re.search(r"font-weight\s*:\s*(normal|[1-4]00)",
                                         a.get("style", ""), re.IGNORECASE):
            self.out.pop()
            emitted = []
        self.stack.append((tag, emitted))

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self.drop and not self.in_note:
            self.out.append("<br>")
        elif tag.lower() == "sup":
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        tag = _RENAME.get(tag, tag)
        # close back to the matching open tag; anything left open inside it is
        # closed too, so the output is always properly nested
        for i in range(len(self.stack) - 1, -1, -1):
            name = self.stack[i][0]
            if name == tag or (tag == "sup" and name == "sup-note"):
                while len(self.stack) > i:
                    self._close(self.stack.pop())
                return

    def _close(self, entry):
        name, emitted = entry
        if name in _DROP_WITH_CONTENT:
            self.drop -= 1
            return
        if name == "sup-note":
            self.in_note -= 1
            return
        for t in reversed(emitted):
            self.out.append("</%s>" % t)

    def handle_data(self, data):
        if self.drop or self.in_note:
            return
        self.out.append(esc(data).replace("&#x27;", "'"))

    def result(self):
        while self.stack:
            self._close(self.stack.pop())
        text = "".join(self.out)
        # empty wrappers left by an editor (<strong></strong>) carry nothing
        for _ in range(3):
            text = re.sub(r"<(strong|em|code|sub|sup)></\1>", "", text)
            text = re.sub(r'<a href="[^"]*"></a>', "", text)
        return text


def clean_inline(raw):
    """Inline HTML reduced to the allowlist. Idempotent."""
    if raw is None:
        return ""
    raw = str(raw)
    if len(raw) > INLINE_MAX * 4:
        raise DocError("A paragraph is too long to save (over %d characters)." % INLINE_MAX)
    p = _Inline()
    p.feed(raw)
    p.close()
    text = p.result()
    text = text.replace("\r", "")
    text = re.sub(r"(<br>)+$", "", text.strip())
    if len(text) > INLINE_MAX:
        raise DocError("A paragraph is too long to save (over %d characters)." % INLINE_MAX)
    return text


def inline_text(fragment):
    """The visible text of a cleaned inline fragment."""
    text = re.sub(r"<sup data-note=\"[^\"]*\"></sup>", "", fragment or "")
    text = re.sub(r"<br>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def plain(value, limit=TEXT_MAX):
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def slugify(title):
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:SLUG_MAX].rstrip("-")
    return text or "untitled"


# ---------------------------------------------------------------------------
# the draft

def new_draft():
    return {"title": "", "dek": "", "summary": "", "slug": "", "authors": [],
            "blocks": [{"id": _bid(), "type": "p", "html": ""}]}


def _bid():
    return uuid.uuid4().hex[:10]


def _block(b):
    if not isinstance(b, dict):
        raise DocError("A block is not in the expected form.")
    t = b.get("type")
    if t not in TYPES:
        raise DocError("Unknown block type '%s'." % plain(t, 30))
    bid = b.get("id") if isinstance(b.get("id"), str) and re.match(r"^[A-Za-z0-9_-]{1,40}$",
                                                                   b.get("id")) else _bid()
    out = {"id": bid, "type": t}
    if t in ("p", "quote"):
        out["html"] = clean_inline(b.get("html"))
    elif t == "heading":
        out["level"] = 3 if b.get("level") == 3 else 2
        out["text"] = plain(b.get("text"), TITLE_MAX)
    elif t == "list":
        out["style"] = "number" if b.get("style") == "number" else "bullet"
        items = b.get("items") if isinstance(b.get("items"), list) else []
        out["items"] = [clean_inline(i) for i in items[:500]] or [""]
    elif t == "table":
        rows = b.get("rows") if isinstance(b.get("rows"), list) else []
        if len(rows) > TABLE_ROWS_MAX:
            raise DocError("A table has more than %d rows." % TABLE_ROWS_MAX)
        width = max([len(r) for r in rows if isinstance(r, list)] or [0])
        if width > TABLE_COLS_MAX:
            raise DocError("A table has more than %d columns." % TABLE_COLS_MAX)
        width = max(width, 1)
        clean_rows = []
        for r in rows:
            r = r if isinstance(r, list) else []
            cells = [clean_inline(c) for c in r] + [""] * (width - len(r))
            clean_rows.append(cells)
        out["rows"] = clean_rows or [[""]]
        align = b.get("align") if isinstance(b.get("align"), list) else []
        out["align"] = [(a if a in ALIGNS else "auto") for a in align[:width]] + \
            ["auto"] * (width - len(align[:width]))
        out["header"] = bool(b.get("header", True))
        out["caption"] = plain(b.get("caption"), TITLE_MAX)
        out["source"] = plain(b.get("source"), TEXT_MAX)
    elif t == "chart":
        ds = plain(b.get("dataset"), 64)
        out["dataset"] = ds if _DATASET.match(ds or "") else ""
        series = b.get("series") if isinstance(b.get("series"), list) else []
        out["series"] = [s for s in (plain(x, 64) for x in series) if _CODE.match(s)][
            :CHART_SERIES_MAX]
        out["measure"] = b.get("measure") if b.get("measure") in MEASURES else "index"
        for k in ("start", "end"):
            v = plain(b.get(k), 10)
            out[k] = v if _PERIOD.match(v) else ""
        out["title"] = plain(b.get("title"), TITLE_MAX)
        out["note"] = plain(b.get("note"), TEXT_MAX)
    elif t == "datachart":
        rows = b.get("rows") if isinstance(b.get("rows"), list) else []
        rows = [r for r in rows if isinstance(r, list)]
        if len(rows) > DATA_ROWS_MAX:
            raise DocError("A chart from your own data can have up to %d rows." % DATA_ROWS_MAX)
        if any(len(r) > DATA_COLS_MAX for r in rows):
            raise DocError("A chart from your own data can show up to %d series: a date or "
                           "label column, then at most %d columns of numbers."
                           % (CHART_SERIES_MAX, CHART_SERIES_MAX))
        out["rows"] = [[plain(c, DATA_CELL_MAX) for c in r] for r in rows]
        out["kind"] = b.get("kind") if b.get("kind") in DATA_KINDS else "line"
        out["unit"] = plain(b.get("unit"), 40)
        out["title"] = plain(b.get("title"), TITLE_MAX)
        out["note"] = plain(b.get("note"), TEXT_MAX)
        out["source"] = plain(b.get("source"), TEXT_MAX)
        out["origin"] = "database" if b.get("origin") == "database" else "author"
    elif t == "image":
        sha = plain(b.get("media"), 64).lower()
        out["media"] = sha if _SHA.match(sha) else ""
        out["ext"] = b.get("ext") if b.get("ext") in IMAGE_EXTS else "png"
        out["alt"] = plain(b.get("alt"), TEXT_MAX)
        out["caption"] = plain(b.get("caption"), TITLE_MAX)
        out["source"] = plain(b.get("source"), TEXT_MAX)
        for k in ("width", "height"):
            v = b.get(k)
            out[k] = int(v) if isinstance(v, (int, float)) and 0 < v < 100000 else None
        out["pending"] = plain(b.get("pending"), 200)
        out["link"] = page_path(b.get("link"))
    elif t == "placeholder":
        out["role"] = b.get("role") if b.get("role") in PLACEHOLDER_ROLES else "text"
        out["text"] = plain(b.get("text"), NOTE_MAX)
    elif t == "snapshot":
        out["url"] = page_path(b.get("url"))
        n = b.get("chart")
        out["chart"] = int(n) if isinstance(n, int) and not isinstance(n, bool) and 0 < n < 100 else 0
        out["title"] = plain(b.get("title"), TITLE_MAX)
        out["note"] = plain(b.get("note"), TEXT_MAX)
        cfg = b.get("cfg")
        kind = b.get("kind")
        if isinstance(cfg, dict) and kind in SNAPSHOT_KINDS:
            size = len(json.dumps(cfg, ensure_ascii=False))
            if size > SNAPSHOT_MAX_BYTES:
                raise DocError("A captured chart is too large to store (%d KB). Shorten the "
                               "page's date range and capture it again." % (size // 1024))
            out["kind"] = kind
            out["cfg"] = cfg
            out["page_title"] = plain(b.get("page_title"), TITLE_MAX)
            out["source"] = plain(b.get("source"), 1000)
            out["calc"] = plain(b.get("calc"), 2000)
            cap = plain(b.get("captured_at"), 30)
            out["captured_at"] = cap if re.match(r"^\d{4}-\d{2}-\d{2}", cap) else ""
        else:
            out["kind"] = ""
            out["cfg"] = None
            out["page_title"] = ""
            out["source"] = ""
            out["calc"] = ""
            out["captured_at"] = ""
    return out


def clean_draft(draft):
    """A draft as it will be stored. Lenient — a half-written article saves —
    but structurally exact: unknown keys are dropped and every string is
    cleaned or capped."""
    if not isinstance(draft, dict):
        raise DocError("The draft is not in the expected form.")
    blocks = draft.get("blocks")
    if not isinstance(blocks, list):
        raise DocError("The draft has no blocks.")
    if len(blocks) > BLOCKS_MAX:
        raise DocError("An article can have at most %d blocks." % BLOCKS_MAX)
    authors = draft.get("authors") if isinstance(draft.get("authors"), list) else []
    out = {
        "title": plain(draft.get("title"), TITLE_MAX),
        "dek": plain(draft.get("dek"), TEXT_MAX),
        "summary": plain(draft.get("summary"), SUMMARY_MAX),
        "slug": plain(draft.get("slug"), SLUG_MAX).lower(),
        "authors": [int(a) for a in authors if isinstance(a, int) and not isinstance(a, bool)][:8],
        "blocks": [_block(b) for b in blocks] or [{"id": _bid(), "type": "p", "html": ""}],
    }
    if out["slug"] and not _SLUG.match(out["slug"]):
        out["slug"] = slugify(out["slug"])
    # homepage placement: market, topics, the chart that stands for the article,
    # and whether that chart is offered as Chart of the Week
    out["market"] = draft.get("market") if draft.get("market") in MARKET_KEYS else ""
    topics = draft.get("topics") if isinstance(draft.get("topics"), list) else []
    out["topics"] = [k for k in TOPIC_KEYS if k in topics][:TOPICS_MAX]
    figure_ids = [b["id"] for b in out["blocks"] if b["type"] in FIGURE_TYPES]
    out["lead"] = draft.get("lead") if draft.get("lead") in figure_ids else ""
    out["feature"] = bool(draft.get("feature")) and bool(figure_ids)
    plan = _clean_plan(draft.get("plan"))
    if plan:
        out["plan"] = plan
    out["notes"] = _clean_notes(draft.get("notes"))
    return out


def _clean_plan(plan):
    """The approved plan, kept beside the draft: its text blocks and who
    approved it when. None when there is no plan."""
    if not isinstance(plan, dict) or not isinstance(plan.get("blocks"), list):
        return None
    blocks = [_block(b) for b in plan["blocks"][:PLAN_BLOCKS_MAX]
              if isinstance(b, dict) and b.get("type") in PLAN_TYPES]
    if not blocks:
        return None
    return {"blocks": blocks, "approved_at": plain(plan.get("approved_at"), 30),
            "approved_by": plain(plan.get("approved_by"), 120)}


_BLOCK_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _clean_notes(notes):
    """The writer's research notes: what each one is, what Insert puts in the
    draft (inline text with its footnote, or a chart block), its source, the
    section it was taken for, and whether it is in the draft yet."""
    out = []
    for n in (notes if isinstance(notes, list) else [])[:NOTES_MAX]:
        if not isinstance(n, dict):
            continue
        kind = n.get("kind") if n.get("kind") in NOTE_KINDS else "mine"
        note = {"id": n["id"] if isinstance(n.get("id"), str) and _BLOCK_ID.match(n["id"]) else _bid(),
                "kind": kind, "text": plain(n.get("text"), NOTE_MAX),
                "html": clean_inline(n.get("html")) if n.get("html") else "",
                "source": plain(n.get("source"), NOTE_MAX),
                "url": safe_href(n.get("url")) or "",
                "section": n["section"] if isinstance(n.get("section"), str) and
                _BLOCK_ID.match(n["section"]) else "",
                "at": plain(n.get("at"), 30), "used": bool(n.get("used"))}
        blk = n.get("block")
        if isinstance(blk, dict) and blk.get("type") in ("chart", "snapshot", "datachart"):
            note["block"] = _block(blk)
        if note["text"] or note["html"] or note.get("block"):
            out.append(note)
    return out


def public_draft(draft):
    """A draft as it may be published or shown outside the desk: without the
    plan and the research notes, which can hold a paid article's text."""
    return dict((k, v) for k, v in draft.items() if k not in ("plan", "notes"))


PLACEHOLDER_PROBLEM = "From the plan, "


def publish_problems(draft, author_names):
    """What stops a draft from being published, one plain sentence each."""
    problems = []
    if not draft["title"]:
        problems.append("Add a title.")
    if not draft["summary"]:
        problems.append("Add a summary — it is the search-engine description and the "
                        "Substack lead.")
    if not draft["slug"] or not _SLUG.match(draft["slug"]):
        problems.append("Set the web address (lower-case words joined by hyphens).")
    if not draft.get("market"):
        problems.append("Choose the market (Japan, United States or Global) — it places the "
                        "note on the PloverResearch homepage.")
    if not author_names:
        problems.append("Choose at least one author.")
    text_blocks = [b for b in draft["blocks"] if b["type"] in ("p", "list", "quote", "heading")]
    if not any(inline_text(b.get("html", "")).strip() or b.get("text") or
               any(inline_text(i).strip() for i in b.get("items", []))
               for b in text_blocks):
        problems.append("The article has no text.")
    n_chart = 0
    n_table = 0
    section = ""
    for b in draft["blocks"]:
        if b["type"] == "heading" and b["text"]:
            section = b["text"]
        if b["type"] == "placeholder":
            where = (" in “%s”" % section) if section else ""
            problems.append(PLACEHOLDER_PROBLEM + (
                "a chart is still to be added%s: %s" if b["role"] == "chart" else
                "the point still to be made%s: %s") % (where, b["text"] or "(no text)"))
        if b["type"] in FIGURE_TYPES:
            n_chart += 1
            label = "Chart %d" % n_chart
            if b["type"] == "snapshot":
                if not b["url"]:
                    problems.append("%s has no Plover page address." % label)
                elif not b["cfg"]:
                    problems.append("%s is waiting to be copied from %s: open the draft in "
                                    "the desk to capture it." % (label, b["url"]))
                if not b["title"]:
                    problems.append("%s needs a title." % label)
            elif b["type"] == "chart":
                if not b["dataset"] or not b["series"]:
                    problems.append("%s has no data selected." % label)
                if not b["title"]:
                    problems.append("%s needs a title." % label)
            elif b["type"] == "datachart":
                if not any(v is not None for col in data_table(b["rows"])["values"] for v in col):
                    problems.append("%s has no numbers: paste cells from a spreadsheet." % label)
                if not b["title"]:
                    problems.append("%s needs a title." % label)
                if not b["source"]:
                    problems.append("%s needs a source line: say where the numbers come from."
                                    % label)
            else:
                if not b["media"]:
                    problems.append("%s is waiting for its image to be uploaded%s."
                                    % (label, (" (" + b["pending"] + ")") if b["pending"] else ""))
                if not b["source"]:
                    problems.append("%s needs a source line." % label)
                if not b["alt"]:
                    problems.append("%s needs a text description for screen readers." % label)
        elif b["type"] == "table":
            n_table += 1
            if not b["source"]:
                problems.append("Table %d needs a source line." % n_table)
    return problems


# ---------------------------------------------------------------------------
# rendering

_NUMERIC_CELL = re.compile(
    u"^[\\s(]*[+\\-\u2212\u2013]?[\u00a5$\u20ac\u00a3]?\\s*[\\d][\\d,]*(\\.\\d+)?\\s*"
    u"(%|pp|bp|x|\u00d7|bn|tn|mn|k)?[)\\s]*$|^[\u2014\u2013\\-]$", re.IGNORECASE)


def _numeric_column(rows, col, header):
    body = rows[1:] if header else rows
    vals = [inline_text(r[col]).strip() for r in body if col < len(r)]
    vals = [v for v in vals if v]
    return bool(vals) and all(_NUMERIC_CELL.match(v) for v in vals)


def _true_minus(fragment):
    # a hyphen that is a minus sign: start of text, after a space, (, or a
    # currency sign, and followed by a digit
    return re.sub(u"(^|[\\s(\u00a5$>])-(?=\\d)", u"\\1\u2212", fragment)


class _Notes(object):
    def __init__(self):
        self.items = []

    def sub(self, fragment):
        def repl(m):
            self.items.append(html.unescape(m.group(1)))
            n = len(self.items)
            return ('<sup class="rs-fn" id="fnref-%d"><a href="#fn-%d" aria-label="Note %d">'
                    '%d</a></sup>' % (n, n, n, n))
        return re.sub(r'<sup data-note="([^"]*)"></sup>', repl, fragment)


def chart_numbering(blocks):
    """{block id: n} for charts and images, {block id: n} for tables."""
    charts, tables = {}, {}
    for b in blocks:
        if b["type"] in FIGURE_TYPES:
            charts[b["id"]] = len(charts) + 1
        elif b["type"] == "table":
            tables[b["id"]] = len(tables) + 1
    return charts, tables


def _period_text(p):
    return p[:7] if p and len(p) >= 7 else (p or "")


def chart_source_line(snap):
    """One line: where the numbers came from, which release, as of when."""
    if not snap:
        return ""
    rel = snap.get("release") or {}
    parts = [snap.get("credit") or ""]
    if rel.get("source_name"):
        parts.append(rel["source_name"])
    if rel.get("label"):
        parts.append("Release " + rel["label"])
    if snap.get("as_of"):
        parts.append("Data as of " + snap["as_of"])
    if snap.get("trust") == "official":
        parts.append("Values as published")
    return u" \u00b7 ".join(p.strip().rstrip(".") for p in parts if p and p.strip()) + "."


def csv_url(block, snap):
    """The API address that returns this chart's numbers, frozen at its date."""
    q = "series=%s&measure=%s" % (",".join(block["series"]), block["measure"])
    if block["start"]:
        q += "&start=" + block["start"]
    if block["end"]:
        q += "&end=" + block["end"]
    if snap and snap.get("as_of"):
        q += "&as_of=" + snap["as_of"]
    return "/api/v1/%s/observations?%s&format=csv" % (block["dataset"], q)


def render_body(doc, snapshots=None, media_base="/research/media/"):
    """The article body as HTML: the blocks and the notes. ``snapshots`` maps a
    chart block id to its frozen data (or an error) — the page script draws
    from that, never from a live request."""
    snapshots = snapshots or {}
    notes = _Notes()
    charts, tables = chart_numbering(doc["blocks"])
    out = []
    w = out.append
    for b in doc["blocks"]:
        t = b["type"]
        if t == "p":
            if inline_text(b["html"]).strip() or "data-note" in b["html"]:
                w("<p>%s</p>" % _true_minus(notes.sub(b["html"])))
        elif t == "heading":
            if b["text"]:
                tag = "h%d" % b["level"]
                w('<%s id="%s">%s</%s>' % (tag, esc(slugify(b["text"])[:60]), esc(b["text"]), tag))
        elif t == "quote":
            if inline_text(b["html"]).strip():
                w("<blockquote><p>%s</p></blockquote>" % _true_minus(notes.sub(b["html"])))
        elif t == "list":
            items = [i for i in b["items"] if inline_text(i).strip()]
            if items:
                tag = "ol" if b["style"] == "number" else "ul"
                w("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % _true_minus(notes.sub(i))
                                                for i in items), tag))
        elif t == "divider":
            w("<hr>")
        elif t == "table":
            w(_render_table(b, tables[b["id"]], notes))
        elif t == "chart":
            w(_render_chart(b, charts[b["id"]], snapshots.get(b["id"])))
        elif t == "image":
            w(_render_image(b, charts[b["id"]], media_base))
        elif t == "snapshot":
            w(_render_snapshot(b, charts[b["id"]]))
        elif t == "datachart":
            w(_render_datachart(b, charts[b["id"]]))
    if notes.items:
        w('<section class="rs-notes" aria-label="Notes"><h2>Notes</h2><ol>')
        for i, note in enumerate(notes.items, 1):
            w('<li id="fn-%d">%s <a class="rs-fn-back" href="#fnref-%d" '
              'aria-label="Back to the text">\u21a9</a></li>' % (i, esc(note), i))
        w("</ol></section>")
    return "\n".join(out)


def _render_table(b, n, notes):
    rows = b["rows"]
    header = b["header"] and len(rows) > 1
    width = len(rows[0]) if rows else 0
    aligns = []
    for c in range(width):
        a = b["align"][c] if c < len(b["align"]) else "auto"
        if a == "auto":
            a = "right" if _numeric_column(rows, c, header) else "left"
        aligns.append(a)

    def cell(tag, text, c):
        cls = ' class="num"' if aligns[c] == "right" else (
            ' class="ctr"' if aligns[c] == "center" else "")
        scope = ' scope="col"' if tag == "th" else ""
        return "<%s%s%s>%s</%s>" % (tag, cls, scope, _true_minus(notes.sub(text)), tag)

    parts = ['<figure class="rs-table" id="table-%d">' % n]
    cap = u"Table %d" % n + (u" \u2014 " + esc(b["caption"]) if b["caption"] else "")
    parts.append('<figcaption class="rs-fig-title">%s</figcaption>' % cap)
    parts.append('<div class="table-wrap"><table class="data" data-no-enhance>')
    body_rows = rows
    if header:
        parts.append("<thead><tr>%s</tr></thead>" % "".join(
            cell("th", rows[0][c], c) for c in range(width)))
        body_rows = rows[1:]
    parts.append("<tbody>")
    for r in body_rows:
        parts.append("<tr>%s</tr>" % "".join(
            cell("td", r[c] if c < len(r) else "", c) for c in range(width)))
    parts.append("</tbody></table></div>")
    if b["source"]:
        parts.append('<p class="source-line">Source: %s</p>' % esc(b["source"]))
    parts.append("</figure>")
    return "".join(parts)


def _render_chart(b, n, snap):
    title = u"Chart %d" % n + (u" \u2014 " + esc(b["title"]) if b["title"] else "")
    parts = ['<figure class="rs-chart" id="chart-%d" data-block="%s">' % (n, esc(b["id"])),
             '<figcaption class="rs-fig-title">%s</figcaption>' % title]
    if not snap or snap.get("error"):
        why = (snap or {}).get("error") or "The data for this chart could not be read."
        parts.append('<p class="rs-chart-error">%s</p>' % esc(why))
        parts.append("</figure>")
        return "".join(parts)
    # The unit is the chart's own axis title (and so in every exported image).
    # No trust badge on research pages: the source line says, in words, that
    # the values are as published (agreed with the user, 2026-10-03).
    parts.append('<div class="rs-plot" data-chart="%s" role="img" aria-label="%s"></div>'
                 % (esc(b["id"]), esc(_aria(b, snap))))
    if b["note"]:
        parts.append('<p class="rs-chart-note">%s</p>' % esc(b["note"]))
    parts.append('<p class="source-line">%s</p>' % esc(chart_source_line(snap)))
    if snap.get("trust") == "derived" and snap.get("calc"):
        parts.append('<details class="calc"><summary>Show calculation</summary>'
                     '<div class="calc-body"><code>%s</code></div></details>' % esc(snap["calc"]))
    parts.append('<div class="rs-chart-acts">'
                 '<button type="button" class="btn" data-png="%s" id="png-%s">Download PNG</button>'
                 '<a class="btn" href="%s">Download CSV</a>'
                 '</div>' % (esc(b["id"]), esc(b["id"]), esc(csv_url(b, snap))))
    parts.append("</figure>")
    return "".join(parts)


def _unit_caption(snap):
    measure = snap.get("measure")
    unit = snap.get("unit") or ""
    if measure == "yoy":
        return "% change on a year earlier"
    if measure == "mom":
        return "% change on the previous month"
    if measure == "ann3m":
        return "% three-month change, annualised"
    return unit


def _aria(b, snap):
    names = ", ".join(s.get("name_en") or s.get("code") for s in snap.get("series", []))
    return "Line chart: %s. %s." % (names, _unit_caption(snap))


def _date_long(iso):
    try:
        d = datetime.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return ""
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def snapshot_source_line(b):
    parts = [b["source"].strip().rstrip(".")] if b.get("source") else []
    if b.get("captured_at"):
        parts.append("Captured " + _date_long(b["captured_at"]))
    if (b.get("cfg") or {}).get("trust") == "official":
        parts.append("Values as published")
    return u" \u00b7 ".join(p for p in parts if p) + ("." if parts else "")


def _render_snapshot(b, n):
    title = u"Chart %d" % n + (u" \u2014 " + esc(b["title"]) if b["title"] else "")
    parts = ['<figure class="rs-chart" id="chart-%d" data-block="%s">' % (n, esc(b["id"])),
             '<figcaption class="rs-fig-title">%s</figcaption>' % title]
    if not b["cfg"]:
        parts.append('<p class="rs-chart-error">This chart has not been copied from its '
                     'page yet.</p></figure>')
        return "".join(parts)
    parts.append('<div class="rs-plot rs-plot-%s" data-chart="%s" role="img" aria-label="%s"></div>'
                 % (esc(b["kind"]), esc(b["id"]), esc("Chart: " + (b["title"] or b["page_title"]))))
    if b["note"]:
        parts.append('<p class="rs-chart-note">%s</p>' % esc(b["note"]))
    parts.append('<p class="source-line">%s</p>' % esc(snapshot_source_line(b)))
    if b["calc"]:
        parts.append('<details class="calc"><summary>Show calculation</summary>'
                     '<div class="calc-body">%s</div></details>' % esc(b["calc"]))
    parts.append('<div class="rs-chart-acts">'
                 '<button type="button" class="btn" data-png="%s" id="png-%s">Download PNG</button>'
                 '<button type="button" class="btn" data-csv="%s">Download CSV</button>'
                 '<a class="btn" href="%s">View on Plover</a></div>'
                 % (esc(b["id"]), esc(b["id"]), esc(b["id"]), esc(b["url"])))
    parts.append("</figure>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# charts of the writer's own numbers

_MISSING_CELLS = set(["", "-", u"\u2014", u"\u2013", "n/a", "na", "#n/a", "..", u"\u2026",
                      "nan", "null", "none"])
_NUMBER = re.compile(u"^([+\\-]?)[\u00a5$\u20ac\u00a3]?(\\d*\\.?\\d+(?:[eE][+\\-]?\\d+)?)%?$")


def data_number(cell):
    """A pasted cell as a number, or None when it is blank, a dash or not a
    number. "1,234.5", "(2.1)", "−3%" and "$40" are read; a missing cell
    is never read as zero. Mirrors researchChart.number in research.js."""
    s = (cell or "").strip()
    if s.lower() in _MISSING_CELLS:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = re.sub(u"[,\\s\u00a0]", "", s.replace(u"\u2212", "-"))
    s = re.sub(u"^([+\\-]?)[\u00a5$\u20ac\u00a3]", r"\1", s)
    m = _NUMBER.match(s)
    if not m:
        return None
    v = float(m.group(2)) * (-1 if m.group(1) == "-" else 1)
    return -v if neg else v


def data_table(rows):
    """The pasted cells read as a chart: series names (from the first row when
    it is a header), the date or label of each row, each series' values, and
    how many filled cells were not numbers. Mirrors researchChart.table."""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    width = max([len(r) for r in rows] or [0])
    if width < 2:
        return {"names": [], "labels": [], "values": [], "bad": 0, "header": False}
    head = rows[0]
    header = any((c or "").strip() and data_number(c) is None for c in head[1:])
    body = rows[1:] if header else rows
    names = [((head[i] if i < len(head) else "") or "").strip() if header else ""
             for i in range(1, width)]
    names = [n or "Series %d" % (i + 1) for i, n in enumerate(names)]
    values = [[data_number(r[i] if i < len(r) else "") for r in body] for i in range(1, width)]
    bad = sum(1 for r in body for i in range(1, width)
              if i < len(r) and (r[i] or "").strip().lower() not in _MISSING_CELLS
              and data_number(r[i]) is None)
    return {"names": names, "labels": [(r[0] if r else "").strip() for r in body],
            "values": values, "bad": bad, "header": header}


def datachart_source_line(b):
    parts = [b["source"].strip().rstrip(".")] if b.get("source") else []
    parts.append("Values from saved Plover data" if b.get("origin") == "database" else "Values as entered by the author")
    return "Source: " + u" \u00b7 ".join(parts) + "." if b.get("source") else parts[-1] + "."


def _render_datachart(b, n):
    title = u"Chart %d" % n + (u" \u2014 " + esc(b["title"]) if b["title"] else "")
    parts = ['<figure class="rs-chart" id="chart-%d" data-block="%s">' % (n, esc(b["id"])),
             '<figcaption class="rs-fig-title">%s</figcaption>' % title]
    table = data_table(b["rows"])
    if not any(v is not None for col in table["values"] for v in col):
        parts.append('<p class="rs-chart-error">This chart has no numbers yet.</p></figure>')
        return "".join(parts)
    parts.append('<div class="rs-plot" data-chart="%s" role="img" aria-label="%s"></div>'
                 % (esc(b["id"]), esc("%s chart: %s.%s" % (
                     "Bar" if b["kind"] == "bar" else "Line", ", ".join(table["names"]),
                     (" " + b["unit"] + ".") if b["unit"] else ""))))
    if b["note"]:
        parts.append('<p class="rs-chart-note">%s</p>' % esc(b["note"]))
    parts.append('<p class="source-line">%s</p>' % esc(datachart_source_line(b)))
    parts.append('<div class="rs-chart-acts">'
                 '<button type="button" class="btn" data-png="%s" id="png-%s">Download PNG</button>'
                 '<button type="button" class="btn" data-csv="%s">Download CSV</button></div>'
                 % (esc(b["id"]), esc(b["id"]), esc(b["id"])))
    parts.append("</figure>")
    return "".join(parts)


def _md_cells(cells):
    return "| " + " | ".join((c or "").replace("|", "\\|") for c in cells) + " |"


def _datachart_md(b):
    """The pasted cells as a Markdown table, the date or label column first."""
    rows = [r for r in b["rows"] if any((c or "").strip() for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    table = data_table(rows)
    head = rows[0] if table["header"] else [""] + table["names"]
    body = rows[1:] if table["header"] else rows
    return [_md_cells(head), "| --- |" + " --: |" * (width - 1)] + [_md_cells(r) for r in body]


def datachart_ref(b):
    """chart:line?unit=%25 — the editable Markdown's marker for a chart of the
    writer's own numbers; the table straight after it holds the numbers."""
    return "chart:%s" % b["kind"] + ("?unit=" + quote(b["unit"], safe="") if b["unit"] else "")


def _render_image(b, n, media_base):
    title = u"Chart %d" % n + (u" \u2014 " + esc(b["caption"]) if b["caption"] else "")
    parts = ['<figure class="rs-image" id="chart-%d">' % n,
             '<figcaption class="rs-fig-title">%s</figcaption>' % title]
    if b["media"]:
        dims = ""
        if b["width"] and b["height"]:
            dims = ' width="%d" height="%d"' % (b["width"], b["height"])
        parts.append('<img src="%s%s.%s" alt="%s"%s loading="lazy">'
                     % (media_base, b["media"], b["ext"], esc(b["alt"]), dims))
    else:
        parts.append('<p class="rs-chart-error">Image not uploaded yet%s.</p>'
                     % ((" (" + esc(b["pending"]) + ")") if b["pending"] else ""))
    if b["source"]:
        parts.append('<p class="source-line">Source: %s</p>' % esc(b["source"]))
    if b.get("link"):
        parts.append('<div class="rs-chart-acts"><a class="btn" href="%s">View on Plover</a></div>'
                     % esc(b["link"]))
    parts.append("</figure>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Markdown, out

def _md_inline(fragment, notes):
    def note(m):
        notes.append(html.unescape(m.group(1)))
        return "[^%d]" % len(notes)
    text = re.sub(r'<sup data-note="([^"]*)"></sup>', note, fragment or "")
    text = re.sub(r'<a href="([^"]*)">(.*?)</a>',
                  lambda m: "[%s](%s)" % (m.group(2), html.unescape(m.group(1))), text)
    text = re.sub(r"</?strong>", "**", text)
    text = re.sub(r"</?em>", "*", text)
    text = re.sub(r"</?code>", "`", text)
    text = re.sub(r"<br>", "  \n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def chart_ref(b):
    """A live-data chart as the one-line reference the editable Markdown uses:
    plover:<dataset>?series=a,b&measure=yoy&start=2020-01&end=."""
    q = "series=%s&measure=%s" % (",".join(b["series"]), b["measure"])
    if b["start"]:
        q += "&start=" + b["start"]
    if b["end"]:
        q += "&end=" + b["end"]
    return "plover:%s?%s" % (b["dataset"], q)


def render_markdown(doc, snapshots=None, site="", media_base="/research/media/", editable=False):
    """The article body as Markdown.

    Published (/research/<slug>.md, AI readers): charts become their title, a
    link to the frozen data and the source line — the numbers a reader would
    otherwise only see drawn.

    ``editable`` (the drafting tools for Claude and Codex): a form that
    import_markdown reads back without loss — a chart is
    ``![title](plover:dataset?series=…)``, a chart copied from a page is
    ``![title](/page.html?…#chart-2)``, a chart of the writer's own numbers is
    ``![title](chart:line?unit=%)`` followed by a table of the numbers, an
    uploaded image keeps its address,
    and a table or image keeps its "Source:" line. A reminder from the plan
    is ``[[To show: …]]`` or ``[[Chart: …]]`` on a line of its own."""
    snapshots = snapshots or {}
    notes = []
    charts, tables = chart_numbering(doc["blocks"])
    out = []
    for b in doc["blocks"]:
        t = b["type"]
        if t in ("p", "quote"):
            text = _md_inline(b["html"], notes).strip()
            if text:
                out.append(("> " + text.replace("\n", "\n> ")) if t == "quote" else text)
        elif t == "heading" and b["text"]:
            out.append("#" * b["level"] + " " + b["text"])
        elif t == "list":
            items = [_md_inline(i, notes).strip() for i in b["items"]]
            items = [i for i in items if i]
            if items:
                out.append("\n".join(("%d. " % (k + 1) if b["style"] == "number" else "- ") + i
                                     for k, i in enumerate(items)))
        elif t == "divider":
            out.append("---")
        elif t == "placeholder" and editable:
            out.append("[[%s: %s]]" % ("Chart" if b["role"] == "chart" else "To show",
                                       b["text"].replace("]]", "] ]")))
        elif t == "chart" and editable:
            line = "![%s](%s)" % (b["title"].replace("]", ")"), chart_ref(b))
            out.append(line + ("\n\nNote: " + b["note"] if b["note"] else ""))
        elif t == "snapshot" and editable:
            ref = b["url"] + ("#chart-%d" % b["chart"] if b["chart"] else "")
            line = "![%s](%s)" % (b["title"].replace("]", ")"), ref)
            out.append(line + ("\n\nNote: " + b["note"] if b["note"] else ""))
        elif t == "datachart" and editable:
            lines = ["![%s](%s)" % (b["title"].replace("]", ")"), datachart_ref(b))]
            table = _datachart_md(b)
            if table:
                lines += [""] + table
            if b["source"]:
                lines += ["", "Source: " + b["source"]]
            if b["note"]:
                lines += ["", "Note: " + b["note"]]
            out.append("\n".join(lines))
        elif t == "datachart":
            n = charts[b["id"]]
            lines = ["**Chart %d%s**" % (n, (u" \u2014 " + b["title"]) if b["title"] else "")]
            table = _datachart_md(b)
            if table:
                lines += [""] + table
            lines += ["", datachart_source_line(b)]
            out.append("\n".join(lines))
        elif t == "image" and editable:
            target = ("%s%s.%s" % (media_base, b["media"], b["ext"])) if b["media"] else \
                (b["pending"] or "missing-image.png")
            line = '![%s](%s "%s")' % (b["caption"].replace("]", ")"), target,
                                       b["alt"].replace('"', "'"))
            out.append(line + ("\n\nSource: " + b["source"] if b["source"] else ""))
        elif t == "snapshot":
            n = charts[b["id"]]
            lines = ["**Chart %d%s**" % (n, (u" \u2014 " + b["title"]) if b["title"] else "")]
            lines.append("")
            lines.append("From %s%s%s" % (site, b["url"], ". " + snapshot_source_line(b)
                                          if b["cfg"] else " (not yet captured)."))
            out.append("\n".join(lines))
        elif t == "table":
            n = tables[b["id"]]
            rows = [[_md_inline(c, notes).replace("|", "\\|").replace("\n", " ") for c in r]
                    for r in b["rows"]]
            lines = ["**Table %d%s**" % (n, (u" \u2014 " + b["caption"]) if b["caption"] else "")]
            if rows:
                head = rows[0] if b["header"] else [""] * len(rows[0])
                lines.append("| " + " | ".join(head) + " |")
                aligns = []
                for c in range(len(rows[0])):
                    a = b["align"][c]
                    if a == "auto":
                        a = "right" if _numeric_column(b["rows"], c, b["header"]) else "left"
                    aligns.append({"right": "--:", "center": ":-:"}.get(a, "---"))
                lines.append("| " + " | ".join(aligns) + " |")
                for r in (rows[1:] if b["header"] else rows):
                    lines.append("| " + " | ".join(r) + " |")
            if b["source"]:
                lines.append("")
                lines.append("Source: " + b["source"])
            out.append("\n".join(lines))
        elif t == "chart":
            n = charts[b["id"]]
            snap = snapshots.get(b["id"])
            lines = ["**Chart %d%s**" % (n, (u" \u2014 " + b["title"]) if b["title"] else "")]
            if snap and not snap.get("error"):
                names = ", ".join(s.get("name_en") or s.get("code") for s in snap.get("series", []))
                lines.append("")
                lines.append("%s (%s). Data: %s%s" % (names, _unit_caption(snap), site,
                                                     csv_url(b, snap)))
                lines.append("")
                lines.append(chart_source_line(snap))
            out.append("\n".join(lines))
        elif t == "image":
            n = charts[b["id"]]
            lines = ["**Chart %d%s**" % (n, (u" \u2014 " + b["caption"]) if b["caption"] else "")]
            if b["media"]:
                lines.append("")
                lines.append("![%s](%s%s%s.%s)" % (b["alt"], site, media_base, b["media"], b["ext"]))
            if b["source"]:
                lines.append("")
                lines.append("Source: " + b["source"])
            out.append("\n".join(lines))
    if notes:
        out.append("\n".join("[^%d]: %s" % (i, n) for i, n in enumerate(notes, 1)))
    return "\n\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Markdown, in — the research pipeline's draft.md

_MD_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)\s*$")
_MD_MEDIA = re.compile(r"^(?:https?://[^/]+)?/research/media/([0-9a-f]{64})\.(png|jpg|webp)$")
_MD_CHART_PLACEHOLDER = re.compile(
    u"^\\*\\*\\[(?:Chart|Figure)\\s*\\d*\\s*[\u2014\u2013:-]?\\s*(.*?)\\]\\*\\*\\s*$")
_MD_REMINDER = re.compile(r"^\[\[(To show|Chart)\s*:\s*(.*?)\]\]$", re.IGNORECASE)
_MD_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _note_plain(text):
    """A footnote is plain text: Markdown or HTML formatting in one is dropped."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(\*\*|`)", "", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    return html.unescape(text).strip()


def _md_to_inline(text, notes):
    out = esc(text).replace("&#x27;", "'")
    # footnote markers become placeholders until the formatting below has run,
    # so a "*" inside a note is never turned into markup in its attribute
    held = []

    def hold(m):
        note = _note_plain(notes.get(m.group(1), ""))
        if not note:
            return ""
        held.append('<sup data-note="%s"></sup>' % esc(note))
        return "\x00%d\x00" % (len(held) - 1)
    out = re.sub(r"\[\^([^\]]+)\]", hold, out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                 lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"<em>\1</em>", out)
    out = re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], out)
    return clean_inline(out)


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def import_markdown(text):
    """A Markdown file turned into a draft. Headings, paragraphs, lists,
    quotes, tables, rules and footnotes come across; an image or a chart
    placeholder becomes an image block waiting for its upload."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    notes = {}
    body = []
    for ln in lines:
        m = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", ln)
        if m:
            notes[m.group(1)] = m.group(2).strip()
        else:
            body.append(ln)
    draft = new_draft()
    blocks = []
    para = []

    def flush():
        if para:
            blocks.append({"id": _bid(), "type": "p",
                           "html": _md_to_inline(" ".join(s.strip() for s in para), notes)})
            del para[:]

    i = 0
    while i < len(body):
        ln = body[i]
        s = ln.strip()
        if not s:
            flush()
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", s)
        if h:
            flush()
            level = len(h.group(1))
            text = inline_text(_md_to_inline(h.group(2).strip(), {}))
            if level == 1 and not draft["title"]:
                draft["title"] = plain(text, TITLE_MAX)
            else:
                blocks.append({"id": _bid(), "type": "heading",
                               "level": 2 if level <= 2 else 3, "text": text})
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
            blocks.append({"id": _bid(), "type": "divider"})
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(body) and _MD_TABLE_SEP.match(body[i + 1].strip()):
            flush()
            if blocks and blocks[-1]["type"] == "datachart" and not blocks[-1]["rows"]:
                # the numbers of the chart marked just above
                rows = [_split_row(s)]
                i += 2
                while i < len(body) and body[i].strip().startswith("|"):
                    rows.append(_split_row(body[i]))
                    i += 1
                blocks[-1]["rows"] = [[inline_text(_md_to_inline(c, {})) for c in r] for r in rows]
                continue
            head = _split_row(s)
            seps = _split_row(body[i + 1])
            align = []
            for sp in seps:
                sp = sp.strip()
                align.append("right" if sp.endswith(":") and not sp.startswith(":") else
                             "center" if sp.startswith(":") and sp.endswith(":") else "auto")
            rows = [head]
            i += 2
            while i < len(body) and body[i].strip().startswith("|"):
                rows.append(_split_row(body[i]))
                i += 1
            width = max(len(r) for r in rows)
            rows = [[_md_to_inline(c, notes) for c in r] + [""] * (width - len(r)) for r in rows]
            # a bold "Table n — caption" line just above becomes the caption
            caption = ""
            if blocks and blocks[-1]["type"] == "p":
                prev = inline_text(blocks[-1]["html"]).strip()
                if re.match(u"^Table\\s*\\d+", prev) or (prev.endswith(":") and len(prev) < 200):
                    caption = re.sub(u"^Table\\s*\\d+\\s*[\u2014\u2013:-]?\\s*", "",
                                     prev).rstrip(":")
                    blocks.pop()
            blocks.append({"id": _bid(), "type": "table", "rows": rows, "header": True,
                           "align": align[:width] + ["auto"] * (width - len(align)),
                           "caption": caption, "source": ""})
            continue
        if re.match(r"^(Source|Note):\s+\S", s) and blocks and not para and \
                blocks[-1]["type"] in ("image", "table", "snapshot", "chart", "datachart"):
            key, value = s.split(":", 1)
            last = blocks[-1]
            if key == "Source" and last["type"] in ("image", "table", "datachart"):
                last["source"] = value.strip()
                i += 1
                continue
            if key == "Note" and last["type"] in ("snapshot", "chart", "datachart"):
                last["note"] = value.strip()
                i += 1
                continue
        rem = _MD_REMINDER.match(s)
        if rem:
            flush()
            blocks.append({"id": _bid(), "type": "placeholder",
                           "role": "chart" if rem.group(1).lower() == "chart" else "text",
                           "text": rem.group(2).strip()})
            i += 1
            continue
        img = _MD_IMAGE.match(s)
        ph = _MD_CHART_PLACEHOLDER.match(s)
        if img or ph:
            flush()
            target = img.group(2) if img else ""
            media = _MD_MEDIA.match(target) if img else None
            page = page_path(target) if img else ""
            data_ref = re.match(r"^chart:(line|bar)(?:\?unit=(.*))?$", target, re.IGNORECASE) \
                if img else None
            if img and target.lower().startswith("plover:"):
                blocks.append(_chart_from_ref(img.group(1), target))
            elif data_ref:
                blocks.append({"id": _bid(), "type": "datachart", "rows": [],
                               "kind": data_ref.group(1).lower(),
                               "unit": unquote(data_ref.group(2) or ""),
                               "title": img.group(1), "note": "", "source": ""})
            elif page:
                frag = re.search(r"#chart-(\d+)$", target)
                blocks.append({"id": _bid(), "type": "snapshot", "url": page,
                               "chart": int(frag.group(1)) if frag else 0,
                               "title": img.group(1), "note": ""})
            elif media:
                blocks.append({"id": _bid(), "type": "image", "media": media.group(1),
                               "ext": media.group(2), "alt": img.group(3) or img.group(1),
                               "caption": img.group(1), "source": ""})
            elif img:
                blocks.append({"id": _bid(), "type": "image", "media": "",
                               "alt": img.group(3) or img.group(1),
                               "caption": img.group(1), "source": "",
                               "pending": img.group(2).split("/")[-1]})
            else:
                blocks.append({"id": _bid(), "type": "image", "media": "", "alt": "",
                               "caption": ph.group(1).strip(), "source": "",
                               "pending": ph.group(1).strip()})
            i += 1
            continue
        if re.match(r"^([-*+])\s+", s) or re.match(r"^\d+[.)]\s+", s):
            flush()
            style = "number" if re.match(r"^\d+[.)]\s+", s) else "bullet"
            items = []
            while i < len(body):
                t = body[i].strip()
                m = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", t)
                if m:
                    items.append(m.group(1))
                elif t and items and body[i].startswith((" ", "\t")):
                    items[-1] += " " + t
                else:
                    break
                i += 1
            blocks.append({"id": _bid(), "type": "list", "style": style,
                           "items": [_md_to_inline(x, notes) for x in items]})
            continue
        if s.startswith(">"):
            flush()
            quote = []
            while i < len(body) and body[i].strip().startswith(">"):
                quote.append(body[i].strip()[1:].strip())
                i += 1
            blocks.append({"id": _bid(), "type": "quote",
                           "html": _md_to_inline(" ".join(quote), notes)})
            continue
        para.append(s)
        i += 1
    flush()
    # the italic standfirst line straight after the title becomes the dek
    if blocks and blocks[0]["type"] == "p":
        first = blocks[0]["html"]
        if first.startswith("<em>") and first.endswith("</em>") and len(first) < 400:
            draft["dek"] = plain(inline_text(first), TEXT_MAX)
            blocks.pop(0)
    draft["blocks"] = blocks or draft["blocks"]
    draft["slug"] = slugify(draft["title"]) if draft["title"] else ""
    return clean_draft(draft)


def _chart_from_ref(title, ref):
    """![title](plover:cpi-jp?series=0001,0161&measure=yoy&start=2020-01)"""
    rest = ref.split(":", 1)[1]
    dataset, _, query = rest.partition("?")
    params = {}
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return {"id": _bid(), "type": "chart", "dataset": dataset.strip().lower(),
            "series": [x for x in params.get("series", "").split(",") if x],
            "measure": params.get("measure", "index"), "start": params.get("start", ""),
            "end": params.get("end", ""), "title": title, "note": ""}


def today_iso():
    return datetime.datetime.utcnow().date().isoformat()
