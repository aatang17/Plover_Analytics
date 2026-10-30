/* AGM votes: how each resolution was voted, and how much support each named
   director received.
   Data: /api/v1/equity/agm/*. Vote counts and approval percentages are
   OFFICIAL, exactly as filed — this page never recomputes an approval
   percentage, because issuers stop counting attending votes once the outcome
   is settled and do not publish the base they used. Counts are voting rights
   (個), never shares. Missing renders as —, never 0.

   Names and resolution titles are filed in Japanese. Every one of them is
   shown in English where an English form EXISTS AS FILED — the issuer's own
   English name from its annual report cover, the issuer's own romanisation of
   a director, the statutory wording of a resolution rendered from a fixed
   table — with the filed Japanese kept underneath. Where no English form
   exists the Japanese stands alone, untranslated and unhidden: this page
   never invents an English name for a filing that does not carry one. */
(function () {
  "use strict";

  var dirState = { max: 100, kind: "election" };
  var propState = { cat: "", sh: "true" };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function pct(v) { return v == null ? MISSING : fmtNum(v, 2); }
  function day(iso) { return iso ? String(iso) : MISSING; }

  // Raw enum -> label. Never render a slug.
  var CATEGORY = {
    director_election: "Director election",
    audit_committee_election: "Audit-committee director election",
    statutory_auditor_election: "Statutory auditor election",
    accounting_auditor: "Accounting auditor",
    dividend: "Dividend / surplus",
    articles_amendment: "Articles amendment",
    compensation: "Remuneration",
    retirement_bonus: "Retirement bonus",
    capital_action: "Capital action",
    takeover_defence: "Takeover defence",
    shareholder_proposal: "Shareholder proposal",
    dismissal: "Dismissal",
    reorganisation: "Reorganisation",
    accounts_approval: "Accounts approval",
    other: "Other",
  };
  function catLabel(v) { return CATEGORY[v] || (v ? esc(v) : MISSING); }

  // Meeting types, as EDINET records them. A fixed lookup, never a translation.
  var MEETING_TYPE = {
    "定時株主総会": "Annual general meeting",
    "臨時株主総会": "Extraordinary general meeting",
  };
  function meetingType(v) {
    if (!v) return "General meeting";
    return MEETING_TYPE[v] || esc(v);
  }

  /* English on top, the filed Japanese underneath. When only the Japanese
     exists it stands on its own line — never a blank, never a guess. */
  function bilingual(en, ja, cls) {
    var main = en || ja || MISSING;
    // A non-Japanese director is filed under the same Latin name in both
    // fields. Printing it twice would look like a rendering fault.
    var same = en && ja && en.replace(/\s+/g, "") === ja.replace(/\s+/g, "");
    return "<span class='" + (cls || "") + "'>" + esc(main) + "</span>";
  }

  var RESULT = { "可決": "Carried", "否決": "Rejected" };
  function resultLabel(v) {
    if (!v) return MISSING;
    var en = RESULT[v];
    return en ? "<span class='nowrap'>" + en + "</span>" : esc(v);
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

  function errorInto(id, e) {
    var el = $(id);
    if (el) el.innerHTML = "<p class='sec-note'>Data unavailable — " + esc(e.message) + "</p>";
  }

  function companyCell(r) {
    var en = r.issuer_name_en || r.name_en, ja = r.issuer_name || r.name;
    var name = en || ja || MISSING;
    var code = r.sec_code;
    var inner = code ? "<a href='agm.html?company=" + esc(code) + "'>" + esc(name) + "</a>"
                     : esc(name);
    return "<div class='cell-item'>" + inner + "</div>";
  }

  // A filing that stopped short of a full tally is marked, because it is why
  // the percentage cannot be rebuilt from the counts beside it.
  function tallyBadge(partial) {
    if (!partial) return "";
    return " <span class='badge badge-note' title='This company counted advance " +
      "votes plus enough of the votes in the room to settle the outcome, then " +
      "stopped, and disclosed that it did. The percentage is as filed; the base " +
      "behind it is not published.'>Partial tally</span>";
  }

  // ---- stat strip ----------------------------------------------------------
  function renderStrip(d) {
    var da = d.director_approval || {};
    var sp = d.shareholder_proposals || {};
    function cell(label, value, foot) {
      return "<div class='strip-cell'><div class='strip-label'>" + label +
        "</div><div class='strip-value num'>" + value +
        "</div><div class='strip-foot'>" + foot + "</div></div>";
    }
    $("stat-strip").innerHTML = "<div class='strip-grid'>" +
      cell("MEETINGS", count(d.meetings),
           day(d.earliest_meeting) + " to " + day(d.latest_meeting)) +
      cell("COMPANIES", count(d.companies), "with at least one meeting on file") +
      cell("DIRECTOR RESULTS", count(da.results),
           "median " + pct(da.median_pct) + "% approval") +
      cell("BELOW 80% SUPPORT", count(da.below_80),
           count(da.below_50) + " below 50%") +
      cell("SHAREHOLDER PROPOSALS", count(sp.n),
           count(sp.rejected) + " rejected") +
      "</div>";
  }

  // ---- distribution chart --------------------------------------------------
  var distChart = null, distData = null;

  function drawDist() {
    if (!distData || !distData.length) return;
    var el = $("dist-chart");
    if (!el) return;
    if (distChart) distChart.dispose();
    distChart = echarts.init(el, null, { renderer: "canvas" });
    var ink = cssVar("--obs-ink"), muted = cssVar("--obs-text-muted");
    var grid = cssVar("--obs-grid"), primary = cssVar("--obs-primary");
    distChart.setOption({
      // no chart border, no background fill, horizontal gridlines only
      grid: { left: 8, right: 16, top: 16, bottom: 28, containLabel: true },
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        formatter: function (ps) {
          var p = ps[0];
          return p.name + "% approval<br/><b>" + fmtNum(p.value, 0) +
            "</b> director results";
        },
      },
      xAxis: {
        type: "category",
        data: distData.map(function (b) { return b.bucket; }),
        axisLine: { lineStyle: { color: grid } },
        axisTick: { show: false },
        axisLabel: { color: muted, fontSize: 11 },
        name: "Approval, % of votes counted (as filed)",
        nameLocation: "middle", nameGap: 30,
        nameTextStyle: { color: muted, fontSize: 11 },
      },
      yAxis: {
        type: "value",
        // bars start at zero, always
        min: 0,
        splitLine: { lineStyle: { color: grid } },
        axisLabel: {
          color: muted, fontSize: 11,
          formatter: function (v) { return fmtNum(v, 0); },
        },
        name: "Director results",
        nameTextStyle: { color: muted, fontSize: 11, align: "left" },
      },
      series: [{
        type: "bar", barMaxWidth: 62,
        itemStyle: { color: primary },
        // The 95-100 bucket is 45,009 against 155 in the tail, so on an honest
        // linear scale the tail is a hairline. Printing each count keeps the
        // scale truthful and the small buckets readable — a log axis would
        // flatter them into looking comparable.
        label: {
          show: true, position: "top", color: muted, fontSize: 11,
          formatter: function (p) { return fmtNum(p.value, 0); },
        },
        data: distData.map(function (b) { return b.n; }),
      }],
      textStyle: { color: ink },
    });
  }

  // ---- lowest support ------------------------------------------------------
  function renderDirectors(d) {
    var rows = d.rows || [];
    $("dir-count").textContent = fmtNum(rows.length, 0) + " results";
    $("dir-meta").innerHTML =
      esc(d.kind_note || "") + " Ranked by the approval percentage each company " +
      "filed, lowest first. Vote counts are voting rights (個), not shares. " +
      "<span class='badge badge-official'>Official statistic</span>";
    var head = "<thead><tr>" +
      "<th>Company</th><th>Meeting</th><th>Director</th><th>Proposal</th>" +
      "<th class='r'>For</th><th class='r'>Against</th><th class='r'>Abstain</th>" +
      "<th class='r'>Approval (%)</th><th>Result</th>" +
      "</tr></thead>";
    var body = rows.map(function (r) {
      return "<tr>" +
        "<td>" + companyCell(r) + "</td>" +
        "<td class='nowrap'>" + day(r.meeting_date) + "</td>" +
        "<td>" + bilingual(r.candidate_name_en, r.candidate_name, "who") +
          (r.shareholder_proposal ? " <span class='badge badge-warn' title='Nominated " +
            "by a shareholder, not by the board. These are routinely opposed by the " +
            "board and routinely fail, so a low figure here is the proposal losing — " +
            "not a sitting director losing support.'>Shareholder nominee</span>" : "") +
          "</td>" +
        "<td class='cell-item'>" + catLabel(r.category) + tallyBadge(r.partial_tally) + "</td>" +
        "<td class='r'>" + count(r.for_votes) + "</td>" +
        "<td class='r'>" + count(r.against_votes) + "</td>" +
        "<td class='r'>" + count(r.abstain_votes) + "</td>" +
        "<td class='r pct' data-sort='" + (r.approval_pct == null ? -1 : r.approval_pct) +
          "'>" + pct(r.approval_pct) + "</td>" +
        "<td>" + resultLabel(r.result) + "</td>" +
        "</tr>";
    }).join("");
    $("dir-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    var calc = (d.calc || {});
    $("dir-formula").innerHTML =
      "<summary>Show calculation</summary>" +
      "<p><b>Approval (%)</b> is <b>not calculated here</b> — it is the figure the " +
      "company filed (賛成割合). " + esc(calc.approval_pct || "") + ".</p>" +
      "<p>Dividing the disclosed counts gives a different, platform-derived number: " +
      "<code>100 × For ÷ (For + Against + Abstain)</code>. It is served as " +
      "<code>approval_pct_of_counted</code> and is not shown in this table, because " +
      "presenting our arithmetic beside the company's would invite the two to be " +
      "read as the same measure.</p>";

    $("dir-csv").onclick = function () {
      csvDownload("agm-lowest-support.csv", [
        "Plover Analytics - AGM voting results, director elections",
        "Source: " + (d.provenance ? d.provenance.note : ""),
        "approval_pct: " + (calc.approval_pct || ""),
        "Vote counts are voting rights (kobetsu, " + "個" + "), not shares.",
        (d.tally_note || ""),
        "Filter: approval <= " + dirState.max + "%",
        (d.director_name_note || ""),
      ], ["sec_code", "issuer_name", "issuer_name_en", "meeting_date",
          "candidate_name", "candidate_name_en", "proposal", "proposal_en",
          "category", "for_votes", "against_votes", "abstain_votes",
          "approval_pct_filed", "result", "partial_tally", "doc_id"],
        rows.map(function (r) {
          return [r.sec_code, r.issuer_name, r.issuer_name_en, r.meeting_date,
                  r.candidate_name, r.candidate_name_en, r.proposal, r.proposal_en,
                  r.category, r.for_votes, r.against_votes, r.abstain_votes,
                  r.approval_pct, r.result, r.partial_tally, r.doc_id];
        }));
    };
  }

  function loadDirectors() {
    $("dir-table").innerHTML = "";
    getJSON("/api/v1/equity/agm/directors?order=" +
            (dirState.kind === "dismissal" ? "highest" : "lowest") +
            "&limit=200&kind=" + dirState.kind + "&max_pct=" + dirState.max)
      .then(renderDirectors).catch(function (e) { errorInto("dir-meta", e); });
  }

  // ---- contested business --------------------------------------------------
  function renderProposals(d) {
    var rows = d.rows || [];
    $("prop-count").textContent = fmtNum(rows.length, 0) + " resolutions";
    var shown = rows.length, englished = rows.filter(function (r) {
      return r.label_en; }).length;
    $("prop-meta").innerHTML =
      "Resolutions of the kind that get argued about. A board election shows no " +
      "single figure — the filing publishes one per candidate — so those rows read " +
      "— and the detail is above. Resolution titles are filed in Japanese; the " +
      "standard statutory wording is shown in English above the filed title" +
      (shown ? " (" + fmtNum(englished, 0) + " of " + fmtNum(shown, 0) + " here)" : "") +
      ", and a non-standard title stands as filed. " +
      "<span class='badge badge-official'>Official statistic</span>";
    var head = "<thead><tr>" +
      "<th>Company</th><th>Meeting</th><th>Resolution</th><th>Type</th>" +
      "<th class='r'>For</th><th class='r'>Against</th><th class='r'>Abstain</th>" +
      "<th class='r'>Approval (%)</th><th>Result</th>" +
      "</tr></thead>";
    var body = rows.map(function (r) {
      return "<tr>" +
        "<td>" + companyCell(r) + "</td>" +
        "<td class='nowrap'>" + day(r.meeting_date) + "</td>" +
        "<td class='cell-item'" + (r.candidates ? " title='" + fmtNum(r.candidates, 0) +
            (r.candidates === 1 ? " candidate" : " candidates") + " voted individually'" : "") + ">" +
          bilingual(r.label_en, r.label) + "</td>" +
        "<td class='cell-item'>" + catLabel(r.category) + tallyBadge(r.partial_tally) + "</td>" +
        "<td class='r'>" + count(r.for_votes) + "</td>" +
        "<td class='r'>" + count(r.against_votes) + "</td>" +
        "<td class='r'>" + count(r.abstain_votes) + "</td>" +
        "<td class='r pct' data-sort='" + (r.approval_pct == null ? -1 : r.approval_pct) +
          "'>" + pct(r.approval_pct) + "</td>" +
        "<td>" + resultLabel(r.result) + "</td>" +
        "</tr>";
    }).join("");
    $("prop-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("prop-csv").onclick = function () {
      csvDownload("agm-proposals.csv", [
        "Plover Analytics - AGM voting results, resolutions",
        "Source: " + (d.provenance ? d.provenance.note : ""),
        "approval_pct is as filed, not recomputed.",
        (d.election_note || ""),
        (d.label_note || ""),
      ], ["sec_code", "issuer_name", "issuer_name_en", "meeting_date",
          "proposal_no", "label", "label_en", "category", "shareholder_proposal",
          "for_votes", "against_votes", "abstain_votes", "approval_pct_filed",
          "result", "doc_id"],
        rows.map(function (r) {
          return [r.sec_code, r.issuer_name, r.issuer_name_en, r.meeting_date,
                  r.proposal_no, r.label, r.label_en, r.category,
                  r.shareholder_proposal, r.for_votes, r.against_votes,
                  r.abstain_votes, r.approval_pct, r.result, r.doc_id];
        }));
    };
  }

  function loadProposals() {
    $("prop-table").innerHTML = "";
    var u = "/api/v1/equity/agm/proposals?limit=200&category=" +
      encodeURIComponent(propState.cat) + "&shareholder=" + propState.sh;
    getJSON(u).then(renderProposals).catch(function (e) { errorInto("prop-meta", e); });
  }

  // ---- by proposal type ----------------------------------------------------
  function renderCategories(d) {
    var rows = (d.by_category || []).filter(function (r) { return r.category; });
    $("cat-count").textContent = fmtNum(rows.length, 0) + " types";
    var head = "<thead><tr><th>Proposal type</th><th class='r'>Resolutions</th>" +
      "<th class='r'>Median approval (%)</th></tr></thead>";
    $("cat-table").innerHTML = head + "<tbody>" + rows.map(function (r) {
      return "<tr><td>" + catLabel(r.category) + "</td>" +
        "<td class='r'>" + count(r.n) + "</td>" +
        "<td class='r pct'>" + pct(r.median_approval_pct) + "</td></tr>";
    }).join("") + "</tbody>";
  }

  // ---- company view --------------------------------------------------------
  function renderCompany(d) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    var ms = d.meetings || [];
    $("co-name").textContent = d.name_en || d.name || d.sec_code;
    $("co-code").textContent = d.sec_code +
      (d.name_en && d.name ? " · " + d.name : "");
    // Matches the served title for this address; see app/prerender.py.
    document.title = (d.name_en || d.name || d.sec_code) + " (" + d.sec_code +
      ") \u2014 AGM Voting Results \u00b7 Plover Analytics";
    $("co-meta").innerHTML = fmtNum(ms.length, 0) + " meeting" +
      (ms.length === 1 ? "" : "s") + " on file · " +
      "<span class='badge badge-official'>Official statistic</span>";
    $("co-meetings").innerHTML = ms.map(function (m) {
      var rows = (m.proposal_rows || []).map(function (p) {
        var main = "<tr>" +
          "<td class='cell-item'>" + bilingual(p.label_en, p.label) + "</td>" +
          "<td class='cell-item'>" + catLabel(p.category) + "</td>" +
          "<td class='r'>" + count(p.for_votes) + "</td>" +
          "<td class='r'>" + count(p.against_votes) + "</td>" +
          "<td class='r'>" + count(p.abstain_votes) + "</td>" +
          "<td class='r pct'>" + pct(p.approval_pct) + "</td>" +
          "<td>" + resultLabel(p.result) + "</td></tr>";
        var kids = (p.directors || []).map(function (v) {
          return "<tr>" +
            "<td class='cell-item' style='padding-left:26px'>" +
              bilingual(v.candidate_name_en, v.candidate_name, "who") + "</td>" +
            "<td class='cell-item'>Candidate</td>" +
            "<td class='r'>" + count(v.for_votes) + "</td>" +
            "<td class='r'>" + count(v.against_votes) + "</td>" +
            "<td class='r'>" + count(v.abstain_votes) + "</td>" +
            "<td class='r pct'>" + pct(v.approval_pct) + "</td>" +
            "<td>" + resultLabel(v.result) + "</td></tr>";
        }).join("");
        return main + kids;
      }).join("");
      return "<div class='meeting'>" +
        "<h3>" + (m.meeting_date ? day(m.meeting_date)
                                 : "Filed " + day(m.filed_date)) +
          " · " + meetingType(m.meeting_type) + "</h3>" +
        (m.meeting_date ? "" : "<p class='mt-meta'>The filing does not state " +
          "the meeting date; the date shown is when it was filed.</p>") +
        "<p class='mt-meta'>" + fmtNum(m.proposals, 0) + " resolutions · " +
          fmtNum(m.candidates, 0) + " director results" + tallyBadge(m.partial_tally) + "</p>" +
        "<div class='table-wrap'><table class='tbl-agm'><thead><tr>" +
        "<th>Resolution</th><th>Type</th><th class='r'>For</th><th class='r'>Against</th>" +
        "<th class='r'>Abstain</th><th class='r'>Approval (%)</th><th>Result</th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div></div>";
    }).join("");
  }

  // ---- search --------------------------------------------------------------
  /* The box takes either language, because the reader may have the name from a
     filing (Japanese) or from a broker screen (English). It searches only
     issuers that have a meeting on file: a hit that led to an empty page would
     read as broken data rather than as the coverage gap it is. */
  function renderSearch(d) {
    var rows = d.companies || [];
    if (!rows.length) {
      $("search-results").innerHTML = "<p class='sec-note'>No issuer with a " +
        "meeting on file matches that. Coverage begins in April 2024.</p>";
      return;
    }
    $("search-results").innerHTML =
      "<div class='table-wrap'><table class='tbl-agm'><thead><tr>" +
      "<th>Company</th><th class='r'>Meetings</th><th class='r'>Director results</th>" +
      "<th>Latest meeting</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr><td>" + companyCell(r) + delistedTag(r) + "</td>" +
          "<td class='r'>" + count(r.meetings) + "</td>" +
          "<td class='r'>" + count(r.director_results) + "</td>" +
          "<td class='nowrap'>" + day(r.latest_meeting) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function runSearch(q) {
    q = (q || "").trim();
    if (q.length < 1) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/agm/companies?q=" + encodeURIComponent(q))
      .then(renderSearch).catch(function (e) { errorInto("search-results", e); });
  }

  // ---- boot ----------------------------------------------------------------
  function seg(id, apply) {
    var box = $(id);
    if (!box) return;
    box.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-max],button[data-cat],button[data-kind]");
      if (!b) return;
      Array.prototype.forEach.call(box.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      apply(b);
    });
  }

  function boot() {
    var params = new URLSearchParams(location.search);
    var company = params.get("company");
    if (company) {
      getJSON("/api/v1/equity/agm/company/" + encodeURIComponent(company))
        .then(renderCompany)
        .catch(function (e) { errorInto("co-meetings", e); $("market-view").hidden = true;
                              $("company-view").hidden = false; });
      return;
    }
    getJSON("/api/v1/equity/agm/summary").then(function (d) {
      renderStrip(d);
      renderCategories(d);
      distData = d.approval_distribution || [];
      $("dist-count").textContent = fmtNum(
        (d.director_approval || {}).results || 0, 0) + " results";
      $("dist-source").innerHTML =
        "Source: 臨時報告書 via EDINET · " +
        day(d.earliest_meeting) + " to " + day(d.latest_meeting) +
        " · <span class='badge badge-official'>Official statistic</span>";
      drawDist();
    }).catch(function (e) { errorInto("stat-strip", e); });

    loadDirectors();
    loadProposals();

    var box = $("q"), timer = null;
    if (box) {
      box.addEventListener("input", function () {
        clearTimeout(timer);
        var v = box.value;
        timer = setTimeout(function () { runSearch(v); }, 180);
      });
    }

    seg("kind-seg", function (b) {
      dirState.kind = b.getAttribute("data-kind") || "election";
      loadDirectors();
    });
    seg("dir-seg", function (b) {
      dirState.max = Number(b.getAttribute("data-max"));
      loadDirectors();
    });
    seg("prop-seg", function (b) {
      propState.cat = b.getAttribute("data-cat") || "";
      propState.sh = b.getAttribute("data-sh") || "";
      loadProposals();
    });

    window.addEventListener("resize", function () { if (distChart) distChart.resize(); });
    if (window.initThemeToggle) initThemeToggle(function () { drawDist(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();
