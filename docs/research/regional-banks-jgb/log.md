# Research log — regional banks and JGB yields

Every call behind the piece, in order. `as_of=2026-09-23` on all of them, so each URL
keeps returning these numbers after the next release. A number that is not in
this table does not go in the draft.

| § | What | Call | Result |
| - | ---- | ---- | ------ |
| §2 | jgb-yields — 10Y, 5Y, 30Y | `https://ploveranalytics.com/api/v1/jgb-yields/observations?series=10Y,5Y,30Y&as_of=2026-09-23` | release 72 (Data through 17 September 2026), 13318 lines -> data/jgb-yields.csv |
| §3 | boj-loan-rates — DLLR2CICBNL1, DLLR2CIRBNL1, DLLR2CIMBNL1, DLLR2CICR35 | `https://ploveranalytics.com/api/v1/boj-loan-rates/observations?series=DLLR2CICBNL1,DLLR2CIRBNL1,DLLR2CIMBNL1,DLLR2CICR35&as_of=2026-09-23` | release 215 (Data through July 2026), 410 lines -> data/loan-rates.csv |
| §3 | boj-deposit-rates — DLDR121N, DLDR43TL61YN, DLDR43TL610YN | `https://ploveranalytics.com/api/v1/boj-deposit-rates/observations?series=DLDR121N,DLDR43TL61YN,DLDR43TL610YN&as_of=2026-09-23` | release 214 (Data through September 2026), 69 lines -> data/deposit-rates.csv |
| §4 | fsa-bank-results — regional.net-interest-income.fy, regional.bond-gains.fy, regional.core-net-business-profit.fy, regional.net-income.fy | `https://ploveranalytics.com/api/v1/fsa-bank-results/observations?series=regional.net-interest-income.fy,regional.bond-gains.fy,regional.core-net-business-profit.fy,regional.net-income.fy&as_of=2026-09-23` | release 51 (Data through March 2026), 38 lines -> data/regional-pl.csv |
| §5 | jba-banks — s.regional.d290, s.regional-2.d290, s.city.d290 | `https://ploveranalytics.com/api/v1/jba-banks/observations?series=s.regional.d290,s.regional-2.d290,s.city.d290&as_of=2026-09-23` | release 52 (Data through March 2026), 40 lines -> data/jgb-holdings-by-group.csv |
| §5 | jba-banks — s.regional.a015, s.regional.d395, s.regional.d275 | `https://ploveranalytics.com/api/v1/jba-banks/observations?series=s.regional.a015,s.regional.d395,s.regional.d275&as_of=2026-09-23` | release 52 (Data through March 2026), 63 lines -> data/regional-balance-sheet.csv |
| §6 | banks ranked by unrealised bond loss against net assets | `https://ploveranalytics.com/api/v1/equity/banks/ranking` | 86 rows -> data/rank-bond-loss-to-equity.csv |
| §6 | banks ranked by share of securities maturing beyond ten years | `https://ploveranalytics.com/api/v1/equity/banks/ranking` | 49 rows -> data/rank-over-10y-share.csv |
| §7 | rate risk in the banking book (IRRBB1) by institution, as each bank printed it | `https://ploveranalytics.com/api/v1/equity/banks/irrbb` | 71 rows -> data/irrbb-by-bank.csv |
| §4/§6 | group totals over clean filings | `https://ploveranalytics.com/api/v1/equity/banks/summary` | regional filers 74 (clean 67); AFS bond diff -3.415 tn; JGB -1.912 tn |
