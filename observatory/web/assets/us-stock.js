/* One US company's Price and Insiders and holders tabs: us-stock.html?t=TICKER
   (&view=owners for the second). Data: /api/v1/us/financials/metrics,
   /api/v1/us/prices, /api/v1/us/insiders, /api/v1/us/holdings (app/).

   Until 2026-10-28 these two tabs were drawn on the front page, which showed
   for two seconds before the company replaced it. Now the company's header
   and tabs (co-head.js) come first, then each tab loads only its own data.

   Signed out where accounts are on, the header and tabs show with a sign-up
   panel in place of the data (agreed with the user, 2026-10-28), as the ask
   box shows the start of an answer.

   Filed values are as the SEC extracted them; closes as the vendor delivered
   them; every calculated figure names its formula. Missing is —, never 0. */
(function () {
  "use strict";

  var API = "/api/v1/us";
  var charts = [];
  var esc = usFmt.esc, money = usFmt.money, pct = usFmt.pct, mult = usFmt.mult, count = usFmt.count,
      dateWords = usFmt.dateWords, tableHtml = usFmt.tableHtml, sourcesHtml = usFmt.sourcesHtml;

  function $(id) { return document.getElementById(id); }
  // the same words as the server's title for this tab (app/prerender.py _us_stock_view)
  function setTitle(name, ticker) {
    document.title = name + " (" + ticker + ") — " +
      (stockView === "owners" ? "Insider Trades & 13F Holders" : "Stock Price & Ratios") + " · Plover Analytics";
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          var e = new Error(typeof b.detail === "string" ? b.detail : "");
          e.status = r.status;
          throw e;
        });
      }
      return r.json();
    });
  }

  var params = new URL(location.href).searchParams;
  var t = String(params.get("t") || "").toUpperCase().replace(/\s+/g, "");
  // which of the company's tabs this page is: "price" or "owners"
  var stockView = params.get("view") === "owners" ? "owners" : "price";

  function renderStock(t, m, px, ins, hold) {
    var el = $("stock");
    var mt = m.metrics || {}, p = m.price || {}, ps = m.per_share || {}, mvb = m.market_value_basis || {};
    // a split read from prices in the last year that no filing confirms makes the one-year change doubtful
    var yearAgo = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
    var doubtfulSplit = !!(px && px.splits && px.splits.some(function (s) { return s.status && s.status !== "confirmed" && s.date >= yearAgo; }));
    var html = "<p class='sec-note'>Latest annual report: year to " + dateWords(m.period_end) + ", filed " + dateWords(m.filed) + " · " +
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
    setTitle(m.name, m.ticker || t);
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

  // Who is reading: signed out where accounts are on, the data is one sign-up
  // away. No account service here (404) or a signed-in reader sees it all.
  function signedOut() {
    return fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 404) return false;
      return r.ok ? r.json().then(function (who) { return !(who && who.email); }) : true;
    }, function () { return false; });
  }

  function gate() {
    var back = "signin.html?returnTo=" + encodeURIComponent("/us-stock.html" + location.search);
    $("stock").innerHTML = "<section class='cp-panel cp-sec'><div class='cp-head'><h2>" +
      (stockView === "owners" ? "Insider Trades and 13F Holders" : "Share Price and Ratios") + "</h2></div>" +
      "<div class='cp-body st-gate'><p>" + (stockView === "owners"
        ? "Every insider purchase and sale in the last 12 months, and the largest institutional holders from 13F filings."
        : "The share price since 2000, market value, P/E and yields, and the latest annual report's figures and ratios.") +
      " A free account shows them.</p>" +
      "<a class='btn btn-primary' href='" + esc(back) + "'>Create free account</a></div></section>";
  }

  function load() {
    var quiet = function (p) { return p.catch(function () { return null; }); };
    return getJSON(API + "/financials/metrics/" + encodeURIComponent(t)).then(function (m) {
      var tk = m.ticker || t;
      // each tab asks only for what it shows
      var owners = stockView === "owners";
      return Promise.all([
        owners ? null : quiet(getJSON(API + "/prices/" + encodeURIComponent(tk) + "?start=2000-01-01")),
        owners ? quiet(getJSON(API + "/insiders/company/cik:" + m.cik + "?months=12&limit=40")) : null,
        owners ? quiet(getJSON(API + "/ask/run/company_holders?company=" + encodeURIComponent(tk)).then(function (a) {
          // the recipe already picked the common-stock CUSIP and the window
          return getJSON(API + "/holdings/issuer?cusip=" + encodeURIComponent(a.entities.cusip) + "&limit=15");
        })) : null,
      ]).then(function (r) { renderStock(tk, m, r[0], r[1], r[2]); });
    });
  }

  function failed(e) {
    $("stock").innerHTML = "<section class='cp-panel cp-sec'><div class='cp-body'><p class='state-error'>" +
      esc(e.status === 404 ? "No US-listed company trades as " + t + "." : (e.message || "This page could not load.")) +
      "</p><p class='sec-note'>Try the ticker as it trades (AAPL, BRK.B), or find the company in " +
      "<a href='us-company.html'>All Companies</a>.</p></div></section>";
  }

  // no company named: the list of companies is where one is picked
  if (!t) { location.replace("us-company.html"); return; }
  // the header and tabs first, whoever is reading
  $("co-card").innerHTML = "<section class='cp-card'><div class='cp-id'><div class='cp-who'><h1>" + esc(t) + "</h1></div></div></section>";
  coHead.load(t).then(function (d) {
    $("co-card").innerHTML = coHead.html(d, stockView);
    coHead.reveal($("co-card"));
    setTitle(d.record.company.name, d.record.company.ticker || t);
  }).catch(function () { /* the ticker stays as the name; the data below says if it is unknown */ });
  $("stock").innerHTML = "<p class='state-wait' aria-live='polite'>Loading " +
    (stockView === "owners" ? "insider trades and holders" : "prices and ratios") + "…</p>";
  signedOut().then(function (out) { return out ? gate() : load(); }).catch(failed);

  // The theme toggle: charts are drawn in the theme's palette, so a switch
  // redraws the page as the other data pages do.
  initThemeToggle(function () { if (charts.length) location.reload(); });
  window.addEventListener("resize", function () { charts.forEach(function (c) { c.resize && c.resize(); }); });
})();
