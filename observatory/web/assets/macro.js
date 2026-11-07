/* Macro overview: the section's five datasets on one screen.

   Each panel is a reduction of its full page — the two readings an economist
   would quote, one small chart, and the page link. Figures come from the same
   API the product pages use and are never baked into the markup; a panel that
   cannot reach its data says so rather than showing stale numbers. Charts are
   fixed views (no controls), so the bare URL is already citable. */
"use strict";

const CHARTS = [];   // live obsChart handles, re-rendered on theme change

function getJSON(url) {
  return fetch(url).then(r => (r.ok ? r.json() : Promise.reject(new Error(url + " " + r.status))));
}

function reading(label, valueHtml, note) {
  return '<div class="ov-reading">' +
    '<div class="ov-reading-label" title="' + escapeHtml(label) + '">' + escapeHtml(label) + "</div>" +
    '<div class="ov-reading-value">' + valueHtml + "</div>" +
    (note ? '<div class="ov-reading-note">' + escapeHtml(note) + "</div>" : "") +
    "</div>";
}

function unitSpan(v, unit) {
  return v + '<span class="unit">' + escapeHtml(unit) + "</span>";
}

/* codeSpec, when given, is the one /observations request the chart draws
   unchanged; it puts "Copy Code" beside the CSV button. */
function mountChart(elId, cfg, pngId, csvId, filename, csvHeader, codeSpec) {
  const el = document.getElementById(elId);
  el.innerHTML = "";
  const handle = obsChart(el, "line", cfg);
  CHARTS.push(handle);
  const png = document.getElementById(pngId);
  const csv = document.getElementById(csvId);
  png.addEventListener("click", e => { e.preventDefault(); handle.exportPNG(filename + ".png"); });
  csv.addEventListener("click", e => { e.preventDefault(); handle.exportCSV(filename + ".csv", csvHeader); });
  if (codeSpec) obsCodeButton(csv, () => codeSpec);
  return handle;
}

/* A panel that cannot load leaves no half-rendered chrome behind. */
function panelFailed(prefix) {
  const readings = document.getElementById(prefix + "-readings");
  if (readings) readings.innerHTML =
    '<p class="ov-reading-note">Live figures are unavailable right now — the full page may still have them.</p>';
  const chart = document.getElementById(prefix + "-chart");
  if (chart) chart.remove();
  ["-png", "-csv"].forEach(sfx => {
    const a = document.getElementById(prefix + sfx);
    if (a) a.remove();
  });
}

/* ---- staleness: one banner naming every dataset behind schedule ---- */

const STALE = [];
function noteStale(d, name) {
  if (!d.stale) return;
  STALE.push(name);
  document.getElementById("stale-banner").innerHTML =
    '<div class="banner">A source release is behind schedule: ' +
    escapeHtml(STALE.join(", ")) +
    ". Panels show the last published data.</div>";
}

function staleMark(d) {
  return d.stale ? " · <b>this release is behind schedule</b>" : "";
}

/* ---- inflation ---- */

async function fillCpi() {
  const ov = await getJSON("/api/v1/cpi-jp/overview");
  noteStale(ov, "Consumer Price Index");
  const byKey = {};
  ov.tiles.forEach(t => { byKey[t.key] = t; });

  document.getElementById("cpi-asof").textContent =
    "Data through " + fmtPeriodLong(ov.release.latest_period);
  document.getElementById("cpi-readings").innerHTML =
    reading("Headline · YoY", unitSpan(fmtNum(byKey.headline_yoy.value, 1), "%"), "calculated") +
    reading("Core · YoY", unitSpan(fmtNum(byKey.core_yoy.value, 1), "%"),
      "less fresh food, calculated") +
    reading("3m Annualized", unitSpan(fmtNum(byKey.headline_ann3m.value, 1), "%"),
      "headline, calculated");

  const wanted = ov.main_series.filter(m => m.role === "headline" || m.role === "core");
  const start = (Number(ov.release.latest_period.slice(0, 4)) - 10) +
    ov.release.latest_period.slice(4, 7);
  const obsUrl = "/api/v1/cpi-jp/observations?series=" +
    wanted.map(m => m.code).join(",") + "&measure=yoy&start=" + start;
  const obs = await getJSON(obsUrl);

  const byCode = {};
  wanted.forEach(m => { byCode[m.code] = m; });
  const source = "Source: Statistics Bureau of Japan via e-Stat · index levels official, " +
    "YoY calculated from them" + staleMark(ov);
  mountChart("cpi-chart", {
    series: obs.series.map(s => ({
      name: byCode[s.code].label, slot: byCode[s.code].slot, points: s.points,
    })),
    unit: "%", yAxisName: "% YoY", trust: obs.trust, sourceLine: source.replace(/<[^>]+>/g, ""),
  }, "cpi-png", "cpi-csv", "japan-cpi-yoy-10y", [
    "Japan CPI — headline and core, year over year (%), last 10 years",
    "source: Statistics Bureau of Japan, via e-Stat (" + ov.release.source_id + ")",
    "trust: derived · " + obs.calc,
    "retrieved: " + ov.release.retrieved_at,
  ], { url: obsUrl, start: null, release: obs.release, source: source.replace(/<[^>]+>/g, "") });
  document.getElementById("cpi-source").innerHTML = source;
}

/* ---- Bank of Japan ---- */

async function fillBoj() {
  const ov = await getJSON("/api/v1/boj-assets/overview");
  noteStale(ov, "Bank of Japan");
  const byKey = {};
  ov.tiles.forEach(t => { byKey[t.key] = t; });
  const h = byKey.holdings, p = byKey.from_peak;
  const tn = v => (v === null || v === undefined ? null : v / 10000);

  document.getElementById("boj-asof").textContent =
    "Data through " + fmtPeriodLong(ov.release.latest_period);
  document.getElementById("boj-readings").innerHTML =
    reading("JGB Holdings", "¥" + unitSpan(fmtNum(tn(h.value), 1), "tn"), "as published") +
    reading("From the Peak", unitSpan(fmtSigned(p.pct, 1), "%"),
      "vs " + fmtPeriodLong(p.peak_period) + ", calculated");

  const head = ov.main_series.find(m => m.role === "headline");
  const obs = await getJSON("/api/v1/boj-assets/observations?series=" + head.code + "&measure=index");
  const s = obs.series[0];
  const points = s.points.map(pt => [pt[0], tn(pt[1])]);
  const peakPt = points.find(pt => pt[0] === p.peak_period);

  const source = "Source: Bank of Japan · holdings as published, shown in ¥tn (exact ÷10,000)" +
    staleMark(ov);
  mountChart("boj-chart", {
    series: [{ name: "BOJ JGB holdings", slot: 1, points: points }],
    unit: "index", dp: 1, yAxisName: "¥tn", trust: "official",
    sourceLine: source.replace(/<[^>]+>/g, ""),
    annotations: peakPt ? [{ x: peakPt[0], y: peakPt[1],
      text: "Peak ¥" + fmtNum(peakPt[1], 0) + "tn" }] : [],
  }, "boj-png", "boj-csv", "boj-jgb-holdings", [
    "Bank of Japan — JGB holdings, end of month (¥tn)",
    "source: Bank of Japan, Time-Series Data Search (" + head.code + ")",
    "trust: official · published in ¥100mn, shown in ¥tn (exact ÷10,000)",
    "retrieved: " + ov.release.retrieved_at,
  ]);
  document.getElementById("boj-source").innerHTML = source;
}

/* ---- yield curve ---- */

async function fillRates() {
  const startYear = new Date().getFullYear() - 10;
  const obsUrl = "/api/v1/jgb-yields/observations?series=10Y,2Y&measure=index&start=" +
    startYear + "-01";
  const obs = await getJSON(obsUrl);
  noteStale(obs, "JGB yields");
  const last = code => {
    const s = obs.series.find(x => x.code === code);
    for (let i = s.points.length - 1; i >= 0; i--) {
      if (s.points[i][1] !== null) return s.points[i][1];
    }
    return null;
  };
  const y10 = last("10Y"), y2 = last("2Y");
  const day = obs.release.latest_period;
  document.getElementById("rates-asof").textContent =
    "Data through " + Number(day.slice(8, 10)) + " " + fmtPeriodLong(day);
  document.getElementById("rates-readings").innerHTML =
    reading("10-Year Yield", unitSpan(fmtNum(y10, 3), "%"), "as published") +
    reading("2s10s Spread", unitSpan(fmtSigned(y10 - y2, 3), "pp"), "calculated");

  const slots = { "10Y": 1, "2Y": 3 };   // same slots the yield-curve page uses
  const source = "Source: Ministry of Finance · constant-maturity yields as published" +
    staleMark(obs);
  mountChart("rates-chart", {
    series: obs.series.map(s => ({
      name: s.code === "10Y" ? "10-year" : "2-year", slot: slots[s.code] || 6, points: s.points,
    })),
    unit: "%", dp: 3, yAxisName: "% per year", isoPeriods: true, trust: "official",
    sourceLine: source.replace(/<[^>]+>/g, ""),
  }, "rates-png", "rates-csv", "jgb-yields-10y-2y", [
    "JGB constant-maturity yields — 10-year and 2-year (% per year), last 10 years",
    "source: Ministry of Finance, Japan",
    "trust: official · yields exactly as published; can be negative",
    "retrieved: " + obs.release.retrieved_at,
  ], { url: obsUrl, start: null, release: obs.release, source: source.replace(/<[^>]+>/g, "") });
  document.getElementById("rates-source").innerHTML = source;
}

/* ---- inbound ---- */

async function fillInbound() {
  const d = await getJSON("/api/v1/jnto-visitors/arrivals");
  noteStale(d, "Visitor arrivals");
  const totals = d.values.total;
  let last = -1;
  for (let i = totals.length - 1; i >= 0; i--) {
    if (totals[i] !== null && totals[i] !== undefined) { last = i; break; }
  }
  const latest = totals[last];
  const prior = last >= 12 ? totals[last - 12] : null;
  const yoy = prior ? (latest / prior - 1) * 100 : null;
  const provisional = (d.provisional_periods || []).indexOf(d.periods[last]) !== -1;

  document.getElementById("inbound-asof").textContent =
    "Data through " + fmtPeriodLong(d.periods[last]);
  document.getElementById("inbound-readings").innerHTML =
    reading("Arrivals · Month", unitSpan(fmtNum(latest / 1e6, 2), "mn"),
      provisional ? "official JNTO estimate" : "as published") +
    reading("Year over Year", unitSpan(fmtSigned(yoy, 1), "%"), "calculated");

  const from = String(new Date().getFullYear() - 11) + "-01-01";
  const points = d.periods.map((iso, i) => [iso,
    totals[i] === null || totals[i] === undefined ? null : totals[i] / 1e6])
    .filter(pt => pt[0] >= from);

  const source = "Source: Japan National Tourism Organization (JNTO) · counts as published" +
    ((d.provisional_periods || []).length
      ? " · latest " + d.provisional_periods.length + " months are JNTO estimates" : "") +
    staleMark(d);
  mountChart("inbound-chart", {
    series: [{ name: "Visitor arrivals", slot: 1, points: points }],
    unit: "index", dp: 2, yAxisName: "mn / month", trust: "official",
    sourceLine: source.replace(/<[^>]+>/g, ""),
  }, "inbound-png", "inbound-csv", "japan-visitor-arrivals", [
    "Visitor arrivals to Japan — all markets, monthly (millions)",
    "source: Japan National Tourism Organization (JNTO)",
    "trust: official · the most recent months are JNTO estimates, rounded",
    "retrieved: " + d.release.retrieved_at,
  ]);
  document.getElementById("inbound-source").innerHTML = source;
}

/* ---- population ---- */

async function fillPop() {
  const reg = await getJSON("/api/v1/population-jp/prefectures");
  const hist = await getJSON("/api/v1/population-jp-history/prefectures");
  noteStale(reg, "Population");
  const nat = reg.national || "00";
  const col = reg.values[nat + ".all.population"];
  const latest = col[col.length - 1];
  const latestYear = reg.periods[reg.periods.length - 1].slice(0, 4);

  // The live vintage may not carry the prior year's national total; the
  // history dataset publishes the same register-basis series and does.
  let prior = col.length > 1 ? col[col.length - 2] : null;
  if (prior === null || prior === undefined) {
    const histCol = hist.values[(hist.national || "00") + ".A2301"];
    const i = hist.periods.indexOf((Number(latestYear) - 1) + "-01-01");
    if (i !== -1) prior = histCol[i];
  }
  const chg = prior === null || prior === undefined ? null : (latest / prior - 1) * 100;

  document.getElementById("pop-asof").textContent =
    "As of 1 January " + latestYear;
  document.getElementById("pop-readings").innerHTML =
    reading("Registered Residents", unitSpan(fmtNum(latest / 1e6, 2), "mn"), "as published") +
    (chg === null
      ? reading("Change · 1 Year", MISSING, "prior year not published")
      : reading("Change · 1 Year", unitSpan(fmtSigned(chg, 2), "%"), "calculated"));

  const hv = hist.values[(hist.national || "00") + ".A1101"];
  const points = hist.periods
    .map((iso, i) => [iso.slice(0, 4), hv[i] === null || hv[i] === undefined ? null : hv[i] / 1e6])
    .filter(pt => pt[1] !== null);

  const source = "Sources: Basic Resident Register (Ministry of Internal Affairs) · long-run " +
    "total population (1 October basis) via the Statistics Bureau" + staleMark(reg);
  mountChart("pop-chart", {
    series: [{ name: "Total population (1 Oct basis)", slot: 1, points: points }],
    unit: "index", dp: 1, yAxisName: "mn", xType: "category", trust: "official",
    sourceLine: source.replace(/<[^>]+>/g, ""),
  }, "pop-png", "pop-csv", "japan-population", [
    "Japan total population, 1 October basis (millions)",
    "source: Statistics Bureau of Japan — System of Social and Demographic Statistics (A1101)",
    "trust: official · counts as published",
    "retrieved: " + hist.release.retrieved_at,
  ]);
  document.getElementById("pop-source").innerHTML = source;
}

/* ---- boot ---- */

fillCpi().catch(() => panelFailed("cpi"));
fillBoj().catch(() => panelFailed("boj"));
fillRates().catch(() => panelFailed("rates"));
fillInbound().catch(() => panelFailed("inbound"));
fillPop().catch(() => panelFailed("pop"));

initThemeToggle(() => { CHARTS.forEach(c => c.render()); });
