# Verification — 10-year JGB yield against bank loan rates, 30 September 2026

Files, all saved from `https://ploveranalytics.com/api/v1/...&format=csv` on 2026-09-30:

- `data/10y.csv` — jgb-yields, series 10Y, daily from 1974-09-24. Release: data through
  29 September 2026; sha256 `88e64a04…a368a5b0cc`. 9,947 rows, no blanks.
- `data/loan-rates.csv` — boj-loan-rates, DLLR2CIDBST1 (outstanding loans, total,
  domestically licensed banks), DLLR2CIDBNL1 (new loans, total), DLLR2CIDBNL3 (new loans,
  long-term; not used in the post). Monthly from 1993-10. Release: data through July 2026.
- `data/deposit-rates.csv` — boj-deposit-rates, DLDR43TL610YN (time deposits, 10 years,
  under ¥3 million). Release: data through September 2026.
- `data/10y-vs-loan-book.csv` — built from the first two: the 10Y value on the last
  trading day of each month beside DLLR2CIDBST1. No value recomputed. The chart reads this.

"Month-end 10Y" below means the last trading day of the month in `data/10y.csv`.

| # | Number in the post | Where it comes from | Match |
|---|---|---|---|
| 1 | 10Y 3.082% on 29 Sep 2026 | 10y.csv 2026-09-29 = 3.082 | yes |
| 2 | Loan-book rate 1.483%, July 2026, latest month | loan-rates.csv 2026-07 DLLR2CIDBST1 = 1.483; last row | yes |
| 3 | "More than double" / "twice" | 3.082 ÷ 1.483 = 2.08 (calculated) | yes |
| 4 | Above the loan-book rate at every month-end since April 2024 | 10y-vs-loan-book.csv: 10Y > loan_book in every row 2024-04 … 2026-07; 2024-03 is not | yes |
| 5 | A run of 28 months | count of those rows = 28 | yes |
| 6 | 394 months since October 1993 | rows with both values, 1993-10 … 2026-07 = 394 | yes |
| 7 | Above in only 62 of them | rows where 10Y > loan_book = 62 (earlier runs: 1995-12–1997-02, 1997-04–06, 2004-07, 2006-03–07, 2023-10) | yes |
| 8 | Gap 1.318 pp in July 2026 | 2.801 − 1.483 = 1.318 (calculated) | yes |
| 9 | Widest in the series | max of (10Y − loan_book) over all 394 rows is 2026-07 | yes |
| 10 | Gap 0.422 pp a year earlier | 2025-07: 1.559 − 1.137 = 0.422 (calculated) | yes |
| 11 | Loan-book rate 1.137% → 1.483%, +0.346 pp | loan-rates.csv 2025-07 = 1.137, 2026-07 = 1.483; difference calculated | yes |
| 12 | Month-end 10Y 1.559% → 2.801%, +1.242 pp | 10y.csv 2025-07-31 = 1.559, 2026-07-31 = 2.801; difference calculated | yes |
| 13 | New lending at 1.706% in July | loan-rates.csv 2026-07 DLLR2CIDBNL1 = 1.706 | yes |
| 14 | 1.095 pp below the 10Y at that month-end | 2.801 − 1.706 = 1.095 (calculated) | yes |
| 15 | 10-year time deposit, under ¥3 million, 0.927% in September | deposit-rates.csv 2026-09 DLDR43TL610YN = 0.927 | yes |
| 16 | Highest daily close since 27 Aug 1996, 3.179% | scan of every row before 2026-09-24: last value ≥ 3.082 is 1996-08-27 = 3.179 | yes |
| 17 | First closed above 3% on 2 Sep 2026 | first 2026 row ≥ 3 is 2026-09-02 = 3.006; the last before it is 1996-09-06 = 3.06, with 0 of the 7,347 rows in between at or above 3 | yes |

Added with the implications (third draft). `data/jba-banks.csv` — jba-banks, all banks
aggregate: s.all.d395 (loans), s.all.a015 (deposits), s.all.f850.fy (profit, fiscal
year). Release: data through March 2026. `data/deposit-rates.csv` also carries DLDR121N
(ordinary deposits).

| # | Number in the post | Where it comes from | Match |
|---|---|---|---|
| 18 | Bank loans outstanding ¥758.3 trillion | jba-banks.csv 2026-03 s.all.d395 = 758,282,558 ¥mn | yes |
| 19 | Gap × loans ≈ ¥10.0 trillion a year | 1.318% × ¥758.28tn = ¥9.99tn (calculated) | yes |
| 20 | Each 0.1 point on the loan book ≈ ¥0.76 trillion | 0.1% × ¥758.28tn = ¥0.758tn (calculated) | yes |
| 21 | Net income ¥6.03 trillion, year to March 2026 | jba-banks.csv 2026-03 s.all.f850.fy = 6,029,540 ¥mn | yes |
| 22 | Deposits ¥1,084.7 trillion | jba-banks.csv 2026-03 s.all.a015 = 1,084,681,239 ¥mn | yes |
| 23 | "Less than about 70%" break-even | loans ÷ deposits = 758.28 ÷ 1,084.68 = 69.9% (calculated) | yes |
| 24 | Ordinary deposit rate 0.183% → 0.255%, +0.072 points | deposit-rates.csv DLDR121N 2025-07 = 0.183, 2026-07 = 0.255 | yes |
| 25 | "About one-fifth" of the loan-book increase | 0.072 ÷ 0.346 = 20.8% (calculated) | yes |
| 26 | 10Y 2.366% on 31 March 2026 | 10y.csv 2026-03-31 = 2.366 | yes |
| 27 | Risen 0.716 points since | 3.082 − 2.366 = 0.716 (calculated) | yes |

Rows 1–17 still hold; the post no longer prints row 3's "twice", row 17's 2 September
date, or the 27 August 1996 value, and says "highest since August 1996" (row 16).

What the implications rest on, and their limits:
- The ¥10.0tn is the gap applied to the whole loan stock. It is a measure of scale, not a
  forecast: the post says so. Loans are at March 2026, the gap at July 2026.
- The 70% break-even treats all loans and all deposits as repricing alike and ignores
  securities income and non-deposit funding. It is the first-order arithmetic only.
- The one-fifth pass-through uses the ordinary deposit rate. Time deposit rates rose
  more (10-year: 0.483% → 0.741% over the same twelve months), so the true average
  pass-through is higher than one-fifth. We hold no deposit mix to weight them.
- "Bond valuation losses will have grown" is direction only. No size is given because we
  hold no September balance-sheet data.

Result: 27 numbers traced, 0 unresolved.

Chart labels: 3.08 is row 1 and 1.48 is row 2, rounded to two decimals. Both lines are
interest rates in %, the same measure type. The loan line ends at July 2026 and the yield
line at September 2026; the two missing loan months are left as a gap.

Links opened: rates.html returned 200; the four `as_of=2026-09-30` CSV links returned
the same last rows as the saved files.

Basis notes:
- The 10Y is the Ministry of Finance's daily constant-maturity closing figure; intraday
  highs and single-bond yields quoted in the press can differ.
- The headline compares a September yield with a July loan rate, because July is the
  latest loan month published. The post says so in its first sentence.
- The loan-book average covers all maturities, including short-term and floating-rate
  loans. The post states that the comparison is not like-for-like on maturity.
