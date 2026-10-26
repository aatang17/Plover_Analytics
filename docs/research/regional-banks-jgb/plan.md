# Plan — Japan regional banks and the rise in JGB yields

**Status:** awaiting approval. No research beyond the inventory below has been done.
**As-of:** `2026-09-23`. Every Plover number is frozen at this date (`as_of=2026-09-23`)
so the piece's links keep showing the same figures after the next release.

---

## 1. Question

> **Which regional banks are winning from higher rates, and which are exposed through
> the bond book they built on the way up?**

The group answer is already public and unsurprising: regional banks earned more in the
year to March 2026 than in any year since the data begins. The piece exists because the
group number hides the distribution — and because we can now open the bond book bank by
bank, which is not something the published aggregates allow.

**Audience:** buy-side and sell-side analysts who cover Japanese banks, plus *Asia
Economics Observations* readers. Assume they know what a bond book is; do not assume they
know what ΔEVE is.

**Format and length:** to be confirmed — see the questions at the end.

## 2. Hypotheses

Written so the data can prove them wrong.

| # | Hypothesis | What would disprove it |
| - | ---------- | ---------------------- |
| **H1** | Rising rates have widened the lending margin faster than deposit costs have risen, so core profit is growing faster than interest income. | Core profit growing in line with, or slower than, net interest income. |
| **H2** | Most of the extra interest income has been spent realising losses on old low-yield bonds, so the profit rise is smaller than it looks. | Bond losses materially smaller than the rise in interest income. |
| **H3** | Regional banks added JGBs while the megabanks cut them, so duration risk is concentrating in the weaker tier. | Regional holdings flat or falling, or city banks adding. |
| **H4** | **The banks carrying the biggest mark-to-market hole are not the banks the supervisor's own rate-risk measure flags.** The two rankings disagree. | The two rankings broadly agree, in which case the unrealised-loss number tells you nothing new. |

H4 is the piece's reason to exist. It needs both the unrealised loss from the securities
note and each bank's own Basel ΔEVE from its Pillar 3 disclosure, side by side. As far as
I can establish, no free source puts those two together.

## 3. Outline

One claim per section, with the evidence and the chart that carries it.

| § | Claim | Evidence | Visual |
| - | ----- | -------- | ------ |
| 1 | **The answer, up front.** Regional banks doubled profits, spent nearly all the margin gain cleaning up the bond book, and are now more rate-exposed than the megabanks. | §2–§6 | — |
| 2 | Rates moved further and faster than the market expected: 10-year JGB 0.9% → 3.0%. | `jgb-yields` 10Y, 5Y, 30Y | Line chart, 2024–now, with the March fiscal year-ends marked |
| 3 | **H1** — The margin is genuinely wider. New-loan rates rose about 1pp while posted deposit rates rose a fraction of that. | `boj-loan-rates` (city/regional/regional-II/shinkin), `boj-deposit-rates` ordinary | Line chart: four lender rates + deposit rate |
| 4 | **H2** — Group profits are up sharply, but bond losses took most of the margin gain. | `fsa-bank-results` regional: net interest income, bond gains, core profit, net income, FY2022–FY2026 | Column chart: change in NII vs bond losses, by fiscal year |
| 5 | **H3** — Regionals bought JGBs as yields rose; the megabanks sold. | `jba-banks` `s.regional.d290`, `s.regional-2.d290`, `s.city.d290`; loan-to-deposit from `d395`/`a015` | Indexed line, March 2022 = 100 |
| 6 | **The distribution.** Which individual banks carry the loss — unrealised bond loss against net assets, and the share of the book beyond ten years. | `bank-balance` screens `bond_loss_to_equity`, `over_10y_share` | Ranked bar, top 15, with the group median marked |
| 7 | **H4** — The supervisor's own measure disagrees with the accounting one. | `bank-balance` `/irrbb` ΔEVE ÷ Tier 1, joined to §6 | Scatter: unrealised loss ÷ equity (x) against ΔEVE ÷ Tier 1 (y), 15% line marked |
| 8 | **What would change the answer** — risks and limits. | coverage gaps below | — |
| — | Appendix: sources, formulas, coverage. | | |

Sections 6 and 7 are the ones nobody else can publish. Sections 2–5 are the setup and
should be brisk.

## 4. Data inventory

Every row below was confirmed by an actual call today, not from memory.

### Held, on production (citable public URLs today)

| Evidence | Dataset | Series / screen | Confirmed |
| -------- | ------- | --------------- | --------- |
| 10y/5y/30y JGB yields, daily to 2026-09-17 | `jgb-yields` | `10Y`, `5Y`, `30Y` | ✅ 10Y = 3.028% on 2026-09-15, 2.366% at FY-end 2026-03-31 |
| Regional group P&L, FY2005–FY2026 | `fsa-bank-results` | `regional.net-interest-income.fy`, `.bond-gains.fy`, `.core-net-business-profit.fy`, `.net-income.fy` | ✅ NII ¥3,884bn → ¥4,975bn; bond gains −¥477bn → −¥1,156bn |
| JGB holdings by bank group | `jba-banks` | `s.regional.d290`, `s.regional-2.d290`, `s.city.d290` | ✅ regional ¥14.70tn → ¥20.76tn; city ¥75.71tn (2022) → ¥55.52tn |
| Loans, deposits, securities by group | `jba-banks` | `s.regional.d395`, `.a015`, `.d275` | ✅ loan/deposit 77.5% → 81.7% |
| Per-bank statement lines, ~150 banks | `jba-banks` | `s.{code}.d290` etc. | ✅ 151 per-bank government-bond series |
| Bad-loan ratio by group | `fsa-npl` | regional tiers | ✅ available, to 2025-09 |

### Held, but **local only — not yet deployed**

| Evidence | Dataset | Confirmed today | Coverage |
| -------- | ------- | --------------- | -------- |
| New-loan rate by lender, 1993– | `boj-loan-rates` | ✅ regional 1.752%, city 1.778% (Jul 2026) | 63 series |
| Posted deposit rates | `boj-deposit-rates` | ✅ | 31 series, Apr 2022– |
| Every deposit-taker with disclosure URL | `fsa-regional-fi` | ✅ | 495 institutions |
| **Unrealised bond loss per bank** | `bank-balance` | ✅ 82 banks ranked; regional total −¥3.42tn, of which JGBs −¥1.91tn | 82 clean of 91 |
| **Share of book beyond 10 years** | `bank-balance` | ✅ 48 banks (six-bucket ladders only) | 48 |
| **Official ΔEVE ÷ Tier 1** | `bank-balance` `/irrbb` | ✅ 72 banks; 58 clean, 14 flagged, 26 not found | 72 of 98 |

### Gaps and how each is handled

| Gap | Decision |
| --- | -------- |
| **Four datasets are local-only.** Sections 3, 6 and 7 — the heart of the piece — cannot be cited to a public URL until they are deployed. | **Needs your call.** Either (a) deploy first, then write, so every chart links to a live citable view; or (b) write now and hold publication until deploy. I recommend (a): the skill's whole point is that each number resolves to a permanent URL, and a Substack post with dead links is worse than one published a day later. |
| **26 of 98 regional banks have no ΔEVE found** — JavaScript-only or bot-blocking sites (Resona group, Yokohama FG, Kyushu FG among them). | **Disclose, don't extrapolate.** §7 states coverage as "72 of 98" and the scatter plots only those. The missing names are listed in the appendix so a reader can check their own. |
| **14 ΔEVE tables carry a flagged check** (a missing scenario row, an as-of read from the document rather than the table). | Shown with the flag visible, excluded from any median or count. Each is traceable to its PDF page and SHA-256. |
| **No share prices.** We hold none under licence. | **Drop the valuation angle entirely.** No "cheap versus expensive", no pair trade. The piece is about balance-sheet exposure. If you want the price overlay, that is your own data and your own call. |
| **Shinkin banks have no per-bank bond book** — they file no securities report. | Out of scope; noted once so a reader does not read "regional banks" as "all local lenders". |
| Prior session's claim that city-bank JGBs were "¥69tn in 2022" | **Re-derived, and it does not match:** the March 2022 figure is ¥75.7tn. The piece will use ¥75.7tn. Flagging because it shows the old note was not fully traceable. |

## 5. Out of scope

- Valuation, price performance, and any buy/sell view.
- Credit quality beyond citing the bad-loan ratio once as context.
- Shinkin banks and credit co-operatives at bank level.
- Interest-rate forecasting. The piece takes the curve as given and asks who is exposed.
- The megabanks, except as the contrast in §5.

## 6. What I need from you before Step 2

1. **Deploy first, or write against local?** (I recommend deploy first.)
2. **Format:** Markdown/Substack, Word, PowerPoint — or more than one?
3. **Length:** I suggest ~1,800 words and 5 charts for Substack; or 12 slides if it is a
   client deck.
4. **Does H4 hold your interest?** If the two rankings turn out to agree, §7 becomes a
   short null result and the piece leans on §6 instead. I will report that rather than
   bury it.
