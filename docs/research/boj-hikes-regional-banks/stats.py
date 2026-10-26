# -*- coding: utf-8 -*-
"""Every number the draft uses, computed from data/ and written to data/numbers.json."""
import csv, json, os, statistics as st
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "data")

def rows(p):
    return list(csv.DictReader(l for l in open(p, encoding="utf-8") if not l.startswith("#")))
def f(x):
    return None if x in ("", None) else float(x)
def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0] * len(xs); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]: j += 1
        for k in range(i, j + 1): r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r
def spearman(a, b):
    ra, rb = rank(a), rank(b); ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return num / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5

N = {}
# --- rates
cr = {r["month"]: f(r["mean_pct"]) for r in rows(os.path.join(D, "call_rate_monthly.csv"))}
N["call"] = {m: cr[m] for m in ("2024-02", "2024-03", "2024-08", "2025-02", "2026-01", "2026-07", "2026-09")}
lr = rows(os.path.join(D, "loan-rates.csv")); dr = rows(os.path.join(D, "deposit-rates.csv"))
L = {r["period"]: f(r["DLLR2CIRBNL1"]) for r in lr}; L2 = {r["period"]: f(r["DLLR2CIMBNL1"]) for r in lr}
Dp = {r["period"]: f(r["DLDR121N"]) for r in dr}
N["loan_rb"] = {"2024-02": L["2024-02"], "2024-03": L["2024-03"], "2026-07": L["2026-07"]}
N["loan_rb2"] = {"2024-02": L2["2024-02"], "2026-07": L2["2026-07"]}
N["dep"] = {"2024-02": Dp["2024-02"], "2024-03": Dp["2024-03"], "2026-07": Dp["2026-07"], "2026-09": Dp["2026-09"]}
N["loan_rb_chg_pp"] = round(L["2026-07"] - L["2024-02"], 3)
N["dep_chg_pp_to_2026_07"] = round(Dp["2026-07"] - Dp["2024-02"], 3)
N["call_chg_pp_to_2026_07"] = round(cr["2026-07"] - cr["2024-02"], 3)

# --- aggregates
J = {}
for r in rows(os.path.join(D, "jba_banks_long.csv")):
    if r["tier"] == "aggregate": J[(r["fi_code"], r["line"], r["period"])] = f(r["yen_mn"])
FY = {"FY2022": "2023-03", "FY2023": "2024-03", "FY2024": "2025-03", "FY2025": "2026-03"}
agg = {}
for g in ("regional", "regional-2"):
    a = {}
    for fy, p in FY.items():
        nii = J[(g, "e020.fy", p)] - J[(g, "e350.fy", p)]
        bond = sum(J.get((g, l, p)) or 0 for l in ("e230.fy", "e240.fy")) - sum(J.get((g, l, p)) or 0 for l in ("e610.fy", "e620.fy", "e630.fy"))
        a[fy] = {"interest_income": J[(g, "e020.fy", p)], "interest_expense": J[(g, "e350.fy", p)], "nii": nii, "bond": bond}
    a["gain_fy25_vs_fy23"] = a["FY2025"]["nii"] - a["FY2023"]["nii"]
    a["gain_cum"] = (a["FY2024"]["nii"] - a["FY2023"]["nii"]) + (a["FY2025"]["nii"] - a["FY2023"]["nii"])
    a["bond_cum"] = a["FY2024"]["bond"] + a["FY2025"]["bond"]
    a["na_2026_03"] = J[(g, "b950", "2026-03")]
    a["exp_chg"] = a["FY2025"]["interest_expense"] - a["FY2023"]["interest_expense"]
    a["inc_chg"] = a["FY2025"]["interest_income"] - a["FY2023"]["interest_income"]
    agg[g] = a
N["agg"] = agg

# --- bank level
B = rows(os.path.join(D, "bank_table.csv"))
for r in B:
    for k in list(r):
        if k not in ("fi_code", "name_ja", "tier", "merger_break", "irrbb_basis", "irrbb_as_of", "irrbb_status", "eve_max_scenario", "filer_name", "filer_sec_code", "filer_status"):
            r[k] = f(r[k])
S = [r for r in B if not r["merger_break"] and r["gain_cum"] is not None]
N["sample"] = len(S); N["excluded_mergers"] = [(r["fi_code"], r["name_ja"]) for r in B if r["merger_break"]]
N["no_fy2025"] = [(r["fi_code"], r["name_ja"]) for r in B if r["gain_cum"] is None]
N["gain_pct_median"] = st.median(r["gain_pct"] for r in S)
for t in ("regional-1", "regional-2"):
    T = [r["gain_pct"] for r in S if r["tier"] == t]
    N["gain_pct_median_" + t] = st.median(T); N["n_" + t] = len(T)
N["gain_pct_range"] = (min(r["gain_pct"] for r in S), max(r["gain_pct"] for r in S))
N["gain_negative"] = [(r["fi_code"], r["name_ja"], round(r["gain_pct"], 1)) for r in S if r["gain_pct"] < 0]
top = sorted(S, key=lambda r: -r["gain_pct"])
N["gain_top10"] = [(r["fi_code"], r["name_ja"], r["tier"], round(r["gain_pct"], 1), round(r["ldr_2024_03"], 1)) for r in top[:10]]
N["gain_bottom10"] = [(r["fi_code"], r["name_ja"], r["tier"], round(r["gain_pct"], 1), round(r["ldr_2024_03"], 1)) for r in top[-10:]]
# H2
P = [r for r in S if r["ldr_2024_03"] is not None]
N["h2_spearman_ldr_gainpct"] = spearman([r["ldr_2024_03"] for r in P], [r["gain_pct"] for r in P]); N["h2_n"] = len(P)
N["ldr_median"] = st.median(r["ldr_2024_03"] for r in P)
hi = [r["gain_pct"] for r in P if r["ldr_2024_03"] >= N["ldr_median"]]; lo = [r["gain_pct"] for r in P if r["ldr_2024_03"] < N["ldr_median"]]
N["gain_pct_median_ldr_hi"], N["gain_pct_median_ldr_lo"] = st.median(hi), st.median(lo)
# H1
C = [r for r in S if r["net_cum"] is not None]
N["h1_n"] = len(C)
N["h1_net_cum_positive"] = sum(1 for r in C if r["net_cum"] > 0)
N["h1_net_simple_positive"] = sum(1 for r in C if r["net_simple"] > 0)
N["net_cum_negative"] = sorted([(r["fi_code"], r["name_ja"], round(r["gain_cum"]), round(r["bond_cum"]), round(r["net_cum"]), round(r["net_cum_to_na"], 1)) for r in C if r["net_cum"] < 0], key=lambda x: x[5])
N["bond_cum_to_na_median"] = st.median(r["bond_cum_to_na"] for r in C)
N["gain_cum_to_na_median"] = st.median(r["gain_cum_to_na"] for r in C)
N["cost_top10"] = [(r["fi_code"], r["name_ja"], round(r["bond_cum_to_na"], 1), round(r["gain_cum_to_na"], 1), round(r["net_cum_to_na"], 1)) for r in sorted(C, key=lambda r: r["bond_cum_to_na"])[:10]]
N["net_top10"] = [(r["fi_code"], r["name_ja"], round(r["net_cum_to_na"], 1), round(r["gain_cum_to_na"], 1), round(r["bond_cum_to_na"], 1)) for r in sorted(C, key=lambda r: -r["net_cum_to_na"])[:10]]
N["no_bond_loss"] = sum(1 for r in C if r["bond_cum"] >= 0)
# unrealised (filer level)
U = [r for r in C if r.get("bond_loss_to_equity_pct") is not None and r.get("filer_status") == "clean"]
N["unrealised_n"] = len(U)
# --- IRRBB
I = [r for r in B if r.get("irrbb_as_of") == "2026-03-31" and r.get("eve_up_to_t1") is not None]
N["irrbb_n"] = len(I)
N["irrbb_n_nii_up"] = sum(1 for r in I if r.get("nii_up_to_t1") is not None)
NU = [r for r in I if r.get("nii_up_to_t1") is not None]
N["nii_up_gain_count"] = sum(1 for r in NU if r["nii_up_to_t1"] < 0)   # negative ΔNII = income rises
N["nii_up_loss_count"] = sum(1 for r in NU if r["nii_up_to_t1"] > 0)
N["nii_up_median_to_t1"] = st.median(r["nii_up_to_t1"] for r in NU)
N["eve_up_median_to_t1"] = st.median(r["eve_up_to_t1"] for r in I)
N["eve_max_from_down"] = sum(1 for r in I if r.get("eve_max_scenario") in ("parallel_down", "short_down", "flattener"))
N["eve_max_from_up"] = sum(1 for r in I if r.get("eve_max_scenario") in ("parallel_up", "short_up", "steepener"))
N["eve_max_scen_counts"] = {}
for r in I: N["eve_max_scen_counts"][r.get("eve_max_scenario")] = N["eve_max_scen_counts"].get(r.get("eve_max_scenario"), 0) + 1
N["eve_up_top10"] = [(r["fi_code"], r["name_ja"], round(r["eve_up_to_t1"], 1), None if r.get("nii_up_to_t1") is None else round(r["nii_up_to_t1"], 1), round(r["eve_max_to_t1"], 1), r["eve_max_scenario"]) for r in sorted(I, key=lambda r: -r["eve_up_to_t1"])[:10]]
N["eve_up_over15"] = [(r["fi_code"], r["name_ja"], round(r["eve_up_to_t1"], 1)) for r in I if r["eve_up_to_t1"] > 15]
N["nii_up_best10"] = [(r["fi_code"], r["name_ja"], round(-r["nii_up_to_t1"], 1), round(r["eve_up_to_t1"], 1)) for r in sorted(NU, key=lambda r: r["nii_up_to_t1"])[:10]]
N["nii_up_worst"] = [(r["fi_code"], r["name_ja"], round(-r["nii_up_to_t1"], 1), round(r["eve_up_to_t1"], 1)) for r in sorted(NU, key=lambda r: -r["nii_up_to_t1"]) if r["nii_up_to_t1"] > 0][:10]
N["tochigi"] = [(r["name_ja"], r["eve_max_to_t1"], r["eve_max_scenario"], round(r["eve_up_to_t1"], 2)) for r in B if r["fi_code"] == "0517"]
# H4: paid most (realised bond cost / NA) vs most exposed to next increase (ΔEVE up / T1)
H = [r for r in C if r.get("irrbb_as_of") == "2026-03-31" and r.get("eve_up_to_t1") is not None]
N["h4_n"] = len(H)
paid = sorted(H, key=lambda r: r["bond_cum_to_na"])[:10]; exp = sorted(H, key=lambda r: -r["eve_up_to_t1"])[:10]
N["h4_overlap"] = sorted({r["fi_code"] for r in paid} & {r["fi_code"] for r in exp})
N["h4_overlap_names"] = [r["name_ja"] for r in paid if r["fi_code"] in N["h4_overlap"]]
N["h4_spearman"] = spearman([-r["bond_cum_to_na"] for r in H], [r["eve_up_to_t1"] for r in H])
# gain vs next-increase NII sensitivity
G = [r for r in H if r.get("nii_up_to_t1") is not None]
N["gain_vs_niiup_spearman"] = spearman([r["gain_cum_to_na"] for r in G], [-r["nii_up_to_t1"] for r in G]); N["gain_vs_niiup_n"] = len(G)
json.dump(N, open(os.path.join(D, "numbers.json"), "w"), ensure_ascii=False, indent=1, default=str)
for k, v in N.items():
    print(k, "=", v if not isinstance(v, float) else round(v, 3))

# --- where the gain came from (sample banks, FY2023 -> FY2025), added after H2 reversed
LV = {}
for r in rows(os.path.join(D, "jba_banks_long.csv")):
    LV[(r["fi_code"], r["line"], r["period"])] = f(r["yen_mn"])
codes = [r["fi_code"] for r in S]
def tot(line, p):
    return sum(LV.get((c, line, p)) or 0 for c in codes)
dec = {}
for line, lab in (("e020.fy", "interest_income"), ("e030.fy", "loans"), ("e040.fy", "securities"), ("e090.fy", "due_from_banks"),
                  ("e350.fy", "interest_expense"), ("e360.fy", "deposit_interest")):
    dec[lab] = {"FY2023": tot(line, "2024-03"), "FY2025": tot(line, "2026-03")}
    dec[lab]["chg"] = dec[lab]["FY2025"] - dec[lab]["FY2023"]
dec["other_income_chg"] = dec["interest_income"]["chg"] - dec["loans"]["chg"] - dec["securities"]["chg"] - dec["due_from_banks"]["chg"]
dec["other_expense_chg"] = dec["interest_expense"]["chg"] - dec["deposit_interest"]["chg"]
dec["nii_chg"] = dec["interest_income"]["chg"] - dec["interest_expense"]["chg"]
N["decomp"] = dec
# per bank: cash-at-banks share of assets at March 2024 vs gain %
Q = []
for r in S:
    c = r["fi_code"]; due, ta = LV.get((c, "d035", "2024-03")), LV.get((c, "d990", "2024-03"))
    e090a, e090b = LV.get((c, "e090.fy", "2024-03")), LV.get((c, "e090.fy", "2026-03"))
    if due is not None and ta and r["gain_pct"] is not None and e090a is not None and e090b is not None:
        Q.append((r, due / ta * 100, (e090b - e090a) / r["nii_FY2023"] * 100 if r["nii_FY2023"] else None))
N["due_share_median"] = st.median(q[1] for q in Q)
N["spearman_dueshare_gainpct"] = spearman([q[1] for q in Q], [q[0]["gain_pct"] for q in Q]); N["due_n"] = len(Q)
N["spearman_ldr_dueshare"] = spearman([q[0]["ldr_2024_03"] for q in Q], [q[1] for q in Q])
N["dueinc_contrib_median_pp"] = st.median(q[2] for q in Q if q[2] is not None)
json.dump(N, open(os.path.join(D, "numbers.json"), "w"), ensure_ascii=False, indent=1, default=str)
for k in ("decomp", "due_share_median", "spearman_dueshare_gainpct", "due_n", "spearman_ldr_dueshare", "dueinc_contrib_median_pp"):
    print(k, "=", N[k])
