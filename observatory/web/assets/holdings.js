/* Cross-shareholdings page: sector unwind view + per-company both-directions view.
   Data: /api/v1/equity/*. Book values official-as-filed; deltas derived client-side
   and labelled as calculated. Missing renders as —, never 0. */
(function () {
  "use strict";

  var MINUS = "−";
  var chart = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  function yenBn(v, dp) {
    if (v == null) return MISSING;
    return fmtNum(v / 1e9, dp == null ? 1 : dp);
  }
  function signedBn(v) {
    if (v == null) return MISSING;
    var s = v < 0 ? MINUS : "+";
    return s + fmtNum(Math.abs(v) / 1e9, 1);
  }
  function shares(v) { return v == null ? MISSING : fmtNum(v, 0); }
  // Ownership stakes run from a rounding error to double digits; two decimals
  // holds the whole range without pretending to precision we do not have, and
  // a stake below a hundredth of a percent is shown as such rather than 0.00.
  // The same position measured against the HOLDER rather than the issuer. No
  // ceiling is applied: a stake worth more than its holder's book equity is a
  // real situation (Megachips' SiTime holding is 102% of equity), and clamping
  // it would hide the most interesting rows on the page.
  function pctHolder(v) {
    if (v == null) return MISSING;
    if (v > 0 && v < 0.01) return "&lt;0.01";
    return fmtNum(v, 2);
  }
  function pctOut(v) {
    if (v == null) return MISSING;
    if (v > 0 && v < 0.01) return "&lt;0.01";
    return fmtNum(v, 2);
  }
  // The basis date qualifies a percentage. Printed under a "—" it reads as data
  // where there is none, so it is shown only when there is a number to qualify.
  // A percentage we withheld is not the same as one we never had. Where the API
  // gives a reason, the dash carries it, so the reader learns the share base
  // moved rather than assuming the issuer is simply missing from the archive.
  function pctCell(v, basis, title, unavailable) {
    if (v == null && unavailable) {
      return "<span class=withheld title='Not shown: " + esc(unavailable) +
        ". Publishing a percentage measured against the wrong share base would " +
        "overstate the stake.'>" + MISSING + "</span>";
    }
    return pctOut(v) + (v != null && basis
      ? "<span class='asof' title='" + title + "'>" + periodShort(basis) + "</span>"
      : "");
  }
  // Derived flags. The filing's own words are what carries them — the marker
  // is a pointer to the footnote, never a claim of its own.
  function actionFlags(h) {
    var out = (h.corporate_actions || []).map(function (a) {
      return " <span class='badge badge-note' title='Stated by the filer, in this row\u2019s purpose text or in the notes below the table'>" +
        esc(a) + "</span>";
    });
    if (h.reclassified_to_pure) {
      out.push(" <span class='badge badge-moved' title='This filing also reports this holding in its \u4fdd\u6709\u76ee\u7684\u3092\u5909\u66f4\u3057\u305f\u6295\u8cc7\u682a\u5f0f table \u2014 see Reclassified As Pure Investment below'>" +
        "reclassified</span>");
    }
    return out.join("");
  }
  function ratioTag(r) {
    if (!r) return "";
    return "<span class='ratio' title='Current shares divided by prior shares is an exact whole number \u2014 the signature of a split or consolidation, not a trade'>" +
      (r >= 1 ? "\u00d7" + fmtNum(r, 2) : "\u00f7" + fmtNum(1 / r, 2)) + "</span>";
  }
  function mutual(rec) {
    if (!rec) return MISSING;
    if (rec.indexOf("有") === 0) return "Mutual";      // 有
    if (rec.indexOf("無") === 0) return "—";      // 無 -> em dash
    return MISSING;
  }
  function periodShort(iso) { return iso ? String(iso).slice(0, 7) : MISSING; }

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

  // ---- sector view ---------------------------------------------------------
  function renderStrip(s) {
    // Year-ends are staggered and delisted filers stop filing, so the reference
    // period is a range, not a single FY. Say so rather than name one year.
    var asof = s.earliest_period_end && s.latest_period_end
      ? "FY ends " + periodShort(s.earliest_period_end) + " – " + periodShort(s.latest_period_end)
      : "latest filing per company";
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Named policy holdings", "¥" + fmtNum(s.total_book_value_yen / 1e12, 2) + '<span class="unit"> tn</span>',
           s.named_holdings.toLocaleString() + " named holdings · " + asof) +
      cell("Positions cut vs added", fmtNum(s.positions_reduced, 0) + '<span class="unit"> · </span>' + fmtNum(s.positions_increased, 0),
           "reduced · increased, year on year (calculated)") +
      cell("Mutual holdings", fmtNum(s.reciprocal_pairs, 0),
           "of " + s.named_holdings.toLocaleString() + " — issuer holds the filer’s shares too (as filed)") +
      cell("Coverage", fmtNum(s.filers, 0) + '<span class="unit"> filers</span>',
           (s.extraction_status.clean || 0) + " clean · " + (s.extraction_status.partial || 0) + " partial · " +
           (s.extraction_status.failed || 0) + " failed — status disclosed per filing") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label">' + label +
        '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  function renderChart(filers) {
    // The honest unwind measure is positions cut vs added: book values are
    // fair-valued, so a rising market inflates Δ value even mid-unwind.
    var top = filers.filter(function (f) { return (f.reduced || 0) + (f.increased || 0) > 0; })
      .sort(function (a, b) { return (b.reduced + b.increased) - (a.reduced + a.increased); })
      .slice(0, 14)
      .sort(function (a, b) { return (a.reduced - a.increased) - (b.reduced - b.increased); })
      .reverse();

    var el = $("unwind-chart");
    chart = echarts.init(el, null, { renderer: "canvas" });
    var neg = cssVar("--obs-diverge-neg") || "#2f6f8f";
    var pos = cssVar("--obs-diverge-pos") || "#b3703a";
    // at phone widths the outside bar labels collide with category names;
    // the tooltip still carries exact values
    var showLabels = el.clientWidth >= 560;
    var opt = {
      animation: false,
      grid: { left: 8, right: 56, top: 28, bottom: 24, containLabel: true },
      legend: {
        top: 0, right: 0, itemWidth: 10, itemHeight: 10, icon: "rect",
        textStyle: { color: cssVar("--obs-text-muted"), fontSize: 11.5 }
      },
      xAxis: {
        type: "value", name: "positions", nameLocation: "end", nameGap: 8,
        // headroom so the outside labels of the longest bars never touch the edges
        min: -Math.ceil(Math.max.apply(null, top.map(function (f) { return f.reduced; })) * 1.18),
        max: Math.ceil(Math.max.apply(null, top.map(function (f) { return f.increased; })) * 1.3),
        axisLabel: {
          color: cssVar("--obs-text-muted"),
          formatter: function (v) { return fmtNum(Math.abs(v), 0); }
        },
        splitLine: { lineStyle: { color: cssVar("--obs-grid") } },
        axisLine: { show: false }, nameTextStyle: { color: cssVar("--obs-text-muted") }
      },
      yAxis: {
        type: "category",
        data: top.map(axisName),
        axisLabel: { color: cssVar("--obs-ink"), fontSize: 11.5, width: 190, overflow: "truncate" },
        axisTick: { show: false }, axisLine: { show: false }
      },
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        formatter: function (ps) {
          var f = top[ps[0].dataIndex];
          return esc(f.name_en || f.name) + " (" + esc(f.sec_code) + ")<br>" +
            (f.name_en ? "<span style='opacity:.75'>" + esc(f.name) + "</span><br>" : "") +
            "Positions cut: <b>" + fmtNum(f.reduced, 0) + "</b> · added: <b>" +
            fmtNum(f.increased, 0) + "</b><br>" +
            "<span style='opacity:.75'>share counts vs prior FY, as filed; " +
            "additions can include stock splits</span>";
        }
      },
      series: [
        { name: "Cut", type: "bar", stack: "s", barMaxWidth: 14,
          itemStyle: { color: neg },
          data: top.map(function (f) { return -f.reduced; }),
          label: { show: showLabels, position: "left", fontSize: 11,
                   color: cssVar("--obs-text-muted"),
                   formatter: function (p) { return fmtNum(Math.abs(p.value), 0); } } },
        { name: "Added", type: "bar", stack: "s", barMaxWidth: 14,
          itemStyle: { color: pos },
          data: top.map(function (f) { return f.increased; }),
          label: { show: showLabels, position: "right", fontSize: 11,
                   color: cssVar("--obs-text-muted"),
                   formatter: function (p) { return fmtNum(p.value, 0); } } }
      ]
    };
    chart.setOption(opt);
    window.addEventListener("resize", function () { chart && chart.resize(); });
  }

  // Both names are as filed: the holding is named in Japanese, and the company's
  // own annual report states its English name on its cover page. Show English
  // first for a reader who cannot read the filing, keep the Japanese name under
  // it as the record — and where no English name exists anywhere, show the
  // Japanese one alone rather than an empty cell.
  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    var link = href
      ? "<a href='" + href + "'>" + esc(primary) + "</a>" : esc(primary);
    return "<div class='cell-item'><div class='en'>" + link + "</div></div>";
  }

  // axis labels: drop the corporate suffix every name carries, so the part that
  // distinguishes one filer from another survives the truncation
  var SUFFIX_EN = /,?\s*(Inc\.?|Incorporated|Corporation|Corp\.?|Co\.,?\s*Ltd\.?|Company,?\s*Limited|Limited|Ltd\.?|K\.K\.)\s*$/i;
  function axisName(f) {
    return f.name_en
      ? f.name_en.replace(SUFFIX_EN, "").trim()
      : String(f.name || "").replace(/株式会社/g, "").trim();
  }

  var NAMES_CSV_NOTE = "Company names as filed in both languages: name_en is the " +
    "English name from the company's own annual report cover page (EDINET's filer " +
    "registry where it files none; blank where neither states one)";

  function renderUnwindTable(filers) {
    var head = "<thead><tr>" +
      "<th>Filer</th><th class=r>Code</th><th class=r>FY end</th>" +
      "<th class=r>Named holdings</th><th class=r>Book value (¥bn)</th>" +
      "<th class=r>Prior (¥bn)</th><th class=r>Δ (¥bn)</th>" +
      "<th class=r>Cut</th><th class=r>Added</th></tr></thead>";
    var body = filers.map(function (f) {
      var d = (f.book_value_yen != null && f.prior_book_value_yen != null)
        ? f.book_value_yen - f.prior_book_value_yen : null;
      return "<tr><td>" +
        nameCell(f.name_en, f.name, "holdings.html?c=" + esc(f.sec_code)) + "</td>" +
        "<td class='r mono'>" + esc(f.sec_code) + "</td>" +
        "<td class=r>" + periodShort(f.period_end) + "</td>" +
        "<td class=r>" + fmtNum(f.named_holdings, 0) + "</td>" +
        "<td class=r>" + yenBn(f.book_value_yen) + "</td>" +
        "<td class=r>" + yenBn(f.prior_book_value_yen) + "</td>" +
        "<td class=r>" + (d == null ? MISSING : signedBn(d)) + "</td>" +
        "<td class=r>" + fmtNum(f.reduced, 0) + "</td>" +
        "<td class=r>" + fmtNum(f.increased, 0) + "</td></tr>";
    }).join("");
    $("unwind-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("unwind-count").textContent = filers.length + " filers";
  }

  function loadSector() {
    Promise.all([getJSON("/api/v1/equity/summary"), getJSON("/api/v1/equity/unwind")])
      .then(function (rs) {
        var summary = rs[0], unwind = rs[1];
        renderStrip(summary);
        renderChart(unwind.filers);
        renderUnwindTable(unwind.filers);
        $("chart-asof").textContent = "named positions cut vs added, YoY · calculated";
        $("chart-source").textContent =
          "Source: annual securities reports via EDINET (FSA). Book values as filed; Δ calculated.";
        $("header-asof").textContent = "Each company's latest filing · through " +
          periodShort(summary.latest_period_end);
        $("unwind-csv").onclick = function () {
          csvDownload("cross-shareholdings-unwind.csv",
            ["Cross-shareholdings — unwind by filer",
             "Source: EDINET annual securities reports; each company on its latest filing; book values official as filed",
             "Delta = current minus prior FY named book value (calculated)",
             NAMES_CSV_NOTE,
             "Retrieved: " + new Date().toISOString().slice(0, 10),
             "Missing values are empty, never 0"],
            ["filer", "filer_name_en", "sec_code", "fy_end", "named_holdings",
             "book_value_yen", "prior_book_value_yen", "delta_yen_calculated",
             "reduced", "increased"],
            unwind.filers.map(function (f) {
              var d = (f.book_value_yen != null && f.prior_book_value_yen != null)
                ? f.book_value_yen - f.prior_book_value_yen : null;
              return [f.name, f.name_en, f.sec_code, f.period_end, f.named_holdings,
                      f.book_value_yen, f.prior_book_value_yen, d, f.reduced, f.increased];
            }));
        };
        $("chart-png").onclick = function () {
          if (!chart) return;
          // Drawn from the live chart rather than re-laid-out offscreen, so the
          // size on offer is the one on screen.
          var render = function () {
            return chart.getDataURL({
              pixelRatio: 2, backgroundColor: cssVar("--obs-surface") || "#fff" });
          };
          obsExportMenu("cross-shareholdings-unwind.png", render, $("chart-png"),
            { w: chart.getWidth(), h: chart.getHeight(), out: chart.getWidth() * 2 });
        };
      })
      .catch(function (e) {
        $("stat-strip").innerHTML = "<p class='table-meta'>Data unavailable — " +
          esc(e.message) + ". The last good state is unaffected; try reloading.</p>";
      });
  }

  // ---- company view --------------------------------------------------------
  function holdingsRows(holdings) {
    var out = [];
    holdings.forEach(function (h, i) {
      var d = (h.shares != null && h.prior_shares != null) ? h.shares - h.prior_shares : null;
      var name = nameCell(h.held_name_en, h.held_name_raw,
        h.held_sec_code ? "holdings.html?c=" + esc(h.held_sec_code) : null);
      out.push("<tr><td>" + name +
        (h.match_status === "foreign" ? " <span class='h2-note'>overseas</span>" : "") +
        actionFlags(h) + "</td>" +
        "<td class=r>" + shares(h.shares) + "</td>" +
        "<td class=r>" + shares(h.prior_shares) + "</td>" +
        "<td class=r>" + (d == null ? MISSING : (d === 0 ? "0" : (d < 0 ? MINUS : "+") + fmtNum(Math.abs(d), 0))) +
          ratioTag(h.share_ratio) + "</td>" +
        "<td class=r>" + yenBn(h.book_value_yen) + "</td>" +
        "<td class=r>" + yenBn(h.prior_book_value_yen) + "</td>" +
        "<td class=r>" + pctCell(h.pct_outstanding, h.pct_basis_period_end,
          "Denominator taken from the issuer\u2019s own annual report nearest this period",
          h.pct_unavailable) + "</td>" +
        "<td class=r>" + pctHolder(h.pct_of_holder_equity) + "</td>" +
        "<td class=r>" + pctHolder(h.pct_of_holder_assets) + "</td>" +
        "<td class=r>" + mutual(h.reciprocal) + "</td>" +
        "<td>" + (h.purpose_ja
          ? "<button type=button class=purpose-toggle data-i=" + i + ">Stated purpose</button>"
          : MISSING) + "</td></tr>");
      if (h.purpose_ja) {
        out.push("<tr class=purpose-row hidden data-for=" + i + "><td colspan=11>" +
          "<b>Stated reason for holding (as filed):</b> " + esc(h.purpose_ja) + "</td></tr>");
      }
    });
    return out.join("");
  }

  var DIRECTION_LABEL = {
    to_pure: "To pure investment",
    to_policy: "To policy holding",
  };

  // The section that closes the gap between "cut its cross-shareholdings" and
  // "sold its cross-shareholdings". A reclassified holding leaves the named
  // table with no transaction behind it, so it is shown against what the same
  // filing says it actually sold.
  // How big the policy book is against the filer's own balance sheet.
  //
  // The numerator is the filing's OWN total for the whole policy bucket, not
  // the sum of the named rows below — the named table lists only the largest
  // issues. Absent (no tagged total in this filing) hides the strip: there is
  // no reading to show, and a row of dashes would imply we looked and found
  // nothing filed. A tagged total with no usable denominator DOES show a dash,
  // because there the gap is in one figure, not in the disclosure.
  function renderScale(d) {
    var s = d.scale;
    if (!s || s.policy_total_yen == null) {
      $("co-scale").hidden = true;
      $("co-scale-formula").hidden = true;
      return;
    }
    var issues = [];
    if (s.listed_issues != null) issues.push(fmtNum(s.listed_issues, 0) + " listed");
    if (s.unlisted_issues != null) issues.push(fmtNum(s.unlisted_issues, 0) + " unlisted");
    var split = [];
    if (s.listed_yen != null) split.push("¥" + yenBn(s.listed_yen) + "bn listed");
    if (s.unlisted_yen != null) split.push("¥" + yenBn(s.unlisted_yen) + "bn unlisted");

    $("co-scale").hidden = false;
    $("co-scale").innerHTML =
      '<div class="strip-grid cols-3">' +
      cell("Policy shareholdings",
           "¥" + yenBn(s.policy_total_yen) + '<span class="unit"> bn</span>',
           (issues.length ? issues.join(" + ") + " issues · " : "") +
           (split.join(" · ") || "total as filed"), null) +
      cell("Share of equity", pct(s.pct_of_equity),
           s.pct_of_equity == null
             ? "No usable equity figure filed"
             : esc(s.equity_basis_label || "Shareholders’ equity") +
               " · ¥" + yenBn(s.equity_yen) + "bn",
           s.pct_of_equity) +
      cell("Share of total assets", pct(s.pct_of_assets),
           s.pct_of_assets == null
             ? "No usable total-assets figure filed"
             : "of ¥" + yenBn(s.total_assets_yen) + "bn total assets",
           s.pct_of_assets) +
      "</div>";

    $("co-scale-formula").hidden = false;
    $("co-scale-formula").innerHTML =
      "Share of equity and share of total assets are calculated, not filed: " +
      esc(s.equity_calc) + ", and " + esc(s.assets_calc) + ". " +
      "The policy total is the filing’s own total for the whole policy bucket — " +
      "not the sum of the named holdings below, which cover only the largest " +
      "issues" + entitiesPhrase(d.scale_entities) + ". " +
      "It is a floor: holdings at group companies the filing does not name are " +
      "disclosed nowhere and are not counted. " + esc(d.scale_reference || "");

    function pct(v) { return v == null ? MISSING : fmtNum(v, 1) + '<span class="unit">%</span>'; }
    // The meter reads directly as the share it states — 61% of equity fills
    // 61% of the track — so the two percentages are comparable at a glance.
    // Clamped, and never a marker for a pass/fail line: the threshold is a
    // third party's policy, stated in words below, not a verdict this page
    // renders. The lane is reserved even when empty so every foot aligns.
    function cell(label, value, foot, meter) {
      return '<div class="strip-cell"><div class="strip-label" title="' + esc(label) +
        '">' + esc(label) + '</div><div class="strip-value num">' + value + "</div>" +
        '<div class="strip-meter' + (meter == null ? " is-empty" : "") + '"><i style="width:' +
        (meter == null ? "0" : Math.max(0, Math.min(100, meter)).toFixed(1)) +
        '%"></i></div>' +
        '<div class="strip-foot">' + foot + "</div></div>";
    }
    function entitiesPhrase(rows) {
      var names = {};
      (rows || []).forEach(function (r) {
        if (r.holder_table_label) names[r.holder_table_label] = 1;
      });
      var list = Object.keys(names);
      return list.length > 1
        ? ", summed across the entities this filing discloses (" +
          esc(list.join("; ")) + ")"
        : "";
    }
  }

  // Filing-level, so it belongs in the export's header block: as columns these
  // would repeat one divisor down every row.
  function scaleCsvLines(d) {
    var s = d.scale;
    if (!s || s.policy_total_yen == null) return [];
    var out = ["policy_shareholdings_total_yen = " + s.policy_total_yen +
               " (filing's own total for the whole policy bucket, not the sum " +
               "of the rows below; a floor — see scale_note)"];
    if (s.listed_yen != null) out.push("  of which listed_yen = " + s.listed_yen);
    if (s.unlisted_yen != null) out.push("  of which unlisted_yen = " + s.unlisted_yen);
    if (s.equity_yen != null) {
      out.push("shareholders_equity_yen = " + s.equity_yen +
               " (" + (s.equity_basis_label || s.equity_basis) + ")");
    }
    if (s.total_assets_yen != null) {
      out.push("total_assets_yen = " + s.total_assets_yen);
    }
    if (s.pct_of_equity != null) {
      out.push("pct_of_equity_calculated = " + s.pct_of_equity.toFixed(2) +
               " — " + s.equity_calc);
    }
    if (s.pct_of_assets != null) {
      out.push("pct_of_total_assets_calculated = " + s.pct_of_assets.toFixed(2) +
               " — " + s.assets_calc);
    }
    if (d.scale_reference) out.push(d.scale_reference);
    return out;
  }

  function renderReclass(d, code, label) {
    var rows = d.reclassified || [];
    if (!rows.length) { $("reclass-sec").hidden = true; $("reclass-formula").innerHTML = ""; return; }
    $("reclass-sec").hidden = false;

    var toPure = rows.filter(function (r) { return r.direction === "to_pure"; });
    // A row whose two filed numbers are mutually impossible cannot be added up.
    // It stays on the page exactly as filed and is marked; it is not totalled.
    var usable = toPure.filter(function (r) { return !r.implausible; });
    var flagged = toPure.length - usable.length;
    var yen = usable.reduce(function (a, r) {
      return r.book_value_yen == null ? a : a + r.book_value_yen; }, 0);
    var toPolicy = rows.length - toPure.length;
    $("reclass-count").textContent =
      toPure.length + " to pure investment" +
      (toPolicy ? " · " + toPolicy + " to policy" : "");

    var listed = (d.flows || []).filter(function (f) {
      return f.share_class === "listed" && f.sale_proceeds_yen != null; });
    var sold = listed.reduce(function (a, f) { return a + f.sale_proceeds_yen; }, 0);
    $("reclass-vs-sold").innerHTML = toPure.length
      ? "<dl class=reclass-kv>" +
          "<div><dt>Moved to pure investment</dt><dd>" +
            // Every row excluded leaves nothing to total. That is missing, not
            // zero — printing ¥0.0bn here would say the filer moved nothing.
            (usable.length ? "¥" + fmtNum(yen / 1e9, 1) + "bn" : MISSING) +
            "<span class=qual>" +
            (usable.length
              ? usable.length + (usable.length === 1 ? " holding" : " holdings") +
                ", book value as filed" +
                (flagged ? " · " + flagged + " excluded, see below" : "")
              : flagged + (flagged === 1 ? " holding moved, but its" : " holdings moved, but their") +
                " filed figures cannot be totalled — see below") +
            "</span>" +
          "</dd></div>" +
          "<div><dt>Sold during the year</dt><dd>" +
            (listed.length ? "¥" + fmtNum(sold / 1e9, 1) + "bn" : MISSING) +
            "<span class=qual>" + (listed.length
              ? "listed policy shares, proceeds as filed"
              : "no sale proceeds reported for listed policy shares") + "</span>" +
          "</dd></div>" +
        "</dl>" +
        "<p class=table-meta>Different measures, shown together because only the first " +
        "leaves the table above: a standing stock of reclassified holdings against one " +
        "year of sales. Neither is derived — both are figures this filing reports.</p>"
      : "";

    $("reclass-table").innerHTML = "<thead><tr>" +
      "<th>Held company</th><th class=r>Change</th>" +
      "<th class=r title='The fiscal year the filer states the change took effect, verbatim'>FY of change</th>" +
      "<th class=r>Shares</th><th class=r>Book value (¥bn)</th><th>Reason</th>" +
      "</tr></thead><tbody>" +
      rows.map(function (r, i) {
        var name = nameCell(r.held_name_en, r.held_name_raw,
          r.held_sec_code ? "holdings.html?c=" + esc(r.held_sec_code) : null);
        return "<tr><td>" + name +
          (r.implausible
            ? " <span class='badge badge-note' title='This row\u2019s two filed numbers are mutually impossible \u2014 book value \u00f7 shares exceeds \u00a51,000,000 per share. A filer tagging error, shown as filed and left out of the total.'>check filing</span>"
            : "") + "</td>" +
          "<td class=r>" + esc(DIRECTION_LABEL[r.direction] || r.direction) + "</td>" +
          "<td class=r>" + (r.fy_of_change_ja ? esc(r.fy_of_change_ja) : MISSING) + "</td>" +
          "<td class=r>" + shares(r.shares) + "</td>" +
          "<td class=r>" + yenBn(r.book_value_yen) + "</td>" +
          "<td>" + (r.reason_ja
            ? "<button type=button class=purpose-toggle data-i=r" + i + ">Stated reason</button>"
            : MISSING) + "</td></tr>" +
          (r.reason_ja
            ? "<tr class=purpose-row hidden data-for=r" + i + "><td colspan=6>" +
              "<b>Stated reason for the change (as filed):</b> " + esc(r.reason_ja) + "</td></tr>"
            : "");
      }).join("") + "</tbody>";
    $("reclass-formula").innerHTML =
      "Official statistics, exactly as filed. Share counts and book values are those the " +
      "filer reports for the reclassified holding; a blank is a value the filing does not " +
      "give, never a zero. A filing repeats these rows for several years after the change, " +
      "so an FY of change earlier than this filing's own year end is expected." +
      (flagged ? "<br>" + esc(d.implausible_note || "") : "");

    $("reclass-table").addEventListener("click", function (e) {
      var b = e.target.closest(".purpose-toggle");
      if (!b) return;
      var row = $("reclass-table").querySelector(".purpose-row[data-for='" + b.dataset.i + "']");
      if (row) row.hidden = !row.hidden;
    });

    $("reclass-csv").onclick = function () {
      csvDownload("purpose-changes-" + code + ".csv",
        ["Holdings whose stated purpose changed — " + label + " (" + code + ")",
         "Source: EDINET filing " + (d.filing ? d.filing.doc_id : "") +
           ", 保有目的を変更した投資株式 table; official statistics exactly as filed",
         "direction=to_pure: moved from a policy shareholding to 純投資目的 (pure investment); " +
           "the shares are not necessarily sold and the holding leaves the named policy table",
         "direction=to_policy: the reverse",
         "fy_of_change_ja is verbatim as filed; some filers list more than one fiscal year",
         NAMES_CSV_NOTE,
         "Missing values are empty, never 0"],
        ["held_company", "held_company_name_en", "held_sec_code", "match_status",
         "direction", "fy_of_change_ja", "shares", "book_value_yen", "reason_ja"],
        rows.map(function (r) {
          return [r.held_name_raw, r.held_name_en, r.held_sec_code, r.match_status,
                  r.direction, r.fy_of_change_ja, r.shares, r.book_value_yen, r.reason_ja];
        }));
    };
  }

  function loadCompany(code) {
    getJSON("/api/v1/equity/company/" + encodeURIComponent(code)).then(function (d) {
      $("sector-view").hidden = true;
      $("company-view").hidden = false;
      var name = (d.entity && d.entity.name_ja) ||
                 (d.filing && d.filing.filer_name) ||
                 (d.holders[0] && d.holders[0].holder_name) || code;
      var nameEn = (d.entity && d.entity.name_en) ||
                   (d.filing && d.filing.filer_name_en) ||
                   (d.holders[0] && d.holders[0].holder_name_en) || "";
      // Matches the title the server already sent for this address, so the
      // tab does not change wording when the fetch lands. See app/prerender.py.
      document.title = (nameEn || name) + " (" + code +
        ") \u2014 Cross-Shareholdings \u00b7 Plover Analytics";
      $("co-name").textContent = nameEn || name;
      // the Japanese name is the one on the filing — keep it on the page, under
      // the English heading, not hidden behind a hover
      $("co-name-ja").textContent = nameEn ? name : "";
      $("co-name-ja").hidden = !nameEn;
      $("co-code").textContent = code;
      // sector is recorded in Japanese only; show the standard English name of
      // the TSE sector where there is one, with the recorded Japanese on hover
      var ind = (d.entity && d.entity.industry) || "";
      var indEn = (d.entity && d.entity.industry_en) || "";
      $("co-industry").textContent = indEn || ind;
      $("co-industry").title = indEn && ind ? ind : "";
      $("header-asof").textContent = d.filing
        ? "FY end " + periodShort(d.filing.period_end)
        : "No extracted filing";

      if (d.filing) {
        $("co-filing").innerHTML = "Filing: <span class=mono>" + esc(d.filing.doc_id) +
          "</span> · FY end " + periodShort(d.filing.period_end) +
          " · filed " + esc(d.filing.filed_date) +
          " · archived SHA-256 <span class=mono>" +
          (d.filing.sha256 ? esc(String(d.filing.sha256).slice(0, 12)) + "…" : MISSING) + "</span>" +
          (d.filing.status !== "clean"
            ? " · <b>extraction status: " + esc(d.filing.status) + "</b> (disclosed, see methodology)"
            : "") +
          " · Official statistic — figures exactly as filed";
      } else {
        $("co-filing").textContent =
          "No extracted filing for this company (it may not disclose named policy holdings, " +
          "or its next fiscal year is not yet filed) — " +
          "the holders view below is still complete for covered filers.";
      }

      renderScale(d);

      if (d.holdings.length) {
        $("holdings-sec").hidden = false;
        $("holdings-count").textContent = d.holdings.length + " named holdings";
        $("holdings-table").innerHTML = "<thead><tr>" +
          "<th>Held company</th><th class=r>Shares</th><th class=r>Prior shares</th>" +
          "<th class=r title='Share counts as filed — a 2×/3×/5× jump with an unchanged position is usually a stock split, not a purchase'>Δ shares*</th>" +
          "<th class=r>Book value (¥bn)</th>" +
          "<th class=r>Prior (¥bn)</th>" +
          "<th class=r title='Stake as a share of the issuer’s own shares outstanding'>% of issuer†</th>" +
          "<th class=r title='This one position as a share of this company’s OWN shareholders’ equity — how much of its own capital sits in this one name'>% of equity‡</th>" +
          "<th class=r title='This one position as a share of this company’s OWN total assets'>% of assets‡</th>" +
          "<th class=r>Mutual</th><th>Purpose</th>" +
          "</tr></thead><tbody>" + holdingsRows(d.holdings) + "</tbody>";
        $("holdings-formula").innerHTML =
          "* Δ shares compares the two as-filed columns; filings are not adjusted for stock " +
          "splits, so a round multiple usually indicates a split rather than a purchase. " +
          "A ×n tag marks an exact whole-number ratio; a tag beside a company name repeats " +
          "what the filer says in its purpose text or notes, and “reclassified” means this " +
          "filing also reports the holding in the purpose-change table below.<br>" +
          "† % of issuer is calculated, not filed: " + esc(d.ownership_calc) + ". " +
          "For a single-class listed stock this is the stake's share of market capitalisation. " +
          "The small date is the issuer fiscal year end the denominator comes from; it is the " +
          "issuer report nearest this filing's own year end, and can fall either side of it. " +
          "Shown as — where the issuer files no annual report we hold, and where a split or " +
          "share issue leaves the share base indeterminate — hover the dash for which. A stake " +
          "measured against the wrong share base is withheld rather than published.<br>" +
          "‡ % of equity and % of assets are the mirror of % of issuer, and are also " +
          "calculated: " + esc(d.holder_share_calc) + ". They say how much of THIS " +
          "company's own capital sits in that one name. " +
          (d.scale && d.scale.equity_basis_label
            ? "Denominator: " + esc(d.scale.equity_basis_label) + ". " : "") +
          "A position can legitimately exceed 100% of equity — a holder whose book " +
          "equity is smaller than one long-held stake is a real case, not an error — " +
          "so no ceiling is applied; check an extreme value against the filing.";
        $("holdings-table").addEventListener("click", function (e) {
          var b = e.target.closest(".purpose-toggle");
          if (!b) return;
          var row = document.querySelector(".purpose-row[data-for='" + b.dataset.i + "']");
          if (row) row.hidden = !row.hidden;
        });
        if (d.notes && d.notes.length) {
          $("filing-notes").hidden = false;
          $("note-list").innerHTML = d.notes.map(function (n) {
            return "<li>" + esc(n.text_ja) + "</li>";
          }).join("");
        } else { $("filing-notes").hidden = true; }

        $("holdings-csv").onclick = function () {
          csvDownload("holdings-" + code + ".csv",
            ["Policy shareholdings of " + (nameEn || name) + " (" + code + ")",
             "Source: EDINET filing " + d.filing.doc_id + ", archived SHA-256 " + d.filing.sha256,
             "Official statistics as filed; delta and percentage columns calculated",
             "pct_of_issuer_outstanding = " + d.ownership_calc,
             "pct_of_holder_equity / pct_of_holder_assets = " + d.holder_share_calc +
               " - the mirror: this position measured against the holder, not the issuer. "
               + "May exceed 100; no ceiling is applied.",
             "pct_basis_period_end = the issuer fiscal year end the denominator is taken from, " +
               "the issuer report nearest this filing's year end on either side",
             "pct_withheld_reason = why no percentage is given; blank where one is",
             "corporate_actions = " + d.action_calc,
             "share_ratio = " + d.ratio_calc,
             NAMES_CSV_NOTE,
             "Missing values are empty, never 0"].concat(scaleCsvLines(d)),
            ["held_company", "held_company_name_en", "held_sec_code", "match_status",
             "shares", "prior_shares",
             "book_value_yen", "prior_book_value_yen",
             "pct_of_issuer_outstanding_calculated", "pct_basis_period_end",
             "pct_withheld_reason",
             "pct_of_holder_equity_calculated", "pct_of_holder_assets_calculated",
             "corporate_actions_detected", "share_ratio_calculated",
             "reciprocal_as_filed", "purpose_ja"],
            d.holdings.map(function (h) {
              return [h.held_name_raw, h.held_name_en, h.held_sec_code, h.match_status,
                      h.shares, h.prior_shares,
                      h.book_value_yen, h.prior_book_value_yen,
                      h.pct_outstanding == null ? null : h.pct_outstanding.toFixed(4),
                      h.pct_basis_period_end, h.pct_unavailable,
                      h.pct_of_holder_equity == null ? null : h.pct_of_holder_equity.toFixed(4),
                      h.pct_of_holder_assets == null ? null : h.pct_of_holder_assets.toFixed(4),
                      (h.corporate_actions || []).join("; "), h.share_ratio,
                      h.reciprocal, h.purpose_ja];
            }));
        };
      } else { $("holdings-sec").hidden = true; $("holdings-formula").innerHTML = ""; }

      renderReclass(d, code, nameEn || name);

      if (d.holders.length) {
        $("holders-sec").hidden = false;
        $("holders-count").textContent = d.holders.length + " disclosed holders";
        $("holders-table").innerHTML = "<thead><tr>" +
          "<th>Holder</th><th class=r>Code</th><th class=r>FY end</th>" +
          "<th class=r>Shares</th><th class=r>Book value (¥bn)</th>" +
          "<th class=r>Prior (¥bn)</th>" +
          "<th class=r title='Holder’s stake as a share of this company’s shares outstanding'>% of company*</th>" +
          "<th class=r title='This position as a share of that HOLDER’s own shareholders’ equity — how much of its capital it has committed to this company'>% of holder’s equity†</th>" +
          "<th class=r title='This position as a share of that HOLDER’s own total assets'>% of holder’s assets†</th>" +
          "<th class=r>Mutual</th></tr></thead><tbody>" +
          d.holders.map(function (h) {
            return "<tr><td>" +
              nameCell(h.holder_name_en, h.holder_name,
                       "holdings.html?c=" + esc(h.holder_sec_code)) + "</td>" +
              "<td class='r mono'>" + esc(h.holder_sec_code) + "</td>" +
              "<td class=r>" + periodShort(h.period_end) + "</td>" +
              "<td class=r>" + shares(h.shares) + "</td>" +
              "<td class=r>" + yenBn(h.book_value_yen) + "</td>" +
              "<td class=r>" + yenBn(h.prior_book_value_yen) + "</td>" +
              "<td class=r>" + pctCell(h.pct_outstanding, h.pct_basis_period_end,
                "Denominator taken from this company\u2019s own annual report nearest this period",
                h.pct_unavailable) + "</td>" +
              "<td class=r>" + pctHolder(h.pct_of_holder_equity) + "</td>" +
              "<td class=r>" + pctHolder(h.pct_of_holder_assets) + "</td>" +
              "<td class=r>" + mutual(h.reciprocal) + "</td></tr>";
          }).join("") + "</tbody>";
        $("holders-formula").innerHTML =
          "* Calculated, not filed: " + esc(d.ownership_calc) + ". The small date is the " +
          "fiscal year end the denominator comes from — the issuer report nearest this " +
          "holder's own year end, either side of it. A dash is a percentage withheld or " +
          "unavailable, never a zero; hover it for the reason. Holders are not aggregated — " +
          "a total across this column would double count where a group files more than one table.<br>" +
          "† Also calculated: " + esc(d.holder_share_calc) + ". This measures the same " +
          "position against the HOLDER instead of against this company — how much of its " +
          "own capital that holder has committed here. Each row uses that holder's own " +
          "balance sheet, so the denominator differs down the column, and a holder " +
          "reporting only parent-company figures is not comparable with a consolidated one.";
        $("holders-csv").onclick = function () {
          csvDownload("holders-of-" + code + ".csv",
            ["Policy holders of " + (nameEn || name) + " (" + code + ")",
             "Source: each holder's EDINET annual securities report (doc ids in column)",
             "Official statistics as filed",
             NAMES_CSV_NOTE,
             "Coverage: filers that disclose named policy holdings — not the full shareholder register",
             "pct_of_company_outstanding = " + d.ownership_calc,
             "pct_withheld_reason = why no percentage is given; blank where one is",
             "pct_of_holder_equity / pct_of_holder_assets = " + d.holder_share_calc +
               " - each row uses THAT holder's own balance sheet, so the denominator "
               + "differs down the column; holder_equity_basis says which figure it is"],
            ["holder", "holder_name_en", "holder_sec_code", "doc_id", "fy_end", "shares",
             "book_value_yen", "prior_book_value_yen",
             "pct_of_company_outstanding_calculated", "pct_basis_period_end",
             "pct_withheld_reason",
             "pct_of_holder_equity_calculated", "pct_of_holder_assets_calculated",
             "holder_equity_basis", "reciprocal_as_filed"],
            d.holders.map(function (h) {
              return [h.holder_name, h.holder_name_en, h.holder_sec_code, h.doc_id, h.period_end,
                      h.shares, h.book_value_yen, h.prior_book_value_yen,
                      h.pct_outstanding == null ? null : h.pct_outstanding.toFixed(4),
                      h.pct_basis_period_end, h.pct_unavailable,
                      h.pct_of_holder_equity == null ? null : h.pct_of_holder_equity.toFixed(4),
                      h.pct_of_holder_assets == null ? null : h.pct_of_holder_assets.toFixed(4),
                      h.holder_equity_basis, h.reciprocal];
            }));
        };
      } else { $("holders-sec").hidden = true; $("holders-formula").innerHTML = ""; }
    }).catch(function () {
      $("sector-view").hidden = true;
      $("company-view").hidden = false;
      $("co-name").textContent = "No data for " + code;
      $("co-scale").hidden = true;
      $("co-scale-formula").hidden = true;
      $("co-filing").textContent =
        "No covered filing and no covered holder names this securities code. " +
        "Coverage is filers that disclose named policy holdings; see the filer list.";
    });
  }

  // ---- search --------------------------------------------------------------
  function initSearch() {
    var t = null;
    $("q").addEventListener("input", function () {
      clearTimeout(t);
      var q = this.value.trim();
      if (!q) { $("search-results").innerHTML = ""; return; }
      t = setTimeout(function () {
        getJSON("/api/v1/equity/companies?q=" + encodeURIComponent(q)).then(function (d) {
          $("search-results").innerHTML = d.companies.length
            ? "<div class='table-wrap' style='margin-top:8px'><table class='tbl-hold'><tbody>" +
              d.companies.map(function (c) {
                return "<tr><td>" +
                  nameCell(c.name_en, c.name, "holdings.html?c=" + esc(c.sec_code)) + delistedTag(c) +
                  "</td><td class='r mono'>" + esc(c.sec_code) + "</td>" +
                  "<td class=r>" + (c.holdings_count ? fmtNum(c.holdings_count, 0) + " holdings" : "") + "</td>" +
                  "<td class=r>" + (c.held_by_count ? "held by " + fmtNum(c.held_by_count, 0) : "") + "</td></tr>";
              }).join("") + "</tbody></table></div>"
            : "<p class='table-meta'>No covered company matches “" + esc(q) +
              "”. Try the English name (Toyota), the Japanese name (トヨタ自動車) " +
              "or the four-digit securities code (7203).</p>";
        });
      }, 200);
    });
  }

  // ---- boot ----------------------------------------------------------------
  initThemeToggle(function () { if (chart) { location.reload(); } });
  var code = new URLSearchParams(location.search).get("c");
  if (code) { loadCompany(code); } else { loadSector(); initSearch(); }
})();
