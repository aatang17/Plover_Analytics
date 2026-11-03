/* index.html: the front page's moving parts that show what Plover does before
   anyone types — a demonstration of each task (Earnings Preview, Draw Charts,
   Screen Stocks, Insider Buying) playing its steps through, and the Example
   Answers drawing themselves in when they come into view.

   Every figure here is real: taken from Plover's own endpoints on 2026-11-03
   (/api/v1/us/prices, /us/financials/screener, /us/preview, /us/insiders/buying)
   over prices to 29 October 2026 and insider filings to 30 September 2026, and
   each demonstration says which. Try It opens the task itself on today's data.
   A reader who asks for less motion (prefers-reduced-motion) sees each
   demonstration's last frame, nothing moving, and no automatic advance. */
"use strict";

(function () {
  var root = document.getElementById("demo");
  if (!root) return;
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  var money = window.usFmt.money;
  function pct(v, signed) { return window.usFmt.pct(v, 1, signed); }
  function arrow(v) {
    return "<span class='" + (v > 0 ? "up" : "down") + "'>" + (v > 0 ? "▲ " : "▼ ") + pct(v, true) + "</span>";
  }

  /* ---- the figures (see the note at the top) ---- */
  var CHART = {
    when: "Daily closes, 29 October 2025 to 29 October 2026, start = 100. The first close of each month is drawn.",
    series: [
      { name: "Apple", key: "AAPL", slot: 1, pts: [["2025-10-29", 100], ["2025-11-03", 99.76], ["2025-12-01", 104.97], ["2026-01-02", 100.49], ["2026-02-02", 100.11], ["2026-03-02", 98.15], ["2026-04-01", 94.78], ["2026-05-01", 103.87], ["2026-06-01", 113.57], ["2026-07-01", 109.15], ["2026-08-03", 112.5], ["2026-09-01", 120.55], ["2026-10-01", 122.48], ["2026-10-29", 129.37]] },
      { name: "Microsoft", key: "MSFT", slot: 2, pts: [["2025-10-29", 100], ["2025-11-03", 95.47], ["2025-12-01", 89.88], ["2026-01-02", 87.33], ["2026-02-02", 78.18], ["2026-03-02", 73.59], ["2026-04-01", 68.21], ["2026-05-01", 76.53], ["2026-06-01", 85.04], ["2026-07-01", 70.96], ["2026-08-03", 90.05], ["2026-09-01", 92.52], ["2026-10-01", 94.69], ["2026-10-29", 97.89]] },
      { name: "S&P 500", key: "SPY", slot: 6, pts: [["2025-10-29", 100], ["2025-11-03", 99.41], ["2025-12-01", 98.96], ["2026-01-02", 99.39], ["2026-02-02", 101.17], ["2026-03-02", 99.85], ["2026-04-01", 95.32], ["2026-05-01", 104.84], ["2026-06-01", 110.35], ["2026-07-01", 108.49], ["2026-08-03", 110.22], ["2026-09-01", 110.82], ["2026-10-01", 111.14], ["2026-10-29", 111.92]] },
    ],
  };
  var SCREEN = {
    matched: 21, when: "Latest annual reports; prices to 29 October 2026.",
    rows: [["Uber Technologies", "UBER", 134277902355, 18.28, 13.9], ["Progressive", "PGR", 133820154019, 16.32, 12.0],
           ["Adobe", "ADBE", 96568304000, 10.53, 14.9], ["The Cigna Group", "CI", 78852003427, 11.24, 13.5],
           ["Cheniere Energy", "LNG", 65872004353, 27.21, 13.2]],
  };
  var EARN = {
    typical: 4.53, since: 16.87, sp: 1.54, revenue: 11536000000, growth: 50.11,
    when: "Prices to 29 October 2026. The expected date is an estimate: a year after the same quarter's results.",
    moves: [["Q3 2024", -7.08], ["Q4 2024", -1.98], ["Q1 2025", -0.23], ["Q2 2025", -7.73], ["Q3 2025", -1.28], ["Q4 2025", -18.71], ["Q1 2026", 23.38], ["Q2 2026", -0.53]],
  };
  var INS = {
    when: "Form 4 filings to 30 September 2026; share prices to 29 October 2026.",
    rows: [["Uber Technologies", "UBER", 15313243, "Dara Khosrowshahi, Chief Executive Officer", -13.23],
           ["Fox Corporation", "FOXA", 10274977, "Lachlan Murdoch, Executive Chair, CEO", -4.53],
           ["Intel", "INTC", 9999985, "Lip-Bu Tan, CEO", -5.77],
           ["Aptiv", "APTV", 7502194, "Paul Meister, Director", -5.49],
           ["Aon", "AON", 5387367, "Lester Knight, Director", -2.79]],
  };

  /* ---- a timeline: steps at set times, cancelled when another demo starts ---- */
  var timers = [], playing = null, auto = !still, inView = false, order = ["chart", "screen", "earnings", "insiders"];
  function clear() { timers.forEach(clearTimeout); timers = []; }
  function at(ms, fn) { timers.push(setTimeout(fn, still ? 0 : ms)); }
  var stage = root.querySelector(".dm-stage"), list = root.querySelector(".dm-list"), bar = null;

  function frame(title, steps, on, body) {
    return "<div class='dm-head'><b>" + esc(title) + "</b><ol>" + steps.map(function (s, i) {
      return "<li class='" + (i === on ? "on" : i < on ? "done" : "") + "'>" + (i + 1) + ". " + esc(s) + "</li>";
    }).join("") + "</ol></div><div class='dm-body'>" + body + "</div>";
  }
  function show(html) {
    stage.innerHTML = html;
    stage.classList.remove("dm-in");
    void stage.offsetWidth;                      // restart the fade
    stage.classList.add("dm-in");
  }
  function pick(sel) { var el = stage.querySelector(sel); if (el) el.classList.add("on"); }
  function opts(list, cls) {
    return "<div class='dm-opts" + (cls ? " " + cls : "") + "'>" + list.map(function (o) {
      return "<span data-o='" + esc(o[0]) + "'><b>" + esc(o[1]) + "</b>" + (o[2] ? "<i>" + esc(o[2]) + "</i>" : "") + "</span>";
    }).join("") + "</div>";
  }
  function typeInto(sel, text, start, each) {
    for (var i = 1; i <= text.length; i++) {
      (function (n) { at(start + n * each, function () { var el = stage.querySelector(sel); if (el) el.textContent = text.slice(0, n); }); })(i);
    }
    return start + text.length * each;
  }
  function next(items) {
    return "<div class='dm-next'><span>Next Step</span>" + items.map(function (x) { return "<em>" + esc(x) + "</em>"; }).join("") + "</div>";
  }
  function reveal(sel, gap, start) {
    Array.prototype.forEach.call(stage.querySelectorAll(sel), function (el, i) { at(start + i * gap, function () { el.classList.add("dm-shown"); }); });
  }

  /* ---- Draw Charts ---- */
  function chartSvg() {
    var W = 560, H = 210, L = 34, R = 116, T = 10, B = 22;
    var t0 = Date.parse(CHART.series[0].pts[0][0]), t1 = Date.parse(CHART.series[0].pts[CHART.series[0].pts.length - 1][0]);
    var lo = 60, hi = 135;
    var x = function (d) { return L + (Date.parse(d) - t0) / (t1 - t0) * (W - L - R); };
    var y = function (v) { return T + (hi - v) / (hi - lo) * (H - T - B); };
    var g = [70, 85, 100, 115, 130].map(function (v) {
      return "<line x1='" + L + "' x2='" + (W - R) + "' y1='" + y(v) + "' y2='" + y(v) + "' class='dm-grid'/><text x='" + (L - 6) + "' y='" + (y(v) + 4) + "' class='dm-ax' text-anchor='end'>" + v + "</text>";
    }).join("");
    var lines = CHART.series.map(function (s) {
      var d = s.pts.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); }).join("");
      var last = s.pts[s.pts.length - 1];
      return "<path d='" + d + "' pathLength='1' class='dm-line' style='stroke:var(--obs-series-" + s.slot + ")'/>" +
        "<text x='" + (W - R + 6) + "' y='" + (y(last[1]) + 4) + "' class='dm-end' style='fill:var(--obs-series-" + s.slot + ")'>" + esc(s.name) + " " + pct(last[1] - 100, true) + "</text>";
    }).join("");
    var months = [["2026-01-02", "Jan 2026"], ["2026-04-01", "Apr"], ["2026-07-01", "Jul"], ["2026-10-01", "Oct"]].map(function (m) {
      return "<text x='" + x(m[0]) + "' y='" + (H - 4) + "' class='dm-ax' text-anchor='middle'>" + m[1] + "</text>";
    }).join("");
    return "<svg viewBox='0 0 " + W + " " + H + "' class='dm-svg' role='img' aria-label='Apple, Microsoft and the S&amp;P 500 over a year, start = 100'>" + g + months + lines + "</svg>";
  }
  function playChart() {
    var steps = ["What to Chart", "Companies", "Period"];
    show(frame("Draw Charts", steps, 0, "<p class='dm-q'>What would you like to chart?</p>" +
      opts([["price", "Share Price", "Against the S&P 500"], ["rev", "Revenue", "By year"], ["profit", "Net Profit", "By year"]])));
    at(900, function () { pick("[data-o=price]"); });
    at(1700, function () {
      show(frame("Draw Charts", steps, 1, "<p class='dm-q'>Which companies?</p><div class='dm-chips'></div><div class='dm-input'><span class='dm-typed'></span><i class='dm-caret'></i></div>"));
    });
    var t = typeInto(".dm-typed", "Apple", 2100, 110);
    at(t + 250, function () { stage.querySelector(".dm-chips").innerHTML = "<span>Apple <b>AAPL</b></span>"; stage.querySelector(".dm-typed").textContent = ""; });
    t = typeInto(".dm-typed", "Microsoft", t + 500, 90);
    at(t + 250, function () { stage.querySelector(".dm-chips").innerHTML += "<span>Microsoft <b>MSFT</b></span>"; stage.querySelector(".dm-typed").textContent = ""; });
    at(t + 900, function () {
      show(frame("Draw Charts", steps, 2, "<p class='dm-q'>Over what period?</p>" + opts([["ytd", "This Year"], ["1y", "Past Year"], ["5y", "Past 5 Years"]], "dm-row")));
    });
    at(t + 1600, function () { pick("[data-o='1y']"); });
    at(t + 2400, function () {
      show("<div class='dm-res'><p class='dm-hl'>Apple beat the S&amp;P 500 over the year; Microsoft fell behind.</p>" + chartSvg() +
        "<p class='dm-src'>" + esc(CHART.when) + " Source: Databento.</p>" + next(["Add a Company", "Change the Period", "Show Revenue Instead"]) + "</div>");
      // a frame after the lines are on the page, so the drawing shows as a movement
      at(80, function () { Array.prototype.forEach.call(stage.querySelectorAll(".dm-line"), function (p) { p.classList.add("dm-draw"); }); });
      reveal(".dm-end, .dm-next", 250, 1700);
    });
    return t + 9000;
  }

  /* ---- Screen Stocks ---- */
  function playScreen() {
    var steps = ["Company Size", "What to Look For", "Results"];
    show(frame("Screen Stocks", steps, 0, "<p class='dm-q'>What size of company?</p>" +
      opts([["any", "Any Size", "Every company on file"], ["large", "Large", "Market value over $10bn"], ["mid", "Mid-Sized", "$2bn to $10bn"]])));
    at(900, function () { pick("[data-o=large]"); });
    at(1700, function () {
      show(frame("Screen Stocks", steps, 1, "<p class='dm-q'>What are you looking for?</p>" +
        opts([["growth", "Growing Sales", "Revenue up more than 10%"], ["cheap", "Cheap on Earnings", "Under 15 times earnings"], ["div", "High Dividend", "Yield above 3%"]])));
    });
    at(2500, function () { pick("[data-o=growth]"); });
    at(3200, function () { pick("[data-o=cheap]"); });
    at(4100, function () {
      show("<div class='dm-res'><p class='dm-hl'>" + SCREEN.matched + " large companies are growing sales above 10% at under 15 times earnings.</p>" +
        "<table class='dm-tab' data-no-enhance><thead><tr><th>Company</th><th class='r'>Market Value</th><th class='r'>Revenue Growth</th><th class='r'>Price to Earnings</th></tr></thead><tbody>" +
        SCREEN.rows.map(function (r) {
          return "<tr class='dm-row-in'><td>" + esc(r[0]) + " <b>" + esc(r[1]) + "</b></td><td class='r'>" + money(r[2]) + "</td><td class='r'>" + pct(r[3]) + "</td><td class='r'>" + fmtNum(r[4], 1) + "×</td></tr>";
        }).join("") + "</tbody></table><p class='dm-src'>The 5 largest of " + SCREEN.matched + ". " + esc(SCREEN.when) + " Source: SEC EDGAR.</p>" +
        next(["Chart the Top 3", "Earnings Preview for UBER", "Change What to Look For"]) + "</div>");
      reveal(".dm-row-in", 180, 200);
      reveal(".dm-next", 0, 1400);
    });
    return 12000;
  }

  /* ---- Earnings Preview ---- */
  function playEarnings() {
    show(frame("Earnings Preview", ["Choose a Company", "Read the Preview"], 0, "<p class='dm-q'>Which company?</p>" +
      "<div class='dm-input'><span class='dm-typed'></span><i class='dm-caret'></i></div><div class='dm-drop'></div>"));
    var t = typeInto(".dm-typed", "AMD", 500, 160);
    at(t + 300, function () { stage.querySelector(".dm-drop").innerHTML = "<span>Advanced Micro Devices, Inc. <b>AMD</b></span>"; });
    at(t + 1000, function () { pick(".dm-drop span"); });
    at(t + 1700, function () {
      var mx = 25;
      show("<div class='dm-res'><p class='dm-hl'>AMD is expected to report results around Tuesday 3 November 2026.</p>" +
        "<dl class='dm-tiles'><div><dt>Typical Move on Results</dt><dd>" + pct(EARN.typical) + "</dd></div>" +
        "<div><dt>Since the Last Results</dt><dd>" + arrow(EARN.since) + "</dd></div>" +
        "<div><dt>Revenue, Q2 2026</dt><dd>" + money(EARN.revenue) + "</dd><small>" + pct(EARN.growth, true) + " on a year earlier</small></div></dl>" +
        "<p class='dm-sub'>Share price around each results day, the day after</p><div class='dm-bars'>" + EARN.moves.map(function (m) {
          var h = Math.abs(m[1]) / mx * 50;
          return "<div><span class='v " + (m[1] > 0 ? "up" : "down") + "'>" + pct(m[1], true) + "</span><div class='b'><i class='" + (m[1] > 0 ? "pos" : "neg") +
            "' style='height:" + h.toFixed(1) + "%'></i></div><span class='q'>" + esc(m[0]) + "</span></div>";
        }).join("") + "</div><p class='dm-src'>" + esc(EARN.when) + " Source: SEC EDGAR, Databento.</p>" +
        next(["Chart AMD Against the S&P 500", "Who Owns AMD?"]) + "</div>");
      reveal(".dm-tiles > div", 220, 150);
      at(900, function () { var b = stage.querySelector(".dm-bars"); if (b) b.classList.add("dm-grow"); });
      reveal(".dm-next", 0, 2200);
    });
    return t + 10500;
  }

  /* ---- Insider Buying ---- */
  function playInsiders() {
    var steps = ["Who Is Buying", "Period and Size", "Results"];
    show(frame("Insider Buying", steps, 0, "<p class='dm-q'>Whose buying do you want to see?</p>" +
      opts([["boss", "Company Bosses", "Directors and officers"], ["all", "Anyone Who Files", "Big shareholders too"]])));
    at(900, function () { pick("[data-o=boss]"); });
    at(1700, function () {
      show(frame("Insider Buying", steps, 1, "<p class='dm-q'>How far back, and how big?</p>" +
        opts([["w", "This Week"], ["m", "Past Month"], ["q", "Past 3 Months"]], "dm-row") + opts([["any", "Any Size"], ["100k", "Over $100,000"], ["1m", "Over $1mn"]], "dm-row") +
        "<p class='dm-check'><i></i> Only companies in the S&amp;P 500</p>"));
    });
    at(2400, function () { pick("[data-o=q]"); });
    at(3000, function () { pick("[data-o='1m']"); });
    at(3600, function () { pick(".dm-check"); });
    at(4400, function () {
      show("<div class='dm-res'><p class='dm-hl'>Bosses at 10 S&amp;P 500 companies bought $53.5mn of their own shares in three months.</p>" +
        "<table class='dm-tab' data-no-enhance><thead><tr><th>Company</th><th class='r'>Bought</th><th>Biggest Buyer</th><th class='r'>Share Price Since</th></tr></thead><tbody>" +
        INS.rows.map(function (r) {
          return "<tr class='dm-row-in'><td>" + esc(r[0]) + " <b>" + esc(r[1]) + "</b></td><td class='r'>" + money(r[2]) + "</td><td>" + esc(r[3]) + "</td><td class='r'>" + arrow(r[4]) + "</td></tr>";
        }).join("") + "</tbody></table><p class='dm-src'>The 5 largest of 10. " + esc(INS.when) + " Source: SEC EDGAR.</p>" +
        next(["See UBER's Insider Trades", "Earnings Preview for UBER"]) + "</div>");
      reveal(".dm-row-in", 180, 200);
      reveal(".dm-next", 0, 1400);
    });
    return 12500;
  }

  var PLAYS = { chart: playChart, screen: playScreen, earnings: playEarnings, insiders: playInsiders };
  var TRY = { chart: "Draw Charts", screen: "Screen Stocks", earnings: "Earnings Preview", insiders: "Insider Buying" };

  function play(name, byHand) {
    clear();
    playing = name;
    if (byHand) auto = false;                 // a reader who picks one stays on it
    Array.prototype.forEach.call(list.querySelectorAll("button"), function (b) {
      var on = b.getAttribute("data-demo") === name;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    });
    var tryIt = root.querySelector(".dm-try");
    tryIt.textContent = "Try " + TRY[name];
    tryIt.setAttribute("href", "/?task=" + name);
    tryIt.setAttribute("data-task", name);
    var length = PLAYS[name]();
    bar = root.querySelector(".dm-list button.on .dm-bar");
    if (bar && !still) {
      bar.style.transition = "none"; bar.style.width = "0";
      void bar.offsetWidth;
      bar.style.transition = "width " + length + "ms linear"; bar.style.width = "100%";
    }
    at(length, function () {
      if (auto && inView) play(order[(order.indexOf(name) + 1) % order.length]);
      else if (!byHand && !inView) playing = null;      // starts again when it comes back into view
    });
  }

  list.addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-demo]");
    if (b) play(b.getAttribute("data-demo"), true);
  });
  root.querySelector(".dm-replay").addEventListener("click", function () { play(playing || "chart", true); });

  // play only while it can be seen: nothing runs above the fold for nobody
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      inView = es[0].isIntersecting;
      if (inView && !playing) play(playing || "chart");
    }, { threshold: 0.35 }).observe(root);
  } else {
    inView = true;
    play("chart");
  }

  /* ---- the Example Answers draw themselves in when they come into view ---- */
  var cards = document.querySelectorAll(".hp-card");
  if (!still && cards.length && "IntersectionObserver" in window) {
    Array.prototype.forEach.call(cards, function (c) { c.classList.add("hp-anim"); });
    var seen = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("hp-anim-on");
        seen.unobserve(e.target);
      });
    }, { threshold: 0.3 });
    Array.prototype.forEach.call(cards, function (c) { seen.observe(c); });
  }
})();
