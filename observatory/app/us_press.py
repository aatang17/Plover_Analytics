# -*- coding: utf-8 -*-
"""S&P 500 press releases: what companies announce on their own newsrooms.

2026-10-19: Nvidia announced a $150 billion increase to its buyback programme
on 28 September 2026 on nvidianews.nvidia.com and filed nothing with the SEC,
so Plover could not say when Nvidia last bought back shares. This watches
each S&P 500 company's own news feed (the user's call: these are the
companies people ask about), keeps every release's headline, date and
address, archives the ones about returning cash (buybacks and dividends)
with their SHA-256, and reads the figures in them.

  discover   find each member's news feed once (equity/us_press_feeds.json):
             its website from Wikidata by SEC CIK, then the feed on its
             investor, newsroom or main site. A feed marked "how": "manual"
             in that file is never replaced.
  poll       read every feed and keep new releases; fetch, archive and read
             the capital-return ones. Writes data/us-press.sqlite. The site's
             watch loop runs it every POLL_EVERY_HOURS (app/refresh.py).
  status     how many members have a feed, and when the newest release was.

    ./.venv/bin/python -m app.us_press discover [--only NVDA,AAPL] [--workers 4]
    ./.venv/bin/python -m app.us_press poll [--only NVDA]
    ./.venv/bin/python -m app.us_press status

The store is SQLite, not DuckDB: it is written every few hours beside the
serving process, which DuckDB's one-writer rule forbids and SQLite's WAL mode
allows (readers never wait). Releases are only ever added: a release kept is
never rewritten, so what a page said on a day can be shown again.

Trust: a release is the company's own words, not a filing. Every figure is
stored with the sentence it came from and the release's address, and is
shown as "Company announcement, not filed with the SEC". Nothing is
inferred: a programme being increased is stored as that, never as shares
bought.
"""
import argparse
import concurrent.futures
import datetime as dt
import email.utils
import gzip
import hashlib
import html
import json
import os
import pathlib
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import sec_gate

ROOT = pathlib.Path(__file__).resolve().parent.parent
FEEDS_PATH = ROOT / "equity" / "us_press_feeds.json"
DB_PATH = pathlib.Path(os.environ.get("US_PRESS_DB") or (ROOT / "data" / "us-press.sqlite"))
RAW_ROOT = ROOT / "data" / "raw" / "us-press"
SEC_DB = os.environ.get("SEC_DB_PATH") or str(ROOT / "data" / "sec.duckdb")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 PloverAnalytics/1.0 (+https://ploveranalytics.com)")
TIMEOUT = 12
POLL_EVERY_HOURS = float(os.environ.get("US_PRESS_POLL_HOURS", "3"))
STALE_AFTER_DAYS = 2           # no successful poll for this long: the health report says so
WORKERS = 4

CREDIT = ("Company press releases: from each company's own news feed (not filed with the SEC), and the "
          "releases companies attach to 8-K filings (U.S. Securities and Exchange Commission, EDGAR).")
SOURCES = {"newsroom": "Company announcement, not filed with the SEC",
           "sec-8k": "Company press release, filed with the SEC as an 8-K exhibit"}
SEC_LOOKBACK_DAYS = int(os.environ.get("US_PRESS_SEC_DAYS", "120"))
SEC_ITEMS = ("2.02", "7.01", "8.01")      # results, Regulation FD, other events: where releases are attached

# ---------------------------------------------------------------- fetching


def fetch(url, timeout=TIMEOUT, limit=4 * 1024 * 1024):
    """(final url, status, content type, bytes). Never raises: an address that
    does not answer is status 0. An SEC address goes through the gate."""
    if sec_gate.is_sec(url):
        try:
            status, body, h = sec_gate.get(url, timeout=timeout, max_bytes=limit)
        except RuntimeError:                       # no EDGAR_USER_AGENT
            return url, 0, "", b""
        ctype = {k.lower(): v for k, v in h.items()}.get("content-type") or ""
        return url, max(status, 0), ctype, body if status == 200 else b""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*",
                                               "Accept-Language": "en-US,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(limit)
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.geturl(), r.status, r.headers.get("Content-Type") or "", body
    except urllib.error.HTTPError as exc:
        return url, exc.code, "", b""
    except Exception:  # noqa: BLE001 — DNS, TLS, timeout
        return url, 0, "", b""


# ---------------------------------------------------------------- feeds file


def load_feeds():
    try:
        return json.loads(FEEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_feeds(feeds):
    tmp = FEEDS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(feeds.items(), key=lambda kv: kv[1].get("ticker") or "")),
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(FEEDS_PATH))


# ---------------------------------------------------------------- reading feeds


def parse_feed(body):
    """[{title, link, published (ISO date or ''), guid}] from RSS or Atom, or None."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    tag = root.tag.lower()
    items = []

    def child(node, name):
        return next((c.text or "" for c in node if c.tag.lower().endswith(name)), "")
    if tag.endswith("rss") or tag.endswith("rdf"):
        for it in [n for n in root.iter() if n.tag.lower().endswith("item")]:
            items.append({"title": _text(child(it, "title")), "link": child(it, "link").strip(),
                          "published": _date(child(it, "pubdate") or child(it, "date")),
                          "guid": child(it, "guid").strip()})
    elif tag.endswith("feed"):
        for it in [n for n in root if n.tag.lower().endswith("entry")]:
            link = next((c.get("href") for c in it if c.tag.lower().endswith("link") and c.get("href")), "")
            items.append({"title": _text(child(it, "title")), "link": link,
                          "published": _date(child(it, "published") or child(it, "updated")),
                          "guid": child(it, "id").strip()})
    else:
        return None
    return [i for i in items if i["title"] and i["link"].startswith("http")]


def _text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _date(s):
    s = (s or "").strip()
    if not s:
        return ""
    try:
        return email.utils.parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- what a release says

# Titles that are about returning cash: these releases are fetched, archived
# and read. Everything else keeps its headline, date and address only.
CAPITAL_RETURN = re.compile(r"repurchas|buy-?back|dividend|return(s|ed|ing)? .{0,20}capital|"
                            r"capital return|tender offer|accelerated share", re.I)

_AMT = r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|tn)?"
_SCALE = {"trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, None: 1.0, "": 1.0}

# Each rule: (kind, pattern, label). The first amount group is the figure.
FACT_RULES = (
    ("authorization_added",
     re.compile(r"(?:additional|incremental|new|increase[sd]? (?:of|by)?|increase (?:its|the) .{0,40}?by)\s+"
                + _AMT + r"[^.]{0,120}?(?:repurchase|buyback)", re.I),
     "Buyback approval added"),
    ("authorization_remaining",
     re.compile(r"(?:approximately|about)?\s*" + _AMT + r"\s+(?:remaining|available|left)[^.]{0,80}?"
                r"(?:repurchase|buyback|authori[sz]ation)", re.I),
     "Buyback approval remaining"),
    ("authorization_remaining",
     re.compile(r"(?:remaining|available)[^.$]{0,60}?(?:repurchase|buyback|authori[sz](?:ation|ed))[^.$]{0,40}?" + _AMT, re.I),
     "Buyback approval remaining"),
    ("accelerated_repurchase",
     re.compile(r"accelerated share repurchase[^.]{0,140}?" + _AMT, re.I),
     "Accelerated buyback"),
    ("repurchased",
     re.compile(r"(?:repurchased|bought back)\s+(?:approximately\s+)?[\d.,]+\s*(?:million|billion)?\s+shares"
                r"[^.]{0,80}?" + _AMT, re.I),
     "Shares bought back"),
    ("dividend_per_share",
     re.compile(r"(?:quarterly|cash|regular|special)[^.]{0,40}?dividend of\s+\$\s?([\d]+\.\d+|[\d]+)\s+per\s+share", re.I),
     "Dividend per share"),
)

# A dividend on preferred stock is a different instrument, not the common
# dividend: "a preferred cash dividend of $14.75", or "$12.15 per share on each
# of our Series A ... preferred stock". Read from the words just before the
# figure and the clause after it, up to the next dividend in the sentence.
_PREFERRED_BEFORE = re.compile(r"preferred\s+(?:stock\s+)?$", re.I)
_SHARE_CLASS_AFTER = re.compile(r"\b(preferred|common|ordinary|class [a-c])\b", re.I)


def _dividend_kind(sentence, m, next_start):
    if _PREFERRED_BEFORE.search(sentence[max(0, m.start() - 20):m.start()]):
        return "preferred_dividend_per_share", "Preferred dividend per share"
    after = sentence[m.end():min(next_start, m.end() + 120)]
    first = _SHARE_CLASS_AFTER.search(after)
    if first and first.group(1).lower() == "preferred":
        return "preferred_dividend_per_share", "Preferred dividend per share"
    return "dividend_per_share", "Dividend per share"


def _sentences(text):
    # split on full stops that end a sentence, not decimal points or "Inc."
    return [s.strip() for s in re.split(r"(?<=[a-z0-9\)%])\.\s+(?=[A-Z])", text) if s.strip()]


def read_facts(text):
    """[{kind, label, value, unit, sentence}] from a release's text. Each
    figure comes with the sentence it was read from; nothing is computed."""
    out, seen = [], set()
    for sentence in _sentences(text)[:400]:
        if len(sentence) > 900:
            continue
        for rule_kind, rx, rule_label in FACT_RULES:
            # a sentence can declare two dividends (preferred and common); read each
            matches = list(rx.finditer(sentence)) if rule_kind == "dividend_per_share" else [rx.search(sentence)]
            for i, m in enumerate(matches):
                if not m:
                    continue
                kind, label = rule_kind, rule_label
                raw = m.group(1).replace(",", "")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if rule_kind == "dividend_per_share":
                    next_start = matches[i + 1].start() if i + 1 < len(matches) else len(sentence)
                    kind, label = _dividend_kind(sentence, m, next_start)
                    unit = "USD per share"
                else:
                    scale = (m.group(2) or "").lower() if m.lastindex and m.lastindex >= 2 else ""
                    value = value * _SCALE.get(scale, 1.0)
                    unit = "USD"
                    if value < 1e6:          # "$150" with no scale is not a buyback programme
                        continue
                key = (kind, round(value, 2))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"kind": kind, "label": label, "value": value, "unit": unit,
                            "sentence": sentence[:600]})
    return out


def page_text(body):
    """A release page's readable text: the article if the page marks one,
    else the paragraphs."""
    raw = body.decode("utf-8", "replace")
    m = re.search(r"(?is)<meta[^>]+name=[\"']description[\"'][^>]*content=[\"']([^\"']+)", raw)
    summary = _text(m.group(1)) if m else ""
    t = re.sub(r"(?is)<(script|style|noscript|nav|header|footer)\b.*?</\1>", " ", raw)
    # the release body, where the page names it (newsroom platforms use these
    # classes); else the longest article block, as a page can hold several
    # (related stories) and the release is the long one
    body_at = re.search(r"(?is)<(?:div|section)[^>]+class=[\"'][^\"']*\b(article-body|press-release-body|"
                        r"release-body|news-body|entry-content|article-content|module_body|wd_body)\b", t)
    if body_at:
        t = t[body_at.start(): body_at.start() + 40000]
    else:
        blocks = re.findall(r"(?is)<article\b.*?</article>", t) or re.findall(r"(?is)<main\b.*?</main>", t)
        if blocks:
            t = max(blocks, key=lambda b: len(_text(b)))
    paras = re.findall(r"(?is)<(?:p|li|h2|h3)\b[^>]*>(.*?)</(?:p|li|h2|h3)>", t)
    text = " ".join(_text(p) for p in paras) if paras else _text(t)
    text = re.sub(r"\s+", " ", text).strip()
    if summary and summary[:60] not in text:
        text = summary.rstrip(".") + ". " + text
    return text


# ---------------------------------------------------------------- the store

SCHEMA = """
CREATE TABLE IF NOT EXISTS press_releases (
    release_id INTEGER PRIMARY KEY,
    cik INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    published TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'newsroom',
    accession TEXT,
    capital_return INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    fetched_at TEXT,
    sha256 TEXT,
    raw_path TEXT,
    text_chars INTEGER
);
CREATE INDEX IF NOT EXISTS press_by_company ON press_releases (cik, published);
CREATE TABLE IF NOT EXISTS press_facts (
    fact_id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES press_releases(release_id),
    cik INTEGER NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    sentence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_by_company ON press_facts (cik, kind);
CREATE TABLE IF NOT EXISTS press_polls (
    poll_id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    feeds INTEGER,
    feeds_ok INTEGER,
    feeds_failed TEXT,
    new_releases INTEGER,
    read_releases INTEGER
);
"""

_lock = threading.Lock()


def connect(readonly=False):
    if readonly:
        if not DB_PATH.exists():
            return None
        con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=10)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_PATH), timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def _now():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _archive(ticker, body):
    sha = hashlib.sha256(body).hexdigest()
    day = dt.date.today().isoformat()
    path = RAW_ROOT / day / ("%s-%s.html.gz" % (re.sub(r"[^A-Za-z0-9-]", "", ticker), sha[:16]))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with gzip.open(str(path), "wb") as fh:
            fh.write(body)
    return sha, str(path.relative_to(ROOT))


# ---------------------------------------------------------------- polling


def _poll_feed(rec):
    """(rec, items or None)."""
    final, status, ctype, body = fetch(rec["feed"])
    if status != 200 or not body:
        return rec, None
    return rec, parse_feed(body)


def _read_release(row):
    """Fetch, archive and read one capital-return release: (row, sha, path, text, facts)."""
    final, status, ctype, body = fetch(row["url"])
    if status != 200 or not body:
        return row, None, None, "", []
    sha, path = _archive(row["ticker"], body)
    text = page_text(body)
    return row, sha, path, text, read_facts(text)


def poll(only=None, workers=WORKERS, log=print, sec=True):
    """Read every feed once; keep new releases; read the capital-return ones.
    Returns the poll's summary. Safe to run while the site serves."""
    feeds = [r for r in load_feeds().values() if r.get("feed")]
    if only:
        feeds = [r for r in feeds if r.get("ticker") in only]
    con = connect()
    started = _now()
    with _lock:
        cur = con.execute("INSERT INTO press_polls (started_at, feeds) VALUES (?, ?)", (started, len(feeds)))
        poll_id = cur.lastrowid
        con.commit()
    ok, failed, new = 0, [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for rec, items in ex.map(_poll_feed, feeds):
            if items is None:
                failed.append(rec["ticker"])
                continue
            ok += 1
            with _lock:
                for it in items:
                    cr = 1 if CAPITAL_RETURN.search(it["title"]) else 0
                    try:
                        con.execute(
                            "INSERT INTO press_releases (cik, ticker, title, url, published, feed_url, "
                            "capital_return, first_seen) VALUES (?,?,?,?,?,?,?,?)",
                            (rec["cik"], rec["ticker"], it["title"][:500], it["link"],
                             it["published"] or started[:10], rec["feed"], cr, started))
                        new += 1
                    except sqlite3.IntegrityError:
                        pass          # already kept: a kept release is never rewritten
                con.commit()
    todo = [dict(r) for r in con.execute(
        "SELECT release_id, ticker, cik, url FROM press_releases WHERE capital_return = 1 AND fetched_at IS NULL "
        "ORDER BY published DESC LIMIT 200").fetchall()]
    read = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for row, sha, path, text, facts in ex.map(_read_release, todo):
            with _lock:
                con.execute("UPDATE press_releases SET fetched_at = ?, sha256 = ?, raw_path = ?, text_chars = ? "
                            "WHERE release_id = ? AND fetched_at IS NULL",
                            (_now(), sha, path, len(text), row["release_id"]))
                for f in facts:
                    con.execute("INSERT INTO press_facts (release_id, cik, kind, label, value, unit, sentence) "
                                "VALUES (?,?,?,?,?,?,?)",
                                (row["release_id"], row["cik"], f["kind"], f["label"], f["value"], f["unit"],
                                 f["sentence"]))
                con.commit()
            read += 1 if sha else 0
    if sec:
        s_new, s_read, s_failed = poll_sec(only, workers, log, con)
        new, read = new + s_new, read + s_read
        failed = failed + ["8-K:" + t for t in s_failed]
    with _lock:
        con.execute("UPDATE press_polls SET finished_at = ?, feeds_ok = ?, feeds_failed = ?, new_releases = ?, "
                    "read_releases = ? WHERE poll_id = ?",
                    (_now(), ok, json.dumps(failed), new, read, poll_id))
        con.commit()
    con.close()
    out = {"feeds": len(feeds), "ok": ok, "failed": failed, "new_releases": new, "read_releases": read}
    log("us-press: %(ok)d of %(feeds)d feeds read, %(new_releases)d new releases, "
        "%(read_releases)d capital-return releases read" % out)
    return out


def _edgar_ua():
    """The SEC asks every automated reader to name itself (EDGAR_USER_AGENT,
    "Company contact@domain"); without it every request is refused."""
    return sec_gate.user_agent()


# Every request to the SEC goes through app/sec_gate.py: at most SEC_RATE a
# second across every process and thread on the host, retries included.
# 2026-10-19: the 8-K backfill read 1,046 filings in 12 minutes on four
# threads that each paused only between companies, and the SEC refused the
# server's address from then on, for every SEC job. Once the SEC answers with
# its refusal page the gate holds every SEC job on the host, and the rest of
# this run asks it nothing (sec_refused()).
_SEC_STATE = {"refused": False}


def sec_refused():
    return _SEC_STATE["refused"]


def _sec_get(url, ua, tries=4):
    """(status, body). A refusal (429, or 403 with the HTML refusal page), or
    the hold the gate keeps after one, is taken as a block: nothing more is
    asked this run. A missing daily index is also a 403, but a short non-HTML
    one, and comes back as 403. A network fault is tried again."""
    if _SEC_STATE["refused"]:
        return 0, b""
    for attempt in range(tries):
        status, body, _ = sec_gate.get(url, timeout=20, max_bytes=8 * 1024 * 1024, headers={"User-Agent": ua})
        if sec_gate.is_refusal(status, body):
            _SEC_STATE["refused"] = True
            return 0, b""
        if status == 0:
            time.sleep(2 * (attempt + 1))
            continue
        return (status, body) if 200 <= status < 300 else (status, b"")
    return 0, b""


_ROW = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_ROW_TYPE = re.compile(r"(?is)<td[^>]*>\s*(EX-99[^<]*?)\s*</td>")
_ROW_HREF = re.compile(r"(?i)href=\"([^\"]+)\"")


def _exhibit_links(index_html):
    """(document address, type) for each EX-99 row of a filing's index page.
    Inline-XBRL documents are linked through the SEC's viewer (/ix?doc=...):
    the document itself is what the viewer names."""
    out = []
    for row in _ROW.findall(index_html):
        t = _ROW_TYPE.search(row)
        h = _ROW_HREF.search(row)
        if not t or not h:
            continue
        href = h.group(1)
        m = re.match(r"/ix\?doc=(/Archives/[^&]+)", href)
        if m:
            href = m.group(1)
        out.append((href, t.group(1)))
    return out


def _release_title(text):
    """A filed release's headline: its first real sentence, not 'Exhibit 99.1'
    or the SEC's document header ("EX-99.2 3 name.htm COMPANY")."""
    while True:
        before = text
        text = re.sub(r"^\s*EX-99[.\d]*\s+(\d+\s+\S+\.html?\s*|Document\s+)?", "", text, flags=re.I)
        text = re.sub(r"^\s*(Exhibit|Ex\.?)\s*99[.\d]*\s*", "", text, flags=re.I)
        if text == before:
            break
    for part in re.split(r"(?<=[.!?•])\s+|\s+•\s+", text)[:15]:
        p = part.strip(" •-")
        words = p.split()
        # a headline has words in lower case too: "JPMORGAN CHASE & CO." is the letterhead
        if len(words) >= 5 and p != p.upper():
            return p[:200]
    return text[:200] or "Press release"


def _sec_filings(member, ua, since):
    """New 8-K press-release exhibits for one member since a date: [(row, body)]."""
    status, body = _sec_get("https://data.sec.gov/submissions/CIK%010d.json" % member["cik"], ua)
    if status != 200:
        return None
    try:
        recent = json.loads(body)["filings"]["recent"]
    except (ValueError, KeyError):
        return None
    out = []
    for i, form in enumerate(recent.get("form") or []):
        if form not in ("8-K", "8-K/A"):
            continue
        filed = recent["filingDate"][i]
        items = (recent.get("items") or [""] * (i + 1))[i] or ""
        if filed < since or not any(it in items for it in SEC_ITEMS):
            continue
        acc = recent["accessionNumber"][i]
        out.append({"accession": acc, "filed": filed, "items": items})
    return out


def _sec_exhibits(member, filing, ua):
    """The EX-99 documents of one 8-K: [(url, body)]."""
    acc = filing["accession"]
    base = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (member["cik"], acc.replace("-", ""))
    status, idx = _sec_get(base + acc + "-index.htm", ua)
    if status != 200:
        return None
    out = []
    for href, typ in _exhibit_links(idx.decode("utf-8", "replace")):
        if not re.search(r"\.html?$", href, re.I):
            continue
        url = urllib.parse.urljoin("https://www.sec.gov/", href)
        st, body = _sec_get(url, ua)
        if st != 200 or not body:
            return None            # a filing read in part is not kept: it is tried again next poll
        out.append((url, body))
    return out


def add_release(ticker, url, published=None, log=print):
    """Keep one release by its address (a newsroom page), read and archive it.
    For an announcement that had left the company's feed before Plover began
    watching it. The release's own date is used when given."""
    rec = next((r for r in load_feeds().values() if (r.get("ticker") or "").upper() == ticker.upper()), None)
    if rec is None:
        raise SystemExit("%s is not in the S&P 500 feeds file" % ticker)
    final, status, ctype, body = fetch(url)
    if status != 200 or not body:
        raise SystemExit("%s answered %s" % (url, status))
    sha, path = _archive(rec["ticker"], body)
    text = page_text(body)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body.decode("utf-8", "replace"))
    title = _text(m.group(1)).split(" | ")[0] if m else _release_title(text)
    facts = read_facts(text)
    con = connect()
    with _lock:
        try:
            cur = con.execute(
                "INSERT INTO press_releases (cik, ticker, title, url, published, feed_url, source, capital_return, "
                "first_seen, fetched_at, sha256, raw_path, text_chars) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec["cik"], rec["ticker"], title[:500], url, published or dt.date.today().isoformat(),
                 rec.get("feed") or "manual", "newsroom", 1 if (facts or CAPITAL_RETURN.search(title)) else 0,
                 _now(), _now(), sha, path, len(text)))
        except sqlite3.IntegrityError:
            con.close()
            log("already kept: %s" % url)
            return None
        for f in facts:
            con.execute("INSERT INTO press_facts (release_id, cik, kind, label, value, unit, sentence) "
                        "VALUES (?,?,?,?,?,?,?)", (cur.lastrowid, rec["cik"], f["kind"], f["label"], f["value"],
                                                   f["unit"], f["sentence"]))
        con.commit()
    con.close()
    log("kept %s %s (%d figures)" % (rec["ticker"], title[:80], len(facts)))
    return facts


def reread(log=print):
    """Read every archived release again with the current rules. The archived
    copy (and its SHA-256) is the record and never changes; what Plover read
    from it (figures, a filed release's headline) is replaced."""
    con = connect()
    rows = [dict(r) for r in con.execute(
        "SELECT release_id, cik, source, raw_path, title FROM press_releases WHERE raw_path IS NOT NULL")]
    changed = 0
    for r in rows:
        path = ROOT / r["raw_path"]
        try:
            with gzip.open(str(path), "rb") as fh:
                text = page_text(fh.read())
        except OSError:
            continue
        facts = read_facts(text)
        with _lock:
            con.execute("DELETE FROM press_facts WHERE release_id = ?", (r["release_id"],))
            for f in facts:
                con.execute("INSERT INTO press_facts (release_id, cik, kind, label, value, unit, sentence) "
                            "VALUES (?,?,?,?,?,?,?)", (r["release_id"], r["cik"], f["kind"], f["label"],
                                                       f["value"], f["unit"], f["sentence"]))
            if r["source"] == "sec-8k":
                title = _release_title(text)
                if title != r["title"]:
                    con.execute("UPDATE press_releases SET title = ? WHERE release_id = ?", (title, r["release_id"]))
                    changed += 1
            if facts:
                con.execute("UPDATE press_releases SET capital_return = 1 WHERE release_id = ?", (r["release_id"],))
            con.commit()
    con.close()
    log("us-press: re-read %d archived releases (%d headlines changed)" % (len(rows), changed))
    return len(rows)


def _daily_8ks(day, ua, ciks):
    """{accession: (cik, filed)} for the 8-Ks of these companies in the SEC's
    daily form index; None when the day has no index (weekend, holiday, or
    not yet published), which the SEC answers with a 403."""
    q = (day.month - 1) // 3 + 1
    url = "https://www.sec.gov/Archives/edgar/daily-index/%d/QTR%d/form.%s.idx" % (day.year, q, day.strftime("%Y%m%d"))
    status, body = _sec_get(url, ua)
    if status != 200 or not body:
        return None
    out = {}
    for line in body.decode("latin-1").splitlines():
        if not (line.startswith("8-K ") or line.startswith("8-K/A ")):
            continue
        m = re.search(r"\s(\d+)\s+(\d{8})\s+(edgar/data/\S+)\s*$", line)
        if not m or int(m.group(1)) not in ciks:
            continue
        acc = re.search(r"(\d{10}-\d{2}-\d{6})", m.group(3))
        if acc:
            d = m.group(2)
            out[acc.group(1)] = (int(m.group(1)), "%s-%s-%s" % (d[:4], d[4:6], d[6:]))
    return out


def poll_sec(only=None, workers=WORKERS, log=print, con=None):
    """8-K press releases for every S&P 500 member: (new releases, read)."""
    _SEC_STATE["refused"] = False                 # each poll asks afresh: a block may have been lifted
    ua = _edgar_ua()
    if not ua:
        log("us-press: EDGAR_USER_AGENT is not set; the 8-K releases were skipped")
        return 0, 0, ["(no EDGAR_USER_AGENT)"]
    mem = [r for r in load_feeds().values() if r.get("cik")]
    if only:
        mem = [m for m in mem if m.get("ticker") in only]
    own = con is None
    con = con or connect()
    since = (dt.date.today() - dt.timedelta(days=SEC_LOOKBACK_DAYS)).isoformat()
    known = set(r[0] for r in con.execute("SELECT accession FROM press_releases WHERE accession IS NOT NULL"))
    new, read, failed = 0, 0, []
    last_sec = con.execute("SELECT max(published) FROM press_releases WHERE source = 'sec-8k'").fetchone()[0]
    covered = con.execute("SELECT count(DISTINCT cik) FROM press_releases WHERE source = 'sec-8k'").fetchone()[0]
    # The daily index only reaches back to the newest filing kept, so a
    # catch-up that was cut short (the SEC refusing a fast reader) would never
    # be filled in: until nearly every member has its filed releases, each
    # poll catches up company by company again (filings kept are skipped).
    caught_up = covered >= 0.9 * len(mem)
    if last_sec and caught_up and not only:
        # Routine: the SEC's daily form index for each day since the newest kept
        # filing (one request a day), not 500 company lists every poll.
        by_cik = dict((int(m["cik"]), m) for m in mem)
        start = dt.date.fromisoformat(last_sec) - dt.timedelta(days=2)
        todo = []
        day = start
        while day <= dt.date.today():
            if day.weekday() < 5:
                found = _daily_8ks(day, ua, set(by_cik))
                if found:
                    todo.extend((acc, cik, filed) for acc, (cik, filed) in found.items() if acc not in known)
                time.sleep(0.2)
            day += dt.timedelta(days=1)
        for acc, cik, filed in todo:
            m = by_cik[cik]
            base = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, acc.replace("-", ""))
            status, idx = _sec_get(base + acc + "-index.htm", ua)
            if status != 200:
                failed.append(m["ticker"])
                continue
            # only the items press releases are attached to
            if not re.search(r"Item\s*(2\.02|7\.01|8\.01)", idx.decode("utf-8", "replace")):
                continue
            exhibits = _sec_exhibits(m, {"accession": acc}, ua)
            if exhibits is None:
                failed.append(m["ticker"])
                continue
            for url, body in exhibits:
                if _keep_filed(con, m, {"accession": acc, "filed": filed}, url, body):
                    new += 1
                    read += 1
        if own:
            con.close()
        log("us-press: %d new 8-K releases from the SEC's daily index (%d filings could not be read)%s"
            % (new, len(failed), "; the SEC refused this server, so the rest were not asked" if sec_refused() else ""))
        return new, read, failed

    def one(m):
        if sec_refused():
            return m, None
        filings = _sec_filings(m, ua, since)          # every request is paced by sec_gate
        if filings is None:
            return m, None
        got = []
        for f in filings:
            if f["accession"] in known:
                continue
            exhibits = _sec_exhibits(m, f, ua)
            if exhibits is None:
                return m, None
            for url, body in exhibits:
                got.append((f, url, body))
        return m, got

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, 4)) as ex:
        for m, got in ex.map(one, mem):
            if got is None:
                failed.append(m["ticker"])
                continue
            for f, url, body in got:
                if _keep_filed(con, m, f, url, body):
                    new += 1
                    read += 1
    if own:
        con.close()
    log("us-press: %d new 8-K releases from %d members (%d could not be read)" % (new, len(mem), len(failed)))
    return new, read, failed


def _keep_filed(con, m, f, url, body):
    """Keep one filed release: archive, read, insert. False if already kept."""
    sha, path = _archive(m["ticker"], body)
    text = page_text(body)
    facts = read_facts(text)
    cr = 1 if (CAPITAL_RETURN.search(text[:20000]) or facts) else 0
    with _lock:
        try:
            cur = con.execute(
                "INSERT INTO press_releases (cik, ticker, title, url, published, feed_url, source, "
                "accession, capital_return, first_seen, fetched_at, sha256, raw_path, text_chars) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (m["cik"], m["ticker"], _release_title(text), url, f["filed"],
                 "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%d&type=8-K" % m["cik"],
                 "sec-8k", f["accession"], cr, _now(), _now(), sha, path, len(text)))
        except sqlite3.IntegrityError:
            return False
        for fact in facts:
            con.execute("INSERT INTO press_facts (release_id, cik, kind, label, value, unit, sentence) "
                        "VALUES (?,?,?,?,?,?,?)", (cur.lastrowid, m["cik"], fact["kind"], fact["label"],
                                                   fact["value"], fact["unit"], fact["sentence"]))
        con.commit()
    return True


_last_poll = {"at": 0.0}


def poll_if_due():
    """Called from the site's watch loop: poll when POLL_EVERY_HOURS have passed
    since the last finished poll (or never), and only where the feeds file and
    US_PRESS_POLL are on."""
    if str(os.environ.get("US_PRESS_POLL", "1")).strip().lower() in ("0", "false", "no", "off"):
        return None
    if not FEEDS_PATH.exists():
        return None
    if time.time() - _last_poll["at"] < POLL_EVERY_HOURS * 3600:
        return None
    con = connect(readonly=True)
    if con is not None:
        try:
            row = con.execute("SELECT max(finished_at) FROM press_polls").fetchone()
            last = row[0] if row else None
        except sqlite3.Error:
            last = None
        con.close()
        if last:
            age = (dt.datetime.utcnow() - dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
            if age < POLL_EVERY_HOURS * 3600:
                _last_poll["at"] = time.time() - age
                return None
    _last_poll["at"] = time.time()
    try:
        out = poll()
    except Exception as exc:  # noqa: BLE001 — kept for the health report, then raised to the loop's log
        _write_status({"at": _now(), "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])})
        raise
    _write_status({"at": _now(), "error": None})
    return out


STATUS_PATH = DB_PATH.with_name("us-press-status.json")


def _write_status(rec):
    """The last poll's outcome, readable by the health report: a poll that
    fails is otherwise only a line in a log nobody reads."""
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(rec), encoding="utf-8")
    except OSError:
        pass


def _read_status():
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- reading the store


def company(ticker_or_cik, limit=20):
    """A company's feed, its latest releases and its capital-return figures,
    newest first; None if it is not an S&P 500 member with a feed record."""
    feeds = load_feeds()
    key = str(ticker_or_cik).strip().upper()
    rec = feeds.get(key) or next((r for r in feeds.values() if (r.get("ticker") or "").upper() == key), None)
    if rec is None:
        return None
    out = {"cik": rec["cik"], "ticker": rec["ticker"], "name": rec.get("name"), "feed": rec.get("feed"),
           "feed_found": rec.get("how"), "releases": [], "capital_return": []}
    con = connect(readonly=True)
    if con is None:
        return out
    try:
        for r in con.execute("SELECT release_id, title, url, published, capital_return, source FROM press_releases "
                             "WHERE cik = ? ORDER BY published DESC, release_id DESC LIMIT ?",
                             (rec["cik"], int(limit))):
            out["releases"].append({"title": r["title"], "url": r["url"], "published": r["published"],
                                    "capital_return": bool(r["capital_return"]),
                                    "source": SOURCES.get(r["source"], r["source"])})
        for r in con.execute(
                "SELECT f.kind, f.label, f.value, f.unit, f.sentence, p.title, p.url, p.published, p.sha256, p.source "
                "FROM press_facts f JOIN press_releases p USING (release_id) WHERE f.cik = ? "
                "ORDER BY p.published DESC, f.fact_id", (rec["cik"],)):
            out["capital_return"].append({"kind": r["kind"], "label": r["label"], "value": r["value"],
                                          "unit": r["unit"], "sentence": r["sentence"],
                                          "release": {"title": r["title"], "url": r["url"],
                                                      "published": r["published"], "sha256": r["sha256"],
                                                      "source": SOURCES.get(r["source"], r["source"])}})
    finally:
        con.close()
    return out


def latest_capital_return(ticker_or_cik):
    """The newest release with capital-return figures, as {release, facts}, or None."""
    c = company(ticker_or_cik, limit=1)
    if not c or not c["capital_return"]:
        return None
    first = c["capital_return"][0]["release"]
    facts = [f for f in c["capital_return"] if f["release"]["url"] == first["url"]]
    return {"release": first, "facts": [{k: f[k] for k in ("kind", "label", "value", "unit", "sentence")}
                                        for f in facts]}


def health():
    """One entry in the shape /catalog/health lists the equity extractors in."""
    feeds = load_feeds()
    with_feed = sum(1 for r in feeds.values() if r.get("feed"))
    con = connect(readonly=True)
    last, newest, failed, running = None, None, [], None
    if con is not None:
        try:
            row = con.execute("SELECT finished_at, feeds_failed FROM press_polls WHERE finished_at IS NOT NULL "
                              "ORDER BY poll_id DESC LIMIT 1").fetchone()
            if row:
                last, failed = row[0], json.loads(row[1] or "[]")
            newest = con.execute("SELECT max(published) FROM press_releases").fetchone()[0]
            running = con.execute("SELECT max(started_at) FROM press_polls WHERE finished_at IS NULL").fetchone()[0]
        except sqlite3.Error:
            pass
        con.close()
    days = None
    if last:
        days = (dt.datetime.utcnow() - dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")).days
    stale = days is None or days > STALE_AFTER_DAYS
    status = _read_status()
    sec_failed = [f for f in failed if str(f).startswith("8-K:")]
    feed_failed = [f for f in failed if not str(f).startswith("8-K:")]
    return [{
        "dataset": "us-press-releases",
        "status": "attention" if (stale or status.get("error")) else "ok",
        "archive_read_through": newest,
        "days_behind": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": last,
        # shape_flags is for {table, column, detail} records of a table changing
        # shape (app/refresh.py reads it so); this entry's sentences are notes
        "shape_flags": [],
        "notes": (["%d of %d S&P 500 members have a news feed" % (with_feed, len(feeds))]
                  + (["%d news feeds failed in the last poll" % len(feed_failed)] if feed_failed else [])
                  + (["%d 8-K filings could not be read in the last poll" % len(sec_failed)]
                     if sec_failed else [])
                  + (["a poll started at %s has not finished" % running] if running else [])
                  + (["last poll failed at %s: %s" % (status["at"], status["error"])]
                     if status.get("error") else [])),
    }]


# ---------------------------------------------------------------- command line


def main(argv=None):
    ap = argparse.ArgumentParser(description="S&P 500 press releases")
    ap.add_argument("command", choices=("discover", "poll", "status", "add", "reread"))
    ap.add_argument("--ticker", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--published", default="", help="the release's date, YYYY-MM-DD")
    ap.add_argument("--only", default="", help="tickers, comma-separated")
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args(argv)
    only = set(t.strip().upper() for t in a.only.split(",") if t.strip()) or None
    if a.command == "discover":
        from . import us_press_discover
        us_press_discover.discover(only, a.workers)
    elif a.command == "poll":
        poll(only, a.workers)
    elif a.command == "add":
        add_release(a.ticker, a.url, a.published or None)
    elif a.command == "reread":
        reread()
    else:
        print(json.dumps(health(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
