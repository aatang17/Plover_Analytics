# -*- coding: utf-8 -*-
"""The box's reader: a small model reads every question first, code answers it.

The model is given the question, today's date and the list of question
shapes, and fills in one fixed form: which shape, which companies, which
index, which period, what to rank by. It returns that form as JSON and
nothing else. It never sees a figure and never writes one: the shape it
names is run by the same code as the word patterns, so every number still
comes from the filings and the price feed. What it read is shown on the
page ("Read as ...") so a misreading is visible.

The word patterns in app/stock_recipes.py stay as the backup: they answer
when the model is off, over its daily cap, slow or unreadable, or when its
form names a company or industry we cannot find.

Guard rails, because this runs on Plover's own key for the public page:

  * off unless a provider key is set and US_ASK_MODEL is not "0";
  * at most US_ASK_MODEL_DAILY_MAX calls a day (default 2000);
  * 300 output tokens, a 15-second limit, temperature 0 (or, for a model
    that reasons, no reasoning), the question cut at 300 characters;
  * one call per distinct question a day: readings are cached;
  * every call is counted, with its tokens, for /api/v1/us/ask/recipes.

Provider: DeepSeek when DEEPSEEK_API_KEY is set, else OpenAI; with both keys
US_ASK_PROVIDER picks one, and US_ASK_MODEL_NAME names the model. The call
is made here, with a short timeout, not through the desk's gateway, whose
two-minute limit suits long agent runs, not a search box.

Tokens. Every prompt is laid out for the providers' prompt caches: the
unchanging part (the tables, the rules) comes first and what varies (the
question, the examples chosen for it) last, so the long prefix is read from
cache (OpenAI charges half for it, DeepSeek a tenth); each kind of call
carries a prompt_cache_key so OpenAI routes it to the same cache; and the
cached share is counted in tokens_cached. Models that reason (OpenAI's
GPT-5 and later) refuse `temperature` and `max_tokens`: on that refusal the
call is sent again the way they want it, with reasoning kept low, and the
model's way is remembered. Models known to reason (gpt-5 and later, the o
series) are sent their way from the first call, so no call is wasted on a
refusal; the reader, which only fills in a form, asks for no reasoning at
all (2-3 s with gpt-6-luna against 3-5 s with low, the same form).
"""
import datetime
import json
import os
import re
import threading
import urllib.error
import urllib.request
from collections import OrderedDict

from .assistant import gateway

_LOCK = threading.Lock()
STATS = {"day": None, "calls": 0, "matched": 0, "declined": 0, "failed": 0, "cached": 0,
         "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0}
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def _env(name):
    v = os.environ.get(name, "").strip()
    if v or not os.path.exists(_ENV_FILE):
        return v
    for raw in open(_ENV_FILE, encoding="utf-8"):
        k, sep, val = raw.strip().partition("=")
        if sep and k.strip() == name:
            return val.strip().strip('"').strip("'")
    return ""


def provider():
    """(provider, key, model) or None when the layer is off."""
    if _env("US_ASK_MODEL") == "0":
        return None
    order = (("deepseek", "DEEPSEEK_API_KEY"), ("openai", "OPENAI_API_KEY"))
    want = _env("US_ASK_PROVIDER").lower()
    if want in ("deepseek", "openai"):
        order = tuple(o for o in order if o[0] == want)
    for prov, var in order:
        key = _env(var)
        if key:
            return prov, key, _env("US_ASK_MODEL_NAME") or gateway.default_model(prov)
    return None


def daily_max():
    try:
        return int(_env("US_ASK_MODEL_DAILY_MAX") or 2000)
    except ValueError:
        return 2000


def _tick(field, tokens=None):
    with _LOCK:
        today = datetime.date.today().isoformat()
        if STATS["day"] != today:
            STATS.update(day=today, calls=0, matched=0, declined=0, failed=0, cached=0, tokens_in=0, tokens_out=0,
                         tokens_cached=0)
        STATS[field] += 1
        if tokens:
            STATS["tokens_in"] += int(tokens.get("in") or 0)
            STATS["tokens_out"] += int(tokens.get("out") or 0)
            STATS["tokens_cached"] += int(tokens.get("cached") or 0)


def under_cap():
    with _LOCK:
        today = datetime.date.today().isoformat()
        return STATS["day"] != today or STATS["calls"] < daily_max()


# A reader that times out falls back to word patterns, which can answer a
# different question ("Tesla is worth $1.40tn" for "how many days did Tesla
# fall"), so a few more seconds of waiting is the better trade.
TIMEOUT_SECONDS = 15
_CACHE = OrderedDict()
_CACHE_MAX = 5000
FORM_KEYS = ("shape", "companies", "industry", "manager", "figure", "rank_by", "order", "universe", "benchmark",
             "from", "to", "years", "count", "min_value", "refuse", "read_as", "question")

RULES = """You read one question about US-listed stocks and fill in a form. Today is %(today)s.
Reply with one JSON object, these keys only:
{"question": <the whole question in plain words: the question itself, or for a follow-up ("and Microsoft?", "what about 2025?", "same for the Dow") the previous question with the new words put in its place>,
 "shape": <a shape id below, or null>,
 "companies": [{"as_written": <the exact words in the question that name the company>, "ticker": <its US ticker, or null>}, ... in order],
 "industry": <industry words, e.g. "banks", "carmakers", "semiconductors", or null>,
 "manager": <an investment manager's name, for 13F questions, or null>,
 "figure": <the filed figure asked for, in plain words (revenue, debt, research spending, gross margin, free cash flow ...), or null>,
 "rank_by": <one of: %(rank_keys)s; or null>,
 "order": "desc" for highest/best/top/largest, "asc" for lowest/worst/cheapest/smallest, or null,
 "universe": <the index whose members are ranked or broken down: %(universes)s; or null>,
 "benchmark": <the index or fund a stock is compared against: %(benchmarks)s; or null>,
 "from": "YYYY-MM-DD" or null, "to": "YYYY-MM-DD" or null,
 "years": <years of history asked for, or null>,
 "count": <how many to list, or null>,
 "min_value": <a dollar market-value floor as a number, or null>,
 "refuse": null, or "why" (asks why something happened), "advice" (whether to buy or sell), "forecast" (estimates, targets, the future), "fragment" (a follow-up that leans on a previous question, and none is given), "no_fit" (nothing below answers it),
 "read_as": <one short plain sentence saying what will be shown, naming the period>}
Rules:
- A follow-up names only what changes ("and Microsoft?", "what about last year?") and leans on the previous question, which is given with it when there is one. Write the whole question in "question" and fill the rest of the form from that whole question. With no previous question, a follow-up is "refuse": "fragment".
- Never invent a company, number or date the question does not imply. "as_written" is copied from the question (the whole question, for a follow-up); the ticker is yours ("google" is GOOGL, "meta" is META).
- "in 2026" means from 2026-01-01 (to 2026-12-31 only if that year is over); "this year" or "year to date" means from %(year)d-01-01; "past year" means from one year before today; no period named for a performance question means from %(year)d-01-01.
- "performers", "best", "winners", "gainers", "returns", "performed" in a ranking mean rank_by price_change; "worst", "losers", "laggards" mean price_change with order asc.
- "S&P", "S&P 500", "SPX", "SPY" are sp500; "Dow", "Dow Jones", "DJIA" are dow; "Nasdaq" or "Nasdaq 100" is nasdaq100; "the market" as a comparison is sp500.
- Did named stocks beat, lag, outperform or underperform an index: shape vs_benchmark, the stocks in companies, the index in benchmark.
- How much of an index's move came from a stock, or what drove an index: shape index_contribution, the index in universe, the stock (if any) in companies.
- Rank or list stocks within an index: shape market_rank with universe set; within an industry: industry_rank.
- Use a shape only when it answers the whole question. Counts ("how many"), shares ("what percentage"), averages and
  medians, sectors, lists filtered on two or more conditions, and anything else no shape answers: shape "steps".
- The path of a price, not just its change over one period, is shape "steps", even when it names a stock or an index:
  months, weeks or days up or down, how many months one stock beat another, best or worst days, the biggest fall from
  a high (drawdown), the highest or lowest close.
Shapes:
%(shapes)s"""


def _norm(question):
    return re.sub(r"\s+", " ", (question or "").strip().lower()).strip(" ?.!")


# how each OpenAI model wants to be called, learnt from its first refusal:
# {"model": {"temperature": bool, "reasoning": bool}}
_WAYS = {}
REASONING_HEADROOM = 800          # output tokens a reasoning model may spend thinking before it writes


_REASONING_MODELS = re.compile(r"^(gpt-[5-9]|gpt-\d{2}|o\d)", re.I)


def _way(prov, model):
    """How to call `model`: what it taught us, else a guess from its name."""
    if model in _WAYS:
        return dict(_WAYS[model])
    if prov != "deepseek" and _REASONING_MODELS.match(model or ""):
        return {"temperature": False}
    return {}


def _body(prov, model, messages, max_tokens, cache_key, way, effort="low"):
    body = {"model": model, "messages": messages, "response_format": {"type": "json_object"}}
    if prov == "deepseek":
        body.update(max_tokens=max_tokens, temperature=0)
        return body
    if cache_key:
        body["prompt_cache_key"] = cache_key
    if way.get("temperature", True):
        body["temperature"] = 0
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_completion_tokens"] = max_tokens + REASONING_HEADROOM
        if way.get("reasoning", True):
            body["reasoning_effort"] = effort
    return body


def _post(url, key, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _complete(prov, key, model, messages, max_tokens=300, timeout=None, cache_key=None, effort="low"):
    """One short JSON-mode call; {"text", "usage": {in, out, cached}}. Raises on any failure.
    `cache_key` names the kind of prompt (reader, steps, review) for the provider's cache;
    `effort` is the reasoning a model that reasons may spend ("none" for filling in a form)."""
    url = gateway.PROVIDERS["deepseek" if prov == "deepseek" else "openai"]["base_url"].rstrip("/") + "/chat/completions"
    way = _way(prov, model)
    for _ in range(3):
        try:
            out = _post(url, key, _body(prov, model, messages, max_tokens, cache_key, way, effort), timeout or TIMEOUT_SECONDS)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 400 or prov == "deepseek":
                raise
            text = exc.read().decode("utf-8", "replace")[:600]
            # a reasoning model: no temperature, no max_tokens; then, if it has no reasoning setting, none
            # (a model guessed from its name to reason that does not refuses reasoning_effort, and is then
            # called with neither temperature nor reasoning, which every model accepts)
            if way.get("temperature", True) and re.search(r"temperature|max_tokens", text):
                way["temperature"] = False
            elif not way.get("temperature", True) and way.get("reasoning", True) and "reasoning" in text:
                way["reasoning"] = False
            else:
                raise
            _WAYS[model] = dict(way)
    else:
        raise RuntimeError("the model refused every way of calling it")
    msg = ((out.get("choices") or [{}])[0].get("message") or {})
    usage = out.get("usage") or {}
    # OpenAI reports the cached prefix under prompt_tokens_details, DeepSeek as prompt_cache_hit_tokens
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0
    return {"text": msg.get("content") or "", "usage": {"in": usage.get("prompt_tokens") or 0,
                                                          "out": usage.get("completion_tokens") or 0, "cached": cached}}


def prompt(shapes_text, rank_keys, universes, benchmarks, today=None):
    today = today or datetime.date.today()
    return RULES % {"today": today.isoformat(), "year": today.year, "rank_keys": ", ".join(rank_keys),
                    "universes": ", ".join(universes), "benchmarks": ", ".join(benchmarks), "shapes": shapes_text}


def user_message(question, prev=None):
    """What the reader is given: the question, with the previous question
    above it when the page sent one, so a follow-up can be read whole."""
    q = (question or "").strip()[:300]
    if prev:
        return "Previous question: %s\nQuestion: %s" % ((prev or "").strip()[:300], q)
    return q


def read(question, system_prompt, prev=None):
    """The model's form for a question, or None when the layer is off,
    capped or failed. A form with "shape": null carries "refuse". `prev` is
    the reader's previous question, for a follow-up."""
    cfg = provider()
    if cfg is None:
        return None
    key_ = (datetime.date.today().isoformat(), _norm(question), _norm(prev) if prev else "")
    with _LOCK:
        if key_ in _CACHE:
            _CACHE.move_to_end(key_)
            STATS["cached"] = STATS.get("cached", 0) + 1
            return dict(_CACHE[key_])
    if not under_cap():
        return None
    prov, key, model = cfg
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message(question, prev)}]
    _tick("calls")
    try:
        out = _complete(prov, key, model, messages, cache_key="plover-us-reader", effort="none")
    except Exception:                                             # noqa: BLE001 — a timeout, a refusal, a bad reply
        _tick("failed")
        return None
    tokens = out.get("usage") or {}
    m = re.search(r"\{.*\}", out.get("text") or "", re.S)
    try:
        form = json.loads(m.group(0)) if m else None
    except ValueError:
        form = None
    if not isinstance(form, dict):
        _tick("failed", tokens)
        return None
    form = {k: form.get(k) for k in FORM_KEYS}
    _tick("matched" if form.get("shape") else "declined", tokens)
    with _LOCK:
        _CACHE[key_] = dict(form)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return form


def status():
    cfg = provider()
    with _LOCK:
        s = dict(STATS)
    s.update(enabled=cfg is not None, provider=cfg[0] if cfg else None, model=cfg[2] if cfg else None,
             daily_max=daily_max())
    return s
