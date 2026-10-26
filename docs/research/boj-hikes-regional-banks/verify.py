# -*- coding: utf-8 -*-
"""Trace every number in draft.md to a value computed from data/ (numbers.json,
bank_table.csv, the rate CSVs). Prints each number with where it was found, and
lists the ones that were not found for manual review."""
import csv, json, os, re
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "data")
N = json.load(open(os.path.join(D, "numbers.json")))
names = json.load(open(os.path.join(D, "names_en.json")))
bank = {r["fi_code"]: r for r in csv.DictReader(l for l in open(os.path.join(D, "bank_table.csv"), encoding="utf-8") if not l.startswith("#"))}
src = {}
def add(val, where, dps=(0, 1, 2, 3)):
    if val is None: return
    v = float(val)
    for dp in dps:
        for x in (v, abs(v)):
            s = ("{:,.%df}" % dp).format(x)
            src.setdefault(s, where)
def walk(x, path):
    if isinstance(x, dict):
        for k, v in x.items(): walk(v, path + "." + str(k))
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x): walk(v, path + "[%d]" % i)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        add(x, path)
        if abs(x) > 1000: add(x / 1000, path + "/1e3")          # ¥mn -> ¥bn
walk(N, "N")
for code, r in bank.items():
    for k in ("gain_pct", "net_cum_to_na", "eve_up_to_t1", "eve_max_to_t1", "nii_up_to_t1", "bond_cum_to_na", "gain_cum_to_na"):
        if r.get(k): add(float(r[k]), "%s.%s" % (names.get(code, code), k))
    for k in ("bond_cum", "gain_cum"):
        if r.get(k): add(float(r[k]) / 1000, "%s.%s/1e3" % (names.get(code, code), k))
# derived shares quoted in the text
d = N["decomp"]
add(d["deposit_interest"]["chg"] / d["loans"]["chg"] * 100, "decomp dep/loan %")
add(d["deposit_interest"]["FY2025"] / d["deposit_interest"]["FY2023"], "decomp dep x")
add(d["due_from_banks"]["chg"] / d["nii_chg"] * 100, "decomp due share %")
a, b = N["agg"]["regional"], N["agg"]["regional-2"]
add(a["gain_fy25_vs_fy23"] / a["FY2023"]["nii"] * 100, "agg regional gain %")
add(b["gain_fy25_vs_fy23"] / b["FY2023"]["nii"] * 100, "agg regional-2 gain %")
add((a["gain_cum"] + b["gain_cum"]) / 1e3, "agg gain_cum both /1e3")
add((a["bond_cum"] + b["bond_cum"]) / 1e3, "agg bond_cum both /1e3")
add((a["gain_cum"] + b["gain_cum"] + a["bond_cum"] + b["bond_cum"]) / 1e3, "agg net both /1e3")
add(N["h1_n"] - N["h1_net_cum_positive"], "h1 net payers"); add(N["h1_n"] - N["h1_net_simple_positive"], "h1 simple net payers")
add(N["irrbb_n_nii_up"] - N["nii_up_gain_count"] - N["nii_up_loss_count"], "nii up zero")
add(-float(bank["0129"]["nii_up_to_t1"]), "Ashikaga -nii_up")
add(N["n_regional-2"] + N["n_regional-1"], "sample")
text = open(os.path.join(HERE, "draft.md"), encoding="utf-8").read()
body = text.split("### Sources and method")[0]
toks = re.findall(r"(?<![\w.])[−-]?\d[\d,]*(?:\.\d+)?", body)
found, missing = [], []
for t in toks:
    k = t.replace("−", "").replace("-", "")
    if k in src: found.append((t, src[k]))
    else: missing.append(t)
print("numbers in draft:", len(toks), "traced:", len(found), "not traced:", len(missing))
print("not traced:", sorted(set(missing), key=lambda x: float(x.replace(",", "").replace("−", "-"))))
json.dump({"traced": found, "untraced": missing}, open(os.path.join(D, "verify_trace.json"), "w"), ensure_ascii=False, indent=1)
