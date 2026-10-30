/* Buybacks: market view (monthly flows + announced-versus-executed) and the
   per-company view. Data: /api/v1/equity/buyback/*.

   Everything the filing states is official as filed — the authorisation, the
   month's buying, the cumulative, the filer's own progress percentage, the
   shares retired. Completion and the unspent balance are calculated here and
   carry their formula. Missing renders as —, never 0.

   Two distinctions the page must never blur:
     announced vs executed  — an authorisation is a ceiling, not spending.
     bought vs retired      — bought shares may sit in treasury for years;
                              retired shares are gone. Never summed. */
(function () {
  "use strict";

  var monthlyChart = null;
  var monthlyData = null;
  var state = { lifecycle: "", sort: "unspent_yen" };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function monthShort(iso) { return iso ? String(iso).slice(0, 7) : MISSING; }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function plural(n, one) { return count(n) + " " + one + (n === 1 ? "" : "s"); }
  function pct(v, dp) { return v == null ? MISSING : fmtNum(v, dp == null ? 1 : dp) + "%"; }

  /* Yen at the scale the reader thinks in, with the unit always attached.
     A buyback is a ¥bn-to-¥tn measure; printing raw yen would be unreadable
     and abbreviating without a unit would be worse. */
  function yenBn(v, dp) {
    if (v == null) return MISSING;
    return "¥" + fmtNum(v / 1e9, dp == null ? 1 : dp) + "bn";
  }
  function yenTn(v, dp) {
    if (v == null) return MISSING;
    return fmtNum(v / 1e12, dp == null ? 2 : dp);
  }
  function shares(v) { return v == null ? MISSING : fmtNum(v, 0); }
  /* The share count behind a yen figure, as hover text on its cell (never a
     second line). No tip when the count is missing. */
  function sharesTip(v) {
    return v == null ? "" : " title='" + fmtNum(v, 0) + " shares'";
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

  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    var link = href ? "<a href='" + href + "'>" + esc(primary) + "</a>" : esc(primary);
    return "<div class='cell-name'><div>" + link + "</div></div>";
  }

  /* The state a programme is in, in the reader's words. The full sentence is on
     the title attribute; a badge that wrapped would break the row's baseline. */
  var STATE_SHORT = {
    completed: "Completed",
    running: "Running",
    expired_unspent: "Closed unspent",
    awaiting_final: "Final report pending",
    unknown: "Not classifiable",
  };

  function stateBadge(row) {
    var key = row.lifecycle;
    var cls = "badge badge-state" + (key === "expired_unspent" ? " unspent" : "");
    return "<span class='" + cls + "' title='" + esc(row.lifecycle_label || "") + "'>" +
      esc(STATE_SHORT[key] || key) + "</span>";
  }

  /* The filing's own dates contradict each other. Shown, never corrected —
     and it also explains why one programme can appear twice. */
  function datesFlag(row) {
    if (!row.dates_inconsistent) return "";
    return " <span class='badge badge-state unspent' title='The filing dates the "
      + "resolution after the start of the period it authorises. Published as filed.'>"
      + "Dates conflict</span>";
  }

  function windowCell(row) {
    if (!row.window_start && !row.window_end) return MISSING;
    return esc(monthShort(row.window_start)) + " → " + esc(monthShort(row.window_end));
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected; try reloading.</p>";
  }

  // ---- market view ---------------------------------------------------------
  function renderStrip(s) {
    var span = monthShort(s.first_reporting_month) + " – " + monthShort(s.last_reporting_month);
    var closed = null;
    (s.lifecycle || []).forEach(function (r) {
      if (r.lifecycle === "expired_unspent") closed = r;
    });
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Authorised", "¥" + yenTn(s.authorised_yen) + '<span class="unit"> tn</span>',
           count(s.authorisations) + " authorisations · " + span) +
      cell("Actually bought", "¥" + yenTn(s.acquired_yen) + '<span class="unit"> tn</span>',
           "as filed, cumulative against those authorisations") +
      cell("Closed unspent", count(closed && closed.authorisations) +
           '<span class="unit"> programmes</span>',
           "¥" + yenTn(s.unspent_yen_expired) + "tn of authorisation left unbought when the " +
           "acquisition period ended (calculated)") +
      cell("Shares retired", fmtNum((s.shares_retired || 0) / 1e9, 2) +
           '<span class="unit"> bn shares</span>',
           "¥" + yenTn(s.retired_yen) + "tn cancelled outright by " +
           count(s.companies_retiring) + " companies") +
      cell("Coverage", count(s.filings) + '<span class="unit"> filings</span>',
           count(s.companies) + " companies · " + count(s.gate.clean) + " rows reconcile " +
           "against the filer's own progress figure, " + count(s.gate.not_reconcilable) +
           " publish none") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label" title="' + esc(label) + '">' +
        label + '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  function drawMonthly() {
    var d = monthlyData;
    // An edge month covered by a handful of filings is an edge of the archive,
    // not a quiet market. Plotting it as a near-zero bar would state something
    // the data does not: it is dropped, and said so under the chart.
    var full = d.months.filter(function (m) { return !m.partial_month; });
    var dropped = d.months.filter(function (m) { return m.partial_month; });
    var cats = full.map(function (m) { return monthShort(m.month); });
    var cfg = {
      categories: cats,
      series: [
        { name: "Bought (¥bn)", slot: 1,
          points: full.map(function (m) { return m.acquired_yen == null ? null : m.acquired_yen / 1e9; }) },
        { name: "Retired (¥bn)", slot: 2,
          points: full.map(function (m) { return m.retired_yen == null ? null : m.retired_yen / 1e9; }) },
      ],
      notes: full.map(function (m) { return count(m.filings) + " filings"; }),
      dp: 1,
      unitSuffix: "bn",
      yAxisName: "¥bn",
      trust: "official",
      sourceLine: sourceLine(),
    };
    if (monthlyChart) monthlyChart.dispose();
    monthlyChart = obsChart($("monthly-chart"), "cols", cfg);
    $("monthly-source").textContent = sourceLine();
    $("monthly-panel").textContent = cats.length + " reporting months";
    $("monthly-calc").innerHTML =
      esc("Bought is the sum of the shares acquired in the reporting month, as filed by every " +
          "company filing that month; retired is the sum of the 消却 row of the same filings. " +
          "Both are as filed and are summed, never netted against each other. ") +
      (dropped.length
        ? esc("Omitted: " + dropped.map(function (m) {
            return monthShort(m.month) + " (" + m.filings + " filings)";
          }).join(", ") + " — the edges of the archive, where the month is covered by a handful " +
          "of filings rather than the market. " + d.coverage_note)
        : esc(d.coverage_note));

    function sourceLine() {
      return "Source: 自己株券買付状況報告書 via EDINET · " + cats.length +
        " reporting months, " + (cats[0] || "") + "–" + (cats[cats.length - 1] || "") +
        " · Plover Analytics";
    }

    $("monthly-png").onclick = function () {
      monthlyChart.exportPNG("japan-buybacks-monthly.png");
    };
    $("monthly-csv").onclick = function () {
      csvDownload("japan-buybacks-monthly.csv",
        ["Plover Analytics — buybacks bought and retired, by reporting month",
         "Yen bought and yen retired are different acts and are never summed.",
         d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET, Financial Services Agency."],
        ["reporting_month", "filings", "acquired_yen", "acquired_shares",
         "retired_yen", "retired_shares", "retirements", "partial_month"],
        d.months.map(function (m) {
          return [monthShort(m.month), m.filings, m.acquired_yen, m.acquired_shares,
                  m.retired_yen, m.retired_shares, m.retirements, m.partial_month];
        }));
    };
  }

  function renderPrograms(d) {
    $("programs-count").textContent = d.total > d.count
      ? count(d.count) + " of " + plural(d.total, "authorisation")
      : plural(d.count, "authorisation");
    $("programs-meta").innerHTML = esc(d.lifecycle === "all"
      ? "Every authorisation in the archive, ranked."
      : (d.lifecycle_labels[d.lifecycle] || d.lifecycle) + ".");
    $("programs-table").innerHTML =
      "<thead><tr><th>Company</th><th>Resolved</th><th>Acquisition period</th>" +
      "<th class=r>Authorised</th><th class=r>Bought</th><th class=r>Completion</th>" +
      "<th class=r>Unspent</th><th>State</th></tr></thead><tbody>" +
      d.programs.map(function (r) {
        return "<tr><td>" + nameCell(r.name_en, r.filer_name,
                 r.sec_code ? "buyback.html?c=" + esc(r.sec_code) : null) +
          "</td><td class=nw>" + esc(r.resolution_date || MISSING) +
          "</td><td class=nw>" + windowCell(r) +
          "</td><td class=r>" + yenBn(r.authorised_yen) +
          "</td><td class=r title='" + plural(r.filings, "report") + "'>" + yenBn(r.cumulative_yen) +
          "</td><td class=r>" + pct(r.completion_pct) +
          "</td><td class=r>" + yenBn(r.unspent_yen) +
          "</td><td>" + stateBadge(r) + datesFlag(r) + "</td></tr>";
      }).join("") + "</tbody>";
    $("programs-formula").textContent =
      "Completion = " + d.calc.completion_pct + ". Unspent = " + d.calc.unspent_yen +
      ". Both are calculated here from the filed figures; the filer's own published " +
      "progress percentage is shown per month on the company page and is official as " +
      "filed. The two can differ in the last decimal because filers truncate or round. " +
      d.dates_note;
    $("programs-csv").onclick = function () {
      csvDownload("japan-buybacks-programmes.csv",
        ["Plover Analytics — buyback authorisations, announced versus executed",
         d.measure_note,
         "Completion and unspent are calculated: " + d.calc.completion_pct + "; " +
           d.calc.unspent_yen + ".",
         d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET, Financial Services Agency."],
        ["sec_code", "edinet_code", "company_en", "company_ja", "resolution_type",
         "resolution_date", "window_start", "window_end", "authorised_shares",
         "authorised_yen", "acquired_shares", "acquired_yen", "completion_pct",
         "unspent_yen", "lifecycle", "dates_inconsistent", "monthly_reports",
         "last_reporting_month", "last_doc_id"],
        d.programs.map(function (r) {
          return [r.sec_code, r.edinet_code, r.name_en, r.filer_name, r.resolution_type,
                  r.resolution_date, r.window_start, r.window_end, r.authorised_shares,
                  r.authorised_yen, r.cumulative_shares, r.cumulative_yen, r.completion_pct,
                  r.unspent_yen, r.lifecycle, r.dates_inconsistent, r.filings,
                  r.last_as_of, r.last_doc_id];
        }));
    };
  }

  function renderRetirements(d) {
    $("retire-count").textContent = d.total > d.count
      ? "largest " + count(d.count) + " of " + plural(d.total, "filing-month")
      : plural(d.count, "filing-month");
    $("retire-meta").textContent = d.retirement_note;
    $("retire-table").innerHTML =
      "<thead><tr><th>Company</th><th>Month</th><th class=r>Shares retired</th>" +
      "<th class=r>Value</th><th class=r>Share of shares before</th>" +
      "<th class=r>Shares outstanding after</th><th>Filing</th></tr></thead><tbody>" +
      d.retirements.map(function (r) {
        return "<tr><td>" + nameCell(r.name_en, r.filer_name,
                 r.sec_code ? "buyback.html?c=" + esc(r.sec_code) : null) +
          "</td><td>" + esc(monthShort(r.as_of)) +
          "</td><td class=r>" + shares(r.cancelled_shares) +
          "</td><td class=r>" + yenBn(r.cancelled_yen) +
          "</td><td class=r>" + pct(r.pct_of_pre_shares) +
          "</td><td class=r>" + shares(r.shares_outstanding) +
          "</td><td><span class='mono'>" + esc(r.doc_id) + "</span></td></tr>";
      }).join("") + "</tbody>";
    $("retire-formula").textContent = "Share of shares before = " + d.calc.pct_of_pre_shares;
    $("retire-csv").onclick = function () {
      csvDownload("japan-buybacks-retirements.csv",
        ["Plover Analytics — shares retired (消却)",
         d.retirement_note,
         "Share of shares before is calculated: " + d.calc.pct_of_pre_shares,
         d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET, Financial Services Agency."],
        ["sec_code", "edinet_code", "company_en", "company_ja", "reporting_month",
         "shares_retired", "value_yen", "shares_outstanding_after", "treasury_shares",
         "pct_of_pre_shares", "doc_id"],
        d.retirements.map(function (r) {
          return [r.sec_code, r.edinet_code, r.name_en, r.filer_name, r.as_of,
                  r.cancelled_shares, r.cancelled_yen, r.shares_outstanding,
                  r.treasury_shares, r.pct_of_pre_shares, r.doc_id];
        }));
    };
  }

  function loadPrograms() {
    syncUrl();
    getJSON("/api/v1/equity/buyback/programs?limit=50&sort=" +
            encodeURIComponent(state.sort) +
            (state.lifecycle ? "&lifecycle=" + encodeURIComponent(state.lifecycle) : ""))
      .then(renderPrograms)
      .catch(function (e) { errorInto("programs-meta", e); });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    if (state.lifecycle) p.set("state", state.lifecycle);
    if (state.sort !== "unspent_yen") p.set("sort", state.sort);
    var qs = p.toString();
    history.replaceState(null, "", "buyback.html" + (qs ? "?" + qs : ""));
  }

  function runSearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/buyback/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No company with a buyback " +
            "filing matches that. A company that ran no buyback in the covered window files " +
            "no monthly report, so it does not appear here.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-bb'><thead><tr><th>Company</th>" +
          "<th class=r>Code</th><th class=r>Monthly reports</th>" +
          "<th class=r>Latest month</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.filer_name,
                     c.sec_code ? "buyback.html?c=" + esc(c.sec_code) : null) + delistedTag(c) +
              "</td><td class=r><span class='mono'>" + esc(c.sec_code || MISSING) +
              "</span></td><td class=r>" + count(c.filings) +
              "</td><td class=r>" + esc(monthShort(c.last_reporting_month)) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  function initMarket() {
    // Read the view state out of the URL BEFORE anything is fetched, so the
    // screen that loads is the one the link described.
    var p = new URLSearchParams(location.search);
    if (p.get("state")) state.lifecycle = p.get("state");
    if (p.get("sort")) state.sort = p.get("sort");
    Array.prototype.forEach.call($("state-seg").querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-state") === state.lifecycle
        ? "true" : "false");
      b.addEventListener("click", function () {
        state.lifecycle = b.getAttribute("data-state");
        Array.prototype.forEach.call($("state-seg").querySelectorAll("button"), function (x) {
          x.setAttribute("aria-pressed", x === b ? "true" : "false");
        });
        loadPrograms();
      });
    });

    getJSON("/api/v1/equity/buyback/summary")
      .then(renderStrip).catch(function (e) { errorInto("stat-strip", e); });

    getJSON("/api/v1/equity/buyback/monthly").then(function (d) {
      monthlyData = d;
      drawMonthly();
    }).catch(function (e) { errorInto("monthly-source", e); });

    getJSON("/api/v1/equity/buyback/programs/sorts").then(function (d) {
      $("sort-select").innerHTML = d.sorts.map(function (s) {
        return "<option value='" + esc(s.key) + "'" +
          (s.key === state.sort ? " selected" : "") + ">" + esc(s.label) + "</option>";
      }).join("");
      $("sort-select").addEventListener("change", function () {
        state.sort = $("sort-select").value;
        loadPrograms();
      });
      loadPrograms();
    }).catch(function (e) { errorInto("programs-meta", e); });

    getJSON("/api/v1/equity/buyback/retirements?limit=50")
      .then(renderRetirements).catch(function (e) { errorInto("retire-meta", e); });

    var timer = null;
    $("q").addEventListener("input", function () {
      var v = $("q").value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { runSearch(v); }, 180);
    });
  }

  // ---- company view --------------------------------------------------------
  function renderCompany(d) {
    // Matches the served title for this address; see app/prerender.py.
    document.title = (d.name_en || d.filer_name) + " (" + (d.sec_code || "") +
      ") \u2014 Share Buybacks \u00b7 Plover Analytics";
    $("co-name").textContent = d.name_en || d.filer_name;
    $("co-code").textContent = d.sec_code || d.edinet_code;
    if (d.name_en && d.filer_name) {
      $("co-name-ja").textContent = d.filer_name;
      $("co-name-ja").hidden = false;
    }
    $("co-filing").innerHTML = count(d.filings) + " monthly reports · " +
      esc(monthShort(d.first_reporting_month)) + " – " + esc(monthShort(d.last_reporting_month)) +
      " · EDINET code <span class='mono'>" + esc(d.edinet_code) + "</span>";

    var latest = d.programs.length ? d.programs[0] : null;
    var treasury = d.treasury.length ? d.treasury[0] : null;
    var retired = d.treasury.reduce(function (a, r) { return a + (r.cancelled_shares || 0); }, 0);
    $("co-facts").innerHTML =
      fact("Authorisations", count(d.programs.length), "reported in the covered window") +
      fact("Latest authorised", latest ? yenBn(latest.authorised_yen) : MISSING,
           latest ? "resolved " + esc(latest.resolution_date || MISSING) : "") +
      fact("Latest bought", latest ? yenBn(latest.cumulative_yen) : MISSING,
           latest && latest.completion_pct != null
             ? pct(latest.completion_pct) + " of the authorisation (calculated)"
             : "no authorisation stated, so completion is not defined") +
      fact("Treasury holding", treasury ? pct(treasury.treasury_pct) : MISSING,
           treasury ? "of shares outstanding at " + esc(monthShort(treasury.month)) : "") +
      fact("Shares retired", retired ? shares(retired) : MISSING,
           retired ? "cancelled outright in the covered window" : "none in the covered window");

    function fact(label, value, qual) {
      return "<div><dt>" + esc(label) + "</dt><dd>" + value +
        (qual ? "<span class='qual'>" + qual + "</span>" : "") + "</dd></div>";
    }

    $("co-prog-count").textContent = plural(d.programs.length, "resolution");
    $("co-prog-table").innerHTML =
      "<thead><tr><th>Resolved</th><th>Acquisition period</th><th class=r>Authorised</th>" +
      "<th class=r>Bought</th><th class=r>Completion</th><th class=r>Unspent</th>" +
      "<th>State</th></tr></thead><tbody>" +
      d.programs.map(function (r) {
        return "<tr><td class=nw title='Resolved by " + (r.resolution_type === "agm"
            ? "the shareholder meeting" : "the board") + "'>" + esc(r.resolution_date || MISSING) +
          "</td><td class=nw>" + windowCell(r) +
          "</td><td class=r" + sharesTip(r.authorised_shares) + ">" + yenBn(r.authorised_yen) +
          "</td><td class=r" + sharesTip(r.cumulative_shares) + ">" + yenBn(r.cumulative_yen) +
          "</td><td class=r>" + pct(r.completion_pct) +
          "</td><td class=r>" + yenBn(r.unspent_yen) +
          "</td><td>" + stateBadge(r) + datesFlag(r) + "</td></tr>";
      }).join("") + "</tbody>";
    $("co-prog-csv").onclick = function () {
      csvDownload("buyback-programmes-" + (d.sec_code || d.edinet_code) + ".csv",
        ["Plover Analytics — buyback authorisations, " + (d.name_en || d.filer_name),
         d.measure_note, "Completion = " + d.calc.completion_pct + "; unspent = " +
           d.calc.unspent_yen + ".", d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET."],
        ["resolution_type", "resolution_date", "window_start", "window_end",
         "authorised_shares", "authorised_yen", "acquired_shares", "acquired_yen",
         "completion_pct", "unspent_yen", "lifecycle", "monthly_reports", "last_doc_id"],
        d.programs.map(function (r) {
          return [r.resolution_type, r.resolution_date, r.window_start, r.window_end,
                  r.authorised_shares, r.authorised_yen, r.cumulative_shares,
                  r.cumulative_yen, r.completion_pct, r.unspent_yen, r.lifecycle,
                  r.filings, r.last_doc_id];
        }));
    };

    $("co-month-count").textContent = plural(d.months.length, "monthly report");
    $("co-month-table").innerHTML =
      "<thead><tr><th>Month</th><th>Resolution</th><th class=r>Bought that month</th>" +
      "<th class=r>Cumulative</th><th class=r>Progress as filed</th>" +
      "<th>Reconciliation</th><th>Filing</th></tr></thead><tbody>" +
      d.months.map(function (r) {
        return "<tr><td class=nw>" + esc(monthShort(r.month)) +
          "</td><td class=nw>" + esc(r.resolution_date || MISSING) +
          "</td><td class=r" + sharesTip(r.month_shares) + ">" + yenBn(r.month_yen) +
          "</td><td class=r>" + yenBn(r.cumulative_yen) +
          "</td><td class=r>" + pct(r.progress_yen_pct, 2) +
          "</td><td>" + reconciliation(r.program_status) +
          "</td><td class=nw title='Filed " + esc(r.submitted || MISSING) + "'><span class='mono'>" +
          esc(r.doc_id) + "</span></td></tr>";
      }).join("") + "</tbody>";
    $("co-month-csv").onclick = function () {
      csvDownload("buyback-months-" + (d.sec_code || d.edinet_code) + ".csv",
        ["Plover Analytics — monthly buyback reports, " + (d.name_en || d.filer_name),
         "Progress is the filer's own published percentage, official as filed.",
         d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET."],
        ["reporting_month", "doc_id", "filed_date", "resolution_type", "resolution_date",
         "window_start", "window_end", "authorised_shares", "authorised_yen",
         "month_shares", "month_yen", "cumulative_shares", "cumulative_yen",
         "progress_shares_pct", "progress_yen_pct", "daily_execution_rows",
         "reconciliation"],
        d.months.map(function (r) {
          return [r.month, r.doc_id, r.submitted, r.resolution_type, r.resolution_date,
                  r.window_start, r.window_end, r.authorised_shares, r.authorised_yen,
                  r.month_shares, r.month_yen, r.cumulative_shares, r.cumulative_yen,
                  r.progress_shares_pct, r.progress_yen_pct, r.daily_rows, r.program_status];
        }));
    };

    $("co-treas-count").textContent = plural(d.treasury.length, "month");
    $("co-treas-table").innerHTML =
      "<thead><tr><th>Month</th><th class=r>Shares retired</th><th class=r>Value</th>" +
      "<th class=r>Treasury holding</th><th class=r>Shares outstanding</th>" +
      "<th class=r>Treasury share</th></tr></thead><tbody>" +
      d.treasury.map(function (r) {
        return "<tr><td>" + esc(monthShort(r.month)) +
          "</td><td class=r>" + (r.cancelled_shares ? shares(r.cancelled_shares) : MISSING) +
          "</td><td class=r>" + (r.cancelled_yen ? yenBn(r.cancelled_yen) : MISSING) +
          "</td><td class=r>" + shares(r.treasury_shares) +
          "</td><td class=r>" + shares(r.shares_outstanding) +
          "</td><td class=r>" + pct(r.treasury_pct) + "</td></tr>";
      }).join("") + "</tbody>";
    $("co-treas-csv").onclick = function () {
      csvDownload("buyback-treasury-" + (d.sec_code || d.edinet_code) + ".csv",
        ["Plover Analytics — treasury shares and retirements, " +
           (d.name_en || d.filer_name),
         d.retirement_note, d.coverage_note,
         "Official statistics as filed in 自己株券買付状況報告書 via EDINET."],
        ["reporting_month", "doc_id", "shares_retired", "value_yen", "treasury_shares",
         "shares_outstanding", "treasury_pct", "disposal_reconciliation"],
        d.treasury.map(function (r) {
          return [r.month, r.doc_id, r.cancelled_shares, r.cancelled_yen, r.treasury_shares,
                  r.shares_outstanding, r.treasury_pct, r.status];
        }));
    };

    $("co-note").innerHTML = esc(d.measure_note) + " " + esc(d.retirement_note) + " " +
      esc(d.coverage_note);
  }

  /* What the gate could and could not check for that filing. Never the raw
     status word: 'unverified' reads as doubt about the number, when it means
     the filer published nothing to reconcile it against. */
  function reconciliation(status) {
    var map = {
      clean: ["Reconciles", "The filer's published progress percentage was recomputed from " +
              "these rows and matched."],
      partial: ["Does not reconcile", "Recomputing the filer's own progress percentage from " +
                "these rows did not return its published figure. Published as filed."],
      unverified: ["Not reconcilable", "The filing published no progress percentage, so there " +
                   "was nothing to reconcile these figures against."],
    };
    var hit = map[status];
    if (!hit) return MISSING;
    return "<span class='badge badge-state" + (status === "partial" ? " unspent" : "") +
      "' title='" + esc(hit[1]) + "'>" + esc(hit[0]) + "</span>";
  }

  // ---- boot ----------------------------------------------------------------
  // The chart reads its palette from the CSS tokens at construction, so a theme
  // change has to redraw it — the tables and badges follow the stylesheet.
  initThemeToggle(function () { if (monthlyData) drawMonthly(); });

  var code = new URLSearchParams(location.search).get("c");
  if (code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    getJSON("/api/v1/equity/buyback/company/" + encodeURIComponent(code))
      .then(renderCompany)
      .catch(function (e) {
        $("co-name").textContent = code;
        $("co-filing").textContent = e.message.indexOf("404") > -1
          ? "No buyback filing for this company in the covered window. A company files a " +
            "monthly report only while a buyback programme is running."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else {
    initMarket();
  }
})();
