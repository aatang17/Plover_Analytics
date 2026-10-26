# IRRBB1 sources for the 26 institutions the collector missed

> **Status 2026-09-24:** re-collected with parser `irrbb-2`. 89 of 98 regional
> banks now carry figures (81 clean, 8 partial), up from 72. Still missing (9):
> the 7 banks whose sites answer "Access Denied", Kiraboshi (its holding
> company's server refuses connections from this network), and Fukuho (site
> gone). Iwate is filled from its **March 2025** book, since the 2026 one is
> scanned images. The group-book pages below are now in
> `irrbb_sources.json`, and the collector refuses any book holding more than
> two entities' tables without them. A PDF placed in
> `data/raw/irrbb/manual/<fi_code>.pdf` is read in place of the download.

Checked 2026-09-23 against the local run (26 `no_source`; production reported 27).
Every "table present" row below was confirmed by downloading the PDF and finding
the IRRBB1 table in its text.

## Parse today — added to `observatory/equity/irrbb_sources.json`

| Code | Bank | PDF | Table page |
| --- | --- | --- | --- |
| 0173 | 百十四銀行 | https://www.114bank.co.jp/ir/disclosure/pdf/2026disclosure/14.pdf | 42 |
| 0133 | 武蔵野銀行 | https://www.musashinobank.co.jp/ir/financial/disclosure/pdf/2026/2026_disclo_data_all.pdf | 93, 109 |
| 0152 | 大垣共立銀行 | https://www.okb.co.jp/assets/pdf/investor/disclosure/2025/2026-%EF%BD%8D.pdf | 24, 39 |
| 0167 | 山陰合同銀行 | https://ssl4.eir-parts.net/doc/8381/ir_material_for_fiscal_ym/209047/00.pdf | 134 (as-of not detected) |
| 0124 | 東北銀行 | https://ssl4.eir-parts.net/doc/8349/ir_material_for_fiscal_ym/209017/00.pdf | 56 |
| 0534 | 富山第一銀行 | https://ssl4.eir-parts.net/doc/7184/ir_material_for_fiscal_ym/208846/00.pdf | 88, 101 |
| 0591 | 宮崎太陽銀行 | https://ssl4.eir-parts.net/doc/8560/ir_material_for_fiscal_ym/209278/00.pdf | 95 |

## Right document, but the parser cannot read the table yet — also added as hints

The labels are letter-spaced (`上 方 パ ラ レ ル シ フ ト`) or wrapped over two
lines, and the year columns come in either order (prior year first in 十六 and 百五).
The parser must read the column years from the header, not assume current-first.

| Code | Bank | PDF |
| --- | --- | --- |
| 0153 | 十六銀行 | https://www.16fg.co.jp/ir/disclosure/files/tougou2026_07.pdf |
| 0155 | 百五銀行 | https://www.hyakugo.co.jp/ir/disclosure/pdf/2026-07.pdf |
| 0178 | 筑邦銀行 | https://www.chikugin.co.jp/uploads/cont/about/ir/2026/disc2026_all_02.pdf |
| 0513 | 福島銀行 | https://www.fukushimabank.co.jp/ir/ir/disclo/pdf/202603disclo_houtei.pdf |

## Group books — NOT added as hints until the collector can pick a bank's own pages

The collector stores every table in a hinted document against the bank, so a
group book would give each bank the group's numbers. PDF page numbers below.

| Code | Bank | PDF | Its own table |
| --- | --- | --- | --- |
| 0138 | 横浜銀行 | https://ssl4.eir-parts.net/doc/7186/ir_material_for_fiscal_ym7/209264/00.pdf | p262 (consolidated), p277 (non-consolidated) |
| 0525 | 東日本銀行 | same | p358, p375 |
| 0530 | 神奈川銀行 | same | p434 |
| — | 横浜FG (group) | same | p135 |
| 0144 | 北陸銀行 | https://pdf.irpocket.com/C8377/xoA3/tx0r/Mj8g/w9ye.pdf | p93, p106 |
| 0116 | 北海道銀行 | same | p148, p161 |
| — | ほくほくFG (group) | same | p47 |

## Still need a person

| Code | Bank | Why | What to do |
| --- | --- | --- | --- |
| 0017, 0159, 0562 | 埼玉りそな, 関西みらい, みなと (りそなHD) | "Access Denied" even to a headless browser | Download by hand in a normal browser |
| 0182, 0185 | 肥後, 鹿児島 (九州FG) | same | same |
| 0175 | 四国銀行 | same | same |
| 0166 | 鳥取銀行 | same | same |
| 0137 | きらぼし銀行 | Site times out from this laptop | Try from the production host, or by hand |
| 0123 | 岩手銀行 | Link found (https://www.iwatebank.co.jp/assets/pdf/202603_tougoushiryo.pdf) but the PDF is scanned images: 65 pages, 84 characters of text | Read the table by eye, or OCR |
| 0537 | 福邦銀行 | fukuho.co.jp no longer resolves | Find the successor bank's disclosure (not checked) |

## Defects found along the way

1. **Group books already in production carry the group's table for every bank.**
   Six of the eight shared hint documents print a separate IRRBB1 table per bank,
   but each bank was stored with the first table (the group's):
   Fidea (3 tables), Mebuki (5), Tomony (5), Nishi-Nippon FH (3), Yamaguchi FG (4),
   Fukuoka FG (7). 82/Nagano and Jimoto genuinely print one table. About 15 banks
   are affected, and the note's "shared disclosures" limitation describes this
   wrongly.
2. **Render step times out.** On this machine Chrome does not exit after
   `--dump-dom`, so `render()` hits its 60s timeout and discards the page. That
   is why Yokohama, 山陰合同, 富山第一, 宮崎太陽 and ほくほく found no PDFs: their
   document lists are built in JavaScript, from `ssl4.eir-parts.net` or
   `pdf.irpocket.com`.
