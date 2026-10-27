# -*- coding: utf-8 -*-
u"""A Japanese company's English name as people write it, for display.

The English name on an annual report's cover page is the company's own, but
it is typed every which way: "TOYOTA MOTOR CORPORATION" in capitals,
"ＯＳＡＫＡ　ＧＡＳ　ＣＯ.,　ＬＴＤ." in full-width letters, "DAIKIN
INDUSTRIES,LTD." with no space after the comma. Beside US names ("Nvidia
Corp", "Apple Inc.", app/us_link.display_name) they read as shouting.

display_name() only changes how the name is written, never which name it is:
full-width letters become ordinary ones, spaces and commas are tidied, and a
word in capitals is written with a capital first letter, unless it is a short
abbreviation (NTT, TDK, MS&AD) or a brand the company itself writes in
capitals (KDDI, ITOCHU, FUJIFILM). A name already in mixed case keeps its
mixed-case words. The as-filed name stays in every API field that says
name_en; this is applied where a page shows the name.
"""
import re
import unicodedata

# Brands the companies write in capitals in their own English text.
_KEEP_UPPER = {"KDDI", "ITOCHU", "FUJIFILM", "TOPPAN", "ENEOS", "OMRON", "LIXIL", "JTEKT", "JVCKENWOOD",
               "SCSK", "ALSOK", "EXEO", "TOTO", "AEON", "ORIX", "HOYA", "SUBARU", "ROHM", "SUMCO", "TOWA",
               "IHI", "NTT", "TDK", "NEC", "AGC", "ANA", "JAL", "NSK", "NTN", "NOK", "NGK", "SMC", "DIC",
               "TIS", "SBI", "JFE", "JR", "JT", "NHK", "UT", "DY", "NH", "LY", "SG", "TEC", "KYB", "THK",
               "JSR", "GMO", "HIS", "MUFG", "SMBC", "UFJ", "USS", "ZOZO", "LINE", "PALTAC", "SCREEN",
               "JUKI", "DMG", "MORI", "NIPRO"}
# Words of three letters or fewer that are words, not abbreviations.
_SHORT_WORDS = {"CO", "LTD", "INC", "THE", "AND", "OF", "FOR", "GAS", "OIL", "ONE", "NEW", "SUN", "BAY",
                "CAR", "ART", "BIO", "SKY", "AIR", "TOP", "BIG", "RED", "WAY", "KAI", "SHA", "DEN"}
# Joining words stay lower case inside a name: "Bank of Japan".
_SMALL = {"AND", "OF", "THE", "FOR", "IN"}
_ROMAN = re.compile(r"^(?:I{1,3}|IV|V|VI{1,3}|IX|X)$")


def _word(w, first):
    if "-" in w:
        return "-".join(_word(p, first and i == 0) for i, p in enumerate(w.split("-")))
    core = w.strip(",.()")
    letters = re.sub(r"[^A-Za-z]", "", core)
    if not letters or letters != letters.upper():
        return w                                    # mixed case or no letters: as written
    if core in _KEEP_UPPER or "&" in core or _ROMAN.match(core) or re.search(r"\d", core):
        return w
    if len(letters) <= 3 and core not in _SHORT_WORDS:
        return w                                    # an abbreviation: NTT, TDK
    if not first and core in _SMALL:
        return w.replace(core, core.lower())
    return w.replace(core, core.capitalize())


def display_name(name):
    """'TOYOTA MOTOR CORPORATION' -> 'Toyota Motor Corporation',
    'DAIKIN INDUSTRIES,LTD.' -> 'Daikin Industries, Ltd.',
    'ＯＳＡＫＡ　ＧＡＳ　ＣＯ.,　ＬＴＤ.' -> 'Osaka Gas Co., Ltd.'."""
    if not name:
        return name
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\s*,\s*", ", ", n)                 # "Co.,Ltd." and "Co.,  Ltd."
    n = " ".join(n.split()).strip(" ,")
    return " ".join(_word(w, i == 0) for i, w in enumerate(n.split(" ")))
