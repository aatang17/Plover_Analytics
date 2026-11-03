---
name: research-report
description: Use when asked to write a research piece, sector analysis, thematic report, company primer, earnings note or investment memo on Japanese markets or the economy — e.g. "write a research piece on Japan regional banks", "do a sector analysis of autos", "memo on the cross-shareholding unwind". Runs a fixed pipeline — question, plan (approval stop), research with a kept log, draft, verification — then renders the same draft to Markdown/Substack, Word or PowerPoint. NOT for a quick factual lookup, a single chart, or code/product changes.
---

# Research report — plan, research, write, verify, render

A research piece is only as good as the numbers under it. This skill makes the plan
explicit, keeps every number traceable to where it came from, and produces the finished
document fast because the content is written **once** and rendered to each format.

> Templates for every file this skill writes are in [templates.md](templates.md), with a
> worked plan for "Japan regional banks".

## The pipeline — in order, no skipping

```
0 Question → 1 Plan ──(STOP: user approves)──→ 2 Research ──(check-in if the plan breaks)──→
3 Draft → 4 Verify → 5 Render (md / docx / pptx)
```

Everything lives in one folder: `docs/research/<slug>/` (slug = short kebab-case topic,
e.g. `regional-banks`). Create it at Step 1.

```
docs/research/<slug>/
  plan.md       question, outline, data inventory, gaps — the approved contract
  log.md        every tool call / fetch, in order, with its citable URL and vintage
  data/         every table pulled, as CSV with its metadata header; external files + SHA-256
  findings.md   what the evidence says, section by section, before any prose
  draft.md      the piece — the single source of truth for every output format
  verify.md     every number in the draft, traced to the log; claim-check results
  out/          rendered files: <slug>.md, <slug>.docx, <slug>.pptx, charts/*.png
```

---

## Step 0 — Turn the topic into a question

"Japan regional banks" is a topic. A piece needs a **question** the reader wants answered,
e.g. *"Which regional banks win from rising rates, and which get hurt?"*

- If the request is only a topic, propose **2–3 candidate questions** and ask the user to
  pick or rewrite one. Also confirm: **audience** (clients, IC, Substack readers),
  **format** (md / docx / pptx — can be several), **length** (pages or slides).
- If the user already gave a question, audience and format, restate them in one sentence
  and move on.

## Step 1 — Plan (non-negotiable), then STOP

Write `plan.md` from the template. It must contain:

1. **Question and hypotheses.** The question, plus 2–4 hypotheses the research will
   *test* — written so the data could prove them wrong.
2. **Outline.** Each section = the claim it will make, the evidence needed, and the chart
   or table that shows it. One idea per section.
3. **Data inventory.** For every piece of evidence: the source, whether **we hold it**
   (Plover dataset id + series/screen), and if not, the gap decision — **fetch** (named,
   verified URL), **cite** (quote a published figure with its source), or **drop the point**.
   Build it by actually calling the tools, not from memory:
   - `list_datasets`, `describe_dataset`, `search` — what we hold and how far back.
   - `list_cohorts` — the peer group (JPX 33-industry spec, e.g. banks = `ind33:7050`);
     hand-curate when the JPX class doesn't match the sector (it rarely does exactly).
   - `screen`, `compare_cohort`, `get_company` — company-level coverage.
   - For external sources: open the URL and confirm it exists and holds what you think.
     **Never list a source you have not opened.** If a site refuses automated access,
     say so — don't scrape around it.
4. **As-of date.** The date every Plover number will be frozen at (`as_of=`), so the
   piece's links keep showing the same numbers after the next release.
5. **Out of scope** — what the piece will not cover.

Then **stop**. Show the user a short summary — the question, the outline in one line per
section, the gaps and how each is handled — and **wait for approval**. Do not start
research on the strength of "looks fine so far"; a plan the user hasn't approved is a draft.

## Step 2 — Research, with a kept log

Work section by section through the approved plan.

- **Every call goes in `log.md`**: tool + arguments, what came back (one line), the
  citable URL (`cite` field, with `as_of`), and the release/vintage. Numbers that are not
  in the log cannot appear in the draft.
- **Save the data**: series via `/api/v1/{dataset}/observations?...&format=csv&as_of=...`
  (the CSV carries its own source/vintage header) into `data/`. External files: save the
  file, record URL, retrieval date and SHA-256.
- **Write `findings.md` as you go** — per section: the finding in one sentence, the key
  numbers, their sources, and whether it supports or breaks the hypothesis.
- **Check-in rule.** Stop and report to the user before drafting if: a main hypothesis is
  disproven, a gap marked "fetch" can't be filled, or the data points to a better
  question. Propose the revised outline; update `plan.md` once agreed.

## Step 3 — Draft

Write `draft.md` from `findings.md` — never from memory.

**Read [tone.md](tone.md) before the first sentence.** The audience is professional
investors and the register is an institutional research note: finding first, third
person, standard financial vocabulary unglossed, measured verbs, complete sentences,
no hooks and no set-up-and-reveal. CLAUDE.md's short-and-plain rule governs replies
to the user, not this deliverable, and letting it leak in is exactly how the first
regional-banks draft came back as "a magazine column, not research".

House rules:

- **Bottom line first.** The answer to the question in the first paragraph.
- **Every number has a source and a "so what"** beside it. Link it to its Plover view
  (with `as_of`) or its external source.
- **Official vs calculated.** Published figures are quoted exactly as published. Rates you
  calculate (growth, ratios, spreads) say so and give the formula once, in a note or the
  appendix. `pp` for a change in a percentage, `%` for a change in a level.
- **Missing is `—`, never zero.** Never fill a gap with an estimate without labelling it.
- **Register per [tone.md](tone.md)** — third person, no journalistic phrasing, no
  fragments, standard terms used straight. Vary sentence length; do not clip.
- **Headings name the measure and the cut**, as descriptive noun phrases. "Realised
  bond losses and the margin gain", never "And it was spent".
- **Charts: one message each**, the message is the title ("Loan margins bottomed in
  2024", not "Net interest margin"), and every chart has a source line.
- **Recommendations are the author's.** If the user wants a call (overweight, pair
  trade), they state it; the skill builds the evidence, it does not invent the view.
  We hold no share prices — valuation needs the user's own price data or is left out.

## Step 4 — Verify

Fill `verify.md`: one row per number in `draft.md` → its `log.md` entry → match?
- Run `check_claim` on every headline claim.
- Totals reconcile (parts sum to the whole, residual disclosed).
- Anything that can't be traced is fixed or removed — never left in "because it's right".
Report the result in one line ("41 numbers traced, 0 unresolved").

## Step 5 — Render

`draft.md` is the content. Every format is a **render** of it — don't rewrite per format.

- **Charts first**: build each chart as PNG from the CSVs in `data/` (matplotlib), light
  theme, Plover series colours in order — `#1a4d8f #c2410c #0f766e #a16207 #475569
  #9d174d` — tabular numbers, true minus sign, source line embedded at the bottom of the
  image. Save to `out/charts/`. Follow the publication's **Chart Style** in full (Writer
  Desk → Assistant Settings; the default is `CHART_STYLE` in
  `observatory/app/pub_profile.py`): message titles, one meaning per colour in the
  note, true minus signs, line-end labels. The editor flags any other colour.
- **Markdown / Substack** → `out/<slug>.md`: the draft, with charts linked to their
  permanent Plover URLs (with `as_of`) so each is citable.
- **Word** → load the `anthropic-skills:docx` skill. Title page (title, author, date,
  as-of), executive summary, sections, charts with captions + source lines, appendix:
  sources and methodology (every dataset, release, formula).
- **PowerPoint** → load the `anthropic-skills:pptx` skill. Slide 1 title; slide 2 the
  answer in three bullets; then **one finding per slide** — the title is the finding, one
  chart or table, a source line; last slides: risks, sources & methodology.
- Open or render each output and look at it before handing over (overflowing text,
  unreadable charts, missing source lines).

Hand over: the file paths, the one-line verification result, and any gap that made it
into the piece as a stated limitation.

## Never

- Start research before the plan is approved.
- Put a number in the draft that isn't in `log.md`.
- Cite a source, link or table you haven't opened.
- Recompute or "correct" an official figure.
- Present a model's estimate as data, or a calculated rate with an Official label.
