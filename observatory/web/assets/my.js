/* my.html: Plover, the signed-in home: a chat over the data, a company list,
   saved reading and the person's AI key, one tab each.

   Chat asks questions about the data (the Investment Assistant's Studies,
   /api/v1/assistant/studies; one chat is one study), Companies keeps a list
   (/coverage, with each one's next results date from the JPX calendar), and
   Reading holds what you saved from your browser (/reading, app/assistant/reading.py).
   Every delete has an Undo; every wait says what it is doing; every failure
   says what to do next. Charts are drawn from the server's chart spec
   exactly as the Assistant draws them (assistant.js chartCfg); nothing here
   computes a value. */
"use strict";

(function () {
  var A = "/api/v1/assistant";
  // Reading (saving articles) waits for the browser extension to be published
  var READING_ON = false;
  var USES = READING_ON ? "Other questions and article summaries use one each." : "Other questions use one each.";
  // each one tried against the live data: the stock three are answered by the
  // US stock box, the economy three by the data series
  var EXAMPLES = [
    ["Stocks", ["Who owns Nvidia?",
                "How has Apple's revenue grown over the last five years?",
                "Which stocks contributed most to the S&P 500 this year?"]],
    ["Economy", ["How far apart are the US and Japanese 10-year yields since 2020?",
                 "Is Japan's core inflation above or below America's?",
                 "When was the JGB 10-year yield last above 3%?"]],
  ];
  var state = { hasKey: false, keyForSummaries: false, free: { on: false, left: 0, limit: 0 }, covered: {} };
  function triesText(n) { return n + (n === 1 ? " free try" : " free tries"); }

  function $(s, r) { return (r || document).querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function api(method, path, body, extra) {
    var opts = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    if (extra) Object.keys(extra).forEach(function (k) { opts[k] = extra[k]; });
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        return { status: r.status, ok: r.ok, body: b };
      });
    });
  }
  function detail(r, fallback) {
    var d = r && r.body && r.body.detail;
    if (typeof d === "string" && d) return d;
    return fallback + (r && r.status ? " (" + r.status + ")" : "") + ".";
  }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function day(v) {
    if (!v) return "";
    var d = typeof v === "number" ? new Date(v * 1000) : new Date(String(v).slice(0, 10) + "T00:00:00");
    if (isNaN(d.getTime())) return "";
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }
  function settingsLink(text) {
    return /Settings|AI key|model key/i.test(text || "") ? ' <a href="#key">Add your key in AI Key</a>.' : "";
  }

  /* ---------------------------------------------------------- toast + undo */
  var toastTimer = null, pending = null;
  function flushPending() {
    if (pending) { var p = pending; pending = null; p.commit(); }
  }
  function toast(text, undo, commit) {
    flushPending();
    var box = $("#my-toast");
    clearTimeout(toastTimer);
    box.innerHTML = "<span>" + esc(text) + "</span>" +
      (undo ? '<button type="button" class="my-undo">Undo</button>' : "") +
      '<button type="button" class="my-x" aria-label="Close this message">×</button>';
    box.hidden = false;
    if (commit) pending = { commit: commit };
    var done = function () { box.hidden = true; flushPending(); };
    if (undo) $(".my-undo", box).onclick = function () {
      clearTimeout(toastTimer); pending = null; box.hidden = true; undo();
    };
    $(".my-x", box).onclick = function () { clearTimeout(toastTimer); done(); };
    toastTimer = setTimeout(done, 7000);
  }
  window.addEventListener("pagehide", flushPending);

  /* ---------------------------------------------------------------- charts
     The Assistant's chart spec (app/assistant/charts.py) into obsChart's
     config; a copy of assistant.js chartCfg, display scaling only. */
  function yenScale(spec) {
    if (!/¥/.test(spec.unit || "") || /per|\//.test(spec.unit)) return null;
    var max = 0;
    spec.series.forEach(function (s) { s.points.forEach(function (p) { if (p[1] != null) max = Math.max(max, Math.abs(p[1])); }); });
    if (max >= 1e12) return { div: 1e12, label: "¥tn" };
    if (max >= 1e9) return { div: 1e9, label: "¥bn" };
    if (max >= 1e6) return { div: 1e6, label: "¥mn" };
    return null;
  }
  function xLabel(spec, x) {
    if (spec.fiscal && typeof fmtFiscalYear === "function") return fmtFiscalYear(x);
    if (/^\d{4}-\d{2}-\d{2}/.test(x)) return spec.daily ? x.slice(0, 10) : fmtPeriod(x);
    return x;
  }
  function sourceLine(spec) {
    var credits = [], vint = [];
    spec.sources.forEach(function (s) {
      if (s.credit && credits.indexOf(s.credit) < 0) credits.push(s.credit);
      if (s.vintage && vint.indexOf(s.vintage) < 0) vint.push(s.vintage);
    });
    return credits.concat(vint).join(" · ") + (credits.length ? " · " : "") + "Plover Analytics";
  }
  function chartCfg(spec) {
    var sc = yenScale(spec), k = sc ? sc.div : 1, vals = [];
    var series = spec.series.map(function (s, i) {
      return { name: s.name, slot: i + 1, points: s.points.map(function (p) {
        var v = p[1] == null ? null : p[1] / k; if (v != null) vals.push(v); return [p[0], v]; }) };
    });
    var dp = sc || spec.unit === "%" || spec.unit === "index" ? 1 : (vals.every(function (v) { return Number.isInteger(v); }) ? 0 : 2);
    var unitLabel = sc ? sc.label : spec.unit;
    var official = spec.series.every(function (s) { return s.trust === "official"; });
    var cfg = { series: series, dp: dp, trust: official ? "official" : null,
      unit: spec.unit === "%" ? "%" : "x",
      unitSuffix: spec.unit === "%" || spec.unit === "index" ? "" : unitLabel,
      yAxisName: spec.unit === "index" ? "Index" : unitLabel,
      yAxisDp: dp, isoPeriods: spec.daily, sourceLine: sourceLine(spec),
      legendBottomNarrow: 34 + 20 * (spec.series.some(function (s) { return s.name.length > 18; })
        ? spec.series.length : Math.ceil(spec.series.length / 2)) };
    if (spec.kind === "bar") {
      var xs = [];
      spec.series.forEach(function (s) { s.points.forEach(function (p) { if (xs.indexOf(p[0]) < 0) xs.push(p[0]); }); });
      xs.sort();
      cfg.categories = xs.map(function (x) { return xLabel(spec, x); });
      cfg.series = series.map(function (s) {
        var m = {}; s.points.forEach(function (p) { m[p[0]] = p[1]; });
        return { name: s.name, slot: s.slot, points: xs.map(function (x) { return m[x] === undefined ? null : m[x]; }) };
      });
      return { kind: "cols", cfg: cfg };
    }
    if (spec.axis === "category") {
      cfg.xType = "category";
      cfg.series = series.map(function (s) {
        return { name: s.name, slot: s.slot, points: s.points.map(function (p) { return [xLabel(spec, p[0]), p[1]]; }) };
      });
    }
    return { kind: "line", cfg: cfg };
  }
  var drawn = [];     // every chart in the conversation, redrawn on a theme change
  function drawChart(el, spec) {
    if (!el || !spec || typeof obsChart !== "function") return;
    var o = chartCfg(spec);
    drawn.push({ el: el, spec: spec, chart: obsChart(el, o.kind, o.cfg) });
  }
  function redrawCharts() {
    drawn.forEach(function (d) {
      try { d.chart && d.chart.dispose && d.chart.dispose(); } catch (e) { /* gone */ }
      if (d.make) { d.chart = d.make(); return; }
      var o = chartCfg(d.spec); d.chart = obsChart(d.el, o.kind, o.cfg);
    });
  }
  function clearCharts() {
    drawn.forEach(function (d) { try { d.chart && d.chart.dispose && d.chart.dispose(); } catch (e) { /* gone */ } });
    drawn = [];
  }

  /* ------------------------------------------------------------- answers */
  var STAT = { latest: "Latest", min: "Lowest", max: "Highest", mean: "Average", median: "Median",
    count: "Count", highest_since: "Highest since", lowest_since: "Lowest since" };
  function figure(v) {
    if (v == null) return "—";
    if (typeof v === "number") return fmtNum(v, Math.min(3, ((String(v).split(".")[1]) || "").length));
    return esc(v);
  }
  function figuresHtml(summary, sources) {
    if (!summary) return "";
    var titles = {};
    (sources || []).forEach(function (s) { titles[s.name] = s.title || s.series; });
    var cells = [];
    Object.keys(summary).forEach(function (col) {
      Object.keys(summary[col] || {}).forEach(function (k) {
        var v = summary[col][k], big, sub = "";
        if (v && typeof v === "object" && "since" in v) {
          big = v.since ? esc(day(v.since) || v.since) : "Never";
          sub = v.since ? "last at " + figure(v.that_value) + ", now " + figure(v.latest) : esc(v.note || "");
        } else if (v && typeof v === "object") { big = figure(v.value); sub = esc(day(v.at) || v.at || ""); }
        else big = figure(v);
        cells.push('<div class="my-fig"><dt>' + esc(titles[col] || col) + " · " + esc(STAT[k] || k) +
          "</dt><dd>" + big + (sub ? "<small>" + sub + "</small>" : "") + "</dd></div>");
      });
    });
    return cells.length ? '<dl class="my-figs">' + cells.join("") + "</dl>" : "";
  }
  /* The Analyst's note, as a chat shows it: paragraphs, bullet lists,
     simple tables, **bold** and `code`. Everything is escaped first. */
  function inline(t) {
    return esc(t).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  }
  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return c.trim(); });
  }
  function numeric(t) { return /^[−+-]?[\d.,]+\s*(%|pp|bp)?$/.test(t); }
  function noteHtml(text) {
    var out = [], lines = String(text || "").replace(/\r/g, "").split("\n"), i = 0, para = [];
    function flush() { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*\|.*\|\s*$/.test(line)) {
        flush();
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        var head = cells(rows[0]), body = rows.slice(1).filter(function (r) { return !/^\s*\|?[\s:|-]+\|?\s*$/.test(r); });
        // a column is numeric when every filled cell in it is a number
        var num = head.map(function (h, k) {
          var vals = body.map(function (r) { return cells(r)[k] || ""; }).filter(Boolean);
          return k > 0 && vals.length > 0 && vals.every(numeric);
        });
        var cls = function (k) { return num[k] ? ' class="num"' : ""; };
        out.push('<div class="my-tbl"><table><thead><tr>' + head.map(function (h, k) { return "<th" + cls(k) + ">" + inline(h) + "</th>"; }).join("") +
          "</tr></thead><tbody>" + body.map(function (r) {
            return "<tr>" + cells(r).map(function (c, k) { return "<td" + cls(k) + ">" + inline(c) + "</td>"; }).join("") + "</tr>";
          }).join("") + "</tbody></table></div>");
        continue;
      }
      if (/^\s*[-*•]\s+/.test(line)) {
        flush();
        var items = [];
        while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*•]\s+/, "")); i++; }
        out.push("<ul>" + items.map(function (t) { return "<li>" + inline(t) + "</li>"; }).join("") + "</ul>");
        continue;
      }
      if (!line.trim()) flush(); else para.push(line.trim().replace(/^#+\s*/, ""));
      i++;
    }
    flush();
    return out.join("");
  }
  function sourcesHtml(sources) {
    var out = [];
    (sources || []).forEach(function (s) {
      var c = String(s.credit || "").replace(/^\s*Source:\s*/, "").replace(/\.$/, "");
      var rel = String(s.release || "");
      rel = rel ? rel.charAt(0).toLowerCase() + rel.slice(1) : "";
      var bit = [c, rel].filter(Boolean).join(", ");
      if (bit && out.indexOf(bit) < 0) out.push(bit);
    });
    return out.length ? '<p class="my-src">Source: ' + out.map(esc).join("; ") + ". Values as published.</p>" : "";
  }

  /* ---------------------------------------------------------------- chat
     A chat is one study (/studies): each message asks it again, so a
     follow-up builds on the answer before it, and every answer is kept as a
     run. Opening a chat replays its runs in order. */
  var chat = { study: null, busy: false };

  function meHtml(text) {
    return '<div class="my-msg my-me"><p>' + esc(text).replace(/\n/g, "<br>") + "</p></div>";
  }
  function ploverHead(when) {
    return '<header class="my-plover-head"><span class="my-who">Plover</span>' +
      (when ? '<span class="my-meta">' + esc(when) + "</span>" : "") + "</header>";
  }
  /* --------------------------------------------------------- stock answers
     A stock question is answered by the US stock box (app/stock_recipes,
     the front page's engine); its answer is drawn here the way the front
     page draws it (us-stocks.js), cut to fit a chat: the first rows of the
     table, and the full answer one click away. */
  var MISSING = "—", MINUS = "\u2212";
  function money(v, dp) {
    if (v == null) return MISSING;
    var a = Math.abs(v), t;
    if (a >= 1e12) t = fmtNum(a / 1e12, 2) + "tn";
    else if (a >= 1e11) t = fmtNum(a / 1e9, 0) + "bn";
    else if (a >= 1e9) t = fmtNum(a / 1e9, 1) + "bn";
    else if (a >= 1e6) t = fmtNum(a / 1e6, 1) + "mn";
    else if (a >= 1e3) t = fmtNum(a, 0);
    else t = fmtNum(a, dp == null ? 2 : dp);
    return (v < 0 ? MINUS : "") + "$" + t;
  }
  function pct(v, dp, signed) {
    if (v == null) return MISSING;
    var t = fmtNum(Math.abs(v), dp == null ? 1 : dp) + "%";
    return v < 0 ? MINUS + t : (signed && v > 0 ? "+" : "") + t;
  }
  function mult(v) { return v == null ? MISSING : fmtNum(v, 1) + "×"; }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function fmtBy(unit, v, dp) {
    if (v == null) return MISSING;
    if (unit === "USD") return money(v, dp);
    if (unit === "%") return pct(v, dp == null ? 1 : dp, false);
    if (unit === "×") return mult(v);
    if (unit === "count" || unit === "shares") return count(v);
    if (unit === "date") return esc(day(v) || v);
    if (typeof v === "number") return fmtNum(v, dp == null ? 2 : dp);
    return esc(v);
  }
  // a table cell takes its unit from the column's words, as on the front page
  function cellFor(col, v, unit) {
    if (v == null || v === "") return '<td class="num">' + MISSING + "</td>";
    if (typeof v === "boolean") return "<td>" + (v ? "Yes" : "No") + "</td>";
    if (typeof v === "number") {
      var c = col.toLowerCase(), out;
      if (/\(usd\)/.test(c)) out = money(v);
      // a change in a share count is a signed count, not a percentage
      else if (/change/.test(c) && /shares/.test(c)) out = (v > 0 ? "+" : v < 0 ? MINUS : "") + count(Math.abs(v));
      else if (/change/.test(c) && !/close|price/.test(c)) out = pct(v, 1, true);
      else if (/\(\u00d7\)/.test(c)) out = mult(v);
      else if (/\(pp\)/.test(c)) out = (v > 0 ? "+" : v < 0 ? MINUS : "") + fmtNum(Math.abs(v), 2) + " pp";
      else if (/share of|%|pct|margin|yield|roe|growth|ratio/.test(c) && !/shares|rank/.test(c)) out = pct(v, 1, false);
      else if (/p\/e/.test(c)) out = mult(v);
      else if (/rank|holders|count|trades|positions|days|shares|volume/.test(c)) out = count(v);
      else if (/price|close/.test(c)) out = money(v, 2);
      else if (/value|revenue|income|assets|equity|cap|buyback|dividend|net|cash|fcf|total|prior/.test(c) || unit === "USD") out = money(v);
      else out = fmtNum(v, Math.abs(v) < 100 ? 2 : 0);
      return '<td class="num">' + out + "</td>";
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return "<td>" + esc(day(v)) + "</td>";
    var t = String(v);
    // a ticker: the company's logo, and its record
    if (/ticker/i.test(col) && /^[A-Z][A-Z0-9.-]{0,9}$/.test(t)) {
      return "<td>" + (window.usLogos ? usLogos.img(t, 16) : "") +
        '<a href="us-company.html?t=' + encodeURIComponent(t) + '">' + esc(t) + "</a></td>";
    }
    return "<td>" + esc(t.length > 60 ? t.slice(0, 57) + "…" : t) + "</td>";
  }
  // Columns that are bookkeeping, not reading: the filing's tag, its
  // accession number, a CUSIP or an internal key. They stay in the full
  // answer and its download; the chat shows what a reader reads.
  var TECH_COLS = /^(tag|xbrl tag|concept|accession|adsh|cusip|cik)$/i;
  function technical(v) {
    var x = String(v == null ? "" : v);
    return !x || /^[A-Z][a-z]+(?:[A-Z][a-z0-9]*){2,}$/.test(x) || /^\d{10}-\d{2}-\d{6}$/.test(x) ||
      /^cik:/i.test(x) || /^[0-9A-Z]{9}$/.test(x) && /\d/.test(x) && /[A-Z]/.test(x);
  }
  function stockTable(t) {
    if (!t || !t.rows || !t.rows.length) return "";
    var keep = t.columns.map(function (c, i) {
      if (TECH_COLS.test(String(c).trim())) return false;
      var vals = t.rows.map(function (r) { return r[i]; }).filter(function (v) { return v != null && v !== ""; });
      return !(vals.length && vals.every(function (v) { return typeof v === "string" && technical(v); }));
    });
    var cols = t.columns.filter(function (c, i) { return keep[i]; });
    // every row: the answer in the chat is the whole answer, with nowhere else to see the rest
    var all = t.rows.map(function (r) { return r.filter(function (v, i) { return keep[i]; }); });
    var rows = all;
    var num = cols.map(function (c, i) { return all.some(function (r) { return typeof r[i] === "number"; }); });
    return '<div class="my-tbl"><table data-no-enhance><thead><tr>' + cols.map(function (c, i) {
      return "<th" + (num[i] ? ' class="num"' : "") + ">" + esc(c) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) { return "<tr>" + r.map(function (v, i) { return cellFor(cols[i], v, t.unit); }).join("") + "</tr>"; }).join("") +
      "</tbody></table></div>";
  }
  function stockFigs(figs) {
    var cells = (figs || []).filter(function (f) { return f && f.value !== undefined; }).map(function (f) {
      return '<div class="my-fig"' + (f.formula ? ' title="' + esc(f.formula) + '"' : "") + "><dt>" + esc(f.label) + "</dt><dd>" +
        fmtBy(f.unit, f.value) + (f.as_of && f.unit !== "date" ? "<small>as of " + esc(day(f.as_of)) + "</small>" : "") + "</dd></div>";
    });
    return cells.length ? '<dl class="my-figs">' + cells.join("") + "</dl>" : "";
  }
  function stockCalc(a) {
    var calc = a.calc || {};
    var items = Object.keys(calc).filter(function (k) { return k !== "query" && k !== "steps"; }).map(function (k) {
      return "<li><b>" + esc(k.replace(/_/g, " ")) + ":</b> " + esc(calc[k]) + "</li>"; });
    var srcs = (a.sources || []).map(function (x) { return "<li><b>" + esc(x.name) + ".</b> " + esc(x.credit) + (x.note ? " " + esc(x.note) : "") + "</li>"; });
    return '<details class="my-calc"><summary>How this was worked out</summary>' +
      (items.length ? "<ul>" + items.join("") + "</ul>" : "") + (srcs.length ? "<ul>" + srcs.join("") + "</ul>" : "") + "</details>";
  }
  function stockHtml(run) {
    var a = run.stock, co = a.entities && a.entities.company;
    var src = (a.sources || []).map(function (x) { return x.credit; }).join(" ");
    var page = a.links && a.links.company ? a.links.company : (co && co.ticker ? "/us-stock.html?t=" + encodeURIComponent(co.ticker) : null);
    return '<article class="my-msg my-plover">' + ploverHead(day(run.created_at)) +
      '<p class="my-head-line">' + esc(a.headline) + "</p>" +
      (a.period && a.period.text ? '<p class="my-src">' + esc(a.period.text) + "</p>" : "") +
      (a.answer ? '<div class="my-note-text"><p>' + esc(a.answer) + "</p></div>" : "") +
      (a.quotes || []).map(function (qt) {
        return '<blockquote class="my-quote">' + (qt.text || []).map(function (p) { return "<p>" + esc(p) + "</p>"; }).join("") +
          (qt.cite ? '<p class="my-src">' + (qt.url ? '<a href="' + esc(qt.url) + '" rel="noopener">' + esc(qt.cite) + "</a>" : esc(qt.cite)) + "</p>" : "") + "</blockquote>";
      }).join("") +
      stockFigs(a.figures) +
      (a.chart ? '<figure class="my-chart"><p class="my-label">' + esc(a.chart.title || "Chart") + '</p><div class="my-plot"></div></figure>' : "") +
      stockTable(a.table) +
      (a.notes && a.notes.length ? '<p class="my-src">' + a.notes.map(esc).join(" ") + "</p>" : "") +
      (src ? '<p class="my-src">' + esc(src) + "</p>" : "") +
      stockCalc(a) +
      (page ? '<p class="my-acts"><a class="btn" href="' + esc(page) + '">Open ' + esc(co ? co.name : "Company") + "</a></p>" : "") +
      "</article>";
  }
  function drawStockChart(el, a) {
    var chart = a.chart;
    if (!el || !chart || !chart.series || !chart.series.length || typeof obsChart !== "function") return;
    var has = chart.series.some(function (x) { return x.points && x.points.some(function (p) { return p[1] != null; }); });
    if (!has) { el.parentElement.remove(); return; }
    var src = (a.sources || []).map(function (x) { return x.credit; }).join(" ");
    var unit = chart.unit || "", isUsd = /USD/.test(unit), make;
    if (chart.kind === "bars") {
      var pts = chart.series[0].points, scale = 1, suffix = unit;
      if (isUsd) {
        var mx = Math.max.apply(null, pts.map(function (p) { return Math.abs(p[1] || 0); }));
        scale = mx >= 1e9 ? 1e9 : mx >= 1e6 ? 1e6 : 1;
        suffix = scale === 1e9 ? "$bn" : scale === 1e6 ? "$mn" : "$";
      }
      var items = pts.map(function (p) { return { name: String(p[0]), value: p[1] == null ? null : p[1] / scale }; });
      var big = Math.max.apply(null, items.map(function (i) { return Math.abs(i.value || 0); })) >= 100;
      make = function () {
        return obsChart(el, "rank", { items: items, unit: isUsd ? suffix.replace("$", "") + " USD" : suffix, dp: unit === "pp" ? 2 : big ? 0 : 1,
          trust: "derived", sourceLine: src, rows: items, columns: [{ key: "name", label: "name" }, { key: "value", label: "value (" + suffix + ")" }] });
      };
    } else {
      // a rate and a dollar amount never share an axis: only the left-hand series is drawn here
      var left = chart.series.filter(function (x) { return x.axis !== "right"; });
      var usdLine = isUsd && left.length === chart.series.length;
      var cfg = { unit: "", unitSuffix: usdLine ? " $" : (unit === "%" ? "%" : ""), dp: 2, trust: "official", sourceLine: src,
        yAxisName: usdLine ? "USD" : (left.length < chart.series.length ? left[0].name : unit), isoPeriods: true,
        series: left.map(function (x) { return { name: x.name, points: x.points.map(function (p) { return [p[0], p[1]]; }) }; }) };
      if (left[0].points.length && /^\d{4}$/.test(String(left[0].points[0][0]))) cfg.xType = "category";
      make = function () { return obsChart(el, "line", cfg); };
    }
    drawn.push({ el: el, make: make, chart: make() });
  }

  function answerHtml(st, run) {
    if (run.stock) return stockHtml(run);
    return '<article class="my-msg my-plover">' + ploverHead(day(run.created_at)) +
      (run.note ? '<div class="my-note-text">' + noteHtml(run.note) + "</div>" : "") +
      figuresHtml(run.summary, run.sources) +
      (run.chart ? '<figure class="my-chart"><div class="my-plot" data-run="' + run.id + '"></div></figure>' : "") +
      (run.chart_error ? '<p class="my-err">' + esc(run.chart_error) + "</p>" : "") +
      sourcesHtml(run.sources) +
      '<p class="my-acts"><a class="btn" href="' + A + "/studies/" + st.id + "/runs/" + run.id + '/csv">Download Data</a></p>' +
      "</article>";
  }
  function addAnswer(st, run, into) {
    var wrap = document.createElement("div");
    wrap.innerHTML = answerHtml(st, run);
    var el = wrap.firstChild;
    if (into) into.replaceWith(el); else $("#my-thread").appendChild(el);
    if (run.stock) drawStockChart($(".my-plot", el), run.stock);
    else if (run.chart) drawChart($(".my-plot", el), run.chart);
    return el;
  }
  function addHtml(html) {
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    var el = wrap.firstChild;
    $("#my-thread").appendChild(el);
    return el;
  }

  function showStart(on) {
    $("#my-hello").hidden = !on;
    $("#my-tries").hidden = !on;
    $("#panel-chat").classList.toggle("is-empty", on);
    $("#my-q").placeholder = on ? "Ask a question about the data" : "Ask a follow-up";
    // two lines for a first question, one in a chat so it hides less of the answer
    $("#my-q").rows = on ? 2 : 1;
    grow();
  }
  function headHtml(st) {
    return '<div class="my-convo-head"><p class="my-convo-title">' + esc(st.title || st.question) + "</p>" +
      '<button type="button" class="my-del" id="my-del-chat">Delete Chat</button></div>';
  }
  function wireHead(st) {
    var b = $("#my-del-chat"); if (b) b.onclick = function () { deleteChat(st); };
  }

  function newChat(focus) {
    chat.study = null;
    clearCharts();
    $("#my-thread").innerHTML = "";
    showStart(true);
    markChat(null);
    if (focus) $("#my-q").focus();
  }

  function openChat(id) {
    if (chat.study && chat.study.id === id && $("#my-thread").children.length) return;
    clearCharts();
    showStart(false);
    $("#my-thread").innerHTML = '<p class="my-wait">Opening the chat…</p>';
    markChat(id);
    api("GET", A + "/studies/" + id).then(function (v) {
      if (!v.ok) {
        $("#my-thread").innerHTML = '<p class="my-err">' + esc(detail(v, "That chat could not be opened")) +
          ' <a href="#chat">Start a new chat</a>.</p>';
        return;
      }
      var st = v.body.study;
      chat.study = st;
      var runs = (st.runs || []).filter(function (r) { return r.question; }).reverse();
      $("#my-thread").innerHTML = headHtml(st);
      wireHead(st);
      if (!runs.length) { addHtml('<p class="my-wait">This chat has no answer yet. Ask below.</p>'); return; }
      // the newest answer came with the study; the older ones are fetched in order
      Promise.all(runs.map(function (r) {
        return r.id === (v.body.run || {}).id ? Promise.resolve(v.body)
          : api("GET", A + "/studies/" + id + "?run=" + r.id).then(function (x) { return x.ok ? x.body : null; });
      })).then(function (views) {
        if (!chat.study || chat.study.id !== id) return;
        views.forEach(function (view, i) {
          addHtml(meHtml(runs[i].question));
          if (view && view.run) addAnswer(st, view.run);
          else addHtml('<p class="my-err">This answer could not be loaded.</p>');
        });
        window.scrollTo(0, document.body.scrollHeight);
      });
    });
  }

  function setAsking(on) {
    chat.busy = on;
    $("#my-go").disabled = on;
    $("#my-go").textContent = on ? "Working…" : "Send";
  }

  function send(question) {
    var q = String(question || "").trim();
    if (chat.busy) return;
    if (!q) { $("#my-q").focus(); return; }
    showStart(false);
    // no key and no free tries: still sent, since a stock question needs
    // neither; the server says what to do for any other question
    $("#my-q").value = ""; grow();
    addHtml(meHtml(q));
    var wait = addHtml('<article class="my-msg my-plover">' + ploverHead() +
      '<p class="my-wait">Working out the answer from the data… <span class="my-secs"></span></p></article>');
    var began = Date.now(), tick = setInterval(function () {
      var s = Math.round((Date.now() - began) / 1000);
      $(".my-secs", wait).textContent = s >= 5 ? s + " s. This can take up to a minute." : "";
    }, 1000);
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    setAsking(true);
    var made = null;
    var start = chat.study ? Promise.resolve({ ok: true, body: { study: chat.study } })
      : api("POST", A + "/studies", { question: q });
    start.then(function (r) {
      if (!r.ok) throw r;
      if (!chat.study) made = r.body.study;
      return api("POST", A + "/studies/" + r.body.study.id + "/ask", { question: q, stocks: true });
    }).then(function (r) {
      if (!r.ok) throw r;
      var st = r.body.study;
      if (made) {
        chat.study = st;
        $("#my-thread").insertAdjacentHTML("afterbegin", headHtml(st));
        wireHead(st);
        history.replaceState(null, "", "#chat/" + st.id);
        markChat(st.id);
      }
      addAnswer(st, r.body.run, wait);
      loadChats();
      if (!state.hasKey) loadKey();
    }).catch(function (r) {
      // nothing half-made left behind: a new chat whose first question failed is put away
      if (made) { api("DELETE", A + "/studies/" + made.id); }
      var why = r && r.status ? detail(r, "Plover could not answer that") : "Plover could not be reached. Check your connection.";
      wait.outerHTML = '<article class="my-msg my-plover my-fail">' + ploverHead() + '<p class="my-err">' + esc(why) + settingsLink(why) + "</p>" +
        '<p class="my-acts"><button type="button" class="btn my-again">Try Again</button></p></article>';
      var fails = document.querySelectorAll(".my-fail .my-again"), again = fails[fails.length - 1];
      again.onclick = function () {
        var box = again.closest(".my-fail"), me = box.previousElementSibling;
        box.remove(); if (me && me.classList.contains("my-me")) me.remove();
        send(q);
      };
    }).then(function () { clearInterval(tick); setAsking(false); });
  }

  function deleteChat(st) {
    api("DELETE", A + "/studies/" + st.id).then(function (r) {
      if (!r.ok) { toast(detail(r, "That chat could not be deleted")); return; }
      if (chat.study && chat.study.id === st.id) { history.replaceState(null, "", "#chat"); newChat(); }
      loadChats();
      toast("Chat deleted.", function () {
        api("POST", A + "/studies/" + st.id + "/restore").then(function () { loadChats(); });
      });
    });
  }

  function markChat(id) {
    Array.prototype.forEach.call(document.querySelectorAll(".my-chat-row"), function (li) {
      li.classList.toggle("is-on", Number(li.getAttribute("data-id")) === id);
    });
  }

  function loadChats() {
    return api("GET", A + "/studies").then(function (r) {
      var list = $("#my-chats");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your chats could not be loaded")) + "</li>"; return; }
      var items = r.body.studies || [];
      // the Overview offers the three latest chats to carry on
      $("#ov-cont").hidden = !items.length;
      $("#ov-cont").innerHTML = items.length ? "Continue: " + items.slice(0, 3).map(function (x) {
        return '<a href="#chat/' + x.id + '">' + esc(x.title || x.question) + "</a>";
      }).join("") : "";
      if (!items.length) { list.innerHTML = '<li class="my-empty">No chats yet.</li>'; return; }
      list.innerHTML = items.map(function (s) {
        return '<li class="my-chat-row" data-id="' + s.id + '"><a href="#chat/' + s.id + '">' +
          '<span class="my-chat-name">' + esc(s.title || s.question) + "</span>" +
          '<span class="my-meta">' + esc(day(s.last_run_at || s.updated_at || s.created_at)) + "</span></a>" +
          '<button type="button" class="my-x-del" data-id="' + s.id + '" aria-label="Delete this chat" title="Delete this chat">Delete</button></li>';
      }).join("");
      Array.prototype.forEach.call(list.querySelectorAll(".my-x-del"), function (b) {
        var s = items.filter(function (x) { return String(x.id) === b.getAttribute("data-id"); })[0];
        b.onclick = function () { deleteChat(s); };
      });
      markChat(chat.study ? chat.study.id : null);
    });
  }

  function grow() {
    var t = $("#my-q");
    t.style.height = "auto";
    if (t.value) t.style.height = Math.min(t.scrollHeight, 200) + "px";   // empty: the rows decide
  }

  /* ----------------------------------------------------------- companies */
  function isUS(code) { return /^cik:/.test(code); }
  function nextResults(code, cell) {
    fetch("/api/v1/equity/calendar/company/" + encodeURIComponent(code), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) {
        var cur = b && b.current && b.current[0];
        if (!cur) { cell.textContent = "No results date listed"; return; }
        cell.textContent = cur.announce_date ? "Results " + day(cur.announce_date) : "Results date not announced";
      })
      .catch(function () { cell.textContent = "—"; });
  }
  /* A US company: its ticker, its page and its next filing, which the SEC
     calendar estimates from the same filing a year earlier. */
  function usDetails(code, row) {
    var cell = $(".my-when", row), meta = $(".my-meta", row), link = $(".my-open", row);
    fetch("/api/v1/us/earnings/company/" + encodeURIComponent(code), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) {
        if (!b) { cell.textContent = "—"; return; }
        if (b.ticker) {
          meta.textContent = b.ticker + (b.exchange ? " · " + b.exchange : "");
          link.setAttribute("href", "us-company.html?t=" + encodeURIComponent(b.ticker));
        }
        // the name every US page shows, in place of the SEC's capitals
        fetch("/api/v1/us/company/" + encodeURIComponent(code.slice(4)), { headers: { Accept: "application/json" } })
          .then(function (r) { return r.ok ? r.json() : null; }).then(function (rec) {
            var n = rec && rec.company && rec.company.name, el = $(".my-name", row);
            if (!n || !el) return;
            var img = el.querySelector("img");
            el.textContent = n;
            if (img) el.insertBefore(img, el.firstChild);
          }).catch(function () { /* the filed name stays */ });
        var next = (b.upcoming || []).filter(function (u) { return u.status === "expected" && u.expected_date; })[0];
        if (!next) { cell.textContent = "No filing date expected"; return; }
        var what = /annual/i.test(next.report || "") ? "Annual report" : "Quarterly report";
        cell.textContent = what + " due about " + day(next.expected_date);
        cell.title = "Estimated from the same filing a year earlier";
      })
      .catch(function () { cell.textContent = "—"; });
  }

  /* ------------------------------------------------------------ overview */
  // The watchlist (the companies on the reader's list) with each US company's
  // latest close, its change over a day, the year to date and a year, and its
  // last 60 closes; what changed at those companies (insider purchases and
  // sales, results filed) in the last 90 days; and the results coming up in
  // the next six weeks. Japanese companies show their announced results date;
  // their prices need a Japanese price source, so they show —.
  var ovFeed = [], ovSoon = [], ovSeq = 0, ovClose = null;
  function jget(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  function iso(d) { return d.toISOString().slice(0, 10); }
  function upDown(v) {
    if (v == null) return MISSING;
    return '<span class="' + (v >= 0 ? "ov-up" : "ov-down") + '">' + (v >= 0 ? "▲ +" : "▼ " + MINUS) + fmtNum(Math.abs(v), 1) + "%</span>";
  }
  function sparkSvg(vals) {
    if (vals.length < 2) return "";
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), span = hi - lo || 1;
    var d = vals.map(function (v, i) { return (i ? "L" : "M") + (2 + i * 84 / (vals.length - 1)).toFixed(1) + " " + (2 + 20 * (1 - (v - lo) / span)).toFixed(1); }).join(" ");
    return '<svg class="' + (vals[vals.length - 1] >= vals[0] ? "ov-up" : "ov-down") + '" width="88" height="24" viewBox="0 0 88 24" aria-hidden="true">' +
      '<path d="' + d + '" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
  }
  // Form 4 files the surname first: "Teter Timothy S." → "Timothy S. Teter"
  function personName(n) {
    var w = String(n || "").split(/\s+/).filter(Boolean);
    if (String(n) === String(n).toUpperCase()) w = w.map(function (x) { return x.charAt(0) + x.slice(1).toLowerCase(); });
    return w.slice(1).concat(w.slice(0, 1)).join(" ");
  }
  function moneyShort(v) { return v >= 1e6 ? "$" + fmtNum(v / 1e6, 1) + "mn" : "$" + fmtNum(Math.round(v / 1e3), 0) + "k"; }
  function renderOvLists() {
    var cut = iso(new Date(Date.now() - 90 * 864e5)), soonCut = iso(new Date(Date.now() + 42 * 864e5)), today = iso(new Date());
    var feed = ovFeed.filter(function (x) { return x.date >= cut; }).sort(function (a, b) { return a.date < b.date ? 1 : -1; }).slice(0, 8);
    $("#ov-feed").innerHTML = feed.length ? feed.map(function (x) {
      return '<li><span class="ov-tk">' + esc(x.tk) + '</span><span><span class="ov-txt">' + esc(x.text) + '</span><span class="ov-sub">' +
        esc(x.kind) + " · " + esc(x.when) + (x.url ? ' · <a href="' + esc(x.url) + '" rel="noopener">Source filing</a>' : "") + "</span></span></li>";
    }).join("") : '<li class="my-empty">Nothing filed by your companies in the last 90 days.</li>';
    var soon = ovSoon.filter(function (x) { return x.date >= today && x.date <= soonCut; }).sort(function (a, b) { return a.date < b.date ? -1 : 1; });
    $("#ov-soon").innerHTML = soon.length ? soon.map(function (x) {
      return '<li><span class="ov-tk">' + esc(day(x.date)) + '</span><span><span class="ov-txt">' + esc((x.co && x.co.shown) || x.name) + '</span><span class="ov-sub">' +
        esc(x.what) + "</span></span></li>";
    }).join("") : '<li class="my-empty">No results due from your companies in the next six weeks.</li>';
  }
  function usOverview(c, tr, mine) {
    var code = c.sec_code;
    // the name every US page shows ("NVIDIA Corporation", not the SEC's "NVIDIA CORP")
    jget("/api/v1/us/company/" + encodeURIComponent(code.slice(4))).then(function (rec) {
      var n = rec && rec.company && rec.company.name, cell = $(".ov-name", tr);
      if (mine !== ovSeq || !n || !cell) return;
      var img = cell.querySelector("img");
      cell.textContent = n;
      if (img) cell.insertBefore(img, cell.firstChild);
      c.shown = n;
      renderOvLists();
    });
    jget("/api/v1/us/earnings/company/" + encodeURIComponent(code)).then(function (e) {
      if (mine !== ovSeq || !e) { if (mine === ovSeq) $(".ov-next", tr).textContent = MISSING; return; }
      var tk = e.ticker || code.slice(4);
      $(".ov-meta", tr).textContent = tk;
      tr.dataset.tk = tk; tr.dataset.name = c.shown || c.name || tk;     // the Compare button reads these
      var next = (e.upcoming || []).filter(function (u) { return u.status === "expected" && u.expected_date; })[0];
      if (next) {
        $(".ov-next", tr).innerHTML = esc(day(next.expected_date)) + ' <span class="ov-faint">about</span>';
        ovSoon.push({ date: next.expected_date, co: c, name: c.name || tk, what: (/annual/i.test(next.report) ? "Annual report" : "Quarterly report") + " due about this day, estimated from last year" });
      } else $(".ov-next", tr).textContent = MISSING;
      var f = (e.filings || [])[0];
      if (f) ovFeed.push({ date: f.filed, tk: tk, kind: "Results filed", when: f.form + " filed " + day(f.filed), url: f.source_url,
        text: "Filed its " + (f.form === "10-K" ? "annual" : "quarterly") + " report for the period to " + day(f.period) + "." });
      var since = new Date(); since.setUTCFullYear(since.getUTCFullYear() - 1, 0, 1);
      return Promise.all([jget("/api/v1/us/prices/" + encodeURIComponent(tk) + "?start=" + iso(since)),
                          jget("/api/v1/us/insiders/company/" + encodeURIComponent(code) + "?months=3&limit=40")]).then(function (r) {
        if (mine !== ovSeq) return;
        var bars = ((r[0] && r[0].bars) || []).filter(function (b) { return (b.adjusted_close || b.close) != null; });
        var v = function (b) { return b.adjusted_close || b.close; };
        if (bars.length > 1) {
          var last = bars[bars.length - 1], jan = iso(new Date(Date.UTC(new Date().getUTCFullYear(), 0, 1)));
          var yearAgo = iso(new Date(new Date(last.date + "T00:00:00Z").getTime() - 365 * 864e5));
          var before = function (d) { var x = bars.filter(function (b) { return b.date < d; }); return x.length ? x[x.length - 1] : null; };
          var ytd = before(jan), ya = before(yearAgo) || null;
          $(".ov-px", tr).textContent = fmtNum(last.close, 2);
          $(".ov-px", tr).title = "Close, " + day(last.date);
          $(".ov-d1", tr).innerHTML = upDown((v(last) / v(bars[bars.length - 2]) - 1) * 100);
          $(".ov-ytd", tr).innerHTML = ytd ? upDown((v(last) / v(ytd) - 1) * 100) : MISSING;
          $(".ov-y1", tr).innerHTML = ya ? upDown((v(last) / v(ya) - 1) * 100) : MISSING;
          $(".ov-sp", tr).innerHTML = sparkSvg(bars.slice(-60).map(v));
          if (!ovClose || last.date > ovClose) ovClose = last.date;
        } else ["ov-px", "ov-d1", "ov-ytd", "ov-y1"].forEach(function (k) { $("." + k, tr).textContent = MISSING; });
        ((r[1] && r[1].trades) || []).filter(function (t) { return t.trans_code === "P" || t.trans_code === "S"; }).slice(0, 2).forEach(function (t) {
          var acc = String(t.accession || "").replace(/-/g, "");
          ovFeed.push({ date: t.filed, tk: tk, kind: "Insider trade", when: "Form 4 filed " + day(t.filed),
            url: acc ? "https://www.sec.gov/Archives/edgar/data/" + (t.issuer_cik || code.slice(4)) + "/" + acc + "/" : "",
            text: personName(t.owner_name) + (t.title ? " (" + t.title + ")" : "") + (t.trans_code === "P" ? " bought " : " sold ") +
              fmtNum(t.shares, 0) + " shares" + (t.price ? " at $" + fmtNum(t.price, 2) : "") + (t.value ? ", " + moneyShort(t.value) : "") + "." });
        });
        renderOvLists();
        if (ovClose) $("#ov-wl-note").textContent = $("#ov-wl-note").textContent.replace(/ · US closes .*$/, "") + " · US closes " + day(ovClose);
      });
    }).then(function () { if (mine === ovSeq) renderOvLists(); });
  }
  function jpOverview(c, tr, mine) {
    ["ov-px", "ov-d1", "ov-ytd", "ov-y1"].forEach(function (k) {
      var cell = $("." + k, tr); cell.textContent = MISSING; cell.title = "Japanese share prices are not on this page yet";
    });
    $(".ov-sp", tr).textContent = "";
    jget("/api/v1/equity/calendar/company/" + encodeURIComponent(c.sec_code)).then(function (b) {
      if (mine !== ovSeq) return;
      var cur = b && b.current && b.current[0];
      if (cur && cur.announce_date) {
        $(".ov-next", tr).innerHTML = esc(day(cur.announce_date)) + ' <span class="ov-faint">set</span>';
        ovSoon.push({ date: cur.announce_date, name: c.name || c.sec_code, what: "Results announced for this day (JPX list)" });
        renderOvLists();
      } else $(".ov-next", tr).textContent = MISSING;
    });
  }
  function loadOverview(rows) {
    var mine = ++ovSeq;
    ovFeed = []; ovSoon = []; ovClose = null;
    $("#ov-wl-note").textContent = rows.length ? rows.length + (rows.length === 1 ? " company" : " companies") : "";
    if (!rows.length) {
      $("#ov-wl").innerHTML = '<tbody><tr><td class="my-empty">No companies yet. <a href="#companies">Add the companies you follow</a> to see their prices, filings and results here.</td></tr></tbody>';
      renderOvLists();
      return;
    }
    $("#ov-wl").innerHTML = '<thead><tr><th>Company</th><th class="num">Last close, $</th><th class="num">1 day</th><th class="num">Year to date</th>' +
      '<th class="num">1 year</th><th class="num">Last 60 days</th><th>Next results</th></tr></thead><tbody>' +
      rows.map(function (c, i) {
        var us = isUS(c.sec_code);
        return '<tr data-i="' + i + '"><td class="ov-co"><a href="' + (us ? "us-company.html?t=" + encodeURIComponent(c.sec_code.slice(4)) :
          "company.html?code=" + encodeURIComponent(c.sec_code)) + '"><span class="ov-name">' + esc(c.name || c.name_ja || c.sec_code) + '</span>' +
          '<span class="ov-meta">' + (us ? "" : esc(c.sec_code)) + "</span></a></td>" +
          '<td class="num ov-px">…</td><td class="num ov-d1">…</td><td class="num ov-ytd">…</td><td class="num ov-y1">…</td>' +
          '<td class="num ov-sp"></td><td class="ov-next">…</td></tr>';
      }).join("") + "</tbody>";
    if (window.usLogos) usLogos.ready.then(function () {
      rows.forEach(function (c, i) {
        var n = $('#ov-wl tr[data-i="' + i + '"] .ov-name');
        if (n && !n.querySelector("img")) n.insertAdjacentHTML("afterbegin", isUS(c.sec_code) ? usLogos.img(c.sec_code, 20) : usLogos.at(c.logo, 20));
      });
    });
    rows.forEach(function (c, i) {
      var tr = $('#ov-wl tr[data-i="' + i + '"]');
      if (isUS(c.sec_code)) usOverview(c, tr, mine); else jpOverview(c, tr, mine);
    });
    renderOvLists();
  }

  function loadCompanies() {
    return api("GET", A + "/coverage").then(function (r) {
      var list = $("#my-cos");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your companies could not be loaded")) + "</li>"; return; }
      var rows = r.body.coverage || [];
      state.covered = {};
      rows.forEach(function (c) { state.covered[c.sec_code] = true; });
      $("#my-cos-n").textContent = rows.length ? String(rows.length) : "";
      loadOverview(rows);
      if (!rows.length) {
        list.innerHTML = '<li class="my-empty">No companies yet. Add one above by name or ticker.</li>';
        return;
      }
      list.innerHTML = rows.map(function (c) {
        var us = isUS(c.sec_code);
        return '<li class="my-row" data-code="' + esc(c.sec_code) + '"><a class="my-open" href="' +
          (us ? "us-company.html?t=" + encodeURIComponent(c.sec_code.slice(4))
              : "company.html?code=" + encodeURIComponent(c.sec_code)) + '">' +
          '<span class="my-name">' + esc(c.name || c.name_ja || c.sec_code) + delistedTag(c) + "</span>" +
          '<span class="my-meta">' + (us ? "United States" : esc(c.sec_code) + (c.name_ja && c.name_ja !== c.name ? " · " + esc(c.name_ja) : "")) + "</span></a>" +
          '<span class="my-when" data-code="' + esc(c.sec_code) + '">…</span>' +
          '<button type="button" class="my-del" data-code="' + esc(c.sec_code) + '" aria-label="Remove ' +
          esc(c.name || c.sec_code) + '">Remove</button></li>';
      }).join("");
      // each US company's logo beside its name, once the logo map is in
      if (window.usLogos) usLogos.ready.then(function () {
        Array.prototype.forEach.call(list.querySelectorAll(".my-row"), function (row) {
          var code = row.getAttribute("data-code"), name = $(".my-name", row);
          var jp = rows.filter(function (c) { return c.sec_code === code; })[0] || {};
          if (name && !name.querySelector("img")) name.insertAdjacentHTML("afterbegin", isUS(code) ? usLogos.img(code, 18) : usLogos.at(jp.logo, 18));
        });
      });
      Array.prototype.forEach.call(list.querySelectorAll(".my-row"), function (row) {
        var code = row.getAttribute("data-code");
        if (isUS(code)) usDetails(code, row); else nextResults(code, $(".my-when", row));
      });
      Array.prototype.forEach.call(list.querySelectorAll(".my-del"), function (b) {
        b.onclick = function () {
          var code = b.getAttribute("data-code");
          var row = rows.filter(function (c) { return c.sec_code === code; })[0];
          api("DELETE", A + "/coverage/" + encodeURIComponent(code)).then(function (x) {
            if (!x.ok) { toast(detail(x, "That company could not be removed")); return; }
            loadCompanies();
            toast((row.name || code) + " removed.", function () {
              api("POST", A + "/coverage", { code: code }).then(function () { loadCompanies(); });
            });
          });
        };
      });
    });
  }

  function setupCompanySearch() {
    var input = $("#my-co-q"), hits = $("#my-co-hits"), timer = null, seq = 0, found = [];
    function hide() { hits.hidden = true; hits.innerHTML = ""; found = []; }
    function add(c) {
      if (state.covered[c.code]) { toast((c.name_en || c.name_ja) + " is already on your list."); hide(); input.value = ""; return; }
      input.disabled = true;
      api("POST", A + "/coverage", { code: c.code }).then(function (r) {
        input.disabled = false;
        if (!r.ok) { toast(detail(r, "That company could not be added")); return; }
        input.value = ""; hide(); input.focus();
        loadCompanies();
        toast((c.name_en || c.name_ja || c.code) + " added.", function () {
          api("DELETE", A + "/coverage/" + encodeURIComponent(c.code)).then(function () { loadCompanies(); });
        });
      });
    }
    function search() {
      var q = input.value.trim(), mine = ++seq;
      if (!q) { hide(); return; }
      api("GET", A + "/companies?markets=all&q=" + encodeURIComponent(q)).then(function (r) {
        if (mine !== seq) return;
        found = (r.ok && r.body.companies) || [];
        if (!found.length) {
          hits.innerHTML = '<li class="my-empty">No company found for “' + esc(q) + '”. Try its ticker, such as AAPL, or its Tokyo code, such as 7203.</li>';
          hits.hidden = false;
          return;
        }
        hits.innerHTML = found.map(function (c, i) {
          return '<li><button type="button" data-i="' + i + '"' + (c.delisted ? " disabled" : "") + "><span>" + esc(c.name_en || c.name_ja) + "</span>" +
            '<span class="my-meta">' + (c.market === "US" ? esc(c.ticker) + (c.exchange ? " · " + esc(c.exchange) : "") : esc(c.code) + " · Japan") +
            (c.delisted ? " · Delisted" : c.covered || state.covered[c.code] ? " · on your list" : "") + "</span></button></li>";
        }).join("");
        hits.hidden = false;
        Array.prototype.forEach.call(hits.querySelectorAll("button"), function (b) {
          b.onclick = function () { add(found[Number(b.getAttribute("data-i"))]); };
        });
      });
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 180); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); var first = found.filter(function (c) { return !c.delisted; })[0]; if (first) add(first); else search(); }
      if (e.key === "Escape") hide();
    });
    document.addEventListener("click", function (e) { if (!e.target.closest(".my-add")) hide(); });
  }

  /* ------------------------------------------------------------- reading */
  function bulletsHtml(text) {
    var lines = String(text || "").split("\n").map(function (l) { return l.replace(/^\s*-\s*/, "").trim(); }).filter(Boolean);
    return '<p class="my-label">Summary of this article</p><ul class="my-sum">' +
      lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>";
  }

  function loadReading() {
    return api("GET", A + "/reading").then(function (r) {
      var list = $("#my-reading");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your reading could not be loaded")) + "</li>"; return; }
      var items = r.body.reading || [];
      $("#my-read-n").textContent = items.length ? String(items.length) : "";
      if (!items.length) {
        list.innerHTML = '<li class="my-empty">Nothing saved yet. Use the extension or the bookmark below on any article.</li>';
        $("#my-howto").open = true;
        return;
      }
      list.innerHTML = items.map(function (it) {
        return '<li class="my-art" data-id="' + it.id + '"><div class="my-art-head">' +
          '<a class="my-name" href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer">' + esc(it.title) + "</a>" +
          '<span class="my-meta">' + esc(it.site) + " · saved " + esc(day(it.saved_at)) + "</span></div>" +
          '<div class="my-art-sum">' + (it.summary ? bulletsHtml(it.summary) : "") + "</div>" +
          '<p class="my-acts">' + (it.summary ? "" : '<button type="button" class="btn" data-act="sum">Summarize</button>') +
          '<button type="button" class="btn" data-act="del">Delete</button></p></li>';
      }).join("");
      Array.prototype.forEach.call(list.querySelectorAll(".my-art"), function (li) {
        var id = li.getAttribute("data-id");
        var sum = $('[data-act="sum"]', li), box = $(".my-art-sum", li);
        if (sum) sum.onclick = function () {
          sum.disabled = true; sum.textContent = "Summarizing…";
          box.innerHTML = '<p class="my-wait">Reading the article. This takes a few seconds.</p>';
          api("POST", A + "/reading/" + id + "/summary").then(function (x) {
            if (x.ok) { box.innerHTML = bulletsHtml(x.body.summary); sum.remove(); if (!state.hasKey) loadKey(); return; }
            var why = detail(x, "The summary could not be made");
            box.innerHTML = '<p class="my-err">' + esc(why) + settingsLink(why) + "</p>";
            sum.disabled = false; sum.textContent = "Try Again";
          }).catch(function () {
            box.innerHTML = '<p class="my-err">Plover could not be reached. Check your connection, then try again.</p>';
            sum.disabled = false; sum.textContent = "Try Again";
          });
        };
        $('[data-act="del"]', li).onclick = function () {
          li.hidden = true;
          // removed for good when the message goes, so Undo needs no copy of the text
          toast("Article deleted.", function () { li.hidden = false; }, function () {
            api("DELETE", A + "/reading/" + id, undefined, { keepalive: true }).then(function () { loadReading(); });
          });
        };
      });
    });
  }

  function setupBookmark() {
    var bm = $("#my-bm");
    if (!bm || typeof ploverExtract !== "function") return;
    var src = "void(function(o){var p=(" + ploverExtract.toString() + ")();" +
      "window.open(o+'/save.html#'+encodeURIComponent(JSON.stringify(p)),'plover-save','width=600,height=680');})(" +
      JSON.stringify(location.origin) + ")";
    bm.setAttribute("href", "javascript:" + encodeURIComponent(src));
    bm.addEventListener("click", function (e) {
      e.preventDefault();
      toast("Drag this button to your bookmarks bar, then click it on an article.");
    });
  }

  /* -------------------------------------------------------------- AI key
     The person's own model key, sealed on their account (/settings, the same
     store the Investment Assistant reads). Saving checks the key with the
     provider first, so a wrong key is never stored. */
  function keyNote() {
    var note = $("#my-keynote");
    if (state.hasKey) { note.hidden = true; return; }
    var f = state.free;
    note.innerHTML = f.on && f.left > 0
      ? "Stock questions are free. " + USES + " You have " + triesText(f.left) + " left on Plover's key. " +
        '<a href="#key">Add your own key</a> to keep going after that.'
      : f.on ? 'Stock questions are free. Your free tries for other questions are used up: <a href="#key">add your own AI key</a> to keep asking them.'
      : 'Stock questions are free. Other questions run on your own AI key: <a href="#key">add your key</a>. It takes a minute.';
    note.hidden = false;
  }

  function loadKey() {
    var box = $("#my-key");
    return api("GET", A + "/settings").then(function (r) {
      if (!r.ok) {
        box.innerHTML = '<p class="my-err">' + esc(detail(r, "Your key settings could not be loaded")) + ' <a href="my.html#key">Try again</a>.</p>';
        return;
      }
      var s = r.body;
      var codex = s.model_provider === "codex" && s.codex && s.codex.connected;
      state.hasKey = !!((s.model_provider && s.model_key_set) || codex);
      state.keyForSummaries = !!(s.model_provider && s.model_key_set && !codex);
      state.free = s.free || { on: false, left: 0, limit: 0 };
      keyNote();
      var provs = (s.providers || []).filter(function (p) { return p.id !== "codex"; });
      var cur = provs.some(function (p) { return p.id === s.model_provider; }) ? s.model_provider : (provs[0] || {}).id;
      var label = function (id) { var p = provs.filter(function (x) { return x.id === id; })[0]; return p ? p.label : id; };
      $("#my-key-n").textContent = state.hasKey ? "" : state.free.on && state.free.left ? state.free.left + " free" : "Needed";
      var status = codex ? "<p>Answers run on your connected ChatGPT account. Add a key below to use one instead.</p>"
        : state.keyForSummaries ? "<p>Your " + esc(label(s.model_provider)) + " key is stored, ending <strong>" + esc(s.model_key_last4 || "····") +
          "</strong>. Paste a new key to replace it.</p>"
        : "<p>" + (state.free.on ? (state.free.left > 0
            ? "Your first " + triesText(state.free.limit) + " run on Plover's key, and you have " + state.free.left + " left. "
            : "You have used your " + triesText(state.free.limit) + ". ") : "") +
          (READING_ON ? "Answers and article summaries" : "Answers") + " run on your own key from Anthropic, OpenAI or DeepSeek. " +
          "The key is stored encrypted on your account and used only for your own questions.</p>";
      if (!s.keychain) {
        box.innerHTML = status + '<p class="my-err">Keys cannot be stored on this server yet. Nothing is wrong on your side.</p>';
        return;
      }
      box.innerHTML = status +
        '<form class="my-key-form" id="my-key-form" autocomplete="off">' +
        '<label class="my-sr" for="my-key-prov">AI provider</label>' +
        '<select id="my-key-prov">' + provs.map(function (p) {
          return '<option value="' + esc(p.id) + '"' + (p.id === cur ? " selected" : "") + ">" + esc(p.label) + "</option>";
        }).join("") + "</select>" +
        '<label class="my-sr" for="my-key-in">API key</label>' +
        '<input id="my-key-in" type="password" spellcheck="false" placeholder="' +
        (state.keyForSummaries ? "Paste a new key to replace it" : "Paste your API key") + '">' +
        '<button type="submit" class="btn" id="my-key-save">' + (state.keyForSummaries ? "Replace Key" : "Save Key") + "</button></form>" +
        '<p class="my-key-msg" id="my-key-msg" role="status" aria-live="polite"></p>';
      $("#my-key-form").addEventListener("submit", function (e) {
        e.preventDefault();
        saveKey(s.model_provider);
      });
    }).catch(function () {
      box.innerHTML = '<p class="my-err">Plover could not be reached. Check your connection, then <a href="my.html#key">try again</a>.</p>';
    });
  }

  function saveKey(storedProvider) {
    var prov = $("#my-key-prov").value, key = $("#my-key-in").value.trim();
    var btn = $("#my-key-save"), msg = $("#my-key-msg"), label = btn.textContent;
    if (!key) { msg.className = "my-key-msg my-err"; msg.textContent = "Paste your key first."; $("#my-key-in").focus(); return; }
    btn.disabled = true; btn.textContent = "Checking…";
    msg.className = "my-key-msg"; msg.textContent = "Checking the key with the provider. This takes a few seconds.";
    // the same provider keeps its chosen model; naming the provider resets it
    var body = prov === storedProvider ? { model_key: key } : { model_provider: prov, model_key: key };
    api("POST", A + "/settings/test", { model_provider: prov, model_key: key }).then(function (t) {
      if (!t.ok) {
        // the provider's own error body is for engineers; say what it means
        var raw = String((t.body && t.body.detail) || "");
        throw { why: /\b40[13]\b|auth|invalid|incorrect/i.test(raw)
          ? "The provider did not accept that key. Check you chose the right provider and copied the whole key."
          : t.status === 400 ? detail(t, "That key could not be checked")
          : "The provider could not be reached to check the key. Try again in a minute." };
      }
      return api("POST", A + "/settings", body);
    }).then(function (r) {
      if (!r.ok) throw { why: detail(r, "The key could not be saved") };
      return loadKey().then(function () { toast("Key saved. You can ask a question now."); });
    }).catch(function (e) {
      btn.disabled = false; btn.textContent = label;
      msg.className = "my-key-msg my-err";
      msg.textContent = e && e.why ? e.why : "Plover could not be reached. Check your connection, then try again.";
    });
  }

  /* ---------------------------------------------------------------- charts
     The Charts tab: a sentence in the box becomes a chart spec (GET
     /api/v1/us/build/chart, app/us_build_api.py), the page fetches the closes
     or the filed figures the spec names and draws them, and every control in
     the Customize panel edits the same spec and redraws. The address carries
     the spec (#charts?s=AAPL,MSFT&m=price&p=ytd…), so a copied link reopens
     the same chart; a saved chart is the spec, redrawn from today's data. */
  var US = "/api/v1/us";
  var CH = { spec: null, id: null, chart: null, seq: 0, measures: [], benchmarks: [], saved: [], cache: {} };
  var CH_DEFAULT = { series: [], measure: "price", period: "ytd", from: null, to: null, benchmarks: ["sp500"],
                     display: "rebased", log: false, chart_type: "line", mark_results: false, title: "" };
  var MEASURE_TEXT = {
    price: "Share price", revenue: "Revenue, quarterly", revenue_ttm: "Revenue, trailing 12 months",
    operating_margin: "Operating margin", net_margin: "Net margin", net_income: "Net profit, quarterly",
    eps: "Earnings per share, diluted", fcf: "Free cash flow, quarterly", rnd: "Research spending, quarterly",
    shares: "Shares outstanding" };
  var MEASURE_HOW = {
    price: { how: "price", unit: "USD" }, revenue: { how: "field", field: "revenue", unit: "USD" },
    revenue_ttm: { how: "field_ttm", field: "revenue", unit: "USD" },
    operating_margin: { how: "ratio", num: "operating_income", den: "revenue", unit: "%" },
    net_margin: { how: "ratio", num: "net_income", den: "revenue", unit: "%" },
    net_income: { how: "field", field: "net_income", unit: "USD" }, eps: { how: "field", field: "eps_diluted", unit: "USD/share" },
    fcf: { how: "fcf", unit: "USD" }, rnd: { how: "tag", tag: "ResearchAndDevelopmentExpense", unit: "USD" },
    shares: { how: "field", field: "shares_outstanding", unit: "shares" } };
  var PERIOD_TEXT = { "1m": "1 month", "3m": "3 months", ytd: "year to date", "1y": "1 year", "5y": "5 years", max: "all history" };
  function chGet(path) {
    if (CH.cache[path]) return CH.cache[path];
    var p = fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      return r.ok ? r.json() : r.json().catch(function () { return {}; }).then(function (b) {
        throw new Error(typeof b.detail === "string" ? b.detail : "Plover could not load that (" + r.status + ")"); });
    });
    CH.cache[path] = p;
    p.catch(function () { delete CH.cache[path]; });
    return p;
  }
  function chStart(spec) {
    if (spec.from) return spec.from;
    var d = new Date(), p = spec.period || "ytd";
    if (p === "ytd") return iso(new Date(Date.UTC(d.getUTCFullYear(), 0, 1)));
    if (p === "max") return "1990-01-01";
    var months = { "1m": 1, "3m": 3, "1y": 12, "5y": 60 }[p] || 12;
    d.setUTCMonth(d.getUTCMonth() - months);
    return iso(d);
  }
  function chBench(key) {
    for (var i = 0; i < CH.benchmarks.length; i++) if (CH.benchmarks[i].id === key) return CH.benchmarks[i];
    return null;
  }
  function chBenches(spec) { return (spec.benchmarks || []).map(chBench).filter(Boolean); }
  // a chart holds six lines, one colour each: companies from the first colour, indexes from the last
  function chBenchColor(j) { return chSeriesColor(5 - j); }
  function chSeriesColor(i) { return "var(--obs-series-" + ((i % 6) + 1) + ")"; }
  /* quarterly rows from the indicators panel, with each fourth quarter derived
     as the year less its three filed quarters (the SEC's own convention) */
  function chQuarters(t) {
    return Promise.all([chGet(US + "/financials/deep/" + encodeURIComponent(t) + "/indicators?freq=quarterly&limit=200"),
                        chGet(US + "/financials/deep/" + encodeURIComponent(t) + "/indicators?freq=annual&limit=40")])
      .then(function (r) {
        var q = (r[0].panel || []).slice().sort(function (a, b) { return a.period_end < b.period_end ? -1 : 1; });
        var out = q.map(function (p) { return { end: p.period_end, start: p.period_start, values: p.values, derived: false }; });
        (r[1].panel || []).forEach(function (y) {
          if (!y.period_start) return;
          var inYear = q.filter(function (p) { return p.period_end > y.period_start && p.period_end < y.period_end; });
          if (inYear.length !== 3 || q.some(function (p) { return p.period_end === y.period_end; })) return;
          var v = {};
          Object.keys(y.values).forEach(function (k) {
            var a = y.values[k];
            if (a == null || /^(eps_|shares_|total_|equity|cash$)/.test(k)) { v[k] = /^(eps_|shares_|total_|equity|cash$)/.test(k) ? a : null; return; }
            var s = 0, ok = true;
            inYear.forEach(function (p) { if (p.values[k] == null) ok = false; else s += p.values[k]; });
            v[k] = ok ? a - s : null;
          });
          out.push({ end: y.period_end, start: null, values: v, derived: true });
        });
        out.sort(function (a, b) { return a.end < b.end ? -1 : 1; });
        return out;
      });
  }
  function chLoadSeries(s, spec) {
    var m = MEASURE_HOW[spec.measure] || MEASURE_HOW.price, start = chStart(spec);
    // a year-on-year display needs the year before the window too
    var fetchFrom = spec.display === "yoy" ? iso(new Date(new Date(start + "T00:00:00Z").setUTCFullYear(new Date(start + "T00:00:00Z").getUTCFullYear() - 1))) : start;
    var name = s.name || s.ticker;
    if (m.how === "price") {
      return chGet(US + "/prices/" + encodeURIComponent(s.ticker) + "?start=" + fetchFrom).then(function (d) {
        return { name: name, ticker: s.ticker, daily: true, points: (d.bars || []).map(function (b) { return [b.date, b.adjusted_close != null ? b.adjusted_close : b.close]; }),
                 splits: d.splits || [], source: "Databento" };
      });
    }
    if (m.how === "tag") {
      return chGet(US + "/financials/deep/" + encodeURIComponent(s.ticker) + "/series?tag=" + m.tag + "&freq=quarterly").then(function (d) {
        var pts = (d.values || []).map(function (v) { return [v.period_end, v.value]; }).sort(function (a, b) { return a[0] < b[0] ? -1 : 1; });
        return { name: name, ticker: s.ticker, points: pts, source: "SEC EDGAR", tag: m.tag };
      });
    }
    return chQuarters(s.ticker).then(function (rows) {
      var pts = [], derived = 0;
      rows.forEach(function (r, i) {
        var v = null;
        if (m.how === "field") v = r.values[m.field];
        else if (m.how === "ratio") v = (r.values[m.num] != null && r.values[m.den]) ? r.values[m.num] / r.values[m.den] * 100 : null;
        else if (m.how === "fcf") v = (r.values.cf_operating != null && r.values.capex != null) ? r.values.cf_operating - Math.abs(r.values.capex) : null;
        else if (m.how === "field_ttm") {
          var last4 = rows.slice(Math.max(0, i - 3), i + 1);
          v = last4.length === 4 && last4.every(function (x) { return x.values[m.field] != null; })
            ? last4.reduce(function (a, x) { return a + x.values[m.field]; }, 0) : null;
        }
        if (r.derived && v != null) derived++;
        pts.push([r.end, v]);
      });
      return { name: name, ticker: s.ticker, points: pts, source: "SEC EDGAR", derived: derived };
    });
  }
  function chTransform(series, spec, start) {
    // rebased: ÷ the first value in the window × 100; yoy: ÷ the value a year earlier − 1
    return series.map(function (s) {
      var pts = s.points;
      if (spec.display === "yoy") {
        var byDate = {}; pts.forEach(function (p) { byDate[p[0]] = p[1]; });
        var dates = pts.map(function (p) { return p[0]; });
        pts = pts.map(function (p) {
          var ago = new Date(p[0] + "T00:00:00Z"); ago.setUTCFullYear(ago.getUTCFullYear() - 1);
          var key = iso(ago), prev = byDate[key];
          if (prev === undefined && s.daily) {           // nearest earlier trading day
            var i = dates.length - 1; while (i >= 0 && dates[i] > key) i--;
            prev = i >= 0 && dates[i] >= iso(new Date(ago.getTime() - 10 * 864e5)) ? byDate[dates[i]] : undefined;
          } else if (prev === undefined && !s.daily) {   // the quarter about a year earlier
            var j = dates.length - 1; while (j >= 0 && dates[j] > key) j--;
            prev = j >= 0 && dates[j] >= iso(new Date(ago.getTime() - 20 * 864e5)) ? byDate[dates[j]] : undefined;
          }
          return [p[0], (p[1] != null && prev != null && prev !== 0) ? (p[1] / prev - 1) * 100 : null];
        });
      }
      pts = pts.filter(function (p) { return p[0] >= start && (!spec.to || p[0] <= spec.to); });
      if (spec.display === "rebased") {
        var base = null;
        for (var k = 0; k < pts.length && base == null; k++) base = pts[k][1];
        pts = pts.map(function (p) { return [p[0], (p[1] != null && base) ? p[1] / base * 100 : null]; });
      }
      return { name: s.name, ticker: s.ticker, slot: s.slot, points: pts, daily: s.daily, source: s.source, derived: s.derived, splits: s.splits, tag: s.tag };
    });
  }
  function chDescribe(spec) {
    var m = MEASURE_TEXT[spec.measure] || "Share price";
    var how = { rebased: ", rebased to 100", yoy: ", change on a year earlier", actual: "" }[spec.display] || "";
    var names = spec.series.map(function (s) { return s.name || s.ticker; });
    var both = function (xs) { return xs.length ? xs.slice(0, -1).join(", ") + (xs.length > 1 ? " and " : "") + xs[xs.length - 1] : ""; };
    var who = both(names), idx = spec.measure === "price" ? chBenches(spec).map(function (b) { return b.name; }) : [];
    if (idx.length) who += (who ? " against the " : "") + both(idx);
    return m + how + (who ? ": " + who : "");
  }
  function chCalc(spec, series) {
    var lines = [];
    if (spec.measure === "price") lines.push("Closes are Databento's end-of-day prices, adjusted for splits.");
    if (spec.measure === "price") chBenches(spec).forEach(function (b) { lines.push(b.label + " is drawn from " + b.fund + ", the fund that tracks it: its share price, without dividends, not the index level itself."); });
    if (spec.display === "rebased") lines.push("Rebased value = value ÷ the first value in the period × 100.");
    if (spec.display === "yoy") lines.push("Change = value ÷ the value a year earlier − 1, as a percentage.");
    if (spec.measure === "operating_margin") lines.push("Operating margin = operating income ÷ revenue × 100, each quarter as filed.");
    if (spec.measure === "net_margin") lines.push("Net margin = net income ÷ revenue × 100, each quarter as filed.");
    if (spec.measure === "fcf") lines.push("Free cash flow = cash from operations − capital spending, each quarter as filed.");
    if (spec.measure === "revenue_ttm") lines.push("Trailing 12 months = the sum of the last four quarters.");
    if (spec.measure === "rnd") lines.push("Research spending is the figure the company tags as research and development expense.");
    if (spec.measure !== "price") lines.push("A fourth quarter is derived as the year less its three filed quarters, as the SEC does; per-share and balance-sheet figures are not derived.");
    series.forEach(function (s) {
      (s.splits || []).forEach(function (sp) { lines.push(s.ticker + ": " + sp.note); });
    });
    if (spec.log) lines.push("Log scale: equal vertical distances are equal percentage moves.");
    return lines;
  }
  function chSource(series, spec) {
    var src = [];
    series.forEach(function (s) { if (s.source && src.indexOf(s.source) < 0) src.push(s.source); });
    if (spec.mark_results) src.push("results days from SEC 8-K filings");
    return "Source: " + src.join(", ") + ". Plover Analytics.";
  }
  function chRender(series, events) {
    var spec = CH.spec, el = $("#ch-plot"), m = MEASURE_HOW[spec.measure] || MEASURE_HOW.price;
    var unit = spec.display === "actual" ? m.unit : "%";
    var vals = []; series.forEach(function (s) { s.points.forEach(function (p) { if (p[1] != null) vals.push(Math.abs(p[1])); }); });
    var max = vals.length ? Math.max.apply(null, vals) : 0, k = 1, label = unit;
    if (spec.display === "actual" && unit === "USD") {
      if (max >= 1e12) { k = 1e12; label = "$tn"; } else if (max >= 1e9) { k = 1e9; label = "$bn"; } else if (max >= 1e6) { k = 1e6; label = "$mn"; } else label = "$";
    } else if (spec.display === "actual" && unit === "shares") {
      if (max >= 1e9) { k = 1e9; label = "bn shares"; } else if (max >= 1e6) { k = 1e6; label = "mn shares"; }
    } else if (spec.display === "actual" && unit === "USD/share") label = "$ per share";
    var dp = spec.display === "rebased" ? 1 : (unit === "%" ? 1 : (k > 1 ? 1 : 2));
    var cfg = {
      series: series.map(function (s) {
        return { name: s.name, slot: s.slot, points: s.points.map(function (p) { return [p[0], p[1] == null ? null : p[1] / k]; }) };
      }),
      dp: dp, unit: unit === "%" && spec.display !== "rebased" ? "%" : "x",
      unitSuffix: spec.display === "rebased" ? "" : (unit === "%" ? "" : label),
      yAxisName: spec.display === "rebased" ? "Rebased, start = 100" : (unit === "%" ? (spec.display === "yoy" ? "Change on a year earlier, %" : "%") : label),
      yAxisDp: dp, isoPeriods: !!series[0] && series[0].daily, sourceLine: chSource(series, spec),
      logScale: !!spec.log && spec.display !== "yoy",
      // a log axis spans whole powers of ten unless told the range: pad the data a little
      yMin: (spec.log && spec.display !== "yoy" && vals.length) ? +(Math.min.apply(null, vals.map(function (v) { return v / k; })) * 0.95).toPrecision(2) : undefined,
      yMax: (spec.log && spec.display !== "yoy" && vals.length) ? +(max / k * 1.05).toPrecision(2) : undefined,
      refLine: spec.display === "rebased" ? { y: 100, label: "100" } : null,
      eventLines: (events || []).map(function (e) { return { x: e.date, label: e.ticker }; }),
      legendFloor: 640 };
    var kind = "line";
    if (spec.chart_type === "bars" || (!series[0].daily && series.length === 1 && spec.display === "actual")) {
      var xs = [];
      series.forEach(function (s) { s.points.forEach(function (p) { if (xs.indexOf(p[0]) < 0) xs.push(p[0]); }); });
      xs.sort();
      cfg.categories = xs.map(function (x) { return series[0].daily ? day(x).replace(/ \d{4}$/, "") : fmtPeriod(x); });
      cfg.series = cfg.series.map(function (s) {
        var mp = {}; s.points.forEach(function (p) { mp[p[0]] = p[1]; });
        return { name: s.name, slot: s.slot, points: xs.map(function (x) { return mp[x] === undefined ? null : mp[x]; }) };
      });
      kind = "cols";
    }
    try { if (CH.chart) CH.chart.dispose(); } catch (e) { /* gone */ }
    el.hidden = false; $("#ch-empty").hidden = true;
    CH.chart = obsChart(el, kind, cfg);
    CH.chartKind = kind; CH.chartCfg = cfg;
    drawn.push({ el: el, make: function () { return obsChart(el, kind, cfg); } });
    $("#ch-src").textContent = chSource(series, spec);
    var calc = chCalc(spec, series);
    $("#ch-calc").innerHTML = calc.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("");
    $("#ch-calcbox").hidden = !calc.length;
  }
  function chEmpty(text, err) {
    try { if (CH.chart) CH.chart.dispose(); } catch (e) { /* gone */ }
    CH.chart = null;
    $("#ch-plot").hidden = true;
    var p = $("#ch-empty"); p.hidden = false; p.className = "my-empty ch-empty" + (err ? " my-err" : ""); p.textContent = text;
    $("#ch-src").textContent = ""; $("#ch-calcbox").hidden = true;
  }
  function chLoad() {
    var spec = CH.spec, mine = ++CH.seq;
    chSync();
    // each line keeps the colour of its chip, even when another line has no data to draw
    var ss = spec.series.slice(0, 6).map(function (s, i) { return Object.assign({}, s, { slot: i + 1 }); });
    if (spec.measure === "price") ss = ss.concat(chBenches(spec).map(function (b, j) { return { ticker: b.fund, name: b.name, slot: 6 - j }; }));
    if (!ss.length) { chEmpty("Describe a chart above, or pick companies on the right."); return Promise.resolve(); }
    chEmpty("Loading…");
    var start = chStart(spec);
    var work = ss.map(function (s) { return chLoadSeries(s, spec).then(function (r) { r.slot = s.slot; return r; }); });
    if (spec.mark_results) {
      work.push(Promise.all(spec.series.slice(0, 6).map(function (s) {
        return chGet(US + "/events?kind=results&ticker=" + encodeURIComponent(s.ticker) + "&since=" + start).then(function (d) {
          return (d.events || []).filter(function (e) { return e.is_earnings; }).map(function (e) { return { date: e.date, ticker: s.ticker }; });
        }).catch(function () { return []; });
      })).then(function (lists) { return { events: [].concat.apply([], lists) }; }));
    }
    return Promise.all(work).then(function (res) {
      if (mine !== CH.seq) return;
      var events = spec.mark_results ? res.pop().events : [];
      var series = chTransform(res, spec, start).filter(function (s) { return s.points.some(function (p) { return p[1] != null; }); });
      if (!series.length) { chEmpty("Nothing to draw: no " + (MEASURE_TEXT[spec.measure] || "").toLowerCase() + " is on file for this period."); return; }
      chRender(series, events);
    }).catch(function (e) {
      if (mine !== CH.seq) return;
      chEmpty((e && e.message) || "Plover could not load the data. Try again in a moment.", true);
    });
  }
  function chSync() {
    var spec = CH.spec;
    $("#ch-title").value = spec.title || chDescribe(spec);
    $("#ch-series").innerHTML = spec.series.map(function (s, i) {
      return '<button type="button" class="ch-chip" data-i="' + i + '" title="Remove ' + esc(s.name || s.ticker) + '">' +
        '<span class="ch-dot" style="background:' + chSeriesColor(i) + '"></span>' + esc(s.ticker) + '<span class="ch-x" aria-hidden="true">×</span></button>';
    }).join("");
    Array.prototype.forEach.call($("#ch-series").querySelectorAll(".ch-chip"), function (b) {
      b.onclick = function () { CH.spec.series.splice(Number(b.dataset.i), 1); CH.spec.title = ""; chChanged(); };
    });
    $("#ch-measure").value = spec.measure;
    Array.prototype.forEach.call($("#ch-period").querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", (!spec.from && b.dataset.p === (spec.period || "ytd")) ? "true" : "false");
    });
    var since = $("#ch-since");
    since.hidden = !spec.from;
    if (spec.from) since.textContent = "From " + day(spec.from) + (spec.to ? " to " + day(spec.to) : "") + ".";
    chSyncBenches(spec);
    $("#ch-display").value = spec.display;
    $("#ch-log").checked = !!spec.log;
    $("#ch-events").checked = !!spec.mark_results;
    $("#ch-type").value = spec.chart_type || "line";
    $("#ch-save").textContent = CH.id ? "Saved" : "Save to My Charts";
    $("#ch-save").disabled = false;
    history.replaceState(null, "", "#charts?" + chHash(spec));
  }
  function chSyncBenches(spec) {
    var room = Math.max(0, 6 - spec.series.length);
    if (spec.benchmarks.length > room) spec.benchmarks = spec.benchmarks.slice(0, room);
    var isPrice = spec.measure === "price", box = $("#ch-benches"), sel = $("#ch-bench"), note = $("#ch-bench-note");
    box.hidden = sel.hidden = !isPrice;
    box.innerHTML = !isPrice ? "" : spec.benchmarks.map(function (key, j) {
      var b = chBench(key), name = b ? b.label : key;
      return '<button type="button" class="ch-chip" data-key="' + esc(key) + '" title="Remove ' + esc(name) + '">' +
        '<span class="ch-dot" style="background:' + chBenchColor(j) + '"></span>' + esc(name) + '<span class="ch-x" aria-hidden="true">×</span></button>';
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".ch-chip"), function (c) {
      c.onclick = function () { CH.spec.benchmarks = CH.spec.benchmarks.filter(function (k) { return k !== c.dataset.key; }); CH.spec.title = ""; chChanged(); };
    });
    var full = spec.benchmarks.length >= room;
    sel.disabled = full;
    sel.innerHTML = '<option value="">+ Add an index</option>' + CH.benchmarks.filter(function (b) { return spec.benchmarks.indexOf(b.id) < 0; }).map(function (b) {
      return '<option value="' + esc(b.id) + '">' + esc(b.name) + "</option>";
    }).join("");
    sel.value = "";
    note.hidden = isPrice && !full;
    note.textContent = "";
    if (!isPrice) {
      note.appendChild(document.createTextNode("An index has no " + (MEASURE_TEXT[spec.measure] || "").toLowerCase().replace(/, .*$/, "") + ". Indexes show on share price only. "));
      var go = document.createElement("button");
      go.type = "button"; go.className = "my-link"; go.textContent = "Show share price";
      go.onclick = function () { var m = $("#ch-measure"); m.value = "price"; m.dispatchEvent(new Event("change")); };
      note.appendChild(go);
    } else if (full) note.textContent = "A chart holds six lines. Remove one to add another index.";
  }
  function chHash(spec) {
    var p = new URLSearchParams();
    p.set("s", spec.series.map(function (s) { return s.ticker; }).join(","));
    p.set("m", spec.measure); if (spec.from) { p.set("from", spec.from); if (spec.to) p.set("to", spec.to); } else p.set("p", spec.period || "ytd");
    p.set("b", (spec.benchmarks || []).join(",") || "none"); p.set("d", spec.display);
    if (spec.log) p.set("log", "1"); if (spec.mark_results) p.set("ev", "1"); if (spec.chart_type === "bars") p.set("ct", "bars");
    if (spec.title) p.set("title", spec.title);
    return p.toString();
  }
  function chFromHash(qs) {
    var p = new URLSearchParams(qs), s = Object.assign({}, CH_DEFAULT);
    var tks = (p.get("s") || "").split(",").map(function (t) { return t.trim().toUpperCase(); }).filter(Boolean);
    if (!tks.length && !p.get("b")) return null;
    s.series = tks.map(function (t) { return { ticker: t, name: t }; });
    s.measure = MEASURE_HOW[p.get("m")] ? p.get("m") : "price";
    if (p.get("from")) { s.from = p.get("from"); s.to = p.get("to") || null; s.period = null; } else s.period = PERIOD_TEXT[p.get("p")] ? p.get("p") : "ytd";
    s.benchmarks = p.get("b") ? p.get("b").split(",").filter(function (k) { return k && k !== "none"; }) : CH_DEFAULT.benchmarks;
    s.display = ["rebased", "actual", "yoy"].indexOf(p.get("d")) >= 0 ? p.get("d") : (s.measure === "price" ? "rebased" : "actual");
    s.log = p.get("log") === "1"; s.mark_results = p.get("ev") === "1"; s.chart_type = p.get("ct") === "bars" ? "bars" : "line";
    s.title = p.get("title") || "";
    return s;
  }
  function chNames(spec) {
    // tickers from a link carry no names, so each is looked up once
    var need = spec.series.filter(function (s) { return !s.name || s.name === s.ticker; });
    return Promise.all(need.map(function (s) {
      return chGet(US + "/company?q=" + encodeURIComponent(s.ticker)).then(function (d) {
        var hit = (d.companies || []).filter(function (c) { return c.ticker === s.ticker; })[0];
        if (hit) { s.name = hit.name; s.cik = hit.cik; }
      }).catch(function () { /* keep the ticker */ });
    }));
  }
  function chChanged() {
    CH.id = null;                                  // an edit makes a new chart; Save keeps it
    chLoad();
  }
  function chSetSpec(spec) {
    var s = Object.assign({}, CH_DEFAULT, spec);
    s.series = (s.series || []).slice(0, 6);
    // a chart saved before several indexes were allowed holds one
    if (spec && spec.benchmarks === undefined && "benchmark" in spec) s.benchmarks = spec.benchmark ? [spec.benchmark] : [];
    delete s.benchmark;
    if (s.measure !== "price") { if (s.display === "rebased") s.display = "actual"; }
    CH.spec = s;
    chSync();                                      // the controls show at once; names fill in as they arrive
    chEmpty("Loading…");
    return chNames(s).then(chLoad);
  }
  function chFromWords(q) {
    q = (q || "").trim();
    if (!q) return;
    var btn = $("#ch-go"), read = $("#ch-read");
    btn.disabled = true; read.hidden = false; read.textContent = "Reading…";
    var url = US + "/build/chart?q=" + encodeURIComponent(q) + (CH.spec && CH.spec.series.length ? "&current=" + encodeURIComponent(JSON.stringify(CH.spec)) : "");
    fetch(url, { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      if (!d || !d.spec) throw new Error();
      CH.measures = d.measures || CH.measures; CH.benchmarks = d.benchmarks || CH.benchmarks;
      if (!d.spec.series.length && !(d.spec.benchmarks || []).length) {
        read.textContent = "Plover could not find a company in that. Name one, e.g. “Apple and Microsoft this year”.";
        return;
      }
      if (CH.spec && CH.spec.series.length && chHash(Object.assign({}, CH.spec, d.spec, { title: "" })) === chHash(Object.assign({}, CH.spec, { title: "" }))) {
        read.textContent = CH.spec.series.length + CH.spec.benchmarks.length >= 6
          ? "That made no change to the chart. A chart holds six lines, so remove one to add another."
          : "That made no change to the chart. Try \u201cadd Amazon\u201d, \u201cmake it revenue\u201d or \u201csince 2020\u201d, or describe a new chart.";
        return;
      }
      read.textContent = "Read as: " + (d.read_as || d.spec.title);
      CH.id = null;
      chSetSpec(d.spec);
    }).catch(function () {
      btn.disabled = false;
      read.textContent = "Plover could not read that. Check your connection, then try again.";
    });
  }
  function chTo(tickers, names) {
    // from the screener or the watchlist: chart these companies against the S&P 500
    var spec = Object.assign({}, CH_DEFAULT, { series: tickers.slice(0, 6).map(function (t, i) { return { ticker: t, name: (names || [])[i] || t }; }) });
    history.pushState(null, "", "#charts");
    CH.id = null;
    chSetSpec(spec);
    route();
  }
  function chSaved() {
    var ul = $("#ch-saved");
    ul.innerHTML = CH.saved.length ? CH.saved.map(function (c) {
      var sp = c.spec || {};
      var sub = (MEASURE_TEXT[sp.measure] || "Share price") + " · " + (sp.from ? "from " + day(sp.from) : PERIOD_TEXT[sp.period] || "") + " · saved " + day(iso(new Date(c.updated_at * 1000)));
      return '<li><a class="my-name" href="#charts?' + esc(chHash(sp)) + '" data-id="' + c.id + '">' + esc(c.title) + '</a><span class="my-meta">' + esc(sub) + '</span>' +
        '<span class="my-acts"><a class="my-link" href="#charts?' + esc(chHash(sp)) + '" data-id="' + c.id + '">Open</a>' +
        '<button type="button" class="my-link ch-copy" data-id="' + c.id + '">Copy link</button>' +
        '<button type="button" class="my-link ch-del" data-id="' + c.id + '">Delete</button></span></li>';
    }).join("") : '<li class="my-empty">No saved charts yet. Draw one and click Save to My Charts.</li>';
    $("#my-ch-n").textContent = CH.saved.length ? String(CH.saved.length) : "";
    Array.prototype.forEach.call(ul.querySelectorAll("a[data-id]"), function (a) {
      a.onclick = function () { CH.id = Number(a.dataset.id); };
    });
    Array.prototype.forEach.call(ul.querySelectorAll(".ch-copy"), function (b) {
      b.onclick = function () {
        var c = CH.saved.filter(function (x) { return x.id === Number(b.dataset.id); })[0];
        if (c) chCopy(location.origin + "/my.html#charts?" + chHash(c.spec));
      };
    });
    Array.prototype.forEach.call(ul.querySelectorAll(".ch-del"), function (b) {
      b.onclick = function () {
        var id = Number(b.dataset.id), gone = CH.saved.filter(function (x) { return x.id === id; })[0];
        CH.saved = CH.saved.filter(function (x) { return x.id !== id; });
        if (CH.id === id) { CH.id = null; chSync(); }
        chSaved();
        toast("Chart deleted.", function () { CH.saved.push(gone); chSaved(); }, function () {
          api("DELETE", A + "/charts/" + id).then(function (r) { if (r.ok) { CH.saved = r.body.charts; chSaved(); } });
        });
      };
    });
  }
  function loadSavedCharts() {
    return api("GET", A + "/charts").then(function (r) { if (r.ok) { CH.saved = r.body.charts || []; chSaved(); } });
  }
  function chCopy(url) {
    var done = function () { toast("Link copied. Anyone signed in to Plover who opens it sees this chart, drawn from that day's data."); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { window.prompt("Copy this link", url); });
    else window.prompt("Copy this link", url);
  }
  function setupCharts() {
    $("#ch-measure").innerHTML = Object.keys(MEASURE_TEXT).map(function (k) { return '<option value="' + k + '">' + esc(MEASURE_TEXT[k]) + "</option>"; }).join("");
    fetch(US + "/build/chart?q=S%26P%20500", { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      CH.benchmarks = d.benchmarks || []; CH.measures = d.measures || [];
      if (CH.spec) chSync();
    }).catch(function () { /* the select stays short */ });
    $("#ch-ask").addEventListener("submit", function (e) { e.preventDefault(); chFromWords($("#ch-q").value); });
    Array.prototype.forEach.call(document.querySelectorAll(".ch-hint"), function (b) {
      b.onclick = function () { $("#ch-q").value = b.textContent; chFromWords(b.textContent); };
    });
    $("#ch-measure").addEventListener("change", function () {
      if (!CH.spec) CH.spec = Object.assign({}, CH_DEFAULT);
      CH.spec.measure = this.value;
      if (this.value !== "price") { if (CH.spec.display === "rebased") CH.spec.display = "actual"; if (CH.spec.period === "ytd" || CH.spec.period === "1m" || CH.spec.period === "3m") CH.spec.period = "5y"; }
      else if (CH.spec.display === "actual" && CH.spec.series.length > 1) CH.spec.display = "rebased";
      CH.spec.title = ""; chChanged();
    });
    $("#ch-period").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b || !CH.spec) return;
      CH.spec.period = b.dataset.p; CH.spec.from = CH.spec.to = null; chChanged();
    });
    $("#ch-bench").addEventListener("change", function () {
      if (!this.value) return;
      if (!CH.spec) CH.spec = Object.assign({}, CH_DEFAULT, { benchmarks: [] });
      CH.spec.benchmarks = CH.spec.benchmarks.concat([this.value]); CH.spec.title = ""; chChanged();
    });
    $("#ch-display").addEventListener("change", function () { if (!CH.spec) return; CH.spec.display = this.value; CH.spec.title = ""; chChanged(); });
    $("#ch-log").addEventListener("change", function () { if (!CH.spec) return; CH.spec.log = this.checked; chChanged(); });
    $("#ch-events").addEventListener("change", function () { if (!CH.spec) return; CH.spec.mark_results = this.checked; chChanged(); });
    $("#ch-type").addEventListener("change", function () { if (!CH.spec) return; CH.spec.chart_type = this.value; chChanged(); });
    $("#ch-title").addEventListener("change", function () { if (!CH.spec) return; CH.spec.title = this.value.trim(); history.replaceState(null, "", "#charts?" + chHash(CH.spec)); });
    $("#ch-save").addEventListener("click", function () {
      if (!CH.spec || !CH.spec.series.length) { toast("Draw a chart first."); return; }
      var btn = this; btn.disabled = true;
      var title = $("#ch-title").value.trim() || chDescribe(CH.spec);
      CH.spec.title = title;
      api("POST", A + "/charts", { title: title, spec: CH.spec, id: CH.id }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { toast(detail(r, "The chart could not be saved")); return; }
        CH.id = r.body.chart.id; CH.saved = r.body.charts; chSaved(); chSync();
        toast("Saved to My Charts.");
      });
    });
    $("#ch-png").addEventListener("click", function () { if (CH.chart) CH.chart.exportPNG("plover-chart.png"); });
    $("#ch-csv").addEventListener("click", function () {
      if (!CH.chart) return;
      var spec = CH.spec;
      CH.chart.exportCSV("plover-chart.csv", [
        "Plover Analytics — " + ($("#ch-title").value || chDescribe(spec)),
        "Measure: " + (MEASURE_TEXT[spec.measure] || "") + "; display: " + spec.display + "; period: " + (spec.from ? "from " + spec.from : PERIOD_TEXT[spec.period] || ""),
        $("#ch-src").textContent, "Downloaded " + iso(new Date()), "Link: " + location.origin + "/my.html#charts?" + chHash(spec)
      ].concat(chCalc(spec, [])));
    });
    $("#ch-link").addEventListener("click", function () { if (CH.spec) chCopy(location.origin + "/my.html#charts?" + chHash(CH.spec)); });
    // add a company: the same type-ahead as the Watchlist, over the SEC universe
    var box = $("#ch-add"), hits = $("#ch-add-hits"), timer = null, last = "";
    function closeHits() { hits.hidden = true; hits.innerHTML = ""; }
    box.addEventListener("input", function () {
      var q = box.value.trim();
      clearTimeout(timer);
      if (q.length < 1) { closeHits(); return; }
      timer = setTimeout(function () {
        last = q;
        chGet(US + "/company?q=" + encodeURIComponent(q)).then(function (d) {
          if (last !== q) return;
          var cos = (d.companies || []).slice(0, 8);
          hits.innerHTML = cos.length ? cos.map(function (c, i) {
            return '<li><button type="button" data-i="' + i + '"><span>' + esc(c.name) + '</span><span class="my-meta">' + esc(c.ticker) + (c.exchange ? " · " + esc(c.exchange) : "") + "</span></button></li>";
          }).join("") : '<li class="my-empty">No US company matches. Try its ticker.</li>';
          hits.hidden = false;
          Array.prototype.forEach.call(hits.querySelectorAll("button"), function (b) {
            b.onclick = function () {
              var c = cos[Number(b.dataset.i)];
              if (!CH.spec) CH.spec = Object.assign({}, CH_DEFAULT);
              if (CH.spec.series.length >= 6) { toast("Six series at most. Remove one first."); closeHits(); return; }
              if (CH.spec.series.some(function (s) { return s.ticker === c.ticker; })) { closeHits(); box.value = ""; return; }
              CH.spec.series.push({ ticker: c.ticker, name: c.name, cik: c.cik });
              CH.spec.title = ""; box.value = ""; closeHits(); chChanged();
            };
          });
        }).catch(closeHits);
      }, 180);
    });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape") closeHits(); });
    document.addEventListener("click", function (e) { if (!hits.contains(e.target) && e.target !== box) closeHits(); });
  }

  /* -------------------------------------------------------------- screener
     The Screener tab: a sentence becomes filters (GET /api/v1/us/build/screen),
     shown as chips a reader can remove or add to; the screener endpoint runs
     them and the table sorts in the page. A saved screen keeps its spec and
     the tickers it matched, so a rerun says which companies are new. */
  var SC = { spec: null, chips: [], params: null, rows: [], matched: 0, universe: 0, sortKey: null, sortDir: -1, picked: {},
             prev: null, id: null, name: "", showAll: false, menu: [], calc: {}, seq: 0, saved: [], question: "" };
  var SC_COLS = [
    { key: "mcap", label: "Mkt value, $bn", get: function (r) { return r.metrics.market_cap == null ? null : r.metrics.market_cap / 1e9; }, fmt: function (v) { return v == null ? MISSING : fmtNum(v, v >= 100 ? 0 : 1); } },
    { key: "pe", label: "P/E", get: function (r) { return r.metrics.pe_x; }, fmt: function (v) { return v == null ? MISSING : fmtNum(v, 1) + "×"; } },
    { key: "revg", label: "Rev. growth", get: function (r) { return r.metrics.revenue_growth_pct; }, fmt: pctCell },
    { key: "opm", label: "Op. margin", get: function (r) { return r.metrics.operating_margin_pct; }, fmt: function (v) { return v == null ? MISSING : fmtNum(v, 1) + "%"; } },
    { key: "m1", label: "1 month", get: function (r) { return r.metrics.price_change_1m_pct; }, fmt: pctCell },
    { key: "y1", label: "1 year", get: function (r) { return r.metrics.price_change_1y_pct; }, fmt: pctCell },
    { key: "dy", label: "Div. yield", get: function (r) { return r.metrics.dividend_yield_pct; }, fmt: function (v) { return v == null || v === 0 ? MISSING : fmtNum(v, 2) + "%"; } },
    { key: "year", label: "Year to", get: function (r) { return r.period_end; }, fmt: function (v) { return v ? day(v) : MISSING; } }
  ];
  function pctCell(v) {
    if (v == null) return MISSING;
    return '<span class="' + (v >= 0 ? "ov-up" : "ov-down") + '">' + (v >= 0 ? "+" : MINUS) + fmtNum(Math.abs(v), 1) + "%</span>";
  }
  var SC_SORT_TO_COL = { market_cap: "mcap", pe_x: "pe", revenue_growth_pct: "revg", operating_margin_pct: "opm", price_change_1m_pct: "m1",
                         price_change_1y_pct: "y1", dividend_yield_pct: "dy" };
  function scBusy(on) { $("#sc-go").disabled = on; }
  function scFromWords(q) {
    q = (q || "").trim();
    if (!q) return;
    scBusy(true);
    SC.question = q;
    var mine = ++SC.seq;
    fetch(US + "/build/screen?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      if (mine !== SC.seq) return;
      scBusy(false);
      if (!d || !d.spec) throw new Error();
      SC.prev = null; SC.id = null; SC.name = q; SC.picked = {}; SC.showAll = false;
      SC.spec = d.spec; SC.chips = d.chips; SC.params = d.params; SC.menu = d.menu || SC.menu;
      $("#sc-undo").hidden = true;
      scChips();
      scRun();
    }).catch(function () {
      scBusy(false);
      scEmpty("Plover could not read that. Check your connection, then try again.", true);
    });
  }
  function scRefresh(spec) {
    SC.spec = spec;
    var mine = ++SC.seq;
    fetch(US + "/build/screen/chips?filters=" + encodeURIComponent(JSON.stringify(spec)), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (mine !== SC.seq) return;
        SC.spec = d.spec; SC.chips = d.chips; SC.params = d.params;
        scChips(); scRun();
      }).catch(function () { scEmpty("Plover could not update the filters. Try again in a moment.", true); });
  }
  function scChips() {
    var row = $("#sc-chips-row"), box = $("#sc-chips");
    row.hidden = false;
    var none = SC.chips.every(function (c) { return c.key === "sort"; });
    $(".sc-readas", row).textContent = none ? "Plover read no filters in that, so this is every company. Add a filter, or describe one:" : "Plover read that as:";
    box.innerHTML = SC.chips.map(function (c, i) {
      var editable = c.key !== "sort" && c.key !== "industry" && c.key !== "sp500" && c.key !== "exchanges";
      return '<span class="sc-chip">' + (editable ? '<button type="button" class="sc-chip-edit" data-i="' + i + '" title="Change this filter">' + esc(c.text) + "</button>" : esc(c.text)) +
        (c.key === "sort" ? "" : '<button type="button" class="sc-chip-x" data-i="' + i + '" aria-label="Remove: ' + esc(c.text) + '">×</button>') + "</span>";
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".sc-chip-x"), function (b) {
      b.onclick = function () {
        var c = SC.chips[Number(b.dataset.i)], spec = JSON.parse(JSON.stringify(SC.spec));
        SC.prev = JSON.parse(JSON.stringify(SC.spec));
        if (c.key === "industry") spec.industry = null;
        else if (c.key === "sp500") spec.sp500 = false;
        else if (c.key === "exchanges") spec.exchanges = [];
        else spec.filters = spec.filters.filter(function (f) { return f.key !== c.key; });
        $("#sc-undo").hidden = false;
        scRefresh(spec);
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".sc-chip-edit"), function (b) {
      b.onclick = function () { var c = SC.chips[Number(b.dataset.i)]; scAddForm(c.key, c.value); };
    });
    var sel = $("#sc-addsel");
    sel.innerHTML = '<option value="">+ Add a filter</option>' + SC.menu.map(function (m) { return '<option value="' + esc(m.key) + '">' + esc(m.label.replace(/ (over|under|by at least|at most)$/, "")) + "</option>"; }).join("");
  }
  function scUnitOf(key) { var m = SC.menu.filter(function (x) { return x.key === key; })[0]; return m ? m.unit : "%"; }
  function scAddForm(key, value) {
    var m = SC.menu.filter(function (x) { return x.key === key; })[0];
    if (!m) return;
    var f = $("#sc-addform"); f.hidden = false; f.dataset.key = key;
    $("#sc-addlab").textContent = m.label + (m.unit === "$" ? " ($, e.g. 20bn)" : m.unit === "x" ? " (times)" : " (%)");
    var inp = $("#sc-addval");
    inp.value = value == null ? "" : (m.unit === "$" ? (Math.abs(value) >= 1e9 ? (value / 1e9) + "bn" : (value / 1e6) + "mn") : String(value));
    inp.focus();
  }
  function scParseValue(text, unit) {
    var t = String(text || "").trim().toLowerCase().replace(/[$,%×x]/g, "").replace(/−/g, "-");
    var m = t.match(/^(-?\d+(?:\.\d+)?)\s*(tn|trillion|bn|billion|b|mn|million|m|k)?$/);
    if (!m) return null;
    var v = parseFloat(m[1]);
    if (unit === "$") v *= { tn: 1e12, trillion: 1e12, bn: 1e9, billion: 1e9, b: 1e9, mn: 1e6, million: 1e6, m: 1e6, k: 1e3 }[m[2]] || 1;
    return v;
  }
  function scRun() {
    var mine = ++SC.seq, card = $("#sc-results");
    scEmpty("Finding them…");
    var p = new URLSearchParams(SC.params || {});
    p.set("limit", "1000");
    fetch(US + "/financials/screener?" + p.toString(), { headers: { Accept: "application/json" } }).then(function (r) {
      return r.ok ? r.json() : r.json().catch(function () { return {}; }).then(function (b) { throw new Error(typeof b.detail === "string" ? b.detail : "The screener could not run (" + r.status + ")"); });
    }).then(function (d) {
      if (mine !== SC.seq) return;
      SC.rows = d.rows || []; SC.matched = d.matched || 0; SC.universe = d.matched != null ? d.universe : 0; SC.calc = d.calc || {}; SC.ranked = d.ranked;
      SC.sortKey = SC_SORT_TO_COL[d.sort] || null; SC.sortDir = d.order === "asc" ? 1 : -1;
      $("#sc-empty").hidden = true;
      card.hidden = false;
      scTable();
      if (SC.id) scRecord();
    }).catch(function (e) {
      if (mine !== SC.seq) return;
      scEmpty((e && e.message) || "Plover could not run the screen. Try again in a moment.", true);
    });
  }
  function scEmpty(text, err) {
    $("#sc-results").hidden = true;
    var p = $("#sc-empty"); p.hidden = false; p.className = "my-empty sc-empty" + (err ? " my-err" : ""); p.textContent = text;
  }
  function scSorted() {
    var rows = SC.rows.slice(), col = SC_COLS.filter(function (c) { return c.key === SC.sortKey; })[0];
    if (!col) return rows;
    rows.sort(function (a, b) {
      var x = col.get(a), y = col.get(b);
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * SC.sortDir;
    });
    return rows;
  }
  function scTable() {
    var rows = scSorted(), shown = SC.showAll ? rows : rows.slice(0, 20), n = Object.keys(SC.picked).filter(function (k) { return SC.picked[k]; }).length;
    $("#sc-count").textContent = fmtNum(SC.matched, 0) + (SC.matched === 1 ? " company matches" : " companies match");
    $("#sc-of").textContent = "of " + fmtNum(SC.universe, 0) + " US filers with a current annual report · click a column to sort";
    $("#sc-table").innerHTML = "<thead><tr><th><span class=\"my-sr\">Pick</span></th><th>Company</th><th>Industry</th>" +
      SC_COLS.map(function (c) {
        var on = c.key === SC.sortKey;
        return '<th class="num" aria-sort="' + (on ? (SC.sortDir < 0 ? "descending" : "ascending") : "none") + '"><button type="button" data-k="' + c.key + '">' + esc(c.label) + (on ? (SC.sortDir < 0 ? " ↓" : " ↑") : "") + "</button></th>";
      }).join("") + "</tr></thead><tbody>" +
      (shown.length ? shown.map(function (r) {
        var t = r.ticker || "";
        return "<tr><td><input type=\"checkbox\" class=\"sc-pick\" data-t=\"" + esc(t) + "\"" + (SC.picked[t] ? " checked" : "") + ' aria-label="Pick ' + esc(r.display_name || r.name) + ' to chart"></td>' +
          '<td class="ov-co"><a href="us-stock.html?t=' + encodeURIComponent(t) + '"><span class="ov-name">' + (r.logo && typeof usLogos !== "undefined" ? usLogos.img(r.sec_code, 18) : "") + esc(r.display_name || r.name) + '</span><span class="ov-meta">' + esc(t) + "</span></a></td>" +
          '<td class="sc-ind">' + esc(titleCase(r.industry || "")) + "</td>" +
          SC_COLS.map(function (c) { var v = c.get(r), h = c.fmt(v); return '<td class="num' + (v == null ? " na" : "") + '">' + h + "</td>"; }).join("") + "</tr>";
      }).join("") : '<tr><td colspan="11" class="my-empty">No company matches every filter. Remove one above.</td></tr>') + "</tbody>";
    Array.prototype.forEach.call($("#sc-table").querySelectorAll("th button"), function (b) {
      b.onclick = function () {
        var k = b.dataset.k;
        if (SC.sortKey === k) SC.sortDir = -SC.sortDir; else { SC.sortKey = k; SC.sortDir = (k === "pe" || k === "year") ? 1 : -1; }
        scTable();
      };
    });
    Array.prototype.forEach.call($("#sc-table").querySelectorAll(".sc-pick"), function (c) {
      c.onchange = function () { SC.picked[c.dataset.t] = c.checked; scTable(); };
    });
    var more = $("#sc-more");
    more.hidden = SC.showAll || rows.length <= 20;
    more.textContent = "Show all " + fmtNum(rows.length, 0);
    $("#sc-chart").textContent = n ? "Chart " + n + " Picked" : "Chart the Top 5";
    $("#sc-save").textContent = SC.id ? "Saved" : "Save Screen";
    var priced = SC.rows.length && SC.rows[0].price ? day(SC.rows[0].price.date) : null;
    $("#sc-note").textContent = "Ratios from each company's latest annual report (the Year to column)" + (priced ? "; prices are the " + priced + " closes" : "") +
      ". P/E = close ÷ diluted EPS in that report. A bank or insurer shows — for operating margin: it reports no operating income." +
      (SC.ranked != null && SC.ranked < SC.matched ? " " + fmtNum(SC.matched - SC.ranked, 0) + " matching companies have no figure to sort on and are left out." : "") +
      " Source: SEC EDGAR, Databento.";
    var keys = ["market_cap", "pe_x", "revenue_growth_pct", "operating_margin_pct", "price_change_1m_pct", "price_change_1y_pct", "dividend_yield_pct"];
    $("#sc-calc").innerHTML = keys.filter(function (k) { return SC.calc[k]; }).map(function (k) { return "<li><strong>" + esc(k.replace(/_pct$|_x$/, "").replace(/_/g, " ")) + "</strong>: " + esc(SC.calc[k]) + "</li>"; }).join("");
    SC.lastQs = "f=" + encodeURIComponent(JSON.stringify(SC.spec));
    history.replaceState(null, "", "#screener?" + SC.lastQs);
  }
  function titleCase(s) {
    return s.toLowerCase().replace(/(^|[\s\-&(/])([a-z])/g, function (m, a, b) { return a + b.toUpperCase(); }).replace(/\bAnd\b/g, "and").replace(/\bOf\b/g, "of").replace(/\bNec\b/, "n.e.c.");
  }
  function scRecord() {
    // a saved screen that just ran: keep its matches, so the next run can say what is new
    var tickers = SC.rows.map(function (r) { return r.ticker; }).filter(Boolean);
    api("POST", A + "/screens", { name: SC.name, spec: SC.spec, tickers: tickers, id: SC.id }).then(function (r) {
      if (r.ok) { SC.saved = r.body.screens; scSaved(); }
    });
  }
  function scSaved() {
    var ul = $("#sc-saved");
    ul.innerHTML = SC.saved.length ? SC.saved.map(function (s) {
      var n = s.last_n == null ? "" : fmtNum(s.last_n, 0) + " now";
      var nw = s.new_n == null ? "" : (s.new_n ? '<span class="sc-new">' + fmtNum(s.new_n, 0) + " new since last run</span>" : "none new");
      return '<li><button type="button" class="my-link my-name sc-open" data-id="' + s.id + '">' + esc(s.name) + '</button><span class="sc-saved-n">' + n + (n && nw ? " · " : "") + nw + "</span>" +
        '<span class="my-acts"><button type="button" class="my-link sc-open" data-id="' + s.id + '">Run</button><button type="button" class="my-link sc-del" data-id="' + s.id + '">Delete</button></span></li>';
    }).join("") : '<li class="my-empty">No saved screens yet. Run one and click Save Screen.</li>';
    $("#my-sc-n").textContent = SC.saved.length ? String(SC.saved.length) : "";
    Array.prototype.forEach.call(ul.querySelectorAll(".sc-open"), function (b) {
      b.onclick = function () {
        var s = SC.saved.filter(function (x) { return x.id === Number(b.dataset.id); })[0];
        if (!s) return;
        SC.id = s.id; SC.name = s.name; SC.picked = {}; SC.showAll = false; SC.prev = null; $("#sc-undo").hidden = true;
        $("#sc-q").value = s.name;
        scRefresh(s.spec);
        window.scrollTo({ top: 0, behavior: "smooth" });
      };
    });
    Array.prototype.forEach.call(ul.querySelectorAll(".sc-del"), function (b) {
      b.onclick = function () {
        var id = Number(b.dataset.id), gone = SC.saved.filter(function (x) { return x.id === id; })[0];
        SC.saved = SC.saved.filter(function (x) { return x.id !== id; });
        if (SC.id === id) { SC.id = null; $("#sc-save").textContent = "Save Screen"; }
        scSaved();
        toast("Screen deleted.", function () { SC.saved.push(gone); scSaved(); }, function () {
          api("DELETE", A + "/screens/" + id).then(function (r) { if (r.ok) { SC.saved = r.body.screens; scSaved(); } });
        });
      };
    });
  }
  function loadSavedScreens() {
    return api("GET", A + "/screens").then(function (r) { if (r.ok) { SC.saved = r.body.screens || []; scSaved(); } });
  }
  function scCsv() {
    var rows = scSorted();
    var head = ["Plover Analytics — screen: " + (SC.name || SC.question), "Filters: " + SC.chips.map(function (c) { return c.text; }).join("; "),
                $("#sc-note").textContent, "Downloaded " + iso(new Date())].map(function (l) { return "# " + l.replace(/\r?\n/g, " "); }).join("\n") + "\n";
    var cols = ["ticker", "company", "industry", "market_value_usd", "pe_x", "revenue_growth_pct", "operating_margin_pct", "price_change_1m_pct", "price_change_1y_pct", "dividend_yield_pct", "year_to", "source_filing"];
    var csv = head + cols.join(",") + "\n" + rows.map(function (r) {
      var m = r.metrics, q = function (s) { return '"' + String(s == null ? "" : s).replace(/"/g, '""') + '"'; }, v = function (x) { return x == null ? "" : x; };
      return [q(r.ticker), q(r.display_name || r.name), q(r.industry), v(m.market_cap), v(m.pe_x), v(m.revenue_growth_pct), v(m.operating_margin_pct), v(m.price_change_1m_pct), v(m.price_change_1y_pct), v(m.dividend_yield_pct), v(r.period_end), q(r.source_url)].join(",");
    }).join("\n") + "\n";
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "plover-screen.csv"; a.click();
    URL.revokeObjectURL(a.href);
  }
  function setupScreener() {
    $("#sc-ask").addEventListener("submit", function (e) { e.preventDefault(); scFromWords($("#sc-q").value); });
    Array.prototype.forEach.call(document.querySelectorAll(".sc-hint"), function (b) {
      b.onclick = function () {
        // a change in words applies to the screen on screen: the words are read as a whole with the current question
        var q = SC.question ? SC.question + ", " + b.textContent : b.textContent;
        $("#sc-q").value = q; scFromWords(q);
      };
    });
    $("#sc-undo").addEventListener("click", function () {
      if (!SC.prev) return;
      var back = SC.prev; SC.prev = null; $("#sc-undo").hidden = true; scRefresh(back);
    });
    $("#sc-addsel").addEventListener("change", function () { if (this.value) scAddForm(this.value, null); this.value = ""; });
    $("#sc-addcancel").addEventListener("click", function () { $("#sc-addform").hidden = true; });
    $("#sc-addform").addEventListener("submit", function (e) {
      e.preventDefault();
      var key = this.dataset.key, v = scParseValue($("#sc-addval").value, scUnitOf(key));
      if (v == null) { toast("Type a number, e.g. 20bn, 18 or 5."); return; }
      if (!SC.spec) SC.spec = { filters: [], industry: null, sp500: false, exchanges: [], sort_key: null, sort_order: null };
      SC.prev = JSON.parse(JSON.stringify(SC.spec));
      var spec = JSON.parse(JSON.stringify(SC.spec));
      spec.filters = spec.filters.filter(function (f) { return f.key !== key; }).concat([{ key: key, value: v }]);
      $("#sc-addform").hidden = true; $("#sc-undo").hidden = false;
      scRefresh(spec);
    });
    $("#sc-more").addEventListener("click", function () { SC.showAll = true; scTable(); });
    $("#sc-chart").addEventListener("click", function () {
      var picked = Object.keys(SC.picked).filter(function (k) { return SC.picked[k]; });
      var rows = picked.length ? SC.rows.filter(function (r) { return SC.picked[r.ticker]; }) : scSorted().slice(0, 5);
      if (!rows.length) return;
      chTo(rows.map(function (r) { return r.ticker; }), rows.map(function (r) { return r.display_name || r.name; }));
    });
    $("#sc-csv").addEventListener("click", function () { if (SC.rows) scCsv(); });
    $("#sc-link").addEventListener("click", function () { if (SC.spec) chCopy(location.origin + "/my.html#screener?f=" + encodeURIComponent(JSON.stringify(SC.spec))); });
    $("#sc-save").addEventListener("click", function () {
      if (!SC.spec) { toast("Run a screen first."); return; }
      var btn = this; btn.disabled = true;
      var tickers = SC.rows.map(function (r) { return r.ticker; }).filter(Boolean);
      api("POST", A + "/screens", { name: SC.name || SC.question || "Screen", spec: SC.spec, tickers: tickers, id: SC.id }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { toast(detail(r, "The screen could not be saved")); return; }
        SC.id = r.body.screen.id; SC.saved = r.body.screens; scSaved(); btn.textContent = "Saved";
        toast("Saved as “" + (SC.name || SC.question) + "”. Open it from My Screens to run it again.");
      });
    });
  }
  function scFromHash(qs) {
    var p = new URLSearchParams(qs);
    if (p.get("q")) { $("#sc-q").value = p.get("q"); scFromWords(p.get("q")); return true; }
    if (p.get("f")) {
      try {
        var spec = JSON.parse(p.get("f"));
        if (spec && typeof spec === "object") { SC.id = null; SC.name = SC.name || "Screen from a link"; scRefresh(spec); return true; }
      } catch (e) { /* not a spec */ }
    }
    return false;
  }

  /* ----------------------------------------------------------------- tabs
     The address names the tab (and the chat), so a reload or a shared link
     lands on the same view: #chat, #chat/12, #companies, #reading, #key. */
  var TABS = ["overview", "chat", "charts", "screener", "companies", "reading", "key"];
  var OLD = { "ai-key": "key", answers: "chat", "": "overview" };
  function route() {
    // #charts?s=AAPL,MSFT&m=price… and #screener?f=… carry the view after a "?"
    var h = location.hash.replace(/^#/, ""), qi = h.indexOf("?"), qs = qi >= 0 ? h.slice(qi + 1) : "";
    var parts = (qi >= 0 ? h.slice(0, qi) : h).split("/");
    var tab = OLD[parts[0]] || parts[0];
    if (TABS.indexOf(tab) < 0 || (tab === "reading" && !READING_ON)) tab = "overview";
    TABS.forEach(function (t) {
      var on = t === tab, a = $("#tab-" + t);
      a.classList.toggle("is-on", on);
      a.setAttribute("aria-selected", on ? "true" : "false");
      $("#panel-" + t).hidden = !on;
    });
    if (tab === "chat") {
      var id = Number(parts[1]);
      if (id) openChat(id);
      else if (chat.study) newChat();
      $("#my-chats").classList.remove("is-open");
      $("#my-side-toggle").setAttribute("aria-expanded", "false");
    }
    if (tab === "key") { var k = $("#my-key-in"); if (k && !k.disabled) k.focus(); }
    if (tab === "charts") {
      var sp = qs ? chFromHash(qs) : null;
      if (sp && (!CH.spec || chHash(sp) !== chHash(CH.spec))) chSetSpec(sp);
      else if (CH.chart && !$("#ch-plot").hidden) CH.chart.render();   // drawn while hidden: size it now
    }
    if (tab === "screener" && qs && qs !== SC.lastQs) { SC.lastQs = qs; scFromHash(qs); }
  }

  /* ---------------------------------------------------------------- start */
  function gate(html) { $("#my-gate").innerHTML = html; $("#my-gate").hidden = false; $("#my-app").hidden = true; }

  function start() {
    if (typeof initThemeToggle === "function") {
      initThemeToggle(redrawCharts);
    }
    // a question from the front page (index.html: my.html?q=…), kept through sign-in
    var asked = (new URLSearchParams(location.search).get("q") || "").trim().slice(0, 600);
    if (asked) history.replaceState(null, "", "my.html" + location.hash);
    api("GET", A + "/status").then(function (r) {
      if (r.status === 404) {
        gate("<p>Plover accounts are not switched on for this server yet. The data pages are free to read without an account.</p>");
        return;
      }
      if (!r.ok) { gate('<p class="my-err">' + esc(detail(r, "This page could not be loaded")) + ' <a href="my.html">Try again</a>.</p>'); return; }
      if (!r.body.signed_in) {
        var back = "my.html" + (asked ? "?q=" + encodeURIComponent(asked) : "");
        gate((asked ? '<p class="my-asked-out">Your question: <strong>' + esc(asked) + "</strong></p>" +
            "<p>Sign in to get the answer. It comes straight back to this question.</p>"
          : "<p>Sign in to ask questions about stocks and the economy, and keep a list of the companies you follow.</p>") +
          '<p><a class="btn btn-primary" href="signin.html?returnTo=' + encodeURIComponent(back) + '">Sign In</a></p>');
        return;
      }
      $("#my-gate").hidden = true;
      $("#my-app").hidden = false;
      $("#my-tries").innerHTML = EXAMPLES.map(function (g) {
        return '<li class="my-tries-group">' + esc(g[0]) + "</li>" + g[1].map(function (q) {
          return '<li><button type="button" class="my-try">' + esc(q) + "</button></li>";
        }).join("");
      }).join("");
      Array.prototype.forEach.call(document.querySelectorAll(".my-try"), function (b) {
        b.onclick = function () { send(b.textContent); };
      });
      var box = $("#my-q");
      $("#my-ask").addEventListener("submit", function (e) { e.preventDefault(); send(box.value); });
      box.addEventListener("input", grow);
      box.addEventListener("keydown", function (e) {
        // Enter sends, Shift+Enter starts a new line, as in any chat
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(box.value); }
      });
      $("#my-new").addEventListener("click", function (e) {
        e.preventDefault();
        history.pushState(null, "", "#chat");
        newChat(true);
        route();
      });
      $("#my-side-toggle").addEventListener("click", function () {
        var open = !$("#my-chats").classList.contains("is-open");
        $("#my-chats").classList.toggle("is-open", open);
        this.setAttribute("aria-expanded", open ? "true" : "false");
      });
      window.addEventListener("hashchange", route);
      setupCompanySearch();
      setupBookmark();
      setupCharts();
      setupScreener();
      $("#ov-compare").addEventListener("click", function () {
        // the watchlist's US companies, charted against the S&P 500
        var rows = Array.prototype.slice.call(document.querySelectorAll("#ov-wl tr[data-tk]"));
        var tks = rows.map(function (r) { return r.dataset.tk; }).filter(Boolean);
        if (!tks.length) { toast("Add a US company to the watchlist first."); return; }
        chTo(tks, rows.map(function (r) { return r.dataset.name || r.dataset.tk; }));
      });
      // the Overview's box asks in a new chat
      $("#ov-ask").addEventListener("submit", function (e) {
        e.preventDefault();
        var q = $("#ov-q").value.trim();
        if (!q) return;
        $("#ov-q").value = "";
        history.pushState(null, "", "#chat");
        newChat();
        route();
        send(q);
      });
      newChat();
      route();
      loadKey().then(function () {
        if (asked) {
          history.replaceState(null, "", "#chat");
          route();
          send(asked);
        }
      });
      loadChats(); loadCompanies(); loadSavedCharts(); loadSavedScreens();
      if (READING_ON) { $("#tab-reading").hidden = false; loadReading(); }
    }).catch(function () {
      gate('<p class="my-err">Plover could not be reached. Check your connection, then <a href="my.html">try again</a>.</p>');
    });
  }

  start();
})();
