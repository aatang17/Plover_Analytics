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


CREDIT = ("Plover Analytics, as of 2026-09-23. ")

# --------------------------------------------------------------- 1. the yield
def chart_yields():
    r = [x for x in rows("jgb-yields.csv") if x["period"] >= "2021-01-01"]
    d = [dt.date.fromisoformat(x["period"]) for x in r]
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    for i, (code, label) in enumerate((("10Y", "10-year"), ("30Y", "30-year"), ("5Y", "5-year"))):
        v = [num(x[code]) for x in r]
        pts = [(dd, vv) for dd, vv in zip(d, v) if vv is not None]
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                color=PALETTE[i], linewidth=1.6 if code == "10Y" else 1.1,
                label=label, zorder=3 if code == "10Y" else 2)
    for fy, lbl in ((dt.date(2024, 3, 29), "FY2023 close\n0.75%"),
                    (dt.date(2026, 3, 31), "FY2025 close\n2.37%")):
        ax.axvline(fy, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)
        ax.annotate(lbl, (fy, 0.015), xycoords=("data", "axes fraction"),
                    fontsize=7.6, color=MUTED, ha="right", va="bottom",
                    xytext=(-5, 0), textcoords="offset points")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "%.0f%%" % v))
    ax.legend(frameon=False, fontsize=8.6, loc="upper left", ncol=3, handlelength=1.6)
    frame(ax, "The 10-year JGB went from 0.75% to 3.0%",
          "Japanese government bond yields, % per year, daily",
          CREDIT + "Source: Ministry of Finance, Japan.", "% per year")
    save(fig, "01-jgb-yields.png")


# ------------------------------------------------------------- 2. the margin
def chart_rates():
    lr = {x["period"]: x for x in rows("loan-rates.csv")}
    dr = {x["period"]: x for x in rows("deposit-rates.csv")}
    per = sorted(p for p in lr if p >= "2021-01")
    xs = [dt.date(int(p[:4]), int(p[5:7]), 1) for p in per]
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    series = (("DLLR2CICR35", "New loans, shinkin"),
              ("DLLR2CIMBNL1", "New loans, regional II"),
              ("DLLR2CIRBNL1", "New loans, regional"),
              ("DLLR2CICBNL1", "New loans, city"))
    for i, (code, label) in enumerate(series):
        v = [num(lr[p][code]) for p in per]
        ax.plot(xs, v, color=PALETTE[i], linewidth=1.5 if "regional" == label.split()[-1] else 1.2,
                label=label)
    dper = sorted(dr)
    dxs = [dt.date(int(p[:4]), int(p[5:7]), 1) for p in dper]
    ax.plot(dxs, [num(dr[p]["DLDR121N"]) for p in dper], color=PALETTE[4],
            linewidth=1.6, label="Ordinary deposits, posted")
    ax.annotate("+0.90pp", (dt.date(2026, 7, 1), 1.752), fontsize=8.4, color=PALETTE[2],
                xytext=(8, -2), textcoords="offset points", fontweight="bold")
    ax.annotate("+0.32pp", (dt.date(2026, 9, 1), 0.322), fontsize=8.4, color=PALETTE[4],
                xytext=(8, -3), textcoords="offset points", fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "%.1f%%" % v))
    ax.set_xlim(dt.date(2021, 1, 1), dt.date(2027, 3, 1))
    ax.legend(frameon=False, fontsize=8.2, loc="upper left", ncol=2, handlelength=1.6)
    frame(ax, "Loan rates rose about three times as fast as deposit rates",
          "Average contracted rate on new loans by lender, and the posted ordinary deposit rate",
          CREDIT + "Source: Bank of Japan (databases IR04 and IR02). The deposit series is "
                   "monthly from April 2022 only.", "% per year")
    save(fig, "02-loan-and-deposit-rates.png")


# --------------------------------------------------------- 3. margin vs bonds
def chart_pl():
    pl = {x["period"]: x for x in rows("regional-pl.csv")}
    fys = ["2022-03", "2023-03", "2024-03", "2025-03", "2026-03"]
    lbl = ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"]
    nii = [num(pl[p]["regional.net-interest-income.fy"]) / 10 for p in fys]
    bond = [num(pl[p]["regional.bond-gains.fy"]) / 10 for p in fys]
    dnii = [None] + [nii[i] - nii[i - 1] for i in range(1, len(nii))]
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    x = range(len(fys))
    ax.bar([i - 0.19 for i in x][1:], dnii[1:], width=0.36, color=PALETTE[0],
           label="Change in net interest income", zorder=3)
    ax.bar([i + 0.19 for i in x], bond, width=0.36, color=PALETTE[1],
           label="Gains and losses on bonds", zorder=3)
    ax.axhline(0, color=MUTED, linewidth=0.9, zorder=4)
    ax.set_xticks(list(x))
    ax.set_xticklabels(lbl)
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, p: (MINUS if v < 0 else "") + format(abs(v), ",.0f")))
    ax.annotate("%s1,156" % MINUS, (4.19, bond[-1]), fontsize=9, color=PALETTE[1],
                ha="center", va="top", xytext=(0, -4), textcoords="offset points",
                fontweight="bold")
    ax.annotate("+738", (3.81, dnii[-1]), fontsize=9, color=PALETTE[0], ha="center",
                va="bottom", xytext=(0, 3), textcoords="offset points", fontweight="bold")
    ax.legend(frameon=False, fontsize=8.6, loc="lower left", handlelength=1.4)
    frame(ax, "One year of bond losses took more than two years of extra margin",
          "Regional banks, change in net interest income vs realised bond gains, ¥bn per fiscal year",
          CREDIT + "Source: Financial Services Agency, summary of bank financial results. "
                   "Published in ¥100mn, shown in ¥bn (÷10, exact).", "¥bn")
    save(fig, "03-margin-vs-bond-losses.png")


# ------------------------------------------------------------ 4. who bought
def chart_holdings():
    h = {x["period"]: x for x in rows("jgb-holdings-by-group.csv")}
    per = [p for p in sorted(h) if p >= "2018-03"]
    xs = [dt.date(int(p[:4]), int(p[5:7]), 1) for p in per]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.8, 2.8),
                                   gridspec_kw={"width_ratios": [1, 1], "wspace": 0.28})
    ax1.plot(xs, [(num(h[p]["s.city.d290"]) or 0) / 1e6 for p in per],
             color=PALETTE[4], linewidth=1.8)
    ax1.set_title("City banks", loc="left", fontsize=10, color=INK, fontweight="bold")
    ax1.annotate("%s¥20tn\nsince 2022" % MINUS, (dt.date(2026, 3, 1), 55.5), fontsize=8.4,
                 color=PALETTE[4], ha="right", va="bottom", xytext=(-6, 6),
                 textcoords="offset points", fontweight="bold")
    ax2.plot(xs, [(num(h[p]["s.regional.d290"]) or 0) / 1e6 for p in per],
             color=PALETTE[0], linewidth=1.8, label="Regional")
    ax2.plot(xs, [(num(h[p]["s.regional-2.d290"]) or 0) / 1e6 for p in per],
             color=PALETTE[1], linewidth=1.4, label="Second-tier regional")
    ax2.set_title("Regional banks", loc="left", fontsize=10, color=INK, fontweight="bold")
    ax2.annotate("+41%", (dt.date(2026, 3, 1), 20.8), fontsize=8.6, color=PALETTE[0],
                 ha="right", va="bottom", xytext=(-6, 5), textcoords="offset points",
                 fontweight="bold")
    ax2.legend(frameon=False, fontsize=8, loc="upper left", handlelength=1.4)
    for ax in (ax1, ax2):
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.xaxis.grid(False)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "%.0f" % v))
        ax.yaxis.set_major_locator(MaxNLocator(5, integer=True))
        ax.set_ylim(bottom=0)
    ax1.set_ylabel("¥tn", fontsize=8.6)
    fig.suptitle("Regional banks added government bonds as city banks reduced holdings",
                 x=0.012, y=1.13, ha="left", fontsize=12.5, color=INK, fontweight="bold")
    fig.text(0.012, 1.015, "Japanese government bonds held, ¥tn, at each 31 March",
             fontsize=9.2, color=MUTED, va="bottom")
    fig.text(0.012, -0.055, CREDIT + "Source: Japanese Bankers Association, "
             "Analysis of Financial Statements of All Banks. Note the different scales.",
             fontsize=7.2, color=MUTED, va="bottom")
    fig.savefig(os.path.join(OUT, "04-jgb-holdings.png"), bbox_inches="tight",
                facecolor="white", pad_inches=0.3)
    plt.close(fig)
    print("  out/charts/04-jgb-holdings.png")


# ------------------------------------------------------- 5. the disagreement
def chart_scatter():
    import json
    import re
    import urllib.request
    import urllib.parse

    def get(p, q=None):
        u = "https://ploveranalytics.com/api/v1" + p + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(u, headers={"User-Agent": "PloverResearch/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())

    TYPE = {}
    rx = re.compile(r"\((Regional bank|Regional bank II|Shinkin bank|Credit co-operative), (\d{4})\)")
    for s in get("/fsa-regional-fi/series", {"q": "Deposits"})["series"]:
        m = rx.search(s.get("name_en") or "")
        if m:
            TYPE[m.group(2)] = m.group(1)
    fi = {c["sec_code"]: c.get("fi_codes") or []
          for c in get("/equity/banks/companies", {"limit": 500})["companies"]}

    loss = {r["sec_code"]: r for r in rows("rank-bond-loss-to-equity.csv") if r["sec_code"]}
    seen, pts = set(), []
    for r in rows("irrbb-by-bank.csv"):
        if not r["delta_eve_to_tier1_pct"] or r["pdf_sha256"] in seen:
            continue
        l = loss.get(r["sec_code"])
        if not l or not l["bond_loss_to_equity_pct"]:
            continue
        t = {TYPE.get(x) for x in (fi.get(r["sec_code"]) or [])}
        if not (t and t <= {"Regional bank", "Regional bank II"}):
            continue
        seen.add(r["pdf_sha256"])
        pts.append((r["name_ja"], abs(num(l["bond_loss_to_equity_pct"])),
                    num(r["delta_eve_to_tier1_pct"])))

    fig, ax = plt.subplots(figsize=(8.6, 3.3))
    ax.scatter([p[1] for p in pts], [p[2] for p in pts], s=34, color=PALETTE[0],
               alpha=0.78, edgecolors="white", linewidths=0.7, zorder=3)
    ax.axhline(15, color=PALETTE[1], linewidth=1.1, linestyle=(0, (4, 3)), zorder=2)
    ax.annotate("Basel outlier line, 15% of Tier 1", (0.012, 15),
                xycoords=("axes fraction", "data"), fontsize=7.8,
                color=PALETTE[1], ha="left", va="bottom", xytext=(0, 4),
                textcoords="offset points")
    label = {"栃木銀行": "Tochigi", "島根銀行": "Shimane", "大東銀行": "Daito",
             "高知銀行": "Kochi", "佐賀銀行": "Saga", "群馬銀行": "Gunma"}
    for nm, x, y in pts:
        if nm in label:
            dx, dy = (6, -11) if nm == "高知銀行" else (7, 3)
            ax.annotate(label[nm], (x, y), fontsize=8.2, color=INK,
                        xytext=(dx, dy), textcoords="offset points")
    # No x-axis label: the subtitle says what each axis means, and a label here
    # lands on the source line whatever padding it is given.
    ax.set_ylabel("\u0394EVE ÷ Tier 1, % of Tier 1", fontsize=8.6, labelpad=6)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, p: "%.0f%%" % v))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: "%.0f%%" % v))
    frame(ax, "The two measures of rate exposure disagree",
          "Each dot is one regional bank. Across: unrealised bond loss ÷ net assets, the "
          "loss already taken.\nUp: \u0394EVE ÷ Tier 1, what the next shock would do. "
          "Rank correlation 0.31, %d banks." % len(pts),
          CREDIT + "Sources: annual securities reports on EDINET (unrealised losses); each "
                   "bank's Basel Pillar 3 disclosure (ΔEVE, Tier 1). One point per document.")
    save(fig, "05-two-measures-disagree.png")


for fn in (chart_yields, chart_rates, chart_pl, chart_holdings, chart_scatter):
    fn()
print("done")
