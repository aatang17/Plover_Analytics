# -*- coding: utf-8 -*-
"""One news chart from one Plover CSV export — never from numbers typed here.

The CSV is the file saved from /api/v1/{dataset}/observations?...&format=csv. Its
`#` header block names the source, the release and the unit; the source line
embedded in the image is read from it, so a screenshotted chart still says where
it came from.

House style matches the research charts: Plover series colours in order,
horizontal gridlines only, no border, tabular figures, a true minus sign, light
theme (the one that survives being pasted anywhere). 1600 x 900 px.

  python chart.py --csv data/10y.csv --out out/chart.png \
      --title "Japan's 10-year yield is at its highest since 1996" \
      --subtitle "10-year JGB yield, daily close, %" \
      --mark-last --hline "3:3%"

  --kind bar            bars instead of a line (monthly or quarterly counts, flows)
  --series 10Y,2Y       columns to draw (default: every column), max six
  --labels "10-year,2-year"   legend names, same order as --series
  --start 1996-01-01    drop rows before this period
  --mark-last           dot and value label on the newest point of each series
  --mark 1996-08-27     dot and value label on that period (first series); repeatable
  --hline "3:3%"        reference line at a value, with a label; repeatable
  --decimals 2          decimals in value labels (default 2)

Runs on Python 3.9 (the local venv): observatory/.venv/bin/python chart.py ...
"""
import argparse
import csv
import datetime as dt
import os
import re
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

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


def read_csv(path):
    """Header comments and data rows of a Plover CSV export."""
    header, body = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            (header if line.startswith("#") else body).append(line)
    return header, list(csv.DictReader(body))


def header_value(header, key):
    for line in header:
        text = line.lstrip("#").strip()
        if text.startswith(key + ":"):
            return text[len(key) + 1:].strip()
    return None


def source_line(header):
    """'Source: Ministry of Finance, Japan. Data through 29 September 2026. ploveranalytics.com'"""
    source = header_value(header, "Source") or ""
    # The chart is for English readers and these fonts have no Japanese glyphs:
    # drop a Japanese gloss such as "(米の相対取引価格)" rather than print boxes.
    source = re.sub(r"\s*\([^()]*[^\x00-\u024f\u2000-\u206f][^()]*\)", "", source)
    release = (header_value(header, "Release") or "").split(";")[0].strip()
    parts = ["Source: " + source.rstrip(".") + "." if source else "",
             release + "." if release else "",
             "ploveranalytics.com"]
    return " ".join(p for p in parts if p)


def num(v):
    # Blank is missing, never zero: a gap stays a gap in the line.
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def period(v):
    # Daily exports give 2026-09-29; monthly and quarterly ones give 2026-09.
    v = v.strip()[:10]
    return dt.datetime.strptime(v if len(v) == 10 else v + "-01", "%Y-%m-%d").date()


def fmt(v, decimals):
    return "{:,.{d}f}".format(v, d=decimals).replace("-", MINUS)


def tick(v, _):
    return "{:,.10g}".format(v).replace("-", MINUS)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--kind", choices=["line", "bar"], default="line")
    ap.add_argument("--series", default="")
    ap.add_argument("--labels", default="")
    ap.add_argument("--start", default="")
    ap.add_argument("--mark-last", action="store_true")
    ap.add_argument("--mark", action="append", default=[])
    ap.add_argument("--hline", action="append", default=[])
    ap.add_argument("--decimals", type=int, default=2)
    a = ap.parse_args()

    header, data = read_csv(a.csv)
    if not data:
        raise SystemExit("no data rows in %s" % a.csv)
    cols = [c for c in a.series.split(",") if c] or [c for c in data[0] if c != "period"]
    if len(cols) > 6:
        raise SystemExit("six series at most; got %d" % len(cols))
    labels = [l for l in a.labels.split(",") if l] or cols
    if len(labels) != len(cols):
        raise SystemExit("--labels must match --series one for one")
    if a.start:
        data = [r for r in data if r["period"] >= a.start[:len(r["period"])]]
    xs = [period(r["period"]) for r in data]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, col in enumerate(cols):
        ys = [num(r.get(col)) for r in data]
        colour = PALETTE[i]
        if a.kind == "bar":
            span = (xs[-1] - xs[0]).days / max(len(xs) - 1, 1)
            width = span * 0.8 / len(cols)
            offs = [x + dt.timedelta(days=(i - (len(cols) - 1) / 2.0) * width) for x in xs]
            ax.bar(offs, [y if y is not None else 0 for y in ys], width=width,
                   color=[colour if y is not None else "none" for y in ys], label=labels[i])
        else:
            ax.plot(xs, [y if y is not None else float("nan") for y in ys],
                    color=colour, linewidth=1.6, label=labels[i])
        if a.mark_last:
            last = [(x, y) for x, y in zip(xs, ys) if y is not None]
            if last:
                x, y = last[-1]
                if a.kind == "line":
                    ax.plot([x], [y], "o", color=colour, markersize=4)
                ax.annotate(fmt(y, a.decimals), (x, y), xytext=(6, 0),
                            textcoords="offset points", va="center",
                            color=colour, fontsize=9.5, fontweight="bold")
        if i == 0:
            by_period = dict(zip([r["period"] for r in data], ys))
            for m in a.mark:
                y = by_period.get(m)
                if y is None:
                    raise SystemExit("--mark %s: no value for that period in %s" % (m, col))
                ax.plot([period(m)], [y], "o", color=colour, markersize=4)
                ax.annotate("%s\n%s" % (fmt(y, a.decimals), period(m).strftime("%-d %b %Y")),
                            (period(m), y), xytext=(6, 8), textcoords="offset points",
                            color=INK, fontsize=8.5)

    for h in a.hline:
        value, _, label = h.partition(":")
        # Stops at the newest point so it never strikes through the value label.
        ax.plot([xs[0], xs[-1]], [float(value)] * 2, color=MUTED, linewidth=0.9,
                linestyle=(0, (4, 3)), zorder=1)
        if label:
            ax.annotate(label, (xs[0], float(value)), xytext=(2, 3),
                        textcoords="offset points", color=MUTED, fontsize=8.5)

    ax.set_title(a.title, loc="left", fontsize=12.5, color=INK, fontweight="bold",
                 pad=32 if a.subtitle else 12)
    if a.subtitle:
        ax.text(0, 1.016, a.subtitle, transform=ax.transAxes, fontsize=9.2,
                color=MUTED, va="bottom")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(False)
    ax.tick_params(length=0)
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_formatter(FuncFormatter(tick))
    ax.margins(x=0.01)
    if a.mark_last:
        # Room on the right for the value label.
        lo, hi = ax.get_xlim()
        ax.set_xlim(lo, hi + (hi - lo) * 0.07)
    if len(cols) > 1:
        # "best" keeps the legend off the lines; a fixed corner sat on the data.
        ax.legend(frameon=False, loc="best", fontsize=9)

    # A two-source line runs past the right edge unless it wraps.
    source = textwrap.wrap(source_line(header), 135)
    fig.text(0.01, 0.012, "\n".join(source), fontsize=7.8, color=MUTED, ha="left",
             va="bottom", linespacing=1.35)
    fig.tight_layout(rect=(0, 0.015 + 0.03 * len(source), 1, 1))
    out_dir = os.path.dirname(os.path.abspath(a.out))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    fig.savefig(a.out, facecolor="white")
    print("wrote %s" % a.out)


if __name__ == "__main__":
    main()
