# Japanese equity, ownership and governance — outreach list, September 2026

Researchers working on Japanese cross-shareholdings, ownership, activism, boards, pay and
buybacks. Deliberately mixed: Japan-based and overseas. Every paper below was verified online
on 2026-09-16; affiliations are from institutional pages the same day.

Five names already have drafted emails in
[academic-outreach-2026-09.md](academic-outreach-2026-09.md) — Miyajima/Saito/Arikawa,
Tsumuraya, Tanabe/Ohnishi, Okamoto, Yada. They are in the table below for completeness; the
emails here are the new ones.

---

## Why this data matters (reuse in any email)

Japanese company disclosure is free, statutory and nearly unusable in bulk. Every annual
securities report on EDINET is an HTML document, not a dataset: the cross-shareholding table
(株式の保有状況), the register (大株主の状況), the board and pay sections, and the segment note
all have to be read out of the filing itself. That is why the literature keeps hand-collecting
the same tables from a few hundred TOPIX companies. Two disclosures also disappear: EDINET
purges buyback status reports after about a year, and AGM extraordinary reports leave the
public window, so nobody can go back and build the history later.

Plover Analytics reads those filings for every filer, keeps every figure exactly as filed with
the source document's id and SHA-256, computes nothing without showing the formula, and archives
each release so the data can be read as it stood on a date. It is free, with CSV exports, a
key-free JSON API and an MCP connector.

**What it holds, as of today:**

| Dataset | Coverage | Page |
| --- | --- | --- |
| Cross-shareholdings | 31,057 filings from FY2021; 2,814 filers and 38,111 named positions in the latest year; 17,279 reciprocal pairs; ¥59.9tn book value. Shares, carrying amount, prior-year shares, stated purpose, reciprocal flag — **both directions** | `/holdings.html` |
| Shareholder register | 5,217 companies, 51,752 top-ten rows, investor-category splits, nominee classification | `/ownership.html` |
| 5% reports | 74,266 filings since Aug 2021, 4,303 issuers, 6,520 filers, with the filed 重要提案行為等 answer (2,512 state one) | `/stakes.html` |
| AGM votes | 9,820 meetings at 3,971 companies from June 2023; 32,174 proposals; **61,895 individual director approval percentages** | `/agm.html` |
| Boards and pay | 4,311 companies, 42,078 board seats, ages, 1,386 individually disclosed ¥100m+ packages, employee salary and gender pay ratio | `/governance.html` |
| Buybacks | 6,683 monthly status reports since Aug 2025; 1,889 authorisations; ¥39.1tn authorised vs ¥30.6tn acquired; 5.5bn shares retired | `/buyback.html` |
| Segments and customers | 36,347 named-customer rows, 140,522 product rows, region revenue | `/company.html` |

## How to use it

1. **CSV:** every page has Download CSV; the file carries source, release and formula in its header.
2. **API, no key:** company datasets sit under `/api/v1/equity/…` —
   `…/company/{sec_code}` for one company, `…/companies` to search, `…/summary` for aggregates,
   and a screen endpoint per dataset (e.g. `/api/v1/equity/unwind`,
   `/api/v1/equity/stakes/holders`, `/api/v1/equity/agm/directors`).
   Series datasets use `/api/v1/{dataset}/observations`. Reference: `/api.html`.
3. **Point-in-time:** `as_of=YYYY-MM-DD`, or list `/api/v1/{dataset}/releases`.
4. **Ask it:** MCP connector at `https://ploveranalytics.com/mcp`.
5. **Cite it:** every page URL encodes the full view, e.g.
   `https://ploveranalytics.com/holdings.html?c=7203`.

---

## The list

| # | Who | Base | Their work | Fit |
| --- | --- | --- | --- | --- |
| 1 | Julian Franks (LBS), Colin Mayer (Oxford) | UK | "Managing Ownership by Management", SSRN 4809243 (2024), AEA 2025, CEPR column | Holdings + buybacks + register |
| 2 | Marco Becht (ULB), Franks, Jeremy Grant, Hannes Wagner (Bocconi) | EU | RFS 2017, 1,740 engagements in 23 countries; Japan the outlier | 5% tape + AGM outcomes |
| 3 | Curtis Milhaupt (Stanford Law) | US | "Corporate Governance in an Era of Geoeconomics" (2025); "(Geo)Politics of Controlling Shareholders" | Register, cross-holdings as control |
| 4 | Gen Goto (Tokyo) · Dan Puchniak (SMU) | JP + SG | ECGI Law WP 811/2024, "Faux Convergence in Asian Corporate Governance" | AGM votes, board data — form vs function |
| 5 | Bruce Aronson (NYU) · Manabu Matsunaka (Nagoya) | US + JP | ECGI Law WP 782/2024, hostile takeover framework | 5% tape, the proposal flag, defences |
| 6 | Kazunori Suzuki (Waseda Business School) · Masahiro Shibata | JP | Int. Review of Finance 2026, 26,792 GPIF engagements — finds governance engagement cuts cross-shareholdings | Exactly our holdings panel |
| 7 | Hideaki Sakawa · Naoki Watanabel (Nagoya City Univ.) | JP | PBFJ 2025, buyback anomalies (pre-registered); board diversity and payouts | Buybacks + retirements |
| 8 | Konari Uchida (Waseda Business School) | JP | "Stewardship Responsibility and Payout Premium" (SSRN 5385864) | Buybacks, register, AGM |
| 9 | Katsuyuki Kubo (Waseda) | JP | "CEO skill and firm performance", JJIE 2025 | 1,386 named ¥100m+ packages, 42,078 seats |
| 10 | Toru Yoshikawa (Waseda) · Daisuke Uchida (Keio) | JP | CGIR 2025, dissent in director elections, 7,887 proposals, TOPIX 500 | 61,895 director results, whole market |
| 11 | Fumiko Takeda (Keio) | JP | IAER 2026, activist board representation, 8 cases | 5% tape + AGM, all issuers |
| 12 | Ryo Sakai (Vanderbilt) | US | arXiv 2206.00640, activism in Japan — could find only 2 cases | Same, now countable |
| 13 | Charoenwong · Wiwattanakantang (NUS) · Morck (Alberta) | SG + CA | Review of Finance 2021, BOJ equity purchases | Governance panel for the follow-up |
| 14 | Vikas Mehrotra (Alberta), Morck, Jungwook Shim (Kyoto Sangyo), Wiwattanakantang | CA + JP | "Adoptive Expectations: Rising Sons in Japanese Family Firms" | Register + boards: family control today |
| 15 | Buchanan · Chai · Deakin (Cambridge) | UK | *Hedge Fund Activism in Japan*; SER 2020 | 5% tape with the proposal flag |
| 16 | Gregory Jackson (Freie Universität Berlin) | DE | Comparative governance; ex-RIETI | Cross-country panel inputs |
| 17 | Nicholas Benes (BDTI, Tokyo) | Tokyo | Proposed Japan's Corporate Governance Code; runs director training | Multiplier, not a citation |
| 18–22 | Miyajima/Saito/Arikawa · Tsumuraya · Tanabe/Ohnishi · Okamoto · Yada | JP | See the other file | Cross-shareholdings |

**Send order:** 1, 6, 10 first — the tightest fits. Then 2, 5, 7, 11, 12. Then the rest.

---

## 1. Franks · Mayer — LBS / Oxford

**Subject:** Both legs of the treasury-stock-to-strategic-investor trade, filing by filing

Dear Professor Franks, Professor Mayer,

"Managing Ownership by Management" describes Japanese boards buying blocks into treasury and placing them with strategic corporate investors. Both legs are now in one free dataset at filing level.

Plover Analytics holds every named policy shareholding disclosed in annual securities reports since FY2021 — the receiving side, with shares, carrying amount, prior-year shares, the stated purpose and whether the holding is reciprocal (38,111 positions across 2,814 filers in the latest year; 17,279 reciprocal pairs). Beside it: every monthly buyback status report since August 2025 — authorised versus acquired, and shares actually retired — and the top-ten register for 5,217 companies. Every row carries its EDINET document id and the archived file's hash.

https://ploveranalytics.com/holdings.html · https://ploveranalytics.com/buyback.html

One honest limit: the buyback archive begins in August 2025 because EDINET deletes those reports after a year — which is why it is worth keeping. If a firm-year panel in your layout would help, tell me the columns and I will cut it.

Best regards,
[Name]
Plover Analytics · Asia Economics Observations

---

## 2. Becht · Franks · Grant · Wagner

**Subject:** Japan was the outlier in your international activism study — here is the tape that explains it

Dear Professor Becht, Professor Franks, Dr Grant, Professor Wagner,

Your RFS study of 1,740 engagements across 23 countries found Japan the exception: high initial expectations, low outcomes. Two Japanese disclosures now make the engagement and its result countable for the whole market, and I publish both free.

Every 5% large-shareholding report since August 2021 — 74,266 filings, 4,303 issuers, 6,520 filers — each carrying the holder's own statutory answer to 重要提案行為等, whether it may make important proposals to the board; 2,512 filings say yes. And every AGM vote disclosed since June 2023: 9,820 meetings at 3,971 companies, 32,174 proposals, and each director's own approval percentage — 61,895 of them.

https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/agm.html

Campaign and outcome from the same filings, without hand-collection. If a Japan extension is of interest, I will prepare the panel.

Best regards,
[Name]
Plover Analytics

---

## 6. Suzuki · Shibata

**Subject:** Your GPIF result on cross-shareholdings, testable filer by filer

Dear Professor Suzuki, Mr Shibata,

Your International Review of Finance paper on GPIF's 26,792 engagements finds that governance engagements reduce cross-shareholdings. That outcome variable is now observable at full depth, free.

Plover Analytics holds every named policy shareholding in every annual securities report since FY2021 — not a total, but each position: shares, carrying amount, prior-year shares, the purpose as written, and whether the issuer holds the filer in return. 38,111 named positions across 2,814 filers in the latest year. One screen ranks filers by holdings moved to the 純投資 bucket rather than sold, which is the obvious confound for any "reduction" measure.

https://ploveranalytics.com/holdings.html

Also here: the register with foreign and institutional category splits, AGM votes, and boards. If a merged panel would help the next paper, say the shape and I will produce it.

Best regards,
[Name]
Plover Analytics

---

## 10. Yoshikawa · Daisuke Uchida

**Subject:** 61,895 director approval percentages — your CGIR sample, beyond the TOPIX 500

Dear Professor Yoshikawa, Professor Uchida,

Your CGIR paper analysed 7,887 proposals covering 5,051 directors at 495 TOPIX 500 firms — hand-built from the extraordinary reports. I have done that extraction for the whole market and it is free.

Plover Analytics holds every AGM disclosure since June 2023: 9,820 meetings at 3,971 companies, 32,174 proposals including shareholder proposals, and for board elections each director's own approval percentage — 61,895 results — with the filer's partial-tally flag, the for/against/abstain counts where disclosed, and a dissent figure whose formula is shown. Alongside it: the register (foreign and domestic institutional shares, the variable your interaction turns on), board composition and pay.

https://ploveranalytics.com/agm.html · https://ploveranalytics.com/ownership.html

If extending the sample is of interest, I will provide it in your layout.

Best regards,
[Name]
Plover Analytics

---

## 3. Milhaupt

**Subject:** Who actually controls Japanese firms — the register and the cross-holding graph, free

Dear Professor Milhaupt,

Your work on controlling shareholders and on governance under geoeconomic pressure turns on who really holds a firm. For Japan that is unusually hard to see, because two nominee trust banks sit at the top of nearly every register.

Plover Analytics publishes, from the filings themselves: the top-ten register for 5,217 companies with each holder classified (nominee, trust account, global custodian, employee association, retirement-benefit trust, natural person) so nominee-excluded rankings are possible; the investor-category split; and every named cross-shareholding both directions since FY2021, with 17,279 reciprocal pairs — the corporate-bloc structure, as disclosed rather than inferred.

https://ploveranalytics.com/ownership.html · https://ploveranalytics.com/holdings.html

Free, with CSV and API. If a cut would be useful for a comparative chapter, I would be glad to prepare it.

With respect,
[Name]
Plover Analytics

---

## 4. Goto · Puchniak

**Subject:** Testing faux convergence with what Japanese filers actually disclose

Dear Professor Goto, Professor Puchniak,

"Faux Convergence in Asian Corporate Governance" argues that Anglo-American forms are adopted while their function is adapted locally. Japan now discloses enough to measure the gap, and I publish it free.

Plover Analytics holds every director at 4,311 companies with role, age and shares held (42,078 seats; 269 boards with no women; 1,386 individuals disclosed at ¥100m or more); every AGM result since June 2023 including each director's own approval percentage; and every named cross-shareholding since FY2021 with the purpose text as written — which is where a code-compliant justification either says something or does not.

https://ploveranalytics.com/governance.html · https://ploveranalytics.com/agm.html · https://ploveranalytics.com/holdings.html

If a table for the Japan sections would help, tell me the shape and I will prepare it.

Best regards,
[Name]
Plover Analytics

---

## 5. Aronson · Matsunaka

**Subject:** The 5% tape as evidence for the takeover-framework argument

Dear Professor Aronson, Professor Matsunaka,

Your ECGI paper on redesigning Japan's hostile-takeover framework notes how much rests on the bulk-shareholding report. That report is now a dataset.

Plover Analytics holds every 大量保有報告書 and 変更報告書 since August 2021 — 74,266 filings, 4,303 issuers, 6,520 filers — as filed: the statutory ratio, the trigger date, the filing date, the joint-holder group, and the answer to 重要提案行為等 where the form asks it (2,512 filings state an act). The median gap between trigger and filing is 7 days, which is itself evidence about the disclosure window you discuss. Beside it, AGM outcomes and board data.

https://ploveranalytics.com/stakes.html

Free, CSV and API, every row linked to its filing. If a table for the article would help, I will produce it.

Best regards,
[Name]
Plover Analytics

---

## 7. Sakawa · Watanabel

**Subject:** Execution-level buyback data — authorised vs acquired vs retired, monthly

Dear Professor Sakawa, Professor Watanabel,

Your pre-registered Pacific-Basin study of buyback anomalies in Japan works from announced programmes. What is rarely used is the monthly execution report filed underneath them.

Plover Analytics holds every 自己株券買付状況報告書 since August 2025: 6,683 filings, 1,305 companies, 1,889 authorisations — what the board authorised (¥39.1tn), what was actually bought month by month (¥30.6tn), what expired unspent (¥1.5tn), and shares actually retired (5.5bn shares, 211 companies). Announcement and execution are different events, and the gap between them is measurable here for the first time.

https://ploveranalytics.com/buyback.html

Honest limit: the series starts in August 2025 because EDINET purges these filings after a year — so it only grows from here. Free, CSV and API. If a panel would help the next pre-registration, I will cut it.

Best regards,
[Name]
Plover Analytics

---

## 8. Konari Uchida

**Subject:** Payout, register and stewardship data in one place

Dear Professor Uchida,

Your work with Moriyasu and Shinozaki on stewardship responsibility and the payout premium needs payout behaviour on one side and ownership on the other. Plover Analytics carries both, free, from the filings.

Payout: every monthly buyback status report since August 2025 — authorised, acquired, unspent, and shares retired — 1,889 authorisations at 1,305 companies. Ownership: the top-ten register for 5,217 companies with nominee accounts classified and the investor-category split, so domestic and foreign institutional shares are separable. Governance outcome: 61,895 individual director approval percentages since June 2023.

https://ploveranalytics.com/buyback.html · https://ploveranalytics.com/ownership.html · https://ploveranalytics.com/agm.html

If a merged panel would save time, tell me the layout and I will produce it.

Best regards,
[Name]
Plover Analytics

---

## 9. Kubo

**Subject:** Individually disclosed executive pay, every filer, machine-readable

Dear Professor Kubo,

Your work on Japanese executive pay — from the 1986–95 panel to the CEO-skill paper this year — has always been limited by what can be extracted from the filings. That extraction is now done for every filer and free.

Plover Analytics holds, from annual securities reports since FY2021: every director as filed with role, age and shares held (42,078 seats at 4,311 companies); officer remuneration by category; and every individually disclosed package for anyone paid ¥100m or more — 1,386 people — on the consolidated 連結報酬等 basis, plus average employee salary and the filed gender pay ratio.

https://ploveranalytics.com/governance.html

One caution the data carries explicitly: named pay and the category table are different bases and must not be netted — filings where the two contradict each other are flagged rather than silently fixed. Free, CSV and API. If a panel in your layout would help, I will cut it.

Best regards,
[Name]
Plover Analytics

---

## 11. Takeda

**Subject:** Beyond eight cases — every activist filing and every AGM result

Dear Professor Takeda,

Your International Advances in Economic Research paper on activist board representation in Japan works from eight cases, because identifying the population is the hard part. Two disclosures now make it countable, and I publish both free.

Every 5% report since August 2021 — 74,266 filings, 4,303 issuers — carrying the holder's own answer to 重要提案行為等 (2,512 filings state an act), which separates a campaign from a passive stake using the filer's words rather than a press count. And every AGM result since June 2023: 61,895 individual director approval percentages at 3,971 companies, so a board seat won or a director's support collapsing is visible directly.

https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/agm.html

If a larger sample would be useful, I will prepare the panel.

Best regards,
[Name]
Plover Analytics

---

## 12. Ryo Sakai

**Subject:** You found two cases in 2022 — there are now thousands of filings to work with

Dear Mr Sakai,

Your 2022 study of activist board representation in Japan was limited to Kawasaki Kisen and Olympus, because those were the identifiable cases. The disclosure landscape has changed and the data is now free.

Plover Analytics holds every 5% large-shareholding report since August 2021 — 74,266 filings, 4,303 issuers, 6,520 filers — with the filed important-proposal answer (2,512 filings state one), and every disclosed AGM vote since June 2023, including each director's own approval percentage across 3,971 companies. A population of engagements and their outcomes, from the filings, with every row traceable.

https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/agm.html

If you are still working on this, I would be glad to help with the data — and to hear what an extension would need.

Best regards,
[Name]
Plover Analytics

---

## 13. Charoenwong · Wiwattanakantang · Morck

**Subject:** Firm-level Japanese governance data for the post-programme follow-up

Dear Professor Charoenwong, Professor Wiwattanakantang, Professor Morck,

Your Review of Finance paper found the BOJ's equity purchases raised investment only at firms with worse governance. Testing what happened as the programme wound down, and as cross-shareholdings unwound, needs firm-level governance data that has been expensive to build for Japan. It is now free.

From EDINET: every named cross-shareholding since FY2021 both directions; the top-ten register for 5,217 companies with nominees classified; 74,266 5% reports; boards, ages and pay for 4,311 companies; buybacks and retirements. Plus the BOJ's own ETF holdings and flows monthly from 1996.

https://ploveranalytics.com/holdings.html · https://ploveranalytics.com/governance.html · https://ploveranalytics.com/boj.html

If a governance index merged to firm-years would help, tell me the components and I will build it.

Best regards,
[Name]
Plover Analytics

---

## 14. Mehrotra · Morck · Shim · Wiwattanakantang

**Subject:** Japanese family control today — register and boards, every filer

Dear Professor Mehrotra and colleagues,

"Adoptive Expectations" used a 40-year postwar panel to show how family firms in Japan pass on control. The modern equivalent of that panel is now assembled from the filings and free.

Plover Analytics holds the top-ten register for 5,217 companies — with natural persons classified separately from nominees and trust accounts, so family holdings are visible — plus every director with name, role, age and shares held (42,078 seats at 4,311 companies), and every named cross-shareholding both directions since FY2021.

https://ploveranalytics.com/ownership.html · https://ploveranalytics.com/governance.html

Free, CSV and API, every row traceable to its filing. If a founder- or family-control panel would be useful for an update, I will prepare it.

Best regards,
[Name]
Plover Analytics

---

## 15. Buchanan · Chai · Deakin

**Subject:** Activism in Japan, now with the filers' own words

Dear Dr Buchanan, Dr Chai, Professor Deakin,

Your work on hedge fund activism in Japan covered a period before a disclosure that now makes the phenomenon countable: every 5% report carries the holder's own answer to 重要提案行為等 — whether it may make important proposals to the board.

Plover Analytics holds 74,266 such filings since August 2021 across 4,303 issuers, with that flag on 2,512 of them, plus every AGM proposal and result since June 2023 including shareholder proposals and each director's approval percentage. The campaign and its outcome, from statutory filings rather than press reports — which bears directly on the "quiet activism" question.

https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/agm.html

Free, with CSV and API. If a revisit is on your list, I would be glad to help with the data.

Best regards,
[Name]
Plover Analytics

---

## 16. Gregory Jackson

**Subject:** Japanese governance variables for comparative work, free and traceable

Dear Professor Jackson,

Your comparative work on corporate governance and ownership usually needs Japanese variables that are expensive to obtain and hard to document. Plover Analytics now builds them from the statutory filings and gives them away.

Board composition, ages and female officer ratios for 4,311 companies; officer pay by category plus 1,386 individually disclosed packages; the shareholder register with investor categories and nominee classification for 5,217 companies; every named cross-shareholding both directions since FY2021; 61,895 director approval percentages. Each figure as filed, each row linked to its source document and hash — which matters when a referee asks where a governance index came from.

https://ploveranalytics.com/governance.html · https://ploveranalytics.com/ownership.html

If a country-level series would help, tell me the definition and I will compute it with the formula shown.

Best regards,
[Name]
Plover Analytics

---

## 17. Nicholas Benes — a multiplier, not a citation

**Subject:** Governance data from the filings, free — useful for BDTI?

Dear Mr Benes,

You argued for Japan's Corporate Governance Code before it existed, and BDTI has been making board practice legible ever since. I have built something adjacent and free, and would value your view on it.

Plover Analytics reads Japanese statutory filings into data: boards, ages and pay for 4,311 companies including every ¥100m+ package; 61,895 individual director approval percentages since June 2023; every named cross-shareholding both directions since FY2021; the register with nominee accounts classified; buybacks and retirements. Everything as filed, traceable to the document, free to use and cite.

https://ploveranalytics.com/governance.html · https://ploveranalytics.com/agm.html

Two questions, if you have ten minutes: which figures would actually change a boardroom conversation, and what is missing that ought to exist? I would rather build to that than guess.

Best regards,
[Name]
Plover Analytics

---

## After sending

Watch the People column on the admin traffic page and the referrer table. A reply asking for a
data cut is a citation in waiting — deliver it within a day, and put the result on a permanent
URL so the paper can cite the page rather than a file you emailed.
