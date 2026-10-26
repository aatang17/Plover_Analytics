# -*- coding: utf-8 -*-
"""Bank-level gain and cost of the BoJ's increases, from the saved CSV/JSON in data/.

Every figure written here is a calculation on published values; formulas are in
the method section of the draft and in plan.md."""
import csv, json, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")

def rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))

V = defaultdict(dict)       # code -> {(line, period): value}
META = {}
for r in rows(os.path.join(D, "jba_banks_long.csv")):
    V[r["fi_code"]][(r["line"], r["period"])] = float(r["yen_mn"])
    META[r["fi_code"]] = (r["name_ja"], r["tier"])

FY = {"FY2022": "2023-03", "FY2023": "2024-03", "FY2024": "2025-03", "FY2025": "2026-03"}
HALVES = ["2023-03", "2023-09", "2024-03", "2024-09", "2025-03", "2025-09", "2026-03"]
BOND_PLUS, BOND_MINUS = ("e230.fy", "e240.fy"), ("e610.fy", "e620.fy", "e630.fy")

def nii(c, fy):
    a, b = V[c].get(("e020.fy", FY[fy])), V[c].get(("e350.fy", FY[fy]))
    return None if a is None or b is None else a - b

def bond(c, fy):
    p = FY[fy]
    vals = [V[c].get((l, p)) for l in BOND_PLUS + BOND_MINUS]
    if all(v is None for v in vals):
        return None
    plus = sum(V[c].get((l, p)) or 0.0 for l in BOND_PLUS)
    minus = sum(V[c].get((l, p)) or 0.0 for l in BOND_MINUS)
    return plus - minus

# A bank's code carries a merged bank's history only from the merger date, so
# growth across a merger is not growth. A deposit jump of more than 20% in one
# half-year marks one; 八十二 absorbed 長野 (whose series ends at September
# 2025) with a smaller jump (+10.1% at March 2026), so it is named explicitly.
ABSORBED = {"0143": "2026-03"}


def merger_break(c):
    if c in ABSORBED:
        return ABSORBED[c]
    dep = [V[c].get(("a015", h)) for h in HALVES]
    for a, b, h in zip(dep, dep[1:], HALVES[1:]):
        if a and b and b / a - 1 > 0.20:
            return h
    return None

out = []
for c, (name, tier) in sorted(META.items()):
    if tier == "aggregate":
        continue
    n = {fy: nii(c, fy) for fy in FY}
    b = {fy: bond(c, fy) for fy in FY}
    brk = merger_break(c)
    rec = {"fi_code": c, "name_ja": name, "tier": tier, "merger_break": brk}
    for fy in FY:
        rec["nii_" + fy] = n[fy]; rec["bond_" + fy] = b[fy]
    ok = n["FY2023"] is not None and n["FY2025"] is not None and n["FY2024"] is not None
    rec["gain_fy25_vs_fy23"] = (n["FY2025"] - n["FY2023"]) if ok else None
    rec["gain_pct"] = ((n["FY2025"] / n["FY2023"] - 1) * 100) if ok and n["FY2023"] else None
    rec["gain_cum"] = ((n["FY2024"] - n["FY2023"]) + (n["FY2025"] - n["FY2023"])) if ok else None
    bb = [b["FY2024"], b["FY2025"]]
    rec["bond_cum"] = sum(bb) if all(x is not None for x in bb) else None
    rec["net_cum"] = (rec["gain_cum"] + rec["bond_cum"]) if rec["gain_cum"] is not None and rec["bond_cum"] is not None else None
    rec["net_simple"] = (rec["gain_fy25_vs_fy23"] + rec["bond_cum"]) if rec["gain_fy25_vs_fy23"] is not None and rec["bond_cum"] is not None else None
    loans, dep = V[c].get(("d395", "2024-03")), V[c].get(("a015", "2024-03"))
    rec["ldr_2024_03"] = loans / dep * 100 if loans and dep else None
    rec["net_assets_2026_03"] = V[c].get(("b950", "2026-03"))
    na = rec["net_assets_2026_03"]
    for k in ("gain_cum", "bond_cum", "net_cum"):
        rec[k + "_to_na"] = rec[k] / na * 100 if rec[k] is not None and na else None
    out.append(rec)

# IRRBB (corrected, consolidated preferred)
irr = {}
for f in ("irrbb_nonconsolidated.json", "irrbb_consolidated.json"):
    for r in json.load(open(os.path.join(D, f)))["rows"]:
        irr[r["fi_code"]] = r
for rec in out:
    r = irr.get(rec["fi_code"])
    if not r:
        continue
    t1 = r["tier1_yen"]
    sc = {s["scenario"]: s for s in r["scenarios"]}
    up = sc.get("parallel_up", {})
    rec.update({"irrbb_basis": r["basis"], "irrbb_as_of": r["as_of"], "irrbb_status": r["status"],
                "tier1_yen": t1,
                "eve_max_to_t1": r["delta_eve_to_tier1_pct"],
                "eve_max_scenario": max((s for s in r["scenarios"] if s["scenario"] != "max" and s["delta_eve_yen"] is not None),
                                        key=lambda s: s["delta_eve_yen"], default={}).get("scenario"),
                "eve_up_to_t1": (up.get("delta_eve_yen") / t1 * 100) if up.get("delta_eve_yen") is not None and t1 else None,
                "nii_up_to_t1": (up.get("delta_nii_yen") / t1 * 100) if up.get("delta_nii_yen") is not None and t1 else None})

# unrealised AFS bond losses at filer level
comp = json.load(open(os.path.join(D, "bank_companies.json")))["companies"]
by_fi = {}
for cpy in comp:
    for fi in cpy.get("fi_codes") or []:
        by_fi[fi] = cpy
for rec in out:
    cpy = by_fi.get(rec["fi_code"])
    if cpy:
        rec["filer_sec_code"] = cpy["sec_code"]; rec["filer_name"] = cpy["filer_name"]
        rec["filer_status"] = cpy["status"]; rec["afs_bond_diff_yen"] = cpy["afs_bond_diff_yen"]
        rec["filer_equity_yen"] = cpy["equity_yen"]; rec["bond_loss_to_equity_pct"] = cpy.get("bond_loss_to_equity_pct")
        rec["filer_banks"] = len(cpy.get("fi_codes") or [])

keys = sorted({k for r in out for k in r})
first = ["fi_code", "name_ja", "tier", "merger_break"]
keys = first + [k for k in keys if k not in first]
with open(os.path.join(D, "bank_table.csv"), "w", newline="", encoding="utf-8") as f:
    f.write("# Calculated from data/jba_banks_long.csv (JBA, non-consolidated, ¥mn), data/irrbb_*.json (IRRBB1, irrbb-2) and data/bank_companies.json (EDINET notes). Formulas: see analysis.py and the draft's method notes.\n")
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
    for r in out:
        w.writerow({k: ("" if r.get(k) is None else (round(r[k], 4) if isinstance(r[k], float) else r[k])) for k in keys})
print("banks:", len(out), "with gain:", sum(1 for r in out if r["gain_cum"] is not None),
      "merger breaks:", [(r["fi_code"], r["name_ja"], r["merger_break"]) for r in out if r["merger_break"]])
