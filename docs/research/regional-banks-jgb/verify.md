# Verification — regional banks and JGB yields

Every number in `draft.md`, re-derived from the CSVs in `data/` by an independent
pass. "Draft" is what the piece says; "data" is what the saved table gives. A row
only passes if they agree inside the tolerance for its precision.

Two figures were corrected at this stage: the median unrealised loss and the
median share beyond ten years had been taken across all bank types rather than
across regional banks only. Both are restated on the regional population.

| § | Figure | Draft | Data | Unit | Source | |
| - | ------ | ----: | ---: | ---- | ------ | - |
| §2 | 10Y JGB 29 Mar 2024 | 0.75 | 0.75 | % | `data/jgb-yields.csv` | ✅ |
| §2 | 10Y JGB 31 Mar 2026 | 2.366 | 2.366 | % | `data/jgb-yields.csv` | ✅ |
| §2 | 10Y JGB 17 Sep 2026 | 2.993 | 2.993 | % | `data/jgb-yields.csv` | ✅ |
| §2 | 10Y JGB high 15 Sep 2026 | 3.028 | 3.028 | % | `data/jgb-yields.csv` | ✅ |
| §2 | 30Y JGB above 4% | 4.047 | 4.047 | % | `data/jgb-yields.csv` | ✅ |
| §3 | regional new-loan rate Mar 2024 | 0.854 | 0.854 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | regional new-loan rate Jul 2026 | 1.752 | 1.752 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | change in loan rate | 0.9 | 0.898 | pp | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | ordinary deposit rate Mar 2024 | 0.002 | 0.002 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | ordinary deposit rate Sep 2026 | 0.322 | 0.322 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | change in deposit rate | 0.32 | 0.32 | pp | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | shinkin new-loan rate Jul 2026 | 2.194 | 2.194 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §3 | city new-loan rate Jul 2026 | 1.778 | 1.778 | % | `data/loan-rates.csv, data/deposit-rates.csv` | ✅ |
| §4 | FY2023 net-interest-income | 3884 | 3884 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2024 net-interest-income | 4237 | 4237 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2025 net-interest-income | 4975 | 4975 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2023 bond-gains | -477 | -476.7 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2024 bond-gains | -475 | -475 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2025 bond-gains | -1156 | -1156 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2023 core-net-business-profit | 1641 | 1641 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2024 core-net-business-profit | 1958 | 1958 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2025 core-net-business-profit | 2637 | 2637 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2023 net-income | 958 | 958.2 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2024 net-income | 1309 | 1309 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | FY2025 net-income | 1804 | 1804 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | NII two-year change | 1091 | 1091 | ¥bn | `data/regional-pl.csv` | ✅ |
| §4 | NII two-year growth | 28 | 28.1 | % | `data/regional-pl.csv` | ✅ |
| §4 | core profit two-year growth | 61 | 60.69 | % | `data/regional-pl.csv` | ✅ |
| §4 | net income two-year growth | 88 | 88.3 | % | `data/regional-pl.csv` | ✅ |
| §4 | FY2025 bond loss ÷ two-year NII gain | 106 | 105.9 | % | `data/regional-pl.csv` | ✅ |
| §5 | regional JGBs Mar 2022 | 15 | 15.02 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional JGBs Mar 2024 | 14.7 | 14.7 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional JGBs Mar 2026 | 20.8 | 20.76 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional-II JGBs Mar 2022 | 2.8 | 2.761 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional-II JGBs Mar 2024 | 3.1 | 3.061 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional-II JGBs Mar 2026 | 4.6 | 4.608 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | city JGBs Mar 2022 | 75.7 | 75.71 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | city JGBs Mar 2024 | 56.6 | 56.62 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | city JGBs Mar 2026 | 55.5 | 55.52 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional JGBs two-year | 41 | 41.27 | % | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional-II JGBs two-year | 51 | 50.52 | % | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | city JGBs two-year | -2 | -1.946 | % | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | city JGB cut since Mar 2022 | 20 | 20.19 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | regional JGB add, two years | 6 | 6.066 | ¥tn | `data/jgb-holdings-by-group.csv` | ✅ |
| §5 | loan-to-deposit Mar 2024 | 77.5 | 77.53 | % | `data/regional-balance-sheet.csv` | ✅ |
| §5 | loan-to-deposit Mar 2026 | 81.7 | 81.69 | % | `data/regional-balance-sheet.csv` | ✅ |
| §5 | loan growth, two years | 21.9 | 21.91 | ¥tn | `data/regional-balance-sheet.csv` | ✅ |
| §5 | deposit growth, two years | 9.9 | 9.855 | ¥tn | `data/regional-balance-sheet.csv` | ✅ |
| §6 | regional AFS bond loss | -3.42 | -3.415 | ¥tn | `data/summary.json` | ✅ |
| §6 | of which JGBs | -1.91 | -1.912 | ¥tn | `data/summary.json` | ✅ |
| §6 | median loss ÷ net assets, regional | 13.9 | 13.92 | % | `data/rank-bond-loss-to-equity.csv` | ✅ |
| §6 | regional filers in that median | 67 | 67 | banks | `data/rank-bond-loss-to-equity.csv` | ✅ |
| §6 | lowest loss ÷ net assets | 0 | 0 | % | `data/rank-bond-loss-to-equity.csv` | ✅ |
| §6 | The Shimane Bank | 58.2 | 58.23 | % | `data/rank-bond-loss-to-equity.csv` | ✅ |
| §6 | The Daito Bank | 58.1 | 58.1 | % | `data/rank-bond-loss-to-equity.csv` | ✅ |
| §6 | median over-10y share, regional | 21.2 | 21.25 | % | `data/rank-over-10y-share.csv` | ✅ |
| §6 | regional banks with a six-bucket ladder | 38 | 38 | banks | `data/rank-over-10y-share.csv` | ✅ |
| §6 | Jimoto over-10y share | 69.8 | 69.8 | % | `data/rank-over-10y-share.csv` | ✅ |
| §7 | institutions with a table | 71 | 71 | banks | `data/irrbb-by-bank.csv` | ✅ |
| §7 | Tochigi ΔEVE | 59 | 59.05 | ¥bn | `data/irrbb-by-bank.csv` | ✅ |
| §7 | Tochigi Tier 1 | 144.1 | 144.1 | ¥bn | `data/irrbb-by-bank.csv` | ✅ |
| §7 | Tochigi ΔEVE ÷ Tier 1 | 41 | 40.98 | % | `data/irrbb-by-bank.csv` | ✅ |
| §7 | banks on both measures | 58 | 58 | banks | `data/irrbb-by-bank.csv` | ✅ |
| §7 | Spearman, all pairs | 0.31 | 0.3061 |  | `data/irrbb-by-bank.csv` | ✅ |
| §7 | top-ten overlap, all pairs | 1 | 1 | names | `data/irrbb-by-bank.csv` | ✅ |
| §7 | banks on distinct documents | 48 | 48 | banks | `data/irrbb-by-bank.csv` | ✅ |
| §7 | Spearman, distinct documents | 0.31 | 0.3073 |  | `data/irrbb-by-bank.csv` | ✅ |
| §7 | top-ten overlap, distinct | 2 | 2 | names | `data/irrbb-by-bank.csv` | ✅ |
| §7 | above the Basel 15% line | 7 | 7 | banks | `data/irrbb-by-bank.csv` | ✅ |
| §7 | median ΔEVE ÷ Tier 1, paired | 7.6 | 7.595 | % | `data/irrbb-by-bank.csv` | ✅ |
| §7 | median accounting hole, paired | 14.6 | 14.6 | % | `data/irrbb-by-bank.csv` | ✅ |
| §7 | documents covering >1 institution | 8 | 8 | documents | `data/irrbb-by-bank.csv` | ✅ |
| §7 | distinct documents | 59 | 59 | documents | `data/irrbb-by-bank.csv` | ✅ |

**73 numbers traced, 0 unresolved.**

## Claim checks

The platform's `check_claim` tool covers cross-shareholding figures only, so it
does not apply to anything in this piece. Verification here is the re-derivation
above: each figure recomputed from the saved CSV by a second pass that does not
read the draft.

## Two corrections made during verification

1. **Median unrealised loss and median share beyond ten years** were first taken
   across all bank types. Restated on regional banks only: 13.9% of net assets
   (67 filers) and 21.2% of securities (38 filers).
2. **The worst-six table in §6** first ranked only the banks that also publish a
   rate-risk table, which dropped Tohoku and San-in Godo and split Fidea
   Holdings into its two banks. Restated across every regional filer.

Both were caught by re-deriving from the CSVs rather than by re-reading the
draft, which is the point of doing it this way.
