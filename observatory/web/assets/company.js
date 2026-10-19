/* Company Profile. The question this screen answers:

     "Everything we hold on this one company — the documents it comes from, the
      statements, who owns it, who runs it, what it bought back and what it owns
      — without leaving the page."

   One composed payload (/api/v1/company/{code}) carries every dataset's view of
   one company; the three statements come from the financials reader
   (/api/v1/equity/financials/statements/{code}) and are fetched per tab, since
   a balance sheet nobody opened is 60 rows of nothing.

   Everything printed here is as filed. The few figures that are ours — the
   change column, the unspent balance, a share of a total — carry their formula
   under "Show calculation", never a badge. A dash means the filing does not
   state the number; it never means zero.

   The company universe is report-filers: a company appears here because it
   filed an annual securities report, not because someone named it in theirs. */
"use strict";

const CO_API = "/api/v1/company";
const FIN_API = "/api/v1/equity/financials";

let CO = null;          // the composed payload
let STMT = {};          // statement cache, keyed "bs" / "pl" / "cf"
let METRICS = null;

/* ---- small helpers ---- */

function $(id) { return document.getElementById(id); }

function getJSON(url) {
  return fetch(url).then(function (r) {
    if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || r.status); });
    return r.json();
  });
}

/* Yen at the scale a reader can hold in their head, with the exact figure in
   the title attribute. Never abbreviates inside a statement table — those stay
   in millions, grouped, one precision per column. */
function yenScale(v) {
  if (v === null || v === undefined) return MISSING;
  const abs = Math.abs(v);
  if (abs >= 1e12) return "¥" + fmtNum(v / 1e12, 2) + '<span class="unit"> tn</span>';
  if (abs >= 1e9) return "¥" + fmtNum(v / 1e9, 1) + '<span class="unit"> bn</span>';
  if (abs >= 1e6) return "¥" + fmtNum(v / 1e6, 0) + '<span class="unit"> mn</span>';
  return "¥" + fmtNum(v, 0);
}

/* Statement lines are filed in yen and read in millions — except the per-share
   lines, which are yen and must not be divided: an EPS of ¥295.25 shown in
   millions is 0, and a zero where a number belongs is the worst kind of wrong. */
function yenMn(v, unit) {
  if (v === null || v === undefined) return MISSING;
  if (unit === "JPYPerShares") return fmtNum(v, 2);
  if (unit && unit !== "JPY") return fmtNum(v, 0);
  return fmtNum(v / 1e6, 0);
}

function pct(v, dp) {
  if (v === null || v === undefined) return MISSING;
  return fmtNum(v, dp === undefined ? 1 : dp) + "%";
}

function count(v) {
  if (v === null || v === undefined) return MISSING;
  return fmtNum(v, 0);
}

function day(iso) { return iso ? String(iso).slice(0, 10) : MISSING; }

/* (FY − prior) ÷ |prior|. A negative or absent base has no meaningful rate, so
   it reads as missing rather than as a number nobody can defend. */
function change(cur, prior) {
  if (cur === null || cur === undefined || !prior) return MISSING;
  if (prior < 0) return MISSING;
  return fmtSigned(((cur - prior) / Math.abs(prior)) * 100, 1, "%");
}

function calc(summary, body) {
  return '<details class="calc"><summary>' + escapeHtml(summary) + "</summary><p>" +
    body + "</p></details>";
}

function cell(label, value, qualifier) {
  return '<div class="strip-cell"><div class="strip-label">' + escapeHtml(label) + "</div>" +
    '<div class="strip-value">' + value + "</div>" +
    (qualifier ? '<div class="strip-foot">' + qualifier + "</div>" : "") + "</div>";
}

function table(head, rows, opts) {
  const o = opts || {};
  return '<div class="table-wrap"><table class="data"' + (o.noSort ? " data-no-enhance" : "") +
    "><thead><tr>" + head + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
}

/* A dataset block, or null when this company has no rows in it. */
function ds(id) { return CO && CO.datasets ? CO.datasets[id] || null : null; }
function facts(id) { const b = ds(id); return b ? b.facts || {} : {}; }
function rows(id, name) {
  const b = ds(id);
  return b && b.tables && b.tables[name] ? b.tables[name] : [];
}
function nrows(id, name) {
  const b = ds(id);
  if (b && b.table_counts && b.table_counts[name] !== undefined) return b.table_counts[name];
  return rows(id, name).length;
}

/* ---- view state in the URL, so any view is citable ---- */

const state = { code: "", fin: "pl", own: "register", basis: "consolidated" };

function readState() {
  const p = new URLSearchParams(location.search);
  // ?code= is canonical (the prerenderer titles the page from it); ?c= is what
  // pages printed before this rebuild link with, and still resolves.
  state.code = (p.get("code") || p.get("c") || "").trim();
  state.fin = p.get("fin") || "pl";
  state.own = p.get("own") || "register";
  state.basis = p.get("basis") === "parent" ? "parent" : "consolidated";
}

function pushState() {
  const p = new URLSearchParams();
  if (state.code) p.set("code", state.code);
  if (state.fin !== "pl") p.set("fin", state.fin);
  if (state.own !== "register") p.set("own", state.own);
  if (state.basis !== "consolidated") p.set("basis", state.basis);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

/* ---- search ---- */

let matches = [], sel = -1, searchTimer = null;

function closeResults() {
  const box = $("results");
  box.hidden = true;
  box.innerHTML = "";
  $("q").setAttribute("aria-expanded", "false");
  matches = [];
  sel = -1;
}

function renderResults() {
  const box = $("results");
  if (!matches.length) {
    box.innerHTML = '<li class="none">No company files a report under that name.</li>';
  } else {
    box.innerHTML = matches.map(function (c, i) {
      return '<li role="option" data-code="' + escapeHtml(c.sec_code) + '" aria-selected="' +
        (i === sel) + '"><span class="co-code">' + escapeHtml(c.sec_code) + "</span>" +
        escapeHtml(c.name_en || c.name || "") +
        '<span class="co-ja">' + escapeHtml(c.name || "") + "</span></li>";
    }).join("");
  }
  box.hidden = false;
  $("q").setAttribute("aria-expanded", "true");
}

function search(term) {
  getJSON(FIN_API + "/companies?q=" + encodeURIComponent(term) + "&limit=8")
    .then(function (d) {
      matches = d.companies || [];
      sel = matches.length ? 0 : -1;
      renderResults();
    })
    .catch(function () { closeResults(); });
}

function wireSearch() {
  const q = $("q");
  q.addEventListener("input", function () {
    const term = q.value.trim();
    clearTimeout(searchTimer);
    if (!term) return closeResults();
    searchTimer = setTimeout(function () { search(term); }, 180);
  });
  q.addEventListener("keydown", function (e) {
    if (e.key === "Escape") return closeResults();
    if (!matches.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
      renderResults();
    } else if (e.key === "Enter" && sel >= 0) {
      e.preventDefault();
      open(matches[sel].sec_code);
    }
  });
  $("results").addEventListener("click", function (e) {
    const li = e.target.closest("li[data-code]");
    if (li) open(li.getAttribute("data-code"));
  });
  document.addEventListener("click", function (e) {
    const box = $("results");
    if (!box.hidden && !box.contains(e.target) && e.target !== q) closeResults();
  });
}

/* ---- identity, freshness, the rail of what we hold ---- */

const DATASET_LABEL = {
  "cross-shareholdings": "Cross-shareholdings",
  "shareholder-register": "Register",
  "large-shareholdings": "5% filings",
  "boards-and-pay": "Boards & pay",
  "agm-votes": "AGM votes",
  "buybacks": "Buybacks",
  "facilities": "Facilities & land",
  "financials": "Statements",
  "segments": "Segments",
};

const SECTION_OF = {
  "cross-shareholdings": "Ownership",
  "shareholder-register": "Ownership",
  "large-shareholdings": "Ownership",
  "boards-and-pay": "Governance",
  "agm-votes": "Governance",
  "buybacks": "Capital returns",
  "facilities": "Assets",
  "financials": "Financials",
  "segments": "Financials",
};

/* The as-of a reader cares about is the newest filing behind that dataset, not
   the ingest clock. Each block states it differently, so this is a lookup. */
function datasetAsOf(id) {
  const f = facts(id);
  if (id === "large-shareholdings") {
    const r = rows(id, "reports");
    return r.length ? "to " + day(r[0].filed_date) : MISSING;
  }
  if (id === "agm-votes") {
    const m = rows(id, "meetings");
    return m.length ? day(m[0].filed_date) : MISSING;
  }
  if (id === "buybacks") {
    return f.last_reporting_month ? "to " + String(f.last_reporting_month).slice(0, 7) : MISSING;
  }
  if (id === "financials") {
    const filings = rows(id, "filings");
    const end = f["latest_filing.period_end"];
    return (end ? fmtFiscalYear(String(end)) : MISSING) +
      (filings.length > 1 ? " · " + filings.length + " years" : "");
  }
  const end = f["filing.period_end"] || f.period_end || f["latest_filing.period_end"];
  return end ? fmtFiscalYear(String(end)) : MISSING;
}

/* The annual report every parsed-from-the-report dataset shares. */
function sourceFiling() {
  const f = facts("shareholder-register");
  if (f.doc_id) return { doc_id: f.doc_id, filed: f.filed_date, period: f.period_end, sha: f.sha256 };
  const g = facts("cross-shareholdings");
  if (g["filing.doc_id"]) {
    return { doc_id: g["filing.doc_id"], filed: g["filing.filed_date"],
             period: g["filing.period_end"], sha: g["filing.sha256"] };
  }
  const h = facts("financials");
  return { doc_id: h["latest_filing.doc_id"], filed: h["latest_filing.filed_date"],
           period: h["latest_filing.period_end"], sha: h["latest_filing.sha256"] };
}

function renderIdentity() {
  const c = CO.company || {};
  const f = sourceFiling();
  const fin = facts("financials");
  const edinet = facts("cross-shareholdings")["entity.edinet_code"] ||
    facts("shareholder-register").edinet_code;

  // The card's h1 is the page's one name, and the prerenderer has already
  // written it for a crawler — this sets the same name, never a second one.
  $("co-name").innerHTML = '<span class="co-tick">' + escapeHtml(c.sec_code) + "</span>" +
    escapeHtml(c.name_en || c.name || c.sec_code);
  $("page-asof").textContent = "FY to " + day(f.period) + " · filed " + day(f.filed);
  $("page-sub").textContent = [c.name, c.industry, edinet ? "EDINET " + edinet : "",
    fin.accounting_standard ? fin.accounting_standard +
      (fin.consolidated ? ", consolidated" : ", parent") : ""].filter(Boolean).join(" · ");

  $("identity").innerHTML =
    '<div class="co-id-filing"><b>Annual securities report to ' + day(f.period) + "</b>" +
      "filed " + day(f.filed) + " · doc " + escapeHtml(f.doc_id || MISSING) +
      (f.sha ? '<span class="co-sha" title="' + escapeHtml(f.sha) + '">sha256 ' +
        escapeHtml(f.sha.slice(0, 8)) + "…" + escapeHtml(f.sha.slice(-6)) + "</span>" : "") +
    "</div>";

  const asof = $("header-asof");
  if (asof) asof.textContent = "Filed " + day(f.filed);
}

function renderHolds() {
  const present = (CO.coverage && CO.coverage.present) || [];
  const groups = ["Ownership", "Governance", "Capital returns", "Assets", "Financials"];
  const anchors = { Ownership: "#h-own", Governance: "#h-gov", "Capital returns": "#h-cap",
                    Assets: "#h-ass", Financials: "#h-fin" };
  $("holds").innerHTML = groups.map(function (g) {
    const held = present.filter(function (id) { return SECTION_OF[id] === g; });
    if (!held.length) return "";
    return '<a class="co-hold-group" href="' + anchors[g] + '"><div class="co-hold-head">' +
      escapeHtml(g) + "</div>" +
      held.map(function (id) {
        return '<span class="co-hold"><i></i>' + escapeHtml(DATASET_LABEL[id] || id) +
          ' <em>' + escapeHtml(datasetAsOf(id)) + "</em></span>";
      }).join("") + "</a>";
  }).join("");
}

/* ---- filings: the evidence that a record belongs to this company ---- */

const REPORT_TYPE = { change: "change", new: "new", discard: "discard" };

function filingRows() {
  const out = [];
  const f = sourceFiling();
  if (f.doc_id) {
    const read = [];
    (CO.coverage.present || []).forEach(function (id) {
      if (["shareholder-register", "boards-and-pay", "cross-shareholdings", "facilities",
           "segments", "financials"].indexOf(id) >= 0) read.push(DATASET_LABEL[id]);
    });
    out.push({ filed: f.filed, doc: f.doc_id, type: "Annual securities report",
               by: (CO.company || {}).name_en || (CO.company || {}).name,
               summary: read.join(" · ") });
  }
  const months = rows("buybacks", "months");
  if (months.length) {
    out.push({ filed: months[0].submitted, doc: months[0].doc_id,
               type: "Buyback monthly report",
               by: (CO.company || {}).name_en || (CO.company || {}).name,
               summary: "Month to " + day(months[0].month) + " · treasury and cancellations" });
  }
  rows("large-shareholdings", "reports").forEach(function (r) {
    out.push({ filed: r.filed_date, doc: r.doc_id,
               type: "5% report — " + (REPORT_TYPE[r.report_type] || r.report_type || "report"),
               by: r.filer_name_en || r.filer_name, by_ja: r.filer_name_en ? r.filer_name : "",
               summary: "A holder's stake in this issuer" });
  });
  rows("agm-votes", "meetings").forEach(function (m) {
    out.push({ filed: m.filed_date, doc: m.doc_id, type: "AGM vote result",
               by: m.issuer_name_en || m.issuer_name,
               summary: count(m.proposals) + " proposal" + (m.proposals === 1 ? "" : "s") +
                        " and their counted votes" });
  });
  out.sort(function (a, b) { return String(b.filed).localeCompare(String(a.filed)); });
  return out;
}

function renderFilings() {
  const list = filingRows();
  $("filings-note").textContent = list.length + (list.length === 1 ? " held" : " held") +
    " · newest first";
  $("filings-table").innerHTML = table(
    "<th>Filed</th><th>Document</th><th>Type</th><th>Filed by</th><th>Summary</th>",
    list.map(function (r) {
      return "<tr><td>" + day(r.filed) + '</td><td><span class="co-code">' +
        escapeHtml(r.doc || MISSING) + "</span></td><td>" + escapeHtml(r.type) + "</td><td>" +
        escapeHtml(r.by || MISSING) +
        "</td><td>" + escapeHtml(r.summary || "") + "</td></tr>";
    }).join(""));
  const others = list.filter(function (r) { return r.type.indexOf("5%") === 0; }).length;
  $("filings-foot").textContent = "Each row is one archived document with its SHA-256." +
    (others ? " " + others + " of them " + (others === 1 ? "was" : "were") +
      " filed by somebody else about this company — a record is tagged by the issuer's code, " +
      "not the filer's." : "");
}

/* ---- financials ---- */

const FIN_TABS = [
  { id: "pl", label: "Income statement" },
  { id: "bs", label: "Balance sheet" },
  { id: "cf", label: "Cash flow" },
  { id: "ratios", label: "Ratios" },
  { id: "segments", label: "Segments" },
];

function renderFinTiles() {
  const m = METRICS || {};
  const size = m.size || {};
  const met = m.metrics || {};
  const filed = m.filed || {};
  const panel = rows("financials", "panel");
  const grid = $("fin-tiles");
  grid.className = "strip-grid cols-3";
  grid.innerHTML =
    cell("Revenue", yenScale(size.revenue_yen),
         met.revenue_growth_pct === undefined ? "" :
           fmtSigned(met.revenue_growth_pct, 1, "%") + " on the prior filed year") +
    cell("Profit, owners", yenScale(size.profit_yen),
         met.profit_growth_pct === undefined ? "" :
           fmtSigned(met.profit_growth_pct, 1, "%") + " on the prior filed year") +
    cell("Total assets", yenScale(size.total_assets_yen),
         met.equity_ratio_pct === undefined ? "" : "equity ratio " + pct(met.equity_ratio_pct)) +
    cell("Operating cash flow", yenScale(size.cf_operating_yen),
         met.fcf_yen == null ? "" : "free cash flow " + yenScale(met.fcf_yen).replace(/<[^>]+>/g, "")) +
    cell("Earnings per share", filed.eps === undefined ? MISSING : "¥" + fmtNum(filed.eps, 2),
         filed.dps === undefined ? "as filed" : "dividend ¥" + fmtNum(filed.dps, 2) + " · as filed") +
    cell("Years held", count(panel.length ? rows("financials", "filings").length : null),
         "annual reports parsed");
}

function statementTable(st) {
  const d = STMT[st];
  if (!d) return '<div class="skeleton" style="width:100%;height:220px"></div>';
  const lines = (d.lines || []).filter(function (l) {
    return l.current !== null && l.current !== undefined;
  });
  if (!lines.length) {
    return '<p class="table-empty">This filing carries no ' +
      escapeHtml((d.statement_name || st).toLowerCase()) + " lines we could read.</p>";
  }
  const head = "<th>" + escapeHtml(d.statement_name || "") + ", as filed</th>" +
    '<th class="num">' + day(d.period_end).slice(0, 7) + "</th>" +
    '<th class="num">' + (d.prior_period_end ? day(d.prior_period_end).slice(0, 7) : MISSING) + "</th>" +
    (st === "bs" ? "" : '<th class="num">Change</th>');
  const body = lines.map(function (l) {
    const label = (l.label_en || l.label_ja || l.element) +
      (l.unit === "JPYPerShares" ? " (¥)" : "");
    const total = l.is_total || l.depth <= 1;
    const depth = Math.min(Math.max(l.depth - 1, 0), 3);
    return '<tr' + (total ? ' class="row-total"' : "") + ">" +
      "<td" + (depth ? ' class="indent-' + depth + '"' : "") + ">" + escapeHtml(label) + "</td>" +
      '<td class="num">' + yenMn(l.current, l.unit) + "</td>" +
      '<td class="num">' + yenMn(l.prior, l.unit) + "</td>" +
      (st === "bs" ? "" : '<td class="num">' + change(l.current, l.prior) + "</td>") + "</tr>";
  }).join("");
  return table(head, body, { noSort: true }) +
    '<p class="table-foot">' + lines.length + " lines carry a figure, in \u00a5 million, from " +
    escapeHtml(d.doc_id || MISSING) + " filed " + day(d.filed_date) + ". " +
    "A dash is a line this filing does not state \u2014 missing, never zero.</p>" +
    (st === "bs" ? "" : calc("Show calculation \u2014 the change column",
      "<code>(this year \u2212 prior year) \u00f7 |prior year| \u00d7 100</code>, one decimal, from " +
      "the two filed columns of the same report. Where the prior figure is negative or absent " +
      "the cell is a dash rather than a rate nobody can defend."));
}

// The reason a platform figure is withheld, in place of its formula.
function withheld(m, key, basis) {
  const why = (m.checks || {})[key];
  return why ? "not shown: " + why : basis;
}

function ratiosTable() {
  const m = METRICS;
  if (!m) return '<div class="skeleton" style="width:100%;height:200px"></div>';
  const met = m.metrics || {}, filed = m.filed || {};
  const defs = [
    ["Return on equity", met.roe_pct, filed.roe_pct, "%", "profit to owners ÷ equity to owners"],
    ["Equity ratio", met.equity_ratio_pct, filed.equity_ratio_pct, "%", "equity to owners ÷ total assets"],
    ["Return on assets", met.roa_pct, null, "%", "profit ÷ total assets"],
    ["Operating margin", met.operating_margin_pct, null, "%", "operating profit ÷ revenue"],
    ["Net margin", met.net_margin_pct, null, "%", "profit to owners ÷ revenue"],
    ["Asset turnover", met.asset_turnover_x, null, "×", "revenue ÷ total assets"],
    ["Revenue growth", met.revenue_growth_pct, null, "%", "against the prior filed year"],
    ["Profit growth", met.profit_growth_pct, null, "%", "against the prior filed year"],
    ["Free cash flow", met.fcf_yen, null, "yen", met.fcf_yen == null && (m.checks || {}).fcf_yen_withheld
      ? "not shown: " + m.checks.fcf_yen_withheld
      : "operating cash flow − capex (plant, equipment and intangibles bought)"],
    ["Liquid cash", met.liquid_cash_yen, null, "yen", withheld(m, "cash_position_withheld",
      "cash and deposits + short-term securities (IFRS: cash and equivalents)")],
    ["Net cash", met.net_cash_yen, null, "yen", withheld(m, "cash_position_withheld",
      withheld(m, "net_cash_withheld", "liquid cash − borrowings, bonds and CP; leases excluded; negative is net debt"))],
    ["Investment securities", met.investment_securities_yen, null, "yen", withheld(m, "cash_position_withheld",
      m.accounting_standard === "IFRS" ? "not shown: IFRS balance sheets have no investment-securities line"
        : "投資有価証券 — shareholdings and long-held bonds; not counted as cash")],
    ["Liquid cash to assets", met.cash_to_assets_pct, null, "%", "liquid cash ÷ total assets"],
    ["Net cash to assets", met.net_cash_to_assets_pct, null, "%", "net cash ÷ total assets"],
    ["Earnings per share", null, filed.eps, "yen2", "filed; we do not recompute it"],
    ["Book value per share", null, filed.bps, "yen2", "filed"],
    ["Dividend per share", null, filed.dps, "yen2", "filed"],
    ["Price / earnings", null, filed.per, "×", "filed"],
  ];
  const fmt = function (v, unit) {
    if (v === null || v === undefined) return MISSING;
    if (unit === "%") return fmtNum(v, 2) + "%";
    if (unit === "×") return fmtNum(v, 2) + "×";
    if (unit === "yen") return (v < 0 ? MINUS : "") + "¥" + fmtNum(Math.abs(v) / 1e6, 0) + " mn";
    return "¥" + fmtNum(v, 2);
  };
  return table('<th>Ratio</th><th class="num">Ours</th>' +
    '<th class="num">As the company filed it</th><th>Basis</th>',
    defs.map(function (d) {
      return "<tr><td>" + escapeHtml(d[0]) + "</td>" +
        '<td class="num">' + fmt(d[1], d[3]) + "</td>" +
        '<td class="num">' + fmt(d[2], d[3]) + "</td>" +
        "<td>" + escapeHtml(d[4]) + "</td></tr>";
    }).join(""), { noSort: true }) +
    '<p class="table-foot">Where the company files a ratio, its figure and ours stand side by ' +
    "side. They differ in the last decimal because the filed one is rounded — disclosed, not " +
    "reconciled away.</p>" +
    calc("Show calculation — our ratios",
      "Each basis above is the formula, over the figures of the latest filed year: " +
      "<code>" + escapeHtml("ROE = profit attributable to owners ÷ equity attributable to owners × 100") +
      "</code>. No ratio here is smoothed, annualised or adjusted.");
}

function segmentsPane() {
  const regions = rows("segments", "regions");
  if (!regions.length) {
    return '<p class="table-empty">This company files no segment note we could read.</p>';
  }
  const years = [];
  regions.forEach(function (r) {
    if (r.fiscal_label && years.indexOf(r.fiscal_label) < 0) years.push(r.fiscal_label);
  });
  years.sort(cmpFiscalLabel);
  const latest = years[years.length - 1], prior = years[years.length - 2];
  const measures = [["revenue", "Revenue by region"], ["noncurrent", "Non-current assets by region"]];
  let html = "";
  measures.forEach(function (m) {
    const set = regions.filter(function (r) { return r.measure === m[0]; });
    if (!set.length) return;
    const total = set.filter(function (r) {
      return r.fiscal_label === latest && /total/i.test(r.region_label_en || "");
    })[0];
    const body = set.filter(function (r) { return r.fiscal_label === latest; }).map(function (r) {
      const p = set.filter(function (x) {
        return x.fiscal_label === prior && x.region_label_en === r.region_label_en;
      })[0];
      const isTotal = /total/i.test(r.region_label_en || "");
      const share = total && total.value_yen ? pct((r.value_yen / total.value_yen) * 100) : MISSING;
      return "<tr" + (isTotal ? ' class="row-total"' : "") + ">" +
        '<td class="indent-' + (r.is_subnote ? 2 : 1) + '">' +
        escapeHtml(r.region_label_en || r.label_ja || "") + "</td>" +
        '<td class="num">' + yenMn(r.value_yen) + "</td>" +
        '<td class="num">' + yenMn(p ? p.value_yen : null) + "</td>" +
        '<td class="num">' + (r.is_subnote ? MISSING : share) + "</td></tr>";
    }).join("");
    html += table("<th>" + escapeHtml(m[1]) + ", as filed</th>" +
      '<th class="num">' + escapeHtml(latest) + "</th>" +
      '<th class="num">' + escapeHtml(prior || "") + "</th>" +
      '<th class="num">Share</th>', body, { noSort: true });
  });
  const products = rows("segments", "products").filter(function (p) {
    return p.fiscal_label === latest;
  });
  if (products.length) {
    html += table("<th>Segment profit, as filed</th>" +
      '<th class="num">' + escapeHtml(latest) + "</th>",
      products.map(function (p) {
        return '<tr><td class="indent-1">' + escapeHtml(p.segment_label_ja || "") +
          '</td><td class="num">' + yenMn(p.segment_profit_yen) + "</td></tr>";
      }).join(""), { noSort: true });
  }
  return html +
    '<p class="table-foot">Sub-notes such as "of which United States" sit under their parent ' +
    "and are never added to it, so they carry no share.</p>" +
    calc("Show calculation — the share column",
      "<code>region value ÷ the filing's own total × 100</code>, one decimal. The total is the " +
      "filing's, not a sum of ours, so the column reconciles to the statement above it.");
}

function renderFinPane() {
  const pane = $("fin-pane");
  if (state.fin === "ratios") { pane.innerHTML = ratiosTable(); return; }
  if (state.fin === "segments") { pane.innerHTML = segmentsPane(); return; }
  const st = state.fin;
  if (STMT[st]) { pane.innerHTML = statementTable(st); return; }
  pane.innerHTML = '<div class="skeleton" style="width:100%;height:220px"></div>';
  getJSON(FIN_API + "/statements/" + encodeURIComponent(state.code) + "?statement=" + st)
    .then(function (d) {
      STMT[st] = d;
      if (state.fin === st) { pane.innerHTML = statementTable(st); }
    })
    .catch(function (err) {
      // Say which of the two it is: a company that files no such statement, or
      // a request that failed. "Not held" for a network blip is a lie.
      const missing = /404|not found|no .*filing/i.test(String(err && err.message));
      if (!missing) console.error("statement " + st + " failed:", err);
      pane.innerHTML = '<p class="table-empty">' + (missing
        ? "This company files no " + (st === "pl" ? "income statement" :
            st === "bs" ? "balance sheet" : "cash flow statement") + " we could read."
        : "The statement could not be loaded just now. Reload the page to try again.") +
        "</p>";
    });
}

function renderFinancials() {
  const block = ds("financials");
  if (!block) { $("sec-fin").hidden = true; return; }
  $("sec-fin").hidden = false;
  const f = facts("financials");
  $("fin-note").textContent = [f.accounting_standard,
    f.consolidated ? "consolidated" : "parent", "¥ million",
    "FY to " + day(f["latest_filing.period_end"])].filter(Boolean).join(" · ");
  $("fin-tabs").innerHTML = FIN_TABS.map(function (t) {
    return '<button type="button" role="tab" data-fin="' + t.id + '" aria-selected="' +
      (state.fin === t.id) + '">' + escapeHtml(t.label) + "</button>";
  }).join("");
  renderFinTiles();
  renderFinPane();
  $("fin-foot").textContent = "Statements are read from the annual securities report as " +
    "filed: no restatement, no adjustment, no alternative basis.";
}

/* ---- ownership ---- */

const OWN_TABS = [
  { id: "register", label: "Register" },
  { id: "holds", label: "What it holds" },
  { id: "holders", label: "Who holds it" },
  { id: "five", label: "5% filings" },
];

const HOLDER_KIND = {
  trust_bank_nominee: "Trust-bank nominee",
  foreign_nominee: "Foreign nominee",
  entity: "Company",
  individual: "Individual",
  government: "Government",
};

function ownPane() {
  if (state.own === "register") {
    const f = facts("shareholder-register");
    const holders = rows("shareholder-register", "holders");
    if (!holders.length) return '<p class="table-empty">No shareholder register is held for this company.</p>';
    const body = holders.map(function (h) {
      return "<tr><td>" + count(h.rank) + "</td><td>" + escapeHtml(h.name_raw || "") + "</td><td>" +
        escapeHtml(HOLDER_KIND[h.holder_kind] || h.holder_kind || MISSING) + '</td><td class="num">' +
        count(h.shares) + '</td><td class="num">' + pct(h.ratio_pct, 2) + "</td></tr>";
    }).join("");
    const cats = rows("shareholder-register", "categories").map(function (c) {
      return escapeHtml(c.category_en) + " " + pct(c.pct, 2);
    }).join(" · ");
    return table("<th>Rank</th><th>Shareholder, as filed</th><th>Kind</th>" +
      '<th class="num">Shares</th><th class="num">Ratio</th>', body) +
      '<p class="table-foot">Issued ' + count(f.issued_shares) + " shares · treasury " +
      count(f.treasury_shares) + " · " + count(f.shareholders_total) + " shareholders" +
      (cats ? " · " + cats : "") + ".</p>" +
      calc("Show calculation — the ratios",
        "The filer's own: <code>shares ÷ (issued − treasury) × 100</code>, as printed in the " +
        "report. Our sum of the filed ratios is " + pct(f.majors_ratio_sum_pct, 2) +
        "; the company's stated total is " + pct(f.majors_ratio_filed_pct, 2) +
        ". Both are shown — rounding in the filing, not an error.");
  }
  if (state.own === "holds") {
    const holds = rows("cross-shareholdings", "holdings");
    if (!holds.length) return '<p class="table-empty">This company names no policy shareholdings.</p>';
    const f = facts("cross-shareholdings");
    const total = f["scale.policy_total_yen"];
    const body = holds.map(function (h) {
      return "<tr><td>" + (h.held_sec_code ? '<span class="co-code">' +
        escapeHtml(h.held_sec_code) + "</span>" : "") +
        escapeHtml(h.held_name_en || h.held_name_raw || "") + '</td><td class="num">' +
        count(h.shares) + '</td><td class="num">' + yenScale(h.book_value_yen) + '</td><td class="num">' +
        (total && h.book_value_yen ? pct((h.book_value_yen / total) * 100) : MISSING) + "</td></tr>";
    }).join("");
    const flows = rows("cross-shareholdings", "flows");
    const bought = flows.reduce(function (a, x) { return a + (x.acquisition_cost_yen || 0); }, 0);
    const sold = flows.reduce(function (a, x) { return a + (x.sale_proceeds_yen || 0); }, 0);
    return table("<th>Issue held</th>" +
      '<th class="num">Shares</th><th class="num">Book value</th><th class="num">Share of total</th>', body) +
      '<p class="table-foot">' + count(nrows("cross-shareholdings", "holdings")) +
      " named holdings, " + yenScale(total).replace(/<[^>]+>/g, "") + " at carrying amount" +
      (bought ? " · bought " + yenScale(bought).replace(/<[^>]+>/g, "") : "") +
      (sold ? " · sold " + yenScale(sold).replace(/<[^>]+>/g, "") + " of proceeds" : "") + ".</p>" +
      calc("Show calculation — share of total, and share of equity",
        "<code>book value of the issue ÷ total policy holdings × 100</code>. The headline ratio is " +
        "<code>" + yenScale(total).replace(/<[^>]+>/g, "") + " ÷ " +
        yenScale(f["scale.equity_yen"]).replace(/<[^>]+>/g, "") + " equity × 100 = " +
        pct(f["scale.pct_of_equity"], 2) + "</code>. Carrying amount is parent-only while equity " +
        "is consolidated: an indicator of scale, not an accounting identity.");
  }
  if (state.own === "holders") {
    const held = rows("cross-shareholdings", "holders");
    if (!held.length) return '<p class="table-empty">No other filer names this company as a policy holding.</p>';
    return table("<th>Holder naming this company in its own filing</th>" +
      '<th class="num">Shares</th><th class="num">Book value</th>',
      held.map(function (h) {
        return "<tr><td>" + (h.holder_sec_code ? '<span class="co-code">' +
          escapeHtml(h.holder_sec_code) + "</span>" : "") +
          escapeHtml(h.holder_name_en || h.holder_name || "") + '</td><td class="num">' +
          count(h.shares) + '</td><td class="num">' + yenScale(h.book_value_yen) + "</td></tr>";
      }).join("")) +
      '<p class="table-foot">' + count(nrows("cross-shareholdings", "holders")) +
      " filers name this company. This side exists because the data is two-sided: what a " +
      "company holds, and who holds it back.</p>";
  }
  const reports = rows("large-shareholdings", "reports");
  if (!reports.length) return '<p class="table-empty">No 5% report has been filed against this issuer.</p>';
  const lf = facts("large-shareholdings");
  return table("<th>Filed</th><th>Filer</th><th>Report</th><th>Document</th>",
    reports.map(function (r) {
      return "<tr><td>" + day(r.filed_date) + "</td><td>" +
        escapeHtml(r.filer_name_en || r.filer_name || "") + "</td><td>" +
        escapeHtml((REPORT_TYPE[r.report_type] || r.report_type || "") +
          (r.change_no ? ", no. " + r.change_no : "")) + '</td><td><span class="co-code">' +
        escapeHtml(r.doc_id) + "</span></td></tr>";
    }).join("")) +
    '<p class="table-foot">' + count(nrows("large-shareholdings", "reports")) +
    " reports held. Groups standing at or above 5% on the latest filing: " +
    count(lf.groups_at_or_above_5pct) + ".</p>";
}

function renderOwnership() {
  const any = ["shareholder-register", "cross-shareholdings", "large-shareholdings"]
    .some(function (id) { return !!ds(id); });
  if (!any) { $("sec-own").hidden = true; return; }
  $("sec-own").hidden = false;
  const f = facts("shareholder-register");
  const x = facts("cross-shareholdings");
  $("own-note").textContent = "as filed, FY to " + day(f.period_end || x["filing.period_end"]);
  const grid = $("own-tiles");
  grid.className = "strip-grid cols-4";
  grid.innerHTML =
    cell("Shareholders", count(f.shareholders_total), "on the register at year end") +
    cell("Top ten", pct(f.majors_ratio_filed_pct, 2), "of voting rights, as filed") +
    cell("Held through nominees", pct(f.nominee_ratio_pct, 2),
         count(f.nominee_rows) + " trust-bank and foreign nominee lines") +
    cell("Policy shareholdings", yenScale(x["scale.policy_total_yen"]),
         count(x["scale.listed_issues"]) + " listed issues, " +
         count(x["scale.unlisted_issues"]) + " unlisted");
  $("own-tabs").innerHTML = OWN_TABS.map(function (t) {
    return '<button type="button" role="tab" data-own="' + t.id + '" aria-selected="' +
      (state.own === t.id) + '">' + escapeHtml(t.label) + "</button>";
  }).join("");
  const pane = $("own-pane");
  pane.innerHTML = ownPane();
  $("own-foot").textContent = "";
}

/* ---- governance ---- */

function renderGovernance() {
  if (!ds("boards-and-pay") && !ds("agm-votes")) { $("sec-gov").hidden = true; return; }
  $("sec-gov").hidden = false;
  const f = facts("boards-and-pay");
  const named = rows("boards-and-pay", "pay_named");
  const top = named.length ? named.reduce(function (a, b) {
    return (b.consolidated_pay_yen || 0) > (a.consolidated_pay_yen || 0) ? b : a;
  }) : null;
  $("gov-note").textContent = "board at " + day(f.period_end);
  const grid = $("gov-tiles");
  grid.className = "strip-grid cols-4";
  grid.innerHTML =
    cell("Board", count(f.board_size) + '<span class="unit"> directors</span>',
         count(f.officers_tagged) + " officers in the filing's table" +
         (f.officers_untagged ? ", " + count(f.officers_untagged) + " without a matched person" : "")) +
    // The denominator is every officer the filing tags, not the board alone:
    // a bank lists 16 directors and 32 officers, and 5 of 16 would be wrong.
    cell("Women among officers", count(f.female_officers) + '<span class="unit"> of ' +
         count(f.officers_tagged) + "</span>",
         f.female_ratio_filed === null || f.female_ratio_filed === undefined ? "as filed" :
           pct(f.female_ratio_filed * 100) + ", the ratio as filed") +
    cell("Average director age", f.avg_director_age === null || f.avg_director_age === undefined ?
         MISSING : fmtNum(f.avg_director_age, 1),
         count(f.directors_70_plus) + " aged 70 or over") +
    cell("Highest paid", top ? yenScale(top.consolidated_pay_yen) : MISSING,
         top ? escapeHtml(top.name_en || "") + ", consolidated" : "no individually named pay");

  const board = rows("boards-and-pay", "board");
  const payOf = {};
  named.forEach(function (p) { payOf[p.person_key] = p.consolidated_pay_yen; });
  let html = "";
  if (board.length) {
    html += table("<th>Seat</th><th>Director</th><th>Title, as filed</th>" +
      '<th class="num">Age</th><th class="num">Pay, ¥ mn</th>',
      board.map(function (b) {
        return "<tr><td>" + count(b.seat_no) + '</td><td><span class="co-nm">' +
          escapeHtml(b.name_en || b.name_ja || "") + "</span>" +
          "</td><td>" + escapeHtml(b.title_ja || "") + '</td><td class="num">' +
          count(b.age_at_period_end) + '</td><td class="num">' +
          (payOf[b.person_key] === undefined ? MISSING : fmtNum(payOf[b.person_key] / 1e6, 0)) +
          "</td></tr>";
      }).join(""));
  }
  const meetings = rows("agm-votes", "meetings");
  if (meetings.length) {
    html += table("<th>Meeting</th><th>Filed</th>" +
      '<th class="num">Proposals</th><th>Document</th>',
      meetings.map(function (m) {
        return "<tr><td>" + escapeHtml(m.meeting_type || "General meeting") + "</td><td>" +
          day(m.filed_date) + '</td><td class="num">' + count(m.proposals) +
          '</td><td><span class="co-code">' + escapeHtml(m.doc_id) + "</span></td></tr>";
      }).join(""));
  }
  $("gov-body").innerHTML = html;

  const parts = [];
  if (f.named_count) {
    parts.push(count(f.named_count) + " directors are named individually because they were paid " +
      "¥100mn or more — " + yenScale(f.named_sum_yen).replace(/<[^>]+>/g, "") + " between them, " +
      "against " + yenScale(f.pay_category_total_yen).replace(/<[^>]+>/g, "") + " for the category");
  }
  if (f.employees_consolidated) {
    parts.push("workforce " + count(f.employees_consolidated) + " consolidated, " +
      count(f.employees_company) + " at the parent · average age " + fmtNum(f.avg_employee_age, 1) +
      " · tenure " + fmtNum(f.avg_tenure_years, 1) + " years · average pay ¥" +
      fmtNum(f.avg_salary_yen, 0));
  }
  parts.push("a dash in the pay column means the filing does not name that person's pay");
  $("gov-foot").textContent = parts.join(" · ") + ".";
}

/* ---- capital returns ---- */

function renderCapital() {
  if (!ds("buybacks")) { $("sec-cap").hidden = true; return; }
  $("sec-cap").hidden = false;
  const programs = rows("buybacks", "programs");
  const months = rows("buybacks", "months");
  const treasury = rows("buybacks", "treasury");
  const p = programs[0] || {};
  const latestSpend = months.filter(function (m) { return m.cumulative_yen; })[0];
  const spent = latestSpend ? latestSpend.cumulative_yen : null;
  const unspent = p.authorised_yen && spent !== null ? p.authorised_yen - spent : null;
  const t = treasury[0] || {};
  $("cap-note").textContent = p.resolution_date ?
    (p.resolution_type === "board" ? "board resolution " : "resolved ") + day(p.resolution_date) : "";
  const grid = $("cap-tiles");
  grid.className = "strip-grid cols-4";
  grid.innerHTML =
    cell("Authorised", yenScale(p.authorised_yen),
         count(p.authorised_shares) + " shares" +
         (p.window_end ? " · window to " + day(p.window_end) : "")) +
    cell("Spent", yenScale(spent),
         latestSpend ? "cumulative to " + day(latestSpend.month) : "no monthly figure filed") +
    cell("Left unspent", yenScale(unspent),
         p.window_end ? "window " + (new Date(p.window_end) < new Date() ? "closed " : "closes ") +
           day(p.window_end) : "") +
    cell("Treasury shares", pct(t.treasury_pct, 2),
         count(t.treasury_shares) + " of " + count(t.shares_outstanding));

  $("cap-body").innerHTML =
    calc("Show calculation — unspent, and the treasury ratio",
      "<code>authorised − cumulative spent</code>, both as filed in the company's own monthly " +
      "reports. The treasury ratio is <code>treasury shares ÷ shares outstanding × 100</code> " +
      "from the same report. Neither rate is published by the company; both are ours, from its " +
      "filed totals.") +
    table("<th>Month</th><th>Reported</th><th>Document</th>" +
      '<th class="num">Cumulative spent</th><th class="num">Treasury shares</th>',
      months.slice(0, 6).map(function (m) {
        const tr = treasury.filter(function (x) { return x.month === m.month; })[0] || {};
        return "<tr><td>" + day(m.month) + "</td><td>" + day(m.submitted) +
          '</td><td><span class="co-code">' + escapeHtml(m.doc_id || MISSING) +
          '</span></td><td class="num">' + yenScale(m.cumulative_yen) + '</td><td class="num">' +
          count(tr.treasury_shares) + "</td></tr>";
      }).join(""), { noSort: true });
  const cancels = treasury.filter(function (x) { return x.cancelled_shares; })[0];
  $("cap-foot").textContent = count(facts("buybacks").filings) + " monthly reports held, " +
    String(facts("buybacks").first_reporting_month || "").slice(0, 7) + " to " +
    String(facts("buybacks").last_reporting_month || "").slice(0, 7) +
    (cancels ? " · " + count(cancels.cancelled_shares) + " shares cancelled in the period" : "") +
    ". A dash is a month with no purchase line filed, not a month of zero purchases.";
}

/* ---- assets ---- */

function renderAssets() {
  if (!ds("facilities")) { $("sec-ass").hidden = true; return; }
  $("sec-ass").hidden = false;
  const f = facts("facilities");
  const fac = rows("facilities", "facilities");
  const segs = fac.filter(function (r) { return r.is_summary || /合計/.test(r.segment || ""); });
  $("ass-note").textContent = "property table, FY to " + day(f.period_end);
  $("ass-body").innerHTML = table("<th>Segment, as filed</th>" +
    '<th class="num">Buildings</th><th class="num">Land</th>',
    (segs.length ? segs : fac.slice(0, 8)).map(function (r) {
      const total = /合計/.test(r.segment || "") || /合計/.test(r.name || "");
      return '<tr class="' + (total ? "fin-sum" : "") + '"><td>' +
        escapeHtml(r.segment || r.name || "") + '</td><td class="num">' +
        yenScale(r.buildings_yen) + '</td><td class="num">' + yenScale(r.land_yen) + "</td></tr>";
    }).join(""), { noSort: true });
  const bs = f.bs_land_yen, tab = f.fac_land_book_yen;
  $("ass-foot").textContent =
    (bs && tab && Math.abs(bs - tab) / tab > 0.05
      ? "Land is " + yenScale(tab).replace(/<[^>]+>/g, "") + " in the property table and " +
        yenScale(bs).replace(/<[^>]+>/g, "") + " on the balance sheet — the table is the group's, " +
        "the balance-sheet line the parent's. Both are printed; reconciling them would mean " +
        "inventing a number neither filing contains. "
      : "") +
    count(f.n_rows) + " rows, " + count(f.n_geocoded) + " of them placed on a map.";
}

/* ---- provenance ---- */

function renderProvenance() {
  const f = sourceFiling();
  const docs = filingRows().slice(0, 8).map(function (r) { return r.doc; }).filter(Boolean);
  $("provenance").innerHTML = "<b>Provenance.</b> Every figure on this page comes from " +
    "documents filed on EDINET and archived here with their SHA-256 — " +
    escapeHtml(docs.slice(0, 4).join(", ")) +
    (docs.length > 4 ? " and " + (docs.length - 4) + " more" : "") + ". Filed values are exact " +
    "as published; figures marked with a calculation are ours. " +
    '<span class="badge badge-official">Official · as filed</span> ' +
    '<a href="' + CO_API + "/" + encodeURIComponent(state.code) + '">This company as JSON</a> · ' +
    '<a href="customs-lens.html?code=' + encodeURIComponent(state.code) +
    '">Filed revenue against customs exports</a>';
}

/* Sort, filter and export come from assets/sortable.js, which enhances every
   table on render and re-render by itself — there is nothing to call here, and
   a table that should stay as-is says so with data-no-enhance. */

/* ---- load ---- */

function open(code) {
  state.code = String(code || "").trim();
  state.fin = "pl";
  state.own = "register";
  STMT = {};
  METRICS = null;
  closeResults();
  $("q").value = "";
  pushState();
  load();
}

function load() {
  if (!state.code) {
    $("profile").hidden = true;
    $("empty").hidden = false;
    $("empty").innerHTML = "<p>Find a company to see everything filed on it — the documents, " +
      "the statements, the register, the board, the buyback and the property table.</p>" +
      '<p class="co-empty-hint">Try ' +
      ["7203", "8306", "6758", "6502"].map(function (c) {
        return '<button type="button" class="co-try" data-code="' + c + '">' + c + "</button>";
      }).join(" · ") + "</p>";
    return;
  }
  $("empty").hidden = true;
  $("profile").hidden = false;
  document.title = state.code + " · Company Profile · Plover Analytics";

  Promise.all([
    getJSON(CO_API + "/" + encodeURIComponent(state.code) + "?limit=25"),
    getJSON(FIN_API + "/metrics/" + encodeURIComponent(state.code)).catch(function () { return null; }),
  ]).then(function (res) {
    // the company API answers an unknown code with every dataset missing
    const held = ((res[0] && res[0].coverage) || {}).present || [];
    if (!held.length && !res[1]) throw new Error("no company");
    CO = res[0];
    METRICS = res[1];
    renderIdentity();
    renderHolds();
    renderFilings();
    renderFinancials();
    renderOwnership();
    renderGovernance();
    renderCapital();
    renderAssets();
    renderProvenance();
  }).catch(function (err) {
    $("profile").hidden = true;
    $("empty").hidden = false;
    document.title = "No Company " + state.code + " · Company Profile · Plover Analytics";
    $("empty").innerHTML = "<p>We hold no company under the code " +
      escapeHtml(state.code) + ".</p><p class=\"co-empty-hint\">The profile covers companies " +
      "that have filed an annual securities report. Search by name above.</p>";
  });
}

/* ---- wiring ---- */

document.addEventListener("DOMContentLoaded", function () {
  readState();
  wireSearch();
  if (window.initThemeToggle) initThemeToggle();

  $("fin-tabs").addEventListener("click", function (e) {
    const b = e.target.closest("button[data-fin]");
    if (!b) return;
    state.fin = b.getAttribute("data-fin");
    pushState();
    Array.prototype.forEach.call(this.children, function (o) {
      o.setAttribute("aria-selected", String(o === b));
    });
    renderFinPane();
  });

  $("own-tabs").addEventListener("click", function (e) {
    const b = e.target.closest("button[data-own]");
    if (!b) return;
    state.own = b.getAttribute("data-own");
    pushState();
    Array.prototype.forEach.call(this.children, function (o) {
      o.setAttribute("aria-selected", String(o === b));
    });
    $("own-pane").innerHTML = ownPane();
  });

  $("empty").addEventListener("click", function (e) {
    const b = e.target.closest("button[data-code]");
    if (b) open(b.getAttribute("data-code"));
  });

  load();
});
