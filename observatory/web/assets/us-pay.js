/* US Executive Pay: the proxy's pay-versus-performance table, S&P 500 wide.
   Data: /api/v1/us/pay (app/us_pay_api.py).

   Two views on one page, like us-companies.html: the index, and one
   company's years behind ?t=TICKER. Every value is official as filed; the
   page only divides dollars by a million for reading, and exports carry the
   exact filed value. The list shows the CEO in office at the year end with
   that person's own pay; co-CEOs get a line each; nothing is summed.
   Missing renders as —, never 0. */
(function () {
  "use strict";

  var API = "/api/v1/us/pay";
  var CREDIT = "Source: U.S. Securities and Exchange Commission, EDGAR (DEF 14A, Item 402(v))";
  var state = { t: null, basis: "latest", chart: "pay" };
  var index = null, co = null, chart = null;

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

  /* $ millions, one decimal; the unit sits in the column header. */
  function mn(v) { return v == null ? MISSING : fmtNum(v / 1e6, 1); }
  function usdMn(v) { return v == null ? MISSING : (v < 0 ? MINUS : "") + "$" + fmtNum(Math.abs(v) / 1e6, 1) + "m"; }
  function tsr(v) { return v == null ? MISSING : fmtNum(v, 2); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function fy(iso) { return iso ? fmtFiscalYear(iso) : MISSING; }

  function secLink(url, text) {
    return "<a href='" + esc(url) + "' target='_blank' rel='noopener'>" + esc(text) + "</a>";
  }

  function checkStale() {
    getJSON("/api/v1/catalog/health").then(function (h) {
      var row = (h.equity_extractors || []).filter(function (x) {
        return x.dataset === "sec-proxy-pay"; })[0];
      if (!row || !row.stale) return;
      $("stale-banner").innerHTML = "<div class='banner' role='alert'>This surface is stale: " +
        "the last check of the SEC for new proxy statements was " +
        (row.last_extracted_at ? fmtStamp(row.last_extracted_at) : "never") +
        "; it is meant to run every day. Proxies filed since then are not shown.</div>";
    }).catch(function () { /* advisory */ });
  }

  function setSeg(id, value) {
    Array.prototype.forEach.call($(id).querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-basis") === value ? "true" : "false");
    });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    if (state.t) p.set("t", state.t);
    if (state.basis !== "latest") p.set("basis", state.basis);
    if (state.t && state.chart !== "pay") p.set("chart", state.chart);
    var qs = p.toString();
    history.replaceState(null, "", "us-pay.html" + (qs ? "?" + qs : ""));
  }

  function basisWords() {
    return state.basis === "first" ? "as first reported" : "latest filed values";
  }

  // ---- index -------------------------------------------------------------------
  var STATUS = { no_pay_table: "No pay table filed", not_checked: "Not checked yet",
                 failed: "Last check failed" };

  function tile(label, value, foot) {
    return "<div class='strip-cell'><div class='strip-label'>" + esc(label) + "</div>" +
      "<div class='strip-value num'>" + value + "</div><div class='strip-foot'>" +
      esc(foot) + "</div></div>";
  }

  function logo(c) {
    return c.logo ? "<img class='co-logo' src='" + esc(c.logo) + "' alt='' width='20' height='20' loading='lazy'>"
                  : "<span class='co-logo'></span>";
  }

  function ceoCell(c, name) {
    if (c.status !== "ok") return "<td class='muted'>" + esc(STATUS[c.status] || c.status) + "</td>";
    return "<td class='ceo'>" + esc(name || MISSING) + "</td>";
  }

  /* One line per CEO in office at the year end: one for nearly every
     company, one each for co-CEOs, each with that person's own figures. */
  function lines(c) {
    if (c.ceos && c.ceos.length > 1) {
      return c.ceos.map(function (p) {
        return { name: p.name, total: p.total_pay, paid: p.actually_paid };
      });
    }
    var v = c.values || {};
    return [{ name: c.ceo_names.join(" and "), total: v.ceo_total_pay, paid: v.ceo_actually_paid }];
  }

  function renderIndex(d) {
    index = d;
    var ok = d.companies.filter(function (c) { return c.status === "ok"; });
    var top = [];
    ok.forEach(function (c) {
      lines(c).forEach(function (l) { if (l.total != null) top.push({ c: c, total: l.total }); });
    });
    top = top.sort(function (a, b) { return b.total - a.total; })[0];
    var latest = ok.map(function (c) { return c.source && c.source.filed; }).filter(Boolean).sort().pop();
    $("stat-strip").innerHTML = "<div class='strip-grid cols-4'>" +
      tile("Companies", count(d.count), "S&P 500 members held by SPY on " + d.index.as_of) +
      tile("With a pay table", count(d.coverage.with_pay),
           count(d.coverage.no_pay_table) + " without · " + count(d.coverage.not_checked) + " not checked yet") +
      tile("Highest CEO pay", top ? usdMn(top.total) : MISSING,
           top ? top.c.ticker + ", " + fy(top.c.fiscal_year_end) : "") +
      tile("Latest proxy", latest || MISSING, "filed with the SEC") +
      "</div>";
    $("ix-note").textContent = count(d.count) + " companies · latest fiscal year each";
    $("ix-explain").innerHTML = "<b>Official as filed</b>, " + esc(basisWords()) + ". CEO pay is the Summary Compensation Table total; <b>actually paid</b> is the " +
      "SEC's measure that marks stock awards to market, so it can be far higher or below zero. " +
      "Return is what $100 invested at the start of the company's own table was worth at the " +
      "year end (hover for the start date; it differs by company). Each line is the CEO in office " +
      "at the fiscal year end, with that person's own pay; a CEO who left during the year is on the " +
      "company's page. Co-CEOs get a line each, never added together.";
    $("ix-table").innerHTML = "<thead><tr><th>Company</th><th>Ticker</th><th>Fiscal Year</th>" +
      "<th>CEO</th><th class=r>CEO Pay <span class='u'>$m</span></th>" +
      "<th class=r>CEO Actually Paid <span class='u'>$m</span></th>" +
      "<th class=r>Other Execs, Avg <span class='u'>$m</span></th>" +
      "<th class=r>Return <span class='u'>$</span></th>" +
      "<th class=r>Peer Return <span class='u'>$</span></th></tr></thead><tbody>" +
      d.companies.map(function (c) {
        var v = c.values || {};
        function num(x, f, tip) {
          return "<td class=r data-sort='" + (x == null ? "" : x) + "'" +
            (tip && x != null ? " title='" + esc(tip) + "'" : "") + ">" + f(x) + "</td>";
        }
        var base = c.tsr_base ? "Value of $100 invested on " + c.tsr_base : null;
        return lines(c).map(function (l) {
          return "<tr><td>" + logo(c) +
            "<a href='us-pay.html?t=" + encodeURIComponent(c.ticker) + "'>" +
            esc(c.name) + "</a></td><td class='mono'>" + esc(c.ticker) + "</td><td data-sort='" +
            esc(c.fiscal_year_end || "") + "'>" + esc(c.fiscal_year_end ? fy(c.fiscal_year_end) : MISSING) +
            "</td>" + ceoCell(c, l.name) + num(l.total, mn) + num(l.paid, mn) +
            num(v.neo_avg_total_pay, mn) + num(v.tsr, tsr, base) + num(v.peer_tsr, tsr, base) + "</tr>";
        }).join("");
      }).join("") + "</tbody>";
    $("ix-calc").innerHTML = "<b>Nothing is recomputed.</b> " + esc(d.calc.total_pay) + " " +
      esc(d.calc.actually_paid) + " " + esc(d.calc.tsr) + " " + esc(d.calc.basis) + " " +
      esc(d.calc.ceos) + " " + esc(d.calc.names) + " Members: " + esc(d.index.index_name) + ", holdings as of " +
      esc(d.index.as_of) + (d.index.unmatched ? "; holdings not matched to an SEC filer: " +
        esc(d.index.unmatched) : "") + ".";
  }

  function indexCsv() {
    if (!index) return;
    csvDownload("us-executive-pay-" + state.basis + ".csv", [
      "Plover Analytics — US executive pay (pay versus performance), S&P 500 members",
      "members: " + index.index.index_name + ", holdings as of " + index.index.as_of,
      "basis: " + state.basis + " · trust: official (as filed) · " + CREDIT,
      "money in USD exactly as filed; tsr and peer_tsr = value of $100 invested",
      "one row per CEO in office at the fiscal year end (co-CEOs: one row each, never summed); ceo_left_in_year = CEOs who left during that year, on the company page",
    ], ["ticker", "cik", "name", "status", "fiscal_year_end", "ceo_name", "ceo_left_in_year",
        "ceo_total_pay_usd", "ceo_actually_paid_usd", "neo_avg_total_pay_usd",
        "neo_avg_actually_paid_usd", "tsr", "peer_tsr", "net_income_usd", "company_measure",
        "proxy_accn", "proxy_filed"],
      index.companies.reduce(function (out, c) {
        var v = c.values || {};
        lines(c).forEach(function (l) {
          out.push([c.ticker, c.cik, c.name, c.status, c.fiscal_year_end, l.name,
                    (c.left_in_year || []).join("; "), l.total, l.paid, v.neo_avg_total_pay,
                    v.neo_avg_actually_paid, v.tsr, v.peer_tsr, v.net_income, c.measure_name,
                    c.source && c.source.accn, c.source && c.source.filed]);
        });
        return out;
      }, []));
  }

  function loadIndex() {
    syncUrl();
    getJSON(API + "?basis=" + state.basis).then(renderIndex).catch(function (e) {
      $("ix-explain").innerHTML = "<span class='state-error'>Data unavailable — " + esc(e.message) +
        ". The last good state is unaffected.</span>";
    });
  }

  // ---- one company ---------------------------------------------------------------
  function years() {
    // The API serves newest first; the table and chart read oldest to newest.
    return co ? co.years.slice().reverse() : [];
  }

  function people() {
    // Everyone tagged individually as CEO in any year, in order of first year.
    var seen = [], out = [];
    years().forEach(function (y) {
      y.ceos.forEach(function (c) {
        if (seen.indexOf(c.member) < 0) { seen.push(c.member); out.push(c); }
      });
    });
    return out;
  }

  function renderCompany() {
    var c = co.company, ys = years();
    document.title = c.name + " (" + c.ticker + ") · US Executive Pay · Plover Analytics";
    $("co-name").textContent = c.name;
    $("co-logo").hidden = !c.logo;
    if (c.logo) $("co-logo").src = c.logo;
    $("co-code").textContent = c.ticker;
    var read = co.filings.filter(function (f) { return f.status === "ok"; }).length;
    $("co-meta").innerHTML = "CIK <span class='mono'>" + c.cik + "</span> · " +
      (c.weight_pct == null ? "" : fmtNum(c.weight_pct, 2) + "% of SPY on " + esc(co.index.as_of) + " · ") +
      count(read) + (read === 1 ? " proxy statement" : " proxy statements") + " read since 2023 · " +
      "<a href='us-company.html?t=" + encodeURIComponent(c.ticker) + "'>Everything held for this company</a>";
    var last = co.years[0];
    if (!last) {
      $("co-facts").innerHTML = "";
      $("co-facts-foot").textContent = "No pay-versus-performance table has been read for this company.";
    } else {
      // The CEO in office at the year end, with that person's own figures.
      var atEnd = last.ceos.filter(function (p) { return p.at_year_end; });
      var single = atEnd.length === 1 ? atEnd[0] : null;
      var total = single && last.ceos.length > 1 ? single.total_pay :
        (last.values.ceo_total_pay != null ? last.values.ceo_total_pay : single && single.total_pay);
      var paid = single && last.ceos.length > 1 ? single.actually_paid :
        (last.values.ceo_actually_paid != null ? last.values.ceo_actually_paid : single && single.actually_paid);
      var multi = atEnd.length > 1;
      var q = fy(last.fiscal_year_end);
      var fact = function (label, value) {
        return "<div><dt>" + esc(label) + "</dt><dd>" + value + "<span class='qual'>" + esc(q) +
          "</span></dd></div>";
      };
      $("co-facts").innerHTML =
        (multi ? "" : fact("CEO total pay", usdMn(total)) + fact("CEO actually paid", usdMn(paid))) +
        fact("Other executives, average", usdMn(last.values.neo_avg_total_pay)) +
        fact("Shareholder return", last.values.tsr == null ? MISSING : "$" + tsr(last.values.tsr)) +
        fact("Peer return", last.values.peer_tsr == null ? MISSING : "$" + tsr(last.values.peer_tsr));
      var src = last.sources.ceo_total_pay || last.sources.tsr ||
        (single && single.sources && (single.sources.total_pay || single.sources.actually_paid));
      $("co-facts-foot").innerHTML = trustBadge("official") + " " +
        esc((last.year_end_ceos || last.ceo_names).join(" and ") || "CEO not named") +
        (multi ? ", co-CEOs at the year end; each is in the table" : ", CEO at the year end") +
        " · " + esc(basisWords()) +
        (src ? " · from the proxy filed " + esc(src.filed) + ", " + secLink(src.url ||
          ("https://www.sec.gov/Archives/edgar/data/" + c.cik + "/" + src.accn.replace(/-/g, "") + "/"), src.accn) : "") + ".";
    }
    renderTable(ys);
    renderFilings();
    drawChart();
  }

  function renderTable(ys) {
    $("pv-note").textContent = ys.length + (ys.length === 1 ? " fiscal year · " : " fiscal years · ") + basisWords();
    $("pv-explain").innerHTML = "<b>Official as filed.</b> Each proxy repeats up to five years of " +
      "its table; each value here comes from the " + (state.basis === "first" ? "first proxy that " +
      "reported it." : "latest proxy that reports it, so a revision replaces the original.") +
      " Where a year had more than one CEO, each is listed by name and none is added together." +
      " Shareholder return is cumulative from a start date that moves forward with every proxy, " +
      "so all its years come from the latest proxy only; earlier years it does not cover stay empty." +
      (co.years.some(function (y) { return y.measure_name; }) ? " The company's own chosen measure: <b>" +
        esc(co.years.filter(function (y) { return y.measure_name; })[0].measure_name) + "</b>." : "");
    if (!ys.length) { $("pv-table").innerHTML = ""; return; }
    var ppl = people();
    var head = "<thead><tr><th>Fiscal Year</th>" + ys.map(function (y) {
      return "<th class=r>" + esc(fy(y.fiscal_year_end)) + "</th>"; }).join("") + "</tr></thead>";
    function row(label, cells, cls) {
      return "<tr><td class='lbl" + (cls ? " " + cls : "") + "'>" + label + "</td>" + cells + "</tr>";
    }
    function vals(f, fmt) {
      return ys.map(function (y) { return "<td class=r>" + fmt(y.values[f]) + "</td>"; }).join("");
    }
    function personVals(p, key) {
      return ys.map(function (y) {
        var hit = y.ceos.filter(function (c) { return c.member === p.member; })[0];
        return "<td class=r>" + mn(hit ? hit[key] : null) + "</td>";
      }).join("");
    }
    var blank = ys.map(function () { return "<td></td>"; }).join("");
    var body = row("CEO", ys.map(function (y) {
      return "<td class=r>" + esc(y.ceo_names.join(" / ") || MISSING) + "</td>"; }).join(""));
    body += "<tr class='heading'><td class='lbl'>CEO pay <span class='unit'>($m)</span></td>" + blank + "</tr>";
    body += row("Total pay", vals("ceo_total_pay", mn));
    ppl.forEach(function (p) { body += row(esc(p.name), personVals(p, "total_pay"), "person"); });
    body += row("Actually paid", vals("ceo_actually_paid", mn));
    ppl.forEach(function (p) { body += row(esc(p.name), personVals(p, "actually_paid"), "person"); });
    body += "<tr class='heading'><td class='lbl'>Other named executives, average <span class='unit'>($m)</span></td>" + blank + "</tr>";
    body += row("Total pay", vals("neo_avg_total_pay", mn));
    body += row("Actually paid", vals("neo_avg_actually_paid", mn));
    body += "<tr class='heading'><td class='lbl'>Performance</td>" + blank + "</tr>";
    var base = ys.map(function (y) { return y.tsr_base; }).filter(Boolean)[0];
    var from = base ? "$100 invested " + esc(base) : "value of $100";
    body += row("Shareholder return <span class='unit'>(" + from + ")</span>", vals("tsr", tsr));
    body += row("Peer group return <span class='unit'>(" + from + ")</span>", vals("peer_tsr", tsr));
    body += row("Net income <span class='unit'>($m)</span>", vals("net_income", mn));
    $("pv-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("pv-calc").innerHTML = "<b>Nothing is recomputed.</b> " + esc(co.calc.total_pay) + " " +
      esc(co.calc.actually_paid) + " " + esc(co.calc.tsr) + " " + esc(co.calc.basis) + " " +
      esc(co.calc.ceos) + " " + esc(co.calc.names) + " A row with no tagged value for the CEO as a whole but one tagged by " +
      "name shows the named person's figure in the person rows only.";
  }

  var PROXY_STATUS = { ok: "Read", ok_no_pvp: "Read, no pay table", no_xbrl: "No tagged data",
                       failed: "Failed, retried next check" };

  function renderFilings() {
    var fs = co.filings;
    $("px-note").textContent = fs.length + (fs.length === 1 ? " proxy" : " proxies") + " since 2023";
    $("px-table").innerHTML = "<thead><tr><th>Filed</th><th>Filing</th><th>Status</th>" +
      "<th class=r>Tagged Facts</th></tr></thead><tbody>" + fs.map(function (f) {
        var st = f.status === "ok" && f.detail ? "ok_no_pvp" : f.status;
        return "<tr><td>" + esc(f.filed) + "</td><td class='mono'>" + secLink(f.url, f.accn) +
          "</td><td" + (f.detail ? " title='" + esc(f.detail) + "'" : "") + ">" +
          esc(PROXY_STATUS[st] || st) + "</td><td class=r>" + count(f.facts) + "</td></tr>";
      }).join("") + "</tbody>";
  }

  var CHARTS = {
    pay: { kind: "cols", unit: "$m", scale: 1e6, dp: 1,
           series: [["ceo_total_pay", "CEO total pay"], ["ceo_actually_paid", "CEO actually paid"]] },
    tsr: { kind: "line", unit: "$", scale: 1, dp: 2,
           series: [["tsr", "Company"], ["peer_tsr", "Peer group"]] },
    neo: { kind: "cols", unit: "$m", scale: 1e6, dp: 1,
           series: [["neo_avg_total_pay", "Total pay"], ["neo_avg_actually_paid", "Actually paid"]] },
  };

  function drawChart() {
    var ys = years(), spec = CHARTS[state.chart], el = $("pv-chart");
    if (chart) { chart.dispose(); chart = null; }
    if (!ys.length) { el.innerHTML = ""; return; }
    var cats = ys.map(function (y) { return fy(y.fiscal_year_end); });
    var anyNeg = false;
    var ser = spec.series.map(function (f, i) {
      return { name: f[1], slot: i + 1, points: ys.map(function (y) {
        var v = y.values[f[0]];
        // The one CEO in office at the year end stands for the year, as in
        // the list; co-CEOs never do, and nothing is added together.
        var atEnd = y.ceos.filter(function (p) { return p.at_year_end; });
        if (atEnd.length === 1 && (v == null || y.ceos.length > 1)) {
          if (f[0] === "ceo_total_pay") v = atEnd[0].total_pay;
          if (f[0] === "ceo_actually_paid") v = atEnd[0].actually_paid;
        }
        if (v != null && v < 0) anyNeg = true;
        return v == null ? null : v / spec.scale;
      }) };
    });
    if (!ser.some(function (s) { return s.points.some(function (v) { return v != null; }); })) {
      el.innerHTML = "<p class='state-empty'>No values for this chart in the proxies read" +
        (state.chart === "pay" ? " — co-CEOs are each in the table, and none is added together" : "") +
        ".</p>";
      $("pv-source").textContent = "";
      return;
    }
    var source = CREDIT + " · " + co.company.name + " (" + co.company.ticker + ") · " + basisWords() +
      " · Official, as filed";
    if (spec.kind === "cols" && !anyNeg) {
      chart = obsChart(el, "cols", { categories: cats, series: ser, unitSuffix: spec.unit, dp: spec.dp,
        yAxisName: spec.unit, trust: "official", sourceLine: source, labelInterval: "auto", gridRight: 30 });
    } else {
      chart = obsChart(el, "line", { xType: "category", unit: "", unitSuffix: spec.unit, dp: spec.dp,
        yAxisName: spec.unit, trust: "official", sourceLine: source, gridRight: 36,
        series: ser.map(function (s) {
          return { name: s.name, slot: s.slot, points: s.points.map(function (v, i) { return [cats[i], v]; }) };
        }) });
    }
    $("pv-source").textContent = source;
  }

  function companyCsv() {
    if (!co) return;
    var c = co.company, ys = years(), ppl = people();
    var fields = ["ceo_total_pay", "ceo_actually_paid", "neo_avg_total_pay", "neo_avg_actually_paid",
                  "tsr", "peer_tsr", "net_income", "measure_value"];
    csvDownload("us-pay-" + c.ticker + "-" + state.basis + ".csv", [
      "Plover Analytics — pay versus performance as filed: " + c.name + " (" + c.ticker + "), CIK " + c.cik,
      "basis: " + state.basis + " · trust: official (as filed) · " + CREDIT,
      "money in USD exactly as filed; tsr and peer_tsr = value of $100 invested; measure_value in the company's own unit",
      "ceo_<name>_ columns: a CEO the proxy tags by name; never added together",
    ], ["fiscal_year_start", "fiscal_year_end", "ceo_names"].concat(fields, ["measure_name"],
        ppl.reduce(function (a, p) {
          var k = "ceo_" + p.name.replace(/[^A-Za-z]+/g, "_").replace(/^_|_$/g, "");
          return a.concat([k + "_total_pay", k + "_actually_paid"]);
        }, [])),
      ys.map(function (y) {
        return [y.fiscal_year_start, y.fiscal_year_end, y.ceo_names.join("; ")]
          .concat(fields.map(function (f) { return y.values[f]; }), [y.measure_name],
            ppl.reduce(function (a, p) {
              var hit = y.ceos.filter(function (x) { return x.member === p.member; })[0];
              return a.concat([hit ? hit.total_pay : null, hit ? hit.actually_paid : null]);
            }, []));
      }));
  }

  function loadCompany() {
    syncUrl();
    getJSON(API + "/" + encodeURIComponent(state.t) + "?basis=" + state.basis).then(function (d) {
      co = d;
      renderCompany();
    }).catch(function (e) {
      $("co-name").textContent = state.t;
      $("co-meta").textContent = e.status === 404
        ? "This ticker is not in the S&P 500 member list. Go back to the list to choose one."
        : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      if (chart) { chart.dispose(); chart = null; }
      $("pv-chart").innerHTML = "";
    });
  }

  // ---- boot --------------------------------------------------------------------------
  checkStale();
  initThemeToggle(function () { if (co) drawChart(); });
  var p = new URLSearchParams(location.search);
  if (p.get("basis") === "first") state.basis = "first";
  if (CHARTS[p.get("chart")]) state.chart = p.get("chart");
  var t = p.get("t");
  if (t) {
    state.t = t.trim().toUpperCase();
    $("index-view").hidden = true;
    $("company-view").hidden = false;
    setSeg("pv-basis", state.basis);
    $("pv-chart-select").value = state.chart;
    $("pv-basis").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      state.basis = b.getAttribute("data-basis"); setSeg("pv-basis", state.basis);
      loadCompany();
    });
    $("pv-chart-select").addEventListener("change", function () {
      state.chart = this.value; syncUrl(); drawChart();
    });
    $("pv-csv").addEventListener("click", companyCsv);
    $("pv-png").addEventListener("click", function () {
      if (chart) chart.exportPNG("us-pay-" + state.t + "-" + state.chart + ".png");
    });
    loadCompany();
  } else {
    setSeg("ix-basis", state.basis);
    $("ix-basis").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      state.basis = b.getAttribute("data-basis"); setSeg("ix-basis", state.basis);
      loadIndex();
    });
    $("ix-csv").addEventListener("click", indexCsv);
    loadIndex();
  }
})();
