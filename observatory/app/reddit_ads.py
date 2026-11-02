"""Opt-in Reddit click attribution for verified new accounts, without a pixel.

Only a click identifier and SIGN_UP event leave Plover. No email, IP, user
agent, account ID, search text or sign-in URL is sent. Disabled by default.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
import urllib.parse
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import seo

router = APIRouter(prefix="/api/v1/ads", tags=["advertising consent"])
CHOICE = "pa_reddit_consent"
CLICK = "pa_reddit_click"
TTL = 30 * 86400
_ID = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_signup_events (
  account_id INTEGER PRIMARY KEY,
  conversion_id TEXT NOT NULL,
  click_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  retry_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);
"""


def enabled():
    return (os.getenv("REDDIT_ADS_ENABLED", "").lower() in ("1", "true", "yes", "on")
            and bool(os.getenv("REDDIT_ADS_ACCESS_TOKEN"))
            and bool(re.fullmatch(r"a2_[a-z0-9]+", os.getenv("REDDIT_ADS_PIXEL_ID", ""))))


def _signature(value):
    return hmac.new(os.environ["REDDIT_ADS_ACCESS_TOKEN"].encode(),
                    value.encode(), hashlib.sha256).hexdigest()


def click_of(request):
    """A browser's readable preference alone can never authorise attribution."""
    if (not enabled() or request.headers.get("sec-gpc") == "1"
            or request.cookies.get(CHOICE) != "granted"):
        return None
    return _verified_click(request)


def _verified_click(request):
    raw = request.cookies.get(CLICK, "")
    try:
        click, expires, signature = raw.split(".")
        value = click + "." + expires
        if (_ID.fullmatch(click) and int(expires) > time.time()
                and int(expires) <= time.time() + TTL + 60
                and hmac.compare_digest(signature, _signature(value))):
            return click
    except (ValueError, TypeError):
        pass
    return None


@router.post("/reddit-consent")
async def consent(request: Request):
    if not enabled():
        raise HTTPException(404, "Advertising measurement is not enabled.")
    # Browsers send Origin on same-origin POSTs. Reject cross-site and missing
    # origins: this endpoint must not be usable to plant someone else's click.
    expected = ("https" if seo.is_https(request) else "http", request.url.netloc)
    origin = urllib.parse.urlsplit(request.headers.get("origin", ""))
    if (origin.scheme, origin.netloc) != expected:
        raise HTTPException(403, "Open cookie settings on this site and try again.")
    raw = await request.body()
    if len(raw) > 1024:
        raise HTTPException(400, "Invalid consent choice.")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid consent choice.")
    granted = body.get("choice") == "granted" and request.headers.get("sec-gpc") != "1"
    click = body.get("click_id") or ""
    if not isinstance(click, str) or (click and not _ID.fullmatch(click)):
        raise HTTPException(400, "Invalid advertising link.")
    response = JSONResponse({"consent": "granted" if granted else "denied"},
                            headers={"Cache-Control": "no-store"})
    kwargs = dict(path="/", secure=seo.is_https(request), samesite="lax")
    response.set_cookie(CHOICE, "granted" if granted else "denied", max_age=TTL, **kwargs)
    if granted and click:
        value = click + "." + str(int(time.time()) + TTL)
        response.set_cookie(CLICK, value + "." + _signature(value),
                            max_age=TTL, httponly=True, **kwargs)
    elif not granted:
        old_click = _verified_click(request)
        if old_click:
            from . import accounts
            db = accounts.conn()
            with accounts._lock:
                db.execute("DELETE FROM reddit_signup_events WHERE click_id = ?", (old_click,))
                db.commit()
        response.delete_cookie(CLICK, **kwargs)
    return response


def enqueue(db, account_id, request, now):
    """Called within the account-creation transaction, only for a new account."""
    click = click_of(request)
    if click:
        db.execute("INSERT OR IGNORE INTO reddit_signup_events "
                   "(account_id, conversion_id, click_id, created_at, retry_at) VALUES (?, ?, ?, ?, ?)",
                   (account_id, uuid.uuid4().hex, click, now, now))


def deliver(test_id=None):
    """Durable retries; lease a batch so overlapping web copies do not race.

    The stable conversion_id also makes a retry after an ambiguous timeout
    safe. Do not log request/response bodies: upstream errors may echo keys.
    """
    if not enabled():
        return
    from . import accounts
    db, now = accounts.conn(), int(time.time())
    with accounts._lock:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM reddit_signup_events WHERE created_at < ?", (now - 7 * 86400,))
        rows = db.execute("SELECT * FROM reddit_signup_events WHERE retry_at <= ? LIMIT 10", (now,)).fetchall()
        for row in rows:
            db.execute("UPDATE reddit_signup_events SET retry_at = ?, attempts = attempts + 1 "
                       "WHERE account_id = ?", (now + 120, row["account_id"]))
        db.commit()
    for row in rows:
        event = {"event_at": row["created_at"] * 1000, "action_source": "WEBSITE",
                 "event_source_url": "https://ploveranalytics.com/signin.html",
                 "type": {"tracking_type": "SIGN_UP"}, "click_id": row["click_id"],
                 "metadata": {"conversion_id": row["conversion_id"]}}
        data = {"events": [event]}
        # Explicit test calls only; no environment setting can accidentally
        # leave production deliveries in test mode.
        if test_id:
            data["test_id"] = test_id
        req = urllib.request.Request(
            "https://ads-api.reddit.com/api/v3/pixels/%s/conversion_events" % os.environ["REDDIT_ADS_PIXEL_ID"],
            data=json.dumps({"data": data}).encode(), method="POST",
            headers={"Authorization": "Bearer " + os.environ["REDDIT_ADS_ACCESS_TOKEN"],
                     "Content-Type": "application/json",
                     "User-Agent": "PloverAnalytics/1.0 (+https://ploveranalytics.com)"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise ValueError("conversion not accepted")
            with accounts._lock:
                db.execute("DELETE FROM reddit_signup_events WHERE account_id = ?", (row["account_id"],))
                db.commit()
        except Exception as exc:  # noqa: BLE001 — never cost a reader their account
            logging.getLogger(__name__).warning("Reddit signup delivery failed (%s); queued for retry",
                                                getattr(exc, "code", type(exc).__name__))
            delay = min(3600, 30 * 2 ** min(row["attempts"], 7))
            with accounts._lock:
                db.execute("UPDATE reddit_signup_events SET retry_at = ? WHERE account_id = ?",
                           (int(time.time()) + delay, row["account_id"]))
                db.commit()


async def loop():
    while True:
        try:
            await asyncio.to_thread(deliver)
        except Exception:  # noqa: BLE001 — an ads outage never affects serving
            logging.getLogger(__name__).warning("Reddit signup queue unavailable")
        await asyncio.sleep(30)
