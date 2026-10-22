"""Release-versioned HTTP cache for the read API.

Every GET under /api/v1 is a pure function of the published release: the data
only changes when ingest rewrites the DuckDB file, and in production that
happens at boot, before uvicorn starts. So a response stays valid until the
file changes — which is what both halves of this middleware exploit.

Server side: the cache stores the *serialized* body, not the query result.
The expensive part of a large response is JSON encoding and compression, not
the DuckDB scan, so caching after the encoder is what actually saves time; a
repeat hit costs one dict lookup and no compression work.

Client side: the same entry carries an ETag and Cache-Control, so a browser
that has the payload already revalidates with an empty 304 instead of pulling
the megabyte down again.

Entries are keyed by (path, query string) and stamped with the database
version. A new ingest changes the stamp, which invalidates every entry at
once — nothing here can serve data from a superseded release.
"""
import gzip
import hashlib
from collections import OrderedDict
from threading import Lock

# Level 6, not gzip's default 9: on the largest payload here 9 costs ~110ms of
# CPU to save 6% over 6's ~17ms, and that cost lands on the first visitor after
# a deploy. Every later visitor is served the stored bytes either way.
GZIP_LEVEL = 6
MIN_GZIP_BYTES = 1024

# The cache key includes user-supplied query strings (?q=…), so it is bounded
# by count and by total size as well as by body size — a search box cannot
# grow it without limit. 64 entries was set in August 2026 for two datasets;
# by October start-up pre-built 497 paths (121 cacheable, 53 MB in all), so
# all but the last 64 were evicted as soon as they were made and most series
# lists were built again on their first visit.
MAX_ENTRIES = 512
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024

# How long a browser may reuse a response without asking. The data is monthly;
# a minute of staleness after a release is immaterial, and revalidation after
# that is a 304 with no body.
MAX_AGE = 60
STALE_WHILE_REVALIDATE = 600

# Paths that must never be cached, however cacheable they look.
#
# Entries are invalidated by the database file changing, which in production
# happens only at boot. That is right for data — a release is immutable — and
# exactly wrong for the health report, whose whole job is to answer "how old
# is this now?". Cached, it froze the boot-time answer for the life of the
# process: `checked_at`, the day counts and every `stale` flag stopped moving,
# so a dataset that went stale at midnight went on reporting `ok` until the
# next deploy. The endpoint meant to reveal staleness was the one thing that
# could not go stale.
NEVER_CACHE = ("/api/v1/catalog/health",)
# Whole namespaces that are per-reader rather than per-dataset. Excluded by
# prefix, not by exact path, so that adding a second account endpoint cannot
# accidentally put one reader's answer in a cache everybody is served from.
NEVER_CACHE_PREFIXES = ("/api/v1/account/", "/api/v1/assistant/")
# Under an excluded prefix but safe to cache: the front page's preview needs no account and
# answers from the same data as the box, so a repeat question is served from memory (the
# reader model is not called again, and the step route's SQL is not rewritten).
CACHE_ANYWAY = ("/api/v1/assistant/preview",)


async def warm(app, paths):
    """Fill the cache by driving `app` directly, before it serves anyone.

    Without this the first visitor after every deploy pays the full cost of
    the largest payload — query, encode and compress — while everyone after
    them is served from memory. Requests are synthesised as ASGI scopes rather
    than issued over the network so this needs no client library and no port.

    Failures are ignored: a warm-up is an optimisation, never a reason for the
    server not to come up.
    """
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    for path in paths:
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode("ascii"), "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"localhost"), (b"accept-encoding", b"gzip")],
            "client": ("127.0.0.1", 0), "server": ("localhost", 80),
            # Not a reader: app/visits.py skips these, or every boot would
            # write hundreds of synthetic hits into the visit log.
            "obs_synthetic": True,
        }
        try:
            await app(scope, receive, send)
        except Exception:  # noqa: BLE001 — see docstring
            pass


def _size(entry):
    return len(entry.body) + len(entry.gz or b"")


class _Entry(object):
    __slots__ = ("version", "etag", "content_type", "body", "gz")

    def __init__(self, version, etag, content_type, body, gz):
        self.version = version
        self.etag = etag
        self.content_type = content_type
        self.body = body
        self.gz = gz


class ResponseCache(object):
    """ASGI middleware. Caches JSON GETs under `prefix` until `version` changes.

    Install it *inside* GZipMiddleware: on a hit this sends its own
    pre-compressed body with Content-Encoding set, which GZipMiddleware then
    passes through untouched instead of compressing it a second time.
    """

    def __init__(self, app, version, prefix="/api/v1", max_age=MAX_AGE):
        self.app = app
        self.version = version
        self.prefix = prefix
        self.max_age = max_age
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    async def __call__(self, scope, receive, send):
        # HEAD is deliberately not cached: it shares a URL with GET but carries
        # no body, and one key must not hold both.
        if (scope["type"] != "http" or scope.get("method") != "GET"
                or not scope["path"].startswith(self.prefix)
                or scope["path"] in NEVER_CACHE
                or (scope["path"].startswith(NEVER_CACHE_PREFIXES) and scope["path"] not in CACHE_ANYWAY)):
            await self.app(scope, receive, send)
            return

        key = scope["path"] + "?" + scope["query_string"].decode("latin-1")
        version = self.version()
        entry = self._get(key, version)
        if entry is None:
            entry = await self._fill(key, version, scope, receive, send)
            if entry is None:
                return  # not cacheable; the response has already been sent
        await self._respond(entry, scope, send)

    def _get(self, key, version):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.version != version:
                del self._entries[key]
                self._bytes -= _size(entry)
                return None
            self._entries.move_to_end(key)
            return entry

    def _put(self, key, entry):
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= _size(old)
            self._entries[key] = entry
            self._bytes += _size(entry)
            while len(self._entries) > 1 and (len(self._entries) > MAX_ENTRIES
                                              or self._bytes > MAX_TOTAL_BYTES):
                _, gone = self._entries.popitem(last=False)
                self._bytes -= _size(gone)

    async def _fill(self, key, version, scope, receive, send):
        """Run the app, buffering the response so it can be stored.

        Returns the new entry, or None when the response turned out not to be
        cacheable — in which case it has already been forwarded verbatim.
        """
        state = {"status": None, "headers": None, "passthrough": False,
                 "flushed": False}
        chunks = []

        async def capture(message):
            if state["passthrough"]:
                await send(message)
                return

            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = message["headers"]
                ctype = b""
                for name, value in message["headers"]:
                    if name.lower() == b"content-type":
                        ctype = value
                        break
                # Only plain successful JSON is cacheable. Errors, redirects and
                # streamed media go straight out, still streaming.
                if message["status"] != 200 or not ctype.startswith(b"application/json"):
                    state["passthrough"] = True
                    await send(message)
                    return
                return  # hold the head until the body size is known

            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if sum(len(c) for c in chunks) <= MAX_BODY_BYTES:
                    return
                # Oversized: give up on caching and release what we withheld.
                state["passthrough"] = True
                await send({"type": "http.response.start",
                            "status": state["status"], "headers": state["headers"]})
                await send({"type": "http.response.body", "body": b"".join(chunks),
                            "more_body": message.get("more_body", False)})
                return

            await send(message)  # anything else (trailers) passes through

        await self.app(scope, receive, capture)
        if state["passthrough"] or state["status"] is None:
            return None

        body = b"".join(chunks)
        ctype = b"application/json"
        for name, value in state["headers"]:
            if name.lower() == b"content-type":
                ctype = value
                break
        gz = gzip.compress(body, GZIP_LEVEL) if len(body) >= MIN_GZIP_BYTES else None
        etag = b'"' + hashlib.sha256(body).hexdigest()[:32].encode("ascii") + b'"'
        entry = _Entry(version, etag, ctype, body, gz)
        self._put(key, entry)
        return entry

    async def _respond(self, entry, scope, send):
        req = {}
        for name, value in scope["headers"]:
            req[name.lower()] = value

        headers = [
            (b"content-type", entry.content_type),
            (b"etag", entry.etag),
            (b"cache-control", ("public, max-age=%d, stale-while-revalidate=%d"
                                % (self.max_age, STALE_WHILE_REVALIDATE)).encode("ascii")),
            (b"vary", b"Accept-Encoding"),
        ]

        if entry.etag in [t.strip() for t in req.get(b"if-none-match", b"").split(b",")]:
            await send({"type": "http.response.start", "status": 304, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        body = entry.body
        if entry.gz is not None and b"gzip" in req.get(b"accept-encoding", b"").lower():
            body = entry.gz
            headers.append((b"content-encoding", b"gzip"))
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})
