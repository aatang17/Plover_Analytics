# Japan Spent Thirty Years Forgetting How Prices Work. Then It Remembered.

*Introducing the Plover Analytics inflation dashboard — 55 years of Japanese
consumer prices, 582 individually priced items, and every calculation shown.*

---

In March 2002, two-thirds of the things a Japanese household bought were getting
cheaper. Not rising slowly — actually falling. Of the 447 individually priced
items in the national CPI basket, **67.3% had a lower price than a year earlier, and
only 23.5% were rising at all.**

In July 2026, that picture is inverted: **71.2% of items are rising, 22.9% are
falling.**

That reversal — not the headline rate, the *breadth* underneath it — is the most
important thing that has happened to the Japanese economy in a generation. It is also
almost never charted, because doing it properly means holding 582 item-level series
back to 1971 and recomputing the distribution every month. So we built the thing that
does it, and we are making it free.

This is the first post from the **Plover Analytics**. Start here:
**[the inflation dashboard →](https://web-production-c9178.up.railway.app/cpi.html)**

---

## Part 1: The long flat line (1995–2012)

To understand why 2% inflation is a big deal in Japan, you have to appreciate how
strange the preceding period was.

Japanese consumer prices rose **190.8%** between January 1970 and January 1990 —
5.5% a year, entirely normal for a fast-growing economy. Then it stopped. Not slowed:
stopped.

- **January 1995: CPI index 96.1. January 2013: 94.2.**
- Eighteen years. The price level ended **lower** than it started.
- From 1999 to 2012, **70% of all months showed negative year-over-year inflation.**
  The average month was −0.28%.
- The single worst month was **October 2009 at −2.56%**.

The usual rebuttal is that this was an energy and food story — global commodity cycles
washing through a small open economy. It was not. Strip out fresh food *and* energy,
and the core-core measure was **negative in 83% of months from 1999 to 2012** (139 of
168). The deflation was in the middle of the basket, not at its edges.

By October 2009, only **27.9% of items were rising** and **63.8% were falling**. This
is what a deflationary mindset actually looks like in data: not a dramatic collapse,
but a slow, grinding consensus that nothing should ever cost more than it did last
year — which becomes self-fulfilling, because no firm dares be the first to raise a
price and no worker dares ask for a raise to cover one.

*See it: [Inflation Over Time, full history →](https://web-production-c9178.up.railway.app/cpi.html?range=max)
· [How Broad Is Inflation, full history →](https://web-production-c9178.up.railway.app/cpi.html?brange=max#h-breadth)*

## Part 2: The decade of trying (2013–2021)

In April 2013 the Bank of Japan launched Qualitative and Quantitative Easing with an
explicit 2% target and a two-year deadline. It went on to buy government bonds, ETFs,
REITs and corporate paper on a scale no major central bank had attempted, and to pin
the entire yield curve.

The result, measured honestly:

**January 2013: 94.2. January 2021: 99.8. Eight years, +5.9% in total — 0.72% a year.**

And that flatters it, because it includes the April 2014 consumption tax rise, which
mechanically lifted headline inflation from 1.59% to 3.39% in a single month without
a single underlying price behaving differently.

By December 2021 — after nearly nine years of the most aggressive monetary experiment
in modern history — **core-core inflation was −0.80%.** Still negative. The policy did
many things. It did not do this one.

## Part 3: The break (April 2022)

Then, in the space of one month:

| | Headline CPI, YoY |
|---|---|
| March 2022 | +1.20% |
| **April 2022** | **+2.42%** |

The proximate causes are well known — a collapsing yen, post-Covid global goods
inflation, and Russia's invasion of Ukraine hitting energy and grain simultaneously.
What matters is what happened next, which is that it did not go away.

- Headline peaked at **+4.39% in January 2023**; core-core peaked at **+4.30% in June
  2023.**
- Breadth peaked in **September 2023**, with **70.1% of all items rising faster than
  2%** and only **7.5% falling** — the broadest reading since **March 1990**, setting
  aside the artificial one-month spike of the April 2014 consumption tax rise. Thirty-
  three years.
- **Of the 52 months since April 2022, 46 have printed at or above 2%.**

Something changed that eight years of QQE could not change: Japanese firms discovered
they could raise prices and keep their customers, and Japanese workers began winning
the shunto wage rounds to match. Imported inflation was the trigger. The regime change
was domestic.

## Part 4: Where we actually are (July 2026)

Here is where it gets interesting, and where most coverage has got it wrong.

Through the first half of 2026, headline inflation fell below 2% for six consecutive
months — bottoming at **1.26% in February**. A great many people wrote that the
Japanese inflation episode was over and the BOJ had missed its window.

The July 2026 print:

- **Headline: +2.06% YoY** — back above target, and up 0.35pp on the month
- **Core (ex fresh food): +1.79%** · **Core-core (ex fresh food and energy): +1.90%**
- **Month-over-month: +0.53%** — which annualises to over 6%
- **3-month annualised: +4.32%**

That last number is the one to sit with. The annual rate says 2%. The last three
months, annualised, say 4.3%. Those are the same data, differently framed, and they
imply completely different policy. The yearly figure is still dragged down by the
comparison against early-2025 prices; the quarterly momentum is telling you what is
happening *now*.

Breadth agrees with momentum, not with the annual rate: the share of items rising
faster than 2% troughed at 45.2% in April and has climbed back to **48.3%**.

*See it: [the current dashboard →](https://web-production-c9178.up.railway.app/cpi.html)
· switch the measure to "3-Month Annualized" and the two stories separate in front of you.*

Zoom out and the household experience is unambiguous. The CPI index stood at 94.2 in
January 2013 and stands at **114.2** today: **+21.2% in thirteen years**, of which
**+12.5% has arrived since April 2022 alone**. Japan did roughly two decades' worth of
its old inflation in four years.

---

## What this dashboard does that a chart on a wire service doesn't

Three things, and they are the reason it exists.

**1. It separates what was published from what we calculated.** Every index level
carries an `Official Statistic` badge — it is exactly what the Statistics Bureau
released, never recomputed. Every rate of change — year-over-year, month-over-month,
3-month annualised, contributions, breadth — carries **no badge and instead shows you
its formula**, on the page and in the header of every CSV you export. You should not
have to trust us. You should be able to check us.

(An honest consequence: because we compute rates from *published, rounded* indices,
our year-over-year figure can differ from the Bureau's own published rate by up to
0.1pp. We disclose that on the [methodology page](https://web-production-c9178.up.railway.app/methodology.html)
rather than quietly adjusting the numbers to match.)

**2. It goes down to 582 individually priced items, and back to 1971.** The breadth
series in this post cannot be pulled from anywhere else that I know of. Nor can
questions like: *what is the single largest contributor to Japanese inflation this
month?* (Optional motor insurance premiums, at +0.108pp. Not what anyone guesses.)

**3. Every view has a permanent URL.** Every link in this post is a live chart, not a
screenshot. Click one and you get the same view I was looking at, with the underlying
data one button away as CSV and the chart one button away as a PNG with its source
line embedded. If you cite this in a note, your reader can verify it. If you cite it in
a paper, the link still works.

Missing data is shown as `—`. Never as zero. Gaps stay gaps.

---

## What I'll be watching

**The tuition effect.** Japanese headline inflation is currently being held down by
roughly **0.16pp** of pure government policy — the free-tuition expansion, visible as
two clean step-downs in the education index in April 2025 and April 2026 and nothing
in between. Absent a third step, that drag mechanically drops out of the annual
comparison in April 2027 and headline inflation rises by about 0.16pp for no economic
reason at all. That's a whole post, and it's next.

**Rice.** Down 11.8% year-over-year, and still nearly **twice** its early-2023 price.
Both facts are true; only one is being reported.

**The bond market's opinion.** The 10-year JGB yields 3.01% and the 30-year 4.12%
against a 2.06% CPI. Somebody is wrong.

---

*Plover Analytics publishes Japanese price statistics, Bank of Japan
balance-sheet data, JGB yields and inbound tourism — free, cited, and versioned.
Source for all price data in this post: Statistics Bureau of Japan, via Plover Analytics.
Index levels are official; rates of change are calculated and carry their formulas.*

**[Open the inflation dashboard →](https://web-production-c9178.up.railway.app/cpi.html)**
