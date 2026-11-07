/* Inbound arrivals page. The question this screen answers:
   "Is Japan's inbound boom still running, and which markets are driving
   or dragging it?" — the tiles lead with the headline and with the same
   figure excluding China, because in 2026 those two say opposite things.

   One /arrivals payload carries every market × every month; everything
   here is sliced from it client-side. Arrivals are official counts shown
   exactly as published; growth rates, shares, contributions and the 2019
   recovery index are calculated on this page and carry their formula
   under "Show calculation" and in every export.

   Two properties of the source shape this page and must not be smoothed
   over. The two most recent months are estimates covering a subset of
   markets, so named markets do not sum to the headline there — the
   contribution residual is therefore derived from the published total,
   which keeps it exact in every month. And the market table uses the
   latest month with a complete breakdown, because a share of the total
   is only comparable when every row is the same month. */
"use strict";

const DATASET = "jnto-visitors";
const API = "/api/v1/" + DATASET;
const CPI_API = "/api/v1/cpi-jp-items";
const HOTEL_CODE = "0139";        // CPI item: hotel charges (宿泊料)

let AV = null;            // /arrivals payload
let PERIODS = [];         // AV.periods
let PIDX = {};            // iso -> index
let LAST = 0;             // index of the newest published month
let LAST_FULL = 0;        // newest month carrying the full regional breakdown
let BASE_YEAR = 2019;
let HOTEL = null;         // {iso: index value} from cpi-jp-items, or null
let monthlyChart = null, contribChart = null, hotelChart = null;
let mixChart = null;

/* One colour per market for the whole page. Slot 1 is the platform's primary
   navy: it goes to the national total on the arrivals chart and to the
   bundled residual on the two decomposition charts — the aggregate, in other
   words — and the two never appear in the same chart. Every named market
   keeps its own slot everywhere, so China is the same colour whether you are
   reading levels, contributions or shares. */
const MARKET_SLOTS = { total: 1, cn: 2, kr: 3, tw: 4, hk: 5, us: 6 };
const AGGREGATE_SLOT = 1;

/* Markets named individually in the decomposition charts. Every one of them
   is published even in an estimate month, so the stack keeps its meaning
   right up to the latest bar. */
const CONTRIB_MARKETS = [
  { code: "cn", slot: MARKET_SLOTS.cn },
  { code: "kr", slot: MARKET_SLOTS.kr },
  { code: "tw", slot: MARKET_SLOTS.tw },
  { code: "hk", slot: MARKET_SLOTS.hk },
  { code: "us", slot: MARKET_SLOTS.us },
];
const RESIDUAL_SLOT = AGGREGATE_SLOT;
const RESIDUAL_NAME = "All other markets";

/* Formula text is rebuilt once the payload names its baseline year, so the
   sentence a reader is shown can never drift from the year actually used. */
function buildCalcs(y) {
  return {
    yoy: "growth[t] = (arrivals[t] / arrivals[t−12 months] − 1) × 100, in percent, " +
      "from published counts.",
    ytd: "year to date[y] = Σ arrivals[January…latest published month of y]; " +
      "growth = (year to date[y] / year to date[y−1] − 1) × 100, over the same " +
      "months of both years.",
    exchina: "ex-China[t] = arrivals[Total, t] − arrivals[China, t], then the " +
      "year-to-date growth formula above. Total and China are both published " +
      "counts; the difference is calculated here.",
    rec: "recovery[t] = (arrivals[t] / arrivals[same month of " + y +
      "]) × 100. 100 = the same month of " + y + ", the last full year " +
      "before the border closed.",
    contrib: "contribution[market, t] = (arrivals[market, t] − arrivals[market, t−12]) " +
      "/ arrivals[Total, t−12] × 100, in percentage points. The residual is " +
      "growth[Total, t] − Σ contribution[named markets, t], so the segments sum " +
      "to the headline growth rate exactly, including in months where JNTO has " +
      "not yet published every market.",
    share: "share[market] = (arrivals[market] / arrivals[Total]) × 100, both for " +
      "the same month, from published counts.",
    hotel: "Both series indexed to the same month of " + y + " = 100: " +
      "arrivals[t] / arrivals[same month " + y + "] × 100, and hotel " +
      "charges index[t] / hotel charges index[same month " + y + "] × 100. " +
      "Arrivals are counts of people and the hotel index is a price index; " +
      "rebasing puts them on one comparable scale and neither is read off the " +
      "other's units.",
  };
}

let CALCS = buildCalcs(2019);

/* ---- lookups ---- */

function val(code, i) {
  const col = AV.values[code];
  if (!col || i < 0 || i >= col.length) return null;
  const v = col[i];
  return v === undefined ? null : v;
}

/* index of the same calendar month n years earlier, or −1 */
function yearsBack(i, n) {
  const iso = PERIODS[i];
  const target = (Number(iso.slice(0, 4)) - n) + iso.slice(4);
  const hit = PIDX[target];
  return hit === undefined ? -1 : hit;
}

function baseIdx(i) {
  const target = BASE_YEAR + PERIODS[i].slice(4);
  const hit = PIDX[target];
  return hit === undefined ? -1 : hit;
}

function pct(now, then) {
  if (now === null || then === null || !then) return null;
  return (now / then - 1) * 100;
}

function isProvisional(iso) {
  return (AV.provisional_periods || []).indexOf(iso) !== -1;
}

/* Σ of a code over the months of PERIODS[i]'s year up to and including i.
   Returns null if any month in the span is unpublished — a part-year sum
   silently compared against a full one would be a wrong number, not a gap. */
function ytdSum(code, i) {
  const year = PERIODS[i].slice(0, 4);
  let total = 0;
  for (let k = 0; k <= i; k++) {
    if (PERIODS[k].slice(0, 4) !== year) continue;
    const v = val(code, k);
    if (v === null) return null;
    total += v;
  }
  return total;
}

/* the same span one year earlier: January..the same month number */
function ytdSumPrior(code, i) {
  const prior = yearsBack(i, 1);
  return prior < 0 ? null : ytdSum(code, prior);
}

function ytdMonthCount(i) {
  return Number(PERIODS[i].slice(5, 7));
}

/* ---- URL state (any view on this page is citable) ---- */

const VIEWS = ["level", "rec", "season"];

function urlState() {
  const p = new URLSearchParams(location.search);
  const view = p.get("view");
  return {
    view: VIEWS.indexOf(view) >= 0 ? view : "level",
    range: p.get("range") || "8",
    markets: p.get("markets") || "total",
    crange: p.get("crange") || "2",
    mix: p.get("mix") === "hhi" ? "hhi" : "share",
    mrange: p.get("mrange") || "10",
    rows: p.get("rows") || "markets",
    sort: p.get("sort") || "arrivals",
    dir: p.get("dir") === "asc" ? "asc" : "desc",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.view !== "level") p.set("view", s.view);
  if (s.range !== "8") p.set("range", s.range);
  if (s.markets !== "total") p.set("markets", s.markets);
  if (s.crange !== "2") p.set("crange", s.crange);
  if (s.mix !== "share") p.set("mix", s.mix);
  if (s.mrange !== "10") p.set("mrange", s.mrange);
  if (s.rows !== "markets") p.set("rows", s.rows);
  if (s.sort !== "arrivals") p.set("sort", s.sort);
  if (s.dir !== "desc") p.set("dir", s.dir);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

function sourceLine(extra) {
  const rel = AV.release;
  return "Source: Japan National Tourism Organization (JNTO) · " + rel.source_id +
    " · Data through " + fmtPeriodLong(rel.latest_period) +
    " · Retrieved " + fmtStamp(rel.retrieved_at) +
    " · " + TRUST_LABELS.official + (extra ? " · " + extra : "");
}

function csvHeader(what) {
  const rel = AV.release;
  return [
    "Plover Analytics — " + what,
    "Source: Japan National Tourism Organization (JNTO), " + rel.source_name,
    "Source page: " + rel.source_page,
    "Release: " + rel.label + " (sha256 " + rel.sha256 + ")",
    "Retrieved: " + fmtStamp(rel.retrieved_at),
    "Arrivals are official counts of persons, exactly as published.",
    "Estimate months (subject to revision): " +
      ((AV.provisional_periods || []).map(fmtPeriod).join(", ") || "none"),
  ];
}

/* ---- header, staleness, provenance ---- */

function renderHeader() {
  const rel = AV.release;
  document.getElementById("header-asof").textContent =
    "Data through " + fmtPeriodLong(rel.latest_period);
  document.getElementById("page-asof").textContent =
    "Ingested " + fmtStamp(rel.ingested_at);
  document.getElementById("page-sub").textContent =
    "Monthly foreign visitor arrivals by market, 2003–present · persons · " +
    "Japan National Tourism Organization, computed from Ministry of Justice " +
    "immigration statistics";
  if (AV.credit_line) {
    document.getElementById("credit-line").textContent = AV.credit_line;
  }
}

function renderStale() {
  const el = document.getElementById("stale-banner");
  if (!AV.stale) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="banner" role="alert">This surface is stale: the newest ' +
    "ingested data is for " + fmtPeriodLong(AV.release.latest_period) +
    ", ingested " + fmtStamp(AV.release.ingested_at) +
    ". JNTO publishes each month around the 19th of the following month — " +
    "if this persists, run the ingestion.</div>";
}

function renderProvenance() {
  const rel = AV.release;
  const prov = (AV.provisional_periods || []).map(fmtPeriodLong).join(" and ");
  document.getElementById("prov-card").innerHTML =
    '<div class="prov-card">' +
      '<div class="prov-card-head">' +
        '<div class="prov-card-title">Data Source</div>' +
        '<div class="prov-card-id">' + escapeHtml(rel.source_id) + "</div>" +
      "</div>" +
      '<div class="prov-grid">' +
        '<div class="prov-field full">' +
          '<div class="prov-label">Official source</div>' +
          '<div class="prov-value"><a href="' + escapeHtml(rel.source_page) +
            '" rel="noopener">' + escapeHtml(rel.source_name) + "</a></div>" +
          '<div class="prov-sub">Coverage ' + fmtPeriodLong(rel.coverage_start) +
            " – " + fmtPeriodLong(rel.latest_period) + " · counts of persons, " +
            "computed by JNTO from Ministry of Justice immigration statistics; " +
            "excludes foreign permanent residents and crew</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Release</div>' +
          '<div class="prov-value">' + escapeHtml(rel.label) + "</div>" +
          '<div class="prov-sub">Published monthly; a new release is stored ' +
            "per changed file</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Revision status</div>' +
          '<div class="prov-value">' + (prov ? "Estimate: " + escapeHtml(prov)
            : "All months definitive") + "</div>" +
          '<div class="prov-sub">JNTO publishes an estimate, then a provisional ' +
            "figure with the full market detail, then a definitive one</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Retrieved</div>' +
          '<div class="prov-value num">' + fmtStamp(rel.retrieved_at) + "</div>" +
          '<div class="prov-sub">Archived ' + fmtStamp(rel.ingested_at) + "</div>" +
        "</div>" +
        '<div class="prov-field">' +
          '<div class="prov-label">Markets</div>' +
          '<div class="prov-value num">' + AV.markets.length + "</div>" +
          '<div class="prov-sub">' + PERIODS.length + " months published</div>" +
        "</div>" +
        '<div class="prov-field full">' +
          '<div class="prov-label">Archived checksum (SHA-256)</div>' +
          '<div class="prov-hash">' + escapeHtml(rel.sha256) + "</div>" +
        "</div>" +
      "</div>" +
    "</div>";
}

/* ---- stat strip ---- */

/* Direction is carried by the arrow and the sign, never by colour alone —
   and never by the platform's up/down tint, whose meaning here would be
   borrowed from inflation (up = bad). More arrivals is not a warning. */
function tileCell(label, valueHtml, delta, unit, title) {
  let html = MISSING;
  if (delta !== null && delta !== undefined) {
    const rounded = Number(delta.toFixed(1));
    const arrow = rounded === 0 ? ""
      : '<span aria-hidden="true">' + (rounded > 0 ? "▲" : "▼") + "</span> " +
        '<span class="visually-hidden">' + (rounded > 0 ? "up " : "down ") + "</span>";
    html = arrow + fmtNum(Math.abs(delta), 1) + unit;
  }
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(title || label) + '">' +
      escapeHtml(label) + "</div>" +
    '<div class="strip-value num">' + valueHtml + "</div>" +
    '<div class="strip-delta num flat">' + html + "</div>" +
    "</div>";
}

function renderTiles() {
  const iso = PERIODS[LAST];
  const y1 = yearsBack(LAST, 1);
  const b = baseIdx(LAST);

  const latest = val("total", LAST);
  const latestYoY = pct(latest, y1 < 0 ? null : val("total", y1));
  const latestVs19 = pct(latest, b < 0 ? null : val("total", b));

  const ytdNow = ytdSum("total", LAST);
  const ytdThen = ytdSumPrior("total", LAST);
  const months = ytdMonthCount(LAST);

  // ex-China: the headline and China are both published counts; the
  // difference is calculated here and labelled as such.
  let exNow = null, exThen = null;
  const cnNow = ytdSum("cn", LAST), cnThen = ytdSumPrior("cn", LAST);
  if (ytdNow !== null && cnNow !== null) exNow = ytdNow - cnNow;
  if (ytdThen !== null && cnThen !== null) exThen = ytdThen - cnThen;

  const spanLabel = months === 1 ? "January" : "January–" +
    fmtPeriodLong(iso).split(" ")[0];

  document.getElementById("tiles").innerHTML =
    tileCell(fmtPeriodLong(iso), fmtNum(latest, 0), latestYoY, "%",
      "Arrivals in the latest published month, against the same month a year earlier") +
    tileCell("Year to Date · " + spanLabel,
      ytdNow === null ? MISSING : fmtNum(ytdNow, 0), pct(ytdNow, ytdThen), "%",
      "Arrivals so far this year, against the same months of last year") +
    tileCell("Latest Month vs " + BASE_YEAR,
      latestVs19 === null ? MISSING : fmtNum(latestVs19 + 100, 1) +
        '<span class="unit">' + BASE_YEAR + " = 100</span>",
      latestVs19, "%",
      "The latest month indexed to the same month of " + BASE_YEAR + " = 100") +
    tileCell("Year to Date, Excluding China",
      exNow === null ? MISSING : fmtNum(exNow, 0), pct(exNow, exThen), "%",
      "Arrivals so far this year excluding China, against the same months of last year");

  document.getElementById("strip-foot").textContent =
    "Counts are official statistics exactly as published; growth rates, the " +
    BASE_YEAR + " comparison and the ex-China figure are calculated from them. " +
    fmtPeriodLong(iso) + " is an estimate and covers " +
    "a subset of markets — it is subject to revision.";

  const calc = document.getElementById("strip-calc");
  calc.style.display = "";
  calc.innerHTML = "<summary>Show calculation</summary>" +
    '<div class="calc-body">' +
      "<code>" + escapeHtml(CALCS.yoy) + "</code><br>" +
      "<code>" + escapeHtml(CALCS.ytd) + "</code><br>" +
      "<code>" + escapeHtml(CALCS.exchina) + "</code><br>" +
      "<code>" + escapeHtml(CALCS.rec) + "</code><br>" +
      "The year-to-date figure is the sum of the published months. Where a month " +
      "is still an estimate JNTO rounds it, and its own published cumulative, to " +
      "the nearest 100 — so this sum can differ from the cumulative in the press " +
      "release by a few tens of people. Neither figure is adjusted to match the " +
      "other." +
      "<br>Inputs: official counts from release “" + escapeHtml(AV.release.label) +
      "” (sha256 " + AV.release.sha256.slice(0, 12) + "…).</div>";
}

/* ---- the market picker ----
   Up to six series, because past that a line chart is unreadable. Colours
   are pinned per market rather than assigned in click order, so a market
   keeps its colour as others are toggled around it. */

const MAX_PICKED = 6;
const PICKER_SIZE = 11;          // plus the national total

function pickerOptions() {
  const named = AV.markets
    .filter(m => m.kind === "market" || m.kind === "group")
    .map(m => ({ code: m.code, value: val(m.code, LAST_FULL) || 0 }))
    .sort((a, b) => b.value - a.value)
    .slice(0, PICKER_SIZE)
    .map(m => m.code);
  return ["total"].concat(named);
}

function pickedMarkets() {
  const known = {};
  AV.markets.forEach(m => { known[m.code] = true; });
  const want = urlState().markets.split(",")
    .filter(c => c && known[c]).slice(0, MAX_PICKED);
  return want.length ? want : ["total"];
}

function slotFor(code, taken) {
  const pinned = MARKET_SLOTS[code];
  if (pinned && taken.indexOf(pinned) === -1) { taken.push(pinned); return pinned; }
  for (let n = 1; n <= 6; n++) {
    if (taken.indexOf(n) === -1) { taken.push(n); return n; }
  }
  return 1;
}

function renderPicker() {
  const picked = pickedMarkets();
  const atLimit = picked.length >= MAX_PICKED;
  const el = document.getElementById("market-picker");
  el.innerHTML = '<span class="band-toggles-label">Markets</span>' +
    pickerOptions().map(code => {
      const on = picked.indexOf(code) !== -1;
      // a full selection greys the unchecked boxes rather than silently
      // ignoring the click
      return "<label" + (!on && atLimit ? ' class="is-disabled"' : "") + ">" +
        '<input type="checkbox" data-market="' + escapeHtml(code) + '"' +
        (on ? " checked" : "") + (!on && atLimit ? " disabled" : "") + "> " +
        escapeHtml(marketName(code)) + "</label>";
    }).join("") +
    '<span class="band-toggles-note">' +
      (atLimit ? "Six is the maximum; clear one to add another."
               : picked.length + " of " + MAX_PICKED) +
      " · all " + AV.markets.length + " are in the table below</span>";

  Array.prototype.forEach.call(el.querySelectorAll("input[data-market]"), box => {
    box.addEventListener("change", () => {
      const code = box.getAttribute("data-market");
      let next = pickedMarkets().slice();
      const at = next.indexOf(code);
      if (at >= 0) next.splice(at, 1);
      else if (next.length < MAX_PICKED) next.push(code);
      if (!next.length) next = ["total"];
      setUrlState({ markets: next.join(",") });
      renderPicker();
      renderMonthly();
    });
  });
}

/* ---- monthly arrivals ---- */

function rangeStart(range) {
  if (range === "max") return 0;
  const back = yearsBack(LAST, Number(range));
  return back < 0 ? 0 : back;
}

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* Years the seasonality view lays over each other. 2020–2022 are left out:
   with the border shut the lines sit on the axis and flatten every other
   year into an unreadable band. They stay in the data, the table and every
   export — the source line says so. */
const SEASON_SKIP = [2020, 2021, 2022];
const SEASON_YEARS = 5;

function seasonYears() {
  const latest = Number(PERIODS[LAST].slice(0, 4));
  const years = [];
  for (let y = latest; y >= 2003 && years.length < SEASON_YEARS - 1; y--) {
    if (SEASON_SKIP.indexOf(y) === -1) years.push(y);
  }
  if (years.indexOf(BASE_YEAR) === -1) years.push(BASE_YEAR);
  return years;
}

function seasonConfig(code) {
  const series = seasonYears().map((y, k) => ({
    name: String(y),
    slot: k + 1,
    points: MONTH_LABELS.map((label, m) => {
      const i = PIDX[y + "-" + (m < 9 ? "0" : "") + (m + 1) + "-01"];
      return [label, i === undefined ? null : val(code, i)];
    }),
  }));
  return {
    series: series,
    xType: "category",
    unit: "persons",
    dp: 0,
    unitSuffix: "visitors",
    yAxisName: "Persons per month — " + marketName(code),
    trust: "official",
    sourceLine: sourceLine("published counts, by calendar month") +
      " · 2020–2022 omitted (border closed); they remain in the table and the CSV",
  };
}

function monthlyConfig() {
  const state = urlState();
  const codes = pickedMarkets();
  if (state.view === "season") return seasonConfig(codes[0]);

  const from = rangeStart(state.range);
  const rec = state.view === "rec";
  const taken = [];
  const series = codes.map(code => {
    const points = [];
    for (let i = from; i <= LAST; i++) {
      const v = val(code, i);
      if (!rec) { points.push([PERIODS[i], v]); continue; }
      const b = baseIdx(i);
      const base = b < 0 ? null : val(code, b);
      points.push([PERIODS[i],
        v === null || !base ? null : (v / base) * 100]);
    }
    return { name: marketName(code), slot: slotFor(code, taken), points: points };
  });

  // the first estimate month, marked on the chart rather than described
  // only in a footnote
  const firstProv = (AV.provisional_periods || [])
    .filter(p => PIDX[p] >= from).sort()[0];

  const cfg = {
    series: series,
    unit: rec ? "index" : "persons",
    dp: rec ? 1 : 0,
    unitSuffix: rec ? "" : "visitors",
    yAxisName: rec ? "Same month of " + BASE_YEAR + " = 100" : "Persons per month",
    trust: "official",
    sourceLine: sourceLine(rec
      ? "indexed to the same month of " + BASE_YEAR
      : "published counts"),
    eventLines: firstProv ? [{ x: firstProv, label: "Estimate" }] : [],
  };
  if (rec) cfg.refLine = { y: 100, label: BASE_YEAR + " level" };
  return cfg;
}

function renderMonthly() {
  const el = document.getElementById("monthly-chart");
  el.innerHTML = "";
  const state = urlState();
  const cfg = monthlyConfig();
  if (monthlyChart) monthlyChart.dispose();
  monthlyChart = obsChart(el, "line", cfg);

  const codes = pickedMarkets();
  const unit = state.view === "rec" ? BASE_YEAR + " = 100" : "persons";
  document.getElementById("monthly-note").textContent =
    state.view === "season"
      ? marketName(codes[0]) + " · by calendar month"
      : (codes.length === 1 ? marketName(codes[0]) : codes.length + " markets") +
        " · " + fmtPeriodLong(PERIODS[LAST]) + " · " + unit;

  document.getElementById("monthly-source").textContent =
    cfg.sourceLine + (state.view === "season" ? ""
      : " · dashed rule marks the first estimate month");

  const formula = state.view === "rec" ? CALCS.rec
    : (state.view === "season"
        ? "Published counts, not recomputed — the same monthly values, " +
          "grouped by calendar month so that like months line up."
        : "Published counts, not recomputed.");
  document.getElementById("monthly-calc").innerHTML =
    "<summary>Show calculation</summary>" +
    '<div class="calc-body">' +
      (state.view === "rec" ? "<code>" + escapeHtml(formula) + "</code>"
                            : escapeHtml(formula)) +
      (state.view === "season"
        ? "<br>Only one market is shown at a time here: the series are the " +
          "years, so a second market would double the lines and halve the " +
          "legibility. The picker's first selection is the one drawn."
        : "") +
      "<br>Inputs: official counts from release “" + escapeHtml(AV.release.label) +
      "” (sha256 " + AV.release.sha256.slice(0, 12) + "…).</div>";

  // Only the counts view is a plain /observations read (same values, same
  // release as /arrivals); the 2019 index and the by-year layout are built here.
  const from = rangeStart(state.range);
  const codeBtn = obsCodeButton(document.getElementById("monthly-csv"), () => state.view === "level" ? {
    url: API + "/observations?series=" + codes.join(",") + "&measure=index",
    start: from > 0 ? PERIODS[from] : null, release: AV.release, source: cfg.sourceLine,
  } : null);
  if (codeBtn) codeBtn.hidden = state.view !== "level";
}

/* ---- contributions to the headline growth rate ---- */

function contribConfig() {
  const state = urlState();
  const from = Math.max(rangeStart(state.crange), 12);

  const named = CONTRIB_MARKETS.map(m => ({
    name: marketName(m.code), slot: m.slot, points: [],
  }));
  const residual = { name: RESIDUAL_NAME, slot: RESIDUAL_SLOT, points: [] };
  const line = { name: "Total, year over year", points: [] };

  for (let i = from; i <= LAST; i++) {
    const y1 = yearsBack(i, 1);
    const base = y1 < 0 ? null : val("total", y1);
    const now = val("total", i);
    const iso = PERIODS[i];
    if (base === null || !base || now === null) {
      named.forEach(s => s.points.push([iso, null]));
      residual.points.push([iso, null]);
      line.points.push([iso, null]);
      continue;
    }
    const headline = (now / base - 1) * 100;
    let accounted = 0, ok = true;
    CONTRIB_MARKETS.forEach((m, k) => {
      const a = val(m.code, i), b = y1 < 0 ? null : val(m.code, y1);
      if (a === null || b === null) { named[k].points.push([iso, null]); ok = false; return; }
      const c = ((a - b) / base) * 100;
      named[k].points.push([iso, c]);
      accounted += c;
    });
    // The residual is taken from the published headline, not from summing
    // the markets we did not name — so it stays exact in estimate months,
    // where JNTO has not yet published every market.
    residual.points.push([iso, ok ? headline - accounted : null]);
    line.points.push([iso, headline]);
  }

  return {
    series: named.concat([residual]),
    line: line,
    unit: "pp",
    yAxisName: "Contribution to year-over-year growth, pp",
    trust: "derived",
    sourceLine: sourceLine("contributions in percentage points"),
  };
}

function renderContrib() {
  const el = document.getElementById("contrib-chart");
  el.innerHTML = "";
  const cfg = contribConfig();
  if (contribChart) contribChart.dispose();
  contribChart = obsChart(el, "stack", cfg);

  const y1 = yearsBack(LAST, 1);
  const headline = pct(val("total", LAST), y1 < 0 ? null : val("total", y1));
  document.getElementById("contrib-note").textContent =
    fmtPeriodLong(PERIODS[LAST]) + " · total " + fmtSigned(headline, 1, "%");
  const nprov = (AV.provisional_periods || []).length;
  document.getElementById("contrib-source").textContent =
    cfg.sourceLine + " · segments sum to the line" +
    (nprov ? " · the last " + (nprov === 1 ? "bar is an estimate"
      : nprov + " bars are estimates") : "");
  document.getElementById("contrib-calc").innerHTML =
    "<summary>Show calculation</summary>" +
    '<div class="calc-body"><code>' + escapeHtml(CALCS.contrib) + "</code>" +
    "<br>Year-over-year rates through 2021–2023 are measured against a base " +
    "reduced to near zero by the closed border, and are shown for completeness " +
    "rather than as a comparable growth rate." +
    "<br>Inputs: official counts from release “" + escapeHtml(AV.release.label) +
    "” (sha256 " + AV.release.sha256.slice(0, 12) + "…).</div>";
}

/* ---- the market mix ----
   Shares over a trailing twelve months, so a market's summer peak reads as
   seasonality rather than as a change in the mix.

   Concentration is the Herfindahl–Hirschman index of the shares that
   actually partition the total: every leaf market, every residual JNTO
   publishes, and — because the residual rows only begin in 2016 — whatever
   is left of the headline after those, as one more unitemised bucket. That
   keeps the partition complete in every era, so the index means the same
   thing in 2003 as it does today. */

const MIX_MARKETS = ["cn", "kr", "tw", "hk", "us"];
const MIX_RESIDUAL_SLOT = AGGREGATE_SLOT;

function leafCodes() {
  return AV.markets.filter(m => m.kind === "market" || m.kind === "residual")
    .map(m => m.code);
}

/* Σ of the trailing twelve months ending at i, or null if any is unpublished.
   A partial window compared against a full one would be a wrong number. */
function rolling12(code, i) {
  if (i < 11) return null;
  let sum = 0;
  for (let k = i - 11; k <= i; k++) {
    const v = val(code, k);
    if (v === null) return null;
    sum += v;
  }
  return sum;
}

function hhiAt(i, leaves) {
  const total = rolling12("total", i);
  if (!total) return null;
  const parts = [];
  let itemised = 0;
  for (let k = 0; k < leaves.length; k++) {
    const v = rolling12(leaves[k], i);
    if (v === null) return null;      // an incomplete partition is not an index
    parts.push(v);
    itemised += v;
  }
  const rest = total - itemised;
  if (rest > 0.5) parts.push(rest);
  return parts.reduce((acc, v) => acc + Math.pow(v / total * 100, 2), 0);
}

function mixConfig() {
  const state = urlState();
  const from = Math.max(rangeStart(state.mrange), 11);

  if (state.mix === "hhi") {
    const leaves = leafCodes();
    const points = [];
    for (let i = from; i <= LAST; i++) points.push([PERIODS[i], hhiAt(i, leaves)]);
    return {
      series: [{ name: "Concentration (HHI)", slot: 1, points: points }],
      unit: "index",
      dp: 0,
      unitSuffix: "",
      yAxisName: "Herfindahl–Hirschman index of market shares",
      trust: "derived",
      sourceLine: sourceLine("trailing 12-month shares") +
        " · ends before the headline: an estimate month does not publish " +
        "every market, and an incomplete partition is not an index",
    };
  }

  const taken = [];
  const series = MIX_MARKETS.map(code => ({
    name: marketName(code), slot: slotFor(code, taken), points: [],
  }));
  const residual = { name: "All other markets", slot: MIX_RESIDUAL_SLOT, points: [] };
  for (let i = from; i <= LAST; i++) {
    const iso = PERIODS[i];
    const total = rolling12("total", i);
    let named = 0, ok = total !== null && total > 0;
    MIX_MARKETS.forEach((code, k) => {
      const v = ok ? rolling12(code, i) : null;
      if (v === null) { series[k].points.push([iso, null]); ok = false; return; }
      const share = v / total * 100;
      series[k].points.push([iso, share]);
      named += share;
    });
    // taken from the published total, so the bars always reach 100%
    residual.points.push([iso, ok ? 100 - named : null]);
  }
  return {
    series: series.concat([residual]),
    unit: "%",
    yMax: 100,
    unsigned: true,
    yAxisName: "Share of arrivals, trailing 12 months, %",
    trust: "derived",
    sourceLine: sourceLine("trailing 12-month shares") + " · segments sum to 100%",
  };
}

function renderMix() {
  const el = document.getElementById("mix-chart");
  el.innerHTML = "";
  const state = urlState();
  const cfg = mixConfig();
  if (mixChart) mixChart.dispose();
  mixChart = obsChart(el, state.mix === "hhi" ? "line" : "stack", cfg);

  const leaves = leafCodes();
  let lastHhi = null, lastHhiIso = null, baseHhi = null;
  for (let i = LAST; i >= 0 && lastHhi === null; i--) {
    lastHhi = hhiAt(i, leaves);
    lastHhiIso = PERIODS[i];
  }
  const b = PIDX[BASE_YEAR + "-12-01"];
  if (b !== undefined) baseHhi = hhiAt(b, leaves);

  document.getElementById("mix-note").textContent = state.mix === "hhi"
    ? (lastHhi === null ? MISSING
        : fmtNum(lastHhi, 0) + " · " + fmtPeriodLong(lastHhiIso))
    : marketName("cn") + " " +
      fmtNum(cfg.series[0].points.filter(p => p[1] !== null).slice(-1)[0][1], 1) +
      "% · trailing 12 months";

  document.getElementById("mix-source").textContent = cfg.sourceLine;

  document.getElementById("mix-calc").innerHTML =
    "<summary>Show calculation</summary>" +
    '<div class="calc-body">' +
      "<code>share[market, t] = Σ arrivals[market, t−11 … t] / " +
      "Σ arrivals[Total, t−11 … t] × 100</code><br>" +
      "<code>HHI[t] = Σ share[part, t]², over every part that partitions the " +
      "total: each leaf market, each published residual, and the remainder of " +
      "the headline not itemised in that era</code><br>" +
      "A share is a percent of arrivals, not a percent of spending — markets " +
      "differ several-fold in what each visitor spends." +
      (baseHhi !== null && lastHhi !== null
        ? "<br>For scale: " + fmtNum(baseHhi, 0) + " at the end of " + BASE_YEAR +
          " against " + fmtNum(lastHhi, 0) + " at " + fmtPeriodLong(lastHhiIso) +
          ". A lower index means arrivals come from a wider spread of markets."
        : "") +
      "<br>Inputs: official counts from release “" + escapeHtml(AV.release.label) +
      "” (sha256 " + AV.release.sha256.slice(0, 12) + "…).</div>";
}

/* ---- arrivals against hotel prices ---- */

function renderHotel() {
  const el = document.getElementById("hotel-chart");
  const src = document.getElementById("hotel-source");
  el.innerHTML = "";

  if (!HOTEL) {
    el.innerHTML = '<div class="state-empty">The consumer price series for hotel ' +
      "charges is not available from this service, so the comparison cannot be " +
      "drawn. Arrivals above are unaffected.</div>";
    src.textContent = sourceLine();
    return;
  }

  const from = PIDX[BASE_YEAR + "-01-01"];
  const arrivals = [], hotel = [];
  for (let i = from; i <= LAST; i++) {
    const iso = PERIODS[i];
    const b = baseIdx(i);
    const v = val("total", i);
    arrivals.push([iso, b < 0 || v === null || !val("total", b)
      ? null : (v / val("total", b)) * 100]);
    const hv = HOTEL[iso], hb = HOTEL[BASE_YEAR + iso.slice(4)];
    hotel.push([iso, hv === undefined || hb === undefined || !hb
      ? null : (hv / hb) * 100]);
  }

  const cfg = {
    series: [
      { name: "Visitor arrivals", slot: 1, points: arrivals },
      { name: "Hotel charges, consumer price index", slot: 3, points: hotel },
    ],
    unit: "index",
    dp: 1,
    yAxisName: "Same month of " + BASE_YEAR + " = 100",
    refLine: { y: 100, label: BASE_YEAR + " level" },
    trust: "derived",
    sourceLine: "Sources: Japan National Tourism Organization (JNTO); " +
      "Statistics Bureau of Japan, consumer price index item " + HOTEL_CODE +
      " · both indexed to the same month of " + BASE_YEAR +
      " · Data through " + fmtPeriodLong(AV.release.latest_period),
  };
  if (hotelChart) hotelChart.dispose();
  hotelChart = obsChart(el, "line", cfg);

  src.textContent = cfg.sourceLine +
    " · the two agencies publish on different schedules, so the series can end " +
    "in different months; a gap stays a gap";
  document.getElementById("hotel-calc").innerHTML =
    "<summary>Show calculation</summary>" +
    '<div class="calc-body"><code>' + escapeHtml(CALCS.hotel) + "</code>" +
    "<br>Hotel charges is item " + HOTEL_CODE + " of the Japanese consumer price " +
    "index (2020 = 100), 0.81% of the basket, published by the Statistics Bureau " +
    "of Japan. Rebasing it does not change the published index." +
    "<br>This chart shows two series moving together over the same months. It is " +
    "not evidence that one causes the other.</div>";
}

/* ---- all markets ---- */

function marketName(code) {
  const hit = AV.markets.filter(m => m.code === code)[0];
  return hit ? hit.name_en : code;
}

function tableRows() {
  const rows = urlState().rows;
  return AV.markets.filter(m => {
    if (rows === "all") return true;
    if (rows === "regions") return m.kind === "total" || m.kind === "region";
    return m.kind === "market" || m.kind === "group";
  });
}

/* Column definitions, shared by the table and its export. `key` is both the
   sort key and the field name on a row object. */
const TABLE_COLS = [
  { key: "name_en", label: "Market", type: "text" },
  { key: "arrivals", label: "Arrivals", num: true,
    title: "Published count of visitors, official statistic" },
  { key: "yoy", label: "YoY (%)", num: true,
    title: "Against the same month a year earlier" },
  { key: "share", label: "Share (%)", num: true,
    title: "Percent of the national total for the same month" },
  { key: "vs19", label: "vs 2019 (%)", num: true,
    title: "Against the same month of 2019" },
  { label: "2-year trend", nosort: true },
];

function tableRowData() {
  const i = LAST_FULL;
  const y1 = yearsBack(i, 1);
  const b = baseIdx(i);
  const total = val("total", i);
  const sparkFrom = Math.max(0, yearsBack(i, 2));
  const byCode = {};
  AV.markets.forEach(m => { byCode[m.code] = m; });

  return tableRows().map(m => {
    const cur = val(m.code, i);
    const spark = [];
    for (let k = sparkFrom; k <= i; k++) spark.push([PERIODS[k], val(m.code, k)]);
    const parent = byCode[m.parent];
    return {
      code: m.code, name_en: m.name_en, name_ja: m.name_ja,
      kind: m.kind, parent: m.parent,
      arrivals: cur,
      yoy: pct(cur, y1 < 0 ? null : val(m.code, y1)),
      share: cur === null || !total ? null : (cur / total) * 100,
      vs19: pct(cur, b < 0 ? null : val(m.code, b)),
      spark: spark,
      // members of a group (Middle East, the Nordics) are indented under it,
      // but only while the table is in its published order — once it is
      // ranked, an indent would imply an adjacency that is no longer there
      indent: !!(parent && parent.kind === "group"),
    };
  });
}

function renderTable() {
  const st = urlState();
  const i = LAST_FULL;
  const iso = PERIODS[i];
  const ranked = st.sort !== "";
  const rows = sortRows(tableRowData(), st.sort, st.dir);

  const body = rows.map(r =>
    "<tr>" +
    "<td" + (r.indent && !ranked ? ' style="padding-left:22px"' : "") + ">" +
      '<div class="cell-item"><div class="en">' + escapeHtml(r.name_en) + "</div></div></td>" +
    '<td class="num">' + (r.arrivals === null ? MISSING : fmtNum(r.arrivals, 0)) + "</td>" +
    '<td class="num">' + fmtSigned(r.yoy, 1, "%") + "</td>" +
    '<td class="num">' + (r.share === null ? MISSING : fmtNum(r.share, 1)) + "</td>" +
    '<td class="num">' + fmtSigned(r.vs19, 1, "%") + "</td>" +
    "<td>" + sparkSVG(r.spark, 110, 26) + "</td>" +
    "</tr>").join("");

  document.getElementById("markets-note").textContent =
    rows.length + " rows · " + fmtPeriodLong(iso);
  const wrap = document.getElementById("markets-table");
  wrap.innerHTML =
    '<table class="data tbl-series"><thead>' +
    sortableHead(TABLE_COLS, st.sort, st.dir) +
    "</thead><tbody>" + body + "</tbody></table>";

  wireSort(wrap, st.sort, st.dir, (key, dir) => {
    setUrlState({ sort: key, dir: dir });
    renderTable();
  });
  // Sorting is the row-object helper's; this adds the filter box on top.
  enhanceTable(wrap, { sort: false, placeholder: "Filter markets…" });

  const laterMonths = PERIODS.length - 1 - LAST_FULL;
  const sortedCol = TABLE_COLS.filter(c => c.key === st.sort)[0];
  document.getElementById("markets-foot").textContent =
    "Arrivals are official counts for " + fmtPeriodLong(iso) + " — the latest " +
    "month with a complete market breakdown, so that every row and every share " +
    "is the same month. " + (laterMonths > 0
      ? "The " + laterMonths + " later month" + (laterMonths > 1 ? "s are estimates" :
        " is an estimate") + " covering only the largest markets; those figures " +
        "appear in the charts above. " : "") +
    "Click any column to rank by it, and again to reverse — rows with no " +
    "published figure stay at the bottom either way, never sorted as zero. " +
    (sortedCol ? "Ranked by " + sortedCol.label.replace(/ \(%\)$/, "") + ", " +
      (st.dir === "desc" ? "highest first. " : "lowest first. ") : "") +
    "— means JNTO published no value. Share is of the national total.";

  document.getElementById("markets-calc").innerHTML =
    "<summary>Show calculation</summary>" +
    '<div class="calc-body">' +
      "<code>" + escapeHtml(CALCS.yoy) + "</code><br>" +
      "<code>" + escapeHtml(CALCS.share) + "</code><br>" +
      "<code>" + escapeHtml(CALCS.rec) + "</code><br>" +
      "Inputs: official counts from release “" + escapeHtml(AV.release.label) +
      "” (sha256 " + AV.release.sha256.slice(0, 12) + "…).</div>";
}

function exportTableCSV() {
  const st = urlState();
  const i = LAST_FULL;
  const rows = sortRows(tableRowData(), st.sort, st.dir);
  let csv = csvHeader("Visitor arrivals by market, " + fmtPeriod(PERIODS[i]))
    .concat([
      "Rows in the order shown on the page: sorted by " + st.sort + ", " +
        (st.dir === "desc" ? "highest first." : "lowest first."),
      "Columns: arrivals are official counts; yoy_pct, share_pct and vs_" +
        BASE_YEAR + "_pct are calculated.",
      CALCS.yoy, CALCS.share, CALCS.rec,
    ]).map(l => "# " + l).join("\n") + "\n";
  csv += "code,market_en,market_ja,kind,parent,period,arrivals,yoy_pct,share_pct,vs_" +
    BASE_YEAR + "_pct\n";
  const q = v => '"' + String(v === null || v === undefined ? "" : v)
    .replace(/"/g, '""') + '"';
  rows.forEach(r => {
    csv += [r.code, q(r.name_en), q(r.name_ja), r.kind, r.parent || "",
            fmtPeriod(PERIODS[i]),
            r.arrivals === null ? "" : r.arrivals,
            r.yoy === null ? "" : r.yoy.toFixed(4),
            r.share === null ? "" : r.share.toFixed(4),
            r.vs19 === null ? "" : r.vs19.toFixed(4)].join(",") + "\n";
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "jnto-arrivals-by-market-" + fmtPeriod(PERIODS[i]) + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---- controls ---- */

function wireSeg(id, attr, key, after) {
  const seg = document.getElementById(id);
  if (!seg) return;
  const current = urlState()[key];
  Array.prototype.forEach.call(seg.querySelectorAll("button"), btn => {
    btn.setAttribute("aria-pressed", btn.getAttribute(attr) === current ? "true" : "false");
    btn.addEventListener("click", () => {
      const next = {};
      next[key] = btn.getAttribute(attr);
      setUrlState(next);
      Array.prototype.forEach.call(seg.querySelectorAll("button"), b =>
        b.setAttribute("aria-pressed", b === btn ? "true" : "false"));
      after();
    });
  });
}

function wireControls() {
  wireSeg("view-seg", "data-view", "view", () => { renderPicker(); renderMonthly(); });
  wireSeg("range-seg", "data-range", "range", renderMonthly);
  wireSeg("contrib-seg", "data-crange", "crange", renderContrib);
  wireSeg("mix-seg", "data-mix", "mix", renderMix);
  wireSeg("mixrange-seg", "data-mrange", "mrange", renderMix);
  wireSeg("rows-seg", "data-rows", "rows", renderTable);

  document.getElementById("mix-png").addEventListener("click", () =>
    mixChart.exportPNG("jnto-arrivals-market-mix.png"));
  document.getElementById("mix-csv").addEventListener("click", () =>
    mixChart.exportCSV("jnto-arrivals-market-mix.csv",
      csvHeader(urlState().mix === "hhi"
        ? "Concentration of arrivals by market (HHI), trailing 12 months"
        : "Share of arrivals by market, trailing 12 months")
        .concat(["Shares are calculated over a trailing twelve months from " +
                 "published counts; the residual is taken from the published " +
                 "total so the shares sum to 100%."])));

  document.getElementById("monthly-png").addEventListener("click", () =>
    monthlyChart.exportPNG("jnto-arrivals-monthly.png"));
  document.getElementById("monthly-csv").addEventListener("click", () => {
    const st = urlState();
    monthlyChart.exportCSV("jnto-arrivals-monthly.csv",
      csvHeader("Monthly visitor arrivals — " +
        pickedMarkets().map(marketName).join(", ") +
        (st.view === "season" ? ", by calendar month" : ""))
        .concat(st.view === "rec" ? [CALCS.rec] : []));
  });

  document.getElementById("contrib-png").addEventListener("click", () =>
    contribChart.exportPNG("jnto-arrivals-contributions.png"));
  document.getElementById("contrib-csv").addEventListener("click", () =>
    contribChart.exportCSV("jnto-arrivals-contributions.csv",
      csvHeader("Contributions to year-over-year arrivals growth, percentage points")
        .concat([CALCS.contrib])));

  document.getElementById("hotel-png").addEventListener("click", () =>
    hotelChart && hotelChart.exportPNG("jnto-arrivals-vs-hotel-cpi.png"));
  document.getElementById("hotel-csv").addEventListener("click", () =>
    hotelChart && hotelChart.exportCSV("jnto-arrivals-vs-hotel-cpi.csv",
      csvHeader("Visitor arrivals and hotel charges, indexed to " + BASE_YEAR)
        .concat(["Source: Statistics Bureau of Japan for the hotel charges index.",
                 CALCS.hotel])));

  document.getElementById("markets-csv").addEventListener("click", exportTableCSV);
}

/* ---- boot ---- */

function showError(err) {
  document.getElementById("tiles").innerHTML =
    '<div class="state-error" style="grid-column:1/-1">This page failed to load. ' +
    "The data service may not be running — start it and reload this page." +
    "<details><summary>See details</summary><pre>" + escapeHtml(String(err)) +
    "</pre></details></div>";
}

async function loadHotel() {
  // A second dataset, on a second agency's schedule. Its absence must not
  // take the page down: the arrivals surfaces stand on their own.
  try {
    const r = await fetch(CPI_API + "/observations?series=" + HOTEL_CODE +
      "&measure=index&start=" + (BASE_YEAR - 1) + "-01");
    if (!r.ok) return null;
    const doc = await r.json();
    const s = (doc.series || [])[0];
    if (!s) return null;
    const out = {};
    s.points.forEach(p => { if (p[1] !== null) out[p[0]] = p[1]; });
    return out;
  } catch (err) {
    return null;
  }
}

async function init() {
  initThemeToggle(() => {
    renderMonthly();
    renderContrib();
    renderMix();
    renderHotel();
  });
  try {
    const r = await fetch(API + "/arrivals");
    if (!r.ok) throw new Error("arrivals " + r.status + " " + (await r.text()).slice(0, 300));
    AV = await r.json();
  } catch (err) {
    showError(err);
    return;
  }

  PERIODS = AV.periods;
  PERIODS.forEach((p, i) => { PIDX[p] = i; });
  LAST = PERIODS.length - 1;
  BASE_YEAR = AV.baseline_year || 2019;
  CALCS = buildCalcs(BASE_YEAR);

  // the newest month carrying every top-level series: the only basis on
  // which a table of shares is comparable row to row
  LAST_FULL = LAST;
  while (LAST_FULL > 0 &&
         !AV.regions.every(code => val(code, LAST_FULL) !== null)) LAST_FULL--;

  HOTEL = await loadHotel();

  renderHeader();
  renderStale();
  renderTiles();
  renderProvenance();
  wireControls();
  renderPicker();
  renderMonthly();
  renderContrib();
  renderMix();
  renderHotel();
  renderTable();
}

init();
