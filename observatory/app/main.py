"""Observatory app: JSON API + static frontend in one process.

Run:  ./.venv/bin/uvicorn app.main:app --port 8007
"""
import asyncio
import contextlib
import hashlib
import os
import pathlib
import threading
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware

from . import cache, db, env, prerender, visits

# Before .api, which resolves the /ask provider from the environment.
env.load()

from . import api  # noqa: E402 — must follow env.load()
from .admin_api import router as admin_router  # noqa: E402
from . import research_backup  # noqa: E402
from .research_api import router as research_admin_router  # noqa: E402
from .research_pages import router as research_router  # noqa: E402
from .research_mcp import router as research_mcp_router  # noqa: E402
from .research_ai import router as research_ai_router  # noqa: E402
from .ai_setup_api import router as ai_setup_router  # noqa: E402
from .research_clips import router as research_clips_router  # noqa: E402
from .writer_api import router as writer_router  # noqa: E402
from . import accounts  # noqa: E402
from . import assistant  # noqa: E402
from .assistant import api as assistant_api  # noqa: E402
from .assistant import desk_mcp as assistant_desk_mcp  # noqa: E402
from .api import router  # noqa: E402
from .buyback_api import router as buyback_router  # noqa: E402
from .cohorts_api import router as cohorts_router  # noqa: E402
from . import equity_api  # noqa: E402
from .equity_api import router as equity_router  # noqa: E402
from .facility_api import router as facility_router  # noqa: E402
from .bank_api import router as bank_router  # noqa: E402
from .financials_api import router as financials_router  # noqa: E402
from .governance_api import router as governance_router  # noqa: E402
from .lvh_api import router as lvh_router  # noqa: E402
from .short_api import router as short_router  # noqa: E402
from .earnings_api import router as earnings_router  # noqa: E402
from .calendar_api import router as calendar_router  # noqa: E402
from .agm_api import router as agm_router  # noqa: E402
from .segments_api import router as segments_router  # noqa: E402
from .risk_api import router as risk_router  # noqa: E402
from . import sec_api  # noqa: E402
from . import us_companies  # noqa: E402
from . import jp_companies  # noqa: E402
from .sec_api import router as sec_router  # noqa: E402
from .sec_deep_api import router as sec_deep_router  # noqa: E402
from .sec_insiders_api import router as sec_insiders_router  # noqa: E402
from .sec_13f_api import router as sec_13f_router  # noqa: E402
from .sec_13dg_api import router as sec_13dg_router  # noqa: E402
from .sec_calendar_api import router as sec_calendar_router  # noqa: E402
from .us_prices_api import router as us_prices_router  # noqa: E402
from .us_events_api import router as us_events_router  # noqa: E402
from .us_ask_api import router as us_ask_router  # noqa: E402
from .us_press_api import router as us_press_router  # noqa: E402
from .us_pay_api import router as us_pay_router  # noqa: E402
from .us_company_api import router as us_company_router, logo_router as us_logo_router  # noqa: E402
from .us_company_admin_api import router as us_company_admin_router  # noqa: E402
from .jp_company_admin_api import router as jp_company_admin_router, logo_router as jp_logo_router  # noqa: E402
from .sec_text_api import router as sec_text_router  # noqa: E402
from .catalog_api import router as catalog_router  # noqa: E402
from .nav_preview import router as nav_preview_router  # noqa: E402
from .apportionment_api import router as representation_router  # noqa: E402
from .company_api import router as company_router  # noqa: E402
from . import registry  # noqa: E402
from . import tools_v2  # noqa: E402
from .ownership_api import router as ownership_router  # noqa: E402
from . import refresh  # noqa: E402
from .mcp import router as mcp_router  # noqa: E402
from . import seo  # noqa: E402
from .seo import router as seo_router  # noqa: E402
from .answers import router as answers_router  # noqa: E402
from .visits_api import router as visits_router  # noqa: E402

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

# prerender reads pages and nav.js from the same directory this serves.
prerender.WEB_DIR = WEB_DIR


class RevalidatedStatic(StaticFiles):
    """Static files that browsers must re-check on every visit.

    Without a Cache-Control header, browsers keep assets for a heuristic
    lifetime of their own choosing — after a deploy, a returning visitor can
    run a new page against a stale shared script (the buyback chart crashed
    exactly this way). ``no-cache`` means "keep a copy, but ask before using
    it": the ETag/Last-Modified pair FileResponse already sends makes that
    check a cheap 304, so unchanged files still never re-download.

    An HTML page is additionally rewritten on the way out, so that a
    crawler which does not run JavaScript still receives the site's links
    and the page's headline figures. See app/prerender.py for what is added
    and why; the ETag is recomputed over the rewritten body, since the
    file's own timestamp no longer describes what was sent.
    """

    async def get_response(self, path, scope):
        # A company's Price and Insiders and holders tabs were the front page
        # with ?t= until 2026-10-28; links already shared land on their own
        # page, permanently, without the front page showing first.
        if path in (".", "", "index.html"):
            query = scope.get("query_string", b"").decode("latin-1")
            if "t" in parse_qs(query):
                return RedirectResponse("/us-stock.html?" + query, status_code=301)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return self._prerendered(response, scope)

    @staticmethod
    def _prerendered(response, scope):
        source_path = getattr(response, "path", None)
        if response.status_code != 200 or not source_path:
            return response
        if not str(source_path).endswith(".html"):
            return response
        try:
            with open(str(source_path), encoding="utf-8") as handle:
                original = handle.read()
        except OSError:
            return response
        query_string = scope.get("query_string", b"").decode("latin-1")
        body = prerender.inject(
            original, os.path.basename(str(source_path)),
            canonical=seo.canonical_url(scope.get("path", "/"), query_string),
            query_string=query_string)
        if body == original:
            return response

        etag = '"%s"' % hashlib.md5(body.encode("utf-8")).hexdigest()
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        sent = Headers(scope=scope).get("if-none-match", "")
        if etag in [tag.strip() for tag in sent.split(",") if tag.strip()]:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="text/html; charset=utf-8",
                        headers=headers)


@contextlib.asynccontextmanager
async def lifespan(app):
    # Prime the cache before the port takes traffic, so no real visitor pays
    # the cost of building the large payloads from cold.
    # Every manifest's endpoints must resolve on this app. A card that
    # names a path that does not exist is quarantined and reported on
    # /catalog/health — never a reason for the port not to open.
    registry.bind(app)
    # The Data menu's previews too: every page header fetches them on first open.
    paths = api.warm_paths() + ["/api/v1/catalog/previews"]
    # WARM_SERIES_LATER=1 (the DigitalOcean server) builds the full series
    # lists just after the port opens instead of before: they are 46 of the
    # 64 s this takes (measured 2026-10-11), and there a deploy waits for it
    # while the old copy goes on serving. Railway still builds everything first.
    later = []
    if os.environ.get("WARM_SERIES_LATER", "").strip().lower() in ("1", "true", "yes", "on"):
        later = [p for p in paths if p.endswith("/series")]
        paths = [p for p in paths if not p.endswith("/series")]
    await cache.warm(app, paths)
    # WARM_PAGES=1 also builds every page once before the port answers (the
    # DigitalOcean server, where the previous copy serves meanwhile). The
    # 2026-10-10 trial: a fresh copy took 1.3-8 s over the home and company
    # pages its first readers asked for. Off on Railway, where boot time is
    # downtime.
    if os.environ.get("WARM_PAGES", "").strip().lower() in ("1", "true", "yes", "on"):
        await cache.warm(app, ["/"] + sorted("/" + p.name for p in WEB_DIR.glob("*.html")))
    # The series search reads every dataset's series list; load them once in
    # the background, after the port is open, so boot is not held up.
    threading.Thread(target=tools_v2.warm_search, name="warm-search", daemon=True).start()
    # The US stock box's ratios, price repairs and name indexes, rebuilt whenever
    # the SEC database is swapped (app/stock_recipes.warm_forever).
    from . import stock_recipes
    threading.Thread(target=stock_recipes.warm_forever, name="warm-us-stocks", daemon=True).start()
    # Watches ingest health for as long as we serve, and — only under
    # start.sh, which sets REFRESH_SUPERVISED — ends the process once a day so
    # the supervisor can re-run the ingests. See refresh.py.
    task = asyncio.ensure_future(refresh.run())
    # Off-volume copies of the research desk and staff accounts: the only
    # data on the volume that cannot be downloaded again. See research_backup.
    # WEB_RUNS_BACKUPS=0 where app/refresher.py runs them instead (the
    # DigitalOcean server), so two copies of the site overlapping during a
    # deploy never back up twice.
    tasks = [task]
    # What readers asked for under the copy before this one (cache.keep_warm
    # saves it): their pages first, then the deferred series lists. Without
    # it only the macro answers above were ready, and the first reader of
    # every other page paid its full build (the CEO pay list: 4-5 s).
    known = set(paths) | set(later)
    later = [k for k in cache.remembered(WARM_KEYS) if k.rstrip("?") not in known] + later
    if later:
        tasks.append(asyncio.ensure_future(_warm_when_released(app, later)))
    # Rebuilds the same list whenever a data swap or a company edit empties the cache.
    tasks.append(asyncio.ensure_future(cache.keep_warm(app, WARM_KEYS)))
    if os.environ.get("WEB_RUNS_BACKUPS", "1").strip().lower() not in ("0", "false", "no", "off"):
        tasks.append(asyncio.ensure_future(research_backup.loop()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        cache.remember(WARM_KEYS)


async def _warm_when_released(app, paths):
    """Build the deferred series lists once deploy.sh has released this copy
    (READY_GATE), or straight away without the gate. Built while deploy.sh is
    still checking the copy, they took the processor its checks needed: the
    smoke check stretched to ~50 s (2026-10-11)."""
    if os.environ.get("READY_GATE", "").strip().lower() in ("1", "true", "yes", "on"):
        for _ in range(1800):
            if RELEASE_FLAG.exists():
                break
            await asyncio.sleep(1)
    # one list at a time with a pause between, so readers arriving at a copy
    # just released are not queued behind it (2026-10-11 trial: a cold company
    # page took 8.6 s while all the lists were being built at once)
    for path in paths:
        await cache.warm(app, [path])
        await asyncio.sleep(0.5)


# The human reference is /api.html, rendered from this schema and the dataset
# manifests so it cannot drift from what the server serves; /api/docs keeps
# its old address and lands there. The framework's try-it-out console stays
# at /api/swagger for anyone who wants to poke an endpoint by hand.
OPENAPI_TAGS = [
    {"name": "Catalog", "description": "What is on the server: every dataset's card, the sections they sit in, and whether each one is current."},
    {"name": "Datasets", "description": "The same endpoints for every statistical dataset. `{dataset}` is a dataset id from the catalog; which endpoints a dataset offers is listed on its card."},
    {"name": "Company", "description": "One securities code across every dataset on the platform."},
    {"name": "Cross-shareholdings", "description": "Policy shareholdings (政策保有株式) as filed in each company's annual securities report."},
    {"name": "Shareholder register", "description": "The ten largest shareholders as filed in each annual report."},
    {"name": "5% filings", "description": "Large shareholding reports (大量保有報告書): who crossed 5% and when."},
    {"name": "Boards and pay", "description": "Directors, independence and officer pay as filed."},
    {"name": "AGM votes", "description": "Voting results from extraordinary reports (臨時報告書): director approval and proposal outcomes."},
    {"name": "Buybacks", "description": "Share repurchase programmes and monthly progress reports."},
    {"name": "Financials", "description": "Financial statements and the filer's five-year summary as tagged on EDINET, and ratios calculated from them."},
    {"name": "Segments and customers", "description": "Reportable segments, geographic revenue and named customers from each annual report, mapped to customs flows."},
    {"name": "Facilities", "description": "Major facilities and land as filed: sites, book value and rented floor space."},
    {"name": "Peer groups", "description": "Fixed and ad-hoc company cohorts, and one metric ranked across a cohort."},
    {"name": "Representation", "description": "Seats per elector in the House of Councillors, from the population register."},
]

app = FastAPI(title="Plover Analytics API", version="1",
              description="Japanese official statistics and company disclosures, read-only, "
                          "no key. Human reference: /api.html",
              docs_url="/api/swagger", redoc_url=None, openapi_url="/api/openapi.json",
              openapi_tags=OPENAPI_TAGS, lifespan=lifespan)


# Health for the DigitalOcean server (deploy/). Two questions, two answers:
#   /healthz        is this copy running? (the container healthcheck)
#   /healthz/ready  may Caddy send it readers? (the proxy's probe)
# With READY_GATE=1 a new copy starts out of rotation: deploy.sh smoke-checks
# it from inside first and only then creates RELEASE_FLAG. Before the gate,
# the 2026-10-10 trial showed readers 42 errors from a broken release in the
# half-minute before the smoke check rolled it back. deploy.sh creates
# DRAIN_FLAG in a copy it is about to stop, so Caddy sends it nothing before
# it does (without that: requests waiting up to 12 s at each stop). Neither is
# a visit (visits._kind) or cached (cache.py stores only /api/ JSON).
DRAIN_FLAG = pathlib.Path(os.environ.get("DRAIN_FLAG", "/tmp/plover-draining"))
RELEASE_FLAG = pathlib.Path(os.environ.get("RELEASE_FLAG", "/tmp/plover-released"))
# The answers readers asked for most recently, saved for the next copy of the site.
WARM_KEYS = db.DATA_DIR / "warm-keys.json"


@app.get("/healthz", include_in_schema=False)
def healthz():
    # names the container, so deploy.sh can see through Caddy which copy
    # readers are reaching before it stops the other one
    return Response("ok %s\n" % os.environ.get("HOSTNAME", ""), media_type="text/plain")


@app.get("/healthz/ready", include_in_schema=False)
def healthz_ready():
    held = (os.environ.get("READY_GATE", "").strip().lower() in ("1", "true", "yes", "on")
            and not RELEASE_FLAG.exists())
    if DRAIN_FLAG.exists() or held:
        return Response("draining\n" if DRAIN_FLAG.exists() else "not released yet\n",
                        status_code=503, media_type="text/plain")
    return Response("ok\n", media_type="text/plain")


@app.get("/api/docs", include_in_schema=False)
def api_docs():
    return RedirectResponse("/api.html", status_code=302)


# Order matters, and add_middleware builds the stack inside out: the last one
# added is the outermost. GZip must be outside the cache so that everything
# else — the charting bundle above all — is compressed too, while the cache's
# own pre-compressed hits pass through it untouched.
# Stamped with EVERY database file: any of them can be swapped in by app/backfill.py
# while the server runs, and an entry must not outlive the file it came from.
# The admin console's company edits (app/us_companies.py, app/jp_companies.py) are
# served at once too, not after the next data swap, but an edit clears only the
# answers that read that company (app/edit_reads.py): in this stamp, one new logo
# emptied the cache and the rebuild held the server for seven minutes (2026-10-29).
app.add_middleware(cache.ResponseCache,
                   version=lambda: (db.file_version(), equity_api.file_version(),
                                    sec_api.file_version()),
                   edits=(us_companies, jp_companies))
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
# Outermost, so a reader served from the response cache is still counted: a
# cache hit never reaches the routers. See app/visits.py.
app.add_middleware(visits.VisitCounter)
# Outside even the counter: a reader arriving on the www spelling is bounced
# to the canonical host before anything else runs, so the visit is counted
# once, at the address it lands on. See app/seo.py.
app.add_middleware(seo.CanonicalHost)

# Equity first: its literal /api/v1/equity/ paths must win over the core
# router's /api/v1/{dataset}/ catch-alls. Governance and buyback ahead of
# holdings, so their longer /equity/… prefixes are matched before the shorter one.
# Cohorts before everything else under /equity: its literal
# /api/v1/equity/cohorts/... paths must beat /equity/company/{sec_code}.
app.include_router(cohorts_router)
app.include_router(governance_router)
app.include_router(ownership_router)
app.include_router(lvh_router)
app.include_router(short_router)
app.include_router(earnings_router)
app.include_router(calendar_router)
app.include_router(agm_router)
app.include_router(facility_router)
app.include_router(bank_router)
app.include_router(financials_router)
app.include_router(segments_router)
app.include_router(risk_router)
app.include_router(buyback_router)
app.include_router(equity_router)
# The US shelf's literal /api/v1/us/financials/… paths, ahead of the core
# router for the same reason as the equity ones.
app.include_router(sec_router)
app.include_router(sec_deep_router)
app.include_router(sec_insiders_router)
app.include_router(sec_13f_router)
app.include_router(sec_13dg_router)
app.include_router(sec_calendar_router)
app.include_router(us_prices_router)
app.include_router(us_events_router)
app.include_router(us_ask_router)
app.include_router(us_press_router)
app.include_router(us_pay_router)
app.include_router(us_company_router)
app.include_router(us_logo_router)
app.include_router(jp_logo_router)
app.include_router(sec_text_router)
# The catalog of manifests ahead of the core router, so its literal
# /api/v1/catalog/… paths can never be shadowed by /{dataset}/… catch-alls.
# /api/v1/company/{code} ahead of the core router, so it beats the
# /api/v1/{dataset}/... catch-alls. /api/v1/representation/… is a derived
# surface over the population datasets and sits ahead of them for the same
# reason.
app.include_router(company_router)
# /api/v1/visit/… is the page talking to the counter about itself, and sits
# ahead of the core router for the same reason: /{dataset}/… would swallow it.
app.include_router(visits_router)
app.include_router(catalog_router)
# What the header's Data menu shows for each page (cached like /api/v1).
app.include_router(nav_preview_router)
app.include_router(representation_router)
app.include_router(router)
# /mcp sits outside /api/v1 on purpose: the response cache only touches GETs
# under that prefix, so JSON-RPC POSTs can never be served stale.
app.include_router(mcp_router)
# /admin/api also sits outside /api/v1: authenticated responses must never be
# served from (or into) the shared response cache.
app.include_router(admin_router)
app.include_router(us_company_admin_router)
app.include_router(jp_company_admin_router)
# The research desk (a writer's role in each publication: app/writers.py) and
# the publications' public pages (/research, /p/<publication>). The public routes are server-rendered HTML outside /api/v1, so a
# newly published article is never held back by the dataset response cache;
# they must sit ahead of the static mount, which answers every other path.
app.include_router(research_admin_router)
app.include_router(writer_router)
app.include_router(research_router)
# /mcp/research: the drafting desk for a writer's own Claude Code or Codex,
# behind a personal key. Outside /api/v1, so never cached.
app.include_router(research_mcp_router)
app.include_router(research_ai_router)
app.include_router(ai_setup_router)
# Pages a writer sends from their own browser (Send to Plover), to read what
# they subscribe to. Per person, under /admin/api like the rest of the desk.
app.include_router(research_clips_router)
# Accounts only exist where ACCOUNTS_ENABLED says so. Absent, every
# /api/v1/account/… path 404s, and the sign-in page reads that as "not switched
# on" rather than showing a form that cannot work. Its responses are per-reader
# and are excluded from the response cache by prefix (app/cache.py).
if accounts.enabled():
    app.include_router(accounts.router)
# The Investment Assistant needs both a signed-in person and its own switch;
# app/assistant/__init__.py checks both. Per-desk responses, cache-excluded
# by prefix like the account routes.
if assistant.enabled():
    app.include_router(assistant_api.router)
    # /mcp/desk: the same desk for the reader's own agent (Claude Code, Codex),
    # behind a personal key made in Settings. Outside /api/v1, so never cached.
    app.include_router(assistant_desk_mcp.router)
# robots.txt and sitemap.xml are generated from the pages in web/, so they
# have to be registered ahead of the static mount that serves those pages —
# the mount answers every remaining path and would 404 both.
app.include_router(seo_router)
app.include_router(answers_router)


# The ask box became the home page on 2026-10-19. Answers already shared as
# us-stocks.html?q=... or ?t=... land on the same answer at /, permanently.
@app.get("/us-stocks.html", include_in_schema=False)
def us_stocks_moved(request: Request):
    query = request.url.query
    return RedirectResponse("/" + ("?" + query if query else ""), status_code=301)


app.mount("/", RevalidatedStatic(directory=str(WEB_DIR), html=True), name="web")
