/* index.html: the front page's tasks, Research a Stock, Earnings Preview, Draw
   Charts, Screen Stocks and Insider Buying. Each walks the reader through a few steps, one choice at a
   time, with suggestions at every step and the next things to do once the
   result shows.

   The address carries the task and every choice, so any step can be linked
   to and Back works: /?task=research&c=AMD, /?task=earnings&c=AMD, /?task=chart&m=price&s=AAPL,MSFT&p=5y,
   /?task=screen&size=large&f=growth,cheap, /?task=insiders&who=bosses&d=30.

   - Research a Stock reads /api/v1/us/research (app/us_research_api.py).
   - Earnings Preview reads /api/v1/us/preview (app/us_preview_api.py).
   - A chart is a question for the box (us-stocks.js ask()), worded the way the
     box's fixed answers read it: share prices against the S&P 500 (up to five
     companies), revenue or net profit by year (up to two). Those wordings were
     tried against the live box on 2026-11-03; others fall to the step route,
     which may answer with a table and no chart.
   - A screen runs the screener (/api/v1/us/financials/screener) with the
     reader's choices as its filters.
   - Insider Buying reads /api/v1/us/insiders/buying (app/sec_insiders_api.py).

   Nothing here computes a figure: every number is the server's. */
"use strict";

(function () {
  var API = "/api/v1/us";
  var panel = document.getElementById("task");
  if (!panel) return;
  var usFmt = window.usFmt;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function getJSON(url) { return usFmt.getJSON(url); }
  var money = usFmt.money, dateWords = usFmt.dateWords;
  function pct(v, signed) { return usFmt.pct(v, 1, signed); }
  function arrow(v) {
    if (v == null) return MISSING;
    return "<span class='" + (v > 0 ? "up" : v < 0 ? "down" : "") + "'>" + (v > 0 ? "▲ " : v < 0 ? "▼ " : "") + pct(v, true) + "</span>";
  }
  // the value a sortable column ranks a cell by (sortable.js), where the text has a unit or an arrow
  function ds(v) { return " data-sort='" + (v == null ? "" : esc(v)) + "'"; }
  function weekday(iso) {
    var d = new Date(iso.slice(0, 10) + "T00:00:00Z");
    return ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][d.getUTCDay()];
  }
  function shortDay(iso) {
    var d = new Date(iso.slice(0, 10) + "T00:00:00Z");
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()].slice(0, 3);
  }
  function logo(c, size) {
    return c && c.logo ? "<img src='" + esc(c.logo) + "' alt='' width='" + size + "' height='" + size + "' loading='lazy'>" : "<i class='tk-nologo' aria-hidden='true'></i>";
  }

  // who is reading: a signed-in reader saves into Your Plover, others are offered an account
  var me = { email: null, on: false };
  var meReady = fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
    if (r.status === 404) return null;
    me.on = true;
    return r.ok ? r.json() : null;
  }).then(function (who) {
    me.email = (who && who.email) || null;
    // the own-condition box depends on who is reading: draw it again once that is known
    if (cur.task === "screen" && $("tk-own")) screenStep(2);
  }, function () {});
  function saveLink(target) {
    return me.email || !me.on ? target : "signin.html?returnTo=" + encodeURIComponent(target);
  }

  /* ---- the panel ------------------------------------------------------ */
  var TITLES = { research: "Research a Stock", earnings: "Earnings Preview", chart: "Draw Charts", screen: "Screen Stocks", insiders: "Insider Buying" };
  var STEPS = {
    research: ["Choose a Company", "Choose What Goes In", "Read and Download"],
    earnings: ["Choose a Company", "Read the Preview"],
    chart: ["What to Chart", "Companies", "Period"],
    screen: ["Company Size", "Sector", "What to Look For", "Results"],
    insiders: ["Who Is Buying", "Period and Size", "Results"],
  };
  var cur = { task: null };

  /* ---- how far readers get (app/task_log.py) ---- */
  // Sent like the page's reading time: a beacon, never retried, never in the way.
  // Each step once in a row; a result again only when its choices change.
  var RESULT_AT = { research: 2, earnings: 1, chart: 3, screen: 3, insiders: 2 };
  var lastNote = "";
  function note(task, step, extra) {
    var body = { path: "/", task: task, step: step };
    Object.keys(extra || {}).forEach(function (k) { if (extra[k]) body[k] = extra[k]; });
    var key = JSON.stringify(body) + (step === "result" && !body.pick ? location.search : "");
    if (key === lastNote) return;
    lastNote = key;
    try {
      if (navigator.sendBeacon) navigator.sendBeacon("/api/v1/visit/ping", JSON.stringify(body));
    } catch (e) { /* counting must never break a page */ }
  }
  function pickOf(task) {
    if (task === "research" || task === "earnings") return cur.t || "";
    if (task === "chart") return cur.c && cur.c.m || "";
    return "";
  }
  function noteDrawn(task, at, body) {
    if (body.indexOf("<p class='tk-wait'") === 0) return;       // still loading
    if (body.indexOf("<p class='tk-err'") === 0) return note(task, "failed");
    if (at >= RESULT_AT[task]) return note(task, "result", { pick: pickOf(task) });
    note(task, at);
  }
  window.ploverNote = note;   // home-demos.js: a demonstration picked by hand

  function stepsHtml(task, at) {
    var names = task === "chart" && cur.c && cur.c.m === "sectors" ? ["What to Chart", "Sectors", "Period"] : STEPS[task];
    return "<ol class='tk-steps' aria-label='Steps'>" + names.map(function (n, i) {
      return "<li class='" + (i === at ? "on" : i < at ? "done" : "") + "'" + (i === at ? " aria-current='step'" : "") + "><b>" + (i + 1) + "</b>" + esc(n) + "</li>";
    }).join("") + "</ol>";
  }
  function draw(task, at, body, foot) {
    panel.innerHTML = "<div class='tk-head'><h2>" + esc(TITLES[task]) + "</h2>" + stepsHtml(task, at) +
      "<button type='button' class='tk-close' id='tk-close'>Close <span aria-hidden='true'>×</span></button></div>" +
      "<div class='tk-body'>" + body + "</div>" + (foot ? "<div class='tk-foot'>" + foot + "</div>" : "");
    panel.hidden = false;
    $("tk-close").onclick = function () { close(true); };
    noteDrawn(task, at, body);
  }
  function busy(text) { return "<p class='tk-wait' role='status'>" + esc(text) + "</p>"; }
  function failed(text, retry) {
    return "<p class='tk-err' role='alert'>" + esc(text) + (retry ? " <button type='button' class='an-link' id='tk-retry'>Try again</button>" : "") + "</p>";
  }

  // the address holds the task and its choices; Back steps through them
  function urlFor(params, over) {
    var u = new URLSearchParams(), all = {};
    Object.keys(params).forEach(function (k) { all[k] = params[k]; });
    Object.keys(over || {}).forEach(function (k) { all[k] = over[k]; });
    Object.keys(all).forEach(function (k) { if (all[k]) u.set(k, all[k]); });
    return "/?" + u.toString().replace(/%2C/g, ",");
  }
  function setUrl(params, push) {
    var url = urlFor(params);
    if (location.pathname + location.search === url) return;
    history[push ? "pushState" : "replaceState"]({ task: params.task }, "", url);
  }
  function enter() {
    if (window.ploverHome) ploverHome.enterTask();
    $("answer").hidden = true;
    $("home-note").hidden = true;
    window.scrollTo(0, 0);
  }
  function close(push) {
    panel.hidden = true;
    panel.innerHTML = "";
    cur = { task: null };
    lastNote = "";
    if (push) {
      history.pushState(null, "", "/");
      if (window.ploverHome) ploverHome.home();
    }
  }

  /* ---- a company picker: type a name or ticker, pick from the list ---- */
  // Companies with a logo on file are the larger listed ones: they go first.
  function picker(el, placeholder, onPick) {
    el.innerHTML = "<div class='tk-find'><label class='fp-sr' for='tk-co'>Company name or ticker</label>" +
      "<input type='search' id='tk-co' autocomplete='off' spellcheck='false' maxlength='80' placeholder='" + esc(placeholder) + "'>" +
      "<ul class='tk-co-list' id='tk-co-list' role='listbox' hidden></ul></div>";
    var input = $("tk-co"), list = $("tk-co-list"), seq = 0, timer = null, hits = [];
    function show(rows, text) {
      hits = rows;
      list.innerHTML = text ? "<li class='tk-none'>" + esc(text) + "</li>" : rows.map(function (c, i) {
        return "<li role='option' data-i='" + i + "'><button type='button'>" + logo(c, 20) + "<span class='n'>" + esc(c.name) + "</span><span class='t'>" + esc(c.ticker) + "</span></button></li>";
      }).join("");
      list.hidden = false;
    }
    function search() {
      var q = input.value.trim(), n = ++seq;
      if (!q) { list.hidden = true; return; }
      getJSON(API + "/company?q=" + encodeURIComponent(q)).then(function (d) {
        if (n !== seq) return;
        var rows = (d.companies || []).filter(function (c) { return c.ticker; });
        rows.sort(function (a, b) { return (b.logo ? 1 : 0) - (a.logo ? 1 : 0); });
        rows = rows.slice(0, 8);
        show(rows, rows.length ? "" : "No listed US company matches “" + q + "”. Try its ticker, e.g. AAPL.");
      }, function () { if (n === seq) show([], "The company list could not be reached. Check your connection, then type again."); });
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 150); });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); if (hits[0]) onPick(hits[0]); else search(); }
      if (ev.key === "Escape") list.hidden = true;
    });
    list.addEventListener("click", function (ev) {
      var li = ev.target.closest("li[data-i]");
      if (li) onPick(hits[+li.getAttribute("data-i")]);
    });
    return input;
  }

  /* ================================================== Earnings Preview */
  // how long after each results day the share price move runs: trading days
  var WINDOWS = [[1, "Day After", "the close the trading day after"], [5, "Week After", "the close 5 trading days after"],
                 [21, "Month After", "the close 21 trading days after"]];
  function windowOf(w) { return WINDOWS.filter(function (x) { return x[0] === w; })[0] || WINDOWS[0]; }
  function earnings(t, push, w) {
    cur = { task: "earnings", t: t || "", w: windowOf(w)[0] };
    // "c", not "t": the server forwards /?t= to the stock page (app/main.py)
    setUrl({ task: "earnings", c: t, w: cur.w > 1 ? String(cur.w) : "" }, push);
    enter();
    if (t) return earningsPreview(t);
    draw("earnings", 0, "<p class='tk-ask'>Which company? Type a name or ticker, or pick one reporting soon.</p>" +
      "<div id='tk-pick'></div><div id='tk-soon'>" + busy("Finding the S&P 500 companies expected to report soon…") + "</div>");
    picker($("tk-pick"), "e.g. AMD", function (c) { earnings(c.ticker, true); }).focus();
    getJSON(API + "/preview/upcoming?days=14&limit=12").then(function (d) {
      if (cur.task !== "earnings" || cur.t) return;
      var row = function (c, when) {
        return "<li><button type='button' data-t='" + esc(c.ticker) + "'><span class='d'>" + esc(when) + "</span>" + logo(c, 20) +
          "<span class='n'>" + esc(c.name) + "</span><span class='t'>" + esc(c.ticker) + "</span></button></li>";
      };
      var html = "";
      if (d.expected.length) {
        html += "<h3>Expected to Report in the Next Two Weeks</h3><ul class='tk-cos'>" +
          d.expected.map(function (c) { return row(c, shortDay(c.expected_date)); }).join("") + "</ul>" +
          (d.expected_count > d.expected.length ? "<p class='tk-note'>The " + d.expected.length + " soonest of " + d.expected_count + ", largest first on each day.</p>" : "") +
          "<p class='tk-note'>Dates are estimates: a year after each company's results for the same quarter. Companies set their own dates.</p>";
      }
      if (d.reported.length) {
        html += "<h3>Reported in the Last Week</h3><ul class='tk-cos'>" +
          d.reported.map(function (c) { return row(c, shortDay(c.results_date)); }).join("") + "</ul>";
      }
      $("tk-soon").innerHTML = html || "<p class='tk-note'>No S&P 500 company is expected to report in the next two weeks. Type any company above.</p>";
      Array.prototype.forEach.call($("tk-soon").querySelectorAll("button[data-t]"), function (b) {
        b.onclick = function () { earnings(b.getAttribute("data-t"), true); };
      });
    }, function () {
      if ($("tk-soon")) $("tk-soon").innerHTML = "<p class='tk-note'>The list of companies reporting soon could not be loaded. Type any company above.</p>";
    });
  }

  function earningsPreview(t) {
    draw("earnings", 1, busy("Putting together the preview for " + t.toUpperCase() + "…"));
    var w = cur.w;
    getJSON(API + "/preview/" + encodeURIComponent(t) + "?after=" + w).then(function (p) {
      if (cur.task !== "earnings" || cur.t !== t || cur.w !== w) return;
      renderPreview(p);
    }, function (e) {
      if (cur.task !== "earnings" || cur.t !== t) return;
      draw("earnings", 1, failed(e.status === 404 ? "No listed US company has the ticker " + t.toUpperCase() + ". Choose another company."
        : (e.message || "The preview could not be loaded.") + " Check your connection.", e.status !== 404),
        "<button type='button' class='tk-back' id='tk-other'>Choose Another Company</button>");
      $("tk-other").onclick = function () { earnings("", true); };
      if ($("tk-retry")) $("tk-retry").onclick = function () { earningsPreview(t); };
    });
  }

  function renderPreview(p) {
    var co = p.company, n = p.next, last = p.last_results, since = p.since_results;
    var today = new Date().toISOString().slice(0, 10);
    var recent = last && (Date.parse(today) - Date.parse(last.date)) / 864e5 <= 14;
    var head, sub = "";
    if (recent) {
      head = co.name + " reported results on " + weekday(last.date) + " " + dateWords(last.date) + ".";
      if (n) sub = "The next results are expected around " + dateWords(n.date) + ".";
    } else if (n && n.overdue) {
      head = co.name + "'s results were expected around " + dateWords(n.date) + " and are not on file yet.";
      sub = "That date is a year after its results for the same quarter, on " + dateWords(n.based_on) + ".";
    } else if (n) {
      head = co.name + " is expected to report results around " + weekday(n.date) + " " + dateWords(n.date) + ".";
      sub = "An estimate: a year after its results for the same quarter, on " + dateWords(n.based_on) + ". The company sets the date.";
    } else {
      head = "No results days are on file for " + co.name + ", so no date can be estimated.";
    }
    var q = p.quarters.slice().reverse(), lq = q[0];
    var figs = "";
    if (p.typical_move_pct != null) {
      figs += "<div><dt>Typical Move, " + esc(windowOf(p.after)[1]) + " Results</dt><dd>" + pct(p.typical_move_pct) + "<small>either way, median of the last " +
        p.reactions.filter(function (r) { return r.move_pct != null; }).length + "</small></dd></div>";
    }
    if (since) {
      figs += "<div><dt>Since the Last Results</dt><dd>" + arrow(since.move_pct) + "<small>S&amp;P 500 " +
        (since.sp500_pct == null ? MISSING : pct(since.sp500_pct, true)) + ", to " + esc(dateWords(since.to)) + "</small></dd></div>";
    }
    if (lq) {
      figs += "<div><dt>Revenue, " + esc(lq.fiscal) + "</dt><dd>" + money(lq.revenue) + "<small>" +
        (lq.revenue_growth_pct == null ? "no year-earlier quarter on file" : pct(lq.revenue_growth_pct, true) + " on a year earlier") + "</small></dd></div>";
    }
    var qRows = q.map(function (r) {
      return "<tr><td>" + esc(r.fiscal) + (r.q4_derived ? " *" : "") + "</td><td class='tk-sm-hide'>" + esc(dateWords(r.quarter_end)) + "</td>" +
        "<td class='r'>" + money(r.revenue) + "</td><td class='r'>" + (r.revenue_growth_pct == null ? MISSING : pct(r.revenue_growth_pct, true)) + "</td>" +
        "<td class='r'>" + (r.operating_margin_pct == null ? MISSING : pct(r.operating_margin_pct)) + "</td>" +
        "<td class='r'>" + (r.eps == null ? MISSING : (r.eps < 0 ? MINUS : "") + "$" + fmtNum(Math.abs(r.eps), 2)) + "</td></tr>";
    }).join("");
    var rRows = p.reactions.map(function (r) {
      return "<tr><td>" + esc(dateWords(r.date)) + "</td><td>" + esc(r.fiscal || (r.quarter_end ? "Quarter to " + dateWords(r.quarter_end) : MISSING)) + "</td>" +
        "<td class='r'>" + arrow(r.move_pct) + "</td><td class='r'>" + (r.sp500_pct == null ? MISSING : pct(r.sp500_pct, true)) + "</td></tr>";
    }).join("");
    var src = p.sources.map(function (s) { return esc(s.credit.replace(/^Source:\s*/, "")); }).join(" · ");
    var calc = Object.keys(p.calc).map(function (k) {
      return "<li><b>" + esc(CALC_NAMES[k] || k) + "</b> = " + esc(p.calc[k]) + "</li>";
    }).join("");
    var tk = co.ticker;
    var body =
      "<div class='tk-co-head'>" + logo(co, 28) + "<b>" + esc(co.name) + "</b><span class='t'>" + esc(tk) + "</span>" +
        "<a href='" + esc(co.page) + "'>Open the company page</a></div>" +
      "<p class='tk-headline hit'>" + esc(head) + "</p>" + (sub ? "<p class='tk-sub'>" + esc(sub) + "</p>" : "") +
      (figs ? "<dl class='an-figs tk-figs'>" + figs + "</dl>" : "") +
      (qRows ? "<h3>Last " + q.length + " Quarters</h3><div class='tk-table'><table class='tk-tab' data-no-enhance><thead><tr><th>Quarter</th><th class='tk-sm-hide'>Quarter End</th>" +
        "<th class='r'>Revenue</th><th class='r'>Revenue Growth, y/y</th><th class='r'>Operating Margin</th><th class='r'>Diluted EPS</th></tr></thead><tbody>" + qRows +
        "</tbody></table></div>" + (q.some(function (r) { return r.q4_derived; }) ? "<p class='tk-note'>* The full year less the first three quarters: no filing reports this quarter on its own, so earnings per share is not shown.</p>" : "")
        : "<p class='tk-note'>No quarterly figures are on file for " + esc(co.name) + ".</p>") +
      (rRows ? "<h3>Share Price Around Results</h3>" +
        "<div class='tk-cards tk-cards-sm tk-win' role='radiogroup' aria-label='How long after results'>" + WINDOWS.map(function (x) {
          return "<button type='button' role='radio' aria-checked='" + (p.after === x[0]) + "' class='tk-card" + (p.after === x[0] ? " on" : "") + "' data-w='" + x[0] + "'><b>" + esc(x[1]) + "</b></button>";
        }).join("") + "</div><div class='tk-table'><table class='tk-tab' data-no-enhance><thead><tr><th>Results Day</th><th>Quarter</th>" +
        "<th class='r'>" + esc(tk) + "</th><th class='r'>S&amp;P 500</th></tr></thead><tbody>" + rRows + "</tbody></table></div>" +
        "<p class='tk-note'>From the close before each results day to " + esc(windowOf(p.after)[2]) + ". A results day too recent for that shows —.</p>" : "") +
      "<details class='tk-calc'><summary>Show calculation</summary><ul>" + calc + "</ul></details>" +
      "<p class='tk-src'>Sources: " + src + (p.price ? " Prices to " + esc(dateWords(p.price.date)) + "." : "") + "</p>";
    var nexts = [
      ["chart", "Chart " + tk + " Against the S&P 500"],
      ["ask", "Ask About " + tk + "'s Latest Quarter"],
      ["own", "Who Owns " + tk + "?"],
      ["other", "Preview Another Company"],
    ];
    draw("earnings", 1, body, "<span class='tk-next-h'>Next Step</span>" + nexts.map(function (x, i) {
      return "<button type='button' class='" + (i ? "tk-opt" : "tk-go") + "' data-next='" + x[0] + "'>" + esc(x[1]) + "</button>";
    }).join(""));
    document.title = co.name + " Earnings Preview · Plover Analytics";
    Array.prototype.forEach.call(panel.querySelectorAll("[data-w]"), function (b) {
      b.onclick = function () {
        var w = +b.getAttribute("data-w");
        if (w === cur.w) return;
        cur.w = w;
        setUrl({ task: "earnings", c: cur.t, w: w > 1 ? String(w) : "" }, false);
        Array.prototype.forEach.call(panel.querySelectorAll("[data-w]"), function (x) {
          var on = +x.getAttribute("data-w") === w;
          x.classList.toggle("on", on);
          x.setAttribute("aria-checked", String(on));
          x.disabled = true;
        });
        refreshPreview(cur.t, w);
      };
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-next]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-next");
        if (k === "chart") return chartStart({ m: "price", s: [{ ticker: tk, name: co.name }], p: "1y" }, true, true);
        if (k === "ask") return ask(tk + " quarterly results");
        if (k === "own") return ask("Who owns " + tk + "?");
        earnings("", true);
      };
    });
  }
  // a new window: the table and the typical move change, the rest of the page stays where it is
  function refreshPreview(t, w) {
    getJSON(API + "/preview/" + encodeURIComponent(t) + "?after=" + w).then(function (p) {
      if (cur.task !== "earnings" || cur.t !== t || cur.w !== w) return;
      var y = window.scrollY;
      renderPreview(p);
      window.scrollTo(0, y);
    }, function (e) {
      if (cur.task !== "earnings" || cur.t !== t) return;
      var note = panel.querySelector(".tk-win");
      if (note) note.insertAdjacentHTML("afterend", failed((e.message || "The share price moves could not be loaded.") + " Check your connection, then choose again."));
      Array.prototype.forEach.call(panel.querySelectorAll("[data-w]"), function (x) { x.disabled = false; });
    });
  }
  var CALC_NAMES = {
    expected_date: "Expected date", results_move: "Move around results", sp500_move: "S&P 500 move",
    typical_move: "Typical move", since_results: "Since the last results", operating_margin: "Operating margin",
    revenue_growth: "Revenue growth", q4: "Fourth quarter",
  };

  function ask(q) {
    close(false);
    if (window.ploverHome) ploverHome.ask(q);
  }

  /* ======================================================= Draw Charts */
  var MEASURES = [
    { id: "price", label: "Share Price", note: "Against the S&P 500, up to five companies", max: 5 },
    { id: "revenue", label: "Revenue", note: "By year, as filed, one or two companies", max: 2 },
    { id: "profit", label: "Net Profit", note: "By year, as filed, one or two companies", max: 2 },
    // drawn here from the sector funds' closes, not asked of the box: it answers sectors in words only
    { id: "sectors", label: "Sector Performance", note: "The S&P 500's sectors against the S&P 500, up to five", max: 5 },
  ];
  var PERIODS = {
    price: [["ytd", "This Year", "this year"], ["1y", "Past Year", "over the last year"], ["5y", "Past 5 Years", "over the last 5 years"]],
    fin: [["5y", "Past 5 Years", "over the last 5 years"], ["10y", "Past 10 Years", "over the last 10 years"]],
  };
  var POPULAR = [["AAPL", "Apple"], ["MSFT", "Microsoft"], ["NVDA", "Nvidia"], ["AMZN", "Amazon"], ["GOOGL", "Alphabet"], ["META", "Meta"], ["TSLA", "Tesla"], ["JPM", "JPMorgan"]];
  function measure(id) { return MEASURES.filter(function (m) { return m.id === id; })[0] || null; }
  function periods(m) { return m === "price" || m === "sectors" ? PERIODS.price : PERIODS.fin; }
  function and(list) { return list.length < 2 ? list.join("") : list.slice(0, -1).join(", ") + " and " + list[list.length - 1]; }

  // the question the box answers with a chart (see the note at the top)
  function chartQuestion(c) {
    var ts = c.s.map(function (x) { return x.ticker; });
    var per = (periods(c.m).filter(function (p) { return p[0] === c.p; })[0] || periods(c.m)[0])[2];
    if (c.m === "price") return "How have " + and(ts) + " shares done against the S&P 500 " + per + "?";
    return and(ts) + (c.m === "revenue" ? " revenue " : " net profit ") + per;
  }
  function chartWords(c) {
    var names = and(c.s.map(function (x) { return x.name || x.ticker; }));
    var per = (periods(c.m).filter(function (p) { return p[0] === c.p; })[0] || periods(c.m)[0])[1].toLowerCase();
    return names + ": " + (c.m === "price" || c.m === "sectors" ? "share price against the S&P 500" : c.m === "revenue" ? "revenue by year" : "net profit by year") + ", " + per;
  }
  function chartParams(c) {
    return { task: "chart", m: c.m, s: c.s.map(function (x) { return x.ticker; }).join(","), p: c.p };
  }

  function chartStart(c, push, drawNow) {
    c = { m: c.m || "", s: (c.s || []).slice(), p: c.p || "" };
    cur = { task: "chart", c: c };
    enter();
    var m = measure(c.m);
    if (m && c.s.length > m.max) c.s = c.s.slice(0, m.max);
    if (m && c.p && !periods(c.m).some(function (p) { return p[0] === c.p; })) c.p = "";
    if (drawNow && m && c.s.length && c.p) return chartDraw(c, push);
    var at = !m ? 0 : !c.s.length ? 1 : 2;
    setUrl(chartParams(c), push);
    chartStep(at);
  }

  function chartStep(at) {
    var c = cur.c, m = measure(c.m), body, foot;
    if (at === 0) {
      body = "<p class='tk-ask'>What would you like to chart?</p><div class='tk-cards' role='radiogroup'>" + MEASURES.map(function (x) {
        return "<button type='button' role='radio' aria-checked='" + (c.m === x.id) + "' class='tk-card" + (c.m === x.id ? " on" : "") + "' data-m='" + x.id + "'><b>" + esc(x.label) + "</b><span>" + esc(x.note) + "</span></button>";
      }).join("") + "</div>" + ownChartHtml();
      foot = "";
      draw("chart", 0, body, foot);
      wireOwnChart();
      Array.prototype.forEach.call(panel.querySelectorAll("[data-m]"), function (b) {
        b.onclick = function () {
          var was = c.m;
          c.m = b.getAttribute("data-m");
          if ((was === "sectors") !== (c.m === "sectors")) c.s = [];    // sectors and companies are different lists
          var mm = measure(c.m);
          if (c.s.length > mm.max) c.s = c.s.slice(0, mm.max);
          if (!periods(c.m).some(function (p) { return p[0] === c.p; })) c.p = "";
          setUrl(chartParams(c), false);
          chartStep(c.s.length ? 2 : 1);
        };
      });
      return;
    }
    if (at === 1 && c.m === "sectors") {
      // the S&P's eleven sectors; on a sector chart an item's "ticker" is the sector's key
      body = "<p class='tk-ask'>Which sectors? Up to " + m.max + ".</p><div class='tk-cards tk-cards-grid' role='group' aria-label='Sectors'>" + SECTORS.map(function (x) {
        var on = c.s.some(function (y) { return y.ticker === x[0]; });
        return "<button type='button' aria-pressed='" + on + "' class='tk-card" + (on ? " on" : "") + "' data-sk='" + x[0] + "'" +
          (!on && c.s.length >= m.max ? " disabled" : "") + "><b>" + esc(x[1]) + "</b><span>" + esc(x[2]) + "</span></button>";
      }).join("") + "</div><p class='tk-note'>Each sector is drawn from the fund that holds its S&amp;P 500 companies (the Select Sector SPDRs).</p>";
      foot = "<button type='button' class='tk-back' id='tk-prev'>Back</button>" +
        "<button type='button' class='tk-go' id='tk-next'" + (c.s.length ? "" : " disabled") + ">Next: Choose the Period</button>";
      draw("chart", 1, body, foot);
      Array.prototype.forEach.call(panel.querySelectorAll("[data-sk]"), function (b) {
        b.onclick = function () {
          var k = b.getAttribute("data-sk"), i = -1;
          c.s.forEach(function (y, j) { if (y.ticker === k) i = j; });
          if (i >= 0) c.s.splice(i, 1);
          else if (c.s.length < m.max) c.s.push({ ticker: k, name: sector(k).name });
          setUrl(chartParams(c), false);
          chartStep(1);
        };
      });
      $("tk-prev").onclick = function () { chartStep(0); };
      $("tk-next").onclick = function () { chartStep(2); };
      return;
    }
    if (at === 1) {
      var full = c.s.length >= m.max;
      body = "<p class='tk-ask'>Which " + (m.max > 2 ? "companies? Up to " + m.max + "." : "company? You can add a second to compare.") + "</p>" +
        "<ul class='tk-picked' aria-label='Chosen companies'>" + c.s.map(function (x, i) {
          return "<li>" + esc(x.name || x.ticker) + " <span class='t'>" + esc(x.ticker) + "</span><button type='button' class='tk-x' data-drop='" + i + "' aria-label='Remove " + esc(x.name || x.ticker) + "'>×</button></li>";
        }).join("") + "</ul>" +
        (full ? "<p class='tk-note'>That is the most this chart compares. Remove one to choose another.</p>" :
          "<div id='tk-pick'></div><p class='tk-pop-h'>Or pick:</p><div class='tk-pop'>" + POPULAR.filter(function (x) {
            return !c.s.some(function (y) { return y.ticker === x[0]; });
          }).map(function (x) { return "<button type='button' data-t='" + x[0] + "' data-n='" + esc(x[1]) + "'>" + esc(x[1]) + "</button>"; }).join("") + "</div>");
      foot = "<button type='button' class='tk-back' id='tk-prev'>Back</button>" +
        "<button type='button' class='tk-go' id='tk-next'" + (c.s.length ? "" : " disabled") + ">Next: Choose the Period</button>";
      draw("chart", 1, body, foot);
      var add = function (x) {
        if (c.s.some(function (y) { return y.ticker === x.ticker; }) || c.s.length >= m.max) return;
        c.s.push({ ticker: x.ticker, name: x.name });
        setUrl(chartParams(c), false);
        chartStep(1);
        if ($("tk-co")) $("tk-co").focus();
      };
      if (!full) picker($("tk-pick"), "Type a name or ticker, e.g. Apple", add).focus();
      Array.prototype.forEach.call(panel.querySelectorAll(".tk-pop button"), function (b) {
        b.onclick = function () { add({ ticker: b.getAttribute("data-t"), name: b.getAttribute("data-n") }); };
      });
      Array.prototype.forEach.call(panel.querySelectorAll("[data-drop]"), function (b) {
        b.onclick = function () { c.s.splice(+b.getAttribute("data-drop"), 1); setUrl(chartParams(c), false); chartStep(1); };
      });
      $("tk-prev").onclick = function () { chartStep(0); };
      $("tk-next").onclick = function () { chartStep(2); };
      return;
    }
    // at === 2: the period, then draw
    var ps = periods(c.m);
    if (!c.p) c.p = ps[ps.length - 1][0];
    body = "<p class='tk-ask'>Over what period?</p><div class='tk-cards tk-cards-sm' role='radiogroup'>" + ps.map(function (p) {
      return "<button type='button' role='radio' aria-checked='" + (c.p === p[0]) + "' class='tk-card" + (c.p === p[0] ? " on" : "") + "' data-p='" + p[0] + "'><b>" + esc(p[1]) + "</b></button>";
    }).join("") + "</div><p class='tk-sum'>Your chart: <b>" + esc(chartWords(c)) + "</b></p>";
    foot = "<button type='button' class='tk-back' id='tk-prev'>Back</button><button type='button' class='tk-go' id='tk-next'>Draw the Chart</button>";
    draw("chart", 2, body, foot);
    Array.prototype.forEach.call(panel.querySelectorAll("[data-p]"), function (b) {
      b.onclick = function () { c.p = b.getAttribute("data-p"); setUrl(chartParams(c), false); chartStep(2); };
    });
    $("tk-prev").onclick = function () { chartStep(1); };
    $("tk-next").onclick = function () { chartDraw(c, true); };
  }

  /* A chart in the reader's own words (signed in only), as on Screen Stocks. The
     Charts tool's reader (/api/v1/us/build/chart, the same as Your Plover's)
     turns the sentence into a chart: its companies or sector funds, figure,
     period, benchmarks, scale. The step shows that reading, built from the
     chart itself, and Draw in Your Plover opens the Charts tool on it
     (my.html#charts?…, the address my.js chFromHash reads), ready to change
     and save. */
  function chartHash(spec) {
    var p = new URLSearchParams();
    p.set("s", spec.series.map(function (x) { return x.ticker; }).join(","));
    p.set("m", spec.measure);
    if (spec.from) { p.set("from", spec.from); if (spec.to) p.set("to", spec.to); } else p.set("p", spec.period || "ytd");
    p.set("b", (spec.benchmarks || []).join(",") || "none");
    p.set("d", spec.display);
    if (spec.log) p.set("log", "1");
    if (spec.mark_results) p.set("ev", "1");
    if (spec.chart_type === "bars") p.set("ct", "bars");
    if (spec.title) p.set("title", spec.title);
    return p.toString();
  }
  function ownChartHtml() {
    var signedOut = me.on && !me.email;
    return "<div class='tk-own'><p class='tk-own-h'>Something else? Describe the chart you want.</p>" +
      "<form class='tk-own-row' id='tk-ownc'><label class='fp-sr' for='tk-ownc-q'>Your own chart</label>" +
      "<input type='text' id='tk-ownc-q' maxlength='300' autocomplete='off' placeholder='e.g. Apple share price since 2020 with results days marked'" + (signedOut ? " disabled" : "") + ">" +
      "<button type='submit' class='tk-opt' id='tk-ownc-go'" + (signedOut ? " disabled" : "") + ">Read It</button></form>" +
      (signedOut ? "<p class='tk-note'>Your own charts need a free account. <a href='" + esc("signin.html?returnTo=" + encodeURIComponent("/?task=chart")) +
        "'>Sign in or create one</a>, and you come straight back here.</p>"
        : "<div id='tk-ownc-out' aria-live='polite'></div>") + "</div>";
  }
  function wireOwnChart() {
    var form = $("tk-ownc"), out = $("tk-ownc-out");
    if (!form || !out) return;
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var q = $("tk-ownc-q").value.trim();
      if (!q) return;
      $("tk-ownc-go").disabled = true;
      out.innerHTML = "<p class='tk-note'>Reading “" + esc(q) + "”…</p>";
      getJSON(API + "/build/chart?q=" + encodeURIComponent(q)).then(function (d) {
        if (cur.task !== "chart") return;
        $("tk-ownc-go").disabled = false;
        var spec = d.spec;
        if (!spec || !((spec.series || []).length || (spec.benchmarks || []).length)) {
          out.innerHTML = "<p class='tk-note'>" + esc((d.cannot && d.cannot.text) || "Plover could not turn that into a chart.") +
            " Try a company and a figure, e.g. Nvidia and AMD revenue over 5 years, or a sector, e.g. energy and utilities this year.</p>";
          return;
        }
        out.innerHTML = "<div class='tk-ownc-read'><p><span>Plover will draw</span> <b>" + esc(d.read_as || spec.title || q) + "</b>" +
          (spec.log ? " · log scale" : "") + (spec.mark_results ? " · results days marked" : "") + "</p>" +
          "<a class='hp-btn' href='" + esc("my.html#charts?" + chartHash(spec)) + "'>Draw in Your Plover</a></div>" +
          "<p class='tk-note'>It opens in Your Plover's Charts, where you can change it and save it.</p>";
      }, function (e) {
        if (cur.task !== "chart") return;
        $("tk-ownc-go").disabled = false;
        out.innerHTML = "<p class='tk-note'>" + esc((e.message || "That could not be read.") + " Check your connection, then try again.") + "</p>";
      });
    };
  }

  // The chart is the box's answer; the Next Step bar goes on top of it once it shows.
  var CHART_KEY = "plover-task-chart";
  function chartDraw(c, push) {
    if (c.m === "sectors") return sectorChart(c, push);
    var q = chartQuestion(c);
    try { sessionStorage.setItem(CHART_KEY, JSON.stringify({ q: q, c: c })); } catch (e) { /* no memory: no Next Step bar */ }
    ask(q);
  }
  document.addEventListener("plover:answer", function (ev) {
    var a = ev.detail || {}, kept = null;
    try { kept = JSON.parse(sessionStorage.getItem(CHART_KEY) || "null"); } catch (e) { kept = null; }
    var same = function (x) { return (x || "").toLowerCase() === (kept && kept.q || "").toLowerCase(); };
    if (!kept || !kept.c || !(same(a.question) || same(a.asked))) return;
    var c = kept.c, m = measure(c.m), el = $("answer");
    if (!m || !el || el.querySelector(".tk-nextbar")) return;
    note("chart", "result", { pick: c.m });
    var other = MEASURES.filter(function (x) { return x.id !== c.m && x.id !== "sectors"; });
    var tool = "my.html#charts?s=" + c.s.map(function (x) { return x.ticker; }).join(",") + "&m=" +
      ({ price: "price", revenue: "revenue", profit: "net_income" })[c.m] + "&p=" + (c.p === "10y" ? "max" : c.p) + (c.m === "price" ? "&b=sp500" : "");
    var bar = document.createElement("div");
    bar.className = "hp-panel tk-nextbar";
    bar.innerHTML = "<span class='tk-next-h'>Next Step</span>" +
      (c.s.length < m.max ? "<button type='button' class='tk-opt' data-n='add'>Add a Company</button>" : "") +
      "<button type='button' class='tk-opt' data-n='period'>Change the Period</button>" +
      other.map(function (x) { return "<button type='button' class='tk-opt' data-n='m:" + x.id + "'>Show " + esc(x.label) + " Instead</button>"; }).join("") +
      "<button type='button' class='tk-opt' data-n='new'>Start a New Chart</button>" +
      "<a class='tk-save' href='" + esc(saveLink(tool)) + "'>" + (me.email || !me.on ? "Customise and Save in Your Plover" : "Save This Chart: Free Account") + "</a>";
    el.insertBefore(bar, el.firstChild);
    Array.prototype.forEach.call(bar.querySelectorAll("[data-n]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-n");
        if (k === "new") return chartStart({}, true);
        var next = { m: c.m, s: c.s, p: c.p };
        if (k === "add") { chartStart(next, true); return chartStep(1); }
        if (k === "period") { chartStart(next, true); return chartStep(2); }
        next.m = k.slice(2);
        if (next.s.length > measure(next.m).max) { chartStart({ m: next.m, s: next.s.slice(0, measure(next.m).max) }, true); return chartStep(1); }
        chartStart(next, true, !!next.p && periods(next.m).some(function (p) { return p[0] === next.p; }));
      };
    });
  });

  /* A sector chart, drawn here: each sector fund's daily closes and SPY's from the
     start of the period, each set to 100 at its first close (/api/v1/us/prices).
     The one figure worked out is each line's change, with its formula shown. */
  var SECTOR_CALC = "(last close ÷ first close in the period − 1) × 100, split-adjusted, before dividends; " +
    "each line starts at 100 on its first close in the period";
  function periodStart(p) {
    var d = new Date();
    if (p === "ytd") return d.getUTCFullYear() + "-01-01";
    d.setUTCFullYear(d.getUTCFullYear() - (p === "5y" ? 5 : 1));
    return d.toISOString().slice(0, 10);
  }
  function sectorChart(c, push) {
    var list = c.s.map(function (x) { return sector(x.ticker); }).filter(Boolean);
    var per = (PERIODS.price.filter(function (p) { return p[0] === c.p; })[0] || PERIODS.price[0]);
    var params = chartParams(c);
    params.go = "1";
    setUrl(params, push);
    draw("chart", 3, busy("Drawing " + and(list.map(function (x) { return x.name; })) + " against the S&P 500…"));
    var seq = cur.seq = (cur.seq || 0) + 1, start = periodStart(c.p);
    Promise.all(list.map(function (x) { return x.fund; }).concat(["SPY"]).map(function (f) {
      return getJSON(API + "/prices/" + f + "?start=" + start);
    })).then(function (rs) {
      if (cur.task !== "chart" || cur.seq !== seq) return;
      var lines = rs.map(function (d, i) {
        var bars = (d.bars || []).map(function (b) { return [b.date, b.adjusted_close != null ? b.adjusted_close : b.close]; }).filter(function (b) { return b[1] != null; });
        var base = bars.length ? bars[0][1] : null;
        return { name: i < list.length ? list[i].name : "S&P 500", fund: i < list.length ? list[i].fund : "SPY", key: i < list.length ? list[i].key : null,
                 points: bars.map(function (b) { return [b[0], Math.round(b[1] / base * 10000) / 100]; }),
                 change: bars.length > 1 ? (bars[bars.length - 1][1] / base - 1) * 100 : null,
                 to: bars.length ? bars[bars.length - 1][0] : null };
      });
      var spx = lines[lines.length - 1], secs = lines.slice(0, -1).filter(function (l) { return l.change != null; });
      if (!secs.length || spx.change == null) throw new Error("Prices for these sectors are not on file for that period.");
      secs.sort(function (a, b) { return b.change - a.change; });
      var best = secs[0], word = function (v) { return (v >= 0 ? "rose " : "fell ") + pct(Math.abs(v)); };
      var head = secs.length === 1
        ? best.name + " " + word(best.change) + " " + per[2] + ", against " + pct(spx.change, true) + " for the S&P 500."
        : best.name + " " + word(best.change) + " " + per[2] + ", the most of the " + secs.length + " sectors; the S&P 500 " + word(spx.change) + ".";
      var src = "Daily closes of the Select Sector SPDR funds and SPY, to " + dateWords(spx.to) + ". Source: Databento.";
      var rows = secs.map(function (l) {
        var gap = l.change - spx.change;
        return "<tr><td>" + esc(l.name) + "</td><td>" + esc(l.fund) + "</td><td class='r'" + ds(l.change) + ">" + arrow(l.change) + "</td>" +
          "<td class='r'" + ds(gap) + ">" + (gap > 0 ? "+" : gap < 0 ? MINUS : "") + fmtNum(Math.abs(gap), 1) + " pp</td></tr>";
      }).join("");
      var body = "<p class='tk-headline hit'>" + esc(head) + "</p>" +
        "<div class='tk-chart'><div class='chart-el' id='tk-chart-el'></div></div>" +
        "<p class='tk-src tk-chart-foot'>" + esc(src) + " <button type='button' class='an-link' id='tk-png'>Download PNG</button> · " +
        "<button type='button' class='an-link' id='tk-csv'>Download CSV</button></p>" +
        "<div class='tk-table'><table class='tk-tab' data-no-enhance><thead><tr><th>Sector</th><th>Fund</th><th class='r'>Change</th><th class='r'>Against the S&amp;P 500</th></tr></thead><tbody>" +
        rows + "<tr><td>S&amp;P 500</td><td>SPY</td><td class='r'>" + arrow(spx.change) + "</td><td class='r'>" + MISSING + "</td></tr></tbody></table></div>" +
        "<details class='tk-calc'><summary>Show calculation</summary><ul><li><b>Change</b> = " + esc(SECTOR_CALC) +
        "</li><li><b>Against the S&amp;P 500</b> = the sector's change − the S&amp;P 500's, in percentage points</li>" +
        "<li><b>Sectors</b> are the S&amp;P's own (GICS): each fund holds that sector's S&amp;P 500 companies.</li></ul></details>";
      var foot = "<span class='tk-next-h'>Next Step</span>" +
        "<button type='button' class='tk-go' data-n='screen'>Screen " + esc(best.name) + " Stocks</button>" +
        (c.s.length < 5 ? "<button type='button' class='tk-opt' data-n='add'>Add a Sector</button>" : "") +
        "<button type='button' class='tk-opt' data-n='period'>Change the Period</button>" +
        "<button type='button' class='tk-opt' data-n='new'>Start a New Chart</button>";
      draw("chart", 3, body, foot);
      document.title = and(list.map(function (x) { return x.name; })) + " Against the S&P 500 · Plover Analytics";
      var chart = { kind: "line", title: and(list.map(function (x) { return x.name; })) + " against the S&P 500, start = 100", unit: "start = 100",
                    series: lines.map(function (l) { return { name: l.name, points: l.points }; }) };
      var drawn = usFmt.drawChart($("tk-chart-el"), chart, src);
      if (drawn) {
        $("tk-png").onclick = function () { drawn.exportPNG("plover-sectors.png"); };
        $("tk-csv").onclick = function () { drawn.exportCSV("plover-sectors.csv", [head, src, "Formula: change = " + SECTOR_CALC]); };
      }
      Array.prototype.forEach.call(panel.querySelectorAll("[data-n]"), function (b) {
        b.onclick = function () {
          var k = b.getAttribute("data-n");
          if (k === "screen") return screenStart({ sec: best.key }, true);
          if (k === "new") return chartStart({}, true);
          chartStart({ m: "sectors", s: c.s, p: c.p }, true);
          chartStep(k === "add" ? 1 : 2);
        };
      });
    }).catch(function (e) {
      if (cur.task !== "chart" || cur.seq !== seq) return;
      draw("chart", 3, failed((e.message || "The sector prices could not be loaded.") + " Check your connection.", true),
        "<button type='button' class='tk-back' id='tk-prev'>Change the Sectors</button>");
      $("tk-retry").onclick = function () { sectorChart(c, false); };
      $("tk-prev").onclick = function () { chartStart({ m: "sectors", s: c.s, p: c.p }, true); chartStep(1); };
    });
  }

  /* The S&P's own sectors (GICS), as app/us_sectors.py holds them: key, name, the
     Select Sector SPDR fund that holds the sector's S&P 500 companies. */
  var SECTORS = [["tech", "Information Technology", "XLK"], ["health", "Health Care", "XLV"], ["financials", "Financials", "XLF"],
    ["consumer_disc", "Consumer Discretionary", "XLY"], ["communication", "Communication Services", "XLC"],
    ["industrials", "Industrials", "XLI"], ["staples", "Consumer Staples", "XLP"], ["energy", "Energy", "XLE"],
    ["utilities", "Utilities", "XLU"], ["real_estate", "Real Estate", "XLRE"], ["materials", "Materials", "XLB"]];
  function sector(key) { var x = SECTORS.filter(function (y) { return y[0] === key; })[0]; return x ? { key: x[0], name: x[1], fund: x[2] } : null; }

  /* ===================================================== Screen Stocks */
  var SIZES = [
    { id: "any", label: "Any Size", note: "Every company with figures on file" },
    { id: "large", label: "Large", note: "Market value over $10bn", p: { market_cap_min: 1e10 } },
    { id: "mid", label: "Mid-Sized", note: "Market value $2bn to $10bn", p: { market_cap_min: 2e9, market_cap_max: 1e10 } },
    { id: "small", label: "Small", note: "Market value under $2bn", p: { market_cap_max: 2e9 } },
  ];
  var LOOKS = [
    { id: "growth", label: "Growing Sales", note: "Revenue up more than 10% on the year before", p: { revenue_growth_min: 10 }, col: ["revenue_growth_pct", "Revenue Growth"] },
    { id: "cheap", label: "Cheap on Earnings", note: "Share price under 15 times earnings", p: { pe_min: 0, pe_max: 15 }, col: ["pe_x", "Price to Earnings"] },
    { id: "dividend", label: "High Dividend", note: "Dividend yield above 3%", p: { dividend_yield_min: 3 }, col: ["dividend_yield_pct", "Dividend Yield"] },
    { id: "profit", label: "Very Profitable", note: "Net margin above 20%", p: { net_margin_min: 20 }, col: ["net_margin_pct", "Net Margin"] },
    { id: "buyback", label: "Buying Back Shares", note: "Buybacks above 3% of market value a year", p: { buyback_yield_min: 3 }, col: ["buyback_yield_pct", "Buyback Yield"] },
    { id: "rising", label: "Rising", note: "Share price up more than 20% over the past year", p: { price_change_1y_min: 20 }, not: "falling" },
    { id: "falling", label: "Fallen", note: "Share price down more than 20% over the past year", p: { price_change_1y_max: -20 }, not: "rising" },
  ];
  var PAGE = 25;
  function size(id) { return SIZES.filter(function (s) { return s.id === id; })[0] || null; }
  function look(id) { return LOOKS.filter(function (l) { return l.id === id; })[0] || null; }
  // step: a choice reached back from the results, so Back returns to them
  function screenParams(s) {
    return { task: "screen", size: s.size, sec: s.sec, sp: s.sp500 ? "1" : "", f: s.f.join(","), x: s.x.length ? JSON.stringify(s.x) : "", step: cur.step || "" };
  }
  /* A reader's own condition, in words (signed in only): /api/v1/us/build/screen
     reads it into the screener's own filters, the same reader as Your Plover's
     Screener. Each filter it found is one tag {t: words, p: query, k: metric,
     l: column} the reader can remove; words it did not turn into a tag are not
     applied, and the step says so. The tags ride in the address (x=). */
  var SAFE_PARAM = /^(?:[a-z0-9_]+_(?:min|max)|min_revenue|min_assets|sic|exchange|sp500|sector)$/;
  function ownTags(d, menu) {
    var labels = {};
    (menu || []).forEach(function (m) { labels[m.key] = m.label; });
    return (d.chips || []).filter(function (c) { return c.id !== "sort"; }).map(function (c) {
      var p = {};
      if (c.id === "industry") p.sic = d.params.sic;
      else if (c.id === "sector") p.sector = d.params.sector;
      else if (c.id === "exchanges") p.exchange = d.params.exchange;
      else if (c.id === "sp500") p.sp500 = "true";
      else if (d.params[c.key] != null) p[c.key] = d.params[c.key];
      var base = /_(min|max)$/.test(c.key || "") ? c.key.replace(/_(min|max)$/, "") : "";
      return { t: c.text, p: p, k: base, l: labels[c.key] ? labels[c.key].replace(/ (over|under)$/i, "") : "" };
    }).filter(function (t) { return Object.keys(t.p).length && Object.keys(t.p).every(function (k) { return SAFE_PARAM.test(k) && t.p[k] != null; }); });
  }
  function readTags(raw) {
    try {
      var list = JSON.parse(raw || "[]");
      return (Array.isArray(list) ? list : []).slice(0, 8).filter(function (t) {
        return t && typeof t.t === "string" && t.p && typeof t.p === "object" &&
          Object.keys(t.p).every(function (k) { return SAFE_PARAM.test(k) && /^[\w.,\-]{1,40}$/.test(String(t.p[k])); });
      }).map(function (t) { return { t: t.t.slice(0, 120), p: t.p, k: String(t.k || "").replace(/[^a-z0-9_]/g, ""), l: String(t.l || "").slice(0, 60) }; });
    } catch (e) { return []; }
  }

  function screenStart(s, push, step) {
    var picked = s.size && ((s.f || []).filter(look).length || (s.x || []).length);
    // an address from before the Sector step (no sec=) reads as any sector
    s = { size: size(s.size) ? s.size : "", sec: sector(s.sec) ? s.sec : (s.sec === "any" || picked ? "any" : ""),
          sp500: !!s.sp500, f: (s.f || []).filter(look), x: s.x || [] };
    cur = { task: "screen", s: s, step: ["size", "sector", "look"].indexOf(step) >= 0 ? step : "" };
    enter();
    setUrl(screenParams(s), push);
    if (s.size && s.sec && (s.f.length || s.x.length) && !cur.step) return screenRun(0);
    screenStep(cur.step === "size" || !s.size ? 0 : cur.step === "sector" || !s.sec ? 1 : 2);
  }

  function screenStep(at) {
    var s = cur.s, body, foot;
    if (cur.step) { cur.step = ["size", "sector", "look"][at]; setUrl(screenParams(s), false); }
    if (at === 0) {
      body = "<p class='tk-ask'>What size of company?</p><div class='tk-cards' role='radiogroup'>" + SIZES.map(function (x) {
        return "<button type='button' role='radio' aria-checked='" + (s.size === x.id) + "' class='tk-card" + (s.size === x.id ? " on" : "") + "' data-size='" + x.id + "'><b>" + esc(x.label) + "</b><span>" + esc(x.note) + "</span></button>";
      }).join("") + "</div><label class='tk-check'><input type='checkbox' id='tk-sp'" + (s.sp500 ? " checked" : "") + "> Only companies in the S&amp;P 500</label>";
      foot = "<button type='button' class='tk-go' id='tk-next'" + (s.size ? "" : " disabled") + ">Next: Sector</button>";
      draw("screen", 0, body, foot);
      Array.prototype.forEach.call(panel.querySelectorAll("[data-size]"), function (b) {
        b.onclick = function () { s.size = b.getAttribute("data-size"); setUrl(screenParams(s), false); screenStep(1); };
      });
      $("tk-sp").onchange = function () { s.sp500 = this.checked; setUrl(screenParams(s), false); };
      $("tk-next").onclick = function () { screenStep(1); };
      return;
    }
    if (at === 1) {
      // the S&P's eleven sectors; one keeps the screen to S&P 500 companies, whose sectors the S&P publishes
      var opt = function (key, label, note) {
        return "<button type='button' role='radio' aria-checked='" + (s.sec === key) + "' class='tk-card" + (s.sec === key ? " on" : "") + "' data-sec='" + key + "'><b>" +
          esc(label) + "</b>" + (note ? "<span>" + esc(note) + "</span>" : "") + "</button>";
      };
      body = "<p class='tk-ask'>Which sector?</p><div class='tk-cards tk-cards-grid' role='radiogroup' aria-label='Sector'>" +
        opt("any", "Any Sector", "Every company, in or out of the S&P 500") +
        SECTORS.map(function (x) { return opt(x[0], x[1], ""); }).join("") + "</div>" +
        "<p class='tk-note'>Sectors are the S&amp;P's own, so a sector keeps the screen to S&amp;P 500 companies.</p>";
      foot = "<button type='button' class='tk-back' id='tk-prev'>Back</button><button type='button' class='tk-go' id='tk-next'" + (s.sec ? "" : " disabled") + ">Next: What to Look For</button>";
      draw("screen", 1, body, foot);
      Array.prototype.forEach.call(panel.querySelectorAll("[data-sec]"), function (b) {
        b.onclick = function () { s.sec = b.getAttribute("data-sec"); setUrl(screenParams(s), false); screenStep(2); };
      });
      $("tk-prev").onclick = function () { screenStep(0); };
      $("tk-next").onclick = function () { screenStep(2); };
      return;
    }
    // at === 2
    body = "<p class='tk-ask'>What are you looking for? Pick one or more.</p><div class='tk-cards' role='group' aria-label='What to look for'>" + LOOKS.map(function (x) {
      var on = s.f.indexOf(x.id) >= 0;
      return "<button type='button' aria-pressed='" + on + "' class='tk-card" + (on ? " on" : "") + "' data-f='" + x.id + "'><b>" + esc(x.label) + "</b><span>" + esc(x.note) + "</span></button>";
    }).join("") + "</div>" + ownHtml(s);
    foot = "<button type='button' class='tk-back' id='tk-prev'>Back</button><button type='button' class='tk-go' id='tk-next'" + (s.f.length || s.x.length ? "" : " disabled") + ">Find Stocks</button>";
    draw("screen", 2, body, foot);
    wireOwn(s);
    Array.prototype.forEach.call(panel.querySelectorAll("[data-f]"), function (b) {
      b.onclick = function () {
        var id = b.getAttribute("data-f"), i = s.f.indexOf(id), x = look(id);
        if (i >= 0) s.f.splice(i, 1);
        else { s.f.push(id); if (x.not) s.f = s.f.filter(function (f) { return f !== x.not; }); }
        setUrl(screenParams(s), false);
        screenStep(2);
      };
    });
    $("tk-prev").onclick = function () { screenStep(1); };
    $("tk-next").onclick = function () { cur.step = ""; setUrl(screenParams(s), true); screenRun(0); };
  }

  // the reader's own condition under the ready-made ones: a box and its tags
  function ownHtml(s) {
    var tags = s.x.length ? "<ul class='tk-own-tags' aria-label='Your own conditions'>" + s.x.map(function (t, i) {
      return "<li>" + esc(t.t) + "<button type='button' class='tk-x' data-x='" + i + "' aria-label='Remove " + esc(t.t) + "'>×</button></li>";
    }).join("") + "</ul>" : "";
    var signedOut = me.on && !me.email;
    return "<div class='tk-own'><p class='tk-own-h'>Something else? Describe it in your own words.</p>" +
      "<form class='tk-own-row' id='tk-own'><label class='fp-sr' for='tk-own-q'>Your own condition</label>" +
      "<input type='text' id='tk-own-q' maxlength='300' autocomplete='off' placeholder='e.g. semiconductor companies with operating margin above 30%'" + (signedOut ? " disabled" : "") + ">" +
      "<button type='submit' class='tk-opt' id='tk-own-add'" + (signedOut ? " disabled" : "") + ">Add</button></form>" +
      (signedOut ? "<p class='tk-note'>Your own conditions need a free account. <a href='" + esc("signin.html?returnTo=" + encodeURIComponent(urlFor(screenParams(s), { step: "look" }))) +
        "'>Sign in or create one</a>, and you come straight back here.</p>"
        : "<p class='tk-note' id='tk-own-msg'>" + (s.x.length ? "Only the conditions shown as tags are applied." : "") + "</p>") + tags + "</div>";
  }
  function wireOwn(s) {
    Array.prototype.forEach.call(panel.querySelectorAll("[data-x]"), function (b) {
      b.onclick = function () { s.x.splice(+b.getAttribute("data-x"), 1); setUrl(screenParams(s), false); screenStep(2); };
    });
    var form = $("tk-own");
    if (!form) return;
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var q = $("tk-own-q").value.trim(), msg = $("tk-own-msg");
      if (!q || !msg) return;
      $("tk-own-add").disabled = true;
      $("tk-own-q").disabled = true;
      msg.textContent = "Reading “" + q + "”…";
      getJSON(API + "/build/screen?q=" + encodeURIComponent(q)).then(function (d) {
        if (cur.task !== "screen") return;
        var found = ownTags(d, d.menu).filter(function (t) { return !s.x.some(function (y) { return y.t === t.t; }); });
        if (!found.length) {
          $("tk-own-add").disabled = false;
          $("tk-own-q").disabled = false;
          msg.textContent = "Plover could not turn that into a condition it can check. Try a figure and a number, e.g. net margin above 15%, or an industry, e.g. banks.";
          return;
        }
        s.x = s.x.concat(found).slice(0, 8);
        setUrl(screenParams(s), false);
        screenStep(2);
        if ($("tk-own-msg")) $("tk-own-msg").textContent = "Added " + found.map(function (t) { return t.t; }).join(" · ") + ". Only the conditions shown as tags are applied.";
      }, function (e) {
        if (cur.task !== "screen") return;
        $("tk-own-add").disabled = false;
        $("tk-own-q").disabled = false;
        msg.textContent = (e.message || "That could not be read.") + " Check your connection, then try again.";
      });
    };
  }

  function screenQuery(s) {
    var p = { sort: "market_cap", order: "desc" };
    var add = function (o) { Object.keys(o || {}).forEach(function (k) { p[k] = o[k]; }); };
    add(size(s.size).p);
    s.f.forEach(function (id) { add(look(id).p); });
    if (sector(s.sec)) p.sector = s.sec;
    s.x.forEach(function (t) { add(t.p); });
    if (s.sp500) p.sp500 = "true";
    return p;
  }

  function screenRun(offset) {
    var s = cur.s, p = screenQuery(s), seq = cur.seq = (cur.seq || 0) + 1;
    var qs = Object.keys(p).map(function (k) { return k + "=" + encodeURIComponent(p[k]); }).join("&");
    if (!offset) draw("screen", 3, busy("Checking every listed US company against your choices…"));
    else $("tk-more").disabled = true;
    getJSON(API + "/financials/screener?" + qs + "&limit=" + PAGE + "&offset=" + offset).then(function (d) {
      if (cur.task !== "screen" || cur.seq !== seq) return;
      if (offset) { cur.rows = cur.rows.concat(d.rows); } else { cur.rows = d.rows; }
      cur.d = d;
      screenShow(s, p);
    }, function (e) {
      if (cur.task !== "screen" || cur.seq !== seq) return;
      draw("screen", 3, failed((e.message || "The screen could not be run.") + " Check your connection.", true),
        "<button type='button' class='tk-back' id='tk-prev'>Change What to Look For</button>");
      $("tk-retry").onclick = function () { screenRun(0); };
      $("tk-prev").onclick = function () { screenStep(2); };
    });
  }

  function screenShow(s, p) {
    var d = cur.d, rows = cur.rows;
    var chosen = [size(s.size).note].concat(sector(s.sec) ? ["Sector: " + sector(s.sec).name + " (S&P 500 companies)"] : [])
      .concat(s.sp500 ? ["In the S&P 500"] : []).concat(s.f.map(function (id) { return look(id).note; }))
      .concat(s.x.map(function (t) { return t.t; }));
    var cols = s.f.map(function (id) { return look(id).col; }).filter(Boolean);
    // a column for each of the reader's own figures the rows carry
    s.x.forEach(function (t) {
      var r0 = (rows[0] || {}).metrics || {};
      var key = [t.k + "_pct", t.k + "_x", t.k].filter(function (k) { return t.k && k in r0; })[0];
      if (key && key !== "market_cap" && key !== "price_change_1y_pct" && !cols.some(function (c) { return c[0] === key; })) cols.push([key, t.l || key]);
    });
    var cell = function (r, key) {
      var v = (r.metrics || {})[key];
      if (key === "pe_x") return v == null ? MISSING : fmtNum(v, 1) + "×";
      return v == null ? MISSING : pct(v);
    };
    var head = d.matched ? fmtNum(d.matched, 0) + " compan" + (d.matched === 1 ? "y matches" : "ies match") + " your choices, largest first."
      : "No company matches all of your choices.";
    var table = rows.length ? "<div class='tk-table'><table class='tk-tab'><thead><tr><th>Company</th><th class='tk-sm-hide'>Ticker</th><th class='r'>Market Value</th>" +
      cols.map(function (c) { return "<th class='r'>" + esc(c[1]) + "</th>"; }).join("") + "<th class='r'>Share Price, 1 Year</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td><a class='tk-coname' href='/us-company.html?t=" + encodeURIComponent(r.ticker) + "'>" + logo(r, 18) + esc(r.display_name || r.name) + "</a></td>" +
          "<td class='tk-sm-hide'>" + esc(r.ticker) + "</td><td class='r'" + ds((r.metrics || {}).market_cap) + ">" + money((r.metrics || {}).market_cap) + "</td>" +
          cols.map(function (c) { return "<td class='r'" + ds((r.metrics || {})[c[0]]) + ">" + cell(r, c[0]) + "</td>"; }).join("") +
          "<td class='r'" + ds((r.metrics || {}).price_change_1y_pct) + ">" + arrow((r.metrics || {}).price_change_1y_pct) + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      (rows.length < d.matched ? "<p class='tk-more-row'><button type='button' class='tk-opt' id='tk-more'>Show " + Math.min(PAGE, d.matched - rows.length) + " More</button> <span class='tk-note'>Showing " + rows.length + " of " + fmtNum(d.matched, 0) + ".</span></p>" : "")
      : "<p class='tk-note'>Try fewer choices, a different size of company" + (sector(s.sec) ? ", or any sector: S&amp;P 500 companies are mostly large" : "") + ".</p>";
    var used = ["market_cap", "price_change_1y_pct"].concat(cols.map(function (c) { return c[0]; }));
    var calc = Object.keys(d.calc || {}).filter(function (k) { return used.some(function (u) { return k === u || k.indexOf(u.replace(/_(pct|x)$/, "")) === 0; }); })
      .map(function (k) { return "<li><b>" + esc(k.replace(/_(pct|x)$/, "").replace(/_/g, " ")) + "</b> = " + esc(d.calc[k]) + "</li>"; }).join("");
    var src = [d.provenance && d.provenance.credit, d.price_provenance && d.price_provenance.credit].filter(Boolean).map(function (x) { return esc(String(x).replace(/^Sources?:\s*/, "")); }).join(" · ");
    var body = "<p class='tk-headline" + (d.matched ? " hit" : "") + "'>" + esc(head) + "</p><p class='tk-sub'>" + esc(chosen.join(" · ")) +
      ". Figures from each company's latest annual report; prices to the latest close.</p>" + table +
      (calc ? "<details class='tk-calc'><summary>Show calculation</summary><ul>" + calc + "</ul></details>" : "") +
      (src ? "<p class='tk-src'>Sources: " + src + "</p>" : "");
    var top = rows.slice(0, 3);
    var spec = { filters: Object.keys(p).filter(function (k) { return ["sort", "order", "sp500", "sic", "exchange", "sector"].indexOf(k) < 0; }).map(function (k) { return { key: k, value: +p[k] }; }),
                 sp500: !!s.sp500 || p.sp500 === "true", exchanges: p.exchange ? String(p.exchange).split(",") : [], sort_key: "market_cap", sort_order: "desc",
                 sectors: p.sector ? String(p.sector).split(",") : [] };
    if (p.sic) spec.industry = { sic: String(p.sic).split(","), label: (s.x.filter(function (t) { return t.p.sic; })[0] || {}).t || "Industry" };
    var foot = "<span class='tk-next-h'>Next Step</span>" +
      (top.length ? "<button type='button' class='tk-go' data-next='chart'>Chart the Top " + (top.length > 1 ? top.length + " " : "") + "Against the S&amp;P 500</button>" +
        "<button type='button' class='tk-opt' data-next='preview'>Earnings Preview for " + esc(top[0].ticker) + "</button>" : "") +
      "<button type='button' class='" + (top.length ? "tk-opt" : "tk-go") + "' data-next='change'>Change What to Look For</button>" +
      "<button type='button' class='tk-opt' data-next='sector'>Change the Sector</button>" +
      "<button type='button' class='tk-opt' data-next='size'>Change the Size</button>" +
      (top.length ? "<a class='tk-save' href='" + esc(saveLink("my.html#screener?f=" + encodeURIComponent(JSON.stringify(spec)))) + "'>" +
        (me.email || !me.on ? "Open in the Screener to Save" : "Save This Screen: Free Account") + "</a>" : "");
    draw("screen", 3, body, foot);
    document.title = "Screen Stocks · Plover Analytics";
    if ($("tk-more")) $("tk-more").onclick = function () { screenRun(cur.rows.length); };
    Array.prototype.forEach.call(panel.querySelectorAll("[data-next]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-next");
        if (k === "chart") return chartStart({ m: "price", s: top.map(function (r) { return { ticker: r.ticker, name: r.display_name || r.name }; }), p: "1y" }, true, true);
        if (k === "preview") return earnings(top[0].ticker, true);
        cur.step = k === "change" ? "look" : k === "sector" ? "sector" : "size";
        setUrl(screenParams(s), true);
        screenStep(k === "change" ? 2 : k === "sector" ? 1 : 0);
      };
    });
  }

  /* ==================================================== Insider Buying */
  // Directors and officers buying their own company's shares on the open
  // market (Form 4, code P), from /api/v1/us/insiders/buying (app/sec_insiders_api.py).
  var WHOS = [
    { id: "bosses", label: "Company Bosses", note: "Directors and officers buying their own company's shares" },
    { id: "all", label: "Anyone Who Files", note: "Bosses and big shareholders owning over 10%, often funds" },
  ];
  var SPANS = [[7, "This Week"], [30, "Past Month"], [90, "Past 3 Months"]];
  var SIZES_IN = [[0, "Any Size"], [100000, "Over $100,000"], [1000000, "Over $1mn"]];
  function spanWords(d) { return d === 7 ? "in the past week" : d === 90 ? "in the past 3 months" : "in the past month"; }
  function insParams(s) { return { task: "insiders", who: s.who, d: s.d ? String(s.d) : "", min: s.min ? String(s.min) : "", sp: s.sp500 ? "1" : "", step: cur.step || "" }; }

  function insidersStart(s, push, step) {
    s = { who: WHOS.some(function (w) { return w.id === s.who; }) ? s.who : "",
          d: SPANS.some(function (x) { return x[0] === s.d; }) ? s.d : 0,
          min: SIZES_IN.some(function (x) { return x[0] === s.min; }) ? s.min : 0, sp500: !!s.sp500 };
    cur = { task: "insiders", s: s, step: step === "who" || step === "size" ? step : "" };
    enter();
    setUrl(insParams(s), push);
    if (s.who && s.d && !cur.step) return insidersRun(0);
    insidersStep(cur.step === "who" || !s.who ? 0 : 1);
  }

  function insidersStep(at) {
    var s = cur.s;
    if (cur.step) { cur.step = at === 0 ? "who" : "size"; setUrl(insParams(s), false); }
    if (at === 0) {
      draw("insiders", 0, "<p class='tk-ask'>Whose buying do you want to see?</p><div class='tk-cards' role='radiogroup'>" + WHOS.map(function (x) {
        return "<button type='button' role='radio' aria-checked='" + (s.who === x.id) + "' class='tk-card" + (s.who === x.id ? " on" : "") + "' data-who='" + x.id + "'><b>" + esc(x.label) + "</b><span>" + esc(x.note) + "</span></button>";
      }).join("") + "</div><p class='tk-note'>Only purchases on the open market, from the forms insiders file with the SEC within two business days. Awards, option exercises and gifts are left out.</p>");
      Array.prototype.forEach.call(panel.querySelectorAll("[data-who]"), function (b) {
        b.onclick = function () { s.who = b.getAttribute("data-who"); setUrl(insParams(s), false); insidersStep(1); };
      });
      return;
    }
    if (!s.d) s.d = 30;
    var seg = function (name, list, val) {
      return "<div class='tk-cards tk-cards-sm' role='radiogroup' aria-label='" + esc(name) + "'>" + list.map(function (x) {
        return "<button type='button' role='radio' aria-checked='" + (val === x[0]) + "' class='tk-card" + (val === x[0] ? " on" : "") + "' data-" + name + "='" + x[0] + "'><b>" + esc(x[1]) + "</b></button>";
      }).join("") + "</div>";
    };
    draw("insiders", 1, "<p class='tk-ask'>How far back?</p>" + seg("d", SPANS, s.d) +
      "<p class='tk-ask tk-ask-2'>Purchases of what size?</p>" + seg("min", SIZES_IN, s.min) +
      "<label class='tk-check'><input type='checkbox' id='tk-sp'" + (s.sp500 ? " checked" : "") + "> Only companies in the S&amp;P 500</label>",
      "<button type='button' class='tk-back' id='tk-prev'>Back</button><button type='button' class='tk-go' id='tk-next'>Show Who Is Buying</button>");
    Array.prototype.forEach.call(panel.querySelectorAll("[data-d]"), function (b) {
      b.onclick = function () { s.d = +b.getAttribute("data-d"); setUrl(insParams(s), false); insidersStep(1); };
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-min]"), function (b) {
      b.onclick = function () { s.min = +b.getAttribute("data-min"); setUrl(insParams(s), false); insidersStep(1); };
    });
    $("tk-sp").onchange = function () { s.sp500 = this.checked; setUrl(insParams(s), false); };
    $("tk-prev").onclick = function () { insidersStep(0); };
    $("tk-next").onclick = function () { cur.step = ""; setUrl(insParams(s), true); insidersRun(0); };
  }

  function insidersRun(offset) {
    var s = cur.s, seq = cur.seq = (cur.seq || 0) + 1;
    var qs = "days=" + s.d + "&who=" + s.who + "&min_value=" + s.min + (s.sp500 ? "&sp500=true" : "") + "&limit=" + PAGE + "&offset=" + offset;
    if (!offset) draw("insiders", 2, busy("Reading the insider filings " + spanWords(s.d) + "…"));
    else $("tk-more").disabled = true;
    getJSON(API + "/insiders/buying?" + qs).then(function (d) {
      if (cur.task !== "insiders" || cur.seq !== seq) return;
      cur.rows = offset ? cur.rows.concat(d.companies) : d.companies;
      cur.d = d;
      insidersShow(s);
    }, function (e) {
      if (cur.task !== "insiders" || cur.seq !== seq) return;
      draw("insiders", 2, failed((e.message || "The insider filings could not be read.") + " Check your connection.", true),
        "<button type='button' class='tk-back' id='tk-prev'>Change the Period or Size</button>");
      $("tk-retry").onclick = function () { insidersRun(0); };
      $("tk-prev").onclick = function () { insidersStep(1); };
    });
  }

  function insidersShow(s) {
    var d = cur.d, rows = cur.rows, t = d.totals;
    var whoWords = s.who === "bosses" ? "bosses" : "insiders, big shareholders included,";
    var head = t.companies ? fmtNum(t.companies, 0) + " compan" + (t.companies === 1 ? "y's " : "ies' ") + whoWords + " bought shares " + spanWords(s.d) +
      ": " + money(t.value) + " in all, by " + fmtNum(t.insiders, 0) + " " + (t.insiders === 1 ? "person" : "people") + "."
      : "No " + (s.who === "bosses" ? "company bosses" : "insiders") + " bought shares on the open market " + spanWords(s.d) + (s.min ? " in purchases " + SIZES_IN.filter(function (x) { return x[0] === s.min; })[0][1].toLowerCase() : "") + ".";
    var chosen = [WHOS.filter(function (w) { return w.id === s.who; })[0].label, SPANS.filter(function (x) { return x[0] === s.d; })[0][1],
      s.min ? "Purchases " + SIZES_IN.filter(function (x) { return x[0] === s.min; })[0][1].toLowerCase() : "Any size"].concat(s.sp500 ? ["In the S&P 500"] : []);
    var table = rows.length ? "<div class='tk-table'><table class='tk-tab'><thead><tr><th>Company</th><th class='r'>Insiders</th><th class='r'>Bought</th>" +
      "<th>Biggest Buyer</th><th class='r tk-sm-hide'>Latest Purchase</th><th class='r'>Share Price Since</th><th class='r tk-sm-hide'>S&amp;P 500 Since</th></tr></thead><tbody>" +
      rows.map(function (r) {
        var b = r.top_buyer;
        return "<tr><td><a class='tk-coname' href='/us-stock.html?t=" + encodeURIComponent(r.ticker) + "&view=owners'>" + logo(r, 18) + esc(r.name) +
          " <span class='t'>" + esc(r.ticker) + "</span></a></td>" +
          "<td class='r'>" + fmtNum(r.insiders, 0) + "</td><td class='r'" + ds(r.value) + ">" + money(r.value) + "</td>" +
          "<td class='tk-wrap'>" + (b ? esc(b.name) + ", " + esc(b.role) : MISSING) + "</td>" +
          "<td class='r tk-sm-hide'" + ds(r.latest_trade) + ">" + esc(shortDay(r.latest_trade)) + "</td>" +
          "<td class='r'" + ds(r.price_since_pct) + ">" + arrow(r.price_since_pct) + "</td>" +
          "<td class='r tk-sm-hide'" + ds(r.sp500_since_pct) + ">" + (r.sp500_since_pct == null ? MISSING : pct(r.sp500_since_pct, true)) + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      "<p class='tk-note'>Share price since: from the close on the day of each company's first purchase in the period to the latest close.</p>" +
      (rows.length < t.companies ? "<p class='tk-more-row'><button type='button' class='tk-opt' id='tk-more'>Show " + Math.min(PAGE, t.companies - rows.length) +
        " More</button> <span class='tk-note'>Showing " + rows.length + " of " + fmtNum(t.companies, 0) + ".</span></p>" : "")
      : "<p class='tk-note'>Insider filings are on file to " + esc(dateWords(d.latest_filed)) + ". Try a longer period, any size, or anyone who files.</p>";
    var calc = Object.keys(d.calc || {}).map(function (k) {
      return "<li><b>" + esc({ value: "Bought", price_since: "Share price since", sp500_since: "S&P 500 since" }[k] || k) + "</b> = " + esc(d.calc[k]) + "</li>";
    }).join("");
    var body = "<p class='tk-headline" + (t.companies ? " hit" : "") + "'>" + esc(head) + "</p><p class='tk-sub'>" + esc(chosen.join(" · ")) +
      ". Companies listed on a US exchange, largest total first. Filings to " + esc(dateWords(d.latest_filed)) + ".</p>" + table +
      (calc ? "<details class='tk-calc'><summary>Show calculation</summary><ul>" + calc + "</ul></details>" : "") +
      "<p class='tk-src'>Sources: " + esc(String(d.provenance.credit).replace(/^Source:\s*/, "")) + " Prices: daily closes.</p>";
    var top = rows.slice(0, 3);
    var foot = "<span class='tk-next-h'>Next Step</span>" +
      (top.length ? "<a class='tk-go tk-btnlink' href='/us-stock.html?t=" + encodeURIComponent(top[0].ticker) + "&view=owners'>See " + esc(top[0].ticker) + "'s Insider Trades</a>" +
        "<button type='button' class='tk-opt' data-next='preview'>Earnings Preview for " + esc(top[0].ticker) + "</button>" +
        "<button type='button' class='tk-opt' data-next='chart'>Chart the Top " + (top.length > 1 ? top.length + " " : "") + "Against the S&amp;P 500</button>" : "") +
      "<button type='button' class='" + (top.length ? "tk-opt" : "tk-go") + "' data-next='change'>Change the Period or Size</button>" +
      "<button type='button' class='tk-opt' data-next='who'>Change Who</button>";
    draw("insiders", 2, body, foot);
    document.title = "Insider Buying · Plover Analytics";
    if ($("tk-more")) $("tk-more").onclick = function () { insidersRun(cur.rows.length); };
    Array.prototype.forEach.call(panel.querySelectorAll("[data-next]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-next");
        if (k === "chart") return chartStart({ m: "price", s: top.map(function (r) { return { ticker: r.ticker, name: r.name }; }), p: "1y" }, true, true);
        if (k === "preview") return earnings(top[0].ticker, true);
        cur.step = k === "who" ? "who" : "size";
        setUrl(insParams(s), true);
        insidersStep(k === "who" ? 0 : 1);
      };
    });
  }

  /* ================================================== Research a Stock */
  // Three steps: choose a company, choose what goes in, read and download. Every
  // choice is in the address (the same keys the API reads, app/us_research_api.py),
  // so a tailored brief can be linked to. The server writes the brief once as a
  // plain document; the page draws it and PDF, Word and Excel are made from it,
  // so a download always matches the page.
  var R_KEYS = ["s", "per", "n", "f", "pc", "p", "h", "r", "k", "ask"];
  var rOpts = null, rUsual = null, rUsualAsked = false;
  function rOptions() {
    if (rOpts) return Promise.resolve(rOpts);
    return getJSON(API + "/research/options").then(function (o) { rOpts = o; return o; });
  }
  // a signed-in reader's usual choices, kept on their account
  function rLayout() {
    return meReady.then(function () {
      if (rUsualAsked || !me.email) return rUsual;
      rUsualAsked = true;
      return getJSON(API + "/research/layout").then(function (d) { rUsual = d.layout; return rUsual; }, function () { return null; });
    });
  }
  // the standard brief (usual = false), or the reader's own usual choices on top of it
  function rDefaults(usual) {
    var d = rOpts.defaults, u = (usual && rUsual) || {};
    var per = u.per || d.per;
    return { s: u.s ? u.s.split(",") : d.sections.slice(), per: per, n: +(u.n || d.n[per]),
             f: u.f ? u.f.split(",") : d.figures.slice(), pc: u.pc ? u.pc.split(",") : d.peer_cols.slice(),
             p: [], h: +(u.h || d.holders), r: u.r != null && u.r !== "" ? +u.r : d.risks, k: [], ask: [] };
  }
  // An address that names any layout choice is read on the standard brief, so a shared
  // link shows everyone the same brief; one that names none starts from the reader's own.
  function rRead(p) {
    var layout = ["s", "per", "n", "f", "pc", "h", "r"].some(function (k) { return p.get(k) != null && p.get(k) !== ""; });
    var c = rDefaults(!layout);
    if (p.get("s")) c.s = p.get("s").split(",").filter(function (x) { return rOpts.sections.some(function (o) { return o.key === x; }); });
    if (p.get("per") === "q" || p.get("per") === "a") { if (p.get("per") !== c.per) c.n = rOpts.defaults.n[p.get("per")]; c.per = p.get("per"); }
    if (+p.get("n") && rOpts.periods[c.per].indexOf(+p.get("n")) >= 0) c.n = +p.get("n");
    if (p.get("f")) c.f = p.get("f").split(",");
    if (p.get("pc")) c.pc = p.get("pc").split(",");
    if (p.get("p")) c.p = p.get("p").split(",").filter(Boolean).slice(0, rOpts.limits.peers);
    if (rOpts.holders.indexOf(+p.get("h")) >= 0) c.h = +p.get("h");
    if (p.get("r") != null && rOpts.risks.indexOf(+p.get("r")) >= 0) c.r = +p.get("r");
    if (p.get("k")) c.k = p.get("k").split("|").filter(Boolean).slice(0, rOpts.limits.topics);
    if (p.get("ask")) c.ask = p.get("ask").split("|").filter(Boolean).slice(0, rOpts.limits.questions);
    if (!c.s.length) c.s = rDefaults(false).s;
    return c;
  }
  // what differs from the standard brief goes in the address, and to the server
  function rParams(c) {
    var d = rDefaults(false), o = {};
    function same(a, b) { return a.join(",") === b.join(","); }
    if (!same(c.s, d.s)) o.s = c.s.join(",");
    o.per = c.per;     // always named: an address that names a choice is read the same way on every reload
    if (c.n !== d.n || c.per !== d.per) o.n = String(c.n);
    if (!same(c.f, d.f)) o.f = c.f.join(",");
    if (!same(c.pc, d.pc)) o.pc = c.pc.join(",");
    if (c.p.length) o.p = c.p.join(",");
    if (c.h !== d.h) o.h = String(c.h);
    if (c.r !== d.r) o.r = String(c.r);
    if (c.k.length) o.k = c.k.join("|");
    if (c.ask.length) o.ask = c.ask.join("|");
    return o;
  }
  function rQuery(c) {
    var u = new URLSearchParams(), o = rParams(c);
    Object.keys(o).forEach(function (k) { u.set(k, o[k]); });
    return u.toString().replace(/%2C/g, ",").replace(/%7C/g, "|");
  }
  function rUrl(step, push) {
    var o = rParams(cur.ch);
    o.task = "research";
    o.c = cur.t;
    if (step === "build") o.step = "build";
    var ordered = { task: o.task, c: o.c, step: o.step };
    R_KEYS.forEach(function (k) { if (o[k]) ordered[k] = o[k]; });
    var u = new URLSearchParams();
    Object.keys(ordered).forEach(function (k) { if (ordered[k]) u.set(k, ordered[k]); });
    var url = "/?" + u.toString().replace(/%2C/g, ",").replace(/%7C/g, "|");
    if (location.pathname + location.search !== url) history[push ? "pushState" : "replaceState"]({ task: "research" }, "", url);
  }
  function notesKey(t) { return "plover-research-notes:" + t; }
  function readNotes(t) { try { return localStorage.getItem(notesKey(t)) || ""; } catch (e) { return ""; } }
  function keepNotes(t, text) { try { if (text) localStorage.setItem(notesKey(t), text); else localStorage.removeItem(notesKey(t)); } catch (e) {} }
  function signinHere() { return "signin.html?returnTo=" + encodeURIComponent(location.pathname + location.search); }

  // entry: from the task link, an address, or a Next Step button
  function research(t, push, params, step) {
    cur = { task: "research", t: (t || "").toUpperCase() };
    enter();
    if (!cur.t) {
      setUrl({ task: "research" }, push);
      return researchPick();
    }
    draw("research", step === "build" ? 1 : 2, busy("Opening " + cur.t + "…"));
    rOptions().then(function () { return rLayout(); }).then(function () {
      if (cur.task !== "research" || cur.t !== (t || "").toUpperCase()) return;
      cur.ch = rRead(params || new URLSearchParams());
      rUrl(step, push);
      if (step === "build") return researchBuild();
      researchBrief();
    }, function () {
      draw("research", 1, failed("The research choices could not be loaded. Check your connection.", true));
      $("tk-retry").onclick = function () { research(t, false, params, step); };
    });
  }

  function researchPick() {
    draw("research", 0, "<p class='tk-ask'>Which company? Type a name or ticker, or pick one below.</p>" +
      "<div id='tk-pick'></div><div id='tk-soon'>" + busy("Finding the largest companies in the S&P 500…") + "</div>");
    picker($("tk-pick"), "e.g. Nvidia or NVDA", function (c) { research(c.ticker, true, null, "build"); }).focus();
    var row = function (c, when) {
      return "<li><button type='button' data-t='" + esc(c.ticker) + "'>" + (when ? "<span class='d'>" + esc(when) + "</span>" : "") + logo(c, 20) +
        "<span class='n'>" + esc(c.display_name || c.name) + "</span><span class='t'>" + esc(c.ticker) + "</span></button></li>";
    };
    Promise.all([
      getJSON(API + "/financials/screener?sp500=true&sort=market_cap&limit=10").catch(function () { return null; }),
      getJSON(API + "/preview/upcoming?days=7&limit=10").catch(function () { return null; }),
    ]).then(function (got) {
      if (cur.task !== "research" || cur.t || !$("tk-soon")) return;
      var big = ((got[0] || {}).rows || []).filter(function (c) { return c.ticker; });
      var done = ((got[1] || {}).reported || []);
      var html = "";
      if (done.length) html += "<h3>Reported Results in the Last Week</h3><ul class='tk-cos'>" + done.map(function (c) { return row(c, shortDay(c.results_date)); }).join("") + "</ul>";
      if (big.length) html += "<h3>Largest in the S&amp;P 500</h3><ul class='tk-cos'>" + big.map(function (c) { return row(c); }).join("") + "</ul>";
      $("tk-soon").innerHTML = html || "<p class='tk-note'>The suggested companies could not be loaded. Type any company above.</p>";
      Array.prototype.forEach.call($("tk-soon").querySelectorAll("button[data-t]"), function (b) {
        b.onclick = function () { research(b.getAttribute("data-t"), true, null, "build"); };
      });
    });
  }

  /* ---- step 2: choose what goes in ------------------------------------ */
  var TOPIC_IDEAS = ["China", "tariffs", "artificial intelligence", "competition", "interest rates", "supply chain", "debt"];
  function questionIdeas(t) { return ["Who owns " + t + "?", t + " revenue by segment", "Insiders selling " + t]; }
  function chipList(items, kind) {
    return items.length ? "<ul class='rb-chips'>" + items.map(function (x, i) {
      return "<li><span>" + esc(x) + "</span><button type='button' class='tk-x' data-del-" + kind + "='" + i + "' aria-label='Remove " + esc(x) + "'>×</button></li>";
    }).join("") + "</ul>" : "";
  }
  function choiceCards(name, items, on) {
    return "<div class='tk-cards tk-cards-sm rb-cards' role='radiogroup'>" + items.map(function (x) {
      var v = x[0], sel = String(v) === String(on);
      return "<button type='button' role='radio' aria-checked='" + sel + "' class='tk-card" + (sel ? " on" : "") + "' data-" + name + "='" + esc(v) + "'><b>" + esc(x[1]) + "</b></button>";
    }).join("") + "</div>";
  }
  function checks(name, opts, on) {
    return "<div class='rb-checks'>" + opts.map(function (o) {
      return "<label><input type='checkbox' data-" + name + "='" + esc(o.key) + "'" + (on.indexOf(o.key) >= 0 ? " checked" : "") + "> " + esc(o.label) + "</label>";
    }).join("") + "</div>";
  }

  function researchBuild() {
    var c = cur.ch, t = cur.t, o = rOpts;
    var label = {};
    o.sections.forEach(function (x) { label[x.key] = x.label; });
    // ticked sections in their order, then the rest
    var order = c.s.concat(o.sections.map(function (x) { return x.key; }).filter(function (k) { return c.s.indexOf(k) < 0; }));
    var secs = "<ol class='rb-order'>" + order.map(function (k, i) {
      var on = c.s.indexOf(k) >= 0;
      return "<li class='" + (on ? "" : "off") + "'><label><input type='checkbox' data-sec='" + k + "'" + (on ? " checked" : "") + "> " + esc(label[k]) + "</label>" +
        (on ? "<span class='rb-move'><button type='button' class='tk-opt' data-up='" + k + "'" + (i === 0 ? " disabled" : "") + " aria-label='Move " + esc(label[k]) + " up'>↑ Up</button>" +
          "<button type='button' class='tk-opt' data-down='" + k + "'" + (i >= c.s.length - 1 ? " disabled" : "") + " aria-label='Move " + esc(label[k]) + " down'>↓ Down</button></span>" : "") + "</li>";
    }).join("") + "</ol>";
    var counts = o.periods[c.per].map(function (n) { return [n, c.per === "q" ? n + " Quarters" : n === 20 ? "Since 2009" : n + " Years"]; });
    var signedIn = !!me.email;
    var lead = "Choose what goes into the brief. " + (rUsual ? "Your usual choices are" : "The standard brief is") + " ticked already: Make the Brief to see it.";
    var body =
      "<div class='tk-co-head' id='rb-co'><b>" + esc(t) + "</b><span class='tk-co-meta'>" + esc(lead) + "</span></div>" +
      "<div class='rs-bar'><button type='button' class='tk-go' data-make='1'>Make the Brief</button><span class='tk-note rs-dl-note'>Or change anything below first.</span></div>" +
      "<div class='rb'>" +
      "<section class='rb-sec'><h3>Sections and Their Order</h3>" + secs + "</section>" +
      "<section class='rb-sec'><h3>Results</h3>" + choiceCards("per", [["a", "By Year"], ["q", "By Quarter"]], c.per) +
        choiceCards("n", counts, c.n) + "<p class='rb-lab'>Figures in the table</p>" + checks("fig", o.figures, c.f) + "</section>" +
      "<section class='rb-sec'><h3>Peers</h3>" + choiceCards("pm", [["auto", "Closest in Size, Same Industry"], ["own", "My Own List"]], c.p.length || cur.ownPeers ? "own" : "auto") +
        (c.p.length || cur.ownPeers ? "<div id='rb-peer-pick'></div>" + chipList(c.p, "peer") +
          "<p class='tk-note'>Up to " + o.limits.peers + " companies. Type a name or ticker and pick it.</p>" : "") +
        "<p class='rb-lab'>Columns in the peer table</p>" + checks("pc", o.peer_cols, c.pc) + "</section>" +
      "<section class='rb-sec'><h3>Fund Holders and Risks</h3><p class='rb-lab'>Largest fund holders</p>" +
        choiceCards("h", o.holders.map(function (n) { return [n, "Top " + n]; }), c.h) +
        "<p class='rb-lab'>Risk factors</p>" + choiceCards("r", o.risks.map(function (n) { return [n, n ? "First " + n : "All"]; }), c.r) + "</section>" +
      "<section class='rb-sec'><h3>Topics in the Annual Report</h3><p class='tk-note'>The brief quotes the paragraphs of " + esc(t) +
        "'s latest 10-K that mention each topic, word for word. Up to " + o.limits.topics + ".</p>" +
        "<div class='rb-add'><label class='fp-sr' for='rb-topic'>A topic</label><input type='text' id='rb-topic' maxlength='40' placeholder='e.g. China' autocomplete='off'>" +
        "<button type='button' class='tk-opt' id='rb-topic-add'>Add Topic</button></div>" + chipList(c.k, "topic") +
        "<div class='rb-ideas'>" + TOPIC_IDEAS.filter(function (x) { return c.k.indexOf(x) < 0; }).map(function (x) {
          return "<button type='button' class='an-link' data-idea-topic='" + esc(x) + "'>+ " + esc(x) + "</button>"; }).join("") + "</div></section>" +
      "<section class='rb-sec'><h3>Your Questions</h3>" + (signedIn ?
        "<p class='tk-note'>Each question is answered from the data and goes into the brief. Up to " + o.limits.questions + ".</p>" +
        "<div class='rb-add'><label class='fp-sr' for='rb-q'>A question</label><input type='text' id='rb-q' maxlength='300' placeholder='e.g. Who owns " + esc(t) + "?' autocomplete='off'>" +
        "<button type='button' class='tk-opt' id='rb-q-add'>Add Question</button></div>" + chipList(c.ask, "q") +
        "<div class='rb-ideas'>" + questionIdeas(t).filter(function (x) { return c.ask.indexOf(x) < 0; }).map(function (x) {
          return "<button type='button' class='an-link' data-idea-q='" + esc(x) + "'>+ " + esc(x) + "</button>"; }).join("") + "</div>"
        : "<p class='tk-note'>Add your own questions about " + esc(t) + " and have them answered in the brief, with a free account. <a href='" + esc(signinHere()) + "'>Sign in or create one</a></p>") +
      "</section>" +
      "<section class='rb-sec'><h3>Your Notes</h3><p class='tk-note'>Your own view, added to the brief and its downloads under Your Notes. Notes stay in this browser.</p>" +
        "<label class='fp-sr' for='rb-notes'>Your notes</label><textarea id='rb-notes' rows='5' maxlength='20000' placeholder='e.g. Expensive on earnings; watch data-centre share.'>" + esc(readNotes(t)) + "</textarea></section>" +
      "</div>";
    var foot = "<button type='button' class='tk-go' id='rb-make'>Make the Brief</button>" +
      "<button type='button' class='tk-opt' id='rb-reset'>Back to the Standard Brief</button>" +
      (signedIn ? "<button type='button' class='tk-opt' id='rb-save'>Keep as My Usual Choices</button><span class='tk-note rb-saved' id='rb-saved' role='status'></span>" : "") +
      "<button type='button' class='tk-back' id='rb-other'>Choose Another Company</button>";
    var y = window.scrollY;
    draw("research", 1, body, foot);
    document.title = t + " Research: Choose What Goes In · Plover Analytics";
    if (cur.redraw) window.scrollTo(0, y);
    cur.redraw = true;

    function again() { rUrl("build", false); researchBuild(); }
    // a company name on the builder: read it from the company list once
    getJSON(API + "/company/" + encodeURIComponent(t)).then(function (d) {
      if (cur.task === "research" && cur.t === t && $("rb-co")) $("rb-co").innerHTML = logo(d.company, 28) + "<b>" + esc(d.company.name) + "</b><span class='t'>" + esc(t) + "</span>" +
        "<span class='tk-co-meta'>" + esc(lead) + "</span>";
    }, function () {});
    function each(sel, fn) { Array.prototype.forEach.call(panel.querySelectorAll(sel), fn); }
    each("[data-sec]", function (x) {
      x.onchange = function () {
        var k = x.getAttribute("data-sec");
        if (x.checked) c.s.push(k); else c.s = c.s.filter(function (y) { return y !== k; });
        again();
      };
    });
    each("[data-up],[data-down]", function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-up") || b.getAttribute("data-down"), i = c.s.indexOf(k), j = b.getAttribute("data-up") ? i - 1 : i + 1;
        if (i < 0 || j < 0 || j >= c.s.length) return;
        c.s[i] = c.s[j]; c.s[j] = k;
        again();
        var nb = panel.querySelector("[data-" + (b.getAttribute("data-up") ? "up" : "down") + "='" + k + "']");
        if (nb && !nb.disabled) nb.focus();
      };
    });
    each("[data-per]", function (b) { b.onclick = function () { var v = b.getAttribute("data-per"); if (v !== c.per) { c.per = v; c.n = rOpts.defaults.n[v]; } again(); }; });
    each("[data-n]", function (b) { b.onclick = function () { c.n = +b.getAttribute("data-n"); again(); }; });
    each("[data-h]", function (b) { b.onclick = function () { c.h = +b.getAttribute("data-h"); again(); }; });
    each("[data-r]", function (b) { b.onclick = function () { c.r = +b.getAttribute("data-r"); again(); }; });
    each("[data-fig]", function (x) {
      x.onchange = function () {
        var k = x.getAttribute("data-fig");
        c.f = x.checked ? rOpts.figures.map(function (o) { return o.key; }).filter(function (y) { return y === k || c.f.indexOf(y) >= 0; }) : c.f.filter(function (y) { return y !== k; });
        if (!c.f.length) c.f = [k];          // a table needs one figure
        again();
      };
    });
    each("[data-pc]", function (x) {
      x.onchange = function () {
        var k = x.getAttribute("data-pc");
        c.pc = x.checked ? rOpts.peer_cols.map(function (o) { return o.key; }).filter(function (y) { return y === k || c.pc.indexOf(y) >= 0; }) : c.pc.filter(function (y) { return y !== k; });
        if (!c.pc.length) c.pc = [k];
        again();
      };
    });
    each("[data-pm]", function (b) {
      b.onclick = function () {
        if (b.getAttribute("data-pm") === "auto") { c.p = []; cur.ownPeers = false; } else cur.ownPeers = true;
        again();
        if (cur.ownPeers && $("tk-co")) $("tk-co").focus();
      };
    });
    if ($("rb-peer-pick")) {
      picker($("rb-peer-pick"), "Add a peer, e.g. Intel or INTC", function (co) {
        if (co.ticker === t || c.p.indexOf(co.ticker) >= 0 || c.p.length >= rOpts.limits.peers) return;
        c.p.push(co.ticker);
        again();
        if ($("tk-co")) $("tk-co").focus();
      });
    }
    function addFrom(id, list, limit, min) {
      var v = $(id).value.trim().replace(/\s+/g, " ").replace(/\|/g, " ");
      if (v.length < min) { $(id).focus(); return; }
      if (list.length >= limit) return;
      if (list.map(function (x) { return x.toLowerCase(); }).indexOf(v.toLowerCase()) < 0) list.push(v);
      again();
      if ($(id)) $(id).focus();
    }
    if ($("rb-topic")) {
      $("rb-topic-add").onclick = function () { addFrom("rb-topic", c.k, rOpts.limits.topics, 3); };
      $("rb-topic").onkeydown = function (ev) { if (ev.key === "Enter") { ev.preventDefault(); addFrom("rb-topic", c.k, rOpts.limits.topics, 3); } };
    }
    if ($("rb-q")) {
      $("rb-q-add").onclick = function () { addFrom("rb-q", c.ask, rOpts.limits.questions, 3); };
      $("rb-q").onkeydown = function (ev) { if (ev.key === "Enter") { ev.preventDefault(); addFrom("rb-q", c.ask, rOpts.limits.questions, 3); } };
    }
    each("[data-idea-topic]", function (b) { b.onclick = function () { if (c.k.length < rOpts.limits.topics) { c.k.push(b.getAttribute("data-idea-topic")); again(); } }; });
    each("[data-idea-q]", function (b) { b.onclick = function () { if (c.ask.length < rOpts.limits.questions) { c.ask.push(b.getAttribute("data-idea-q")); again(); } }; });
    each("[data-del-topic]", function (b) { b.onclick = function () { c.k.splice(+b.getAttribute("data-del-topic"), 1); again(); }; });
    each("[data-del-q]", function (b) { b.onclick = function () { c.ask.splice(+b.getAttribute("data-del-q"), 1); again(); }; });
    each("[data-del-peer]", function (b) { b.onclick = function () { c.p.splice(+b.getAttribute("data-del-peer"), 1); again(); }; });
    $("rb-notes").oninput = function () { keepNotes(t, $("rb-notes").value); };
    each("#rb-make,[data-make]", function (x) { x.onclick = function () { cur.redraw = false; rUrl("brief", true); researchBrief(); window.scrollTo(0, 0); }; });
    // the standard brief, whatever the reader keeps as usual
    $("rb-reset").onclick = function () { cur.ch = rDefaults(false); cur.ownPeers = false; again(); };
    $("rb-other").onclick = function () { research("", true); };
    if ($("rb-save")) {
      $("rb-save").onclick = function () {
        // the full choices, not only what differs: the usual brief is now these
        var b = $("rb-save"), keep = { s: c.s.join(","), per: c.per, n: String(c.n), f: c.f.join(","), pc: c.pc.join(","), h: String(c.h), r: String(c.r) };
        b.disabled = true;
        fetch(API + "/research/layout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout: keep }) })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (d) {
            rUsual = d.layout;
            $("rb-saved").textContent = "Kept. Every brief you open starts with these choices, wherever you sign in.";
            b.disabled = false;
          }, function () {
            $("rb-saved").textContent = "Your choices could not be kept. Check your connection, then try again.";
            b.disabled = false;
          });
      };
    }
  }

  /* ---- step 3: read and download -------------------------------------- */
  function researchBrief() {
    var t = cur.t, q = rQuery(cur.ch);
    draw("research", 2, busy("Putting together the research brief for " + t + ": filings, peers, holders and insider trades" +
      (cur.ch.ask.length ? ", and answering your questions" : "") + "…"));
    getJSON(API + "/research/" + encodeURIComponent(t) + (q ? "?" + q : "")).then(function (d) {
      if (cur.task !== "research" || cur.t !== t) return;
      renderResearch(d);
    }, function (e) {
      if (cur.task !== "research" || cur.t !== t) return;
      draw("research", 2, failed(e.status === 404 ? "No listed US company has the ticker " + t + ". Choose another company."
        : (e.message || "The research brief could not be loaded.") + " Check your connection.", e.status !== 404),
        "<button type='button' class='tk-back' id='tk-other'>Choose Another Company</button>");
      $("tk-other").onclick = function () { research("", true); };
      if ($("tk-retry")) $("tk-retry").onclick = function () { researchBrief(); };
    });
  }

  // one block of the server's document, drawn as the page shows it
  function blockHtml(b, i, all) {
    // Key Points beside Key Data, as the downloads lay them out
    if (b.t === "points") {
      var kd = (all || []).filter(function (x) { return x.t === "keydata"; })[0];
      return "<div class='rs-top'><div class='rs-points'><h4 class='rs-k'>Key Points</h4><ul>" + b.items.map(function (x) {
        return "<li>" + esc(x) + "</li>"; }).join("") + "</ul></div>" +
        (kd ? "<aside class='rs-kd' aria-label='Key data'><h4 class='rs-k'>Key Data</h4><dl>" + kd.items.map(function (x) {
          return "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd></div>"; }).join("") + "</dl></aside>" : "") + "</div>";
    }
    if (b.t === "keydata") return "";
    if (b.t === "h") return "<h3" + (b.id ? " id='rs-" + esc(b.id) + "'" : "") + ">" + esc(b.text) + "</h3>";
    if (b.t === "p") {
      if (b.style === "note") return "<p class='tk-note'>" + esc(b.text) + (b.signin ? " <a href='" + esc(signinHere()) + "'>Sign in or create one</a>" : "") + "</p>";
      if (b.style === "sub") return "<h4 class='rs-sub'>" + esc(b.text) + "</h4>";
      return "<p class='tk-biz'>" + esc(b.text) + "</p>";
    }
    if (b.t === "stats") {
      return "<dl class='an-figs tk-figs" + (b.items.length < 3 ? " tk-figs-2" : "") + "'>" + b.items.map(function (x) {
        return "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + (x[2] ? "<small>" + esc(x[2]) + "</small>" : "") + "</dd></div>";
      }).join("") + "</dl>";
    }
    if (b.t === "table") {
      var strong = b.strong || [], muted = b.muted || [];
      var exn = (all || []).slice(0, i + 1).filter(function (x) { return x.t === "table" && x.title; }).length;
      return (b.title ? "<p class='rs-ex'><b>Exhibit " + exn + ":</b> " + esc(b.title) + "</p>" : "") + "<div class='tk-table'><table class='tk-tab' data-no-enhance><thead><tr>" + b.cols.map(function (c) {
        return "<th" + (c[1] === "r" ? " class='r'" : "") + ">" + esc(c[0]) + "</th>";
      }).join("") + "</tr></thead><tbody>" + b.rows.map(function (r, i) {
        var cls = strong.indexOf(i) >= 0 ? "tk-me" : muted.indexOf(i) >= 0 ? "tk-med" : "";
        return "<tr" + (cls ? " class='" + cls + "'" : "") + ">" + r.map(function (v, j) {
          if (j === 0 && b.tickers) {
            var tk = b.tickers[i], lg = b.logos ? { logo: b.logos[i] } : null;
            if (tk) return "<td class='tk-wrap'><button type='button' class='tk-coname tk-linkbtn' data-rs='" + esc(tk) + "' title='Research " + esc(tk) + "'>" + logo(lg, 18) + esc(v) + "</button></td>";
            if (lg && lg.logo) return "<td class='tk-wrap'><span class='tk-coname'>" + logo(lg, 18) + esc(v) + "</span></td>";
          }
          return "<td class='" + (b.cols[j][1] === "r" ? "r" : "tk-wrap") + "'>" + esc(v) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>" + ((b.source || b.note) ? "<p class='tk-note'>" +
        esc([b.source ? "Source: " + b.source : "", b.note || ""].filter(Boolean).join(" ")) + "</p>" : "");
    }
    if (b.t === "list") {
      var tag = b.ordered ? "ol" : "ul";
      return "<" + tag + " class='tk-list'>" + b.items.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</" + tag + ">";
    }
    if (b.t === "quote") return "<blockquote class='rs-quote'><p>" + esc(b.text) + "</p>" + (b.cite ? "<cite>" + esc(b.cite) + "</cite>" : "") + "</blockquote>";
    if (b.t === "links") {
      if (b.style === "note") {
        return b.items.map(function (x) { return "<p class='tk-note'>" + (x[2] ? "<a href='" + esc(x[2]) + "' rel='noopener'>" + esc(x[1]) + "</a>" : esc(x[1])) + "</p>"; }).join("");
      }
      return "<ul class='tk-list tk-news'>" + b.items.map(function (x) {
        return "<li>" + (x[0] ? "<span class='d'>" + esc(x[0]) + "</span>" : "") + (x[2] ? "<a href='" + esc(x[2]) + "' rel='noopener'>" + esc(x[1]) + "</a>" : esc(x[1])) + "</li>";
      }).join("") + "</ul>";
    }
    if (b.t === "notes") {
      var text = readNotes(cur.t).trim();
      if (!text) return "";
      return "<h3 id='rs-notes'>Your Notes</h3><p class='tk-note'>Written by you, not by Plover. They stay in this browser and go into your downloads.</p>" +
        text.split("\n").filter(function (x) { return x.trim(); }).map(function (x) { return "<p class='tk-biz'>" + esc(x) + "</p>"; }).join("");
    }
    return "";
  }

  var DL = [["pdf", "PDF"], ["docx", "Word"], ["xlsx", "Excel"]];
  function downloadBar(where) {
    return "<div class='rs-bar' role='group' aria-label='Download the brief'><span class='tk-next-h'>Download</span>" + DL.map(function (x) {
      return "<button type='button' class='" + (x[0] === "pdf" ? "tk-go" : "tk-opt") + "' data-dl='" + x[0] + "'>" + x[1] + "</button>";
    }).join("") + "<button type='button' class='tk-opt' data-change='1'>Change What Goes In</button>" +
      "<span class='tk-note rs-dl-note' id='rs-dl-" + where + "' role='status'></span></div>";
  }
  function download(fmt, btn, noteId) {
    var t = cur.t, name = { pdf: "PDF", docx: "Word file", xlsx: "Excel file" }[fmt];
    var all = panel.querySelectorAll("[data-dl]");
    Array.prototype.forEach.call(all, function (b) { b.disabled = true; });
    var label = btn.textContent;
    btn.textContent = "Making the " + name + "…";
    $(noteId).textContent = "";
    fetch(API + "/research/" + encodeURIComponent(t) + "/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: fmt, query: rQuery(cur.ch), notes: readNotes(t) }),
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) { throw new Error(e.detail || ""); });
      var cd = r.headers.get("Content-Disposition") || "", m = cd.match(/filename="([^"]+)"/);
      return r.blob().then(function (blob) { return [blob, m ? m[1] : t + "-research." + fmt]; });
    }).then(function (got) {
      var a = document.createElement("a"), url = URL.createObjectURL(got[0]);
      a.href = url; a.download = got[1];
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      if ($(noteId)) $(noteId).textContent = "Downloaded " + got[1] + ".";
    }, function (e) {
      if ($(noteId)) $(noteId).textContent = (e.message ? e.message + " " : "The " + name + " could not be made. ") + "Check your connection, then try again.";
    }).then(function () {
      btn.textContent = label;
      Array.prototype.forEach.call(panel.querySelectorAll("[data-dl]"), function (b) { b.disabled = false; });
    });
  }

  function renderResearch(d) {
    var co = d.company, tk = co.ticker, doc = d.doc;
    var calc = (doc.calc || []).map(function (x) { return "<li><b>" + esc(x[0]) + "</b> = " + esc(x[1]) + "</li>"; }).join("");
    var body =
      "<div class='tk-co-head'>" + logo(co, 28) + "<b>" + esc(co.name) + "</b><span class='t'>" + esc(tk) + "</span>" +
        (doc.meta[0] ? "<span class='tk-co-meta'>" + esc(doc.meta[0]) + "</span>" : "") +
        "<a href='" + esc(co.page) + "'>Open the company page</a></div>" +
      (doc.subtitle ? "<p class='tk-headline hit'>" + esc(doc.subtitle) + "</p>" : "") +
      downloadBar("top") +
      doc.blocks.map(function (b, i) { return blockHtml(b, i, doc.blocks); }).join("") +
      (calc ? "<details class='tk-calc'><summary>Show calculation</summary><ul>" + calc + "</ul></details>" : "") +
      "<p class='tk-src'>Sources: " + esc(doc.sources.join("; ")) + ". " + esc(doc.meta[1] || "") + "</p>" +
      downloadBar("end");
    var nexts = [
      ["preview", "Earnings Preview for " + tk],
      ["chart", "Chart " + tk + " Against the S&P 500"],
      ["ask", "Ask a Question About " + tk],
      ["other", "Research Another Company"],
    ];
    draw("research", 2, body, "<span class='tk-next-h'>Next Step</span>" + nexts.map(function (x, i) {
      return "<button type='button' class='" + (i ? "tk-opt" : "tk-go") + "' data-next='" + x[0] + "'>" + esc(x[1]) + "</button>";
    }).join(""));
    document.title = co.name + " Research · Plover Analytics";
    Array.prototype.forEach.call(panel.querySelectorAll("[data-dl]"), function (b) {
      b.onclick = function () { download(b.getAttribute("data-dl"), b, b.closest(".rs-bar").querySelector(".rs-dl-note").id); };
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-change]"), function (b) {
      b.onclick = function () { cur.redraw = false; rUrl("build", true); researchBuild(); window.scrollTo(0, 0); };
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-rs]"), function (b) {
      b.onclick = function () {
        // the same choices for the peer, without this company's own peers, topics and questions
        var keep = new URLSearchParams(rQuery(cur.ch));
        ["p", "k", "ask"].forEach(function (k) { keep.delete(k); });
        research(b.getAttribute("data-rs"), true, keep, "brief");
      };
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-next]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-next");
        if (k === "preview") return earnings(tk, true);
        if (k === "chart") return chartStart({ m: "price", s: [{ ticker: tk, name: co.name }], p: "1y" }, true, true);
        // the reader's own question: the box holds the ticker, the cursor after it
        if (k === "ask") {
          var q = $("q");
          q.value = tk + " ";
          window.scrollTo(0, 0);
          q.focus();
          q.setSelectionRange(q.value.length, q.value.length);
          return;
        }
        research("", true);
      };
    });
  }

  /* ---- entry points --------------------------------------------------- */
  function open(task, push) {
    if (task === "research") return research("", push);
    if (task === "earnings") return earnings("", push);
    if (task === "chart") return chartStart({}, push);
    if (task === "screen") return screenStart({}, push);
    if (task === "insiders") return insidersStart({}, push);
  }
  // us-stocks.js route(): an address with ?task= opens that task at the step it names
  var landedOn = new URLSearchParams(location.search).get("task");
  function route(u) {
    var p = u.searchParams, task = p.get("task");
    // the page opened on a task's address (a shared link, a bookmark): once
    if (landedOn && task === landedOn && TITLES[task]) note(task, "open", { from: "link" });
    landedOn = null;
    if (task === "research") return research((p.get("c") || "").trim().toUpperCase().slice(0, 12), false, p, p.get("step") === "build" ? "build" : "brief");
    if (task === "earnings") return earnings((p.get("c") || "").trim().toUpperCase().slice(0, 12), false, +p.get("w") || 1);
    if (task === "chart") {
      var m = measure(p.get("m")) ? p.get("m") : "", raw = (p.get("s") || "").split(",");
      var items = m === "sectors"
        ? raw.filter(sector).slice(0, 5).map(function (k) { return { ticker: k, name: sector(k).name }; })
        : raw.filter(function (t) { return /^[A-Za-z.\-]{1,10}$/.test(t); }).slice(0, 5).map(function (t) { return { ticker: t.toUpperCase() }; });
      return chartStart({ m: m, s: items, p: p.get("p") || "" }, false, m === "sectors" && p.get("go") === "1");
    }
    if (task === "screen") return screenStart({ size: p.get("size"), sec: p.get("sec"), sp500: p.get("sp") === "1", f: (p.get("f") || "").split(","), x: readTags(p.get("x")) }, false, p.get("step"));
    if (task === "insiders") return insidersStart({ who: p.get("who"), d: +p.get("d") || 0, min: +p.get("min") || 0, sp500: p.get("sp") === "1" }, false, p.get("step"));
    close(false);
    return false;
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-task]"), function (b) {
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      var task = b.getAttribute("data-task");
      note(task, "open", { from: b.classList.contains("dm-try") ? "demo" : "menu" });
      open(task, true);
    });
  });
  window.ploverTasks = { route: route, hide: function () { close(false); }, open: open };
})();
