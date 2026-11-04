/* Births, deaths and marriages, monthly — the vital-stats-jp section of the
   population page.

   Two monthly counts from the Ministry of Health, Labour and Welfare, drawn
   side by side and never added together: the preliminary count (速報), which
   includes foreign residents and Japanese abroad, and the approximate count
   (概数), Japanese nationals in Japan only. Every number on the section is
   read from /api/v1/vital-stats-jp/observations; the year-to-date and
   12-month totals are the API's own sums (measure=ytd, sum_12m), so the chart,
   the tiles and the CSV download agree by construction.

   URL state: vev (births | deaths | natural_change | marriages | divorces)
   and vview (index | sum_12m | ytd). Loaded after population.js, whose
   tileCell() it uses for the tiles. */

var VITAL = "vital-stats-jp";
var VITAL_EVENTS = {
  births: { label: "Births", unit: "persons" },
  deaths: { label: "Deaths", unit: "persons" },
  natural_change: { label: "Natural Change", unit: "persons" },
  marriages: { label: "Marriages", unit: "couples" },
  divorces: { label: "Divorces", unit: "couples" },
};
var VITAL_VIEWS = {
  index: { label: "Monthly", note: "monthly count" },
  sum_12m: { label: "Last 12 Months", note: "12-month total" },
  ytd: { label: "Year to Date", note: "total so far each year" },
};
var VITAL_RELEASES = [
  { key: "sokuhou", name: "Preliminary (incl. foreign residents)", slot: 1 },
  { key: "gaisu", name: "Japanese nationals (approximate)", slot: 2 },
];
var MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                    "Oct", "Nov", "Dec"];

var vitalChart = null;
var vitalBody = null;     // the observations payload the chart is drawn from
var vitalAsked = 0;       // the newest chart request; an older reply is dropped

function vitalState() {
  var p = new URLSearchParams(location.search);
  return {
    ev: VITAL_EVENTS[p.get("vev")] ? p.get("vev") : "births",
    view: VITAL_VIEWS[p.get("vview")] ? p.get("vview") : "sum_12m",
  };
}

function setVitalState(next) {
  var s = Object.assign(vitalState(), next);
  var p = new URLSearchParams(location.search);
  p.delete("vev"); p.delete("vview");
  if (s.ev !== "births") p.set("vev", s.ev);
  if (s.view !== "sum_12m") p.set("vview", s.view);
  var qs = p.toString();
  history.replaceState(null, "", (qs ? "?" + qs : location.pathname) + location.hash);
}

function vitalUrl(codes, measure, start, format) {
  return "api/v1/" + VITAL + "/observations?series=" + codes.join(",") +
    "&measure=" + measure + (start ? "&start=" + start : "") +
    (format ? "&format=" + format : "");
}

function monthName(iso) {
  return MONTHS_SHORT[Number(iso.slice(5, 7)) - 1] + " " + iso.slice(0, 4);
}

function lastPoint(points) {
  for (var i = points.length - 1; i >= 0; i--) if (points[i][1] !== null) return points[i];
  return null;
}

function valueAt(points, iso) {
  for (var i = 0; i < points.length; i++) if (points[i][0] === iso) return points[i][1];
  return null;
}

function yearEarlier(iso) {
  return (Number(iso.slice(0, 4)) - 1) + iso.slice(4);
}

function pctChange(cur, prev) {
  return cur === null || prev === null || !prev ? null : (cur / prev - 1) * 100;
}

/* ---------- tiles ---------- */

function vitalTile(label, points, title) {
  var last = lastPoint(points);
  if (!last) return tileCell(label, MISSING, null, "%", title);
  var prev = valueAt(points, yearEarlier(last[0]));
  return tileCell(label, fmtNum(last[1], 0), pctChange(last[1], prev), "%", title);
}

function renderVitalTiles(ytd, monthly) {
  var pts = function (body, code) {
    var s = body.series.filter(function (x) { return x.code === code; })[0];
    return s ? s.points : [];
  };
  var bPre = pts(ytd, "sokuhou.births"), bJp = pts(ytd, "gaisu.births");
  var dPre = pts(ytd, "sokuhou.deaths"), mPre = pts(monthly, "sokuhou.births");
  var span = function (points) {
    var last = lastPoint(points);
    return last ? "Jan–" + MONTHS_SHORT[Number(last[0].slice(5, 7)) - 1] + " " +
      last[0].slice(0, 4) : "";
  };
  document.getElementById("vital-tiles").innerHTML =
    vitalTile("Births " + span(bPre) + " · Preliminary", bPre,
      "Births so far this year, preliminary count including foreign residents and " +
      "Japanese abroad, against the same months a year earlier") +
    vitalTile("Births " + span(bJp) + " · Japanese", bJp,
      "Births so far this year to Japanese nationals in Japan (approximate count), " +
      "against the same months a year earlier") +
    vitalTile("Births " + (lastPoint(mPre) ? monthName(lastPoint(mPre)[0]) : "") +
      " · Preliminary", mPre,
      "The latest month's preliminary count of births, against the same month a " +
      "year earlier") +
    vitalTile("Deaths " + span(dPre) + " · Preliminary", dPre,
      "Deaths so far this year, preliminary count, against the same months a year " +
      "earlier");
  var lb = lastPoint(bPre), lj = lastPoint(bJp);
  document.getElementById("vital-foot").textContent =
    "Counts are official statistics as published; the year-to-date totals and the " +
    "changes on a year earlier are calculated from them." +
    (lb && lj ? " The preliminary count runs to " + monthName(lb[0]) +
      " and the Japanese-nationals count to " + monthName(lj[0]) +
      ", about three months behind." : "");
}

/* ---------- chart ---------- */

function vitalConfig() {
  var st = vitalState();
  var ev = VITAL_EVENTS[st.ev];
  var view = VITAL_VIEWS[st.view];
  var series = (vitalBody ? vitalBody.series : []).map(function (s, i) {
    var r = VITAL_RELEASES[i];
    return { name: r.name, slot: r.slot, points: s.points };
  });
  return {
    series: series,
    unit: ev.unit,
    dp: 0,
    unitSuffix: ev.unit,
    yAxisName: ev.unit,
    trust: st.view === "index" ? "official" : "derived",
    sourceLine: "Source: Ministry of Health, Labour and Welfare, Vital Statistics. " +
      ev.label + ", " + view.note + ".",
    title: ev.label + " in Japan — " + view.note,
  };
}

function renderVitalChart() {
  var st = vitalState();
  var el = document.getElementById("vital-chart");
  var codes = VITAL_RELEASES.map(function (r) { return r.key + "." + st.ev; });
  var asked = ++vitalAsked;
  el.setAttribute("aria-busy", "true");
  return fetch(vitalUrl(codes, st.view)).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok) throw new Error(body.detail || "HTTP " + r.status);
      return body;
    });
  }).then(function (body) {
    // Two quick clicks send two requests; the slower, older answer must
    // not overwrite the view the reader picked last.
    if (asked !== vitalAsked) return;
    el.removeAttribute("aria-busy");
    vitalBody = body;
    var cfg = vitalConfig();
    el.innerHTML = "";
    if (!vitalChart) vitalChart = obsChart(el, "line", cfg);
    vitalChart.render(cfg);
    var ev = VITAL_EVENTS[st.ev], view = VITAL_VIEWS[st.view];
    var ends = body.series.map(function (s) {
      var l = lastPoint(s.points); return l ? l[0] : null;
    });
    document.getElementById("vital-note").textContent =
      ev.label + " · " + view.note + (ends[0] ? " · to " + monthName(ends[0]) : "");
    document.getElementById("vital-source").innerHTML =
      (body.trust === "official" ? trustBadge("official") + " " : "") +
      "Source: Ministry of Health, Labour and Welfare, Vital Statistics (人口動態統計). " +
      "Preliminary count (速報) to " + (ends[0] ? monthName(ends[0]) : "—") +
      "; approximate count (概数) to " + (ends[1] ? monthName(ends[1]) : "—") +
      ". Release “" + escapeHtml(body.release.label) + "”, ingested " +
      fmtStamp(body.release.ingested_at) + ".";
    document.getElementById("vital-calc").innerHTML =
      "<summary>Show calculation</summary><div class=\"calc-body\">" +
      (st.view === "index"
        ? "The values are published monthly counts; nothing is computed.<br>"
        : "<code>" + escapeHtml(body.calc) + "</code><br>") +
      "The tiles' changes: <code>(value[t] ÷ value[same month a year earlier] − 1) × " +
      "100</code>, on the year-to-date totals or the month's count.<br>" +
      "The two lines count different people and are never added together. The " +
      "preliminary count (速報) is every event reported in Japan, foreign residents " +
      "included, plus Japanese abroad, published about two months after the month. " +
      "The approximate count (概数) is Japanese nationals in Japan only, published " +
      "about five months after; it is the population behind the annual final " +
      "figures and the fertility rate. December's preliminary count has been below " +
      "the approximate count in every year on file, as published, so read December " +
      "against December. " + (ev.unit === "couples"
        ? "Marriages and divorces count couples, not people. " : "") +
      "A month the Ministry has not published is a gap, never a zero." +
      "</div>";
  }).catch(function (err) {
    if (asked !== vitalAsked) return;
    el.removeAttribute("aria-busy");
    if (vitalChart) vitalChart.dispose();
    vitalChart = null;
    el.innerHTML = '<p class="state-empty">Could not load the monthly figures (' +
      escapeHtml(String(err && err.message ? err.message : err)) +
      "). Reload the page to try again; every other section is unaffected.</p>";
  });
}

/* ---------- wiring ---------- */

function vitalSeg(id, attr, value, onPick) {
  var root = document.getElementById(id);
  Array.prototype.forEach.call(root.querySelectorAll("button"), function (b) {
    b.setAttribute("aria-pressed", b.getAttribute(attr) === value ? "true" : "false");
    b.addEventListener("click", function () {
      Array.prototype.forEach.call(root.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      onPick(b.getAttribute(attr));
    });
  });
}

function bootVital() {
  if (!document.getElementById("vital-chart")) return;
  var st = vitalState();
  vitalSeg("vital-event-seg", "data-vev", st.ev, function (v) {
    setVitalState({ ev: v }); renderVitalChart();
  });
  vitalSeg("vital-view-seg", "data-vview", st.view, function (v) {
    setVitalState({ view: v }); renderVitalChart();
  });
  document.getElementById("vital-png").addEventListener("click", function () {
    var s = vitalState();
    if (vitalChart) vitalChart.exportPNG("japan-" + s.ev.replace("_", "-") + "-" +
      (s.view === "index" ? "monthly" : s.view === "ytd" ? "year-to-date" : "12-month") +
      ".png");
  });
  // The API's own CSV: the same numbers as the chart under the full source,
  // release, formula and vintage block.
  document.getElementById("vital-csv").addEventListener("click", function () {
    var s = vitalState();
    var codes = VITAL_RELEASES.map(function (r) { return r.key + "." + s.ev; });
    location.href = vitalUrl(codes, s.view, null, "csv");
  });

  var seriesTiles = ["sokuhou.births", "gaisu.births", "sokuhou.deaths"];
  var start = (new Date().getFullYear() - 2) + "-01";
  Promise.all([
    fetch(vitalUrl(seriesTiles, "ytd", start)).then(function (r) { return r.json(); }),
    fetch(vitalUrl(["sokuhou.births"], "index", start)).then(function (r) { return r.json(); }),
  ]).then(function (all) {
    if (!all[0].series || !all[1].series) throw new Error(all[0].detail || all[1].detail);
    renderVitalTiles(all[0], all[1]);
  }).catch(function () {
    document.getElementById("vital-tiles").innerHTML =
      '<p class="state-empty">The monthly figures could not be loaded. Reload the ' +
      "page to try again.</p>";
  });
  renderVitalChart();

  // Redraw in the other theme; population.js owns the toggle's callback.
  new MutationObserver(function () { if (vitalChart) vitalChart.render(vitalConfig()); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
}

bootVital();
