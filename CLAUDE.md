# Plover Analytics — Project Rules

> Always-true, cross-cutting rules for this repo. The product lives in `observatory/`;
> plans live in `docs/plans/`.

---

## What this product is

- **Scope: all markets.** The product covers every market we can source, not Japan
  alone. Japan came first (price statistics, Bank of Japan, EDINET filings); the United
  States is second (Treasury curve, SEC company filings); more follow. Each market is a
  top-level tab in the site header (`NAV_MARKETS` in `web/assets/nav.js`). A new market
  meets the same bar as Japan before it ships: the trust contract, point-in-time
  vintages, and its own row in `/api/v1/catalog/health`. Hong Kong is verified and not
  started. (Changed 2026-09-24 from "Japan, deep — not Asia, wide".)
- **An institutional data product, not a consumer dashboard.** Ordered customers:
  sell-side economists → buy-side/quant data teams → academics (free; citations are the
  point) → discretionary PMs. Judgement calls favour the API, the data, and reproducibility
  over UI polish.
- **The moat is point-in-time vintages.** Everything we ingest is free and public; we sell
  reproducibility, depth, and *history*. Vintage history can only accumulate going
  forward, so the ingest running reliably every month **is** the asset.
- **Distribution is the *Asia Economics Observations* Substack**, not cold outreach. Every
  chart in a post must be a permanent, citable URL on the platform — that's what the
  "URL encodes full view state" design rule is for.

## Golden Rule

**The platform is dataset-agnostic; datasets are adapters.** A new dataset is exactly:
a new module in `observatory/app/adapters/` (exposing `DATASET`, `SOURCE`, `DOWNLOAD_URL`,
`fetch()`, `parse()`, `validate()`, `PRESENTATION`, `ValidationError`) plus registration in
the `ADAPTERS` dicts in `app/ingest.py` and `app/api.py`. It also needs a `MANIFEST`
(draft one with `python -m app.registry --scaffold <slug>`) whose `page` is a real file
under `web/`, and a non-empty `PRESENTATION["kinds"]`; run `python -m app.registry --check`
and `tests/test_manifests.py` before calling it done. **The core schema (`app/db.py`)
and the API contract (`/api/v1/{dataset}/...`) never change for a dataset.** If a new
dataset seems to need a core migration, stop and revisit the design before writing code.
This rule has been exercised (cpi-jp-items, dataset #2, ~740 series: adapter + registry
entries, zero core changes) — keep it that way.

## Trust Contract (P0 — never regress)

Every number on every surface says where it came from:

- **Index levels** → `Official Statistic` badge — exactly as published, never recomputed.
- **Calculated rates** (YoY, MoM, 3-month annualized, contributions, breadth) → **no badge**.
  They carry their **formula** instead: under "Show calculation" on the page and in every
  CSV export header. The API still tags them `trust: "derived"`; the front end renders the
  formula, not a label. `TRUST_LABELS` in `format.js` contains only `official` and `model`.
- **`Model Estimate` badge** — reserved for future nowcasts; not used today.
- **Missing is `—`, never zero.** Gaps stay gaps in charts; a missing value is never
  exported as 0.
- Index levels are exact, but a rate computed from published (rounded) indices can differ
  from the Bureau's published rate by ±0.1 pp — the Methodology page discloses this; don't
  "fix" it by adjusting values.

## Definitions

- **cpi-jp** — national middle-class indices (~80 category series; headline, cores, ten
  major groups). e-Stat statInfId `000032103842`.
- **cpi-jp-items** — national detailed item indices (~740 series). e-Stat statInfId
  `000032103844`. **Leaf item** = code NOT starting with `0` (582 individually priced
  items, weights sum to ~10,000); codes starting `0` are aggregates.
- **Release / vintage** — one accepted ingest of a source file. One live vintage; every
  raw file archived under `data/raw/` with its SHA-256. **A stored vintage is immutable** —
  see Ingest Guardrails.
- **Measure type** — price *change* (index), price *level* (yen), *stock* (holdings),
  *flow* (net purchases). Never rank, aggregate, or chart across types. BOJ data is levels
  in ¥100mn and flows that **go negative**; `weight_per_10000` is meaningless for it.
- **BOJ Time-Series Data Search API** — `https://www.stat-search.boj.or.jp/api/v1/`
  (`getDataCode` · `getMetadata` · `getDataLayer`), JSON/CSV, **no key**. Two mandatory
  obligations: email the Research and Statistics Department on release, and display the
  BOJ credit line. Key DBs: `BS01` accounts · `MD09` stock+flow · `MD01` monetary base ·
  `FF` flow of funds · `PR01`–`PR04` price statistics.
- **Weights** — stored as parts per 10,000 (１万分比), not percent. Mis-scaling this is a
  recurring bug class.
- **Ready-to-adapt sibling tables** (same long-run CSV layout, same parser):
  `000032103845` goods/services splits · `000032103846` seasonally adjusted ·
  `000032103843` 1946– all-items-less-imputed-rent.

## Where to look

| Working on…                          | Read                                                                 |
| ------------------------------------ | -------------------------------------------------------------------- |
| Product overview, run/ingest/API     | [`observatory/README.md`](observatory/README.md)                     |
| Any UI change (layout, chart, style) | the **`ui-ux-design` skill** — mandatory, not optional               |
| Formulas, trust labels, limitations  | [`observatory/web/methodology.html`](observatory/web/methodology.html) — keep it in sync with any calculation change |
| Product strategy / scope / roadmap   | [`docs/plans/PLAN-JAPAN-MACRO-OBSERVATORY.md`](docs/plans/PLAN-JAPAN-MACRO-OBSERVATORY.md) — tiers and customers still current; its **Japan-only scope is superseded** by "Scope: all markets" above |
| Trust contract, risk register (v1)   | [`docs/plans/PLAN-JAPAN-INFLATION-OBSERVATORY.md`](docs/plans/PLAN-JAPAN-INFLATION-OBSERVATORY.md) — still valid there, out of date on tiers/scope |
| Cross-shareholding DB (product #2)   | [`docs/plans/PLAN-CROSS-SHAREHOLDING-DB.md`](docs/plans/PLAN-CROSS-SHAREHOLDING-DB.md) — own schema namespace; macro golden rule does **not** apply to it |
| Boards & pay (product surface #3)     | [`docs/plans/PLAN-BOARD-AND-PAY.md`](docs/plans/PLAN-BOARD-AND-PAY.md) |
| Architecture / milestones            | [`docs/plans/IMPL-JAPAN-INFLATION-OBSERVATORY.md`](docs/plans/IMPL-JAPAN-INFLATION-OBSERVATORY.md) |
| e-Stat CSV parsing                   | [`observatory/app/adapters/estat_csv.py`](observatory/app/adapters/estat_csv.py) |
| Ask (LLM Q&A) behaviour and tools    | [`observatory/app/agent.py`](observatory/app/agent.py) module docstring |

## General Rules

- **The app must be VERY EASY TO USE (P0).** A first-time user gets every common task done
  without instructions or help. So: every action is visible where people look for it
  (delete, undo and close are buttons on the thing, not hidden in a menu); the common case
  is one click; nothing waits without saying what it is doing; every failure says what to
  do next; and nothing half-made is left behind. If a task needs explaining, the design is
  wrong. (2026-10-03: deleting a chart in the desk needed the ⋮⋮ handle and a menu; Copy a
  Chart from a page with no chart waited 25 seconds and left an empty block in the draft.)
- Don't assume I'm correct — do a thorough check. If my request is ambiguous, restate your
  interpretation in one sentence before making changes.
- I'm a businessman, not an engineer — explain things in plain language.
- Never try to please me for the sake of it. Give your professional answer.
- **Answer the question I asked. No unsolicited opinions.** If I ask you to find,
  read, check or summarise something, report what you found and stop. Don't append a
  verdict on whether it's worth it, how it fits the strategy, what I should do next,
  or what the "real" lesson is. I'll ask for a view when I want one — and when I do,
  give it straight (that rule stands, this one only governs when it applies).
- Do not hallucinate. If you cite a number, it must come from the data; if you name a link
  or e-Stat table, verify it exists.
- **Commit or push only when I ask.** No branches, commits, or pushes on your own
  initiative.
- **Other sessions work in this checkout at the same time.** Commit only your own files,
  with `git commit -m "…" -- <paths>`, after checking `git diff --cached --name-only`. Never
  `git add -A`, `commit -a`, `stash`, `checkout --` or `clean` files you didn't change.
  (September 2026: two commits carried another session's half-written files.)
- **Build for every user, not just me.** The tools people work in, the Research app first,
  will let each user set their own preferences. Don't hard-code my workflow, my outlets or
  my defaults into shared code. A choice another person might make differently becomes a
  per-user setting with a sensible default. When I ask for something that suits my way of
  working, build it as my setting and say so. (2026-10-08: the Research app's publish flow
  assumes every writer posts to Substack.)
- **Every feature assumes Substack-style accounts: each person has their own.** A person is
  one account, found by their email. Whatever they connect, store or choose belongs to that
  account: API keys, data sources, AI provider, Google, preferences. It follows them to
  every place they sign in (Writer Desk, editor, Investment Assistant). Only their own runs
  use it, and nobody else on the team or publication can see it. A publication is its own
  workspace, like a portfolio: its members, skills, house style, article defaults, look,
  coverage list, AI key and data keys belong to it, its owner runs them, and its articles
  use them before a writer's own. Never build a connection as admin-only, team-wide or for
  one page only; the shared admin password is nobody and gets nothing personal.
  (2026-10-11: data sources were built for the Investment Assistant desk only, so writers
  on a publication could not use them.)
- Before concluding there is a bug: check the server is running the current code (uvicorn
  has no auto-reload here — restart it from `observatory/`), the right port (8007), and
  that you're not looking at a cached response.
- Before declaring a feature done: hit the real endpoints and load the real pages, not a
  mock. Data must round-trip: ingest → API → rendered page.
- **A production fill is done only when the live API shows it, after all other running jobs
  have finished.** Before a manual fill, check nothing else is updating that database.
  (2026-10-29: a price fill was reported done, then another job's older copy erased it.)
- Numbers shown to users must reconcile: contributions sum to headline (residual disclosed
  and bundled, never hidden); exports match what the chart shows.

### Response Format

**Write short. Write simple. This is a P0 rule, not a preference.**

I am often *learning* the topic from your answer. Dense writing costs me more time than
it saves you. Before sending, re-read it and cut.

- **Answer in the first five lines.** Bottom line first, detail after.
- **Under 200 words** for a normal answer. Over 400 needs a reason. Tables and code do
  not buy extra room.
- **Short sentences, one idea each.** Two em-dashes or three commas in a sentence means
  split it.
- **No jargon unless you explain it in the same sentence.** "Malapportionment (some
  people's votes count for more than others')". If a plain word exists, use the plain
  word: say "the best possible way to split the seats", not "the constrained optimum".
- **Every number needs a "so what" beside it.** Not "3.13×" but "3.13× — one vote in
  Fukui counts as much as three in Tokyo".
- **One table per answer, maximum**, unless I ask for more.
- **Cut any sentence that only proves you did the work.** I assume you did it.
- After finishing a task: **What changed** (max 4 plain-English bullets) · **What to
  expect** (max 2 lines: which page/URL, what I'll see) · **What to be aware of** (only
  genuinely useful flags; "Nothing to flag." if none). When the product changed, add
  one line **Tried as a user:** what you typed and clicked, and what happened (the done
  gate requires it). No other sections.

---

## Dev Environment

Single process, no Node build, no Docker needed locally:

```bash
cd observatory
./.venv/bin/python -m app.ingest cpi-jp        # idempotent; archives + validates
./.venv/bin/python -m app.ingest cpi-jp-items
./.venv/bin/uvicorn app.main:app --port 8007
```

- **Local Python is 3.9** — no `match`, no `X | Y` union syntax, no 3.10+ features in
  `app/` code, even though the production container runs 3.12. Code must run on both.
- DuckDB is **pinned `>=1.4,<1.6`** — `INSERT OR REPLACE` semantics around foreign keys
  changed after 1.5 and an unpinned install made production diverge from local. Don't
  loosen the pin.
- **Don't stop another session's server or take its database lock.** DuckDB lets one
  program hold a file at a time. If a database is locked, copy it to your scratchpad, work
  on the copy, then swap it in (check the original's modified time hasn't changed). Run
  your own server on a spare port, not 8007. Responses are cached by the macro database's
  version, so after swapping `equity.duckdb` add `?_=fresh` to a URL before deciding the
  swap failed.
- **Never open `observatory/seed/equity.duckdb` directly**, even read-only: DuckDB
  rewrites the file and git shows the tracked seed as modified. Copy it first.
- `.env` (gitignored, from `.env.example`) holds the ask-provider key only. Everything
  except the ask box works without it.
- Frontend is vanilla JS + vendored ECharts (`web/assets/echarts.min.js`). No new runtime
  dependencies, no CDN references, no build step.

## Checks that run every time

Mechanical, not memory. Added 2026-10-03 after the desk search found nothing for
"CPI" and ten clicks on Create Key made ten keys. Fix the code, never the check.

| Check | What it catches | When |
| --- | --- | --- |
| `.claude/hooks/check-syntax.py` | a Python, JS, page-script or JSON file that no longer parses | after every file Claude edits |
| `.claude/hooks/done-gate.py` | guards, the tests naming a changed module, the browser checks for a changed page, and a missing **Tried as a user:** line | every time Claude ends a turn that changed code |
| `web/assets/lock.js` (added to every page by the server) | double clicks: a control whose click sends a change is off until the server answers; the same change in flight is never sent twice | in the browser, always |
| `ci/guards.py` | a page without the lock, a write that bypasses it, a search box missing from `ci/search_boxes.json` | GitHub on every push, the pre-push hook, the done gate |
| `ci/search_examples.py` | a search box whose own examples (or obvious queries) find nothing; ten clicks sending more than one request | the pre-push hook, the done gate for changed pages |

- **Use it like a user before calling it done.** After the endpoint and screenshot checks,
  try the first things a real person would do: the obvious input, the example shown on
  screen, a double-click, a slow reply, an empty result, an error. Report what you tried.
- A new search box goes into `ci/search_boxes.json` with the selector its results appear
  in. The guard will not let it ship otherwise.
- The gate's one exit for a failure that is not yours (another session's file): say so to
  me in the reply, on a line starting `GATE: not mine`, naming the file.

## Deploy

- **Production runs on a DigitalOcean server behind Cloudflare (since 2026-10-10).** A push
  to `main` deploys itself once the GitHub checks pass (`.github/workflows/deploy.yml` →
  `observatory/deploy/deploy.sh`): the new copy is smoke-checked before readers reach it,
  and a failure rolls back with no outage. About 5 minutes from push to live (measured
  4 min 43 s: checks 1 min 17 s, deploy 3 min 24 s). Railway is only a quiet fallback
  until about 2026-10-24. Manual deploy or rollback:
  `ssh root@206.189.209.173 /opt/plover/repo/observatory/deploy/deploy.sh main` (or
  `image:sha-<old>`).
- The image is built from `observatory/Dockerfile`; `observatory/railway.json` pins the
  Railway fallback (healthcheck `/api/v1/catalog/datasets`).
- `start.sh` runs the seven macro ingests, then the equity refresh
  (`observatory/equity/refresh_equity.py` — all seven EDINET-derived datasets, incremental
  from the S3 archive), then uvicorn. Both are fail-safe by design: an unreachable source,
  a failed validation or a failing extractor publishes nothing and the last good data stays
  live — a boot with no upstream still serves data. **Never make ingest or extraction
  failure fatal to boot.**
- `data/` is a mounted volume in production — the DuckDB file and raw archive must survive
  redeploys. Never write anything that matters outside `data/`.

## Ask feature (LLM Q&A)

- **Off, and staying off.** `ASK_ENABLED` is an explicit kill switch — the box stays
  hidden unless it is set truthy, even when a provider key is present. For an
  institutional product this is a settled decision, not a "not yet": professional users
  won't trust an LLM over the numbers, and it is a support and credibility liability.
  Don't propose enabling it without a specific customer asking.
- The agent has **no direct DB access** — it reaches data only through tools wrapping the
  same functions that serve `/api/v1`, and every tool call is recorded and shown to the
  reader. Don't add a tool that bypasses the public API's numbers.
- Providers: DeepSeek (`DEEPSEEK_API_KEY`, checked first) or OpenAI (`OPENAI_API_KEY`);
  same OpenAI-compatible client for both. Bump `SYSTEM_VERSION` in `agent.py` whenever the
  system prompt changes.

## Design Rules

All UI rules live in the **`ui-ux-design` skill**. P0 rules repeated for visibility:

- Colors from `tokens.css` `--obs-*` tokens only (6 series slots); both light and dark
  must work. Tabular numerals for all numbers; true minus sign; `pp` vs `%` used
  correctly (a change *of* a percentage is not a percentage *of* a level).
- Every chart: source line, Download PNG (light-theme, source embedded) and Download CSV
  (metadata header block); URL encodes the full view state so any view is citable.
- **Mandatory look-at-it gate:** never call UI done without screenshots at 1440 (light +
  dark) and true-390. Headless Chrome floors layout at ~500px even in `--headless=new` —
  true-390 checks need a CDP script (`Emulation.setDeviceMetricsOverride`, browser cache
  disabled) or the iframe harness; `--virtual-time-budget` does not wait for fetches
  inside iframes.
- **Never kill a browser by application name (P0).** `pkill -f "Google Chrome"` (or
  `killall`, or any pattern matching the app bundle path) terminates my real browser
  window and every other agent's browser on this machine — unsaved tabs and all. Kill
  only the process you launched: match on `--headless=new`, on a `--user-data-dir` you
  chose, or on the PID you started. This rule covers Chrome, Chromium, Safari, Edge and
  the Playwright MCP browsers. The same applies to any long-lived app I might be using;
  scope every `pkill` to something only your own process matches.
- **Buttons that create, send or delete switch off until the server answers.**
  (2026-10-03: a slow reply turned 10 clicks on Create Key into 10 live keys.)
- **Test a search with what people will actually type** before calling it done: the
  box's own example, plus short names like CPI, GDP or a stock code, against real data.
  (2026-10-03: the desk search found nothing for "CPI".)

## Ingest Guardrails (P0)

1. Ingest is **idempotent and fail-safe**: unchanged file → skip; failed validation →
   publish nothing, previous release stays live. Every downloaded file archived with
   SHA-256 before parsing.
2. **Vintages are immutable — this is the commercial moat, not housekeeping.** A stored
   release is never updated in place, never back-filled, never "corrected". A revision is
   a *new* vintage. Code that could overwrite a stored release is a P0 defect even if
   every displayed number looks right.
3. Validation gates are the product's credibility — never weaken one to make an ingest
   pass. Investigate the data instead.
4. e-Stat CSVs are cp932-encoded; blank cells are missing, never zero.
5. One DuckDB writer at a time: ingest runs before the API starts (see `start.sh`); never
   add a write path to the serving process.
6. **Every dataset reports its own freshness.** A dataset absent from
   `/api/v1/catalog/health` goes stale silently: the 5% filings stopped on 2026-08-06 and
   nothing noticed for four weeks, because the health report only knew about the macro
   adapters while every page still rendered a healthy-looking dashboard over month-old
   data. A dataset is not done until its staleness shows up there.
7. **A shipped seed never overwrites fresher data on the volume.** `data/equity.duckdb` is
   topped up nightly inside the container; the image's `seed/equity.duckdb` is installed
   only when its recorded watermark (`eq_extract_runs`) is *ahead* of the volume's. Copying
   a seed over a live volume unconditionally is a P0 defect — it silently discards every
   night of extraction since the image was built.

---

## Continuous Improvement

Treat this file as living rules. If you spot a repeat mistake, missing guardrail, or
better convention, propose an update — exact text, why, where — and wait for approval.
Do not update rules automatically. `AGENTS.md` is the Codex copy of these rules: any
approved change here goes into `AGENTS.md` in the same edit.
