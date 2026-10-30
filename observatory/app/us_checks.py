# -*- coding: utf-8 -*-
"""The checks a careful analyst runs on a result before reading it out.

The step-by-step route (app/us_steps.py) plans a query and runs it. It
retried only when the query crashed or found nothing; a result that came
back absurd went out as the answer. These are the checks that catch that,
by code, on the question, the model's plan and the result rows:

  mean_swamped      a mean of ratios across companies that a few near-zero-
                    revenue filers dominate (the mean operating margin of
                    software companies came out at −3,643%)
  tiny_base         a growth rate ranked or filtered with no floor on the
                    year-before figure (a company growing from $1mn leads)
  few_behind        an average, share or count resting on fewer than ten
                    companies, or with no count at all for the reader
  word_vs_column    the question's word measured by a different column
                    without saying so ("debt" as total liabilities)
  impossible        a value no going concern has, in the row the headline
                    names (a yield above 50%, a margin below −1,000%)
  how_many          "how many" answered with a list instead of a count
  period            a year named in the question that the plan's period
                    does not cover
  other_market      a market this box does not cover (Japan, Hong Kong …),
                    which must get the pointer, never US data

Each finding says what is wrong, carries a plain note for the reader, and an
instruction the model can act on. A finding is `revise` (send the model
back for another plan), `note` (keep the answer, show the note) or `refuse`
(answer with the note alone). No figure is written here: notes quote the
result only through the same formatting the headline uses.

review() is the one model call: it reads the question, the steps, the
filled headline and a few result rows, and says ok / revise / note. It is
cached per question per day, costs at most 200 output tokens, and a failure
is "ok" — the answer still goes out with the code checks done.
"""
import datetime
import json
import re
import threading

REVISE, NOTE, REFUSE = "revise", "note", "refuse"
FEW = 10                 # companies behind a figure before the reader is told how few
MEAN_MEDIAN_GAP_PP = 20  # a mean and median of a percentage this far apart: the mean is swamped
MEAN_MEDIAN_RATIO = 5.0  # or this many times apart
EXTREME_RATIO_PCT = 200  # a mean ratio beyond ±this is not a figure anyone would quote
TOP_GROWTH_PCT = 1000    # growth above this leads a list only from a tiny base

_MEAN = re.compile(r"\b(avg|average|mean)\b|^(avg|average|mean)_|_(avg|average|mean)(_|$)", re.I)
_MEDIAN = re.compile(r"median", re.I)
_GROWTH = re.compile(r"(revenue|profit)_growth_pct", re.I)
_COUNT = re.compile(r"^(n|count|members|companies|stocks|beat|fell|rose|up|down|total|matched|n_[a-z_]+|[a-z_]+_n)$", re.I)
_RATIO_COL = re.compile(r"_pct$|_x$", re.I)
_MARKETS = [
    (r"\bjapan(ese)?\b|\btokyo\b|\bnikkei\b|\btopix\b|\btse\b", "Japanese companies are under the Japan tab (Companies, Banks and the screener there)"),
    (r"\bhong\s*kong\b|\bhang\s*seng\b|\bhk\b|\bhsi\b", "Hong Kong is under the Asia tab"),
    (r"\beurope(an)?\b|\buk\b|\bbritish\b|\blondon\b|\bftse\b|\bdax\b|\bcac\b|\bstoxx\b|\bgerman\b|\bfrench\b",
     "European companies are not covered yet"),
    (r"\bshanghai\b|\bshenzhen\b|\bchina a.shares?\b|\ba-shares?\b|\bcsi\s*300\b|\bkospi\b|\bkorean?\b|\btaiwan(ese)?\b|\btwse\b|\bindian?\b|\bnifty\b|\bsensex\b|\basx\b|\baustralian?\b",
     "that market is not covered yet"),
]
# the question's word -> (columns that measure it, patterns that mean another figure was used instead)
_WORDS = {
    "debt": (r"\bdebt\b|borrowings?\b|indebted|leverage", ["borrowings_usd"],
             [r"total_assets_usd\s*-\s*equity_usd", r"liabilit"], "borrowings"),
    "dividend yield": (r"dividend yield", ["dividend_yield_pct"], [r"payout_ratio_pct"], "dividend yield"),
}


def finding(check, severity, note, instruction=None):
    return {"check": check, "severity": severity, "note": note, "instruction": instruction or note}


def _fmt(col, v):
    from .us_steps import fmt_value
    return fmt_value(col, v)


def _first_placeholder(template):
    m = re.search(r"\{([^}]*)\}", template or "")
    return m.group(1).strip() if m else None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------------------------------------------------------------- the checks
# A country in a question about a company's own business is where that company sells, not a market it trades in
_OWN_BUSINESS = re.compile(r"\b(?:revenues?|sales|sell|sells|sold|earn|earns|earned|makes?|made|exposure|exposed|business|operations)\b")


def about_us_listed(question, cur=None):
    """True when a country word does not mean another stock market: the question
    names a company that files here ("Taiwan Semiconductor capex", "Apple revenue
    from Japan") or asks where US companies sell ("the most revenue in India").
    "Best Japanese banks" and "largest UK companies" stay pointed elsewhere."""
    ql = (question or "").lower()
    # a market's own stocks, however a ticker is spelt ("UK" is Ubiquiti's old symbol, "Taiwan" an alias of TSMC)
    if re.search(r"\b(?:japan(?:ese)?|tokyo|korean?|taiwan(?:ese)?|india[n]?|australian?|europe(?:an)?|british|uk|german|french|chinese|china|shanghai|"
                 r"shenzhen|hong kong|hk|london) (?:banks?|stocks?|shares|compan|firms?|markets?|equities|listed|index|indices)", ql) \
            or re.search(r"\b(?:companies|stocks|firms|banks|shares) (?:in|from|listed in) (?:the )?(?:japan|tokyo|korea|taiwan|india|australia|europe|uk|germany|france|china|shanghai|hong kong|london)", ql):
        return False
    if _OWN_BUSINESS.search(ql) and re.search(r"\b(?:us|u\.s\.|american|s&p|dow) (?:compan|firms?|stocks?)|\b(?:in|from|to) (?:the )?[a-z]", ql):
        return True
    if cur is None:
        return False
    try:
        from . import stock_recipes, us_link
        return bool(us_link.find_companies(cur, stock_recipes._without_benchmarks(question)))
    except Exception:                                             # noqa: BLE001 — unsure: the pointer stands
        return False


def other_market(question, cur=None):
    ql = (question or "").lower()
    if about_us_listed(question, cur):
        return None
    for pat, where in _MARKETS:
        if re.search(pat, ql):
            return finding("other_market", REFUSE,
                           "This box covers US-listed companies only. %s. A foreign company appears here only if it is "
                           "also listed in the US (its depositary shares)." % where)
    return None


def mean_swamped(plan, rows, cols):
    """A mean beside a median far from it, or a mean ratio beyond what anyone would quote."""
    if not rows:
        return None
    row = rows[0]
    means = [c for c in cols if _MEAN.search(c) and _RATIO_COL.search(c)]
    medians = [c for c in cols if _MEDIAN.search(c)]
    lead = _first_placeholder(plan.get("headline"))
    for mc in means:
        mv = row[cols.index(mc)]
        if not _num(mv):
            continue
        stem = re.sub(r"(avg|average|mean)_?", "", mc, flags=re.I).strip("_")
        med = next((c for c in medians if stem and stem in c), medians[0] if len(medians) == 1 else None)
        dv = row[cols.index(med)] if med else None
        swamped = abs(mv) > EXTREME_RATIO_PCT
        if _num(dv):
            gap = abs(mv - dv)
            swamped = swamped or gap > MEAN_MEDIAN_GAP_PP or (dv != 0 and abs(mv / dv) > MEAN_MEDIAN_RATIO)
        if not swamped:
            continue
        if med and lead == med:
            return finding("mean_swamped", NOTE,
                           "The plain average (%s) is pulled far from the median (%s) by a few companies with almost no "
                           "revenue; the median is the figure to use." % (_fmt(mc, mv), _fmt(med, dv)))
        return finding("mean_swamped", REVISE,
                       "The plain average is dominated by a few companies with almost no revenue.",
                       "The mean %s is swamped by near-zero-revenue filers. Lead the headline with the median "
                       "(median(...) AS median_..._pct), drop companies with revenue_usd below 1e8 and say "
                       "\"companies with revenue above $100mn\"; give the mean only beside the median." % mc)
    return None


def tiny_base(plan, rows, cols):
    """Growth ranked or filtered without a floor on the year-before figure."""
    sql = plan.get("sql") or ""
    if not _GROWTH.search(sql):
        return None
    # a count of companies above a growth rate (FILTER (WHERE ...)) is not a ranking
    unfiltered = re.sub(r"FILTER\s*\(\s*WHERE[^)]*\)", "", sql, flags=re.I)
    ranked = re.search(r"ORDER\s+BY[^;]*growth_pct|WHERE[^;]*growth_pct\s*[><]", unfiltered, re.I | re.S)
    floored = re.search(r"revenue_prior_usd\s*>=?\s*\d|net_income_prior", sql, re.I)
    top = None
    for c in cols:
        if _GROWTH.search(c) and rows and _num(rows[0][cols.index(c)]):
            top = (c, rows[0][cols.index(c)])
    if ranked and not floored:
        return finding("tiny_base", REVISE,
                       "Growth from a tiny base leads any list; a floor on the year-before figure is needed.",
                       "Growth is ranked or filtered with no base: add revenue_prior_usd >= 5e7 to the WHERE and say "
                       "\"from a base above $50mn\" in the headline.")
    if top and top[1] > TOP_GROWTH_PCT:
        return finding("tiny_base", NOTE,
                       "The leading growth rate (%s) comes from a very small year-before figure." % _fmt(*top))
    return None


def few_behind(question, plan, rows, cols):
    """An aggregate with no count for the reader, or resting on fewer than FEW companies."""
    sql = plan.get("sql") or ""
    aggregate = re.search(r"\b(avg|median|sum|quantile|stddev)\s*\(", sql, re.I) and len(rows) == 1
    counts = [c for c in cols if _COUNT.match(c)]
    if aggregate and not counts:
        return finding("few_behind", REVISE,
                       "The figure does not say how many companies stand behind it.",
                       "Add count(<the averaged column>) AS n to the SELECT so the reader sees how many companies "
                       "stand behind the figure, and name it in the headline (\"across {n} companies\").")
    for c in counts:
        v = rows[0][cols.index(c)] if rows else None
        if _num(v) and 0 < v < FEW and len(rows) == 1 and not re.search(r"\bhow many\b", (question or "").lower()):
            return finding("few_behind", NOTE, "Only %s companies stand behind this figure." % _fmt(c, v))
    return None


def word_vs_column(question, plan):
    """The question's word measured by another column without the headline saying so."""
    ql = (question or "").lower()
    sql = plan.get("sql") or ""
    head = (plan.get("headline") or "").lower()
    for key, (words, cols, wrong, name) in _WORDS.items():
        if not re.search(words, ql):
            continue
        used_right = any(c in sql for c in cols)
        used_wrong = [w for w in wrong if re.search(w, sql, re.I)]
        if used_wrong and not used_right and name not in head and "liabilit" not in head:
            return finding("word_vs_column", REVISE,
                           "The question asks about %s but something else was measured." % key,
                           "The question says \"%s\"; the query measures %s instead. Use %s, or if it is blank for "
                           "the companies in question, name what was measured in the headline (\"total liabilities, "
                           "for debt\")." % (key, " / ".join(used_wrong), " or ".join(cols)))
    return None


_LIMITS = [
    (re.compile(r"pe_x$", re.I), 1.0, 1000.0, "a P/E outside 1–1,000×"),
    (re.compile(r"margin_pct$", re.I), -1000.0, 100.0, "a margin outside −1,000% to 100%"),
    (re.compile(r"yield_pct$", re.I), 0.0, 50.0, "a yield above 50%"),
    (re.compile(r"roe_pct$", re.I), -1000.0, 1000.0, "a return on equity beyond ±1,000%"),
]


def impossible(rows, cols):
    """A value no going concern has, in the row the headline names (revise)
    or elsewhere in the list (note)."""
    if not rows:
        return None
    hits = []
    for i, row in enumerate(rows):
        for c in cols:
            v = row[cols.index(c)]
            if not _num(v):
                continue
            for pat, lo, hi, what in _LIMITS:
                if pat.search(c) and (v < lo or v > hi):
                    hits.append((i, c, v, what))
    if not hits:
        return None
    first = [h for h in hits if h[0] == 0]
    if first:
        i, c, v, what = first[0]
        return finding("impossible", REVISE,
                       "The leading row carries %s (%s), which is not a figure to quote." % (what, _fmt(c, v)),
                       "The first result row has %s = %s: %s. Exclude such rows (a revenue floor of 1e8, or a "
                       "bound on the ratio) and say so in the headline." % (c, v, what))
    return finding("impossible", NOTE, "%d of the rows carry %s; they are shown but are not figures to quote."
                   % (len({h[0] for h in hits}), hits[0][3]))


def how_many(question, rows, cols):
    """A 'how many' question answered with a list rather than a count."""
    if not re.search(r"\bhow many\b", (question or "").lower()):
        return None
    if len(rows) > 1 and not any(_COUNT.match(c) for c in cols):
        return finding("how_many", REVISE, "The question asks how many; the answer must be a count.",
                       "The question asks \"how many\": return one row with count(...) AS n and the number it is out "
                       "of (count(<column>) AS members), not a list.")
    return None


def period(question, plan, today=None):
    """A year named in the question that the plan's period does not cover."""
    today = today or datetime.date.today()
    years = [int(y) for y in re.findall(r"\b(20[0-3]\d)\b", question or "") if int(y) <= today.year]
    if not years:
        return None
    p = plan.get("period") or {}
    try:
        start = datetime.date.fromisoformat(str(p.get("from"))[:10])
        end = datetime.date.fromisoformat(str(p.get("to"))[:10]) if p.get("to") else today
    except (ValueError, TypeError):
        return None
    y = years[0]
    if start.year <= y <= end.year:
        return None
    return finding("period", REVISE, "The period does not cover the year the question names.",
                   "The question names %d; set \"period\" to cover it (from %d-01-01 to %d-12-31 for a finished year)."
                   % (y, y, y))


def wrong_company(question, rows, cols, cur=None):
    """One company named in the question, and a one-row result about another
    ticker: the query matched a name loosely ("AAPL revenue" found a $19mn
    Apple), and must use the ticker."""
    if not rows or len(rows) != 1 or "ticker" not in cols or cur is None:
        return None
    try:
        from . import stock_recipes, us_link
        cos = us_link.find_companies(cur, stock_recipes._without_benchmarks(question))
    except Exception:                                             # noqa: BLE001 — a check never fails the answer
        return None
    if len(cos) != 1:
        return None
    got = str(rows[0][cols.index("ticker")] or "").upper()
    want = {t.upper() for t in ([cos[0].get("ticker")] + list(cos[0].get("tickers") or [])) if t}
    want |= {t.replace("-", ".") for t in want}
    if not got or got in want:
        return None
    return finding("wrong_company", REVISE, "The result is about %s, not the company the question names." % got,
                   "The question names %s (ticker %s); the result row is %s. Match the company by ticker = '%s', never by name with ILIKE."
                   % (cos[0].get("name"), cos[0].get("ticker"), got, cos[0].get("ticker")))


def check(question, plan, rows, cols, headline=None, today=None, cur=None):
    """Every finding on this plan and result, most serious first. `headline`
    is the filled headline, or None when us_steps refused the template."""
    out = []
    om = other_market(question, cur)
    if om:
        return [om]
    wc = wrong_company(question, rows, cols, cur)
    if wc:
        out.append(wc)
    if rows and headline is None and plan.get("headline"):
        out.append(finding("headline", REVISE, "The headline could not be filled from the result.",
                           "The headline wrote a number of its own, or named a column the query does not return. "
                           "Use only these result columns in {braces}: %s; and no literal numbers except those in "
                           "the question or the query's own thresholds." % ", ".join(cols)))
    for f in (period(question, plan, today), how_many(question, rows, cols), word_vs_column(question, plan),
              tiny_base(plan, rows, cols), mean_swamped(plan, rows, cols), impossible(rows, cols),
              few_behind(question, plan, rows, cols)):
        if f:
            out.append(f)
    order = {REFUSE: 0, REVISE: 1, NOTE: 2}
    out.sort(key=lambda f: order[f["severity"]])
    return out


def to_revise(findings):
    """The one instruction to send the model back with, or None."""
    rev = [f for f in findings if f["severity"] == REVISE]
    if not rev:
        return None
    return " ".join(f["instruction"] for f in rev[:2]) + " Reply with the whole JSON object again."


def notes(findings):
    return [f["note"] for f in findings if f["severity"] == NOTE]


# ---------------------------------------------------------------- the review
_REVIEWS = {}
_REVIEWS_MAX = 3000
_LOCK = threading.Lock()
REVIEW_TOKENS = 200
REVIEW_SECONDS = 8
SAMPLE_ROWS = 5


def _key(question):
    return (datetime.date.today().isoformat(), re.sub(r"\s+", " ", (question or "").strip().lower()).strip(" ?.!"))


def review_prompt(question, plan, headline, rows, cols):
    sample = [dict(zip(cols, r)) for r in rows[:SAMPLE_ROWS]]
    return ("You are the reviewer on an analyst's desk. A junior analyst answered a question about US-listed stocks "
            "from a database of company filings and daily prices. Read the question, the steps they took, the "
            "headline they wrote and a sample of the result. Say whether the headline answers the question that was "
            "asked, as a careful analyst would. Do not check arithmetic and do not add figures of your own.\n\n"
            "Reply with one JSON object: {\"verdict\": \"ok\" | \"revise\" | \"note\", \"reason\": \"one sentence\"}.\n"
            "\"revise\" means the steps should be redone (wrong group of companies, wrong period, wrong figure, a "
            "headline that does not answer the question); the reason tells the analyst what to change. \"note\" means "
            "the answer stands but the reader should be told one thing the headline does not already say; the reason is "
            "then printed under the answer as it stands, so write it to the reader (\"The latest month is not over, so "
            "the count can still change.\"), never about the reader or the analyst. \"ok\" otherwise.\n\n"
            "House conventions, never grounds to revise: an index is measured by the fund that tracks it (the S&P 500 "
            "by SPY, the Dow by DIA); \"the Nasdaq\" means the Nasdaq-100 (QQQ), since the Composite is not held; \"the "
            "market\" means the S&P 500; price changes are before dividends; the current month or week counts to the "
            "latest close; a question naming no period means this year, except a question about the days around dated "
            "events (after earnings, on CPI release days, after Fed decisions, during shutdowns), which uses every event "
            "on file from 2018 unless the question names a period; \"N days after\" an event runs from the close on the "
            "last trading day before the event day to the close N trading days after the event day, so the move on the "
            "news is inside it whether the news came before the open or after the close.\n\n"
            "TODAY: %s\nQUESTION: %s\nSTEPS: %s\nHEADLINE: %s\nRESULT (%d rows, first %d): %s"
            % (datetime.date.today().isoformat(), question, json.dumps(plan.get("steps") or [], ensure_ascii=False), headline, len(rows),
               len(sample), json.dumps(sample, ensure_ascii=False, default=str)[:1500]))


def review(question, plan, headline, rows, cols):
    """{"verdict": "ok"|"revise"|"note", "reason": str}; "ok" when the model
    is off, over its cap, slow or unreadable."""
    ok = {"verdict": "ok", "reason": None}
    k = _key(question)
    with _LOCK:
        if k in _REVIEWS:
            return _REVIEWS[k]
    try:
        from . import us_ask_model
        cfg = us_ask_model.provider()
        if cfg is None or not us_ask_model.under_cap():
            return ok
        prov, key, model = cfg
        us_ask_model._tick("calls")
        out = us_ask_model._complete(prov, key, model,
                                     [{"role": "system", "content": review_prompt(question, plan, headline, rows, cols)},
                                      {"role": "user", "content": "Your verdict?"}],
                                     max_tokens=REVIEW_TOKENS, timeout=REVIEW_SECONDS, cache_key="plover-us-review")
        m = re.search(r"\{.*\}", out.get("text") or "", re.S)
        got = json.loads(m.group(0)) if m else {}
        us_ask_model._tick("matched", out.get("usage"))
    except Exception:                                            # noqa: BLE001 — a review never blocks an answer
        return ok
    verdict = str(got.get("verdict") or "ok").lower()
    if verdict not in ("ok", "revise", "note"):
        verdict = "ok"
    res = {"verdict": verdict, "reason": (str(got.get("reason") or "").strip()[:300] or None)}
    with _LOCK:
        if len(_REVIEWS) >= _REVIEWS_MAX:
            _REVIEWS.clear()
        _REVIEWS[k] = res
    return res
