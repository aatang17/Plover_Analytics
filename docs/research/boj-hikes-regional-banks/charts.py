# -*- coding: utf-8 -*-
"""The five charts, built from the CSVs in data/ — never from numbers typed here.

House style: Plover series colours in order, horizontal gridlines only, no
chart border, no shadow, no gradient. Tabular figures, a true minus sign, and a
source line embedded in the image so a screenshotted chart still says where it
came from. Light theme, because that is what survives being pasted anywhere.

  python charts.py        # writes out/charts/*.png
"""
import csv
import os
import datetime as dt

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out", "charts")
os.makedirs(OUT, exist_ok=True)

PALETTE = ["#1a4d8f", "#c2410c", "#0f766e", "#a16207", "#475569", "#9d174d"]
INK, MUTED, GRID = "#0f172a", "#64748b", "#e2e8f0"
MINUS = "−"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 9,
    "axes.edgecolor": GRID,
    "axes.labelcolor": MUTED,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "figure.dpi": 200,
    "savefig.dpi": 200,
})


def rows(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return list(csv.DictReader([l for l in f if not l.startswith("#")]))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def frame(ax, title, subtitle, source, ylabel=None, source_gap=34):
    # The subtitle sits just above the axes and the title above that, so the
    # title's pad has to clear the subtitle's line height or the two overprint.
    ax.set_title(title, loc="left", fontsize=12.5, color=INK, fontweight="bold",
                 pad=32 if subtitle else 12)
    if subtitle:
        ax.text(0, 1.016, subtitle, transform=ax.transAxes, fontsize=9.2,
                color=MUTED, va="bottom")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(False)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8.6)
    # The source line is anchored to the AXES with an offset in points, not to a
    # fraction of the figure. A fraction moves with the figure height, so when
    # the charts were shortened for the page it slid up into the tick labels.
    # Points are absolute: this clears them whatever the chart's height.
    ax.annotate(source, xy=(0, 0), xycoords="axes fraction",
                xytext=(0, -source_gap), textcoords="offset points",
                fontsize=7.2, color=MUTED, ha="left", va="top",
                annotation_clip=False)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight", facecolor="white",
                pad_inches=0.28)
    plt.close(fig)
    print("  out/charts/%s" % name)


def pct(v, _p=None):
    s = "%.1f" % abs(v)
    return (MINUS if v < 0 else "") + s + "%"

import json
CREDIT = "Plover Analytics, as of 2026-09-24. "
NAMES = json.load(open(os.path.join(DATA, "names_en.json"), encoding="utf-8"))
T1, T2 = PALETTE[0], PALETTE[1]


def bank_rows():
    out = []
    for r in rows("bank_table.csv"):
        d = dict(r)
        for k, v in r.items():
            if k not in ("fi_code", "name_ja", "tier", "merger_break", "irrbb_basis", "irrbb_as_of",
                         "irrbb_status", "eve_max_scenario", "filer_name", "filer_sec_code", "filer_status"):
                d[k] = num(v)
        out.append(d)
    return out


def month(s):
    return dt.date(int(s[:4]), int(s[5:7]), 1)


# 1 --------------------------------------------------------------- rates
def chart_rates():
    cr = rows("call_rate_monthly.csv"); lr = rows("loan-rates.csv"); dr = rows("deposit-rates.csv")
    fig, ax = plt.subplots(figsize=(8.0, 4.1))
    series = [
        ([month(r["month"]) for r in cr if r["month"] >= "2023-01"], [num(r["mean_pct"]) for r in cr if r["month"] >= "2023-01"], "Overnight call rate (monthly average)", PALETTE[4], "-"),
        ([month(r["period"]) for r in lr], [num(r["DLLR2CIRBNL1"]) for r in lr], "New loans, regional banks", T1, "-"),
        ([month(r["period"]) for r in lr], [num(r["DLLR2CIMBNL1"]) for r in lr], "New loans, second-tier regional banks", PALETTE[2], "-"),
        ([month(r["period"]) for r in dr], [num(r["DLDR121N"]) for r in dr], "Ordinary deposits, posted rate", T2, "-"),
    ]
    for x, y, lab, col, ls in series:
        pts = [(a, b) for a, b in zip(x, y) if b is not None]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=col, lw=1.8, ls=ls, label=lab)
    ax.axhline(0, color=GRID, lw=1)
    from matplotlib.ticker import MultipleLocator
    ax.yaxis.set_major_locator(MultipleLocator(0.25))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: (MINUS if v < 0 else "") + "%.2f%%" % abs(v)))
    ax.legend(loc="upper left", frameon=False, fontsize=8.2)
    frame(ax, "Loan rates rose with the call rate; the posted deposit rate rose a quarter as much",
          "Per cent per year, monthly, January 2023 to the latest month published",
          CREDIT + "Sources: Bank of Japan — FM01 (call rate, uncollateralised overnight, monthly mean of daily values, calculated), IR04 (new-loan rates), IR02 (posted deposit rates).")
    save(fig, "01-rates.png")


# 2 --------------------------------------------------------------- gain by bank
def chart_gain():
    B = [r for r in bank_rows() if not r["merger_break"] and r["gain_pct"] is not None]
    B.sort(key=lambda r: -r["gain_pct"])
    fig, ax = plt.subplots(figsize=(8.0, 4.1))
    xs = range(len(B))
    ax.bar(xs, [r["gain_pct"] for r in B], color=[T1 if r["tier"] == "regional-1" else T2 for r in B], width=0.8)
    import statistics as st
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    handles = []
    for tier, col, lab in (("regional-1", T1, "First-tier regional banks"), ("regional-2", T2, "Second-tier regional banks")):
        m = st.median(r["gain_pct"] for r in B if r["tier"] == tier)
        ax.axhline(m, color=col, lw=1, ls="--")
        handles += [Patch(color=col, label=lab), Line2D([0], [0], color=col, lw=1, ls="--", label="%s, median %s" % (lab.split(" regional")[0], pct(m)))]
    ax.legend(handles=handles, loc="upper right", frameon=False, fontsize=8.2, ncol=2)
    for i in (0, 1, len(B) - 1):
        r = B[i]
        ax.annotate(NAMES.get(r["fi_code"], r["fi_code"]), (i, r["gain_pct"]), xytext=(4 if i < 3 else -4, 3 if r["gain_pct"] >= 0 else -10),
                    textcoords="offset points", fontsize=7.6, color=INK, ha="left" if i < 3 else "right")
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xticks([])
    ax.set_xlim(-1, len(B))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    frame(ax, "Second-tier regional banks raised net interest income a third as much",
          "Change in net interest income, FY2025 against FY2023, per bank, ranked (%d banks)" % len(B),
          CREDIT + "Source: Japanese Bankers Association (non-consolidated). Net interest income = interest income − interest expenses (calculated). Banks merged in the period excluded.",
          source_gap=14)
    save(fig, "02-gain-by-bank.png")


# 3 --------------------------------------------------------------- driver
def chart_driver():
    long = {}
    for r in rows("jba_banks_long.csv"):
        long[(r["fi_code"], r["line"], r["period"])] = num(r["yen_mn"])
    B = [r for r in bank_rows() if not r["merger_break"] and r["gain_pct"] is not None]
    fig, ax = plt.subplots(figsize=(8.0, 4.1))
    for tier, col, lab in (("regional-1", T1, "First-tier"), ("regional-2", T2, "Second-tier")):
        pts = [(long[(r["fi_code"], "d035", "2024-03")] / long[(r["fi_code"], "d990", "2024-03")] * 100, r["gain_pct"], r["fi_code"])
               for r in B if r["tier"] == tier]
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=22, color=col, alpha=0.85, label=lab, edgecolors="white", linewidths=0.5)
        for x, y, c in pts:
            if c in ("0129", "0128", "0144", "0146", "0582", "0578"):
                ax.annotate(NAMES.get(c, c), (x, y), xytext=(4, 2), textcoords="offset points", fontsize=7.4, color=INK)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.set_xlabel("Deposits with banks (including the Bank of Japan) as a share of total assets, March 2024", fontsize=8.4)
    ax.legend(loc="lower right", frameon=False, fontsize=8.2)
    N = json.load(open(os.path.join(DATA, "numbers.json")))
    frame(ax, "Banks holding more of their assets as deposits with banks gained most",
          "Change in net interest income, FY2025 against FY2023, against the share held as deposits with banks before the first increase (rank correlation %.2f)" % N["spearman_dueshare_gainpct"],
          CREDIT + "Source: Japanese Bankers Association (non-consolidated); shares and changes calculated. Deposits with banks include current-account balances at the Bank of Japan.",
          source_gap=40)
    save(fig, "03-driver.png")


# 4 --------------------------------------------------------------- gain vs cost
def chart_net():
    B = [r for r in bank_rows() if not r["merger_break"] and r["net_cum_to_na"] is not None]
    fig, ax = plt.subplots(figsize=(8.0, 4.3))
    for tier, col, lab in (("regional-1", T1, "First-tier"), ("regional-2", T2, "Second-tier")):
        pts = [r for r in B if r["tier"] == tier]
        ax.scatter([r["gain_cum_to_na"] for r in pts], [-r["bond_cum_to_na"] for r in pts], s=22, color=col, alpha=0.85,
                   edgecolors="white", linewidths=0.5, label=lab)
    xmax = max(r["gain_cum_to_na"] for r in B) * 1.12
    ymax = max(-r["bond_cum_to_na"] for r in B) * 1.06
    lo = min(0, min(r["gain_cum_to_na"] for r in B)) - 1
    ax.plot([lo, xmax], [lo, xmax], color=MUTED, lw=1, ls="--")
    ax.text(xmax * 0.99, xmax * 0.99 + 1.2, "cost = gain", color=MUTED, fontsize=7.6, ha="right", va="bottom")
    ax.text(xmax * 0.55, ymax * 0.92, "Above the line: realised bond losses\nexceeded the extra interest income", color=MUTED, fontsize=7.8, va="top")
    for r in B:
        if r["fi_code"] in ("0516", "0517", "0120", "0146", "0569", "0162", "0128", "0565"):
            ax.annotate(NAMES.get(r["fi_code"]), (r["gain_cum_to_na"], -r["bond_cum_to_na"]), xytext=(4, 2), textcoords="offset points", fontsize=7.4)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.set_xlabel("Extra net interest income over FY2024–FY2025 against FY2023, % of net assets (March 2026)", fontsize=8.4)
    ax.set_ylabel("Realised bond losses FY2024–FY2025, % of net assets", fontsize=8.4)
    ax.legend(loc="lower right", frameon=False, fontsize=8.2)
    ax.set_xlim(lo, xmax); ax.set_ylim(min(-2, min(-r["bond_cum_to_na"] for r in B) - 1), ymax)
    N = json.load(open(os.path.join(DATA, "numbers.json")))
    frame(ax, "%d of %d banks realised more in bond losses than they gained in interest income" % (N["h1_n"] - N["h1_net_cum_positive"], N["h1_n"]),
          "Each bank's two-year gain against its two-year realised bond result, both scaled to net assets",
          CREDIT + "Source: Japanese Bankers Association (non-consolidated). Bond result = gains on sale and redemption of bonds − losses on sale, redemption and devaluation (calculated). Merged banks excluded.",
          source_gap=40)
    save(fig, "04-gain-vs-cost.png")


# 5 --------------------------------------------------------------- next increase
def chart_next():
    B = [r for r in bank_rows() if r.get("irrbb_as_of") == "2026-03-31" and r["eve_up_to_t1"] is not None]
    fig, ax = plt.subplots(figsize=(8.0, 4.1))
    fall = [r for r in B if r["eve_max_scenario"] in ("parallel_down", "short_down", "flattener")]
    rise = [r for r in B if r not in fall]
    ax.scatter([r["eve_up_to_t1"] for r in rise], [r["eve_max_to_t1"] for r in rise], s=22, color=T1, alpha=0.85, edgecolors="white", linewidths=0.5,
               label="Worst case is a rising-rate scenario (%d)" % len(rise))
    ax.scatter([r["eve_up_to_t1"] for r in fall], [r["eve_max_to_t1"] for r in fall], s=22, color=T2, alpha=0.85, edgecolors="white", linewidths=0.5,
               label="Worst case is a falling-rate scenario (%d)" % len(fall))
    xmax = max(r["eve_up_to_t1"] for r in B) * 1.15
    ax.plot([0, xmax], [0, xmax], color=MUTED, lw=1, ls="--")
    ax.text(xmax * 0.98, xmax * 0.98 + 1.0, "worst case = parallel rise", color=MUTED, fontsize=7.4, ha="right", va="bottom")
    for r in B:
        if r["fi_code"] in ("0517", "0116", "0184", "0183", "0578"):
            ax.annotate(NAMES.get(r["fi_code"]), (r["eve_up_to_t1"], r["eve_max_to_t1"]), xytext=(5, -3), textcoords="offset points", fontsize=7.4)
    ax.set_xlim(-0.5, xmax)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: pct(v)))
    ax.set_xlabel("ΔEVE under the prescribed parallel upward shock, % of Tier 1", fontsize=8.4)
    ax.set_ylabel("Largest ΔEVE of the six scenarios, % of Tier 1", fontsize=8.4)
    ax.legend(loc="upper right", frameon=False, fontsize=8.2)
    frame(ax, "For most banks the regulatory worst case is a fall in rates, not a rise",
          "Each bank's own IRRBB1 figures at March 2026 (%d banks)" % len(B),
          CREDIT + "Source: each bank's Basel Pillar 3 disclosure (IRRBB1), collected by Plover. Ratios are printed figures divided (calculated).",
          source_gap=40)
    save(fig, "05-next-increase.png")


if __name__ == "__main__":
    chart_rates(); chart_gain(); chart_driver(); chart_net(); chart_next()
