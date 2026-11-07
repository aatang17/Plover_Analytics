/* Rice and farm prices. The question this screen answers:
   "What is Japanese rice trading at, and is that high or low against the
   stock the trade is holding and against what it costs to grow?"

   Four datasets, one page, because they are one argument: the contract price
   by brand, the private stock behind it, the published cost of production it
   has to clear, and the wider farm-gate and input price indices it sits
   inside. Prices, stocks, costs and indices are official values shown as
   released; crop-year averages, the terms of trade and every change are
   calculated here and carry their formula on the page and in every export.
   Missing is "—" and a gap in a series stays a gap — never interpolated,
   never zero. */
"use strict";

const PRICES = "rice-prices-jp";
const STOCK = "rice-inventory-jp";
const COST = "rice-production-cost";
const TERMS = "agri-prices";
const API = "/api/v1/";

const ALL_BRANDS = "price.00.all-brands";

/* Default brands: the published average, the top of the market, a large
   mainstream brand and two of the biggest by volume. Kept short — more than
   six lines on one chart is unreadable. */
const DEFAULT_BRANDS = [ALL_BRANDS, "price.15.koshihikari.uonuma",
                        "price.05.akitakomachi", "price.01.nanatsuboshi"];

/* Slot 1 is reserved for the Ministry's own all-brand average, the reference
   line every other brand is read against. */
const BRAND_SLOTS = {};
BRAND_SLOTS[ALL_BRANDS] = 1;

const STOCK_VIEWS = {
  stage: [
    { code: "trade.total", label: "Shippers + wholesalers", slot: 1 },
    { code: "shippers.total", label: "Shippers", slot: 2 },
    { code: "wholesalers.total", label: "Wholesalers", slot: 3 },
  ],
  age: [
    { code: "trade.total", label: "All stock", slot: 1 },
    { code: "trade.new-crop", label: "Current crop", slot: 4 },
    { code: "trade.old-crop", label: "One-year-old rice", slot: 5 },
  ],
};

const TERMS_VIEWS = {
  headline: [
    { code: "out.total", label: "Farm output prices", slot: 1 },
    { code: "in.total", label: "Farm input prices", slot: 2 },
    { code: "out.rice", label: "Rice", slot: 3 },
  ],
  inputs: [
    { code: "in.total", label: "All inputs", slot: 2 },
    { code: "in.fertiliser", label: "Fertiliser", slot: 3 },
    { code: "in.feed", label: "Feed", slot: 4 },
    { code: "in.fuel-power", label: "Fuel and power", slot: 5 },
    { code: "in.machinery", label: "Machinery", slot: 6 },
  ],
};

const COST_LEVELS = [
  { code: "nat.per60kg.total-cost", label: "Total cost", slot: 4 },
  { code: "nat.per60kg.cost-net", label: "Cost net of by-products", slot: 5 },
  { code: "nat.per60kg.cost-full", label: "Full cost, interest and rent imputed", slot: 2 },
];

const CROP_YEAR_MEAN_CALC =
  "crop-year average price[y] = mean of the published monthly contract prices " +
  "for September of year y through August of year y+1 — the marketing year of " +
  "the crop harvested in year y. A crop year missing any of its twelve months " +
  "is left out rather than averaged short.";
const TERMS_CALC =
  "terms of trade[t] = (farm output price index[t] / farm input price index[t]) " +
  "× 100, from published index values on the same base year. Above 100 means " +
  "output prices have risen further from the base than input prices have.";

/* ---- state ---------------------------------------------------------------

   The URL carries the whole view, so any state on this page is a citable
   link — the platform's rule for anything that can appear in a post. */

function urlState() {
  const p = new URLSearchParams(location.search);
  const brands = (p.get("brands") || "").split(",").filter(Boolean);
  return {
    brands: brands.length ? brands : DEFAULT_BRANDS.slice(),
    measure: p.get("measure") === "yoy" ? "yoy" : "index",
    range: p.get("range") || "10",
    crange: p.get("crange") || "20",
    sview: p.get("sview") === "age" ? "age" : "stage",
    srange: p.get("srange") || "max",
    tview: (TERMS_VIEWS[p.get("tview")] || p.get("tview") === "terms")
      ? p.get("tview") : "headline",
    trange: p.get("trange") || "25",
    rows: p.get("rows") === "all" ? "all" : "current",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.brands.join(",") !== DEFAULT_BRANDS.join(",")) p.set("brands", s.brands.join(","));
  if (s.measure !== "index") p.set("measure", s.measure);
  if (s.range !== "10") p.set("range", s.range);
  if (s.crange !== "20") p.set("crange", s.crange);
  if (s.sview !== "stage") p.set("sview", s.sview);
  if (s.srange !== "max") p.set("srange", s.srange);
  if (s.tview !== "headline") p.set("tview", s.tview);
  if (s.trange !== "25") p.set("trange", s.trange);
  if (s.rows !== "current") p.set("rows", s.rows);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

/* ---- data ---------------------------------------------------------------- */

const D = {
  overview: null,      // rice-prices-jp /overview
  brands: null,        // rice-prices-jp /series
  releases: {},        // slug -> newest published release
  points: {},          // "dataset|code|measure" -> [[iso, v|null], ...]
  costYears: null,     // crop year -> {code: value}
};

function $(id) { return document.getElementById(id); }

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

/* /observations takes at most eight codes AND caps the whole `series`
   parameter at 200 characters, so a batch has two limits, not one. Chunking
   by count alone is what broke this page in production: eight of these codes
   are over 200 characters together and the API answered 422, not 404. */
const MAX_CODES = 8;
const MAX_SERIES_CHARS = 190;   // 200, less room for a trailing comma

function batchCodes(codes) {
  const batches = [];
  let current = [], length = 0;
  codes.forEach(c => {
    const cost = c.length + (current.length ? 1 : 0);
    if (current.length && (current.length >= MAX_CODES ||
                           length + cost > MAX_SERIES_CHARS)) {
      batches.push(current);
      current = []; length = 0;
    }
    current.push(c);
    length += c.length + (current.length > 1 ? 1 : 0);
  });
  if (current.length) batches.push(current);
  return batches;
}

/* Published values for a set of codes; cached by dataset and measure. */
async function loadSeries(dataset, codes, measure) {
  const want = codes.filter(c => !(dataset + "|" + c + "|" + measure in D.points));
  for (const chunk of batchCodes(want)) {
    const payload = await getJSON(API + dataset + "/observations?series=" +
      encodeURIComponent(chunk.join(",")) + "&measure=" + measure);
    payload.series.forEach(s => {
      D.points[dataset + "|" + s.code + "|" + measure] =
        s.points.map(p => [p[0], p[1]]);
    });
    // A code the API returned nothing for must not be re-requested forever.
    chunk.forEach(c => {
      const key = dataset + "|" + c + "|" + measure;
      if (!(key in D.points)) D.points[key] = [];
    });
  }
  return codes.map(c => D.points[dataset + "|" + c + "|" + measure] || []);
}

/* "Copy Code" for a chart of published series as loaded: one request, and
   one start date only when every line ends in the same month (cut() trims
   each from its own last month). Null when one request cannot reproduce it. */
function codeSpec(dataset, codes, measure, loaded, years, source) {
  if (batchCodes(codes).length !== 1) return null;
  const ends = loaded.filter(p => p.length).map(p => p[p.length - 1][0]);
  if (years !== "max" && ends.some(e => e !== ends[0])) return null;
  return {
    url: API + dataset + "/observations?series=" + codes.join(",") + "&measure=" + measure,
    start: years === "max" || !ends.length ? null
      : (Number(ends[0].slice(0, 4)) - Number(years)) + ends[0].slice(4),
    release: D.releases[dataset], source: source,
  };
}

function codeButton(csvId, spec) {
  const btn = obsCodeButton($(csvId), () => spec);
  if (btn) btn.style.display = spec ? "" : "none";
}

function cut(points, years) {
  if (years === "max") return points;
  const n = Number(years);
  if (!n || !points.length) return points;
  const last = points[points.length - 1][0];
  const from = (Number(last.slice(0, 4)) - n) + last.slice(4);
  return points.filter(p => p[0] >= from);
}

/* ---- header, staleness, provenance --------------------------------------- */

function renderHead() {
  const rel = D.releases[PRICES];
  $("page-asof").textContent = "Contract prices through " + fmtPeriodLong(rel.latest_period);
  $("header-asof").textContent = "Rice through " + fmtPeriodLong(rel.latest_period);
  const tile = (D.overview.tiles || []).find(t => t.key === "all_brands");
  $("page-sub").innerHTML =
    "The all-brand average contract price in " + escapeHtml(fmtPeriodLong(rel.latest_period)) +
    " was <strong class=\"num\">¥" + fmtNum(tile ? tile.value : null, 0) +
    "</strong> per 60kg of brown rice, across " + fmtNum(countPriced(), 0) +
    " origin-and-variety brands. " + trustBadge("official");
}

function countPriced() {
  const rel = D.releases[PRICES];
  return (D.brands.series || []).filter(
    s => s.code !== ALL_BRANDS && s.as_of === rel.latest_period).length;
}

function renderStale() {
  const late = Object.keys(D.releases).filter(slug => D.stale[slug]);
  const el = $("stale-banner");
  if (!late.length) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="banner" role="alert">' +
    late.map(slug => "The " + escapeHtml(DATASET_LABELS[slug]) + " data is stale: the newest " +
      "ingested period is " + escapeHtml(fmtPeriodLong(D.releases[slug].latest_period)) +
      ", ingested " + escapeHtml(fmtStamp(D.releases[slug].ingested_at)) + ".").join(" ") +
    " If this persists, run the ingestion.</div>";
}

const DATASET_LABELS = {};
DATASET_LABELS[PRICES] = "contract price";
DATASET_LABELS[STOCK] = "private inventory";
DATASET_LABELS[COST] = "production cost";
DATASET_LABELS[TERMS] = "agricultural price index";

const SOURCE_NOTES = {};
SOURCE_NOTES[PRICES] = "One release archives every crop year's published price table, " +
  "2008 to date, under a single checksum.";
SOURCE_NOTES[STOCK] = "The national inventory workbook published with the monthly price release.";
SOURCE_NOTES[COST] = "The two long-run rice cost tables from the Farm Management Statistics, " +
  "retrieved through the e-Stat API.";
SOURCE_NOTES[TERMS] = "The class-level output and input price index tables, retrieved " +
  "through the e-Stat API.";

function renderProvenance() {
  const cards = [PRICES, STOCK, COST, TERMS].map(slug => {
    const rel = D.releases[slug];
    if (!rel) return "";
    return '<div class="prov-card">' +
      '<div class="prov-card-head">' +
        '<div class="prov-card-title">' + escapeHtml(titleCase(DATASET_LABELS[slug])) + "</div>" +
        '<div class="prov-card-id">' + escapeHtml(rel.source_id || slug) + "</div>" +
      "</div>" +
      '<div class="prov-grid">' +
        '<div class="prov-field full">' +
          '<div class="prov-label">Official source</div>' +
          '<div class="prov-value"><a href="' + escapeHtml(rel.source_page || "#") +
            '" rel="noopener">' + escapeHtml(rel.source_name || "") + "</a></div>" +
          '<div class="prov-sub">' + escapeHtml(SOURCE_NOTES[slug]) + "</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Release</div>' +
          '<div class="prov-value">Data through ' + escapeHtml(fmtPeriodLong(rel.latest_period)) +
            "</div>" +
          '<div class="prov-sub">Published ' + escapeHtml(rel.frequency || "") + "</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Retrieved</div>' +
          '<div class="prov-value num">' + fmtStamp(rel.retrieved_at) + "</div>" +
          '<div class="prov-sub">Archived ' + fmtStamp(rel.ingested_at) + "</div>" +
        "</div>" +
        '<div class="prov-field full">' +
          '<div class="prov-label">Archived checksum (SHA-256)</div>' +
          '<div class="prov-hash">' + escapeHtml(rel.sha256 || "") + "</div>" +
        "</div>" +
      "</div></div>";
  });
  $("prov-card").innerHTML = cards.join("");
}

function titleCase(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

/* ---- stat strip ---------------------------------------------------------- */

function yenDelta(delta, unit) {
  if (delta === null || delta === undefined) return { html: MISSING, dir: "flat" };
  const rounded = Math.round(delta);
  const dir = rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
  const arrow = rounded === 0 ? ""
    : '<span aria-hidden="true">' + (rounded > 0 ? "▲" : "▼") + "</span> " +
      '<span class="visually-hidden">' + (rounded > 0 ? "up " : "down ") + "</span>";
  return { html: arrow + fmtNum(Math.abs(delta), 0) + " " + unit, dir: dir };
}

function tileCell(label, valueHtml, deltaHtml, dir, title) {
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(title || label) + '">' +
      escapeHtml(label) + "</div>" +
    '<div class="strip-value num">' + valueHtml + "</div>" +
    '<div class="strip-delta num ' + dir + '">' + deltaHtml + "</div>" +
    "</div>";
}

function renderTiles() {
  const byKey = {};
  (D.overview.tiles || []).forEach(t => { byKey[t.key] = t; });
  const cells = [];

  const all = byKey.all_brands;
  if (all) {
    const d = yenDelta(all.delta, "¥");
    cells.push(tileCell("All Brands",
      "¥" + fmtNum(all.value, 0) + '<span class="unit">/60kg</span>',
      d.html, d.dir, all.series_name + " — " + all.calc));
  }

  const peak = byKey.from_peak;
  if (peak) {
    cells.push(tileCell("From Peak",
      fmtSigned(peak.value, 0) + '<span class="unit">¥</span>',
      fmtSigned(peak.pct, 1, "%") + " · peak " + fmtPeriod(peak.peak_period) +
        " (¥" + fmtNum(peak.peak_value, 0) + ")",
      (peak.value ?? 0) < 0 ? "down" : "up",
      peak.series_name + " — " + peak.calc));
  }

  // Stock is a different dataset and a different unit; it is the single most
  // useful thing to read the price against, so it earns a slot in the strip.
  const stock = D.stockTile;
  if (stock) {
    const d = stockDelta(stock.delta);
    cells.push(tileCell("Trade Stock",
      fmtNum(stock.value, 0) + '<span class="unit">10k t</span>',
      d.html, d.dir, stock.series_name + " — " + stock.calc));
  }

  // The cost of growing it, on the same 60kg bag. Annual and two years back,
  // so the cell states its own crop year rather than borrowing the price's.
  const cost = D.costTile;
  if (cost) {
    cells.push(tileCell("Full Cost",
      "¥" + fmtNum(cost.value, 0) + '<span class="unit">/60kg</span>',
      cost.year + " crop", "flat",
      "Full production cost per 60kg with interest on own capital and rent on " +
      "own land imputed — " + cost.year + " crop year, as published."));
  }

  $("tiles").innerHTML = cells.join("");
  $("strip-foot").textContent =
    "Price changes " + (all ? all.comparison : "vs the prior month") +
    " · price in ¥ per 60kg bag of brown rice, stock in 10,000 tonnes of brown rice, " +
    "cost for the newest published crop year — the three are different measures and " +
    "are never ranked together";

  const derived = (D.overview.tiles || []).filter(t => t.trust === "derived");
  const calcEl = $("strip-calc");
  if (derived.length) {
    calcEl.style.display = "";
    calcEl.innerHTML = "<summary>Show calculation</summary>" +
      derived.map(t => '<p><strong>' + escapeHtml(t.label) + "</strong> — " +
        escapeHtml(t.calc) + "</p>").join("");
  }
}

function stockDelta(delta) {
  if (delta === null || delta === undefined) return { html: MISSING, dir: "flat" };
  const rounded = Number(delta.toFixed(0));
  const dir = rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
  const arrow = rounded === 0 ? ""
    : '<span aria-hidden="true">' + (rounded > 0 ? "▲" : "▼") + "</span> " +
      '<span class="visually-hidden">' + (rounded > 0 ? "up " : "down ") + "</span>";
  return { html: arrow + fmtNum(Math.abs(delta), 0) + " 10k t", dir: dir };
}

/* ---- brand chart --------------------------------------------------------- */

let brandChart = null;

function brandName(code) {
  const hit = (D.brands.series || []).find(s => s.code === code);
  return hit ? hit.name_en : code;
}

/* A brand's colour comes from its own code, never from where it sits in the
   list: removing one brand must not repaint the others, or a reader comparing
   two screenshots of this chart is comparing different things. */
function slotFor(code) {
  if (BRAND_SLOTS[code]) return BRAND_SLOTS[code];
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) % 100003;
  return (h % 5) + 2;
}

async function renderBrands() {
  const st = urlState();
  const codes = st.brands.slice(0, 6);
  const loaded = await loadSeries(PRICES, codes, st.measure);
  const rel = D.releases[PRICES];
  const isRate = st.measure === "yoy";
  const cfg = {
    series: codes.map((code, i) => ({
      name: brandName(code), slot: slotFor(code),
      points: cut(loaded[i], st.range),
    })),
    unit: isRate ? "%" : "index",
    unitSuffix: isRate ? "" : "¥/60kg",
    dp: isRate ? 1 : 0,
    yAxisName: isRate ? "% change on a year earlier" : "¥ per 60kg, brown rice",
    yAxisDp: isRate ? 1 : 0,
    trust: isRate ? "derived" : "official",
    legendFloor: 700,
    sourceLine: "Source: MAFF — Rice contract prices (米の相対取引価格). " +
      "Data through " + fmtPeriodLong(rel.latest_period) + ".",
  };
  if (brandChart) brandChart.render(cfg); else brandChart = obsChart($("brand-chart"), "line", cfg);
  codeButton("brand-csv", codeSpec(PRICES, codes, st.measure, loaded, st.range, cfg.sourceLine));

  $("brands-note").textContent = codes.length + " of " +
    fmtNum((D.brands.series || []).length, 0) + " brands";
  $("brand-source").innerHTML = escapeHtml(cfg.sourceLine) + " " +
    (isRate ? "" : trustBadge("official"));
  $("brand-calc").innerHTML = isRate
    ? "<summary>Show calculation</summary><p>" +
      escapeHtml("(price[t] / price[t−12 months] − 1) × 100, from published contract prices.") +
      "</p>"
    : "<summary>Show calculation</summary><p>Published contract prices as released, in yen " +
      "per 60kg of brown rice. Not recomputed. The all-brand average is the Ministry's own, " +
      "weighted by the previous crop year's inspected quantity by brand.</p>";
}

function renderBrandPicker() {
  const st = urlState();
  const wrap = $("brand-picker");
  wrap.querySelectorAll("button").forEach(b => b.remove());
  st.brands.forEach(code => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "band-toggle on";
    b.textContent = brandName(code);
    b.title = "Remove " + brandName(code);
    b.addEventListener("click", () => {
      const next = urlState().brands.filter(c => c !== code);
      if (!next.length) return;              // never leave the chart empty
      setUrlState({ brands: next });
      renderBrandPicker();
      renderBrands();
    });
    wrap.appendChild(b);
  });

  const sel = $("brand-add");
  const priced = (D.brands.series || [])
    .filter(s => st.brands.indexOf(s.code) === -1)
    .sort((a, b) => a.name_en.localeCompare(b.name_en));
  sel.innerHTML = '<option value="">Choose a brand…</option>' +
    priced.map(s => '<option value="' + escapeHtml(s.code) + '">' +
      escapeHtml(s.name_en) + (s.discontinued ? " (to " + fmtPeriod(s.as_of) + ")" : "") +
      "</option>").join("");
  sel.value = "";
}

/* ---- price against cost -------------------------------------------------- */

let costChart = null;

/* Crop-year mean of the monthly contract price: September of year y through
   August of y+1, the marketing year of the crop harvested in y. A crop year
   short of any month is left out rather than averaged over what is there. */
function cropYearMeans(points) {
  const buckets = {};
  points.forEach(p => {
    if (p[1] === null) return;
    const y = Number(p[0].slice(0, 4)), m = Number(p[0].slice(5, 7));
    const crop = m >= 9 ? y : y - 1;
    (buckets[crop] = buckets[crop] || []).push(p[1]);
  });
  const out = {};
  Object.keys(buckets).forEach(crop => {
    if (buckets[crop].length === 12) {
      out[crop] = buckets[crop].reduce((a, b) => a + b, 0) / 12;
    }
  });
  return out;
}

async function renderCost() {
  const st = urlState();
  const costCodes = COST_LEVELS.map(c => c.code);
  const costSeries = await loadSeries(COST, costCodes, "index");
  const price = await loadSeries(PRICES, [ALL_BRANDS], "index");
  const means = cropYearMeans(price[0]);

  const byYear = {};
  costSeries.forEach((pts, i) => pts.forEach(p => {
    const y = p[0].slice(0, 4);
    (byYear[y] = byYear[y] || {})[COST_LEVELS[i].code] = p[1];
  }));
  Object.keys(means).forEach(y => { (byYear[y] = byYear[y] || {}).price = means[y]; });

  let years = Object.keys(byYear).sort();
  if (st.crange !== "max") {
    years = years.slice(-Number(st.crange));
  }
  D.costYears = { years: years, byYear: byYear };

  const series = COST_LEVELS.map(c => ({
    name: c.label, slot: c.slot,
    points: years.map(y => [y, byYear[y][c.code] ?? null]),
  }));
  series.unshift({
    name: "Contract price, crop-year average", slot: 1,
    points: years.map(y => [y, byYear[y].price ?? null]),
  });

  const rel = D.releases[COST];
  const cfg = {
    series: series, xType: "category",
    unit: "index", unitSuffix: "¥/60kg", dp: 0,
    yAxisName: "¥ per 60kg, brown rice", yAxisDp: 0,
    trust: "official", legendFloor: 760,
    sourceLine: "Sources: MAFF — Farm Management Statistics, rice production cost " +
      "(through the " + fmtPeriod(rel.latest_period).slice(0, 4) + " crop); MAFF — Rice " +
      "contract prices.",
  };
  if (costChart) costChart.render(cfg); else costChart = obsChart($("cost-chart"), "line", cfg);

  const withPrice = years.filter(y => byYear[y].price !== undefined);
  const newest = withPrice[withPrice.length - 1];
  $("cost-note").textContent = years.length ? years[0] + "–" + years[years.length - 1] +
    " crop years" : "";
  $("cost-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official") +
    " Cost levels are published as released; the price line is calculated.";
  $("cost-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>" + escapeHtml(CROP_YEAR_MEAN_CALC) + "</p>" +
    "<p>Cost levels are published values, not recomputed. The newest crop year for " +
    "which both a full crop-year price and a published cost exist is " +
    escapeHtml(newest || MISSING) + ".</p>";
}

/* ---- inventories --------------------------------------------------------- */

let stockChart = null;

async function renderStock() {
  const st = urlState();
  const defs = STOCK_VIEWS[st.sview];
  const loaded = await loadSeries(STOCK, defs.map(d => d.code), "index");
  const rel = D.releases[STOCK];
  const cfg = {
    series: defs.map((d, i) => ({
      name: d.label, slot: d.slot, points: cut(loaded[i], st.srange),
    })),
    unit: "index", unitSuffix: "10k t", dp: 0,
    yAxisName: "10,000 tonnes of brown rice", yAxisDp: 0,
    trust: "official", legendFloor: 620,
    sourceLine: "Source: MAFF — Private rice inventories (民間在庫の推移). " +
      "Data through " + fmtPeriodLong(rel.latest_period) + ".",
  };
  if (stockChart) stockChart.render(cfg); else stockChart = obsChart($("stock-chart"), "line", cfg);
  codeButton("stock-csv", codeSpec(STOCK, defs.map(d => d.code), "index", loaded, st.srange,
    cfg.sourceLine));

  $("stock-note").textContent = "through " + fmtPeriod(rel.latest_period);
  $("stock-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("stock-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Published end-of-month stocks as released, in 10,000 tonnes of brown rice. " +
    "Not recomputed.</p>" +
    (st.sview === "age"
      ? "<p>The current crop and one-year-old rice do not add to the total: rice two " +
        "years old and older is inside the total with no row of its own. The residual " +
        "is the difference and is never shown as zero.</p>"
      : "<p>Shippers and wholesalers sum to the combined stage, bar the Ministry's " +
        "independent rounding of each row to whole 10,000 tonnes.</p>");
}

/* ---- output and input prices --------------------------------------------- */

let termsChart = null;

async function renderTerms() {
  const st = urlState();
  const rel = D.releases[TERMS];
  let series, yAxisName, trust, calcHtml, unitSuffix, dp, refLabel, spec = null;

  if (st.tview === "terms") {
    const loaded = await loadSeries(TERMS, ["out.total", "in.total"], "index");
    const inputs = {};
    loaded[1].forEach(p => { if (p[1] !== null) inputs[p[0]] = p[1]; });
    const ratio = loaded[0].map(p => {
      const denom = inputs[p[0]];
      // A month where either side is missing stays a gap: a ratio needs both.
      return [p[0], (p[1] === null || denom === undefined || !denom)
        ? null : (p[1] / denom) * 100];
    });
    series = [{ name: "Terms of trade", slot: 1, points: cut(ratio, st.trange) }];
    yAxisName = "Output prices ÷ input prices × 100";
    // On this view 100 does not mean the base year's price level: it means
    // output and input prices have moved by the same amount since the base.
    refLabel = "Output and input moved alike";
    trust = "derived";
    unitSuffix = "";
    dp = 1;
    calcHtml = "<summary>Show calculation</summary><p>" + escapeHtml(TERMS_CALC) + "</p>";
  } else {
    const defs = TERMS_VIEWS[st.tview];
    const loaded = await loadSeries(TERMS, defs.map(d => d.code), "index");
    series = defs.map((d, i) => ({
      name: d.label, slot: d.slot, points: cut(loaded[i], st.trange),
    }));
    spec = { codes: defs.map(d => d.code), loaded: loaded };
    yAxisName = "Index, 2020 = 100";
    refLabel = "2020 = 100";
    trust = "official";
    unitSuffix = "";
    dp = 1;
    calcHtml = "<summary>Show calculation</summary><p>Published price indices as released " +
      "on the 2020 base. Not recomputed.</p>";
  }

  const cfg = {
    series: series, unit: "index", unitSuffix: unitSuffix, dp: dp,
    yAxisName: yAxisName, yAxisDp: 1, trust: trust, legendFloor: 700,
    refLine: { y: 100, label: refLabel },
    sourceLine: "Source: MAFF — Agricultural Price Statistics (農業物価統計). " +
      "Data through " + fmtPeriodLong(rel.latest_period) + ".",
  };
  if (termsChart) termsChart.render(cfg); else termsChart = obsChart($("terms-chart"), "line", cfg);
  // The terms-of-trade view is a ratio calculated here, so it gets no code.
  codeButton("terms-csv", spec &&
    codeSpec(TERMS, spec.codes, "index", spec.loaded, st.trange, cfg.sourceLine));

  $("terms-note").textContent = "through " + fmtPeriod(rel.latest_period);
  $("terms-source").innerHTML = escapeHtml(cfg.sourceLine) + " " +
    (trust === "official" ? trustBadge("official") : "");
  $("terms-calc").innerHTML = calcHtml;
}

/* ---- brand table --------------------------------------------------------- */

function renderDetail() {
  const st = urlState();
  const rel = D.releases[PRICES];
  const rows = (D.brands.series || [])
    .filter(s => s.code !== ALL_BRANDS)
    .filter(s => st.rows === "all" || !s.discontinued)
    .sort((a, b) => (b.latest ?? -Infinity) - (a.latest ?? -Infinity));

  const head = "<thead><tr>" +
    '<th scope="col">Brand</th>' +
    '<th scope="col" class="num">Price, ¥/60kg</th>' +
    '<th scope="col" class="num">1-month change, ¥</th>' +
    '<th scope="col" class="num">12-month change, ¥</th>' +
    '<th scope="col" class="num">12-month average, ¥</th>' +
    '<th scope="col">Last 5 years</th>' +
    '<th scope="col">Priced through</th>' +
    "</tr></thead>";

  const body = rows.map(s => {
    const stale = s.discontinued;
    return "<tr>" +
      "<td>" + escapeHtml(s.name_en) + "</td>" +
      '<td class="num" data-sort="' + (s.latest ?? "") + '">' + fmtNum(s.latest, 0) + "</td>" +
      '<td class="num" data-sort="' + (s.delta_1m ?? "") + '">' + fmtSigned(s.delta_1m, 0) + "</td>" +
      '<td class="num" data-sort="' + (s.delta_12m ?? "") + '">' + fmtSigned(s.delta_12m, 0) + "</td>" +
      '<td class="num" data-sort="' + (s.avg_12m ?? "") + '">' + fmtNum(s.avg_12m, 0) + "</td>" +
      "<td>" + sparkSVG(s.spark || []) + "</td>" +
      '<td data-sort="' + escapeHtml(s.as_of) + '">' + escapeHtml(fmtPeriod(s.as_of)) +
        (stale ? ' <span class="muted">· no longer priced</span>' : "") + "</td>" +
      "</tr>";
  }).join("");

  $("detail-table").innerHTML =
    '<table class="data">' + head + "<tbody>" + body + "</tbody></table>";
  // sortable.js watches the DOM and enhances any table it finds, on render and
  // on every re-render; nothing to call here.

  $("detail-note").textContent = fmtNum(rows.length, 0) + " brands";
  $("detail-foot").textContent =
    "Prices are official values on each brand's own latest published month; a brand with no " +
    "contract that month, or under 100 tonnes contracted, is not published and shows —. " +
    "Changes and the twelve-month average are calculated from published prices. " +
    "Release: data through " + fmtPeriodLong(rel.latest_period) + ".";
  $("detail-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>1-month change = price[t] − price[t−1 month]. " +
    "12-month change = price[t] − price[t−12 months]. " +
    "12-month average = mean of the twelve published monthly prices ending at t; a brand " +
    "missing any of them shows —, never a shorter average.</p>";
}

function detailCSV() {
  const st = urlState();
  const rel = D.releases[PRICES];
  const rows = (D.brands.series || [])
    .filter(s => s.code !== ALL_BRANDS)
    .filter(s => st.rows === "all" || !s.discontinued);
  const header = [
    "Plover Analytics — rice contract prices by origin and variety",
    "Source: MAFF — Rice contract prices (米の相対取引価格)",
    "Release: data through " + fmtPeriodLong(rel.latest_period),
    "Retrieved: " + fmtStamp(rel.retrieved_at) + " · SHA-256 " + (rel.sha256 || ""),
    "Unit: yen per 60kg of brown rice, first-grade table rice, including freight, " +
      "packaging and consumption tax",
    "Trust: price = official statistic as published; changes and the 12-month average " +
      "are calculated from published prices",
    "1-month change = price[t] − price[t−1 month]",
    "12-month change = price[t] − price[t−12 months]",
    "12-month average = mean of the twelve published monthly prices ending at t",
    "An empty cell is a value the Ministry did not publish — never zero",
  ].map(l => "# " + l).join("\n");
  const lines = ["code,brand,as_of,price_jpy_per_60kg,change_1m_jpy,change_12m_jpy," +
    "average_12m_jpy,still_priced"];
  rows.forEach(s => {
    lines.push([s.code, '"' + s.name_en.replace(/"/g, '""') + '"', s.as_of,
      s.latest ?? "", s.delta_1m ?? "", s.delta_12m ?? "", s.avg_12m ?? "",
      s.discontinued ? "no" : "yes"].join(","));
  });
  const blob = new Blob([header + "\n" + lines.join("\n") + "\n"], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "japan-rice-contract-prices-" + rel.latest_period.slice(0, 7) + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---- exports ------------------------------------------------------------- */

function exportHeader(slug, extra) {
  const rel = D.releases[slug];
  return [
    "Plover Analytics — " + DATASET_LABELS[slug],
    "Source: " + (rel.source_name || ""),
    "Release: data through " + fmtPeriodLong(rel.latest_period),
    "Retrieved: " + fmtStamp(rel.retrieved_at) + " · SHA-256 " + (rel.sha256 || ""),
  ].concat(extra || []).concat([
    "An empty cell is a value the source did not publish — never zero",
  ]);
}

/* ---- wiring -------------------------------------------------------------- */

function seg(id, attr, key, after) {
  const wrap = $(id);
  if (!wrap) return;
  wrap.querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => {
      const next = {};
      next[key] = b.getAttribute(attr);
      setUrlState(next);
      syncSeg(id, attr, key);
      after();
    });
  });
  syncSeg(id, attr, key);
}

function syncSeg(id, attr, key) {
  const cur = String(urlState()[key]);
  $(id).querySelectorAll("button").forEach(b => {
    b.setAttribute("aria-pressed", String(b.getAttribute(attr) === cur));
  });
}

async function boot() {
  initThemeToggle(() => {
    if (brandChart) renderBrands();
    if (costChart) renderCost();
    if (stockChart) renderStock();
    if (termsChart) renderTerms();
  });

  const [ov, brands] = await Promise.all([
    getJSON(API + PRICES + "/overview"),
    getJSON(API + PRICES + "/series"),
  ]);
  D.overview = ov;
  D.brands = brands;
  D.releases[PRICES] = ov.release;
  D.stale = {};
  D.stale[PRICES] = ov.stale;

  // The other three datasets are read for their release card, their staleness
  // and the two cross-dataset tiles. Each is independent: one being down must
  // not take the page with it.
  await Promise.all([STOCK, TERMS].map(async slug => {
    try {
      const o = await getJSON(API + slug + "/overview");
      D.releases[slug] = o.release;
      D.stale[slug] = o.stale;
      if (slug === STOCK) {
        D.stockTile = (o.tiles || []).find(t => t.key === "total") || null;
      }
    } catch (e) { D.stale[slug] = false; }
  }));
  try {
    const rels = await getJSON(API + COST + "/releases");
    const published = (rels.releases || []).find(r => r.status === "published");
    if (published) {
      D.releases[COST] = {
        latest_period: published.latest_period,
        ingested_at: published.ingested_at,
        retrieved_at: published.ingested_at,
        sha256: published.sha256,
        source_name: "MAFF — Farm Management Statistics, rice production cost",
        source_page: "https://www.maff.go.jp/j/tokei/kouhyou/noukei/seisanhi_nousan/",
        source_id: "estat:00500201-rice-cost",
        frequency: "annual",
      };
      const v = JSON.parse(published.validation || "{}");
      D.costTile = {
        value: v.latest_full_cost_per_60kg_jpy,
        year: published.latest_period.slice(0, 4),
      };
    }
  } catch (e) { /* the cost dataset is optional to the rest of the page */ }

  renderHead();
  renderStale();
  renderTiles();
  renderProvenance();

  renderBrandPicker();
  await renderBrands();
  await renderCost();
  await renderStock();
  await renderTerms();
  renderDetail();

  seg("brand-measure-seg", "data-measure", "measure", renderBrands);
  seg("brand-range-seg", "data-range", "range", renderBrands);
  seg("cost-range-seg", "data-crange", "crange", renderCost);
  seg("stock-view-seg", "data-sview", "sview", renderStock);
  seg("stock-range-seg", "data-srange", "srange", renderStock);
  seg("terms-view-seg", "data-tview", "tview", renderTerms);
  seg("terms-range-seg", "data-trange", "trange", renderTerms);
  seg("detail-rows-seg", "data-rows", "rows", renderDetail);

  $("brand-add").addEventListener("change", e => {
    const code = e.target.value;
    if (!code) return;
    const next = urlState().brands.concat([code]).slice(-6);
    setUrlState({ brands: next });
    renderBrandPicker();
    renderBrands();
  });

  $("brand-png").addEventListener("click", () =>
    brandChart.exportPNG("japan-rice-contract-prices.png"));
  $("brand-csv").addEventListener("click", () =>
    brandChart.exportCSV("japan-rice-contract-prices.csv",
      exportHeader(PRICES, [
        "Unit: " + (urlState().measure === "yoy"
          ? "percent change on a year earlier (calculated)"
          : "yen per 60kg of brown rice (official statistic, as published)"),
        urlState().measure === "yoy"
          ? "(price[t] / price[t−12 months] − 1) × 100, from published contract prices"
          : "Prices include freight, packaging and consumption tax, first-grade table rice",
      ])));
  $("cost-png").addEventListener("click", () =>
    costChart.exportPNG("japan-rice-price-vs-cost.png"));
  $("cost-csv").addEventListener("click", () =>
    costChart.exportCSV("japan-rice-price-vs-cost.csv",
      exportHeader(COST, [
        "Unit: yen per 60kg of brown rice, by crop year",
        "Trust: cost levels are official statistics as published; the price line is calculated",
        CROP_YEAR_MEAN_CALC,
      ])));
  $("stock-png").addEventListener("click", () =>
    stockChart.exportPNG("japan-rice-inventories.png"));
  $("stock-csv").addEventListener("click", () =>
    stockChart.exportCSV("japan-rice-inventories.csv",
      exportHeader(STOCK, [
        "Unit: 10,000 tonnes of brown rice, end of month (official statistic, as published)",
        "Private-sector stocks only: on-farm stocks and the government reserve are excluded",
      ])));
  $("terms-png").addEventListener("click", () =>
    termsChart.exportPNG("japan-farm-prices.png"));
  $("terms-csv").addEventListener("click", () =>
    termsChart.exportCSV("japan-farm-prices.csv",
      exportHeader(TERMS, urlState().tview === "terms"
        ? ["Unit: ratio × 100 (calculated)", TERMS_CALC]
        : ["Unit: price index, 2020 = 100 (official statistic, as published)"])));
  $("detail-csv").addEventListener("click", detailCSV);
}

boot().catch(err => {
  $("stale-banner").innerHTML = '<div class="banner" role="alert">This page could not load ' +
    'its data. ' + escapeHtml(String(err.message || err)) + "</div>";
});
