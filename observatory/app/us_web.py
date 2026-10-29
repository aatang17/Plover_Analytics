# -*- coding: utf-8 -*-
"""Why a stock or the market moved: a web search for the news, for the box.

The filings and the price feed say what happened, never why. A reader who
asks "why did Tesla fall" or follows a price answer with "what happened"
gets the news instead of a refusal: one call to OpenAI's Responses API with
its web search tool, which searches, reads the reports and writes a short
answer with a citation on every claim.

What the reader is told, because none of it comes from Plover's data:

  * the answer says it comes from news reports found when they asked;
  * every report it used is listed with its title and a link;
  * the searches it ran are shown under "How This Was Worked Out";
  * any price figure beside it comes from Plover's own daily prices
    (app/stock_recipes.py adds those, with their usual source line).

Guard rails, because this runs on Plover's own key for the public page:

  * off unless OPENAI_API_KEY is set and neither US_ASK_WEB nor US_ASK_MODEL
    is "0";
  * at most US_ASK_WEB_DAILY_MAX searches a day (default 300);
  * one search per distinct question an hour: answers are cached here, and
    the page's response cache is told not to keep them (news moves; a cached
    answer would also be rebuilt, and paid for again, after every refresh);
  * a plain "why did Tesla move" or "any news on Apple", however it is
    worded, is one shared search per company (or index) and period for each
    part of the New York day: before the open, trading, after the close
    (app/stock_recipes.py _news_topic writes the shared question);
  * a 45-second limit; any failure falls back to the plain refusal.

Model: US_ASK_WEB_MODEL, else the box's reader model when the reader runs on
OpenAI, else the gateway's OpenAI default.
"""
import datetime
import re
import threading
import urllib.parse
from collections import OrderedDict

try:
    from zoneinfo import ZoneInfo
    _NEW_YORK = ZoneInfo("America/New_York")
except Exception:                                                  # noqa: BLE001 — no tz data: UTC−5 is near enough
    _NEW_YORK = datetime.timezone(datetime.timedelta(hours=-5))

from . import us_ask_model
from .assistant import gateway

TIMEOUT_SECONDS = 45
MAX_SOURCES = 6
_LOCK = threading.Lock()
_CACHE = OrderedDict()
_CACHE_MAX = 500
_INFLIGHT = {}            # key -> Event: a second reader asking while the first search runs waits for it
STATS = {"day": None, "calls": 0, "failed": 0, "cached": 0, "shared": 0, "searches": 0, "tokens_in": 0, "tokens_out": 0}


class WebError(RuntimeError):
    """The search could not answer: off, over the cap, failed or empty."""


def config():
    """(key, model) or None when web search is off."""
    if us_ask_model._env("US_ASK_WEB") == "0" or us_ask_model._env("US_ASK_MODEL") == "0":   # the box's models are off
        return None
    key = us_ask_model._env("OPENAI_API_KEY")
    if not key:
        return None
    model = us_ask_model._env("US_ASK_WEB_MODEL")
    if not model:
        reader = us_ask_model.provider()
        model = reader[2] if reader and reader[0] == "openai" else gateway.default_model("openai")
    return key, model


def available():
    return config() is not None and _under_cap()


def daily_max():
    try:
        return int(us_ask_model._env("US_ASK_WEB_DAILY_MAX") or 300)
    except ValueError:
        return 300


def _today_stats():
    today = datetime.date.today().isoformat()
    if STATS["day"] != today:
        STATS.update(day=today, calls=0, failed=0, cached=0, shared=0, searches=0, tokens_in=0, tokens_out=0)


def _under_cap():
    with _LOCK:
        _today_stats()
        return STATS["calls"] < daily_max()


PROMPT = """You explain what happened to US-listed stocks and US markets, from news on the web. Today is %(today)s.
Search the web for recent news reports, then answer the question:
- At most three short sentences, in plain words, for an individual investor. The first sentence, under 25 words, gives the main reason; detail goes in the next two.
- Name the day or week things happened. Say where the news came from ("Reuters reported...") when it matters.
- Use only what the reports say. Never guess a reason. If the reports disagree, or nothing explains the move, say so.
- When a previous question is given, the question follows it: read it as being about the same company, index and period.
- No advice on buying or selling, no forecasts, no price targets.
- Plain text only: no markdown, no bold, no lists, no headings."""


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower()).strip(" ?.!")


_CITE = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")               # ([reuters.com](https://...))
_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://)[^)]*\)")
_BOLD = re.compile(r"\*\*|__")


def _plain(text):
    text = _CITE.sub("", text or "")
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _clean_url(url):
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))


def _site(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def read_response(out):
    """{"text", "sources": [{title, url, site}], "searches": [query]} from a
    Responses API reply."""
    texts, sources, searches, seen = [], [], [], set()
    for item in out.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for q in action.get("queries") or ([action["query"]] if action.get("query") else []):
                if q and q not in searches:
                    searches.append(q)
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") not in ("output_text", "text"):
                continue
            texts.append(c.get("text") or "")
            for a in c.get("annotations") or []:
                url = _clean_url(a.get("url")) if a.get("type") == "url_citation" else None
                if url and url not in seen and len(sources) < MAX_SOURCES:
                    seen.add(url)
                    sources.append({"title": re.sub(r"\s+", " ", a.get("title") or "").strip() or _site(url),
                                    "url": url, "site": _site(url)})
    return {"text": _plain(" ".join(texts)), "sources": sources, "searches": searches}


def market_slot(now=None):
    """The part of the New York day a shared answer lasts for: news on a
    stock changes at the open and at the close, rarely in between."""
    t = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(_NEW_YORK)
    part = "before the open" if (t.hour, t.minute) < (9, 30) else "trading" if t.hour < 16 else "after the close"
    return t.date().isoformat(), part


def search(question, prev=None, share=None):
    """The news answer to a why question: {"text", "sources", "searches",
    "model", "asked_at"}. Raises WebError when it cannot answer. `share`
    (a company or index and a period) makes the answer one every reader
    asking the same thing shares until the next part of the day."""
    cfg = config()
    if cfg is None:
        raise WebError("web search is off")
    if share:
        key_ = ("shared", market_slot(), tuple(share))
    else:
        key_ = (datetime.datetime.now().strftime("%Y-%m-%dT%H"), _norm(question), _norm(prev))
    with _LOCK:
        _today_stats()
        if key_ in _CACHE:
            _CACHE.move_to_end(key_)
            STATS["shared" if share else "cached"] += 1
            return dict(_CACHE[key_])
        wait = _INFLIGHT.get(key_)
        if wait is None:
            if STATS["calls"] >= daily_max():
                raise WebError("over the daily cap")
            STATS["calls"] += 1
            _INFLIGHT[key_] = threading.Event()
    if wait is not None:
        wait.wait(TIMEOUT_SECONDS + 5)
        with _LOCK:
            if key_ in _CACHE:
                STATS["shared" if share else "cached"] += 1
                return dict(_CACHE[key_])
        raise WebError("the same search, already running, found nothing")
    try:
        return _search(cfg, key_, question, prev)
    finally:
        with _LOCK:
            _INFLIGHT.pop(key_).set()


def _search(cfg, key_, question, prev):
    api_key, model = cfg
    body = {"model": model, "tools": [{"type": "web_search"}],
            "input": [{"role": "system", "content": PROMPT % {"today": datetime.date.today().isoformat()}},
                      {"role": "user", "content": us_ask_model.user_message(question, prev)}]}
    if us_ask_model._REASONING_MODELS.match(model):
        body["reasoning"] = {"effort": "low"}
    url = gateway.PROVIDERS["openai"]["base_url"].rstrip("/") + "/responses"
    try:
        out = us_ask_model._post(url, api_key, body, TIMEOUT_SECONDS)
    except Exception as exc:                                       # noqa: BLE001 — a timeout, a refusal, a bad key
        with _LOCK:
            STATS["failed"] += 1
        raise WebError("the search failed: %s" % type(exc).__name__)
    got = read_response(out)
    usage = out.get("usage") or {}
    with _LOCK:
        STATS["searches"] += len(got["searches"])
        STATS["tokens_in"] += int(usage.get("input_tokens") or 0)
        STATS["tokens_out"] += int(usage.get("output_tokens") or 0)
    if not got["text"] or not got["sources"]:
        with _LOCK:
            STATS["failed"] += 1
        raise WebError("the search found no reports")       # an answer with no report behind it is not shown
    got.update(model=model, asked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"))
    with _LOCK:
        _CACHE[key_] = dict(got)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return got


def status():
    cfg = config()
    with _LOCK:
        _today_stats()
        s = dict(STATS)
    s.update(enabled=cfg is not None, model=cfg[1] if cfg else None, daily_max=daily_max())
    return s
