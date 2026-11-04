/* Population by prefecture.

   The screen answers one question: which prefectures are growing and
   shrinking, and why. The map ranks all 47 on one measure, the chart puts
   any one of them against fifty years of its own history, and the table is
   the same latest year in full.

   Two datasets feed it and they are not interchangeable:

   - population-jp   — the Basic Resident Register workbooks. The latest
                       year only, but every register flow and both
                       nationalities. Stocks are dated 1 January; the flows
                       are dated to the calendar year they cover, so the
                       change shown for 2025 is the year that ended on the
                       1 January 2026 count.
   - population-jp-history — the Statistics Bureau's regional series, back
                       to 1975. Its register figures sit on the same 1
                       January basis, so the two join; its total-population
                       figures are census and estimate readings taken on 1
                       October and are a different measure, never mixed
                       into the same line.

   Nearly everything that crosses the wire is a published count, and every
   share and rate computed from one is arithmetic done here, stating its
   formula. The single exception is the total fertility rate: it is a
   published rate, stored as issued because its denominator is in no table
   the platform holds, and it is the only number on this page that carries
   the official badge without being a count. */
"use strict";

var REG = null;          // register dataset payload
var HIST = null;         // long-run dataset payload
var GEOS = [];           // [{code, name_ja, name_en, region}]
var GEO_BY_CODE = {};
var NATIONAL = "00";
var mapChart = null;
var mapReady = false;    // japan geojson registered with ECharts
var histChart = null;
var pyramidChart = null;

var MEASURES = {
  change_pct: {
    label: "Change during the year", unit: "%", dp: 2, diverging: true,
    calc: "change % = net change ÷ (population − net change) × 100",
    help: "Net change over the calendar year, against the population at the start of it",
  },
  natural_pct: {
    label: "Natural change", unit: "%", dp: 2, diverging: true,
    calc: "natural % = (births − deaths) ÷ (population − net change) × 100",
    help: "Births less deaths, against the population at the start of the year",
  },
  social_pct: {
    label: "Social change", unit: "%", dp: 2, diverging: true,
    calc: "social % = (net change − natural change) ÷ (population − net change) × 100",
    help: "Everything the register recorded other than births and deaths",
  },
  foreign_pct: {
    label: "Foreign residents", unit: "%", dp: 2, diverging: false,
    calc: "foreign share % = foreign residents ÷ all residents × 100",
    help: "Foreign residents as a share of everyone on the register",
  },
  aged_pct: {
    label: "Aged 65 and over", unit: "%", dp: 1, diverging: false,
    calc: "aged 65+ % = (sum of the five-year bands from 65 upward) ÷ all residents × 100",
    help: "Residents aged 65 and over as a share of everyone on the register",
  },
  birth_rate: {
    label: "Births per 1,000", unit: "per 1,000", dp: 1, diverging: false,
    calc: "crude birth rate = births ÷ mid-year population × 1,000, where " +
          "mid-year population = (population at 1 January + population a year earlier) ÷ 2",
    help: "Births over the calendar year, per thousand of the population living through it",
  },
  death_rate: {
    label: "Deaths per 1,000", unit: "per 1,000", dp: 1, diverging: false,
    calc: "crude death rate = deaths ÷ mid-year population × 1,000, where " +
          "mid-year population = (population at 1 January + population a year earlier) ÷ 2",
    help: "Deaths over the calendar year, per thousand of the population living through it",
  },
  population: {
    label: "Registered residents", unit: "persons", dp: 0, diverging: false,
    calc: null,
    help: "Everyone on the register at 1 January",
  },
};

/* The pyramid's two modes and the two long-run views that are rates rather
   than counts, kept here so the URL, the controls and the chart cannot
   disagree about what exists. */
var PYRAMID_MODES = ["count", "share"];
var HIST_VIEWS = ["level", "index", "age", "vital", "fertility"];

var SEGMENT_LABEL = { all: "All residents", jp: "Japanese residents", fgn: "Foreign residents" };

/* ---------- url state ---------- */

function urlState() {
  var p = new URLSearchParams(location.search);
  var measure = p.get("measure");
  var pref = p.get("pref");
  return {
    measure: MEASURES[measure] ? measure : "change_pct",
    pref: pref && GEO_BY_CODE[pref] && pref !== NATIONAL ? pref : "13",
    hist: HIST_VIEWS.indexOf(p.get("hist")) >= 0 ? p.get("hist") : "level",
    pyr: PYRAMID_MODES.indexOf(p.get("pyr")) >= 0 ? p.get("pyr") : "count",
    scale: p.get("scale") === "log" ? "log" : "linear",
    from: p.get("from") || "",
    to: p.get("to") || "",
    segment: SEGMENT_LABEL[p.get("segment")] ? p.get("segment") : "all",
    muni: p.get("muni") === "all" ? "all" : "leaves",
    sort: p.get("sort") || "change_pct",
    dir: p.get("dir") === "asc" ? "asc" : "desc",
  };
}

function setUrlState(next) {
  var s = Object.assign(urlState(), next);
  // Keys this function does not own (the monthly births section's, in
  // vital.js) are kept; its own are cleared and rewritten.
  var p = new URLSearchParams(location.search);
  ["measure", "pref", "hist", "pyr", "scale", "from", "to", "segment", "muni",
   "sort", "dir"].forEach(function (k) { p.delete(k); });
  if (s.measure !== "change_pct") p.set("measure", s.measure);
  if (s.pref !== "13") p.set("pref", s.pref);
  if (s.hist !== "level") p.set("hist", s.hist);
  if (s.pyr !== "count") p.set("pyr", s.pyr);
  if (s.scale !== "linear") p.set("scale", s.scale);
  if (s.from) p.set("from", s.from);
  if (s.to) p.set("to", s.to);
  if (s.segment !== "all") p.set("segment", s.segment);
  if (s.muni !== "leaves") p.set("muni", s.muni);
  if (s.sort !== "change_pct") p.set("sort", s.sort);
  if (s.dir !== "desc") p.set("dir", s.dir);
  var qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

/* ---------- reading the payloads ---------- */

/* The latest published value of one register series, or null.
   A register series runs over years (stocks at 1 January, flows dated to
   the year they cover); the page shows the newest, so "the last one
   present" is the value, and an absent series is missing, never zero. */
function reg(geo, segment, measure) {
  var col = REG.values[geo + "." + segment + "." + measure];
  if (!col) return null;
  for (var i = col.length - 1; i >= 0; i--) if (col[i] !== null) return col[i];
  return null;
}

/* [[iso, value], ...] for one historical indicator, gaps preserved. */
function hist(geo, indicator) {
  if (!HIST) return [];
  var col = HIST.values[geo + "." + indicator];
  if (!col) return [];
  var out = [];
  for (var i = 0; i < col.length; i++) {
    if (col[i] !== null) out.push([HIST.periods[i], col[i]]);
  }
  return out;
}

/* Every year the long-run dataset publishes anything for, as strings. The
   two reference dates in it (1 January and 1 October) collapse to one year. */
function histYears() {
  var seen = {};
  (HIST ? HIST.periods : []).forEach(function (p) { seen[p.slice(0, 4)] = true; });
  return Object.keys(seen).sort();
}

/* The window actually in force: the URL's, clamped to what exists. */
function histWindow() {
  var years = histYears();
  if (!years.length) return { from: "", to: "", first: "", last: "" };
  var st = urlState();
  var first = years[0], last = years[years.length - 1];
  var from = st.from && years.indexOf(st.from) >= 0 ? st.from : first;
  var to = st.to && years.indexOf(st.to) >= 0 ? st.to : last;
  if (from > to) { from = first; to = last; }
  return { from: from, to: to, first: first, last: last, years: years };
}

function agedTotal(geo, segment) {
  var bands = (REG.age_groups || {}).aged_65_plus || [];
  var sum = 0, seen = false;
  for (var i = 0; i < bands.length; i++) {
    var v = reg(geo, segment, bands[i] + "_total");
    if (v === null) return null;      // a partial sum would understate it
    sum += v; seen = true;
  }
  return seen ? sum : null;
}

/* Population at the start of the year the flows cover. The register
   publishes the closing count and the change, never the opening one. */
function opening(geo, segment) {
  var pop = reg(geo, segment, "population");
  var net = reg(geo, segment, "net_change");
  if (pop === null || net === null) return null;
  var open = pop - net;
  return open > 0 ? open : null;
}

function ratio(numerator, denominator) {
  if (numerator === null || denominator === null || !denominator) return null;
  return numerator / denominator * 100;
}

/* The population that lived through the flow year. A crude rate is per
   thousand of the mid-year population, not of either endpoint: the register
   publishes the count at 1 January and the change over the year before it,
   so the opening count is one subtraction away and the mean of the two is
   the closest thing the register offers to a mid-year figure. */
function midyear(geo, segment) {
  var pop = reg(geo, segment, "population");
  var open = opening(geo, segment);
  if (pop === null || open === null) return null;
  return (pop + open) / 2;
}

function perThousand(numerator, denominator) {
  if (numerator === null || denominator === null || !denominator) return null;
  return numerator / denominator * 1000;
}

function measureValue(geo, key, segment) {
  var seg = segment || "all";
  if (key === "population") return reg(geo, seg, "population");
  if (key === "change_pct") return ratio(reg(geo, seg, "net_change"), opening(geo, seg));
  if (key === "natural_pct") return ratio(reg(geo, seg, "natural_change"), opening(geo, seg));
  if (key === "social_pct") return ratio(reg(geo, seg, "social_change"), opening(geo, seg));
  if (key === "foreign_pct") return ratio(reg(geo, "fgn", "population"), reg(geo, "all", "population"));
  if (key === "aged_pct") return ratio(agedTotal(geo, seg), reg(geo, seg, "population"));
  if (key === "birth_rate") return perThousand(reg(geo, seg, "births"), midyear(geo, seg));
  if (key === "death_rate") return perThousand(reg(geo, seg, "deaths"), midyear(geo, seg));
  return null;
}

function fmtMeasure(v, key) {
  var m = MEASURES[key];
  if (v === null) return MISSING;
  if (m.unit === "persons") return fmtNum(v, 0);
  // A count per thousand carries no percent sign; its unit is in the label.
  if (m.unit === "per 1,000") return fmtNum(v, m.dp);
  return m.diverging ? fmtSigned(v, m.dp, "%") : fmtRate(v, m.dp);
}

/* Which reference date a measure is read on. Stocks and shares of stocks
   are the 1 January count; anything built from a register flow covers the
   calendar year before it. Both the map note and the PNG export read this,
   so an export can never date a number differently from the screen. */
function measurePeriodLabel(key) {
  var m = MEASURES[key];
  if (!m) return "";
  var stockBased = m.unit === "persons" || key === "foreign_pct" || key === "aged_pct";
  return stockBased ? fmtPeriodLong(stockPeriod()) : "calendar " + flowYear();
}

function prefectures() {
  return GEOS.filter(function (g) { return g.code !== NATIONAL; });
}

/* The 1 January the stocks are counted on, and the calendar year the flows
   cover — both stated rather than inferred, because they differ by one. */
function stockPeriod() { return REG.release.latest_period; }
function flowYear() { return Number(stockPeriod().slice(0, 4)) - 1; }

/* ---------- municipalities ---------- */

/* One prefecture's worth at a time. The dataset is 585,000 series — the whole
   country in one payload would be tens of megabytes — so the API insists on a
   prefecture and this keeps what it has already fetched. */
var MUNI = {};          // prefecture code -> payload
var MUNI_PENDING = {};

function loadMunicipalities(prefecture, done) {
  if (MUNI[prefecture]) { done(MUNI[prefecture]); return; }
  if (MUNI_PENDING[prefecture]) { MUNI_PENDING[prefecture].push(done); return; }
  MUNI_PENDING[prefecture] = [done];
  fetch("api/v1/population-jp-municipal/prefectures?prefecture=" + encodeURIComponent(prefecture))
    .then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; })
    .then(function (payload) {
      MUNI[prefecture] = payload || {"unavailable": true};
      var waiting = MUNI_PENDING[prefecture] || [];
      delete MUNI_PENDING[prefecture];
      waiting.forEach(function (fn) { fn(MUNI[prefecture]); });
    });
}

function muniValue(payload, geo, segment, measure) {
  var col = payload.values[geo + "." + segment + "." + measure];
  if (!col) return null;
  for (var i = col.length - 1; i >= 0; i--) if (col[i] !== null) return col[i];
  return null;
}

function muniAged(payload, geo, segment) {
  var bands = (payload.age_groups || {}).aged_65_plus || [];
  var sum = 0;
  for (var i = 0; i < bands.length; i++) {
    var v = muniValue(payload, geo, segment, bands[i] + "_total");
    if (v === null) return null;     // a withheld band would understate the sum
    sum += v;
  }
  return bands.length ? sum : null;
}

var LEVEL_LABEL = { group: "City or district total", municipality: "Municipality",
                    prefecture: "Prefecture", national: "Japan" };

var MUNI_COLS = [
  { key: "name", label: "Municipality", type: "text" },
  { key: "population", label: "Residents", num: true },
  { key: "net_change", label: "Change", num: true },
  { key: "change_pct", label: "Change (%)", num: true },
  { key: "natural_change", label: "Natural", num: true },
  { key: "social_change", label: "Social", num: true },
  { key: "births", label: "Births", num: true },
  { key: "deaths", label: "Deaths", num: true },
  { key: "foreign_pct", label: "Foreign (%)", num: true },
  { key: "aged_pct", label: "Aged 65+ (%)", num: true },
];

function muniRows(payload) {
  var st = urlState();
  var seg = st.segment;
  var wanted = st.muni === "all" ? ["municipality", "group"] : ["municipality"];
  var out = [];
  // Every English name ends ", <prefecture>", which is useful in search and in
  // the API but is the same word on all 194 rows of a table already scoped to
  // one prefecture. Strip that one suffix, and only where it matches.
  var prefRow = (payload.geographies || []).filter(function (g) {
    return g.level === "prefecture";
  })[0];
  var suffix = prefRow && prefRow.name_en ? ", " + prefRow.name_en : null;
  (payload.geographies || []).forEach(function (g) {
    if (wanted.indexOf(g.level) < 0) return;
    var pop = muniValue(payload, g.code, seg, "population");
    var net = muniValue(payload, g.code, seg, "net_change");
    var all = muniValue(payload, g.code, "all", "population");
    var fgn = muniValue(payload, g.code, "fgn", "population");
    var aged = muniAged(payload, g.code, seg);
    // Six municipalities — the disputed Northern Territories — are on the
    // register with no residents at all, so every share of them is undefined
    // rather than zero.
    var opening = (pop !== null && net !== null) ? pop - net : null;
    out.push({
      // The register names every area in Japanese only; name_en is Japan
      // Post's published romanisation, attached by the API. English leads and
      // the filed Japanese sits beside it, as on the prefecture table.
      code: g.code, name: shortName(g.name_en, suffix) || g.name,
      name_ja: g.name_ja || g.name, level: g.level,
      population: pop, net_change: net,
      change_pct: (opening && opening > 0 && net !== null) ? net / opening * 100 : null,
      natural_change: muniValue(payload, g.code, seg, "natural_change"),
      social_change: muniValue(payload, g.code, seg, "social_change"),
      births: muniValue(payload, g.code, seg, "births"),
      deaths: muniValue(payload, g.code, seg, "deaths"),
      foreign_pct: (all && fgn !== null) ? fgn / all * 100 : null,
      aged_pct: (pop && aged !== null) ? aged / pop * 100 : null,
    });
  });
  return out;
}

function shortName(nameEn, suffix) {
  if (!nameEn || !suffix) return nameEn;
  return nameEn.slice(-suffix.length) === suffix
    ? nameEn.slice(0, -suffix.length) : nameEn;
}

function muniCell(row, key) {
  if (key === "name") {
    var aside = [];
    if (row.name_ja && row.name_ja !== row.name) {
      aside.push('<span class="name-part">' + escapeHtml(row.name_ja) + "</span>");
    }
    if (row.level === "group") aside.push("· total of the areas below it");
    return '<td data-sort="' + escapeHtml(row.name) + '">' +
      '<span class="name-part">' + escapeHtml(row.name) + "</span>" +
      (aside.length ? ' <span class="muted">' + aside.join(" ") + "</span>" : "") +
      "</td>";
  }
  var v = row[key];
  if (key === "change_pct") {
    return '<td class="num">' + (v === null ? MISSING : fmtSigned(v, 2, "%")) + "</td>";
  }
  if (key === "foreign_pct") return '<td class="num">' + fmtRate(v, 2) + "</td>";
  if (key === "aged_pct") return '<td class="num">' + fmtRate(v, 1) + "</td>";
  if (key === "net_change" || key === "natural_change" || key === "social_change") {
    return '<td class="num">' + (v === null ? MISSING : fmtSigned(v, 0, "")) + "</td>";
  }
  return '<td class="num">' + fmtNum(v, 0) + "</td>";
}

function renderMunicipalities() {
  var st = urlState();
  var wrap = document.getElementById("muni-table");
  if (!wrap) return;
  var prefName = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  loadMunicipalities(st.pref, function (payload) {
    if (urlState().pref !== st.pref) return;      // the reader moved on
    if (!payload || payload.unavailable) {
      wrap.innerHTML = '<p class="state-empty">Municipality data is not available on ' +
        "this deployment. Everything above is unaffected.</p>";
      document.getElementById("muni-foot").textContent = "";
      return;
    }
    var rows = muniRows(payload);
    var body = rows.map(function (r) {
      return "<tr>" + MUNI_COLS.map(function (c) { return muniCell(r, c.key); }).join("") + "</tr>";
    }).join("");
    wrap.innerHTML = '<table class="data tbl-series" data-filter-placeholder="Filter towns…">' +
      "<thead><tr>" + MUNI_COLS.map(function (c) {
        return '<th scope="col"' + (c.num ? ' class="num"' : "") + ">" +
          escapeHtml(c.label) + "</th>";
      }).join("") + "</tr></thead><tbody>" + body + "</tbody></table>";
    if (typeof enhanceTable === "function") enhanceTable(wrap);

    var leaves = rows.filter(function (r) { return r.level === "municipality"; }).length;
    var groups = rows.length - leaves;
    document.getElementById("muni-note").textContent =
      prefName + " · " + leaves + " municipalities" +
      (groups ? " + " + groups + (groups === 1 ? " total" : " totals") : "");

    var shrinking = rows.filter(function (r) {
      return r.level === "municipality" && r.net_change !== null && r.net_change < 0; }).length;
    document.getElementById("muni-foot").textContent =
      "Residents are counted at 1 January " + stockPeriod().slice(0, 4) +
      "; every flow column covers calendar " + flowYear() + ". " +
      shrinking + " of the " + leaves + " shrank over the year. " +
      (groups
        ? "The " + groups + " rows marked as a total are designated cities and districts, " +
          "which contain the municipalities listed separately — never add them to the rest. "
        : "Designated-city and district totals are hidden; showing them would let a sum " +
          "count the same people twice. ") +
      MISSING + " means no value was published: the ministry withholds counts small enough " +
      "to identify someone, and six municipalities in the disputed Northern Territories are " +
      "on the register with no residents at all, so every share of them is undefined.";

    document.getElementById("muni-calc").innerHTML =
      "<summary>Show calculation</summary><div class=\"calc-body\">" +
      "<code>" + escapeHtml(MEASURES.change_pct.calc) + "</code><br>" +
      "<code>" + escapeHtml(MEASURES.foreign_pct.calc) + "</code><br>" +
      "<code>" + escapeHtml(MEASURES.aged_pct.calc) + "</code><br>" +
      "Municipality names are published in Japanese only. Counts are the ministry's own; " +
      "the shares and the rate are calculated here. Inputs: release “" +
      escapeHtml(payload.release.label) + "” (sha256 " +
      escapeHtml(payload.release.sha256.slice(0, 12)) + "…).</div>";
  });
}

function muniCsvRows() {
  var payload = MUNI[urlState().pref];
  if (!payload || payload.unavailable) return [["no data"]];
  var rows = muniRows(payload);
  var keys = MUNI_COLS.filter(function (c) { return c.key !== "name"; })
    .map(function (c) { return c.key; });
  return [["code", "municipality", "municipality_ja", "level"].concat(keys)].concat(
    rows.map(function (r) {
      return [r.code, r.name, r.name_ja, r.level].concat(
        keys.map(function (k) { return r[k] === null ? "" : r[k]; }));
    }));
}

/* ---------- header, tiles, provenance ---------- */

function renderStale() {
  var el = document.getElementById("stale-banner");
  if (!REG.stale) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="banner" role="alert">This surface is stale: the newest ' +
    "ingested register year is " + fmtPeriodLong(REG.release.latest_period) +
    ", ingested " + fmtStamp(REG.release.ingested_at) +
    ". The ministry publishes each January count in late July — if this " +
    "persists, run the ingestion.</div>";
}

function renderHead() {
  document.getElementById("page-asof").textContent =
    "As of " + fmtPeriodLong(stockPeriod());
  document.getElementById("page-sub").textContent =
    "Everyone on the Basic Resident Register of all 47 prefectures at 1 January " +
    stockPeriod().slice(0, 4) + ", with the register's own account of the " +
    flowYear() + " change, the age and sex of every resident, and fifty years of " +
    "births, deaths and fertility behind it.";
  if (REG.credit_line) {
    document.getElementById("credit-line").textContent = REG.credit_line;
  }
}

/* Direction is carried by the arrow and the sign, never by colour: a falling
   population is not a loss and a rising one is not a gain. The explanation
   goes on the title, because a tile label never wraps — a second line here
   would drop this cell's value off the baseline its neighbours share. */
function tileCell(label, value, delta, deltaUnit, title) {
  var html = MISSING;
  if (delta !== null && delta !== undefined) {
    var dp = deltaUnit === "%" ? 2 : 0;
    var rounded = Number(delta.toFixed(dp));
    var arrow = rounded === 0 ? ""
      : '<span aria-hidden="true">' + (rounded > 0 ? "▲" : "▼") + "</span> " +
        '<span class="visually-hidden">' + (rounded > 0 ? "up " : "down ") + "</span>";
    html = arrow + fmtNum(Math.abs(delta), dp) + (deltaUnit === "%" ? "%" : "");
  }
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(title || label) + '">' +
      escapeHtml(label) + "</div>" +
    '<div class="strip-value num">' + value + "</div>" +
    '<div class="strip-delta num flat">' + html + "</div>" +
    "</div>";
}

function renderTiles() {
  var pop = reg(NATIONAL, "all", "population");
  var net = reg(NATIONAL, "all", "net_change");
  var fgn = reg(NATIONAL, "fgn", "population");
  var fgnNet = reg(NATIONAL, "fgn", "net_change");
  var aged = agedTotal(NATIONAL, "all");
  var natural = reg(NATIONAL, "all", "natural_change");

  // The strip answers the page's question in four numbers: how many people,
  // and why the number moved. Natural and social change are the two halves
  // of that "why", so neither tile repeats the other's rate — an earlier
  // draft showed the same 0.45% twice, which reads as a bug.
  document.getElementById("tiles").innerHTML =
    tileCell("Registered Residents", fmtNum(pop, 0),
      ratio(net, opening(NATIONAL, "all")), "%",
      "Everyone on the Basic Resident Register at 1 January " +
        stockPeriod().slice(0, 4) + ", against the same count a year earlier. " +
        "The change over " + flowYear() + " was " + fmtSigned(net, 0, "") + ".") +
    tileCell("Natural Change " + flowYear(),
      natural === null ? MISSING : fmtSigned(natural, 0, ""),
      ratio(natural, opening(NATIONAL, "all")), "%",
      "Births less deaths over calendar " + flowYear() +
        ", and the same figure as a rate on the opening population") +
    tileCell("Foreign Residents", fmtNum(fgn, 0),
      ratio(fgnNet, fgn === null || fgnNet === null ? null : fgn - fgnNet), "%",
      "Foreign residents at 1 January " + stockPeriod().slice(0, 4) + " — " +
        fmtRate(ratio(fgn, pop), 2) + " of all residents — against a year earlier") +
    tileCell("Aged 65 and Over",
      aged === null ? MISSING : fmtRate(ratio(aged, pop), 1),
      null, "%",
      (aged === null ? "Not available" : fmtNum(aged, 0) + " residents") +
        " — share of everyone on the register at 1 January " +
        stockPeriod().slice(0, 4) + ". No prior year is published on this " +
        "basis yet, so no change is shown.");

  document.getElementById("strip-foot").textContent =
    "Counts are official statistics exactly as published; the rates and the " +
    "65-and-over share are calculated from them. The register lost " +
    fmtNum(Math.abs(net), 0) + " residents over " + flowYear() + " — a natural " +
    "change of " + fmtSigned(natural, 0, "") + " and a social change of " +
    fmtSigned(reg(NATIONAL, "all", "social_change"), 0, "") +
    ", which add to it exactly. " + fmtNum(aged, 0) + " residents are aged 65 " +
    "or over.";

  var calc = document.getElementById("strip-calc");
  calc.style.display = "";
  calc.innerHTML = "<summary>Show calculation</summary>" +
    '<div class="calc-body">' +
      "<code>" + escapeHtml(MEASURES.change_pct.calc) + "</code><br>" +
      "<code>" + escapeHtml(MEASURES.foreign_pct.calc) + "</code><br>" +
      "<code>" + escapeHtml(MEASURES.aged_pct.calc) + "</code><br>" +
      "The register publishes the count at 1 January and the change over the " +
      "year before it, but not the population it started from, so the rate's " +
      "denominator is the published count less the published change. " +
      "Inputs: official counts from release “" + escapeHtml(REG.release.label) +
      "” (sha256 " + REG.release.sha256.slice(0, 12) + "…).</div>";
}

function renderProvenance() {
  var rel = REG.release;
  var hrel = HIST ? HIST.release : null;
  var rows = [
    ["Source", rel.source_name],
    ["Release", rel.label],
    ["Reference date", fmtPeriodLong(rel.latest_period) + " (stocks); calendar " +
      flowYear() + " (flows)"],
    ["Ingested", fmtStamp(rel.ingested_at)],
    ["Artifact sha256", rel.sha256.slice(0, 24) + "…"],
  ];
  if (hrel) {
    rows.push(["Long-run source", hrel.source_name]);
    rows.push(["Long-run release", hrel.label + " · sha256 " + hrel.sha256.slice(0, 12) + "…"]);
  }
  document.getElementById("prov-card").innerHTML =
    '<div class="prov-card">' +
      '<div class="prov-card-head">' +
        '<div class="prov-card-title">Data Source</div>' +
        '<div class="prov-card-id">' + escapeHtml(rel.source_id) + "</div>" +
      "</div>" +
      rows.map(function (r) {
        return '<div class="prov-row"><span class="prov-label">' + escapeHtml(r[0]) +
          '</span><span class="prov-value">' + escapeHtml(String(r[1])) + "</span></div>";
      }).join("") +
      '<p class="prov-sub">' + trustBadge("official") +
      " Counts of residents are the ministry's own, stored and served unchanged. " +
      "Shares and rates on this page are calculated from those counts and show " +
      "their formula. The ministry keeps only the current year on its site, so " +
      "the archive of past years here is the history." +
      "</p>" +
    "</div>";
}

/* ---------- map ---------- */

function mapPalette(diverging, pal) {
  return diverging
    ? [pal.divergeNeg, pal.divergeNegSoft, pal.divergeMid, pal.divergePosSoft, pal.divergePos]
    : [pal.divergeMid, pal.divergeNegSoft, pal.divergeNeg];
}

function readMapPalette() {
  return {
    ink: cssVar("--obs-ink"), muted: cssVar("--obs-text-muted"),
    border: cssVar("--obs-border"), surface: cssVar("--obs-surface"),
    subtle: cssVar("--obs-surface-subtle"),
    divergeNeg: cssVar("--obs-diverge-neg"),
    divergeNegSoft: cssVar("--obs-diverge-neg-soft"),
    divergeMid: cssVar("--obs-diverge-mid"),
    divergePosSoft: cssVar("--obs-diverge-pos-soft"),
    divergePos: cssVar("--obs-diverge-pos"),
  };
}

function mapRows() {
  var st = urlState();
  return prefectures().map(function (g) {
    return { code: g.code, name: g.name_ja, en: g.name_en,
             value: measureValue(g.code, st.measure, "all") };
  });
}

function mapOption(pal) {
  var st = urlState();
  var m = MEASURES[st.measure];
  var rows = mapRows();
  var present = rows.filter(function (r) { return r.value !== null; })
                    .map(function (r) { return r.value; });
  var lo = Math.min.apply(null, present);
  var hi = Math.max.apply(null, present);
  // A diverging measure is read against zero, so the scale must be
  // symmetric — otherwise the same rate reads as a different colour on
  // either side of it and the midpoint stops meaning "no change".
  if (m.diverging) {
    var span = Math.max(Math.abs(lo), Math.abs(hi));
    lo = -span; hi = span;
  }
  // A prefecture with no published value must not be plotted as zero:
  // ECharts treats null as out-of-range and paints it the empty colour.
  var data = rows.map(function (r) {
    return { name: r.name, value: r.value === null ? null : r.value,
             code: r.code, en: r.en };
  });
  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item", confine: true,
      backgroundColor: pal.surface, borderColor: pal.border,
      textStyle: { color: pal.ink, fontSize: 12 },
      formatter: function (q) {
        if (!q.data) return escapeHtml(q.name);
        var g = q.data.code;
        return '<div style="font-weight:600;margin-bottom:2px">' +
          escapeHtml(q.data.en) + " · " + escapeHtml(q.name) + "</div>" +
          escapeHtml(m.label) + ": " + fmtMeasure(q.data.value, st.measure) +
          '<div style="margin-top:4px;font-size:11px;color:' + pal.muted + '">' +
          "Residents " + fmtNum(reg(g, "all", "population"), 0) + " · " +
          "Foreign " + fmtNum(reg(g, "fgn", "population"), 0) + "</div>";
      },
    },
    visualMap: {
      type: "continuous", min: lo, max: hi,
      calculable: false, orient: "horizontal", left: "center", bottom: 4,
      itemWidth: 12, itemHeight: 120,
      text: [fmtMeasure(hi, st.measure), fmtMeasure(lo, st.measure)],
      textStyle: { color: pal.muted, fontSize: 11 },
      seriesIndex: [0, 1],
      inRange: { color: mapPalette(m.diverging, pal) },
      outOfRange: { color: pal.subtle },
    },
    series: [{
      type: "map", map: "japan", roam: false, aspectScale: 0.88,
      // Japan runs south-west to north-east, and the Ryukyu chain out to
      // Okinawa stretches that diagonal far enough that one frame leaves
      // half the canvas empty and the main islands too small to read. The
      // four main islands take this frame; Okinawa is drawn beside it at
      // the same scale of colour, the way a printed Japanese statistical
      // map insets it. Cropping it out entirely was not an option — it is
      // one of the 47 and one of the more interesting ones.
      // Every one of the 47 is inside this box — Yonaguni in the west,
      // Okinawa in the south, Hokkaido in the north. Only the uninhabited
      // Pacific outliers are cropped, and they were setting a bounding box
      // half again as wide as the country. Cropping a *prefecture* to tidy
      // the shape was never an option.
      boundingCoords: [[122.7, 45.9], [146.5, 23.8]],
      left: 8, right: 8, top: 8, bottom: 34,
      nameProperty: "name",
      itemStyle: { borderColor: pal.border, borderWidth: 0.6, areaColor: pal.subtle },
      emphasis: { label: { show: false }, itemStyle: { borderColor: pal.ink, borderWidth: 1.2 } },
      select: { label: { show: false }, itemStyle: { borderColor: pal.ink, borderWidth: 1.6 } },
      selectedMode: "single",
      label: { show: false },
      data: data,
    }],
  };
}

function drawMap() {
  if (!mapReady || !REG) return;
  var el = document.getElementById("pop-map");
  var pal = readMapPalette();
  if (!mapChart) {
    el.innerHTML = "";
    mapChart = echarts.init(el, null, { renderer: "canvas" });
    mapChart.on("click", function (q) {
      if (q.data && q.data.code) selectPrefecture(q.data.code, "hist-chart");
    });
  }
  mapChart.setOption(mapOption(pal), true);

  var st = urlState();
  var m = MEASURES[st.measure];
  document.getElementById("map-note").textContent =
    m.label + " · " + measurePeriodLabel(st.measure);
  document.getElementById("map-source").innerHTML =
    trustBadge("official") + " " + escapeHtml(REG.credit_line || "") +
    " Release “" + escapeHtml(REG.release.label) + "”, ingested " +
    fmtStamp(REG.release.ingested_at) + ". " + escapeHtml(m.help) + ".";
  document.getElementById("map-calc").innerHTML = m.calc
    ? "<summary>Show calculation</summary><div class=\"calc-body\"><code>" +
      escapeHtml(m.calc) + "</code><br>Computed on this page from the published " +
      "counts; the counts themselves are unchanged.</div>"
    : "<summary>Show calculation</summary><div class=\"calc-body\">" +
      "Registered residents are shown exactly as published — nothing is computed.</div>";
  renderExtremes();
}

function extremeRow(r, key, current) {
  return '<div class="pop-ex-row" role="button" tabindex="0" data-code="' + r.code +
    '"' + (r.code === current ? ' aria-current="true"' : "") + '>' +
    "<span>" + escapeHtml(r.en) + "</span>" +
    '<span class="v">' + fmtMeasure(r.value, key) + "</span></div>";
}

function renderExtremes() {
  var st = urlState();
  var rows = mapRows().filter(function (r) { return r.value !== null; })
    .sort(function (a, b) { return b.value - a.value; });
  var m = MEASURES[st.measure];
  var el = document.getElementById("map-extremes");
  var top = rows.slice(0, 10);
  var bottom = rows.slice(-10).reverse();
  el.innerHTML =
    '<div class="pop-ex-cols">' +
      "<div><h3>Highest</h3>" +
        top.map(function (r) { return extremeRow(r, st.measure, st.pref); }).join("") + "</div>" +
      "<div><h3>Lowest</h3>" +
        bottom.map(function (r) { return extremeRow(r, st.measure, st.pref); }).join("") + "</div>" +
    "</div>" +
    '<p class="pop-ex-note">' + escapeHtml(m.help) +
    ". Click any prefecture, here or on the map, to put it in the chart below.</p>";
  Array.prototype.forEach.call(el.querySelectorAll(".pop-ex-row"), function (row) {
    row.addEventListener("click", function () {
      selectPrefecture(row.getAttribute("data-code"), "hist-chart");
    });
    row.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectPrefecture(row.getAttribute("data-code"), "hist-chart");
      }
    });
  });
}

function mapCsvRows() {
  var st = urlState();
  var m = MEASURES[st.measure];
  return [["prefecture_code", "prefecture", "prefecture_ja", m.unit === "persons"
    ? "registered_residents" : st.measure]].concat(
    mapRows().map(function (r) {
      return [r.code, r.en, r.name, r.value === null ? "" : r.value];
    }));
}

/* ---------- population pyramid ---------- */

/* The register's five-year age bands by sex, for one area and one segment.
   Rows run youngest first, which is bottom-first on the chart's category
   axis. A band with no published value stays null: a pyramid drawn with a
   missing band as zero would show a notch that is not there. */
function pyramidRows(geo, segment) {
  var bands = REG.age_bands || [];
  return bands.map(function (b) {
    return {
      code: b.code,
      label: b.label,
      label_ja: b.label_ja,
      male: reg(geo, segment, b.code + "_male"),
      female: reg(geo, segment, b.code + "_female"),
      total: reg(geo, segment, b.code + "_total"),
    };
  });
}

/* The denominator every share on this chart is taken over: the residents
   who have a recorded age and sex, which is the sum of the bands, not the
   published headcount. The two differ by a few dozen people nationally and
   the difference is disclosed under the chart rather than hidden by
   dividing by a total the bars do not add up to. */
function pyramidBase(rows) {
  var sum = 0, seen = false;
  rows.forEach(function (r) {
    if (r.male !== null) { sum += r.male; seen = true; }
    if (r.female !== null) { sum += r.female; seen = true; }
  });
  return seen ? sum : null;
}

function pyramidValue(v, base, mode) {
  if (v === null) return null;
  if (mode !== "share") return v;
  return base ? v / base * 100 : null;
}

/* The next round number at or above a value: 4.3 -> 4.5, 581,204 -> 600,000.
   Half a power of ten is fine enough that the axis does not gain a lot of
   empty space, and coarse enough that the ticks stay round. */
function niceCeil(v) {
  if (!v || !isFinite(v) || v <= 0) return 0;
  var step = Math.pow(10, Math.floor(Math.log(v) / Math.LN10)) / 2;
  return Math.ceil(v / step) * step;
}

function pyramidOption(pal) {
  var st = urlState();
  var geo = st.pref, segment = st.segment, mode = st.pyr;
  var rows = pyramidRows(geo, segment);
  var base = pyramidBase(rows);
  var natRows = pyramidRows(NATIONAL, segment);
  var natBase = pyramidBase(natRows);
  var name = (GEO_BY_CODE[geo] || {}).name_en || geo;
  var share = mode === "share";
  var dp = share ? 2 : 0;
  // A pyramid is read against its centre line, so the axis has to be
  // symmetric. Left to itself ECharts picks a nice interval from the actual
  // minimum and maximum, and with a longest bar of −4.3 against +3.9 it
  // returns −6 to +4: the zero line sits off-centre and the two sexes look
  // like they are drawn to different scales. So the extent is set here, from
  // the largest bar on either side — including Japan's outline, which can be
  // wider than the prefecture's own bars.
  var extent = 0;
  var widest = function (r, key, denominator, asMode) {
    var v = pyramidValue(r[key], denominator, asMode);
    if (v !== null && Math.abs(v) > extent) extent = Math.abs(v);
  };
  rows.forEach(function (r) {
    widest(r, "male", base, mode); widest(r, "female", base, mode);
  });

  // Axis ticks read at one decimal; the tooltip keeps the second.
  var fmtAxis = function (v) {
    return v === null || v === undefined ? MISSING
      : (share ? fmtRate(Math.abs(v), 1) : fmtNum(Math.abs(v), 0));
  };

  var series = [
    {
      name: "Male", type: "bar", stack: "sex", barWidth: "86%",
      itemStyle: { color: pal.series[0] },
      data: rows.map(function (r) {
        var v = pyramidValue(r.male, base, mode);
        return v === null ? null : -v;
      }),
    },
    {
      name: "Female", type: "bar", stack: "sex", barWidth: "86%",
      itemStyle: { color: pal.series[1] },
      data: rows.map(function (r) { return pyramidValue(r.female, base, mode); }),
    },
  ];
  // Counts of two areas cannot share an axis; shares can. So the national
  // outline is offered only in share mode, where it is the whole point.
  var overlay = share && geo !== NATIONAL && natBase;
  if (overlay) {
    ["male", "female"].forEach(function (sex, i) {
      series.push({
        name: "Japan", type: "line", step: "middle", symbol: "none",
        silent: true, z: 3,
        lineStyle: { color: pal.muted, width: 1.2, type: "dashed" },
        showInLegend: i === 0,
        data: natRows.map(function (r) {
          var v = pyramidValue(r[sex], natBase, "share");
          return v === null ? null : (sex === "male" ? -v : v);
        }),
      });
    });
  }

  if (overlay) {
    natRows.forEach(function (r) {
      widest(r, "male", natBase, "share"); widest(r, "female", natBase, "share");
    });
  }
  var axisExtent = niceCeil(extent * 1.02) || 1;
  var plot = document.getElementById("pop-pyramid");
  var narrowPlot = plot ? plot.clientWidth < 560 : false;

  return {
    animation: false,
    backgroundColor: "transparent",
    legend: {
      show: true, top: 0, right: 0, itemWidth: 16, itemHeight: 8,
      data: [{ name: "Male" }, { name: "Female" }].concat(
        overlay ? [{ name: "Japan" }] : []),
      textStyle: { color: pal.muted, fontSize: 12 },
    },
    // The outermost x label is a six-digit count; 12px of gutter clipped it.
    grid: { left: 8, right: 28, top: 30, bottom: 34, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" }, confine: true,
      backgroundColor: pal.surface, borderColor: pal.border,
      textStyle: { color: pal.ink, fontSize: 12 },
      formatter: function (items) {
        if (!items.length) return "";
        var i = items[0].dataIndex;
        var r = rows[i];
        var head = '<div style="font-weight:600;margin-bottom:2px">' +
          escapeHtml(r.label) + "</div>";
        var line = function (label, v) {
          return escapeHtml(label) + ": " + (v === null ? MISSING :
            (share ? fmtRate(v / (base || 1) * 100, 2) + " · " + fmtNum(v, 0)
                   : fmtNum(v, 0))) + "<br>";
        };
        var nat = "";
        if (overlay && natRows[i]) {
          var nr = natRows[i];
          var natShare = nr.total === null ? null : nr.total / natBase * 100;
          nat = '<div style="margin-top:4px;font-size:11px;color:' + pal.muted +
            '">Japan: ' + (natShare === null ? MISSING : fmtRate(natShare, 2)) +
            " of all residents</div>";
        }
        return head + line("Male", r.male) + line("Female", r.female) +
          line("Total", r.total) + nat;
      },
    },
    xAxis: {
      type: "value",
      min: -axisExtent, max: axisExtent,
      // Six-digit counts on a phone-width plot run into each other, so the
      // tick count is cut rather than the labels shortened: "600,000" is a
      // number a reader can use and "600k" is not.
      splitNumber: narrowPlot ? 2 : 6,
      name: share ? "% of residents" : "residents",
      nameLocation: "middle", nameGap: 26,
      nameTextStyle: { color: pal.muted, fontSize: 11 },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: pal.muted, fontSize: 11,
                   formatter: function (v) { return fmtAxis(v); } },
      splitLine: { lineStyle: { color: pal.grid } },
    },
    yAxis: {
      type: "category",
      data: rows.map(function (r) { return r.label.replace("Age ", ""); }),
      axisLine: { lineStyle: { color: pal.border } },
      axisTick: { show: false },
      axisLabel: { color: pal.muted, fontSize: 11, interval: 0 },
    },
    series: series,
  };
}

function pyramidPalette() {
  var pal = readMapPalette();
  pal.grid = cssVar("--obs-grid");
  pal.series = [1, 2, 3, 4, 5, 6].map(function (i) {
    return cssVar("--obs-series-" + i);
  });
  return pal;
}

function pyramidStats() {
  var st = urlState();
  var rows = pyramidRows(st.pref, st.segment);
  var base = pyramidBase(rows);
  var groups = REG.age_groups || {};
  var byCode = {};
  rows.forEach(function (r) { byCode[r.code] = r; });
  // A partial sum would understate the group, so one missing band makes the
  // whole group missing.
  var sumOf = function (codes, sex) {
    var sum = 0;
    if (!codes || !codes.length) return null;
    for (var i = 0; i < codes.length; i++) {
      var r = byCode[codes[i]];
      var v = r ? r[sex] : null;
      if (v === null || v === undefined) return null;
      sum += v;
    }
    return sum;
  };
  var young = sumOf(groups.under_15, "total");
  var working = sumOf(groups.working_15_64, "total");
  var old = sumOf(groups.aged_65_plus, "total");
  var males = sumOf(rows.map(function (r) { return r.code; }), "male");
  var females = sumOf(rows.map(function (r) { return r.code; }), "female");
  var largest = null;
  rows.forEach(function (r) {
    if (r.total === null) return;
    if (largest === null || r.total > largest.total) largest = r;
  });
  return {
    rows: rows, base: base, young: young, working: working, aged: old,
    males: males, females: females, largest: largest,
    headcount: reg(st.pref, st.segment, "population"),
  };
}

function renderPyramid() {
  if (!REG) return;
  var st = urlState();
  var el = document.getElementById("pop-pyramid");
  if (!el) return;
  var name = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  var stats = pyramidStats();
  if (stats.base === null) {
    el.innerHTML = '<p class="state-empty">No age bands are published for ' +
      escapeHtml(name) + " on this segment.</p>";
    if (pyramidChart) pyramidChart.dispose();
    pyramidChart = null;
  } else {
    if (!pyramidChart) {
      el.innerHTML = "";
      pyramidChart = echarts.init(el, null, { renderer: "canvas" });
    }
    pyramidChart.setOption(pyramidOption(pyramidPalette()), true);
  }

  document.getElementById("pyr-note").textContent =
    name + " · " + SEGMENT_LABEL[st.segment] + " · " +
    fmtPeriodLong(stockPeriod());

  var pct = function (v) { return ratio(v, stats.base); };
  var rows = [
    ["Under 15", stats.young, pct(stats.young)],
    ["15 to 64", stats.working, pct(stats.working)],
    ["65 and over", stats.aged, pct(stats.aged)],
  ];
  var dependency = (stats.aged !== null && stats.working)
    ? stats.aged / stats.working * 100 : null;
  var sexRatio = (stats.males !== null && stats.females)
    ? stats.males / stats.females * 100 : null;
  document.getElementById("pyr-stats").innerHTML =
    "<h3>Age Structure</h3>" +
    rows.map(function (r) {
      return '<div class="pop-ex-row" style="cursor:default">' +
        "<span>" + escapeHtml(r[0]) + "</span>" +
        '<span class="v">' + (r[1] === null ? MISSING : fmtNum(r[1], 0)) +
        '<span class="muted"> · ' + fmtRate(r[2], 1) + "</span></span></div>";
    }).join("") +
    "<h3>Ratios</h3>" +
    '<div class="pop-ex-row" style="cursor:default"><span>Aged 65+ per 100 aged 15–64</span>' +
      '<span class="v">' + (dependency === null ? MISSING : fmtNum(dependency, 1)) +
      "</span></div>" +
    '<div class="pop-ex-row" style="cursor:default"><span>Males per 100 females</span>' +
      '<span class="v">' + (sexRatio === null ? MISSING : fmtNum(sexRatio, 1)) +
      "</span></div>" +
    '<div class="pop-ex-row" style="cursor:default"><span>Largest band</span>' +
      '<span class="v">' + (stats.largest ? escapeHtml(stats.largest.label.replace("Age ", "")) +
        '<span class="muted"> · ' + fmtNum(stats.largest.total, 0) + "</span>" : MISSING) +
      "</span></div>" +
    '<p class="pop-ex-note">Every share here is taken over the ' +
      fmtNum(stats.base, 0) + " residents who have a recorded age and sex" +
      (stats.headcount !== null && stats.base !== null && stats.headcount !== stats.base
        ? ", " + fmtNum(stats.headcount - stats.base, 0) + " fewer than the " +
          "published headcount of " + fmtNum(stats.headcount, 0) +
          ". Those residents stay in the total rather than being spread across the bands."
        : ".") +
      "</p>";

  document.getElementById("pyr-source").innerHTML =
    trustBadge("official") + " " + escapeHtml(REG.credit_line || "") +
    " Bars are published counts. Release “" + escapeHtml(REG.release.label) +
    "”, ingested " + fmtStamp(REG.release.ingested_at) + ".";

  document.getElementById("pyr-calc").innerHTML =
    "<summary>Show calculation</summary><div class=\"calc-body\">" +
    (st.pyr === "share"
      ? "<code>share % = residents in the band and sex ÷ residents with a " +
        "recorded age and sex × 100</code><br>"
      : "The bars are published counts; nothing is computed.<br>") +
    "<code>aged 65+ per 100 aged 15–64 = residents 65 and over ÷ residents " +
    "15 to 64 × 100</code><br>" +
    "<code>males per 100 females = male residents ÷ female residents × 100</code><br>" +
    (st.pyr === "share"
      ? "Japan's profile is drawn as a dashed outline for comparison. Two areas " +
        "cannot share a count axis, so the outline appears only on shares.<br>"
      : "Switch to Share of Residents to compare this profile with Japan's.<br>") +
    "The register publishes five-year bands, so the chart has the shape the " +
    "source has and no finer. Only one January is published on this basis so " +
    "far, so there is no earlier profile to draw behind it yet." +
    "</div>";
}

function pyramidCsvRows() {
  var st = urlState();
  var rows = pyramidRows(st.pref, st.segment);
  var base = pyramidBase(rows);
  var name = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  return [["prefecture_code", "prefecture", "segment", "age_band", "age_band_ja",
           "male", "female", "total", "share_pct"]].concat(
    rows.map(function (r) {
      return [st.pref, name, st.segment, r.label, r.label_ja,
              r.male === null ? "" : r.male,
              r.female === null ? "" : r.female,
              r.total === null ? "" : r.total,
              r.total === null || !base ? "" : r.total / base * 100];
    }));
}

/* Like the map, the pyramid is not an obsChart, so it exports itself —
   light theme, title and source burnt in. */
function renderPyramidPng() {
  if (!pyramidChart) return;
  var root = document.documentElement;
  var prev = root.getAttribute("data-theme");
  root.setAttribute("data-theme", "light");
  var st = urlState();
  var name = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  var off = document.createElement("div");
  off.style.cssText = "position:absolute;left:-10000px;width:900px;height:720px";
  document.body.appendChild(off);
  var shot = echarts.init(off, null, { renderer: "canvas" });
  var option = pyramidOption(pyramidPalette());
  option.title = {
    text: name + " — residents by age and sex",
    subtext: SEGMENT_LABEL[st.segment] + " · " + fmtPeriodLong(stockPeriod()) +
      " · " + (REG.credit_line || "") +
      (st.pyr === "share"
        ? "\nShare of residents with a recorded age and sex"
        : ""),
    left: 12, top: 10,
    textStyle: { color: cssVar("--obs-ink"), fontSize: 15, fontWeight: 600 },
    subtextStyle: { color: cssVar("--obs-text-muted"), fontSize: 11, lineHeight: 15 },
  };
  option.grid.top = 76;
  option.legend.top = 58;
  option.backgroundColor = cssVar("--obs-surface");
  shot.setOption(option);
  var url = shot.getDataURL({ pixelRatio: 2, backgroundColor: cssVar("--obs-surface") });
  shot.dispose();
  document.body.removeChild(off);
  if (prev === null) root.removeAttribute("data-theme"); else root.setAttribute("data-theme", prev);
  return url;
}

function exportPyramidMenu() {
  if (!pyramidChart) return;
  var st = urlState();
  var name = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  obsExportMenu("japan-" + name.toLowerCase() + "-population-pyramid.png",
    renderPyramidPng, document.getElementById("pyr-png"),
    { w: 900, h: 720, out: 1800 });
}

/* ---------- history chart ---------- */

var HIST_SERIES = [
  { ind: "A2301", name: "Registered, total", slot: 1 },
  { ind: "A2101", name: "Registered, Japanese", slot: 3 },
  { ind: "A2201", name: "Registered, foreign", slot: 2 },
  { ind: "A1101", name: "Total population, 1 Oct", slot: 5 },
];

var AGE_SERIES = [
  { ind: "A1301", name: "Under 15", slot: 3 },
  { ind: "A1302", name: "15 to 64", slot: 1 },
  { ind: "A1303", name: "65 and over", slot: 2 },
];

/* Crude rates from the long-run table. Births and deaths are calendar-year
   flows dated 1 January of the year they cover; the population they are
   divided by is the 1 October estimate for that same year, which is the
   mid-year figure the vital statistics themselves use. The two series are
   therefore joined on the YEAR, never on the period — pairing them by date
   would divide a flow by a population measured nine months later. The
   denominator is the Japanese population, because these births and deaths
   are the vital statistics' count of Japanese nationals. */
var CRUDE_SERIES = [
  { flow: "A4101", name: "Births per 1,000", slot: 1 },
  { flow: "A4200", name: "Deaths per 1,000", slot: 2 },
];
var CRUDE_DENOMINATOR = "A1102";

function crudeRate(geo, flowIndicator) {
  var pop = {};
  hist(geo, CRUDE_DENOMINATOR).forEach(function (p) { pop[p[0].slice(0, 4)] = p[1]; });
  var out = [];
  hist(geo, flowIndicator).forEach(function (p) {
    var denominator = pop[p[0].slice(0, 4)];
    // No population for that year is a gap, never a zero and never a guess.
    if (denominator) out.push([p[0], p[1] / denominator * 1000]);
  });
  return out;
}

function histConfig() {
  var st = urlState();
  var w = histWindow();
  var geo = st.pref;
  var name = (GEO_BY_CODE[geo] || {}).name_en || geo;
  var defs = st.hist === "age" ? AGE_SERIES : HIST_SERIES;
  var inWindow = function (p) {
    var y = p[0].slice(0, 4);
    return (!w.from || y >= w.from) && (!w.to || y <= w.to);
  };

  // The two rate views are built from different inputs and are not counts,
  // so they are assembled here rather than run through the count pipeline.
  if (st.hist === "vital" || st.hist === "fertility") {
    return rateConfig(st, w, geo, name, inWindow);
  }

  var series = defs.map(function (d) {
    var pts = hist(geo, d.ind).filter(inWindow);
    if (st.hist === "index") {
      // Indexed to the first year in view, not to a fixed 1975: with a custom
      // window a fixed base would put the whole chart off the top or bottom.
      var base = null;
      for (var i = 0; i < pts.length; i++) { if (pts[i][1] !== null) { base = pts[i][1]; break; } }
      pts = pts.map(function (p) { return [p[0], base ? p[1] / base * 100 : null]; });
    }
    return { name: d.name, slot: d.slot, points: pts };
  }).filter(function (s) { return s.points.length; });

  // A log axis cannot show a zero or a negative. These are counts of people so
  // it never arises, but a silently broken axis is worse than a linear one.
  var positive = series.every(function (s) {
    return s.points.every(function (p) { return p[1] === null || p[1] > 0; });
  });
  var log = st.scale === "log" && positive;

  return {
    series: series,
    unit: st.hist === "index" ? "index" : "persons",
    dp: st.hist === "index" ? 1 : 0,
    logScale: log,
    yAxisName: (st.hist === "index" ? indexBaseLabel(series) : "persons") +
               (log ? " · log scale" : ""),
    trust: "official",
    sourceLine: (HIST ? HIST.credit_line : "") + " " + name + ".",
    title: name,
    window: w,
    logAvailable: positive,
  };
}

/* The two views that show a rate rather than a count.

   `vital` is ours: births and deaths per thousand people, computed here from
   published counts, so it carries its formula and no badge. `fertility` is
   the Bureau's own total fertility rate, stored exactly as published because
   its denominator — women by single year of age — is in no table we hold, so
   it carries the official badge instead. Never mixed on one axis: one is per
   thousand people, the other is children per woman. */
/* Annual points with every year between the first and the last present,
   missing ones as null. A rate series can skip years — prefecture fertility
   is published for 1980, 1985 and then annually — and on a time axis a
   straight line across the gap would invent four readings. Nothing is added
   before the first or after the last point: an absent tail is not a gap. */
function padYears(points) {
  if (points.length < 2) return points;
  var byYear = {};
  points.forEach(function (p) { byYear[Number(p[0].slice(0, 4))] = p; });
  var years = Object.keys(byYear).map(Number).sort(function (a, b) { return a - b; });
  var out = [];
  for (var y = years[0]; y <= years[years.length - 1]; y++) {
    out.push(byYear[y] || [y + "-01-01", null]);
  }
  return out;
}

function rateConfig(st, w, geo, name, inWindow) {
  var series, unit, dp, yAxisName, trust, refLine, yMax;
  if (st.hist === "vital") {
    series = CRUDE_SERIES.map(function (d) {
      return { name: d.name, slot: d.slot,
               points: padYears(crudeRate(geo, d.flow).filter(inWindow)) };
    });
    unit = "per 1,000";
    dp = 1;
    yAxisName = "per 1,000 people";
    trust = "derived";
  } else {
    series = [{ name: name, slot: 1,
                points: padYears(hist(geo, "A4103").filter(inWindow)) }];
    // A prefecture is read against the country, which is the comparison a
    // reader makes anyway. The national line is dropped when the national
    // row is what is already being shown.
    if (geo !== NATIONAL) {
      series.push({ name: "Japan", slot: 5,
                    points: padYears(hist(NATIONAL, "A4103").filter(inWindow)) });
    }
    unit = "births per woman";
    dp = 2;
    yAxisName = "births per woman";
    trust = "official";
    refLine = { y: 2.07, label: "2.07 — replacement" };
    // Without this the reference line sits above the top of the scale and
    // is simply not drawn: no Japanese series has reached it since 1974.
    yMax = 2.2;
  }
  series = series.filter(function (x) { return x.points.length; });
  return {
    series: series,
    unit: unit,
    dp: dp,
    unitSuffix: st.hist === "vital" ? "per 1,000" : "per woman",
    yAxisDp: dp,
    refLine: refLine,
    yMax: yMax,
    showPoints: true,
    logScale: false,
    logAvailable: false,      // a rate on a log axis answers no question here
    yAxisName: yAxisName,
    trust: trust,
    sourceLine: (HIST ? HIST.credit_line : "") + " " + name + ".",
    title: name,
    window: w,
  };
}

/* "1990 = 100" — the base is whatever the window starts at. */
function indexBaseLabel(series) {
  var first = null;
  series.forEach(function (s) {
    if (s.points.length && (first === null || s.points[0][0] < first)) first = s.points[0][0];
  });
  return first ? first.slice(0, 4) + " = 100" : "index";
}

/* Presets plus the two year pickers, in the platform's range-row pattern. */
function renderRange() {
  var el = document.getElementById("hist-range");
  if (!el || !HIST) return;
  var w = histWindow();
  if (!w.years) { el.innerHTML = ""; return; }
  var st = urlState();
  var lastYear = Number(w.last);
  var presets = [
    { label: "Max", from: w.first, to: w.last },
    { label: "30Y", from: String(Math.max(Number(w.first), lastYear - 29)), to: w.last },
    { label: "10Y", from: String(Math.max(Number(w.first), lastYear - 9)), to: w.last },
  ];
  var options = function (selected, lo, hi) {
    return w.years.filter(function (y) { return y >= lo && y <= hi; })
      .map(function (y) {
        return '<option value="' + y + '"' + (y === selected ? " selected" : "") +
          ">" + y + "</option>";
      }).join("");
  };
  var shown = w.years.filter(function (y) { return y >= w.from && y <= w.to; }).length;

  el.innerHTML =
    '<span class="seg" role="group" aria-label="Year range" id="range-seg">' +
      presets.map(function (p) {
        var on = p.from === w.from && p.to === w.to;
        return '<button type="button" data-from="' + p.from + '" data-to="' + p.to +
          '" aria-pressed="' + (on ? "true" : "false") + '">' + p.label + "</button>";
      }).join("") + "</span>" +
    '<span class="range-pair"><label for="range-from">From</label>' +
      '<select id="range-from" class="num">' + options(w.from, w.first, w.to) + "</select></span>" +
    '<span class="range-pair"><label for="range-to">To</label>' +
      '<select id="range-to" class="num">' + options(w.to, w.from, w.last) + "</select></span>" +
    '<span class="seg" role="group" aria-label="Axis scale" id="scale-seg">' +
      '<button type="button" data-scale="linear" aria-pressed="' +
        (st.scale === "linear" ? "true" : "false") + '">Linear</button>' +
      '<button type="button" data-scale="log" aria-pressed="' +
        (st.scale === "log" ? "true" : "false") + '">Log</button>' +
    "</span>" +
    '<span class="range-note num">' + shown + " of " + w.years.length +
      " years · series covers " + w.first + " to " + w.last + "</span>";

  Array.prototype.forEach.call(el.querySelectorAll("#range-seg button"), function (b) {
    b.addEventListener("click", function () {
      setUrlState({ from: b.getAttribute("data-from"), to: b.getAttribute("data-to") });
      renderHistory();
    });
  });
  ["from", "to"].forEach(function (end) {
    document.getElementById("range-" + end).addEventListener("change", function (e) {
      var next = {};
      next[end] = e.target.value;
      setUrlState(next);
      renderHistory();
    });
  });
  Array.prototype.forEach.call(el.querySelectorAll("#scale-seg button"), function (b) {
    b.addEventListener("click", function () {
      setUrlState({ scale: b.getAttribute("data-scale") });
      renderHistory();
    });
  });
}

var HIST_SUB = {
  level: "The long run behind the latest year, from the Statistics Bureau's regional " +
    "series. Registered residents are counted at 1 January and are split into " +
    "Japanese and foreign residents only from 2013, when foreign residents joined " +
    "the register; the total population line is the census and population estimate, " +
    "measured at 1 October on a different basis, so the two are close but not the " +
    "same number and neither is read off the other.",
  age: "Under 15, working age and 65 and over, from the census and the population " +
    "estimates, measured at 1 October. These three add to the total population, " +
    "not to the register count, and the year the 65-and-over line crosses the " +
    "under-15 line is the year the prefecture's age structure inverted.",
  vital: "Births and deaths over each calendar year, per thousand people. Both are " +
    "calculated here from published counts and the 1 October population of the same " +
    "year. The year the death line crosses above the birth line is the year the " +
    "prefecture began shrinking on its own, before anyone moved in or out.",
  fertility: "The total fertility rate: the number of children a woman would have " +
    "if she lived through the birth rates recorded in that single year. It is the " +
    "Bureau's own figure, not calculated here. It reads a year rather than a " +
    "generation, so it moves when births are postponed as well as when families get " +
    "smaller, and the vital statistics behind it run about two years behind the " +
    "register counts above.",
};

function renderHistory() {
  var st = urlState();
  var el = document.getElementById("hist-chart");
  var cfg = histConfig();
  if (!cfg.series.length) {
    el.innerHTML = '<p class="state-empty">No long-run series published for this prefecture.</p>';
    histChart = null;
    return;
  }
  el.innerHTML = "";
  if (!histChart) histChart = obsChart(el, "line", cfg);
  histChart.render(cfg);

  renderRange();
  var name = (GEO_BY_CODE[st.pref] || {}).name_en || st.pref;
  var sub = document.getElementById("hist-sub");
  if (sub) sub.textContent = HIST_SUB[st.hist] || HIST_SUB.level;
  var w = cfg.window;
  var viewNote = { age: "age structure", index: "indexed",
                   vital: "births and deaths per 1,000",
                   fertility: "births per woman", level: "residents" };
  // A rate view can end years before the register does, so the note says the
  // window actually drawn, not the window the reader asked for.
  var drawn = { from: null, to: null };
  cfg.series.forEach(function (x) {
    x.points.forEach(function (pt) {
      if (pt[1] === null) return;
      var y = pt[0].slice(0, 4);
      if (drawn.from === null || y < drawn.from) drawn.from = y;
      if (drawn.to === null || y > drawn.to) drawn.to = y;
    });
  });
  document.getElementById("hist-note").textContent =
    name + " · " + (viewNote[st.hist] || viewNote.level) +
    " · " + (drawn.from || w.from) + "–" + (drawn.to || w.to) +
    (cfg.logScale ? " · log scale" : "");
  document.getElementById("hist-source").innerHTML =
    trustBadge(cfg.trust) + (cfg.trust === "official" ? " " : "") +
    escapeHtml(HIST.credit_line || "") +
    " Release “" + escapeHtml(HIST.release.label) + "”, ingested " +
    fmtStamp(HIST.release.ingested_at) + "." +
    (st.hist === "vital"
      ? " Rates are calculated on this page from the published counts; the " +
        "formula is under Show calculation."
      : "");

  var bases = (HIST.bases || {});
  if (st.hist === "vital" || st.hist === "fertility") {
    document.getElementById("hist-calc").innerHTML =
      "<summary>Show calculation</summary><div class=\"calc-body\">" +
      (st.hist === "vital"
        ? "<code>births per 1,000 = births in year Y ÷ Japanese population at " +
          "1 October Y × 1,000</code><br>" +
          "<code>deaths per 1,000 = deaths in year Y ÷ Japanese population at " +
          "1 October Y × 1,000</code><br>" +
          "Births and deaths are the vital statistics' count for Japanese " +
          "nationals, so the denominator is the Japanese population rather than " +
          "the total. The 1 October estimate is the mid-year population the " +
          "vital statistics themselves use. The two are joined on the year, not " +
          "on the reference date, and a year missing either side is a gap."
        : "The rate is the Statistics Bureau's own, stored exactly as published " +
          "and not recomputed here. It is the sum of birth rates at each single " +
          "year of a mother's age from 15 to 49; the female population by single " +
          "year of age is in no table on this platform, so the rate cannot be " +
          "rebuilt from the counts shown elsewhere on this page — which is why " +
          "this one number is carried as published." +
          (st.pref !== NATIONAL
            ? " Japan is drawn alongside for comparison, on the same basis."
            : "") +
          " The dashed line at 2.07 is roughly the rate at which one generation " +
          "replaces itself; it is a reference level, not a published series. " +
          "Prefecture figures are published for 1980, 1985 and then every year " +
          "from 1986, so the gap between 1980 and 1985 is a gap.") +
      "</div>";
    return;
  }
  document.getElementById("hist-calc").innerHTML =
    "<summary>Show calculation</summary><div class=\"calc-body\">" +
    (st.hist === "index"
      ? "<code>index = value ÷ that series' value in " + escapeHtml(w.from) +
        " × 100</code>, so the base moves with the window you choose.<br>"
      : "The values are published counts; nothing is computed.<br>") +
    (cfg.logScale
      ? "The vertical axis is <b>logarithmic</b>: equal vertical distances are " +
        "equal <i>proportional</i> changes, not equal numbers of people. It is on " +
        "because registered residents and foreign residents are two orders of " +
        "magnitude apart, and on a linear axis the smaller line sits on the " +
        "baseline with no readable shape.<br>"
      : (cfg.logAvailable && st.hist !== "index"
         ? "Foreign residents are a small fraction of the total, so on this " +
           "linear axis their line sits near the baseline. Switch the axis to " +
           "Log to read its shape against the others.<br>" : "")) +
    "Reference dates differ by series and are not interchangeable. " +
    escapeHtml(bases.register || "") + " " + escapeHtml(bases.asof_oct || "") +
    " Foreign residents joined the register in 2013, so that line starts there; " +
    "a gap in any line is a year the Bureau published no value, never a zero." +
    "</div>";
}

function histCsvRows() {
  var cfg = histConfig();
  var periods = [];
  cfg.series.forEach(function (s) {
    s.points.forEach(function (p) { if (periods.indexOf(p[0]) < 0) periods.push(p[0]); });
  });
  periods.sort();
  var index = {};
  cfg.series.forEach(function (s) {
    index[s.name] = {};
    s.points.forEach(function (p) { index[s.name][p[0]] = p[1]; });
  });
  return [["period"].concat(cfg.series.map(function (s) { return s.name; }))].concat(
    periods.map(function (p) {
      return [p].concat(cfg.series.map(function (s) {
        var v = index[s.name][p];
        return v === null || v === undefined ? "" : v;
      }));
    }));
}

/* ---------- table ---------- */

var TABLE_COLS = [
  { key: "name", label: "Prefecture", type: "text" },
  { key: "population", label: "Residents", num: true, title: "On the register at 1 January" },
  { key: "net_change", label: "Change", num: true, title: "Over the calendar year before it" },
  { key: "change_pct", label: "Change (%)", num: true },
  { key: "natural_change", label: "Natural", num: true, title: "Births less deaths" },
  { key: "social_change", label: "Social", num: true, title: "Net change less natural change" },
  { key: "births", label: "Births", num: true },
  { key: "deaths", label: "Deaths", num: true },
  { key: "birth_rate", label: "Births / 1,000", num: true,
    title: "Births per thousand of the mid-year population" },
  { key: "death_rate", label: "Deaths / 1,000", num: true,
    title: "Deaths per thousand of the mid-year population" },
  { key: "foreign_pct", label: "Foreign (%)", num: true, title: "Foreign residents as a share of all residents" },
  { key: "aged_pct", label: "Aged 65+ (%)", num: true },
];

function tableRows() {
  var st = urlState();
  var seg = st.segment;
  return prefectures().map(function (g) {
    return {
      code: g.code,
      name: g.name_en,
      name_ja: g.name_ja,
      population: reg(g.code, seg, "population"),
      net_change: reg(g.code, seg, "net_change"),
      change_pct: measureValue(g.code, "change_pct", seg),
      natural_change: reg(g.code, seg, "natural_change"),
      social_change: reg(g.code, seg, "social_change"),
      births: reg(g.code, seg, "births"),
      deaths: reg(g.code, seg, "deaths"),
      birth_rate: measureValue(g.code, "birth_rate", seg),
      death_rate: measureValue(g.code, "death_rate", seg),
      foreign_pct: measureValue(g.code, "foreign_pct", seg),
      aged_pct: measureValue(g.code, "aged_pct", seg),
    };
  });
}

function cellFor(row, key) {
  if (key === "name") {
    return "<td>" + escapeHtml(row.name) +
      ' <span class="muted">' + escapeHtml(row.name_ja) + "</span></td>";
  }
  var v = row[key];
  if (key === "change_pct") return '<td class="num">' + (v === null ? MISSING : fmtSigned(v, 2, "%")) + "</td>";
  if (key === "foreign_pct" || key === "aged_pct") return '<td class="num">' + fmtRate(v, key === "aged_pct" ? 1 : 2) + "</td>";
  if (key === "birth_rate" || key === "death_rate") return '<td class="num">' + fmtNum(v, 1) + "</td>";
  if (key === "net_change" || key === "natural_change" || key === "social_change") {
    return '<td class="num">' + (v === null ? MISSING : fmtSigned(v, 0, "")) + "</td>";
  }
  return '<td class="num">' + fmtNum(v, 0) + "</td>";
}

function renderTable() {
  var st = urlState();
  var rows = sortRows(tableRows(), st.sort, st.dir);
  var body = rows.map(function (r) {
    return '<tr class="clickable" data-code="' + r.code + '">' +
      TABLE_COLS.map(function (c) { return cellFor(r, c.key); }).join("") + "</tr>";
  }).join("");

  var wrap = document.getElementById("pref-table");
  wrap.innerHTML = '<table class="data tbl-series"><thead>' +
    sortableHead(TABLE_COLS, st.sort, st.dir) + "</thead><tbody>" + body + "</tbody></table>";

  wireSort(wrap, st.sort, st.dir, function (key, dir) {
    setUrlState({ sort: key, dir: dir });
    renderTable();
  });
  Array.prototype.forEach.call(wrap.querySelectorAll("tr.clickable"), function (tr) {
    tr.addEventListener("click", function () {
      selectPrefecture(tr.getAttribute("data-code"), "hist-chart");
    });
  });

  // Sorting is handled above by the row-object helper; this adds the filter
  // box and re-applies whatever the reader had typed, since the re-render
  // just replaced every row.
  enhanceTable(wrap, { sort: false, placeholder: "Filter prefectures…" });

  document.getElementById("table-note").textContent =
    rows.length + " prefectures · " + SEGMENT_LABEL[st.segment];

  var nat = reg(NATIONAL, st.segment, "population");
  document.getElementById("table-foot").textContent =
    "Residents are counted at 1 January " + stockPeriod().slice(0, 4) +
    "; every flow column covers calendar " + flowYear() + ". " +
    "The ministry's national total is " + fmtNum(nat, 0) + " for " +
    SEGMENT_LABEL[st.segment].toLowerCase() + ", which the 47 rows sum to exactly. " +
    "Click any column to rank by it, and again to reverse — a prefecture with no " +
    "published figure stays at the bottom either way, never sorted as zero. " +
    MISSING + " means no value was published.";

  document.getElementById("table-calc").innerHTML =
    "<summary>Show calculation</summary><div class=\"calc-body\">" +
    "<code>" + escapeHtml(MEASURES.change_pct.calc) + "</code><br>" +
    "<code>" + escapeHtml(MEASURES.foreign_pct.calc) + "</code><br>" +
    "<code>" + escapeHtml(MEASURES.aged_pct.calc) + "</code><br>" +
    "<code>" + escapeHtml(MEASURES.birth_rate.calc) + "</code><br>" +
    "<code>" + escapeHtml(MEASURES.death_rate.calc) + "</code><br>" +
    "The foreign share is always foreign residents over all residents, whichever " +
    "segment is shown, because a share of one nationality within itself has no " +
    "meaning. A small number of residents have no recorded age, so the age bands " +
    "fall slightly short of the published total — the shortfall stays in the " +
    "total rather than being spread across the bands." +
    "</div>";
}

function tableCsvRows() {
  var st = urlState();
  var rows = sortRows(tableRows(), st.sort, st.dir);
  return [["prefecture_code", "prefecture", "prefecture_ja"]
    .concat(TABLE_COLS.filter(function (c) { return c.key !== "name"; })
      .map(function (c) { return c.key; }))]
    .concat(rows.map(function (r) {
      return [r.code, r.name, r.name_ja].concat(
        TABLE_COLS.filter(function (c) { return c.key !== "name"; })
          .map(function (c) { return r[c.key] === null ? "" : r[c.key]; }));
    }));
}

/* ---------- csv export ---------- */

function csvHeader(extra) {
  var lines = [
    "# Plover Analytics — population by prefecture",
    "# Source: " + (REG.credit_line || ""),
    "# Release: " + REG.release.label + " (sha256 " + REG.release.sha256 + ")",
    "# Reference: stocks at " + REG.release.latest_period +
      "; flows cover calendar " + flowYear(),
    "# Trust: counts are official statistics as published; any column ending " +
      "_pct or _rate is calculated on the platform, from the formulas below",
  ];
  return lines.concat(extra || []).map(function (l) { return l; });
}

function downloadCsv(filename, rows, headerLines) {
  var body = rows.map(function (r) {
    return r.map(function (cell) {
      var s = cell === null || cell === undefined ? "" : String(cell);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(",");
  }).join("\n");
  var text = headerLines.join("\n") + "\n" + body + "\n";
  var blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 0);
}

/* ---------- interaction ---------- */

var PREF_SELECTS = ["pref-select", "muni-pref-select", "pyr-pref-select"];

/* One prefecture drives the chart and the municipality table, and every
   picker on the page shows it. `scrollTo` is the element to bring into view
   when the choice came from the map or the ranking — picking from a dropdown
   must not yank the reader somewhere else on the page. */
function selectPrefecture(code, scrollTo) {
  if (!code || !GEO_BY_CODE[code] || code === NATIONAL) return;
  setUrlState({ pref: code });
  PREF_SELECTS.forEach(function (id) {
    var select = document.getElementById(id);
    if (select) select.value = code;
  });
  renderHistory();
  renderExtremes();
  renderPyramid();
  renderMunicipalities();
  if (scrollTo) {
    var el = document.getElementById(scrollTo);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function wireSeg(id, attr, onPick) {
  var root = document.getElementById(id);
  if (!root) return;
  Array.prototype.forEach.call(root.querySelectorAll("button"), function (b) {
    b.addEventListener("click", function () {
      Array.prototype.forEach.call(root.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      onPick(b.getAttribute(attr));
    });
  });
}

function syncSeg(id, attr, value) {
  var root = document.getElementById(id);
  if (!root) return;
  Array.prototype.forEach.call(root.querySelectorAll("button"), function (b) {
    b.setAttribute("aria-pressed", b.getAttribute(attr) === value ? "true" : "false");
  });
}

function wire() {
  var st = urlState();

  syncSeg("measure-seg", "data-measure", st.measure);
  wireSeg("measure-seg", "data-measure", function (v) {
    setUrlState({ measure: v });
    drawMap();
  });

  syncSeg("hist-seg", "data-hist", st.hist);
  wireSeg("hist-seg", "data-hist", function (v) {
    setUrlState({ hist: v });
    renderHistory();
  });

  // The segment, like the prefecture, is one choice the whole page follows:
  // the pyramid and the table each carry a control for it and both stay in
  // step, so a reader never sees two different answers to "which residents".
  var pickSegment = function (v) {
    setUrlState({ segment: v });
    syncSeg("segment-seg", "data-segment", v);
    syncSeg("pyr-segment-seg", "data-segment", v);
    renderPyramid();
    renderTable();
    renderMunicipalities();
  };
  syncSeg("segment-seg", "data-segment", st.segment);
  wireSeg("segment-seg", "data-segment", pickSegment);
  syncSeg("pyr-segment-seg", "data-segment", st.segment);
  wireSeg("pyr-segment-seg", "data-segment", pickSegment);

  syncSeg("pyr-seg", "data-pyr", st.pyr);
  wireSeg("pyr-seg", "data-pyr", function (v) {
    setUrlState({ pyr: v });
    renderPyramid();
  });

  syncSeg("muni-seg", "data-muni", st.muni);
  wireSeg("muni-seg", "data-muni", function (v) {
    setUrlState({ muni: v });
    renderMunicipalities();
  });

  var options = prefectures().map(function (g) {
    return '<option value="' + g.code + '">' + escapeHtml(g.name_en) + " · " +
      escapeHtml(g.name_ja) + "</option>";
  }).join("");
  PREF_SELECTS.forEach(function (id) {
    var select = document.getElementById(id);
    if (!select) return;
    select.innerHTML = options;
    select.value = st.pref;
    // No scroll: the reader is already looking at the thing they just changed.
    select.addEventListener("change", function () { selectPrefecture(select.value); });
  });

  document.getElementById("map-csv").addEventListener("click", function () {
    var m = MEASURES[urlState().measure];
    downloadCsv("japan-prefecture-" + urlState().measure + ".csv", mapCsvRows(),
      csvHeader(["# Measure: " + m.label + (m.calc ? " — " + m.calc : "")]));
  });
  document.getElementById("table-csv").addEventListener("click", function () {
    downloadCsv("japan-prefecture-population.csv", tableCsvRows(),
      csvHeader(["# Segment: " + SEGMENT_LABEL[urlState().segment],
        "# " + MEASURES.change_pct.calc,
        "# " + MEASURES.foreign_pct.calc,
        "# " + MEASURES.aged_pct.calc,
        "# " + MEASURES.birth_rate.calc,
        "# " + MEASURES.death_rate.calc]));
  });
  document.getElementById("hist-csv").addEventListener("click", function () {
    var name = (GEO_BY_CODE[urlState().pref] || {}).name_en || "prefecture";
    var cfg = histConfig();
    downloadCsv("japan-" + name.toLowerCase() + "-population-history.csv", histCsvRows(),
      [ "# Plover Analytics — " + name + ", long-run population",
        "# Source: " + (HIST.credit_line || ""),
        "# Release: " + HIST.release.label + " (sha256 " + HIST.release.sha256 + ")",
        "# Years: " + cfg.window.from + " to " + cfg.window.to +
          " (series covers " + cfg.window.first + " to " + cfg.window.last + ")",
        "# Reference dates differ by series: " +
          (HIST.bases ? HIST.bases.register + " " + HIST.bases.asof_oct : ""),
        (urlState().hist === "index"
          ? "# Values are indexed: value ÷ that series' " + cfg.window.from +
            " value × 100"
          : urlState().hist === "vital"
          ? "# Values are rates per 1,000 people, calculated on the platform: " +
            "births (or deaths) in year Y ÷ Japanese population at 1 October Y × 1,000"
          : urlState().hist === "fertility"
          ? "# Values are the published total fertility rate, in births per woman"
          : "# Values are published counts, in persons"),
        (urlState().hist === "vital"
          ? "# Trust: derived — calculated from official counts, formula above"
          : "# Trust: official statistics as published") ]);
  });
  document.getElementById("muni-csv").addEventListener("click", function () {
    var name = (GEO_BY_CODE[urlState().pref] || {}).name_en || "prefecture";
    downloadCsv("japan-" + name.toLowerCase() + "-municipalities.csv", muniCsvRows(),
      csvHeader(["# Prefecture: " + name,
                 "# Rows: " + (urlState().muni === "all"
                   ? "municipalities plus designated-city and district totals — the " +
                     "totals contain the municipalities, so never add the two together"
                   : "municipalities only; designated-city and district totals excluded"),
                 "# " + MEASURES.change_pct.calc,
                 "# " + MEASURES.foreign_pct.calc,
                 "# " + MEASURES.aged_pct.calc]));
  });
  document.getElementById("pyr-csv").addEventListener("click", function () {
    var st2 = urlState();
    var name = (GEO_BY_CODE[st2.pref] || {}).name_en || "prefecture";
    downloadCsv("japan-" + name.toLowerCase() + "-population-pyramid.csv",
      pyramidCsvRows(),
      csvHeader(["# Area: " + name,
                 "# Segment: " + SEGMENT_LABEL[st2.segment],
                 "# Bands are published counts of residents by five-year age " +
                   "band and sex, at " + REG.release.latest_period,
                 "# share_pct = band total ÷ residents with a recorded age and " +
                   "sex × 100, calculated on the platform",
                 "# Residents with no recorded age or sex are in the published " +
                   "headcount but in no band"]));
  });
  document.getElementById("pyr-png").addEventListener("click", exportPyramidMenu);
  document.getElementById("hist-png").addEventListener("click", function () {
    var name = (GEO_BY_CODE[urlState().pref] || {}).name_en || "prefecture";
    if (histChart) histChart.exportPNG("japan-" + name.toLowerCase() + "-population.png");
  });
  document.getElementById("map-png").addEventListener("click", exportMapMenu);
}

/* The map is not an obsChart, so it exports itself — light theme, with the
   source line burnt in, the same contract every other export here honours. */
function renderMapPng() {
  if (!mapChart) return;
  var root = document.documentElement;
  var prev = root.getAttribute("data-theme");
  root.setAttribute("data-theme", "light");
  var st = urlState();
  var m = MEASURES[st.measure];
  var off = document.createElement("div");
  off.style.cssText = "position:absolute;left:-10000px;width:900px;height:760px";
  document.body.appendChild(off);
  var shot = echarts.init(off, null, { renderer: "canvas" });
  var pal = readMapPalette();
  var option = mapOption(pal);
  option.title = {
    text: "Japan — " + m.label + " by prefecture",
    subtext: measurePeriodLabel(st.measure) +
             " · " + (REG.credit_line || "") +
             (m.calc ? "\n" + m.calc : ""),
    left: 12, top: 10,
    textStyle: { color: cssVar("--obs-ink"), fontSize: 15, fontWeight: 600 },
    subtextStyle: { color: cssVar("--obs-text-muted"), fontSize: 11, lineHeight: 15 },
  };
  option.series[0].top = 70;
  option.backgroundColor = cssVar("--obs-surface");
  shot.setOption(option);
  var url = shot.getDataURL({ pixelRatio: 2, backgroundColor: cssVar("--obs-surface") });
  shot.dispose();
  document.body.removeChild(off);
  if (prev === null) root.removeAttribute("data-theme"); else root.setAttribute("data-theme", prev);

  return url;
}

function exportMapMenu() {
  if (!mapChart) return;
  obsExportMenu("japan-prefecture-" + urlState().measure + ".png",
    renderMapPng, document.getElementById("map-png"),
    { w: 900, h: 760, out: 1800 });
}

/* ---------- boot ---------- */

function boot() {
  Promise.all([
    fetch("api/v1/population-jp/prefectures").then(function (r) { return r.json(); }),
    fetch("api/v1/population-jp-history/prefectures")
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; }),
    fetch("assets/japan.geo.json").then(function (r) { return r.json(); }),
  ]).then(function (all) {
    REG = all[0];
    HIST = all[1];
    GEOS = REG.geographies || [];
    GEOS.forEach(function (g) { GEO_BY_CODE[g.code] = g; });
    NATIONAL = REG.national || "00";

    echarts.registerMap("japan", all[2]);
    mapReady = true;

    renderStale();
    renderHead();
    renderTiles();
    renderProvenance();
    wire();
    drawMap();
    renderPyramid();
    renderTable();
    renderMunicipalities();
    if (HIST) {
      renderHistory();
    } else {
      // The long run needs a keyed source; the rest of the page does not,
      // so say what is missing rather than leaving an empty frame.
      document.getElementById("hist-chart").innerHTML =
        '<p class="state-empty">The long-run series is not available on this ' +
        "deployment. Every figure above is unaffected.</p>";
      document.getElementById("hist-source").textContent = "";
    }
  }).catch(function (err) {
    document.getElementById("stale-banner").innerHTML =
      '<div class="banner" role="alert">Could not load the population data. ' +
      escapeHtml(String(err && err.message ? err.message : err)) + "</div>";
  });

  initThemeToggle(function () {
    drawMap();
    renderPyramid();
    if (histChart) histChart.render();
  });
  window.addEventListener("resize", function () {
    if (mapChart) mapChart.resize();
    if (pyramidChart) pyramidChart.resize();
  });
}

boot();
