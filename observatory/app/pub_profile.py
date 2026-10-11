# -*- coding: utf-8 -*-
"""A publication's own profile: what makes it its own workspace, like a
publication on Substack.

Each publication already keeps its members, skills, house style and
connectors. This module holds the rest, all set by its owner:

* **AI** — one OpenAI or Anthropic API key the publication pays for. A run on
  one of its articles uses it instead of the writer's own provider; with none
  set, each writer's own is used, as before. Only API keys: a Claude or
  ChatGPT plan is one person's subscription and cannot be shared.
* **Article defaults** — the length every run in it writes to, the starting
  text of a new article, and the skill the AI panel opens on.
* **Look** — its logo and its accent colour (one of ACCENTS, defined in
  tokens.css with a light and a dark shade), on its /p/<address> pages and in
  its feed. Its name and tagline are the publication's own fields already.

Its own market-data keys are kept with the person-level ones in the desk store
(app/assistant/store.py, ia_pub_data_sources); see app/assistant/market_data.py.

Stored in staff.db's team_settings, one row per field, "pub.<field>@<id>",
next to each publication's house style. The AI key is sealed with the
assistant keychain and never returned. Python 3.9: no 3.10+ syntax.
"""
import re

from . import research, skills, staff
from .assistant import gateway, keychain

AI_PROVIDERS = (("openai", "OpenAI API key"), ("anthropic", "Anthropic API key"))
AI_LABEL = dict(AI_PROVIDERS)
DEFAULT_MODEL = {"openai": "gpt-5.5", "anthropic": "claude-sonnet-5-5"}
# The accents a publication may pick: each is a pair of --obs-pub-* tokens in
# tokens.css (light and dark), dark enough for white type in both themes.
ACCENTS = (("navy", "Navy"), ("teal", "Teal"), ("forest", "Forest"), ("rust", "Rust"),
           ("plum", "Plum"), ("slate", "Slate"))
ACCENT_LABEL = dict(ACCENTS)
TEMPLATE_MAX = 6000
_LOGO = re.compile(r"^([0-9a-f]{64})\.(png|jpg|webp)$")


class ProfileError(ValueError):
    """A change refused, worded for the owner."""


def _k(publication_id, field):
    return "pub.%s@%d" % (field, int(publication_id))


def _get(publication_id, field):
    return staff.team_setting(_k(publication_id, field)) or None


def _set(publication_id, field, value):
    staff.set_team_setting(_k(publication_id, field), value or "")


# ------------------------------------------------------------------ AI

def ai(publication_id):
    """The publication's AI, without its key: None when it has none."""
    provider = _get(publication_id, "ai_provider")
    if provider not in AI_LABEL or not _get(publication_id, "ai_key_ct"):
        return None
    model = _get(publication_id, "ai_model")
    return {"provider": provider, "label": AI_LABEL[provider], "model": model or "",
            "default_model": DEFAULT_MODEL[provider],
            "key_last4": _get(publication_id, "ai_key_last4") or ""}


def ai_for_run(publication_id):
    """{provider, model, key_ct} for a run on one of its articles, or None."""
    a = ai(publication_id)
    if a is None:
        return None
    return {"provider": a["provider"], "model": a["model"] or DEFAULT_MODEL[a["provider"]],
            "key_ct": _get(publication_id, "ai_key_ct")}


def set_ai(publication_id, provider, model=None, key=None):
    """Connect or change the publication's AI. A new key is tried first."""
    if provider not in AI_LABEL:
        raise ProfileError("Choose OpenAI or Anthropic. A Claude or ChatGPT plan belongs to one "
                           "person and cannot be shared by a publication.")
    model = (model or "").strip()[:80]
    key = (key or "").strip()
    current = ai(publication_id)
    if not key and not (current and current["provider"] == provider):
        raise ProfileError("Paste the publication's %s." % AI_LABEL[provider])
    if key:
        if not keychain.enabled():
            raise ProfileError("This server cannot store keys yet: ASSISTANT_SECRET is not set.")
        try:
            gateway.ping(provider, key, model or DEFAULT_MODEL[provider])
        except gateway.GatewayError as exc:
            raise ProfileError("That key did not work: %s" % exc)
        _set(publication_id, "ai_key_ct", keychain.seal(key))
        _set(publication_id, "ai_key_last4", keychain.last4(key))
    _set(publication_id, "ai_provider", provider)
    _set(publication_id, "ai_model", model)
    return ai(publication_id)


def clear_ai(publication_id):
    for f in ("ai_provider", "ai_model", "ai_key_ct", "ai_key_last4"):
        _set(publication_id, f, "")


# ------------------------------------------------------- article defaults

def defaults(publication_id):
    skill_id = _get(publication_id, "default_skill")
    sk = skills.get(int(skill_id), publication_id) if skill_id and skill_id.isdigit() else None
    return {"length": _get(publication_id, "length") or "",
            "template": _get(publication_id, "template") or "",
            "default_skill": sk["id"] if sk and sk["enabled"] else None}


def set_defaults(publication_id, length=None, template=None, default_skill=None, lengths=()):
    """`lengths` is the writing assistant's list of length keys (research_ai.LENGTHS);
    "" means each writer's own choice."""
    length = (length or "").strip()
    if length and length not in lengths:
        raise ProfileError("Choose one of the lengths listed.")
    template = (template or "").replace("\r\n", "\n").strip()
    if len(template) > TEMPLATE_MAX:
        raise ProfileError("Keep the starting text under %d characters." % TEMPLATE_MAX)
    if default_skill:
        sk = skills.get(int(default_skill), publication_id)
        if sk is None or not sk["enabled"]:
            raise ProfileError("That skill is not one of this publication's.")
    _set(publication_id, "length", length)
    _set(publication_id, "template", template)
    _set(publication_id, "default_skill", str(int(default_skill)) if default_skill else "")
    return defaults(publication_id)


# ------------------------------------------------------------------ look

def look(publication_id):
    accent = _get(publication_id, "accent")
    logo = _get(publication_id, "logo")
    return {"accent": accent if accent in ACCENT_LABEL else "navy",
            "logo": logo if logo and _LOGO.match(logo) else ""}


def logo_url(publication_id):
    logo = look(publication_id)["logo"]
    return "/research/media/" + logo if logo else ""


def set_look(publication_id, accent=None, logo=None):
    accent = (accent or "navy").strip()
    if accent not in ACCENT_LABEL:
        raise ProfileError("Choose one of the accent colours listed.")
    logo = (logo or "").strip()
    if logo:
        m = _LOGO.match(logo)
        if not m or research.media_file(m.group(1), m.group(2)) is None:
            raise ProfileError("Upload the logo again: that picture could not be found.")
    _set(publication_id, "accent", accent)
    _set(publication_id, "logo", logo)
    return look(publication_id)


def view(publication_id):
    """Everything the owner sees on Settings, no key included."""
    return {"ai": ai(publication_id), "ai_providers": [{"key": k, "label": v} for k, v in AI_PROVIDERS],
            "defaults": defaults(publication_id), "look": look(publication_id),
            "accents": [{"key": k, "label": v} for k, v in ACCENTS],
            "keychain": keychain.enabled()}
