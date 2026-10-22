# Plover Analytics — product app

Working product for [Plover Analytics](../README.md) ([plan](../docs/plans/PLAN-JAPAN-INFLATION-OBSERVATORY.md),
[implementation plan](../docs/plans/IMPL-JAPAN-INFLATION-OBSERVATORY.md)). Japan CPI came first —
two datasets, both monthly, January 1970 to the latest published month, ingested directly from e-Stat:

- **cpi-jp** — national middle-class indices (~80 category series: headline, cores, ten major
  expenditure groups, and the categories beneath them)
- **cpi-jp-items** — national detailed item indices (~740 series down to individual goods and
  services: rice, electricity, mobile phone charges, ...)

- **cpi-jp-goods-services** — the same national index cut into goods and services (41 series:
  agricultural, industrial, utilities, publications; public and general services; durables),
  with the six-group contribution to headline. e-Stat statInfId `000040482946`.
- **cpi-jp-sa** — the Bureau's seasonally adjusted headline, cores, goods and services, from
  January 2010, published without weights (so no contributions). statInfId `000040482947`.
- **cpi-jp-long** — all items less imputed rent, monthly from August 1946, one series on one
  linked base. statInfId `000040482944`.
- **cpi-tokyo** / **cpi-tokyo-items** — the Tokyo ward-area middle-class and item tables
  (statInfIds `000040482965` / `000040482967`). The newest month is the mid-month advance,
  published about three weeks before the national figure; `tokyo.html` reads it beside `cpi-jp`.
- **All CPI datasets are on the 2025 base** since the Bureau rebased on 28 August 2026. The
  2020-base files are frozen at July 2026 and their values remain as the earlier vintages.

plus two monetary datasets:

- **boj-assets** — Bank of Japan balance-sheet stocks and flows (JGB holdings, purchases,
  redemptions, monetary base; monthly, ¥100mn, from the Bank's time-series API, table MD09)
- **jgb-yields** — the JGB yield curve: Ministry of Finance daily constant-maturity yields,
  15 tenors (1–40Y), every business day since September 1974

three banking datasets:

- **fsa-npl** — bad loans by bank group: the FSA's half-yearly disclosure of claims under the
  Financial Reconstruction Act (total credit, the three tiers of disclosed claims, the bad-loan
  ratio, disposal losses, real net business profit) for thirteen lender groups from city banks
  to credit co-operatives, every half-year from March 1999. Stitched from table 1 of every
  release, newest release winning where they overlap.
- **fsa-bank-results** — the FSA's aggregate results summary for the major banks (from March
  2008) and the regional banks (from March 2005): the income statement, loans, bad loans and
  capital ratios, in ¥100mn and ¥tn as published. Income-statement lines are `.fy` (full year,
  March) and `.h1` (first half, September) series.
- **jba-banks** — every JBA member bank's balance sheet and income statement, non-consolidated
  and consolidated, plus the association's aggregates by bank type, every half-year from March
  2002 (~44,000 series, ¥mn; editions before fiscal 2001 are Excel 5/95 files and are archived
  unread). Series are `{s|c}.{金融機関コード}.{reference code}`; the
  Reference Code sheet of any edition is the key. ~72MB of workbooks per ingest; about 8 minutes on a laptop, most of it the download — lift it out of the boot path with `INGEST_DATASETS` if the healthcheck window is tight.

and six trade datasets, all from the same Ministry of Finance table and served by one page script:

- **boj-loan-rates** — the BOJ's average contract interest rates on loans by lender (city, regional,
  regional II, shinkin): new loans made in the month from 1993 and the outstanding book from 1976,
  % per year, monthly.
- **boj-deposit-rates** — the BOJ's average rates posted on ordinary and time deposits by term and
  size, monthly from April 2022.
- **fsa-regional-fi** — the FSA's annual list of every regional bank, shinkin bank and credit
  co-operative (~500) with deposits, loans, capital and bad-loan ratios, branches and SME lending;
  one point in time per edition, and the source of each institution's disclosure-page URL.
- **bank-balance** (equity, `equity/bank_extract.py` + `equity/irrbb_collect.py`) — per bank: securities
  and loans by remaining term, deposits by term, unrealised gains and losses by type and the bank's
  own VaR from the securities report; and each bank's Basel III IRRBB1 table (ΔEVE, ΔNII, Tier 1)
  read from its own Pillar 3 disclosure PDF (needs `pdftotext`). Served at
  `/api/v1/equity/banks/…` and on the Banks page.
- **trade-semis** — Japan's semiconductor trade by partner country: monthly customs value
  (¥1,000) and quantity for integrated circuits, discrete semiconductors, thermionic tubes,
  the published component group, and semiconductor manufacturing equipment, in **both
  directions**, from January 2001 (2,544 series, 225 partners). From the principal-commodity
  by country tables (概況品別国別表) of the Ministry of Finance's Trade Statistics of Japan,
  via the e-Stat API. **Needs `ESTAT_APP_ID`.** Three things to know before using it:
  export and import commodity codes are separate vocabularies whose codes do not correspond
  (`70311000` is semiconductors on the import side, audio equipment on the export side); a
  published group always exceeds the sum of the items carried beneath it; and the Ministry
  publishes months it has not yet compiled as `0`, which the adapter drops rather than
  storing as fabricated zeros. Uniquely among the sources here the data is **natively
  vintaged** — every month passes through 速報 → 確報 → 確々報 → 確定 — so the point-in-time
  history is the source's own revision cycle rather than an artefact of when we fetched.
  Year-blocks the Ministry has closed are cached on the data volume under its own
  `UPDATED_DATE`, so a routine run re-downloads only the current year.

- **trade-autos** — the same tables read for the vehicle lines: motor vehicles (the published
  group), passenger cars, buses and trucks, vehicle parts and motorcycles, both directions, from
  January 2001 (3,208 series, 232 partners). Same machinery and series-code shape as `trade-semis`,
  so `/trade` and the shared page serve it unchanged. The code trap again: `70503000` is motor
  vehicles on the export side and *parts* on the import side. **Needs `ESTAT_APP_ID`.**

- **trade-energy** — the fuel lines, led by imports: the mineral-fuel total, crude oil, LNG,
  coal, refined products and LPG by supplier, and refined products (gasoline, kerosene and jet
  fuel, gas oil) by destination, from January 2001 (1,330 series, 199 partners). `30301000` is
  crude on the import side and refined products on the export side; the mineral-fuel total has
  no quantity and therefore no unit value. **Needs `ESTAT_APP_ID`.**

- **trade-machinery** — general machinery: the published group plus machine tools, construction
  and mining machinery, internal-combustion engines, pumps and compressors, and bearings on the
  export side; the group plus machine tools, construction machinery, computers, power-generating
  machinery and air conditioners on the import side. From January 2001. **Needs `ESTAT_APP_ID`.**

- **trade-pharma** — medical products (医薬品): the published group in both directions plus the
  vitamin, antibiotic and hormone lines the Ministry breaks out beneath it. Led by imports. From
  January 2001. **Needs `ESTAT_APP_ID`.**

- **trade-food** — food and live animals: the section total plus beef, pork, fish and shellfish,
  wheat and maize on the import side; the total plus meat, fish and shellfish, rice and other
  food preparations on the export side. Led by imports. From January 2001. **Needs
  `ESTAT_APP_ID`.**

- **trade-inputs** — the step upstream of `trade-semis`: silicon wafers (HS 3818.00) by partner
  country, monthly from January 2001, both directions, value (¥1,000) and quantity (kg), from the
  HS-detail commodity by country tables (品別国別表). Same adapter machinery and series-code shape
  as `trade-semis`, so `/trade` serves it unchanged. Two traps it carries so you don't have to:
  e-Stat publishes **no names** for HS lines (they are curated here from the tariff schedule), and
  the export and import schedules split 3818.00 differently (`-100`/`-900` vs `-010`/`-020`).
  **Needs `ESTAT_APP_ID`.**

and two demand datasets:

- **jnto-visitors** — monthly foreign visitor arrivals to Japan by market (54 series: the
  national total, six regional totals and named markets), from January 2003, published by
  the Japan National Tourism Organization

- **accommodation-jp** — monthly guest nights and room occupancy from the Japan Tourism
  Agency's Accommodation Survey (宿泊旅行統計調査), ~3,900 series. Guest nights for the
  nation, the 47 prefectures and the 10 transport-bureau regions run from January 2011,
  split into Japanese and foreign guests, with room occupancy by hotel type over the same
  span; from January 2019 also foreign guest nights by 24 visitor nationalities, guest
  nights by hotel type, and the leisure/business and in-prefecture/out-of-prefecture
  splits; from January 2026 also property-size bands and 210 named municipalities. Where
  arrivals count people, this counts **person-nights** — a distinct unit that must never be
  ranked or summed against arrivals. Three traps the adapter carries so you do not have to:
  every download link is a rotating content id, so one release is a deterministic zip of
  fourteen discovered workbooks (trend, annual definitive and monthly preliminary) under one
  checksum; the January 2026 survey re-stratified properties from employee count to room
  count, which is why property-size series start there and the residual "Other" nationality
  is two separate codes; and the nationality tables cover larger properties only, so their
  categories do not sum to the all-properties foreign guest nights.

and one demographic dataset:

- **population-jp** — population by prefecture from the Basic Resident Register (総務省), as
  of 1 January each year: population, households and the year's register flows (births,
  deaths, in- and out-migration, naturalisations), plus population by five-year age band and
  sex — each of those split three ways, all residents / Japanese residents / foreign
  residents, for all 47 prefectures and the national total (12,720 series). Administrative
  counts, not survey estimates. Stocks are dated 1 January of the reference year; flows are
  dated to the calendar year they cover, so population(Y+1) − population(Y) equals the flows
  dated Y. **The ministry keeps only the current year online** — last year's workbook is
  deleted — so the vintage archive here is the history.
- **population-jp-municipal** — the same register release at municipality level: every city,
  town, village and ward in Japan, about **1,900 of them**, three resident segments deep, with
  the year's register flows and five-year age bands (584,781 series). The workbook mixes four
  levels of area in one column — adding every municipality row gives 160 million people in a
  country of 124 million — because designated cities are published alongside their own wards,
  districts alongside their towns, and Tokyo's islands as one `島しょ` row. The 328 grouping
  rows are identified by name containment plus the workbook's outline structure and **proved**
  by reconciliation: the 1,898 municipalities sum to their prefecture exactly, in all three
  segments. Grouping rows are stored but carry `level: "group"` and must never be added to the
  rest. Small cells are suppressed by the ministry (17,829 of them, foreign age bands only) and
  are missing, never zero. Because 584,781 series is not a payload,
  `/api/v1/population-jp-municipal/prefectures` **requires** `?prefecture=NN`.
- **gdp-jp** — the Cabinet Office's quarterly GDP estimates on the expenditure side: real
  (chained 2020 prices), nominal and the deflator, seasonally adjusted at annual rates in
  ¥ billion, quarterly from 1994 Q1, from the three e-Stat tables the Cabinet Office
  overwrites at every release (`0003109750`, `0003109785`, `0003109787`). Levels only —
  growth rates and contributions are calculated on the platform and carry their formula.
  Because the table ids are stable and rewritten each release, every revision is captured
  as a vintage from the first ingest on. **Needs `ESTAT_APP_ID`.**

  Its real-time history reaches back to 2002: `python -m app.gdp_vintages load` backfills
  the 134 first and second preliminary estimates the Cabinet Office published between
  August 2002 and March 2019 from its archived e-Stat tables, dated from the Cabinet
  Office's own release calendar and release pages (the two agreed on all 24 releases where
  both could be checked). Those releases are written **only** to `observation_vintages`,
  with `status='archived'` and `releases.published_at` set — `observations` is never
  touched, so the live answer is always the newest release. Nothing is recorded between
  March 2019, where e-Stat's archive ends, and September 2026, where our ingests begin.
- **corporate-finance-jp** — the Ministry of Finance's quarterly corporate survey
  (法人企業統計調査): sales, profits, capital investment, cash, borrowings, equity holdings,
  headcount and the balance-sheet totals for companies with capital of ¥10 million or more,
  by 31 industry aggregates and four capital classes, quarterly from 1954, in ¥ million as
  published and not seasonally adjusted. A pinned subset of e-Stat table `0003060191`
  (~580,000 of its 23 million values). **Needs `ESTAT_APP_ID`.**
- **population-jp-history** — the long run behind it: population, age structure, foreign
  residents, households, births, deaths, migration and the total fertility rate for the same
  47 prefectures back to **1975**, from the System of Social and Demographic Statistics
  (社会・人口統計体系, table A) via the e-Stat API. The fertility rate is the one published
  *rate* the platform stores rather than calculates — its denominator (women by single year of
  age) is in no table held here, so it cannot be rebuilt from the counts; it is served exactly
  as published, and must never be summed across prefectures. Reference dates differ by indicator and are pinned per series —
  register counts at 1 January of the following year, census/estimates at 1 October, births
  and migration as calendar-year flows — because the API carries no reference date of its
  own. The two datasets join exactly: registered residents were 124,330,690 at 1 Jan 2025
  here, which is the 123,767,642 at 1 Jan 2026 in `population-jp` plus that year's decline.
  **Needs `ESTAT_APP_ID`** (free, https://www.e-stat.go.jp/mypage/view/api); every other
  dataset works without a key.

### Hand-edited reference lists

Two curated Python modules, both applied at serve time so an edit costs a redeploy and never a
re-extraction or a rewritten vintage:

- [`app/filer_labels.py`](app/filer_labels.py) — **investors.** What kind of institution a
  5%-filer is (read from its own filed 事業内容) and which family it belongs to (curated:
  BlackRock files under sixteen EDINET codes and no document names the parent).
- [`app/company_labels.py`](app/company_labels.py) — **companies.** English names, the
  alternative spellings filers actually use for the same buyer, corporate families, and theme
  tags (`memory`, `semicap`, `wafer`, `materials`, …). Toyota is written two ways and Honda
  three; no filing says they are the same company. The name as filed is never replaced, nothing
  is translated, and a subsidiary is never rolled into its parent.
  `python -m app.company_labels --check` reports aliases that no longer appear in any filing
  and the most-named companies still missing from the list.

## Run it

```bash
cd observatory
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# fetch, archive, validate, and publish the latest official data
./.venv/bin/python -m app.ingest cpi-jp
./.venv/bin/python -m app.ingest cpi-jp-items
./.venv/bin/python -m app.ingest cpi-jp-goods-services
./.venv/bin/python -m app.ingest cpi-jp-sa
./.venv/bin/python -m app.ingest cpi-jp-long
./.venv/bin/python -m app.ingest cpi-tokyo                # the mid-month advance, ~3 weeks ahead
./.venv/bin/python -m app.ingest cpi-tokyo-items
./.venv/bin/python -m app.ingest boj-assets
./.venv/bin/python -m app.ingest jgb-yields
./.venv/bin/python -m app.ingest jnto-visitors
./.venv/bin/python -m app.ingest accommodation-jp        # ~22MB of workbooks, ~4 min
./.venv/bin/python -m app.ingest population-jp
./.venv/bin/python -m app.ingest population-jp-history   # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest population-jp-municipal # ~12 min: 585k series
./.venv/bin/python -m app.ingest trade-semis             # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-inputs            # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-autos             # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-energy            # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-machinery         # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-pharma            # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest trade-food              # needs ESTAT_APP_ID
./.venv/bin/python -m app.ingest fsa-npl                 # 40 small FSA workbooks
./.venv/bin/python -m app.ingest fsa-bank-results
./.venv/bin/python -m app.ingest jba-banks               # ~72MB of JBA workbooks, ~8 min
./.venv/bin/python -m app.ingest boj-loan-rates
./.venv/bin/python -m app.ingest boj-deposit-rates
./.venv/bin/python -m app.ingest fsa-regional-fi
(cd equity && ../.venv/bin/python bank_extract.py --source api --all)      # EDINET_API_KEY; or --source s3
(cd equity && ../.venv/bin/python irrbb_collect.py --types regional-1,regional-2)   # bank websites, pdftotext

# segment notes (revenue by region, named customers, reportable segments) from the
# annual reports already archived — the company side of the Company Lens
cd equity && ../.venv/bin/python seg_extract.py --all --source local --workers 8 && cd ..

# serve API + frontend on one port
./.venv/bin/uvicorn app.main:app --port 8007
```

Then open <http://localhost:8007>. Pages: **Overview** (headline/core/core-core tiles, main
chart with measure + range controls, major-group breakdown, provenance), **Item Explorer**
(Categories / Detailed items toggle, EN/JA search, sortable, sparklines, per-item detail with
full history), and **Methodology** (how to use, trust labels, formulas, limitations). **Population** (prefecture map on six measures, highest/lowest rankings, one prefecture's fifty-year history, and all 47 in a sortable table).

Re-run the ingest command any time; it is idempotent. A file identical to the last published
one is skipped; a file that fails validation publishes nothing and leaves the previous release
live. Every downloaded file is archived under `data/raw/` with its SHA-256.

## Admin console

`/admin.html` — internal operations, unlisted from the public nav. Six views: **Ingest
Health** (per-dataset currency, quiet-ingest detection, artifact fingerprints), **Vintage
Browser** (every stored release and exactly what it introduced, revised, or withdrew),
**Traffic** (below), **Curation Queue** and **Party Profiles** (below), and **Audit Log**
(every sign-in and admin action).

- Enabled only when `ADMIN_PASSWORD` is set (environment or `.env`); without it every
  `/admin/api` endpoint answers 503 and the page says so. Sessions are HttpOnly cookies
  signed with a per-boot secret — a restart signs everyone out.
- The admin surface is read-only against DuckDB (the one-writer rule holds). Everything it
  writes is a file under `data/admin/`: the append-only audit trail `audit.jsonl`, and the
  party registry `parties.json`.
- Routes live under `/admin/api` (`app/admin_api.py`), outside `/api/v1`, so authenticated
  responses never touch the shared response cache.

### Traffic (`#traffic`)

Readership counted by the server itself (`app/visits.py`), because script-tag analytics are
blocked on the bank and fund networks this product is aimed at. Events go to append-only
JSONL under `data/analytics/`; no DuckDB write path, and no counting failure can reach a
response.

- **Two identities.** By default a visitor is a salted one-way hash of address and user
  agent that changes daily, so no address is stored and nobody can be followed across days.
  A reader who accepts the cookie banner is given a random id in `pa_vid` instead (a year,
  HttpOnly) and is keyed on a hash of that — which is what makes returning readers
  answerable. `pa_consent` stores the answer either way, so a reader who declines is not
  asked again. Both cookies are issued only by `POST /api/v1/visit/consent`, never as a
  side effect of serving a page; `navigator.globalPrivacyControl` suppresses the question.
- **Reading now** is held in memory for five minutes and never written down. Open pages
  send a keep-alive to `POST /api/v1/visit/ping` once a minute so a reader sitting on one
  page does not vanish from it.
- **Time on page** is reported by the page when it is hidden or closed, counting visible
  seconds only, capped at an hour, deduplicated per page view. Neither this nor the
  keep-alive stores anything on the reader's machine, so neither is behind the banner.
- **People** is the strict figure, and the one to quote. Anything that does not call
  itself a bot is a *visit*; a *person* is a visit that also reported a reading time (only
  the page's script can send one, so a browser rendered it), from an address placed on a
  network, where that network is not a cloud, host or CDN (`HOSTING_MARKERS` in
  `app/visits.py`; such rows are marked Data Centre in the network table). Deliberately an
  undercount — iCloud Private Relay readers leave through Cloudflare or Akamai and are
  excluded. The People column on the arrivals table is what says whether a source's clicks
  were real. `BOT_MARKERS` also names Google's non-"bot" fetchers (`Google-InspectionTool`,
  `GoogleOther`, `Google-Read-Aloud`), which were the largest "readership" for a month.
- **AI assistants** are counted as automated traffic, never as readers, but are reported
  in their own section with what each was doing: `training` (no person at the other end),
  `search` (one may arrive later) or `asked` (a person was asking that assistant about
  this page at that moment). `AI_AGENTS` in `app/visits.py` names them; every entry must
  also appear in `BOT_MARKERS`, and a test asserts the two agree. Separately,
  `ai_referrals` counts page reads whose referrer was an assistant — a reader who
  followed a citation out of an answer, which is the only figure here that is a person.
- The banner itself is `web/assets/consent.js`, injected into every page by
  `app/prerender.py` (like the icons) so a new page cannot ship without it. What readers
  are told is on `/methodology.html#cookies`.

### What machines are told (`app/seo.py`)

`robots.txt`, `sitemap.xml` and **`llms.txt`** are generated from the registry and the
contents of `web/`, never stored as files, so none can drift from what the site serves.
`llms.txt` follows the llmstxt.org convention: what the site is, what `trust: "official"`
versus `trust: "derived"` means, that a missing value is never zero, how to cite a page,
and how to read the data as of a past date. It exists because an assistant that lifts a
number off a chart without its trust label reports a calculated rate as an official
statistic — the one failure this product exists to prevent. Only datasets that actually
serve data are listed.

**Answer pages** (`app/answers.py`) are one permanent address per question people ask an
assistant — `/japan-inflation-rate.html`, `/boj-jgb-holdings.html`, `/jgb-10-year-yield.html`
and so on. The title is the question; the first paragraph is the answer, with the period,
the source and the comparison with a year earlier, written into the HTML at serve time from
the same functions that serve `/api/v1`, so it changes when the ingest publishes and never
by hand. Each carries the definition, a chart, the latest readings as a table, a citation
line and the provenance card, and is served as Markdown at `.md` and as JSON at
`/api/v1/answers/{slug}`. A question is one entry in `QUESTIONS` (dataset, series, what
kind of figure it is, the nouns the sentence needs) plus a shell page in `web/`; the
sentence rules — a rate says it is calculated and shows its formula, a level is stated as
published, a comparison across a known series break is not made — live in that one module.

`robots.txt` names the crawlers behind the AI assistants and admits each; `sitemap-pages.xml`
carries a `<lastmod>` per page from the newest release date among the datasets the page
fronts (none for a page with no data behind it). With `INDEXNOW_KEY` set (`app/indexnow.py`),
an ingest that publishes a release reports the pages it changed to Bing and the engines that
share its index; the key is served at `/indexnow-key.txt` as proof of ownership. Unset, nothing
is sent and the ingest is unchanged.

Every page also carries its numbers in the HTML itself, for a crawler that does not run
JavaScript (most of the ones behind AI assistants): `app/readable.py` builds, from the
same functions that serve `/api/v1`, one sentence with the latest reading and one table of
the latest values for the series the page leads with, and `app/prerender.py` writes it
into a collapsed `<details class="readable">` at the foot of `<main>`. The same block is
served as Markdown at the page's address with `.md` in place of `.html` (`/cpi.md`,
`/company.md?code=7203`), and every page links to it with `<link rel="alternate">`. The
landing page's four coverage counts are written into their tiles the same way.

### Party profiles (`#parties`, `#queue`)

Who each fund, company and person IS, curated by hand — see
[`docs/plans/PLAN-PARTY-PROFILES.md`](../docs/plans/PLAN-PARTY-PROFILES.md). The store is
`app/parties.py`; nothing here touches an `eq_*` table, so no vintage can be rewritten from
the console.

- **Its own identifier, not EDINET's.** BlackRock files under sixteen codes, 17,345 of
  25,320 register rows carry no code at all, and no director has one — so a party has our
  id, and the source keys (`edinet_code`, `name_key`, `sec_code`, `person_key`) hang off it
  as aliases. One key belongs to one profile; a duplicate is refused, because it would
  double-count a ranking.
- **Two levels.** A group parent ("Nomura") plus its arms, each with its own `group_role`
  (Asset management · Securities · Banking · Trust). An arm is never made to stand for the
  group, and the roll-up is never the default arithmetic — Nomura Securities' 5% stake is a
  trading book and Nomura Asset Management's is client money.
- **Three type fields, not one list.** `party_class` (what it legally is) · `strategy`
  (how it invests, zero or many) · `holder_role` (why it is on the register). The third is
  what separates a 信託口 nominee from a beneficial owner — the two most frequent names on
  the Japanese register are custodian trust accounts, not owners.
- **Curated, never Official.** These are our judgement; no filing states a category such as
  "hedge fund". `app/filer_labels.py` keeps deriving `filer_type` from the filing's own
  事業内容 and the form shows it beside the curated class, flagged where the two differ.
  Nothing reaches `/api/v1` in this milestone; every profile carries a `public` flag for
  when something does.

Seed the group structure once from the already-curated group map:

```bash
./.venv/bin/python -m app.parties_seed [--dry-run]   # 19 parents + 109 arms
```

**Back it up.** The live store is on the mounted volume; `Export To Repo` copies it to
`curation/parties.json`, which seeds an *absent* store on boot and is meant to be committed.
An unreadable live store is never silently replaced by the seed.

## Investment Assistant (`/assistant.html`)

A signed-in reader's desk of specialists: named, versioned analyst-agents that run on the
**customer's own model key** against the same tools the MCP endpoint serves, post what
they find with the filing attached, and ask a person before anything goes outward. The
plan is `docs/plans/PLAN-INVESTMENT-ASSISTANT.md`; how one run works is
`docs/plans/ARCH-INVESTMENT-ASSISTANT-AGENT.md`. Code lives in `app/assistant/`, its own
namespace — the golden rule for datasets does not apply and nothing here writes a dataset
file.

- **Switches.** Off unless both `ACCOUNTS_ENABLED` and `ASSISTANT_ENABLED` are truthy.
  `ASSISTANT_SECRET` (a long random string) seals each desk's model key and Slack
  webhook before they are stored; without it a desk can be browsed but no key can be
  stored and no run starts. `ASK_ENABLED` is unrelated and stays off: the assistant never
  spends our key.
- **Store.** The `ia_*` tables in `data/workspace.db`, beside the account tables
  (`app/assistant/store.py`): desks, hires, coverage, runs, tool calls, posts, approvals,
  threads, messages, files. SQLite, WAL mode; never the DuckDB files.
- **A run** (`app/assistant/runner.py`): the specialist's brief plus fixed trust rules,
  the desk's coverage list and its last summary, then a tool loop on the customer's key
  through `app/assistant/gateway.py` (Anthropic and OpenAI-compatible endpoints, urllib,
  no SDK). Read tools are `tools_v2.run_tool`; the only action tools are `post_to_desk`,
  `request_approval`, `write_workspace_file` and `my_coverage`, and a note without a
  source is refused. The allowlist in `app/assistant/specialists.py` is enforced by the
  runner, not the prompt. Budgets: 30 tool calls, 60k tokens, 5 minutes. Every call is
  recorded with its latency and a hash of its result.
- **Monitors do their bookkeeping in code** (`app/assistant/digest.py`). A specialist
  whose manifest says `prepare: coverage` does not loop the model over raw filings: the
  runner fetches each covered company's latest earnings and buyback state through the
  same tools, diffs it against the hire's `state/coverage.json`, and hands the model a
  short digest with every document id attached. One model call writes the note; sources
  on the post come from the code, not the model; a scheduled run with nothing new posts
  "no new filings" without calling a model at all. Tool results in the ordinary loop are
  compacted for the model (`runner.compact_result`: explanatory notes, vintage and
  truncation metadata, raw facts and repeated row columns removed; numbers never
  touched). Manifests carry a `tier`: `monitor` specialists run on the desk's optional
  monitor model (a cheaper one is enough), research specialists on the main model.
- **Approval is the only outward path.** `request_approval` writes a pending row and the
  Inbox shows it; `POST /approvals/{id}/approve` is what posts to Slack
  (`app/assistant/delivery.py`). A run cannot send anything itself.
- **Triggers today.** An `@mention` in the `#desk` feed, a thread question, and "Run now"
  on a specialist's page, each executed in a worker thread while the request waits.
  Scheduled runs: `python -m app.assistant.runner --all` (every enabled hire on every
  desk with its default task) from cron or the nightly loop — not wired into `start.sh`
  yet, because it spends customers' keys and should be switched on per desk.
- **API** under `/api/v1/assistant/…` (`app/assistant/api.py`): `status`, `desk`,
  `specialists` and `…/{slug}/hire`, `hires/{id}/run|update`, `coverage`, `feed`,
  `inbox`, `approvals/{id}/approve|decline`, `threads`, `runs`, `audit`, `files`,
  `settings` and `settings/test`. Every route reads the sign-in cookie and answers 401
  without one; the namespace is excluded from the shared response cache by prefix.
- **Outside agents** (`app/assistant/desk_mcp.py`). A reader can connect their own
  Claude Code, Codex or any MCP client to their desk at `POST /mcp/desk`, with a personal
  key made under Settings › Connections (`/api/v1/assistant/connections`; only the key's
  SHA-256 is stored, shown once, revocable). It serves the same read tools as `/mcp`
  plus the desk: `desk_overview`, `read_feed`, `list_threads`, `read_thread`,
  `add_to_coverage`, `post_to_desk`, `request_approval`, `ask_specialist`. There is no
  send tool; `request_approval` lands in the Inbox. Reading tools carry
  `readOnlyHint` so Codex runs them without asking and asks before any write. Calls are
  audited under one run per connection per day. Setup, as the Settings page prints it:
  `claude mcp add --transport http plover-desk <url>/mcp/desk --header "Authorization:
  Bearer <key>"`; for Codex, `[mcp_servers.plover-desk] url = ".../mcp/desk"` and
  `bearer_token_env_var = "PLOVER_DESK_KEY"` in `~/.codex/config.toml`. Both verified
  end to end on 23 Sep 2026 (Claude Code 2.1.278, codex-cli 0.144.3).
- **Coverage lists.** A desk keeps named lists (`ia_lists`, `ia_list_items`): the main
  "Coverage" list, which cannot be deleted, and any the PM creates. The first desks' single
  list (`ia_coverage`) moves into the main list the first time lists are read. A specialist
  watches the main list unless its hire's config names another (`POST
  /hires/{id}/config`); deleting a watched list sends it back to the main one. Routes:
  `GET|POST /lists`, `PATCH|DELETE /lists/{id}`, and `list_id` on `/coverage` and
  `/companies`. The page (`#/coverage`) removes a company in two steps (Remove, Confirm).
- **Coverage search.** `GET /api/v1/assistant/companies?q=` feeds the coverage box as a
  type-ahead: a code, an English or Japanese name, or a market nickname (MUFG), ranked
  exact code › code prefix › name prefix (company-type words such as 株式会社 and filing
  footnote marks ignored), each marked if already covered. Backed by the equity company
  index (~20 ms), falling back to the all-dataset search (~1.6 s) where that index is
  absent. The desk MCP's `add_to_coverage` takes a name too, and answers with the
  candidates when a name matches more than one company.
- **The desk's own MCP servers** (`app/assistant/mcp_client.py`, page `#/tools`). A PM
  registers their firm's or a vendor's MCP server once; its tools are then offered to
  every specialist on that desk, named `ext_<server><id>_<tool>` so they cannot collide
  with ours, and every call is audited like any other. The tool list is cached on the
  server record by the connection check, so a run never waits on `tools/list`. **The URL
  is checked before any request**: https only, every address the host resolves to must be
  public (no loopback, private, link-local or metadata address), no redirects, 30-second
  timeout, 2 MB cap — otherwise a stored server would be a way to read whatever the
  container can reach. `ASSISTANT_MCP_ALLOW_HOSTS` exempts named hosts for local work.
- **Stored keys** (page `#/keys`). Named secrets (`ia_secrets`), sealed by the keychain,
  referred to by an MCP server rather than pasted into it. No route returns a key: reads
  give the name, the last four characters and what uses it. A key in use cannot be
  deleted until the server using it is changed. The model key and Slack webhook stay in
  Settings and are listed on the page for completeness.
- **Health.** `/api/v1/catalog/health` carries an `assistant` row with the last
  successful run when the feature is on, so a stalled runner is visible.
- **Tests.** `tests/test_assistant.py` drives the whole loop with a scripted model and
  canned tools: no key, no network, no data.

## Deploy

`Dockerfile` + `start.sh` build a container that ingests both datasets and then serves them.
`data/` must be a persistent volume — it holds the DuckDB file and the raw source archive.

```bash
railway up            # or: docker build -t observatory . && docker run -v obs:/app/data -p 8007:8007 observatory
```

Live: <https://web-production-c9178.up.railway.app> (Railway project `observatory`, volume at `/app/data`).

Ingest runs before uvicorn binds, because DuckDB takes a single writer and the API holds
read-only connections. A cold boot on network-backed storage therefore takes ~4–5 minutes,
and a redeploy is a short outage. Refreshing data means restarting the service.

**Daily refresh.** `start.sh` is a supervisor, not a one-shot: it ingests, serves, and when
the server ends, ingests again. `app/refresh.py` ends the server once a day — 13:00 UTC by
default, which is 22:00 in Tokyo: after the Ministry of Finance posts the day's yield curve,
and after the 12:00 UTC EDINET capture job, so the equity extractors read the same day's
archive rather than yesterday's.
So the container refreshes itself and the platform's restart policy is never load-bearing;
the container itself never exits.

This replaces the second Railway cron service this file used to recommend. That service was
never created, and between 1 and 3 September 2026 the site quietly served three-day-old
yields because nothing restarted it. A loop in the repo cannot be forgotten and needs no API
token.

Refreshing still means a short outage — the ingests run with nothing serving, which on the
network-mounted volume takes about four minutes. The next step, if that becomes a problem,
is for ingest to build a new DuckDB file beside the live one and rename it into place:
`db.read_cursor()` already reopens when the file changes underneath it.

| Variable | Default | What it does |
| --- | --- | --- |
| `REFRESH_AT` | `13:00` | Daily refresh time, **UTC**. Japan has no daylight saving, so this is 22:00 JST all year. |
| `REFRESH_ENABLED` | `1` | Kill switch for both the refresh and the health watch. |
| `REFRESH_MAX_AGE_HOURS` | `26` | How old the ingest stamp may get before the refresh counts as broken. |
| `ALERT_WEBHOOK_URL` | unset | Slack-shaped webhook; the server posts to it when something needs attention. |
| `EDINET_S3_*` | unset | Bucket credentials for the nightly equity refresh (below). Unset = the refresh skips itself and the shipped equity data is served as-is. |

`REFRESH_SUPERVISED` is set by `start.sh` alone and is what arms the daily shutdown. A
development `uvicorn app.main:app` therefore never ends itself, whatever the clock says.

### The nightly equity refresh

The EDINET-derived datasets — 5% filings, cross-shareholdings, boards and pay, buybacks,
facilities, rental property, shareholder registers, financial statements, AGM votes, segments,
tender offers, semiannual reports, capital raises, corporate events — refresh in the same
cycle, from the same S3 bucket the capture jobs write to. The TDnet wire refreshes in the
same job from its own prefix in that bucket. The listed-issue classification runs in the same job
but reads two small files over HTTP instead (see **Peer groups** below):

```
python equity/refresh_equity.py --seed seed/equity.duckdb
```

They used to be extracted by hand on a laptop and shipped as a seed file. That is exactly
how the 5% filings went four weeks stale: capture moved to the cloud bucket on 6 August
2026, the extractors kept being pointed at the laptop's frozen archive, and every page
still rendered a healthy-looking dashboard over month-old data.

- **Incremental.** Each extractor records how far it has read the archive in
  `eq_extract_runs` and resumes from there (minus a 10-day lookback, because EDINET
  back-fills). A routine night is one day of filings — about 130 documents, under a
  minute for all seven. `--full` re-reads five years and takes hours.
- **The bucket listing is shared.** 182k keys is 26 seconds; `EDINET_LISTING_CACHE` keeps
  one answer for an hour so seven extractors ask once.
- **The seed no longer always wins.** `--seed` installs the shipped database only when its
  watermark is ahead of the volume's, so a redeploy never discards accumulated nights. A
  fresh offline re-extraction (after a parser fix) is still ahead, and is still how a
  rebuild reaches production.
- **Fail-safe, like ingest.** A failing extractor logs `ATTENTION`, leaves its previous
  data live, and never stops the server coming back up.
- **Freshness is reported.** `/api/v1/catalog/health` carries an `equity_extractors`
  block: how far each extractor has read, and whether that is more than 7 days behind
  (long enough to survive the New Year closure without crying wolf). A source on a
  different clock sets its own threshold in `STALE_AFTER_BY_EXTRACTOR` — the
  classification is stamped month-end, so a current copy is up to five weeks old by its
  own date and is judged at 45 days.

### The MCP surface (v2)

`POST /mcp` serves two tool surfaces, chosen by `MCP_TOOLSET` (`v1` · `v2` · `both`, default
`both`). v2 is six generic tools whose `dataset` argument is resolved through the registry, so
a new dataset needs a manifest and a dispatch row, not a new tool:

| Tool | What it answers |
| --- | --- |
| `list_datasets` | what is published — id, section, shape, capabilities, screens, coverage |
| `describe_dataset` | one dataset's card: source, every measure with its trust label and formula, endpoints, notes, live coverage |
| `search` | companies (name or code) and series (name or code) across datasets, one ranked list |
| `get_company` | one company in one dataset, or — with no dataset — a compact profile across every dataset that knows it, with a coverage list |
| `get_series` | a series' history as the published value or a calculated rate, with `as_of` |
| `screen` | a ranked cross-section from the dataset's own screens; an unknown sort answers with the valid ones |

Every result is one envelope — `tool · dataset · data · provenance · calc · vintage · cite ·
coverage` — and no-data is an answer (`data: null`, `missing: why`), never a JSON-RPC error.
The manifests are also served as MCP resources (`observatory://datasets/{id}`,
`observatory://sections`, `observatory://methodology`), and the `initialize` instructions are
generated from the registry so they name every dataset on the server.

### One company, every dataset

`/api/v1/company/{code}` (`app/company_api.py`) walks the registry, calls each dataset that
declares a company view, and returns one document in section order — holdings, register, 5%
filings, board and pay, buybacks, facilities, financials, AGM votes, segments. It is what the
company page and the MCP `get_company` tool both read, so the two cannot disagree.

Three rules, all consequences of the trust contract. **Every block is independent**: a dataset
that fails becomes an `errors` entry and costs the other eight nothing. **Absence is reported,
never omitted**: a dataset with no rows appears under `coverage.missing` with its reason, so
"no facilities filing for this company" reads differently from "facilities are not published
here", and neither looks like zero. **Nothing is recomputed across blocks**: each carries its
own dataset's `calc`, `provenance` and `cite`; yen book values, voting rights and square metres
never meet in a total.

`?compact=1` drops the tables and keeps the counts (~35 KB for Toyota against ~240 KB full);
`?limit=` caps rows per table and discloses the full count; `?datasets=` and `?sections=`
narrow it. Cold is about 230 ms and repeat hits come from the release cache. It is deliberately
**not** in `WARM_ENDPOINTS`: the cache holds 64 entries and warm-up already fills 42, so
priming companies would evict the dataset payloads every page needs.

`?as_of=YYYY-MM-DD` serves the filings that existed publicly on EDINET by the end of that day
— Toyota as of 2025-07-01 returns its FY2025 annual report, not the FY2026 one filed since,
and buybacks and facilities disappear entirely because those archives begin later. The ceiling
applies to the whole document or to none of it: a company view answering half its blocks as of
a past date and half as of today would be worse than refusing.

**The basis is the filed date, and that is a decision.** The plan specified "what the platform
had captured on that date". No capture timestamp exists on any equity table — they record when
a document was *filed*, and nothing records when we fetched it — so capture-time semantics are
not reconstructible for a single filing already in the archive. `as_of` therefore means what
the market could read by that date, which is the more useful basis for a backtest anyway.
Every response says so in its `vintage` block. **Capture time should start being recorded now**,
so vintages accumulating from here can support the stronger claim.

One thing is withheld under a ceiling rather than answered wrongly: the buyback programme
rollup (`eq_buyback_lifecycle`) aggregates every monthly filing of an authorisation, so its
cumulative and completion figures would describe a future the response is not allowed to know.
It is returned empty with `programs_unavailable` saying why; `months` carries each filing with
the cumulative figure it stated at the time, which is the point-in-time answer.

How the ceiling travels is worth knowing before editing a query: it is set once per request on
a contextvar (`app/asof.py`) and read by every filing-selection query. Nothing about that makes
a query which forgets to ask fail loudly — it just quietly returns today's filing. The
guarantee is `tests/test_asof.py`, which runs every company view under a ceiling and fails if
any filing in the response was filed after it, **and** fails if a dataset returns rows while
stating no filing date to check. AGM votes passed silently that way until it was made to carry
one.

### The dataset registry

Every dataset module exports a `MANIFEST` — one plain dict saying what the dataset is,
where its numbers come from, which measures are official and which are calculated (with
the exact formula), where to fetch it and where to cite it. `app/registry.py` collects them
and refuses a card that breaks the trust contract: a calculated measure with no formula, an
official one with a formula, a unit outside the vocabulary (`%` is a share of a level, `pp`
a change of a percentage), a formula for the generic rates that differs from what `api.py`
computes, or an endpoint that is not a real route on the app.

A bad card is **quarantined, never fatal**: the serving process drops that one dataset from
the catalog, keeps serving the rest, and reports it in the `manifests` block of
`/api/v1/catalog/health` (which turns the report to `attention` and alerts). Set
`MANIFEST_STRICT=1` to make a fault refuse to boot instead — for staging, not production.

```bash
./.venv/bin/python -m app.registry --check            # validate every card; exit 1 on a fault
./.venv/bin/python -m app.registry --scaffold cpi-jp  # draft a card for a macro adapter
./.venv/bin/python -m unittest tests.test_manifests
```

A new macro dataset gets its card from `--scaffold`: everything the adapter already declares
is filled in, and what only a person can write — the section, the summary, and above all the
formula for any dataset-specific calculation — is left as `TODO`, which the validator treats
as an error. A draft can never reach the shelf half-finished.

### Knowing when it stops

The per-dataset staleness limits run from 7 days to 950, so a refresh that stops is invisible
inside them for days. The machinery gets its own signal: `start.sh` stamps
`data/ingest_heartbeat.json` at the end of every cycle, and the health report says how long
ago that was.

```bash
curl https://web-production-c9178.up.railway.app/api/v1/catalog/health           # always 200
curl https://web-production-c9178.up.railway.app/api/v1/catalog/health?strict=1  # 503 when unwell
```

Point an uptime monitor at the `strict=1` URL every five minutes and one check covers both
failure modes: stale data or a stopped refresh answers 503, and a service that is down
answers nothing at all. **Do not point Railway's own healthcheck at it** — it would refuse a
deploy over a late source file; that stays on `/api/v1/catalog/datasets`.

The report is deliberately excluded from the response cache (`cache.py`, `NEVER_CACHE`).
Cached, it froze the boot-time answer for the life of the process, so the one endpoint meant
to reveal staleness was the one that could never go stale.

The server also watches itself every 15 minutes, logs `ATTENTION` lines a log rule can key
on, and posts to `ALERT_WEBHOOK_URL` if one is set, at most once every six hours per distinct
fault. That covers detail; it cannot cover the service being down, which is what the external
monitor is for.

## Layout

```
app/
  db.py              generic core schema (datasets, sources, artifacts, releases,
                     series, observations) — nothing Japan/CPI-specific
  ingest.py          fetch -> archive -> parse -> validate -> publish runner
  adapters/estat_csv.py    shared parser for e-Stat long-run CPI CSV files
  adapters/cpi_jp.py       middle-class indices adapter: source identity,
                           validation gates, presentation roles (headline/core/groups)
  adapters/cpi_jp_items.py detailed item indices adapter (~740 series)
  api.py             dataset-scoped JSON API (/api/v1/{dataset}/...)
  tools.py           shared tool layer over the API functions — the only data
                     path for the ask agent and the MCP endpoint
  agent.py           ask (LLM Q&A) loop; off unless ASK_ENABLED is set
  mcp.py             remote MCP server (POST /mcp) for external AI clients
  refresh.py         daily self-restart (under start.sh only) + the health watch
  heartbeat.py       when the ingest cycle last ran — the stopped-refresh signal
  main.py            FastAPI app: API + static frontend
web/
  assets/tokens.css  design tokens (single source of truth for colour, incl. dark mode)
  assets/format.js   centralised number/date/trust-label formatters
  assets/charts.js   house chart chrome for ECharts (vendored, self-hosted)
  assets/nav.js      the site header, rendered from one list of sections/pages
  index.html         Landing: live directory of every dataset (+ assets/landing.js)
  macro.html         Macro / Overview — all five datasets on one screen (+ assets/macro.js)
  cpi.html           Macro / Inflation (CPI)  (+ assets/overview.js, shared by every CPI page)
  tokyo.html         Macro / Inflation / Tokyo Advance · goods-services.html · cpi-sa.html · cpi-long.html
  explorer.html      Macro / Item Explorer (+ assets/explorer.js)
  semis.html         Trade / Semiconductors (+ assets/trade.js, shared by the trade pages)
  autos.html         Trade / Motor Vehicles  (+ assets/trade.js)
  energy.html        Trade / Energy          (+ assets/trade.js)
  machinery.html     Trade / Machinery       (+ assets/trade.js)
  pharma.html        Trade / Pharmaceuticals (+ assets/trade.js)
  food.html          Trade / Food            (+ assets/trade.js)
  population.html    Demographics / Population · representation.html (Vote Weight)
  equities.html      Equities / Overview — live summary per dataset (+ assets/equities.js)
  holdings.html      Equities / Cross-Shareholdings (+ assets/holdings.js)
  ownership.html     Equities / Register           (+ assets/ownership.js)
  stakes.html        Equities / 5% Filings         (+ assets/stakes.js)
  connect.html       Connect Your AI (MCP setup) · manual.html (MCP manual)
  methodology.html   Methodology / how to use
data/
  raw/               archived source artifacts (checksummed, kept forever)
  observatory.duckdb the database (gitignored; rebuild via ingest)
```

## Adding the next dataset

1. Write `app/adapters/<slug>.py` exposing `DATASET`, `SOURCE`, `DOWNLOAD_URL`, `fetch()`,
   `parse()`, `validate()`, `PRESENTATION`, and a `ValidationError`.
2. Register it in `ADAPTERS` in `app/ingest.py` and `app/api.py`.
3. Run `python -m app.ingest <slug>`.

No core schema change, no API change — that is the platform-generality rule from the plan.
If a new dataset seems to need a core migration, stop and revisit the design first.
The rule has been exercised once already: `cpi-jp-items` (dataset #2, ~740 series) was added
as an adapter plus registry entries with zero changes to the core schema or the API contract.

## Trust contract in v1

- Index levels are **Official Statistic** — exactly as published.
- YoY / MoM / 3-month-annualized carry no badge — they are computed from published index
  values, with the formula shown on every surface ("Show calculation") and in every CSV
  export header. The API still tags them `trust: "derived"`; the front end renders the
  formula instead of a label.
- Missing is `—`, never zero; gaps stay gaps in charts.
- **Point-in-time history is kept.** Every release appends what it introduced or revised
  to an append-only store, so any past view is reproducible — see below.

## API

```
GET /api/v1/catalog/datasets
GET /api/v1/cpi-jp/overview
GET /api/v1/cpi-jp/series?q=electricity
GET /api/v1/cpi-jp/observations?series=0001,0161&measure=yoy&start=2020-01
GET /api/v1/cpi-jp/observations?series=0001,0161&measure=yoy&format=csv
                                                   # the same numbers as one wide CSV under a
                                                   #   '#' metadata block (source, release, formula,
                                                   #   vintage). pandas: read_csv(url, comment="#");
                                                   #   R: read.csv(url, comment.char="#"). Add
                                                   #   &as_of=YYYY-MM-DD for a frozen, citable file.
GET /api/v1/cpi-jp/contributions?start=2023-01     # pp decomposition of headline YoY by group
GET /api/v1/cpi-jp-items/breadth?threshold=2       # share of the 582 priced items rising/falling
GET /api/v1/jgb-yields/curve                       # every date x every tenor, one payload
GET /api/v1/jnto-visitors/arrivals                 # every market x every month, plus the hierarchy
GET /api/v1/accommodation-jp/accommodation?area=13 # one area's whole cube — guest nights by hotel type,
                                                   #   nationality, purpose, residence and property size —
                                                   #   plus the guest-nights/foreign/occupancy backbone for
                                                   #   all 58 areas and every named municipality, so the
                                                   #   regions ranking needs no second call. Omit `area`
                                                   #   for All Japan; series codes are nights.<area>[.jp|
                                                   #   .fx|.type.<t>|.nat.<n>|.res.<r>|.purpose.<p>|
                                                   #   .rooms.<b>], occ.<area>[.type.<t>], muni.<code>.<m>
GET /api/v1/trade-semis/observations?series=exp.70131000.50105.val&period=fiscal_quarter&fy_end=3
                                                   # any monthly FLOW summed into a company's fiscal
                                                   #   quarters or years (period=fiscal_year); refuses an
                                                   #   index, a stock or a rate; complete periods only
GET /api/v1/equity/segments/lens/8035              # Company Lens: filed regions, customers and segments
GET /api/v1/equity/segments/lens/8035.csv          #   beside the customs flows the company is mapped to,
GET /api/v1/equity/segments/customers?name=Taiwan  #   in its own fiscal periods; who names whom
GET /api/v1/equity/segments/supply-chain           #   the editorial commodity -> company mapping
GET /api/v1/trade-semis/trade?flow=exp&commodity=70323050
                                                   # one commodity x every partner x every month,
                                                   #   plus world totals for all eleven commodity-flows.
                                                   #   trade-semis series codes are flow.commodity.partner.measure:
                                                   #     flow      exp | imp
                                                   #     commodity the Ministry's 概況品 code, per direction
                                                   #     partner   e-Stat area code (MoF country code + '50')
                                                   #     measure   val (¥1,000) | qty (commodity's own unit)
GET /api/v1/population-jp/observations?series=13.all.population,13.fgn.population
                                                   # population-jp series codes are geo.segment.measure:
                                                   #   geo     '00' Japan, '01'-'47' JIS prefecture code
                                                   #   segment all | jp | fgn
                                                   #   measure population, households, births, deaths,
                                                   #           net_change, natural_change, social_change,
                                                   #           in_/out_domestic|overseas|total, ...
                                                   #           age_65_69_female, age_total_male, ...
GET /api/v1/cpi-jp/releases
GET /api/v1/catalog/health                         # is every dataset current, and did an ingest go quiet
GET /api/v1/catalog/manifests                      # every dataset's card: source, measures, formulas, endpoints
GET /api/v1/catalog/manifests/cpi-jp               # one card; an unknown id answers with the valid ids
GET /api/v1/catalog/sections                       # the fixed section list and which datasets sit in each
GET /api/v1/company/7203                           # every dataset's view of one company, in section order
GET /api/v1/company/7203?compact=1                 # facts and row counts only, no tables
GET /api/v1/company/7203/coverage                  # which datasets hold this company and which do not
```

Human reference at `/api.html` (also `/api/docs`), rendered from the OpenAPI schema and the
dataset manifests by `web/assets/apidocs.js`; the schema is `/api/openapi.json` and the framework's
try-it-out console is `/api/swagger`. Tag a new router by product area and, where a handler needs
parameters to answer, give it an `openapi_extra={"x-example": ...}` URL so the page can show one.

### Point-in-time (vintages)

The Statistics Bureau republishes the whole history every month, and past months can
change. `observation_vintages` records what each observation was worth at each release
and is **append-only** — a revision is a new row, never an edit. Two endpoints read it:

```
GET /api/v1/cpi-jp/observations?series=0001&as_of=2026-08-20   # the data as it stood that day
GET /api/v1/cpi-jp/revisions?series=0001&period=2026-07        # how one figure has moved, release by release
```

`as_of` answers what a reader would have seen on a given date, before any later revision
— which is what makes a published chart citable years later and a backtest honest. The
release block in the response describes the vintage that was live then, and the response
echoes `as_of` so the same URL returns the same numbers next year.

The store is **change-only**: a release writes a row for a value it introduces or
changes, not for one it republishes unchanged. Publishing July 2026 CPI wrote 78 rows
against 52,110 observations, because only the new month was new. A withdrawn observation
is written as an explicit `NULL` tombstone, so `as_of` can tell "not published yet" from
"published then retracted". Series rows are upserted rather than replaced, so `series_id`
is stable for the life of the series; a series the source stops publishing is marked
`active = FALSE`, never deleted.

```bash
./.venv/bin/python -m app.vintages status   # vintages held per dataset
./.venv/bin/python -m app.vintages seed     # first vintage, from the live table
./.venv/bin/python -m app.vintages compact  # repair: drop rows restating the value in force
```

`seed` reconstructs the **first** vintage from live observations, which already carry the
release that produced them. Two things about it are load-bearing. It must run *before* an
ingest, not after — an ingest replaces the live values, and the pre-existing vintage is
then unrecoverable except by re-parsing the raw archive; `start.sh` runs it first on every
boot for that reason. And it skips any dataset that already has history: after a later
release the live table holds *that* release's values, so re-seeding would restate the whole
history as a change made by the newest release. It did exactly that once during
development — thousands of phantom revisions and a full copy per release instead of a
delta — which is what `compact` exists to repair.

`compact` deletes rows whose value equals the one already in force. Such a row asserts a
revision that never happened; removing it leaves every as-of view byte-identical, which is
the check to run before and after. `status` warns when any are present.

### Ingest health

Fail-safe ingest is silent by design, and silence looks like success. In August 2026 the
July CPI file was fetched, archived, and never published; the site served June for three
weeks and nothing said so. `/api/v1/catalog/health` reports two signals per dataset:
`stale` (the newest period served is older than the dataset's tolerance) and
`unpublished_artifact` (a file was fetched after the one currently published — always
either a validation failure or a crash). The second is the sharper of the two: in the
August case the staleness threshold had not yet been crossed, and only the orphaned
artifact revealed the problem. `start.sh` prints the same report after each boot's
ingests, so a log rule can alert on it.

## MCP (connect an AI assistant)

`POST /mcp` is a stateless remote MCP server — paste the URL into Claude (or
any MCP client) and its assistant can search series, pull history, and
decompose headline inflation through the same read-only tool layer that backs
`/api/v1`. Every response carries its trust label, its formula where the
figure is calculated, and a permanent `cite` URL on the site. Setup steps for
readers live at `/connect.html`. No key required; per-IP rate limited;
`MCP_ENABLED=0` turns it off. The protocol is implemented directly in
`app/mcp.py` (the official SDK needs Python 3.10+, local dev runs 3.9).
The user manual lives at `/manual.html`.

The cross-shareholding dataset (`/api/v1/equity/...`, built offline by
`observatory/equity/extract.py` into `data/equity.duckdb`) is exposed through the same
MCP server; its tools are listed only on servers where the database
file is present. Production receives it as `seed/equity.duckdb` baked into
the image and copied onto the volume at boot by `start.sh`.

A holding that leaves the named policy table has not necessarily been sold:
filers may move it to 純投資目的 (pure investment), which is disclosed in a
table of its own and captured in `eq_reclassified`. `/api/v1/equity/company/
{sec_code}` returns it as `reclassified`, alongside `notes` (the filing's own
footnotes to the table) and `flows` (the filing's own sale proceeds and
acquisition costs, the honest test of what actually moved).
`/api/v1/equity/reclassified` ranks filers by the value reclassified rather
than sold. Named positions also carry `pct_outstanding` — the stake as a share
of the issuer's issued shares less treasury, taken from the issuer's own annual
report *nearest* the holding's fiscal year end and therefore calculated, not
filed. It is withheld, with the reason in `pct_unavailable`, wherever a split
or share issue leaves the share base indeterminate or the result would exceed
100%: a stake measured against the wrong share base overstates it by the whole
split ratio. See `docs/METHODOLOGY-CROSS-SHAREHOLDINGS.md` §4.5–4.7 and §8.5.

`pct_outstanding` sizes a stake against the *issuer*. The `scale` block on
`/api/v1/equity/company/{sec_code}` sizes the policy book against the *filer*:
its total policy shareholdings as a share of shareholders' equity and of total
assets, with `scale_history` giving the same reading per fiscal year. The
numerator is the filing's own total for the whole policy bucket
(`eq_filing_totals`), summed across the entities a filing discloses — **not**
the sum of the named rows, which cover only the largest issues and run about
three quarters of the true total. The denominators are read from the filing's
own 主要な経営指標等の推移 table, and `equity_basis` says which accounting
figure was used: an IFRS or US-GAAP adopter stops tagging the Japanese
consolidated figure but leaves prior years in place, so a naive read falls
through to the parent-only figure and reads several times too high. A
parent-only denominator is labelled, never silently mixed with a group one.
See §4.8–4.9 and §8.7.

**Measurement basis.** Every yen figure in this dataset is on one convention —
balance-sheet **carrying amount**, at fiscal year end, from an **annual**
securities report — and that convention is now returned as a typed `basis`
tuple on every response that carries a yen figure (`measurement`,
`entity_scope`, `share_scope`, `trust_included`, `as_of`, `period_type`).
It is *derived*, not stored: `entity_scope` is read from `holder_table`
(`reporting` → `parent_only`, `largest` → `largest_holding_company`,
`second_largest` → …) and `share_scope` from `share_class`, both of which the
filings have always carried. Nothing is backfilled and no stored row is
written to. `app/basis.py` is the source of truth; `equity/extract.py`
projects the vocabulary into `eq_basis_labels` and the view
`v_holdings_basis` so it is queryable in SQL, and the API never reads either —
a database an extract has not touched still serves a correct basis.

This matters because it is *not* the basis the press and IR decks use.
Reduction targets and progress figures are quoted on acquisition cost, often
at commercial-bank level rather than group level, often listed-only, and often
at a half-year date this product does not hold. The differences run to
multiples: Nikkei Asia put the three megabanks at ¥2.56tn at 2025-09-30; the
filings give ¥11.5085tn at 2026-03-31. Both are right.
`entity_scope` is the axis that hides in plain sight — SMFG discloses ¥3.458tn
of listed policy shares under SMBC and ¥153.8bn at the holding company, so a
figure quoted for one entity is not the group figure.

`GET /api/v1/equity/claim-check` (MCP tool `check_claim`) reconciles an
external figure against what the filings support. It **never infers the
claim's basis from its wording**: the caller states it in `claimed_*`
arguments, or the answer says the gap cannot be classified. `context` is
echoed for the record and never parsed. Verdicts describe what this dataset
can corroborate (`consistent`, `cannot_verify`, `date_mismatch`,
`basis_mismatch`, `scope_mismatch`, `measure_not_held`, `coverage_gap`) and
are never a judgement that a published figure is wrong. There is no
book-value-reduction measure here: `sale_proceeds_yen` is cash received over a
fiscal year, and the two are never presented as equivalent.

### Boards and pay (`/api/v1/equity/governance/...`)

The third surface, from the same annual reports and the same DuckDB file
(`observatory/equity/board_extract.py`; see `docs/METHODOLOGY-BOARDS-AND-PAY.md`):

| Endpoint | What it returns |
| --- | --- |
| `/governance/summary` | coverage first, then market aggregates — board size, director age, 70+ share, female ratio, pay per officer. `?listed=true` for listed filers only |
| `/governance/company/{sec_code}` | one filing: the board, the officer-pay table, the named individuals |
| `/governance/history?sec_code=` | that company across every extracted fiscal year |
| `/governance/screen?metric=` | ranked cross-sections; `/screen/metrics` lists them |
| `/governance/named` | highest-paid named individuals |
| `/governance/years` | fiscal years available |

Three things the responses carry because a reader will otherwise get them
wrong. **Named pay is 連結報酬等 — consolidated**, a different basis from the
officer-category table, so the two are never netted (`pay_basis`, and
`named_exceeds_category` where the arithmetic proves it). **Pay components need
not sum to the filed total** — filers disagree on whether 非金銭報酬等 is
additive — so the total is the published number and `components_reconcile`
says whether that row adds up. **`pay_consistency_flag`** marks a filing whose
per-head pay implies an officer above the ¥100m individual-disclosure
threshold while naming nobody: the filer's own figures contradict each other,
and the numbers are still published exactly as filed.

Two endpoints exist for the page rather than the API contract:
`/governance/companies` (search scoped to this dataset — a company can have a
board here and no policy holdings next door) and `/governance/trend`, a matched
panel of the companies with a clean filing in *every* year of the window, one
filing per company per year. Coverage differs by fiscal year, so an unmatched
average would move because the population moved.

The router is registered ahead of the holdings router in `app/main.py` so the
longer `/equity/governance/` prefix is matched first.

The page is `web/governance.html` (**Equities → Boards & Pay**): a market view
— coverage strip, the panel trend chart with a measure picker, ten screens — and
a company view at `?c={sec_code}` with the board, the pay table, the individuals
disclosed and a five-year record. The URL encodes the measure, the screen and the
scope, so any view is citable.

### Financials (`/api/v1/equity/financials/...`)

Every tagged number in the annual securities report, kept long
(`observatory/equity/fin_extract.py`, parser `fin-1`; extractor name `financials`
in `eq_extract_runs`, so it reports its own freshness). Four tables:
`eq_fin_filings` (one row per filing: status, accounting standard, the
balance-sheet gate per basis, which statements the filing carries),
`eq_fin_facts` (one row per element × context: every numeric fact in a plain
year context, current and up to four prior years, consolidated and parent),
`eq_fin_lines` (the filing's own presentation order for the five-year summary
and each primary statement, from the t1 package's presentation linkbase) and
`eq_fin_elements` (the element dictionary with the filer's Japanese label and,
where the filing carries one, its English label). Statements and the
key-indicator panel are both views over those tables; adding a note or a
dimensional breakdown later is a filter change in the extractor, not a schema
change.

| Endpoint | What it returns |
| --- | --- |
| `/financials/company/{sec_code}` | the key-indicator panel across fiscal years — each year from the latest filing covering it, the element behind every field, the summary lines as filed; `?basis=`, `?as_filed_in=YYYY` for one filing's five years as first published |
| `/financials/statements/{sec_code}?statement=bs\|pl\|ci\|cf\|ss\|summary` | one statement of one filing, every line in the filer's order with Japanese and English labels, current and prior values; `?basis=`, `?year=` |
| `/financials/facts/{sec_code}?element=` | one element's every filed value across filings and restated years |
| `/financials/elements?q=` | the element dictionary |
| `/financials/screen?metric=` | ranked cross-section, one filing per company; `/screen/metrics` lists them |
| `/financials/summary`, `/financials/companies?q=` | coverage; search scoped to this dataset |

Nothing is recomputed: ratios are the filer's own (`*_pct` = filed fraction ×
100), and the only derived figures are a statement's change columns, computed
on the page. Methodology is on the site's Methodology page under *Financial
statements and key indicators*.

The page is `web/financials.html` (**Equities → Financials**): a market view —
coverage strip and a ranking on any key indicator — and a company view at
`?c={sec_code}` with the record chart, the five-year table and each statement
under tabs. The URL encodes basis, statement, filing year and chart. MCP tools:
`get_financials`, `get_financial_statement`, `get_financials_screen`.

Running it locally reads the laptop archive; `--sec-codes 7203,8306` narrows a
run, `--all --source s3 --new-only` is what the nightly refresh does. The first
production run has no watermark and therefore reads the whole bucket — every
annual report since 2021 — which is hours, not minutes; run it once by hand
before relying on the nightly window.

#### Calculated ratios and the screener

`app/fin_metrics.py` is the one place a financial number is *computed*: ROE and
ROA on average balances, margins, equity ratio, asset turnover, growth, cash
conversion, simple free cash flow, cash to assets, and implied PBR / dividend
yield from the filer's own year-end PER (no price feed exists). Every row
carries the formula, the inputs used (value, element, fiscal-year offset) and
the filer's own ROE and equity ratio with the difference — our ROE reconciles
to the filer's within 0.5 pp for over 99% of companies. Computed on request
over the latest filing per company and memoised per database version (about
0.3 s for the universe).

| Endpoint | What it returns |
| --- | --- |
| `/financials/metrics/{sec_code}` | one company's ratios with formulas, inputs and the filer's own figures |
| `/financials/screener?sort=&order=&industry=&standard=&roe_min=&…` | filtered, ranked cross-section; `/screener/options` lists industries, standards, metrics and formulas |

The page is `web/screener.html` (**Equities → Screener**); the URL carries every
filter and the sort. MCP tools: `get_financial_metrics`, `screen_financial_metrics`.

### US financials — the SEC shelf (`/api/v1/us/financials/...`)

The US counterpart of the financials above, in the same shape, from the SEC's
own parse of its filings: the quarterly *Financial Statement Data Sets*
(every XBRL 10-K, 10-Q, 20-F and 40-F since 2009, four tab files per quarter)
that `equity/us_capture.py` already banks under `us/`. Nothing is parsed from
the submission text files; `observatory/equity/sec_extract.py` (parser
`sec-1`) loads each quarter's zip straight into `data/sec.duckdb` — its own
file, because a quarter is ~140MB and `equity.duckdb` ships as a seed. One
quarter is one vintage: recorded with the zip's SHA-256 in `sec_quarters`,
reloaded only when the SEC republishes it or the parser changes, staged and
validated first and swapped in inside one transaction, so a bad file leaves
the previous load live. Tables: `sec_filings` (every filing, with this
platform's balance-sheet verdict: assets = liabilities + equity on the
period end, one basis point of tolerance — `clean` / `partial` with the
reason / `skipped` for non-periodic forms), `sec_facts` (every numeric fact
of the periodic reports: tag, period end, span in quarters, unit, segment,
value; the SEC's nil facts dropped as missing), `sec_lines` (which facts sit
on which statement, in the filer's order with the filer's label),
`sec_tags` (the taxonomy) and `sec_segments` (the dimensional qualifiers,
once each). Identity is the SEC CIK; on the shared company surfaces it is
written `cik:320193` so a four-digit Japanese code can never resolve to a US
filer.

| Endpoint | What it returns |
| --- | --- |
| `/us/financials/companies?q=` | filers by name or CIK |
| `/us/financials/company/{cik}?form=10-K\|10-Q` | key indicators per filing — revenue, incomes, EPS, assets, equity, cash, cash flows, capex, dividends, buybacks, shares — each naming the tag it came from |
| `/us/financials/statements/{cik}?statement=BS\|IS\|CF\|CI\|EQ&period=&fy=` | one statement as filed, with the filer's own comparative |
| `/us/financials/facts/{cik}?tag=&form=&segments=1` | one tag's history across filings |
| `/us/financials/tags?q=` | the taxonomy, to find a tag name |
| `/us/financials/screen?metric=&fy=&unit=USD` | filers ranked on one indicator, latest annual report, within one unit |
| `/us/financials/summary` | quarters loaded (with hashes), filings by form, verdict counts |

Freshness sits in `/catalog/health` under `equity_extractors` as
`sec-financials` (stale 130 days after the newest loaded quarter ends — by
then the next set has been published). `start.sh` loads the newest
`SEC_QUARTERS` (default 4) quarters at boot when the bucket is configured;
`app/backfill.py` then deepens the file two quarters a slice, copy → load →
swap, until it holds `BACKFILL_SEC_QUARTERS` (default 12; 0 = leave it).
MCP tools: `search_us_companies`, `get_us_financials`,
`get_us_financial_statement`, `get_us_facts`, `screen_us_financials`; the
generic `get_company` / `search` / `screen` / `describe_dataset` reach it as
dataset `sec-financials`. Not a product surface: it is the comparison shelf,
served so a Japanese figure has a US counterpart in the same shape.

### Noticing when a source changes shape

Three things can go wrong with a feed. Until now only the first was caught.

| | What happens | Caught by |
| --- | --- | --- |
| It stops | the job dies, the source is unreachable | the extractor records the failure, last good data stays live, freshness stops advancing |
| It empties | the source renames a field; the parser still says `clean` and a column is blank | `equity/metrics.py` |
| It lies | the field is full and the values are wrong | `equity/canary.py` |

**Canaries** are filings whose answers a human has already read off the
document. `equity/canary_expected.json` holds eleven of them, one per shape
rather than one per company: a table-form filing and a prose-form filing, a
third-party bid and an issuer self-tender, an earnings release with blank
forecast fields. They are reparsed every night and asserted exactly. Both real
bugs from the week the extractors were written are provably caught: restoring
the old self-closing-blind pattern turns one expected 13.32bn into `None`, and
loosening the prose label turns a company name into the section heading above
it.

A canary is never "fixed" by editing the expected value to match new output.
The filing is immutable, so the old value was either right and the parser is
now broken, or wrong and always was. Open the document first.

**Metrics** cover the whole dataset approximately where canaries cover a few
filings exactly. After every run `equity/metrics.py` records how many rows each
table holds, what share of every COLUMN came back non-null, and the clean /
partial / failed mix — roughly 600 measurements per run, with the column list
taken from the database schema rather than a hand-kept list that would be wrong
by the second dataset. It then compares against the median of the last five
runs and flags a fill rate that falls more than 15 points, a column that empties
or disappears, a column that appears, and a partial or failed share that rises
more than 10 points. Tables under 200 rows are not compared, because a day with
three filings swings wildly without anything being wrong.

Flags land in `eq_extract_drift`, and `/api/v1/catalog/health` carries them per
dataset as `shape_flags` beside freshness. **Drift only ever warns.** A quiet
week with no takeover bids is not a broken parser, and a check that could block
an ingest would cost a day of data the first time it cried wolf and be switched
off within the month.

**The heartbeat is the only thing that reaches a human.** Failed extractors and
broken canaries ping `HEARTBEAT_URL`'s failure endpoint; drift alone does not,
because a warning that pages someone weekly stops being read. Set
`HEARTBEAT_URL` on the web service (healthchecks.io, Cronitor, Better Stack —
anything with a ping URL) or none of this reaches you: unset, the ping is a
no-op by design.

### Recently extracted, not yet served

Three EDINET families were archived for years before anything read them. They now have
extractors and appear in `/api/v1/catalog/health` like every other dataset, but they have
**no API surface and no page yet** — the tables are there to be queried, and the endpoints
are the next piece of work.

| Extractor | Doc types | Tables | Rows over the archive |
| --- | --- | --- | --- |
| `toi_extract.py` (`tender-offers`, `toi-1`) | 240 · 250 · 270 · 290 · 300 | `eq_toi_filings` | 2,417 documents, 566 bidder/target pairs |
| `ssr_extract.py` (`semiannual`, `ssr-1`) | 160 · 170 | `eq_ssr_filings`, `eq_ssr_facts` | ~10,000 filings |
| `issue_extract.py` (`capital-raises`, `iss-1`) | 030 · 040 | `eq_issue_filings`, `eq_issue_allottees` | 4,339 filings, 3,126 named allottees |
| `event_extract.py` (`corporate-events`, `ev-1`) | 180 · 190 | `eq_event_filings` + shareholders, officers, parties | 23,534 filings, 12,373 of them non-AGM |
| `tdnet_extract.py` (`tdnet`, `td-1`) | TDnet wire | `eq_tdnet_items`, `eq_tdnet_filings`, `eq_tdnet_facts` | 15,898 disclosures, 3,747 earnings releases, 207,535 facts |

Three things about them are worth knowing before querying:

- **A tender offer is a sequence of documents, not a state.** The offer, its amendments,
  the target board's opinion and the result are each an immutable row; `pair_key` joins the
  two sides and `round_no` separates one offer from the next by the same buyer for the same
  target. Kamogawa Grand Hotel was bid for at ¥120 and again at ¥290 five weeks later —
  two rounds, one pair.
- **The two ownership ratios in a tender offer are stored exactly as filed and are not
  gated.** The denominator is often not the tagged total but a base share count disclosed
  in a footnote in prose, and it differs per filer. Our own arithmetic sits beside them in
  `ratio_after_recomputed`, clearly derived, with `ratio_consistent` saying whether the two
  agree. They disagree about two thirds of the time, which is exactly why it is not a gate.
  Same lesson as the AGM approval percentage.
- **Semiannual facts live in their own tables on purpose.** A half-year revenue and a
  full-year revenue are the same element in the same unit for the same company; putting
  them in `eq_fin_facts` would leave nothing but a remembered filter between a reader and a
  2x error. `span` distinguishes six-month, three-month and full-year figures inside the
  interim tables too.

Capital raises carry the dilution signal the archive was kept for: 1,772 of the 4,339
filings are third-party allotments (第三者割当), 1,750 of them naming the allottee.

**Corporate events** are the other half of the 臨時報告書 pile. `agm_extract.py` has always
opened every one of these documents, found no vote table in half of them, written
`not_agm` and moved on; those 12,373 filings are now read for what they actually say —
mergers, share exchanges, company splits, subsidiary and parent moves, chief-executive
changes, major-shareholder changes, auditor changes, litigation, covenant breaches. The
event type is not guessed: each filing carries one inline-XBRL block naming it, so all
23,534 are classified across 40 types, and the enabling clause the filing cites
(内閣府令 第19条第2項第N号) is stored beside it as an independent check. Four families also
yield structured detail, at 87–96% coverage.

**TDnet is the one dataset here that could not be rebuilt.** The exchange deletes the wire
after about 31 days, so the archive that began on 2026-07-10 is the only copy outside the
TSE, and it grows one day at a time rather than backwards. It is also the fast half of the
record: a 決算短信 lands here roughly six weeks before the statutory filing reaches EDINET,
and it carries the one thing EDINET never does — **management's own forecast**, with upper
and lower bounds, revised whenever the company changes its mind. Of 3,747 earnings releases
read so far, 3,671 carry forward guidance.

One trap is worth repeating because it has now bitten in three separate parsers. A fact the
company did not supply is written SELF-CLOSING (`<ix:nonFraction … xsi:nil="true" />`).
Matched with a lazy `(.*?)</…>` the pattern runs past the slash and captures the next
fact's digits, so a blank forecast silently takes its neighbour's number: Nihon Ski Resort's
empty guidance came out as 15.8 yen against 11.5bn of actual sales, which was in fact the
+15.8% change figure two rows down. Every parser here refuses a tag whose attributes end
in `/`.

### Buybacks (`/api/v1/equity/buyback/...`)

The fourth surface, and the only one not built from annual reports: EDINET type
220, the **monthly** 自己株券買付状況報告書 a company files while a buyback runs
(`observatory/equity/buyback.py`, parser `bb-2`). Announcement → execution → cancellation
all come out of that one filing.

| Endpoint | What it returns |
| --- | --- |
| `/buyback/summary` | coverage, then the market aggregates: yen authorised, yen bought, retirements, and the lifecycle split |
| `/buyback/monthly` | yen bought and yen retired by reporting month — the chart feed; `partial_month` marks the archive's edges |
| `/buyback/programs` | one row per authorisation: announced, executed, unspent. `?lifecycle=`, `?sort=` (see `/programs/sorts`), `?q=` |
| `/buyback/retirements` | filing-months in which shares were cancelled outright |
| `/buyback/company/{sec_code}` | one company: its authorisations, its month-by-month buying, its treasury and retirements |
| `/buyback/companies` | search scoped to companies that filed a buyback report |

Four things the responses carry because a reader will otherwise get them wrong.
**Authorised is a ceiling a board voted for, not spending** — never summed or
netted against what was bought. **The filer publishes its own progress
percentage**, which is official as filed; `completion_pct` is ours and is
returned alongside it, never instead. **A closed window is not an abandoned
programme** — `expired_unspent` means the acquisition period ended with the
authorisation unspent, while a formal 取得中止 is announced on TDnet, which this
dataset does not carry. **Retiring shares (消却) is a different act from buying
them**: bought shares may sit in treasury for years, so retirements are reported
on their own and never netted against purchases.

`unspent_yen` is empty, not zero, where the filing states an authorisation but
no cumulative — unknown is not nothing. `dates_inconsistent` marks a filing
whose own dates put the resolution after the start of the period it authorises
(filers mistype the year); the row is published exactly as filed, and such a
filing also splits one programme into two rows, because the resolution date is
what identifies an authorisation.

Coverage is capped by the source, permanently: **EDINET purges type 220 filings
after about a year**, so the archive begins at 2025-08-12 and no earlier filing
is retrievable by anyone. Everything before that exists only where somebody was
already capturing — which is the whole argument for the daily capture job.

The page is `web/buyback.html` (**Equities → Buybacks**): a market view —
coverage strip, bought-versus-retired by month, the announced-versus-executed
ranking with a lifecycle filter — and a company view at `?c={sec_code}` with
each authorisation, the month-by-month record and the treasury table. The URL
encodes the filter and the ranking, so any view is citable.

### Peer groups (`/api/v1/equity/cohorts/...`)

A rank across the whole listed market is the wrong denominator for almost every
question anyone asks: a 3% female board is unremarkable among 3,700 issues and
conspicuous inside TOPIX Core30. A **cohort** is the peer group a number is read
against, and `app/cohorts.py` is the one place a cohort spec becomes a set of
security codes — so "within TOPIX Core30" names the same companies on the
financials screener, the board screen and the register screen alike.

Specs are short because they have to survive in a URL:

| Spec | Cohort |
| --- | --- |
| `topix` | every TOPIX constituent |
| `size:core30` · `large70` · `mid400` · `small1` · `small2` | the TOPIX scale bands; `size:large` and `size:small` roll them up |
| `segment:prime` · `standard` · `growth` | JPX market segment |
| `ind33:3650` · `ind17:6` | the JPX 33- and 17-industry classifications |
| `index:nk225` | Nikkei 225 membership — **restricted**, see below |
| `codes:7203,6758,…` | a basket you define, up to 500 codes |

The classification comes from JPX's own listed issue list (東証上場銘柄一覧,
`observatory/equity/class_extract.py`, parser `class-1`; extractor name
`classification` in `eq_extract_runs`) — market segment, both industry
classifications and the scale band, exactly as published, never inferred. The
issues carrying a scale band **are** the TOPIX constituents (1,636 at
2026-08-31). This platform calculates no index and licenses none.

Three rules the code enforces rather than documents:

- **Vintages, not a snapshot.** A vintage is one accepted read, identified by the
  SHA-256 of its bytes and stamped with the file's own effective date; the raw
  file is archived under `data/raw/` first. A stored vintage is never rewritten —
  the same bytes store once, a revised file at the same date lands *beside* the
  first — so index reviews and segment migrations accumulate as history and
  `?as_of=` can reconstruct a cohort as it stood. Neither publisher offers
  back-history, so this accumulates forward, like every other vintage here.
- **Restricted membership never leaks.** The Nikkei 225 constituent file is the
  publisher's copyrighted work and may not be redistributed. Its rows are stored
  with `public = FALSE` and will not resolve unless `INTERNAL_COHORTS` is an
  explicit truthy value — off by default, the same shape of kill switch as
  `ASK_ENABLED`. With it unset the cohort is absent from the catalogue, from a
  company's cohort list and from the assistant tools, and asking for it answers
  400 rather than the whole market.
- **Baskets are stateless.** The serving process never writes to the database
  (guardrail 5), so there is nowhere to save a named basket to — and that is the
  better design: a basket carried entirely in its spec string is a permanent,
  citable URL. The page remembers names for baskets in the reader's own browser.

| Endpoint | What it answers |
| --- | --- |
| `/cohorts` | every cohort that can be asked for, grouped, with member counts |
| `/cohorts/metrics` | what a cohort can be compared on, by family, each with its formula |
| `/cohorts/members?cohort=` | who is in a cohort, with the classification each member carries |
| `/cohorts/company/{sec_code}` | the cohorts one company belongs to, and its default peer group |
| `/cohorts/compare?cohort=&metric=&highlight=` | one measure across the cohort: every member ranked with its percentile, the cohort's median and quartiles, and one company located inside them |

`?cohort=` also narrows `/financials/screener`, `/governance/screen` and
`/ownership/screen`, and reaches the assistant as `filters.cohort` on `screen`
plus two tools of its own, `list_cohorts` and `compare_cohort`.

Nothing here is a new measurement: every metric is either as filed or one of the
ratios `fin_metrics` already computes, and it carries the same formula it carries
on its own page. The cohort statistics are derived and carry theirs — quartiles by
linear interpolation (R type 7), percentile by mid-rank on ties. **A member that
does not report a measure is excluded from the distribution and from every rank,
listed separately with the reason, and counted — never imputed, never zero.**

The page is `web/cohorts.html` (**Equities → Peer Groups**): pick a cohort and a
measure, and read the distribution with the quartiles marked, one company
highlighted, and the members ranked beneath. Where a cohort's tails are extreme
the chart cuts its axis to the 1st–99th percentile, counts the outliers in the
end bars and says so; the table always carries every value. The URL encodes the
cohort, the measure, the order and the highlighted company, so any view is
citable.

### Shareholder register (`/api/v1/equity/ownership/...`)

The reverse of cross-shareholdings: who holds each listed company. Extracted
from the ownership section of the same annual reports (`observatory/equity/ownership_extract.py`,
parser `own-1`) — 大株主の状況, the named holders at the top of the register,
and 所有者別状況, the whole register split by investor category. Methodology:
`docs/METHODOLOGY-OWNERSHIP.md`.

| Endpoint | What it answers |
| --- | --- |
| `/ownership/summary` | coverage first, then the market aggregates: foreign ownership, the share held through nominees, the register split |
| `/ownership/company/{sec_code}` | one filing: the named holders, the investor-type split, and the company's year-by-year record |
| `/ownership/holder/{key}` | the reverse view — every top-ten register this holder appears in. `key` is an EDINET code where one resolved, else the filed name |
| `/ownership/holders` | who appears in the most registers; nominee accounts excluded unless `?include_nominees=true` |
| `/ownership/screen?metric=` | companies ranked on one register metric; `/screen/metrics` lists them |
| `/ownership/companies?q=` | search scoped to companies with an extracted register |
| `/ownership/years` | fiscal years available |

Two disclosures travel with every number, because without them the data is
actively misleading. **The register is not beneficial ownership**: two nominee
trust banks sit at the top of almost every register in Japan holding for index
funds and pension money they do not own, and a fifth of all named rows are
custody accounts. Every row carries a `holder_kind` — **ours, derived from the
name, never a filed field** — and the holder ranking excludes nominees by
default because that is the ranking that means something. **The two percentage
columns have different denominators**: a register row's ratio is of shares in
issue *excluding* treasury (the filing's own denominator), while the
investor-category percentages are of *all* issued shares. They are never netted.

Percentages are stored as percent, converted from the XBRL fraction at the
precision the filing states. Gates recompute the filer's own totals — category
units against the 計 row exactly, category and register percentages inside what
the filing's own printing precision allows, and no holder above shares in issue.
A filing that fails one is published anyway, marked `partial`, with the failure
in `detail`.

The page is `web/ownership.html` (**Equities → Register**): a market view —
coverage strip, the holders appearing in the most registers with a nominee
toggle, and screens on foreign ownership, individuals, nominee share and
concentration — plus a company view at `?c={sec_code}` with the register, the
investor-type composition and the year-by-year record, and a holder view at
`?h={edinet_code}`. The URL encodes the screen, its direction and the nominee
toggle, so any view is citable.

### 5% filings (`/api/v1/equity/stakes/...`)

The fast tape: EDINET types 350 and 360, the large-shareholding reports
(`observatory/equity/lvh_extract.py`, parser `lvh-1`). Anyone crossing 5% files within five
business days and again on every one-point move, so this names an accumulating
holder before the annual report does. Methodology:
`docs/METHODOLOGY-5PCT-FILINGS.md`.

| Endpoint | What it answers |
| --- | --- |
| `/stakes/summary` | coverage, the current groups at or above 5%, and how many reports state an important-proposal act |
| `/stakes/recent` | the tape, most recently filed first; `?activist=true`, `?report_type=`, `?min_ratio=`, `?min_change=` |
| `/stakes/company/{sec_code}` | who has filed 5% on this company — each group's latest report with its members, plus every report on the company |
| `/stakes/holder/{edinet_code}` | one holder's book: its latest position per issuer, and every report |
| `/stakes/holders` | the most active filers, consolidated into groups by default; `?by=entity`, `?filer_type=`, `?group=`, `?activist=true` |
| `/stakes/holder-types` | the filer types and how many filing entities carry each |
| `/stakes/companies?q=` | search scoped to issuers a report names |

Unlike every other extractor here the source is the **t1 inline-XBRL package** —
EDINET publishes no CSV rendition of this form — and the scan is stack-based,
because inline XBRL nests and a regex silently swallows the facts inside a text
block (measured: the holding ratio lost in 36% of filings).

Four things the responses carry because a reader will otherwise get them wrong.
**A report is an event, not a position**: each is a snapshot at its own trigger
date, and a group that falls below 5% files once more and then stops. **The
group is the unit and is not the sum of its members** — the form deducts claims
between joint holders, and a member that has just left is still described with
last-report figures only, so `in_group_total` says who counts. **重要提案行為 is
not asked on every form**: only the general first-schedule form carries the
field, so `proposal_asked` is false on change reports and on the special form,
and a null answer never means "no". **The ratio is the statutory one**, whose
denominator adds the holder's own potential shares, so it does not equal
`shares_held / shares_outstanding`.

**Filers carry two derived labels**, both applied at serve time in
`app/filer_labels.py` (the extractor stores only what the filing says).
`filer_type` is read from the filer's own 事業内容 plus the filed 法人/個人 flag
and types 99.3% of the 1,340 filing entities — no filing states a category such
as "hedge fund" and none is invented. `group` consolidates a family's filing
entities, and it is curated rather than derived because no document names the
parent: BlackRock files under 16 EDINET codes, Fidelity 13, Nomura 8. A group's
issuer count is the distinct companies its entities cover between them, not the
sum of theirs; a joint venture is its own group, never counted inside either
parent.

`filed_date` is EDINET's own submission record rather than the date printed on
the cover page, which the filer types and occasionally gets wrong (`cover_date`
keeps that one); the tape is ordered by it, because a change report routinely
restates the date the holder first crossed 5% years earlier.

The page is `web/stakes.html` (**Equities → 5% Filings**): a market view — the
tape with filters for important proposals, new 5% holders and moves of a point
or more, plus the most active filers — and a company view at `?c={sec_code}`
with each disclosed group, its members and its stated purpose as filed, and a
holder view at `?h={edinet_code}`.

### AGM votes (`/api/v1/equity/agm/...`)

How every resolution at a Japanese shareholder meeting was voted, and — for
board elections — how much support each named director received
(`observatory/equity/agm_extract.py`, parser `agm-1`; see
`docs/METHODOLOGY-AGM-VOTES.md`). **9,690 meetings, 32,204 resolutions and
62,021 individual director results.** It is the only free, public, structured
measure of a named director's mandate in Japan.

| Endpoint | What it answers |
| --- | --- |
| `/agm/summary` | coverage, the distribution of director support, and medians by proposal type |
| `/agm/directors?order=lowest` | named directors ranked by the support they received — the point of the dataset |
| `/agm/proposals?category=` | resolutions by kind; `?shareholder=true` for shareholder proposals |
| `/agm/company/{sec_code}` | one issuer's meetings, each resolution and each candidate underneath it |

Three things the responses carry because a reader will otherwise get them
wrong. **The percentage is the company's and cannot be rebuilt from the counts
beside it**: 95% of these filings disclose that the issuer stopped counting
attending votes once the outcome was settled, so the denominator behind 賛成割合
is never published. It is stored and shown exactly as filed; our own arithmetic
is served separately as `approval_pct_of_counted` and never sits in the same
column. **A board election has no proposal-level vote** — one result per
candidate and no total — so those vote columns are null by structure, not
missing. **Counts are voting rights (個), not shares**, and are not comparable
with the share counts anywhere else in the product.

Rankings show only rows whose filed percentage the platform can reproduce from
the counts printed beside it (`pct_consistent`). That check exists because it
caught a real bug: a minority of filers publish a trailing `(参考) 反対率`
column, and reading it as the approval rate turned Omron's 99.1% into 0.1% and
put it top of "lowest support". Failing rows are kept and returned by
`include_unverified=true`, flagged — never deleted.

Unlike every other extractor here there is almost nothing to read from the
XBRL: type 180 is 98% "XBRL" by EDINET's flag, but the tagged facts are
cover-page boilerplate plus one free-text block. The substance is an HTML table
in the t1 honbun, parsed with the grid machinery from `facility_extract.py`.
That document type is also a grab-bag — mergers, subsidiary changes, officer
changes — so only about 42% of type-180 filings produce rows, and the rest are
recorded as examined-and-not-a-meeting so the count reconciles with the archive.

**Coverage begins in April 2024 and nothing earlier can be recovered.** 臨時報告書
leave EDINET's public inspection window far sooner than annual reports do; the
earliest one still carrying metadata is 2024-04-01, against 2021-08 for annual
reports and 5% filings. History accumulates forward from here.

The page is `web/agm.html` (**Equities → AGM Votes**): the distribution of
director support, a lowest-support league table with thresholds, contested
business (shareholder proposals, takeover defences, dismissals, pay), medians
by proposal type, and a company view at `?company={sec_code}`.

Companies in that dataset are named in Japanese in the filings, but every
annual report states the filer's own English name on its cover page, so both
names are as filed. Every name-bearing response carries the English one
(`name_en`, `filer_name_en`, `held_name_en`, `holder_name_en`) alongside the
Japanese, and `/companies?q=` matches code, Japanese name and English name
alike. EDINET's filer registry is the fallback for a company that files no
annual report of its own. See `docs/METHODOLOGY-CROSS-SHAREHOLDINGS.md` §5.1.

Nobody types the filed name, though: "MUFG" is in neither
株式会社三菱ＵＦＪフィナンシャル・グループ nor "Mitsubishi UFJ Financial Group,
Inc.", so every `/companies?q=` search also matches a market nickname
(`app/aliases.py`). Two layers, both applied at serve time and neither stored:
initials generated from the filed English name (MUFG, SMFG, MHI), and
`app/curation/company_aliases.json` for what initials cannot reach — JAL,
TEPCO, JR East, Uniqlo, Docomo, brands, and operating subsidiaries that do not
list, each row naming the listed filer and the reason it is there. Matching is
exact on the nickname, so an alias only ever ADDS the company it names; it
never reorders or removes anything the name search already found.

Nor does anyone type the filed name the way EDINET filed it. 181 listed filers
carry a space inside their own name (`株式会社　りそなホールディングス`), 543
carry full-width latin (`ＮＴＴ株式会社`), and 株式会社 sits at whichever end
the company chose — so typing a company's own full legal name returned nothing
at all. Both sides of the name comparison are now folded (`aliases.fold`):
NFKC, upper-case, legal form and punctuation removed, and spaces dropped where
they touch a kana or kanji. A space between two latin words survives on
purpose — welding `ＴＯＹＯ　ＴＡＮＳＯ` into one word would answer a search for
Toyota with a graphite maker — so a name typed with no spaces at all is matched
against the whole de-spaced name only, never a fragment of it. As with the
nicknames, the fold contributes only what the plain substring search missed.

Quick check:

```
curl -X POST localhost:8007/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```


## US Treasury curves (`ust-yields`, `ust-real-yields`)

The first non-Japan datasets in the macro core, added 2026-09-15 for market
study rather than for the product. They follow the golden rule exactly: two
adapter modules plus registry entries, no core schema change. A yield curve is
a time series, so unlike the SEC financials it fits `datasets → series →
observations` without argument.

| | `ust-yields` | `ust-real-yields` |
| --- | --- | --- |
| What | Par yields on Treasuries | Real yields on TIPS |
| Maturities | 14, from 1 month to 30 years | 5, from 5 to 30 years |
| History | 1990-01-02, 9,181 business days | 2003-01-02, 5,929 business days |
| Observations | 99,502 | 27,469 |

Subtracting one from the other at the same maturity gives **breakeven
inflation**, the market's own CPI forecast for that horizon. That is a derived
figure: it carries its formula, never an official badge, and is computed at
serve time rather than stored.

### Two things the source forces

**One CSV per year, and each request takes ~19 seconds** regardless of size.
That is server-side and cannot be hurried; the all-years URL answers 403. A
cold fetch is therefore 37 requests and about 12 minutes.

**So closed years are cached.** The Treasury does not revise 1995, so past
years are kept under `data/ust-yields-years/` and only the current year and
the one before it are re-fetched — two requests and about forty seconds on a
normal run. The cache is derived state: delete it and the next run rebuilds
it, which is also how a year would be repaired if the Treasury ever restated
one. `UST_YEARS_REFRESH_ALL=1` forces a full re-download. On Railway the cache
sits on the mounted volume, so it survives redeploys; a fresh volume pays the
cold cost once, behind an already-open port.

The endpoint also stops answering occasionally — a cold run timed out mid-way
on 15 September 2026 and the same URL served fine a minute later — so
`fetch_year_csv` retries four times with a widening pause before giving up.

### Verified end to end (2026-09-15)

- Every maturity for 2026-09-09 reconciles **exactly** against the Treasury's
  own published row.
- **Gaps stay gaps.** The 30-year was suspended in February 2002 and returned
  in February 2006: 994 nulls, **zero** zeros. The 1.5-month is null before
  February 2025, the 4-month before October 2022, the 1-month before July 2001.
- Breakevens reproduce known history: 3.17% at the November 2021 inflation
  scare, 1.46% in August 2020, and **−1.79%** during the November 2008
  deflation panic.
- Both appear in `/api/v1/catalog/datasets`, on the `/curve` endpoint, and in
  the MCP `list_datasets` tool under the Rates section.

A `rates.html` page still shows the JGB curve only. Putting these on a page is
a UI change and belongs with the `ui-ux-design` skill; the data and the API
are done without it.


## How the box answers a question: code first, then the model

Every question to the front-page box (`/api/v1/us/ask`, `app/stock_recipes.answer`) goes down
one of five routes, tried in this order. The admin Traffic page's Ask Box section shows how
many questions took each route and the median wait, so the split can be watched.

| Route | What it is | When it runs | Cost |
| --- | --- | --- | --- |
| Refusals and reasons | Word patterns for advice, forecasts, "why", figures not held, markets not covered, names that are not filers | Before anything else | 0 ms, no model |
| Economy answers (`app/assistant/preview.py`) | A catalogue of US, Japan and Hong Kong series, then the series search, run by code | The question names an economy topic and no company | under 1 s, no model |
| Quick lane (`stock_recipes.quick_match`) | A company (or manager, industry, index) plus a few words that name one shape, read by a fixed grammar | The grammar matches the whole question | under 1 s, no model |
| Reader (`app/us_ask_model.py`) | One short model call fills a form (shape, companies, period, ranking); code runs the shape | The quick lane did not match | 3–8 s, one call, cached per question per day |
| Steps (`app/us_steps.py`) | The model writes the steps and one SQL query over the sandbox tables; code runs and checks it | The reader names no shape: counts, shares, screens with several conditions, custom maths | 10–30 s, two to four calls, plan cached per question per day |

Rules of thumb when adding something:

- A question people ask in the same words every day (a figure, an owner, a date, a ranking)
  belongs in the quick lane: add a grammar line to `QUICK_SHAPES` and, if needed, a recipe. It is
  the only route that is instant, deterministic and free.
- A question with many phrasings but one shape belongs with the reader: improve `SHAPE_HINTS`.
- A question that genuinely needs arithmetic across companies belongs with the steps route: add a
  rule to `app/us_steps_rules.txt` or a worked example to `app/us_steps_examples.json`. Its answers
  vary run to run, so never leave a common question there.
- A question the data cannot answer gets a plain reason, never a guess (`app/ask_miss.py`).

Caching, in layers: the provider's prompt cache (every model call carries a `prompt_cache_key`
and puts the unchanging part of the prompt first, so the long prefix is read from cache and
counted in `tokens_cached` on `/api/v1/us/ask/recipes`); the reader's form, the step plan and the
review verdict, each cached per question per day in memory; the metrics rows, name indexes and
sandbox tables, built once per database version and rebuilt in a background thread
(`stock_recipes.warm_forever`); and the response cache (`app/cache.py`), which stores every
`/api/v1` GET body, the box and the front page's preview included, keyed by path and query and
stamped with the three database versions, so a repeat question costs one dictionary lookup
until the nightly refresh.
