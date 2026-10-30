# -*- coding: utf-8 -*-
"""US financial statements from SEC filings — the comparison shelf, queryable.

The Japanese financials next door (financials_api.py) come from EDINET XBRL.
This module serves the same three views over US periodic reports — 10-K,
10-Q, 20-F, 40-F — loaded by equity/sec_extract.py from the SEC's own
Financial Statement Data Sets:

  1. **Key indicators per filing** (`/company/{cik}`): revenue, operating
     and net income, EPS, total assets, liabilities, equity, cash, the three
     cash-flow totals, capex, dividends and buybacks paid, shares
     outstanding — each standardised field mapped from a short, ordered list
     of us-gaap tags, and every row names the tag it used. A filing that
     tags none of a field's candidates has that field null.
  2. **The statements as filed** (`/statements/{cik}`): balance sheet,
     income statement, cash flows, comprehensive income, equity — each line
     in the filer's own order with the filer's own label, the standard
     taxonomy label, and the value for the filing's period beside the
     comparative the filer tagged.
  3. **One tag's history** (`/facts/{cik}`): every filed value of one
     element for one company across its filings — the raw panel.

Trust contract: every value is Official — exactly as the SEC extracted it
from the filing, never recomputed, never rescaled; the unit is on every row
(USD, shares, pure, or a foreign currency for some 20-F filers). Missing is
null, never 0; the SEC's nil facts were dropped at load. Every filing
carries this platform's balance-sheet verdict (assets = liabilities + equity
on the filing's own period end) as `status` and `bs_check`.

Identity is the SEC's Central Index Key (CIK). On the generic surfaces that
share a code namespace with the Japanese datasets (/api/v1/company/{code},
the MCP get_company tool) a CIK is written `cik:320193`, so a four-digit
Japanese securities code can never be read as a US filer that happens to
have the same number.

Own DuckDB file (data/sec.duckdb): a quarter of facts is ~140MB and the
equity file ships inside the image as a seed. Read-only reader, reopened
when the file changes underneath (a backfill swaps a new file in).
"""
import datetime
import os
import pathlib
import re
import threading

import duckdb
from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import db

router = APIRouter(prefix="/api/v1/us/financials", tags=["US financials"])

DB_PATH = pathlib.Path(os.environ.get(
    "SEC_DB_PATH",
    str(pathlib.Path(__file__).resolve().parent.parent / "data" / "sec.duckdb")))

_READER = None
_READER_VERSION = None
_LOCK = threading.Lock()

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as the SEC extracted them from each company's "
             "XBRL filing (Financial Statement Data Sets, Division of "
             "Economic and Risk Analysis). Nothing is recomputed. Each "
             "quarterly data set is archived with its SHA-256; adsh is the "
             "SEC accession number of the source filing."),
    "labels": ("plabel is the filer's own line label; tlabel is the standard "
               "taxonomy label for the tag (custom = the filer's own "
               "extension element)."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR.",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh_nodash}/",
}

CALC = {
    "standard_fields": ("Each key-indicator field is the first of a short list "
                        "of us-gaap tags the filing carries for its own period "
                        "(instant on the period end; duration over the "
                        "filing's own span); the tag used is on the row."),
    "comparative": ("`prior` on a statement line is the most recent earlier "
                    "value the filer tagged for the same tag and span — the "
                    "comparative column of the filing, not a platform "
                    "calculation."),
    "balance_check": ("status/bs_check: Assets against "
                      "LiabilitiesAndStockholdersEquity (or Liabilities + "
                      "StockholdersEquity) on the period end, tolerance one "
                      "basis point of assets. A verdict, never an adjustment."),
}

STATEMENTS = {
    "BS": "Balance sheet",
    "IS": "Income statement",
    "CF": "Cash flow statement",
    "CI": "Comprehensive income",
    "EQ": "Stockholders' equity",
    "UN": "Unclassifiable",
    "CP": "Cover page",
    "SI": "Schedule of investments",
}
PRIMARY = ("BS", "IS", "CF", "CI", "EQ")
ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A")
QUARTERLY_FORMS = ("10-Q", "10-Q/A", "10-QT", "10-QT/A")
FORM_FAMILY = {"10-K": ANNUAL_FORMS, "annual": ANNUAL_FORMS,
               "10-Q": QUARTERLY_FORMS, "quarterly": QUARTERLY_FORMS}

# (field, kind, [tags in priority order]). Instants are read on the filing's
# period end; durations over the filing's own span (4 quarters for an annual
# report, the quarter itself for a 10-Q).
FIELDS = [
    ("revenue", "duration",
     ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
      "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
      "SalesRevenueGoodsNet", "TotalRevenuesAndOtherIncome",
      "InterestAndDividendIncomeOperating",
      # IFRS filers (20-F): Shell, Novo Nordisk and Toyota tag "Revenue", TSMC "RevenueFromContractsWithCustomers"
      "Revenue", "RevenueFromContractsWithCustomers"]),
    ("operating_income", "duration", ["OperatingIncomeLoss"]),
    ("pretax_income", "duration",
     ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("net_income", "duration",
     ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]),
    ("eps_basic", "duration", ["EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"]),
    ("eps_diluted", "duration", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]),
    ("total_assets", "instant", ["Assets"]),
    ("total_liabilities", "instant", ["Liabilities"]),
    ("equity", "instant",
     ["StockholdersEquity",
      "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("cash", "instant",
     ["CashAndCashEquivalentsAtCarryingValue",
      "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("cf_operating", "duration", ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"]),
    ("cf_investing", "duration", ["NetCashProvidedByUsedInInvestingActivities"]),
    ("cf_financing", "duration", ["NetCashProvidedByUsedInFinancingActivities"]),
    # Amazon files its capital spending as "productive assets"; without the second tag its free cash flow was blank
    ("capex", "duration", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    # dividends to common shareholders first: the all-in line also carries preferred dividends
    ("dividends_paid", "duration", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
    ("buybacks", "duration", ["PaymentsForRepurchaseOfCommonStock"]),
    # the parts of a bank's or a landlord's revenue (sec_metrics.composite_revenue)
    ("interest_income", "duration", ["InterestAndDividendIncomeOperating"]),
    ("noninterest_income", "duration", ["NoninterestIncome"]),
    ("lease_income", "duration", ["OperatingLeaseLeaseIncome"]),
    ("shares_outstanding", "instant",
     ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]),
]
FIELD_TAGS = sorted({t for _, _, tags in FIELDS for t in tags})
SCREEN_FIELDS = ("revenue", "operating_income", "net_income", "total_assets",
                 "equity", "cash", "cf_operating", "capex", "buybacks", "dividends_paid")

# How far behind the calendar the newest loaded quarter may be. The SEC posts
# a quarter's data set in the first days after the quarter ends, so the set
# for the quarter ending 30 June is on the shelf by early July and the next
# one by early October; 130 days past the newest loaded quarter's end means
# a whole later set has been published and not picked up.
STALE_AFTER_DAYS = 130

_CIK_RE = re.compile(r"^(?:cik:)?0*(\d{1,10})$", re.I)
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,9}$")   # a ticker starts with a letter; "7203x" is neither


# ---- reader ----------------------------------------------------------------
def _version():
    try:
        st = DB_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


file_version = _version


def _cur():
    global _READER, _READER_VERSION
    if not DB_PATH.exists():
        raise HTTPException(503, "US financials database not built yet")
    version = _version()
    with _LOCK:
        if _READER is None or _READER_VERSION != version:
            if _READER is not None:
                _READER.close()
            _READER = db.limit_memory(duckdb.connect(str(DB_PATH), read_only=True))
            _READER_VERSION = version
        return _READER.cursor()


def _require():
    cur = _cur()
    names = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if "sec_filings" not in names or "sec_facts" not in names:
        raise HTTPException(503, "US financials not published on this server yet")
    return cur


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def parse_cik(value):
    """'320193', '0000320193' or 'cik:320193' -> 320193; anything else is 400."""
    m = _CIK_RE.match((value or "").strip())
    if not m:
        raise HTTPException(400, "give a CIK (digits, optionally written cik:NNN), e.g. 320193")
    return int(m.group(1))


def _has(cur, table):
    return bool(cur.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ?",
                            [table]).fetchone()[0])


def resolve_company(cur, value):
    """A CIK ('320193', 'cik:320193') or a ticker ('AAPL', 'brk-b') -> CIK.

    Tickers come from the SEC's own company_tickers_exchange.json, loaded by
    equity/sec_universe.py into sec_companies; a share-class ticker (GOOG,
    GOOGL) resolves to the one filer. Unknown is 404, not a guess."""
    raw = (value or "").strip()
    m = _CIK_RE.match(raw)
    if m and (raw.lower().startswith("cik:") or raw.isdigit()):
        return int(m.group(1))
    t = raw.upper().replace(".", "-")
    if not _TICKER_RE.match(t) or not _has(cur, "sec_companies"):
        raise HTTPException(400, "give a CIK (digits, optionally cik:NNN) or a ticker, e.g. AAPL")
    row = cur.execute("""
        SELECT cik FROM sec_companies
        WHERE ticker = ? OR list_contains(string_split(tickers, ','), ?)
        ORDER BY (ticker = ?) DESC LIMIT 1""", [t, t, t]).fetchone()
    if not row:
        raise HTTPException(404, "no SEC filer with ticker %s" % t)
    return int(row[0])


def _company_meta(cur, cik):
    """ticker / exchange / industry for one CIK, or {} before the universe is loaded."""
    if not _has(cur, "sec_companies"):
        return {}
    r = cur.execute("SELECT ticker, tickers, exchange, sic, industry, office FROM sec_companies "
                    "WHERE cik = ?", [cik]).fetchone()
    if not r:
        return {}
    return {"ticker": r[0], "tickers": (r[1] or "").split(",") if r[1] else [],
            "exchange": r[2], "sic": r[3], "industry": r[4], "office": r[5]}


def _forms(form):
    f = (form or "").strip()
    if not f:
        return ANNUAL_FORMS
    if f in FORM_FAMILY:
        return FORM_FAMILY[f]
    if f.upper() in ANNUAL_FORMS + QUARTERLY_FORMS:
        return (f.upper(),)
    raise HTTPException(400, "form must be 10-K (annual: 10-K, 20-F, 40-F) or 10-Q")


def _span(f):
    """Quarters the filing's own flow figures cover."""
    if f["form"] in ANNUAL_FORMS:
        return 4
    return {"Q1": 1, "Q2": 1, "Q3": 1}.get(f["fp"] or "", 1)


def _ytd_span(f):
    if f["form"] in ANNUAL_FORMS:
        return 4
    return {"Q1": 1, "Q2": 2, "Q3": 3}.get(f["fp"] or "", 1)


FILINGS_SQL = """
    SELECT adsh, quarter, cik, name, sic, country_ba, state_ba, city_ba, country_inc,
           fye, form, period, fy, fp, filed, accepted, prevrpt, status, note, bs_check,
           total_assets, facts
    FROM sec_filings s
    WHERE s.cik = ? AND s.form IN (%s)/*ASOF*/
    ORDER BY period DESC, filed DESC
"""


def _filings(cur, cik, forms):
    sql = (FILINGS_SQL % ",".join("?" * len(forms))).replace("/*ASOF*/", asof.clause("filed", "s"))
    rows = _rows(cur, sql, [cik] + list(forms))
    if not rows:
        raise HTTPException(404, "no %s filing loaded for CIK %d" % ("/".join(sorted({f.split('/')[0] for f in forms})), cik))
    return rows


def _pick(filings, period, fy):
    if period:
        try:
            want = datetime.date.fromisoformat(period)
        except ValueError:
            raise HTTPException(400, "period must be YYYY-MM-DD")
        for f in filings:
            if f["period"] == want:
                return f
        raise HTTPException(404, "no filing with period end %s (have %s)"
                            % (period, ", ".join(_d(f["period"]) for f in filings[:8])))
    if fy:
        for f in filings:
            if str(f["fy"]) == str(fy).strip():
                return f
        raise HTTPException(404, "no filing for fiscal year %s (have %s)"
                            % (fy, ", ".join(sorted({str(f["fy"]) for f in filings}))))
    return filings[0]


def _filing_out(f):
    out = {k: _d(v) for k, v in f.items()}
    out["source_url"] = ("https://www.sec.gov/Archives/edgar/data/%d/%s/"
                         % (f["cik"], f["adsh"].replace("-", "")))
    return out


# ---- key indicators --------------------------------------------------------
PERIOD_FACTS_SQL = """
    SELECT tag, qtrs, uom, value
    FROM sec_facts
    WHERE adsh = ? AND ddate = ? AND segment_id IS NULL AND coreg IS NULL
      AND tag IN (%s)
""" % ",".join("?" * len(FIELD_TAGS))


def _indicators(cur, f):
    """{field: value}, {field: tag}, {field: unit} for one filing's own period."""
    facts = {}
    for r in _rows(cur, PERIOD_FACTS_SQL, [f["adsh"], f["period"]] + FIELD_TAGS):
        facts.setdefault((r["tag"], r["qtrs"]), (r["value"], r["uom"]))
    span, ytd = _span(f), _ytd_span(f)
    values, tags, units, ytd_values = {}, {}, {}, {}
    for field, kind, candidates in FIELDS:
        values[field] = tags[field] = units[field] = None
        for tag in candidates:
            hit = facts.get((tag, 0 if kind == "instant" else span))
            if hit is not None:
                values[field], units[field] = hit
                tags[field] = tag
                break
        if kind == "duration" and ytd != span:
            for tag in candidates:
                hit = facts.get((tag, ytd))
                if hit is not None:
                    ytd_values[field] = hit[0]
                    break
    from . import sec_metrics
    as_tagged = dict(tags)
    if sec_metrics.composite_revenue(values, tags, f.get("sic")):
        units["revenue"] = units.get("interest_income") or units.get("lease_income") or units.get("revenue")
        if ytd_values.get("interest_income") is not None or ytd_values.get("lease_income") is not None:
            sec_metrics.composite_revenue(ytd_values, as_tagged, f.get("sic"))
    return values, tags, units, ytd_values


@router.get("/company/{cik}", openapi_extra={"x-example": "/api/v1/us/financials/company/320193"})
def company_by_cik(cik: str,
                   form: str = Query("", description="10-K (default; also 20-F/40-F) or 10-Q"),
                   limit: int = Query(12, ge=1, le=100)):
    """One company's key indicators per filing, newest first, each row naming
    the tag behind every field."""
    cur = _require()
    n = resolve_company(cur, cik)
    forms = _forms(form)
    filings = _filings(cur, n, forms)[:limit]
    head = filings[0]
    meta = _company_meta(cur, n)
    panel = []
    for f in filings:
        values, tags, units, ytd = _indicators(cur, f)
        row = {
            "adsh": f["adsh"], "form": f["form"], "period": _d(f["period"]),
            "fy": f["fy"], "fp": f["fp"], "filed": _d(f["filed"]),
            "amendment": bool(f["prevrpt"]), "status": f["status"], "note": f["note"],
            "span_quarters": _span(f), "values": values, "tags": tags, "units": units,
        }
        if ytd:
            row["ytd_values"] = ytd
            row["ytd_span_quarters"] = _ytd_span(f)
        panel.append(row)
    return {
        "cik": n, "sec_code": "cik:%d" % n, "name": head["name"], "sic": head["sic"],
        "ticker": meta.get("ticker"), "tickers": meta.get("tickers") or [],
        "exchange": meta.get("exchange"), "industry": meta.get("industry"),
        "country": head["country_ba"], "state": head["state_ba"], "city": head["city_ba"],
        "country_inc": head["country_inc"], "fiscal_year_end": head["fye"],
        "forms": sorted({f["form"] for f in filings}),
        "latest_filing": _filing_out(head),
        "fields": [{"id": fld, "kind": kind, "tags": tags} for fld, kind, tags in FIELDS],
        "panel": panel,
        "calc": CALC, "provenance": PROVENANCE,
    }


def company(sec_code, form="", limit=12):
    """The registry's company view: accepts `cik:NNN` (or bare digits).

    A code that is not a CIK is "not here" (404), not a bad request: the
    composed company page asks every dataset about a Japanese securities
    code, and this shelf simply has nothing under it."""
    if not _CIK_RE.match((sec_code or "").strip()):
        raise HTTPException(404, "no US filer under %r — this shelf is keyed by SEC CIK "
                                 "(write cik:320193)" % (sec_code or ""))
    return company_by_cik(sec_code, form=form, limit=limit)


# ---- statements ------------------------------------------------------------
STATEMENT_SQL = """
    WITH ln AS (
        SELECT l.report, l.line, l.tag, l.version, l.plabel, l.inpth, l.negating,
               t.tlabel, t.custom, t.abstract, t.iord, t.crdr, t.datatype
        FROM sec_lines l
        LEFT JOIN sec_tags t ON t.tag = l.tag AND t.version = l.version
        WHERE l.adsh = ? AND l.stmt = ?
    ),
    fx AS (
        SELECT tag, ddate, qtrs, uom, value
        FROM sec_facts
        WHERE adsh = ? AND segment_id IS NULL AND coreg IS NULL
    ),
    cur AS (
        SELECT ln.tag, ln.report, ln.line, f.uom, f.value, f.qtrs
        FROM ln JOIN fx f ON f.tag = ln.tag AND f.ddate = ?
                         AND f.qtrs = CASE WHEN ln.iord = 'I' THEN 0 ELSE ? END
    ),
    prior AS (
        SELECT tag, report, line, value, ddate FROM (
            SELECT ln.tag, ln.report, ln.line, f.value, f.ddate,
                   row_number() OVER (PARTITION BY ln.report, ln.line ORDER BY f.ddate DESC) AS rn
            FROM ln JOIN fx f ON f.tag = ln.tag AND f.ddate < ?
                             AND f.qtrs = CASE WHEN ln.iord = 'I' THEN 0 ELSE ? END
        ) WHERE rn = 1
    )
    SELECT ln.report, ln.line, ln.tag, ln.version, ln.plabel, ln.tlabel, ln.custom,
           ln.abstract, ln.iord, ln.crdr, ln.inpth, ln.negating,
           c.uom AS unit, c.value AS current, c.qtrs AS qtrs,
           p.value AS prior, p.ddate AS prior_date
    FROM ln
    LEFT JOIN cur c ON c.report = ln.report AND c.line = ln.line
    LEFT JOIN prior p ON p.report = ln.report AND p.line = ln.line
    ORDER BY ln.report, ln.line
"""


@router.get("/statements/{cik}",
            openapi_extra={"x-example": "/api/v1/us/financials/statements/320193?statement=IS"})
def statements_by_cik(cik: str,
                      statement: str = Query("BS", description="BS | IS | CF | CI | EQ"),
                      form: str = Query("", description="10-K (default; also 20-F/40-F) or 10-Q"),
                      period: str = Query("", description="period end YYYY-MM-DD; default latest"),
                      fy: str = Query("", description="fiscal year of the filing; default latest")):
    """One statement of one filing, every line as filed, with the comparative
    the filer tagged."""
    cur = _require()
    n = resolve_company(cur, cik)
    st = (statement or "BS").strip().upper()
    if st not in STATEMENTS:
        raise HTTPException(400, "statement must be one of %s" % ", ".join(STATEMENTS))
    filings = _filings(cur, n, _forms(form))
    f = _pick(filings, period, fy)
    span = _span(f)
    lines = _rows(cur, STATEMENT_SQL, [f["adsh"], st, f["adsh"], f["period"], span,
                                       f["period"], span])
    avail = [r[0] for r in cur.execute(
        "SELECT DISTINCT stmt FROM sec_lines WHERE adsh = ? ORDER BY stmt", [f["adsh"]]).fetchall()]
    if not lines:
        raise HTTPException(404, "filing %s carries no %s statement (available: %s)"
                            % (f["adsh"], st, ", ".join(a for a in avail if a) or "none"))
    items = []
    for r in lines:
        items.append({
            "report": r["report"], "line": r["line"], "tag": r["tag"], "version": r["version"],
            "label": r["plabel"], "label_standard": r["tlabel"],
            "custom": bool(r["custom"]), "is_heading": bool(r["abstract"]),
            "parenthetical": bool(r["inpth"]), "negated": bool(r["negating"]),
            "kind": "instant" if r["iord"] == "I" else "duration",
            "balance": r["crdr"], "unit": r["unit"],
            "current": r["current"], "prior": r["prior"], "prior_date": _d(r["prior_date"]),
        })
    out = {
        "cik": n, "name": f["name"], "statement": st, "statement_name": STATEMENTS[st],
        "adsh": f["adsh"], "form": f["form"], "period": _d(f["period"]), "fy": f["fy"],
        "fp": f["fp"], "filed": _d(f["filed"]), "span_quarters": span,
        "status": f["status"], "note": f["note"], "bs_check": f["bs_check"],
        "available": [a for a in avail if a],
        "available_periods": [_d(x["period"]) for x in filings],
        "source_url": _filing_out(f)["source_url"],
        "lines": items,
        "calc": {"note": ("Values exactly as tagged. `negated` means the filer "
                          "prints the figure with its sign reversed. A duration "
                          "line covers span_quarters ending on `period`; `prior` "
                          "is the filer's own comparative (prior_date)."),
                 "comparative": CALC["comparative"]},
        "provenance": PROVENANCE,
    }
    return out


# ---- one tag's history -----------------------------------------------------
@router.get("/facts/{cik}", openapi_extra={"x-example": "/api/v1/us/financials/facts/320193?tag=Revenues"})
def facts_by_cik(cik: str,
                 tag: str = Query(..., description="us-gaap tag, e.g. NetIncomeLoss"),
                 form: str = Query("", description="10-K (default family) | 10-Q | all"),
                 segments: int = Query(0, description="1 to include dimensional (segment) facts")):
    """Every filed value of one tag for one company, across filings — the raw
    panel a model is built on."""
    cur = _require()
    n = resolve_company(cur, cik)
    t = (tag or "").strip()
    if not t:
        raise HTTPException(400, "tag is required")
    forms = ANNUAL_FORMS + QUARTERLY_FORMS if (form or "").strip() == "all" else _forms(form)
    rows = _rows(cur, """
        SELECT f.adsh, s.form, s.period, s.fy, s.fp, s.filed, f.ddate, f.qtrs, f.uom,
               f.value, g.segments, f.coreg, f.footnote
        FROM sec_facts f JOIN sec_filings s USING (adsh)
        LEFT JOIN sec_segments g ON g.segment_id = f.segment_id
        WHERE s.cik = ? AND f.tag = ? AND s.form IN (%s)%s
          AND (? = 1 OR f.segment_id IS NULL)
        ORDER BY f.ddate DESC, f.qtrs, s.filed DESC, g.segments
        """ % (",".join("?" * len(forms)), asof.clause("filed", "s")),
        [n, t] + list(forms) + [1 if segments else 0])
    if not rows:
        raise HTTPException(404, "no filed value of %s for CIK %d" % (t, n))
    lab = _rows(cur, "SELECT tlabel, doc, custom FROM sec_tags WHERE tag = ? "
                     "ORDER BY version DESC LIMIT 1", [t])
    for r in rows:
        for k in ("period", "filed", "ddate"):
            r[k] = _d(r[k])
    return {"cik": n, "tag": t,
            "label_standard": lab[0]["tlabel"] if lab else None,
            "definition": lab[0]["doc"] if lab else None,
            "values": rows, "provenance": PROVENANCE}


# ---- search, tags, summary -------------------------------------------------
@router.get("/companies", openapi_extra={"x-example": "/api/v1/us/financials/companies?q=apple"})
def companies(q: str = Query("", description="name substring, ticker or CIK"),
              exchange: str = Query("", description="Nasdaq | NYSE | OTC | CBOE | TXSE"),
              sic: str = Query("", description="SIC code, e.g. 3674"),
              limit: int = Query(25, ge=1, le=500)):
    """Filers with at least one periodic report loaded, by name, ticker or
    CIK — with the ticker, exchange and industry the SEC lists for them."""
    cur = _require()
    needle = (q or "").strip()
    forms = ANNUAL_FORMS + QUARTERLY_FORMS
    universe = _has(cur, "sec_companies")
    where, params = "", []
    if needle:
        m = _CIK_RE.match(needle)
        if m and (needle.lower().startswith("cik:") or needle.isdigit()):
            where, params = " AND s.cik = ?", [int(m.group(1))]
        elif universe:
            t = needle.upper().replace(".", "-")
            where = (" AND (lower(s.name) LIKE ? OR s.cik IN (SELECT cik FROM sec_companies "
                     "WHERE ticker = ? OR list_contains(string_split(tickers, ','), ?)))")
            params = ["%" + needle.lower() + "%", t, t]
        else:
            where, params = " AND lower(s.name) LIKE ?", ["%" + needle.lower() + "%"]
    if universe and (exchange or "").strip():
        where += " AND s.cik IN (SELECT cik FROM sec_companies WHERE lower(exchange) = ?)"
        params.append(exchange.strip().lower())
    if (sic or "").strip():
        if not sic.strip().isdigit():
            raise HTTPException(400, "sic must be a code, e.g. 3674")
        where += " AND s.sic = ?"
        params.append(int(sic.strip()))
    join = ("LEFT JOIN sec_companies c ON c.cik = f.cik" if universe else "")
    cols = ("c.ticker, c.exchange, c.industry" if universe
            else "NULL AS ticker, NULL AS exchange, NULL AS industry")
    rows = _rows(cur, """
        WITH f AS (
            SELECT s.cik, arg_max(s.name, s.filed) AS name, arg_max(s.sic, s.filed) AS sic,
                   arg_max(s.country_ba, s.filed) AS country, arg_max(s.state_ba, s.filed) AS state,
                   max(s.filed) AS latest_filed, arg_max(s.form, s.filed) AS latest_form,
                   count(*) AS filings
            FROM sec_filings s
            WHERE s.form IN (%s)%s%s
            GROUP BY s.cik)
        SELECT f.*, %s FROM f %s
        ORDER BY f.latest_filed DESC, f.name LIMIT ?
        """ % (",".join("?" * len(forms)), asof.clause("filed", "s"), where, cols, join),
        list(forms) + params + [limit])
    for r in rows:
        r["latest_filed"] = _d(r["latest_filed"])
        r["sec_code"] = "cik:%d" % r["cik"]
    return {"query": needle, "companies": rows, "provenance": PROVENANCE}


@router.get("/tags", openapi_extra={"x-example": "/api/v1/us/financials/tags?q=revenue"})
def tags(q: str = Query("", description="substring of the tag or its standard label"),
         limit: int = Query(50, ge=1, le=500)):
    """Standard (non-custom) taxonomy elements, for finding the tag to ask for."""
    cur = _require()
    needle = (q or "").strip().lower()
    rows = _rows(cur, """
        SELECT tag, arg_max(tlabel, version) AS label, arg_max(doc, version) AS definition,
               arg_max(iord, version) AS iord, arg_max(crdr, version) AS balance,
               arg_max(datatype, version) AS datatype, max(version) AS latest_version
        FROM sec_tags
        WHERE NOT custom AND NOT abstract
          AND (? = '' OR lower(tag) LIKE ? OR lower(tlabel) LIKE ?)
        GROUP BY tag ORDER BY length(tag), tag LIMIT ?
        """, [needle, "%" + needle + "%", "%" + needle + "%", limit])
    return {"query": needle, "tags": rows, "provenance": PROVENANCE}


@router.get("/summary", openapi_extra={"x-example": "/api/v1/us/financials/summary"})
def summary():
    """What is loaded: quarters, filings by form, companies, verdict counts."""
    cur = _require()
    quarters = _rows(cur, "SELECT quarter, sha256, captured_at, loaded_at, parser_version, "
                          "filings, facts, lines, rejected_rows, status, detail "
                          "FROM sec_quarters ORDER BY quarter DESC")
    for r in quarters:
        r["captured_at"], r["loaded_at"] = _d(r["captured_at"]), _d(r["loaded_at"])
    forms = _rows(cur, "SELECT form, count(*) AS filings, count(DISTINCT cik) AS companies "
                       "FROM sec_filings GROUP BY form ORDER BY filings DESC")
    verdicts = _rows(cur, "SELECT status, count(*) AS filings FROM sec_filings "
                          "GROUP BY status ORDER BY filings DESC")
    tot = _rows(cur, "SELECT count(*) AS filings, count(DISTINCT cik) AS companies, "
                     "max(filed) AS latest_filed, min(filed) AS earliest_filed FROM sec_filings")[0]
    facts = cur.execute("SELECT count(*) FROM sec_facts").fetchone()[0]
    return {"quarters": quarters, "forms": forms, "verdicts": verdicts,
            "filings": tot["filings"], "companies": tot["companies"], "facts": facts,
            "earliest_filed": _d(tot["earliest_filed"]), "latest_filed": _d(tot["latest_filed"]),
            "provenance": PROVENANCE}


# ---- screen ----------------------------------------------------------------
SCREEN_SQL = """
    WITH latest AS (
        SELECT * FROM (
            SELECT s.*, row_number() OVER (PARTITION BY cik ORDER BY period DESC, filed DESC) AS rn
            FROM sec_filings s
            WHERE s.form IN (%s) AND s.status IN ('clean','partial')/*ASOF*/
              AND (CAST(? AS VARCHAR) IS NULL OR CAST(s.fy AS VARCHAR) = CAST(? AS VARCHAR))
        ) WHERE rn = 1
    ),
    cand AS (SELECT * FROM (VALUES %s) v(tag, rank)),
    hits AS (
        SELECT l.adsh, f.tag, f.uom, f.value, c.rank,
               row_number() OVER (PARTITION BY l.adsh ORDER BY c.rank) AS rn
        FROM latest l
        JOIN sec_facts f ON f.adsh = l.adsh AND f.ddate = l.period
                        AND f.segment_id IS NULL AND f.coreg IS NULL
                        AND f.qtrs = ?
        JOIN cand c ON c.tag = f.tag
    )
    SELECT l.cik, l.name, l.sic, l.country_ba AS country, l.form, l.period, l.fy, l.filed,
           l.status, h.tag, h.uom AS unit, h.value
    FROM latest l JOIN hits h ON h.adsh = l.adsh AND h.rn = 1
    WHERE h.uom = ?
    ORDER BY h.value %s NULLS LAST LIMIT ?
"""


@router.get("/screen", openapi_extra={"x-example": "/api/v1/us/financials/screen?metric=revenue&limit=20"})
def screen(metric: str = Query("revenue", description="one of " + ", ".join(SCREEN_FIELDS)),
           fy: str = Query("", description="fiscal year; default each company's latest annual report"),
           order: str = Query("desc", description="desc | asc"),
           unit: str = Query("USD", description="only filings reporting in this unit are ranked"),
           limit: int = Query(25, ge=1, le=200)):
    """Companies ranked on one key indicator from their latest annual report
    (10-K/20-F/40-F). Ranked within one reporting unit only — a filer
    reporting in CNY is never ranked against one reporting in USD."""
    cur = _require()
    m = (metric or "revenue").strip()
    if m not in SCREEN_FIELDS:
        raise HTTPException(400, "metric must be one of %s" % ", ".join(SCREEN_FIELDS))
    kind, tags_ = next((k, t) for fld, k, t in FIELDS if fld == m)
    values = ", ".join("('%s', %d)" % (t, i) for i, t in enumerate(tags_))
    sql = (SCREEN_SQL % (",".join("?" * len(ANNUAL_FORMS)), values,
                         "ASC" if (order or "").lower() == "asc" else "DESC")
           ).replace("/*ASOF*/", asof.clause("filed", "s"))
    fy_v = (fy or "").strip() or None
    rows = _rows(cur, sql, list(ANNUAL_FORMS) + [fy_v, fy_v, 0 if kind == "instant" else 4,
                                                 (unit or "USD").strip(), limit])
    for r in rows:
        r["period"], r["filed"] = _d(r["period"]), _d(r["filed"])
        r["sec_code"] = "cik:%d" % r["cik"]
    return {"metric": m, "kind": kind, "tags": tags_, "unit": (unit or "USD").strip(),
            "fy": fy_v, "order": "asc" if (order or "").lower() == "asc" else "desc",
            "rows": rows, "calc": CALC, "provenance": PROVENANCE}


# ---- ratios: metrics, screener, industries ---------------------------------
DERIVED_PROVENANCE = {
    "trust": "derived",
    "note": ("Calculated on this platform from each filer's latest annual report as the SEC "
             "extracted it; every metric names its formula and the tag behind every input. "
             "No market data: there is no market value, yield or multiple here."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR (inputs).",
}


def _num(value, name):
    v = (value or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        raise HTTPException(400, "%s must be a number" % name)


def _decorate(cur, rows):
    """Attach ticker, exchange and industry from the universe to metric rows.
    The industry title follows the SIC code on the filing itself; the
    universe's title is the fallback for a filing with no code."""
    meta = ({r[0]: r[1:] for r in cur.execute(
        "SELECT cik, ticker, exchange, industry, office FROM sec_companies").fetchall()}
        if _has(cur, "sec_companies") else {})
    titles = ({r[0]: r[1] for r in cur.execute("SELECT sic, industry FROM sec_sic").fetchall()}
              if _has(cur, "sec_sic") else {})
    from . import sec_metrics, sec_shares, us_prices_api
    closes = us_prices_api.latest_closes(cur)
    by_symbol = {sec_shares._norm(k): v for k, v in closes.items()}
    for r in rows:
        # each decorated copy gets its own metrics: the price figures written below would otherwise
        # land in the dict shared with the cached ratio rows, and two passes running at once (the
        # warm-up and a first request) could blank a value before the other pass judged it
        r["metrics"] = dict(r.get("metrics") or {})
        m = meta.get(r["cik"]) or (None, None, None, None)
        r["ticker"], r["exchange"], r["office"] = m[0], m[1], m[3]
        r["industry"] = (titles.get(int(r["sic"])) if r.get("sic") is not None else None) or m[2]
        px = (closes.get(r["ticker"]) or by_symbol.get(sec_shares._norm(r["ticker"]))) if r["ticker"] else None
        if px is not None:
            sec_metrics.with_prices(r, px[1], px[2], _d(px[0]), sec_shares.basis(cur, r, by_symbol),
                                    close_1m=px[3] if len(px) > 3 else None)
    sec_shares.hold_back_uncorroborated(cur, rows)
    return rows


def _has_prices(cur):
    return _has(cur, "us_px_daily")


@router.get("/metrics/{company}", openapi_extra={"x-example": "/api/v1/us/financials/metrics/AAPL"})
def metrics(company: str):
    """One filer's platform-calculated ratios from its latest annual report,
    with the formula for each and the tag behind every input."""
    from . import sec_metrics, us_prices_api
    cur = _require()
    n = resolve_company(cur, company)
    row = next((r for r in sec_metrics.all_rows(cur) if r["cik"] == n), None)
    if row is None:
        raise HTTPException(404, "no annual report loaded for CIK %d, so no ratios" % n)
    row = dict(row)
    _decorate(cur, [row])
    from . import us_link
    row["name"] = us_link.company_name(n, row.get("name") or "", cur)
    row["logo"] = us_link.logo(n)
    priced = _has_prices(cur)
    row["metric_defs"] = sec_metrics.metric_defs(prices=priced)
    row["calc"] = dict(sec_metrics.FORMULAS, **(sec_metrics.PRICE_FORMULAS if priced else {}))
    row["provenance"] = DERIVED_PROVENANCE
    if priced:
        row["price_provenance"] = us_prices_api.PROVENANCE
    return row


@router.get("/screener", openapi_extra={"x-example": "/api/v1/us/financials/screener?sort=roe_pct&limit=20"})
def screener(sic: str = Query("", description="SIC code (e.g. 3674) or a 2-digit prefix (37)"),
             industry: str = Query("", description="substring of the SIC industry title, e.g. motor vehicles"),
             exchange: str = Query("", description="Nasdaq | NYSE | OTC | CBOE | TXSE, or several separated by commas"),
             tickers: str = Query("", description="comma-separated tickers to rank within"),
             q: str = Query("", max_length=80, description="part of the company's name, its ticker, or its SEC number"),
             unit: str = Query("USD", description="reporting currency ranked; size sorts never mix units"),
             min_revenue: str = Query(""), min_assets: str = Query(""),
             roe_min: str = Query(""), roe_max: str = Query(""), roa_min: str = Query(""),
             operating_margin_min: str = Query(""), net_margin_min: str = Query(""),
             equity_ratio_min: str = Query(""), equity_ratio_max: str = Query(""),
             revenue_growth_min: str = Query(""), cash_to_assets_min: str = Query(""),
             payout_ratio_min: str = Query(""), buyback_ratio_min: str = Query(""),
             profit_growth_min: str = Query(""), fcf_margin_min: str = Query(""),
             # price-based limits (only rows with a close carry these figures)
             market_cap_min: str = Query("", description="US dollars, e.g. 20000000000"), market_cap_max: str = Query(""),
             pe_min: str = Query(""), pe_max: str = Query(""),
             dividend_yield_min: str = Query(""), buyback_yield_min: str = Query(""),
             price_change_1y_min: str = Query(""), price_change_1y_max: str = Query(""),
             price_change_1m_min: str = Query(""), price_change_1m_max: str = Query(""),
             sp500: bool = Query(False, description="members of the S&P 500 only"),
             sort: str = Query("roe_pct", description="a metric or a size field to rank on"),
             order: str = Query("desc", description="desc | asc"),
             current_only: bool = Query(True, description="leave out filers whose latest annual report "
                                                          "ended more than 18 months ago (delisted, acquired or late)"),
             limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Every US filer's latest annual report on platform-calculated ratios,
    filtered and ranked. Companies missing the sort field are excluded from
    the ranking and counted in `excluded_missing_sort`. Size fields are
    ranked within one reporting currency only."""
    from . import sec_metrics, us_prices_api
    cur = _require()
    priced = _has_prices(cur)
    sortable = set(sec_metrics.METRIC_ORDER) | set(sec_metrics.SIZE_FIELDS)
    if priced:
        sortable |= set(sec_metrics.PRICE_ORDER)
    if sort not in sortable:
        raise HTTPException(400, "sort must be one of %s" % ", ".join(sorted(sortable)))
    asc = (order or "").lower() == "asc"
    rows = _decorate(cur, [dict(r) for r in sec_metrics.all_rows(cur)])
    f = {k: _num(v, k) for k, v in (
        ("min_revenue", min_revenue), ("min_assets", min_assets), ("roe_min", roe_min),
        ("roe_max", roe_max), ("roa_min", roa_min), ("operating_margin_min", operating_margin_min),
        ("net_margin_min", net_margin_min), ("equity_ratio_min", equity_ratio_min),
        ("equity_ratio_max", equity_ratio_max), ("revenue_growth_min", revenue_growth_min),
        ("cash_to_assets_min", cash_to_assets_min), ("payout_ratio_min", payout_ratio_min),
        ("buyback_ratio_min", buyback_ratio_min), ("profit_growth_min", profit_growth_min),
        ("fcf_margin_min", fcf_margin_min), ("market_cap_min", market_cap_min), ("market_cap_max", market_cap_max),
        ("pe_min", pe_min), ("pe_max", pe_max), ("dividend_yield_min", dividend_yield_min),
        ("buyback_yield_min", buyback_yield_min), ("price_change_1y_min", price_change_1y_min),
        ("price_change_1y_max", price_change_1y_max), ("price_change_1m_min", price_change_1m_min),
        ("price_change_1m_max", price_change_1m_max))}
    sic_q = (sic or "").strip()
    sic_set = [s.strip() for s in sic_q.split(",") if s.strip()]
    if any(not s.isdigit() for s in sic_set):
        raise HTTPException(400, "sic must be digits: a full code or a prefix, or several separated by commas")
    ind_q = (industry or "").strip().lower()
    ex_set = {e.strip().lower() for e in (exchange or "").split(",") if e.strip()}
    ex_q = ",".join(sorted(ex_set))
    sp_members = None
    if sp500:
        from . import us_benchmarks
        sp_members = us_benchmarks.members(cur, "sp500")[0] or set()
    want = {t.strip().upper().replace(".", "-") for t in (tickers or "").split(",") if t.strip()}
    name_q = " ".join((q or "").split()).lower()
    u = (unit or "USD").strip().upper()
    size_sort = sort in sec_metrics.SIZE_FIELDS

    def ge(v, lim):
        return lim is None or (v is not None and v >= lim)

    def le(v, lim):
        return lim is None or (v is not None and v <= lim)

    cutoff = (datetime.date.today() - datetime.timedelta(days=550)).isoformat()
    kept = []
    for r in rows:
        m, z = r["metrics"], r["size"]
        if current_only and (r.get("period_end") or "") < cutoff:
            continue
        if sic_set and not (r.get("sic") is not None and any(str(int(r["sic"])).startswith(s) for s in sic_set)):
            continue
        if ind_q and ind_q not in (r.get("industry") or "").lower():
            continue
        if ex_set and (r.get("exchange") or "").lower() not in ex_set:
            continue
        if sp_members is not None and r["cik"] not in sp_members:
            continue
        if want and (r.get("ticker") or "") not in want:
            continue
        if name_q and name_q not in (r.get("name") or "").lower() and \
                name_q.upper().replace(".", "-") != (r.get("ticker") or "") and \
                not (name_q.isdigit() and int(name_q) == r["cik"]):
            continue
        if (size_sort or f["min_revenue"] is not None or f["min_assets"] is not None) and r["unit"] != u:
            continue
        if not (ge(z["revenue"], f["min_revenue"]) and ge(z["total_assets"], f["min_assets"])
                and ge(m["roe_pct"], f["roe_min"]) and le(m["roe_pct"], f["roe_max"])
                and ge(m["roa_pct"], f["roa_min"]) and ge(m["operating_margin_pct"], f["operating_margin_min"])
                and ge(m["net_margin_pct"], f["net_margin_min"])
                and ge(m["equity_ratio_pct"], f["equity_ratio_min"]) and le(m["equity_ratio_pct"], f["equity_ratio_max"])
                and ge(m["revenue_growth_pct"], f["revenue_growth_min"])
                and ge(m["cash_to_assets_pct"], f["cash_to_assets_min"])
                and ge(m["payout_ratio_pct"], f["payout_ratio_min"])
                and ge(m["buyback_ratio_pct"], f["buyback_ratio_min"])
                and ge(m.get("profit_growth_pct"), f["profit_growth_min"])
                and ge(m.get("fcf_margin_pct"), f["fcf_margin_min"])
                and ge(m.get("market_cap"), f["market_cap_min"]) and le(m.get("market_cap"), f["market_cap_max"])
                and ge(m.get("pe_x"), f["pe_min"]) and le(m.get("pe_x"), f["pe_max"])
                and ge(m.get("dividend_yield_pct"), f["dividend_yield_min"])
                and ge(m.get("buyback_yield_pct"), f["buyback_yield_min"])
                and ge(m.get("price_change_1y_pct"), f["price_change_1y_min"])
                and le(m.get("price_change_1y_pct"), f["price_change_1y_max"])
                and ge(m.get("price_change_1m_pct"), f["price_change_1m_min"])
                and le(m.get("price_change_1m_pct"), f["price_change_1m_max"])):
            continue
        kept.append(r)

    def key(r):
        return r["metrics"].get(sort) if sort in r["metrics"] else r["size"].get(sort)
    ranked = [r for r in kept if key(r) is not None]
    missing = len(kept) - len(ranked)
    ranked.sort(key=key, reverse=not asc)
    page = []
    from . import us_link
    for i, r in enumerate(ranked[offset:offset + limit], offset + 1):
        page.append({
            "rank": i, "cik": r["cik"], "sec_code": r["sec_code"], "name": r["name"],
            # the name every US page shows, and the company's icon (app/us_link.py)
            "display_name": us_link.company_name(r["cik"], r["name"], cur), "logo": us_link.logo(r["cik"]),
            "ticker": r.get("ticker"), "exchange": r.get("exchange"), "sic": r.get("sic"),
            "industry": r.get("industry"), "country": r.get("country"), "form": r["form"],
            "period_end": r["period_end"], "fy": r["fy"], "filed": r["filed"], "status": r["status"],
            "unit": r["unit"], "size": r["size"], "per_share": r.get("per_share"), "price": r.get("price"),
            "metrics": r["metrics"], "tags": r["tags"],
            "checks": r["checks"], "flags": r["flags"], "sort_value": key(r),
            "source_url": r["source_url"],
        })
    return {"sort": sort, "order": "asc" if asc else "desc",
            "filters": {"sic": sic_q or None, "industry": ind_q or None, "exchange": ex_q or None, "q": name_q or None,
                        "tickers": sorted(want) or None, "unit": u, "sp500": sp500 or None,
                        **{k: v for k, v in f.items() if v is not None}},
            "universe": len(rows), "matched": len(kept), "ranked": len(ranked),
            "excluded_missing_sort": missing, "offset": offset, "limit": limit,
            "rows": page, "metric_defs": sec_metrics.metric_defs(prices=priced),
            "calc": dict(sec_metrics.FORMULAS, **(sec_metrics.PRICE_FORMULAS if priced else {})),
            "provenance": DERIVED_PROVENANCE,
            "price_provenance": us_prices_api.PROVENANCE if priced else None}


@router.get("/screener/options")
def screener_options():
    """What the screener can filter and sort on, with universe counts."""
    from . import sec_metrics
    cur = _require()
    rows = _decorate(cur, [dict(r) for r in sec_metrics.all_rows(cur)])
    by_ind, by_ex, by_unit = {}, {}, {}
    for r in rows:
        k = (r.get("sic"), r.get("industry"))
        by_ind[k] = by_ind.get(k, 0) + 1
        by_ex[r.get("exchange")] = by_ex.get(r.get("exchange"), 0) + 1
        by_unit[r.get("unit")] = by_unit.get(r.get("unit"), 0) + 1
    return {
        "universe": len(rows),
        "industries": sorted([{"sic": k[0], "industry": k[1], "companies": v}
                              for k, v in by_ind.items() if k[0] is not None], key=lambda x: -x["companies"]),
        "exchanges": sorted([{"exchange": k, "companies": v} for k, v in by_ex.items() if k],
                            key=lambda x: -x["companies"]),
        "units": sorted([{"unit": k, "companies": v} for k, v in by_unit.items() if k],
                        key=lambda x: -x["companies"]),
        "metrics": sec_metrics.metric_defs(prices=_has_prices(cur)),
        "size_fields": sec_metrics.SIZE_FIELDS,
        "provenance": DERIVED_PROVENANCE,
    }


@router.get("/industries", openapi_extra={"x-example": "/api/v1/us/financials/industries?q=semiconductor"})
def industries(q: str = Query("", description="substring of the industry title")):
    """The SEC's SIC codes with their industry titles and how many filers
    with a loaded annual report carry each."""
    cur = _require()
    if not _has(cur, "sec_sic"):
        raise HTTPException(503, "industry list not loaded yet (equity/sec_universe.py)")
    needle = (q or "").strip().lower()
    rows = _rows(cur, """
        SELECT s.sic, s.office, s.industry, count(DISTINCT f.cik) AS companies
        FROM sec_sic s
        LEFT JOIN sec_filings f ON f.sic = s.sic AND f.form IN (%s)
        WHERE ? = '' OR lower(s.industry) LIKE ?
        GROUP BY s.sic, s.office, s.industry ORDER BY s.sic
        """ % ",".join("?" * len(ANNUAL_FORMS)), list(ANNUAL_FORMS) + [needle, "%" + needle + "%"])
    return {"query": needle, "industries": rows, "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------
def health():
    """One entry in the shape /catalog/health lists the equity extractors in."""
    if not DB_PATH.exists():
        return []
    try:
        cur = _require()
    except HTTPException:
        return []
    rows = cur.execute(
        "SELECT quarter, loaded_at, status FROM sec_quarters ORDER BY quarter").fetchall()
    good = [r for r in rows if r[2] in ("ok", "partial")]
    if not good:
        return [{"dataset": "sec-financials", "status": "attention",
                 "archive_read_through": None, "days_behind": None,
                 "stale_after_days": STALE_AFTER_DAYS, "stale": True,
                 "last_extracted_at": None, "archive_read_back_to": None,
                 "shape_flags": ["no quarter loaded"]}]
    newest = max(good, key=lambda r: (int(r[0][:4]), int(r[0][5])))
    oldest = min(good, key=lambda r: (int(r[0][:4]), int(r[0][5])))
    y, q = int(newest[0][:4]), int(newest[0][5])
    through = datetime.date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - datetime.timedelta(days=1)
    back_to = datetime.date(int(oldest[0][:4]), (int(oldest[0][5]) - 1) * 3 + 1, 1)
    days = (datetime.date.today() - through).days
    stale = days > STALE_AFTER_DAYS
    failed = [r[0] for r in rows if r[2] == "failed"]
    ran = max((r[1] for r in good if r[1]), default=None)
    return [{
        "dataset": "sec-financials",
        "status": "attention" if (stale or failed) else "ok",
        "archive_read_through": through.isoformat(),
        "days_behind": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": ran.isoformat() + "Z" if ran else None,
        "archive_read_back_to": back_to.isoformat(),
        "shape_flags": (["quarter %s failed to load" % f for f in failed]),
    }] + _universe_health(cur)


UNIVERSE_STALE_AFTER_DAYS = 10


def _universe_health(cur):
    """The ticker/industry lookup's own row: when the SEC was last reached."""
    if not _has(cur, "sec_universe_pulls"):
        return []
    last_ok, last_any, n_cos, first = cur.execute("""
        SELECT (SELECT max(fetched_at) FROM sec_universe_pulls WHERE status = 'ok'),
               (SELECT max(fetched_at) FROM sec_universe_pulls),
               (SELECT count(*) FROM sec_companies),
               (SELECT min(fetched_at) FROM sec_universe_pulls WHERE status = 'ok')""").fetchone()
    failed = bool(last_any and (last_ok is None or last_any > last_ok))
    days = (datetime.datetime.utcnow() - last_ok).days if last_ok else None
    stale = days is None or days > UNIVERSE_STALE_AFTER_DAYS
    return [{
        "dataset": "sec-universe",
        "status": "attention" if (stale or failed or not n_cos) else "ok",
        "archive_read_through": last_ok.date().isoformat() if last_ok else None,
        "days_behind": days,
        "stale_after_days": UNIVERSE_STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": last_ok.isoformat() + "Z" if last_ok else None,
        "archive_read_back_to": first.date().isoformat() if first else None,
        "shape_flags": (["latest pull failed"] if failed else []) + ([] if n_cos else ["no companies loaded"]),
    }]


# ---- manifest --------------------------------------------------------------
MANIFEST = {
    "id": "sec-financials",
    "section": "us-reference",
    "name": {"en": "US financial statements (SEC filings)",
             "ja": "米国企業財務諸表（SEC提出書類）"},
    "shape": "company",
    "summary": ("Every numeric fact in US periodic reports — 10-K, 10-Q, 20-F, "
                "40-F — from the SEC's own Financial Statement Data Sets: key "
                "indicators per filing, the statements as filed in the filer's "
                "own order and labels, and any tag's history. The US comparison "
                "shelf beside the Japanese financials; identity is the SEC CIK, "
                "written cik:NNN on shared surfaces."),
    "source": {
        "publisher": "U.S. Securities and Exchange Commission",
        "document": "Financial Statement Data Sets (quarterly; every XBRL periodic report)",
        "url": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets",
        "credit": "Source: U.S. Securities and Exchange Commission, EDGAR.",
        "license_note": "US government work; public domain.",
    },
    "keys": ["cik", "adsh"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "the newest %d quarterly data sets by default (SEC_QUARTERS); "
                        "the SEC's sets run from 2009 Q1" % int(os.environ.get("SEC_QUARTERS", "4")),
        "stale_after_days": STALE_AFTER_DAYS,
    },
    "measures": [
        {"id": "revenue", "label": "Revenue", "unit": "count", "trust": "official"},
        {"id": "operating_income", "label": "Operating income", "unit": "count", "trust": "official"},
        {"id": "net_income", "label": "Net income", "unit": "count", "trust": "official"},
        {"id": "eps_diluted", "label": "Diluted EPS", "unit": "count", "trust": "official"},
        {"id": "total_assets", "label": "Total assets", "unit": "count", "trust": "official"},
        {"id": "total_liabilities", "label": "Total liabilities", "unit": "count", "trust": "official"},
        {"id": "equity", "label": "Stockholders' equity", "unit": "count", "trust": "official"},
        {"id": "cash", "label": "Cash and equivalents", "unit": "count", "trust": "official"},
        {"id": "cf_operating", "label": "Operating cash flow", "unit": "count", "trust": "official"},
        {"id": "capex", "label": "Capital expenditure", "unit": "count", "trust": "official"},
        {"id": "buybacks", "label": "Share repurchases paid", "unit": "count", "trust": "official"},
        {"id": "dividends_paid", "label": "Dividends paid", "unit": "count", "trust": "official"},
        {"id": "shares_outstanding", "label": "Shares outstanding", "unit": "shares", "trust": "official"},
        {"id": "statement_line", "label": "Any statement line, in the filer's own order and label",
         "unit": "count", "trust": "official"},
    ],
    "endpoints": {
        "company": "/api/v1/us/financials/company/{cik}",
        "statements": "/api/v1/us/financials/statements/{cik}",
        "facts": "/api/v1/us/financials/facts/{cik}",
        "search": "/api/v1/us/financials/companies",
        "tags": "/api/v1/us/financials/tags",
        "summary": "/api/v1/us/financials/summary",
        "screen": "/api/v1/us/financials/screen",
        "screener": "/api/v1/us/financials/screener",
        "metrics": "/api/v1/us/financials/metrics/{cik_or_ticker}",
        "industries": "/api/v1/us/financials/industries",
        "calendar": "/api/v1/us/earnings/calendar",
        "calendar_company": "/api/v1/us/earnings/company/{cik_or_ticker}",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [{"id": k, "title": "US filers ranked on %s, latest annual report" % k.replace("_", " ")}
                for k in SCREEN_FIELDS],
    "cite": "/api/v1/us/financials/company/{cik}",
    "page": "/api.html",
    "notes": [
        "Values are in the filing's own unit (USD for most; some 20-F filers report in "
        "their home currency) — the unit is on every row and the screen ranks within one unit only.",
        "Key-indicator fields are a tag mapping, not a calculation: the tag used is named on "
        "every row, and a filing tagging none of a field's candidates has that field null.",
        "The SEC's nil facts (a dash in the statement) are dropped as missing, never stored as 0.",
        "Tickers, exchanges and industry titles come from the SEC's company_tickers_exchange.json "
        "and SIC code list (equity/sec_universe.py); any endpoint taking a CIK also takes a ticker.",
        "The ratios under /screener and /metrics are the one calculated layer (app/sec_metrics.py): "
        "no badge, a formula on every metric, and the tag behind every input named.",
        "The reporting calendar (/api/v1/us/earnings/…) rests on these filing dates: an expected date is "
        "last year's filing of the same report moved a year forward, named on the row; it is not an "
        "announced date and not the earnings-release date.",
        "Every filing carries a balance-sheet verdict (status, bs_check); partial filings are "
        "served with the reason and are never silently excluded or corrected.",
        "Not a product surface: the US shelf exists so a Japanese figure has a US counterpart "
        "to compare against, in the same shape, from the same kind of official source.",
    ],
}
