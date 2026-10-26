# -*- coding: utf-8 -*-
"""Stock questions answered by code: the recipe grid for US equities.

A question is a shape plus blanks. "Who is buying X" is one shape and works
for every ticker; "cheapest in industry Y by P/E" is one shape and works for
every industry and ratio. This module holds the shapes, the sentence each one
writes, and the matcher that maps typed words to a shape and its blanks. No
model is involved: the match is word patterns over the question, the blanks
come from app/us_link.py, and every number comes from the same functions
that serve /api/v1.

Every answer has the same parts, so one page can show any of them:

  headline   one line in large type with the number that answers the question
  answer     at most two short sentences the headline and figures do not
             already say, each figure from the data; method and caveats
             never go here, they go in notes
  figures    the few numbers used, with unit and as-of
  table      rows to scan, one row per thing the reader asked about (a
             person, a company), not one per filing line (optional); the
             page shows the first ten and folds the rest
  notes      what to read with care, shown beside the answer
  more       {label, href}: where every underlying row lives (optional)
  chart      a line or bar spec drawn by code from the full result (optional)
  sources    where every number came from, with the credit line
  calc       the formula behind every computed figure
  entities   what the question was read as naming, so a wrong guess is visible

Trust contract: filed values are as the SEC extracted them, prices as the
vendor delivered them, and every computed figure carries its formula. A
mention that resolves to nothing is reported, never guessed.
"""
import datetime
import re
import threading

from fastapi import HTTPException

from . import api as macro_api
from . import asof
from . import sec_13f_api, sec_api, sec_shares, sec_calendar_api, sec_insiders_api, sec_metrics, us_benchmarks, us_link, us_prices_api
from .tools import call_api

_LOCK = threading.Lock()
_MEMO = {}

SEC_SOURCE = {"name": "SEC filings", "credit": sec_api.PROVENANCE["credit"],
              "note": "Figures as filed and extracted by the SEC; ratios calculated with the formula shown."}
PRICE_SOURCE = {"name": "Daily prices", "credit": us_prices_api.PROVENANCE["credit"],
                "note": "Consolidated end-of-day closes, one-day delay, not adjusted."}
INSIDER_SOURCE = {"name": "Insider filings", "credit": sec_insiders_api.PROVENANCE["credit"]}
F13_SOURCE = {"name": "13F holdings", "credit": sec_13f_api.PROVENANCE["credit"]}
UST_SOURCE = {"name": "US Treasury yield curve", "credit": "Source: U.S. Department of the Treasury."}
COMPANIES_SOURCE = {"name": "SEC company list", "credit": "Source: U.S. Securities and Exchange Commission (company_tickers_exchange.json, SIC code list)."}


# ---- formatting ------------------------------------------------------------
def money(v, dp=None):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e12:
        s = "%.2ftn" % (v / 1e12)
    elif a >= 1e9:
        s = ("%.1fbn" if a < 1e11 else "%.0fbn") % (v / 1e9)
    elif a >= 1e6:
        s = "%.1fmn" % (v / 1e6)
    elif a >= 1e3:
        s = "{:,.0f}".format(v)
    else:
        s = "%.2f" % v
    return "$" + s.replace("$-", "-$") if v >= 0 else "−$" + s.lstrip("-")


def pct(v, dp=1, sign=False):
    if v is None:
        return "—"
    s = ("%+." + str(dp) + "f") % v if sign else ("%." + str(dp) + "f") % v
    return s.replace("-", "−") + "%"


def mult(v, dp=1):
    return "—" if v is None else (("%." + str(dp) + "f") % v).replace("-", "−") + "×"


def date_words(d):
    if d is None:
        return "—"
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d[:10])
    return d.strftime("%-d %B %Y")


def month_words(d):
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d[:10])
    return d.strftime("%B %Y")


def _d(v):
    return sec_api._d(v)


def num(v, dp=0):
    return "—" if v is None else ("{:,.%df}" % dp).format(v)


def share_words(v):
    """A signed share count as a reader says it: +228.8mn, −1.2bn, +45,300."""
    if v is None:
        return "—"
    a = abs(v)
    s = ("%.1fbn" % (a / 1e9)) if a >= 1e9 else ("%.1fmn" % (a / 1e6)) if a >= 1e6 else "{:,.0f}".format(a)
    return ("+" if v > 0 else "−" if v < 0 else "") + s


# ---- data access -----------------------------------------------------------
def _memo(cur, key, build):
    v = sec_api.file_version()
    with _LOCK:
        hit = _MEMO.get(key)
        if hit and hit[0] == v:
            return hit[1]
    value = build(cur)
    with _LOCK:
        _MEMO[key] = (v, value)
    return value


def all_metric_rows(cur):
    """Every filer's latest-annual ratios with ticker, industry and price figures."""
    return _memo(cur, "metric_rows", lambda c: sec_api._decorate(c, [dict(r) for r in sec_metrics.all_rows(c)]))


def metric_rows(cur):
    """The filers a ranking may list: latest annual report ended within 18
    months. A company that stopped filing (delisted, acquired) keeps its last
    figures for a direct question, but never ranks against current ones."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=550)).isoformat()
    return [r for r in all_metric_rows(cur) if (r.get("period_end") or "") >= cutoff]


def row_for(cur, cik):
    return next((r for r in all_metric_rows(cur) if r["cik"] == cik), None)


def closes(cur, ticker, start=None, end=None):
    """[(date, split-adjusted close)] — see closes_with_splits for the events."""
    return closes_with_splits(cur, ticker, start, end)[0]


def closes_with_splits(cur, ticker, start=None, end=None):
    if not sec_api._has(cur, "us_px_daily") or not ticker:
        return [], []
    rows, info = us_prices_api.clean_closes(cur, ticker, start, end)
    _LAST_PRICE_INFO["info"] = info
    return [(d, a) for d, c, a in rows], info["splits"]


_LAST_PRICE_INFO = {"info": None}


def _sentence(text):
    t = (text or "").strip()
    t = t[:1].upper() + t[1:]
    return t if t.endswith(".") else t + "."


def _split_words(cur, rec, since):
    """[(sentence, status)] for the splits read from this company's prices since `since`."""
    sym = (rec.get("ticker") or "").replace("-", ".")
    evs = us_prices_api.split_events(cur).get(sym) or us_prices_api.split_events(cur).get(rec.get("ticker") or "") or []
    return [sec_shares.split_sentence(cur, rec["cik"], d, r) for d, r in evs if d >= since]


def cautions_for(cur, tickers):
    """'Read with care: ELVR, its share count is from 30 June 2025 …; CTVA, …' for
    the tickers given that carry a caution, at most five; None when none do."""
    c = sec_shares.cautions(cur)
    hits = []
    for t in tickers:
        if t and t in c and t not in [h[0] for h in hits]:
            hits.append((t, c[t]))
    if not hits:
        return None
    more = len(hits) - 5
    return "Read with care: %s%s." % ("; ".join("%s, %s" % h for h in hits[:5]),
                                      ("; and %d more" % more) if more > 0 else "")


def company_link(rec):
    return "/?t=%s" % rec["ticker"] if rec and rec.get("ticker") else None


def pkg(recipe, question, headline, answer, figures=None, table=None, chart=None, sources=None,
        calc=None, entities=None, links=None, notes=None, period=None):
    out = _pkg(recipe, question, headline, answer, figures, table, chart, sources, calc, entities, links, notes)
    if period:
        out["period"] = period
    return out


def _pkg(recipe, question, headline, answer, figures=None, table=None, chart=None, sources=None,
         calc=None, entities=None, links=None, notes=None):
    return {"recipe": recipe, "question": question, "headline": headline, "answer": answer,
            "figures": figures or [], "table": table, "chart": chart, "sources": sources or [],
            "calc": calc or {}, "entities": entities or {}, "links": links or {}, "notes": notes or [],
            "trust": {"filed": "official", "prices": "official", "ratios": "derived"}}


def fig(label, value, unit="", as_of=None, formula=None):
    out = {"label": label, "value": value, "unit": unit, "as_of": as_of}
    if formula:
        out["formula"] = formula
    return out


def _need(rec, what):
    if rec is None:
        raise HTTPException(404, "I could not tell which company you mean. Try a ticker, such as AAPL, or the full name.")
    return rec


# ---- compared with what ----------------------------------------------------
# A number alone does not say whether it is a lot. Each answer about one
# company sets its figure beside one or two references a reader can judge it
# by: larger companies in the same sector, the S&P 500's middle member, the
# S&P 500 itself over the same days, or the company a year earlier. Words are
# descriptive ("above", "about the same as"), never causal.
PEER_FLOOR = 2e9          # peers are companies worth at least this, so micro-caps do not set the middle
_SECTOR_WORD = {"Financials": "financial", "Industrials": "industrial", "Utilities": "utility",
                "Health Care": "health care", "Real Estate": "real estate", "Consumer Discretionary": "consumer discretionary",
                "Consumer Staples": "consumer staples", "Communication": "communication", "Technology": "technology",
                "Materials": "materials", "Energy": "energy"}


def _figure_of(r, key):
    v = r["metrics"].get(key) if key in r["metrics"] else r["size"].get(key)
    if key == "pe_x" and (v is None or v <= 0):
        return None
    return v


def peer_median(cur, row, key):
    """(median, count, label) of a figure across companies worth over $2bn in
    the row's sector, the row's own company left out; None under 8 peers."""
    from .us_steps import sector_for
    sector = sector_for(row.get("sic"))
    if not sector:
        return None
    vals = [v for r in metric_rows(cur) if r["cik"] != row["cik"] and (r["metrics"].get("market_cap") or 0) >= PEER_FLOOR
            and sector_for(r.get("sic")) == sector for v in [_figure_of(r, key)] if v is not None]
    if len(vals) < 8:
        return None
    import statistics
    return statistics.median(vals), len(vals), "large %s companies" % _SECTOR_WORD.get(sector, sector.lower())


def index_median(cur, key, ix="sp500"):
    """(median, count) of a figure across the index's members, or None."""
    ciks, _w, _as_of = us_benchmarks.members(cur, ix)
    if not ciks:
        return None
    vals = [v for r in metric_rows(cur) if r["cik"] in ciks for v in [_figure_of(r, key)] if v is not None]
    if len(vals) < 50:
        return None
    import statistics
    return statistics.median(vals), len(vals)


def index_change(cur, start, end=None, ix="sp500"):
    """The S&P 500's price change in % from the first close on or after
    `start` to the last on or before `end`, from the fund that tracks it;
    None when its prices do not reach back to within 10 days of `start`."""
    b = us_benchmarks.BENCHMARKS[ix]
    s = us_prices_api.clean_closes(cur, b["etf"], start, end)[0]
    if len(s) < 2 or (s[0][0] - (start if isinstance(start, datetime.date) else datetime.date.fromisoformat(str(start)[:10]))).days > 10:
        return None
    return (s[-1][2] / s[0][2] - 1) * 100


def rel_word(v, ref):
    """'well above', 'above', 'about the same as', 'below' or 'well below':
    a positive figure against a positive reference."""
    if v is None or ref is None or ref <= 0 or v <= 0:
        return None
    r = v / ref
    return "well above" if r >= 1.5 else "above" if r > 1.1 else "about the same as" if r >= 0.9 else "below" if r > 0.67 else "well below"


def pp_word(v, ref):
    """Two rates side by side: 'faster than' / 'slower than' / 'in line with', with the gap in pp."""
    if v is None or ref is None:
        return None
    gap = v - ref
    return "in line with" if abs(gap) < 1 else "%s pp %s" % (fmt_pp(abs(gap)), "above" if gap > 0 else "below")


INDEX_NOTE = "The S&P 500 is measured by the price of the SPY fund, which tracks it, before dividends and adjusted for splits."
def vs_sentence(cur, row, key, label):
    """'Revenue growth of 17.8% is 9.8 pp above large technology companies'
    median (8.0%) and 12.6 pp above the S&P 500's (5.2%).', or None."""
    v = _figure_of(row, key)
    if v is None:
        return None
    peer, ixm = peer_median(cur, row, key), index_median(cur, key)
    refs = []
    if peer:
        refs.append("%s %s' median (%s)" % (pp_word(v, peer[0]), peer[2], pct(peer[0])))
    if ixm:
        refs.append("%s the S&P 500's (%s)" % (pp_word(v, ixm[0]), pct(ixm[0])))
    return ("%s of %s is %s." % (label, pct(v), " and ".join(refs))) if refs else None


PEER_CALC = {"peer_median": "the middle value of the same figure across companies worth over $2bn in the same broad sector "
                            "(from the SEC industry code), each on its latest annual report, this company left out",
             "index_median": "the middle value across S&P 500 members (the SPY fund's holdings), each on its latest annual report"}


# ---- recipes: one company --------------------------------------------------
# Valuation figures a question may name that are not on the card as such:
# (words, label, unit, formula, how to compute from the metrics row)
def _ev(cur, row):
    debt = figure_cross(cur, FIGURE_BY_ID["debt"]).get(row["cik"])
    mc, cash = row["metrics"].get("market_cap"), row["size"].get("cash")
    if mc is None or not debt or cash is None:
        return None
    return mc + debt[0] - cash


def _net_debt(cur, row):
    debt = figure_cross(cur, FIGURE_BY_ID["debt"]).get(row["cik"])
    cash = row["size"].get("cash")
    return (debt[0] - cash) if (debt and cash is not None) else None


ASKED_VALUES = [
    (r"price.to.sales|p/s\b|\bps ratio|sales multiple|times sales", "price to sales", "\u00d7",
     "market value \u00f7 revenue in the latest annual report",
     lambda cur, row: (row["metrics"]["market_cap"] / row["size"]["revenue"]) if row["metrics"].get("market_cap") and row["size"].get("revenue") else None),
    (r"price.to.book|p/b\b|\bpb ratio|book multiple|times book", "price to book", "\u00d7",
     "market value \u00f7 shareholders' equity in the latest annual report",
     lambda cur, row: (row["metrics"]["market_cap"] / row["size"]["equity"]) if row["metrics"].get("market_cap") and (row["size"].get("equity") or 0) > 0 else None),
    (r"enterprise value|\bev\b", "enterprise value", "USD",
     "market value + borrowings \u2212 cash and equivalents, borrowings and cash from the latest annual report", _ev),
    (r"net debt|net cash position|net cash\b", "net debt", "USD",
     "borrowings \u2212 cash and equivalents, both from the latest annual report (negative means net cash)", _net_debt),
    (r"book value per share|\bbvps\b|tangible book", "book value per share", "USD/share",
     "shareholders' equity in the latest annual report \u00f7 shares outstanding",
     lambda cur, row: (row["size"]["equity"] / ((row.get("market_value_basis") or {}).get("shares") or row["per_share"].get("shares_outstanding")))
     if row["size"].get("equity") and ((row.get("market_value_basis") or {}).get("shares") or row["per_share"].get("shares_outstanding")) else None),
    (r"shares outstanding|share count|number of shares|how many shares", "shares outstanding", "shares",
     "shares outstanding on the cover of the latest 10-K or 10-Q (the balance-sheet count when no cover count is on file)",
     lambda cur, row: ((row.get("market_value_basis") or {}).get("shares")
                       if (row.get("market_value_basis") or {}).get("shares") is not None else row["per_share"].get("shares_outstanding"))),
    (r"\beps\b|earnings per share|diluted earnings", "diluted earnings per share", "USD/share",
     "diluted earnings per share as filed in the latest annual report", lambda cur, row: row["per_share"].get("eps_diluted")),
    (r"price.to.earnings|p/?e ratio|p/e\b|\bpe\b|earnings multiple|times earnings", "P/E", "\u00d7",
     sec_metrics.PRICE_FORMULAS["pe_x"], lambda cur, row: row["metrics"].get("pe_x")),
    (r"dividend yield", "dividend yield", "%", sec_metrics.PRICE_FORMULAS["dividend_yield_pct"],
     lambda cur, row: row["metrics"].get("dividend_yield_pct")),
]


def asked_value(question):
    ql = (question or "").lower()
    for pat, label, unit, formula, fn in ASKED_VALUES:
        if re.search(pat, ql):
            return label, unit, formula, fn
    return None


def _fmt_asked(v, unit):
    if v is None:
        return "\u2014"
    if unit == "USD":
        return money(v)
    if unit == "\u00d7":
        return mult(v)
    if unit == "%":
        return pct(v, 2)
    if unit == "USD/share":
        return "$%s" % num(v, 2)
    return num(v, 0)


def r_company_value(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    row = row_for(cur, rec["cik"])
    if row is None:
        raise HTTPException(404, "%s has no annual report loaded, so there are no figures to value it on." % rec["name"])
    m, p = row["metrics"], row.get("price") or {}
    series = closes(cur, rec["ticker"], datetime.date.today() - datetime.timedelta(days=365))
    basis = row.get("market_value_basis") or {}
    if m.get("market_cap") is not None:
        headline = "%s is worth %s." % (rec["name"], money(m["market_cap"]))
    elif p.get("close") is None:
        headline = "No market value: %s has no recent close on file." % rec["name"]
    else:
        headline = "%s closed at %s; its market value is not shown." % (rec["name"], money(p["close"]))
    bits = []
    asked = asked_value(q)
    asked_fig = None
    ql = (q or "").lower()
    if re.search(r"fiscal year|financial year|year.end\b|when does (?:its|the) year end", ql):
        pe = datetime.date.fromisoformat(row["period_end"])
        bits.append(headline)
        headline = "%s's fiscal year ends in %s; the latest annual report covers the year to %s." % (rec["name"], pe.strftime("%B"), date_words(row["period_end"]))
    elif re.search(r"\bcik\b|central index key|\bsic\b|sic code|industry code|what (?:exchange|sector|industry)|which (?:exchange|sector|industry)|listed on|sector is|industry is|what does the sec class|"
                   r"\bticker\b|\bsymbol\b|stock exchange|\bexchange\b", ql):
        from . import us_steps as _us
        sector = _us.sector_for(row.get("sic"))
        bits.append(headline)
        headline = "%s: SEC CIK %d, ticker %s on %s; SIC %s (%s)%s." % (rec["name"], rec["cik"], rec.get("ticker") or "\u2014", rec.get("exchange") or "an unknown exchange",
                                                                         row.get("sic") or "\u2014", (row.get("industry") or "no industry title").lower(), (", in the %s sector" % sector) if sector else "")
    if asked:
        # the figure the question named leads; the card follows
        label, unit, formula, fn = asked
        try:
            v = fn(cur, row)
        except Exception:                                         # noqa: BLE001 — a missing part means no figure
            v = None
        if v is None:
            lead = "%s's %s cannot be worked out: a part of it is not on file." % (rec["name"], label)
            if label == "price to book" and (row["size"].get("equity") or 0) <= 0 and row["size"].get("equity") is not None:
                lead = "%s has negative shareholders' equity, so there is no price to book." % rec["name"]
        elif label == "net debt" and v < 0:
            lead = "%s has net cash of %s: cash exceeds borrowings." % (rec["name"], money(-v))
        elif label == "shares outstanding":
            lead = "%s has %s shares outstanding." % (rec["name"], num(v, 0))
        else:
            lead = "%s: %s %s." % (rec["name"], label, _fmt_asked(v, unit))
        bits.append(headline)
        headline = lead
        asked_fig = fig(label.capitalize() if label != "P/E" else "P/E", v, unit, p.get("date") or row["period_end"], formula)
    if p.get("close") is not None and m.get("market_cap") is not None:
        # a value shown on a count to read with care says so first, and when it will update
        if basis.get("caution"):
            bits.append(basis["caution"])
            if basis.get("expect"):
                bits.append(basis["expect"])
    elif p.get("close") is not None and m.get("market_cap") is None:
        reason = basis.get("note") if basis.get("method") == "held back" or basis.get("expect") else None
        if reason:
            bits.append("Market value is not shown. %s%s" % (_sentence(reason), (" " + basis["expect"]) if basis.get("expect") else ""))
        bits.append("The close is %s on %s." % (money(p["close"]), date_words(p["date"])))
    lst = basis.get("listing") or {}
    if lst.get("listing") == "ads" and lst.get("ratio") and float(lst["ratio"]) != 1 and m.get("market_cap") is not None:
        bits.append("The US listing is a depositary share, each standing for %s of the company's shares, as its %s states; "
                    "market value and P/E allow for that." % (("%d" % lst["ratio"]) if float(lst["ratio"]).is_integer() else "%g" % lst["ratio"],
                                                              lst.get("form") or "filing"))
    # compared with what: the P/E against large peers and the S&P 500's middle member
    calc_more = {}
    if m.get("pe_x") is not None:
        refs = []
        peer, ixm = peer_median(cur, row, "pe_x"), index_median(cur, "pe_x")
        if peer:
            refs.append("%s %s (median %s)" % (rel_word(m["pe_x"], peer[0]), peer[2], mult(peer[0])))
            calc_more["peer_median"] = PEER_CALC["peer_median"]
        if ixm:
            refs.append("%s the S&P 500's middle company (%s)" % (rel_word(m["pe_x"], ixm[0]), mult(ixm[0])))
            calc_more["index_median"] = PEER_CALC["index_median"]
        if refs:
            bits.append("At %s its latest annual earnings, it is priced %s." % (mult(m["pe_x"]), " and ".join(refs)))
        else:
            bits.append("That is %s the diluted earnings of the latest annual report (year to %s)." % (mult(m["pe_x"]), date_words(row["period_end"])))
    elif row["per_share"].get("eps_diluted") is not None and row["per_share"]["eps_diluted"] <= 0:
        bits.append("There is no P/E: the latest annual report shows a loss per share.")
    # and the past year against the S&P 500 over the same days
    if len(series) >= 2 and series[0][1]:
        ch = (series[-1][1] / series[0][1] - 1) * 100
        ix = index_change(cur, series[0][0], series[-1][0])
        bits.append("The shares are %s %s over the past year%s." % ("up" if ch >= 0 else "down", pct(abs(ch), 1),
                                                                     (", against %s for the S&P 500" % pct(ix, 1, sign=True)) if ix is not None else ""))
        bits += [w for w, status in _split_words(cur, rec, datetime.date.today() - datetime.timedelta(days=365))
                 if status != "confirmed"]
    chart = {"kind": "line", "title": "%s share price, one year" % rec["ticker"], "unit": "USD",
             "series": [{"name": rec["ticker"], "points": [[_d(d), c] for d, c in series]}]} if series else None
    figures = ([asked_fig] if asked_fig else []) + [fig("Market value", m.get("market_cap"), "USD", p.get("date"), sec_metrics.PRICE_FORMULAS["market_cap"]),
               fig("Close", p.get("close"), "USD", p.get("date")),
               fig("P/E", m.get("pe_x"), "×", p.get("date"), sec_metrics.PRICE_FORMULAS["pe_x"]),
               fig("Dividend yield", m.get("dividend_yield_pct"), "%", p.get("date"), sec_metrics.PRICE_FORMULAS["dividend_yield_pct"]),
               fig("Buyback yield", m.get("buyback_yield_pct"), "%", p.get("date"), sec_metrics.PRICE_FORMULAS["buyback_yield_pct"]),
               (fig("Shares outstanding", basis.get("shares"), "shares", basis.get("shares_date"))
                if basis and basis.get("method") != "balance sheet" and basis.get("shares") is not None
                else fig("Shares outstanding", row["per_share"].get("shares_outstanding"), "shares", row["period_end"])),
               fig("Diluted EPS", row["per_share"].get("eps_diluted"), "USD", row["period_end"])]
    calc = {k: sec_metrics.PRICE_FORMULAS[k] for k in ("market_cap", "pe_x", "dividend_yield_pct", "buyback_yield_pct")}
    calc.update(calc_more)
    if asked:
        calc[asked[0]] = asked[2]
    return pkg("company_value", q, headline, " ".join(bits), figures, None, chart,
               [PRICE_SOURCE, SEC_SOURCE], calc,
               {"company": rec}, {"company": company_link(rec), "filing": row["source_url"]},
               [INDEX_NOTE] if any("S&P 500" in b for b in bits) else [])


def _parts_of(lines):
    """{line name: [its parts]} for a product line the report also splits
    into smaller lines (Nvidia's Data Center = Compute + Networking): smaller
    lines that add up to it to within 0.1%. Four parts only under a generic
    line (Apple's "Product" = iPhone, Wearables, Mac, iPad), and never a
    generic line as a part: across a dozen lines a four-way sum matches by
    chance."""
    import itertools
    out, taken = {}, set()
    for big in sorted(lines, key=lambda s: -s["revenue_usd"]):
        if big["segment"] in taken:
            continue
        small = [s for s in lines if s["revenue_usd"] < big["revenue_usd"] and s["segment"] not in taken
                 and not _GENERIC_LINE.match(s["segment"])][:12]
        for n in ((2, 3, 4) if _GENERIC_LINE.match(big["segment"]) else (2, 3)):
            hit = next((c for c in itertools.combinations(small, n)
                        if abs(sum(s["revenue_usd"] for s in c) - big["revenue_usd"]) <= big["revenue_usd"] * 0.001), None)
            if hit:
                out[big["segment"]] = [s["segment"] for s in sorted(hit, key=lambda s: -s["revenue_usd"])]
                taken.update(out[big["segment"]])
                break
    return out


def _line_words(lines, parts):
    """'Data Center (89.7%, of which Compute 75.2% and Networking 14.5%), Gaming (7.4%)'."""
    inside = {p for ps in parts.values() for p in ps}
    by_name = {s["segment"]: s for s in lines}
    bits = []
    for s in lines:
        if s["segment"] in inside:
            continue
        sub = parts.get(s["segment"])
        extra = (", of which " + _join(["%s %s" % (p, pct(by_name[p]["share_of_revenue_pct"])) for p in sub])) if sub else ""
        bits.append("%s (%s%s)" % (_plain(s["segment"]), pct(s["share_of_revenue_pct"]), extra))
    return bits


_GENERIC_LINE = re.compile(r"(?i)^(products?|services?|goods|products? and services?|net sales|sales|revenues?|other)$")


def _plain(segment):
    """'United States (US)' -> 'United States': the code is for matching, not for a sentence."""
    return re.sub(r" \([A-Z]{2}\)$", "", segment)


def _join(bits):
    return bits[0] if len(bits) == 1 else ", ".join(bits[:-1]) + " and " + bits[-1]


def r_company_business(cur, q, e):
    """What a company does, told by where its revenue comes from: product
    lines, reporting segments and regions from its latest annual report."""
    from . import us_steps, us_tables
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    row = row_for(cur, rec["cik"])
    name = rec["name"]
    industry = (row or {}).get("industry")
    sector = us_steps.sector_for((row or {}).get("sic")) if row else None
    classed = ("The SEC classes it as %s%s." % (industry.lower().rstrip("."), (", in the %s sector" % sector) if sector else "")) if industry else ""
    ticker = us_tables._tickers(cur).get(rec["cik"]) or rec["ticker"]
    c = us_tables.sandbox(cur).cursor()
    cols = ["kind", "segment", "revenue_usd", "share_of_revenue_pct", "revenue_growth_pct", "operating_income_usd", "fiscal_year_end"]
    segs = [dict(zip(cols, r)) for r in c.execute(
        "SELECT %s FROM segments WHERE ticker = ? AND revenue_usd > 0 ORDER BY revenue_usd DESC" % ", ".join(cols), [ticker]).fetchall()]
    kinds = {k: [s for s in segs if s["kind"] == k] for k in ("product", "business", "geography")}
    links = {"company": company_link(rec), "filing": (row or {}).get("source_url")}
    srcs = [us_tables.sources()["segments"], SEC_SOURCE]
    calc = {"share_of_revenue_pct": "segment revenue ÷ the company's total revenue in the same annual report × 100",
            "revenue_growth_pct": "(segment revenue ÷ the same segment's revenue the year before, as given in the same report − 1) × 100"}
    revenue = ((row or {}).get("size") or {}).get("revenue") if row and row.get("unit") == "USD" else None
    words_q = _own_words(rec)
    if words_q:
        srcs = [_TEXT_SOURCE] + srcs
    if not segs:
        # no split read is not the same as no split filed: banks, insurers and
        # some large filers (Alphabet) tag segment revenue in ways not read here
        headline = ("The SEC classes %s as %s%s." % (name, industry.lower().rstrip("."), (", in the %s sector" % sector) if sector else "")
                    if industry else "%s's revenue split could not be read from its annual report." % name)
        bits = []
        if revenue is not None:
            bits.append("Revenue was %s in the year to %s." % (money(revenue), date_words(row["period_end"])))
        if words_q:
            headline = "What %s says it does, in its annual report." % name
            bits.append("Its revenue split by product line, segment or region could not be read from the report.")
        else:
            bits.append("Its revenue split by product line, segment or region could not be read from its annual report, "
                        "so what it sells is not shown here. The business section (Item 1) of that report, linked below, describes it.")
        out = pkg("company_business", q, headline, " ".join(b for b in bits if b), [], None, None, srcs, {},
                  {"company": rec}, links)
        if words_q:
            out["quotes"] = [words_q]
        return out
    # product lines tell it best when they add up to the whole once a line's
    # parts are set aside; overlapping lines (Microsoft's) give way to the
    # reporting segments
    parts = _parts_of(kinds["product"]) if kinds["product"] else {}
    inside = {p for ps in parts.values() for p in ps}
    product_total = sum(s["share_of_revenue_pct"] or 0 for s in kinds["product"] if s["segment"] not in inside)
    clean = bool(kinds["product"]) and 90 <= product_total <= 102
    main = kinds["product"] if clean or len(kinds["business"]) < 2 else kinds["business"]
    by_region = bool(re.search(r"\b(region|regions|geograph\w*|countr(y|ies)|where (does|do) .* (sell|make|earn)|by (market|area))\b", (q or "").lower()))
    generic_geo = re.compile(r"(?i)^(other countries|other|non.?u\.?s\.?|international|foreign|domestic|united states|u\.?s\.?a?|rest of (the )?world|all other|overseas)\b")
    region_words = re.compile(r"(?i)americas?|europe|emea|asia|pacific|apac|china|japan|india|korea|canada|mexico|latin|africa|middle east|"
                              r"international|north america|united states|\bu\.?s\.?\b|uk\b|united kingdom|germany|france|australia|brazil|rest of")
    # Apple's regions (Americas, Europe, Greater China ...) are its reporting segments; its "geography"
    # split is only the US against other countries, so the segments that read as regions are the regions
    regions = [s for s in kinds["business"] if region_words.search(s["segment"])]
    if len(regions) >= 3 and len(regions) >= len(kinds["business"]) - 1:
        kinds["geography"] = sorted(regions + [s for s in kinds["geography"] if not generic_geo.match(s["segment"]) and not any(
            r["segment"].lower() == s["segment"].lower() for r in regions)], key=lambda s: -(s["revenue_usd"] or 0))
    elif len(kinds["geography"]) >= 3 and any(not generic_geo.match(s["segment"]) for s in kinds["geography"]):
        kinds["geography"] = [s for s in kinds["geography"] if not generic_geo.match(s["segment"])]
    if (not main or by_region) and kinds["geography"]:
        main = kinds["geography"]                 # a filer that splits revenue by region only (Palantir), or a question about regions
    if not main:
        main = kinds["business"] or kinds["product"] or segs
    if main is not kinds["product"]:
        parts, inside = {}, set()
    shown = [s for s in main if s["segment"] not in inside] or main
    # a generic line split into named parts (Apple's "Product" = iPhone, Mac ...) is told by its parts
    by_name = {s["segment"]: s for s in main}
    leaves = sorted([by_name[p] for s in shown for p in (parts.get(s["segment"]) or [])
                     if _GENERIC_LINE.match(s["segment"])] +
                    [s for s in shown if not (_GENERIC_LINE.match(s["segment"]) and parts.get(s["segment"]))],
                    key=lambda s: -s["revenue_usd"]) or shown
    top, fy = leaves[0], date_words(segs[0]["fiscal_year_end"])
    if main is kinds["geography"]:
        headline = "%s of %s's revenue in the year to %s came from %s." % (pct(top["share_of_revenue_pct"]), name, fy, _plain(top["segment"]))
    elif _GENERIC_LINE.match(top["segment"]):
        # "makes most of its money from Product" says nothing: say it plainly
        what = {"product": "product sales", "products": "product sales", "goods": "product sales",
                "service": "services", "services": "services"}.get(top["segment"].lower(), top["segment"].lower())
        headline = "%s of %s's revenue in the year to %s came from %s." % (pct(top["share_of_revenue_pct"]), name, fy, what)
    elif top["share_of_revenue_pct"] is not None and top["share_of_revenue_pct"] >= 50:
        headline = "%s makes most of its money from %s: %s of revenue in the year to %s." % (name, _plain(top["segment"]), pct(top["share_of_revenue_pct"]), fy)
    else:
        headline = "%s's largest line of business is %s: %s of revenue in the year to %s." % (name, _plain(top["segment"]), pct(top["share_of_revenue_pct"]), fy)
    bits = [classed] if classed else []
    if revenue is not None:
        bits.append("Revenue was %s." % money(revenue))
    words = _line_words(main, parts)
    if len(words) > 1:
        bits.append("By %s: %s." % ("product line" if main is kinds["product"] else "region" if main is kinds["geography"] else "reporting segment", _join(words[:6])))
    if main is kinds["product"] and kinds["business"]:
        segw = ["%s (%s)" % (_plain(s["segment"]), pct(s["share_of_revenue_pct"])) for s in kinds["business"][:5]]
        n = len(kinds["business"])
        bits.append("It reports %s: %s." % ("one segment" if n == 1 else "%d segments" % n, _join(segw)) if n <= 5
                    else "It reports %d segments; the largest are %s." % (n, _join(segw)))
    # a growth of exactly nothing is a prior year tagged with this year's figure, not a real change
    grow = [s for s in leaves if s["revenue_growth_pct"] is not None and abs(s["revenue_growth_pct"]) >= 0.05
            and (s["share_of_revenue_pct"] or 0) >= 1]
    if len(grow) >= 2:
        g = max(grow, key=lambda s: s["revenue_growth_pct"])
        bits.append("The fastest-growing line was %s, %s on the year before." % (_plain(g["segment"]), pct(g["revenue_growth_pct"], 1, sign=True)))
    if kinds["geography"] and main is not kinds["geography"]:
        geo = [s for s in kinds["geography"] if not re.match(r"(?i)(all )?other|rest of", s["segment"])][:3]
        if geo:
            bits.append("By region, the largest %s %s." % ("is" if len(geo) == 1 else "are",
                                                           _join(["%s (%s)" % (_plain(s["segment"]), pct(s["share_of_revenue_pct"])) for s in geo])))
    order = {"product": 0, "business": 1, "geography": 2}
    kind_words = {"product": "Product line", "business": "Reporting segment", "geography": "Region"}
    table = {"columns": ["Split", "Line", "Revenue (USD)", "Share of revenue %", "Growth %"],
             "rows": [[kind_words[s["kind"]], ("  of which " + s["segment"]) if s["segment"] in inside else s["segment"],
                       s["revenue_usd"], s["share_of_revenue_pct"], s["revenue_growth_pct"]]
                      for s in sorted(segs, key=lambda s: order[s["kind"]])]}
    pts = [[s["segment"], s["share_of_revenue_pct"]] for s in leaves[:12] if s["share_of_revenue_pct"] is not None]
    chart = ({"kind": "bars", "title": "%s revenue by %s, year to %s" % (rec["ticker"], "product line" if main is kinds["product"] else "region" if main is kinds["geography"] else "segment", fy),
              "unit": "%", "series": [{"name": "Share of revenue", "points": pts}]} if len(pts) >= 2 else None)
    figures = [fig("Revenue", revenue, "USD", _d(segs[0]["fiscal_year_end"])),
               fig("%s's share" % _plain(top["segment"]), top["share_of_revenue_pct"], "%", _d(segs[0]["fiscal_year_end"]), calc["share_of_revenue_pct"])]
    notes = ["Lines of one kind can overlap or leave a remainder, so they need not add up to 100%."]
    if kinds["geography"]:
        notes.append("Regions are as each company defines them in its report (often where the customer is billed).")
    out = pkg("company_business", q, headline, " ".join(bits), figures, table, chart, srcs, calc, {"company": rec}, links, notes)
    if words_q:
        out["quotes"] = [words_q]
    return out


# ---- the annual report's own words (app/sec_text_api.py) ----------------------
_TEXT_SOURCE = {"name": "Annual report text", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (annual reports).",
                "note": "Quoted exactly as the company wrote it in its latest annual report."}
_SECTION_ITEM = {"10-K": {"business": "Item 1", "risk_factors": "Item 1A", "cybersecurity": "Item 1C", "properties": "Item 2",
                          "legal": "Item 3", "mdna": "Item 7", "market_risk": "Item 7A"},
                 "20-F": {"business": "Item 4", "risk_factors": "Item 3.D", "cybersecurity": "Item 16K", "mdna": "Item 5",
                          "market_risk": "Item 11"}}


def _cite(f, key):
    from . import sec_text_api
    form = "10-K" if f["form"] in ("10-K", "10-KT") else f["form"]
    item = _SECTION_ITEM.get(form, {}).get(key)
    return "%s, %s%s, %s for the year to %s" % (
        sec_text_api.SECTION_NAMES.get(key, key), (item + " of the ") if item else "", f["form"],
        "filed " + date_words(f["filed"]), date_words(f["period"]))


def _clip(text, n=900):
    """A passage cut at a sentence end near n characters, marked when cut."""
    if len(text) <= n:
        return text
    cut = max(text.rfind(". ", 0, n), text.rfind("; ", 0, n))
    return (text[:cut + 1] if cut > n // 2 else text[:n].rsplit(" ", 1)[0]) + " […]"


def _text_report(rec):
    """(cursor, latest annual report read) for a company, or (None, None). A
    filer under a new SEC number falls back to the reports read under its old one."""
    from . import sec_text_api
    c = sec_text_api.cur()
    if c is None or not rec:
        return None, None
    f = sec_text_api.latest(c, rec["cik"])
    if not f and rec["cik"] in us_link.PREDECESSOR_CIKS:
        f = sec_text_api.latest(c, us_link.PREDECESSOR_CIKS[rec["cik"]])
    return c, f


def _own_words(rec):
    """{"text", "cite", "url"}: the opening of the company's Business section."""
    from . import sec_text_api
    c, f = _text_report(rec)
    if not f:
        return None
    paras = sec_text_api.summary(c, f["adsh"])
    if not paras:
        return None
    return {"text": [_clip(p, 700) for p in paras], "cite": _cite(f, "business"), "url": f["url"]}


def r_company_risks(cur, q, e):
    """The risks a company lists in its latest annual report, in its order and words."""
    from . import sec_text_api
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    c, f = _text_report(rec)
    if not f:
        raise HTTPException(404, "%s's annual report has not been read yet, so its risk factors are not here." % rec["name"])
    topic = _topic_of(q, rec)
    if topic:
        return _says(q, rec, c, f, topic, ["risk_factors"])
    risks = sec_text_api.risk_list(c, f["adsh"])
    links = {"company": company_link(rec), "filing": f["url"]}
    year = date_words(f["period"])
    if not risks:
        note = f.get("note") or ""
        if "smaller reporting company" in note:
            headline = "%s lists no risk factors: as a smaller reporting company it is not required to." % rec["name"]
        elif "by reference" in note:
            headline = "%s's annual report refers to another filing for its risk factors." % rec["name"]
        else:
            headline = "%s's risk factors could not be split into single risks." % rec["name"]
        got = sec_text_api.section(c, f["adsh"], "risk_factors")
        out = pkg("company_risks", q, headline, "", [], None, None, [_TEXT_SOURCE], {}, {"company": rec}, links)
        if got:
            out["quotes"] = [{"text": [_clip(p, 700) for p in got[1][:2]], "cite": _cite(f, "risk_factors"), "url": f["url"]}]
        return out
    groups, order = {}, []
    for r in risks:
        g = r["category"] or "Risks"
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(r["headline"])
    headline = "%s lists %d risks in its annual report for the year to %s." % (rec["name"], len(risks), year)
    answer = ("They are in the company's own order and words, grouped under its own headings; the order is not a ranking."
              if len(order) > 1 else "They are in the company's own order and words; the order is not a ranking.")
    out = pkg("company_risks", q, headline, answer, [fig("Risks listed", len(risks), "count", _d(f["filed"]))], None, None,
              [_TEXT_SOURCE], {}, {"company": rec}, links)
    out["lists"] = {"title": "Risk Factors", "unit": "risks", "note": _cite(f, "risk_factors"), "url": f["url"],
                    "groups": [{"name": g if len(order) > 1 else None, "items": groups[g]} for g in order]}
    return out


# a question's subject inside a company's report: "what does Apple say about tariffs"
_SAYS = re.compile(r"(?i)\b(?:say|says|said|mention|mentions|discuss|discusses|disclose|discloses|write|writes|tell us|report)\b"
                   r"\s+(?:anything\s+)?(?:about|on|regarding|concerning|of)?\s*(.+)$")
_EXPOSED = re.compile(r"(?i)\b(?:exposure|exposed|exposes|risk|risks|threat|threats)\s+(?:to|from|of|about|around|related to|relating to)\s+(.+)$")
_RISKS_ON = re.compile(r"(?i)\brisk factors?\s+(?:on|about|around|regarding|concerning|over|tied to|linked to)\s+(.+)$")
_NOUNS = [(r"\bcompetitors?\b|\bcompetition\b|\bcompete\b|\brivals?\b", "competition", "compet"),
          (r"\bcustomers?\b|\bclients?\b", "customers", "customer"),
          (r"\bsuppliers?\b|\bsupply chain\b", "suppliers", "supplier"),
          (r"\bemployees?\b|\bheadcount\b|\bworkforce\b|\bstaff\b", "employees", "employees"),
          (r"\bpatents?\b|intellectual property", "intellectual property", "intellectual property"),
          (r"\bregulat\w*", "regulation", "regulat")]
_SECTION_WORDS = [(r"(?i)lawsuits?|litigation|legal (cases|proceedings|matters)|\bsued\b|\bcourts?\b|antitrust|investigations?", "legal"),
                  (r"(?i)cyber|hack|data breach|security incident", "cybersecurity"),
                  (r"(?i)\bproperties\b|offices|factories|plants|headquarter|head office|where .* (based|located|headquartered)|\bfacilities\b", "properties"),
                  (r"(?i)management|outlook|md&a|results of operations|how was (the|its) year", "mdna")]


_EXACT = {"competition": "competitors", "customers": "customers", "suppliers": "suppliers"}


def _topic_of(q, rec):
    """The subject a question asks about inside the report, or None."""
    text = re.sub(r"(?i)\b(in|from) (its|the|their) (latest |last |most recent )?(annual report|10-?k|20-?f|filing)s?\b", " ", q or "")
    for pat in (_SAYS, _EXPOSED, _RISKS_ON):
        m = pat.search(text)
        if m:
            t = m.group(1)
            names = [rec.get("name") or "", rec.get("ticker") or ""] + [re.sub(r"[^\w&-]", "", w) for w in (rec.get("name") or "").split()[:1]]
            for w in sorted(names, key=len, reverse=True):
                if len(w) > 2:
                    t = re.sub(r"(?i)\b%s('s)?\b" % re.escape(w), " ", t)
            t = re.sub(r"(?i)^\s*(?:(?:from|to|of|about|on|for|its|their|the|a|an)\s+)+", "", t.strip()).strip(" ?.!\"'“”")
            if 2 <= len(t) <= 60:
                return t
    for pat, label, stem in _NOUNS:
        if re.search(pat, q or "", re.I):
            return label
    # "Walmart on wages", "Nvidia about China": the subject after the company's name
    rest = _strip_companies(q or "", [rec]) if rec else (q or "").lower()
    m = re.match(r"^(?:what (?:does|do) (?:it |they )?say )?(?:on|about|regarding|concerning)\s+(.+)$", rest)
    if m and 3 <= len(m.group(1).strip(" ?.!")) <= 60:
        return m.group(1).strip(" ?.!")
    return None


def _says(q, rec, c, f, topic, sections=None):
    from . import sec_text_api
    # a named thing ("competitors") is looked for by its own word first, then by its family ("compet")
    stems = {label: stem for _, label, stem in _NOUNS}
    hits = []
    for word in ([_EXACT.get(topic)] if _EXACT.get(topic) else []) + [stems.get(topic, topic)]:
        hits = sec_text_api.mentions(c, f["adsh"], word, sections, limit=1000)
        if hits:
            break
    links = {"company": company_link(rec), "filing": f["url"]}
    where = " in its Risk Factors" if sections == ["risk_factors"] else ""
    if not hits:
        headline = "%s's latest annual report does not mention “%s”%s." % (rec["name"], topic, where)
        return pkg("company_says", q, headline, "Checked: %s." % _cite(f, (sections or ["business"])[0]).split(",")[0] if sections
                   else "Checked every section read from its %s for the year to %s: Business, Risk Factors, Legal "
                        "Proceedings, Management's Discussion and Market Risk." % (f["form"], date_words(f["period"])),
                   [], None, None, [_TEXT_SOURCE], {}, {"company": rec}, links)
    by = {}
    for k, _ in hits:
        by[k] = by.get(k, 0) + 1
    most = max(by, key=by.get)
    headline = "%s's annual report mentions “%s” in %d passage%s%s, most in %s." % (
        rec["name"], topic, len(hits), "" if len(hits) == 1 else "s", where, sec_text_api.SECTION_NAMES[most]) if len(by) > 1 else \
        "%s's annual report mentions “%s” in %d passage%s, in %s." % (
            rec["name"], topic, len(hits), "" if len(hits) == 1 else "s", sec_text_api.SECTION_NAMES[most])
    out = pkg("company_says", q, headline, "The passages that mention it most, quoted from the report:", [], None, None,
              [_TEXT_SOURCE], {}, {"company": rec}, links)
    out["quotes"] = [{"text": [_clip(p, 900)], "cite": _cite(f, k), "url": f["url"]} for k, p in hits[:5]]
    return out


def r_company_says(cur, q, e):
    """What a company's annual report says about a subject, or a section of it
    (legal cases, cybersecurity, properties) when no subject is named."""
    from . import sec_text_api
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    c, f = _text_report(rec)
    if not f:
        raise HTTPException(404, "%s's annual report has not been read yet." % rec["name"])
    key = next((k for pat, k in _SECTION_WORDS if re.search(pat, q or "")), None)
    topic = _topic_of(q, rec)
    if topic and not (key and re.search(r"(?i)^(lawsuits?|litigation|legal|cyber|properties|offices)", topic)):
        return _says(q, rec, c, f, topic)
    if key is None:
        raise HTTPException(422, "Say what to look for in %s's annual report, e.g. “what does %s say about tariffs”."
                            % (rec["name"], rec["ticker"]))
    got = sec_text_api.section(c, f["adsh"], key)
    links = {"company": company_link(rec), "filing": f["url"]}
    if not got:
        return pkg("company_says", q, "%s's latest annual report has no %s section that could be read." % (
            rec["name"], sec_text_api.SECTION_NAMES[key]), "", [], None, None, [_TEXT_SOURCE], {}, {"company": rec}, links)
    _, paras, heads = got
    body = [p for i, p in enumerate(paras) if i not in heads and len(p) >= 60][:3]
    refers = bool(body) and len(" ".join(body)) < 400 and re.search(r"(?i)\b(see|refer to|described in|incorporated)\b", body[0])
    headline = ("%s's %s section points elsewhere in the report." % (rec["name"], sec_text_api.SECTION_NAMES[key]) if refers
                else "What %s's annual report says under %s." % (rec["name"], sec_text_api.SECTION_NAMES[key]))
    out = pkg("company_says", q, headline, "", [], None, None, [_TEXT_SOURCE], {}, {"company": rec}, links)
    out["quotes"] = [{"text": [_clip(p, 900) for p in body], "cite": _cite(f, key), "url": f["url"]}] if body else []
    return out


def r_company_growth_margin(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    row = row_for(cur, rec["cik"])
    if row is None:
        raise HTTPException(404, "%s has no annual report loaded." % rec["name"])
    m, z, pr = row["metrics"], row["size"], row["prior"]
    ccy = row.get("unit") or "USD"
    money_ = (lambda v: money_in(v, ccy))
    fy = "the year to %s" % date_words(row["period_end"])
    if m.get("revenue_growth_pct") is None:
        headline = "%s: revenue %s in %s; no prior year tagged, so no growth rate." % (rec["name"], money_(z["revenue"]), fy)
    else:
        g = m["revenue_growth_pct"]
        headline = ("%s grew revenue %s in %s." % (rec["name"], pct(g, 1), fy) if g >= 0
                    else "%s's revenue fell %s in %s." % (rec["name"], pct(abs(g), 1), fy))
    ql = (q or "").lower()
    focus = "revenue_growth_pct"
    if re.search(r"profitable|lose money|losing money|in the red|make money|making money|loss.making|in profit|make a profit|make a loss|turn a profit", ql) and z.get("net_income") is not None:
        ni = z["net_income"]
        focus = "net_margin_pct"
        headline = "%s %s in %s: net income of %s%s." % (rec["name"], "made a profit" if ni > 0 else "made a loss" if ni < 0 else "broke even", fy,
                                                         money_(ni), (" (net margin %s)" % pct(m["net_margin_pct"])) if m.get("net_margin_pct") is not None else "")
    elif re.search(r"free cash flow margin|fcf margin|cash conversion", ql) and m.get("fcf_margin_pct") is not None:
        focus = "fcf_margin_pct"
        headline = "%s's free cash flow margin was %s in %s (free cash flow %s)." % (rec["name"], pct(m["fcf_margin_pct"]), fy, money_(m.get("fcf")))
    elif re.search(r"net margin", ql) and m.get("net_margin_pct") is not None:
        focus = "net_margin_pct"
        headline = "%s's net margin was %s in %s." % (rec["name"], pct(m["net_margin_pct"]), fy)
    elif re.search(r"margin|profitab", ql) and m.get("operating_margin_pct") is not None:
        focus = "operating_margin_pct"
        headline = "%s's operating margin was %s in %s%s." % (rec["name"], pct(m["operating_margin_pct"]), fy,
                                                               (" (net margin %s)" % pct(m["net_margin_pct"])) if m.get("net_margin_pct") is not None else "")
    elif re.search(r"return on equity|\broe\b", ql) and m.get("roe_pct") is not None:
        focus = "roe_pct"
        headline = "%s's return on equity was %s in %s." % (rec["name"], pct(m["roe_pct"]), fy)
    # compared with what: the figure asked about, then the other one a reader judges a business by
    labels = {"revenue_growth_pct": "Revenue growth", "operating_margin_pct": "Its operating margin", "net_margin_pct": "Its net margin",
              "roe_pct": "Its return on equity", "fcf_margin_pct": "Its free cash flow margin"}
    second = "operating_margin_pct" if focus == "revenue_growth_pct" else "revenue_growth_pct"
    if second == "operating_margin_pct" and m.get("operating_margin_pct") is None:
        second = "net_margin_pct"
    bits = [b for b in (vs_sentence(cur, row, focus, labels[focus]), vs_sentence(cur, row, second, labels[second])) if b]
    if not bits:
        bits.append("Revenue was %s against %s a year earlier, in the same filing." % (money_(z["revenue"]), money_(pr["revenue"])))
    if ccy != "USD":
        bits.append("Figures are in the company's reporting currency (%s), as filed." % ccy)
    table = {"columns": ["Figure", fy.replace("the year to ", "Year to "), "Prior year"],
             "rows": [["Revenue", z["revenue"], pr["revenue"]], ["Operating income", z["operating_income"], None],
                      ["Net income", z["net_income"], pr["net_income"]], ["Total assets", z["total_assets"], pr["total_assets"]],
                      ["Equity", z["equity"], pr["equity"]]], "unit": row["unit"]}
    figures = [fig("Revenue growth", m.get("revenue_growth_pct"), "%", row["period_end"], sec_metrics.FORMULAS["revenue_growth_pct"]),
               fig("Operating margin", m.get("operating_margin_pct"), "%", row["period_end"], sec_metrics.FORMULAS["operating_margin_pct"]),
               fig("Net margin", m.get("net_margin_pct"), "%", row["period_end"], sec_metrics.FORMULAS["net_margin_pct"]),
               fig("ROE", m.get("roe_pct"), "%", row["period_end"], sec_metrics.FORMULAS["roe_pct"])]
    return pkg("company_growth_margin", q, headline, " ".join(bits), figures, table, None, [SEC_SOURCE],
               dict({k: sec_metrics.FORMULAS[k] for k in ("revenue_growth_pct", "operating_margin_pct", "net_margin_pct", "roe_pct", "profit_growth_pct")}, **PEER_CALC),
               {"company": rec}, {"company": company_link(rec), "filing": row["source_url"]},
               ["Tags used: " + ", ".join("%s=%s" % (k, v) for k, v in row["tags"].items() if v and k in ("revenue", "operating_income", "net_income"))])


def r_company_shareholder_return(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    row = row_for(cur, rec["cik"])
    if row is None:
        raise HTTPException(404, "%s has no annual report loaded." % rec["name"])
    m, z = row["metrics"], row["size"]
    total = m.get("shareholder_return")
    fy = "the year to %s" % date_words(row["period_end"])
    if total is None:
        headline = "%s reports no dividends or buybacks in %s." % (rec["name"], fy)
    else:
        headline = "%s returned %s to shareholders in %s." % (rec["name"], money(total), fy)
    ql = (q or "").lower()
    if re.search(r"buyback yield|repurchase yield", ql) and m.get("buyback_yield_pct") is not None:
        headline = "%s's buyback yield is %s: %s of buybacks in %s against today's market value." % (rec["name"], pct(m["buyback_yield_pct"], 2), money(z["buybacks"]), fy)
    elif re.search(r"payout ratio|payout", ql) and m.get("payout_ratio_pct") is not None:
        headline = "%s's dividend payout ratio was %s in %s: dividends of %s against net income of %s." % (
            rec["name"], pct(m["payout_ratio_pct"]), fy, money(z["dividends_paid"]), money(z["net_income"]))
    elif re.search(r"dividend", ql) and not re.search(r"buyback", ql) and z.get("dividends_paid") is not None:
        headline = "%s paid %s in dividends in %s%s." % (rec["name"], money(z["dividends_paid"]), fy,
                                                         (", a yield of %s on today's value" % pct(m["dividend_yield_pct"], 2)) if m.get("dividend_yield_pct") is not None else "")
    elif re.search(r"buyback|repurchas", ql) and z.get("buybacks") is not None:
        headline = "%s bought back %s of its shares in %s%s." % (rec["name"], money(z["buybacks"]), fy,
                                                                 (", %s of today's market value" % pct(m["buyback_yield_pct"], 2)) if m.get("buyback_yield_pct") is not None else "")
    bits = ["Buybacks were %s and dividends %s." % (money(z["buybacks"]), money(z["dividends_paid"]))]
    if m.get("market_cap"):
        bits.append("Against today's market value of %s that is a buyback yield of %s and a dividend yield of %s, %s in all."
                    % (money(m["market_cap"]), pct(m.get("buyback_yield_pct"), 2), pct(m.get("dividend_yield_pct"), 2),
                       pct((m.get("buyback_yield_pct") or 0) + (m.get("dividend_yield_pct") or 0), 2)))
    if m.get("payout_ratio_pct") is not None or m.get("buyback_ratio_pct") is not None:
        bits.append("Relative to net income of %s: dividends %s, buybacks %s." % (money(z["net_income"]), pct(m.get("payout_ratio_pct")), pct(m.get("buyback_ratio_pct"))))
    figures = [fig("Buybacks", z["buybacks"], row["unit"], row["period_end"]), fig("Dividends paid", z["dividends_paid"], row["unit"], row["period_end"]),
               fig("Buyback yield", m.get("buyback_yield_pct"), "%", (row.get("price") or {}).get("date"), sec_metrics.PRICE_FORMULAS["buyback_yield_pct"]),
               fig("Dividend yield", m.get("dividend_yield_pct"), "%", (row.get("price") or {}).get("date"), sec_metrics.PRICE_FORMULAS["dividend_yield_pct"])]
    chart = {"kind": "bars", "title": "Cash returned to shareholders, %s" % fy, "unit": row["unit"],
             "series": [{"name": "Buybacks", "points": [["Buybacks", z["buybacks"]]]}, {"name": "Dividends", "points": [["Dividends", z["dividends_paid"]]]}]}
    return pkg("company_shareholder_return", q, headline, " ".join(bits), figures, None, chart, [SEC_SOURCE, PRICE_SOURCE],
               {"shareholder_return": sec_metrics.FORMULAS["shareholder_return"], "buyback_yield_pct": sec_metrics.PRICE_FORMULAS["buyback_yield_pct"],
                "dividend_yield_pct": sec_metrics.PRICE_FORMULAS["dividend_yield_pct"], "payout_ratio_pct": sec_metrics.FORMULAS["payout_ratio_pct"]},
               {"company": rec}, {"company": company_link(rec), "filing": row["source_url"]})


_HIGH_LOW = re.compile(r"52.?week (high|low)|52w (high|low)|one.year (high|low)|all.time (high|low)|record (high|low)|year(ly)? (high|low)|annual (high|low)|"
                       r"highest (ever|close|price|point|level)|lowest (close|price|ever|point|level)|\bpeak\b|\bath\b|(below|off|from|under) (its|the|their) (high|peak|record|top)|drawdown")
_PRICE_ASKED = re.compile(r"price today|current price|latest price|price now|share price$|stock price$|^price$|what is (the |its )?(share |stock )?price|how much (is|does) (a share|the stock|it) (cost|trade)|trading at|closed at|last close|latest close")


def r_company_price_performance(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    ql = (q or "").lower()
    hl = _HIGH_LOW.search(ql)
    window = us_link.parse_year_window(q)
    since, until = e.get("since"), e.get("until")
    if window and not since:
        since, until = window
    from_start = bool(re.search(r"since (its |the )?(ipo|listing|inception|beginning|start)|all.?time|ever listed|whole history|entire history|max(imum)? history|since forever", ql))
    if from_start and not window:
        since, until = datetime.date(1990, 1, 1), None
    if hl and re.search(r"this year|ytd|so far", ql) and not window:
        since = datetime.date(datetime.date.today().year, 1, 1)
    elif hl and re.search(r"all.time|record|ever|\bath\b|highest close|highest price|lowest close|lowest price|peak|highest point|lowest point", hl.group(0)) and not since:
        since = datetime.date(1990, 1, 1)                          # as far back as the prices on file go
    since = since or (datetime.date.today() - datetime.timedelta(days=365 * (e.get("years") or 1)))
    if until and until >= datetime.date.today():
        until = None
    on = us_link.parse_date(q) if re.search(r"\b(on|at|as of|last (?:monday|tuesday|wednesday|thursday|friday)|yesterday)\b", ql) else None
    if on:
        # the run from that day to today, so the close is adjusted for every split since (Apple's 4-for-1 in August 2020)
        rows, info = us_prices_api.clean_closes(cur, rec["ticker"], on - datetime.timedelta(days=12), None)
        upto = [r for r in rows if r[0] <= on]
        if not upto:
            p0 = us_prices_api.first_date(cur)
            raise HTTPException(404, "No %s price on file for %s: the daily prices on file begin %s." % (
                rec["name"], date_words(on), date_words(p0) if p0 else "later"))
        d0, c0, a0 = upto[-1]
        headline = "%s closed at %s on %s%s." % (rec["name"], money(c0, 2), date_words(d0),
                                                 " (the last trading day on or before the date asked)" if d0 != on else "")
        answer = ("Adjusted for the share split%s since, that close is %s, the basis of every price change shown here."
                  % ("s" if len(info.get("splits") or []) > 1 else "", money(a0, 2))) if abs(a0 - c0) > 0.005 else "No share split since then, so the close stands as traded."
        if rows and a0:
            dl, cl, al = rows[-1]
            answer += " It closed at %s on %s, %s since then, split-adjusted." % (money(cl, 2), date_words(dl), pct((al / a0 - 1) * 100, 1, sign=True))
        answer += " Closes are the consolidated end-of-day prices as the vendor delivered them."
        return pkg("company_price_performance", q, headline, answer,
                   [fig("Close", c0, "USD", d0), fig("Split-adjusted close", a0, "USD", d0, us_prices_api.SPLIT_RULE)], None, None, [PRICE_SOURCE],
                   {"adjusted_close": us_prices_api.SPLIT_RULE}, {"company": rec, "on": _d(d0)}, {"company": company_link(rec)})
    series, splits = closes_with_splits(cur, rec["ticker"], since, until)
    if not series:
        raise HTTPException(404, "No daily prices on file for %s since %s." % (rec["name"], date_words(since)))
    first, last = series[0], series[-1]
    late_start = (first[0] - since).days > 20 and since.year > 1990
    hi = max(series, key=lambda x: x[1])
    lo = min(series, key=lambda x: x[1])
    change = (last[1] / first[1] - 1) * 100 if first[1] else None
    word = "up" if (change or 0) >= 0 else "down"
    moved = "%s %s" % (word, pct(abs(change) if change is not None else None))
    if until:
        headline = "%s was %s in %s (%s to %s)." % (rec["name"], moved, until.year, date_words(first[0]), date_words(last[0])) \
            if since.month == 1 and since.day == 1 and until.month == 12 else \
            "%s was %s from %s to %s." % (rec["name"], moved, date_words(first[0]), date_words(last[0]))
    elif from_start:
        headline = "%s is %s since the earliest price on file, %s." % (rec["name"], moved, date_words(first[0]))
    elif _PRICE_ASKED.search(ql) or re.fullmatch(r"(?:[a-z0-9.&' -]+ )?(?:price|share price|stock price|shares|stock)", _strip_companies(q, [rec])):
        headline = "%s closed at %s on %s, %s over the past year." % (rec["name"], money(last[1], 2), date_words(last[0]), moved)
    else:
        headline = "%s is %s since %s." % (rec["name"], moved, date_words(first[0]))
    ix = index_change(cur, first[0], last[0]) if change is not None else None
    if ix is not None:
        gap = change - ix
        answer = "Over the same days the S&P 500 %s %s, so the shares did %s." % (
            "rose" if ix >= 0 else "fell", pct(abs(ix), 1),
            "about the same" if abs(gap) < 2 else "%s pp %s" % (fmt_pp(abs(gap)), "better" if gap > 0 else "worse") if abs(gap) < 100
            else "far %s" % ("better" if gap > 0 else "worse"))
    else:
        answer = "It closed at %s on %s against %s on %s." % (money(last[1]), date_words(last[0]), money(first[1]), date_words(first[0]))
    if hl:
        low_asked = bool(re.search(r"\b(low|lowest)\b", hl.group(0)))
        pt = lo if low_asked else hi
        span = ("on file (since %s)" % date_words(first[0])) if since.year <= 1990 else ("over the past year" if not window else "in %d" % since.year)
        gap = (last[1] / pt[1] - 1) * 100 if pt[1] else None
        if pt[0] == last[0]:
            headline = "%s's %s close %s is the latest one: %s on %s." % (rec["name"], "lowest" if low_asked else "highest", span, money(pt[1]), date_words(pt[0]))
        else:
            headline = "%s's %s close %s was %s on %s; it closed at %s on %s, %s %s." % (
                rec["name"], "lowest" if low_asked else "highest", span, money(pt[1]), date_words(pt[0]), money(last[1]), date_words(last[0]),
                pct(abs(gap)) if gap is not None else "\u2014", "above it" if (gap or 0) >= 0 else "below it")
        if since.year <= 1990:
            answer += " Closing prices only, split-adjusted; intraday highs and lows are not held."
    info = _LAST_PRICE_INFO.get("info") or {}
    if info.get("linked_from"):
        answer += " Before %s the shares traded as %s; the chart follows them across the ticker change." % (
            date_words(info["linked_from"][1]), info["linked_from"][0])
    # The asked-for start has no price: say so in the headline, never swap the period silently.
    if info.get("starts_at") and info["starts_at"] > since:
        start = info["starts_at"]
        before = cur.execute("SELECT date, close FROM us_px_daily WHERE symbol = ? AND close > 0 AND date < ? ORDER BY date DESC LIMIT 1",
                             [info["symbol"], start]).fetchone()
        if before and (start - before[0]).days > 20:
            headline = ("No %s price for %s: its shares did not trade on a US exchange from %s to %s. Since trading resumed they are %s."
                        % (rec["name"], date_words(since), date_words(before[0]), date_words(start), moved))
            answer += (" The last close before the gap was %s on %s. The price moved from %s to %s across the gap, too far for a share split,"
                       " which usually means the shares were replaced (for example in a restructuring), so the change is measured from %s."
                       % (money(before[1]), date_words(before[0]), money(before[1]), money(first[1]), date_words(start)))
        else:
            jump = (" from %s to %s" % (money(before[1]), money(first[1]))) if before else ""
            headline = ("No %s price for %s that compares with today: the price jumped%s on %s. Since then the shares are %s."
                        % (rec["name"], date_words(since), jump, date_words(start), moved))
            answer += (" The jump on %s matches no share-split ratio. It may be a different security taking over the ticker %s,"
                       " or a reverse split on a day the price also moved, so prices before then are left out."
                       % (date_words(start), info["symbol"]))
    elif late_start:
        headline = ("No %s price for %s: the daily prices on file begin %s. Since then the shares are %s."
                    % (rec["name"], date_words(since), date_words(first[0]), moved))
        answer += " The daily prices on file begin %s, so the chart and the change start there." % date_words(first[0])
    if splits:
        answer += " " + " ".join(sec_shares.split_sentence(cur, rec["cik"], d, r)[0] for d, r in splits)
    chart = {"kind": "line", "title": "%s share price since %s" % (rec["ticker"], date_words(first[0])), "unit": "USD",
             "series": [{"name": rec["ticker"], "points": [[_d(d), c] for d, c in series]}]}
    figures = [fig("Change", round(change, 2) if change is not None else None, "%", last[0], "(last close ÷ first close in the window − 1) × 100"),
               fig("Latest close", last[1], "USD", last[0]), fig("High", hi[1], "USD", hi[0]), fig("Low", lo[1], "USD", lo[0])]
    if hl:
        figures = figures[2:] + figures[:2]
    out = pkg("company_price_performance", q, headline, answer.strip(), figures, None, chart, [PRICE_SOURCE],
              {"change_pct": "(last close ÷ first close in the window − 1) × 100, split-adjusted", "adjusted_close": us_prices_api.SPLIT_RULE},
              {"company": rec, "since": _d(since), "until": _d(until) if until else None, "splits": [[_d(d), r] for d, r in splits]}, {"company": company_link(rec)},
              [INDEX_NOTE] if ix is not None else [])
    if until:
        out["period"] = period_span(first[0], last[0])
    return out


def r_company_calendar(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    ql = (q or "").lower()
    if len(e.get("companies") or []) >= 2 and re.search(r"before|after|earlier|later|first|same (day|week)", ql):
        # "does Microsoft report before Apple": both next dates, side by side
        parts = []
        for c in e["companies"][:3]:
            dd = call_api(sec_calendar_api.company, sec_code="cik:%d" % c["cik"])
            u = dd["upcoming"][0] if dd["upcoming"] else None
            parts.append((c, u))
        known = [(c, u) for c, u in parts if u]
        if len(known) >= 2:
            known.sort(key=lambda cu: cu[1]["expected_date"])
            a, b = known[0], known[1]
            headline = "%s is expected first, around %s; %s around %s." % (short_name(a[0]["name"]), date_words(a[1]["expected_date"]),
                                                                           short_name(b[0]["name"]), date_words(b[1]["expected_date"]))
            table = {"columns": ["Company", "Next report", "Expected", "Based on last year's filing"],
                     "rows": [[c["name"], u["report"], u["expected_date"], u["basis"]["filed"]] for c, u in known]}
            return pkg("company_calendar", q, headline, "Each date is last year's filing of the same report moved forward a year: an expected date, not an announced one, and not the earnings-release date.",
                       [fig("%s next expected" % short_name(c["name"]), u["expected_date"], "date", None) for c, u in known], table, None, [SEC_SOURCE], sec_calendar_api.CALC,
                       {"companies": e["companies"][:3]}, {"company": company_link(a[0]), "company_2": company_link(b[0])})
    d = call_api(sec_calendar_api.company, sec_code="cik:%d" % rec["cik"])
    up = d["upcoming"][0] if d["upcoming"] else None
    last = d["filings"][0]
    if re.search(r"when did .* last|last (?:file|filed|report|reported)|most recent (?:filing|10-k|10-q|report)|latest (?:filing|10-k|10-q)|previous (?:filing|report)", ql):
        want = "10-K" if re.search(r"10-?k|annual", ql) else ("10-Q" if re.search(r"10-?q|quarterly", ql) else None)
        f = next((x for x in d["filings"] if not want or x["form"].startswith(want)), last)
        headline = "%s last filed a %s on %s, for the period to %s." % (rec["name"], f["form"], date_words(f["filed"]), date_words(f["period"]))
        answer = ("Its next %s is expected around %s." % (up["report"], date_words(up["expected_date"]))) if up else ""
        table = {"columns": ["Form", "Period end", "Filed", "Days after period end"],
                 "rows": [[x["form"], x["period"], x["filed"], x["lag_days"]] for x in d["filings"][:8]]}
        return pkg("company_calendar", q, headline, answer, [fig("Last filed", f["filed"], "date", None), fig("Next expected", up["expected_date"] if up else None, "date", None)],
                   table, None, [SEC_SOURCE], sec_calendar_api.CALC, {"company": rec}, {"company": company_link(rec), "filing": f["source_url"]})
    if up:
        headline = "%s is expected to file its next %s around %s." % (rec["name"], up["report"], date_words(up["expected_date"]))
        answer = ("That rests on last year's filing of the same report on %s. The most recent filing was a %s for the period to %s, filed %s, %d days after the period end."
                  % (date_words(up["basis"]["filed"]), last["form"], date_words(last["period"]), date_words(last["filed"]), last["lag_days"]))
    else:
        headline = "%s last filed a %s on %s." % (rec["name"], last["form"], date_words(last["filed"]))
        answer = "No filing a year earlier is on file to base an expectation on."
    table = {"columns": ["Form", "Period end", "Filed", "Days after period end"],
             "rows": [[f["form"], f["period"], f["filed"], f["lag_days"]] for f in d["filings"][:8]]}
    return pkg("company_calendar", q, headline, answer,
               [fig("Next expected", up["expected_date"] if up else None, "date", None, sec_calendar_api.CALC["expected_date"]),
                fig("Last filed", last["filed"], "date", None)], table, None, [SEC_SOURCE], sec_calendar_api.CALC,
               {"company": rec}, {"company": company_link(rec), "filing": last["source_url"]},
               ["An expected date is not an announced date and not the earnings-release date; most companies release results by press release and 8-K before the 10-Q."])


def r_company_holders(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    cusips = us_link.cusips_for(cur, rec["cik"])
    if not cusips:
        raise HTTPException(404, "No 13F positions could be matched to %s by name." % rec["name"])
    d = call_api(sec_13f_api.issuer, cusip=cusips[0]["cusip"], limit=15)
    rows = d["rows"]
    top = rows[0]
    adds = sorted([r for r in rows if (r.get("shares_change") or 0) > 0], key=lambda r: -r["shares_change"])[:3]
    cuts = sorted([r for r in rows if (r.get("shares_change") or 0) < 0], key=lambda r: r["shares_change"])[:3]
    co = short_name(rec["name"])
    mgr = lambda r: short_name(r["manager_name"])
    headline = "%s is %s's largest reported holder, with %s of the %s shares fund managers report owning." % (mgr(top), co, pct(top["share_of_reported_pct"]), co)
    # compared with what: did the largest holders add or cut over the quarter
    had = [r for r in rows if r.get("shares") is not None and (r.get("prior_shares") is not None or r.get("new_position"))]
    up, down = sum(1 for r in had if (r.get("shares_change") or 0) > 0), sum(1 for r in had if (r.get("shares_change") or 0) < 0)
    # a count, not a summed change: a manager that starts filing for its affiliates "adds" shares it already held
    bits = ["Of the %d largest, %d added shares in the quarter to %s and %d cut." % (len(had), up, date_words(top["report_period"]), down)] if len(had) >= 5 else \
        ["%s managers reported holding %s of %s shares at %s." % (num(d["holders"]), money(d["total_reported_value"]), co, date_words(top["report_period"]))]
    ql = (q or "").lower()
    if adds and re.search(r"\b(added|adding|increased|raised|bought|buying|built|new)\b", ql) and not re.search(r"\b(sold|cut|trimmed|reduced)\b", ql):
        bits.insert(0, headline)
        headline = "Among %s's largest holders, %s added the most %s shares in the quarter to %s: %s." % (
            co, mgr(adds[0]), rec["ticker"], date_words(top["report_period"]),
            "; ".join("%s +%s" % (mgr(r), share_words(r["shares_change"])) for r in adds))
    elif cuts and re.search(r"\b(sold|selling|cut|trimmed|reduced|exited|dumped)\b", ql):
        bits.insert(0, headline)
        headline = "Among %s's largest holders, %s cut the most %s shares in the quarter to %s: %s." % (
            co, mgr(cuts[0]), rec["ticker"], date_words(top["report_period"]),
            "; ".join("%s %s" % (mgr(r), share_words(r["shares_change"])) for r in cuts))
    if adds:
        bits.append("The biggest buyers that quarter: " + _people(["%s (%s shares)" % (mgr(r), share_words(r["shares_change"])) for r in adds]) + ".")
    class_note = ["This counts the %s class only; %s has %d share classes in 13F data." % (us_link.display_name(cusips[0]["issuer_name"]), co, len(cusips))] if len(cusips) > 1 else []
    table = {"columns": ["Manager", "Shares", "Value", "Share of reported", "Change in shares"],
             "rows": [[us_link.display_name(r["manager_name"]), r["shares"], r["value"], r["share_of_reported_pct"], r["shares_change"]] for r in rows], "unit": d["window"]["value_unit"]}
    chart = {"kind": "bars", "title": "Largest 13F holders of %s, value" % rec["ticker"], "unit": "USD",
             "series": [{"name": "Value", "points": [[mgr(r)[:28], r["value"]] for r in rows[:10]]}]}
    return pkg("company_holders", q, headline, " ".join(bits),
               [fig("Holders reporting", d["holders"], "count", d["window"]["end"]), fig("Reported value", d["total_reported_value"], "USD", d["window"]["end"])],
               table, chart, [F13_SOURCE], {"share_of_reported_pct": sec_13f_api.PROVENANCE["note"]},
               {"company": rec, "cusip": cusips[0]["cusip"]}, {"company": company_link(rec)},
               ["13F covers managers over $100m and long positions only, reported up to 45 days after quarter end; a share of reported value is not a share of the company."] + class_note)


def insiders_held_from(cur):
    """The first filing date of the Form 4s on file: before it no trade is
    known, so a window starting earlier is cut back to it and says so."""
    def build(_c):
        v = call_api(sec_insiders_api.summary).get("earliest_filed")
        return datetime.date.fromisoformat(v[:10]) if v else None
    return _memo(cur, "insiders_held_from", build)


def poss(name):
    """'Meta Platforms' -> "Meta Platforms'", 'Intel' -> "Intel's"."""
    return name + ("'" if name.endswith("s") else "'s")


def _since_words(since):
    """'this year' for 1 January of the current year, else 'since 1 March 2026'."""
    return "this year" if since == datetime.date.today().replace(month=1, day=1) else "since %s" % date_words(since)


def _month_cols(trades, since, series):
    """A column chart spec of the value traded each month from `since` to
    today, one series per (name, code); a month with no trade is a true 0."""
    today = datetime.date.today()
    months, m = [], since.replace(day=1)
    while m <= today:
        months.append(m.isoformat()[:7])
        m = (m.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    label = (lambda k: datetime.date.fromisoformat(k + "-01").strftime("%b")) if months[0][:4] == months[-1][:4] else \
        (lambda k: datetime.date.fromisoformat(k + "-01").strftime("%b %Y"))
    out = []
    for name, code in series:
        by = dict.fromkeys(months, 0.0)
        for t in trades:
            if t["trans_code"] == code and t["trans_date"][:7] in by:
                by[t["trans_date"][:7]] += t["value"] or 0
        out.append({"name": name, "points": [round(by[k], 2) for k in months]})
    return {"categories": [label(k) for k in months], "series": out}


def _people(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def r_company_insiders(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    since = e.get("since") or datetime.date.today().replace(month=1, day=1)
    held_from = insiders_held_from(cur)
    cut = bool(held_from and since < held_from)
    if cut:
        since = held_from
    cut_note = ["The insider filings on file begin %s, so earlier trades are not counted." % date_words(held_from)] if cut else []
    months = max(1, int((datetime.date.today() - since).days / 30) + 1)
    ql = q.lower()
    # buying and selling are read from every open-market trade in the window,
    # so the headline, the tiles and the table always count the same trades
    d = call_api(sec_insiders_api.company, sec_code="cik:%d" % rec["cik"], months=months, code="", limit=2000)
    trades = [t for t in d["trades"] if t["trans_date"] and t["trans_date"] >= since.isoformat() and t["trans_code"] in ("P", "S")]
    co = short_name(rec["name"])
    # a person named in the question ("has Elon Musk sold Tesla shares"): only that filer's trades
    named = _person_words(q, rec)
    person = _person_in(q, rec, [t.get("owner_name") or "" for t in d["trades"]])
    if named and not person:
        d_all = call_api(sec_insiders_api.company, sec_code="cik:%d" % rec["cik"], months=36, code="", limit=2000)
        person = _person_in(q, rec, [t.get("owner_name") or "" for t in d_all["trades"]])
        if person:
            trades = [t for t in d_all["trades"] if t["trans_date"] and t["trans_date"] >= since.isoformat() and t["trans_code"] in ("P", "S")]
    who = "%s insiders" % co
    if person:
        # the question writes "Mark Zuckerberg", the filing "Zuckerberg Mark": every word, in any order
        trades = [t for t in trades if all(re.search(r"\b%s\b" % re.escape(w), t.get("owner_name") or "", re.I) for w in person.split())]
        who = us_link.person_name(trades[0]["owner_name"]) if trades else person
    buys = [t for t in trades if t["trans_code"] == "P"]
    sells = [t for t in trades if t["trans_code"] == "S"]
    bought, sold = sum(t["value"] or 0 for t in buys), sum(t["value"] or 0 for t in sells)
    plan_share = (sum(t["value"] or 0 for t in sells if t["plan_10b5_1"]) / sold * 100) if sold else None
    latest = max((t["trans_date"] for t in trades), default=None)
    when = _since_words(since)
    of_shares = "of %s shares" % co if person else "of shares"

    # one row per person, largest amount first
    by_owner = {}
    for t in trades:
        o = by_owner.setdefault(t["owner_name"], {"sold": 0.0, "bought": 0.0, "planned": 0.0, "trades": 0, "latest": "", "role": None})
        o["sold" if t["trans_code"] == "S" else "bought"] += t["value"] or 0
        o["planned"] += (t["value"] or 0) if t["trans_code"] == "S" and t["plan_10b5_1"] else 0
        o["trades"] += 1
        if t["trans_date"] >= o["latest"]:
            o["latest"], o["role"] = t["trans_date"], t.get("title") or t.get("relationship")
    ranked = sorted(by_owner.items(), key=lambda kv: -(kv[1]["sold"] + kv[1]["bought"]))
    sellers = [(us_link.person_name(k), v) for k, v in ranked if v["sold"]]
    buyers = [(us_link.person_name(k), v) for k, v in ranked if v["bought"]]
    buyers.sort(key=lambda kv: -kv[1]["bought"])

    if not trades:
        headline = ("%s has not bought or sold %s shares on the open market %s." % (who, co, when) if person
                    else "No %s insider has bought or sold shares on the open market %s." % (co, when))
    elif not buys:
        headline = "%s sold %s %s %s and bought none." % (who, money(sold), of_shares, when)
    elif not sells:
        headline = "%s bought %s %s %s and sold none." % (who, money(bought), of_shares, when)
    else:
        headline = "%s bought %s and sold %s %s %s." % (who, money(bought), money(sold), of_shares, when)
    # "which directors bought at Boeing": the buyers, named, when there are few
    if buyers and not person and len(buyers) <= 3 and re.search(r"\b(who|which|bought|buying|buy|purchas)", ql):
        headline = "%s %s %s shares %s." % ("; ".join("%s (%s) bought %s" % (n, v["role"], money(v["bought"])) if v["role"] else "%s bought %s" % (n, money(v["bought"]))
                                                     for n, v in buyers), "of" if len(buyers) == 1 else "worth of", co, when)
        headline = headline[0].upper() + headline[1:]
    if named and not person:
        headline = "No Form 4 by %s at %s is on file in the past three years. %s" % (named, co, headline)

    bits = []
    # compared with what: the same months a year earlier, and the company's market value
    side, amount = ("sold", sold) if sold >= bought else ("bought", bought)
    then = None
    if trades and held_from and held_from <= since - datetime.timedelta(days=365):
        a_year = datetime.timedelta(days=365)
        d_then = call_api(sec_insiders_api.company, sec_code="cik:%d" % rec["cik"], months=min(120, months + 13), code="", limit=2000)
        if len(d_then["trades"]) < 2000:          # a list cut at the limit would understate a year ago
            lo, hi = (since - a_year).isoformat(), (datetime.date.today() - a_year).isoformat()
            then = sum(t["value"] or 0 for t in d_then["trades"] if t["trans_code"] == ("S" if side == "sold" else "P")
                       and t["trans_date"] and lo <= t["trans_date"] <= hi
                       and (not person or all(re.search(r"\b%s\b" % re.escape(w), t.get("owner_name") or "", re.I) for w in person.split())))
    mcap = (row_for(cur, rec["cik"]) or {}).get("metrics", {}).get("market_cap")
    if trades and mcap and amount:
        share = amount / mcap * 100
        bits.append("That is %s of %s %s market value." % ("less than 0.01%" if share < 0.01 else pct(share, 2), poss(co), money(mcap)))
    if then is not None:
        year = (datetime.date.today() - datetime.timedelta(days=365)).year
        r = amount / then if then else None
        bits.append("Nothing was %s in the same months of %d." % (side, year) if not then else
                    "It is %s the %s %s in the same months of %d." % (
                        "%.1f times" % r if r >= 1.95 else "%s more than" % pct((r - 1) * 100, 0) if r > 1.1 else
                        "about the same as" if r >= 0.9 else "%s less than" % pct((1 - r) * 100, 0), money(then), side, year))
    if not bits and sellers and not person:
        top = sellers[:3]
        share = sum(v["sold"] for _, v in top) / sold * 100
        if len(sellers) == 1:
            bits.append("All of it was sold by %s." % top[0][0])
        elif len(sellers) <= 3:
            bits.append("The sellers: %s." % _people([n for n, _ in top]))
        else:
            bits.append("%s of the selling came from %s." % (pct(share, 0), _people([n for n, _ in top])))
    if plan_share is not None:
        if plan_share >= 99.5:
            bits.append("All of it was sold under 10b5-1 plans: instructions set up in advance that then sell automatically.")
        elif plan_share < 0.5:
            bits.append("None of the sales were under a 10b5-1 plan (instructions set up in advance), so each sale was decided at the time.")
        else:
            bits.append("%s of the value sold was under 10b5-1 plans: instructions set up in advance that then sell automatically." % pct(plan_share, 1))

    if not trades:
        # nothing in the window: when the last open-market trade was, and the latest few before it
        d_prev = call_api(sec_insiders_api.company, sec_code="cik:%d" % rec["cik"], months=36, code="", limit=2000)
        prev = [t for t in d_prev["trades"] if t["trans_code"] in ("P", "S") and t["trans_date"] and t["trans_date"] < since.isoformat()
                and (not person or all(re.search(r"\b%s\b" % re.escape(w), t.get("owner_name") or "", re.I) for w in person.split()))]
        if prev:
            t0 = prev[0]
            bits.append("The last one was a %s of %s by %s on %s." % ("purchase" if t0["trans_code"] == "P" else "sale", money(t0["value"]),
                                                                     us_link.person_name(t0["owner_name"]), date_words(t0["trans_date"])))
            table = {"columns": ["Date", "Insider", "Role", "Trade", "Value (USD)"],
                     "rows": [[t["trans_date"], us_link.person_name(t["owner_name"]), t.get("title") or t.get("relationship"),
                               "Purchase" if t["trans_code"] == "P" else "Sale", t["value"]] for t in prev[:10]]}
        else:
            bits.append("None is on file in the past three years either.")
            table = None
        out = pkg("company_insiders", q, headline, " ".join(bits), [fig("Bought", 0, "USD", None), fig("Sold", 0, "USD", None)], table, None,
                  [INSIDER_SOURCE], {"value": "shares × the price the filer reported"}, {"company": rec, "since": _d(since), "code": "all"},
                  {"company": company_link(rec)}, ["Only open-market purchases (Form 4 code P) and sales (code S) count. Share awards, option exercises and shares withheld for tax are left out.",
                                                   "A Form 4 is due within two business days of the trade, so the last few days may still be missing."] + cut_note,
                  period={"text": "Since %s" % date_words(since)})
        if rec.get("ticker"):
            out["more"] = {"label": "Every trade, filing by filing", "href": "/?t=%s&view=owners" % rec["ticker"]}
        return out

    has_b, has_s = bool(buys), bool(sells)
    cols = ["Insider"] + (["Bought (USD)"] if has_b else []) + (["Sold (USD)"] if has_s else []) + ["Role", "Trades", "Latest trade"] + \
        (["Sold under 10b5-1 plan %"] if has_s else [])
    rows = []
    for k, v in ranked:
        rows.append([us_link.person_name(k)] + ([v["bought"] or None] if has_b else []) + ([v["sold"] or None] if has_s else []) +
                    [v["role"], v["trades"], v["latest"]] + ([round(v["planned"] / v["sold"] * 100, 1) if v["sold"] else None] if has_s else []))
    table = {"columns": cols, "rows": rows} if rows else None

    chart = None
    if trades and months >= 3:
        spec = _month_cols(trades, since, [s for s in (("Bought", "P") if has_b else None, ("Sold", "S") if has_s else None) if s])
        chart = dict(spec, kind="cols", title="Open-market %s by %s, by month" % ("trades" if has_b and has_s else "sales" if has_s else "purchases", who), unit="USD")
    elif len(ranked) > 1:
        chart = {"kind": "bars", "title": "Open-market trades by %s, by insider" % co, "unit": "USD",
                 "series": [{"name": "Value", "points": [[us_link.person_name(k)[:28], v["sold"] + v["bought"]] for k, v in ranked[:10]]}]}

    figures = [fig("Bought", bought, "USD", None, "shares × reported price"), fig("Sold", sold, "USD", None, "shares × reported price")]
    if not person and trades:
        figures.append(fig("Insiders selling" if has_s or not has_b else "Insiders buying", len(sellers) if has_s or not has_b else len(buyers), "count", None))
    if plan_share is not None:
        figures.append(fig("Sold under 10b5-1 plans", round(plan_share, 1), "%", None, "value sold under a 10b5-1 plan ÷ value of all sales × 100"))
    out = pkg("company_insiders", q, headline, " ".join(bits), figures, table, chart, [INSIDER_SOURCE],
              {"value": "shares × the price the filer reported",
               "sold_under_plan": "value of the sales the filer marked as under a Rule 10b5-1 plan ÷ value of all sales × 100",
               "a_year_earlier": "the same trades counted over the same dates one year (365 days) earlier",
               "share_of_market_value": "value traded ÷ the company's market value today × 100"},
              {"company": rec, "since": _d(since), "code": "all"}, {"company": company_link(rec)},
              ["Only open-market purchases (Form 4 code P) and sales (code S) count. Share awards, option exercises and shares withheld for tax are left out.",
               "A Form 4 is due within two business days of the trade, so the last few days may still be missing."] + cut_note,
              period={"text": "%s to %s" % (date_words(since), date_words(latest)) if latest else "Since %s" % date_words(since)})
    if rec.get("ticker"):
        out["more"] = {"label": "Every trade, filing by filing", "href": "/?t=%s&view=owners" % rec["ticker"]}
    return out


_PERSON_STOP = {"Insider", "Insiders", "Has", "Have", "Did", "Does", "Is", "Are", "Who", "Which", "What", "When", "Shares", "Stock",
                "Form", "The", "Sold", "Bought", "Buy", "Sell", "Selling", "Buying", "This", "Last", "Year", "Month", "Ceo", "Cfo", "Chairman"}


def _person_words(question, rec):
    """The capitalised words of a person's name in the question, the company's own words set aside, or None."""
    text = re.sub(r"(\w)['\u2019]s\b", r"\1", question or "")
    for w in (rec.get("name") or "").split() + [rec.get("ticker") or ""] + [a for a, t in us_link.ALIASES.items() if t == (rec.get("ticker") or "")]:
        if len(w) > 1:
            text = re.sub(r"(?i)\b%s\b" % re.escape(w.strip(",.")), " ", text)
    text = re.sub(r"^\s*[A-Z][a-z]+\b", lambda m: m.group(0) if m.group(0) in ("Elon", "Tim", "Jensen", "Warren", "Mark", "Satya", "Jeff", "Jamie", "Sundar") else m.group(0).lower(), text)
    words = [w for w in re.findall(r"\b[A-Z][a-z]+\b", text) if w not in _PERSON_STOP and w.upper() not in us_link._ENGLISH]
    return " ".join(words[:3]) if words else None


def _person_in(question, rec, owner_names):
    """A Form 4 filer the question names ("Elon Musk", "Cook"), matched against
    the trades' owner names; the company's own words never count."""
    named = _person_words(question, rec)
    words = named.split() if named else []
    if not words:
        return None
    owners = {o for o in owner_names if o}
    for n in range(min(3, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            cand = words[i:i + n]
            hits = [o for o in owners if all(re.search(r"\b%s\b" % re.escape(c), o, re.I) for c in cand)]
            if hits:
                return cand[-1] if n == 1 else " ".join(cand)
    return None


def r_company_as_filed_history(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    d = call_api(sec_api.company_by_cik, cik="cik:%d" % rec["cik"], form="10-K", limit=5)
    panel = d["panel"]
    if not panel:
        raise HTTPException(404, "No annual reports loaded for %s." % rec["name"])
    rows = [[p["period"], p["values"].get("revenue"), p["tags"].get("revenue"), p["filed"], p["adsh"]] for p in panel]
    headline = "%s reported revenue of %s for the year to %s, in the 10-K filed %s." % (rec["name"], money(panel[0]["values"].get("revenue")), date_words(panel[0]["period"]), date_words(panel[0]["filed"]))
    answer = ("Each figure below is the one printed in that year's own annual report, under the tag named, with the filing's accession number. Later restatements are not applied; a company that restated shows the original here and the restated figure in the later filing's comparative column.")
    table = {"columns": ["Fiscal year end", "Revenue as filed", "Tag", "Filed", "Accession"], "rows": rows, "unit": d["panel"][0]["units"].get("revenue")}
    chart = {"kind": "bars", "title": "%s revenue as filed, by annual report" % rec["ticker"], "unit": "USD",
             "series": [{"name": "Revenue", "points": [[p["period"][:4], p["values"].get("revenue")] for p in reversed(panel)]}]}
    return pkg("company_as_filed_history", q, headline, answer, [], table, chart, [SEC_SOURCE], {}, {"company": rec},
               {"company": company_link(rec), "filing": d["latest_filing"]["source_url"]},
               ["Only the annual reports loaded on this server appear; the SEC's data sets run from 2009."])


# ---- recipes: managers -----------------------------------------------------
def _manager_changes(cur, manager_cik):
    rows = cur.execute("""
        WITH f AS (
            SELECT accession, batch, report_period, filed,
                   row_number() OVER (ORDER BY report_period DESC, filed DESC) AS rn
            FROM sec_13f_filings WHERE manager_cik = ? AND NOT amendment),
        cur AS (SELECT h.cusip, arg_max(h.issuer_name, h.value) AS name, sum(h.value) AS value, sum(h.shares) AS shares
                FROM sec_13f_holdings h JOIN f ON f.accession = h.accession AND f.rn = 1 WHERE h.put_call IS NULL GROUP BY h.cusip),
        prev AS (SELECT h.cusip, arg_max(h.issuer_name, h.value) AS name, sum(h.value) AS value, sum(h.shares) AS shares
                 FROM sec_13f_holdings h JOIN f ON f.accession = h.accession AND f.rn = 2 WHERE h.put_call IS NULL GROUP BY h.cusip)
        SELECT coalesce(c.cusip, p.cusip) AS cusip, coalesce(c.name, p.name) AS name, c.value, c.shares, p.value AS prev_value, p.shares AS prev_shares,
               (SELECT max(report_period) FROM f WHERE rn = 1) AS period, (SELECT max(report_period) FROM f WHERE rn = 2) AS prev_period
        FROM cur c FULL OUTER JOIN prev p USING (cusip)
        ORDER BY coalesce(c.value, 0) DESC""", [manager_cik]).fetchall()
    return rows


def r_manager_portfolio(cur, q, e):
    mgr = e.get("manager")
    if not mgr:
        raise HTTPException(404, "I could not tell which investment manager you mean.")
    cik, name = mgr
    rows = _manager_changes(cur, cik)
    if not rows:
        raise HTTPException(404, "No 13F filing by %s in the loaded windows." % name)
    period, prev_period = rows[0][6], rows[0][7]
    cur_rows = [r for r in rows if r[2] is not None]
    total = sum(r[2] or 0 for r in cur_rows)
    # "does Berkshire own Apple", "Vanguard's stake in Apple": that one position
    target = next((c for c in (e.get("companies") or []) if c["cik"] != cik), None)
    if target:
        cusips = {c["cusip"] for c in us_link.cusips_for(cur, target["cik"])}
        pos = [r for r in rows if r[0] in cusips]
        held = [r for r in pos if r[2] is not None]
        if held:
            r = max(held, key=lambda x: x[2] or 0)
            share = (r[2] / total * 100) if total else None
            change = ((r[3] or 0) - (r[5] or 0)) if r[5] is not None else None
            headline = "%s held %s %s shares worth %s at %s, %s of its reported portfolio%s." % (
                name.title(), num(r[3], 0), target["ticker"] or target["name"], money(r[2]), date_words(period), pct(share),
                ("; %s %s shares since the quarter before" % ("up" if change > 0 else "down", num(abs(change), 0))) if change else "")
            answer = "Its largest position overall is %s at %s of %s reported." % (cur_rows[0][1].title(), pct(cur_rows[0][2] / total * 100 if total else None), money(total))
        elif pos:
            headline = "%s no longer held %s at %s; it reported %s shares the quarter before." % (name.title(), target["name"], date_words(period), num(pos[0][5], 0))
            answer = ""
        else:
            headline = "%s reported no %s position in its 13F for the quarter to %s." % (name.title(), target["name"], date_words(period))
            answer = "Its largest position is %s at %s of %s reported." % (cur_rows[0][1].title(), pct(cur_rows[0][2] / total * 100 if total else None), money(total))
        table = {"columns": ["Issuer", "CUSIP", "Value", "Shares", "Share of portfolio", "Prior shares", "Change in shares"],
                 "rows": [[r[1], r[0], r[2], r[3], round(r[2] / total * 100, 2) if total and r[2] is not None else None, r[5],
                           ((r[3] or 0) - (r[5] or 0)) if (r[3] is not None or r[5] is not None) else None] for r in (pos + [x for x in rows if x not in pos])[:60]], "unit": "USD"}
        return pkg("manager_portfolio", q, headline, answer, [fig("Position value", held[0][2] if held else None, "USD", period),
                                                                fig("Shares held", held[0][3] if held else None, "shares", period)],
                   table, None, [F13_SOURCE], {"share_of_portfolio": "position value \u00f7 total reported value \u00d7 100"},
                   {"manager": {"cik": cik, "name": name}, "company": target}, {"company": company_link(target)},
                   ["Long positions only, as reported on Form 13F; values are as filed."])
    new = [r for r in cur_rows if r[4] is None and prev_period]
    dropped = [r for r in rows if r[2] is None and r[4] is not None]
    bigger = sorted([r for r in cur_rows if r[5] and r[3] and r[3] > r[5] * 1.001], key=lambda r: -((r[3] or 0) - (r[5] or 0)) * ((r[2] or 0) / (r[3] or 1)))
    smaller = sorted([r for r in cur_rows if r[5] and r[3] is not None and r[3] < r[5] * 0.999], key=lambda r: ((r[3] or 0) - (r[5] or 0)) * ((r[2] or 0) / (r[3] or 1)))
    top = cur_rows[0]
    headline = "%s reported %s across %d positions for the quarter to %s; the largest is %s at %s." % (
        name.title(), money(total), len(cur_rows), date_words(period), top[1].title(), pct(top[2] / total * 100 if total else None))
    bits = []
    ql = (q or "").lower()
    sells = bool(re.search(r"\b(sold|sell|sells|selling|trimmed|trim|cut|reduced|dropped|exited|dumped|disposed)\b", ql))
    buys = bool(re.search(r"\b(bought|buy|buys|buying|added|adds|adding|new positions?|increased|raised|built)\b", ql))
    if prev_period and sells and not buys:
        drop_names = ", ".join(r[1].title() for r in sorted(dropped, key=lambda r: -(r[4] or 0))[:5]) or "none"
        trim_names = ", ".join("%s (%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in smaller[:4]) or "none"
        headline = "%s sold out of %d position%s and trimmed %d in the quarter to %s. Sold out: %s. Trimmed most: %s." % (
            name.title(), len(dropped), "" if len(dropped) == 1 else "s", len(smaller), date_words(period), drop_names, trim_names)
        bits.append("Against the quarter to %s. The portfolio held %s across %d positions at the quarter end; the largest is %s at %s." % (
            date_words(prev_period), money(total), len(cur_rows), top[1].title(), pct(top[2] / total * 100 if total else None)))
    elif prev_period and buys and not sells:
        new_names = ", ".join(r[1].title() for r in sorted(new, key=lambda r: -(r[2] or 0))[:5]) or "none"
        add_names = ", ".join("%s (+%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in bigger[:4]) or "none"
        headline = "%s opened %d new position%s and added to %d in the quarter to %s. New: %s. Added most: %s." % (
            name.title(), len(new), "" if len(new) == 1 else "s", len(bigger), date_words(period), new_names, add_names)
        bits.append("Against the quarter to %s. The portfolio held %s across %d positions at the quarter end; the largest is %s at %s." % (
            date_words(prev_period), money(total), len(cur_rows), top[1].title(), pct(top[2] / total * 100 if total else None)))
    if prev_period:
        bits.append("Against the quarter to %s: %d new positions (%s), %d dropped (%s)." % (
            date_words(prev_period), len(new), ", ".join(r[1].title() for r in sorted(new, key=lambda r: -(r[2] or 0))[:4]) or "none",
            len(dropped), ", ".join(r[1].title() for r in sorted(dropped, key=lambda r: -(r[4] or 0))[:4]) or "none"))
        if bigger:
            bits.append("Added to: " + ", ".join("%s (+%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in bigger[:3]) + ".")
        if smaller:
            bits.append("Trimmed: " + ", ".join("%s (%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in smaller[:3]) + ".")
    else:
        bits.append("Only one quarter is loaded for this manager, so there is no quarter-on-quarter change yet.")
    table = {"columns": ["Issuer", "CUSIP", "Value", "Shares", "Share of portfolio", "Prior shares", "Change in shares"],
             "rows": [[r[1], r[0], r[2], r[3], round(r[2] / total * 100, 2) if total and r[2] is not None else None, r[5],
                       ((r[3] or 0) - (r[5] or 0)) if (r[3] is not None or r[5] is not None) else None] for r in rows[:60]], "unit": "USD"}
    chart = {"kind": "bars", "title": "%s: largest positions" % name.title(), "unit": "USD",
             "series": [{"name": "Value", "points": [[r[1].title()[:26], r[2]] for r in cur_rows[:12]]}]}
    return pkg("manager_portfolio", q, headline, " ".join(bits), [fig("Reported value", total, "USD", period), fig("Positions", len(cur_rows), "count", period)],
               table, chart, [F13_SOURCE], {"share_of_portfolio": "position value ÷ total reported value × 100"},
               {"manager": {"cik": cik, "name": name}}, {},
               ["Long positions only, as reported on Form 13F; values are as filed."])


_PAY_COLS = ["ticker", "ceo", "fiscal_year_end", "ceo_total_pay_usd", "ceo_actually_paid_usd", "other_execs_avg_pay_usd",
             "tsr_value_of_100", "peer_tsr_value_of_100", "tsr_since", "proxy_filed"]


def _pay_rows(cur):
    from . import us_tables
    def build(c):
        try:
            rows = us_tables.sandbox(c).cursor().execute("SELECT %s FROM pay" % ", ".join(_PAY_COLS)).fetchall()
        except Exception:                                         # noqa: BLE001 — no pay table on this server
            rows = []
        return [dict(zip(_PAY_COLS, r)) for r in rows]
    return _memo(cur, "pay_rows", build)


_NICKNAMES = {"jensen": "jen-hsun", "jamie": "james", "tim": "timothy", "andy": "andrew", "mike": "michael", "bill": "william",
              "bob": "robert", "dave": "david", "jim": "james", "steve": "stephen", "chris": "christopher", "ed": "edward",
              "tom": "thomas", "larry": "lawrence", "ken": "kenneth", "jeff": "jeffrey", "greg": "gregory", "pat": "patrick"}


def person_named(q):
    """The capitalised words that look like a person's name in the question, or None."""
    words = [w for w in re.findall(r"\b[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b", q or "") if w not in _PERSON_STOP and w.upper() not in us_link._ENGLISH]
    return words or None


def _ceo_named(q, rows):
    """The pay row whose CEO's name is written in the question, or None. Two
    name words must match, or one surname that only one CEO carries."""
    words = person_named(q)
    if not words:
        return None
    asked = {w.lower() for w in words} | {_NICKNAMES[w.lower()] for w in words if w.lower() in _NICKNAMES}
    best, by_surname = None, {}
    for r in rows:
        for n in [x.strip() for x in (r.get("ceo") or "").split("/")]:
            parts = [p.lower().strip(".,") for p in n.split() if len(p) > 1]
            hit = [p for p in parts if p in asked]
            if len(hit) >= 2 and (best is None or len(hit) > best[0]):
                best = (len(hit), r, n)
            if parts and parts[-1] in asked and len(parts[-1]) >= 4:
                by_surname.setdefault(parts[-1], []).append((r, n))
    if best:
        return best[1], best[2]
    for s, hits in by_surname.items():
        if len(hits) == 1 and len(words) <= 3:
            return hits[0]
    return None


_SEGMENT_Q = re.compile(r"\b(services?|aws|cloud|data cent(?:er|re)|iphone|ipad|mac|wearables|energy|automotive|advertising|ads|youtube|search|gaming|"
                        r"azure|office|windows|xbox|linkedin|instagram|whatsapp|prime|streaming|subscription|hardware|software|licensing|segment)\b.*\b(revenue|sales)\b|"
                        r"\b(revenue|sales)\b.*\b(from|of|in)\b.*\b(services?|aws|cloud|data cent(?:er|re)|iphone|china|europe|segment)\b", re.I)
_SEG_STOP = {"revenue", "revenues", "sales", "from", "of", "in", "the", "its", "how", "much", "what", "is", "was", "does", "do", "make",
             "earn", "get", "total", "share", "as", "a", "percent", "percentage", "last", "year", "latest", "annual", "fiscal", "and", "by",
             "loss", "losses", "operating", "income", "profit", "margin", "parent", "company", "segment", "division", "unit", "business",
             "give", "me", "show", "please", "tell", "whats", "latest", "its", "it", "their", "for", "to"}
_SEG_ALIASES = {"aws": ["amazon web services", "aws"], "datacenter": ["data center"], "iphones": ["iphone"], "ads": ["advertising", "ads"],
                "cloud": ["cloud"], "services": ["service"], "energy": ["energy"], "autos": ["automotive"], "cars": ["automotive"],
                "azure": ["cloud"], "google": ["google"], "youtube": ["youtube"], "instagram": ["family of apps"], "whatsapp": ["family of apps"],
                "facebook": ["family of apps"], "reality": ["reality labs"], "metaverse": ["reality labs"], "north": ["north america", "united states", "americas"],
                "america": ["north america", "americas", "united states"], "international": ["international", "non-us", "other countries", "rest of"],
                "us": ["united states", "us canada"], "usa": ["united states"], "domestic": ["united states"], "china": ["china"], "europe": ["europe", "emea"],
                "asia": ["asia", "apac"], "japan": ["japan"], "india": ["india"], "subscription": ["subscription"], "hardware": ["hardware"],
                "software": ["software"], "licensing": ["licens"], "windows": ["windows", "more personal computing"], "office": ["productivity"],
                "xbox": ["gaming", "more personal computing"], "linkedin": ["linkedin", "productivity"], "automotive": ["automotive"]}


_EMPLOYEES = re.compile(r"(?i)(?:approximately|about|roughly|over|more than|nearly|had|employed|employs|employing|of)?\s*"
                        r"(\d{1,3}(?:,\d{3})+|\d{4,7}|\d+(?:\.\d+)?\s+million)\s+(?:(?:full[- ]time|part[- ]time|regular|total|equivalent|and|or|temporary|permanent|"
                        r"salaried|hourly|global|worldwide|active)\s+){0,5}(?:employees|people|team members|associates|persons|workers|individuals|employee\b|teammates|crew members)")
# "our global headcount was approximately 125,665" (Tesla): the count follows the word, with no noun after it
_HEADCOUNT_WAS = re.compile(r"(?i)\b(?:headcount|workforce|number of employees|employee count)(?:\s+(?:worldwide|globally|total|in total|overall))?\s+(?:was|of|is|totaled|totalled|stood at|reached|at)\s+"
                            r"(?:approximately|about|roughly|over|more than|nearly)?\s*(\d{1,3}(?:,\d{3})+|\d{4,7}|\d+(?:\.\d+)?\s+million)")
# the sentence that states the headcount, as filers phrase it
_HEADCOUNT_CUE = re.compile(r"(?i)\b(?:we (?:had|employed|employ)|the company (?:had|employed|employs)|(?:had|employed) (?:approximately|about|roughly)?\s*[\d.,]+|headcount|"
                            r"total (?:number of )?employees|employees worldwide|employees globally|full[- ]time employees|people worldwide)")


def r_company_employees(cur, q, e):
    """How many people a company employs, read from the text of its latest annual report."""
    from . import sec_text_api
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    c, f = _text_report(rec)
    if not f:
        raise HTTPException(404, "%s's annual report has not been read yet, so its employee count is not here." % rec["name"])
    hits = sec_text_api.mentions(c, f["adsh"], "employees", ["business"], limit=400) or sec_text_api.mentions(c, f["adsh"], "employees", None, limit=400)
    hits += [h for h in sec_text_api.mentions(c, f["adsh"], "associates", ["business"], limit=100) if h not in hits]
    best = None
    for key, p in hits:
        for m in list(_EMPLOYEES.finditer(p)) + list(_HEADCOUNT_WAS.finditer(p)):
            raw = m.group(1).lower()
            n = int(round(float(raw.replace("million", "").strip()) * 1_000_000)) if "million" in raw else int(raw.replace(",", ""))
            # the sentence that states the headcount wins; among those, the largest count (full-time plus part-time)
            cue = bool(_HEADCOUNT_CUE.search(p[max(0, m.start() - 120):m.end() + 40]))
            score = (cue, n)
            if 10 <= n <= 5_000_000 and (best is None or score > best[0]):
                best = (score, key, p, m.group(0), n)
    if best is None:
        raise HTTPException(404, "%s's annual report mentions employees but gives no headcount the box could read." % rec["name"])
    _score, key, p, phrase, n = best
    headline = "%s had about %s employees, as its annual report for the year to %s puts it." % (rec["name"], num(n, 0), date_words(f["period"]))
    out = pkg("company_employees", q, headline, "The sentence it comes from is quoted below; headcounts are given once a year, as of the fiscal year end.",
              [fig("Employees", n, "count", _d(f["period"]))], None, None, [_TEXT_SOURCE],
              {"employees": "the number written in the annual report's text; not a tagged figure"}, {"company": rec}, {"company": company_link(rec), "filing": f["url"]})
    out["quotes"] = [{"text": [_clip(p, 700)], "cite": _cite(f, key), "url": f["url"]}]
    return out


def r_company_segment(cur, q, e):
    """One line of a company's revenue split (a product line, segment or region) from its latest annual report."""
    from . import us_tables
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    ticker = us_tables._tickers(cur).get(rec["cik"]) or rec["ticker"]
    c = us_tables.sandbox(cur).cursor()
    cols = ["kind", "segment", "revenue_usd", "share_of_revenue_pct", "revenue_growth_pct", "operating_income_usd", "fiscal_year_end"]
    segs = [dict(zip(cols, r)) for r in c.execute(
        "SELECT %s FROM segments WHERE ticker = ? AND (revenue_usd > 0 OR operating_income_usd IS NOT NULL) "
        "ORDER BY coalesce(revenue_usd, 0) DESC, coalesce(operating_income_usd, 0) DESC" % ", ".join(cols), [ticker]).fetchall()]
    if not segs:
        raise HTTPException(404, "%s's revenue split by segment, product line or region could not be read from its annual report." % rec["name"])
    rest = _strip_companies(q, [rec])
    words = [w for w in re.findall(r"[a-z0-9&]+", rest) if w not in _SEG_STOP]
    stems = []
    for w in words:
        stems += _SEG_ALIASES.get(w, [w[:-1] if w.endswith("s") and len(w) > 4 else w])
    if not stems:
        raise HTTPException(422, "Which line of %s's revenue? Its split: %s." % (rec["name"], ", ".join(s["segment"] for s in segs[:8])))
    def score(s):
        name = s["segment"].lower()
        return sum(1 for st in stems if st in name)
    best = max(segs, key=lambda s: (score(s), s["revenue_usd"] or 0, s.get("operating_income_usd") or 0))
    if score(best) == 0:
        raise HTTPException(404, "%s's annual report has no revenue line called \u201c%s\u201d. Its split: %s." % (
            rec["name"], " ".join(words), ", ".join("%s (%s)" % (s["segment"], pct(s["share_of_revenue_pct"]) if s.get("share_of_revenue_pct") is not None else "operating income only") for s in segs[:8])))
    fy = date_words(best["fiscal_year_end"])
    share = best.get("share_of_revenue_pct")
    if best.get("revenue_usd") is None:
        # Alphabet: the data set carries operating income by segment but no revenue line
        oi = best.get("operating_income_usd")
        headline = "%s's %s %s of %s in the year to %s; its revenue by segment is not tagged in the data set." % (
            rec["name"], _plain(best["segment"]), "made an operating loss" if (oi or 0) < 0 else "made an operating profit", money(abs(oi)), fy)
        table = {"columns": ["Split", "Line", "Revenue (USD)", "Share of revenue %", "Operating income (USD)"],
                 "rows": [[{"product": "Product line", "business": "Reporting segment", "geography": "Region"}.get(s["kind"], "Line"), s["segment"],
                           s["revenue_usd"], s["share_of_revenue_pct"], s["operating_income_usd"]] for s in segs]}
        row = row_for(cur, rec["cik"])
        return pkg("company_segment", q, headline, "Operating income by reporting segment, as filed; the segment revenue lines are not in the SEC's statement data sets for this filer.",
                   [fig("%s operating income" % _plain(best["segment"]), oi, "USD", _d(best["fiscal_year_end"]))], table, None,
                   [us_tables.sources()["segments"], SEC_SOURCE], {}, {"company": rec, "segment": best["segment"]},
                   {"company": company_link(rec), "filing": (row or {}).get("source_url")})
    headline = "%s's %s revenue was %s in the year to %s%s." % (rec["name"], _plain(best["segment"]), money(best["revenue_usd"]), fy,
                                                              (", %s of its revenue" % pct(share)) if share is not None else "")
    if re.search(r"\bloss\b|losses|operating (?:income|profit|loss)|profit\b|profitab|margin", q.lower()):
        oi = best.get("operating_income_usd")
        if oi is None:
            headline = "%s's annual report gives no operating income for %s; its revenue was %s in the year to %s." % (rec["name"], _plain(best["segment"]), money(best["revenue_usd"]), fy)
        else:
            headline = "%s's %s %s of %s in the year to %s, on revenue of %s%s." % (
                rec["name"], _plain(best["segment"]), "made an operating loss" if oi < 0 else "made an operating profit", money(abs(oi)), fy, money(best["revenue_usd"]),
                (" (margin %s)" % pct(oi / best["revenue_usd"] * 100)) if best["revenue_usd"] else "")
    if re.search(r"share|percent|proportion|fraction|how much of", q.lower()) and share is not None:
        headline = "%s made %s of its revenue from %s in the year to %s: %s." % (rec["name"], pct(share), _plain(best["segment"]), fy, money(best["revenue_usd"]))
    bits = []
    if best.get("revenue_growth_pct") is not None and abs(best["revenue_growth_pct"]) >= 0.05:
        bits.append("That is %s on the year before, as the same report gives it." % pct(best["revenue_growth_pct"], 1, sign=True))
    if best.get("operating_income_usd") is not None:
        bits.append("Its operating income was %s." % money(best["operating_income_usd"]))
    kind_words = {"product": "product line", "business": "reporting segment", "geography": "region"}
    bits.append("Read as the %s \u201c%s\u201d; the whole split is in the table." % (kind_words.get(best["kind"], "line"), best["segment"]))
    if re.search(r"history|over time|each year|every year|since \d{4}", q.lower()):
        bits.append("Only the latest annual report's split is held here, so there is no year-by-year chart of this line.")
    order = {"product": 0, "business": 1, "geography": 2}
    table = {"columns": ["Split", "Line", "Revenue (USD)", "Share of revenue %", "Growth %"],
             "rows": [[kind_words[s["kind"]].capitalize(), s["segment"], s["revenue_usd"], s["share_of_revenue_pct"], s["revenue_growth_pct"]]
                      for s in sorted(segs, key=lambda s: order.get(s["kind"], 3))]}
    calc = {"share_of_revenue_pct": "segment revenue \u00f7 the company's total revenue in the same annual report \u00d7 100",
            "revenue_growth_pct": "(segment revenue \u00f7 the same segment's revenue the year before, as given in the same report \u2212 1) \u00d7 100"}
    row = row_for(cur, rec["cik"])
    return pkg("company_segment", q, headline, " ".join(bits), [fig("%s revenue" % _plain(best["segment"]), best["revenue_usd"], "USD", _d(best["fiscal_year_end"])),
                                                              fig("Share of revenue", share, "%", _d(best["fiscal_year_end"]), calc["share_of_revenue_pct"])],
               table, None, [us_tables.sources()["segments"], SEC_SOURCE], calc, {"company": rec, "segment": best["segment"]},
               {"company": company_link(rec), "filing": (row or {}).get("source_url")},
               ["Lines of one kind can overlap or leave a remainder, so they need not add up to 100%."])


_Q_FIG_COLS = [(r"net income|net profit|profit|earnings\b(?! per)|bottom line", "net_income_usd", "net income"),
               (r"operating (?:income|profit)", "operating_income_usd", "operating income"),
               (r"\beps\b|earnings per share", "eps_usd", "diluted earnings per share"),
               (r"revenue|sales|turnover|top line", "revenue_usd", "revenue")]


def r_company_quarters(cur, q, e):
    """A company's quarterly results from the last eight quarters on file: the
    latest quarter, a named quarter, or every quarter."""
    from . import us_tables
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    ticker = us_tables._tickers(cur).get(rec["cik"]) or rec["ticker"]
    c = us_tables.sandbox(cur).cursor()
    cols = ["quarter_end", "fiscal_year", "fiscal_quarter", "revenue_usd", "operating_income_usd", "net_income_usd", "eps_usd",
            "revenue_year_ago_usd", "revenue_growth_yoy_pct", "net_income_year_ago_usd", "is_latest", "q4_derived", "filed"]
    rows = [dict(zip(cols, r)) for r in c.execute("SELECT %s FROM quarters WHERE ticker = ? ORDER BY quarter_end" % ", ".join(cols), [ticker]).fetchall()]
    if not rows:
        raise HTTPException(404, "No quarterly figures are on file for %s." % rec["name"])
    ql = (q or "").lower()
    col, label = next(((cc, lab) for pat, cc, lab in _Q_FIG_COLS if re.search(pat, ql)), ("revenue_usd", "revenue"))
    m = re.search(r"\bq([1-4])\s*(?:fy)?\s*'?((?:19|20)?\d{2})\b|\b(?:fy)?((?:19|20)\d{2})\s*q([1-4])\b|(first|second|third|fourth) quarter(?: of)? (?:fy)?((?:19|20)\d{2})", ql)
    pick = None
    if m:
        qn = m.group(1) or m.group(4) or {"first": "1", "second": "2", "third": "3", "fourth": "4"}[m.group(5)]
        yr = m.group(2) or m.group(3) or m.group(6)
        yr = int(yr) + (2000 if int(yr) < 100 else 0)
        pick = next((r for r in rows if r["fiscal_quarter"] == "Q%s" % qn and r["fiscal_year"] == yr), None)
        if pick is None:
            raise HTTPException(404, "No figures for %s's fiscal Q%s %d are on file; the quarters held run %s to %s." % (
                rec["name"], qn, yr, "%s %d" % (rows[0]["fiscal_quarter"], rows[0]["fiscal_year"]), "%s %d" % (rows[-1]["fiscal_quarter"], rows[-1]["fiscal_year"])))
    last = pick or rows[-1]
    v = last.get(col)
    fmt = (lambda x: "$%s" % num(x, 2)) if col == "eps_usd" else money
    yoy = ""
    if col == "revenue_usd" and last.get("revenue_growth_yoy_pct") is not None:
        yoy = ", %s on a year earlier" % pct(last["revenue_growth_yoy_pct"], 1, sign=True)
    elif col == "net_income_usd" and last.get("net_income_year_ago_usd"):
        yoy = ", against %s a year earlier" % money(last["net_income_year_ago_usd"])
    every = bool(re.search(r"every quarter|each quarter|by quarter|per quarter|quarterly (?:revenue|sales|results|net income|profit|eps|earnings)|quarter by quarter|all quarters|last \d+ quarters|recent quarters", ql))
    if v is None:
        headline = "%s's %s for the quarter to %s is not tagged in its filings." % (rec["name"], label, date_words(last["quarter_end"]))
    elif every:
        headline = "%s's %s was %s in the quarter to %s%s." % (rec["name"], label, fmt(v), date_words(last["quarter_end"]), yoy)
    else:
        headline = "%s's %s was %s in the quarter to %s (fiscal %s %d)%s." % (rec["name"], label, fmt(v), date_words(last["quarter_end"]), last["fiscal_quarter"], last["fiscal_year"], yoy)
    bits = []
    # compared with what: growth against the quarters before it, or the figure against the same quarter a year earlier
    i = rows.index(last)
    g = last.get("revenue_growth_yoy_pct")
    if col == "revenue_usd" and g is not None and i > 0 and rows[i - 1].get("revenue_growth_yoy_pct") is not None:
        g0 = rows[i - 1]["revenue_growth_yoy_pct"]
        held = [r["revenue_growth_yoy_pct"] for r in rows[:i + 1] if r.get("revenue_growth_yoy_pct") is not None]
        rank = (", the fastest of the %d quarters on file" % len(held)) if len(held) >= 4 and g >= max(held) else \
               (", the slowest of the %d quarters on file" % len(held)) if len(held) >= 4 and g <= min(held) else ""
        bits.append("Growth %s from %s the quarter before%s." % ("picked up" if g > g0 + 0.5 else "slowed" if g < g0 - 0.5 else "held steady",
                                                              pct(g0, 1, sign=True), rank))
    elif col != "revenue_usd" and col != "net_income_usd" and v is not None:
        ago = next((r for r in rows if r["fiscal_quarter"] == last["fiscal_quarter"] and r["fiscal_year"] == last["fiscal_year"] - 1), None)
        if ago and ago.get(col) is not None:
            bits.append("A year earlier, in fiscal %s %d, it was %s." % (ago["fiscal_quarter"], ago["fiscal_year"], fmt(ago[col])))
    if last.get("q4_derived"):
        bits.append("The fourth quarter is the full year less the first three quarters: no filing reports it on its own.")
    if re.search(r"since (?:19|20)\d{2}|every quarter since", ql):
        yr = re.search(r"since ((?:19|20)\d{2})", ql)
        if yr and rows[0]["fiscal_year"] > int(yr.group(1)):
            bits.insert(0, "Only the last %d quarters are held, from %s %d; earlier quarters are not on file." % (len(rows), rows[0]["fiscal_quarter"], rows[0]["fiscal_year"]))
    table = {"columns": ["Quarter end", "Fiscal", "Revenue (USD)", "Operating income (USD)", "Net income (USD)", "Diluted EPS", "Revenue growth y/y %", "Q4 derived"],
             "rows": [[_d(r["quarter_end"]), "%s %d" % (r["fiscal_quarter"], r["fiscal_year"]), r["revenue_usd"], r["operating_income_usd"], r["net_income_usd"], r["eps_usd"],
                       r["revenue_growth_yoy_pct"], bool(r["q4_derived"])] for r in reversed(rows)], "unit": "USD"}
    pts = [[("%s %d" % (r["fiscal_quarter"], r["fiscal_year"])), r.get(col)] for r in rows if r.get(col) is not None]
    chart = {"kind": "bars", "title": "%s: %s by quarter" % (rec["ticker"] or rec["name"], label), "unit": "USD/share" if col == "eps_usd" else "USD",
             "series": [{"name": label, "points": pts}]} if len(pts) >= 2 else None
    srcs = [us_tables.sources().get("quarters") or SEC_SOURCE, SEC_SOURCE]
    return pkg("company_quarters", q, headline, " ".join(bits), [fig(label.capitalize(), v, "USD/share" if col == "eps_usd" else "USD", _d(last["quarter_end"])),
                                                                fig("Revenue growth, year on year", last.get("revenue_growth_yoy_pct"), "%", _d(last["quarter_end"]))],
               table, chart, srcs, {"revenue_growth_yoy_pct": "(revenue this quarter \u00f7 revenue in the same quarter a year earlier \u2212 1) \u00d7 100",
                                    "q4": "a fourth quarter no filing reports alone is the full year less the first three quarters"},
               {"company": rec, "quarter": "%s %d" % (last["fiscal_quarter"], last["fiscal_year"])}, {"company": company_link(rec)},
               ["Each quarter is as last filed in a 10-Q or 10-K; the table holds the last %d quarters." % len(rows)])


def r_ceo_pay(cur, q, e):
    """What a CEO was paid, from the company's latest proxy statement (S&P 500
    members), by company or by the CEO's name; the highest-paid without one."""
    rows = _pay_rows(cur)
    if not rows:
        raise HTTPException(503, "CEO pay is not loaded on this server right now.")
    src = {"name": "Proxy statements", "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (DEF 14A, pay versus performance disclosure)."}
    calc = {"ceo_total_pay_usd": "total compensation as reported in the Summary Compensation Table: salary, bonus, stock and option awards at grant-date value, other pay",
            "ceo_actually_paid_usd": "the SEC's 'compensation actually paid': equity awards marked to market over the year; it can be negative",
            "tsr_value_of_100": "what $100 invested at the start of the measurement period was worth at the fiscal year end, dividends reinvested"}
    idx = us_link.companies(cur)["by_ticker"]
    rec = e["companies"][0] if e.get("companies") else None
    row, ceo = None, None
    if rec:
        row = next((r for r in rows if (r.get("ticker") or "").upper().replace(".", "-") == (rec.get("ticker") or "").upper().replace(".", "-")), None)
        if row is None:
            raise HTTPException(404, "No proxy pay figures are loaded for %s: the pay table covers S&P 500 members' latest proxy statements." % rec["name"])
        ceo = row.get("ceo")
    else:
        hit = _ceo_named(q, rows)
        if hit:
            row, ceo = hit
            rec = idx.get((row.get("ticker") or "").upper()) or {"name": row.get("ticker"), "ticker": row.get("ticker"), "cik": None}
    ql = (q or "").lower()
    actual = bool(re.search(r"actually paid|realis|realiz", ql))
    if row is not None:
        total, paid, fy = row.get("ceo_total_pay_usd"), row.get("ceo_actually_paid_usd"), row.get("fiscal_year_end")
        lead = paid if actual else total
        headline = "%s, %s's CEO, was paid %s in the year to %s%s." % (
            ceo, short_name(rec["name"]), money(lead) if lead is not None else "\u2014", date_words(fy),
            " (compensation actually paid, equity marked to market)" if actual else " (total pay as reported)")
        if total is None and paid is None:
            headline = "%s's proxy statement carries no amount for %s's pay in the year to %s: its pay-versus-performance table leaves the CEO columns blank%s." % (
                short_name(rec["name"]), ceo, date_words(fy),
                (" (the other named executives averaged %s)" % money(row["other_execs_avg_pay_usd"])) if row.get("other_execs_avg_pay_usd") is not None else "")
        if re.search(r"who (is|runs|leads|heads|is the)|\bceo\b\?$|name of the ceo|who's the", ql) and not re.search(r"\bpa(y|id)\b|compensation|salar", ql):
            headline = "%s's CEO is %s, per its proxy statement filed %s; total pay %s in the year to %s." % (
                short_name(rec["name"]), ceo, date_words(row["proxy_filed"]) if row.get("proxy_filed") else "on file", money(total) if total is not None else "\u2014", date_words(fy))
        elif re.search(r"performance|\btsr\b|shareholder return|versus the stock|vs the stock", ql) and row.get("tsr_value_of_100") is not None:
            headline = "%s was paid %s in the year to %s while $100 in %s shares became $%s%s." % (
                ceo, money(total) if total is not None else "\u2014", date_words(fy), short_name(rec["name"]), num(row["tsr_value_of_100"], 0),
                (" (peers: $%s)" % num(row["peer_tsr_value_of_100"], 0)) if row.get("peer_tsr_value_of_100") is not None else "")
        bits = []
        # compared with what: where this pay ranks among the S&P 500's CEOs
        key = "ceo_actually_paid_usd" if actual else "ceo_total_pay_usd"
        if lead is not None:
            import statistics
            pool = sorted((r[key] for r in rows if r.get(key) is not None), reverse=True)
            if len(pool) >= 50:
                bits.append("That ranks %s of the %d S&P 500 CEOs with a figure, whose median is %s." % (
                    _ordinal(1 + sum(1 for x in pool if x > lead)), len(pool), money(statistics.median(pool))))
        if not actual and paid is not None:
            bits.append("Compensation actually paid, with equity awards marked to market over the year, was %s." % money(paid))
        if row.get("tsr_value_of_100") is not None:
            bits.append("$100 in the shares at %s was worth %s at the year end%s." % (
                date_words(row["tsr_since"]) if row.get("tsr_since") else "the start of the measurement period", "$%s" % num(row["tsr_value_of_100"], 0),
                (", against $%s for its peer group" % num(row["peer_tsr_value_of_100"], 0)) if row.get("peer_tsr_value_of_100") is not None else ""))
        table = {"columns": ["Figure", "Value"], "rows": [["Total pay as reported (USD)", total], ["Compensation actually paid (USD)", paid],
                                                          ["Other named executives, average (USD)", row.get("other_execs_avg_pay_usd")],
                                                          ["$100 in the shares became", row.get("tsr_value_of_100")], ["Peer group, $100 became", row.get("peer_tsr_value_of_100")]], "unit": "USD"}
        figures = [fig("Total pay as reported", total, "USD", fy, calc["ceo_total_pay_usd"]), fig("Compensation actually paid", paid, "USD", fy, calc["ceo_actually_paid_usd"])]
        return pkg("ceo_pay", q, headline, " ".join(b for b in bits if b), figures, table, None, [src], calc,
                   {"company": rec, "ceo": ceo}, {"company": company_link(rec)} if rec.get("cik") else {},
                   (["From the proxy statement filed %s; pay is for the company's own fiscal year." % date_words(row["proxy_filed"])] if row.get("proxy_filed") else []) +
                   ["Pay covers S&P 500 members, each company's latest proxy statement only."])
    who = person_named(q)
    if who and not re.search(r"(highest|best|top|lowest|worst).paid|most paid|paid (the )?most|\bceos\b", ql):
        raise HTTPException(404, "No proxy pay figures for %s are loaded: the pay table covers the S&P 500 members' latest proxy "
                                 "statements, and that name is not among their CEOs." % " ".join(who[:3]))
    # no company and no name: the highest-paid
    key = "ceo_actually_paid_usd" if actual else "ceo_total_pay_usd"
    low = bool(re.search(r"lowest|least|worst.paid|cheapest", ql))
    ranked = sorted([r for r in rows if r.get(key) is not None], key=lambda r: r[key], reverse=not low)
    n = e.get("count") or 10
    top = ranked[:n]
    if not top:
        raise HTTPException(404, "No CEO pay figures are loaded.")
    first = top[0]
    fname = short_name((idx.get((first.get("ticker") or "").upper()) or {}).get("name") or first.get("ticker"))
    headline = "%s S&P 500 CEO in each company's latest fiscal year: %s of %s, %s%s." % (
        "Lowest-paid" if low else "Highest-paid", first.get("ceo"), fname, money(first[key]),
        " (compensation actually paid)" if actual else " (total pay as reported)")
    others = "; ".join("%s (%s) %s" % (r.get("ceo"), short_name((idx.get((r.get("ticker") or "").upper()) or {}).get("name") or r.get("ticker")), money(r[key])) for r in top[1:5])
    table = {"columns": ["Rank", "CEO", "Ticker", "Fiscal year end", "Total pay as reported (USD)", "Compensation actually paid (USD)", "$100 in the shares became"],
             "rows": [[i + 1, r.get("ceo"), r.get("ticker"), _d(r.get("fiscal_year_end")), r.get("ceo_total_pay_usd"), r.get("ceo_actually_paid_usd"), r.get("tsr_value_of_100")]
                      for i, r in enumerate(top)], "unit": "USD"}
    chart = {"kind": "bars", "title": "%s S&P 500 CEOs, %s" % ("Lowest-paid" if low else "Highest-paid", "compensation actually paid" if actual else "total pay as reported"),
             "unit": "USD", "series": [{"name": "Pay", "points": [[r.get("ticker"), r[key]] for r in top]}]}
    return pkg("ceo_pay", q, headline, ("Next: %s." % others) if others else "", [fig("CEOs ranked", len(ranked), "count", None)], table, chart, [src], calc,
               {"metric": key, "count": n}, {}, ["Pay covers S&P 500 members, each company's latest proxy statement; years end on each company's own fiscal year end."])


def r_managers_holding_both(cur, q, e):
    cos = e["companies"][:2]
    if len(cos) < 2:
        raise HTTPException(404, "Name two companies to compare their holders.")
    cus = [[c["cusip"] for c in us_link.cusips_for(cur, co["cik"])] for co in cos]
    if not cus[0] or not cus[1]:
        raise HTTPException(404, "13F positions could not be matched for %s." % (cos[0]["name"] if not cus[0] else cos[1]["name"]))
    rows = cur.execute("""
        WITH latest AS (SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 1),
        a AS (SELECT f.manager_cik, arg_max(f.manager_name, f.filed) AS name, sum(h.value) AS v
              FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
              WHERE h.batch = (SELECT batch FROM latest) AND NOT f.amendment AND h.put_call IS NULL AND h.cusip IN (%s) GROUP BY f.manager_cik),
        b AS (SELECT f.manager_cik, sum(h.value) AS v
              FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
              WHERE h.batch = (SELECT batch FROM latest) AND NOT f.amendment AND h.put_call IS NULL AND h.cusip IN (%s) GROUP BY f.manager_cik)
        SELECT a.manager_cik, a.name, a.v, b.v FROM a JOIN b USING (manager_cik) ORDER BY a.v + b.v DESC
        """ % (",".join("?" * len(cus[0])), ",".join("?" * len(cus[1]))), cus[0] + cus[1]).fetchall()
    n_a = cur.execute("SELECT count(DISTINCT f.manager_cik) FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession) WHERE h.batch = (SELECT batch FROM sec_13f_batches WHERE status='ok' ORDER BY window_end DESC LIMIT 1) AND h.cusip IN (%s)" % ",".join("?" * len(cus[0])), cus[0]).fetchone()[0]
    headline = "%d managers hold both %s and %s; %s holds the most of the pair." % (len(rows), cos[0]["name"], cos[1]["name"], rows[0][1].title() if rows else "none")
    answer = "Of the %d managers reporting a %s position in the latest window, %d also report %s. Values are the latest 13F filings, long positions only." % (n_a, cos[0]["ticker"], len(rows), cos[1]["ticker"])
    table = {"columns": ["Manager", "%s value" % cos[0]["ticker"], "%s value" % cos[1]["ticker"], "Both"],
             "rows": [[r[1], r[2], r[3], (r[2] or 0) + (r[3] or 0)] for r in rows[:50]], "unit": "USD"}
    return pkg("managers_holding_both", q, headline, answer, [fig("Managers holding both", len(rows), "count", None)], table, None, [F13_SOURCE], {},
               {"companies": cos}, {"company": company_link(cos[0]), "company_2": company_link(cos[1])})


# ---- figures: everyday lines from the filings ------------------------------
# Each figure is a word pattern, a label, and the tags that carry it, in
# order of preference. An alternative that lists several tags is summed
# (debt is reported in parts), and the tags used are named on the row.
# Coverage counts are from the 6,757 latest annual reports on file
# (2026-10-19) and decide the order.
FIGURES = [
    {"id": "debt", "words": r"\bdebt\b|borrowings?\b|leverag|\bowe\b|indebted", "label": "borrowings", "unit": "USD", "kind": "instant",
     "tags": [["DebtLongtermAndShorttermCombinedAmount"], ["LongTermDebtAndCapitalLeaseObligations", "ShortTermBorrowings", "CommercialPaper"],
              ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper", "DebtCurrent"], ["LongTermDebt", "ShortTermBorrowings"],
              ["LongTermDebtAndFinanceLeasesNoncurrent", "LongTermDebtAndFinanceLeasesCurrent"], ["LongTermNotesPayable", "NotesPayable"],
              ["LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "ShortTermBorrowings"], ["LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]]},
    {"id": "rd", "words": r"research|r&d|\br ?and ?d\b|development spend", "label": "research and development spending", "unit": "USD", "kind": "duration",
     "tags": [["ResearchAndDevelopmentExpense"], ["ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]]},
    {"id": "gross_profit", "words": r"gross profit", "label": "gross profit", "unit": "USD", "kind": "duration", "tags": [["GrossProfit"]]},
    {"id": "gross_margin", "words": r"gross margin", "label": "gross margin", "unit": "%", "kind": "duration", "tags": [["GrossProfit"]], "ratio_to": "revenue"},
    {"id": "cost_of_sales", "words": r"cost of (sales|revenue|goods)|cogs", "label": "cost of sales", "unit": "USD", "kind": "duration",
     "tags": [["CostOfGoodsAndServicesSold"], ["CostOfRevenue"]]},
    {"id": "sga", "words": r"sg&a|selling, general|overheads?|general and administrative", "label": "selling, general and administrative expense", "unit": "USD", "kind": "duration",
     "tags": [["SellingGeneralAndAdministrativeExpense"], ["GeneralAndAdministrativeExpense", "SellingAndMarketingExpense"], ["GeneralAndAdministrativeExpense"]]},
    {"id": "opex", "words": r"operating expenses?|opex", "label": "operating expenses", "unit": "USD", "kind": "duration", "tags": [["OperatingExpenses"]]},
    {"id": "interest_expense", "words": r"interest expense|interest cost|interest bill", "label": "interest expense", "unit": "USD", "kind": "duration", "tags": [["InterestExpense"]]},
    {"id": "tax", "words": r"\btax(es)?\b", "label": "income tax expense", "unit": "USD", "kind": "duration", "tags": [["IncomeTaxExpenseBenefit"]]},
    {"id": "da", "words": r"depreciation|amortis|amortiz", "label": "depreciation and amortisation", "unit": "USD", "kind": "duration", "tags": [["DepreciationDepletionAndAmortization"]]},
    {"id": "inventory", "words": r"inventor(y|ies)|stock on hand", "label": "inventories", "unit": "USD", "kind": "instant", "tags": [["InventoryNet"]]},
    {"id": "receivables", "words": r"receivables?", "label": "accounts receivable", "unit": "USD", "kind": "instant", "tags": [["AccountsReceivableNetCurrent"]]},
    {"id": "ppe", "words": r"property, plant|plant and equipment|fixed assets|\bppe\b|\bpp&e\b", "label": "property, plant and equipment", "unit": "USD", "kind": "instant",
     "tags": [["PropertyPlantAndEquipmentNet"]]},
    {"id": "goodwill", "words": r"goodwill", "label": "goodwill", "unit": "USD", "kind": "instant", "tags": [["Goodwill"]]},
    {"id": "intangibles", "words": r"intangible", "label": "intangible assets", "unit": "USD", "kind": "instant", "tags": [["IntangibleAssetsNetExcludingGoodwill"]]},
    {"id": "investments", "words": r"short.term investments|marketable securities|securities held", "label": "short-term investments", "unit": "USD", "kind": "instant",
     "tags": [["ShortTermInvestments"], ["MarketableSecuritiesCurrent"], ["AvailableForSaleSecuritiesDebtSecurities"]]},
    {"id": "retained", "words": r"retained earnings|accumulated deficit", "label": "retained earnings", "unit": "USD", "kind": "instant", "tags": [["RetainedEarningsAccumulatedDeficit"]]},
    {"id": "current_assets", "words": r"current assets", "label": "current assets", "unit": "USD", "kind": "instant", "tags": [["AssetsCurrent"]]},
    {"id": "current_liabilities", "words": r"current liabilities", "label": "current liabilities", "unit": "USD", "kind": "instant", "tags": [["LiabilitiesCurrent"]]},
    {"id": "sbc", "words": r"stock.based comp|share.based comp|stock comp", "label": "share-based compensation", "unit": "USD", "kind": "duration", "tags": [["ShareBasedCompensation"]]},
    {"id": "acquisitions", "words": r"acquisition spend|spent on acquisitions|m&a spend|acquisitions", "label": "cash paid for acquisitions", "unit": "USD", "kind": "duration",
     "tags": [["PaymentsToAcquireBusinessesNetOfCashAcquired"]]},
    {"id": "leases", "words": r"lease liabilit|operating leases", "label": "operating lease liabilities", "unit": "USD", "kind": "instant",
     "tags": [["OperatingLeaseLiability"], ["OperatingLeaseLiabilityNoncurrent", "OperatingLeaseLiabilityCurrent"], ["LeaseLiabilityNoncurrent", "LeaseLiabilityCurrent"], ["LeaseLiabilityNoncurrent"]]},
    {"id": "tax_rate", "words": r"effective tax rate|\btax rate\b", "label": "effective tax rate", "unit": "%", "kind": "duration",
     "tags": [["IncomeTaxExpenseBenefit"]], "ratio_to": "pretax_income"},
    {"id": "dps", "words": r"dividend per share|dividends per share|\bdps\b", "label": "dividends declared per share", "unit": "USD/share", "kind": "duration",
     "tags": [["CommonStockDividendsPerShareDeclared"], ["CommonStockDividendsPerShareCashPaid"]]},
    {"id": "capex", "words": r"\bcapex\b|capital (expenditures?|spending|investment)|investment in (plant|property)", "label": "capital expenditure", "unit": "USD", "kind": "duration",
     "tags": [["PaymentsToAcquirePropertyPlantAndEquipment"], ["PaymentsToAcquireProductiveAssets"]]},
    {"id": "diluted_shares", "words": r"diluted shares|share count|number of shares", "label": "weighted diluted shares", "unit": "shares", "kind": "duration",
     "tags": [["WeightedAverageNumberOfDilutedSharesOutstanding"]]},
    {"id": "deposits", "words": r"deposits", "label": "deposits", "unit": "USD", "kind": "instant", "tags": [["Deposits"]]},
    {"id": "nii", "words": r"net interest income", "label": "net interest income", "unit": "USD", "kind": "duration", "tags": [["InterestIncomeExpenseNet"]]},
    {"id": "provisions", "words": r"provision for (loan|credit) loss|credit costs|loan loss", "label": "provision for loan losses", "unit": "USD", "kind": "duration",
     "tags": [["ProvisionForLoanLossesExpensed"], ["ProvisionForLoanLeaseAndOtherLosses"], ["ProvisionForCreditLosses"]]},
    {"id": "noninterest_expense", "words": r"non.?interest expense", "label": "non-interest expense", "unit": "USD", "kind": "duration", "tags": [["NoninterestExpense"]]},
    {"id": "advertising", "words": r"advertising|marketing spend", "label": "advertising expense", "unit": "USD", "kind": "duration", "tags": [["AdvertisingExpense"]]},
    {"id": "interest_paid", "words": r"interest paid", "label": "interest paid", "unit": "USD", "kind": "duration", "tags": [["InterestPaidNet"]]},
    {"id": "taxes_paid", "words": r"taxes paid|tax paid", "label": "income taxes paid", "unit": "USD", "kind": "duration", "tags": [["IncomeTaxesPaidNet"]]},
    {"id": "rd_intensity", "words": r"research intensity|r&d intensity|r&d as a share|research as a share", "label": "research spending as a share of revenue", "unit": "%", "kind": "duration",
     "tags": [["ResearchAndDevelopmentExpense"]], "ratio_to": "revenue"},
    {"id": "debt_to_equity", "words": r"debt.to.equity|gearing", "label": "borrowings as a share of equity", "unit": "%", "kind": "instant",
     "tags": [], "ratio_to": "equity"},
]
FIGURES[-1]["tags"] = FIGURES[0]["tags"]                       # the same borrowings lines as the debt figure
FIGURE_BY_ID = {f["id"]: f for f in FIGURES}
# Figures people ask for that the data sets do not carry, each with the honest reason.
NOT_HELD = [
    (r"(?!.*\b(?:at|does|do|has|have|of|employ)\b.*)(?:employees|headcount|staff\b|workforce|workers|people work|how many people)",
     "Employee counts sit in the text of each 10-K, not in a data set, so I can report one company's at a time ('how many employees does Microsoft have'), not rank them."),
    (r"\bebitda\b", "EBITDA is not a filed line; I can show operating income, and depreciation and amortisation, separately."),
    (r"backlog|order book", "Backlog is disclosed in the text of filings, not as a tagged figure I hold."),
    (r"same.store|comparable sales|like.for.like", "Same-store sales are not a tagged figure in the filings I hold."),
    (r"subscribers?|subscriber counts?|user counts?|customer counts?|how many (users|customers)", "Subscriber and user counts are not tagged figures in the filings I hold."),
    (r"\busers\b", "Subscriber and user counts are not tagged figures in the filings I hold."),
    (r"\bebit\b", "EBIT is not a filed line; operating income is the nearest, and I hold that."),
    (r"\bpeg\b|peg ratio", "I hold no growth forecasts, so there is no PEG ratio; the P/E and last year's growth are shown separately."),
    (r"\broic\b|return on invested capital|\broce\b|return on capital employed", "I don't compute return on invested capital; return on equity is held, from the latest annual report."),
    (r"which (companies|stocks|firms) (mention|say|discuss|talk about|cite|warn)|how many (companies|stocks|firms) (mention|say|discuss|cite|warn)",
     "I search one company's annual report at a time, not every report at once: ask what one company says, e.g. \u201cwhat does Apple say about tariffs\u201d."),
]
# Figures that can be told from the question alone are refused before any model runs
NOT_HELD_EARLY = [p for p in NOT_HELD if p[0] != r"\busers\b" and "employees" not in p[0]]   # one company's headcount is answered from its report


def not_held_early(question):
    """The honest reason, when the question plainly asks for a figure the data
    sets do not carry; None otherwise."""
    ql = (question or "").lower()
    for pat, msg in NOT_HELD_EARLY:
        if re.search(pat, ql):
            return msg
    return None


def figure_in(question):
    """The figure a question names, or None; raises 422 for one we do not hold."""
    ql = (question or "").lower()
    for pat, msg in NOT_HELD:
        if re.search(pat, ql):
            raise HTTPException(422, msg)
    best = None
    for f in FIGURES:
        m = re.search(f["words"], ql)
        # the longest match wins ("debt to equity" over "debt"); ties go to the earlier one
        if m and (best is None or len(m.group(0)) > best[1] or (len(m.group(0)) == best[1] and m.start() < best[0])):
            best = (m.start(), len(m.group(0)), f)
    return best[2] if best else None


def _all_figure_tags():
    tags = set()
    for f in FIGURES:
        for alt in f["tags"]:
            tags.update(alt)
    return sorted(tags)


FIGURE_TAGS = sorted(set(_all_figure_tags()) | {t for f, k, tags in sec_api.FIELDS if f == "pretax_income" for t in tags})


def _pick_alt(fig, have):
    """(value, tags used) from {tag: value} for the first alternative fully present."""
    for alt in fig["tags"]:
        vals = [have.get(t) for t in alt]
        if alt[0] in have and any(v is not None for v in vals):
            used = [t for t in alt if have.get(t) is not None]
            return sum(have[t] for t in used), used
    return None, []


def figure_for_filing(cur, adsh, period, fig):
    """(value, tags, prior value) of one figure in one filing, on its own
    period and the comparative a year earlier that the filer tagged."""
    q = 0 if fig["kind"] == "instant" else 4
    rows = cur.execute("""
        SELECT tag, ddate, value, uom FROM sec_facts WHERE adsh = ? AND segment_id IS NULL AND coreg IS NULL
          AND qtrs = ? AND tag IN (%s) AND ddate <= ? AND ddate >= ? - INTERVAL 400 DAY
        ORDER BY (uom = 'USD') DESC
        """ % ",".join("?" * len(FIGURE_TAGS)), [adsh, q] + FIGURE_TAGS + [period, period]).fetchall()
    cur_vals, cur_uom, prior_vals = {}, {}, {}
    lo, hi = period - datetime.timedelta(days=400), period - datetime.timedelta(days=330)
    for tag, d, v, uom in rows:                   # USD first, so a second currency never overwrites it
        if d == period and tag not in cur_vals:
            cur_vals[tag], cur_uom[tag] = v, uom
    for tag, d, v, uom in rows:                   # the comparative must be in the same unit
        if lo <= d <= hi and tag not in prior_vals and uom == cur_uom.get(tag):
            prior_vals[tag] = v
    value, used = _pick_alt(fig, cur_vals)
    prior = None
    if used:
        pv = [prior_vals.get(t) for t in used]
        prior = sum(v for v in pv if v is not None) if any(v is not None for v in pv) else None
    return value, used, prior


def figure_cross(cur, fig):
    """{cik: (value, tags used)} for every filer's latest annual report."""
    key = "figure:" + fig["id"]
    def build(c):
        q = 0 if fig["kind"] == "instant" else 4
        tags = sorted({t for alt in fig["tags"] for t in alt})
        sql = """
            WITH latest AS (
                SELECT * FROM (
                    SELECT s.adsh, s.cik, s.period,
                           row_number() OVER (PARTITION BY s.cik ORDER BY s.period DESC, s.filed DESC) AS rn
                    FROM sec_filings s
                    WHERE s.form IN (%s) AND s.status IN ('clean','partial')/*ASOF*/
                ) WHERE rn = 1)
            SELECT l.cik, f.tag, f.value, f.uom FROM latest l
            JOIN sec_facts f ON f.adsh = l.adsh AND f.ddate = l.period AND f.segment_id IS NULL
                            AND f.coreg IS NULL AND f.qtrs = ? AND f.tag IN (%s)
            ORDER BY (f.uom = 'USD') DESC
        """ % (",".join("?" * len(sec_api.ANNUAL_FORMS)), ",".join("?" * len(tags)))
        sql = sql.replace("/*ASOF*/", asof.clause("filed", "s"))
        have, unit = {}, {}
        for cik, tag, v, uom in c.execute(sql, list(sec_api.ANNUAL_FORMS) + [q] + tags).fetchall():
            if tag not in have.setdefault(cik, {}):
                have[cik][tag] = v
                unit[cik] = uom
        # a dollar ranking never carries a rupee figure: only USD-tagged lines rank
        have = {cik: vals for cik, vals in have.items() if unit.get(cik) == "USD"}
        out = {}
        for cik, vals in have.items():
            v, used = _pick_alt(fig, vals)
            if v is not None:
                out[cik] = (v, used)
        return out
    return _memo(cur, key, build)


def figure_value(cur, row, fig):
    """A figure for one metrics row (latest annual report): (value, tags, prior),
    turned into a ratio when the figure is defined as a share of a size field."""
    value, used, prior = figure_for_filing(cur, row["adsh"], datetime.date.fromisoformat(row["period_end"]), fig)
    if fig.get("ratio_to") == "pretax_income":
        pre = {"kind": "duration", "tags": [[t] for t in _FIELD_TAGS["pretax_income"]]}
        base, _, _ = figure_for_filing(cur, row["adsh"], datetime.date.fromisoformat(row["period_end"]), pre)
        value = (value / base * 100) if (value is not None and base) else None
        prior = None
    elif fig.get("ratio_to"):
        base = row["size"].get(fig["ratio_to"])
        value = (value / base * 100) if (value is not None and base) else None
        prior = None
    return value, used, prior


def _segmented_only(cur, adsh, fig):
    """True when the figure's tags appear in the filing only with a business
    segment attached (a carmaker's finance arm, a bank's divisions)."""
    tags = sorted({t for alt in fig["tags"] for t in alt})
    n = cur.execute("SELECT count(*) FROM sec_facts WHERE adsh = ? AND segment_id IS NOT NULL AND tag IN (%s)"
                    % ",".join("?" * len(tags)), [adsh] + tags).fetchone()[0]
    return n > 0


def _figure_sentence(rec, fig, value, used, prior, period_end, unit, segmented=False):
    fy = ("at %s" if fig["kind"] == "instant" else "for the year to %s") % date_words(period_end)
    if value is None and segmented:
        return ("%s reports %s by business segment only, with no company-wide line in its latest annual report; I do not add segments together." % (
            rec["name"], fig["label"]), "Tags looked for: %s." % ", ".join(t for alt in fig["tags"] for t in alt))
    if value is None:
        return ("%s's latest annual report tags no line for %s." % (rec["name"], fig["label"]),
                "Tags looked for: %s." % ", ".join(t for alt in fig["tags"] for t in alt))
    val = pct(value, 1) if fig["unit"] == "%" else (money(value) if unit == "USD" else "%s %s" % (num(value, 2 if "share" in fig["unit"] else 0), unit))
    head = "%s: %s %s, %s." % (rec["name"], fig["label"], val, fy)
    more = ""
    if prior is not None and prior:
        more = " A year earlier it was %s (%s)." % (money(prior) if unit == "USD" else num(prior, 0), pct((value / prior - 1) * 100, 1, sign=True) if fig["unit"] != "%" else "")
    more += " Tag%s used: %s." % ("s" if len(used) > 1 else "", ", ".join(used)) if used else ""
    if len(used) > 1:
        more += " The figure is the sum of those lines."
    return head, more


def r_company_figure(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    fig = e.get("figure") or figure_in(q)
    if fig is None:
        raise HTTPException(422, "Which figure? I can show debt, research spending, gross margin, cost of sales, inventories, goodwill, taxes, deposits and about twenty more.")
    row = row_for(cur, rec["cik"])
    if row is None:
        raise HTTPException(404, "%s has no annual report loaded." % rec["name"])
    value, used, prior = figure_value(cur, row, fig)
    head, more = _figure_sentence(rec, fig, value, used, prior, row["period_end"], row["unit"],
                                  segmented=(value is None and _segmented_only(cur, row["adsh"], fig)))
    # the same line in each annual report on file, oldest first
    hist = []
    try:
        filings = sec_api._filings(cur, rec["cik"], sec_api.ANNUAL_FORMS)[:6]
    except HTTPException:
        filings = []                                            # a filer under a new SEC number: the latest report only
    for f in filings:
        v, u, _ = figure_for_filing(cur, f["adsh"], f["period"], fig)
        if fig.get("ratio_to"):
            vals, _, _, _ = sec_api._indicators(cur, f)
            base = vals.get(fig["ratio_to"])
            v = (v / base * 100) if (v is not None and base) else None
        hist.append([_d(f["period"]), v, ", ".join(u), _d(f["filed"])])
    hist.reverse()
    table = {"columns": ["Fiscal year end", fig["label"].capitalize() + (" (%)" if fig["unit"] == "%" else ""), "Tags", "Filed"], "rows": hist, "unit": row["unit"]}
    chart = {"kind": "bars", "title": "%s: %s by annual report" % (rec["ticker"] or rec["name"], fig["label"]), "unit": "%" if fig["unit"] == "%" else row["unit"],
             "series": [{"name": fig["label"], "points": [[h[0][:4], h[1]] for h in hist]}]} if any(h[1] is not None for h in hist) else None
    bits = more
    if fig["unit"] == "USD" and row["size"].get("revenue") and value is not None and not fig.get("ratio_to"):
        bits += " That is %s of revenue." % pct(value / row["size"]["revenue"] * 100, 1)
    return pkg("company_figure", q, head, bits.strip(), [fig_(fig, value, row["period_end"])],
               table, chart, [SEC_SOURCE], {fig["id"]: "as filed under the tag(s) named; a figure reported in parts is their sum" + (("; shown as a share of " + fig["ratio_to"]) if fig.get("ratio_to") else "")},
               {"company": rec, "figure": fig["id"]}, {"company": company_link(rec), "filing": row["source_url"]})


def fig_(figd, value, as_of):
    return {"label": figd["label"].capitalize(), "value": value, "unit": figd["unit"], "as_of": as_of}


# the lines a comparison shows, the one asked about first: (label, unit, group, key)
COMPARE_LINES = [("Revenue", "USD", "size", "revenue"), ("Revenue growth", "%", "metrics", "revenue_growth_pct"),
                 ("Operating margin", "%", "metrics", "operating_margin_pct"), ("Net margin", "%", "metrics", "net_margin_pct"),
                 ("Operating income", "USD", "size", "operating_income"), ("Net income", "USD", "size", "net_income"),
                 ("Free cash flow", "USD", "metrics", "fcf"), ("Cash", "USD", "size", "cash"),
                 ("Return on equity", "%", "metrics", "roe_pct"), ("Market value", "USD", "metrics", "market_cap"),
                 ("P/E", "\u00d7", "metrics", "pe_x"), ("Dividend yield", "%", "metrics", "dividend_yield_pct"),
                 ("Buyback yield", "%", "metrics", "buyback_yield_pct"), ("Diluted EPS", "USD/share", "per_share", "eps_diluted")]
_COMPARE_WORDS = [(r"\bytd\b|year to date|this year|performance|share price|stock price|\breturns?\b(?!\s+on)|since (?:19|20)\d{2}|over the (?:past|last)|in (?:19|20)\d{2}\b|outperform|underperform|done better|done worse", "price_change"),
                  (r"free cash flow|\bfcf\b", "fcf"), (r"\bcash\b", "cash"), (r"operating income|operating profit", "operating_income"),
                  (r"profitab", "net_margin_pct"),
                  (r"\beps\b|earnings per share", "eps_diluted"), (r"\bgrow(s|th|ing)?\b|faster", "revenue_growth_pct"),
                  (r"buyback", "buyback_yield_pct"), (r"dividend", "dividend_yield_pct"), (r"gross margin", None),
                  (r"operating margin|\bmargins?\b", "operating_margin_pct"), (r"net margin", "net_margin_pct"),
                  (r"return on equity|\broe\b", "roe_pct"), (r"net income|profits?\b|earnings\b", "net_income"),
                  (r"revenue|sales", "revenue"), (r"\bp/?e\b|price.to.earnings|cheaper|expensive", "pe_x"),
                  (r"market (value|cap)|bigger|larger|\bworth\b|valuable", "market_cap")]


def _compare_key(q):
    """The metrics-row key a comparison question asks about, or None."""
    ql = (q or "").lower()
    for pat, key in _COMPARE_WORDS:
        if re.search(pat, ql):
            return key
    return None


def _compare_prices(cur, q, e, cos):
    """Two to four stocks' price changes over the same window ("Nvidia vs AMD ytd")."""
    start, end, words = _period(e, cur)
    ch = us_prices_api.window_changes(cur, start, end)
    vals = []
    for c in cos:
        w = ch.get((c.get("ticker") or "").replace("-", ".")) or ch.get(c.get("ticker") or "")
        vals.append(w[4] if w else None)
    ranked = sorted(range(len(cos)), key=lambda i: (vals[i] is None, -(vals[i] or 0)))
    if all(v is None for v in vals):
        raise HTTPException(404, "No prices on file for %s over that period." % " and ".join(c["name"] for c in cos))
    headline = "%s %s: %s." % (" against ".join(short_name(cos[i]["name"]) for i in ranked[:2]) if len(cos) == 2 else "Price change", words,
                                "; ".join("%s %s" % (short_name(cos[i]["name"]), pct(vals[i], 1, sign=True)) for i in ranked))
    if len(cos) == 2 and vals[ranked[0]] is not None and vals[ranked[1]] is not None:
        headline = "%s %s against %s for %s %s, %s pp apart." % (short_name(cos[ranked[0]]["name"]), pct(vals[ranked[0]], 1, sign=True), pct(vals[ranked[1]], 1, sign=True),
                                                                 short_name(cos[ranked[1]]["name"]), words, fmt_pp(abs(vals[ranked[0]] - vals[ranked[1]])))
    series = []
    for c in cos:
        s = closes(cur, c["ticker"], start, end)
        if s:
            a0 = s[0][1]
            series.append({"name": c["ticker"] or c["name"], "points": [[_d(d), round(v / a0 * 100, 3)] for d, v in s]})
    chart = {"kind": "line", "title": "%s, start = 100" % " vs ".join(c["ticker"] or c["name"] for c in cos), "unit": "start = 100", "series": series} if series else None
    table = {"columns": ["Company", "Ticker", "Change %", "First close", "Last close"],
             "rows": [[c["name"], c["ticker"], vals[i], (ch.get((c.get("ticker") or "").replace("-", ".")) or ch.get(c.get("ticker") or "") or (None,) * 5)[1],
                       (ch.get((c.get("ticker") or "").replace("-", ".")) or ch.get(c.get("ticker") or "") or (None,) * 5)[3]] for i, c in enumerate(cos)]}
    out = pkg("compare_companies", q, headline, "Price changes run from the first to the last close in the period, adjusted for splits, before dividends.",
              [fig("%s change" % short_name(c["name"]), round(v, 2) if v is not None else None, "%", _d(end) if end else None) for c, v in zip(cos, vals)],
              table, chart, [PRICE_SOURCE], {"change_pct": "(last close \u00f7 first close in the window \u2212 1) \u00d7 100, split-adjusted"},
              {"companies": cos, "since": _d(start)}, {"company": company_link(cos[0]), "company_2": company_link(cos[1])})
    if e.get("period_span"):
        out["period"] = period_span(*e["period_span"])
    return out


def r_compare_companies(cur, q, e):
    cos = e["companies"][:4]
    if len(cos) < 2:
        raise HTTPException(404, "Name two companies to compare.")
    rows = [row_for(cur, c["cik"]) for c in cos]
    if any(r is None for r in rows):
        missing = cos[[i for i, r in enumerate(rows) if r is None][0]]["name"]
        raise HTTPException(404, "%s has no annual report loaded." % missing)
    n = len(cos)
    figd = e.get("figure")
    try:
        figd = figd or figure_in(q)
    except HTTPException:
        raise
    key = _compare_key(q)
    if key == "price_change":
        return _compare_prices(cur, q, e, cos)
    if figd and key and key != "revenue" and not figd.get("ratio_to"):
        figd = None if key in ("fcf", "cash", "revenue_growth_pct", "market_cap", "pe_x") else figd
    lines = []
    if figd:
        vals = []
        for c, r in zip(cos, rows):
            v, used, _ = figure_value(cur, r, figd)
            vals.append(v if r.get("unit") == "USD" or figd["unit"] == "%" else None)
        lines.append((figd["label"].capitalize(), figd["unit"], vals))
        lab, u, order = figd["label"].capitalize(), figd["unit"], "desc"
    else:
        key = key or "market_cap"
        order = "asc" if (key == "pe_x" and re.search(r"cheap", (q or "").lower())) else "desc"
        asked = [s for s in COMPARE_LINES if s[3] == key]
        for lab, u, grp, k in (asked + [s for s in COMPARE_LINES if s[3] != key]):
            lines.append((lab, u, [(r[grp].get(k) if (r.get("unit") == "USD" or u in ("%", "\u00d7") or k == "market_cap") else None) for r in rows]))
        lab, u, vals = lines[0]
    ranked = sorted(range(n), key=lambda i: ((vals[i] is None), -(vals[i] or 0) if order == "desc" else (vals[i] or 0)))
    lead = ranked[0]
    if n == 2:
        headline = "%s: %s %s against %s for %s." % (lab, cos[lead]["name"], _fmt_unit(vals[lead], u), _fmt_unit(vals[ranked[1]], u), cos[ranked[1]]["name"])
    else:
        headline = "%s: %s." % (lab, "; ".join("%s %s" % (short_name(cos[i]["name"]), _fmt_unit(vals[i], u)) for i in ranked))
    if u == "%" and lab in ("Revenue growth",) and vals[lead] is not None:
        headline = "%s grew fastest: revenue %s against %s." % (short_name(cos[lead]["name"]), pct(vals[lead], 1, sign=True),
                                                                 ", ".join("%s for %s" % (pct(vals[i], 1, sign=True), short_name(cos[i]["name"])) for i in ranked[1:]))
    foreign = [c["name"] for c, r in zip(cos, rows) if r.get("unit") != "USD"]
    answer = ("%s reports in another currency, so its dollar lines are left blank; only ratios and market value compare." % " and ".join(foreign)) if foreign else ""
    compared = "Each company's latest annual report (%s)%s, with every ratio worked out the same way for each." % (
        "; ".join("%s to %s" % (c["ticker"] or c["name"], date_words(r["period_end"])) for c, r in zip(cos, rows)),
        " and the latest closes" if any(l[0] in ("Market value", "P/E", "Dividend yield", "Buyback yield") for l in lines) else "")
    table = {"columns": ["Figure"] + [c["name"] for c in cos],
             "rows": [[l[0] + (" (%s)" % l[1] if l[1] not in ("USD",) else "")] + list(l[2]) for l in lines], "unit": "USD"}
    chart = None
    first = lines[0]
    if sum(1 for v in first[2] if v is not None) >= 2:
        chart = {"kind": "bars", "title": first[0], "unit": first[1],
                 "series": [{"name": first[0], "points": [[cos[i]["ticker"] or cos[i]["name"][:16], first[2][i]] for i in range(n) if first[2][i] is not None]}]}
    calc = dict(sec_metrics.FORMULAS, **sec_metrics.PRICE_FORMULAS)
    calc["fcf"] = "operating cash flow \u2212 purchases of property, plant and equipment"
    links = {"company": company_link(cos[0]), "company_2": company_link(cos[1])}
    for i, c in enumerate(cos[2:], start=3):
        links["company_%d" % i] = company_link(c)
    return pkg("compare_companies", q, headline, answer, [], table, chart, [SEC_SOURCE, PRICE_SOURCE],
               {k: v for k, v in calc.items() if k in ("revenue_growth_pct", "operating_margin_pct", "net_margin_pct", "roe_pct", "market_cap", "pe_x", "dividend_yield_pct", "buyback_yield_pct", "fcf")},
               {"companies": cos}, links, [compared])


def _fmt_unit(v, unit):
    if v is None:
        return "—"
    if unit == "USD/share":
        return "$%s per share" % num(v, 2)
    if unit == "USD":
        return money(v)
    if unit == "%":
        return pct(v, 1)
    if unit == "×":
        return mult(v)
    return num(v, 0) + (" " + unit if unit else "")


# ---- history: one company's figure across its annual reports ---------------
# Specific phrases first, then the filed-line vocabulary (FIGURES), then the
# broad words — so "free cash flow" is never read as "cash", and "gross
# margin" is never read as "margin".
_FIELD_TAGS = {f: tags for f, kind, tags in sec_api.FIELDS}
_FIELD_KIND = {f: kind for f, kind, tags in sec_api.FIELDS}
HIST_SPECIFIC = [
    (r"free cash flow|\bfcf\b", "fcf", "free cash flow", "USD"),
    (r"operating cash flow|cash (flow )?from operations|operating cash", "cf_operating", "operating cash flow", "USD"),
    (r"capex|capital (spending|expenditure)", "capex", "capital expenditure", "USD"),
    (r"operating margin", "operating_margin", "operating margin", "%"),
    (r"net margin|profit margin", "net_margin", "net margin", "%"),
    (r"operating (income|profit)", "operating_income", "operating income", "USD"),
    (r"pre.?tax", "pretax_income", "pre-tax income", "USD"),
    (r"diluted eps|earnings per share|\beps\b", "eps_diluted", "diluted earnings per share", "USD/share"),
    (r"net income|net profit|net earnings", "net_income", "net income", "USD"),
    (r"buybacks?|repurchas", "buybacks", "buybacks", "USD"),
    (r"dividends?", "dividends_paid", "dividends paid", "USD"),
    (r"total assets", "total_assets", "total assets", "USD"),
    (r"total liabilities", "total_liabilities", "total liabilities", "USD"),
    (r"(stockholders'?|shareholders'?) equity|book value", "equity", "stockholders' equity", "USD"),
]
HIST_GENERIC = [
    (r"revenues?|\bsales\b|turnover|top line", "revenue", "revenue", "USD"),
    (r"\bprofits?\b|\bearnings\b", "net_income", "net income", "USD"),
    (r"\bassets\b", "total_assets", "total assets", "USD"),
    (r"\bequity\b", "equity", "stockholders' equity", "USD"),
    (r"\bcash\b", "cash", "cash and equivalents", "USD"),
    (r"\bmargins?\b", "operating_margin", "operating margin", "%"),
]
_HIST_PARTS = {"fcf": ("cf_operating", "capex"), "operating_margin": ("operating_income", "revenue"),
               "net_margin": ("net_income", "revenue")}
_HIST_WORDS = re.compile(r"\bchart\b|\bdraw\b|\bplot\b|\bgraph\b|histor|\btrend|over time|each year|every year|by year|"
                         r"year by year|annual(ly)? (figures|history)|\b(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty)[- ]?(years?|yrs?)\b|"
                         r"\bdecade\b|over the (last|past) \w+ years|since (19|20)\d\d")


def history_figure(question):
    """{'id', 'label', 'unit', 'parts' or 'tags'} for the figure a history
    question asks about; None if it names none. Figures we do not hold raise."""
    q = (question or "").lower()
    figure_in(q)                               # employees, EBITDA … say so here
    for pat, fid, label, unit in HIST_SPECIFIC:
        if re.search(pat, q):
            return {"id": fid, "label": label, "unit": unit}
    for f in FIGURES:
        if re.search(f["words"], q):
            return {"id": "fig:" + f["id"], "label": f["label"], "unit": f["unit"], "figure": f}
    for pat, fid, label, unit in HIST_GENERIC:
        if re.search(pat, q):
            return {"id": fid, "label": label, "unit": unit}
    return None


def _units_of(cur, cik):
    """The units a filer's figures may carry: dollars, or its own currency when it reports in one."""
    row = row_for(cur, cik)
    u = (row or {}).get("unit")
    out = ["USD", "USD/shares"]
    if u and u != "USD":
        out += [u, u + "/shares"]
    return out


def _cf_rows(cur, cik, tags, kind):
    """Company-facts rows (the deep-history set, 2009 on) for one company."""
    if not sec_api._has(cur, "sec_cf_facts"):
        return []
    span = ("AND period_end - period_start BETWEEN 350 AND 380" if kind == "duration" else "AND period_start IS NULL")
    units = _units_of(cur, cik)
    return cur.execute("""
        SELECT tag, period_end, value, filed, accn, unit FROM sec_cf_facts
        WHERE cik = ? AND tag IN (%s) AND fp = 'FY' AND form IN ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A')
          AND unit IN (%s) %s""" % (",".join("?" * len(tags)), ",".join("?" * len(units)), span), [cik] + list(tags) + units).fetchall()


def _fsds_rows(cur, cik, tags, kind):
    """Statement data-set rows: every annual report loaded for the company,
    current year and the comparatives it tagged."""
    adshs = [r[0] for r in cur.execute(
        "SELECT adsh FROM sec_filings WHERE cik = ? AND form IN (%s) AND status IN ('clean','partial')"
        % ",".join("?" * len(sec_api.ANNUAL_FORMS)), [cik] + list(sec_api.ANNUAL_FORMS)).fetchall()]
    if not adshs:
        return []
    units = _units_of(cur, cik)
    rows = cur.execute("""
        SELECT f.tag, f.ddate, f.value, s.filed, f.adsh, f.uom FROM sec_facts f JOIN sec_filings s USING (adsh)
        WHERE f.adsh IN (%s) AND f.tag IN (%s) AND f.qtrs = ? AND f.segment_id IS NULL AND f.coreg IS NULL
          AND f.uom IN (%s)""" % (",".join("?" * len(adshs)), ",".join("?" * len(tags)), ",".join("?" * len(units))),
        adshs + list(tags) + [4 if kind == "duration" else 0] + units).fetchall()
    # a filer that also gives a dollar translation (TSMC): dollars win for that year
    usd = {(r[0], r[1]) for r in rows if r[5] in ("USD", "USD/shares")}
    return [r for r in rows if r[5] in ("USD", "USD/shares") or (r[0], r[1]) not in usd]


def _annual_part(cur, cik, tags, kind, fy_month_day):
    """{period_end: {value, tag, filed, accn, first_value, first_filed}} for one
    line, from both sources. Per year the first candidate tag the company used
    that year wins (companies switch tags over time); the latest filed value
    is shown and the first-reported one kept beside it."""
    rows = _cf_rows(cur, cik, tags, kind) + _fsds_rows(cur, cik, tags, kind)
    by_end = {}
    for tag, end, value, filed, accn, unit in rows:
        if value is None or end is None:
            continue
        # The statement data sets round every period end to its month end
        # (25 Sep -> 30 Sep); company facts keep the exact day. One fiscal year
        # must be one key, so both are filed under the nearest month end.
        end = _month_end(end)
        if kind == "instant" and fy_month_day:
            # only balance-sheet dates at a fiscal year end (52/53-week years drift a week)
            ref = datetime.date(end.year, fy_month_day[0], min(fy_month_day[1], 28))
            if min(abs((end - ref).days), abs((end - ref.replace(year=end.year - 1)).days),
                   abs((end - ref.replace(year=end.year + 1)).days)) > 10:
                continue
        by_end.setdefault(end, []).append((tags.index(tag), filed, value, tag, accn))
    out = {}
    for end, hits in by_end.items():
        best = min(h[0] for h in hits)
        mine = sorted([h for h in hits if h[0] == best], key=lambda h: h[1])
        first, last = mine[0], mine[-1]
        out[end] = {"value": last[2], "tag": last[3], "filed": last[1], "accn": last[4],
                    "first_value": first[2], "first_filed": first[1]}
    return out


def _month_end(d):
    if d.day < 15:
        d = d.replace(day=1) - datetime.timedelta(days=1)
    nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return nxt - datetime.timedelta(days=1)


def annual_history(cur, cik, hf, latest_period):
    """[(period_end, value, detail)] oldest first for a history figure."""
    md = (latest_period.month, latest_period.day) if latest_period else None
    def part(field):
        return _annual_part(cur, cik, _FIELD_TAGS[field], _FIELD_KIND[field], md)
    fid = hf["id"]
    if fid.startswith("fig:"):
        f = hf["figure"]
        kind = f["kind"]
        alts = f["tags"]
        # a summed figure (debt) takes, per year, the first alternative that has a value
        series = {}
        for alt in alts:
            pieces = [_annual_part(cur, cik, [t], kind, md) for t in alt]
            for end in set().union(*[set(p) for p in pieces]):
                if end in series:
                    continue
                if alt[0] not in [p[end]["tag"] for p in pieces if end in p] and len(alt) > 1 and end not in pieces[0]:
                    continue
                vals = [p[end] for p in pieces if end in p]
                series[end] = {"value": sum(v["value"] for v in vals), "tag": " + ".join(v["tag"] for v in vals),
                               "filed": max(v["filed"] for v in vals), "accn": vals[0]["accn"],
                               "first_value": sum(v["first_value"] for v in vals), "first_filed": min(v["first_filed"] for v in vals)}
        if f.get("ratio_to"):
            base = part(f["ratio_to"] if f["ratio_to"] in _FIELD_TAGS else "revenue")
            series = {e: dict(v, value=v["value"] / base[e]["value"] * 100, first_value=None, tag=v["tag"] + " ÷ " + base[e]["tag"])
                      for e, v in series.items() if e in base and base[e]["value"]}
    elif fid in _HIST_PARTS:
        a, b = _HIST_PARTS[fid]
        pa, pb = part(a), part(b)
        series = {}
        for end in set(pa) & set(pb):
            if fid == "fcf":
                v = pa[end]["value"] - pb[end]["value"]
                fv = pa[end]["first_value"] - pb[end]["first_value"]
                tag = pa[end]["tag"] + " − " + pb[end]["tag"]
            else:
                if not pb[end]["value"]:
                    continue
                v = pa[end]["value"] / pb[end]["value"] * 100
                fv = None
                tag = pa[end]["tag"] + " ÷ " + pb[end]["tag"]
            series[end] = {"value": v, "tag": tag, "filed": max(pa[end]["filed"], pb[end]["filed"]),
                           "accn": pa[end]["accn"], "first_value": fv, "first_filed": min(pa[end]["first_filed"], pb[end]["first_filed"])}
    else:
        series = part(fid)
    return sorted(series.items())


def _fy(end):
    return "fiscal %d" % end.year


def _fmt_hist(v, unit):
    if v is None:
        return "—"
    if unit == "%":
        return pct(v, 1)
    if unit == "USD/share":
        return money(v, 2)
    return money(v)


def r_company_history(cur, q, e):
    cos = e["companies"][:3]
    if not cos:
        raise HTTPException(404, "I could not tell which company you mean. Try a ticker, such as AAPL, or the full name.")
    hf = e.get("history") or history_figure(q)
    if hf is None:
        raise HTTPException(422, "Which figure over the years? I can chart revenue, net income, operating income, margins, "
                                 "earnings per share, cash flows, buybacks, dividends, debt, research spending and more.")
    # A named year or span ("in 2019", "2015 vs 2025", "fiscal 2023", "five years
    # ago") picks fiscal years by the calendar year they end in; otherwise the
    # years asked for, or five. "since 2015" runs from that year to the latest.
    window = us_link.parse_year_window(q)
    since, until = e.get("since"), e.get("until")
    if window and not (since and until and since <= window[0] and until >= window[1]):
        since, until = window
    if not window and re.search(r"\blast (?:fiscal )?year\b|\blatest\b|most recent", (q or "").lower()) and not e.get("years"):
        since, until = None, None                               # "revenue last year": the latest fiscal year, with its path
    plain = not (window or e.get("years") or since or _HIST_WORDS.search((q or "").lower()))
    peak_q = bool(re.search(r"\bpeak\b|\brecord\b|best year|worst year|\btrough\b", (q or "").lower()))
    years = e.get("years") or (None if (since or peak_q) else 5)
    per_co, notes = [], []
    for rec in cos:
        head = cur.execute("SELECT max(period) FROM sec_filings WHERE cik = ? AND form IN (%s)"
                           % ",".join("?" * len(sec_api.ANNUAL_FORMS)), [rec["cik"]] + list(sec_api.ANNUAL_FORMS)).fetchone()[0]
        hist = annual_history(cur, rec["cik"], hf, head)
        if since:
            hist = [h for h in hist if h[0].year >= since.year]
        if until:
            hist = [h for h in hist if h[0].year <= until.year]
        if years and not until:
            hist = hist[-years:]
        per_co.append((rec, hist))
    rec, hist = per_co[0]
    ccy = (row_for(cur, rec["cik"]) or {}).get("unit") or "USD"
    def _fmt_hist_ccy(v, u):
        return _fmt_hist(v, u) if ccy == "USD" or u != "USD" else money_in(v, ccy)
    if not hist and plain and len(per_co) == 1:
        got = _latest_from_row(cur, rec, hf, q)
        if got:
            return got
    if not hist and (since or until) and len(per_co) == 1:
        full = annual_history(cur, rec["cik"], hf, cur.execute(
            "SELECT max(period) FROM sec_filings WHERE cik = ? AND form IN (%s)" % ",".join("?" * len(sec_api.ANNUAL_FORMS)),
            [rec["cik"]] + list(sec_api.ANNUAL_FORMS)).fetchone()[0])
        if full:
            raise HTTPException(404, "No annual %s for %s is on file for %s; the years on file run %d to %d." % (
                hf["label"], rec["name"], ("fiscal %d" % since.year) if since and until and since.year == until.year else "those years",
                full[0][0].year, full[-1][0].year))
    if not hist:
        f = hf.get("figure")
        row = row_for(cur, rec["cik"])
        if f and row and _segmented_only(cur, row["adsh"], f):
            raise HTTPException(404, "%s reports %s by business segment only, with no company-wide line, so there is no "
                                     "yearly figure to chart; I do not add segments together." % (rec["name"], f["label"]))
        raise HTTPException(404, "No annual figure for %s is on file for %s." % (hf["label"], rec["name"]))
    unit = hf["unit"]
    first, last = hist[0], hist[-1]
    n_years = last[0].year - first[0].year
    owner = rec["name"] + ("'" if rec["name"].endswith("s") else "'s")
    peak = re.search(r"\bpeak\b|\brecord\b|\bhighest\b|best year|\blowest\b|worst year|\btrough\b", (q or "").lower())
    if peak and len(hist) >= 2 and len(per_co) == 1:
        low = bool(re.search(r"lowest|worst|trough", peak.group(0)))
        pk = min(hist, key=lambda h: h[1]["value"]) if low else max(hist, key=lambda h: h[1]["value"])
        headline = "%s %s %s at %s in %s; it was %s in %s." % (owner, hf["label"], "was lowest" if low else "peaked",
                                                               _fmt_hist_ccy(pk[1]["value"], unit), _fy(pk[0]), _fmt_hist_ccy(last[1]["value"], unit), _fy(last[0]))
    elif plain and len(hist) >= 2 and unit != "%" and len(per_co) == 1:
        # "Apple revenue": the latest year's figure first, then the path
        prev = hist[-2]
        pv, lv = prev[1]["value"], last[1]["value"]
        change = (", %s %s on %s" % ("up" if lv >= pv else "down", pct(abs((lv / pv - 1) * 100), 1), _fy(prev[0]))) if pv and pv > 0 else ""
        headline = "%s %s was %s in %s%s." % (owner, hf["label"], _fmt_hist_ccy(lv, unit), _fy(last[0]), change)
    elif plain and len(hist) >= 2 and len(per_co) == 1:
        prev = hist[-2]
        headline = "%s %s was %s in %s, against %s in %s." % (owner, hf["label"], pct(last[1]["value"]), _fy(last[0]),
                                                              pct(prev[1]["value"]), _fy(prev[0]))
    elif len(hist) == 1:
        headline = "%s %s was %s in %s." % (owner, hf["label"], _fmt_hist_ccy(last[1]["value"], unit), _fy(last[0]))
    elif unit == "%":
        d = last[1]["value"] - first[1]["value"]
        headline = "%s %s went from %s in %s to %s in %s, %s %s pp." % (
            owner, hf["label"], pct(first[1]["value"]), _fy(first[0]), pct(last[1]["value"]), _fy(last[0]),
            "up" if d >= 0 else "down", fmt_pp(abs(d)))
    else:
        a, b = first[1]["value"], last[1]["value"]
        verb = "rose" if b >= a else "fell"
        headline = "%s %s %s from %s in %s to %s in %s" % (owner, hf["label"], verb, _fmt_hist_ccy(a, unit), _fy(first[0]),
                                                             _fmt_hist_ccy(b, unit), _fy(last[0]))
        if a > 0 and b > 0 and n_years > 0:
            cagr = ((b / a) ** (1.0 / n_years) - 1) * 100
            headline += ", %s %s a year on average." % (pct(abs(cagr), 1), "growth" if cagr >= 0 else "decline")
        else:
            headline += "."
    bits = []
    if plain and len(hist) >= 2 and unit != "%" and len(per_co) == 1 and first[1]["value"] and last[1]["value"] > 0 and first[1]["value"] > 0 and n_years > 0:
        cagr = ((last[1]["value"] / first[1]["value"]) ** (1.0 / n_years) - 1) * 100
        bits.append("From %s in %s, that is %s a year on average." % (_fmt_hist_ccy(first[1]["value"], unit), _fy(first[0]), pct(cagr, 1, sign=True)))
    if last[0].month != 12 and len(per_co) == 1:
        bits.append("%s fiscal year ends in %s." % (owner, last[0].strftime("%B")))
    asked = years or (last[0].year - since.year + 1 if since and not until else None)
    if asked and len(hist) < asked:
        bits.append("You asked for %d years; %d %s on file." % (asked, len(hist), "is" if len(hist) == 1 else "are"))
    restated = [h for h in hist if h[1].get("first_value") is not None and h[1]["first_value"] != h[1]["value"]]
    restate_note = ("Each year shows the latest figure the %s filed for it%s." % ("company" if len(per_co) == 1 else "companies", 
        "; %d %s later restated, and the first-reported figure is in the table" % (len(restated), "year was" if len(restated) == 1 else "years were")
        if restated else ", and none was later restated"))
    if hf["id"] in _HIST_PARTS:
        bits.append({"fcf": "Free cash flow = operating cash flow − purchases of property, plant and equipment.",
                     "operating_margin": "Operating margin = operating income ÷ revenue × 100.",
                     "net_margin": "Net margin = net income ÷ revenue × 100."}[hf["id"]])
    def _growth(hh):
        if len(hh) < 2 or unit == "%":
            return None
        a, b, n = hh[0][1]["value"], hh[-1][1]["value"], hh[-1][0].year - hh[0][0].year
        return ((b / a) ** (1.0 / n) - 1) * 100 if (a > 0 and b > 0 and n > 0) else None
    if len(per_co) > 1:
        # several companies: the headline compares them, each over its own fiscal years
        parts = []
        for c, hh in per_co:
            if not hh:
                bits.append("No annual figure for %s is on file for %s." % (hf["label"], c["name"]))
                continue
            g = _growth(hh)
            if unit == "%":
                parts.append((hh[-1][1]["value"], "%s %s in %s" % (c["name"], pct(hh[-1][1]["value"]), _fy(hh[-1][0]))))
            else:
                parts.append((g if g is not None else -1e9, "%s %s, from %s in %s to %s in %s" % (
                    c["name"], ("%s a year" % pct(g, 1, sign=True)) if g is not None else "no average", _fmt_hist_ccy(hh[0][1]["value"], unit),
                    _fy(hh[0][0]), _fmt_hist_ccy(hh[-1][1]["value"], unit), _fy(hh[-1][0]))))
        parts.sort(key=lambda x: -x[0])
        if unit == "%":
            headline = "%s, latest year: %s." % (hf["label"].capitalize(), "; ".join(p for _, p in parts))
        else:
            headline = "%s growth on average: %s." % (hf["label"].capitalize(), "; ".join(p for _, p in parts))
        ends = sorted({c_hh[-1][0].strftime("%B") for _, c_hh in per_co if c_hh})
        if len(ends) > 1:
            bits.insert(0, "The companies' fiscal years end in different months (%s); each year on the chart is the fiscal year "
                           "ending in that calendar year." % ", ".join(ends))
    # chart: one line per company, scaled for reading
    big = max(abs(h[1]["value"]) for _, hh in per_co for h in hh) if any(hh for _, hh in per_co) else 0
    scale, ulabel = 1.0, ("%" if unit == "%" else "$ per share" if unit == "USD/share" else "$")
    if unit == "USD":
        sym = "$" if ccy == "USD" else CCY_SYMBOLS.get(ccy, ccy + " ")
        scale, ulabel = ((1e9, sym + "bn") if big >= 1e9 else (1e6, sym + "mn") if big >= 1e6 else (1.0, sym))
    # one shared axis of fiscal years; a company without a figure for a year
    # gets a gap there, never a neighbour's point shifted into its place
    all_years = sorted({h[0].year for _, hh in per_co for h in hh})
    chart = {"kind": "line", "title": "%s, %s" % (hf["label"].capitalize(), " vs ".join(c["ticker"] or c["name"] for c, _ in per_co)),
             "unit": ulabel,
             "series": [{"name": c["ticker"] or c["name"],
                         "points": [[str(y), (round(dict((h[0].year, h[1]["value"]) for h in hh)[y] / scale, 4)
                                              if y in dict((h[0].year, 1) for h in hh) else None)] for y in all_years]}
                        for c, hh in per_co if hh]}
    label_col = hf["label"].capitalize() + (" %" if unit == "%" else " value" if unit == "USD" else "")
    multi = len(per_co) > 1
    table = {"columns": (["Company"] if multi else []) + ["Fiscal year end", label_col,
                         "First reported value" if unit != "%" else "First reported %", "Tag", "Latest filed", "Accession"],
             "rows": [([c["ticker"] or c["name"]] if multi else []) +
                      [_d(h[0]), h[1]["value"], h[1].get("first_value"), h[1]["tag"], _d(h[1]["filed"]), h[1]["accn"]]
                      for c, hh in per_co for h in reversed(hh)], "unit": ccy if unit == "USD" else unit}
    calc = {"history": "each fiscal year's figure as last filed by the company; companies switch tags over time, so the tag "
                       "is named per year; a year restated later shows the first-reported value beside it"}
    if hf["id"] in _HIST_PARTS or (hf.get("figure") or {}).get("ratio_to"):
        calc["formula"] = bits[-1] if hf["id"] in _HIST_PARTS else "%s as a share of revenue" % hf["label"]
    if unit not in ("%",) and len(hist) > 1 and hist[0][1]["value"] > 0 and hist[-1][1]["value"] > 0:
        calc["average_growth"] = "(last ÷ first) ^ (1 ÷ years between them) − 1, × 100"
    return pkg("company_history", q, headline, " ".join(bits),
               [fig_({"label": hf["label"], "unit": unit}, last[1]["value"], _d(last[0])),
                {"label": "Years shown", "value": len(hist), "unit": "count", "as_of": None}],
               table, chart, [SEC_SOURCE], calc, {"company": rec, "figure": hf["id"], "years": years},
               {"company": company_link(rec)},
               [restate_note, "Figures come from each annual report's own statements: SEC company facts for the companies held at full "
                "depth, and the SEC's quarterly data sets (each 10-K carries three years) for the rest."])


CCY_SYMBOLS = {"USD": "$", "JPY": "\u00a5", "EUR": "\u20ac", "GBP": "\u00a3", "CNY": "CN\u00a5", "TWD": "NT$", "KRW": "\u20a9",
               "INR": "\u20b9", "BRL": "R$", "CAD": "C$", "AUD": "A$", "HKD": "HK$", "MXN": "MX$", "ARS": "AR$", "SGD": "S$"}


def money_in(v, ccy):
    """A sum in the filer's own currency: \u00a550.7tn, DKK 309bn."""
    if v is None:
        return "\u2014"
    sym = CCY_SYMBOLS.get(ccy or "USD", (ccy or "") + " ")
    return money(v).replace("$", sym, 1) if money(v).startswith(("$", "\u2212$", "-$")) else sym + money(v)


_ROW_FIELDS = {"revenue": ("size", "revenue", "revenue"), "net_income": ("size", "net_income", "net income"),
               "operating_income": ("size", "operating_income", "operating income"), "cash": ("size", "cash", "cash and equivalents"),
               "total_assets": ("size", "total_assets", "total assets"), "equity": ("size", "equity", "stockholders' equity"),
               "fcf": ("metrics", "fcf", "free cash flow"), "eps_diluted": ("per_share", "eps_diluted", "diluted earnings per share"),
               "operating_margin": ("metrics", "operating_margin_pct", "operating margin"), "net_margin": ("metrics", "net_margin_pct", "net margin")}


def _latest_from_row(cur, rec, hf, q):
    """The latest annual figure from the company's metrics row when no year-by-year
    history is on file (a filer under a new SEC number), or None."""
    row = row_for(cur, rec["cik"])
    spec = _ROW_FIELDS.get(hf["id"])
    if row is None or spec is None:
        return None
    grp, key, label = spec
    v = (row.get(grp) or {}).get(key)
    if v is None:
        return None
    ccy = row.get("unit") or "USD"
    owner = rec["name"] + ("'" if rec["name"].endswith("s") else "'s")
    if hf["unit"] == "%":
        shown = pct(v, 1)
    elif hf["unit"] == "USD/share":
        shown = "%s%s" % (CCY_SYMBOLS.get(ccy, ccy + " "), num(v, 2))
    else:
        shown = money_in(v, ccy)
    headline = "%s %s was %s in the year to %s." % (owner, label, shown, date_words(row["period_end"]))
    prior = (row.get("prior") or {}).get(key) if grp == "size" else None
    bits = []
    if prior:
        bits.append("A year earlier it was %s (%s)." % (money_in(prior, ccy), pct((v / prior - 1) * 100, 1, sign=True)))
    bits.append("Only the latest annual report is on file for this filer, so there is no year-by-year chart yet.")
    return pkg("company_history", q, headline, " ".join(bits), [fig_({"label": label, "unit": hf["unit"]}, v, row["period_end"]),
                                                             {"label": "Years shown", "value": 1, "unit": "count", "as_of": None}],
               None, None, [SEC_SOURCE], {"figure": "as filed in the latest annual report"}, {"company": rec, "figure": hf["id"], "years": 1},
               {"company": company_link(rec), "filing": row.get("source_url")})


def fmt_pp(v):
    return ("%.1f" % v).replace("-", "−")


# ---- benchmarks: a stock against an index, and contributions to an index ----
def _period(e, cur=None):
    """(start, end, words) for a price window; so far this year when none is
    named. With `cur`, the words carry both dates ("so far in 2026 (1 January
    to 16 October 2026)"), the end being the latest close on file, so a reader
    sees exactly which days are compared."""
    today = datetime.date.today()
    start = e.get("since") or datetime.date(today.year, 1, 1)
    end = e.get("until")
    if end:
        words = "in %d" % start.year if start.year == end.year and start.month == 1 and start.day == 1 else \
            "from %s to %s" % (date_words(start), date_words(end))
    elif start == datetime.date(today.year, 1, 1):
        words = "so far in %d" % today.year
    else:
        words = "since %s" % date_words(start)
    last = us_prices_api.latest_date(cur) if cur is not None else None
    e["period_span"] = (start, end or last)
    return start, end, words


def period_span(start, stop):
    """The two dates a comparison runs between, for the quiet line under the
    headline: "1 January – 16 October 2026", or across years "2 July 2025 –
    16 October 2026". The headline keeps its plain words ("so far in 2026")."""
    if not start or not stop:
        return None
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start[:10])
    if isinstance(stop, str):
        stop = datetime.date.fromisoformat(stop[:10])
    if start.year == stop.year:
        return {"from": _d(start), "to": _d(stop), "text": "%s \u2013 %s" % (start.strftime("%-d %B"), date_words(stop))}
    return {"from": _d(start), "to": _d(stop), "text": "%s \u2013 %s" % (date_words(start), date_words(stop))}


_SUFFIX = re.compile(r"(?:,?\s+(?:Inc\.?|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|LLC|L\.P\.|/\w+/?))+$")


def short_name(name):
    """'Nvidia Corp' -> 'Nvidia', 'Meta Platforms, Inc.' -> 'Meta Platforms': the name a reader would write."""
    n = us_link.display_name(name)
    return _SUFFIX.sub("", n).strip().rstrip(" &,") or n


def _bench_name(b):
    return "the " + b["name"] if not b["name"].startswith(("S&P", "Dow", "Nasdaq", "Russell")) else "the " + b["name"]


def r_vs_benchmark(cur, q, e):
    cos = e["companies"][:6]
    if not cos:
        raise HTTPException(404, "Name the companies to compare with the index, e.g. Apple and Meta against the S&P 500.")
    key = e.get("benchmark") or e.get("index") or "sp500"
    b = us_benchmarks.BENCHMARKS[key]
    start, end, words = _period(e, cur)
    bench, _ = us_prices_api.clean_closes(cur, b["etf"], start, end)
    if len(bench) < 2:
        raise HTTPException(404, "No prices on file for the %s fund (%s) over that period." % (b["name"], b["etf"]))
    bret = (bench[-1][2] / bench[0][2] - 1) * 100
    results, missing = [], []
    for rec in cos:
        series, info = us_prices_api.clean_closes(cur, rec["ticker"], start, end)
        if len(series) < 2:
            missing.append(rec["name"])
            continue
        ret = (series[-1][2] / series[0][2] - 1) * 100
        results.append({"rec": rec, "ret": ret, "gap": ret - bret, "series": series, "info": info})
    if not results:
        raise HTTPException(404, "No prices on file for %s over that period." % ", ".join(missing))
    under = [r for r in results if r["gap"] < 0]
    over = [r for r in results if r["gap"] >= 0]
    bn = _bench_name(b)
    def names(rs):
        n = [short_name(r["rec"]["name"]) for r in rs]
        return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " and " + n[-1]
    if not over:
        headline = "%s%s underperformed %s %s." % ("Both " if len(results) == 2 else ("All of " if len(results) > 2 else ""),
                                                   names(results), bn, words)
    elif not under:
        headline = "%s%s outperformed %s %s." % ("Both " if len(results) == 2 else ("All of " if len(results) > 2 else ""),
                                                 names(results), bn, words)
    else:
        headline = "%s outperformed %s %s; %s underperformed." % (names(over), bn, words, names(under))
    bits = ["%s against %s for %s." % (", ".join("%s %s (%s pp %s)" % (short_name(r["rec"]["name"]), pct(r["ret"], 1, sign=True),
                                                                      fmt_pp(abs(r["gap"])), "ahead" if r["gap"] >= 0 else "behind")
                                                for r in results), pct(bret, 1, sign=True), bn)]
    if missing:
        bits.append("No prices on file for %s." % ", ".join(missing))
    vs_notes = ["%s is measured by the price of the %s fund, which tracks it; every figure is a price change from %s to %s, "
                "adjusted for splits, before dividends." % (b["name"], b["etf"], date_words(bench[0][0]), date_words(bench[-1][0]))]
    if not e.get("since"):
        vs_notes.append("No period was named, so this is %s; ask \u201cover the past year\u201d or \u201csince 2025\u201d for another." % words)
    for r in results:
        if r["info"].get("linked_from"):
            vs_notes.append("%s traded as %s before %s; the comparison follows it across the ticker change." % (
                short_name(r["rec"]["name"]), r["info"]["linked_from"][0], date_words(r["info"]["linked_from"][1])))
    def rebased(series):
        a0 = series[0][2]
        return [[_d(d), round(a / a0 * 100, 3)] for d, c, a in series]
    chart = {"kind": "line", "title": "%s against %s, start = 100" % (" and ".join(r["rec"]["ticker"] for r in results), b["name"]),
             "unit": "start = 100",
             "series": [{"name": r["rec"]["ticker"] or r["rec"]["name"], "points": rebased(r["series"])} for r in results] +
                       [{"name": "%s (%s)" % (b["name"], b["etf"]), "points": rebased(bench)}]}
    table = {"columns": ["Company", "Ticker", "Change %", "Benchmark change %", "Difference (pp)", "Result"],
             "rows": [[r["rec"]["name"], r["rec"]["ticker"], r["ret"], bret, round(r["gap"], 2),
                       "Outperformed" if r["gap"] >= 0 else "Underperformed"] for r in results]}
    return pkg("vs_benchmark", q, headline, " ".join(bits),
               [fig_({"label": "%s change" % b["name"], "unit": "%"}, round(bret, 2), _d(bench[-1][0]))],
               table, chart, [PRICE_SOURCE],
               {"change": "(last close ÷ first close in the period − 1) × 100, split-adjusted",
                "difference": "company change − benchmark change, in percentage points",
                "benchmark": "the %s fund's price, which tracks the %s before dividends and its fee" % (b["etf"], b["name"])},
               {"companies": cos, "benchmark": key, "since": _d(start)},
               {"company": company_link(cos[0])} if len(results) == 1 else {}, vs_notes)


_CONTRIB_WORDS = re.compile(r"contribut|caused by|driven by|\bdrove\b|account(ed|s)? for|share of the (gain|rise|rally|return|move)|"
                            r"how much of .*(gain|rise|return|rally|move|performance)|carried the|responsible for")


def r_index_contribution(cur, q, e):
    key = e.get("index") or e.get("benchmark") or "sp500"
    b = us_benchmarks.BENCHMARKS.get(key)
    ciks, weights, as_of = us_benchmarks.members(cur, key)
    if ciks is None:
        if b.get("members"):
            raise HTTPException(503, "The %s member list is not loaded on this server right now, so I cannot break its move into "
                                     "contributions. Try again later." % b["name"])
        raise HTTPException(422, "I hold the member lists and weights of the %s, not of the %s, so I cannot break its move into "
                                 "contributions. I can compare a stock with the %s through the %s fund instead." % (
                                     " and the ".join(us_benchmarks.with_members()), b["name"], b["name"], b["etf"]))
    start, end, words = _period(e, cur)
    mem = cur.execute("""SELECT cik, ticker, name, weight_pct FROM us_index_members WHERE snapshot_id = (
                           SELECT snapshot_id FROM us_index_snapshots WHERE index_name = ? ORDER BY snapshot_id DESC LIMIT 1)""",
                      [b["members"]]).fetchall()
    to_end = us_prices_api.window_changes(cur, start, end)
    to_snap = us_prices_api.window_changes(cur, min(start, as_of), max(start, as_of)) if as_of != start else {}
    idx = us_link.companies(cur)["by_cik"]
    parts = []
    for cik, ticker, name, w in mem:
        sym = (ticker or "").replace("-", ".")
        ch = to_end.get(sym) or to_end.get(ticker)
        if not ch or w is None:
            continue
        r_end = ch[4] / 100.0
        # weight at the start of the period: the fund's weight on its holdings date,
        # scaled back by the stock's price change between the two dates
        sn = to_snap.get(sym) or to_snap.get(ticker)
        if as_of == start:
            ratio = 1.0
        elif not sn:
            continue
        elif as_of > start:
            ratio = 1.0 / (1 + sn[4] / 100.0)
        else:
            ratio = 1 + sn[4] / 100.0
        rec = idx.get(cik) or {}
        parts.append({"cik": cik, "ticker": ticker, "name": rec.get("name") or us_link.display_name(name),
                      "w_raw": w * ratio, "r": r_end, "first": ch[0], "last": ch[2]})
    tot_w = sum(p_["w_raw"] for p_ in parts)
    if not parts or not tot_w:
        raise HTTPException(404, "No member prices on file for the %s over that period." % b["name"])
    for p_ in parts:
        p_["w0"] = p_["w_raw"] / tot_w * 100
        p_["c"] = p_["w0"] * p_["r"]                  # percentage points of the index's change
    est = sum(p_["c"] for p_ in parts)
    fund = us_prices_api.window_changes(cur, start, end).get(b["etf"])
    fund_ret = fund[4] if fund else None
    parts.sort(key=lambda p_: -p_["c"])
    named = [c for c in e.get("companies") or []]
    bn = _bench_name(b)
    hits, missing = [], []
    if named:
        # every company the question names, in the order named; one not in the index is said, not skipped
        by_cik = {p_["cik"]: p_ for p_ in parts}
        missing = [c["name"] for c in named if c["cik"] not in by_cik]
        hits = [by_cik[c["cik"]] for c in named if c["cik"] in by_cik]
        if not hits:
            raise HTTPException(404, "%s is not in the %s member list held (as of %s), so it has no contribution."
                                     % (" and ".join(missing), b["name"], date_words(as_of)))
    move = "%s's %s %s" % (bn, pct(est, 1, sign=True), words)
    signed = lambda c: ("+" if c >= 0 else "\u2212") + fmt_pp(abs(c)) + " pp"
    if len(hits) == 1:
        hit = hits[0]
        share = (hit["c"] / est * 100) if abs(est) >= 0.5 and est > 0 and hit["c"] > 0 else None
        headline = "%s %s %s pp %s %s." % (short_name(hit["name"]), "added" if hit["c"] >= 0 else "took",
                                         fmt_pp(abs(hit["c"])), "to" if hit["c"] >= 0 else "from", move)
        rank = parts.index(hit) + 1
        bits = ["%s%s: a %s weight times a %s price change." % (
            ("That is %s of the gain. " % pct(share, 0)) if share is not None else "",
            "The largest contributor" if rank == 1 else "The %s largest contributor" % _ordinal(rank),
            pct(hit["w0"], 1), pct(hit["r"] * 100, 1, sign=True))]
    elif hits:
        total = sum(h["c"] for h in hits)
        share = (total / est * 100) if abs(est) >= 0.5 and est > 0 and total > 0 else None
        names = [short_name(h["name"]) for h in hits]
        headline = "%s %s %s pp %s %s." % (", ".join(names[:-1]) + " and " + names[-1], "added" if total >= 0 else "took",
                                         fmt_pp(abs(total)), "to" if total >= 0 else "from", move)
        each = sorted(hits, key=lambda h: -h["c"])
        bits = ["%s%s." % (("That is %s of the gain: " % pct(share, 0)) if share is not None else "",
                           ", ".join("%s %s" % (short_name(h["name"]), signed(h["c"])) for h in each))]
    else:
        lead = parts[0]
        headline = "%s added the most to %s: %s." % (short_name(lead["name"]), move, signed(lead["c"]))
        bits = ["The ten largest contributors made %s pp of the %s." % (fmt_pp(sum(p_["c"] for p_ in parts[:10])), pct(est, 1, sign=True))]
    if missing:
        bits.append("%s %s not in the %s." % (" and ".join(short_name(m) for m in missing), "is" if len(missing) == 1 else "are", b["name"]))
    named_ciks = {h["cik"] for h in hits}
    table = {"columns": ["Ticker", "Company", "Start weight %", "Change %", "Contribution (pp)"],
             "rows": [[p_["ticker"], p_["name"], round(p_["w0"], 3), round(p_["r"] * 100, 2), round(p_["c"], 3)]
                      for p_ in parts[:15] + [p_ for p_ in parts[15:-5] if p_["cik"] in named_ciks] + parts[-5:]]}
    show = parts[:10] + [p_ for p_ in parts[10:] if p_["cik"] in named_ciks]
    chart = {"kind": "bars", "title": "Contribution to %s's change %s, pp" % (b["name"], words), "unit": "pp",
             "series": [{"name": "pp", "points": [[p_["ticker"], round(p_["c"], 3)] for p_ in show]}]}
    return pkg("index_contribution", q, headline, " ".join(bits),
               [fig_({"label": "%s change, from members" % b["name"], "unit": "%"}, round(est, 2), _d(parts[0]["last"])),
                fig_({"label": "%s fund change" % b["etf"], "unit": "%"}, round(fund_ret, 2) if fund_ret is not None else None, _d(parts[0]["last"]))],
               table, chart, [PRICE_SOURCE, {"name": "Index members", "credit": "Holdings of the %s fund (State Street), %s." % (b["etf"], date_words(as_of))}],
               {"contribution": "start-of-period weight × price change over the period, in percentage points",
                "start_weight": "the fund's weight on its holdings date ÷ (1 + the stock's price change from the period start to that date), "
                                "rescaled to add to 100%",
                "price_change": "(last close ÷ first close in the period − 1) × 100, split-adjusted"},
               {"index": key, "since": _d(start), "companies": named},
               {},
               ["Contribution is each stock's weight at the start of the period times its price change: how much it moved the "
                "index, not why. The members' changes add up to %s; the %s fund moved %s over the same days (the gap is "
                "dividends and the fund's costs)." % (pct(est, 1, sign=True), b["etf"], pct(fund_ret, 1, sign=True) if fund_ret is not None else "\u2014"),
                "An estimate: share counts are assumed unchanged through the period, and stocks that joined or left the index "
                "during it are treated as members throughout. Price change only, before dividends."])


def _ordinal(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


# ---- recipes: ranks and lists ----------------------------------------------
METRIC_WORDS = [
    (r"price.to.book|p/b\b|\bpb ratio|book multiple|times book", "pb_x", "asc", "price to book", "×"),
    (r"price.to.sales|p/s\b|\bps ratio|sales multiple|times sales", "ps_x", "asc", "price to sales", "×"),
    (r"most expensive|highest p/?e|priciest|dearest|richest valuation", "pe_x", "desc", "P/E", "×"),
    (r"cheapest|lowest p/?e|low p/?e|price.to.earnings|p/e", "pe_x", "asc", "P/E", "×"),
    (r"operating margin|\bmargins?\b", "operating_margin_pct", "desc", "operating margin", "%"),
    (r"net margin", "net_margin_pct", "desc", "net margin", "%"),
    (r"return on equity|\broe\b", "roe_pct", "desc", "return on equity", "%"),
    (r"fastest.growing|growing fastest|revenue growth|growth", "revenue_growth_pct", "desc", "revenue growth", "%"),
    (r"buyback yield|buybacks?|repurchas", "buyback_yield_pct", "desc", "buyback yield", "%"),
    (r"dividend yield|dividends?", "dividend_yield_pct", "desc", "dividend yield", "%"),
    (r"fell most|biggest fall|worst perform|lost most|declined most|down most", "price_change_1y_pct", "asc", "one-year price change", "%"),
    (r"rose most|best perform|perform(ed|ing|s)? (the )?best|gained most|top perform|up most|biggest gain|top gainers|winners", "price_change_1y_pct", "desc", "one-year price change", "%"),
    (r"perform(ed|ing|s)? (the )?worst|worst perform|losers|biggest (loss|fall|drop)", "price_change_1y_pct", "asc", "one-year price change", "%"),
    (r"\bworst\b|\bbottom\b|laggards|underperform", "price_change_1y_pct", "asc", "one-year price change", "%"),
    (r"perform|\bbest\b|winners|gainers|outperform|\breturns?\b", "price_change_1y_pct", "desc", "one-year price change", "%"),
    (r"more profitable|most profitable|profitab", "net_margin_pct", "desc", "net margin", "%"),
    (r"lost money|made a loss|loss.making|unprofitable|lost the most|biggest loss|largest loss|negative (net )?income|in the red", "net_income", "asc", "net income", "USD"),
    (r"biggest profit|net income|profits?\b", "net_income", "desc", "net income", "USD"),
    (r"\brevenues?\b|\bsales\b|turnover", "revenue", "desc", "revenue", "USD"),
    (r"most cash|cash pile", "cash", "desc", "cash", "USD"),
    (r"free cash flow|fcf", "fcf", "desc", "free cash flow", "USD"),
    (r"market (value|cap)|largest|biggest|most valuable|valuable", "market_cap", "desc", "market value", "USD"),
]


_RANK_PHRASE = re.compile(r"\b(?:by|on|ranked by|sorted by)\s+([a-z/&\- ]+?)(?:[,.;?]|\bwith\b|\bamong\b|\babove\b|\bin\b|\bfor\b|$)")
_SUPERLATIVE = re.compile(r"\b(?:most|highest|lowest|largest|biggest|smallest|cheapest|fastest|best|worst)\s+([a-z/&\-]+(?: [a-z/&\-]+)?)")
_FILLER = {"companies", "company", "stocks", "stock", "us", "the", "a", "an", "of", "value", "valuable", "listed", "firms", "names", "ones"}


def _metric(q, strict=True):
    """The ranking figure: a 'by …' or 'on …' phrase decides first ("largest by
    market value, with P/E" ranks on market value and merely shows P/E); then
    the first metric or figure word anywhere in the question. A phrase that
    names a figure we do not know is refused, never swapped for market value."""
    ql = q.lower()
    figure_in(ql)                      # raises for figures we do not hold
    m = _RANK_PHRASE.search(ql)
    phrase = m.group(1).strip() if m else None
    high = bool(re.search(r"most expensive|highest|priciest|dearest|richest", ql))
    if phrase:
        for pat, key, order, label, unit in METRIC_WORDS:
            if re.search(pat, phrase):
                if key == "pe_x" and high and not re.search(r"cheap|lowest", ql):
                    order = "desc"                  # "most expensive by P/E"
                return key, order, label, unit
        for f in FIGURES:
            if re.search(f["words"], phrase):
                return "fig:" + f["id"], "desc", f["label"], f["unit"]
        if strict and [w for w in phrase.split() if w not in _FILLER]:
            raise HTTPException(422, "I don't hold a figure called \u201c%s\u201d. I can rank on market value, P/E, margins, growth, "
                                     "return on equity, yields, revenue, net income, cash, and lines such as debt, research spending, "
                                     "gross margin, inventories or goodwill." % phrase)
    low = bool(re.search(r"\b(worst|lowest|bottom|smallest|least|weakest)\b", ql))
    for pat, key, order, label, unit in METRIC_WORDS:
        if re.search(pat, ql):
            if low and order == "desc" and key != "price_change_1y_pct":
                order = "asc"                       # "worst margins", "lowest dividend yield"
            return key, order, label, unit
    for f in FIGURES:
        if re.search(f["words"], ql):
            return "fig:" + f["id"], ("asc" if low else "desc"), f["label"], f["unit"]
    sm = _SUPERLATIVE.search(ql)
    if strict and sm and [w for w in sm.group(1).split() if w not in _FILLER]:
        raise HTTPException(422, "I don't hold a figure called \u201c%s\u201d. I can rank on market value, P/E, margins, growth, "
                                 "return on equity, yields, revenue, net income, cash, and lines such as debt, research spending, "
                                 "gross margin, inventories or goodwill." % sm.group(1))
    return "market_cap", "desc", "market value", "USD"


def _size_floor(q, e):
    """(field, floor) from '$10bn in market value' / 'above $50bn' / 'large companies'."""
    ql = q.lower()
    amount = e.get("money")
    if amount:
        if re.search(r"revenue|sales", ql) and not re.search(r"market (value|cap)", ql):
            return "revenue", amount
        return "market_cap", amount
    if re.search(r"\blarge\b|large.cap|big companies|major", ql):
        return "market_cap", 1e10
    return None, None


def _rank(cur, q, e, rows, scope_label, recipe):
    key, order, label, unit = e.get("rank") or _metric(q)
    field, floor = _size_floor(q, e)
    n = e.get("count") or 10
    perf_floor_note = ""
    ix = e.get("index")
    if ix:
        b = us_benchmarks.BENCHMARKS[ix]
        ciks, _w, as_of = us_benchmarks.members(cur, ix)
        if ciks is None:
            if b.get("members"):
                raise HTTPException(503, "The %s member list is not loaded on this server right now, so I cannot rank within it. "
                                         "Try again later, or compare a stock with the %s through the %s fund." % (b["name"], b["name"], b["etf"]))
            raise HTTPException(422, "I hold the member lists of the %s, not of the %s, so I cannot rank within it. I can compare "
                                     "a stock with the %s through the %s fund instead." % (
                                         " and the ".join(us_benchmarks.with_members()), b["name"], b["name"], b["etf"]))
        rows = [r for r in rows if r["cik"] in ciks]
        scope_label = ("%s members" % b["name"]) if scope_label == "US-listed companies" else scope_label + " in the " + b["name"]
        e = dict(e, index_note=" %s membership is taken from the holdings of the %s fund, which tracks the index, on %s."
                               % (b["name"], b["etf"], date_words(as_of)))
    # Performance over a named window ("in 2026", "this year", "since March")
    # replaces the default one-year change: the change is from the first close
    # in the window to the last, split-adjusted, per symbol.
    window = None
    if key == "price_change_1y_pct" and e.get("since"):
        start = e["since"]
        p0 = us_prices_api.first_date(cur)
        if p0 and start < p0:
            raise HTTPException(422, "Daily prices on file begin %s, so I cannot rank performance from %s." % (date_words(p0), date_words(start)))
        changes = us_prices_api.window_changes(cur, start, e.get("until"))
        window = (start, e.get("until"))
        if e.get("until"):
            label = "price change in %d" % start.year
        elif start == datetime.date(datetime.date.today().year, 1, 1):
            label = "price change so far in %d" % start.year
        else:
            label = "price change since %s" % date_words(start)
        last_day = us_prices_api.latest_date(cur)
        e["period_span"] = (start, e.get("until") or last_day)
        def _ok(w):
            # a ticker that stopped trading in the window, or a penny stock at the start, is not ranked
            return w is not None and (not last_day or e.get("until") or (last_day - w[2]).days <= 7) and w[1] >= 1.0
        rows = [dict(r, metrics=dict(r["metrics"], price_change_1y_pct=(changes.get(r["ticker"] or "") or (None,) * 5)[4]
                                     if _ok(changes.get(r["ticker"] or "")) else None),
                     window=changes.get(r["ticker"] or "")) for r in rows]
    perf_floor_note = ""
    base_note = (" Growth is ranked only for companies whose year-before revenue was above %s, so a jump from a tiny "
                 "base does not lead the list." % money(GROWTH_BASE_USD)) if key in ("revenue_growth_pct", "profit_growth_pct") else ""
    ratio_keys = ("roe_pct", "operating_margin_pct", "net_margin_pct", "revenue_growth_pct", "profit_growth_pct", "dividend_yield_pct", "pb_x", "ps_x",
                  "buyback_yield_pct", "pe_x", "fcf_margin_pct")
    if (key in ratio_keys or key.startswith("fig:")) and not e.get("index") and not e.get("money") and not field \
            and not re.search(r"\ball\b|small|micro|penny", q.lower()):
        # a ratio across an industry: tiny filers post the wildest ratios (a $20mn retailer with 183% ROE)
        field, floor = "market_cap", 2e9
        perf_floor_note = " Companies under $2bn in market value are left out; ask for \u201call\u201d to include them."
    if key == "price_change_1y_pct" and not e.get("index") and not e.get("money") and not re.search(r"\ball\b|small|micro|penny", q.lower()):
        # Across the whole market the top of a performance list is micro-caps and
        # reverse-split leftovers; companies above $2bn are ranked unless asked.
        field, floor = "market_cap", 2e9
        perf_floor_note = " Companies under $2bn in market value are left out; ask for \u201call\u201d stocks to include them."
    figd = FIGURE_BY_ID.get(key[4:]) if key.startswith("fig:") else None
    cross = figure_cross(cur, figd) if figd else None
    kept = []
    fundish = re.compile(r"\b(FUND|TRUST|ETF|BITCOIN|ETHEREUM|ISHARES|SPDR|GRAYSCALE|PORTFOLIO|INCOME SECURITIES|PROSHARES|DIREXION|ULTRA|LEVERAGED|INVERSE|SPROTT|INVESCO DB|COMMODITY)\b", re.I)
    for r in rows:
        # a filing in another currency still has a dollar price: it ranks on market value and price
        # change, never on a figure in its own currency against others' dollars
        if r.get("unit") != "USD" and key not in ("market_cap", "price_change_1y_pct"):
            continue
        if r.get("sic") == 6770:
            continue                           # a blank-check company is a pool of cash, not a business
        if key == "price_change_1y_pct":
            if fundish.search(r["name"] or "") or (r.get("sic") in (6221, 6189, 6199)):
                continue                       # funds and commodity trusts are not stocks to rank on price
        elif key in ("buyback_yield_pct", "dividend_yield_pct", "pe_x", "cash_conversion_x", "fcf_margin_pct"):
            # a grantor trust's "repurchases" are redemptions, not buybacks; it has no revenue either
            if not (r["size"].get("revenue") or 0) > 0 or fundish.search(r["name"] or "") or (r.get("sic") in (6221, 6189, 6199)):
                continue
        if field and (r["metrics"].get(field) if field in r["metrics"] else r["size"].get(field)) is None:
            continue
        if field and (r["metrics"].get(field) if field in r["metrics"] else r["size"].get(field)) < floor:
            continue
        if figd:
            hit = cross.get(r["cik"])
            v = hit[0] if hit else None
            if v is not None and figd.get("ratio_to"):
                base = r["size"].get(figd["ratio_to"])
                v = (v / base * 100) if base else None
        elif key == "pb_x":
            mc, eq = r["metrics"].get("market_cap"), r["size"].get("equity")
            v = (mc / eq) if (mc and eq and eq > 0) else None
        elif key == "ps_x":
            mc, rv = r["metrics"].get("market_cap"), r["size"].get("revenue")
            v = (mc / rv) if (mc and rv and rv > 0) else None
        else:
            v = r["metrics"].get(key) if key in r["metrics"] else r["size"].get(key)
        if v is None:
            continue
        if key == "dividend_yield_pct" and v > 25:
            continue                            # a special or liquidating distribution, not a yield anyone will earn again
        if key == "roe_pct" and ((r["size"].get("equity") or 0) <= 0 or (r["size"].get("total_assets") and r["size"]["equity"] < 0.05 * r["size"]["total_assets"])):
            continue                            # a sliver of equity makes any profit look like a 15,000% return
        if e.get("country") and (r.get("country") or "").upper() != e["country"]:
            continue
        if e.get("sector") and us_steps_sector(r.get("sic")) != e["sector"]:
            continue
        if key in ("revenue_growth_pct", "profit_growth_pct"):
            # growth from a tiny base leads any list: the year-before revenue must be real money
            prior = r.get("prior") or {}
            if (prior.get("revenue") or 0) < GROWTH_BASE_USD or (key == "profit_growth_pct" and (prior.get("net_income") or 0) < GROWTH_BASE_USD / 5):
                continue
        kept.append((v, r))
    kept.sort(key=lambda x: x[0], reverse=(order == "desc"))
    if key == "net_income" and re.search(r"lost money|made a loss|loss.making|unprofitable|negative (net )?income|in the red|losses", q.lower()):
        losers = [kv for kv in kept if kv[0] < 0]
        if not losers:
            smallest = min(kept, key=lambda kv: kv[0]) if kept else None
            raise HTTPException(404, "None of the %d %s with a net income on file reported a loss%s." % (
                len(kept), scope_label, ("; the smallest profit was %s's %s" % (short_name(smallest[1]["name"]), money(smallest[0]))) if smallest else ""))
        kept, order = sorted(losers, key=lambda kv: kv[0]), "asc"
    top = kept[:n]
    if not top:
        raise HTTPException(404, "No %s with a %s on file%s." % (scope_label, label, (" above %s" % money(floor)) if floor else ""))
    def fmt(v):
        return money(v) if unit == "USD" else mult(v) if unit == "×" else pct(v) if unit == "%" else num(v, 0) + " " + unit
    first = top[0][1]
    if key in ("revenue_growth_pct", "profit_growth_pct") and top[0][0] > 1000:
        # growth of ten times or more is an acquisition or a changed business, not sales growth; the reader is told
        prior = (first.get("prior") or {}).get("revenue")
        base_note += (" %s's revenue a year earlier was %s: growth that large usually comes from an acquisition or a "
                      "changed business, not from selling more." % (us_link.display_name(first["name"]), money(prior)))
    lead = short_name(first["name"])
    if key == "price_change_1y_pct":
        span = label.replace("price change", "").replace("one-year", "over the past year").strip() or "over the past year"
        v0 = top[0][0]
        if (e.get("count") or 0) > 1 and len(top) > 1:
            # "top 10 performers": the headline confirms the ten were read
            headline = "%s leads the %d %s among %s %s, %s %s." % (lead, len(top), "worst performers" if order == "asc" else "best performers",
                                                                 scope_label, span, "up" if v0 >= 0 else "down", pct(abs(v0), 1))
        else:
            headline = "%s did %s among %s %s, %s %s." % (lead, "worst" if order == "asc" else "best", scope_label, span,
                                                        "up" if v0 >= 0 else "down", pct(abs(v0), 1))
    else:
        headline = "%s has the %s %s among %s: %s." % (lead, "lowest" if order == "asc" else "highest", label, scope_label, fmt(top[0][0]))
    others = ", ".join("%s %s" % ((r["ticker"] or short_name(r["name"])),
                                  pct(v, 1, sign=True) if key == "price_change_1y_pct" else fmt(v)) for v, r in top[1:5])
    floor_text = (" with %s above %s" % ("market value" if field == "market_cap" else "revenue", money(floor))) if floor else ""
    if window:
        w = first.get("window")
        answer = "%s went from %s to %s.%s" % (lead, money(w[1], 2), money(w[3], 2), (" Next: %s." % others) if others else "")
        method = "Ranked on %s: the last close against the first close in the window, adjusted for splits%s." % (label, floor_text)
    else:
        answer = ("Next: %s." % others) if others else ""
        basis_text = (": the latest close \u00d7 the shares outstanding on the cover of each company's newest 10-K or 10-Q, "
                      "each share class at its own price" if key == "market_cap" else
                      " from each company's latest annual report%s" % (" and the latest close" if key in sec_metrics.PRICE_ORDER else ""))
        method = "Ranked on %s%s%s." % (label, basis_text, floor_text)
    rank_notes = [("%s %d companies qualified.%s" % (method, len(kept), perf_floor_note + base_note + e.get("index_note", ""))).strip()]
    if key == "dividend_yield_pct":
        rank_notes.append("Yields above 25% are left out: they come from one-off special or liquidating distributions, not a payout to expect again.")
    # figures to read with care among those shown, and companies left out that may belong
    if key in ("market_cap", "dividend_yield_pct", "buyback_yield_pct", "price_change_1y_pct"):
        care = cautions_for(cur, [r["ticker"] for v, r in top])
        if care:
            rank_notes.append(care)
    if key == "market_cap" and order == "desc" and top:
        floor_shown = top[-1][0]
        held = sec_shares.institutional_value(cur)
        missing = sorted([r for r in rows if (r.get("market_value_basis") or {}).get("method") == "held back"
                          and (held.get(r["cik"]) or 0) >= floor_shown], key=lambda r: -(held.get(r["cik"]) or 0))
        if missing:
            rank_notes.append("Not ranked because their market value is held back, though funds alone report holding more "
                              "than the smallest value shown: %s. Ask about one by name to see why." % ", ".join(
                                  "%s (%s)" % (r["ticker"], short_name(r["name"])) for r in missing[:5]))
    if window:
        table = {"columns": ["Rank", "Ticker", "Company", "Industry", label.capitalize() + " %", "First close", "Last close", "Market value"],
                 "rows": [[i + 1, r["ticker"], us_link.company_name(r["cik"], r["name"], cur), r["industry"], v,
                           (r.get("window") or (None, None))[1], (r.get("window") or (None,) * 4)[3], r["metrics"].get("market_cap")]
                          for i, (v, r) in enumerate(top)], "unit": unit}
    else:
        # the fixed context columns, less the one being ranked (a market-value ranking showed "Market value" twice)
        extra = [c for c in (("Market value", "market_cap", lambda r: r["metrics"].get("market_cap")),
                             ("P/E", "pe_x", lambda r: r["metrics"].get("pe_x")),
                             ("Revenue", "revenue", lambda r: r["size"].get("revenue") if r.get("unit") == "USD" else None),
                             ("1-year change", "price_change_1y_pct", lambda r: r["metrics"].get("price_change_1y_pct")))
                 if c[1] != key]
        table = {"columns": ["Rank", "Ticker", "Company", "Industry", label.title()] + [c[0] for c in extra],
                 "rows": [[i + 1, r["ticker"], us_link.company_name(r["cik"], r["name"], cur), r["industry"], v] + [c[2](r) for c in extra]
                          for i, (v, r) in enumerate(top)], "unit": unit}
    chart = {"kind": "bars", "title": "%s, %s" % (label.title(), scope_label), "unit": unit,
             "series": [{"name": label, "points": [[r["ticker"] or r["name"][:18], v] for v, r in top]}]}
    calc = dict(sec_metrics.FORMULAS, **sec_metrics.PRICE_FORMULAS)
    if window:
        calc[key] = ("(last close in the window ÷ first close in the window − 1) × 100; the first close is divided by any "
                     "split ratio between the two dates (a day whose previous close ÷ close is a whole number)")
    if figd:
        calc[key] = "%s as filed under %s%s" % (figd["label"], " / ".join(" + ".join(alt) for alt in figd["tags"]),
                                                ("; shown as a share of " + figd["ratio_to"]) if figd.get("ratio_to") else "")
    if key == "pb_x":
        calc[key] = "market value \u00f7 shareholders' equity in the latest annual report (companies with negative equity left out)"
    if key == "ps_x":
        calc[key] = "market value \u00f7 revenue in the latest annual report"
    return pkg(recipe, q, headline, answer, [fig("Companies qualifying", len(kept), "count", None)], table, chart,
               [SEC_SOURCE, PRICE_SOURCE, COMPANIES_SOURCE], {key: calc.get(key, "as filed")},
               {"metric": key, "order": order, "floor": {"field": field, "value": floor} if field else None, "count": n},
               {"screener": "/api/v1/us/financials/screener?sort=%s&order=%s" % (key, order)},
               rank_notes + ["Companies missing the ranking figure are left out and counted in the API's excluded_missing_sort."],
               period=period_span(*e["period_span"]) if window and e.get("period_span") else None)


def us_steps_sector(sic):
    from . import us_steps
    return us_steps.sector_for(sic)


COUNTRY_WORDS = [(r"\bchinese\b|\bchina\b", "CN", "Chinese"), (r"\bisraeli?\b", "IL", "Israeli"), (r"\bcanadian\b|\bcanada\b", "CA", "Canadian"),
                 (r"\bbritish\b|\buk\b", "GB", "British"), (r"\bindian?\b", "IN", "Indian"), (r"\bbrazil(?:ian)?\b", "BR", "Brazilian"),
                 (r"\bmexican\b|\bmexico\b", "MX", "Mexican"), (r"\bgerman(?:y)?\b", "DE", "German"), (r"\bfrench\b|\bfrance\b", "FR", "French"),
                 (r"\bswiss\b", "CH", "Swiss"), (r"\bdutch\b", "NL", "Dutch"), (r"\birish\b|\bireland\b", "IE", "Irish"),
                 (r"\bsingapore(?:an)?\b", "SG", "Singaporean"), (r"\bkorean?\b", "KR", "Korean"), (r"\btaiwan(?:ese)?\b", "TW", "Taiwanese"),
                 (r"\baustralian?\b", "AU", "Australian"), (r"\bargentin\w*", "AR", "Argentine")]
SECTOR_WORDS = [(r"\btech(?:nology)?\b", "Technology"), (r"health ?care|\bhealth\b", "Health Care"), (r"\bfinancials?\b", "Financials"),
                (r"real estate", "Real Estate"), (r"\benergy\b", "Energy"), (r"\butilities\b", "Utilities"), (r"\bmaterials\b", "Materials"),
                (r"\bindustrials?\b", "Industrials"), (r"consumer discretionary", "Consumer Discretionary"), (r"consumer staples|\bstaples\b", "Consumer Staples"),
                (r"communication(?: services)?", "Communication")]


def country_sector(q):
    """(country code, country word, sector) named in the question, each or None."""
    ql = (q or "").lower()
    cc = next(((code, word) for pat, code, word in COUNTRY_WORDS if re.search(pat, ql)), (None, None))
    sec = next((name for pat, name in SECTOR_WORDS if re.search(pat, ql)), None)
    return cc[0], cc[1], sec


def r_industry_rank(cur, q, e):
    codes, label = e["industry"]
    rows = [r for r in metric_rows(cur) if r.get("sic") is not None and any(str(int(r["sic"])).startswith(c) for c in codes)]
    out = _rank(cur, q, e, rows, label, "industry_rank")
    out["entities"]["industry"] = {"codes": codes, "label": label}
    return out


def r_market_rank(cur, q, e):
    code, word, sector = country_sector(q)
    e = dict(e, country=code, sector=sector)
    label = "US-listed companies"
    if word:
        label = "US-listed %s companies" % word
    if sector:
        label = "%s %s stocks" % (label.replace(" companies", ""), sector.lower()) if word else "%s sector stocks" % sector
    out = _rank(cur, q, e, metric_rows(cur), label, "market_rank")
    if word or sector:
        out.setdefault("notes", []).append("%s%s" % (("Country is the business address as filed with the SEC. " if word else ""),
                                                      ("Sectors are approximate, grouped from the SEC's industry codes." if sector else "")))
    return out


def r_index_stat(cur, q, e):
    """One statistic (median or mean) of a figure across an index's members."""
    import statistics
    ix = e.get("index") or "sp500"
    b = us_benchmarks.BENCHMARKS[ix]
    ciks, _w, as_of = us_benchmarks.members(cur, ix)
    if ciks is None:
        raise HTTPException(422, "I hold the member lists of the %s, not of the %s." % (" and the ".join(us_benchmarks.with_members()), b["name"]))
    key, order, label, unit = _metric(e.get("stat_words") or q, strict=False)
    rows = [r for r in metric_rows(cur) if r["cik"] in ciks]
    vals = []
    for r in rows:
        if key == "pb_x":
            mc, eq = r["metrics"].get("market_cap"), r["size"].get("equity")
            v = (mc / eq) if (mc and eq and eq > 0) else None
        elif key == "ps_x":
            mc, rv = r["metrics"].get("market_cap"), r["size"].get("revenue")
            v = (mc / rv) if (mc and rv and rv > 0) else None
        else:
            v = r["metrics"].get(key) if key in r["metrics"] else r["size"].get(key)
        if v is not None and not (key == "roe_pct" and (r["size"].get("equity") or 0) <= 0):
            vals.append(v)
    if not vals:
        raise HTTPException(404, "No %s on file for %s members." % (label, b["name"]))
    med, mean = statistics.median(vals), sum(vals) / len(vals)
    def fmt(v):
        return money(v) if unit == "USD" else mult(v) if unit == "\u00d7" else pct(v) if unit == "%" else num(v, 0)
    stat = (e.get("stat") or "median").lower()
    lead_v, lead_w, other_v, other_w = (mean, "mean", med, "median") if stat == "mean" else (med, "median", mean, "mean")
    headline = "The %s %s of %s members is %s (%s %s), across %d of %d members with the figure." % (
        lead_w, label, b["name"], fmt(lead_v), other_w, fmt(other_v), len(vals), len(rows))
    bits = ["The median is the middle company; the mean is pulled by the extremes, so the two differ."] if abs(mean - med) > 0.1 * abs(med or 1) else []
    return pkg("index_stat", q, headline, " ".join(bits), [fig("Median", med, unit, None), fig("Mean", mean, unit, None), fig("Members with the figure", len(vals), "count", None)],
               None, None, [SEC_SOURCE, PRICE_SOURCE], {key: dict(sec_metrics.FORMULAS, **sec_metrics.PRICE_FORMULAS).get(key, "as filed")},
               {"index": ix, "metric": key, "stat": stat}, {},
               ["%s membership is taken from the holdings of the %s fund on %s." % (b["name"], b["etf"], date_words(as_of))])


def r_index_member(cur, q, e):
    """Whether a company is in an index, and its weight."""
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    ix = e.get("index") or "sp500"
    b = us_benchmarks.BENCHMARKS[ix]
    ciks, weights, as_of = us_benchmarks.members(cur, ix)
    if ciks is None:
        raise HTTPException(422, "I hold the member lists of the %s, not of the %s, so I cannot say." % (" and the ".join(us_benchmarks.with_members()), b["name"]))
    w = (weights or {}).get(rec["cik"])
    if rec["cik"] in ciks:
        headline = "%s is in the %s%s." % (rec["name"], b["name"], (", with a weight of %s" % pct(w, 2)) if w is not None else "")
    else:
        headline = "%s is not in the %s, going by the %s fund's holdings on %s." % (rec["name"], b["name"], b["etf"], date_words(as_of))
    rank = None
    if w is not None and weights:
        rank = 1 + sum(1 for v in weights.values() if v is not None and v > w)
    answer = ("That makes it the %s largest weight of %d members." % (_ordinal(rank), len(weights))) if rank else ""
    return pkg("index_member", q, headline, answer, [fig("Weight", w, "%", as_of)], None, None,
               [{"name": "Index members", "credit": "Holdings of the %s fund (State Street), %s." % (b["etf"], date_words(as_of))}],
               {"weight": "the fund's holding weight on its holdings date; membership is read from the fund that tracks the index"},
               {"company": rec, "index": ix}, {"company": company_link(rec)})


def r_company_splits(cur, q, e):
    """Share splits read from the daily price history (a day whose previous close divided by close is a whole ratio)."""
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    sym = (rec.get("ticker") or "").replace("-", ".")
    evs = us_prices_api.split_events(cur).get(sym) or us_prices_api.split_events(cur).get(rec.get("ticker") or "") or []
    p0 = us_prices_api.first_date(cur)
    since = date_words(p0) if p0 else "the start of the price history"
    if not evs:
        headline = "No share split by %s shows in the daily prices on file (since %s)." % (rec["name"], since)
        answer = "A split is read from the prices: a day whose previous close divided by its close is a whole ratio. Splits before the price history began are not held."
        return pkg("company_splits", q, headline, answer, [], None, None, [PRICE_SOURCE], {"split": us_prices_api.SPLIT_RULE}, {"company": rec}, {"company": company_link(rec)})
    words = []
    for d, r in evs:
        words.append("%s on %s" % (("%d-for-1" % round(r)) if r >= 1 and abs(r - round(r)) < 0.05 else ("1-for-%d reverse" % round(1 / r)) if r < 1 else "%g-for-1" % r, date_words(d)))
    headline = "%s last split its shares %s%s." % (rec["name"], words[-1], (" and before that %s" % ", ".join(words[:-1])) if len(evs) > 1 else "")
    answer = "Read from the daily prices on file since %s: a day whose previous close divided by its close is a whole ratio. Earlier splits are not held." % since
    table = {"columns": ["Date", "Ratio"], "rows": [[_d(d), r] for d, r in evs]}
    return pkg("company_splits", q, headline, answer, [fig("Splits on file", len(evs), "count", None)], table, None, [PRICE_SOURCE],
               {"split": us_prices_api.SPLIT_RULE}, {"company": rec, "splits": [[_d(d), r] for d, r in evs]}, {"company": company_link(rec)})


def r_share_classes(cur, q, e):
    """Two listed classes of one company side by side (GOOG and GOOGL)."""
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    classes = e.get("classes") or []
    start, end, words = _period(e, cur)
    parts, rows_ = [], []
    for t in classes:
        s = us_prices_api.clean_closes(cur, t, start, end)[0]
        if not s:
            continue
        ch = (s[-1][2] / s[0][2] - 1) * 100 if s[0][2] else None
        parts.append((t, s[-1][1], s[-1][0], ch))
        rows_.append([t, s[-1][1], s[-1][0], round(ch, 2) if ch is not None else None])
    if len(parts) < 2:
        raise HTTPException(404, "Prices for both classes are not on file.")
    a, b_ = parts[0], parts[1]
    gap = (a[1] / b_[1] - 1) * 100 if b_[1] else None
    headline = "%s closed at %s and %s at %s on %s (%s apart); %s %s and %s %s %s." % (
        a[0], money(a[1], 2), b_[0], money(b_[1], 2), date_words(a[2]), pct(abs(gap), 1) if gap is not None else "\u2014", a[0], pct(a[3], 1, sign=True), b_[0], pct(b_[3], 1, sign=True), words)
    table = {"columns": ["Ticker", "Close", "Date", "Change %"], "rows": rows_}
    return pkg("share_classes", q, headline, "Both are classes of %s; they differ in voting rights, not in the business." % rec["name"],
               [fig("%s close" % a[0], a[1], "USD", a[2]), fig("%s close" % b_[0], b_[1], "USD", b_[2])], table, None, [PRICE_SOURCE],
               {"change_pct": "(last close \u00f7 first close in the window \u2212 1) \u00d7 100, split-adjusted"}, {"company": rec, "classes": classes}, {"company": company_link(rec)})


def r_insider_buying_market(cur, q, e):
    since = e.get("since") or datetime.date.today().replace(day=1)
    days = max(1, (datetime.date.today() - since).days + 1)
    floor = e.get("money") or 0
    # summed per company over the whole window, a jointly filed trade once
    d = call_api(sec_insiders_api.recent_companies, code="P", min_value=floor, days=days, limit=200)
    ranked, tot = d["companies"], d["totals"]
    above = (" above %s" % money(floor)) if floor else ""
    api = "/api/v1/us/insiders/recent/companies?code=P&min_value=%d&days=%d" % (floor, days)
    if not ranked:
        headline = "No open-market insider purchases%s %s in the loaded filings." % (above, _since_words(since))
        return pkg("insider_buying_market", q, headline, "", [], None, None, [INSIDER_SOURCE], {}, {"since": _d(since), "floor": floor}, {},
                   ["A Form 4 is due within two business days of the trade; the loaded quarters are listed under /api/v1/us/insiders/summary."])
    name = lambda r: us_link.display_name(r["issuer_name"])
    for r in ranked:   # some filings write "N/A" or "NONE" for an unlisted issuer's ticker
        if (r["issuer_symbol"] or "").strip().upper() in ("", "N/A", "NA", "NONE"):
            r["issuer_symbol"] = None
    top = ranked[0]
    headline = "Insiders bought the most at %s %s: %s of shares." % (name(top), _since_words(since), money(top["value"]))
    answer = ("In all, %s insiders bought %s%s at %s companies. Next after %s: %s." % (
        num(tot["insiders"]), money(tot["value"]), above, num(tot["companies"]), short_name(top["issuer_name"]),
        _people(["%s %s" % (short_name(r["issuer_name"]), money(r["value"])) for r in ranked[1:4]]))) if len(ranked) > 1 else \
        "No other company had an open-market insider purchase%s." % above
    table = {"columns": ["Company", "Ticker", "Bought (USD)", "Insiders", "Trades", "Latest trade"],
             "rows": [[name(r), r["issuer_symbol"] or None, r["value"], r["insiders"], r["trades"], r["latest_trade"]] for r in ranked]}
    chart = {"kind": "bars", "title": "Open-market insider purchases %s, by company" % _since_words(since), "unit": "USD",
             "series": [{"name": "Value bought", "points": [[r["issuer_symbol"] or short_name(r["issuer_name"])[:16], r["value"]] for r in ranked[:12]]}]}
    out = pkg("insider_buying_market", q, headline, answer,
              [fig("Bought", tot["value"], "USD", None, "shares × reported price"), fig("Companies", tot["companies"], "count", None),
               fig("Insiders", tot["insiders"], "count", None)],
              table, chart, [INSIDER_SOURCE], {"value": "shares × the price the filer reported"}, {"since": _d(since), "floor": floor},
              {"api": api},
              ["Only open-market purchases (Form 4 code P) count. Share awards and option exercises are left out.",
               "A trade filed jointly by several owners (a fund and its managers) counts once. A line priced above $1mn a share or worth over $20bn is left out as a typing error."] +
              (["The table lists the 200 companies with the most bought; the totals count all %s." % num(tot["companies"])] if tot["companies"] > len(ranked) else []),
              period={"text": "%s to today" % date_words(since)})
    return out


def r_calendar_window(cur, q, e):
    days = e.get("ahead_days") or 14
    day = us_link.parse_weekday(q)
    if day:
        days = max(1, (day - datetime.date.today()).days + 1)
    ex = ""
    for name in ("nasdaq", "nyse", "otc", "cboe"):
        if name in q.lower():
            ex = name
    rep = "annual" if re.search(r"annual|10-k", q.lower()) else ("quarterly" if re.search(r"quarter|10-q", q.lower()) else "")
    d = call_api(sec_calendar_api.calendar, days=days, exchange=ex, report=rep, limit=2000)
    rows = [r for r in d["rows"] if r["status"] != "filed"]
    if day:
        rows = [r for r in rows if str(r["expected_date"])[:10] == day.isoformat()]
        d = dict(d, start=day.isoformat(), end=day.isoformat())
    big = sorted([r for r in rows if r.get("ticker")], key=lambda r: ((row_for(cur, r["cik"]) or {}).get("metrics", {}).get("market_cap") or 0), reverse=True)
    if day:
        headline = "%d companies%s are expected to file a 10-K or 10-Q on %s %s." % (len(rows), (" on %s" % ex.upper()) if ex else "", day.strftime("%A"), date_words(day))
    else:
        headline = "%d companies%s are expected to file a 10-K or 10-Q between %s and %s." % (len(rows), (" on %s" % ex.upper()) if ex else "", date_words(d["start"]), date_words(d["end"]))
    answer = "Largest by market value: %s. Each date is last year's filing of the same report moved forward a year; it is not an announced date and not the earnings-release date." % (
        ", ".join("%s (%s)" % (r["ticker"], date_words(r["expected_date"])) for r in big[:8]) or "none with a price on file")
    table = {"columns": ["Expected", "Ticker", "Company", "Report", "Fiscal period", "Exchange", "Based on filing of"],
             "rows": [[r["expected_date"], r["ticker"], r["name"], r["report"], r["fiscal_period"], r["exchange"], r["basis"]["filed"]] for r in rows[:300]]}
    return pkg("calendar_window", q, headline, answer, [fig("Expected filings", len(rows), "count", None)], table, None, [SEC_SOURCE], sec_calendar_api.CALC,
               {"days": days, "exchange": ex or None, "report": rep or None}, {"api": "/api/v1/us/earnings/calendar?days=%d" % days})


def r_holders_increase_industry(cur, q, e):
    codes, label = e["industry"]
    cmap = us_link._build_cusips_memo(cur)["by_cusip"]
    by_cik = us_link._build_cusips_memo(cur)["by_cik"]
    idx = us_link.companies(cur)["by_cik"]
    # one security per company: its most-held CUSIP (the common stock), so a
    # preferred or warrant class never stands in for the company
    wanted = [lst[0]["cusip"] for cik, lst in by_cik.items() if lst and (idx.get(cik) or {}).get("sic") is not None
              and any(str(int(idx[cik]["sic"])).startswith(p) for p in codes)]
    if not wanted:
        raise HTTPException(404, "No 13F positions could be matched to %s." % label)
    batches = cur.execute("SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 2").fetchall()
    if len(batches) < 2:
        raise HTTPException(404, "Only one 13F window is loaded, so there is no quarter-on-quarter change yet.")
    latest, prior = batches[0][0], batches[1][0]
    rows = cur.execute("""
        WITH lr AS (SELECT manager_cik, batch, max(report_period) AS rp FROM sec_13f_filings WHERE NOT amendment AND batch IN (?, ?) GROUP BY manager_cik, batch),
        lines AS (
            SELECT h.batch, h.cusip, h.value, h.shares, f.manager_cik
            FROM sec_13f_holdings h JOIN sec_13f_filings f USING (accession)
            JOIN lr ON lr.manager_cik = f.manager_cik AND lr.batch = h.batch AND lr.rp = f.report_period
            WHERE h.batch IN (?, ?) AND NOT f.amendment AND h.put_call IS NULL AND h.cusip IN (%s)),
        med AS (SELECT batch, cusip, median(value / shares) AS px FROM lines WHERE shares > 0 AND value > 0 GROUP BY batch, cusip),
        pos AS (
            -- share counts whose implied price is wildly off the crowd's are units slips and are set aside
            SELECT l.batch, l.cusip,
                   sum(CASE WHEN l.shares > 0 AND l.value > 0 AND (l.value / l.shares) / m.px BETWEEN 1.0/3 AND 3 THEN l.shares END) AS shares,
                   sum(l.value) AS value, count(DISTINCT l.manager_cik) AS holders
            FROM lines l JOIN med m USING (batch, cusip)
            GROUP BY l.batch, l.cusip)
        SELECT c.cusip, c.shares, c.value, c.holders, p.shares, p.value, p.holders
        FROM pos c LEFT JOIN pos p ON p.cusip = c.cusip AND p.batch = ?
        WHERE c.batch = ?""" % ",".join("?" * len(wanted)), [latest, prior, latest, prior] + wanted + [prior, latest]).fetchall()
    out = []
    for cusip, sh, val, hold, psh, pval, phold in rows:
        rec = cmap[cusip]
        co = idx.get(rec["cik"]) or {}
        chg = (sh or 0) - (psh or 0) if psh is not None else None
        out.append({"ticker": co.get("ticker"), "name": us_link.company_name(rec["cik"], co.get("sec_name") or co.get("name"), cur), "cusip": cusip, "shares": sh, "prior_shares": psh,
                    "change_shares": chg, "change_pct": (chg / psh * 100) if (psh and chg is not None) else None,
                    "value": val, "holders": hold, "prior_holders": phold})
    out = [o for o in out if o["change_shares"] is not None]
    out.sort(key=lambda o: -(o["change_pct"] or -1e9))
    if not out:
        raise HTTPException(404, "No %s position appears in both loaded 13F windows." % label)
    top = out[0]
    headline = "Among %s, 13F holders added most to %s: %s more shares (%s) quarter on quarter." % (label, top["name"], num(top["change_shares"]), pct(top["change_pct"], 1, sign=True))
    answer = "Shares held by all reporting managers, latest window against the previous. Next: %s. Cuts: %s." % (
        "; ".join("%s %s" % (o["ticker"] or o["name"], pct(o["change_pct"], 1, sign=True)) for o in out[1:5]),
        "; ".join("%s %s" % (o["ticker"] or o["name"], pct(o["change_pct"], 1, sign=True)) for o in sorted(out, key=lambda o: o["change_pct"])[:3]))
    table = {"columns": ["Ticker", "Company", "Shares held", "Prior quarter", "Change", "Change %", "Holders", "Reported value"],
             "rows": [[o["ticker"], o["name"], o["shares"], o["prior_shares"], o["change_shares"], o["change_pct"], o["holders"], o["value"]] for o in out[:50]], "unit": "USD"}
    chart = {"kind": "bars", "title": "Change in 13F-held shares, %s" % label, "unit": "%",
             "series": [{"name": "Change %", "points": [[o["ticker"] or o["name"][:16], o["change_pct"]] for o in out[:12]]}]}
    return pkg("holders_increase_industry", q, headline, answer, [fig("Companies compared", len(out), "count", None)], table, chart, [F13_SOURCE, COMPANIES_SOURCE],
               {"change_pct": "(shares held in the latest window ÷ shares held in the previous − 1) × 100, summed over all reporting managers; "
                              "a line whose value ÷ shares is more than three times off the median implied price is left out of the share count"},
               {"industry": {"codes": codes, "label": label}}, {})


# ---- recipes: macro meets markets ------------------------------------------
def r_banks_vs_yield(cur, q, e):
    since = e.get("since") or datetime.date(2018, 1, 1)
    banks = cur.execute("""
        SELECT c.ticker, c.cik, year(f.period_end) AS y, arg_max(f.value, f.filed) AS value
        FROM sec_cf_facts f JOIN sec_cf_companies c USING (cik)
        JOIN (SELECT cik, arg_max(sic, filed) AS sic FROM sec_filings GROUP BY cik) s USING (cik)
        WHERE s.sic BETWEEN 6020 AND 6029 AND f.tag = 'NetIncomeLoss' AND f.fp = 'FY' AND f.unit = 'USD'
          AND f.form IN ('10-K', '10-K/A') AND f.period_end - f.period_start BETWEEN 360 AND 371
        GROUP BY c.ticker, c.cik, year(f.period_end)""").fetchall()
    if not banks:
        raise HTTPException(404, "No bank in the deep-history set.")
    tickers = sorted({b[0] for b in banks})
    by_year = {}
    for t, cik, y, v in banks:
        if y >= since.year:
            by_year.setdefault(int(y), {})[t] = v
    years = sorted(y for y in by_year if len(by_year[y]) == len(tickers))
    raw = call_api(macro_api.observations, dataset="ust-yields", series="10Y", measure="index", start=since.isoformat())
    pts = []
    for s in raw.get("series", []):
        for p in (s.get("observations") or s.get("points") or []):
            d, v = (p["period"], p["value"]) if isinstance(p, dict) else (p[0], p[1])
            if v is not None:
                pts.append((d[:4], v))
    yavg = {}
    for y, v in pts:
        yavg.setdefault(y, []).append(v)
    yavg = {int(y): sum(v) / len(v) for y, v in yavg.items()}
    rows = [[y, sum(by_year[y].values()), yavg.get(y)] for y in years]
    if not rows:
        raise HTTPException(404, "No overlapping years.")
    first, last = rows[0], rows[-1]
    headline = "Net income at %s went from %s in %d to %s in %d while the 10-year yield averaged %s and %s." % (
        " and ".join(tickers), money(first[1]), first[0], money(last[1]), last[0], pct(first[2], 2), pct(last[2], 2))
    answer = ("Bank profits are the sum of annual net income as each bank filed it (SEC company facts, calendar-year frames) for the banks in the deep-history set: %s. "
              "The yield is the calendar-year average of daily 10-year constant-maturity closes. Two series side by side; no cause is claimed." % ", ".join(tickers))
    table = {"columns": ["Year", "Net income, %s" % " + ".join(tickers), "10-year yield, annual average %"], "rows": rows, "unit": "USD"}
    chart = {"kind": "line", "title": "Bank net income and the 10-year yield", "unit": "USD | %",
             "series": [{"name": "Net income (USD)", "points": [[str(r[0]), r[1]] for r in rows], "axis": "left"},
                        {"name": "10-year yield (%)", "points": [[str(r[0]), r[2]] for r in rows], "axis": "right"}]}
    return pkg("banks_vs_yield", q, headline, answer, [], table, chart, [SEC_SOURCE, UST_SOURCE],
               {"yield_annual_avg": "mean of daily 10-year constant-maturity yields within the calendar year"},
               {"banks": tickers, "since": since.year}, {},
               ["Only banks in the 57-company deep-history set are included; the full-market statements shelf holds the latest quarters only."])


# ---- the grid --------------------------------------------------------------
_VS_WORDS = (r"outperform|underperform|\bbeat(en|s)?\b|\blag(ged|ging|s)?\b|\bbehind\b|ahead of|\bvs\.? (the )?(s&p|dow|nasdaq|market|index|benchmark|russell)|"
             r"against the (s&p|dow|nasdaq|market|index|benchmark|russell)|relative to|compared? (with|to) the|benchmark|than the (s&p|dow|nasdaq|market|index|russell)")
RECIPES = [
    {"id": "index_contribution", "label": "What drove an index", "needs": ["contribution"],
     "patterns": [_CONTRIB_WORDS.pattern], "run": r_index_contribution, "weight": 7},
    {"id": "vs_benchmark", "label": "A stock against a benchmark", "needs": ["company", "benchmark"],
     "patterns": [_VS_WORDS], "run": r_vs_benchmark, "weight": 7},
    {"id": "company_history", "label": "A company figure over the years", "needs": ["company", "history"],
     "patterns": [_HIST_WORDS.pattern], "run": r_company_history, "weight": 6},
    {"id": "compare_companies", "label": "Compare two companies", "needs": ["two_companies"],
     "patterns": [r"compar", r"\bvs\.?\b", r"versus", r"\bthan\b", r"\bor\b", r"\band\b", r"which is (bigger|larger|more|cheaper|better)"], "run": r_compare_companies, "weight": 2},
    {"id": "company_figure", "label": "One figure from a company's filing", "needs": ["company", "figure"],
     "patterns": [r"how much", r"how big", r"what (is|are|was|were)", r"\bdebt\b", r"spend", r"research", r"inventor", r"goodwill", r"gross", r"deposits", r"tax", r"receivable", r"expense"], "run": r_company_figure, "weight": 3},
    {"id": "managers_holding_both", "label": "Managers holding two stocks", "needs": ["two_companies"],
     "patterns": [r"\bboth\b", r"hold(s|ers)? .* and", r"own(s)? .* and"], "run": r_managers_holding_both, "weight": 3},
    {"id": "company_insiders", "label": "Insider trades at a company", "needs": ["company"],
     "patterns": [r"insider", r"10b5", r"executives? (sold|bought|buy|sell)", r"directors? (sold|bought)", r"form 4", r"(buy|bought|sell|sold|selling|buying) (stock|shares)", r"anyone at",
                  r"\b(sold|sell|bought|buy|buying|selling|dumped|unloaded)\b.*\b(shares|stock)\b", r"\b(has|have|did)\b.*\b(sold|bought)\b"], "run": r_company_insiders, "weight": 3},
    {"id": "company_holders", "label": "Who owns a company", "needs": ["company"],
     "patterns": [r"holders?", r"who owns", r"who holds", r"shareholders", r"institution", r"13f", r"added last quarter", r"owners", r"\bstake\b",
                  r"which (funds|managers|investors)", r"funds? (increased|added|bought|raised|cut|sold|trimmed)"], "run": r_company_holders, "weight": 3},
    {"id": "company_as_filed_history", "label": "As-filed history", "needs": ["company"],
     "patterns": [r"as filed", r"at the time", r"each of the last", r"which filing", r"annual reports", r"restat", r"originally reported"], "run": r_company_as_filed_history, "weight": 3},
    {"id": "company_calendar", "label": "When a company reports", "needs": ["company"],
     "patterns": [r"report next", r"next report", r"when (does|will|did) .* (report|file)", r"last file", r"filing date", r"earnings date", r"reports? (on|in) "], "run": r_company_calendar, "weight": 3},
    {"id": "company_shareholder_return", "label": "Buybacks and dividends", "needs": ["company"],
     "patterns": [r"buyback", r"repurchas", r"dividend", r"return(ed)? to shareholders", r"yield", r"payout"], "run": r_company_shareholder_return, "weight": 2},
    {"id": "company_price_performance", "label": "Share price performance", "needs": ["company"],
     "patterns": [r"stock (done|perform|price|fallen|risen)", r"share price", r"performance", r"(chart|graph|plot).*(stock|shares|share price)|(stock|share) (chart|price chart)", r"\bfell\b", r"\brose\b", r"up or down", r"over the past (year|month|week)", r"ytd", r"year to date", r"how has .* done", r"how did .* do\b", r"since 20\d\d",
                  r"52.?week", r"all.time (high|low)", r"record high"], "run": r_company_price_performance, "weight": 2},
    {"id": "company_risks", "label": "A company's risk factors", "needs": ["company"],
     "patterns": [r"\brisks?\b", r"risk factors", r"what could go wrong", r"\bthreats?\b", r"\bworr(y|ies|ied)\b", r"headwinds"],
     "run": r_company_risks, "weight": 4},
    {"id": "company_says", "label": "What a company's annual report says", "needs": ["company"],
     "patterns": [r"\b(say|says|said|mention|mentions|discuss|discusses|disclose|discloses)\b", r"lawsuits?|litigation|legal (cases|proceedings)|\bsued\b|antitrust",
                  r"\bcyber", r"\b10-?k\b", r"annual report", r"\b(competitors|rivals|compete with|suppliers)\b", r"who (are|is) .* (competitors|customers|suppliers)",
                  r"\bheadquarter|head office|where is .* (based|located|headquartered)", r"\bproperties\b|\bfacilities\b|\boffices\b|\bfactories\b"],
     "run": r_company_says, "weight": 4},
    {"id": "company_business", "label": "What a company does", "needs": ["company"],
     "patterns": [r"what does .* do\b", r"what (does|do) .* (sell|make|produce|offer)\b", r"what (business|kind of (company|business))",
                  r"\bbusiness (mix|lines?|model)\b", r"lines? of business", r"(make|makes|earn|earns|get|gets) (its|their|most of its) money",
                  r"where does .* (money|revenue|sales) come from", r"revenue (mix|split|breakdown)", r"\bproduct lines?\b",
                  r"tell me (everything |more |all )?about", r"overview of", r"what is .* known for", r"who (is|are) .* customers"], "run": r_company_business, "weight": 4},
    {"id": "company_growth_margin", "label": "Growth and margins", "needs": ["company"],
     "patterns": [r"grow", r"margin", r"profitab", r"revenue", r"sales", r"earnings\b(?! per)", r"net income", r"return on equity", r"\broe\b"], "run": r_company_growth_margin, "weight": 2},
    {"id": "company_value", "label": "What a company is worth", "needs": ["company"],
     "patterns": [r"\bworth\b", r"market (value|cap)", r"valu(e|ation)", r"trades? on", r"p/?e\b", r"price.to.earnings", r"how big", r"multiple"], "run": r_company_value, "weight": 2},
    {"id": "company_employees", "label": "Employees", "needs": ["company"],
     "patterns": [r"employees|headcount|staff\b|workforce|workers|people work|how many people"], "run": r_company_employees, "weight": 7},
    {"id": "company_quarters", "label": "Quarterly results", "needs": ["company"],
     "patterns": [r"\bquarter(ly|s)?\b", r"\bq[1-4]\b"], "run": r_company_quarters, "weight": 6},
    {"id": "index_stat", "label": "A statistic across an index", "needs": ["no_company"],
     "patterns": [r"\b(average|median|mean|typical)\b.*\b(s&p|dow|sp500|spx|djia)\b"], "run": r_index_stat, "weight": 6},
    {"id": "index_member", "label": "Index membership", "needs": ["company"],
     "patterns": [r"\b(in|part of|member of|included in|weight in|weighting in) the (s&p|dow|nasdaq)"], "run": r_index_member, "weight": 6},
    {"id": "company_splits", "label": "Share splits", "needs": ["company"],
     "patterns": [r"\bsplits?\b"], "run": r_company_splits, "weight": 6},
    {"id": "share_classes", "label": "Share classes", "needs": ["company"],
     "patterns": [r"class [abc] (vs|versus|against|or) class [abc]"], "run": r_share_classes, "weight": 6},
    {"id": "company_segment", "label": "One line of a company's revenue", "needs": ["company"],
     "patterns": [_SEGMENT_Q.pattern], "run": r_company_segment, "weight": 5},
    {"id": "ceo_pay", "label": "CEO pay", "needs": [],
     "patterns": [r"\bceos?\b.*\bpa(y|id)\b", r"\bpa(y|id)\b.*\bceos?\b", r"(?<!stock.based )(?<!share.based )compensation", r"\bsalar(y|ies)\b", r"(highest|best|top|lowest|worst).paid",
                  r"how much (was|is|did) .* (paid|earn|make)\b", r"\bceo\b", r"chief executive", r"who runs"], "run": r_ceo_pay, "weight": 5},
    {"id": "manager_portfolio", "label": "A manager's portfolio", "needs": ["manager"],
     "patterns": [r"what does .* own", r"portfolio", r"positions", r"added or dropped", r"add(ed)? or drop", r"holdings", r"\bown\b", r"\bowns\b", r"bought and sold"], "run": r_manager_portfolio, "weight": 3},
    {"id": "holders_increase_industry", "label": "13F changes in an industry", "needs": ["industry", "no_company"],
     "patterns": [r"13f", r"position increases", r"institutions? (bought|added|increased)", r"managers (added|bought)", r"holders added"], "run": r_holders_increase_industry, "weight": 3},
    {"id": "industry_rank", "label": "Rank an industry", "needs": ["industry", "no_company"],
     "patterns": [r"rank", r"cheapest", r"highest", r"lowest", r"\bmost\b", r"\bbest\b", r"\btop\b", r"largest", r"biggest", r"which", r"\bby\b", r"worst",
                  r"lost money|loss.making|unprofitable|made a loss"], "run": r_industry_rank, "weight": 1},
    {"id": "insider_buying_market", "label": "Where insiders are buying", "needs": ["no_company"],
     "patterns": [r"insiders? (are )?buying", r"open.market", r"insider purchases", r"where are insiders", r"insiders? bought"], "run": r_insider_buying_market, "weight": 3},
    {"id": "calendar_window", "label": "Who reports next", "needs": ["no_company"],
     "patterns": [r"report(ing)? (in|over|during) the next", r"reporting next", r"which companies report", r"earnings (this|next) week", r"next (two|2|three) weeks", r"report next week", r"who reports",
                  r"earnings calendar", r"reporting calendar", r"results calendar", r"who is reporting", r"report(s|ing)? (tomorrow|today|this week|this month)"], "run": r_calendar_window, "weight": 5},
    {"id": "banks_vs_yield", "label": "Bank profits and the 10-year yield", "needs": [],
     "patterns": [r"bank profits", r"banks?.*(10|ten).year", r"yield.*banks?", r"against the (10|ten).year", r"bank earnings .* rates"], "run": r_banks_vs_yield, "weight": 4},
    {"id": "market_rank", "label": "Rank the market", "needs": ["no_company", "no_industry"],
     "patterns": [r"largest", r"biggest", r"\btop\b", r"cheapest", r"highest", r"lowest", r"fell most", r"worst", r"best perform", r"rose most", r"yield", r"\bmost\b", r"\bten\b",
                  r"perform(ed|ing|s)? (the )?(best|worst)", r"winners|losers|top gainers", r"\bwhich (stock|company|companies|stocks)\b",
                  r"lost money|loss.making|unprofitable|made a loss|that lost"], "run": r_market_rank, "weight": 1},
]
BY_ID = {r["id"]: r for r in RECIPES}

_UNANSWERABLE = [
    (r"\bwhy\b", "I don't explain why a price or a figure moved; I show what the published data says. Ask what happened instead: how the stock has done, what the company reported, who bought or sold."),
    (r"should (i|we|you|one) (buy|sell|invest|hold|own)|is it a (good )?buy|is .* a (good |bad |great |safe )?(buy|investment|stock to buy|stock to own|bet)\b|buy or sell|worth (buying|investing|owning|holding)|recommend|"
     r"good (time )?to (buy|sell|invest)|buy now|sell now|undervalued|overvalued|is .* cheap\b|is .* expensive\b|bullish|bearish|long or short",
     "I don't give investment advice. I can show the figures people weigh: valuation, growth, who is buying, what insiders are doing."),
    (r"will .* (earn|report|make)|forecast|estimate|consensus|guidance|target price|price target", "I hold no analyst estimates or forecasts, only what companies have filed and where their shares have traded."),
]


def _without_benchmarks(question):
    """The question with index names blanked, so "Dow Jones" does not find
    Jones Lang LaSalle and "S&P" does not find a company called S&P."""
    q = question or ""
    for key, b in us_benchmarks.BENCHMARKS.items():
        if key == "gold":
            continue                                  # Barrick Gold is a company
        q = re.sub(b["words"], " ", q, flags=re.I)
    return q


def entities(cur, question):
    cos = us_link.find_companies(cur, _without_benchmarks(question))
    mgr = us_link.resolve_manager(cur, question)
    # "Berkshire" names both a listed company and a manager; the question's verbs decide below
    try:
        figd = figure_in(question)
    except HTTPException:
        figd = None               # a figure we do not hold: the shapes that need one will say so
    hist = None
    if _HIST_WORDS.search((question or "").lower()) and not re.search(r"\bstock\b|\bshares\b|share price", (question or "").lower()):
        try:
            hist = history_figure(question)
        except HTTPException:
            hist = None
    return {"companies": cos, "manager": mgr, "industry": us_link.resolve_industry(cur, question),
            "money": us_link.parse_money(question), "since": us_link.parse_since(question),
            "count": us_link.parse_count(question), "ahead_days": us_link.parse_ahead_days(question),
            "figure": figd, "history": hist, "years": us_link.parse_years(question),
            "index": us_link.parse_index(question), "until": us_link.parse_until(question),
            "benchmark": us_benchmarks.find(question) or (
                "sp500" if re.search(r"the (market|index|benchmark)\b", (question or "").lower()) else None),
            "contribution": bool(_CONTRIB_WORDS.search((question or "").lower()))}


def match(cur, question):
    """(recipe, entities, score, alternatives). The highest pattern score among
    recipes whose needs the question satisfies; a company mention with no
    other clue falls to the valuation shape."""
    q = (question or "").strip()
    ql = q.lower()
    for pat, msg in _UNANSWERABLE:
        if re.search(pat, ql):
            raise HTTPException(422, msg)
    e = entities(cur, q)
    # "Who owns X" asks for X's holders even when X also files a 13F of its
    # own (Nvidia does); a manager reading needs manager words and no
    # holder words.
    holder_words = re.search(r"who (owns|holds)|holders|shareholders|who are the (biggest|largest)", ql)
    manager_words = (us_link._MANAGER_ASKS.search(ql) or re.search(r"what does .* own|what .* owns?\b", ql)) and not holder_words
    scored = []
    for r in RECIPES:
        needs = r["needs"]
        if "company" in needs and not e["companies"]:
            continue
        if "two_companies" in needs and len(e["companies"]) < 2:
            continue
        if "manager" in needs and not (e["manager"] and manager_words):
            continue
        if "industry" in needs and not e["industry"]:
            continue
        if "figure" in needs and not e["figure"]:
            continue
        if "history" in needs and not e["history"]:
            continue
        if "benchmark" in needs and not e.get("benchmark"):
            continue
        if "contribution" in needs and not e.get("contribution"):
            continue
        if "no_company" in needs and e["companies"] and not (r["id"] == "insider_buying_market"):
            continue
        if "no_industry" in needs and e["industry"]:
            continue
        hits = sum(1 for p in r["patterns"] if re.search(p, ql))
        if hits == 0:
            continue
        score = hits * r["weight"]
        if "manager" in needs:
            score += 2
        if r["id"] == "managers_holding_both":
            score += 4
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    # Two companies named: a one-company shape would answer about the first
    # and drop the second, which is answering a different question. Compare.
    if len(e["companies"]) >= 2 and scored and "company" in scored[0][1]["needs"] and not any(
            r["id"] in ("managers_holding_both", "compare_companies", "company_history", "vs_benchmark", "index_contribution")
            for _, r in scored[:1]):
        return BY_ID["compare_companies"], e, scored[0][0], [{"recipe": scored[0][1]["id"], "label": scored[0][1]["label"]}]
    if not scored:
        figure_in(q)                                   # a figure we do not hold says so here
        if e["companies"] and e.get("figure"):
            return BY_ID["company_figure"], e, 0, []
        # No shape word at all: the model may read it (answer() tries that on a
        # 422); only if it cannot does a bare company name mean "what is it worth".
        raise HTTPException(422, NO_MATCH_TEXT if not e["companies"] else _BARE_COMPANY)
    best = scored[0][1]
    alts = [{"recipe": r["id"], "label": r["label"]} for s, r in scored[1:3]]
    return best, e, scored[0][0], alts


_BARE_COMPANY = "bare-company"
NO_MATCH_TEXT = ("I couldn't match that to a question I can answer. Try a company (a ticker like AAPL works), "
                 "an industry, a manager, or a list such as 'largest US companies by market value'.")


SHAPE_HINTS = {
    "index_contribution": "how much of an index's change came from each stock (or one named stock); the index in universe",
    "vs_benchmark": "did one or more named stocks beat or lag an index or fund over a period",
    "company_history": "one company's (or two companies') filed figure year by year: the latest revenue, net income, cash, free cash flow or EPS ('Apple revenue'), one named year ('Apple revenue in 2019', 'fiscal 2023'), a span ('2015 vs 2025', 'CAGR 2015-2025'), or 5 years of a figure; also charts of a figure",
    "compare_companies": "two to four companies side by side on one named figure (free cash flow, cash, growth, margins, P/E, market value) or on all of them",
    "company_figure": "one filed figure from a company's latest annual report (debt, research spending, gross margin, capex, deposits ...)",
    "managers_holding_both": "which 13F managers hold both of two named stocks",
    "company_insiders": "insider buying and selling at one company",
    "company_holders": "who owns a company: the largest 13F holders and who added",
    "company_as_filed_history": "a figure as each annual report first filed it, with the filing",
    "company_calendar": "when a company reports next and last filed",
    "company_shareholder_return": "a company's buybacks and dividends and the yield",
    "company_price_performance": "how one company's share price has done over a period, a finished year ('how did Intel do in 2024'), or its 52-week or all-time high or low",
    "company_risks": "the risks a company lists in its annual report (its risk factors), or its risks from one thing ('risks to Apple from tariffs')",
    "company_says": "what a company's annual report says about a subject ('what does Apple say about tariffs', 'who are Nvidia's competitors'), its lawsuits and legal cases, cybersecurity, or where its offices and plants are",
    "company_business": "what a company does or sells, its business, what it makes its money from, its whole revenue mix by product line, segment and region ('what does Nvidia do', 'tell me about Costco'); a question about ONE named segment, product or region, or its share ('what share of Apple's revenue is services', 'how much does Nvidia make in China'), is steps",
    "company_growth_margin": "a company's revenue growth and margins",
    "company_value": "what a company is worth: market value, P/E, yields",
    "manager_portfolio": "what a 13F manager owns, added and dropped; what it sold or bought last quarter",
    "ceo_pay": "what a CEO was paid, by company ('Apple CEO pay') or by name ('Jensen Huang pay'), or the highest-paid S&P 500 CEOs; from the latest proxy statements",
    "company_segment": "one named line of a company's revenue from its latest annual report: a product line, segment or region, and its share ('Apple services revenue', 'Amazon AWS revenue', 'Nvidia data center revenue as a share of total')",
    "index_stat": "the median or mean of one figure across an index's members ('average P/E of the Dow', 'median market cap of the S&P 500')",
    "company_quarters": "one company's quarterly revenue, net income, operating income or EPS: the latest quarter, a named quarter ('Apple Q3 2025 revenue'), last quarter against a year earlier, or every quarter on file (the last eight)",
    "company_employees": "how many people one company employs, read from the text of its annual report ('how many employees does Microsoft have')",
    "index_member": "whether a company is in the S&P 500 or the Dow, and its weight",
    "company_splits": "a company's share splits read from the price history",
    "share_classes": "two listed share classes of one company side by side (GOOG and GOOGL)",
    "holders_increase_industry": "13F position increases in an industry",
    "industry_rank": "rank companies in an industry by a figure",
    "insider_buying_market": "where insiders are buying across the market",
    "calendar_window": "which companies report in the coming days",
    "banks_vs_yield": "bank profits against the 10-year Treasury yield",
    "market_rank": "rank all US-listed stocks, or an index's members (universe), by a figure, e.g. best performers in the S&P 500",
}


def rank_keys():
    """rank_by words the model may use -> (key, default order, label, unit)."""
    out = {}
    for _pat, key, order, label, unit in METRIC_WORDS:
        name = "price_change" if key == "price_change_1y_pct" else key
        if name not in out:
            out[name] = (key, "desc" if key != "pe_x" else "asc", label, unit)
    out["price_change"] = ("price_change_1y_pct", "desc", "one-year price change", "%")
    for f in FIGURES:
        out.setdefault(f["id"], ("fig:" + f["id"], "desc", f["label"], f["unit"]))
    return out


def shapes_text():
    """The shapes, for the model that reads questions."""
    return "\n".join(["- %s: %s" % (r["id"], SHAPE_HINTS.get(r["id"], r["label"])) for r in RECIPES] +
                     ["- steps: anything the shapes above do not fully answer: how many, what share, averages, "
                      "sectors, lists filtered on several figures; and every question about quarterly results, CEO pay, "
                      "business segments or regions, or counts and rankings over insider trades or fund holdings across "
                      "companies; worked out step by step on tables of every stock"])


def reader_prompt():
    from . import us_ask_model
    return us_ask_model.prompt(shapes_text(), list(rank_keys()), list(us_benchmarks.UNIVERSES), list(us_benchmarks.ORDER))


def _date_or_none(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _int_or_none(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


class FormError(Exception):
    """The model's form names something the data cannot place."""


def entities_from_form(cur, question, form):
    """The entities a shape runs on, from the model's form. Every name is
    looked up in our own data; a company the question does not mention is
    dropped (the model must not add one); anything unplaceable is a
    FormError, and the word patterns get their turn."""
    shape = form.get("shape")
    if shape not in BY_ID:
        raise FormError("unknown shape %r" % shape)
    ql = (question or "").lower()
    idx = us_link.companies(cur)
    cos = []
    for raw in (form.get("companies") or [])[:6]:
        if isinstance(raw, dict):
            written, ticker = str(raw.get("as_written") or "").strip(), str(raw.get("ticker") or "").strip().upper()
        else:
            written, ticker = str(raw or "").strip(), ""
        # the words must be the question's own: the model may not add a company
        if not written or written.lower() not in ql:
            if not (ticker and re.search(r"\b%s\b" % re.escape(ticker.lower()), ql)):
                continue
        rec = None
        for t in (ticker, ticker.replace(".", "-"), written.upper() if len(written) <= 6 else ""):
            if t and t in idx["by_ticker"]:
                rec = idx["by_ticker"][t]
                break
        if rec is None:
            hit = us_link.find_companies(cur, written.lower(), limit=1) or us_link.find_companies(cur, written, limit=1)
            rec = hit[0] if hit else None
        if rec is None:
            raise FormError("company %r not found" % (written or ticker))
        if all(c["cik"] != rec["cik"] for c in cos):
            cos.append(dict(rec, matched_by="reader"))
    e = {"companies": cos, "manager": None, "industry": None, "money": None, "since": None, "until": None,
         "count": _int_or_none(form.get("count"), 1, 100), "ahead_days": us_link.parse_ahead_days(question),
         "figure": None, "history": None, "years": _int_or_none(form.get("years"), 1, 30),
         "index": None, "benchmark": None, "contribution": shape == "index_contribution", "rank": None}
    if form.get("industry"):
        e["industry"] = us_link.resolve_industry(cur, str(form["industry"]))
        if not e["industry"]:
            raise FormError("industry %r not placed" % form["industry"])
    if form.get("manager"):
        e["manager"] = us_link.resolve_manager(cur, str(form["manager"]))
        if not e["manager"]:
            raise FormError("manager %r not found" % form["manager"])
    if form.get("figure"):
        try:
            e["figure"] = figure_in(str(form["figure"]))
        except HTTPException:
            e["figure"] = None
        if shape == "company_history":
            try:
                e["history"] = history_figure(str(form["figure"]))
            except HTTPException:
                e["history"] = None
    e["since"] = _date_or_none(form.get("from"))
    e["until"] = _date_or_none(form.get("to"))
    if e["until"] and e["until"] >= datetime.date.today():
        e["until"] = None
    if form.get("min_value") not in (None, ""):
        try:
            e["money"] = float(form["min_value"])
        except (TypeError, ValueError):
            pass
    # an index counts only when the question names it: "US companies" is the
    # whole market, not the total-market fund's (unheld) member list
    u = form.get("universe")
    if u in us_benchmarks.UNIVERSES and re.search(us_benchmarks.BENCHMARKS[u]["words"], ql):
        e["index"] = u
    if form.get("benchmark") in us_benchmarks.BENCHMARKS:
        e["benchmark"] = form["benchmark"]
    rk = rank_keys()
    if form.get("rank_by") in rk:
        key, order, label, unit = rk[form["rank_by"]]
        if form.get("order") in ("asc", "desc"):
            order = form["order"]
        e["rank"] = (key, order, label, unit)
    # the shape's needs, checked as the pattern matcher checks them
    needs = BY_ID[shape]["needs"]
    if "company" in needs and not cos:
        raise FormError("no company for %s" % shape)
    if "two_companies" in needs and len(cos) < 2:
        raise FormError("two companies needed")
    if "industry" in needs and not e["industry"]:
        raise FormError("industry needed")
    if "manager" in needs and not e["manager"]:
        raise FormError("manager needed")
    if "figure" in needs and not e["figure"]:
        raise FormError("figure needed")
    if "history" in needs and not e["history"]:
        raise FormError("history figure needed")
    if "benchmark" in needs and not e["benchmark"]:
        e["benchmark"] = e["index"] or "sp500"
    if shape == "index_contribution" and not e["index"]:
        e["index"] = e["benchmark"] or "sp500"
    if shape in ("market_rank", "industry_rank") and cos:
        raise FormError("a ranking with a company named")
    return e


def run_explicit(cur, recipe_id, blanks):
    """Run one shape with its blanks given as words: the route the API's
    /run endpoint and the model's reading both use. Unknown names are 404."""
    r = BY_ID.get(recipe_id)
    if r is None:
        raise HTTPException(404, "no such recipe; see /api/v1/us/ask/recipes")
    b = {k: (v if v is not None else "") for k, v in (blanks or {}).items()}
    text = " ".join(str(x) for x in (b.get("figure") or b.get("metric") or "", b.get("company") or "", b.get("company_2") or "",
                                     b.get("industry") or "", b.get("manager") or "") if x)
    e = {"companies": [], "manager": None, "industry": None, "money": None, "since": None,
         "count": int(b.get("count") or 10), "ahead_days": int(b.get("days") or 14), "figure": None,
         "history": None, "years": int(b.get("years") or 0) or None}
    for c in (b.get("company"), b.get("company_2")):
        c = str(c or "").strip()
        if c:
            found = us_link.find_companies(cur, c.upper() if len(c) <= 6 else c, limit=1)
            if not found:
                raise HTTPException(404, "I could not find a company called %r." % c)
            e["companies"].append(found[0])
    if str(b.get("industry") or "").strip():
        e["industry"] = us_link.resolve_industry(cur, str(b["industry"]))
        if not e["industry"]:
            raise HTTPException(404, "I could not place the industry %r." % b["industry"])
    if str(b.get("manager") or "").strip():
        e["manager"] = us_link.resolve_manager(cur, str(b["manager"]))
        if not e["manager"]:
            raise HTTPException(404, "I could not find a 13F manager called %r." % b["manager"])
    if str(b.get("since") or "").strip():
        try:
            e["since"] = datetime.date.fromisoformat(str(b["since"]).strip()[:10])
        except ValueError:
            raise HTTPException(400, "since must be YYYY-MM-DD")
    if b.get("min_value") not in (None, ""):
        try:
            e["money"] = float(b["min_value"])
        except (TypeError, ValueError):
            raise HTTPException(400, "min_value must be a number")
    fig_words = str(b.get("figure") or "")
    if fig_words:
        e["figure"] = figure_in(fig_words)
        if recipe_id == "company_history":
            e["history"] = history_figure(fig_words)
    out = r["run"](cur, text or r["label"], e)
    return out


_REFUSALS = {"why": _UNANSWERABLE[0][1], "advice": _UNANSWERABLE[1][1], "forecast": _UNANSWERABLE[2][1],
             "fragment": ("That reads as a follow-up to an earlier question, and none came with it, so I would only be "
                          "guessing what it follows. Ask the whole question, e.g. \u201cwhat is Microsoft worth\u201d."),
             "unclear": None}
# what each refusal is, for the reader-facing reply (app/ask_miss.py)
_REFUSAL_KIND = {"why": "policy", "advice": "policy", "forecast": "policy", "fragment": "unclear", "unclear": "unclear"}


# a message that is plainly not a question, answered "not understood" before
# anything runs (no model call); "hello" and "test" otherwise find companies
# named Hello Group and Aehr Test Systems
_NOT_A_QUESTION = re.compile(
    r"(hi|hello|hey|hiya|yo|test(ing)?( \d+)?|thanks?( you)?|thx|ok(ay)?|help|start|menu|ping|\?+|"
    r"[^a-z0-9]*|(asdf|qwer|zxcv|jkl)\w*( \w+)?)", re.I)     # never letters alone: "GM", "F" and "GE" are tickers


# ---- the quick lane ----------------------------------------------------------
# The plainest questions ("Apple revenue", "who owns Nvidia", "AAPL", "Tesla
# vs GM margins") are read by a short grammar and answered by code with no
# model call: a company (or several) plus a few words that name one shape.
# Anything the grammar does not match whole goes to the reader as before.
_Q_YEAR = r"(?:19|20)\d{2}"
_Q_WHEN = (r"(?: ?(?:last year|last fiscal year|latest|annual|this year|in %(y)s|for %(y)s|%(y)s|fy ?\d{2,4}|fiscal(?: year)? %(y)s|"
           r"%(y)s ?(?:-|–|to|vs\.?|versus|and) ?%(y)s|(?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten) years ago|history|over time|"
           r"since %(y)s|(?:over the |for the )?(?:last|past) (?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten) years|trend|"
           r"growth|growth since %(y)s|cagr|cagr %(y)s ?- ?%(y)s|by year|each year|every year|chart|graph))?" % {"y": _Q_YEAR})
_Q_LEAD = (r"(?:(?:what|whats|what's|what is|what was|what are|how much|how much is|how much was|how big is|show|show me|give me|tell me|latest|the|"
           r"please|can you|could you|would you|i want|i'd like|i need|find|look up|lookup|get|get me|pull|pull up|check) )*")
_CORE_FIG = (r"(?:revenues?|sales|turnover|top line|net income|net profit|net earnings|profits?|earnings|bottom line|operating (?:income|profit)|"
             r"free cash flow|fcf|operating cash flow|cash from operations|cash|cash pile|cash balance|capex|capital (?:expenditure|spending)|"
             r"total assets|assets|(?:shareholders'? |stockholders'? )?equity|book value|eps|earnings per share|diluted eps|buybacks?|share repurchases|"
             r"repurchases|dividends? paid|gross margin|operating margin|net margin|profit margin|margins?|pre.?tax income|total liabilities)")
QUICK_SHAPES = [
    (r"(?:vs\.?|versus|or|and|compare|compared|compared with|against|compare with)?", "compare_companies", 2),
    (r"", "company_value", 1),
    (r"(?:is|was|are) (?:it |they )?(?:profitable|making money|loss.making|in the red|in profit|making a profit|making a loss|losing money)(?: last year| this year| in %(y)s)?|"
     r"(?:did|does|do) (?:it |they )?(?:lose money|make money|make a profit|make a loss|turn a profit)(?: last year| this year| in %(y)s)?" % {"y": _Q_YEAR}, "company_growth_margin", 1),
    (r"how much (?:cash|money|revenue|debt|profit|sales) (?:does|do|did) (?:it |they )?(?:have|hold|make|earn|owe|generate|bring in)(?: on hand| on its balance sheet| in a year| a year| last year| annually| per year)?", "company_history", 1),
    (r"(?:who (?:is|runs|leads|heads) (?:its |the )?(?:ceo|chief executive|boss|chief)|who is (?:its |the )?ceo|who (?:runs|leads|heads)|name of (?:its |the )?ceo|ceo name|ceo)", "ceo_pay", 1),
    (r"(?:stock )?splits?(?: history)?|when did (?:it |they )?(?:last )?split(?: (?:its|their) (?:stock|shares))?|split history|has (?:it |they )?(?:ever )?split|last (?:stock )?split|share splits?", "company_splits", 1),
    (_Q_LEAD + r"(?:worth|market cap|market value|market capitali[sz]ation|valuation|value|valued at)(?: today| now)?", "company_value", 1),
    (_Q_LEAD + r"(?:class [abc] )?(?:the )?(?:p/e|pe|p/e ratio|pe ratio|price to earnings(?: ratio)?|price.to.sales|p/s|price to book|p/b|enterprise value|ev|net debt|net cash|"
     r"shares outstanding|share count|number of shares|eps|earnings per share|dividend yield|multiple|earnings multiple|book value per share|bvps|tangible book value(?: per share)?)(?: today| now)?", "company_value", 1),
    (_Q_LEAD + r"(?:sec )?(?:cik|cik number|central index key|sic(?: code)?|industry code|ticker|ticker symbol|symbol|stock symbol|exchange|stock exchange|sector|industry|fiscal year(?: end)?|financial year end|year end)(?: (?:for|of))?", "company_value", 1),
    (r"(?:(?:vs\.?|versus|or|and|against|compared (?:with|to)) ?)*(?:ytd|year to date|this year|performance|stock performance|share price performance|share price|stock price|price|returns?|"
     r"since %(y)s|over the (?:past|last) (?:year|month|\d+ years|five years|ten years)|in %(y)s|last year|which did better|which performed better|who did better)(?: (?:vs\.?|versus|or|and|against))?(?: (?:ytd|this year|since %(y)s|in %(y)s))?" % {"y": _Q_YEAR}, "compare_companies", 2),
    (_Q_LEAD + r"(?:share price|stock price|stock|shares|price|price chart|stock chart|chart|performance|stock performance|share price performance|"
     r"ytd|year to date|how has (?:the )?stock done|how has it done|how did (?:the )?stock do|how is (?:the )?stock doing|returns?)"
     r"(?: (?:today|ytd|year to date|this year|in %(y)s|since %(y)s|over the (?:past|last) (?:year|month|week|\d+ years|five years|ten years)|"
     r"(?:last|past) (?:year|month|week|\d+ years|five years|ten years)))?" % {"y": _Q_YEAR}, "company_price_performance", 1),
    (_Q_LEAD + r"(?:52.?week|52w|one.year|all.time|record) (?:high|low)|" + _Q_LEAD + r"(?:highest|lowest) (?:close|price)(?: ever)?|peak", "company_price_performance", 1),
    (r"(?:who owns|who holds|owners|holders|shareholders|largest holders|biggest holders|top holders|institutional holders|13f holders|ownership|"
     r"who are the (?:largest|biggest) (?:holders|shareholders|owners)(?: of)?|who owns the most)", "company_holders", 1),
    (r"(?:when does|when will|when did|when is|when are)? ?(?:report|reports|reporting|report earnings|reports earnings|next earnings|earnings date|next report|report next|"
     r"file|next filing|earnings|results|next results|earnings release|report next earnings|due to report|expected to report)(?: next| due)?", "company_calendar", 1),
    (r"(?:insider|insiders|insider trades|insider trading|insider buying|insider selling|insider activity|insider transactions|insider sales|insider purchases|"
     r"any insider buying|any insider selling|are insiders buying|are insiders selling|insiders buying|insiders selling|form 4s?)(?: at| in)?(?: this year| this month| recently)?", "company_insiders", 1),
    (r"(?:what (?:does|do) (?:it |they )?(?:do|sell|make|produce)|business|business model|what it does|what they do|tell me (?:everything |more |all )?about|about|describe|overview(?: of)?|"
     r"revenue (?:mix|split|breakdown)|revenue by (?:segment|region|product|country)|segments|business lines|product lines|what (?:is|are) (?:its|their) (?:segments|business lines|products))", "company_business", 1),
    (r"(?:which|what|who among) (?:funds|institutions|managers|investors|holders|hedge funds|institutional investors) (?:increased|added|bought|raised|own|hold|sold|cut|trimmed|reduced|dumped|exited|dropped)(?: their| to| out of)?(?: stake| position| shares| holdings?)?(?: last quarter| this quarter| recently)?", "company_holders", 1),
    (r"(?!.*\b(?:funds?|institutions?|managers?|hedge)\b)(?:has|have|did|is|are)? ?(?:[a-z]+ ){0,3}(?:sold|sell|selling|bought|buy|buying|dumped|unloaded)(?: (?:any|its|his|her|their|more|some))? ?(?:shares|stock)?(?: this year| this month| recently| lately| in %(y)s| since %(y)s)?" % {"y": _Q_YEAR}, "company_insiders", 1),
    (r"how (?:did|has|is) (?:it |the stock |the shares |the share price )?(?:do|done|doing|perform|performed|performing|fare|fared)(?: in %(y)s| this year| ytd| last year| since %(y)s| over the (?:past|last) (?:year|month|\d+ years))?" % {"y": _Q_YEAR}, "company_price_performance", 1),
    (r"(?:risks?|risk factors|key risks|biggest risks|main risks|top risks|what could go wrong|headwinds|how many risks? (?:does|do) (?:it |they )?(?:list|have|disclose)|how many risk factors(?: (?:does|do) (?:it |they )?(?:list|have|disclose))?)", "company_risks", 1),
    (r"(?:how many (?:employees|people|staff|workers) (?:does|do) (?:it |they )?(?:have|employ)|(?:number of |how many )?(?:employees|headcount|staff|workforce|workers)(?: count| size)?)", "company_employees", 1),
    (r"(?:what (?:does|do) (?:it |they )?say )?(?:on|about|regarding|concerning) [a-z][a-z0-9 &'.-]{1,50}", "company_says", 1),
    (r"(?:who are |what are |its |their |the )?(?:largest|biggest|main|key|top|major)? ?(?:customers|clients|suppliers|competitors|rivals|competition|markets served)", "company_says", 1),
    (r"(?:[a-z]+ ){1,3}(?:ceo )?(?:pay|paid|compensation|salary|package|pay package)(?: in %(y)s| last year| %(y)s)?(?: (?:vs\.?|versus|and|against) (?:performance|tsr|shareholder returns?|the stock))?" % {"y": _Q_YEAR}, "ceo_pay", 1),
    (r"(?:who|which (?:insiders|executives|directors|officers))\s+(?:sold|bought|is selling|is buying|has sold|has bought)(?: shares| stock)?(?: in| during)?(?: the)?(?: last quarter| this quarter| last month| this month| this year| last year| recently| lately| in %(y)s)?" % {"y": _Q_YEAR}, "company_insiders", 1),
    (r"(?:what (?:was|is) (?:the |its )?)?(?:share price|stock price|price|close|closing price)(?: on| at| as of)? (?:\d{1,2}(?:st|nd|rd|th)? [a-z]{3,9}\.?,? (?:19|20)\d{2}|[a-z]{3,9}\.? \d{1,2}(?:st|nd|rd|th)?,? (?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2}|last (?:monday|tuesday|wednesday|thursday|friday)|yesterday)", "company_price_performance", 1),
    (r"(?:how much |what |what is |whats |what was )?(?:revenue (?:from|of|in) )?(?:[a-z][a-z0-9&'.-]*(?: [a-z][a-z0-9&'.-]*){0,3}) (?:revenue|sales)(?: history| as a share of (?:total|revenue|sales)| share| mix| last year| in %(y)s| %(y)s)?" % {"y": _Q_YEAR}, "company_segment", 1),
    (r"(?!(?:give|show|tell|what|how|please|can|could|find|get|the|its|latest|annual)\b)(?:[a-z][a-z0-9&'.-]*(?: [a-z][a-z0-9&'.-]*){0,3}) (?:loss|losses|operating (?:income|loss|profit)|segment profit|segment margin)", "company_segment", 1),
    (r"(?:ceo |chief executive |boss )?(?:pay|paid|compensation|salary|package|pay package|how much (?:is|was|does) (?:the )?(?:ceo|chief executive|he|she) (?:paid|make|earn))(?: in %(y)s| last year| %(y)s)?(?: (?:vs\.?|versus|and|against) (?:performance|tsr|shareholder returns?|the stock))?" % {"y": _Q_YEAR}, "ceo_pay", 1),
    (r"(?:dividend|dividends|buyback|buybacks|share buybacks|shareholder returns?|capital return|payout|payout ratio|dividend payout(?: ratio)?|dividends and buybacks|buybacks and dividends|buyback yield|cash returned(?: to shareholders)?)", "company_shareholder_return", 1),
    (r"(?:does|do|is|has|will)? ?(?:it |they )?(?:pay|pays|have|has|offer|offers|earn|earns|generate|generates) (?:a |the )?(?:higher|lower|bigger|better|larger|more|less|smaller|faster|stronger) (?:dividend|yield|dividend yield|margin|margins|growth|revenue|sales|profit|profits|cash|debt|free cash flow|return on equity|roe)(?: than)?", "compare_companies", 2),
    (r"(?:does|do|will|did)? ?(?:it |they )?(?:report|reports|file|files|announce)(?: earnings| results)? (?:before|after|earlier than|later than|first|ahead of)(?: or after)?", "company_calendar", 2),
    (r"(?:class )?[abc] shares? (?:price|close)|(?:class )?[abc] (?:share|stock) price|since (?:its |the )?(?:ipo|listing|inception|beginning|start)|all.?time|whole history|entire history", "company_price_performance", 1),
    (r"(?:year(?:ly)? (?:high|low)|annual (?:high|low)|(?:highest|lowest) (?:price|close|point|level)(?: this year| ever| of the year| so far this year)?|"
     r"how far (?:is|are)?(?: it| the stock| the shares)? ?(?:below|off|from|under) (?:its |the |their )?(?:high|peak|record|top|all.?time high)|drawdown(?: from (?:its |the )?(?:peak|high))?|"
     r"(?:below|off|from) (?:its |the )?(?:high|peak|record|all.?time high))", "company_price_performance", 1),
    (_Q_LEAD + r"(?:" + "|".join(f["words"] for f in FIGURES) + r")[a-z]*(?: of (?:revenue|sales))?(?: last year| latest| in %(y)s| for %(y)s| %(y)s)?" % {"y": _Q_YEAR}, "company_figure", 1),
    (_Q_LEAD + _CORE_FIG + _Q_WHEN + r"(?: (?:peak|record|peak year|record year|highest|lowest|best year|worst year|trough))?", "company_history", 1),
    (_Q_LEAD + _CORE_FIG + r" (?:peak|record|peak year|record year|highest|lowest|best year|worst year|trough)(?: year)?", "company_history", 1),
    (r"(?:vs\.?|versus|or|and|compare|compared|compared with|against|compare with)? ?" + _Q_LEAD + _CORE_FIG + r"(?: (?:vs\.?|versus|or|and|compared|compare))?(?: last year| latest)?", "compare_companies", 2),
    (r"(?:who|which) (?:has|is|grows|grew|makes|earns|pays|generates) (?:more|the most|bigger|larger|faster|higher|lower|better|cheaper)?(?: \w+){0,3}", "compare_companies", 2),
    (r"(?:is|was)? ?(?:bigger|larger|cheaper|more expensive|more profitable|more valuable|growing faster|worth more)(?: than)?", "compare_companies", 2),
]
_QUICK = [(re.compile("^(?:" + pat + r")\??$"), BY_ID_NAME, n) for pat, BY_ID_NAME, n in QUICK_SHAPES]


_MANAGER_NAME = re.compile(r"\b((?:[A-Z][\w&.'-]*\s+){0,3}[A-Z][\w&.'-]*)\s+(Asset Management|Capital Management|Capital Partners|Capital|Partners|Advisors|Advisers|"
                           r"Investment Management|Investments|Management|Associates|Fund Management|Funds?|Holdings)\b")


def _unknown_manager(cur, question):
    """A sentence when the question names an investment manager by name and asks
    about its portfolio, and no 13F by that name is on file; None otherwise."""
    q = question or ""
    if not us_link._MANAGER_ASKS.search(q.lower()) or not _MANAGER_NAME.search(q):
        return None
    try:
        if us_link.resolve_manager(cur, q) or us_link.find_companies(cur, _without_benchmarks(q)):
            return None
    except Exception:                                             # noqa: BLE001
        return None
    m = _MANAGER_NAME.search(q)
    name = "%s %s" % (m.group(1).strip(), m.group(2))
    return ("No Form 13F by %s is on file. Managers file 13F only while they run more than $100mn of US-listed stock; one that "
            "has deregistered or fallen below that stops appearing." % name)


def _strip_companies(text, cos):
    """The question with every mention of the companies found taken out."""
    from . import us_link as _ul
    t = re.sub(r"(\w)['\u2019]s\b", r"\1", text or "")
    pats = []
    for c in cos:
        for tk in [c.get("ticker") or ""] + list(c.get("tickers") or []):
            if tk:
                pats += [r"\$?" + re.escape(tk), r"\$?" + re.escape(tk.replace("-", "."))]
        for alias, tk in _ul.ALIASES.items():
            if tk == (c.get("ticker") or "") or tk.replace("-", ".") == (c.get("ticker") or "").replace("-", "."):
                pats.append(re.escape(alias))
        for nm in (c.get("name") or "", c.get("sec_name") or "", short_name(c.get("name") or ""), _ul.display_name(c.get("sec_name") or "")):
            if nm:
                pats.append(re.escape(nm))
        for w in _ul.norm_name(c.get("name") or "").split():
            if len(w) >= 4 and w not in _ul._GENERIC_NAMES and w not in _ul._ENGLISH:
                pats.append(re.escape(w))
    for p in sorted(set(pats), key=len, reverse=True):
        t = re.sub(r"(?<![\w&])" + p + r"(?![\w&])", " ", t, flags=re.I)
    t = re.sub(r"(?:^|\s)(?:inc|corp|co|plc|ltd|the|company|the company|group|firm|parent|parent company|its parent)\b\.?", " ", t, flags=re.I)
    t = re.sub(r"[,;:!\"\u201c\u201d\u2018\u2019]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().strip(".").strip().lower()


_Q_MANAGER = re.compile(r"^(?:(?:what|who|which|how much|how many shares) (?:did|does|has|is|are|was|of)|whats|what's|show|show me|does|do|did)? ?(?:it |they |its |their |the )?(?:sell|sold|selling|buy|bought|buying|own|owns|hold|holds|holding|holdings|portfolio|positions|"
                        r"stake(?: in)?|position(?: in)?|holding(?: in| of)?|exposure(?: to)?|shares(?: of| in)?|still own|still hold|"
                        r"top holdings|largest holdings?|biggest holdings?|largest positions?|biggest positions?|top positions|added|dropped|trimmed|new positions|13f|"
                        r"largest holding|biggest holding|top holding|what is in (?:its|the) portfolio)(?: last quarter| this quarter| recently| in q[1-4])?\??$")


QUICK_LANE = True            # off in tests that exercise the reader


def quick_match(cur, question):
    """(recipe, entities) when the question is a company and a few words
    that name one shape; None otherwise."""
    q = (question or "").strip()
    if not QUICK_LANE or not q or len(q) > 120:
        return None
    cos = us_link.find_companies(cur, _without_benchmarks(q))
    if _STEPS_DATA.search(q) and not re.search(r"\bpa(?:y|id)\b|compensation|salar|\bsold\b|\bbought\b|\bsell|\bbuy|insider|holders?|\bowns?\b|portfolio|stake", q.lower()):
        # one company's quarters, by code: "Apple Q3 2025 revenue", "Nvidia revenue every quarter", "Snowflake revenue last quarter"
        if len(cos) == 1 and re.search(r"\bquarter|\bq[1-4]\b", q.lower()) and not re.search(r"\b(vs\.?|versus|compar|segment|division|how many|which|share of|median|average)\b", q.lower()):
            rest_q = _strip_companies(_without_benchmarks(q), cos)
            if re.fullmatch(_Q_LEAD + r"(?:(?:revenue|sales|net income|profit|earnings|operating income|operating profit|eps|earnings per share|results) )?"
                            r"(?:(?:in|for|of|from) )?(?:(?:the )?(?:last|latest|most recent|previous|past|this) quarter|q[1-4] ?(?:fy)?'?\d{2,4}|(?:fy)?\d{4} ?q[1-4]|(?:first|second|third|fourth) quarter(?: of)? (?:fy)?\d{4}|"
                            r"(?:every|each|by|per) quarter|quarterly|quarter by quarter|all quarters|last \d+ quarters|recent quarters|every quarter since \d{4}|quarterly since \d{4})"
                            r"(?: (?:revenue|sales|net income|profit|earnings|operating income|eps|results))?(?: (?:vs|versus|against|compared (?:with|to)) (?:the )?same quarter (?:last|a) year(?: ago| earlier)?| year on year| yoy)?\??", rest_q):
                return BY_ID["company_quarters"], entities(cur, q)
        return None                                              # other quarterly and segment questions live in the step route's tables
    ql = q.lower()
    if not cos:
        # "largest insider purchases last week", "where are insiders buying": across the market, by code
        if re.fullmatch(r"(?:the |what are the |show me the |show |list )?(?:largest|biggest|top|most|recent|latest)?\s*insider (?:purchases|buys|buying|purchase)(?: by (?:value|size))?"
                        r"(?: (?:last|this|past) (?:week|month|quarter|year)| recently| today| yesterday| in %(y)s| so far this year| ytd)?\??" % {"y": _Q_YEAR}, ql) or \
                re.fullmatch(r"where are insiders buying(?: (?:this|last) (?:week|month|year))?\??", ql):
            return BY_ID["insider_buying_market"], entities(cur, q)
        # "earnings calendar next week", "who is reporting this week": the filing calendar, by code
        if re.search(r"\b(earnings|results|reporting|filing) calendar\b|who (?:is|are) reporting|which companies (?:are )?reporting|companies reporting", ql):
            return BY_ID["calendar_window"], entities(cur, q)
        # "average P/E of the Dow", "median market cap of the S&P 500": one statistic across an index
        m = re.fullmatch(r"(?:what is |whats |what's |show me )?(?:the )?(average|median|mean|typical) (.+?) (?:of|for|in|across|on) (?:the )?(?:s&p 500|s&p|sp500|spx|dow|dow jones|djia)(?: members| stocks| companies| index)?\??", ql)
        if m and us_link.parse_index(q):
            e = entities(cur, q)
            e["index"], e["stat"], e["stat_words"] = us_link.parse_index(q), m.group(1), m.group(2)
            return BY_ID["index_stat"], e
    # "Chinese companies listed in the US by market value", "Israeli tech stocks": a country or sector, by code
    if not cos and not us_benchmarks.find(q) and re.search(r"\b(companies|company|stocks|stock|firms|firm|names|shares|adrs)\b", ql) and not _SHARE_WORDS.search(ql) \
            and not _THRESHOLD_WORDS.search(ql) and not us_link.resolve_industry(cur, q) and country_sector(q)[0] or (
            not cos and not us_benchmarks.find(q) and re.search(r"\b(companies|company|stocks|stock|firms|firm)\b", ql) and country_sector(q)[2] and not us_link.resolve_industry(cur, q)
            and re.search(r"\b(largest|biggest|top|best|worst|cheapest|most|highest|lowest|fastest)\b", ql) and not _SHARE_WORDS.search(ql) and not _THRESHOLD_WORDS.search(ql)):
        try:
            _metric(q)
        except HTTPException:
            return None
        return BY_ID["market_rank"], entities(cur, q)
    # "best performing semiconductor stock over 5 years", "cheapest airlines by P/E": an industry and a ranking word
    if not cos and not us_benchmarks.find(q):
        ind = us_link.resolve_industry(cur, q)
        if ind and re.search(r"\b(best|worst|top|cheapest|most expensive|largest|biggest|smallest|highest|lowest|fastest|most profitable|most valuable|rank|margins?|"
                             r"return on equity|roe|growth|p/e|yields?|free cash flow|revenues?|profits?|deposits|valuations?|leverage|debt)\b", q.lower()) \
                and not re.search(r"\b(against|vs\.?|versus|moved|treasury|10.year|ten.year|interest rates?|index|s&p|dow|nasdaq|correlat|compared)\b", q.lower()) \
                and not re.search(r"\b(and|with|without|positive|negative|no|at least|more than|less than)\b", q.lower()) \
                and not _SHARE_WORDS.search(q.lower()) and not _THRESHOLD_WORDS.search(q.lower()) and not _PAY_WORDS.search(q) \
                and not re.search(r"insider|13f|holders?|funds?\b", q.lower()):
            try:
                _metric(q)                                       # a figure we do not rank on says so here
            except HTTPException:
                return None
            e = entities(cur, q)
            return BY_ID["industry_rank"], e
    # a 13F manager and the words for its portfolio ("what did Berkshire sell last quarter", "Vanguard's top holdings")
    mgr = us_link.resolve_manager(cur, q) if us_link._MANAGER_ASKS.search(q.lower()) else None
    if mgr is None and cos and len(cos) == 1 and us_link._MANAGER_ASKS.search(q.lower()) and \
            re.fullmatch(r"(?:its |their |the )?(?:holdings|portfolio|positions|top holdings|largest holdings?|biggest holdings?|13f|13f holdings)(?: last quarter| this quarter)?\??",
                         _strip_companies(q, cos)):
        mgr = us_link.manager_by_cik(cur, cos[0]["cik"], cos[0]["name"])
    if mgr and not re.search(r"who (owns|holds)|holders|shareholders", q.lower()):
        rest_m = _strip_companies(q, cos) if cos else re.sub(r"\s+", " ", q).strip().lower()
        rest_m = re.sub(r"(?i)\b" + "|".join(re.escape(w) for w in us_link.norm_name(mgr[1]).split() if len(w) >= 3) + r"\b", " ", rest_m) if mgr[1] else rest_m
        rest_m = re.sub(r"(?i)\b(inc|llc|lp|ltd|corp|co|the|of|s)\b|['\u2019]s", " ", rest_m)
        rest_m = re.sub(r"\s+", " ", rest_m).strip(" ?.")
        if _Q_MANAGER.match(rest_m):
            e = entities(cur, q)
            e["manager"] = mgr
            return BY_ID["manager_portfolio"], e
    if not cos:
        return None
    rest = _strip_companies(_without_benchmarks(q), cos)
    rest = _drop_near_names(rest, cos)
    # "is Nvidia in the S&P 500", "Apple's weight in the Dow": membership, by code
    ix = us_link.parse_index(q)
    if ix and len(cos) == 1 and re.search(r"\b(in|part of|member of|included in|belong to|weight|weighting)\b", q.lower()) and \
            not re.search(r"beat|outperform|underperform|vs\.?|versus|against|compar|drove|contribut|rank|best|worst|cheapest|largest", q.lower()):
        e = entities(cur, q)
        e["index"] = ix
        return BY_ID["index_member"], e
    # "GOOG vs GOOGL", "BRK.A vs BRK.B": two classes of one company
    toks = [t.upper().replace(".", "-") for t in re.findall(r"\$?\b[A-Z]{1,5}(?:[.-][A-Z])?\b", q)]
    classes = [t for t in dict.fromkeys(toks) if t in us_link.companies(cur)["by_ticker"]]
    if len({c["cik"] for c in cos}) == 1 and len(classes) >= 2 and len({us_link.companies(cur)["by_ticker"][t]["cik"] for t in classes}) == 1:
        e = entities(cur, q)
        e["classes"] = classes
        return BY_ID["share_classes"], e
    if us_benchmarks.find(q) or _CONTRIB_WORDS.search(q.lower()):
        return None                                          # an index is named: the full matcher reads it
    # "Boeing vs Airbus": the other side is no US-listed filer, and the reader is told so at once
    m = re.match(r"^(?:compare )?(.+?)\s+(?:vs\.?|versus|against|compared (?:with|to))\s+(.+?)\??$", q, re.I)
    if m and len(cos) == 1:
        sides = [m.group(1), m.group(2)]
        other = next((s for s in sides if not us_link.find_companies(cur, s)), None)
        if other and re.match(r"^[A-Z][\w&.'-]*(?: [A-Z][\w&.'-]*){0,3}$", other.strip()):
            from . import ask_miss
            raise ask_miss.miss("%s is not a US-listed filer on file, so there is nothing to compare %s with. Ask about %s on its own."
                                % (other.strip(), cos[0]["name"], cos[0]["name"]), "not_held")
    for rx, rid, need in _QUICK:
        if len(cos) < need or (need == 1 and len(cos) > 1 and rid != "company_history"):
            if not (need == 1 and len(cos) == 1):
                continue
        if not rx.match(rest):
            continue
        e = entities(cur, q)
        if rid == "company_history":
            if len(cos) > 1 and not re.search(r"\bvs\.?\b|versus|\band\b|\bor\b|compar", rest):
                return None
            if len(cos) > 1:
                rid = "compare_companies"
            else:
                try:
                    e["history"] = e.get("history") or history_figure(rest)
                except HTTPException:
                    return None
                if not e["history"]:
                    return None
                # a margin or ratio named plainly is the growth-and-margins card, which shows it with its context
                if e["history"]["id"] in ("operating_margin", "net_margin") and not us_link.parse_year_window(q) and not _HIST_WORDS.search(rest):
                    rid = "company_growth_margin"
                elif e["history"]["id"].startswith("fig:") and not us_link.parse_year_window(q) and not _HIST_WORDS.search(rest):
                    rid, e["figure"] = "company_figure", e["history"]["figure"]
        if rid == "compare_companies" and len(cos) < 2:
            continue
        if rid == "company_value" and rest == "" and len(cos) > 1:
            rid = "compare_companies"
        if rid == "company_segment":
            words = [w for w in re.findall(r"[a-z0-9&]+", rest) if w not in _SEG_STOP]
            if not words or any(re.fullmatch(_CORE_FIG, " ".join(words)) for _ in [0]) or re.search(r"\b(region|regions|by)\b", rest):
                continue
            try:
                if figure_in(rest):
                    continue                              # "provision for loan losses" is a filed line, not a segment
            except HTTPException:
                continue
        return BY_ID[rid], e
    return None


def _near_word(a, b):
    """Within two edits, for words of five letters or more."""
    a, b = (a or "").lower(), (b or "").lower()
    if len(a) < 5 or abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur_ = [i]
        for j, cb in enumerate(b, 1):
            cur_.append(min(prev[j] + 1, cur_[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur_
    return prev[-1] <= 2


def _drop_near_names(rest, cos):
    """Words within two letters of a found company's name word are the name
    misspelt ("Hathway"), and are dropped from the rest of the question."""
    name_words = {w.lower() for c in cos for w in re.findall(r"[A-Za-z]{5,}", (c.get("name") or "") + " " + (c.get("sec_name") or ""))}
    if not name_words:
        return rest
    def near(a, b):
        if abs(len(a) - len(b)) > 2:
            return False
        # a cheap edit distance for short words
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur_ = [i]
            for j, cb in enumerate(b, 1):
                cur_.append(min(prev[j] + 1, cur_[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur_
        return prev[-1] <= 2
    kept = [w for w in rest.split() if not (len(w) >= 5 and any(near(w, n) for n in name_words))]
    return " ".join(kept)


# A question about the economy, not a company: it goes straight to the
# economy answers (app/assistant/preview.py) with no model call and no company
# read ("rent inflation" once found American Homes 4 Rent).
MACRO_WORDS = re.compile(
    r"\b(inflation|cpi|consumer prices?|price index|price indices|treasur(?:y|ies)|yield curve|2s10s|10s2s|unemployment|jobless|payrolls?|"
    r"job openings|gdp|fed funds|federal funds|fomc|fed meeting|fed rate|interest rates?|mortgage rates?|wage growth|real wages|wages|"
    r"hourly earnings|average hourly|pce|bank of japan|boj|jgbs?|monetary base|rents?|rent inflation|housing costs?|shelter|core inflation|headline inflation|"
    r"egg prices|gasoline prices|gas prices|food prices|rice prices|retail sales|trade deficit|trade balance|exports|imports|"
    r"milk|bread|coffee|chicken|beef|bacon|bananas|electricity|natural gas|utility bills?|grocery prices|used car prices|new car prices|"
    r"medical care|health ?care costs?|t.?bills?|real yields?|tips|cost of living|"
    r"(?:10|ten|2|two|5|five|30|thirty)[- ]year (?:yield|rate|bond|note)|tips)\b", re.I)
MACRO_MISS = ("That reads as a question about the economy, and Plover's data doesn't include that series yet. It holds US inflation, "
              "wages, jobs and Treasury yields, and prices, jobs, GDP and yields for Japan and Hong Kong.")


# a dated event the events table holds (app/us_tables.py): a question about the market or a stock
# around one is a price question for the step route, not an economy series
_EVENT_WORDS = re.compile(r"\b(?:fomc|fed (?:meetings?|decisions?|statements?|days?)|(?:cpi|inflation|jobs) (?:releases?|days?|prints?|reports?)|"
                          r"(?:government |federal )?shutdowns?|earnings (?:days?|announcements?|releases?)|results days?)\b|"
                          r"\b(?:after|before|around|following|post)[- ](?:(?:its|their|the|last|latest|next|each|every|an?) )*"
                          r"(?:earnings|results|reports?|reporting|it reports|they report|fomc|cpi|fed)\b")
_EVENT_FRAME = re.compile(r"\b(?:after|before|around|during|following|since|on|next|upcoming|when is)\b|\b(?:market|s&p|sp500|dow|nasdaq|stocks?|shares|index)\b")


def is_macro_question(cur, question):
    ql = (question or "").lower()
    if not MACRO_WORDS.search(ql):
        return False
    if _EVENT_WORDS.search(ql) and _EVENT_FRAME.search(ql):
        return False
    if re.search(r"\b(bank|banks|stocks?|companies|company|shares|dividend|insider|earnings (?:date|calendar|season|report)|reports?)\b", ql) and not re.search(r"\bbank of japan\b|\bboj\b", ql):
        return False
    try:
        rest = MACRO_WORDS.sub(" ", _without_benchmarks(question))
        rest = re.sub(r"(?i)\b(rates?|index|data|figures?|numbers?|today|now|latest|current|us|u\.s\.|american|last month|this month|this year|japan(ese)?|tokyo|"
                      r"hong kong|hk|growth|rate of|month on month|year on year|trend|rising|falling|peak|price of|prices? of|average|cost of)\b", " ", rest)
        return not us_link.find_companies(cur, rest)
    except Exception:                                             # noqa: BLE001 — no company list: still a macro question
        return True


def answer(cur, question, prev=None):
    """The quick lane first: a company plus a few words naming one shape is
    answered by code at once. Then the reader: a small model fills in the
    question's form (app/us_ask_model.py) and the shape it names runs on the
    same code as a pattern match. When the reader is off or its form cannot
    be placed, the word patterns answer. A refusal from the data (an index
    whose members we do not hold, a figure not filed) is returned as is,
    never swapped for a different question."""
    from . import ask_miss, ask_progress, us_ask_model
    ql = (question or "").lower()
    if _NOT_A_QUESTION.fullmatch(ql.strip(" ?!.,")):
        raise ask_miss.miss(ask_miss.UNCLEAR, "unclear")
    for pat, msg in _UNANSWERABLE:
        if re.search(pat, ql):
            raise HTTPException(422, msg)
    held = not_held_early(question)
    if held:
        raise ask_miss.miss(held, "not_held")
    if not prev and is_macro_question(cur, question):
        raise ask_miss.miss(MACRO_MISS, "not_held")
    from . import us_steps
    pointer = us_steps.other_market(question) if not prev else None
    if pointer:
        us_steps.log(question, "other market", pointer)
        raise HTTPException(422, pointer)
    unknown = _unknown_manager(cur, question)
    if unknown:
        raise ask_miss.miss(unknown, "not_held")
    # "SVB deposits", "Airbus revenue": a name with a figure word, and no filer by that name
    try:
        names_a_figure = bool(figure_in(question) or history_figure(question))
    except HTTPException:
        names_a_figure = False                                   # a figure not held is answered by its own reason later
    if not prev and names_a_figure and not us_link.find_companies(cur, _without_benchmarks(question)) \
            and not us_link.resolve_industry(cur, question) and not us_benchmarks.find(question) and not is_macro_question(cur, question):
        caps = [w for w in re.findall(r"\b[A-Z][A-Za-z0-9&.'-]*\b", question) if w.upper() not in us_link._ENGLISH and w.upper() not in us_link._GENERIC_NAMES
                and w not in _PERSON_STOP and w.upper() not in us_link._TICKER_STOP and len(w) >= 3]
        near = any(_near_word(c.lower(), k) for c in caps for k in list(us_link.ALIASES) + list(us_link._memo(cur, "short_names", us_link._build_short_names)))
        if caps and not near and not re.search(r"\b(which|what|who|how many|largest|biggest|top|best|worst|cheapest|average|median|sold|sales|sell|bought|buy|insider|stake|owns?|portfolio|holdings?)\b", question.lower()):
            raise ask_miss.miss("No US-listed filer called %s is on file. The box covers companies that file with the SEC; a bank or company that was "
                                "taken over or delisted no longer appears." % " ".join(caps[:3]), "not_held")
    if not prev and _PAY_WORDS.search(question or "") and not us_link.find_companies(cur, _without_benchmarks(question)):
        e = entities(cur, question)
        named = _ceo_named(question, _pay_rows(cur))
        if named or re.search(r"(highest|best|top|lowest|worst).paid|most paid|paid (the )?most|\bceos\b", ql):
            ask_progress.say(_doing(BY_ID["ceo_pay"], e))
            out = r_ceo_pay(cur, question, e)
            out["match"] = {"recipe": "ceo_pay", "label": "CEO pay", "score": None, "alternatives": [], "via": "quick", "read_as": None}
            return _finish(out, e)
        who = person_named(question)
        if who and _pay_rows(cur):
            raise ask_miss.miss("No proxy pay figures for %s are loaded: the pay table covers the S&P 500 members' latest proxy "
                                "statements, and that name is not among their CEOs." % " ".join(who[:3]), "not_held")
    if not prev:
        quick = quick_match(cur, question)
        if quick:
            r, e = quick
            ask_progress.say(_doing(r, e))
            out = r["run"](cur, question, e)
            out["match"] = {"recipe": r["id"], "label": r["label"], "score": None, "alternatives": [], "via": "quick", "read_as": None}
            return _finish(out, e)
    ask_progress.say("Reading your question…")
    form = us_ask_model.read(question, reader_prompt(), prev)
    if form:
        if form.get("refuse") in _REFUSALS and not form.get("shape"):
            from . import ask_miss
            kind = _REFUSAL_KIND[form["refuse"]]
            us_steps.log(question, "refused: " + form["refuse"])
            # "not understood" from the reader is checked against the words: a question that names a
            # company ("Apple revenue history") goes to the word patterns instead of being refused
            if kind == "unclear" and us_link.find_companies(cur, _without_benchmarks(question)):
                return _pattern_answer(cur, question)
            raise ask_miss.miss(_REFUSALS[form["refuse"]] or ask_miss.UNCLEAR, kind)
        # a follow-up ("and Microsoft?") is answered as the whole question the
        # reader wrote from it and the previous one; the page shows that reading
        whole = str(form.get("question") or "").strip()
        if whole and us_ask_model._norm(whole) != us_ask_model._norm(question) and 2 <= len(whole) <= 300:
            us_steps.log(question, "follow-up read as", whole)
            question, ql = whole, whole.lower()
            if is_macro_question(cur, whole):
                exc = ask_miss.miss(MACRO_MISS, "not_held")
                exc.question = whole                       # the economy answers read the follow-up whole
                raise exc
            pointer = us_steps.other_market(whole)
            if pointer:
                raise HTTPException(422, pointer)
        e = None
        # a nationality ("Chinese stocks", "Israeli companies") is a filter the
        # ready-made rankings do not have; the step route has a country column
        if form.get("shape") in ("market_rank", "industry_rank") and _NATIONALITY.search(ql) and not country_sector(question)[0]:
            form = dict(form, shape="steps")
        # quarterly results, CEO pay and segments live only in the step route's tables (app/us_tables.py)
        if form.get("shape") in _FIGURE_SHAPES and _STEPS_DATA.search(ql):
            form = dict(form, shape="steps")
        # a price around a dated event (after earnings, on CPI days, during a shutdown) joins the events
        # table: the step route, never the plain price shapes (the reader read "Tesla 3 days after
        # earnings" as its price change since last October)
        if form.get("shape") and form["shape"] != "steps" and _EVENT_WORDS.search(ql) and _EVENT_FRAME.search(ql):
            form = dict(form, shape="steps")
        # a share, a count or an average is worked out step by step, never answered with a ranking
        if form.get("shape") in ("market_rank", "industry_rank") and (_SHARE_WORDS.search(ql) or _THRESHOLD_WORDS.search(ql)):
            form = dict(form, shape="steps")
        if form.get("shape") in ("steps", "company_says") and re.search(r"how many risks?|how many risk factors|count (of )?(the )?risks?", ql) and form.get("companies"):
            form = dict(form, shape="company_risks")
        if form.get("shape") == "steps" and form.get("companies") and len(form.get("companies") or []) == 1 and _SEGMENT_Q.search(ql) \
                and not re.search(r"\bquarter|\bq[1-4]\b|history|over time|each year|every year", ql):
            form = dict(form, shape="company_segment")
        if form.get("shape") in ("market_rank", "industry_rank", "company_figure", "company_value", "steps") and _PAY_WORDS.search(ql) \
                and not re.search(r"\b(median|average|mean|how many|ratio|employee|over time|histor)\b", ql):
            form = dict(form, shape="ceo_pay")
        # a span of years, or growth over years, is the history shape, not one year's growth
        if form.get("shape") in ("company_growth_margin", "company_figure") and (
                (us_link.parse_year_window(question) or _SPAN_WORDS.search(ql)) and form.get("companies")):
            form = dict(form, shape="company_history")
        if form.get("shape") and form["shape"] != "steps":
            try:
                e = entities_from_form(cur, question, form)
            except (FormError, HTTPException):
                e = None
        if e is not None:
            r = BY_ID[form["shape"]]
            ask_progress.say(_doing(r, e))
            out = r["run"](cur, question, e)
            out["match"] = {"recipe": r["id"], "label": r["label"], "score": None, "alternatives": [], "via": "reader",
                            "read_as": form.get("read_as")}
            return _finish(out, e)
        # the reader found no shape at all ("no_fit"): a word pattern that names a
        # specific shape for this question answers it before the step route is
        # tried ("Nvidia share price" is the price shape, which the reader missed)
        if not form.get("shape") and form.get("refuse") in (None, "no_fit"):
            try:
                recipe, e2, score, alts = match(cur, question)
            except HTTPException:
                recipe = None
            if recipe is not None and score > 0:
                us_steps.log(question, "no_fit: answered by patterns", recipe["id"])
                ask_progress.say(_doing(recipe, e2))
                out = recipe["run"](cur, question, e2)
                out["match"] = {"recipe": recipe["id"], "label": recipe["label"], "score": score, "alternatives": alts,
                                "via": "patterns", "read_as": form.get("read_as")}
                return _finish(out, e2)
        # no ready-made shape answers it: work it out step by step
        ask_progress.say("No ready-made answer fits, so working it out step by step…")
        try:
            out = us_steps.answer(cur, question)
            if form.get("read_as"):
                out["match"]["read_as"] = form["read_as"]       # the reading is shown however the answer was made
            return out
        except us_steps.StepsError:
            pass
    try:
        return _pattern_answer(cur, question)
    except HTTPException as exc:
        if form is None and exc.status_code == 422:
            us_steps.log(question, "no match (reader off)", exc.detail)
        raise


_FIGURE_SHAPES = ("market_rank", "industry_rank", "company_figure", "company_growth_margin", "compare_companies", "company_history",
                  "company_value")
_SHARE_WORDS = re.compile(r"\bshare of\b|what share|what percentage|what percent|percentage of|percent of|\bhow many\b|\bmedian\b|\baverage\b|\bmean\b|how much of|what fraction")
# a figure cut-off ("above 4%", "over $10bn"), not a span of time ("over 5 years")
_THRESHOLD_WORDS = re.compile(r"\b(above|below|over|under|more than|less than|at least|at most|greater than|higher than|lower than|exceeding)\s+\$?\d[\d.,]*"
                              r"(?!\s*(?:years?|months?|weeks?|days?|quarters?|yrs?)\b)")
_SPAN_WORDS = re.compile(r"\bcagr\b|compound|a year on average|per year|annual(?:ised|ized)? growth|over (?:the )?(?:last|past) \d+ years|since (?:19|20)\d{2}|years ago")
_STEPS_DATA = re.compile(r"\bquarter(ly|s)?\b|\bq[1-4]\b|"
                         r"\bsegments?\b|\bdivisions?\b|revenue from (china|europe|asia|the us|abroad|overseas)", re.I)
_PAY_WORDS = re.compile(r"(?!.*(?:stock.based|share.based|equity comp|stock comp|unearned|deferred comp))(?:\bceos?\b.*\bpa(y|id)\b|\bpa(y|id)\b.*\bceos?\b|compensation|\bsalar(y|ies)\b|(highest|best|top|lowest).paid)", re.I)

GROWTH_BASE_USD = 5e7       # same floor as the step route's rule: revenue_prior_usd >= 5e7


_NATIONALITY = re.compile(r"\b(chinese|china|israeli|israel|canadian|canada|british|irish|dutch|german|french|swiss|"
                          r"indian|india|brazilian|brazil|mexican|mexico|argentin\w+|singapore\w*|korean|taiwanese|"
                          r"australian|bermuda\w*|cayman\w*|luxembourg\w*|foreign|overseas)\b", re.I)


# what each shape is doing, said to the reader while it runs (app/ask_progress.py)
DOING = {
    "company_business": "Reading {co}'s business description and revenue split from its latest annual report",
    "company_risks": "Reading the risk factors in {co}'s latest annual report",
    "company_says": "Searching {co}'s latest annual report",
    "company_value": "Valuing {co} on its latest close and share count",
    "company_growth_margin": "Reading {co}'s revenue and profit from its latest annual report",
    "company_figure": "Finding the figure in {co}'s latest annual report",
    "company_history": "Collecting {co}'s figures from each year's annual report",
    "compare_companies": "Putting {co} side by side",
    "company_insiders": "Reading insider filings (Forms 4) at {co}",
    "company_holders": "Reading the funds' 13F filings for holders of {co}",
    "company_as_filed_history": "Reading each of {co}'s annual reports as first filed",
    "company_calendar": "Checking when {co} last filed and reports next",
    "company_shareholder_return": "Reading {co}'s buybacks and dividends",
    "company_price_performance": "Reading {co}'s daily closes",
    "vs_benchmark": "Comparing {co} with the index over the period",
    "index_contribution": "Working out each member's part in the index's move",
    "managers_holding_both": "Matching the 13F filings that hold both",
    "manager_portfolio": "Reading the manager's latest 13F filing",
    "ceo_pay": "Reading the pay figures from the latest proxy statements",
    "company_segment": "Reading {co}'s revenue split from its latest annual report",
    "index_stat": "Working the statistic out across the index's members",
    "company_quarters": "Reading {co}'s quarterly filings",
    "company_employees": "Reading the headcount in {co}'s annual report",
    "index_member": "Checking the index fund's holdings for {co}",
    "company_splits": "Reading {co}'s price history for splits",
    "share_classes": "Reading the closes of each share class",
    "holders_increase_industry": "Reading 13F changes across the industry",
    "industry_rank": "Ranking the industry's companies",
    "insider_buying_market": "Reading insider purchases across the market",
    "calendar_window": "Checking who reports in the coming days",
    "banks_vs_yield": "Lining up bank profits with the 10-year yield",
    "market_rank": "Ranking US-listed companies",
}


def _doing(recipe, e):
    names = [c["name"] for c in (e.get("companies") or [])[:3]]
    co = _join(names) if names else "the company"
    return DOING.get(recipe["id"], recipe["label"]).format(co=co) + "…"


def _pattern_answer(cur, question):
    from . import ask_progress
    try:
        recipe, e, score, alts = match(cur, question)
    except HTTPException as exc:
        if exc.detail == _BARE_COMPANY:
            e = entities(cur, question)
            out = r_company_value(cur, question, e)
            out["match"] = {"recipe": "company_value", "label": BY_ID["company_value"]["label"], "score": 0,
                            "alternatives": [], "via": "patterns"}
            return _finish(out, e)
        raise
    ask_progress.say(_doing(recipe, e))
    out = recipe["run"](cur, question, e)
    out["match"] = {"recipe": recipe["id"], "label": recipe["label"], "score": score, "alternatives": alts, "via": "patterns"}
    return _finish(out, e)


def _finish(out, e):
    if e.get("period_span") and not out.get("period"):
        out["period"] = period_span(*e["period_span"])
    for c in e.get("companies") or []:
        # "Tencent" finds Tencent Music, the only filer with that word: the reader is told what was read
        if c.get("matched_by") == "short name" and len((c.get("name") or "").split()) >= 3:
            note = "Read as %s, the US-listed filer whose name carries that word; a parent or affiliate not listed in the US is not held." % c["name"]
            out.setdefault("notes", [])
            if note not in out["notes"]:
                out["notes"].insert(0, note)
    out["entities"].update({"mentioned_companies": [{"cik": c["cik"], "ticker": c["ticker"], "name": c["name"], "matched_by": c["matched_by"]} for c in e["companies"]],
                            "industry_read": e["industry"][1] if e["industry"] else None,
                            "manager_read": e["manager"][1] if e["manager"] else None,
                            "since_read": _d(e["since"]) if e.get("since") else None,
                            "amount_read": e.get("money")})
    return out


def catalogue():
    return [{"id": r["id"], "label": r["label"], "needs": r["needs"]} for r in RECIPES]


# ---- warm-up -------------------------------------------------------------------
# The first answer after a start, or after the nightly refresh swaps the SEC
# database, would build every company's ratios, the price repairs and the name
# indexes from cold: 11.6 s measured on 2026-10-19. A background thread builds
# them as soon as the server is up and again whenever the file changes, so no
# reader waits for it.
def warm_once():
    cur = sec_api._cur()
    if not sec_api._has(cur, "sec_filings"):
        return
    all_metric_rows(cur)
    us_link.companies(cur)
    us_link._build_cusips_memo(cur)
    if sec_api._has(cur, "us_px_daily"):
        us_prices_api._events(cur)
        us_prices_api.latest_closes(cur)
        us_prices_api.window_changes(cur, datetime.date(datetime.date.today().year, 1, 1))
    try:
        from . import us_tables
        us_tables.sandbox(cur)                 # the step route's tables, so no reader waits for their build
        us_steps.base_rows(cur)                # and its stock list (7 s cold locally)
    except Exception:                          # noqa: BLE001 — a warm-up never takes the server down
        pass


def warm_forever(poll_seconds=60):
    import time
    seen = None
    while True:
        try:
            v = sec_api.file_version()
            if v and v != seen:
                warm_once()
                seen = v
        except Exception:                                         # noqa: BLE001 — a warm-up never takes the server down
            pass
        time.sleep(poll_seconds)
