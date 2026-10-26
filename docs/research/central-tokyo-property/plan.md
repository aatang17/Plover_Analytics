# Central Tokyo residential property: extent and drivers of the recent correction

- **Question:** Are residential property prices declining across most central Tokyo wards, particularly Minato, and what do transaction prices, supply, demand, rental income and financing conditions establish about the breadth and persistence of the correction?
- **Audience:** A property investor seeking a detailed, plain-English evidence report. Assumed pending approval.
- **Format:** Illustrated PDF plus editable Markdown and chart-data CSVs. Assumed pending approval.
- **Length:** Approximately 12-15 pages, including sources and methodology, with 8-10 charts where the data permits.
- **Information cutoff / Plover as-of:** 2026-10-14. Each series retains its actual observation date; an October report does not imply October observations.
- **Status:** Draft plan; awaiting user approval under the workspace research-report skill.

## Geography and property scope

Residential condominiums are the primary segment. Cover Minato, Chiyoda, Chuo, Shinjuku, Shibuya and Bunkyo individually where published data permits. Use the same six-ward aggregate only when its definition is confirmed in the source. Compare against Tokyo's 23 wards and the metropolitan region, clearly labelled as broader benchmarks. Include Minato neighbourhoods or station areas only when supported by adequate observations; examples of interest are Akasaka, Azabu, Roppongi, Aoyama, Shirokane and the Shiba/Shinagawa waterfront.

Resale asking prices, completed resale transactions, new-build launch prices and appraised land values are separate measures. Office indicators may provide context but cannot establish residential price declines.

## Hypotheses to test

1. **The current correction is broad across central wards.** Test each ward's latest quarter/month, year-on-year change and drawdown from its observed peak. Refuted if fewer than half the six wards show comparable negative changes. Report missing wards explicitly rather than counting them as unchanged.
2. **Selling conditions weakened before completed sale prices.** Test listing inventory, price-cut prevalence, discount size, transaction volumes and selling periods. Refuted if comparable metrics show tightening supply or improving execution. A discount from an optimistic asking price alone does not establish falling market values.
3. **Affordability and higher financing costs contribute to weakness.** Test mortgage rates, repayment scenarios and prices relative to income/rents. Refuted or weakened if borrowing costs and affordability did not deteriorate. Correlation will not be presented as causal proof.
4. **The correction reflects valuation pressure more than deteriorating occupancy demand.** Test rents, occupancy, households, net migration and new supply. Refuted if rents, occupancy and household demand weaken alongside sale prices. Rental strength is counterevidence to a demand-collapse claim, but cannot rule out a valuation correction.

## Proposed report and exhibits

1. **Executive finding and evidence scorecard.** State exactly what is declining, over which period, and how many wards support the claim. Chart: ward-level change and peak drawdown, with missing observations visible.
2. **Historical prices and the turning point.** Aim for 2015-latest comparable history, plus monthly detail for 2024-2026. Charts: resale price levels and rebased series; monthly/annual growth. Extend earlier only where consistent definitions exist.
3. **Minato and central-ward comparisons.** Compare prices per square metre, age/size/location-controlled published cohorts and transaction counts. Chart: ward comparison and available Minato submarket breakdown. No inference about every neighbourhood from a ward aggregate.
4. **Asking prices versus completed sales.** Compare source definitions, dated price movements, asking-to-sale discounts and selling periods. Chart: separate series or panels, avoiding a false matched-property comparison.
5. **Supply and absorption.** Examine resale listings, new registrations, completed transactions, new launches, initial-month contract rates, unsold stock and the development pipeline. Charts: stock versus sales and pipeline by ward. Future completions are not current unsold inventory.
6. **Demand and demographics.** Examine households, net migration, foreign residents and rental occupancy. Chart: household/migration changes and occupancy. Resident nationality is not a measure of overseas property purchases.
7. **Rents and investment valuation.** Examine rent growth, gross yields and any comparable published net yields. Chart: separate price and rent growth, plus clearly labelled yield sensitivities. Include management fees, repair reserves, vacancy and other ownership costs where verified.
8. **Financing and affordability.** Examine actual mortgage benchmarks and calculate repayments at alternative rates for an explicitly assumed loan. Charts: mortgage-rate history and repayment sensitivity. BOJ average business/all-loan rates are macro context, not mortgage rates.
9. **Inflation and alternative explanations.** Calculate inflation-adjusted price changes where aligned observations permit; discuss shifts in building age, unit size and luxury-project mix, construction costs and exchange-rate effects only with sourced evidence.
10. **Persistence and uncertainty.** Distinguish a few monthly falls from a sustained annual downturn. Present conditional scenarios rather than a point forecast. Appendix: sources, release dates, calculations, coverage and limitations.

## Data inventory checked on 14 October 2026

| Evidence | Verified source / coverage | Held? | Gap decision |
|---|---|---|---|
| Latest resale price movement and selling-pressure indicators | [Tokyo Kantei August 2026 release](https://www.kantei.ne.jp/report/70m2/9447/) and [monthly archive](https://www.kantei.ne.jp/report/category/70m2/), both opened. Latest release is dated 24 September 2026. | No property-price dataset identified in the advertised Plover dataset list. | Fetch the release's linked price and selling-pressure PDFs after approval. Extract definitions, central-area coverage and history. Use attributed published benchmarks; do not assume the monthly average controls for mix. |
| More comparable resale cohorts | [Tokyo Kantei H1 2026 report](https://www.kantei.ne.jp/news/release/9375/) and [metropolitan PDF](https://www.kantei.ne.jp/wp-content/uploads/market-price20261H_shuto.pdf), opened. Cohort: age 10 +/- 5 years and station walk within 15 minutes. | External. | Fetch after approval; identify ward/station coverage. Cohort restriction is not full quality adjustment. |
| Asking-to-sale discount and selling period | [Tokyo Kantei 2025 report](https://www.kantei.ne.jp/news/release/9230/), opened; published 30 July 2026. | External. | Fetch linked metropolitan PDF. Treat 2025 observations as historical context, not current 2026 outcomes. |
| Completed transactions, inventory and registrations | [East Japan REINS 2026 library](https://www.reins.or.jp/library/2026.html), opened unsuccessfully: tool returned HTTP 502. Search confirms the library exists. | External. | Access not yet confirmed. Retry official public document access after approval. If still inaccessible, use MLIT transaction downloads where available and disclose the REINS gap. No circumvention of denied access. |
| Property-level transactions and land appraisals | [MLIT Real Estate Information Library](https://www.reinfolib.mlit.go.jp/), opened; public download and land-value functions are listed. | External. | Fetch public downloads after approval. Verify ward coverage, lag, duplicated observations and transaction-price versus contract-price definitions. Use quarterly medians with sample counts; do not claim repeat-sales results without matched data. |
| Official price-index history | [MLIT index page](https://www.mlit.go.jp/totikensangyo/totikensangyo_tk5_000085.html?_fsi=Ozap5lwu), opened. Update list ends with December 2025 residential data, published 31 March 2026. September notice says January-June 2026 releases were delayed by a calculation-program problem. | External. | Fetch historical series. Do not invent a 2026 official index or treat missing releases as no price change. Use other clearly identified sources for the latest period. |
| Tokyo land-value check | [Tokyo July 2026 appraisal release](https://www.metro.tokyo.lg.jp/information/press/2026/09/2026091521), open returned HTTP 403; search snippet only. | External. | Content access unconfirmed. Prefer official public appraisal downloads from the opened MLIT library. Keep appraised land values separate from condo prices and recognise annual lag. |
| New supply, launch pricing, contracts and unsold stock | [Real Estate Economic Institute](https://www.fudousankeizai.co.jp/) and [completed-inventory archive](https://www.fudousankeizai.co.jp/mansion?archiveFlg=2), opened. August 2026 launch release was corrected on 9 October; September release is scheduled for 20 October. | External. | Fetch corrected August release and history. Quote broader regional data as broader data. Development pipeline requires a separately verified primary release. |
| Resale rents | [Tokyo Kantei rent archive](https://www.kantei.ne.jp/report/category/rent/), opened. | External. | Fetch relevant release after approval; confirm property scope and whether rents are asking or contracted. |
| Rental occupancy and professional valuation context | [Savills residential research](https://www.savills.co.jp/insight-and-opinion/research.aspx?f=date&p=Residential&page=0&q=&rc=Japan&t=), opened; Tokyo Residential Leasing Q2/2026 is listed, dated 21 July 2026. | External. | Fetch linked report after approval. Treat its managed-property sample as that sample, not all Tokyo households. Verify cap-rate data separately; drop unsupported valuation claims. |
| Ward households and population | Plover `population-jp-municipal`, described; coverage January 2025-January 2026, annual. Search verified Minato codes `13103.all.population`, `13103.all.households`, `13103.all.in_domestic`. [Source view](https://ploveranalytics.com/population.html). | Yes. | Fetch series with `as_of=2026-10-14`; search other wards and net-flow measures. Annual history is too short for a ten-year ward demand chart; use municipal public history if verified, otherwise show the shorter window. |
| Inflation adjustment | Plover `cpi-tokyo`, described; January 1970-September 2026; September advance can be revised. Search verified headline code `0001`. [Source view](https://ploveranalytics.com/tokyo.html). | Yes. | Fetch `0001` at the frozen as-of date, align with price months, and calculate a real-price series. Headline CPI measures general purchasing power, not construction replacement cost. |
| Financing context | Plover `boj-loan-rates`, described; through August 2026. Search verified `DLLR2CIDBNL3` (new long-term loans, domestically licensed banks). [Source view](https://ploveranalytics.com/banks.html?dataset=boj-loan-rates). | Yes, macro loans only. | Optional macro-context series. Mortgage history is a separate gap: discover and open Japan Housing Finance Agency or bank primary releases before adding their numbers. No substitution of all-loan rates for mortgages. |

## Methods and evidence limits

- Calculate change as `(latest / earlier - 1) * 100`; drawdown as `(latest / observed comparable peak - 1) * 100`. Name the exact peak search window.
- Show month-on-month, quarter-on-quarter and year-on-year separately. A positive annual change can coexist with a recent peak drawdown.
- For transactions, use price per square metre, report sample counts, and compare age/size bands where possible. Flag small samples and mix changes. Do not fit a quality-adjustment model to insufficient observations.
- Estimate months of inventory only from geographically and definitionally comparable inventory and monthly sales; use a three-month sales average where justified. Do not divide Kantei listings by REINS transactions and call it true months of supply.
- Gross yield is annual comparable rent divided by property price. Net yield requires stated operating costs. Aggregate price/rent comparisons are indicative unless the units and sample match.
- Mortgage calculations state principal, term, rate type and repayment formula. Scenarios are calculations, not observed market outcomes.
- Real-price change is `(price_latest / price_base) / (CPI_latest / CPI_base) - 1`.
- Preserve source dates and published precision. Never combine land values, new launches and resale transactions into one continuous price index.
- Every figure must be logged and traced in `verify.md`. Primary-source evidence takes precedence over commentary. Save underlying data and file checksums where retrieval is allowed.

## Out of scope

- A preselected conclusion that most central wards are falling regardless of evidence.
- Commercial-property valuation as a substitute for housing evidence.
- Paid datasets, subscriptions, outreach, publication or trading actions.
- Assertions about overseas buyer demand without verified purchase data.
- Building-specific valuation, legal/tax advice or a forecast presented as a measured result.
- Product changes, new data ingestion, commits or pushes.

## Approval requested

Approve this scope to proceed with the detailed research, charts, report and verification. If accepted without changes, the assumed residential-condominium scope, six central wards, PDF/Markdown formats and approximate length become the working specification.
