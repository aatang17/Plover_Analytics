# International outreach — September 2026

Researchers outside Japan, or outside Japanese academia, whose current work runs on data
Plover Analytics publishes. Every paper was verified online on 2026-09-16. Coverage figures
come from the live catalogue the same day — each email claims only what the site holds.

---

## Why it matters, in one paragraph (reuse anywhere)

Japan's official statistics and company disclosures are free, deep and scattered across a dozen
ministries, e-Stat, EDINET and the BOJ — each with its own format, encoding, base year and
revision habits, and several that delete history as they go (EDINET purges buyback reports after a
year; the MOF debt workbook is a rolling five-year window). Plover Analytics collects them into one
place, keeps every published value exactly as published with its source file's hash, computes every
rate with its formula shown, and — the part nothing else offers — archives each release as a
vintage, so the data can be read as it stood on any date. It is free, and every view has a
permanent URL that can be cited.

## How to use it, in four lines

1. **Read it:** open the page (`/cpi.html`, `/holdings.html`, `/boj.html`, `/semis.html`,
   `/agm.html`, `/rates.html`, `/population.html`, `/fiscal.html`, `/gdp.html`, `/stakes.html`)
   and press **Download CSV** — the file carries a header block with source, release and formula.
2. **Pull it:** the JSON API needs no key. List series, then fetch:
   `https://ploveranalytics.com/api/v1/cpi-jp-items/series` →
   `https://ploveranalytics.com/api/v1/cpi-jp-items/observations?series=1001&measure=yoy`.
   Same shape for every series dataset; company datasets sit under `/api/v1/equity/…`.
   Reference: `https://ploveranalytics.com/api.html`.
3. **Read it as of a date:** add `as_of=YYYY-MM-DD` to an observations call, or list
   `/api/v1/{dataset}/releases` — each release has its date and the archived file's SHA-256.
4. **Ask it:** an MCP connector at `https://ploveranalytics.com/mcp` lets Claude, ChatGPT or any
   MCP client query the same endpoints, with every number carrying its trust label.

Cite the page URL with its query string; it encodes the full view.

---

## The list

| # | Who | Their work | What we have that fits |
| --- | --- | --- | --- |
| 1 | Julian Franks (LBS), Colin Mayer (Oxford) | "Managing Ownership by Management" — ECGI Finance WP / SSRN 4809243 (Apr 2024), AEA 2025, VoxEU 2025: treasury-stock blocks sold to strategic corporate investors | Cross-shareholdings both directions FY2021–; buybacks and retirements from Aug 2025 |
| 2 | Kenneth Rogoff (Harvard), Takeshi Tashiro (PIIE) | PIIE WP25-22 (Dec 2025), "Perspectives on Japan's continuing exorbitant privilege" | Daily JGB curve since 1974; central-government debt by instrument; BOJ balance sheet 1996– |
| 3 | Yan Carrière-Swallow, Gene Kindberg-Hanlon, Danila Smirnov (IMF) | IMF WP 2025/227 (Nov 2025), macro effects and spillovers of BOJ unconventional policy | BOJ MD09 stocks and flows monthly; JGB curve; CPI vintages |
| 4 | Chad Bown (PIIE) | "Semiconductors and Modern Industrial Policy" (JEP 2024, w/ Dan Wang); WP25-11; WP26-5 | Semiconductor trade by partner, monthly 2001–, each MOF revision stage kept as a vintage |
| 5 | Chris Miller (Tufts, Fletcher) | *Chip War*; Japan's equipment exports to China | Same series, equipment lines by partner |
| 6 | Kazunobu Hayakawa (IDE-JETRO), Fukunari Kimura (Keio), Kenta Yamanouchi | JJIE 2025, "The trade effects of export control regulations in Japan" — the 23 SME/SIE items controlled from July 2023 | Monthly partner-level values and quantities, with preliminary → final vintages |
| 7 | Ryo Makioka, Hongyong Zhang (RIETI) | JJIE 2024, Japan–Korea semiconductor-materials dispute | Semiconductor inputs by partner (wafers, HS detail) |
| 8 | Fernando Ortiz-Moya (IGES), Sarah Sieloff (Haley & Aldrich) | Geografiska Annaler B 2025, planning responses in Yubari, Suzu, Manazuru | Every municipality's register: residents, age bands, natural vs social change, foreign share |
| 9 | Selahattin İmrohoroğlu (USC) | Fiscal sustainability under ageing, with Kitao and Yamada | Prefecture population, general-account budget and settlement, tax receipts, debt |
| 10 | Toru Yoshikawa (Waseda), Daisuke Uchida (Keio) | CGIR 2025, shareholder dissent in director elections, 7,887 proposals, TOPIX 500 | AGM votes for 3,971 companies: 61,895 director results, every proposal, from June 2023 |
| 11 | Shigenori Shiratsuka (Keio) | HIAS-E-146 (Sep 2025), "Toward a Guidepost for Quantitative Tightening: the BOJ" | BOJ JGB gross purchases and redemptions monthly 1996– |
| 12 | John Buchanan, Dominic Chai, Simon Deakin (Cambridge) | *Hedge Fund Activism in Japan*; Socio-Economic Review 2020 | 5% tape with the filed important-proposal flag; shareholder proposals and their votes |
| 13 | Masumi Sai, Kazuo Yamada | RIBAF 2025, passive ownership and governance under the BOJ ETF programme | BOJ ETF holdings monthly; AGM outcomes as the governance variable |

Send 1, 2, 4 first — biggest names, tightest fit. Then 3, 6, 10. Then the rest.

---

## 1. Franks · Mayer

**Subject:** The treasury-stock-to-strategic-investor transactions in "Managing Ownership by Management", filing by filing

Dear Professor Franks, Professor Mayer,

"Managing Ownership by Management" documents Japanese boards buying blocks into treasury and placing them with strategic corporate investors. Both legs of that transaction are now visible in one free dataset, at filing level.

Plover Analytics holds every named policy shareholding disclosed in annual securities reports on EDINET since FY2021 — the receiving side, with shares, book value, prior-year shares, the stated purpose and whether the holding is reciprocal (38,111 positions in the latest year, 17,279 reciprocal pairs). Alongside it, every monthly buyback status report since August 2025: what the board authorised, what was bought month by month, and what was retired. Every row links to its source document and hash.

https://ploveranalytics.com/holdings.html · https://ploveranalytics.com/buyback.html

Honest limit: the buyback archive starts in August 2025 because EDINET deletes those reports after a year — which is also why it is worth keeping. If a firm-year panel would help, tell me the shape and I will cut it.

Best regards,
[Name]
Plover Analytics · Asia Economics Observations

---

## 2. Rogoff · Tashiro

**Subject:** Daily JGB curve since 1974 and the debt stock by instrument, free, for the exorbitant-privilege work

Dear Professor Rogoff, Dr Tashiro,

Your December working paper ties Japan's continuing privilege to its ability to keep issuing safe debt at scale and to keep inflation in hand. The series that test that are on one free site, as published, by API.

Plover Analytics carries the Ministry of Finance constant-maturity JGB curve, 15 maturities, every business day since September 1974; central-government debt outstanding by instrument and tenor (stitched across the Ministry's rolling five-year workbooks, which otherwise lose history); the BOJ's balance sheet monthly from 1996; quarterly GDP; and CPI at item level from 1970, with every release archived as a vintage.

https://ploveranalytics.com/rates.html · https://ploveranalytics.com/fiscal.html · https://ploveranalytics.com/boj.html

Every figure is exactly as published, every computed spread shows its formula, and every view has a citable URL. If a ready-made panel would save a research assistant a week, say which series and I will produce it.

With respect,
[Name]
Plover Analytics

---

## 3. Carrière-Swallow · Kindberg-Hanlon · Smirnov (IMF)

**Subject:** BOJ balance-sheet stocks and flows, monthly since 1996, as vintages — for WP 2025/227 follow-ups

Dear Dr Carrière-Swallow, Dr Kindberg-Hanlon, Dr Smirnov,

Your working paper on the macroeconomic effects and spillovers of the BOJ's unconventional policy needs the programme's time path in one consistent form. I publish it that way, free.

Plover Analytics serves the BOJ's MD09 table — JGB gross purchases and redemptions, T-bills, ETFs, J-REITs, loans, the monetary base — monthly from July 1996 in ¥100mn, exactly as published with the BOJ's credit line; the daily JGB curve since 1974; and item-level CPI from 1970. Every release is archived and queryable as of a date, which matters for anything estimated on real-time information.

https://ploveranalytics.com/boj.html — JSON API, CSV with a metadata header, MCP connector.

If a specific layout would help the next round, tell me and I will provide it.

Best regards,
[Name]
Plover Analytics

---

## 4. Bown

**Subject:** Japan's semiconductor trade by partner, monthly since 2001, every MOF revision stage kept

Dear Dr Bown,

Your work on semiconductor industrial policy and export restrictions leans on partner-level trade flows, and Japan's are awkward to assemble: e-Stat, an API key, cp932 files, and four revision stages that overwrite each other.

Plover Analytics publishes Japan's semiconductor, component and equipment exports and imports by partner country, monthly from January 2001, customs value and quantity exactly as the Ministry of Finance releases them — and keeps each stage (preliminary, confirmed, revised, final) as its own vintage, so the number you cited in March is still the number you cited. Partner shares, 12-month totals and unit values are computed with the formula shown.

https://ploveranalytics.com/semis.html — CSV and JSON API, no key.

If a particular cut — equipment lines to China since the July 2023 controls, say — would be useful, I will prepare it.

Best regards,
[Name]
Plover Analytics

---

## 5. Miller

**Subject:** Japan's chip-equipment exports to China, month by month, as published

Dear Professor Miller,

For anyone writing about Japan's place in the chip supply chain, the monthly Ministry of Finance trade tables by partner are the primary source and the most tedious to get at. I publish them cleaned and free: semiconductor, component and manufacturing-equipment exports and imports by partner country since January 2001, exactly as released, with each revision stage archived.

https://ploveranalytics.com/semis.html — every chart has a permanent citable URL and a CSV with the source in its header.

If a chart of Japanese equipment exports to China around the 2023 controls would be useful for a piece, it is one click; say the word and I will send the link.

Best regards,
[Name]
Plover Analytics

---

## 6. Hayakawa · Kimura · Yamanouchi

**Subject:** Monthly partner-level series for the July 2023 SME controls, with preliminary-to-final vintages

Dear Professor Kimura, Dr Hayakawa, Dr Yamanouchi,

Your JJIE paper on the trade effects of Japan's July 2023 export controls on semiconductor manufacturing and inspection equipment is the cleanest study of that episode I have read. For the follow-up, Plover Analytics may save the data step.

It serves Japan's semiconductor-related exports and imports by partner country, monthly from 2001, value and quantity as published by the Ministry of Finance, with each revision stage — 速報, 確報, 確々報, 確定 — stored as its own vintage and queryable as of a date. That makes a replication package reproducible to the figure.

https://ploveranalytics.com/semis.html — CSV and API, free.

If a panel in your layout would help, I will cut it.

Best regards,
[Name]
Plover Analytics

---

## 7. Makioka · Zhang

**Subject:** Semiconductor materials and wafers by partner — the Japan–Korea dispute series, monthly since 2001

Dear Dr Makioka, Dr Zhang,

Your JJIE paper on the 2019 Japan–Korea dispute measured what the controls did to trade in semiconductor materials. Plover Analytics publishes the monthly partner-level Ministry of Finance series those estimates rest on — semiconductor inputs including wafers at HS detail, plus components and equipment — from 2001, as published, with revision stages kept as vintages.

https://ploveranalytics.com/semis.html — free, CSV and JSON API.

If it is useful for an update, tell me the series and layout and I will provide them.

Best regards,
[Name]
Plover Analytics

---

## 8. Ortiz-Moya · Sieloff

**Subject:** Every Japanese municipality's population register, with natural vs social change — for the shrinking-cities work

Dear Dr Ortiz-Moya, Ms Sieloff,

Your Geografiska Annaler paper on Yubari, Suzu and Manazuru sets out a framework for how municipalities respond to decline. Plover Analytics can supply the population side for all of them at once.

It publishes the Basic Resident Register for every municipality in Japan — about 1,900 cities, towns, villages and wards — as of 1 January: residents, households, five-year age bands by sex, Japanese and foreign residents, births, deaths and moves, with the split between natural and social change and the aged-65+ share computed and shown with its formula. Prefecture series run further back.

https://ploveranalytics.com/population.html — free, CSV and API.

Honest limit: the municipal register here begins with the 2025 edition; it grows a year at a time. If a table for your three cases or a wider set would help, I will prepare it.

Best regards,
[Name]
Plover Analytics

---

## 9. İmrohoroğlu

**Subject:** Japanese fiscal and demographic inputs, as published, in one place

Dear Professor İmrohoroğlu,

Your work with Sagiri Kitao and Tomoaki Yamada on fiscal balance under Japan's ageing rests on inputs — population by age, the general account, tax receipts, the debt stock — that live on separate ministry sites in separate formats. Plover Analytics puts them on one free site, as published, by API.

It carries prefecture and municipal population with age bands, the general-account budget and settlement by purpose and by ministry, national tax receipts budget versus outturn, central-government debt by instrument, and quarterly GDP — each with its source, its release archived, and every computed ratio carrying its formula.

https://ploveranalytics.com/fiscal.html · https://ploveranalytics.com/population.html

If a calibration table in a particular shape would help, tell me and I will produce it.

With respect,
[Name]
Plover Analytics

---

## 10. Yoshikawa · Uchida

**Subject:** Every director's approval percentage at every disclosed AGM since 2023 — 61,895 results

Dear Professor Yoshikawa, Professor Uchida,

Your CGIR paper analysed 7,887 director-election proposals for TOPIX 500 firms. Plover Analytics now holds the same disclosures for the whole market, free.

From the extraordinary reports that publish AGM votes: 9,820 meetings at 3,971 companies from June 2023, every proposal's result, and for board elections every director's own approval percentage — 61,895 director results — with the filer's partial-tally flag, votes for/against/abstain where disclosed, and a computed dissent figure whose formula is shown. Every row links to its filing.

https://ploveranalytics.com/agm.html — CSV and JSON API.

If extending your sample beyond the TOPIX 500 is of interest, I will provide the panel in your layout.

Best regards,
[Name]
Plover Analytics

---

## 11. Shiratsuka

**Subject:** BOJ JGB purchases and redemptions, monthly since 1996, for the QT guidepost

Dear Professor Shiratsuka,

Your HIAS paper on a guidepost for the BOJ's quantitative tightening turns on the path of gross purchases against redemptions. Plover Analytics serves that from the BOJ's MD09 table — JGB gross purchases, redemptions, holdings, T-bills, ETFs, the monetary base — monthly from July 1996, as published, with the BOJ credit line, by API and CSV, and with every release archived.

https://ploveranalytics.com/boj.html

If a particular series layout would help, I will prepare it.

With respect,
[Name]
Plover Analytics

---

## 12. Buchanan · Chai · Deakin

**Subject:** The 5% tape with the filed "important proposal" flag — activism in Japan, as disclosed

Dear Dr Buchanan, Dr Chai, Professor Deakin,

Your work on hedge fund activism in Japan predates a disclosure that now makes the phenomenon countable: every 5% report on EDINET carries the holder's own answer to 重要提案行為等 — whether it may make important proposals to the board.

Plover Analytics holds every large-shareholding report since August 2021 — 74,266 filings, 4,303 issuers — with that flag (2,512 filings state one), plus every AGM proposal and vote since June 2023 including shareholder proposals. Together they give the campaign and the outcome from the same filings, free.

https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/agm.html

If a revisit of the 2001–2008 period's successors is on your list, I would be glad to help with the data.

Best regards,
[Name]
Plover Analytics

---

## 13. Sai · Yamada

**Subject:** BOJ ETF holdings monthly, and AGM outcomes for the governance side

Dear Dr Sai, Dr Yamada,

Your RIBAF paper on passive ownership under the BOJ's ETF programme needed both the programme's path and a governance outcome. Plover Analytics publishes both, free: the BOJ's ETF holdings and monthly flows from the MD09 table since 1996, and every disclosed AGM vote since June 2023 — each director's approval percentage at 3,971 companies.

https://ploveranalytics.com/boj.html · https://ploveranalytics.com/agm.html

If it is useful for an extension, I will provide a merged panel in your layout.

Best regards,
[Name]
Plover Analytics
