# -*- coding: utf-8 -*-
"""Every table in every Plover database, visible to the step-by-step route.

The route (app/us_steps.py) used to see nine summary tables built by hand
(app/us_tables.py) and was told in its rules that anything else was not
held. 2026-11-05: "what does Saba Capital own" was refused as "Plover's
data doesn't show which fund holds which stock" with 3.5 million fund
positions on file, because no summary table held a manager's own lines.
The same evening the user asked for every market to follow one method.

Now the three database files are attached to the sandbox read-only before
it is locked, so one query can reach every table in them:

  sec    the US filings (data/sec.duckdb): 13F positions, insiders, facts,
         prices, market values, index lists
  jp     the Japanese filings (data/equity.duckdb): annual reports, 5% stake
         filings, major shareholders, cross-shareholdings, pay, boards,
         risks, buybacks, short positions, segments, AGM votes, TDnet results
  macro  the official statistics (data/observatory.duckdb): every series of
         every dataset for Japan, the United States and Hong Kong, live
         vintage

On top of the raw tables sit views in the words readers use (fund_positions,
jp_companies, econ_observations …); every other table is listed for the
model from the schema itself, under its own name with its catalog prefix,
with its columns and row count. Nothing here is written by hand except the
one-line meaning of each view and its columns.

The lock-down still holds: the files are attached before
``enable_external_access`` is switched off, after which no query can open
another file, write one, or change a setting; a query is one SELECT
(app/analysis.py), so it cannot DETACH either.
"""
import os
import re

CATALOG = "sec"                      # kept for callers that know only the US file
CATALOGS = ("sec", "jp", "macro")

# tables that record how a loader ran, not what it loaded
_BOOKKEEPING = re.compile(r"(_checks|_runs|_pulls|_batches|_quarters|_days|_frames|_rates|_conflicts|_filers|_files|_labels|_sources|_artifacts|_vintages)$")

_LATEST_FILING = """(SELECT doc_id, sec_code, filer_name, period_end, filed_date,
                          row_number() OVER (PARTITION BY edinet_code ORDER BY period_end DESC, filed_date DESC) AS rn
                   FROM {jp}.eq_filings WHERE status <> 'failed')"""
_LATEST_SEG = """(SELECT doc_id, sec_code, filer_name, period_end, filed_date,
                          row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) AS rn
                   FROM {jp}.eq_seg_filings WHERE status <> 'failed')"""
_CO = [("sec_code", "VARCHAR", "the company; joins jp_companies.sec_code"), ("company", "VARCHAR", "its name as filed"),
       ("fiscal_year_end", "DATE", "the report's year end"), ("latest", "BOOLEAN", "true for the company's newest report")]

# name: (meaning, SQL over the attached files, [(column, type, meaning)])
VIEWS = {
    # ------------------------------------------------------------------ US
    "fund_positions": (
        "13F positions line by line, one row per manager per security per quarter, as filed (long positions and options). "
        "A manager's portfolio: WHERE latest AND manager_cik = …; its change: quarters_back 1 against 2. "
        "value_usd and shares are as filed; put_call is 'PUT' or 'CALL' for an option line and blank for shares held.",
        """
        WITH f AS (
            SELECT accession, manager_cik, manager_name, report_period, filed,
                   row_number() OVER (PARTITION BY manager_cik ORDER BY report_period DESC, filed DESC) AS rn
            FROM {sec}.sec_13f_filings WHERE NOT amendment)
        SELECT f.manager_cik, f.manager_name AS manager, f.report_period AS quarter_end, f.filed,
               f.rn = 1 AS latest, f.rn AS quarters_back,
               h.cusip, h.issuer_name, h.title_of_class, h.put_call, h.value AS value_usd, h.shares, h.shares_type,
               t.ticker, t.cik AS issuer_cik,
               h.shares > 0 AND h.value > 0 AND p.px > 0 AND (h.value / h.shares) / p.px BETWEEN 1.0/3 AND 3 AS shares_ok
        FROM {sec}.sec_13f_holdings h JOIN f USING (accession)
        LEFT JOIN cusip_tickers t USING (cusip)
        LEFT JOIN cusip_prices p ON p.cusip = h.cusip AND p.quarter_end = f.report_period
        """,
        [("manager_cik", "BIGINT", "the filing manager's SEC id; joins managers.manager_cik"),
         ("manager", "VARCHAR", "the manager's name as filed"),
         ("quarter_end", "DATE", "the quarter the positions are held at"),
         ("filed", "DATE", "when the 13F was filed, up to 45 days after quarter_end"),
         ("latest", "BOOLEAN", "true for the manager's newest filed quarter"),
         ("quarters_back", "INTEGER", "1 = the manager's newest quarter, 2 = the one before"),
         ("cusip", "VARCHAR", "the security's 9-character CUSIP"),
         ("issuer_name", "VARCHAR", "the security's issuer as the manager wrote it (abbreviated)"),
         ("title_of_class", "VARCHAR", "share class or note, as filed"),
         ("put_call", "VARCHAR", "'PUT' or 'CALL' for an option line; blank for shares held outright"),
         ("value_usd", "DOUBLE", "market value of the position at quarter_end, US$"),
         ("shares", "DOUBLE", "shares (or principal amount) held"),
         ("shares_type", "VARCHAR", "'SH' shares or 'PRN' principal amount"),
         ("ticker", "VARCHAR", "the issuer's ticker when the CUSIP maps to a listed company; joins stocks.ticker; blank for funds and notes"),
         ("issuer_cik", "BIGINT", "the issuer's SEC id when known"),
         ("shares_ok", "BOOLEAN", "false when value_usd ÷ shares is more than three times off the security's median that quarter, a units slip in "
                                  "the filing (shares in thousands, or value and shares swapped): sum or rank shares only WHERE shares_ok; "
                                  "value_usd is kept either way")]),
    "managers": (
        "Every 13F manager, one row each, at its newest filed quarter: positions, value and the long-stock part. "
        "Find a manager by name here (ILIKE '%saba capital%'), then read fund_positions.",
        """
        SELECT manager_cik, any_value(manager) AS name, any_value(quarter_end) AS quarter_end, any_value(filed) AS filed,
               count(*) AS positions, sum(value_usd) AS value_usd,
               count(*) FILTER (WHERE put_call IS NULL) AS stock_positions,
               sum(value_usd) FILTER (WHERE put_call IS NULL) AS stock_value_usd
        FROM fund_positions WHERE latest GROUP BY manager_cik
        """,
        [("manager_cik", "BIGINT", "the manager's SEC id"),
         ("name", "VARCHAR", "the manager's name as filed"),
         ("quarter_end", "DATE", "its newest filed quarter"),
         ("filed", "DATE", "when that 13F was filed"),
         ("positions", "INTEGER", "lines in the filing, options included"),
         ("value_usd", "DOUBLE", "total value filed, US$"),
         ("stock_positions", "INTEGER", "lines that are shares held outright"),
         ("stock_value_usd", "DOUBLE", "value of shares held outright, US$")]),
    "market_value_daily": (
        "Market value of every US-listed company for every trading day since 2018 (close × shares outstanding from the filing cover). "
        "Use it for a market value on a past date or over time; stocks.market_cap_usd is today's.",
        """
        SELECT date, ticker, cik, close, shares, market_cap AS market_cap_usd
        FROM {sec}.us_mcap_daily
        """,
        [("date", "DATE", "trading day"),
         ("ticker", "VARCHAR", "joins stocks.ticker"),
         ("cik", "BIGINT", "the company's SEC id"),
         ("close", "DOUBLE", "closing price that day, US$"),
         ("shares", "DOUBLE", "shares outstanding used, from the latest filing cover before that day"),
         ("market_cap_usd", "DOUBLE", "close × shares, US$")]),
    "index_members": (
        "Members of the S&P 500 and the Dow with their weights, from the tracking funds' latest holdings lists.",
        """
        SELECT CASE WHEN s.index_name LIKE 'S&P 500%' THEN 'S&P 500' WHEN s.index_name LIKE 'Dow%' THEN 'Dow' ELSE s.index_name END AS index_name,
               s.as_of, m.ticker, m.cik, m.name, m.weight_pct
        FROM {sec}.us_index_members m JOIN {sec}.us_index_snapshots s USING (snapshot_id)
        WHERE s.snapshot_id IN (SELECT max(snapshot_id) FROM {sec}.us_index_snapshots GROUP BY index_name)
        """,
        [("index_name", "VARCHAR", "'S&P 500' or 'Dow'"),
         ("as_of", "DATE", "the holdings list's date"),
         ("ticker", "VARCHAR", "joins stocks.ticker"),
         ("cik", "BIGINT", "the company's SEC id"),
         ("name", "VARCHAR", "the company's name on the list"),
         ("weight_pct", "DOUBLE", "the member's weight in the index, percent")]),
    # --------------------------------------------------------------- Japan
    "jp_companies": (
        "Every Japanese company that files annual reports with EDINET, one row each, with its latest annual report's size lines. "
        "sec_code is the 4-character stock code as text ('7203' Toyota); find a company with name_en ILIKE. Tokyo share prices are not held.",
        """
        WITH f AS (SELECT edinet_code, period_end, filed_date, issued_shares, treasury_shares, equity_yen, total_assets_yen,
                          row_number() OVER (PARTITION BY edinet_code ORDER BY period_end DESC, filed_date DESC) AS rn
                   FROM {jp}.eq_filings WHERE status <> 'failed')
        SELECT e.sec_code, e.edinet_code, e.name_en, e.name_ja, e.industry, e.listed,
               f.period_end AS fiscal_year_end, f.issued_shares, f.treasury_shares, f.equity_yen, f.total_assets_yen,
               y.employees_consolidated AS employees, y.board_size, y.female_officers, y.avg_director_age,
               y.avg_salary_yen, y.avg_employee_age, y.avg_tenure_years
        FROM {jp}.eq_entities e
        LEFT JOIN f ON f.edinet_code = e.edinet_code AND f.rn = 1
        LEFT JOIN {jp}.eq_company_year y ON y.edinet_code = e.edinet_code AND y.period_end = f.period_end
        """,
        [("sec_code", "VARCHAR", "4-character stock code as text; joins every jp_ table"),
         ("edinet_code", "VARCHAR", "the filer's EDINET id"),
         ("name_en", "VARCHAR", "English name"), ("name_ja", "VARCHAR", "Japanese name"),
         ("industry", "VARCHAR", "EDINET industry"), ("listed", "BOOLEAN", "listed on a Japanese exchange"),
         ("fiscal_year_end", "DATE", "the latest annual report's year end"),
         ("issued_shares", "DOUBLE", "shares issued"), ("treasury_shares", "DOUBLE", "treasury shares"),
         ("equity_yen", "DOUBLE", "shareholders' equity, yen"), ("total_assets_yen", "DOUBLE", "total assets, yen"),
         ("employees", "DOUBLE", "consolidated employees"), ("board_size", "INTEGER", "directors and officers on the board"),
         ("female_officers", "INTEGER", "women among them"), ("avg_director_age", "DOUBLE", "average director age"),
         ("avg_salary_yen", "DOUBLE", "average employee salary, yen"), ("avg_employee_age", "DOUBLE", "average employee age"),
         ("avg_tenure_years", "DOUBLE", "average years of service")]),
    "jp_figures": (
        "Every figure in Japanese annual reports (XBRL), this year (year_offset 0) and last (−1): net sales, operating profit, "
        "profit attributable to owners, assets, cash flows and the rest. Filter by sec_code and label_en ILIKE ('%net sales%'); "
        "basis 'consolidated' first, 'parent' when the company reports no group; period_kind 'duration' for flows, 'instant' for balances.",
        """
        WITH ff AS (SELECT doc_id, sec_code, filer_name, period_end, filed_date, accounting_standard,
                           row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) AS rn
                    FROM {jp}.eq_fin_filings WHERE status <> 'failed'),
             el AS (SELECT element, any_value(label_en) AS label_en FROM {jp}.eq_fin_elements GROUP BY element)
        SELECT ff.sec_code, ff.filer_name AS company, ff.period_end AS fiscal_year_end, ff.rn = 1 AS latest, ff.accounting_standard,
               x.element, el.label_en, x.year_offset, x.basis, x.period_kind, x.unit, x.value
        FROM {jp}.eq_fin_facts x JOIN ff USING (doc_id) LEFT JOIN el USING (element)
        WHERE x.year_offset IN (0, -1)
        """,
        _CO + [("accounting_standard", "VARCHAR", "Japan GAAP, IFRS or US GAAP"),
               ("element", "VARCHAR", "XBRL element (jppfs_cor:NetSales, jppfs_cor:OperatingIncome, jppfs_cor:ProfitLossAttributableToOwnersOfParent)"),
               ("label_en", "VARCHAR", "the element's English label"), ("year_offset", "INTEGER", "0 this report's year, -1 the year before"),
               ("basis", "VARCHAR", "'consolidated' or 'parent'"), ("period_kind", "VARCHAR", "'duration' or 'instant'"),
               ("unit", "VARCHAR", "JPY, pure (a ratio), JPYPerShares, shares"), ("value", "DOUBLE", "the figure, in unit (yen are whole yen)")]),
    "jp_key_figures": (
        "The headline figures of every Japanese annual report on one row per company per year, harmonised across Japan GAAP, IFRS and US GAAP "
        "(this year and last): revenue, operating profit, profit attributable to owners, total assets, equity. Use this before jp_figures.",
        """
        WITH ff AS (SELECT doc_id, sec_code, filer_name, period_end, filed_date, accounting_standard,
                           row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) AS rn
                    FROM {jp}.eq_fin_filings WHERE status <> 'failed'),
             x AS (SELECT ff.sec_code, ff.filer_name, ff.period_end, ff.rn, ff.accounting_standard, f.year_offset, f.element, f.value, f.basis, f.period_kind
                   FROM {jp}.eq_fin_facts f JOIN ff USING (doc_id)
                   WHERE f.year_offset IN (0, -1) AND f.unit = 'JPY' AND f.value IS NOT NULL
                     AND f.element NOT ILIKE '%PerShare%' AND f.element NOT ILIKE '%Ratio%' AND f.element NOT ILIKE '%Change%' AND f.element NOT ILIKE '%Growth%'),
             b AS (SELECT sec_code, max(CASE WHEN basis = 'consolidated' THEN 1 ELSE 0 END) AS has_cons FROM x GROUP BY sec_code)
        SELECT x.sec_code, any_value(x.filer_name) AS company, x.period_end AS fiscal_year_end, x.rn = 1 AS latest, x.year_offset,
               any_value(x.accounting_standard) AS accounting_standard,
               CASE WHEN b.has_cons = 1 THEN 'consolidated' ELSE 'parent' END AS basis,
               max(x.value) FILTER (WHERE x.period_kind = 'duration' AND (x.element ILIKE '%NetSales%' OR x.element ILIKE '%Revenue%')
                                      AND x.element NOT ILIKE '%Cost%' AND x.element NOT ILIKE '%Unearned%' AND x.element NOT ILIKE '%Deferred%'
                                      AND x.element NOT ILIKE '%NonOperating%' AND x.element NOT ILIKE '%Other%') AS revenue_yen,
               max(x.value) FILTER (WHERE x.period_kind = 'duration' AND (x.element ILIKE '%OperatingIncome%' OR x.element ILIKE '%OperatingProfit%')
                                      AND x.element NOT ILIKE '%NonOperating%') AS operating_profit_yen,
               max(x.value) FILTER (WHERE x.period_kind = 'duration' AND x.element ILIKE '%ProfitLossAttributableToOwnersOfParent%') AS net_profit_yen,
               max(x.value) FILTER (WHERE x.period_kind = 'instant' AND (x.element ILIKE '%:Assets' OR x.element ILIKE '%:AssetsIFRS' OR x.element ILIKE '%TotalAssets%')) AS total_assets_yen,
               max(x.value) FILTER (WHERE x.period_kind = 'instant' AND (x.element ILIKE '%:NetAssets' OR x.element ILIKE '%EquityAttributableToOwnersOfParent%'
                                      OR x.element ILIKE '%TotalEquity%' OR x.element ILIKE '%NetAssetsSummaryOfBusinessResults%')) AS equity_yen
        FROM x JOIN b USING (sec_code)
        WHERE x.basis = CASE WHEN b.has_cons = 1 THEN 'consolidated' ELSE 'parent' END
        GROUP BY x.sec_code, x.period_end, x.rn, x.year_offset, b.has_cons
        """,
        _CO + [("year_offset", "INTEGER", "0 this report's year, -1 the year before (both from the same report)"),
               ("accounting_standard", "VARCHAR", "Japan GAAP, IFRS or US GAAP"), ("basis", "VARCHAR", "consolidated, or parent when the company reports no group"),
               ("revenue_yen", "DOUBLE", "net sales or revenue, yen"), ("operating_profit_yen", "DOUBLE", "operating profit, yen"),
               ("net_profit_yen", "DOUBLE", "profit attributable to owners of parent, yen"), ("total_assets_yen", "DOUBLE", "total assets, yen"),
               ("equity_yen", "DOUBLE", "net assets or equity attributable to owners, yen")]),
    "jp_major_shareholders": (
        "The ten largest shareholders each Japanese company lists in its annual report (the register), with their stake.",
        """
        WITH o AS (SELECT doc_id, sec_code, filer_name, period_end, filed_date,
                          row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) AS rn
                   FROM {jp}.eq_own_filings WHERE status <> 'failed')
        SELECT o.sec_code, o.filer_name AS company, o.period_end AS fiscal_year_end, o.rn = 1 AS latest,
               m.rank, m.name_base AS holder, m.name_raw AS holder_raw, m.holder_kind, m.shares, m.ratio_pct, m.holder_sec_code
        FROM {jp}.eq_major_shareholders m JOIN o USING (doc_id)
        """,
        _CO + [("rank", "INTEGER", "1 = largest"), ("holder", "VARCHAR", "holder's name, cleaned"),
               ("holder_raw", "VARCHAR", "as printed"), ("holder_kind", "VARCHAR", "trust bank account, company, individual…"),
               ("shares", "DOUBLE", "shares held"), ("ratio_pct", "DOUBLE", "% of shares outstanding, as filed"),
               ("holder_sec_code", "VARCHAR", "the holder's own stock code when it is a listed company")]),
    "jp_shareholder_mix": (
        "Who holds each Japanese company by type, from its annual report: foreigners, financial institutions, other companies, individuals.",
        """
        SELECT sec_code, filer_name AS company, period_end AS fiscal_year_end,
               row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) = 1 AS latest,
               shareholders_total, foreign_pct, financial_institutions_pct, other_corporations_pct, individuals_pct, securities_firms_pct, government_pct
        FROM {jp}.eq_own_filings WHERE status <> 'failed'
        """,
        _CO + [("shareholders_total", "DOUBLE", "number of shareholders"),
               ("foreign_pct", "DOUBLE", "% held by foreign investors"), ("financial_institutions_pct", "DOUBLE", "% by banks and insurers"),
               ("other_corporations_pct", "DOUBLE", "% by other companies"), ("individuals_pct", "DOUBLE", "% by individuals"),
               ("securities_firms_pct", "DOUBLE", "% by brokers"), ("government_pct", "DOUBLE", "% by government")]),
    "jp_stakes": (
        "Large shareholding (5%) filings on Japanese companies: who holds 5% or more, the stake and its change, every filing. "
        "A holder's current stake: WHERE latest; who is buying or selling: ratio_change_pp by filed_date.",
        """
        SELECT issuer_sec_code AS sec_code, issuer_name_raw AS company, filer_name AS holder, filer_edinet_code AS holder_edinet_code,
               report_type, filed_date, ratio_pct, prior_ratio_pct, ratio_change_pp, shares_held, shares_outstanding,
               purpose_ja, important_proposal,
               row_number() OVER (PARTITION BY issuer_sec_code, filer_edinet_code ORDER BY filed_date DESC) = 1 AS latest
        FROM {jp}.eq_lvh_filings WHERE status <> 'failed' AND issuer_sec_code IS NOT NULL
        """,
        [("sec_code", "VARCHAR", "the company held"), ("company", "VARCHAR", "its name as filed"), ("holder", "VARCHAR", "the filer holding the stake"),
         ("holder_edinet_code", "VARCHAR", "the holder's EDINET id"), ("report_type", "VARCHAR", "'initial', 'change' or 'amendment'"),
         ("filed_date", "DATE", "when filed"), ("ratio_pct", "DOUBLE", "stake after the filing, %"), ("prior_ratio_pct", "DOUBLE", "stake before, %"),
         ("ratio_change_pp", "DOUBLE", "change, percentage points"), ("shares_held", "DOUBLE", "shares held"),
         ("shares_outstanding", "DOUBLE", "the company's shares outstanding"), ("purpose_ja", "VARCHAR", "purpose of holding, Japanese"),
         ("important_proposal", "BOOLEAN", "the holder may make important proposals (an activist filing)"),
         ("latest", "BOOLEAN", "the holder's newest filing on this company")]),
    "jp_cross_holdings": (
        "Cross-shareholdings: the listed shares each Japanese company holds in other companies, from its annual report, with book value and the year before.",
        "WITH f AS " + _LATEST_FILING + """
        SELECT f.sec_code AS holder_sec_code, f.filer_name AS holder, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               h.held_name_raw AS held_company, h.held_sec_code, h.shares, h.book_value_yen, h.prior_shares, h.prior_book_value_yen,
               h.reciprocal, h.purpose_ja
        FROM {jp}.eq_holdings h JOIN f USING (doc_id)
        """,
        [("holder_sec_code", "VARCHAR", "the company holding the shares"), ("holder", "VARCHAR", "its name"),
         ("fiscal_year_end", "DATE", "report year end"), ("latest", "BOOLEAN", "newest report"),
         ("held_company", "VARCHAR", "the company held, as printed"), ("held_sec_code", "VARCHAR", "its stock code when matched"),
         ("shares", "DOUBLE", "shares held"), ("book_value_yen", "DOUBLE", "balance-sheet value, yen"),
         ("prior_shares", "DOUBLE", "shares the year before"), ("prior_book_value_yen", "DOUBLE", "value the year before, yen"),
         ("reciprocal", "BOOLEAN", "the held company holds this one too"), ("purpose_ja", "VARCHAR", "stated purpose, Japanese")]),
    "jp_pay": (
        "Named executive pay at Japanese companies (every officer paid ¥100m or more, named in the annual report), yen per year.",
        "WITH f AS " + _LATEST_FILING + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               p.name_en AS person, p.consolidated_pay_yen AS pay_yen, p.pay_basis, p.on_board_at_filing
        FROM {jp}.eq_pay_named p JOIN f USING (doc_id)
        """,
        _CO + [("person", "VARCHAR", "the officer"), ("pay_yen", "DOUBLE", "total pay, yen"),
               ("pay_basis", "VARCHAR", "consolidated or parent"), ("on_board_at_filing", "BOOLEAN", "still on the board when filed")]),
    "jp_pay_categories": (
        "Board pay by category (directors, outside directors, auditors) at Japanese companies: headcount and totals, yen.",
        "WITH f AS " + _LATEST_FILING + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               c.category_en AS category, c.headcount, c.total_yen, c.per_head_yen, c.fixed_yen, c.performance_yen, c.bonus_yen
        FROM {jp}.eq_pay_category c JOIN f USING (doc_id)
        """,
        _CO + [("category", "VARCHAR", "directors, outside directors, audit…"),
               ("headcount", "INTEGER", "people paid"), ("total_yen", "DOUBLE", "total, yen"), ("per_head_yen", "DOUBLE", "per person, yen"),
               ("fixed_yen", "DOUBLE", "fixed part"), ("performance_yen", "DOUBLE", "performance part"), ("bonus_yen", "DOUBLE", "bonus part")]),
    "jp_board": (
        "Every board seat at Japanese companies from the annual report: name, title, role, age, shares held.",
        "WITH f AS " + _LATEST_FILING + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               b.seat_no, b.name_en AS person, b.name_ja AS person_ja, b.title_ja, b.role, b.is_representative, b.age_at_period_end AS age, b.shares_held
        FROM {jp}.eq_board b JOIN f USING (doc_id)
        """,
        _CO + [("seat_no", "INTEGER", "order in the report"), ("person", "VARCHAR", "name, English"),
               ("person_ja", "VARCHAR", "name, Japanese"), ("title_ja", "VARCHAR", "title as printed"), ("role", "VARCHAR", "director, outside director, auditor…"),
               ("is_representative", "BOOLEAN", "representative director"), ("age", "INTEGER", "age at year end"), ("shares_held", "DOUBLE", "shares of the company held")]),
    "jp_risks": (
        "Risk factors (事業等のリスク) of Japanese annual reports, one row per heading, with the text.",
        """
        WITH f AS (SELECT doc_id, sec_code, filer_name, period_end, filed_date,
                          row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code) ORDER BY period_end DESC, filed_date DESC) AS rn
                   FROM {jp}.eq_risk_filings WHERE status <> 'failed')
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               r.item_no, r.level, r.heading, r.n_chars, r.body
        FROM {jp}.eq_risk_items r JOIN f USING (doc_id)
        """,
        _CO + [("item_no", "INTEGER", "order"), ("level", "INTEGER", "heading depth"),
               ("heading", "VARCHAR", "the risk heading, Japanese"), ("n_chars", "INTEGER", "length of the text"), ("body", "VARCHAR", "the text, Japanese")]),
    "jp_buybacks": (
        "Share buyback programmes of Japanese companies from their monthly buyback reports: authorised, bought this month and to date.",
        """
        SELECT bf.sec_code, bf.filer_name AS company, bf.submitted, bf.as_of AS report_month,
               p.resolution_type, p.resolution_date, p.window_start, p.window_end, p.authorised_shares, p.authorised_yen,
               p.month_shares, p.month_yen, p.cumulative_shares, p.cumulative_yen, p.progress_yen_pct
        FROM {jp}.eq_buyback_programs p JOIN {jp}.eq_buyback_filings bf USING (doc_id)
        """,
        [("sec_code", "VARCHAR", "the company"), ("company", "VARCHAR", "its name"), ("submitted", "DATE", "when the report was filed"),
         ("report_month", "DATE", "the month reported"), ("resolution_type", "VARCHAR", "board or AGM resolution"), ("resolution_date", "DATE", "when authorised"),
         ("window_start", "DATE", "buying window start"), ("window_end", "DATE", "buying window end"), ("authorised_shares", "DOUBLE", "shares authorised"),
         ("authorised_yen", "DOUBLE", "yen authorised"), ("month_shares", "DOUBLE", "shares bought in the month"), ("month_yen", "DOUBLE", "yen spent in the month"),
         ("cumulative_shares", "DOUBLE", "shares bought under the programme to date"), ("cumulative_yen", "DOUBLE", "yen spent to date"),
         ("progress_yen_pct", "DOUBLE", "% of the yen authorisation used")]),
    "jp_shorts": (
        "Short positions in Japanese stocks reported to the FSA (0.5% and above), by holder and date.",
        """
        SELECT sec_code, name_en AS company, holder_name AS holder, fund_name, calc_date, publish_date, ratio_pct, prev_ratio_pct, shares, below_threshold,
               row_number() OVER (PARTITION BY sec_code, holder_key ORDER BY calc_date DESC, publish_date DESC) = 1 AS latest
        FROM {jp}.eq_short_reports
        """,
        [("sec_code", "VARCHAR", "the stock"), ("company", "VARCHAR", "its name"), ("holder", "VARCHAR", "who is short"), ("fund_name", "VARCHAR", "the fund, when named"),
         ("calc_date", "DATE", "the position date"), ("publish_date", "DATE", "when published"), ("ratio_pct", "DOUBLE", "short position, % of shares"),
         ("prev_ratio_pct", "DOUBLE", "the previous report's %"), ("shares", "DOUBLE", "shares short"), ("below_threshold", "BOOLEAN", "fell below 0.5% (position closed or cut)"),
         ("latest", "BOOLEAN", "the holder's newest report on this stock")]),
    "jp_segments": (
        "Revenue, profit and assets by business segment from Japanese annual reports (this year).",
        "WITH f AS " + _LATEST_SEG + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               s.segment_label_ja AS segment, s.external_revenue_yen, s.total_revenue_yen, s.segment_profit_yen, s.segment_assets_yen
        FROM {jp}.eq_seg_products s JOIN f USING (doc_id) WHERE s.year_offset = 0
        """,
        _CO + [("segment", "VARCHAR", "segment name, Japanese"), ("external_revenue_yen", "DOUBLE", "revenue from outside customers, yen"),
               ("total_revenue_yen", "DOUBLE", "revenue including other segments, yen"), ("segment_profit_yen", "DOUBLE", "segment profit, yen"), ("segment_assets_yen", "DOUBLE", "segment assets, yen")]),
    "jp_regions": (
        "Revenue (and assets) by region from Japanese annual reports (this year): Japan, North America, China and the rest.",
        "WITH f AS " + _LATEST_SEG + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest,
               r.measure, r.label_ja AS region, r.region_key, r.value_yen, r.share_pct
        FROM {jp}.eq_seg_regions r JOIN f USING (doc_id) WHERE r.year_offset = 0 AND NOT r.is_subnote
        """,
        _CO + [("measure", "VARCHAR", "'revenue' or 'assets'"), ("region", "VARCHAR", "region as printed, Japanese"),
               ("region_key", "VARCHAR", "a normalised key (japan, north_america, china, europe, asia, other)"), ("value_yen", "DOUBLE", "yen"), ("share_pct", "DOUBLE", "% of the total")]),
    "jp_customers": (
        "Major customers (10% or more of revenue) named in Japanese annual reports, with the revenue from each.",
        "WITH f AS " + _LATEST_SEG + """
        SELECT f.sec_code, f.filer_name AS company, f.period_end AS fiscal_year_end, f.rn = 1 AS latest, c.customer_name AS customer, c.value_yen, c.segment_label
        FROM {jp}.eq_seg_customers c JOIN f USING (doc_id) WHERE c.year_offset = 0
        """,
        _CO + [("customer", "VARCHAR", "the customer as printed"), ("value_yen", "DOUBLE", "revenue from it, yen"), ("segment_label", "VARCHAR", "segment it sits in")]),
    "jp_agm_proposals": (
        "Every proposal voted at Japanese AGMs with the vote counts and approval rate, from the voting-results filings; shareholder proposals flagged.",
        """
        SELECT m.issuer_sec_code AS sec_code, m.issuer_name AS company, m.meeting_date, m.meeting_type, p.proposal_no, p.label, p.category,
               p.shareholder_proposal, p.for_votes, p.against_votes, p.abstain_votes, p.approval_pct_filed AS approval_pct, p.result
        FROM {jp}.eq_agm_proposals p JOIN {jp}.eq_agm_meetings m USING (doc_id) WHERE m.status <> 'failed'
        """,
        [("sec_code", "VARCHAR", "the company"), ("company", "VARCHAR", "its name"), ("meeting_date", "DATE", "the meeting"), ("meeting_type", "VARCHAR", "AGM or EGM"),
         ("proposal_no", "INTEGER", "number on the agenda"), ("label", "VARCHAR", "the proposal, Japanese"), ("category", "VARCHAR", "director election, dividend, articles…"),
         ("shareholder_proposal", "BOOLEAN", "proposed by a shareholder"), ("for_votes", "DOUBLE", "votes for"), ("against_votes", "DOUBLE", "votes against"),
         ("abstain_votes", "DOUBLE", "abstentions"), ("approval_pct", "DOUBLE", "approval rate as filed, %"), ("result", "VARCHAR", "passed or rejected")]),
    "jp_results": (
        "Quarterly and annual earnings releases (TDnet 決算短信) of Japanese companies, every tagged figure: results and the company's own forecasts.",
        """
        SELECT t.sec_code, t.company_name AS company, t.disclosed_on, t.fiscal_period, x.element, x.period, x.period_kind, x.nature, x.basis, x.sub_period, x.unit, x.value
        FROM {jp}.eq_tdnet_facts x JOIN {jp}.eq_tdnet_filings t USING (doc_key) WHERE t.status <> 'failed'
        """,
        [("sec_code", "VARCHAR", "the company"), ("company", "VARCHAR", "its name"), ("disclosed_on", "DATE", "release date"), ("fiscal_period", "VARCHAR", "the period reported"),
         ("element", "VARCHAR", "XBRL element (NetSales, OperatingIncome, OrdinaryIncome, ProfitAttributableToOwnersOfParent, …)"), ("period", "VARCHAR", "the period the value covers"),
         ("period_kind", "VARCHAR", "current, prior, forecast"), ("nature", "VARCHAR", "result or forecast"), ("basis", "VARCHAR", "consolidated or parent"),
         ("sub_period", "VARCHAR", "full year, half, quarter"), ("unit", "VARCHAR", "JPY (millions as filed), pure, JPYPerShares"), ("value", "DOUBLE", "the figure")]),
    # ------------------------------------------------------- official statistics
    "econ_datasets": (
        "Every official-statistics dataset Plover holds, one row each: Japan, the United States and Hong Kong (CPI, GDP, wages, yields, government finance, trade, population, BOJ, banks…).",
        """
        SELECT slug AS dataset, title, country, agency, frequency, description FROM {macro}.datasets
        """,
        [("dataset", "VARCHAR", "the dataset's slug (cpi-jp, cpi-us, ust-yields, gdp-jp…); joins econ_series.dataset"), ("title", "VARCHAR", "its title"),
         ("country", "VARCHAR", "Japan, United States or Hong Kong"), ("agency", "VARCHAR", "the publisher"), ("frequency", "VARCHAR", "daily, monthly, quarterly, annual…"),
         ("description", "VARCHAR", "what it holds")]),
    "econ_series": (
        "Every series of every dataset: find one with name_en ILIKE within a dataset, then read econ_observations by series_id.",
        """
        SELECT s.series_id, s.dataset, s.code, s.name_en, s.name_ja, s.unit, d.country, d.frequency, s.active
        FROM {macro}.series s JOIN {macro}.datasets d ON d.slug = s.dataset
        """,
        [("series_id", "BIGINT", "joins econ_observations.series_id"), ("dataset", "VARCHAR", "the dataset slug"), ("code", "VARCHAR", "the publisher's series code"),
         ("name_en", "VARCHAR", "English name"), ("name_ja", "VARCHAR", "Japanese name, when the publisher is Japanese"), ("unit", "VARCHAR", "the unit as published (index, %, ¥100mn…)"),
         ("country", "VARCHAR", "Japan, United States or Hong Kong"), ("frequency", "VARCHAR", "daily, monthly, quarterly, annual"), ("active", "BOOLEAN", "still published")]),
    "econ_observations": (
        "Every value of every series, live vintage: one row per series per period. A rate of change is worked out here from index levels "
        "(this period ÷ the same period a year earlier − 1) × 100; a blank is missing, never zero.",
        """
        SELECT o.series_id, s.dataset, s.code, s.name_en, s.unit, d.country, d.frequency, o.period, o.value
        FROM {macro}.observations o JOIN {macro}.series s USING (series_id) JOIN {macro}.datasets d ON d.slug = s.dataset
        """,
        [("series_id", "BIGINT", "the series"), ("dataset", "VARCHAR", "the dataset slug"), ("code", "VARCHAR", "the series code"), ("name_en", "VARCHAR", "the series name"),
         ("unit", "VARCHAR", "the unit as published"), ("country", "VARCHAR", "Japan, United States or Hong Kong"), ("frequency", "VARCHAR", "how often"),
         ("period", "DATE", "the period (first day of the month, quarter or year; the day for daily series)"), ("value", "DOUBLE", "the published value")]),
    "econ_latest": (
        "The latest value of every series, one row each.",
        """
        SELECT series_id, dataset, code, name_en, unit, country, frequency, max(period) AS latest_period, arg_max(value, period) AS latest_value
        FROM econ_observations WHERE value IS NOT NULL GROUP BY series_id, dataset, code, name_en, unit, country, frequency
        """,
        [("series_id", "BIGINT", "the series"), ("dataset", "VARCHAR", "the dataset slug"), ("code", "VARCHAR", "the series code"), ("name_en", "VARCHAR", "the series name"),
         ("unit", "VARCHAR", "the unit"), ("country", "VARCHAR", "the country"), ("frequency", "VARCHAR", "how often"),
         ("latest_period", "DATE", "the newest period with a value"), ("latest_value", "DOUBLE", "its value")]),
}

# the raw tables the views are built from are still listed, so a question the views do not fit can reach them
_RAW_NOTES = {
    "sec_13f_filings": "one row per 13F filing (manager, quarter, filed date)",
    "sec_13f_holdings": "every 13F line as filed; join sec_13f_filings USING (accession)",
    "sec_ins_filings": "one row per insider filing (Forms 3, 4, 5): issuer, filed date",
    "sec_ins_owners": "the insider on each filing: name, title, director / officer / 10% owner",
    "sec_ins_transactions": "each trade on an insider filing: date, code (P purchase, S sale, A award, M option exercise, F tax withholding, G gift), shares, price",
    "sec_ins_holdings": "shares the insider held after each filing",
    "sec_companies": "every SEC filer with a ticker: name, exchange, SIC industry code",
    "sec_sic": "SIC industry codes and their titles",
    "sec_filings": "every quarterly and annual financial filing in the SEC's financial statement data sets",
    "sec_facts": "every reported figure in those filings by XBRL tag (ddate = period end, qtrs = quarters covered, uom = unit)",
    "sec_lines": "the statement line each fact sits on, with its label as printed",
    "sec_tags": "what each XBRL tag means",
    "sec_segments": "the segment each fact belongs to",
    "sec_cf_facts": "every reported figure from the SEC's company facts feed, point in time (filed date), S&P 500 companies",
    "sec_cf_companies": "the companies in the company facts feed",
    "sec_cover_shares": "shares outstanding by class from each filing's cover page",
    "sec_px_facts": "proxy statement (DEF 14A) figures: executive pay by person",
    "sec_px_names": "the people named in each proxy statement",
    "sec_ads_ratios": "ADR ratios (depositary shares per ordinary share) for foreign companies",
    "us_events": "dated events: FOMC statement days, CPI release mornings, shutdowns, results days",
    "us_px_daily": "daily closing prices (the prices table is built from it)",
    "us_mcap_daily": "daily market values (market_value_daily is built from it)",
    "us_index_members": "index membership lists by snapshot (index_members is the latest)",
    "us_index_snapshots": "when each index list was taken",
    "eq_entities": "every EDINET filer (jp_companies is built from it)",
    "eq_filings": "one row per Japanese annual report: filer, year end, shares, equity, assets",
    "eq_company_year": "per annual report: board, employees, pay totals, diversity figures",
    "eq_fin_facts": "every XBRL figure in Japanese annual reports (jp_figures keeps this year and last)",
    "eq_fin_elements": "what each XBRL element means",
    "eq_fin_filings": "the annual reports the figures come from",
    "eq_lvh_filings": "5% large-shareholding filings (jp_stakes)", "eq_lvh_holders": "each holder named on a 5% filing (joint holders)",
    "eq_major_shareholders": "the shareholder register's top ten (jp_major_shareholders)", "eq_own_filings": "the register's filing and holder mix (jp_shareholder_mix)",
    "eq_own_category": "holders by category and share class",
    "eq_holdings": "cross-shareholdings line by line (jp_cross_holdings)", "eq_filing_totals": "cross-shareholding totals per report", "eq_filing_flows": "shares bought and sold in the year",
    "eq_pay_named": "named officer pay (jp_pay)", "eq_pay_category": "board pay by category (jp_pay_categories)", "eq_board": "board seats (jp_board)",
    "eq_risk_items": "risk factor headings and text (jp_risks)", "eq_risk_filings": "the reports they come from, with the full text",
    "eq_buyback_programs": "buyback programmes (jp_buybacks)", "eq_buyback_filings": "the monthly buyback reports", "eq_buyback_treasury": "treasury shares in those reports",
    "eq_short_reports": "FSA short-position reports (jp_shorts)",
    "eq_seg_products": "segment revenue (jp_segments)", "eq_seg_regions": "regional revenue (jp_regions)", "eq_seg_customers": "major customers (jp_customers)", "eq_seg_filings": "the reports they come from",
    "eq_agm_meetings": "AGM voting-result filings", "eq_agm_proposals": "each proposal's votes (jp_agm_proposals)", "eq_agm_votes": "votes per director candidate",
    "eq_tdnet_filings": "earnings releases (jp_results)", "eq_tdnet_facts": "their tagged figures", "eq_tdnet_items": "the TDnet disclosure list",
    "eq_facilities": "major facilities by site from annual reports", "eq_fac_filings": "the reports they come from",
    "eq_rental_tables": "rental property tables", "eq_rental_filings": "the reports they come from",
    "eq_bank_securities": "banks' securities holdings by type", "eq_bank_maturity": "banks' bond holdings by maturity", "eq_bank_irrbb": "banks' interest-rate risk disclosures",
    "eq_cal_rows": "the JPX earnings calendar", "eq_reclassified": "filers whose industry was reclassified",
    "datasets": "every statistics dataset (econ_datasets)", "series": "every series (econ_series)", "observations": "every value, live vintage (econ_observations)",
    "releases": "every ingest of a dataset, with its date (vintages)", "observation_vintages": "every value of every vintage, for point-in-time reads",
}


def _paths():
    """{catalog: path} of the three database files."""
    from . import db, equity_api, sec_api
    return {"sec": sec_api.DB_PATH, "jp": equity_api.DB_PATH, "macro": db.DB_PATH}


def attach(db_con, path, name=CATALOG):
    """Attach one database file read-only; False when there is no file."""
    if not path or not os.path.exists(str(path)):
        return False
    try:
        db_con.execute("ATTACH '%s' AS %s (READ_ONLY)" % (str(path).replace("'", "''"), name))
        return True
    except Exception as exc:                                      # noqa: BLE001 — the summary tables still answer
        print("us_catalog: attach %s failed: %s" % (name, exc))
        return False


def attach_all(db_con):
    """Attach every database file that exists; the set of catalog names attached."""
    done = set()
    for name, path in _paths().items():
        if attach(db_con, path, name):
            done.add(name)
    return done


def create_views(db_con, cusip_rows, attached=None):
    """The views over the attached files. `cusip_rows` maps CUSIPs to tickers
    ([{cusip, ticker, cik}], from us_link's name matching), loaded as a small
    table so fund positions join stocks. A view whose catalog is not attached
    is skipped; the names made are returned."""
    import json
    attached = set(attached) if attached is not None else set(CATALOGS)
    payload = json.dumps([[r.get("cusip"), r.get("ticker"), r.get("cik")] for r in cusip_rows])
    db_con.execute("CREATE TABLE cusip_tickers AS SELECT (j->>0)::VARCHAR AS cusip, (j->>1)::VARCHAR AS ticker, (j->>2)::BIGINT AS cik "
                   "FROM (SELECT unnest(from_json(?::JSON, '[\"JSON\"]')) AS j)", [payload])
    # the median price each security was valued at, per quarter, so a units slip in one filing shows
    try:
        if "sec" not in attached:
            raise RuntimeError("no US file")
        db_con.execute("""
            CREATE TABLE cusip_prices AS
            SELECT h.cusip, f.report_period AS quarter_end, median(h.value / h.shares) AS px
            FROM sec.sec_13f_holdings h JOIN sec.sec_13f_filings f USING (accession)
            WHERE NOT f.amendment AND h.put_call IS NULL AND h.shares > 0 AND h.value > 0
            GROUP BY 1, 2""")
    except Exception as exc:                                      # noqa: BLE001 — no 13F tables on this server
        print("us_catalog: cusip_prices skipped: %s" % exc)
        db_con.execute("CREATE TABLE cusip_prices (cusip VARCHAR, quarter_end DATE, px DOUBLE)")
    made = []
    for name, (_, sql, _) in VIEWS.items():
        needs = {c for c in CATALOGS if ("{%s}" % c) in sql}
        if needs - attached:
            continue
        try:
            db_con.execute("CREATE VIEW %s AS %s" % (name, sql.format(sec="sec", jp="jp", macro="macro")))
            made.append(name)
        except Exception as exc:                                  # noqa: BLE001 — a table missing on this server: the view is skipped
            print("us_catalog: view %s skipped: %s" % (name, exc))
    return made


def raw_tables(db_con, catalog=CATALOG):
    """[(name, rows, [(column, type)])] for every table in one attached file worth listing."""
    try:
        tabs = db_con.execute("SELECT table_name, estimated_size FROM duckdb_tables() WHERE database_name = ? ORDER BY table_name",
                              [catalog]).fetchall()
        cols = db_con.execute("SELECT table_name, column_name, data_type FROM duckdb_columns() WHERE database_name = ? "
                              "ORDER BY table_name, column_index", [catalog]).fetchall()
    except Exception:                                             # noqa: BLE001 — not attached
        return []
    by_table = {}
    for t, c, ty in cols:
        by_table.setdefault(t, []).append((c, ty))
    return [(t, n, by_table.get(t, [])) for t, n in tabs if not _BOOKKEEPING.search(t) and n]


def views_doc(made=None):
    lines = []
    for name, (what, _, cols) in VIEWS.items():
        if made is not None and name not in made:
            continue
        lines.append("%s: %s" % (name, what))
        lines += ["  %s %s: %s" % (c, t, d) for c, t, d in cols]
    return "\n".join(lines)


_CATALOG_WORDS = {"sec": "the US filings as loaded", "jp": "the Japanese filings as loaded", "macro": "the official statistics as loaded"}


def raw_doc(db_con, attached=None):
    """The other tables, from the schema: catalog, name, rows, columns with types."""
    lines = []
    for catalog in (attached or CATALOGS):
        tabs = raw_tables(db_con, catalog)
        if not tabs:
            continue
        if not lines:
            lines.append("RAW TABLES (prefix catalog.table, e.g. FROM sec.sec_13f_filings, FROM jp.eq_filings, FROM macro.series): "
                         "use a table above when one holds what is asked; these are for what none of them holds.")
        lines.append("-- %s.*: %s" % (catalog, _CATALOG_WORDS.get(catalog, "")))
        for t, n, cols in tabs:
            note = _RAW_NOTES.get(t, "")
            # a table a view above is built from is named with its note only; the view carries its columns
            built_into = re.search(r"\((fund_positions|managers|market_value_daily|index_members|jp_\w+|econ_\w+)[^)]*\)", note)
            if built_into:
                lines.append("%s.%s (%s rows) — %s" % (catalog, t, "{:,}".format(int(n)), note))
                continue
            lines.append("%s.%s (%s rows)%s: %s" % (catalog, t, "{:,}".format(int(n)), (" — " + note) if note else "",
                                                 ", ".join("%s %s" % (c, ty.lower()) for c, ty in cols[:14]) + (", …" if len(cols) > 14 else "")))
    return "\n".join(lines)
