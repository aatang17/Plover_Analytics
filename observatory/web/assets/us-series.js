/* Series pages — the US price and pay pages, Japan's labour page and the
   Hong Kong pages: one
   script for every dataset that is read as
   "find the series, see its latest value, chart a few of them". The question
   each page answers is "what is <this item> at now, and how has it moved?" —
   the stat strip leads, the chart follows, the full searchable table below.

   The page declares on <main>:
     data-datasets   "slug:Label,slug:Label" — the datasets this page switches
                     between (the first is the default); ?dataset= picks one
     data-default-q  a starting search for a dataset too large to list whole
     data-national   "1": offer the "U.S. city average only" filter, for a
                     dataset whose area series carry "Area — " in their name

   Everything else comes from the API: the overview tiles, the series listing
   (index datasets carry yoy/mom; level datasets latest and changes), the
   formula for every calculated number and the release it came from.
   Missing is "—"; a gap stays a gap. */
"use strict";

const MAIN = document.querySelector("main[data-datasets]");
const CHOICES = MAIN.getAttribute("data-datasets").split(",").map(x => {
  const i = x.indexOf(":");
  return { slug: x.slice(0, i), label: x.slice(i + 1) };
});
const DEFAULT_Q = MAIN.getAttribute("data-default-q") || "";
const NATIONAL = MAIN.getAttribute("data-national") === "1";
const MAX_SERIES = 6;

/* Display precision per dataset: one precision per column, as published —
   the BLS prints average prices and weights to three decimals, earnings to
   two, and the Atlanta Fed its medians to one. Index levels follow the
   platform rule of one decimal. */
const DP = { "us-avg-prices": 3, "us-wages": 2, "cpi-us-weights": 3, "us-wage-tracker": 1, "us-labour": 1,
             /* Hong Kong: the C&SD publishes its rates and its labour counts
                (in thousands) to one decimal. */
             "cpi-hk-rates": 1, "labour-hk": 1 };
/* Datasets whose series share a currency but not a quantity (a dozen eggs, a
   gallon of milk; an hour and a week of pay): their levels may be charted one
   at a time only, and several at once as rates of change. */
const SINGLE_LEVEL = { "us-avg-prices": true, "us-wages": true };
/* A dataset with no entry in DP mixes units (Japan's wage survey carries yen,
   hours, days, headcounts and indices), so precision follows each series'
   unit, as published: yen to the yen, headcounts whole, indices and hours to
   one decimal, job-openings ratios to two. */
const UNIT_DP = { "¥": 0, "10k persons": 0, "persons": 0, "index": 1, "hours": 1,
                  "days": 1, "%": 1, "ratio": 2, "HK$mn": 0, "'000 persons": 1 };
/* The few series published to two decimals inside a one-decimal unit: the
   wage survey's hire, separation and part-time rates. Keyed by the code's
   first segment. */
const CODE_DP = { "wages-jp": { part_time_share: 2, hiring_rate: 2, separation_rate: 2 } };

let OV = null;       // /overview
let LIST = null;     // /series
let OBS = null;      // /observations for the chart
let chart = null;
let chartSeq = 0;     // the newest request wins; a slow earlier one is dropped
let listSeq = 0;

function urlState() {
  const p = new URLSearchParams(location.search);
  const ds = p.get("dataset");
  const dataset = CHOICES.some(c => c.slug === ds) ? ds : CHOICES[0].slug;
  return {
    dataset: dataset,
    q: p.has("q") ? p.get("q") : DEFAULT_Q,
    series: (p.get("series") || "").split(",").filter(Boolean),
    measure: p.get("measure") || "",
    range: p.get("range") || "10",
    national: p.has("all") ? false : NATIONAL,
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.dataset !== CHOICES[0].slug) p.set("dataset", s.dataset);
  if (s.q !== DEFAULT_Q) p.set("q", s.q);
  if (s.series.length) p.set("series", s.series.join(","));
  if (s.measure) p.set("measure", s.measure);
  if (s.range !== "10") p.set("range", s.range);
  if (NATIONAL && !s.national) p.set("all", "1");
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

const api = path => "/api/v1/" + urlState().dataset + path;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status + " " + (await r.text()).slice(0, 200));
  return r.json();
}

/* ---- dataset shape ---- */

// An index dataset's listing carries yoy; a levels dataset's carries latest.
const isIndex = () => !!(LIST && LIST.series.length && "yoy" in LIST.series[0]);
const isRate = () => !!(LIST && LIST.series.length && LIST.series[0].kind === "rate");
const dp = () => (isIndex() ? 1 : (DP[urlState().dataset] ?? 2));

function dpFor(unit, code) {
  const ds = urlState().dataset;
  if (isIndex()) return 1;
  if (DP[ds] !== undefined) return DP[ds];
  const own = CODE_DP[ds];
  const head = (code || "").split(".")[0];
  if (own && own[head] !== undefined) return own[head];
  return UNIT_DP[unit] ?? 2;
}

const rowFor = code => (LIST ? LIST.series.find(s => s.code === code) : null);
const mixedUnits = () => !!LIST && new Set(LIST.series.map(s => s.unit)).size > 1;

/* A value with its unit: "$37.75", "¥434,507", "2.5%", "177 10k persons".
   An index or a ratio is a bare number; its label says what it is. */
function withUnit(v, unit, d) {
  const n = fmtNum(v, d);
  if (unit === "$" || unit === "1982-84 $") return "$" + n;
  if (unit === "¥") return "¥" + n;
  if (unit === "%") return n + '<span class="unit">%</span>';
  if (!unit || unit === "index" || unit === "ratio") return n;
  return n + '<span class="unit"> ' + escapeHtml(unit) + "</span>";
}

/* The charted series decide what can be calculated: a rate or a ratio is
   read in points, and the API refuses a percentage change of a flow, so
   neither is offered a percentage change here. */
function chartedRates() {
  const rows = chartCodes().map(rowFor).filter(Boolean);
  return rows.length ? rows.every(r => r.kind === "rate") : isRate();
}

function chartedNoPct() {
  const rows = chartCodes().map(rowFor).filter(Boolean);
  return rows.length ? rows.some(r => r.kind === "rate" || r.kind === "flow") : isRate();
}

function measures() {
  if (isIndex()) return ["yoy", "mom", "ann3m", "index"];
  if (chartedNoPct()) return ["index"];
  // A quarterly series has no month before it: a month-on-month change of
  // it would draw an empty chart. Its change is read on the year before.
  return isQuarterly() ? ["index", "yoy"] : ["index", "yoy", "mom"];
}

function measureLabel(m) {
  if (m !== "index") return MEASURE_LABELS[m] + " (%)";
  if (isIndex()) return "Index Level";
  const first = rowFor(chartCodes()[0]);
  if (first && first.unit === "ratio") return "Published Ratio";
  return chartedRates() ? "Published Value (%)" : "Published Level";
}

function currentMeasure() {
  const s = urlState();
  let m = measures().indexOf(s.measure) !== -1 ? s.measure : measures()[0];
  if (m === "index" && SINGLE_LEVEL[s.dataset] && chartCodes().length > 1) m = "yoy";
  return m;
}

function chartCodes() {
  const s = urlState();
  if (s.series.length) return s.series.slice(0, MAX_SERIES);
  const main = (OV && OV.main_series ? OV.main_series.map(m => m.code) : []);
  // Where levels only chart one at a time, the default is the first headline
  // series in its own unit — "$2.27 a dozen" — not four items as rates.
  return (SINGLE_LEVEL[s.dataset] ? main.slice(0, 1) : main).slice(0, MAX_SERIES);
}

/* ---- periods ---- */

/* A quarterly dataset dates each quarter by its first month (2026-04 is Q2
   2026); shown as a month it would read as April. */
const isQuarterly = () => (OV && OV.release && OV.release.frequency === "quarterly") ||
  (LIST && LIST.period_months === 3);
const quarterOf = iso => "Q" + Math.ceil(Number(iso.slice(5, 7)) / 3) + " " + iso.slice(0, 4);
const periodShort = iso => (iso && isQuarterly() ? quarterOf(iso) : fmtPeriod(iso));
const periodLong = iso => (iso && isQuarterly() ? quarterOf(iso) : fmtPeriodLong(iso));

/* ---- header and tiles ---- */

function sourceLine(rel, trust) {
  const label = TRUST_LABELS[trust];
  return (OV.credit_line || "Source: U.S. Bureau of Labor Statistics.").replace(/\.$/, "") +
    " · " + rel.source_id + " · Data through " + periodShort(rel.latest_period) +
    " · Retrieved " + fmtStamp(rel.retrieved_at) + (label ? " · " + label : "");
}

function renderHeader() {
  const rel = OV.release;
  document.getElementById("page-asof").textContent = "Ingested " + fmtStamp(rel.ingested_at);
  document.getElementById("page-sub").textContent =
    (OV.credit_line || "").replace(/^Sources?: /, "").replace(/\.$/, "") +
    " · Data through " + periodLong(rel.latest_period);
  const el = document.getElementById("stale-banner");
  el.innerHTML = OV.stale
    ? '<div class="banner" role="alert">This surface is stale: the newest ingested data is for ' +
      fmtPeriodLong(rel.latest_period) + ", ingested " + fmtStamp(rel.ingested_at) + ".</div>"
    : "";
}

/* Direction reads off the arrow; the hidden word is the same fact for a
   screen reader. The amount beside it is unsigned and carries its unit. */
function arrowFor(r) {
  return r === 0 ? ""
    : '<span aria-hidden="true">' + (r > 0 ? "▲" : "▼") + "</span> " +
      '<span class="visually-hidden">' + (r > 0 ? "up " : "down ") + "</span>";
}

function renderTiles() {
  const tiles = OV.tiles || [];
  const d = dp();
  // Rows of equal length: six tiles as two rows of three, four as one row,
  // never five and a lonely sixth.
  const grid = document.getElementById("tiles");
  grid.classList.toggle("cols-3", tiles.length % 3 === 0 && tiles.length % 4 !== 0);
  grid.classList.toggle("cols-4", tiles.length % 4 === 0);
  grid.innerHTML = tiles.map(t => {
    let value, delta = MISSING, dir = "flat";
    if (t.measure) {                       // index dataset: a rate in %
      value = t.value === null ? MISSING : fmtNum(t.value, 1) + '<span class="unit">%</span>';
      if (t.delta_pp !== null) {
        const r = Number(t.delta_pp.toFixed(1));
        dir = r > 0 ? "up" : r < 0 ? "down" : "flat";
        delta = arrowFor(r) + fmtNum(Math.abs(t.delta_pp), 1) + " pp";
      }
    } else {                               // levels: the published value
      const td = DP[urlState().dataset] !== undefined ? d : dpFor(t.unit, t.series_code);
      const unit = t.unit === "%" ? "%" : "";
      const cur = t.unit === "$" || t.unit === "1982-84 $" ? "$" : t.unit === "¥" ? "¥" : "";
      value = t.value === null ? MISSING : withUnit(t.value, t.unit, td);
      if (t.delta !== null) {
        const r = Number(t.delta.toFixed(td));
        dir = r > 0 ? "up" : r < 0 ? "down" : "flat";
        // Direction reads off the arrow; the hidden word is the same fact for
        // a screen reader. The amount carries its unit: "$0.083", "0.2 pp".
        delta = arrowFor(r) + cur + fmtNum(Math.abs(t.delta), td) + (unit === "%" ? " pp" : "");
      }
    }
    return '<div class="strip-cell">' +
      '<div class="strip-label" title="' + escapeHtml(t.series_name || t.label) + '">' +
        escapeHtml(t.label) + "</div>" +
      '<div class="strip-value num">' + value + "</div>" +
      '<div class="strip-delta num ' + dir + '">' + delta + "</div>" +
      "</div>";
  }).join("");
  // An annual dataset dated December compares with the December before:
  // the API says "vs 2024", which on a weights page must say which month.
  const december = OV.release.latest_period.slice(5, 7) === "12" && OV.period_months === 12;
  const cmp = tiles.map(t => (december && /^vs \d{4}$/.test(t.comparison || ""))
    ? t.comparison.replace("vs ", "vs December ") : t.comparison).filter(Boolean);
  document.getElementById("strip-foot").textContent = tiles.length
    ? "Change " + (cmp[0] || "vs the prior period") +
      (tiles[0].measure ? ", in percentage points" : "")
    : "";
}

/* ---- chart ---- */

async function renderChart() {
  const codes = chartCodes();
  const measure = currentMeasure();
  const sel = document.getElementById("measure-select");
  sel.innerHTML = measures().map(m => '<option value="' + m + '">' + measureLabel(m) + "</option>").join("");
  sel.value = measure;
  sel.disabled = measures().length === 1;
  const note = document.getElementById("chart-note");
  note.textContent = measure !== urlState().measure && urlState().measure === "index"
    ? "Several items: shown as % change"
    : "";
  if (!codes.length) return;
  const seq = ++chartSeq;
  const data = await getJSON(api("/observations?series=" + encodeURIComponent(codes.join(",")) +
                                 "&measure=" + measure));
  if (seq !== chartSeq) return;
  OBS = data;
  // A one-line chart has no legend, so the caption names the series.
  if (data.series.length === 1 && !note.textContent) {
    note.textContent = data.series[0].name_en + " · " + data.series[0].code;
  }
  const range = urlState().range;
  const latest = data.release.latest_period;
  const start = range === "max" ? null
    : (Number(latest.slice(0, 4)) - Number(range)) + latest.slice(4, 7);
  // An index or a ratio is a bare number on the axis; yen reads as "yen"
  // after a figure, never as a trailing "¥".
  const axisUnit = data.unit === "index" ? "Index" + (data.release.base ? ", " + data.release.base : "")
    : data.unit === "¥" ? "Yen" : data.unit === "ratio" ? "Ratio" : data.unit;
  const suffix = data.unit === "index" || data.unit === "ratio" ? ""
    : data.unit === "¥" ? "yen" : data.unit;
  const first = rowFor(codes[0]);
  const cfg = {
    series: data.series.map((s, i) => ({
      name: s.name_en, slot: i + 1,
      points: s.points.filter(p => !start || p[0] >= start),
    })),
    unit: data.unit === "%" ? "%" : data.unit,
    dp: measure === "index" ? (first && DP[urlState().dataset] === undefined
                               ? dpFor(first.unit, first.code) : dp()) : 1,
    unitSuffix: data.unit === "%" ? "" : (measure === "index" ? suffix : ""),
    yAxisName: measure === "index" ? (isIndex() ? "Index" : axisUnit) : "%",
    trust: data.trust, legendFloor: 1100,
    // Under the plot on a phone each long series name takes a row of its
    // own; reserve a row per series so the legend never lands on the dates.
    legendBottomNarrow: data.series.length > 1 ? 24 * data.series.length + 8 : undefined,
    sourceLine: sourceLine(data.release, data.trust),
  };
  const el = document.getElementById("main-chart");
  el.innerHTML = "";
  if (chart) chart.dispose();
  chart = obsChart(el, "line", cfg);
  document.getElementById("main-source").innerHTML =
    escapeHtml(cfg.sourceLine).replace("Source: ", 'Source: <a href="' +
      escapeHtml(data.release.source_page) + '" rel="noopener">') .replace(" · " + escapeHtml(data.release.source_id),
      "</a> · " + escapeHtml(data.release.source_id));
  const calcEl = document.getElementById("main-calc");
  if (data.trust === "derived") {
    calcEl.style.display = "";
    calcEl.innerHTML = "<summary>Show calculation</summary><div class=\"calc-body\">" +
      MEASURE_LABELS[measure] + ": <code>" + escapeHtml(data.calc) + "</code><br>Inputs: " +
      "published values from " + escapeHtml(data.release.source_name) + " (sha256 " +
      data.release.sha256.slice(0, 12) + "…), release “" + escapeHtml(data.release.label) +
      "”.</div>";
  } else {
    calcEl.style.display = "none";
  }
  const stem = urlState().dataset + "-" + measure;
  document.getElementById("main-png").onclick = () => chart.exportPNG(stem + ".png");
  document.getElementById("main-csv").onclick = () => chart.exportCSV(stem + ".csv", [
    data.release.source_name + " — " + measureLabel(measure),
    "Series: " + data.series.map(s => s.code + " " + s.name_en).join("; "),
    TRUST_LABELS[data.trust] ? "Trust: " + TRUST_LABELS[data.trust]
      : "Trust: calculated from published values (formula below)",
    "Calculation: " + data.calc,
    "Unit: " + data.unit,
    "Release: " + data.release.label + " (" + data.release.source_id + ")",
    "Retrieved: " + fmtStamp(data.release.retrieved_at),
    "Permalink: " + location.href,
  ]);
}

/* ---- table ---- */

function rowCells(s) {
  const d = dp();
  if (isIndex()) {
    return [
      [fmtIndex(s.index), s.index],
      [fmtSigned(s.mom, 1, ""), s.mom],
      [fmtSigned(s.yoy, 1, ""), s.yoy],
      [fmtSigned(s.ann3m, 1, ""), s.ann3m],
    ];
  }
  if (mixedUnits()) {
    // Units differ row to row, so each row is shown at its own unit's
    // precision; a change of a percentage is in points and says so.
    const rd = dpFor(s.unit, s.code);
    const pp = s.unit === "%" ? " pp" : "";
    return [
      [fmtNum(s.latest, rd), s.latest],
      [fmtSigned(s.delta_1m, rd, "") + (s.delta_1m === null ? "" : pp), s.delta_1m],
      [fmtSigned(s.delta_12m, rd, "") + (s.delta_12m === null ? "" : pp), s.delta_12m],
    ];
  }
  return [
    [fmtNum(s.latest, d), s.latest],
    [fmtSigned(s.delta_1m, d, ""), s.delta_1m],
    [fmtSigned(s.delta_12m, d, ""), s.delta_12m],
  ];
}

function headers() {
  if (isIndex()) return ["Index", "MoM %", "YoY %", "3m ann. %"];
  // Mixed units carry a unit column; a ratio is neither a percentage nor
  // in percentage points.
  if (mixedUnits() || (LIST.series[0] && LIST.series[0].unit === "ratio")) {
    return ["Latest", stepLabel(), "Δ 12 months"];
  }
  const u = isRate() ? "pp" : (LIST.series[0] && LIST.series[0].unit === "$" ? "$" : "");
  const lvl = isRate() ? "Latest %" : "Latest" + (u ? " " + u : "");
  // An annual series (December weights) has one change: on the year before.
  if (LIST.period_months === 12) return [lvl, "Δ 1 year " + u];
  return [lvl, stepLabel() + " " + u, "Δ 12 months " + u];
}

// The change on the period before: a month, or a quarter.
const stepLabel = () => (LIST && LIST.period_months === 3 ? "Δ 1 quarter" : "Δ 1 month");

/* On a phone the table keeps the level and the year-on-year column: for an
   index row that is Index and YoY (MoM and 3m ann. step aside), for a level
   row Latest and the 12-month change. */
function NARROW_HIDE(i) {
  if (isIndex()) return i === 1 || i === 3;
  return LIST.period_months !== 12 && i === 1;
}

function visibleRows() {
  const s = urlState();
  let rows = LIST.series;
  if (NATIONAL && s.national) rows = rows.filter(r => r.name_en.indexOf(" — ") === -1);
  return rows;
}

function renderTable() {
  const rows = visibleRows();
  const chosen = chartCodes();
  const hdr = headers();
  const annual = !isIndex() && LIST.period_months === 12;
  const mixed = mixedUnits();
  document.getElementById("row-count").textContent = fmtNum(rows.length, 0);
  const body = rows.map(s => {
    const on = chosen.indexOf(s.code) !== -1;
    let cells = rowCells(s);
    if (annual) cells = cells.slice(0, 2);
    const asOf = periodShort(s.as_of) + (s.discontinued ? ' <span class="muted">· ended</span>' : "");
    return "<tr>" +
      '<td><input type="checkbox" data-code="' + escapeHtml(s.code) + '"' +
        (on ? " checked" : "") + ' aria-label="Chart ' + escapeHtml(s.name_en) + '"></td>' +
      '<td title="' + escapeHtml(s.name_en) + '">' + escapeHtml(s.name_en) + "</td>" +
      '<td class="mono muted narrow-hide">' + escapeHtml(s.code) + "</td>" +
      (mixed ? '<td class="narrow-hide">' + escapeHtml(unitName(s.unit)) + "</td>" : "") +
      cells.map((c, i) => '<td class="num' + (NARROW_HIDE(i) ? " narrow-hide" : "") + '"' +
        (c[1] === null || c[1] === undefined ? "" : ' data-sort="' + c[1] + '"') + ">" +
        c[0] + "</td>").join("") +
      '<td class="narrow-hide">' + sparkSVG(s.spark || [], 110, 26) + "</td>" +
      '<td class="num">' + asOf + "</td></tr>";
  }).join("");
  document.getElementById("table-wrap").innerHTML =
    '<table class="data"><thead><tr><th><span class="visually-hidden">Chart</span></th>' +
    '<th>Series</th><th class="narrow-hide">Code</th>' +
    (mixed ? '<th class="narrow-hide">Unit</th>' : "") +
    hdr.map((h, i) => '<th class="num' + (NARROW_HIDE(i) ? " narrow-hide" : "") + '">' + h + "</th>").join("") +
    '<th class="narrow-hide">5y trend</th><th class="num">As of</th></tr></thead><tbody>' + body + "</tbody></table>";
  document.querySelectorAll('#table-wrap input[type="checkbox"]').forEach(cb => {
    cb.addEventListener("change", () => toggleSeries(cb.getAttribute("data-code"), cb));
  });
  const calc = LIST.calc || {};
  document.getElementById("table-calc").innerHTML = "<summary>Show calculation</summary>" +
    '<div class="calc-body">' + escapeHtml(isIndex() ? CALC_TEXT
      : [calc.delta_1m, calc.delta_12m].filter(Boolean).join(" ")) + "</div>";
}

/* The unit column's wording: the API's unit labels, spelled out where a
   symbol alone would be ambiguous in a column of mixed units. */
const UNIT_NAMES = { "¥": "yen", "index": "index, 2020 = 100", "10k persons": "10k persons",
                     "ratio": "openings per applicant" };
/* A dataset whose indices stand on another base says so: the wording above
   is Japan's wage survey (2020 = 100). Hong Kong's GDP deflator is on 2024;
   its retail sales indices on October 2019 – September 2020. */
const UNIT_NAMES_BY_DATASET = {
  "gdp-hk": { "index": "index, 2024 = 100" },
  "retail-hk": { "index": "index, Oct 2019 – Sep 2020 = 100" },
};
const unitName = u => ((UNIT_NAMES_BY_DATASET[urlState().dataset] || {})[u] ||
  UNIT_NAMES[u] || u || "");

const CALC_TEXT = "YoY = (index[t] / index[t−12] − 1) × 100; MoM = (index[t] / index[t−1] − 1) × 100; " +
  "3m ann. = ((index[t] / index[t−3]) ^ 4 − 1) × 100.";

function toggleSeries(code, cb) {
  let codes = chartCodes().slice();
  const i = codes.indexOf(code);
  if (i === -1) {
    // One unit per axis: a series in another unit starts a new chart
    // rather than sharing an axis with, say, a percentage.
    const cur = rowFor(codes[0]), next = rowFor(code);
    if (cur && next && cur.unit !== next.unit) {
      document.querySelectorAll('#table-wrap input[type="checkbox"]').forEach(x => {
        x.checked = x.getAttribute("data-code") === code;
      });
      document.getElementById("filter-note").textContent =
        "A series in a different unit starts a new chart.";
      setUrlState({ series: [code], measure: "" });
      renderChart().catch(err => sectionError("main-chart", err));
      return;
    }
    if (codes.length >= MAX_SERIES) {
      cb.checked = false;
      document.getElementById("filter-note").textContent =
        "Up to " + MAX_SERIES + " series on the chart; untick one first.";
      return;
    }
    codes.push(code);
  } else {
    codes.splice(i, 1);
  }
  document.getElementById("filter-note").textContent = "";
  setUrlState({ series: codes });
  renderChart().catch(err => sectionError("main-chart", err));
}

async function loadList() {
  const q = urlState().q.trim();
  const seq = ++listSeq;
  const data = await getJSON(api("/series" + (q ? "?q=" + encodeURIComponent(q) : "")));
  if (seq !== listSeq) return false;
  LIST = data;
  return true;
}

/* ---- provenance ---- */

function renderProvenance() {
  const rel = OV.release;
  document.getElementById("prov-card").innerHTML =
    '<div class="prov-card"><div class="prov-card-head">' +
      '<div class="prov-card-title">Data Source</div>' +
      '<div class="prov-card-id">' + escapeHtml(rel.source_id) + "</div></div>" +
    '<div class="prov-grid">' +
      '<div class="prov-field full"><div class="prov-label">Official source</div>' +
        '<div class="prov-value"><a href="' + escapeHtml(rel.source_page) + '" rel="noopener">' +
        escapeHtml(rel.source_name) + "</a></div>" +
        '<div class="prov-sub">Series coverage ' + periodLong(rel.coverage_start) +
        " – latest period · " + escapeHtml(OV.credit_line || "") + "</div></div>" +
      '<div class="prov-field"><div class="prov-label">Release</div>' +
        '<div class="prov-value">Data through ' + periodLong(rel.latest_period) + "</div>" +
        '<div class="prov-sub">Published ' + escapeHtml(rel.frequency || "") + "</div></div>" +
      '<div class="prov-field"><div class="prov-label">Retrieved</div>' +
        '<div class="prov-value num">' + fmtStamp(rel.retrieved_at) + "</div>" +
        '<div class="prov-sub">Archived ' + fmtStamp(rel.ingested_at) + "</div></div>" +
      '<div class="prov-field full"><div class="prov-label">Archived checksum (SHA-256)</div>' +
        '<div class="prov-hash">' + escapeHtml(rel.sha256) + "</div></div>" +
    "</div></div>";
}

/* ---- wiring ---- */

function sectionError(id, err) {
  document.getElementById(id).innerHTML = '<div class="state-error">This panel failed to load.' +
    "<details><summary>See details</summary><pre>" + escapeHtml(String(err)) + "</pre></details></div>";
}

function renderDatasetSeg() {
  const seg = document.getElementById("dataset-seg");
  if (CHOICES.length < 2) { seg.style.display = "none"; return; }
  const cur = urlState().dataset;
  seg.innerHTML = CHOICES.map(c => '<button type="button" data-ds="' + c.slug + '" aria-pressed="' +
    (c.slug === cur) + '">' + escapeHtml(c.label) + "</button>").join("");
  seg.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    if (b.getAttribute("data-ds") === urlState().dataset) return;
    setUrlState({ dataset: b.getAttribute("data-ds"), series: [], measure: "", q: DEFAULT_Q });
    load();
  }));
}

function wire() {
  const q = document.getElementById("q");
  // Every table already filters itself (sortable.js). The server-side search
  // is only for a dataset too large to list whole, which declares a default.
  if (!DEFAULT_Q) q.closest(".search-rail").style.display = "none";
  q.value = urlState().q;
  let timer = null;
  q.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      setUrlState({ q: q.value });
      try { if (await loadList()) renderTable(); } catch (err) { sectionError("table-wrap", err); }
    }, 300);
  });
  const nat = document.getElementById("national");
  if (nat) {
    nat.parentElement.style.display = NATIONAL ? "" : "none";
    nat.checked = urlState().national;
    nat.addEventListener("change", () => { setUrlState({ national: nat.checked }); renderTable(); });
  }
  document.getElementById("measure-select").addEventListener("change", e => {
    setUrlState({ measure: e.target.value });
    renderChart().catch(err => sectionError("main-chart", err));
  });
  const seg = document.getElementById("range-seg");
  seg.querySelectorAll("button").forEach(b => {
    b.setAttribute("aria-pressed", String(b.dataset.range === urlState().range));
    b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      setUrlState({ range: b.dataset.range });
      renderChart().catch(err => sectionError("main-chart", err));
    });
  });
}

async function load() {
  renderDatasetSeg();
  try {
    OV = await getJSON(api("/overview"));
  } catch (err) {
    sectionError("tiles", err);
    return;
  }
  renderHeader();
  renderTiles();
  renderProvenance();
  document.getElementById("q").value = urlState().q;
  try {
    await loadList();
    renderTiles();              // precision depends on the listing's shape
    renderTable();
  } catch (err) {
    sectionError("table-wrap", err);
  }
  await renderChart().catch(err => sectionError("main-chart", err));
}

initThemeToggle(() => { if (chart) renderChart().catch(() => {}); });
wire();
load();
