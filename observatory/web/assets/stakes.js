/* 5% filings: the tape and the most active filers (market view), every
   disclosed holder of one company (company view), and one holder's book
   (holder view).
   Data: /api/v1/equity/stakes/*. Ratios, share counts, purposes and the
   important-proposal answer are official as filed; the change in percentage
   points and every count on this page are calculated. Missing renders as —,
   never 0, and a null important-proposal answer means the question was not put
   to that filer — never "no". */
(function () {
  "use strict";

  var tapeState = { filter: "all" };
  var filerState = { activist: "", by: "group", type: "", group: "" };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function pct(v, dp) { return v == null ? MISSING : fmtNum(v, dp == null ? 2 : dp); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function yenM(v) { return v == null ? MISSING : fmtNum(v / 1e6, 0); }
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

  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    var link = href ? "<a href='" + href + "'>" + esc(primary) + "</a>" : esc(primary);
    return "<div class='cell-item'><div class='en'>" + link + "</div></div>";
  }

  function errorInto(id, e) {
    $(id).innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) +
      ". The last good state is unaffected; try reloading.</p>";
  }

  var REPORT_LABEL = { initial: "New 5% holder", change: "Change", amendment: "Correction" };

  // An activist's stated purpose can run past a thousand characters — the whole
  // campaign, as filed. It is the most valuable text on the page and it must
  // not push the numbers off the screen, so a long one opens on request.
  // A purpose belongs in prose, not in a table cell: an activist's runs past a
  // thousand characters and one row then fills the screen, pushing every number
  // in the table off it. The row keeps a one-line opening with the whole
  // statement on hover, and the CSV carries it in full.
  var CLIP = 90;
  function clip(text) {
    if (!text) return MISSING;
    return text.length <= CLIP ? text : text.slice(0, CLIP) + "…";
  }

  var PURPOSE_PREVIEW = 220;
  function purposeBlock(text) {
    var label = "<b>Stated purpose (as filed):</b> ";
    if (text.length <= PURPOSE_PREVIEW) {
      return "<p class='grp-purpose'>" + label + esc(text) + "</p>";
    }
    return "<details class='grp-purpose'><summary>" + label +
      esc(text.slice(0, PURPOSE_PREVIEW)) + "…</summary><p>" + esc(text) + "</p></details>";
  }

  // Three states, and the third is the one a reader must not mistake for "no":
  // the special-reporting form used by institutions never asks the question.
  function proposalBadge(v, asked) {
    if (v === true) return "<span class='badge badge-warn'>Important proposals</span>";
    if (v === false) return "<span class='badge badge-note'>None stated</span>";
    if (asked === false) return "<span class='badge badge-note' title='The " +
      "special-reporting form, filed by institutions that undertake to make no " +
      "important proposals, carries no 重要提案行為等 field — the question was " +
      "not put to this filer'>Not on this form</span>";
    return "<span class='badge badge-note' title='This filing does not tag the " +
      "field'>Not stated</span>";
  }

  // ---- market view ---------------------------------------------------------
  function renderStrip(s) {
    var cur = s.current_positions || {};
    $("stat-strip").innerHTML =
      '<div class="strip-grid">' +
      cell("Reports archived", count(s.filings) + '<span class="unit"> filings</span>',
           count(s.issuers) + " companies · " + count(s.filers) + " filing groups · " +
           "filed " + day(s.earliest_filed) + " to " + day(s.latest_filed)) +
      cell("Groups at 5% or more", count(cur.at_or_above_5pct) + '<span class="unit"> groups</span>',
           "Latest report per group still showing 5% or more, across " +
           count(cur.issuers) + " companies") +
      cell("Reports stating proposals", count(s.activist_filings) + '<span class="unit"> filings</span>',
           "Filed answer to 重要提案行為等 — the line between a stake and a campaign") +
      cell("Filed within", count(s.median_days_to_file) + '<span class="unit"> days</span>',
           "Median gap from trigger date to filing (calculated). A change report often " +
           "restates the original trigger, so this is not a lateness measure") +
      cell("Coverage", count((s.extraction_status || {}).clean) + '<span class="unit"> clean filings</span>',
           count((s.extraction_status || {}).partial) + " partial — every gate that failed " +
           "is published on the filing") +
      "</div>";
    function cell(label, value, foot) {
      return '<div class="strip-cell"><div class="strip-label">' + label +
        '</div><div class="strip-value num">' + value +
        '</div><div class="strip-foot">' + foot + "</div></div>";
    }
  }

  var TAPE_QUERY = {
    all: "limit=60",
    activist: "limit=60&activist=true",
    initial: "limit=60&report_type=initial",
    moves: "limit=60&min_change=1",
  };
  var TAPE_META = {
    all: "Every report as filed, most recently filed first.",
    activist: "Reports where a holder states it may act under <b>重要提案行為等</b> — " +
      "board proposals, capital policy, a sale of the business. This is the filed answer, " +
      "not our inference.",
    initial: "First reports: a holder crossing 5% for the first time.",
    moves: "Reports where the group's holding moved by at least one percentage point.",
  };

  function renderTape(d) {
    var rows = d.filings;
    $("tape-count").textContent = rows.length + " reports";
    $("tape-meta").innerHTML = TAPE_META[tapeState.filter];
    $("tape-table").innerHTML =
      "<thead><tr><th>Filed</th><th>Company</th><th>Holder</th><th>Report</th>" +
      "<th class=r>Stake (%)</th><th class=r>Was (%)</th><th class=r>Move</th>" +
      "<th>Important proposals</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td class='nowrap' title='Triggered " + esc(day(r.requirement_date)) + "'>" +
          esc(day(r.filed_date)) +
          (r.status === "partial" ? " <span class='badge badge-note' title='" +
             esc(r.detail || "") + "'>Partial</span>" : "") +
          "</td><td>" + nameCell(r.issuer_name_en, r.issuer_name_raw,
              r.issuer_sec_code ? "stakes.html?c=" + esc(r.issuer_sec_code) : null) +
          "</td><td>" + nameCell(null, r.filer_name,
              r.filer_edinet_code ? "stakes.html?h=" + esc(r.filer_edinet_code) : null) +
          "</td><td" + (r.change_no ? " title='Change report No. " + esc(r.change_no) + "'" : "") + ">" +
          esc(REPORT_LABEL[r.report_type] || r.report_type) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class=r>" + pct(r.prior_ratio_pct) +
          "</td><td class='r move'>" + move(r.ratio_change_pp) +
          "</td><td>" + proposalBadge(r.important_proposal, r.proposal_asked) +
          "</td></tr>";
      }).join("") + "</tbody></table>";
    $("tape-formula").textContent =
      "Stake and prior stake are the filed 株券等保有割合. Move is the difference " +
      "between them in percentage points, calculated. Rows are ordered by the date " +
      "the report was filed. " + (d.tape_note || "");
    $("tape-csv").onclick = function () {
      csvDownload("japan-5pct-filings-" + tapeState.filter + ".csv",
        ["Plover Analytics — 5% filings (大量保有報告書)",
         "View: " + TAPE_META[tapeState.filter].replace(/<[^>]+>/g, ""),
         "Ratios, share counts and the important-proposal answer exactly as filed.",
         "filed_date is EDINET's own submission record; cover_date is the date printed on the filing.",
         "ratio_change_pp is calculated as ratio_pct − prior_ratio_pct.",
         "important_proposal is empty where the form does not carry the field — never read as 'no'.",
         "Source: 大量保有報告書 via EDINET, Financial Services Agency."],
        ["requirement_date", "filed_date", "cover_date", "issuer_sec_code", "issuer_name_ja",
         "issuer_name_en", "filer_name", "filer_edinet_code", "report_type",
         "change_no", "shares_held", "shares_outstanding", "ratio_pct",
         "prior_ratio_pct", "ratio_change_pp", "important_proposal", "purpose_ja",
         "doc_id", "status"],
        rows.map(function (r) {
          return [r.requirement_date, r.filed_date, r.cover_date, r.issuer_sec_code, r.issuer_name_raw,
                  r.issuer_name_en, r.filer_name, r.filer_edinet_code, r.report_type,
                  r.change_no, r.shares_held, r.shares_outstanding, r.ratio_pct,
                  r.prior_ratio_pct, r.ratio_change_pp, r.important_proposal,
                  r.purpose_ja, r.doc_id, r.status];
        }));
    };
  }

  function renderFilers(d) {
    var rows = d.holders;
    var grouped = d.by === "group";
    $("filers-count").textContent = grouped
      ? count(d.groups_total) + " groups · " + count(d.filers_total) + " filing entities"
      : count(rows.length) + " of " + count(d.filers_total) + " filing entities";
    var lead = d.group
      ? "<a href='#' id='clear-group'>← All filers</a> · filing entities inside <b>" +
        esc(d.group) + "</b>."
      : (d.activist_only
          ? "Filers that have stated an <b>important-proposal</b> act on at least one report."
          : "Ranked by how many companies the filer has reported on.");
    $("filers-meta").innerHTML = lead + (grouped && !d.group
      ? " A family's filing entities are consolidated: <b>BlackRock files under " +
        "sixteen EDINET codes</b>, so listing them separately reads as sixteen investors."
      : "");
    $("filers-table").innerHTML =
      "<thead><tr><th>" + (grouped ? "Filer group" : "Filing entity") +
      "</th><th>Type</th><th class=r>Companies</th><th class=r>Reports</th>" +
      "<th class=r>Largest stake (%)</th><th class=r>Proposal reports</th>" +
      "<th>Latest report</th></tr></thead><tbody>" +
      rows.map(function (r) {
        var name = grouped
          ? "<div class='cell-item'><div class='en'>" + esc(r.group) + "</div>" +
            (r.entity_count > 1
              ? "<span class='members'><a href='#' data-group='" + esc(r.group) + "'>" +
                r.entity_count + " filing entities</a></span>" : "") + "</div>"
          : nameCell(r.name_en, r.name_ja,
                     r.holder_key ? "stakes.html?h=" + encodeURIComponent(r.holder_key) : null);
        return "<tr><td>" + name +
          "</td><td>" + typeBadge(r) +
          "</td><td class=r>" + count(r.issuers) +
          "</td><td class=r>" + count(r.reports) +
          "</td><td class=r>" + pct(r.max_ratio_pct) +
          "</td><td class=r>" + count(r.proposal_reports) +
          "</td><td>" + esc(day(r.latest_report)) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("filers-formula").textContent =
      "Companies counts the distinct issuers reported on — for a group it is the " +
      "issuers its entities cover between them, not the sum of theirs, because " +
      "they file on largely the same names. " + (d.type_note || "") + " " +
      (d.group_note || "");
    Array.prototype.forEach.call($("filers-table").querySelectorAll("a[data-group]"),
      function (a) {
        a.onclick = function (ev) {
          ev.preventDefault();
          filerState.group = a.getAttribute("data-group");
          loadFilers();
        };
      });
    if ($("clear-group")) {
      $("clear-group").onclick = function (ev) {
        ev.preventDefault();
        filerState.group = "";
        loadFilers();
      };
    }
    $("filers-csv").onclick = function () {
      csvDownload("japan-5pct-filers.csv",
        ["Plover Analytics — most active 5% filers",
         d.activist_only ? "Filers that have stated an important-proposal act" : "All filers",
         "Counts are calculated over archived reports; ratios are as filed.",
         "filer_type is derived from the filer's own 事業内容; group is a classification map of filing entities to their family.",
         "Source: 大量保有報告書 via EDINET, Financial Services Agency."],
        ["group", "edinet_code", "name_ja", "name_en", "filer_type", "entities",
         "issuers", "reports", "max_ratio_pct", "proposal_reports", "latest_report"],
        rows.map(function (r) {
          return [r.group, r.holder_edinet_code, r.name_ja, r.name_en, r.filer_type,
                  r.entity_count, r.issuers, r.reports, r.max_ratio_pct,
                  r.proposal_reports, r.latest_report];
        }));
    };
  }

  function loadTape() {
    syncUrl();
    getJSON("/api/v1/equity/stakes/recent?" + TAPE_QUERY[tapeState.filter])
      .then(renderTape).catch(function (e) { errorInto("tape-meta", e); });
  }

  // A filer's type is our reading of what it filed as its business, so it is
  // rendered like every other derived label here: outline, quiet, with the
  // evidence on hover.
  function typeBadge(r) {
    if (!r.filer_type) return MISSING;
    var why = r.filer_type === "mixed"
      ? "This group's filing entities state different businesses: " +
        (r.filer_type_mix || []).join(", ")
      : "From the filer's own 事業内容";
    return "<span class='badge badge-note' title='" + esc(why) + "'>" +
      esc(r.filer_type_en) + "</span>";
  }

  function loadFilers() {
    syncUrl();
    getJSON("/api/v1/equity/stakes/holders?limit=40&by=" + filerState.by +
            (filerState.type ? "&filer_type=" + encodeURIComponent(filerState.type) : "") +
            (filerState.group ? "&group=" + encodeURIComponent(filerState.group) : "") +
            (filerState.activist ? "&activist=true" : ""))
      .then(renderFilers).catch(function (e) { errorInto("filers-meta", e); });
  }

  function syncUrl() {
    var p = new URLSearchParams();
    if (tapeState.filter !== "all") p.set("view", tapeState.filter);
    if (filerState.activist) p.set("filers", "activist");
    if (filerState.by !== "group") p.set("by", filerState.by);
    if (filerState.type) p.set("type", filerState.type);
    if (filerState.group) p.set("group", filerState.group);
    history.replaceState(null, "", "stakes.html" + (p.toString() ? "?" + p.toString() : ""));
  }

  function runSearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/stakes/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No archived 5% report " +
            "names that company. A company with no holder above 5% has no report — " +
            "and the archive reaches back only as far as the capture does.</p>";
          return;
        }
        $("search-results").innerHTML =
          "<div class='table-wrap'><table class='tbl-stk'><thead><tr><th>Company</th>" +
          "<th class=r>Reports</th><th class=r>Filing groups</th>" +
          "<th class=r>Proposal reports</th><th>Latest report</th></tr></thead><tbody>" +
          d.companies.map(function (c) {
            return "<tr><td>" + nameCell(c.name_en, c.name, "stakes.html?c=" + esc(c.sec_code)) +
              "</td><td class=r>" + count(c.reports) +
              "</td><td class=r>" + count(c.groups) +
              "</td><td class=r>" + count(c.activist_reports) +
              "</td><td>" + esc(day(c.latest_report)) + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      })
      .catch(function (e) { errorInto("search-results", e); });
  }

  // ---- company view --------------------------------------------------------
  function renderCompany(d) {
    var groups = d.groups || [];
    var nameJa = groups.length && groups[0].issuer_name_raw
      ? groups[0].issuer_name_raw : d.issuer_name;
    var name = d.issuer_name_en || nameJa;
    document.title = name + " · 5% Filings · Plover Analytics";
    $("co-name").textContent = name;
    $("co-code").textContent = d.issuer_sec_code || "";
    if (d.issuer_name_en && nameJa) {
      $("co-name-ja").textContent = nameJa;
      $("co-name-ja").hidden = false;
    }
    $("co-meta").innerHTML = count(d.reports.length) + " reports archived · " +
      "<span class='badge badge-official'>Official statistic</span>";
    var proposals = groups.filter(function (g) { return g.important_proposal; }).length;
    $("co-facts").innerHTML =
      fact("Groups at 5% or more", count(d.groups_at_or_above_5pct), "",
           "Latest report per filing group") +
      fact("Disclosed above 5%", pct(d.combined_current_pct), "%",
           "Sum of those groups' filed ratios (calculated) — an indication, not an exact share") +
      fact("Stating proposals", count(proposals), "",
           "Groups whose latest report states an act under 重要提案行為等");
    $("grp-count").textContent = groups.length + " groups";
    $("groups").innerHTML = groups.map(function (g) {
      var members = (g.holders || []).map(function (h) {
        return "<tr><td>" + nameCell(h.name_en, h.name_raw,
                 h.holder_edinet_code ? "stakes.html?h=" + esc(h.holder_edinet_code) : null) +
          "</td><td>" + esc(h.holder_type_ja || MISSING) +
          "</td><td class=r>" + count(h.shares_held) +
          "</td><td class=r>" + pct(h.ratio_pct) +
          "</td><td class=r>" + pct(h.prior_ratio_pct) +
          "</td><td>" + (h.in_group_total ? "In the group total"
             : "<span class='badge badge-note'>Not in the group total</span>") +
          "</td></tr>";
      }).join("");
      return "<div class='group" + (g.is_current ? " is-current" : "") + "'>" +
        "<h3>" + esc(g.filer_name || MISSING) + "</h3>" +
        "<p class='grp-meta'>" + (g.is_current
            ? "Holds <b>" + pct(g.ratio_pct) + "%</b>"
            : "Exited — latest report shows " + pct(g.ratio_pct) + "%") +
        " · was " + pct(g.prior_ratio_pct) + "% · " + move(g.ratio_change_pp) +
        " · " + esc(REPORT_LABEL[g.report_type] || g.report_type) +
        " triggered " + esc(day(g.requirement_date || g.filed_date)) +
        " · " + proposalBadge(g.important_proposal, g.proposal_asked) +
        " · <a href='https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100=" +
        esc(g.doc_id) + "'>filing " + esc(g.doc_id) + "</a></p>" +
        (g.purpose_ja ? purposeBlock(g.purpose_ja) : "") +
        (g.filing_reason_ja ? "<p class='grp-purpose'><b>Reason for this report:</b> " +
           esc(g.filing_reason_ja) + "</p>" : "") +
        (members ? "<div class='table-wrap' style='margin-top:8px'><table class='tbl-stk'>" +
           "<thead><tr><th>Holder</th><th>Type as filed</th><th class=r>Shares</th>" +
           "<th class=r>Stake (%)</th><th class=r>Was (%)</th><th>Counted</th></tr></thead>" +
           "<tbody>" + members + "</tbody></table></div>" : "") +
        (g.status === "partial" && g.detail
           ? "<p class='disclose flagged'><b>This filing did not pass every check.</b> " +
             esc(g.detail) + ". Figures are published exactly as filed.</p>" : "") +
        "</div>";
    }).join("");

    var reps = d.reports || [];
    $("rep-count").textContent = reps.length + " reports";
    $("rep-table").innerHTML =
      "<thead><tr><th>Trigger date</th><th>Filed</th><th>Holder</th><th>Report</th>" +
      "<th class=r>Shares</th><th class=r>Stake (%)</th><th class=r>Was (%)</th>" +
      "<th class=r>Move</th><th>Important proposals</th></tr></thead><tbody>" +
      reps.map(function (r) {
        return "<tr><td>" + esc(day(r.requirement_date)) +
          "</td><td>" + esc(day(r.filed_date)) +
          "</td><td>" + nameCell(null, r.filer_name,
              r.filer_edinet_code ? "stakes.html?h=" + esc(r.filer_edinet_code) : null) +
          "</td><td>" + esc(REPORT_LABEL[r.report_type] || r.report_type) +
          "</td><td class=r>" + count(r.shares_held) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class=r>" + pct(r.prior_ratio_pct) +
          "</td><td class='r move'>" + move(r.ratio_change_pp) +
          "</td><td>" + proposalBadge(r.important_proposal, r.proposal_asked) +
          "</td></tr>";
      }).join("") + "</tbody></table>";
    $("rep-csv").onclick = function () {
      csvDownload("5pct-reports-" + (d.issuer_sec_code || "issuer") + ".csv",
        ["Plover Analytics — every archived 5% report on one company",
         name + ", securities code " + (d.issuer_sec_code || ""),
         "Each row is one report as filed at its own trigger date, not a running position.",
         "Source: 大量保有報告書 via EDINET, Financial Services Agency."],
        ["requirement_date", "filed_date", "filer_name", "filer_edinet_code",
         "report_type", "change_no", "shares_held", "shares_outstanding", "ratio_pct",
         "prior_ratio_pct", "ratio_change_pp", "important_proposal", "purpose_ja",
         "doc_id", "status"],
        reps.map(function (r) {
          return [r.requirement_date, r.filed_date, r.filer_name, r.filer_edinet_code,
                  r.report_type, r.change_no, r.shares_held, r.shares_outstanding,
                  r.ratio_pct, r.prior_ratio_pct, r.ratio_change_pp,
                  r.important_proposal, r.purpose_ja, r.doc_id, r.status];
        }));
    };

    function fact(label, value, unit, qual) {
      return "<div><dt>" + label + "</dt><dd>" + value +
        (unit ? "<span class='unit'>" + unit + "</span>" : "") +
        "<span class='qual'>" + qual + "</span></dd></div>";
    }
  }

  // ---- holder view ---------------------------------------------------------
  function renderHolder(d) {
    document.title = (d.name || d.holder_edinet_code) + " · 5% Filings · Plover Analytics";
    $("ho-name").textContent = d.name_en || d.name || d.holder_edinet_code;
    $("ho-code").textContent = d.holder_edinet_code || "";
    var prof = [];
    if (d.filer_type_en) prof.push("<b>" + esc(d.filer_type_en) + "</b>");
    if (d.business_ja) prof.push("Filed business: " + esc(d.business_ja));
    if (d.group && d.group_entities > 1) {
      prof.push("Part of <a href='stakes.html?group=" + encodeURIComponent(d.group) +
                "'>" + esc(d.group) + "</a> — " + d.group_entities +
                " filing entities in Japan");
    }
    $("ho-profile").innerHTML = prof.join(" · ");
    $("ho-meta").innerHTML = (d.name_en && d.name ? esc(d.name) + " · " : "") +
      count(d.issuers) + " companies reported on · " + count(d.reports_total) +
      " reports · <span class='badge badge-official'>Official statistic</span>";
    var cur = d.current || [];
    $("ho-count").textContent = cur.length < d.issuers
      ? cur.length + " of " + count(d.issuers) + " shown" : cur.length + " companies";
    $("ho-table").innerHTML =
      "<thead><tr><th>Company</th><th class=r>Shares</th><th class=r>Stake (%)</th>" +
      "<th class=r>Was (%)</th><th>Latest report</th><th>Important proposals</th>" +
      "<th>Stated purpose</th></tr></thead><tbody>" +
      cur.map(function (r) {
        return "<tr><td>" + nameCell(r.issuer_name_en, r.issuer_name_raw,
                 r.issuer_sec_code ? "stakes.html?c=" + esc(r.issuer_sec_code) : null) +
          "</td><td class=r>" + count(r.shares_held) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class=r>" + pct(r.prior_ratio_pct) +
          "</td><td>" + esc(day(r.requirement_date || r.filed_date)) +
          "</td><td>" + proposalBadge(r.important_proposal, null) +
          "</td><td class='purpose' title='" + esc(r.purpose_ja || "") + "'>" +
          esc(clip(r.purpose_ja)) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("ho-rep-count").textContent = d.reports.length < d.reports_total
      ? d.reports.length + " of " + count(d.reports_total) + " shown"
      : d.reports.length + " reports";
    $("ho-rep-table").innerHTML =
      "<thead><tr><th>Trigger date</th><th>Company</th><th>Report</th>" +
      "<th class=r>Shares</th><th class=r>Stake (%)</th><th class=r>Was (%)</th>" +
      "</tr></thead><tbody>" +
      d.reports.map(function (r) {
        return "<tr><td>" + esc(day(r.requirement_date || r.filed_date)) +
          "</td><td>" + nameCell(r.issuer_name_en, r.issuer_name_raw,
              r.issuer_sec_code ? "stakes.html?c=" + esc(r.issuer_sec_code) : null) +
          "</td><td>" + esc(REPORT_LABEL[r.report_type] || r.report_type) +
          "</td><td class=r>" + count(r.shares_held) +
          "</td><td class=r>" + pct(r.ratio_pct) +
          "</td><td class=r>" + pct(r.prior_ratio_pct) + "</td></tr>";
      }).join("") + "</tbody></table>";
    $("ho-csv").onclick = function () {
      csvDownload("5pct-holder-" + (d.holder_edinet_code || "holder") + ".csv",
        ["Plover Analytics — one holder's 5% reports",
         "Holder: " + (d.name || "") + (d.holder_edinet_code ? " (" + d.holder_edinet_code + ")" : ""),
         "Latest report per issuer. Each is a snapshot at its own trigger date.",
         "shares_held and ratio_pct are the filing group's position; holder_* is this holder's own line.",
         "Source: 大量保有報告書 via EDINET, Financial Services Agency."],
        ["issuer_sec_code", "issuer_name_ja", "issuer_name_en", "requirement_date",
         "filed_date", "shares_held", "ratio_pct", "prior_ratio_pct",
         "holder_shares_held", "holder_ratio_pct", "joint_holders",
         "important_proposal", "proposal_stance", "purpose_ja", "doc_id"],
        cur.map(function (r) {
          return [r.issuer_sec_code, r.issuer_name_raw, r.issuer_name_en,
                  r.requirement_date, r.filed_date, r.shares_held, r.ratio_pct,
                  r.prior_ratio_pct, r.holder_shares_held, r.holder_ratio_pct,
                  r.joint_holders, r.important_proposal, r.proposal_stance,
                  r.purpose_ja, r.doc_id];
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
    if (p.get("by") === "entity") {
      filerState.by = "entity";
      Array.prototype.forEach.call($("by-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-by") === "entity" ? "true" : "false");
      });
    }
    if (p.get("type")) filerState.type = p.get("type");
    if (p.get("group")) filerState.group = p.get("group");
    if (p.get("filers") === "activist") {
      filerState.activist = "true";
      Array.prototype.forEach.call($("filer-seg").querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-activist") ? "true" : "false");
      });
    }

    getJSON("/api/v1/equity/stakes/summary")
      .then(renderStrip).catch(function (e) { errorInto("stat-strip", e); });
    loadTape();
    loadFilers();

    $("tape-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      tapeState.filter = b.getAttribute("data-filter");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadTape();
    });
    $("by-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      filerState.by = b.getAttribute("data-by");
      filerState.group = "";
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      loadFilers();
    });
    $("type-select").onchange = function () {
      filerState.type = this.value;
      loadFilers();
    };
    getJSON("/api/v1/equity/stakes/holder-types").then(function (d) {
      $("type-select").innerHTML = "<option value=''>Every type</option>" +
        d.types.map(function (t) {
          return "<option value='" + esc(t.filer_type) + "'" +
            (t.filer_type === filerState.type ? " selected" : "") + ">" +
            esc(t.label) + " (" + fmtNum(t.filers, 0) + ")</option>";
        }).join("");
    }).catch(function () {});
    $("filer-seg").addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b) return;
      filerState.activist = b.getAttribute("data-activist");
      Array.prototype.forEach.call(this.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      syncUrl();
      loadFilers();
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
    getJSON("/api/v1/equity/stakes/company/" + encodeURIComponent(code))
      .then(renderCompany)
      .catch(function (e) {
        $("co-name").textContent = code;
        $("co-meta").textContent = e.message.indexOf("404") > -1
          ? "No archived 5% report names this company. Either no holder has crossed 5%, " +
            "or the reports predate this archive."
          : "Data unavailable — " + e.message + ". The last good state is unaffected.";
      });
  } else if (holderKey) {
    $("market-view").hidden = true;
    $("holder-view").hidden = false;
    getJSON("/api/v1/equity/stakes/holder/" + encodeURIComponent(holderKey))
      .then(renderHolder)
      .catch(function (e) {
        $("ho-name").textContent = holderKey;
        $("ho-meta").textContent = "Data unavailable — " + e.message;
      });
  } else {
    initMarket();
  }
})();
