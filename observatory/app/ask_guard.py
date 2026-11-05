# -*- coding: utf-8 -*-
"""The last check on every answer the question box gives.

On 2026-11-02 a reader asked "トヨタの政策保有株式が過去5年間でどう変化したか" (how
Toyota's cross-shareholdings changed) and got Toyota's revenue, in dollars, under a
line saying it showed cross-shareholdings. The log called it answered. So:

  * A question in another language is put into English first, because the box's
    checks read English words and would otherwise pass it unread; the answer goes
    back into that language (app/ask_language.py).
  * Cross-shareholdings are answered from their own data (app/jp_holdings_ask.py).
  * Every answer is read against the question before it goes out (on_topic), the
    quick lane's too: "who is selling Toyota?" got what Toyota's own fund arm sold.
    One that shows a different thing is worked out step by step once; if that misses
    too, the reader is told what is not held. A different figure is never shown in
    its place. The check takes about 0.8 s.
  * A headline in dollars over a table in another currency takes the table's.
  * A Japanese company is read from its Japanese filings (app/jp_company_ask.py).
  * A refusal gets one more try too (_second_try): on 2026-11-02 "who is selling
    Kioxia?" was refused in a second, while the 5% filings that answer it were on file.
  * A refusal goes to the whole economic catalogue before it reaches a reader
    (_economy, app/econ_ask.py): the routes above see only part of what Plover holds,
    so none of them can say Plover lacks something.

on_topic fails open, like every review on this box: if the model is off or slow
the answer goes out and the log says so. The fixed recipes hold their own line for
that case (r_company_as_filed_history refuses a figure it does not show).
"""
import json
import re
import threading
import time

from fastapi import HTTPException

from . import ask_language, ask_miss, ask_progress, econ_ask, jp_company_ask, jp_holdings_ask

FOREIGN_SCRIPT = re.compile(r"[぀-ヿ㐀-鿿가-힯Ѐ-ӿ֐-ۿऀ-ॿ฀-๿]")
CHECKED_ROUTES = ("quick", "reader", "patterns", "steps")
CHECK_SECONDS = 8
QUICK_CHECK_SECONDS = 4                                   # a fixed answer is ready: don't hold it long if the model is slow
HUNG_SKIP_SECONDS = 60
_hung_until = [0]
RETRIED_MISSES = ("not_held", "unsure")
NO_SUCH_FILER = re.compile(r"^No US-listed filer called ")
CANT_READ = "I can't read that question right now. Please ask it in English, for example “What is Toyota's revenue?”"

ON_TOPIC_PROMPT = """You check one answer before a reader sees it. Say whether it shows what the question asks about.
Reply with one JSON object: {"shows_it": true or false, "asked_for": "the thing asked about, in a few plain words"}.
false only when the figures measure something other than what was asked (cross-shareholdings answered with revenue,
carbon emissions answered with revenue, market share answered with revenue, staff turnover answered with sales), or
they are about a different company.
true in every other case, including: extra detail beyond the question; a period shorter or longer than asked; a close
definition that is stated; an answer that says plainly the figure is not filed or not held; any answer you are unsure of.
House conventions, never grounds for false: "sales" and "revenue" are the same; "the market" is the S&P 500; an index
is measured by the fund that tracks it; "how is X doing" may be answered with its share price or its results.
Do not check arithmetic. The question may be in any language."""


def _log(question, what, detail=""):
    from . import us_steps
    us_steps.log(question, what, detail)


def on_topic(question, out, seconds=CHECK_SECONDS):
    """(shows_it, asked_for). True when the check cannot run."""
    from . import us_ask_model
    cfg = us_ask_model.provider()
    if cfg is None or not us_ask_model.under_cap():
        return True, None
    table = out.get("table") or {}
    shown = {"headline": out.get("headline"), "explanation": (out.get("answer") or "")[:300], "columns": table.get("columns"),
             "first_rows": (table.get("rows") or [])[:2], "chart": (out.get("chart") or {}).get("title"),
             "figures": [f.get("label") for f in out.get("figures") or [] if isinstance(f, dict)][:8]}
    prov, key, model = cfg
    if time.time() < _hung_until[0]:
        return True, None
    us_ask_model._tick("calls")
    box = {}

    def call():
        try:
            box["got"] = us_ask_model._complete(prov, key, model, [
                {"role": "system", "content": ON_TOPIC_PROMPT},
                {"role": "user", "content": "QUESTION: %s\nANSWER: %s" % (question, json.dumps(shown, ensure_ascii=False, default=str)[:1800])}],
                max_tokens=120, timeout=seconds, cache_key="plover-us-on-topic")
        except Exception as exc:                                  # noqa: BLE001 — re-raised below, on the reader's thread
            box["failed"] = exc
    # a socket time-out does not cover a stuck name lookup (2026-11-02: 40 s an answer with
    # the network down), so the wait has its own limit and the next checks are skipped
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(seconds + 1)
    if worker.is_alive():
        _hung_until[0] = time.time() + HUNG_SKIP_SECONDS
        _log(question, "on-topic check did not run", "no reply in %d s" % (seconds + 1))
        return True, None
    try:
        if "failed" in box:
            raise box["failed"]
        got = box["got"]
        m = re.search(r"\{.*\}", got.get("text") or "", re.S)
        verdict = json.loads(m.group(0))
        us_ask_model._tick("matched", got.get("usage"))
    except Exception as exc:                                      # noqa: BLE001 — a check never blocks an answer
        _log(question, "on-topic check did not run", repr(exc)[:120])
        return True, None
    return verdict.get("shows_it") is not False, str(verdict.get("asked_for") or "").strip()[:80] or None


def _not_shown(question, asked_for):
    what = "that" if not asked_for else "“%s”" % asked_for
    return ask_miss.miss("I couldn't find %s in the company data, so I haven't shown a different figure in its place." % what, "not_held")


def checked(cur, question, out):
    """out when it shows what was asked; else one try by steps; else a refusal."""
    if ((out or {}).get("match") or {}).get("via") not in CHECKED_ROUTES:
        return out
    ask_progress.say("Checking the answer matches your question…")
    asked = out.get("question") or question               # a follow-up is checked as the whole question
    shows, asked_for = on_topic(asked, out, QUICK_CHECK_SECONDS if out["match"]["via"] == "quick" else CHECK_SECONDS)
    if shows:
        return out
    _log(asked, "off-topic answer stopped", "%s: %s" % (out.get("recipe"), out.get("headline")))
    if out["match"]["via"] == "steps":
        raise _not_shown(asked, asked_for)
    from . import us_steps
    ask_progress.say("That wasn't what you asked, so working it out step by step…")
    try:
        again = us_steps.answer(cur, asked)
    except (us_steps.StepsError, HTTPException):
        raise _not_shown(asked, asked_for)
    if not on_topic(asked, again)[0]:
        _log(asked, "off-topic answer stopped", "steps: %s" % again.get("headline"))
        raise _not_shown(asked, asked_for)
    return again


def right_currency(out):
    """A dollar sign on a headline whose table is in another currency is wrong by the exchange rate."""
    from .stock_recipes import CCY_SYMBOLS
    unit = ((out or {}).get("table") or {}).get("unit")
    if unit in (None, "USD", "%") or unit not in CCY_SYMBOLS:
        return out
    sym = CCY_SYMBOLS[unit]
    if out.get("headline") and re.search(r"\$\d", out["headline"]):
        _log(out.get("question"), "dollar headline over a %s table" % unit, out["headline"])
        out["headline"] = re.sub(r"\$(?=\d)", lambda _: sym, out["headline"])
    chart = out.get("chart")
    if isinstance(chart, dict) and chart.get("unit") in ("USD", "$", "$bn"):
        chart["unit"] = {"USD": unit, "$": sym, "$bn": sym + "bn"}[chart["unit"]]
    return out


def _own_data(question, cur=None):
    """Cross-shareholdings, read against the question like any other answer: this
    shows a company's totals, not its stake in one named stock. That detail is
    worked out step by step (jp_cross_holdings in the sandbox) before the page
    pointer is shown."""
    try:
        own = jp_holdings_ask.answer(question)
    except HTTPException as exc:
        # the fixed answer points at the page for detail it does not show: that detail is in the sandbox now
        if cur is not None and exc.status_code < 500:
            from . import stock_recipes
            alt = stock_recipes._steps_try(cur, question, "cross-holdings pointer: %s" % str(exc.detail)[:60], honest=True)
            if alt is not None:
                return alt
        raise
    if own and not on_topic(question, own)[0]:
        _log(question, "off-topic answer stopped", own.get("headline"))
        if cur is not None:
            from . import stock_recipes
            alt = stock_recipes._steps_try(cur, question, "cross-holdings fixed answer off topic", honest=True)
            if alt is not None:
                return alt
        raise ask_miss.miss("This box shows a company's yearly totals, not that detail. " + jp_holdings_ask.PAGE_POINTER, "not_held")
    return own


def _steps_last(cur, question, prev, exc):
    """The last resort before a refusal is shown: the step route, which holds
    every table (app/us_catalog.py), when nothing else has run it and the
    refusal is one data could answer. None when it cannot either."""
    from . import stock_recipes, us_steps
    text = exc.detail if isinstance(exc.detail, str) else ""
    kind = getattr(exc, "miss", None) or ask_miss.classify(text)
    if prev or us_steps.tried() or exc.status_code >= 500 or kind not in RETRIED_MISSES:
        return None
    if text == stock_recipes.MACRO_MISS or getattr(exc, "question", None):
        return None                                            # the fixed economy answers read this first (the page, app/assistant/preview.py)
    out = stock_recipes._steps_try(cur, question, "last resort after: %s" % text[:80])
    if out is not None and not on_topic(question, out)[0]:
        _log(question, "off-topic answer stopped", "steps last: %s" % out.get("headline"))
        return None
    if out is not None:
        _log(question, "answered on the last try", "steps: %s" % out.get("headline"))
    return out


def _japan(cur, question, prev):
    """A Japanese company from its Japanese filings, read against the question like any other answer."""
    own = jp_company_ask.first_try(cur, question, prev)
    if own and not on_topic(question, own)[0]:
        _log(question, "off-topic answer stopped", own.get("headline"))
        return None
    if own:
        # a fixed answer may not drop a date or time phrase it does not read: the step route, which
        # holds the Japanese tables too, is tried first (app/stock_recipes._run_shape has the same rule)
        from . import stock_recipes
        unread = stock_recipes._unread_by(question, {"id": "jp_fixed"})
        if unread:
            alt = stock_recipes._steps_try(cur, question, "japan fixed leaves %s unread" % unread, honest=True)
            if alt is not None:
                return alt
    return own


def _second_try(cur, question, prev, exc):
    """A refusal is worth one more try, as a wrong answer is: first the Japanese filings,
    then step by step when the route that refused did not already go that way. None when
    the refusal stands. The reader never waits on this for a gap the box knows it has."""
    from . import stock_recipes, us_figures, us_steps
    text = exc.detail if isinstance(exc.detail, str) else ""
    if exc.status_code >= 500 or (getattr(exc, "miss", None) or ask_miss.classify(text)) not in RETRIED_MISSES:
        return None
    if text == stock_recipes.MACRO_MISS or getattr(exc, "question", None):       # the economy answers read this next
        return None
    ask_progress.say("Looking again…")
    own = jp_company_ask.second_try(cur, question)
    if own and on_topic(question, own)[0]:
        _log(question, "answered on the second try", "japan: %s" % own.get("headline"))
        return own
    if prev or us_steps.tried() or us_figures.judge(text, question)["verdict"] == "gap" or NO_SUCH_FILER.search(text):
        return None
    ask_progress.say("Working it out step by step…")
    try:
        again = us_steps.answer(cur, question)
    except (us_steps.StepsError, HTTPException):
        return None
    if not on_topic(question, again)[0]:
        _log(question, "off-topic answer stopped", "steps: %s" % again.get("headline"))
        return None
    _log(question, "answered on the second try", "steps: %s" % again.get("headline"))
    return again


# what a refusal said when the catalogue might still answer; advice and our own failures never do
ECONOMY_MISSES = ("not_held", "unsure", "other_market", "unclear")
_MACRO_VERDICTS = {}


def macro_on_topic(question, out):
    """Whether a fixed economy answer shows what was asked. "Japan government debt to GDP"
    matched the word GDP and got GDP growth; these answers run before any other check."""
    # one reading per answer: the page's two doors each ask, and must not get different verdicts
    key = (time.strftime("%Y-%m-%d"), question, out.get("headline"))
    if key in _MACRO_VERDICTS:
        return _MACRO_VERDICTS[key]
    shows, asked_for = on_topic(question, out, QUICK_CHECK_SECONDS)
    if not shows:
        _log(question, "off-topic answer stopped", "macro: %s" % out.get("headline"))
    if len(_MACRO_VERDICTS) > 2000:
        _MACRO_VERDICTS.clear()
    _MACRO_VERDICTS[key] = shows
    return shows


def _economy(cur, question, prev, exc):
    """The whole catalogue's answer to a refused question, or None when the refusal stands.
    Raises the catalogue's own refusal when it looked and nothing measures it."""
    from . import stock_recipes
    from .assistant import preview
    if exc.status_code >= 500:
        return None
    text = exc.detail if isinstance(exc.detail, str) else ""
    if (getattr(exc, "miss", None) or ask_miss.classify(text)) not in ECONOMY_MISSES or NO_SUCH_FILER.search(text):
        return None
    asked = getattr(exc, "question", None) or question
    if text == stock_recipes.MACRO_MISS:                     # the economy answers may hold it: the page asks them next
        try:
            fixed = preview._macro(asked)
        except Exception:                                    # noqa: BLE001 — the reader below still gets its turn
            fixed = None
        if fixed and macro_on_topic(asked, fixed):
            return None
    out = econ_ask.answer(asked, prev if asked == question else None)
    if out is not None and not on_topic(asked, out)[0]:
        _log(asked, "off-topic answer stopped", "economy: %s" % out.get("headline"))
        return None
    return out


def answer(cur, question, prev, opts, inner):
    """The box's answer, through the checks above and back into the reader's language.
    `inner` is the routing in stock_recipes (question, prev, opts as it takes them)."""
    question = ask_language.country_as_us(question)
    # written in Japanese, cross-shareholdings are answered in Japanese without a model call
    if not ask_language.might_be_foreign(question):
        own = _own_data(question, cur)
        if own:
            return own
    elif ask_language.KANA.search(question):
        try:
            own = _own_data(question, cur)
        except HTTPException:                                  # ホンダ: filed as 本田技研工業, found by its English name below
            own = None
        if own:
            return own
    written, language = question, None
    read = ask_language.read(question)
    if read:
        if not read["english"]:
            raise ask_miss.miss(CANT_READ, "unclear")
        ask_progress.say("Reading your question…")
        question, language = read["english"], read["language"]
        own = _own_data(question, cur)
        if own:
            own["question"] = written
            own["match"]["read_as"] = question
            return ask_language.answer_in(language, own)
        if prev and ask_language.might_be_foreign(prev):
            prev = (ask_language.read(prev) or {}).get("english") or prev
    from . import us_steps
    out = _japan(cur, question, prev)
    if out is None:
        us_steps.not_tried()
        try:
            out = right_currency(checked(cur, question, inner(cur, question, prev, opts)))
        except HTTPException as exc:
            out = _second_try(cur, question, prev, exc) or _economy(cur, question, prev, exc) or _steps_last(cur, question, prev, exc)
            if out is None:
                if language and not getattr(exc, "question", None):
                    exc.question = question                    # the economy answers read the English
                raise
            out = right_currency(out)
    if language:
        out["question"] = written
        out.setdefault("match", {})["read_as"] = question
        ask_progress.say("Putting the answer into %s…" % language)
        ask_language.answer_in(language, out)
    return out
