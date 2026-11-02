# -*- coding: utf-8 -*-
"""Japanese cross-shareholdings (政策保有株式) in the question box.

The box's company answers read SEC filings, which carry no such line, so
"トヨタの政策保有株式" was once answered with Toyota's revenue. The figures live
in the cross-shareholding product (app/equity_api.py); this reads them through
the same functions that serve /api/v1/equity, so the box and the holdings page
cannot disagree.

Figures are as filed. Book values are fair-valued at each year end, so the note
under the answer says a change mixes selling with market moves.
"""
import re

from fastapi import HTTPException

from . import ask_miss

TOPIC = re.compile(r"政策保有|持ち合い|持合い?株|cross[- ]?(?:share)?hold|polic\w*[- ](?:based[- ]|related[- ])?(?:share|stock|stake|hold|equit)|"
                   r"strategic[- ](?:share|stock)|mutual[- ]shareholding", re.I)
_JA = re.compile(r"[぀-ヿ㐀-鿿]")
_RANKING = re.compile(r"一番|最も|ランキング|上位|どの会社|\b(?:which|most|largest|biggest|top|rank\w*|who)\b", re.I)
_WORD_YEARS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10}
_NOT_NAME = {"how", "have", "has", "had", "did", "do", "does", "is", "are", "was", "were", "what", "the", "a", "an", "of", "in", "at", "on", "for",
             "over", "last", "past", "years", "year", "changed", "change", "changes", "changing", "history", "and", "show", "me", "its", "their",
             "much", "many", "fallen", "fell", "risen", "rose", "reduced", "reduce", "grown", "grew", "unwinding", "unwound", "been", "since", "to"}
PAGE_POINTER = "Cross-shareholdings are on the Cross-Shareholdings page under the Japan tab, company by company and across the market."


def _yen_ja(v):
    if v is None:
        return "—"
    oku = round(v / 1e8)
    cho, rest = divmod(oku, 10000)
    return ("%d兆%s億円" % (cho, "{:,}".format(rest))) if cho else "%s億円" % "{:,}".format(rest)


def _yen_en(v):
    if v is None:
        return "—"
    return "¥%.2ftn" % (v / 1e12) if abs(v) >= 1e12 else "¥%.1fbn" % (v / 1e9)


def _period_ja(h):
    y, m = h["period_end"][:7].split("-")
    return "%s年%d月期" % (y, int(m))


def _years_asked(question):
    m = re.search(r"(\d+)\s*年", question) or re.search(r"(\d+)[- ]years?\b", question, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(%s)[- ]years?\b" % "|".join(_WORD_YEARS), question, re.I)
    return _WORD_YEARS[m.group(1).lower()] if m else None


def _name_words(question):
    """The words that can name the company, longest reading first."""
    topic = TOPIC.search(question)
    if _JA.search(question):
        before = question[:topic.start()].rstrip("のがはをと、 ")
        name = re.split(r"[のがはをと、\s]", before)[-1] if before else ""
        after = question[topic.end():]
        if not name:
            m = re.search(r"(?:株式|株)?(?:について|の)?\s*([^\s、のがは]+)の?$", after)
            name = m.group(1) if m else ""
        return [name] if name else []
    text = TOPIC.sub(" ", question)
    text = re.sub(r"(?:share)?holdings?|shareholdings?|'s\b|’s\b", " ", text, flags=re.I)
    words = [w for w in re.findall(r"[A-Za-z0-9&.-]+", text) if w.lower().strip(".") not in _NOT_NAME and not w.isdigit()]
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def _company(question):
    from . import equity_api
    for name in _name_words(question):
        found = (equity_api.companies(q=name) or {}).get("companies") or []
        if found:
            return found[0]
    return None


def answer(question):
    """The answer package, None when the question is not about cross-shareholdings,
    or a refusal that says where to look. Never a different figure."""
    if not TOPIC.search(question or ""):
        return None
    from . import equity_api
    try:
        found = _company(question)
    except HTTPException:
        raise ask_miss.miss("Cross-shareholdings aren't available on this server right now. Try again in a few minutes.", "not_held")
    if not found:
        if _RANKING.search(question):
            raise ask_miss.miss(PAGE_POINTER, "not_held")
        raise ask_miss.miss("Cross-shareholdings are a Japanese disclosure, and no Japanese filer by that name has them on file. " + PAGE_POINTER, "not_held")
    d = equity_api.company(found["sec_code"])
    history = [dict(h, period_end=str(h["period_end"])) for h in d.get("history") or [] if h.get("book_value_yen") is not None]
    ent = d.get("entity") or {}
    ja = bool(_JA.search(question))
    en_name = (ent.get("name_en") or found.get("name_en") or "").title()
    name = (ent.get("name_ja") or found.get("name")) if ja else en_name
    if not history:
        raise ask_miss.miss("%s has no cross-shareholding table on file." % (en_name or found["sec_code"]), "not_held")
    asked = _years_asked(question)
    shown = history[-(asked + 1):] if asked else history
    a, b = shown[0], shown[-1]
    notes = []
    if asked and len(history) < asked + 1:
        notes.append("%d years were asked for; the annual reports on file go back to %s." % (asked, history[0]["year"]))
    if ja:
        headline = ("%sの政策保有株式（銘柄を記載した分の簿価合計）は、%sの%s・%d銘柄から、%sの%s・%d銘柄になりました。"
                    % (name, _period_ja(a), _yen_ja(a["book_value_yen"]), a["named_holdings"], _period_ja(b), _yen_ja(b["book_value_yen"]), b["named_holdings"]))
        text = ("各年の有価証券報告書に記載された数値です。簿価は期末の時価で評価されるため、金額の変化には売却と株価変動の両方が含まれます。"
                "売却の進み具合は銘柄数の変化で見てください。")
        cols = ["決算期末", "記載銘柄数", "簿価合計"]
        title = "%s 政策保有株式の簿価合計（記載銘柄）" % name
        source_note = "有価証券報告書（EDINET）の記載どおり。"
        link_note = "銘柄ごとの内訳は保有株式のページにあります。"
    else:
        headline = ("%s's named cross-shareholdings went from %s across %d stocks in %s to %s across %d stocks in %s."
                    % (name, _yen_en(a["book_value_yen"]), a["named_holdings"], a["year"], _yen_en(b["book_value_yen"]), b["named_holdings"], b["year"]))
        text = ("Each figure is as filed in that year's annual securities report. Book values are marked to market at the year end, so a change "
                "mixes selling with share-price moves; the number of stocks shows the selling.")
        cols = ["Fiscal year end", "Named stocks", "Book value"]
        title = "%s named cross-shareholdings, book value" % name
        source_note = "As filed in each annual securities report (EDINET)."
        link_note = "The holding-by-holding list is on the holdings page."
    notes.append(link_note)
    return {"recipe": "jp_cross_shareholdings", "question": question, "headline": headline, "answer": text, "figures": [],
            "table": {"columns": cols, "rows": [[h["period_end"], str(h["named_holdings"]), h["book_value_yen"]] for h in shown], "unit": "JPY"},
            "chart": {"kind": "bars", "title": title, "unit": "¥bn",
                      "series": [{"name": "Book value", "points": [[h["year"], h["book_value_yen"] / 1e9] for h in shown]}]},
            "sources": [{"name": "EDINET annual securities reports", "credit": "Source: Financial Services Agency of Japan, EDINET.", "note": source_note}],
            "calc": {}, "entities": {"company": {"name": name, "sec_code": found["sec_code"]}},
            "links": {"company": "/holdings.html?c=%s" % found["sec_code"]}, "notes": notes,
            "trust": {"filed": "official", "prices": "official", "ratios": "derived"},
            "match": {"recipe": "jp_cross_shareholdings", "label": "Cross-shareholdings", "score": None, "alternatives": [], "via": "quick", "read_as": None},
            "did_you_mean": []}
