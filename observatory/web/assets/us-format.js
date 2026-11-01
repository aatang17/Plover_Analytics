/* US figures as words a reader can use: every number with its unit, tables
   whose cells guess their unit from the column header, and the sources block.
   Shared by the ask box (us-stocks.js), a company's Price and Insiders and
   holders page (us-stock.js) and the data pages' question box (page-ask.js),
   so they never format one figure two ways.
   Needs format.js (fmtNum, MISSING, MINUS, MONTHS, escapeHtml); drawing a chart
   needs charts.js. */
(function () {
  "use strict";

  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  /* ---- formatting: every number carries its unit ---------------------- */
  function money(v, dp) {
    if (v == null) return MISSING;
    var a = Math.abs(v), s;
    if (a >= 1e12) s = fmtNum(a / 1e12, 2) + "tn";
    else if (a >= 1e11) s = fmtNum(a / 1e9, 0) + "bn";
    else if (a >= 1e9) s = fmtNum(a / 1e9, 1) + "bn";
    else if (a >= 1e6) s = fmtNum(a / 1e6, 1) + "mn";
    else if (a >= 1e3) s = fmtNum(a, 0);
    else s = fmtNum(a, dp == null ? 2 : dp);
    return (v < 0 ? MINUS : "") + "$" + s;
  }
  function pct(v, dp, signed) {
    if (v == null) return MISSING;
    var s = fmtNum(Math.abs(v), dp == null ? 1 : dp) + "%";
    if (v < 0) return MINUS + s;
    return (signed && v > 0 ? "+" : "") + s;
  }
  function mult(v) { return v == null ? MISSING : fmtNum(v, 1) + "×"; }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function dateWords(iso) {
    if (!iso) return MISSING;
    var d = new Date(iso.slice(0, 10) + "T00:00:00Z");
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
  }
  function fmtBy(unit, v, dp) {
    if (v == null) return MISSING;
    if (unit === "USD") return money(v, dp);
    if (unit === "%") return pct(v, dp == null ? 1 : dp, false);
    if (unit === "×") return mult(v);
    if (unit === "count" || unit === "shares") return count(v);
    if (unit === "date") return dateWords(v);
    if (typeof v === "number") return fmtNum(v, dp == null ? 2 : dp);
    return esc(v);
  }
  // Table cells: the column header says what the column is; the cell guesses
  // its unit from the header words so a reader never sees a bare 5074023879600.
  function cellFor(col, v, unit) {
    if (v == null || v === "") return "<td class='r'>" + MISSING + "</td>";
    if (typeof v === "boolean") return "<td>" + (v ? "Yes" : "No") + "</td>";
    if (typeof v === "number") {
      var c = col.toLowerCase();
      if (/\(usd\)/.test(c)) return "<td class='r'>" + money(v) + "</td>";
      // a change counted in shares is a number of shares, not a percent ("Change in shares": +16,385,212)
      if (/change/.test(c) && /shares/.test(c) && !/%/.test(c)) return "<td class='r'>" + (v > 0 ? "+" : v < 0 ? MINUS : "") + fmtNum(Math.abs(v), 0) + "</td>";
      if (/change/.test(c) && !/close|price/.test(c)) return "<td class='r'>" + pct(v, 1, true) + "</td>";
      if (/\(\u00d7\)/.test(c)) return "<td class='r'>" + mult(v) + "</td>";
      if (/\(pp\)/.test(c)) return "<td class='r'>" + (v > 0 ? "+" : v < 0 ? "\u2212" : "") + fmtNum(Math.abs(v), 2) + " pp</td>";
      if (/share of|%|change %|pct|margin|yield|roe|growth|ratio/.test(c) && !/shares|rank/.test(c)) return "<td class='r'>" + pct(v, 1, /change/.test(c)) + "</td>";
      if (/p\/e/.test(c)) return "<td class='r'>" + mult(v) + "</td>";
      if (/rank|holders|insiders|count|trades|positions|days/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/shares|volume/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/price|close/.test(c) && !/change/.test(c)) return "<td class='r'>" + money(v, 2) + "</td>";
      if (/value|revenue|income|assets|equity|cap|buyback|dividend|net|cash|fcf|both|total|prior/.test(c) || unit === "USD") return "<td class='r'>" + money(v) + "</td>";
      return "<td class='r'>" + fmtNum(v, Math.abs(v) < 100 ? 2 : 0) + "</td>";
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return "<td>" + dateWords(v) + "</td>";
    if (/^\d{4}-\d{2}$/.test(String(v))) return "<td>" + esc(v) + "</td>";
    var s = String(v);
    if (/^cik:|^\d{10}-\d{2}-\d{6}$/.test(s)) return "<td class='quiet'>" + esc(s) + "</td>";
    if (/^[A-Z][A-Z0-9.-]{0,9}$/.test(s) && /ticker/.test(col.toLowerCase())) return "<td>" + (window.usLogos ? usLogos.img(s, 16) : "") + "<a href='/us-stock.html?t=" + encodeURIComponent(s) + "'>" + esc(s) + "</a></td>";
    return "<td class='txt'>" + esc(s.length > 60 ? s.slice(0, 57) + "…" : s) + "</td>";
  }

  function tableHtml(t, id) {
    if (!t || !t.rows || !t.rows.length) return "";
    var head = "<thead><tr>" + t.columns.map(function (c, i) {
      var numeric = t.rows.some(function (r) { return typeof r[i] === "number"; });
      return "<th" + (numeric ? " class='r'" : "") + ">" + esc(c) + "</th>";
    }).join("") + "</tr></thead>";
    var body = "<tbody>" + t.rows.map(function (r) {
      return "<tr class='hit'>" + r.map(function (v, i) { return cellFor(t.columns[i], v, t.unit); }).join("") + "</tr>";
    }).join("") + "</tbody>";
    return "<div class='table-wrap'><table class='tbl-fin' id='" + id + "'>" + head + body + "</table></div>";
  }

  function sourcesHtml(srcs, calc, entities, match) {
    var items = (srcs || []).map(function (s) {
      return "<li><b>" + esc(s.name) + ".</b> " + esc(s.credit) + (s.note ? " " + esc(s.note) : "") + "</li>";
    });
    var calcItems = Object.keys(calc || {}).filter(function (k) { return k !== "query" && k !== "steps"; }).map(function (k) {
      return "<li><b>" + esc(k.replace(/_/g, " ")) + ":</b> " + esc(calc[k]) + "</li>";
    });
    var read = [];
    if (entities) {
      if (entities.mentioned_companies && entities.mentioned_companies.length) {
        read.push("company: " + entities.mentioned_companies.map(function (c) { return c.name + " (" + (c.ticker || "cik " + c.cik) + ", by " + c.matched_by + ")"; }).join("; "));
      }
      if (entities.industry_read) read.push("industry: " + entities.industry_read);
      if (entities.manager_read) read.push("manager: " + entities.manager_read);
      if (entities.since_read) read.push("since: " + dateWords(entities.since_read));
      if (entities.amount_read) read.push("amount: " + money(entities.amount_read));
    }
    return "<details class='calc'><summary>Sources and formulas</summary><div class='calc-body'>" +
      (match ? "<p class='quiet'>Question shape: " + esc(match.label) + ". " + (read.length ? "Read as — " + esc(read.join("; ")) + "." : "") + "</p>" : "") +
      (calcItems.length ? "<ul class='sources'>" + calcItems.join("") + "</ul>" : "") +
      (calc && calc.query ? "<p class='quiet'>The query run on the data:</p><pre class='sql'>" + esc(calc.query) + "</pre>" : "") +
      "<ul class='sources'>" + items.join("") + "</ul></div></details>";
  }

  // A preview's figure: the server's unit, as many decimals as it sent.
  function previewValue(f) {
    var v = f.value, u = f.unit || "";
    if (v == null) return MISSING;
    if (u === "USD" || u === "$") return money(v);
    // as many decimals as the server sent (1 for a rate, 2 for a yield), so a figure matches its headline
    if (u === "%" || u === "pct" || u === "percent") return pct(v, f.dp != null ? f.dp : Math.min(2, Math.max(1, (String(v).split(".")[1] || "").length)));
    if (u === "count") return fmtNum(v, 0);
    if (u === "x" || u === "ratio" || u === "\u00d7") return mult(v);
    return (typeof v === "number" ? fmtNum(v, Math.abs(v) < 100 ? 2 : 0) : esc(v)) + (u ? " " + esc(u) : "");
  }

  /* ---- the ask box's answers: the front page and the data pages' boxes ---- */
  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          var e = new Error(typeof b.detail === "string" ? b.detail : "");
          e.status = r.status;
          e.miss = b.miss;            // what kind of miss, and questions that do answer (app/ask_miss.py)
          e.tries = b.try;
          throw e;
        });
      }
      return r.json();
    });
  }

  // The answer as a stream (/api/v1/us/ask/stream): onSay(text) hears each
  // step as the server takes it (question read, company found, steps written,
  // a check that sent it back), then the promise settles like getJSON's. A
  // server without the stream answers through the plain endpoint.
  function askStream(path, onSay) {
    if (!window.ReadableStream || !window.TextDecoder) return getJSON("/api/v1/us" + path);
    return fetch("/api/v1/us" + path.replace(/^\/ask/, "/ask/stream")).then(function (r) {
      if (r.status === 404 || !r.body) return getJSON("/api/v1/us" + path);
      if (!r.ok) return getJSON("/api/v1/us" + path);
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      return new Promise(function (resolve, reject) {
        function pump() {
          reader.read().then(function (chunk) {
            if (chunk.done) { reject(new Error("The answer was cut off. Ask again.")); return; }
            buf += dec.decode(chunk.value, { stream: true });
            var cut;
            while ((cut = buf.indexOf("\n\n")) >= 0) {
              var line = buf.slice(0, cut).replace(/^data: ?/, "");
              buf = buf.slice(cut + 2);
              var ev;
              try { ev = JSON.parse(line); } catch (e) { continue; }
              if (ev.say) onSay(ev.say);
              else if (ev.answer) { resolve(ev.answer); return; }
              else if (ev.error) {
                var err = new Error(ev.error.detail || "");
                err.status = ev.error.status;
                err.miss = ev.error.miss;
                err.tries = ev.error.try;
                reject(err);
                return;
              }
            }
            pump();
          }, reject);
        }
        pump();
      });
    });
  }

  // Draws an answer's chart; a second chart (two units are never one axis) goes to keep(chart).
  function drawChart(el, chart, sourceLine, keep) {
    if (!chart || !chart.series || !chart.series.length) return null;
    // a column chart's points are plain values, one per category; other charts' are [x, value]
    var hasPts = chart.series.some(function (s) {
      return s.points && s.points.some(function (p) { return chart.kind === "cols" ? p != null : p[1] != null; });
    });
    if (!hasPts) return null;
    var unit = chart.unit || "";
    var isUsd = /USD/.test(unit);
    // columns per period ("Sold" by month): a dollar axis in $mn or $bn
    if (chart.kind === "cols") {
      var top = 0;
      chart.series.forEach(function (s) { s.points.forEach(function (v) { top = Math.max(top, Math.abs(v || 0)); }); });
      var sc = isUsd ? (top >= 1e9 ? 1e9 : top >= 1e6 ? 1e6 : 1) : 1;
      var sfx = isUsd ? (sc === 1e9 ? "$bn" : sc === 1e6 ? "$mn" : "$") : unit;
      return obsChart(el, "cols", { categories: chart.categories, dp: 1, unitSuffix: sfx, yAxisName: sfx, trust: "derived", sourceLine: sourceLine,
        series: chart.series.map(function (s, i) { return { name: s.name, slot: i + 1, points: s.points.map(function (v) { return v == null ? null : v / sc; }) }; }) });
    }
    if (chart.kind === "bars") {
      var pts = chart.series[0].points;
      var scale = 1, suffix = unit;
      if (isUsd) {
        var mx = Math.max.apply(null, pts.map(function (p) { return Math.abs(p[1] || 0); }));
        scale = mx >= 1e9 ? 1e9 : mx >= 1e6 ? 1e6 : 1;
        suffix = scale === 1e9 ? "$bn" : scale === 1e6 ? "$mn" : "$";
      }
      var items = pts.map(function (p) { return { name: String(p[0]), value: p[1] == null ? null : p[1] / scale }; });
      var big = Math.max.apply(null, items.map(function (i) { return Math.abs(i.value || 0); })) >= 100;
      return obsChart(el, "rank", { items: items, unit: isUsd ? suffix.replace("$", "") + " USD" : suffix, dp: unit === "pp" ? 2 : big ? 0 : 1, trust: "derived",
        sourceLine: sourceLine, rows: items, columns: [{ key: "name", label: "name" }, { key: "value", label: "value (" + suffix + ")" }] });
    }
    var usdLine = isUsd && chart.series.every(function (s) { return !s.axis || s.axis === "left"; });
    var cfg = {
      unit: "", unitSuffix: usdLine ? " $" : (unit === "%" ? "%" : ""), dp: 2, trust: "official", sourceLine: sourceLine,
      yAxisName: usdLine ? "USD" : (unit || ""), isoPeriods: true,
      series: chart.series.map(function (s) { return { name: s.name, points: s.points.map(function (p) { return [p[0], p[1]]; }) }; }),
    };
    // Two series on different units (bank profits and a yield) are two small
    // charts, never one axis — a rate and a dollar amount never share a scale.
    if (chart.series.some(function (s) { return s.axis === "right"; })) {
      cfg.xType = "category";
      cfg.series = [cfg.series[0]];
      cfg.unitSuffix = ""; cfg.yAxisName = chart.series[0].name;
      var c1 = obsChart(el, "line", cfg);
      var el2 = document.createElement("div");
      el2.className = "chart-el"; el2.style.height = "260px";
      el.parentElement.insertBefore(el2, el.nextSibling);
      var right = chart.series.filter(function (s) { return s.axis === "right"; })[0];
      var c2 = obsChart(el2, "line", { xType: "category", unit: "%", dp: 2, trust: "official", sourceLine: sourceLine,
        yAxisName: right.name, series: [{ name: right.name, points: right.points }] });
      if (keep) keep(c2);
      return c1;
    }
    if (chart.series[0].points.length && /^\d{4}$/.test(String(chart.series[0].points[0][0]))) cfg.xType = "category";
    return obsChart(el, "line", cfg);
  }

  window.usFmt = { esc: esc, money: money, pct: pct, mult: mult, count: count, dateWords: dateWords,
                   fmtBy: fmtBy, cellFor: cellFor, tableHtml: tableHtml, sourcesHtml: sourcesHtml,
                   previewValue: previewValue, getJSON: getJSON, askStream: askStream, drawChart: drawChart };
})();
