"""The things a page tells the server about itself.

Both exist because a request log cannot see reading. A reader who opens one
page and stays on it for ten minutes makes exactly one request, so without the
page saying "still here" and then "closed after 9 minutes 40", the honest
answer to how long anyone stayed is that we do not know.

Neither endpoint stores anything on the reader's machine, so neither needs
their permission. The third endpoint here does: it is where the banner's
answer is recorded, and the only place the visitor cookie is ever issued. See
app/visits.py for what each identity means.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import seo, task_log, visits

router = APIRouter(prefix="/api/v1/visit", tags=["Traffic"],
                   include_in_schema=False)

# A beacon body is a path and a number. Anything larger is not ours.
MAX_BODY = 1024


async def _payload(request):
    """The JSON a page sent. sendBeacon sets its own content type, so the body
    is parsed rather than trusted to be declared, and a body that is not ours
    is simply ignored — this endpoint never reports an error to a page that is
    in the middle of closing."""
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001 — a dropped beacon is not an error
        return {}
    if not raw or len(raw) > MAX_BODY:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/ping")
async def ping(request: Request):
    """A page saying it is open, or reporting how long it was.

    Answers 204 whatever happens: the caller is a beacon with nowhere to put a
    response, and a page must never be slowed or broken by the counting.
    """
    data = await _payload(request)
    path = data.get("path")
    try:
        if data.get("task"):
            # a step in one of the front page's tasks (app/task_log.py)
            task_log.record(request.scope, data)
        elif data.get("closed") or data.get("early"):
            visits.record_dwell(request.scope, path, data.get("seconds"),
                                data.get("view"), early=bool(data.get("early")))
        else:
            visits.heartbeat(request.scope, path)
    except Exception as exc:  # noqa: BLE001 — counting is never fatal
        print("VISIT PING FAILED (%s)" % exc)
    # A 204 carries no body. JSONResponse(None) sent "null", and uvicorn
    # raised "Response content longer than Content-Length" on every ping.
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


# The smallest transparent GIF: what tokens.css asks for on every page.
_DRAWN_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00"
              b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
              b"\x00\x02\x02D\x01\x00;")


@router.get("/drawn")
async def drawn():
    """A page's styles asking for one invisible pixel: proof that a browser
    drew the page, which arrives even where an office network blocks the
    page's scripts. Counted as a browser by app/visits.py (DRAWN_PATH); never
    stored, so it is asked for again on every page a returning reader opens."""
    return Response(content=_DRAWN_GIF, media_type="image/gif",
                    headers={"Cache-Control": "no-store"})


@router.post("/consent")
async def consent(request: Request):
    """Record the reader's answer to the banner.

    A "yes" issues the durable id, or keeps the one they already have — saying
    yes twice must not make somebody a new reader. A "no" deletes it and is
    itself stored, because a reader who declines and is then asked again on
    every page has not really been given a choice.
    """
    data = await _payload(request)
    choice = "granted" if data.get("choice") == "granted" else "denied"
    jar = {}
    for part in (request.headers.get("cookie") or "").split(";"):
        name, sep, value = part.partition("=")
        if sep:
            jar[name.strip()] = value.strip()

    existing, since = (None, None)
    if choice == "granted":
        existing, since = visits.parse_visitor_cookie(jar.get(visits.VISITOR_COOKIE))
        if not existing:
            existing, since = visits.new_visitor_id(), None
        since = since or visits.today()

    body = {"consent": choice}
    if since:
        body["since"] = since
    response = JSONResponse(body, headers={"Cache-Control": "no-store"})
    # Secure only where the browser will accept it: a Secure cookie set over
    # plain http is dropped, which would silently break local development.
    secure = seo.is_https(request)
    response.set_cookie(visits.CONSENT_COOKIE, choice,
                        max_age=visits.CONSENT_MAX_AGE, path="/",
                        samesite="lax", secure=secure)
    if existing:
        # httponly: the id is the server's business. A script on the page has
        # no use for it, and one that is not ours must not be able to read it.
        response.set_cookie(visits.VISITOR_COOKIE,
                            visits.consent_value(existing, since),
                            max_age=visits.CONSENT_MAX_AGE, path="/",
                            samesite="lax", secure=secure, httponly=True)
    else:
        response.delete_cookie(visits.VISITOR_COOKIE, path="/")
    return response
