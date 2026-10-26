# Low-Token Research: Tokens for Judgement, Code for Numbers

Status: Proposed. Prepared 15 October 2026. Builds on
[PLAN-AI-DATA-WORKSPACE.md](PLAN-AI-DATA-WORKSPACE.md) (the query tool, built) and the Studies
section (built 15 October 2026). Scope: how a person does research and analysis on Plover with
as few AI tokens as possible, and the options for getting there.

## The Rule

**The AI is paid to understand and to write. Code is free and does the numbers.** Every design
choice below follows from that: the AI should see the question, a short list of what the data
holds, and a small result. It should never see a history, a table or a catalogue it does not
need, and it should never be asked twice for the same thing.

## Where the Tokens Go Today

Measured on 15 October 2026, Studies, the question "How far apart are the US and Japanese
10-year yields since 2020, and when was the gap widest?": 28,327 tokens in, 531 out, three
steps (two code lookups, one analysis, then the answer).

| What was sent | Share | Why |
| --- | --- | --- |
| A description of all 62 datasets, inside the instructions | 75% | Sent on every step, to every specialist |
| Descriptions of the four tools | 17% | 6,500 characters; the query tool alone is 2,700 |
| Role, rules and the question | 5% | |
| The data: two lookups and the analysis preview | 3% | Already small, because the server did the work |

Each step re-sends everything before it, so three steps cost three copies of the fixed part.
The old route, the AI reading the series and typing the chart, was about 165,000 tokens by
estimate. Caching already bills about 65% of today's tokens at the cheaper cached rate.

## The Framework: Four Tiers

Work is routed to the cheapest tier that can do it. A higher tier is used only when the one
below cannot answer.

| Tier | Who does the work | Tokens per question | What it covers |
| --- | --- | --- | --- |
| **0 · No AI** | Code and the person | 0 | Ready-made analyses chosen from a list; re-running a saved study; changing a date, series or measure; scheduled refreshes; downloading data and script to run in Python locally |
| **1 · One call** | AI translates, code runs | ≈ 1,000–2,000 | The question matched to a ready-made analysis and its settings, in one AI call with no tool loop; the server has already found the likely series |
| **2 · Short loop** | AI writes a query | ≈ 3,000–5,000 | Anything the ready-made list does not cover: the Study Analyst with a lean prompt, the series found in advance, at most three steps |
| **3 · Heavy** | AI writes a script | 10,000 and up | Work SQL cannot do: event studies across thousands of stocks, regressions with diagnostics. Python in a sandbox. Budgeted, and the cost shown before it runs |

Three rules hold in every tier:

- **Results by reference.** The server keeps every row; the AI gets a preview and the figures
  it asked for. Charts, tables and CSVs are drawn by code. (Built.)
- **Nothing is asked twice.** A study is saved with its query; the same question for another
  series, period or measure is a Tier 0 change. Past studies are searched before the AI is
  called.
- **Every run shows its token count**, and a person can set a budget per study. (Count built;
  budget not.)

## Cross-Cutting Optimisations

These apply to every tier that uses the AI. Savings are estimates from the measured character
counts, not live runs, unless marked.

1. **A lean prompt per role.** The Study Analyst does not need the 62-dataset description:
   it finds codes with search and can call list_datasets when it must. Dropping it saves
   about 21,000 of the 28,000 tokens. Its instructions also say "then get_series", a tool it
   deliberately does not have; that line goes.
2. **Compact tool descriptions.** Halve them: the helper list and the worked example move
   into a reference the AI can fetch once (a `help` tool), not text sent on every step.
   Saves about 2,500.
3. **Find the series first.** Before the AI is called, the server searches the question's
   nouns ("US 10-year", "Japanese 10-year", "core CPI") and hands it the likely codes. One
   step fewer, so one full re-send fewer: about 30% of what remains.
4. **Stable prefix, volatile tail.** Keep the fixed part identical across calls (rules, tools)
   and put what changes (today's date, the coverage list, the question) at the end, so the
   provider's cache reads the fixed part every time. Caching is already switched on.
5. **One shot where a loop is not needed.** For Tier 1, ask the AI for a structured answer
   (which analysis, which series, which dates) in a single call with no tools.
6. **Right-size the model.** Writing SQL from a question is a job for a cheaper model. Let the
   person choose a Studies model separately from their chat model, as the Assistant already
   does for monitors. Prices from the Claude API reference cached on 25 September 2026:
   Haiku 4.5 is half the input price of Sonnet 5.5 and a fifth of Opus 5.5.
7. **Cap the loop.** At most five steps for Tier 2; a failed query is retried once with the
   error shown, then the person is told what went wrong.
8. **Keep AI text short.** Ask for four sentences at most; the chart and figures carry the
   rest. Output tokens cost about five times input.

## Options

| | Option | Cuts | Effort | Recommend |
| --- | --- | --- | --- | --- |
| A | **Trim the Study Analyst** (optimisations 1–3, 7, 8) | ≈ 90% on AI-asked studies: 28,000 → ≈ 3,000 | Small: a day | Yes, first |
| B | **Ready-made analyses** (Tier 0 and Tier 1): the thirteen common question shapes from the earlier survey (latest, trend, compare, gap, highest-since, contributions, breadth, ranking, peer comparison, running total, share, revisions, fact-check) as a list with pickers; the AI only matches a typed question to one | Most questions to 0 or ≈ 1,500 | Medium: a week | Yes, second |
| C | **Model choice for Studies** (optimisation 6) | Half again, or more, on whatever is left | Small: hours | Yes, with A |
| D | **Reuse library**: search past studies and shared templates before calling the AI; "run this for another series" as one click | Repeat questions to 0 | Medium | Later, once there are studies to reuse |
| E | **Local execution**: Download for Python (data, query and a ten-line script) and scheduled re-runs | 0 for anything run locally or on a schedule | Small to medium | Yes for Gordon's workflow |
| F | **Python sandbox** (Tier 3) with a shown budget | Enables the heavy work; does not cut tokens | Large: separate plan | When stock prices are loaded |
| G | **Apply the lean prompt to Chat and specialists** | Similar share on every other AI run | Medium: each role tested separately | After A proves it |

**Recommended order:** A and C now (two days), E alongside (Gordon runs locally anyway),
then B, then D and G.

*Built 16 October 2026 (A, B, C; uncommitted).* Measured live on DeepSeek, same questions
as the 15 October baseline:

| Question | Route | Tokens before | Tokens after |
| --- | --- | --- | --- |
| US–Japan 10-year yield gap since 2020 | Tier 1, matched to "gap" | 28,858 | 767 |
| When was the 10-year JGB yield last this high? | Tier 1, "since" | — | 776 |
| Which JGB yield was more volatile month to month? | Tier 2, lean loop | — | 7,562 |
| China's share of visitor arrivals since 2015 | Tier 2, lean loop (the match said no) | — | 12,536 |
| Core CPI, highest since, by picker | Tier 0 | — | 0 |

Eleven recipes (`app/recipes.py`), the one-call matcher and a Studies model setting are
in.

*Built 16 October 2026, second pass (G, lookups, hints, E; uncommitted):*

- **Every role's prompt is compact** (`tools_v2.instructions(compact=True)`): the dataset
  list is one line each, the date comes last for caching, tool descriptions are cut to
  their first sentences and the parameter schemas lose their 60-item dataset enums.
  Chat's fixed cost per step: 30,400 + 17,900 characters → 7,100 + 10,600.
- **Lean lookups** for the Study Analyst: `compact_result(…, lean=True)` drops a dataset's
  source, page and capabilities and keeps 8 rows (describe jnto-visitors: 3,301 → 1,184).
- **Hints find a series the question names** (China) by searching the single words inside
  the datasets the question already points to; the matcher tries once more with a wider
  search when it says nothing fits.
- **Zero-token runs:** Download for Python (inputs CSV, plain SQL, `run.py`; verified to
  reproduce the server's result locally) and a Refresh setting (daily or weekly) run by the
  refresh loop with no AI, kept as runs by "schedule".

Measured on DeepSeek, before → after:

| Question | Route | Before | After |
| --- | --- | --- | --- |
| China's share of visitor arrivals since 2015 | Studies: now matched to "share" | 12,536 | 814 |
| Chat: Japan core inflation now and a year ago | Chat Analyst (5 → 4 calls) | 70,727 | 26,868 |
| Chat: Toyota revenue, last three years | Chat Analyst (2 → 6 calls) | 45,109 | 59,470 |

The Toyota run shows the limit of trimming: the fixed cost per step fell by about 60%,
but the model took six steps instead of two (it tried `get_series` on a company dataset,
a screen and the earnings releases before answering). The number of steps is the
model's choice; the next lever for Chat is a brief that stops the lookup once the figures
are in hand, which needs its own testing. Not in this version: the shapes that need other tools (contributions, breadth, company
rankings, peers, revisions, fact-check); they still have their ready-made tools and pages. F waits for the J-Quants prices (action item 3), which the illiquidity
study needs before any sandbox would help.

Rough cost per AI-asked question, using the cached price table above and Sonnet 5.5 at $2 per
million input tokens: today about $0.06; after A and C about $0.005. A hand-edited re-run is
free. These are estimates from measured character counts; the live figures will be recorded
by the token counter on each run.

*Built 23 October 2026 (budget, D, the Audit line, the Chat brief; the sandbox F is not
built):*

- **A token budget per study** (Budget, beside Refresh: 2,000 to 100,000 tokens, or the
  server's cap). It counts the one-call match and the loop together. A run that reaches
  it keeps the last analysis that ran and says so under the question; one that made no
  analysis answers with the budget named and what to do (raise it, or narrow the
  question). The cap is checked before each model turn, so a run can end a little over
  it: 2,184 tokens against a budget of 2,000 on the volatility question, live.
- **Nothing is asked twice (D).** Before any model is called, a fresh question is
  compared with every question the person has asked before (content words, order and
  punctuation ignored). A match runs the earlier study's own analysis again on today's
  data: no model, zero tokens, a run "by your past study" that names where it came from.
  A different series, year or measure is a different question and is not reused.
- **AI use by month** on the Audit page: questions a model answered, tokens in and out,
  the average per question, for the account, six months back. The one-call matches are
  counted though they make no run row; hand runs, ready-made analyses, refreshes and
  reused answers are not, because no model ran.
- **The Chat brief** now says where a company's figures live (get_company, never
  get_series), tells the model to stop looking once a tool has returned the figures asked
  for, and the runner holds Chat to ten tool calls. Chat questions also get the likely
  series found by code first, as Studies do, with a note to ignore them for a company
  question (a series search matches Toyota to Toyota City). Measured live on DeepSeek,
  one run each, against the 16 October figures:

| Question | 16 Oct | Brief only | Brief + hints |
| --- | --- | --- | --- |
| Toyota revenue, last three years | 59,470 (6 calls) | 13,469 (1 call) | 13,883 (1 call) |
| Japan core inflation now and a year ago | 26,868 (4 calls) | 44,522 (6 calls: three spent finding the core CPI code) | 17,168 (2 calls) |

One run each, so the spread between runs is not measured; the number of calls is the
model's choice on the day.

- **Not built: the Python sandbox (F).** It runs model-written code on the production
  server, so it needs its own isolation (a container per run, no network, a time and
  memory cap) and the J-Quants prices it was waiting for. It stays a separate plan.

## What Is Not Changed

- The trust contract: official figures as published, calculated ones with their formula.
- The AI never computes; it never sees a full series.
- Each person's AI key and plan stay their own; the publication's settings come first.

## Measuring It

Every study run already records tokens in, out and cached. Add a monthly line per account
(questions asked, tokens, average per question) to the Assistant's Audit page, so the effect of
each option is a number, not a feeling.
