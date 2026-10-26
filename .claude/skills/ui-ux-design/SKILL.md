---
name: ui-ux-design
description: Use when creating, changing, or reviewing the visual appearance, layout, or styling of any dashboard, chart, data table, stat tile, filter, export, or embed surface for Plover Analytics data platform — public pages, Pro workspaces, and internal ops screens. Enforces the financial-data house style (FRED / Bloomberg / Koyfin / Stripe Dashboard), numeric-formatting discipline, the provenance-on-every-number rule, the anti-AI-tell checklist, and the mandatory responsive look-at-it gate before any UI is called done. NOT for API/DB/pipeline/business-logic, ingestion jobs, or copy-only edits that don't change how a screen looks.
---

# UI/UX Design — Observatory Data Platform

> **Skip this skill** for API/DB/pipeline logic, ingestion jobs, validation rules, or copy-only changes that don't alter how a screen looks. It's for **visual/layout** work only.

Professional financial-data UI. It must feel **precise, dense, and trustworthy** — an analyst should get the number they came for in seconds and be able to defend it to a client. When in doubt, make it plainer and show the source. References: FRED, Bloomberg Terminal, Koyfin, Stripe Dashboard, Trading Economics, Macrobond.

> SKILL.md is the **procedure**; [reference.md](reference.md) holds the exhaustive specs (numeric formats, chart chrome, tables, tokens, states, dark mode). Tokens and colours below are mirrors — the source of truth is the token file named in Step 8.

## Step 0 — Name the question the screen answers

Before writing markup, complete: _"A user opens this to find out \_\_\_."_
Make that answer the single most prominent thing on the screen. Everything else is quieter. If you can't name it in one sentence, stop and figure it out first.

Dashboards fail by answering ten questions equally. Rank them, then build to the rank.

## Step 1 — Pick the container BEFORE building

| Data shape | Use | Never |
| --- | --- | --- |
| One number that matters right now | **Stat tile** (value + delta + as-of) | Chart |
| One series over time | **Line chart** | Bar chart |
| Composition of a total at one point | **Bar / stacked bar / waterfall** | Pie, donut |
| Many series × few periods, compared | **Data table** with sparkline column | Multi-line spaghetti chart |
| Many records × short comparable fields | **Data table** | Cards |
| One record's attributes (label → value) | **Definition list** (2-col) | Table |
| Distribution across items | **Histogram / weighted density** | Sorted bar list |
| Long text (methodology, notes, revisions) | **Stacked rows / prose** | Table cell |

Hard rules:

- **If a cell would hold a sentence, it must not be a table.** Tables are for scanning _down a column_.
- **No footnote lines under table entries.** A cell holds its value and nothing else — no grey
  second line of background or caveat ("earlier years filed by…", a registrant's legal name, an
  XBRL tag, an "incl. predecessor" pill). If the detail matters, put it on the item's own page (a
  company page's header) or on the Methodology page, never in the list. The user asked for this
  on 2026-09-24 after seeing "earlier years filed by WALT DISNEY CO/" under The Walt Disney Company.
- **Every data table sorts and filters, and you get it for free.** Loading `assets/sortable.js` is the
  whole integration: it enhances any table with a header row and six or more rows, on render and on
  re-render. Never write a bespoke sort or filter for a table; give a cell `data-sort` when its text
  does not sort correctly, and `data-no-enhance` to opt a table out. Spec →
  [reference.md](reference.md#data-tables).
- **More than ~6 series on one line chart is unreadable.** Above that, switch to a table with sparklines, small multiples, or make series selectable with a sane default subset.
- **Never a pie or donut chart.** Composition is a bar; change in composition is a waterfall or stacked area.

## Step 2 — Hierarchy, not boxes

- **One** filled primary action per view. Secondary actions = text/outline, lower weight.
- Show importance with **type scale, weight, colour** — not by drawing a border around everything.
- **Every top-level `h2` is a quiet label over a rule** — small capitals (12.5px, 600, tracked) in
  `--obs-ink`, a 1px `--obs-border` line under it, with at most one short qualifier (a count, an as-of)
  right-aligned and muted. No filled band: the user replaced the navy band on 2026-10-26 ("the site was an
  adaptation of the GSA design, not suited to finance"). `h3` stays plain text, and a section that is
  already a bordered panel gets the same label as its panel head. **The heading carries its own top margin
  (32px)** — its clearance from the content above must never depend on a `<section>`/`.prose` wrapper,
  and no negative margins to tighten text against it.
- Dashboard order: headline stat row → the primary chart → supporting breakdown → the full table → provenance/methodology. Not a grid of equal-weight boxes.
- **Signed-in workspace pages (an app with a left menu) use the workspace app shell**: a light bar across
  the top (the header tokens), white sidebar, stat strip, `h2` labels, lists instead of card grids. Spec →
  [reference.md](reference.md#workspace-app-shell).
- One border, one background per panel. **No box nested inside a box.** No scrollable inner box except a genuinely wide data table wrapped in `overflow-x: auto`.
- Spacing: nothing touches. Min 8px between unrelated elements, 4px between closely related.
- **Charts get room.** A time-series chart squeezed into a 200px-tall card is decoration, not analysis. Give the primary chart at least 320px of plot height on desktop and let it span the content width; reduce the *number* of charts before shrinking any one of them.

## Step 3 — Numbers are the product

This is what separates a credible data product from a pretty one.

- **Tabular (lining) numerals everywhere a number appears** — `font-variant-numeric: tabular-nums`. Digits must sit in the same column down a table and must not jitter when a value updates.
- **Right-align all numerics**, including their headers. Left-align text. Align on the decimal point.
- **One precision per column, stated.** Rates to 1 decimal (`2.7%`), index levels to 1 (`108.3`), contributions to 2 (`0.24 pp`), yen prices to 0 (`¥487`). Never mix `2.7` and `2.70` in one column.
- **Always carry the unit**: `%`, `pp` (percentage points — never write `%` for a difference of two percentages), `¥`, `= 100` for index bases. Put the unit in the column header rather than repeating it in every cell, unless the column mixes units (it shouldn't).
- **Group thousands** (`1,653` not `1653`). Never abbreviate to `1.7K` in a professional table; abbreviation is acceptable only in a stat tile where space forces it, and then keep full precision in the tooltip.
- **Signed values show their sign explicitly** — `+0.3` / `−0.2`, using a true minus (`−`, U+2212), not a hyphen. Unsigned zero is `0.0`, never `+0.0`.
- **Missing is not zero and not blank.** Render `—` with a tooltip saying why (not yet published / not collected in this geography / suppressed). A zero and an unpublished value must never look alike.
- **Provisional and revised values are marked** (Step 5) — never silently swapped.
- Percentages, ratios, and index levels are different measures. Never rank or plot them on one axis.

## Step 4 — Charts that survive scrutiny

- **Zero baseline is mandatory for bars**, optional and often wrong for lines. A YoY-rate line need not be forced to zero if that hides the movement; an index-level line should not be forced to zero at all. Never truncate a bar axis.
- **Direction colour is not P&L colour.** For macro indicators, rising is not "bad red" and falling is not "good green" — use a neutral series colour plus an explicit sign and arrow. Reserve red/green for genuine gain/loss surfaces, and remember the East-Asian convention is inverted (red = up), so colour alone must never carry direction.
- **Diverging data** (contributions, surprises) uses one diverging scale — cool for negative, warm for positive — never red/green, and always with the sign printed.
- **Max ~6 categorical colours**, from the fixed palette in [reference.md](reference.md#colour-system). No rainbow ramps, no per-chart improvised colours. A given series keeps the same colour across every chart on the platform.
- **Gaps stay gaps.** A break in a series renders as a break in the line — never interpolated across, never dropped to zero.
- **Every chart labels its axes with units**, states its base/vintage where relevant, and carries a source line. A chart that can be screenshotted must be self-explanatory once it leaves the page.
- **Gridlines: horizontal only, 1px, `--obs-grid`.** No vertical gridlines, no chart border, no background fill, no 3D, no shadow, no gradient area fill (a flat tint at ≤12% opacity is fine).
- **Tooltips show full-precision value, date, series name, and unit.** On multi-series charts show every series at that x-position behind one crosshair, not one series at a time.
- Annotate real events (base change, methodology break, policy date) with a thin vertical rule and a label. A methodology break must be visible on the chart, not only in the footnote.

Full chart chrome spec → [reference.md](reference.md#chart-chrome).

## Step 5 — Provenance and freshness are UI, not footnotes

Every number on screen carries its trust label and its as-of. This is the platform's commercial moat; treat a missing label as a bug of the same severity as a wrong number.

- **Trust label on every metric, chart, table, and export**: Official statistic · Platform-derived · Platform model/estimate. Visually distinct, never decorative-only, never inferable only by absence.
- **Derived values expose "show calculation"** — formula, inputs, and calc version, inline and collapsed by default.
- **As-of and vintage are always visible** on the surface, not only on hover: reference period, publication timestamp, base/vintage, provisional-vs-final.
- **Staleness is loud.** If the pipeline hasn't refreshed or a release is pending, say so at the top of the surface with the last-good timestamp. A stale screen must never look current.
- **Revisions are inspectable** — a revised value shows a marker linking to the prior vintage.

## Step 6 — Copy: instruct, never sell

- Shortest sentence that says what the thing is or what to do next. No hype, no slogans, no time-saving promises.
- **Section headings name the measure and the cut — they are not headlines.** The audience is
  sell-side economists and buy-side data teams, not Substack readers. Use a descriptive noun
  phrase in Title Case: "Contributions to Headline Inflation", "Population Change by Prefecture",
  "Maturity Detail". Never a rhetorical question ("What Is Driving Inflation", "Who Holds It"),
  never an editorial flourish ("The Tape", "The Unwind", "One Prefecture, Fifty Years"), and never
  a bare "All X" where "X Detail" is the convention. The specifics that vary — the company, the
  date range, the row count — belong in the `h2-note` qualifier, not in the heading. **This applies
  to documentation pages too (manual, methodology, API).** Plain names only: "Overview",
  "Limitations", "Troubleshooting", "Lookup Reference" — never chatty or first-person phrasing
  ("Limits, honestly stated", "When something looks wrong", "The lookups, one by one", "Ten
  questions to try"). The user asked for this on 2026-10-02 after the Manual's headings read as
  informal. Applies to every new heading going forward.
- **Never render a raw enum/slug/db value** (`published_yoy`, `concept_changed`) — map to a Title-Case label via one `Record<value, label>` per surface. Sentences = sentence case; badges/headings/buttons = Title Case.
- **Descriptive, never causal.** "Coincides with", "contributed mechanically", "is associated with" — never "because", "driven by", or "caused by" unless a documented research design backs it.
- **Never name the technology.** Describe what a feature does for the user, not that it uses AI/ML/a model. "Estimated from Tokyo series", not "AI-powered nowcast".
- **Internal ops users are not engineers.** Never surface a raw parser error, stack trace, or API body — translate to _what happened · why · what to do next_, and keep raw text behind a collapsed "See details". A warning stays red.

Full copy rules → [reference.md](reference.md#copy).

## Step 7 — Anti-AI-tell checklist (gate before finalising)

Reject the design if ANY of these are present:

1. border-radius > 4px
2. box-shadow
3. gradient (esp. purple→blue / purple→pink), including gradient-filled chart areas
4. a status label drawn as a box — filled **or outlined** green/red/coloured badges, pills or status chips.
   A label (Official Statistic, Stale, OK) is coloured words in weight 600, no border, no background; the
   user asked for no rectangular green or red boxes on 2026-10-26. Direction is coloured text with an arrow.
5. icon inside a coloured circle/square
6. glassmorphism / frosted / backdrop-blur
7. 2×2 or 3×3 "feature card" grid
8. indigo/violet/purple as primary
9. more than one font family (excluding the mono numeric face) / more than 3 greys + brand
10. two components touching with zero gap
11. pie chart, donut chart, 3D chart, or a radial/gauge "score" dial
12. rainbow or neon series palette; glow effects on lines
13. a big number with no unit, no as-of, and no source

## Step 8 — Use the real tokens (never hard-code hex)

| Token | Value | Use |
| --- | --- | --- |
| `--obs-primary` | `#1d5bd8` | Buttons, links |
| `--obs-primary-dark` | `#123f9c` | Hover |
| `--obs-ink` | `#0f172a` | Primary text, key figures |
| `--obs-text-muted` | `#64748b` | Labels, secondary text, axis text |
| `--obs-border` | `#e2e8f0` | Borders, dividers |
| `--obs-grid` | `#eef2f7` | Chart gridlines |
| `--obs-surface` / `--obs-surface-subtle` | `#ffffff` / `#f8fafc` | Panels, hover rows |
| `--obs-header-bg` | `#ffffff` | Site header: light, a 1px `--obs-header-border` rule under it, never navy |
| `--obs-accent` / `--obs-ground` / `--obs-up` / `--obs-down` | `#1d5bd8` / `#f4f6f9` / `#0b8a5c` / `#c2410c` | Front page and answers: accent, grey page ground under white panels, a share price's up and down |

- The header keeps its own `-ink` / `-text` / `-muted` tokens so the workspace bars follow it. Never a
  `box-shadow` under it. `--obs-band-bg` (navy) survives only as the PloverResearch accent rule.
- Series palette, diverging scale, and trust-label colours → [reference.md](reference.md#colour-system).
- **One font: IBM Plex Sans** (self-hosted in `assets/fonts/`, declared in app.css), numbers in its
  tabular figures — never a monospaced face for figures. Inter was dropped on 2026-10-26 because it read
  as AI-made. Monospace only for code, IDs and checksums. No CDN `@import`.
- Tokens live in **one** file. Never redefine `--obs-*` in an app's own CSS; never hard-code a hex in a component or a chart config.
- Dark mode is a first-class requirement, not a filter — see [reference.md](reference.md#dark-mode).

## Step 9 — Overlays and interaction must not break

- Popovers/dropdowns/tooltips open **downward** and sit **below** the sticky header. Only true modals may cover it.
- **A dropdown must never be clipped or painted under later content.** Two silent killers, invisible in code review:
  1. **Clipping** — any ancestor with `overflow:hidden` cuts an absolutely-positioned menu at its edge. If a panel needs `overflow:hidden`, portal the menu out.
  2. **Underpainting** — a `z-index` only wins inside its own stacking context. If the menu's positioned ancestor has `z-index:1` and a **later sibling section** also has `z-index:1`, the later one paints on top no matter how high the menu's own z-index. Raise the _ancestor_. This is often masked at desktop widths and shows only on mobile where the layout stacks taller.
- **A close/dismiss control must never overlap content.** Reserve a lane for it and test against the **worst-case content** (longest series name, a value that wraps), not the tidy short case.
- A popover with its own scrollable body must not close when the user scrolls **inside** it — gate the close on `!popup.contains(event.target)`.
- **Chart interaction:** the crosshair follows the cursor across all synced charts; hover must not shift layout; a legend click isolates or toggles a series, and that state survives a date-range change.

## Step 10 — MANDATORY look-at-it gate (do not skip)

Never call UI done without rendering it at **390 / 768 / 1280 / 1440** and _looking_. Then ask:

> Can a professional find the number this screen is for in **3 seconds**, and cite it with its source in **10**?

If not, fix hierarchy before any polish. Use the `run` skill or a screenshot — never declare a visual change done from code inspection alone.

**Full-bleed or container-shaped components (maps, canvases, embeds) must additionally be checked at one ultra-wide viewport (≥2200px)** — layout math that survives 1440 can still collapse when the container's aspect ratio changes.

**Interact, don't just look.** Open every dropdown, tooltip, and expandable at every breakpoint (Step 9 bugs appear only when open). Hover the chart. Change the date range. Toggle a series.

**"Renders" is not "done" — check the data, not just the layout.** Test against the **worst-case row** (longest series name, a missing value, a negative, a revised figure, a five-digit index), never the tidy first one:

- **Alignment:** repeated fields (value, delta, as-of, actions) sit in the same position on every row. A long name must not shove the value sideways; a missing field must not let the next one slide left — reserve the column or right-align.
- **Formatting:** every value human-formatted — grouped thousands, consistent precision and units, true minus signs, Title-Case labels not raw enums, and no placeholder junk (`1970-01-01`, `NaN`, `null`, or `0.0` standing in for missing).
- **Charts:** axis labels not clipped at 390px, legend not overlapping the plot, gaps rendered as gaps, tooltip readable at the right edge of the viewport.
- **No ragged edges:** no orphaned separator, no stray marker, nothing that visibly jumps as content varies between rows.
- **Even spacing:** measure the gaps between neighbouring items in a bar, toolbar or row (header links, buttons, tabs). Gaps must match. An empty item, or width reserved for content that isn't there, must take no space. Check every state the bar can be in: signed out, signed in, with and without its as-of. (2026-10-11: Docs and Sign in sat 67px apart while Sign in and Dark Mode sat 18px apart.)

## Exception — PloverResearch pages (`/research`, the research desk)

Editorial pages, not data dashboards. Agreed with the user on 2026-10-03; applies to
`research.css`, `research_pages.py` and the desk's article canvas only — every data page keeps
the rules above.

- **The front page is a grid of boxed cards** (overrides anti-AI-tell #7 here). Every area is a
  box: the lead story, Chart of the Week, each card, the subscribe panel. Each box carries a 3px
  `--obs-band-bg` rule along its top. Cards stay sparse: chart, category label, headline, date.
- **Chart-first lead story:** the lead box opens with the note's chart, the words beside it in a
  white box. No solid filled story panel.
- **Section headings are the platform's navy band**; inside an article, sub-headings are bold
  type under a short navy rule instead.
- **One typeface: IBM Plex Sans** (Inter until 2026-10-26), headlines heavy (700–800) and tight. No serif — the user tried one and
  dropped it to keep the look distinct from news publishers.
- **No trust badge on research charts.** The "Official Statistic" pill read as AI-made; the
  source line says it in words instead ("Values as published"), and derived rates keep their
  "Show calculation". Provenance is still on every chart — only the badge is gone.
- **Don't imitate a named publisher.** Borrow patterns (cards, labels, a share row), never a
  publisher's recognisable combination of colours, type and layout.

## P0 — never regress

Trust labels on every metric; as-of/vintage visible on every surface; a provenance path from every displayed value to its source artefact; missing rendered distinctly from zero; price-change and price-level measures never mixed in one ranking or on one axis; exports carrying their data dictionary, source, vintage, and trust label. Ask before adding a top-level nav item.
