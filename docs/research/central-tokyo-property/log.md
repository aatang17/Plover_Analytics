# Source discovery log

Date: 14 October 2026. These calls establish the draft plan's source inventory. Detailed research has not started; no report conclusion is settled.

| # | Tool / arguments | Returned | Citable URL / observation or release |
|---|---|---|---|
| D01 | `web.run` searches: Tokyo Kantei 2026 central condo prices; REINS September 2026; Tokyo 2026 land survey | Located Kantei archives, REINS 2026 library and Tokyo land release. Search snippets are not full-document verification. | URLs below; retrieved 2026-10-14. |
| D02 | `web.run` open: Kantei `/report/70m2/`; REINS `/library/2026.html`; Tokyo September appraisal release; Kantei 2026 news | First Kantei path failed (correct category found later); REINS 502; Tokyo 403; Kantei news archive readable. | https://www.kantei.ne.jp/news/date/2026/ ; https://www.reins.or.jp/library/2026.html ; https://www.metro.tokyo.lg.jp/information/press/2026/09/2026091521 |
| D03 | `list_datasets(section=demography)` | Municipality population/household dataset exists, annual 2025-2026; prefectural datasets separately listed. | https://ploveranalytics.com/population.html |
| D04 | `web.run` click Kantei news links: monthly prices, rents, H1 resale cohort report, 2025 sale discount, Q2 new/resale supply | All five source pages opened and readable. | https://www.kantei.ne.jp/report/category/70m2/ ; https://www.kantei.ne.jp/report/category/rent/ ; https://www.kantei.ne.jp/news/release/9375/ ; https://www.kantei.ne.jp/news/release/9230/ ; https://www.kantei.ne.jp/news/release/9227/ |
| D05 | `web.run` searches: REINS central wards 2026; REEI August release; Savills Q2 residential; MLIT June index | Located primary REEI/Savills pages and MLIT index-delay notice. | https://www.fudousankeizai.co.jp/ ; https://www.savills.co.jp/insight-and-opinion/research.aspx?f=date&p=Residential&page=0&q=&rc=Japan&t= ; https://www.mlit.go.jp/totikensangyo/totikensangyo_tk5_000085.html?_fsi=Ozap5lwu |
| D06 | `describe_dataset(population-jp-municipal)` and `search(dataset=population-jp-municipal,query=港区,limit=5)` | Coverage January 2025-January 2026; latest stored release published 2026-09-10. Minato population, households and domestic in-migration codes confirmed. | https://ploveranalytics.com/population.html ; series listed in plan. |
| D07 | `describe_dataset(boj-loan-rates)` | Monthly loan-rate history through August 2026, stored release 2026-10-05. These are not specifically mortgage rates. | https://ploveranalytics.com/banks.html?dataset=boj-loan-rates |
| D08 | `describe_dataset(cpi-tokyo)` | Tokyo CPI through September 2026; stored release 2026-10-02; as-of supported. | https://ploveranalytics.com/tokyo.html |
| D09 | `web.run` open source-page bodies, MLIT index page, REEI inventory archive and Savills residential archive | Kantei latest monthly entry is August, published 24 September. H1 age/location cohort definition confirmed. Discount report is 2025 history. MLIT lists December 2025 latest residential update and a notice delaying 2026 releases. | Source URLs in D04-D05; inventory https://www.fudousankeizai.co.jp/mansion?archiveFlg=2 |
| D10 | `web.run` click August Kantei price release and H1 metropolitan PDF; open REEI homepage and MLIT library | Price-release headline: Tokyo 23 wards -0.4% MoM, JPY126.77m per 70 sq m, three consecutive monthly falls. Regional movements differ. H1 PDF exists (2 pages). REEI August release corrected October 9; next release scheduled October 20. MLIT public transaction and appraisal downloads exist. | https://www.kantei.ne.jp/report/70m2/9447/ ; https://www.kantei.ne.jp/wp-content/uploads/market-price20261H_shuto.pdf ; https://www.fudousankeizai.co.jp/ ; https://www.reinfolib.mlit.go.jp/ |
| D11 | `search(dataset=cpi-tokyo,query=All items,limit=2)` and `search(dataset=boj-loan-rates,query=new long,limit=3)` | Codes `0001` and `DLLR2CIDBNL3` verified. No observations fetched yet. | Plover source views in D07-D08. |
| D12 | `web.run` open August Kantei price-release body at line 160 | Verified the price-release headline and links to detailed price and selling-pressure PDFs. Central-ward values still need extraction; the plan must not infer that all wards decline. | https://www.kantei.ne.jp/report/70m2/9447/ ; published 2026-09-24, observations August 2026. |

## Inventory access gaps

- REINS library: HTTP 502 in this tool; no data extracted.
- Tokyo appraisal press release: HTTP 403; search result only, no numeric claim included in plan.
- Mortgage data, construction-cost data and foreign buyer purchase data: not yet inventoried; must be verified after approval or excluded.
- No data values pulled from Plover, and no property model estimated in the planning phase.
