# Findings — regional banks and JGB yields

Every number here traces to `log.md` and the CSVs in `data/`. `as_of=2026-09-23`
throughout. Verdicts are against the four hypotheses in `plan.md`.

---

## §2 The rate move — setup, not a finding

| 10-year JGB | |
| --- | --- |
| 29 Mar 2024 (FY2023 close) | 0.750% |
| 31 Mar 2026 (FY2025 close) | 2.366% |
| 17 Sep 2026 (latest) | 2.993% |
| High in this cycle | 3.028%, 15 Sep 2026 |

The 30-year is 4.047% and the 5-year 2.322% at the latest date. Source:
`data/jgb-yields.csv`.

## §3 H1 — the margin genuinely widened. **Holds.**

New-loan rate, regional banks: **0.854% → 1.752%, +0.90pp** (Mar 2024 → Jul 2026).
Posted ordinary deposit rate: **0.002% → 0.322%, +0.32pp** (Mar 2024 → Sep 2026).

So roughly 0.9pp came in on new lending and 0.3pp went out on the most common
deposit. The gap is the margin.

Worth noting for the draft: at July 2026 the shinkin banks charge **2.194%**,
more than the city banks' **1.778%** — the small lenders charge most and the
megabanks least, which is the usual ordering and not a finding in itself.

City banks' new-loan rate spiked to 2.007% in March 2026 and fell back to
1.778% by July; that line is volatile month to month because it depends on
which large loans were written. Do not read the March-to-July fall as easing.

Source: `data/loan-rates.csv`, `data/deposit-rates.csv`.

## §4 H2 — the margin gain was spent on the bond book. **Holds, and more exactly than expected.**

Regional banks, fiscal years to March, ¥bn (published ¥100mn ÷ 10):

| | FY2023 | FY2024 | FY2025 | two-year change |
| --- | --: | --: | --: | --: |
| Net interest income | 3,884 | 4,237 | 4,975 | **+1,091 (+28%)** |
| Gains on bonds | −477 | −475 | **−1,156** | worse by 679 |
| Core net business profit | 1,641 | 1,958 | 2,637 | **+996 (+61%)** |
| Net income | 958 | 1,309 | 1,804 | +846 (+88%) |

**The FY2025 bond loss of ¥1,156bn is 106% of the entire two-year gain in net
interest income.** One year of realised losses consumed slightly more than two
years of extra margin. That is the sentence the piece is built on.

Core profit grew faster than interest income (+61% against +28%), which is H1
confirmed from the P&L side as well as the rate side: costs did not eat the gain.

Source: `data/regional-pl.csv`.

## §5 H3 — regionals bought, megabanks sold. **Holds.**

Government bonds held, ¥tn:

| | Mar 2022 | Mar 2024 | Mar 2026 | two-year |
| --- | --: | --: | --: | --: |
| Regional banks | 15.0 | 14.7 | 20.8 | **+41%** |
| Second-tier regionals | 2.8 | 3.1 | 4.6 | **+51%** |
| City banks | 75.7 | 56.6 | 55.5 | **−2%** |

City banks have cut their JGB book by ¥20tn since March 2022. The regionals
added ¥6tn in the last two years alone. The exposure is moving down the tiers.

Lending is also catching up with deposits: loan-to-deposit **77.5% → 81.7%**
(regional aggregate, Mar 2024 → Mar 2026). Loans grew ¥21.9tn, deposits ¥9.9tn.

**Correction to the earlier note:** city banks held **¥75.7tn** at March 2022,
not the ¥69tn used previously.

Source: `data/jgb-holdings-by-group.csv`, `data/regional-balance-sheet.csv`.

## §6 The distribution — the group number hides enormous spread

Unrealised loss on available-for-sale bonds, regional filers whose filing passed
every gate (67 of 74): **−¥3.42tn**, of which JGBs **−¥1.91tn**.

Against net assets, per bank — **median 13.9%** across the 67 regional filers
whose accounts reconciled in full:

| Bank | Loss ÷ net assets |
| --- | --: |
| Shimane | 58.2% |
| Daito | 58.1% |
| Tohoku | 48.0% |
| Kochi | 46.0% |
| Fidea Holdings | 43.5% |
| San-in Godo | 37.4% |

Share of the securities book maturing beyond ten years — **median 21.2%** across
the 38 regional banks whose note prints a six-bucket ladder (49 banks of all
types do): Jimoto 69.8%, Shizuoka FG 63.6%, San-in Godo 60.0%, Shimane 54.9%,
Hirogin 53.2%.

Shimane appears near the top of both lists — a small bank with a long book and
thin equity. It is the clearest single illustration.

**Note for §7:** three of the six worst (Tohoku, San-in Godo, and Iwate just
below) publish no rate-risk table we could retrieve, so they drop out of the
comparison. The earlier version of this table ranked only the banks that carry
both measures, which put Kochi third and split Fidea Holdings into its two
banks; corrected here to rank every regional filer.

Source: `data/rank-bond-loss-to-equity.csv`, `data/rank-over-10y-share.csv`,
`data/summary.json`.

## §7 H4 — the two measures flag different banks. **Holds, and strongly.**

Banks carrying both an unrealised-loss figure and their own Basel ΔEVE: **58**.

- **Spearman rank correlation: 0.307.** Weak.
- **Top-ten overlap: 1 of 10.**
- Restricting to one row per distinct source document (48 banks, see the caveat
  below): **Spearman 0.307, overlap 2 of 10.** The result is not an artifact of
  shared tables.

Worst on the accounting measure vs worst on the supervisor's:

| Worst accounting hole | loss ÷ equity | its ΔEVE ÷ Tier 1 |
| --- | --: | --: |
| Shimane | 58.2% | 10.7% |
| Daito | 58.1% | 5.0% |
| Kochi | 46.0% | 13.9% |

| Worst ΔEVE ÷ Tier 1 | ΔEVE ÷ Tier 1 | its loss ÷ equity |
| --- | --: | --: |
| **Tochigi** | **41.0%** | 9.0% |
| Saga | 17.8% | 26.8% |
| Daishi Hokuetsu | 17.8% | 16.1% |
| Gunma | 17.5% | 11.9% |

Tochigi: ΔEVE ¥59.0bn against Tier 1 of ¥144.1bn, from its own disclosure, page
61. It is the single largest rate exposure among the banks that publish one, and
it is nowhere near the top of the accounting list.

**Why they differ, for the draft.** The accounting number is backward-looking
and narrow: the mark already taken on bonds you happen to hold, over a small
equity base. The Basel number is forward-looking and whole-balance-sheet: what
the *next* shock does to the economic value of loans and deposits as well as
bonds. A bank can have taken its pain already and have little left to lose, or
have taken none and be badly placed for the next move. Across the 58 banks carrying both, median ΔEVE is 7.6% of Tier 1 against a
median accounting hole of 14.6% of net assets — a different population from the
13.9% above, and stated here only to compare the two measures on identical banks.

Above the Basel 15% outlier line: **7 of 48** distinct tables.

Source: `data/irrbb-by-bank.csv` joined to `data/rank-bond-loss-to-equity.csv`.

---

## Caveats that must appear in the piece

1. **Coverage.** 71 of 98 regional and second-tier regional institutions publish
   a rate-risk table we could find: 56 clean, 15 with a flagged check, 27 not
   found. The 27 are mostly sites that block automated access.
2. **Shared tables.** 70 rows come from 59 distinct documents. Eight holding
   companies publish one table covering several banks — Fukuoka FG's covers four
   (Fukuoka, Juhachi-Shinwa, Fukuoka Chuo, Kumamoto), Yamaguchi's covers three.
   Those are not independent observations, which is why §7 reports the result
   both ways.
3. **Gunma** blocks scripted access from some networks; its figure was collected
   successfully from the production host but not from a laptop.
4. **The maturity ladder is principal, not book value**, as the note itself
   states, so it does not reconcile to the balance-sheet securities line.
5. **Nine of 91 filings are `partial`** on the bond-book extraction and are
   excluded from every ranking and group sum, shown only on their own page.
6. **No share prices.** Nothing here says whether any of this is in the price.
