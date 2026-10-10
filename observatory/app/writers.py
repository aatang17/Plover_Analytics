# -*- coding: utf-8 -*-
"""Who is writing, and what they may do in each publication.

Who
---
A writer is a person in ``app/staff.py`` — the same record that holds their
AI settings, personal keys and Google link. They reach the desk one of two
ways, and both land on that record:

* an **email sign-in link** (``app/accounts.py``): the address is looked up
  in staff. Writers invited to a publication sign in this way and never see
  the admin console.
* a **staff password session** (the admin console's login): how the Plover
  team has always signed in.

The shared admin password is never a writer: it is nobody, and an article's
history must say who wrote it.

Roles
-----
Each publication has members with one role:

``owner``   everything an editor can, plus the publication's settings and its
            members
``editor``  write, edit, publish, withdraw and reinstate any of its articles
``writer``  write and preview their own articles (started by them or crediting
            them); ask an editor to publish. Cannot publish.

PloverResearch (publication 1) also counts the admin console's Writing
permission: Writing makes a person its editor, Writing with Team its owner.
That keeps the Team page working as before; explicit membership only ever
adds to it.
"""
from fastapi import HTTPException

from . import accounts, research, staff

RANK = {"writer": 1, "editor": 2, "owner": 3}
ROLE_LABEL = {"owner": "Owner", "editor": "Editor", "writer": "Writer"}
WRITE_PAGE = "write.html"


# ---------------------------------------------------------------- identity

def current_writer(request):
    """The signed-in person (staff record), or None."""
    from .admin_api import current_person, STAFF_COOKIE
    if request.cookies.get(STAFF_COOKIE):
        person = current_person(request)
        if person is not None and not person.get("shared"):
            return person
    account = accounts.current_account(request)
    if account is not None:
        person = staff.get_by_email(account["email"])
        if person is not None and person["status"] == "active":
            return person
    return None


def signed_in_email(request):
    """An email-link session's address, even when it has no writer record."""
    account = accounts.current_account(request)
    return account["email"] if account else None


def require_writer(request):
    """The signed-in person with at least one publication, or 401/403."""
    person = current_writer(request)
    if person is None:
        from .admin_api import current_person
        shared = current_person(request)
        if shared is not None and shared.get("shared"):
            raise HTTPException(403, "The shared admin password is nobody, and every article "
                                     "says who wrote it. Sign in with your own account to write.")
        raise HTTPException(401, "Not signed in")
    if not roles(person):
        raise HTTPException(403, "You are not a member of any publication yet. Ask the owner "
                                 "of the publication you write for to invite you.")
    return person


# ------------------------------------------------------------------- roles

def _implied_home(person):
    perms = person.get("permissions") or []
    if "writing" not in perms:
        return None
    return "owner" if "team" in perms else "editor"


def _better(a, b):
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def role(person, publication_id):
    """The person's role in a publication, or None."""
    best = research.member_role(publication_id, person.get("email"))
    if publication_id == research.HOME:
        best = _better(_implied_home(person), best)
    return best


def roles(person):
    """{publication id: role} for everywhere the person can write."""
    out = research.memberships(person.get("email"))
    implied = _implied_home(person)
    if implied:
        out[research.HOME] = _better(implied, out.get(research.HOME))
    return dict((k, v) for k, v in out.items() if v)


def at_least(person, publication_id, needed):
    return RANK.get(role(person, publication_id), 0) >= RANK[needed]


def _own(person, article):
    return (article.get("created_by") == person.get("email")
            or person.get("id") in (article.get("authors") or []))


def can(person, action, article):
    """Whether the person may do ``action`` to an article (a summary or full
    article from app/research.py). Actions: view, edit, publish (also
    withdraw and reinstate), delete."""
    r = role(person, article["publication_id"])
    if r is None:
        return False
    if RANK[r] >= RANK["editor"]:
        return True
    if action in ("view", "edit"):
        return _own(person, article)
    if action == "delete":
        return article.get("created_by") == person.get("email") and not article.get("published_version")
    return False


def require(person, action, article):
    if not can(person, action, article):
        if action in ("view", "edit") or role(person, article["publication_id"]) is None:
            # say nothing about an article the person may not see
            raise HTTPException(404, "No such article")
        if action == "publish":
            raise HTTPException(403, "Writers cannot publish. Use Ask to Publish and an editor "
                                     "of the publication will publish it.")
        raise HTTPException(403, "Only an editor of the publication can do that.")


def permissions_for(person, article):
    """What the editor should offer on this article."""
    return dict((a, can(person, a, article)) for a in ("edit", "publish", "delete"))


def opens_for_others(person):
    """The Plover team opens a publication for someone it invites, as owner."""
    return "team" in (person.get("permissions") or [])


def can_create_publications(person):
    """Every writer may have a publication of their own: someone who owns
    none yet may open one, as its owner. The Plover team may open any number,
    for anyone."""
    if opens_for_others(person):
        return True
    return "owner" not in research.memberships(person.get("email")).values()


def own_publication(person):
    """The publication that is the person's own: the first they own, else the
    first they write for, else PloverResearch. A run with no article uses its
    Assistant Settings."""
    mine = roles(person)
    owned = sorted(pid for pid, r in mine.items() if r == "owner" and pid != research.HOME)
    if owned:
        return owned[0]
    others = sorted(pid for pid in mine if pid != research.HOME)
    if research.HOME in mine or not others:
        return research.HOME
    return others[0]


# --------------------------------------------------------------- sign-in

def may_sign_in(email):
    """Admits a writer past ACCOUNTS_ALLOWED_EMAILS (see accounts.EXTRA_ALLOWED)."""
    if research.memberships(email):
        return True
    person = staff.get_by_email(email)
    return bool(person and person["status"] == "active" and _implied_home(person))


if may_sign_in not in accounts.EXTRA_ALLOWED:
    accounts.EXTRA_ALLOWED.append(may_sign_in)


def _public_base(request):
    """The site's address, ending in '/'. Behind the platform's proxy the
    request reads http and an inside host; links must carry the public https
    address (as the setup links do)."""
    import os
    from . import seo
    if os.environ.get("PUBLIC_BASE_URL"):
        return accounts.base_url(request)
    if seo.is_https(request):
        return seo.SITE_BASE_URL + "/"
    return str(request.base_url)


def signin_link(request, email):
    """A single-use 72-hour sign-in link, or None when email sign-in is
    switched off on this server."""
    if not accounts.enabled():
        return None
    return accounts.issue_link(email, WRITE_PAGE, _public_base(request))


_A_ROLE = {"owner": "an owner", "editor": "an editor", "writer": "a writer"}


def email_link(request, kind, email, link, pub, role, sender):
    """Email a sign-in link made by ``signin_link``. ``kind`` is ``invite``
    (a new member), ``owner`` (a publication opened for them) or ``link`` (a
    fresh link for a member). ``sender`` is the person sending it; replies go
    to them, since the site's own address has no mailbox.

    True when it was emailed. False means the caller shows the link so the
    sender can pass it on by hand: no link, no mailbox configured, or the
    provider refused it.
    """
    from . import mailer
    if not link or not mailer.configured():
        return False
    who = (sender or {}).get("name") or (sender or {}).get("email") or "The Plover team"
    name = pub["name"]
    where = name if pub.get("home") else name + " on PloverResearch"
    if kind == "link":
        subject = "Your sign-in link for %s" % name
        paras = ["%s sent you a new sign-in link for %s." % (who, where),
                 "It works once, within 72 hours, and replaces any earlier link."]
    elif kind == "owner":
        subject = "%s opened %s for you" % (who, name)
        paras = ["%s has opened %s for you. You are its owner: you publish its articles, "
                 "run its settings and invite its writers." % (who, where),
                 "This link signs you in and opens the Writer Desk. It works once, within 72 hours."]
    else:
        subject = "%s invited you to write for %s" % (who, name)
        paras = ["%s has invited you to write for %s, as %s." % (who, where, _A_ROLE[role]),
                 "This link signs you in and opens the Writer Desk. It works once, within 72 hours."]
    after = ["To sign in again later, go to %s%s and enter this email address. We email you a "
             "new link each time; there is no password." % (_public_base(request), WRITE_PAGE)]
    reply_to = (sender or {}).get("email")
    if reply_to:
        after.append("Reply to this email to reach %s." % who)
    return mailer.send_link(email, subject, paras, link, "Open the Writer Desk", after,
                            reply_to=reply_to)


# ---------------------------------------------------------------- members

def _last_seen(person, email):
    """The later of a password sign-in and an email-link session's last use."""
    times = [t for t in ((person or {}).get("last_login_at"), accounts.last_seen(email)) if t]
    return max(times) if times else None


def member_list(publication_id):
    """Everyone who can write in a publication, with where their role comes
    from: ``member`` (managed here) or ``team`` (the admin console's Team
    page, PloverResearch only)."""
    rows = {}
    for m in research.members(publication_id):
        p = staff.get_by_email(m["email"])
        rows[m["email"]] = {"email": m["email"], "name": p["name"] if p else m["email"],
                            "role": m["role"], "source": "member", "added_at": m["added_at"],
                            "active": bool(p and p["status"] == "active"),
                            "last_login_at": _last_seen(p, m["email"]),
                            "id": p["id"] if p else None}
    if publication_id == research.HOME:
        for p in staff.list_all():
            implied = _implied_home(p) if p["status"] == "active" else None
            if not implied:
                continue
            row = rows.get(p["email"])
            if row is None or RANK[implied] >= RANK[row["role"]]:
                rows[p["email"]] = {"email": p["email"], "name": p["name"], "role": implied,
                                    "source": "team", "added_at": p["created_at"], "active": True,
                                    "last_login_at": _last_seen(p, p["email"]), "id": p["id"]}
    order = sorted(rows.values(), key=lambda r: (-RANK[r["role"]], r["name"].lower()))
    return order


def _owners_after(publication_id, email, new_role):
    """How many owners a publication would have if ``email`` had ``new_role``
    (None = removed)."""
    count = 0
    for m in member_list(publication_id):
        r = new_role if m["email"] == email and m["source"] == "member" else m["role"]
        if r == "owner":
            count += 1
    return count


def set_role(publication_id, email, new_role, actor):
    email = staff.normalise_email(email)
    if research.member_role(publication_id, email) is None:
        raise HTTPException(404, "%s is not a member here." % email)
    if new_role not in RANK:
        raise HTTPException(400, "A role is Owner, Editor or Writer.")
    if publication_id != research.HOME and _owners_after(publication_id, email, new_role) < 1:
        raise HTTPException(400, "A publication needs at least one owner. Make someone else "
                                 "an owner first.")
    research.set_member(publication_id, email, new_role, actor)


def remove(publication_id, email):
    email = staff.normalise_email(email)
    if research.member_role(publication_id, email) is None:
        raise HTTPException(404, "%s is not a member here. People who write through the "
                                 "Team page's Writing permission are changed there." % email)
    if publication_id != research.HOME and _owners_after(publication_id, email, None) < 1:
        raise HTTPException(400, "A publication needs at least one owner. Make someone else "
                                 "an owner first.")
    research.remove_member(publication_id, email)


def invite(request, publication_id, email, name, new_role, actor):
    """Add a person to a publication. Returns (member row, sign-in link)."""
    if new_role not in RANK:
        raise HTTPException(400, "A role is Owner, Editor or Writer.")
    try:
        person = staff.ensure_person(email, name, actor)
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    current = research.member_role(publication_id, person["email"])
    if current is not None:
        raise HTTPException(400, "%s is already a member, as %s." % (
            person["email"], ROLE_LABEL[current].lower()))
    research.set_member(publication_id, person["email"], new_role, actor)
    row = [m for m in member_list(publication_id) if m["email"] == person["email"]][0]
    return row, signin_link(request, person["email"])
