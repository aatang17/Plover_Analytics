# Verification — BoJ rate increases and regional banks

**Result: 135 of 135 data figures in the draft traced to computed values; 0 unresolved.** The remaining 30 numeric tokens are dates, years and counts of the institution list (98 banks), checked by eye.

- Method: `verify.py` extracts every number in `draft.md` (above "Sources and method") and matches it against values computed by `stats.py` / `analysis.py` from the files logged in `log.md` (`data/numbers.json`, `data/bank_table.csv`). Trace in `data/verify_trace.json`.
- Independent recomputation of the headline sector figures from the MCP `get_series` output (jba-banks release "Data through March 2026"): first-tier FY2025 NII = 5,609,314 − 1,578,582 = ¥4,030.7bn; second-tier = 1,062,649 − 237,335 = ¥825.3bn. Both match the table.
- `check_claim` not applicable: it verifies cross-shareholding figures only.
- Totals reconcile: two-year extra NII 1,299.4 + 116.5 = 1,415.8; bond result −1,309.9 − 298.4 = −1,608.3; net −192.4 (¥bn). Source split of the 94-bank NII change: interest income +1,958.0 = loans 1,142.6 + securities 542.3 + deposits with banks 245.3 + other 27.9; interest expense +898.8 = deposits 803.5 + other 95.3; NII +1,059.2.
- Hypotheses: H1 disproven (40 of 92 net positive); H2 disproven, reversed (ρ = −0.26); H3 supported (28.0% vs 9.4%); H4 supported (overlap 2 of 10, ρ = 0.01). The draft reports all four as found.

## Stated limitations carried into the piece
Merged banks excluded (Aomori Michinoku, Aichi, Hachijuni; Nagano has no FY2025); 9 banks without an IRRBB1 table; Iwate as of March 2025; filer-level unrealised losses; JBA NII vs FSA 資金利益; shock sizes not restated (BIS PDF not readable by script); no share prices.

## Before publishing
- Production still serves the pre-correction IRRBB figures (`irrbb-1`). The corrected data must be live before the piece's links are frozen; set `as_of` to the day it is.
- The observations API silently keeps 8 series per request (`app/api.py:1809`); raise separately.
