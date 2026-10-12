# -*- coding: utf-8 -*-
"""The Writer Desk's own API, under /admin/api/write (``web/write.html``).

The article endpoints the desk shares with the editor are in
app/research_api.py. This module holds what is about the writer and their
publications rather than one article:

``GET  /me``                                who is signed in, and where they write
``POST /signout``                           end this browser's writer sessions
``POST /publications``                      open a publication: one of your own, or (Plover team) for anyone
``PUT  /publications/{id}``                 its name, tagline and description (owner)
``GET  /publications/{id}/members``         who writes there (any member)
``POST /publications/{id}/members``         invite someone (owner); returns a sign-in link
``PUT  /publications/{id}/members``         change a role (owner)
``DELETE /publications/{id}/members``       remove someone (owner)
``POST /publications/{id}/members/link``    a fresh sign-in link for a member (owner)
``GET  /publications/{id}/assistant``       its Assistant Settings: skills, house style, connectors (any member)
``POST/PUT/DELETE .../assistant/skills``    its skills (editor)
``PUT  .../assistant/house-style``          its house style (editor)
``POST/PUT/DELETE .../assistant/connectors`` its connectors, their check and tools (owner)
``GET  /publications/{id}/profile``         its own AI key, article defaults and look (any member)
``PUT/DELETE .../profile/ai``               its AI key (owner)
``PUT  .../profile/defaults``               length, starting text, default skill (editor)
``PUT  .../profile/look``                   logo and accent colour (owner)
``GET/POST/PATCH/DELETE .../data-sources``  its own market-data keys, and ``/{sid}/try`` (owner)
``GET/POST/DELETE .../coverage``           its coverage list (any member reads; editors change)
``GET  /companies?q=``                      find a listed company to add to a coverage list
``GET/POST/DELETE /keys``                   personal keys for the writer's own AI agent
``GET/POST/PATCH/DELETE /data-sources``      the writer's own market-data accounts (and ``/{id}/try``)
``GET/PUT /style``                          the writer's own writing style: description and samples

It sits under /admin/api only so that the Plover team's staff session cookie
(scoped to /admin) reaches it; nothing here is part of the admin console.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import accounts, connectors, mailer, research, seo, skills, staff, writers, writing_style
from . import pub_coverage, pub_profile
from .assistant import market_data, memory, store as ia_store
from .admin_api import STAFF_COOKIE, _client_ip, audit

router = APIRouter(prefix="/admin/api/write", include_in_schema=False)


def _audit(request, person, action, detail):
    audit(action, detail, _client_ip(request), by=staff.actor_label(person))


def _pub_or_404(publication_id):
    pub = research.publication(publication_id)
    if pub is None:
        raise HTTPException(404, "No such publication")
    return pub


def _need(person, publication_id, role):
    pub = _pub_or_404(publication_id)
    if writers.role(person, publication_id) is None:
        raise HTTPException(404, "No such publication")
    if not writers.at_least(person, publication_id, role):
        raise HTTPException(403, "Only the publication's %ss can do that." % role)
    return pub


def _pub_view(pub, role):
    return {"id": pub["id"], "slug": pub["slug"], "name": pub["name"], "tagline": pub["tagline"],
            "about": pub["about"], "base": pub["base"], "home": pub["home"], "role": role,
            "url": seo.SITE_BASE_URL + pub["base"]}


# ------------------------------------------------------------------ who

@router.get("/me")
def me(request: Request):
    """Never 401: the page uses this to choose between the sign-in screen and
    the desk, and says why someone signed in still cannot write."""
    sign_in = {"email_links": accounts.enabled(), "email_delivery": mailer.configured()}
    person = writers.current_writer(request)
    if person is None:
        email = writers.signed_in_email(request)
        return {"signed_in": bool(email), "writer": False, "email": email, "sign_in": sign_in}
    mine = writers.roles(person)
    pubs = [_pub_view(research.publication(pid), r) for pid, r in sorted(mine.items())]
    return {"signed_in": True, "writer": bool(pubs), "email": person["email"],
            "person": {"id": person["id"], "name": person["name"], "email": person["email"]},
            "publications": pubs,
            "can_create_publications": writers.can_create_publications(person),
            "opens_for_others": writers.opens_for_others(person),
            "sign_in": sign_in}


@router.post("/signout")
def signout(request: Request, response: Response):
    """Ends both kinds of session this browser may hold for the desk."""
    person = writers.current_writer(request)
    staff.end_session(request.cookies.get(STAFF_COOKIE))
    response.delete_cookie(STAFF_COOKIE, path="/admin")
    raw = request.cookies.get(accounts.COOKIE)
    if raw:
        db = accounts.conn()
        with accounts._lock:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (accounts._hash(raw),))
            db.commit()
        response.delete_cookie(accounts.COOKIE, path="/")
    if person is not None:
        _audit(request, person, "writer_signout", "signed out of the Writer Desk")
    return {"signed_out": True}


# --------------------------------------------------------- publications

class NewPublication(BaseModel):
    slug: str
    name: str
    tagline: str = ""
    owner_email: str = ""
    owner_name: str = ""


@router.post("/publications")
def create_publication(body: NewPublication, request: Request):
    """Open a publication and make its first owner. A writer opens one of
    their own; the Plover team (Team permission) opens one for anyone."""
    person = writers.require_writer(request)
    if not writers.can_create_publications(person):
        raise HTTPException(403, "You already own a publication. Each person opens one of their own.")
    actor = staff.actor_label(person)
    owner_email = staff.normalise_email(body.owner_email or person["email"])
    if owner_email != person["email"] and not writers.opens_for_others(person):
        raise HTTPException(403, "You can open a publication for yourself only. The Plover team "
                                 "opens publications for other people.")
    try:
        if owner_email != person["email"]:
            staff.ensure_person(owner_email, body.owner_name, actor)
        pub = research.create_publication(body.slug, body.name, body.tagline, actor)
    except (staff.StaffError, research.ResearchError) as exc:
        raise HTTPException(400, str(exc))
    research.set_member(pub["id"], owner_email, "owner", actor)
    link = None if owner_email == person["email"] else writers.signin_link(request, owner_email)
    emailed = writers.email_link(request, "owner", owner_email, link, pub, "owner", person)
    _audit(request, person, "publication_created", "%s (%s), owner %s"
           % (pub["base"], pub["name"], owner_email))
    return {"publication": _pub_view(pub, writers.role(person, pub["id"])),
            "link": None if emailed else link, "emailed": emailed, "owner_email": owner_email}


class PublicationBody(BaseModel):
    name: str
    tagline: str = ""
    about: str = ""


@router.put("/publications/{publication_id}")
def update_publication(publication_id: int, body: PublicationBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    if pub["home"]:
        raise HTTPException(400, "PloverResearch's name and description are part of the site "
                                 "and are changed with it, not here.")
    try:
        pub = research.update_publication(publication_id, staff.actor_label(person), body.name,
                                          body.tagline, body.about)
    except research.ResearchError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "publication_updated", pub["base"])
    return _pub_view(pub, writers.role(person, publication_id))


# --------------------------------------------------------------- members

@router.get("/publications/{publication_id}/members")
def members(publication_id: int, request: Request):
    person = writers.require_writer(request)
    _need(person, publication_id, "writer")
    return {"members": writers.member_list(publication_id),
            "manage": writers.at_least(person, publication_id, "owner")}


class InviteBody(BaseModel):
    email: str
    name: str = ""
    role: str = "writer"


@router.post("/publications/{publication_id}/members")
def invite(publication_id: int, body: InviteBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    row, link = writers.invite(request, publication_id, body.email, body.name, body.role,
                               staff.actor_label(person))
    emailed = writers.email_link(request, "invite", row["email"], link, pub, row["role"], person)
    _audit(request, person, "member_invited", "%s to %s as %s%s" % (
        row["email"], pub["base"], row["role"], ", emailed" if emailed else ""))
    return {"member": row, "link": None if emailed else link, "emailed": emailed}


class RoleBody(BaseModel):
    email: str
    role: str


@router.put("/publications/{publication_id}/members")
def change_role(publication_id: int, body: RoleBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    writers.set_role(publication_id, body.email, body.role, staff.actor_label(person))
    _audit(request, person, "member_role", "%s in %s: %s" % (body.email, pub["base"], body.role))
    return {"members": writers.member_list(publication_id), "manage": True}


@router.delete("/publications/{publication_id}/members")
def remove(publication_id: int, request: Request, email: str = ""):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    writers.remove(publication_id, email)
    _audit(request, person, "member_removed", "%s from %s" % (email, pub["base"]))
    return {"members": writers.member_list(publication_id), "manage": True}


class LinkBody(BaseModel):
    email: str


@router.post("/publications/{publication_id}/members/link")
def fresh_link(publication_id: int, body: LinkBody, request: Request):
    """A new sign-in link for a member who lost theirs. It cancels the old one."""
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    email = staff.normalise_email(body.email)
    if not any(m["email"] == email for m in writers.member_list(publication_id)):
        raise HTTPException(404, "%s does not write here." % email)
    link = writers.signin_link(request, email)
    if link is None:
        raise HTTPException(400, "Email sign-in is switched off on this server.")
    emailed = writers.email_link(request, "link", email, link, pub, None, person)
    _audit(request, person, "member_link", "%s in %s%s" % (email, pub["base"],
                                                         ", emailed" if emailed else ""))
    return {"link": None if emailed else link, "emailed": emailed, "email": email}


# ---------------------------------------------------- assistant settings
#
# Each publication's own skills, house style and connectors (app/skills.py,
# app/connectors.py): what the AI tab uses on its articles. Every member sees
# them; editors change the skills and the house style; only owners change the
# connectors, which decide what a run can reach and carry the publication's
# keys. Every change goes into the audit trail with the publication's address.

def _assistant(pub, person):
    pid = pub["id"]
    return {"publication_id": pid,
            "skills": skills.list_skills(publication_id=pid),
            "house_style": skills.house_style(pid),
            "default_house_style": skills.HOUSE_STYLE,
            "connectors": connectors.list_connectors(publication_id=pid),
            "can_edit": writers.at_least(person, pid, "editor"),
            "can_manage": writers.at_least(person, pid, "owner")}


@router.get("/publications/{publication_id}/assistant")
def assistant(publication_id: int, request: Request):
    person = writers.require_writer(request)
    return _assistant(_need(person, publication_id, "writer"), person)


class SkillBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    enabled: Optional[bool] = None
    writes: Optional[bool] = None


@router.post("/publications/{publication_id}/assistant/skills")
def skill_create(publication_id: int, body: SkillBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    try:
        sk = skills.create(body.name, body.description, body.instructions, staff.actor_label(person),
                           True if body.writes is None else body.writes, publication_id=pub["id"])
    except skills.SkillError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_skill_created", "%s in %s" % (sk["name"], pub["base"]))
    return sk


@router.put("/publications/{publication_id}/assistant/skills/{skill_id}")
def skill_update(publication_id: int, skill_id: int, body: SkillBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    try:
        sk = skills.update(skill_id, staff.actor_label(person), body.name, body.description,
                           body.instructions, body.enabled, body.writes, publication_id=pub["id"])
    except skills.SkillError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_skill_updated", "%s in %s" % (sk["name"], pub["base"]))
    return sk


@router.delete("/publications/{publication_id}/assistant/skills/{skill_id}")
def skill_delete(publication_id: int, skill_id: int, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    sk = skills.get(skill_id, pub["id"])
    if sk is None:
        raise HTTPException(404, "No such skill")
    skills.delete(skill_id, pub["id"])
    _audit(request, person, "ai_skill_deleted", "%s in %s" % (sk["name"], pub["base"]))
    return {"ok": True}


class StyleBody(BaseModel):
    text: str


@router.put("/publications/{publication_id}/assistant/house-style")
def house_style(publication_id: int, body: StyleBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    try:
        text = skills.set_house_style(body.text, pub["id"])
    except skills.SkillError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_house_style", "%d characters in %s" % (len(text), pub["base"]))
    return {"house_style": text}


class ConnectorBody(BaseModel):
    label: Optional[str] = None
    url: Optional[str] = None
    auth_kind: Optional[str] = None
    header_name: Optional[str] = None
    key: Optional[str] = None
    enabled: Optional[bool] = None


class ToolBody(BaseModel):
    tool: str
    on: bool


def _connector(pub, connector_id):
    c = connectors.get(connector_id, publication_id=pub["id"])
    if c is None:
        raise HTTPException(404, "No such connector")
    return c


@router.post("/publications/{publication_id}/assistant/connectors")
def connector_create(publication_id: int, body: ConnectorBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    try:
        c = connectors.create(body.label, body.url, body.auth_kind or "none", body.header_name,
                              (body.key or "").strip() or None, staff.actor_label(person),
                              publication_id=pub["id"])
    except connectors.ConnectorError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_connector_added", "%s %s in %s" % (c["label"], c["url"], pub["base"]))
    return c


@router.put("/publications/{publication_id}/assistant/connectors/{connector_id}")
def connector_update(publication_id: int, connector_id: int, body: ConnectorBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    _connector(pub, connector_id)
    try:
        c = connectors.update(connector_id, staff.actor_label(person), body.label, body.url,
                              body.auth_kind, body.header_name, (body.key or "").strip() or None,
                              body.enabled, publication_id=pub["id"])
    except connectors.ConnectorError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_connector_updated", "%s in %s" % (c["label"], pub["base"]))
    return c


@router.post("/publications/{publication_id}/assistant/connectors/{connector_id}/check")
def connector_check(publication_id: int, connector_id: int, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    _connector(pub, connector_id)
    try:
        return connectors.check(connector_id, publication_id=pub["id"])
    except connectors.ConnectorError as exc:
        raise HTTPException(400, str(exc))


@router.put("/publications/{publication_id}/assistant/connectors/{connector_id}/tools")
def connector_tool(publication_id: int, connector_id: int, body: ToolBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    _connector(pub, connector_id)
    try:
        c = connectors.set_tool(connector_id, body.tool, body.on, publication_id=pub["id"])
    except connectors.ConnectorError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "ai_connector_tool", "%s in %s: %s %s" % (
        c["label"], pub["base"], body.tool, "on" if body.on else "off"))
    return c


@router.delete("/publications/{publication_id}/assistant/connectors/{connector_id}")
def connector_delete(publication_id: int, connector_id: int, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    c = _connector(pub, connector_id)
    connectors.delete(connector_id, publication_id=pub["id"])
    _audit(request, person, "ai_connector_removed", "%s in %s" % (c["label"], pub["base"]))
    return {"ok": True}


# ------------------------------------------------------------------ keys

def _mcp_url(request):
    base = seo.SITE_BASE_URL if seo.is_https(request) else str(request.base_url).rstrip("/")
    return base + "/mcp/research"


@router.get("/keys")
def my_keys(request: Request):
    person = writers.require_writer(request)
    return {"keys": staff.list_keys(person["id"]), "endpoint": _mcp_url(request)}


class KeyBody(BaseModel):
    label: str = ""


@router.post("/keys")
def make_key(body: KeyBody, request: Request):
    person = writers.require_writer(request)
    try:
        made = staff.create_key(person["id"], body.label)
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "key_created", made["label"])
    made["endpoint"] = _mcp_url(request)
    return made


@router.delete("/keys/{key_id}")
def drop_key(key_id: int, request: Request):
    person = writers.require_writer(request)
    try:
        staff.revoke_key(person["id"], key_id)
    except staff.StaffError as exc:
        raise HTTPException(404, str(exc))
    _audit(request, person, "key_revoked", "key %d" % key_id)
    return {"revoked": key_id}


# ----------------------------------------------------------- data sources
# The writer's own market-data accounts (app/assistant/market_data.py). They
# belong to the person's sign-in account, so the same connection shows on
# their Investment Assistant desk, and only their own runs can use it.

def _own_person(request):
    person = writers.require_writer(request)
    if person.get("shared") or not person.get("email"):
        raise HTTPException(403, "Sign in with your own account to connect a data source: "
                                 "its key is personal.")
    return person


def _own_account(request):
    """The signed-in writer's own account (by email), made on first use."""
    person = writers.require_writer(request)
    if person.get("shared") or not person.get("email"):
        raise HTTPException(403, "Sign in with your own account to see your memory: it is personal.")
    return market_data.account_for(person["email"], create=True)


# what the writer's assistant remembers about them (shared with the Investment Assistant)
memory.mount(router, _own_account)


# ----------------------------------------------------------- writing style
# How the writer writes (app/writing_style.py): theirs, in every publication.

class StyleBody(BaseModel):
    text: str = ""
    samples: list = []


def _style_person(request):
    person = writers.require_writer(request)
    if person.get("shared") or not person.get("id"):
        raise HTTPException(403, "Sign in with your own account to set your writing style: "
                                 "it is personal.")
    return person


@router.get("/style")
def my_style(request: Request):
    return writing_style.view(_style_person(request))


@router.put("/style")
def save_style(body: StyleBody, request: Request):
    person = _style_person(request)
    try:
        out = writing_style.save(person, body.text, body.samples)
    except writing_style.StyleError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "writing_style_saved", "%d samples" % len(out["samples"]))
    return out


class SourceBody(BaseModel):
    provider: str = ""
    key: str = ""


class SourceUpdate(BaseModel):
    enabled: bool = True


class SourceTry(BaseModel):
    symbol: str = ""
    start: str = ""
    end: str = ""


def _source(request, source_id):
    person = _own_person(request)
    aid = market_data.account_for(person["email"])
    src = ia_store.data_source(aid, source_id) if aid else None
    if src is None:
        raise HTTPException(404, "No such data source.")
    return person, aid, src


@router.get("/data-sources")
def my_sources(request: Request):
    person = _own_person(request)
    return market_data.view(market_data.account_for(person["email"]))


@router.post("/data-sources")
def connect_source(body: SourceBody, request: Request):
    person = _own_person(request)
    aid = market_data.account_for(person["email"], create=True)
    try:
        src = market_data.connect(aid, (body.provider or "").strip().lower(), body.key)
    except market_data.SourceError as exc:
        raise HTTPException(503 if "ASSISTANT_SECRET" in str(exc) else 400, str(exc))
    _audit(request, person, "data_source_connected", src["provider"])
    return src


@router.patch("/data-sources/{source_id}")
def switch_source(source_id: int, body: SourceUpdate, request: Request):
    person, aid, src = _source(request, source_id)
    return ia_store.update_data_source(aid, source_id, enabled=1 if body.enabled else 0)


@router.post("/data-sources/{source_id}/try")
def try_source(source_id: int, body: SourceTry, request: Request):
    person, aid, src = _source(request, source_id)
    try:
        return market_data.try_source(aid, src, body.symbol, body.start, body.end)
    except market_data.SourceError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/data-sources/{source_id}")
def drop_source(source_id: int, request: Request):
    person, aid, src = _source(request, source_id)
    ia_store.delete_data_source(aid, source_id)
    _audit(request, person, "data_source_removed", src["provider"])
    return {"ok": True}


# ------------------------------------------------------ publication profile
# What makes a publication its own workspace (app/pub_profile.py): its AI key,
# article defaults, look and market-data keys. Members see it; owners change
# it, except article defaults, which editors set like the house style.

def _profile(pid, person):
    out = pub_profile.view(pid)
    out["data"] = market_data.view(ia_store.pub_owner(pid))
    out["role"] = writers.role(person, pid)
    from .research_ai import LENGTHS
    out["lengths"] = [{"key": x["key"], "label": x["label"], "hint": x["hint"]} for x in LENGTHS]
    out["skills"] = [{"id": k["id"], "name": k["name"]} for k in skills.list_skills(True, pid)]
    return out


def _profile_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except pub_profile.ProfileError as exc:
        raise HTTPException(400, str(exc))


@router.get("/publications/{publication_id}/profile")
def get_profile(publication_id: int, request: Request):
    person = writers.require_writer(request)
    _need(person, publication_id, "writer")
    return _profile(publication_id, person)


class ProfileAI(BaseModel):
    provider: str = ""
    model: str = ""
    key: str = ""


@router.put("/publications/{publication_id}/profile/ai")
def set_profile_ai(publication_id: int, body: ProfileAI, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    _profile_call(pub_profile.set_ai, publication_id, body.provider, body.model, body.key)
    _audit(request, person, "publication_ai_set", "%s in %s" % (body.provider, pub["base"]))
    return _profile(publication_id, person)


@router.delete("/publications/{publication_id}/profile/ai")
def clear_profile_ai(publication_id: int, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    pub_profile.clear_ai(publication_id)
    _audit(request, person, "publication_ai_removed", pub["base"])
    return _profile(publication_id, person)


class ProfileDefaults(BaseModel):
    length: str = ""
    template: str = ""
    default_skill: Optional[int] = None


@router.put("/publications/{publication_id}/profile/defaults")
def set_profile_defaults(publication_id: int, body: ProfileDefaults, request: Request):
    person = writers.require_writer(request)
    _need(person, publication_id, "editor")
    from .research_ai import LENGTHS
    _profile_call(pub_profile.set_defaults, publication_id, body.length, body.template,
                  body.default_skill, lengths=[x["key"] for x in LENGTHS])
    return _profile(publication_id, person)


class ProfileLook(BaseModel):
    accent: str = "navy"
    logo: str = ""


@router.put("/publications/{publication_id}/profile/look")
def set_profile_look(publication_id: int, body: ProfileLook, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    _profile_call(pub_profile.set_look, publication_id, body.accent, body.logo)
    _audit(request, person, "publication_look_set", pub["base"])
    return _profile(publication_id, person)


def _pub_source(request, publication_id, source_id):
    person = writers.require_writer(request)
    _need(person, publication_id, "owner")
    owner = ia_store.pub_owner(publication_id)
    src = ia_store.data_source(owner, source_id)
    if src is None:
        raise HTTPException(404, "No such data source.")
    return person, owner, src


@router.get("/publications/{publication_id}/data-sources")
def pub_sources(publication_id: int, request: Request):
    person = writers.require_writer(request)
    _need(person, publication_id, "writer")
    out = market_data.view(ia_store.pub_owner(publication_id))
    out["can_manage"] = writers.at_least(person, publication_id, "owner")
    return out


@router.post("/publications/{publication_id}/data-sources")
def connect_pub_source(publication_id: int, body: SourceBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "owner")
    try:
        src = market_data.connect(ia_store.pub_owner(publication_id),
                                  (body.provider or "").strip().lower(), body.key)
    except market_data.SourceError as exc:
        raise HTTPException(503 if "ASSISTANT_SECRET" in str(exc) else 400, str(exc))
    _audit(request, person, "data_source_connected", "%s for %s" % (src["provider"], pub["base"]))
    return src


@router.patch("/publications/{publication_id}/data-sources/{source_id}")
def switch_pub_source(publication_id: int, source_id: int, body: SourceUpdate, request: Request):
    person, owner, src = _pub_source(request, publication_id, source_id)
    return ia_store.update_data_source(owner, source_id, enabled=1 if body.enabled else 0)


@router.post("/publications/{publication_id}/data-sources/{source_id}/try")
def try_pub_source(publication_id: int, source_id: int, body: SourceTry, request: Request):
    person, owner, src = _pub_source(request, publication_id, source_id)
    try:
        return market_data.try_source(owner, src, body.symbol, body.start, body.end)
    except market_data.SourceError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/publications/{publication_id}/data-sources/{source_id}")
def drop_pub_source(publication_id: int, source_id: int, request: Request):
    person, owner, src = _pub_source(request, publication_id, source_id)
    ia_store.delete_data_source(owner, source_id)
    _audit(request, person, "data_source_removed", "%s for publication %d" % (src["provider"], publication_id))
    return {"ok": True}


# ----------------------------------------------------------------- coverage
# The publication's coverage list (app/pub_coverage.py).

@router.get("/companies")
def find_companies(request: Request, q: str = ""):
    writers.require_writer(request)
    from .assistant.api import find_companies as find
    return {"companies": find(q, limit=8)}


@router.get("/publications/{publication_id}/coverage")
def get_coverage(publication_id: int, request: Request):
    person = writers.require_writer(request)
    _need(person, publication_id, "writer")
    out = pub_coverage.view(publication_id)
    out["can_edit"] = writers.at_least(person, publication_id, "editor")
    return out


class CoverageBody(BaseModel):
    code: str = ""


@router.post("/publications/{publication_id}/coverage")
def add_coverage(publication_id: int, body: CoverageBody, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    from .assistant.api import _company
    code = (body.code or "").strip().upper()
    name = _company(code)[0] if code else None
    try:
        pub_coverage.add(publication_id, code, name)
    except pub_coverage.CoverageError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, person, "coverage_added", "%s in %s" % (code, pub["base"]))
    return get_coverage(publication_id, request)


@router.delete("/publications/{publication_id}/coverage/{code}")
def remove_coverage(publication_id: int, code: str, request: Request):
    person = writers.require_writer(request)
    pub = _need(person, publication_id, "editor")
    pub_coverage.remove(publication_id, code)
    _audit(request, person, "coverage_removed", "%s in %s" % (code.upper(), pub["base"]))
    return get_coverage(publication_id, request)
