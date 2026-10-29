# -*- coding: utf-8 -*-
"""US dated events, for "how did X do after Y" questions: one table of days
something happened, each row saying where the date came from.

Four kinds, each from the body that sets the date:

  fomc      the day of each FOMC statement since 2018 (the decision day of a
            two-day meeting), from the Federal Reserve's calendar page and its
            historical pages; meetings on the calendar that have no statement
            yet are kept as scheduled, so "the next FOMC" has a date
  cpi       the day the BLS released each monthly Consumer Price Index, from
            its archive of CPI news releases, plus the dates on its published
            schedule for the months ahead (scheduled)
  shutdown  each lapse in federal appropriations since 2018 that closed
            agencies, with its start and end day; the list is short and kept
            in this file against the Wikipedia article that collects them,
            and every start date is checked against the article's text when
            it is fetched (a row whose date is not found is marked unverified)
  results   the day a company filed an 8-K under Item 2.02, "Results of
            Operations and Financial Condition" — the form a quarterly results
            announcement is filed on — read from EDGAR's submissions JSON for
            every company with a ticker. is_earnings marks the one that is
            the quarter's results: the first Item 2.02 filing from seven to 75
            days after a fiscal quarter end (Tesla's delivery numbers, filed
            two days after quarter end under the same item, are not).

Usage:
    ./.venv/bin/python equity/us_events.py --db data/sec.work.duckdb
    ./.venv/bin/python equity/us_events.py --db data/sec.work.duckdb --kinds fomc,cpi,shutdown
    ./.venv/bin/python equity/us_events.py --db data/sec.work.duckdb --kinds results --max-companies 1500
    ./.venv/bin/python equity/us_events.py --db data/sec.work.duckdb --kinds results --tickers JPM,BAC

The table is rebuilt kind by kind: the rows of a kind are replaced in one
transaction when its source was read in full, and left as they were when it
was not. S&P 500 members are read every run; the other companies are
refreshed in turns (--max-companies a run, failed and oldest fetch first), so
a nightly run reads each of them about weekly; the first run, with no company
read yet, reads them all whatever the cap. Every page read is archived
under data/raw/us-events/ with its SHA-256 and recorded in us_event_pulls,
which the health row reads. The SEC and the BLS both refuse a client that
does not name a contact: EDGAR_USER_AGENT (or BLS_USER_AGENT for the BLS)
must be set, as for the other EDGAR and BLS jobs.
"""
import argparse
import calendar
import datetime
import gzip
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from app import sec_gate                                          # noqa: E402 — one gate for every SEC request on the host

RAW_ROOT = os.environ.get("US_EVENTS_RAW", os.path.join(HERE, "..", "data", "raw", "us-events"))
PARSER_VERSION = "events-1"
HISTORY_FROM = datetime.date(2018, 1, 1)          # daily prices begin 2018-05-01; nothing earlier is useful
# Pages of a company's EDGAR filing list read per company. A nightly read takes the
# latest six (12,000 filings: 2018 for nearly every company). A bank that files
# thousands of structured-note prospectuses needs far more: JPMorgan's six reached
# back only to April 2025 and it showed 6 results days, not 35 (2026-10-29). Such
# a company is read deep once, to 2018, and the nightly reads then keep the older rows.
SHALLOW_PAGES = 6
DEEP_PAGES = 80                                    # JPMorgan needs 54 to reach 2018
REMARK_DAYS = 80                                   # rows this close to the start of a read are left as stored
KINDS = ("fomc", "cpi", "shutdown", "results")
REFUSED_AFTER = 25          # this many failures before any success: the source is refusing us, stop

FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical%d.htm"
BLS_CPI_ARCHIVE = "https://www.bls.gov/bls/news-release/cpi.htm"
BLS_CPI_SCHEDULE = "https://www.bls.gov/schedule/news_release/cpi.htm"
SHUTDOWN_ARTICLE = "https://en.wikipedia.org/wiki/Government_shutdowns_in_the_United_States"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK%010d.json"
EDGAR_SUBMISSIONS_FILE = "https://data.sec.gov/submissions/%s"

# Federal shutdowns since 2018, as the article's table and text give them (a
# shutdown's days count both the first and the last day). Each start date is
# checked against the article when it is fetched.
SHUTDOWNS = [
    ("2018-01-20", "2018-01-22", "Federal government shutdown, 3 days (all agencies)"),
    ("2018-12-22", "2019-01-25", "Federal government shutdown, 35 days (agencies without full-year appropriations)"),
    ("2025-10-01", "2025-11-12", "Federal government shutdown, 43 days (all agencies)"),
    ("2026-01-31", "2026-02-03", "Federal government shutdown, 4 days (about half of departments)"),
    ("2026-02-14", "2026-04-30", "Department of Homeland Security shutdown, 76 days (end day from the article's day count)"),
]

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS us_event_seq START 1;
CREATE TABLE IF NOT EXISTS us_events (
    event_id INTEGER, kind VARCHAR, date DATE, end_date DATE, cik BIGINT, ticker VARCHAR,
    label VARCHAR, detail VARCHAR, scheduled BOOLEAN, is_earnings BOOLEAN,
    source VARCHAR, source_url VARCHAR, loaded_at TIMESTAMP);
CREATE SEQUENCE IF NOT EXISTS us_event_pull_seq START 1;
CREATE TABLE IF NOT EXISTS us_event_pulls (
    pull_id INTEGER, kind VARCHAR, requested_at TIMESTAMP, status VARCHAR, rows INTEGER,
    pages INTEGER, sha256 VARCHAR, raw_path VARCHAR, parser_version VARCHAR, detail VARCHAR);
CREATE TABLE IF NOT EXISTS us_event_filers (
    cik BIGINT, fetched_at TIMESTAMP, status VARCHAR, pages INTEGER, earliest DATE, detail VARCHAR);
"""

MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m: i for i, m in enumerate(calendar.month_abbr) if m})


class EventsError(Exception):
    pass


class Refused(EventsError):
    """The SEC refused this server, or the gate is holding after a refusal."""


# ---------------------------------------------------------------- fetching
def _env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            k, sep, val = raw.strip().partition("=")
            if sep and k.strip() == name:
                return val.strip().strip('"').strip("'")
    return ""


def user_agent(host):
    ua = (_env("BLS_USER_AGENT") if "bls.gov" in host else "") or _env("EDGAR_USER_AGENT")
    if not ua:
        raise EventsError("EDGAR_USER_AGENT (or BLS_USER_AGENT) is not set; the SEC and the BLS refuse an unnamed client")
    return ua


class Client(object):
    """GET with a named client, retries, a pause between calls and an archive of every body."""

    def __init__(self, raw_root, pause=0.25, log=print):
        self.raw_root = raw_root
        # at most 1/pause requests a second across every thread; SEC requests are also held to the
        # host-wide SEC_RATE (app/sec_gate.py, default 3), shared with every other SEC job
        self.pause = pause
        self.log = log
        self.last = 0.0
        self.pages = 0
        self.archived = []                 # (path, sha256)
        self._lock = threading.Lock()

    def _pace(self):
        with self._lock:
            wait = self.pause - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.pages += 1

    def get(self, url, timeout=60, tries=3):
        host = urllib.parse.urlsplit(url).netloc
        for attempt in range(tries):
            self._pace()
            headers = {"User-Agent": user_agent(host), "Accept-Encoding": "gzip",
                       "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
            if sec_gate.is_sec(url):
                status, body, _ = sec_gate.get(url, timeout=timeout, headers=headers)
                if sec_gate.is_refusal(status, body):
                    raise Refused("the SEC is refusing this server (%s)"
                                  % ("held after a refusal" if status == sec_gate.HELD else "HTTP %d" % status))
                if status == 200:
                    return body
                exc = (urllib.error.HTTPError(url, status, "HTTP %d" % status, {}, None) if status
                       else urllib.error.URLError("no answer"))
            else:
                req = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        body = resp.read()
                        if resp.headers.get("Content-Encoding") == "gzip":
                            body = gzip.decompress(body)
                    return body
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                    exc = e
            if getattr(exc, "code", None) == 404 or attempt + 1 == tries:
                raise exc
            time.sleep(2.0 * (attempt + 1))
            self.log("  retry %s after %s" % (url, exc))

    def archive(self, kind, name, body):
        day = datetime.date.today().isoformat()
        folder = os.path.join(self.raw_root, day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "%s-%s.gz" % (kind, name))
        with gzip.open(path, "wb") as f:
            f.write(body)
        sha = hashlib.sha256(body).hexdigest()
        self.archived.append((path, sha))
        return path, sha



# ---------------------------------------------------------------- parsers
def _text(markup):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def parse_fomc_statements(page):
    """Statement days on a Fed calendar or historical page: {date}."""
    out = set()
    for m in re.finditer(r"monetary(\d{4})(\d{2})(\d{2})a\.htm", page):
        try:
            out.add(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    return out


def parse_fomc_scheduled(page):
    """Meeting days on the calendar page, as (year, month, last day) -> decision date,
    for meetings that may not have a statement yet. A range "27-28" ends on the 28th;
    "31-1" runs into the next month."""
    out = set()
    year = None
    for m in re.finditer(r"(\d{4}) FOMC Meetings|fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?"
                         r"fomc-meeting__date[^>]*>\s*([^<]+)<", page, re.S):
        if m.group(1):
            year = int(m.group(1))
            continue
        if year is None:
            continue
        months = [MONTHS.get(x.strip()[:3].title()) or MONTHS.get(x.strip()) for x in m.group(2).split("/")]
        days = re.findall(r"\d{1,2}", m.group(3))
        if not days or not months or not months[0]:
            continue
        last_day = int(days[-1])
        month = months[-1] if len(months) > 1 else months[0]
        if len(months) == 1 and len(days) > 1 and int(days[-1]) < int(days[0]):
            month = month % 12 + 1
        try:
            out.add(datetime.date(year, month, last_day))
        except ValueError:
            continue
    return out


def parse_cpi_archive(page):
    """[(release day, reference month 'September 2026')] from the BLS archive of CPI releases."""
    out = []
    for m in re.finditer(r"cpi_(\d{2})(\d{2})(\d{4})\.htm\"[^>]*>([^<]*)<", page):
        try:
            d = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        ref = re.search(r"([A-Z][a-z]+ \d{4})", m.group(4))
        out.append((d, ref.group(1) if ref else None))
    return out


def parse_cpi_schedule(page):
    """[(release day, reference month)] from the BLS release schedule."""
    out = []
    for m in re.finditer(r"<td>\s*([A-Z][a-z]+ \d{4})\s*</td>\s*<td>\s*([A-Z][a-z]+)\.? (\d{1,2}), (\d{4})\s*</td>", page):
        month = MONTHS.get(m.group(2)[:3].title())
        if not month:
            continue
        try:
            out.append((datetime.date(int(m.group(4)), month, int(m.group(3))), m.group(1)))
        except ValueError:
            continue
    return out


def _date_words(d):
    return "%s %d, %d" % (calendar.month_name[d.month], d.day, d.year)


def shutdown_rows(article_text):
    """The shutdown list, each checked against the article's text."""
    rows = []
    for start, end, label in SHUTDOWNS:
        s, e = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
        verified = _date_words(s) in article_text
        rows.append((s, e, label, "start date found in the article" if verified else "start date not found in the article: unverified"))
    return rows


def results_from_submissions(recent):
    """[(filing date, items, accession)] of 8-K filings under Item 2.02 in one
    submissions page (the 'recent' block or an older file, same shape)."""
    out = []
    forms, dates, items, acc = recent.get("form") or [], recent.get("filingDate") or [], recent.get("items") or [], recent.get("accessionNumber") or []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        it = items[i] if i < len(items) else ""
        if "2.02" not in (it or ""):
            continue
        try:
            d = datetime.date.fromisoformat(dates[i])
        except (ValueError, IndexError):
            continue
        out.append((d, it, acc[i] if i < len(acc) else None))
    return out


def quarter_ends(fye, lo, hi):
    """Fiscal quarter ends (last day of the month) for a fiscal year ending in
    month fye ('1231' -> December) between lo and hi."""
    try:
        m = int(str(fye)[:2])
        if not 1 <= m <= 12:
            raise ValueError
    except (TypeError, ValueError):
        m = 12
    months = sorted({(m - 1 - 3 * k) % 12 + 1 for k in range(4)})
    out = []
    for y in range(lo.year - 1, hi.year + 2):
        for mm in months:
            d = datetime.date(y, mm, calendar.monthrange(y, mm)[1])
            if lo <= d <= hi:
                out.append(d)
    return sorted(out)


def mark_earnings(filings, fye, known_periods=()):
    """{filing date: (is_earnings, period end)} for a company's Item 2.02 filings:
    the first filing from seven days after a quarter end is that quarter's results."""
    if not filings:
        return {}
    dates = sorted({d for d, _, _ in filings})
    ends = set(quarter_ends(fye, dates[0] - datetime.timedelta(days=200), dates[-1]))
    ends.update(p for p in known_periods if p)
    ends = sorted(ends)
    out = {}
    taken = set()
    for d in dates:
        # the latest quarter end at least seven days before the filing
        p = None
        for e in ends:
            if e + datetime.timedelta(days=7) <= d:
                p = e
            else:
                break
        if p is None:
            out[d] = (False, None)
            continue
        is_e = p not in taken and (d - p).days <= 75      # a 10-K is due 90 days on; results come well before it
        if is_e:
            taken.add(p)
        out[d] = (is_e, p)
    return out


# ---------------------------------------------------------------- loading
def _connect(path):
    import duckdb
    con = duckdb.connect(path)
    for stmt in SCHEMA.strip().split(";"):
        if stmt.strip():
            con.execute(stmt)
    return con


def _record(con, kind, status, rows, pages, sha, raw_path, detail=None):
    con.execute("INSERT INTO us_event_pulls VALUES (nextval('us_event_pull_seq'), ?, now(), ?, ?, ?, ?, ?, ?, ?)",
                [kind, status, rows, pages, sha, raw_path, PARSER_VERSION, detail])


def _replace(con, kind, rows, cik=None, keep_before=None):
    """Replace a kind's rows (or one company's) in one transaction; a company's
    rows dated before keep_before stay as stored (its read did not reach them)."""
    con.execute("BEGIN")
    try:
        if cik is None:
            con.execute("DELETE FROM us_events WHERE kind = ?", [kind])
        elif keep_before is not None:
            con.execute("DELETE FROM us_events WHERE kind = ? AND cik = ? AND date >= ?", [kind, cik, keep_before])
        else:
            con.execute("DELETE FROM us_events WHERE kind = ? AND cik = ?", [kind, cik])
        for r in rows:
            con.execute("INSERT INTO us_events VALUES (nextval('us_event_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())", r)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def load_fomc(con, client, log=print, today=None):
    today = today or datetime.date.today()
    page = client.get(FOMC_CALENDAR).decode("utf-8", "replace")
    path, sha = client.archive("fomc", "calendar", page.encode("utf-8"))
    stated = parse_fomc_statements(page)
    scheduled = parse_fomc_scheduled(page)
    pages = 1
    for y in range(HISTORY_FROM.year, today.year - 4):
        try:
            hist = client.get(FOMC_HISTORICAL % y).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:           # a year the calendar page still carries has no historical page yet
            if exc.code == 404:
                continue
            raise
        client.archive("fomc", "historical%d" % y, hist.encode("utf-8"))
        stated |= parse_fomc_statements(hist)
        pages += 1
    rows = []
    for d in sorted(stated):
        if d >= HISTORY_FROM:
            rows.append(("fomc", d, None, None, None, "FOMC statement", "the Federal Open Market Committee's statement on its policy decision",
                         False, None, "Federal Reserve Board", FOMC_CALENDAR))
    for d in sorted(scheduled):
        if d >= today and d not in stated:
            rows.append(("fomc", d, None, None, None, "FOMC meeting (scheduled)", "a meeting on the Federal Reserve's published calendar; the statement is not out yet",
                         True, None, "Federal Reserve Board", FOMC_CALENDAR))
    if len([r for r in rows if not r[7]]) < 8 * (today.year - HISTORY_FROM.year):
        _record(con, "fomc", "failed", len(rows), pages, sha, path, "too few statement dates read: %d" % len(rows))
        raise EventsError("FOMC: too few statement dates read (%d)" % len(rows))
    _replace(con, "fomc", rows)
    _record(con, "fomc", "ok", len(rows), pages, sha, path)
    log("fomc: %d rows (%d scheduled)" % (len(rows), len([r for r in rows if r[7]])))
    return len(rows)


def load_cpi(con, client, log=print, today=None):
    today = today or datetime.date.today()
    page = client.get(BLS_CPI_ARCHIVE).decode("utf-8", "replace")
    path, sha = client.archive("cpi", "archive", page.encode("utf-8"))
    released = parse_cpi_archive(page)
    sched_page = client.get(BLS_CPI_SCHEDULE).decode("utf-8", "replace")
    client.archive("cpi", "schedule", sched_page.encode("utf-8"))
    scheduled = parse_cpi_schedule(sched_page)
    rows, seen = [], set()
    for d, ref in sorted(released):
        if d >= HISTORY_FROM and d not in seen:
            seen.add(d)
            rows.append(("cpi", d, None, None, None, "CPI release", ("Consumer Price Index for %s, released 8:30 ET" % ref) if ref else "Consumer Price Index release, 8:30 ET",
                         False, None, "U.S. Bureau of Labor Statistics", BLS_CPI_ARCHIVE))
    for d, ref in sorted(scheduled):
        if d >= today and d not in seen:
            seen.add(d)
            rows.append(("cpi", d, None, None, None, "CPI release (scheduled)", "Consumer Price Index for %s, on the BLS release schedule" % ref,
                         True, None, "U.S. Bureau of Labor Statistics", BLS_CPI_SCHEDULE))
    if len([r for r in rows if not r[7]]) < 11 * (today.year - HISTORY_FROM.year):
        _record(con, "cpi", "failed", len(rows), 2, sha, path, "too few release dates read: %d" % len(rows))
        raise EventsError("CPI: too few release dates read (%d)" % len(rows))
    _replace(con, "cpi", rows)
    _record(con, "cpi", "ok", len(rows), 2, sha, path)
    log("cpi: %d rows (%d scheduled)" % (len(rows), len([r for r in rows if r[7]])))
    return len(rows)


def load_shutdowns(con, client, log=print):
    page = client.get(SHUTDOWN_ARTICLE).decode("utf-8", "replace")
    path, sha = client.archive("shutdown", "article", page.encode("utf-8"))
    rows = []
    for s, e, label, detail in shutdown_rows(_text(page)):
        rows.append(("shutdown", s, e, None, None, label, detail, False, None, "Wikipedia, Government shutdowns in the United States", SHUTDOWN_ARTICLE))
    _replace(con, "shutdown", rows)
    unverified = [r for r in rows if "unverified" in r[6]]
    _record(con, "shutdown", "ok" if not unverified else "partial", len(rows), 1, sha, path,
            None if not unverified else "%d start dates not found in the article" % len(unverified))
    log("shutdown: %d rows (%d unverified)" % (len(rows), len(unverified)))
    return len(rows)


def _sp500(con):
    """CIKs on the latest S&P 500 member list (equity/sec_proxy_pay.py), or none before it is loaded."""
    try:
        return {r[0] for r in con.execute("""
            SELECT cik FROM us_index_members WHERE cik IS NOT NULL AND snapshot_id = (
                SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)')""").fetchall()}
    except Exception:                                              # noqa: BLE001 — no member list on this file
        return set()


def _short_history(con):
    """Companies whose last read stopped at the page limit short of HISTORY_FROM and
    whose stored results do not reach back to it: read deep once (DEEP_PAGES)."""
    return {r[0] for r in con.execute("""
        SELECT f.cik FROM us_event_filers f
        WHERE f.status = 'ok' AND f.pages >= ? AND f.earliest > ?
          AND coalesce((SELECT min(e.date) FROM us_events e WHERE e.kind = 'results' AND e.cik = f.cik),
                       DATE '9999-12-31') > ?""",
        [SHALLOW_PAGES, HISTORY_FROM, HISTORY_FROM + datetime.timedelta(days=366)]).fetchall()}


def _companies_due(con, max_companies, today, only=None):
    """[(cik, ticker, fye, [known period ends], deep)]: the S&P 500 first, then the rest's turn.
    `only` (tickers) reads just those companies, whatever their turn.

    S&P 500 members are read every night, since the questions asked are mostly
    about them and a results day is wanted the morning after it. The rest are
    read weekly, max_companies a run. A company whose read failed is due again
    the next night: 2026-10-23 the SEC refused 7,278 reads, each stamped as
    fetched, and the weekly rule then kept Tesla, Apple and Nvidia off the
    table for a week after the block was lifted."""
    rows = con.execute("""
        SELECT c.cik, c.ticker, f.fetched_at, f.status
        FROM sec_companies c LEFT JOIN us_event_filers f USING (cik)
        WHERE c.ticker IS NOT NULL""").fetchall()
    members = _sp500(con)
    short = _short_history(con)
    if only:
        want = {t.upper() for t in only}
        rows = [r for r in rows if (r[1] or "").upper() in want]
        members = {r[0] for r in rows}                 # every one named is due tonight
        today = today + datetime.timedelta(days=1)
    # never read and failed first, then the oldest read
    rows.sort(key=lambda r: (r[2] is not None and r[3] == "ok", r[2] or datetime.datetime.min, r[0]))
    head = [(cik, t) for cik, t, at, st in rows
            if cik in members and (at is None or st != "ok" or at.date() < today)]
    rest = [(cik, t) for cik, t, at, st in rows
            if cik not in members and (at is None or st != "ok" or (today - at.date()).days >= 7)]
    if max_companies and any(at is not None for _, _, at, _ in rows):
        rest = rest[:max_companies]        # a later run reads its turn; the first run reads every company
    due = head + rest
    out = []
    for cik, ticker in due:
        fye = con.execute("SELECT fye FROM sec_filings WHERE cik = ? AND form IN ('10-K', '10-Q') ORDER BY filed DESC LIMIT 1", [cik]).fetchone()
        periods = [r[0] for r in con.execute("SELECT DISTINCT period FROM sec_filings WHERE cik = ? AND form IN ('10-K', '10-Q') AND period IS NOT NULL", [cik]).fetchall()]
        out.append((cik, ticker, fye[0] if fye else None, periods, cik in short))
    return out


def fetch_results(client, cik, max_pages=SHALLOW_PAGES):
    """[(date, items, accession)] of a company's Item 2.02 8-Ks back to HISTORY_FROM, and the pages read."""
    body = client.get(EDGAR_SUBMISSIONS % cik)
    client.archive("results", "cik%d" % cik, body)
    d = json.loads(body.decode("utf-8"))
    filings = results_from_submissions(d.get("filings", {}).get("recent", {}))
    pages = 1
    recent_from = min((d.get("filings", {}).get("recent", {}).get("filingDate") or ["9999-12-31"]))
    for f in d.get("filings", {}).get("files", []) or []:
        if pages >= max_pages:
            break
        if (f.get("filingTo") or "0000") < HISTORY_FROM.isoformat() or (recent_from <= HISTORY_FROM.isoformat()):
            break
        older = client.get(EDGAR_SUBMISSIONS_FILE % f["name"])
        client.archive("results", f["name"].replace(".json", ""), older)
        od = json.loads(older.decode("utf-8"))
        filings += results_from_submissions(od)
        pages += 1
        recent_from = min(recent_from, f.get("filingFrom") or recent_from)
    return [x for x in filings if x[0] >= HISTORY_FROM], pages, recent_from


def _fetch_one(client, item):
    """(item, filings, pages, earliest, error): one company's filings, for a worker thread."""
    cik = item[0]
    try:
        filings, pages, earliest = fetch_results(client, cik, DEEP_PAGES if item[4] else SHALLOW_PAGES)
        return item, filings, pages, earliest, None
    except Refused as exc:
        return item, None, 0, None, "refused: %s" % exc
    except urllib.error.HTTPError as exc:
        return item, None, 0, None, "HTTP %s" % exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return item, None, 0, None, str(exc)[:200]


def load_results(con, client, max_companies=1500, log=print, today=None, workers=6, only=None):
    """Companies are fetched on `workers` threads (the client paces every
    request across them); the database is written by this thread alone."""
    from concurrent.futures import ThreadPoolExecutor
    today = today or datetime.date.today()
    due = _companies_due(con, max_companies, today, only)
    log("results: %d companies due, %d read deep to %s" % (len(due), sum(1 for d in due if d[4]), HISTORY_FROM))
    n_rows = n_ok = n_fail = 0
    t0 = time.time()
    refused = None
    batch = max(1, workers) * 4                    # queued a few at a time, so a stop really stops

    def fetched(ex):
        for b in range(0, len(due), batch):
            if refused:
                return
            for out in ex.map(lambda item: _fetch_one(client, item), due[b:b + batch]):
                yield out
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, (item, filings, pages, earliest, err) in enumerate(fetched(ex)):
            cik, ticker, fye, periods, _deep = item
            if err is not None:
                con.execute("DELETE FROM us_event_filers WHERE cik = ?", [cik])
                con.execute("INSERT INTO us_event_filers VALUES (?, now(), 'failed', 0, NULL, ?)", [cik, err])
                n_fail += 1
                # the SEC refusing every request is a block on the address, not one bad company:
                # stop instead of knocking 8,000 times (2026-10-23: the server's address was refused).
                # The gate says so at the first refusal; REFUSED_AFTER catches a block that looks otherwise.
                if err.startswith("refused") or (n_ok == 0 and n_fail >= REFUSED_AFTER):
                    refused = err
                    break
                continue
            marks = mark_earnings(filings, fye, periods)
            rows = []
            for d, items, acc in sorted(filings):
                is_e, p = marks.get(d, (False, None))
                detail = "Item 2.02 (results of operations)%s%s" % (
                    ("; the results for the quarter to %s" % p.isoformat()) if is_e and p else "",
                    ("; items %s" % items) if items else "")
                rows.append(("results", d, None, cik, ticker, "Results announced (8-K)" if is_e else "Results-related 8-K",
                             detail, False, is_e, "U.S. Securities and Exchange Commission, EDGAR",
                             "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%010d&type=8-K" % cik))
            # a read that stopped short of HISTORY_FROM keeps the stored rows before it; rows near its
            # start are kept too, since their quarter's first filing may lie before the read
            read_from = datetime.date.fromisoformat(earliest) if earliest and earliest != "9999-12-31" else None
            keep_before = read_from + datetime.timedelta(days=REMARK_DAYS) if read_from and read_from > HISTORY_FROM else None
            if keep_before:
                rows = [r for r in rows if r[1] >= keep_before]
            _replace(con, "results", rows, cik=cik, keep_before=keep_before)
            con.execute("DELETE FROM us_event_filers WHERE cik = ?", [cik])
            con.execute("INSERT INTO us_event_filers VALUES (?, now(), 'ok', ?, ?, ?)",
                        [cik, pages, datetime.date.fromisoformat(earliest) if earliest and earliest != "9999-12-31" else None,
                         "%d Item 2.02 filings, %d marked as quarterly results" % (len(rows), sum(1 for r in rows if r[8]))])
            n_rows += len(rows)
            n_ok += 1
            if (i + 1) % 200 == 0:
                log("  %d/%d companies, %d rows, %.0fs" % (i + 1, len(due), n_rows, time.time() - t0))
    if refused:
        _record(con, "results", "failed", n_rows, client.pages, None, None,
                "the SEC refused this server after %d companies read and %d failed (%s); stopped"
                % (n_ok, n_fail, refused))
        raise EventsError("the SEC refused this server after %d companies read and %d failed (%s): the address is blocked"
                          % (n_ok, n_fail, refused))
    _record(con, "results", "ok" if n_ok or not due else "failed", n_rows, client.pages, None, None,
            "%d companies read, %d failed" % (n_ok, n_fail))
    log("results: %d companies read, %d failed, %d rows, %.0fs" % (n_ok, n_fail, n_rows, time.time() - t0))
    return n_rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="the sec.duckdb to load (a working copy, never the served file)")
    ap.add_argument("--kinds", default=",".join(KINDS), help="comma-separated: fomc,cpi,shutdown,results")
    ap.add_argument("--tickers", default="", help="results only for these companies (comma-separated tickers), whatever their turn")
    ap.add_argument("--max-companies", type=int, default=1500, help="companies outside the S&P 500 to refresh this run, "
                         "on top of every member (0 = every company due)")
    ap.add_argument("--raw", default=RAW_ROOT)
    ap.add_argument("--workers", type=int, default=6, help="threads fetching company filings (requests are paced as one)")
    args = ap.parse_args(argv)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        print("unknown kind(s): %s" % ", ".join(bad))
        return 2
    con = _connect(args.db)
    client = Client(args.raw)
    failed = []
    for kind in kinds:
        try:
            if kind == "fomc":
                load_fomc(con, client)
            elif kind == "cpi":
                load_cpi(con, client)
            elif kind == "shutdown":
                load_shutdowns(con, client)
            elif kind == "results":
                load_results(con, client, max_companies=args.max_companies, workers=args.workers,
                             only=[t.strip() for t in args.tickers.split(",") if t.strip()] or None)
        except Exception as exc:                                   # noqa: BLE001 — one kind failing leaves the others
            print("%s FAILED: %s" % (kind, exc))
            failed.append(kind)
            try:
                _record(con, kind, "failed", 0, client.pages, None, None, str(exc)[:300])
            except Exception:                                       # noqa: BLE001
                pass
    con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
