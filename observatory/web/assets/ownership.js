/* Shareholder register: market view (holder ranking + screens), per-company
   view (the register, the investor-type split, the year-by-year record) and a
   holder view (every register one holder appears in).
   Data: /api/v1/equity/ownership/*. Names, share counts and percentages are
   official as filed; holder type, nominee totals and averages are calculated
   here and carry their formula. Missing renders as —, never 0. */
(function () {
  "use strict";

  var mixChart = null;
  var company = null;
  var holderState = { nominees: "" };
  var screenState = { metric: "foreign_pct", order: "desc" };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function periodShort(iso) { return iso ? String(iso).slice(0, 7) : MISSING; }
  function pct(v, dp) { return v == null ? MISSING : fmtNum(v, dp == null ? 2 : dp); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }

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

  // A holder kind is our classification, so it is labelled in plain English and
  // never shown as the raw slug the API carries.
  var KIND_LABEL = {
    entity: "Company",
    individual: "Individual",
    trust_bank_nominee: "Nominee (trust bank)",
    foreign_nominee: "Nominee (custodian)",
    retirement_benefit_trust: "Pension trust",
    employee_association: "Employee association",
    foreign_entity: "Overseas holder",
    treasury: "Treasury shares",
  };
  var NOMINEE_KINDS = { trust_bank_nominee: 1, foreign_nominee: 1 };

  function kindBadge(kind) {
    var label = KIND_LABEL[kind] || kind || MISSING;
    return "<span class='badge " + (NOMINEE_KINDS[kind] ? "badge-nominee" : "badge-note") +
      "'>" + esc(label) + "</span>";
  }

  var CATEGORY_ORDER = ["individuals_and_others", "foreign_institutions",
    "financial_institutions", "other_corporations", "financial_service_providers",
    "foreign_individuals", "government"];
  // Axis labels only. The filing's own category names are long enough to
  // collide under a column chart, and the table below the chart carries them
  // in full, so the axis gets the short form and nothing is lost.
  var CATEGORY_SHORT = {
    individuals_and_others: "Individuals",
    foreign_institutions: "Foreign inst.",
    financial_institutions: "Banks & insurers",
    other_corporations: "Companies",
    financial_service_providers: "Brokers",
    foreign_individuals: "Foreign indiv.",
    government: "Government",
  };

  // ---- market view ---------------------------------------------------------
  function renderStrip(s) {
    var asof = s.earliest_period_end && s.latest_period_end
      ? "FY ends " + periodShort(s.earliest_period_end) + " – " + periodShort(s.latest_period_end)
      : "latest filing per company";
    var st = s.extraction_status || {};
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Companies with a register", count(s.companies) + '<span class="unit"> filings</span>',
           count(s.register_rows) + " named holders · " + asof) +
      cell("Foreign ownership", pct(s.avg_foreign_pct, 1) + '<span class="unit">%</span>',
           "Average across companies of 外国法人等 as filed (institutions and individuals)") +
      cell("Held through nominees", pct(s.avg_nominee_pct, 1) + '<span class="unit">%</span>',
           "Average share of the register's named holders sitting in custody accounts — held for others, not owned") +
      cell("Individual shareholders", pct(s.avg_individuals_pct, 1) + '<span class="unit">%</span>',
           "Average 個人その他 share of all issued shares, as filed") +
      cell("Coverage", count(st.clean) + '<span class="unit"> clean filings</span>',
           count(st.partial) + " partial · " + count(st.unsupported_form) +
           " on forms with no register — status disclosed per filing") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label">' + label +
        '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  function renderHolders(d) {
    var rows = d.holders;
    $("holders-count").textContent = rows.length + " holders · " + d.scope;
    $("holders-meta").innerHTML = d.nominees_included
      ? "Nominee and custodian accounts <b>included</b>. The two Japanese custody banks " +
        "appear at the top of most registers in Japan holding for index funds and pension " +
        "money they do not own — read this ranking as custody, not ownership."
      : "Nominee and custodian accounts <b>excluded</b>, so every row is a holder in its own name.";
    $("holders-table").innerHTML =
      "<thead><tr><th>Holder</th><th>Type</th><th class=r>Top-ten seats</th>" +
      "<th class=r>Largest holder</th><th class=r>Average stake (%)</th>" +
      "<th class=r>Largest stake (%)</th></tr></thead><tbody>" +
      rows.map(function (r) {
        var href = r.holder_edinet_code
          ? "ownership.html?h=" + encodeURIComponent(r.holder_edinet_code) : null;
        return "<tr><td>" + nameCell(r.name_en, r.name_ja, href) +
          "</td><td>" + kindBadge(r.holder_kind) +
          "</td><td class=r>" + count(r.top_ten_seats) +
          "</td><td class=r>" + count(r.largest_holder_seats) +
          "</td><td class=r>" + pct(r.avg_ratio_pct) +
          "</td><td class=r>" + pct(r.max_ratio_pct) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("holders-formula").textContent =
      "Top-ten seats counts the registers this holder appears in, one filing per company. " +
      "Average and largest stake are of the filed percentages (of shares in issue excluding " +
      "treasury). Both are calculated by the platform from figures as filed.";
    $("holders-csv").onclick = function () {
      csvDownload("japan-register-holders.csv",
        ["Plover Analytics — holders appearing in Japanese shareholder registers",
         "Scope: " + d.scope + (d.nominees_included ? "" : "; nominee and custodian accounts excluded"),
         "Names, share counts and percentages as filed in 大株主の状況; seat counts and averages calculated.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["holder_name_ja", "holder_name_en", "holder_kind", "edinet_code", "sec_code",
         "top_ten_seats", "largest_holder_seats", "avg_ratio_pct", "max_ratio_pct"],
        rows.map(function (r) {
          return [r.name_ja, r.name_en, r.holder_kind, r.holder_edinet_code,
                  r.holder_sec_code, r.top_ten_seats, r.largest_holder_seats,
                  r.avg_ratio_pct, r.max_ratio_pct];
        }));
    };
  }

  var SCREEN_LABEL = {
    foreign_pct: "Foreign ownership (%)",
    individuals_pct: "Individual shareholders (%)",
    financial_institutions_pct: "Banks and insurers (%)",
    other_corporations_pct: "Other companies (%)",
    securities_firms_pct: "Securities firms (%)",
    nominee_ratio_pct: "Held through nominees (%)",
    top_holders_pct: "Top holders combined (%)",
    shareholders_total: "Number of shareholders",
  };

  function renderScreen(d) {
    var rows = d.companies;
    $("screen-count").textContent = rows.length + " companies · " + d.scope;
    $("screen-meta").innerHTML = "Ranked by <b>" + esc(SCREEN_LABEL[d.metric] || d.metric) +
      "</b>. Every percentage is as filed; the ranking is ours.";
    var isCount = d.metric === "shareholders_total";
    $("screen-table").innerHTML =
      "<thead><tr><th>Company</th><th class=r>Foreign</th><th class=r>Individuals</th>" +
      "<th class=r>Banks &amp; insurers</th><th class=r>Other companies</th>" +
      "<th class=r>Nominees</th><th class=r>Top holders</th><th class=r>Shareholders</th>" +
      "<th class=r>FY</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td>" + nameCell(r.filer_name_en, r.filer_name,
                 "ownership.html?c=" + esc(r.sec_code)) +
          "</td><td class=r>" + pct(r.foreign_pct, 1) +
          "</td><td class=r>" + pct(r.individuals_pct, 1) +
          "</td><td class=r>" + pct(r.financial_institutions_pct, 1) +
          "</td><td class=r>" + pct(r.other_corporations_pct, 1) +
          "</td><td class=r>" + pct(r.nominee_ratio_pct, 1) +
          "</td><td class=r>" + pct(r.top_holders_pct, 1) +
          "</td><td class=r>" + count(r.shareholders_total) +
          "</td><td class=r>" + esc(String(r.period_end || "").slice(0, 4)) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("screen-formula").textContent = isCount
      ? "Shareholder counts are the filer's own 株主数, as filed."
      : "Percentages are the filer's own 所有株式数の割合, as filed, except " +
        "'Nominees', which sums the register rows this platform classifies as " +
        "custody accounts, and 'Top holders', which is the filing's own 計 row.";
    $("screen-csv").onclick = function () {
      csvDownload("japan-register-screen-" + d.metric + ".csv",
        ["Plover Analytics — shareholder register screen",
         "Ranked by " + (SCREEN_LABEL[d.metric] || d.metric) + ", " + d.order + "; scope: " + d.scope,
         "Percentages as filed in 所有者別状況 and 大株主の状況; nominee totals classified by the platform.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["sec_code", "name_ja", "name_en", "period_end", "foreign_pct", "individuals_pct",
         "financial_institutions_pct", "other_corporations_pct", "securities_firms_pct",
         "nominee_ratio_pct", "top_holders_pct", "shareholders_total", "doc_id", "status"],
        rows.map(function (r) {
          return [r.sec_code, r.filer_name, r.filer_name_en, r.period_end, r.foreign_pct,
                  r.individuals_pct, r.financial_institutions_pct, r.other_corporations_pct,
                  r.securities_firms_pct, r.nominee_ratio_pct, r.top_holders_pct,
                  r.shareholders_total, r.doc_id, r.status];
        }));
    };
  }

  function loadHolders() {
    getJSON("/api/v1/equity/ownership/holders?limit=40" +
            (holderState.nominees ? "&include_nominees=true" : ""))
      .then(renderHolders).catch(function (e) { errorInto("holders-meta", e); });
  }

  function loadScreen() {
    syncUrl();
    getJSON("/api/v1/equity/ownership/screen?limit=50&listed=true&metric=" +
            encodeURIComponent(screenState.metric) + "&order=" + screenState.order)
      .then(renderScreen).catch(function (e) { errorInto("screen-meta", e); });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    p.set("screen", screenState.metric);
    if (screenState.order !== "desc") p.set("order", screenState.order);
    if (holderState.nominees) p.set("nominees", "all");
    history.replaceState(null, "", "ownership.html?" + p.toString());
  }

  function runSearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/ownership/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No company with an " +
            "extracted register matches that. A filing on a form that carries no " +
            "株式等の状況 section has no register here — the status is disclosed above.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-own'><thead><tr><th>Company</th>" +
          "<th>Sector</th><th class=r>Named holders</th><th class=r>Top holders (%)</th>" +
          "<th class=r>Foreign (%)</th><th class=r>FY</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.name, "ownership.html?c=" + esc(c.sec_code)) +
              "</td><td>" + esc(c.industry || MISSING) +
              "</td><td class=r>" + count(c.majors_rows) +
              "</td><td class=r>" + pct(c.majors_ratio_filed_pct, 1) +
              "</td><td class=r>" + pct(c.foreign_pct, 1) +
              "</td><td class=r>" + esc(c.year) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  // ---- company view --------------------------------------------------------
  function renderCompany(d) {
    company = d;
    var name = d.filer_name_en || d.filer_name;
    // The server's title already names this company, spelled for reading: keep it.
    if (!d.sec_code || document.title.indexOf("(" + d.sec_code + ")") < 0) {
      document.title = name + (d.sec_code ? " (" + d.sec_code + ")" : "") +
        " · Major Shareholders · Plover Analytics";
    }
    $("co-name").textContent = name;
    $("co-code").textContent = d.sec_code || d.edinet_code || "";
    if (d.filer_name_en && d.filer_name) {
      $("co-name-ja").textContent = d.filer_name;
      $("co-name-ja").hidden = false;
    }
    $("co-filing").innerHTML =
      "Fiscal year ended " + esc(fmtPeriodLong(d.period_end) || MISSING) +
      " · filed " + esc(d.filed_date || MISSING) +
      " · <span class='badge badge-official'>Official statistic</span>" +
      " · <a href='https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?" +
      "S100=" + esc(d.doc_id) + "'>filing " + esc(d.doc_id) + "</a>" +
      " · <span class='mono' title='SHA-256 of the archived filing'>" +
      esc(String(d.sha256 || "").slice(0, 12)) + "…</span>";

    var nomineeShare = d.nominee_ratio_pct;
    $("co-facts").innerHTML =
      fact("Top holders combined", pct(d.majors_ratio_filed_pct, 2), "%",
           "The filing's own 計 row over " + count(d.majors_rows) + " named holders") +
      fact("Foreign ownership", pct(d.foreign_pct, 2), "%",
           "外国法人等, institutions and individuals, of all issued shares") +
      fact("Held through nominees", pct(nomineeShare, 2), "%",
           "Of the named holders — held for others, not owned (calculated)") +
      fact("Shareholders", count(d.shareholders_total), "",
           "On the register at the fiscal year end, as filed") +
      fact("Shares in issue", count(d.issued_shares), "",
           d.treasury_shares == null ? "As filed at the filing date"
             : count(d.treasury_shares) + " held in treasury, as filed");
    if (d.status === "partial" && d.detail) {
      $("co-flag").innerHTML = "<b>This filing did not pass every check.</b> " +
        esc(d.detail) + ". The figures below are published exactly as filed and are " +
        "never corrected here.";
      $("co-flag").hidden = false;
    }
    renderRegister(d);
    renderMix(d);
    renderHistory(d);

    function fact(label, value, unit, qual) {
      return "<div><dt>" + label + "</dt><dd>" + value +
        (unit ? "<span class='unit'>" + unit + "</span>" : "") +
        "<span class='qual'>" + qual + "</span></dd></div>";
    }
  }

  function renderRegister(d) {
    var rows = d.holders || [];
    var nominees = rows.filter(function (r) { return NOMINEE_KINDS[r.holder_kind]; });
    $("reg-count").textContent = rows.length + " named holders";
    // Running total down the register. It is only a number in the filing's own
    // rank order — which is why this table opts out of click-to-sort — and it
    // is DERIVED, so it carries its formula under the table. A row the filing
    // leaves without a percentage does not advance the total and shows —,
    // rather than repeating the line above and implying a zero.
    var cumulative = 0;
    $("reg-table").innerHTML =
      "<thead><tr><th class=rank>#</th><th>Holder</th><th>Type</th>" +
      "<th class=r>Shares</th><th class=r>Stake (%)</th>" +
      "<th class=r>Cumulative (%)</th><th>Address as filed</th>" +
      "</tr></thead><tbody>" +
      rows.map(function (r) {
        var href = r.holder_sec_code ? "ownership.html?c=" + esc(r.holder_sec_code)
                 : (r.holder_edinet_code ? "ownership.html?h=" + esc(r.holder_edinet_code) : null);
        // The account a holding sits in is hover text, not a second line.
        var sub = "";
        if (r.account_raw) sub = "Account: " + r.account_raw;
        if (r.beneficiary_raw) sub = "Pension trust of " + r.beneficiary_raw;
        if (r.ratio_pct != null) cumulative += r.ratio_pct;
        return "<tr><td class=rank>" + count(r.rank) + "</td><td" +
          (sub ? " title='" + esc(sub) + "'" : "") + ">" +
          nameCell(r.holder_name_en, r.name_raw, href) +
          "</td><td>" + kindBadge(r.holder_kind) +
          "</td><td class=r>" + count(r.shares) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class='r cum'>" + (r.ratio_pct == null ? MISSING : pct(cumulative)) +
          "</td><td>" + esc(r.address_raw || MISSING) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("reg-formula").textContent = rows.length
      ? "Cumulative is the running sum of the filed percentages down the "
        + "register, calculated here — " + pct(cumulative) + "% over "
        + rows.length + " named holders against the filing's own 計 row of "
        + pct(d.majors_ratio_filed_pct) + "%. It includes custody accounts, "
        + "which hold for others. Each percentage is of shares in issue "
        + "excluding treasury, so they share a denominator and do add up."
      : "";
    $("reg-note").innerHTML = nominees.length
      ? "<b>" + nominees.length + " of these rows are custody accounts</b> holding for " +
        "someone else — " + pct(d.nominee_ratio_pct, 2) + "% of the company between them. " +
        "The register cannot say who the beneficial owners are; no public Japanese filing does."
      : "No custody accounts appear in this register, so every row is a holder in its own name.";
    $("reg-csv").onclick = function () {
      csvDownload("register-" + (d.sec_code || d.doc_id) + ".csv",
        ["Plover Analytics — shareholder register (大株主の状況)",
         (d.filer_name_en || "") + " (" + d.filer_name + "), securities code " + (d.sec_code || ""),
         "Fiscal year ended " + d.period_end + "; filed " + d.filed_date + "; filing " + d.doc_id,
         "Official statistics exactly as filed. Stake % is of shares in issue excluding treasury.",
         "cumulative_ratio_pct is calculated: the running sum of ratio_pct down the register.",
         "holder_kind and beneficiary are classified by the platform from the filed name.",
         "SHA-256 of archived filing: " + (d.sha256 || ""),
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["rank", "holder_name_ja", "holder_name_en", "account", "holder_kind",
         "beneficiary", "shares", "ratio_pct", "cumulative_ratio_pct", "address",
         "holder_edinet_code", "holder_sec_code", "match_status"],
        (function () {
          var run = 0;
          return rows.map(function (r) {
            if (r.ratio_pct != null) run += r.ratio_pct;
            return [r.rank, r.name_raw, r.holder_name_en, r.account_raw, r.holder_kind,
                    r.beneficiary_raw, r.shares, r.ratio_pct,
                    r.ratio_pct == null ? null : Math.round(run * 100) / 100,
                    r.address_raw, r.holder_edinet_code, r.holder_sec_code,
                    r.match_status];
          });
        })());
    };
  }

  function renderMix(d) {
    var cats = (d.categories || []).filter(function (c) {
      return c.share_class === (d.categories[0] || {}).share_class;
    });
    var byKey = {};
    cats.forEach(function (c) { byKey[c.category] = c; });
    var ordered = CATEGORY_ORDER.filter(function (k) { return byKey[k]; })
      .map(function (k) { return byKey[k]; });
    $("mix-count").textContent = ordered.length
      ? ordered.length + " investor categories" : "not tagged in this filing";
    if (!ordered.length) {
      $("mix-chart").innerHTML = "<p class='sec-note'>This filing does not tag the " +
        "所有者別状況 table, so the whole-register split is not available for it.</p>";
      $("mix-table").innerHTML = "";
      return;
    }
    var source = "Source: " + (d.filer_name_en || d.filer_name) +
      " annual securities report, FY ended " + (d.period_end || "") +
      " · via EDINET · Plover Analytics";
    // Composition of one total at one point in time: columns on a zero
    // baseline, never a pie.
    var cfg = {
      categories: ordered.map(function (c) {
        return CATEGORY_SHORT[c.category] || c.category_en;
      }),
      series: [{ name: "Share of issued shares", slot: 1,
                 points: ordered.map(function (c) { return c.pct; }) }],
      dp: 2, unitSuffix: "%", yAxisName: "% of issued shares",
      trust: "official", sourceLine: source,
    };
    if (mixChart) mixChart.dispose();
    mixChart = obsChart($("mix-chart"), "cols", cfg);
    $("mix-source").textContent = source;
    $("mix-png").onclick = function () {
      mixChart.exportPNG("register-mix-" + (d.sec_code || d.doc_id) + ".png");
    };
    $("mix-table").innerHTML =
      "<thead><tr><th>Investor type</th><th class=r>Shareholders</th>" +
      "<th class=r>Units held</th><th class=r>Share of issued (%)</th></tr></thead><tbody>" +
      ordered.map(function (c) {
        return "<tr><td>" + esc(c.category_en) + "</td><td class=r>" + count(c.shareholders) +
          "</td><td class=r>" + count(c.units) + "</td><td class=r>" + pct(c.pct) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("mix-csv").onclick = function () {
      csvDownload("register-mix-" + (d.sec_code || d.doc_id) + ".csv",
        ["Plover Analytics — register by investor category (所有者別状況)",
         (d.filer_name_en || "") + " (" + d.filer_name + "), securities code " + (d.sec_code || ""),
         "Fiscal year ended " + d.period_end + "; filing " + d.doc_id,
         "Official statistics exactly as filed. Percentages are of ALL issued shares — a " +
         "different denominator from the named-holder table, which excludes treasury.",
         "One unit is the company's trading unit (単元), typically 100 shares.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["share_class", "category", "category_en", "shareholders", "units", "pct_of_issued"],
        ordered.map(function (c) {
          return [c.share_class, c.category, c.category_en, c.shareholders, c.units, c.pct];
        }));
    };
  }

  function renderHistory(d) {
    var rows = d.history || [];
    $("hist-count").textContent = rows.length + " filings archived";
    $("hist-table").innerHTML =
      "<thead><tr><th>Fiscal year</th><th class=r>Top holders (%)</th>" +
      "<th class=r>Nominees (%)</th><th class=r>Foreign (%)</th>" +
      "<th class=r>Individuals (%)</th><th class=r>Banks &amp; insurers (%)</th>" +
      "<th class=r>Other companies (%)</th><th class=r>Shareholders</th>" +
      "<th>Filing</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td>" + esc(periodShort(r.period_end)) +
          "</td><td class=r>" + pct(r.majors_ratio_filed_pct) +
          "</td><td class=r>" + pct(r.nominee_ratio_pct) +
          "</td><td class=r>" + pct(r.foreign_pct) +
          "</td><td class=r>" + pct(r.individuals_pct) +
          "</td><td class=r>" + pct(r.financial_institutions_pct) +
          "</td><td class=r>" + pct(r.other_corporations_pct) +
          "</td><td class=r>" + count(r.shareholders_total) +
          "</td><td>" + esc(r.doc_id) + (r.status === "partial" ?
             " <span class='badge badge-note'>Partial</span>" : "") + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("hist-csv").onclick = function () {
      csvDownload("register-history-" + (d.sec_code || d.doc_id) + ".csv",
        ["Plover Analytics — shareholder register, year by year",
         (d.filer_name_en || "") + " (" + d.filer_name + "), securities code " + (d.sec_code || ""),
         "One row per archived annual report. Percentages as filed; nominee share calculated.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["period_end", "top_holders_pct", "nominee_pct", "foreign_pct", "individuals_pct",
         "financial_institutions_pct", "other_corporations_pct", "shareholders_total",
         "doc_id", "status"],
        rows.map(function (r) {
          return [r.period_end, r.majors_ratio_filed_pct, r.nominee_ratio_pct, r.foreign_pct,
                  r.individuals_pct, r.financial_institutions_pct, r.other_corporations_pct,
                  r.shareholders_total, r.doc_id, r.status];
        }));
    };
  }

  // ---- holder view ---------------------------------------------------------
  function renderHolder(d) {
    var rows = d.positions || [];
    var name = d.name_en || d.name || (rows.length ? rows[0].held_as : d.key) || d.key;
    document.title = name + " · Shareholder Register · Plover Analytics";
    $("ho-name").textContent = name;
    $("ho-code").textContent = d.key;
    $("ho-meta").innerHTML = "Appears in " + count(d.companies) +
      " top-ten registers · average stake " + pct(d.avg_ratio_pct) + "% · " + esc(d.scope) +
      " · <span class='badge badge-official'>Official statistic</span>";
    $("ho-count").textContent = rows.length < d.companies
      ? rows.length + " of " + count(d.companies) + " shown" : rows.length + " companies";
    $("ho-note").innerHTML = esc(d.reverse_note) +
      " The rank this holder occupies in each register, and the name that " +
      "register prints for it, are on the row and in the CSV.";
    // Four columns, because four are what a reader ranks on. The register
    // rank and the name the filer actually printed are provenance, not
    // ranking material: they ride on the row's tooltip and in the CSV, so the
    // match behind every row stays checkable without a column that nobody
    // sorts by.
    $("ho-table").innerHTML =
      "<thead><tr><th>Company</th><th class=r>Shares</th>" +
      "<th class=r>Stake (%)</th><th class=r>FY</th></tr></thead><tbody>" +
      rows.map(function (r) {
        var provenance = "Rank " + (r.rank == null ? MISSING : r.rank) +
          " in this register · named as " + (r.held_as || MISSING);
        return "<tr title='" + esc(provenance) + "'><td>" +
          nameCell(r.company_name_en, r.filer_name,
                   r.sec_code ? "ownership.html?c=" + esc(r.sec_code) : null) +
          "</td><td class=r>" + count(r.shares) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class=r>" + esc(periodShort(r.period_end)) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("ho-csv").onclick = function () {
      csvDownload("register-holder-" + d.key + ".csv",
        ["Plover Analytics — one holder's appearances in Japanese registers",
         "Holder: " + name + " (" + d.key + ")",
         d.reverse_note,
         "Share counts and percentages exactly as filed in each company's 大株主の状況.",
         "Source: 有価証券報告書 via EDINET, Financial Services Agency."],
        ["sec_code", "company_ja", "company_en", "period_end", "rank", "shares",
         "ratio_pct", "named_as", "doc_id"],
        rows.map(function (r) {
          return [r.sec_code, r.filer_name, r.company_name_en, r.period_end, r.rank,
                  r.shares, r.ratio_pct, r.held_as, r.doc_id];
        }));
    };
  }

  // ---- boot ----------------------------------------------------------------
  function initMarket() {
    var p = new URLSearchParams(location.search);
    if (p.get("screen") && SCREEN_LABEL[p.get("screen")]) screenState.metric = p.get("screen");
    if (p.get("order") === "asc") {
      screenState.order = "asc";
      Array.prototype.forEach.call($("order-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-order") === "asc" ? "true" : "false");
      });
    }
    if (p.get("nominees") === "all") {
      holderState.nominees = "true";
      Array.prototype.forEach.call($("nominee-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-nominees") ? "true" : "false");
      });
    }

    getJSON("/api/v1/equity/ownership/summary")
      .then(renderStrip).catch(function (e) { errorInto("stat-strip", e); });

    getJSON("/api/v1/equity/ownership/screen/metrics").then(function (d) {
      $("screen-select").innerHTML = d.metrics.map(function (m) {
        return "<option value='" + esc(m) + "'" +
          (m === screenState.metric ? " selected" : "") + ">" +
          esc(SCREEN_LABEL[m] || m) + "</option>";
      }).join("");
      loadScreen();
    }).catch(function (e) { errorInto("screen-meta", e); });

    loadHolders();

    $("screen-select").onchange = function () {
      screenState.metric = this.value;
      loadScreen();
    };
    $("order-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      screenState.order = b.getAttribute("data-order");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadScreen();
    });
    $("nominee-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      holderState.nominees = b.getAttribute("data-nominees");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      syncUrl();
      loadHolders();
    });

    var timer = null;
    $("q").addEventListener("input", function () {
      var v = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () { runSearch(v); }, 180);
    });
  }

  initThemeToggle(function () { if (company) renderMix(company); });

  var params = new URLSearchParams(location.search);
  var code = params.get("c");
  var holderKey = params.get("h");
  if (code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    getJSON("/api/v1/equity/ownership/company/" + encodeURIComponent(code))
      .then(renderCompany)
      .catch(function (e) {
        $("co-name").textContent = code;
        $("co-filing").textContent = e.message.indexOf("404") > -1
          ? "No extracted register for this company. Its annual report may be on a form " +
            "that carries no 株式等の状況 section — the status is disclosed on the market view."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else if (holderKey) {
    $("market-view").hidden = true;
    $("holder-view").hidden = false;
    getJSON("/api/v1/equity/ownership/holder/" + encodeURIComponent(holderKey))
      .then(renderHolder)
      .catch(function (e) {
        $("ho-name").textContent = holderKey;
        $("ho-meta").textContent = "Data unavailable — " + e.message;
      });
  } else {
    initMarket();
  }
})();
