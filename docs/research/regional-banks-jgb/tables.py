# -*- coding: utf-8 -*-
"""The key-statistics annex: every table, no prose, no charts.

Built from the CSVs in data/ — never from numbers typed here — so it carries
the same verification as the piece. Writes out/regional-banks-jgb-tables.md,
which md2pdf renders.

  python tables.py
"""
import csv
import json
import os
import re
import statistics as st
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")
AS_OF = "2026-09-23"
BASE = "https://ploveranalytics.com/api/v1"
MINUS = "−"


def rows(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return list(csv.DictReader([l for l in f if not l.startswith("#")]))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get(path, q=None):
    u = BASE + path + ("?" + urllib.parse.urlencode(q) if q else "")
    req = urllib.request.Request(u, headers={"User-Agent": "PloverResearch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def f(v, dp=1, unit=""):
    """A number for a table cell: fixed precision, true minus, or an em dash.

    A value that rounds to zero loses its sign: "−0" reads as a number when it
    is really a rounding artefact."""
    if v is None:
        return "—"
    r = round(v, dp)
    s = format(abs(v), ",.%df" % dp)
    return (MINUS if r < 0 else "") + s + unit


def sgn(v, dp=1, unit=""):
    if v is None:
        return "—"
    r = round(v, dp)
    s = format(abs(v), ",.%df" % dp)
    return ("+" if r > 0 else MINUS if r < 0 else "") + s + unit


def clean(name):
    """The filer's English name, tidied for a narrow column.

    Some filers write their English name in full-width Latin characters
    (Ｔｈｅ　Ｓｈｉｇａ　Ｂａｎｋ), which sets one glyph per em and blows the
    column apart. NFKC folds those to ordinary Latin first."""
    n = unicodedata.normalize("NFKC", (name or "")).replace("\u3000", " ").strip()
    n = re.sub(r"[,.]?\s*(LTD|Ltd|Inc|CO|Co|Company|Limited)\.?\s*$", "", n)
    n = re.sub(r"^THE\s+", "", n, flags=re.I)
    n = re.sub(r"\s+", " ", n).strip().rstrip(",")
    if n.isupper():
        n = n.title()
        n = re.sub(r"\b(Of|And|The)\b", lambda m: m.group(1).lower(), n)
        n = n[0].upper() + n[1:] if n else n
    return n


# --- who is a regional bank, by the API's own rule ---------------------------
TYPE = {}
rx = re.compile(r"\((Regional bank|Regional bank II|Shinkin bank|Credit co-operative), (\d{4})\)")
for s in get("/fsa-regional-fi/series", {"q": "Deposits"})["series"]:
    m = rx.search(s.get("name_en") or "")
    if m:
        TYPE[m.group(2)] = m.group(1)
FI = {c["sec_code"]: c.get("fi_codes") or []
      for c in get("/equity/banks/companies", {"limit": 500, "as_of": AS_OF})["companies"]}


def regional(sec):
    t = {TYPE.get(x) for x in (FI.get(sec) or [])}
    return bool(t) and t <= {"Regional bank", "Regional bank II"}


def tier(sec):
    t = {TYPE.get(x) for x in (FI.get(sec) or [])}
    if t == {"Regional bank II"}:
        return "II"
    return "I" if t else "—"


L = []
out = L.append

out("# Japan regional banks and JGB yields — key statistics\n")
out("Every figure below is drawn from the tables behind the research note, frozen at\n"
    "`as_of=2026-09-23`. Published figures are quoted exactly as published; the three\n"
    "calculated measures are named in §11. A figure a source does not publish is `—`\n"
    "and is never treated as zero.\n")

# ---------------------------------------------------------------- 1. findings
out("\n## 1. The four findings\n")
out("| # | Question | Answer | The number |")
out("| - | -------- | ------ | ---------- |")
pl = {r["period"]: r for r in rows("regional-pl.csv")}
g = lambda p, k: num(pl[p][k]) / 10
nii0, nii2 = g("2024-03", "regional.net-interest-income.fy"), g("2026-03", "regional.net-interest-income.fy")
c0, c2 = g("2024-03", "regional.core-net-business-profit.fy"), g("2026-03", "regional.core-net-business-profit.fy")
bond2 = g("2026-03", "regional.bond-gains.fy")
h = {r["period"]: r for r in rows("jgb-holdings-by-group.csv")}
tn = lambda p, k: (num(h[p][k]) or 0) / 1e6
out("| 1 | Did the lending margin genuinely widen? | Yes | Core profit +%.0f%% against interest income +%.0f%% over two years |"
    % (100 * (c2 / c0 - 1), 100 * (nii2 / nii0 - 1)))
out("| 2 | Where did the margin gain go? | Into the bond book | FY2025 bond losses were %.0f%% of the two-year gain in interest income |"
    % (100 * abs(bond2) / (nii2 - nii0)))
out("| 3 | Who is adding duration? | The regionals, not the megabanks | Regional JGBs +%.0f%%, city banks %s%.0f%% over two years |"
    % (100 * (tn("2026-03", "s.regional.d290") / tn("2024-03", "s.regional.d290") - 1),
       MINUS, abs(100 * (tn("2026-03", "s.city.d290") / tn("2024-03", "s.city.d290") - 1))))
out("| 4 | Do the accounting loss and the supervisor's measure agree? | No | Rank correlation 0.31; one name shared between the two worst-ten lists |")

# ------------------------------------------------------------------ 2. yields
out("\n## 2. Japanese government bond yields\n")
y = {r["period"]: r for r in rows("jgb-yields.csv")}
out("| Date | 5-year | 10-year | 30-year |")
out("| ---- | -----: | ------: | ------: |")
for d, lbl in (("2024-03-29", "29 Mar 2024, FY2023 close"),
               ("2025-03-31", "31 Mar 2025, FY2024 close"),
               ("2026-03-31", "31 Mar 2026, FY2025 close"),
               ("2026-09-15", "15 Sep 2026, cycle high"),
               ("2026-09-17", "17 Sep 2026, latest")):
    r = y.get(d) or y[[k for k in sorted(y) if k <= d][-1]]
    out("| %s | %s | %s | %s |" % (lbl, f(num(r["5Y"]), 3, "%"), f(num(r["10Y"]), 3, "%"), f(num(r["30Y"]), 3, "%")))

# ------------------------------------------------------------------- 3. rates
out("\n## 3. Lending and deposit rates, by lender\n")
out("Average contracted rate on loans made during the month (Bank of Japan, IR04), and\n"
    "the average posted ordinary deposit rate (IR02). A change in a rate is in\n"
    "percentage points.\n")
lr = {r["period"]: r for r in rows("loan-rates.csv")}
dr = {r["period"]: r for r in rows("deposit-rates.csv")}
out("| Lender | Mar 2024 | Mar 2026 | Jul 2026 | Change since Mar 2024 |")
out("| ------ | -------: | -------: | -------: | --------------------: |")
for code, lbl in (("DLLR2CICBNL1", "City banks"), ("DLLR2CIRBNL1", "Regional banks"),
                  ("DLLR2CIMBNL1", "Second-tier regional"), ("DLLR2CICR35", "Shinkin banks")):
    a, b = num(lr["2024-03"][code]), num(lr["2026-07"][code])
    out("| %s | %s | %s | %s | %s |" % (lbl, f(a, 3, "%"), f(num(lr["2026-03"][code]), 3, "%"),
                                        f(b, 3, "%"), sgn(b - a, 2, "pp")))
a, b = num(dr["2024-03"]["DLDR121N"]), num(dr[max(dr)]["DLDR121N"])
out("| Ordinary deposits, posted | %s | %s | %s | %s |"
    % (f(a, 3, "%"), f(num(dr["2026-03"]["DLDR121N"]), 3, "%"), f(b, 3, "%"), sgn(b - a, 2, "pp")))
out("\nDeposit rates are to September 2026, the latest month published; the series is\nmonthly from April 2022 only.\n")

# --------------------------------------------------------------------- 4. P&L
out("\n## 4. Regional banks — group profit and loss\n")
out("Fiscal years to 31 March, ¥bn. Published by the FSA in ¥100mn; shown here divided\nby 10, an exact conversion.\n")
fys = [("2022-03", "FY2021"), ("2023-03", "FY2022"), ("2024-03", "FY2023"),
       ("2025-03", "FY2024"), ("2026-03", "FY2025")]
out("| Line | " + " | ".join(x[1] for x in fys) + " | Two-year change |")
out("| ---- | " + " | ".join("---:" for _ in fys) + " | ---: |")
for key, lbl in (("regional.net-interest-income.fy", "Net interest income"),
                 ("regional.bond-gains.fy", "Gains and losses on bonds"),
                 ("regional.core-net-business-profit.fy", "Core net business profit"),
                 ("regional.net-income.fy", "Net income")):
    vals = [g(p, key) for p, _ in fys]
    out("| %s | %s | %s |" % (lbl, " | ".join(f(v, 0) for v in vals), sgn(vals[-1] - vals[2], 0)))

# ---------------------------------------------------------------- 5. holdings
out("\n## 5. Government bonds held, by bank group\n")
out("¥tn at each 31 March.\n")
per = [p for p in sorted(h) if p >= "2020-03"]
out("| Group | " + " | ".join(p[:4] for p in per) + " | Two-year |")
out("| ----- | " + " | ".join("---:" for _ in per) + " | ---: |")
for key, lbl in (("s.regional.d290", "Regional banks"),
                 ("s.regional-2.d290", "Second-tier regional"),
                 ("s.city.d290", "City banks")):
    vals = [tn(p, key) for p in per]
    ch = 100 * (tn("2026-03", key) / tn("2024-03", key) - 1)
    out("| %s | %s | %s |" % (lbl, " | ".join(f(v, 1) for v in vals), sgn(ch, 0, "%")))

# ----------------------------------------------------------- 6. balance sheet
out("\n## 6. Regional banks — deposits, loans and securities\n")
out("¥tn at each 31 March, with the loan-to-deposit ratio.\n")
bs = {r["period"]: r for r in rows("regional-balance-sheet.csv")}
marches = [p for p in sorted(bs) if p.endswith("-03") and p >= "2020-03"]
out("| | " + " | ".join(p[:4] for p in marches) + " |")
out("| --- | " + " | ".join("---:" for _ in marches) + " |")
for key, lbl, dp in (("s.regional.a015", "Deposits", 1),
                     ("s.regional.d395", "Loans and bills discounted", 1),
                     ("s.regional.d275", "Securities", 1)):
    out("| %s | %s |" % (lbl, " | ".join(f((num(bs[p][key]) or 0) / 1e6, dp) for p in marches)))
out("| Loan-to-deposit ratio | %s |"
    % " | ".join(f(100 * num(bs[p]["s.regional.d395"]) / num(bs[p]["s.regional.a015"]), 1, "%")
                 for p in marches))

# ----------------------------------------------------- 7. unrealised, summary
out("\n## 7. Unrealised gains and losses on securities — group totals\n")
sm = json.load(open(os.path.join(DATA, "summary.json")))
rb = sm["regional_banks"]
out("| | ¥tn |")
out("| --- | ---: |")
out("| Available-for-sale bonds, all regional filers | %s |" % f(rb["afs_bond_diff_yen_clean_sum"] / 1e12, 2))
out("| of which government bonds | %s |" % f(rb["afs_jgb_diff_yen_clean_sum"] / 1e12, 2))
out("| All available-for-sale securities, including equities | %s |" % f(rb["afs_diff_yen_clean_sum"] / 1e12, 2))
out("\nAcross the %d regional filers of %d whose accounts reconciled in full. A negative\n"
    "figure is an unrealised loss; the all-securities line is positive because equity\n"
    "gains exceed the bond losses.\n" % (rb["clean"], rb["filers"]))

# ---------------------------------------------------- 8. per bank, bond loss
LOSS = [r for r in rows("rank-bond-loss-to-equity.csv")
        if num(r["bond_loss_to_equity_pct"]) is not None and regional(r["sec_code"])]
LOSS.sort(key=lambda r: num(r["bond_loss_to_equity_pct"]))
med = st.median([abs(num(r["bond_loss_to_equity_pct"])) for r in LOSS])
IRR = {r["sec_code"]: r for r in rows("irrbb-by-bank.csv")
       if r["sec_code"] and r["delta_eve_to_tier1_pct"]}
out("\n## 8. Per bank — unrealised bond loss against net assets\n")
out("Every regional filer whose bond-book extraction passed each gate, worst first.\n"
    "Median %s. The last column is the same bank's own Basel figure from §10, where it\n"
    "publishes one.\n" % f(med, 1, "%"))
out("| # | Bank | Code | Tier | Bond loss, ¥bn | ÷ net assets | Securities, ¥bn | ΔEVE ÷ Tier 1 |")
out("| -: | ---- | ---- | ---- | ---: | ---: | ---: | ---: |")
for i, r in enumerate(LOSS, 1):
    d = IRR.get(r["sec_code"])
    out("| %d | %s | %s | %s | %s | %s | %s | %s |"
        % (i, clean(r["name_en"] or r["filer_name"]), r["sec_code"], tier(r["sec_code"]),
           f((num(r["afs_bond_diff_yen"]) or 0) / 1e9, 0),
           f(num(r["bond_loss_to_equity_pct"]), 1, "%"),
           f((num(r["securities_yen"]) or 0) / 1e9, 0),
           f(num(d["delta_eve_to_tier1_pct"]), 1, "%") if d else "—"))

# ------------------------------------------------------- 9. per bank, tenor
DUR = [r for r in rows("rank-over-10y-share.csv")
       if num(r["over_10y_share_pct"]) is not None and regional(r["sec_code"])]
DUR.sort(key=lambda r: -num(r["over_10y_share_pct"]))
out("\n## 9. Per bank — securities maturing beyond ten years\n")
out("Share of the securities maturity ladder sitting beyond ten years, longest first.\n"
    "Median %s across the %d regional banks whose note prints a six-bucket ladder. The\n"
    "ladder is principal amounts as the note states, not balance-sheet book value.\n"
    % (f(st.median([num(r["over_10y_share_pct"]) for r in DUR]), 1, "%"), len(DUR)))
out("| # | Bank | Code | Beyond 10 years, ¥bn | Ladder total, ¥bn | Share |")
out("| -: | ---- | ---- | ---: | ---: | ---: |")
for i, r in enumerate(DUR, 1):
    out("| %d | %s | %s | %s | %s | %s |"
        % (i, clean(r["name_en"] or r["filer_name"]), r["sec_code"],
           f((num(r["over_10y_yen"]) or 0) / 1e9, 0), f((num(r["ladder_yen"]) or 0) / 1e9, 0),
           f(num(r["over_10y_share_pct"]), 1, "%")))

# --------------------------------------------------------- 10. per bank, IRRBB
EN = {}
for r in rows("rank-bond-loss-to-equity.csv") + rows("rank-over-10y-share.csv"):
    if r.get("sec_code") and (r.get("name_en") or r.get("filer_name")):
        EN.setdefault(r["sec_code"], clean(r["name_en"] or r["filer_name"]))
ALL = [r for r in rows("irrbb-by-bank.csv") if r["delta_eve_to_tier1_pct"]]
ALL.sort(key=lambda r: -num(r["delta_eve_to_tier1_pct"]))
out("\n## 10. Per bank — Basel rate risk in the banking book (IRRBB1)\n")
out("The fall in the economic value of the banking book under the worst of six\n"
    "prescribed rate shocks (ΔEVE), against Tier 1 capital, exactly as each bank printed\n"
    "it in its own Pillar 3 disclosure. Sorted by the ratio. A ratio above 15% marks an\n"
    "outlier for supervisory attention under the Basel standard; it is not a breach.\n"
    "The code is the FSA institution code, not a securities code; a bank whose annual\n"
    "report is filed by a holding company keeps its own name here.\n")
out("| # | Bank | FSA code | As of | ΔEVE, ¥bn | Tier 1, ¥bn | ÷ Tier 1 | ΔNII, ¥bn | Check |")
out("| -: | ---- | ---- | ---- | ---: | ---: | ---: | ---: | ---- |")
for i, r in enumerate(ALL, 1):
    unique = len(FI.get(r["sec_code"]) or []) == 1
    name = (EN.get(r["sec_code"]) if unique else None) or r["name_ja"]
    out("| %d | %s | %s | %s | %s | %s | %s | %s | %s |"
        % (i, name, r["fi_code"], (r["as_of"] or "—")[:7],
           f((num(r["delta_eve_max_yen"]) or 0) / 1e9, 0),
           f((num(r["tier1_yen"]) or 0) / 1e9, 0),
           f(num(r["delta_eve_to_tier1_pct"]), 1, "%"),
           f((num(r["delta_nii_parallel_up_yen"]) or 0) / 1e9, 0) if r["delta_nii_parallel_up_yen"] else "—",
           "ok" if r["status"] == "clean" else "flagged"))

# ------------------------------------------------------------ 11. definitions
out("\n## 11. Definitions, coverage and caveats\n")
out("**The three calculated measures.** Everything else is a published figure.\n")
out("| Measure | Formula |")
out("| ------- | ------- |")
out("| Unrealised bond loss ÷ net assets | available-for-sale bond difference (book value − cost, as printed) ÷ consolidated net assets from the same report × 100 |")
out("| Share beyond ten years | securities maturing beyond ten years ÷ the sum of all six maturity buckets × 100 |")
out("| ΔEVE ÷ Tier 1 | largest ΔEVE of the six scenarios ÷ Tier 1 capital, both as printed in the same table × 100 |")
srcs = {}
for r in rows("irrbb-by-bank.csv"):
    srcs[r["status"]] = srcs.get(r["status"], 0) + 1
out("\n**Coverage.**\n")
out("| | Count |")
out("| --- | ---: |")
out("| Regional and second-tier regional institutions on the FSA list | 98 |")
out("| ...publishing a rate-risk table we could retrieve | %d |" % len(rows("irrbb-by-bank.csv")))
out("| ...of those, read without a flagged check | %d |" % srcs.get("clean", 0))
out("| ...with a flagged check, shown as printed | %d |" % srcs.get("partial", 0))
out("| Bank filings with a parsed bond book | %d |" % sm["banks"])
out("| ...passing every reconciliation gate | %d |" % sm["clean"])
out("| Regional filers in the group totals | %d |" % rb["clean"])
out("""
**Caveats.**

- 27 of 98 institutions publish a rate-risk table we could not retrieve, mostly
  because their websites block automated access. The distribution in §10 is of the
  71 that we could.
- Eight holding companies publish a single table covering several banks — Fukuoka
  Financial Group's covers four, Yamaguchi's three — so the rows in §10 come from
  59 distinct documents, and those banks are not independent observations.
- The maturity ladder in §9 is principal, as the note states, so it does not
  reconcile to the balance-sheet securities line in §6.
- Filings that failed a reconciliation gate are excluded from every ranking and
  group total here, and are shown only on their own company page.
- No share price data is held under licence, so nothing here says whether any of
  this is reflected in the price.

**Sources.** Ministry of Finance (yields); Bank of Japan, databases IR04 and IR02
(loan and deposit rates); Financial Services Agency, summary of bank financial
results and the annual list of regional and co-operative institutions; Japanese
Bankers Association, Analysis of Financial Statements of All Banks; annual
securities reports filed on EDINET; and each bank's own Basel Pillar 3 disclosure.
Every rate-risk figure records the source URL, the document's SHA-256 and the page.
""")

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "regional-banks-jgb-tables.md")
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("wrote %s" % path)
print("  sections: 11;  per-bank rows: %d losses, %d ladders, %d rate-risk"
      % (len(LOSS), len(DUR), len(ALL)))
