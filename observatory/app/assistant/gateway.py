"""The model gateway: one call shape over Anthropic and OpenAI-compatible APIs.

The customer's key, never ours. Two wire formats are spoken with the standard
library's urllib, so this module needs no SDK and behaves the same on the
laptop's Python 3.9 and the container's 3.12.

The neutral shapes the runner uses:

  messages: [{"role": "system"|"user"|"assistant"|"tool", "content": str,
              (a user message may add "images": [{"media_type", "data" (base64)}])
              "tool_calls": [{"id", "name", "args"}]   # assistant only
              "tool_call_id": str, "name": str}]         # tool only
  tools:    [{"name", "description", "parameters": <JSON schema>}]

  complete(...) -> {"text": str, "tool_calls": [{"id","name","args"}],
                    "usage": {"in": int, "out": int, "cached": int}, "stop": str}

"in" is every input token the model read this turn, cached or not, so a
budget counts the same work on every provider; "cached" is the part of it
served from the provider's prompt cache, which is billed at a fraction of
the normal rate.

A run sends the whole conversation again on every step, so each step's
input repeats everything before it. Anthropic caches that repeated part only
when the request asks: every Anthropic request here marks the end of the
system prompt (tools and system, the same on every step of a run) and asks
for automatic caching of the growing conversation after it. OpenAI and
DeepSeek cache repeated prompts on their own; their usage says how much.
"""
import json
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": {"label": "Anthropic", "default_model": "claude-sonnet-4-5",
                  "base_url": "https://api.anthropic.com"},
    "openai": {"label": "OpenAI", "default_model": "gpt-4o-mini",
               "base_url": "https://api.openai.com/v1"},
    "deepseek": {"label": "DeepSeek", "default_model": "deepseek-chat",
                 "base_url": "https://api.deepseek.com"},
}
TIMEOUT_SECONDS = 120
ANTHROPIC_VERSION = "2023-06-01"


class GatewayError(Exception):
    """The provider could not be reached, refused the key, or answered oddly."""


def default_model(provider):
    p = PROVIDERS.get(provider)
    return p["default_model"] if p else ""


def _post(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise GatewayError("The model API answered %s. %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise GatewayError("The model API could not be reached (%s)." % exc.reason)
    except (ValueError, TimeoutError) as exc:
        raise GatewayError("The model API answered something unreadable (%s)." % exc)


# --------------------------------------------------------------- anthropic

def _anthropic_messages(messages):
    system = []
    out = []
    for m in messages:
        role = m["role"]
        if role == "system":
            system.append(m["content"])
        elif role == "user":
            blocks = [{"type": "text", "text": m["content"]}]
            for img in m.get("images") or []:
                blocks.append({"type": "image", "source": {"type": "base64",
                               "media_type": img["media_type"], "data": img["data"]}})
            out.append({"role": "user", "content": blocks})
        elif role == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                               "input": tc.get("args") or {}})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m["content"]}
            # consecutive tool results belong in one user turn
            if out and out[-1]["role"] == "user" and out[-1]["content"] and \
                    out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system), out


def _anthropic(key, model, messages, tools, max_tokens, base_url):
    system, msgs = _anthropic_messages(messages)
    # Prompt caching: an explicit breakpoint after the system prompt, and the
    # top-level field for the conversation that grows behind it.
    body = {"model": model, "max_tokens": max_tokens, "messages": msgs,
            "cache_control": {"type": "ephemeral"}}
    if system:
        body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"],
                          "input_schema": t["parameters"]} for t in tools]
    resp = _post((base_url or PROVIDERS["anthropic"]["base_url"]) + "/v1/messages",
                 {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}, body)
    text = []
    calls = []
    for block in resp.get("content") or []:
        if block.get("type") == "text":
            text.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            calls.append({"id": block.get("id"), "name": block.get("name"),
                          "args": block.get("input") or {}})
    usage = resp.get("usage") or {}
    # input_tokens counts only what was neither read from nor written to the cache
    read = int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    return {"text": "".join(text).strip(), "tool_calls": calls,
            "usage": {"in": int(usage.get("input_tokens") or 0) + read + written,
                      "out": int(usage.get("output_tokens") or 0), "cached": read},
            "stop": resp.get("stop_reason") or ""}


# ----------------------------------------------------- openai-compatible

def _openai_messages(messages):
    out = []
    for m in messages:
        role = m["role"]
        if role == "user" and m.get("images"):
            out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}] + [
                {"type": "image_url", "image_url": {
                    "url": "data:%s;base64,%s" % (img["media_type"], img["data"])}}
                for img in m["images"]]})
        elif role in ("system", "user"):
            out.append({"role": role, "content": m["content"]})
        elif role == "assistant":
            entry = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                entry["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc.get("args") or {})}}
                    for tc in m["tool_calls"]]
            out.append(entry)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": m["content"]})
    return out


def _openai(key, model, messages, tools, max_tokens, base_url):
    body = {"model": model, "messages": _openai_messages(messages), "max_tokens": max_tokens}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in tools]
        body["tool_choice"] = "auto"
    resp = _post((base_url or PROVIDERS["openai"]["base_url"]).rstrip("/") + "/chat/completions",
                 {"Authorization": "Bearer " + key}, body)
    try:
        choice = resp["choices"][0]
        msg = choice.get("message") or {}
    except (KeyError, IndexError, TypeError):
        raise GatewayError("The model API answered without a choice.")
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"_unparseable": fn.get("arguments")}
        calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
    usage = resp.get("usage") or {}
    # prompt_tokens already includes the cached part; OpenAI reports it under
    # prompt_tokens_details, DeepSeek as prompt_cache_hit_tokens
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") \
        or usage.get("prompt_cache_hit_tokens") or 0
    return {"text": (msg.get("content") or "").strip(), "tool_calls": calls,
            "usage": {"in": int(usage.get("prompt_tokens") or 0),
                      "out": int(usage.get("completion_tokens") or 0), "cached": int(cached)},
            "stop": choice.get("finish_reason") or ""}


# ------------------------------------------------------------------ public

def complete(provider, key, model, messages, tools=None, max_tokens=1500, base_url=None):
    """One model turn. Raises GatewayError on any failure."""
    if not key:
        raise GatewayError("No model key is stored for this desk.")
    if provider == "anthropic":
        return _anthropic(key, model, messages, tools or [], max_tokens, base_url)
    if provider in ("openai", "deepseek", "custom"):
        url = base_url or PROVIDERS.get(provider, PROVIDERS["openai"])["base_url"]
        return _openai(key, model, messages, tools or [], max_tokens, url)
    raise GatewayError("Unknown model provider '%s'." % provider)


def ping(provider, key, model, base_url=None):
    """The cheapest possible call, to prove a key works. Returns the reply text."""
    out = complete(provider, key, model,
                   [{"role": "user", "content": "Reply with the single word OK."}],
                   tools=None, max_tokens=5, base_url=base_url)
    return out["text"]
