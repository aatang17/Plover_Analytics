# -*- coding: utf-8 -*-
u"""Which company edits an answer read, so saving one clears only the answers
that showed that company.

The admin console's hand edits (app/us_companies.py, app/jp_companies.py)
were part of the response cache's one stamp until 2026-10-29: one new logo
for Alphabet emptied the whole cache, and rebuilding the ~360 answers readers
had used (cache.keep_warm) held the server for seven minutes, long enough for
the proxy to take it out of rotation and show readers errors.

Now each store's read functions note the company they read, with that
company's edit stamp, while the cache fills an entry (app/cache.py). The entry
stays good until one of those companies is edited. An answer that reads the
whole store (the list of edited companies) or more than MAX_KEYS companies
depends on every edit, as before.

A store is a module with STORE (its name here), version() (changes with any
edit) and stamp(key) (changes with an edit to that company).
"""
import contextvars

ANY = "*"
MAX_KEYS = 2000

# {store: {key: stamp} or ANY} for the entry being filled; None outside a fill.
# Starlette runs a plain `def` route in a worker thread with a copy of this
# context, and the copy holds the same dict, so its reads land here too.
_READS = contextvars.ContextVar("edit_reads", default=None)


def note(store, key, stamp):
    """A store's read function read `key` at `stamp`."""
    reads = _READS.get()
    if reads is None:
        return
    keys = reads.setdefault(store, {})
    if keys is ANY:
        return
    keys.setdefault(key, stamp)
    if len(keys) > MAX_KEYS:
        reads[store] = ANY


def note_all(store):
    """The answer read the whole store: any edit to it clears the answer."""
    reads = _READS.get()
    if reads is not None:
        reads[store] = ANY


def begin():
    """(reads, token): start noting for one fill; pass the token to end()."""
    reads = {}
    return reads, _READS.set(reads)


def end(token):
    _READS.reset(token)


def holds(reads, versions, stores):
    """True while no company in `reads` has been edited since the fill.

    `versions` is {store: version()} at the fill, and is moved forward when
    the edits since touched none of the answer's companies, so the next look
    is one file check per store."""
    for store in stores:
        v = store.version()
        if versions.get(store.STORE) == v:
            continue
        keys = reads.get(store.STORE)
        if keys is ANY:
            return False
        if keys and any(store.stamp(k) != s for k, s in keys.items()):
            return False
        versions[store.STORE] = v
    return True
