# -*- coding: utf-8 -*-
"""Platform-calculated financial ratios — the screener's numbers.

Everything under /equity/financials/ is as filed. This module is the one
place a number is *computed*, and it follows the macro side's rule for
derived values: no badge, a formula instead, and every input named. A row
carries, for each metric, the formula, the inputs it used (value, element,
fiscal-year offset) and, where the filer prints the same ratio, the filer's
figure beside ours so the two can be reconciled.

Conventions — chosen to match how Japanese filers and sell-side analysts
compute these, and stated on the Methodology page:

  * Return ratios use the AVERAGE of opening and closing balances (the
    filer's own ROE convention under the Cabinet Office Ordinance). A row
    whose opening balance is not tagged gets no ratio rather than a
    closing-balance one, and a row whose average base is not positive gets
    none either — a return measured on negative equity inverts its own sign
    and would rank a loss-making, balance-sheet-insolvent company at the top.
    Such a row is flagged `negative_equity` instead, which is the finding.
    Filers disagree on the denominator (Chiyoda uses the average and matches
    this platform; Kodama Chemical uses the closing balance, and prints 82.8%
    where the average gives 144.9%) — hence one consistent convention here,
    with the filer's own figure and the difference on every row.
  * Equity is equity attributable to owners of the parent (自己資本): under
    IFRS the tagged element; under Japan GAAP net assets less
    non-controlling interests less subscription rights to shares. A balance
    sheet that prints no such line has none, so a missing subtrahend is nil
    — the only place this module treats an absent tag as zero, and only
    ever for a subtractive component below a total that is present.
  * Revenue and profit are the filer's own summary lines, resolved the same
    way as the key-indicator panel (banks and insurers report 経常収益;
    a filer with a custom revenue element is taken at its first summary
    line). Operating income is the summary's where printed, else the income
    statement's.
  * Free cash flow is operating cash flow less capital expenditure — the
    cash-flow statement's purchases of property, plant and equipment and of
    intangible assets. It was once operating plus *all* investing cash flow,
    which counts a treasury desk's securities buying as spending and a
    cross-shareholding sale as free cash: Sony read −¥25bn against ¥1.49tn
    on capex, Denso +¥494bn against ¥77bn, and one non-financial company in
    five was off by more than half. No capex line tagged, no free cash flow.
    Banks, insurers and brokers get none: their operating cash flow is the
    balance sheet moving, and "free" cash is not a meaningful figure there.
  * Valuation ratios exist only where the filer prints its year-end PER:
    implied price = PER × EPS, and everything with a price in it is
    labelled implied. This platform has no price feed.

Consolidated where the filing has consolidated statements, parent-only
otherwise; the row says which.
"""
import re
import threading

from . import equity_api

# ---- inputs ----------------------------------------------------------------
# Balance-sheet elements read from the statements (facts table), by concept.
# Lists are in priority order; the IFRS twin first where it is unambiguous.
BS_ELEMENTS = {
    "total_assets": ["jpigp_cor:AssetsIFRS", "jppfs_cor:Assets"],
    "equity_owners_ifrs": ["jpigp_cor:EquityAttributableToOwnersOfParentIFRS"],
    "net_assets": ["jppfs_cor:NetAssets"],
    "nci": ["jppfs_cor:NonControllingInterests"],
    "subscription_rights": ["jppfs_cor:SubscriptionRightsToShares"],
    "operating_income_stmt": ["jpigp_cor:OperatingProfitLossIFRS", "jppfs_cor:OperatingIncome"],
}
# Capital expenditure, from the cash flow statement. A filer tags either one
# combined line or property-plant-and-equipment and intangibles separately;
# the combined forms are taken first so nothing is counted twice.
CAPEX_COMBINED = ["jpigp_cor:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssetsInvCFIFRS",
                  "jppfs_cor:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssetsInvCF",
                  "jppfs_cor:PurchaseOfNoncurrentAssetsInvCF"]
CAPEX_PPE = ["jpigp_cor:PurchaseOfPropertyPlantAndEquipmentInvCFIFRS",
             "jppfs_cor:PurchaseOfPropertyPlantAndEquipmentInvCF"]
CAPEX_INTANGIBLE = ["jpigp_cor:PurchaseOfIntangibleAssetsInvCFIFRS",
                    "jppfs_cor:PurchaseOfIntangibleAssetsInvCF"]
# A filer with its own capex elements (Toyota's AdditionsToFixedAssets…,
# NTT's PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsAndInvestment…)
# is read by name: a payment for fixed or intangible assets on the investing
# side. Retirements, equipment leased out to customers (a finance arm's
# lending, not the company's own plant) and "other" fixed assets (a remainder
# beside a line we cannot see) are not capex of this kind.
_EXT_CAPEX = re.compile(
    r"^CapitalExpenditures?InvCF(IFRS)?$|"
    r"^(PurchaseOf|PurchasesOf|AdditionsTo|AcquisitionOf|AcquisitionsOf|PaymentsForPurchaseOf|"
    r"PaymentsForAcquisitionOf)\w*?(NonCurrentAssets|NoncurrentAssets|FixedAssets|PropertyPlant|"
    r"PropertyEquipment|TangibleAndIntangible|TangibleAssets|IntangibleAssets)\w*InvCF(IFRS)?$")
_EXT_EXCLUDE = re.compile(r"Retirement|^AdditionsToEquipmentLeasedToOthers|(Of|To)Other(?!s)")
_EXT_COMBINED = re.compile(r"^CapitalExpenditure|NonCurrentAssets|NoncurrentAssets|TangibleAndIntangible|"
                           r"AndIntangible|IntangibleAssetsAnd")


def ext_capex_elements(element):
    local = element.split(":")[-1]
    if not _EXT_CAPEX.match(local) or _EXT_EXCLUDE.search(local):
        return None
    return "combined" if _EXT_COMBINED.search(local) else "part"


# Cash position, read from the lines printed on the face of the balance
# sheet. Liquid cash is cash and deposits plus short-term securities (有価証券
# in current assets) under Japan GAAP. Under IFRS it is cash and cash
# equivalents alone: IFRS balance sheets fold time deposits and short-term
# investments into "other financial assets" beside derivatives and loans, so
# there is no clean line to add. Investment securities (投資有価証券, mostly
# cross-shareholdings) are shown on their own and never counted as cash.
CASH_IFRS = "jpigp_cor:CashAndCashEquivalentsIFRS"
CASH_JGAAP = "jppfs_cor:CashAndDeposits"
ST_SECURITIES = "jppfs_cor:ShortTermInvestmentSecurities"
INV_SECURITIES = "jppfs_cor:InvestmentSecurities"
# Interest-bearing debt: borrowings, bonds, convertibles and commercial paper.
# Lease liabilities are left out — under IFRS nearly every lease is on the
# balance sheet and under Japan GAAP most are not, so counting them would make
# an IFRS filer look more indebted for an accounting reason. No filer prints a
# borrowing total beside its own parts, so the lines present are summed; a line
# the balance sheet does not print is a debt the company does not have.
DEBT_ELEMENTS = [
    "jppfs_cor:ShortTermLoansPayable", "jppfs_cor:ShortTermBondsPayable",
    "jppfs_cor:CommercialPapersLiabilities", "jppfs_cor:CurrentPortionOfLongTermLoansPayable",
    "jppfs_cor:CurrentPortionOfBonds", "jppfs_cor:CurrentPortionOfBondsWithSubscriptionRightsToShares",
    "jppfs_cor:CurrentPortionOfConvertibleBonds", "jppfs_cor:BondsPayable",
    "jppfs_cor:LongTermLoansPayable", "jppfs_cor:ConvertibleBondTypeBondsWithSubscriptionRightsToShares",
    "jppfs_cor:BondsWithSubscriptionRightsToSharesNCL", "jppfs_cor:ConvertibleBonds",
    "jppfs_cor:ShortTermLoansPayableToSubsidiariesAndAffiliates",
    "jppfs_cor:LongTermLoansPayableToSubsidiariesAndAffiliates",
    "jppfs_cor:CurrentPortionOfLongTermLoansPayableToSubsidiariesAndAffiliates",
    "jpigp_cor:BondsAndBorrowingsCLIFRS", "jpigp_cor:BondsAndBorrowingsNCLIFRS",
    "jpigp_cor:BondsAndBorrowingsLiabilitiesIFRS", "jpigp_cor:BorrowingsCLIFRS",
    "jpigp_cor:BorrowingsNCLIFRS", "jpigp_cor:BorrowingsLiabilitiesIFRS",
    "jpigp_cor:CurrentPortionOfLongTermBorrowingsCLIFRS", "jpigp_cor:BondsPayableCLIFRS",
    "jpigp_cor:BondsPayableNCLIFRS", "jpigp_cor:BondsPayableLiabilitiesIFRS",
    "jpigp_cor:LongTermDebtNCLIFRS", "jpigp_cor:InterestBearingLiabilitiesCLIFRS",
    "jpigp_cor:InterestBearingLiabilitiesNCLIFRS", "jpigp_cor:InterestBearingLiabilitiesLiabilitiesIFRS",
]
# A borrowing-like liability line outside that list — usually one the filer
# named itself — is debt when its name is borrowing alone (Sony's and
# Hitachi's CurrentPortionOfLongTermDebt, a non-recourse loan, a convertible's
# current portion, Honda's and Subaru's FinancingLiabilities — 資金調達に係る
# 債務, the finance arm's borrowing). One whose name mixes in leases or "other
# financial liabilities" cannot be split, so net cash is withheld rather than
# computed with a lease left in or a debt left out.
_MIXED = re.compile(r"Lease|OtherFinancial")
_DEBT_LIKE = re.compile(r"Loan|Borrowing|Bond|CommercialPaper|Debt|FinancingLiabilit")
# An IFRS balance sheet with no borrowing line at all may still owe money: some
# filers print bonds and loans inside "(other) financial liabilities" beside
# deposits received and derivatives (Fast Retailing's bonds, NSK's borrowings).
# Absent there means unknown, not none.
_FIN_LIAB_CATCHALL = re.compile(r"FinancialLiabilities(CL|NCL)?IFRS$")
_LIABILITY = re.compile(r"(CL|NCL|Liabilities)(IFRS)?$|Payable|Borrowing")
_NOT_DEBT = re.compile(r"Receivable|Assets?(IFRS)?$|N?CA(IFRS)?$|IOA$|DA$|BadDebt|Allowance|"
                       r"Provision|Reserve|InterestPayable|Guarantee|Deposit")


_IB_PREFIX = "jpigp_cor:InterestBearingLiabilities"
_LEASE_LINE = re.compile(r"Lease(Liabilit|Obligation)")


def other_debt_line(element):
    """For a liability line outside DEBT_ELEMENTS: "debt" when its name is
    borrowing alone, "mixed" when it folds in leases or other liabilities,
    None when it is not a borrowing line."""
    local = element.split(":")[-1]
    if (element in DEBT_ELEMENTS or not _DEBT_LIKE.search(local)
            or not _LIABILITY.search(local) or _NOT_DEBT.search(local)):
        return None
    return "mixed" if _MIXED.search(local) else "debt"


# Sectors where free cash flow is not a meaningful figure (TSE 33 names).
NO_FCF_INDUSTRIES = ("銀行業", "保険業", "証券、商品先物取引業", "その他金融業")
ALL_BS_ELEMENTS = sorted({e for lst in BS_ELEMENTS.values() for e in lst} |
                         set(CAPEX_COMBINED + CAPEX_PPE + CAPEX_INTANGIBLE))

FORMULAS = {
    "roe_pct": "profit attributable to owners ÷ average(equity attributable to owners, opening and closing) × 100; not computed when that average is not positive, because a return on a negative base inverts its own sign",
    "roa_pct": "profit attributable to owners ÷ average(total assets, opening and closing) × 100; not computed when that average is not positive",
    "operating_margin_pct": "operating income ÷ revenue × 100",
    "net_margin_pct": "profit attributable to owners ÷ revenue × 100",
    "equity_ratio_pct": "equity attributable to owners ÷ total assets × 100, closing balances",
    "asset_turnover_x": "revenue ÷ average(total assets, opening and closing)",
    "revenue_growth_pct": "(revenue ÷ prior-year revenue − 1) × 100, both from the same filing",
    "profit_growth_pct": "(profit ÷ prior-year profit − 1) × 100; not computed when the prior year was a loss",
    "cash_conversion_x": "operating cash flow ÷ profit attributable to owners; not computed for a loss",
    "fcf_yen": "operating cash flow − capital expenditure (purchases of property, plant and equipment and of intangible assets, from the cash flow statement); not computed where no capex line is tagged, or for banks, insurers and brokers",
    "fcf_margin_pct": "free cash flow ÷ revenue × 100",
    "liquid_cash_yen": "Japan GAAP: cash and deposits + short-term securities (有価証券, current assets; nil where the balance sheet prints none). IFRS: cash and cash equivalents only — IFRS balance sheets put short-term investments in 'other financial assets' with derivatives and loans. Closing balances; not computed for banks, insurers and brokers",
    "net_cash_yen": "liquid cash − interest-bearing debt (short- and long-term borrowings, bonds, convertible bonds and commercial paper, current and non-current, including non-recourse loans; lease liabilities excluded). Negative means net debt. Consolidated, so a captive finance arm's borrowing counts as debt (carmakers' auto-loan funding). Not computed where the balance sheet folds borrowings and leases into one line — including an IFRS interest-bearing liabilities line with no separate lease line — where an IFRS balance sheet prints no borrowing line but does print (other) financial liabilities, which can hold bonds and loans, or for banks, insurers and brokers",
    "investment_securities_yen": "investment securities (投資有価証券, non-current assets) as printed on the Japan GAAP balance sheet — shareholdings in other companies, cross-shareholdings among them, and bonds held for over a year; not included in liquid or net cash. Not available under IFRS, which has no equivalent line",
    "cash_to_assets_pct": "liquid cash ÷ total assets × 100, closing balances",
    "net_cash_to_assets_pct": "net cash ÷ total assets × 100, closing balances",
    "investment_securities_to_assets_pct": "investment securities ÷ total assets × 100, closing balances",
    "pbr_implied_x": "(filer's year-end PER × EPS) ÷ book value per share — a price implied from the filer's own PER, not a market quote",
    "dividend_yield_implied_pct": "dividend per share ÷ (year-end PER × EPS, all three from the same summary table — the reporting company's, where filers print dividends) × 100 — implied, not a market quote; withheld where the filer's own payout ratio disagrees with DPS ÷ EPS by over 25 pp",
    "equity_owners_yen": "IFRS: equity attributable to owners of parent as tagged; Japan GAAP: net assets − non-controlling interests − subscription rights to shares (absent lines nil); US GAAP (statements filed as text): the equity attributable to owners of parent line of the filer's own five-year summary",
}
METRIC_LABELS = {
    "roe_pct": ("ROE", "%"), "roa_pct": ("ROA", "%"),
    "operating_margin_pct": ("Operating margin", "%"), "net_margin_pct": ("Net margin", "%"),
    "equity_ratio_pct": ("Equity ratio", "%"), "asset_turnover_x": ("Asset turnover", "×"),
    "revenue_growth_pct": ("Revenue growth", "%"), "profit_growth_pct": ("Profit growth", "%"),
    "cash_conversion_x": ("Cash conversion", "×"), "fcf_yen": ("Free cash flow", "¥"),
    "fcf_margin_pct": ("FCF margin", "%"),
    "liquid_cash_yen": ("Liquid cash", "¥"), "net_cash_yen": ("Net cash", "¥"),
    "investment_securities_yen": ("Investment securities", "¥"),
    "cash_to_assets_pct": ("Liquid cash / assets", "%"),
    "net_cash_to_assets_pct": ("Net cash / assets", "%"),
    "investment_securities_to_assets_pct": ("Investment securities / assets", "%"),
    "pbr_implied_x": ("PBR (implied)", "×"), "dividend_yield_implied_pct": ("Dividend yield (implied)", "%"),
}
METRIC_ORDER = ["roe_pct", "roa_pct", "operating_margin_pct", "net_margin_pct", "equity_ratio_pct",
                "asset_turnover_x", "revenue_growth_pct", "profit_growth_pct", "cash_conversion_x",
                "fcf_yen", "fcf_margin_pct", "liquid_cash_yen", "net_cash_yen",
                "investment_securities_yen", "cash_to_assets_pct", "net_cash_to_assets_pct",
                "investment_securities_to_assets_pct", "pbr_implied_x",
                "dividend_yield_implied_pct"]
# Size / identity fields a screen filters and sorts on besides the ratios.
SIZE_FIELDS = ["revenue_yen", "profit_yen", "total_assets_yen", "equity_owners_yen", "cf_operating_yen"]

LATEST_SQL = """
    SELECT * FROM (
        SELECT f.doc_id, f.sec_code, f.edinet_code, f.filer_name, f.period_end, f.filed_date,
               f.accounting_standard, f.status, f.statements,
               row_number() OVER (PARTITION BY f.sec_code ORDER BY f.period_end DESC, f.filed_date DESC) AS rn
        FROM eq_fin_filings f
        WHERE f.sec_code IS NOT NULL AND f.status IN ('clean','partial')
    ) WHERE rn = 1
"""
SUMMARY_SQL = """
    WITH latest AS (%s)
    SELECT l.doc_id, l.basis, l.ord, l.element, f.year_offset, f.unit, f.value, f.period_kind
    FROM eq_fin_lines l JOIN latest g USING (doc_id)
    JOIN eq_fin_facts f ON f.doc_id = l.doc_id AND f.element = l.element AND f.basis = l.basis
    WHERE l.statement = 'summary' AND f.year_offset IN (0, -1)
    ORDER BY l.doc_id, l.basis, f.year_offset DESC, l.ord
""" % LATEST_SQL
BS_SQL = """
    WITH latest AS (%s)
    SELECT f.doc_id, f.basis, f.element, f.year_offset, f.value
    FROM eq_fin_facts f JOIN latest g USING (doc_id)
    WHERE f.element IN (%s) AND f.year_offset IN (0, -1) AND f.period_kind IN ('instant', 'duration')
""" % (LATEST_SQL, ",".join("'%s'" % e for e in ALL_BS_ELEMENTS))
EXT_CAPEX_SQL = """
    WITH latest AS (%s)
    SELECT f.doc_id, f.basis, f.element, f.value
    FROM eq_fin_facts f JOIN latest g USING (doc_id)
    WHERE f.year_offset = 0 AND f.element LIKE '%%InvCF%%'
      AND f.element NOT LIKE 'jppfs_cor:%%' AND f.element NOT LIKE 'jpigp_cor:%%'
""" % LATEST_SQL
# Every line on the face of the balance sheet, with its current-year value:
# the cash position is summed from what the statement prints, not from notes.
BS_LINES_SQL = """
    WITH latest AS (%s)
    SELECT l.doc_id, l.basis, l.element, f.value
    FROM eq_fin_lines l JOIN latest g USING (doc_id)
    LEFT JOIN eq_fin_facts f ON f.doc_id = l.doc_id AND f.element = l.element AND f.basis = l.basis
         AND f.year_offset = 0 AND f.period_kind = 'instant'
    WHERE l.statement = 'bs'
""" % LATEST_SQL
NAMES_SQL = "WITH x AS (SELECT 1)" + equity_api.NAME_CTES + """
    SELECT e.edinet_code, e.sec_code, e.industry, coalesce(n.name_en, s.name_en) AS name_en
    FROM eq_entities e
    LEFT JOIN en_ecode n ON n.edinet_code = e.edinet_code
    LEFT JOIN en_scode s ON s.sec_code = e.sec_code
    WHERE e.edinet_code IN (SELECT edinet_code FROM (%s))
""" % LATEST_SQL

_MEMO = {"version": None, "rows": None}
_LOCK = threading.Lock()


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _avg(a, b):
    if a is None or b is None:
        return None
    return (a + b) / 2.0


def _pct(x):
    return None if x is None else round(x * 100.0, 4)


def _r(x, dp=4):
    return None if x is None else round(x, dp)


def _summary_fields(rows):
    """Summary lines of one (doc, basis) -> {offset: {field: (value, element)}}
    using the panel's mapping, including its revenue fallback."""
    from .financials_api import PERCENT_FIELDS, field_of   # lazy: avoids an import cycle
    out = {}
    for off in (0, -1):
        lines = [r for r in rows if r["year_offset"] == off]
        fields, rank = {}, {}
        first = None
        for r in lines:
            f, rk = field_of(r["element"], r["unit"])
            if f is None:
                if (first is None and r["unit"] == "JPY" and r["period_kind"] == "duration"
                        and r["value"] is not None):
                    first = (r["value"], r["element"])
                continue
            if f in rank and rank[f] <= rk:
                continue
            v = r["value"]
            if f in PERCENT_FIELDS and v is not None:
                v = round(v * 100.0, 4)
            fields[f] = (v, r["element"])
            rank[f] = rk
        if "revenue" not in fields and first:
            fields["revenue"] = first
            fields["revenue_source"] = ("first_line", None)
        out[off] = fields
    return out


def _bs(facts, concept, off):
    """(value, element) for a balance-sheet concept at an offset, first
    candidate present wins."""
    for el in BS_ELEMENTS[concept]:
        v = facts.get((el, off))
        if v is not None:
            return v, el
    return None, None


def _capex(facts):
    """(capex as a positive yen amount, element description) for the
    current year, or (None, None) where the filer tags no capex line."""
    def first(els):
        for el in els:
            v = facts.get((el, 0))
            if v is not None:
                return abs(v), el
        return None, None
    v, el = first(CAPEX_COMBINED)
    if v is not None:
        return v, el
    ppe, ppe_el = first(CAPEX_PPE)
    if ppe is None:
        # the filer's own elements: one combined line, else the sum of parts
        ext = [(el, abs(v)) for (el, off), v in facts.items()
               if off == 0 and v is not None and not el.startswith(("jppfs_cor:", "jpigp_cor:"))
               and ext_capex_elements(el)]
        comb = [x for x in ext if ext_capex_elements(x[0]) == "combined"]
        use = comb[:1] if comb else ext
        if not use:
            # a filer that bought only software tags intangibles and no plant
            return first(CAPEX_INTANGIBLE)
        return sum(v for _, v in use), " + ".join(el for el, _ in use)
    # intangibles sit below a PP&E line that is present: absent means none
    intang, int_el = first(CAPEX_INTANGIBLE)
    if intang is None:
        return ppe, ppe_el
    return ppe + intang, ppe_el + " + " + int_el


def cash_position(face, financial):
    """Liquid cash, interest-bearing debt and investment securities from the
    face of one balance sheet ({element: current-year value}).

    Returns {name: (value, element description)} and {check: note}."""
    out, notes = {}, {}
    if financial:
        notes["cash_position_withheld"] = ("a bank, insurer or broker: cash and securities are "
                                           "its operating assets, so a cash position is not a "
                                           "meaningful figure")
        return out, notes
    if not face:
        return out, notes
    if face.get(CASH_IFRS) is not None:
        out["liquid_cash"] = (face[CASH_IFRS], CASH_IFRS)
    elif face.get(CASH_JGAAP) is not None:
        st = face.get(ST_SECURITIES)
        if st is None:
            out["liquid_cash"] = (face[CASH_JGAAP], CASH_JGAAP)
        else:
            out["liquid_cash"] = (face[CASH_JGAAP] + st, CASH_JGAAP + " + " + ST_SECURITIES)
    if face.get(INV_SECURITIES) is not None:
        out["investment_securities"] = (face[INV_SECURITIES], INV_SECURITIES)
    mixed = sorted(el for el in face if other_debt_line(el) == "mixed")
    ib_with_leases = []
    # IFRS "interest-bearing liabilities" includes lease liabilities unless
    # the balance sheet prints leases on a line of their own: under IFRS 16
    # nearly every company has them, so a sheet with no lease line has put
    # them inside (Toyota, JAL, SoftBank Corp).
    if not any(_LEASE_LINE.search(el) for el in face):
        ib_with_leases = sorted(el for el in face if el.startswith(_IB_PREFIX))
    # a borrowing line printed with no value this year (repaid) still says the
    # filer shows its borrowings separately
    borrowing_printed = any(el in DEBT_ELEMENTS or other_debt_line(el) == "debt" for el in face)
    catchall = [] if borrowing_printed else sorted(el for el in face if _FIN_LIAB_CATCHALL.search(el))
    if mixed or ib_with_leases or catchall:
        notes["net_cash_withheld"] = (
            "the balance sheet puts borrowings and leases in one line, so debt cannot be separated"
            if mixed else
            "the balance sheet's interest-bearing liabilities have no separate lease line beside "
            "them, so they most likely include leases"
            if ib_with_leases else
            "the balance sheet prints no borrowing line, and any bonds or loans would sit inside "
            "its financial liabilities beside other items, so debt cannot be separated")
        notes["net_cash_withheld_lines"] = ", ".join(mixed + ib_with_leases + catchall)
    else:
        debt = [(el, face[el]) for el in DEBT_ELEMENTS if face.get(el) is not None]
        debt += sorted((el, v) for el, v in face.items()
                       if v is not None and other_debt_line(el) == "debt")
        out["interest_bearing_debt"] = (sum(v for _, v in debt),
                                        " + ".join(el for el, _ in debt)
                                        or ("borrowing line printed with no balance" if borrowing_printed
                                            else "no borrowing line printed"))
    return out, notes


def compute_row(head, summary_rows, bs_facts_by_basis, bs_face_by_basis=None):
    """One company's metrics from its latest filing.

    summary_rows: rows of SUMMARY_SQL for the doc (both bases).
    bs_facts_by_basis: {basis: {(element, offset): value}}.
    bs_face_by_basis: {basis: {element: current-year value}}, the lines
    printed on the face of the balance sheet.
    """
    bases_with_summary = {r["basis"] for r in summary_rows}
    basis = "consolidated" if "consolidated" in bases_with_summary else "parent"
    if "consolidated" not in bases_with_summary and "parent" not in bases_with_summary:
        basis = "consolidated" if "consolidated" in bs_facts_by_basis else "parent"
    S = _summary_fields([r for r in summary_rows if r["basis"] == basis])
    # Per-share dividends and the payout ratio are parent-company items: the
    # filer prints them in the reporting company's own summary table, not
    # the group's. The yield is therefore computed entirely inside that
    # table — its DPS against the price implied by ITS OWN PER × EPS — so a
    # share split restated in one table and not the other cannot mix bases.
    P = (_summary_fields([r for r in summary_rows if r["basis"] == "parent"])
         if basis == "consolidated" else S)
    for off in (0, -1):
        for f in ("dps", "dps_interim", "payout_ratio_pct"):
            if f not in S.get(off, {}) and f in P.get(off, {}):
                S.setdefault(off, {})[f] = P[off][f]
    B = bs_facts_by_basis.get(basis, {})
    inputs = {}

    def take(name, pair, off):
        v, el = pair
        if v is not None:
            inputs[name] = {"value": v, "element": el, "year_offset": off}
        return v

    s0, s1 = S.get(0, {}), S.get(-1, {})
    revenue = take("revenue", s0.get("revenue", (None, None)), 0)
    revenue_prior = take("revenue_prior", s1.get("revenue", (None, None)), -1)
    profit = take("profit", s0.get("profit", (None, None)), 0)
    profit_prior = take("profit_prior", s1.get("profit", (None, None)), -1)
    op = s0.get("operating_income", (None, None))
    if op[0] is None:
        op = _bs(B, "operating_income_stmt", 0)
    operating_income = take("operating_income", op, 0)
    cfo = take("cf_operating", s0.get("cf_operating", (None, None)), 0)
    financial = (head.get("industry") or "") in NO_FCF_INDUSTRIES
    pos, pos_notes = cash_position((bs_face_by_basis or {}).get(basis, {}), financial)
    liquid = take("liquid_cash", pos.get("liquid_cash", (None, None)), 0)
    debt = take("interest_bearing_debt", pos.get("interest_bearing_debt", (None, None)), 0)
    inv_sec = take("investment_securities", pos.get("investment_securities", (None, None)), 0)
    net_cash = liquid - debt if (liquid is not None and debt is not None) else None

    ta = _bs(B, "total_assets", 0)
    if ta[0] is None:
        ta = s0.get("total_assets", (None, None))
    ta1 = _bs(B, "total_assets", -1)
    if ta1[0] is None:
        ta1 = s1.get("total_assets", (None, None))
    total_assets = take("total_assets", ta, 0)
    total_assets_prior = take("total_assets_prior", ta1, -1)

    def equity_owners(off):
        v, el = _bs(B, "equity_owners_ifrs", off)
        if v is not None:
            return v, el
        na, el = _bs(B, "net_assets", off)
        if na is not None:
            nci = _bs(B, "nci", off)[0] or 0.0
            sub = _bs(B, "subscription_rights", off)[0] or 0.0
            return na - nci - sub, "jppfs_cor:NetAssets − NonControllingInterests − SubscriptionRightsToShares"
        # US GAAP filers tag their statements as text, so the balance sheet
        # carries nothing; their five-year summary still does. Only an element
        # that names the owners of the parent is 自己資本 — a plain 純資産額
        # includes minorities and would flatter the return — so this fallback
        # is taken on the element name, not on the mapped field alone.
        sv = S.get(off, {}).get("net_assets")
        if sv and "AttributableToOwnersOfParent" in sv[1]:
            return sv[0], sv[1]
        return None, None
    eq = take("equity_owners", equity_owners(0), 0)
    eq_prior = take("equity_owners_prior", equity_owners(-1), -1)

    per = take("per_filed", s0.get("per", (None, None)), 0)
    eps = take("eps_filed", s0.get("eps", (None, None)), 0)
    bps = take("bps_filed", s0.get("bps", (None, None)), 0)
    dps = take("dps_filed", s0.get("dps", (None, None)), 0)
    roe_filed = take("roe_filed_pct", s0.get("roe_pct", (None, None)), 0)
    eqr_filed = take("equity_ratio_filed_pct", s0.get("equity_ratio_pct", (None, None)), 0)
    payout_filed = take("payout_ratio_filed_pct", s0.get("payout_ratio_pct", (None, None)), 0)

    # A return ratio is only interpretable over a POSITIVE base. Japan Display
    # closed FY2026 with equity of −¥7.5bn against +¥6.7bn a year earlier, so
    # its average equity is negative and a ¥19.8bn LOSS divided by it came back
    # as +4,570% — the sign inverted and a failing company screened as the best
    # in the market. The test is the denominator actually used (the average),
    # not the endpoints: across the universe this suppresses exactly the three
    # companies whose own filings decline to print an ROE, and leaves every
    # company that does print one — including two that recovered from negative
    # equity during the year — matching the filer to 0.1 pp.
    avg_equity = _avg(eq, eq_prior)
    avg_assets = _avg(total_assets, total_assets_prior)
    roe_note = None
    if avg_equity is not None and avg_equity <= 0:
        roe_note = ("average equity over the year is not positive (¥%.0fmn): a return "
                    "measured on a negative base inverts its own sign, so none is shown"
                    % (avg_equity / 1e6))
    roa_note = None
    if avg_assets is not None and avg_assets <= 0:
        roa_note = "average total assets over the year is not positive"

    implied_price = per * eps if (per is not None and eps is not None and per > 0) else None
    # The yield's price comes from the same table as the dividend (see P
    # above): the parent table's own PER × EPS where the filer has one.
    p0 = P.get(0, {})
    per_p = take("per_dividend_table", p0.get("per", (None, None)), 0)
    eps_p = take("eps_dividend_table", p0.get("eps", (None, None)), 0)
    yield_price = per_p * eps_p if (per_p is not None and eps_p is not None and per_p > 0) else None
    # Second line of defence: the filer's own payout ratio, printed in that
    # same table, must agree with DPS ÷ EPS there. Where it does not, the
    # per-share figures are not on one basis and no yield is derived.
    dps_note = None
    if dps is not None and eps_p is not None and eps_p > 0:
        implied_payout = dps / eps_p * 100.0
        if payout_filed is not None and abs(implied_payout - payout_filed) > 25:
            dps_note = ("dps and eps on different share bases: dps ÷ eps = %.0f%% but the "
                        "filer prints a payout ratio of %.1f%%" % (implied_payout, payout_filed))
    elif dps is not None and yield_price is None:
        dps_note = "the table carrying the dividend prints no PER or EPS to imply a price from"
    capex = take("capex", _capex(B), 0)
    fcf_note = None
    if financial:
        fcf_note = ("a bank, insurer or broker: its operating cash flow is its balance "
                    "sheet moving, so free cash flow is not a meaningful figure")
    elif capex is None:
        fcf_note = "the cash flow statement tags no capital-expenditure line"
    fcf = (cfo - capex) if (fcf_note is None and cfo is not None) else None
    m = {
        "roe_pct": _pct(_div(profit, avg_equity)) if roe_note is None else None,
        "roa_pct": _pct(_div(profit, avg_assets)) if roa_note is None else None,
        "operating_margin_pct": _pct(_div(operating_income, revenue)) if revenue and revenue > 0 else None,
        "net_margin_pct": _pct(_div(profit, revenue)) if revenue and revenue > 0 else None,
        "equity_ratio_pct": _pct(_div(eq, total_assets)),
        "asset_turnover_x": _r(_div(revenue, avg_assets)) if roa_note is None else None,
        "revenue_growth_pct": _pct(_div(revenue, revenue_prior) - 1) if (revenue is not None and revenue_prior) and revenue_prior > 0 else None,
        "profit_growth_pct": _pct(_div(profit, profit_prior) - 1) if (profit is not None and profit_prior) and profit_prior > 0 else None,
        "cash_conversion_x": _r(_div(cfo, profit)) if (profit and profit > 0) else None,
        "fcf_yen": fcf,
        "fcf_margin_pct": _pct(_div(fcf, revenue)) if revenue and revenue > 0 else None,
        "liquid_cash_yen": liquid,
        "net_cash_yen": net_cash,
        "investment_securities_yen": inv_sec,
        "cash_to_assets_pct": _pct(_div(liquid, total_assets)),
        "net_cash_to_assets_pct": _pct(_div(net_cash, total_assets)),
        "investment_securities_to_assets_pct": _pct(_div(inv_sec, total_assets)),
        "pbr_implied_x": _r(_div(implied_price, bps)) if (bps and bps > 0) else None,
        "dividend_yield_implied_pct": _pct(_div(dps, yield_price)) if dps_note is None else None,
    }
    checks = {}
    if roe_note:
        checks["roe_withheld"] = roe_note
    if roa_note:
        checks["roa_withheld"] = roa_note
    if dps_note:
        checks["dividend_yield_withheld"] = dps_note
    if fcf_note and cfo is not None:
        checks["fcf_yen_withheld"] = checks["fcf_margin_withheld"] = fcf_note
    checks.update(pos_notes)
    # Negative book equity is a finding in itself, not an absence: the screener
    # shows it in place of the blank ROE so a reader sees why there is none.
    flags = []
    if eq is not None and eq <= 0:
        flags.append("negative_equity")
    elif eq_prior is not None and eq_prior <= 0:
        flags.append("negative_equity_prior_year")
    if roe_filed is not None and m["roe_pct"] is not None:
        checks["roe_vs_filed_pp"] = round(m["roe_pct"] - roe_filed, 2)
    if eqr_filed is not None and m["equity_ratio_pct"] is not None:
        checks["equity_ratio_vs_filed_pp"] = round(m["equity_ratio_pct"] - eqr_filed, 2)
    return {
        "sec_code": head["sec_code"], "edinet_code": head["edinet_code"],
        "filer_name": head["filer_name"], "doc_id": head["doc_id"],
        "period_end": head["period_end"].isoformat(), "filed_date": head["filed_date"].isoformat(),
        "accounting_standard": head["accounting_standard"], "status": head["status"],
        "basis": basis,
        "size": {"revenue_yen": revenue, "profit_yen": profit, "total_assets_yen": total_assets,
                 "equity_owners_yen": eq, "cf_operating_yen": cfo},
        "metrics": m, "filed": {"roe_pct": roe_filed, "equity_ratio_pct": eqr_filed,
                                "per": per, "eps": eps, "bps": bps, "dps": dps},
        "checks": checks, "flags": flags, "inputs": inputs,
        "revenue_source": "first_line" if "revenue_source" in s0 else None,
    }


def all_rows(cur):
    """Metrics for every company's latest filing, memoised per DB version."""
    version = equity_api._version()
    with _LOCK:
        if _MEMO["version"] == version and _MEMO["rows"] is not None:
            return _MEMO["rows"]
    heads = {r["doc_id"]: r for r in equity_api._rows(cur, LATEST_SQL)}
    summary = {}
    for r in equity_api._rows(cur, SUMMARY_SQL):
        summary.setdefault(r["doc_id"], []).append(r)
    bs = {}
    for r in equity_api._rows(cur, BS_SQL):
        bs.setdefault(r["doc_id"], {}).setdefault(r["basis"], {})[(r["element"], r["year_offset"])] = r["value"]
    for r in equity_api._rows(cur, EXT_CAPEX_SQL):
        if ext_capex_elements(r["element"]):
            bs.setdefault(r["doc_id"], {}).setdefault(r["basis"], {})[(r["element"], 0)] = r["value"]
    face = {}
    for r in equity_api._rows(cur, BS_LINES_SQL):
        face.setdefault(r["doc_id"], {}).setdefault(r["basis"], {})[r["element"]] = r["value"]
    names = {}
    for r in equity_api._rows(cur, NAMES_SQL):
        names[r["edinet_code"]] = r
    rows = []
    for doc_id, head in heads.items():
        n = names.get(head["edinet_code"]) or {}
        head = dict(head, industry=n.get("industry"))
        row = compute_row(head, summary.get(doc_id, []), bs.get(doc_id, {}), face.get(doc_id, {}))
        row["filer_name_en"] = n.get("name_en")
        row["industry"] = n.get("industry")
        rows.append(row)
    rows.sort(key=lambda r: r["sec_code"])
    with _LOCK:
        _MEMO["version"], _MEMO["rows"] = version, rows
    return rows
