# -*- coding: utf-8 -*-
"""Equity product API — bank balance-sheet notes and rate risk.

Three things a bank prints that decide how it fares when yields rise, none
of them tagged as numbers anywhere:

  1. **The maturity ladder** (償還予定額): securities and loans by remaining
     term in six buckets, and deposits by term — read from the
     financial-instruments note of the annual securities report
     (equity/bank_extract.py). This is the only per-bank statement of how
     long the bond book runs.
  2. **Unrealised gains and losses** on available-for-sale securities by
     type — equities, bonds, JGBs, municipal, corporate, foreign — from the
     securities note of the same report. The bond line is the mark the
     market has already taken; the JGB line is the part of it that is
     the sovereign curve.
  3. **The Basel III rate-risk table** (IRRBB1): ΔEVE and ΔNII under six
     rate shocks and Tier 1 capital, read from each bank's own Pillar 3
     disclosure PDF (equity/irrbb_collect.py) — the bank's own official
     estimate of what a 100bp-type shock does to the economic value of its
     banking book. Keyed by the FSA's institution code, not the EDINET
     filer, because the bank publishes it, not the holding company.

Every figure is as printed; the raw table text is archived with the PDF's
SHA-256. The derived measures here — a loss as a share of equity, the
share of securities beyond ten years, ΔEVE as a share of Tier 1 — are
divisions of two printed figures and carry their formula.

Keys: the securities-report data is by EDINET filer (four-digit securities
code); the rate-risk data by the FSA 金融機関コード. The two are linked here
by name, plus a short table for the banks whose annual report is filed by
a holding company (Yokohama → Yokohama FG). `fi:0134` addresses a bank by
its FSA code on every endpoint that takes a code.
"""
import datetime
import re
import unicodedata

from fastapi import APIRouter, HTTPException, Query

from . import asof
from .equity_api import NAME_CTES, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/banks", tags=["Banks"])

PROVENANCE = {
    "trust": "official",
    "note": ("Maturity ladder and unrealised gains exactly as printed in the "
             "financial-instruments and securities notes of each bank's 有価証券報告書 "
             "(EDINET); rate risk (ΔEVE, ΔNII, Tier 1) exactly as printed in the "
             "IRRBB1 table of each bank's own Basel III Pillar 3 disclosure, read "
             "from the PDF the bank publishes, with the PDF's SHA-256 and page "
             "recorded and the table text archived."),
}
CALC = {
    "bond_loss_to_equity": ("available-for-sale bond difference (book value − cost, "
                            "as printed) ÷ consolidated net assets (純資産, as filed in "
                            "the same report) × 100"),
    "over_10y_share": ("securities maturing in over 10 years ÷ the sum of all six "
                       "maturity buckets of the securities row × 100; the ladder is "
                       "principal amounts, not book values, as the note states"),
    "delta_eve_to_tier1": ("最大値 ΔEVE (the largest of the six scenarios, as "
                           "printed) ÷ Tier 1 capital (as printed in the same table) × 100"),
    "note": ("Derived measures are divisions of two printed figures from one "
             "document and are labelled derived; nothing is estimated."),
}

# Banks whose annual report is filed by a listed holding company: FSA
# 金融機関コード → the filer's securities code. Everything else links by name.
HOLDCO = {
    "0017": "8308", "0159": "8308", "0562": "8308",            # Resona
    "0116": "8377", "0144": "8377",                            # Hokuhoku FG
    "0117": "7384",                                            # Procrea
    "0120": "8713", "0121": "8713",                            # Fidea
    "0129": "7167", "0130": "7167",                            # Mebuki
    "0137": "7173",                                            # Tokyo Kiraboshi
    "0138": "7186", "0525": "7186", "0530": "7186",            # Yokohama FG
    "0140": "7327",                                            # Daishi Hokuetsu
    "0143": "8359", "0533": "8359",                            # Hachijuni Nagano
    "0146": "7381",                                            # CCI (Hokkoku)
    "0149": "5831",                                            # Shizuoka FG
    "0153": "7380",                                            # Juroku
    "0154": "7322",                                            # San ju San
    "0158": "5844",                                            # Kyoto FG
    "0161": "8714",                                            # Ikeda Senshu
    "0168": "5832",                                            # Chugin
    "0169": "7337",                                            # Hirogin
    "0170": "8418", "0191": "8418", "0569": "8418",            # Yamaguchi FG
    "0174": "5830",                                            # Iyogin
    "0177": "8354", "0181": "8354", "0582": "8354", "0587": "8354",  # Fukuoka FG
    "0182": "7180", "0185": "7180",                            # Kyushu FG
    "0188": "7350",                                            # Okinawa FG
    "0190": "7189", "0585": "7189",                            # Nishi-Nippon FH
    "0508": "7161", "0512": "7161",                            # Jimoto
    "0542": "7389",                                            # Aichi FG
    "0572": "8600", "0573": "8600",                            # Tomony
    "0537": "8362",                                            # Fukuho → Fukui Bank
    # City, trust and other banks: not on the FSA's regional list, so linked
    # here only. Codes are the JBA's (jba-banks), checked against its names.
    "0001": "8411", "0289": "8411",                            # Mizuho Bank, Mizuho Trust → Mizuho FG
    "0005": "8306", "0288": "8306",                            # MUFG Bank, MUFG Trust → MUFG
    "0009": "8316",                                            # SMBC → SMFG
    "0294": "8309",                                            # SuMi Trust Bank → SuMi Trust Group
    "0010": "8308",                                            # Resona Bank → Resona
    "0397": "8303", "0398": "8304", "0034": "8410",            # SBI Shinsei, Aozora, Seven Bank (file themselves)
}

TABLES = ("eq_bank_filings", "eq_bank_maturity", "eq_bank_securities")
IRRBB_TABLES = ("eq_bank_irrbb", "eq_bank_irrbb_sources")
BUCKET_COLS = ["within_1y_yen", "y1_3_yen", "y3_5_yen", "y5_7_yen", "y7_10_yen", "over_10y_yen"]
BUCKET_LABELS = {
    "standard6": ["Within 1 year", "1–3 years", "3–5 years", "5–7 years", "7–10 years", "Over 10 years"],
    "5_open7": ["Within 1 year", "1–3 years", "3–5 years", "5–7 years", "Over 7 years", None],
}
SCENARIO_LABELS = {
    "parallel_up": "Parallel up", "parallel_down": "Parallel down",
    "steepener": "Steepener", "flattener": "Flattener",
    "short_up": "Short rate up", "short_down": "Short rate down", "max": "Largest of the six",
}


def _tables_present(cur, names):
    got = _rows(cur, "SELECT table_name FROM information_schema.tables WHERE table_name IN (%s)"
                % ",".join(["?"] * len(names)), list(names))
    return len(got) == len(names)


def _require():
    cur = _cur()
    if not _tables_present(cur, TABLES):
        raise HTTPException(503, "bank balance-sheet dataset not published on this server yet")
    return cur


def _has_irrbb(cur):
    return _tables_present(cur, IRRBB_TABLES)


def _norm_name(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(u"株式会社|\\s+|　", "", s)
    return s


_FI_NAME_RE = re.compile(r"^(.*?) \((Regional bank|Regional bank II|Shinkin bank|Credit co-operative), (\d{4})\) — Deposits$")


_FI_TYPE = {"Regional bank": "regional-1", "Regional bank II": "regional-2",
            "Shinkin bank": "shinkin", "Credit co-operative": "shinkumi"}


def _fsa_institutions(cur):
    """[(fi_code, name_ja, fi_type)] from the FSA list ingested as
    fsa-regional-fi in the macro store, whose series names carry the code,
    the type and the Japanese name; the rate-risk sources table is the
    fallback when the macro store is not there."""
    out = []
    try:
        from . import db
        mc = db.read_cursor()
        mc.execute("""
            SELECT name_en FROM series
            WHERE dataset = 'fsa-regional-fi' AND code LIKE '%.deposits'""")
        for (name_en,) in mc.fetchall():
            m = _FI_NAME_RE.match(name_en or "")
            if m:
                out.append((m.group(3), m.group(1), _FI_TYPE.get(m.group(2))))
    except Exception:  # noqa: BLE001 — the macro store may not be there
        out = []
    if not out and _has_irrbb(cur):
        out = [(r["fi_code"], r["name_ja"], r["fi_type"]) for r in
               _rows(cur, "SELECT fi_code, name_ja, fi_type FROM eq_bank_irrbb_sources")]
    return out


def _links(cur):
    """{fi_code: sec_code} and {sec_code: [fi_code]} between the FSA's
    institution list and the EDINET filers: the holding-company table first,
    then an exact match on the bank's name."""
    fis = [{"fi_code": c, "name_ja": n, "fi_type": t} for c, n, t in _fsa_institutions(cur)]
    if not fis:
        return {}, {}
    ents = _rows(cur, "SELECT sec_code, name_ja FROM eq_entities WHERE sec_code IS NOT NULL")
    by_name = {}
    for e in ents:
        by_name.setdefault(_norm_name(e["name_ja"]), e["sec_code"][:4])
    fi_to_sec, sec_to_fi = {}, {}
    for f in fis:
        sec = HOLDCO.get(f["fi_code"]) or by_name.get(_norm_name(f["name_ja"]))
        if sec:
            fi_to_sec[f["fi_code"]] = sec
            sec_to_fi.setdefault(sec, []).append(f["fi_code"])
    # banks the FSA's regional list does not carry (city and trust banks)
    for fi, sec in HOLDCO.items():
        if fi not in fi_to_sec:
            fi_to_sec[fi] = sec
            sec_to_fi.setdefault(sec, []).append(fi)
    return fi_to_sec, sec_to_fi


def _group_of(cur):
    """{sec_code: 'regional' | 'other'} — a filer counts as regional when
    every bank the FSA lists under it is a regional or second-tier regional
    bank (Resona, which owns two regionals and a city bank, is not)."""
    fis = {c: t for c, _n, t in _fsa_institutions(cur)}
    _, sec_to_fi = _links(cur)
    out = {}
    for sec, codes in sec_to_fi.items():
        types = {fis.get(c) for c in codes}
        out[sec] = "regional" if types and types <= {"regional-1", "regional-2"} else "other"
    return out


def _resolve(cur, code):
    """A securities code, or fi:XXXX → the filer's securities code."""
    code = (code or "").strip()
    if code.lower().startswith("fi:"):
        fi = code[3:]
        fi_to_sec, _ = _links(cur)
        sec = fi_to_sec.get(fi)
        if not sec:
            raise HTTPException(404, "no annual-report filer linked to institution %s" % fi)
        return sec
    return code[:4]


def _year_params(year):
    y = (year or "").strip() or None
    return [y, y]


_LATEST = """
    WITH latest AS (
        SELECT * FROM (
            SELECT f.*, row_number() OVER (PARTITION BY sec_code
                                           ORDER BY period_end DESC, filed_date DESC) AS rn
            FROM eq_bank_filings f
            WHERE sec_code IS NOT NULL AND status IN ('clean','partial')/*ASOF*/
              AND (CAST(? AS VARCHAR) IS NULL
                   OR CAST(year(period_end) AS VARCHAR) = CAST(? AS VARCHAR))
        ) WHERE rn = 1
    ),
    equity AS (
        SELECT doc_id, equity_yen, total_assets_yen FROM eq_filings
    ),
    over10 AS (
        SELECT doc_id,
               sum(over_10y_yen) AS over_10y_yen,
               sum(coalesce(within_1y_yen,0)+coalesce(y1_3_yen,0)+coalesce(y3_5_yen,0)
                   +coalesce(y5_7_yen,0)+coalesce(y7_10_yen,0)+coalesce(over_10y_yen,0)) AS ladder_yen,
               max(bucket_scheme) AS bucket_scheme
        FROM eq_bank_maturity
        WHERE year_offset = 0 AND side = 'assets' AND item_key = 'securities' AND parent_key IS NULL
        GROUP BY doc_id
    )
"""


def _latest_sql():
    return _LATEST.replace("/*ASOF*/", asof.clause("filed_date", ""))


def _irrbb_latest(cur):
    """The newest clean-or-partial IRRBB reading per institution and basis."""
    if not _has_irrbb(cur):
        return {}
    rows = _rows(cur, """
        SELECT r.fi_code, r.name_ja, r.basis, r.as_of, r.scenario, r.delta_eve_yen,
               r.delta_nii_yen, r.tier1_yen, r.status, r.detail, r.source_url, r.page,
               s.fi_type, s.pdf_sha256
        FROM eq_bank_irrbb r JOIN eq_bank_irrbb_sources s USING (fi_code)
        WHERE r.year_offset = 0
        ORDER BY r.fi_code, r.basis, r.ord""")
    out = {}
    for r in rows:
        key = (r["fi_code"], r["basis"])
        d = out.setdefault(key, {
            "fi_code": r["fi_code"], "name_ja": r["name_ja"], "fi_type": r["fi_type"],
            "basis": r["basis"], "as_of": r["as_of"], "status": r["status"], "detail": r["detail"],
            "source_url": r["source_url"], "page": r["page"], "pdf_sha256": r["pdf_sha256"],
            "tier1_yen": r["tier1_yen"], "scenarios": []})
        d["scenarios"].append({
            "scenario": r["scenario"], "label": SCENARIO_LABELS.get(r["scenario"], r["scenario"]),
            "delta_eve_yen": r["delta_eve_yen"], "delta_nii_yen": r["delta_nii_yen"]})
        if r["scenario"] == "max":
            d["delta_eve_max_yen"] = r["delta_eve_yen"]
            d["delta_nii_max_yen"] = r["delta_nii_yen"]
        if r["scenario"] == "parallel_up":
            d["delta_eve_parallel_up_yen"] = r["delta_eve_yen"]
            d["delta_nii_parallel_up_yen"] = r["delta_nii_yen"]
    for d in out.values():
        mx, t1 = d.get("delta_eve_max_yen"), d.get("tier1_yen")
        d["delta_eve_to_tier1_pct"] = (round(mx / t1 * 100.0, 2)
                                       if mx is not None and t1 else None)
    return out


@router.get("/summary")
def summary(year: str = Query("")):
    """How many banks, how recent, and the group totals of what they printed."""
    cur = _require()
    p = _year_params(year)
    head = _rows(cur, _latest_sql() + """
        SELECT count(*) AS banks,
               sum(CASE WHEN status = 'clean' THEN 1 ELSE 0 END) AS clean,
               max(period_end) AS latest_period_end,
               max(filed_date) AS latest_filed,
               sum(CASE WHEN status = 'clean' THEN afs_bond_diff_yen END) AS afs_bond_diff_yen_clean_sum,
               sum(CASE WHEN status = 'clean' THEN afs_jgb_diff_yen END) AS afs_jgb_diff_yen_clean_sum,
               sum(CASE WHEN status = 'clean' THEN afs_diff_yen END) AS afs_diff_yen_clean_sum
        FROM latest""", p)[0]
    # The same sums for the regional banks alone — the group the Banks page
    # follows — over filers the FSA list places wholly in that group.
    group = _group_of(cur)
    rows = _rows(cur, _latest_sql() + """
        SELECT sec_code, status, afs_bond_diff_yen, afs_jgb_diff_yen, afs_diff_yen, period_end
        FROM latest""", p)
    reg = [r for r in rows if group.get(r["sec_code"]) == "regional"]
    clean = [r for r in reg if r["status"] == "clean"]

    def _sum(key):
        vals = [r[key] for r in clean if r[key] is not None]
        return sum(vals) if vals else None
    head["regional_banks"] = {
        "filers": len(reg), "clean": len(clean),
        "afs_bond_diff_yen_clean_sum": _sum("afs_bond_diff_yen"),
        "afs_jgb_diff_yen_clean_sum": _sum("afs_jgb_diff_yen"),
        "afs_diff_yen_clean_sum": _sum("afs_diff_yen"),
        "note": ("filers every one of whose FSA-listed banks is a regional or second-tier "
                 "regional bank; a holding company that also owns a city bank is not counted"),
    }
    irrbb = _irrbb_latest(cur)
    cons = [d for d in irrbb.values() if d["basis"] == "consolidated"]
    ratios = [d["delta_eve_to_tier1_pct"] for d in cons if d["delta_eve_to_tier1_pct"] is not None]
    head["irrbb"] = {
        "institutions": len(set(d["fi_code"] for d in irrbb.values())),
        "latest_as_of": max((d["as_of"] for d in irrbb.values() if d["as_of"]), default=None),
        "delta_eve_to_tier1_pct_median": (sorted(ratios)[len(ratios) // 2] if ratios else None),
        "above_15pct": sum(1 for r in ratios if r > 15.0),
    }
    head["provenance"] = PROVENANCE
    head["calc"] = dict(CALC, group_sums=("sums over the banks whose filing passed every gate "
                                          "('clean'); a bank with a partial filing is not in the sum"))
    return head


@router.get("/companies")
def companies(q: str = Query("", max_length=100), year: str = Query(""), limit: int = Query(200, ge=1, le=500)):
    """Every bank with a parsed filing, newest first, with the headline lines."""
    cur = _require()
    p = _year_params(year)
    like = "%" + q.strip() + "%"
    rows = _rows(cur, _latest_sql() + NAME_CTES + """
        SELECT l.sec_code, l.filer_name, en.name_en, l.period_end, l.filed_date, l.status, l.detail,
               l.basis, l.securities_yen, l.loans_yen, l.deposits_yen,
               l.afs_book_yen, l.afs_diff_yen, l.afs_bond_diff_yen, l.afs_jgb_diff_yen,
               l.var_banking_yen, e.equity_yen, o.over_10y_yen, o.ladder_yen, o.bucket_scheme
        FROM latest l
        LEFT JOIN equity e USING (doc_id)
        LEFT JOIN over10 o USING (doc_id)
        LEFT JOIN en_scode en ON en.sec_code = l.sec_code
        WHERE (? = '%%' OR l.filer_name LIKE ? OR l.sec_code LIKE ? OR en.name_en ILIKE ?)
        ORDER BY l.filer_name LIMIT ?""", p + [like, like, like, like, limit])
    _, sec_to_fi = _links(cur)
    irrbb = _irrbb_latest(cur)
    for r in rows:
        _derive(r)
        r["fi_codes"] = sec_to_fi.get(r["sec_code"], [])
        cons = [irrbb.get((fi, "consolidated")) or irrbb.get((fi, "non-consolidated")) for fi in r["fi_codes"]]
        cons = [c for c in cons if c]
        r["irrbb"] = ([{"fi_code": c["fi_code"], "name_ja": c["name_ja"], "basis": c["basis"],
                        "as_of": c["as_of"], "delta_eve_max_yen": c.get("delta_eve_max_yen"),
                        "tier1_yen": c.get("tier1_yen"),
                        "delta_eve_to_tier1_pct": c.get("delta_eve_to_tier1_pct"),
                        "status": c["status"]} for c in cons])
    return {"count": len(rows), "companies": rows, "provenance": PROVENANCE, "calc": CALC}


def _derive(r):
    bond, eq = r.get("afs_bond_diff_yen"), r.get("equity_yen")
    r["bond_loss_to_equity_pct"] = round(bond / eq * 100.0, 2) if bond is not None and eq else None
    o, l = r.get("over_10y_yen"), r.get("ladder_yen")
    r["over_10y_share_pct"] = (round(o / l * 100.0, 2)
                               if o is not None and l and r.get("bucket_scheme") == "standard6" else None)


@router.get("/company/{sec_code}")
def company(sec_code: str, year: str = Query("")):
    """One bank: its maturity ladder, funding by term, unrealised gains by
    type, its own VaR sentence, and its Basel rate-risk table.

    The parameter is named `sec_code` because that is what every company-shaped
    dataset here is addressed by, and what the MCP company tool passes. This one
    also accepts `fi:0134`, the FSA institution code, which resolves to whichever
    filer files that bank's annual report."""
    cur = _require()
    sec = _resolve(cur, sec_code)
    p = _year_params(year)
    f = _rows(cur, _latest_sql() + NAME_CTES + """
        SELECT l.*, en.name_en, e.equity_yen, e.total_assets_yen,
               o.over_10y_yen, o.ladder_yen, o.bucket_scheme
        FROM latest l
        LEFT JOIN equity e USING (doc_id)
        LEFT JOIN over10 o USING (doc_id)
        LEFT JOIN en_scode en ON en.sec_code = l.sec_code
        WHERE l.sec_code = ?""", p + [sec])
    if not f:
        raise HTTPException(404, "no bank balance-sheet filing for %s" % sec_code)
    f = f[0]
    f.pop("rn", None)
    _derive(f)
    mats = _rows(cur, """
        SELECT year_offset, basis, side, ord, label_ja, item_key, parent_key,
               within_1y_yen, y1_3_yen, y3_5_yen, y5_7_yen, y7_10_yen, over_10y_yen, bucket_scheme
        FROM eq_bank_maturity WHERE doc_id = ? AND basis = ?
        ORDER BY side, year_offset DESC, ord""", [f["doc_id"], f["basis"]])
    for m in mats:
        m["bucket_labels"] = BUCKET_LABELS.get(m["bucket_scheme"], BUCKET_LABELS["standard6"])
    f["maturity"] = [m for m in mats if m["side"] == "assets"]
    f["funding"] = [m for m in mats if m["side"] == "funding"]
    f["securities"] = _rows(cur, """
        SELECT year_offset, basis, category, block, ord, label_ja, type_key,
               book_yen, cost_yen, fair_yen, diff_yen
        FROM eq_bank_securities WHERE doc_id = ? AND basis = ?
        ORDER BY category, year_offset DESC, ord""", [f["doc_id"], f["basis"]])
    f["history"] = _rows(cur, """
        SELECT doc_id, period_end, filed_date, status, securities_yen, loans_yen, deposits_yen,
               afs_diff_yen, afs_bond_diff_yen, afs_jgb_diff_yen, var_banking_yen
        FROM eq_bank_filings WHERE sec_code = ? AND status IN ('clean','partial')"""
        + asof.clause("filed_date") + " ORDER BY period_end", [sec])
    _, sec_to_fi = _links(cur)
    irrbb = _irrbb_latest(cur)
    f["fi_codes"] = sec_to_fi.get(sec, [])
    f["irrbb"] = [irrbb[k] for k in sorted(irrbb) if k[0] in f["fi_codes"]]
    if f["status"] == "partial":
        f["warning"] = ("A validation gate failed on this filing (%s); figures are as "
                        "printed but the bank is excluded from group sums and rankings."
                        % f["detail"])
    f["provenance"] = PROVENANCE
    f["calc"] = CALC
    return f


METRICS = {
    "afs_bond_diff": ("Largest unrealised loss on available-for-sale bonds", "afs_bond_diff_yen", "asc", "official"),
    "afs_jgb_diff": ("Largest unrealised loss on JGBs", "afs_jgb_diff_yen", "asc", "official"),
    "afs_diff": ("Largest unrealised loss on all available-for-sale securities", "afs_diff_yen", "asc", "official"),
    "bond_loss_to_equity": ("Unrealised bond loss as a share of net assets", "bond_loss_to_equity_pct", "asc", "derived"),
    "over_10y_share": ("Largest share of securities maturing beyond ten years", "over_10y_share_pct", "desc", "derived"),
    "securities": ("Largest securities book (by maturity ladder principal)", "securities_yen", "desc", "official"),
    "var_banking": ("Largest banking-book value at risk, as stated", "var_banking_yen", "desc", "official"),
}


@router.get("/ranking")
def ranking(metric: str = Query("afs_bond_diff"), year: str = Query(""),
            limit: int = Query(50, ge=1, le=200)):
    """Banks ranked on one printed line or one derived ratio; clean filings only."""
    if metric not in METRICS:
        raise HTTPException(400, "metric must be one of %s" % ", ".join(sorted(METRICS)))
    title, field, order, trust = METRICS[metric]
    cur = _require()
    p = _year_params(year)
    rows = _rows(cur, _latest_sql() + NAME_CTES + """
        SELECT l.sec_code, l.filer_name, en.name_en, l.period_end, l.status,
               l.securities_yen, l.afs_diff_yen, l.afs_bond_diff_yen, l.afs_jgb_diff_yen,
               l.var_banking_yen, e.equity_yen, o.over_10y_yen, o.ladder_yen, o.bucket_scheme
        FROM latest l
        LEFT JOIN equity e USING (doc_id)
        LEFT JOIN over10 o USING (doc_id)
        LEFT JOIN en_scode en ON en.sec_code = l.sec_code
        WHERE l.status = 'clean'""", p)
    for r in rows:
        _derive(r)
    rows = [r for r in rows if r.get(field) is not None]
    rows.sort(key=lambda r: r[field], reverse=(order == "desc"))
    for i, r in enumerate(rows[:limit], start=1):
        r["rank"] = i
    return {"metric": metric, "title": title, "field": field, "trust": trust,
            "calc": CALC.get(metric), "count": len(rows), "rows": rows[:limit],
            "provenance": PROVENANCE}


@router.get("/irrbb")
def irrbb(fi_type: str = Query(""), basis: str = Query("consolidated"),
          limit: int = Query(500, ge=1, le=1000)):
    """Every institution's Basel rate-risk table, newest reading, ranked on
    ΔEVE as a share of Tier 1 — the supervisor's own yardstick."""
    cur = _cur()
    if not _has_irrbb(cur):
        raise HTTPException(503, "rate-risk dataset not published on this server yet")
    data = _irrbb_latest(cur)
    fi_to_sec, _ = _links(cur)
    out = []
    seen = set()
    for (fi, b), d in sorted(data.items()):
        if fi in seen:
            continue
        alt = data.get((fi, basis)) or d
        seen.add(fi)
        if fi_type and alt["fi_type"] != fi_type:
            continue
        row = dict(alt)
        row["sec_code"] = fi_to_sec.get(fi)
        out.append(row)
    out.sort(key=lambda r: (r["delta_eve_to_tier1_pct"] is None,
                            -(r["delta_eve_to_tier1_pct"] or 0)))
    src = _rows(cur, "SELECT status, count(*) AS n FROM eq_bank_irrbb_sources GROUP BY status")
    return {"count": len(out), "rows": out[:limit], "coverage": src,
            "calc": {"delta_eve_to_tier1_pct": CALC["delta_eve_to_tier1"],
                     "materiality": ("The Basel standard flags a bank whose largest ΔEVE exceeds "
                                     "15% of Tier 1 (international standard) — Japan's domestic "
                                     "standard uses 20% of core capital — as an outlier for "
                                     "supervisory review; it is a threshold, not a breach.")},
            "provenance": PROVENANCE}


@router.get("/irrbb/sources")
def irrbb_sources():
    """Where each institution's rate-risk table was found, or why it was not."""
    cur = _cur()
    if not _has_irrbb(cur):
        raise HTTPException(503, "rate-risk dataset not published on this server yet")
    return {"rows": _rows(cur, """
        SELECT fi_code, name_ja, fi_type, disclosure_url, status, detail, source_url,
               pdf_sha256, page, as_of, unit_label, tables, pdfs_tried, pages_crawled,
               collected_at, parser_version
        FROM eq_bank_irrbb_sources ORDER BY fi_type, fi_code""")}


from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "bank-balance",
    "section": "banking",
    "name": {"en": "Bank bond maturities, unrealised losses and rate risk",
             "ja": "銀行の有価証券残存期間・評価損益・金利リスク（IRRBB）"},
    "shape": "company",
    "summary": ("For every bank filing an annual report: securities and loans by "
                "remaining term, deposits by term, unrealised gains and losses on "
                "available-for-sale securities by type, and the bank's own banking-book "
                "VaR — from the notes to the securities report; plus each bank's Basel III "
                "IRRBB1 table (ΔEVE, ΔNII, Tier 1) read from its own Pillar 3 disclosure."),
    "source": dict(_EDINET_SOURCE,
                   document=("有価証券報告書 · 金融商品関係 / 有価証券関係 notes (annual "
                             "securities report), and each bank's 自己資本の充実の状況 "
                             "(Basel III Pillar 3) disclosure PDF"),
                   credit=("Source: company filings on EDINET (Financial Services Agency "
                           "of Japan) and each bank's own Pillar 3 disclosure.")),
    "keys": ["sec_code", "fiscal_year"],
    "frequency": "per-filing",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "FY2021", "stale_after_days": None,
    },
    "measures": [
        {"id": "maturity_yen", "label": "Securities, loans and deposits by remaining term (six buckets)",
         "unit": "JPY", "trust": "official"},
        {"id": "afs_diff_yen", "label": "Unrealised gain or loss on available-for-sale securities, by type",
         "unit": "JPY", "trust": "official"},
        {"id": "var_banking_yen", "label": "Banking-book value at risk, as stated by the bank",
         "unit": "JPY", "trust": "official"},
        {"id": "delta_eve_yen", "label": "ΔEVE under the six Basel rate shocks and the largest of them",
         "unit": "JPY", "trust": "official"},
        {"id": "delta_nii_yen", "label": "ΔNII under the parallel shocks",
         "unit": "JPY", "trust": "official"},
        {"id": "tier1_yen", "label": "Tier 1 capital, as printed in the IRRBB table",
         "unit": "JPY", "trust": "official"},
        {"id": "bond_loss_to_equity_pct", "label": "Unrealised bond loss as a share of net assets",
         "unit": "%", "trust": "derived", "calc": CALC["bond_loss_to_equity"]},
        {"id": "over_10y_share_pct", "label": "Share of securities maturing beyond ten years",
         "unit": "%", "trust": "derived", "calc": CALC["over_10y_share"]},
        {"id": "delta_eve_to_tier1_pct", "label": "Largest ΔEVE as a share of Tier 1",
         "unit": "%", "trust": "derived", "calc": CALC["delta_eve_to_tier1"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/banks/company/{sec_code}",
        "search": "/api/v1/equity/banks/companies",
        "summary": "/api/v1/equity/banks/summary",
        "screen": "/api/v1/equity/banks/ranking",
        "irrbb": "/api/v1/equity/banks/irrbb",
        "irrbb_sources": "/api/v1/equity/banks/irrbb/sources",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [{"id": k, "title": v[0]} for k, v in METRICS.items()],
    "cite": "/banks.html?code={sec_code}",
    "page": "/banks.html",
    "notes": [PROVENANCE["note"], CALC["note"],
              "The maturity ladder is principal amounts, as the note states, and so does not "
              "equal the balance-sheet book value of securities.",
              "A bank whose annual report is filed by a holding company is addressed by the "
              "holding company's securities code; fi:0138 (Yokohama Bank's FSA code) resolves "
              "to 7186 (Yokohama FG). The rate-risk table is keyed by the FSA code because the "
              "bank, not the holding company, publishes it."],
}
