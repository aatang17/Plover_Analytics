/* US Dollar Exchange Rates (us-fx.html) — the Federal Reserve Board's H.10
   rates, daily (app/adapters/fed_h10.py).

   The page answers "where is the dollar now, and how has it moved?": four
   tiles with the latest published rate and its change from the previous
   published day, then one rate charted over the chosen range. The rates are
   quoted differently (yen per dollar, dollars per euro, an index), so they
   never share an axis: one rate is charted at a time.

   The monthly series template (us-series.js) reads changes a calendar month
   back, which on daily data lands on a weekend; here every change is read
   from the published days themselves. Missing is "—"; a gap stays a gap.
   URL state: ?pair=USDJPY&range=5 — any view is citable. */
"use strict";

const DATASET = "us-fx";
const PAIRS = [
  { code: "USDJPY", label: "Dollar–Yen", quote: "yen per US dollar", dp: 2,
    rises: "A rise is a stronger dollar.", axis: "Yen per US dollar", suffix: "yen" },
  { code: "USDHKD", label: "Dollar–Hong Kong Dollar", quote: "HK dollars per US dollar", dp: 4,
    rises: "A rise is a stronger dollar. Pegged near 7.80 since October 1983.", axis: "HK dollars per US dollar", suffix: "HK$" },
  { code: "EURUSD", label: "Euro–Dollar", quote: "US dollars per euro", dp: 4,
    rises: "A rise is a weaker dollar.", axis: "US dollars per euro", suffix: "US$" },
  { code: "BROAD", label: "Broad Dollar Index", quote: "January 2006 = 100", dp: 2,
    rises: "Against 26 trading partners, weighted by trade. A rise is a stronger dollar.", axis: "Index, January 2006 = 100", suffix: "" },
];
const RANGES = ["1", "5", "10", "25", "max"];
const CHANGE_CALC = "change[t] = value[t] − value[t−1]; % change = change[t] / value[t−1] × 100, " +
  "where t−1 is the previous day the Board published a rate (a US holiday has none).";

const DATA = {};      // code -> /observations body (full history, as published)
let chart = null;
let chartSeq = 0;

const pairFor = code => PAIRS.find(p => p.code === code);

function urlState() {
  const p = new URLSearchParams(location.search);
  return {
    pair: pairFor(p.get("pair")) ? p.get("pair") : PAIRS[0].code,
    range: RANGES.indexOf(p.get("range")) !== -1 ? p.get("range") : "5",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.pair !== PAIRS[0].code) p.set("pair", s.pair);
  if (s.range !== "5") p.set("range", s.range);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status + " " + (await r.text()).slice(0, 200));
  return r.json();
}

/* One request per rate: their units differ, and the API refuses to put
   different published units in one response. */
function load(code) {
  if (!DATA[code]) {
    DATA[code] = getJSON("/api/v1/" + DATASET + "/observations?series=" + code + "&measure=index")
      .catch(err => { delete DATA[code]; throw err; });
  }
  return DATA[code];
}

/* The published days, oldest first, without gaps. */
const published = body => body.series[0].points.filter(p => p[1] !== null);

function dayLong(iso) {
  return iso ? Number(iso.slice(8, 10)) + " " + MONTHS[Number(iso.slice(5, 7)) - 1] + " " + iso.slice(0, 4) : MISSING;
}

function sourceLine(rel) {
  return "Source: Board of Governors of the Federal Reserve System, H.10 · " + rel.source_id +
    " · Data through " + dayLong(rel.latest_period) + " · Retrieved " + fmtStamp(rel.retrieved_at) +
    " · " + TRUST_LABELS.official;
}

/* ---- header and tiles ---- */

function renderHeader(rel) {
  document.getElementById("page-asof").textContent = "Ingested " + fmtStamp(rel.ingested_at);
  document.getElementById("page-sub").textContent =
    "Federal Reserve Board, H.10 · noon buying rates in New York · Data through " + dayLong(rel.latest_period);
  // Weekly release with the previous week's days; two weeks without one is a missed release.
  const days = (Date.now() - Date.parse(rel.latest_period)) / 864e5;
  document.getElementById("stale-banner").innerHTML = days > 14
    ? '<div class="banner" role="alert">This surface is stale: the newest published rate is for ' +
      dayLong(rel.latest_period) + ", ingested " + fmtStamp(rel.ingested_at) + ".</div>"
    : "";
}

function arrowFor(r) {
  return r === 0 ? ""
    : '<span aria-hidden="true">' + (r > 0 ? "▲" : "▼") + "</span> " +
      '<span class="visually-hidden">' + (r > 0 ? "up " : "down ") + "</span>";
}

function tileHtml(pair, body) {
  // Neutral colour either way: a dollar up is no better than a dollar down,
  // so the arrow and sign carry the direction, never green or red.
  let value = MISSING, delta = MISSING, when = "";
  if (body) {
    const pts = published(body);
    const last = pts[pts.length - 1], prev = pts[pts.length - 2];
    if (last) {
      value = fmtNum(last[1], pair.dp);
      when = fmtDay(last[0]);
    }
    if (last && prev) {
      const ch = last[1] - prev[1];
      const r = Number(ch.toFixed(pair.dp));
      delta = arrowFor(r) + fmtNum(Math.abs(ch), pair.dp) +
        ' <span class="unit">(' + fmtSigned(ch / prev[1] * 100, 2, "%") + ")</span>";
    }
  }
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(pair.quote) + '">' + escapeHtml(pair.label) + "</div>" +
    '<div class="strip-value num">' + value + "</div>" +
    '<div class="strip-delta num flat">' + delta + "</div>" +
    '<div class="strip-foot">' + escapeHtml(pair.quote) + (when ? " · " + when : "") + "</div>" +
    "</div>";
}

function renderTiles(bodies) {
  document.getElementById("tiles").innerHTML = PAIRS.map((p, i) => tileHtml(p, bodies[i])).join("");
  document.getElementById("strip-foot").textContent = "Change from the previous published day";
  const calc = document.getElementById("strip-calc");
  calc.style.display = "";
  calc.innerHTML = "<summary>Show calculation</summary><div class=\"calc-body\"><code>" +
    escapeHtml(CHANGE_CALC) + "</code><br>Inputs: published rates from " +
    "the Federal Reserve Board's H.10 release, as published.</div>";
}

/* ---- chart ---- */

function renderPairSeg() {
  const seg = document.getElementById("pair-seg");
  const cur = urlState().pair;
  seg.innerHTML = PAIRS.map(p => '<button type="button" data-pair="' + p.code + '" aria-pressed="' +
    (p.code === cur) + '">' + escapeHtml(p.label) + "</button>").join("");
  seg.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    seg.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    setUrlState({ pair: b.getAttribute("data-pair") });
    renderChart().catch(err => sectionError("main-chart", err));
  }));
}

function startFor(latest, range) {
  if (range === "max") return null;
  return (Number(latest.slice(0, 4)) - Number(range)) + latest.slice(4);
}

async function renderChart() {
  const s = urlState();
  const pair = pairFor(s.pair);
  const seq = ++chartSeq;
  const body = await load(pair.code);
  if (seq !== chartSeq) return;
  const rel = body.release;
  const start = startFor(rel.latest_period, s.range);
  const points = body.series[0].points.filter(p => !start || p[0] >= start);
  document.getElementById("chart-note").textContent = body.series[0].name_en + " · " + pair.rises;
  const cfg = {
    series: [{ name: pair.label + " (" + pair.quote + ")", slot: 1, points: points }],
    unit: pair.code === "BROAD" ? "index" : pair.suffix,
    dp: pair.dp,
    unitSuffix: pair.code === "BROAD" ? "" : pair.suffix,
    yAxisName: pair.axis,
    trust: "official", legendFloor: 1100,
    sourceLine: sourceLine(rel),
  };
  const el = document.getElementById("main-chart");
  el.innerHTML = "";
  if (chart) chart.dispose();
  chart = obsChart(el, "line", cfg);
  document.getElementById("main-source").innerHTML =
    escapeHtml(cfg.sourceLine).replace("Source: ", 'Source: <a href="' + escapeHtml(rel.source_page) +
      '" rel="noopener">').replace(" · " + escapeHtml(rel.source_id), "</a> · " + escapeHtml(rel.source_id));
  const stem = DATASET + "-" + pair.code.toLowerCase() + (s.range === "max" ? "" : "-" + s.range + "y");
  document.getElementById("main-png").onclick = () => chart.exportPNG(stem + ".png");
  document.getElementById("main-csv").onclick = () => chart.exportCSV(stem + ".csv", [
    rel.source_name + " — " + body.series[0].name_en,
    "Series: " + pair.code + " " + body.series[0].name_en + " (" + pair.quote + ")",
    "Trust: " + TRUST_LABELS.official,
    "Calculation: " + body.calc,
    "Unit: " + pair.quote,
    "Noon buying rates in New York. A US holiday has no rate: blank, never zero.",
    "Release: " + rel.label + " (" + rel.source_id + ")",
    "Retrieved: " + fmtStamp(rel.retrieved_at),
    "Permalink: " + location.href,
  ]);
  obsCodeButton(document.getElementById("main-csv"), () => ({
    url: "/api/v1/" + DATASET + "/observations?series=" + pair.code + "&measure=index",
    start: start, release: rel, source: cfg.sourceLine,
  }));
}

/* ---- provenance ---- */

function renderProvenance(rel) {
  document.getElementById("prov-card").innerHTML =
    '<div class="prov-card"><div class="prov-card-head">' +
      '<div class="prov-card-title">Data Source</div>' +
      '<div class="prov-card-id">' + escapeHtml(rel.source_id) + "</div></div>" +
    '<div class="prov-grid">' +
      '<div class="prov-field full"><div class="prov-label">Official source</div>' +
        '<div class="prov-value"><a href="' + escapeHtml(rel.source_page) + '" rel="noopener">' +
        escapeHtml(rel.source_name) + "</a></div>" +
        '<div class="prov-sub">Noon buying rates in New York, published each Monday with the previous week\'s days</div></div>' +
      '<div class="prov-field"><div class="prov-label">Release</div>' +
        '<div class="prov-value">Data through ' + dayLong(rel.latest_period) + "</div>" +
        '<div class="prov-sub">Daily rates, released weekly</div></div>' +
      '<div class="prov-field"><div class="prov-label">Retrieved</div>' +
        '<div class="prov-value num">' + fmtStamp(rel.retrieved_at) + "</div>" +
        '<div class="prov-sub">Archived ' + fmtStamp(rel.ingested_at) + "</div></div>" +
      '<div class="prov-field full"><div class="prov-label">Archived checksum (SHA-256)</div>' +
        '<div class="prov-hash">' + escapeHtml(rel.sha256) + "</div></div>" +
    "</div></div>";
}

/* ---- wiring ---- */

function sectionError(id, err) {
  document.getElementById(id).innerHTML = '<div class="state-error">This panel failed to load. Reload the page to try again.' +
    "<details><summary>See details</summary><pre>" + escapeHtml(String(err)) + "</pre></details></div>";
}

function wireRange() {
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

async function start() {
  renderPairSeg();
  wireRange();
  const chartDone = renderChart().catch(err => sectionError("main-chart", err));
  const bodies = await Promise.all(PAIRS.map(p => load(p.code).catch(() => null)));
  const rel = (bodies.find(Boolean) || {}).release;
  if (!rel) {
    sectionError("tiles", "No exchange rate could be loaded.");
    return chartDone;
  }
  renderHeader(rel);
  renderTiles(bodies);
  renderProvenance(rel);
  return chartDone;
}

initThemeToggle(() => { if (chart) renderChart().catch(() => {}); });
start();
