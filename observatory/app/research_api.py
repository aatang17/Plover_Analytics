# -*- coding: utf-8 -*-
"""The research desk's API, under /admin/api/research.

Every endpoint needs a signed-in writer (app/writers.py: an email-link
session or a staff password session), and every article is checked against
the person's role in its publication — a writer sees and edits only their own
articles and cannot publish; editors and owners do everything. Every change is
written to the admin audit trail with their email. Publishing reads each chart's data as it stands today, freezes it into
the new version, renders the page and Markdown from the same draft, and
stores all of it at once (app/research.py) — a version is never edited
afterwards.

Nothing here touches the DuckDB datasets except to read chart data through
the same function /api/v1 serves it with.
"""
import datetime
import json
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import (chart_library, mailer, newsletter, market_history, pub_coverage, pub_profile, research, research_backup, research_doc as rd, research_pages as rp, seo, staff,
               writers)
from .admin_api import _client_ip, audit

router = APIRouter(prefix="/admin/api/research", include_in_schema=False)

PRESENCE_SECONDS = 75
_presence = {}   # article id -> {actor email: (name, last seen)}


def _writer(request):
    return writers.require_writer(request)


def _who(person):
    return staff.actor_label(person)


def _article_or_404(article_id, person=None, action="view"):
    a = research.get(article_id)
    if a is None:
        raise HTTPException(404, "No such article")
    if person is not None:
        writers.require(person, action, a)
    return a


def _url(a, slug=None):
    return seo.SITE_BASE_URL + research.address(a["publication_id"], slug or a["slug"] or
                                                a["draft"]["slug"] or "")


def _decorate(person, a):
    """What the editor needs besides the article: what this person may do with
    it and where its publication lives."""
    pub = research.publication(a["publication_id"])
    a["can"] = writers.permissions_for(person, a)
    a["role"] = writers.role(person, a["publication_id"])
    a["publication"] = {"id": pub["id"], "name": pub["name"], "base": pub["base"],
                        "home": pub["home"]}
    return a


def _publication_for_new(person, publication_id):
    """The publication a new article goes into: the one asked for (if the
    person can write there), else PloverResearch, else their first."""
    mine = writers.roles(person)
    if publication_id is not None:
        if publication_id not in mine:
            raise HTTPException(403, "You are not a member of that publication.")
        return publication_id
    if research.HOME in mine:
        return research.HOME
    return sorted(mine)[0]


def _audit(request, person, action, detail):
    audit(action, detail, _client_ip(request), by=_who(person))


def _authors_for(ids):
    names = []
    for i in ids:
        p = staff.get(i)
        if p is not None:
            names.append(p["name"])
    return names


def _today():
    return datetime.datetime.utcnow().date().isoformat()


# ---------------------------------------------------------------------------
# list, create, import

@router.get("/articles")
def list_articles(request: Request, publication_id: Optional[int] = None):
    """The articles this person can open, in one publication or all of theirs."""
    person = _writer(request)
    mine = writers.roles(person)
    if publication_id is not None and publication_id not in mine:
        raise HTTPException(403, "You are not a member of that publication.")
    out = []
    for a in research.list_articles(publication_id):
        if a["publication_id"] in mine and writers.can(person, "view", a):
            a["can"] = writers.permissions_for(person, a)
            a["url"] = _url(a) if a["published_version"] else None
            out.append(a)
    home_editor = writers.at_least(person, research.HOME, "editor")
    return {"articles": out, "site": seo.SITE_BASE_URL,
            "backup": research_backup.read_status() if home_editor else None}


@router.get("/authors")
def authors(request: Request, article_id: Optional[int] = None,
            publication_id: Optional[int] = None):
    """Everyone who can be credited on an article: the active members of its
    publication (PloverResearch: with the Writing permission or a membership)."""
    person = _writer(request)
    if article_id is not None:
        publication_id = _article_or_404(article_id, person)["publication_id"]
    elif publication_id is None:
        publication_id = research.HOME
    if writers.role(person, publication_id) is None:
        raise HTTPException(403, "You are not a member of that publication.")
    return {"authors": [{"id": m["id"], "name": m["name"]}
                        for m in writers.member_list(publication_id) if m["active"] and m["id"]]}


class NewBody(BaseModel):
    publication_id: Optional[int] = None


@router.post("/articles")
def create_article(request: Request, body: Optional[NewBody] = None):
    person = _writer(request)
    pid = _publication_for_new(person, body.publication_id if body else None)
    draft = rd.new_draft()
    # the publication's starting text, when its owner set one (pub_profile)
    template = pub_profile.defaults(pid)["template"]
    if template:
        try:
            draft = rd.import_markdown(template)
        except rd.DocError:
            draft = rd.new_draft()
    draft["authors"] = [person["id"]]
    a = research.create(_who(person), draft, pid)
    _audit(request, person, "article_created", "article %d" % a["id"])
    return _decorate(person, a)


class ImportBody(BaseModel):
    markdown: str
    publication_id: Optional[int] = None


@router.post("/import")
def import_markdown(body: ImportBody, request: Request):
    person = _writer(request)
    pid = _publication_for_new(person, body.publication_id)
    if len(body.markdown) > 2000000:
        raise HTTPException(400, "That file is too large to import.")
    try:
        draft = rd.import_markdown(body.markdown)
    except rd.DocError as exc:
        raise HTTPException(400, str(exc))
    draft["authors"] = [person["id"]]
    a = research.create(_who(person), draft, pid)
    _audit(request, person, "article_imported", "article %d: %s" % (a["id"], draft["title"]))
    return _decorate(person, a)


# ---------------------------------------------------------------------------
# one article

def _others(article_id, me):
    now = time.time()
    seen = _presence.get(article_id, {})
    return [{"name": name, "email": email} for email, (name, at) in seen.items()
            if email != me and now - at < PRESENCE_SECONDS]


@router.get("/articles/{article_id}")
def get_article(article_id: int, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person)
    a["others"] = _others(article_id, _who(person))
    a["site"] = seo.SITE_BASE_URL
    return _decorate(person, a)


@router.post("/articles/{article_id}/presence")
def presence(article_id: int, request: Request):
    """Called every half minute by an open editor: who else has it open."""
    person = _writer(request)
    a = _article_or_404(article_id, person)
    _presence.setdefault(article_id, {})[_who(person)] = (person["name"], time.time())
    return {"others": _others(article_id, _who(person)), "revision": a["revision"],
            "updated_by": a["updated_by"], "updated_at": a["updated_at"]}


class SaveBody(BaseModel):
    draft: dict
    base_revision: int
    label: Optional[str] = None


@router.put("/articles/{article_id}")
def save_article(article_id: int, body: SaveBody, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person, "edit")
    if body.draft.get("slug"):
        slug = rd.plain(body.draft.get("slug"), rd.SLUG_MAX).lower()
        if rd._SLUG.match(slug) and research.slug_taken(slug, article_id):
            raise HTTPException(400, "Another article already uses the address %s."
                                % research.address(a["publication_id"], slug))
    try:
        saved = research.save(article_id, body.draft, body.base_revision, _who(person),
                              label=rd.plain(body.label, 80) or None)
    except research.Conflict as exc:
        return JSONResponse(status_code=409, content={
            "detail": "%s saved a newer version of this article." % exc.current["updated_by"],
            "current": exc.current})
    except (rd.DocError, research.ResearchError) as exc:
        raise HTTPException(400, str(exc))
    return saved


@router.delete("/articles/{article_id}")
def delete_article(article_id: int, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person, "delete")
    try:
        research.delete_draft(article_id)
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "article_deleted", "article %d: %s" % (article_id, a["title"]))
    return {"deleted": article_id}


@router.get("/articles/{article_id}/history")
def article_history(article_id: int, request: Request):
    _article_or_404(article_id, _writer(request))
    return {"history": research.history(article_id)}


@router.get("/articles/{article_id}/history/{history_id}")
def article_history_draft(article_id: int, history_id: int, request: Request):
    _article_or_404(article_id, _writer(request))
    draft = research.history_draft(article_id, history_id)
    if draft is None:
        raise HTTPException(404, "No such saved copy")
    return {"draft": draft}


def _check(a):
    """(problems, author names, snapshots) for the article's saved draft."""
    draft = a["draft"]
    names = _authors_for(draft["authors"])
    problems = rd.publish_problems(draft, names)
    if draft["slug"] and rd._SLUG.match(draft["slug"]) and not a["slug_locked"] and \
            research.slug_taken(draft["slug"], a["id"]):
        problems.append("Another article already uses the address %s."
                        % research.address(a["publication_id"], draft["slug"]))
    snaps = rp.build_snapshots(draft, _today())
    charts, _ = rd.chart_numbering(draft["blocks"])
    for b in draft["blocks"]:
        if b["type"] == "chart" and b["dataset"] and b["series"]:
            err = (snaps.get(b["id"]) or {}).get("error")
            if err:
                problems.append("Chart %d: %s" % (charts[b["id"]], err))
    return problems, names, snaps


@router.get("/articles/{article_id}/check")
def check_article(article_id: int, request: Request):
    a = _article_or_404(article_id, _writer(request))
    problems, names, _ = _check(a)
    return {"problems": problems, "authors": names, "revision": a["revision"],
            "version": (a["published_version"] or 0) + 1, "url": _url(a)}


def _preview_version(a, names, snaps):
    draft = a["draft"]
    return {
        "slug": draft["slug"] or "preview",
        "version": (a["published_version"] or 0) + 1,
        "title": draft["title"] or "Untitled",
        "dek": draft["dek"],
        "summary": draft["summary"],
        "authors": names,
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
        "body_html": rd.render_body(draft, snaps),
        "doc": draft,
        "snapshots": snaps,
    }


@router.get("/articles/{article_id}/preview")
def preview_article(article_id: int, request: Request):
    """The saved draft exactly as the published page would show it now."""
    a = _article_or_404(article_id, _writer(request))
    pub = research.publication(a["publication_id"])
    names = _authors_for(a["draft"]["authors"])
    snaps = rp.build_snapshots(a["draft"], _today())
    v = _preview_version(a, names, snaps)
    versions = [dict(x, change_note=x["change_note"]) for x in
                research.versions_meta(article_id)] if a["published_version"] else []
    versions.append({"version": v["version"], "published_at": v["published_at"],
                     "change_note": "This preview."})
    main = rp.article_main(v, versions, v["version"], "", preview=True, pub=pub)
    text = rp.page("Preview · %s" % v["title"], v["summary"], None, main,
                   data=rp._chart_data(v), robots="noindex, nofollow", preview=True, pub=pub)
    headers = dict(rp.SECURITY_HEADERS)
    headers["Cache-Control"] = "no-store"
    return HTMLResponse(text, headers=headers)


class PublishBody(BaseModel):
    base_revision: int
    change_note: str = ""


@router.post("/articles/{article_id}/publish")
def publish_article(article_id: int, body: PublishBody, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person, "publish")
    if a["revision"] != body.base_revision:
        return JSONResponse(status_code=409, content={
            "detail": "%s saved a newer version of this article. Reload it, check it, and "
                      "publish again." % a["updated_by"]})
    if a["status"] == "withdrawn":
        raise HTTPException(400, "Reinstate the article before publishing a new version.")
    problems, names, snaps = _check(a)
    if problems:
        return JSONResponse(status_code=400, content={
            "detail": "The article is not ready to publish.", "problems": problems})
    draft = dict(a["draft"])
    if a["slug_locked"]:
        draft["slug"] = a["slug"]
    body_html = rd.render_body(draft, snaps)
    markdown = rd.render_markdown(draft, snaps, site=seo.SITE_BASE_URL)
    try:
        done = research.publish(article_id, body.base_revision, _who(person),
                                rd.plain(body.change_note, 400), draft, names, snaps,
                                body_html, markdown)
    except research.Conflict:
        return JSONResponse(status_code=409, content={
            "detail": "Someone saved while this was publishing. Reload and publish again."})
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    path = research.address(a["publication_id"], done["slug"])
    _audit(request, person, "article_published", "%s version %d" % (path, done["version"]))
    rp.announce(path)
    research_backup.soon()
    done["url"] = seo.SITE_BASE_URL + path
    return done


@router.post("/articles/{article_id}/review")
def ask_to_publish(article_id: int, request: Request):
    """A writer hands the article to the publication's editors. It is marked
    on their article list until a version is published."""
    person = _writer(request)
    a = _article_or_404(article_id, person, "edit")
    if a["status"] == "withdrawn":
        raise HTTPException(400, "This article was withdrawn. An editor can reinstate it.")
    research.request_review(article_id, _who(person))
    _audit(request, person, "article_review_requested", "article %d: %s"
           % (article_id, a["title"]))
    return _decorate(person, research.get(article_id))


class WithdrawBody(BaseModel):
    reason: str


@router.post("/articles/{article_id}/withdraw")
def withdraw_article(article_id: int, body: WithdrawBody, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person, "publish")
    try:
        research.withdraw(article_id, _who(person), body.reason)
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "article_withdrawn", "%s: %s"
           % (research.address(a["publication_id"], a["slug"]), body.reason))
    research_backup.soon()
    return _decorate(person, research.get(article_id))


@router.post("/articles/{article_id}/reinstate")
def reinstate_article(article_id: int, request: Request):
    person = _writer(request)
    a = _article_or_404(article_id, person, "publish")
    try:
        research.reinstate(article_id)
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "article_reinstated", research.address(a["publication_id"], a["slug"]))
    return _decorate(person, research.get(article_id))


class EmailBody(BaseModel):
    version: int


def _email_state(a):
    pub = research.publication(a["publication_id"])
    v = a["published_version"]
    return {"available": newsletter.can_collect(pub), "publication": pub["name"],
            "version": v, "subscribers": newsletter.counts(pub["id"])["active"],
            "email_ready": mailer.configured(),
            "send": newsletter.send_for(a["id"], v) if v else None}


@router.get("/articles/{article_id}/email")
def email_state(article_id: int, request: Request):
    """Whether the current version can be emailed to the publication's list,
    to how many, and how a send of it is going."""
    a = _article_or_404(article_id, _writer(request), "publish")
    return _email_state(a)


@router.post("/articles/{article_id}/email")
def email_article(article_id: int, body: EmailBody, request: Request):
    """Email a published version to the publication's subscribers (or resume
    an interrupted send of it). Editors and owners: whoever may publish."""
    person = _writer(request)
    a = _article_or_404(article_id, person, "publish")
    pub = research.publication(a["publication_id"])
    if not newsletter.can_collect(pub):
        raise HTTPException(400, "PloverResearch posts through Substack; use Prepare Substack Post.")
    if body.version != a["published_version"]:
        raise HTTPException(409, "A newer version is published. Reload the editor and send that one.")
    try:
        send = newsletter.start_send(pub, article_id, body.version, _who(person),
                                     person.get("email") or "")
    except newsletter.NewsletterError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "article_emailed", "%s version %d to %d subscribers" % (
        research.address(a["publication_id"], a["slug"]), body.version, send["total"]))
    state = _email_state(research.get(article_id))
    return state


@router.get("/articles/{article_id}/substack")
def substack(article_id: int, request: Request):
    """What to paste into Substack: the title, the standfirst, the summary and
    a link to the full piece. Rich text and plain text, so either paste works."""
    a = _article_or_404(article_id, _writer(request))
    if not a["published_version"]:
        raise HTTPException(400, "Publish the article first: the Substack post links to it.")
    v = research.version(article_id, a["published_version"])
    url = seo.SITE_BASE_URL + research.address(a["publication_id"], v["slug"])
    link_text = "Read the full note, with the charts and data, on " + \
        research.publication(a["publication_id"])["name"]
    html = "".join([
        "<p>%s</p>" % rd.esc(v["summary"]),
        '<p><a href="%s">%s →</a></p>' % (rd.esc(url), link_text),
    ])
    text = "%s\n\n%s: %s\n" % (v["summary"], link_text, url)
    charts = []
    for b in v["doc"]["blocks"]:
        if b["type"] == "chart" and not (v["snapshots"].get(b["id"]) or {}).get("error"):
            charts.append({"id": b["id"], "title": b["title"], "block": b,
                           "snap": v["snapshots"][b["id"]]})
    return {"title": v["title"], "subtitle": v["dek"], "html": html, "text": text, "url": url,
            "charts": charts, "version": v["version"]}


# ---------------------------------------------------------------------------
# research panel: Plover data, and reading a web page

# Company pages a chart can be copied from, with the parameter each one reads
# its company from (company.html takes `code`; the dataset pages take `c`).
COMPANY_PAGES = [
    ("Company Overview", "/company.html?code=%s"),
    ("Financials", "/financials.html?c=%s"),
    ("Ownership", "/ownership.html?c=%s"),
    ("Earnings", "/earnings.html?c=%s"),
    ("Buybacks", "/buyback.html?c=%s"),
    ("Business Risks", "/risks.html?c=%s"),
]


# Words a writer uses for a topic that the pages put another way: "CEO
# compensation" is "officer remuneration" on the Japanese page and "CEO pay"
# on the US one. A query word matches a page when it, or one of these, is in
# the page's title or description.
TOPIC_WORDS = {
    "compensation": ("pay", "remuneration"),
    "remuneration": ("pay", "compensation"),
    "pay": ("remuneration", "compensation"),
    "salary": ("wage", "earnings", "pay"),
    "salaries": ("wage", "earnings", "pay"),
    "ceo": ("chief executive", "executive", "officer"),
    "ceos": ("chief executive", "executive", "officer"),
    "executive": ("officer", "ceo"),
    "inflation": ("cpi", "consumer price"),
    "jobless": ("unemployment",),
    "buyback": ("repurchase",),
    "buybacks": ("repurchase", "buyback"),
}
_PAGES = []


def _page_index():
    """Every public page: its address, title and description (the same
    description it serves to search engines, app/prerender.DESCRIPTIONS)."""
    if not _PAGES:
        import html as html_lib
        import pathlib
        import re
        from . import prerender
        web = pathlib.Path(prerender.WEB_DIR or pathlib.Path(__file__).resolve().parent.parent / "web")
        for name, desc in sorted(prerender.DESCRIPTIONS.items()):
            path = web / name
            if not path.exists():
                continue
            m = re.search(r"(?is)<title>(.*?)</title>", path.read_text(encoding="utf-8"))
            title = html_lib.unescape(m.group(1)).split(" · ")[0].strip() if m else name
            _PAGES.append({"url": "/" + name, "title": title, "description": desc,
                           "text": (title + " " + desc).lower()})
    return _PAGES


def page_hits(q, limit=6):
    """Pages about the topic in q: every word (or a word for the same thing)
    starting a word in the page's title or description. Title matches first.
    The query is read as the series search reads it (tools_v2._read_query):
    "us" names the US, it is not the "us" in "Housing"; Japan is the default
    and needs no word."""
    import re
    from . import tools_v2
    needs = []
    jp_only = False
    for t in tools_v2._read_query(q)["terms"]:
        if t["market"] == "jp":
            jp_only = True
            continue
        forms = t["forms"] + t["syn"] + TOPIC_WORDS.get(t["forms"][0], ())
        if t["market"] == "us":
            forms = ("us", "u.s.", "united states", "american")
        needs.append(re.compile("|".join("(?<![a-z0-9])" + re.escape(f) for f in forms)))
    if not needs:
        return []
    hits = []
    for page in _page_index():
        title = page["title"].lower()
        if jp_only and title.startswith("us "):
            continue
        if all(n.search(page["text"]) for n in needs):
            hits.append((-sum(bool(n.search(title)) for n in needs), page["title"], page))
    hits.sort(key=lambda h: h[:2])
    return [{"url": p["url"], "title": p["title"], "description": p["description"]}
            for _, _, p in hits[:limit]]


@router.get("/data/search")
def data_search(request: Request, q: str = "", response: Response = None):
    """Plover series and companies matching q — the same search the public MCP
    server runs — with the page each hit lives on, plus the pages about q as
    a topic ("CEO compensation"), whose charts can be copied."""
    person = _writer(request)
    if response is not None:
        response.headers["Cache-Control"] = "private, no-store"
    from . import registry, tools_v2
    if not q.strip():
        return {"series": [], "companies": [], "pages": []}
    raw = json.loads(tools_v2.search(q.strip()[:200], limit=15))
    if raw.get("error"):
        raise HTTPException(400, raw["error"])
    series = []
    for h in raw.get("series") or []:
        card = registry.get(h["dataset"]) or {}
        series.append({"dataset": h["dataset"], "code": h["code"], "name": h["name_en"],
                       "unit": h.get("unit"),
                       "dataset_name": (card.get("name") or {}).get("en") or h["dataset"],
                       "page": card.get("page")})
    companies = []
    for c in raw.get("companies") or []:
        # Japanese listed companies only: the copyable pages below read a
        # securities code; a US filer (cik:…) has no such pages.
        if not c.get("sec_code") or str(c["sec_code"]).startswith("cik:"):
            continue
        if len(companies) >= 8:
            break
        companies.append({"code": c["sec_code"], "name": c.get("name_en") or c.get("name"),
                          "name_ja": c.get("name"),
                          "pages": [{"label": lab, "url": tpl % c["sec_code"]}
                                    for lab, tpl in COMPANY_PAGES]})
    out = {"series": series, "companies": companies, "pages": page_hits(q),
           # the chart people mean by this, when there is one ("CPI", "10-year")
           "common": chart_library.match(q), "company_first": False, "places": [],
           "market": market_history.catalog(person, q)}
    nick = _nickname_codes(q)
    named = next((c for c in companies if c["code"] in nick or _company_intent(q, c)), None)
    if named is not None and not out["common"]:
        # "Toyota": the company, with its charts ready, before Toyota City's guest nights
        companies.remove(named)
        companies.insert(0, named)
        out["company_first"] = True
        companies[0]["charts"] = chart_library.company_charts(companies[0]["code"])
        out["places"] = [x for x in series if x["dataset"] in PLACE_DATASETS]
        out["series"] = [x for x in series if x["dataset"] not in PLACE_DATASETS]
    return out


# Private hourly data never enters the public macro catalog or its cache.
@router.get("/market")
def market_catalog(request: Request):
    return JSONResponse({"market": market_history.catalog(_writer(request))},
                        headers={"Cache-Control": "private, no-store"})


# Private hourly data never enters the public macro catalog or its cache.
@router.get("/market/{slug}/history")
def market_rows(slug: str, request: Request, start: str = "", end: str = "",
                publisher: Optional[int] = None, limit: int = 250, offset: int = 0,
                revision: str = ""):
    person = _writer(request)
    try:
        out = market_history.history(person, slug, start, end, publisher, limit, offset, revision)
    except market_history.HistoryError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(out, headers={"Cache-Control": "private, no-store"})


@router.get("/market/{slug}/chart")
def market_chart(slug: str, request: Request, start: str = "", end: str = "",
                 publisher: Optional[int] = None, field: str = "close", revision: str = ""):
    person = _writer(request)
    try:
        out = market_history.chart(person, slug, start, end, publisher, field, revision)
    except market_history.HistoryError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({"block": out}, headers={"Cache-Control": "private, no-store"})


@router.get("/market/{slug}/csv")
def market_csv(slug: str, request: Request, start: str = "", end: str = "",
               publisher: Optional[int] = None, revision: str = ""):
    person = _writer(request)
    try:
        data = market_history.export(person, slug, start, end, publisher, revision)
    except market_history.HistoryError as exc:
        raise HTTPException(400, str(exc))
    return StreamingResponse(data, media_type="text/csv", headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="%s-hourly.csv"' % slug})


# Datasets whose series are named after places ("Toyota-shi, Aichi — guest
# nights"): when a search names a company, they are listed apart, last.
PLACE_DATASETS = frozenset(("accommodation-jp", "population-jp", "population-jp-municipal",
                            "population-jp-history", "local-finance-jp", "fsa-regional-fi"))
_CO_SUFFIX = re.compile(r"\b(corporation|corp|co|company|inc|incorporated|ltd|limited|holdings|group|"
                        r"kabushiki kaisha|k\.k)\b\.?", re.I)


def _nickname_codes(q):
    """Codes a market nickname stands for ("MUFG"): app/aliases.py."""
    try:
        from . import aliases, equity_api
        return set(aliases.codes(equity_api._cur(), q))
    except Exception:  # noqa: BLE001 — no equity data: no nicknames
        return set()


def _company_intent(q, company):
    """True when q names this company: its code, or the start of its name
    ("toyota", "sony group"), in English or Japanese."""
    ql = " ".join(q.lower().split())
    if not ql:
        return False
    if ql == str(company.get("code") or "").lower():
        return True
    en = " ".join(_CO_SUFFIX.sub(" ", (company.get("name") or "").lower()).split())
    ja = company.get("name_ja") or ""
    return (len(ql) >= 3 and en.startswith(ql)) or (len(ql) >= 2 and ql in ja)


# ---------------------------------------------------------------------------
# chart library (app/chart_library.py): common, company, saved and most used

def _own_writer(request):
    person = _writer(request)
    if person.get("shared") or not person.get("id"):
        raise HTTPException(403, "Sign in with your own account to keep saved charts: they are personal.")
    return person


@router.get("/library")
def library(request: Request, article: Optional[int] = None, response: Response = None):
    """The Charts tab: the writer's saved charts, the most used and common
    charts, and the coverage list of the open article's publication."""
    person = _writer(request)
    if response is not None:
        response.headers["Cache-Control"] = "private, no-store"
    out = chart_library.view(person["id"]) if person.get("id") else \
        {"topics": chart_library.topics(), "popular": chart_library.popular(), "saved": []}
    out["coverage"], out["publication"] = [], None
    out["market"] = market_history.catalog(person)
    if article:
        a = research.get(article)
        if a is not None and writers.can(person, "view", a):
            pub = research.publication(a["publication_id"])
            out["coverage"] = pub_coverage.companies(a["publication_id"])
            out["publication"] = pub["name"] if pub else None
    return out


@router.get("/company-charts/{code}")
def company_charts(code: str, request: Request):
    _writer(request)
    return {"code": code.upper(), "charts": chart_library.company_charts(code)}


class UsedBody(BaseModel):
    id: str


@router.post("/library/used")
def library_used(body: UsedBody, request: Request):
    _writer(request)
    chart_library.used(body.id)
    return {"ok": True}


class SaveChartBody(BaseModel):
    block: dict


@router.post("/library/saved")
def library_save(body: SaveChartBody, request: Request):
    person = _own_writer(request)
    try:
        return chart_library.save(person["id"], body.block)
    except chart_library.LibraryError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/library/saved/{saved_id}")
def library_unsave(saved_id: int, request: Request):
    person = _own_writer(request)
    return {"saved": chart_library.unsave(person["id"], saved_id)}


class ReadBody(BaseModel):
    url: str


@router.post("/web/read")
def web_read(body: ReadBody, request: Request):
    _writer(request)
    from . import research_web
    try:
        return research_web.read(body.url)
    except research_web.WebError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# images

@router.post("/media")
async def upload_media(request: Request):
    """The image as the raw request body; X-File-Name carries its name. A raw
    body, not a multipart form, so no form-parsing package is needed."""
    person = _writer(request)
    length = int(request.headers.get("content-length") or 0)
    if length > research.MEDIA_MAX_BYTES:
        raise HTTPException(413, "Images can be up to %d MB."
                            % (research.MEDIA_MAX_BYTES // (1024 * 1024)))
    data = await request.body()
    try:
        info = research.add_media(data, request.headers.get("x-file-name", ""), _who(person))
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "image_uploaded", "%s.%s (%d bytes)"
           % (info["media"][:12], info["ext"], info["bytes"]))
    return info


@router.post("/backup")
def backup_now(request: Request):
    person = _writer(request)
    if not writers.at_least(person, research.HOME, "editor"):
        raise HTTPException(403, "Backups are run by PloverResearch's editors.")
    status = research_backup.run()
    _audit(request, person, "backup_run", "ok" if status.get("ok") else status.get("error"))
    return status


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
