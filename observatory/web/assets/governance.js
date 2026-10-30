/* Boards & pay: market view (trend + screens) and per-company view.
   Data: /api/v1/equity/governance/*. Everything the filing states is official
   as filed; ages, ratios and per-head figures are calculated here and carry
   their formula. Missing renders as —, never 0. */
(function () {
  "use strict";

  var trendChart = null;
  var trendData = null;
  var screenState = { metric: "oldest_boards", listed: "true" };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function periodShort(iso) { return iso ? String(iso).slice(0, 7) : MISSING; }

  function yenM(v, dp) {
    if (v == null) return MISSING;
    return fmtNum(v / 1e6, dp == null ? 0 : dp);
  }
  function pct(v, dp) { return v == null ? MISSING : fmtNum(v, dp == null ? 1 : dp); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function ratioPct(v) { return v == null ? MISSING : fmtNum(v * 100, 1); }

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
    return "<div class='cell-item'><div class='en'>" + link + "</div></div>";
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected; try reloading.</p>";
  }

  // ---- market view ---------------------------------------------------------
  function renderStrip(s) {
    var asof = s.earliest_period_end && s.latest_period_end
      ? "FY ends " + periodShort(s.earliest_period_end) + " – " + periodShort(s.latest_period_end)
      : "latest filing per company";
    var st = s.extraction_status || {};
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Median board size", count(s.median_board_size) + '<span class="unit"> directors</span>',
           count(s.companies) + " listed companies · " + asof) +
      cell("Average director age", pct(s.avg_director_age) + '<span class="unit"> years</span>',
           pct(s.directors_70_plus_pct) + "% of board seats held by directors aged 70 or over (calculated)") +
      cell("Female officers", pct(s.avg_female_officer_pct) + '<span class="unit">%</span>',
           count(s.boards_with_no_women) + " boards with no women — ratio as filed, averaged across companies") +
      cell("Median pay per inside director", "¥" + yenM(s.median_inside_director_pay_yen, 1) + '<span class="unit"> m</span>',
           count(s.companies_with_inside_category) + " of " + count(s.companies_with_pay_table) +
           " companies use the standard category tag") +
      cell("Coverage", count(st.clean) + '<span class="unit"> clean filings</span>',
           count(st.partial) + " partial · " + count(st.no_tagged_board) + " no tagged board · " +
           count(st.unsupported_form) + " unsupported form — status disclosed per filing") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label">' + label +
        '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  // Each measure is its own unit, so the chart shows one at a time: a shared
  // axis across years and percentages would be a category error, and a second
  // y-axis invites exactly the false comparison it pretends to make.
  var MEASURES = {
    avg_director_age: { label: "Average director age", unit: "years", dp: 2, scale: 1,
      calc: "Mean across the panel of each company's average director age, itself the mean of (fiscal period end − date of birth) over the directors that company tags." },
    directors_70_plus_pct: { label: "Directors aged 70 or over", unit: "%", dp: 1, scale: 1,
      calc: "Board seats held by a director aged 70 or over ÷ all board seats in the panel × 100." },
    female_officer_pct: { label: "Female officers", unit: "%", dp: 1, scale: 1,
      calc: "Mean of each company's filed female officer ratio × 100. The ratio is as filed; in a committee-system company the filer's own officer count includes executive officers who are not individually disclosed." },
    median_pay_per_officer_yen: { label: "Median pay per officer", unit: "¥m", dp: 2, scale: 1e6,
      calc: "Median across the panel of (sum of the filing's category totals ÷ sum of the officers those categories cover). Totals are as filed; the division is ours. ‘Of which’ sub-rows are excluded so an officer is not counted twice." },
    median_employee_salary_yen: { label: "Median employee salary", unit: "¥m", dp: 2, scale: 1e6,
      calc: "Median across the panel of the filer's own 平均年間給与, as filed." },
  };

  function drawTrend() {
    var key = $("measure-select").value;
    var m = MEASURES[key];
    var points = trendData.series.map(function (r) {
      return [r.year, r[key] == null ? null : r[key] / m.scale];
    });
    var source = "Source: annual securities reports via EDINET · matched panel of " +
      count(trendData.panel_companies) + " listed companies, FY" + trendData.first_year +
      "–FY" + trendData.last_year + " · Plover Analytics";
    var cfg = {
      xType: "category",
      series: [{ name: m.label + (m.unit === "%" ? " (%)" : " (" + m.unit + ")"), slot: 1, points: points }],
      unit: m.unit === "%" ? "%" : "",
      unitSuffix: m.unit === "%" ? "" : m.unit,
      dp: m.dp,
      yAxisName: m.unit === "%" ? "%" : m.unit,
      trust: "official",
      sourceLine: source,
    };
    if (trendChart) trendChart.dispose();
    trendChart = obsChart($("trend-chart"), "line", cfg);
    $("trend-source").textContent = source;
    $("trend-calc").innerHTML = esc(m.calc) + " " + esc(trendData.panel_note);
    $("trend-png").onclick = function () {
      trendChart.exportPNG("japan-boards-" + key + ".png");
    };
    $("trend-csv").onclick = function () {
      csvDownload("japan-boards-panel.csv",
        ["Plover Analytics — boards and pay, matched panel",
         trendData.panel_note,
         "Scope: " + trendData.listed_scope,
         "Official statistics as filed; averages, ratios and per-head figures calculated by the platform.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["fiscal_year", "companies", "avg_director_age_years", "directors_70_plus_pct",
         "female_officer_pct", "median_board_size", "median_pay_per_officer_yen",
         "median_employee_salary_yen"],
        trendData.series.map(function (r) {
          return [r.year, r.companies, r.avg_director_age, r.directors_70_plus_pct,
                  r.female_officer_pct, r.median_board_size,
                  r.median_pay_per_officer_yen, r.median_employee_salary_yen];
        }));
    };
  }

  // Every screen shows the SAME columns and only changes what it sorts by, so a
  // company reads the same way whichever screen found it. The ranked column is
  // marked in the header rather than repeated as a second copy of itself; a
  // metric that is not already a column is appended as one.
  var SCREEN_BASE = [
    { key: "board_size", head: "Board", fmt: function (r) { return count(r.board_size); } },
    { key: "avg_director_age", head: "Avg age (years)", fmt: function (r) { return pct(r.avg_director_age); } },
    { key: "directors_70_plus", head: "Aged 70+", fmt: function (r) { return count(r.directors_70_plus); } },
    { key: "female_officers", head: "Women", fmt: function (r) { return count(r.female_officers); } },
    { key: "pay_category_total_yen", head: "Officer pay (¥m)", fmt: function (r) { return yenM(r.pay_category_total_yen); } },
    { key: "pay_per_officer_yen", head: "Per officer (¥m)", fmt: function (r) { return yenM(r.pay_per_officer_yen, 1); } },
    { key: "avg_salary_yen", head: "Avg salary (¥m)", fmt: function (r) { return yenM(r.avg_salary_yen, 2); } },
  ];
  var SCREEN_SORT_KEY = {
    oldest_boards: "avg_director_age", youngest_boards: "avg_director_age",
    oldest_directors: "directors_70_plus", no_women: "female_officers",
    most_female: "female_ratio_filed", largest_boards: "board_size",
    highest_paid_boards: "pay_category_total_yen",
    highest_pay_per_officer: "pay_per_officer_yen",
    highest_employee_pay: "avg_salary_yen",
    widest_gender_pay_gap: "gender_pay_gap_all",
  };
  var EXTRA_COL = {
    most_female: { key: "female_ratio_filed", head: "Female officers (%)",
      fmt: function (r) { return ratioPct(r.female_ratio_filed); } },
    widest_gender_pay_gap: { key: "gender_pay_gap_all", head: "Female-to-male wage ratio",
      fmt: function (r) { return r.gender_pay_gap_all == null ? MISSING : fmtNum(r.gender_pay_gap_all, 3); } },
  };

  function renderScreen(d) {
    var sortKey = SCREEN_SORT_KEY[d.metric];
    var cols = SCREEN_BASE.slice();
    if (EXTRA_COL[d.metric]) cols.splice(4, 0, EXTRA_COL[d.metric]);
    $("screen-count").textContent = d.rows.length + " companies · " + d.scope;
    $("screen-meta").innerHTML = esc(d.title) +
      ". Board size, gender counts and pay totals are <b>official statistics</b> as filed; " +
      "average age, ratios and pay per officer are calculated.";
    var head = "<thead><tr><th>Company</th><th>Sector</th>" +
      cols.map(function (c) {
        var ranked = c.key === sortKey;
        return "<th class=r" + (ranked ? " title='This screen is ranked by this column'" : "") +
          ">" + esc(c.head) + (ranked ? " ▾" : "") + "</th>";
      }).join("") + "<th class=r>FY</th></tr></thead>";
    var body = d.rows.map(function (r) {
      var flag = r.pay_consistency_flag
        ? " <span class='badge badge-warn' title='" + esc(r.pay_consistency_flag) + "'>check filing</span>"
        : "";
      return "<tr><td>" + nameCell(r.filer_name_en, r.filer_name,
          "governance.html?c=" + esc(r.sec_code)) +
        "</td><td>" + esc(r.industry_en || r.industry || MISSING) + "</td>" +
        cols.map(function (c) {
          return "<td class=r>" + c.fmt(r) +
            (c.key === "pay_per_officer_yen" ? flag : "") + "</td>";
        }).join("") +
        "<td class=r>" + esc(r.year) + "</td></tr>";
    }).join("");
    $("screen-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("screen-formula").innerHTML =
      "<b>Avg age</b> = mean of (fiscal period end − date of birth) over the directors the " +
      "filing tags. <b>Pay per officer</b> = sum of the filing's category totals ÷ the officers " +
      "they cover, excluding ‘of which’ sub-rows. " + esc(d.pay_consistency_note || "");
    $("screen-csv").onclick = function () {
      csvDownload("japan-boards-" + d.metric + ".csv",
        ["Plover Analytics — boards and pay screen: " + d.title,
         "Scope: " + d.scope,
         "Official statistics as filed; average age, ratios and pay per officer calculated.",
         d.pay_consistency_note || "",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["sec_code", "name", "name_en", "industry", "fiscal_year", "board_size",
         "avg_director_age_years", "directors_70_plus", "female_officers",
         "female_ratio_filed", "pay_category_total_yen", "pay_per_officer_yen",
         "avg_salary_yen", "gender_pay_gap_all", "extraction_status", "pay_consistency_flag"],
        d.rows.map(function (r) {
          return [r.sec_code, r.filer_name, r.filer_name_en, r.industry_en || r.industry,
                  r.year, r.board_size, r.avg_director_age, r.directors_70_plus,
                  r.female_officers, r.female_ratio_filed, r.pay_category_total_yen,
                  r.pay_per_officer_yen, r.avg_salary_yen, r.gender_pay_gap_all,
                  r.status, r.pay_consistency_flag];
        }));
    };
  }

  function loadScreen() {
    var url = "/api/v1/equity/governance/screen?metric=" +
      encodeURIComponent(screenState.metric) + "&limit=50" +
      (screenState.listed ? "&listed=true" : "");
    syncUrl();
    getJSON(url).then(renderScreen).catch(function (e) { errorInto("screen-meta", e); });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    p.set("screen", screenState.metric);
    if (!screenState.listed) p.set("listed", "all");
    if ($("measure-select").value !== "avg_director_age") p.set("m", $("measure-select").value);
    history.replaceState(null, "", "governance.html?" + p.toString());
  }

  function runSearch(q) {
    if (!q || q.length < 1) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/governance/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No company with an " +
            "extracted board matches that. A filing whose officers table is not tagged " +
            "in XBRL has no board here — the status is disclosed above.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-gov'><thead><tr><th>Company</th>" +
          "<th>Sector</th><th class=r>Board</th><th class=r>Avg age</th>" +
          "<th class=r>Disclosed individuals</th><th class=r>FY</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.name, "governance.html?c=" + esc(c.sec_code)) + delistedTag(c) +
              "</td><td>" + esc(c.industry || MISSING) +
              "</td><td class=r>" + count(c.board_size) +
              "</td><td class=r>" + pct(c.avg_director_age) +
              "</td><td class=r>" + count(c.named_count) +
              "</td><td class=r>" + esc(c.year) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  function initMarket() {
    // Read the view state out of the URL BEFORE anything is fetched. It happens
    // to work at the end too, since no promise can resolve first — but a reader
    // should not have to prove that to know which screen will load.
    var p = new URLSearchParams(location.search);
    if (p.get("m") && MEASURES[p.get("m")]) $("measure-select").value = p.get("m");
    if (p.get("screen")) screenState.metric = p.get("screen");
    if (p.get("listed") === "all") {
      screenState.listed = false;
      Array.prototype.forEach.call($("listed-seg").querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x.getAttribute("data-listed") === "" ? "true" : "false");
      });
    }

    getJSON("/api/v1/equity/governance/summary?listed=true")
      .then(renderStrip).catch(function (e) { errorInto("stat-strip", e); });

    getJSON("/api/v1/equity/governance/trend").then(function (d) {
      trendData = d;
      $("trend-panel").textContent = count(d.panel_companies) + " companies, same panel every year";
      drawTrend();
    }).catch(function (e) { errorInto("trend-source", e); });

    getJSON("/api/v1/equity/governance/screen/metrics").then(function (d) {
      $("screen-select").innerHTML = d.metrics.map(function (m) {
        return "<option value='" + esc(m.metric) + "'" +
          (m.metric === screenState.metric ? " selected" : "") + ">" + esc(m.title) + "</option>";
      }).join("");
      loadScreen();
    }).catch(function (e) { errorInto("screen-meta", e); });

    $("measure-select").onchange = function () { if (trendData) { drawTrend(); syncUrl(); } };
    $("screen-select").onchange = function () {
      screenState.metric = $("screen-select").value;
      loadScreen();
    };
    $("listed-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-listed]");
      if (!b) return;
      screenState.listed = b.getAttribute("data-listed") === "true";
      Array.prototype.forEach.call($("listed-seg").querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadScreen();
    });

    var t = null;
    $("q").addEventListener("input", function () {
      clearTimeout(t);
      var v = $("q").value.trim();
      t = setTimeout(function () { runSearch(v); }, 180);
    });

  }

  // ---- company view --------------------------------------------------------
  var ROLE_LABELS = {
    director: "Director",
    audit_committee_director: "Director (audit committee)",
    statutory_auditor: "Statutory auditor",
    other: "Other officer",
  };

  function renderCompany(d) {
    document.title = (d.filer_name_en || d.filer_name) + " — Boards & Pay · Plover Analytics";
    $("co-name").textContent = d.filer_name_en || d.filer_name;
    $("co-code").textContent = d.sec_code || "";
    $("co-industry").textContent = d.industry_en || d.industry || "";
    if (d.filer_name_en && d.filer_name) {
      $("co-name-ja").textContent = d.filer_name;
      $("co-name-ja").hidden = false;
    }
    $("co-filing").innerHTML = "Filing: <span class=mono>" + esc(d.doc_id) +
      "</span> · FY end " + periodShort(d.period_end) +
      " · filed " + esc(d.filed_date) +
      " · archived SHA-256 <span class=mono>" +
      (d.sha256 ? esc(String(d.sha256).slice(0, 12)) + "…" : MISSING) + "</span>" +
      (d.status !== "clean"
        ? " · <b>extraction status: " + esc(d.status) + "</b> — " + esc(d.detail || "") +
          " (disclosed, see methodology)"
        : "") +
      " · Official statistic — figures exactly as filed";

    // Missing is "—" on its own: "¥— m" reads as a number that failed to load
    // rather than a figure the filing does not state.
    function withUnit(v, text, unit) {
      return v == null ? MISSING : text + '<span class="unit"> ' + unit + "</span>";
    }
    $("co-facts").innerHTML =
      fact("Board size", count(d.board_size), d.officers_untagged
        ? count(d.officers_tagged) + " officers in the filer's own count — " +
          count(d.officers_untagged) + " not individually disclosed"
        : "directors tagged in the filing") +
      fact("Average age", withUnit(d.avg_director_age, pct(d.avg_director_age), "years"),
        count(d.directors_70_plus) + " aged 70 or over (calculated)") +
      fact("Female officers", d.female_ratio_filed == null ? MISSING
             : ratioPct(d.female_ratio_filed) + '<span class="unit">%</span>',
        count(d.female_officers) + " of " + count(d.officers_tagged || d.board_size) + ", as filed") +
      fact("Employees", count(d.employees_consolidated),
        (d.avg_salary_yen == null ? "average salary not stated"
          : "average salary ¥" + yenM(d.avg_salary_yen, 2) + "m at the filer (as filed)")) +
      fact("Officer pay, filed total",
        withUnit(d.pay_category_total_yen, "¥" + yenM(d.pay_category_total_yen), "m"),
        count(d.named_count) + " individuals disclosed separately on a consolidated basis");
    function fact(label, value, qual) {
      return "<div><dt>" + label + "</dt><dd>" + value +
        '<span class="qual">' + qual + "</span></dd></div>";
    }

    if (d.pay_consistency_flag) {
      $("co-flag").innerHTML = "<b>This filing's own pay figures contradict each other.</b> " +
        esc(d.pay_consistency_flag) + " " + esc(d.pay_consistency_note);
      $("co-flag").hidden = false;
    }

    // board
    $("board-count").textContent = d.board.length + " directors as at the filing date";
    $("board-table").innerHTML = "<thead><tr><th class=r>#</th><th>Director</th><th>Title, as filed</th>" +
      "<th>Role</th><th class=r>Age</th><th class=r>Shares held</th></tr></thead><tbody>" +
      d.board.map(function (p) {
        return "<tr><td class=r>" + p.seat_no + "</td><td>" +
          nameCell(p.name_en, p.name_ja, null) +
          (p.is_representative
            ? " <span class='badge badge-note' title='Title includes 代表 — a representative director'>representative</span>"
            : "") +
          "</td><td>" + esc(p.title_ja || MISSING) +
          "</td><td>" + esc(ROLE_LABELS[p.role] || "Other officer") +
          "</td><td class=r" + (p.date_of_birth ? " title='Born " + esc(p.date_of_birth) + "'" : "") + ">" +
          (p.age_at_period_end == null ? MISSING : p.age_at_period_end) +
          "</td><td class=r>" + count(p.shares_held) + "</td></tr>";
      }).join("") + "</tbody>";
    // The API's notes name their own fields, which is right for a machine
    // consumer and wrong on a page. Same facts, reader's words.
    $("board-note").innerHTML =
      "This table is the board (取締役会). " +
      (d.officers_untagged
        ? "This company's own officer count is <b>" + count(d.officers_tagged) +
          "</b> — " + count(d.officers_untagged) + " more than the directors named here, " +
          "because a company with a nominating committee also counts its executive officers " +
          "(執行役), whom the filing describes only in prose and does not tag individually. "
        : "") +
      "Director names in English are the company's own romanisation, taken from the filing " +
      "itself — not a translation by this platform. The Japanese name is always shown beside it.";
    $("board-csv").onclick = function () {
      csvDownload("board-" + d.sec_code + ".csv",
        ["Plover Analytics — board of " + (d.filer_name_en || d.filer_name),
         "Filing " + d.doc_id + ", FY end " + d.period_end + ", filed " + d.filed_date,
         "SHA-256 of the archived filing: " + (d.sha256 || ""),
         "Names, titles, dates of birth and shareholdings are official statistics as filed. " +
         "age_at_period_end = fiscal period end − date of birth, whole years (calculated).",
         d.names_note, d.board_note,
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["seat_no", "name_ja", "name_en", "title_ja", "role", "is_representative",
         "date_of_birth", "age_at_period_end", "shares_held"],
        d.board.map(function (p) {
          return [p.seat_no, p.name_ja, p.name_en, p.title_ja, ROLE_LABELS[p.role] || p.role,
                  p.is_representative, p.date_of_birth, p.age_at_period_end, p.shares_held];
        }));
    };

    // pay by category
    var pay = d.pay_by_category;
    $("pay-count").textContent = pay.length + " officer categories, as filed";
    $("pay-table").innerHTML = "<thead><tr><th>Officer category</th><th class=r>Officers paid</th>" +
      "<th class=r>Total (¥m)</th><th class=r>Per head (¥m)</th><th class=r>Fixed (¥m)</th>" +
      "<th class=r>Performance (¥m)</th><th class=r>Non-monetary (¥m)</th>" +
      "<th class=r>Other (¥m)</th><th>Components</th></tr></thead><tbody>" +
      pay.map(function (r) {
        var rec = r.components_reconcile == null
          ? "<span title='This row breaks out no components'>" + MISSING + "</span>"
          : (r.components_reconcile
              ? "<span class='badge badge-note' title='The filer&#39;s own components sum to its filed total'>adds up</span>"
              : "<span class='badge badge-warn' title='The filer&#39;s components do not sum to its filed total. The total is the published figure.'>does not add up</span>");
        var ofWhich = /^ofwhich/i.test(r.category_key || "");
        var label = esc(r.category_label_en || MISSING).replace(/([A-Za-z])(\d+)$/, "$1 $2");
        return "<tr><td" + (ofWhich
            ? " title='A subset of a category above, as the filer presents it — not an additional category. It is excluded from the filed total and from pay per officer so no officer is counted twice.'>Of which: "
            : r.category_label_source === "derived_from_filer_tag"
              ? " title='This filer defines its own officer category; the label is read from its own tag, not a published English name'>"
              : ">") + label +
          "</td><td class=r>" + count(r.headcount) +
          "</td><td class=r>" + yenM(r.total_yen) +
          "</td><td class=r>" + yenM(r.per_head_yen, 1) +
          "</td><td class=r>" + yenM(r.fixed_yen != null ? r.fixed_yen : r.base_yen) +
          "</td><td class=r>" + yenM(r.performance_yen != null ? r.performance_yen : r.bonus_yen) +
          "</td><td class=r>" + yenM(r.non_monetary_yen) +
          "</td><td class=r>" + yenM(r.other_components_yen) +
          "</td><td>" + rec + "</td></tr>";
      }).join("") + "</tbody>";
    // Reader's words, not the API's field names — same facts as components_note.
    $("pay-note").innerHTML =
      "<b>Read the total, not the sum of the parts.</b> The total (報酬等の総額) is what the " +
      "company filed and published. The components beside it are also as filed and often do " +
      "not add up to it: companies differ on whether non-monetary pay (非金銭報酬等) is a " +
      "component or a memo of what is already counted, figures are printed rounded to the " +
      "nearest million yen, and a forfeited share award can be negative. The last column says " +
      "whether this row's own components add up; where they do not, that is the filing, and " +
      "nothing here is corrected. Headcounts count officers <b>paid during the year</b>, " +
      "including anyone who left mid-year, so they legitimately differ from the board size above.";
    $("pay-csv").onclick = function () {
      csvDownload("officer-pay-" + d.sec_code + ".csv",
        ["Plover Analytics — officer remuneration of " + (d.filer_name_en || d.filer_name),
         "Filing " + d.doc_id + ", FY end " + d.period_end + ", filed " + d.filed_date,
         "All yen figures are official statistics as filed. per_head_yen = total ÷ officers paid (calculated).",
         d.components_note,
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["category_key", "category_label_en", "category_label_source", "headcount",
         "total_yen", "per_head_yen", "fixed_yen", "base_yen", "performance_yen",
         "bonus_yen", "non_monetary_yen", "retirement_yen", "other_components_yen",
         "other_components", "components_sum_yen", "components_reconcile"],
        pay.map(function (r) {
          return [r.category_key, r.category_label_en, r.category_label_source, r.headcount,
                  r.total_yen, r.per_head_yen, r.fixed_yen, r.base_yen, r.performance_yen,
                  r.bonus_yen, r.non_monetary_yen, r.retirement_yen, r.other_components_yen,
                  r.other_components, r.components_sum_yen, r.components_reconcile];
        }));
    };

    // named individuals
    if (d.pay_named.length) {
      $("named-sec").hidden = false;
      $("named-count").textContent = d.pay_named.length + " individuals";
      $("named-table").innerHTML = "<thead><tr><th>Officer</th><th class=r>Consolidated pay (¥m)</th>" +
        "<th>Basis</th><th>On this board</th></tr></thead><tbody>" +
        d.pay_named.map(function (p) {
          return "<tr><td>" + esc(p.name_en) +
            (p.voluntary_below_100m
              ? " <span class='badge badge-note' title='Below the ¥100m disclosure trigger — this company discloses it voluntarily'>voluntary</span>"
              : "") +
            "</td><td class=r>" + yenM(p.consolidated_pay_yen) +
            "</td><td>Group-wide, as filed" +
            "</td><td>" + (p.on_board_at_filing
              ? "Yes"
              : "<span title='An officer of a group company, or one who left during the year'>No — group officer</span>") +
            "</td></tr>";
        }).join("") + "</tbody>";
      $("named-note").innerHTML = "<b>A different basis from the table above.</b> " +
        "These figures are group-wide remuneration (連結報酬等) — pay from subsidiaries " +
        "included — while the table above is the company's own officer-category table. " +
        "People can appear here who do not sit on this board: an operating subsidiary's " +
        "chief executive is named in the parent's report. Never subtract one from the other " +
        "or divide one by the other." +
        (d.named_exceeds_category
          ? " <b>For this filing the named total exceeds the whole officer-category total</b> — " +
            "arithmetic proof that the two are measured differently."
          : "");
      $("named-csv").onclick = function () {
        csvDownload("named-pay-" + d.sec_code + ".csv",
          ["Plover Analytics — individuals disclosed by " + (d.filer_name_en || d.filer_name),
           "Filing " + d.doc_id + ", FY end " + d.period_end + ", filed " + d.filed_date,
           "Consolidated remuneration (連結報酬等), official statistics as filed.",
           d.consolidated_pay_note,
           "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
          ["name_en", "person_key", "pay_basis", "consolidated_pay_yen",
           "voluntary_below_100m", "on_board_at_filing"],
          d.pay_named.map(function (p) {
            return [p.name_en, p.person_key, p.pay_basis, p.consolidated_pay_yen,
                    p.voluntary_below_100m, p.on_board_at_filing];
          }));
      };
    }

    // five-year record
    getJSON("/api/v1/equity/governance/history?sec_code=" + encodeURIComponent(d.sec_code))
      .then(function (h) {
        $("hist-count").textContent = h.series.length + " filings";
        $("hist-table").innerHTML = "<thead><tr><th class=r>FY</th><th class=r>Board</th>" +
          "<th class=r>Avg age</th><th class=r>Aged 70+</th><th class=r>Women</th>" +
          "<th class=r>Pay per officer (¥m)</th><th class=r>Employees</th>" +
          "<th class=r>Avg salary (¥m)</th><th class=r>Individuals disclosed</th>" +
          "<th>Status</th></tr></thead><tbody>" +
          h.series.slice().reverse().map(function (r) {
            return "<tr><td class=r>" + esc(r.year) +
              "</td><td class=r>" + count(r.board_size) +
              "</td><td class=r>" + pct(r.avg_director_age) +
              "</td><td class=r>" + count(r.directors_70_plus) +
              "</td><td class=r>" + count(r.female_officers) +
              "</td><td class=r>" + yenM(r.pay_per_officer_yen, 1) +
              "</td><td class=r>" + count(r.employees_consolidated) +
              "</td><td class=r>" + yenM(r.avg_salary_yen, 2) +
              "</td><td class=r>" + count(r.named_count) +
              "</td><td>" + esc(r.status) + "</td></tr>";
          }).join("") + "</tbody>";
        $("hist-csv").onclick = function () {
          csvDownload("board-history-" + d.sec_code + ".csv",
            ["Plover Analytics — five-year board and pay record, " +
             (d.filer_name_en || d.filer_name),
             h.panel_note, h.consolidated_pay_note,
             "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
            ["fiscal_year", "period_end", "doc_id", "extraction_status", "board_size",
             "avg_director_age_years", "directors_70_plus", "female_officers",
             "female_ratio_filed", "pay_per_officer_yen", "employees_consolidated",
             "avg_salary_yen", "named_count", "named_sum_yen"],
            h.series.map(function (r) {
              return [r.year, r.period_end, r.doc_id, r.status, r.board_size,
                      r.avg_director_age, r.directors_70_plus, r.female_officers,
                      r.female_ratio_filed, r.pay_per_officer_yen, r.employees_consolidated,
                      r.avg_salary_yen, r.named_count, r.named_sum_yen];
            }));
        };
      })
      .catch(function (e) { errorInto("hist-count", e); });
  }

  // ---- boot ----------------------------------------------------------------
  // The chart reads its palette from the CSS tokens at construction, so a theme
  // change has to redraw it — the tables and badges follow the stylesheet on
  // their own.
  initThemeToggle(function () { if (trendData) drawTrend(); });

  var code = new URLSearchParams(location.search).get("c");
  if (code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    getJSON("/api/v1/equity/governance/company/" + encodeURIComponent(code))
      .then(renderCompany)
      .catch(function (e) {
        $("co-name").textContent = code;
        $("co-filing").textContent = e.message.indexOf("404") > -1
          ? "No extracted board or pay filing for this company. Its officers table may not be " +
            "tagged in XBRL, or its annual report is on a form that carries no governance " +
            "section — the status is disclosed on the market view."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else {
    initMarket();
  }
})();
