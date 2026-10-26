# Research log — BoJ rate increases and regional banks

All retrievals 2026-09-24 unless stated. Files in `data/`.

| # | Section | Call | Citable URL | Vintage / file |
|---|---|---|---|---|
| 1 | §2 | BoJ Time-Series API, FM01 `STRDCLUCON` (uncollateralised overnight call rate, daily average), from 2022-01 | `https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=FM01&code=STRDCLUCON&startDate=202201` | daily to 2026-09; `boj_fm01_strdclucon.json` sha256 `c97a2d1efd5fe5b62cb4a9a64941751315b60106de2a9305259ceea1634d6ca0`; monthly means → `call_rate_monthly.csv` (calculated) |
| 2 | §2 | `boj-loan-rates` DLLR2CIRBNL1 (new loans, total, regional banks), DLLR2CICBNL1 (city), DLLR2CIMBNL1 (regional II), from 2023-01 | `https://ploveranalytics.com/api/v1/boj-loan-rates/observations?series=DLLR2CIRBNL1,DLLR2CICBNL1,DLLR2CIMBNL1&start=2023-01&format=csv` | Data through July 2026 → `loan-rates.csv` |
| 3 | §2 | `boj-deposit-rates` DLDR121N (ordinary deposits, posted), from 2023-01 | `https://ploveranalytics.com/api/v1/boj-deposit-rates/observations?series=DLDR121N&start=2023-01&format=csv` | Data through September 2026 → `deposit-rates.csv` |
| 4 | §3–5 | `jba-banks`, 98 regional banks + `regional`, `regional-2` aggregates; lines e020, e350, e230, e240, e610, e620, e630 (.fy); d395, a015, b950; e030, e040, e090, e360 (.fy); d035, d990 — from 2022-03 | `https://ploveranalytics.com/api/v1/jba-banks/observations?series=s.{bank}.{line}&start=2022-03&format=csv` | release "Data through March 2026", source sha256 `b4e39587…`; per-bank CSVs with full headers `jba/*.g{0,1,2}.csv` → `jba_banks_long.csv` |
| 5 | §6 | Local API on the corrected equity database (`irrbb-2`, 2026-09-24): `/api/v1/equity/banks/irrbb?basis=consolidated` and `?basis=non-consolidated` | production URL `https://ploveranalytics.com/api/v1/equity/banks/irrbb` once deployed | `irrbb_consolidated.json`, `irrbb_nonconsolidated.json` (89 banks) |
| 6 | §5 | Local API `/api/v1/equity/banks/companies?limit=500` (unrealised AFS bond losses, filer level) | `https://ploveranalytics.com/api/v1/equity/banks/companies` | `bank_companies.json` (91 filers) |
| 7 | §6 | BCBS IRRBB recalibration page (implementation by 1 January 2026) | `https://bis.org/bcbs/publ/d578.htm` | opened; the per-currency shock table is in the PDF, which could not be read automatically — **shock sizes are not quoted** |
| 8 | §6 | `https://www.bis.org/bcbs/publ/d368.pdf` | — | returned an HTML page instead of the PDF (site refused scripted download); not used |
| 9 | method | JBA archive `observatory/data/raw/jba-banks-20260922T164357Z-b4e39587168f.json`, editions 2024-interim to 2025-terminal, shared strings searched for 合併/経営統合/承継/事業譲渡/商号 | — | no merger notes in text form; mergers identified from the deposit series instead (#10) |

## Traps met, and what was done

10. **Mergers.** Deposits (a015) jump +75.0% at 2025-03 for 青森みちのく (0117) and +50.1% for あいち (0542); 長野 (0533) stops after 2025-09 and 八十二 (0143) rises +10.1% at 2026-03. All three excluded from growth rankings; 長野 has no FY2025. Other half-year moves above 8% (京都 +8.2%, 山陰合同 +8.4%, 東京スター +8.3%/+18.7%, 名古屋 +10.4%, 福岡中央 +8.4%, 長崎 +8.9%) have no disappearing counterpart and are kept.
11. **The observations API keeps at most eight series per request and drops the rest without an error** (`app/api.py:1809`, `[:8]`). The first pull lost deposits and net assets for every bank; re-pulled in groups of ≤8. Product defect to raise separately.
12. **A series a bank never reports makes the whole request 404.** Those groups were re-fetched line by line; absent lines recorded in `jba_absent_lines.json` (mostly e240 償還益, e620 償還損, e630 償却). In the bond sum an absent line contributes nothing; this is disclosed in the method note.
13. **ΔNII sign.** The FSA template prints a *fall* in income as positive (stated in 福岡銀行's note: 「ΔNIIのプラス表示は期間収益の減少」, and in one other bank's archive). Patterns across banks (up, down): (+,+) 45, (−,+) 17, (0,+) 11, (+,0) 8, (+,−) 7 — consistent with that convention; ΔNII is reported with the convention stated and not ranked as a headline measure, since each bank's deposit model differs.
14. **Largest ΔEVE is often a falling-rate scenario.** 栃木's 40.98% of Tier 1 is from parallel down; its parallel-up ΔEVE is 0.42%. §6 therefore uses the parallel-up row for "the next increase".
15. **Plan deviation (recorded):** Chart 3 plots deposits with banks as a share of assets rather than the loan-to-deposit ratio, because H2 reversed (ρ = −0.26) and the due-from-banks share carries the relationship (ρ = 0.57). The loan-to-deposit result is still reported in the text.
