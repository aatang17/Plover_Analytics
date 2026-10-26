/* US Stocks: the box, and one company's page behind ?t=TICKER.
   Data: /api/v1/us/ask (app/us_ask_api.py), /api/v1/us/financials/metrics,
   /api/v1/us/prices, /api/v1/us/insiders, /api/v1/us/holdings,
   /api/v1/us/earnings (the modules under app/).

   The box sends the typed question to the server, which matches it to a
   question shape and answers by code — no model. The page draws what comes
   back: a headline, the sentences, the few figures, a chart or a table, the
   sources and the formula behind every computed number. The URL carries the
   question (?q=) or the ticker (?t=), so any answer is a link.

   Filed values are as the SEC extracted them; closes as the vendor delivered
   them; every calculated figure names its formula. Missing is —, never 0. */
(function () {
  "use strict";

  var API = "/api/v1/us";
  var charts = [];
  var lastAsked = "";

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

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
    if (!window.ReadableStream || !window.TextDecoder) return getJSON(API + path);
    return fetch(API + path.replace(/^\/ask/, "/ask/stream")).then(function (r) {
      if (r.status === 404 || !r.body) return getJSON(API + path);
      if (!r.ok) return getJSON(API + path);
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
      if (/rank|holders|count|trades|positions|days/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/shares|volume/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/price|close/.test(c) && !/change/.test(c)) return "<td class='r'>" + money(v, 2) + "</td>";
      if (/value|revenue|income|assets|equity|cap|buyback|dividend|net|cash|fcf|both|total|prior/.test(c) || unit === "USD") return "<td class='r'>" + money(v) + "</td>";
      return "<td class='r'>" + fmtNum(v, Math.abs(v) < 100 ? 2 : 0) + "</td>";
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return "<td>" + dateWords(v) + "</td>";
    if (/^\d{4}-\d{2}$/.test(String(v))) return "<td>" + esc(v) + "</td>";
    var s = String(v);
    if (/^cik:|^\d{10}-\d{2}-\d{6}$/.test(s)) return "<td class='quiet'>" + esc(s) + "</td>";
    if (/^[A-Z][A-Z0-9.-]{0,9}$/.test(s) && /ticker/.test(col.toLowerCase())) return "<td>" + (window.usLogos ? usLogos.img(s, 16) : "") + "<a href='/?t=" + encodeURIComponent(s) + "'>" + esc(s) + "</a></td>";
    return "<td class='txt'>" + esc(s.length > 60 ? s.slice(0, 57) + "…" : s) + "</td>";
  }

  function disposeCharts() {
    charts.forEach(function (c) { try { c.dispose && c.dispose(); } catch (e) { /* gone */ } });
    charts = [];
  }

  /* ---- pieces ---------------------------------------------------------- */
  function figuresHtml(figs) {
    var cells = (figs || []).filter(function (f) { return f && (f.value !== undefined); }).map(function (f) {
      var qual = f.as_of && f.unit !== "date" ? "<small>as of " + dateWords(f.as_of) + "</small>" : "";
      var title = f.formula ? " title='" + esc(f.formula) + "'" : "";
      return "<div class='hit'" + title + "><dt>" + esc(f.label) + "</dt><dd>" + fmtBy(f.unit, f.value) + qual + "</dd></div>";
    });
    return cells.length ? "<section class='hp-panel'><dl class='an-figs'>" + cells.join("") + "</dl></section>" : "";
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

  function drawChart(el, chart, sourceLine) {
    if (!chart || !chart.series || !chart.series.length) return null;
    var hasPts = chart.series.some(function (s) { return s.points && s.points.some(function (p) { return p[1] != null; }); });
    if (!hasPts) return null;
    var unit = chart.unit || "";
    var isUsd = /USD/.test(unit);
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
      charts.push(obsChart(el2, "line", { xType: "category", unit: "%", dp: 2, trust: "official", sourceLine: sourceLine,
        yAxisName: right.name, series: [{ name: right.name, points: right.points }] }));
      return c1;
    }
    if (chart.series[0].points.length && /^\d{4}$/.test(String(chart.series[0].points[0][0]))) cfg.xType = "category";
    return obsChart(el, "line", cfg);
  }

  function followupsFor(a) {
    var out = [];
    var co = a.entities && a.entities.company;
    var cos = (a.entities && a.entities.mentioned_companies) || [];
    var name = co ? (co.ticker || co.name) : (cos[0] ? (cos[0].ticker || cos[0].name) : null);
    var mk = function (label, q) { out.push({ label: label, q: q }); };
    if (name) {
      if (a.recipe !== "company_value") mk("What is " + name + " worth?", "What is " + name + " worth?");
      if (a.recipe !== "company_holders") mk("Who owns " + name, "Who are the biggest holders of " + name + "?");
      if (a.recipe !== "company_insiders") mk("Insiders at " + name, "Have insiders at " + name + " bought or sold this year?");
      if (a.recipe !== "vs_benchmark") mk(name + " against the S&P 500", "Did " + name + " beat the S&P 500 this year?");
      if (a.recipe === "vs_benchmark") mk(name + " against the Dow", "Did " + name + " beat the Dow this year?");
      if (a.recipe !== "company_price_performance") mk(name + " over the past year", "How has " + name + " stock done over the past year?");
      if (a.recipe !== "company_calendar") mk("When " + name + " reports", "When does " + name + " report next?");
      if (a.recipe !== "company_growth_margin") mk(name + " growth and margins", "How fast is " + name + " growing and at what margin?");
      if (a.recipe !== "company_history") mk(name + " revenue, 5 years", "Chart " + name + " revenue over the last five years");
    } else {
      mk("Top 10 in the S&P 500 this year", "Top 10 performers of the S&P 500 this year");
      mk("What drove the S&P 500", "Which stocks contributed most to the S&P 500 this year?");
      mk("Best in the Dow this year", "Best performers in the Dow this year");
      mk("Ten largest by market value", "The ten largest US companies by market value");
      mk("Who reports next week", "Which companies report in the next two weeks?");
      mk("Cheapest large banks by P/E", "Which large banks trade on the lowest P/E?");
    }
    return out.slice(0, 5);
  }

  /* ---- the annual report's own words ----------------------------------- */
  // Passages are quoted exactly as filed, each with the section, form and
  // year it comes from and a link to the document on EDGAR.
  function citeHtml(cite, url) {
    if (!cite) return "";
    return "<p class='quote-cite'>" + (url ? "<a href='" + esc(url) + "' rel='noopener'>" + esc(cite) + " →</a>" : esc(cite)) + "</p>";
  }
  function quotesHtml(qs) {
    if (!qs || !qs.length) return "";
    return qs.map(function (q) {
      return "<blockquote class='answer-quote'>" + (q.text || []).map(function (p) { return "<p>" + esc(p) + "</p>"; }).join("") +
        citeHtml(q.cite, q.url) + "</blockquote>";
    }).join("");
  }
  // A list of sentences (a company's risk headlines) under its own group
  // headings: a list, not a table, since every entry is a sentence.
  function listsHtml(l) {
    if (!l || !l.groups || !l.groups.length) return "";
    var n = 0, total = l.groups.reduce(function (s, g) { return s + g.items.length; }, 0);
    var html = "<h2>" + esc(l.title || "List") + " <span class='h2-note'>" + count(total) + (l.unit ? " " + esc(l.unit) : "") + "</span></h2>";
    l.groups.forEach(function (g) {
      if (g.name) html += "<h3 class='list-group'>" + esc(g.name) + "</h3>";
      html += "<ol class='answer-list' start='" + (n + 1) + "'>" + g.items.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ol>";
      n += g.items.length;
    });
    return html + citeHtml(l.note, l.url);
  }

  /* ---- how it was worked out: the steps the server took, with their timing ---- */
  function secs(t) { return (Math.round(t * 10) / 10).toFixed(1) + " s"; }
  function trailHtml(trail, cls) {
    if (!trail || !trail.length) return "";
    return "<ol class='trail" + (cls ? " " + cls : "") + "'>" + trail.map(function (s, i) {
      return "<li" + (i === trail.length - 1 && !s.done ? " class='now'" : "") + "><span class='trail-t'>" + esc(secs(s.t)) + "</span><span>" + esc(s.text) + "</span></li>";
    }).join("") + "</ol>";
  }
  // The full account for an answer the server worked out step by step (or read
  // with the model): what it did and when, how it read the question, the steps
  // in words, anything it looked at first, the checks that sent it back, the
  // reviewer's verdict, and the query it ran. Open by default when the answer
  // came by steps, since those are the ones worth reading; collapsed otherwise.
  function workedOutHtml(a, trail) {
    var m = a.match || {};
    var bySteps = m.via === "steps";
    var hasTrail = trail && trail.length > 1;
    if (!bySteps && !hasTrail && !(a.looks && a.looks.length) && !(a.checks && a.checks.length)) return "";
    var body = "";
    if (hasTrail) body += "<h3>What happened, and when</h3>" + trailHtml(trail.map(function (s) { return { t: s.t, text: s.text, done: true }; }));
    if (m.read_as) body += "<h3>How the question was read</h3><p>" + esc(String(m.read_as).replace(/\d{4}-\d{2}-\d{2}/g, function (d) { return dateWords(d); })) + "</p>";
    if (a.steps && a.steps.length) body += "<h3>The steps</h3><ol class='steps'>" + a.steps.map(function (st) { return "<li>" + esc(st) + "</li>"; }).join("") + "</ol>";
    if (a.looks && a.looks.length) {
      body += "<h3>Looked at first</h3>" + a.looks.map(function (l) {
        return "<p class='quiet'>" + (l.error ? "Did not run: " + esc(l.error) : esc(String(l.rows == null ? 0 : l.rows)) + " row" + (l.rows === 1 ? "" : "s") + " seen" + (l.more ? " (first of more)" : "")) + "</p>" +
          "<pre class='sql'>" + esc(l.query || "") + "</pre>";
      }).join("");
    }
    if (a.checks && a.checks.length) {
      body += "<h3>Checks</h3><ul class='checks'>" + a.checks.map(function (c) {
        var word = c.severity === "revise" ? "Sent back" : c.severity === "refuse" ? "Refused" : "Noted";
        return "<li><b>" + esc(word) + ":</b> " + esc(c.note || c.check) + "</li>";
      }).join("") + "</ul>";
    }
    if (a.review && a.review.verdict) {
      var v = a.review.verdict;
      body += "<h3>Review</h3><p>" + esc(v === "ok" ? "The answer was read against the question and passed." :
        v === "note" ? "Passed, with a note: " + (a.review.reason || "") : v === "revise" ? "Sent back once: " + (a.review.reason || "") : String(v)) + "</p>";
    }
    if (a.query || (a.calc && a.calc.query)) body += "<h3>The query run on the data</h3><pre class='sql'>" + esc(a.query || a.calc.query) + "</pre>";
    if (!body) return "";
    return "<details class='calc worked-out'" + (bySteps ? " open" : "") + "><summary>How it was worked out" +
      (hasTrail ? " <span class='h2-note'>" + esc(secs(trail[trail.length - 1].t)) + "</span>" : "") + "</summary><div class='calc-body'>" + body + "</div></details>";
  }

  /* ---- the answer ------------------------------------------------------ */
  // A formula's key as a reader's words: "share_of_reported_pct" → "Share of reported".
  function calcLabel(k) {
    var s = String(k).replace(/_(pct|x|usd)$/, "").replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function panel(title, note, body, cls) {
    return "<section class='hp-panel" + (cls ? " " + cls : "") + "'>" +
      (title ? "<div class='an-head'><h2>" + esc(title) + "</h2>" + (note ? "<span class='note'>" + esc(note) + "</span>" : "") + "</div>" : "") +
      body + "</section>";
  }
  function copyText(btn, text) {
    var done = function (word) {
      var was = btn.getAttribute("data-label") || btn.textContent;
      btn.setAttribute("data-label", was);
      btn.textContent = word;
      setTimeout(function () { btn.textContent = was; }, 1800);
    };
    if (!navigator.clipboard) { done("Copy blocked"); return; }
    navigator.clipboard.writeText(text).then(function () { done("Copied"); }, function () { done("Copy blocked"); });
  }

  function renderAnswer(a, trail) {
    disposeCharts();
    var el = $("answer");
    el.classList.remove("an-stale");
    // the full account sits inside the rail's own "How This Was Worked Out" panel, so its summary names what it opens
    // and stays folded: the rail shows the steps, the full log is one click away
    var worked = workedOutHtml(a, trail).replace("<summary>How it was worked out", "<summary>The full log, checks and query")
      .replace("<details class='calc worked-out' open>", "<details class='calc worked-out'>");
    var calc = a.calc || {};
    var co = (a.entities && a.entities.company) || ((a.entities && a.entities.mentioned_companies) || [])[0] || null;
    var link = a.links && a.links.company ? a.links.company : (co && co.ticker ? "/?t=" + encodeURIComponent(co.ticker) : null);
    var logo = co && co.ticker && window.usLogos ? usLogos.img(co.ticker, 16) : "";
    var src = (a.sources || []).map(function (s) { return s.credit; }).join(" ");
    var readAs = a.match && a.match.read_as ? String(a.match.read_as).replace(/\d{4}-\d{2}-\d{2}/g, function (d) { return dateWords(d); }) : "";
    var when = a.period && a.period.text ? a.period.text : (typeof calc.period === "string" ? calc.period.replace(/\s*\(\d{4}-.*\)$/, "") : "");
    var hasTable = !!(a.table && a.table.rows && a.table.rows.length);
    var stamp = "Plover Analytics, " + location.origin + "/?q=" + encodeURIComponent(a.question || lastAsked);
    var csvHead = [a.headline, src, "Formulas: " + Object.keys(calc).filter(function (k) { return k !== "query" && k !== "steps"; })
      .map(function (k) { return calcLabel(k) + " = " + calc[k]; }).join("; "), stamp];

    // the main column: the question and its answer, the figures, the chart, the table, a follow-up
    var main = panel("", "",
      "<div class='an-asked'><span>You asked</span><b>" + esc(a.question || lastAsked) + "</b>" +
        (co && link ? "<a class='an-co' href='" + esc(link) + "'>" + logo + esc(co.name) + "</a>" : "") +
        (when ? "<span class='when'>" + esc(when) + "</span>" : "") + "</div>" +
      // how a question was read matters only when it was read as another (a follow-up made whole)
      (readAs && a.question && a.question !== lastAsked ? "<p class='quiet read-as'>Read as: " + esc(readAs) + "</p>" : "") +
      "<p class='headline hit'>" + esc(a.headline) + "</p>" +
      (a.answer ? "<p class='answer-text'>" + esc(a.answer) + "</p>" : "") +
      quotesHtml(a.quotes), "an-q") +
      figuresHtml(a.figures);
    if (a.chart) {
      main += panel(a.chart.title || "Chart", a.chart.unit || "",
        "<div class='an-chart'><div class='chart-el' id='an-chart'></div></div>" +
        "<div class='an-foot'><span>" + esc(src) + "</span><span class='acts'>" +
        "<button type='button' class='an-link' id='an-png'>Download PNG</button>" +
        "<button type='button' class='an-link' id='an-csv'>Download CSV</button></span></div>", "an-chart-panel");
    }
    if (a.lists) main += "<section class='hp-panel an-list'>" + listsHtml(a.lists) + "</section>";
    if (hasTable) main += panel("Detail", count(a.table.rows.length) + " rows" + (a.table.unit ? " · " + a.table.unit : ""), tableHtml(a.table, "an-table"));
    var fu = followupsFor(a);
    main += panel("", "", "<form id='an-ask' autocomplete='off'><label for='an-q' class='fp-sr'>Ask a follow-up</label>" +
      "<input type='search' id='an-q' maxlength='300' placeholder='Ask a follow-up, e.g. and Microsoft?'><button type='submit'>Ask</button></form>" +
      (fu.length ? "<div class='an-chips'>" + fu.map(function (f) { return "<a href='/?q=" + encodeURIComponent(f.q) + "'>" + esc(f.label) + "</a>"; }).join("") + "</div>" : ""), "an-ask");

    // the rail: what to do with it, how it was worked out, what to read with care, where it came from
    var formulas = Object.keys(calc).filter(function (k) { return k !== "query" && k !== "steps" && k !== "period"; }).map(function (k) {
      return "<li><b>" + esc(calcLabel(k)) + ":</b> " + esc(calc[k]) + "</li>";
    }).join("");
    var steps = a.steps && a.steps.length ? "<ol>" + a.steps.map(function (st) { return "<li>" + esc(st) + "</li>"; }).join("") + "</ol>" : "";
    var query = !worked && calc.query ? "<details><summary>Show the query</summary><pre class='sql'>" + esc(calc.query) + "</pre></details>" : "";
    var rail = panel("", "", "<div class='an-acts'>" +
      "<button type='button' id='an-copy'>Copy link</button><button type='button' id='an-cite'>Cite</button></div>");
    if (worked || steps || formulas || query) {
      rail += panel("How This Was Worked Out", "", "<div class='an-body'>" + steps +
        (formulas ? "<ul class='plain'" + (steps ? " style='margin-top:10px'" : "") + ">" + formulas + "</ul>" : "") + query + worked + "</div>");
    }
    if (a.notes && a.notes.length) rail += panel("Read With Care", "", "<div class='an-body'><ul class='plain'>" + a.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul></div>");
    if ((a.sources || []).length) {
      rail += panel("Sources", "", "<div class='an-body'><ul class='plain'>" + a.sources.map(function (s) {
        return "<li>" + esc(s.credit) + (s.note ? " " + esc(s.note) : "") + "</li>";
      }).join("") + (a.links && a.links.filing ? "<li><a href='" + esc(a.links.filing) + "' rel='noopener'>The filing on EDGAR</a></li>" : "") + "</ul></div>");
    }
    if (co && link) {
      rail += panel("Company", "", "<div class='an-company'><a class='name' href='" + esc(link) + "'>" +
        (co.ticker && window.usLogos ? usLogos.img(co.ticker, 28) : "") + esc(co.name) + "</a>" +
        "<a href='" + esc(link) + "'>Open the company page</a></div>");
    }

    el.innerHTML = "<div class='an-grid'><div class='an-col'>" + main + "</div><aside class='an-rail' aria-label='About this answer'>" + rail + "</aside></div>";
    el.hidden = false;
    $("stock").hidden = true;
    $("home-note").hidden = true;

    var c = null;
    if (a.chart) {
      c = drawChart($("an-chart"), a.chart, src);
      if (c) {
        charts.push(c);
        $("an-png").onclick = function () { c.exportPNG("plover-" + a.recipe + ".png"); };
        $("an-csv").onclick = function () { c.exportCSV("plover-" + a.recipe + ".csv", csvHead); };
      } else {
        $("an-chart").closest(".hp-panel").remove();
      }
    }
    $("an-copy").onclick = function () { copyText(this, location.origin + "/?q=" + encodeURIComponent(a.question || lastAsked)); };
    $("an-cite").onclick = function () { copyText(this, a.headline + " " + stamp + " (answered " + dateWords(new Date().toISOString()) + ")."); };
    $("an-ask").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var nq = $("an-q").value.trim();
      if (nq) { window.scrollTo(0, 0); ask(nq, true); }
    });
    Array.prototype.forEach.call(el.querySelectorAll(".an-chips a"), function (x) {
      x.addEventListener("click", function (ev) {
        var nq = new URL(x.href, location.href).searchParams.get("q");
        if (!nq) return;
        ev.preventDefault();
        window.scrollTo(0, 0);
        ask(nq, true);
      });
    });
    if (window.enhanceTables) window.enhanceTables(el);
    document.title = a.headline.slice(0, 70) + " · Plover Analytics";
  }

  // A question first asked by clicking an example stays an example when this
  // tab sends it again (Ask, Enter, a reload), so the ask log counts one click,
  // not a click and a typed question (app/ask_log.py)
  var EXAMPLE_KEY = "plover-example-asked", lastExample = null;
  function exampleVia(q, via) {
    var key = q.toLowerCase();
    if (via === "example") {
      lastExample = key;
      try { sessionStorage.setItem(EXAMPLE_KEY, key); } catch (e) {}
      return via;
    }
    if (via) return via;
    var kept = lastExample;
    try { kept = sessionStorage.getItem(EXAMPLE_KEY) || kept; } catch (e) {}
    return kept === key ? "example" : via;
  }

  // via: "example" when the question is one of the page's own suggestions,
  // so the ask log can tell a click from a question somebody typed (app/ask_log.py)
  function ask(q, push, via) {
    via = exampleVia(q, via);
    placeBox(true);
    window.scrollTo(0, 0);
    // The last answer and the front page's examples go at once: the question
    // shows straight away in a card where its answer will appear, with the
    // server's steps as they happen, and the answer takes that card's place.
    // A previous answer stays, faded back, until the new one replaces it: the
    // answer area changes only when an answer arrives.
    var el = $("answer");
    if (!el.hidden && el.innerHTML) el.classList.add("an-stale");
    $("stock").hidden = true;
    $("home-note").hidden = true;
    // The wait card lives outside #answer, so the answer area changes only
    // when an answer arrives — a reader (or the example checker) never counts
    // "working…" as a result. It follows the server's steps (askStream).
    var started = Date.now(), trail = [];
    $("status").innerHTML = "<section class='hp-panel an-q an-wait'><div class='an-asked'><span>You asked</span><b>" + esc(q) + "</b></div>" +
      "<div class='an-skel' aria-hidden='true'><i></i><i></i><i></i></div><div id='wait-trail'></div></section>";
    function heard(text) {
      if (trail.length && trail[trail.length - 1].text === text) return;   // the server's first line repeats ours
      if (trail.length) trail[trail.length - 1].done = true;
      trail.push({ t: (Date.now() - started) / 1000, text: text, done: false });
      if ($("wait-trail")) $("wait-trail").innerHTML = trailHtml(trail, "wait");
    }
    heard("Reading your question…");
    $("status").hidden = false;
    if ($("q").value.trim() !== q) $("q").value = q;
    lastAsked = q;
    if (push) history.replaceState({ q: q }, "", "/?q=" + encodeURIComponent(q));
    // The previous question in this tab goes with the new one, so a follow-up
    // ("and Microsoft?") is read as a whole question; the answer shows that reading.
    var prev = prevQuestion();
    var tail = (prev && prev !== q ? "&prev=" + encodeURIComponent(prev) : "") +
      (via ? "&via=" + encodeURIComponent(via) : "");
    function full() {
      return askStream("/ask?q=" + encodeURIComponent(q) + tail, function (text) {
        if (lastAsked === q) heard(text);
      }).then(function (a) {
        $("status").hidden = true;
        heard("Answered.");
        rememberQuestion(a.question || q);
        renderAnswer(a, trail);
      }, function (e) {
        // not a stock question the box answers: the US economy may (inflation, wages, Treasury yields)
        if (e.status !== 422 && e.status !== 404) throw e;
        return getJSON(PREVIEW + "?q=" + encodeURIComponent(q) + (via ? "&via=" + encodeURIComponent(via) : "")).then(function (p) {
          if (p.kind !== "macro") throw e;
          $("status").hidden = true;
          renderPreview(p, q);
        }, function () { throw e; });
      });
    }
    return reader.then(function () {
      if (!gated()) return full();
      // signed out: the start of the answer, and the rest one sign-up away
      return getJSON(PREVIEW + "?q=" + encodeURIComponent(q) + tail).then(function (p) {
        $("status").hidden = true;
        if (p.question) rememberQuestion(p.question);
        renderPreview(p, q);
      }, function (e) {
        if (e.status === 404) { previewOff = true; return full(); }     // no preview on this server: the full answer
        throw e;
      });
    }).catch(function (e) {
      $("status").hidden = true;
      // a miss the server worded carries its kind; anything else (the network
      // dropped, a server error with no words) is ours, and says so
      var ours = !e.miss && (!e.status || e.status >= 500 || !e.message);
      renderMiss(ours ? MISS_ERROR : e.message, ours ? [] : e.tries, ours ? "error" : (e.miss || "not_held"), q);
    });
  }

  /* ---- when the box cannot answer -------------------------------------- */
  // Never a code or a blank: one plain sentence, the questions that come
  // closest, and, when the fault is ours, Try Again with the question still in
  // the box. The words come from the server (app/ask_miss.py).
  var MISS_ERROR = "That didn't work on our side. Try again in a moment; your question is still in the box.";

  function renderMiss(text, tries, kind, q) {
    disposeCharts();
    var el = $("answer");
    el.classList.remove("an-stale");
    el.hidden = false;
    $("stock").hidden = true;
    var list = (tries || []).map(function (t) {
      return "<li><a href='/?q=" + encodeURIComponent(t) + "'>" + esc(t) + "</a></li>";
    }).join("");
    el.innerHTML = "<div class='fp-card fp-miss'><p class='fp-head'>" + esc(text || MISS_ERROR) + "</p>" +
      (kind === "error" ? "<p><button type='button' class='fp-cta fp-retry' id='miss-retry'>Try Again</button></p>" : "") +
      (list ? "<p class='fp-lead'>Try instead:</p><ul class='fp-examples' id='miss-try' aria-label='Questions Plover can answer'>" +
        list + "</ul>" : "") + "</div>";
    // the front page's examples, when there is nothing closer to offer
    $("home-note").hidden = !!list || kind === "error";
    Array.prototype.forEach.call(el.querySelectorAll("#miss-try a"), function (a) {
      a.addEventListener("click", function (ev) {
        var nq = new URL(a.href, location.href).searchParams.get("q");
        if (!nq) return;
        ev.preventDefault();
        ask(nq, true, "example");
      });
    });
    var retry = $("miss-retry");
    if (retry) retry.addEventListener("click", function () { ask(q, true); });
    document.title = "Plover Analytics";
  }

  /* ---- who is reading: the start of an answer, or all of it ----------- */
  // Signed out, a question shows the start of its answer and the rest is one
  // sign-up away (agreed with the user, 2026-10-19). Signed in, or where
  // accounts are off (no /api/v1/account here), the full answer shows.
  var PREVIEW = "/api/v1/assistant/preview";
  var me = { on: false, email: null }, previewOff = false;
  var reader = fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
    if (r.status === 404) return null;
    me.on = true;
    return r.ok ? r.json() : null;
  }).then(function (who) { me.email = (who && who.email) || null; account(); },
           function () { account(); });

  function gated() { return me.on && !me.email && !previewOff; }

  function account() {
    var slot = $("fp-account");
    var back = "signin.html?returnTo=" + encodeURIComponent("/" + location.search);
    if (slot) {
      slot.innerHTML = !me.on ? "" : me.email ? "<a class='hp-mine' href='my.html'>Your Plover</a>"
        : "<a class='hp-signin' href='" + back + "'>Sign in</a><a class='hp-join' href='" + back + "'>Create free account</a>";
    }
    if ($("gate-cta")) {
      $("gate-cta").hidden = !gated();
      $("gate-cta-link").href = back;
    }
  }

  // The box sits on the front band until something is asked, then in the
  // header above the answer, so the next question is always in the same place.
  function placeBox(asked) {
    document.body.classList.toggle("asked", asked);
    var f = $("ask-form"), slot = $(asked ? "box-top" : "box-hero");
    if (f && slot && f.parentNode !== slot) slot.appendChild(f);
  }

  // The strip under the front band: US CPI and core CPI on the year, the 2- and
  // 10-year Treasury yields and the gap between them, each linked to its page.
  // A rate's change is in pp, warm when rising and cool when falling. If either
  // source fails its half is left out; if both fail the strip stays hidden.
  function strip() {
    var el = $("strip");
    if (!el) return;
    var since = new Date(Date.now() - 45 * 864e5).toISOString().slice(0, 10);
    var quiet = function (p) { return p.catch(function () { return null; }); };
    Promise.all([quiet(getJSON("/api/v1/cpi-us/overview")),
                 quiet(getJSON("/api/v1/ust-yields/observations?series=2Y,10Y&start=" + since))]).then(function (r) {
      var parts = [], when = [];
      var pp = function (v) { return (v > 0 ? "▲ +" : v < 0 ? "▼ " + MINUS : "") + fmtNum(Math.abs(v), 2) + "pp"; };
      var ov = r[0];
      if (ov && ov.tiles) {
        var month = ov.release && ov.release.latest_period ? MONTHS[+ov.release.latest_period.slice(5, 7) - 1] : "";
        [["headline_yoy", "US CPI"], ["core_yoy", "Core CPI"]].forEach(function (k) {
          var t = ov.tiles.filter(function (x) { return x.key === k[0]; })[0];
          if (!t || t.value == null) return;
          parts.push("<a href='us-inflation.html' title='" + esc(t.calc || "") + "'>" + k[1] + " <b>" + fmtNum(t.value, 1) + "%</b> " + esc(month) +
            (t.delta_pp != null ? " <span class='" + (t.delta_pp > 0 ? "rise" : "fall") + "'>" + pp(t.delta_pp) + "</span>" : "") + "</a>");
        });
        if (ov.release && ov.release.latest_period) when.push("CPI data through " + MONTHS[+ov.release.latest_period.slice(5, 7) - 1] + " " + ov.release.latest_period.slice(0, 4));
      }
      var ys = r[1] && r[1].series;
      if (ys) {
        var last = function (code) {
          var s = ys.filter(function (x) { return x.code === code; })[0];
          var pts = s ? s.points.filter(function (p) { return p[1] != null; }) : [];
          return pts.length ? pts[pts.length - 1] : null;
        };
        var y2 = last("2Y"), y10 = last("10Y");
        if (y2) parts.push("<a href='us-treasury.html'>2Y <b>" + fmtNum(y2[1], 2) + "%</b></a>");
        if (y10) parts.push("<a href='us-treasury.html'>10Y <b>" + fmtNum(y10[1], 2) + "%</b></a>");
        if (y2 && y10 && y2[0] === y10[0]) {
          var gap = y10[1] - y2[1];
          parts.push("<a href='us-treasury.html' title='10-year yield − 2-year yield'>10Y − 2Y <b>" + (gap > 0 ? "+" : gap < 0 ? MINUS : "") + fmtNum(Math.abs(gap), 2) + "pp</b></a>");
        }
        if (y10) when.unshift("Yields " + dateWords(y10[0]));
      }
      if (!parts.length) return;
      el.innerHTML = "<div class='hp-strip-in'>" + parts.join("") + "<span class='when'>" + esc(when.join(" · ")) + "</span></div>";
      el.hidden = false;
    });
  }

  function previewValue(f) {
    var v = f.value, u = f.unit || "";
    if (v == null) return MISSING;
    if (u === "USD") return money(v);
    // as many decimals as the server sent (1 for a rate, 2 for a yield), so a figure matches its headline
    if (u === "%" || u === "pct" || u === "percent") return pct(v, Math.min(2, Math.max(1, (String(v).split(".")[1] || "").length)));
    if (u === "count") return fmtNum(v, 0);
    if (u === "x" || u === "ratio" || u === "\u00d7") return mult(v);
    return (typeof v === "number" ? fmtNum(v, Math.abs(v) < 100 ? 2 : 0) : esc(v)) + (u ? " " + esc(u) : "");
  }

  function renderPreview(p, q) {
    disposeCharts();
    var el = $("answer");
    el.classList.remove("an-stale");
    el.hidden = false;
    $("stock").hidden = true;
    if (p.kind === "none") {
      renderMiss(p.message, p.try, p.miss || "not_held", q);
      return;
    }
    var signedIn = !gated();
    // an economy answer's full version is its dataset page; a stock answer's is this page, signed in
    var url = p.kind === "macro" ? p.full_url : "/?q=" + encodeURIComponent(p.question || q);
    var href = signedIn ? url : "signin.html?returnTo=" + encodeURIComponent(url);
    var figs = (p.figures || []).map(function (f) {
      return "<div><dt>" + esc(f.label) + "</dt><dd>" + previewValue(f) +
        (f.as_of ? "<small>" + esc(f.as_of_label || dateWords(f.as_of)) + "</small>" : "") + "</dd></div>";
    }).join("");
    el.innerHTML = "<article class='fp-card'>" +
      "<div class='an-asked'><span>You asked</span><b>" + esc(q) + "</b></div>" +
      "<p class='fp-head hit'>" + esc(p.headline) + "</p>" +
      (p.period && p.period.text ? "<p class='period-line'>" + esc(p.period.text) + "</p>" : "") +
      (p.lead ? "<p class='fp-lead'>" + esc(p.lead) + "</p>" : "") +
      (figs ? "<dl class='fp-figs'>" + figs + "</dl>" : "") +
      ((p.sources || []).length ? "<p class='fp-src'>" + p.sources.map(function (x) { return esc(String(x).replace(/\.\s*$/, "")); }).join(" · ") + "</p>" : "") +
      (p.calc ? "<p class='fp-src'>" + esc(p.calc) + "</p>" : "") +
      (signedIn && p.kind !== "macro" ? "" :
        "<div class='fp-more'><p class='fp-more-h'>The full answer also has</p><ul>" +
        (p.more || []).map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul>" +
        "<p><a class='fp-cta' href='" + esc(href) + "'>" +
        (signedIn ? "Open the Full Chart" : "Sign Up Free to See the Full Answer") + "</a></p>" +
        (signedIn ? "" : "<p class='fp-small'>Your email is all it takes. No password, no card.</p>") + "</div>") +
      "</article>";
    $("home-note").hidden = true;
    document.title = String(p.headline || "").slice(0, 70) + " · Plover Analytics";
  }

  // The last question answered in this tab (a reload keeps it; a new tab starts clean).
  function prevQuestion() {
    try { return sessionStorage.getItem("us-ask-prev") || ""; } catch (e) { return ""; }
  }
  function rememberQuestion(q) {
    try { sessionStorage.setItem("us-ask-prev", q); } catch (e) { /* storage blocked: no memory, still answers */ }
  }

  /* ---- one company ----------------------------------------------------- */
  function renderStock(t, m, px, ins, hold) {
    disposeCharts();
    var el = $("stock");
    var mt = m.metrics || {}, p = m.price || {}, ps = m.per_share || {}, mvb = m.market_value_basis || {};
    // a split read from prices in the last year that no filing confirms makes the one-year change doubtful
    var yearAgo = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
    var doubtfulSplit = !!(px && px.splits && px.splits.some(function (s) { return s.status && s.status !== "confirmed" && s.date >= yearAgo; }));
    // the company's header card and tabs (co-head.js) replace this placeholder once loaded
    var html = "<div id='stock-card'><section class='cp-card'><div class='cp-id'><div class='cp-who'><h1>" + esc(m.name) +
      "</h1></div></div></section></div>" +
      "<p class='sec-note'>Latest annual report: year to " + dateWords(m.period_end) + ", filed " + dateWords(m.filed) + " · " +
      "<a href='" + esc(m.source_url) + "' target='_blank' rel='noopener'>filing on EDGAR</a></p>" +
      "<dl class='fact-row'>" +
      "<div><dt>Close</dt><dd>" + money(p.close, 2) + "<span class='qual'>" + (p.date ? dateWords(p.date) : "no price on file") + "</span></dd></div>" +
      "<div><dt>Market value</dt><dd>" + money(mt.market_cap) + "<span class='qual'>" +
        (mvb.note && mt.market_cap == null ? "not shown: see below" : mvb.caution ? "read with care: see below" : "close × shares outstanding") +
        "</span></dd></div>" +
      "<div><dt>P/E</dt><dd>" + mult(mt.pe_x) + "<span class='qual'>on diluted EPS " + (ps.eps_diluted == null ? MISSING : "$" + fmtNum(ps.eps_diluted, 2)) + "</span></dd></div>" +
      "<div><dt>1-year change</dt><dd>" + pct(mt.price_change_1y_pct, 1, true) + "<span class='qual'>" +
        (doubtfulSplit ? "read with care: see the note under the chart" : "split-adjusted") + "</span></dd></div>" +
      "<div><dt>Dividend yield</dt><dd>" + pct(mt.dividend_yield_pct, 2) + "<span class='qual'>last fiscal year ÷ value</span></dd></div>" +
      "<div><dt>Buyback yield</dt><dd>" + pct(mt.buyback_yield_pct, 2) + "<span class='qual'>last fiscal year ÷ value</span></dd></div>" +
      "</dl>";
    // a held-back or doubtful market value says why, and when it will fix itself
    if (mt.market_cap == null && mvb.note && p.close != null) {
      html += "<p class='sec-note value-note'>Market value is not shown. " + esc(mvb.note) + (mvb.expect ? " " + esc(mvb.expect) : "") + "</p>";
    } else if (mvb.caution && mt.market_cap != null) {
      html += "<p class='sec-note value-note'>" + esc(mvb.caution) + (mvb.expect ? " " + esc(mvb.expect) : "") + "</p>";
    }
    // price
    html += "<h2>Share Price <span class='h2-note'>USD, daily close, since " + (px && px.bars.length ? dateWords(px.bars[0].date) : "—") + "</span></h2>" +
      "<div id='px-range'></div><div class='chart-foot'><span id='px-src'></span></div>";
    if (px && px.splits && px.splits.length) {
      html += "<p class='sec-note' style='margin:8px 0 0'>" + px.splits.map(function (s) {
        return s.note ? esc(s.note) : "Closes before " + dateWords(s.date) + " are adjusted for the " +
          (s.ratio >= 1 ? fmtNum(s.ratio, 0) + "-for-1 split" : "1-for-" + fmtNum(1 / s.ratio, 0) + " reverse split") + ".";
      }).join(" ") + " The bars themselves are as delivered.</p>";
    }
    // ratios
    var fm = m.calc || {};
    var ratioRows = (m.metric_defs || []).map(function (d) {
      var v = mt[d.metric];
      return [d.label, v == null ? null : (d.unit === "%" ? pct(v, 1) : d.unit === "×" ? mult(v) : d.unit === "USD" || d.unit === "unit" ? money(v) : fmtNum(v, 2)), d.formula];
    });
    html += "<h2>Latest Annual Report <span class='h2-note'>" + esc(m.unit || "") + " · year to " + dateWords(m.period_end) + "</span></h2>" +
      "<dl class='fact-row'>" +
      [["Revenue", m.size.revenue], ["Operating income", m.size.operating_income], ["Net income", m.size.net_income], ["Total assets", m.size.total_assets], ["Equity", m.size.equity], ["Cash", m.size.cash], ["Operating cash flow", m.size.cf_operating], ["Buybacks", m.size.buybacks], ["Dividends paid", m.size.dividends_paid]]
        .map(function (x) { return "<div><dt>" + x[0] + "</dt><dd>" + money(x[1]) + "</dd></div>"; }).join("") + "</dl>" +
      "<p class='sec-note'>As filed. Tags: " + esc(Object.keys(m.tags || {}).filter(function (k) { return m.tags[k]; }).map(function (k) { return k.replace(/_/g, " ") + " = " + m.tags[k]; }).join("; ")) + "</p>" +
      "<div class='table-wrap'><table class='tbl-fin' data-no-enhance><thead><tr><th>Ratio</th><th class='r'>Value</th><th>Formula</th></tr></thead><tbody>" +
      ratioRows.map(function (r) { return "<tr><td>" + esc(r[0]) + "</td><td class='r'>" + (r[1] == null ? MISSING : r[1]) + "</td><td class='txt quiet'>" + esc(r[2]) + "</td></tr>"; }).join("") +
      "</tbody></table></div>" +
      (m.flags && m.flags.length ? "<p class='sec-note' style='margin:8px 0 0'>Flags: " + esc(m.flags.join(", ").replace(/_/g, " ")) + ".</p>" : "");
    // insiders
    if (ins) {
      var s = ins.summary || {}, b = s.open_market_buys || {}, sl = s.open_market_sells || {};
      html += "<h2>Insider Trades <span class='h2-note'>last " + ins.months + " months · Forms 3, 4, 5</span></h2>" +
        "<dl class='fact-row'>" +
        "<div><dt>Open-market purchases</dt><dd>" + count(b.trades) + "<span class='qual'>" + money(b.value) + " by " + count(b.insiders) + " insiders</span></dd></div>" +
        "<div><dt>Open-market sales</dt><dd>" + count(sl.trades) + "<span class='qual'>" + money(sl.value) + " by " + count(sl.insiders) + " insiders</span></dd></div>" +
        "<div><dt>Net shares</dt><dd>" + (s.net_open_market_shares == null ? MISSING : (s.net_open_market_shares < 0 ? MINUS : "+") + fmtNum(Math.abs(s.net_open_market_shares), 0)) + "<span class='qual'>purchases − sales</span></dd></div></dl>" +
        "<p class='sec-note'>Awards, option exercises and tax withholding are counted separately and never as buying or selling. Value = shares × the price the filer reported.</p>" +
        tableHtml({ columns: ["Date", "Insider", "Role", "Code", "Shares", "Price", "Value", "10b5-1 plan"],
          rows: (ins.trades || []).slice(0, 40).map(function (t) { return [t.trans_date, t.owner_name, t.title || t.relationship, t.trans_code, t.shares, t.price, t.value, t.plan_10b5_1]; }) }, "ins-table");
    } else {
      html += "<h2>Insider Trades</h2><p class='sec-note'>No insider filings for this company in the loaded quarters.</p>";
    }
    // holders
    if (hold) {
      html += "<h2>Largest 13F Holders <span class='h2-note'>quarter to " + dateWords(hold.rows[0].report_period) + " · " + count(hold.holders) + " managers · " + money(hold.total_reported_value) + " reported</span></h2>" +
        tableHtml({ columns: ["Manager", "Shares", "Value", "Share of reported", "Change in shares"],
          rows: hold.rows.map(function (r) { return [r.manager_name, r.shares, r.value, r.share_of_reported_pct, r.shares_change]; }), unit: "USD" }, "hold-table") +
        "<p class='sec-note' style='margin:8px 0 0'>13F covers managers over $100m and long positions only; a share of reported value is not a share of the company. Change is against the previous filing window.</p>";
    } else {
      html += "<h2>Largest 13F Holders</h2><p class='sec-note'>No 13F positions could be matched to this company by name.</p>";
    }
    html += sourcesHtml([
      { name: "SEC filings", credit: "Source: U.S. Securities and Exchange Commission, EDGAR.", note: "Figures as filed and extracted by the SEC." },
      { name: "Daily prices", credit: "Source: Databento (EQUS.SUMMARY). Market data provided by Databento.", note: "Consolidated end-of-day closes, one-day delay." },
      { name: "Insider filings", credit: "Source: U.S. Securities and Exchange Commission, EDGAR (Forms 3, 4 and 5)." },
      { name: "13F holdings", credit: "Source: U.S. Securities and Exchange Commission, EDGAR (Form 13F)." }], fm, null, null);
    var fu = [[t + " revenue, 5 years", "Chart " + t + " revenue over the last five years"],
              ["Who owns " + t, "Who are the biggest holders of " + t + "?"], ["Insiders at " + t, "Have insiders at " + t + " bought or sold this year?"],
              [t + " over the past year", "How has " + t + " stock done over the past year?"], ["When " + t + " reports", "When does " + t + " report next?"]];
    html += "<div class='followups'>" + fu.map(function (f) { return "<a href='/?q=" + encodeURIComponent(f[1]) + "'>" + esc(f[0]) + "</a>"; }).join("") + "</div>";
    el.innerHTML = html;
    panelize(el, "Market Data");
    // two tabs of the company's pages share this view: Price (the market data,
    // the price chart and the latest annual report's ratios) and Insiders and
    // holders; panels with no heading (sources, follow-ups) show on both
    var KEEP = stockView === "owners" ? ["Insider Trades", "Largest 13F Holders"] : ["Market Data", "Share Price", "Latest Annual Report"];
    Array.prototype.forEach.call(el.querySelectorAll(".cp-sec"), function (sec) {
      var h = sec.querySelector(".cp-head h2");
      if (h && KEEP.indexOf(h.textContent) < 0) sec.remove();
    });
    el.hidden = false;
    $("answer").hidden = true;
    $("home-note").hidden = true;
    if (window.coHead) {
      coHead.load(m.ticker || t).then(function (d) { if ($("stock-card")) { $("stock-card").innerHTML = coHead.html(d, stockView); coHead.reveal($("stock-card")); } })
        .catch(function () { /* the placeholder keeps the name */ });
    }
    document.title = m.name + " (" + (m.ticker || t) + ") · Plover Analytics";
    var srcLine = "Source: Databento (EQUS.SUMMARY), consolidated end-of-day closes, one-day delay" + (px && px.splits && px.splits.length ? ", split-adjusted" : "") + ".";
    if ($("px-src")) $("px-src").textContent = srcLine;
    // the range (1M to Max, the year to date, or from–to) and its change: price-range.js
    if ($("px-range")) charts.push(priceRange.mount($("px-range"), { bars: (px && px.bars) || [], ticker: m.ticker || t, name: m.name, source: srcLine }));
    if (window.enhanceTables) window.enhanceTables(el);
  }

  // A page drawn as one run of headings and blocks becomes panels (co-page.css):
  // each h2 opens a panel whose head is the heading and its note; the blocks
  // before the first heading go under `first`; the sources and the follow-ups
  // each get a panel of their own. Runs before any chart is drawn, so each
  // chart is sized in its final place.
  function panelize(el, first) {
    var out = document.createDocumentFragment(), body = null;
    function start(title, note) {
      var sec = document.createElement("section");
      sec.className = "cp-panel cp-sec";
      if (title) {
        var head = document.createElement("div");
        head.className = "cp-head";
        head.innerHTML = "<h2>" + esc(title) + "</h2>" + (note ? "<span class='note'>" + esc(note) + "</span>" : "");
        sec.appendChild(head);
      }
      body = document.createElement("div");
      body.className = "cp-body";
      sec.appendChild(body);
      out.appendChild(sec);
    }
    Array.prototype.slice.call(el.childNodes).forEach(function (n) {
      if (n.id === "stock-card") { out.appendChild(n); body = null; return; }
      if (n.nodeType === 1 && n.tagName === "H2") {
        var note = n.querySelector(".h2-note"), words = note ? note.textContent : "";
        if (note) note.remove();
        start(n.textContent.trim(), words);
        return;
      }
      if (n.nodeType === 1 && (n.matches("details.calc") || n.matches(".followups"))) start("", "");
      if (!body) start(first, "");
      body.appendChild(n);
    });
    el.innerHTML = "";
    el.appendChild(out);
  }

  // which of the company's tabs this view shows: "price" or "owners"
  var stockView = "price";
  function openStock(t, push, view) {
    t = String(t || "").toUpperCase().replace(/\s+/g, "");
    stockView = view === "owners" ? "owners" : "price";
    if (gated()) return ask(t, push);          // signed out, a company page is the start of its answer
    placeBox(true);
    var el = $("stock");
    el.hidden = false;
    $("answer").hidden = true;
    el.innerHTML = "<p class='state-wait' aria-live='polite'>Loading " + esc(t) + "…</p>";
    if (push) history.pushState({ t: t }, "", "/?t=" + encodeURIComponent(t) + (stockView === "owners" ? "&view=owners" : ""));
    var since = new Date("2000-01-01T00:00:00Z");     // every close on file, so any range can be read
    var quiet = function (p) { return p.catch(function () { return null; }); };
    return getJSON(API + "/financials/metrics/" + encodeURIComponent(t)).then(function (m) {
      var tk = m.ticker || t;
      return Promise.all([
        quiet(getJSON(API + "/prices/" + encodeURIComponent(tk) + "?start=" + since.toISOString().slice(0, 10))),
        quiet(getJSON(API + "/insiders/company/cik:" + m.cik + "?months=12&limit=40")),
        quiet(getJSON(API + "/ask/run/company_holders?company=" + encodeURIComponent(tk)).then(function (a) {
          // the recipe already picked the common-stock CUSIP and the window
          return getJSON(API + "/holdings/issuer?cusip=" + encodeURIComponent(a.entities.cusip) + "&limit=15");
        })),
      ]).then(function (r) { renderStock(tk, m, r[0], r[1], r[2]); });
    }).catch(function (e) {
      el.innerHTML = "<p class='state-error'>" + esc(e.message) + "</p><p class='sec-note'>Try the ticker as it trades (AAPL, BRK.B) or ask by name in the box above.</p>";
      $("home-note").hidden = false;
    });
  }

  /* ---- routing --------------------------------------------------------- */
  function route(push) {
    var u = new URL(location.href);
    var q = (u.searchParams.get("q") || "").trim();
    var t = (u.searchParams.get("t") || "").trim();
    if (t) return openStock(t, false, u.searchParams.get("view"));
    if (q) {
      // a bare ticker typed into the box opens the company page
      if (/^[A-Za-z]{1,5}([.-][A-Za-z])?$/.test(q) && q.toUpperCase() === q) return openStock(q, false);
      return ask(q, false);
    }
    $("answer").hidden = true;
    $("stock").hidden = true;
    $("home-note").hidden = false;
    placeBox(false);
    $("q").focus();
  }

  $("ask-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var q = $("q").value.trim();
    if (!q) return;
    if (/^[A-Z]{1,5}([.-][A-Z])?$/.test(q)) return openStock(q, true);
    ask(q, true);
  });
  // Nothing is sent while the reader types, a ticker included: only Enter or
  // Ask sends, as in any chat box. A ticker sent on a pause in typing sent
  // the first letter of a question (2026-10-25: a phone capitalises the
  // first letter, and "A", "B", "T", "D" went from four readers mid-word,
  // each answered with an error).
  // The theme toggle: charts are drawn in the theme's palette, so a switch
  // redraws the page as the other data pages do.
  initThemeToggle(function () { if (charts.length) location.reload(); });
  window.addEventListener("popstate", function () { route(false); });
  window.addEventListener("resize", function () { charts.forEach(function (c) { c.resize && c.resize(); }); });
  // An example question, or an example answer's Open Answer, answers in place without reloading the page.
  Array.prototype.forEach.call(document.querySelectorAll("#examples a, a.hp-open"), function (a) {
    a.addEventListener("click", function (ev) {
      var q = new URL(a.href, location.href).searchParams.get("q");
      if (!q) return;
      ev.preventDefault();
      ask(q, true, "example");
    });
  });
  // who is reading decides how much of an answer shows, so the page waits for that first
  reader.then(function () { route(false); });
  strip();
})();
