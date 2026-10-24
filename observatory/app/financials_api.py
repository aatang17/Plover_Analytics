# -*- coding: utf-8 -*-
"""Equity product API — financial statements and key indicators (有価証券報告書).

Every tagged number in a company's annual securities report, served two ways:

  1. **The filer's own five-year summary** (主要な経営指標等の推移): revenue,
     profits, net assets, total assets, per-share figures, equity ratio, ROE,
     PER, the three cash-flow totals, cash, dividends, payout, employees. Each
     filing restates five fiscal years, so successive filings overlap; the
     panel takes each fiscal year from the latest filing that covers it and
     says which one, and `?as_filed_in=` serves a single filing's five years
     unchanged — the point-in-time view.
  2. **The statements as filed** — balance sheet, income statement,
     comprehensive income, cash flows, changes in equity — consolidated and
     parent-only, each line in the order the filer's own presentation
     linkbase puts it, with the filer's Japanese label and, where the filer
     gave one, its English label.

Trust contract: every value is Official — exactly as tagged in the filing,
never recomputed, never rescaled except that ratios the taxonomy stores as
fractions (0.101) are served in percent (10.1) and say so in `calc`. A
missing value is null, never 0. Standardised field names (revenue, profit,
roe…) are a mapping from element names and each row lists the element behind
every field, so a reader can always see which filed line a number is.

Same DuckDB file and reader as the equity APIs next door; registered before
them so the literal /equity/financials/ paths win.
"""
import datetime
import math
import re

from fastapi import APIRouter, HTTPException, Query

from . import asof

from . import aliases
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/financials", tags=["Financials"])

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as tagged in each company's annual securities "
             "report (有価証券報告書) on EDINET — the filer's own five-year "
             "summary and its financial statements. Nothing is recomputed. "
             "Raw filings archived with SHA-256; doc_id links to the source."),
    "labels": ("Japanese labels are the filer's own (項目名). English labels "
               "are the filer's own where the filing carries one "
               "(label_en_source = filed); otherwise a readable form of the "
               "XBRL element name (label_en_source = derived), never a "
               "translation of the Japanese."),
    "credit": "Financial Services Agency of Japan, EDINET",
}

CALC = {
    "percent_fields": ("equity_ratio_pct, roe_pct, payout_ratio_pct: the "
                       "filed fraction × 100 (the taxonomy stores 0.101 for "
                       "10.1%)."),
    "fiscal_year_end": ("period end of the filing shifted by the row's "
                        "year_offset in whole years; a filer that changed its "
                        "fiscal year-end inside the window is labelled by "
                        "that arithmetic, not by its actual old year-end."),
    "panel": ("Each fiscal year is taken from the latest accepted filing "
              "that covers it (its source is on the row). Pass "
              "?as_filed_in=YYYY to read one filing's five years as first "
              "published."),
    "split_adjusted": ("Split-adjusted per-share figure = filed figure ÷ the "
                       "split ratios between that year and the latest, so every "
                       "year is on the latest year's share count. Each year's "
                       "share basis is read from its own figures (profit ÷ EPS, "
                       "else net assets ÷ BPS); a jump in it between two years "
                       "counts as a split only when the issued-share count shows "
                       "a split of the same ratio. A year whose basis cannot be "
                       "read that way is left blank. Dividends per share are "
                       "often printed as paid, on the old count, even where "
                       "EPS was restated; their divisor is read separately, "
                       "from the filer's payout ratio (dps ÷ eps against it)."),
    "revenue_fallback": ("Where no standard revenue element is tagged, "
                         "revenue is the first yen line of the filer's own "
                         "summary table (revenue_source = first_line)."),
}

STATEMENT_NAMES = {
    "summary": "Key indicators (主要な経営指標等の推移)",
    "bs": "Balance sheet",
    "pl": "Income statement",
    "ci": "Statement of comprehensive income",
    "ss": "Statement of changes in equity",
    "cf": "Cash flow statement",
}
BASES = ("consolidated", "parent")

# Standardised panel fields. Keyed by the element's "core" name: the local
# name with the SummaryOfBusinessResults suffix and any IFRS / USGAAP marker
# removed, so one entry covers a JGAAP element and its IFRS twin. Lists are
# in priority order — the first core present in a row wins.
FIELDS = [
    ("revenue", ["NetSales", "Revenue", "Revenues", "OperatingRevenue1",
                 "OperatingRevenue2", "OperatingRevenues", "NetOperatingRevenue",
                 "OrdinaryIncome", "OrdinaryIncomeBNK", "NetPremiumsWrittenINS",
                 "TotalNetRevenues", "SalesRevenue", "SalesRevenues"]),
    ("operating_income", ["OperatingIncome", "OperatingProfitLoss",
                          "OperatingIncomeLoss"]),
    ("ordinary_income", ["OrdinaryIncomeLoss", "OrdinaryIncomeLossBNK", "OrdinaryProfit"]),
    ("pretax_income", ["ProfitLossBeforeTax", "IncomeBeforeIncomeTaxes"]),
    # "Profit" last: a parent-only filer has no minorities and tags plain
    # 当期純利益, but a consolidated filer's attributable-to-owners element must
    # always win, so the bare name is only ever the final fallback.
    ("profit", ["ProfitLossAttributableToOwnersOfParent",
                "NetIncomeLossAttributableToOwnersOfParent", "NetIncomeLoss",
                "ProfitLoss", "Profit"]),
    ("comprehensive_income", ["ComprehensiveIncomeAttributableToOwnersOfParent",
                              "ComprehensiveIncome"]),
    ("net_assets", ["NetAssets", "EquityAttributableToOwnersOfParent",
                    "TotalEquity", "Equity", "ShareholdersEquity"]),
    ("total_assets", ["TotalAssets"]),
    ("bps", ["NetAssetsPerShare", "EquityAttributableToOwnersOfParentPerShare",
             "TotalEquityPerShare", "BookValuePerShare"]),
    ("eps", ["BasicEarningsLossPerShare", "BasicEarningsPerShare",
             "EarningsPerShare", "NetIncomeLossPerShare", "BasicNetIncomeLossPerShare",
             "BasicAndDilutedEarningsLossPerShare"]),
    ("eps_diluted", ["DilutedEarningsPerShare", "DilutedEarningsLossPerShare",
                     "DilutedNetIncomeLossPerShare"]),
    ("equity_ratio_pct", ["EquityToAssetRatio", "RatioOfOwnersEquityToGrossAssets",
                          "EquityRatio"]),
    ("roe_pct", ["RateOfReturnOnEquity", "ReturnOnEquity"]),
    ("per", ["PriceEarningsRatio"]),
    ("cf_operating", ["NetCashProvidedByUsedInOperatingActivities",
                      "CashFlowsFromUsedInOperatingActivities"]),
    ("cf_investing", ["NetCashProvidedByUsedInInvestingActivities",
                      "NetCashProvidedByUsedInInvestmentActivities",
                      "CashFlowsFromUsedInInvestingActivities"]),
    ("cf_financing", ["NetCashProvidedByUsedInFinancingActivities",
                      "CashFlowsFromUsedInFinancingActivities"]),
    ("cash", ["CashAndCashEquivalents"]),
    ("capital_stock", ["CapitalStock"]),
    ("issued_shares", ["TotalNumberOfIssuedShares",
                       "TotalNumberOfIssuedSharesTotalNumberOfIssuedShares"]),
    ("dps", ["DividendPaidPerShare"]),
    ("dps_interim", ["InterimDividendPaidPerShare"]),
    ("payout_ratio_pct", ["PayoutRatio"]),
    ("employees", ["NumberOfEmployees"]),
]
CORE_TO_FIELD = {}
CORE_RANK = {}
for _field, _cores in FIELDS:
    for _i, _core in enumerate(_cores):
        CORE_TO_FIELD.setdefault(_core, _field)
        CORE_RANK.setdefault(_core, _i)
PERCENT_FIELDS = {"equity_ratio_pct", "roe_pct", "payout_ratio_pct"}
FIELD_ORDER = [f for f, _ in FIELDS]

FIELD_LABELS = {
    "revenue": "Revenue", "operating_income": "Operating income",
    "ordinary_income": "Ordinary income", "pretax_income": "Profit before tax",
    "profit": "Profit attributable to owners",
    "comprehensive_income": "Comprehensive income", "net_assets": "Net assets",
    "total_assets": "Total assets", "bps": "Book value per share",
    "eps": "EPS (basic)", "eps_diluted": "EPS (diluted)",
    "equity_ratio_pct": "Equity ratio", "roe_pct": "Return on equity",
    "per": "PER (at year end)", "cf_operating": "Operating cash flow",
    "cf_investing": "Investing cash flow", "cf_financing": "Financing cash flow",
    "cash": "Cash and equivalents", "capital_stock": "Capital stock",
    "issued_shares": "Issued shares", "dps": "Dividend per share",
    "dps_interim": "Interim dividend per share", "payout_ratio_pct": "Payout ratio",
    "employees": "Employees",
}
FIELD_UNITS = {
    "bps": "yen", "eps": "yen", "eps_diluted": "yen", "dps": "yen",
    "dps_interim": "yen", "equity_ratio_pct": "%", "roe_pct": "%",
    "payout_ratio_pct": "%", "per": "x", "issued_shares": "shares",
    "employees": "people",
}


def core_of(element):
    """The element's name with the summary-table suffixes and the accounting
    standard markers removed. Filers' own summary elements use the suffix
    KeyFinancialData (Toyota, Sony) where the taxonomy uses
    SummaryOfBusinessResults; both are location, not meaning."""
    local = element.split(":")[-1]
    core = local.replace("SummaryOfBusinessResults", "").replace("KeyFinancialData", "")
    return core.replace("IFRS", "").replace("USGAAP", "")


def field_of(element, unit):
    """(field, rank) or (None, None). One taxonomy quirk handled here: the
    IFRS element named EquityToAssetRatio… is labelled and filed as book
    value per share, so the unit decides."""
    core = core_of(element)
    if core == "EquityToAssetRatio" and unit == "JPYPerShares":
        return "bps", 9
    f = CORE_TO_FIELD.get(core)
    return (f, CORE_RANK[core]) if f else (None, None)


# Suffixes the taxonomy hangs on element names to place them in a statement.
# They are location, not meaning, so they leave the derived English label.
_SUFFIXES = ("OpeCF", "InvCF", "FinCF", "BNK", "INS", "SEC", "PPE", "IOA",
             "SGA", "NOI", "NOE", "EI", "EL", "CA", "NCA", "CL", "NCL",
             "OCI", "IFRS", "USGAAP", "SummaryOfBusinessResults", "Abstract")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def derived_label(element):
    local = element.split(":")[-1]
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if len(local) > len(suf) + 2 and local.endswith(suf):
                local = local[:-len(suf)]
                changed = True
    words = _CAMEL.sub(" ", local).split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() or (i > 0 and w in ("Loss", "Income")):
            out.append(w if w.isupper() else w.lower())
        else:
            out.append(w if i == 0 else w.lower())
    text = " ".join(out)
    text = text.replace("profit loss", "profit (loss)").replace("Profit loss", "Profit (loss)")
    text = text.replace("gain loss", "gain (loss)").replace("income loss", "income (loss)")
    text = text.replace("increase decrease", "increase (decrease)")
    text = text.replace("decrease increase", "decrease (increase)")
    return text


def shift_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                      # 29 Feb
        return d.replace(year=d.year + years, day=28)


def _require():
    cur = _cur()
    got = _rows(cur, """
        SELECT table_name FROM information_schema.tables
        WHERE table_name IN ('eq_fin_filings','eq_fin_facts','eq_fin_lines','eq_fin_elements')""")
    if len(got) != 4:
        raise HTTPException(503, "financials dataset not published on this server yet")
    return cur


def _basis(b):
    b = (b or "").strip().lower()
    if b and b not in BASES:
        raise HTTPException(400, "basis must be consolidated or parent")
    return b or None


def _label_en(row):
    if row.get("label_en"):
        return row["label_en"], "filed"
    return derived_label(row["element"]), "derived"


# ---- filings ---------------------------------------------------------------
_FILINGS_SQL = """
    SELECT f.doc_id, f.edinet_code, f.sec_code, f.filer_name, f.period_end,
           f.filed_date, f.status, f.detail, f.accounting_standard, f.consolidated,
           f.facts, f.statements, f.sha256, f.parser_version
    FROM eq_fin_filings f
    WHERE f.sec_code = ? AND f.status IN ('clean','partial')/*ASOF*/
    ORDER BY f.period_end DESC, f.filed_date DESC, f.doc_id DESC
"""


def filings_sql():
    """This company's filings, with the point-in-time ceiling in force."""
    return _FILINGS_SQL.replace("/*ASOF*/", asof.clause("filed_date", "f"))


def _filings(cur, code):
    rows = _rows(cur, filings_sql(), [code])
    if not rows:
        raise HTTPException(404, "no extracted financial filing for %s" % code)
    return rows


def _header(cur, f):
    name = _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
        SELECT coalesce(n.name_en, s.name_en) AS name_en, e.industry
        FROM (SELECT ? AS edinet_code, ? AS sec_code) k
        LEFT JOIN eq_entities e ON e.edinet_code = k.edinet_code
        LEFT JOIN en_ecode n ON n.edinet_code = k.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = k.sec_code""",
        [f["edinet_code"], f["sec_code"]])
    h = {
        "sec_code": f["sec_code"], "edinet_code": f["edinet_code"],
        "filer_name": f["filer_name"],
        "filer_name_en": name[0]["name_en"] if name else None,
        "industry": name[0]["industry"] if name else None,
        "accounting_standard": f["accounting_standard"],
        "consolidated": f["consolidated"],
    }
    return h


def _statements_available(f):
    out = {}
    for part in (f.get("statements") or "").split(";"):
        if ":" in part:
            b, codes = part.split(":", 1)
            out[b] = [c for c in codes.split(",") if c]
    return out


# ---- the panel -------------------------------------------------------------
SUMMARY_FACTS_SQL = """
    SELECT l.basis, l.ord, l.element, l.depth, f.year_offset, f.unit, f.value,
           f.period_kind, e.label_ja, e.label_en
    FROM eq_fin_lines l
    JOIN eq_fin_facts f ON f.doc_id = l.doc_id AND f.element = l.element
                       AND f.basis = l.basis
    LEFT JOIN eq_fin_elements e ON e.element = l.element
    WHERE l.doc_id = ? AND l.statement = 'summary'
    ORDER BY l.basis, f.year_offset DESC, l.ord
"""
# Filings whose t1 package was unreadable still carry the summary facts by
# element name; the order is then the instance's.
SUMMARY_FALLBACK_SQL = """
    SELECT f.basis, f.ord, f.element, 1 AS depth, f.year_offset, f.unit, f.value,
           f.period_kind, e.label_ja, e.label_en
    FROM eq_fin_facts f LEFT JOIN eq_fin_elements e ON e.element = f.element
    WHERE f.doc_id = ? AND f.element LIKE '%SummaryOfBusinessResults'
    ORDER BY f.basis, f.year_offset DESC, f.ord
"""


def _panel_rows(cur, filing):
    """One filing -> {(basis, year_offset): row} of standardised fields plus
    every summary line as filed."""
    facts = _rows(cur, SUMMARY_FACTS_SQL, [filing["doc_id"]])
    if not facts:
        facts = _rows(cur, SUMMARY_FALLBACK_SQL, [filing["doc_id"]])
    rows = {}
    for r in facts:
        key = (r["basis"], r["year_offset"])
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "fiscal_year_end": shift_years(filing["period_end"], r["year_offset"]).isoformat(),
                "basis": r["basis"],
                "source": {"doc_id": filing["doc_id"],
                           "filed_date": filing["filed_date"].isoformat(),
                           "period_end": filing["period_end"].isoformat(),
                           "year_offset": r["year_offset"]},
                "values": {}, "elements": {}, "lines": [],
                "_rank": {},
            }
        en, en_src = _label_en(r)
        row["lines"].append({"element": r["element"], "label_ja": r["label_ja"],
                             "label_en": en, "label_en_source": en_src,
                             "unit": r["unit"], "value": r["value"]})
        field, rank = field_of(r["element"], r["unit"])
        if field is None:
            continue
        if field in row["_rank"] and row["_rank"][field] <= rank:
            continue
        v = r["value"]
        if field in PERCENT_FIELDS and v is not None:
            v = round(v * 100.0, 4)
        row["values"][field] = v
        row["elements"][field] = r["element"]
        row["_rank"][field] = rank
    for row in rows.values():
        # No standard revenue line: the summary's first yen flow is what
        # the filer leads with, and that is revenue by convention.
        if "revenue" not in row["values"]:
            for ln in row["lines"]:
                if ln["unit"] == "JPY" and ln["value"] is not None:
                    fact = next((f for f in facts if f["element"] == ln["element"]
                                 and f["basis"] == row["basis"]
                                 and f["year_offset"] == row["source"]["year_offset"]), None)
                    if fact and fact["period_kind"] == "duration":
                        row["values"]["revenue"] = ln["value"]
                        row["elements"]["revenue"] = ln["element"]
                        row["revenue_source"] = "first_line"
                        break
        row.pop("_rank", None)
    return rows


def build_panel(cur, filings, basis=None, as_filed_in=None, restated=None):
    """Fiscal-year rows across filings, latest filing winning each year.

    Pass a list as `restated` to collect, for every fiscal year two filings
    both print, the ratio of the older filing's EPS to the newer's — a
    filer's own record of a split it restated for."""
    chosen = filings
    if as_filed_in:
        chosen = [f for f in filings if str(f["period_end"].year) == str(as_filed_in)]
        if not chosen:
            raise HTTPException(404, "no accepted filing with fiscal year ending in %s"
                                % as_filed_in)
        # the year's latest period, as first published: a later correction is not
        chosen = [f for f in chosen if f["period_end"] == chosen[0]["period_end"]][-1:]
    panel = {}
    for f in chosen:
        for (b, off), row in _panel_rows(cur, f).items():
            if basis and b != basis:
                continue
            key = (row["fiscal_year_end"], b)
            if key not in panel:                 # latest filing already there
                panel[key] = row
            elif restated is not None:
                newer, older = panel[key]["values"].get("eps"), row["values"].get("eps")
                if newer and older and abs(newer) >= 1 and older / newer > 0:
                    restated.append(older / newer)
    # Per-share dividends are printed in the reporting company's own summary
    # table, not the group's; a consolidated row takes them from the parent
    # row of the same fiscal year and says so.
    for (fy, b), row in panel.items():
        if b != "consolidated":
            continue
        parent = panel.get((fy, "parent"))
        if not parent:
            continue
        for f in ("dps", "dps_interim", "payout_ratio_pct"):
            if row["values"].get(f) is None and parent["values"].get(f) is not None:
                row["values"][f] = parent["values"][f]
                row["elements"][f] = parent["elements"][f] + " (parent table)"
    rows = sorted(panel.values(), key=lambda r: (r["basis"], r["fiscal_year_end"]))
    return rows


# ---- split-adjusted per-share figures ----------------------------------------
# A filer restates per-share figures for a stock split only as far back as it
# chooses: Fast Retailing's 3-for-1 left FY2021 EPS on the old share count
# (¥1,663) beside FY2022 on the new (¥892), so the filed row reads as a 46%
# fall that never happened. The adjusted figures sit beside the filed ones,
# never in place of them.
PER_SHARE_FIELDS = ("eps", "eps_diluted", "bps", "dps", "dps_interim")


# Ratios Japanese companies actually split or consolidate by.
SPLIT_RATIOS = sorted(
    [float(n) for n in range(2, 11)] +
    [20.0, 25.0, 30.0, 40.0, 50.0, 100.0, 200.0, 250.0, 300.0, 400.0, 500.0, 1000.0] +
    [1.1, 1.2, 1.25, 4.0 / 3, 1.5, 2.5] +
    [1.0 / n for n in (2, 3, 4, 5, 10, 20, 50, 100)])


def _split_ratio(r):
    """The split or consolidation ratio issued shares moved by between two
    year ends, or None. A big ratio may carry a same-year cancellation or
    option exercise (Nippon Soda's 2-for-1 shows as 1.975), so 3% slack; a
    small one (1.1 to 1.5) must be exact to 0.2%, or a share issue would pass
    for a split (Demae-can's raises moved issued shares by 1.541)."""
    if r is None or r <= 0:
        return None
    for c in SPLIT_RATIOS:
        tol = 0.03 if (c >= 2 or c <= 0.5) else 0.002
        if abs(r - c) / c < tol:
            return c
    return None


def _split_ratio_exact(r):
    for c in SPLIT_RATIOS:
        if r and abs(r - c) / c < 0.01:
            return c
    return None


def _implied_shares(v):
    """The share count the filer divided by for this row: profit ÷ EPS, or
    net assets ÷ BPS where EPS is not usable."""
    for num, den in (("profit", "eps"), ("net_assets", "bps")):
        a, b = v.get(num), v.get(den)
        if a is not None and b and abs(b) >= 0.5 and a / b > 0:
            return a / b
    return None


def _products(ratios):
    out = {1.0}
    for r in ratios:
        out |= {round(c * r, 9) for c in out}
    return sorted(out)


def split_adjust(rows, restated=()):
    """Put split-adjusted per-share figures on every row, all on the share
    basis of the latest year. Returns the share-basis breaks found.

    Which share count a filed per-share figure uses depends on the filing it
    came from and how far back that filer chose to restate, so the basis is
    read from the figures themselves: the share count each row implies
    (profit ÷ EPS) is compared with the next year's. A jump between two years
    is a split only if it matches (within 12%) a split the issued-share count
    also shows at some point — two independent records of the same event. A
    jump that matches neither a split nor "no change" leaves every earlier
    year without an adjusted figure (null), never a guess."""
    shares = {}
    for r in rows:
        n = r["values"].get("issued_shares")
        if n:
            shares.setdefault(r["fiscal_year_end"], n)
    years = sorted(shares)
    events = []
    for a, b in zip(years, years[1:]):
        ratio = _split_ratio(shares[b] / shares[a])
        if ratio:
            events.append({"fiscal_year_end": b, "ratio": ratio,
                           "shares_before": shares[a], "shares_after": shares[b]})
    # The same fiscal year's EPS in an older and a newer filing: a clean
    # ratio between them (to 1% — both are the filer's own rounding of one
    # profit over one share count) is the filer restating for a split.
    restatements = sorted({c for c in (_split_ratio_exact(q) for q in restated) if c})
    if not events and not restatements:
        return []
    cands = _products(sorted({e["ratio"] for e in events} | set(restatements)))
    breaks = []
    for basis in sorted({r["basis"] for r in rows}):
        brows = sorted((r for r in rows if r["basis"] == basis), key=lambda r: r["fiscal_year_end"])
        implied = [(i, _implied_shares(r["values"])) for i, r in enumerate(brows)]
        implied = [(i, s) for i, s in implied if s]
        step = {}                      # index of the later row -> ratio, or None if unreadable
        for (i, s0), (j, s1) in zip(implied, implied[1:]):
            q = math.log(s1 / s0)
            dist = sorted((abs(q - math.log(c)), c) for c in cands)
            best = dist[0][1]
            gap = min(abs(math.log(c / best)) for c in cands if c != best) if len(cands) > 1 else 1.0
            ok = dist[0][0] < min(0.4 * gap, math.log(1.12))
            if not ok and abs(q) < math.log(1.5) and \
                    all(abs(q - math.log(c)) >= math.log(1.12) for c in cands if c != 1.0):
                # a buyback, a cancellation or a share issue: the count really
                # moved, and no split of that size is on record
                best, ok = 1.0, True
            if best != 1.0 or not ok:
                step[j] = best if ok else None
                breaks.append({"basis": basis, "from": brows[i]["fiscal_year_end"],
                               "to": brows[j]["fiscal_year_end"],
                               "implied_share_ratio": round(s1 / s0, 4),
                               "ratio": best if ok else None})
        first = implied[0][0] if implied else len(brows)
        last = implied[-1][0] if implied else -1
        for i, r in enumerate(brows):
            divisor = 1.0
            for j, ratio in step.items():
                if j > i:
                    divisor = None if (divisor is None or ratio is None) else divisor * ratio
            # a row after the last readable one has no evidence either way
            # rows outside the readable run have no evidence of their own;
            # a restatement ratio carries no date, so it could fall anywhere
            if i > last and (restatements or any(e["fiscal_year_end"] > r["fiscal_year_end"]
                                                 for e in events)):
                divisor = None
            if i < first and (restatements or any(
                    r["fiscal_year_end"] < e["fiscal_year_end"] <= brows[first]["fiscal_year_end"]
                    for e in events)):
                divisor = None
            r["split_adjusted"] = {"divisor": divisor}
    # Dividends per share are often printed as paid, on the share count of
    # the day, even where the filer restated EPS beside them. Their basis is
    # read from the filer's own payout ratio: dps ÷ eps equals the payout
    # ratio when both sit on one share count and is off by the split ratio
    # when they do not.
    by_key = dict(((r["basis"], r["fiscal_year_end"]), r) for r in rows)
    for r in rows:
        adj = r["split_adjusted"]
        fy = r["fiscal_year_end"]
        src = r
        if (r["elements"].get("dps") or "").endswith("(parent table)"):
            src = by_key.get(("parent", fy), r)
        latest = fy == max(x["fiscal_year_end"] for x in rows)
        if not restatements and not any(e["fiscal_year_end"] > fy for e in events):
            dps_div = 1.0
        else:
            v, eps_div = src["values"], src["split_adjusted"]["divisor"]
            dps, eps, pay = v.get("dps"), v.get("eps"), v.get("payout_ratio_pct")
            m = None
            if dps and eps and pay and eps > 0 and pay > 0:
                raw = (dps / eps) / (pay / 100.0)
                best = min(cands + [1.0], key=lambda c: abs(math.log(raw / c)))
                if abs(math.log(raw / best)) < math.log(1.05):
                    m = best
            if m is None and latest:
                m = 1.0                    # the latest year is the reference basis
            dps_div = None if (m is None or eps_div is None) else eps_div * m
        adj["dps_divisor"] = dps_div
        d = adj["divisor"]
        vals = {}
        for f in PER_SHARE_FIELDS:
            div = dps_div if f in ("dps", "dps_interim") else d
            x = r["values"].get(f)
            vals[f] = None if (x is None or div is None) else round(x / div, 4)
        adj["values"] = vals
        divs = [x for x in (d, dps_div) if x is not None]
        adj["status"] = ("undetermined" if d is None or dps_div is None and r["values"].get("dps")
                         else "adjusted" if any(x != 1.0 for x in divs) else "unchanged")
    return {"issued_share_splits": events, "restated_ratios": restatements,
            "basis_breaks": breaks}


@router.get("/company/{sec_code}")
def company(sec_code: str,
            basis: str = Query("", description="consolidated | parent; default both"),
            as_filed_in: str = Query("", description="fiscal year (YYYY) of one filing to read as first published")):  # noqa: E501
    """One company's key indicators across fiscal years, with provenance."""
    cur = _require()
    code = (sec_code or "").strip()
    filings = _filings(cur, code)
    b = _basis(basis)
    latest = filings[0]
    out = _header(cur, latest)
    out["latest_filing"] = {
        "doc_id": latest["doc_id"], "period_end": latest["period_end"].isoformat(),
        "filed_date": latest["filed_date"].isoformat(), "status": latest["status"],
        "detail": latest["detail"], "sha256": latest["sha256"],
        "parser_version": latest["parser_version"],
        "statements": _statements_available(latest),
    }
    out["filings"] = [{
        "doc_id": f["doc_id"], "period_end": f["period_end"].isoformat(),
        "filed_date": f["filed_date"].isoformat(), "status": f["status"],
        "detail": f["detail"], "accounting_standard": f["accounting_standard"],
    } for f in filings]
    restated = []
    out["panel"] = build_panel(cur, filings, b, as_filed_in.strip() or None, restated)
    out["splits"] = split_adjust(out["panel"], restated) or None
    out["fields"] = [{"field": f, "label": FIELD_LABELS[f], "unit": FIELD_UNITS.get(f, "yen")}
                     for f in FIELD_ORDER]
    out["calc"] = CALC
    out["provenance"] = PROVENANCE
    return out


# ---- statements ------------------------------------------------------------
STATEMENT_SQL = """
    SELECT l.ord, l.depth, l.element, l.label_role, e.label_ja, e.label_en,
           c.value AS current, c.unit AS unit, p.value AS prior
    FROM eq_fin_lines l
    LEFT JOIN eq_fin_elements e ON e.element = l.element
    LEFT JOIN eq_fin_facts c ON c.doc_id = l.doc_id AND c.element = l.element
                            AND c.basis = l.basis AND c.year_offset = 0
    LEFT JOIN eq_fin_facts p ON p.doc_id = l.doc_id AND p.element = l.element
                            AND p.basis = l.basis AND p.year_offset = -1
    WHERE l.doc_id = ? AND l.statement = ? AND l.basis = ?
    ORDER BY l.ord
"""


def _pick_filing(filings, year):
    y = (year or "").strip()
    if not y:
        return filings[0]
    for f in filings:
        if str(f["period_end"].year) == y:
            return f
    raise HTTPException(404, "no accepted filing with fiscal year ending in %s" % y)


@router.get("/statements/{sec_code}")
def statements(sec_code: str,
               statement: str = Query("bs", description="bs | pl | ci | cf | ss | summary"),
               basis: str = Query("", description="consolidated (default where filed) | parent"),
               year: str = Query("", description="fiscal year (YYYY) of the filing; default latest")):  # noqa: E501
    """One statement of one filing, every line as filed, current and prior year."""
    cur = _require()
    code = (sec_code or "").strip()
    st = (statement or "bs").strip().lower()
    if st not in STATEMENT_NAMES:
        raise HTTPException(400, "statement must be one of %s" % ", ".join(STATEMENT_NAMES))
    filings = _filings(cur, code)
    f = _pick_filing(filings, year)
    avail = _statements_available(f)
    b = _basis(basis)
    if not b:
        b = "consolidated" if st in avail.get("consolidated", []) else "parent"
    if st not in avail.get(b, []):
        raise HTTPException(404, "filing %s carries no %s %s statement (available: %s)"
                            % (f["doc_id"], b, st, avail or "none"))
    lines = _rows(cur, STATEMENT_SQL, [f["doc_id"], st, b])
    out = _header(cur, f)
    out.update({
        "statement": st, "statement_name": STATEMENT_NAMES[st], "basis": b,
        "doc_id": f["doc_id"], "period_end": f["period_end"].isoformat(),
        "prior_period_end": shift_years(f["period_end"], -1).isoformat(),
        "filed_date": f["filed_date"].isoformat(), "status": f["status"],
        "detail": f["detail"], "sha256": f["sha256"],
        "available": avail,
        "available_years": list(dict.fromkeys(str(x["period_end"].year) for x in filings)),
    })
    items = []
    for r in lines:
        en, en_src = _label_en(r)
        items.append({
            "ord": r["ord"], "depth": r["depth"], "element": r["element"],
            "label_ja": r["label_ja"], "label_en": en, "label_en_source": en_src,
            "is_heading": r["element"].endswith("Abstract"),
            "is_total": r["label_role"] == "totalLabel",
            "negated": bool(r["label_role"] and r["label_role"].startswith("negated")),
            "unit": r["unit"], "current": r["current"], "prior": r["prior"],
        })
    out["lines"] = items
    out["calc"] = {"note": ("Values exactly as tagged; a negated label role "
                            "means the filer prints the figure with its sign "
                            "reversed (a cost shown as a positive number). "
                            "Change columns, where a client shows them, are "
                            "current − prior on the tagged values.")}
    out["provenance"] = PROVENANCE
    return out


# ---- one element's history -------------------------------------------------
@router.get("/facts/{sec_code}")
def facts(sec_code: str,
          element: str = Query(..., description="element id, e.g. jppfs_cor:NetSales"),
          basis: str = Query("", description="consolidated | parent; default both")):
    """Every filed value of one element for one company, across filings and
    the years each filing restates — the raw panel a model is built on."""
    cur = _require()
    code = (sec_code or "").strip()
    b = _basis(basis)
    rows = _rows(cur, """
        SELECT f.doc_id, g.period_end, g.filed_date, f.basis, f.year_offset,
               f.period_kind, f.unit, f.value
        FROM eq_fin_facts f JOIN eq_fin_filings g USING (doc_id)
        WHERE g.sec_code = ? AND g.status IN ('clean','partial')""" + asof.clause("filed_date", "g") + """ AND f.element = ?
          AND (CAST(? AS VARCHAR) IS NULL OR f.basis = ?)
        ORDER BY f.basis, g.period_end DESC, g.filed_date DESC, f.year_offset DESC""",
        [code, element.strip(), b, b])
    if not rows:
        raise HTTPException(404, "no filed value of %s for %s" % (element, code))
    lab = _rows(cur, "SELECT element, label_ja, label_en FROM eq_fin_elements WHERE element = ?",
                [element.strip()])
    en, en_src = _label_en(lab[0] if lab else {"element": element, "label_en": None})
    for r in rows:
        r["fiscal_year_end"] = shift_years(r["period_end"], r["year_offset"]).isoformat()
        r["period_end"] = r["period_end"].isoformat()
        r["filed_date"] = r["filed_date"].isoformat()
    return {"sec_code": code, "element": element,
            "label_ja": lab[0]["label_ja"] if lab else None,
            "label_en": en, "label_en_source": en_src,
            "values": rows, "provenance": PROVENANCE}


@router.get("/elements")
def elements(q: str = Query("", description="substring of the element id or Japanese/English label"),
             limit: int = Query(50, ge=1, le=500)):
    """The element dictionary: what has been tagged, and by how many filings."""
    cur = _require()
    like = "%" + q.strip() + "%"
    rows = _rows(cur, """
        SELECT e.element, e.namespace, e.label_ja, e.label_en,
               count(DISTINCT f.doc_id) AS filings
        FROM eq_fin_elements e LEFT JOIN eq_fin_facts f ON f.element = e.element
        WHERE e.element ILIKE ? OR coalesce(e.label_ja,'') LIKE ? OR coalesce(e.label_en,'') ILIKE ?
        GROUP BY 1,2,3,4 ORDER BY filings DESC, e.element LIMIT ?""",
        [like, like, like, limit])
    for r in rows:
        r["label_en"], r["label_en_source"] = _label_en(r)
    return {"elements": rows}


# ---- cross-section ---------------------------------------------------------
SCREEN_METRICS = {
    "revenue": ("Revenue (¥)", "desc"),
    "profit": ("Profit attributable to owners (¥)", "desc"),
    "total_assets": ("Total assets (¥)", "desc"),
    "net_assets": ("Net assets (¥)", "desc"),
    "roe_pct": ("Return on equity (%)", "desc"),
    "equity_ratio_pct": ("Equity ratio (%)", "desc"),
    "payout_ratio_pct": ("Payout ratio (%)", "desc"),
    "cf_operating": ("Operating cash flow (¥)", "desc"),
    "cash": ("Cash and equivalents (¥)", "desc"),
    "employees": ("Employees", "desc"),
}

# Latest accepted filing per company, its current-year summary lines, the
# standardised field for each, best-ranked element per field. The consolidated
# row is used where the filing has one; parent-only filers fall back.
_SCREEN_SQL = """
    WITH latest AS (
        SELECT * FROM (
            SELECT f.*, row_number() OVER (PARTITION BY sec_code
                                           ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM eq_fin_filings f
            WHERE sec_code IS NOT NULL AND status IN ('clean','partial')/*ASOF*/
              AND (CAST(? AS VARCHAR) IS NULL OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
        ) WHERE rn = 1),
    cur AS (
        SELECT l.doc_id, l.basis, l.element, l.ord, f.unit, f.value, f.period_kind
        FROM eq_fin_lines l JOIN latest g USING (doc_id)
        JOIN eq_fin_facts f ON f.doc_id = l.doc_id AND f.element = l.element
                           AND f.basis = l.basis AND f.year_offset = 0
        WHERE l.statement = 'summary'),
    pick AS (
        SELECT doc_id, CASE WHEN bool_or(basis = 'consolidated') THEN 'consolidated' ELSE 'parent' END AS basis
        FROM cur GROUP BY doc_id)
    SELECT g.sec_code, g.filer_name, g.edinet_code, g.doc_id, g.period_end, g.filed_date,
           g.accounting_standard, g.status, p.basis, c.element, c.unit, c.value,
           c.ord, c.period_kind
    FROM latest g JOIN pick p USING (doc_id)
    JOIN cur c ON c.doc_id = g.doc_id AND c.basis = p.basis
    ORDER BY g.doc_id, c.ord
"""



def screen_sql():
    """The cross-section, with the point-in-time ceiling in force."""
    return _SCREEN_SQL.replace("/*ASOF*/", asof.clause("filed_date"))


@router.get("/screen")
def screen(metric: str = Query("revenue", description="one of /screen/metrics"),
           year: str = Query("", description="fiscal year of the filing; default each company's latest"),
           limit: int = Query(50, ge=1, le=500)):
    """Ranked cross-section on one key indicator, one filing per company."""
    cur = _require()
    m = (metric or "revenue").strip()
    if m not in SCREEN_METRICS:
        raise HTTPException(400, "metric must be one of %s" % ", ".join(SCREEN_METRICS))
    y = (year or "").strip() or None
    rows = _rows(cur, screen_sql(), [y, y])
    by_doc = {}
    for r in rows:
        d = by_doc.setdefault(r["doc_id"], {
            "sec_code": r["sec_code"], "filer_name": r["filer_name"],
            "edinet_code": r["edinet_code"], "doc_id": r["doc_id"],
            "period_end": r["period_end"].isoformat(), "filed_date": r["filed_date"].isoformat(),
            "accounting_standard": r["accounting_standard"], "status": r["status"],
            "basis": r["basis"], "values": {}, "elements": {}, "_rank": {}})
        field, rank = field_of(r["element"], r["unit"])
        if field is None:
            # Same fallback as the panel: the summary's first yen flow is
            # revenue when no standard revenue element is tagged.
            if (r["unit"] == "JPY" and r["period_kind"] == "duration"
                    and r["value"] is not None and "_first" not in d):
                d["_first"] = (r["element"], r["value"])
            continue
        if field in d["_rank"] and d["_rank"][field] <= rank:
            continue
        v = r["value"]
        if field in PERCENT_FIELDS and v is not None:
            v = round(v * 100.0, 4)
        d["values"][field] = v
        d["elements"][field] = r["element"]
        d["_rank"][field] = rank
    for d in by_doc.values():
        first = d.pop("_first", None)
        if "revenue" not in d["values"] and first:
            d["values"]["revenue"], d["elements"]["revenue"] = first[1], first[0]
            d["revenue_source"] = "first_line"
    ranked = [d for d in by_doc.values() if d["values"].get(m) is not None]
    ranked.sort(key=lambda d: d["values"][m], reverse=True)
    names = {}
    if ranked:
        codes = [d["sec_code"] for d in ranked[:limit]]
        for n in _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """
            SELECT sec_code, name_en FROM en_scode WHERE sec_code IN (%s)"""
                       % ",".join("?" * len(codes)), codes):
            names[n["sec_code"]] = n["name_en"]
    out = []
    for i, d in enumerate(ranked[:limit], 1):
        d.pop("_rank", None)
        d["rank"] = i
        d["filer_name_en"] = names.get(d["sec_code"])
        d["metric_value"] = d["values"][m]
        out.append(d)
    return {"metric": m, "metric_label": SCREEN_METRICS[m][0], "year": y,
            "companies_ranked": len(ranked), "rows": out,
            "calc": CALC, "provenance": PROVENANCE}


@router.get("/screen/metrics")
def screen_metrics():
    """The filed metrics /screen accepts, each with its label."""
    return {"metrics": [{"metric": k, "label": v[0]} for k, v in SCREEN_METRICS.items()]}


# ---- coverage --------------------------------------------------------------
@router.get("/summary")
def summary():
    """What the dataset holds — filings by status, companies, fiscal years."""
    cur = _require()
    status = _rows(cur, "SELECT status, count(*) AS n FROM eq_fin_filings GROUP BY 1 ORDER BY 1")
    years = _rows(cur, """
        SELECT CAST(year(f.period_end) AS VARCHAR) AS year, count(*) AS filings,
               count(DISTINCT f.sec_code) AS companies
        FROM eq_fin_filings f WHERE f.status IN ('clean','partial')""" + asof.newest(
            "f", "eq_fin_filings", ok="newer.status IN ('clean','partial')") + """
        GROUP BY 1 ORDER BY 1 DESC""")
    totals = _rows(cur, """
        SELECT (SELECT count(*) FROM eq_fin_facts) AS facts,
               (SELECT count(*) FROM eq_fin_lines) AS lines,
               (SELECT count(*) FROM eq_fin_elements) AS elements,
               (SELECT count(DISTINCT sec_code) FROM eq_fin_filings
                 WHERE status IN ('clean','partial')) AS companies,
               (SELECT max(filed_date) FROM eq_fin_filings) AS latest_filed""")[0]
    stds = _rows(cur, """
        SELECT f.accounting_standard, count(*) AS n FROM eq_fin_filings f
        WHERE f.status IN ('clean','partial')""" + asof.newest(
            "f", "eq_fin_filings", ok="newer.status IN ('clean','partial')") + """
        GROUP BY 1 ORDER BY 2 DESC""")
    if totals.get("latest_filed"):
        totals["latest_filed"] = totals["latest_filed"].isoformat()
    return {"status": status, "years": years, "totals": totals,
            "accounting_standards": stds, "provenance": PROVENANCE}


@router.get("/companies")
def companies(q: str = Query("", description="name or code substring"),
              limit: int = Query(25, ge=1, le=100)):
    """Search box feed: companies with an accepted financial filing."""
    cur = _require()
    like = "%" + q.strip() + "%"
    alias_sql, alias_params = aliases.clause(cur, "l.sec_code", q)
    return {"companies": _rows(cur, "WITH x AS (SELECT 1)" + NAME_CTES + """,
        latest AS (
            SELECT sec_code, max(filer_name) AS name, max(period_end) AS period_end,
                   max(edinet_code) AS edinet_code,
                   count(DISTINCT period_end) AS filings  -- a correction is not another year
            FROM eq_fin_filings WHERE sec_code IS NOT NULL AND status IN ('clean','partial')
            GROUP BY 1)
        SELECT l.sec_code, l.name, coalesce(n.name_en, s.name_en) AS name_en,
               l.period_end, l.filings, e.industry
        FROM latest l
        LEFT JOIN en_ecode n ON n.edinet_code = l.edinet_code
        LEFT JOIN en_scode s ON s.sec_code = l.sec_code
        LEFT JOIN eq_entities e ON e.edinet_code = l.edinet_code
        WHERE l.sec_code LIKE ? OR l.name LIKE ?
           OR lower(coalesce(n.name_en, s.name_en, '')) LIKE lower(?)"""
        + alias_sql + """
        ORDER BY l.filings DESC, l.sec_code LIMIT ?""",
        [like, like, like] + alias_params + [limit])}


# ---- derived ratios and the screener ----------------------------------------
from . import equity_api  # noqa: E402
from . import fin_metrics  # noqa: E402
from .equity_api import INDUSTRY_EN  # noqa: E402

DERIVED_PROVENANCE = {
    "trust": "derived",
    "note": ("Calculated on this platform from the filed statements and the "
             "filer's own summary; every metric names its formula and inputs. "
             "The filer's own ROE and equity ratio are returned beside ours "
             "with the difference, so the two can be reconciled. Ratios that "
             "involve a price are implied from the filer's year-end PER — this "
             "platform has no market data."),
    "credit": "Financial Services Agency of Japan, EDINET (inputs)",
}


def _metric_defs():
    return [{"metric": k, "label": fin_metrics.METRIC_LABELS[k][0],
             "unit": fin_metrics.METRIC_LABELS[k][1], "formula": fin_metrics.FORMULAS[k]}
            for k in fin_metrics.METRIC_ORDER]


@router.get("/metrics/{sec_code}")
def metrics(sec_code: str):
    """One company's platform-calculated ratios with formula, inputs and the
    filer's own figure where it prints one."""
    cur = _require()
    code = (sec_code or "").strip()
    row = next((r for r in fin_metrics.all_rows(cur) if r["sec_code"] == code), None)
    if row is None:
        raise HTTPException(404, "no accepted financial filing for %s" % code)
    out = dict(row)
    out["industry_en"] = INDUSTRY_EN.get(row.get("industry"))
    out["metric_defs"] = _metric_defs()
    out["calc"] = fin_metrics.FORMULAS
    out["provenance"] = DERIVED_PROVENANCE
    return out


SCREEN_SORTABLE = set(fin_metrics.METRIC_ORDER) | set(fin_metrics.SIZE_FIELDS)


def _num(v, name):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(400, "%s must be a number" % name)


@router.get("/screener", openapi_extra={"x-example": "/api/v1/equity/financials/screener?sort=roe_pct&limit=20"})
def screener(industry: str = Query("", description="EDINET industry (Japanese), e.g. 銀行業; omit for all"),
             standard: str = Query("", description="Japan GAAP | IFRS | US GAAP"),
             min_revenue_yen: str = Query(""), min_assets_yen: str = Query(""),
             roe_min: str = Query(""), roe_max: str = Query(""),
             roa_min: str = Query(""), operating_margin_min: str = Query(""),
             equity_ratio_min: str = Query(""), equity_ratio_max: str = Query(""),
             revenue_growth_min: str = Query(""), pbr_implied_max: str = Query(""),
             dividend_yield_min: str = Query(""), cash_to_assets_min: str = Query(""),
             net_cash_to_assets_min: str = Query(""),
             cohort: str = Query("", description="restrict to a cohort, e.g. size:core30, "
                                                 "ind33:3650 or codes:7203,6758 — "
                                                 "see /api/v1/equity/cohorts"),
             sort: str = Query("roe_pct", description="metric or size field to rank on"),
             order: str = Query("desc", description="desc | asc"),
             limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Cross-section of every company's latest filing on platform-calculated
    ratios, filtered and ranked. Companies missing the sort metric are
    excluded from the ranking and counted in `excluded_missing_sort`.

    ?cohort= narrows the universe to a peer group before anything is ranked,
    so a rank is a rank inside that group, not inside the market."""
    cur = _require()
    rows = fin_metrics.all_rows(cur)
    codes, cohort_info = equity_api.cohort_filter(cohort)
    if sort not in SCREEN_SORTABLE:
        raise HTTPException(400, "sort must be one of %s" % ", ".join(sorted(SCREEN_SORTABLE)))
    asc = (order or "desc").lower() == "asc"
    ind = industry.strip() or None
    std = standard.strip() or None
    f = {k: _num(v, k) for k, v in (
        ("min_revenue_yen", min_revenue_yen), ("min_assets_yen", min_assets_yen),
        ("roe_min", roe_min), ("roe_max", roe_max), ("roa_min", roa_min),
        ("operating_margin_min", operating_margin_min), ("equity_ratio_min", equity_ratio_min),
        ("equity_ratio_max", equity_ratio_max), ("revenue_growth_min", revenue_growth_min),
        ("pbr_implied_max", pbr_implied_max), ("dividend_yield_min", dividend_yield_min),
        ("cash_to_assets_min", cash_to_assets_min),
        ("net_cash_to_assets_min", net_cash_to_assets_min))}

    def ge(val, floor):
        return floor is None or (val is not None and val >= floor)

    def le(val, cap):
        return cap is None or (val is not None and val <= cap)

    kept = []
    for r in rows:
        m, s = r["metrics"], r["size"]
        if codes is not None and r.get("sec_code") not in codes:
            continue
        if ind and r.get("industry") != ind:
            continue
        if std and (r.get("accounting_standard") or "") != std:
            continue
        if not (ge(s["revenue_yen"], f["min_revenue_yen"]) and ge(s["total_assets_yen"], f["min_assets_yen"])
                and ge(m["roe_pct"], f["roe_min"]) and le(m["roe_pct"], f["roe_max"])
                and ge(m["roa_pct"], f["roa_min"]) and ge(m["operating_margin_pct"], f["operating_margin_min"])
                and ge(m["equity_ratio_pct"], f["equity_ratio_min"]) and le(m["equity_ratio_pct"], f["equity_ratio_max"])
                and ge(m["revenue_growth_pct"], f["revenue_growth_min"])
                and le(m["pbr_implied_x"], f["pbr_implied_max"])
                and ge(m["dividend_yield_implied_pct"], f["dividend_yield_min"])
                and ge(m["cash_to_assets_pct"], f["cash_to_assets_min"])
                and ge(m["net_cash_to_assets_pct"], f["net_cash_to_assets_min"])):
            continue
        kept.append(r)

    def key(r):
        return r["metrics"].get(sort) if sort in r["metrics"] else r["size"].get(sort)
    ranked = [r for r in kept if key(r) is not None]
    missing = len(kept) - len(ranked)
    ranked.sort(key=key, reverse=not asc)
    page = []
    for i, r in enumerate(ranked[offset:offset + limit], offset + 1):
        page.append({
            "rank": i, "sec_code": r["sec_code"], "filer_name": r["filer_name"],
            "filer_name_en": r.get("filer_name_en"), "industry": r.get("industry"),
            "industry_en": INDUSTRY_EN.get(r.get("industry")),
            "accounting_standard": r["accounting_standard"], "basis": r["basis"],
            "status": r["status"], "doc_id": r["doc_id"], "period_end": r["period_end"],
            "size": r["size"], "metrics": r["metrics"], "filed": r["filed"], "checks": r["checks"],
            "flags": r.get("flags") or [],
            "sort_value": key(r),
        })
    universe = len(rows) if codes is None else sum(
        1 for r in rows if r.get("sec_code") in codes)
    return {"sort": sort, "order": "asc" if asc else "desc",
            "filters": {"industry": ind, "standard": std, "cohort": cohort.strip() or None,
                        **{k: v for k, v in f.items() if v is not None}},
            "cohort": cohort_info and {k: cohort_info[k] for k in
                                       ("spec", "label", "as_of", "public", "kind")},
            "cohort_note": None if cohort_info is None else (
                "Ranked within %s — %d of its members have an extracted filing."
                % (cohort_info["label"], universe)),
            "universe": universe, "matched": len(kept), "ranked": len(ranked),
            "excluded_missing_sort": missing, "offset": offset, "limit": limit,
            "rows": page, "metric_defs": _metric_defs(), "calc": fin_metrics.FORMULAS,
            "provenance": DERIVED_PROVENANCE}


@router.get("/screener/options")
def screener_options():
    """What the screener can filter and sort on, with universe counts."""
    cur = _require()
    rows = fin_metrics.all_rows(cur)
    by_ind = {}
    by_std = {}
    for r in rows:
        by_ind[r.get("industry")] = by_ind.get(r.get("industry"), 0) + 1
        by_std[r.get("accounting_standard")] = by_std.get(r.get("accounting_standard"), 0) + 1
    return {
        "universe": len(rows),
        "industries": sorted([{"industry": k, "industry_en": INDUSTRY_EN.get(k), "companies": v}
                              for k, v in by_ind.items() if k], key=lambda x: -x["companies"]),
        "standards": sorted([{"standard": k, "companies": v} for k, v in by_std.items() if k],
                            key=lambda x: -x["companies"]),
        "metrics": _metric_defs(),
        "size_fields": fin_metrics.SIZE_FIELDS,
        "provenance": DERIVED_PROVENANCE,
    }


# The dataset's card (app/registry.py). Everything under /financials is as
# filed; the ratios are the one computed layer (fin_metrics) and each carries
# the formula the screener shows.
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

_METRIC_UNIT = {"%": "%", "×": "x", "¥": "JPY"}

MANIFEST = {
    "id": "financials",
    "section": "financials",
    "name": {"en": "Financial statements and ratios",
             "ja": "経営指標等の推移・財務諸表"},
    "shape": "company",
    "summary": ("Every tagged number in a company's annual securities report — "
                "the filer's own five-year summary and the statements as filed "
                "— plus platform-calculated ratios (ROE, margins, growth, "
                "implied PBR) with formula and inputs on every row."),
    "source": dict(_EDINET_SOURCE,
                   document="有価証券報告書 · 主要な経営指標等の推移 / 財務諸表 (annual "
                            "securities report, XBRL facts)",
                   credit="Source: company filings on EDINET (Financial Services "
                          "Agency of Japan)."),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2021 (each filing restates five years; filings from FY2025)",
        "stale_after_days": None,
    },
    "measures": [
        {"id": "revenue_yen", "label": "Revenue", "unit": "JPY", "trust": "official"},
        {"id": "profit_yen", "label": "Profit attributable to owners", "unit": "JPY",
         "trust": "official"},
        {"id": "total_assets_yen", "label": "Total assets", "unit": "JPY", "trust": "official"},
        {"id": "net_assets_yen", "label": "Net assets", "unit": "JPY", "trust": "official"},
        {"id": "cf_operating_yen", "label": "Operating cash flow", "unit": "JPY",
         "trust": "official"},
        {"id": "cash_yen", "label": "Cash and equivalents", "unit": "JPY", "trust": "official"},
        {"id": "eps", "label": "Earnings per share", "unit": "JPY", "trust": "official"},
        {"id": "bps", "label": "Book value per share", "unit": "JPY", "trust": "official"},
        {"id": "dps", "label": "Dividend per share", "unit": "JPY", "trust": "official"},
        {"id": "per", "label": "Price-earnings ratio at year end, as filed", "unit": "x",
         "trust": "official"},
        {"id": "employees", "label": "Employees", "unit": "count", "trust": "official"},
        {"id": "equity_ratio_pct_filed", "label": "Equity ratio, as filed", "unit": "%",
         "trust": "official"},
        {"id": "roe_pct_filed", "label": "Return on equity, as filed", "unit": "%",
         "trust": "official"},
        {"id": "payout_ratio_pct", "label": "Payout ratio, as filed", "unit": "%",
         "trust": "official"},
        {"id": "statement_line", "label": "Any statement line, in the filer's own order and label",
         "unit": "JPY", "trust": "official"},
    ] + [
        {"id": k, "label": fin_metrics.METRIC_LABELS[k][0],
         "unit": _METRIC_UNIT[fin_metrics.METRIC_LABELS[k][1]],
         "trust": "derived", "calc": fin_metrics.FORMULAS[k]}
        for k in fin_metrics.METRIC_ORDER
    ] + [
        {"id": "equity_owners_yen", "label": "Equity attributable to owners (standardised)",
         "unit": "JPY", "trust": "derived", "calc": fin_metrics.FORMULAS["equity_owners_yen"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/financials/company/{sec_code}",
        "statements": "/api/v1/equity/financials/statements/{sec_code}",
        "facts": "/api/v1/equity/financials/facts/{sec_code}",
        "metrics": "/api/v1/equity/financials/metrics/{sec_code}",
        "search": "/api/v1/equity/financials/companies",
        "summary": "/api/v1/equity/financials/summary",
        "screen": "/api/v1/equity/financials/screener",
        "screen_options": "/api/v1/equity/financials/screener/options",
        "filed_screen": "/api/v1/equity/financials/screen",
        "filed_screen_metrics": "/api/v1/equity/financials/screen/metrics",
        "elements": "/api/v1/equity/financials/elements",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": (
        [{"id": k, "title": "Companies ranked on %s (platform-calculated)"
                            % fin_metrics.METRIC_LABELS[k][0]}
         for k in fin_metrics.METRIC_ORDER]
        + [{"id": "filed:" + k, "title": "Companies ranked on %s, as filed" % v[0]}
           for k, v in sorted(SCREEN_METRICS.items())]),
    "cite": "/financials.html?c={sec_code}",
    "page": "/financials.html",
    "notes": [PROVENANCE["note"], PROVENANCE["labels"], CALC["percent_fields"],
              CALC["panel"], DERIVED_PROVENANCE["note"]],
}
