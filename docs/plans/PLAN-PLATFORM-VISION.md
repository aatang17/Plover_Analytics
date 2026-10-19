# Plover: What It Becomes

Status: draft for decision, 19 October 2026. Replaces the earlier "Japan Without a Japan
Analyst" vision, which is retired in full. Nothing here is built by this document.

Written for: Aaron, as owner, and every working session that needs to know where the
product is going.

## The Answer

Plover becomes **your analyst for markets, built on official data that nobody else
keeps.**

You ask it something. It answers with the number, the chart and the source. It
remembers what you cover. It watches every release and filing for you, and tells you
first when something changes. When you need to write, it drafts the note with the
charts already in it.

The data is the moat. The analyst is the product. Publishing is how it spreads.

## Who It Is For

In the order they pay:

1. Sell-side economists and strategists.
2. Buy-side and quant data teams, and their own AI agents.
3. Discretionary portfolio managers.
4. Writers and small research shops.
5. Academics, free, because citations make Plover the standard source.

## The Product: One Box, Four Jobs

There is one place to start: a question box. Everything else sits behind it.

| Job | What the person gets | Built today as |
| --- | --- | --- |
| Ask | An answer with its number, chart, source and working | Studies, inside the Investment Assistant |
| Keep | Saved answers that re-run when new data lands | Investment Assistant coverage and saved studies |
| Watch | A message from Plover when a saved answer or a covered company changes | Not built. Only drawn on the mockup canvas |
| Write | The answer turned into a note or post, charts frozen and citable | Writer Desk and PloverResearch |

Today these are separate apps with separate front doors. The end state is one product,
one account, one box. The Investment Assistant and the Writer Desk stop being names a
customer has to learn.

Underneath sits the data layer: every dataset, the API and the MCP server. It is sold on
its own as well, to people who bring their own tools.

## Markets

Japan, Hong Kong and the United States are live. More follow. Every market meets the same
bar before it ships: sources on every number, point-in-time history, and its own line in
the health report.

## What Makes It Hard to Copy

- **Point-in-time history.** Every release is kept as it was published. This only grows
  forward. A competitor starting today can never have our past.
- **Filings that disappear.** Japanese sources delete filings after set periods. We keep
  them.
- **A source on every number.** Official figures are shown exactly as published.
  Calculated figures show their formula. Nothing is unexplained.
- **One database.** Holdings, boards, pay, buybacks and macro sit together, so questions
  that cross them can be answered at all.

What is not hard to copy: the AI. Any model can sit on top. What it stands on is ours.

## How It Reaches People

- **Inside the assistants people already use.** Plover listed as a connector in ChatGPT,
  Claude and other assistants, through the MCP server we already run. Every person who
  asks those assistants about our markets gets Plover's number with Plover's name on it.
- **Cited by AI search.** When an AI answer quotes a figure for our markets, it links to us.
- **The Asia Economics Observations Substack**, and every writer who publishes through
  Plover.

## How It Makes Money

The rule: **asking is free; paying starts when the data leaves the page or a machine asks.**

| Who pays | For what |
| --- | --- |
| Individuals | A seat: saved answers, alerts, downloads, writing tools |
| Data and quant teams | API and MCP access, metered, and the point-in-time archive |
| Companies building AI assistants | Licensed, sourced data their agent can cite |
| Institutions | An annual desk licence: seats, single sign-on, service levels, rights to quote in notes |
| Publications | A workspace for a team that writes and sells research |

Prices are not set. Billing does not exist yet: accounts do, payments do not.

## What It Never Becomes

- A stock picker. No ratings, no buy lists, no advice.
- A broker. Nothing that touches orders or money.
- A news and estimates terminal.
- A place where sponsors sit next to numbers.

## Decisions Needed

These block the plan. Each is the owner's call.

1. **Does Plover answer in its own words?** CLAUDE.md says the Ask feature stays off and
   that this is settled. The one-box product reverses it. The middle path: Plover only
   finds data, runs calculations and shows the formula and source. It never gives an
   opinion. This needs an explicit yes, and CLAUDE.md changes with it.
2. **Who pays for the AI?** Today a person's runs use their own AI key, and one writer
   paid about $4 for one chart. Choices: Plover pays and prices it into the seat; the
   person brings their own key; or both.
3. **How deep per market.** All markets are in scope, but depth is the moat. Decide which
   markets get company-level depth and which stay macro only.
4. **Prices and billing.** Set the first price points and pick a payment provider.

## What This Changes Elsewhere

- **CLAUDE.md**, "What this product is" and "Ask feature": need rewriting once decision 1
  is made. Proposed separately, not edited here.
- **PLAN-JAPAN-MACRO-OBSERVATORY.md**: its customer order and vintage moat still hold. Its
  tiers and scope do not.
- **PLAN-INVESTMENT-ASSISTANT.md, PLAN-RESEARCH-PUBLISHING.md, PLAN-STUDIES-WORKSPACE.md**:
  become parts of one product rather than three. Their build detail still holds.
