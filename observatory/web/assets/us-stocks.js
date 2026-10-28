/* The ask box on the front page. One company's page (?t=TICKER) is us-stock.js.
   Data: /api/v1/us/ask (app/us_ask_api.py), /api/v1/us/financials/metrics,
   /api/v1/us/prices, /api/v1/us/insiders, /api/v1/us/holdings,
   /api/v1/us/earnings (the modules under app/).

   The box sends the typed question to the server, which matches it to a
   question shape and answers by code — no model. The page draws what comes
   back: a headline, the sentences, the few figures, a chart or a table, the
   sources and the formula behind every computed number. The URL carries the
   question (?q=), so any answer is a link.

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

  // number formatting, tables and the sources block: us-format.js
  var money = usFmt.money, pct = usFmt.pct, mult = usFmt.mult, count = usFmt.count,
      dateWords = usFmt.dateWords, fmtBy = usFmt.fmtBy, tableHtml = usFmt.tableHtml,
      sourcesHtml = usFmt.sourcesHtml;

  function disposeCharts() {
    charts.forEach(function (c) { try { c.dispose && c.dispose(); } catch (e) { /* gone */ } });
    charts = [];
  }

  /* ---- pieces ---------------------------------------------------------- */
  function figuresHtml(figs) {
    var cells = (figs || []).filter(function (f) { return f && (f.value !== undefined); }).map(function (f) {
      var qual = f.as_of && f.unit !== "date" ? "<small>as of " + dateWords(f.as_of) + "</small>" : "";
      var title = f.formula ? " title='" + esc(f.formula) + "'" : "";
      // a true zero amount reads "$0", not "$0.00"
      var shown = f.unit === "USD" && f.value === 0 ? "$0" : fmtBy(f.unit, f.value);
      return "<div class='hit'" + title + "><dt>" + esc(f.label) + "</dt><dd>" + shown + qual + "</dd></div>";
    });
    return cells.length ? "<section class='hp-panel'><dl class='an-figs'>" + cells.join("") + "</dl></section>" : "";
  }

  function drawChart(el, chart, sourceLine) {
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
      charts.push(obsChart(el2, "line", { xType: "category", unit: "%", dp: 2, trust: "official", sourceLine: sourceLine,
        yAxisName: right.name, series: [{ name: right.name, points: right.points }] }));
      return c1;
    }
    if (chart.series[0].points.length && /^\d{4}$/.test(String(chart.series[0].points[0][0]))) cfg.xType = "category";
    return obsChart(el, "line", cfg);
  }

  // Follow-ups stay on the question asked: the same question turned round, the same question
  // for the other index or period, or a company the answer leads with. Each rewording was
  // checked to read back as the question it names ("dragged down most" still answers "added
  // most", "CEOs in the Dow" ignores the Dow, so neither is offered). With nothing relevant
  // to offer, no chips show rather than unrelated ones.
  var MARKET_RECIPES = ["market_rank", "industry_rank", "index_contribution", "index_stat", "ceo_pay", "insider_buying_market",
                        "calendar_window", "holders_increase_industry", "manager_portfolio", "banks_vs_yield"];
  var SCOPES = [{ name: "the S&P 500", re: /\b(the )?(s&p( ?500)?|sp ?500|s and p( 500)?)(?![\w&])/i, other: "the Dow" },
                { name: "the Dow", re: /\b(the )?(dow( jones)?( industrial average)?|djia)\b/i, other: "the S&P 500" }];
  var PERIODS = [{ re: /\b(so far )?this year\b|\bytd\b|\byear to date\b/i, other: "this month" },
                 { re: /\bthis month\b/i, other: "this year" }];
  // best/worst only turn round a price ranking; highest/lowest and cheapest any other
  var FLIPS_PRICE = [[/\btop\b/i, "worst"], [/\bbest\b/i, "worst"], [/\bworst\b/i, "best"], [/\bbottom\b/i, "top"]];
  var FLIPS = [[/\bhighest\b/i, "lowest"], [/\blowest\b/i, "highest"], [/\bmost expensive\b/i, "cheapest"], [/\bcheapest\b/i, "most expensive"]];

  function sentence(q) {
    q = String(q || "").replace(/[?.!\s]+$/, "");
    return q.charAt(0).toUpperCase() + q.slice(1);
  }
  function swapFirst(q, pairs) {
    for (var i = 0; i < pairs.length; i++) if (pairs[i][0].test(q)) return q.replace(pairs[i][0], pairs[i][1]);
    return null;
  }
  // the first n non-empty values of a table column, by its heading
  function colVals(a, heading, n) {
    var t = a.table, i = t && t.columns ? t.columns.indexOf(heading) : -1;
    if (i < 0) return [];
    return t.rows.map(function (r) { return r[i]; }).filter(function (v) { return v; }).slice(0, n);
  }
  function titled(s) {
    return /[a-z]/.test(s) ? s : s.toLowerCase().replace(/\b[a-z]/g, function (c) { return c.toUpperCase(); });
  }

  function marketFollowups(a, mk) {
    var q = sentence(a.question), e = a.entities || {};
    var scope = SCOPES.filter(function (s) { return s.re.test(q); })[0];
    var period = PERIODS.filter(function (p) { return p.re.test(q); })[0];
    var metric = e.metric || "", priced = /^price_change/.test(metric);
    var tickers = colVals(a, "Ticker", 2), t0 = tickers[0];
    var addScope = function () {
      if (scope) mk(sentence(q.replace(scope.re, scope.other)), q.replace(scope.re, scope.other));
    };
    var addPeriod = function () {
      if (period) mk(sentence(q.replace(period.re, period.other)), q.replace(period.re, period.other));
    };
    switch (a.recipe) {
      case "market_rank":
      case "industry_rank":
        // the smallest by market value is a list of shells at $0, never worth a click
        var flipped = metric === "market_cap" ? null : swapFirst(q, priced ? FLIPS_PRICE : FLIPS);
        if (flipped) mk(sentence(flipped), sentence(flipped));
        if (priced) addPeriod();
        if (a.recipe === "market_rank") {
          if (scope) addScope();
          else mk("Only S&P 500 members", q + " in the S&P 500");
        }
        var bench = scope ? scope.name : "the S&P 500";
        tickers.forEach(function (t) {
          if (priced) mk(t + " against " + bench, "Did " + t + " beat " + bench + " this year?");
          else mk("What is " + t + " worth?", "What is " + t + " worth?");
        });
        break;
      case "index_contribution":
        var ix = (scope || SCOPES[0]).name, oix = (scope || SCOPES[0]).other, when = period ? " " + q.match(period.re)[0] : "";
        mk("What drove " + oix + when, "Which stocks contributed most to " + oix + when + "?");
        if (period) mk("What drove " + ix + " " + period.other, "Which stocks contributed most to " + ix + " " + period.other + "?");
        mk("Best performers in " + ix + when, "Top 10 performers of " + ix + when);
        if (t0) mk(t0 + " against " + ix, "Did " + t0 + " beat " + ix + " this year?");
        break;
      case "index_stat":
        addScope();
        var rank = q.replace(/\b(average|median|mean|typical)\b/i, /^(pe|ps|pb)_x$/.test(metric) ? "lowest" : "highest");
        if (rank !== q) mk(sentence(rank), sentence(rank));
        break;
      case "ceo_pay":
        var other = swapFirst(q, FLIPS.concat([[/\bbest\b/i, "lowest"], [/\btop\b/i, "lowest"]]));
        if (other) mk(sentence(other), sentence(other));
        if (t0) {
          mk(t0 + " against the S&P 500", "Did " + t0 + " beat the S&P 500 this year?");
          mk("Insiders at " + t0, "Have insiders at " + t0 + " bought or sold this year?");
        }
        break;
      case "insider_buying_market":
        tickers.forEach(function (t) { mk("Insiders at " + t, "Have insiders at " + t + " bought or sold this year?"); });
        if (t0) mk(t0 + " this year", "How has " + t0 + " stock done this year?");
        break;
      case "calendar_window":
        if (e.days && e.days > 7) mk("Who reports this week", "Which companies report this week?");
        else mk("Who reports in the next two weeks", "Which companies report in the next two weeks?");
        tickers.forEach(function (t) { mk(t + " last quarter", t + " quarterly results"); });
        break;
      case "holders_increase_industry":
        var ind = e.industry && e.industry.label ? e.industry.label.replace(/\s*\(.*\)$/, "") : null;
        if (ind) mk("Largest " + ind + " by market value", "Largest " + ind + " by market value");
        tickers.forEach(function (t) { mk("Who owns " + t, "Who are the biggest holders of " + t + "?"); });
        break;
      case "manager_portfolio":
        colVals(a, "Issuer", 2).forEach(function (n) {
          mk("Who else owns " + titled(n), "Who are the biggest holders of " + titled(n) + "?");
        });
        break;
      case "banks_vs_yield":
        var banks = e.banks || [];
        if (banks.length >= 2) mk("Compare " + banks[0] + " and " + banks[1], "Compare " + banks[0] + " and " + banks[1]);
        mk("Cheapest large banks by P/E", "Which large banks trade on the lowest P/E?");
        break;
    }
  }

  function followupsFor(a) {
    var out = [];
    var co = a.entities && a.entities.company;
    var cos = (a.entities && a.entities.mentioned_companies) || [];
    var market = !co && MARKET_RECIPES.indexOf(a.recipe) >= 0;
    var name = market ? null : co ? (co.ticker || co.name) : (cos[0] ? (cos[0].ticker || cos[0].name) : null);
    var mk = function (label, q) {
      if (!out.some(function (f) { return f.q.toLowerCase() === q.toLowerCase(); }) &&
          q.toLowerCase().replace(/[?.!\s]+$/, "") !== String(a.question || "").toLowerCase().replace(/[?.!\s]+$/, "")) {
        out.push({ label: label, q: q });
      }
    };
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
    } else if (market) {
      marketFollowups(a, mk);
    } else {
      // an answer worked out step by step: the companies it leads with
      colVals(a, "Ticker", 3).forEach(function (t) { mk("What is " + t + " worth?", "What is " + t + " worth?"); });
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

  // What a ranking assumed (app/stock_recipes.py _read_as): what "tech" means
  // here, the switches that change the reading, and a warning only when a
  // switch changes the list. A switch is part of the link, so any view is citable.
  function readHtml(r) {
    if (!r || !r.switches) return "";
    var on = r.opts || [];
    var sw = r.switches.map(function (x) {
      var next = x.on ? on.filter(function (o) { return o !== x.key; }) : on.concat([x.key]);
      return "<a href='" + esc(answerUrl(lastAsked, next.join(","))) + "' role='button' aria-pressed='" + (x.on ? "true" : "false") +
        "' data-opt='" + esc(next.join(",")) + "'>" + (x.on ? "\u2713 " : "") + esc(x.label) + "</a>";
    }).join("");
    if (!sw && !r.means && !r.warning) return "";
    return "<div class='an-read'>" + (r.means ? "<p class='an-read-means'>" + esc(r.means) + "</p>" : "") +
      (sw ? "<div class='an-switches' role='group' aria-label='Change how the question is read'>" + sw + "</div>" : "") +
      (r.warning ? "<p class='an-read-warn'>" + esc(r.warning) + "</p>" : "") + "</div>";
  }
  // other common readings of the question (app/us_ask_model.py alternatives): each a whole question, one click away
  function dymHtml(list) {
    if (!list || !list.length) return "";
    return "<div class='an-dym'><span>Did you mean</span>" + list.map(function (d) {
      return "<a href='" + esc(answerUrl(d.question, "")) + "' title='" + esc(d.question) + "'>" + esc(d.label) + "</a>";
    }).join("") + "</div>";
  }
  function wireDym(el) {
    Array.prototype.forEach.call(el.querySelectorAll(".an-dym a"), function (x) {
      x.addEventListener("click", function (ev) {
        var nq = new URL(x.href, location.href).searchParams.get("q");
        if (!nq) return;
        ev.preventDefault();
        ask(nq, true);
      });
    });
  }
  function answerUrl(q, opt) {
    return "/?q=" + encodeURIComponent(q) + (opt ? "&opt=" + encodeURIComponent(opt) : "");
  }

  var FOLD_ROWS = 10;
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
    var link = a.links && a.links.company ? a.links.company : (co && co.ticker ? "/us-stock.html?t=" + encodeURIComponent(co.ticker) : null);
    var logo = co && co.ticker && window.usLogos ? usLogos.img(co.ticker, 16) : "";
    var src = (a.sources || []).map(function (s) { return s.credit; }).join(" ");
    var readAs = a.match && a.match.read_as ? String(a.match.read_as).replace(/\d{4}-\d{2}-\d{2}/g, function (d) { return dateWords(d); }) : "";
    var when = a.period && a.period.text ? a.period.text : (typeof calc.period === "string" ? calc.period.replace(/\s*\(\d{4}-.*\)$/, "") : "");
    var hasTable = !!(a.table && a.table.rows && a.table.rows.length);
    var dym = (a.did_you_mean || []).filter(function (d) { return d && d.question; });
    var stamp = "Plover Analytics, " + location.origin + answerUrl(a.question || lastAsked, lastOpt);
    var csvHead = [a.headline, src, "Formulas: " + Object.keys(calc).filter(function (k) { return k !== "query" && k !== "steps"; })
      .map(function (k) { return calcLabel(k) + " = " + calc[k]; }).join("; "), stamp];

    // the main column: the question and its answer, the figures, the chart, the table, a follow-up
    var main = panel("", "",
      "<div class='an-asked'><span>You asked</span><b>" + esc(a.question || lastAsked) + "</b>" +
        (co && link ? "<a class='an-co' href='" + esc(link) + "'>" + logo + esc(co.name) + "</a>" : "") +
        (when ? "<span class='when'>" + esc(when) + "</span>" : "") + "</div>" +
      // a ranking always says what it assumed; otherwise the reading matters only when the
      // question was read as another (a follow-up made whole)
      (a.read && a.read.text ? "<p class='quiet read-as'><b>Read as</b> " + esc(a.read.text) + "</p>" :
        readAs && ((a.question && a.question !== lastAsked) || dym.length) ? "<p class='quiet read-as'><b>Read as</b> " + esc(readAs) + "</p>" : "") +
      // other common readings the question has: each a whole question, one click away (app/us_ask_model.py alternatives)
      dymHtml(dym) +
      "<p class='headline hit'>" + esc(a.headline) + "</p>" +
      (a.answer ? "<p class='answer-text'>" + esc(a.answer) + "</p>" : "") +
      quotesHtml(a.quotes) + readHtml(a.read), "an-q") +
      figuresHtml(a.figures);
    if (a.chart) {
      main += panel(a.chart.title || "Chart", a.chart.unit || "",
        "<div class='an-chart'><div class='chart-el' id='an-chart'></div></div>" +
        "<div class='an-foot'><span>" + esc(src) + "</span><span class='acts'>" +
        "<button type='button' class='an-link' id='an-png'>Download PNG</button>" +
        "<button type='button' class='an-link' id='an-csv'>Download CSV</button></span></div>", "an-chart-panel");
    }
    if (a.lists) main += "<section class='hp-panel an-list'>" + listsHtml(a.lists) + "</section>";
    // a long table shows its first rows; the rest are one click away and always in Copy and CSV
    var nRows = hasTable ? a.table.rows.length : 0, fold = nRows > FOLD_ROWS + 2;
    if (hasTable) {
      main += panel("Detail", count(nRows) + " rows" + (a.table.unit ? " · " + a.table.unit : ""),
        "<div class='an-tbl" + (fold ? " an-fold" : "") + "'>" + tableHtml(a.table, "an-table") + "</div>" +
        (fold || a.more ? "<div class='an-foot an-tbl-foot'>" +
          (fold ? "<button type='button' class='an-link' id='an-all' aria-expanded='false'>Show all " + count(nRows) + " rows</button>" : "") +
          (a.more ? "<a class='an-more' href='" + esc(a.more.href) + "'>" + esc(a.more.label) + "</a>" : "") + "</div>" : ""), "an-detail");
    }
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
    if (fold) {
      var box = el.querySelector(".an-tbl"), allBtn = $("an-all");
      var setFold = function (on) {
        box.classList.toggle("an-fold", on);
        allBtn.setAttribute("aria-expanded", on ? "false" : "true");
        allBtn.textContent = on ? "Show all " + count(nRows) + " rows" : "Show the first " + FOLD_ROWS;
      };
      allBtn.onclick = function () { setFold(!box.classList.contains("an-fold")); };
      // typing in the table's filter searches every row, so the fold opens
      el.querySelector(".an-detail").addEventListener("input", function () { setFold(false); });
    }
    $("an-copy").onclick = function () { copyText(this, location.origin + answerUrl(a.question || lastAsked, lastOpt)); };
    $("an-cite").onclick = function () { copyText(this, a.headline + " " + stamp + " (answered " + dateWords(new Date().toISOString()) + ")."); };
    $("an-ask").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var nq = $("an-q").value.trim();
      if (nq) { window.scrollTo(0, 0); ask(nq, true); }
    });
    wireDym(el);
    // a switch asks the same question again under the other reading
    Array.prototype.forEach.call(el.querySelectorAll(".an-switches a"), function (x) {
      x.addEventListener("click", function (ev) {
        ev.preventDefault();
        ask(lastAsked, true, null, x.getAttribute("data-opt"));
      });
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
    // an answer made by code alone (the quick lane) has not been read by the model: its other
    // readings are fetched afterwards and added above the headline, so the answer is never held up
    var m = a.match || {}, asked = a.question || lastAsked;
    if (!dym.length && (m.via === "quick" || m.via === "patterns")) {
      getJSON(API + "/ask/readings?q=" + encodeURIComponent(asked)).then(function (r) {
        var list = (r.did_you_mean || []).filter(function (d) { return d && d.question; });
        var head = el.querySelector(".headline");
        if (!list.length || !head || lastAsked !== asked || el.querySelector(".an-dym")) return;
        head.insertAdjacentHTML("beforebegin", dymHtml(list));
        wireDym(el);
      }, function () { /* no other readings: the answer stands as it is */ });
    }
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
  var lastOpt = "";
  function ask(q, push, via, opt) {
    via = exampleVia(q, via);
    opt = opt || "";
    placeBox(true);
    window.scrollTo(0, 0);
    // The last answer and the front page's examples go at once: the question
    // shows straight away in a card where its answer will appear, with the
    // server's steps as they happen, and the answer takes that card's place.
    // A previous answer stays, faded back, until the new one replaces it: the
    // answer area changes only when an answer arrives.
    var el = $("answer");
    if (!el.hidden && el.innerHTML) el.classList.add("an-stale");
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
    lastOpt = opt;
    if (push) history.replaceState({ q: q }, "", answerUrl(q, opt));
    // The previous question in this tab goes with the new one, so a follow-up
    // ("and Microsoft?") is read as a whole question; the answer shows that reading.
    var prev = prevQuestion();
    var tail = (prev && prev !== q ? "&prev=" + encodeURIComponent(prev) : "") +
      (via ? "&via=" + encodeURIComponent(via) : "") + (opt ? "&opt=" + encodeURIComponent(opt) : "");
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

  // the sign-up panel under the examples; the bar's own account control is nav.js's
  function account() {
    var back = "signin.html?returnTo=" + encodeURIComponent("/" + location.search);
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

  /* ---- routing --------------------------------------------------------- */
  // A company's Price and Insiders and holders tabs are a page of their own
  // since 2026-10-28 (us-stock.html). Drawn here, under the front page, the
  // front page showed for two seconds before the company replaced it. The
  // server forwards an old /?t= link there (app/main.py) before this runs.
  function stockPage(t) {
    return "/us-stock.html?t=" + encodeURIComponent(String(t).toUpperCase().replace(/\s+/g, ""));
  }
  // Signed out, a ticker is the start of its answer, as any question is.
  function openStock(t, push) {
    if (gated()) return ask(t, push);
    location[push ? "assign" : "replace"](stockPage(t));
  }
  function route(push) {
    var u = new URL(location.href);
    var q = (u.searchParams.get("q") || "").trim();
    if (q) {
      // a bare ticker typed into the box opens the company page
      if (/^[A-Za-z]{1,5}([.-][A-Za-z])?$/.test(q) && q.toUpperCase() === q) return openStock(q, false);
      return ask(q, false, null, (u.searchParams.get("opt") || "").trim());
    }
    $("answer").hidden = true;
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
