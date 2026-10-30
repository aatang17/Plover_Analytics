/* Facilities & Land: the map of everything companies disclose in 主要な設備の状況,
   and the hidden-land screen. Data: /api/v1/equity/facilities/*.

   Every yen and every ㎡ is official as filed. Two derived things, each carrying
   its formula: book ¥ per ㎡ (land book ÷ disclosed area) and the map positions
   (city-level filed address → municipality centroid, Geolonia CC BY 4.0 — a
   national-map position, never a parcel). Book value is historical cost, not
   market value, and the page must say so wherever the number leads.
   Missing renders as —, never 0. */
(function () {
  "use strict";

  var state = { sizeBy: "land_book", minLand: 1e9, metric: "yen_per_m2",
                rmetric: "unrealized" };
  var mapChart = null;
  var mapData = null;      // /map payload, decoded to objects
  var rankData = null;
  var rentData = null;
  var mapReady = false;    // japan geojson registered

  function $(id) { return document.getElementById(id); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  /* Yen at the scale of the value, unit always attached: ¥2mn must never
     render as ¥0.0bn — a rounded-to-zero figure reads as no land at all. */
  function yenBn(v, dp) {
    if (v == null) return MISSING;
    if (v < 1e9) return "¥" + fmtNum(v / 1e6, 0) + "mn";
    return "¥" + fmtNum(v / 1e9, dp == null ? 1 : dp) + "bn";
  }
  function yenTn(v) { return v == null ? MISSING : "¥" + fmtNum(v / 1e12, 2) + "tn"; }
  /* Unrealized gains can be negative (impaired property below book) — the
     sign leads with a true minus, never buried inside the number. */
  function yenSigned(v) {
    if (v == null) return MISSING;
    return v < 0 ? "−" + yenBn(-v) : yenBn(v);
  }
  /* Land areas span 6 orders of magnitude; pick the unit per value but always
     print it. km² only when it genuinely is square kilometres. */
  function areaFmt(v) {
    if (v == null) return MISSING;
    if (v >= 1e6) return fmtNum(v / 1e6, 1) + " km²";
    if (v >= 1e4) return fmtNum(v / 1e4, 1) + " ha";
    return fmtNum(v, 0) + " ㎡";
  }
  function yenPerM2(v) { return v == null ? MISSING : "¥" + fmtNum(v, v < 100 ? 1 : 0) + "/㎡"; }

  /* A row filed in a foreign currency shows its own symbol and is never
     converted — no exchange rate is an official input here. */
  var CURRENCY_SYMBOL = { "米ドル": "US$", "ドル": "US$", "ユーロ": "€",
                          "元": "CN¥", "ウォン": "₩", "ポンド": "£" };

  /* Derived use categories (API classifies from the filer's own text; the
     filed segment/contents always render as filed next to the label).
     Unclassified is — like any other missing value. */
  var USE_LABELS = { production: "Production", office: "Offices & branches",
    retail: "Stores & sales", logistics: "Logistics & warehouses",
    rental: "Rental & real estate", rnd: "R&D", transport: "Rail & transport",
    hotel: "Hotels & leisure", housing: "Housing & welfare",
    energy: "Energy & utilities", idle: "Idle" };
  var NONCORE = { rental: true, housing: true, idle: true };
  function useLabel(u) { return u ? (USE_LABELS[u] || u) : null; }
  function moneyFmt(v, currency) {
    if (v == null) return MISSING;
    if (!currency || currency === "JPY") return yenBn(v);
    var sym = CURRENCY_SYMBOL[currency] || currency + " ";
    if (v >= 1e9) return sym + fmtNum(v / 1e9, 1) + "bn";
    return sym + fmtNum(v / 1e6, 0) + "mn";
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

  var SOURCE_LINE = "Source: company annual securities reports (EDINET), figures as filed. " +
    "Positions: municipality centroids (Geolonia Japanese-addresses, CC BY 4.0). " +
    "Land at historical-cost book value, not market value.";

  /* ---- map ---- */

  function pal() {
    return {
      ink: cssVar("--obs-ink"), muted: cssVar("--obs-text-muted"),
      border: cssVar("--obs-border"), grid: cssVar("--obs-grid"),
      surface: cssVar("--obs-surface"), subtle: cssVar("--obs-surface-subtle"),
      primary: cssVar("--obs-primary"),
    };
  }

  function dotValue(d) {
    var land = (d.land_yen || 0) + (d.trust_land_yen || 0);
    return state.sizeBy === "land_area" ? (d.land_area_m2 || 0) : land;
  }

  function visibleDots() {
    return mapData.filter(function (d) {
      var land = (d.land_yen || 0) + (d.trust_land_yen || 0);
      return land >= state.minLand || (state.sizeBy === "land_area" && (d.land_area_m2 || 0) > 0 && state.minLand === 0);
    });
  }

  /* sqrt scaling: a dot's AREA tracks the value, so a 100× larger holding
     reads as a visibly, not absurdly, larger dot */
  function symbolSizer(dots) {
    var max = 0;
    dots.forEach(function (d) { max = Math.max(max, dotValue(d)); });
    var scale = max > 0 ? 26 / Math.sqrt(max) : 1;
    return function (val) { return Math.max(2.5, Math.sqrt(val[2] || 0) * scale); };
  }

  function mapOption(p) {
    var dots = visibleDots();
    var size = symbolSizer(dots);
    return {
      backgroundColor: "transparent",
      geo: {
        map: "japan", roam: true, aspectScale: 0.85,
        // the wheel zooms the map while the reader is trying to scroll the
        // page — never let that shrink it below the fitted view
        scaleLimit: { min: 1, max: 60 },
        itemStyle: { areaColor: p.subtle, borderColor: p.border, borderWidth: 0.6 },
        emphasis: { disabled: true },
        label: { show: false },
        left: 10, right: 10, top: 10, bottom: 10,
      },
      tooltip: {
        trigger: "item", confine: true,
        backgroundColor: p.surface, borderColor: p.border, textStyle: { color: p.ink, fontSize: 12 },
        formatter: function (q) {
          var d = q.data.meta;
          var land = (d.land_yen || 0) + (d.trust_land_yen || 0);
          return "<b>" + esc(d.company) + "</b>" + (d.sec_code ? " · " + esc(d.sec_code) : "") +
            "<br>" + esc(d.name || d.location || "") +
            "<br>" + esc(d.location_en || d.muni_name || "") +
            (d.use ? "<br>Use: " + esc(useLabel(d.use)) : "") +
            "<br>Land (book): " + (land ? yenBn(land) : MISSING) +
            " · Area: " + areaFmt(d.land_area_m2) +
            (land && d.land_area_m2 ? "<br>Book ¥/㎡: " + yenPerM2(land / d.land_area_m2) : "");
        },
      },
      series: [{
        type: "scatter", coordinateSystem: "geo",
        data: dots.map(function (d) {
          return { value: [d.lng, d.lat, dotValue(d)], meta: d };
        }),
        symbolSize: size,
        itemStyle: { color: p.primary, opacity: 0.55 },
        emphasis: { itemStyle: { opacity: 0.95 } },
      }],
    };
  }

  function drawMap() {
    if (!mapReady || !mapData) return;
    var el = $("fac-map");
    if (mapChart) { mapChart.dispose(); }
    mapChart = echarts.init(el, null, { renderer: "canvas" });
    mapChart.setOption(mapOption(pal()));
    mapChart.on("click", function (q) {
      var d = q.data && q.data.meta;
      if (d && d.sec_code) { location.href = "facilities.html?code=" + encodeURIComponent(d.sec_code); }
    });
    var dots = visibleDots();
    $("map-qualifier").textContent = fmtNum(dots.length, 0) + " facilities · " +
      (state.sizeBy === "land_area" ? "sized by land area" : "sized by land book value");
  }

  /* PNG export renders on the LIGHT palette regardless of the viewer's theme,
     with the source line burned in — screenshots leave the page. */
  function renderMapPNG() {
    var off = document.createElement("div");
    off.style.cssText = "position:fixed;left:-10000px;width:1200px;height:800px";
    document.body.appendChild(off);
    var light = { ink: "#0f172a", muted: "#64748b", border: "#e2e8f0", grid: "#eef2f7",
                  surface: "#ffffff", subtle: "#f8fafc", primary: "#1a4d8f" };
    var tmp = echarts.init(off, null, { renderer: "canvas" });
    var opt = mapOption(light);
    opt.backgroundColor = "#ffffff";
    opt.title = {
      text: "Plover Analytics — Facilities & Land",
      subtext: SOURCE_LINE, left: 12, top: 8,
      textStyle: { color: light.ink, fontSize: 15 },
      subtextStyle: { color: light.muted, fontSize: 10, width: 1100, overflow: "break" },
    };
    tmp.setOption(opt);
    var url = tmp.getDataURL({ pixelRatio: 2, backgroundColor: "#ffffff" });
    tmp.dispose();
    document.body.removeChild(off);
    return url;
  }

  /* A map has a shape of its own, so it offers copy and download at its own
     size rather than the slide and column presets a time series takes. */
  function exportMapMenu() {
    obsExportMenu("japan-facilities-map.png", renderMapPNG, $("map-png"),
      { w: 1200, h: 800, out: 2400 });
  }

  /* ---- zoom buttons ---- */

  function zoomBy(chart, factor, min, max) {
    if (!chart) return;
    var g = (chart.getOption().geo || [])[0];
    if (!g) return;
    var z = Math.min(max, Math.max(min, (g.zoom || 1) * factor));
    chart.setOption({ geo: { zoom: z } });
  }

  function wireZoom(idIn, idOut, getChart, max) {
    var bi = $(idIn), bo = $(idOut);
    if (!bi) return;
    bi.addEventListener("click", function () { zoomBy(getChart(), 1.5, 1, max); });
    bo.addEventListener("click", function () { zoomBy(getChart(), 1 / 1.5, 1, max); });
  }

  /* ---- search ---- */

  function nameCell(en, ja, href) {
    var primary = en || ja || MISSING;
    var link = href ? "<a href='" + href + "'>" + esc(primary) + "</a>" : esc(primary);
    return "<div class='cell-name'><div>" + link + "</div></div>";
  }

  function runSearch(q) {
    if (!q) { $("search-results").innerHTML = ""; return; }
    getJSON("/api/v1/equity/facilities/companies?q=" + encodeURIComponent(q))
      .then(function (d) {
        if (!d.companies.length) {
          $("search-results").innerHTML = "<p class='sec-note'>No company with an " +
            "extracted facilities section matches that. Coverage is listed filers' " +
            "annual reports; a filing whose section could not be parsed is not here.</p>";
          return;
        }
        $("search-results").innerHTML =
          '<div style="overflow-x:auto"><table class="tbl-fac"><thead><tr><th>Company</th>' +
          '<th class="r">Land book</th><th class="r">Land area</th>' +
          '<th class="r">Facilities</th><th class="r nw">FY</th><th>Status</th></tr></thead><tbody>' +
          d.companies.map(function (c) {
            return "<tr><td>" +
              nameCell(c.name_en, c.name, "facilities.html?code=" + esc(c.sec_code)) + delistedTag(c) + "</td>" +
              '<td class="r">' + yenBn(c.land_book_yen) + "</td>" +
              '<td class="r">' + areaFmt(c.land_area_m2) + "</td>" +
              '<td class="r">' + (c.n_rows == null ? MISSING : fmtNum(c.n_rows, 0)) + "</td>" +
              '<td class="r nw">' + esc(c.year || MISSING) + "</td>" +
              "<td>" + (c.status === "clean" ? "reconciled"
                        : "as filed, gate failed") + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      });
  }

  /* ---- market view ---- */

  function fact(label, valueHTML, qual) {
    return "<div><dt>" + esc(label) + "</dt><dd>" + valueHTML +
      (qual ? '<span class="qual">' + esc(qual) + "</span>" : "") + "</dd></div>";
  }

  function renderFacts(s) {
    $("market-facts").innerHTML =
      fact("Companies covered", fmtNum(s.companies, 0), "latest clean filing per company") +
      fact("Facilities disclosed", fmtNum(s.facility_rows, 0),
           fmtNum(s.facility_rows_geocoded, 0) + " mapped to a municipality") +
      fact("Disclosed land, book", yenTn(s.land_book_yen), "historical cost, as filed") +
      fact("Disclosed land area", areaFmt(s.land_area_m2),
           "fiscal periods to " + (s.last_period_end || MISSING));
  }

  function renderRanking() {
    var rows = rankData.companies;
    var head =
      "<thead><tr><th>#</th><th>Company</th>" +
      '<th class="r">Land book</th><th class="r">Land area</th>' +
      '<th class="r">Book ¥/㎡</th><th class="r">BS land</th>' +
      '<th class="r">Not itemised</th><th class="r nw">Period end</th></tr></thead>';
    var body = rows.map(function (r, i) {
      var name = esc(r.company || r.sec_code || "");
      var link = r.sec_code
        ? '<a href="facilities.html?code=' + esc(r.sec_code) + '">' + name + "</a>" : name;
      return "<tr><td class=r>" + (i + 1) + "</td>" +
        '<td class="cell-name">' + link + "</td>" +
        '<td class="r">' + yenBn(r.land_book_yen) + "</td>" +
        '<td class="r">' + areaFmt(r.land_area_m2) + "</td>" +
        '<td class="r">' + yenPerM2(r.yen_per_m2) + "</td>" +
        '<td class="r">' + yenBn(r.bs_land_yen) + "</td>" +
        '<td class="r">' + yenBn(r.unlisted_land_yen) + "</td>" +
        '<td class="r nw">' + esc(r.period_end || MISSING) + "</td></tr>";
    }).join("");
    $("rank-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("rank-count").textContent = rows.length + " companies";
    $("rank-calc").innerHTML =
      "<b>Book ¥/㎡</b> = disclosed land book value (¥) ÷ disclosed land area (㎡), " +
      "both exactly as filed in the company's 主要な設備の状況. Only companies whose " +
      "filing discloses both can appear. <b>Not itemised</b> = consolidated " +
      "balance-sheet land − land disclosed in the facilities section: the land the " +
      "filing chose not to name, computed only where the extraction reconciled " +
      "against the balance sheet (— for IFRS filers, whose figure is parent-only). " +
      "Land book value is historical cost under JGAAP — not a market value, and no " +
      "market estimate is made.";
  }

  /* ---- rental property at market (賃貸等不動産) ---- */

  function ratioFmt(v) { return v == null ? MISSING : fmtNum(v, 2) + "×"; }

  function renderRental() {
    var rows = rentData.companies;
    var head =
      "<thead><tr><th>#</th><th>Company</th>" +
      '<th class="r">Carrying (book)</th><th class="r">Fair value</th>' +
      '<th class="r">Unrealized</th><th class="r">Fair ÷ book</th>' +
      '<th class="r nw">Period end</th></tr></thead>';
    var body = rows.map(function (r, i) {
      var name = esc(r.company || r.sec_code || "");
      var link = r.sec_code
        ? '<a href="facilities.html?code=' + esc(r.sec_code) + '">' + name + "</a>" : name;
      return "<tr><td class=r>" + (i + 1) + "</td>" +
        '<td class="cell-name">' + link + "</td>" +
        '<td class="r">' + yenBn(r.carrying_yen) + "</td>" +
        '<td class="r">' + yenBn(r.fair_value_yen) + "</td>" +
        '<td class="r">' + yenSigned(r.unrealized_yen) + "</td>" +
        '<td class="r">' + ratioFmt(r.fair_to_book) + "</td>" +
        '<td class="r nw">' + esc(r.period_end || MISSING) + "</td></tr>";
    }).join("");
    $("rent-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("rent-calc").innerHTML =
      "<b>Unrealized</b> = fair value − carrying amount; <b>Fair ÷ book</b> = fair " +
      "value ÷ carrying amount. Both inputs are exactly as disclosed in the " +
      "company's 賃貸等不動産 note: the carrying amount is acquisition cost less " +
      "depreciation, the fair value is the filer's own year-end 時価 (mostly " +
      "appraisal-based). Dual-use property is disclosed at the whole property's " +
      "amounts. This platform estimates nothing.";
  }

  function loadRental() {
    getJSON("/api/v1/equity/facilities/rental/ranking?metric=" +
            encodeURIComponent(state.rmetric) + "&limit=50")
      .then(function (r) { rentData = r; renderRental(); });
  }

  function marketView(params) {
    state.sizeBy = params.get("size") || state.sizeBy;
    state.minLand = params.has("min") ? Number(params.get("min")) : state.minLand;
    state.metric = params.get("metric") || state.metric;
    state.rmetric = params.get("rmetric") || state.rmetric;
    $("size-by").value = state.sizeBy;
    $("min-land").value = String(state.minLand);
    $("rank-metric").value = state.metric;
    $("rent-metric").value = state.rmetric;

    getJSON("/api/v1/equity/facilities/summary").then(function (r) {
      renderFacts(r.summary);
    });
    getJSON("/api/v1/equity/facilities/rental/summary").then(function (r) {
      var s = r.summary;
      $("rent-count").textContent = fmtNum(s.companies, 0) + " companies";
      $("rent-facts").innerHTML =
        fact("Companies disclosing", fmtNum(s.companies, 0), "clean extractions only") +
        fact("Carrying amount", yenTn(s.carrying_yen), "book, as disclosed") +
        fact("Fair value", yenTn(s.fair_value_yen), "year-end 時価, as disclosed") +
        fact("Unrealized gain", yenTn(s.unrealized_yen), "derived: fair − carrying");
    });
    loadRental();
    getJSON("/api/v1/equity/facilities/map").then(function (r) {
      mapData = r.rows.map(function (row) {
        var o = {};
        r.columns.forEach(function (c, i) { o[c] = row[i]; });
        return o;
      });
      drawMap();
    });
    loadRanking();

    $("size-by").addEventListener("change", function () {
      state.sizeBy = this.value; pushState(); drawMap();
    });
    $("min-land").addEventListener("change", function () {
      state.minLand = Number(this.value); pushState(); drawMap();
    });
    $("rank-metric").addEventListener("change", function () {
      state.metric = this.value; pushState(); loadRanking();
    });
    $("rent-metric").addEventListener("change", function () {
      state.rmetric = this.value; pushState(); loadRental();
    });
    var timer = null;
    $("q").addEventListener("input", function () {
      var v = $("q").value.trim();
      clearTimeout(timer);
      timer = setTimeout(function () {
        runSearch(v);
        // the URL encodes the search too, so a result list is citable
        var q = new URLSearchParams(location.search);
        if (v) { q.set("q", v); } else { q.delete("q"); }
        var qs = q.toString();
        history.replaceState(null, "", "facilities.html" + (qs ? "?" + qs : ""));
      }, 180);
    });
    if (params.get("q")) {
      $("q").value = params.get("q");
      runSearch(params.get("q"));
    }
    $("map-png").addEventListener("click", function (e) { e.preventDefault(); exportMapMenu(); });
    $("map-csv").addEventListener("click", function (e) {
      e.preventDefault();
      if (!mapData) return;
      csvDownload("japan-facilities-map.csv",
        [SOURCE_LINE, "one latest clean filing per company; missing values are empty, never 0",
         "location_en: municipality romanization (derived) · use: category classified " +
         "from the filer's text (derived); empty = unclassified"],
        ["sec_code", "company", "facility", "location", "location_en", "use",
         "municipality", "lat", "lng",
         "land_book_yen", "trust_land_book_yen", "land_area_m2", "total_book_yen", "employees"],
        visibleDots().map(function (d) {
          return [d.sec_code, d.company, d.name, d.location, d.location_en, d.use,
                  d.muni_name, d.lat, d.lng,
                  d.land_yen, d.trust_land_yen, d.land_area_m2, d.total_yen, d.employees];
        }));
    });
    $("rank-csv").addEventListener("click", function (e) {
      e.preventDefault();
      if (!rankData) return;
      csvDownload("japan-hidden-land-screen.csv",
        [SOURCE_LINE, "metric: " + state.metric,
         "book_yen_per_m2 = land_book_yen / land_area_m2 (derived)",
         "unlisted_land_yen = bs_land_yen - land_book_yen (derived; empty where " +
         "the balance-sheet gate did not reconcile or the filer reports IFRS)"],
        ["rank", "sec_code", "company", "period_end", "land_book_yen", "land_area_m2",
         "book_yen_per_m2", "bs_land_yen", "unlisted_land_yen"],
        rankData.companies.map(function (r, i) {
          return [i + 1, r.sec_code, r.company, r.period_end, r.land_book_yen,
                  r.land_area_m2, r.yen_per_m2, r.bs_land_yen, r.unlisted_land_yen];
        }));
    });
    $("rent-csv").addEventListener("click", function (e) {
      e.preventDefault();
      if (!rentData) return;
      csvDownload("japan-rental-property-fair-value.csv",
        ["Source: company annual securities reports (EDINET), 賃貸等不動産 note, " +
         "figures as disclosed. Fair value is the filer's own year-end 時価.",
         "metric: " + state.rmetric,
         "unrealized_yen = fair_value_yen - carrying_yen (derived) · " +
         "fair_to_book = fair_value_yen / carrying_yen (derived)"],
        ["rank", "sec_code", "company", "period_end", "consolidated",
         "carrying_yen", "fair_value_yen", "unrealized_yen", "fair_to_book",
         "carrying_prior_yen", "fair_value_prior_yen", "doc_id"],
        rentData.companies.map(function (r, i) {
          return [i + 1, r.sec_code, r.company, r.period_end, r.consolidated,
                  r.carrying_yen, r.fair_value_yen, r.unrealized_yen, r.fair_to_book,
                  r.carrying_prior_yen, r.fair_value_prior_yen, r.doc_id];
        }));
    });
  }

  function loadRanking() {
    getJSON("/api/v1/equity/facilities/ranking?metric=" + encodeURIComponent(state.metric) +
            "&limit=50")
      .then(function (r) { rankData = r; renderRanking(); });
  }

  function pushState() {
    var q = new URLSearchParams();
    if (state.sizeBy !== "land_book") q.set("size", state.sizeBy);
    if (state.minLand !== 1e9) q.set("min", String(state.minLand));
    if (state.metric !== "yen_per_m2") q.set("metric", state.metric);
    if (state.rmetric !== "unrealized") q.set("rmetric", state.rmetric);
    var qs = q.toString();
    history.replaceState(null, "", "facilities.html" + (qs ? "?" + qs : ""));
  }

  /* ---- company map ---- */

  var coChart = null;
  var coData = null;   // the company's geocoded facilities

  /* Several sites often share one municipality and would stack into a single
     dot at its centroid. Spread them on a small deterministic spiral (a few
     hundred metres) so each stays clickable — the position was already a
     centroid, never a parcel, and the page says so under the map. */
  function spreadDots(rows) {
    var seen = {};
    return rows.map(function (x) {
      var key = x.lat + "," + x.lng;
      var k = seen[key] = (seen[key] || 0) + 1;
      if (k === 1) return { lat: x.lat, lng: x.lng, f: x };
      var a = k * 2.4, r = 0.006 * Math.sqrt(k);
      return { lat: x.lat + r * Math.sin(a), lng: x.lng + r * Math.cos(a), f: x };
    });
  }

  /* Murata-style filings merge the site name into the location column —
     本社 (京都府長岡京市). Split for display only; the stored value stays as
     filed. */
  function splitSite(x) {
    if (x.name) return { name: x.name, loc: x.location };
    var m = /^(.*?)\s*[（(]([^（()）]+)[）)]\s*$/.exec(x.location || "");
    if (m && m[1]) return { name: m[1], loc: m[2] };
    return { name: null, loc: x.location };
  }

  function coTooltip(x) {
    function line(label, val) {
      return val == null || val === "" ? "" : "<br>" + label + ": " + val;
    }
    var land = x.land_yen == null && x.trust_land_yen == null
      ? null : (x.land_yen || 0) + (x.trust_land_yen || 0);
    var site = splitSite(x);
    var cur = x.currency;
    var jpy = !cur || cur === "JPY";
    return "<b>" + esc(site.name || site.loc || "Facility") + "</b>" +
      line("Location", esc(x.location_en || site.loc)) +
      line("Use", esc(useLabel(x.use)) +
           (NONCORE[x.use] ? " · non-core" : "")) +
      line("Segment", esc(x.segment)) +
      line("Buildings", x.buildings_yen == null ? null : moneyFmt(x.buildings_yen, cur)) +
      line("Machinery", x.machinery_yen == null ? null : moneyFmt(x.machinery_yen, cur)) +
      line("Land (book)", land == null ? null : moneyFmt(land, cur)) +
      line("Land area", x.land_area_m2 == null ? null : areaFmt(x.land_area_m2)) +
      line("Book ¥/㎡", (jpy && land && x.land_area_m2) ? yenPerM2(land / x.land_area_m2) : null) +
      line("Total", x.total_yen == null ? null : moneyFmt(x.total_yen, cur)) +
      line("Employees", x.employees == null ? null : fmtNum(x.employees, 0));
  }

  function drawCoMap() {
    if (!mapReady || !coData || !coData.length) return;
    $("co-map-wrap").hidden = false;
    var p = pal();
    var el = $("co-map");
    if (!el.offsetWidth) {           // measured while hidden → a corner-sized
      requestAnimationFrame(drawCoMap);   // canvas nobody can recover by hand
      return;
    }
    var dots = spreadDots(coData);
    var lats = dots.map(function (d) { return d.lat; });
    var lngs = dots.map(function (d) { return d.lng; });
    // Fit the view to the company's own bounding box (±1.5° margin) — ECharts
    // does the zoom arithmetic, so the frame is filled whatever the container
    // shape. Hand-computed center+zoom collapsed on wide windows.
    var box = [[Math.min.apply(null, lngs) - 1.5, Math.max.apply(null, lats) + 1.5],
               [Math.max.apply(null, lngs) + 1.5, Math.min.apply(null, lats) - 1.5]];
    var max = 1;
    dots.forEach(function (d) { max = Math.max(max, d.f.total_yen || d.f.land_yen || 0); });
    var scale = 52 / Math.sqrt(max);
    if (coChart) coChart.dispose();
    coChart = echarts.init(el, null, { renderer: "canvas" });
    coChart.setOption({
      backgroundColor: "transparent",
      geo: {
        map: "japan", roam: true, aspectScale: 0.85,
        scaleLimit: { min: 1, max: 30 },   // wheel can zoom in, never below fit
        boundingCoords: box,
        itemStyle: { areaColor: p.subtle, borderColor: p.border, borderWidth: 0.6 },
        emphasis: { disabled: true }, label: { show: false },
        left: 10, right: 10, top: 10, bottom: 10,
      },
      tooltip: {
        trigger: "item", confine: true, triggerOn: "mousemove|click",
        backgroundColor: p.surface, borderColor: p.border,
        textStyle: { color: p.ink, fontSize: 12 },
        formatter: function (q) { return coTooltip(q.data.f); },
      },
      series: [{
        type: "scatter", coordinateSystem: "geo",
        data: dots.map(function (d) {
          return { value: [d.lng, d.lat, d.f.total_yen || d.f.land_yen || 0], f: d.f };
        }),
        symbolSize: function (v) { return Math.max(15, Math.sqrt(v[2] || 0) * scale); },
        itemStyle: { color: p.primary, opacity: 0.9,
                     borderColor: p.surface, borderWidth: 1 },
        emphasis: { itemStyle: { opacity: 1 } },
      }],
    });
  }

  /* ---- company view ---- */

  function companyView(code) {
    $("market-view").hidden = true;
    $("company-view").hidden = false;
    getJSON("/api/v1/equity/facilities/company/" + encodeURIComponent(code)).then(function (f) {
      document.title = (f.name_en || f.filer_name || code) +
        " · Facilities & Land · Plover Analytics";
      $("co-name").textContent = f.name_en || f.filer_name || code;
      if (f.name_en && f.filer_name) {
        $("co-name-ja").textContent = f.filer_name;
        $("co-name-ja").hidden = false;
      }
      $("co-code").textContent = f.sec_code || "";
      $("co-filing").innerHTML = "Annual report for the period ended <b>" +
        esc(f.period_end || MISSING) + "</b> · filed " + esc(f.filed_date || MISSING) +
        ' · doc <span class="mono">' + esc(f.doc_id) + "</span>" +
        ' · SHA-256 <span class="mono">' + esc((f.sha256_t1 || "").slice(0, 12)) + "…</span>";
      if (f.status === "partial") {
        $("co-warning").hidden = false;
        $("co-warning").innerHTML = "<b>Validation gate failed on this filing.</b> " +
          "Figures are shown exactly as filed, but this company is excluded from the " +
          "map and the screen until the extraction reconciles.";
      }
      var landPerM2 = (f.fac_land_book_yen && f.fac_land_area_m2)
        ? f.fac_land_book_yen / f.fac_land_area_m2 : null;
      /* Non-core land: rows the derived classification calls rental real
         estate, housing/welfare or idle. Only yen rows can join a yen sum. */
      var ncLand = 0;
      f.facilities.forEach(function (x) {
        if (x.is_summary || !x.noncore) return;
        if (x.currency && x.currency !== "JPY") return;
        ncLand += (x.land_yen || 0) + (x.trust_land_yen || 0);
      });
      $("co-facts").innerHTML =
        fact("Disclosed land, book", yenBn(f.fac_land_book_yen), "historical cost, as filed") +
        fact("Disclosed land area", areaFmt(f.fac_land_area_m2), "owned land where filed") +
        fact("Book ¥/㎡", yenPerM2(landPerM2), "derived: land book ÷ area") +
        fact("Balance-sheet land", yenBn(f.bs_land_yen),
             f.bs_land_status === "clean" ? "facilities land reconciles (≤)" :
             f.bs_land_status === "parent_only_bs" ? "parent-only figure (IFRS filer)" :
             f.bs_land_status || "") +
        (ncLand > 0
          ? fact("Of which non-core land", yenBn(ncLand),
                 "rental, housing, idle — derived classification")
          : "") +
        (f.rental
          ? fact("Rental property, fair value", yenBn(f.rental.fair_value_yen),
                 "vs " + yenBn(f.rental.carrying_yen) +
                 " carrying — 賃貸等不動産 note, as disclosed") +
            fact("Unrealized gain",
                 yenSigned(f.rental.fair_value_yen - f.rental.carrying_yen),
                 "derived: fair value − carrying amount")
          : "");
      renderCompanyTable(f);
      coData = f.facilities.filter(function (x) { return x.lat != null && !x.is_summary; });
      drawCoMap();
      $("co-csv").addEventListener("click", function (e) {
        e.preventDefault();
        csvDownload((f.sec_code || code) + "-facilities.csv",
          [SOURCE_LINE, "doc_id: " + f.doc_id + " · sha256(t1): " + f.sha256_t1,
           "status: " + f.status + " · parser: " + f.parser_version,
           "location_en, use, noncore are derived (romanization; keyword " +
           "classification of the filer's text); all other columns as filed"],
          ["table_no", "row_no", "scope", "is_summary", "name", "location",
           "location_en", "use", "noncore", "segment",
           "contents", "currency", "buildings", "machinery", "land", "trust_land",
           "lease", "other", "total", "employees", "land_area_m2",
           "municipality", "lat", "lng"],
          f.facilities.map(function (x) {
            return [x.table_no, x.row_no, x.scope, x.is_summary, x.name, x.location,
                    x.location_en, x.use, x.use ? x.noncore : null,
                    x.segment, x.contents, x.currency || "JPY",
                    x.buildings_yen, x.machinery_yen, x.land_yen,
                    x.trust_land_yen, x.lease_yen, x.other_yen, x.total_yen, x.employees,
                    x.land_area_m2, x.muni_name, x.lat, x.lng];
          }));
      });
    }).catch(function () {
      $("co-name").textContent = "No facilities filing found";
      $("co-code").textContent = code;
      $("co-filing").textContent =
        "This company has no clean or partial facilities extraction in the current data.";
    });
  }

  var SCOPE_LABEL = { parent: "Parent", domestic_sub: "Domestic subsidiary",
                      overseas_sub: "Overseas subsidiary", group: "Group" };

  function renderCompanyTable(f) {
    var rows = f.facilities.filter(function (x) { return !x.is_summary; });
    var summaries = f.facilities.filter(function (x) { return x.is_summary; });
    var head =
      "<thead><tr><th>Facility</th><th>Location</th><th>Use</th>" +
      '<th class="r">Buildings</th><th class="r">Machinery</th>' +
      '<th class="r">Land</th><th class="r">Land area</th>' +
      '<th class="r">Total</th><th class="r">Employees</th></tr></thead>';
    function tr(x) {
      var land = x.land_yen == null && x.trust_land_yen == null
        ? null : (x.land_yen || 0) + (x.trust_land_yen || 0);
      var site = splitSite(x);
      var cur = x.currency;
      // One line per cell. Currency is in moneyFmt's symbol; the scope, the
      // filed Japanese location, the segment and trust land are hover text.
      return "<tr><td class=cell-name" +
        (x.scope ? " title='" + esc(SCOPE_LABEL[x.scope] || x.scope) + "'" : "") + ">" +
        esc(site.name || MISSING) + "</td>" +
        "<td" + (x.location_en && site.loc ? " title='Filed as: " + esc(site.loc) + "'" : "") + ">" +
        esc(x.location_en || site.loc || MISSING) + "</td>" +
        '<td class="nw"' + (x.segment ? " title='Segment: " + esc(x.segment) + "'" : "") + ">" +
        (x.use ? esc(useLabel(x.use)) : MISSING) +
        (NONCORE[x.use] ? ' <span class="chip-nc">non-core</span>' : "") + "</td>" +
        '<td class="r">' + moneyFmt(x.buildings_yen, cur) + "</td>" +
        '<td class="r">' + moneyFmt(x.machinery_yen, cur) + "</td>" +
        '<td class="r"' + (x.trust_land_yen ? " title='Includes land held in trust: " +
          moneyFmt(x.trust_land_yen, cur) + "'" : "") + ">" + moneyFmt(land, cur) + "</td>" +
        '<td class="r">' + areaFmt(x.land_area_m2) + "</td>" +
        '<td class="r">' + moneyFmt(x.total_yen, cur) + "</td>" +
        '<td class="r">' + (x.employees == null ? MISSING : fmtNum(x.employees, 0)) + "</td></tr>";
    }
    var body = rows.map(tr).join("");
    $("co-table").innerHTML = head + "<tbody>" + body + "</tbody>";
    $("co-qualifier").textContent = rows.length + " facilities" +
      (summaries.length ? " · filer also publishes " + summaries.length + " summary rows" : "");
  }

  /* ---- boot ---- */

  function boot() {
    var params = new URLSearchParams(location.search);
    var code = params.get("code");

    fetch("assets/japan.geo.json").then(function (r) { return r.json(); })
      .then(function (gj) {
        echarts.registerMap("japan", gj);
        mapReady = true;
        drawMap();
        drawCoMap();
      });

    if (code) { companyView(code); } else { marketView(params); }

    window.addEventListener("resize", function () {
      if (mapChart) mapChart.resize();
      if (coChart) coChart.resize();
    });
    // container size can change without a window resize (fonts, scrollbars,
    // the section unhiding) — a chart initialised against a stale measurement
    // stays tiny forever without this
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () {
        if (mapChart) mapChart.resize();
        if (coChart) coChart.resize();
      });
      ro.observe($("fac-map"));
      ro.observe($("co-map"));
    }
    wireZoom("fac-zoom-in", "fac-zoom-out", function () { return mapChart; }, 60);
    wireZoom("co-zoom-in", "co-zoom-out", function () { return coChart; }, 30);
    initThemeToggle(function () { drawMap(); drawCoMap(); });
  }

  boot();
})();
