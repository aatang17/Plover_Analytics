# -*- coding: utf-8 -*-
"""What the front page's box says when it cannot answer.

A reader never sees a status code, a blank, or the box's own plumbing. Every
miss is one of five kinds, each with one plain sentence and, where it helps,
up to three questions the box does answer:

  not_held      the data does not carry it ("most employees"): say what is
                missing, offer the closest thing it does hold
  policy        something Plover does not do (why it moved, buy or sell,
                forecasts): say so, offer the factual version
  unclear       not a question it can read ("asdf qwerty"): say so, show
                examples; never attempt an answer
  other_market  a market this box does not cover: point to where it is
  error         our fault (a failure, a time-out, data not loaded): say so;
                the page offers Try Again and keeps the question in the box

The kind travels on the HTTPException that carries the sentence (miss()), or
is read off the sentence when an older raise site did not set it
(classify()). reply() makes the JSON every door of the box returns:
{"detail": sentence, "miss": kind, "try": [questions]}. "detail" stays a
plain string, so anything reading the old shape still works.
"""
import re

from fastapi import HTTPException

KINDS = ("not_held", "policy", "unclear", "other_market", "error")

# the front page's own examples, every one of which answers (ci/search_examples.py checks them)
EXAMPLES = ["Who owns Nvidia?", "Most valuable US companies", "Is US core inflation falling?"]

UNCLEAR = ("I couldn't tell what you're asking. Ask about a US company (its owners, results, buybacks, "
           "insider trades or share price), or about US inflation, wages or Treasury yields.")
ERROR = "That didn't work on our side. Try again in a moment; your question is still in the box."
NOT_LOADED = "US company data isn't available on this server right now. Try again in a few minutes."
NOTHING = "That isn't something Plover can answer yet."


def miss(detail, kind, tries=None, status=422):
    """An HTTPException that carries its kind and suggestions."""
    exc = HTTPException(status, detail)
    exc.miss = kind
    exc.tries = [t for t in (tries or []) if t]
    return exc


def classify(text):
    """The kind of a sentence raised without one."""
    from . import stock_recipes, us_steps
    t = text or ""
    if t in (m for _, m in stock_recipes._UNANSWERABLE):
        return "policy"
    if t.startswith(UNCLEAR[:30]) or t == stock_recipes._REFUSALS.get("fragment") or t == stock_recipes.NO_MATCH_TEXT:
        return "unclear"
    if us_steps.OTHER_MARKET_PREFIX and t.startswith(us_steps.OTHER_MARKET_PREFIX):
        return "other_market"
    return "not_held"


# the box's internal words, never shown to a reader
_PLAIN = [
    (re.compile(r"\b[Tt]he (?:requested |available |provided )?tables (?:do not|don't|does not|doesn't) "
                r"(?:contain|hold|include|have|provide)\b"), "Plover's data doesn't include"),
    (re.compile(r"\b[Tt]he (?:requested |available |provided )?tables (?:only )?(?:contain|hold|include|have|provide)\b"),
     "Plover's data includes"),
    (re.compile(r"\b(?:[Tt]he|[Tt]hese|[Mm]y) (?:requested |available |provided )?tables\b"), "Plover's data"),
    (re.compile(r"\bthe data sets I hold\b"), "the data Plover holds"),
    (re.compile(r"\bin the data sets\b"), "in Plover's data"),
]


def plain(text):
    """A sentence with the box's internal words replaced, first letter capital."""
    t = " ".join(str(text or "").split())
    for pat, rep in _PLAIN:
        t = pat.sub(rep, t)
    t = re.sub(r"\bPlover's data (?:contains|holds)\b", "Plover's data includes", t)
    return (t[:1].upper() + t[1:]) if t else t


_SUFFIX = re.compile(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|plc|PLC|N\.V\.|S\.A\.|Holdings?|"
                     r"Group|Class [A-C]|/[A-Z]{2}/)$")


def _short(name):
    n = re.sub(r"\s*/[A-Z]*/?$", "", (name or "").strip())           # the SEC's "/DE/", "Corp/"
    n = n.rstrip("/ ").strip()
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip().rstrip(",")
        if n2 == n:
            break
        n = n2
    return n


def company_in(cur, question):
    """The first company the question names, as a short name, or None."""
    if cur is None:
        return None
    try:
        from . import stock_recipes, us_link
        cos = us_link.find_companies(cur, stock_recipes._without_benchmarks(question))
    except Exception:                                             # noqa: BLE001 — a suggestion is a bonus
        return None
    if not cos:
        return None
    from . import us_link
    return _short(us_link.display_name(cos[0].get("name"))) or cos[0].get("ticker")


def suggestions(question, kind, text, cur=None, given=None):
    """Up to three questions the box answers, closest first."""
    if kind in ("unclear", "error", "other_market"):
        return []
    co = company_in(cur, question)
    ql = (question or "").lower()
    out = list(given or [])
    if kind == "policy":
        if re.search(r"\bwhy\b", ql):
            out += (["How has %s done this year?" % co, "What did %s report last quarter?" % co,
                     "Are insiders buying %s?" % co] if co else
                    ["Best S&P 500 stocks this year", "What drove the S&P 500 this year?", "Most valuable US companies"])
        elif re.search(r"forecast|estimate|consensus|guidance|target|will ", ql):
            out += (["What did %s report last quarter?" % co, "%s revenue growth" % co, "When does %s report next?" % co]
                    if co else ["Who reports next week?", "Most valuable US companies", "Is US core inflation falling?"])
        else:                                                    # advice
            out += (["What is %s's P/E?" % co, "Who owns %s?" % co, "Are insiders buying %s?" % co] if co else
                    ["Cheapest S&P 500 stocks by P/E", "Where have insiders bought the most stock?", "Who owns Nvidia?"])
    else:
        if re.search(r"employees|headcount|staff\b|workforce", ql):
            out += ["Largest US companies by revenue"]
        if co:
            # price and filings every listed company has (a 20-F filer's revenue growth can come back empty)
            out += ["What does %s do?" % co, "How has %s done this year?" % co, "Who owns %s?" % co]
        out += EXAMPLES
    seen, uniq = set(), []
    for t in out:
        k = t.lower().strip(" ?")
        if k and k not in seen and k != ql.strip(" ?"):
            seen.add(k)
            uniq.append(t)
    return uniq[:3]


def reply(question, exc=None, cur=None, status=None):
    """{"detail", "miss", "try"} for a miss: an HTTPException from the box, or None for a failure of ours."""
    if exc is None or not isinstance(exc, HTTPException):
        return {"detail": ERROR, "miss": "error", "try": []}
    code = status or exc.status_code
    if code == 503:
        return {"detail": NOT_LOADED, "miss": "error", "try": []}
    if code >= 500:
        return {"detail": ERROR, "miss": "error", "try": []}
    text = exc.detail if isinstance(exc.detail, str) else NOTHING
    kind = getattr(exc, "miss", None) or classify(text)
    sentence = UNCLEAR if kind == "unclear" and not text else plain(text)
    return {"detail": sentence, "miss": kind, "try": suggestions(question, kind, text, cur, getattr(exc, "tries", None))}
