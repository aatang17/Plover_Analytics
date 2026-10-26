# AI Data Workspace: Send the Code to the Data

Status: Proposed. Prepared 12 October 2026. Scope: how the AI features handle data,
what moves into code, and the order of work. Related: [Studies workspace](PLAN-STUDIES-WORKSPACE.md),
which this plan supplies with its calculation engine.

## The Problem

A writer reported that combining two series into one chart through the Writer Desk's AI
tab, on their own API key, cost about $4. The same job in the Claude app costs them nothing
extra. The Claude app is a flat monthly plan, while an API key pays for every word the
model reads and writes (a "token").

The cost comes from how the AI tab works today (`app/research_ai.py`, `_run_api`):

1. **The model reads every number.** `get_series` returns the full history as text. US and
   Japanese 10-year yields since 2020 come to 69,564 characters, or about 23,000 tokens. (The
   AI tab cut each result at 24,000 characters, so the model actually got about 48,000
   characters and lost the rest. See point 4.)
2. **The model re-reads it on every step.** Each step sends the whole conversation again,
   with no shortening and no prompt caching. A run can take up to 20 steps.
3. **The model types the chart.** A live `chart` block takes series from one dataset only.
   So a chart that combines two datasets has to be a `datachart`, and the model types every
   row. For the yields chart that is 1,605 rows and 51,747 characters (about 17,000
   tokens). Typed output is the most expensive kind of token. It is also over the
   8,000-token cap on a single reply, so it takes at least three replies.
4. **It is not even complete.** `get_series` keeps only the latest 1,500 points, so the
   "since 2020" chart actually starts in July 2020 for Japan and September 2020 for the US.
   Worse, the AI tab then cut the text at 24,000 characters, which removes its end: for the
   10-year JGB the model saw readings only up to about October 2024, not August 2026.
   *Fixed 14 October 2026:* a result too long to send is now refused with a message pointing
   to `run_analysis`, never cut.

The Investment Assistant already avoids part of this. Its data results are cut to 8,000
characters before the model sees them, and `draw_chart` reads numbers from the full result
by reference (`app/assistant/charts.py`). The AI tab has neither.

Token figures in this plan are estimates at about one token per three characters, which
suits number-heavy text. Character counts were measured against the local server on
12 October 2026.

## The Idea

**The AI writes a short instruction, and our server runs it on the data.** The model sees
the instruction and a small result, never the full history. Charts, tables and CSVs are
filled from the full result by code.

This is how the writer already works in Python. AI writes the first version of a script,
the script runs on the data, and the writer tweaks it by hand. Hand edits cost no tokens at
all.

## What Stays With the AI and What Moves to Code

| The AI does | Code does |
| --- | --- |
| Understand the question | Fetch the series, at any length, through the same functions as `/api/v1` |
| Pick the datasets and series (search) | Line up dates, and convert daily to monthly or monthly to quarterly with a stated rule |
| Write the query (a few lines) | Every calculation: changes, growth rates, rebasing, spreads, ratios, real values, rolling averages |
| Read a small result (a few rows, a summary) | Statistics: averages, ranks, "highest since", correlation, lead and lag, regression, contributions |
| Write the words and choose what to show | Screens across hundreds of series or companies |
|  | Charts, tables and CSV exports, filled from the full result |
|  | Checking every number in a draft against the data |

## Features Covered by Code

Grouped by what a writer asks for.

1. **Getting data.** Any series or company table the person may read, any date range, with
   no 1,500-point limit. That includes official statistics, company filings and the
   person's own licensed vendor prices. Each input records its dataset, release and as-of
   date.
2. **Combining.** Join series from different datasets and markets, for example JGB and US
   Treasury yields, or Japan CPI and wages. Frequency changes are explicit: a flow is
   summed, a stock takes the period-end value, a rate takes the average.
3. **Transforming.** Year-on-year, month-on-month, annualised changes, rebasing to 100,
   differences in pp, spreads, ratios, shares of a total, deflating by CPI, currency
   conversion and rolling windows.
4. **Summarising.** Latest, minimum, maximum, average, percentile rank, "highest since",
   count of months above a level, breadth (the share of items rising).
5. **Relating.** Correlation, lead and lag, and simple regression. These are the methods in
   the Studies plan's relationship engine, so they are built once and shared.
6. **Screening.** Rank or filter hundreds of series or companies in one step, such as the
   CPI items with the fastest price rises, or the top 20 companies by ROE.
7. **Output.** A chart or table block drawn from the full result, CSV with its metadata
   header, and the analysis saved so it can be re-run or edited.
8. **Checking.** Compare every number in a draft with the data and list any that differ.

## How It Saves Tokens

Measured on 12 October 2026. "Today" means the AI tab on an API key.

| Task | Today | With the workspace |
| --- | --- | --- |
| Chart of US and Japan 10-year yields since 2020, with the spread | Reads ≈ 23,000 tokens of data and types ≈ 17,000 tokens of rows. The data is re-read on each later step, so ≈ 165,000 tokens in all. The chart still starts mid-2020. | ≈ 300-character call plus a summary of four numbers: ≈ 500 tokens. Full history from January 2020. |
| Share of the 589 CPI items rising faster than 2%, monthly for five years | The ready-made breadth tool (`get_breakdown`, cut `breadth`) does it in one call, but returns every month since 1971: 70,950 characters, ≈ 24,000 tokens, re-read on each later step. Without that tool, 99 calls of 6 series (≈ 9,600 characters each), ≈ 315,000 tokens. | One query that returns 60 monthly shares: ≈ 500 tokens. |
| "When was inflation last this high?" (CPI since 1946) | 22,482 characters, ≈ 7,500 tokens, then the model scans by eye | One query that returns one row: ≈ 50 tokens |
| Writer tweaks the chart: start in 2022, add 2-year yields | Repeats the first row: another ≈ 165,000 tokens | Edit one line and press Run: no AI, no tokens. Asking the AI to do it: ≈ 500 tokens. |

The ≈ 165,000 assumes seven steps: search, two data calls, read the draft, three replies
typing rows, and a closing reply. Each step from the draft read on carries the 23,000
tokens of data, plus the rows typed so far.

Here is the whole `run_analysis` call for the first example, about 300 characters. It uses
helpers from the appendix:

```
query:   SELECT date, japan, us, spread_pp(us, japan) AS spread
         FROM series_wide({'japan': 'jgb-yields:10Y', 'us': 'ust-yields:10Y'},
                          start := '2020-01-01', join := 'both')
chart:   line · japan, us · "US and Japan 10-year yields" · unit %
summary: latest, min, max, mean of spread
```

## How It Works

**One new AI tool, `run_analysis`.** The model sends a query and names the series it needs.
The server:

1. loads each series through the same functions that serve `/api/v1`, checking the
   person's access, including private vendor data;
2. puts them into a fresh in-memory DuckDB with no access to files, the network or the main
   databases;
3. runs the query in a separate worker process, with caps on time and memory;
4. returns to the model a short result (at most 50 rows or 8,000 characters, the cap the
   Assistant already uses), the row count, and a result number;
5. keeps the full result under that number, so `draw_chart`, a table block or a CSV can
   use it.

**The query language is SQL in DuckDB, plus a small set of helpers** (`yoy`, `rebase`,
`to_monthly`, `highest_since` and so on). DuckDB is already on the server, so this adds no
dependency, and it handles joins, lags, rolling windows and correlation. A check on
12 October 2026 confirmed that a locked DuckDB connection refuses file reads, file writes,
`ATTACH`, extension installs and attempts to unlock itself. A hosted Python sandbox comes
later, and only if people need it (Phase 3).

**Saved analyses.** Each run stores the query, the inputs with their releases and as-of
dates, and the result. A chart in a draft points to a run, so it never changes silently.
A re-run makes a new run, which matches how vintages work everywhere else.

**Editable by the writer.** An analysis appears in the draft's Research tab with its steps
in plain English, controls for the common changes, a Run button, and the result and chart
below. The writer can tweak and re-run without the AI.

**Download for Python.** One button downloads the data as CSV, the query, and a ten-line
Python script that runs it with DuckDB, for people who work in PyCharm.

## Users Never Need to Learn the Syntax

The helpers are for the AI, not for people. A writer works in three layers, and only the
last one shows any code.

1. **Ask in plain English.** "Chart US and Japan 10-year yields since 2020 and give me the
   spread." The AI writes the query.
2. **Read and change it in plain English.** Every helper has a fixed sentence, so each
   analysis is shown as numbered steps. For the yields example:

   > 1. US 10-year Treasury yield and 10-year JGB yield, daily from 1 January 2020.
   > 2. Keep the days both markets published a yield.
   > 3. Spread = US yield − Japan yield, in percentage points.
   > 4. Line chart of both yields; latest, lowest, highest and average spread.

   Each step has controls for the usual changes: a date picker, a series picker, a
   frequency menu, a chart-type switch. Changing a control rewrites the query, and the
   query re-runs without the AI.
3. **Show Code, for those who want it.** A link opens the query for hand editing, or to
   download it for Python. Anyone who uses SQL can read it, because the grammar is
   standard SQL. Only the helper names are ours, and each one links to its line in the
   appendix.

This sentence for each helper is also the formula that the trust contract requires under
"Show calculation". So writing it once serves both purposes.

## Rules It Must Keep

- **Same numbers as the API.** Data comes only through the functions behind `/api/v1`. The
  workspace never reads raw tables.
- **Trust contract.** Inputs keep their source lines. Every workspace output is a
  calculated figure: no badge, and its formula (the query) sits under "Show calculation"
  and in the CSV header.
- **Missing stays missing.** Helpers never fill gaps with zero. A query that turns missing
  values into zero is refused with a message saying why.
- **Each person's data stays theirs.** A run sees only what its person may read, and saved
  runs follow the account and publication rules in `CLAUDE.md`.
- **No writes.** The worker cannot change any database, and nothing is written to the
  serving process.
- **Double clicks.** Run sits behind the existing lock, so ten clicks make one run.

## Phases

### Phase 0: Quick fixes in the current AI tab

1. **Charts by reference, not typed.** Bring the Assistant's `draw_chart` and its 8,000-
   character result cap into the AI tab. Let a live `chart` block take series from
   different datasets (`dataset:code`), so combining two datasets no longer means typing
   rows.
2. **Prompt caching on the Anthropic API path.** `app/assistant/gateway.py` sends none
   today. With it, the context re-sent on each step is billed at Anthropic's reduced cached
   rate. Check the current price before quoting a saving.
   *Built 14 October 2026, not yet tried on a live key.* Every Anthropic request now asks for
   caching. Every AI-tab run, on any provider, counts the tokens it read (and how many came
   from the cache) and wrote. It keeps the count with its reply (`tokens` on the run), and
   Investment Assistant runs count cached tokens too.
3. **Point writers to their flat-rate plan.** The AI tab already accepts a Claude plan
   (through Claude Code) or a ChatGPT plan (through Codex). That changes who pays, not how
   many tokens are used, and the plan's usage limits still apply.

Exit condition: the yields chart in the first example, built on an API key, uses under
30,000 tokens, and no number in the chart passed through the model.

### Phase 1: `run_analysis` for the AI tab and the Assistant

*First version built 14 October 2026 (`app/analysis.py`, in the Writer Desk AI tab on every
route; not yet in the Investment Assistant).* It has the locked in-memory DuckDB, 13 helper
words, summaries (including "highest since") and charts written into the draft by code.
On the yields example, the model gets back 1,550 characters, while the 47,000-character
chart table goes into the draft without passing through it. Not yet done: a live A/B run
with a real model, the Investment Assistant, private vendor prices, and a plain-English
reading of each step.

Build the worker, the locked DuckDB, the helpers, saved runs and the short-result rule.
Build it once and give it to both the Writer Desk AI tab and the Investment Assistant.

*Studies built 15 October 2026 (uncommitted):* the Investment Assistant's new Studies
section (`#/studies`) asks the Study Analyst, which has `run_analysis` and no `get_series`,
and keeps every run's full result (`ia_studies`, `ia_study_runs`). A hand-written query runs
with no AI, and `/mcp/desk` offers `run_analysis`, `save_study`, `list_studies` and
`read_study` so a person's own Claude Code or Codex can do the same on their plan. Live
test on DeepSeek: the yields question in 8 seconds and 28,858 tokens (18,304 cached), the
"last this high" question in 39,379 (36,992 cached).

Exit condition: all four examples in the savings table complete within the "with the
workspace" figures, and their results match an independent calculation. A query cannot
read a file, reach the network, see another person's private data, or turn missing values
into zero.

### Phase 2: Hand editing and Download for Python

Add the analysis block in the Research tab: its plain-English steps with controls, Run,
the result and chart, Show Code, and the download.

Exit condition: a writer who has never seen the syntax changes the start date and adds a
series to a saved analysis, using only the controls and without the AI. The downloaded script runs on a clean machine and gives the same numbers.

### Phase 3: Hosted Python, only on demand

Offer Python only if users ask for work that SQL and the helpers cannot do. It would run in
a separate container with no network, read-only inputs, and caps on CPU, memory and time.
The Studies plan currently leaves an open notebook out of its first release. Keep it out
until a customer asks for one.

## Relationship to the Studies Plan

The Studies plan already requires that "the assistant receives permission-scoped results
and source references, rather than computing full histories in conversation". This plan is
how that happens. `run_analysis` and its saved runs serve as the engine for Studies, so the
relationship, transformation and frequency rules are written once.

## Open Decisions

1. Which AI surface the $4 run used: the AI tab on an API key is assumed. If it was
   the Investment Assistant or an outside tool over MCP, Phase 0 changes, but Phase 1 does
   not.
2. Limits on rows loaded, run time and memory. Set them from a benchmark on the largest
   real datasets, not in advance.
3. Whether MCP clients (Claude, ChatGPT) get `run_analysis` too. That would cut their
   token use the same way, but it opens server compute to outside callers, so it needs
   per-account limits first.

## Appendix: The Helper List

These are the words we add to standard SQL. Each one has a fixed formula and a plain-English
sentence (the "Reads as" column), which is shown to the writer and saved with the run.
`x`, `a` and `b` are series. Phase 1 ships groups A–F, H and I. Group G arrives with the
Studies relationship engine.

### A. Getting Data

| Helper | What it does | Reads as |
| --- | --- | --- |
| `series(id, start, end, as_of)` | One series, as published, with no point limit. `id` is `dataset:code`. | "{name}, {frequency} from {start}" |
| `series_wide({name: id, …}, start, end, join)` | Several series side by side, one row per date. `join` is `both` (dates where all have a value) or `any`. | "{names}, lined up by date" |
| `company(code, metric, basis)` | One company's figure over time, from its filings. `basis` is consolidated or parent. | "{company}: {metric}, {basis}" |
| `companies(dataset, metric, filter, as_of)` | The same figure for many companies at once. | "{metric} for {filter}" |
| `items(dataset, level)` | Every item in a breakdown, such as the CPI's priced items (codes not starting with 0), with their weights. | "All {level} items in {dataset}" |
| `vendor_prices(symbol)` | The person's own licensed market prices. Only their runs can use them. | "{symbol}, your {vendor} prices" |
| `as_of(date)` | Use the data as it stood on that date (a past vintage). | "As published on {date}" |

### B. Lining Up and Changing Frequency

| Helper | What it does | Reads as |
| --- | --- | --- |
| `to_monthly(x, rule)` | Daily or weekly to monthly. `rule` is `sum` (flows), `end` (stocks) or `average` (rates). There is no default, so the rule is always chosen. | "Monthly {rule} of {name}" |
| `to_quarterly(x, rule)` | Same, to calendar quarters | "Quarterly {rule} of {name}" |
| `to_annual(x, rule, year)` | Same, to calendar or Japanese fiscal years (April–March) | "{year} year {rule} of {name}" |
| `market_close(x, market)` | Daily market data cut at one market's close, so Tokyo and New York days line up honestly | "At the {market} close" |
| `period(x, start, end)` | Keep a date window | "From {start} to {end}" |

### C. Changes

The first three use the same formulas as the API, so a figure from the workspace matches the
one on the page.

| Helper | Formula | Reads as |
| --- | --- | --- |
| `yoy(x)` | (x[t] / x[t − 12 months] − 1) × 100 | "Change on a year earlier, %" |
| `mom(x)` | (x[t] / x[t − 1 month] − 1) × 100 | "Change on the month before, %" |
| `ann3m(x)` | ((x[t] / x[t − 3 months]) ^ 4 − 1) × 100 | "Three-month change, annualised, %" |
| `qoq(x)` and `qoq_ann(x)` | Quarter-on-quarter change, plain or annualised | "Change on the quarter before, %" |
| `change(x, n)` | x[t] − x[t − n], in the series' own unit | "Change over {n} periods" |
| `pct_change(x, n)` | (x[t] / x[t − n] − 1) × 100 | "% change over {n} periods" |
| `diff_pp(x, n)` | Change in a rate, in percentage points, never % | "Change over {n} periods, pp" |
| `cagr(x, years)` | Average yearly growth rate over the span | "Average yearly growth, %" |
| `log_return(x)` | 100 × ln(x[t] / x[t − 1]) for market prices | "Log return, %" |

### D. Levels and Combinations

| Helper | What it does | Reads as |
| --- | --- | --- |
| `rebase(x, period)` | Rescale so the chosen period = 100 | "Rebased, {period} = 100" |
| `spread_pp(a, b)` | a − b, for two rates, in pp | "{a} minus {b}, pp" |
| `ratio(a, b)` | a / b | "{a} divided by {b}" |
| `share(part, total)` | part / total × 100 | "{part} as % of {total}" |
| `real(x, deflator, base)` | Value adjusted for inflation by a price index | "{name} in {base} prices, using {deflator}" |
| `convert(x, fx)` | Change currency using a stated exchange-rate series, date by date | "In {currency}, at {fx}" |
| `per_person(x, population)` | Divide by a population series | "Per person" |
| `cumulative(x)` | Running total, for flows only | "Running total of {name}" |
| `rolling_mean(x, n)` and `rolling_sum(x, n)` | Average or total over the last n periods. Blank if any of them is missing. | "{n}-period average of {name}" |

### E. Summaries

| Helper | What it gives | Reads as |
| --- | --- | --- |
| `latest(x)` | Last value and its date | "Latest" |
| `min_of(x)`, `max_of(x)`, `mean_of(x)`, `median_of(x)` | Over the window, with dates for min and max | "Lowest / highest / average / median" |
| `percentile(x)` | Where the latest value ranks in its own history, 0–100 | "Higher than {p}% of past readings" |
| `highest_since(x)` and `lowest_since(x)` | The last date with a value this high (or low) | "Highest since {date}" |
| `count_above(x, level)` | How many periods were above a level | "Months above {level}" |
| `streak(x, rule)` | How many periods in a row met a rule, such as "above 2%" | "{n} months in a row {rule}" |
| `gaps(x)` | Which dates are missing, so they can be disclosed | "Missing: {dates}" |

### F. Many Series at Once

| Helper | What it does | Reads as |
| --- | --- | --- |
| `breadth(items, rule)` | Share of items meeting a rule, each period | "Share of items {rule}" |
| `top(x, n, by)` and `bottom(x, n, by)` | The n highest or lowest | "Top {n} by {by}" |
| `rank(x, by)` | Rank within the group | "Rank by {by}" |
| `weighted_mean(x, weights)` | Average using published weights. CPI weights are parts per 10,000, never %. | "Weighted average, weights per 10,000" |
| `contributions(items, total)` | Each item's share of the headline change. A residual is always shown, never hidden. | "Contribution to {total}, pp; residual {r}" |

### G. Relationships (with the Studies engine)

| Helper | What it does | Reads as |What
| --- | --- | --- |
| `corr(a, b)` | Correlation over the window, with the number of observations | "Correlation, {n} observations" |
| `rolling_corr(a, b, n)` | Correlation over a moving window | "{n}-period rolling correlation" |
| `lead_lag(a, b, max)` | Correlation at each delay from −max to +max | "Correlation when {a} leads by k periods" |
| `regress(y, xs)` | Straight-line fit of y on one or more series, with the usual checks | "Estimated association of {y} with {xs}" |
| `beta(a, market)` | Sensitivity of one return to another | "Beta to {market}" |

### H. Output

Output goes in the `run_analysis` call next to the query, not inside it.

| Field | What it does |
| --- | --- |
| `chart` | Line, bar or scatter. Series, title and unit. Drawn from the full result, with source lines and the formula attached. |
| `table` | A table block in the draft from the result, with its own source line |
| `csv` | The full result as a download, with the metadata header |
| `summary` | The few numbers sent back to the AI, from group E |

### I. Checks

| Helper | What it does |
| --- | --- |
| `check(value, x, date)` | Compare a number written in the draft with the data, and report a match or the correct figure |
| `check_draft()` | Run `check` on every number in the draft that came from an analysis |

### Refusals Built Into the Helpers

These come from the project rules. A helper stops with a plain message rather than produce
a misleading number.

- Mixing measure types (an index with a yen level, a stock with a flow) in one sum, ranking
  or chart axis.
- `pct_change` on a series that is already a rate (use `diff_pp`), or on a flow that goes
  negative.
- Changing frequency with no rule chosen.
- Filling a missing value with zero, by any route.
- Weights given in percent where parts per 10,000 are expected.

## Appendix: Ten Worked Examples

Each example shows the question as a writer types it and the steps the writer sees. It
also shows the call the AI writes and what goes back to the AI. Series codes are real
codes on the platform, checked on 13 October 2026. Example 5 uses group G, so it waits for
the Studies relationship engine. The rest work in Phase 1.

### 1. "Is Japan's core inflation now higher than America's?"

Steps the writer sees:
1. Japan CPI, all items less fresh food and energy; US CPI, all items less food and energy.
   Monthly from January 2015. *The two cores exclude slightly different things.*
2. Change on a year earlier, % (each).
3. Gap = Japan − US, pp.
4. Line chart of both rates. Latest gap, and how many months in a row Japan has been above.

```
query:   SELECT date, yoy(jp) AS japan, yoy(us) AS us, spread_pp(yoy(jp), yoy(us)) AS gap
         FROM series_wide({'jp': 'cpi-jp:0178', 'us': 'cpi-us:CUUR0000SA0L1E'},
                          start := '2015-01-01', join := 'both')
chart:   line · japan, us · unit %
summary: latest of gap; streak of gap > 0
```

Back to the AI: three numbers and a date. Code helps because the AI never sees the 280 or so
monthly values. It writes from the gap and the streak.

### 2. "Which items are driving Japanese inflation?"

Steps:
1. All 589 priced CPI items with their weights (per 10,000), latest month.
2. Each item's contribution to the headline change on a year earlier, pp.
3. The ten largest, the rest as one line, and the residual.

```
query:   SELECT * FROM top(contributions(items('cpi-jp-items', 'leaf'), total := 'cpi-jp-items:0001'),
                           10, by := 'contribution_pp')
table:   item, weight, yoy, contribution_pp
summary: sum of top 10; all others; residual
```

Back to the AI: 13 rows. Today a ready-made tool (`get_breakdown`, cut `contributions`)
does it in one call, but returns 210,891 characters (≈ 70,000 tokens), re-read on each
later step.

### 3. "How broad is inflation?"

Steps:
1. All 589 priced CPI items, monthly for five years.
2. Change on a year earlier, % (each item).
3. Share of items rising faster than 2%, each month.
4. Line chart. Latest share, and highest since when.

```
query:   SELECT date, breadth(yoy(items('cpi-jp-items', 'leaf')), '> 2') AS share_above_2
         FROM period(start := '2021-08-01')
chart:   line · share_above_2 · unit % of items
summary: latest; highest_since
```

Back to the AI: 2 numbers. Today the ready-made breadth tool answers this in one call, but
sends every month since 1971 (≈ 24,000 tokens). A ready-made tool works only for questions
we built one for. Change the question (items above 5%, or services only) and today's route
is 99 calls and ≈ 315,000 tokens.

### 4. "When was the 10-year JGB yield last this high?"

Steps:
1. 10-year JGB yield, daily, full history (from July 1986: 9,927 readings).
2. The last date before today with a yield at or above today's.

```
query:   SELECT latest(x), highest_since(x) FROM series('jgb-yields:10Y') AS x
summary: both
```

Back to the AI: one row. Today the AI sees only the latest 1,500 readings, about six
years. It cannot answer any "since" question that reaches further back.

### 5. "Does Tokyo CPI tell us national CPI early?" (group G, later)

Steps:
1. Tokyo CPI and national CPI, all items less fresh food, monthly from 2006.
2. Change on a year earlier, % (each).
3. How closely the month-to-month moves match, and how often they move in the same
   direction.

```
query:   SELECT corr(diff_pp(yoy(tokyo), 1), diff_pp(yoy(japan), 1)) AS corr_moves,
                share(count_where(sign(diff_pp(yoy(tokyo),1)) = sign(diff_pp(yoy(japan),1))),
                      count(*)) AS same_direction
         FROM series_wide({'tokyo': 'cpi-tokyo:0161', 'japan': 'cpi-jp:0161'},
                          start := '2006-01-01', join := 'both')
summary: both, with the number of months used
```

Back to the AI: 3 numbers. The step list says this is an association, not a forecast
test. A forecast test needs the Studies "Validate" step.

### 6. "How much has the BOJ's JGB pile shrunk from its peak?"

Steps:
1. Bank of Japan JGB holdings, monthly, ¥100 million, shown in ¥ trillion.
2. Peak and its month. Latest. Change from the peak, ¥ trillion and %.
3. Line chart.

```
query:   SELECT max_of(x), latest(x), change_from_peak(x), pct_from_peak(x)
         FROM series('boj-assets:MA03021034S') AS x
chart:   line · x · unit ¥ trillion
summary: all four
```

Back to the AI: 4 numbers with dates. The unit is converted once, by code, and stated.
This is the scaling the project rules call a recurring bug class.

### 7. "Which companies have raised their dividend every year for five years?"

Steps:
1. Dividend per share for every company filing an annual report: the five-year summary
   in its latest filing.
2. Keep companies whose dividend rose in each of the last five years.
3. List them with the latest dividend and five-year growth per year.

```
query:   SELECT code, name, latest(dps), cagr(dps, 5)
         FROM companies('financials', 'dps')
         WHERE streak(dps, 'rising') >= 5
         ORDER BY cagr(dps, 5) DESC
table:   all rows (to the draft or CSV)
summary: count; first 20
```

Back to the AI: a count and 20 rows. The full list goes to the table or CSV without
passing through the AI. Today a screen returns at most 50 rows, so a five-year test
across all filers cannot be done.

### 8. "Are foreigners net buyers of Japanese stocks this year?"

Steps:
1. TSE Prime, foreigners' purchases and sales, ¥, weekly from 1 January this year.
2. Net buying = purchases − sales (a flow, which can be negative).
3. Running total since 1 January, ¥ billion.
4. Bar chart of weekly net buying with the running total as a line.

```
query:   SELECT date, net, cumulative(net) AS ytd
         FROM (SELECT date, buy - sell AS net
               FROM series_wide({'buy': 'investor-flows-jp:prime.foreigners.purchases.value',
                                 'sell': 'investor-flows-jp:prime.foreigners.sales.value'},
                                start := '2026-01-01', join := 'both'))
chart:   bars · net; line · ytd · unit ¥ billion
summary: latest ytd; weeks of net buying; weeks of net selling
```

Back to the AI: 3 numbers.

### 9. "Where do Japan's chip exports go, and which destinations are growing fastest?"

Steps:
1. Exports of semiconductors and electronic components, by destination (202
   destinations), value, monthly.
2. Last 12 months and the 12 months before, per destination.
3. Growth, %, and share of the total of all destinations, %.
4. The ten largest destinations.

```
query:   SELECT * FROM top(
           SELECT destination, rolling_sum(x, 12) AS last12, pct_change(rolling_sum(x, 12), 12) AS growth,
                  share(rolling_sum(x, 12), total_of(rolling_sum(x, 12))) AS share
           FROM items('trade-semis', 'exp.70323000.*.val') AS x WHERE date = latest_date(),
           10, by := 'last12')
table:   destination, last12, growth, share
summary: share of the top 3; fastest grower
```

Back to the AI: 10 rows. Today a ready-made tool (`get_breakdown`, cut `trade`) returns
this commodity's trade with every partner in one call: 506,662 characters (≈ 170,000
tokens). The total is the sum of
the destinations as published, and the step list says so.

### 10. "Are banks earning more on loans than they pay on deposits as rates rise?"

Steps:
1. Average rate on new loans, domestically licensed banks, %; time deposits, 1 year,
   ¥10 million or more, %. Monthly from 2015.
2. Gap = loan rate − deposit rate, pp.
3. Line chart of both rates and the gap. Latest gap, change over 12 months, and the lowest
   gap with its date.

```
query:   SELECT date, loan, deposit, spread_pp(loan, deposit) AS gap
         FROM series_wide({'loan': 'boj-loan-rates:DLLR2CIDBNL1',
                           'deposit': 'boj-deposit-rates:DLDR43TL41YN'},
                          start := '2015-01-01', join := 'both')
chart:   line · loan, deposit, gap · unit %
summary: latest gap; diff_pp(gap, 12); min_of(gap)
```

Back to the AI: 3 numbers. `join := 'both'` stops at July 2026, the last month both
series cover; deposit rates run two months further. The step list says so, rather than
leave the chart's end looking ragged.

### What the Examples Show

- The AI only ever receives 1 to 20 rows. Everything else goes straight into the chart,
  table or CSV.
- Two of the ten (4 and 7) cannot be done properly today at any price. They hit the
  1,500-point cap or the 50-row screen cap.
- Examples 2, 3 and 9 work today only because a ready-made tool exists for that exact
  question. Each returns far more than the answer needs: ≈ 24,000 to ≈ 170,000 tokens per
  call. The workspace fixes both problems: any question, and only the answer comes back.
- Three helpers appear above that the list does not have yet: `change_from_peak`,
  `pct_from_peak` and `count_where`. Add them to group E, or drop them in favour of plain
  SQL.
