"""Every calculated figure in the draft, with its formula and inputs -> data/derived.csv.

Inputs are only the CSVs in data/ (all logged in log.md). Fiscal years: AMD and Intel
end late December; NVIDIA's year ends late January and is labelled here by the calendar
year it mostly covers (FY ending Jan 2026 -> 2025).
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel import panel  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
A, I, N0 = panel("AMD"), panel("INTC"), panel("NVDA")
N = {k: {y - 1: v for y, v in d.items()} for k, d in N0.items()}  # NVDA: FY Jan-2026 -> 2025

rows = []


def add(key, value, unit, formula, inputs):
    rows.append([key, round(value, 4) if isinstance(value, float) else value, unit, formula, inputs])


def m(x):
    return x / 1e6


def debt(y):  # AMD total debt as filed: LongTermDebt (incl. current) where tagged, else noncurrent + current parts
    for a, b in (("ltd", None), ("ltdcl", "ltdclc"), ("ltdnc", "ltdc")):
        if y in A[a] and (b is None or True):
            if b is None:
                return A[a][y], "LongTermDebt"
            return A[a][y] + A[b].get(y, 0.0), "%s + %s" % (a, b)
    return None, None


# Revenue path
for y in range(2010, 2026):
    add("AMD rev %d" % y, m(A["rev"][y]), "$m", "as filed", "revenue tag chain")
add("AMD rev chg 2014-15", 100 * (A["rev"][2015] / A["rev"][2014] - 1), "%", "rev2015/rev2014-1", "")
add("AMD rev chg 2011-14", 100 * (A["rev"][2014] / A["rev"][2011] - 1), "%", "rev2014/rev2011-1", "")
add("AMD rev chg 2015-16", 100 * (A["rev"][2016] / A["rev"][2015] - 1), "%", "rev2016/rev2015-1", "")
add("AMD rev chg 2016-17", 100 * (A["rev"][2017] / A["rev"][2016] - 1), "%", "rev2017/rev2016-1", "")
add("AMD rev multiple 2015-21", A["rev"][2021] / A["rev"][2015], "x", "rev2021/rev2015", "")
add("AMD rev multiple 2015-25", A["rev"][2025] / A["rev"][2015], "x", "rev2025/rev2015", "")
add("AMD rev CAGR 2015-21", 100 * ((A["rev"][2021] / A["rev"][2015]) ** (1 / 6) - 1), "%", "(rev2021/rev2015)^(1/6)-1", "")
add("AMD rev chg 2024-25", 100 * (A["rev"][2025] / A["rev"][2024] - 1), "%", "rev2025/rev2024-1", "")
for y in range(2011, 2026):
    add("AMD GM %d" % y, 100 * A["gp"][y] / A["rev"][y], "%", "GrossProfit/revenue", "")
    add("AMD OM %d" % y, 100 * A["oi"][y] / A["rev"][y], "%", "OperatingIncomeLoss/revenue", "")
    add("AMD RD/rev %d" % y, 100 * A["rd"][y] / A["rev"][y], "%", "R&D/revenue", "")
    d, how = debt(y)
    if d is not None:
        add("AMD debt %d" % y, m(d), "$m", how, "")
    add("AMD cash+STI %d" % y, m(A["cash"][y] + A["sti"].get(y, 0.0)), "$m", "cash + short-term investments (blank = 0 only where the tag is absent that year)", "")
    add("AMD FCF %d" % y, m(A["cfo"][y] - A["capex"][y]), "$m", "CFO - capex", "")
add("AMD RD chg 2012-15", 100 * (A["rd"][2015] / A["rd"][2012] - 1), "%", "rd2015/rd2012-1", "")
add("AMD RD chg 2014-15", 100 * (A["rd"][2015] / A["rd"][2014] - 1), "%", "rd2015/rd2014-1", "")
add("AMD RD multiple 2015-25", A["rd"][2025] / A["rd"][2015], "x", "rd2025/rd2015", "")
add("AMD GM chg 2016-21", 100 * (A["gp"][2021] / A["rev"][2021] - A["gp"][2016] / A["rev"][2016]), "pp", "GM2021-GM2016", "")
add("AMD shares chg 2021-22", m(A["sh"][2022] - A["sh"][2021]), "m shares", "sh2022-sh2021", "")
add("AMD amort / OI 2023", 2800 / m(A["oi"][2023]), "x", "amortisation 2023 / operating income 2023", "")

# Segments
seg = {}
for r in csv.DictReader(l for l in open(os.path.join(HERE, "data", "AMD_segments.csv")) if not l.startswith("#")):
    if r["tag"] == "OperatingIncomeLoss" or "Reconciling" in r["other_dims"]:
        continue
    y = int(r["period_end"][:4])
    name = r["segment"].replace("Member", "") + ("/" + r["other_dims"].split("=")[1].replace("Member", "")
                                                 if "ProductOrService" in r["other_dims"] else "")
    name = name.replace("EnterpriseEmbeddedAndSemiCustoms", "EnterpriseEmbeddedandSemiCustom")
    prev = seg.get((name, y))
    if prev is None or r["filing_fy"] > prev[1]:
        seg[(name, y)] = (float(r["value"]), r["filing_fy"])
for (name, y), (v, fy) in sorted(seg.items()):
    add("seg %s %d" % (name, y), m(v), "$m", "as filed, latest 10-K (FY%s)" % fy, "")
for y in range(2020, 2026):
    add("DC share %d" % y, 100 * seg[("Datacenter", y)][0] / A["rev"][y], "%", "Data Center revenue / total revenue", "")
add("DC multiple 2020-25", seg[("Datacenter", 2025)][0] / seg[("Datacenter", 2020)][0], "x", "DC2025/DC2020", "")
add("CG chg 2014-15", 100 * (seg[("ComputingandGraphics", 2015)][0] / seg[("ComputingandGraphics", 2014)][0] - 1), "%", "", "")
add("EESC chg 2014-15", 100 * (seg[("EnterpriseEmbeddedandSemiCustom", 2015)][0] / seg[("EnterpriseEmbeddedandSemiCustom", 2014)][0] - 1), "%", "", "")
add("CG multiple 2015-21", seg[("ComputingandGraphics", 2021)][0] / seg[("ComputingandGraphics", 2015)][0], "x", "", "")
for y in range(2014, 2026):  # segments must sum to reported revenue
    parts = [v for (nm, yy), (v, _) in seg.items() if yy == y and "/" not in nm and nm != "AllOther"
             and not (y >= 2020 and nm in ("ComputingandGraphics", "EnterpriseEmbeddedandSemiCustom"))
             and not (y < 2025 and nm == "ClientAndGaming")
             and not (y == 2025 and nm in ("Client", "Gaming"))]
    add("seg sum check %d" % y, m(sum(parts) - A["rev"][y]), "$m", "sum of segments - reported revenue", "")

# Peers (calendar-aligned; NVIDIA shifted as above)
for y in (2014, 2025):
    add("AMD/INTC rev %d" % y, 100 * A["rev"][y] / I["rev"][y], "%", "AMD revenue / Intel revenue", "")
    add("AMD/NVDA rev %d" % y, 100 * A["rev"][y] / N["rev"][y], "%", "AMD revenue / NVIDIA revenue", "")
    for tk, P in (("INTC", I), ("NVDA", N), ("AMD", A)):
        add("%s rev %d" % (tk, y), m(P["rev"][y]), "$m", "as filed", "")
        add("%s GM %d" % (tk, y), 100 * P["gp"][y] / P["rev"][y], "%", "GrossProfit/revenue", "")
        add("%s OM %d" % (tk, y), 100 * P["oi"][y] / P["rev"][y], "%", "OperatingIncomeLoss/revenue", "")
        add("%s RD %d" % (tk, y), m(P["rd"][y]), "$m", "as filed", "")
        add("%s NI %d" % (tk, y), m(P["ni"][y]), "$m", "as filed", "")
add("INTC NI 2024", m(I["ni"][2024]), "$m", "as filed", "")
add("INTC OM 2024", 100 * I["oi"][2024] / I["rev"][2024], "%", "", "")

with open(os.path.join(HERE, "data", "derived.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["key", "value", "unit", "formula", "note"])
    w.writerows(rows)
for r in rows:
    print("%-34s %12s %-8s %s" % (r[0], r[1], r[2], r[3]))
