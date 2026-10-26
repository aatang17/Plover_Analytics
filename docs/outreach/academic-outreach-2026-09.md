# Academic outreach — September 2026

Ten researchers whose published work uses exactly the data Plover Analytics now holds.
Every paper below was verified online on 2026-09-16; nothing here is guessed. Each email
names their paper, says what we have that they built by hand, and asks one thing.

Facts used in the emails (from the live catalogue, same day):

- **Cross-shareholdings** (`/holdings.html`): every named 政策保有株式 in every annual securities
  report, both directions — 31,057 filings from FY2021, 2,814 filers in the latest year, 38,111
  named positions, 17,279 reciprocal pairs. Shares, book value, prior-year shares, stated purpose,
  reciprocal flag. Every row carries the EDINET doc_id and the archived file's SHA-256.
- **5% tape** (`/stakes.html`): 74,266 large-shareholding reports since August 2021, 4,303 issuers,
  with the filed 重要提案行為等 answer (2,512 filings state one).
- **BOJ** (`/boj.html`): MD09 stocks and flows — JGBs, ETFs, J-REITs, monetary base — monthly
  from July 1996, ¥100mn, as published.
- **CPI items** (`/cpi.html`): all 589 individually priced items, monthly from January 1970 on the
  2025 base, weights in parts per 10,000, breadth of price rises.
- **Vintages**: every release archived with its SHA-256 and queryable as-of a date. Honest
  caveat: the CPI/BOJ vintage history only starts when our ingest started (August 2026). Do not
  claim decades of vintages to anyone.
- Everything is free, has a CSV export with a metadata header, a JSON API, and an MCP connector.

Contact routes are institutional pages (researchmap, university directory) — no addresses are
listed here; find them on the linked pages.

---

## The list

| # | Who | Their paper | What we have that they need |
| --- | --- | --- | --- |
| 1 | Hideaki Miyajima (Waseda / RIETI), Takuji Saito (Keio), Yasuhiro Arikawa (Waseda) | RIETI PDP 25-P-014 (Sep 2025); 23-E-077 "Has reduction in strategic shareholding been achieved?" | The named-holdings panel; the "reclassified, not sold" screen answers their own question |
| 2 | Shoichi Tsumuraya 円谷昭一 (Hitotsubashi) | 『政策保有株式の実証分析』(2020) — read the 有報 himself | Same data, every filer, every year since FY2021, machine-readable |
| 3 | Shinichiro Tanabe, Takaaki Ohnishi (Rikkyo; Ohnishi also CIGS) | PLoS One 2026, cross-shareholding networks 2001–2023 from Nikkei NEEDS | The holder-side edge list NEEDS doesn't have: named policy holdings with value and purpose |
| 4 | Noriaki Okamoto (Rikkyo) | J. Management & Governance, financialisation and cross-shareholding disclosure | Purpose text as filed, reclassification flows — the performativity he argues |
| 5 | Kanae Yada 矢田歌菜絵 (Daiwa Institute of Research) | 政策保有株式の縮減状況 2024年版 (Nov 2025), TOPIX500 by hand | All 2,814 filers, not 500; both directions |
| 6 | Mitsuru Katagiri, Junnosuke Shino (Waseda), Koji Takahashi (BIS) | J. Financial Markets 2025; Rev. Asset Pricing Studies 2025 — BOJ ETF programme | BOJ ETF stocks/flows monthly 1996– by API; ownership data for controls |
| 7 | Ben Charoenwong, Yupana Wiwattanakantang (NUS), Randall Morck (Alberta) | Review of Finance 2021, "Bank of Japan Equity Purchases" | Same BOJ series plus firm-level governance data (cross-holdings, 5% tape, boards) |
| 8 | Kozo Ueda (Waseda) | Japanese Economic Review 2024, "Evidence on price stickiness in Japan" | 589-item CPI 1970– on one base, breadth measure, API |
| 9 | Toshiaki Shoji (Seikei; visiting Hitotsubashi to Aug 2026, Melbourne from Sep 2026) | Empirical Economics 2024, inflation heterogeneity over the life cycle; RIW 2026 | Item indices plus weights per 10,000 — what reweighting by household type needs |
| 10 | Fumio Hayashi (GRIPS), Yuta Tachi (Reitaku) | Empirical Economics 2022, "Nowcasting Japan's GDP" — "genuine real-time data" | A point-in-time archive being built forward; ask what coverage they would need |

Order to send: 1, 2, 5 first (cross-shareholdings is the strongest match and the data is
unique). Then 3, 4, 6, 7. Then 8, 9. Send 10 last and as a request for advice, not a pitch.

---

## 1. Miyajima · Saito · Arikawa

Where: RIETI page for Miyajima (rieti.go.jp/jp/fellow_act/allcategory_top10_miyajima-hideaki.html); researchmap.jp/read0206404. Send to Miyajima, cc Saito.

**Subject:** 政策保有株の縮小 — every named holding, FY2021–, machine-readable and free

Dear Professor Miyajima, Professor Saito,

Your 2023 RIETI paper asked whether the reduction in 政策保有株 under the governance reforms was real, and your September 2025 policy paper with Professor Arikawa returns to the same question. I have built the dataset that question needs and I would like you to have it.

Plover Analytics holds every named policy shareholding disclosed in every annual securities report on EDINET since FY2021 — 31,057 filings, 38,111 named positions in the latest year, both directions, with shares, book value, prior-year shares, the stated purpose text and a reciprocal flag. Each row links to its doc_id and the archived filing's hash, so any figure can be traced to the page it came from.

One screen may interest you directly: filers ranked by holdings reclassified from policy to pure investment rather than sold — https://ploveranalytics.com/holdings.html

It is free, exports to CSV with a metadata header, and has an API. If it would help to have a cut prepared in the shape your panel uses, tell me the layout and I will produce it.

With respect,
[Name]
Plover Analytics · Asia Economics Observations

---

## 2. Tsumuraya Shoichi 円谷昭一

Where: tsumuraya.hub.hit-u.ac.jp; researchmap.jp/7000000089. Japanese first; English version below.

**件名:** 政策保有株式データ（全提出者・FY2021以降）のご提供

円谷昭一 先生

『政策保有株式の実証分析』を拝読しました。有価証券報告書を一件ずつ読んで数えるという方法にこそ、あの本の説得力があったと理解しています。

その作業を全提出者について機械可読にしたものを作りました。EDINET上の有価証券報告書「株式の保有状況」から、FY2021以降の全銘柄（保有側・被保有側の両方向、31,057件の報告書、直近年度で38,111銘柄）を、株式数・貸借対照表計上額・前年度株式数・保有目的の記載文・相互保有フラグとともに収録しています。各行は提出書類のdoc_idと保存ファイルのハッシュに紐づくため、どの数字も元の頁まで遡れます。

https://ploveranalytics.com/holdings.html — 無料で、CSV（出典ヘッダ付き）とAPIで取得できます。

先生の研究に使いやすい形（パネルの列構成など）があれば、その形で切り出してお送りします。ご意見をいただけましたら幸いです。

[氏名]
Plover Analytics · Asia Economics Observations

*(English)* Subject: Policy-shareholding data, every filer, FY2021 onward. — I read 『政策保有株式の実証分析』; its authority came from reading the securities reports one by one. I have done that work for every filer, machine-readably: every named holding since FY2021, both directions, with shares, book value, prior-year shares, purpose text and a reciprocal flag, each row traceable to its doc_id. Free, CSV and API. If a particular panel layout would help, tell me and I will cut it.

---

## 3. Tanabe · Ohnishi

Where: Rikkyo Graduate School of Artificial Intelligence and Science; Ohnishi also at the Canon Institute for Global Studies. Send to Ohnishi, cc Tanabe.

**Subject:** The other half of the cross-shareholding graph — holder-side, named, with value and purpose

Dear Professor Ohnishi, Dr Tanabe,

Your PLoS One paper on the 2001–2023 cross-shareholding network builds its edges from NEEDS major-shareholder records — the issuer's view of who holds it. There is a second edge list that NEEDS does not carry: what each *holder* discloses in its own annual securities report under 株式の保有状況.

I have extracted that for every filer on EDINET since FY2021: 38,111 named positions in the latest year, with shares, book value, prior-year shares, the stated purpose and whether the issuer holds the filer in return (17,279 reciprocal pairs). Because it is holder-declared, it includes positions below any major-shareholder threshold and it carries a value on every edge.

https://ploveranalytics.com/holdings.html — free, CSV and JSON API. The full edge list for a year is one request.

If it is useful for a follow-up on the hierarchy you found, I would be glad to provide it in whatever format your pipeline reads.

Best regards,
[Name]
Plover Analytics

---

## 4. Noriaki Okamoto

Where: univdb.rikkyo.ac.jp (Department of Global Business); researchmap.jp/read0211nori.

**Subject:** Purpose text and reclassification data for the performativity argument

Dear Professor Okamoto,

Your Journal of Management and Governance article argues that the governance code's disclosure requirement has been performative in reducing cross-shareholdings. I have data that bears on it directly.

For every annual securities report on EDINET since FY2021, I hold each named policy holding with its stated purpose exactly as filed, its book value, and its prior-year shares — so one can see, filer by filer, whether a holding was sold, kept with a rewritten purpose, or moved to the 純投資 bucket without being sold. A screen ranks filers by that last move: https://ploveranalytics.com/holdings.html

The purpose field is text, as filed, across 38,111 positions in the latest year — the kind of corpus a disclosure-language study needs. It is free, with CSV and API access.

If a particular cut would help, I would be glad to prepare it.

Best regards,
[Name]
Plover Analytics

---

## 5. Kanae Yada 矢田歌菜絵 (Daiwa Institute of Research)

Where: dir.co.jp author page (法務・会計・税制 research). Japanese.

**件名:** 政策保有株式の縮減状況 — TOPIX500ではなく全提出者で

矢田 歌菜絵 様

「政策保有株式の縮減状況～2024年版～」を拝読しました。TOPIX500構成企業を有価証券報告書から金融・事業会社に分けて集計されたものと理解しています。

同じ「株式の保有状況」をEDINET上の全提出者について収録したデータを公開しています。FY2021以降の31,057件の報告書、直近年度で2,814社・38,111銘柄、保有側と被保有側の両方向、株式数・計上額・前年度株式数・保有目的・相互保有の別。各行は提出書類のdoc_idに紐づきます。

https://ploveranalytics.com/holdings.html — 無料、CSV・API。

次回版の作業に使えるものがあれば、必要な切り口でお出しします。ご意見をいただければ幸いです。

[氏名]
Plover Analytics

---

## 6. Katagiri · Shino · Takahashi

Where: Katagiri — sites.google.com/site/mitsurukatagiri (Waseda School of Commerce); Shino — Waseda; Takahashi — BIS. Send to Katagiri, cc the other two.

**Subject:** BOJ ETF stocks and flows, monthly from 1996, by API — plus ownership data for controls

Dear Professor Katagiri, Professor Shino, Dr Takahashi,

Your two 2025 papers on the BOJ's ETF programme (Journal of Financial Markets; Review of Asset Pricing Studies) each needed the programme's time path assembled from BOJ releases. I publish that series ready to use.

Plover Analytics carries the BOJ's MD09 table — ETF and J-REIT holdings, JGB purchases and redemptions, the monetary base — monthly from July 1996, in ¥100mn, exactly as published, with the BOJ credit line, via a JSON API and CSV: https://ploveranalytics.com/boj.html

To be clear about what it is not: it is the BOJ's own aggregate stocks and flows, not the stock-level indirect-holdings panel you constructed. Where it may help is alongside: every large-shareholding (5%) report since August 2021 with the filed important-proposal flag, and every named policy shareholding from annual reports since FY2021 — firm-level ownership controls in one place.

If a particular series layout would save you time, tell me and I will produce it.

Best regards,
[Name]
Plover Analytics

---

## 7. Charoenwong · Wiwattanakantang · Morck

Where: NUS Business School (Charoenwong, Wiwattanakantang); University of Alberta (Morck). Send to Charoenwong, cc the others.

**Subject:** Firm-level Japanese ownership and governance data, free, for follow-ups to "Bank of Japan Equity Purchases"

Dear Professor Charoenwong, Professor Wiwattanakantang, Professor Morck,

Your Review of Finance paper found that the BOJ's equity purchases raised investment only at firms with worse governance. A follow-up on what happened as the programme ended, and as cross-shareholdings unwound, needs firm-level governance data that has been expensive to assemble for Japan. I have built it and it is free.

Plover Analytics, from EDINET filings: every named policy shareholding in every annual securities report since FY2021 (both directions, with value and purpose — 38,111 positions in the latest year); every 5% large-shareholding report since August 2021 with the filed "important proposal" flag; board composition and pay; buybacks. Plus the BOJ's ETF holdings and flows monthly from 1996. All as filed, every row traceable to its source document, CSV and API.

https://ploveranalytics.com/holdings.html · https://ploveranalytics.com/stakes.html · https://ploveranalytics.com/boj.html

If a specific panel would help — say, policy-holding value by firm-year merged to a governance index — I would be glad to prepare it.

Best regards,
[Name]
Plover Analytics

---

## 8. Kozo Ueda

Where: Waseda School of Political Science and Economics; CARF page for the working paper.

**Subject:** The published side of Japanese prices — 589 CPI items from 1970 on one base

Dear Professor Ueda,

Your Japanese Economic Review paper works from Retail Price Survey microdata, which nothing I hold can replace. What I can offer is the published side done properly: every one of the 589 individually priced CPI items, monthly from January 1970, linked on the 2025 base, with basket weights and a monthly breadth measure (the share of items rising, and rising above a threshold), by API and CSV: https://ploveranalytics.com/cpi.html

Two things may be useful beyond convenience. First, the index levels are exactly as published and every calculated rate shows its formula, so a figure can be cited as the Bureau's. Second, from this year every CPI release is archived as a vintage and queryable as of a date, so revisions and base changes stop being a nuisance in replication — I would value your view on which vintage features matter most.

It is free. If it saves a student an afternoon, it has done its job.

Best regards,
[Name]
Plover Analytics

---

## 9. Toshiaki Shoji

Where: sites.google.com/site/tshojiecon (Seikei; visiting Hitotsubashi through August 2026, University of Melbourne from September 2026).

**Subject:** Item-level CPI with weights per 10,000 — for inflation heterogeneity work

Dear Professor Shoji,

Your Empirical Economics paper on heterogeneity in prices and inflation over the life cycle, and the Review of Income and Wealth paper on anticipated price increases, both rest on item-level Japanese price data reweighted by household type. That reweighting is the tedious part, and I have the inputs in one place.

Plover Analytics publishes all 589 individually priced CPI items monthly from January 1970 on the 2025 base, with each item's basket weight in parts per 10,000, so a group-specific index is a weight vector away. Index levels are exactly as published; every calculated rate carries its formula; CSV and JSON API: https://ploveranalytics.com/cpi.html

It is free. If a particular layout would help for a replication package, I can produce it.

Best regards,
[Name]
Plover Analytics

---

## 10. Fumio Hayashi · Yuta Tachi — send last, as a request for advice

Where: sites.google.com/view/fumio-hayashis-hp (GRIPS); Tachi — Reitaku University.

**Subject:** Building a point-in-time archive for Japanese statistics — what would you have wanted?

Dear Professor Hayashi, Professor Tachi,

Your Empirical Economics paper on nowcasting Japan's GDP notes that collecting past vintages of Japanese indicators is a major undertaking because off-the-shelf vintage coverage of Japan is so limited. I am building exactly that archive and would value ten minutes of your advice before it hardens.

Plover Analytics now archives every release of the statistics it carries — CPI at item level, BOJ balance-sheet tables, quarterly GDP, trade and fiscal series — with the source file's hash, and serves the data as it stood on any date. Honest caveat: the history begins with our ingest this year, so the value is prospective; it accumulates only if the archive runs reliably every month, which is why I am asking now.

What I would like to know: for a real-time exercise like yours, which series' vintages matter most, and what release-timing metadata did you find missing? I would rather build to that than guess.

https://ploveranalytics.com/methodology.html describes the archive.

With respect,
[Name]
Plover Analytics

---

## Not on the list, and why

- **Tsutomu Watanabe (Tokyo)** — has better price data than we do (his own scanner and daily
  price indices). Worth knowing us, not worth an email claiming to help him.
- **Hidefusa Iida** (ECGI Law WP 2025 on Japanese M&A and activism) — legal scholar; our data is
  peripheral to his argument.
- **BOJ Working Paper authors** (e.g. 22-E-18 on pandemic inflation) — Bank staff with internal
  access to everything we publish.

## After sending

Tag nothing; just watch the People column on the admin traffic page and the referrer table. A
reply that asks for a cut is a citation in waiting — deliver it within a day.
