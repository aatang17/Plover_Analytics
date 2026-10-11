/* PloverResearch article pages, and the chart drawing the research desk
   shares with them.

   On an article page every chart is drawn from the data embedded in the page
   (#rs-data): the numbers as they stood on the version's publication date,
   frozen when it was published. Nothing here asks the API for live data, so
   a chart can never drift away from the text written about it. The desk
   (desk.js) calls the same researchChart.cfg() on live data while writing,
   so the preview and the published chart are drawn by one function. */
"use strict";

var researchChart = (function () {
  var MEASURE_CAPTION = {
    yoy: "% change on a year earlier",
    mom: "% change on the previous month",
    ann3m: "% three-month change, annualised",
  };

  function caption(snap) {
    return MEASURE_CAPTION[snap.measure] || snap.unit || "";
  }

  function sourceLine(snap) {
    var rel = snap.release || {};
    var parts = [snap.credit || "", rel.source_name || "",
                 rel.label ? "Release " + rel.label : "",
                 snap.as_of ? "Data as of " + snap.as_of : "Live data",
                 snap.trust === "official" ? "Values as published" : ""];
    return parts.filter(function (p) { return p && p.trim(); })
      .map(function (p) { return p.trim().replace(/\.$/, ""); }).join(" · ") + ".";
  }

  /* obsChart "line" config from a block and its observations response. */
  function cfg(block, snap) {
    var pct = snap.measure && snap.measure !== "index";
    return {
      series: snap.series.map(function (s, i) {
        return { name: s.name_en || s.code, slot: i + 1, points: s.points };
      }),
      unit: pct ? "%" : snap.unit,
      unitSuffix: pct ? "" : (snap.unit && snap.unit !== "index" ? snap.unit : ""),
      yAxisName: caption(snap),
      trust: null,   // research pages carry no trust badge; the source line says it
      sourceLine: (block.title ? block.title + " — " : "") + sourceLine(snap),
      isoPeriods: snap.frequency === "daily",
      dp: pct ? 1 : undefined,
    };
  }

  function draw(el, block, snap) {
    return obsChart(el, "line", cfg(block, snap));
  }

  /* A chart copied from a Plover page: the page's own kind and config, so it
     is drawn exactly as the page drew it. Rankings and distributions get the
     height their rows need. */
  function drawSnapshot(el, entry, fixedHeight) {
    var c = entry.cfg || {};
    var rows = (c.rows || c.items || []).length;
    if (!fixedHeight && (entry.kind === "rank" || entry.kind === "bar") && rows) {
      el.style.height = Math.max(320, Math.min(1400, rows * 24 + 80)) + "px";
    }
    var copy = JSON.parse(JSON.stringify(c));
    copy.trust = null;
    copy.sourceLine = (entry.title ? entry.title + " \u2014 " : "") + (entry.source || c.sourceLine || "");
    return obsChart(el, entry.kind, copy);
  }

  /* ---- charts of the writer's own numbers (datachart blocks) ----
     The cells as pasted are kept in the article; these read them the same
     way research_doc.py's data_number / data_table do. */
  var MISSING = ["", "-", "\u2014", "\u2013", "n/a", "na", "#n/a", "..", "\u2026", "nan", "null", "none"];
  var MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

  function isMissing(cell) { return MISSING.indexOf(String(cell || "").trim().toLowerCase()) !== -1; }

  /* "1,234.5", "(2.1)", "−3%", "$40" are numbers; blanks and dashes are missing, never zero. */
  function number(cell) {
    var s = String(cell || "").trim();
    if (isMissing(s)) return null;
    var neg = s.charAt(0) === "(" && s.charAt(s.length - 1) === ")";
    if (neg) s = s.slice(1, -1);
    s = s.replace(/\u2212/g, "-").replace(/[,\s\u00a0]/g, "").replace(/^([+\-]?)[\u00a5$\u20ac\u00a3]/, "$1");
    var m = /^([+\-]?)(\d*\.?\d+(?:[eE][+\-]?\d+)?)%?$/.exec(s);
    if (!m) return null;
    var v = Number(m[2]) * (m[1] === "-" ? -1 : 1);
    return neg ? -v : v;
  }

  function decimals(cell) {
    var m = /\.(\d+)/.exec(String(cell || "").replace(/[eE].*$/, ""));
    return m ? m[1].length : 0;
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  /* One date cell: {sort, label, iso, p} where p is y(ear), q(uarter), m(onth) or d(ay). */
  function readDate(s, dmy) {
    var m;
    s = String(s || "").trim();
    if ((m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$/.exec(s)) && !isNaN(Date.parse(s))) {
      return { p: "h", iso: m[1] };
    }
    if ((m = /^(\d{4})$/.exec(s))) return { p: "y", label: m[1], sort: m[1] };
    if ((m = /^(\d{4})\s*-?\s*Q([1-4])$/i.exec(s)) || (m = /^Q([1-4])\s*-?\s*(\d{4})$/i.exec(s))) {
      var y = m[1].length === 4 ? m[1] : m[2], q = m[1].length === 4 ? m[2] : m[1];
      return { p: "q", label: y + " Q" + q, sort: y + "-Q" + q };
    }
    if ((m = /^(\d{4})[-\/.](\d{1,2})$/.exec(s)) && +m[2] >= 1 && +m[2] <= 12) {
      return { p: "m", iso: m[1] + "-" + pad(+m[2]) };
    }
    if ((m = /^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})(?:[ T].*)?$/.exec(s)) && +m[2] <= 12 && +m[3] <= 31) {
      return { p: "d", iso: m[1] + "-" + pad(+m[2]) + "-" + pad(+m[3]) };
    }
    if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s))) {
      var mo = dmy ? +m[2] : +m[1], d = dmy ? +m[1] : +m[2];
      if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31) return { p: "d", iso: m[3] + "-" + pad(mo) + "-" + pad(d) };
    }
    if ((m = /^([A-Za-z]{3,9})\.?[\s\-\/']*(\d{2}|\d{4})$/.exec(s))) {
      var k = MONTHS.indexOf(m[1].slice(0, 3).toLowerCase());
      if (k !== -1) {
        var yr = m[2].length === 2 ? (+m[2] < 50 ? 2000 : 1900) + +m[2] : +m[2];
        return { p: "m", iso: yr + "-" + pad(k + 1) };
      }
    }
    return null;
  }

  /* The first column as dates, or null when any filled cell is not one. A
     slash date is day-first only when some first part is above 12. */
  function readDates(labels) {
    var dmy = labels.some(function (l) { var m = /^(\d{1,2})\/\d{1,2}\/\d{4}$/.exec(String(l).trim()); return m && +m[1] > 12; });
    var out = labels.map(function (l) { return readDate(l, dmy); });
    if (!out.length || out.some(function (d) { return !d; })) return null;
    var kinds = {};
    out.forEach(function (d) { kinds[d.p] = 1; });
    var time = !kinds.y && !kinds.q;
    if (!time && Object.keys(kinds).length > 1) return null;   // years mixed with months
    out.forEach(function (d) {
      if (time) { d.sort = d.iso; d.label = d.iso; }
    });
    return { list: out, time: time, daily: !!kinds.d, hourly: !!kinds.h };
  }

  /* The pasted cells as a chart: names, labels, values, dates and how many
     filled cells were not numbers. Rows with dates are put in date order. */
  function table(rows) {
    rows = (rows || []).filter(function (r) { return r.some(function (c) { return String(c || "").trim(); }); });
    var width = rows.reduce(function (w, r) { return Math.max(w, r.length); }, 0);
    var empty = { names: [], labels: [], values: [], bad: 0, dates: null, dp: 0, header: false };
    if (width < 2) return empty;
    var head = rows[0];
    var header = head.slice(1).some(function (c) { return String(c || "").trim() && number(c) === null; });
    var body = header ? rows.slice(1) : rows;
    var names = [];
    for (var i = 1; i < width; i++) names.push((header && String(head[i] || "").trim()) || "Series " + i);
    var dates = readDates(body.map(function (r) { return String(r[0] || "").trim(); }));
    var order = body.map(function (r, k) { return k; });
    if (dates) order.sort(function (a, b) { return dates.list[a].sort < dates.list[b].sort ? -1 : dates.list[a].sort > dates.list[b].sort ? 1 : a - b; });
    var bad = 0, dp = 0;
    var values = names.map(function (n, j) {
      return order.map(function (k) {
        var cell = body[k][j + 1];
        var v = number(cell);
        if (v === null && !isMissing(cell)) bad++;
        if (v !== null) dp = Math.max(dp, decimals(cell));
        return v;
      });
    });
    return {
      names: names, values: values, bad: bad, header: header, dp: Math.min(dp, 3),
      labels: order.map(function (k) { return dates ? dates.list[k].label : String(body[k][0] || "").trim(); }),
      dates: dates,
    };
  }

  function dataSource(block) {
    var src = (block.source || "").trim().replace(/\.$/, "");
    return (src ? "Source: " + src + " · " : "") +
      (block.origin === "database" ? "Values from saved Plover data." : "Values as entered by the author.");
  }

  /* {kind, cfg} for obsChart, or null when there is nothing to draw. Bars
     for labels (countries, sectors); a line for dates unless bars are asked for. */
  function dataCfg(block) {
    var t = table(block.rows);
    if (!t.values.some(function (col) { return col.some(function (v) { return v !== null; }); })) return null;
    var unit = (block.unit || "").trim();
    var pct = unit === "%";
    var common = {
      yAxisName: unit, trust: null, dp: t.dp,
      sourceLine: (block.title ? block.title + " \u2014 " : "") + dataSource(block),
    };
    if (block.kind === "bar" || !t.dates) {
      return { kind: "cols", table: t, cfg: Object.assign(common, {
        categories: t.labels,
        series: t.names.map(function (n, i) { return { name: n, slot: i + 1, points: t.values[i] }; }),
        unitSuffix: pct ? "%" : unit,
        labelInterval: t.labels.length > 12 ? "auto" : undefined,
      }) };
    }
    return { kind: "line", table: t, cfg: Object.assign(common, {
      series: t.names.map(function (n, i) {
        return { name: n, slot: i + 1, points: t.labels.map(function (l, k) { return [l, t.values[i][k]]; }) };
      }),
      unit: pct ? "%" : "",
      unitSuffix: pct ? "" : unit,
      hourlyPeriods: t.dates.hourly,
      isoPeriods: t.dates.daily,
      xType: t.dates.time ? undefined : "category",
    }) };
  }

  function drawData(el, block) {
    var d = dataCfg(block);
    return d ? obsChart(el, d.kind, d.cfg) : null;
  }

  /* The numbers as read, with the source in the header: a missing cell stays blank. */
  function dataCSV(block, headerLines) {
    var t = table(block.rows);
    var q = function (s) { return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    var csv = (headerLines || []).map(function (l) { return "# " + l; }).join("\n") + "\n";
    csv += ["period"].concat(t.names).map(q).join(",") + "\n";
    t.labels.forEach(function (l, k) {
      csv += [q(l)].concat(t.values.map(function (col) { return col[k] === null ? "" : col[k]; })).join(",") + "\n";
    });
    return csv;
  }

  /* A thumbnail: the same chart with its axes, legend and tooltip taken away,
     so a list of notes reads as a row of small pictures of the data. */
  function strip(el) {
    var inst = window.echarts && echarts.getInstanceByDom(el);
    if (!inst) return;
    var o = inst.getOption();
    inst.setOption({
      animation: false,
      legend: (o.legend || []).map(function () { return { show: false }; }),
      tooltip: { show: false },
      grid: (o.grid || [{}]).map(function () { return { left: 4, right: 4, top: 8, bottom: 4, containLabel: false }; }),
      xAxis: (o.xAxis || []).map(function () { return { show: false }; }),
      yAxis: (o.yAxis || []).map(function () { return { show: false }; }),
      graphic: [],
      series: (o.series || []).map(function () {
        return { markPoint: { data: [] }, label: { show: false }, emphasis: { disabled: true } };
      }),
    });
  }

  return { cfg: cfg, draw: draw, drawSnapshot: drawSnapshot, strip: strip, sourceLine: sourceLine, caption: caption,
           number: number, table: table, dataCfg: dataCfg, drawData: drawData, dataCSV: dataCSV,
           dataSource: dataSource };
})();

(function articlePage() {
  // The desk loads this file for researchChart only; it owns its own toggle.
  if (!document.querySelector("main.rs-wrap")) return;
  var holder = document.getElementById("rs-data");
  // The theme toggle lives in the shared header on every research page,
  // including the list, which carries no chart data.
  var charts = {};
  var thumbs = [];
  initThemeToggle(function () {
    Object.keys(charts).forEach(function (k) { charts[k].render(); });
    thumbs.forEach(researchChart.strip);
  });
  if (!holder) return;
  var data;
  try { data = JSON.parse(holder.textContent); } catch (e) { return; }

  // Homepage and "More from" figures: the lead story's chart, Chart of the
  // Week and the thumbnails, each drawn from its article's frozen data.
  Object.keys(data.figures || {}).forEach(function (key) {
    var entry = data.figures[key];
    var el = document.querySelector('.rh-plot[data-fig="' + key + '"]');
    if (!el || !entry) return;
    var thumb = el.classList.contains("rh-plot-card");
    charts["fig:" + key] = entry.data ? researchChart.drawData(el, entry.block)
      : entry.kind ? researchChart.drawSnapshot(el, entry, thumb)
      : researchChart.draw(el, entry.block, entry.snap);
    if (thumb) { thumbs.push(el); researchChart.strip(el); }
  });
  homeFilters();

  Object.keys(data.charts || {}).forEach(function (id) {
    var entry = data.charts[id];
    var el = document.querySelector('.rs-plot[data-chart="' + id + '"]');
    if (!el) return;
    charts[id] = entry.data ? researchChart.drawData(el, entry.block)
      : entry.kind ? researchChart.drawSnapshot(el, entry) : researchChart.draw(el, entry.block, entry.snap);
  });

  // Download CSV for a chart copied from a Plover page: its own data, with
  // the page, the source and the capture date in the header.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-csv]");
    if (!btn) return;
    var id = btn.getAttribute("data-csv");
    var entry = data.charts[id];
    if (!charts[id] || !entry) return;
    var n = btn.closest("figure") ? btn.closest("figure").id : "chart";
    var name = (data.slug || "plover-research") + "-v" + data.version + "-" + n + ".csv";
    if (entry.data) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([researchChart.dataCSV(entry.block, [
        "PloverResearch — " + (entry.title || ""), entry.source || "",
        "From " + location.origin + location.pathname,
        "Blank cells are missing values, never zero.",
      ])], { type: "text/csv" }));
      a.download = name;
      a.click();
      URL.revokeObjectURL(a.href);
      return;
    }
    charts[id].exportCSV((data.slug || "plover-research") + "-v" + data.version + "-" + n + ".csv", [
      "PloverResearch — " + (entry.title || ""),
      entry.source || "",
      "Copied from " + location.origin + entry.url,
      "Blank cells are missing values, never zero.",
    ]);
  });

  // Download PNG: the export menu (size, copy, download) every chart on the
  // platform uses, light theme and source line included.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-png]");
    if (!btn) return;
    var id = btn.getAttribute("data-png");
    if (!charts[id]) return;
    var n = btn.closest("figure") ? btn.closest("figure").id : "chart";
    charts[id].exportPNG((data.slug || "plover-research") + "-v" + data.version + "-" + n + ".png");
  });

  // In-page links (footnotes): the page sets <base href="/"> so the shared
  // header's relative links work, which would send "#fn-1" to the home page.
  document.addEventListener("click", function (e) {
    var a = e.target.closest('a[href^="#"]');
    if (!a) return;
    var target = document.getElementById(a.getAttribute("href").slice(1));
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ block: "center" });
    history.replaceState(null, "", location.pathname + a.getAttribute("href"));
    target.classList.add("rs-flash");
    setTimeout(function () { target.classList.remove("rs-flash"); }, 1200);
  });

  /* Market tabs on the homepage cards. The choice is kept in the address
     (?market=jp) so a filtered view can be shared. */
  function homeFilters() {
    var list = document.getElementById("rh-list");
    if (!list || !document.querySelector(".rh-tabs")) return;
    var market = new URLSearchParams(location.search).get("market") || "";
    function apply() {
      var shown = 0;
      Array.prototype.forEach.call(list.querySelectorAll(".rh-card"), function (card) {
        card.hidden = !!market && card.getAttribute("data-market") !== market;
        if (!card.hidden) shown++;
      });
      document.getElementById("rh-none").hidden = shown > 0;
      Array.prototype.forEach.call(document.querySelectorAll(".rh-tabs [data-market]"), function (b) {
        b.setAttribute("aria-pressed", String(b.getAttribute("data-market") === market));
      });
      var q = new URLSearchParams(location.search);
      if (market) q.set("market", market); else q.delete("market");
      var qs = q.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
      window.dispatchEvent(new Event("resize"));   // cards that were hidden
    }
    document.addEventListener("click", function (e) {
      var m = e.target.closest(".rh-tabs [data-market]");
      if (m) { market = m.getAttribute("data-market"); apply(); }
      else if (e.target.id === "rh-clear") { market = ""; apply(); }
    });
    apply();
  }

  var link = document.getElementById("rs-copy-link");
  if (link) {
    link.addEventListener("click", function () {
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(link.getAttribute("data-url")).then(function () {
        link.classList.add("done");
        link.setAttribute("title", "Link copied");
        setTimeout(function () { link.classList.remove("done"); link.setAttribute("title", "Copy link"); }, 1600);
      });
    });
  }
  var printBtn = document.getElementById("rs-print");
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });

  var copy = document.getElementById("rs-cite-copy");
  if (copy) {
    copy.addEventListener("click", function () {
      var text = document.getElementById("rs-cite").textContent;
      var done = function () { copy.textContent = "Copied"; };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      }
    });
  }
})();
