# -*- coding: utf-8 -*-
"""Every filed company figure Plover holds, in one list.

On 2026-10-30 the ask box told a reader "Plover's data doesn't include capital
expenditure figures" while the fixed answers on the same page showed Alphabet's
$91.4bn. The fixed answers read the figures in stock_recipes.FIGURES and
sec_api.FIELDS; the model writing SQL (app/us_steps.py) saw only the columns
someone had added to `stocks` by hand, and said so truthfully and wrongly.

So the two now share one list, built from those two lists:

  catalogue()      one entry per figure: its column name, label, unit, the words
                   a reader uses for it. A figure added to FIGURES or FIELDS
                   gets its columns with no other edit.
  latest_values()  each company's figures from its latest annual report, for
                   the `stocks` table.
  annual_rows()    each company's figures year by year, for the `annual` table:
                   company facts (S&P 500, from 2009) and the SEC statement data
                   sets (every filer, the last few years), as last filed.
  judge()          whether a sentence saying "we don't hold X" is true, for the
                   check on every refusal the model writes.

Every value is as filed, in US dollars only: a 20-F filer's own currency is left
blank, never converted. A share of revenue is worked out the way the fixed
answers do it, and a bank's revenue is composed the same way.
"""
import datetime
import re

from . import sec_api, sec_metrics, stock_recipes

# fields whose column is not simply "<field>_usd"
_FIELD_COLUMNS = {"eps_diluted": "eps_usd", "eps_basic": "eps_basic_usd", "shares_outstanding": "shares_outstanding"}
_FIELD_LABELS = {"revenue": "revenue", "operating_income": "operating income", "pretax_income": "pre-tax income",
                 "net_income": "net income", "eps_basic": "basic earnings per share", "eps_diluted": "diluted earnings per share",
                 "total_assets": "total assets", "total_liabilities": "total liabilities", "equity": "shareholders' equity",
                 "cash": "cash and equivalents", "cf_operating": "operating cash flow", "cf_investing": "investing cash flow",
                 "cf_financing": "financing cash flow", "capex": "capital expenditure", "dividends_paid": "dividends paid",
                 "buybacks": "buybacks", "interest_income": "interest and dividend income", "noninterest_income": "non-interest income",
                 "lease_income": "lease income", "shares_outstanding": "shares outstanding"}
_FIELD_WORDS = {"interest_income": r"interest income", "noninterest_income": r"non.?interest income", "lease_income": r"lease income|rental income",
                "cf_investing": r"investing (cash|activities)|cash (flow )?from investing", "cf_financing": r"financing (cash|activities)|cash (flow )?from financing",
                "eps_basic": r"basic (eps|earnings per share)", "shares_outstanding": r"shares outstanding"}
_FIGURE_COLUMNS = {"debt": "borrowings_usd", "diluted_shares": "diluted_shares"}
# a cover-page count, not a line of the annual statements
_NOT_YEARLY = {"shares_outstanding"}
_COVER_TAGS = {t for f, _, tags in sec_api.FIELDS if f == "shares_outstanding" for t in tags}


def _column(fig_id, unit):
    if fig_id in _FIGURE_COLUMNS:
        return _FIGURE_COLUMNS[fig_id]
    return fig_id + ("_pct" if unit == "%" else "" if unit == "shares" else "_usd")


def _field_words(field):
    if field in _FIELD_WORDS:
        return _FIELD_WORDS[field]
    words = [pat for pat, fid, _, _ in stock_recipes.HIST_SPECIFIC + stock_recipes.HIST_GENERIC if fid == field]
    return "|".join(words) or re.escape(_FIELD_LABELS.get(field, field))


_CATALOGUE = []


def catalogue():
    """[{id, col, label, unit, kind, alts, ratio_to, words, field}], the fields
    first and then the figures that are not fields too."""
    if _CATALOGUE:
        return _CATALOGUE
    out, seen = [], set()
    figures = {f["id"]: f for f in stock_recipes.FIGURES}
    for field, kind, tags in sec_api.FIELDS:
        col = _FIELD_COLUMNS.get(field) or field + "_usd"
        words = _field_words(field)
        if field in figures:                                       # capex is on both lists: its own words too
            words = figures[field]["words"] + "|" + words
        out.append({"id": field, "col": col, "label": _FIELD_LABELS.get(field, field.replace("_", " ")),
                    "unit": "shares" if field == "shares_outstanding" else "USD/share" if field.startswith("eps") else "USD",
                    "kind": kind, "alts": [[t] for t in tags], "ratio_to": None, "words": words, "field": True})
        seen.add(col)
    for f in stock_recipes.FIGURES:
        col = _column(f["id"], f["unit"])
        if col in seen:
            continue
        seen.add(col)
        out.append({"id": f["id"], "col": col, "label": f["label"], "unit": f["unit"], "kind": f["kind"], "alts": f["tags"],
                    "ratio_to": f.get("ratio_to"), "words": f["words"], "field": False})
    _CATALOGUE[:] = out
    return _CATALOGUE


def _unit_words(entry):
    return {"USD": "US$", "USD/share": "US$ a share", "%": "%", "shares": "shares"}.get(entry["unit"], entry["unit"])


def stock_columns(have=()):
    """(column, type, what it means) for `stocks`, for each figure it lacks."""
    return [(e["col"], "DOUBLE", "%s in the latest annual report, %s; blank when not filed" % (e["label"], _unit_words(e)))
            for e in catalogue() if e["col"] not in have]


def annual_columns():
    """(column, type, what it means) of the `annual` table."""
    cols = [("ticker", "VARCHAR", "joins stocks.ticker"),
            ("fiscal_year", "INTEGER", "the calendar year the fiscal year ends in, as in 'fiscal 2025'"),
            ("fiscal_year_end", "DATE", "the month-end the fiscal year closes on"),
            ("filed", "DATE", "when the latest of these figures was filed"),
            ("is_latest", "BOOLEAN", "the company's most recent fiscal year on file"),
            ("fcf_usd", "DOUBLE", "free cash flow: operating cash flow less capital expenditure, US$")]
    return cols + [(e["col"], "DOUBLE", "%s, %s" % (e["label"], _unit_words(e))) for e in catalogue() if e["id"] not in _NOT_YEARLY]


def _all_tags():
    return sorted({t for e in catalogue() for alt in e["alts"] for t in alt})


def _value_of(entry, have, tags_used=None, prefer=()):
    """The figure from {tag: value}: the first alternative present (a figure
    filed in parts is their sum), as the fixed answers read it. With `prefer`,
    a line the company's own statements use comes before the rest."""
    alts = [a for a in entry["alts"] if a[0] in prefer and a[0] in have] or entry["alts"]
    for alt in alts:
        vals = [have.get(t) for t in alt]
        if alt[0] in have and any(v is not None for v in vals):
            if tags_used is not None:
                tags_used[entry["id"]] = alt[0]
            return sum(v for v in vals if v is not None)
    return None


def _ratio(value, base):
    return (value / base * 100) if (value is not None and base) else None


def _figures_from(have, sic=None, prefer=()):
    """{column: value} for one company's one year, from {tag: value} per kind."""
    values, tags_used = {}, {}
    for e in catalogue():
        if e["field"]:
            values[e["id"]] = _value_of(e, have[e["kind"]], tags_used, prefer)
    values = {k: v for k, v in values.items() if v is not None}
    sec_metrics.composite_revenue(values, tags_used, sic)
    out = {e["col"]: values.get(e["id"]) for e in catalogue() if e["field"]}
    for e in catalogue():
        if e["field"]:
            continue
        v = _value_of(e, have[e["kind"]], None, prefer)
        if e["ratio_to"]:
            v = _ratio(v, values.get(e["ratio_to"]))
        out[e["col"]] = v
    return out


def latest_values(cur, rows):
    """{cik: {column: value}} for metric rows (each one a company's latest annual
    report), read on that report's own period. Dollars only."""
    wanted = {r["adsh"]: r for r in rows if r.get("adsh") and r.get("period_end") and r.get("unit") == "USD"}
    if not wanted:
        return {}
    tags = _all_tags()
    cur.execute("CREATE OR REPLACE TEMP TABLE _fig_adsh (adsh VARCHAR)")
    cur.executemany("INSERT INTO _fig_adsh VALUES (?)", [(a,) for a in wanted])
    facts = cur.execute("""
        SELECT f.adsh, f.tag, f.ddate, f.qtrs, f.value
        FROM sec_facts f JOIN _fig_adsh a USING (adsh)
        WHERE f.segment_id IS NULL AND f.coreg IS NULL AND f.qtrs IN (0, 4) AND f.uom IN ('USD', 'USD/shares', 'shares')
          AND f.tag IN (%s)""" % ",".join("?" * len(tags)), tags).fetchall()
    cur.execute("DROP TABLE IF EXISTS _fig_adsh")
    have, cover = {}, {}
    for adsh, tag, ddate, qtrs, value in facts:
        r = wanted[adsh]
        if tag in _COVER_TAGS:
            # a cover-page count is dated weeks after the year-end, not on it
            if value is not None and ddate >= cover.get((adsh, tag), (ddate, None))[0]:
                cover[(adsh, tag)] = (ddate, value)
            continue
        if ddate.isoformat() != str(r["period_end"])[:10]:
            continue
        have.setdefault(adsh, {"duration": {}, "instant": {}})["duration" if qtrs == 4 else "instant"].setdefault(tag, value)
    for (adsh, tag), (_, value) in cover.items():
        have.setdefault(adsh, {"duration": {}, "instant": {}})["instant"][tag] = value
    return {r["cik"]: _figures_from(have[a], r.get("sic")) for a, r in wanted.items() if a in have}


def _month_end_sql(col):
    # the statement data sets round a period end to its month end (25 Sep -> 30 Sep);
    # company facts keep the day: one fiscal year must be one key (stock_recipes._month_end)
    return "CASE WHEN day(%s) < 15 THEN %s - day(%s)::INTEGER ELSE last_day(%s) END" % (col, col, col, col)


def annual_rows(cur, tickers, today=None):
    """One row per company per fiscal year: every figure, as last filed, in dollars.
    `tickers` is {cik: ticker}, the companies in `stocks`."""
    if not sec_api._has(cur, "sec_facts") or not sec_api._has(cur, "sec_filings"):
        return []
    today = today or datetime.date.today()
    tags = _all_tags()
    marks = ",".join("?" * len(tags))
    forms = list(sec_api.ANNUAL_FORMS)
    form_marks = ",".join("?" * len(forms))
    parts = ["""
        SELECT s.cik, f.tag, f.ddate AS d, f.qtrs = 4 AS is_duration, f.value, s.filed, 0 AS source
        FROM sec_facts f JOIN sec_filings s USING (adsh)
        WHERE s.form IN (%s) AND s.status IN ('clean', 'partial') AND f.segment_id IS NULL AND f.coreg IS NULL
          AND f.qtrs IN (0, 4) AND f.uom IN ('USD', 'USD/shares', 'shares') AND f.tag IN (%s)""" % (form_marks, marks)]
    args = forms + tags
    if sec_api._has(cur, "sec_cf_facts"):
        parts.append("""
        SELECT cik, tag, period_end AS d, period_start IS NOT NULL AS is_duration, value, filed, 1 AS source
        FROM sec_cf_facts
        WHERE fp = 'FY' AND form IN (%s) AND unit IN ('USD', 'USD/shares', 'shares') AND tag IN (%s)
          AND (period_start IS NULL OR period_end - period_start BETWEEN 350 AND 380)""" % (form_marks, marks))
        args += forms + tags
    rows = cur.execute("""
        WITH raw AS (%s), clean AS (
            SELECT cik, tag, %s AS end_d, is_duration, value, filed, source FROM raw
            WHERE value IS NOT NULL AND d >= DATE '1990-01-01' AND d <= ?)
        SELECT cik, end_d, tag, is_duration, arg_max(value, filed), max(filed), min(source) FROM clean
        GROUP BY cik, end_d, tag, is_duration""" % (" UNION ALL ".join(parts), _month_end_sql("d")), args + [today]).fetchall()
    last = {cik: (p, sic) for cik, p, sic in cur.execute(
        "SELECT cik, max(period), arg_max(sic, filed) FROM sec_filings WHERE form IN (%s) GROUP BY cik" % form_marks, forms).fetchall()}
    # A company's own statements name its revenue line. Company facts can carry a second, larger "Revenues"
    # (Republic Services' $19.0bn before intercompany sales, against the $16.6bn on its statements): the lines
    # the statement data sets use for the company come first, in every year (2026-10-30).
    own = {}
    years, filed_of = {}, {}
    for cik, end, tag, is_duration, value, filed, source in rows:
        if cik not in tickers:
            continue
        if source == 0:
            own.setdefault(cik, set()).add(tag)
        if not is_duration:
            # only balance-sheet dates at a fiscal year end (52/53-week years drift a week)
            fy = (last.get(cik) or (None,))[0]
            if fy is not None and not _at_year_end(end, fy):
                continue
        years.setdefault((cik, end), {"duration": {}, "instant": {}})["duration" if is_duration else "instant"][tag] = value
        if filed and (filed_of.get((cik, end)) is None or filed > filed_of[(cik, end)]):
            filed_of[(cik, end)] = filed
    best = {}
    for (cik, end), have in years.items():
        vals = _figures_from(have, (last.get(cik) or (None, None))[1], own.get(cik) or ())
        filled = sum(v is not None for v in vals.values())
        if not filled:
            continue
        # a change of year-end gives two ends in one calendar year: one row a year, the fuller one
        key = (cik, end.year)
        if key not in best or (filled, end) > (best[key][0], best[key][1]):
            best[key] = (filled, end, vals)
    newest, out = {}, []
    for (cik, _), (_, end, vals) in best.items():
        cfo, capex = vals.get("cf_operating_usd"), vals.get("capex_usd")
        rec = dict(vals, ticker=tickers[cik], fiscal_year=end.year, fiscal_year_end=end, filed=filed_of.get((cik, end)),
                   fcf_usd=(cfo - capex) if (cfo is not None and capex is not None) else None)
        rec["is_latest"] = False
        out.append(rec)
        if tickers[cik] not in newest or end > newest[tickers[cik]]["fiscal_year_end"]:
            newest[tickers[cik]] = rec
    for rec in newest.values():
        rec["is_latest"] = True
    return out


def _at_year_end(end, fy_end):
    try:
        ref = datetime.date(end.year, fy_end.month, min(fy_end.day, 28))
    except ValueError:
        return True
    near = (abs((end - ref).days), abs((end - ref.replace(year=end.year - 1)).days), abs((end - ref.replace(year=end.year + 1)).days))
    return min(near) <= 10


# ---------------------------------------------------------------- is a refusal true?
# What Plover truly does not hold, or does not do. A refusal naming one of these
# stands. The fixed reasons (stock_recipes.NOT_HELD) say the same thing in full.
GAPS = [
    r"employee|headcount|\bstaff\b|workforce|\bworkers\b",
    r"\banalyst\w*(?: \w+)?|\bratings?\b|\bprice targets?|\btarget prices?|\bconsensus\b|\bestimates?\b|\bforecasts?\b|\bguidance\b|\bpredictions?\b",
    r"short interest|short sellers?|short positions?|\boptions?\b|implied volatility|intraday|minute.by.minute|tick data|bid.ask",
    r"\bebitda\b|\bebit\b|\bpeg\b|\broic\b|\broce\b|return on (?:invested )?capital|backlog|order book|same.store|comparable sales|like.for.like",
    r"\bsubscribers?\b|\busers?\b|customer counts?|how many customers|market share|\bpatents?\b|store counts?",
    r"member lists?|members? of the (?:nasdaq|russell)|nasdaq.100 members|russell \d+ members|(?:its|their) members",
    r"pay below|below the executive|pay outside|outside the s&p|non.s&p|workers' pay",
    r"(?:a |one |single |single fund's )?(?:fund|manager)'?s? (?:own )?portfolio|which fund holds",
    r"before (?:mid.)?(?:19|20)\d\d|prior to (?:19|20)\d\d|earlier than (?:19|20)\d\d|that (?:far|early)|history (?:only )?(?:starts|begins)",
    r"explain why|\bwhy a |\bwhy it |\badvice\b|\brecommend|buy or sell|what will happen|the future",
    r"quarterly (?!revenue|results|earnings|profit|net income|operating income|eps)\w+(?: \w+){0,3}|(?:by|each|per|single) quarter",
    r"earnings calls?|\btranscripts?\b|\bnews\b|press releases?|\bsentiment\b|social media",
    r"\bcrypto|\bbitcoin\b|\bcommodit|\bcurrenc(?:y|ies)\b|exchange rates?|private compan",
    r"\bno debt\b|\bdebt.free\b|didn't tag|did not tag",
    r"\bjapan|hong kong|\bnon.us\b|foreign.listed|not (?:listed|a us)",
    r"more than eight quarters|eight quarters|beyond the last",
]
_GAP = re.compile("|".join("(?:%s)" % g for g in GAPS), re.I)
# the part of a refusal that says what is missing, before it goes on to what it can show
_CUT = re.compile(r"[;:]|\. | but | however|, so | so it |\bI can\b|\bI do hold\b|\bonly\b|\binstead\b|\bit can\b|\bit does\b|\bnearest\b", re.I)
# a question naming only what the table has, held outside the filed figures
HELD_ELSEWHERE = [
    (r"market (?:value|cap)|\bp/?e\b|price.to.earnings|dividend yield|buyback yield|payout ratio|return on equity|\broe\b|net margin|operating margin|revenue growth|"
     r"share price|stock price|price change|daily prices?|closing prices?|trading volume", "stocks and prices"),
    (r"ceo pay|ceo compensation|executive pay|\bpay ratio\b", "pay"),
    (r"quarterly (?:revenue|results|earnings|profit|net income)|last quarter", "quarters"),
    (r"segments?\b|divisions?\b|product lines?", "segments"),
    (r"insider (?:buying|selling|trades?|purchases?|sales?)", "insider_trades"),
    (r"13f|fund holdings|institutional (?:holders|ownership)", "fund_holdings"),
]


def missing_part(sentence):
    """The words of a refusal saying what is missing, not what it offers instead."""
    return _CUT.split(" ".join(str(sentence or "").split()), maxsplit=1)[0]


def held_in(text):
    """The catalogue's figures, and the other tables, a piece of text names:
    [{'label', 'where'}], whole phrases only, in catalogue order."""
    t = (text or "").lower()
    out = []
    for e in catalogue():
        try:
            hit = re.search(e["words"], t)
        except re.error:
            hit = None
        if hit:
            where = "%s in stocks (latest year)" % e["col"]
            if e["id"] not in _NOT_YEARLY:
                where += " and annual (every year)"
            out.append({"label": e["label"], "where": where, "id": e["id"]})
    for pat, table in HELD_ELSEWHERE:
        m = re.search(pat, t)
        if m:
            out.append({"label": m.group(0), "where": "the %s table%s" % (table, "s" if " and " in table else ""), "id": None})
    return out


def judge(sentence, question=""):
    """Whether "we don't hold this" is true: {'verdict': 'held' | 'gap' | 'unsure', 'names': [...]}.

      held    it names something Plover holds, or says nothing checkable about a
              question that names one: the model should try again
      gap     it names what Plover truly does not hold or do
      unsure  neither: the reader is told the box could not work it out, never
              that the data lacks it
    """
    part = missing_part(sentence)
    named_gap = bool(_GAP.search(part))
    rest = _GAP.sub(" ", part)
    held = held_in(rest)
    if held:
        return {"verdict": "held", "names": held}
    if named_gap:
        return {"verdict": "gap", "names": []}
    asked = held_in(_GAP.sub(" ", question or ""))
    if asked:
        return {"verdict": "held", "names": asked}
    return {"verdict": "unsure", "names": []}


def pointer(names):
    """What the model is told when it refused something Plover holds."""
    bits = ["%s: %s" % (n["label"], n["where"]) for n in names[:6]]
    return ("Plover does hold what you said is missing. %s. Work the answer out from those columns, and reply with the "
            "whole JSON object again. A ratio or comparison of two figures is one query over them; a year-by-year "
            "question reads annual. Say \"cannot\" again only if the question needs something else that no table holds, "
            "and then name that thing." % "; ".join(bits))
