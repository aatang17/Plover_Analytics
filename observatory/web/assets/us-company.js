/* US company record (us-company.html): search, or one company's record behind
   ?t=TICKER (or a CIK). The record is /api/v1/us/company/{t}: identity, and
   each item the site holds with how much, how recent and where to read it. */
(function () {
  "use strict";
  var API = "/api/v1/us/company";
  var MISSING = "—";

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          var e = new Error(b.detail || ("HTTP " + r.status)); e.status = r.status; throw e;
        });
      }
      return r.json();
    });
  }

  // ---- the company list ----------------------------------------------------------
  // The S&P 500 (its members and weights from the SPY holdings behind
  // /api/v1/us/pay) or every filer, ranked on /api/v1/us/financials/screener,
  // fifty at a time. The filter matches part of a name, a ticker or an SEC
  // number; the URL keeps the choice (?set=, ?sort=, ?q=) so a view is a link.
  var seq = 0, timer = null, members = null;
  var list = { set: "sp", sort: "market_cap:desc", q: "", offset: 0 };
  var PAGE = 50;

  function cell(v, cls) { return "<td" + (cls ? " class='" + cls + "'" : "") + ">" + v + "</td>"; }
  function moneyShort(v) {
    if (v == null) return MISSING;
    var a = Math.abs(v), s = a >= 1e12 ? fmtNum(a / 1e12, 2) + "tn" : a >= 1e9 ? fmtNum(a / 1e9, 1) + "bn" : fmtNum(a / 1e6, 0) + "mn";
    return (v < 0 ? MINUS : "") + "$" + s;
  }
  function chg(v) {
    if (v == null) return MISSING;
    return "<span class='" + (v >= 0 ? "up" : "down") + "'>" + (v >= 0 ? "▲ +" : "▼ " + MINUS) + fmtNum(Math.abs(v), 1) + "%</span>";
  }

  function syncListUrl() {
    var u = new URLSearchParams();
    if (list.set !== "sp") u.set("set", list.set);
    if (list.sort !== "market_cap:desc") u.set("sort", list.sort);
    if (list.q) u.set("q", list.q);
    history.replaceState(null, "", "us-company.html" + (u.toString() ? "?" + u : ""));
  }

  function loadList() {
    var mine = ++seq;
    syncListUrl();
    $("uc-state").textContent = "Loading…";
    var sort = list.sort.split(":");
    var url = "/api/v1/us/financials/screener?sort=" + sort[0] + "&order=" + sort[1] + "&limit=" + PAGE +
      "&offset=" + list.offset + (list.q ? "&q=" + encodeURIComponent(list.q) : "");
    var go = function () {
      if (list.set === "sp") url += "&tickers=" + encodeURIComponent(Object.keys(members).join(","));
      return getJSON(url);
    };
    // A typed filter shows the matching companies at once from the quick name
    // search (/api/v1/us/company), and their figures when the screener answers:
    // right after a restart the screener's first answer can take several seconds.
    var ranked = false;
    quick = null;
    if (list.q) {
      getJSON(API + "?q=" + encodeURIComponent(list.q)).then(function (f) {
        if (mine !== seq || ranked) return;
        var hits = f.companies.filter(function (c) { return list.set !== "sp" || !members || members[c.ticker] != null; });
        if (hits.length) renderQuick(hits);
      }).catch(function () { /* the screener's answer still comes */ });
    }
    (list.set === "sp" && !members ? getJSON("/api/v1/us/pay").then(function (d) {
      members = {};
      d.companies.forEach(function (c) { (c.tickers || [c.ticker]).forEach(function (t) { members[t] = c.weight_pct; }); });
    }) : Promise.resolve()).then(go).then(function (d) {
      if (mine !== seq) return;
      ranked = true;
      renderList(d);
    }).catch(function (e) {
      if (mine !== seq) return;
      $("uc-state").textContent = "The list is unavailable (" + e.message + "). Try again in a moment.";
      $("uc-results").innerHTML = "";
    });
  }

  var HEAD = "<thead><tr><th>#</th><th style='text-align:left'>Company</th><th class='tk'>Ticker</th>" +
    "<th>Market value</th><th class='hide-sm'>Weight in S&amp;P</th><th class='hide-sm'>P/E</th><th class='hide-sm'>Revenue</th><th>1-year change</th></tr></thead>";

  function nameCell(c, name) {
    return cell((c.logo ? "<img src='" + esc(c.logo) + "' alt='' width='20' height='20' loading='lazy'>" : "<span class='nologo'></span>") +
      "<a href='us-company.html?t=" + encodeURIComponent(c.ticker || c.cik) + "'>" + esc(name) + "</a>", "nm");
  }

  // the matches from the quick name search, while their figures load
  var quick = null;
  function renderQuick(cs) {
    quick = cs;
    $("uc-state").textContent = cs.length + (cs.length === 1 ? " match" : " matches") + " · loading figures…";
    $("uc-results").innerHTML = HEAD + "<tbody>" + cs.map(function (c) {
      var w = members && c.ticker ? members[c.ticker] : null;
      return "<tr>" + cell("", "rk") + nameCell(c, c.name) + cell(esc(c.ticker || MISSING), "tk") + cell("…") +
        cell(w == null ? MISSING : fmtNum(w, 2) + "%", "hide-sm") + cell("…", "hide-sm") + cell("…", "hide-sm") + cell("…") + "</tr>";
    }).join("") + "</tbody>";
    $("cl-prev").disabled = $("cl-next").disabled = true;
  }

  function renderList(d) {
    var rows = d.rows;
    var first = rows[0] && rows[0].price && rows[0].price.date;
    $("cl-sub").textContent = fmtNum(d.universe, 0) + " SEC filers · " + (list.set === "sp" ? "the S&P 500" : "every filer") +
      " shown, " + $("cl-sort").selectedOptions[0].textContent.toLowerCase() + (first ? " · close " + coHead.dayShort(first) : "");
    if (!rows.length && quick && list.q) {
      // found by name, but with no recent annual report to rank: keep them, figures missing
      $("uc-results").innerHTML = $("uc-results").innerHTML.replace(/>…</g, ">" + MISSING + "<");
      $("uc-state").textContent = quick.length + (quick.length === 1 ? " match" : " matches") + " · no annual report in the last 18 months to rank";
      return;
    }
    if (!rows.length) {
      $("uc-state").textContent = list.q ? "No company matches “" + list.q + "”." : "No companies to show.";
      $("uc-results").innerHTML = "<tbody><tr><td>" + (list.q ? "No company matches “" + esc(list.q) + "”" +
        (list.set === "sp" ? " in the S&amp;P 500. <button type='button' class='btn-text' id='cl-try-all'>Search all filers</button>" :
          ". Try a ticker, like AAPL, or part of the name.") : "") + "</td></tr></tbody>";
      if ($("cl-try-all")) $("cl-try-all").onclick = function () { setSet("all"); };
      $("cl-prev").disabled = $("cl-next").disabled = true;
      return;
    }
    var from = d.offset + 1, to = d.offset + rows.length;
    $("uc-state").textContent = from + "–" + to + " of " + fmtNum(d.ranked, 0) +
      (d.excluded_missing_sort ? " · " + fmtNum(d.excluded_missing_sort, 0) + " without this figure left out" : "");
    $("uc-results").innerHTML = HEAD + "<tbody>" +
      rows.map(function (r) {
        var m = r.metrics || {}, w = members && r.ticker ? members[r.ticker] : null;
        return "<tr>" + cell(r.rank, "rk") + nameCell(r, r.display_name || r.name) +
          cell(esc(r.ticker || MISSING), "tk") + cell(moneyShort(m.market_cap)) +
          cell(w == null ? MISSING : fmtNum(w, 2) + "%", "hide-sm") +
          cell(m.pe_x == null ? MISSING : fmtNum(m.pe_x, 1) + "×", "hide-sm") +
          cell(r.unit === "USD" ? moneyShort(r.size && r.size.revenue) : MISSING, "hide-sm") + cell(chg(m.price_change_1y_pct)) + "</tr>";
      }).join("") + "</tbody>";
    $("cl-prev").disabled = d.offset === 0;
    $("cl-next").disabled = to >= d.ranked;
  }

  function setSet(v) {
    list.set = v; list.offset = 0;
    Array.prototype.forEach.call($("cl-set").querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-set") === v ? "true" : "false");
    });
    loadList();
  }

  function openList() {
    var u = new URLSearchParams(location.search);
    list.set = u.get("set") === "all" ? "all" : "sp";
    if (u.get("sort") && $("cl-sort").querySelector("option[value='" + u.get("sort").replace(/'/g, "") + "']")) list.sort = u.get("sort");
    list.q = (u.get("q") || "").trim();
    $("cl-sort").value = list.sort;
    $("uc-q").value = list.q;
    $("uc-q").addEventListener("input", function () {
      var q = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { list.q = q; list.offset = 0; loadList(); }, 250);
    });
    $("cl-sort").addEventListener("change", function () { list.sort = this.value; list.offset = 0; loadList(); });
    Array.prototype.forEach.call($("cl-set").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () { setSet(b.getAttribute("data-set")); });
    });
    $("cl-prev").addEventListener("click", function () { list.offset = Math.max(0, list.offset - PAGE); loadList(); window.scrollTo(0, 0); });
    $("cl-next").addEventListener("click", function () { list.offset += PAGE; loadList(); window.scrollTo(0, 0); });
    setSet(list.set);
  }

  // ---- one company: the Overview ------------------------------------------------
  // The header card (co-head.js), then the latest annual figures with their
  // change and last ten years, revenue and net income by year, and beside them
  // valuation, the one-year share price and every item held. The ten years come
  // from the company-facts history, which covers the largest companies; for the
  // rest the figures are the latest annual report's alone.
  var charts = [];

  function money(v) {
    if (v == null) return MISSING;
    var a = Math.abs(v), s = a >= 1e12 ? fmtNum(a / 1e12, 2) + "tn" : a >= 1e9 ? fmtNum(a / 1e9, 1) + "bn" :
      a >= 1e6 ? fmtNum(a / 1e6, 1) + "mn" : fmtNum(a, 0);
    return (v < 0 ? MINUS : "") + "$" + s;
  }
  function pct(v, dp) { return v == null ? MISSING : (v < 0 ? MINUS : "") + fmtNum(Math.abs(v), dp == null ? 1 : dp) + "%"; }

  /* a small line of values, oldest to newest; a missing year is a gap, never zero */
  function spark(vals, w, h) {
    var nums = vals.filter(function (x) { return x != null && isFinite(x); });
    if (nums.length < 2) return "";
    var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums), span = hi - lo || 1;
    var step = (w - 4) / Math.max(vals.length - 1, 1), d = "", pen = false;
    vals.forEach(function (x, i) {
      if (x == null || !isFinite(x)) { pen = false; return; }
      d += (pen ? "L" : "M") + (2 + i * step).toFixed(1) + " " + (2 + (h - 4) * (1 - (x - lo) / span)).toFixed(1) + " ";
      pen = true;
    });
    return "<svg viewBox='0 0 " + w + " " + h + "' width='" + w + "' height='" + h + "' aria-hidden='true'><path d='" + d +
      "' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linejoin='round'/></svg>";
  }

  /* the change from the year before; a percentage across a loss means nothing, so that is said in words */
  function change(cur, prev) {
    if (cur == null || prev == null || prev === 0 || cur === prev) return "";
    var up = cur > prev, words;
    if (prev < 0 && cur >= 0) words = "from a loss";
    else if (prev >= 0 && cur < 0) words = "to a loss";
    else if (prev < 0) words = up ? "loss narrowed" : "loss widened";
    else words = (up ? "+" : MINUS) + fmtNum(Math.abs((cur / prev - 1) * 100), 1) + "%";
    return "<span class='" + (up ? "up" : "down") + "'>" + (up ? "▲ " : "▼ ") + esc(words) + "</span>";
  }

  var FIGS = [["Revenue", "revenue", money], ["Net income", "net_income", money],
              ["Diluted EPS", "eps_diluted", function (v) { return v == null ? MISSING : (v < 0 ? MINUS : "") + "$" + fmtNum(Math.abs(v), 2); }],
              ["Operating cash flow", "cf_operating", money], ["Total assets", "total_assets", money],
              ["Stockholders' equity", "equity", money]];

  function keyFigures(deep, m) {
    var rows = deep ? deep.panel : null;            // newest first
    var cur = rows ? rows[0].values : (m ? Object.assign({ eps_diluted: m.per_share && m.per_share.eps_diluted }, m.size) : null);
    if (!cur) return;
    // without the ten-year history, the latest report's own prior year gives the change (no lines)
    var prev = rows && rows[1] ? rows[1].values : (!rows && m && m.prior ? m.prior : null);
    var last10 = rows ? rows.slice(0, 10).reverse() : [];
    $("kf-tiles").innerHTML = FIGS.map(function (f) {
      return "<div class='cp-tile'><div class='lb'>" + esc(f[0]) + "</div><div class='vl'>" + f[2](cur[f[1]]) + "</div>" +
        "<div class='tr'>" + (prev ? change(cur[f[1]], prev[f[1]]) : "<span></span>") +
        spark(last10.map(function (r) { return r.values[f[1]]; }), 88, 24) + "</div></div>";
    }).join("");
    var src = rows ? (rows[0].sources.revenue || rows[0].sources.net_income) : null;
    var end = rows ? rows[0].period_end : m.period_end;
    $("kf-note").textContent = "Year to " + coHead.dayShort(end) +
      (src ? " · from the " + src.form + " filed " + coHead.dayShort(src.filed) : m && m.filed ? " · from the " + (m.form || "annual report") + " filed " + coHead.dayShort(m.filed) : "");
    $("kf-trust").innerHTML = trustBadge("official");
    $("kf-foot").textContent = rows ? "Change = this year ÷ the year before − 1, calculated here. Lines: the last ten years as filed; gaps are years not filed."
                                    : "As filed in the latest annual report; change = this year ÷ the year before in the same report − 1, calculated here.";
    $("kf").hidden = false;
  }

  function revenueChart(deep, name) {
    var rows = deep.panel.slice(0, 10).reverse();
    if (rows.length < 2) return;
    var cats = rows.map(function (r) { return fmtFiscalYear(r.period_end); });
    var bn = function (k) { return rows.map(function (r) { var v = r.values[k]; return v == null ? null : v / 1e9; }); };
    var src = "Source: U.S. Securities and Exchange Commission, EDGAR (company facts) · " + name + " · values as filed, latest filed";
    $("rn-note").textContent = "$bn · " + cats[0] + " to " + cats[cats.length - 1];
    $("rn-src").textContent = src;
    $("rn").hidden = false;
    var c = obsChart($("rn-chart"), "cols", { categories: cats, unitSuffix: "$bn", dp: 1, trust: "official", sourceLine: src,
      yAxisName: "$bn", gridRight: 28,
      series: [{ name: "Revenue", slot: 1, points: bn("revenue"), barMaxWidth: 36 },
               { name: "Net income", slot: 2, type: "line", points: bn("net_income") }] });
    charts.push(c);
    $("rn-png").onclick = function () { c.exportPNG("plover-" + (deep.company.ticker || "company") + "-revenue.png"); };
    $("rn-csv").onclick = function () { c.exportCSV("plover-" + (deep.company.ticker || "company") + "-revenue.csv", [name + ": revenue and net income, $bn, as filed", src]); };
  }

  function byYear(deep, t) {
    var all = deep.panel, rows = all.slice(0, 5).reverse(), ten = all.slice(0, 10).reverse();
    var lines = [["Revenue", "revenue", 1e6], ["Operating income", "operating_income", 1e6], ["Net income", "net_income", 1e6],
                 ["Operating cash flow", "cf_operating", 1e6], ["Total assets", "total_assets", 1e6], ["Diluted EPS ($)", "eps_diluted", 1]];
    $("by-note").textContent = "$ mn unless noted · latest " + rows.length + " of " + all.length + " years";
    $("by-more").innerHTML = "<a href='us-companies.html?t=" + encodeURIComponent(t) + "'>All " + all.length + " years</a>";
    $("by-table").innerHTML = "<thead><tr><th>Line</th><th style='text-align:left'>10 years</th>" +
      rows.map(function (r) { return "<th>" + esc(fmtFiscalYear(r.period_end)) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      lines.map(function (l) {
        return "<tr><td>" + esc(l[0]) + "</td><td class='sp'>" + spark(ten.map(function (r) { return r.values[l[1]]; }), 72, 18) + "</td>" +
          rows.map(function (r) {
            var v = r.values[l[1]];
            return "<td>" + (v == null ? MISSING : (v < 0 ? MINUS : "") + fmtNum(Math.abs(v) / l[2], l[2] === 1 ? 2 : 0)) + "</td>";
          }).join("") + "</tr>";
      }).join("") + "</tbody>";
    $("by").hidden = false;
  }

  function valuation(m) {
    var mt = m && m.metrics;
    if (!mt) return;
    var defs = {};
    (m.metric_defs || []).forEach(function (d) { defs[d.metric] = d; });
    var rows = [["Market value", money(mt.market_cap), "close × shares outstanding on the latest filing cover"],
                ["P/E", mt.pe_x == null ? MISSING : fmtNum(mt.pe_x, 1) + "×", defs.pe_x && defs.pe_x.formula],
                ["Dividend yield", pct(mt.dividend_yield_pct, 2), defs.dividend_yield_pct && defs.dividend_yield_pct.formula],
                ["Return on equity", pct(mt.roe_pct), defs.roe_pct && defs.roe_pct.formula],
                ["Net margin", pct(mt.net_margin_pct), defs.net_margin_pct && defs.net_margin_pct.formula]];
    $("val-note").textContent = "calculated" + (m.price && m.price.date ? " · close " + coHead.dayShort(m.price.date) : "");
    $("val-dl").innerHTML = rows.map(function (r) { return "<div><dt>" + esc(r[0]) + "</dt><dd>" + r[1] + "</dd></div>"; }).join("");
    var notes = rows.filter(function (r) { return r[2]; }).map(function (r) { return "<li><b>" + esc(r[0]) + ":</b> " + esc(r[2]) + "</li>"; });
    var mvb = m.market_value_basis || {};
    if (mvb.note || mvb.caution) notes.push("<li>" + esc(mvb.note || mvb.caution) + "</li>");
    $("val-formulas").innerHTML = notes.join("");
    $("val-calc").hidden = !notes.length;
    $("val").hidden = false;
  }

  // The year's daily closes, first in the main column: the price is the figure
  // most readers come for. Split-adjusted closes; the full history is the Price tab.
  function priceChart(px, t) {
    var bars = (px && px.bars || []).filter(function (b) { return (b.adjusted_close || b.close) != null; });
    if (bars.length < 2) return;
    var src = "Source: Databento (EQUS.SUMMARY), consolidated end-of-day closes, one-day delay, split-adjusted.";
    $("px-src").textContent = src;
    $("px-more").href = "index.html?t=" + encodeURIComponent(t);
    $("px").hidden = false;
    // the range (1M to Max, the year to date, or from–to) and its change: price-range.js
    charts.push(priceRange.mount($("px-range"), { bars: bars, ticker: t, name: t, source: src }));
  }

  // The next report the SEC calendar expects (estimated from the same filing a
  // year earlier, not an announced date) and the last four filed, each with how
  // many days after its period it came.
  function calendar(cal) {
    if (!cal || !(cal.filings || []).length) return;
    var up = (cal.upcoming || []).filter(function (u) { return u.status === "expected" && u.expected_date; })[0];
    $("cal-body").innerHTML = (up ? "<p class='cal-next'><span>Next</span> <b>" + esc(/annual/i.test(up.report) ? "Annual report" : "Quarterly report") +
      "</b> due about <b>" + esc(coHead.dayShort(up.expected_date)) + "</b></p><p class='cal-why'>Estimated from the same filing a year earlier; not an announced date.</p>" : "") +
      "<table class='cp-table cal-table' data-no-enhance><thead><tr><th>Form</th><th>Period to</th><th>Filed</th><th>Days</th></tr></thead><tbody>" +
      cal.filings.slice(0, 4).map(function (f) {
        return "<tr><td>" + (f.source_url ? "<a href='" + esc(f.source_url) + "' rel='noopener'>" + esc(f.form) + "</a>" : esc(f.form)) + "</td><td>" +
          esc(coHead.dayShort(f.period)) + "</td><td>" + esc(coHead.dayShort(f.filed)) + "</td><td>" + (f.lag_days == null ? MISSING : f.lag_days) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("cal").hidden = false;
  }

  function filesOnRecord(rec) {
    var held = rec.items.filter(function (i) { return i.held; });
    $("items-note").textContent = rec.held + " of " + rec.items.length + " items";
    $("items-list").innerHTML = held.map(function (i) {
      return "<li><a href='" + esc(i.page || i.api) + "'>" + esc(i.label) + "</a><span>" + fmtNum(i.count, 0) + " " +
        esc(i.count === 1 ? i.unit_one : i.unit) + "</span></li>";
    }).join("") || "<li>Nothing held yet for this company.</li>";
  }

  function render(data, deep, px, cal) {
    var rec = data.record, c = rec.company, t = c.ticker || c.cik;
    var shown = c.name || c.sec_name || ("CIK " + c.cik);
    document.title = shown + (c.ticker ? " (" + c.ticker + ")" : "") + " · US Company Records · Plover Analytics";
    document.body.classList.add("co-ground");
    $("co-card").innerHTML = coHead.html(data, "overview");
    coHead.reveal($("co-card"));
    keyFigures(deep, data.metrics);
    if (deep && deep.panel && deep.panel.length) { revenueChart(deep, shown); byYear(deep, t); }
    valuation(data.metrics);
    priceChart(px, c.ticker || t);
    calendar(cal);
    filesOnRecord(rec);
    $("co-grid").hidden = false;
    $("search-view").hidden = true;
    $("company-view").hidden = false;
  }

  function open(t) {
    $("search-view").hidden = true;
    $("company-view").hidden = false;
    $("co-name").textContent = t;
    $("co-meta").textContent = "Loading this company…";
    var quiet = function (p) { return p.catch(function () { return null; }); };
    var since = "2000-01-01";            // every close on file, so any range can be read
    Promise.all([coHead.load(t),
                 quiet(getJSON("/api/v1/us/financials/deep/" + encodeURIComponent(t) + "/indicators?freq=annual&limit=40")),
                 quiet(getJSON("/api/v1/us/prices/" + encodeURIComponent(t) + "?start=" + since)),
                 quiet(getJSON("/api/v1/us/earnings/company/" + encodeURIComponent(t)))]).then(function (r) {
      render(r[0], r[1], r[2], r[3]);
    }).catch(function (e) {
      $("co-meta").innerHTML = (e.status === 404 || e.status === 400
        ? "No US company has the ticker or SEC number “" + esc(t) + "”. "
        : "This company is unavailable (" + esc(e.message) + "). ") +
        "<a href='us-company.html'>Search for the company</a>.";
    });
  }

  window.addEventListener("resize", function () { charts.forEach(function (c) { c.render && c.render(); }); });
  initThemeToggle(function () { if (charts.length) location.reload(); });
  var t = new URLSearchParams(location.search).get("t");
  if (t) open(t.trim());
  else openList();
})();
