# Ask Box Self-Check: Plan, Run, Check, Revise

Status: Approved 19 October 2026, being built. Applies to the US stocks ask box
(`/api/v1/us/ask`, `web/us-stocks.html`). Builds on the fixed question shapes
(`app/stock_recipes.py`) and the step-by-step route (`app/us_steps.py`, built 18–19
October 2026). Same rule as [PLAN-LOW-TOKEN-RESEARCH.md](PLAN-LOW-TOKEN-RESEARCH.md): the AI
is paid to understand and to write; code does the numbers.

## The problem

The box answers two ways. Common questions match a fixed shape and get a finished answer by
code. Everything else goes to the step-by-step route: an AI model breaks the question into
steps and one database query, the server runs the query, and the model never sees or writes
a number. That route already retries once when the query crashes or finds nothing.

What it never does is look at a result that *did* come back and ask whether it makes sense.
Eight ordinary questions on 19 October 2026 showed the gap:

| Question | What came back | What was wrong |
| --- | --- | --- |
| average operating margin of software companies | −3,643% (median −5.1%) | a few near-zero-revenue filers swamped the mean |
| more cash than debt and revenue growth over 20% | Viridian Therapeutics, +23,360% | "debt" became total liabilities without saying so; growth from almost no revenue |
| which Japanese banks are cheapest | US banks ranked, no warning | the box only covers US listings and said nothing |

A careful analyst does four things: plan, run, **check the result**, revise. The box does the
first two and retries only on a crash. This plan adds the other two, and a way to know,
every time the code changes, whether the box got better or worse.

## What gets built

### 1. Code checks on every step-by-step result (`app/us_checks.py`, new)

Pure functions over the question, the model's plan, and the result rows. Each returns a
finding: what is wrong, a plain-English note for the reader, and an instruction the model
can act on. No model call. Checks:

| Check | Fires when | Instruction to the model |
| --- | --- | --- |
| Mean swamped by outliers | a result reports a mean and a median of the same thing and they differ by more than 5× or 20 points; or a mean margin/ratio outside ±200% | lead with the median; or apply a revenue floor and say so |
| Growth from a tiny base | a growth column is ranked or filtered and the query has no base floor; or the top growth is above 1,000% | require `revenue_prior_usd >= 5e7` and say "from a base above $50mn" |
| Too few behind a claim | an average, share or "how many" rests on fewer than 10 rows | say how many companies are behind the figure in the headline |
| Word asked, column used | the question says debt / profit / sales / cash and the query uses a different column (liabilities for debt, revenue for sales is fine) without the headline naming it | use the matching column, or name what was measured |
| Impossible value | P/E below 1 or above 1,000; margin below −1,000%; a yield above 50%; a market value below $1mn in a ranking | drop the row and say why, or hold the answer back |
| Headline number not in the result | the filled headline carries a number that neither the question, the query's thresholds nor the result gave | already refused by `fill_headline`; the check adds the reason to the log |
| Market not covered | the question names Japan, Hong Kong, Europe, China-listed etc. | answer with the pointer, never with US data |
| Period mismatch | the question names a period and the plan's period does not cover it | fix the period |

A finding is either `revise` (send the model back) or `note` (keep the answer, tell the
reader). The notes appear under the answer with the steps, in plain words.

### 2. A review pass by the model (`us_checks.review`, in the same module)

One extra model call, on step-by-step answers only, after the code checks pass. It sees the
question, the steps it wrote, the headline as filled, up to five result rows, and the
question: "Does this answer what was asked? What would a careful analyst change?" It replies
`ok`, or `revise` with one sentence of reason, or `note` with one sentence for the reader.

Guard rails, same as the first call: temperature 0, 200 output tokens, 8-second limit,
cached per question per day, counted against the same daily cap. Off when the model is off.
A review that fails or times out is `ok`: the answer still goes out, with the code checks
done. At most two revisions per question; the third attempt's answer stands, with any
surviving notes attached.

Cost: common questions still take the fast fixed route and no model at all. A step-by-step
question costs one planning call plus one review call, plus one more planning call per
revision. On the 19 October sample, two of four step-by-step answers would have been revised
once.

### 3. A test set of real questions (`tests/us_ask_eval.json`, `tests/test_us_ask_eval.py`)

A list of questions people actually type, each with what a good answer must contain:

```json
{"question": "average operating margin of software companies",
 "route": "steps",
 "must_say": ["median"],
 "must_not_say": ["−3643", "-3643"],
 "ranges": {"headline_numbers": [-100, 100]},
 "notes": "mean swamped by near-zero-revenue filers; the median is the answer"}
```

Fields: `route` (a fixed shape's id, `steps`, or `refuse`), `must_say`, `must_not_say`,
`ranges` for any number in the headline, `min_rows` for a list. The runner asks each
question through the real `answer()` against `data/sec.duckdb`, checks every field, and
prints a one-line verdict per question and a score. It skips cleanly when the database or
the model is absent, so the ordinary test run is not slowed.

Seed: the 38 worked examples' questions, the 88 lines already in `data/us_ask_log.jsonl`,
and the eight questions from 19 October. Every miss found in use goes in as a new line
before it is fixed, so the set only grows. **The set is what makes the box steadily better
instead of fixing complaints one at a time**: a rule or example that fixes one question
and breaks three others fails the run.

### 4. Logging that feeds the set

`us_steps.log` already records every question the step route saw. It gains the checks'
findings and the review's verdict, so the weekly read of the log shows which checks fire
most and which questions the model keeps getting wrong.

## What does not change

- The fixed question shapes and their answers. The checks run only on the step-by-step route.
- The trust contract: the model still never sees a figure from the data in the planning
  call; the review call sees the result the reader is about to see, nothing more.
- The sandbox: one read-only query, two tables, no file or network access, eight seconds.

## Order of work

1. `app/us_checks.py` with the code checks and tests in memory (no database).
2. The eval set and runner, seeded from the examples and the log, run once to record the
   baseline score.
3. The hook in `us_steps.answer()`: checks after a successful run, revise up to twice,
   notes attached to the answer, findings logged.
4. The review pass.
5. Re-run the eval set; the score must not fall. Record both scores here.

## Open questions

- Whether the review pass earns its cost. Measure on the eval set with and without it;
  keep it only if it changes verdicts.
- The daily cap is shared with the planning call. If reviews push the box over the cap on a
  busy day, the planning call matters more: reviews should stop first.

## Scores

| Date | Questions | Passed without checks | With checks and review | After the fixed-route fixes |
| --- | --- | --- | --- | --- |
| 19 Oct 2026 | 50 (1 known miss) | 47 | 47 | 49 |

Read of the first run: the three 19 October misses were already fixed by rules before the
checks were measured, so the checks moved wording, not the count ("across 443 members",
"with both figures", "with tagged borrowings"; the review added "the top movers are tiny,
illiquid micro-caps" under the Chinese-stocks answer). The two misses that did move the
score were in the **fixed** route, which the checks do not cover: a nationality word
swallowed by the plain market ranking (now re-routed to steps) and growth ranked with no
base floor (now the same $50mn floor as the steps rule, plus a note when the leader is an
acquisition). Next: run the same checks on the fixed rankings' results.
