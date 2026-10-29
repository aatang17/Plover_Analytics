# -*- coding: utf-8 -*-
u"""Hand edits to Japanese companies: the logo and the name shown.

The Japanese counterpart of app/us_companies.py. A Japanese company is its
securities code (7203, 285A); the pages that name one (Your Plover, the
company page) find its logo and name here before falling back to the annual
report's English name, written readably (app/jp_names.py). The admin
console's Companies tab writes this store; nothing else does.

A JSON file under data/admin/, written atomically, never a DuckDB table, for
the reasons us_companies.py gives: the serving process does not write to the
databases, and no edit here can touch a filed figure. An uploaded logo is kept
inside the file (base64, at most MAX_LOGO_BYTES), so the daily off-volume
backup (app/research_backup.py) carries the whole curation. Each company keeps
its own change history.

Logo states: no entry (no logo: none are fetched for Japanese companies yet),
"uploaded" (served from /logos/jp/<code>-<sha>.<ext>) and "hidden".
"""
import base64
import copy
import hashlib
import json
import os
import re
import tempfile

from . import db, edit_reads
from .us_companies import EditError, MAX_LOGO_BYTES, NAME_MAX, TYPES, _now, logo_type  # noqa: F401

STORE = "jp-companies"
STORE_PATH = db.DATA_DIR / "admin" / "jp_companies.json"
SCHEMA_VERSION = 1
_CODE = re.compile(r"^[0-9][0-9A-Z]{2}[0-9A-Z]$")

_CACHE, _CACHE_VERSION = None, None


def code_of(value):
    """'7203', '72030' (EDINET's five digits) or '285a' -> '7203' / '285A';
    None when it is not a securities code."""
    c = (value or "").strip().upper()
    if len(c) == 5 and c.endswith("0"):
        c = c[:4]
    return c if _CODE.match(c) else None


def _version():
    try:
        st = STORE_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def version():
    """Changes whenever an edit is saved; see us_companies.version()."""
    return _version()


def _stamp(e):
    return (e.get("updated_at"), len(e.get("history") or ()))


def stamp(code):
    """Changes with each edit saved for this company."""
    c = code_of(code)
    return _stamp((load()["companies"].get(c) or {}) if c else {})


def load():
    """The whole store; an unreadable file is an error, never silently empty."""
    global _CACHE, _CACHE_VERSION
    v = _version()
    if _CACHE is not None and _CACHE_VERSION == v:
        return _CACHE
    if v is None:
        doc = {"schema": SCHEMA_VERSION, "companies": {}}
    else:
        try:
            with open(str(STORE_PATH), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            raise RuntimeError("%s is not readable as the Japanese company store; "
                               "refusing to treat it as empty" % STORE_PATH)
        if not isinstance(doc, dict) or not isinstance(doc.get("companies"), dict):
            raise RuntimeError("%s has no companies object" % STORE_PATH)
    _CACHE, _CACHE_VERSION = doc, v
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


def _get(code):
    """The company's stored edits, noted for the response cache (app/edit_reads.py)."""
    c = code_of(code)
    if not c:
        return {}
    e = load()["companies"].get(c) or {}
    edit_reads.note(STORE, c, _stamp(e))
    return e


def entry(code):
    """This company's edits ({} when none), without the logo bytes."""
    return copy.deepcopy({k: v for k, v in _get(code).items() if k != "logo_b64"})


def _change(code, by, fields, apply):
    c = code_of(code)
    if c is None:
        raise EditError("A securities code is four characters, such as 7203.")
    doc = copy.deepcopy(load())
    e = doc["companies"].setdefault(c, {})
    old = {k: e.get(k) for k in fields}
    apply(e)
    new = {k: e.get(k) for k in fields}
    if old == new:
        return entry(c)
    shown = lambda k, v: v[:16] if k == "logo_sha" and v else v   # noqa: E731 — which logo, never its bytes
    e.setdefault("history", []).append({"at": _now(), "by": by, "changed": sorted(fields),
                                        "from": {k: shown(k, v) for k, v in old.items()},
                                        "to": {k: shown(k, v) for k, v in new.items()}})
    e["updated_at"], e["updated_by"] = _now(), by
    if not any(e.get(k) for k in ("name", "logo")):
        doc["companies"][c] = {"history": e["history"]}
    _save(doc)
    return entry(c)


def set_name(code, name, by):
    """The name shown for the company; blank goes back to the filed name."""
    name = " ".join((name or "").split())
    if len(name) > NAME_MAX:
        raise EditError("A name can be up to %d characters." % NAME_MAX)

    def apply(e):
        if name:
            e["name"] = name
        else:
            e.pop("name", None)
    return _change(code, by, ["name"], apply)


def set_logo(code, data, by):
    ext, _ = logo_type(data)
    sha = hashlib.sha256(data).hexdigest()

    def apply(e):
        e.update(logo="uploaded", logo_ext=ext, logo_sha=sha, logo_bytes=len(data),
                 logo_b64=base64.b64encode(data).decode("ascii"))
    return _change(code, by, ["logo", "logo_sha"], apply)


def hide_logo(code, by):
    def apply(e):
        for k in ("logo_ext", "logo_sha", "logo_bytes", "logo_b64"):
            e.pop(k, None)
        e["logo"] = "hidden"
    return _change(code, by, ["logo", "logo_sha"], apply)


def reset_logo(code, by):
    def apply(e):
        for k in ("logo", "logo_ext", "logo_sha", "logo_bytes", "logo_b64"):
            e.pop(k, None)
    return _change(code, by, ["logo", "logo_sha"], apply)


# ---- read side, for every surface -------------------------------------------
def logo(code):
    """The uploaded logo's address, or None (none uploaded, or hidden)."""
    e = _get(code)
    if e.get("logo") == "uploaded" and e.get("logo_sha"):
        return "/logos/jp/%s-%s.%s" % (code_of(code), e["logo_sha"][:16], e["logo_ext"])
    return None


def logo_file(name):
    """(bytes, content type) for /logos/jp/<name>, or None."""
    m = re.fullmatch(r"([0-9][0-9A-Z]{2}[0-9A-Z])-([0-9a-f]{16})\.(png|jpg|gif|webp)", name or "")
    if not m:
        return None
    e = load()["companies"].get(m.group(1)) or {}
    if e.get("logo") != "uploaded" or not (e.get("logo_sha") or "").startswith(m.group(2)):
        return None
    return base64.b64decode(e["logo_b64"]), TYPES[e["logo_ext"]]


def name(code):
    return _get(code).get("name")


def shown_name(code, name_en):
    """The one English name a page shows: an admin edit, else the annual
    report's name written readably."""
    from .jp_names import display_name
    return name(code) or display_name(name_en)


def edited():
    """Codes with any edit in force, newest first."""
    edit_reads.note_all(STORE)
    rows = [(e.get("updated_at") or "", c) for c, e in load()["companies"].items()
            if any(e.get(k) for k in ("name", "logo"))]
    return [c for _, c in sorted(rows, reverse=True)]
