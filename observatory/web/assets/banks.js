/* Japanese banks. The question this screen answers: "How are Japan's banks
   doing — bad loans, earnings — and what does any one bank's balance sheet
   look like?"

   Three datasets, one page: the FSA's bad-loan disclosure by bank group
   (fsa-npl), the FSA's results summary for the major and regional banks
   (fsa-bank-results), and the JBA's per-bank statements (jba-banks). Every
   figure is an official value as published; the only calculations here are
   changes, and they carry their formula on the page and in every export. A
   value a source does not publish is "—", never zero. */
"use strict";

const NPL = "/api/v1/fsa-npl";
const RES = "/api/v1/fsa-bank-results";
const JBA = "/api/v1/jba-banks";
const LOAN = "/api/v1/boj-loan-rates";
const DEP = "/api/v1/boj-deposit-rates";
const BANKS = "/api/v1/equity/banks";
const BOJ_CREDIT = "This service uses the API provided by the 'Bank of Japan Time-Series Data Search.' " +
  "The Bank of Japan does not guarantee the content of the service.";

/* New-loan rates by lender, then the posted ordinary-deposit rate. Five
   lines: the ceiling is six. */
const RATE_SERIES = [
  { api: LOAN, code: "DLLR2CICBNL1", label: "New loans, city banks", slot: 1 },
  { api: LOAN, code: "DLLR2CIRBNL1", label: "New loans, regional banks", slot: 2 },
  { api: LOAN, code: "DLLR2CIMBNL1", label: "New loans, regional banks II", slot: 3 },
  { api: LOAN, code: "DLLR2CICR35", label: "New loans, shinkin banks", slot: 4 },
  { api: DEP, code: "DLDR121N", label: "Ordinary deposits, posted", slot: 5 },
];
const BUCKET_KEYS = ["within_1y_yen", "y1_3_yen", "y3_5_yen", "y5_7_yen", "y7_10_yen", "over_10y_yen"];
const SCENARIO_LABELS = { parallel_up: "Parallel up", parallel_down: "Parallel down", steepener: "Steepener",
  flattener: "Flattener", short_up: "Short rate up", short_down: "Short rate down", max: "Largest of the six" };

/* Bad-loan ratio series shown, in slot order. Six is the platform's ceiling
   for one line chart; the two combined groups (major, all deposit-takers)
   are left to the API. */
const NPL_GROUPS = [
  { code: "all-banks", label: "All banks", slot: 1 },
  { code: "city", label: "City banks", slot: 2 },
  { code: "regional-1", label: "Regional banks (first association)", slot: 3 },
  { code: "regional-2", label: "Regional banks (second association)", slot: 4 },
  { code: "shinkin", label: "Shinkin banks", slot: 5 },
  { code: "shinkumi", label: "Credit co-operatives", slot: 6 },
];

/* Earnings measures: the same FSA line for both groups except net business
   profit, where the majors' consolidated 業務純益 and the regionals'
   non-consolidated 実質業務純益 are different measures and are named so. */
const MEASURES = {
  "net-income": { label: "Net income",
    major: ["major.net-income.fy", "Major banks (attributable to parent)"],
    regional: ["regional.net-income.fy", "Regional banks"] },
  "net-business-profit": { label: "Net business profit",
    major: ["major.net-business-profit.fy", "Major banks (consolidated net business profit)"],
    regional: ["regional.real-net-business-profit.fy", "Regional banks (real net business profit)"] },
  "credit-costs": { label: "Credit costs",
    major: ["major.credit-costs.fy", "Major banks"],
    regional: ["regional.credit-costs.fy", "Regional banks"] },
  "net-interest-income": { label: "Net interest income",
    major: ["major.net-interest-income.fy", "Major banks"],
    regional: ["regional.net-interest-income.fy", "Regional banks"] },
};

/* The principal lines of a statement, by basis, in the association's own
   order. The line code is the JBA reference code; the income statement is
   read on the fiscal-year (.fy) series. */
const LINES = {
  s: [
    ["Balance sheet", null],
    ["a015", "Deposits"], ["a135", "Negotiable certificates of deposit"],
    ["a345", "Borrowed money"], ["a480", "Bonds payable"], ["a980", "Total liabilities"],
    ["b050", "Share capital"], ["b300", "Retained earnings"],
    ["b700", "Valuation difference on available-for-sale securities"], ["b950", "Total net assets"],
    ["d005", "Cash and due from banks"], ["d275", "Securities"], ["d290", "of which government bonds", 1],
    ["d350", "of which stocks", 1], ["d395", "Loans and bills discounted"],
    ["d965", "Allowance for loan losses"], ["d990", "Total assets"],
    ["Income statement, fiscal year", null],
    ["e010.fy", "Ordinary income"], ["e020.fy", "Interest income", 1], ["e030.fy", "of which interest on loans", 2],
    ["e120.fy", "Fees and commissions", 1], ["e340.fy", "Ordinary expenses"], ["e350.fy", "Interest expenses", 1],
    ["e360.fy", "of which interest on deposits", 2], ["e680.fy", "General and administrative expenses", 1],
    ["e730.fy", "Provision of allowance for loan losses", 1], ["k400.fy", "Net business profit"],
    ["e800.fy", "Ordinary profit"], ["f450.fy", "Profit before income taxes"], ["f850.fy", "Net income"],
  ],
  c: [
    ["Balance sheet", null],
    ["a010", "Deposits"], ["a050", "Negotiable certificates of deposit"], ["a330", "Borrowed money"],
    ["a450", "Bonds payable"], ["a950", "Total liabilities"], ["b010", "Share capital"],
    ["b190", "Retained earnings"], ["b430", "Valuation difference on available-for-sale securities"],
    ["b970", "Total net assets"], ["d010", "Cash and due from banks"], ["d250", "Securities"],
    ["d310", "Loans and bills discounted"], ["d910", "Allowance for loan losses"], ["d970", "Total assets"],
    ["Income statement, fiscal year", null],
    ["e010.fy", "Ordinary income"], ["e025.fy", "Interest income", 1], ["e190.fy", "Fees and commissions", 1],
    ["e350.fy", "Ordinary expenses"], ["e380.fy", "Interest expenses", 1],
    ["e860.fy", "General and administrative expenses", 1], ["e920.fy", "Provision of allowance for loan losses", 1],
    ["e970.fy", "Ordinary profit"], ["f490.fy", "Profit before income taxes"], ["f790.fy", "Net income"],
    ["f940.fy", "Net income attributable to owners of parent"],
  ],
};
const DEPOSITS = { s: "a015", c: "a010" };
const LOANS = { s: "d395", c: "d310" };

const CHANGE_CALC =
  "change[t] = value[t] − value[t−12 months], from published values; percent change = " +
  "(value[t] ÷ value[t−12 months] − 1) × 100. A period missing either side shows —, never zero.";

function $(id) { return document.getElementById(id); }

const D = { rel: {}, stale: false, series: {}, banks: [], consolidated: {} };

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

/* Series are cached under "<api>|<code>". A code the release does not carry
   404s the whole batch, so unknown codes are requested one at a time and an
   absent series is stored as empty — it renders "—", never a zero. */
async function loadSeries(api, codes) {
  const want = codes.filter(c => !((api + "|" + c) in D.series));
  for (const chunk of batchCodes(want)) {
    try {
      const payload = await getJSON(api + "/observations?series=" + encodeURIComponent(chunk.join(",")));
      payload.series.forEach(s => { D.series[api + "|" + s.code] = s.points; });
    } catch (err) {
      if (chunk.length === 1) { D.series[api + "|" + chunk[0]] = []; continue; }
      for (const one of chunk) await loadSeries(api, [one]);
    }
  }
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

/* "2026-03-01" -> "Mar 2026"; a half-year is dated to its closing month. */
function half(period) {
  if (!period) return MISSING;
  return (period.slice(5, 7) === "03" ? "Mar " : "Sep ") + period.slice(0, 4);
}
/* "2026-03-01" -> "FY2025": a March point closes the fiscal year that began
   the April before. */
function fyOf(period) { return period ? "FY" + (Number(period.slice(0, 4)) - 1) : MISSING; }
function yearBefore(period) { return String(Number(period.slice(0, 4)) - 1) + period.slice(4); }

/* ---- URL state ---- */

function urlState() {
  const p = new URLSearchParams(location.search);
  return {
    range: p.get("range") === "10" ? "10" : "max",
    rates: p.get("rates") === "10" ? "10" : "max",
    measure: MEASURES[p.get("measure")] ? p.get("measure") : "net-income",
    bank: p.get("bank") || "0001",
    basis: p.get("basis") === "c" ? "c" : "s",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  if (s.range !== "max") p.set("range", s.range);
  if (s.rates !== "max") p.set("rates", s.rates);
  if (s.measure !== "net-income") p.set("measure", s.measure);
  if (s.bank !== "0001") p.set("bank", s.bank);
  if (s.basis !== "s") p.set("basis", s.basis);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

/* ---- head, staleness, provenance ---- */

function renderHead() {
  const latest = D.rel.npl.latest_period;
  $("page-asof").textContent = "Bad loans through " + half(latest) +
    " · results through " + half(D.rel.res.latest_period) +
    " · statements through " + half(D.rel.jba.latest_period);
  $("header-asof").textContent = half(latest);
  const ratio = at(NPL, "all-banks.npl-ratio", latest);
  const claims = at(NPL, "all-banks.disclosed", latest);
  const majorPeriod = latestOf(RES, "major.net-income.fy");
  const major = at(RES, "major.net-income.fy", majorPeriod);
  $("page-sub").innerHTML =
    "At " + half(latest) + " the banks' bad-loan ratio was <strong class=\"num\">" +
    fmtRate(ratio, 1) + "</strong> of total credit, on ¥" +
    fmtNum(claims === null ? null : claims / 10000, 1) + "tn of disclosed claims. The major " +
    "banks earned <strong class=\"num\">¥" + fmtNum(major === null ? null : major / 10, 0) +
    "bn</strong> in " + fyOf(majorPeriod) + ". " + trustBadge("official");
}

function renderStale() {
  const el = $("stale-banner");
  const stale = ["npl", "res", "jba"].filter(k => D.stale[k]);
  if (!stale.length) { el.innerHTML = ""; return; }
  const names = { npl: "bad-loan disclosure", res: "results summary", jba: "bank statements" };
  el.innerHTML = '<div class="banner" role="alert">This surface is stale: the ' +
    escapeHtml(stale.map(k => names[k] + " (through " + half(D.rel[k].latest_period) + ")").join(", ")) +
    " " + (stale.length > 1 ? "are" : "is") + " older than the source's own cadence allows. " +
    "If this persists, run the ingestion.</div>";
}

function provCard(rel, title, sub) {
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
        '<div class="prov-value">Data through ' + escapeHtml(half(rel.latest_period)) + "</div>" +
        '<div class="prov-sub">Published half-yearly</div>' +
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
  $("prov-cards").innerHTML =
    provCard(D.rel.npl, "Bad Loans by Bank Group",
      "Table 1 of every FSA release since 2006, archived together under one checksum; " +
      "the newest release wins where two overlap.") +
    provCard(D.rel.res, "Bank Earnings by Group",
      "The FSA's two long-run workbooks (major banks, regional banks), archived together.") +
    provCard(D.rel.jba, "Bank Financial Statements",
      "The aggregate and per-bank workbooks of every JBA edition since fiscal 2006, " +
      "archived together under one checksum.");
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

function renderTiles() {
  const latest = D.rel.npl.latest_period;
  const prior = yearBefore(latest);
  const ratio = at(NPL, "all-banks.npl-ratio", latest), ratioWas = at(NPL, "all-banks.npl-ratio", prior);
  const claims = at(NPL, "all-banks.disclosed", latest), claimsWas = at(NPL, "all-banks.disclosed", prior);
  const dRatio = delta(ratio === null || ratioWas === null ? null : ratio - ratioWas, 1, "pp");
  const dClaims = delta(claims === null || claimsWas === null ? null : (claims - claimsWas) / 10000, 2, "¥tn");
  const cells = [
    tileCell("Bad-Loan Ratio, All Banks",
      fmtNum(ratio, 1) + '<span class="unit">%</span>', dRatio.html, dRatio.dir,
      "Disclosed claims as a share of total credit, all banks, " + half(latest) + ", as published by the FSA."),
    tileCell("Disclosed Claims, All Banks",
      fmtNum(claims === null ? null : claims / 10000, 1) + '<span class="unit">¥tn</span>',
      dClaims.html, dClaims.dir,
      "Claims disclosed under the Financial Reconstruction Act, all banks, published in ¥100mn and shown in ¥tn."),
  ];
  [["major", "Major Banks Net Income"], ["regional", "Regional Banks Net Income"]].forEach(([g, label]) => {
    const code = g + ".net-income.fy";
    const p = latestOf(RES, code);
    const now = at(RES, code, p), was = p ? at(RES, code, yearBefore(p)) : null;
    const d = delta(now === null || was === null ? null : (now - was) / 10, 0, "¥bn");
    cells.push(tileCell(label + ", " + fyOf(p),
      fmtSigned(now === null ? null : now / 10, 0) + '<span class="unit">¥bn</span>', d.html, d.dir,
      label + " for the fiscal year, published in ¥100mn and shown in ¥bn."));
  });
  $("tiles").innerHTML = cells.join("");
  $("strip-foot").textContent =
    "Changes are against the same half-year a year earlier (ratio in percentage points) or the " +
    "prior fiscal year · ¥tn = published ¥100mn ÷ 10,000 and ¥bn = ¥100mn ÷ 10, exact conversions";
  $("strip-calc").style.display = "";
  $("strip-calc").innerHTML = "<summary>Show calculation</summary><p>" + escapeHtml(CHANGE_CALC) +
    "</p><p>Ratios and amounts are published values, not recomputed.</p>";
}

/* ---- bad-loan ratio by group ---- */

let nplChart = null;

function nplPeriods() {
  const set = new Set();
  NPL_GROUPS.forEach(g => pts(NPL, g.code + ".npl-ratio").forEach(p => set.add(p[0])));
  const all = Array.from(set).sort();
  return urlState().range === "10" ? all.slice(-20) : all;
}

function renderNpl() {
  const periods = nplPeriods();
  const cfg = {
    series: NPL_GROUPS.map(g => ({
      name: g.label, slot: g.slot,
      points: periods.map(p => [half(p), at(NPL, g.code + ".npl-ratio", p)]),
    })),
    xType: "category", unit: "%", dp: 1,
    yAxisName: "Bad-loan ratio, % of total credit", yAxisDp: 0,
    trust: "official", legendFloor: 900, legendBottomNarrow: 118,
    sourceLine: "Source: FSA — Status of claims disclosed under the Financial Reconstruction Act " +
      "(金融再生法開示債権の状況等). Data through " + half(D.rel.npl.latest_period) + ".",
  };
  if (nplChart) nplChart.render(cfg); else nplChart = obsChart($("npl-chart"), "line", cfg);
  $("npl-note").textContent = periods.length ? half(periods[0]) + "–" + half(periods[periods.length - 1]) : "";
  $("npl-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("npl-calc").innerHTML = "<summary>Show calculation</summary><p>Published ratio (不良債権比率) " +
    "for each lender group, as released by the FSA. Not computed here. Groups by bank type " +
    "separately begin at March 2002; all banks begin at March 1999. A half-year is dated to its " +
    "closing month.</p>";
}

/* ---- earnings by group ---- */

let earnChart = null;

function renderEarn() {
  const m = MEASURES[urlState().measure];
  const set = new Set();
  [m.major[0], m.regional[0]].forEach(c => pts(RES, c).forEach(p => set.add(p[0])));
  const periods = Array.from(set).sort();
  const cfg = {
    categories: periods.map(fyOf),
    series: [
      { name: m.major[1], slot: 1, points: periods.map(p => { const v = at(RES, m.major[0], p); return v === null ? null : v / 10; }) },
      { name: m.regional[1], slot: 3, points: periods.map(p => { const v = at(RES, m.regional[0], p); return v === null ? null : v / 10; }) },
    ],
    dp: 0, unitSuffix: "¥bn", yAxisName: m.label + ", ¥bn", trust: "official", legendFloor: 900,
    sourceLine: "Source: FSA — Summary of bank financial results (主要行等・地域銀行の決算の概要). " +
      "Data through " + fyOf(D.rel.res.latest_period) + ".",
  };
  if (earnChart) earnChart.render(cfg); else earnChart = obsChart($("earn-chart"), "cols", cfg);
  $("earn-note").textContent = periods.length ? fyOf(periods[0]) + "–" + fyOf(periods[periods.length - 1]) : "";
  $("earn-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("earn-calc").innerHTML = "<summary>Show calculation</summary><p>" + escapeHtml(m.label) +
    " for the fiscal year as published by the FSA (the 3月期 figure), in ¥100mn, shown in ¥bn — " +
    "an exact division by 10. Credit costs carry the FSA's sign: a negative is a cost. The " +
    "major-bank figure is consolidated; the regional-bank figure is not.</p>";
}

/* ---- one bank's statement ---- */

let bankChart = null;

function bankLabel(code) {
  const b = D.banks.find(x => x.code === code);
  return b ? b.name : code;
}

function seriesCode(basis, bank, line) { return basis + "." + bank + "." + line; }

async function loadBank() {
  const st = urlState();
  const codes = LINES[st.basis].filter(l => l[1] !== null).map(l => seriesCode(st.basis, st.bank, l[0]));
  await loadSeries(JBA, codes);
}

function bankPeriods(basis, bank) {
  const set = new Set();
  pts(JBA, seriesCode(basis, bank, DEPOSITS[basis])).forEach(p => set.add(p[0]));
  return Array.from(set).sort();
}

function renderBank() {
  const st = urlState();
  const periods = bankPeriods(st.basis, st.bank);
  const marches = periods.filter(p => p.slice(5, 7) === "03");
  const latest = marches.length ? marches[marches.length - 1] : null;
  const prior = latest ? yearBefore(latest) : null;
  const name = bankLabel(st.bank) + (st.basis === "c" ? " (consolidated)" : "");
  $("bank-note").textContent = name;
  if (!latest) {
    $("bank-table").innerHTML = '<p class="table-foot">No ' + (st.basis === "c" ? "consolidated" : "non-consolidated") +
      " statements are published for this bank.</p>";
    $("bank-foot").textContent = "";
    $("bank-calc").innerHTML = "";
    return;
  }
  const head = "<thead><tr>" +
    '<th scope="col">Line</th>' +
    '<th scope="col" class="num">' + fyOf(latest) + " (" + half(latest) + "), ¥bn</th>" +
    '<th scope="col" class="num">' + fyOf(prior) + ", ¥bn</th>" +
    '<th scope="col" class="num">Change, ¥bn</th>' +
    '<th scope="col" class="num">Change, %</th>' +
    "</tr></thead>";
  const body = LINES[st.basis].map(([line, label, indent]) => {
    if (label === null) {
      return '<tr><th scope="rowgroup" colspan="5" style="text-align:left">' + escapeHtml(line) + "</th></tr>";
    }
    const c = seriesCode(st.basis, st.bank, line);
    const now = at(JBA, c, latest), was = at(JBA, c, prior);
    const change = (now === null || was === null) ? null : now - was;
    const pct = (change === null || !was) ? null : (change / Math.abs(was)) * 100;
    return "<tr>" +
      '<td' + (indent ? ' class="indent-' + indent + '"' : "") + '>' + escapeHtml(label) +
        ' <span class="muted">' + escapeHtml(line.replace(".fy", "")) + "</span></td>" +
      '<td class="num" data-sort="' + (now ?? "") + '">' + fmtNum(now === null ? null : now / 1000, 1) + "</td>" +
      '<td class="num" data-sort="' + (was ?? "") + '">' + fmtNum(was === null ? null : was / 1000, 1) + "</td>" +
      '<td class="num" data-sort="' + (change ?? "") + '">' + fmtSigned(change === null ? null : change / 1000, 1) + "</td>" +
      '<td class="num" data-sort="' + (pct ?? "") + '">' + (pct === null ? MISSING : fmtSigned(pct, 1, "%")) + "</td>" +
      "</tr>";
  }).join("");
  $("bank-table").innerHTML = '<table class="data" data-no-enhance>' + head + "<tbody>" + body + "</tbody></table>";
  $("bank-foot").textContent =
    "Published amounts in millions of yen, shown in ¥bn (÷ 1,000). The code beside each line is the " +
    "association's reference code — the key to the full statement in the API. A line the association " +
    "does not publish for this bank shows — and is not a zero. Release: statements through " +
    half(D.rel.jba.latest_period) + ".";
  $("bank-calc").innerHTML = "<summary>Show calculation</summary><p>" + escapeHtml(CHANGE_CALC) +
    "</p><p>Balance-sheet lines are the end of the fiscal year (March); income-statement lines are " +
    "the full fiscal year. Amounts are published values, not recomputed.</p>";

  const cfg = {
    series: [
      { name: "Deposits", slot: 1, points: periods.map(p => { const v = at(JBA, seriesCode(st.basis, st.bank, DEPOSITS[st.basis]), p); return [half(p), v === null ? null : v / 1e6]; }) },
      { name: "Loans and bills discounted", slot: 2, points: periods.map(p => { const v = at(JBA, seriesCode(st.basis, st.bank, LOANS[st.basis]), p); return [half(p), v === null ? null : v / 1e6]; }) },
    ],
    xType: "category", unit: "index", unitSuffix: "¥tn", dp: 2,
    yAxisName: "¥tn, end of half-year", yAxisDp: 1, trust: "official",
    sourceLine: "Source: JBA — Analysis of Financial Statements of All Banks (全国銀行財務諸表分析). " +
      name + ". Data through " + half(D.rel.jba.latest_period) + ".",
  };
  if (bankChart) bankChart.render(cfg); else bankChart = obsChart($("bank-chart"), "line", cfg);
  $("bank-chart-title").textContent = "Deposits and Loans — " + name;
  $("bank-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("bank-chart-calc").innerHTML = "<summary>Show calculation</summary><p>Deposits (預金) and loans " +
    "and bills discounted (貸出金) at the end of each half-year, as published in millions of yen; " +
    "¥tn = millions ÷ 1,000,000, an exact conversion. A half-year the bank did not report is a gap " +
    "in the line.</p>";
}

function bankCSV() {
  const st = urlState();
  const periods = bankPeriods(st.basis, st.bank);
  const marches = periods.filter(p => p.slice(5, 7) === "03");
  if (!marches.length) return;
  const latest = marches[marches.length - 1], prior = yearBefore(latest);
  const name = bankLabel(st.bank) + (st.basis === "c" ? " (consolidated)" : "");
  const header = [
    "Plover Analytics — bank financial statement, principal lines",
    "Bank: " + name + " (金融機関コード " + st.bank + ", basis " + (st.basis === "c" ? "consolidated" : "non-consolidated") + ")",
    "Source: " + (D.rel.jba.source_name || ""),
    "Release: statements through " + half(D.rel.jba.latest_period),
    "Retrieved: " + fmtStamp(D.rel.jba.retrieved_at) + " · SHA-256 " + (D.rel.jba.sha256 || ""),
    "Unit: millions of yen, exactly as published (not the ¥bn shown on screen)",
    "Trust: amounts are official statistics as published; the change is calculated from them",
    CHANGE_CALC,
    "An empty cell is a line the association does not publish for this bank — never zero",
  ].map(l => "# " + l).join("\n");
  const rows = ["line_code,label," + latest + "_jpy_million," + prior + "_jpy_million,change_jpy_million,change_pct"];
  LINES[st.basis].forEach(([line, label]) => {
    if (label === null) return;
    const c = seriesCode(st.basis, st.bank, line);
    const now = at(JBA, c, latest), was = at(JBA, c, prior);
    const change = (now === null || was === null) ? "" : now - was;
    const pct = (change === "" || !was) ? "" : (change / Math.abs(was)) * 100;
    rows.push([line, '"' + label.replace(/"/g, '""') + '"', now ?? "", was ?? "", change, pct].join(","));
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([header + "\n" + rows.join("\n") + "\n"], { type: "text/csv" }));
  a.download = "bank-statement-" + st.bank + "-" + st.basis + "-" + latest.slice(0, 4) + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---- exports ---- */

function exportHeader(rel, title, extra) {
  return [
    "Plover Analytics — " + title,
    "Source: " + (rel.source_name || ""),
    "Release: data through " + half(rel.latest_period),
    "Retrieved: " + fmtStamp(rel.retrieved_at) + " · SHA-256 " + (rel.sha256 || ""),
    "A half-year is dated to its closing month (March or September)",
  ].concat(extra || []).concat(["An empty cell is a value the source did not publish — never zero"]);
}

/* ---- wiring ---- */

function seg(id, attr, key, after) {
  $(id).querySelectorAll("button").forEach(b => {
    b.addEventListener("click", () => {
      const next = {}; next[key] = b.getAttribute(attr);
      setUrlState(next); syncSeg(id, attr, key); after();
    });
  });
  syncSeg(id, attr, key);
}

function syncSeg(id, attr, key) {
  const cur = String(urlState()[key]);
  $(id).querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.getAttribute(attr) === cur)));
}

/* ---- loan and deposit rates ---- */

let ratesChart = null;

function renderRates() {
  const set = new Set();
  RATE_SERIES.forEach(r => pts(r.api, r.code).forEach(p => set.add(p[0])));
  let periods = Array.from(set).sort();
  if (urlState().rates === "10") periods = periods.slice(-120);
  const cfg = {
    series: RATE_SERIES.map(r => ({
      name: r.label, slot: r.slot,
      points: periods.map(p => [p, at(r.api, r.code, p)]),
    })),
    unit: "%", dp: 3, yAxisName: "% per year", yAxisDp: 1,
    trust: "official", legendFloor: 900, legendBottomNarrow: 118,
    sourceLine: "Source: Bank of Japan — average contract interest rates on loans (貸出約定平均金利, IR04) " +
      "and average interest rates posted on deposits (IR02). Data through " +
      fmtPeriod(D.rel.loan.latest_period) + " (loans) and " + fmtPeriod(D.rel.dep.latest_period) + " (deposits).",
  };
  if (ratesChart) ratesChart.render(cfg); else ratesChart = obsChart($("rates-chart"), "line", cfg);
  $("rates-note").textContent = periods.length ? fmtPeriod(periods[0]) + "–" + fmtPeriod(periods[periods.length - 1]) : "";
  $("rates-source").innerHTML = escapeHtml(cfg.sourceLine) + " " + trustBadge("official");
  $("rates-calc").innerHTML = "<summary>Show calculation</summary><p>Published rates in percent per year, " +
    "not computed here. New-loan rates are the average contracted rate on loans made during the month; " +
    "the deposit rate is the average of rates posted at financial institutions, monthly from April 2022 " +
    "(the survey was weekly before that and is not spliced). A month a series does not cover is a gap.</p>" +
    "<p>" + escapeHtml(BOJ_CREDIT) + "</p>";
}

/* ---- the selected bank's bond book, gains and rate risk ---- */

/* A failed check in words. The extractors record a code for each check that
   did not pass (equity/bank_extract.py, the Pillar 3 reader); a reader gets
   what it means, never the code. Several are joined by ";". */
const SHOCKS = { parallel_up: "parallel up", parallel_down: "parallel down", steepener: "steepener",
  flattener: "flattener", short_up: "short-rate up", short_down: "short-rate down" };
function checkWords(detail) {
  return String(detail || "").split(";").filter(Boolean).map(code => {
    if (code === "no_current_maturity_table") return "the report has no maturity table for this year";
    if (code === "no_current_afs_table") return "the report has no table of available-for-sale securities for this year";
    if (code === "maturity:no_total_row" || code === "afs:no_total_row") return "a table has no total row";
    if (code === "maturity:no_securities_row") return "the maturity table has no securities line";
    if (/^maturity:/.test(code)) return "the maturity table's lines do not add up to its total";
    if (/^afs:/.test(code)) return "the available-for-sale table's lines do not add up to its totals";
    if (/^mixed_units:/.test(code)) return "the tables are printed in different units";
    if (code === "as_of_from_url") return "the as-of date comes from the file's address, not the table";
    if (/^missing_/.test(code)) return "the table has no row for the " + (SHOCKS[code.slice(8)] || "one") + " shock";
    if (/^max_mismatch:/.test(code)) return "the printed maximum differs from the largest of the six shocks";
    return "an internal check did not pass";
  }).join("; ");
}

function yenBn(v, dp) { return v === null || v === undefined ? MISSING : fmtNum(v / 1e9, dp === undefined ? 1 : dp); }
function sortAttr(v) { return ' data-sort="' + (v === null || v === undefined ? "" : v) + '"'; }
function pct(v, dp) { return v === null || v === undefined ? MISSING : fmtSigned(v, dp === undefined ? 1 : dp, "%"); }

async function loadBook() {
  const code = "fi:" + urlState().bank;
  if (D.book && D.book.code === code) return;
  D.book = { code, data: null, error: null };
  try {
    D.book.data = await getJSON(BANKS + "/company/" + encodeURIComponent(code));
  } catch (err) {
    D.book.error = String(err.message || err);
  }
}

function fyLabel(iso) { return iso ? "FY" + (Number(iso.slice(0, 4)) - 1) + " (to " + fmtPeriodLong(iso) + ")" : MISSING; }

function renderBook() {
  const b = D.book && D.book.data;
  const name = bankLabel(urlState().bank);
  if (!b) {
    $("book-note").textContent = name;
    $("book-table").innerHTML = '<p class="table-foot">No annual securities report is on file for this bank ' +
      "under its own name or its holding company's, so no maturity note can be shown.</p>";
    $("book-foot").textContent = "";
    $("afs-table").innerHTML = ""; $("afs-foot").textContent = "";
    $("irr-table").innerHTML = ""; $("irr-foot").textContent = "";
    $("book-calc").innerHTML = "";
    return;
  }
  const filer = b.name_en || b.filer_name;
  $("book-note").textContent = filer + " · " + fyLabel(b.period_end) + (b.basis === "consolidated" ? ", consolidated" : ", non-consolidated");
  const cur = b.maturity.filter(m => m.year_offset === 0);
  const fund = b.funding.filter(m => m.year_offset === 0);
  const labels = (cur[0] || fund[0] || {}).bucket_labels || ["Within 1 year", "1–3 years", "3–5 years", "5–7 years", "7–10 years", "Over 10 years"];
  const cols = labels.filter(l => l !== null);
  const head = "<thead><tr><th scope=\"col\">Line</th>" +
    cols.map(l => '<th scope="col" class="num">' + escapeHtml(l) + ", ¥bn</th>").join("") +
    '<th scope="col" class="num">Total, ¥bn</th></tr></thead>';
  function rowsOf(list, title) {
    if (!list.length) return "";
    let out = '<tr><th scope="rowgroup" colspan="' + (cols.length + 2) + '" style="text-align:left">' + escapeHtml(title) + "</th></tr>";
    list.forEach(m => {
      const vals = BUCKET_KEYS.slice(0, cols.length).map(k => m[k]);
      const present = vals.filter(v => v !== null && v !== undefined);
      const total = present.length ? present.reduce((a, v) => a + v, 0) : null;
      const indent = m.parent_key === "securities" ? 1 : (m.parent_key ? 2 : 0);
      out += '<tr><td class="nowrap' + (indent ? ' indent-' + indent : "") + '">' + escapeHtml(m.label_ja) + "</td>" +
        vals.map(v => '<td class="num"' + sortAttr(v) + ">" + yenBn(v) + "</td>").join("") +
        '<td class="num"' + sortAttr(total) + ">" + yenBn(total) + "</td></tr>";
    });
    return out;
  }
  $("book-table").innerHTML = '<table class="data" data-no-enhance>' + head + "<tbody>" +
    rowsOf(cur, "Securities and loans by remaining term") + rowsOf(fund, "Deposits and funding by remaining term") +
    "</tbody></table>";
  const warn = b.status === "partial" ? " A check on this filing did not pass (" + checkWords(b.detail) + "); figures are as printed but the bank is left out of rankings." : "";
  $("book-foot").textContent = "Principal amounts by remaining term as printed in the note (単位: " + (b.unit_label || "百万円") +
    "), shown in ¥bn. The total column adds the printed buckets; a bucket the note leaves blank is — and adds nothing. " +
    "Demand deposits sit in \u201cwithin 1 year\u201d by the note's own rule. Filed " + fmtPeriodLong(b.filed_date) + ", EDINET " + b.doc_id + "." + warn;

  // unrealised gains: AFS current year by type, prior year's difference beside it
  const afsCur = b.securities.filter(x => x.category === "afs" && x.year_offset === 0);
  const afsPri = b.securities.filter(x => x.category === "afs" && x.year_offset === -1);
  const priBy = {};
  afsPri.forEach(x => { priBy[x.block + "|" + x.label_ja] = x; });
  if (afsCur.length) {
    const blockLabel = { above: "Above cost", below: "Below cost", total: "Total" };
    let body = "";
    let lastBlock = null;
    afsCur.forEach(x => {
      if (x.block !== lastBlock) {
        body += '<tr><th scope="rowgroup" colspan="5" style="text-align:left">' + escapeHtml(blockLabel[x.block] || x.block) + "</th></tr>";
        lastBlock = x.block;
      }
      const p = priBy[x.block + "|" + x.label_ja];
      const indent = (x.type_key === "jgb" || x.type_key === "municipal" || x.type_key === "corporate" || x.type_key === "short_corporate") ? 1 :
        (x.type_key === "foreign" || x.type_key === "investment_trusts") ? 1 : 0;
      body += '<tr><td class="nowrap' + (indent ? ' indent-1' : "") + '">' + escapeHtml(x.label_ja) + "</td>" +
        '<td class="num"' + sortAttr(x.book_yen) + ">" + yenBn(x.book_yen) + "</td>" +
        '<td class="num"' + sortAttr(x.cost_yen) + ">" + yenBn(x.cost_yen) + "</td>" +
        '<td class="num"' + sortAttr(x.diff_yen) + ">" + (x.diff_yen === null ? MISSING : fmtSigned(x.diff_yen / 1e9, 1)) + "</td>" +
        '<td class="num"' + sortAttr(p ? p.diff_yen : null) + ">" + (p && p.diff_yen !== null ? fmtSigned(p.diff_yen / 1e9, 1) : MISSING) + "</td></tr>";
    });
    $("afs-table").innerHTML = '<table class="data" data-no-enhance><thead><tr><th scope="col">Available-for-sale securities</th>' +
      '<th scope="col" class="num">Book value, ¥bn</th><th scope="col" class="num">Cost, ¥bn</th>' +
      '<th scope="col" class="num">Difference, ¥bn</th><th scope="col" class="num">Difference a year earlier, ¥bn</th></tr></thead><tbody>' + body + "</tbody></table>";
    $("afs-foot").textContent = "Book value, cost and their difference as printed in the securities note (その他有価証券), in ¥bn. " +
      "A negative difference is an unrealised loss. Bonds are the sum of the bond lines under them; \u201cother\u201d includes foreign bonds and investment trusts where the bank names them." +
      (b.var_banking_yen ? " The bank states its banking-book value at risk as " + yenBn(b.var_banking_yen, 1) + " ¥bn" +
        (b.var_banking_prior_yen ? " (a year earlier " + yenBn(b.var_banking_prior_yen, 1) + " ¥bn)" : "") + "." : "");
  } else {
    $("afs-table").innerHTML = '<p class="table-foot">No available-for-sale table was read from this filing.</p>';
    $("afs-foot").textContent = "";
  }

  // rate risk
  const irr = (b.irrbb || []);
  if (irr.length) {
    const t = irr.find(x => x.basis === "consolidated") || irr[0];
    const body = t.scenarios.map(sc => "<tr><td>" + escapeHtml(sc.label) + "</td>" +
      '<td class="num"' + sortAttr(sc.delta_eve_yen) + ">" + yenBn(sc.delta_eve_yen) + "</td>" +
      '<td class="num"' + sortAttr(sc.delta_nii_yen) + ">" + yenBn(sc.delta_nii_yen) + "</td></tr>").join("");
    $("irr-table").innerHTML = '<table class="data" data-no-enhance><thead><tr><th scope="col">Rate shock</th>' +
      '<th scope="col" class="num">ΔEVE, ¥bn</th><th scope="col" class="num">ΔNII, ¥bn</th></tr></thead><tbody>' + body +
      "<tr><td>Tier 1 capital</td><td class=\"num\"" + sortAttr(t.tier1_yen) + ">" + yenBn(t.tier1_yen) + '</td><td class="num"></td></tr>' +
      "<tr><td>Largest ΔEVE as a share of Tier 1</td><td class=\"num\"" + sortAttr(t.delta_eve_to_tier1_pct) + ">" +
      (t.delta_eve_to_tier1_pct === null ? MISSING : fmtRate(t.delta_eve_to_tier1_pct, 1)) + ' <span class="muted">derived</span></td><td class="num"></td></tr>' +
      "</tbody></table>";
    $("irr-foot").innerHTML = escapeHtml(t.name_ja + " (" + (t.basis === "consolidated" ? "consolidated" : "bank only") + "), as of " +
      fmtPeriodLong(t.as_of) + ", as printed in its Pillar 3 disclosure, page " + t.page + ". ") +
      '<a href="' + escapeHtml(t.source_url) + '" rel="noopener">Source PDF</a>' +
      escapeHtml(" (SHA-256 " + (t.pdf_sha256 || "").slice(0, 12) + "…). ΔEVE is the fall in economic value under the shock; ΔNII the fall in net interest income. " +
        (t.status === "partial" ? "A check on this table did not pass (" + checkWords(t.detail) + "); figures are as printed." : ""));
  } else {
    $("irr-table").innerHTML = '<p class="table-foot">No Basel III rate-risk table has been collected for this bank yet.</p>';
    $("irr-foot").textContent = "";
  }
  $("book-calc").innerHTML = "<summary>Show calculation</summary><p>Every amount in these three tables is a printed " +
    "figure: the maturity note, the securities note and the IRRBB1 table, converted from the printed unit to ¥bn " +
    "exactly. Derived: the total column (sum of printed buckets) and ΔEVE as a share of Tier 1 " +
    "(largest ΔEVE ÷ Tier 1 × 100, both from the same table).</p>";
}

function bookCSV() {
  const b = D.book && D.book.data;
  if (!b) return;
  const lines = [
    "# Plover Analytics — bank bond book by remaining term, unrealised gains and rate risk",
    "# Bank: " + (b.name_en || b.filer_name) + " (" + b.sec_code + "), " + fyLabel(b.period_end) + ", " + b.basis,
    "# Source: annual securities report on EDINET (" + b.doc_id + ", filed " + b.filed_date + "); rate risk from the bank's Pillar 3 disclosure",
    "# Unit: yen, exactly as printed converted from " + (b.unit_label || "百万円") + "; blank = not printed, never zero",
    "# Trust: official statistic (printed figures)",
    "table,year_offset,side_or_category,block,label,item_key,parent_key,within_1y,y1_3,y3_5,y5_7,y7_10,over_10y,book,cost,diff,bucket_scheme",
  ];
  const cell = v => (v === null || v === undefined) ? "" : String(v);
  b.maturity.concat(b.funding).forEach(m => lines.push(["maturity", m.year_offset, m.side, "", m.label_ja, m.item_key, m.parent_key]
    .concat(BUCKET_KEYS.map(k => m[k])).concat(["", "", "", m.bucket_scheme]).map(cell).map(v => /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v).join(",")));
  b.securities.forEach(x => lines.push(["securities", x.year_offset, x.category, x.block, x.label_ja, x.type_key, "", "", "", "", "", "", "",
    x.book_yen, x.cost_yen, x.diff_yen, ""].map(cell).map(v => /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v).join(",")));
  (b.irrbb || []).forEach(t => t.scenarios.forEach(sc => lines.push(["irrbb", 0, t.basis, sc.scenario, t.name_ja, "delta_eve", "", "", "", "", "", "", "",
    sc.delta_eve_yen, sc.delta_nii_yen, t.tier1_yen, t.source_url].map(cell).join(","))));
  downloadDataURL("data:text/csv;charset=utf-8," + encodeURIComponent(lines.join("\n")), "bank-bond-book-" + b.sec_code + ".csv");
}

/* ---- rate risk across banks ---- */

async function loadIrrbb() {
  if (D.irrbb) return;
  try { D.irrbb = await getJSON(BANKS + "/irrbb?fi_type=&basis=consolidated"); }
  catch (err) { D.irrbb = { rows: [], error: String(err.message || err) }; }
}

function renderIrrbb() {
  const rows = (D.irrbb && D.irrbb.rows) || [];
  if (!rows.length) {
    $("irrbb-table").innerHTML = '<p class="table-foot">No rate-risk tables have been collected yet.</p>';
    $("irrbb-note").textContent = ""; $("irrbb-foot").textContent = "";
    return;
  }
  const head = '<thead><tr><th scope="col">Bank</th><th scope="col">As of</th>' +
    '<th scope="col" class="num">Largest ΔEVE, ¥bn</th><th scope="col" class="num">Tier 1, ¥bn</th>' +
    '<th scope="col" class="num">ΔEVE ÷ Tier 1, %</th><th scope="col" class="num">ΔNII, parallel up, ¥bn</th>' +
    '<th scope="col">Basis</th><th scope="col">Source</th></tr></thead>';
  const body = rows.map(r => "<tr><td>" + escapeHtml(r.name_ja) + ' <span class="muted">' + escapeHtml(r.fi_code) + "</span></td>" +
    '<td data-sort="' + escapeHtml(r.as_of || "") + '">' + (r.as_of ? fmtPeriodLong(r.as_of) : MISSING) + "</td>" +
    '<td class="num"' + sortAttr(r.delta_eve_max_yen) + ">" + yenBn(r.delta_eve_max_yen) + "</td>" +
    '<td class="num"' + sortAttr(r.tier1_yen) + ">" + yenBn(r.tier1_yen) + "</td>" +
    '<td class="num"' + sortAttr(r.delta_eve_to_tier1_pct) + ">" + (r.delta_eve_to_tier1_pct === null ? MISSING : fmtRate(r.delta_eve_to_tier1_pct, 1)) + "</td>" +
    '<td class="num"' + sortAttr(r.delta_nii_parallel_up_yen) + ">" + yenBn(r.delta_nii_parallel_up_yen) + "</td>" +
    "<td>" + (r.basis === "consolidated" ? "Consolidated" : "Bank only") + (r.status === "partial" ? ' <span class="muted" title="' + escapeHtml(checkWords(r.detail)) + '">check failed</span>' : "") + "</td>" +
    '<td><a href="' + escapeHtml(r.source_url) + '" rel="noopener">PDF p.' + escapeHtml(String(r.page)) + "</a></td></tr>").join("");
  $("irrbb-table").innerHTML = '<table class="data">' + head + "<tbody>" + body + "</tbody></table>";
  const asOfs = rows.map(r => r.as_of).filter(Boolean).sort();
  $("irrbb-note").textContent = rows.length + " banks" + (asOfs.length ? ", as of " + fmtPeriodLong(asOfs[0]) + (asOfs[0] !== asOfs[asOfs.length - 1] ? "–" + fmtPeriodLong(asOfs[asOfs.length - 1]) : "") : "");
  const cov = (D.irrbb.coverage || []).map(c => c.n + " " + c.status).join(", ");
  $("irrbb-foot").textContent = "ΔEVE, ΔNII and Tier 1 as printed in each bank's IRRBB1 table, in ¥bn; the ratio is derived. " +
    "Sorted by the ratio; a bank whose table has not been found is not listed (collection status: " + cov + "). " +
    "Under the Basel standard a ratio above 15% of Tier 1 marks an outlier for supervisory review, not a breach.";
  $("irrbb-calc").innerHTML = "<summary>Show calculation</summary><p>ΔEVE ÷ Tier 1 = largest ΔEVE of the six " +
    "scenarios (最大値, as printed) ÷ Tier 1 capital (as printed in the same table) × 100. Amounts converted from " +
    "the printed unit to ¥bn exactly. The consolidated table is shown where a bank prints both.</p>";
}

function irrbbCSV() {
  const rows = (D.irrbb && D.irrbb.rows) || [];
  if (!rows.length) return;
  const lines = [
    "# Plover Analytics — rate risk in the banking book (IRRBB1) by bank",
    "# Source: each bank's own Basel III Pillar 3 disclosure PDF, as printed; URL, page and SHA-256 per row",
    "# Unit: yen; delta_eve_to_tier1_pct is derived: largest ΔEVE ÷ Tier 1 × 100",
    "# Trust: official statistic (printed figures); ratio derived",
    "fi_code,name,fi_type,basis,as_of,delta_eve_max_yen,delta_nii_parallel_up_yen,delta_eve_parallel_up_yen,tier1_yen,delta_eve_to_tier1_pct,status,source_url,page,pdf_sha256",
  ];
  const cell = v => (v === null || v === undefined) ? "" : String(v);
  rows.forEach(r => lines.push([r.fi_code, r.name_ja, r.fi_type, r.basis, r.as_of, r.delta_eve_max_yen, r.delta_nii_parallel_up_yen,
    r.delta_eve_parallel_up_yen, r.tier1_yen, r.delta_eve_to_tier1_pct, r.status, r.source_url, r.page, r.pdf_sha256].map(cell).join(",")));
  downloadDataURL("data:text/csv;charset=utf-8," + encodeURIComponent(lines.join("\n")), "bank-rate-risk-irrbb.csv");
}

async function boot() {
  initThemeToggle(() => { renderNpl(); renderEarn(); renderBank(); renderRates(); });

  const [npl, res, jba, listing, loan, dep] = await Promise.all([
    getJSON(NPL + "/overview"), getJSON(RES + "/overview"), getJSON(JBA + "/overview"),
    // The deposits line of every bank names every bank the release carries;
    // the listing is a search, and the codes tell the bases apart.
    getJSON(JBA + "/series?q=" + encodeURIComponent("deposits")),
    getJSON(LOAN + "/overview").catch(() => null), getJSON(DEP + "/overview").catch(() => null),
  ]);
  D.rel = { npl: npl.release, res: res.release, jba: jba.release,
    loan: loan ? loan.release : { latest_period: null }, dep: dep ? dep.release : { latest_period: null } };
  D.stale = { npl: npl.stale, res: res.stale, jba: jba.stale };
  (listing.series || []).forEach(s => {
    const m = /^s\.(\d{4})\.a015$/.exec(s.code);
    if (m) D.banks.push({ code: m[1], name: s.name_en.split(" — ")[0] });
    const c = /^c\.(\d{4})\.a010$/.exec(s.code);
    if (c) D.consolidated[c[1]] = true;
  });
  if (!D.banks.some(b => b.code === urlState().bank)) setUrlState({ bank: D.banks.length ? D.banks[0].code : "0001" });

  await Promise.all([
    // One unit per request: the API refuses a ratio and an amount together.
    loadSeries(NPL, NPL_GROUPS.map(g => g.code + ".npl-ratio")),
    loadSeries(NPL, ["all-banks.disclosed"]),
    loadSeries(RES, Object.keys(MEASURES).map(k => MEASURES[k].major[0]).concat(Object.keys(MEASURES).map(k => MEASURES[k].regional[0]))),
    loadBank(),
    loadSeries(LOAN, RATE_SERIES.filter(r => r.api === LOAN).map(r => r.code)),
    loadSeries(DEP, RATE_SERIES.filter(r => r.api === DEP).map(r => r.code)),
    loadBook(),
    loadIrrbb(),
  ]);

  const select = $("bank-select");
  select.innerHTML = D.banks.map(b => '<option value="' + escapeHtml(b.code) + '">' +
    escapeHtml(b.name) + " · " + escapeHtml(b.code) + "</option>").join("");
  select.value = urlState().bank;
  const measure = $("earn-measure");
  measure.innerHTML = Object.keys(MEASURES).map(k => '<option value="' + k + '">' +
    escapeHtml(MEASURES[k].label) + "</option>").join("");
  measure.value = urlState().measure;

  renderHead(); renderStale(); renderTiles(); renderNpl(); renderEarn(); renderBank(); renderProvenance();
  renderRates(); renderBook(); renderIrrbb();

  seg("npl-range", "data-range", "range", renderNpl);
  seg("bank-basis", "data-basis", "basis", async () => { await loadBank(); renderBank(); });
  select.addEventListener("change", async e => { setUrlState({ bank: e.target.value }); await Promise.all([loadBank(), loadBook()]); renderBank(); renderBook(); });
  seg("rates-range", "data-range", "rates", renderRates);
  $("rates-png").addEventListener("click", () => ratesChart && ratesChart.exportPNG("loan-and-deposit-rates.png"));
  $("rates-csv").addEventListener("click", () => ratesChart && ratesChart.exportCSV("loan-and-deposit-rates.csv",
    exportHeader(D.rel.loan, "loan and deposit rates by lender", [
      "Unit: percent per year, official statistic as published (BOJ IR04 new-loan rates; IR02 posted ordinary-deposit rate)",
      BOJ_CREDIT])));
  $("book-csv").addEventListener("click", bookCSV);
  $("irrbb-csv").addEventListener("click", irrbbCSV);
  measure.addEventListener("change", e => { setUrlState({ measure: e.target.value }); renderEarn(); });

  $("npl-png").addEventListener("click", () => nplChart.exportPNG("bad-loan-ratio-by-bank-group.png"));
  $("npl-csv").addEventListener("click", () => nplChart.exportCSV("bad-loan-ratio-by-bank-group.csv",
    exportHeader(D.rel.npl, "bad-loan ratio by bank group", [
      "Unit: percent of total credit, official statistic as published (不良債権比率)"])));
  $("earn-png").addEventListener("click", () => earnChart.exportPNG("bank-earnings-by-group.png"));
  $("earn-csv").addEventListener("click", () => earnChart.exportCSV("bank-earnings-by-group.csv",
    exportHeader(D.rel.res, "bank earnings by group", [
      "Unit: ¥ billion (published ¥100mn ÷ 10, an exact conversion)",
      "Measure: " + MEASURES[urlState().measure].label + " for the fiscal year (3月期), official statistic as published",
      "The major-bank figure is consolidated; the regional-bank figure is non-consolidated"])));
  $("bank-png").addEventListener("click", () => bankChart && bankChart.exportPNG("bank-deposits-loans-" + urlState().bank + ".png"));
  $("bank-chart-csv").addEventListener("click", () => bankChart && bankChart.exportCSV("bank-deposits-loans-" + urlState().bank + ".csv",
    exportHeader(D.rel.jba, "bank deposits and loans", [
      "Bank: " + bankLabel(urlState().bank) + " (金融機関コード " + urlState().bank + ", " + (urlState().basis === "c" ? "consolidated" : "non-consolidated") + ")",
      "Unit: ¥ trillion (published millions of yen ÷ 1,000,000, an exact conversion)"])));
  $("bank-csv").addEventListener("click", bankCSV);
}

boot().catch(err => {
  $("stale-banner").innerHTML = '<div class="banner" role="alert">This page could not load its data. ' +
    escapeHtml(String(err.message || err)) + "</div>";
});
