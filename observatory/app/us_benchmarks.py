# -*- coding: utf-8 -*-
"""Benchmarks: the indexes people measure a stock against, by the funds that track them.

An index level is the index provider's licensed product; the exchange-traded
fund that tracks it trades in our price feed (Databento, EQUS.SUMMARY) and its
holdings are published daily. So each benchmark here is a fund: its price is
the benchmark's performance (price return, before dividends and the fund's
small fee), and where we hold its holdings (equity/us_index_members.py) the
index can also be ranked within and broken into contributions.

    key -> name, fund ticker, the member list as stored (None when we hold no
           member list), the words people use for it
"""
import re

BENCHMARKS = {
    "sp500": {"name": "S&P 500", "etf": "SPY", "members": "S&P 500 (SPY holdings)",
              "words": r"s ?& ?p(?: ?500)?\b|\bsp ?500\b|s and p 500|\bspx\b|\bspy\b"},
    "dow": {"name": "Dow Jones Industrial Average", "etf": "DIA", "members": "Dow Jones Industrial Average (DIA holdings)",
            "words": r"dow jones(?: industrial average)?|\bdow\b|\bdjia?\b|\bdia\b"},
    "nasdaq100": {"name": "Nasdaq-100", "etf": "QQQ", "members": None,
                  "words": r"nasdaq.?100|\bndx\b|\bqqq\b|nasdaq (index|composite)|\bthe nasdaq\b"},
    "russell2000": {"name": "Russell 2000", "etf": "IWM", "members": None,
                    "words": r"russell ?2000|\biwm\b|small.?caps?"},
    "total": {"name": "total US stock market", "etf": "VTI", "members": None,
              "words": r"total (us )?(stock )?market|whole market|\bvti\b"},
    "sp500_equal": {"name": "S&P 500 equal weight", "etf": "RSP", "members": None,
                    "words": r"equal.?weight"},
    "midcap": {"name": "S&P MidCap 400", "etf": "MDY", "members": None, "words": r"mid.?cap|midcap 400|\bmdy\b"},
    "intl": {"name": "developed markets outside North America (MSCI EAFE)", "etf": "EFA", "members": None,
             "words": r"\beafe\b|\befa\b|international (stocks|developed)"},
    "tech": {"name": "S&P 500 technology sector", "etf": "XLK", "members": None, "words": r"tech(nology)? sector|\bxlk\b"},
    "financials": {"name": "S&P 500 financials sector", "etf": "XLF", "members": None, "words": r"financials? sector|\bxlf\b"},
    "energy": {"name": "S&P 500 energy sector", "etf": "XLE", "members": None, "words": r"energy sector|\bxle\b"},
    "health": {"name": "S&P 500 health care sector", "etf": "XLV", "members": None, "words": r"health ?care sector|\bxlv\b"},
    "industrials": {"name": "S&P 500 industrials sector", "etf": "XLI", "members": None, "words": r"industrials sector|\bxli\b"},
    "consumer_disc": {"name": "S&P 500 consumer discretionary sector", "etf": "XLY", "members": None,
                      "words": r"consumer discretionary|\bxly\b"},
    "staples": {"name": "S&P 500 consumer staples sector", "etf": "XLP", "members": None, "words": r"consumer staples|\bxlp\b"},
    "utilities": {"name": "S&P 500 utilities sector", "etf": "XLU", "members": None, "words": r"utilities sector|\bxlu\b"},
    "materials": {"name": "S&P 500 materials sector", "etf": "XLB", "members": None, "words": r"materials sector|\bxlb\b"},
    "real_estate": {"name": "S&P 500 real estate sector", "etf": "XLRE", "members": None, "words": r"real estate sector|\bxlre\b"},
    "communication": {"name": "S&P 500 communication services sector", "etf": "XLC", "members": None,
                      "words": r"communication services|\bxlc\b"},
    "semis": {"name": "semiconductor stocks (VanEck Semiconductor ETF)", "etf": "SMH", "members": None,
              "words": r"semiconductor (index|etf)|\bsmh\b|\bsox\b"},
    "regional_banks": {"name": "US regional banks (SPDR S&P Regional Banking ETF)", "etf": "KRE", "members": None,
                       "words": r"regional banks? (index|etf)|\bkre\b"},
    "gold": {"name": "gold (SPDR Gold Shares)", "etf": "GLD", "members": None, "words": r"\bgold\b|\bgld\b"},
    "treasuries": {"name": "long-dated US Treasuries (iShares 20+ Year Treasury Bond ETF)", "etf": "TLT", "members": None,
                   "words": r"treasur(y|ies) (index|etf|bonds?)|long bonds?|\btlt\b"},
}
ORDER = list(BENCHMARKS)
# What a reader sees for each benchmark: the ask box's `benchmarks` table holds
# these, not the keys, so a query that shows the column shows words. Sector
# labels are spelled as the stocks table's sectors (app/us_steps.py) so the two join.
LABELS = {
    "sp500": "S&P 500", "dow": "Dow", "nasdaq100": "Nasdaq-100", "russell2000": "Russell 2000",
    "total": "Total Market", "sp500_equal": "S&P 500 Equal Weight", "midcap": "S&P MidCap 400",
    "intl": "International (EAFE)", "tech": "Technology", "financials": "Financials", "energy": "Energy",
    "health": "Health Care", "industrials": "Industrials", "consumer_disc": "Consumer Discretionary",
    "staples": "Consumer Staples", "utilities": "Utilities", "materials": "Materials", "real_estate": "Real Estate",
    "communication": "Communication", "semis": "Semiconductors", "regional_banks": "Regional Banks",
    "gold": "Gold", "treasuries": "Long Treasuries",
}
# Broad equity indexes: a question can rank "within" these. Sector funds, gold and
# bonds are benchmarks to compare against, never a universe ("gold miners" is an
# industry, not the gold price).
UNIVERSES = ("sp500", "dow", "nasdaq100", "russell2000", "total", "sp500_equal", "midcap")


def find(question):
    """The benchmark a question names, or None. Earlier in the list wins a tie
    (the S&P 500 before 'the market')."""
    q = (question or "").lower()
    best = None
    for key in ORDER:
        m = re.search(BENCHMARKS[key]["words"], q)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), key)
    return best[1] if best else None


def catalogue():
    return [{"key": k, "name": b["name"], "fund": b["etf"], "members_held": bool(b["members"])} for k, b in BENCHMARKS.items()]


def members(cur, key):
    """(set of CIKs, {cik: weight_pct}, as_of date) for a benchmark whose member
    list we hold; (None, None, None) otherwise."""
    b = BENCHMARKS.get(key)
    if not b or not b["members"]:
        return None, None, None
    snap = cur.execute("SELECT snapshot_id, as_of FROM us_index_snapshots WHERE index_name = ? "
                       "ORDER BY snapshot_id DESC LIMIT 1", [b["members"]]).fetchone()
    if not snap:
        return None, None, None
    rows = cur.execute("SELECT cik, weight_pct FROM us_index_members WHERE snapshot_id = ?", [snap[0]]).fetchall()
    return {r[0] for r in rows}, {r[0]: r[1] for r in rows}, snap[1]


def with_members():
    return [b["name"] for b in BENCHMARKS.values() if b["members"]]
