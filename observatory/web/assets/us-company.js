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

  function logo(url, cls) {
    return url ? "<img class='" + cls + "' src='" + esc(url) + "' alt='' width='20' height='20' loading='lazy'>"
               : "<span class='" + cls + "'></span>";
  }

  // ---- search ------------------------------------------------------------------
  var seq = 0, timer = null;

  function search(q) {
    var mine = ++seq;
    var term = q.trim();
    if (!term) {
      $("uc-state").textContent = "Type a name or a ticker.";
      $("uc-results").innerHTML = "";
      return;
    }
    $("uc-state").textContent = "Searching…";
    getJSON(API + "?q=" + encodeURIComponent(term)).then(function (d) {
      if (mine !== seq) return;
      if (!d.companies.length) {
        $("uc-state").textContent = "No US company matches “" + term + "”. Try a ticker, like AAPL, or part of the name.";
        $("uc-results").innerHTML = "";
        return;
      }
      $("uc-state").textContent = d.count === 20 ? "The first 20 matches. Type more of the name to narrow them."
                                                 : d.count + (d.count === 1 ? " match" : " matches");
      $("uc-results").innerHTML = "<thead><tr><th>Company</th><th>Ticker</th><th>Exchange</th>" +
        "<th class='r'>SEC number</th></tr></thead><tbody>" +
        d.companies.map(function (c) {
          var href = "us-company.html?t=" + encodeURIComponent(c.ticker || c.cik);
          return "<tr><td>" + logo(c.logo, "logo") + "<a href='" + href + "'>" + esc(c.name) + "</a></td>" +
            "<td class='mono'>" + esc(c.ticker || MISSING) + "</td><td>" + esc(c.exchange || MISSING) +
            "</td><td class='r mono'>" + c.cik + "</td></tr>";
        }).join("") + "</tbody>";
    }).catch(function (e) {
      if (mine !== seq) return;
      $("uc-state").textContent = "Search is unavailable (" + e.message + "). Try again in a moment.";
    });
  }

  // ---- one company -------------------------------------------------------------
  function render(d) {
    var c = d.company;
    var shown = c.name || c.sec_name || ("CIK " + c.cik);
    document.title = shown + (c.ticker ? " (" + c.ticker + ")" : "") + " · US Company Records · Plover Analytics";
    $("co-name").textContent = shown;
    $("co-code").textContent = c.ticker || "";
    $("co-logo").hidden = !c.logo;
    if (c.logo) $("co-logo").src = c.logo;
    var meta = ["SEC number (CIK) <span class='mono'>" + c.cik + "</span>"];
    if (c.tickers && c.tickers.length > 1) meta.push("Tickers " + c.tickers.map(esc).join(", "));
    if (c.exchange) meta.push(esc(c.exchange));
    if (c.industry) meta.push(esc(c.industry) + (c.sic ? " (SIC " + esc(c.sic) + ")" : ""));
    if (c.website) meta.push("<a href='https://" + esc(c.website) + "' rel='noopener'>" + esc(c.website) + "</a>");
    if (c.sp500_weight_pct != null) meta.push(fmtNum(c.sp500_weight_pct, 2) + "% of the S&amp;P 500 (SPY)");
    if (c.sec_name && c.sec_name !== shown) meta.push("Name as filed: " + esc(c.sec_name));
    if (c.predecessor_cik) meta.push("Earlier filings under CIK <span class='mono'>" + c.predecessor_cik + "</span>");
    $("co-meta").innerHTML = meta.join(" · ");
    $("items-note").textContent = d.held + " of " + d.items.length + " items held";
    $("items-table").innerHTML = "<thead><tr><th>Item</th><th class='r'>Held</th><th class='r'>Latest</th>" +
      "<th class='open'>Read it</th><th class='api'>API</th></tr></thead><tbody>" +
      d.items.map(function (i) {
        var held = i.held ? fmtNum(i.count, 0) + " <span class='unit'>" + esc(i.count === 1 ? i.unit_one : i.unit) + "</span>" : MISSING;
        var where = i.page || (i.held ? i.api : null);
        var latest = i.held && i.latest ? "<span class='unit'>" + esc(i.latest_kind) + "</span> <span class='day'>" + esc(i.latest) + "</span>" : MISSING;
        var page = i.page ? "<a href='" + esc(i.page) + "'>Open</a>"
                          : (i.held ? "<a href='" + esc(i.api) + "'>As data</a>" : MISSING);
        return "<tr><td" + (i.held ? "" : " class='muted'") + ">" +
          (where ? "<a href='" + esc(where) + "'>" + esc(i.label) + "</a>" : esc(i.label)) + "</td>" +
          "<td class='r" + (i.held ? "" : " muted") + "'>" + held + "</td>" +
          "<td class='r" + (i.held ? "" : " muted") + "'>" + latest + "</td>" +
          "<td class='open'>" + page + "</td><td class='api'><a class='mono' href='" + esc(i.api) + "'>" + esc(i.api) + "</a></td></tr>";
      }).join("") + "</tbody>";
    $("search-view").hidden = true;
    $("company-view").hidden = false;
  }

  function open(t) {
    $("search-view").hidden = true;
    $("company-view").hidden = false;
    $("co-name").textContent = t;
    $("co-meta").textContent = "Loading this company's record…";
    getJSON(API + "/" + encodeURIComponent(t)).then(render).catch(function (e) {
      $("items-table").innerHTML = "";
      $("items-note").textContent = "";
      $("co-meta").innerHTML = (e.status === 404 || e.status === 400
        ? "No US company has the ticker or SEC number “" + esc(t) + "”. "
        : "The record is unavailable (" + esc(e.message) + "). ") +
        "<a href='us-company.html'>Search for the company</a>.";
    });
  }

  initThemeToggle(function () {});
  var t = new URLSearchParams(location.search).get("t");
  if (t) {
    open(t.trim());
  } else {
    $("uc-q").addEventListener("input", function () {
      var q = this.value;
      clearTimeout(timer);
      timer = setTimeout(function () { search(q); }, 180);
    });
    $("uc-q").focus();
  }
})();
