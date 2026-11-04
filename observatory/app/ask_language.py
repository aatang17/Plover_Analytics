# -*- coding: utf-8 -*-
"""The question box answers in the language it was asked in.

Every check and route reads English, so a question in another language is put
into English first (read) and the finished answer is put back (answer_in,
reply_in, preview_in). Refusals and suggestions go back too.

A number must survive that trip. Figures are the product, and a translator will
happily turn $416bn into 4,160億ドル or drop a digit, so each string is checked
(same_figures): a translation that loses or adds a figure is thrown away and that
string stays in English. The reader may see mixed languages; never a changed number.
If the model is off or fails the whole answer stays in English.

Left in English on purpose: table cells (names and tags as filed), the source
credit lines, the steps log, and the page's own buttons and labels.
"""
import collections
import json
import re
import threading
import unicodedata

FOREIGN_SCRIPT = re.compile(r"[぀-ヿ㐀-鿿가-힯Ѐ-ӿ֐-ۿऀ-ॿ฀-๿]")
KANA = re.compile(r"[぀-ヿ]")
# Latin-script languages cost a model call only when a word suggests one; the model then says
# what it is, so "Nestlé revenue" stays English. A question it misses is still answered, in English.
_ACCENT = re.compile(r"[à-öø-ÿąćęłńśźżğış]")     # no IGNORECASE here: under it "ı" matches a plain "i", and every English question looks foreign
_LATIN_HINT = re.compile(r"\b(?:quels?|quelles?|combien|pourquoi|est-ce|les|des|du|cuánto|cuanto|cuál|cual|qué|que|por|ingresos|"
                         r"ventas|precio|acciones|wie|viel|warum|umsatz|aktie|gewinn|der|und|quanto|quanti|perché|ricavi|receita|lucro|berapa|saham|"
                         r"pendapatan|hoeveel|omzet|aandeel)\b", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MONTH_OR_WORD = re.compile(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
                            r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", re.I)
_PER_ONE = re.compile(r"\b(?:per|each|a|an)\b", re.I)
ENGLISH_NAMES = ("english", "en")
CACHE_MAX = 5000
_LOCK = threading.Lock()
_DONE = collections.OrderedDict()


def country_as_us(question):
    """"in the United States" is the country, not United States Oil Fund or United States Steel."""
    return re.sub(r"\b(in|of|for|across) the united states(?! (?:oil|steel|cellular|lime|natural|gasoline|brent|commodity|antimony))\b",
                  lambda m: m.group(1) + " the US", question or "", flags=re.I)


def might_be_foreign(question):
    q = question or ""
    return bool(FOREIGN_SCRIPT.search(q) or _ACCENT.search(q.lower()) or _LATIN_HINT.search(q))


def read(question):
    """None for English; else {"language", "english"}. A question in another script
    the model could not read comes back with english None."""
    if not might_be_foreign(question):
        return None
    from . import us_ask_model
    got = us_ask_model.translate(question)
    if not got:
        return {"language": None, "english": None} if FOREIGN_SCRIPT.search(question) else None
    if str(got.get("language") or "").strip().lower() in ENGLISH_NAMES:
        return None
    return got


def _plain_number(n):
    """One form for one figure: 1,234 is 1234, and 6,4 is the French and Spanish for 6.4.
    German 166.000 stays a different figure, so that string is kept in English."""
    n = n.rstrip(",")
    n = re.sub(r",(?=\d{3}(?:\D|$))", "", n)
    return n.replace(",", ".")


def _numbers(text):
    found = _NUMBER.findall(unicodedata.normalize("NFKC", text or ""))
    return collections.Counter(_plain_number(n) for n in found)


_SCALED = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(bn|tn|mn)(?![A-Za-z])")


def same_figures(english, translated):
    """Every figure kept, none added, and its scale with it: 416億 has the digits of
    $416bn and a tenth of its size, so $416bn must still read 416bn. A month or a small
    number written as a word may become digits ("31 March" is 3月31日), so up to that
    many digits under 13 are let in."""
    plain = unicodedata.normalize("NFKC", translated or "")
    kept = collections.Counter((_plain_number(n), scale) for n, scale in _SCALED.findall(plain))
    if collections.Counter((_plain_number(n), scale) for n, scale in _SCALED.findall(english or "")) - kept:
        return False
    before, after = _numbers(english), _numbers(translated)
    if before - after:
        return False
    extra = after - before
    # "yen per dollar" is 1ドル80.80円 and "a share" 1株: a 1 for each per, each, a or an
    extra["1"] -= min(extra["1"], len(_PER_ONE.findall(english or "")))
    extra += collections.Counter()
    if any(not n.isdigit() or int(n) > 12 for n in extra):
        return False
    return sum(extra.values()) <= len(_MONTH_OR_WORD.findall(english or ""))


def strings_in(language, strings):
    """Each string in `language`; one that fails the figures check stays as it is."""
    from . import us_ask_model
    wanted = [s for s in dict.fromkeys(strings) if isinstance(s, str) and re.search(r"[A-Za-z]{2}", s)]
    with _LOCK:
        known = {s: _DONE[(language, s)] for s in wanted if (language, s) in _DONE}
    todo = [s for s in wanted if s not in known]
    if todo:
        for s, t in zip(todo, us_ask_model.translate_strings(language, todo) or todo):
            if t != s and not same_figures(s, t):
                from . import us_steps
                us_steps.log(s, "translation changed a figure, kept in English", t)
                t = s
            known[s] = t
            if t != s:
                with _LOCK:
                    _DONE[(language, s)] = t
                    while len(_DONE) > CACHE_MAX:
                        _DONE.popitem(last=False)
    return [known.get(s, s) for s in strings]


def _swap(language, spots):
    """spots: (holder, key) pairs whose string is replaced in place."""
    spots = [(h, k) for h, k in spots if isinstance(h[k], str) and h[k]]
    done = strings_in(language, [h[k] for h, k in spots])
    for (h, k), t in zip(spots, done):
        h[k] = t


def _listed(values):
    return [(values, i) for i in range(len(values or []))]


def answer_in(language, out):
    """A stock answer in `language`, the figures untouched."""
    spots = [(out, k) for k in ("headline", "answer", "period") if k in out]
    spots += _listed(out.get("notes"))
    match = out.get("match")
    if isinstance(match, dict) and "read_as" in match:
        spots.append((match, "read_as"))
    table = out.get("table")
    if isinstance(table, dict):
        spots += _listed(table.get("columns"))
    chart = out.get("chart")
    if isinstance(chart, dict):
        if "title" in chart:
            spots.append((chart, "title"))
        spots += [(s, "name") for s in chart.get("series") or [] if isinstance(s, dict) and "name" in s]
    spots += [(f, "label") for f in out.get("figures") or [] if isinstance(f, dict) and "label" in f]
    for alt in out.get("did_you_mean") or []:
        if isinstance(alt, dict):
            spots += [(alt, k) for k in ("question", "differs") if k in alt]
    _swap(language, spots)
    return out


def reply_in(question, reply):
    """A refusal's reason and suggestions, in the language it was asked in."""
    read_as = read(question)
    if not read_as or not read_as.get("language"):
        return reply
    _swap(read_as["language"], [(reply, "detail")] + _listed(reply.get("try")))
    return reply


def preview_in(language, out):
    """The front page's preview or economy answer in `language`."""
    spots = [(out, k) for k in ("headline", "lead", "read_as", "message", "title", "unit_note") if k in out]
    for k in ("more", "try", "holds"):
        if isinstance(out.get(k), list):
            spots += _listed(out[k])
    if isinstance(out.get("holds"), str):
        spots.append((out, "holds"))
    spots += [(f, "label") for f in out.get("figures") or [] if isinstance(f, dict) and "label" in f]
    _swap(language, spots)
    return out
