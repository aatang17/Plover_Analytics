# -*- coding: utf-8 -*-
"""Market value of a US company: which share count, and how it is priced.

Market value is close × shares outstanding. The count comes from the cover of
the company's newest 10-K or 10-Q (equity/sec_cover_shares.py), the figure the
company itself states a few weeks before filing. Three cases:

  one count     the company files one figure: count × the close of its
                primary ticker.
  share classes the company states a count per class (Alphabet A/B/C, Meta
                A/B, Berkshire A/B). Each class is valued at the close of the
                ticker its own cover lists for it. A class with no ticker is
                valued through the listed class it converts into, at the ratio
                in CONVERSIONS — each entry cites the company's own filing.
                A class that is neither listed nor in CONVERSIONS leaves the
                market value blank: a part-valued company would rank wrong.
  no cover      no cover count is on file: the year-end balance-sheet count
                from the annual report, as before, and the response says so.

Whatever the count, a foreign company's US price is usually per American
Depositary Share, and one of those can stand for any number of the shares
the filing counts (three for HDFC Bank, eight for Alibaba, 4,000 for
Scinai). equity/sec_ads_ratios.py reads that number from each foreign
filer's own filing; here the count is divided by it before it meets the
price, and the P/E multiplies the per-share earnings by it. A 20-F filer
whose filing has not been read, or whose filing names a depositary share
without stating the ratio, gets no market value and a note, never a guess.
A company with several share classes and a depositary-share listing is
valued with every class at that one price (equal economic rights assumed),
which the response says.

SUCCESSORS links a company that became a subsidiary of a new holding company
to its successor: the old filer's annual report keeps supplying the
financials, priced on the successor's ticker and share count, until the
successor files an annual report of its own.
"""
import datetime
import re
import threading

from . import asof

# Old filer CIK -> the holding company that replaced it on the exchange.
SUCCESSORS = {
    34088: {"cik": 2115436, "effective": datetime.date(2026, 7, 1), "ratio": 1.0,
            "source": ("Exxon Mobil Corporation 8-K, 1 July 2026: each share was exchanged for one share "
                       "of ExxonMobil Holdings Corp, which took the ticker XOM.")},
}

# How an unlisted share class is valued, from the company's own 10-K:
#   CIK -> {class: (listed class it converts into, or None for the one listed
#   class; shares of that class per share; source)}.
# Class names are the XBRL member's local name, lower-case ("commonclassb").
# A ratio of 0 is a class with no economic rights (votes only): it counts for
# nothing. A class whose filing tags CommonStockConversionRate (Visa) needs no
# entry here: the filer's own rate, read from the same filing, is used first.
# Every entry was checked against the wording quoted, in the 10-K named.
_ONE = 1.0
CONVERSIONS = {
    1559720: {"commonclassb": (None, _ONE, "Airbnb 10-K 2026-02-12: each Class B share convertible into one share of Class A"),
              "commonclassh": (None, _ONE, "Airbnb 10-K 2026-02-12: Class H converts into Class A on a share-for-share basis")},
    91142: {"commonclassa": (None, _ONE, "A. O. Smith 10-K 2026-02-10: Class A convertible to Common Stock one for one")},
    1751008: {"commonclassb": (None, _ONE, "AppLovin 10-K 2026-02-19: one Class B share convertible into one share of Class A")},
    1166691: {"commonclassb": (None, _ONE, "Comcast 10-K 2026-02-03: Class B convertible share for share into Class A")},
    1679788: {"commonclassb": (None, _ONE, "Coinbase 10-K 2026-02-12: Class B convertible into Class A on a one-to-one basis")},
    1792789: {"commonclassb": (None, _ONE, "DoorDash 10-K 2026-02-18: each Class B share converts into one share of Class A")},
    1561550: {"commonclassb": (None, _ONE, "Datadog 10-K 2026-02-18: each Class B share convertible into one share of Class A")},
    1571996: {"commonclassa": (None, _ONE, "Dell 10-K 2026-03-16: Class A and Class B convertible into Class C one-to-one"),
              "commonclassb": (None, _ONE, "Dell 10-K 2026-03-16: Class A and Class B convertible into Class C one-to-one")},
    1415404: {"commonclassb": (None, _ONE, "EchoStar 10-K 2026-03-02: each Class B and Class C share convertible into one share of Class A"),
              "commonclassc": (None, _ONE, "EchoStar 10-K 2026-03-02: each Class B and Class C share convertible into one share of Class A")},
    1001250: {"commonclassb": (None, _ONE, "Estee Lauder 10-K 2026-08-19: one Class A share for each Class B share converted")},
    922621: {"commonclassb": (None, 2400.0, "Erie Indemnity 10-K 2026-02-23: Class B convertible into Class A at 2,400 to 1")},
    1324424: {"commonclassb": (None, _ONE, "Expedia 10-K 2026-02-13: Class B convertible into common stock one for one")},
    37996: {"commonclassb": (None, _ONE, "Ford 10-K 2026-02-11: Class B may be converted into an equal number of Common Stock")},
    1652044: {"commonclassb": ("us-gaap:CommonClassAMember", _ONE,
                               "Alphabet 10-K 2026-02-05: rights of Class A, B and C identical except voting; B converts to A")},
    1783879: {"commonclassb": (None, _ONE, "Robinhood 10-K 2026-02-18: each Class B share convertible into one share of Class A")},
    47111: {"commonclassb": (None, _ONE, "Hershey 10-K 2026-02-17: Class B convertible into Common Stock share-for-share")},
    1141391: {"commonclassb": (None, _ONE, "Mastercard 10-K 2026-02-11: Class B convertible one-for-one into Class A")},
    1326801: {"commonclassb": (None, _ONE, "Meta 10-K 2026-01-29: Class B convertible into an equivalent number of Class A")},
    320187: {"commonclassa": (None, _ONE, "Nike 10-K 2026-07-15: each Class A share convertible into one share of Class B")},
    1321655: {"commonclassb": (None, _ONE, "Palantir 10-K 2026-02-17: rights of Class A, B and F identical except voting and conversion"),
              "commonclassf": (None, _ONE, "Palantir 10-K 2026-02-17: rights of Class A, B and F identical except voting and conversion")},
    1713445: {"commonclassb": (None, _ONE, "Reddit 10-K 2026-02-06: each Class B share converts into one share of Class A")},
    872589: {"commonclassa": (None, _ONE, "Regeneron 10-K 2026-02-04: Class A convertible into Common Stock share-for-share")},
    1037038: {"commonclassb": (None, _ONE, "Ralph Lauren 10-K 2026-05-21: Class B convertible into Class A one-for-one")},
    1063761: {"commonclassb": (None, _ONE, "Simon Property 10-K 2026-02-25: Class B converts into an equal number of common stock")},
    16918: {"convertiblecommonstock": (None, _ONE, "Constellation Brands 10-K 2026-04-22: Class 1 convertible into Class A one-to-one")},
    100493: {"commonclassb": (None, _ONE, "Tyson Foods 10-K 2025-11-10: Class B convertible into Class A share-for-share")},
    352915: {"commonclassa": (None, _ONE, "Universal Health 10-K 2026-02-25: Class A, C and D convertible share for share into Class B"),
             "commonclassc": (None, _ONE, "Universal Health 10-K 2026-02-25: Class A, C and D convertible share for share into Class B"),
             "commonclassd": (None, _ONE, "Universal Health 10-K 2026-02-25: Class A, C and D convertible share for share into Class B")},
    1090727: {"commonclassa": (None, _ONE, "UPS 10-K 2026-02-17: each class A share convertible into one share of class B")},
    1327811: {"commonclassb": (None, _ONE, "Workday 10-K 2026-03-06: each Class B share can be converted into a share of Class A")},
    1512673: {"commonclassb": (None, _ONE, "Block 10-K 2026-02-26: Class B convertible into an equivalent number of Class A")},
    1973266: {"commonclassb": (None, 0.0, "TKO 10-K 2026-02-25: Class B common stock does not have economic rights")},
    1690820: {"commonclassb": (None, 0.0, "Carvana 10-K 2026-02-18: Class B gets no dividends and nothing on liquidation")},
    1176948: {"commonclassb": (None, 0.0, "Ares 10-K 2026: Class B and Class C common stock are non-economic"),
              "commonclassc": (None, 0.0, "Ares 10-K 2026: Class B and Class C common stock are non-economic"),
              "nonvotingcommonstock": (None, _ONE, "Ares 10-K 2026: non-voting common stock has the same economic rights as Class A")},
    1467373: {"commonclassx": (None, 0.0, "Accenture 10-K 2026-10-16: Class X gets no dividends and nothing on a winding-up")},
}

# An unlisted class with no known ratio, smaller than this share of all the
# company's shares, is left out with a note (Charter's one Class B share,
# Interactive Brokers' 100). Erie's 2,542 Class B shares are 0.006% of its
# count and worth 13% of it — which is why the bar is this low and why Erie
# has its ratio above.
NEGLIGIBLE = 1e-5

_LOCK = threading.Lock()
_MEMO = {}


def _norm(t):
    return (t or "").strip().upper().replace("-", ".").replace("/", ".").replace(" ", ".")


def _has(cur, table):
    return cur.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]).fetchone()[0] > 0


def merge_successors(rows):
    """Re-key a replaced filer's metrics row to its successor, in place of a
    successor that has no annual report yet; drop it once the successor has one."""
    by_cik = {r["cik"]: r for r in rows}
    out = []
    for r in rows:
        s = SUCCESSORS.get(r["cik"])
        if s is None:
            out.append(r)
            continue
        if s["cik"] in by_cik:
            continue                      # the successor reports for itself now
        out.append(dict(r, cik=s["cik"], sec_code="cik:%d" % s["cik"],
                        succession={"from_cik": r["cik"], "effective": s["effective"].isoformat(),
                                    "note": s["source"]}))
    return out


def _counts(cur):
    """{cik: {"rows": [counts of its newest cover filing], "rates": {class:
    rate}}} — memoised per database version and as-of ceiling. Under a
    ceiling only filings filed by then count."""
    from . import sec_api
    key = (sec_api.file_version(), asof.current())
    with _LOCK:
        hit = _MEMO.get(key)
    if hit is not None:
        return hit
    out = {}
    if _has(cur, "sec_cover_shares"):
        ceiling = asof.current()
        rows = cur.execute("""
            WITH k AS (
                SELECT c.cik, c.accn, c.end_date, c.class_key, c.ticker, c.shares, c.source,
                       coalesce(f.filed, s.filed, CAST(c.first_seen AS DATE)) AS known,
                       coalesce(f.form, s.form) AS form
                FROM sec_cover_shares c
                LEFT JOIN sec_cover_filings f ON f.accn = c.accn
                LEFT JOIN sec_filings s ON s.adsh = c.accn
                WHERE c.shares IS NOT NULL AND c.end_date <= current_date),
            filings AS (
                SELECT cik, accn, max(end_date) AS end_date, bool_or(source = 'instance') AS inst
                FROM k WHERE (CAST(? AS DATE) IS NULL OR known <= ?) GROUP BY cik, accn),
            pick AS (
                SELECT cik, accn, rn FROM (
                    SELECT cik, accn, row_number() OVER (
                        PARTITION BY cik ORDER BY end_date DESC, inst DESC, accn DESC) AS rn
                    FROM filings) WHERE rn <= 2)
            SELECT k.cik, pick.rn, k.accn, k.end_date, k.class_key, k.ticker, k.shares, k.source, k.known, k.form
            FROM k JOIN pick USING (cik, accn)
            ORDER BY k.cik, pick.rn, k.class_key""", [ceiling, ceiling]).fetchall()
        for cik, rn, accn, end, ck, tk, n, src, known, form in rows:
            v = out.setdefault(cik, {"rows": [], "prev": [], "rates": {}})
            v["rows" if rn == 1 else "prev"].append(
                {"accn": accn, "end": end, "class": ck, "ticker": tk, "shares": n, "source": src,
                 "filed": known, "form": form})
        # a filing read from its instance supersedes the frames row for the same accession
        for cik, v in out.items():
            for part in ("rows", "prev"):
                if any(r["source"] == "instance" for r in v[part]):
                    v[part] = [r for r in v[part] if r["source"] == "instance"]
        if _has(cur, "sec_cover_rates"):
            for cik, accn, ck, rate in cur.execute(
                    "SELECT cik, accn, class_key, rate FROM sec_cover_rates WHERE rate > 0").fetchall():
                v = out.get(cik)
                if v and v["rows"][0]["accn"] == accn:
                    v["rates"][ck] = rate
    with _LOCK:
        _MEMO.clear()
        _MEMO[key] = out
    return out


_ADS_MEMO = {}


def _ads(cur):
    """{cik: the newest listing row read for it (equity/sec_ads_ratios.py):
    listing 'ads' | 'direct' | 'unknown', ratio, title, quote, source, form,
    filed}; memoised per database version and as-of ceiling."""
    from . import sec_api
    key = (sec_api.file_version(), asof.current())
    with _LOCK:
        hit = _ADS_MEMO.get(key)
    if hit is not None:
        return hit
    out = {}
    if _has(cur, "sec_ads_ratios"):
        ceiling = asof.current()
        rows = cur.execute("""
            SELECT r.cik, r.accn, r.listing, r.ratio, r.title, r.quote, r.source, f.filed, f.form
            FROM sec_ads_ratios r LEFT JOIN sec_ads_filings f USING (accn)
            WHERE CAST(? AS DATE) IS NULL OR f.filed IS NULL OR f.filed <= ?
            ORDER BY r.cik, f.filed DESC NULLS LAST, r.first_seen DESC""", [ceiling, ceiling]).fetchall()
        for cik, accn, listing, ratio, title, quote, source, filed, form in rows:
            out.setdefault(cik, {"accn": accn, "listing": listing, "ratio": ratio, "title": title, "quote": quote,
                                 "source": source, "filed": filed, "form": form})
    with _LOCK:
        _ADS_MEMO.clear()
        _ADS_MEMO[key] = out
    return out


def _close(closes, ticker):
    """(date, close) for a ticker as a filing writes it: BRK.B, BRK-B, or
    Brown-Forman's BFB for BF.B when no symbol BFB trades."""
    if not ticker:
        return None
    t = _norm(ticker)
    hit = closes.get(t)
    if hit is None and "." not in t and len(t) >= 3:
        hit = closes.get(t[:-1] + "." + t[-1])
    return (hit[0], hit[1]) if hit else None


def _local(member):
    m = member.split(":", 1)[-1]
    return (m[:-6] if m.endswith("Member") else m).lower()


def _share_classes(rs):
    """Share classes only: BlackRock's cover also counts subsidiary units and a fully diluted total."""
    return [r for r in rs if r["class"] and "=" not in r["class"] and r["shares"] > 0
            and "unit" not in _local(r["class"]) and "fullydiluted" not in _local(r["class"])]


def _total(rs):
    whole = [r["shares"] for r in rs if not r["class"]]
    return whole[0] if whole else sum(r["shares"] for r in _share_classes(rs))


def _unexplained_jump(cur, row, hit):
    """'from X to Y shares' when the newest count moved more than JUMP times
    against the filing before it and no split in the price history explains
    it (a count tagged in the wrong scale), else None."""
    prev, now = _total(hit["prev"]), _total(hit["rows"])
    if not prev or not now:
        return None
    q = now / prev
    if 1.0 / JUMP <= q <= JUMP:
        return None
    from . import us_prices_api
    since = hit["prev"][0]["end"]
    f = 1.0
    for sym, events in us_prices_api.split_events(cur).items():
        if _norm(sym) == _norm(row.get("ticker")):
            for d, ratio in events:
                if d > since:
                    f *= ratio
    if 1.0 / 3 <= q / f <= 3:
        return None
    return "from {:,.0f} to {:,.0f} shares".format(prev, now)


JUMP = 5.0       # a count that moves more than this between two filings needs a split to explain it


def basis(cur, row, closes):
    """How this row's market value is formed, or None to fall back to the
    balance-sheet count. `closes` is {normalised symbol: (date, close, close_1y)}.

    Returns {"market_cap", "shares" (the filing's own count), "listed_shares"
    (that count ÷ shares per listed share), "per_listed_share", "listing",
    "shares_date", "accn", "form", "filed", "method", "classes", "note",
    "expect", "caution"}. market_cap is None when a class cannot be valued or
    the listing cannot be read: "note" then says why in one plain sentence and
    "expect" when it will fix itself. A value that is shown but rests on a
    count to read with care carries "caution" (and "expect")."""
    cik = row["cik"]
    foreign = (row.get("form") or "").startswith("20-F")
    ads = _ads(cur).get(cik)
    per, listing = 1.0, "direct"
    out = {"shares_date": None, "accn": None, "form": None, "filed": None, "source_url": None, "classes": [],
           "note": None, "expect": None, "caution": None, "per_listed_share": 1.0, "listing": None, "listed_shares": None}
    if ads is not None:
        out["listing"] = {"listing": ads["listing"], "ratio": ads["ratio"], "title": ads["title"], "quote": ads["quote"],
                          "source": ads["source"], "form": ads["form"],
                          "filed": ads["filed"].isoformat() if ads["filed"] else None}
        units = re.search(r"(\d[\d,]*|[a-z]+)\s+units?\b", "%s %s" % (ads["title"] or "", ads["quote"] or ""), re.I)
        if units and (ads["listing"] == "unknown" or not ads["ratio"]):
            out.update(method="held back", shares=None, market_cap=None,
                       note=("Each US share stands for %s units, and a unit is a bundle of different share classes, so "
                             "how many company shares lie behind one US share is not on file." % units.group(1)),
                       expect="It stays blank until the make-up of a unit is on file.")
            return out
        if ads["listing"] == "ads" and not ads["ratio"]:
            out.update(method="held back", shares=None, market_cap=None,
                       note=("Its US shares are depositary shares, and its annual report does not say how many company "
                             "shares each one stands for."),
                       expect="It stays blank until a later annual report states it.")
            return out
        if ads["listing"] == "unknown":
            out.update(method="held back", shares=None, market_cap=None,
                       note=("Its annual report does not make clear whether the US shares are the company's own shares or "
                             "depositary shares standing for several of them."),
                       expect="It stays blank until a later annual report makes it clear.")
            return out
        if ads["listing"] == "ads":
            per, listing = float(ads["ratio"]), "ads"
    elif foreign:
        out.update(method="held back", shares=None, market_cap=None,
                   note=("Its annual report has not been read yet for how many company shares one US share stands for."),
                   expect="The next data refresh reads it.")
        return out
    out["per_listed_share"] = per
    ads_note = ("priced per depositary share, each standing for %s shares (%s)" % (
        ("%d" % per) if per == int(per) else ("%g" % per), ads["source"])) if listing == "ads" else None
    primary = _close(closes, row.get("ticker"))

    def done(n, method, notes, caution=None, **extra):
        out.update(method=method, shares=n, listed_shares=(n / per) if n is not None else None,
                   market_cap=(primary[1] * n / per) if (primary and n is not None) else None, **extra)
        if n is not None and not primary:
            notes = notes + ["no recent close for %s" % row.get("ticker")]
        if ads_note:
            notes = notes + [ads_note]
        out["note"] = "; ".join(x for x in notes if x) or None
        out["caution"] = caution
        if out["market_cap"] is not None and caution is None:
            out["caution"], out["expect"] = stale_count(row, out.get("shares_date"))
        return out

    hit = _counts(cur).get(cik)
    if not hit or not hit["rows"]:
        n = (row.get("per_share") or {}).get("shares_outstanding")
        if not n or n <= 0:
            return out if ads is not None else None
        caution, expect = stale_count(row, row.get("period_end"))
        out["expect"] = expect
        return done(n, "balance sheet", ["no cover-page count on file; the year-end balance-sheet count is used"],
                    caution=caution or ("No recent share count is on file, so the count at the fiscal year end (%s) is "
                                        "used." % _words(row.get("period_end"))),
                    shares_date=row.get("period_end"))
    rs, rates = hit["rows"], hit["rates"]
    ratio = 1.0
    succ = row.get("succession")
    if succ:
        ratio = SUCCESSORS[succ["from_cik"]]["ratio"]
    first = rs[0]
    out.update(shares_date=first["end"].isoformat(), accn=first["accn"], form=first["form"],
               filed=first["filed"].isoformat() if first["filed"] else None,
               source_url="https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, first["accn"].replace("-", "")))
    # A cover count ten, a hundred or a thousand times the balance-sheet count (or a tenth
    # of it) with no split in the price history is a scale slip on the cover (Alibaba's 20-F
    # cover tagged 1.86bn shares against 18.58bn on its balance sheet, 2026-10-19): the
    # balance-sheet count is used and the response says so.
    bs = (row.get("per_share") or {}).get("shares_outstanding")
    first_n = sum(r["shares"] for r in rs if r["shares"] is not None) or None
    if bs and first_n and _power_of_ten_off(first_n / bs) and not _split_between(cur, row, rs):
        return done(bs, "balance sheet", ["the cover count (%s) is %s the balance-sheet count (%s) and no split explains "
                                          "it, so the balance-sheet count is used" % (
                                              "{:,.0f}".format(first_n), _power_of_ten_off(first_n / bs), "{:,.0f}".format(bs))],
                    caution=("The share count on its latest filing's cover (%s) is %s the count on its balance sheet (%s), and "
                             "no split explains the gap, so the balance-sheet count is used." % (
                                 _big(first_n), _power_of_ten_off(first_n / bs), _big(bs))),
                    shares_date=row.get("period_end"))
    jump = _unexplained_jump(cur, row, hit)
    if jump:
        out.update(method="held back", shares=None, market_cap=None,
                   note=("Its share count moved %s between its last two filings, and no split in the share price "
                         "explains that." % jump),
                   expect="It returns with the next 10-Q or 10-K if that filing confirms the new count.")
        return out
    classes = _share_classes(rs)
    whole = [r for r in rs if not r["class"]]
    listed = [r for r in classes if r["ticker"]]
    # One figure for the whole company, unless two classes trade at their own prices
    if len(classes) <= 1 or (whole and len(listed) < 2):
        r = whole[0] if whole else (classes[0] if classes else None)
        if r is None:
            return None
        return done(r["shares"] * ratio, "one count", [])
    # A depositary-share listing, or a 20-F filer: every class at the one listed price
    if listing == "ads" or foreign:
        if whole and whole[0]["shares"]:
            # the filer's own total beside its classes (Youdao: 120mn, then 32mn Class A and 88mn Class B)
            return done(whole[0]["shares"] * ratio, "one count", [])
        counted = [r for r in classes if r["shares"] is not None
                   and not any(w in _local(r["class"]) for w in ("adr", "ads", "depositary", "depository", "warrant",
                                                                 "preference", "preferred"))]
        # a plain "common stock" member that equals the other classes together is the total, not a class
        # (Youdao: CommonStock 120mn beside Class A 32mn and Class B 88mn)
        plain = [r for r in counted if _local(r["class"]) in ("commonstock", "ordinaryshares", "commonshares")]
        if len(plain) == 1 and len(counted) > 1:
            rest = sum(r["shares"] for r in counted if r is not plain[0])
            if rest and abs(plain[0]["shares"] / rest - 1) <= 0.02:
                return done(plain[0]["shares"] * ratio, "one count", [])
        return done(sum(r["shares"] for r in counted) * ratio, "all classes, one price",
                    ["%d share classes counted at the listed share's price, equal economic rights assumed" % len(counted)])
    conv = CONVERSIONS.get(cik, {})
    by_member = {r["class"]: r for r in listed}
    total_shares = sum(r["shares"] for r in classes)
    value, missing, skipped = 0.0, [], []
    for r in classes:
        px, via = _close(closes, r["ticker"]), None
        if px is None and r["ticker"] and len(listed) == 1 and primary:
            px = primary                       # the one listed class, under a ticker since changed (PSKY -> SKYD)
        if px is None and not r["ticker"]:
            rule = None
            if r["class"] in rates and len(listed) == 1:
                rule = (None, rates[r["class"]], "the company's own tagged conversion rate, same filing")
            elif _local(r["class"]) in conv:
                rule = conv[_local(r["class"])]
            if rule is not None:
                into, k, src = rule
                target = by_member.get(into) if into else (listed[0] if len(listed) == 1 else None)
                tpx = None
                if target is not None:
                    tpx = _close(closes, target["ticker"]) or (primary if len(listed) == 1 else None)
                if k == 0:
                    px, via = (None, 0.0), {"ratio": 0.0, "source": src}
                elif tpx:
                    px, via = (tpx[0], tpx[1] * k), {"into": _label(target["class"]), "ticker": target["ticker"],
                                                     "ratio": k, "source": src}
            elif r["shares"] < NEGLIGIBLE * total_shares:
                skipped.append(r)
                continue
        item = {"class": _label(r["class"]), "ticker": r["ticker"], "shares": r["shares"] * ratio,
                "close": px[1] if px else None,
                "close_date": str(px[0])[:10] if px and px[0] is not None else None, "converted": via}
        if px is None:
            missing.append(r["class"])
            item["value"] = None
        else:
            item["value"] = r["shares"] * ratio * px[1]
            value += item["value"]
        out["classes"].append(item)
    n = sum(c["shares"] for c in out["classes"])
    out.update(method="share classes", shares=n, listed_shares=n, market_cap=None if missing else value)
    notes = []
    if missing:
        notes.append("One of its share classes (%s) has no market price of its own, and how it converts into a listed "
                     "class is not on file" % ", ".join(_label(c) for c in missing))
        out["expect"] = "It stays blank until those conversion terms are on file."
    if skipped:
        notes.append("left out as negligible: %s" % ", ".join(
            "%s (%s shares)" % (_label(r["class"]), "{:,.0f}".format(r["shares"])) for r in skipped))
    out["note"] = ("; ".join(notes) + ".") if notes else None
    if not missing and value:
        out["caution"], out["expect"] = stale_count(row, out.get("shares_date"))
    return out


def _words(d):
    """'2025-06-30' -> '30 June 2025'."""
    try:
        d = datetime.date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return str(d)
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def _big(n):
    return ("%.2fbn" % (n / 1e9)) if n >= 1e9 else ("%.0fmn" % (n / 1e6)) if n >= 1e6 else "{:,.0f}".format(n)


STALE_COUNT_DAYS = 400


def stale_count(row, shares_date, today=None):
    """(caution, expect) when the share count behind a shown market value is
    more than STALE_COUNT_DAYS old, else (None, None). Shares issued, bought
    back or consolidated since are not in it (Elevra Lithium's count predated
    its consolidation, 2026-10-19). The next annual report's due date is the
    fiscal year end plus a year plus the filing window: four months for a
    20-F, ninety days for a 10-K."""
    today = today or datetime.date.today()
    try:
        d = datetime.date.fromisoformat(str(shares_date)[:10])
    except (TypeError, ValueError):
        return None, None
    if (today - d).days <= STALE_COUNT_DAYS:
        return None, None
    caution = ("Its share count is from %s, more than a year ago; shares issued, bought back or consolidated since then "
               "are not reflected, so this value may be out of date." % _words(d))
    try:
        fye = datetime.date.fromisoformat(str(row.get("period_end"))[:10])
    except (TypeError, ValueError):
        return caution, "It updates when the company files its next annual report."
    nxt = fye.replace(year=fye.year + 1) if not (fye.month == 2 and fye.day == 29) else fye.replace(year=fye.year + 1, day=28)
    if (row.get("form") or "").startswith("20-F"):
        # four calendar months after the year end, to the month's last day
        y, mth = nxt.year + (nxt.month + 4 - 1) // 12, (nxt.month + 4 - 1) % 12 + 1
        due = datetime.date(y + (mth == 12), (mth % 12) + 1, 1) - datetime.timedelta(days=1)
    else:
        due = nxt + datetime.timedelta(days=90)
    if due >= today:
        return caution, "It updates with the next annual report, due by %s." % _words(due)
    return caution, ("It updates with the next annual report, which was due by %s and has not reached us yet." % _words(due))


# ---- splits: does the company's own share count bear out a split read from prices?
_SPLIT_MEMO = {}


def _cover_totals(cur):
    """{cik: [(count date, total shares)]} from every cover count on file,
    oldest first; memoised per database version."""
    from . import sec_api
    key = sec_api.file_version()
    with _LOCK:
        hit = _SPLIT_MEMO.get(key)
    if hit is not None:
        return hit
    out = {}
    if _has(cur, "sec_cover_shares"):
        for cik, end, n in cur.execute("""
                SELECT cik, end_date, sum(shares) FROM sec_cover_shares
                WHERE shares IS NOT NULL AND (class_key = '' OR class_key NOT LIKE '%=%')
                GROUP BY cik, accn, end_date ORDER BY cik, end_date""").fetchall():
            out.setdefault(cik, []).append((end, n))
    with _LOCK:
        _SPLIT_MEMO.clear()
        _SPLIT_MEMO[key] = out
    return out


def split_evidence(cur, cik, day, ratio):
    """'confirmed' when the company's share count changed by about the split
    ratio across `day`, 'contradicted' when it barely changed, 'unconfirmed'
    when no count after the day is on file yet."""
    counts = _cover_totals(cur).get(cik) or []
    before = [n for d, n in counts if d < day]
    after = [n for d, n in counts if d >= day]
    if not before or not after or not before[-1]:
        return "unconfirmed"
    q = after[0] / before[-1]
    if abs(q / ratio - 1) <= 0.25:
        return "confirmed"
    if abs(q - 1) <= 0.25 and abs(ratio - 1) > 0.5:
        return "contradicted"
    return "unconfirmed"


def split_sentence(cur, cik, day, ratio):
    """One plain sentence on a split read from the price series, saying how
    far the company's own share count bears it out."""
    kind = ("%d-for-1 split" % round(ratio)) if ratio >= 1 else ("1-for-%d reverse split" % round(1 / ratio))
    move = ("fell to a %s of the day before" % _fraction(ratio)) if ratio >= 1 else ("rose %d-fold overnight" % round(1 / ratio))
    status = split_evidence(cur, cik, day, ratio) if cik else "unconfirmed"
    if status == "confirmed":
        return ("Earlier closes are adjusted for the %s on %s, which the company's own share count confirms."
                % (kind, _words(day)), status)
    if status == "contradicted":
        return ("On %s the close %s, but the company's share count did not change, so it was not a split (a spin-off or "
                "a special payout is likelier). Earlier closes are still adjusted as if it were, so the change shown "
                "across that date is wrong." % (_words(day), move), status)
    return ("On %s the close %s. The price series reads that as a %s and adjusts earlier closes for it, but no filing "
            "confirms a split yet; if it was a spin-off or a special payout instead, the change shown across that date "
            "is wrong. It is checked again when the company next reports its share count."
            % (_words(day), move, kind), status)


def _fraction(r):
    words = {2: "half", 3: "third", 4: "quarter", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 10: "tenth"}
    return words.get(int(round(r)), "1/%d" % round(r))


def cautions(cur, rows=None, since_days=400):
    """{ticker: short plain clause} for every company whose figures need
    reading with care: a market value held back, a share count over a year
    old or replaced by the balance-sheet count, or a split read from prices
    in the last `since_days` that no filing confirms."""
    from . import stock_recipes, us_prices_api
    rows = rows if rows is not None else stock_recipes.all_metric_rows(cur)
    out = {}
    cik_of, ticker_of = {}, {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        cik_of[_norm(t)] = r["cik"]
        ticker_of[r["cik"]] = t
        b = r.get("market_value_basis") or {}
        if b.get("method") == "held back" or (b.get("note") and (r.get("metrics") or {}).get("market_cap") is None
                                              and (r.get("price") or {}).get("close")):
            out[t] = "no market value: %s" % _lower_first(b.get("note") or "its share count could not be confirmed")
        elif b.get("caution"):
            out[t] = _lower_first(b["caution"])
    cutoff = datetime.date.today() - datetime.timedelta(days=since_days)
    for sym, events in us_prices_api.split_events(cur).items():
        for d, ratio in events:
            if d < cutoff:
                continue
            cik = cik_of.get(_norm(sym))
            if cik is None:
                continue
            status = split_evidence(cur, cik, d, ratio)
            if status != "confirmed":
                tk = ticker_of.get(cik, sym)
                clause = ("a %s-fold overnight %s on %s is treated as a %s, which no filing %s" % (
                    round(ratio) if ratio >= 1 else round(1 / ratio), "fall" if ratio >= 1 else "rise", _words(d),
                    "split" if ratio >= 1 else "reverse split", "confirms" if status == "unconfirmed" else "bears out"))
                out[tk] = (out[tk] + "; " + clause) if tk in out else clause
    return out


def _lower_first(text):
    t = (text or "").strip().rstrip(".")
    return t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t


_HOLDERS_MEMO = {}
UNCORROBORATED_MAX_SHARE = 0.001      # institutions holding under 0.1% of the value a count implies
UNCORROBORATED_MIN_VALUE = 3e9


def institutional_value(cur):
    """{cik: value of its shares the 13F filers reported holding in the latest
    window}, with a manager whose implied price is twenty times off the
    others' (a units slip) left out; memoised per database version."""
    from . import sec_api, us_link
    key = sec_api.file_version()
    with _LOCK:
        hit = _HOLDERS_MEMO.get(key)
    if hit is not None:
        return hit
    out = {}
    if _has(cur, "sec_13f_holdings") and _has(cur, "sec_13f_batches"):
        import statistics
        batch = cur.execute("SELECT batch FROM sec_13f_batches WHERE status = 'ok' ORDER BY window_end DESC LIMIT 1").fetchone()
        if batch:
            per = {}
            for cusip, acc, v, sh in cur.execute("SELECT cusip, accession, sum(value), sum(shares) FROM sec_13f_holdings "
                                                 "WHERE batch = ? AND shares > 0 AND value > 0 GROUP BY 1, 2", [batch[0]]).fetchall():
                per.setdefault(cusip, []).append((v, sh))
            clean = {}
            for cusip, lst in per.items():
                med = statistics.median([v / sh for v, sh in lst])
                clean[cusip] = sum(v for v, sh in lst if med > 0 and 1 / 20 <= (v / sh) / med <= 20)
            by_cusip = us_link._build_cusips_memo(cur).get("by_cusip") or {}
            for cusip, rec in by_cusip.items():
                cik = rec["cik"] if isinstance(rec, dict) else rec[0]
                out[cik] = out.get(cik, 0.0) + clean.get(cusip, 0.0)
    with _LOCK:
        _HOLDERS_MEMO.clear()
        _HOLDERS_MEMO[key] = out
    return out


def hold_back_uncorroborated(cur, rows):
    """A market value above $3bn that institutional holders' reports put at
    under 0.1% is not believed when the filing offers no second count
    (Lion Group's 20-F cover tagged 150bn Class A shares, $417bn at the close,
    with no 13F holder of note, 2026-10-19). The value is held back with a
    note; a company with a balance-sheet count has the power-of-ten check
    instead, and a company no 13F filer names at all is left alone."""
    held = institutional_value(cur)
    n = 0
    for r in rows:
        b = r.get("market_value_basis") or {}
        cap = b.get("market_cap")             # this pass's own figure, never a shared one
        if not cap or cap < UNCORROBORATED_MIN_VALUE or b.get("method") not in ("one count", "all classes, one price"):
            continue
        if (r.get("per_share") or {}).get("shares_outstanding"):
            continue
        v = held.get(r["cik"])
        if v is None or v / cap >= UNCORROBORATED_MAX_SHARE:
            continue
        b.update(method="held back", market_cap=None, caution=None,
                 note=("Its share count implies a value of %s, yet funds report holding almost none of it (under 0.1%%), "
                       "so the count is probably out of date or mis-scaled." % ("${:,.0f}bn".format(cap / 1e9))),
                 expect="It returns when a later filing gives a count that funds' holdings bear out.")
        m = r["metrics"]
        m.update(market_cap=None, buyback_yield_pct=None, dividend_yield_pct=None)
        n += 1
    return n


def _power_of_ten_off(q):
    """'10x', '1/100th' ... when q is a power of ten (not one) within 3%, else None."""
    for k, word in ((10, "10x"), (100, "100x"), (1000, "1,000x"), (0.1, "a tenth of"), (0.01, "a hundredth of"), (0.001, "a thousandth of")):
        if abs(q / k - 1) <= 0.03:
            return word
    return None


def _split_between(cur, row, rs):
    """A split in the price history between the balance-sheet date and the cover date."""
    from . import us_prices_api
    try:
        pe = datetime.date.fromisoformat(str(row.get("period_end"))[:10])
    except (TypeError, ValueError):
        return False
    ends = [r["end"] for r in rs if r.get("end")]
    if not ends:
        return False
    lo, hi = min(pe, min(ends)), max(pe, max(ends))
    for d, ratio in (us_prices_api.split_events(cur).get(_norm(row.get("ticker") or "")) or []):
        if lo <= d <= hi:
            return True
    return False


def _label(member):
    """us-gaap:CommonClassBMember -> CommonClassB."""
    m = member.split(":", 1)[-1]
    return m[:-6] if m.endswith("Member") else m


def health():
    """The share-count loader's own freshness row."""
    from . import sec_api
    try:
        cur = sec_api._cur()
    except Exception:                                        # noqa: BLE001
        return []
    if not _has(cur, "sec_cover_runs"):
        return []
    last_ok, last_any = cur.execute(
        "SELECT max(run_at) FILTER (WHERE status = 'ok'), max(run_at) FROM sec_cover_runs").fetchone()
    newest = cur.execute("SELECT max(end_date) FROM sec_cover_shares").fetchone()[0]
    days = (datetime.datetime.utcnow() - last_ok).days if last_ok else None
    stale = days is None or days > STALE_AFTER_DAYS
    failed = bool(last_any and (last_ok is None or last_any > last_ok))
    rows = [{"dataset": "sec-cover-shares", "status": "attention" if (stale or failed) else "ok",
             "archive_read_through": newest.isoformat() if newest else None, "days_behind": days,
             "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
             "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
             "shape_flags": (["latest run failed"] if failed else [])}]
    if _has(cur, "sec_ads_runs"):
        last_ok, last_any = cur.execute(
            "SELECT max(run_at) FILTER (WHERE status = 'ok'), max(run_at) FROM sec_ads_runs").fetchone()
        newest = cur.execute("SELECT max(filed) FROM sec_ads_filings").fetchone()[0]
        unknown = cur.execute("""SELECT count(*) FROM (SELECT cik, arg_max(listing, first_seen) AS l FROM sec_ads_ratios
                                 GROUP BY cik) WHERE l = 'unknown'""").fetchone()[0]
        days = (datetime.datetime.utcnow() - last_ok).days if last_ok else None
        stale = days is None or days > STALE_AFTER_DAYS
        failed = bool(last_any and (last_ok is None or last_any > last_ok))
        rows.append({"dataset": "sec-ads-ratios", "status": "attention" if (stale or failed) else "ok",
                     "archive_read_through": newest.isoformat() if newest else None, "days_behind": days,
                     "stale_after_days": STALE_AFTER_DAYS, "stale": stale,
                     "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
                     "shape_flags": (["latest run failed"] if failed else []) +
                                    (["%d companies with an unreadable listing" % unknown] if unknown else [])})
    return rows


STALE_AFTER_DAYS = 4        # the loader runs every refresh cycle
