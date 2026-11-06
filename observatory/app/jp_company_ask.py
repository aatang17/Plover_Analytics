# -*- coding: utf-8 -*-
"""Japanese companies in the question box, read from the Japanese filings.

The box's company answers read SEC filings and US prices. Kioxia is known there
only as an over-the-counter line (KXIAY) with no filings and no prices, so on
2026-11-02 "How has Kioxia done this year?" and "who is selling Kioxia?" were
refused while its results and every 5% filing sat in Plover under code 285A, and
"Who owns Kioxia?" said one fund held 99.9%, of the few ADRs US managers report.

This reads those figures through the same functions that serve /api/v1/equity, so
the box and the pages cannot disagree. Two doors (app/ask_guard.py):

  first_try   before the US answers: a company with no US-exchange listing, and
              who owns or is trading any Japanese company, where a US list of
              ADR holders would be the wrong answer even for Toyota
  second_try  after the US answers refused: any other question we hold here

Tokyo share prices are not held, so a price question says so and offers the rest.
"""
import datetime
import re
import threading
import time

from fastapi import HTTPException

from . import ask_miss, jp_names

_FIRST_WORD_MIN = 4
_NAMES_SECONDS = 3600
_LEGAL = re.compile(r"\b(?:corporation|corp|company|co|ltd|limited|inc|incorporated|holdings?|group|kabushiki kaisha|kk)\b")
_YEAR = re.compile(r"\b(?:fy\s?|fiscal\s+|in\s+)?((?:19|20)\d\d)\b", re.I)
_CODE = re.compile(r"\b(\d{3}[0-9A-Z])\b")
_TRIES = ("How has %s done this year?", "Who owns %s?", "Who is selling %s?")
NOMINEE_KINDS = ("trust_bank_nominee", "foreign_nominee", "nominee")

CREDIT = "Source: company filings on EDINET (Financial Services Agency of Japan)."
TDNET = "Source: company disclosures on TDnet, Tokyo Stock Exchange."
JPX = "Source: Japan Exchange Group — Information on Outstanding Short Selling Positions."
NO_PRICES = "Tokyo share prices aren't in Plover's data"

# (what the question asks, the words for it), first match wins
_SHAPES = [
    ("price", r"\b(?:share price|stock price|price|market (?:cap\w*|value)|worth|p/?e\b|52.week|all.time high|shares? (?:rose|fell|up|down)|valuation|dividend yield)\b"),
    ("business", r"\bwhat (?:does|do|is)\b.*\b(?:do|make|makes|sell|sells|produce|produces|business)\b|\bwhat is\s+\w+\W*$|\bwhat kind of\b|\bbusiness\b|\bwho is\s+\w+\W*$"),
    ("shorts", r"\bshort(?:ed|ing|s)?\b|\bshort (?:sellers?|interest|positions?)\b"),
    ("buybacks", r"\bbuy.?backs?\b|\brepurchas\w+\b"),
    ("stakes", r"\b(?:sell\w*|sold|trim\w*|dump\w*|exit\w*|reduc\w*|unload\w*|buy\w*|bought|accumulat\w*|adding|added|cut(?:ting)? (?:its |their )?stakes?)\b"),
    ("owners", r"\bowns?\b|\bowners?\b|\bownership\b|\bshareholders?\b|\bholders?\b|\bstakes?\b|\binvestors?\b"),
    ("results", r"\b(?:how\b.*\b(?:done|doing|did|perform\w*|going)|performance|results?|earnings|last quarter|latest quarter|quarterly|reported?)\b"),
]
_OWN_DOOR = ("owners", "stakes")                 # asked first, even of a company with a US listing
_SELLING = re.compile(r"\b(?:sell\w*|sold|trim\w*|dump\w*|exit\w*|reduc\w*|unload\w*|cut\w*)\b", re.I)
_BUYING = re.compile(r"\b(?:buy\w*|bought|accumulat\w*|adding|added)\b", re.I)

# (id, the words, label, kind): the annual figures the filings carry (app/financials_api.py)
_FIGURES = [
    ("employees", r"\bemployees?\b|\bheadcount\b|\bstaff\b|\bworkforce\b", "Employees", "count"),
    ("eps", r"\beps\b|\bearnings per share\b", "Earnings per share", "per_share"),
    ("dps", r"\bdividends?\b(?! yield)", "Dividend per share", "per_share"),
    ("roe_pct", r"\broe\b|\breturn on equity\b", "Return on equity, as filed", "pct"),
    ("equity_ratio_pct", r"\bequity ratio\b", "Equity ratio, as filed", "pct"),
    ("operating_income", r"\boperating (?:income|profit)\b", "Operating income", "yen"),
    ("cf_operating", r"\boperating cash flow\b|\bcash from operations\b", "Operating cash flow", "yen"),
    ("revenue", r"\brevenues?\b|\bsales\b|\bturnover\b", "Revenue", "yen"),
    ("profit", r"\bnet (?:income|profit)\b|\bprofits?\b", "Profit attributable to owners", "yen"),
    ("total_assets", r"\btotal assets\b|\bassets\b", "Total assets", "yen"),
    ("net_assets", r"\bnet assets\b|\bequity\b|\bbook value\b", "Net assets", "yen"),
    ("cash", r"\bcash\b", "Cash", "yen"),
]

# a question about many companies or the market, where a word like "change" is not Change Inc.
_NOT_ONE_COMPANY = re.compile(r"\b(?:largest|biggest|smallest|top|best|worst|highest|lowest|most|least|companies|stocks|firms|banks|insiders|funds|"
                              r"sectors?|industry|industries|market|economy|inflation|yields?|average|median|how many|rank\w*|list|s&p|dow|nasdaq|moved)\b", re.I)

_names = {"at": 0, "by_core": {}, "by_first": {}, "by_code": {}}
_names_lock = threading.Lock()


def _core(name):
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(_LEGAL.sub(" ", n).split())


def _load_names():
    """Listed Japanese companies by the words a reader would use for them."""
    with _names_lock:
        if time.time() - _names["at"] < _NAMES_SECONDS and _names["by_core"]:
            return _names
        from . import equity_api
        rows = equity_api._cur().execute(
            "SELECT sec_code, name_en, name_ja, industry FROM eq_entities WHERE listed AND sec_code IS NOT NULL AND name_en IS NOT NULL").fetchall()
        by_core, by_first, by_code = {}, {}, {}
        for code, en, ja, industry in rows:
            row = {"code": code, "name_en": en, "name_ja": ja, "industry": industry}
            by_code[code] = row
            core = _core(en)
            if core:
                by_core.setdefault(core, row)
                by_first.setdefault(core.split()[0], []).append(row)
        _names.update(at=time.time(), by_core=by_core, by_first=by_first, by_code=by_code)
        return _names


def _named(question):
    """The Japanese company a question names when the US list knows none. A common
    English word is never a name unless it is typed as one ("Fast Retailing")."""
    from . import us_link
    if _NOT_ONE_COMPANY.search(question or ""):
        return None
    names = _load_names()
    text = re.sub(r"(\w)['’]s\b", r"\1", question or "")
    for m in _CODE.finditer(text):
        code = m.group(1)
        if code in names["by_code"] and not re.fullmatch(r"(?:19|20)\d\d", code):
            return names["by_code"][code]
    words = re.findall(r"[A-Za-z0-9]+", text)
    low = [w.lower() for w in words]
    typed_lower = text == text.lower()
    for n in range(min(5, len(low)), 0, -1):
        for i in range(len(low) - n + 1):
            core = _core(" ".join(low[i:i + n]))
            if core in names["by_core"] and core == " ".join(low[i:i + n]) and \
                    (n > 1 or (low[i].upper() not in us_link._ENGLISH and (typed_lower or words[i][0].isupper()))):
                return names["by_core"][core]
    for word, w in zip(words, low):
        hits = names["by_first"].get(w) or []
        if len(hits) == 1 and len(w) >= _FIRST_WORD_MIN and w.upper() not in us_link._ENGLISH and (typed_lower or word[0].isupper()):
            return hits[0]
    return None


def resolve(cur, question):
    """(the Japanese company, whether the US side holds next to nothing on it), or None.
    A US company whose name happens to resemble a Japanese one stays American."""
    from . import stock_recipes, us_link
    if cur is None:
        return None
    try:
        us = us_link.find_companies(cur, stock_recipes._without_benchmarks(question))
    except HTTPException:
        us = []
    if len(us or []) > 1:
        return None
    if us:
        row = _load_names()["by_core"].get(_core(us[0].get("name")))
        return (row, us[0].get("exchange") in (None, "", "OTC")) if row else None
    row = _named(question)
    return (row, True) if row else None


def _shape(question, row):
    ql = (question or "").lower()
    figure = _figure_in(ql)
    if figure and figure[0] == "eps":
        return "figure"
    for shape, pat in _SHAPES:
        if re.search(pat, ql):
            return "figure" if shape == "results" and figure else shape
    if figure:
        return "figure"
    # the company's name alone is its results
    rest = ql
    for w in _core(row["name_en"]).split() + [row["code"].lower()]:
        rest = rest.replace(w, " ")
    rest = re.sub(r"[^a-z0-9]+|\b(?:holdings?|corp\w*|group|stock|shares?)\b", " ", rest).strip()
    return "results" if not rest else None


def _figure_in(ql):
    for f in _FIGURES:
        if re.search(f[1], ql):
            return f
    return None


def _refused_elsewhere(question):
    """Advice, forecasts and 'why' keep their own answers (app/stock_recipes.py)."""
    from . import stock_recipes
    ql = (question or "").lower()
    return bool(stock_recipes._NEWS_ASK.search(ql) or any(re.search(p, ql) for p, _ in stock_recipes._UNANSWERABLE))


def first_try(cur, question, prev=None):
    """The answer from Japanese filings where that is the right place to look, else None."""
    if _refused_elsewhere(question):
        return None
    hit = resolve(cur, question)
    if not hit and prev:
        hit = resolve(cur, prev)
        if hit and not hit[1]:
            return None
    if not hit:
        return None
    row, thin = hit
    shape = _shape(question, row)
    if shape is None or (not thin and shape not in _OWN_DOOR):
        return None
    return _run(shape, row, question, thin)


def second_try(cur, question):
    """The same reading, after the US answers refused: any question we hold here."""
    if _refused_elsewhere(question):
        return None
    hit = resolve(cur, question)
    if not hit:
        return None
    shape = _shape(question, hit[0])
    if shape is None or (shape == "price" and not hit[1]):
        return None
    try:
        return _run(shape, hit[0], question)
    except HTTPException:
        return None


def _run(shape, row, question, thin=False):
    try:
        return _RUN[shape](row, question)
    except HTTPException as exc:
        if getattr(exc, "miss", None):
            raise
        if exc.status_code == 404:
            if thin:                         # the US side holds nothing either: say what is here instead
                raise ask_miss.miss("Nothing of that kind is filed for %s in Plover's Japanese data." % _who(row), "not_held", _tries(row))
            return None
        raise ask_miss.miss("Japanese company data isn't available on this server right now. Try again in a few minutes.", "error", status=503)


# ------------------------------------------------------------------ wording
def _who(row):
    return ask_miss._short(jp_names.display_name(row["name_en"]) or row["name_en"])


def _tries(row):
    return [t % _who(row) for t in _TRIES]


def _yen(v):
    if v is None:
        return "—"
    sign = "−" if v < 0 else ""
    v = abs(v)
    return "%s¥%.2ftn" % (sign, v / 1e12) if v >= 1e12 else "%s¥%.1fbn" % (sign, v / 1e9) if v >= 1e9 else "%s¥%sm" % (sign, "{:,.0f}".format(v / 1e6))


def _pct(v, digits=2):
    return "—" if v is None else ("%.*f%%" % (digits, v)).replace("-", "−")


def _pp(v):
    return "—" if v is None else "%s%.2f pp" % ("+" if v > 0 else "−" if v < 0 else "", abs(v))


def _month(d):
    d = str(d)[:10]
    return datetime.date(int(d[:4]), int(d[5:7]), 1).strftime("%B %Y")


def _day(d):
    d = datetime.date.fromisoformat(str(d)[:10])
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def _holder(name):
    """A holder as filed, without the Tokyo agent the register adds in brackets."""
    n = " ".join(re.split(r"[（(]常任代理人", name or "")[0].split())
    return (jp_names.display_name(n) or n) if n == n.upper() else n      # only a name filed in capitals is re-cased: BCPE stays BCPE


def _pkg(recipe, label, row, question, headline, text, table, chart, sources, calc, page, notes):
    return {"recipe": recipe, "question": question, "headline": re.sub(r"(?<=s)'s\b", "'", headline), "answer": text, "figures": [], "table": table, "chart": chart,
            "sources": sources, "calc": calc, "entities": {"company": {"name": _who(row), "sec_code": row["code"]}},
            "links": {"company": "/%s" % page}, "notes": notes,
            "trust": {"filed": "official", "prices": "official", "ratios": "derived"},
            "match": {"recipe": recipe, "label": label, "score": None, "alternatives": [], "via": "japan", "read_as": None},
            "did_you_mean": []}


# ------------------------------------------------------------------ answers
def _r_price(row, question):
    raise ask_miss.miss("%s, so I can't show %s's share price or market value. Its results and who owns it are." % (NO_PRICES, _who(row)), "not_held", _tries(row))


def _groups(code):
    """The filing groups in the 5% reports, with a group that changed lead filer
    joined up: Kioxia's Bain vehicles report under two filer numbers, and counting
    both would count one stake twice."""
    from . import lvh_api
    d = lvh_api.company(code, history=500)
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def member(h):
        return h.get("holder_edinet_code") or " ".join((h.get("name_en") or h.get("name_raw") or "").lower().split())

    for g in d["groups"]:
        filer = g.get("filer_edinet_code") or g.get("filer_name")
        for h in g.get("holders") or []:
            if member(h):
                parent[find(member(h))] = find(filer)
    families = {}
    for g in d["groups"]:
        filer = g.get("filer_edinet_code") or g.get("filer_name")
        f = families.setdefault(find(filer), {"reports": [], "members": set()})
        for h in g.get("holders") or []:
            f["members"].add(" ".join(_holder(h.get("name_en") or h.get("name_raw")).lower().split()))
            if h.get("holder_edinet_code"):
                f["members"].add(h["holder_edinet_code"])
    for r in d["reports"]:
        key = find(r.get("filer_edinet_code") or r.get("filer_name"))
        if key in families and r.get("ratio_pct") is not None:
            families[key]["reports"].append(r)
    by_doc = {g["doc_id"]: g for g in d["groups"]}
    out = []
    for f in families.values():
        if not f["reports"]:
            continue
        f["reports"].sort(key=lambda r: (str(r["filed_date"]), r.get("change_no") or 0))
        last = f["reports"][-1]
        holders = (by_doc.get(last["doc_id"]) or {}).get("holders") or []
        lead = next((h for h in holders if (h.get("shares_held") or 0) > 0), holders[0] if holders else {})
        joint = sum(1 for h in holders if (h.get("shares_held") or 0) > 0) > 1
        f["name"] = _holder(lead.get("name_en") or lead.get("name_raw") or last.get("filer_name")) + (" and joint holders" if joint else "")
        f["last"] = last
        out.append(f)
    return out


def _window(question):
    """The period asked about, default this calendar year, and how to say it."""
    from . import us_link
    if us_link.unread_time(question):
        raise ask_miss.miss("I can read periods like “this year”, “last 6 months” or “since March”, but not “%s”." % us_link.unread_time(question)[0], "unclear")
    since = us_link.parse_since(question) or datetime.date.today().replace(month=1, day=1)
    return since, _day(since)


def _r_stakes(row, question):
    since, when = _window(question)
    selling, buying = bool(_SELLING.search(question)), bool(_BUYING.search(question))
    moves = []
    for f in _groups(row["code"]):
        seen = [r for r in f["reports"] if str(r["filed_date"]) >= since.isoformat()]
        if not seen:
            continue
        first = seen[0]
        start = first["prior_ratio_pct"] if first.get("prior_ratio_pct") is not None else first["ratio_pct"]
        end = f["last"]["ratio_pct"]
        moves.append({"name": f["name"], "start": start, "end": end, "change": round(end - start, 2), "date": f["last"]["filed_date"]})
    moves.sort(key=lambda m: m["change"])
    who = _who(row)
    down, up = [m for m in moves if m["change"] < 0], [m for m in reversed(moves) if m["change"] > 0]
    side = (down, up) if selling or not buying else (up, down)
    lead, other = side
    word, other_word = ("cut", "raised") if lead is down else ("raised", "cut")
    if not moves:
        raise ask_miss.miss("No holder has filed a change in its %s stake since %s. Only holders of 5%% or more have to report, so smaller sales wouldn't show." % (who, when), "not_held", _tries(row))
    if lead:
        named = "; ".join("%s from %s to %s" % (m["name"], _pct(m["start"]), _pct(m["end"])) for m in lead[:2])
        headline = "Since %s, the biggest disclosed %s in %s: %s." % (when, "sellers" if lead is down else "buyers", who, named)
    else:
        headline = "No holder of 5%% or more has reported %s %s shares since %s." % ("selling" if lead is down else "buying", who, when)
    bits = ["These are the stakes filed in Japan's 5% reports, the only sales or purchases a holder has to disclose."]
    if other:
        bits.append("%s %s: %s." % (other_word.capitalize(), "their stakes" if len(other) > 1 else "its stake", "; ".join("%s to %s" % (m["name"], _pct(m["end"])) for m in other[:2])))
    notes = ["A holder files when its stake moves by 1% or more, and stops filing once it is under 5%, so smaller sellers don't show and a last report can be months old.",
             "A stake can also fall because the company issued new shares.",
             "The percentage is the filing's own holding ratio, which counts the holder's convertible rights."]
    rows = [[m["name"], _pct(m["start"]), _pct(m["end"]), _pp(m["change"]), str(m["date"])] for m in moves]
    chart = {"kind": "bars", "title": "Change in filed stakes in %s since %s" % (who, when), "unit": "pp",
             "series": [{"name": "Change", "points": [[m["name"][:28], m["change"]] for m in moves[:10]]}]}
    return _pkg("jp_stakes", "Who is buying or selling", row, question, headline, " ".join(bits),
                {"columns": ["Holder", "Stake before", "Latest stake", "Change", "Last report"], "rows": rows}, chart,
                [{"name": "EDINET 5% filings", "credit": "Source: large shareholding reports filed on EDINET (Financial Services Agency of Japan).", "note": "As filed."}],
                {"change": "latest filed holding ratio − the holding ratio before the first report in the period, both as filed (percentage points)"},
                "stakes.html?c=%s" % row["code"], notes)


def _r_owners(row, question):
    from . import ownership_api
    who = _who(row)
    try:
        families = [f for f in _groups(row["code"]) if (f["last"]["ratio_pct"] or 0) >= 5]
    except HTTPException:
        families = []
    families.sort(key=lambda f: -f["last"]["ratio_pct"])
    try:
        reg = ownership_api.company(row["code"], year="")
    except HTTPException as exc:
        if exc.status_code != 404 or not families:
            raise
        reg = {"holders": []}
    period = reg.get("period_end")
    held_in_group = set().union(*[f["members"] for f in families]) if families else set()
    rows = [[f["name"], _pct(f["last"]["ratio_pct"]), str(f["last"]["filed_date"]), "5% filing"] for f in families]
    nominees, named_holder = 0, None
    for h in reg["holders"]:
        name = _holder(h.get("holder_name_en") or h.get("name_raw"))
        if h.get("holder_edinet_code") in held_in_group or " ".join(name.lower().split()) in held_in_group:
            continue
        custody = h.get("holder_kind") in NOMINEE_KINDS
        nominees += custody
        named_holder = named_holder or (None if custody else (name, h.get("ratio_pct")))
        rows.append([name + (" (custody account)" if custody else ""), _pct(h.get("ratio_pct")), str(period), "Shareholder register"])
    if not rows:
        raise ask_miss.miss("No shareholder list is on file for %s." % who, "not_held", _tries(row))
    if families:
        named = "; ".join("%s, %s (reported %s)" % (f["name"], _pct(f["last"]["ratio_pct"]), _month(f["last"]["filed_date"])) for f in families[:2])
        headline = "The largest disclosed stakes in %s: %s." % (who, named)
    elif named_holder:
        headline = "%s is %s's largest holder named on the register, with %s at %s." % (named_holder[0], who, _pct(named_holder[1]), _day(period))
    else:
        headline = "%s's largest registered holders are custody accounts, which hold for clients, so the owners behind them aren't named." % who
    bits = ["5% filings give the latest position and count a holder's vehicles together; the rest is the shareholder register from the annual report."]
    # A register can type a count into its percentage column (Sportsfield's
    # 5,860%); such a figure is not a share and is not quoted.
    if reg.get("foreign_pct") is not None and 0 <= reg["foreign_pct"] <= 100:
        bits.append("Foreign investors held %s of the shares at %s." % (_pct(reg["foreign_pct"]), _day(period)))
    notes = ["A custody account is a bank holding for its clients, so it does not show who really owns the shares."] if nominees else []
    notes.append("Register figures and 5% figures use slightly different denominators, so they don't add to a total.")
    return _pkg("jp_owners", "Who owns it", row, question, headline, " ".join(bits),
                {"columns": ["Holder", "Stake", "As of", "From"], "rows": rows}, None,
                [{"name": "EDINET filings", "credit": CREDIT, "note": "5% reports and the annual report's shareholder sections, as filed."}], {},
                "ownership.html?c=%s" % row["code"], notes)


def _period_words(rel):
    fy = str(rel.get("fiscal_period") or "")
    months = rel.get("months_covered")
    if not re.fullmatch(r"\d{4}-\d\d", fy) or not months:
        return rel.get("period_label") or "the latest period"
    y, m = int(fy[:4]), int(fy[5:7])
    total = y * 12 + (m - 1) - 12 + months
    end = datetime.date(total // 12, total % 12 + 1, 1).strftime("%B %Y")
    if months == 12:
        return "the year to %s" % end
    return "the %s to %s" % ({3: "three months", 6: "six months", 9: "nine months"}.get(months, "%d months" % months), end)


def _change_words(r):
    if r.get("yoy_pct_published") is not None:
        p = r["yoy_pct_published"]
        return "%s %s%% on a year earlier" % ("up" if p >= 0 else "down", "{:,.1f}".format(abs(p)))
    if r.get("prior_value") is not None:
        return "against %s a year earlier" % _yen(r["prior_value"])
    return None


def _r_results(row, question):
    from . import earnings_api
    who = _who(row)
    try:
        d = earnings_api.company(row["code"], doc_key="", wire=0)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return _r_figure(row, question, ("revenue", "", "Revenue", "yen"))
    rel = d.get("release") or {}
    by = {r["line"]: r for r in d.get("results") or []}
    if not by:
        return _r_figure(row, question, ("revenue", "", "Revenue", "yen"))
    when = _period_words(rel)
    parts = []
    for line, word in (("revenue", "revenue"), ("profit", "profit")):
        r = by.get(line)
        if r and r.get("value") is not None:
            change = _change_words(r)
            parts.append("%s was %s%s" % (word, _yen(r["value"]), " (%s)" % change if change else ""))
    headline = "In %s, %s's %s." % (when, who, " and ".join(parts))
    rows = []
    for r in d["results"]:
        money = r.get("unit") == "JPY"
        show = _yen if money else (lambda v: "—" if v is None else "¥%s" % "{:,.2f}".format(v))
        rows.append([r["label"], show(r.get("value")), show(r.get("prior_value")), _pct(r.get("yoy_pct_published"), 1)])
    notes = ["%s, so this shows results, not how the shares have moved." % NO_PRICES]
    if (rel.get("months_covered") or 0) not in (0, 3, 12):
        notes.append("Japanese interim results add up from the start of the fiscal year.")
    fc = next((f for f in d.get("forecast") or [] if f.get("line") == "revenue" and f.get("value") is not None), None)
    text = "From its earnings release of %s (%s)." % (_day(rel["filed_date"]), rel.get("period_label"))
    if fc:
        text += " The company's own full-year revenue forecast is %s." % _yen(fc["value"])
    return _pkg("jp_results", "Latest results", row, question, headline, text,
                {"columns": ["Line", "This period", "Year earlier", "Change, as published"], "rows": rows}, None,
                [{"name": "TDnet earnings releases", "credit": TDNET, "note": "As published."}], {}, "earnings.html?c=%s" % row["code"], notes)


def _fig_show(v, kind):
    if v is None:
        return "—"
    return {"yen": _yen, "pct": _pct, "per_share": lambda x: "¥%s" % "{:,.2f}".format(x), "count": lambda x: "{:,.0f}".format(x)}[kind](v)


def _r_figure(row, question, fig=None):
    from . import financials_api
    who = _who(row)
    fig = fig or _figure_in(question.lower())
    d = financials_api.company(row["code"], basis="consolidated", as_filed_in="")
    panel = [p for p in d.get("panel") or [] if (p.get("values") or {}).get(fig[0]) is not None]
    if not panel:
        raise ask_miss.miss("No annual figure for %s is filed for %s." % (fig[2].lower(), who), "not_held", _tries(row))
    panel.sort(key=lambda p: str(p["fiscal_year_end"]))
    m = _YEAR.search(question)
    pick = panel[-1]
    if m:
        found = [p for p in panel if str(p["fiscal_year_end"])[:4] == m.group(1)]
        if not found:
            raise ask_miss.miss("%s's reports on file cover fiscal years ending %s to %s, not %s." % (who, str(panel[0]["fiscal_year_end"])[:4], str(panel[-1]["fiscal_year_end"])[:4], m.group(1)), "not_held", _tries(row))
        pick = found[-1]
    i = panel.index(pick)
    value = pick["values"][fig[0]]
    headline = ("%s had %s employees at %s" % (who, _fig_show(value, "count"), _month(pick["fiscal_year_end"])) if fig[3] == "count" else
                "%s's %s was %s in the year to %s" % (who, fig[2].lower() if fig[2][:3] not in ("Ear",) else "earnings per share", _fig_show(value, fig[3]), _month(pick["fiscal_year_end"])))
    calc = {}
    if i and fig[3] == "yen" and panel[i - 1]["values"][fig[0]] and panel[i - 1]["values"][fig[0]] > 0:
        change = (value / panel[i - 1]["values"][fig[0]] - 1) * 100
        headline += ", %s %.1f%% on the year before" % ("up" if change >= 0 else "down", abs(change))
        calc["change"] = "(this year ÷ last year − 1) × 100, both as filed"
    rows = [[str(p["fiscal_year_end"]), _fig_show(p["values"][fig[0]], fig[3])] for p in panel]
    chart = None
    if fig[3] == "yen" and len(panel) > 1:
        chart = {"kind": "bars", "title": "%s %s" % (who, fig[2].lower()), "unit": "¥bn",
                 "series": [{"name": fig[2], "points": [[str(p["fiscal_year_end"])[:7], p["values"][fig[0]] / 1e9] for p in panel]}]}
    return _pkg("jp_figure", fig[2], row, question, headline + ".", "As filed in each annual securities report, group figures.",
                {"columns": ["Fiscal year end", fig[2]], "rows": rows}, chart,
                [{"name": "EDINET annual securities reports", "credit": CREDIT, "note": "As filed."}], calc,
                "financials.html?c=%s" % row["code"], [])


def _r_shorts(row, question):
    from . import short_api
    who = _who(row)
    d = short_api.company(row["code"], history=200)
    open_ = [p for p in d.get("positions") or [] if p.get("is_open")]
    if open_:
        headline = "Disclosed short positions in %s add up to %s of the shares, across %d holder%s." % (who, _pct(d.get("disclosed_short_pct")), len(open_), "" if len(open_) == 1 else "s")
    else:
        headline = "No short position of 0.5%% or more in %s is open in the reports on file." % who
    rows = [[p.get("holder_name"), _pct(p.get("ratio_pct")), str(p.get("calc_date")), "Open" if p.get("is_open") else "Closed"] for p in d.get("positions") or []]
    cover = d.get("coverage") or {}
    notes = ["Only positions of 0.5%% or more are disclosed, so the true short interest is higher. The record begins %s." % (_day(cover["first_published"]) if cover.get("first_published") else "when capture began")]
    return _pkg("jp_shorts", "Short positions", row, question, headline, "From the daily reports the Japan Exchange Group publishes.",
                {"columns": ["Holder", "Short position", "As of", "Status"], "rows": rows}, None,
                [{"name": "JPX short positions", "credit": JPX, "note": "As published."}], {}, "shorts.html?c=%s" % row["code"], notes)


def _r_buybacks(row, question):
    from . import buyback_api
    who = _who(row)
    d = buyback_api.company(row["code"])
    programs = d.get("programs") or []
    if not programs:
        raise HTTPException(404, "no buybacks")
    p = programs[0]
    headline = "%s's latest buyback: %s bought of %s authorised, %s." % (who, _yen(p.get("cumulative_yen")), _yen(p.get("authorised_yen")), (p.get("lifecycle") or "status unknown").replace("_", " "))
    rows = [[str(x.get("resolution_date")), _yen(x.get("authorised_yen")), _yen(x.get("cumulative_yen")), _pct(x.get("completion_pct"), 1), str(x.get("last_as_of"))] for x in programs]
    return _pkg("jp_buybacks", "Buybacks", row, question, headline, "From the monthly buyback reports filed on EDINET.",
                {"columns": ["Board decision", "Authorised", "Bought", "Done", "Reported to"], "rows": rows}, None,
                [{"name": "EDINET buyback reports", "credit": "Source: buyback status reports filed on EDINET (Financial Services Agency of Japan).", "note": "As filed."}],
                {"done": "yen bought ÷ yen authorised × 100"}, "buyback.html?c=%s" % row["code"],
                ["EDINET removes these reports after about a year, so older buybacks are not on file."])


def _r_business(row, question):
    from . import company_labels, equity_api, segments_api
    who = _who(row)
    d = segments_api.company(row["code"])
    industry_en = equity_api.INDUSTRY_EN.get((row.get("industry") or "").strip())
    f = d.get("filing") or {}
    latest = [r for r in d.get("regions") or [] if r.get("year_offset") == 0 and r.get("measure", "revenue") == "revenue" and not r.get("is_subnote") and r.get("value_yen") is not None] or \
             [r for r in d.get("regions") or [] if not r.get("is_subnote") and r.get("value_yen") is not None]
    latest.sort(key=lambda r: -(r["value_yen"] or 0))
    products = [p for p in d.get("products") or [] if p.get("year_offset", 0) == 0]
    headline = "%s is a Tokyo-listed company (%s)%s." % (who, row["code"], " in %s" % industry_en if industry_en else "")
    bits = []
    label = next((v for v in company_labels.COMPANIES.values() if v.get("sec_code") == row["code"]), None)
    if label and label.get("tags"):
        headline = "%s is a Tokyo-listed company (%s) in the %s business." % (who, row["code"], " and ".join(label["tags"]))
    if f.get("consolidated_revenue_yen"):
        bits.append("Revenue was %s in the year to %s." % (_yen(f["consolidated_revenue_yen"]), _month(f["period_end"])))
    if f.get("single_segment"):
        bits.append("It reports one business segment.")
    rows = [[r.get("region_label_en") or r.get("label_ja"), _yen(r["value_yen"]), _pct(r.get("share_pct"), 1)] for r in latest[:10]]
    table = {"columns": ["Where revenue came from", "Revenue", "Share"], "rows": rows} if rows else None
    return _pkg("jp_business", "What it does", row, question, headline, " ".join(bits), table, None,
                [{"name": "EDINET segment notes", "credit": d.get("credit_line") or CREDIT, "note": "As filed."}], {},
                "company.html?code=%s" % row["code"],
                ["The company page lists everything Plover holds on it, including its stated business risks."] + ([] if not products else ["Its product lines are on the company page."]))


_RUN = {"price": _r_price, "stakes": _r_stakes, "owners": _r_owners, "results": _r_results, "figure": _r_figure,
        "shorts": _r_shorts, "buybacks": _r_buybacks, "business": _r_business}
