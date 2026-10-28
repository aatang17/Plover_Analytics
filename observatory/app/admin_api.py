"""Internal admin API: login, team, ingest health, release history, audit log.

Everything here is read-only against the DuckDB store — the one-writer rule
(see CLAUDE.md, Ingest Guardrails 5) holds: the serving process never writes
to the database. What the admin surface writes lives beside it on the volume:
its append-only audit trail (JSONL under data/admin/), the staff accounts
(app/staff.py, SQLite) and the party curation file.

Auth is one login per person (app/staff.py): an email, a password and a set
of permissions — team, operations, classification, writing — checked on every
endpoint. Sessions are stored server side, so a deploy does not sign anyone
out. The shared ADMIN_PASSWORD still signs in, as "Shared password" with
every permission, so a deployment with no accounts yet can create its first
one; its cookie is signed with a per-boot secret exactly as before. With
neither a shared password nor any account the whole surface answers 503.

Paths live under /admin/api on purpose: the response cache only touches GETs
under /api/v1, so nothing authenticated can ever be served from cache.
"""
import datetime
import hashlib
import hmac
import json
import os
import time

from typing import Optional

import duckdb

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import accounts, db, filer_labels, mailer, parties, registry, seo, staff, vintages, visits
from .api import ADAPTERS, health
from .equity_api import DB_PATH as EQUITY_DB_PATH

router = APIRouter(prefix="/admin/api", include_in_schema=False)

ADMIN_DIR = db.DATA_DIR / "admin"
AUDIT_PATH = ADMIN_DIR / "audit.jsonl"

COOKIE = "obs_admin"            # shared-password session (per-boot signature)
STAFF_COOKIE = "obs_staff"      # a person's session (stored, survives restarts)
SESSION_HOURS = 12
# Signing secret for the shared-password cookie. Per boot unless
# ADMIN_SESSION_SECRET is set: where a deploy runs the old and new copies of
# the site side by side (the DigitalOcean server), both must accept the same
# cookie, or every shared-password login ends at each deploy.
_SECRET = (hashlib.sha256(os.environ["ADMIN_SESSION_SECRET"].encode("utf-8")).digest()
           if os.environ.get("ADMIN_SESSION_SECRET", "").strip() else os.urandom(32))

# Failed sign-ins per IP and per email, sliding window — same in-process
# shape as the /ask rate limit: it bounds one worker, which is the whole
# deployment. Only failures count: two colleagues behind one office address
# signing in, then one typo, must not lock the office out.
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_PER_WINDOW = 5
_LOGIN_HITS = {}

PERMISSION_LABELS = dict(staff.PERMISSIONS)


def _password():
    value = os.environ.get("ADMIN_PASSWORD", "").strip()
    return value or None


def _enabled():
    return _password() is not None or staff.count() > 0


def _sign(expiry):
    mac = hmac.new(_SECRET, str(expiry).encode("ascii"), hashlib.sha256)
    return "%d.%s" % (expiry, mac.hexdigest())


def _token_valid(token):
    if not token or "." not in token:
        return False
    expiry_part = token.split(".", 1)[0]
    if not expiry_part.isdigit():
        return False
    expiry = int(expiry_part)
    if expiry < time.time():
        return False
    return hmac.compare_digest(_sign(expiry), token)


def current_person(request):
    """Who is signed in, or None: a person's session first, then the shared
    password's (which stops working the moment ADMIN_PASSWORD is removed)."""
    person = staff.session_person(request.cookies.get(STAFF_COOKIE))
    if person is not None:
        return person
    if _password() is not None and _token_valid(request.cookies.get(COOKIE)):
        return staff.shared_person()
    return None


def _require_admin(request, permission=None):
    """The signed-in person, or a 503/401/403 that says which."""
    if not _enabled():
        raise HTTPException(503, "Admin is disabled: no accounts and no ADMIN_PASSWORD")
    person = current_person(request)
    if person is None:
        raise HTTPException(401, "Not signed in")
    if permission and permission not in person["permissions"]:
        raise HTTPException(
            403, "Your account does not have the %s permission. Ask someone on the "
                 "team to add it." % PERMISSION_LABELS.get(permission, permission))
    return person


def _client_ip(request):
    return request.client.host if request.client else "unknown"


def _blocked(*keys):
    """True when any key has used up its failures in the window."""
    now = time.monotonic()
    cutoff = now - LOGIN_WINDOW_SECONDS
    for stale in [k for k, hits in _LOGIN_HITS.items() if not hits or hits[-1] < cutoff]:
        del _LOGIN_HITS[stale]
    for key in keys:
        if key and len([t for t in _LOGIN_HITS.get(key, []) if t >= cutoff]) >= LOGIN_MAX_PER_WINDOW:
            return True
    return False


def _failed(*keys):
    now = time.monotonic()
    for key in keys:
        if key:
            _LOGIN_HITS.setdefault(key, []).append(now)


# --- audit trail -------------------------------------------------------------

def audit(action, detail, ip, by=None):
    """Append one action to the audit trail. Never raises: an unwritable log
    must not take the admin surface down, but it is loudly reported."""
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc)
              .replace(tzinfo=None).isoformat() + "Z",
        "action": action,
        "detail": detail,
        "ip": ip,
        "by": by,
    }
    try:
        ADMIN_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(AUDIT_PATH), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("AUDIT WRITE FAILED (%s): %s" % (exc, entry))


def _read_audit(limit):
    if not AUDIT_PATH.exists():
        return []
    entries = []
    with open(str(AUDIT_PATH), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                entries.append({"at": None, "action": "unparseable_entry",
                                "detail": line[:200], "ip": None})
    return entries[-limit:][::-1]


# --- session -----------------------------------------------------------------

class LoginBody(BaseModel):
    email: str = ""
    password: str


def _person_payload(person):
    return {"id": person["id"], "name": person["name"], "email": person["email"],
            "permissions": person["permissions"], "shared": bool(person.get("shared"))}


def _set_staff_cookie(request, response, token):
    response.set_cookie(
        STAFF_COOKIE, token, max_age=staff.SESSION_MAX_SECONDS, path="/admin",
        httponly=True, samesite="strict", secure=seo.is_https(request))
    _mark_team(request, response)


def _mark_team(request, response):
    """Marks this browser as the team's on every page, so the visit counter
    leaves it out (visits.TEAM_COOKIE). Kept after signing out."""
    response.set_cookie(
        visits.TEAM_COOKIE, "1", max_age=visits.TEAM_MAX_AGE, path="/",
        httponly=True, samesite="lax", secure=seo.is_https(request))


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    if not _enabled():
        raise HTTPException(503, "Admin is disabled: no accounts and no ADMIN_PASSWORD")
    ip = _client_ip(request)
    email = staff.normalise_email(body.email)
    email_key = "email:" + email if email else None
    if _blocked(ip, email_key):
        audit("login_locked_out", "too many attempts" + (" for " + email if email else ""), ip)
        raise HTTPException(429, "Too many attempts — wait a few minutes")
    if email:
        person = staff.authenticate(email, body.password)
        if person is None:
            _failed(ip, email_key)
            audit("login_failed", "wrong email or password (%s)" % email, ip)
            raise HTTPException(401, "Wrong email or password")
        _set_staff_cookie(request, response, staff.start_session(person["id"], ip))
        response.delete_cookie(COOKIE, path="/admin")
        audit("login", "signed in", ip, by=staff.actor_label(person))
        return {"ok": True, "user": _person_payload(person)}
    if _password() is None:
        raise HTTPException(401, "Enter your email address and password")
    if not hmac.compare_digest(body.password.encode("utf-8"),
                               _password().encode("utf-8")):
        _failed(ip)
        audit("login_failed", "wrong shared password", ip)
        raise HTTPException(401, "Wrong password")
    expiry = int(time.time()) + SESSION_HOURS * 3600
    # seo.is_https, not request.url.scheme: behind the platform's proxy the
    # scheme reads http on an https-only site, and the session cookie was
    # therefore never marked Secure in production.
    response.set_cookie(
        COOKIE, _sign(expiry), max_age=SESSION_HOURS * 3600, path="/admin",
        httponly=True, samesite="strict",
        secure=seo.is_https(request))
    _mark_team(request, response)
    audit("login", "signed in with the shared password", ip, by="shared password")
    expires = datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc)
    return {"ok": True, "user": _person_payload(staff.shared_person()),
            "expires_at": expires.replace(tzinfo=None).isoformat() + "Z"}


@router.post("/logout")
def logout(request: Request, response: Response):
    person = current_person(request)
    staff.end_session(request.cookies.get(STAFF_COOKIE))
    response.delete_cookie(STAFF_COOKIE, path="/admin")
    response.delete_cookie(COOKIE, path="/admin")
    if person is not None:
        audit("logout", "signed out", _client_ip(request), by=staff.actor_label(person))
    return {"ok": True}


@router.get("/session")
def session(request: Request, response: Response):
    """Lets the page decide between the login screen and the dashboard
    without a failed request in the console."""
    if not _enabled():
        return {"enabled": False, "authenticated": False}
    person = current_person(request)
    if person is not None and request.cookies.get(visits.TEAM_COOKIE) != "1":
        _mark_team(request, response)
    return {"enabled": True,
            "authenticated": person is not None,
            "user": _person_payload(person) if person else None,
            "shared_password": _password() is not None,
            "permissions": [{"key": k, "label": v} for k, v in staff.PERMISSIONS]}


# --- setup links and passwords -------------------------------------------------

class SetupCheck(BaseModel):
    token: str


class SetupBody(BaseModel):
    token: str
    password: str


@router.post("/setup/check")
def setup_check(body: SetupCheck):
    """Who a setup link is for. POST, so the token never sits in a log line."""
    person = staff.setup_target(body.token)
    if person is None:
        raise HTTPException(410, "This setup link has expired or has already been used. "
                                 "Ask for a new one.")
    return {"name": person["name"], "email": person["email"]}


@router.post("/setup")
def setup_complete(body: SetupBody, request: Request, response: Response):
    ip = _client_ip(request)
    if _blocked(ip):
        raise HTTPException(429, "Too many attempts — wait a few minutes")
    try:
        person = staff.complete_setup(body.token, body.password)
    except staff.StaffError as exc:
        if staff.setup_target(body.token) is None:
            _failed(ip)       # a guessed or spent token, not a weak password
        raise HTTPException(400, str(exc))
    _set_staff_cookie(request, response, staff.start_session(person["id"], ip))
    response.delete_cookie(COOKIE, path="/admin")
    audit("password_set", "chose a password from a setup link", ip,
          by=staff.actor_label(person))
    return {"ok": True, "user": _person_payload(person)}


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/me/password")
def change_password(body: PasswordBody, request: Request):
    person = _require_admin(request)
    if person.get("shared"):
        raise HTTPException(400, "The shared password is changed in the server settings, "
                                 "not here.")
    try:
        staff.change_password(person["id"], body.current, body.new,
                              keep_session_hash=person.get("session_hash"))
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    audit("password_changed", "changed their password", _client_ip(request),
          by=staff.actor_label(person))
    return {"ok": True}


# --- personal keys for Claude / Codex ------------------------------------------

def _mcp_url(request):
    base = seo.SITE_BASE_URL if seo.is_https(request) else str(request.base_url).rstrip("/")
    return base + "/mcp/research"


def _own_person(request):
    person = _require_admin(request, "writing")
    if person.get("shared"):
        raise HTTPException(400, "Sign in with your own account to make a key: a key acts "
                                 "as a person, and the shared password is nobody.")
    return person


@router.get("/me/keys")
def my_keys(request: Request):
    person = _own_person(request)
    return {"keys": staff.list_keys(person["id"]), "endpoint": _mcp_url(request)}


class KeyBody(BaseModel):
    label: str = ""


@router.post("/me/keys")
def make_key(body: KeyBody, request: Request):
    person = _own_person(request)
    try:
        made = staff.create_key(person["id"], body.label)
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    audit("key_created", made["label"], _client_ip(request), by=staff.actor_label(person))
    made["endpoint"] = _mcp_url(request)
    return made


@router.delete("/me/keys/{key_id}")
def drop_key(key_id: int, request: Request):
    person = _own_person(request)
    try:
        staff.revoke_key(person["id"], key_id)
    except staff.StaffError as exc:
        raise HTTPException(404, str(exc))
    audit("key_revoked", "key %d" % key_id, _client_ip(request), by=staff.actor_label(person))
    return {"revoked": key_id}


# --- team ----------------------------------------------------------------------

def _setup_url(request, token):
    # Fragment, not query: a fragment is never sent to a server, so the token
    # stays out of access logs and Referer headers.
    base = seo.SITE_BASE_URL if seo.is_https(request) else str(request.base_url).rstrip("/")
    return base + "/admin.html#setup=" + token


def _email_setup(request, person, url, sender, new):
    """Email a setup link to the person it is for. True when sent; False
    means the Team page shows the link to pass on by hand."""
    who = (sender or {}).get("name") or "The Plover team"
    if new:
        subject = "%s added you to Plover Analytics" % who
        first = ("%s has given you an account on the Plover Analytics admin console. "
                 "Choose your password to start." % who)
    else:
        subject = "Set your Plover Analytics password"
        first = ("%s sent you a link to set a new password for the Plover Analytics admin "
                 "console. Using it signs you out everywhere else." % who)
    base = url.split("/admin.html")[0]
    after = ["To sign in later, go to %s/admin.html and use this email address and your "
             "password." % base]
    reply_to = (sender or {}).get("email")
    if reply_to:
        after.append("Reply to this email to reach %s." % who)
    return mailer.send_link(person["email"], subject,
                            [first, "The link works once, within %d hours."
                             % (staff.SETUP_TTL_SECONDS // 3600)],
                            url, "Choose Your Password", after, reply_to=reply_to)


@router.get("/team")
def team_list(request: Request):
    _require_admin(request, "team")
    return {"people": staff.list_all(),
            "shared_password": _password() is not None,
            "email_delivery": mailer.configured(),
            "permissions": [{"key": k, "label": v} for k, v in staff.PERMISSIONS]}


class NewPerson(BaseModel):
    email: str
    name: str
    permissions: list


@router.post("/team")
def team_add(body: NewPerson, request: Request):
    actor = _require_admin(request, "team")
    try:
        person, token = staff.create(body.email, body.name, body.permissions,
                                     staff.actor_label(actor))
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    audit("staff_added", "%s (%s): %s" % (person["name"], person["email"],
                                          ", ".join(person["permissions"]) or "no permissions"),
          _client_ip(request), by=staff.actor_label(actor))
    url = _setup_url(request, token)
    emailed = _email_setup(request, person, url, None if actor.get("shared") else actor, True)
    return {"person": person, "setup_url": None if emailed else url, "emailed": emailed,
            "expires_hours": staff.SETUP_TTL_SECONDS // 3600}


class PersonChange(BaseModel):
    name: Optional[str] = None
    permissions: Optional[list] = None
    status: Optional[str] = None


@router.put("/team/{staff_id}")
def team_update(staff_id: int, body: PersonChange, request: Request):
    actor = _require_admin(request, "team")
    try:
        person = staff.update(staff_id, actor["id"], name=body.name,
                              permissions=body.permissions, status=body.status)
    except staff.StaffError as exc:
        raise HTTPException(400, str(exc))
    audit("staff_changed", "%s: %s, %s" % (person["email"], person["status"],
                                           ", ".join(person["permissions"]) or "no permissions"),
          _client_ip(request), by=staff.actor_label(actor))
    return {"person": person}


@router.post("/team/{staff_id}/setup-link")
def team_setup_link(staff_id: int, request: Request):
    actor = _require_admin(request, "team")
    person = staff.get(staff_id)
    if person is None:
        raise HTTPException(404, "No such person")
    if person["status"] != "active":
        raise HTTPException(400, "Enable the account before issuing a setup link")
    token = staff.issue_setup(staff_id, staff.actor_label(actor))
    url = _setup_url(request, token)
    emailed = _email_setup(request, person, url, None if actor.get("shared") else actor, False)
    audit("setup_link_issued", person["email"] + (", emailed" if emailed else ""),
          _client_ip(request), by=staff.actor_label(actor))
    return {"setup_url": None if emailed else url, "emailed": emailed,
            "expires_hours": staff.SETUP_TTL_SECONDS // 3600}


# --- ingest health -----------------------------------------------------------

# What a person picking a dataset needs: its name as the site shows it, the
# section it sits under, and its market. The manifest is the source; a dataset
# whose manifest failed validation still appears, under its code.
_SECTION_RANK = dict((sec["id"], i) for i, sec in enumerate(registry.SECTIONS))
_SECTION_LABEL = dict((sec["id"], sec["label"]) for sec in registry.SECTIONS)


def _describe(slug):
    meta = ADAPTERS[slug].DATASET
    m = registry.get(slug) or {}
    section = m.get("section")
    return {
        "name": (m.get("name") or {}).get("en") or meta.get("title") or slug,
        "section": _SECTION_LABEL.get(section, "Other"),
        "section_rank": _SECTION_RANK.get(section, len(_SECTION_RANK)),
        "market": meta.get("country"),
        "agency": meta.get("agency"),
        "frequency": meta.get("frequency"),
        "page": m.get("page"),
    }


@router.get("/overview")
def overview(request: Request):
    """The public health check, widened with what an operator needs next:
    the artifact behind each live release and its validation summary."""
    _require_admin(request, "operations")
    report = health()
    con = db.read_cursor()
    extra = {}
    for slug in ADAPTERS:
        row = con.execute(
            "SELECT r.release_id, r.label, r.validation, a.sha256, a.bytes, "
            "       a.retrieved_at, a.url "
            "FROM releases r JOIN source_artifacts a USING(artifact_id) "
            "WHERE r.dataset=? AND r.status='published'", [slug]).fetchone()
        counts = con.execute(
            "SELECT count(*), count(*) FILTER (status='rejected') "
            "FROM releases WHERE dataset=?", [slug]).fetchone()
        series_n = con.execute(
            "SELECT count(*) FILTER (active), count(*) "
            "FROM series WHERE dataset=?", [slug]).fetchone()
        last_release = con.execute(
            "SELECT max(ingested_at) FROM releases "
            "WHERE dataset=? AND status <> 'rejected'", [slug]).fetchone()[0]
        extra[slug] = _describe(slug)
        extra[slug].update({
            "release_id": row[0] if row else None,
            "label": row[1] if row else None,
            "validation": json.loads(row[2]) if row and row[2] else None,
            "artifact_sha256": row[3] if row else None,
            "artifact_bytes": row[4] if row else None,
            "artifact_retrieved_at":
                row[5].isoformat() + "Z" if row else None,
            "artifact_url": row[6] if row else None,
            "releases_total": counts[0],
            "releases_rejected": counts[1],
            "series_active": series_n[0],
            "series_total": series_n[1],
            "last_release_at":
                last_release.isoformat() + "Z" if last_release else None,
        })
    for d in report["datasets"]:
        d.update(extra.get(d["dataset"], {}))
    return report


# --- release history ---------------------------------------------------------

@router.get("/releases/{dataset}")
def releases(dataset, request: Request):
    """Every stored release of one dataset, newest first, with what each one
    changed. The counts come straight from observation_vintages, so a release
    that merely republished unchanged data shows zero — which is the truth."""
    _require_admin(request, "operations")
    if dataset not in ADAPTERS:
        raise HTTPException(404, "Unknown dataset '%s'" % dataset)
    con = db.read_cursor()
    # Newest first BY PUBLICATION, not by when we fetched it: a backfilled
    # archive is 134 releases sharing one ingest timestamp, and ordering those
    # by it would list seventeen years of history in an arbitrary order.
    at = vintages.known_at(con)
    rows = con.execute(
        "SELECT r.release_id, r.label, r.latest_period, r.ingested_at, r.status, "
        "       a.sha256, a.bytes, a.retrieved_at, "
        "       count(v.release_id) FILTER (v.value IS NOT NULL) AS recorded, "
        "       count(v.release_id) FILTER (v.value IS NULL) AS withdrawn, "
        "       " + vintages.published_col(con) + " AS published_at "
        "FROM releases r "
        "JOIN source_artifacts a USING(artifact_id) "
        "LEFT JOIN observation_vintages v USING(release_id) "
        "WHERE r.dataset=? "
        "GROUP BY 1,2,3,4,5,6,7,8,11 ORDER BY " + at + " DESC, r.release_id DESC",
        [dataset]).fetchall()
    first_release = rows[-1][0] if rows else None
    # What each release did, counted the way /changes splits it: a value for a
    # (series, period) never seen before is new, a later one revises it, and a
    # NULL withdraws it. One pass over the dataset's vintages, in publication
    # order, instead of one /changes query per release.
    v_at = vintages.known_at(con, "rv")
    did = dict((r[0], r[1:]) for r in con.execute(
        "SELECT release_id, "
        "       count(*) FILTER (value IS NOT NULL AND n = 1), "
        "       count(*) FILTER (value IS NOT NULL AND n > 1) "
        "FROM (SELECT v.release_id, v.value, row_number() OVER ("
        "        PARTITION BY v.series_id, v.period "
        "        ORDER BY " + v_at + ", v.release_id) AS n "
        "      FROM observation_vintages v "
        "      JOIN releases rv ON rv.release_id = v.release_id "
        "      WHERE rv.dataset = ?) "
        "GROUP BY release_id", [dataset]).fetchall())
    validation = dict(con.execute(
        "SELECT release_id, validation FROM releases WHERE dataset=?",
        [dataset]).fetchall())
    out = []
    for i, r in enumerate(rows):
        # The release before this one in publication order, skipping rejected
        # ones: what "added August 2026" is measured against.
        prior = next((p for p in rows[i + 1:] if p[4] != "rejected"), None)
        new_n, revised_n = did.get(r[0], (0, 0))
        checks = validation.get(r[0])
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except ValueError:
                checks = None
        out.append({
            "new_values": new_n,
            "revised": revised_n,
            "previous_period": prior[2].isoformat() if prior else None,
            "validation": checks if isinstance(checks, dict) else None,
            "release_id": r[0], "label": r[1],
            "latest_period": r[2].isoformat(),
            "ingested_at": r[3].isoformat() + "Z",
            "status": r[4],
            "sha256": r[5], "bytes": r[6],
            "retrieved_at": r[7].isoformat() + "Z",
            "recorded": r[8], "withdrawn": r[9],
            "published_at": r[10].isoformat() + "Z" if r[10] else None,
            "known_at": (r[10] or r[3]).isoformat() + "Z",
            "known_at_basis": "agency publication" if r[10] else "our fetch",
            "is_first_vintage": r[0] == first_release,
        })
    return {"dataset": dataset, "releases": out}


CHANGES_CAP = 500


@router.get("/releases/{dataset}/{release_id}/changes")
def release_changes(dataset, release_id: int, request: Request):
    """What one release did, split the way an operator reads it: values that
    REVISED an earlier vintage (with the prior value alongside), values for
    new periods, and withdrawals. Lists are capped; the counts never are."""
    _require_admin(request, "operations")
    if dataset not in ADAPTERS:
        raise HTTPException(404, "Unknown dataset '%s'" % dataset)
    con = db.read_cursor()
    owner = con.execute("SELECT dataset FROM releases WHERE release_id=?",
                        [release_id]).fetchone()
    if owner is None or owner[0] != dataset:
        raise HTTPException(404, "No release %d in dataset '%s'" % (release_id, dataset))

    # Each vintage row of this release, with the value previously in force:
    # the newest earlier vintage row for the same (series, period).
    # "Earlier" means earlier in PUBLICATION order. Comparing ingest times
    # would make every backfilled release look like it revised nothing, since
    # the whole archive was fetched in one afternoon.
    p_at, v_at = vintages.known_at(con, "rp"), vintages.known_at(con, "rv")
    rows = con.execute(
        "SELECT s.code, s.name_en, v.period, v.value, "
        "  (SELECT p.value FROM observation_vintages p "
        "   JOIN releases rp ON rp.release_id = p.release_id "
        "   JOIN releases rv ON rv.release_id = v.release_id "
        "   WHERE p.series_id = v.series_id AND p.period = v.period "
        "     AND " + p_at + " < " + v_at +
        "   ORDER BY " + p_at + " DESC LIMIT 1) AS prior, "
        "  EXISTS (SELECT 1 FROM observation_vintages p "
        "   JOIN releases rp ON rp.release_id = p.release_id "
        "   JOIN releases rv ON rv.release_id = v.release_id "
        "   WHERE p.series_id = v.series_id AND p.period = v.period "
        "     AND " + p_at + " < " + v_at + ") AS had_prior "
        "FROM observation_vintages v JOIN series s USING(series_id) "
        "WHERE v.release_id=? ORDER BY s.code, v.period", [release_id]).fetchall()

    revisions, new_values, withdrawals = [], [], []
    for code, name, period, value, prior, had_prior in rows:
        entry = {"code": code, "name": name, "period": period.isoformat(),
                 "value": value, "prior": prior}
        if value is None:
            withdrawals.append(entry)
        elif had_prior:
            revisions.append(entry)
        else:
            new_values.append(entry)

    def clip(entries):
        return {"count": len(entries), "rows": entries[:CHANGES_CAP],
                "truncated": len(entries) > CHANGES_CAP}

    return {"dataset": dataset, "release_id": release_id,
            "revisions": clip(revisions),
            "new_values": clip(new_values),
            "withdrawals": clip(withdrawals)}


# --- audit log ---------------------------------------------------------------

@router.get("/audit")
def audit_log(request: Request, limit: int = 200):
    _require_admin(request, "operations")
    return {"entries": _read_audit(max(1, min(limit, 1000)))}


# --- traffic ------------------------------------------------------------------

@router.get("/visits/live")
def visits_live(request: Request):
    """Who is on the site in the last few minutes. Held in the server's
    memory, never written down, and gone when it restarts — the one figure
    here that cannot be asked about yesterday."""
    _require_admin(request, "operations")
    return visits.live()


@router.get("/visits")
def visit_summary(request: Request, days: int = 30):
    """Readership counted by the server itself, from the append-only visit log
    on the volume. See app/visits.py for what a "visitor" means here."""
    _require_admin(request, "operations")
    out = visits.summary(days)
    # Sign-ups per tagged link, from the accounts store. Absent where accounts
    # are switched off, which the page says rather than showing zeros.
    out["signups"] = None
    if accounts.enabled():
        start = datetime.datetime.strptime(out["from"], "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc).timestamp()
        by = accounts.signups_by_source(start)
        out["signups"] = {"total": sum(by.values()), "tagged": sum(n for k, n in by.items() if k)}
        rows = dict((r["key"], r) for r in out["sources"])
        for tag, n in by.items():
            if not tag:
                continue
            row = rows.get(tag)
            if row is None:
                row = {"key": tag, "views": 0, "visitors": 0, "asked": 0}
                out["sources"].append(row)
            row["signups"] = n
        for row in out["sources"]:
            row.setdefault("signups", 0)
    return out


# --- equity reads for the classification queue --------------------------------
#
# Its own short-lived read-only connection rather than equity_api's cached
# reader: the queue is opened by one operator a few times a day, and borrowing
# the serving reader would let an admin page hold a cursor open across the
# nightly refresh's file swap.

def _equity_rows(sql, params=()):
    if not EQUITY_DB_PATH.exists():
        raise HTTPException(503, "equity database not built yet")
    con = duckdb.connect(str(EQUITY_DB_PATH), read_only=True)
    try:
        cur = con.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _lvh_entities():
    """Every distinct 5% filer, with what it filed about itself and how much
    it files. `business_ja` is picked from the most recent filing that states
    one -- a holder that left the field blank once should not lose the
    description it gave in every other report."""
    return _equity_rows("""
        WITH ranked AS (
            SELECT h.holder_edinet_code AS edinet_code,
                   h.name_raw, h.name_en, h.address_raw, h.is_individual,
                   h.business_ja, h.occupation_ja, f.filed_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY h.holder_edinet_code
                       ORDER BY (h.business_ja IS NOT NULL) DESC,
                                f.filed_date DESC) AS rn
            FROM eq_lvh_holders h
            JOIN eq_lvh_filings f USING (doc_id)
            WHERE h.holder_edinet_code IS NOT NULL
        ), agg AS (
            SELECT h.holder_edinet_code AS edinet_code,
                   COUNT(DISTINCT h.doc_id) AS filings,
                   COUNT(DISTINCT f.issuer_edinet_code) AS issuers,
                   MIN(f.filed_date) AS first_filed,
                   MAX(f.filed_date) AS last_filed,
                   MAX(CASE WHEN h.important_proposal THEN 1 ELSE 0 END) AS ever_proposal,
                   MAX(CASE WHEN h.borrowings_yen > 0 THEN 1 ELSE 0 END) AS ever_borrowed
            FROM eq_lvh_holders h
            JOIN eq_lvh_filings f USING (doc_id)
            WHERE h.holder_edinet_code IS NOT NULL
            GROUP BY 1
        )
        SELECT a.edinet_code, r.name_raw, r.name_en, r.address_raw,
               COALESCE(r.is_individual, FALSE) AS is_individual,
               r.business_ja, r.occupation_ja,
               a.filings, a.issuers, a.first_filed, a.last_filed,
               a.ever_proposal = 1 AS ever_proposal,
               a.ever_borrowed = 1 AS ever_borrowed
        FROM agg a JOIN ranked r ON r.edinet_code = a.edinet_code AND r.rn = 1
        ORDER BY a.filings DESC, a.edinet_code
    """)


def _party_evidence(party):
    """What the filings say about the EDINET codes this profile claims -- the
    check that a profile is about the entity the operator thinks it is, and
    the place a curated class can be seen disagreeing with the filed one."""
    codes = [a["key_value"] for a in party.get("aliases") or ()
             if a["key_type"] == "edinet_code"]
    if not codes:
        return []
    placeholders = ", ".join(["?"] * len(codes))
    rows = _equity_rows("""
        SELECT h.holder_edinet_code AS edinet_code,
               ANY_VALUE(h.name_raw) AS name_raw,
               ANY_VALUE(h.business_ja) AS business_ja,
               COALESCE(ANY_VALUE(h.is_individual), FALSE) AS is_individual,
               COUNT(DISTINCT h.doc_id) AS filings,
               MAX(f.filed_date) AS last_filed
        FROM eq_lvh_holders h JOIN eq_lvh_filings f USING (doc_id)
        WHERE h.holder_edinet_code IN (%s)
        GROUP BY 1 ORDER BY filings DESC
    """ % placeholders, codes)
    for row in rows:
        derived, evidence = filer_labels.type_of(
            row["business_ja"], row["is_individual"], None)
        row["derived_type"] = derived
        row["derived_type_label"] = filer_labels.TYPE_EN.get(derived, derived)
        row["derived_evidence"] = evidence
    return rows

# --- party profiles ----------------------------------------------------------
#
# The one thing on this surface that WRITES. It stays inside the one-writer
# rule because it does not write to DuckDB: curation lives in a JSON file
# beside the audit trail (see app/parties.py). Nothing here can touch an eq_*
# table, so no vintage can be rewritten from the admin console.

async def _json_body(request):
    """The submitted profile, as a plain dict. `parties.normalise` is the
    validator -- it rejects unknown keys, so a pass-through pydantic model
    would add nothing but a dependency on its extra-field semantics."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


def _party_actor(request):
    """Who made the edit: the signed-in person's email, or for the shared
    password the address it came from -- an accurate IP beats a fictional
    username."""
    person = current_person(request)
    if person is None or person.get("shared"):
        return "admin@" + _client_ip(request)
    return person["email"]


def _party_or_404(party_id):
    doc = parties.load()
    if party_id not in doc["parties"]:
        raise HTTPException(404, "No party %s" % party_id)
    return doc


@router.get("/parties/vocab")
def party_vocab(request: Request):
    _require_admin(request, "classification")
    return parties.vocab_payload()


@router.get("/parties/candidates")
def party_candidates(request: Request, min_filings: int = 5,
                     include_individuals: bool = False,
                     unprofiled_only: bool = True, limit: int = 400):
    """The work queue: 5% filers ranked by how much they file, each marked
    with the type DERIVED from its own 事業内容 and with the profile it already
    has, if any. The derived label is a starting point for the operator, never
    a value that gets saved on its behalf."""
    _require_admin(request, "classification")
    rows = _lvh_entities()
    index = parties.alias_index()
    doc = parties.load()

    # Coverage over the WHOLE archive, not just the rows this call returns:
    # the queue is filtered, and "how much filing activity now belongs to a
    # profile" is the number that says whether curation is getting anywhere.
    total_filings = sum(r["filings"] for r in rows)
    attributed = sum(r["filings"] for r in rows
                     if ("edinet_code", r["edinet_code"]) in index)

    out = []
    for row in rows:
        if row["filings"] < max(1, min_filings):
            continue
        if row["is_individual"] and not include_individuals:
            continue
        party_id = index.get(("edinet_code", row["edinet_code"]))
        if unprofiled_only and party_id:
            continue
        derived, evidence = filer_labels.type_of(
            row["business_ja"], row["is_individual"], None)
        item = dict(row)
        item["derived_type"] = derived
        item["derived_type_label"] = filer_labels.TYPE_EN.get(derived, derived)
        item["derived_evidence"] = evidence
        item["derived_group"] = filer_labels.group_of(row["edinet_code"])
        item["party_id"] = party_id
        if party_id:
            party = doc["parties"][party_id]
            item["party_display"] = parties.display_of(party)
            item["party_class"] = party.get("party_class")
        out.append(item)
        if len(out) >= max(1, min(limit, 2000)):
            break
    return {"candidates": out,
            "filings_total": total_filings,
            "filings_attributed": attributed,
            "filings_unattributed": total_filings - attributed,
            "entities_total": len(rows),
            "note": ("Ranked by filing count. derived_type is read from the "
                     "filer's own 事業内容 and is evidence, not classification.")}


@router.get("/parties")
def party_list(request: Request, q: str = "", party_class: str = "",
               tier: str = "", limit: int = 500):
    _require_admin(request, "classification")
    doc = parties.load()
    needle = (q or "").strip().lower()
    items = []
    for party_id in doc["parties"]:
        party = parties.decorate(doc, party_id)
        if party_class and party.get("party_class") != party_class:
            continue
        if tier and party.get("coverage_tier") != tier:
            continue
        if needle:
            hay = " ".join(str(party.get(f) or "") for f in
                           ("party_id", "display", "legal_name_ja",
                            "legal_name_en", "group_name", "group_label"))
            hay += " " + " ".join(a["key_value"] for a in party.get("aliases") or ())
            if needle not in hay.lower():
                continue
        items.append(party)
    items.sort(key=lambda p: (p.get("completeness", 0), p.get("display") or ""))
    return {"parties": items[:max(1, min(limit, 2000))],
            "total": len(doc["parties"])}


@router.post("/parties/export")
def party_export(request: Request):
    """Copy the live store over the git-tracked seed, so hand-typed curation
    is versioned and survives the volume. Deliberately manual and deliberately
    one-way: the seed only ever loads into an ABSENT store."""
    _require_admin(request, "classification")
    doc = parties.load()
    parties.SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
    with open(str(parties.SEED_PATH), "w", encoding="utf-8") as f:
        f.write(payload + "\n")
    audit("party_exported", "%d parties -> %s"
          % (len(doc["parties"]), parties.SEED_PATH.name), _client_ip(request), by=_party_actor(request))
    return {"path": str(parties.SEED_PATH), "parties": len(doc["parties"]),
            "bytes": len(payload) + 1,
            "note": "Commit observatory/curation/parties.json to version this."}


@router.get("/parties/{party_id}")
def party_detail(party_id: str, request: Request):
    _require_admin(request, "classification")
    doc = _party_or_404(party_id)
    party = parties.decorate(doc, party_id)
    party["evidence"] = _party_evidence(party)
    return party


@router.post("/parties")
async def party_create(request: Request):
    _require_admin(request, "classification")
    body = await _json_body(request)
    try:
        party = parties.create(body, _party_actor(request))
    except parties.ProfileError as exc:
        raise HTTPException(400, str(exc))
    audit("party_created", "%s (%s)" % (party["party_id"], party["display"]),
          _client_ip(request), by=_party_actor(request))
    return party


@router.put("/parties/{party_id}")
async def party_update(party_id: str, request: Request):
    _require_admin(request, "classification")
    _party_or_404(party_id)
    body = await _json_body(request)
    try:
        party = parties.update(party_id, body, _party_actor(request))
    except parties.ProfileError as exc:
        raise HTTPException(400, str(exc))
    audit("party_updated", "%s (%s)" % (party_id, party["display"]),
          _client_ip(request), by=_party_actor(request))
    return party


@router.delete("/parties/{party_id}")
def party_delete(party_id: str, request: Request):
    _require_admin(request, "classification")
    _party_or_404(party_id)
    try:
        parties.delete(party_id)
    except parties.ProfileError as exc:
        raise HTTPException(409, str(exc))
    audit("party_deleted", party_id, _client_ip(request), by=_party_actor(request))
    return {"deleted": party_id}
