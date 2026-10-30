/* Company Profile. The question this screen answers:
   "What does this company report by region and customer, and how does that
   sit against the customs flows its business is part of — in its own fiscal
   year, so I can put it in a model?"

   One /lens payload carries both sides for one company. Filed figures are
   official as the company reported them; the fiscal-year and fiscal-quarter
   sums of customs values, the implied share and the commodity-to-company
   mapping are the platform's and carry their formula under "Show calculation"
   and in every export. Missing renders as —, never 0.

   Two things the page never does: it never adds a sub-note ("of which United
   States") to the region it sits inside, and it never presents the implied
   share as anything but an indicator — the caveat travels with the number. */
"use strict";

const API = "/api/v1/equity/segments";

let L = null;                 // the /lens payload
let customsChart = null, quartersChart = null;

const SLOT_FILED = 1;         // navy: what the company filed
const SLOT_CUSTOMS = 2;       // orange: the customs line
const REGION_SLOTS = { CN: 2, TW: 3, KR: 4, NA: 5, US: 5, EU: 6 };

const CALCS = {
  fy: "Fiscal years are named for the calendar year in which they begin (the year " +
    "ending March 2026 is FY2025). Customs months are summed into the company's " +
    "fiscal periods; a period with an unpublished month is left out, never summed short.",
  yen: "Filed values are stored in yen from the filer's stated unit (百万円 for nearly all " +
    "filers); customs values are published in thousands of yen. Both are shown here in " +
    "billions of yen (÷ 1,000,000,000).",
};

/* ---- helpers ---- */

function $(id) { return document.getElementById(id); }
function bn(v) { return v === null || v === undefined ? null : v / 1e9; }
function fmtBn(v, dp) { return v === null || v === undefined ? MISSING : fmtNum(v / 1e9, dp === undefined ? 1 : dp); }

function getJSON(url) {
  return fetch(url).then(r => {
    if (!r.ok) return r.json().then(b => { throw new Error(b.detail || ("HTTP " + r.status)); },
                                    () => { throw new Error("HTTP " + r.status); });
    return r.json();
  });
}

function calcBlock(el, lines) {
  const node = $(el);
  if (!node) return;
  node.style.display = "";
  node.innerHTML = "<summary>Show calculation</summary><div class='calc-body'>" +
    lines.map(l => "<p>" + escapeHtml(l) + "</p>").join("") + "</div>";
}

function urlState() {
  const p = new URLSearchParams(location.search);
  return {
    // Every other company page on the platform links with ?c=. This page used
    // to read only ?code=, so every one of those links silently fell through
    // to the default company. Both are read; ?c= is what we write.
    code: p.get("c") || p.get("code") || "8035",
    commodity: p.get("commodity") || "",
    view: p.get("view") === "share" ? "share" : "level",
    qrange: p.get("qrange") || "10",
  };
}

function setUrlState(next) {
  const s = Object.assign(urlState(), next);
  const p = new URLSearchParams();
  p.set("c", s.code);
  if (s.commodity) p.set("commodity", s.commodity);
  if (s.view !== "level") p.set("view", s.view);
  if (s.qrange !== "10") p.set("qrange", s.qrange);
  history.replaceState(null, "", "?" + p.toString());
}

function wire(id, handler) {
  const el = $(id);
  if (!el) return;
  const fresh = el.cloneNode(true);
  el.replaceWith(fresh);
  fresh.addEventListener("click", e => { e.preventDefault(); handler(); });
}

function pressGroup(groupId, attr, value) {
  const group = $(groupId);
  if (!group) return;
  Array.prototype.forEach.call(group.querySelectorAll("button"), b => {
    b.setAttribute("aria-pressed", b.getAttribute(attr) === String(value) ? "true" : "false");
  });
}

function companyName() {
  return L.company.name_en || L.company.name_ja || L.company.sec_code;
}

function filedSource() {
  const f = L.filing;
  return "Source: " + L.credit_lines[0] + " " + f.doc_id + " · FY to " +
    fmtPeriodLong(f.period_end) + " · filed " + f.filed_date + " · " + TRUST_LABELS.official;
}

function customsSource(extra) {
  const rel = L.customs.release;
  return "Source: Ministry of Finance, Japan · " + rel.source_id + " · Data through " +
    fmtPeriodLong(rel.latest_period) + " · Retrieved " + fmtStamp(rel.retrieved_at) +
    " · Official customs values; fiscal sums calculated" + (extra ? " · " + extra : "");
}

function csvHeader(what, formulas) {
  const f = L.filing, rel = L.customs.release;
  return [
    "Plover Analytics — Company Profile: " + companyName() + " (" + L.company.sec_code + ") — " + what,
    "Filed figures: " + L.credit_lines[0] + " Filing " + f.doc_id + ", FY to " + f.period_end +
      ", filed " + f.filed_date + ", status " + f.status + (f.detail ? " — " + f.detail : ""),
    "Customs figures: " + L.credit_lines[1] + " Release " + rel.label + " (sha256 " + rel.sha256 +
      "), retrieved " + fmtStamp(rel.retrieved_at),
    "Region basis as filed: " + (f.basis_text || "not stated"),
    CALCS.fy, CALCS.yen,
  ].concat(formulas || []);
}

/* ---- header, tiles, provenance ---- */

function renderHeader() {
  const f = L.filing;
  $("co-name").textContent = companyName();
  $("header-asof").textContent = "FY to " + fmtPeriodLong(f.period_end);
  $("page-asof").textContent = "Filing " + f.doc_id + " · filed " + f.filed_date +
    (f.status === "partial" ? " · partial: " + (f.detail || "") : "");
  $("page-sub").textContent = (L.company.name_ja ? L.company.name_ja + " · " : "") +
    L.company.sec_code + " · " + (L.company.industry || "") +
    " · fiscal year ends month " + L.fy_end_month +
    " · " + (f.accounting_standard || "");
  const csv = $("lens-csv");
  csv.href = API + "/lens/" + L.company.sec_code + ".csv";
  csv.download = "company-lens-" + L.company.sec_code + ".csv";
}

function tile(label, valueHtml, sub, title) {
  return '<div class="strip-cell">' +
    '<div class="strip-label" title="' + escapeHtml(title || label) + '">' + escapeHtml(label) + "</div>" +
    '<div class="strip-value num">' + valueHtml + "</div>" +
    '<div class="strip-delta flat">' + escapeHtml(sub || "") + "</div>" +
    "</div>";
}

function renderTiles() {
  const f = L.filing;
  const cur = L.filed.regions.filter(r => r.year_offset === 0 && !r.is_subnote);
  const total = cur.find(r => r.region_key === "TOTAL");
  const cons = f.consolidated_revenue_yen;
  const overseas = cur.filter(r => r.region_key !== "TOTAL" && r.region_key !== "JP")
    .reduce((s, r) => s + (r.value_yen || 0), 0);
  const base = total ? total.value_yen : cons;
  // The filer's own consolidated revenue where it tags one; otherwise the
  // segment note's total row, which is equally as filed and says so.
  const revenue = (cons === null || cons === undefined) ? base : cons;
  // A filer's "その他 / Other" line is a residual bucket, not a place. It counts
  // towards the overseas total but must never be named as a region.
  const overseasRegions = cur.filter(r =>
    r.region_key !== "TOTAL" && r.region_key !== "JP" && r.region_key !== "OT");
  const biggest = overseasRegions.slice()
    .sort((a, b) => (b.value_yen || 0) - (a.value_yen || 0))[0];
  const named = L.filed.customers.filter(c => c.year_offset === 0);
  const fy = cur.length ? cur[0].fiscal_label : "";

  $("tiles").innerHTML =
    tile("Consolidated Revenue",
      revenue === null || revenue === undefined ? MISSING
        : fmtNum(revenue / 1e9, 1) + '<span class="unit"> ¥bn</span>',
      fy + (cons === null || cons === undefined
        ? " \u00b7 segment-note total, as filed" : " \u00b7 as filed"),
      cons === null || cons === undefined
        ? "The segment note's own total row, as filed \u2014 this filer does not tag a separate consolidated revenue figure"
        : "Consolidated revenue from the financial statements, as filed") +
    tile("Overseas Share of Revenue", base && cur.length
      ? fmtNum(overseas / base * 100, 1) + '<span class="unit"> %</span>' : MISSING,
      cur.length ? "on the filer's basis: " + (f.basis_text || "not stated") : (f.region_omitted_reason || "no region table"),
      "Sum of every non-Japan region the filer reports, over the filed total") +
    tile("Largest Overseas Region", biggest
      ? escapeHtml(biggest.region_label_en || biggest.label_ja) : MISSING,
      biggest ? fmtBn(biggest.value_yen) + " ¥bn · " + fmtNum(biggest.value_yen / base * 100, 1) + "% of revenue"
        : (cur.length ? "the filer names no overseas region beyond a residual \u201cother\u201d" : ""),
      "The non-Japan region with the most filed revenue, excluding the filer's residual \u201cother\u201d bucket") +
    tile("Named Customers", named.length ? String(named.length) : "0",
      named.length ? named.map(c => c.customer_name.length > 26 ? c.customer_name.slice(0, 24) + "…" : c.customer_name).join(" · ") : "none at or above 10% of revenue",
      "Customers the filer names as ten percent or more of revenue");
  $("strip-foot").textContent = "Filed figures for " + fy + ". Regions are on the company's " +
    "own basis; the overseas share and the largest-region share are calculated from them.";
  calcBlock("strip-calc", [
    "overseas share = Σ revenue[region ≠ Japan] / filed total × 100, over the regions the filer reports for the latest year, excluding sub-notes.",
    L.filed.calc, CALCS.yen]);
}

function renderProvenance() {
  const f = L.filing, rel = L.customs.release;
  $("prov-card").innerHTML =
    '<div class="prov-card">' +
      '<div class="prov-card-head"><div class="prov-card-title">Data Sources</div>' +
      '<div class="prov-card-id">' + escapeHtml(f.doc_id) + " · " + escapeHtml(rel.source_id) + "</div></div>" +
      '<div class="prov-grid">' +
        '<div class="prov-field full"><div class="prov-label">Filed figures</div>' +
          '<div class="prov-value">Annual securities report (有価証券報告書), segment information note — EDINET ' +
          escapeHtml(f.doc_id) + "</div>" +
          '<div class="prov-sub">FY to ' + fmtPeriodLong(f.period_end) + " · filed " + escapeHtml(f.filed_date) +
          " · " + escapeHtml(f.accounting_standard || "") + " · parser " + escapeHtml(f.parser_version) +
          " · status " + escapeHtml(f.status) + (f.detail ? " — " + escapeHtml(f.detail) : "") + "</div></div>" +
        '<div class="prov-field"><div class="prov-label">Region basis, as filed</div>' +
          '<div class="prov-value">' + escapeHtml(f.basis_text || "not stated") + "</div>" +
          '<div class="prov-sub">' + (f.reconciliation ? "Regions vs consolidated revenue: " + escapeHtml(f.reconciliation) : "") + "</div></div>" +
        '<div class="prov-field"><div class="prov-label">Customs figures</div>' +
          '<div class="prov-value">' + escapeHtml(rel.source_name) + "</div>" +
          '<div class="prov-sub">' + escapeHtml(rel.label) + " · retrieved " + fmtStamp(rel.retrieved_at) + "</div></div>" +
        '<div class="prov-field full"><div class="prov-label">Company ↔ commodity mapping</div>' +
          '<div class="prov-value">' + (L.mapping.entries.length ? L.mapping.entries.map(m =>
            escapeHtml(m.label) + " (" + escapeHtml(m.role) + ")").join(" · ") : "not mapped to any customs line") + "</div>" +
          '<div class="prov-sub">' + escapeHtml(L.mapping.calc) + "</div></div>" +
        '<div class="prov-field full"><div class="prov-label">Filing checksum (SHA-256, t1 package)</div>' +
          '<div class="prov-hash">' + escapeHtml(f.sha256_t1 || "") + "</div></div>" +
      "</div></div>";
}

/* ---- regions table ---- */

function renderRegions() {
  const rows = L.filed.regions;
  const years = [];
  rows.forEach(r => { if (years.indexOf(r.fiscal_label) === -1) years.push(r.fiscal_label); });
  years.sort(cmpFiscalLabel);
  const labels = [];
  rows.forEach(r => {
    const key = r.label_ja + "|" + (r.is_subnote ? "sub" : "");
    if (!labels.some(l => l.key === key)) labels.push({ key, r });
  });
  const val = (lab, y) => {
    const hit = rows.find(r => r.label_ja === lab.r.label_ja && r.fiscal_label === y && r.is_subnote === lab.r.is_subnote);
    return hit ? hit.value_yen : null;
  };
  const total = y => { const t = rows.find(r => r.region_key === "TOTAL" && r.fiscal_label === y); return t ? t.value_yen : null; };
  let html = '<table class="data" data-no-enhance><thead><tr><th>Region (as filed)</th><th>Key</th>' +
    years.map(y => '<th class="num">' + escapeHtml(y) + " (¥bn)</th>").join("") +
    years.map(y => '<th class="num">Share ' + escapeHtml(y) + " (%)</th>").join("") + "</tr></thead><tbody>";
  labels.forEach(lab => {
    const r = lab.r;
    const isTotal = r.region_key === "TOTAL";
    html += "<tr" + (isTotal ? ' style="font-weight:600"' : "") + "><td>" +
      (r.is_subnote ? '<span style="color:var(--obs-text-muted)">of which </span>' : "") +
      escapeHtml(r.label_ja) + (r.region_label_en && r.region_label_en !== r.label_ja
        ? ' <span style="color:var(--obs-text-muted)">' + escapeHtml(r.region_label_en) + "</span>" : "") +
      (r.is_subnote && r.parent_label_ja ? ' <span class="badge">sub-note of ' + escapeHtml(r.parent_label_ja) + "</span>" : "") + "</td>" +
      "<td>" + escapeHtml(r.region_key || "—") + "</td>" +
      years.map(y => '<td class="num">' + fmtBn(val(lab, y)) + "</td>").join("") +
      years.map(y => { const v = val(lab, y), t = total(y);
        return '<td class="num">' + (v === null || !t || isTotal ? (isTotal && v !== null ? "100.0" : MISSING) : fmtNum(v / t * 100, 1)) + "</td>"; }).join("") +
      "</tr>";
  });
  html += "</tbody></table>";
  $("regions-table").innerHTML = rows.length ? html :
    '<p class="table-foot">' + escapeHtml(L.filing.region_omitted_reason || "The filer publishes no revenue by region.") + "</p>";
  $("regions-note").textContent = rows.length ? (rows.filter(r => r.year_offset === 0 && !r.is_subnote && r.region_key !== "TOTAL").length + " regions") : "";
  $("regions-foot").textContent = rows.length
    ? "Basis as filed: " + (L.filing.basis_text || "not stated") + ". Shares are of the filed total for the same year. " +
      (L.filing.reconciliation ? "Regions against consolidated revenue: " + L.filing.reconciliation + "." : "")
    : "";
  calcBlock("regions-calc", [L.filed.calc, "share = region revenue / filed total × 100, same fiscal year.", CALCS.yen]);
}

/* ---- customs comparison ---- */

function currentBlock() {
  const st = urlState();
  const blocks = L.customs.blocks;
  if (!blocks.length) return null;
  return blocks.find(b => b.key === st.commodity) || blocks[0];
}

function renderCommodityPicker() {
  const sel = $("commodity-select");
  const blocks = L.customs.blocks;
  sel.innerHTML = blocks.length ? blocks.map(b =>
    '<option value="' + escapeHtml(b.key) + '">' + escapeHtml(b.label) + "</option>").join("")
    : '<option value="">Not mapped to a customs line</option>';
  const cur = currentBlock();
  if (cur) sel.value = cur.key;
  sel.disabled = !blocks.length;
  sel.onchange = () => { setUrlState({ commodity: sel.value }); renderCustoms(); renderQuarters(); };
}

function renderCustoms() {
  const st = urlState();
  const block = currentBlock();
  const el = $("customs-chart");
  // Most filers are not mapped to a customs commodity — the mapping only
  // covers the semiconductor chain. Say so once and drop the two customs
  // sections, rather than leaving two empty chart wells down the page.
  const mapped = !!block;
  $("customs-body").hidden = !mapped;
  $("customs-empty").hidden = mapped;
  $("sec-quarters").hidden = !mapped;
  if (!mapped) {
    $("customs-empty").textContent = "This company is not mapped to a customs commodity, " +
      "so there is nothing to put beside its filed regions. The mapping covers the " +
      "semiconductor supply chain only; everything above is the company's own filing.";
    $("customs-note").textContent = "not mapped";
    return;
  }
  const rel = L.relationship.rows.filter(r => r.commodity_key === block.key);
  const years = [];
  rel.forEach(r => { if (years.indexOf(r.fiscal_label) === -1) years.push(r.fiscal_label); });
  years.sort(cmpFiscalLabel);
  const order = ["CN", "TW", "KR", "NA", "US", "EU", "HK", "SG"];
  const regions = [];
  rel.forEach(r => { if (regions.indexOf(r.region_key) === -1) regions.push(r.region_key); });
  regions.sort((a, b) => (order.indexOf(a) + 100) % 100 - (order.indexOf(b) + 100) % 100);
  const cats = regions.map(k => REGION_LABEL_EN[k] || k);
  const pick = (k, y, f) => { const hit = rel.find(r => r.region_key === k && r.fiscal_label === y); return hit ? f(hit) : null; };

  let cfg;
  if (st.view === "share") {
    // one bar per region per fiscal year: how much of the customs line the filer's revenue amounts to
    cfg = {
      categories: cats,
      series: years.map((y, n) => ({ name: y + " implied share", slot: n === years.length - 1 ? SLOT_FILED : 5,
        points: regions.map(k => pick(k, y, r => r.implied_share_pct)) })),
      dp: 1, unitSuffix: "%", yAxisName: "% — filed revenue ÷ customs exports",
      trust: "derived", sourceLine: customsSource("implied share by region, " + block.label),
    };
  } else {
    // the latest fiscal year: what the company filed beside what customs recorded
    const y = years[years.length - 1];
    cfg = {
      categories: cats,
      series: [
        { name: y + " filed revenue", slot: SLOT_FILED, points: regions.map(k => pick(k, y, r => bn(r.filed_revenue_yen))) },
        { name: y + " customs exports", slot: SLOT_CUSTOMS, points: regions.map(k => pick(k, y, r => bn(r.customs_value_yen))) },
      ],
      dp: 1, unitSuffix: "¥bn", yAxisName: "¥bn, " + y,
      trust: "derived", sourceLine: customsSource("filed revenue vs customs exports, " + y + ", " + block.label),
    };
  }
  el.innerHTML = "";
  if (customsChart) customsChart.dispose();
  customsChart = obsChart(el, "cols", cfg);
  $("customs-source").textContent = cfg.sourceLine;
  $("customs-note").textContent = block.label;
  const formulas = [L.customs.calc, L.relationship.calc, CALCS.fy, CALCS.yen,
    "Region → customs partners for this company: " + regions.map(k =>
      (REGION_LABEL_EN[k] || k) + " = " + ((block.regions[k] || {}).partners_label || "")).join("; ") + "."];
  calcBlock("customs-calc", formulas);

  // one row per region × fiscal year
  let html = '<table class="data"><thead><tr><th>Region (filed)</th><th>Fiscal year</th>' +
    '<th class="num">Filed revenue (¥bn)</th><th class="num">Customs exports (¥bn)</th>' +
    '<th class="num">Implied share (%)</th><th>Customs partners counted</th></tr></thead><tbody>';
  rel.slice().sort((a, b) => cmpFiscalLabel(b.fiscal_label, a.fiscal_label)
      || regions.indexOf(a.region_key) - regions.indexOf(b.region_key)).forEach(r => {
    html += "<tr><td>" + escapeHtml(REGION_LABEL_EN[r.region_key] || r.region_key) + "</td><td>" + escapeHtml(r.fiscal_label) + "</td>" +
      '<td class="num">' + fmtBn(r.filed_revenue_yen) + '</td><td class="num">' + fmtBn(r.customs_value_yen) + "</td>" +
      '<td class="num">' + (r.upper_bound ? "≤ " : "") + fmtNum(r.implied_share_pct, 1) + "</td><td>" +
      escapeHtml((block.regions[r.region_key] || {}).partners_label || "") + "</td></tr>";
  });
  html += "</tbody></table>";
  $("relationship-table").innerHTML = rel.length ? html : "";
  $("relationship-foot").textContent = rel.length
    ? (L.relationship.upper_bound_note ? L.relationship.upper_bound_note + " " : "") +
      "An implied share above 100% is the caveat made visible: the company books revenue the customs line does not carry — shipments from overseas plants, service and installation, or a broader product set than the customs commodity."
    : "No filed region of this company names a place the customs data can be matched to.";

  const name = "company-lens-" + L.company.sec_code + "-" + block.key.replace(".", "-") + "-" + st.view;
  wire("customs-png", () => customsChart.exportPNG(name + ".png"));
  wire("customs-csv", () => customsChart.exportCSV(name + ".csv", csvHeader(block.label + " — " + (st.view === "share" ? "implied share" : "filed vs customs"), formulas)));
}

const REGION_LABEL_EN = { JP: "Japan", CN: "China", TW: "Taiwan", KR: "Korea", HK: "Hong Kong",
  SG: "Singapore", US: "United States", NA: "North America", EU: "Europe", DE: "Germany",
  GB: "United Kingdom", NL: "Netherlands", FR: "France", WORLD: "World" };

/* ---- quarterly customs driver ---- */

function renderQuarters() {
  const st = urlState();
  const block = currentBlock();
  const el = $("quarters-chart");
  if (!block) { el.innerHTML = ""; $("quarters-source").textContent = ""; return; }
  // The regions the filer reports as leaves — a sub-note (US inside North
  // America) is not drawn beside its parent, which would count it twice.
  const subnotes = {};
  L.filed.regions.forEach(r => { if (r.is_subnote && r.region_key) subnotes[r.region_key] = true; });
  let keys = Object.keys(block.regions).filter(k => k !== "WORLD" && !subnotes[k]);
  const order = ["CN", "TW", "KR", "NA", "US", "EU", "HK", "SG"];
  keys.sort((a, b) => (order.indexOf(a) + 100) % 100 - (order.indexOf(b) + 100) % 100);
  keys = keys.slice(0, 5);
  const chosen = ["WORLD"].concat(keys);
  const used = {};
  const slots = {};
  chosen.forEach(k => { const w = k === "WORLD" ? 1 : REGION_SLOTS[k]; if (w && !used[w]) { used[w] = true; slots[k] = w; } });
  chosen.forEach(k => { if (slots[k]) return; for (let sl = 2; sl <= 6; sl++) if (!used[sl]) { used[sl] = true; slots[k] = sl; return; } slots[k] = 6; });
  const nQ = st.qrange === "max" ? Infinity : Number(st.qrange) * 4;
  const series = chosen.map(k => {
    const q = block.regions[k].fiscal_quarters;
    const window = q.slice(Math.max(0, q.length - nQ));
    return { name: REGION_LABEL_EN[k] || k, slot: slots[k],
      points: window.map(x => [x.period_end_month, bn(x.value_yen)]) };
  });
  const cfg = { series, unitSuffix: "¥bn", dp: 1, yAxisName: "¥bn per fiscal quarter",
    legendFloor: 1000, trust: "derived",
    sourceLine: customsSource("fiscal quarters, " + block.label) };
  el.innerHTML = "";
  if (quartersChart) quartersChart.dispose();
  quartersChart = obsChart(el, "line", cfg);
  $("quarters-source").textContent = cfg.sourceLine;
  const dropped = block.regions.WORLD.months_not_in_a_quarter;
  $("quarters-note").textContent = block.label + (dropped.length ? " · " + dropped.length + " month" + (dropped.length > 1 ? "s" : "") + " not yet a full quarter" : "");
  const formulas = [L.customs.calc.replace("fiscal year's twelve months", "fiscal quarter's three months"), CALCS.fy, CALCS.yen,
    "Months published but not yet part of a complete quarter: " + (dropped.map(fmtPeriod).join(", ") || "none") + "."];
  calcBlock("quarters-calc", formulas);
  const name = "company-lens-" + L.company.sec_code + "-" + block.key.replace(".", "-") + "-quarters";
  wire("quarters-png", () => quartersChart.exportPNG(name + ".png"));
  wire("quarters-csv", () => quartersChart.exportCSV(name + ".csv", csvHeader(block.label + " — fiscal quarters", formulas)));
}

/* ---- customers and products ---- */

function renderCustomers() {
  const rows = L.filed.customers;
  const cons = L.filing.consolidated_revenue_yen;
  let html = '<table class="data" data-no-enhance><thead><tr><th>Customer (as filed)</th><th>Fiscal year</th>' +
    '<th class="num">Revenue (¥bn)</th><th class="num">Share of revenue (%)</th><th>Segment</th><th>Disclosed as</th></tr></thead><tbody>';
  rows.forEach(r => {
    html += "<tr><td>" + escapeHtml(r.customer_name) + "</td><td>" + escapeHtml(r.fiscal_label) + "</td>" +
      '<td class="num">' + fmtBn(r.value_yen) + "</td>" +
      '<td class="num">' + (cons && r.year_offset === 0 ? fmtNum(r.value_yen / cons * 100, 1) : MISSING) + "</td>" +
      "<td>" + escapeHtml(r.segment_label || "") + "</td><td>" + (r.source === "prose" ? "text of the note" : "table") + "</td></tr>";
  });
  html += "</tbody></table>";
  $("customers-table").innerHTML = rows.length ? html : '<p class="table-foot">The filer names no customer at or above ten percent of revenue.</p>';
  $("customers-note").textContent = rows.filter(r => r.year_offset === 0).length + " named this year";
  $("customers-foot").textContent = rows.length ? "Share of revenue uses consolidated revenue for the same year; prior-year shares are not shown because the prior-year revenue is not carried here." : "";
}

function renderProducts() {
  const rows = L.filed.products;
  let html = '<table class="data" data-no-enhance><thead><tr><th>Reportable segment (as filed)</th><th>Fiscal year</th>' +
    '<th class="num">External revenue (¥bn)</th><th class="num">Segment profit (¥bn)</th></tr></thead><tbody>';
  rows.forEach(r => {
    html += "<tr><td>" + escapeHtml(r.segment_label_ja) + "</td><td>" + escapeHtml(r.fiscal_label) + "</td>" +
      '<td class="num">' + fmtBn(r.external_revenue_yen) + "</td><td class=\"num\">" + fmtBn(r.segment_profit_yen) + "</td></tr>";
  });
  html += "</tbody></table>";
  $("products-table").innerHTML = rows.length ? html :
    '<p class="table-foot">' + (L.filing.single_segment ? "The filer reports a single segment." : "No reportable-segment table was parsed from this filing.") + "</p>";
  $("products-note").textContent = rows.length ? (rows.filter(r => r.year_offset === 0).length + " segments") : (L.filing.single_segment ? "single segment" : "");
}

/* ---- company search ---- */

/* The picker used to be a <select> built from the semiconductor supply-chain
   mapping: eighteen companies. Every other company on the platform — and the
   lens serves any filer with an accepted segment note — was unreachable, so
   the page sat on its default. The search rail is the same one the rest of the
   equity pages use, against the same kind of endpoint. */

function nameCell(en, ja, code) {
  const main = en || ja || code;
  return "<div class='cell-name'><a href='company.html?c=" + escapeHtml(code) + "'>" +
    escapeHtml(main) + "</a></div>";
}

function renderSearch(d) {
  const box = $("search-results");
  if (!d.companies.length) {
    box.innerHTML = "<p class='table-foot'>No company with an accepted segment note " +
      "matches that.</p>";
    return;
  }
  box.innerHTML = "<div class='table-wrap'><table class='data' data-no-enhance><thead><tr>" +
    "<th>Company</th><th>Sector</th><th class='num'>Filings</th>" +
    "<th class='num'>Latest FY end</th></tr></thead><tbody>" +
    d.companies.map(c =>
      "<tr><td>" + nameCell(c.name_en, c.name, c.sec_code) + delistedTag(c) + "</td>" +
      "<td>" + escapeHtml(c.industry || MISSING) + "</td>" +
      "<td class='num'>" + fmtNum(c.filings, 0) + "</td>" +
      "<td class='num'>" + (c.period_end ? escapeHtml(fmtPeriodLong(c.period_end)) : MISSING) +
      "</td></tr>").join("") + "</tbody></table></div>";
}

function initSearch() {
  const input = $("q");
  let timer = null;
  input.addEventListener("input", () => {
    const q = input.value.trim();
    clearTimeout(timer);
    if (!q) { $("search-results").innerHTML = ""; return; }
    timer = setTimeout(() => {
      getJSON("/api/v1/equity/segments/companies?q=" + encodeURIComponent(q))
        .then(renderSearch)
        .catch(err => {
          $("search-results").innerHTML = "<p class='table-foot'>Company search is " +
            "unavailable (" + escapeHtml(err.message) + ").</p>";
        });
    }, 180);
  });
}

/* ---- load ---- */

function renderAll() {
  renderHeader();
  renderTiles();
  renderRegions();
  renderCommodityPicker();
  pressGroup("view-seg", "data-view", urlState().view);
  pressGroup("qrange-seg", "data-qrange", urlState().qrange);
  renderCustoms();
  renderQuarters();
  renderCustomers();
  renderProducts();
  renderProvenance();
}

function load() {
  const code = urlState().code;
  getJSON(API + "/lens/" + encodeURIComponent(code)).then(payload => {
    L = payload;
    $("stale-banner").innerHTML = "";
    renderAll();
  }).catch(err => {
    $("stale-banner").innerHTML = '<div class="banner" role="alert">This company could not be loaded (' +
      escapeHtml(err.message) + "). Nothing on this page is showing a stale number in the meantime.</div>";
  });
}

(function init() {
  initThemeToggle(() => { if (L) { renderCustoms(); renderQuarters(); } });
  $("view-seg").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    setUrlState({ view: b.getAttribute("data-view") }); pressGroup("view-seg", "data-view", b.getAttribute("data-view")); renderCustoms();
  });
  $("qrange-seg").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    setUrlState({ qrange: b.getAttribute("data-qrange") }); pressGroup("qrange-seg", "data-qrange", b.getAttribute("data-qrange")); renderQuarters();
  });
  initSearch();
  load();
})();
