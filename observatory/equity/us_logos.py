# -*- coding: utf-8 -*-
"""US company logos, for the small icon beside a company's name in a list.

One square PNG per company, named by CIK, under web/assets/logos/us/, served
from our own host like any other asset: a reader's browser never asks a
third party for a logo. Run by hand when the member list changes; logos
change rarely and nothing reads them at boot.

  domain   the company's official website from Wikidata (P856, looked up by
           the SEC's CIK, P5531), or DOMAINS below for a company Wikidata
           does not link to its CIK (mostly holding companies and spin-offs
           newer than their Wikidata entry)
  icon     Google's site-icon service for that domain, else the site's own
           apple-touch-icon, then the icons its homepage links to; the first
           at least MIN_PX square is resized to SIZE and saved as PNG, and a
           16px favicon is the last resort

sources.json records, for each CIK, the ticker, domain, the URL the icon came
from, its SHA-256 and the day it was fetched; a company with no usable icon
is listed with icon null and the page shows no logo for it.

Usage:
    ./.venv/bin/python equity/us_logos.py                 # S&P 500 members
    ./.venv/bin/python equity/us_logos.py --only NVDA,BRK-B --force
"""
import argparse
import datetime
import hashlib
import io
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

import duckdb
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "assets" / "logos" / "us"
UA = "PloverAnalytics/1.0 (aaron.jy@gmail.com)"
SIZE = 64
MIN_PX = 32
SMALL_PX = 16

# Wikidata has no official website against these CIKs. Each domain is the
# company's own site, and the icon it gives was looked at by eye.
DOMAINS = {
    "XOM": "exxonmobil.com", "GEV": "gevernova.com", "MRVL": "marvell.com", "SNDK": "sandisk.com",
    "BLK": "blackrock.com", "CEG": "constellationenergy.com", "VRT": "vertiv.com",
    "LITE": "lumentum.com", "APP": "applovin.com", "BE": "bloomenergy.com",
    "TRGP": "targaresources.com", "ADSK": "autodesk.com", "HONA": "honeywell.com",
    "CIEN": "ciena.com", "PYPL": "paypal.com", "FERG": "ferguson.com", "CVNA": "carvana.com",
    "IBKR": "interactivebrokers.com", "KVUE": "kenvue.com", "AXON": "axon.com", "EQT": "eqt.com",
    "EME": "emcorgroup.com", "GEHC": "gehealthcare.com", "WSM": "williams-sonomainc.com",
    "PCG": "pge.com", "WST": "westpharma.com", "IR": "irco.com", "VICI": "viciproperties.com",
    "ARES": "aresmgmt.com", "Q": "qnityelectronics.com", "VLTO": "veralto.com",
    "SW": "smurfitwestrock.com", "TPL": "texaspacific.com", "SMCI": "supermicro.com",
    "BG": "bunge.com", "APA": "apacorp.com", "FICO": "fico.com", "INVH": "invitationhomes.com",
    "GDDY": "godaddy.com", "FDXF": "fedex.com", "ECHO": "echostar.com", "TECH": "bio-techne.com",
    "TKO": "tkogrp.com", "LULU": "lululemon.com", "PODD": "insulet.com", "NCLH": "nclhltd.com",
    "ERIE": "erieinsurance.com", "SKYD": "paramount.com",
    # Wikidata's website is a country site, an IR subdomain or an old name.
    "LOW": "lowes.com", "VZ": "verizon.com", "BEN": "franklintempleton.com", "FITB": "53.com",
    "CARR": "carrier.com", "UAL": "united.com", "CMCSA": "comcastcorporation.com",
    "TT": "tranetechnologies.com", "DOC": "healthpeak.com", "WEC": "wecenergygroup.com", "MDLZ": "mondelezinternational.com", "MRK": "merck.com",
    "MU": "micron.com", "NXPI": "nxp.com", "SHW": "sherwin-williams.com", "STZ": "cbrands.com",
    "NFLX": "netflix.com", "HAS": "hasbro.com", "MTB": "mtb.com", "NDAQ": "nasdaq.com", "V": "visa.com",
    # a product, a former name or an old company's site (checked 2026-10-24)
    "AAPL": "apple.com", "COHR": "coherent.com", "EXE": "expandenergy.com",
    "GL": "globelifeinsurance.com", "TPR": "tapestry.com", "TWLO": "twilio.com",
    "UNH": "unitedhealthgroup.com",
}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def members(db):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("""
            SELECT cik, ticker FROM us_index_members
            WHERE snapshot_id = (SELECT max(snapshot_id) FROM us_index_snapshots
                                 WHERE index_name = 'S&P 500 (SPY holdings)')
            ORDER BY weight_pct DESC""").fetchall()
    finally:
        con.close()


def wikidata_sites(ciks):
    """{cik: domain} from each item's official website; a preferred-rank
    statement wins, then a .com host (items also list country sites), then
    the shortest."""
    vals = " ".join('"%s"' % str(c).zfill(10) for c in ciks)
    q = ("SELECT ?cik ?site ?rank WHERE { VALUES ?cik { %s } ?item wdt:P5531 ?cik . "
         "?item p:P856 ?st . ?st ps:P856 ?site . ?st wikibase:rank ?rank }" % vals)
    req = urllib.request.Request(
        "https://query.wikidata.org/sparql",
        data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = json.load(r)["results"]["bindings"]
    best = {}
    for b in rows:
        cik = int(b["cik"]["value"])
        host = urllib.parse.urlparse(b["site"]["value"]).hostname or ""
        host = host[4:] if host.startswith("www.") else host
        if not host:
            continue
        key = (not b["rank"]["value"].endswith("PreferredRank"), not host.endswith(".com"), len(host))
        if cik not in best or key < best[cik][0]:
            best[cik] = (key, host)
    return {c: h for c, (_, h) in best.items()}


def _square(data, min_px=MIN_PX):
    """The image as a SIZE-px RGBA PNG, or None if it is smaller than min_px
    or not square enough to be an icon."""
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "format", "") == "ICO":
            im.size = max(im.ico.sizes())
        im = im.convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    if min(w, h) < min_px or max(w, h) > 1.25 * min(w, h):
        return None
    im = im.resize((SIZE, SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_LINK = re.compile(r"<link\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-z-]+)\s*=\s*["\']([^"\']*)["\']', re.I)


def page_icons(domain):
    """The icon links a homepage declares, largest first: apple-touch-icon
    and sized rel=icon links, which are often bigger than its favicon."""
    for home in ("https://www.%s/" % domain, "https://%s/" % domain):
        try:
            status, html = _get(home, timeout=15)
        except Exception:
            continue
        found = []
        for tag in _LINK.findall(html[:300000].decode("utf-8", "replace")):
            a = {k.lower(): v for k, v in _ATTR.findall(tag)}
            rel = a.get("rel", "").lower()
            if "icon" not in rel or not a.get("href") or "mask" in rel:
                continue
            px = max([int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))] or
                     [180 if "apple" in rel else 0])
            found.append((px, urllib.parse.urljoin(home, a["href"])))
        return [u for _, u in sorted(found, reverse=True)]
    return []


def icon(domain):
    """(png bytes, source url) or (None, None)."""
    def tries():
        yield "https://www.google.com/s2/favicons?domain=%s&sz=%d" % (domain, SIZE)
        yield "https://%s/apple-touch-icon.png" % domain
        yield "https://www.%s/apple-touch-icon.png" % domain
        for u in page_icons(domain):
            if not u.lower().split("?")[0].endswith(".svg"):
                yield u
    small = (None, None)
    for url in tries():
        try:
            status, data = _get(url)
        except Exception:
            continue
        png = _square(data) if status == 200 else None
        if png:
            return png, url
        if status == 200 and small[0] is None:
            small = (_square(data, SMALL_PX), url)
    # Some companies publish only a browser-tab favicon: soft at this size,
    # but still the company's own mark.
    return small if small[0] else (None, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "sec.duckdb"))
    ap.add_argument("--only", default="", help="comma-separated tickers")
    ap.add_argument("--force", action="store_true", help="refetch icons already on disk")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    src_path = OUT / "sources.json"
    sources = json.loads(src_path.read_text()) if src_path.exists() else {}
    rows = members(a.db)
    if a.only:
        want = {t.strip().upper() for t in a.only.split(",")}
        rows = [r for r in rows if r[1].upper() in want]
    sites = wikidata_sites([c for c, _ in rows])
    today = datetime.date.today().isoformat()
    got = 0
    for cik, ticker in rows:
        path = OUT / ("%d.png" % cik)
        if path.exists() and not a.force:
            got += 1
            continue
        domain = DOMAINS.get(ticker) or sites.get(cik)
        png, url = icon(domain) if domain else (None, None)
        if png:
            path.write_bytes(png)
            got += 1
        elif path.exists():
            path.unlink()
        sources[str(cik)] = {"ticker": ticker, "domain": domain, "icon": url,
                             "sha256": hashlib.sha256(png).hexdigest() if png else None,
                             "fetched": today}
        print("%-6s %-28s %s" % (ticker, domain or "-", "ok" if png else "no icon"), flush=True)
        time.sleep(0.3)
    src_path.write_text(json.dumps(sources, indent=1, sort_keys=True) + "\n")
    print("%d of %d members have a logo" % (got, len(rows)))


if __name__ == "__main__":
    sys.exit(main())
