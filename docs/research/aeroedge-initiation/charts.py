"""Build the AeroEdge initiation charts from data/*.csv into out/charts/.

Run: observatory/.venv/bin/python docs/research/aeroedge-initiation/charts.py
"""
import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out", "charts")
os.makedirs(OUT, exist_ok=True)

C = ["#1a4d8f", "#c2410c", "#0f766e", "#a16207", "#475569", "#9d174d"]
INK, MUTED, GRID = "#111827", "#6b7280", "#e5e7eb"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK,
    "axes.unicode_minus": True,
    "xtick.color": INK,
    "ytick.color": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


def read(name):
    with open(os.path.join(DATA, name)) as f:
        rows = [r for r in csv.reader(line for line in f if not line.startswith("#"))]
    head, body = rows[0], rows[1:]
    return [dict(zip(head, r)) for r in body]


def num(s):
    return float(s) if s not in ("", None) else None


def finish(fig, title, source, name):
    fig.suptitle(title, x=0.02, ha="left", fontsize=13, fontweight="bold", color=INK, y=0.97)
    fig.text(0.02, 0.015, source, ha="left", fontsize=7.5, color=MUTED, wrap=True)
    fig.savefig(os.path.join(OUT, name), dpi=200)
    plt.close(fig)


def yen_mn(ax):
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: ("−" if v < 0 else "") + f"{abs(v):,.0f}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


# 1 — engine-set equivalents vs LEAP deliveries
rows = read("engine_sets_vs_leap.csv")
fig, ax = plt.subplots(figsize=(9, 5.2))
fig.subplots_adjust(top=0.84, bottom=0.2, left=0.08, right=0.98)
xs = range(len(rows))
w = 0.38
for i, r in enumerate(rows):
    leap, ae = num(r["leap_deliveries_jul_jun"]), num(r["aeroedge_engine_sets"])
    est = r["fiscal_year"].endswith("E")
    if leap is not None:
        ax.bar(i - w / 2, leap, w, color=C[0], label="LEAP engine deliveries (Jul–Jun)" if i == 0 else None)
        ax.text(i - w / 2, leap + 25, f"{leap:,.0f}", ha="center", fontsize=8.5, color=INK)
    ax.bar(i + w / 2, ae, w, color="white" if est else C[1], edgecolor=C[1], hatch="///" if est else None,
           label="AeroEdge engine-set equivalents" if i == 0 else None)
    ax.text(i + w / 2, ae + 25, f"{ae:,.0f}", ha="center", fontsize=8.5, color=INK)
ax.set_xticks(list(xs))
ax.set_xticklabels([r["fiscal_year"] + ("\nguidance" if r["fiscal_year"].endswith("E") else
                    f"\nratio {num(r['ratio_pct']):.1f}%") for r in rows])
ax.tick_params(axis="x", length=0, pad=6)
ax.set_ylim(0, 2400)
ax.set_ylabel("Engines")
yen_mn(ax)
ax.legend(frameon=False, loc="upper left", fontsize=9)
ax.set_title("AeroEdge blade shipments converted to engine sets vs CFM LEAP deliveries, July–June years",
             loc="left", fontsize=9.5, color=MUTED, pad=10)
finish(fig, "AeroEdge blade volume has tracked about 40% of LEAP engine deliveries",
       "Source: AeroEdge results presentation, 20 Aug 2026, pp. 9–10 (LEAP deliveries compiled by the company from GE Aerospace results); "
       "growth-plan presentation, 7 Sep 2026, p. 48. Ratio = AeroEdge engine sets ÷ LEAP deliveries × 100 (Plover Analytics calculation). "
       "FY6/27 is company guidance.", "01-engine-sets-vs-leap.png")

# 2 — sales and operating margin (two panels: margin above, sales below)
rows = read("pl_history.csv")
fig, (axm, ax) = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"height_ratios": [1, 2.3]})
fig.subplots_adjust(top=0.85, bottom=0.15, left=0.09, right=0.98, hspace=0.12)
for i, r in enumerate(rows):
    est = r["status"] != "actual"
    s = num(r["sales_jpy_mn"])
    ax.bar(i, s, 0.62, color="white" if est else C[0], edgecolor=C[0], hatch="///" if est else None)
    ax.text(i, s + 120, f"{s:,.0f}", ha="center", fontsize=8.5, color=INK)
ax.set_xticks(range(len(rows)))
ax.set_xticklabels([r["fiscal_year"] + ("\nguidance" if r["status"] != "actual" else "") for r in rows])
ax.tick_params(axis="x", length=0)
ax.set_ylim(0, 9000)
ax.set_ylabel("Sales, ¥ mn")
yen_mn(ax)
pts = [(i, num(r["op_margin_pct"])) for i, r in enumerate(rows) if r["op_margin_pct"]]
axm.plot([p[0] for p in pts], [p[1] for p in pts], color=C[1], marker="o", linewidth=2)
for x, y in pts:
    axm.text(x, y + 2.2, f"{y:.1f}%", ha="center", fontsize=8.5, color=C[1])
for i, r in enumerate(rows):
    if not r["op_margin_pct"] and r["operating_income_jpy_mn"]:
        axm.text(i, 12, "loss", ha="center", fontsize=8.5, color=MUTED)
axm.set_ylim(0, 32)
axm.set_yticks([0, 10, 20, 30])
axm.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
axm.grid(axis="y", color=GRID, linewidth=0.8)
axm.set_axisbelow(True)
axm.tick_params(axis="x", length=0)
axm.set_ylabel("Op. margin")
axm.set_title("Operating margin (top) and sales, ¥ mn (bottom). FY6/19–20 operating income not in the filed summary",
              loc="left", fontsize=9.5, color=MUTED, pad=10)
finish(fig, "Sales have recovered six-fold since FY6/21; operating margin has held near 20%",
       "Source: AeroEdge annual securities reports FY6/23–FY6/25 (EDINET, via Plover Analytics, as of 27 Sep 2026); FY6/21 operating loss from the growth-plan presentation, p. 49; "
       "FY6/26 earnings release (TDnet, 13 Aug 2026); FY6/27 company guidance (hatched). Parent-only to FY6/25, consolidated from FY6/26. Margin = operating income ÷ sales × 100.",
       "02-sales-and-margin.png")

# 3 — sales by source
rows = read("sales_by_source.csv")
fig, ax = plt.subplots(figsize=(9, 5.2))
fig.subplots_adjust(top=0.84, bottom=0.17, left=0.08, right=0.98)
parts = [("tial_blade_processing", "TiAl blade processing (Safran)", C[0]),
         ("other", "Other: contract development, Engine B parts", C[1]),
         ("ono_plant", "Ono Plant (airframe parts)", C[2])]
for i, r in enumerate(rows):
    base = 0
    for key, label, col in parts:
        v = num(r[key])
        if v:
            ax.bar(i, v, 0.55, bottom=base, color=col, label=label if i == len(rows) - 1 else None)
            if v > 350:
                ax.text(i, base + v / 2, f"{v:,.0f}", ha="center", va="center", fontsize=8.5, color="white")
            base += v
    ax.text(i, base + 110, f"non-blade {num(r['non_blade_share_pct']):.1f}%", ha="center", fontsize=9, color=INK)
ax.set_xticks(range(len(rows)))
ax.set_xticklabels([r["fiscal_year"] + (" (guidance)" if r["fiscal_year"].endswith("E") else "") for r in rows])
ax.tick_params(axis="x", length=0)
ax.set_ylim(0, 9000)
ax.set_ylabel("Sales, ¥ mn")
yen_mn(ax)
ax.legend(frameon=False, loc="upper left", fontsize=9)
ax.set_title("Sales by source, ¥ mn", loc="left", fontsize=9.5, color=MUTED, pad=10)
finish(fig, "Non-blade revenue is guided to reach 30% of sales in FY6/27",
       "Source: AeroEdge growth-plan presentation, 7 Sep 2026, p. 47 (company figures; FY6/27 is guidance). "
       "Non-blade share = (other + Ono Plant) ÷ total × 100 (Plover Analytics calculation).", "03-sales-by-source.png")

# 4 — cash flow
rows = read("cash_flow.csv")
fig, ax = plt.subplots(figsize=(9, 5.2))
fig.subplots_adjust(top=0.84, bottom=0.17, left=0.08, right=0.98)
w = 0.36
for i, r in enumerate(rows):
    ex, sub, cap, ocf = num(r["ocf_ex_subsidy"]), num(r["subsidies_received"]), num(r["capex"]), num(r["ocf"])
    ax.bar(i - w / 2, ex, w, color=C[0], label="Operating cash flow excl. subsidies" if i == 0 else None)
    ax.bar(i - w / 2, sub, w, bottom=ex, color=C[3], label="Subsidies received" if i == 0 else None)
    ax.text(i - w / 2, ocf + 50, f"{ocf:,.0f}", ha="center", fontsize=8.5, color=INK)
    ax.bar(i + w / 2, cap, w, color=C[4], label="Capital expenditure" if i == 0 else None)
    ax.text(i + w / 2, cap + 50, f"{cap:,.0f}", ha="center", fontsize=8.5, color=INK)
ax.set_xticks(range(len(rows)))
ax.set_xticklabels([r["fiscal_year"] for r in rows])
ax.tick_params(axis="x", length=0)
ax.set_ylim(0, 3500)
ax.set_ylabel("¥ mn")
yen_mn(ax)
ax.legend(frameon=False, loc="upper left", fontsize=9)
ax.set_title("Operating cash flow (stacked: underlying and subsidies) vs capital expenditure, ¥ mn",
             loc="left", fontsize=9.5, color=MUTED, pad=10)
finish(fig, "Subsidies exceeded underlying operating cash flow in FY6/26",
       "Source: AeroEdge annual securities reports FY6/23–FY6/25 (cash-flow statements, EDINET); FY6/26 results presentation, 20 Aug 2026, p. 17 "
       "(capex = fixed-asset spending, mainly for new programmes; excludes ¥709m for Ono Plant shares). Underlying = OCF − subsidies received (Plover Analytics calculation).",
       "04-cash-flow.png")

# 5 — holders
rows = read("holders_jun2026.csv")
col = {"strategic": C[0], "founder": C[1], "government-owned": C[2], "nominee": C[4], "broker": C[4]}
fig, ax = plt.subplots(figsize=(9, 5.2))
fig.subplots_adjust(top=0.84, bottom=0.15, left=0.34, right=0.97)
names = [r["holder_en"] for r in rows][::-1]
vals = [num(r["pct"]) for r in rows][::-1]
cols = [col[r["type"]] for r in rows][::-1]
ax.barh(names, vals, color=cols, height=0.6)
for y, v in enumerate(vals):
    ax.text(v + 0.3, y, f"{v:.1f}%", va="center", fontsize=8.5, color=INK)
ax.set_xlim(0, 20)
ax.set_xticks([0, 5, 10, 15, 20])
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
ax.grid(axis="x", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.tick_params(axis="y", length=0)
from matplotlib.patches import Patch  # noqa: E402
ax.legend(handles=[Patch(color=C[0], label="Strategic"), Patch(color=C[1], label="Founder / CEO"),
                   Patch(color=C[2], label="Government-owned bank"), Patch(color=C[4], label="Nominee / broker")],
          frameon=False, loc="lower right", fontsize=9)
ax.set_title("Ten largest shareholders, % of shares outstanding excl. treasury, 30 June 2026", loc="left",
             fontsize=9.5, color=MUTED, pad=10)
finish(fig, "Five strategic and founding holders own 56% of the shares",
       "Source: AeroEdge growth-plan presentation, 7 Sep 2026, p. 10 (company figures). Top five total 56.1% (Plover Analytics sum). "
       "The FY6/26 annual securities report, due by 30 Sep 2026, will carry the filed register.", "05-holders.png")

print("charts written to", OUT)
