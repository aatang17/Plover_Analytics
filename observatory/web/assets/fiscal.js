/* Japan's public finances. The question this screen answers: "What does the
   Japanese government take in, what does it spend it on, and what does it owe?"

   Eight datasets, one page. The central government's general account from 1875
   (fiscal-jp) with its revenue heads (fiscal-jp-revenue), its spending by
   policy purpose and by ministry (fiscal-jp-expenditure, fiscal-jp-ministry),
   the tax take against budget (tax-receipts-jp), the debt stock
   (govt-debt-jp), the whole of government on the IMF's GFS basis
   (govt-accounts-jp) and the 47 prefectures (local-finance-jp).

   Every figure is an official value as published. The only arithmetic here is
   a unit conversion to ¥ trillion, a difference between two published figures
   and a share of a published total; each carries its formula on the page and
   in every export. A value a source does not publish is "—", never zero. */
"use strict";

const ACC = "/api/v1/fiscal-jp";
const REV = "/api/v1/fiscal-jp-revenue";
const EXP = "/api/v1/fiscal-jp-expenditure";
const MIN = "/api/v1/fiscal-jp-ministry";
const TAX = "/api/v1/tax-receipts-jp";
const DEBT = "/api/v1/govt-debt-jp";
const GG = "/api/v1/govt-accounts-jp";
const PREF = "/api/v1/local-finance-jp";

/* Published unit -> ¥ trillion. Exact conversions, stated in every calc block:
   the fiscal tables are ¥mn, the debt statement ¥100mn, the national accounts
   ¥bn and the prefecture accounts ¥1,000. */
const TN_FROM = { mn: 1e6, hm: 1e4, bn: 1e3, k: 1e9 };

/* The general account's four figures. Budget and settlement are different
   measures and share no line. */
const ACCOUNT_SERIES = [
  { code: "revenue.settlement", label: "Revenue, settled", slot: 1 },
  { code: "expenditure.settlement", label: "Expenditure, settled", slot: 2 },
  { code: "revenue.budget", label: "Revenue, budgeted", slot: 3 },
  { code: "expenditure.budget", label: "Expenditure, budgeted", slot: 4 },
];

/* Revenue heads worth a line. Six is the platform's ceiling; the小 heads
   (monopoly payments, asset disposals, the bridge bonds) stay in the data and
   out of the chart. */
const REVENUE_HEADS = [
  { code: "total", label: "Total revenue", slot: 1 },
  { code: "tax-and-stamp", label: "Tax and stamp revenue", slot: 2 },
  { code: "bond-issuance", label: "Bond issuance", slot: 3 },
  { code: "miscellaneous", label: "Miscellaneous revenue", slot: 4 },
  { code: "prior-year-surplus", label: "Prior-year surplus carried in", slot: 5 },
];
const REVENUE_MEASURES = [
  { id: "settlement", label: "Settlement (決算額)" },
  { id: "budget", label: "Budget (予算額)" },
];

/* The six largest headings of the major-expense classification. Others stay in
   the dataset; a seventh line would put the chart past the palette. */
const SPEND_HEADS = [
  { code: "social-security", label: "Social security", slot: 1 },
  { code: "debt-service", label: "National debt service", slot: 2 },
  { code: "local-allocation-tax", label: "Local allocation tax grants", slot: 3 },
  { code: "public-works", label: "Public works", slot: 4 },
  { code: "education-and-science", label: "Education and science", slot: 5 },
  { code: "defence", label: "Defence", slot: 6 },
];
const SPEND_MEASURES = [
  { id: "settlement", label: "Settlement (決算額)" },
  { id: "budget-final", label: "Budget as finally available (予算現額)" },
  { id: "initial-budget", label: "Initial budget (当初予算)" },
  { id: "budget-total", label: "Budget as voted (当初 + 補正)" },
];

const DEBT_LINES = [
  { code: "total", label: "Total debt", slot: 1 },
  { code: "general-bonds", label: "General bonds", slot: 2 },
  { code: "filp-bonds", label: "FILP bonds", slot: 3 },
  { code: "financing-bills", label: "Financing bills", slot: 4 },
  { code: "borrowings", label: "Borrowings", slot: 5 },
];

const GG_LINES = [
  { code: "1", label: "Revenue", slot: 1 },
  { code: "2", label: "Expense", slot: 2 },
  { code: "net-lending", label: "Net lending (+) / borrowing (−)", slot: 3 },
  { code: "11", label: "Taxes", slot: 4 },
  { code: "27", label: "Social benefits", slot: 5 },
];
const GG_SECTORS = [
  { id: "general", label: "General government" },
  { id: "central", label: "Central government" },
  { id: "local", label: "Local government" },
  { id: "social-security-funds", label: "Social security funds" },
];

/* The taxes the receipts table lists, in the Ministry's own order. The block
   prefix matters: 「その他」 exists in both blocks with different values. */
const TAX_ROWS = [
  ["General account", null],
  ["general.income-tax", "Income tax"],
  ["general.income-tax.withheld", "of which withheld at source", 1],
  ["general.income-tax.self-assessed", "of which self-assessed", 1],
  ["general.corporation-tax", "Corporation tax"],
  ["general.inheritance-tax", "Inheritance tax"],
  ["general.consumption-tax", "Consumption tax"],
  ["general.liquor-tax", "Liquor tax"],
  ["general.tobacco-tax", "Tobacco tax"],
  ["general.gasoline-tax", "Gasoline tax"],
  ["general.petroleum-and-coal-tax", "Petroleum and coal tax"],
  ["general.power-development-tax", "Power development promotion tax"],
  ["general.motor-vehicle-tonnage-tax", "Motor vehicle tonnage tax"],
  ["general.international-tourist-tax", "International tourist tax"],
  ["general.customs-duty", "Customs duty"],
  ["general.other", "Other taxes"],
  ["general.stamp-revenue", "Stamp revenue"],
  ["general.total", "Total — general account", 0, true],
  ["Reference: collected nationally, funding local government or a special account", null],
  ["ref.local-corporation-tax", "Local corporation tax"],
  ["ref.local-gasoline-tax", "Local gasoline tax"],
  ["ref.forest-environment-tax", "Forest environment tax"],
  ["ref.special-corporate-enterprise-tax", "Special corporate enterprise tax"],
  ["ref.special-tobacco-tax", "Special tobacco tax"],
  ["ref.reconstruction-income-surtax", "Reconstruction special income surtax"],
  ["ref.grand-total", "Grand total — all national taxes", 0, true],
];

const DIFF_CALC =
  "difference = collected − budgeted, from published values; outturn % = " +
  "(collected ÷ budgeted) × 100. A tax missing either side shows —, never zero.";
const SHARE_CALC =
  "share % = (body's spending ÷ the published 合計 for the same year) × 100, " +
  "from published values.";

function $(id) { return document.getElementById(id); }

const D = { rel: {}, stale: {}, series: {}, names: {} };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

/* /observations takes at most eight codes and 200 characters of them. */
const MAX_CODES = 8;
const MAX_SERIES_CHARS = 190;

function batchCodes(codes) {
  const batches = [];
  let current = [], length = 0;
  codes.forEach(c => {
    const cost = c.length + (current.length ? 1 : 0);
    if (current.length && (current.length >= MAX_CODES || length + cost > MAX_SERIES_CHARS)) {
      batches.push(current);
      current = []; length = 0;
    }
    current.push(c);
    length += cost;
  });
  if (current.length) batches.push(current);
  return batches;
}

/* How many of a dataset's batches are in flight at once.

   The prefecture table alone needs 48 areas x 3 measures, which is 18 batches
   at the endpoint's eight-code ceiling. Awaited one after another against the
   production host that was eighteen round trips deep — the page sat on its
   skeletons for about six seconds, which on a public page is indistinguishable
   from broken. Six at a time keeps the wall clock near one round trip without
   opening a connection per batch. */
const MAX_PARALLEL = 6;

/* A code the release does not carry 404s the whole batch, so a failed batch is
   retried one code at a time and an absent series is stored empty — it renders
   "—", never a zero. */
async function loadSeries(api, codes) {
  const want = codes.filter(c => !((api + "|" + c) in D.series));
  const chunks = batchCodes(want);
  let next = 0;

  async function worker() {
    while (next < chunks.length) {
      const chunk = chunks[next++];
      try {
        const payload = await getJSON(api + "/observations?series=" + encodeURIComponent(chunk.join(",")));
        payload.series.forEach(s => {
          D.series[api + "|" + s.code] = s.points;
          D.names[api + "|" + s.code] = s.name_en || s.code;
        });
      } catch (err) {
        if (chunk.length === 1) { D.series[api + "|" + chunk[0]] = []; continue; }
        for (const one of chunk) await loadSeries(api, [one]);
      }
    }
  }

  const workers = [];
  for (let i = 0; i < Math.min(MAX_PARALLEL, chunks.length); i++) workers.push(worker());
  await Promise.all(workers);

  codes.forEach(c => { if (!((api + "|" + c) in D.series)) D.series[api + "|" + c] = []; });
}

function pts(api, code) { return D.series[api + "|" + code] || []; }

function at(api, code, period) {
  const p = pts(api, code);
  for (let i = 0; i < p.length; i++) if (p[i][0] === period) return p[i][1];
  return null;
}

function latestOf(api, code) {
  const p = pts(api, code);
  for (let i = p.length - 1; i >= 0; i--) if (p[i][1] !== null) return p[i][0];
  return null;
}

/* Every period a set of series carries, ascending. */
function periodsOf(api, codes) {
  const seen = new Set();
  codes.forEach(c => pts(api, c).forEach(p => { if (p[1] !== null) seen.add(p[0]); }));
  return Array.from(seen).sort();
}

/* "2023-04-01" -> "FY2023": a fiscal year is dated to the 1 April it begins. */
function fy(period) { return period ? "FY" + period.slice(0, 4) : MISSING; }
/* "2026-06-01" -> "Jun 2026": a debt quarter is dated to its closing month. */
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function qtr(period) {
  return period ? MON[Number(period.slice(5, 7)) - 1] + " " + period.slice(0, 4) : MISSING;
}
function yearBefore(period) { return String(Number(period.slice(0, 4)) - 1) + period.slice(4); }

function tn(v, from) { return v === null || v === undefined ? null : v / TN_FROM[from]; }

/* ---- URL state ---- */

function urlState() {
  const p = new URLSearchParams(location.search);
  const one = (key, allowed, fallback) =>
    allowed.indexOf(p.get(key)) >= 0 ? p.get(key) : fallback;
  return {
    range: one("range", ["post", "max"], "post"),
    revenue: one("revenue", REVENUE_MEASURES.map(m => m.id), "settlement"),
    spend: one("spend", SPEND_MEASURES.map(m => m.id), "settlement"),
    sector: one("sector", GG_SECTORS.map(s => s.id), "general"),
    taxYear: p.get("taxYear") || "",
    ministryYear: p.get("ministryYear") || "",
    prefYear: p.get("prefYear") || "",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.range !== "post") p.set("range", s.range);
  if (s.revenue !== "settlement") p.set("revenue", s.revenue);
  if (s.spend !== "settlement") p.set("spend", s.spend);
  if (s.sector !== "general") p.set("sector", s.sector);
  ["taxYear", "ministryYear", "prefYear"].forEach(k => { if (s[k]) p.set(k, s[k]); });
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

/* ---- head, staleness, provenance ---- */

const DATASETS = [
  ["acc", ACC, "General Account — Budget and Settlement",
   "Table 1 of the Ministry's fiscal statistics: one row per fiscal year since 1875, budget and settlement, revenue and expenditure."],
  ["rev", REV, "General Account Revenue by Head",
   "Tables 3 and 4, read together, plus the two heads the Ministry publishes only in the note under the table."],
  ["exp", EXP, "Spending by Policy Purpose",
   "Tables 20 and 19(2): the major-expense classification at heading level, on five measures."],
  ["min", MIN, "Spending by Ministry",
   "Tables 5 and 6: every body under the name it had, from the Meiji period."],
  ["tax", TAX, "National Tax Receipts",
   "One workbook per fiscal year, every national tax against the supplementary budget."],
  ["debt", DEBT, "Central Government Debt Outstanding",
   "The quarterly statement, stitched across every edition: the published file is a rolling five-year window."],
  ["gg", GG, "General Government Accounts (GFS)",
   "Appended table 6(2) of the national accounts, the newest edition taken whole."],
  ["pref", PREF, "Prefecture Finances",
   "Table 1 of the prefectural settlement survey, one workbook per fiscal year since 2002."],
];

function renderHead() {
  const budgetYear = D.rel.acc.latest_period;
  const taxYear = latestOf(TAX, "general.total.settlement");
  // Kept short: the as-of line is nowrap platform-wide, and three spelled-out
  // clauses ran off the right edge at 390px.
  $("page-asof").textContent =
    "Budget " + fy(budgetYear) +
    " · Debt " + qtr(D.rel.debt.latest_period) +
    " · GFS " + fy(D.rel.gg.latest_period);
  $("header-asof").textContent = fy(budgetYear);

  const spend = tn(at(ACC, "expenditure.budget", budgetYear), "mn");
  const tax = tn(at(TAX, "general.total.settlement", taxYear), "mn");
  const debt = tn(at(DEBT, "total", D.rel.debt.latest_period), "hm");
  const lend = tn(at(GG, "general.net-lending", D.rel.gg.latest_period), "bn");
  $("page-sub").innerHTML =
    "The general account is budgeted at <strong class=\"num\">¥" + fmtNum(spend, 1) +
    "tn</strong> in " + fy(budgetYear) + ", against <strong class=\"num\">¥" +
    fmtNum(tax, 1) + "tn</strong> of national tax collected in " + fy(taxYear) +
    ". Central government debt stood at <strong class=\"num\">¥" + fmtNum(debt, 0) +
    "tn</strong> at " + qtr(D.rel.debt.latest_period) +
    ", and the whole of government ran a <strong class=\"num\">¥" +
    fmtNum(Math.abs(lend === null ? 0 : lend), 1) + "tn</strong> " +
    (lend !== null && lend < 0 ? "deficit" : "surplus") + " in " +
    fy(D.rel.gg.latest_period) + " on the GFS basis. " + trustBadge("official");
}

function renderStale() {
  const el = $("stale-banner");
  const names = {};
  DATASETS.forEach(([key, , title]) => { names[key] = title.toLowerCase(); });
  const stale = Object.keys(D.stale).filter(k => D.stale[k]);
  if (!stale.length) { el.innerHTML = ""; return; }
  el.innerHTML = '<div class="banner" role="alert">This surface is stale: the ' +
    escapeHtml(stale.map(k => names[k] + " (through " +
      (k === "debt" ? qtr(D.rel[k].latest_period) : fy(D.rel[k].latest_period)) + ")").join(", ")) +
    " " + (stale.length > 1 ? "are" : "is") +
    " older than the source's own cadence allows. If this persists, run the ingestion.</div>";
}

function provCard(rel, title, sub, periodLabel, cadence) {
  return '<div class="prov-card">' +
    '<div class="prov-card-head">' +
      '<div class="prov-card-title">' + escapeHtml(title) + "</div>" +
      '<div class="prov-card-id">' + escapeHtml(rel.source_id || "") + "</div>" +
    "</div>" +
    '<div class="prov-grid">' +
      '<div class="prov-field full">' +
        '<div class="prov-label">Official source</div>' +
        '<div class="prov-value"><a href="' + escapeHtml(rel.source_page || "#") +
          '" rel="noopener">' + escapeHtml(rel.source_name || "") + "</a></div>" +
        '<div class="prov-sub">' + escapeHtml(sub) + "</div>" +
      "</div>" +
      '<div class="prov-field">' +
        '<div class="prov-label">Release</div>' +
        '<div class="prov-value">Data through ' + escapeHtml(periodLabel) + "</div>" +
        '<div class="prov-sub">' + escapeHtml(cadence) + "</div>" +
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
}

function renderProvenance() {
  $("prov-cards").innerHTML = DATASETS.map(([key, , title, sub]) => {
    const rel = D.rel[key];
    if (!rel) return "";
    const label = key === "debt" ? qtr(rel.latest_period) : fy(rel.latest_period);
    const cadence = key === "debt" ? "Published quarterly" : "Published annually";
    return provCard(rel, title, sub, label, cadence);
  }).join("");
}

/* ---- stat strip ---- */

function tileCell(label, valueHtml, deltaHtml, dir, title) {
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(title || label) + '">' + escapeHtml(label) + "</div>" +
    '<div class="strip-value num">' + valueHtml + "</div>" +
    '<div class="strip-delta num ' + dir + '">' + deltaHtml + "</div>" +
    "</div>";
}

function delta(v, dp, unit) {
  if (v === null || v === undefined) return { html: MISSING, dir: "flat" };
  const rounded = Number(v.toFixed(dp));
  const dir = rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
  const arrow = rounded === 0 ? ""
    : '<span aria-hidden="true">' + (rounded > 0 ? "▲" : "▼") + "</span> " +
      '<span class="visually-hidden">' + (rounded > 0 ? "up " : "down ") + "</span>";
  return { html: arrow + fmtNum(Math.abs(rounded), dp) + " " + unit, dir: dir };
}

function yearOnYear(api, code, from, period) {
  const now = tn(at(api, code, period), from);
  const was = tn(at(api, code, yearBefore(period)), from);
  return now === null || was === null ? null : now - was;
}

function renderTiles() {
  const budgetYear = D.rel.acc.latest_period;
  const taxYear = latestOf(TAX, "general.total.settlement");
  const debtQ = D.rel.debt.latest_period;
  const ggYear = D.rel.gg.latest_period;

  const spend = tn(at(ACC, "expenditure.budget", budgetYear), "mn");
  const tax = tn(at(TAX, "general.total.settlement", taxYear), "mn");
  const debt = tn(at(DEBT, "total", debtQ), "hm");
  const lend = tn(at(GG, "general.net-lending", ggYear), "bn");

  const dSpend = delta(yearOnYear(ACC, "expenditure.budget", "mn", budgetYear), 1, "¥tn");
  const dTax = delta(yearOnYear(TAX, "general.total.settlement", "mn", taxYear), 1, "¥tn");
  const dDebt = delta(yearOnYear(DEBT, "total", "hm", debtQ), 0, "¥tn");
  const dLend = delta(yearOnYear(GG, "general.net-lending", "bn", ggYear), 1, "¥tn");

  $("tiles").innerHTML = [
    tileCell("General Account Budget, " + fy(budgetYear),
      fmtNum(spend, 1) + '<span class="unit">¥tn</span>', dSpend.html, dSpend.dir,
      "General account expenditure as budgeted — the initial budget plus every supplementary — " +
      "published in ¥ million and shown in ¥tn."),
    tileCell("National Tax Collected, " + fy(taxYear),
      fmtNum(tax, 1) + '<span class="unit">¥tn</span>', dTax.html, dTax.dir,
      "Tax and stamp revenue actually collected for the general account, published in ¥ million " +
      "and shown in ¥tn."),
    tileCell("Central Government Debt, " + qtr(debtQ),
      fmtNum(debt, 0) + '<span class="unit">¥tn</span>', dDebt.html, dDebt.dir,
      "Bonds, borrowings and financing bills outstanding at quarter end, published in ¥100mn " +
      "and shown in ¥tn."),
    tileCell("Govt Net Lending, " + fy(ggYear),
      fmtSigned(lend, 1) + '<span class="unit">¥tn</span>', dLend.html, dLend.dir,
      "Net lending (+) or net borrowing (−) of central government, local government and the " +
      "social security funds together, published in ¥ billion and shown in ¥tn."),
  ].join("");

  $("strip-foot").innerHTML =
    "Every figure is an official value as published. " + trustBadge("official");
  $("strip-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Each tile is the published figure for the period named on it, converted to ¥ trillion: " +
    "¥tn = ¥mn ÷ 1,000,000, ¥100mn ÷ 10,000, or ¥bn ÷ 1,000 — exact conversions, not estimates.</p>" +
    "<p>The change beneath is value[t] − value[t−1 year], from published values. A period " +
    "missing either side shows —, never zero.</p>";
}

/* ---- general account ---- */

let accountChart = null;

function renderAccount() {
  const st = urlState();
  const all = periodsOf(ACC, ACCOUNT_SERIES.map(s => s.code));
  const years = st.range === "max" ? all : all.filter(p => Number(p.slice(0, 4)) >= 1946);
  const cfg = {
    series: ACCOUNT_SERIES.map(s => ({
      name: s.label, slot: s.slot,
      points: years.map(p => [fy(p), tn(at(ACC, s.code, p), "mn")]),
    })),
    xType: "category",
    unit: "index", unitSuffix: "¥tn", dp: 1,
    yAxisName: "¥ trillion", yAxisDp: 0,
    trust: "official", legendFloor: 760,
    sourceLine: "Source: Ministry of Finance — Fiscal Statistics (財政統計), Table 1. " +
      "Data through " + fy(D.rel.acc.latest_period) + ".",
  };
  if (accountChart) accountChart.render(cfg); else accountChart = obsChart($("account-chart"), "line", cfg);
  $("account-note").textContent = years.length
    ? fy(years[0]) + "–" + fy(years[years.length - 1]) : "";
  $("account-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("account-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Published amounts, not computed here. 予算額 is the initial budget plus every " +
    "supplementary the Diet passed during the year; 決算額 is the final settled figure.</p>" +
    "<p>Amounts are published in 円 for 1875–1946 and 千円 from 1947, normalised to ¥ million " +
    "on ingestion so the series does not change unit mid-run, and shown here as ¥tn = ¥mn ÷ " +
    "1,000,000.</p>" +
    "<p>From 1947 the two budget lines lie exactly on top of each other: Japan's general " +
    "account budget balances by construction, because bond issuance is counted as revenue, so " +
    "budgeted revenue equals budgeted expenditure in every modern year. They diverge in 45 of " +
    "the years before 1946, which the 'Since 1875' view shows.</p>" +
    "<p>Fiscal years are dated to the 1 April they begin on. Before 1886 the Japanese fiscal " +
    "year began in October and then in July, so that dating is a convention for those years. " +
    "The settlement lines stop about two years short of the budget lines because a year's " +
    "accounts close roughly twenty months after it starts; the gap is a gap, never a zero.</p>";
}

/* ---- revenue by head ---- */

let revenueChart = null;

function renderRevenue() {
  const st = urlState();
  const codes = REVENUE_HEADS.map(h => h.code + "." + st.revenue);
  const years = periodsOf(REV, codes);
  const cfg = {
    series: REVENUE_HEADS.map(h => ({
      name: h.label, slot: h.slot,
      points: years.map(p => [fy(p), tn(at(REV, h.code + "." + st.revenue, p), "mn")]),
    })),
    xType: "category",
    unit: "index", unitSuffix: "¥tn", dp: 1,
    yAxisName: "¥ trillion", yAxisDp: 0,
    trust: "official", legendFloor: 820,
    sourceLine: "Source: Ministry of Finance — Fiscal Statistics (財政統計), Tables 3 and 4. " +
      "Data through " + fy(D.rel.rev.latest_period) + ".",
  };
  if (revenueChart) revenueChart.render(cfg); else revenueChart = obsChart($("revenue-chart"), "line", cfg);
  $("revenue-note").textContent = years.length
    ? fy(years[0]) + "–" + fy(years[years.length - 1]) : "";
  $("revenue-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("revenue-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Published amounts, not computed here; shown as ¥tn = ¥mn ÷ 1,000,000.</p>" +
    "<p>Bond issuance (公債金) is a revenue head of the general account in Japan's own " +
    "presentation, so the total line includes it. Two further heads — the bridge bonds and " +
    "receipts from the settlement adjustment fund — exist only in the note under the Ministry's " +
    "table and are in the dataset but not plotted; the plotted heads therefore do not add to the " +
    "total in the thirteen years those apply.</p>";
}

/* ---- tax receipts ---- */

function renderTaxYears() {
  const periods = periodsOf(TAX, ["general.total.settlement"]);
  const st = urlState();
  const chosen = periods.indexOf(st.taxYear) >= 0 ? st.taxYear : periods[periods.length - 1];
  $("tax-year").innerHTML = periods.slice().reverse()
    .map(p => '<option value="' + p + '"' + (p === chosen ? " selected" : "") + ">" +
      fy(p) + "</option>").join("");
  return chosen;
}

function renderTax() {
  const period = renderTaxYears();
  const rows = TAX_ROWS.map(row => {
    const [code, label, indent, strong] = row;
    if (label === null) {
      return '<tr><th scope="rowgroup" colspan="5" style="text-align:left">' +
        escapeHtml(code) + "</th></tr>";
    }
    const budget = at(TAX, code + ".budget", period);
    const actual = at(TAX, code + ".settlement", period);
    const diff = budget === null || actual === null ? null : actual - budget;
    const pct = budget === null || actual === null || !budget ? null : (actual / budget) * 100;
    const name = strong ? "<strong>" + escapeHtml(label) + "</strong>" : escapeHtml(label);
    return "<tr>" +
      "<td" + (indent ? ' class="indent-' + indent + '"' : "") + ">" + name + "</td>" +
      '<td class="num" data-sort="' + (budget === null ? "" : budget) + '">' +
        fmtNum(tn(budget, "mn"), 2) + "</td>" +
      '<td class="num" data-sort="' + (actual === null ? "" : actual) + '">' +
        fmtNum(tn(actual, "mn"), 2) + "</td>" +
      '<td class="num" data-sort="' + (diff === null ? "" : diff) + '">' +
        fmtSigned(tn(diff, "mn"), 2) + "</td>" +
      '<td class="num" data-sort="' + (pct === null ? "" : pct) + '">' +
        fmtNum(pct, 1) + "</td>" +
      "</tr>";
  }).join("");

  $("tax-table").innerHTML =
    '<table class="data"><caption class="visually-hidden">National tax receipts ' +
    "against budget, " + fy(period) + "</caption><thead><tr>" +
    '<th scope="col">Tax</th>' +
    '<th scope="col" class="num">Budgeted, ¥tn</th>' +
    '<th scope="col" class="num">Collected, ¥tn</th>' +
    '<th scope="col" class="num">Difference, ¥tn</th>' +
    '<th scope="col" class="num">Outturn, %</th>' +
    "</tr></thead><tbody>" + rows + "</tbody></table>";
  if (window.enhanceTables) window.enhanceTables($("tax-table"));

  const total = at(TAX, "general.total.settlement", period);
  const budget = at(TAX, "general.total.budget", period);
  $("tax-foot").innerHTML = "General account tax and stamp revenue came in at ¥" +
    fmtNum(tn(total, "mn"), 1) + "tn in " + fy(period) + ", against ¥" +
    fmtNum(tn(budget, "mn"), 1) + "tn budgeted. " + trustBadge("official");
  $("tax-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Budgeted (補正後予算額) and collected (決算額) are published by the Ministry; shown as " +
    "¥tn = ¥mn ÷ 1,000,000.</p><p>" + escapeHtml(DIFF_CALC) + "</p>" +
    "<p>The Ministry publishes its own 進捗割合 and 増減 columns. They are not ingested: both " +
    "are arithmetic on the two published series, so they are computed here and carry this " +
    "formula instead.</p>";
}

/* ---- spending by policy purpose ---- */

let spendChart = null;

function renderSpend() {
  const st = urlState();
  const codes = SPEND_HEADS.map(h => h.code + "." + st.spend);
  const years = periodsOf(EXP, codes);
  const cfg = {
    series: SPEND_HEADS.map(h => ({
      name: h.label, slot: h.slot,
      points: years.map(p => [fy(p), tn(at(EXP, h.code + "." + st.spend, p), "mn")]),
    })),
    xType: "category",
    unit: "index", unitSuffix: "¥tn", dp: 1,
    yAxisName: "¥ trillion", yAxisDp: 0,
    trust: "official", legendFloor: 900, legendBottomNarrow: 92,
    sourceLine: "Source: Ministry of Finance — Fiscal Statistics (財政統計), Tables 19(2) and 20. " +
      "Data through " + fy(D.rel.exp.latest_period) + ".",
  };
  if (spendChart) spendChart.render(cfg); else spendChart = obsChart($("spend-chart"), "line", cfg);
  $("spend-note").textContent = years.length
    ? fy(years[0]) + "–" + fy(years[years.length - 1]) : "";
  $("spend-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("spend-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Published amounts under the Ministry's major-expense classification, not computed here; " +
    "shown as ¥tn = ¥mn ÷ 1,000,000.</p>" +
    "<p>Six of about fourteen headings are plotted — the largest, and the palette's ceiling for " +
    "one chart. The rest are in the dataset, so these six do not sum to total spending.</p>" +
    "<p>予算現額 adds carry-overs from the prior year and reserve drawdowns to the voted budget, " +
    "so it is larger than 当初予算 + 補正予算 and is a different measure. The settlement measure " +
    "stops about two years short of the budget measures.</p>";
}

/* ---- spending by ministry ---- */

function renderMinistryYears() {
  const periods = periodsOf(MIN, ["total.budget"]).filter(p => Number(p.slice(0, 4)) >= 1947);
  const st = urlState();
  const chosen = periods.indexOf(st.ministryYear) >= 0 ? st.ministryYear : periods[periods.length - 1];
  $("ministry-year").innerHTML = periods.slice().reverse()
    .map(p => '<option value="' + p + '"' + (p === chosen ? " selected" : "") + ">" +
      fy(p) + "</option>").join("");
  return chosen;
}

function renderMinistry() {
  const period = renderMinistryYears();
  const total = at(MIN, "total.budget", period);
  const bodies = D.ministryCodes
    .map(code => ({ code: code, value: at(MIN, code + ".budget", period),
                    name: D.names[MIN + "|" + code + ".budget"] || code }))
    .filter(b => b.value !== null && b.code !== "total")
    .sort((a, b) => b.value - a.value);

  const rows = bodies.map(b => {
    const share = total ? (b.value / total) * 100 : null;
    // "Ministry of Finance — budgeted" -> "Ministry of Finance"
    const name = b.name.split(" — ")[0];
    return "<tr><td>" + escapeHtml(name) + "</td>" +
      '<td class="num" data-sort="' + b.value + '">' + fmtNum(b.value / 1000, 1) + "</td>" +
      '<td class="num" data-sort="' + (share === null ? "" : share) + '">' +
        fmtNum(share, 1) + "</td></tr>";
  }).join("");

  $("ministry-table").innerHTML =
    '<table class="data"><caption class="visually-hidden">General account budget by ' +
    "ministry, " + fy(period) + "</caption><thead><tr>" +
    '<th scope="col">Body</th>' +
    '<th scope="col" class="num">Budgeted, ¥bn</th>' +
    '<th scope="col" class="num">Share of total, %</th>' +
    "</tr></thead><tbody>" + rows +
    "<tr><td><strong>Total</strong></td>" +
    '<td class="num" data-sort="' + (total === null ? "" : total) + '"><strong>' +
      fmtNum(total === null ? null : total / 1000, 1) + "</strong></td>" +
    '<td class="num"><strong>100.0</strong></td></tr>' +
    "</tbody></table>";
  if (window.enhanceTables) window.enhanceTables($("ministry-table"));

  $("ministry-foot").innerHTML = bodies.length +
    " bodies carried a budget in " + fy(period) + ". " + trustBadge("official");
  $("ministry-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Budgeted amounts as published (予算額), shown as ¥bn = ¥mn ÷ 1,000. The smallest " +
    "bodies carry a few billion yen against the Ministry of Finance's tens of trillions, and in " +
    "¥tn every one of them would round to 0.00 and read as nothing.</p>" +
    "<p>" + escapeHtml(SHARE_CALC) + "</p>" +
    "<p>Bodies are listed under the name they had in the year shown. The 2001 reorganisation " +
    "split and merged ministries rather than renaming them, so a body that appears in one year " +
    "and not the next has not moved — it ceased to exist.</p>";
}

/* ---- debt ---- */

let debtChart = null;

function renderDebt() {
  const quarters = periodsOf(DEBT, DEBT_LINES.map(l => l.code));
  const cfg = {
    series: DEBT_LINES.map(l => ({
      name: l.label, slot: l.slot,
      points: quarters.map(p => [qtr(p), tn(at(DEBT, l.code, p), "hm")]),
    })),
    xType: "category", logScale: true,
    unit: "index", unitSuffix: "¥tn", dp: 1,
    yAxisName: "Amount outstanding, ¥ trillion (log scale)", yAxisDp: 0,
    trust: "official", legendFloor: 800,
    sourceLine: "Source: Ministry of Finance — Central Government Debt Outstanding " +
      "(国債及び借入金並びに政府保証債務現在高). Data through " + qtr(D.rel.debt.latest_period) + ".",
  };
  if (debtChart) debtChart.render(cfg); else debtChart = obsChart($("debt-chart"), "line", cfg);
  $("debt-note").textContent = quarters.length
    ? qtr(quarters[0]) + "–" + qtr(quarters[quarters.length - 1]) : "";
  $("debt-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("debt-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Amounts outstanding at quarter end as published, not computed here; shown as ¥tn = " +
    "¥100mn ÷ 10,000. A quarter is dated to the first day of its closing month.</p>" +
    "<p>The y-axis is logarithmic. Total debt is about ¥1,347tn and borrowings about ¥49tn, so " +
    "on a linear axis the three smaller lines lie flat along the bottom and say nothing. Equal " +
    "vertical distances on this axis are equal proportional changes, not equal yen amounts.</p>" +
    "<p>General bonds and FILP bonds are both inside the total, alongside the non-marketable " +
    "issues that are not plotted, so the plotted lines do not sum to it.</p>" +
    "<p>The Ministry publishes only the last five years in one file. The history here is " +
    "stitched across every edition the platform has archived, newest edition winning where two " +
    "cover the same quarter.</p>";
}

/* ---- general government ---- */

let ggChart = null;

function renderGG() {
  const st = urlState();
  const codes = GG_LINES.map(l => st.sector + "." + l.code);
  const years = periodsOf(GG, codes);
  const cfg = {
    series: GG_LINES.map(l => ({
      name: l.label, slot: l.slot,
      points: years.map(p => [fy(p), tn(at(GG, st.sector + "." + l.code, p), "bn")]),
    })),
    xType: "category",
    unit: "index", unitSuffix: "¥tn", dp: 1,
    yAxisName: "¥ trillion", yAxisDp: 0,
    trust: "official", legendFloor: 880, legendBottomNarrow: 92,
    sourceLine: "Source: Cabinet Office — National Accounts (国民経済計算), appended table 6(2). " +
      "Data through " + fy(D.rel.gg.latest_period) + ".",
  };
  if (ggChart) ggChart.render(cfg); else ggChart = obsChart($("gg-chart"), "line", cfg);
  $("gg-note").textContent = years.length
    ? fy(years[0]) + "–" + fy(years[years.length - 1]) : "";
  $("gg-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("gg-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Published amounts on the IMF Government Finance Statistics classification, not computed " +
    "here; shown as ¥tn = ¥bn ÷ 1,000.</p>" +
    "<p>Net lending is the Cabinet Office's own balancing item, taken from the source rather " +
    "than derived by subtracting revenue from expense. It is signed: negative is net borrowing.</p>" +
    "<p>General government is central government plus local government plus the social security " +
    "funds, with the transfers between them netted out. The three sectors therefore do not add " +
    "to it without the consolidation adjustment, which is a series of its own in the dataset.</p>";
}

/* ---- prefectures ---- */

function renderPrefYears() {
  const periods = periodsOf(PREF, ["total.revenue"]);
  const st = urlState();
  const chosen = periods.indexOf(st.prefYear) >= 0 ? st.prefYear : periods[periods.length - 1];
  $("pref-year").innerHTML = periods.slice().reverse()
    .map(p => '<option value="' + p + '"' + (p === chosen ? " selected" : "") + ">" +
      fy(p) + "</option>").join("");
  return chosen;
}

function renderPref() {
  const period = renderPrefYears();
  const rows = D.prefCodes.map(code => {
    const name = (D.names[PREF + "|" + code + ".revenue"] || code).split(" — ")[0];
    const rev = at(PREF, code + ".revenue", period);
    const exp = at(PREF, code + ".expenditure", period);
    const real = at(PREF, code + ".real-balance", period);
    const margin = rev ? (real === null ? null : (real / rev) * 100) : null;
    return "<tr><td>" + escapeHtml(name) + "</td>" +
      '<td class="num" data-sort="' + (rev === null ? "" : rev) + '">' +
        fmtNum(tn(rev, "k"), 2) + "</td>" +
      '<td class="num" data-sort="' + (exp === null ? "" : exp) + '">' +
        fmtNum(tn(exp, "k"), 2) + "</td>" +
      '<td class="num" data-sort="' + (real === null ? "" : real) + '">' +
        fmtSigned(real === null ? null : real / 1e6, 1) + "</td>" +
      '<td class="num" data-sort="' + (margin === null ? "" : margin) + '">' +
        fmtNum(margin, 2) + "</td></tr>";
  }).join("");

  const rev = at(PREF, "total.revenue", period);
  const exp = at(PREF, "total.expenditure", period);
  const real = at(PREF, "total.real-balance", period);
  $("pref-table").innerHTML =
    '<table class="data"><caption class="visually-hidden">Prefecture settled accounts, ' +
    fy(period) + "</caption><thead><tr>" +
    '<th scope="col">Prefecture</th>' +
    '<th scope="col" class="num">Revenue, ¥tn</th>' +
    '<th scope="col" class="num">Expenditure, ¥tn</th>' +
    '<th scope="col" class="num">Real balance, ¥bn</th>' +
    '<th scope="col" class="num">Real balance, % of revenue</th>' +
    "</tr></thead><tbody>" + rows +
    "<tr><td><strong>All prefectures</strong></td>" +
    '<td class="num"><strong>' + fmtNum(tn(rev, "k"), 2) + "</strong></td>" +
    '<td class="num"><strong>' + fmtNum(tn(exp, "k"), 2) + "</strong></td>" +
    '<td class="num"><strong>' + fmtSigned(real === null ? null : real / 1e6, 1) + "</strong></td>" +
    '<td class="num"><strong>' + fmtNum(rev ? (real / rev) * 100 : null, 2) + "</strong></td>" +
    "</tr></tbody></table>";
  if (window.enhanceTables) window.enhanceTables($("pref-table"));

  $("pref-foot").innerHTML = "All 47 prefectures settled ¥" + fmtNum(tn(exp, "k"), 1) +
    "tn of expenditure in " + fy(period) + ". " + trustBadge("official");
  $("pref-calc").innerHTML = "<summary>Show calculation</summary>" +
    "<p>Revenue, expenditure and 実質収支 are published by the Ministry in ¥ thousand; shown " +
    "here as ¥tn = ¥1,000 ÷ 1,000,000,000 and ¥bn = ¥1,000 ÷ 1,000,000.</p>" +
    "<p>Real balance as a share of revenue = (real balance ÷ revenue) × 100, from published " +
    "values. It is a share of a level, so it is a percentage and not a percentage-point change.</p>" +
    "<p>実質収支 is the difference between revenue and expenditure less the funds already " +
    "committed to the next year's carried-over projects. It is the Ministry's own figure, not " +
    "computed here.</p>";
}

/* ---- CSV exports ---- */

function csvHeader(lines) { return lines.map(l => "# " + l); }

function downloadCSV(filename, headerLines, columns, rows) {
  let csv = csvHeader(headerLines).join("\n") + "\n";
  csv += columns.join(",") + "\n";
  rows.forEach(r => { csv += r.join(",") + "\n"; });
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function quote(s) { return '"' + String(s).replace(/"/g, '""') + '"'; }

/* ---- wiring ---- */

function fillSelect(el, options, chosen) {
  el.innerHTML = options.map(o => '<option value="' + o.id + '"' +
    (o.id === chosen ? " selected" : "") + ">" + escapeHtml(o.label) + "</option>").join("");
}

function wireSeg(id, key, render) {
  Array.from($(id).querySelectorAll("button")).forEach(b => {
    b.addEventListener("click", () => {
      Array.from($(id).querySelectorAll("button")).forEach(x => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      const next = {}; next[key] = b.dataset.range;
      setUrlState(next);
      render();
    });
  });
  const current = urlState()[key];
  Array.from($(id).querySelectorAll("button")).forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.range === current)));
}

function wireSelect(id, key, render) {
  $(id).addEventListener("change", () => {
    const next = {}; next[key] = $(id).value;
    setUrlState(next);
    render();
  });
}

async function main() {
  // Releases first: every section's as-of and the stale banner come from them.
  const overviews = await Promise.all(DATASETS.map(([, api]) => getJSON(api + "/overview")));
  DATASETS.forEach(([key], i) => {
    D.rel[key] = overviews[i].release;
    D.stale[key] = overviews[i].stale;
  });
  renderStale();

  // Series lists give the ministry and prefecture codes and their names, so
  // neither list is hard-coded on the page.
  // The tax list says which sides each tax has: "Other taxes" has a
  // settlement but no budget line, and asking for one fails a whole batch.
  const [minList, prefList, taxList] = await Promise.all([
    getJSON(MIN + "/series?q="), getJSON(PREF + "/series?q="), getJSON(TAX + "/series?q="),
  ]);
  const taxHeld = new Set(taxList.series.map(s => s.code));
  D.ministryCodes = minList.series
    .filter(s => s.code.endsWith(".budget"))
    .map(s => s.code.slice(0, -".budget".length));
  D.prefCodes = prefList.series
    .filter(s => s.code.endsWith(".revenue") && s.code !== "total.revenue")
    .map(s => s.code.slice(0, -".revenue".length));

  await Promise.all([
    loadSeries(ACC, ACCOUNT_SERIES.map(s => s.code)),
    loadSeries(REV, REVENUE_HEADS.flatMap(h => REVENUE_MEASURES.map(m => h.code + "." + m.id))),
    loadSeries(EXP, SPEND_HEADS.flatMap(h => SPEND_MEASURES.map(m => h.code + "." + m.id))),
    loadSeries(MIN, D.ministryCodes.map(c => c + ".budget")),
    loadSeries(TAX, TAX_ROWS.filter(r => r[1] !== null)
      .flatMap(r => [r[0] + ".budget", r[0] + ".settlement"]).filter(c => taxHeld.has(c))),
    loadSeries(DEBT, DEBT_LINES.map(l => l.code)),
    loadSeries(GG, GG_SECTORS.flatMap(s => GG_LINES.map(l => s.id + "." + l.code))),
    loadSeries(PREF, D.prefCodes.concat(["total"])
      .flatMap(c => [c + ".revenue", c + ".expenditure", c + ".real-balance"])),
  ]);

  renderHead();
  renderTiles();

  const st = urlState();
  fillSelect($("revenue-measure"), REVENUE_MEASURES, st.revenue);
  fillSelect($("spend-measure"), SPEND_MEASURES, st.spend);
  fillSelect($("gg-sector"), GG_SECTORS, st.sector);

  renderAccount(); renderRevenue(); renderTax();
  renderSpend(); renderMinistry(); renderDebt(); renderGG(); renderPref();
  renderProvenance();

  wireSeg("account-range", "range", renderAccount);
  wireSelect("revenue-measure", "revenue", renderRevenue);
  wireSelect("spend-measure", "spend", renderSpend);
  wireSelect("gg-sector", "sector", renderGG);
  wireSelect("tax-year", "taxYear", renderTax);
  wireSelect("ministry-year", "ministryYear", renderMinistry);
  wireSelect("pref-year", "prefYear", renderPref);

  $("account-png").addEventListener("click", () => accountChart.exportPNG("japan-general-account.png"));
  $("account-csv").addEventListener("click", () => accountChart.exportCSV(
    "japan-general-account.csv", csvHeader([
      "Japan general account — budget and settlement, ¥ trillion",
      "Source: Ministry of Finance — Fiscal Statistics (財政統計), Table 1",
      "Trust: official statistic, as published",
      "Published in ¥ million; ¥tn = ¥mn ÷ 1,000,000",
      "Fiscal year dated to the 1 April it begins on",
      "Data through " + fy(D.rel.acc.latest_period),
    ])));
  $("revenue-png").addEventListener("click", () => revenueChart.exportPNG("japan-revenue-by-head.png"));
  $("revenue-csv").addEventListener("click", () => revenueChart.exportCSV(
    "japan-revenue-by-head.csv", csvHeader([
      "Japan general account revenue by head, ¥ trillion",
      "Source: Ministry of Finance — Fiscal Statistics (財政統計), Tables 3 and 4",
      "Trust: official statistic, as published",
      "Measure: " + (REVENUE_MEASURES.find(m => m.id === urlState().revenue) || {}).label,
      "Published in ¥ million; ¥tn = ¥mn ÷ 1,000,000",
      "Data through " + fy(D.rel.rev.latest_period),
    ])));
  $("spend-png").addEventListener("click", () => spendChart.exportPNG("japan-spending-by-purpose.png"));
  $("spend-csv").addEventListener("click", () => spendChart.exportCSV(
    "japan-spending-by-purpose.csv", csvHeader([
      "Japan general account spending by policy purpose, ¥ trillion",
      "Source: Ministry of Finance — Fiscal Statistics (財政統計), Tables 19(2) and 20",
      "Trust: official statistic, as published",
      "Measure: " + (SPEND_MEASURES.find(m => m.id === urlState().spend) || {}).label,
      "Published in ¥ million; ¥tn = ¥mn ÷ 1,000,000",
      "Six of about fourteen headings; they do not sum to total spending",
      "Data through " + fy(D.rel.exp.latest_period),
    ])));
  $("debt-png").addEventListener("click", () => debtChart.exportPNG("japan-government-debt.png"));
  $("debt-csv").addEventListener("click", () => debtChart.exportCSV(
    "japan-government-debt.csv", csvHeader([
      "Japan central government debt outstanding, ¥ trillion",
      "Source: Ministry of Finance — 国債及び借入金並びに政府保証債務現在高",
      "Trust: official statistic, as published",
      "Published in ¥100mn; ¥tn = ¥100mn ÷ 10,000",
      "Stock at quarter end, dated to the first day of the closing month",
      "Data through " + qtr(D.rel.debt.latest_period),
    ])));
  $("gg-png").addEventListener("click", () => ggChart.exportPNG("japan-general-government.png"));
  $("gg-csv").addEventListener("click", () => ggChart.exportCSV(
    "japan-general-government.csv", csvHeader([
      "Japan general government on the GFS basis, ¥ trillion",
      "Source: Cabinet Office — National Accounts (国民経済計算), appended table 6(2)",
      "Trust: official statistic, as published",
      "Sector: " + (GG_SECTORS.find(s => s.id === urlState().sector) || {}).label,
      "Published in ¥ billion; ¥tn = ¥bn ÷ 1,000",
      "Data through " + fy(D.rel.gg.latest_period),
    ])));

  $("tax-csv").addEventListener("click", () => {
    const period = $("tax-year").value;
    downloadCSV("japan-tax-receipts-" + fy(period) + ".csv", [
      "Japan national tax receipts against budget, " + fy(period) + ", ¥ million as published",
      "Source: Ministry of Finance — 租税及び印紙収入決算額調",
      "Trust: budgeted and collected are official statistics as published",
      "difference and outturn are calculated: " + DIFF_CALC,
    ], ["tax", "block", "budgeted_jpy_mn", "collected_jpy_mn", "difference_jpy_mn", "outturn_pct"],
      TAX_ROWS.filter(r => r[1] !== null).map(r => {
        const b = at(TAX, r[0] + ".budget", period), a = at(TAX, r[0] + ".settlement", period);
        return [quote(r[1]), quote(r[0].split(".")[0]),
                b === null ? "" : b, a === null ? "" : a,
                b === null || a === null ? "" : a - b,
                b === null || a === null || !b ? "" : ((a / b) * 100).toFixed(1)];
      }));
  });

  $("ministry-csv").addEventListener("click", () => {
    const period = $("ministry-year").value;
    const total = at(MIN, "total.budget", period);
    downloadCSV("japan-spending-by-ministry-" + fy(period) + ".csv", [
      "Japan general account budget by ministry, " + fy(period) + ", ¥ million as published",
      "Source: Ministry of Finance — Fiscal Statistics (財政統計), Table 5",
      "Trust: the budgeted amount is an official statistic as published",
      "share is calculated: " + SHARE_CALC,
      "Bodies are named as they were in the year shown",
    ], ["body", "series_code", "budgeted_jpy_mn", "share_pct"],
      D.ministryCodes.filter(c => c !== "total").map(code => {
        const v = at(MIN, code + ".budget", period);
        const name = (D.names[MIN + "|" + code + ".budget"] || code).split(" — ")[0];
        return [quote(name), quote(code), v === null ? "" : v,
                v === null || !total ? "" : ((v / total) * 100).toFixed(2)];
      }).filter(r => r[2] !== ""));
  });

  $("pref-csv").addEventListener("click", () => {
    const period = $("pref-year").value;
    downloadCSV("japan-prefecture-finances-" + fy(period) + ".csv", [
      "Japanese prefecture settled accounts, " + fy(period) + ", ¥ thousand as published",
      "Source: Ministry of Internal Affairs and Communications — 都道府県決算状況調, Table 1",
      "Trust: revenue, expenditure and the real balance are official statistics as published",
      "real balance as a share of revenue is calculated: (real balance ÷ revenue) × 100",
    ], ["prefecture", "jis_code", "revenue_jpy_1000", "expenditure_jpy_1000",
        "real_balance_jpy_1000", "real_balance_pct_of_revenue"],
      D.prefCodes.map(code => {
        const name = (D.names[PREF + "|" + code + ".revenue"] || code).split(" — ")[0];
        const rev = at(PREF, code + ".revenue", period);
        const exp = at(PREF, code + ".expenditure", period);
        const real = at(PREF, code + ".real-balance", period);
        return [quote(name), quote(code), rev === null ? "" : rev, exp === null ? "" : exp,
                real === null ? "" : real,
                rev && real !== null ? ((real / rev) * 100).toFixed(2) : ""];
      }));
  });

  window.addEventListener("resize", () => {
    [accountChart, revenueChart, spendChart, debtChart, ggChart]
      .forEach(c => { if (c) c.render(); });
  });
  if (window.initThemeToggle) {
    initThemeToggle(() => {
      [accountChart, revenueChart, spendChart, debtChart, ggChart]
        .forEach(c => { if (c) c.render(); });
    });
  }
}

main().catch(err => {
  console.error(err);
  $("page-sub").textContent = "This page could not load its data. Try again in a moment.";
});
