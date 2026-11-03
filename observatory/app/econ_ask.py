# -*- coding: utf-8 -*-
"""The question box's reader for the whole economic catalogue.

On 2026-11-03 "Draw Japan interest payments as % of Government expenses for last
5 yrs" was told "Plover's data doesn't include Japan's government interest payments
or government expenses", with both on file (govt-accounts-jp). The cause was not
that question. The box had two readers: the US-stock route, whose model sees only
stock tables, and a hand-written list of about 21 economic datasets, out of 63. A
model that saw only stock tables was speaking for all of Plover, and a dataset
nobody had added to the list could not be reached at all.

So this reads the registry, not a list, and a dataset is reachable the day its
manifest ships:

  1. The model sees every series dataset's name and summary, and picks where to look.
  2. It sees the series in those datasets, and picks them and one calculation.
  3. Code loads them through the API's own function (analysis.load), does the
     arithmetic and writes every figure. No number the model wrote reaches a reader.

"Not held" is said only after step 2 has looked, and it names the thing asked for.
The model picks from a closed list of calculations, each with its formula shown
(the trust contract); a series code it did not get from us is refused. It fails
closed: a model that is off, slow or unsure leaves the refusal that brought us here.
"""
import datetime
import json
import re
import threading

from fastapi import HTTPException

from . import analysis, api, ask_miss, ask_progress, registry, stock_recipes, us_ask_model
from .tools import call_api

DATASETS_MAX = 4             # datasets opened for one question
NAME_DATASETS_MAX = 3        # and those found by the words in series names
SECOND_LOOK_SHOWN = 300      # and on the second look, before "not held" reaches a reader
SECOND_LOOK_DATASETS = 3
SERIES_SHOWN = 90            # series the model reads per dataset: its totals first, then what the words find
READ_SECONDS = 15
YEARS_DEFAULT = 5
CALCS = ("level", "share", "difference", "yoy", "total", "annual_sum")
# names that read like a stock and are one period's change. "General government — Liabilities — debt securities"
# is that year's net borrowing: 0.9% of GDP, read on 2026-11-03 as Japan's debt to GDP
FLOW_NAMES = {"govt-accounts-jp": re.compile(r"\.3[23]\d*$")}
STALE_YEARS = 4              # a series with nothing this recent stopped: not an answer unless that period was asked for
_GDP_ANNUALISED = re.compile(r"Gross domestic product.*Nominal, seasonally adjusted", re.I)
TOTAL_SERIES_MAX = 400       # the destinations added up for one product

# yen amounts in these units are the same kind of thing at different scales
YEN_UNITS = {"¥": 1.0, "jpy": 1.0, "yen": 1.0, "jpy_1000": 1e3, "¥1000": 1e3, "¥1,000": 1e3, "¥thousand": 1e3, "¥mn": 1e6, "jpy_million": 1e6,
             "¥100mn": 1e8, "jpy_100mn": 1e8, "¥bn": 1e9, "jpy_billion": 1e9, "¥tn": 1e12}
# a customs line names one destination: added up across them, there is no published total to disagree with
_PARTNER = re.compile(r"^(.*— (?:exports to|imports from) )(.+?)( \((?:value|quantity)\))$")
RATE_UNITS = re.compile(r"%|percent|^pct$|\bpp\b|^index$|\bindex\b|=\s*100|yoy|ratio|^x$", re.I)

PICK_DATASETS = """You read a question for Plover, which holds official economic statistics for Japan, the United States and Hong Kong, and company data separately.
Today is {today}. These are all the economic datasets it holds:

{catalogue}

Reply with one JSON object:
{{"about": "economy" or "company" or "other",
  "datasets": ["up to {most} dataset ids, most likely first"],
  "words": ["2 to 6 English words or short phrases to look for in series names"]}}

"economy": a question about a country, a government, a price, a market-wide statistic or anything these datasets could measure, even if worded loosely. "company": it is about one or more companies or stocks, including any name that could be a company or fund and is not a country, city, central bank or government body, even one you have never heard of ("Foo capex", "Airbus deposits"). "other": advice, forecasts, greetings, gibberish.
Interest paid, debt, deficits, spending and tax are government finance: the whole-government accounts and the fiscal tables. Choose any dataset that might hold the figure, even if you are not sure. No country named: choose whichever market's datasets might hold it."""

PICK_SERIES = """You choose the series that answer a question for Plover. Today is {today}. You choose; code loads the numbers and does the arithmetic. Never state a figure.

The series, as dataset:code | name | unit:

{series}

Reply with one JSON object:
{{"found": true or false,
  "series": ["dataset:code", ...],
  "calc": "level" or "share" or "difference" or "yoy" or "total" or "annual_sum",
  "start": "YYYY" or null, "end": "YYYY" or null, "years": a whole number or null,
  "title": "what the answer shows, in a few plain words, naming the country and who is measured",
  "note": "one short sentence on what was chosen and how it differs from what the words could also mean, or null; no numbers",
  "alternative": null,
  "compare_destinations": true only when the question names the destinations it compares, else false,
  "missing": "when found is false: the thing asked for, in a few plain words, and the market if one was named",
  "closest": [{{"ref": "dataset:code", "question": "a short question that series answers"}}]}}

Calculations:
- level: the series as published (1 to 4 series; several only when the question compares them).
- share: first series as a percent of the second (exactly 2). "as % of", "as a share of", "to GDP" is this.
- difference: first minus second (exactly 2, same unit).
- yoy: change on a year earlier, for one series. Use for growth or for inflation from a price index.
- annual_sum: one monthly or quarterly series added up by calendar year, for "how many in a year" or "last year". Only for amounts that add up (visitors, exports, purchases); never a balance, a stock, a price or a rate.
- total: one series named "… exports to X" or "… imports from X"; code adds that product across every destination. Use when a product is asked for with no destination named, on the widest line that fits ("Japan energy imports": Mineral fuels (all), by value). A year asked for ("last year"): annual_sum on that line instead; code adds the destinations too.

Rules:
- Use a series only if it measures what was asked. A named country means that country's series; never another country's in its place. If it is held for another market only, found is false and "missing" says so, as in "US government interest payments (held for Japan only)".
- "share" needs two amounts measured the same way, for the same people and dates: interest paid over expense for the same sector, a line over its own total. Never mix a budget with a settlement, or one sector with another. Two series from one table are best.
- Say "central" or "general" government in the title when you pick one. "Government" with nothing else: general government when the table has it. Interest paid is not debt service, which includes repaying principal: the note says so if you use either in place of the other.
- A period asked for goes in start/end or years ("last 5 yrs": years 5). None asked: years {years}.
- Answer whenever a series measures the subject, even if the words differ or it is a little narrower or wider: "hotel guest nights" is guest nights; "tourists" is visitor arrivals. The note says what was used. A loose question takes the headline series, for the whole country.
- Debt or spending "to GDP": the amount over nominal GDP, seasonally adjusted, as a share; the note says whose debt it is and that GDP is annualised.
- Debt means the amount outstanding (a stock, such as total government debt). "Net incurrence", "net purchases" and the like are one period's change (a flow): never use one for a stock, or divide a flow by a stock.
- The two series of a share or difference must come at the same frequency: annual with annual, quarterly with quarterly. One exception: a yearly amount over nominal seasonally adjusted GDP, which code averages over that year ("tax revenue as % of GDP", "defence spending as % of GDP").
- Use the series whose name says what was asked: "interest" is Interest paid, not debt service. A series marked "stopped in" a year is not an answer unless that period was asked for.
- If only parts are on file and "total" does not apply, show up to 4 of the main parts as level; the title says "by" what, and the note says no total is on file.
- found is false only when nothing here measures the subject at all. closest then lists up to 3 series that are truly related, from this list only.
- "alternative": the other common reading of a loose question, if one would change the answer: {{"question": "the question, whole", "label": "a few plain words"}}, else null. "Government" read as general government: the alternative asks for central government."""


class _Lost(Exception):
    """The reader could not finish: the refusal that brought us here stands."""


_CACHE = {}
_LOCK = threading.Lock()
_CACHE_MAX = 500


def datasets():
    """Every series dataset on this server: the registry, nothing hand-listed."""
    out = []
    for mid in registry.ids():
        m = registry.get(mid) or {}
        if m.get("shape") == "series" and registry.available(mid):
            out.append(m)
    return out


def catalogue_text(ms=None):
    lines = []
    for m in (datasets() if ms is None else ms):
        name = (m.get("name") or {}).get("en") or m["id"]
        lines.append("%s | %s | %s | %s" % (m["id"], name, m.get("frequency") or "", re.sub(r"\s+", " ", m.get("summary") or "")[:170]))
    return "\n".join(lines)


def _word_forms(words):
    out = set()
    for w in words or []:
        w = str(w).lower().strip()
        if len(w) < 3:
            continue
        out.add(w)
        out.update(x for x in re.findall(r"[a-z]{4,}", w))
    return {re.sub(r"(?:ies|s)$", "", w) for w in out}


def series_in(dataset, words, most=SERIES_SHOWN):
    """This dataset's series the model may choose from. A long list
    keeps its first rows, where publishers put the totals, and every name the words find."""
    forms = _word_forms(words)
    con = api._con()
    try:                                                          # names only: reading every value took a minute on population-jp
        rows = api._series_map(con, dataset)
        stopped = dict(con.execute("SELECT o.series_id, year(max(o.period)) FROM observations o JOIN series s ON o.series_id = s.series_id "
                                   "WHERE s.dataset = ? AND o.value IS NOT NULL GROUP BY o.series_id", [dataset]).fetchall())
        cutoff = datetime.date.today().year - STALE_YEARS
        rows = [dict(s, stopped=stopped[s["series_id"]]) if stopped.get(s["series_id"], cutoff) < cutoff else s for s in rows]
        flows = FLOW_NAMES.get(dataset)
        if flows:
            rows = [dict(s, one_period_change=True) if flows.search(s["code"]) else s for s in rows]
    finally:
        con.close()
    # a customs table is thousands of destinations of a few products: one line per product, plus any destination named
    lines, kept = set(), []
    for s in rows:
        m = _PARTNER.match(s.get("name_en") or "")
        if m:
            line = (m.group(1), m.group(3))
            if line in lines and not any(f in m.group(2).lower() for f in forms):
                continue
            lines.add(line)
        kept.append(s)
    rows = kept
    if len(rows) <= most:
        return rows
    # a word most names carry ("government") tells nothing: rarer words count for more, so "interest" keeps its row
    names = [(s.get("name_en") or "").lower() for s in rows]
    weight = {f: 1.0 / max(1, sum(1 for n in names if f in n)) for f in forms}

    def score(s):
        name = (s.get("name_en") or "").lower()
        return sum(w for f, w in weight.items() if f in name)
    hits = sorted((s for s in rows if score(s)), key=lambda s: -score(s))[:most]
    keep = {s["code"] for s in hits} | {s["code"] for s in rows[:most // 3]}
    return [s for s in rows if s["code"] in keep]


def _listing(chosen, words, most=SERIES_SHOWN):
    """({ref: series}, the lines the model reads) for these datasets."""
    shown, lines = {}, []
    for d in chosen:
        for s in series_in(d, words, most):
            ref = "%s:%s" % (d, s["code"])
            listed = _listed_name(s, words)
            # a line listed as standing for every destination is never read as its one destination
            shown[ref] = dict(s, every_destination="every destination" in listed)
            lines.append("%s | %s | %s" % (ref, listed, s.get("unit") or ""))
    return shown, lines


def _told(user, why):
    return "%s\n\nYour last choice could not be used: %s. Choose again." % (user, why)


def _found_by_name(words, chosen):
    """Datasets whose series names carry the words, beside the model's picks: a dataset it
    passed over must not lead to "not held". Up to NAME_DATASETS_MAX more."""
    from . import tools_v2
    extra = []
    for w in (words or [])[:4]:
        try:
            hits = json.loads(tools_v2.search(str(w), limit=40)).get("series") or []
        except Exception:                                         # noqa: BLE001 — the model's picks still stand
            continue
        for h in hits:
            if h["dataset"] not in chosen and h["dataset"] not in extra:
                extra.append(h["dataset"])
    return extra[:NAME_DATASETS_MAX]


def _listed_name(s, words):
    """A series' name as the model reads it. A customs line stands for every destination, so
    one shown alone must not read as held for that destination only."""
    name = s.get("name_en") or s["code"]
    m = _PARTNER.match(name)
    if m and not any(f in m.group(2).lower() for f in _word_forms(words)):
        return "%s(every destination listed separately; this code stands for all, use total) %s" % (m.group(1), m.group(3).strip())
    if s.get("one_period_change"):
        name += " (that year's change, not the amount outstanding)"
    return "%s (stopped in %d)" % (name, s["stopped"]) if s.get("stopped") else name


def _ask_model(system, user, cache_key):
    cfg = us_ask_model.provider()
    if cfg is None or not us_ask_model.under_cap():
        raise _Lost("model off")
    prov, key, model = cfg
    us_ask_model._tick("calls")
    try:
        got = us_ask_model._complete(prov, key, model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                     max_tokens=500, timeout=READ_SECONDS, cache_key=cache_key, effort="low")
        m = re.search(r"\{.*\}", got.get("text") or "", re.S)
        form = json.loads(m.group(0))
    except Exception as exc:                                      # noqa: BLE001 — a time-out, a bad reply
        us_ask_model._tick("failed")
        raise _Lost(repr(exc)[:100])
    us_ask_model._tick("matched", got.get("usage"))
    if not isinstance(form, dict):
        raise _Lost("no form")
    return form


def read(question, prev=None, today=None):
    """What the model chose, checked against what it was shown: None when the
    question is not about the economy; a form with found false when nothing measures it."""
    today = today or datetime.date.today().isoformat()
    key = (today, us_ask_model._norm(question), us_ask_model._norm(prev) if prev else "")
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    ms = datasets()
    user = us_ask_model.user_message(question, prev)
    first = _ask_model(PICK_DATASETS.format(today=today, catalogue=catalogue_text(ms), most=DATASETS_MAX), user, "plover-econ-datasets")
    form = None
    if first.get("about") == "economy":
        known = {m["id"] for m in ms}
        chosen = [d for d in first.get("datasets") or [] if d in known][:DATASETS_MAX]
        chosen += [d for d in _found_by_name(first.get("words"), chosen) if d in known]
        words = list(first.get("words") or []) + [question]
        shown, lines = _listing(chosen, words)
        if not lines:
            form = {"found": False, "missing": "", "closest": []}
        else:
            system = PICK_SERIES.format(today=today, series="\n".join(lines), years=YEARS_DEFAULT)
            try:
                form = _checked(_ask_model(system, user, "plover-econ-series"), shown)
            except _Lost as exc:
                form = _checked(_ask_model(system, _told(user, exc), "plover-econ-series"), shown)
            if not form.get("found"):
                # the shortlist may have missed it: "not held" is said only after a wider look
                wide, wide_lines = _listing(chosen[:SECOND_LOOK_DATASETS], words, SECOND_LOOK_SHOWN)
                second = PICK_SERIES.format(today=today, series="\n".join(wide_lines), years=YEARS_DEFAULT)
                form, shown, system = _checked(_ask_model(second, user, "plover-econ-series"), wide), wide, second
            form["_again"] = (system, user, shown)
    with _LOCK:
        _CACHE[key] = form
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
    return form


def _checked(form, shown):
    """The model's choice with anything it was not shown taken out."""
    closest = []
    for c in form.get("closest") or []:
        if isinstance(c, dict) and c.get("ref") in shown and str(c.get("question") or "").strip():
            closest.append(str(c["question"]).strip()[:120])
    if not form.get("found"):
        return {"found": False, "missing": str(form.get("missing") or "").strip()[:120], "closest": closest[:3]}
    refs = form.get("series") or []
    calc = form.get("calc")
    if calc not in CALCS or not refs or any(r not in shown for r in refs):
        raise _Lost("a choice outside the list")
    if re.search(r"\bdebt\b|liabilit", str(form.get("title") or ""), re.I) and any(shown[r].get("one_period_change") for r in refs):
        raise _Lost("debt was asked for and that series is one year's change, not the amount outstanding")
    # several destinations of one customs line, with none asked for, is that line's total
    lines = [_PARTNER.match(shown[r].get("name_en") or "") for r in refs]
    if calc == "level" and len(refs) > 1 and all(lines) and len({(m.group(1), m.group(3)) for m in lines}) == 1 and not form.get("compare_destinations"):
        calc, refs = "total", refs[:1]
        form = dict(form, title="")                               # what it wrote described a few destinations
    across = False
    if len(refs) == 1 and shown[refs[0]].get("every_destination"):
        if calc == "level":
            calc = "total"
        elif calc in ("annual_sum", "yoy"):
            across = True
    if (calc in ("share", "difference") and len(refs) != 2) or (calc in ("yoy", "total", "annual_sum") and len(refs) != 1) or (calc == "level" and len(refs) > 4):
        raise _Lost("%s with %d series" % (calc, len(refs)))
    if calc == "total" and not _PARTNER.match(shown[refs[0]].get("name_en") or ""):
        raise _Lost("a total of something that is not a customs line")
    # the total's formula says what it is; a note written about the one line chosen would only confuse
    out = {"found": True, "calc": calc, "title": str(form.get("title") or "").strip()[:140], "note": "" if calc == "total" else _no_figures(form.get("note"))}
    out["across"] = across
    out["series"] = [{"ref": r, "name": shown[r].get("name_en") or r, "unit": shown[r].get("unit") or ""} for r in refs]
    out["start"], out["end"] = _year(form.get("start")), _year(form.get("end"))
    years = form.get("years")
    out["years"] = years if isinstance(years, int) and 0 < years <= 200 else (None if out["start"] else YEARS_DEFAULT)
    other = form.get("alternative")
    if isinstance(other, dict) and 5 <= len(str(other.get("question") or "")) <= 200:
        out["did_you_mean"] = [{"question": str(other["question"]).strip(), "label": str(other.get("label") or other["question"]).strip()[:80]}]
    return out


def _year(v):
    return str(v) if re.fullmatch(r"(?:18|19|20)\d\d", str(v or "")) else None


def _no_figures(note):
    """The model's sentence reaches the reader only if it states no figure: every number comes from code."""
    note = str(note or "").strip()[:220]
    return "" if re.search(r"\d", note) else note


def scale_of(unit):
    """Yen per one of this unit, or None when it is not a yen amount."""
    return YEN_UNITS.get(re.sub(r"\s+", "", unit or "").lower())


_OTHER_MONEY = re.compile(r"^(hkd|usd)_(thousand|million|billion)$")
_MONEY_SIGN = {"hkd": "HK$", "usd": "$"}
_MONEY_SIZE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def _period_words(day, annual, quarterly):
    d = datetime.date.fromisoformat(day[:10])
    if annual:
        return ("FY%d" % d.year) if d.month == 4 else str(d.year)
    if quarterly:
        return "Q%d %d" % ((d.month - 1) // 3 + 1, d.year)
    return d.strftime("%B %Y")


def _spacing(rows):
    """(annual, quarterly) from how far apart the dates are."""
    if len(rows) < 2:
        return False, False
    a, b = datetime.date.fromisoformat(rows[-2][0][:10]), datetime.date.fromisoformat(rows[-1][0][:10])
    gap = (b - a).days
    return gap >= 300, 80 <= gap < 300


def _number(v, unit=""):
    if v is None:
        return "—"
    whole = abs(v) >= 1000 or (not (unit or "").strip() and float(v).is_integer())
    return ("{:,.0f}" if whole else "{:,.1f}").format(v).replace("-", "−")


_UNIT_WORDS = {"person_nights": "guest nights", "percent": "%", "thousand shares": "thousand shares"}


def _amount(v, unit):
    """A figure in its own unit, or yen in the scale a reader uses."""
    money = _OTHER_MONEY.match(unit or "")
    per, sign = (_MONEY_SIZE[money.group(2)], _MONEY_SIGN[money.group(1)]) if money else (scale_of(unit), "¥")
    if per and v is not None:
        amount = v * per
        if amount == 0:
            return sign + "0"
        for size, word in ((1e12, "tn"), (1e9, "bn"), (1e6, "mn")):
            if abs(amount) >= size:
                return ("−" if amount < 0 else "") + sign + "{:,.1f}{}".format(abs(amount) / size, word)
    if (unit or "").strip() in ("%", "percent"):
        return _number(v) + "%"
    if unit == "pp":
        return ("{:,.2f}".format(v).replace("-", "−") if v is not None else "—") + " pp"
    if unit == "usd_1982_84" and v is not None:
        return "${:,.2f} in 1982-84 dollars".format(v)
    return _number(v, unit) + (" " + _UNIT_WORDS.get(unit, unit) if unit else "")


def _load(form):
    """The picked series through the API's own read: (rows, names, sources)."""
    if form["calc"] == "total" or form.get("across"):
        return _load_total(form)
    series = {"s%d" % i: s["ref"] for i, s in enumerate(form["series"])}
    two = form["calc"] in ("share", "difference")
    try:
        rows, cols, sources = analysis.load(series, join="any")
    except analysis.AnalysisError as exc:
        raise _Lost(str(exc)[:120])
    if two:
        if form["calc"] == "share" and _GDP_ANNUALISED.search(form["series"][1]["name"]) and _every_how_many_months(rows, 1) == 12:
            rows = _over_yearly_gdp(rows)
            form["gdp_averaged"] = True
        else:
            _same_frequency(rows)
            rows = _lined_up(rows)
    return rows, cols[1:], sources


def _over_yearly_gdp(rows):
    """A yearly amount with GDP for that year: the mean of the four annualised quarters from its date."""
    gdp = {r[0][:7]: r[2] for r in rows if r[2] is not None}
    out = []
    for r in rows:
        if r[1] is None:
            continue
        y, m = int(r[0][:4]), int(r[0][5:7])
        months = [(y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1) for k in range(0, 12)]
        quarters = [gdp["%04d-%02d" % t] for t in months if "%04d-%02d" % t in gdp]
        if len(quarters) == 4:                                   # part of a year is not a year's GDP
            out.append([r[0], r[1], sum(quarters) / 4])
    return out


def _every_how_many_months(rows, col):
    """The usual gap between one column's readings, in months."""
    days = [r[0] for r in rows if r[col] is not None]
    gaps = sorted((int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) for a, b in zip(days, days[1:]))
    return gaps[len(gaps) // 2] if gaps else None


def _same_frequency(rows):
    """On 2026-11-03 "Japan government debt to GDP" read 0.9%: a yearly flow over a quarterly stock,
    lined up on the quarter the year happened to be dated in. Different frequencies never divide."""
    a, b = _every_how_many_months(rows, 1), _every_how_many_months(rows, 2)
    if a and b and a != b:
        raise _Lost("one series comes every %d months and the other every %d, so they do not line up" % (a, b))


def _lined_up(rows):
    """Two series' rows on dates both have. Two tables can date one quarter differently (the
    first month or the last), so a pair with no date in common is matched by calendar quarter."""
    both = [r for r in rows if r[1] is not None and r[2] is not None]
    if both:
        return both
    def quarter(day):
        return day[:4], (int(day[5:7]) - 1) // 3
    second = {quarter(r[0]): r[2] for r in rows if r[2] is not None}
    return [[r[0], r[1], second[quarter(r[0])]] for r in rows if r[1] is not None and quarter(r[0]) in second]


def _load_total(form):
    """One product across every destination: the same customs line, added up."""
    dataset, code = form["series"][0]["ref"].split(":", 1)
    m = _PARTNER.match(form["series"][0]["name"])
    con = api._con()
    try:
        rows = api._series_map(con, dataset)
    finally:
        con.close()
    codes = []
    for s in rows:
        h = _PARTNER.match(s.get("name_en") or "")
        if h and h.group(1) == m.group(1) and h.group(3) == m.group(3):
            codes.append(s["code"])
    if not codes or len(codes) > TOTAL_SERIES_MAX:
        raise _Lost("%d destinations" % len(codes))
    by_day, count, raw = {}, 0, {}
    for i in range(0, len(codes), 8):                             # the API reads eight series at a time
        try:
            raw = call_api(api.observations, dataset=dataset, series=",".join(codes[i:i + 8]), measure="index")
        except HTTPException as exc:
            raise _Lost(str(exc.detail)[:120])
        for s in raw.get("series") or []:
            count += 1
            for p, v in s["points"]:
                if v is not None:
                    by_day[str(p)[:10]] = by_day.get(str(p)[:10], 0.0) + v
    rel, reg = raw.get("release") or {}, registry.get(dataset) or {}
    form["series"][0]["name"] = m.group(1).split(" — ")[0] + " — " + ("exports, all destinations" if "exports to" in m.group(1) else "imports, all origins") + m.group(3)
    form["destinations"] = count
    source = {"name": "s0", "series": form["series"][0]["ref"], "credit": ((reg.get("source") or {}).get("credit") or "").strip(),
              "release": rel.get("label"), "unit": raw.get("unit")}
    return [[d, by_day[d]] for d in sorted(by_day)], ["s0"], [source]


def _window(rows, form):
    """The rows in the period asked for. `years` counts back from the newest reading held."""
    if form.get("start"):
        rows = [r for r in rows if r[0][:4] >= form["start"]]
    if form.get("end"):
        rows = [r for r in rows if r[0][:4] <= form["end"]]
    if form.get("years") and rows:
        last = datetime.date.fromisoformat(rows[-1][0][:10])
        rows = [r for r in rows if r[0][:10] > "%04d-%02d-%02d" % (last.year - form["years"], last.month, min(last.day, 28))]
    return rows


def _calculate(form, rows, names):
    """([date, value...] rows, the value column's label, its unit, the formula). Code's arithmetic, and all of it."""
    calc, s = form["calc"], form["series"]
    if calc == "level":
        return rows, None, s[0]["unit"], None
    if calc == "annual_sum":
        return _annual_sum(rows, s[0]["unit"])
    if calc == "total":
        return rows, None, s[0]["unit"], "Total = the published figure for each destination, added up (%d destinations). There is no published total; a destination with no figure in a month adds nothing." % form["destinations"]
    a, b = s[0]["name"], s[-1]["name"]
    if calc == "yoy":
        by_day = {r[0][:10]: r[1] for r in rows}
        out = []
        for r in rows:
            d = datetime.date.fromisoformat(r[0][:10])
            base = by_day.get("%04d-%02d-%02d" % (d.year - 1, d.month, d.day))
            out.append([r[0], r[1], 100.0 * (r[1] / base - 1) if base and r[1] is not None and base > 0 else None])
        return [r for r in out if r[2] is not None], "Change on a year earlier, %", "%", "Change on a year earlier = (%s ÷ the same period a year before − 1) × 100" % a
    ua, ub = s[0]["unit"], s[1]["unit"]
    pa, pb = scale_of(ua), scale_of(ub)
    if ua != ub and not (pa and pb):
        raise _Lost("%s and %s are not the same kind of amount" % (ua, ub))
    if calc == "share":
        if RATE_UNITS.search(ua) or RATE_UNITS.search(ub):
            raise _Lost("a share of a rate or an index means nothing")
        ratio = (pa / pb) if (ua != ub) else 1.0
        out = [[r[0], r[1], r[2], 100.0 * r[1] * ratio / r[2] if r[1] is not None and r[2] else None] for r in rows]
        return [r for r in out if r[3] is not None], "Share, %", "%", "Share = %s ÷ %s × 100" % (a, b)
    if ua != ub:
        raise _Lost("a difference across units")
    rates = (ua or "").strip().lower() in ("%", "percent")          # a change of a percentage is not a percentage of a level
    return [[r[0], r[1], r[2], r[1] - r[2]] for r in rows if r[1] is not None and r[2] is not None], "Difference" + (" (pp)" if rates else ""), "pp" if rates else ua, "Difference = %s − %s" % (a, b)


def _short_name(name):
    """The part of a series' name that tells it from its neighbours."""
    m = _PARTNER.match(name or "")
    return m.group(2) if m else (name or "")


def _annual_sum(rows, unit):
    """Whole calendar years of a flow, added up; a year with a month or quarter missing is left out."""
    if RATE_UNITS.search(unit or ""):
        raise _Lost("a rate or an index does not add up")
    every = _every_how_many_months(rows, 1)
    if every not in (1, 3):
        raise _Lost("annual_sum needs monthly or quarterly readings")
    years = {}
    for r in rows:
        if r[1] is not None:
            years.setdefault(r[0][:4], []).append(r[1])
    out = [["%s-01-01" % y, sum(v)] for y, v in sorted(years.items()) if len(v) == 12 // every]
    return out, None, unit, "Yearly figure = the %s published figures of each calendar year, added up." % ("monthly" if every == 1 else "quarterly")


_UNIT_LABELS = {"jpy_1000": "¥ thousand", "jpy_million": "¥mn", "jpy_100mn": "¥100mn", "jpy_billion": "¥bn", "hkd_million": "HK$mn", "hkd_thousand": "HK$ thousand",
                "hkd_billion": "HK$bn", "usd_million": "$mn", "usd_billion": "$bn", "usd_thousand": "$ thousand", "person_nights": "guest nights", "percent": "%",
                "usd_1982_84": "1982-84 dollars"}


def _unit_label(unit):
    return _UNIT_LABELS.get(unit, unit or "")


def _heading(name, unit):
    return "%s (%s)" % (name, _unit_label(unit)) if unit else name


def build(question, form):
    """The answer in the box's own shape, every figure from the data."""
    form = dict(form, series=[dict(x) for x in form["series"]])   # a total renames its series; the chosen form is cached
    rows, names, sources = _load(form)
    rows, value_label, value_unit, formula = _calculate(form, rows, names)
    held_from = rows[0][0] if rows else None
    rows = _window(rows, form)
    if not rows:
        raise _Lost("no readings in that period")
    if not form.get("start") and not form.get("end") and int(rows[-1][0][:4]) < datetime.date.today().year - STALE_YEARS:
        raise _Lost("that series stopped in %s, and no old period was asked for" % rows[-1][0][:4])
    annual, quarterly = _spacing(rows)
    annual = annual or form["calc"] == "annual_sum"
    when = lambda r: _period_words(r[0], annual, quarterly)
    s = form["series"]
    title = form.get("title") or s[0]["name"]
    title = title[0].upper() + title[1:]
    latest, first = rows[-1], rows[0]
    columns = ["Period"] + [_heading(x["name"], x["unit"]) for x in s] + ([value_label] if value_label else [])
    table = {"columns": columns, "rows": [[when(r)] + [_round(v) for v in r[1:]] for r in rows]}
    figures = []
    if value_label:
        col = len(s) + 1
        shown = (_number(latest[col]) + "%") if value_unit == "%" else _amount(latest[col], value_unit)
        headline = "%s: %s in %s" % (title, shown, when(latest))
        if len(rows) >= 2 and first[col] is not None:
            was = (_number(first[col]) + "%") if value_unit == "%" else _amount(first[col], value_unit)
            headline += ", against %s in %s." % (was, when(first))
        else:
            headline += "."
        figures.append(stock_recipes.fig("%s, %s" % (value_label, when(latest)), _round(latest[col]), value_unit, None, formula))
        chart_series = [{"name": value_label, "points": [[r[0][:10], _round(r[col])] for r in rows]}]
        chart_unit = value_unit
    else:
        col = 1
        kept = [r for r in rows if r[1] is not None] or rows
        latest, first = kept[-1], kept[0]
        headline = "%s: %s in %s" % (title, _amount(latest[1], s[0]["unit"]), when(latest))
        headline += (", against %s in %s." % (_amount(first[1], s[0]["unit"]), when(first))) if len(kept) >= 2 else "."
        if len(s) > 1:
            headline = "%s, latest readings: %s." % (title, "; ".join(
                "%s %s" % (_short_name(x["name"]), _amount(next((r[i + 1] for r in reversed(rows) if r[i + 1] is not None), None), x["unit"])) for i, x in enumerate(s)))
        figures.append(stock_recipes.fig("%s, %s" % (_heading(s[0]["name"], s[0]["unit"]), when(latest)), _round(latest[1]), _unit_label(s[0]["unit"]), None))
        same = len({x["unit"] for x in s}) == 1
        chart_series = [{"name": x["name"], "points": [[r[0][:10], _round(r[i + 1])] for r in rows if r[i + 1] is not None]}
                        for i, x in enumerate(s if same else s[:1])]
        chart_unit = s[0]["unit"]
    chart = None
    if len(rows) >= 2:
        chart = {"kind": "line", "title": title, "unit": _unit_label(chart_unit), "series": chart_series}
        if annual:                                                # a fiscal year is a column, not a point on a calendar
            days = [r[0][:10] for r in rows]
            chart.update(kind="cols", categories=[when(r) for r in rows],
                         series=[{"name": x["name"], "points": [dict(x["points"]).get(d) for d in days]} for x in chart_series])
    srcs, seen = [], set()
    for src, x in zip(sources, s):
        dataset = x["ref"].split(":")[0]
        if dataset in seen:
            continue
        seen.add(dataset)
        m = registry.get(dataset) or {}
        srcs.append({"name": (m.get("name") or {}).get("en") or dataset, "credit": src.get("credit") or "",
                     "note": ("Release: %s." % src["release"]) if src.get("release") else ""})
    if form.get("across"):
        formula = (formula or "") + " Across %d destinations, added up; there is no published total." % form["destinations"]
    calc = {"series": "; ".join("%s (%s)" % (x["name"], x["ref"]) for x in s),
            "formula": formula or "Published values as released, not recomputed."}
    if formula and form["calc"] == "yoy":
        calc["limit"] = "Worked out from published figures, so it can differ from a published rate by 0.1 pp."
    if form.get("gdp_averaged"):
        calc["gdp"] = "GDP for each year is the mean of that year's four seasonally adjusted annualised quarters."
    if form["calc"] == "share" and s[0]["unit"] != s[1]["unit"]:
        calc["units"] = "%s and %s converted to the same scale before dividing." % (s[0]["unit"], s[1]["unit"])
    notes = [form["note"]] if form.get("note") else []
    if form.get("years") and held_from == rows[0][0] and len(rows) >= 2:
        last = datetime.date.fromisoformat(rows[-1][0][:10])
        if datetime.date.fromisoformat(rows[0][0][:10]) > last.replace(year=last.year - form["years"] + 1, day=1):
            notes.append("You asked for %d years. Plover holds this from %s." % (form["years"], when(rows[0])))
    sentence = ""
    if form["calc"] in ("share", "difference"):
        sentence = "In %s, %s was %s and %s was %s." % (when(rows[-1]), s[0]["name"], _amount(rows[-1][1], s[0]["unit"]), s[1]["name"], _amount(rows[-1][2], s[1]["unit"]))
    out = stock_recipes.pkg("economy", question, headline, sentence, figures, table, chart, srcs, calc, {}, {},
                            notes,
                            {"text": "%s to %s" % (when(rows[0]), when(rows[-1])), "from": rows[0][0][:10], "to": rows[-1][0][:10]})
    out["trust"] = {"published": "official", "worked_out": "derived"}
    out["did_you_mean"] = form.get("did_you_mean") or []
    out["match"] = {"recipe": "economy", "label": "Economic statistics", "score": None, "alternatives": [], "via": "economy",
                    "read_as": title}
    return out


def _round(v):
    return round(v, 4) if isinstance(v, float) else v


def refusal(form):
    """The one place the box says the economic data lacks something, after looking."""
    what = form.get("missing") or "what you asked for"
    exc = ask_miss.miss("I looked through Plover's economic data and found no series for %s." % what, "not_held", form.get("closest"))
    exc.economy = True
    return exc


def answer(question, prev=None):
    """The answer, or None when this is not an economic question or the reader could not
    finish. Raises the refusal when the catalogue was searched and nothing measures it."""
    try:
        ask_progress.say("Searching Plover's economic data…")
        form = read(question, prev)
        if form is None:
            return None
        if not form.get("found"):
            raise refusal(form)
        ask_progress.say("Working it out from the published figures…")
        try:
            return build(question, form)
        except _Lost as exc:
            # the choice could not be worked out ("a share of a rate"): told why, the model chooses once more
            system, user, shown = form["_again"]
            form = _checked(_ask_model(system, _told(user, exc), "plover-econ-series"), shown)
            if not form.get("found"):
                raise refusal(form)
            return build(question, form)
    except _Lost as exc:
        if str(exc) != "model off":
            from . import us_steps
            us_steps.log(question, "economy reader gave up", str(exc))
        return None
