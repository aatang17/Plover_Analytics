# -*- coding: utf-8 -*-
"""Free tries: a few answers on Plover's own AI key for someone who has not
added theirs yet.

Answers and article summaries run on the person's own key (Settings on
Your Plover). A new account has none, so it gets FREE_TRIES uses on the
server's key first: each question asked on Your Plover and each article
summary takes one. Once they are gone the page asks for the person's own key.
Someone with their own key (or a connected ChatGPT plan) never touches these.

Two limits, both server settings the owner sets:

  * FREE_TRIES (default 5) per account, for good. 0 switches free tries off.
  * FREE_TRIES_PER_DAY (default 200) across every account, so a rush of
    sign-ups cannot run up an unbounded bill. When the day's tries are gone,
    the message says to come back tomorrow or add a key.

The key is the server's own: FREE_TRIES_PROVIDER names one of the gateway's
providers (deepseek, openai, anthropic), else the first of DEEPSEEK_API_KEY,
OPENAI_API_KEY, ANTHROPIC_API_KEY that is set. FREE_TRIES_MODEL overrides the
provider's default model. Nothing here is ever sent to the browser except
counts.

A try is taken before the work starts and given back if the work fails, so
two clicks at once cannot both use the last one, and an error costs nothing.
"""
import datetime
import os

from . import codex, gateway, store

_KEY_ENV = (("deepseek", "DEEPSEEK_API_KEY"), ("openai", "OPENAI_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"))

USED_UP = ("Your free tries are used up. Add your own AI key in the AI Key section "
           "to keep asking.")
DAY_FULL = ("Today's free tries have all been used. Add your own AI key in the AI Key "
            "section to keep asking now, or try again tomorrow.")


def _int_env(name, default):
    try:
        return max(0, int(str(os.environ.get(name, "")).strip()))
    except ValueError:
        return default


def limit():
    return _int_env("FREE_TRIES", 5)


def per_day():
    return _int_env("FREE_TRIES_PER_DAY", 200)


def house():
    """The server's key as {provider, key, model}, or None when there is none."""
    want = str(os.environ.get("FREE_TRIES_PROVIDER", "")).strip().lower()
    for provider, var in _KEY_ENV:
        if want and provider != want:
            continue
        key = str(os.environ.get(var, "")).strip()
        if key and provider in gateway.PROVIDERS:
            model = str(os.environ.get("FREE_TRIES_MODEL", "")).strip() or gateway.default_model(provider)
            return {"provider": provider, "key": key, "model": model}
    return None


def own_key(desk):
    """True when the person's answers run on their own key or ChatGPT plan."""
    p = desk.get("model_provider")
    if not p:
        return False
    if p == codex.PROVIDER:
        return bool(desk.get("codex_connected"))
    return bool(desk.get("model_key_set"))


def _today():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def used(account_id):
    row = store.conn().execute("SELECT used FROM ia_free_uses WHERE account_id = ?",
                               (account_id,)).fetchone()
    return int(row[0]) if row else 0


def status(account_id):
    """What the page shows: how many free tries this person has left."""
    on = limit() > 0 and house() is not None
    n = used(account_id)
    return {"on": on, "limit": limit(), "used": n, "left": max(0, limit() - n) if on else 0}


class NoTries(Exception):
    """No free try can be taken; str() is worded for the reader."""


def take(account_id):
    """Use one free try. Returns the server's key for this one piece of work;
    raises NoTries, worded for the reader, when none is left."""
    key = house()
    if limit() <= 0 or key is None:
        raise NoTries("Answers run on your own AI key. Add one in the AI Key section, then try again.")
    db, t, day = store.conn(), store.now(), _today()
    with store._lock:
        db.execute("INSERT OR IGNORE INTO ia_free_uses (account_id, used, updated_at) VALUES (?, 0, ?)",
                   (account_id, t))
        db.execute("INSERT OR IGNORE INTO ia_free_days (day, used) VALUES (?, 0)", (day,))
        mine = db.execute("UPDATE ia_free_uses SET used = used + 1, updated_at = ? "
                          "WHERE account_id = ? AND used < ?", (t, account_id, limit())).rowcount
        if not mine:
            db.commit()
            raise NoTries(USED_UP)
        everyone = db.execute("UPDATE ia_free_days SET used = used + 1 WHERE day = ? AND used < ?",
                              (day, per_day())).rowcount
        if not everyone:
            db.rollback()
            raise NoTries(DAY_FULL)
        db.commit()
    return key


def give_back(account_id):
    """The work failed: the try was not used."""
    db, day = store.conn(), _today()
    with store._lock:
        db.execute("UPDATE ia_free_uses SET used = used - 1 WHERE account_id = ? AND used > 0", (account_id,))
        db.execute("UPDATE ia_free_days SET used = used - 1 WHERE day = ? AND used > 0", (day,))
        db.commit()
