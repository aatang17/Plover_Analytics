/* Women aged 20–44 and 25–39 — the population-age-jp section of the
   population page: the denominators births are read against.

   Both lines are sums of the Statistics Bureau's published single years of
   age, calculated by /api/v1/population-age-jp/age-bands, so they carry the
   formula and no badge, and the CSV download is that endpoint's own file.
   URL state: anat (jp | total). */

var AGES_DS = "population-age-jp";
var AGES_BANDS = "25-39,20-44";
var AGES_NAT = {
  jp: { label: "Japanese nationals", phrase: "Japanese nationals", from: "2000" },
  total: { label: "Total population", phrase: "total population", from: "1980" },
};
var agesChart = null;
var agesBody = null;
var agesAsked = 0;

function agesState() {
  var v = new URLSearchParams(location.search).get("anat");
  return { nat: AGES_NAT[v] ? v : "jp" };
}

function agesUrl(nat, format) {
  return "api/v1/" + AGES_DS + "/age-bands?nationality=" + nat + "&sex=f&bands=" +
    AGES_BANDS + (format ? "&format=" + format : "");
}

function agesConfig() {
  var nat = AGES_NAT[agesState().nat];
  return {
    series: (agesBody ? agesBody.series : []).map(function (s, i) {
      return { name: "Women aged " + s.code, slot: i + 1,
               points: s.points.map(function (p) { return [p[0], p[1]]; }) };
    }),
    unit: "'000 persons", dp: 0, unitSuffix: "thousand", yAxisName: "thousand women",
    trust: "derived", showPoints: true,
    sourceLine: "Source: Statistics Bureau of Japan — Population Estimates. " +
      nat.label + ", women at 1 October; sums of published single years of age.",
    title: "Women aged 25–39 and 20–44, " + nat.phrase,
  };
}

function renderAges() {
  var st = agesState();
  var el = document.getElementById("ages-chart");
  var asked = ++agesAsked;
  return fetch(agesUrl(st.nat)).then(function (r) {
    return r.json().then(function (b) {
      if (!r.ok) throw new Error(b.detail || "HTTP " + r.status);
      return b;
    });
  }).then(function (body) {
    if (asked !== agesAsked) return;
    agesBody = body;
    el.innerHTML = "";
    if (!agesChart) agesChart = obsChart(el, "line", agesConfig());
    agesChart.render(agesConfig());
    var last = body.series[0].points.filter(function (p) { return p[1] !== null; }).pop();
    var prev = body.series[0].points.filter(function (p) {
      return last && p[0] === (Number(last[0].slice(0, 4)) - 1) + last[0].slice(4);
    })[0];
    document.getElementById("ages-note").textContent =
      AGES_NAT[st.nat].label + " · " + AGES_NAT[st.nat].from + "–" +
      (last ? last[0].slice(0, 4) : "");
    document.getElementById("ages-foot").textContent = last
      ? "Women aged 25–39, " + AGES_NAT[st.nat].phrase + ": " +
        fmtNum(last[1], 0) + " thousand at 1 October " + last[0].slice(0, 4) +
        (prev && prev[1] !== null
          ? ", " + fmtSigned((last[1] / prev[1] - 1) * 100, 1, "%") + " on a year earlier." : ".")
      : "";
    document.getElementById("ages-source").innerHTML =
      "Source: Statistics Bureau of Japan — Population Estimates (人口推計), 1 October. " +
      "Release “" + escapeHtml(body.release.label) + "”, ingested " +
      fmtStamp(body.release.ingested_at) + ".";
    document.getElementById("ages-calc").innerHTML =
      "<summary>Show calculation</summary><div class=\"calc-body\">" +
      "<code>" + escapeHtml(body.calc) + "</code><br>" +
      "The change on a year earlier: <code>(value[t] ÷ value[t−1 year] − 1) × 100</code>.<br>" +
      "Total population runs from 1980 and Japanese nationals from 2000: earlier " +
      "Japanese single years exist only in older tables that were never adjusted to " +
      "the following census, and are not loaded. The 2000–2020 years are the " +
      "Bureau's long-run series adjusted between censuses; later years are its " +
      "current annual table.</div>";
  }).catch(function (err) {
    if (asked !== agesAsked) return;
    if (agesChart) agesChart.dispose();
    agesChart = null;
    el.innerHTML = '<p class="state-empty">Could not load the age figures (' +
      escapeHtml(String(err && err.message ? err.message : err)) +
      "). Reload the page to try again; every other section is unaffected.</p>";
  });
}

function bootAges() {
  if (!document.getElementById("ages-chart")) return;
  var root = document.getElementById("ages-nat-seg");
  Array.prototype.forEach.call(root.querySelectorAll("button"), function (b) {
    b.setAttribute("aria-pressed", b.getAttribute("data-anat") === agesState().nat ? "true" : "false");
    b.addEventListener("click", function () {
      Array.prototype.forEach.call(root.querySelectorAll("button"), function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      var p = new URLSearchParams(location.search);
      if (b.getAttribute("data-anat") === "jp") p.delete("anat");
      else p.set("anat", b.getAttribute("data-anat"));
      var qs = p.toString();
      history.replaceState(null, "", (qs ? "?" + qs : location.pathname) + location.hash);
      renderAges();
    });
  });
  document.getElementById("ages-png").addEventListener("click", function () {
    if (agesChart) agesChart.exportPNG("japan-women-20-44-" + agesState().nat + ".png");
  });
  document.getElementById("ages-csv").addEventListener("click", function () {
    location.href = agesUrl(agesState().nat, "csv");
  });
  renderAges();
  new MutationObserver(function () { if (agesChart) agesChart.render(agesConfig()); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
}

bootAges();
