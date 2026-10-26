# The BoJ's rate increases and Japan's regional banks: which banks have gained, and which have paid — research plan

- **Question:** Since the Bank of Japan began raising rates in March 2024, which regional banks have gained most and which have paid most, and does the next increase change the ranking?
- **Audience:** Professional investors; *Asia Economics Observations* readership · **Format:** Markdown (Substack) + PDF · **Length:** ~1,800 words, 5 charts
- **As-of date for Plover numbers:** set on the day the corrected rate-risk data (`irrbb-2`, 2026-09-24) is live in production. Until then production still serves the pre-correction figures, and a link frozen now would show them.
- **Relation to the earlier note:** standalone. It reuses data already pulled for `regional-banks-jgb` but answers a different question (bank-level gain and cost of the hikes, rather than accounting against regulatory exposure).
- **Status:** approved 2026-09-24

## The rate path this piece measures against

The uncollateralised overnight call rate (BoJ, FM01 `STRDCLUCON`, monthly average of daily values, checked 2026-09-24) moves in five steps:

| Month the step appears | Average call rate after the step |
| --- | --- |
| March 2024 | 0.077% (from −0.006% in February) |
| August 2024 | 0.227% |
| January–February 2025 | 0.478% |
| December 2025–January 2026 | 0.728% |
| June–July 2026 | 0.978% |

The first four fall inside the published accounts (fiscal years to March 2024, 2025 and 2026). The fifth does not, and is covered only by the banks' own forward estimates (ΔNII under a parallel rise, from their IRRBB1 disclosures).

## Hypotheses (each must be disprovable)

1. **Most regional banks are net gainers so far.** The increase in net interest income from FY2023 to FY2025 exceeds realised losses on bonds over FY2024–FY2025 for a majority of banks. *Disproven if* fewer than half of the banks in the sample show a positive net figure.
2. **The gain follows the lending franchise.** Banks with a higher loan-to-deposit ratio at March 2024 show a larger percentage increase in net interest income. *Disproven if* the Spearman rank correlation between the two is below 0.2 or negative.
3. **Second-tier regional banks gained less.** The median percentage increase in net interest income for second-tier regional banks is below that of first-tier regional banks. *Disproven if* it is equal or higher.
4. **The banks that have paid most are not those most exposed to the next increase.** Ranked by realised plus unrealised bond losses against net assets, and separately by ΔEVE against Tier 1, the top-ten lists overlap by no more than three banks. *Disproven if* the overlap is four or more, or the rank correlation exceeds 0.5.

## Outline

| # | Section | Claim it will make | Evidence | Chart / table |
|---|---|---|---|---|
| 1 | Summary | The answer: how many banks are net gainers, who leads and who lags, and whether the next increase reorders them | All below | — |
| 2 | The rate path and its pass-through | Four increases reached the accounts by March 2026; loan rates moved materially further than deposit rates | Call rate (BoJ FM01); regional-bank new-loan rate (IR04) and ordinary deposit rate (IR02) | **Chart 1** — call rate, regional-bank loan rate and posted deposit rate, 2023–2026 |
| 3 | The sector result | Net interest income against realised bond losses, first-tier and second-tier regional banks, FY2023–FY2025 | JBA aggregates: interest income (e020), interest expenses (e350), bond sale/redemption gains and losses (e230, e240, e610, e620, e630) | Table — the two tiers, three fiscal years |
| 4 | The gainers | The distribution of the increase in net interest income by bank, and its relation to the loan-to-deposit ratio | JBA bank-level e020 − e350; loans (d395) and deposits (a015) | **Chart 2** — increase in net interest income by bank, ranked · **Chart 3** — against loan-to-deposit ratio |
| 5 | The cost | Realised bond losses FY2024–FY2025 and unrealised losses at March 2026, against net assets; the banks for which the cost exceeds the gain | JBA bond lines and net assets (b950); unrealised AFS bond losses from the annual securities reports (`bank-balance`) | **Chart 4** — gain against cost by bank, both scaled to net assets |
| 6 | The next increase | The June 2026 increase is not yet in the accounts; each bank's own ΔNII and ΔEVE under a parallel rise indicate who gains and who is exposed | IRRBB1 (`bank-balance` IRRBB, corrected `irrbb-2`, 89 of 98 banks) | **Chart 5** — ΔNII against ΔEVE under a parallel rise, per bank, as % of Tier 1 · Table — the most exposed banks |
| 7 | Limitations | Mergers, missing banks, bank vs holding-company basis, no share prices | — | — |

## Data inventory

| Evidence | Source | Held? (dataset · series) | Gap decision |
|---|---|---|---|
| Policy rate path | BoJ Time-Series Data Search, FM01 `STRDCLUCON` | **No** | **Fetch** — `https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=FM01&code=STRDCLUCON` (opened 2026-09-24, returns daily data to September 2026). Save with SHA-256; carry the BoJ credit line. Quote the call rate as published; do not state policy-rate decisions that were not opened at source. |
| Loan rates by lender type | BoJ IR04 | Yes · `boj-loan-rates` | — |
| Posted deposit rates | BoJ IR02 | Yes · `boj-deposit-rates` (monthly from April 2022) | — |
| Bank-level income statement: interest income, interest expenses, bond gains and losses | JBA 全国銀行財務諸表分析 | Yes · `jba-banks` `s.{bank}.e020/e350/e230/e240/e610/e620/e630.fy`, through March 2026 | — |
| Bank-level loans, deposits, net assets | JBA | Yes · `jba-banks` `s.{bank}.d395 / a015 / b950` | — |
| Tier aggregates | JBA | Yes · `jba-banks` `s.regional.*`, `s.regional-2.*` | — |
| Unrealised losses on AFS bonds | Annual securities reports (EDINET), notes | Yes · `bank-balance` (filer level; for a holding company this is the group) | Stated as a basis difference where the filer is a holding company |
| ΔEVE, ΔNII, Tier 1 | Each bank's Basel Pillar 3 disclosure (IRRBB1) | Yes, corrected locally · `bank-balance` IRRBB (`irrbb-2`) | **Deploy first** — production still serves `irrbb-1`, which swapped current and prior year for 11 banks and gave 18 banks their group's figures |
| JGB yields | MOF | Yes · `jgb-yields` | Context only |
| Share prices | — | No (not licensed) | **Drop** — no valuation view |

## Method notes

- **Net interest income** = interest income (e020) − interest expenses (e350), non-consolidated, calculated. It is close to but not identical with the FSA's 資金利益, which deducts the funding cost of money-held-in-trust; the piece says so once and uses the JBA definition throughout.
- **Realised bond result** = (e230 + e240) − (e610 + e620 + e630), the 国債等債券損益 components, calculated.
- **Gain** = FY2025 net interest income − FY2023 net interest income. **Cost** = realised bond result summed over FY2024–FY2025, plus unrealised AFS bond losses at March 2026 shown separately. Both are scaled to March 2026 net assets.
- **Mergers.** A bank's code absorbs a merged bank's history only from the merger date, so a merger inside the window inflates growth. Banks with a merger in FY2023–FY2025 (from the JBA 利用上の注意 sheets) are excluded from growth rankings and listed.
- **Sample.** First-tier and second-tier regional banks in the JBA data. Rate-risk sections use the 89 banks with an IRRBB1 table; the 9 without one are named. Iwate's rate-risk figure is as of March 2025 and is excluded from Chart 5.
- **Basis.** JBA and IRRBB figures are the bank's own; unrealised losses are the filer's, which is the group for a holding company. Where the two bases meet (Section 5), the difference is stated beside the figure.

## Out of scope

- Valuation and share-price reaction (no licensed price data).
- Shinkin banks and credit co-operatives.
- City banks, except as context in Section 2.
- Forecasting further BoJ decisions; the piece takes the path to June 2026 as given and uses the banks' own sensitivity estimates beyond it.
- A recommendation. The piece builds the evidence; any call is the author's to add.
