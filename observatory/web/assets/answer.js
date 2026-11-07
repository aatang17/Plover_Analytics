/* An answer page: one question, one series, one chart.

   The server writes the lead sentence, the comparison line, the table and
   the citation into the page before it is served (app/answers.py via
   app/prerender.py), so a reader — or a crawler that runs no script — has
   the answer in the HTML. This script draws the chart, which needs the
   full history, and fills the same elements only if the server left them
   empty (a database mid-swap, a page served from a plain file server).

   The page declares itself on <main>:
     data-answer    the page's slug, which is also its /api/v1/answers key
     data-dataset   the dataset the chart reads
     data-series    the series code
     data-measure   what the chart draws: yoy, ann3m or index (as published)
     data-file-tag  the stem of exported file names */
"use strict";

(function () {
  const MAIN = document.querySelector("main[data-answer]");
  if (!MAIN) return;
  const SLUG = MAIN.getAttribute("data-answer");
  const DATASET = MAIN.getAttribute("data-dataset");
  const SERIES = MAIN.getAttribute("data-series");
  const MEASURE = MAIN.getAttribute("data-measure") || "index";
  const FILE_TAG = MAIN.getAttribute("data-file-tag") || SLUG;
  const $ = id => document.getElementById(id);

  let BLOCK = null;
  let OBS = null;
  let chart = null;
  let range = MAIN.getAttribute("data-default-range") || "10";

  function text(id, value) {
    const el = $(id);
    if (!el || el.getAttribute("data-filled") === "1") return;
    if (value) { el.textContent = value; el.setAttribute("data-filled", "1"); }
  }

  function tableHTML(t) {
    if (!t || !t.rows || !t.rows.length) return "";
    const head = t.columns.map(c =>
      "<th" + (c.num ? ' class="num"' : "") + ">" + escapeHtml(c.label) + "</th>").join("");
    const body = t.rows.map(r => "<tr>" + r.map((v, i) =>
      "<td" + (t.columns[i].num ? ' class="num"' : "") + ">" + escapeHtml(v) + "</td>").join("") +
      "</tr>").join("");
    return '<div class="table-wrap"><table class="data" data-no-enhance>' +
      (t.caption ? "<caption>" + escapeHtml(t.caption) + "</caption>" : "") +
      "<thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  function fillFromBlock() {
    if (!BLOCK) return;
    text("answer-lead", BLOCK.sentence);
    text("answer-context", BLOCK.context);
    text("answer-asof", BLOCK.asof);
    text("page-asof", BLOCK.release);
    text("answer-cite", BLOCK.cite);
    const tbl = $("answer-table");
    if (tbl && tbl.getAttribute("data-filled") !== "1") {
      tbl.innerHTML = tableHTML(BLOCK.table);
      tbl.setAttribute("data-filled", "1");
    }
    const calc = $("answer-calc");
    if (calc && calc.getAttribute("data-filled") !== "1") {
      if (BLOCK.calc) {
        calc.innerHTML = "<summary>Show calculation</summary>" +
          '<div class="calc-body">' + escapeHtml(BLOCK.calc) + "</div>";
        calc.setAttribute("data-filled", "1");
      } else {
        calc.remove();
      }
    }
    const links = $("answer-links");
    if (links && links.getAttribute("data-filled") !== "1" && BLOCK.links) {
      links.innerHTML = BLOCK.links.map(l =>
        '<a href="' + escapeHtml(l[1]) + '">' + escapeHtml(l[0]) + "</a>").join(" · ");
      links.setAttribute("data-filled", "1");
    }
  }

  function sourceLine() {
    const s = (BLOCK && BLOCK.source) || {};
    const parts = [];
    if (BLOCK && BLOCK.credit) parts.push(BLOCK.credit.replace(/\.$/, ""));
    if (s.id) parts.push(s.id);
    if (BLOCK && BLOCK.release) parts.push(BLOCK.release);
    parts.push(MEASURE === "index" ? "as published" : "calculated from published values");
    return parts.join(" · ");
  }

  function renderProvenance() {
    const el = $("prov-card");
    if (!el || !BLOCK || !BLOCK.source) return;
    const s = BLOCK.source;
    el.innerHTML =
      '<div class="prov-card">' +
        '<div class="prov-card-head">' +
          '<div class="prov-card-title">Data Source</div>' +
          '<div class="prov-card-id">' + escapeHtml(s.id || "") + "</div>" +
        "</div>" +
        '<div class="prov-grid">' +
          '<div class="prov-field full">' +
            '<div class="prov-label">Official source</div>' +
            '<div class="prov-value">' + (s.page ?
              '<a href="' + escapeHtml(s.page) + '" rel="noopener">' + escapeHtml(s.name || s.page) + "</a>" :
              escapeHtml(s.name || "")) + "</div>" +
            '<div class="prov-sub">' + escapeHtml(BLOCK.credit || "") + "</div>" +
          "</div>" +
          '<div class="prov-field">' +
            '<div class="prov-label">Release</div>' +
            '<div class="prov-value">' + escapeHtml(BLOCK.release || "") + "</div>" +
            '<div class="prov-sub">Series ' + escapeHtml(SERIES) + " · " + escapeHtml(DATASET) + "</div>" +
          "</div>" +
          '<div class="prov-field">' +
            '<div class="prov-label">Retrieved</div>' +
            '<div class="prov-value num">' + fmtStamp(s.retrieved) + "</div>" +
            '<div class="prov-sub">Archived ' + fmtStamp(s.ingested) + "</div>" +
          "</div>" +
          '<div class="prov-field full">' +
            '<div class="prov-label">Trust</div>' +
            '<div class="prov-value">' + trustBadge(BLOCK.trust) +
              (BLOCK.trust === "derived" ? " Rates are calculated from published values; the formula is under Show calculation." :
                " Values are exactly as published by the source.") + "</div>" +
          "</div>" +
        "</div>" +
      "</div>";
  }

  function rangeCutoff() {
    if (!OBS || !OBS.series || !OBS.series.length || range === "max") return null;
    const all = OBS.series[0].points;
    if (!all.length) return null;
    const last = all[all.length - 1][0];
    return (parseInt(last.slice(0, 4), 10) - parseInt(range, 10)) + last.slice(4);
  }

  function points() {
    if (!OBS || !OBS.series || !OBS.series.length) return [];
    const cutoff = rangeCutoff();
    const all = OBS.series[0].points;
    return cutoff ? all.filter(p => p[0] >= cutoff) : all;
  }

  function drawChart() {
    const el = $("answer-chart");
    if (!el || !OBS) return;
    const pts = points();
    if (!pts.length) { el.innerHTML = '<p class="empty">No values to draw.</p>'; return; }
    const unit = OBS.unit || (MEASURE === "index" ? "" : "%");
    const daily = OBS.frequency === "daily";
    const cfg = {
      series: [{ name: MAIN.getAttribute("data-series-label") || OBS.series[0].name_en,
                 slot: 1, points: pts }],
      unit: unit === "%" ? "%" : "index",
      yAxisName: MAIN.getAttribute("data-y-axis") || unit,
      trust: BLOCK ? BLOCK.trust : (MEASURE === "index" ? "official" : "derived"),
      sourceLine: sourceLine(),
      isoPeriods: daily,
      dp: MEASURE === "index" ? (daily ? 3 : 0) : 1,
    };
    el.innerHTML = "";
    if (chart) chart.dispose();
    chart = obsChart(el, "line", cfg);
    const src = $("answer-source");
    if (src) src.textContent = sourceLine();
  }

  function wireControls() {
    const seg = $("range-seg");
    if (seg) {
      seg.querySelectorAll("button").forEach(b => {
        b.setAttribute("aria-pressed", b.getAttribute("data-range") === range ? "true" : "false");
        b.onclick = () => {
          range = b.getAttribute("data-range");
          seg.querySelectorAll("button").forEach(o =>
            o.setAttribute("aria-pressed", o === b ? "true" : "false"));
          drawChart();
        };
      });
    }
    const png = $("answer-png");
    if (png) png.onclick = () => chart && chart.exportPNG(FILE_TAG + ".png");
    const csv = $("answer-csv");
    if (csv) csv.onclick = () => chart && chart.exportCSV(FILE_TAG + ".csv", [
      (BLOCK && BLOCK.question) || document.title,
      "Series: " + SERIES + " (" + DATASET + ") · measure: " + MEASURE,
      sourceLine(),
      BLOCK && BLOCK.calc ? BLOCK.calc : "Values as published.",
      "Page: " + location.origin + "/" + SLUG + ".html",
    ]);
    obsCodeButton(csv, () => OBS && OBS.series && OBS.series.length ? {
      url: "/api/v1/" + DATASET + "/observations?series=" + encodeURIComponent(SERIES) +
        "&measure=" + MEASURE,
      start: rangeCutoff(), release: OBS.release, source: sourceLine(),
    } : null);
  }

  async function load() {
    try {
      const r = await fetch("/api/v1/answers/" + SLUG);
      if (r.ok) BLOCK = await r.json();
    } catch (e) { BLOCK = null; }
    fillFromBlock();
    renderProvenance();
    try {
      const r = await fetch("/api/v1/" + DATASET + "/observations?series=" +
        encodeURIComponent(SERIES) + "&measure=" + MEASURE);
      if (r.ok) OBS = await r.json();
    } catch (e) { OBS = null; }
    if (OBS && !OBS.frequency && OBS.release) OBS.frequency = OBS.release.frequency;
    if (OBS && OBS.frequency === "daily" && !MAIN.getAttribute("data-default-range")) range = "5";
    wireControls();
    drawChart();
  }

  if (typeof initThemeToggle === "function") initThemeToggle(() => drawChart());
  load();
})();
