"""Server-side visit counting: traffic as the server itself sees it.

Script-tag analytics (Plausible, Google Analytics) are blocked by default on
the bank and fund networks this product is aimed at, so they undercount
exactly the readers that matter. Counting inside the app sees every request,
blocker or not, and needs no third party and no cookie banner.

A visitor is HMAC(daily salt, address + user agent), truncated: stable for one
UTC day so a session counts once, unlinkable across days, and not reversible
to an address. No address is ever stored. The salt lives beside the log on the
volume, so a restart does not split a day's visitors in two.

That identity deliberately dies at midnight, which makes "did this reader come
back next week" unanswerable. A reader who accepts the banner is given a
durable random id in a cookie instead, and their events are keyed on a hash of
it: the same person across days, still no address and still nothing that says
who they are. Consent is the whole difference between the two identities, so
the cookie is issued by one endpoint the reader's own click reaches, never set
by the counter as a side effect of serving a page. Declining is recorded the
same way — a stored "no" is what stops the banner asking again.

Two things need no consent and are measured for everyone: the count of readers
active in the last few minutes, which lives in memory and is never written
down, and how long a page was open, which the page reports at the moment it
closes. Neither stores anything on the reader's machine, and both are keyed on
whichever identity that reader already has.

Writes take the same shape as the admin audit trail (see admin_api.py):
append-only JSONL under data/, no DuckDB write path anywhere (CLAUDE.md,
Ingest Guardrails 5), and no failure able to reach a response. Events are
buffered and flushed in batches, so a kill loses at most the live buffer —
immaterial for a visit counter.

The counts are an estimate and the admin page says so: one office behind one
address on one browser version reads as a single visitor, while one person on
a laptop and a phone reads as two.
"""
import binascii
import datetime
import hashlib
import hmac
import json
import os
import time
from threading import Lock

from . import ask_log, db, geoip

ANALYTICS_DIR = db.DATA_DIR / "analytics"
SALT_PATH = ANALYTICS_DIR / "salt"

VISITOR_CHARS = 12
MAX_PATH_CHARS = 200

# The reader's stored choice, and the durable id that a "yes" buys. The id is
# opaque and random: it is not derived from anything about the reader, so it
# says nothing if it leaks and can be thrown away by clearing cookies.
PING_PATH = "/api/v1/visit/ping"
CONSENT_PATH = "/api/v1/visit/consent"

CONSENT_COOKIE = "pa_consent"
VISITOR_COOKIE = "pa_vid"
CONSENT_MAX_AGE = 60 * 60 * 24 * 365  # a year, then the choice is asked again
VID_CHARS = 32

# How recently a request must have arrived for its visitor to count as here
# now. Open pages send a keep-alive inside this window, so a reader sitting
# still on one page stays counted; anything without scripts drops off it.
LIVE_SECONDS = 300
LIVE_MAX = 5000

# Gap that ends a session. Thirty minutes is the industry's convention and is
# kept so the number can be compared with anyone else's.
SESSION_GAP_SECONDS = 1800

# A page reported as open for longer than this was left open, not read. Capped
# rather than dropped: the visit happened, and dropping the long tail would
# flatter the median.
MAX_DWELL_SECONDS = 3600

# Batch size and age at which the buffer is written out.
FLUSH_EVERY = 25
FLUSH_SECONDS = 60

# Ceiling on how much log a single summary will parse, so the admin page cannot
# be made slow by a year of accumulated traffic.
MAX_LINES = 500000

# Static files are requests, not visits: one page pulls a dozen and they would
# swamp every count on the page.
ASSET_SUFFIXES = (".css", ".js", ".map", ".png", ".jpg", ".jpeg", ".gif",
                  ".svg", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".txt",
                  ".xml", ".csv", ".pdf")

# Substrings of a user agent that mean "not a person". An absent agent counts
# too: real browsers always send one, so a blank belongs to the platform's
# healthcheck or a script.
BOT_MARKERS = ("bot", "crawler", "spider", "crawl", "slurp", "curl", "wget",
               "python-requests", "httpx", "go-http-client", "java/",
               "okhttp", "headless", "phantom", "monitor", "uptime",
               "pingdom", "healthcheck", "health-check", "kube-probe",
               "railway", "preview", "scraper", "fetcher", "archiver",
               "facebookexternalhit", "embedly", "feedfetcher",
               # Google's own fetchers that do not say "bot": the Search
               # Console inspection tool, the crawler for its other products,
               # the read-aloud renderer and the ownership check. Together they
               # were the largest "readership" on the site for a month.
               "google-inspectiontool", "googleother", "google-read-aloud",
               "google-site-verification", "google favicon", "google-safety",
               "lighthouse", "pagespeed", "webpagetest", "gtmetrix",
               # AI assistants fetching a page on a user's behalf. Still not a
               # reader of this site.
               "chatgpt-user", "claude-user", "perplexity-user",
               "meta-externalagent", "cohere-ai", "anthropic-ai",
               "mistralai-user", "bytespider", "omgili",
               # Libraries and tools that identify themselves honestly.
               "scrapy", "aiohttp", "python-urllib", "python/", "node-fetch",
               "axios/", "libwww", "httpclient", "postman", "insomnia",
               "dataprovider", "censys", "zgrab", "nuclei", "masscan",
               "wappalyzer", "netcraft")

# The fetchers that work for an AI product, and what each one is doing. They
# are automated traffic and are counted as such — none of them is a reader —
# but lumping them in with crawlers and uptime monitors throws away the one
# question worth asking of them: is anything out there answering questions
# about this site, and from which pages?
#
# Three purposes, and they are different facts:
#   training - taking the text to train on. No reader at the other end.
#   search   - building an index that can later cite a page. A reader may
#              follow, and arrives with the assistant's site as the referrer.
#   asked    - fetched right now because a person asked that assistant about
#              this page. The closest thing to a reader in this table, though
#              still not a visit: the person read the answer, not the page.
#
# The label and the purpose are OUR reading of each vendor's published
# documentation, not something the request states. The user agent is, as
# always, a claim anyone can copy.
AI_AGENTS = (
    ("chatgpt-user", "ChatGPT", "asked"),
    ("oai-searchbot", "OpenAI SearchBot", "search"),
    ("gptbot", "OpenAI GPTBot", "training"),
    ("claude-user", "Claude", "asked"),
    ("claude-searchbot", "Claude SearchBot", "search"),
    ("claudebot", "Anthropic ClaudeBot", "training"),
    ("anthropic-ai", "Anthropic", "training"),
    ("perplexity-user", "Perplexity", "asked"),
    ("perplexitybot", "Perplexity", "search"),
    ("duckassistbot", "DuckDuckGo Assist", "search"),
    ("mistralai-user", "Mistral", "asked"),
    ("meta-externalagent", "Meta AI", "training"),
    ("bytespider", "ByteDance", "training"),
    ("amazonbot", "Amazon", "training"),
    ("applebot", "Apple", "search"),
    ("ccbot", "Common Crawl", "training"),
    ("cohere-ai", "Cohere", "training"),
    ("diffbot", "Diffbot", "training"),
    ("youbot", "You.com", "search"),
    ("timpibot", "Timpi", "search"),
    ("omgilibot", "Webz.io", "training"),
)

# Sites where a person asks an assistant a question and can then click through
# to a source. An arrival carrying one of these as its referrer is a reader who
# reached us through an answer — the thing the AI fetchers above are only ever
# a proxy for. Matched on the host, so a country domain counts too.
AI_REFERRER_HOSTS = ("chatgpt.com", "openai.com", "claude.ai", "anthropic.com",
                     "perplexity.ai", "copilot.microsoft.com", "gemini.google.com",
                     "bard.google.com", "you.com", "phind.com", "poe.com",
                     "deepseek.com", "kagi.com", "mistral.ai", "grok.com", "x.ai")

# Substrings of a network operator's name that mean "a data centre, not a
# home or an office". Matched against the DB-IP operator name, lower-cased,
# and used only to keep such visits out of the People figure — they are still
# counted everywhere else. Undercounting is the accepted direction: a reader
# behind iCloud Private Relay leaves through Cloudflare or Akamai and is not
# counted as a person, which the admin page discloses.
#
# "google llc" rather than "google", or Google Fiber's subscribers would be
# machines. "apple" is deliberately absent for the same reason.
HOSTING_MARKERS = ("amazon", "google llc", "google cloud", "microsoft",
                   "digitalocean", "ovh", "hetzner", "linode", "akamai",
                   "cloudflare", "fastly", "scaleway", "tencent", "alibaba",
                   "huawei cloud", "oracle", "m247", "hostroyale", "hostpapa",
                   "vultr", "the constant company", "choopa", "leaseweb",
                   "contabo", "ionos", "godaddy", "psychz", "colocrossing",
                   "quadranet", "zenlayer", "datacamp", "gcore", "g-core",
                   "hostinger", "namecheap", "hostwinds", "kamatera", "upcloud",
                   "equinix", "rackspace", "servers.com", "hostkey", "selectel",
                   "timeweb", "sharktech", "ipxo", "webnx", "limestone", "nocix",
                   "frantech", "ramnode", "hivelocity", "reliablesite",
                   "hostdime", "datacenter", "data center", "hosting", "cloud",
                   "server", "vps", "dedicated", "colocation")

_SALT = []
_BUFFER = []
_LOCK = Lock()
_LAST_FLUSH = [time.monotonic()]

# Visitors already confirmed to be running a browser today. A page render pulls
# styles, scripts and images; a script fetching HTML pulls none — so an asset
# request is the evidence that separates a reader from something wearing a
# browser's user agent. One confirmation per visitor per day is recorded, never
# the assets themselves: a page load is a dozen of them and logging each would
# multiply the log for no extra information.
_BROWSER_DAY = [""]
_BROWSER_SEEN = set()
MAX_BROWSER_SEEN = 100000

# Who is on the site right now: visitor -> what they last asked for and when.
# Memory only, never written to the log — "right now" has no history, and a
# restart that empties it costs five minutes of a number nobody stores.
_LIVE = {}


def _salt():
    """The HMAC key, generated once and kept on the volume."""
    if _SALT:
        return _SALT[0]
    value = b""
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        if SALT_PATH.exists():
            with open(str(SALT_PATH), "rb") as f:
                value = f.read().strip()
        if not value:
            value = binascii.hexlify(os.urandom(32))
            with open(str(SALT_PATH), "wb") as f:
                f.write(value + b"\n")
    except OSError as exc:
        # An unwritable volume must never stop the server. A per-boot key
        # still counts today's visitors; it just cannot survive a restart.
        print("VISIT SALT UNAVAILABLE (%s): using a per-boot key" % exc)
        if not value:
            value = binascii.hexlify(os.urandom(32))
    _SALT.append(value)
    return value


def _visitor(ip, user_agent, day):
    mac = hmac.new(_salt(), ("%s|%s|%s" % (day, ip, user_agent)).encode("utf-8"),
                   hashlib.sha256)
    return mac.hexdigest()[:VISITOR_CHARS]


def _stable_visitor(vid):
    """The id of a reader who accepted the cookie, hashed the same way and the
    same width as a daily visitor, so every count downstream is blind to which
    of the two it is holding. Hashed rather than stored raw for one reason: the
    log is then useless to anyone who also has the reader's cookie."""
    mac = hmac.new(_salt(), ("vid|%s" % vid).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:VISITOR_CHARS]


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def new_visitor_id():
    return binascii.hexlify(os.urandom(VID_CHARS // 2)).decode("ascii")


def _cookies(headers):
    """Cookie header -> dict. Tolerant on purpose: a malformed cookie from
    somewhere else on the domain must not cost us the counting."""
    jar = {}
    raw = _header(headers, b"cookie")
    for part in raw.split(";"):
        name, sep, value = part.partition("=")
        if sep:
            jar[name.strip()] = value.strip()
    return jar


def consent_value(vid, since):
    """What is stored in the visitor cookie: the id, and the day it was
    issued. Carrying the day in the cookie is what makes "reader we already
    knew" answerable without keeping a register of everyone ever seen."""
    return "%s.%s" % (vid, since)


def parse_visitor_cookie(value):
    """(durable visitor id, day it was issued), or (None, None) if the cookie
    does not parse. Anything unreadable is treated as no cookie at all — the
    daily identity still counts that reader, so a mangled value loses the
    detail, never the visit."""
    vid, _, since = (value or "").partition(".")
    if not vid or not vid.isalnum() or len(vid) > 64:
        return None, None
    if len(since) != 10 or since[4] != "-" or since[7] != "-":
        since = None
    return vid, since


def _consented(jar):
    """The reader's durable id, but only where they have said yes to it."""
    if jar.get(CONSENT_COOKIE) != "granted":
        return None, None
    return parse_visitor_cookie(jar.get(VISITOR_COOKIE))


def _touch_live(visitor, path, country, network):
    """Note that this visitor is here, now. Bounded: a flood evicts the
    stalest entries rather than growing without limit.

    A path of None means "still here, on whatever you last had open" — a page
    fetching its own chart data must keep its reader alive without the live
    table claiming somebody is sitting on an API endpoint.
    """
    with _LOCK:
        held = _LIVE.get(visitor) or {}
        _LIVE[visitor] = {"at": time.time(), "path": path or held.get("path"),
                          "country": country, "network": network}
        if len(_LIVE) > LIVE_MAX:
            for key in sorted(_LIVE, key=lambda k: _LIVE[k]["at"])[:len(_LIVE) - LIVE_MAX]:
                _LIVE.pop(key, None)


def live():
    """Readers active in the last LIVE_SECONDS, and what they have open.

    Counted from memory, so this is the one figure on the traffic page that
    owes nothing to the log — and the one that cannot be asked about the past.
    """
    cutoff = time.time() - LIVE_SECONDS
    with _LOCK:
        rows = [(v, dict(d)) for v, d in _LIVE.items() if d["at"] >= cutoff]
        for key in [v for v, d in _LIVE.items() if d["at"] < cutoff - LIVE_SECONDS]:
            _LIVE.pop(key, None)  # swept here, so no timer has to exist
    pages, countries = {}, {}
    for _, d in rows:
        if d.get("path"):
            pages[d["path"]] = pages.get(d["path"], 0) + 1
        if d.get("country"):
            countries[d["country"]] = countries.get(d["country"], 0) + 1

    def top(source):
        out = [{"key": k, "visitors": n} for k, n in source.items()]
        out.sort(key=lambda r: (-r["visitors"], r["key"]))
        return out[:15]

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return {
        "at": now.isoformat(timespec="seconds") + "Z",
        "window_seconds": LIVE_SECONDS,
        "visitors": len(rows),
        "pages": top(pages),
        "countries": top(countries),
    }


def record_dwell(scope, path, seconds, view=None):
    """One page, closed, after this many seconds visible. Reported by the page
    itself at the moment it goes away — the server cannot see reading, only
    requests, and a reader on one page for ten minutes makes none.

    The report is also the evidence that a script ran in a real browser,
    which is one of the three tests behind the People figure in summary()."""
    try:
        seconds = int(float(seconds))
    except (TypeError, ValueError):
        return
    if seconds <= 0:
        return
    seconds = min(seconds, MAX_DWELL_SECONDS)
    headers = [(k.lower(), v) for k, v in scope.get("headers") or ()]
    user_agent = _header(headers, b"user-agent")
    if _is_bot(user_agent):
        return
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ip = _client_ip(scope, _header(headers, b"x-forwarded-for"))
    visitor = identify(ip, user_agent, headers, now.strftime("%Y-%m-%d"))[0]
    event = {
        "at": now.isoformat(timespec="seconds") + "Z",
        "visitor": visitor,
        "kind": "dwell",
        "path": (path or "/")[:MAX_PATH_CHARS],
        "seconds": seconds,
        "bot": False,
    }
    # Which page view this belongs to. A page reports again every time it is
    # hidden, so one reading can arrive three times; the id is what lets the
    # longest report win instead of all three being counted as separate reads.
    if view:
        event["view"] = str(view)[:16]
    _record(event)


def heartbeat(scope, path):
    """A page saying it is still open. Touches the live registry and writes
    nothing: a keep-alive every minute would otherwise be the largest thing in
    the log and would tell us nothing the dwell record does not."""
    headers = [(k.lower(), v) for k, v in scope.get("headers") or ()]
    user_agent = _header(headers, b"user-agent")
    if _is_bot(user_agent):
        return
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ip = _client_ip(scope, _header(headers, b"x-forwarded-for"))
    geoip.prepare()
    country, network = geoip.lookup(ip)
    visitor = identify(ip, user_agent, headers, now.strftime("%Y-%m-%d"))[0]
    _touch_live(visitor, (path or "/")[:MAX_PATH_CHARS], country, network)


def identify(ip, user_agent, headers, day):
    """(visitor id, the day their cookie was issued or None, consent state).

    One place decides which of the two identities a request carries, so the
    counter, the keep-alive and the dwell record can never disagree about who
    somebody is.
    """
    jar = _cookies(headers)
    vid, since = _consented(jar)
    if vid:
        return _stable_visitor(vid), since, "granted"
    choice = jar.get(CONSENT_COOKIE)
    return (_visitor(ip, user_agent, day), None,
            "denied" if choice == "denied" else None)


def _header(headers, name):
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return ""


def _client_ip(scope, forwarded):
    """The reader's address, which behind Railway's proxy is the first hop of
    X-Forwarded-For. Used only as HMAC input, never stored or trusted."""
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _referrer_host(referer, host):
    """(sending site, came from our own pages).

    Only the sending host is kept: enough to tell Substack from a search
    engine, without recording the page someone came from. The port is stripped
    from both sides before comparing, or every click inside a host:port
    deployment reads as an external referral.

    The second value is what separates the two ways a request can carry no
    site: one of our own pages fetching its chart data, and somebody arriving
    with no referring link at all. Recording only the first made those
    identical, so direct arrivals were invisible and in-page fetches were
    counted as API use.
    """
    if not referer:
        return None, False
    rest = referer.split("://", 1)[-1]
    sender = rest.split("/", 1)[0].split("@")[-1].split(":")[0].lower()
    if not sender:
        return None, False
    if sender == (host or "").split(":")[0].lower():
        return None, True
    return sender[:MAX_PATH_CHARS], False


def _is_bot(user_agent):
    if not user_agent.strip():
        return True
    agent = user_agent.lower()
    for marker in BOT_MARKERS:
        if marker in agent:
            return True
    return False


def ai_agent(user_agent):
    """(label, purpose) for an AI product's fetcher, or (None, None).

    Checked in AI_AGENTS order, which puts the specific names first: a
    user-initiated fetch and a training crawler from the same vendor share a
    prefix, and the wrong one matching would report a reader's question as a
    scrape.
    """
    agent = (user_agent or "").lower()
    if not agent:
        return None, None
    for marker, label, purpose in AI_AGENTS:
        if marker in agent:
            return label, purpose
    return None, None


def is_ai_referrer(host):
    """Did this arrival come from an assistant's answer?"""
    if not host:
        return False
    name = host.lower()
    for domain in AI_REFERRER_HOSTS:
        if name == domain or name.endswith("." + domain):
            return True
    return False


def is_hosting(network):
    """True when the network operator is a cloud, host or CDN — somewhere a
    machine lives, not a reader. None (address not placed) is not hosting, but
    it is not a person either: the People figure requires a placed network."""
    if not network:
        return False
    name = network.lower()
    for marker in HOSTING_MARKERS:
        if marker in name:
            return True
    return False


def _kind(path):
    """What was asked for. None means "do not count at all"."""
    if path.startswith("/admin") or path.startswith("/desk") or path.startswith("/clip"):
        return None  # the console, the research desk and Send to Plover are not traffic
    if path.startswith("/healthz"):
        return None  # the proxy asks every second; that is not a reader
    if path.startswith(PING_PATH) or path.startswith(CONSENT_PATH):
        # The page telling us it is open, and the reader answering the banner.
        # Both are handled where they land; counting them here would turn one
        # reader on one page into a steady stream of API calls.
        return None
    if path.startswith("/api/v1") or path.startswith("/api/openapi"):
        return "api"
    if path.startswith("/mcp"):
        return "mcp"
    lowered = path.lower()
    for suffix in ASSET_SUFFIXES:
        if lowered.endswith(suffix):
            return "asset"
    return "page"


def _month_path(when):
    return ANALYTICS_DIR / ("visits-%s.jsonl" % when.strftime("%Y-%m"))


def _flush_locked():
    """Write the buffer out, grouped by the month each event belongs to.
    Never raises: an unwritable log must not take the site down."""
    if not _BUFFER:
        return
    batch, _BUFFER[:] = list(_BUFFER), []
    _LAST_FLUSH[0] = time.monotonic()
    by_month = {}
    for event in batch:
        by_month.setdefault(event["at"][:7], []).append(event)
    try:
        ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        for month, events in by_month.items():
            path = ANALYTICS_DIR / ("visits-%s.jsonl" % month)
            with open(str(path), "a", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("VISIT LOG WRITE FAILED (%s): %d event(s) dropped" % (exc, len(batch)))


def flush():
    with _LOCK:
        _flush_locked()


def _record(event):
    with _LOCK:
        _BUFFER.append(event)
        due = (len(_BUFFER) >= FLUSH_EVERY
               or time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
        if due:
            _flush_locked()


def _confirm_browser(visitor, day):
    """True the first time today that this visitor fetches an asset."""
    with _LOCK:
        if _BROWSER_DAY[0] != day:
            _BROWSER_DAY[0] = day
            _BROWSER_SEEN.clear()
        if visitor in _BROWSER_SEEN:
            return False
        if len(_BROWSER_SEEN) >= MAX_BROWSER_SEEN:
            return False  # bounded; a busier day simply confirms fewer
        _BROWSER_SEEN.add(visitor)
        return True


def observe(scope, status, seconds=None):
    """Note one finished request. Called from the middleware's finally block,
    so every exit path is counted and none can raise into the response. A
    question to the box is also written, without the visitor, to the ask log
    (app/ask_log.py); `seconds` is how long the answer took."""
    if scope.get("obs_synthetic"):
        return  # the cache warm-up driving the app at boot, not a reader
    method = scope.get("method")
    if method not in ("GET", "POST"):
        return
    path = (scope.get("path") or "/")[:MAX_PATH_CHARS]
    kind = _kind(path)
    if kind is None:
        return
    if kind == "page" and method == "POST":
        return  # a form post is not a page read
    if kind == "asset" and method != "GET":
        return

    headers = [(k.lower(), v) for k, v in scope.get("headers") or ()]
    user_agent = _header(headers, b"user-agent")
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    day = now.strftime("%Y-%m-%d")
    ip = _client_ip(scope, _header(headers, b"x-forwarded-for"))
    # Derived here, while the address is still in hand, and stored in place of
    # it — the address itself goes no further than this function.
    geoip.prepare()
    country, network = geoip.lookup(ip)
    visitor, since, choice = identify(ip, user_agent, headers, day)

    if kind == "asset":
        if not _confirm_browser(visitor, day):
            return
        _record({
            "at": now.isoformat(timespec="seconds") + "Z",
            "visitor": visitor,
            "kind": "browser",
            "country": country,
            "network": network,
            "bot": _is_bot(user_agent),
        })
        return

    sender, internal = _referrer_host(_header(headers, b"referer"),
                                      _header(headers, b"host"))
    bot = _is_bot(user_agent)
    label, purpose = ai_agent(user_agent)
    event = {
        "at": now.isoformat(timespec="seconds") + "Z",
        "visitor": visitor,
        "path": path,
        "kind": kind,
        "status": status,
        "ref": sender,
        "internal": internal,
        "country": country,
        "network": network,
        "bot": bot,
    }
    # Which AI product asked for this, and why. Recorded on the event rather
    # than worked out when reading, so a vendor renaming its crawler next year
    # does not silently rewrite what last year's traffic was.
    if label:
        event["agent"] = label
        event["agent_purpose"] = purpose
    # Only recorded when the reader has answered the banner: the absence of
    # these two is what "has not chosen yet" looks like in the log.
    if choice:
        event["consent"] = choice
    if since:
        event["since"] = since
    _record(event)
    if not bot:
        _touch_live(visitor, path if kind == "page" else None, country, network)
        if path in ask_log.PATHS:
            ask_log.record(scope, status, seconds, now)


class VisitCounter(object):
    """ASGI middleware. Install it OUTSIDE the response cache: a cache hit
    never reaches the routers, and a reader served from memory is still a
    reader."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        seen = {"status": 0}
        began = time.monotonic()

        async def watched(message):
            if message["type"] == "http.response.start":
                seen["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, watched)
        finally:
            try:
                observe(scope, seen["status"], time.monotonic() - began)
            except Exception as exc:  # noqa: BLE001 — counting is never fatal
                print("VISIT COUNT FAILED (%s)" % exc)


# --- reading back ------------------------------------------------------------

def _log_files():
    if not ANALYTICS_DIR.exists():
        return []
    return sorted(ANALYTICS_DIR.glob("visits-*.jsonl"))


def counting_since():
    """The day counting actually began, read from the oldest log file.

    Distinct from the first event inside a window, which for any window
    starting after that day is just the window's own start — reporting that as
    "counting since" would claim the counter is younger than it is.
    """
    files = _log_files()
    if not files:
        return None
    try:
        with open(str(files[0]), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)["at"][:10]
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return None
    return None


def _months_in_window(start, end):
    months, cursor = set(), start
    while cursor <= end:
        months.add(cursor.strftime("%Y-%m"))
        cursor += datetime.timedelta(days=1)
    months.add(end.strftime("%Y-%m"))
    return months


_DAY_BASE = {}


def _epoch(at):
    """"2026-09-16T08:30:00Z" -> seconds. Written out rather than handed to
    strptime because this runs once per line and a half-million lines of
    strptime is seconds of wall clock on the admin page."""
    day = at[:10]
    base = _DAY_BASE.get(day)
    if base is None:
        stamp = datetime.datetime(int(day[:4]), int(day[5:7]), int(day[8:10]),
                                  tzinfo=datetime.timezone.utc)
        base = _DAY_BASE[day] = int(stamp.timestamp())
    return base + int(at[11:13]) * 3600 + int(at[14:16]) * 60 + int(at[17:19])


def visitor_of(event):
    return event.get("visitor") or ""


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _sessions(activity):
    """One visitor's timestamps -> the sessions they sat in.

    A session ends when nothing is asked for for half an hour. Its length is
    the span of what it contains, so a session of one page is zero seconds
    long and is counted separately rather than averaged in as a very short
    read — that is the bounce, and it is a different fact.
    """
    lengths, pages, single = [], [], 0
    for stamps in activity.values():
        stamps.sort()
        run = [stamps[0]]
        for stamp in stamps[1:]:
            if stamp - run[-1] > SESSION_GAP_SECONDS:
                lengths.append(run[-1] - run[0])
                pages.append(len(run))
                single += 1 if len(run) == 1 else 0
                run = [stamp]
            else:
                run.append(stamp)
        lengths.append(run[-1] - run[0])
        pages.append(len(run))
        single += 1 if len(run) == 1 else 0
    return lengths, pages, single


def summary(days=30, top=15):
    """Traffic over the last `days` UTC days, aggregated for the admin page.

    Humans and automated traffic are separated rather than merged: the
    headline counts exclude anything that says it is a bot, and the bot total
    is reported beside them so a crawl is never mistaken for readership.

    Anything that does not say so is counted as a visit, which is why the
    stricter "people" figure exists: visits that also reported a reading time
    (a script ran, so a browser rendered the page) from a network that is
    placed and is not a data centre. See `confirmed` in the result.
    """
    flush()  # so the page shows the requests that just arrived
    days = max(1, min(int(days), 365))
    today = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).date()
    start = today - datetime.timedelta(days=days - 1)
    wanted = _months_in_window(start, today)

    daily = {}
    pages, refs, countries, networks = {}, {}, {}, {}
    direct = {"views": 0, "visitors": set()}
    direct_networks, direct_pages = {}, {}
    located, unlocated = 0, 0
    visitors, bot_hits, api_calls, mcp_calls = set(), 0, 0, 0
    ask_hits = []  # (at, visitor) for each question to the box (the questions themselves are in ask_log)
    # Automated traffic that belongs to an AI product, kept apart from the rest
    # of it: which assistant, what it was doing, and which page it wanted.
    ai_agents = {}
    # Page reads that arrived from an assistant's answer — a person, not a
    # fetcher — counted separately because they are the only evidence that any
    # of the fetching ever reaches a reader.
    ai_referred = {"views": 0, "visitors": set()}
    api_in_page, api_external = 0, 0
    browsers = set()
    # Visitors whose page reported how long it was open. That report is sent
    # by a script running in the page, which a scraper taking the HTML never
    # runs — so it is the evidence that a real browser rendered the page.
    scripted = set()
    # The network each visitor was placed on. A visitor is one address on one
    # day, so this is constant for them; a cookie reader who changes networks
    # keeps the last one seen.
    visitor_network = {}
    # How long pages were open, as reported by the pages themselves, one entry
    # per page view rather than per report.
    dwell_views = {}
    # When each visitor asked for something, so sessions can be cut out of it.
    activity = {}
    # Readers carrying a cookie: the day theirs was issued, and the days they
    # have appeared on. The only two facts here that outlive a single day.
    stable_since, stable_days = {}, {}
    consent = {"granted": 0, "denied": 0, "unanswered": 0}
    first_event, last_event = None, None
    lines_read, unreadable = 0, 0

    for path in _log_files():
        month = path.name[len("visits-"):-len(".jsonl")]
        if month not in wanted:
            continue
        try:
            handle = open(str(path), encoding="utf-8")
        except OSError:
            continue
        with handle as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lines_read += 1
                if lines_read > MAX_LINES:
                    break
                try:
                    event = json.loads(line)
                    at = event["at"]
                    day = at[:10]
                except (ValueError, KeyError, TypeError):
                    unreadable += 1
                    continue
                if day < start.isoformat():
                    continue
                if first_event is None or at < first_event:
                    first_event = at
                if last_event is None or at > last_event:
                    last_event = at

                bucket = daily.setdefault(
                    day, {"visitors": set(), "pageviews": 0, "api": 0, "bots": 0})
                if event.get("bot"):
                    bot_hits += 1
                    bucket["bots"] += 1
                    label = event.get("agent")
                    if label:
                        row = ai_agents.setdefault(label, {
                            "views": 0, "visitors": set(),
                            "purpose": event.get("agent_purpose") or "",
                            "pages": {}, "last": ""})
                        row["views"] += 1
                        if event.get("visitor"):
                            row["visitors"].add(event["visitor"])
                        page = event.get("path")
                        if page:
                            row["pages"][page] = row["pages"].get(page, 0) + 1
                        if at > row["last"]:
                            row["last"] = at
                    continue

                kind = event.get("kind")
                if event.get("path") in ask_log.PATHS and event.get("visitor"):
                    ask_hits.append((at, event["visitor"]))
                # Not traffic, just the note that this visitor rendered a page.
                if kind == "browser":
                    if event.get("visitor"):
                        browsers.add(event["visitor"])
                        if event.get("network"):
                            visitor_network[event["visitor"]] = event["network"]
                    continue
                # Nor is a closing page report traffic: the page view it
                # belongs to was counted when the page was asked for.
                if kind == "dwell":
                    if event.get("visitor"):
                        scripted.add(event["visitor"])
                    seconds = event.get("seconds")
                    if isinstance(seconds, (int, float)) and seconds > 0:
                        key = event.get("view") or ("%s|%s" % (visitor_of(event), at))
                        held = dwell_views.get(key)
                        if not held or seconds > held[1]:
                            dwell_views[key] = (event.get("path") or "/", seconds)
                    continue
                visitor = event.get("visitor")
                if visitor:
                    bucket["visitors"].add(visitor)
                    visitors.add(visitor)
                    if event.get("network"):
                        visitor_network[visitor] = event["network"]
                # Older records predate the in-page flag and cannot be told
                # apart; they are left out of both splits rather than guessed at.
                knows_origin = "internal" in event
                in_page = bool(event.get("internal"))

                # A reader is somebody moving around the site: pages they
                # asked for, and the chart data their own page fetched, which
                # is the only proof a tab is still being used. Somebody pulling
                # the API from a terminal is not sitting in a session and would
                # otherwise invent hours-long ones.
                if visitor and (kind == "page"
                                or (kind == "api" and in_page)):
                    activity.setdefault(visitor, []).append(_epoch(at))
                if visitor and event.get("since"):
                    stable_since.setdefault(visitor, event["since"])
                    stable_days.setdefault(visitor, set()).add(day)

                if kind in ("api", "mcp"):
                    if kind == "api":
                        api_calls += 1
                    else:
                        mcp_calls += 1
                    bucket["api"] += 1
                    if knows_origin:
                        if in_page:
                            api_in_page += 1
                        else:
                            api_external += 1
                elif kind == "page" and (event.get("status") or 0) < 400:
                    bucket["pageviews"] += 1
                    choice = event.get("consent") or "unanswered"
                    if choice in consent:
                        consent[choice] += 1
                    page = pages.setdefault(
                        event.get("path") or "/", {"views": 0, "visitors": set()})
                    page["views"] += 1
                    if visitor:
                        page["visitors"].add(visitor)
                # How a page was reached. Page reads only: a referrer answers
                # "how did someone get here", and in-page chart fetches would
                # otherwise drown the answer.
                sender = event.get("ref")
                is_read = kind == "page" and (event.get("status") or 0) < 400
                if is_read and sender:
                    ref = refs.setdefault(sender, {"views": 0, "visitors": set()})
                    ref["views"] += 1
                    if visitor:
                        ref["visitors"].add(visitor)
                    if is_ai_referrer(sender):
                        ai_referred["views"] += 1
                        if visitor:
                            ai_referred["visitors"].add(visitor)
                elif is_read and knows_origin and not in_page:
                    # No referring link: typed, bookmarked, or opened from a
                    # mail or messaging app, which strip the header.
                    direct["views"] += 1
                    if visitor:
                        direct["visitors"].add(visitor)
                    for source, key in ((direct_networks, event.get("network")),
                                        (direct_pages, event.get("path"))):
                        if not key:
                            continue
                        row = source.setdefault(key, {"views": 0, "visitors": set()})
                        row["views"] += 1
                        if visitor:
                            row["visitors"].add(visitor)

                # Location covers page reads AND API calls: an institution
                # pulling the API is the readership worth knowing about, and a
                # page-only cut would hide it.
                place = event.get("country")
                operator = event.get("network")
                if place or operator:
                    located += 1
                else:
                    unlocated += 1
                for source, key in ((countries, place), (networks, operator)):
                    if not key:
                        continue
                    row = source.setdefault(key, {"views": 0, "visitors": set()})
                    row["views"] += 1
                    if visitor:
                        row["visitors"].add(visitor)

    # A person, for the purposes of this page: a visit whose page reported its
    # reading time (so a script ran in a real browser) from a network that is
    # placed and is not a data centre. Every part of that is evidence a
    # scraper does not usually produce; none of it is proof. Deliberately the
    # strict reading — the figure is meant to be believed, not to be large.
    hosting_visitors = set(v for v, n in visitor_network.items() if is_hosting(n))
    placed_visitors = set(visitor_network)
    people = (scripted & placed_visitors) - hosting_visitors

    # A day before counting began is UNKNOWN, not zero: padding the window
    # back to its requested length with zeros would draw weeks of invented
    # quiet. A day after it with no traffic is a true zero.
    began = counting_since() or (first_event[:10] if first_event else None)
    series = []
    cursor = start
    while cursor <= today:
        key = cursor.isoformat()
        bucket = daily.get(key)
        if began is None or key < began:
            series.append({"date": key, "visitors": None, "people": None,
                           "pageviews": None, "api_calls": None, "bot_hits": None})
        else:
            series.append({
                "date": key,
                "visitors": len(bucket["visitors"]) if bucket else 0,
                "people": len(bucket["visitors"] & people) if bucket else 0,
                "pageviews": bucket["pageviews"] if bucket else 0,
                "api_calls": bucket["api"] if bucket else 0,
                "bot_hits": bucket["bots"] if bucket else 0,
            })
        cursor += datetime.timedelta(days=1)

    dwell, page_dwell = [], {}
    for page_path, seconds in dwell_views.values():
        dwell.append(seconds)
        page_dwell.setdefault(page_path, []).append(seconds)

    session_lengths, session_pages, single_page = _sessions(activity)
    # New here means "accepted the cookie inside this window"; returning means
    # the cookie predates it. Read off the day each cookie was issued, which
    # the cookie itself carries — no register of past readers is kept.
    returning = sum(1 for since in stable_since.values() if since < start.isoformat())
    repeat = sum(1 for seen in stable_days.values() if len(seen) > 1)
    days_seen = [len(seen) for seen in stable_days.values()]

    def ranked(source, dwell_by=None):
        rows = [{"key": k, "views": v["views"], "visitors": len(v["visitors"]),
                 # Visits from something that also fetched the page's styles and
                 # images, so was rendering it rather than only reading the HTML.
                 "browser_visits": len(v["visitors"] & browsers),
                 # Visits that pass every test for a person, defined above.
                 "people": len(v["visitors"] & people)}
                for k, v in source.items()]
        if dwell_by is not None:
            for row in rows:
                seen = dwell_by.get(row["key"]) or []
                row["dwell_median"] = _median(seen)
                row["dwell_samples"] = len(seen)
        rows.sort(key=lambda r: (-r["views"], r["key"]))
        return rows[:max(1, min(int(top), 100))]

    network_rows = ranked(networks)
    for row in network_rows:
        row["hosting"] = is_hosting(row["key"])

    referrer_rows = ranked(refs)
    for row in referrer_rows:
        row["ai"] = is_ai_referrer(row["key"])

    # Assistants, busiest first. The page is the most-requested one, which is
    # what says whether anything out there is reading a particular dataset.
    agent_rows = []
    for label, row in ai_agents.items():
        by_page = sorted(row["pages"].items(), key=lambda kv: (-kv[1], kv[0]))
        agent_rows.append({
            "key": label, "purpose": row["purpose"], "views": row["views"],
            "visitors": len(row["visitors"]), "last": row["last"] or None,
            "top_page": by_page[0][0] if by_page else None,
            "pages": len(row["pages"]),
        })
    agent_rows.sort(key=lambda r: (-r["views"], r["key"]))

    files = _log_files()
    return {
        # The front page's question box: searches and questions from its own
        # log, which holds no visitor; people per day from this one.
        "ask": ask_log.summary(days, start, today, ask_hits),
        "window_days": days,
        "from": start.isoformat(),
        "to": today.isoformat(),
        "counting_since": began,
        "pageviews": sum(d["pageviews"] or 0 for d in series),
        "visitors": len(visitors),
        "api_calls": api_calls,
        "mcp_calls": mcp_calls,
        "bot_hits": bot_hits,
        "daily": series,
        "top_pages": ranked(pages, page_dwell),
        "top_referrers": referrer_rows,
        # Arrivals carrying no referring link, and the only things known about
        # them. Without this the referrer table silently omits most arrivals
        # and an empty table reads as broken detection.
        "direct": {"views": direct["views"], "visitors": len(direct["visitors"]),
                   "people": len(direct["visitors"] & people)},
        "direct_networks": ranked(direct_networks),
        "direct_pages": ranked(direct_pages),
        "api_in_page": api_in_page,
        "api_external": api_external,
        # Visits that rendered a page rather than only pulling its HTML. The
        # honest test for "was this a person": a browser fetches the styles and
        # images, a script almost never does.
        "browser_visits": len(visitors & browsers),
        # The strict figure: visits that reported a reading time from a placed,
        # non-hosting network. The parts are reported beside it so a zero can
        # be read — no script reports, no placed networks, or all data centre.
        "confirmed": {
            "people": len(people),
            "scripted": len(visitors & scripted),
            "placed": len(visitors & placed_visitors),
            "hosting": len(visitors & hosting_visitors),
        },
        # How long a page stayed open, and how long a visit lasted. The first
        # is reported by the page, the second read off the request log; both
        # are medians, because one tab left open for a day would carry an
        # average on its own.
        "dwell": {
            "samples": len(dwell),
            "median_seconds": _median(dwell),
        },
        "sessions": {
            "samples": len(session_lengths),
            "median_seconds": _median([n for n in session_lengths if n > 0]),
            "measurable": len([n for n in session_lengths if n > 0]),
            "single_page": single_page,
            "pages_median": _median(session_pages),
        },
        # Readers carrying a cookie, which is the only population that can be
        # followed from one day to the next.
        "people": {
            "known": len(stable_since),
            "returning": returning,
            "new": len(stable_since) - returning,
            "repeat": repeat,
            "days_median": _median(days_seen),
        },
        # What share of page reads came from a reader who had answered the
        # banner at all. Without it the cookie figures read as the whole
        # readership rather than the part of it that said yes.
        "consent": consent,
        # What the AI products are doing here: the fetchers, and the readers
        # who arrived from one of their answers. The second is the only one of
        # the two that is a person.
        "ai_agents": agent_rows,
        "ai_referrals": {"views": ai_referred["views"],
                         "visitors": len(ai_referred["visitors"])},
        "top_countries": ranked(countries),
        "top_networks": network_rows,
        # What share of counted traffic could be placed at all. Without it an
        # empty location table reads as "nobody came" rather than "the tables
        # were not loaded".
        "located_hits": located,
        "unlocated_hits": unlocated,
        "geoip": geoip.status(),
        "first_event": first_event,
        "last_event": last_event,
        "log_files": len(files),
        "log_bytes": sum(p.stat().st_size for p in files if p.exists()),
        "lines_read": lines_read,
        "unreadable_lines": unreadable,
        "truncated": lines_read > MAX_LINES,
    }
