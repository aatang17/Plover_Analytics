"""Plover Assistant (beta): a chat panel that answers from the page's own data and works the page.

The question box (us_ask) writes a separate answer from its own readers, and on
the Japanese equity pages it cannot read what the page shows: on 2026-11-05 it
answered 5 of 30 fund-analyst questions on Boards & Pay, Shareholder Register and
5% Filings, and none of the ten about boards and pay. This is the other way
round. The model only chooses which of the page's own views answers the
question (a screen, a company, the trend chart, the market summary); the reply
is written here from the same functions that serve the page's tables, so every
number in the chat is a number the page shows, and the panel then sets the page
to that view.

One page so far: governance (Boards & Pay). A page is a PAGES entry: its plan
prompt, its word rules for when the model is off, and its executor.

Questions are kept, with the reader's network and country as the visit log keeps
them, in data/analytics/pagechat-YYYY-MM.jsonl (append-only, like the task log),
so the Traffic page can say what a firm's reader asked.
"""
import datetime
import json
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request

from . import geoip, governance_api, visits
from .jp_names import display_name

router = APIRouter(prefix="/api/v1/page-chat", tags=["Plover Assistant"])

MAX_QUESTION = 300
PLAN_SECONDS = 8
SOURCE_GOV = ("Source: annual securities reports (有価証券報告書) via EDINET. Board counts and pay "
              "totals as filed; average age, ratios and pay per officer are calculated.")

# ---- formatting: the page's own conventions (governance.js yenM, pct, count) ----

MINUS = "−"


def _num(v, dp=0):
    if v is None:
        return "—"
    s = "{:,.{dp}f}".format(abs(float(v)), dp=dp)
    return (MINUS + s) if float(v) < 0 else s


def _yen_m(v, dp=0):
    return "—" if v is None else "¥" + _num(float(v) / 1e6, dp) + "m"


def _pct_ratio(v):
    return "—" if v is None else _num(float(v) * 100, 1) + "%"


def _name(r):
    """As the page writes it: 'TOEI ANIMATION CO.,LTD.' -> 'Toei Animation Co., Ltd.'."""
    en = r.get("filer_name_en") or r.get("name_en")
    return display_name(en) if en else (r.get("filer_name") or r.get("name") or r.get("sec_code"))


def _period(iso):
    """'2026-02-28' -> 'February 2026'."""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return d.strftime("%B %Y")


# ---- the Boards & Pay page ---------------------------------------------------

GOV_SCREENS = governance_api.SCREENS
GOV_MEASURES = {
    "avg_director_age": ("Average director age", "years", 1, 1.0),
    "directors_70_plus_pct": ("Directors aged 70 or over", "%", 1, 1.0),
    "female_officer_pct": ("Female officers", "%", 1, 1.0),
    "median_pay_per_officer_yen": ("Median pay per officer", "¥m", 1, 1e6),
    "median_employee_salary_yen": ("Median employee salary", "¥m", 2, 1e6),
}
GOV_TOPICS = ("overview", "board", "women", "age", "pay", "named", "salary", "gender_gap",
              "history", "independent")

GOV_PROMPT = """You choose which view of a web page answers a reader's question. Reply with one JSON object and nothing else.

The page is Boards & Pay: board and pay data from the annual securities reports of Japanese companies (about 3,800 filers, each company's latest filing, five years of history). It holds: each company's board members (names, titles, ages, shares held), number of women, average director age, directors aged 70+, officer pay by category, individuals paid ¥100m or more by name, average employee salary, the female-to-male wage ratio; market-wide ranked screens; a five-year trend of board measures; a market summary.
It does NOT hold: which directors are independent or outside, committee membership, share prices, voting results, ownership, or companies outside Japan.

Actions:
{"action":"screen","metric":M,"listed":true}  rank companies. M is one of: %(screens)s
{"action":"company","company":"<company name as the reader means it, e.g. Toyota Motor, Seven & i Holdings, or a 4-character code>","topic":T}  one company. T is one of: overview, board, women, age, pay, named, salary, gender_gap, history, independent
{"action":"top_paid"}  the highest-paid named individuals across Japan
{"action":"trend","measure":X}  how a board measure changed over five years. X is one of: %(measures)s
{"action":"summary"}  market-wide figures (how many companies, median board size, share of women, boards with no women)
{"action":"none","missing":"<what the reader wants that this page does not hold, in a few plain words>"}

Rules:
- listed is false only when the reader asks about unlisted companies too.
- A question about a CEO's or one executive's pay at one company is company/named; about pay of the whole board is company/pay.
- "independent directors" or "outside directors" for one company is company/independent; across the market it is none.
- When the reader is on a company's view and does not name another company, the company is that one.
- A short follow-up ("and Sony?", "what about women") continues the previous question.
"""


def _gov_prompt():
    return GOV_PROMPT % {
        "screens": ", ".join("%s (%s)" % (k, v[2]) for k, v in sorted(GOV_SCREENS.items())),
        "measures": ", ".join("%s (%s)" % (k, v[0]) for k, v in sorted(GOV_MEASURES.items())),
    }


# Used when the model is off, over its daily cap or slow: plain word patterns.
_GOV_WORDS = (
    (r"\b(no|without|zero|any)\s+(wom[ae]n|female)", ("screen", "no_women")),
    (r"\b(gender|wage)\s+(pay\s+)?gap", ("screen", "widest_gender_pay_gap")),
    (r"\b(most|highest|share of|ratio of)\s+(wom[ae]n|female)", ("screen", "most_female")),
    (r"\byoungest\b", ("screen", "youngest_boards")),
    (r"\b(oldest|eldest)\s+board", ("screen", "oldest_boards")),
    (r"\b(70|seventy)\b", ("screen", "oldest_directors")),
    (r"\b(largest|biggest)\s+boards?\b", ("screen", "largest_boards")),
    (r"\bper\s+(officer|director)\b", ("screen", "highest_pay_per_officer")),
    (r"\b(employee|staff)\s+(pay|salar)|\bsalar(y|ies)\b", ("screen", "highest_employee_pay")),
    (r"\b(highest|top|best)[- ]paid\s+(executives?|ceos?|people|individuals?|directors?|bosses)"
     r"|\b100\s*m|\bover\s+¥?\s*100", ("top_paid", None)),
    (r"\b(highest|largest|biggest)\s+(total\s+)?(board|officer)\s+(pay|remuneration)", ("screen", "highest_paid_boards")),
)


def _gov_words(q, company):
    low = q.lower()
    for pattern, (action, arg) in _GOV_WORDS:
        if re.search(pattern, low):
            if action == "screen":
                if company and arg in ("no_women", "most_female"):
                    return {"action": "company", "company": company, "topic": "women"}
                return {"action": "screen", "metric": arg, "listed": True}
            return {"action": action}
    if company:
        for word, topic in (("independent", "independent"), ("outside", "independent"),
                            ("wom", "women"), ("female", "women"), ("age", "age"), ("old", "age"),
                            ("ceo", "named"), ("paid", "pay"), ("pay", "pay"), ("remuneration", "pay"),
                            ("salar", "salary"), ("board", "board"), ("director", "board"),
                            ("history", "history"), ("changed", "history")):
            if word in low:
                return {"action": "company", "company": company, "topic": topic}
        return {"action": "company", "company": company, "topic": "overview"}
    return None


def _find_company(name):
    """The filer a reader means by `name`: a code, an exact English name, a name
    that starts with it, then any match; listed companies before delisted."""
    name = (name or "").strip()
    if not name:
        return None
    if re.match(r"^[0-9][0-9A-Z]{3}$", name.upper()):
        rows = governance_api.companies(q=name.upper())["companies"]
        hit = [r for r in rows if r.get("sec_code") == name.upper()]
        if hit:
            return hit[0]
    variants = [name]
    trimmed = re.sub(r"\b(co\.?,?|ltd\.?|inc\.?|corp(oration)?\.?|holdings?|group|k\.?k\.?)\b", "", name, flags=re.I)
    trimmed = re.sub(r"[\s,.]+", " ", trimmed).strip()
    if trimmed and trimmed.lower() != name.lower():
        variants.append(trimmed)
    for v in variants:
        rows = governance_api.companies(q=v)["companies"]
        if not rows:
            continue
        low = v.lower()

        def rank(r):
            en = (r.get("name_en") or "").lower()
            core = re.sub(r"[\s,.]+(co|ltd|inc|corporation|corp|holdings?|group)\b.*$", "", en).strip()
            return (bool(r.get("delisted")),
                    0 if core == low else 1 if en.startswith(low) else 2,
                    len(en))
        return sorted(rows, key=rank)[0]
    return None


def _gov_screen(plan):
    metric = plan.get("metric")
    if metric not in GOV_SCREENS:
        return None
    listed = plan.get("listed") is not False
    d = governance_api.screen(metric=metric, year="", listed="true" if listed else "", cohort="", limit=50)
    rows = d["rows"]
    key = {"oldest_boards": ("avg_director_age", "Avg age (years)", lambda r: _num(r.get("avg_director_age"), 1)),
           "youngest_boards": ("avg_director_age", "Avg age (years)", lambda r: _num(r.get("avg_director_age"), 1)),
           "no_women": ("board_size", "Board", lambda r: _num(r.get("board_size"))),
           "most_female": ("female_ratio_filed", "Female officers", lambda r: _pct_ratio(r.get("female_ratio_filed"))),
           "largest_boards": ("board_size", "Board", lambda r: _num(r.get("board_size"))),
           "oldest_directors": ("directors_70_plus", "Aged 70+", lambda r: _num(r.get("directors_70_plus"))),
           "highest_paid_boards": ("pay_category_total_yen", "Officer pay", lambda r: _yen_m(r.get("pay_category_total_yen"))),
           "highest_pay_per_officer": ("pay_per_officer_yen", "Per officer", lambda r: _yen_m(r.get("pay_per_officer_yen"), 1)),
           "highest_employee_pay": ("avg_salary_yen", "Avg salary", lambda r: _yen_m(r.get("avg_salary_yen"), 2)),
           "widest_gender_pay_gap": ("gender_pay_gap_all", "Female-to-male wage ratio",
                                     lambda r: _num(r.get("gender_pay_gap_all"), 3))}[metric]
    scope = "listed companies" if listed else "filers, listed and unlisted"
    if not rows:
        reply = "No %s match this screen." % scope
    else:
        top = rows[0]
        reply = "%s, among %s. First: %s (%s)." % (d["title"], scope, _name(top), key[2](top))
        if metric == "no_women":
            s = governance_api.summary(year="", listed="true" if listed else "")
            if s.get("boards_with_no_women") is not None:
                reply = "%s %s have no women on the board. Largest first: %s (%s members)." % (
                    _num(s["boards_with_no_women"]), scope, _name(top), _num(top.get("board_size")))
    table = {"columns": ["Company", key[1]],
             "rows": [[{"text": _name(r), "href": "governance.html?c=" + r["sec_code"]}, key[2](r)] for r in rows[:5]]}
    return {"reply": reply + " The full list of %d is in Governance Screens on this page." % len(rows),
            "table": table, "answered": bool(rows),
            "page_action": {"type": "screen", "metric": metric, "listed": listed},
            "follow": ["Open %s" % _name(rows[0])] if rows else []}


def _gov_company(plan):
    hit = _find_company(plan.get("company"))
    if not hit:
        return {"reply": "I couldn't find “%s” among the companies on this page. Try its name as "
                         "filed in English or its four-character code." % (plan.get("company") or ""),
                "answered": False}
    code = hit["sec_code"]
    try:
        d = governance_api.company(sec_code=code, year="")
    except HTTPException:
        return {"reply": "%s has no extracted board or pay filing." % _name(hit), "answered": False}
    name = _name(d)
    topic = plan.get("topic") if plan.get("topic") in GOV_TOPICS else "overview"
    when = "as filed for the year to %s" % _period(d.get("period_end"))
    section, table, answered = "board", None, True
    if topic == "independent":
        reply = ("This page doesn't mark which directors are independent: the filings' tagged board table "
                 "gives names, titles and ages, not independence. %s's board has %s members, %s." % (
                     name, _num(d.get("board_size")), when))
        answered = False
    elif topic == "women":
        reply = "%s has %s women among %s board members (%s filed), %s." % (
            name, _num(d.get("female_officers")), _num(d.get("board_size")),
            _pct_ratio(d.get("female_ratio_filed")), when)
    elif topic == "age":
        reply = "%s's directors average %s years; %s are aged 70 or over, %s." % (
            name, _num(d.get("avg_director_age"), 1), _num(d.get("directors_70_plus")), when)
    elif topic == "pay":
        section = "pay"
        cats = [c for c in d.get("pay_by_category") or [] if not c["category_key"].startswith("OfWhich")]
        table = {"columns": ["Category", "Officers", "Total"],
                 "rows": [[re.sub(r"([A-Za-z])(\d+)$", r"\1 \2", c.get("category_label_en") or "—"),
                           _num(c.get("headcount")), _yen_m(c.get("total_yen"))]
                          for c in cats[:5]]}
        reply = "%s paid its officers %s in total, %s." % (name, _yen_m(d.get("pay_category_total_yen")), when)
    elif topic == "named":
        section = "named"
        named = sorted(d.get("pay_named") or [], key=lambda p: -(p.get("consolidated_pay_yen") or 0))
        if named:
            table = {"columns": ["Person", "Consolidated pay"],
                     "rows": [[p.get("name_en") or p.get("name_ja"), _yen_m(p.get("consolidated_pay_yen"))]
                              for p in named[:5]]}
            reply = ("%s names %d people paid ¥100m or more. Highest: %s, %s (consolidated, including pay "
                     "from group companies), %s." % (name, len(named), named[0].get("name_en") or named[0].get("name_ja"),
                                                     _yen_m(named[0].get("consolidated_pay_yen")), when))
        else:
            section = "pay"
            reply = ("%s names nobody paid ¥100m or more, so no one's individual pay is disclosed. "
                     "Officers were paid %s in total, %s." % (name, _yen_m(d.get("pay_category_total_yen")), when))
    elif topic == "salary":
        reply = "%s's employees earn %s on average (company only, %s employees), %s." % (
            name, _yen_m(d.get("avg_salary_yen"), 2), _num(d.get("employees_company")), when)
    elif topic == "gender_gap":
        reply = "At %s, women earn %s for every ¥1 men earn (all workers), %s." % (
            name, "¥" + _num(d.get("gender_pay_gap_all"), 3) if d.get("gender_pay_gap_all") is not None else "—", when)
    elif topic == "history":
        section = "history"
        reply = "%s's five-year record of board size, age, women and pay is in Five-Year History on its page." % name
    else:
        reply = "%s: %s board members, %s women, average age %s; officers paid %s in total, %s." % (
            name, _num(d.get("board_size")), _num(d.get("female_officers")),
            _num(d.get("avg_director_age"), 1), _yen_m(d.get("pay_category_total_yen")), when)
    if topic == "board" or topic == "independent":
        people = d.get("board") or []
        table = {"columns": ["Director", "Age"],
                 "rows": [[p.get("name_en") or p.get("name_ja"), _num(p.get("age_at_period_end"))] for p in people[:5]]}
        if topic == "board":
            reply = "%s's board has %s members, %s women, average age %s, %s. The first five as filed:" % (
                name, _num(d.get("board_size")), _num(d.get("female_officers")),
                _num(d.get("avg_director_age"), 1), when)
    return {"reply": reply, "table": table, "answered": answered,
            "page_action": {"type": "company", "code": code, "section": section},
            "follow": []}


def _gov_top_paid(plan):
    d = governance_api.named(year="", listed="true", limit=5, min_yen=0)
    rows = d.get("rows") or []
    if not rows:
        return {"reply": "No individually disclosed pay is on file.", "answered": False}
    top = rows[0]
    return {"reply": "The highest-paid named individual is %s of %s: %s (consolidated, including pay from group "
                     "companies, latest filing). Japanese filings name anyone paid ¥100m or more." % (
                         top.get("name_en"), _name(top), _yen_m(top.get("consolidated_pay_yen"))),
            "table": {"columns": ["Person", "Company", "Pay"],
                      "rows": [[r.get("name_en"), {"text": _name(r), "href": "governance.html?c=" + r["sec_code"]},
                                _yen_m(r.get("consolidated_pay_yen"))] for r in rows]},
            "answered": True, "page_action": None, "follow": []}


def _gov_trend(plan):
    key = plan.get("measure")
    if key not in GOV_MEASURES:
        key = "female_officer_pct"
    label, unit, dp, scale = GOV_MEASURES[key]
    d = governance_api.trend(years_back=5, listed="true")
    pts = [(r["year"], r.get(key)) for r in d.get("series") or [] if r.get(key) is not None]
    if len(pts) < 2:
        return {"reply": "Not enough years on file to show a trend.", "answered": False}
    (y0, v0), (y1, v1) = pts[0], pts[-1]
    fmt = (lambda v: _num(v / scale, dp) + ("%" if unit == "%" else " years" if unit == "years" else ""))
    if unit == "¥m":
        fmt = (lambda v: "¥" + _num(v / scale, dp) + "m")
    reply = "%s: %s in %s, %s in %s, for the same %s listed companies every year." % (
        label, fmt(v0), y0, fmt(v1), y1, _num(d.get("panel_companies")))
    return {"reply": reply, "table": None, "answered": True,
            "page_action": {"type": "trend", "measure": key}, "follow": []}


def _gov_summary(plan):
    s = governance_api.summary(year="", listed="true")
    reply = ("%s listed companies: median board %s members, directors average %s years, %s of officers are women, "
             "and %s boards have no women. Each company's latest filing." % (
                 _num(s.get("companies")), _num(s.get("median_board_size")), _num(s.get("avg_director_age"), 1),
                 _num(s.get("avg_female_officer_pct"), 1) + "%", _num(s.get("boards_with_no_women"))))
    return {"reply": reply, "table": None, "answered": True,
            "page_action": {"type": "section", "section": "summary"}, "follow": []}


def _gov_none(plan):
    missing = (plan.get("missing") or "that").strip().rstrip(".")
    return {"reply": "This page doesn't hold %s. It has each Japanese company's board members, ages, women "
                     "on the board, officer pay and anyone paid ¥100m or more, from annual reports." % missing,
            "table": None, "answered": False, "page_action": None, "follow": []}


GOV_RUN = {"screen": _gov_screen, "company": _gov_company, "top_paid": _gov_top_paid,
           "trend": _gov_trend, "summary": _gov_summary, "none": _gov_none}

PAGES = {
    "governance": {"prompt": _gov_prompt, "words": _gov_words, "run": GOV_RUN, "source": SOURCE_GOV},
}


# ---- planning ------------------------------------------------------------------

def _model_plan(page, q, company_name, prev):
    """The model's choice of view, or None when it is off, capped, slow or unreadable."""
    from . import us_ask_model
    cfg = us_ask_model.provider()
    if cfg is None or not us_ask_model.under_cap():
        return None
    prov, key, model = cfg
    us_ask_model._tick("calls")
    where = ("The reader is on the page of %s." % company_name) if company_name else \
        "The reader is on the market view (no company open)."
    user = where + ("\nPrevious question: %s" % prev if prev else "") + "\nQuestion: %s" % q
    box = {}

    def call():
        try:
            box["got"] = us_ask_model._complete(prov, key, model, [
                {"role": "system", "content": PAGES[page]["prompt"]()},
                {"role": "user", "content": user}],
                max_tokens=120, timeout=PLAN_SECONDS, cache_key="plover-page-chat-" + page)
        except Exception as exc:                                  # noqa: BLE001 — the word rules take over
            box["failed"] = exc
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(PLAN_SECONDS + 1)
    got = box.get("got")
    if not got:
        return None
    m = re.search(r"\{.*\}", got.get("text") or "", re.S)
    try:
        plan = json.loads(m.group(0)) if m else None
    except ValueError:
        plan = None
    if isinstance(plan, dict) and plan.get("action") in PAGES[page]["run"]:
        us_ask_model._tick("matched", got.get("usage"))
        return plan
    return None


def plan_for(page, q, company_name=None, prev=None):
    """(plan, how): how is 'model' or 'words'."""
    plan = _model_plan(page, q, company_name, prev)
    if plan:
        if plan.get("action") == "company" and not plan.get("company") and company_name:
            plan["company"] = company_name
        return plan, "model"
    plan = PAGES[page]["words"](q, company_name)
    return plan or {"action": "none", "missing": "an answer to that"}, "words"


# ---- the route ---------------------------------------------------------------------

_LOG_LOCK = threading.Lock()


def _log(request, page, q, company, plan, out, how, seconds):
    """Kept as the visit log keeps a reader: daily visitor id, country and network."""
    try:
        scope = request.scope
        headers = [(k.lower(), v) for k, v in scope.get("headers") or ()]
        user_agent = visits._header(headers, b"user-agent")
        ip = visits._client_ip(scope, visits._header(headers, b"x-forwarded-for"))
        geoip.prepare()
        country, network = geoip.lookup(ip)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        day = now.strftime("%Y-%m-%d")
        line = {"at": now.isoformat(timespec="seconds") + "Z", "page": page, "q": q,
                "company": company or None, "action": plan.get("action"),
                "answered": bool(out.get("answered")), "how": how, "seconds": round(seconds, 1),
                "visitor": visits.identify(ip, user_agent, headers, day)[0],
                "country": country, "network": network,
                "from": "us" if visits._is_ours(ip, headers) else
                        "machine" if visits._is_bot(user_agent) else "reader"}
        if visits._signed_in(headers):
            line["member"] = True
        visits.ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, open(str(visits.ANALYTICS_DIR / ("pagechat-%s.jsonl" % day[:7])), "a",
                             encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:                                      # noqa: BLE001 — logging never breaks a reply
        print("PAGE CHAT LOG WRITE FAILED (%r)" % exc)


@router.get("")
def ask(request: Request,
        page: str = Query(..., description="which page: governance"),
        q: str = Query(..., description="the reader's question"),
        c: str = Query("", description="the company open on the page, by code"),
        prev: str = Query("", description="the previous question, for follow-ups")):
    """Answer from the page's own data and say which view of the page shows it."""
    if page not in PAGES:
        raise HTTPException(404, "Plover Assistant is not on that page yet.")
    q = (q or "").strip()[:MAX_QUESTION]
    if not q:
        raise HTTPException(400, "Type a question.")
    t0 = time.time()
    company_name = None
    if c:
        try:
            company_name = _name(governance_api.company(sec_code=c.strip(), year=""))
        except HTTPException:
            company_name = None
    plan, how = plan_for(page, q, company_name, (prev or "").strip()[:MAX_QUESTION] or None)
    try:
        out = PAGES[page]["run"][plan["action"]](plan) or PAGES[page]["run"]["none"]({})
    except HTTPException:
        out = {"reply": "That part of the page's data is not available on this server right now. "
                        "The page's own tables still work.", "answered": False}
    out.setdefault("table", None)
    out.setdefault("page_action", None)
    out.setdefault("follow", [])
    out["source"] = PAGES[page]["source"]
    out["plan"] = {"action": plan.get("action")}
    _log(request, page, q, c.strip() or None, plan, out, how, time.time() - t0)
    return out
