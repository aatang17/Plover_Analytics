# -*- coding: utf-8 -*-
"""Finding each S&P 500 member's news feed, once: the slow, one-off half of
app/us_press.py. Writes equity/us_press_feeds.json; see app/us_press.py.

For each member: its website from Wikidata (P856, looked up by SEC CIK,
P5531), then the investor, newsroom and main hosts under that domain and any
investor site the main site links to, the feed links each page names, one hop
to a page listing feeds, and the paths investor-site providers use. A feed is
kept when it parses, has recent items and is about press releases (not
events, filings or a blog). Measured 2026-10-19: see the run's summary line.
A feed fixed by hand ("how": "manual") is never replaced.
"""
import concurrent.futures
import socket
import datetime as dt
import html
import re
import urllib.parse
import urllib.request
import json

from .us_press import SEC_DB, UA, fetch, load_feeds, parse_feed, save_feeds

QUICK = 7      # seconds: a newsroom that has not answered by then is not one to poll

# ---------------------------------------------------------------- members


def members():
    """The latest S&P 500 snapshot: [{cik, ticker, name}], heaviest first."""
    import duckdb
    con = duckdb.connect(SEC_DB, read_only=True)
    try:
        sid = con.execute("SELECT max(snapshot_id) FROM us_index_snapshots "
                          "WHERE index_name LIKE 'S&P 500%'").fetchone()[0]
        rows = con.execute("SELECT cik, ticker, name FROM us_index_members WHERE snapshot_id = ? "
                           "ORDER BY weight_pct DESC", [sid]).fetchall()
    finally:
        con.close()
    return [{"cik": int(r[0]), "ticker": r[1], "name": r[2]} for r in rows if r[0]]


def websites(ciks):
    """{cik: official website} from Wikidata (P5531 SEC CIK, P856 website)."""
    out = {}
    for i in range(0, len(ciks), 200):
        vals = " ".join('"%010d"' % c for c in ciks[i:i + 200])
        q = ("SELECT ?cik ?site WHERE { VALUES ?cik { %s } ?item wdt:P5531 ?cik . "
             "?item wdt:P856 ?site . }" % vals)
        req = urllib.request.Request(
            "https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
            headers={"Accept": "application/sparql-results+json", "User-Agent": UA})
        d = json.load(urllib.request.urlopen(req, timeout=120))
        found = {}
        for b in d["results"]["bindings"]:
            found.setdefault(int(b["cik"]["value"]), []).append(b["site"]["value"])
        for cik, urls in found.items():
            out[cik] = sorted(urls, key=_site_rank)[0]
    return out


def _site_rank(u):
    p = urllib.parse.urlsplit(u)
    host = p.netloc.lower()
    return (not host.endswith(".com"), len(p.path.strip("/")) > 0, len(host), u)


def base_domain(url):
    host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------------------------------------------------------- feeds

# Where investor-site providers put the press-release feed: Q4
# (/news-events/press-releases/rss), Notified (/rss/news-releases.xml), and
# the common hand-built ones. Tried on each investor or news host found.
FEED_PATHS = ("/news-events/press-releases/rss", "/rss/news-releases.xml", "/rss/pressrelease.aspx",
              "/news-releases/rss", "/press-releases/rss", "/news/press-releases/rss",
              "/releases.xml", "/rss/news.xml", "/news/rss.xml", "/news/rss", "/feed/", "/rss",
              "/rss.xml", "/press-releases/rss.xml", "/news-releases?format=rss")
# Pages on a company's main site that link to its investor site, which is
# often on another domain (pginvestor.com, investor.citizensbank.com).
IR_PAGES = ("/investors", "/investor-relations", "/investors/", "/about/investors", "/en/investors",
            "/company/investors", "/ir")
NOT_RELEASES = re.compile(r"event|presentation|annual.?report|sec.?filing|webcast|calendar|blog|podcast|"
                          r"career|job|stock.?quote", re.I)
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
_A_TAG = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>", re.I)



def _feed_links(page_url, body):
    """Feed addresses a page names: <link rel=alternate type=rss/atom> and
    links whose address mentions rss or a news/press .xml file."""
    text = body.decode("utf-8", "replace")
    out = []
    for tag in _LINK_TAG.findall(text):
        if re.search(r"application/(rss|atom)\+xml", tag, re.I):
            m = re.search(r"href=[\"']([^\"']+)", tag, re.I)
            if m:
                out.append(urllib.parse.urljoin(page_url, html.unescape(m.group(1))))
    for href in _A_TAG.findall(text):
        h = html.unescape(href)
        if re.search(r"(^|[/=._-])rss([/=._-]|$)|\.xml($|\?)", h, re.I) and re.search(r"news|press|release", h, re.I):
            out.append(urllib.parse.urljoin(page_url, h))
    seen, uniq = set(), []
    for u in out:
        if u not in seen and u.startswith("http"):
            seen.add(u)
            uniq.append(u)
    return uniq[:12]


def _judge(url, items, company):
    """A feed's score: recent, plenty of items, on an investor or news host,
    and about press releases. Higher is better; None means not usable."""
    if not items or len(items) < 3:
        return None
    dates = sorted(i["published"] for i in items if i["published"])
    if not dates:
        return None
    newest = dates[-1]
    age = (dt.date.today() - dt.date.fromisoformat(newest)).days
    if age > 400:
        return None
    host = urllib.parse.urlsplit(url).netloc.lower()
    score = 100 - min(age, 100)
    score += 30 if re.search(r"(^|\.)(investor|investors|ir)\.", host) else 0
    score += 20 if re.search(r"news|press|release", url, re.I) else 0
    score += min(len(items), 20)
    words = " ".join(i["title"] for i in items).lower()
    if re.search(r"report|results|dividend|announc|quarter", words):
        score += 25
    # "news-events" is the investor-site section that holds the press
    # releases (Q4's /news-events/press-releases/rss), not an events feed
    path = re.sub(r"news-?(and-)?events", "news", urllib.parse.urlsplit(url).path + "?" +
                  urllib.parse.urlsplit(url).query, flags=re.I)
    if NOT_RELEASES.search(path):
        return None
    return score


def _resolves(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


GOOD_ENOUGH = 150      # a recent feed on an investor host about press releases: stop looking


def discover_one(member, site):
    """The best news feed for one company, or a record saying none was found.
    Hosts that do not resolve are skipped without a request; each candidate
    feed is judged as soon as it is found, and the search stops at the first
    one that is plainly right."""
    tried = []
    best = {"score": None}
    seen = set()

    def consider(url):
        if url in seen or len(seen) > 60:
            return False
        seen.add(url)
        final, status, ctype, body = fetch(url, timeout=QUICK)
        if status != 200 or not body:
            return False
        items = parse_feed(body)
        score = _judge(final, items, member["name"])
        if score is not None and (best["score"] is None or score > best["score"]):
            best.update(url=final, score=score, items=len(items),
                        newest=max(i["published"] for i in items if i["published"]))
        return best["score"] is not None and best["score"] >= GOOD_ENOUGH

    domain = base_domain(site)
    label = domain.split(".")[0]
    hosts = [h for h in ("investor." + domain, "investors." + domain, "ir." + domain, "newsroom." + domain,
                         "news." + domain, label + "news." + domain) if _resolves(h)]
    # the investor site the main site links to, when it lives on another domain
    for path in IR_PAGES[:3]:
        page_url, status, ctype, body = fetch("https://www.%s%s" % (domain, path), timeout=QUICK)
        if status != 200:
            continue
        for href in _A_TAG.findall(body.decode("utf-8", "replace")):
            h = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, html.unescape(href))).netloc.lower()
            if (h and h not in hosts and re.search(r"invest|(^|\.)ir\.|newsroom|news\.", h)
                    and len(hosts) < 9):
                hosts.insert(0, h)
        for u in _feed_links(page_url, body):
            if consider(u):
                break
        break
    hosts.append("www." + domain)
    for host in hosts:
        if best["score"] is not None and best["score"] >= GOOD_ENOUGH:
            break
        page_url, status, ctype, body = fetch("https://%s/" % host, timeout=QUICK)
        tried.append("%s %s" % (host, status))
        if status != 200:
            continue
        if b"<rss" in body[:2000] or b"<feed" in body[:2000]:
            if consider(page_url):
                break
        found = False
        for u in _feed_links(page_url, body):
            if consider(u):
                found = True
                break
        if found:
            break
        # one hop to a page that lists the feeds ("RSS feeds")
        for href in _A_TAG.findall(body.decode("utf-8", "replace")):
            h = html.unescape(href)
            if re.search(r"rss|feeds", h, re.I) and not re.search(r"\.(svg|png|ico|css|js)", h, re.I):
                sub = urllib.parse.urljoin(page_url, h)
                s_url, s_status, s_ctype, s_body = fetch(sub, timeout=QUICK)
                if s_status == 200:
                    if b"<rss" in s_body[:2000] or b"<feed" in s_body[:2000]:
                        found = consider(s_url)
                    else:
                        for u in _feed_links(s_url, s_body):
                            if consider(u):
                                found = True
                                break
                break
        if found:
            break
        if not host.startswith("www."):
            base = "https://" + urllib.parse.urlsplit(page_url).netloc
            for p in FEED_PATHS:
                if consider(base + p):
                    break
    rec = {"cik": member["cik"], "ticker": member["ticker"], "name": member["name"], "site": site,
           "checked": dt.date.today().isoformat()}
    if best["score"] is not None:
        rec.update(feed=best["url"], how="found", items=best["items"], newest=best["newest"])
    else:
        rec.update(feed=None, how="not found", tried=tried[:8])
    return rec


def discover(only=None, workers=4):
    mem = members()
    if only:
        mem = [m for m in mem if m["ticker"] in only]
    known = load_feeds()
    sites = websites([m["cik"] for m in mem])
    todo = [m for m in mem if (known.get(str(m["cik"])) or {}).get("how") != "manual"]
    out = dict(known)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for m in todo:
            site = sites.get(m["cik"])
            if not site:
                out[str(m["cik"])] = {"cik": m["cik"], "ticker": m["ticker"], "name": m["name"],
                                      "site": None, "feed": None, "how": "no website",
                                      "checked": dt.date.today().isoformat()}
                continue
            futs[ex.submit(discover_one, m, site)] = m
        for f in concurrent.futures.as_completed(futs):
            rec = f.result()
            out[str(rec["cik"])] = rec
            print("%-6s %-10s %s" % (rec["ticker"], rec["how"], rec.get("feed") or ""), flush=True)
            done = sum(1 for _ in [0])  # noqa: F841
            if len(out) % 25 == 0:
                save_feeds(out)
    save_feeds(out)
    found = sum(1 for r in out.values() if r.get("feed"))
    print("us-press discover: %d of %d members have a feed" % (found, len(out)))
    return out
