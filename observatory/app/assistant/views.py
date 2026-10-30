# -*- coding: utf-8 -*-
"""Saved charts and saved screens: what a signed-in person keeps on the
Charts and Screener tabs of Your Plover (web/my.html).

A saved chart is its spec (companies, measure, period, benchmark, display:
app/us_build_api.py) and a title; the page redraws it from today's closes
and filings, so it never goes stale. A saved screen is its spec plus the
tickers it matched when it was last run, so the next run can say which
companies are new.

Both belong to the account that saved them: only that account can list,
change or delete them. Routes under /api/v1/assistant/charts and
/api/v1/assistant/screens; mounted from app/assistant/api.py.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts
from . import store

KEEP = 100          # saved charts, and saved screens, per account

SCHEMA = """
CREATE TABLE IF NOT EXISTS ia_saved_charts (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    spec TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_saved_charts_by_account ON ia_saved_charts (account_id, updated_at);
CREATE TABLE IF NOT EXISTS ia_saved_screens (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    last_tickers TEXT NOT NULL,
    last_n INTEGER,
    last_run_at INTEGER,
    new_n INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ia_saved_screens_by_account ON ia_saved_screens (account_id, updated_at);
"""
_made = set()


def _conn():
    c = store.conn()
    key = str(store.DB_PATH)
    if key not in _made:
        c.executescript(SCHEMA)
        _made.add(key)
    return c


def reset_for_tests():
    _made.clear()


def _chart(r):
    return {"id": r["id"], "title": r["title"], "spec": store._loads(r["spec"], {}),
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _screen(r):
    return {"id": r["id"], "name": r["name"], "spec": store._loads(r["spec"], {}),
            "last_tickers": store._loads(r["last_tickers"], []), "last_n": r["last_n"],
            "last_run_at": r["last_run_at"], "new_n": r["new_n"],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


# ------------------------------------------------------------------ charts

def charts(account_id):
    cur = _conn().execute("SELECT * FROM ia_saved_charts WHERE account_id = ? ORDER BY updated_at DESC, id DESC",
                          (account_id,))
    return [_chart(r) for r in store._rows(cur)]


def save_chart(account_id, title, spec, chart_id=None):
    title = (title or "").strip()[:160] or "Untitled chart"
    body = json.dumps(spec)
    t = store.now()
    c = _conn()                      # before the lock: store.conn() takes it too
    with store._lock:
        if chart_id:
            c.execute("UPDATE ia_saved_charts SET title = ?, spec = ?, updated_at = ? WHERE id = ? AND account_id = ?",
                      (title, body, t, chart_id, account_id))
            if c.total_changes == 0 or c.execute("SELECT 1 FROM ia_saved_charts WHERE id = ? AND account_id = ?",
                                                 (chart_id, account_id)).fetchone() is None:
                c.commit()
                return None
            new_id = chart_id
        else:
            new_id = c.execute("INSERT INTO ia_saved_charts (account_id, title, spec, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?, ?)", (account_id, title, body, t, t)).lastrowid
            c.execute("DELETE FROM ia_saved_charts WHERE account_id = ? AND id NOT IN (SELECT id FROM ia_saved_charts "
                      "WHERE account_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?)", (account_id, account_id, KEEP))
        c.commit()
    r = _conn().execute("SELECT * FROM ia_saved_charts WHERE id = ?", (new_id,)).fetchone()
    return _chart(dict(r)) if r else None


def delete_chart(account_id, chart_id):
    c = _conn()
    with store._lock:
        c.execute("DELETE FROM ia_saved_charts WHERE id = ? AND account_id = ?", (chart_id, account_id))
        c.commit()


# ----------------------------------------------------------------- screens

def screens(account_id):
    cur = _conn().execute("SELECT * FROM ia_saved_screens WHERE account_id = ? ORDER BY updated_at DESC, id DESC",
                          (account_id,))
    return [_screen(r) for r in store._rows(cur)]


def save_screen(account_id, name, spec, tickers=None, screen_id=None):
    """Save a screen, or record a run of a saved one: `tickers` are the
    matches now, and new_n is how many were not in the last run."""
    name = (name or "").strip()[:160] or "Untitled screen"
    body = json.dumps(spec)
    t = store.now()
    c = _conn()
    with store._lock:
        prev = None
        if screen_id:
            prev = c.execute("SELECT * FROM ia_saved_screens WHERE id = ? AND account_id = ?",
                             (screen_id, account_id)).fetchone()
            if prev is None:
                return None
            prev = dict(prev)
        last_tickers = sorted(set(tickers)) if tickers is not None else store._loads(prev["last_tickers"], []) if prev else []
        new_n = None
        if tickers is not None and prev is not None and prev["last_run_at"]:
            before = set(store._loads(prev["last_tickers"], []))
            new_n = len([x for x in last_tickers if x not in before])
        last_n = len(last_tickers) if tickers is not None else (prev["last_n"] if prev else None)
        last_run_at = t if tickers is not None else (prev["last_run_at"] if prev else None)
        if prev:
            c.execute("UPDATE ia_saved_screens SET name = ?, spec = ?, last_tickers = ?, last_n = ?, last_run_at = ?, "
                      "new_n = ?, updated_at = ? WHERE id = ?",
                      (name, body, json.dumps(last_tickers), last_n, last_run_at, new_n, t, screen_id))
            new_id = screen_id
        else:
            new_id = c.execute("INSERT INTO ia_saved_screens (account_id, name, spec, last_tickers, last_n, last_run_at, "
                               "new_n, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (account_id, name, body, json.dumps(last_tickers), last_n, last_run_at, new_n, t, t)).lastrowid
            c.execute("DELETE FROM ia_saved_screens WHERE account_id = ? AND id NOT IN (SELECT id FROM ia_saved_screens "
                      "WHERE account_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?)", (account_id, account_id, KEEP))
        c.commit()
    r = _conn().execute("SELECT * FROM ia_saved_screens WHERE id = ?", (new_id,)).fetchone()
    return _screen(dict(r)) if r else None


def delete_screen(account_id, screen_id):
    c = _conn()
    with store._lock:
        c.execute("DELETE FROM ia_saved_screens WHERE id = ? AND account_id = ?", (screen_id, account_id))
        c.commit()


# ------------------------------------------------------------------ routes

router = APIRouter()


def _account(request):
    account = accounts.current_account(request)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    return account


class ChartIn(BaseModel):
    title: str = ""
    spec: dict
    id: Optional[int] = None


class ScreenIn(BaseModel):
    name: str = ""
    spec: dict
    tickers: Optional[List[str]] = None
    id: Optional[int] = None


@router.get("/charts")
def list_charts(request: Request):
    return {"charts": charts(_account(request)["id"])}


@router.post("/charts")
def post_chart(payload: ChartIn, request: Request):
    aid = _account(request)["id"]
    if len(json.dumps(payload.spec)) > 8000:
        raise HTTPException(400, "That chart has too much in it to save.")
    out = save_chart(aid, payload.title, payload.spec, payload.id)
    if out is None:
        raise HTTPException(404, "No such saved chart.")
    return {"chart": out, "charts": charts(aid)}


@router.delete("/charts/{chart_id}")
def remove_chart(chart_id: int, request: Request):
    aid = _account(request)["id"]
    delete_chart(aid, chart_id)
    return {"charts": charts(aid)}


@router.get("/screens")
def list_screens(request: Request):
    return {"screens": screens(_account(request)["id"])}


@router.post("/screens")
def post_screen(payload: ScreenIn, request: Request):
    aid = _account(request)["id"]
    if len(json.dumps(payload.spec)) > 8000:
        raise HTTPException(400, "That screen has too much in it to save.")
    tickers = [str(t)[:12] for t in payload.tickers][:5000] if payload.tickers is not None else None
    out = save_screen(aid, payload.name, payload.spec, tickers, payload.id)
    if out is None:
        raise HTTPException(404, "No such saved screen.")
    return {"screen": out, "screens": screens(aid)}


@router.delete("/screens/{screen_id}")
def remove_screen(screen_id: int, request: Request):
    aid = _account(request)["id"]
    delete_screen(aid, screen_id)
    return {"screens": screens(aid)}
