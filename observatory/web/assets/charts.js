/* House chart chrome for ECharts. All colours come from tokens via cssVar();
   never pass a hex literal here or in page code.
   Chrome rules: horizontal gridlines only, no chart border or background,
   2px lines, crosshair tooltip with all series, zero line when data crosses
   zero, source line included in PNG exports, light-theme exports by default. */
"use strict";

function readPalette() {
  return {
    ink: cssVar("--obs-ink"),
    text: cssVar("--obs-text"),
    muted: cssVar("--obs-text-muted"),
    border: cssVar("--obs-border"),
    grid: cssVar("--obs-grid"),
    surface: cssVar("--obs-surface"),
    subtle: cssVar("--obs-surface-subtle"),
    series: [1, 2, 3, 4, 5, 6].map(i => cssVar("--obs-series-" + i)),
    divergePos: cssVar("--obs-diverge-pos"),
    divergeNeg: cssVar("--obs-diverge-neg"),
  };
}

/* palette for an explicit theme (used for light-mode PNG export) */
function paletteFor(theme) {
  const root = document.documentElement;
  const prev = root.getAttribute("data-theme");
  root.setAttribute("data-theme", theme);
  const p = readPalette();
  if (prev === null) root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", prev);
  return p;
}

function axisCommon(pal) {
  return {
    axisLine: { lineStyle: { color: pal.border } },
    axisTick: { show: false },
    axisLabel: { color: pal.muted, fontSize: 11 },
  };
}

/* Legend geometry, worked out before drawing so a chart can pick the layout
   its legend fits. It follows ECharts' own legend layout: icon, 5px, the name
   (2px left padding), itemGap between items, 5px padding round the box, each
   row as tall as the font, itemGap between rows. Names are measured in the
   face ECharts draws them in, not the page's. */
const LEGEND_FACE = /^Win/.test(navigator.platform || "") ? "Microsoft YaHei" : "sans-serif";

function legendItemWidths(names, fontSize, itemWidth) {
  let ctx = legendItemWidths._ctx;
  if (ctx === undefined) {
    try {
      ctx = document.createElement("canvas").getContext("2d");
    } catch (e) {
      ctx = null;
    }
    legendItemWidths._ctx = ctx;
  }
  if (ctx) ctx.font = fontSize + "px " + LEGEND_FACE;
  return names.map(n => itemWidth + 7 +
    (ctx ? ctx.measureText(String(n)).width : String(n).length * fontSize * 0.56));
}

/* Height of a legend laid out under the plot, wrapping at the chart's width. */
function legendHeightBelow(names, width, fontSize, itemWidth, itemGap) {
  const room = width - 10;
  let rows = 1, x = 0;
  legendItemWidths(names, fontSize, itemWidth).forEach(w => {
    if (x > 0 && x + itemGap + w > room) { rows++; x = w; }
    else x += (x > 0 ? itemGap : 0) + w;
  });
  return rows * fontSize + (rows - 1) * itemGap + 10;
}

/* Whether the legend fits on one row in the top-right corner, clear of the
   y-axis name at the top-left. If it does not, ECharts wraps it leftwards
   over the name and down into the plot, so the caller puts it underneath.
   The name starts at the y-axis line, which sits right of the widest tick
   label; that label is estimated from the largest value plotted. */
function legendFitsTop(names, width, cfg, values, itemWidth, itemGap) {
  const legendW = legendItemWidths(names, 12, itemWidth).reduce((a, w) => a + w, 0) +
    itemGap * (names.length - 1) + 10;
  let reserve = 0;
  if (cfg.yAxisName) {
    let maxAbs = 0, neg = false;
    values.forEach(v => {
      if (typeof v !== "number" || !isFinite(v)) return;
      if (v < 0) neg = true;
      maxAbs = Math.max(maxAbs, Math.abs(v));
    });
    // the top tick can be a digit longer than the data (8.6m on an axis to 10m)
    const tick = (neg ? "−" : "") + fmtNum(maxAbs * 1.25, cfg.yAxisDp || 0);
    const tickW = legendItemWidths([tick], 11, 0)[0] - 7;
    const nameW = legendItemWidths([cfg.yAxisName], 11, 0)[0] - 7;
    reserve = 8 + tickW + 8 + nameW + 16;
  }
  return legendW <= width - reserve;
}

/* A line chart's legend lists its series highest latest value first. */
function legendOrder(series) {
  const latestOf = s => {
    for (let i = s.points.length - 1; i >= 0; i--) if (s.points[i][1] !== null) return s.points[i][1];
    return -Infinity;
  };
  return series.slice().sort((a, b) => latestOf(b) - latestOf(a));
}

/* The legend names, in legend order, and plotted values of a chart that has
   a legend, or null. */
function legendNamesAndValues(kind, cfg) {
  const vals = [];
  if (kind === "line") {
    if (cfg.series.length < 2) return null;
    cfg.series.forEach(s => s.points.forEach(p => vals.push(p[1])));
    return { names: legendOrder(cfg.series).map(s => s.name), values: vals, itemWidth: 16, itemGap: 18 };
  }
  if (kind === "stack") {
    // stacked bars reach the sum of their positive (or negative) parts
    const n = cfg.series.length ? cfg.series[0].points.length : 0;
    for (let i = 0; i < n; i++) {
      let up = 0, down = 0;
      cfg.series.forEach(s => {
        const v = s.points[i] && s.points[i][1];
        if (typeof v === "number") { if (v > 0) up += v; else down += v; }
      });
      vals.push(up, down);
    }
    if (cfg.line) cfg.line.points.forEach(p => vals.push(p[1]));
    return { names: cfg.series.map(s => s.name).concat(cfg.line ? [cfg.line.name] : []),
             values: vals, itemWidth: 16, itemGap: 14 };
  }
  if (kind === "cols") {
    cfg.series.forEach(s => s.points.forEach(v => vals.push(v)));
    return { names: cfg.series.map(s => s.name), values: vals, itemWidth: 14, itemGap: 18 };
  }
  return null;
}

/* cfg: { series: [{name, slot, points: [[iso, v|null], ...]}],
          unit: "%" | "index", yAxisName, trust, sourceLine,
          annotations?: [{x: iso, y: value, text}] }
   Annotations mark real readings (a peak, a policy date) on the first
   series: a small filled point with a label. The caller supplies the
   coordinates from its own data — nothing is computed here. */
function lineOptions(cfg, pal, narrow, below) {
  // Annual data has no meaningful position between two points, so a fiscal-year
  // series uses a category axis: a time axis would label it by month and imply
  // readings we do not have. Time remains the default for every other caller.
  const isCat = cfg.xType === "category";
  const dp = cfg.dp !== undefined ? cfg.dp : (cfg.unit === "%" ? 2 : 1);
  let crossesZero = false;
  cfg.series.forEach(s => s.points.forEach(p => { if (p[1] !== null && p[1] < 0) crossesZero = true; }));

  const ordered = legendOrder(cfg.series);

  return {
    animation: false,
    color: pal.series,
    legend: Object.assign(
      {
        show: cfg.series.length > 1,
        itemWidth: 16, itemHeight: 8, itemGap: narrow ? 10 : 18,
        textStyle: { color: pal.text, fontSize: narrow ? 11 : 12, padding: [0, 0, 0, 2] },
        data: ordered.map(s => s.name),
      },
      // narrow containers: legend below the plot; wide: top-right, clear of
      // the y-axis name at top-left
      narrow ? { bottom: 0, left: 0 } : { top: 0, right: 0 }),
    grid: narrow
      // The legend sits under the plot when narrow and wraps to as many rows
      // as it needs. Two rows fit in 44px; a fourth series pushes it to three
      // and it lands on top of the axis labels, so reserve more for it.
      ? { left: 8, right: cfg.gridRight || 12,
          // Six long names wrap to four rows; a caller that knows its legend
          // is that tall reserves the room with legendBottomNarrow. `below`
          // is the measured height of the wrapped legend, which wins when
          // the names are longer than either guess allowed for.
          bottom: Math.max(cfg.legendBottomNarrow ||
            (cfg.series.length > 3 ? 70 : (cfg.series.length > 1 ? 44 : 8)), below || 0),
          containLabel: true,
          top: cfg.yAxisName ? 26 : 12 }
      // cfg.gridRight: room for a long last category label ("FY Dec-2025"),
      // centred on the right edge of a category axis. Opt-in.
      : { left: 8, right: cfg.gridRight || 20, bottom: 8, containLabel: true,
          top: cfg.series.length > 1 ? 34 : (cfg.yAxisName ? 28 : 16) },
    xAxis: Object.assign(axisCommon(pal), isCat
      ? { type: "category", boundaryGap: false, splitLine: { show: false },
          data: cfg.series[0].points.map(p => p[0]) }
      : { type: "time", splitLine: { show: false } }),
    yAxis: Object.assign(axisCommon(pal), {
      // A log axis is the honest way to put series orders of magnitude apart
      // on one chart: on a linear axis the smaller one is pinned to the
      // baseline and its shape is unreadable. Callers opt in and must label
      // it — a reader who misses the switch misreads every distance.
      type: cfg.logScale ? "log" : "value",
      scale: !cfg.logScale && cfg.unit !== "%",   // index levels never forced to zero
      // A reference level above every plotted value (a replacement fertility
      // rate, a policy target) is invisible unless the axis is told to reach
      // it: a markLine does not extend the scale. Opt-in only.
      max: cfg.yMax !== undefined ? cfg.yMax : null,
      // Whole-number series (counts of things) opt in with 1: without it a
      // range as narrow as 3 to 5 is split into half steps, and fixed-zero
      // tick labels then print "5, 5, 4, 4, 3". Unset for every other caller.
      minInterval: cfg.yAxisMinInterval,
      name: cfg.yAxisName || "",
      nameTextStyle: { color: pal.muted, fontSize: 11, align: "left" },
      axisLine: { show: false },
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
      // Opt-in fixed precision on the tick labels. ECharts drops trailing
      // zeros, so a scale running 3.12 / 3.09 / 3.06 / 3.03 prints a bare
      // "3" in the middle of the column — one precision per column is the
      // house rule. Only callers that ask for it are affected.
      axisLabel: cfg.yAxisDp === undefined
        ? { color: pal.muted, fontSize: 11 }
        : { color: pal.muted, fontSize: 11,
            formatter: v => fmtNum(v, cfg.yAxisDp) },
    }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: pal.muted, width: 1 } },
      backgroundColor: pal.surface,
      borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: params => {
        const date = isCat ? params[0].name
          : cfg.hourlyPeriods ? new Date(params[0].value[0]).toISOString().slice(0, 16).replace("T", " ") + " UTC"
          : fmtPeriod(new Date(params[0].value[0]).toISOString());
        const rows = params.slice()
          .sort((a, b) => ((isCat ? b.value : b.value[1]) ?? -Infinity) -
                          ((isCat ? a.value : a.value[1]) ?? -Infinity))
          .map(p => {
            const v = isCat ? p.value : p.value[1];
            const txt = v === null || v === undefined ? "—"
              : fmtNum(v, dp) + (cfg.unit === "%" ? "%"
                : (cfg.unitSuffix ? " " + cfg.unitSuffix : ""));
            return p.marker + " " + escapeHtml(p.seriesName) +
              ' <span class="num" style="float:right;margin-left:16px;font-weight:600">' + txt + "</span>";
          });
        const trust = TRUST_LABELS[cfg.trust] ? '<div style="margin-top:4px;font-size:11px;color:' +
          pal.muted + '">' + TRUST_LABELS[cfg.trust] + "</div>" : "";
        return '<div style="font-weight:600;margin-bottom:2px">' + date + "</div>" +
          rows.join("<br>") + trust;
      },
    },
    series: ordered.map(s => ({
      name: s.name,
      type: "line",
      // Annual series opt into a marker per reading (cfg.showPoints): with a
      // year between points there is nothing to interpolate, and a year
      // whose neighbours are both missing is otherwise drawn as nothing at
      // all — a published figure invisible on the chart.
      showSymbol: isCat || !!cfg.showPoints,
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { width: 2 },
      itemStyle: { color: pal.series[(s.slot - 1) % 6] },
      emphasis: { focus: "none" },
      data: isCat ? s.points.map(p => p[1]) : s.points.map(p => [p[0], p[1]]),
      // one markLine block carries the zero line, an optional horizontal
      // reference level (cfg.refLine: {y, label} — the 100 on a rebased
      // index, where the baseline is the whole point of the chart), and
      // any event rules (cfg.eventLines: [{x: iso, label}] — thin
      // vertical rules with a small label, for policy dates and
      // methodology breaks)
      markLine: (crossesZero || cfg.refLine
                 || (cfg.eventLines && cfg.eventLines.length))
          && s === ordered[0] ? {
        silent: true, symbol: "none",
        label: { show: false },
        lineStyle: { color: pal.muted, width: 1, type: "solid" },
        data: (crossesZero ? [{ yAxis: 0 }] : []).concat(
          cfg.refLine ? [{
            yAxis: cfg.refLine.y,
            lineStyle: { color: pal.muted, width: 1, type: "dashed" },
            label: { show: !!cfg.refLine.label, formatter: cfg.refLine.label,
                     position: "insideStartTop", color: pal.muted, fontSize: 10 },
          }] : []).concat(
          (cfg.eventLines || []).map(e => ({
            xAxis: e.x,
            lineStyle: { color: pal.border, width: 1, type: "dashed" },
            // stagger drops a label one line down, so two events close in
            // time (NIRP and YCC) don't print on top of each other
            // align "right" ends the label at the rule, for an event so
            // close to the latest date that a centred label runs off the plot
            label: { show: !!e.label, formatter: e.label, position: "end",
                     offset: e.stagger ? [0, 13] : [0, 0],
                     align: e.align || "center",
                     color: pal.muted, fontSize: 10 },
          }))),
      } : undefined,
      markPoint: cfg.annotations && cfg.annotations.length && s === ordered[0] ? {
        silent: true,
        symbol: "circle", symbolSize: 7,
        itemStyle: { color: pal.ink },
        // narrow plots: the label sits left of the point, since a peak near
        // the right edge would push a centered label off the canvas
        label: { show: true, position: narrow ? "left" : "top",
                 distance: 8, color: pal.ink, fontSize: 11, fontWeight: 600,
                 formatter: p => p.data.text },
        data: cfg.annotations.map(a => ({ coord: [a.x, a.y], text: a.text })),
      } : undefined,
    })),
  };
}

/* cfg: { series: [{name, slot, points}], line: {name, points} | null,
          unit: "pp", yAxisName, trust, sourceLine }
   Stacked bars per series (contributions) with an optional line overlay
   (the total the stacks decompose). */
function stackOptions(cfg, pal, narrow, below) {
  const isCat = cfg.xType === "category";
  const names = cfg.series.map(s => s.name).concat(cfg.line ? [cfg.line.name] : []);
  // Shares that sum to a fixed whole say so on the axis: cfg.yMax = 100 stops
  // ECharts padding the scale past the only value the stack can reach.
  const bars = cfg.series.length ? cfg.series[0].points.length : 0;
  // Past ~60 bars the inter-bar gaps read as a picket fence rather than a
  // composition; closing the gap turns the same data into a stacked area.
  const gap = bars > 60 ? "0%" : "20%";
  return {
    animation: false,
    color: pal.series,
    legend: Object.assign(
      {
        show: true,
        itemWidth: 16, itemHeight: 8, itemGap: narrow ? 8 : 14,
        textStyle: { color: pal.text, fontSize: narrow ? 11 : 12, padding: [0, 0, 0, 2] },
        data: names,
      },
      narrow ? { bottom: 0, left: 0 } : { top: 0, right: 0 }),
    grid: narrow
      // The axis name is drawn above the grid, so a chart that carries one
      // needs the room or its unit is clipped off the top of the panel.
      ? { left: 8, right: 12, top: cfg.yAxisName ? 26 : 12, bottom: Math.max(56, below || 0),
          containLabel: true }
      : { left: 8, right: 20, top: 34, bottom: 8, containLabel: true },
    xAxis: Object.assign(axisCommon(pal), { type: "time", splitLine: { show: false } }),
    yAxis: Object.assign(axisCommon(pal), {
      type: "value",
      name: cfg.yAxisName || "",
      max: cfg.yMax !== undefined ? cfg.yMax : null,
      nameTextStyle: { color: pal.muted, fontSize: 11, align: "left" },
      axisLine: { show: false },
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
    }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: pal.muted, width: 1 } },
      backgroundColor: pal.surface,
      borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: params => {
        const date = isCat ? params[0].name
          : fmtPeriod(new Date(params[0].value[0]).toISOString());
        const rows = params.slice()
          .sort((a, b) => ((isCat ? b.value : b.value[1]) ?? -Infinity) -
                          ((isCat ? a.value : a.value[1]) ?? -Infinity))
          .map(p => {
            const v = isCat ? p.value : p.value[1];
            // a share is a level, not a movement — it carries no sign
            const txt = v === null || v === undefined ? "—"
              : (cfg.unsigned ? fmtNum(v, 1) + (cfg.unit === "%" ? "%" : "")
                              : fmtSigned(v, 2, cfg.unit));
            return p.marker + " " + escapeHtml(p.seriesName) +
              ' <span class="num" style="float:right;margin-left:16px;font-weight:600">' + txt + "</span>";
          });
        return '<div style="font-weight:600;margin-bottom:2px">' + date + "</div>" + rows.join("<br>");
      },
    },
    series: cfg.series.map((s, i) => ({
      name: s.name,
      type: "bar",
      stack: "contrib",
      barCategoryGap: gap,
      itemStyle: { color: pal.series[(s.slot - 1) % 6] },
      emphasis: { focus: "none" },
      data: isCat ? s.points.map(p => p[1]) : s.points.map(p => [p[0], p[1]]),
      // The zero line, plus any event rules (cfg.eventLines: [{x: iso,
      // label}]) — a methodology break has to be visible on the chart, not
      // only in the footnote, and a stacked contribution chart is exactly
      // where a break in the underlying series misleads most.
      markLine: i === 0 ? {
        silent: true, symbol: "none", label: { show: false },
        lineStyle: { color: pal.muted, width: 1, type: "solid" },
        data: [{ yAxis: 0 }].concat((cfg.eventLines || []).map(e => ({
          xAxis: e.x,
          lineStyle: { color: pal.border, width: 1, type: "dashed" },
          label: { show: !!e.label, formatter: e.label, position: "end",
                   color: pal.muted, fontSize: 10 },
        }))),
      } : undefined,
    })).concat(cfg.line ? [{
      name: cfg.line.name,
      type: "line",
      // Annual series opt into a marker per reading (cfg.showPoints): with a
      // year between points there is nothing to interpolate, and a year
      // whose neighbours are both missing is otherwise drawn as nothing at
      // all — a published figure invisible on the chart.
      showSymbol: isCat || !!cfg.showPoints,
      symbolSize: 5,
      connectNulls: false,
      z: 10,
      lineStyle: { width: 2, color: pal.ink },
      itemStyle: { color: pal.ink },
      emphasis: { focus: "none" },
      data: cfg.line.points.map(p => [p[0], p[1]]),
    }] : []),
  };
}

/* cfg: { items: [{name, value, weight, note?}], unit: "%" | "pp",
          valueLabel?, trust, sourceLine }
   note is an optional pre-formatted secondary tooltip line. */
function barOptions(cfg, pal) {
  const items = cfg.items.slice().sort((a, b) => (a.value ?? -Infinity) - (b.value ?? -Infinity));
  const unit = cfg.unit || "%";
  const dp = unit === "pp" ? 2 : 1;
  return {
    animation: false,
    grid: { left: 8, right: 62, top: 8, bottom: 8, containLabel: true },
    xAxis: Object.assign(axisCommon(pal), {
      type: "value",
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
      axisLabel: { color: pal.muted, fontSize: 11,
                   formatter: v => unit === "%" ? v + "%" : v },
      axisLine: { show: false },
    }),
    yAxis: Object.assign(axisCommon(pal), {
      type: "category",
      data: items.map(i => i.name),
      axisLabel: { color: pal.text, fontSize: 12 },
    }),
    tooltip: {
      trigger: "item",
      backgroundColor: pal.surface,
      borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: p => {
        const it = items[p.dataIndex];
        const trust = TRUST_LABELS[cfg.trust] ? '<div style="margin-top:4px;font-size:11px;color:' +
          pal.muted + '">' + TRUST_LABELS[cfg.trust] + "</div>" : "";
        const note = it.note ? "<br>" + '<span style="color:' + pal.muted + '">' +
          escapeHtml(it.note) + "</span>" : "";
        return "<b>" + escapeHtml(it.name) + "</b><br>" +
          (cfg.valueLabel || MEASURE_SHORT.yoy) +
          ': <span class="num" style="font-weight:600">' + fmtSigned(it.value, dp, unit) + "</span>" +
          note + "<br>" +
          '<span style="color:' + pal.muted + '">Weight: ' + fmtNum(it.weight, 0) + " / 10,000</span>" +
          trust;
      },
    },
    series: [{
      type: "bar",
      barWidth: 16,
      data: items.map(i => ({
        value: i.value,
        itemStyle: { color: (i.value ?? 0) >= 0 ? pal.divergePos : pal.divergeNeg },
      })),
      label: {
        show: true,
        position: "right",
        color: pal.ink,
        fontSize: 11.5,
        fontWeight: 600,
        formatter: p => fmtSigned(items[p.dataIndex].value, dp, unit),
      },
      markLine: {
        silent: true, symbol: "none", label: { show: false },
        lineStyle: { color: pal.muted, width: 1, type: "solid" },
        data: [{ xAxis: 0 }],
      },
    }],
  };
}

/* cfg: { values: [n, ...], rows: [{sec_code, name, value}], unit, dp,
          metricLabel, stats: {p25, median, p75}, highlight: {name, value},
          trust, sourceLine, bins? }
   A cohort's distribution: how many peers fall in each band of one metric,
   with the quartiles marked and — where one is named — a rule on the company
   being read. This is the shape a rank cannot show. A company's value on its
   own says nothing; the same value against a cohort that is tightly bunched
   and against one that is spread out are two different findings, and only the
   distribution distinguishes them.

   Counts, so the y axis starts at zero and is never truncated. Bins are equal
   width over the observed range; a cohort whose values are all identical gets
   one bin rather than a divide-by-zero. */
function distOptions(cfg, pal, narrow) {
  const vals = (cfg.values || []).filter(v => v !== null && v !== undefined).sort((a, b) => a - b);
  const dp = cfg.dp === undefined ? 1 : cfg.dp;
  const unit = cfg.unit || "";
  const fmt = v => (v === null || v === undefined ? "—" : fmtNum(v, dp) + unit);
  // The caller may narrow the plotted window (see the percentile clip in
  // cohorts.js): one company with a −2,000% margin otherwise stretches the
  // axis until 220 of 222 peers stand in a single bar. Values outside the
  // window are counted in the end bars rather than dropped — nothing
  // disappears from a distribution — and the caller says so in the caption.
  const lo = cfg.min !== undefined && cfg.min !== null ? cfg.min : (vals.length ? vals[0] : 0);
  const hi = cfg.max !== undefined && cfg.max !== null ? cfg.max : (vals.length ? vals[vals.length - 1] : 0);
  // Square-root rule with a floor: Sturges gives six bars for a cohort of 30,
  // which collapsed a Core30 ROE spread of −4% to 58% into one block. Ten is
  // the fewest that shows a shape at all; thirty stops a cohort of 1,500
  // becoming a comb.
  const n = cfg.bins || Math.max(1, Math.min(30, Math.min(
    vals.length, Math.max(10, Math.ceil(Math.sqrt(vals.length))))));
  const width = hi > lo ? (hi - lo) / n : 1;
  const binOf = v => {
    let b = hi > lo ? Math.floor((v - lo) / width) : 0;
    if (b >= n) b = n - 1;
    return b < 0 ? 0 : b;
  };
  // Counts come from `values`, which is every member; the names in a tooltip
  // come from `rows`, which may be one page of a long table. Counting the page
  // instead would quietly draw a different distribution from the one the
  // quartiles describe.
  const counts = new Array(n).fill(0);
  const members = [];
  for (let i = 0; i < n; i++) members.push([]);
  vals.forEach(v => { counts[binOf(v)] += 1; });
  (cfg.rows || []).forEach(r => {
    if (r.value === null || r.value === undefined) return;
    const b = binOf(r.value);
    if (members[b].length < 6) members[b].push(r);
  });
  const hv = cfg.highlight && cfg.highlight.value;
  let hb = -1;
  if (hv !== null && hv !== undefined) {
    hb = hi > lo ? Math.floor((hv - lo) / width) : 0;
    if (hb >= n) hb = n - 1;
    if (hb < 0) hb = -1;
  }
  const edges = [];
  for (let i = 0; i <= n; i++) edges.push(lo + width * i);

  // The quartile rules carry no labels. Three of them plus the highlighted
  // company's sit within a few percent of each other on any bunched cohort,
  // and ECharts will not move a markLine label out of a collision — they
  // overprinted into an unreadable smear on the first Core30 render. The
  // numbers are on the tiles above the chart and in the caption below it;
  // only the company being read is labelled here, because only it is unique.
  const rules = [];
  const st = cfg.stats || {};
  [["p25", "dashed"], ["median", "solid"], ["p75", "dashed"]].forEach(([k, type]) => {
    if (st[k] === null || st[k] === undefined) return;
    rules.push({
      xAxis: st[k],
      lineStyle: { color: pal.muted, width: 1, type: type },
      label: { show: false },
    });
  });
  if (hv !== null && hv !== undefined) {
    rules.push({
      xAxis: hv,
      // ECharts defaults a markLine to dashed; the quartile rules are meant to
      // be, the company's rule is not.
      lineStyle: { color: pal.series[1], width: 2, type: "solid" },
      // rotate:0 is not the default — a markLine label takes the line's angle,
      // and on a vertical rule that prints the company name down the plot one
      // character wide.
      label: { show: true, color: pal.series[1], fontSize: 11, fontWeight: 600,
               position: "end", rotate: 0, distance: 4,
               formatter: (cfg.highlight.code || cfg.highlight.name || "") + " " + fmt(hv) },
    });
  }

  return {
    animation: false,
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: Object.assign(axisCommon(pal), {
      type: "value", min: lo, max: hi > lo ? hi : lo + 1,
      name: cfg.metricLabel ? cfg.metricLabel + (unit ? " (" + unit + ")" : "") : "",
      nameLocation: "middle", nameGap: 26,
      nameTextStyle: { color: pal.muted, fontSize: 11 },
      splitLine: { show: false },
      axisLabel: { color: pal.muted, fontSize: 11, formatter: v => fmtNum(v, dp) },
    }),
    // No y-axis name: ECharts anchors it at the top of the axis, exactly where
    // the highlighted company's label sits, and the two overprinted. What the
    // bars count belongs in the caption, which has room for a sentence.
    yAxis: Object.assign(axisCommon(pal), {
      type: "value", minInterval: 1,
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
      axisLine: { show: false },
    }),
    tooltip: {
      trigger: "item",
      backgroundColor: pal.surface, borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: p => {
        const i = p.dataIndex;
        const who = members[i].map(r => escapeHtml(r.name || r.sec_code)).join("<br>");
        const more = counts[i] > members[i].length
          ? '<br><span style="color:' + pal.muted + '">+' +
            (counts[i] - members[i].length) + " more</span>" : "";
        return "<b>" + fmt(edges[i]) + " to " + fmt(edges[i + 1]) + "</b><br>" +
          '<span class="num" style="font-weight:600">' + counts[i] +
          (counts[i] === 1 ? " company" : " companies") + "</span>" +
          (who ? '<div style="margin-top:4px;color:' + pal.muted + '">' + who + more + "</div>" : "");
      },
    },
    series: [{
      type: "bar", barCategoryGap: "8%",
      data: counts.map((c, i) => ({
        value: [edges[i] + width / 2, c],
        itemStyle: { color: i === hb ? pal.series[1] : pal.series[0] },
      })),
      barWidth: hi > lo ? undefined : 24,
      markLine: { silent: true, symbol: "none", data: rules },
    }],
  };
}

/* cfg: { categories: ["2025-07", ...], series: [{name, slot, points: [v|null]}],
          dp, unitSuffix, yAxisName, trust, sourceLine }
   Grouped vertical bars: two flows measured in the same unit over the same
   months, side by side rather than stacked — they are different acts and their
   sum means nothing. Zero baseline always; a bar axis is never truncated. */
function colsOptions(cfg, pal, narrow, below) {
  const dp = cfg.dp === undefined ? 1 : cfg.dp;
  const suffix = cfg.unitSuffix ? " " + cfg.unitSuffix : "";
  const fmt = v => (v === null || v === undefined ? "—" : fmtNum(v, dp) + suffix);
  return {
    animation: false,
    color: pal.series,
    legend: Object.assign(
      { itemWidth: 14, itemHeight: 8, itemGap: narrow ? 10 : 18,
        textStyle: { color: pal.text, fontSize: narrow ? 11 : 12, padding: [0, 0, 0, 2] },
        data: cfg.series.map(s => s.name) },
      narrow ? { bottom: 0, left: 0 } : { top: 0, right: 0 }),
    // cfg.gridRight: room for a long last category label ("FY Dec-2025"),
    // which is centred on the last bar and would otherwise run off the edge.
    grid: { left: 8, right: cfg.gridRight || 12, containLabel: true,
            top: narrow ? 26 : 34, bottom: narrow ? Math.max(44, below || 0) : 8 },
    xAxis: Object.assign(axisCommon(pal), {
      type: "category", data: cfg.categories,
      splitLine: { show: false },
      axisLabel: { color: pal.muted, fontSize: 11,
                   // a month label per bar is unreadable at phone width.
                   // cfg.labelInterval ("auto") opts a long run of periods
                   // into ECharts' own overlap thinning at every width.
                   interval: cfg.labelInterval !== undefined ? cfg.labelInterval
                     : (narrow ? 2 : 0), rotate: narrow ? 0 : 0 },
    }),
    yAxis: Object.assign(axisCommon(pal), {
      // Bars keep a zero baseline; a series that goes negative (credit costs,
      // a net flow) extends the axis below zero instead of vanishing.
      type: "value",
      min: cfg.series.some(s => s.points.some(v => v !== null && v < 0)) ? null : 0,
      name: cfg.yAxisName || "",
      nameTextStyle: { color: pal.muted, fontSize: 11, align: "left" },
      axisLine: { show: false },
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
    }),
    tooltip: {
      trigger: "axis",
      // The band is opaque, so it sits behind the bars (series z is 2);
      // at ECharts' default z of 50 it hides the column being read.
      axisPointer: { type: "shadow", z: 1, shadowStyle: { color: pal.subtle } },
      backgroundColor: pal.surface,
      borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: params => {
        const rows = params.map(p => p.marker + " " + escapeHtml(p.seriesName) +
          ' <span class="num" style="float:right;margin-left:16px;font-weight:600">' +
          fmt(p.value) + "</span>");
        const note = (cfg.notes && cfg.notes[params[0].dataIndex])
          ? '<div style="margin-top:4px;font-size:11px;color:' + pal.muted + '">' +
            escapeHtml(cfg.notes[params[0].dataIndex]) + "</div>" : "";
        const trust = TRUST_LABELS[cfg.trust] ? '<div style="margin-top:4px;font-size:11px;color:' +
          pal.muted + '">' + TRUST_LABELS[cfg.trust] + "</div>" : "";
        return '<div style="font-weight:600;margin-bottom:2px">' +
          escapeHtml(params[0].name) + "</div>" + rows.join("<br>") + note + trust;
      },
    },
    // A series with type "line" draws over the columns on the same axis (a
    // company's net income over its revenue: one unit, one scale); a gap
    // stays a gap.
    series: cfg.series.map(s => (s.type === "line" ? {
      name: s.name,
      type: "line",
      symbol: "circle",
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { width: 2, color: pal.series[(s.slot - 1) % 6] },
      itemStyle: { color: pal.series[(s.slot - 1) % 6] },
      emphasis: { focus: "none" },
      z: 3,
      data: s.points,
    } : {
      name: s.name,
      type: "bar",
      barMaxWidth: s.barMaxWidth || 22,
      itemStyle: { color: pal.series[(s.slot - 1) % 6] },
      emphasis: { focus: "none" },
      data: s.points,
    })),
  };
}

/* cfg: { items: [{name, value, sub, note}], unit, dp, valueLabel,
          rules?: [{value, text}], trust, sourceLine, rows? }
   A ranking: one value per named thing, laid out horizontally so the names
   are readable at their natural length and the eye runs down the order. Use
   it where a column chart would rotate its labels — 45 constituencies, 47
   prefectures — and where the ordering is itself the finding.

   Distinct from barOptions, which ranks signed contributions around a zero
   line and carries a basket weight. Here the values are levels: the axis
   starts at zero and is never truncated, one colour carries every bar
   because the bars are the same kind of thing, and comparison against a
   threshold is done with a labelled rule rather than by colouring bars,
   so nothing is encoded in colour alone. Items arrive in the order the
   caller wants them read; nothing is re-sorted here. */
/* Widest rendered width of a set of strings, in the page's own UI font.
   Chart layout that guesses at text width gets it wrong in one direction or
   the other; the canvas knows. Falls back to a character estimate where no
   2d context is available. */
function measureTextWidth(strings, fontSize) {
  let ctx = measureTextWidth._ctx;
  if (ctx === undefined) {
    try {
      ctx = document.createElement("canvas").getContext("2d");
    } catch (e) {
      ctx = null;
    }
    measureTextWidth._ctx = ctx;
  }
  if (!ctx) {
    return strings.reduce((m, s) => Math.max(m, s.length), 0) * fontSize * 0.56;
  }
  ctx.font = fontSize + "px " + getComputedStyle(document.body).fontFamily;
  return strings.reduce((m, s) => Math.max(m, ctx.measureText(s).width), 0);
}

function rankOptions(cfg, pal, narrow) {
  const items = cfg.items;
  const dp = cfg.dp === undefined ? 0 : cfg.dp;
  const suffix = cfg.unit ? " " + cfg.unit : "";
  // A list with a value below zero (monthly changes, falls from a high) is
  // diverging: the axis runs through zero, falls are drawn to its left in the
  // cool colour and rises to its right in the warm one, each label carries
  // its sign, and a zero rule marks the baseline. A list with no negative
  // value is drawn exactly as before (axis from zero, one series colour).
  const vals = items.map(i => i.value).filter(v => v !== null && v !== undefined && !Number.isNaN(v));
  const hasNeg = vals.some(v => v < 0);
  const fmt = v => (v === null || v === undefined ? MISSING
    : hasNeg ? fmtSigned(v, dp, "") + suffix : fmtNum(v, dp) + suffix);
  // Room left of the longest fall for its label, so the label never prints
  // over the names; the label lane on the right is already reserved by grid.right.
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const axisMin = hasNeg && !narrow ? lo - (hi - lo) * 0.16 : (hasNeg ? lo : 0);
  // The label lane is measured, not left to ECharts' containLabel, which
  // under-measured it: a 97px name got a 65px lane and was cut off at its
  // left edge — silently, no ellipsis, so "Tottori & Shimane" read
  // "ri & Shimane" and the chart looked fine. Measuring the real string in
  // the real font and setting every inset explicitly is deterministic.
  const fontSize = narrow ? 10.5 : 11.5;
  const labelMargin = 8;
  const cap = narrow ? 104 : 168;
  const labelWidth = Math.min(cap, measureTextWidth(
    items.map(i => String(i.name)), fontSize) + 2);
  // ECharts rotates a markLine label to run along the line by default, which
  // on a vertical rule prints the text sideways down the plot. rotate: 0
  // keeps it horizontal above the line; `stagger` drops one label a line so
  // two rules close together do not print over each other.
  const rules = (cfg.rules || []).map(r => ({
    xAxis: r.value,
    label: {
      show: true, position: "end", rotate: 0, distance: 6,
      offset: r.stagger ? [0, -13] : [0, 0],
      color: pal.muted, fontSize: 10.5, formatter: r.text,
    },
    lineStyle: { color: pal.muted, width: 1, type: "dashed" },
  }));
  return {
    animation: false,
    // containLabel is off on purpose (see labelWidth above): every inset is
    // set here. Bottom carries the tick labels plus the axis name when one
    // is given, so the name is never printed over the ticks.
    grid: {
      left: labelWidth + labelMargin + 2,
      right: narrow ? 54 : 76,
      // Reference-line labels sit above the plot; without room for them they
      // would print over the first bar.
      top: (cfg.rules && cfg.rules.length) ? 30 : 8,
      bottom: cfg.xAxisName ? 46 : 24,
      containLabel: false,
    },
    xAxis: Object.assign(axisCommon(pal), {
      type: "value",
      min: hasNeg ? axisMin : 0,
      max: hasNeg && hi === 0 ? 0 : undefined,
      splitLine: { show: true, lineStyle: { color: pal.grid, width: 1 } },
      axisLine: { show: false },
      axisLabel: { color: pal.muted, fontSize: 11, showMinLabel: !hasNeg,
                   formatter: v => fmtNum(v, dp) },
      name: cfg.xAxisName || "",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: pal.muted, fontSize: 11 },
    }),
    yAxis: Object.assign(axisCommon(pal), {
      type: "category",
      // ECharts draws a category y-axis bottom-up, so the caller's first
      // item has to be sent last to appear at the top.
      data: items.map(i => i.name).slice().reverse(),
      // The label lane is reserved explicitly rather than left to
      // containLabel to work out. Leaving it implicit cut the two longest
      // names off at their left edge — silently, with no ellipsis, so the
      // chart looked fine and read "…ri & Shimane". With an explicit width
      // a name that still does not fit is truncated at its *end* with an
      // ellipsis, which is visible as truncation.
      axisLabel: {
        color: pal.text, fontSize: fontSize, margin: labelMargin,
        // A name longer than the cap is truncated at its end with an
        // ellipsis — visible as truncation, unlike a canvas clip.
        width: labelWidth, overflow: "truncate", ellipsis: "…",
      },
      axisLine: { show: false },
    }),
    tooltip: {
      trigger: "item",
      backgroundColor: pal.surface,
      borderColor: pal.border,
      textStyle: { color: pal.text, fontSize: 12 },
      formatter: p => {
        const it = items[items.length - 1 - p.dataIndex];
        const trust = TRUST_LABELS[cfg.trust]
          ? '<div style="margin-top:4px;font-size:11px;color:' + pal.muted + '">' +
            TRUST_LABELS[cfg.trust] + "</div>" : "";
        const sub = it.sub
          ? '<br><span style="color:' + pal.muted + '">' + escapeHtml(it.sub) + "</span>" : "";
        const note = it.note
          ? '<div style="margin-top:4px;font-size:11px;color:' + pal.muted + '">' +
            escapeHtml(it.note) + "</div>" : "";
        return "<b>" + escapeHtml(it.name) + "</b><br>" +
          escapeHtml(cfg.valueLabel || "Value") +
          ': <span class="num" style="font-weight:600">' + fmt(it.value) + "</span>" +
          sub + note + trust;
      },
    },
    series: [{
      type: "bar",
      barMaxWidth: 14,
      itemStyle: { color: pal.series[0] },
      emphasis: { focus: "none" },
      data: hasNeg
        ? items.map(i => ({
            value: i.value,
            itemStyle: { color: (i.value ?? 0) < 0 ? pal.divergeNeg : pal.divergePos },
            label: { position: (i.value ?? 0) < 0 ? "left" : "right" },
          })).slice().reverse()
        : items.map(i => i.value).slice().reverse(),
      label: {
        show: !narrow,
        position: "right",
        color: pal.ink,
        fontSize: 11,
        fontWeight: 600,
        formatter: p => fmt(p.value),
      },
      markLine: (rules.length || hasNeg)
        ? { silent: true, symbol: "none",
            data: rules.concat(hasNeg ? [{ xAxis: 0, label: { show: false },
                                           lineStyle: { color: pal.muted, width: 1, type: "solid" } }] : []) }
        : undefined,
    }],
  };
}

/* mount a chart; returns {render, exportPNG, exportCSV, dispose} */
function obsChart(el, kind, cfg) {
  let chart = null;
  const layoutNarrow = width => {
    if (width < (cfg.legendFloor || 520)) return true;
    const lg = legendNamesAndValues(kind, cfg);
    return !!lg && !legendFitsTop(lg.names, width, cfg, lg.values, lg.itemWidth, lg.itemGap);
  };
  let drawnNarrow = null;
  const optionsFor = (pal, widthPx) => {
    // Below this width the legend moves under the plot, where it can wrap.
    // 520 suits two or three short series names; a chart carrying six long
    // ones (partner countries, commodity groups) sets cfg.legendFloor higher,
    // because a legend wider than the plot prints over the y-axis name
    // instead of wrapping. Opt-in, so no existing chart moves.
    // On top of that floor, a legend that is measured and found too long for
    // one row beside the y-axis name goes underneath at any width, with the
    // plot raised by its wrapped height. A chart whose series names the page
    // does not know in advance (a research desk chart) needs no floor.
    const width = widthPx || el.clientWidth;
    const narrow = layoutNarrow(width);
    const lg = legendNamesAndValues(kind, cfg);
    const below = narrow && lg && width > 0 ? legendHeightBelow(lg.names, width, 11, lg.itemWidth,
                                                   kind === "stack" ? 8 : 10) + 6 : 0;
    if (kind === "line") return lineOptions(cfg, pal, narrow, below);
    if (kind === "stack") return stackOptions(cfg, pal, narrow, below);
    if (kind === "cols") return colsOptions(cfg, pal, narrow, below);
    if (kind === "dist") return distOptions(cfg, pal, narrow);
    if (kind === "rank") return rankOptions(cfg, pal, narrow);
    return barOptions(cfg, pal);
  };

  function render(newCfg) {
    if (newCfg) cfg = newCfg;
    if (chart) chart.dispose();
    chart = echarts.init(el, null, { renderer: "canvas" });
    chart.setOption(optionsFor(readPalette()));
    drawnNarrow = layoutNarrow(el.clientWidth);
  }

  /* Draw the chart offscreen at an export preset. Light theme by default:
     exports end up in decks and documents, which are white. */
  function renderPNG(size) {
    const pal = paletteFor("light");
    const off = document.createElement("div");
    off.style.cssText = "position:fixed;left:-99999px;width:" + size.w +
      "px;height:" + size.h + "px";
    document.body.appendChild(off);
    const tmp = echarts.init(off, null, { renderer: "canvas" });
    const opts = optionsFor(pal, size.w);
    opts.graphic = [{
      type: "text", left: 10, bottom: 6,
      style: { text: cfg.sourceLine || "", fontSize: 11, fill: pal.muted },
    }];
    // The source line takes the bottom strip, so a legend drawn under the
    // plot moves up above it and the plot moves up with it.
    if (opts.legend && opts.legend.bottom !== undefined && opts.legend.show !== false) {
      opts.legend.bottom = 24;
      opts.grid.bottom += 24;
    } else {
      opts.grid.bottom = 30;
    }
    tmp.setOption(opts);
    const url = tmp.getDataURL({
      pixelRatio: size.out / size.w,
      backgroundColor: pal.surface,
    });
    tmp.dispose();
    off.remove();
    return url;
  }

  /* Pages still call this from their own click handler, and still own the
     filename; what changed is that it opens the size/copy menu instead of
     downloading one fixed file. */
  function exportPNG(filename) {
    obsExportMenu(filename, renderPNG, exportButton());
  }

  /* The control that opens the menu, so the menu can anchor to it and so the
     label can stop promising a straight download.

     Found by climbing from the chart to the first ancestor that contains an
     export control, and taken only when that ancestor contains exactly one:
     one control in scope belongs to this chart, two means the climb has
     reached a section holding several charts and guessing would relabel the
     wrong one. That is what makes this safe to infer rather than thread
     through fifty call sites — and it copes with the pages whose controls row
     is a sibling of the chart rather than its parent. */
  function exportButton() {
    let node = el.parentElement;
    let btn = null;
    while (node && node !== document.body && !btn) {
      const hits = node.querySelectorAll('[id$="-png"]');
      if (hits.length > 1) break;
      if (hits.length === 1) btn = hits[0];
      node = node.parentElement;
    }
    if (!btn) return null;
    if (btn.dataset.exportMenu !== "1") {
      if (/^\s*download png\s*$/i.test(btn.textContent || "")) {
        btn.textContent = "Export image \u25be";
      }
      btn.dataset.exportMenu = "1";
      btn.setAttribute("aria-haspopup", "menu");
      btn.setAttribute("aria-expanded", "false");
    }
    return btn;
  }

  function exportCSV(filename, headerLines) {
    let csv = (headerLines || []).map(l => "# " + l).join("\n") + "\n";
    if (kind === "cols") {
      csv += "period," + cfg.series.map(c => '"' + c.name.replace(/"/g, '""') + '"').join(",") + "\n";
      cfg.categories.forEach((cat, i) => {
        csv += cat + "," + cfg.series.map(s => {
          const v = s.points[i];
          return v === null || v === undefined ? "" : v;
        }).join(",") + "\n";
      });
    } else if (kind === "line" || kind === "stack") {
      const cols = kind === "stack" && cfg.line ? cfg.series.concat([cfg.line]) : cfg.series;
      const periods = new Set();
      cols.forEach(s => s.points.forEach(p => periods.add(p[0])));
      csv += "period," + cols.map(c => '"' + c.name.replace(/"/g, '""') + '"').join(",") + "\n";
      Array.from(periods).sort().forEach(period => {
        const row = cols.map(s => {
          const hit = s.points.find(p => p[0] === period);
          return hit && hit[1] !== null ? hit[1] : "";
        });
        // daily series keep the full date; monthly series keep YYYY-MM
        const label = cfg.hourlyPeriods ? period : cfg.isoPeriods ? period.slice(0, 10) : fmtPeriod(period);
        csv += label + "," + row.join(",") + "\n";
      });
    } else if (kind === "rank") {
      // The ranking's own columns, in the order shown. cfg.columns names
      // them so an export is readable without the page beside it.
      const cols = cfg.columns || [{ key: "name", label: "name" },
                                   { key: "value", label: "value" }];
      csv += cols.map(c => '"' + c.label.replace(/"/g, '""') + '"').join(",") + "\n";
      (cfg.rows || cfg.items).forEach(r => {
        csv += cols.map(c => {
          const v = r[c.key];
          if (v === null || v === undefined) return "";
          return typeof v === "number" ? v : '"' + String(v).replace(/"/g, '""') + '"';
        }).join(",") + "\n";
      });
    } else if (kind === "dist") {
      // The members, not the bars: a histogram's bins are a rendering choice,
      // and an export that carried them could not be checked against anything.
      csv += "sec_code,name,value\n";
      (cfg.rows || []).forEach(r => {
        csv += '"' + String(r.sec_code || "").replace(/"/g, '""') + '","' +
          String(r.name || "").replace(/"/g, '""') + '",' +
          (r.value === null || r.value === undefined ? "" : r.value) + "\n";
      });
    } else {
      csv += "group,value,weight_per_10000\n";
      cfg.items.forEach(i => {
        csv += '"' + i.name.replace(/"/g, '""') + '",' + (i.value ?? "") + "," + (i.weight ?? "") + "\n";
      });
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // A resize that changes where the legend belongs redraws the options; any
  // other just resizes. A chart whose legend a caller has hidden (thumbnails,
  // the desk's chart picker) keeps what the caller made of it.
  window.addEventListener("resize", () => {
    if (!chart) return;
    const now = layoutNarrow(el.clientWidth);
    const lg = chart.getOption().legend;
    const hidden = lg && lg[0] && lg[0].show === false;
    if (now !== drawnNarrow && !hidden && el.clientWidth > 0) {
      chart.setOption(optionsFor(readPalette()), { notMerge: true });
      drawnNarrow = now;
    }
    chart.resize();
  });
  render();
  exportButton();
  const handle = { render, exportPNG, exportCSV, dispose: () => chart && chart.dispose() };
  /* Every chart a page draws, with the config it is drawn from now. The
     research desk opens a pasted Plover page out of sight and reads this to
     copy a chart — its data included — into an article. Read-only use; the
     page itself never looks at it. */
  (window.__obsCharts = window.__obsCharts || []).push({
    el, kind, get cfg() { return cfg; },
  });
  return handle;
}

/* tiny inline-SVG sparkline; gaps in the data stay gaps */
function sparkSVG(points, w, h) {
  w = w || 110; h = h || 26;
  const vals = points.map(p => p[1]).filter(v => v !== null && v !== undefined);
  if (vals.length < 2) return "";
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = max - min || 1;
  const n = points.length;
  const segs = [];
  let cur = [];
  points.forEach((p, i) => {
    if (p[1] === null || p[1] === undefined) {
      if (cur.length > 1) segs.push(cur);
      cur = [];
      return;
    }
    const x = (i / (n - 1)) * (w - 2) + 1;
    const y = h - 2 - ((p[1] - min) / span) * (h - 4);
    cur.push(x.toFixed(1) + "," + y.toFixed(1));
  });
  if (cur.length > 1) segs.push(cur);
  const lines = segs.map(s => '<polyline points="' + s.join(" ") + '"/>').join("");
  return '<svg class="spark" width="' + w + '" height="' + h +
    '" viewBox="0 0 ' + w + " " + h + '" aria-hidden="true">' + lines + "</svg>";
}
