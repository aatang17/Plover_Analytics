# -*- coding: utf-8 -*-
u"""Hand edits to US companies: the logo, the name shown and the website.

A US company is its SEC number (CIK); every US surface keys its data by it
and finds the company's logo and name here before falling back to what the
filings and equity/us_logos.py supply. The admin console's US Companies tab
writes this store; nothing else does.

Like the party registry (app/parties.py) it is a JSON file under
data/admin/, written atomically, never a DuckDB table: the serving process
does not write to the databases (Ingest Guardrail 5), and no edit here can
touch a filed figure. An uploaded logo is kept inside the same file (base64,
at most MAX_LOGO_BYTES), so the one file is the whole curation and the daily
off-volume backup (app/research_backup.py) carries it. Each company keeps its
own change history: who changed what, when, from what.

Logo states: no entry (the built-in icon, if any), "uploaded" (served from
/logos/us/<cik>-<sha>.<ext>) and "hidden" (no logo, even where a built-in
one exists — for an icon that is wrong and nothing better is to hand).
"""
import base64
import copy
import datetime
import hashlib
import json
import os
import re
import tempfile

from . import db

STORE_PATH = db.DATA_DIR / "admin" / "us_companies.json"
SCHEMA_VERSION = 1
MAX_LOGO_BYTES = 256 * 1024
NAME_MAX = 120
_SIGNATURES = [(b"\x89PNG\r\n\x1a\n", "png", "image/png"),
               (b"\xff\xd8\xff", "jpg", "image/jpeg"),
               (b"GIF87a", "gif", "image/gif"), (b"GIF89a", "gif", "image/gif")]
TYPES = {ext: ctype for _, ext, ctype in _SIGNATURES}
TYPES["webp"] = "image/webp"
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

_CACHE, _CACHE_VERSION = None, None


class EditError(ValueError):
    """An edit refused, with a sentence that says what to do instead."""


def _empty():
    return {"schema": SCHEMA_VERSION, "companies": {}}


def _version():
    try:
        st = STORE_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def version():
    """Changes whenever an edit is saved: part of the response cache's stamp,
    so a renamed company or a new logo is served at once."""
    return _version()


def load():
    """The whole store; an unreadable file is an error, never silently empty."""
    global _CACHE, _CACHE_VERSION
    version = _version()
    if _CACHE is not None and _CACHE_VERSION == version:
        return _CACHE
    if version is None:
        doc = _empty()
    else:
        try:
            with open(str(STORE_PATH), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            raise RuntimeError("%s is not readable as the US company store; "
                               "refusing to treat it as empty" % STORE_PATH)
        if not isinstance(doc, dict) or not isinstance(doc.get("companies"), dict):
            raise RuntimeError("%s has no companies object" % STORE_PATH)
    _CACHE, _CACHE_VERSION = doc, version
    return doc


def _save(doc):
    global _CACHE, _CACHE_VERSION
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(STORE_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(STORE_PATH))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _CACHE, _CACHE_VERSION = doc, _version()


def _now():
    return (datetime.datetime.now(datetime.timezone.utc)
            .replace(tzinfo=None, microsecond=0).isoformat() + "Z")


def entry(cik):
    """This company's edits ({} when none), without the logo bytes."""
    e = load()["companies"].get(str(int(cik))) or {}
    out = {k: v for k, v in e.items() if k != "logo_b64"}
    return copy.deepcopy(out)


def _change(cik, by, field, apply):
    doc = copy.deepcopy(load())
    e = doc["companies"].setdefault(str(int(cik)), {})
    old = {k: e.get(k) for k in field}
    apply(e)
    now = {k: e.get(k) for k in field}
    if old == now:
        return entry(cik)
    e.setdefault("history", []).append({"at": _now(), "by": by, "changed": sorted(field),
                                        "from": {k: _shown(k, v) for k, v in old.items()},
                                        "to": {k: _shown(k, v) for k, v in now.items()}})
    e["updated_at"], e["updated_by"] = _now(), by
    if not any(e.get(k) for k in ("name", "website", "logo")):
        e = {"history": e["history"]}
        doc["companies"][str(int(cik))] = e
    _save(doc)
    return entry(cik)


def _shown(field, value):
    # the history records which logo, never its bytes
    return value[:16] if field == "logo_sha" and value else value


def set_name(cik, name, by):
    """The name shown for the company; blank goes back to the filed name."""
    name = " ".join((name or "").split())
    if len(name) > NAME_MAX:
        raise EditError("A name can be up to %d characters." % NAME_MAX)
    return _change(cik, by, ["name"], lambda e: _put(e, "name", name or None))


def set_website(cik, website, by):
    """The company's own site, as a bare domain ('apple.com'); blank clears."""
    w = (website or "").strip().lower()
    w = re.sub(r"^https?://", "", w).split("/")[0]
    w = w[4:] if w.startswith("www.") else w
    if w and not _DOMAIN.match(w):
        raise EditError("Type the website as a domain, like apple.com.")
    return _change(cik, by, ["website"], lambda e: _put(e, "website", w or None))


def logo_type(data):
    """(ext, content type) of an image we accept, or raise EditError."""
    if not data:
        raise EditError("The file is empty. Choose a PNG, JPEG, GIF or WebP image.")
    if len(data) > MAX_LOGO_BYTES:
        raise EditError("A logo can be up to %d KB; this one is %d KB. Save it smaller and try again."
                        % (MAX_LOGO_BYTES // 1024, (len(data) + 1023) // 1024))
    for sig, ext, ctype in _SIGNATURES:
        if data.startswith(sig):
            return ext, ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise EditError("That file is not a PNG, JPEG, GIF or WebP image. SVG is not accepted, "
                    "because a drawing file can carry script.")


def set_logo(cik, data, by):
    ext, _ = logo_type(data)
    sha = hashlib.sha256(data).hexdigest()

    def apply(e):
        e.update(logo="uploaded", logo_ext=ext, logo_sha=sha, logo_bytes=len(data),
                 logo_b64=base64.b64encode(data).decode("ascii"))
    return _change(cik, by, ["logo", "logo_sha"], apply)


def hide_logo(cik, by):
    def apply(e):
        for k in ("logo_ext", "logo_sha", "logo_bytes", "logo_b64"):
            e.pop(k, None)
        e["logo"] = "hidden"
    return _change(cik, by, ["logo", "logo_sha"], apply)


def reset_logo(cik, by):
    def apply(e):
        for k in ("logo", "logo_ext", "logo_sha", "logo_bytes", "logo_b64"):
            e.pop(k, None)
    return _change(cik, by, ["logo", "logo_sha"], apply)


def _put(e, key, value):
    if value is None:
        e.pop(key, None)
    else:
        e[key] = value


# ---- read side, for every surface -------------------------------------------
def logo_url(cik):
    """'' for a hidden logo, the uploaded file's address, or None (no edit)."""
    e = load()["companies"].get(str(int(cik))) or {}
    if e.get("logo") == "hidden":
        return ""
    if e.get("logo") == "uploaded" and e.get("logo_sha"):
        return "/logos/us/%d-%s.%s" % (int(cik), e["logo_sha"][:16], e["logo_ext"])
    return None


def logo_file(name):
    """(bytes, content type) for /logos/us/<name>, or None."""
    m = re.fullmatch(r"(\d{1,10})-([0-9a-f]{16})\.(png|jpg|gif|webp)", name or "")
    if not m:
        return None
    e = load()["companies"].get(str(int(m.group(1)))) or {}
    if e.get("logo") != "uploaded" or not (e.get("logo_sha") or "").startswith(m.group(2)):
        return None
    return base64.b64decode(e["logo_b64"]), TYPES[e["logo_ext"]]


def name(cik):
    return (load()["companies"].get(str(int(cik))) or {}).get("name")


def website(cik):
    return (load()["companies"].get(str(int(cik))) or {}).get("website")


def edited():
    """CIKs with any edit in force, newest first."""
    rows = [(e.get("updated_at") or "", int(c)) for c, e in load()["companies"].items()
            if any(e.get(k) for k in ("name", "website", "logo"))]
    return [c for _, c in sorted(rows, reverse=True)]
