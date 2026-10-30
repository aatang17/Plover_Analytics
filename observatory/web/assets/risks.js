/* Business risks (事業等のリスク, annual securities report): a company search and
   a text search across every company's latest section (market view), and one
   company's section item by item with what is new against its previous report
   (company view). Data: /api/v1/equity/risks/*. The text is official, exactly as
   filed; the item cut and the New / Dropped comparison are calculated and carry
   their rule. The URL holds the whole view: ?c=code&year=YYYY&q=term, or ?t=term. */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function day(iso) { return iso ? String(iso) : MISSING; }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
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

  function csvHeader(calc, extra) {
    return [
      "Plover Analytics — business risks (事業等のリスク), annual securities reports, EDINET",
      "Source: company filings on EDINET (Financial Services Agency of Japan)",
      "Official as filed: the text (heading, body). Spacing tidied; tables one row per line.",
      "Calculated: items = " + (calc ? calc.items : ""),
      "Calculated: new = " + (calc ? calc.new : ""),
      "Retrieved: " + new Date().toISOString(),
    ].concat(extra || []);
  }

  // Wrap every occurrence of the search term; the text is escaped first, so
  // the only markup that reaches the page is ours.
  function highlight(text, term) {
    var safe = esc(text);
    if (!term) return safe;
    var t = esc(term);
    return safe.split(t).join("<mark class='hl'>" + t + "</mark>");
  }

  function paras(text, term) {
    if (!text) return "";
    return text.split("\n").map(function (l) {
      return "<p>" + highlight(l, term) + "</p>";
    }).join("");
  }

  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    return "<div><a href='" + href + "'>" + esc(primary) + "</a></div>";
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected; try reloading.</p>";
  }

  // ---- market view ---------------------------------------------------------
  function renderCoverage(d) {
    var clean = 0;
    (d.by_status || []).forEach(function (s) { if (s.status === "clean") clean = s.companies; });
    $("coverage").innerHTML =
      "Held: <b>" + count(d.companies) + " companies</b>, " + count(d.filings) +
      " annual reports for fiscal years ending " + esc(day(d.first_period_end)) + " to " +
      esc(day(d.last_period_end)) + ", latest filed " + esc(day(d.last_filed)) + ". " +
      count(clean) + " companies' latest sections are itemised; the rest have no numbered " +
      "headings and are shown whole.";
  }

  function runCompanySearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/risks/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No business-risks section on " +
            "file for a company matching “" + esc(q) + "”.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-stk' data-no-enhance><thead><tr>" +
          "<th>Company</th><th>Fiscal year</th><th class=r>Items</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.name, "risks.html?c=" + esc(c.sec_code)) + delistedTag(c) +
              "</td><td class=nowrap>" +
              esc(fmtFiscalYear(c.period_end)) + "</td><td class=r>" +
              (c.status === "unsplit" ? MISSING : count(c.n_top_items)) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  function runTextSearch(term) {
    var p = new URLSearchParams(location.search);
    if (term) p.set("t", term); else p.delete("t");
    history.replaceState(null, "", "risks.html" + (p.toString() ? "?" + p.toString() : ""));
    if (!term || term.length < 2) {
      $("ts-wrap").innerHTML = ""; $("ts-count").textContent = "";
      return;
    }
    getJSON("/api/v1/equity/risks/search?limit=500&q=" + encodeURIComponent(term))
      .then(function (d) {
        $("ts-count").textContent = count(d.companies_matched) + " companies";
        if (!d.companies.length) {
          $("ts-wrap").innerHTML = "<p class='sec-note'>No company's latest section contains “" +
            esc(term) + "”.</p>";
          return;
        }
        $("ts-wrap").innerHTML =
          "<table class='tbl-stk' data-export-name='Business risks mentioning " +
          esc(term) + "'><thead><tr><th>Company</th><th class=col-narrow>Fiscal year</th>" +
          "<th class='r col-narrow'>Items matched</th><th>Where it appears</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            var href = "risks.html?c=" + encodeURIComponent(c.sec_code) + "&q=" +
              encodeURIComponent(term);
            var items = c.matches.filter(function (m) { return m.item_no != null; });
            return "<tr><td>" + nameCell(c.name_en, c.name, href) +
              "</td><td class='nowrap col-narrow' data-sort='" +
              esc(c.period_end) + "'>" + esc(fmtFiscalYear(c.period_end)) +
              "</td><td class='r col-narrow'>" + count(items.length) + "</td><td><ul class='hit-list'>" +
              c.matches.slice(0, 3).map(function (m) {
                return "<li><div class='hit-head'>" +
                  (m.heading ? esc(m.heading) : "Preamble") + "</div><div class='hit-snip'>" +
                  highlight(m.snippet, term) + "</div></li>";
              }).join("") +
              (c.matches.length > 3 ? "<li class='hit-snip'>and " +
                count(c.matches.length - 3) + " more</li>" : "") +
              "</ul></td></tr>";
          }).join("") + "</tbody></table>" +
          (d.returned < d.companies_matched ? "<p class='sec-note'>Showing the first " +
            count(d.returned) + ".</p>" : "");
      })
      .catch(function (e) { errorInto("ts-wrap", e); });
  }

  function initMarket() {
    getJSON("/api/v1/equity/risks/summary").then(renderCoverage)
      .catch(function (e) { errorInto("coverage", e); });
    var timer = null, timer2 = null;
    $("q").addEventListener("input", function () {
      var v = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { runCompanySearch(v); }, 180);
    });
    $("ts-q").addEventListener("input", function () {
      var v = this.value.trim();
      clearTimeout(timer2);
      timer2 = setTimeout(function () { runTextSearch(v); }, 300);
    });
    var t = new URLSearchParams(location.search).get("t");
    if (t) { $("ts-q").value = t; runTextSearch(t); }
  }

  // ---- company view --------------------------------------------------------
  function fact(label, value, qual) {
    return "<div><dt>" + label + "</dt><dd>" + value +
      (qual ? "<span class='qual'>" + qual + "</span>" : "") + "</dd></div>";
  }

  function renderCompany(d, term) {
    document.title = (d.name_en || d.filer_name) + " — Business Risks · Plover Analytics";
    $("c-name").textContent = d.name_en || d.filer_name;
    $("c-code").textContent = d.sec_code;
    if (d.name_en && d.filer_name) { $("c-name-ja").textContent = d.filer_name; $("c-name-ja").hidden = false; }
    $("c-meta").innerHTML = trustBadge("official") + "Annual securities report for " +
      esc(fmtFiscalYear(d.period_end)) + " (period ended " + esc(day(d.period_end)) +
      "), filed " + esc(day(d.filed_date)) + " · <a href='" + esc(d.source_url) +
      "' rel='noopener'>" + esc(d.doc_id) + " on EDINET</a>";

    var top = d.items.filter(function (i) { return i.level === 1; }).length;
    var subs = d.items.length - top;
    var fresh = d.items.filter(function (i) { return i.new === true; }).length;
    var cmp = d.compared_with;
    var comparable = d.items.some(function (i) { return i.new !== null; });
    $("c-facts").innerHTML =
      fact("Items", d.status === "unsplit" ? MISSING : count(top),
           subs ? count(subs) + " sub-items" : (d.status === "unsplit" ? "Not itemised" : "")) +
      fact("New since last report", comparable ? count(fresh) : MISSING,
           comparable ? "vs " + esc(fmtFiscalYear(cmp.period_end))
                      : (cmp ? "Previous report not itemised" : "No earlier report on file")) +
      fact("Dropped", d.dropped == null ? MISSING : count(d.dropped.length),
           d.dropped == null ? "" : "headings no longer present") +
      fact("Length", count(d.n_chars), "characters");

    $("c-items-note").textContent = fmtFiscalYear(d.period_end);
    $("c-years").innerHTML = d.reports.map(function (r) {
      var y = r.period_end ? r.period_end.slice(0, 4) : "";
      return "<button type='button' data-year='" + esc(y) + "' aria-pressed='" +
        (r.doc_id === d.doc_id) + "'>" + esc(fmtFiscalYear(r.period_end)) + "</button>";
    }).join("");
    $("c-years").onclick = function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      var p = new URLSearchParams(location.search);
      p.set("year", b.getAttribute("data-year"));
      location.search = p.toString();
    };

    if (d.warning) { $("c-warning").textContent = d.warning; $("c-warning").hidden = false; }
    if (d.status === "unsplit") {
      $("c-items").innerHTML = "<li class='risk-item'>" + paras(d.full_text, term) + "</li>";
    } else {
      if (d.preamble) {
        $("c-preamble").innerHTML = paras(d.preamble, term);
        $("c-pre-wrap").hidden = false;
        if (term && d.preamble.indexOf(term) > -1) $("c-pre-wrap").open = true;
      }
      $("c-items").innerHTML = d.items.map(function (it) {
        var isNew = it.new === true ? " <span class='badge badge-new'>New</span>" : "";
        return "<li class='risk-item lvl-" + it.level + (it.heading_inline ? " inline" : "") +
          "'><h3>" + highlight(it.heading, term) + isNew + "</h3>" + paras(it.body, term) + "</li>";
      }).join("");
    }
    $("c-calc").innerHTML = "<p><b>Items.</b> " + esc(d.calc.items) + "</p><p><b>New.</b> " +
      esc(d.calc.new) + "</p><p>Parser " + esc(d.parser_version) + " · source file SHA-256 " +
      "<code>" + esc(d.sha256_t1) + "</code></p>";

    if (d.dropped && d.dropped.length) {
      $("c-dropped").innerHTML = d.dropped.map(function (h) { return "<li>" + esc(h) + "</li>"; }).join("");
      $("c-dropped-note").textContent = "vs " + fmtFiscalYear(cmp.period_end);
      $("c-dropped-wrap").hidden = false;
    }

    $("c-csv").onclick = function () {
      var rows = d.items.map(function (it) {
        return [d.sec_code, d.doc_id, d.period_end, it.item_no, it.level, it.parent_no,
                it.heading, it.body, it.new];
      });
      if (d.status === "unsplit") {
        rows = [[d.sec_code, d.doc_id, d.period_end, "", "", "", "", d.full_text, ""]];
      }
      csvDownload("business-risks-" + d.sec_code + "-" + (d.period_end || "") + ".csv",
        csvHeader(d.calc, ["Company: " + (d.name_en || "") + " " + d.filer_name + " (" +
                           d.sec_code + ")",
                           "Filing: " + d.doc_id + ", period ended " + d.period_end +
                           ", filed " + d.filed_date + ", SHA-256 " + d.sha256_t1,
                           "Compared with: " + (cmp ? cmp.doc_id + " (" + cmp.period_end + ")"
                                                     : "no earlier report on file")]),
        ["sec_code", "doc_id", "period_end", "item_no", "level", "parent_item_no",
         "heading", "body", "new"], rows);
    };

    if (term) {
      var first = document.querySelector("#c-items mark.hl, #c-preamble mark.hl");
      if (first) first.scrollIntoView({ block: "center" });
    }
  }

  initThemeToggle(function () {});

  var params = new URLSearchParams(location.search);
  var code = params.get("c");
  if (code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    var term = params.get("q") || "";
    getJSON("/api/v1/equity/risks/company/" + encodeURIComponent(code) +
            (params.get("year") ? "?year=" + encodeURIComponent(params.get("year")) : ""))
      .then(function (d) { renderCompany(d, term); })
      .catch(function (e) {
        $("c-name").textContent = code;
        $("c-meta").textContent = e.message.indexOf("404") > -1
          ? "No business-risks section on file for this company."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else {
    initMarket();
  }
})();
