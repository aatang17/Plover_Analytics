---
name: news-brief
description: Use when asked for a news item, news brief, short post, data flash or "what can we report today" from Plover data — e.g. "any news we can report", "write today's news piece", "news brief on the 10-year yield", "turn the GDP revision into a post". Scans the datasets for what changed, shortlists candidates (user picks one), verifies every number against the live data, finds the angle from a second dataset and states its implications explicitly (who gains, who pays, how much), builds the chart, and writes a 300–450 word Substack-ready post. NOT for a multi-page research piece or sector analysis (use research-report), a quick factual lookup, or code/product changes.
---

# News brief — scan, pick, verify, chart, write

A news brief is **one fact, its implications, one chart, one short post**, out the same
day. We have no newsroom; what we have is the numbers and the record of how they
changed. The brief reports what the data says, **says what follows from it**, and links
to the page that proves it. It carries no forecast of levels or dates and no policy
opinion.

**No implications, no article.** A piece that cannot say who gains, who pays, or what
has to change is not written. This is the user's rule, given twice on the first brief
(2026-09-30): the read-out was "too boring", and the version that added a second series
was "comparing for the sake of comparing" — the comparison had substance, but the post
never said what it meant.

**A brief is not a read-out.** "The yield closed at 3.08%, the highest since 1996, up
from 1.66% a year ago" is a read-out: every sentence restates the same series, and the
fact needs an **angle**, and the angle needs **explicit implications** — see Step 3a.

For anything longer than a page, or anything that needs a thesis, use `research-report`.

## The pipeline

```
1 Scan → 2 Shortlist ──(STOP: user picks one)──→ 3 Verify → 3a Angle → 4 Chart → 5 Write → 6 Hand over
```

If the user already named the story ("write up the 10-year yield"), skip to Step 3.

Everything for one brief lives in `docs/news/<YYYY-MM-DD>-<slug>/`:

```
data/        every CSV the brief uses, saved from the API with its header block
chart.png    the chart, built by chart.py from data/
post.md      the post — headline, text, chart, source line, links
verify.md    every number in the post → where it came from → match?
```

---

## Step 1 — Scan for what changed

Look for news in four places. Use the Plover tools (`list_datasets`, `get_overview`,
`get_vintages`, `screen`, `get_series`) — never memory.

1. **New releases.** `get_vintages` with no series shows each dataset's newest release
   and when it arrived. Anything published since the last brief is a candidate.
   `ls docs/news/` gives the date of the last brief.
2. **Revisions.** `get_vintages` with a series shows a number that changed between
   releases. A revision is news only we can report — check GDP, CPI and any dataset with
   more than one vintage.
3. **Extremes.** A series at its highest or lowest in years, or crossing a round number.
   Daily series (yields) and monthly headline series are where these turn up.
4. **Filings.** `screen` on buybacks, large-shareholdings, earnings-releases: a programme
   finished unusually fast, a large unspent authorisation, a result far ahead of the
   company's own forecast, a new 5% holder.

Check `/api/v1/catalog/health` first. **A stale dataset is not a source of news** — say
it is stale and leave it out.

## Step 2 — Shortlist, then STOP

Show three to five candidates in one table: the story in a few words, the number, and
why it matters in one plain sentence. Put the strongest first and say which one you
would write. Then **wait for the user to pick**.

A candidate is strong when it is (a) new this week, (b) a number a reader can grasp in
one line, and (c) chartable from data we hold.

## Step 3 — Verify (before writing a word)

Pull every number again, fresh, and save the data:

- Series: `https://ploveranalytics.com/api/v1/{dataset}/observations?series=...&format=csv`
  into `data/`. The CSV carries its own source, release and unit in the header.
- Filings and screens: save the tool's JSON rows to `data/` as they came back.

Then check the claim itself:

- **"Highest / lowest since X"** — scan the **whole** history in the saved CSV, every
  row between X and today, with a one-line script. Never infer it from a sample of
  years. State the last date the level was matched or beaten and its value.
- **"First time above / below N"** — same: find the last date it was on the other side.
- **"Revised"** — both values come from `get_vintages`. The dates are *our fetch* dates
  unless the release record says otherwise; never present them as the publisher's
  release dates.
- **Changes** — `pp` for a change in a percentage, `%` for a change in a level. A rate
  we calculate is labelled as calculated; an official figure is quoted exactly.
- **Date basis** — say what the number is: a daily close, a month, a filing as of a date.

If the claim fails the check, do not soften it into something vaguer. Tell the user and
go back to the shortlist.

Write `verify.md`: one row per number in the post → source file or tool call → match.

## Step 3a — Find the angle

The angle is what the fact **touches**, shown with a **second dataset we hold**. It is
what makes the post worth reading, and it is where our breadth beats a wire story.

Ask: who pays, who earns, or what now looks out of line because of this number? Then
pull the series that answers it and set it beside the headline series.

| The fact | An angle from a second dataset |
|---|---|
| 10-year yield at a 30-year high | Bank loan and deposit rates: the yield is twice the rate on banks' loan books |
| CPI item rising fast | Its basket weight and contribution to the headline; the breadth across items |
| GDP revised | Which component moved, and what the first print said about it |
| A buyback completed in a month | The company's cross-shareholdings, or who filed a 5% change in the same weeks |
| Visitor arrivals up | Guest-nights by prefecture: where they actually stayed |

Rules for the angle:

- **It is a comparison the data makes, not a reason we supply.** "The yield is above the
  loan rate" is data. "Because the BoJ…" is not ours to say unless a dataset shows it.
- **Test how unusual it is** across the whole overlapping history — how many months it
  has held, whether the gap is a record — with the same every-row scan as a "highest
  since" claim. An angle that is normal is not an angle; try another.
- **Same measure type only.** Rates beside rates, levels beside levels.
- **State the mismatch.** If the two series differ in date (a September yield, a July
  loan rate) or in kind (all maturities against ten years), the post says so plainly.
- If no second dataset gives a real angle, tell the user the story is thin and offer the
  next candidate. Do not pad a read-out.

### Then turn the angle into implications — explicitly

A comparison is not a finding until the post says what follows from it. For each angle,
write two to four implications before drafting, each one:

1. **Names who it falls on** — borrowers, banks, depositors, the government, holders of
   the bonds, a sector, a company's shareholders.
2. **States the consequence as a sentence with a verb**: "Borrowers face a further rise
   in interest costs", not "Loan rates and the yield have diverged".
3. **Puts a size on it in yen or points where we hold the base** — multiply the rate gap
   by the stock it applies to (loans, deposits, debt outstanding), and set the result
   beside something the reader knows (sector net income, the budget line). A size is
   arithmetic on published figures, labelled as calculated. It is a measure of scale,
   and the post says it is not a forecast of timing.
4. **Says what it depends on** — the condition under which it holds or reverses ("only
   while deposit rates rise by less than about 70% of the rise in loan rates").

Where we hold no base to size an implication, give the direction and say no size is
given. **If no implication survives this test, the story is not written** — tell the
user and offer the next candidate.

What stays out: a forecast of where a rate or price will be, a date by which something
happens, what a policymaker should do, and any cause the data does not show. "Loan
pricing has further to rise" is an implication of the gap; "the BoJ will hike in
December" is not ours.

Record in `verify.md` what each implication rests on and its limits (what the arithmetic
ignores, which dates are mismatched, which proxy stood in for what).

Every number the angle and the implications add goes through Step 3 and into `verify.md`.

## Step 4 — Build the chart

Every brief has exactly one chart, built by [chart.py](chart.py) from a CSV in `data/` —
never from numbers typed into a script. **The chart shows the angle**, not just the
headline series: usually two lines, the headline series and the one it is set against.
When the two come from different datasets, write a combined CSV into `data/` (one
`period` column, one column each, values copied as published, blanks left blank) with
`# Source:` and `# Release:` lines naming both.

```bash
observatory/.venv/bin/python .claude/skills/news-brief/chart.py \
  --csv docs/news/<dir>/data/10y.csv --out docs/news/<dir>/chart.png \
  --title "Japan's 10-year yield is at its highest since 1996" \
  --subtitle "10-year JGB yield, daily close, %" \
  --mark-last --mark 1996-08-27 --hline "3:3%"
```

- `--kind line` (default) for a level or rate over time; `--kind bar` for monthly or
  quarterly counts and flows.
- `--mark-last` labels the newest value. `--mark YYYY-MM-DD` labels the comparison point
  the story rests on. `--hline "value:label"` draws the threshold that was crossed.
- `--start` trims the history. Show enough to prove the claim: a "highest since 1996"
  chart must reach back past 1996.
- `--series` / `--labels` for up to six lines. **Never mix measure types on one chart**
  (an index with a yen level, a stock with a flow).
- The source line is read from the CSV header and embedded in the image.

For filings data there is no observations CSV. Write a small one into `data/` in the same
layout — `# Source: ...` and `# Release: ...` header lines, then `period,<column>` rows —
from the saved tool output, and chart that.

Chart rules:

- **The title is the message** ("Rice contract prices are 15% below the peak"), not the
  name of the series. The subtitle names the measure and the unit.
- Missing values stay as gaps. Nothing is filled or smoothed.
- **Look at the image before handing it over** (Read the PNG). Check: the labelled values
  match `verify.md`, no label is cut off or struck through, the source line is present.
  Fix and rebuild if not.

If chart.py cannot draw what the story needs (two panels, a ranked bar of companies),
write a one-off `chart.py` in the brief's own folder, copying the style block from the
skill's script so the look stays the same.

## Step 5 — Write the post

Read [../research-report/tone.md](../research-report/tone.md) first. Same register as a
research note — finding first, third person, measured verbs, complete sentences, no
hooks — at a fraction of the length. CLAUDE.md's short-and-plain rule is for replies to
the user, not for this post.

`post.md`, 300–450 words:

1. **Headline** — the implication, with its size. "Japan's bond market has repriced and
   its bank loans have not: about ¥10 trillion a year sits in the gap", not "10-year
   yield reaches 3.08%" and not "the yield is twice the loan rate".
2. **First paragraph** — the fact, the angle and the headline implication, with the
   values and dates.
3. **The chart.**
4. **How unusual it is** — the run, the record, the count of months — in one or two
   sentences, ending "N implications follow."
5. **The implications, one short paragraph each**, opening with the implication itself in
   bold as a full sentence, then the numbers that support and size it, then its
   condition. Count them and make the count match.
6. **The limits**, in one or two sentences: mismatched dates, what the comparison does
   not hold constant.
7. **Source line** — the publisher's credit line as the dataset gives it, and the date
   basis ("daily closing yields, Ministry of Finance").
8. **Links** — the Plover page for the chart (the `cite` URL), and the API CSV link with
   `&as_of=YYYY-MM-DD` as the frozen record. The page itself does not freeze on `as_of`;
   only the API link does.

Not in a brief: forecasts of levels or dates, what policymakers "should" do, reasons the
data does not show, and numbers we do not hold. If a number from outside is essential, quote it with
its named source after opening that source; otherwise leave it out.

## Step 6 — Hand over

Give the user: the path to `post.md` and `chart.png`, the one-line verification result
("7 numbers traced, 0 unresolved"), and anything they should know before posting (a date
basis, a caveat on the comparison). The skill **never publishes** — the user posts it.

## Never

- Write before the claim has passed Step 3.
- Hand over a read-out: a post whose every sentence restates one series.
- Hand over a comparison with no stated implication — "comparing for the sake of it".
- Supply a cause the data does not show.
- Print "highest since" without scanning every row between then and now.
- Put a number in the post that is not in `verify.md`.
- Build a chart from typed numbers, or mix measure types on one chart.
- Recompute or "correct" an official figure, or show a missing value as zero.
- Report from a dataset the health report marks stale.
- Publish, email or post anything.
