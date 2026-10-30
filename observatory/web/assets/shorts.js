/* Short positions: the most-shorted companies, the short sellers and the tape
   (market view), every disclosed short in one company (company view), and one
   holder's book (holder view).
   Data: /api/v1/equity/shorts/*. Ratios, share counts and the calculation date
   are official exactly as JPX published them; whether a position is still open,
   a company's total across its holders, and the move since the holder's
   previous report are calculated here and say so.
   Missing renders as —, never 0. A closing report's zero is a REAL zero: the
   position went away. */
(function () {
  "use strict";

  var tapeState = { filter: "all" };
  var coState = { sort: "disclosed_pct" };
  var coverage = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function pct(v, dp) { return v == null ? MISSING : fmtNum(v, dp == null ? 2 : dp); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function day(iso) { return iso ? String(iso) : MISSING; }

  function move(v) {
    // A change of a percentage is percentage points, and the sign is printed
    // rather than coloured: this is not a gain/loss surface.
    return v == null ? MISSING : fmtSigned(v, 2, "pp");
  }

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

  // Every export carries where the numbers came from, which of them are
  // published and which are calculated, and how deep the archive runs.
  function csvHeader(extra) {
    var head = [
      "Plover Analytics — short positions (空売り残高), Japan Exchange Group",
      "Source: JPX daily Information on Outstanding Short Selling Positions",
      "Official as published: ratio_pct (空売り残高割合), shares (空売り残高数量), " +
        "units, calc_date (計算年月日), publish_date",
      "Calculated: is_open = the newest report per (issuer, holder, manager, fund), " +
        "counted open unless it is a below-threshold closing report",
      "Calculated: disclosed_short_pct = sum of that issuer's open positions' ratios",
      "Calculated: ratio_change_pp = ratio_pct − prev_ratio_pct, both as published",
      "Only positions of 0.5% or more are disclosed: a total is the DISCLOSED short, " +
        "not short interest",
      "Missing is empty, never 0. A closing report's 0 is a real zero.",
    ];
    if (coverage) {
      head.push("Archive: " + coverage.days_covered + " publication days, " +
                coverage.first_published + " to " + coverage.last_published);
    }
    head.push("Retrieved: " + new Date().toISOString());
    return head.concat(extra || []);
  }

  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    var link = href ? "<a href='" + href + "'>" + esc(primary) + "</a>" : esc(primary);
    return "<div class='cell-item'><div class='en'>" + link + "</div></div>";
  }

  function holderCell(name, key, sub) {
    // The holder's address or mandate is hover text, not a second line.
    return "<div class='cell-item'" + (sub ? " title='" + esc(sub) + "'" : "") +
      "><a href='shorts.html?h=" + encodeURIComponent(key) + "'>" + esc(name) + "</a></div>";
  }

  // A holder reporting through a mandate names the manager and the fund. Those
  // are part of the position's identity, not decoration, so they travel with
  // the holder's name rather than being dropped.
  function mandate(row) {
    var parts = [];
    if (row.manager_name) parts.push("via " + row.manager_name);
    if (row.fund_name) parts.push(row.fund_name);
    return parts.join(" · ");
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected; try reloading.</p>";
  }

  // ---- coverage ------------------------------------------------------------
  // The depth of the archive is the completeness of every book on this page, so
  // it is stated at the top rather than in a footnote — and it goes loud when
  // the tape stops arriving.
  function renderCoverage(el, c) {
    coverage = c;
    var last = c.last_published;
    var behind = last ? Math.round((Date.now() - Date.parse(last + "T00:00:00Z")) / 86400000) : null;
    var stale = behind == null || behind > 7;
    el.className = "coverage-bar" + (stale ? " is-stale" : "");
    el.innerHTML =
      (stale
        ? "<b>The tape has not advanced since " + esc(day(last)) + "</b> (" +
          count(behind) + " days). Everything below is the book as it stood then. "
        : "") +
      "This record holds <b>" + count(c.days_covered) + " publication days</b>, " +
      esc(day(c.first_published)) + " to " + esc(day(c.last_published)) + ", and " +
      count(c.reports_published) + " reports. JPX keeps about twelve business days on its " +
      "site and then deletes them — there is no archive, no API and no second source, so " +
      "this record begins when capture began and cannot be back-filled. " +
      "<b>A position opened before " + esc(day(c.first_published)) + " and not moved since " +
      "has never been published into it.</b>";
  }

  // ---- market view ---------------------------------------------------------
  function renderStrip(d) {
    var b = d.book, t = d.tape;
    renderCoverage($("coverage"), d.coverage);
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Open positions", count(b.open_positions) + '<span class="unit"> positions</span>',
           "Latest report per holder and company still at 0.5% or more, across " +
           count(b.issuers_shorted) + " companies (calculated)") +
      cell("Companies shorted", count(b.issuers_shorted) + '<span class="unit"> companies</span>',
           count(b.holders) + " holders disclose a position of 0.5% or more") +
      cell("Largest position", pct(b.largest_position_pct) + '<span class="unit">%</span>',
           "Biggest single holder's position; median is " + pct(b.median_position_pct) +
           "% — as published") +
      cell("Reports on the tape", count(t.reports) + '<span class="unit"> reports</span>',
           "Measured " + day(t.earliest_calc_date) + " to " + day(t.latest_calc_date) +
           " · " + count(t.closing_reports) + " closed a position") +
      cell("Archive depth", count(d.coverage.days_covered) + '<span class="unit"> days</span>',
           day(d.coverage.first_published) + " to " + day(d.coverage.last_published) +
           " — the book above is only as complete as this is long") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label">' + label +
        '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  var CO_META = {
    disclosed_pct: "Companies by the share of their stock that disclosed short sellers hold, " +
      "adding each holder's published ratio. Only positions of 0.5% or more are disclosed, so " +
      "this is the <b>disclosed</b> short — always less than the true short interest.",
    holders: "Companies by how many separate holders disclose a short position of 0.5% or more.",
    shares: "Companies by the number of shares held short across all disclosed holders. " +
      "Share counts are comparable within a company, never between two.",
  };

  function renderCompanies(d) {
    var rows = d.companies;
    $("co-count").textContent = rows.length + " companies";
    $("co-meta").innerHTML = CO_META[coState.sort];
    $("co-table").innerHTML =
      "<thead><tr><th>Company</th><th class=r>Disclosed short (%)</th>" +
      "<th class=r>Holders</th><th class=r>Shares short</th>" +
      "<th>Measured</th><th>Published</th></tr></thead><tbody>" +
      rows.map(function (c) {
        return "<tr><td>" + nameCell(c.name_en, c.name, "shorts.html?c=" + esc(c.sec_code)) + delistedTag(c) +
          "</td><td class=r>" + pct(c.disclosed_short_pct) +
          "</td><td class=r>" + count(c.holders) +
          "</td><td class=r>" + count(c.disclosed_short_shares) +
          "</td><td class=nowrap>" + esc(day(c.latest_calc_date)) +
          "</td><td class=nowrap>" + esc(day(c.latest_published)) +
          "</td></tr>";
      }).join("") + "</tbody></table>";
    $("co-formula").innerHTML =
      "<b>Disclosed short (%)</b> = " + esc(d.calc.disclosed_short_pct) + ". " +
      "<b>Open position</b> = " + esc(d.calc.open_position) + ".";
    $("co-csv").onclick = function () {
      csvDownload("jpx-short-companies.csv",
        csvHeader(["Ranked by: " + coState.sort]),
        ["sec_code", "name", "name_en", "disclosed_short_pct", "holders",
         "disclosed_short_shares", "latest_calc_date", "latest_published"],
        rows.map(function (c) {
          return [c.sec_code, c.name, c.name_en, c.disclosed_short_pct, c.holders,
                  c.disclosed_short_shares, c.latest_calc_date, c.latest_published];
        }));
    };
  }

  function renderHolders(d) {
    var rows = d.holders;
    $("ho-count").textContent = rows.length + " holders";
    $("ho-table").innerHTML =
      "<thead><tr><th>Holder</th><th class=r>Open positions</th><th class=r>Companies</th>" +
      "<th class=r>Largest position (%)</th><th>Latest report</th></tr></thead><tbody>" +
      rows.map(function (h) {
        return "<tr><td>" + holderCell(h.holder_name, h.holder_key, h.holder_address) +
          "</td><td class=r>" + count(h.open_positions) +
          "</td><td class=r>" + count(h.issuers) +
          "</td><td class=r>" + pct(h.largest_pct) +
          "</td><td class=nowrap>" + esc(day(h.latest_calc_date)) +
          "</td></tr>";
      }).join("") + "</tbody></table>";
    $("ho-csv").onclick = function () {
      csvDownload("jpx-short-holders.csv", csvHeader(),
        ["holder_key", "holder_name", "holder_address", "open_positions", "companies",
         "largest_pct", "latest_calc_date"],
        rows.map(function (h) {
          return [h.holder_key, h.holder_name, h.holder_address, h.open_positions,
                  h.issuers, h.largest_pct, h.latest_calc_date];
        }));
    };
  }

  var TAPE_QUERY = {
    all: "limit=80",
    moves: "limit=80&min_change=0.5",
    large: "limit=80&min_ratio=2",
    closing: "limit=80&closing=true",
  };
  var TAPE_META = {
    all: "Every report as published, newest calculation date first.",
    moves: "Reports where the holder's position moved by at least half a percentage point " +
      "since its own previous report.",
    large: "Reports of a position of 2% or more of the company.",
    closing: "Final reports: the holder's position fell under the 0.5% disclosure threshold. " +
      "The ratio at or near zero is <b>the position closing</b>, not a missing value.",
  };

  function reportRows(rows, showCompany) {
    return rows.map(function (r) {
      var sub = mandate(r);
      return "<tr" + (r.below_threshold ? " class='is-closed'" : "") + ">" +
        "<td class=nowrap>" + esc(day(r.calc_date)) + "</td>" +
        "<td class=nowrap>" + esc(day(r.publish_date)) + "</td>" +
        (showCompany
          ? "<td>" + nameCell(r.name_en, r.name_ja, "shorts.html?c=" + esc(r.sec_code)) + "</td>"
          : "") +
        "<td>" + holderCell(r.holder_name, r.holder_key, sub) + "</td>" +
        "<td class=r>" + pct(r.ratio_pct) + "</td>" +
        "<td class=r>" + pct(r.prev_ratio_pct) + "</td>" +
        "<td class='r move'>" + move(r.ratio_change_pp) + "</td>" +
        "<td class=r>" + count(r.shares) + "</td>" +
        "<td>" + (r.below_threshold
          ? "<span class='badge badge-note'>Closed — under 0.5%</span>" : "") + "</td>" +
        "</tr>";
    }).join("");
  }

  function reportHead(showCompany) {
    return "<thead><tr><th>Measured</th><th>Published</th>" +
      (showCompany ? "<th>Company</th>" : "") +
      "<th>Holder</th><th class=r>Position (%)</th><th class=r>Was (%)</th>" +
      "<th class=r>Move</th><th class=r>Shares</th><th></th></tr></thead>";
  }

  function reportCols() {
    return ["calc_date", "publish_date", "sec_code", "name_ja", "name_en", "holder_name",
            "manager_name", "fund_name", "ratio_pct", "prev_ratio_pct", "ratio_change_pp",
            "shares", "units", "below_threshold", "note_raw"];
  }

  function reportValues(r) {
    return [r.calc_date, r.publish_date, r.sec_code, r.name_ja, r.name_en, r.holder_name,
            r.manager_name, r.fund_name, r.ratio_pct, r.prev_ratio_pct, r.ratio_change_pp,
            r.shares, r.units, r.below_threshold, r.note_raw];
  }

  function renderTape(d) {
    var rows = d.reports;
    $("tape-count").textContent = rows.length + " reports";
    $("tape-meta").innerHTML = TAPE_META[tapeState.filter];
    $("tape-table").innerHTML = reportHead(true) + "<tbody>" + reportRows(rows, true) +
      "</tbody>";
    $("tape-formula").innerHTML = "<b>Move</b> = " + esc(d.calc.ratio_change_pp) + ".";
    $("tape-csv").onclick = function () {
      csvDownload("jpx-short-reports.csv",
        csvHeader(["Filter: " + tapeState.filter]),
        reportCols(), rows.map(reportValues));
    };
  }

  function renderFiles(d) {
    var rows = d.files;
    $("files-count").textContent = rows.length + " days";
    $("files-table").innerHTML =
      "<thead><tr><th>Published</th><th class=r>Reports</th><th class=r>Companies</th>" +
      "<th class=r>Holders</th><th>Measured</th><th>SHA-256</th></tr></thead><tbody>" +
      rows.map(function (f) {
        return "<tr><td class=nowrap>" + esc(day(f.publish_date)) +
          "</td><td class=r>" + count(f.reports) +
          "</td><td class=r>" + count(f.issuers) +
          "</td><td class=r>" + count(f.holders) +
          "</td><td class=nowrap>" + esc(day(f.calc_date_min)) + " – " +
          esc(day(f.calc_date_max)) +
          "</td><td><span class='sub' style='font-family:var(--obs-mono,monospace)'>" +
          esc((f.sha256 || "").slice(0, 16)) + "…</span></td></tr>";
      }).join("") + "</tbody>";
  }

  function loadCompanies() {
    syncUrl();
    getJSON("/api/v1/equity/shorts/companies?limit=40&sort=" + coState.sort)
      .then(renderCompanies).catch(function (e) { errorInto("co-meta", e); });
  }

  function loadTape() {
    syncUrl();
    getJSON("/api/v1/equity/shorts/recent?" + TAPE_QUERY[tapeState.filter])
      .then(renderTape).catch(function (e) { errorInto("tape-meta", e); });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    if (tapeState.filter !== "all") p.set("view", tapeState.filter);
    if (coState.sort !== "disclosed_pct") p.set("sort", coState.sort);
    history.replaceState(null, "", "shorts.html" + (p.toString() ? "?" + p.toString() : ""));
  }

  function runSearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/shorts/companies?limit=25&q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No disclosed short position " +
            "names that company in this archive. Either nobody is short 0.5% of it, or the " +
            "position has not been re-reported since capture began.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-stk' data-no-enhance><thead><tr>" +
          "<th>Company</th><th class=r>Disclosed short (%)</th><th class=r>Holders</th>" +
          "<th>Measured</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.name, "shorts.html?c=" + esc(c.sec_code)) + delistedTag(c) +
              "</td><td class=r>" + pct(c.disclosed_short_pct) +
              "</td><td class=r>" + count(c.holders) +
              "</td><td class=nowrap>" + esc(day(c.latest_calc_date)) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  // ---- company view --------------------------------------------------------
  function fact(label, value, unit, qual) {
    return "<div><dt>" + label + "</dt><dd>" + value +
      (unit ? "<span class='unit'> " + unit + "</span>" : "") +
      (qual ? "<span class='qual'>" + qual + "</span>" : "") + "</dd></div>";
  }

  function renderCompany(d) {
    var open = d.positions.filter(function (p) { return p.is_open; });
    var latest = open.length ? open[0] : d.positions[0];
    $("c-name").textContent = d.name_en || d.name || d.sec_code;
    $("c-code").textContent = d.sec_code;
    if (d.name_en && d.name) {
      $("c-name-ja").textContent = d.name;
      $("c-name-ja").hidden = false;
    }
    $("c-meta").innerHTML = "Disclosed short positions as published by JPX. " +
      "Latest report measured " + esc(day(latest && latest.calc_date)) + ".";
    $("c-facts").innerHTML =
      fact("Disclosed short", pct(d.disclosed_short_pct), "%",
           "Sum of the open holders' published ratios (calculated)") +
      fact("Holders", count(d.open_positions), "",
           "Separate disclosed positions of 0.5% or more") +
      fact("Shares short", count(d.disclosed_short_shares), "",
           "Sum of the open holders' published share counts (calculated)") +
      fact("Reports held", count(d.reports.length), "",
           "On this company, over the archive's " + count(d.coverage.days_covered) + " days");
    renderCoverage($("c-coverage"), d.coverage);

    $("c-pos-count").textContent = open.length + " open · " +
      (d.positions.length - open.length) + " closed";
    $("c-pos-table").innerHTML =
      "<thead><tr><th>Holder</th><th class=r>Position (%)</th><th class=r>Was (%)</th>" +
      "<th class=r>Move</th><th class=r>Shares</th><th>Measured</th><th></th></tr></thead>" +
      "<tbody>" + d.positions.map(function (p) {
        return "<tr" + (p.is_open ? "" : " class='is-closed'") + ">" +
          "<td>" + holderCell(p.holder_name, p.holder_key, mandate(p) || p.holder_address) +
          "</td><td class=r>" + pct(p.ratio_pct) +
          "</td><td class=r>" + pct(p.prev_ratio_pct) +
          "</td><td class='r move'>" + move(p.ratio_change_pp) +
          "</td><td class=r>" + count(p.shares) +
          "</td><td class=nowrap>" + esc(day(p.calc_date)) +
          "</td><td>" + (p.is_open ? "" :
            "<span class='badge badge-note'>Closed — under 0.5%</span>") +
          "</td></tr>";
      }).join("") + "</tbody>";
    $("c-formula").innerHTML =
      "<b>Open</b> = " + esc(d.calc.open_position) + ". " +
      "<b>Disclosed short</b> = " + esc(d.calc.disclosed_short_pct) + ". " +
      "<b>Move</b> = " + esc(d.calc.ratio_change_pp) + ".";

    $("c-rep-count").textContent = d.reports.length + " reports";
    $("c-rep-table").innerHTML = reportHead(false) + "<tbody>" +
      reportRows(d.reports, false) + "</tbody>";
    $("c-csv").onclick = function () {
      csvDownload("jpx-short-" + d.sec_code + ".csv",
        csvHeader(["Company: " + d.sec_code + " " + (d.name_en || d.name || "")]),
        reportCols(), d.reports.map(reportValues));
    };
  }

  // ---- holder view ---------------------------------------------------------
  function renderHolder(d) {
    $("h-name").textContent = d.holder_name || d.holder_key;
    $("h-meta").textContent = d.holder_address || "";
    var largest = d.positions.length ? d.positions[0] : null;
    $("h-facts").innerHTML =
      fact("Open positions", count(d.open_positions), "",
           "Companies this holder discloses a short of 0.5% or more in") +
      fact("Largest position", pct(largest && largest.ratio_pct), "%",
           largest ? (largest.name_en || largest.name || largest.sec_code) : "") +
      fact("Archive", count(d.coverage.days_covered), "days",
           esc(day(d.coverage.first_published)) + " to " + esc(day(d.coverage.last_published)));
    $("h-pos-count").textContent = d.positions.length + " companies";
    $("h-table").innerHTML =
      "<thead><tr><th>Company</th><th class=r>Position (%)</th><th class=r>Shares</th>" +
      "<th>Measured</th><th>Published</th></tr></thead><tbody>" +
      d.positions.map(function (p) {
        return "<tr><td>" + nameCell(p.name_en, p.name, "shorts.html?c=" + esc(p.sec_code)) +
          "</td><td class=r>" + pct(p.ratio_pct) +
          "</td><td class=r>" + count(p.shares) +
          "</td><td class=nowrap>" + esc(day(p.calc_date)) +
          "</td><td class=nowrap>" + esc(day(p.publish_date)) +
          "</td></tr>";
      }).join("") + "</tbody>";
    $("h-csv").onclick = function () {
      csvDownload("jpx-short-holder.csv",
        csvHeader(["Holder: " + (d.holder_name || d.holder_key)]),
        ["sec_code", "name", "name_en", "ratio_pct", "shares", "calc_date", "publish_date"],
        d.positions.map(function (p) {
          return [p.sec_code, p.name, p.name_en, p.ratio_pct, p.shares, p.calc_date,
                  p.publish_date];
        }));
    };
  }

  // ---- boot ----------------------------------------------------------------
  function initMarket() {
    var p = new URLSearchParams(location.search);
    if (p.get("view") && TAPE_QUERY[p.get("view")]) {
      tapeState.filter = p.get("view");
      Array.prototype.forEach.call($("tape-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed",
          b.getAttribute("data-filter") === tapeState.filter ? "true" : "false");
      });
    }
    if (p.get("sort") && CO_META[p.get("sort")]) {
      coState.sort = p.get("sort");
      Array.prototype.forEach.call($("co-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed",
          b.getAttribute("data-sort") === coState.sort ? "true" : "false");
      });
    }

    getJSON("/api/v1/equity/shorts/summary")
      .then(renderStrip).catch(function (e) { errorInto("stat-strip", e); });
    loadCompanies();
    loadTape();
    getJSON("/api/v1/equity/shorts/holders?limit=40")
      .then(renderHolders).catch(function (e) { errorInto("ho-table", e); });
    getJSON("/api/v1/equity/shorts/files?limit=60")
      .then(renderFiles).catch(function (e) { errorInto("files-table", e); });

    $("tape-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      tapeState.filter = b.getAttribute("data-filter");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadTape();
    });
    $("co-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      coState.sort = b.getAttribute("data-sort");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadCompanies();
    });

    var timer = null;
    $("q").addEventListener("input", function () {
      var v = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { runSearch(v); }, 180);
    });
  }

  initThemeToggle(function () {});

  var params = new URLSearchParams(location.search);
  var code = params.get("c");
  var holderKey = params.get("h");
  if (code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    getJSON("/api/v1/equity/shorts/company/" + encodeURIComponent(code))
      .then(renderCompany)
      .catch(function (e) {
        $("c-name").textContent = code;
        $("c-meta").textContent = e.message.indexOf("404") > -1
          ? "No disclosed short position names this company in the archive. Either nobody is " +
            "short 0.5% of it, or the position has not been re-reported since capture began."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else if (holderKey) {
    $("market-view").hidden = true;
    $("holder-view").hidden = false;
    getJSON("/api/v1/equity/shorts/holder/" + encodeURIComponent(holderKey))
      .then(renderHolder)
      .catch(function (e) {
        $("h-name").textContent = holderKey;
        $("h-meta").textContent = "Data unavailable — " + e.message;
      });
  } else {
    initMarket();
  }
})();
