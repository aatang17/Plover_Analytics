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
          '<span class="my-name">' + esc(c.name || c.name_ja || c.sec_code) + "</span>" +
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
          return '<li><button type="button" data-i="' + i + '"><span>' + esc(c.name_en || c.name_ja) + "</span>" +
            '<span class="my-meta">' + (c.market === "US" ? esc(c.ticker) + (c.exchange ? " · " + esc(c.exchange) : "") : esc(c.code) + " · Japan") +
            (c.covered || state.covered[c.code] ? " · on your list" : "") + "</span></button></li>";
        }).join("");
        hits.hidden = false;
        Array.prototype.forEach.call(hits.querySelectorAll("button"), function (b) {
          b.onclick = function () { add(found[Number(b.getAttribute("data-i"))]); };
        });
      });
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 180); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); if (found.length) add(found[0]); else search(); }
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

  /* ----------------------------------------------------------------- tabs
     The address names the tab (and the chat), so a reload or a shared link
     lands on the same view: #chat, #chat/12, #companies, #reading, #key. */
  var TABS = ["overview", "chat", "companies", "reading", "key"];
  var OLD = { "ai-key": "key", answers: "chat", "": "overview" };
  function route() {
    var h = location.hash.replace(/^#/, ""), parts = h.split("/");
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
      loadChats(); loadCompanies();
      if (READING_ON) { $("#tab-reading").hidden = false; loadReading(); }
    }).catch(function () {
      gate('<p class="my-err">Plover could not be reached. Check your connection, then <a href="my.html">try again</a>.</p>');
    });
  }

  start();
})();
