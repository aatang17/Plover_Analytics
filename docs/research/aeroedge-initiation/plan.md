# AeroEdge (7409) — initiation of coverage — research plan

- **Question:** What is AeroEdge, how does it make money, what drives its earnings, and what could change the story?
- **Audience:** *Asia Economics Observations* Substack readers — foreign investors, mixed expertise · **Format:** Markdown / Substack · **Length:** ~2,500–3,000 words, 5–6 charts, one table
- **Rating:** none. Fundamentals-only initiation; no valuation section (we hold no share prices). Stated in the piece.
- **As-of date for Plover numbers:** 2026-09-27
- **Status:** approved 2026-09-27 (start before the FY6/26 annual report; add it when filed)

## Hypotheses (each must be disprovable)
1. **Revenue follows LEAP output.** AeroEdge's blade volume (engine-set equivalents, company KPI) grows in step with LEAP engine deliveries. *Disproven if* the company's engine-set growth and Safran's LEAP delivery growth diverge by more than ~15 pp in the same period without a share change to explain it.
2. **Volume lifts margins, because the material is free-issued.** Low variable cost means operating margin rises with sales. *Disproven if* margin fell in a year sales grew (FY6/25 already looks like a counter-example: sales +7.5%, margin 21.1% → 18.2%) — the piece must explain why.
3. **The company is still a one-product business through FY6/27.** *Disproven if* non-blade revenue (Engine B, Ono Plant, other) exceeds ~20% of the FY6/27 guided sales. The growth-plan deck's bridge suggests ~30%, so this is likely to break — the piece reports whichever holds.
4. **Growth is funded by debt and operating cash, not new equity.** *Disproven if* the share count rose for funding (the FY6/26 rise from 23.06m to 24.78m split-adjusted shares needs explaining — options, acquisition consideration or placement).

## Outline
| # | Section | Claim it will make | Evidence | Chart / table |
|---|---|---|---|---|
| 1 | Summary | Bottom line in one paragraph: sole-listed pure play on LEAP low-pressure-turbine blades; 40% contracted share to 2034; FY6/27 guided +56% sales | All below | — |
| 2 | Product, customer and contract | One of two qualified suppliers of TiAl LPT blades for LEAP; Safran 97.1% of sales; share 40% → high-40s from Jan 2028; contract terms (no minimum volume, firm orders only weeks ahead, termination triggers) | Growth-plan deck pp. 4–11, 29, 51; segments customer note | Table: contract terms |
| 3 | Volume driver | Blade volume tracks LEAP deliveries; 12-year backlog | Company KPI (engine-set equivalents FY6/24–FY6/27E); Safran LEAP deliveries; JADC backlog as cited by company | Chart: engine-set equivalents vs LEAP deliveries |
| 4 | Financial record | Sales ¥0.85bn (FY6/21) → ¥5.08bn (FY6/26); margin 22.5%; why FY6/25 margin fell | Annual reports FY6/23–25 (financials); FY6/26 earnings release; deck p. 49 for FY6/21–22 | Chart: sales and operating margin FY6/21–FY6/27E |
| 5 | Unit economics and FX | Revenue = processing fee; material free-issued; USD pricing with an FX band; ¥40–50m operating-profit sensitivity per ¥1/USD | Deck pp. 28, 46, 52 | — |
| 6 | Growth pillars | Share gain, own material (vertical integration, ~¥2.6bn capex), Engine A/B, Ono Plant, later MRO; FY6/27 bridge | Deck pp. 32–41, 47 | Chart: FY6/27 sales bridge by source |
| 7 | Balance sheet and funding | ¥3.8bn new loans; net D/E 0.6x vs 1.5x ceiling; OCF ¥2.96bn of which subsidies ~¥1.95bn; capex programme | FY6/26 earnings release; results presentation; FY6/26 annual report when filed | Chart: OCF (ex-subsidy) vs capex |
| 8 | Ownership and governance | Strategic holders (Kikuchi Gear, Toyota Tsusho, CEO, DMG Mori, DBJ) hold the majority; foreign ownership 2.6%; small free float; no dividend; one disclosed short | shareholder-register, large-shareholdings, short-positions, boards-and-pay | Chart: holder mix |
| 9 | Risks | Single customer and contract terms; free-issued material from one supplier; own-material ramp; 737 MAX / A320neo rates; FX; dilution | Business-risks section; deck pp. 51–52; Safran outlook | — |
| 10 | Appendix | Sources, formulas, what is company-sourced vs verified | log.md | — |

## Data inventory
| Evidence | Source | Held? (dataset · series/screen) | Gap decision |
|---|---|---|---|
| FY6/21–25 financials (five-year summary, statements) | Annual reports on EDINET | Yes · `financials` 7409 (3 filings: FY6/23–25, each with 5-year summary) | — |
| FY6/26 results and FY6/27 guidance | 決算短信, TDnet 13 Aug 2026 | Yes · `earnings-releases` 7409 (74 facts) | — |
| FY6/26 annual report (share-count bridge, options, Ono Plant consideration, subsidies) | EDINET, due by 30 Sep 2026 | **No — not filed as of 26 Sep** | **Fetch** when filed; if not in time, cite the earnings release and flag the gap |
| Customer concentration | Segment note, annual reports | Yes · `segments` 7409 (Safran rows FY6/23–25) | FY6/26 97.1% cite deck p. 7 |
| Revenue by region | Segment note | Yes · `segments` 7409 | — |
| Holders, register, 5% filings, shorts | EDINET / JPX | Yes · `shareholder-register`, `large-shareholdings`, `short-positions` | Register as of 30 Jun 2026: cite deck p. 10 until the annual report lands |
| Board, pay, employees | Annual reports | Yes · `boards-and-pay` 7409 | — |
| Business risks (as filed) | Annual report FY6/25 | Yes · `business-risks` 7409 (22 items) | — |
| Contract terms, KPI (engine sets), FY6/27 bridge, FX sensitivity, capex plan | Company decks on TDnet: growth plan 7 Sep 2026, results presentation 20 Aug 2026 | Archived here · `data/tdnet-*.pdf` with SHA-256 (see log) | **Cite** as company disclosure, labelled |
| LEAP deliveries | Safran H1 2026 results, 28 Jul 2026 — opened: 1,030 LEAP in H1 2026 (+41%), FY2026 guide "up high-teens" | No | **Cite** (https://www.safran-group.com/pressroom/safran-reports-its-first-half-2026-results-2026-07-28). PDF blocks scripted download; the page opens |
| A320neo / 737 MAX backlog | JADC, as reproduced in the company deck | No | **Cite** via the company deck, labelled as company-sourced |
| Share price, market cap, valuation | — | Not held | **Drop** (no rating) |
| Peer (the one competitor, "海外A社") | Not named by the company | No | **Drop** — do not name or guess |

## Out of scope
- Valuation, rating, target price.
- Naming the competing blade supplier (the company does not).
- Forecasts of our own. Only the company's guidance is used, labelled as such.
