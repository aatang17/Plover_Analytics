# -*- coding: utf-8 -*-
"""PloverResearch, the public side: the list, each article, its versions.

Every page is built here on the server, so a search engine or an AI reader
gets the whole text without running a script. The browser script
(``assets/research.js``) only draws the charts, from data embedded in the
page — the numbers frozen at publication, never a live request — so a chart
can never disagree with the sentence beside it.

Addresses
---------
``/research``                       every live article, newest first
``/research/<slug>``                the current version
``/research/<slug>/v<n>``           a specific version, kept for citations
``/research/<slug>.md``             the current version as Markdown
``/research/feed.xml``              RSS
``/research/media/<sha256>.<ext>``  an uploaded image, immutable
``/sitemap-research.xml``           for search engines

That is PloverResearch (publication 1). Every other publication has the same
set under ``/p/<publication>`` — ``/p/<publication>/<slug>``,
``/p/<publication>/feed.xml`` and so on — with its own name on the page and a
line saying the views are its writers' own. Images are shared, at
``/research/media``.

The pages set ``<base href="/">`` because the shared site header links pages
relatively ("cpi.html"), and those links have to work from /research/x/v2.
They also carry a Content-Security-Policy: what a writer types is cleaned to
an allowlist before it is stored, and the policy means that even a mistake in
that cleaner could not run a script on a reader's machine.
"""
import datetime
import html
import json
import logging
import os
import threading
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)

from . import newsletter, pub_profile, research, research_doc as rd, seo

log = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

BRAND = "PloverResearch"
# Cloudflare adds its cookieless Web Analytics beacon to every page it serves;
# the data pages send no policy and count, so the research pages allow that one
# script and its reporting address, or every article visit goes uncounted.
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' https://static.cloudflareinsights.com; "
       "connect-src 'self' https://cloudflareinsights.com; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")
SECURITY_HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "strict-origin-when-cross-origin"}

esc = rd.esc


# ---------------------------------------------------------------------------
# chart data, frozen

def build_snapshots(doc, as_of):
    """{chart block id: the observations response as it stood on ``as_of``}.

    Read through the same function that serves /api/v1/{dataset}/observations,
    with ``as_of`` — so the CSV link under each chart returns exactly the
    numbers drawn. A chart that cannot be read carries its reason instead."""
    from fastapi import HTTPException as _HTTPException

    from . import api, registry
    out = {}
    for b in doc["blocks"]:
        if b["type"] != "chart":
            continue
        if not b["dataset"] or not b["series"]:
            out[b["id"]] = {"error": "No data selected for this chart."}
            continue
        adapter = api.ADAPTERS.get(b["dataset"])
        if adapter is None:
            out[b["id"]] = {"error": "The dataset '%s' is not on this server." % b["dataset"]}
            continue
        try:
            body = api.observations(
                b["dataset"], series=",".join(b["series"]), measure=b["measure"],
                start=b["start"] or None, end=b["end"] or None, as_of=as_of,
                period=None, fy_end=3, format="json", request=None)
        except _HTTPException as exc:
            out[b["id"]] = {"error": str(exc.detail)}
            continue
        except Exception as exc:  # noqa: BLE001 — one chart must not sink the article
            log.exception("research chart %s failed", b["id"])
            out[b["id"]] = {"error": "The data could not be read (%s)." % type(exc).__name__}
            continue
        if not any(p[1] is not None for s in body["series"] for p in s["points"]):
            out[b["id"]] = {"error": "No values in the chosen date range."}
            continue
        manifest = getattr(adapter, "MANIFEST", None) or {}
        credit = (manifest.get("source") or {}).get("credit") \
            or adapter.PRESENTATION.get("credit_line") \
            or "Source: %s." % adapter.DATASET.get("agency", "")
        body["credit"] = credit
        body["as_of"] = as_of
        body["frequency"] = adapter.DATASET.get("frequency")
        card = registry.get(b["dataset"]) or {}
        body["page"] = card.get("page")
        body["title"] = (manifest.get("name") or {}).get("en") or adapter.DATASET.get("title")
        out[b["id"]] = body
    return out


# ---------------------------------------------------------------------------
# page shell

def _date_long(iso):
    if not iso:
        return ""
    d = datetime.date.fromisoformat(iso[:10])
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def _authors_text(names):
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _json_script(obj, element_id, kind="application/json"):
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # "</script" inside the data would end the element early
    text = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    return '<script type="%s" id="%s">%s</script>' % (kind, element_id, text)


def _home():
    return research.publication(research.HOME)


def page(title, description, canonical, main, data=None, robots=None, ld=None,
         extra_head="", preview=False, wide=False, pub=None):
    pub = pub or _home()
    look = pub_profile.look(pub["id"])
    # the publication's accent (pub_profile.ACCENTS, tokens.css) on its header and bands
    accent = "" if look["accent"] == "navy" else (
        ' style="--obs-band-bg: var(--obs-pub-%s); --obs-header-bg: var(--obs-pub-%s)"'
        % (look["accent"], look["accent"]))
    head = [
        "<!DOCTYPE html>",
        '<html lang="en"%s>' % accent,
        "<head>",
        '<meta charset="utf-8">',
        '<script src="/assets/lock.js"></script>',  # one click, one request: see prerender.LOCK_SCRIPT
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<base href="/">',
        "<title>%s</title>" % esc(title),
        '<meta name="description" content="%s">' % esc(description),
    ]
    if canonical:
        head.append('<link rel="canonical" href="%s">' % esc(canonical))
    if robots:
        head.append('<meta name="robots" content="%s">' % esc(robots))
    head += [
        '<meta property="og:site_name" content="%s">' % esc(pub["name"]),
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:description" content="%s">' % esc(description),
        '<link rel="alternate" type="application/rss+xml" title="%s" href="%s/feed.xml">'
        % (esc(pub["name"]), esc(pub["base"])),
        '<link rel="icon" href="/favicon.ico">',
        '<link rel="stylesheet" href="/assets/tokens.css">',
        '<link rel="stylesheet" href="/assets/app.css">',
        '<link rel="stylesheet" href="/assets/research.css">',
        extra_head,
    ]
    if canonical:
        head.append('<meta property="og:url" content="%s">' % esc(canonical))
    if ld:
        head.append(_json_script(ld, "rs-ld", "application/ld+json"))
    head.append("</head>")
    body = [
        '<body class="no-toc">' if wide else "<body>",
        '<header class="site-header" data-section="" data-page="research"></header>',
        '<script src="/assets/nav.js"></script>',
    ]
    if preview:
        body.append('<div class="rs-preview-bar" role="status">Preview of the saved draft '
                    '— not published. Charts read today’s data, as they would if '
                    'published now.</div>')
    body += ['<main class="container rs-wrap%s">' % (" rs-home" if wide else ""), main, "</main>"]
    if data is not None:
        body.append(_json_script(data, "rs-data"))
    body += ['<script src="/assets/format.js"></script>',
             '<script src="/assets/sortable.js"></script>',
             '<script src="/assets/echarts.min.js"></script>',
             '<script src="/assets/charts.js"></script>',
             '<script src="/assets/research.js"></script>',
             "</body>", "</html>", ""]
    return "\n".join(head + body)


def _html(text, status=200, cache="public, max-age=60"):
    headers = dict(SECURITY_HEADERS)
    headers["Cache-Control"] = cache
    return HTMLResponse(text, status_code=status, headers=headers)


# ---------------------------------------------------------------------------
# one article

def citation(authors, title, version, published_at, url, brand=BRAND):
    year = (published_at or "")[:4]
    who = _authors_text(authors) or brand
    return "%s (%s). %s. %s, version %d, %s. %s" % (
        who, year, title.rstrip("."), brand, version, _date_long(published_at), url)


def _logo(pub, cls):
    url = pub_profile.logo_url(pub["id"])
    return ('<img class="%s" src="%s" alt="" width="40" height="40">' % (cls, esc(url))) if url else ""


def _trail(pub):
    return '<nav class="rs-trail" aria-label="Breadcrumb"><a href="%s">%s%s</a></nav>' % (
        esc(pub["base"]), _logo(pub, "rs-trail-logo"), esc(pub["name"]))


def _own_views(pub):
    """Under every article of a publication other than PloverResearch."""
    if pub["home"]:
        return ""
    return ('<p class="rs-own-views">Published in %s on %s. The views are the authors\u2019 '
            'own, not Plover Analytics\u2019.</p>' % (esc(pub["name"]), BRAND))


_ICON = {
    "x": '<path d="M4 4l16 16M20 4L4 20" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    "in": '<rect x="3" y="3" width="18" height="18" rx="2.5" fill="none" stroke="currentColor" '
          'stroke-width="1.8"/><text x="12" y="16.6" text-anchor="middle" font-size="10.5" '
          'font-weight="700" font-family="Arial, Helvetica, sans-serif" fill="currentColor">in</text>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="1.5" fill="none" stroke="currentColor" '
            'stroke-width="1.8"/><path d="M3.5 6l8.5 7 8.5-7" fill="none" stroke="currentColor" stroke-width="1.8"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 '
            '5.7 5.7l1-1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    "print": '<path d="M7 9V4h10v5M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 '
             '1h-2M7 14h10v6H7z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
}


def _svg(name):
    return ('<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">%s'
            '</svg>' % _ICON[name])


def share_bar(url, title):
    """One quiet line of share links. Plain links — no third-party script, so
    nothing on the page reports a reader to anyone."""
    q = urllib.parse.quote
    links = [
        ("X", "https://twitter.com/intent/tweet?url=%s&text=%s" % (q(url, ""), q(title, ""))),
        ("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=%s" % q(url, "")),
        ("Email", "mailto:?subject=%s&body=%s" % (q(title, ""), q(url, ""))),
    ]
    out = ['<p class="rs-share" role="group" aria-label="Share"><span class="rs-share-h">Share</span>']
    for label, href in links:
        out.append('<a href="%s" rel="noopener" target="_blank">%s</a>' % (esc(href), label))
    out.append('<button type="button" class="linkish" id="rs-copy-link" data-url="%s">Copy link</button>'
               % esc(url))
    out.append('<button type="button" class="linkish" id="rs-print">Print</button>')
    out.append("</p>")
    return "".join(out)


def article_main(v, versions, current_version, notice="", preview=False, pub=None):
    """The <article> for one version. ``v`` is a version dict (or a preview
    built from the draft in the same shape)."""
    pub = pub or _home()
    url = seo.SITE_BASE_URL + pub["base"] + "/" + v["slug"]
    version_url = url + ("/v%d" % v["version"] if v["version"] != current_version else "")
    first = versions[0]["published_at"] if versions else v["published_at"]
    dateline = ['<time datetime="%s">%s</time>' % (esc(first[:10]), esc(_date_long(first)))]
    if v["version"] > 1:
        dateline.append("Version %d, updated %s" % (v["version"], esc(_date_long(v["published_at"]))))
    parts = [
        _trail(pub),
        notice,
        '<article class="rs-article">',
        '<header class="rs-head">',
    ]
    doc = v.get("doc") or {}
    kicker = [t for t in [MARKET_LABEL.get(doc.get("market"), "")] +
              [TOPIC_LABEL.get(t, "") for t in doc.get("topics") or []] if t]
    if kicker:
        parts.append('<p class="rs-kicker">%s</p>' % ' <span aria-hidden="true">\u00b7</span> '.join(
            esc(k) for k in kicker))
    parts.append("<h1>%s</h1>" % esc(v["title"]))
    if v["dek"]:
        parts.append('<p class="rs-dek">%s</p>' % esc(v["dek"]))
    parts.append('<div class="rs-byline">%s<p class="rs-dateline">%s</p></div>' % (
        ('<p class="rs-author">%s</p>' % esc(_authors_text(v["authors"]))) if v["authors"] else "",
        " \u00b7 ".join(dateline)))
    parts.append(share_bar(url, v["title"]))
    parts.append("</header>")
    parts.append('<div class="rs-body">%s</div>' % v["body_html"])
    if len(versions) > 1:
        parts.append('<section class="rs-versions" aria-label="Versions"><h2>Versions</h2><ol>')
        for item in versions:
            href = "%s/%s/v%d" % (pub["base"], v["slug"], item["version"])
            label = "Version %d" % item["version"]
            current = item["version"] == v["version"]
            parts.append(
                '<li%s><a href="%s">%s</a> <span class="rs-v-date">%s</span> %s</li>' % (
                    ' aria-current="true"' if current else "", esc(href), label,
                    esc(_date_long(item["published_at"])), esc(item["change_note"])))
        parts.append("</ol></section>")
    cite = citation(v["authors"], v["title"], v["version"], v["published_at"],
                    url + "/v%d" % v["version"], pub["name"])
    parts.append('<section class="rs-cite-box" aria-label="How to cite"><h2>How to Cite</h2>'
                 '<p class="rs-cite" id="rs-cite">%s</p>'
                 '<p class="rs-cite-acts"><button type="button" class="btn" id="rs-cite-copy">'
                 'Copy Citation</button> <a class="btn" href="%s/%s.md">Markdown</a></p>'
                 '<p class="rs-cite-note">Each version keeps its own address. The charts show '
                 'the data as it stood on the version’s publication date; the CSV under '
                 'each chart returns those same numbers.</p></section>'
                 % (esc(cite), esc(pub["base"]), esc(v["slug"])))
    parts.append(_own_views(pub))
    parts.append("</article>")
    del version_url
    return "\n".join(parts)


def _ld(v, versions, pub):
    first = versions[0]["published_at"] if versions else v["published_at"]
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": v["title"],
        "description": v["summary"],
        "datePublished": first,
        "dateModified": v["published_at"],
        "version": str(v["version"]),
        "author": [{"@type": "Person", "name": n} for n in v["authors"]],
        "publisher": {"@type": "Organization", "name": "Plover Analytics",
                      "url": seo.SITE_BASE_URL},
        "isPartOf": {"@type": "Periodical", "name": pub["name"]},
        "url": seo.SITE_BASE_URL + pub["base"] + "/" + v["slug"],
        "inLanguage": "en",
    }


def _chart_data(v):
    charts = {}
    for b in v["doc"]["blocks"]:
        if b["type"] == "chart":
            snap = v["snapshots"].get(b["id"])
            if snap and not snap.get("error"):
                charts[b["id"]] = {"block": b, "snap": snap}
        elif b["type"] == "snapshot" and b.get("cfg"):
            # copied from a Plover page: drawn by the same chart code, from
            # the config captured with it
            charts[b["id"]] = {"kind": b["kind"], "cfg": b["cfg"], "title": b["title"],
                               "url": b["url"], "source": rd.snapshot_source_line(b)}
        elif b["type"] == "datachart" and _figure_ok(b, {}):
            charts[b["id"]] = figure_payload(b, {})
    return {"charts": charts, "slug": v["slug"], "version": v["version"]}


def render_article(row, number=None, pub=None):
    """(html, status) for /research/<slug> or /research/<slug>/v<n>."""
    pub = pub or research.publication(row["publication_id"])
    current = row["published_version"]
    versions = research.versions_meta(row["id"])
    if row["status"] == "withdrawn":
        latest = research.version(row["id"], current)
        main = ('%s<article class="rs-article"><header class="rs-head"><h1>%s</h1></header>'
                '<div class="rs-notice rs-notice-warn" role="status"><strong>Withdrawn.</strong> '
                'This article was withdrawn on %s. %s</div></article>' % (
                    _trail(pub), esc(latest["title"]),
                    esc(_date_long(research._iso(row["withdrawn_at"]))),
                    esc(row["withdrawn_reason"] or "")))
        return page("%s · %s" % (latest["title"], pub["name"]), "This article was withdrawn.",
                    None, main, robots="noindex", pub=pub), 410
    number = number or current
    v = research.version(row["id"], number)
    if v is None:
        return None, 404
    notice = ""
    robots = None
    if number != current:
        robots = "noindex"
        notice = ('<div class="rs-notice" role="status">You are reading version %d of %d, '
                  'published %s. <a href="%s/%s">Read the current version</a>.</div>'
                  % (number, current, esc(_date_long(v["published_at"])), esc(pub["base"]),
                     esc(v["slug"])))
    main = article_main(v, versions, current, notice, pub=pub)
    more, more_figs = more_from(v["slug"], pub=pub)
    main += "\n" + more + "\n" + _subscribe_panel(pub=pub)
    data = _chart_data(v)
    data["figures"] = more_figs
    canonical = seo.SITE_BASE_URL + pub["base"] + "/" + v["slug"]
    return page("%s · %s" % (v["title"], pub["name"]), v["summary"], canonical, main,
                data=data, robots=robots, ld=_ld(v, versions, pub),
                extra_head='<meta property="og:type" content="article">', pub=pub), 200


def _row_or_404(slug, pub=None):
    pub = pub or _home()
    if not rd._SLUG.match(slug or ""):
        raise HTTPException(404, "No such article")
    row = research.by_slug(slug, pub["id"])
    if row is None or row["published_version"] is None:
        raise HTTPException(404, "No such article")
    return row


# ---------------------------------------------------------------------------
# routes — specific paths before /research/{slug}

# ---------------------------------------------------------------------------
# the homepage: lead story, Chart of the Week, latest notes, archive

SUBSTACK_URL = os.environ.get("SUBSTACK_URL", "https://substack.com/@ploverresearch")
LATEST_ROWS = 12
MARKET_LABEL = dict(rd.MARKETS)
TOPIC_LABEL = dict(rd.TOPICS)


def _figure_ok(b, snapshots):
    if b["type"] == "chart":
        snap = snapshots.get(b["id"])
        return bool(snap) and not snap.get("error")
    if b["type"] == "snapshot":
        return bool(b.get("cfg"))
    if b["type"] == "datachart":
        return any(v is not None for col in rd.data_table(b["rows"])["values"] for v in col)
    return bool(b.get("media"))


def figures(doc, snapshots):
    """Every drawable figure in an article, in order: (block, chart number)."""
    nums, _ = rd.chart_numbering(doc["blocks"])
    return [(b, nums[b["id"]]) for b in doc["blocks"]
            if b["type"] in rd.FIGURE_TYPES and _figure_ok(b, snapshots)]


def lead_figure(doc, snapshots):
    """The figure that stands for the article: the one the writer chose, else
    the first. (block, number) or (None, None)."""
    figs = figures(doc, snapshots)
    for b, n in figs:
        if b["id"] == doc.get("lead"):
            return b, n
    return figs[0] if figs else (None, None)


def figure_title(b):
    return b.get("title") or b.get("caption") or ""


def figure_payload(b, snapshots):
    """What research.js needs to draw a figure — or None for an image, which
    the server writes as an <img>."""
    if b["type"] == "chart":
        return {"block": b, "snap": snapshots[b["id"]]}
    if b["type"] == "snapshot":
        return {"kind": b["kind"], "cfg": b["cfg"], "title": b["title"], "url": b["url"],
                "source": rd.snapshot_source_line(b)}
    if b["type"] == "datachart":
        # the writer's own numbers, drawn by researchChart.drawData
        return {"data": True, "block": b, "title": b["title"],
                "source": rd.datachart_source_line(b)}
    return None


def figure_source(b, snapshots):
    if b["type"] == "chart":
        return rd.chart_source_line(snapshots[b["id"]])
    if b["type"] == "snapshot":
        return rd.snapshot_source_line(b)
    if b["type"] == "datachart":
        return rd.datachart_source_line(b)
    return ("Source: " + b["source"]) if b.get("source") else ""


def _plot(b, key, mode, data, alt):
    """A figure slot: an <img> for an image, else a div research.js draws into."""
    if b["type"] == "image":
        dims = (' width="%d" height="%d"' % (b["width"], b["height"])
                if b.get("width") and b.get("height") else "")
        return '<img class="rh-img rh-img-%s" src="/research/media/%s.%s" alt="%s"%s loading="lazy">' % (
            mode, b["media"], b["ext"], esc(b.get("alt") or figure_title(b)), dims)
    data["figures"][key] = figure_payload(b, data["_snaps"])
    return '<div class="rh-plot rh-plot-%s" data-fig="%s" role="img" aria-label="%s"></div>' % (
        mode, esc(key), esc("Chart: " + (alt or "")))


def _label(a, topics=1):
    """The category line on a card: market, then the first topic."""
    doc = a["doc"]
    out = [MARKET_LABEL.get(doc.get("market"), "")]
    out += [TOPIC_LABEL.get(t, "") for t in (doc.get("topics") or [])[:topics]]
    return " · ".join(t for t in out if t)


def _date_short(iso):
    if not iso:
        return ""
    d = datetime.date.fromisoformat(iso[:10])
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def _thumb(a, key, data, mode="card", brand=BRAND):
    b, _n = lead_figure(a["doc"], a["snapshots"])
    data["_snaps"] = a["snapshots"]
    if b is None:
        return '<span class="rh-noimg" aria-hidden="true">%s</span>' % esc(brand)
    return _plot(b, key, mode, data, figure_title(b))


def _card(a, key, data, pub=None):
    """One note as a box: its chart on top, then label, headline and date.
    Deliberately sparse — the standfirst and author are on the note itself."""
    pub = pub or _home()
    href = esc(pub["base"] + "/" + a["slug"])
    doc = a["doc"]
    return ('<article class="rh-card" data-market="%s">'
            '<a class="rh-card-fig" href="%s" tabindex="-1" aria-hidden="true">%s</a>'
            '<div class="rh-card-body"><p class="rh-label">%s</p>'
            '<h3 class="rh-card-title"><a href="%s">%s</a></h3>'
            '<p class="rh-date"><time datetime="%s">%s</time></p></div></article>' % (
                esc(doc.get("market") or ""), href, _thumb(a, key, data, brand=pub["name"]),
                esc(_label(a)),
                href, esc(a["title"]), esc(a["first_published_at"][:10]),
                esc(_date_short(a["first_published_at"]))))


def _chart_of_the_week(items, lead):
    """(article, block, number) for Chart of the Week: the newest note whose
    writer offered its chart, else the newest note other than the lead with a
    chart, else the lead's second chart. Never the chart already shown in
    the lead story."""
    lead_block = lead_figure(lead["doc"], lead["snapshots"])[0] if lead else None
    for a in items:
        if a["doc"].get("feature"):
            b, n = lead_figure(a["doc"], a["snapshots"])
            if b is not None and not (a is lead and b is lead_block):
                return a, b, n
    for a in items:
        if a is lead:
            continue
        b, n = lead_figure(a["doc"], a["snapshots"])
        if b is not None:
            return a, b, n
    if lead:
        rest = [(b, n) for b, n in figures(lead["doc"], lead["snapshots"]) if b is not lead_block]
        if rest:
            return lead, rest[0][0], rest[0][1]
    return None, None, None


def _subscribe_panel(title="Get New Notes by Email", pub=None):
    pub = pub or _home()
    if not pub["home"]:
        # the publication's own list (app/newsletter.py): the form works with
        # or without the script, which only keeps the reader on the page
        return ('<aside class="rh-subscribe" id="subscribe" aria-label="Subscribe">'
                '<div><p class="rh-subscribe-title">Get New Articles by Email</p>'
                '<p class="rh-subscribe-text">Each new article from %s, sent to your inbox. '
                'Unsubscribe any time.</p></div>'
                '<form class="rh-subform" method="post" action="%s/email/subscribe" '
                'data-subscribe novalidate>'
                '<label class="visually-hidden" for="rh-sub-email">Email address</label>'
                '<input type="email" id="rh-sub-email" name="email" required maxlength="254" '
                'autocomplete="email" placeholder="name@example.com">'
                '<button class="btn btn-primary" type="submit">Subscribe</button>'
                '<a class="btn" href="%s/feed.xml">RSS</a>'
                '<p class="rh-sub-msg" role="status" aria-live="polite" hidden></p>'
                '</form></aside>' % (esc(pub["name"]), esc(pub["base"]), esc(pub["base"])))
    return ('<aside class="rh-subscribe" aria-label="Subscribe">'
            '<div><p class="rh-subscribe-title">%s</p>'
            '<p class="rh-subscribe-text">Every PloverResearch note is sent to Substack subscribers '
            'the day it is published.</p></div>'
            '<div class="rh-subscribe-acts"><a class="btn btn-primary" href="%s" rel="noopener">'
            'Subscribe on Substack</a><a class="btn" href="/research/feed.xml">RSS Feed</a></div>'
            '</aside>' % (esc(title), esc(SUBSTACK_URL)))


def _section_head(title, extra="", note=""):
    """The platform's navy section band; a control (the market tabs) sits in
    the row under it, never inside the band."""
    head = '<h2>%s%s</h2>' % (esc(title), (' <span class="h2-note">%s</span>' % esc(note)) if note else "")
    return head + ('<div class="rh-tabbar">%s</div>' % extra if extra else "")


def render_home(pub=None):
    """A publication's front page: the platform's section bands, a boxed
    lead story that opens with its chart, Chart of the Week boxed beside it,
    then the other notes as a grid of boxed cards. Every area is a box;
    nothing is laid out loose on the page. Chart-first by design: the data is
    what distinguishes a Plover note."""
    pub = pub or _home()
    items = research.published(pub["id"])
    data = {"figures": {}, "_snaps": {}}
    if pub["home"]:
        acts = ('<a class="btn btn-primary" href="%s" rel="noopener">Subscribe</a>'
                '<a class="btn" href="/research/feed.xml">RSS</a>' % esc(SUBSTACK_URL))
    else:
        acts = ('<a class="btn btn-primary" href="%s#subscribe">Subscribe</a>'
                '<a class="btn" href="%s/feed.xml">RSS</a>' % (esc(pub["base"]), esc(pub["base"])))
    parts = ['<header class="rh-mast">%s<div><h1>%s</h1>%s%s</div>'
             '<div class="rh-mast-acts">%s</div></header>'
             % (_logo(pub, "rh-logo"), esc(pub["name"]),
                ('<p class="rh-strap">%s</p>' % esc(pub["tagline"])) if pub["tagline"] else "",
                ('<p class="rh-about">%s</p>' % esc(pub["about"])) if pub["about"] and not pub["home"]
                else "", acts)]
    if not items:
        parts.append(_section_head("Latest Notes"))
        parts.append('<div class="rh-box rh-empty">%s</div>' % (
            "No notes published yet. The first arrives here and on Substack together."
            if pub["home"] else "No notes published yet."))
        parts.append(_subscribe_panel(pub=pub))
        parts.append(_own_views(pub))
        data.pop("_snaps", None)
        return "\n".join(parts), data

    lead = items[0]
    rest = items[1:1 + LATEST_ROWS]
    markets = [k for k, _ in rd.MARKETS if any(a["doc"].get("market") == k for a in rest)]
    tabs = ""
    if len(rest) >= 3 and len(markets) > 1:
        tabs = ('<div class="rh-tabs" role="group" aria-label="Market">'
                '<button type="button" data-market="" aria-pressed="true">All</button>%s</div>' % "".join(
                    '<button type="button" data-market="%s" aria-pressed="false">%s</button>'
                    % (k, esc(MARKET_LABEL[k])) for k in markets))
    parts.append(_section_head("Latest Notes"))

    # the lead story: its chart leads, the words sit beside it, one box
    lb, ln = lead_figure(lead["doc"], lead["snapshots"])
    href = esc(pub["base"] + "/" + lead["slug"])
    data["_snaps"] = lead["snapshots"]
    fig = ""
    if lb is not None:
        fig = ('<a class="rh-hero-fig" href="%s" tabindex="-1" aria-hidden="true">%s'
               '<span class="rh-hero-cap">Chart %d · %s</span></a>' % (
                   href, _plot(lb, "lead", "lead", data, figure_title(lb)), ln,
                   esc(figure_title(lb))))
    hero = ('<article class="rh-hero%s">%s<div class="rh-hero-text">'
            '<p class="rh-label">%s</p>'
            '<h3 class="rh-hero-title"><a href="%s">%s</a></h3>%s'
            '<p class="rh-hero-meta">%s<time datetime="%s">%s</time></p>'
            '<p class="rh-hero-go"><a class="rh-read" href="%s">Read the note'
            '<span aria-hidden="true"> \u2192</span></a></p></div></article>' % (
                "" if fig else " rh-hero-solo", fig, esc(_label(lead, topics=2)), href,
                esc(lead["title"]),
                ('<p class="rh-hero-dek">%s</p>' % esc(lead["dek"] or lead["summary"]))
                if (lead["dek"] or lead["summary"]) else "",
                esc("By " + _authors_text(lead["authors"]) + " · ") if lead["authors"] else "",
                esc(lead["first_published_at"][:10]), esc(_date_long(lead["first_published_at"])),
                href))

    ca, cb, cn = _chart_of_the_week(items, lead)
    side = ""
    if cb is not None:
        data["_snaps"] = ca["snapshots"]
        side = ('<aside class="rh-cotw" aria-label="Chart of the Week">'
                '<p class="rh-label">Chart of the Week</p>%s'
                '<h3 class="rh-cotw-title"><a href="%s/%s">%s</a></h3>'
                '<p class="rh-cotw-from">Chart %d in %s</p></aside>' % (
                    _plot(cb, "cotw", "cotw", data, figure_title(cb)), esc(pub["base"]),
                    esc(ca["slug"]),
                    esc(figure_title(cb)), cn, esc(ca["title"])))
    parts.append('<div class="rh-top%s">%s%s</div>' % ("" if side else " rh-top-solo", hero, side))

    if rest:
        cards = "".join(_card(a, "card-%d" % i, data, pub) for i, a in enumerate(rest))
        parts.append(_section_head("More Notes", tabs,
                                   "%d %s" % (len(rest), "note" if len(rest) == 1 else "notes")))
        parts.append('<div class="rh-cards" id="rh-list">%s</div>'
                     '<div class="rh-box rh-empty" id="rh-none" hidden>No notes for this market yet. '
                     '<button type="button" class="linkish" id="rh-clear">Show all notes</button></div>'
                     % cards)

    archive = items[1 + LATEST_ROWS:]
    if archive:
        trs = "".join(
            '<tr><td class="num"><time datetime="%s">%s</time></td><td><a href="%s/%s">%s'
            '</a></td><td>%s</td><td>%s</td></tr>' % (
                esc(a["first_published_at"][:10]), esc(_date_long(a["first_published_at"])),
                esc(pub["base"]), esc(a["slug"]), esc(a["title"]),
                esc(MARKET_LABEL.get(a["doc"].get("market"), "")),
                esc(_authors_text(a["authors"]))) for a in archive)
        parts.append(_section_head("Archive"))
        parts.append('<div class="table-wrap rh-box"><table class="data rh-archive"><thead><tr>'
                     '<th scope="col" class="num">Published</th><th scope="col">Title</th>'
                     '<th scope="col">Market</th><th scope="col">Authors</th></tr></thead>'
                     '<tbody>%s</tbody></table></div>' % trs)

    parts.append(_subscribe_panel(pub=pub))
    parts.append(_own_views(pub))
    data.pop("_snaps", None)
    return "\n".join(parts), data


def more_from(current_slug, limit=3, pub=None):
    """(html, figures) for the boxed cards under an article."""
    pub = pub or _home()
    items = [a for a in research.published(pub["id"]) if a["slug"] != current_slug][:limit]
    if not items:
        return "", {}
    data = {"figures": {}, "_snaps": {}}
    cards = "".join(_card(a, "more-%d" % i, data, pub) for i, a in enumerate(items))
    data.pop("_snaps", None)
    return ('<section class="rh-more" aria-label="More from %s">'
            '<h2>More From %s</h2>'
            '<div class="rh-cards rh-cards-3">%s</div>'
            '<p class="rh-allrow"><a class="rh-read" href="%s">All notes'
            '<span aria-hidden="true"> \u2192</span></a></p></section>'
            % (esc(pub["name"]), esc(pub["name"]), cards, esc(pub["base"]))), data["figures"]


def _pub_or_404(pub_slug):
    pub = research.publication_by_slug(pub_slug)
    if pub is None or pub["home"]:
        raise HTTPException(404, "No such publication")
    return pub


def _index(pub):
    main, data = render_home(pub)
    if pub["home"]:
        title = BRAND + " · Plover Analytics"
        desc = ("Research notes from Plover Analytics on Japan and the United States, built "
                "on official statistics and company filings, with every chart frozen at "
                "publication.")
    else:
        title = "%s · %s" % (pub["name"], BRAND)
        desc = pub["tagline"] or pub["about"][:300] or ("Research notes published in %s on %s."
                                                         % (pub["name"], BRAND))
    return _html(page(title, desc, seo.SITE_BASE_URL + pub["base"], main, data=data, wide=True,
                      pub=pub))


def _feed(pub):
    items = research.published(pub["id"])[:50]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0"><channel>',
           "<title>%s</title>" % esc(pub["name"]),
           "<link>%s%s</link>" % (esc(seo.SITE_BASE_URL), esc(pub["base"])),
           "<description>%s</description>" % esc(
               "Research notes from Plover Analytics." if pub["home"]
               else (pub["tagline"] or "Research notes published in %s." % pub["name"])),
           "<language>en</language>"]
    logo = pub_profile.logo_url(pub["id"])
    if logo:
        out.append("<image><url>%s%s</url><title>%s</title><link>%s%s</link></image>" % (
            esc(seo.SITE_BASE_URL), esc(logo), esc(pub["name"]), esc(seo.SITE_BASE_URL), esc(pub["base"])))
    for a in items:
        url = "%s%s/%s" % (seo.SITE_BASE_URL, pub["base"], a["slug"])
        when = datetime.datetime.fromisoformat(a["first_published_at"].rstrip("Z"))
        out.append("<item><title>%s</title><link>%s</link><guid>%s</guid>"
                   "<pubDate>%s</pubDate><description>%s</description>%s</item>" % (
                       esc(a["title"]), esc(url), esc(url),
                       when.strftime("%a, %d %b %Y %H:%M:%S +0000"), esc(a["summary"]),
                       "".join("<author>%s</author>" % esc(n) for n in a["authors"][:1])))
    out.append("</channel></rss>")
    return Response("\n".join(out), media_type="application/rss+xml",
                    headers={"Cache-Control": "public, max-age=300"})


def _markdown(pub, slug):
    row = _row_or_404(slug, pub)
    if row["status"] == "withdrawn":
        raise HTTPException(410, "This article was withdrawn")
    v = research.version(row["id"], row["published_version"])
    versions = research.versions_meta(row["id"])
    url = seo.SITE_BASE_URL + pub["base"] + "/" + v["slug"]
    head = ["# " + v["title"], ""]
    if v["dek"]:
        head += ["*" + v["dek"] + "*", ""]
    meta = []
    if v["authors"]:
        meta.append("By " + _authors_text(v["authors"]))
    meta.append("Published " + _date_long(versions[0]["published_at"]))
    meta.append("Version %d" % v["version"])
    head += [" · ".join(meta) + ". " + url, ""]
    tail = ["", "---", "",
            "Cite as: " + citation(v["authors"], v["title"], v["version"], v["published_at"],
                                   url + "/v%d" % v["version"], pub["name"]), ""]
    if not pub["home"]:
        tail += ["Published in %s on %s. The views are the authors' own, not Plover "
                 "Analytics'." % (pub["name"], BRAND), ""]
    return PlainTextResponse("\n".join(head) + v["markdown"] + "\n".join(tail),
                             media_type="text/markdown; charset=utf-8",
                             headers={"Cache-Control": "public, max-age=60"})


def _version(pub, slug, number):
    row = _row_or_404(slug, pub)
    text, status = render_article(row, number, pub)
    if text is None:
        raise HTTPException(404, "No such version")
    return _html(text, status)


def _article(pub, slug):
    row = _row_or_404(slug, pub)
    text, status = render_article(row, pub=pub)
    return _html(text, status)


@router.get("/research")
@router.get("/research/")
def research_index():
    return _index(_home())


@router.get("/research/feed.xml")
def research_feed():
    return _feed(_home())


@router.get("/research/media/{name}")
def research_media(name: str):
    if "." not in name:
        raise HTTPException(404, "No such image")
    sha, ext = name.rsplit(".", 1)
    path = research.media_file(sha.lower(), ext.lower())
    if path is None:
        raise HTTPException(404, "No such image")
    return FileResponse(str(path), media_type=rd.IMAGE_EXTS[ext.lower()],
                        headers={"Cache-Control": "public, max-age=31536000, immutable",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'"})


@router.get("/research/{slug}.md")
def research_markdown(slug: str):
    return _markdown(_home(), slug)


@router.get("/research/{slug}/v{number}")
def research_version(slug: str, number: int):
    return _version(_home(), slug, number)


@router.get("/research/{slug}")
def research_article(slug: str):
    return _article(_home(), slug)


@router.get("/p/" + research.HOME_SLUG)
@router.get("/p/" + research.HOME_SLUG + "/")
def home_by_slug():
    return RedirectResponse("/research", status_code=301)


@router.get("/p/{pub_slug}")
@router.get("/p/{pub_slug}/")
def pub_index(pub_slug: str):
    return _index(_pub_or_404(pub_slug))


@router.get("/p/{pub_slug}/feed.xml")
def pub_feed(pub_slug: str):
    return _feed(_pub_or_404(pub_slug))


# ---------------------------------------------------------------------------
# a publication's email list: sign up, confirm, unsubscribe (app/newsletter.py)
#
# Under /p/<publication>/email/… so no address can shadow an article's: an
# article's own address is one segment, /p/<publication>/<slug>.

def _notice(pub, title, paragraphs, extra=""):
    main = (_trail(pub) + '<div class="rs-email"><h1>%s</h1>%s%s</div>' % (
        esc(title), "".join("<p>%s</p>" % p for p in paragraphs), extra))
    return _html(page(title + " · " + pub["name"], title, None, main, robots="noindex", pub=pub),
                 cache="no-store")


def _back(pub):
    return '<p><a class="btn" href="%s">Go to %s</a></p>' % (esc(pub["base"]), esc(pub["name"]))


SIGNUP_MESSAGES = {
    "confirm_sent": "Check your inbox: we sent a link to confirm your subscription.",
    "already": "Check your inbox: we sent a link to confirm your subscription.",
    "resend_later": "A confirmation link was sent a few minutes ago. Check your inbox and spam "
                    "folder, or try again in ten minutes.",
}


@router.post("/p/{pub_slug}/email/subscribe")
async def pub_subscribe(pub_slug: str, request: Request):
    """JSON from the page's script, or a plain form post without it. An
    address already subscribed gets the same reply as a new one, so the form
    cannot be used to find out who reads what."""
    pub = _pub_or_404(pub_slug)
    raw = (await request.body())[:2000]
    wants_json = "json" in request.headers.get("content-type", "")
    try:
        email = (json.loads(raw.decode("utf-8") or "{}").get("email", "") if wants_json else
                 urllib.parse.parse_qs(raw.decode("utf-8", "replace")).get("email", [""])[0])
    except (ValueError, AttributeError):
        email = ""
    from . import accounts
    try:
        status = newsletter.subscribe(pub, email, accounts._client_ip(request))
        msg, code = SIGNUP_MESSAGES[status], 200
    except newsletter.MailFailed as exc:
        msg, code = str(exc), 503
    except newsletter.NewsletterError as exc:
        msg, code = str(exc), 400
    except Exception:  # noqa: BLE001 — the mail provider refused or is down
        log.exception("newsletter sign-up failed")
        msg, code = ("The confirmation email could not be sent just now. Try again in a few "
                     "minutes."), 503
    if wants_json:
        return JSONResponse({"ok": code == 200, "message": msg}, status_code=code)
    return _notice(pub, "Almost Done" if code == 200 else "Not Subscribed", [esc(msg)],
                   _back(pub))


@router.get("/p/{pub_slug}/email/confirm")
def pub_confirm(pub_slug: str, t: str = ""):
    pub = _pub_or_404(pub_slug)
    email = newsletter.confirm(pub, t)
    if email is None:
        return _notice(pub, "Link No Longer Valid", [
            "This confirmation link has expired or was replaced by a newer one.",
            'Sign up again on <a href="%s#subscribe">%s</a> and use the newest email.'
            % (esc(pub["base"]), esc(pub["name"]))])
    return _notice(pub, "You Are Subscribed", [
        "New articles from %s will arrive at <strong>%s</strong>." % (esc(pub["name"]), esc(email)),
        "Every email has a link to unsubscribe."], _back(pub))


@router.get("/p/{pub_slug}/email/unsubscribe")
def pub_unsubscribe_page(pub_slug: str, t: str = ""):
    """A page with a button, not an unsubscribe on GET: mail scanners open
    every link in a message, and would unsubscribe readers who never asked."""
    pub = _pub_or_404(pub_slug)
    row = newsletter.subscriber_by_token(pub, t)
    if row is None:
        return _notice(pub, "Link Not Recognised", [
            "This unsubscribe link does not match an address on the list. It may have been "
            "removed already.", "Reply to any email from %s to ask the writer directly."
            % esc(pub["name"])], _back(pub))
    if row["status"] == "unsubscribed":
        return _notice(pub, "You Are Unsubscribed", [
            "<strong>%s</strong> receives no more email from %s." % (esc(row["email"]),
                                                                     esc(pub["name"]))],
            '<p><a class="btn" href="%s#subscribe">Subscribe Again</a></p>' % esc(pub["base"]))
    return _notice(pub, "Unsubscribe from %s" % pub["name"], [
        "Stop sending new articles to <strong>%s</strong>?" % esc(row["email"])],
        '<form method="post" action="%s/email/unsubscribe?t=%s">'
        '<button class="btn btn-primary" type="submit">Unsubscribe</button> '
        '<a class="btn" href="%s">Keep Subscription</a></form>'
        % (esc(pub["base"]), esc(urllib.parse.quote(t)), esc(pub["base"])))


@router.post("/p/{pub_slug}/email/unsubscribe")
def pub_unsubscribe(pub_slug: str, t: str = ""):
    """The page's button, and mail providers' one-click unsubscribe
    (List-Unsubscribe-Post, RFC 8058), which posts here with no cookie."""
    pub = _pub_or_404(pub_slug)
    email = newsletter.unsubscribe(pub, t)
    if email is None:
        return _notice(pub, "Link Not Recognised", [
            "This unsubscribe link does not match an address on the list."], _back(pub))
    return _notice(pub, "You Are Unsubscribed", [
        "<strong>%s</strong> receives no more email from %s." % (esc(email), esc(pub["name"]))],
        '<p><a class="btn" href="%s#subscribe">Subscribe Again</a></p>' % esc(pub["base"]))


@router.get("/p/{pub_slug}/{slug}.md")
def pub_markdown(pub_slug: str, slug: str):
    return _markdown(_pub_or_404(pub_slug), slug)


@router.get("/p/{pub_slug}/{slug}/v{number}")
def pub_version(pub_slug: str, slug: str, number: int):
    return _version(_pub_or_404(pub_slug), slug, number)


@router.get("/p/{pub_slug}/{slug}")
def pub_article(pub_slug: str, slug: str):
    return _article(_pub_or_404(pub_slug), slug)


@router.get("/sitemap-research.xml")
def sitemap_research():
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for pub in research.list_publications():
        items = research.published(pub["id"])
        if not items and not pub["home"]:
            continue
        newest = max(a["published_at"] for a in items) if items else None
        body.append("  <url><loc>%s%s</loc>%s</url>" % (
            esc(seo.SITE_BASE_URL), esc(pub["base"]),
            "<lastmod>%s</lastmod>" % esc(newest[:10]) if newest else ""))
        for a in items:
            body.append("  <url><loc>%s%s/%s</loc><lastmod>%s</lastmod></url>" % (
                esc(seo.SITE_BASE_URL), esc(pub["base"]), esc(a["slug"]),
                esc(a["published_at"][:10])))
    body.append("</urlset>")
    body.append("")
    return Response("\n".join(body), media_type="application/xml")


def llms_section():
    """Lines for /llms.txt: PloverResearch's published articles, with their
    Markdown. Other publications are their writers' work, not Plover's."""
    try:
        items = research.published()
    except Exception:  # noqa: BLE001 — llms.txt must render without research
        return []
    lines = ["## Research", "",
             "%s notes, each at /research/<slug> and as Markdown at /research/<slug>.md. "
             "Charts are frozen at publication; each links to the same numbers through "
             "the API with as_of." % BRAND, ""]
    if not items:
        lines += ["- %s/research (no articles yet)" % seo.SITE_BASE_URL, ""]
        return lines
    for a in items:
        url = "%s/research/%s" % (seo.SITE_BASE_URL, a["slug"])
        lines.append("- [%s](%s) (Markdown: %s.md). %s" % (a["title"], url, url,
                                                         seo._one_line(a["summary"])))
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# after publication

def announce(path):
    """Tell the search indexes about an article's path (and its publication's
    front page), in the background. Never raises. A bare slug is a
    PloverResearch article."""
    if not path.startswith("/"):
        path = "/research/" + path
    base = path.rsplit("/", 1)[0]

    def run():
        try:
            from . import indexnow
            indexnow.submit([seo.SITE_BASE_URL + path, seo.SITE_BASE_URL + base])
        except Exception:  # noqa: BLE001
            log.warning("indexnow ping for research failed", exc_info=True)
    threading.Thread(target=run, daemon=True).start()


def unescape(text):
    return html.unescape(text)
