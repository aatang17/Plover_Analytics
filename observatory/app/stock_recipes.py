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
  answer     two or three sentences of context, each figure from the data
  figures    the few numbers used, with unit and as-of
  table      rows to scan (optional)
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


# ---- recipes: one company --------------------------------------------------
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
    if p.get("close") is not None and m.get("market_cap") is not None:
        bits.append("The shares closed at %s on %s." % (money(p["close"]), date_words(p["date"])))
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
    if m.get("pe_x") is not None:
        bits.append("That is %s the diluted earnings of the latest annual report (year to %s)." % (mult(m["pe_x"]), date_words(row["period_end"])))
    elif row["per_share"].get("eps_diluted") is not None and row["per_share"]["eps_diluted"] <= 0:
        bits.append("There is no P/E: the latest annual report shows a loss per share.")
    if m.get("dividend_yield_pct") is not None or m.get("buyback_yield_pct") is not None:
        bits.append("Dividends paid last fiscal year were %s of today's value and buybacks %s."
                    % (pct(m.get("dividend_yield_pct"), 2), pct(m.get("buyback_yield_pct"), 2)))
    if m.get("price_change_1y_pct") is not None:
        bits.append("The stock is %s over one year." % pct(m["price_change_1y_pct"], 1, sign=True))
        bits += [w for w, status in _split_words(cur, rec, datetime.date.today() - datetime.timedelta(days=365))
                 if status != "confirmed"]
    chart = {"kind": "line", "title": "%s share price, one year" % rec["ticker"], "unit": "USD",
             "series": [{"name": rec["ticker"], "points": [[_d(d), c] for d, c in series]}]} if series else None
    figures = [fig("Market value", m.get("market_cap"), "USD", p.get("date"), sec_metrics.PRICE_FORMULAS["market_cap"]),
               fig("Close", p.get("close"), "USD", p.get("date")),
               fig("P/E", m.get("pe_x"), "×", p.get("date"), sec_metrics.PRICE_FORMULAS["pe_x"]),
               fig("Dividend yield", m.get("dividend_yield_pct"), "%", p.get("date"), sec_metrics.PRICE_FORMULAS["dividend_yield_pct"]),
               fig("Buyback yield", m.get("buyback_yield_pct"), "%", p.get("date"), sec_metrics.PRICE_FORMULAS["buyback_yield_pct"]),
               (fig("Shares outstanding", basis.get("shares"), "shares", basis.get("shares_date"))
                if basis and basis.get("method") != "balance sheet" and basis.get("shares") is not None
                else fig("Shares outstanding", row["per_share"].get("shares_outstanding"), "shares", row["period_end"])),
               fig("Diluted EPS", row["per_share"].get("eps_diluted"), "USD", row["period_end"])]
    return pkg("company_value", q, headline, " ".join(bits), figures, None, chart,
               [PRICE_SOURCE, SEC_SOURCE], {k: sec_metrics.PRICE_FORMULAS[k] for k in ("market_cap", "pe_x", "dividend_yield_pct", "buyback_yield_pct")},
               {"company": rec}, {"company": company_link(rec), "filing": row["source_url"]})


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
    if _GENERIC_LINE.match(top["segment"]):
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
        bits.append("By %s: %s." % ("product line" if main is kinds["product"] else "reporting segment", _join(words[:6])))
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
    if kinds["geography"]:
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
    chart = ({"kind": "bars", "title": "%s revenue by %s, year to %s" % (rec["ticker"], "product line" if main is kinds["product"] else "segment", fy),
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
    """(cursor, latest annual report read) for a company, or (None, None)."""
    from . import sec_text_api
    c = sec_text_api.cur()
    if c is None or not rec:
        return None, None
    return c, sec_text_api.latest(c, rec["cik"])


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
_NOUNS = [(r"\bcompetitors?\b|\bcompetition\b|\bcompete\b|\brivals?\b", "competition", "compet"),
          (r"\bcustomers?\b|\bclients?\b", "customers", "customer"),
          (r"\bsuppliers?\b|\bsupply chain\b", "suppliers", "supplier"),
          (r"\bemployees?\b|\bheadcount\b|\bworkforce\b|\bstaff\b", "employees", "employees"),
          (r"\bpatents?\b|intellectual property", "intellectual property", "intellectual property"),
          (r"\bregulat\w*", "regulation", "regulat")]
_SECTION_WORDS = [(r"(?i)lawsuits?|litigation|legal (cases|proceedings|matters)|\bsued\b|\bcourts?\b|antitrust|investigations?", "legal"),
                  (r"(?i)cyber|hack|data breach|security incident", "cybersecurity"),
                  (r"(?i)\bproperties\b|offices|factories|plants|headquarters|where .* (based|located)", "properties"),
                  (r"(?i)management|outlook|md&a|results of operations|how was (the|its) year", "mdna")]


_EXACT = {"competition": "competitors", "customers": "customers", "suppliers": "suppliers"}


def _topic_of(q, rec):
    """The subject a question asks about inside the report, or None."""
    text = re.sub(r"(?i)\b(in|from) (its|the|their) (latest |last |most recent )?(annual report|10-?k|20-?f|filing)s?\b", " ", q or "")
    for pat in (_SAYS, _EXPOSED):
        m = pat.search(text)
        if m:
            t = m.group(1)
            names = [rec.get("name") or "", rec.get("ticker") or ""] + [re.sub(r"[^\w&-]", "", w) for w in (rec.get("name") or "").split()[:1]]
            for w in sorted(names, key=len, reverse=True):
                if len(w) > 2:
                    t = re.sub(r"(?i)\b%s('s)?\b" % re.escape(w), " ", t)
            t = re.sub(r"(?i)^\s*(?:(?:from|to|of|about|on|for|its|their|the|a|an)\s+)+", "", t.strip()).strip(" ?.!\"'“”")
            if 3 <= len(t) <= 60:
                return t
    for pat, label, stem in _NOUNS:
        if re.search(pat, q or "", re.I):
            return label
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
    fy = "the year to %s" % date_words(row["period_end"])
    if m.get("revenue_growth_pct") is None:
        headline = "%s: revenue %s in %s; no prior year tagged, so no growth rate." % (rec["name"], money(z["revenue"]), fy)
    else:
        g = m["revenue_growth_pct"]
        headline = ("%s grew revenue %s in %s." % (rec["name"], pct(g, 1), fy) if g >= 0
                    else "%s's revenue fell %s in %s." % (rec["name"], pct(abs(g), 1), fy))
    bits = ["Revenue was %s against %s a year earlier, in the same filing." % (money(z["revenue"]), money(pr["revenue"]))]
    if m.get("operating_margin_pct") is not None:
        bits.append("Operating margin was %s and net margin %s." % (pct(m["operating_margin_pct"]), pct(m.get("net_margin_pct"))))
    elif m.get("net_margin_pct") is not None:
        bits.append("Net margin was %s; the filing tags no operating-income line." % pct(m["net_margin_pct"]))
    if m.get("profit_growth_pct") is not None:
        pg = m["profit_growth_pct"]
        bits.append(("Net income grew %s to %s." % (pct(pg, 1), money(z["net_income"]))) if pg >= 0
                    else ("Net income fell %s to %s." % (pct(abs(pg), 1), money(z["net_income"]))))
    elif z.get("net_income") is not None:
        bits.append("Net income was %s." % money(z["net_income"]))
    if m.get("roe_pct") is not None:
        bits.append("Return on equity was %s." % pct(m["roe_pct"]))
    table = {"columns": ["Figure", fy.replace("the year to ", "Year to "), "Prior year"],
             "rows": [["Revenue", z["revenue"], pr["revenue"]], ["Operating income", z["operating_income"], None],
                      ["Net income", z["net_income"], pr["net_income"]], ["Total assets", z["total_assets"], pr["total_assets"]],
                      ["Equity", z["equity"], pr["equity"]]], "unit": row["unit"]}
    figures = [fig("Revenue growth", m.get("revenue_growth_pct"), "%", row["period_end"], sec_metrics.FORMULAS["revenue_growth_pct"]),
               fig("Operating margin", m.get("operating_margin_pct"), "%", row["period_end"], sec_metrics.FORMULAS["operating_margin_pct"]),
               fig("Net margin", m.get("net_margin_pct"), "%", row["period_end"], sec_metrics.FORMULAS["net_margin_pct"]),
               fig("ROE", m.get("roe_pct"), "%", row["period_end"], sec_metrics.FORMULAS["roe_pct"])]
    return pkg("company_growth_margin", q, headline, " ".join(bits), figures, table, None, [SEC_SOURCE],
               {k: sec_metrics.FORMULAS[k] for k in ("revenue_growth_pct", "operating_margin_pct", "net_margin_pct", "roe_pct", "profit_growth_pct")},
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


def r_company_price_performance(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    since = e.get("since") or (datetime.date.today() - datetime.timedelta(days=365 * (e.get("years") or 1)))
    series, splits = closes_with_splits(cur, rec["ticker"], since)
    if not series:
        raise HTTPException(404, "No daily prices on file for %s since %s." % (rec["name"], date_words(since)))
    first, last = series[0], series[-1]
    late_start = (first[0] - since).days > 20
    hi = max(series, key=lambda x: x[1])
    lo = min(series, key=lambda x: x[1])
    change = (last[1] / first[1] - 1) * 100 if first[1] else None
    word = "up" if (change or 0) >= 0 else "down"
    moved = "%s %s" % (word, pct(abs(change) if change is not None else None))
    headline = "%s is %s since %s." % (rec["name"], moved, date_words(first[0]))
    answer = ("It closed at %s on %s against %s on %s. The high in the window was %s on %s and the low %s on %s."
              % (money(last[1]), date_words(last[0]), money(first[1]), date_words(first[0]), money(hi[1]), date_words(hi[0]), money(lo[1]), date_words(lo[0])))
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
    return pkg("company_price_performance", q, headline, answer, figures, None, chart, [PRICE_SOURCE],
               {"change_pct": "(last close ÷ first close in the window − 1) × 100, split-adjusted", "adjusted_close": us_prices_api.SPLIT_RULE},
               {"company": rec, "since": _d(since), "splits": [[_d(d), r] for d, r in splits]}, {"company": company_link(rec)})


def r_company_calendar(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    d = call_api(sec_calendar_api.company, sec_code="cik:%d" % rec["cik"])
    up = d["upcoming"][0] if d["upcoming"] else None
    last = d["filings"][0]
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
    headline = "%s's largest reported holder is %s, with %s of all 13F-reported value." % (rec["name"], top["manager_name"].title(), pct(top["share_of_reported_pct"]))
    bits = ["%d managers reported %s positions worth %s in all for the quarter to %s." % (d["holders"], rec["ticker"], money(d["total_reported_value"]), date_words(top["report_period"]))]
    if adds:
        bits.append("The biggest additions since the previous quarter: " + "; ".join("%s added %s shares" % (r["manager_name"].title(), num(r["shares_change"])) for r in adds) + ".")
    if len(cusips) > 1:
        bits.append("This counts the %s class only; %s has %d share classes in 13F data." % (cusips[0]["issuer_name"], rec["name"], len(cusips)))
    table = {"columns": ["Manager", "Shares", "Value", "Share of reported", "Change in shares"],
             "rows": [[r["manager_name"], r["shares"], r["value"], r["share_of_reported_pct"], r["shares_change"]] for r in rows], "unit": d["window"]["value_unit"]}
    chart = {"kind": "bars", "title": "Largest 13F holders of %s, value" % rec["ticker"], "unit": "USD",
             "series": [{"name": "Value", "points": [[r["manager_name"].title()[:28], r["value"]] for r in rows[:10]]}]}
    return pkg("company_holders", q, headline, " ".join(bits),
               [fig("Holders reporting", d["holders"], "count", d["window"]["end"]), fig("Reported value", d["total_reported_value"], "USD", d["window"]["end"])],
               table, chart, [F13_SOURCE], {"share_of_reported_pct": sec_13f_api.PROVENANCE["note"]},
               {"company": rec, "cusip": cusips[0]["cusip"]}, {"company": company_link(rec)},
               ["13F covers managers over $100m and long positions only, reported up to 45 days after quarter end; a share of reported value is not a share of the company."])


def r_company_insiders(cur, q, e):
    rec = _need(e["companies"][0] if e["companies"] else None, "company")
    since = e.get("since") or datetime.date.today().replace(month=1, day=1)
    months = max(1, int((datetime.date.today() - since).days / 30) + 1)
    ql = q.lower()
    code = "S" if re.search(r"\bsold|sell|sales\b", ql) and not re.search(r"\bbought|buy|purchas", ql) else ""
    d = call_api(sec_insiders_api.company, sec_code="cik:%d" % rec["cik"], months=months, code=code, limit=200)
    s = d["summary"]
    buys, sells = s["open_market_buys"], s["open_market_sells"]
    trades = [t for t in d["trades"] if t["trans_date"] and t["trans_date"] >= since.isoformat()]
    plan_share = None
    sells_list = [t for t in trades if t["trans_code"] == "S"]
    if sells_list:
        plan_share = sum(1 for t in sells_list if t["plan_10b5_1"]) / len(sells_list) * 100
    if buys["trades"] == 0 and sells["trades"] == 0:
        headline = "No open-market buying or selling by %s insiders since %s." % (rec["name"], date_words(since))
    elif buys["trades"] == 0:
        headline = "%s insiders have only sold since %s: %d sales worth %s, no open-market purchases." % (rec["name"], date_words(since), sells["trades"], money(sells["value"]))
    else:
        headline = "%s insiders made %d open-market purchases worth %s and %d sales worth %s since %s." % (rec["name"], buys["trades"], money(buys["value"]), sells["trades"], money(sells["value"]), date_words(since))
    bits = []
    if plan_share is not None:
        bits.append("%s of the sales were under pre-arranged 10b5-1 plans." % pct(plan_share, 0))
    by_owner = {}
    for t in trades:
        if t["trans_code"] in ("P", "S"):
            k = t["owner_name"]
            by_owner.setdefault(k, {"shares": 0, "value": 0, "code": t["trans_code"], "title": t.get("title")})
            by_owner[k]["shares"] += t["shares"] or 0
            by_owner[k]["value"] += t["value"] or 0
    biggest = sorted(by_owner.items(), key=lambda kv: -kv[1]["value"])[:3]
    if biggest:
        bits.append("Largest: " + "; ".join("%s %s %s shares for %s" % (k.title(), "bought" if v["code"] == "P" else "sold", num(v["shares"]), money(v["value"])) for k, v in biggest) + ".")
    bits.append("Awards, option exercises and tax withholding are counted separately and never as buying or selling.")
    table = {"columns": ["Date", "Insider", "Role", "Code", "Shares", "Price", "Value", "10b5-1 plan"],
             "rows": [[t["trans_date"], t["owner_name"], t.get("title") or t.get("relationship"), t["trans_code"], t["shares"], t["price"], t["value"], t["plan_10b5_1"]] for t in trades[:60]]}
    return pkg("company_insiders", q, headline, " ".join(bits),
               [fig("Open-market purchases", buys["trades"], "count", None), fig("Purchase value", buys["value"], "USD", None, "shares × reported price"),
                fig("Open-market sales", sells["trades"], "count", None), fig("Sale value", sells["value"], "USD", None, "shares × reported price"),
                fig("Sales under 10b5-1 plans", round(plan_share, 1) if plan_share is not None else None, "%", None)],
               table, None, [INSIDER_SOURCE], {"value": "shares × the price the filer reported"},
               {"company": rec, "since": _d(since), "code": code or "all"}, {"company": company_link(rec)})


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
    new = [r for r in cur_rows if r[4] is None and prev_period]
    dropped = [r for r in rows if r[2] is None and r[4] is not None]
    bigger = sorted([r for r in cur_rows if r[5] and r[3] and r[3] > r[5] * 1.001], key=lambda r: -((r[3] or 0) - (r[5] or 0)) * ((r[2] or 0) / (r[3] or 1)))[:3]
    smaller = sorted([r for r in cur_rows if r[5] and r[3] is not None and r[3] < r[5] * 0.999], key=lambda r: ((r[3] or 0) - (r[5] or 0)) * ((r[2] or 0) / (r[3] or 1)))[:3]
    top = cur_rows[0]
    headline = "%s reported %s across %d positions for the quarter to %s; the largest is %s at %s." % (
        name.title(), money(total), len(cur_rows), date_words(period), top[1].title(), pct(top[2] / total * 100 if total else None))
    bits = []
    if prev_period:
        bits.append("Against the quarter to %s: %d new positions (%s), %d dropped (%s)." % (
            date_words(prev_period), len(new), ", ".join(r[1].title() for r in sorted(new, key=lambda r: -(r[2] or 0))[:4]) or "none",
            len(dropped), ", ".join(r[1].title() for r in sorted(dropped, key=lambda r: -(r[4] or 0))[:4]) or "none"))
        if bigger:
            bits.append("Added to: " + ", ".join("%s (+%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in bigger) + ".")
        if smaller:
            bits.append("Trimmed: " + ", ".join("%s (%s shares)" % (r[1].title(), num((r[3] or 0) - (r[5] or 0))) for r in smaller) + ".")
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
              ["LongTermDebtAndFinanceLeasesNoncurrent", "LongTermDebtAndFinanceLeasesCurrent"], ["LongTermNotesPayable", "NotesPayable"]]},
    {"id": "rd", "words": r"research|r&d|\br ?and ?d\b|development spend", "label": "research and development spending", "unit": "USD", "kind": "duration",
     "tags": [["ResearchAndDevelopmentExpense"], ["ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]]},
    {"id": "gross_profit", "words": r"gross profit", "label": "gross profit", "unit": "USD", "kind": "duration", "tags": [["GrossProfit"]]},
    {"id": "gross_margin", "words": r"gross margin", "label": "gross margin", "unit": "%", "kind": "duration", "tags": [["GrossProfit"]], "ratio_to": "revenue"},
    {"id": "cost_of_sales", "words": r"cost of (sales|revenue|goods)|cogs", "label": "cost of sales", "unit": "USD", "kind": "duration",
     "tags": [["CostOfGoodsAndServicesSold"], ["CostOfRevenue"]]},
    {"id": "sga", "words": r"sg&a|selling, general|overheads?|general and administrative", "label": "selling, general and administrative expense", "unit": "USD", "kind": "duration",
     "tags": [["SellingGeneralAndAdministrativeExpense"]]},
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
    {"id": "leases", "words": r"lease liabilit|operating leases", "label": "operating lease liabilities", "unit": "USD", "kind": "instant", "tags": [["OperatingLeaseLiability"]]},
    {"id": "dps", "words": r"dividend per share|dividends per share|\bdps\b", "label": "dividends declared per share", "unit": "USD/share", "kind": "duration",
     "tags": [["CommonStockDividendsPerShareDeclared"]]},
    {"id": "diluted_shares", "words": r"diluted shares|share count|number of shares", "label": "weighted diluted shares", "unit": "shares", "kind": "duration",
     "tags": [["WeightedAverageNumberOfDilutedSharesOutstanding"]]},
    {"id": "deposits", "words": r"deposits", "label": "deposits", "unit": "USD", "kind": "instant", "tags": [["Deposits"]]},
    {"id": "nii", "words": r"net interest income", "label": "net interest income", "unit": "USD", "kind": "duration", "tags": [["InterestIncomeExpenseNet"]]},
    {"id": "provisions", "words": r"provision for (loan|credit) loss|credit costs|loan loss", "label": "provision for loan losses", "unit": "USD", "kind": "duration",
     "tags": [["ProvisionForLoanLossesExpensed"]]},
    {"id": "noninterest_expense", "words": r"non.?interest expense", "label": "non-interest expense", "unit": "USD", "kind": "duration", "tags": [["NoninterestExpense"]]},
    {"id": "advertising", "words": r"advertising|marketing spend", "label": "advertising expense", "unit": "USD", "kind": "duration", "tags": [["AdvertisingExpense"]]},
    {"id": "interest_paid", "words": r"interest paid", "label": "interest paid", "unit": "USD", "kind": "duration", "tags": [["InterestPaidNet"]]},
    {"id": "taxes_paid", "words": r"taxes paid|tax paid", "label": "income taxes paid", "unit": "USD", "kind": "duration", "tags": [["IncomeTaxesPaidNet"]]},
    {"id": "rd_intensity", "words": r"research intensity|r&d intensity|r&d as a share|research as a share", "label": "research spending as a share of revenue", "unit": "%", "kind": "duration",
     "tags": [["ResearchAndDevelopmentExpense"]], "ratio_to": "revenue"},
    {"id": "debt_to_equity", "words": r"debt.to.equity|gearing", "label": "borrowings as a share of equity", "unit": "%", "kind": "instant",
     "tags": [["DebtLongtermAndShorttermCombinedAmount"], ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper", "DebtCurrent"], ["LongTermDebt"]], "ratio_to": "equity"},
]
FIGURE_BY_ID = {f["id"]: f for f in FIGURES}
# Figures people ask for that the data sets do not carry, each with the honest reason.
NOT_HELD = [
    (r"employees|headcount|staff\b|workforce|workers|people work|how many people", "Employee counts sit in the text of the 10-K, not in the data sets I hold, so I cannot rank or report them yet."),
    (r"\bebitda\b", "EBITDA is not a filed line; I can show operating income, and depreciation and amortisation, separately."),
    (r"backlog|order book", "Backlog is disclosed in the text of filings, not as a tagged figure I hold."),
    (r"same.store|comparable sales|like.for.like", "Same-store sales are not a tagged figure in the filings I hold."),
    (r"subscribers|users|customers\b", "Subscriber and user counts are not tagged figures in the filings I hold."),
    (r"\bebit\b", "EBIT is not a filed line; operating income is the nearest, and I hold that."),
]


def figure_in(question):
    """The figure a question names, or None; raises 422 for one we do not hold."""
    ql = (question or "").lower()
    for pat, msg in NOT_HELD:
        if re.search(pat, ql):
            raise HTTPException(422, msg)
    best = None
    for f in FIGURES:
        m = re.search(f["words"], ql)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), f)
    return best[1] if best else None


def _all_figure_tags():
    tags = set()
    for f in FIGURES:
        for alt in f["tags"]:
            tags.update(alt)
    return sorted(tags)


FIGURE_TAGS = _all_figure_tags()


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
    if fig.get("ratio_to"):
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
    for f in sec_api._filings(cur, rec["cik"], sec_api.ANNUAL_FORMS)[:6]:
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


def r_compare_companies(cur, q, e):
    cos = e["companies"][:2]
    if len(cos) < 2:
        raise HTTPException(404, "Name two companies to compare.")
    rows = [row_for(cur, c["cik"]) for c in cos]
    if any(r is None for r in rows):
        missing = cos[[i for i, r in enumerate(rows) if r is None][0]]["name"]
        raise HTTPException(404, "%s has no annual report loaded." % missing)
    figd = e.get("figure")
    try:
        figd = figd or figure_in(q)
    except HTTPException:
        raise
    key, order, label, unit = _metric(q, strict=False)
    lines = []
    if figd:
        vals = []
        for c, r in zip(cos, rows):
            v, used, _ = figure_value(cur, r, figd)
            vals.append(v)
        lines.append((figd["label"].capitalize(), figd["unit"], vals))
        lead = max(range(2), key=lambda i: (vals[i] is not None, vals[i] or 0))
        headline = "%s: %s %s against %s for %s." % (
            figd["label"].capitalize(), cos[lead]["name"], _fmt_unit(vals[lead], figd["unit"]), _fmt_unit(vals[1 - lead], figd["unit"]), cos[1 - lead]["name"])
    else:
        standard = [("Revenue", "USD", "size", "revenue"), ("Revenue growth", "%", "metrics", "revenue_growth_pct"),
                    ("Operating margin", "%", "metrics", "operating_margin_pct"), ("Net margin", "%", "metrics", "net_margin_pct"),
                    ("Net income", "USD", "size", "net_income"), ("Return on equity", "%", "metrics", "roe_pct"),
                    ("Market value", "USD", "metrics", "market_cap"), ("P/E", "×", "metrics", "pe_x"), ("Dividend yield", "%", "metrics", "dividend_yield_pct")]
        asked = [s for s in standard if s[3] == key]
        for lab, u, grp, k in (asked + [s for s in standard if s[3] != key]):
            lines.append((lab, u, [r[grp].get(k) for r in rows]))
        lab, u, vals = lines[0]
        lead = max(range(2), key=lambda i: (vals[i] is not None, vals[i] or 0)) if order == "desc" else min(range(2), key=lambda i: (vals[i] is None, vals[i] or 0))
        headline = "%s: %s %s against %s for %s." % (lab, cos[lead]["name"], _fmt_unit(vals[lead], u), _fmt_unit(vals[1 - lead], u), cos[1 - lead]["name"])
    answer = "Each company's latest annual report (%s to %s; %s to %s)%s. Ratios are calculated the same way for both; the formula is under each." % (
        cos[0]["ticker"] or cos[0]["name"], date_words(rows[0]["period_end"]), cos[1]["ticker"] or cos[1]["name"], date_words(rows[1]["period_end"]),
        " and the latest closes" if any(l[0] in ("Market value", "P/E", "Dividend yield") for l in lines) else "")
    table = {"columns": ["Figure", cos[0]["name"], cos[1]["name"]], "rows": [[l[0] + (" (%s)" % l[1] if l[1] not in ("USD",) else ""), l[2][0], l[2][1]] for l in lines], "unit": "USD"}
    chart = None
    first = lines[0]
    if all(v is not None for v in first[2]):
        chart = {"kind": "bars", "title": first[0], "unit": first[1], "series": [{"name": first[0], "points": [[cos[i]["ticker"] or cos[i]["name"][:16], first[2][i]] for i in range(2)]}]}
    calc = dict(sec_metrics.FORMULAS, **sec_metrics.PRICE_FORMULAS)
    return pkg("compare_companies", q, headline, answer, [], table, chart, [SEC_SOURCE, PRICE_SOURCE],
               {k: v for k, v in calc.items() if k in ("revenue_growth_pct", "operating_margin_pct", "net_margin_pct", "roe_pct", "market_cap", "pe_x", "dividend_yield_pct")},
               {"companies": cos}, {"company": company_link(cos[0]), "company_2": company_link(cos[1])})


def _fmt_unit(v, unit):
    if v is None:
        return "—"
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


def _cf_rows(cur, cik, tags, kind):
    """Company-facts rows (the deep-history set, 2009 on) for one company."""
    if not sec_api._has(cur, "sec_cf_facts"):
        return []
    span = ("AND period_end - period_start BETWEEN 350 AND 380" if kind == "duration" else "AND period_start IS NULL")
    return cur.execute("""
        SELECT tag, period_end, value, filed, accn, unit FROM sec_cf_facts
        WHERE cik = ? AND tag IN (%s) AND fp = 'FY' AND form IN ('10-K','10-K/A','20-F','20-F/A','40-F','40-F/A')
          AND unit IN ('USD', 'USD/shares') %s""" % (",".join("?" * len(tags)), span), [cik] + list(tags)).fetchall()


def _fsds_rows(cur, cik, tags, kind):
    """Statement data-set rows: every annual report loaded for the company,
    current year and the comparatives it tagged."""
    adshs = [r[0] for r in cur.execute(
        "SELECT adsh FROM sec_filings WHERE cik = ? AND form IN (%s) AND status IN ('clean','partial')"
        % ",".join("?" * len(sec_api.ANNUAL_FORMS)), [cik] + list(sec_api.ANNUAL_FORMS)).fetchall()]
    if not adshs:
        return []
    return cur.execute("""
        SELECT f.tag, f.ddate, f.value, s.filed, f.adsh, f.uom FROM sec_facts f JOIN sec_filings s USING (adsh)
        WHERE f.adsh IN (%s) AND f.tag IN (%s) AND f.qtrs = ? AND f.segment_id IS NULL AND f.coreg IS NULL
          AND f.uom IN ('USD', 'USD/shares')""" % (",".join("?" * len(adshs)), ",".join("?" * len(tags))),
        adshs + list(tags) + [4 if kind == "duration" else 0]).fetchall()


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
    years = e.get("years") or (None if e.get("since") else 5)
    since = e.get("since")
    per_co, notes = [], []
    for rec in cos:
        head = cur.execute("SELECT max(period) FROM sec_filings WHERE cik = ? AND form IN (%s)"
                           % ",".join("?" * len(sec_api.ANNUAL_FORMS)), [rec["cik"]] + list(sec_api.ANNUAL_FORMS)).fetchone()[0]
        hist = annual_history(cur, rec["cik"], hf, head)
        if since:
            hist = [h for h in hist if h[0] >= since]
        if years:
            hist = hist[-years:]
        per_co.append((rec, hist))
    rec, hist = per_co[0]
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
    if len(hist) == 1:
        headline = "%s %s was %s in %s." % (owner, hf["label"], _fmt_hist(last[1]["value"], unit), _fy(last[0]))
    elif unit == "%":
        d = last[1]["value"] - first[1]["value"]
        headline = "%s %s went from %s in %s to %s in %s, %s %s pp." % (
            owner, hf["label"], pct(first[1]["value"]), _fy(first[0]), pct(last[1]["value"]), _fy(last[0]),
            "up" if d >= 0 else "down", fmt_pp(abs(d)))
    else:
        a, b = first[1]["value"], last[1]["value"]
        verb = "rose" if b >= a else "fell"
        headline = "%s %s %s from %s in %s to %s in %s" % (owner, hf["label"], verb, _fmt_hist(a, unit), _fy(first[0]),
                                                             _fmt_hist(b, unit), _fy(last[0]))
        if a > 0 and b > 0 and n_years > 0:
            cagr = ((b / a) ** (1.0 / n_years) - 1) * 100
            headline += ", %s %s a year on average." % (pct(abs(cagr), 1), "growth" if cagr >= 0 else "decline")
        else:
            headline += "."
    bits = []
    if last[0].month != 12 and len(per_co) == 1:
        bits.append("%s fiscal year ends in %s." % (owner, last[0].strftime("%B")))
    asked = years or (last[0].year - since.year + 1 if since else None)
    if asked and len(hist) < asked:
        bits.append("You asked for %d years; %d %s on file." % (asked, len(hist), "is" if len(hist) == 1 else "are"))
    restated = [h for h in hist if h[1].get("first_value") is not None and h[1]["first_value"] != h[1]["value"]]
    bits.append("Each year shows the latest figure the %s filed for it%s." % ("company" if len(per_co) == 1 else "companies", 
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
                    c["name"], ("%s a year" % pct(g, 1, sign=True)) if g is not None else "no average", _fmt_hist(hh[0][1]["value"], unit),
                    _fy(hh[0][0]), _fmt_hist(hh[-1][1]["value"], unit), _fy(hh[-1][0]))))
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
        scale, ulabel = ((1e9, "$bn") if big >= 1e9 else (1e6, "$mn") if big >= 1e6 else (1.0, "$"))
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
                      for c, hh in per_co for h in reversed(hh)], "unit": "USD" if unit == "USD" else unit}
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
               ["Figures come from each annual report's own statements: SEC company facts for the companies held at full "
                "depth, and the SEC's quarterly data sets (each 10-K carries three years) for the rest."])


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
    return _SUFFIX.sub("", n).strip() or n


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
    (r"cheapest|lowest p/?e|low p/?e|price.to.earnings|p/e", "pe_x", "asc", "P/E", "×"),
    (r"most expensive|highest p/?e", "pe_x", "desc", "P/E", "×"),
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
    if phrase:
        for pat, key, order, label, unit in METRIC_WORDS:
            if re.search(pat, phrase):
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
        else:
            v = r["metrics"].get(key) if key in r["metrics"] else r["size"].get(key)
        if v is None:
            continue
        if key in ("revenue_growth_pct", "profit_growth_pct"):
            # growth from a tiny base leads any list: the year-before revenue must be real money
            prior = r.get("prior") or {}
            if (prior.get("revenue") or 0) < GROWTH_BASE_USD or (key == "profit_growth_pct" and (prior.get("net_income") or 0) < GROWTH_BASE_USD / 5):
                continue
        kept.append((v, r))
    kept.sort(key=lambda x: x[0], reverse=(order == "desc"))
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
                 "rows": [[i + 1, r["ticker"], us_link.display_name(r["name"]), r["industry"], v,
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
                 "rows": [[i + 1, r["ticker"], us_link.display_name(r["name"]), r["industry"], v] + [c[2](r) for c in extra]
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
    return pkg(recipe, q, headline, answer, [fig("Companies qualifying", len(kept), "count", None)], table, chart,
               [SEC_SOURCE, PRICE_SOURCE, COMPANIES_SOURCE], {key: calc.get(key, "as filed")},
               {"metric": key, "order": order, "floor": {"field": field, "value": floor} if field else None, "count": n},
               {"screener": "/api/v1/us/financials/screener?sort=%s&order=%s" % (key, order)},
               rank_notes + ["Companies missing the ranking figure are left out and counted in the API's excluded_missing_sort."],
               period=period_span(*e["period_span"]) if window and e.get("period_span") else None)


def r_industry_rank(cur, q, e):
    codes, label = e["industry"]
    rows = [r for r in metric_rows(cur) if r.get("sic") is not None and any(str(int(r["sic"])).startswith(c) for c in codes)]
    out = _rank(cur, q, e, rows, label, "industry_rank")
    out["entities"]["industry"] = {"codes": codes, "label": label}
    return out


def r_market_rank(cur, q, e):
    return _rank(cur, q, e, metric_rows(cur), "US-listed companies", "market_rank")


def r_insider_buying_market(cur, q, e):
    since = e.get("since") or datetime.date.today().replace(day=1)
    days = max(1, (datetime.date.today() - since).days + 1)
    floor = e.get("money") or 0
    d = call_api(sec_insiders_api.recent, code="P", min_value=floor, days=days, limit=200)
    trades = d["trades"]
    by_issuer = {}
    for t in trades:
        k = (t["issuer_symbol"] or t["issuer_name"], t["issuer_name"])
        by_issuer.setdefault(k, {"value": 0, "trades": 0, "insiders": set()})
        by_issuer[k]["value"] += t["value"] or 0
        by_issuer[k]["trades"] += 1
        by_issuer[k]["insiders"].add(t["owner_name"])
    ranked = sorted(by_issuer.items(), key=lambda kv: -kv[1]["value"])
    if not ranked:
        headline = "No open-market insider purchases%s since %s in the loaded filings." % ((" above %s" % money(floor)) if floor else "", date_words(since))
        return pkg("insider_buying_market", q, headline, "Form 4 filings arrive within two business days of a trade; the loaded quarters are listed under /api/v1/us/insiders/summary.", [], None, None, [INSIDER_SOURCE], {}, {"since": _d(since), "floor": floor}, {})
    top = ranked[0]
    headline = "Insiders bought most at %s since %s: %s across %d purchase%s." % (top[0][1].title(), date_words(since), money(top[1]["value"]), top[1]["trades"], "" if top[1]["trades"] == 1 else "s")
    answer = "%d open-market purchases%s by %d insiders at %d companies. Next: %s. Purchases are transaction code P only; awards and option exercises are not counted." % (
        len(trades), (" above %s" % money(floor)) if floor else "", len({t["owner_name"] for t in trades}), len(ranked),
        "; ".join("%s %s" % (k[1].title(), money(v["value"])) for k, v in ranked[1:5]))
    table = {"columns": ["Date", "Company", "Ticker", "Insider", "Role", "Shares", "Price", "Value", "10b5-1 plan"],
             "rows": [[t["trans_date"], t["issuer_name"], t["issuer_symbol"], t["owner_name"], t.get("title") or t.get("relationship"), t["shares"], t["price"], t["value"], t["plan_10b5_1"]] for t in trades[:100]]}
    chart = {"kind": "bars", "title": "Open-market insider purchases since %s, by company" % date_words(since), "unit": "USD",
             "series": [{"name": "Value bought", "points": [[k[0], v["value"]] for k, v in ranked[:12]]}]}
    return pkg("insider_buying_market", q, headline, answer, [fig("Purchases", len(trades), "count", None), fig("Companies", len(ranked), "count", None)],
               table, chart, [INSIDER_SOURCE], {"value": "shares × the price the filer reported"}, {"since": _d(since), "floor": floor},
               {"api": "/api/v1/us/insiders/recent?code=P&min_value=%d&days=%d" % (floor, days)})


def r_calendar_window(cur, q, e):
    days = e.get("ahead_days") or 14
    ex = ""
    for name in ("nasdaq", "nyse", "otc", "cboe"):
        if name in q.lower():
            ex = name
    rep = "annual" if re.search(r"annual|10-k", q.lower()) else ("quarterly" if re.search(r"quarter|10-q", q.lower()) else "")
    d = call_api(sec_calendar_api.calendar, days=days, exchange=ex, report=rep, limit=2000)
    rows = [r for r in d["rows"] if r["status"] != "filed"]
    big = sorted([r for r in rows if r.get("ticker")], key=lambda r: ((row_for(cur, r["cik"]) or {}).get("metrics", {}).get("market_cap") or 0), reverse=True)
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
        out.append({"ticker": co.get("ticker"), "name": us_link.display_name(co.get("sec_name") or co.get("name")), "cusip": cusip, "shares": sh, "prior_shares": psh,
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
     "patterns": [r"insider", r"10b5", r"executives? (sold|bought|buy|sell)", r"directors? (sold|bought)", r"form 4", r"(buy|bought|sell|sold|selling|buying) (stock|shares)", r"anyone at"], "run": r_company_insiders, "weight": 3},
    {"id": "company_holders", "label": "Who owns a company", "needs": ["company"],
     "patterns": [r"holders?", r"who owns", r"who holds", r"shareholders", r"institution", r"13f", r"added last quarter", r"owners"], "run": r_company_holders, "weight": 3},
    {"id": "company_as_filed_history", "label": "As-filed history", "needs": ["company"],
     "patterns": [r"as filed", r"at the time", r"each of the last", r"which filing", r"annual reports", r"restat", r"originally reported"], "run": r_company_as_filed_history, "weight": 3},
    {"id": "company_calendar", "label": "When a company reports", "needs": ["company"],
     "patterns": [r"report next", r"next report", r"when (does|will|did) .* (report|file)", r"last file", r"filing date", r"earnings date", r"reports? (on|in) "], "run": r_company_calendar, "weight": 3},
    {"id": "company_shareholder_return", "label": "Buybacks and dividends", "needs": ["company"],
     "patterns": [r"buyback", r"repurchas", r"dividend", r"return(ed)? to shareholders", r"yield"], "run": r_company_shareholder_return, "weight": 2},
    {"id": "company_price_performance", "label": "Share price performance", "needs": ["company"],
     "patterns": [r"stock (done|perform|price|fallen|risen)", r"share price", r"performance", r"(chart|graph|plot).*(stock|shares|share price)|(stock|share) (chart|price chart)", r"\bfell\b", r"\brose\b", r"up or down", r"over the past (year|month|week)", r"ytd", r"year to date", r"how has .* done", r"since 20\d\d"], "run": r_company_price_performance, "weight": 2},
    {"id": "company_risks", "label": "A company's risk factors", "needs": ["company"],
     "patterns": [r"\brisks?\b", r"risk factors", r"what could go wrong", r"\bthreats?\b", r"\bworr(y|ies|ied)\b", r"headwinds"],
     "run": r_company_risks, "weight": 4},
    {"id": "company_says", "label": "What a company's annual report says", "needs": ["company"],
     "patterns": [r"\b(say|says|said|mention|mentions|discuss|discusses|disclose|discloses)\b", r"lawsuits?|litigation|legal (cases|proceedings)|\bsued\b|antitrust",
                  r"\bcyber", r"\b10-?k\b", r"annual report", r"\b(competitors|rivals|compete with|suppliers)\b", r"who (are|is) .* (competitors|customers|suppliers)",
                  r"\bheadquarters\b|where is .* (based|located)"],
     "run": r_company_says, "weight": 4},
    {"id": "company_business", "label": "What a company does", "needs": ["company"],
     "patterns": [r"what does .* do\b", r"what (does|do) .* (sell|make|produce|offer)\b", r"what (business|kind of (company|business))",
                  r"\bbusiness (mix|lines?|model)\b", r"lines? of business", r"(make|makes|earn|earns|get|gets) (its|their|most of its) money",
                  r"where does .* (money|revenue|sales) come from", r"revenue (mix|split|breakdown)", r"\bproduct lines?\b",
                  r"tell me about", r"what is .* known for", r"who (is|are) .* customers"], "run": r_company_business, "weight": 4},
    {"id": "company_growth_margin", "label": "Growth and margins", "needs": ["company"],
     "patterns": [r"grow", r"margin", r"profitab", r"revenue", r"sales", r"earnings\b(?! per)", r"net income", r"return on equity", r"\broe\b"], "run": r_company_growth_margin, "weight": 2},
    {"id": "company_value", "label": "What a company is worth", "needs": ["company"],
     "patterns": [r"\bworth\b", r"market (value|cap)", r"valu(e|ation)", r"trades? on", r"p/?e\b", r"price.to.earnings", r"how big", r"multiple"], "run": r_company_value, "weight": 2},
    {"id": "manager_portfolio", "label": "A manager's portfolio", "needs": ["manager"],
     "patterns": [r"what does .* own", r"portfolio", r"positions", r"added or dropped", r"add(ed)? or drop", r"holdings", r"\bown\b", r"\bowns\b", r"bought and sold"], "run": r_manager_portfolio, "weight": 3},
    {"id": "holders_increase_industry", "label": "13F changes in an industry", "needs": ["industry", "no_company"],
     "patterns": [r"13f", r"position increases", r"institutions? (bought|added|increased)", r"managers (added|bought)", r"holders added"], "run": r_holders_increase_industry, "weight": 3},
    {"id": "industry_rank", "label": "Rank an industry", "needs": ["industry", "no_company"],
     "patterns": [r"rank", r"cheapest", r"highest", r"lowest", r"\bmost\b", r"\bbest\b", r"\btop\b", r"largest", r"biggest", r"which", r"\bby\b", r"worst"], "run": r_industry_rank, "weight": 1},
    {"id": "insider_buying_market", "label": "Where insiders are buying", "needs": ["no_company"],
     "patterns": [r"insiders? (are )?buying", r"open.market", r"insider purchases", r"where are insiders", r"insiders? bought"], "run": r_insider_buying_market, "weight": 3},
    {"id": "calendar_window", "label": "Who reports next", "needs": ["no_company"],
     "patterns": [r"report(ing)? (in|over|during) the next", r"reporting next", r"which companies report", r"earnings (this|next) week", r"next (two|2|three) weeks", r"report next week", r"who reports"], "run": r_calendar_window, "weight": 3},
    {"id": "banks_vs_yield", "label": "Bank profits and the 10-year yield", "needs": [],
     "patterns": [r"bank profits", r"banks?.*(10|ten).year", r"yield.*banks?", r"against the (10|ten).year", r"bank earnings .* rates"], "run": r_banks_vs_yield, "weight": 4},
    {"id": "market_rank", "label": "Rank the market", "needs": ["no_company", "no_industry"],
     "patterns": [r"largest", r"biggest", r"\btop\b", r"cheapest", r"highest", r"lowest", r"fell most", r"worst", r"best perform", r"rose most", r"yield", r"\bmost\b", r"\bten\b",
                  r"perform(ed|ing|s)? (the )?(best|worst)", r"winners|losers|top gainers", r"\bwhich (stock|company|companies|stocks)\b"], "run": r_market_rank, "weight": 1},
]
BY_ID = {r["id"]: r for r in RECIPES}

_UNANSWERABLE = [
    (r"\bwhy\b", "I don't explain why a price or a figure moved; I show what the published data says. Ask what happened instead: how the stock has done, what the company reported, who bought or sold."),
    (r"should i|is it a (good )?buy|buy or sell|worth buying|recommend", "I don't give investment advice. I can show the figures people weigh: valuation, growth, who is buying, what insiders are doing."),
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
    manager_words = (re.search(r"\b(portfolio|positions|holdings|added|dropped|manager|fund)\b", ql)
                     or re.search(r"what does .* own|what .* owns?\b", ql)) and not holder_words
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
    "company_history": "one company's (or two companies') filed figure year by year, e.g. 5 years of revenue; also charts of a figure",
    "compare_companies": "two or more companies side by side on value, growth and margins",
    "company_figure": "one filed figure from a company's latest annual report (debt, research spending, gross margin ...)",
    "managers_holding_both": "which 13F managers hold both of two named stocks",
    "company_insiders": "insider buying and selling at one company",
    "company_holders": "who owns a company: the largest 13F holders and who added",
    "company_as_filed_history": "a figure as each annual report first filed it, with the filing",
    "company_calendar": "when a company reports next and last filed",
    "company_shareholder_return": "a company's buybacks and dividends and the yield",
    "company_price_performance": "how one company's share price has done over a period",
    "company_risks": "the risks a company lists in its annual report (its risk factors), or its risks from one thing ('risks to Apple from tariffs')",
    "company_says": "what a company's annual report says about a subject ('what does Apple say about tariffs', 'who are Nvidia's competitors'), its lawsuits and legal cases, cybersecurity, or where its offices and plants are",
    "company_business": "what a company does or sells, its business, what it makes its money from, its revenue mix by product line, segment and region ('what does Nvidia do', 'tell me about Costco')",
    "company_growth_margin": "a company's revenue growth and margins",
    "company_value": "what a company is worth: market value, P/E, yields",
    "manager_portfolio": "what a 13F manager owns, added and dropped",
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
                          "guessing what it follows. Ask the whole question, e.g. \u201cwhat is Microsoft worth\u201d.")}


def answer(cur, question, prev=None):
    """The reader first: a small model fills in the question's form
    (app/us_ask_model.py) and the shape it names runs on the same code as
    a pattern match. When the reader is off or its form cannot be placed,
    the word patterns answer. A refusal from the data (an index whose
    members we do not hold, a figure not filed) is returned as is, never
    swapped for a different question."""
    from . import ask_progress, us_ask_model
    ql = (question or "").lower()
    for pat, msg in _UNANSWERABLE:
        if re.search(pat, ql):
            raise HTTPException(422, msg)
    from . import us_steps
    pointer = us_steps.other_market(question)
    if pointer:
        us_steps.log(question, "other market", pointer)
        raise HTTPException(422, pointer)
    ask_progress.say("Reading your question…")
    form = us_ask_model.read(question, reader_prompt(), prev)
    if form:
        if form.get("refuse") in _REFUSALS and not form.get("shape"):
            raise HTTPException(422, _REFUSALS[form["refuse"]])
        # a follow-up ("and Microsoft?") is answered as the whole question the
        # reader wrote from it and the previous one; the page shows that reading
        whole = str(form.get("question") or "").strip()
        if whole and us_ask_model._norm(whole) != us_ask_model._norm(question) and 2 <= len(whole) <= 300:
            us_steps.log(question, "follow-up read as", whole)
            question, ql = whole, whole.lower()
        e = None
        # a nationality ("Chinese stocks", "Israeli companies") is a filter the
        # ready-made rankings do not have; the step route has a country column
        if form.get("shape") in ("market_rank", "industry_rank") and _NATIONALITY.search(ql):
            form = dict(form, shape="steps")
        # quarterly results, CEO pay and segments live only in the step route's tables (app/us_tables.py)
        if form.get("shape") in _FIGURE_SHAPES and _STEPS_DATA.search(ql):
            form = dict(form, shape="steps")
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
_STEPS_DATA = re.compile(r"\bquarter(ly|s)?\b|\bq[1-4]\b|\bceos?\b.*\bpa(y|id)\b|\bpa(y|id)\b.*\bceos?\b|compensation|\bsalar(y|ies)\b|"
                         r"\bsegments?\b|\bdivisions?\b|revenue from (china|europe|asia|the us|abroad|overseas)", re.I)

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
