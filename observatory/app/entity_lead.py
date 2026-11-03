# -*- coding: utf-8 -*-
"""The opening lines of a one-company page, written by the server.

A company's pages are shells: the name, the numbers and every link to its
other pages arrive from /api after the page loads. A reader who lands from a
search sees the answer only once the scripts have run, and the page holds no
plain link to the same company's other pages, because the section tabs lead
to each page's market view and drop the company. Google read 20,000 such
pages in October 2026 and sent about ten people a week, most of whom left
after one page.

So each one-company page now opens with:

  * two or three plain sentences carrying the figures a reader searched for
    (the largest shareholder and its stake, revenue and profit, the board,
    CEO pay), each as filed, with the year it is for; and
  * one line of links to the same company's other pages, and one to the same
    page for up to five companies in its industry.

Every figure comes from the function that serves the page's own API, so the
lead and the tables under it cannot disagree. Nothing here is calculated:
no rates, no sums, no rankings beyond "the largest" as the filing orders it.
A page whose data is missing gets no sentence rather than a guess, and
nothing here may raise into a request.

The lead names the company it was written for (data-value). When the page
switches company in place, nav.js hides it, so a reader never sees one
company's figures above another's tables.
"""
import re

from . import readable

DASH = "—"

# Each Japanese company page: the address of one company on it, and its name.
# Order is the order of the link line.
JP_PAGES = [
    ("company.html", "company.html?code=%s", "Company Profile"),
    ("ownership.html", "ownership.html?c=%s", "Major Shareholders"),
    ("stakes.html", "stakes.html?c=%s", "5% Shareholders"),
    ("holdings.html", "holdings.html?c=%s", "Cross-Shareholdings"),
    ("governance.html", "governance.html?c=%s", "Board & Pay"),
]
US_PAGES = [
    ("us-company.html", "us-company.html?t=%s", "Company Record"),
    ("us-stock.html", "us-stock.html?t=%s", "Share Price"),
    ("us-pay.html", "us-pay.html?t=%s", "CEO Pay"),
]
PAGES = set(p for p, _, _ in JP_PAGES) | {"us-pay.html"}

PEERS = 5

# Where the lead goes on each page: straight after the element this matches,
# which is the last line of the company's header. company.html has no company
# view of its own until a company is picked, so its lead sits above that.
_ANCHORS = {
    "company.html": (re.compile(r'(?=<div id="profile" hidden>)'), "before"),
    "ownership.html": (re.compile(r'<p class="filing-meta" id="co-filing"></p>'), "after"),
    "holdings.html": (re.compile(r'<p class="filing-meta" id="co-filing"></p>'), "after"),
    "governance.html": (re.compile(r'<p class="filing-meta" id="co-filing"></p>'), "after"),
    "stakes.html": (re.compile(r'<p class="filing-meta" id="co-meta"></p>'), "after"),
    "us-pay.html": (re.compile(r'<p class="cp-msg" id="co-meta"></p>'), "after"),
}

# "（常任代理人 株式会社三菱ＵＦＪ銀行）": the custodian's local agent, which a
# register names after the holder. The holder is the name before it.
_PROXY = re.compile(r"\s*[（(]\s*常任代理人.*$")
# "ウエリントン・…（Wellington Management Company LLP）": the English name a
# filer gives in brackets after its Japanese one.
_ENGLISH_IN_BRACKETS = re.compile(r"[（(]\s*([A-Za-z][^（）()]*?)\s*[）)]\s*$")
_SPACE = re.compile(r"[\s　]+")

_KIND_WORDS = {
    "trust_bank_nominee": "a trust-bank custody account",
    "foreign_nominee": "a custody account",
    "retirement_benefit_trust": "a pension trust",
    "employee_association": "the employee shareholding association",
    "treasury": "treasury shares",
}
_CUSTODY = ("trust_bank_nominee", "foreign_nominee")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))


def _pct(value):
    """A filed percentage as the register prints it: two decimals."""
    return DASH if value is None else readable.fmt(value, 2) + "%"


def _year_to(period_end):
    """"the year to March 2026" — the fiscal year a reader would name."""
    if not period_end:
        return ""
    if isinstance(period_end, str):
        import datetime
        try:
            period_end = datetime.date.fromisoformat(period_end[:10])
        except ValueError:
            return ""
    return "the year to %s" % period_end.strftime("%B %Y")


def _holder_name(row):
    name = row.get("holder_name_en") or row.get("name_raw") or ""
    return _SPACE.sub(" ", _PROXY.sub("", name)).strip()


def _filer_name(raw):
    raw = _SPACE.sub(" ", raw or "").strip()
    m = _ENGLISH_IN_BRACKETS.search(raw)
    return m.group(1).strip() if m else raw


def _yen(value):
    """¥3.8tn, ¥441.4bn, ¥294mn: a sentence's yen, at the scale the figure has."""
    if value is None:
        return DASH
    value = float(value)
    mag = abs(value)
    if mag >= 1e12:
        return "\u00a5" + readable.fmt(value / 1e12, 1) + "tn"
    if mag >= 1e9:
        return "\u00a5" + readable.fmt(value / 1e9, 1) + "bn"
    if mag >= 1e6:
        return "\u00a5" + readable.fmt(value / 1e6, 0) + "mn"
    return "\u00a5" + readable.fmt(value, 0)


def _usd(value):
    if value is None:
        return DASH
    sign = "−" if value < 0 else ""
    return "%s$%sm" % (sign, readable.fmt(abs(float(value)) / 1e6, 1))


# ---------------------------------------------------------------------------
# what each page opens with
# ---------------------------------------------------------------------------

def _largest_holders(code):
    """(largest holder, largest that is not a custody account, register head)."""
    from . import ownership_api
    head = ownership_api.company(code, year="")
    rows = [r for r in head.get("holders") or [] if r.get("ratio_pct") is not None]
    if not rows:
        return None, None, head
    top = rows[0]
    owner = next((r for r in rows if r.get("holder_kind") not in _CUSTODY
                  and r.get("holder_kind") != "treasury"), None)
    return top, owner, head


def _holder_clause(row):
    """"a trust-bank custody account (日本マスタートラスト…), with 20.65% of shares":
    a custody account is named for what it is first, since it holds for others."""
    kind = _KIND_WORDS.get(row.get("holder_kind"))
    name = _holder_name(row)
    who = ("%s (%s)" % (kind, name)) if kind else name
    return "%s, with %s of shares" % (who, _pct(row.get("ratio_pct")))


def _register_sentences(name, code, lead_with_total, subject=None):
    top, owner, head = _largest_holders(code)
    if not top:
        return []
    period = _year_to(head.get("period_end"))
    out = []
    if lead_with_total and head.get("majors_ratio_filed_pct") is not None:
        out.append("The %d largest shareholders of %s own %s of its shares, as filed for %s."
                   % (len(head.get("holders") or []), name,
                      _pct(head["majors_ratio_filed_pct"]), period))
        out.append("The largest is %s." % _holder_clause(top))
    else:
        out.append("%s largest shareholder is %s, as filed for %s."
                   % (subject or name + "'s", _holder_clause(top), period))
    if owner and owner is not top:
        out.append("The largest that is not a custody account is %s." % _holder_clause(owner))
    if lead_with_total and head.get("foreign_pct") is not None:
        out.append("Foreign investors hold %s of all shares." % _pct(head["foreign_pct"]))
    return out


def _financial_sentence(name, code):
    from . import company_api
    doc = company_api.compose(code, datasets=["financials"], limit=200)
    panel = ((((doc.get("datasets") or {}).get("financials") or {})
              .get("tables") or {}).get("panel") or [])
    best = {}
    for row in panel:
        fy = row.get("fiscal_year_end")
        if not fy:
            continue
        rank = ((row.get("source") or {}).get("filed_date") or "",
                1 if row.get("basis") == "consolidated" else 0)
        if fy not in best or rank > best[fy][0]:
            best[fy] = (rank, row)
    years = [best[fy][1] for fy in sorted(best)]
    if not years:
        return None
    now = years[-1].get("values") or {}
    if now.get("revenue") is None or now.get("profit") is None:
        return None
    text = "In %s, %s reported revenue of %s and profit of %s" % (
        _year_to(years[-1]["fiscal_year_end"]), name,
        _yen(now["revenue"]), _yen(now["profit"]))
    if len(years) > 1:
        before = years[-2].get("values") or {}
        if before.get("revenue") is not None and before.get("profit") is not None:
            text += ", against %s and %s a year earlier" % (
                _yen(before["revenue"]), _yen(before["profit"]))
    return text + "."


def _company_lead(name, code):
    out = []
    fin = _financial_sentence(name, code)
    if fin:
        out.append(fin)
    try:
        out.extend(_register_sentences(name, code, lead_with_total=False,
                                       subject="Its" if fin else None))
    except Exception:  # noqa: BLE001 — no register filing: the figures above stand
        pass
    return out


def _ownership_lead(name, code):
    return _register_sentences(name, code, lead_with_total=True)


def _stakes_lead(name, code):
    from . import lvh_api
    s = lvh_api.company(code, history=1)
    groups = s.get("groups") or []
    if not groups:
        return []
    over = [g for g in groups if g.get("is_current")]
    if not over:
        latest = max(groups, key=lambda g: str(g.get("filed_date") or ""))
        return ["No holder reports 5%% or more of %s in its latest filing. The most recent "
                "report on it was filed by %s on %s." % (
                    name, _filer_name(latest.get("filer_name")), _day(latest.get("filed_date")))]
    top = max(over, key=lambda g: g.get("ratio_pct") or 0)
    count = len(over)
    first = ("%d holder%s report%s 5%% or more of %s in %s latest filing%s." % (
        count, "" if count == 1 else "s", "s" if count == 1 else "", name,
        "its" if count == 1 else "their", "" if count == 1 else "s"))
    second = "The largest is %s, with %s of shares, filed %s." % (
        _filer_name(top.get("filer_name")), _pct(top.get("ratio_pct")), _day(top.get("filed_date")))
    return [first, second]


def _day(value):
    from . import prerender
    return prerender._day(value)


def _holdings_lead(name, code, entity):
    from . import equity_api
    out = [entity["description"]] if entity and entity.get("description") else []
    doc = equity_api.company(code)
    held = [h for h in doc.get("holdings") or [] if h.get("book_value_yen") is not None]
    if held:
        big = max(held, key=lambda h: float(h["book_value_yen"]))
        holding = big.get("held_name_en") or big.get("held_name_raw")
        if holding:
            out.append("The largest is %s, at %s book value." % (
                _SPACE.sub(" ", holding).strip(), _yen(big["book_value_yen"])))
    return out


def _governance_lead(name, code):
    from . import governance_api
    g = governance_api.company(code, year="")
    out = []
    period = _year_to(g.get("period_end"))
    if g.get("board_size"):
        text = "%s has %d directors and officers" % (name, g["board_size"])
        if g.get("female_officers") is not None:
            text += ", %d of them women" % g["female_officers"]
        if g.get("avg_director_age") is not None:
            text += ", with an average age of %s" % readable.fmt(g["avg_director_age"], 1)
        out.append(text + ", as filed for %s." % period)
    if g.get("pay_category_total_yen") is not None:
        text = "It paid its officers %s in total" % _yen(g["pay_category_total_yen"])
        named = [p for p in g.get("pay_named") or [] if p.get("consolidated_pay_yen")]
        if named:
            top = max(named, key=lambda p: p["consolidated_pay_yen"])
            text += ("; %d %s disclosed at ¥100mn or more, the highest %s at %s"
                     % (len(named), "person was" if len(named) == 1 else "people were",
                        top.get("name_en") or top.get("person_key"),
                        _yen(top["consolidated_pay_yen"])))
        out.append(text + ".")
    return out


def _us_pay_lead(name, ticker):
    from . import us_pay_api
    d = us_pay_api.company(ticker, basis="latest", as_of="")
    years = [y for y in d.get("years") or [] if (y.get("values") or {}).get("ceo_total_pay") is not None]
    if not years:
        return []
    y = max(years, key=lambda r: r.get("fiscal_year_end") or "")
    v = y["values"]
    text = ("%s paid its chief executive %s in total pay for its fiscal %s, as "
            "reported in its proxy statement" % (
                name, _usd(v["ceo_total_pay"]), _year_to(y["fiscal_year_end"])[4:]))
    out = [text + "."]
    if v.get("ceo_actually_paid") is not None:
        out.append("Compensation actually paid, the SEC's measure with equity awards at fair "
                   "value, was %s." % _usd(v["ceo_actually_paid"]))
    if v.get("neo_avg_total_pay") is not None:
        out.append("Its other named executives averaged %s." % _usd(v["neo_avg_total_pay"]))
    return out


# ---------------------------------------------------------------------------
# where to go next
# ---------------------------------------------------------------------------

def _has_page(page, query):
    """True when that address serves something: the page's own view says so."""
    from . import prerender
    meta = prerender.entity_meta(page, query)
    return bool(meta) and not meta.get("noindex")


def _jp_links(page_name, code):
    out = []
    for page, template, label in JP_PAGES:
        if page == page_name:
            continue
        if page == "company.html":
            ok = _has_page(page, "code=" + code)
        else:
            ok = _has_page(page, "c=" + code)
        if ok:
            out.append((label, template % code))
    return out


def _jp_peers(page_name, code):
    """Up to five companies in the same JPX 33-industry, nearest in TOPIX size
    band first, each with this same page."""
    from . import cohorts, equity_api
    cur = equity_api._cur()
    if not cohorts.available(cur):
        return None, []
    info = cohorts.classify(cur, code)
    if not info or not info.get("ind33_code"):
        return None, []
    v = cohorts.vintage(cur, "jpx-listed")
    rows = cur.execute(
        "SELECT sec_code, size_code FROM eq_classification "
        "WHERE vintage_id = ? AND ind33_code = ? AND sec_code <> ?",
        [v[0], info["ind33_code"], code]).fetchall()
    order = ["core30", "large70", "mid400", "small1", "small2", None]

    def band(size):
        return order.index(size) if size in order else len(order)
    mine = band(info.get("size_code"))
    rows.sort(key=lambda r: (abs(band(r[1]) - mine), r[0]))
    template = dict((p, t) for p, t, _ in JP_PAGES)[page_name]
    query = "code=%s" if page_name == "company.html" else "c=%s"
    from . import prerender
    peers = []
    for sec_code, _ in rows[:40]:
        meta = prerender.entity_meta(page_name, query % sec_code)
        if not meta or meta.get("noindex") or not meta.get("h1"):
            continue
        peers.append((meta["h1"], template % sec_code))
        if len(peers) >= PEERS:
            break
    industry = None
    try:
        industry = equity_api.INDUSTRY_EN.get(info.get("ind33_name"))
    except Exception:  # noqa: BLE001
        industry = None
    return industry, peers


def _us_links(page_name, ticker):
    return [(label, template % ticker) for page, template, label in US_PAGES
            if page != page_name]


def _us_peers(ticker):
    """Up to five S&P 500 members with the same SEC industry code, nearest in
    index weight first."""
    from . import sec_api
    cur = sec_api._cur()
    rows = cur.execute("""
        WITH snap AS (SELECT max(snapshot_id) AS id FROM us_index_snapshots
                      WHERE index_name = 'S&P 500 (SPY holdings)'),
             m AS (SELECT m.cik, m.ticker, m.weight_pct, c.industry
                   FROM us_index_members m JOIN snap ON m.snapshot_id = snap.id
                   LEFT JOIN sec_companies c ON c.cik = m.cik),
             me AS (SELECT * FROM m WHERE upper(ticker) = ? LIMIT 1)
        SELECT m.ticker, m.cik, me.industry FROM m, me
        WHERE m.industry = me.industry AND m.cik <> me.cik AND m.industry IS NOT NULL
        ORDER BY abs(coalesce(m.weight_pct, 0) - coalesce(me.weight_pct, 0)), m.ticker
        LIMIT 12""", [ticker]).fetchall()
    from . import prerender
    peers, industry = [], None
    for peer, _, ind in rows:
        industry = ind
        meta = prerender.entity_meta("us-pay.html", "t=" + peer)
        if not meta or meta.get("noindex") or not meta.get("h1"):
            continue
        peers.append((meta["h1"], "us-pay.html?t=%s" % peer))
        if len(peers) >= PEERS:
            break
    return (industry.title() if industry else None), peers


# ---------------------------------------------------------------------------
# the block
# ---------------------------------------------------------------------------

def _subject(page_name, query):
    """(key, value) naming the company in this address, normalised as the page does."""
    if page_name == "us-pay.html":
        t = (query.get("t") or "").strip().upper().replace(".", "-")
        return ("t", t) if t else None
    code = (query.get("code") or query.get("c") or "").strip().upper()
    if page_name != "company.html":
        code = (query.get("c") or "").strip().upper()
    return ("c", code) if code else None


def build(page_name, query, entity):
    """{sentences, links, peers, peer_label, keys, value} for one address, or None."""
    if page_name not in PAGES or not entity or entity.get("noindex"):
        return None
    subject = _subject(page_name, query)
    if not subject:
        return None
    _, value = subject
    name = entity.get("h1")
    if not name:
        return None
    from . import prerender
    version = prerender._sec_version if page_name == "us-pay.html" else prerender._equity_version

    def make():
        if page_name == "us-pay.html":
            sentences = _us_pay_lead(name, value)
            links = _us_links(page_name, value)
            peer_label, peers = _us_peers(value)
        else:
            sentences = {
                "company.html": lambda: _company_lead(name, value),
                "ownership.html": lambda: _ownership_lead(name, value),
                "stakes.html": lambda: _stakes_lead(name, value),
                "holdings.html": lambda: _holdings_lead(name, value, entity),
                "governance.html": lambda: _governance_lead(name, value),
            }[page_name]()
            links = _jp_links(page_name, value)
            try:
                peer_label, peers = _jp_peers(page_name, value)
            except Exception:  # noqa: BLE001 — no classification: no peer line
                peer_label, peers = None, []
        if not sentences and not links:
            return None
        return {"sentences": sentences, "links": links, "peers": peers,
                "peer_label": peer_label, "name": name, "value": value,
                "keys": "t" if page_name == "us-pay.html" else
                        ("code c" if page_name == "company.html" else "c")}
    return prerender._cached(("lead", page_name, value), version, make)


def html(lead):
    if not lead:
        return ""
    parts = ['<div class="entity-lead" data-entity-lead data-keys="%s" data-value="%s">'
             % (_esc(lead["keys"]), _esc(lead["value"]))]
    if lead["sentences"]:
        parts.append('<p class="entity-lead-text">%s</p>'
                     % _esc(" ".join(lead["sentences"])))
    if lead["links"]:
        parts.append('<p class="entity-lead-links"><span class="entity-lead-label">More on %s</span>%s</p>'
                     % (_esc(lead["name"]), "".join('<a href="%s">%s</a>' % (_esc(h), _esc(t))
                                                    for t, h in lead["links"])))
    if lead["peers"]:
        label = "Same industry" + (" (%s)" % lead["peer_label"] if lead.get("peer_label") else "")
        parts.append('<p class="entity-lead-links"><span class="entity-lead-label">%s</span>%s</p>'
                     % (_esc(label), "".join('<a href="%s">%s</a>' % (_esc(h), _esc(t))
                                             for t, h in lead["peers"])))
    parts.append("</div>")
    return "".join(parts)


def inject(page, page_name, query, entity):
    """The page with its lead written in, or the page unchanged. Never raises."""
    try:
        anchor = _ANCHORS.get(page_name)
        if not anchor:
            return page
        block = html(build(page_name, query, entity))
        if not block:
            return page
        pattern, where = anchor
        m = pattern.search(page)
        if not m:
            return page
        at = m.start() if where == "before" else m.end()
        return page[:at] + block + page[at:]
    except Exception:  # noqa: BLE001 — a page without its lead beats no page
        return page
