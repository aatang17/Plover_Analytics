# -*- coding: utf-8 -*-
"""The four charts, built from data/derived.csv and data/AMD_pay.csv — never from numbers typed here.

House style as in regional-banks-jgb/charts.py: Plover series colours in order, horizontal
gridlines only, tabular figures, true minus sign, source line embedded, light theme.

  python charts.py        # writes out/charts/*.png
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out", "charts")
os.makedirs(OUT, exist_ok=True)

PALETTE = ["#1a4d8f", "#c2410c", "#0f766e", "#a16207", "#475569", "#9d174d"]
INK, MUTED, GRID = "#0f172a", "#64748b", "#e2e8f0"
MINUS = "−"
CREDIT = "Plover Analytics, as of 2026-09-30."

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "figure.dpi": 200, "savefig.dpi": 200,
    "axes.unicode_minus": True,
})

D = {r["key"]: float(r["value"]) for r in csv.DictReader(open(os.path.join(DATA, "derived.csv")))}


def get(key):
    return D.get(key)


def frame(ax, title, subtitle, source, source_gap=30):
    ax.set_title(title, loc="left", fontsize=12.5, color=INK, fontweight="bold", pad=30 if subtitle else 12)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.2, color=MUTED, va="bottom")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(False)
    if source:
        ax.annotate(source, xy=(0, 0), xycoords="axes fraction", xytext=(0, -source_gap),
                    textcoords="offset points", fontsize=7.2, color=MUTED, ha="left", va="top",
                    annotation_clip=False)


def money(v, _p=None):
    s = "{:,.0f}".format(abs(v))
    return (MINUS if v < 0 else "") + "$" + s


def pct(v, _p=None):
    return (MINUS if v < 0 else "") + "%.0f%%" % abs(v)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight", facecolor="white", pad_inches=0.28)
    plt.close(fig)
    print("  out/charts/%s" % name)


# 1. Balance sheet, 2012-2021 --------------------------------------------------------------
def chart_balance():
    ys = list(range(2012, 2022))
    eq = {}
    for r in csv.DictReader(l for l in open(os.path.join(DATA, "AMD_StockholdersEquity.csv")) if not l.startswith("#")):
        y = int(r["period_end"][:4])
        if y in ys:
            eq[y] = float(r["value"]) / 1e6
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    series = [("Debt", [get("AMD debt %d" % y) for y in ys], PALETTE[1]),
              ("Cash and short-term investments", [get("AMD cash+STI %d" % y) for y in ys], PALETTE[0]),
              ("Stockholders' equity", [eq.get(y) for y in ys], PALETTE[2])]
    for label, v, c in series:
        ax.plot(ys, v, color=c, linewidth=1.8, marker="o", markersize=3.2, label=label)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.axvline(2014.77, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(2014.85, ax.get_ylim()[1] * 0.97, "Su appointed\nCEO, Oct 2014", fontsize=7.6, color=MUTED, va="top")
    ax.set_ylim(-1300, 8000)
    ax.annotate("%s%s" % (MINUS, "$350m"), xy=(2015, eq[2015]), xytext=(2015.2, -1050),
                fontsize=7.8, color=PALETTE[2], arrowprops=dict(arrowstyle="-", color=PALETTE[2], lw=0.7))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: money(v) + "m"))
    ax.set_xticks(ys)
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=3, bbox_to_anchor=(0, -0.09))
    frame(ax, "Equity turned negative in 2015; debt was all but retired by 2021",
          "AMD, year-end, $m", "Source: AMD Forms 10-K (SEC company facts). " + CREDIT, source_gap=42)
    save(fig, "c1_balance_sheet.png")


# 2. Revenue and gross margin, 2012-2025 ---------------------------------------------------
def chart_revenue():
    ys = list(range(2012, 2026))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, 3.2), gridspec_kw={"wspace": 0.28})
    a1.bar(ys, [get("AMD rev %d" % y) / 1000 for y in ys], color=PALETTE[0], width=0.7)
    a1.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "$%.0fbn" % v))
    a1.set_xticks([2012, 2015, 2018, 2021, 2024])
    a1.text(0, 1.02, "Revenue, $bn", transform=a1.transAxes, fontsize=9.2, color=MUTED, va="bottom")
    a2.plot(ys, [get("AMD GM %d" % y) for y in ys], color=PALETTE[1], linewidth=1.8, marker="o", markersize=3.2)
    a2.yaxis.set_major_formatter(FuncFormatter(pct))
    a2.set_ylim(15, 55)
    a2.set_xticks([2012, 2015, 2018, 2021, 2024])
    a2.text(0, 1.02, "Gross margin, % (calculated)", transform=a2.transAxes, fontsize=9.2, color=MUTED, va="bottom")
    for a in (a1, a2):
        frame(a, "", "", "")
        a.axvline(2014.77, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
    a2.annotate("23.2%", xy=(2016, get("AMD GM 2016")), xytext=(2016.3, 18), fontsize=7.8, color=PALETTE[1])
    a2.annotate("48.2%", xy=(2021, get("AMD GM 2021")), xytext=(2019.6, 51), fontsize=7.8, color=PALETTE[1])
    fig.suptitle("Revenue quadrupled from 2015 to 2021 while gross margin rose 25pp from 2016", x=0.125, ha="left",
                 fontsize=12.5, fontweight="bold", y=1.04)
    fig.text(0.125, -0.04, "Source: AMD Forms 10-K (SEC company facts). Gross margin = gross profit ÷ revenue. "
             "Dashed line: Su appointed CEO, Oct 2014. " + CREDIT, fontsize=7.2, color=MUTED)
    save(fig, "c2_revenue_margin.png")


# 3. Segments, 2020-2025 -------------------------------------------------------------------
def chart_segments():
    ys = list(range(2020, 2026))
    def seg(name, y):
        if y == 2025 and name in ("Client", "Gaming"):
            return get("seg ClientAndGaming/%s 2025" % name) if name == "Client" else get("seg Gaming/Gaming 2025")
        return get("seg %s %d" % (name, y))
    parts = [("Data Center", "Datacenter", PALETTE[0]), ("Client", "Client", PALETTE[4]),
             ("Gaming", "Gaming", PALETTE[3]), ("Embedded", "Embedded", PALETTE[2])]
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    base = [0.0] * len(ys)
    for label, key, c in parts:
        v = [seg(key, y) / 1000 for y in ys]
        ax.bar(ys, v, bottom=base, color=c, width=0.62, label=label, edgecolor="white", linewidth=0.6)
        base = [b + x for b, x in zip(base, v)]
    for y in ys:
        s = get("DC share %d" % y)
        ax.text(y, seg("Datacenter", y) / 2000, "%.0f%%" % s, ha="center", va="center", fontsize=7.6, color="white")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "$%.0fbn" % v))
    ax.set_xticks(ys)
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=4, bbox_to_anchor=(0, -0.09))
    frame(ax, "Data Center reached about half of AMD's revenue in 2024",
          "Revenue by segment, $bn; label = Data Center share of total (calculated)",
          "Source: AMD Forms 10-K, XBRL segment facts (SEC EDGAR). FY2025 reports Client and Gaming as one segment; "
          "its Client and Gaming product lines are shown. " + CREDIT, source_gap=42)
    save(fig, "c3_segments.png")


# 4. Shareholder return vs index, 2020-2025 ------------------------------------------------
def chart_tsr():
    r = list(csv.DictReader(l for l in open(os.path.join(DATA, "AMD_pay.csv")) if not l.startswith("#")))
    ys = [int(x["fiscal_year"]) for x in r]
    fig, ax = plt.subplots(figsize=(8.6, 3.2))
    ax.plot(ys, [float(x["tsr"]) for x in r], color=PALETTE[0], linewidth=1.8, marker="o", markersize=3.2, label="AMD")
    ax.plot(ys, [float(x["peer_tsr"]) for x in r], color=PALETTE[1], linewidth=1.8, marker="o", markersize=3.2,
            label="S&P 500 Semiconductors & Semiconductor Equipment Index")
    for x in r:
        if x["fiscal_year"] in ("2023", "2025"):
            a_up = x["fiscal_year"] == "2023"  # AMD above the index in 2023, below it in 2025
            ax.annotate("%.0f" % float(x["tsr"]), xy=(int(x["fiscal_year"]), float(x["tsr"])),
                        xytext=(-4, 7) if a_up else (4, -13), ha="right" if a_up else "left",
                        textcoords="offset points", fontsize=7.8, color=PALETTE[0])
            ax.annotate("%.0f" % float(x["peer_tsr"]), xy=(int(x["fiscal_year"]), float(x["peer_tsr"])),
                        xytext=(6, -12) if a_up else (4, 5),
                        textcoords="offset points", fontsize=7.8, color=PALETTE[1])
    ax.set_xticks(ys)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "$%.0f" % v))
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=2, bbox_to_anchor=(0, -0.09))
    frame(ax, "AMD led the semiconductor index to 2023 and trailed it from 2024",
          "Value of a fixed $100 investment, cumulative, as filed in AMD's proxy statements",
          "Source: AMD DEF 14A Pay versus Performance tables, 2023–2026. The base date is uncertain (see Limitations); "
          "AMD and the index share it. " + CREDIT, source_gap=42)
    save(fig, "c4_tsr.png")


if __name__ == "__main__":
    chart_balance()
    chart_revenue()
    chart_segments()
    chart_tsr()
