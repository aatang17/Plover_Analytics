# PLAN — Equity Prototype: the datasets to build next, and the page that shows them

> **Status:** PROPOSAL v2 — plan only, awaiting go-ahead. Written 2026-09-03 for an
> Opus build session; v2 adds the three target users (§0.5), the key-indicators panel
> (candidate J), market aggregates as series, and a post-prototype quant phase (P6).
> Every source fact below was measured against the archive, the bucket or the live
> schema on that date; nothing is assumed from memory.
>
> **One-liner:** In about four and a half working weeks, turn the six equity datasets we
> already serve into a prototype an investor can click through in five minutes — one page
> per company that assembles everything we know, a ten-year panel of the filer's own key
> indicators, an events record behind it, and three new datasets (AGM votes, tender
> offers, the TDnet tape) that show the platform *watching* Japan, not just describing it.
> Built so that fundamental analysts and activists can use it on day one and quants can
> use it without anything being rebuilt.
>
> **Companion docs:** [PLAN-PLATFORM-VISION.md](PLAN-PLATFORM-VISION.md) (the five-layer
> vision this executes) · [PLAN-API-MCP-V2.md](PLAN-API-MCP-V2.md) (the composed company
> endpoint — reused here, not rebuilt) · [PLAN-CROSS-SHAREHOLDING-DB.md](PLAN-CROSS-SHAREHOLDING-DB.md)
> · [PLAN-BOARD-AND-PAY.md](PLAN-BOARD-AND-PAY.md) · [../SYLLABUS-JAPAN-EQUITIES.md](../SYLLABUS-JAPAN-EQUITIES.md)
> · [../../equity/README.md](../../equity/README.md) (extractor conventions every new
> extractor copies)

---

## 0. Interpretation

"Investors can see a prototype" is read as: **a person with money — a fund evaluating
the product, or someone evaluating the business — opens a link and, without a guide,
understands in five minutes what the platform does that nothing else does.** That
audience does not read API docs. It reads one company page, one screen, one event feed,
and one AI conversation. So the prototype is judged on those four surfaces, and the
datasets chosen below are the ones that make those surfaces convincing.

Two things are deliberately **not** in scope and are called out in §8 as decisions:
a licensed price feed (§3, rejected for the prototype on licensing evidence) and user
accounts / watchlists (the "Watch" layer's retention feature — the prototype shows the
*feed*; saving it needs accounts, which is a separate plan).

## 0.5 Who it is for — three users, one platform

Three users, in the order they can use the product. Each row names the job, what that
job needs from us, and which phase delivers it. The datasets are shared; the *surfaces*
differ.

| User | The job | What they need from us | Surfaces | Delivered by |
| --- | --- | --- | --- | --- |
| **1. Fundamental analyst** (sell-side and long-only buy-side) | Build a view on *one company*: is the balance sheet, board, ownership and capital allocation what management says it is? | The company page as a briefing book · **ten years of the filer's own key indicators** (revenue, profit, ROE, equity ratio, cash, dividends, payout, PER) · who holds it, what it holds, the board, pay, buybacks announced vs bought, land at cost · **peers in the same 33-industry** for every figure · CSV/PNG with the filing behind every number | `company.html`, dataset pages, exports, cite URLs | **P0–P2 + J** |
| **2. Activist / event-driven** (and the macro reader who trades the governance story) | Find targets, monitor situations, read the tape | The 5% tape with the important-proposal flag, five years deep · AGM votes: director support, shareholder proposals, failed items · cross-holding unwind pace and the register (nominee-excluded — who to canvass) · unspent buybacks · deals: price, stated premium, opinion, outcome · a daily events feed · **a campaign tracker**: filing → proposal → vote → outcome · **market aggregates as time series** (unwind pace, buybacks per month, activist filings per month, foreign ownership) chartable beside CPI and the BOJ | `tape.html`, `agm.html`, `deals.html`, `screens.html`, `stakes.html`, series endpoints, MCP | **P2–P5** |
| **3. Quant** (eventually) | Pull panels, not pages | Tables keyed by (entity, period, filed date, document) with **restatements kept as separate rows** · `as_of` on every read · bulk CSV/Parquet per table · a data dictionary with unit and trust per column · stable identifiers (5-digit code, EDINET code, corporate number) · API keys and limits · no UI required | `/api/v1/equity/bulk/`, `/catalog/dictionary`, keyed API, MCP | **P6 (after the prototype)** — but the *rules* in §5.0 bind from P0 so nothing is rebuilt |

Two consequences for the build:

- **Nothing is aggregated away.** Every extractor keeps one row per (document, period),
  which is what a fundamental analyst wants (the restated figure beside the original) and
  what a quant needs (a point-in-time panel). The API serves the latest filing by default
  and exposes the rest.
- **Every screen is also a series.** A ranking a PM reads is a cross-section; the same
  aggregate over time is a macro series. P5 exposes the aggregates through the same
  series shape the macro side already uses, so the governance trade can be charted
  like a macro variable.

## 1. What the investor sees — the five-minute demo

The prototype is done when this script works, unscripted, on production. One moment per
user type is marked.

1. **Open a company page** (`company.html?c=7203`, Toyota). One screen: **ten years of key
   indicators as the company itself filed them** (analyst moment), what it holds and who
   holds it, the board and what it pays, buybacks announced vs bought, 5% filers, plants and
   land — and a **timeline** down the side: AGM results, major-shareholder changes, buyback
   resolutions, forecast revisions, filed this year. Every number links to its filing; every
   figure shows the industry median beside it.
2. **Click one AGM line.** See every proposal at the last AGM with for/against/abstain
   and the approval rate, exactly as filed; the director who scraped through at 71%.
3. **Open a screen** (activist moment). "Prime companies whose policy holdings exceed 20%
   of equity, ROE under 8% for three years, and any director under 80% support" — the join
   nobody else can run, ranked, exportable, citable URL. Then the **campaign tracker**: one
   2024–2026 situation from the 5% filing through the shareholder proposal to the vote.
4. **Open Deals.** Every tender offer and MBO since 2021: offeror, target, price, the
   premium the filer stated, the target board's opinion, whether it succeeded.
5. **Ask Claude** (connected to `/mcp`): "What happened at Toyota's last AGM, and who has
   filed 5% on it since?" — an answer grounded in tool calls, each with a cite link
   that loads. Then, for the quant in the room: open the data dictionary and download one
   table as CSV (quant moment — the bulk endpoint proper is P6).

**Exit test** (from the vision plan, unchanged): *a PM can read one page on any listed
company and know its governance posture without asking anyone.*

## 2. Where we stand (measured 2026-09-03, local `data/equity.duckdb`)

| Dataset | Rows | Coverage today | Gap to full depth |
| --- | --- | --- | --- |
| Cross-shareholdings | 21,105 filings · 204,472 holdings (94% entity-matched) | FY2021–FY2026, five years, all filers | none — full S3 run done |
| Boards & pay | 21,099 filings · 195,097 seats · 5,605 named ¥100m+ | five years, 99.4% clean among listed | none |
| Shareholder register | 2,503 filings · 24,353 holders | **one year** (local archive only, periods ending 2026) | four more years sit in the bucket, extractor takes `--source s3` unchanged |
| 5% filings | 3,893 reports · 8,287 holder rows | **ten weeks** (filed 2026-05-29 → 08-06) | **~74,000 reports back to 2021-08** in the bucket (350: 64,708 · 360: 9,477). Five years of activist tape, zero new code |
| Buybacks | 6,236 filings · 1,765 authorisations | 2025-08-12 → 2026-08-21 | none possible — EDINET purges type 220 after ~12 months |
| Facilities & land | 2,243 filings · 27,244 sites (69% geocoded) | **one year** | four more years in the bucket |

Everything above is served, paged and reachable over MCP. What is missing for the demo is
(a) depth on three of the six, (b) the one-page assembly, (c) any *financial* context —
the platform can say who sits on Toyota's board but not what Toyota earned — and (d) any
notion of *events*: today the platform describes a company as of its last annual report
and says nothing about what happened since.

## 3. Candidates, ranked — with the evidence

The bucket holds far more than we parse. Each candidate below was checked against real
documents on 2026-09-03 (tags counted from the inline XBRL or the t5 CSV, volumes from the
1,316 daily indexes in `equity/data/raw/edinet/lists/`).

| # | Candidate | Source · volume in the archive | What is tagged (verified) | Value to the prototype | Effort | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| A | **Full-history runs** of register, facilities, 5% filings | bucket; 5 FY / 74k reports | already parsed by existing extractors | Turns "ten weeks of 5% filings" into "every activist campaign since 2021" | machine hours, ~½ day of code | **Build first (P0)** |
| **J** | **Key indicators panel** — 主要な経営指標等の推移 | the same annual-report t5 CSV the holdings and board extractors already open; **21,105 filings × 5 fiscal years each** → a ten-year annual panel (FY2016–FY2026) for every filer | **Fully tagged** (verified on S100Y1I3, 2026-06-22): 22 `…SummaryOfBusinessResults` elements per filing across `Prior4Year…CurrentYear` contexts — net sales, ordinary income, profit attributable to owners, comprehensive income, net assets, total assets, **BPS, EPS (basic, diluted), equity ratio, ROE, PER as filed**, operating / investing / financing cash flow, cash, capital stock, issued shares, DPS, interim DPS, payout ratio; parent-only and consolidated contexts separately; IFRS / US GAAP variants exist and `extract.py` already handles them for equity and assets | **Highest for users 1 and 3.** The analyst's first table; the quant's first panel; and the filer's own PER gives a year-end valuation without a price licence. Stays inside the "no financials terminal" line: it is the filer's own twenty-line summary, not the statements | 2 days | **Build (P1)** |
| B | **Corporate events + AGM votes** — 臨時報告書 (types 180/190) | **33,387 reports** in the index from **2024-04-01** (the earliest the index serves; nothing older is retrievable) + 1,328 amendments | Taxonomy `jpcrp-esr_cor`. **The event type is the tag name** of the text block present — no classifier needed. In a 160-report random sample of listed filers: 54% AGM/EGM voting results, 9% stock-option issues, 8% major-shareholder changes, 6% representative-director changes, 6% parent/subsidiary changes, 14% "significant events" (impairments, lawsuits, disasters), plus mergers, share exchanges, splits, squeeze-outs, covenant breaches. AGM results are an HTML table inside the text block (proposal · for · against · abstain · requirement · result · approval %) | **Highest for user 2.** The timeline on every company page; director support rates; shareholder proposals and their votes; who just became a major holder. Nothing in English carries this | 4–5 days | **Build (P2)** |
| C | **Tender offers & MBOs** — 240/250/270/290/300 | **593 offers** 2021-08 → 2026-08 (37 · 77 · 100 · 129 · 168 · 82 by year) + 586 result reports + 462 target opinions + 749 amendments | Taxonomy `jptoo-ton_cor` (offer) / `jptoo-tor_cor` (result). Mostly text blocks with **regular table shapes**: `PriceOfPurchaseEtcTextBlock` ("普通株式１株につき、金2,451円"), `PeriodOfPurchaseEtcTextBlock`, `NumberOfShareCertificatesEtcIntendedToPurchaseTextBlock` (planned / minimum / maximum), `PurposesOfPurchaseEtcTextBlock`; **`HoldingRatioOfShareCertificatesEtcAfterPurchaseEtc` is a tagged number**; `InformationAboutStockPricesTextBlock` gives six months of monthly high/low **as filed** | High per item, small volume, and the only place the prototype can show a *price* legitimately (the filer's own stated premium and monthly high/low) | 3–4 days | **Build (P3)** |
| D | **TDnet tape + earnings summaries** | 8,617 disclosures over the 22 days held locally (~390/day; bucket since 2026-07-13); 2,536 carry an XBRL zip | Earnings-release summary is fully tagged (`tse-ed-t`: NetSales, OperatingIncome, ProfitAttributableToOwnersOfParent, forecasts, **DividendPerShare ×16 contexts**, shares outstanding and treasury). Everything else is PDF + title. Title regex over the 22 days: 2,475 earnings · 599 buyback resolutions · 479 forecast revisions · 383 dividend notices · 116 third-party allotments · 96 cancellations · 51 TOB notices · 33 delistings · 30 major-holder changes | The "watching" demo: today's disclosures by company, classified; buyback *resolutions* (the missing front end of the buyback lifecycle); forecast revisions; the quarterly bridge between annual key-indicator rows. History is short (seven weeks) and the tape says so | 3 days | **Build (P4)** |
| E | **Universe master** — JPX listed-issues file (`data_j.xlsx`, monthly) + corporate numbers from the EDINET code list | one file; link verified on jpx.co.jp 2026-09-03 | spreadsheet: code, name, market segment (Prime/Standard/Growth), 33- and 17-industry codes, size band; the EDINET code list carries 法人番号 | Needed for "Prime only" screens, **peer medians by industry** (user 1) and honest coverage denominators; corporate number is the join key quants ask for first | ¼ day | **Build (P0)** |
| F | **Prices / market cap** | J-Quants (JPX) or a vendor | n/a | Unlocks live PBR, % of market cap, buyback yield | — | **Not in the prototype — licensing (see below)** |
| G | Full financial statements from 有報 | t5 CSV (facts) + t1 presentation linkbase (statement order), fully tagged | yes | **Decision reversed 2026-09-03:** the destination is full statements, on-demand models and an MCP that answers from them. Built as one long facts table (`eq_fin_facts`) with the filer's own presentation order (`eq_fin_lines`); J is a view over it, not a separate extractor. First cut: `equity/fin_extract.py`, `/api/v1/equity/financials/`, `financials.html`, three MCP tools; 2026-09-04: platform-calculated ratios (`app/fin_metrics.py`: ROE/ROA on average balances, margins, growth, FCF, implied PBR/yield) and `screener.html` with two more MCP tools | 1½ days (done) | **Built (P1, supersedes J)** |
| H | Capital raises (030/040), stock-option issues | 3,300 filings | tagged cover, text bodies | Red-flag value (dilution) but niche; the *event* is already captured by B's classification | — | Later; B carries the flag |
| I | English translation of purpose text | LLM | — | Nice on the company page; a translation error is a credibility event | — | Later, behind a label |

**Why prices are out.** J-Quants Pro's own FAQ describes it as "a service for internal use
for institutional operations" whose terms prohibit "distributing or disclosing this
information to third parties", with external distribution "permitted for some datasets"
only under a separate pricing table. The retail J-Quants API is marketed to individual
investors and its plan page refused automated reading (HTTP 403), so its terms are
unverified. Neither is safe to build a public screen on without a written licence. The
prototype therefore uses **book equity** for every ratio, plus the **filer's own PER** from
J for year-end valuation (`implied_year_end_price = PER_filed × EPS_filed`,
`PBR_implied = implied price ÷ BPS_filed` — derived, formula shown, labelled "implied
from the filer's own PER at fiscal year end; not a market price"). Decision for you in §8.

**Why J then B then C then D.** J is two days, reuses an open file, and is the first thing
a fundamental analyst looks for. B is the largest volume, the cheapest classification (the
tag *is* the label), and feeds the company-page timeline the demo opens with. C is small
and high-value but stands alone. D has seven weeks of history and grows daily; it is the
"live" proof, so it goes last but ships inside the prototype.

## 4. Build order

Phases are sequential for one builder; P0 runs on machine time underneath everything.
Each phase leaves production demonstrable on its own. P6 is after the prototype and is
listed so that nothing built before it has to change.

| Phase | Deliverable | Depends on | Effort | What the investor can see after it | Serves |
| --- | --- | --- | --- | --- | --- |
| **P0** | Full-history S3 runs (register, facilities, 5% filings 2021→); universe master + corporate numbers; production re-seed | nothing | ½ day code + ~1 day machine | Five years of activist filings; Prime/Standard/Growth and industry on every screen | 1 · 2 |
| **P1** | **Company page** `company.html?c=` on a composed `GET /api/v1/company/{code}` (PLAN-API-MCP-V2 M1+M3+M6, prototype cut) **with the key-indicators panel (J) and industry peer medians** | P0 | 3–4 days + 2 days (J) | The dossier: ten years of the filer's numbers and six datasets on one page, one URL per company | 1 |
| **P2** | **Events + AGM votes** (臨時報告書): `eq_events`, `eq_agm_*`, `eq_major_holder_changes`; `/equity/events/`, `/equity/agm/`; page `agm.html`; timeline block on the company page | P1 | 4–5 days | AGM support rates, shareholder proposals, the timeline | 2 · 1 |
| **P3** | **Deals** (tender offers/MBOs): `eq_tob_*`; `/equity/deals/`; page `deals.html`; deal block on company page | P1 | 3–4 days | Every TOB since 2021 with price, stated premium, opinion, outcome | 2 |
| **P4** | **Tape** (TDnet): `eq_tdnet_*`; `/equity/tape/`; page `tape.html`; "since the annual report" block on the company page | P1 | 3 days | Today's disclosures, classified; buyback resolutions; forecast revisions; latest earnings summary | 2 · 1 |
| **P5** | Cross-dataset screens + campaign tracker, **market aggregates as series**, coverage page, data dictionary, MCP tools for P1–P4, demo rehearsal, production seed | P2–P4 | 3 days | The §1 script end to end | 2 · 1 · 3 |
| **P6** (after) | **Quant access**: bulk CSV/Parquet per table, `as_of` ceilings, API keys and limits, OpenAPI from manifests | P5 | ~1½ weeks | A quant pulls the whole panel with one key and reproduces any page | 3 |

Total for the prototype: roughly **four and a half working weeks**. P2–P4 can be
reordered without breaking anything; P1 must precede them because each adds a block to
the page.

## 5. Specifications

### 5.0 Cross-cutting rules (Opus: read before touching anything)

- **Namespace.** Everything lands in the equity DuckDB (`data/equity.duckdb`), tables
  prefixed `eq_`, API under `/api/v1/equity/...`, routers registered before the core
  router in `app/main.py`. The macro golden rule does not apply; the macro core is untouched.
- **Extractor conventions** are those of `equity/README.md`: `--source local|s3`,
  `--workers N`, `--docs ID,ID` for re-running a subset, parser version string on every
  row, SHA-256 of the bytes actually parsed, `status ∈ {clean, partial, failed, …}` with a
  `detail` reason, and a **gate that recomputes the filer's own totals** wherever the
  document prints one. `lsof -ti:8007 | xargs kill` before any local run — DuckDB treats
  the API's reader as a lock.
- **The archive is the vintage; the DuckDB is a rebuildable view.** A 訂正報告書 is a new
  row linked to the original by `amends_doc_id`, never an overwrite. Nothing back-fills.
- **Quant-ready by construction (new in v2).** Every new table carries `doc_id`,
  `edinet_code`, `sec_code`, `period_end` (or the event date) and `filed_date`; units live
  in column names (`_yen`, `_pct`, `_m2`, `_shares`); enumerations are documented in the
  module docstring and the manifest; one row per (document, period) — **a later filing
  that restates an earlier year is a new row, never a replacement**; readers default to
  the latest filing and accept `as_filed_in=` / `as_of=` to pick another. No value is ever
  overwritten; corrections are rows.
- **Trust contract.** As-filed values are official. Anything we compute (an approval rate
  recomputed from counts, an implied PBR, a peer median, a category we assign to a title)
  is derived and carries its formula in the API `calc` block, the page's "Show
  calculation", and the CSV header. Missing is `—`, never 0.
- **One filing per company** on every cross-section; history endpoints for the series.
- **Entity resolution** reuses `eq_entities` and the existing name-matching helpers
  (`rematch.py`, `filer_labels.py`). A natural person is never entity-matched.
- **Python 3.9**, stdlib + duckdb + boto3 (lazy) only. No new runtime dependency, no CDN,
  no build step; ECharts is vendored.
- **UI** goes through the `ui-ux-design` skill and its look-at-it gate (1440 light/dark,
  true-390). Every chart: source line, PNG, CSV, URL-encoded view state.
- **No prices, no recommendations, no ratings.** Screens rank facts.
- **Commit and push only when asked.**

### 5.1 P0 — depth and universe

**Full-history runs** (no code changes expected; budget for parser fixes the new years
expose):

```bash
set -a; . ~/.edinet-s3.env; set +a          # EDINET_S3_* (see equity/README.md)
lsof -ti:8007 | xargs kill
../observatory/.venv/bin/python lvh_extract.py        --source s3 --workers 16   # ~74k reports
../observatory/.venv/bin/python ownership_extract.py  --all --source s3 --workers 16
../observatory/.venv/bin/python facility_extract.py   --source s3 --workers 12
```

Acceptance: `/equity/stakes/summary` reports filings from 2021-08; every named campaign in
the syllabus (Elliott, Oasis, 3D, Effissimo, Strategic Capital, Dalton, Murakami-related
filers) has a multi-year holder page; register and facilities `/years` list five fiscal
years; the fac-6 clean rate on the older years is reported, not assumed. Any parser fix is
a new parser version and a full re-run, never a patch to stored rows.

**Universe master** — new extractor `equity/universe.py`:

- Source: `https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx`
  (link present on the JPX "listed issues" page on 2026-09-03; the page itself 403s
  non-browser fetches, so download with a browser User-Agent and archive the file with its
  SHA-256 under `data/raw/jpx/`). Monthly; each download is a dated vintage.
- Table `eq_universe(as_of DATE, sec_code, name_ja, market_segment, industry33_code,
  industry33_ja, industry17_code, industry17_ja, size_code, size_ja, sha256)`. Verify the
  column headings on first download; do not hard-code positions.
- **Corporate number.** Refresh `eq_entities` from the EDINET code list
  (`EdinetcodeDlInfo.csv`, fixed URL in the API spec) and add `corporate_number`
  (法人番号) — the identifier quants and compliance teams join on. Additive column.
- API: `?segment=prime|standard|growth` and `?industry=` on every equity screen endpoint;
  `coverage` blocks state "N of M Prime filers". Company responses gain `segment`,
  `industry33` and `corporate_number`.
- Credit line: JPX, with the file date.

### 5.2 P1 — the company page, with the key-indicators panel

Build the prototype cut of PLAN-API-MCP-V2, not a hand-written page:

- **M1 (manifests, equity only).** `MANIFEST` in each of `equity_api`, `ownership_api`,
  `lvh_api`, `governance_api`, `buyback_api`, `facility_api`, the new `indicators_api`,
  and later `events_api`, `deals_api`, `tape_api`; `app/registry.py` validates them. Macro
  manifests, the v2 tool set and API keys are **out** of the prototype.
- **M3 (composed endpoint).** `GET /api/v1/company/{code}` assembling every dataset's
  company block in a fixed order (indicators first), each block independently try/except
  (a dataset with no rows reports under `coverage.missing`, never 500s), cached per code
  and warmed for the 100 largest policy holders at boot. `GET /api/v1/company/{code}/coverage`
  alongside. **Every numeric fact carries the industry median** for the same fiscal year
  (`peer` block: value, median, rank, N, industry — all derived, formula stated).
- **M6 (page).** `web/company.html` + `assets/company.js`: header (name EN/JA, code,
  segment, industry, corporate number, latest annual report and its hash), fact strip,
  **the key-indicators table first** (ten fiscal years × the filed lines, with a
  "restated" marker where a later filing changed a prior year), then one section per
  dataset with its chart or table and a link to the full dataset page, then the
  **timeline** (empty until P2, rendered as "no events extracted yet" — never hidden).
  Search box resolves through the existing `/equity/companies?q=`.
- Every dataset page's company view links to `company.html?c=`; nav gets "Company".
- One new MCP tool `get_company_profile(code, sections?)` over the composed endpoint,
  compact mode (facts only, ~2k tokens). Contract test: a code with no annual report, a
  delisted code, and a 5%-only issuer all return the envelope, not an error.

**Key indicators (J)** — new extractor `equity/indicators_extract.py` (parser `ki-1`),
reading the same `jpcrp030000-asr` CSV the holdings extractor opens:

- Table `eq_key_indicators(doc_id, edinet_code, sec_code, filed_date, fiscal_year_end
  DATE, year_offset INTEGER /* 0 current … -4 */, basis /* consolidated | parent */,
  accounting_standard /* jgaap | ifrs | usgaap */, net_sales_yen, ordinary_income_yen,
  profit_owners_yen, comprehensive_income_yen, net_assets_yen, total_assets_yen,
  bps_yen, eps_basic_yen, eps_diluted_yen, equity_ratio_pct, roe_pct, per_filed,
  cf_operating_yen, cf_investing_yen, cf_financing_yen, cash_yen, capital_stock_yen,
  issued_shares, dps_yen, dps_interim_yen, payout_ratio_pct, sha256, parser_version,
  status, detail)`. One row per (filing, fiscal year, basis): **five rows per basis per
  filing**, so FY2023 appears in the FY2023, FY2024, FY2025 and FY2026 reports — that is
  the restatement panel, kept.
- Element map: `…SummaryOfBusinessResults` with IFRS / US GAAP twins (`NetSalesIFRS…`,
  `RevenueIFRS…`, `ProfitLossAttributableToOwnersOfParentIFRS…`, `TotalEquityIFRS…`,
  etc.) — extend the variant lists already in `extract.py` rather than duplicating them.
  Ratios arrive as fractions (0.716) and are stored in percent (71.6) — the same
  weights-scale trap as `weight_per_10000`, so test it. "－" is null. PER is null when the
  filer prints "－" (loss years).
- Gate: `net_assets_yen ≤ total_assets_yen`; `equity_ratio_pct` recomputed from the
  balance-sheet lines within 1 pp where both exist (basis-aware: IFRS filers print equity
  attributable to owners); `eps_basic_yen × issued_shares` within an order of magnitude
  of profit (share-count scale check). Status `partial` with reason on any miss.
- API `app/indicators_api.py`, `/api/v1/equity/indicators`: `/company/{code}` (the
  ten-year panel from the latest filing, `?as_filed_in=YYYY` for a vintage, `?basis=`),
  `/screen?metric=roe|equity_ratio|payout|pbr_implied|cash_to_assets&year=&segment=&industry=`
  (`/screen/metrics` lists them), `/peers/{code}` (industry medians per metric per year),
  `/years`. `calc`: `pbr_implied = (per_filed × eps_basic_yen) ÷ bps_yen`;
  `cash_to_assets_pct = cash_yen ÷ total_assets_yen × 100`; `roe_3y_avg`.
- MCP: `get_key_indicators(code, years?)`, `get_indicator_screen(metric, year?, segment?, industry?, limit?)`.
- Methodology `docs/METHODOLOGY-KEY-INDICATORS.md`: the table is the filer's own summary,
  restatements are kept as rows, PER is the filer's figure at fiscal year end, implied
  PBR is derived and not a market ratio.

Acceptance: Toyota, MUFG, Nintendo, SoftBank Group and one Growth-market name render all
present blocks with the same numbers as the dataset pages; Toyota's ten-year panel
reconciles line by line to its two most recent annual reports; response < 300 ms warm.

### 5.3 P2 — corporate events and AGM votes (臨時報告書)

**Source facts.** EDINET types 180 (report) and 190 (amendment). The daily index serves
them from **2024-04-01**; 33,387 reports and 1,328 amendments to 2026-08-06; all in the
bucket as `docs/{date}/{docID}_t1.zip` (inline XBRL, taxonomy `jpcrp-esr_cor`). Every
report carries `ReasonForFilingTextBlock` plus one or more **content blocks whose tag
names the event**:

| Tag (…TextBlock) | Event | Sample share |
| --- | --- | --- |
| `ResolutionOfShareholdersMeeting` | AGM / EGM voting results | 54% |
| `IssueOfStockOptionsNotSubjectToSecuritiesRegistration` | stock-option grant | 9% |
| `EventWithSignificantEffectsOnFinancialPosition…` (+ `…OfGroup`) | impairment, lawsuit, disaster, restructuring | 14% |
| `ChangesInMajorShareholder` | a holder crossed or left 10% (主要株主の異動) | 8% |
| `ChangesInRepresentativeDirectors` | CEO / representative change | 6% |
| `ChangesInParentCompaniesOrSpecifiedSubsidiaries` | control / group change | 6% |
| `DecisionOnAbsorptionTypeMerger` · `DecisionOnShareExchange` · `DecisionOnAbsorptionTypeSplit` · `DecisionOnAcquisitionOfSubsidiary` | M&A resolutions | ~3% |
| `NotificationOfRequestForSaleOfSharesFromSpecialControllingShareholders…` | squeeze-out at 90% | <1% |
| `DecisionOnHoldingShareholdersMeetingForPurposeOfReverseStockSplit` | squeeze-out via consolidation | 1% |
| `FinancialCovenants` · `ModificationOfFinancialCovenants` (+ subsidiaries) | covenant breach / waiver | 1% |
| `ChangeInIndependentAuditors` · `CorporateShareholderGovernanceAgreement` · `PublicOfferingOrSecondaryDistributionOfSecuritiesOutsideJapan` | other | ~2% |

**Extractor** `equity/events_extract.py` (parser `evt-1`), two levels:

1. **Every report → one `eq_events` row** with the event types present (a report can
   carry several), the filer, `filed_date`, `reason_ja` (the filing's own stated reason,
   as filed), `amends_doc_id` for type 190, SHA-256, status. This level alone gives the
   company timeline and needs no table parsing. Coverage gate: every listed-company 180 in
   the index has a row (status `unparsed` is a valid state for event types with no
   table parser yet; it is a row, not a gap).
2. **Table parsers, in this order:**
   - **AGM results** → `eq_agm(doc_id, meeting_date, meeting_kind, proposals, shareholder_proposals, counts_omitted, counts_omitted_reason_ja, status)` and
     `eq_agm_proposals(doc_id, proposal_no, sub_no, title_ja, is_shareholder_proposal, candidate_name_ja, votes_for, votes_against, votes_abstain, requirement_ja, result_ja, approval_pct_filed, approval_pct_calc, basis_differs)`.
     Verified table shape (宮越ホールディングス): header `決議事項 | 賛成数(個) | 反対数(個) | 棄権数(個) | 可決要件 | 決議の結果及び賛成割合(％)`; a director-election proposal is a header row with empty cells followed by **one row per candidate**; `―` means not applicable; the result cell may hold "可決" and the percentage together or split across two cells; footnote markers like "(注)１" sit inside cells.
     Traps to design for: filers may lawfully omit counts when the result was decided by advance votes and say so in a note — record the reason, store nothing as zero; shareholder proposals are numbered in sequence with the company's and identified by "株主提案" in the title; votes are in **units of voting rights (個)**, not shares; some filers print 賛成割合 on a different denominator (exercised votes vs. all votes), so `approval_pct_calc = votes_for / (votes_for + votes_against + votes_abstain) × 100` is derived, `approval_pct_filed` stays the headline, and `basis_differs` marks any gap over 0.5 pp. Gate: every proposal heading has a row; every result is one of the filer's result vocabulary (可決/否決/承認/…); numbers parse or are null with a reason. Candidate names join to `eq_board.person_key` within the same filer where they match, so a director's support rate sits on the board table.
   - **Major-shareholder changes** → `eq_major_holder_changes(doc_id, holder_name_raw, holder_edinet_code, match_status, direction, before_shares, before_pct, after_shares, after_pct, change_date, reason_ja)`. Direction `became | ceased`. Entity-match with the ownership rules (never a natural person).
   - **Representative-director changes** → `eq_rep_director_changes(doc_id, name_ja, from_title, to_title, effective_date, reason_ja)`; joins to `eq_board.person_key` where the name matches within the same filer.
   - M&A decisions, squeeze-outs and covenants: level-1 rows only in P2; their tables are a
     follow-on.

**API** `app/events_api.py`, prefix `/api/v1/equity/events`:

| Endpoint | Returns |
| --- | --- |
| `/summary` | coverage (index reaches 2024-04-01; N reports; N parsed to tables), counts by event type and by month |
| `/recent?type=&segment=&q=` | the tape, newest first, with `cite` per row |
| `/company/{sec_code}` | that company's events, newest first — the page timeline |
| `/agm/summary` | AGMs covered; distribution of approval rates; shareholder proposals count and pass rate |
| `/agm/company/{sec_code}` | every AGM with every proposal |
| `/agm/screen?metric=lowest_director_support|shareholder_proposals|failed_proposals&year=&segment=&industry=` | ranked cross-sections; `/agm/screen/metrics` lists them |
| `/holder-changes/recent` · `/holder-changes/company/{code}` | the 10% crossings |

**Page** `web/agm.html` ("AGM Votes"): market view — approval-rate distribution for
director elections this season, the lowest-support directors (name, company, %, as filed),
shareholder proposals and their votes; company view at `?c=`. The events tape itself lives
on the company page and on `tape.html` (P4) filtered to source=EDINET.

**MCP:** `get_company_events(code, type?)`, `get_agm_results(code, year?)`,
`get_agm_screen(metric, year?, segment?)`.

**Methodology** `docs/METHODOLOGY-EVENTS.md`: the index horizon (2024-04-01), the two
denominators for approval rates, what "counts omitted" means, that an event type is the
filer's own choice of form section.

### 5.4 P3 — tender offers and MBOs

**Source facts.** 240 公開買付届出書 (593 since 2021-08) · 250 amendment (487: price bumps,
extensions) · 270 公開買付報告書 result (586) · 290 意見表明報告書 target opinion (462) · 300
its amendment (262). Verified on S100YT8Y (キーウェスト・ネットワーク → Future Corp, MBO,
¥2,451, 30 business days): the offer document tags the cover page and the **holding ratio
after purchase as a number**; price, period, planned/min/max shares, purpose, funding and
the six-month monthly high/low are text blocks whose inner tables are regular. The 270 and
290 taxonomies (`jptoo-tor_cor`, opinion form) were only cover-checked — **M0 for this
phase is to open five of each and list the tags before writing the parser.**

**Extractor** `equity/tob_extract.py` (parser `tob-1`) → tables:

- `eq_tob(doc_id, doc_type, amends_doc_id, offeror_name_raw, offeror_edinet_code, target_sec_code, target_edinet_code, match_status, filed_date, purpose_kind, price_yen, price_text_raw, period_start, period_end, business_days_filed, planned_shares, min_shares, max_shares, holding_ratio_after_pct, stated_premium_pct, stated_premium_reference_ja, funding_total_yen, sha256, parser_version, status, detail)`
  - `purpose_kind` derived by keyword from the purpose block: `mbo | take_private | make_subsidiary | make_wholly_owned | self_tender | partial | other`, always with the raw purpose text alongside. Self-tender = offeror is the target.
  - `stated_premium_pct` is **the filer's own sentence** ("…に対して30.25％のプレミアム…"), captured with the reference it names (終値 / 1-month average …). Null when the filer states none. Never recomputed from the monthly range.
  - Gate: `planned_shares ≥ min_shares`; `period_end ≥ period_start`; price parses to an integer yen; holding-ratio number equals the percentage in the text block where both exist.
- `eq_tob_prices(doc_id, month, high_yen, low_yen, exchange_ja)` — the filed monthly range, official as filed, the only price series on the platform.
- `eq_tob_result(doc_id, offer_doc_id, tendered_shares, purchased_shares, succeeded, settlement_date)` from 270.
- `eq_tob_opinion(doc_id, offer_doc_id, target_sec_code, opinion_kind, opinion_text_raw, special_committee, fairness_opinion)` from 290 — `opinion_kind ∈ {support_recommend, support_neutral, oppose, reserve, neutral}` by keyword over the opinion block, raw text always beside it.
- Linkage 240 ↔ 270 ↔ 290 by (target, offeror, period) — state the rule in methodology; unlinked rows are rows, not gaps.
- **Cross-reference for user 1:** the deal detail shows the target's key-indicator panel
  (J) beside the offer price, so an analyst reads the price against the filer's own BPS
  and EPS — `price ÷ bps_yen` and `price ÷ eps_basic_yen` derived, formula shown.

**API** `app/deals_api.py`, `/api/v1/equity/deals`: `/summary` (count by year, by purpose
kind, success rate, median stated premium where stated), `/recent`, `/list?year=&kind=&q=`,
`/deal/{doc_id}` (offer + amendments + opinion + result + monthly range + target panel), `/company/{code}`
(as target and as offeror), `/companies?q=`.

**Page** `web/deals.html` ("Deals"): a table-first page — year filter, kind filter, columns
offeror · target · kind · price · stated premium · opinion · result; deal detail view at
`?d=`; one chart: offers per quarter by kind. **MCP:** `get_deals(year?, kind?, q?)`,
`get_deal(doc_id)`.

### 5.5 P4 — the TDnet tape and earnings summaries

**Source facts.** `tdnet/lists/{date}.json` rows `{time, sec_code, title, pdf, xbrl}` and
the documents beside them, since 2026-07-13; XBRL zips on ~29% of rows; the earnings
summary at `XBRLData/Summary/tse-*-ixbrl.htm` is fully tagged (`tse-ed-t`), verified on
4722 (2026-07-28).

**Extractor** `equity/tdnet_extract.py` (parser `tdn-1`):

- `eq_tdnet_disclosures(disclosure_id, date, time, sec_code, edinet_code, title_ja, category, category_rule, pdf_key, xbrl_key, sha256_pdf, sha256_xbrl, is_correction)` — one row per list entry; `category` is **our** keyword classification (derived, rule id stored): `earnings | forecast_revision | dividend | buyback_resolution | buyback_progress | cancellation | tob | mbo | third_party_allotment | split | delisting | major_holder_change | shareholder_proposal | board_change | other`. Rule precedence documented; a title matching nothing is `other`, never guessed. Corrections (訂正) flagged, not merged.
- `eq_tdnet_earnings(disclosure_id, sec_code, period_end, period_type, accounting_standard, consolidated, net_sales_yen, operating_income_yen, profit_yen, eps_yen, total_assets_yen, net_assets_yen, equity_ratio_pct, forecast_net_sales_yen, forecast_operating_income_yen, forecast_profit_yen, forecast_eps_yen, forecast_period_end, dps_actual_yen, dps_forecast_yen, dps_prior_yen, shares_outstanding, treasury_shares, forecast_revised, dividend_revised)` from the XBRL summary. Every field as filed; contexts resolved explicitly (current period, prior period, forecast) — the 16 `DividendPerShare` facts are quarterly × actual/forecast × current/prior and must be mapped by context id, not by order. Gate: `shares_outstanding ≥ treasury_shares`; forecast fields null when the flag says no forecast. Column names match `eq_key_indicators` where the concept is the same, so the quarterly rows bridge the annual panel.

**API** `app/tape_api.py`, `/api/v1/equity/tape`: `/summary` (horizon, rows/day,
category counts), `/recent?category=&segment=&q=&since=`, `/company/{code}`, `/earnings/{code}`
(latest summary + history since capture), `/categories`. The company page gains a **"Since
the annual report"** block: latest earnings summary with forecast, any forecast revision,
buyback resolutions (linking to the EDINET lifecycle rows by company and date), dividend
changes.

**Page** `web/tape.html` ("Tape"): a day-by-day feed with category chips and a company
filter, EDINET events (P2) merged in as their own source chip; the horizon stated in the
header ("captured since 13 July 2026 — TDnet keeps ~31 days; this archive keeps
everything from that date"). **MCP:** `get_tape(category?, code?, since?)`,
`get_latest_earnings(code)`.

### 5.6 P5 — screens, series, coverage, dictionary, demo

- **Cross-dataset screens** on a new `/api/v1/equity/screen` (one endpoint, named screens,
  each with its formula and its denominators): `entrenched_and_cross_held` (policy
  holdings / equity above X **and** lowest director support below Y), `unwind_vs_activist`
  (companies with a 5% filer stating an important-proposal act **and** policy holdings
  above X of equity), `low_roe_cash_rich` (ROE below X for three years **and** cash / assets
  above Y, from J — the classic target screen), `unspent_buybacks_low_pbr` (expired-unspent
  authorisation **and** implied PBR under 1). Page `screens.html`. Every screen filterable
  by segment and industry.
- **Campaign tracker** `/api/v1/equity/campaigns`: for each (issuer, filing group) with an
  important-proposal act stated, the joined sequence — 5% reports (P0 depth), shareholder
  proposals and their votes (P2), major-holder changes (P2), deals on the issuer (P3),
  buyback resolutions after the first filing (P4). Derived linkage by issuer and dates,
  rule stated. Page section on `stakes.html` and the company timeline.
- **Market aggregates as series** — `/api/v1/equity/series` (`/` lists, `/{id}` returns
  `[{period, value}]` with `calc` and the denominator N), same response shape as the macro
  `values` endpoint so the existing chart code and the `get_series_values`-style MCP tool
  work unchanged: `policy_holdings_total_yen` (annual), `policy_holdings_pct_equity_median`
  (annual), `buyback_authorised_yen_monthly`, `buyback_executed_yen_monthly`,
  `retirements_yen_monthly`, `activist_filings_monthly`, `five_pct_reports_monthly`,
  `foreign_ownership_avg_pct` (annual), `director_support_median_pct` (annual),
  `shareholder_proposals_count` (annual), `tob_count_quarterly`, `tob_stated_premium_median_pct`
  (quarterly), `roe_median_pct` (annual, from J). Each is derived, states its population,
  and is the Substack chart feed for the macro reader.
- **Coverage page** `coverage.html`: one matrix — dataset × fiscal year (or month) × filers
  covered / clean rate / horizon — generated from the manifests. Being explicit about what
  is *not* covered is itself the pitch.
- **Data dictionary** `GET /api/v1/catalog/dictionary` (+ `dictionary.html`): every `eq_`
  table and column with type, unit, trust (official / derived + formula), enumeration
  values and the methodology link — generated from the manifests and DuckDB's
  `information_schema`, never hand-written. This is the quant's first page and costs a day.
- `equities.html` gains the new products and drops the "on the roadmap" line.
- MCP `list_datasets` and the connect-time instructions mention every equity dataset.
- **Production seed.** `seed/equity.duckdb` is 70 MB today and ships inside the image;
  P0–P4 will multiply it. If it passes ~300 MB, move it to the volume with a one-off
  upload step in `start.sh` (download from the bucket on boot if newer) rather than
  bloating the image. Decide at the end of P0 from the real size.
- Rehearse §1 on production, in a fresh browser, timing it.

### 5.7 P6 — quant access (after the prototype)

Listed so that P0–P5 build toward it; nothing here is needed for the demo.

- **Bulk export.** A nightly offline job writes every `eq_` table as Parquet and gzipped
  CSV under `data/exports/{date}/`, with the dictionary and a `MANIFEST.json` (row counts,
  SHA-256, parser versions). Served as static files under `/api/v1/equity/bulk/`;
  `/bulk/latest` redirects. The serving process never writes (guardrail 5).
- **`as_of` ceilings** on every equity reader, per PLAN-API-MCP-V2 §2.7 (capture-date rule,
  `as_of_basis=filed` as the alternative); the restatement panel from J plus `as_of`
  gives point-in-time fundamentals, which is the thing a backtest cannot get from a
  vendor's revised history.
- **API keys, tiers and limits** — PLAN-API-MCP-V2 M4 (SQLite key store under `data/`,
  `X-API-Key`, admin keys tab). Bulk and `as_of` are keyed; the public tier keeps latest
  vintage and per-IP limits.
- **OpenAPI descriptions from manifests** so an agent or a quant's codegen can self-serve.
- Effort ~1½ weeks; own plan when it starts.

## 6. Acceptance for the whole prototype

1. Every §1 step works on production, unscripted, with no console errors, at 1440 and 390.
2. Every number on the company page reconciles with the dataset page it came from.
3. Every new endpoint answers a no-data company with the envelope, not a 500 (contract
   test over: a Growth-market IPO from 2026, a delisted code, a 5%-only issuer).
4. Every derived figure shows its formula on the page and in the CSV header; every as-filed
   figure links to its `doc_id`.
5. Methodology pages exist for key indicators, events, deals and tape, and the coverage
   page states each dataset's horizon (2024-04-01 for events, 2021-08 for offers and 5%
   filings, 2025-08 for buybacks, 2026-07-13 for the tape, FY2016 for key indicators).
6. Ten scripted MCP questions across the datasets, each answer's cite URL loads.
7. Capture jobs still green on the heartbeat monitor throughout — the demo must never cost
   a day of archive.
8. **User 1:** Toyota's ten-year panel reconciles line by line to its two most recent
   annual reports, restated years marked; every company-page figure shows its industry
   median with N.
9. **User 2:** the campaign tracker shows one 2024–2026 situation end to end (5% filing →
   shareholder proposal → vote → outcome); at least six market-aggregate series chart
   on the macro side's chart component with PNG and CSV.
10. **User 3:** the data dictionary lists every `eq_` table and column with unit and
    trust; the `eq_key_indicators` CSV downloads from the page and reloads into DuckDB
    with the same row count.

## 7. Files (new / changed)

**New** — `equity/universe.py`, `equity/indicators_extract.py`, `equity/events_extract.py`,
`equity/tob_extract.py`, `equity/tdnet_extract.py` (each with an `*_m1/` prototype folder
and trap notes, as the others have); `observatory/app/registry.py`, `company_api.py`,
`indicators_api.py`, `events_api.py`, `deals_api.py`, `tape_api.py`, `screen_api.py`,
`series_api.py`, `dictionary_api.py`; `web/company.html`, `agm.html`, `deals.html`,
`tape.html`, `screens.html`, `coverage.html`, `dictionary.html` and their `assets/*.js`;
`docs/METHODOLOGY-KEY-INDICATORS.md`, `METHODOLOGY-EVENTS.md`, `METHODOLOGY-DEALS.md`,
`METHODOLOGY-TAPE.md`.

**Changed (additive)** — the six equity API modules (`MANIFEST`, `segment` / `industry`
filters, peer medians), `equity/extract.py` (shared element-variant lists), `app/main.py`
(routers), `app/tools.py` + `app/mcp.py` (tools, instructions), `web/assets/nav.js`
(Company, AGM Votes, Deals, Tape, Screens, Coverage, Dictionary), `web/equities.html`,
`web/methodology.html` (pointers), `observatory/README.md`, `equity/README.md`, `start.sh`
(seed strategy if the size forces it). `eq_entities` gains `corporate_number`.

**Untouched** — `app/db.py`, every macro adapter, every existing `/api/v1` contract, the
capture jobs.

## 8. Decisions for you (not code)

1. **Prices.** Ask JPX for J-Quants Pro redistribution terms for "display of derived
   ratios to third parties" now, so the answer exists when the prototype needs live PBR.
   Or accept book-based ratios and the filer's own year-end PER for the prototype and
   revisit after the first investor meeting. Recommendation: ask now, build nothing on it
   yet — J makes the prototype credible without it.
2. **Backfill window.** The index serves 臨時報告書 from 2024-04-01 and the oldest date
   rolls forward continuously. Nothing to decide — but note it: this dataset's history is
   exactly as old as our capture and cannot be bought back later, same as buybacks.
3. **Naming for the fund audience.** The nav will read Company · Cross-Shareholdings ·
   Register · 5% Filings · Boards & Pay · Buybacks · Facilities · AGM Votes · Deals ·
   Tape · Screens · Coverage · Dictionary — thirteen equity pages under "Plover
   Analytics". Fine for a prototype; the vision plan's open question about a separate
   equity brand gets sharper once investors see it.
4. **TDnet in public.** The tape shows classified titles and filed numbers, not the PDFs.
   Confirm you are comfortable serving a public, near-real-time feed derived from TDnet
   list pages (the archive itself stays private).
5. **Watchlists and accounts** are the retention product and are not in this plan.
   Confirm the prototype ships without login, with a URL-encoded "my list" as the stand-in.
6. **Quant timing.** P6 is sequenced after the demo. If a quant prospect appears earlier,
   the bulk export (one day) can be pulled forward on its own; keys and `as_of` cannot.

## 9. Risks

| Risk | Treatment |
| --- | --- |
| AGM tables vary more than the one sample suggests (merged cells, per-candidate sub-tables, counts omitted) | M1 on 60 reports across three fiscal years and all three board structures before the full run; publish the clean rate; `unparsed` is a row state, not a gap |
| Key-indicator variants (IFRS / US GAAP / parent-only / banks and insurers with their own line items) fragment the panel | Reuse and extend the variant lists in `extract.py`; store `accounting_standard` and `basis` on every row; a missing line is null, never mapped to a near-equivalent; publish the fill rate per column |
| An implied PBR is read as a market ratio | Label on every surface ("implied from the filer's own PER at fiscal year end"), formula shown, null for loss years; never the sort key of a screen without the label |
| A parsed premium or opinion misstates a live deal | Only the filer's own sentence is stored, raw text always beside it, no recomputation; deals page labels "as stated by the offeror" |
| TDnet title classifier mislabels | Rule id stored per row; `other` for no match; a rules test file with 200 labelled titles; the raw title is always shown |
| Equity DuckDB outgrows the image | Size check at end of P0; volume + boot download path ready in `start.sh` |
| Composed endpoint slow or fragile | Per-block try/except, cache per code, warm the top 100; contract test on no-data codes |
| Four weeks of build stalls capture maintenance | Heartbeat monitor stays the first thing checked each session; capture code is not touched by this plan |
| Demo shows a number an investor can disprove | Every figure links to its filing; approval rates, premiums and key indicators are as filed; coverage page states every horizon |
| Quant needs arrive before P6 | The §5.0 rules mean no table changes shape; only export, keys and `as_of` are added |

## 10. Definition of ready

Build starts when: (a) the P0 runs have been launched (they take a day and nothing else
waits on them); (b) §8 items 4 and 5 are confirmed; (c) the size of the re-seeded equity
DuckDB is known. **First slice: P1 on the existing six datasets plus J** — a company page
for Toyota with its ten-year panel that reconciles to every dataset page and to the two
latest annual reports, live on production, before any other new extractor is written.
