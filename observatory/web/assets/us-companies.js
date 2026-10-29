/* US Company Financials: the large US companies held at full filed depth.
   Data: /api/v1/us/financials/deep (app/sec_deep_api.py).

   Two views on one page, like financials.html for Japan: the covered list,
   and one company's record behind ?t=TICKER. Every value is official as
   filed and names the filing it came from; the page recomputes nothing. It
   only divides dollars by a million or a billion for reading, and exports
   carry the exact filed value. Choosing which filed value to show (latest,
   as first reported, known on a date) is selection, done by the API.
   Missing renders as —, never 0. */
(function () {
  "use strict";

  var API = "/api/v1/us/financials/deep";
  var SEC_CREDIT = "Source: U.S. Securities and Exchange Commission, EDGAR";
  var state = { t: null, freq: "annual", basis: "latest", asof: "", chart: "income",
                q: "", tag: "", mode: "one" };
  var head = null;         // the company, from /indicators
  var annual = null;       // annual panel: feeds the headline facts
  var panel = null;        // panel for the current frequency
  var concepts = null;     // /deep/{t} payload
  var showAll = false;
  var series = null;       // /series payload
  var kiChart = null, cnChart = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          var e = new Error(b.detail || (url + " -> " + r.status));
          e.status = r.status;
          throw e;
        });
      }
      return r.json();
    });
  }

  function csvDownload(name, headerLines, cols, rows) {
    var lines = headerLines.map(function (l) { return "# " + l; });
    lines.push(cols.join(","));
    rows.forEach(function (r) {
      lines.push(r.map(function (v) {
        v = v == null ? "" : String(v);
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(","));
    });
    var blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  /* Dollars at a stated scale; the unit is always attached or in the header. */
  function usdBn(v) { return v == null ? MISSING : (v < 0 ? MINUS : "") + "$" + fmtNum(Math.abs(v) / 1e9, 1) + "bn"; }
  function usdMn(v) { return v == null ? MISSING : fmtNum(v / 1e6, 0); }
  function perShare(v) { return v == null ? MISSING : fmtNum(v, 2); }
  function sharesMn(v) { return v == null ? MISSING : fmtNum(v / 1e6, 0); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }

  function secUrl(cik, accn) {
    return "https://www.sec.gov/Archives/edgar/data/" + cik + "/" + String(accn).replace(/-/g, "") + "/";
  }
  function secLink(cik, accn, text) {
    return "<a href='" + esc(secUrl(cik, accn)) + "' target='_blank' rel='noopener'>" +
      esc(text || accn) + "</a>";
  }

  // A US company's year is named by the month it ends, as the Japanese
  // pages do: Apple's year to September 2025 is "FY Sep-2025".
  function periodLabel(iso) {
    if (!iso) return MISSING;
    return state.freq === "annual" ? fmtFiscalYear(iso)
      : MONTHS[Number(iso.slice(5, 7)) - 1].slice(0, 3) + "-" + iso.slice(0, 4);
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<span class='state-error'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected.</span>";
  }

  // ---- freshness -----------------------------------------------------------
  function checkStale() {
    getJSON("/api/v1/catalog/health").then(function (h) {
      var row = (h.equity_extractors || []).filter(function (x) {
        return x.dataset === "sec-companyfacts"; })[0];
      if (!row || !row.stale) return;
      $("stale-banner").innerHTML = "<div class='banner' role='alert'>This surface is stale: " +
        "the last successful pull from the SEC was " +
        (row.last_extracted_at ? fmtStamp(row.last_extracted_at) : "never") +
        ", and it is meant to run at least every " + row.stale_after_days +
        " days. Filings made since then are not shown.</div>";
    }).catch(function () { /* the health check is advisory */ });
  }

  // ---- directory -----------------------------------------------------------
  function tile(label, value, foot) {
    return "<div class='strip-cell'><div class='strip-label'>" + esc(label) + "</div>" +
      "<div class='strip-value num'>" + value + "</div><div class='strip-foot'>" +
      esc(foot) + "</div></div>";
  }

  function renderDirectory(d) {
    var cs = d.companies;
    var facts = 0, filings = 0, latest = null, latestT = "";
    cs.forEach(function (c) {
      facts += c.facts; filings += c.filings;
      c.predecessors.forEach(function (p) { facts += p.facts; filings += p.filings; });
      if (!latest || c.last_filed > latest) { latest = c.last_filed; latestT = c.ticker; }
    });
    var from = cs.map(function (c) { return c.history_from; }).sort()[0];
    $("stat-strip").innerHTML = "<div class='strip-grid cols-4'>" +
      tile("Companies", count(cs.length), "S&P 500 members, in " + d.sectors.length + " GICS sectors") +
      tile("Filed values", count(facts), "across " + count(filings) + " filings") +
      tile("History from", from ? from.slice(0, 4) : MISSING, "first filing held: " + (from || MISSING)) +
      tile("Latest filing", latest || MISSING, latestT ? "filed by " + latestT : "") +
      "</div>";
    $("dir-note").textContent = cs.length + " companies";
    $("dir-table").innerHTML = "<thead><tr><th>Company</th><th>Ticker</th><th>Sector</th>" +
      "<th class=r>History From</th><th class=r>Latest Filing</th><th class=r>Filings</th>" +
      "<th class=r>Filed Values</th></tr></thead><tbody>" +
      cs.map(function (c) {
        var f = c.facts + c.predecessors.reduce(function (a, p) { return a + p.facts; }, 0);
        var n = c.filings + c.predecessors.reduce(function (a, p) { return a + p.filings; }, 0);
        return "<tr><td>" + (c.logo ? "<img class='logo' src='" + esc(c.logo) + "' alt='' width='20' height='20' loading='lazy'>"
                                    : "<span class='logo'></span>") +
          "<a href='us-companies.html?t=" + encodeURIComponent(c.ticker) + "'>" +
          esc(c.name) + "</a></td><td class='mono'>" + esc(c.ticker) + "</td><td>" +
          esc(c.sector) + "</td><td class=r>" + esc(c.history_from) + "</td><td class=r>" +
          esc(c.last_filed) + "</td><td class=r data-sort='" + n + "'>" + count(n) +
          "</td><td class=r data-sort='" + f + "'>" + count(f) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  // ---- company: key indicators ---------------------------------------------
  var ROWS = [
    ["Income statement"],
    ["revenue", "Revenue", "$ mn"], ["operating_income", "Operating income", "$ mn"],
    ["pretax_income", "Pre-tax income", "$ mn"], ["net_income", "Net income", "$ mn"],
    ["Balance sheet"],
    ["total_assets", "Total assets", "$ mn"], ["total_liabilities", "Total liabilities", "$ mn"],
    ["equity", "Stockholders' equity", "$ mn"], ["cash", "Cash and equivalents", "$ mn"],
    ["Cash flow"],
    ["cf_operating", "Operating cash flow", "$ mn"], ["cf_investing", "Investing cash flow", "$ mn"],
    ["cf_financing", "Financing cash flow", "$ mn"], ["capex", "Capital expenditure, paid", "$ mn"],
    ["dividends_paid", "Dividends paid", "$ mn"], ["buybacks", "Share repurchases, paid", "$ mn"],
    ["Per share"],
    ["eps_basic", "EPS, basic", "$"], ["eps_diluted", "EPS, diluted", "$"],
    ["shares_outstanding", "Shares outstanding", "mn"],
  ];
  var FMT = { "$ mn": usdMn, "$": perShare, "mn": sharesMn };
  var LABEL = {};
  ROWS.forEach(function (r) { if (r[1]) LABEL[r[0]] = r[1]; });

  function query(extra) {
    var p = ["basis=" + state.basis];
    if (state.asof) p.push("as_of=" + state.asof);
    return p.concat(extra || []).join("&");
  }

  function syncUrl() {
    var p = new URLSearchParams();
    if (state.t) {
      p.set("t", state.t);
      if (state.freq !== "annual") p.set("freq", state.freq);
      if (state.basis !== "latest") p.set("basis", state.basis);
      if (state.asof) p.set("as_of", state.asof);
      if (state.chart !== "income") p.set("chart", state.chart);
      if (state.tag) p.set("tag", state.tag);
      if (state.mode !== "one") p.set("versions", "all");
    }
    var qs = p.toString();
    history.replaceState(null, "", "us-companies.html" + (qs ? "?" + qs : ""));
  }

  function basisWords() {
    return (state.basis === "first" ? "as first reported" : "latest filed values") +
      (state.asof ? ", as known on " + state.asof : "");
  }

  // The company's header card and tabs (co-head.js), drawn once: the same card
  // as its Overview, with Financials marked. Until it arrives the name shows.
  var carded = false;
  function renderHead() {
    var c = head.company;
    document.title = c.name + " (" + c.ticker + ") · US Company Financials · Plover Analytics";
    document.body.classList.add("co-ground");
    if (carded) return;
    carded = true;
    $("co-name").textContent = c.name;
    coHead.load(c.ticker).then(function (d) { $("co-card").innerHTML = coHead.html(d, "financials"); coHead.reveal($("co-card")); })
      .catch(function () { /* the name stays; the figures below do not depend on the card */ });
  }

  /* A small line of values, oldest to newest; a missing year is a gap, never zero. */
  function spark(vals, w, h) {
    var nums = vals.filter(function (x) { return x != null && isFinite(x); });
    if (nums.length < 2) return "";
    var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums), span = hi - lo || 1;
    var step = (w - 4) / Math.max(vals.length - 1, 1), d = "", pen = false, lx = 0, ly = 0;
    vals.forEach(function (x, i) {
      if (x == null || !isFinite(x)) { pen = false; return; }
      lx = 2 + i * step; ly = 2 + (h - 4) * (1 - (x - lo) / span);
      d += (pen ? "L" : "M") + lx.toFixed(1) + " " + ly.toFixed(1) + " ";
      pen = true;
    });
    return "<svg class='spark' viewBox='0 0 " + w + " " + h + "' width='" + w + "' height='" + h +
      "' aria-hidden='true'><path d='" + d + "' fill='none' stroke='currentColor' stroke-width='1.5' " +
      "stroke-linejoin='round'/><circle cx='" + lx.toFixed(1) + "' cy='" + ly.toFixed(1) + "' r='2' fill='currentColor'/></svg>";
  }

  function orderedRows() {
    // The API serves newest first; the table and chart read oldest to newest.
    return panel ? panel.panel.slice().reverse() : [];
  }

  function renderPanel() {
    var all = orderedRows();
    var q4 = state.freq === "quarterly";
    // the latest ten years (twelve quarters) unless the reader asks for all
    var keep = q4 ? 12 : 10;
    var rows = state.allPeriods ? all : all.slice(-keep);
    $("ki-note").textContent = all.length + (q4 ? " quarters" : " fiscal years") + " · " + basisWords();
    $("ki-more").innerHTML = all.length > keep
      ? "<button type='button' class='btn-text' id='ki-toggle'>" + (state.allPeriods
          ? "Show the latest " + keep + (q4 ? " quarters" : " years")
          : "Show all " + all.length + (q4 ? " quarters" : " years")) + "</button>" : "";
    $("asof-flag").hidden = !state.asof;
    if (state.asof) {
      $("asof-flag").innerHTML = "<b>Known on " + esc(state.asof) + ":</b> only filings filed on or " +
        "before this date are read, so later years are absent and restated years show what was on " +
        "file then.";
    }
    $("ki-explain").innerHTML = "<b>Official as filed.</b> " + (state.basis === "first"
        ? "Each period shows the value from the first filing that reported it, before any later restatement."
        : "Each period shows the value from the most recent filing that carries it, so a restatement replaces the original.") +
      " Hover any value for the XBRL tag and filing behind it. Dollar lines in $ millions; " +
      "capital expenditure, dividends and repurchases are cash paid, filed as positive numbers." +
      (q4 ? " Quarters are three-month periods as filed. Most companies do not file their fourth " +
        "quarter separately (it sits inside the annual report), so it is usually missing — it is " +
        "never derived as the year less three quarters. Cash flow statements are filed " +
        "year-to-date (three, six, nine months), so only the first quarter of each year has a " +
        "three-month cash flow; the others show —." : "");
    if (!rows.length) {
      $("ki-table").innerHTML = "";
      return;
    }
    var headRow = "<thead><tr><th class='lbl'>" + (q4 ? "Quarter ended" : "Fiscal year") + "</th><th class='trend'>Trend</th>" +
      rows.map(function (r) { return "<th class=r>" + esc(periodLabel(r.period_end)) + "</th>"; }).join("") +
      "</tr></thead>";
    var body = ROWS.map(function (def) {
      if (def.length === 1) {
        return "<tr class='heading'><td class='lbl'>" + esc(def[0]) + "</td><td></td>" +
          rows.map(function () { return "<td></td>"; }).join("") + "</tr>";
      }
      var f = def[0], fmt = FMT[def[2]];
      if (!rows.some(function (r) { return r.values[f] != null; })) return "";
      return "<tr><td class='lbl'>" + esc(def[1]) + " <span class='unit'>(" + esc(def[2]) + ")</span></td>" +
        "<td class='trend'>" + spark(rows.map(function (r) { return r.values[f]; }), 64, 18) + "</td>" +
        rows.map(function (r) {
          var s = r.sources[f];
          var tip = s ? s.tag + " · " + s.form + " filed " + s.filed + " · " + s.accn +
            (s.cik !== head.company.cik ? " · CIK " + s.cik : "") : "Not filed for this period";
          return "<td class=r title='" + esc(tip) + "'>" + fmt(r.values[f]) + "</td>";
        }).join("") + "</tr>";
    }).join("");
    // The filing row names where each column's income statement came from.
    // Balances can come from a later report (a 10-Q repeats the year-end
    // balance sheet), so hovering a cell names that cell's own filing.
    var src = "<tr><td class='lbl'>Income statement from</td><td></td>" + rows.map(function (r) {
      var best = r.sources.revenue || r.sources.net_income || r.sources.cf_operating;
      return "<td class='r src'" + (best ? " title='Filed " + esc(best.filed) + " · " +
        esc(best.accn) + "'>" + secLink(best.cik, best.accn, best.form) : ">" + MISSING) + "</td>";
    }).join("") + "</tr>";
    $("ki-table").innerHTML = headRow + "<tbody>" + body + src + "</tbody>";
    // Newest periods sit at the right edge; start the reader there.
    var wrap = $("ki-wrap");
    wrap.scrollLeft = wrap.scrollWidth;
    $("ki-calc").innerHTML = "<b>Nothing is recomputed.</b> " + esc(panel.calc.basis) + " " +
      esc(panel.calc.as_of) + " " + esc(panel.calc.periods) + " " + esc(panel.calc.indicators) +
      " Tags tried, in order: " + panel.fields.map(function (f) {
        return "<b>" + esc(LABEL[f.id] || f.id) + "</b> " + f.tags.map(function (t) {
          return "<code>" + esc(t) + "</code>"; }).join(", ");
      }).join("; ") + ".";
    drawChart();
  }

  var CHARTS = {
    income: { kind: "cols", fields: [["revenue", "Revenue"], ["net_income", "Net income"]], scale: 1e9, unit: "$bn", dp: 1 },
    cf: { kind: "line", fields: [["cf_operating", "Operating"], ["cf_investing", "Investing"],
                                 ["cf_financing", "Financing"]], scale: 1e9, unit: "$bn", dp: 1 },
    returns: { kind: "cols", fields: [["dividends_paid", "Dividends paid"], ["buybacks", "Share repurchases"]],
               scale: 1e9, unit: "$bn", dp: 1 },
    eps: { kind: "line", fields: [["eps_diluted", "Diluted EPS"]], scale: 1, unit: "$", dp: 2 },
  };

  function chartSource() {
    return SEC_CREDIT + " · " + head.company.name + " (" + head.company.ticker + ") · " +
      basisWords() + " · Official, as filed";
  }

  function drawChart() {
    var rows = orderedRows();
    var spec = CHARTS[state.chart];
    var el = $("ki-chart");
    if (kiChart) { kiChart.dispose(); kiChart = null; }
    if (!rows.length) { el.innerHTML = ""; return; }
    var cats = rows.map(function (r) { return periodLabel(r.period_end); });
    var anyNeg = false;
    var ser = spec.fields.map(function (f, i) {
      var pts = rows.map(function (r) {
        var v = r.values[f[0]];
        if (v != null && v < 0) anyNeg = true;
        return v == null ? null : v / spec.scale;
      });
      return { name: f[1], slot: i + 1, points: pts };
    });
    if (!ser.some(function (s) { return s.points.some(function (v) { return v != null; }); })) {
      el.innerHTML = "<p class='state-empty'>" + esc(head.company.name) + " has not filed " +
        esc(spec.fields.map(function (f) { return f[1].toLowerCase(); }).join(" or ")) +
        " under the tags this panel reads. Search the concepts below for the line it uses.</p>";
      $("ki-source").textContent = "";
      return;
    }
    var source = chartSource();
    // A column chart keeps a zero baseline, so a loss or an outflow draws as a line.
    if (spec.kind === "cols" && !anyNeg) {
      kiChart = obsChart(el, "cols", { categories: cats, series: ser, unitSuffix: spec.unit, dp: spec.dp,
        labelInterval: "auto", gridRight: 30,
        yAxisName: spec.unit, trust: "official", sourceLine: source });
    } else {
      kiChart = obsChart(el, "line", { xType: "category", unit: "", unitSuffix: spec.unit, dp: spec.dp,
        gridRight: 36,
        yAxisName: spec.unit, trust: "official", sourceLine: source,
        series: ser.map(function (s) {
          return { name: s.name, slot: s.slot, points: s.points.map(function (v, i) { return [cats[i], v]; }) };
        }) });
    }
    $("ki-source").textContent = source;
  }

  function panelCsv() {
    var rows = orderedRows();
    if (!rows.length) return;
    var fields = panel.fields.map(function (f) { return f.id; });
    var c = head.company;
    csvDownload("us-" + c.ticker + "-key-indicators-" + state.freq + "-" + state.basis +
      (state.asof ? "-asof-" + state.asof : "") + ".csv", [
      "Plover Analytics — US key indicators as filed: " + c.name + " (" + c.ticker + "), CIK " + c.cik,
      "frequency: " + state.freq + " · basis: " + state.basis + (state.asof ? " · as_of: " + state.asof : "") +
        " · trust: official (as filed) · " + SEC_CREDIT,
      "values exact as filed: USD for money, USD/shares for EPS, shares for shares outstanding",
      "each <field>_tag / _accn / _filed column names the XBRL tag and the filing the value came from",
      panel.calc.basis,
      panel.calc.periods,
    ], ["period_start", "period_end"].concat(fields.reduce(function (a, f) {
        return a.concat([f, f + "_tag", f + "_accn", f + "_filed"]); }, [])),
      rows.map(function (r) {
        return [r.period_start, r.period_end].concat(fields.reduce(function (a, f) {
          var s = r.sources[f];
          return a.concat([r.values[f], s && s.tag, s && s.accn, s && s.filed]);
        }, []));
      }));
  }

  function loadPanels() {
    syncUrl();
    var want = [getJSON(API + "/" + encodeURIComponent(state.t) + "/indicators?freq=annual&limit=40&" + query())];
    if (state.freq === "quarterly") {
      want.push(getJSON(API + "/" + encodeURIComponent(state.t) + "/indicators?freq=quarterly&limit=120&" + query()));
    }
    return Promise.all(want).then(function (res) {
      annual = res[0]; panel = res[1] || res[0]; head = annual;
      renderHead(); $("co-msg").textContent = ""; renderPanel();
    }).catch(function (e) {
      if (e.status === 404 && !head && !/deep-coverage universe/.test(e.message)) {
        // A covered company with nothing filed by the chosen date: name it
        // from its concept menu, then fall through to the empty state.
        return getJSON(API + "/" + encodeURIComponent(state.t) + "?limit=1").then(function (d) {
          head = { company: d.company, registrants: d.registrants };
          renderHead();
          throw e;
        }).catch(function () { return noPeriods(); });
      }
      if (e.status === 404 && head) return noPeriods();
      if (!head) {
        $("co-name").textContent = state.t;
        $("co-msg").textContent = e.status === 404
          ? "This ticker is not among the covered US companies. Go back to the list to choose one."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
        return;
      }
      errorInto("ki-explain", e);
    });
  }

  // A known company with nothing filed by the chosen date.
  function noPeriods() {
    annual = panel = null;
    $("co-msg").textContent = "Nothing was filed by the chosen date. Clear the date to see every year.";
    $("ki-table").innerHTML = "";
    $("ki-calc").innerHTML = "";
    if (kiChart) { kiChart.dispose(); kiChart = null; }
    $("ki-chart").innerHTML = "<p class='state-empty'>No " + esc(state.freq) + " periods had been " +
      "filed by " + esc(state.asof || "then") + ". Clear the date or choose a later one.</p>";
    $("ki-source").textContent = "";
    $("ki-note").textContent = "";
  }

  // ---- company: every filed concept ----------------------------------------
  var CN_SHOW = 8;           // the most-filed first; search or "Show all" for the rest
  var cnReq = 0;
  function loadConcepts() {
    var mine = ++cnReq;
    getJSON(API + "/" + encodeURIComponent(state.t) + "?q=" + encodeURIComponent(state.q)).then(function (d) {
      if (mine !== cnReq) return;
      concepts = d; renderConcepts();
    }).catch(function (e) { if (mine === cnReq) errorInto("cn-more", e); });
  }

  function fmtUnits(units) {
    return units.map(function (u) { return u === "USD/shares" ? "$ per share" : u; }).join(", ");
  }

  function renderConcepts() {
    var list = concepts.concepts;
    $("cn-note").textContent = count(concepts.count) + (state.q ? " matching" : " concepts");
    var shown = showAll ? list : list.slice(0, CN_SHOW);
    $("cn-table").innerHTML = "<thead><tr><th>Concept</th><th>Unit</th><th class=r>Years</th>" +
      "<th class=r>Quarters</th><th class=r>First Period</th><th class=r>Last Period</th></tr></thead><tbody>" +
      (shown.length ? shown.map(function (c) {
        var key = c.taxonomy + ":" + c.tag;
        return "<tr class='pick' tabindex='0' data-tag='" + esc(key) + "' aria-selected='" +
          (key === state.tag ? "true" : "false") + "'><td class='lbl'>" + esc(c.label || c.tag) + "</td><td>" + esc(fmtUnits(c.units)) +
          "</td><td class=r>" + count(c.annual_periods) + "</td><td class=r>" + count(c.quarterly_periods) +
          "</td><td class=r>" + esc(c.first_period) + "</td><td class=r>" + esc(c.last_period) + "</td></tr>";
      }).join("") : "<tr><td colspan='6' class='state-empty'>No concept matches that.</td></tr>") + "</tbody>";
    $("cn-more").innerHTML = list.length > CN_SHOW
      ? "<button type='button' class='btn-text' id='cn-toggle'>" +
        (showAll ? "Show the first " + CN_SHOW : "Show all " + count(list.length)) + "</button>" : "";
  }

  function splitTag(key) {
    var i = key.indexOf(":");
    return i < 0 ? ["us-gaap", key] : [key.slice(0, i), key.slice(i + 1)];
  }

  function loadSeries() {
    if (!state.tag) { $("cn-detail").hidden = true; return; }
    syncUrl();
    var parts = splitTag(state.tag);
    var basis = state.mode === "all" ? "all" : state.basis;
    var url = API + "/" + encodeURIComponent(state.t) + "/series?taxonomy=" + encodeURIComponent(parts[0]) +
      "&tag=" + encodeURIComponent(parts[1]) + "&freq=" + state.freq + "&basis=" + basis +
      (state.asof ? "&as_of=" + state.asof : "");
    $("cn-detail").hidden = false;
    getJSON(url).then(function (d) { series = d; renderSeries(); }).catch(function (e) {
      series = null;
      $("cn-title").textContent = parts[1];
      if (cnChart) { cnChart.dispose(); cnChart = null; }
      $("cn-chart").innerHTML = "";
      $("cn-values").innerHTML = "";
      $("cn-def").innerHTML = e.status === 404
        ? "<span class='state-empty'>No " + esc(state.freq) + " values of this concept" +
          (state.asof ? " filed by " + esc(state.asof) : "") + ". Try the other frequency.</span>"
        : "<span class='state-error'>Data unavailable — " + esc(e.message) + "</span>";
    });
  }

  /* Scale a concept's values for reading: dollars in $bn or $ mn by size,
     anything else as filed. The table always shows the exact value. */
  function seriesScale(d) {
    if (d.unit !== "USD") return { f: 1, unit: d.unit === "USD/shares" ? "$" : d.unit, dp: 2 };
    var max = 0;
    d.values.forEach(function (v) { if (v.value != null) max = Math.max(max, Math.abs(v.value)); });
    if (max >= 1e9) return { f: 1e9, unit: "$bn", dp: 2 };
    if (max >= 1e6) return { f: 1e6, unit: "$ mn", dp: 1 };
    return { f: 1, unit: "$", dp: 0 };
  }

  function renderSeries() {
    var d = series;
    var all = d.basis === "all";
    $("cn-title").innerHTML = esc(d.label || d.tag) + "<span class='mono'>" + esc(d.taxonomy + ":" + d.tag) + "</span>";
    $("cn-def").innerHTML = (d.definition ? esc(d.definition) + " " : "") + "<b>" + count(d.count) +
      (all ? " filed versions" : " periods") + "</b> in " + esc(d.unit) + ", " +
      (all ? "every filing's value for every period" : basisWords()) +
      (d.units_available.length > 1 ? "; also filed in " + esc(d.units_available.filter(function (u) {
        return u !== d.unit; }).join(", ")) : "") + ". " + trustBadge("official");
    // Chart: one value per period, oldest to newest. In the every-version
    // view it plots the latest filed value per period.
    if (cnChart) { cnChart.dispose(); cnChart = null; }
    var per = {};
    d.values.forEach(function (v) {
      var k = v.period_end + "|" + (v.period_start || "");
      if (!per[k] || v.filed > per[k].filed) per[k] = v;
    });
    var pts = Object.keys(per).map(function (k) { return per[k]; })
      .sort(function (a, b) { return a.period_end < b.period_end ? -1 : 1; });
    var sc = seriesScale(d);
    var src = SEC_CREDIT + " · " + head.company.name + " · " + d.taxonomy + ":" + d.tag + " · Official, as filed";
    if (pts.length) {
      var cats = pts.map(function (v) {
        return v.period_start ? periodLabel(v.period_end) : v.period_end; });
      cnChart = obsChart($("cn-chart"), "line", { xType: "category", unit: "", unitSuffix: sc.unit, dp: sc.dp,
        gridRight: 36,
        yAxisName: sc.unit, trust: "official", sourceLine: src,
        series: [{ name: d.label || d.tag, slot: 1, points: pts.map(function (v, i) {
          return [cats[i], v.value == null ? null : v.value / sc.f]; }) }] });
      $("cn-source").textContent = src;
    } else {
      $("cn-chart").innerHTML = "";
      $("cn-source").textContent = "";
    }
    var money = d.unit === "USD" || d.unit === "USD/shares";
    var dp = d.unit === "USD/shares" ? 2 : 0;
    $("cn-values").innerHTML = "<thead><tr><th>Period Start</th><th>Period End</th><th class=r>Value (" +
      esc(d.unit) + ")</th><th>Form</th><th>Fiscal Period</th><th class=r>Filed</th><th>Filing</th></tr></thead><tbody>" +
      d.values.map(function (v, i) {
        var prev = d.values[i - 1];
        var repeat = all && prev && prev.period_end === v.period_end && prev.period_start === v.period_start;
        var val = v.value == null ? MISSING : (money || Math.abs(v.value) >= 1 ? fmtNum(v.value, v.value % 1 ? Math.max(dp, 2) : dp) : String(v.value));
        return "<tr" + (repeat ? " class='rev'" : "") + "><td>" + esc(v.period_start || "balance on") +
          "</td><td>" + esc(v.period_end) + "</td><td class=r>" + val + "</td><td>" + esc(v.form) +
          "</td><td>" + esc((v.fy || "") + " " + (v.fp || "")) + "</td><td class=r>" + esc(v.filed) +
          "</td><td>" + secLink(v.cik, v.accn) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  function seriesCsv() {
    if (!series) return;
    var d = series, c = head.company;
    csvDownload("us-" + c.ticker + "-" + d.tag + "-" + d.freq + "-" + d.basis +
      (d.as_of ? "-asof-" + d.as_of : "") + ".csv", [
      "Plover Analytics — " + (d.label || d.tag) + " (" + d.taxonomy + ":" + d.tag + ") as filed: " +
        c.name + " (" + c.ticker + "), CIK " + c.cik,
      "unit: " + d.unit + " · frequency: " + d.freq + " · basis: " + d.basis +
        (d.as_of ? " · as_of: " + d.as_of : "") + " · trust: official (as filed) · " + SEC_CREDIT,
      d.definition ? "definition: " + d.definition : "definition: not in the SEC taxonomy file",
      "value exact as filed; accn = SEC accession number; source_url = the filing's folder on EDGAR",
    ], ["period_start", "period_end", "value", "unit", "form", "fy", "fp", "filed", "accn", "cik", "source_url"],
      d.values.map(function (v) {
        return [v.period_start, v.period_end, v.value, v.unit, v.form, v.fy, v.fp, v.filed, v.accn, v.cik, v.source_url];
      }));
  }

  // ---- wiring --------------------------------------------------------------
  function setSeg(id, attr, value) {
    Array.prototype.forEach.call($(id).querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute(attr) === value ? "true" : "false");
    });
  }

  function reloadAll() { loadPanels(); if (state.tag) loadSeries(); }

  function initCompany(t) {
    var p = new URLSearchParams(location.search);
    state.t = t;
    if (p.get("freq") === "quarterly") state.freq = "quarterly";
    if (p.get("basis") === "first") state.basis = "first";
    if (/^\d{4}-\d{2}-\d{2}$/.test(p.get("as_of") || "")) state.asof = p.get("as_of");
    if (CHARTS[p.get("chart")]) state.chart = p.get("chart");
    if (p.get("tag")) state.tag = p.get("tag");
    if (p.get("versions") === "all") state.mode = "all";
    setSeg("freq-seg", "data-freq", state.freq);
    setSeg("basis-seg", "data-basis", state.basis);
    setSeg("cn-mode", "data-mode", state.mode);
    $("chart-select").value = state.chart;
    $("asof").value = state.asof;
    $("asof").max = new Date().toISOString().slice(0, 10);
    $("asof-clear").hidden = !state.asof;

    $("freq-seg").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      state.freq = b.getAttribute("data-freq"); setSeg("freq-seg", "data-freq", state.freq);
      reloadAll();
    });
    $("basis-seg").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      state.basis = b.getAttribute("data-basis"); setSeg("basis-seg", "data-basis", state.basis);
      reloadAll();
    });
    $("asof").addEventListener("change", function () {
      var v = this.value;
      if (v && !/^\d{4}-\d{2}-\d{2}$/.test(v)) return;
      state.asof = v; $("asof-clear").hidden = !v;
      reloadAll();
    });
    $("asof-clear").addEventListener("click", function () {
      state.asof = ""; $("asof").value = ""; this.hidden = true;
      reloadAll();
    });
    $("chart-select").addEventListener("change", function () {
      state.chart = this.value; syncUrl(); drawChart();
    });
    $("ki-csv").addEventListener("click", panelCsv);
    $("ki-png").addEventListener("click", function () {
      if (kiChart) kiChart.exportPNG("us-" + state.t + "-" + state.chart + "-" + state.freq + ".png");
    });

    var timer = null;
    $("cn-q").addEventListener("input", function () {
      var q = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { state.q = q; showAll = false; loadConcepts(); }, 200);
    });
    $("ki-more").addEventListener("click", function (e) {
      if (!e.target.closest("#ki-toggle")) return;
      state.allPeriods = !state.allPeriods; renderPanel();
    });
    $("cn-more").addEventListener("click", function (e) {
      if (e.target.id !== "cn-toggle") return;
      showAll = !showAll; renderConcepts();
    });
    function pick(tr) {
      state.tag = tr.getAttribute("data-tag");
      Array.prototype.forEach.call($("cn-table").querySelectorAll("tr.pick"), function (r) {
        r.setAttribute("aria-selected", r === tr ? "true" : "false");
      });
      loadSeries();
      $("cn-detail").scrollIntoView({ behavior: "smooth", block: "start" });
    }
    $("cn-table").addEventListener("click", function (e) {
      var tr = e.target.closest("tr.pick"); if (tr) pick(tr);
    });
    $("cn-table").addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var tr = e.target.closest("tr.pick"); if (tr) { e.preventDefault(); pick(tr); }
    });
    $("cn-mode").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      state.mode = b.getAttribute("data-mode"); setSeg("cn-mode", "data-mode", state.mode);
      loadSeries();
    });
    $("cn-csv").addEventListener("click", seriesCsv);
    $("cn-png").addEventListener("click", function () {
      if (cnChart && series) cnChart.exportPNG("us-" + state.t + "-" + series.tag + ".png");
    });

    loadPanels().then(function () {
      if (!head) return;
      loadConcepts();
      if (state.tag) loadSeries();
    });
  }

  // ---- boot ----------------------------------------------------------------
  checkStale();
  initThemeToggle(function () {
    if (panel) drawChart();
    if (series) renderSeries();
  });
  var t = new URLSearchParams(location.search).get("t");
  if (t) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    initCompany(t.trim().toUpperCase());
  } else {
    getJSON(API).then(renderDirectory).catch(function (e) { errorInto("dir-note", e); });
  }
})();
