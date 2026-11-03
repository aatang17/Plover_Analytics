# -*- coding: utf-8 -*-
"""Plover's own AI key for Your Plover: questions on the house, a day at a time.

Since 2026-11-03 Plover pays for Your Plover's questions (the owner's decision:
subsidise it for now, and think about charging if demand picks up). Someone
without a key of their own gets FREE_TRIES questions a day on the server's key;
each question asked on Your Plover and each article summary takes one, and
they come back the next day. Someone with their own key (or a connected
ChatGPT plan) never touches these and has no limit.

Two limits, both server settings the owner sets:

  * FREE_TRIES (default 20) per account per day. 0 switches it off, and every
    question then needs the person's own key, as before.
  * FREE_TRIES_PER_DAY (default 200) across every account, so a rush of
    sign-ups cannot run up an unbounded bill. When the day's are gone, the
    message says to come back tomorrow or add a key.

Demand is kept for the charging decision: ia_free_uses.used counts every
question a person has had on the house, for good; ia_free_account_days and
ia_free_days count each day's.

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

USED_UP = ("You have asked today's %d questions on Plover. They come back tomorrow; to keep asking now, "
           "add your own AI key in the AI Key section.")
DAY_FULL = ("Plover's questions for today have all been used. They come back tomorrow; to keep asking now, "
            "add your own AI key in the AI Key section.")


def _int_env(name, default):
    try:
        return max(0, int(str(os.environ.get(name, "")).strip()))
    except ValueError:
        return default


def limit():
    return _int_env("FREE_TRIES", 20)


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
    """Questions on the house today."""
    row = store.conn().execute("SELECT used FROM ia_free_account_days WHERE account_id = ? AND day = ?",
                               (account_id, _today())).fetchone()
    return int(row[0]) if row else 0


def used_ever(account_id):
    """Questions on the house, for good: the demand the charging decision reads."""
    row = store.conn().execute("SELECT used FROM ia_free_uses WHERE account_id = ?",
                               (account_id,)).fetchone()
    return int(row[0]) if row else 0


def status(account_id):
    """What the page shows: how many of today's questions on Plover this person has left."""
    on = limit() > 0 and house() is not None
    n = used(account_id)
    return {"on": on, "limit": limit(), "per": "day", "used": n, "left": max(0, limit() - n) if on else 0,
            "ever": used_ever(account_id)}


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
        db.execute("INSERT OR IGNORE INTO ia_free_account_days (account_id, day, used) VALUES (?, ?, 0)",
                   (account_id, day))
        db.execute("INSERT OR IGNORE INTO ia_free_days (day, used) VALUES (?, 0)", (day,))
        mine = db.execute("UPDATE ia_free_account_days SET used = used + 1 "
                          "WHERE account_id = ? AND day = ? AND used < ?", (account_id, day, limit())).rowcount
        if not mine:
            db.commit()
            raise NoTries(USED_UP % limit())
        everyone = db.execute("UPDATE ia_free_days SET used = used + 1 WHERE day = ? AND used < ?",
                              (day, per_day())).rowcount
        if not everyone:
            db.rollback()
            raise NoTries(DAY_FULL)
        db.execute("UPDATE ia_free_uses SET used = used + 1, updated_at = ? WHERE account_id = ?", (t, account_id))
        db.commit()
    return key


def give_back(account_id):
    """The work failed: the try was not used."""
    db, day = store.conn(), _today()
    with store._lock:
        db.execute("UPDATE ia_free_uses SET used = used - 1 WHERE account_id = ? AND used > 0", (account_id,))
        db.execute("UPDATE ia_free_account_days SET used = used - 1 WHERE account_id = ? AND day = ? AND used > 0",
                   (account_id, day))
        db.execute("UPDATE ia_free_days SET used = used - 1 WHERE day = ? AND used > 0", (day,))
        db.commit()
