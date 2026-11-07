/* Item Explorer. The question this screen answers:
   "How is the price of <this item> moving?" — search, scan, click for detail.
   All control state (q, sort, selected series) lives in the URL. */
"use strict";

const DEFAULT_DATASET = "cpi-jp";
// per-dataset copy; everything else is driven by the API payload
const DATASET_UI = {
  "cpi-jp": {
    sub: "The published category aggregates of the national CPI (middle-class indices), " +
         "searchable in English and Japanese. Click a row for its full history and provenance.",
    placeholder: "Search items. E.g. Electricity, 電気代, 0001",
    csvName: "japan-cpi-categories.csv",
  },
  "cpi-jp-items": {
    sub: "All national CPI series at full item depth — individual goods and services, " +
         "searchable in English and Japanese. Click a row for its full history and provenance.",
    placeholder: "Search items. E.g. Rice, 米類, 1001",
    csvName: "japan-cpi-detailed-items.csv",
  },
};

function currentDataset() { return urlState().dataset; }
function apiBase() { return "/api/v1/" + currentDataset(); }

let ALL = null;        // /series payload
let detailChart = null;
let detailMeta = null;   // row metadata for the open series
let detailData = null;   // its /observations payload, full history
let detailMonths = [];   // every month that payload covers, "YYYY-MM", ascending

/* Columns are grouped into bands by what they mean, and the optional bands are
   switchable so the row never gets too wide to read. Series / Weight / the
   latest month are always on; the rest are in the URL like every other control. */
const BANDS = ["level", "contrib", "trend"];

function urlState() {
  const p = new URLSearchParams(location.search);
  const ds = p.get("dataset");
  return {
    dataset: ds && DATASET_UI[ds] ? ds : DEFAULT_DATASET,
    q: p.get("q") || "",
    sort: p.get("sort") || "weight",
    dir: p.get("dir") || "desc",
    series: p.get("series") || "",
    measure: p.get("measure") || "yoy",
    from: p.get("from") || "",
    to: p.get("to") || "",
    hide: (p.get("hide") || "").split(",").filter(b => BANDS.indexOf(b) !== -1),
  };
}

function bandOn(band) { return urlState().hide.indexOf(band) === -1; }

/* "2026-06-01" minus n months -> "2026-05" */
function monthsBack(iso, n) {
  let y = Number(iso.slice(0, 4));
  let m = Number(iso.slice(5, 7)) - n;
  while (m <= 0) { y -= 1; m += 12; }
  return y + "-" + (m < 10 ? "0" : "") + m;
}

/* `cls` styles the column, `band` marks the first column of a band (it carries
   the dividing rule), `lane` reserves the footnote-marker lane. */
function columns() {
  const latest = ALL.release.latest_period;
  const cols = [
    { key: "code", label: "Code", cls: "col-code" },
    { key: "name_en", label: "Item", cls: "col-item" },
    { key: "weight", label: "/ 10,000", num: true, cls: "col-weight band-start-soft" },
    { key: "index", label: "Index", num: true, cls: "col-index band-start" },
  ];
  if (bandOn("level")) {
    cols.push({ key: "prev_index", label: "Prev · " + monthsBack(latest, 1).slice(5),
                num: true, cls: "col-prev" });
  }
  cols.push({ key: "mom", label: "MoM %", num: true, lane: true, cls: "col-mom" });
  if (bandOn("contrib")) {
    cols.push({ key: "yoy", label: "YoY %", num: true, lane: true, cls: "col-yoy band-start" });
    cols.push({ key: "yoy_prior", label: "YoY, " + monthsBack(latest, 12).slice(0, 4),
                num: true, cls: "col-yoyprior" });
    cols.push({ key: "contrib_pp", label: "Contrib. pp", num: true, cls: "col-contrib" });
  }
  if (bandOn("trend")) {
    cols.push({ key: "ann3m", label: "3M ann. %", num: true, cls: "col-ann band-start" });
    cols.push({ key: "spark", label: "5Y index trend", cls: "col-spark", nosort: true });
  }
  return cols;
}

/* The upper header row: what each group of columns is about. Spans have to
   track `columns()` exactly or the two rows come apart. */
function bandRow() {
  const bands = [
    { label: "Series", span: 2 },
    { label: "Weight", span: 1, num: true, cls: "band-start-soft" },
    { label: "Latest month · " + fmtPeriod(ALL.release.latest_period),
      span: bandOn("level") ? 3 : 2, cls: "band-start band-accent" },
  ];
  if (bandOn("contrib")) bands.push({ label: "Year on year", span: 3, cls: "band-start" });
  if (bandOn("trend")) bands.push({ label: "Momentum", span: 2, cls: "band-start band-accent" });
  return '<tr class="band-row">' + bands.map(b =>
    '<th scope="colgroup" colspan="' + b.span + '" class="' +
    ((b.num ? "num " : "") + (b.cls || "")).trim() + '">' + b.label + "</th>").join("") +
    "</tr>";
}

function setUrlState(next) {
  const cur = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (cur.dataset !== DEFAULT_DATASET) p.set("dataset", cur.dataset);
  if (cur.q) p.set("q", cur.q);
  if (cur.sort !== "weight" || cur.dir !== "desc") { p.set("sort", cur.sort); p.set("dir", cur.dir); }
  if (cur.series) p.set("series", cur.series);
  if (cur.measure !== "yoy") p.set("measure", cur.measure);
  if (cur.hide && cur.hide.length) p.set("hide", cur.hide.join(","));
  // an explicit window is part of what a permalink has to reproduce
  if (cur.from) p.set("from", cur.from);
  if (cur.to) p.set("to", cur.to);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

function filtered() {
  const q = urlState().q.trim().toLowerCase();
  let rows = ALL.series;
  if (q) {
    rows = rows.filter(s =>
      s.name_en.toLowerCase().includes(q) ||
      (s.name_ja || "").toLowerCase().includes(q) ||
      s.code === q);
  }
  const { sort, dir } = urlState();
  const mul = dir === "asc" ? 1 : -1;
  rows = rows.slice().sort((a, b) => {
    const av = a[sort], bv = b[sort];
    // missing values sort last in BOTH directions
    const aMiss = av === null || av === undefined;
    const bMiss = bv === null || bv === undefined;
    if (aMiss && bMiss) return 0;
    if (aMiss) return 1;
    if (bMiss) return -1;
    if (typeof av === "string") return mul * av.localeCompare(bv);
    return mul * (av - bv);
  });
  return rows;
}

/* One renderer per column. Each returns the cell's inner HTML and any extra
   class the value itself earns; the wrapper adds the column's own classes so a
   band can be switched off without touching any of this. */
const CELL = {
  code: function (s) { return { html: s.code }; },
  name_en: function (s) {
    // a series that stops before the latest month says so where its name is —
    // the row's own as-of, since the band header speaks for every other row
    const ends = s.discontinued
      ? ' <span class="badge badge-stale" title="No value for the latest reference month">Ends ' +
        fmtPeriod(s.as_of) + "</span>"
      : "";
    return { html: '<div class="cell-item"><div class="en">' + escapeHtml(s.name_en) + ends +
      "</div></div>" };
  },
  weight: function (s) { return { html: fmtNum(s.weight, 0) }; },
  index: function (s) { return { html: fmtIndex(s.index) }; },
  prev_index: function (s) { return { html: fmtIndex(s.prev_index) }; },
  mom: function (s) { return { html: fmtSigned(s.mom, 1) + noteLane(s.notes, "mom") }; },
  yoy: function (s) { return { html: fmtSigned(s.yoy, 1) + noteLane(s.notes, "yoy") }; },
  yoy_prior: function (s) { return { html: fmtSigned(s.yoy_prior, 1) }; },
  contrib_pp: function (s, maxContrib) {
    if (s.contrib_pp === null || s.contrib_pp === undefined) {
      return { html: '<span class="contrib-val num">' + MISSING + "</span>" };
    }
    // bar length is magnitude only; the sign is printed beside it
    const w = maxContrib > 0 ? Math.round(Math.abs(s.contrib_pp) / maxContrib * 100) : 0;
    return { html: '<span class="contrib-cell"><span class="contrib-track">' +
      '<i style="width:' + w + '%"></i></span>' +
      '<span class="contrib-val num">' + fmtSigned(s.contrib_pp, 2) + "</span></span>" };
  },
  ann3m: function (s) {
    const a = s.ann3m, y = s.yoy;
    let cls = "", tip = "";
    if (a !== null && a !== undefined && y !== null && y !== undefined) {
      if (a > y) {
        cls = "trend-accel";
        tip = "The last three months are running faster than the 12-month rate";
      } else if (a < y - 0.3) {
        cls = "trend-decel";
        tip = "The last three months are running slower than the 12-month rate";
      }
    }
    const v = fmtSigned(a, 1);
    return { html: tip ? '<span title="' + tip + '">' + v + "</span>" : v, cls: cls };
  },
  spark: function (s) { return { html: sparkSVG(s.spark, 112, 26) }; },
};

function renderTable() {
  const cols = columns();
  // switching a band off can hide the column the table is sorted by, which
  // would leave the rows in an order with nothing on screen to explain it
  if (!cols.some(c => c.key === urlState().sort)) {
    setUrlState({ sort: "weight", dir: "desc" });
  }
  const rows = filtered();
  const { sort, dir, q } = urlState();

  document.getElementById("row-count").textContent =
    fmtNum(rows.length, 0) + " of " + fmtNum(ALL.series.length, 0);
  document.getElementById("filter-note").textContent =
    q ? "· filtered by “" + q + "”" : "";

  const head = cols.map(c => {
    // has-lane keeps the header flush with digits that sit left of a marker lane
    const cls = (c.num ? "num " : "") + (c.lane ? "has-lane " : "") +
      (c.nosort ? "" : "sortable ") + c.cls;
    const arrow = c.key === sort ? '<span class="arrow">' + (dir === "asc" ? "▲" : "▼") + "</span>" : "";
    return '<th scope="col" class="' + cls + '" data-key="' + c.key + '"' +
      (c.key === sort ? ' aria-sort="' + (dir === "asc" ? "ascending" : "descending") + '"' : "") +
      ">" + c.label + arrow + "</th>";
  }).join("");

  // one scale for the contribution bars, so bar length is comparable down the column
  const maxContrib = rows.reduce((m, s) =>
    s.contrib_pp === null || s.contrib_pp === undefined ? m : Math.max(m, Math.abs(s.contrib_pp)), 0);

  const body = rows.map(s =>
    '<tr class="clickable" data-code="' + s.code + '" tabindex="0" ' +
    'aria-label="Open detail for ' + escapeHtml(s.name_en) + '">' +
    cols.map(c => {
      const cell = CELL[c.key](s, maxContrib);
      const cls = [c.cls, c.num ? "num" : "", cell.cls || ""].filter(Boolean).join(" ");
      return '<td class="' + cls + '">' + cell.html + "</td>";
    }).join("") + "</tr>").join("");

  document.getElementById("table-wrap").innerHTML =
    rows.length === 0
      ? '<div class="state-empty">No items match “' + escapeHtml(q) +
        "”. Try the Japanese name (電気代), the English name (electricity), or clear the search.</div>"
      : '<table class="data tbl-bands"><thead>' + bandRow() + '<tr class="col-row">' + head +
        "</tr></thead><tbody>" + body + "</tbody></table>";

  renderTableFoot(rows);
  renderTableNotes(rows);

  document.querySelectorAll("th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      const cur = urlState();
      const nextDir = cur.sort === key && cur.dir === "desc" ? "asc" : "desc";
      setUrlState({ sort: key, dir: nextDir });
      renderTable();
    });
  });

  document.querySelectorAll("tr.clickable").forEach(tr => {
    const open = () => { setUrlState({ series: tr.dataset.code }); renderDetail(); };
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", e => { if (e.key === "Enter") open(); });
  });
}

/* The caption under the table explains the two columns whose meaning is not in
   their header. It tracks the visible bands: a formula for a column that is
   switched off is noise. */
function renderTableFoot(rows) {
  const parts = [];
  // nothing to annotate under an empty result
  if (!rows.length) { document.getElementById("table-foot").innerHTML = ""; return; }
  if (bandOn("contrib")) {
    parts.push("Contribution is this series' share of headline year-on-year inflation, in " +
      "percentage points: weight × (index[t] − index[t−12]) ÷ (10,000 × headline index[t−12]) " +
      "× 100. Rows in this table overlap — an aggregate contains its own components — so the " +
      "column does not sum to headline; the group decomposition that does is on the Overview.");
  }
  if (bandOn("trend")) {
    parts.push("3-month annualised uses the last three published index values.");
  }
  document.getElementById("table-foot").innerHTML = parts.length
    ? parts.join(" ") + ' <a href="methodology.html#calc">How these are computed</a>'
    : "";
}

/* Footnotes under the table, listing only the flags the visible rows actually
   carry — an explanation for a marker nobody can see is noise. A flag whose
   only column is in a switched-off band is invisible too, so it drops out. */
function renderTableNotes(rows) {
  const slot = document.getElementById("table-notes");
  const shown = columns().map(c => c.key);
  const ids = notesPresent(rows).filter(id =>
    NOTE_DEFS[id].cols.some(col => shown.indexOf(col) !== -1));
  if (ids.length === 0) { slot.innerHTML = ""; return; }
  slot.innerHTML = ids.map(id => {
    const d = NOTE_DEFS[id];
    return '<p><sup class="note-mark">' + d.marker + "</sup> <b>" + d.label +
      "</b> — " + d.rule + "</p>";
  }).join("") +
    "<p class=\"note-calc\">Flags are calculated here from published index values; " +
    "they are not published by the agency. Hover a marker for that row's month and size. " +
    '<a href="methodology.html#flags">How these are computed</a></p>';
}

function sourceLine(rel, trust) {
  const label = TRUST_LABELS[trust];
  return "Source: Statistics Bureau of Japan · " + rel.source_id +
    " · " + (rel.base || "").replace("=", " = ") + " · Data through " + fmtPeriod(rel.latest_period) +
    " · Retrieved " + fmtStamp(rel.retrieved_at) +
    (label ? " · " + label : "");
}

/* ---- chart date range ----
   The window is a view on the full history the API already returned: rates of
   change are computed server-side over every month, so trimming the view never
   changes a value at the left edge the way refetching a shorter series would. */

const RANGE_PRESETS = [
  { label: "5Y", years: 5 },
  { label: "10Y", years: 10 },
  { label: "20Y", years: 20 },
];

/* "2026-06" minus n years, same month */
function monthMinusYears(month, years) {
  return String(Number(month.slice(0, 4)) - years) + month.slice(4);
}

/* first covered month at or after `month` — a preset that reaches back further
   than the series does starts where the series does */
function snapForward(month) {
  for (let i = 0; i < detailMonths.length; i++) {
    if (detailMonths[i] >= month) return detailMonths[i];
  }
  return detailMonths[detailMonths.length - 1];
}

/* the window actually shown: URL values clamped to this series' coverage, so a
   window carried over from a longer series can never point off the end */
function detailWindow() {
  const first = detailMonths[0];
  const last = detailMonths[detailMonths.length - 1];
  const st = urlState();
  const ok = m => /^\d{4}-\d{2}$/.test(m) && m >= first && m <= last;
  let from = ok(st.from) ? st.from : first;
  const to = ok(st.to) ? st.to : last;
  if (from > to) from = first;
  return { from: from, to: to, first: first, last: last,
           full: from === first && to === last };
}

function renderRangeRow() {
  const w = detailWindow();
  const shown = detailMonths.filter(m => m >= w.from && m <= w.to).length;

  // a preset that would resolve to the full history is the Max button already
  const buttons = RANGE_PRESETS
    .filter(p => monthMinusYears(w.last, p.years) > w.first)
    .map(p => {
      const start = snapForward(monthMinusYears(w.last, p.years));
      return '<button type="button" data-from="' + start + '" data-to="' + w.last +
        '" aria-pressed="' + (w.from === start && w.to === w.last) + '" ' +
        'title="' + start + " to " + w.last + '">' + p.label + "</button>";
    });
  buttons.push('<button type="button" data-from="" data-to="" aria-pressed="' + w.full +
    '" title="' + w.first + " to " + w.last + '">Max</button>');

  // bounding each end by the other makes an inverted window unselectable
  const options = (selected, lo, hi) => detailMonths
    .filter(m => m >= lo && m <= hi)
    .map(m => '<option value="' + m + '"' + (m === selected ? " selected" : "") + ">" + m + "</option>")
    .join("");

  document.getElementById("detail-range").innerHTML =
    '<span class="seg" role="group" aria-label="Date range" id="range-seg">' +
      buttons.join("") + "</span>" +
    '<label for="range-from">From</label>' +
    '<select id="range-from" class="num">' + options(w.from, w.first, w.to) + "</select>" +
    '<label for="range-to">To</label>' +
    '<select id="range-to" class="num">' + options(w.to, w.from, w.last) + "</select>" +
    '<span class="range-note num">' + fmtNum(shown, 0) + " of " +
      fmtNum(detailMonths.length, 0) + " months · series covers " + w.first + " to " + w.last +
    "</span>";

  document.querySelectorAll("#range-seg button").forEach(b => {
    b.addEventListener("click", () => {
      setUrlState({ from: b.dataset.from, to: b.dataset.to });
      applyDetailRange();
    });
  });
  ["from", "to"].forEach(end => {
    document.getElementById("range-" + end).addEventListener("change", e => {
      const next = {};
      next[end] = e.target.value;
      setUrlState(next);
      applyDetailRange();
    });
  });
}

/* redraw chart, source line and exports for the current window */
function applyDetailRange() {
  // a window carried over from a longer series may not survive the clamp; the
  // URL must not claim a window the chart isn't drawing
  const st = urlState();
  const clamped = detailWindow();
  if ((st.from && st.from !== clamped.from) || (st.to && st.to !== clamped.to)) {
    setUrlState(clamped.full ? { from: "", to: "" }
                             : { from: clamped.from, to: clamped.to });
  }

  renderRangeRow();

  const { measure } = urlState();
  const w = detailWindow();
  const points = detailData.series[0].points
    .filter(p => fmtPeriod(p[0]) >= w.from && fmtPeriod(p[0]) <= w.to);
  const windowLine = w.full ? "" : " · Shown: " + w.from + " to " + w.to;
  const src = sourceLine(ALL.release, detailData.trust) + windowLine;

  const el = document.getElementById("detail-chart");
  el.innerHTML = "";
  if (detailChart) detailChart.dispose();
  detailChart = obsChart(el, "line", {
    series: [{ name: detailMeta.name_en, slot: 1, points: points }],
    unit: detailData.unit,
    yAxisName: measure === "index" ? "Index (" + (ALL.release.base || "").replace("=", " = ") + ")" : "%",
    trust: detailData.trust,
    sourceLine: src,
  });
  document.getElementById("detail-source").textContent = src;

  const stem = "japan-cpi-" + detailMeta.code + "-" + measure +
    (w.full ? "" : "-" + w.from + "_" + w.to);
  document.getElementById("detail-png").onclick = () => detailChart.exportPNG(stem + ".png");
  document.getElementById("detail-csv").onclick = () =>
    detailChart.exportCSV(stem + ".csv", [
      "Japan CPI — " + detailMeta.name_en + " (" + (detailMeta.name_ja || "") + ") — " +
        MEASURE_LABELS[measure],
      "Range: " + w.from + " to " + w.to + " (" + points.length +
        " months); series covers " + w.first + " to " + w.last,
      TRUST_LABELS[detailData.trust] ? "Trust: " + TRUST_LABELS[detailData.trust]
        : "Trust: calculated from official index values (formula below)",
      "Calculation: " + detailData.calc,
      "Source: Statistics Bureau of Japan via e-Stat, " + ALL.release.source_id,
      "Release: " + ALL.release.label + " (" + (ALL.release.base || "").replace("=", " = ") + ")",
      "Retrieved: " + fmtStamp(ALL.release.retrieved_at),
      "Permalink: " + location.href,
    ]);
  // The rate is computed server-side over the full history; the window is a trim.
  const obsUrl = apiBase() + "/observations?series=" + detailMeta.code + "&measure=" + measure +
    (w.to !== w.last ? "&end=" + w.to : "");
  obsCodeButton(document.getElementById("detail-csv"), () => ({
    url: obsUrl, start: w.from !== w.first ? w.from : null,
    release: detailData.release, source: src,
  }));
}

async function renderDetail() {
  const { series: code, measure } = urlState();
  const slot = document.getElementById("detail-slot");
  if (!code) { slot.innerHTML = ""; return; }
  const meta = ALL.series.find(s => s.code === code);
  if (!meta) { slot.innerHTML = ""; return; }

  slot.innerHTML = '<div class="detail">' +
    '<div class="detail-head"><h2>' + escapeHtml(meta.name_en) + "</h2>" +
    '<span class="ja">' + escapeHtml(meta.name_ja || "") + "</span>" +
    '<span class="muted num" style="font-size:12px">Code ' + meta.code + "</span>" +
    '<button type="button" class="btn detail-close" id="detail-close">Close</button></div>' +
    '<p class="detail-meta num">Weight ' + fmtNum(meta.weight, 0) + " / 10,000 · Latest index " +
    fmtIndex(meta.index) + " (" + fmtPeriod(meta.as_of) + ") · YoY " +
    (meta.yoy === null ? MISSING : fmtSigned(meta.yoy, 1, "%")) + " · MoM " +
    (meta.mom === null ? MISSING : fmtSigned(meta.mom, 1, "%")) + "</p>" +
    (meta.notes || []).map(n => {
      const d = NOTE_DEFS[n.id];
      return d ? '<p class="detail-note"><sup class="note-mark">' + d.marker + "</sup> <b>" +
        d.label + "</b> — " + escapeHtml(d.text(n)) + "</p>" : "";
    }).join("") +
    '<div class="controls">' +
    '<label for="detail-measure">Measure</label>' +
    '<select id="detail-measure">' +
    '<option value="yoy">Year over Year (%)</option>' +
    '<option value="mom">Month over Month (%)</option>' +
    '<option value="ann3m">3-Month Annualized (%)</option>' +
    '<option value="index">Index Level (' + (ALL.release.base || "").replace("=", " = ") + ')</option>' +
    "</select>" +
    '<span class="spacer"></span>' +
    '<button type="button" class="btn" id="detail-png">Download PNG</button>' +
    '<button type="button" class="btn" id="detail-csv">Download CSV</button>' +
    "</div>" +
    '<div class="controls range-row" id="detail-range"></div>' +
    '<div class="chart" id="detail-chart"><div class="skeleton" style="width:100%;height:100%"></div></div>' +
    '<p class="source-line" id="detail-source"></p>' +
    '<details class="calc" id="detail-calc"></details>' +
    "</div>";

  document.getElementById("detail-close").addEventListener("click", () => {
    setUrlState({ series: "", from: "", to: "" });
    if (detailChart) { detailChart.dispose(); detailChart = null; }
    slot.innerHTML = "";
  });
  const sel = document.getElementById("detail-measure");
  sel.value = measure;
  sel.addEventListener("change", () => { setUrlState({ measure: sel.value }); renderDetail(); });

  let data;
  try {
    const r = await fetch(apiBase() + "/observations?series=" + code + "&measure=" + measure);
    if (!r.ok) throw new Error("observations " + r.status);
    data = await r.json();
  } catch (err) {
    document.getElementById("detail-chart").innerHTML =
      '<div class="state-error">This series failed to load. Retry, and check that the data service is running.' +
      "<details><summary>See details</summary><pre>" + escapeHtml(String(err)) + "</pre></details></div>";
    return;
  }

  detailMeta = meta;
  detailData = data;
  detailMonths = data.series[0].points.map(p => fmtPeriod(p[0]));
  if (detailMonths.length === 0) {
    document.getElementById("detail-chart").innerHTML =
      '<div class="state-empty">No observations are published for this series.</div>';
    document.getElementById("detail-source").textContent = sourceLine(ALL.release, data.trust);
    return;
  }
  applyDetailRange();

  const calcEl = document.getElementById("detail-calc");
  if (data.trust === "derived") {
    calcEl.innerHTML = "<summary>Show calculation</summary>" +
      '<div class="calc-body"><code>' + escapeHtml(data.calc) + "</code><br>" +
      "Inputs: official index values, release “" + escapeHtml(ALL.release.label) +
      "” (sha256 " + ALL.release.sha256.slice(0, 12) + "…).</div>";
  } else {
    calcEl.innerHTML = "";
  }
  // exports are wired in applyDetailRange — they carry the shown window
}

function exportFilteredCSV() {
  const rows = filtered();
  const rel = ALL.release;
  // every measure the table can show, whatever bands are on: a CSV has no
  // width limit, and a column missing from an export cannot be recovered
  const COLUMNS = "code,name_en,name_ja,weight_per_10000,index,prev_month_index," +
    "mom_pct,yoy_pct,yoy_pct_prior_year,contribution_pp,ann3m_pct,as_of,flags";
  const header = [
    "Japan CPI — Item Explorer export (filtered rows)",
    "Columns: " + COLUMNS + " (index and prev_month_index are official; every rate is calculated)",
    "Trust: index = Official Statistic; yoy/mom/ann3m, contribution and flags are calculated here from published index values",
    "Calculation — yoy: (index[t]/index[t-12] - 1) x 100",
    "Calculation — mom: (index[t]/index[t-1] - 1) x 100",
    "Calculation — ann3m: ((index[t]/index[t-3]) ^ 4 - 1) x 100",
    "Calculation — yoy_pct_prior_year: yoy at t-12, the base a year ago",
    "Calculation — contribution: " + (ALL.contrib_calc || ""),
    "Note: rows overlap (an aggregate contains its own components), so contribution_pp does not sum to headline YoY",
    "Flags: step = one month is at least 70% of the 12-month move and shifted the index at least 10%; low_base = index level below 5.0, so percent changes are unstable",
    "Source: Statistics Bureau of Japan via e-Stat, " + rel.source_id,
    "Release: " + rel.label + " (" + (rel.base || "").replace("=", " = ") + ")",
    "Retrieved: " + fmtStamp(rel.retrieved_at),
    "Permalink: " + location.href,
  ].map(l => "# " + l).join("\n");
  // blank, never 0: a missing value must not export as a number
  const n = (v, dp) => (v === null || v === undefined ? "" : v.toFixed(dp));
  let csv = header + "\n" + COLUMNS + "\n";
  rows.forEach(s => {
    const flags = (s.notes || [])
      .filter(n2 => NOTE_DEFS[n2.id])
      .map(n2 => n2.id + ": " + NOTE_DEFS[n2.id].text(n2))
      .join(" ");
    csv += [
      s.code,
      '"' + s.name_en.replace(/"/g, '""') + '"',
      '"' + (s.name_ja || "").replace(/"/g, '""') + '"',
      s.weight ?? "",
      s.index ?? "",
      s.prev_index ?? "",
      n(s.mom, 4),
      n(s.yoy, 4),
      n(s.yoy_prior, 4),
      n(s.contrib_pp, 6),
      n(s.ann3m, 4),
      fmtPeriod(s.as_of),
      '"' + flags.replace(/"/g, '""') + '"',
    ].join(",") + "\n";
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = DATASET_UI[currentDataset()].csvName;
  a.click();
  URL.revokeObjectURL(a.href);
}

function syncDatasetUI() {
  const ds = currentDataset();
  const ui = DATASET_UI[ds];
  document.getElementById("page-sub").textContent = ui.sub;
  document.getElementById("q").placeholder = ui.placeholder;
  document.querySelectorAll("#dataset-seg button").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.dataset === ds)));
}

function syncBandUI() {
  const hide = urlState().hide;
  document.querySelectorAll("#band-toggles input").forEach(cb => {
    cb.checked = hide.indexOf(cb.dataset.band) === -1;
  });
}

async function loadData() {
  syncDatasetUI();
  document.getElementById("table-wrap").innerHTML =
    '<div class="skeleton" style="height:400px"></div>';
  try {
    const r = await fetch(apiBase() + "/series");
    if (!r.ok) throw new Error("series " + r.status + " " + (await r.text()).slice(0, 300));
    ALL = await r.json();
  } catch (err) {
    document.getElementById("table-wrap").innerHTML =
      '<div class="state-error">The item list failed to load. The data service may not be running — ' +
      "start it and reload this page.<details><summary>See details</summary><pre>" +
      escapeHtml(String(err)) + "</pre></details></div>";
    return false;
  }
  document.getElementById("header-asof").textContent =
    "Data through " + fmtPeriod(ALL.release.latest_period);
  return true;
}

async function init() {
  initThemeToggle(() => { if (urlState().series) renderDetail(); });

  const q = document.getElementById("q");
  q.value = urlState().q;
  let t = null;
  q.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(() => { setUrlState({ q: q.value }); renderTable(); }, 150);
  });

  document.getElementById("export-csv").addEventListener("click", exportFilteredCSV);

  syncBandUI();
  document.querySelectorAll("#band-toggles input").forEach(cb => {
    cb.addEventListener("change", () => {
      const hide = BANDS.filter(b => !document.querySelector(
        '#band-toggles input[data-band="' + b + '"]').checked);
      setUrlState({ hide: hide });
      renderTable();
    });
  });

  document.querySelectorAll("#dataset-seg button").forEach(b => {
    b.addEventListener("click", async () => {
      if (b.dataset.dataset === currentDataset()) return;
      // series codes are table-specific; drop any open detail on switch
      setUrlState({ dataset: b.dataset.dataset, series: "" });
      if (detailChart) { detailChart.dispose(); detailChart = null; }
      document.getElementById("detail-slot").innerHTML = "";
      if (await loadData()) renderTable();
    });
  });

  if (!(await loadData())) return;
  renderTable();
  renderDetail();
}

init();
