# AMD under Lisa Su — research plan

- **Question:** What did AMD change under Lisa Su (CEO from October 2014) that turned a loss-making, indebted chipmaker into a profitable one — and what do the filed numbers show about the order in which it happened?
- **Audience:** professional investors (Substack readers) — *to confirm* · **Format:** md/Substack + docx — *to confirm* · **Length:** ~6–8 pages
- **As-of date for Plover numbers:** 2026-09-30
- **Status:** approved 2026-09-30 (user: add AMD and Intel; quote segments from annual reports, data-backed). Audience and format not answered — defaults used (professional investors; md + docx).

## Hypotheses (each must be disprovable)
1. **Repair came before growth.** Losses narrowed and debt fell (2015–2017) before revenue grew. — Disproven if revenue growth came first or debt rose through 2017.
2. **R&D was protected during the squeeze.** R&D held up while revenue fell. — Disproven if R&D was cut in step with revenue (R&D / revenue flat or falling 2014–2016).
3. **The recovery was margin-led, not just volume.** Gross margin rose materially after 2016. — Disproven if gross margin stayed near its 2014 level while revenue grew.
4. **Xilinx (2022) was paid in shares, not debt.** — Disproven if long-term debt rose materially in FY2022 rather than share count and goodwill.

## Outline
| # | Section | Claim it will make | Evidence | Chart / table |
|---|---|---|---|---|
| 1 | Summary | The answer, in order of events | all below | — |
| 2 | Starting point, 2012–2014 | AMD was shrinking, loss-making and indebted when Su took over | revenue, net income, debt, cash | Table: FY2012–14 |
| 3 | Balance-sheet repair, 2015–2017 | Tests H1, H2 | net income, debt, cash, R&D, R&D/revenue | Chart: R&D vs revenue |
| 4 | Product-led growth, 2017–2021 | Tests H3 | revenue, gross margin, operating income | Chart: revenue and gross margin |
| 5 | Scale by acquisition, 2022– | Tests H4 | goodwill, shares outstanding, long-term debt | Chart: goodwill and share count |
| 6 | Position in 2025 / Q1 2026 vs peers | Where AMD stands against NVIDIA and Intel | revenue, margins, R&D for all three | One comparison table |
| 7 | CEO pay and shareholder return | What the board paid for, 2020–2025 | Pay vs Performance: Su's pay, TSR, peer TSR | Chart: pay vs TSR |
| 8 | Limitations | What the filings can't show | — | — |

## Data inventory
| Evidence | Source | Held? | Gap decision |
|---|---|---|---|
| AMD annual financials FY2008–2025 (revenue, gross profit, R&D, net income, debt, cash, goodwill, shares) | SEC companyfacts, CIK 2488 — opened 2026-09-30; revenue 2010–2025 across two tags (switch in 2016–17) | **No.** AMD is not in the 55-company deep set; Plover holds only the FY2025 10-K and Q1 2026 10-Q | **Fetch:** add AMD to `equity/us_universe.json` and run the companyfacts pull → `/api/v1/us/financials/deep/AMD` |
| Intel financials (peer) | SEC companyfacts | No | **Fetch** the same way (optional) |
| NVIDIA financials (peer) | Plover deep set | Yes · `/api/v1/us/financials/deep/NVDA` | — |
| Revenue by segment (Data Center, Client, Gaming, Embedded) | 10-K segment notes | Only FY2025–26 (FSDS shelf); companyfacts has no segment detail | **Decide:** cite a few years from 10-Ks, or drop |
| Su's pay, TSR vs peer TSR, 2020–2025 | DEF 14A Pay vs Performance | Yes · `sec_px_facts`, 4 proxies | Page `us-pay.html` not deployed yet — links local only |
| Key events (CEO appointment, Zen launch, foundry deal, Xilinx close, ZT Systems) | AMD 8-K / 10-K filings | No (text, not numbers) | **Cite** from filings, opened during research |
| Share price before 2020 | — | Not held | **Drop** — no valuation or pre-2020 returns |

## Out of scope
- Valuation, forecasts, any buy/sell view.
- Product and technology detail beyond what filings state.
- Anything that needs share prices before 2020.
