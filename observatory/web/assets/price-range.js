/* A company's share price over a range the reader picks: presets (1M to Max,
   and the year to date) or a custom from–to, with the change over that range,
   its first and last close, and its low and high. Used on a US company's
   Overview (us-company.js) and its Price tab (us-stocks.js).

   priceRange.mount(el, { bars, name, ticker, source }) draws into el and
   returns the chart handle's owner, { dispose }. bars are the prices API's
   daily bars, oldest first. The range is kept in the address (?range=ytd, or
   ?from=…&to=…) with the page's other parameters, so a view is a link.

   Change = last close in the range ÷ first close in the range − 1, on closes
   adjusted for splits (never for dividends). */
(function () {
  "use strict";

  var PRESETS = [["1M", 30], ["3M", 91], ["6M", 182], ["YTD", "ytd"], ["1Y", 365], ["3Y", 1095], ["5Y", 1826], ["Max", "max"]];

  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function iso(d) { return d.toISOString().slice(0, 10); }
  function dayShort(s) {
    var d = new Date(s + "T00:00:00Z");
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()].slice(0, 3) + " " + d.getUTCFullYear();
  }
  function close(b) { return b.adjusted_close != null ? b.adjusted_close : b.close; }

  // the first and last day of a preset, measured back from the latest close
  function window_(preset, last) {
    var end = new Date(last + "T00:00:00Z"), start;
    if (preset === "max") return [null, last];
    if (preset === "ytd") start = new Date(Date.UTC(end.getUTCFullYear(), 0, 1));
    else start = new Date(end.getTime() - preset * 864e5);
    return [iso(start), last];
  }

  function mount(el, opts) {
    var bars = (opts.bars || []).filter(function (b) { return close(b) != null; });
    if (bars.length < 2) {
      el.innerHTML = "<p class='state-empty'>No daily prices on file for " + esc(opts.ticker) + ".</p>";
      return { dispose: function () {} };
    }
    var first = bars[0].date, last = bars[bars.length - 1].date;
    var u = new URLSearchParams(location.search);
    var state = { preset: "1Y", from: null, to: null };
    var asked = (u.get("range") || "").toUpperCase();
    if (u.get("from") || u.get("to")) { state.preset = null; state.from = u.get("from"); state.to = u.get("to"); }
    else if (PRESETS.some(function (p) { return p[0].toUpperCase() === asked; })) {
      state.preset = PRESETS.filter(function (p) { return p[0].toUpperCase() === asked; })[0][0];
    } else if (opts.initial) state.preset = opts.initial;

    el.innerHTML =
      "<div class='pr-controls'>" +
      "<div class='seg pr-presets' role='group' aria-label='Range'>" + PRESETS.map(function (p) {
        return "<button type='button' data-p='" + p[0] + "'>" + p[0] + "</button>";
      }).join("") + "</div>" +
      "<span class='pr-custom'><label>From <input type='date' class='pr-from' min='" + first + "' max='" + last + "'></label>" +
      "<label>To <input type='date' class='pr-to' min='" + first + "' max='" + last + "'></label></span>" +
      "<span class='pr-acts'><button type='button' class='pr-link pr-png'>Download PNG</button>" +
      "<button type='button' class='pr-link pr-csv'>Download CSV</button></span></div>" +
      "<p class='pr-perf' aria-live='polite'></p>" +
      "<div class='chart-el pr-chart'></div>";
    var chartEl = el.querySelector(".pr-chart"), chart = null;

    function range() {
      var w = state.preset ? window_(PRESETS.filter(function (p) { return p[0] === state.preset; })[0][1], last)
                           : [state.from || first, state.to || last];
      var from = w[0] && w[0] > first ? w[0] : first, to = w[1] && w[1] < last ? w[1] : last;
      if (from > to) to = last;
      return [from, to];
    }

    function keepInUrl() {
      var q = new URLSearchParams(location.search);
      q.delete("range"); q.delete("from"); q.delete("to");
      if (state.preset && state.preset !== (opts.initial || "1Y")) q.set("range", state.preset.toLowerCase());
      if (!state.preset) { if (state.from) q.set("from", state.from); if (state.to) q.set("to", state.to); }
      var s = q.toString();
      history.replaceState(history.state, "", location.pathname + (s ? "?" + s : ""));
    }

    function draw() {
      var r = range();
      var shown = bars.filter(function (b) { return b.date >= r[0] && b.date <= r[1]; });
      Array.prototype.forEach.call(el.querySelectorAll(".pr-presets button"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-p") === state.preset ? "true" : "false");
      });
      el.querySelector(".pr-from").value = r[0];
      el.querySelector(".pr-to").value = r[1];
      if (chart) { try { chart.dispose(); } catch (e) { /* gone */ } chart = null; }
      if (shown.length < 2) {
        el.querySelector(".pr-perf").textContent = "Fewer than two closes between " + dayShort(r[0]) + " and " + dayShort(r[1]) +
          ". Pick a wider range, or one of the buttons.";
        chartEl.innerHTML = "";
        return;
      }
      var v = shown.map(close), a = v[0], z = v[v.length - 1], chg = (z / a - 1) * 100;
      var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v);
      el.querySelector(".pr-perf").innerHTML =
        "<span class='" + (chg >= 0 ? "pr-up" : "pr-down") + "' title='last close ÷ first close in the range − 1, split-adjusted'>" +
        (chg >= 0 ? "▲ +" : "▼ " + MINUS) + fmtNum(Math.abs(chg), 1) + "%</span> " +
        "<span class='pr-sub'>$" + fmtNum(a, 2) + " on " + esc(dayShort(shown[0].date)) + " → $" + fmtNum(z, 2) + " on " +
        esc(dayShort(shown[shown.length - 1].date)) + " · low $" + fmtNum(lo, 2) + " · high $" + fmtNum(hi, 2) + "</span>";
      chart = obsChart(chartEl, "line", { unit: "", unitSuffix: " $", dp: 2, trust: "official", sourceLine: opts.source,
        isoPeriods: true, yAxisName: "USD", series: [{ name: opts.ticker || "Close", points: shown.map(function (b, i) { return [b.date, v[i]]; }) }] });
      el.querySelector(".pr-png").onclick = function () { chart.exportPNG("plover-" + opts.ticker + "-price.png"); };
      el.querySelector(".pr-csv").onclick = function () {
        chart.exportCSV("plover-" + opts.ticker + "-price.csv", [opts.name + " daily close, split-adjusted, " + r[0] + " to " + r[1], opts.source]);
      };
    }

    Array.prototype.forEach.call(el.querySelectorAll(".pr-presets button"), function (b) {
      b.addEventListener("click", function () { state.preset = b.getAttribute("data-p"); state.from = state.to = null; keepInUrl(); draw(); });
    });
    // a date typed past the other one moves the other one, never swaps them:
    // the date the reader just chose is the one kept
    var clamp = function (d) { return d < first ? first : d > last ? last : d; };
    ["pr-from", "pr-to"].forEach(function (cls) {
      el.querySelector("." + cls).addEventListener("change", function () {
        var r = range();
        var from = clamp(el.querySelector(".pr-from").value || r[0]), to = clamp(el.querySelector(".pr-to").value || r[1]);
        if (from > to) { if (cls === "pr-from") to = last; else from = first; }
        state.preset = null; state.from = from; state.to = to;
        keepInUrl(); draw();
      });
    });
    window.addEventListener("resize", function () { if (chart && chart.render) chart.render(); });
    draw();
    return { dispose: function () { if (chart) chart.dispose(); } };
  }

  window.priceRange = { mount: mount };
})();
