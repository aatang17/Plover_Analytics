"""Adapter: regional and cooperative financial institutions — FSA 中小・地域金融機関情報一覧.

Source: 金融庁 (Financial Services Agency), 中小・地域金融機関情報一覧 — two
workbooks the FSA compiles once a year from each institution's own
disclosure: one for the regional banks (地方銀行 and 第二地方銀行, about a
hundred) and one for the shinkin banks and credit co-operatives (信用金庫
and 信用組合, about four hundred). Per institution: deposits, loans, the
capital ratio, the bad-loan ratio, the number of branches, loans to small
and medium-sized enterprises and the number of SME borrowers for two
year-ends, plus the institution's code, corporate number, head-office
location and the URL of its disclosure page.

Why it is here: the shinkin banks and credit co-operatives are not members
of the Japanese Bankers Association, so `jba-banks` cannot see them, and
the FSA's results summaries cover banks only. This is the one free source
that puts every deposit-taker in the country on one list with the same
few measures — and the one that records where each institution publishes
the rest of its disclosure, which is what the Basel rate-risk collector
reads.

**One point in time per edition.** The FSA overwrites the same two files
each year (2025-1.xlsx, 2025-2.xlsx; no archive). Each ingest is a vintage
and the history accumulates here, nowhere else.

**Units are made the same across the two files, exactly.** The bank file
is in 億円 and the co-operative file in 百万円; the bank figures are
multiplied by 100 — an exact change of unit, not a recomputation — so
every amount is stored in 百万円 and one loan-to-deposit ranking can run
across all five hundred institutions. Ratios are in percent as published.

**The as-of date comes from the file, not the page.** The SME columns carry
their own year-end headers (令和６年３月末, 令和７年３月末); every other
figure is dated to the later of the two, which is the date the FSA's page
states for the edition. A file whose SME headers do not resolve to two
consecutive March year-ends is refused.
"""
import base64
import datetime
import json
import re
import urllib.parse

from . import boj_ts, xlsx


class ValidationError(Exception):
    pass


# The FSA posts one edition a year as two workbooks, <year>-1.xlsx (banks)
# and <year>-2.xlsx (shinkin banks and credit co-operatives), and moves the
# previous year's files when it does: on 2026-11-04 the 2025 files answered
# 404 and the 2026 ones sat at a new path. fetch() therefore reads the page
# and takes the newest year's pair; these are only the last pair known, used
# when the page lists none.
PAGE = "https://www.fsa.go.jp/policy/chusho/shihyou.html"
FILES = {
    "banks": "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2026-1.xlsx",
    "coops": "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2026-2.xlsx",
}
# .../zenkoku/2026-1.xlsx — the year the edition was posted, then 1 = banks,
# 2 = co-operatives. Matched anywhere in the href so a moved folder still fits.
_FILE_LINK = re.compile(r'href="([^"]*?/(\d{4})-([12])\.xlsx)"', re.I)
_GROUP_BY_NUMBER = {"1": "banks", "2": "coops"}
# The bank file is in 億円, the co-operative file in 百万円: ×100 is exact.
AMOUNT_SCALE = {"banks": 100, "coops": 1}

TYPES = {
    "地方銀行": ("regional-1", "Regional bank", 0),
    "第二地方銀行": ("regional-2", "Regional bank II", 1),
    "信用金庫": ("shinkin", "Shinkin bank", 2),
    "信用組合": ("shinkumi", "Credit co-operative", 3),
}

# (slug, English, unit, order). Amounts in 百万円 after scaling.
LINES = [
    ("deposits", "Deposits", "jpy_mn", 0),
    ("loans", "Loans", "jpy_mn", 1),
    ("capital-ratio", "Capital ratio", "percent", 2),
    ("npl-ratio", "Bad-loan ratio", "percent", 3),
    ("sme-loans", "Loans to SMEs", "jpy_mn", 4),
    ("sme-borrowers", "SME borrowers", "count", 5),
    ("branches", "Branches", "count", 6),
]
LINE_JA = {
    "deposits": "預金", "loans": "貸出金", "capital-ratio": "自己資本比率",
    "npl-ratio": "不良債権比率", "sme-loans": "中小企業等向け貸出残高",
    "sme-borrowers": "中小企業等向け貸出先件数", "branches": "店舗数",
}

# Column headers, normalised by removing whitespace. The two files differ in
# where the type column sits and in the deposit label (預金 / 預金積金), so
# columns are found by header text, never by letter.
HEADERS = {
    "type": ("地銀第二地銀", "種別"),
    "pref": ("都道府県",),
    "code": ("金融機関コード",),
    "name": ("金融機関名",),
    "corp": ("法人番号",),
    "location": ("本店所在地",),
    "branches": ("店舗数",),
    "url": ("ディスクロージャー", "ディスクロージャー（Web）"),
    "deposits": ("預金積金（百万円）", "預金（億円）"),
    "loans": ("貸出金（百万円）", "貸出金（億円）"),
    "capital-ratio": ("自己資本比率（％）",),
    "npl-ratio": ("不良債権比率（％）",),
    "sme-loans": ("中小企業等向け貸出残高（百万円）", "中小企業等向け貸出残高（億円）"),
    "sme-borrowers": ("中小企業等向け貸出先件数（件）", "中小企業等向け貸出先件数"),
}
_WS = re.compile(r"[\s　]+")
_YEAR_END = re.compile(r"^(令和|平成)(元|\d{1,2})年(\d{1,2})月末$")

DATASET = {
    "slug": "fsa-regional-fi",
    "title": "Regional Banks, Shinkin Banks and Credit Co-operatives — FSA List (Japan)",
    "country": "Japan",
    "agency": "Financial Services Agency",
    "agency_ja": "金融庁",
    "base": None,
    "frequency": "annual",
    "description": (
        "Every regional bank, second-tier regional bank, shinkin bank and "
        "credit co-operative in Japan — about five hundred institutions — with "
        "deposits, loans, the capital ratio, the bad-loan ratio, branches, and "
        "loans to and number of SME borrowers for two year-ends, from the FSA's "
        "annual list compiled from each institution's own disclosure. Amounts "
        "in 百万円 (the bank file's 億円 scaled ×100 exactly); ratios in percent."
    ),
}

SOURCE = {
    "source_id": "fsa:chusho-shihyou",
    "name": "FSA — List of regional and small-and-medium-sized financial institutions (中小・地域金融機関情報一覧)",
    "name_ja": "金融庁 中小・地域金融機関情報一覧（業態別・全国）",
    "url": "https://www.fsa.go.jp/policy/chusho/shihyou.html",
    "license_note": (
        "Financial Services Agency website terms of use (compatible with CC BY "
        "4.0): free to use with attribution to the Financial Services Agency. "
        "The FSA compiles the figures from each institution's published disclosure."
    ),
}

DOWNLOAD_URL = SOURCE["url"] + " (the newest zenkoku/<year>-1.xlsx and <year>-2.xlsx linked there)"
RAW_SUFFIX = ".json"

PRESENTATION = {
    "credit_line": ("Source: Financial Services Agency — 中小・地域金融機関情報一覧, "
                    "compiled from each institution's disclosure."),
    # One edition a year, posted in the autumn for the March year-end.
    "stale_after_days": 450,
    "main_series": [],
    "overview_tiles": [],
    "kinds": {},
    # ~3,500 series: the listing is a search, never a dump.
    "series_requires_query": True,
}


# --- fetching ---------------------------------------------------------------

def linked_files(html, page_url=PAGE):
    """{group: url} for the newest edition's two workbooks linked from the page.

    Every <year>-1/-2.xlsx link is collected; the newest year that has both
    files wins. Empty when the page links none, so the caller can fall back to
    the last pair known rather than fail on a page redesign alone.
    """
    by_year = {}
    for href, year, number in _FILE_LINK.findall(html):
        by_year.setdefault(int(year), {})[_GROUP_BY_NUMBER[number]] = \
            urllib.parse.urljoin(page_url, href.replace("&amp;", "&"))
    for year in sorted(by_year, reverse=True):
        if set(by_year[year]) == set(FILES):
            return by_year[year]
    return {}


def current_files():
    """The two workbooks to read: found on the page, or the last pair known."""
    try:
        html = boj_ts.fetch_bytes(PAGE).decode("utf-8", "replace")
    except Exception:                                        # noqa: BLE001
        return dict(FILES)      # the page is down; the files themselves may not be
    return linked_files(html) or dict(FILES)


def fetch():
    envelope = {}
    for group, url in sorted(current_files().items()):
        envelope[group] = {
            "url": url,
            "b64": base64.b64encode(boj_ts.fetch_bytes(url)).decode("ascii"),
        }
    return json.dumps({"files": envelope}, sort_keys=True).encode("utf-8")


# --- parsing ----------------------------------------------------------------

def _colnum(col):
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n


def _norm(text):
    return _WS.sub("", text or "")


def _year_end(text):
    m = _YEAR_END.match(_norm(text))
    if not m:
        return None
    era, year, month = m.group(1), m.group(2), int(m.group(3))
    year = 1 if year == "元" else int(year)
    if era == "令和":
        y = 2018 + year
    else:
        y = 1988 + year
    if month != 3:
        return None
    return datetime.date(y, 3, 31)


def _num(text):
    text = (text or "").strip().replace(",", "")
    if text in ("", "-", "－", "―", "…", "***", "×"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _layout(grid):
    """(column letter by field, [(SME column letter, year-end)] pairs)."""
    rows = sorted(grid)
    head = {k: _norm(v[0]) for k, v in grid[rows[0]].items()}
    cols = {}
    for field, labels in HEADERS.items():
        # Labels are in order of preference: the bank file has both a 種別
        # column (地域銀行 on every row) and a 地銀/第二地銀 column, and the
        # second is the one that says which.
        for label in labels:
            hit = [col for col, text in head.items() if text == label]
            if hit:
                cols[field] = hit[0]
                break
    missing = [f for f in HEADERS if f not in cols]
    if missing:
        raise ValidationError("header columns not found: %s (header row: %s)"
                              % (", ".join(missing), sorted(head.items())))
    sub = {k: v[0] for k, v in grid[rows[1]].items()}
    sme = {}
    for field in ("sme-loans", "sme-borrowers"):
        first = _colnum(cols[field])
        pairs = []
        for col in sorted(sub, key=_colnum):
            if _colnum(col) in (first, first + 1):
                ye = _year_end(sub[col])
                if ye is None:
                    raise ValidationError("%s: sub-header %r is not a March year-end"
                                          % (field, sub[col]))
                pairs.append((col, ye))
        if len(pairs) != 2 or pairs[1][1].year - pairs[0][1].year != 1:
            raise ValidationError("%s: expected two consecutive year-end columns, got %s"
                                  % (field, pairs))
        sme[field] = pairs
    return cols, sme, rows[2:]


def parse(raw):
    files = json.loads(raw.decode("utf-8"))["files"]
    meta, values, entities = {}, {}, {}
    for group in sorted(files):
        if group not in FILES:
            raise ValidationError("unknown group %r in the envelope" % group)
        book = xlsx.sheets(base64.b64decode(files[group]["b64"]))
        if len(book) != 1:
            raise ValidationError("%s: expected one sheet, found %s" % (group, list(book)))
        grid = list(book.values())[0]
        cols, sme, data_rows = _layout(grid)
        as_of = sme["sme-loans"][1][1]
        scale = AMOUNT_SCALE[group]
        for r in data_rows:
            cells = {k: v[0] for k, v in grid[r].items()}
            code = (cells.get(cols["code"]) or "").strip()
            name = (cells.get(cols["name"]) or "").strip()
            if not code and not name:
                continue
            if not re.match(r"^\d{4}$", code):
                raise ValidationError("%s row %d: institution code %r is not four digits"
                                      % (group, r, code))
            kind_ja = (cells.get(cols["type"]) or "").strip()
            if kind_ja not in TYPES:
                raise ValidationError("%s row %d (%s): unknown institution type %r"
                                      % (group, r, name, kind_ja))
            kind, kind_en, kind_order = TYPES[kind_ja]
            if code in entities:
                raise ValidationError("institution code %s appears twice (%s, %s)"
                                      % (code, entities[code]["name_ja"], name))
            entities[code] = {
                "code": code, "name_ja": name, "type": kind, "type_ja": kind_ja,
                "prefecture": (cells.get(cols["pref"]) or "").strip(),
                "corporate_number": (cells.get(cols["corp"]) or "").strip(),
                "location": (cells.get(cols["location"]) or "").strip(),
                "disclosure_url": (cells.get(cols["url"]) or "").strip(),
            }
            points = []
            for slug, en, unit, order in LINES:
                if slug in ("sme-loans", "sme-borrowers"):
                    for col, ye in sme[slug]:
                        v = _num(cells.get(col))
                        if v is not None and unit == "jpy_mn":
                            v = v * scale
                        points.append((slug, en, unit, order, ye, v))
                else:
                    v = _num(cells.get(cols[slug]))
                    if v is not None and unit == "jpy_mn":
                        v = v * scale
                    points.append((slug, en, unit, order, as_of, v))
            for slug, en, unit, order, period, v in points:
                if v is None:
                    continue
                if unit == "percent" and not (0.0 <= v <= 100.0):
                    raise ValidationError("%s %s %s: %s%% is not a percentage"
                                          % (name, slug, period, v))
                scode = "%s.%s" % (code, slug)
                meta.setdefault(scode, {
                    "code": scode,
                    "name_en": "%s (%s, %s) — %s" % (name, kind_en, code, en),
                    "name_ja": "%s %s %s" % (name, kind_ja, LINE_JA[slug]),
                    "unit": unit,
                    "weight_per_10000": None,
                    "sort_order": kind_order * 100000 + int(code) * 10 + order,
                })
                prev = values.setdefault(scode, {}).get(period)
                if prev is not None and prev != v:
                    raise ValidationError("%s %s appears twice with different values"
                                          % (scode, period))
                values[scode][period] = v
    series = sorted(meta.values(), key=lambda s: (s["sort_order"], s["code"]))
    observations = [{"code": c, "period": p, "value": v}
                    for c in sorted(values) for p, v in sorted(values[c].items())]
    # Kept on the module for the collectors that read the archived raw file
    # through parse(); the observation store has no room for a URL.
    parse.entities = entities
    return series, observations


# --- validation -------------------------------------------------------------

EXPECTED_COUNT = {"regional-1": (55, 70), "regional-2": (30, 45),
                  "shinkin": (230, 270), "shinkumi": (120, 160)}
# Shinkin deposits in total have been ¥150–170tn; regional banks' ¥400tn+.
SHINKIN_DEPOSITS_MN = (120e6, 220e6)
REGIONAL_DEPOSITS_MN = (350e6, 600e6)


def validate(series, observations):
    if not observations:
        raise ValidationError("no observations parsed")
    by_code, seen = {}, set()
    for o in observations:
        key = (o["code"], o["period"])
        if key in seen:
            raise ValidationError("duplicate observation %s %s" % key)
        seen.add(key)
        by_code.setdefault(o["code"], {})[o["period"]] = o["value"]

    entities = getattr(parse, "entities", {})
    counts = {}
    for e in entities.values():
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    for kind, (lo, hi) in EXPECTED_COUNT.items():
        n = counts.get(kind, 0)
        if not (lo <= n <= hi):
            raise ValidationError("%d %s institutions; expected %d–%d" % (n, kind, lo, hi))
    if any(not e["disclosure_url"].startswith("http") for e in entities.values()):
        bad = [e["name_ja"] for e in entities.values() if not e["disclosure_url"].startswith("http")]
        if len(bad) > 10:
            raise ValidationError("%d institutions carry no disclosure URL: %s"
                                  % (len(bad), bad[:5]))

    as_of = max(o["period"] for o in observations)
    totals = {"shinkin": 0.0, "regional": 0.0}
    # An institution the FSA lists with no figures (one that has just been
    # wound up or merged, or has not published) is a gap, not a fault — but
    # more than a handful means the columns have moved.
    blank = []
    for code, e in entities.items():
        dep = by_code.get("%s.deposits" % code, {}).get(as_of)
        loans = by_code.get("%s.loans" % code, {}).get(as_of)
        if dep is None or loans is None:
            blank.append(e["name_ja"])
            continue
        if dep <= 0 or loans < 0:
            raise ValidationError("%s: deposits %s, loans %s" % (e["name_ja"], dep, loans))
        if loans > 1.5 * dep:
            raise ValidationError("%s: loans %s exceed 1.5× deposits %s — a unit or column slip"
                                  % (e["name_ja"], loans, dep))
        if e["type"] == "shinkin":
            totals["shinkin"] += dep
        elif e["type"].startswith("regional"):
            totals["regional"] += dep
    if len(blank) > 5:
        raise ValidationError("%d institutions carry no deposits or loans: %s"
                              % (len(blank), blank[:6]))
    if not (SHINKIN_DEPOSITS_MN[0] <= totals["shinkin"] <= SHINKIN_DEPOSITS_MN[1]):
        raise ValidationError("shinkin deposits sum to %s 百万円, outside the plausible range"
                              % totals["shinkin"])
    if not (REGIONAL_DEPOSITS_MN[0] <= totals["regional"] <= REGIONAL_DEPOSITS_MN[1]):
        raise ValidationError("regional-bank deposits sum to %s 百万円, outside the plausible range"
                              % totals["regional"])
    today = datetime.date.today()
    if (today - as_of).days > 700:
        raise ValidationError("as-of %s is implausibly old for a changed file" % as_of)
    return {
        "institutions": len(entities),
        "by_type": counts,
        "without_figures": blank,
        "series": len(series),
        "observations": len(observations),
        "latest_period": as_of.isoformat(),
        "shinkin_deposits_jpy_mn": totals["shinkin"],
        "regional_bank_deposits_jpy_mn": totals["regional"],
    }


MANIFEST = {
    "id": DATASET["slug"],
    "section": "banking",
    "name": {"en": "Regional banks, shinkin banks and credit co-operatives — FSA list",
             "ja": "中小・地域金融機関情報一覧（地域銀行・信用金庫・信用組合）"},
    "shape": "series",
    "summary": DATASET["description"],
    "source": {
        "publisher": DATASET["agency"],
        "publisher_ja": DATASET["agency_ja"],
        "document": SOURCE["name"],
        "url": SOURCE["url"],
        "credit": PRESENTATION["credit_line"],
        "license_note": SOURCE["license_note"],
    },
    "keys": ["series_code", "period"],
    "frequency": "annual",
    "vintage": {
        "unit": "release", "as_of_basis": "release-in-force",
        "as_of_supported": True, "history_from": "2024-03",
        "stale_after_days": PRESENTATION["stale_after_days"],
    },
    "measures": [
        {"id": "index", "label": "Published amount (百万円), ratio (%) or count, as released",
         "unit": "JPY_million", "trust": "official"},
    ],
    "endpoints": {
        "series": "/api/v1/%s/observations" % DATASET["slug"],
        "search": "/api/v1/%s/series" % DATASET["slug"],
        "releases": "/api/v1/%s/releases" % DATASET["slug"],
        "revisions": "/api/v1/%s/revisions" % DATASET["slug"],
    },
    "capabilities": ["series", "search"],
    "cite": "/banks.html?dataset=fsa-regional-fi",
    "page": "/banks.html",
    "notes": [
        "Series codes are {institution code}.{line}: the four-digit 金融機関コード "
        "(0134 Chiba Bank, 1001 Hokkaido Shinkin) and one of deposits, loans, "
        "capital-ratio, npl-ratio, sme-loans, sme-borrowers, branches.",
        "Amounts are in 百万円. The FSA's bank file is in 億円 and is multiplied by "
        "100 — an exact change of unit — so banks and co-operatives share one scale.",
        "Shinkin banks and credit co-operatives are not members of the Japanese "
        "Bankers Association and do not appear in jba-banks; this is their only "
        "per-institution line here.",
        "One point in time per edition: the FSA overwrites the same file each "
        "year, so the history is only what has been ingested here.",
        "Loans to SMEs and SME borrowers carry two year-ends per edition; every "
        "other figure is dated to the later one, the edition's as-of date.",
    ],
}
