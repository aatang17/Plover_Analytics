/* Admin console: ingest health, release history, audit log.

   Structure follows the GSA-platform admin (persistent sidebar driven by one
   nav description, KPI summary cards, outline-only status badges, rows that
   expand into detail) rendered with Plover Analytics tokens and helpers. All data
   comes from /admin/api/*, which requires a signed-in session; the page shell
   itself holds no numbers.

   Statuses are never shown raw — every enum goes through a label map. */
"use strict";

var ROOT = document.getElementById("admin-root");

/* `perm` is the permission a page needs (app/staff.py). The menu shows only
   what the signed-in person can open; the API refuses the rest regardless. */
var ADMIN_NAV = [
  { group: "Operations", pages: [
    { id: "health", label: "Ingest Health", hash: "#health", perm: "operations" },
    { id: "vintages", label: "Release History", hash: "#vintages", perm: "operations" },
    { id: "traffic", label: "Traffic", hash: "#traffic", perm: "operations" },
  ]},
  { group: "Research", pages: [
    // articles live in the Writer Desk now (write.html), with every publication's
    { id: "articles", label: "Writer Desk", hash: "#articles", perm: "writing" },
    { id: "ai", label: "Assistant Settings", hash: "#ai", perm: "writing" },
  ]},
  { group: "Classification", pages: [
    { id: "queue", label: "Classification Queue", hash: "#queue", perm: "classification" },
    { id: "parties", label: "Party Profiles", hash: "#parties", perm: "classification" },
    { id: "companies", label: "Companies", hash: "#companies", perm: "classification" },
  ]},
  { group: "System", pages: [
    { id: "team", label: "Team", hash: "#team", perm: "team" },
    { id: "audit", label: "Audit Log", hash: "#audit", perm: "operations" },
    { id: "account", label: "My Account", hash: "#account", perm: null },
  ]},
];

/* The signed-in person, from /admin/api/session or /login. */
var ME = null;

function can(perm) {
  return !perm || !!(ME && ME.permissions.indexOf(perm) !== -1);
}

function allowedPages() {
  var out = [];
  ADMIN_NAV.forEach(function (g) {
    g.pages.forEach(function (p) {
      if (p.id === "account" && ME && ME.shared) return;
      if (can(p.perm)) out.push(p);
    });
  });
  return out;
}

/* raw enum -> Title-Case label + badge tone; never render the slug */
var AUDIT_ACTIONS = {
  party_created: { label: "Profile Created", cls: "badge-info" },
  party_updated: { label: "Profile Edited", cls: "badge-info" },
  party_deleted: { label: "Profile Deleted", cls: "badge-warn" },
  party_exported: { label: "Classification Exported", cls: "badge-neutral" },
  us_company_changed: { label: "Company Name Or Website Changed", cls: "badge-info" },
  us_company_logo_uploaded: { label: "Company Logo Uploaded", cls: "badge-info" },
  us_company_logo_reset: { label: "Company Logo Reset", cls: "badge-neutral" },
  us_company_logo_hidden: { label: "Company Logo Hidden", cls: "badge-warn" },
  jp_company_changed: { label: "Japanese Company Name Changed", cls: "badge-info" },
  jp_company_logo_uploaded: { label: "Japanese Company Logo Uploaded", cls: "badge-info" },
  jp_company_logo_reset: { label: "Japanese Company Logo Removed", cls: "badge-neutral" },
  jp_company_logo_hidden: { label: "Japanese Company Logo Hidden", cls: "badge-warn" },
  login: { label: "Signed In", cls: "badge-ok" },
  password_set: { label: "Password Set", cls: "badge-ok" },
  password_changed: { label: "Password Changed", cls: "badge-info" },
  staff_added: { label: "Person Added", cls: "badge-info" },
  staff_changed: { label: "Person Changed", cls: "badge-info" },
  setup_link_issued: { label: "Setup Link Issued", cls: "badge-neutral" },
  article_created: { label: "Article Created", cls: "badge-neutral" },
  article_imported: { label: "Article Imported", cls: "badge-neutral" },
  article_deleted: { label: "Draft Deleted", cls: "badge-warn" },
  article_published: { label: "Article Published", cls: "badge-ok" },
  article_withdrawn: { label: "Article Withdrawn", cls: "badge-warn" },
  article_reinstated: { label: "Article Reinstated", cls: "badge-info" },
  image_uploaded: { label: "Image Uploaded", cls: "badge-neutral" },
  backup_run: { label: "Backup Run", cls: "badge-neutral" },
  logout: { label: "Signed Out", cls: "badge-neutral" },
  login_failed: { label: "Failed Login", cls: "badge-danger" },
  login_locked_out: { label: "Locked Out", cls: "badge-danger" },
  unparseable_entry: { label: "Unreadable Entry", cls: "badge-warn" },
};

function badge(map, key) {
  var d = map[key] || { label: key ? key.replace(/_/g, " ") : MISSING, cls: "badge-neutral" };
  return '<span class="badge ' + d.cls + '">' + escapeHtml(d.label) + "</span>";
}

/* Values across datasets span index points to ¥100mn stocks: group thousands,
   true minus, and ONE precision per table — the widest number of decimals any
   value in the column actually carries (capped at 4), so 127.0 and 129.6
   never sit in the same column as "127" and "129.6". */
function decimalsOf(v) {
  if (v === null || v === undefined) return 0;
  var s = String(v);
  var dot = s.indexOf(".");
  return dot === -1 ? 0 : Math.min(4, s.length - dot - 1);
}

function fmtVal(v, dp) {
  if (v === null || v === undefined) return MISSING;
  return v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })
    .replace(/-/g, MINUS);
}

/* Age of the ingest heartbeat. Hours up to two days, then days: an operator
   needs "is this today's data?" at a glance, and 51.7 does not answer it. */
function fmtAgo(hours) {
  if (hours === null || hours === undefined) return MISSING;
  if (hours < 1) return "Under 1h";
  if (hours < 48) return Math.round(hours) + "h ago";
  return Math.round(hours / 24) + "d ago";
}

function fmtBytes(b) {
  if (b === null || b === undefined) return MISSING;
  if (b >= 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + " MB";
  return Math.round(b / 1024).toLocaleString("en-US") + " KB";
}

/* ---------- API ---------- */

function api(path, opts) {
  return fetch("/admin/api" + path, Object.assign({ credentials: "same-origin" }, opts || {}))
    .then(function (r) {
      // A 401 from the sign-in itself is a wrong password, not an expired session:
      // let its message through, and keep what the person typed.
      if (r.status === 401 && path !== "/login") { renderLogin(""); throw new Error("Signed out"); }
      // Signing in as someone else in another tab changes who this page acts
      // as: reload so the menu is theirs, rather than show their refusals.
      if (r.status === 403 && ME && path !== "/session") {
        fetch("/admin/api/session", { credentials: "same-origin" }).then(function (x) { return x.json(); })
          .then(function (sess) {
            var now = sess && sess.user;
            if (!now || now.id !== ME.id || now.shared !== ME.shared) location.reload();
          }).catch(function () {});
      }
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          throw new Error(b.detail || "The request failed (" + r.status + ")");
        });
      }
      return r.json();
    });
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function loadFailed(what, err) {
  return '<div class="admin-alert"><span class="head">Could not load ' + escapeHtml(what) +
    ".</span> " + escapeHtml(err.message || String(err)) + " Reload the page to try again.</div>";
}

/* ---------- login / disabled ---------- */

function renderDisabled() {
  ROOT.innerHTML =
    '<div class="admin-login-wrap"><div class="admin-login-card">' +
    "<h1>Admin Console</h1>" +
    '<p class="sub">The admin console is switched off on this deployment: there are no ' +
    "accounts yet and no shared admin password. Set <code>ADMIN_PASSWORD</code> in the " +
    "server environment (or in <code>.env</code> locally), restart, sign in with it and " +
    "add the first account on the Team page.</p>" +
    '<a class="btn" href="index.html">Back to Plover Analytics</a>' +
    "</div></div>";
}

var SHARED_PASSWORD_ON = false;

function renderLogin(message) {
  ME = null;
  ROOT.innerHTML =
    '<div class="admin-login-wrap"><div class="admin-login-card">' +
    "<h1>Admin Console</h1>" +
    '<p class="sub">Plover Analytics — internal operations. Sign in to continue.</p>' +
    '<div id="login-alert"></div>' +
    '<form id="login-form">' +
    '<label for="login-email">Email</label>' +
    '<input type="email" id="login-email" autocomplete="username"' +
    (SHARED_PASSWORD_ON ? "" : " required") + ">" +
    '<label for="login-pw">Password</label>' +
    '<input type="password" id="login-pw" autocomplete="current-password" required>' +
    (SHARED_PASSWORD_ON
      ? '<p class="login-hint">To use the shared admin password, leave Email empty.</p>' : "") +
    '<button type="submit" class="btn btn-primary" id="login-btn">Sign In</button>' +
    "</form></div></div>";
  if (message) showLoginAlert(message);
  var form = document.getElementById("login-form");
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = document.getElementById("login-btn");
    btn.disabled = true;
    btn.textContent = "Signing in…";
    post("/login", { email: document.getElementById("login-email").value,
                     password: document.getElementById("login-pw").value })
      .then(function (res) { ME = res.user; renderShell(); route(); })
      .catch(function (err) {
        btn.disabled = false;
        btn.textContent = "Sign In";
        showLoginAlert(err.message);
      });
  });
  document.getElementById("login-email").focus();
}

function showLoginAlert(message) {
  var box = document.getElementById("login-alert");
  if (box) box.innerHTML = '<div class="login-alert" role="alert">' + escapeHtml(message) + "</div>";
}

/* ---------- shell ---------- */

function renderShell() {
  var allowed = allowedPages();
  var nav = ADMIN_NAV.map(function (g) {
    var pages = g.pages.filter(function (p) { return allowed.indexOf(p) !== -1; });
    if (!pages.length) return "";
    return '<div class="admin-nav-group">' + escapeHtml(g.group) + "</div>" +
      pages.map(function (p) {
        return '<a class="admin-nav-item" data-view="' + p.id + '" href="' + p.hash + '">' +
          escapeHtml(p.label) + "</a>";
      }).join("");
  }).join("");

  ROOT.innerHTML =
    '<div class="admin-shell">' +
    '<aside class="admin-sidebar" id="admin-sidebar">' +
    '<div class="admin-side-head"><span class="brand">Plover Analytics ' +
    '<span class="ds">/ Admin</span></span></div>' +
    '<button type="button" class="admin-menu-btn" id="admin-menu-btn">Menu</button>' +
    '<nav class="admin-nav" aria-label="Admin">' + nav + "</nav>" +
    '<div class="admin-side-foot">' +
    (ME ? '<span class="side-who" title="' + escapeHtml(ME.email || "") + '">' +
      escapeHtml(ME.name) + "</span>" : "") +
    '<a class="side-btn" href="index.html">View Site</a>' +
    '<button type="button" class="theme-toggle side-btn">Dark Mode</button>' +
    '<button type="button" class="side-btn" id="sign-out">Sign Out</button>' +
    "</div></aside>" +
    '<main class="admin-main"><div class="admin-container" id="admin-view"></div></main>' +
    "</div>";

  // The traffic chart reads its colours from tokens at build time, so a theme
  // switch has to redraw it.
  initThemeToggle(function () { if (trafficChart) trafficChart.render(); });
  document.getElementById("sign-out").addEventListener("click", function () {
    post("/logout").then(function () { renderLogin("Signed out."); })
      .catch(function () { renderLogin(""); });
  });
  document.getElementById("admin-menu-btn").addEventListener("click", function () {
    var side = document.getElementById("admin-sidebar");
    if (side.hasAttribute("data-menu-open")) side.removeAttribute("data-menu-open");
    else side.setAttribute("data-menu-open", "");
  });
}

function route() {
  var allowed = allowedPages();
  var fallback = allowed.length ? allowed[0].id : "account";
  var hash = location.hash || "#" + fallback;
  var parts = hash.slice(1).split("/");
  var view = parts[0] || fallback;
  var known = ADMIN_NAV.some(function (g) {
    return g.pages.some(function (p) { return p.id === view; });
  });
  var denied = null;
  if (known && !allowed.some(function (p) { return p.id === view; })) {
    ADMIN_NAV.forEach(function (g) { g.pages.forEach(function (p) { if (p.id === view) denied = p; }); });
  }
  if (!known || denied) view = fallback;
  var arg = parts[1] || null;
  /* #parties/new/<edinet-code> carries the filer to prefill from. */
  var arg2 = parts[2] || null;
  var links = document.querySelectorAll(".admin-nav-item");
  for (var i = 0; i < links.length; i++) {
    if (!denied && links[i].getAttribute("data-view") === view) links[i].setAttribute("aria-current", "page");
    else links[i].removeAttribute("aria-current");
  }
  var side = document.getElementById("admin-sidebar");
  if (side) side.removeAttribute("data-menu-open");
  var target = document.getElementById("admin-view");
  if (!target) return;
  disposeTrafficChart();  // the view about to be replaced may own it
  if (denied) {
    // say why, rather than quietly showing another page
    target.innerHTML = '<div class="admin-alert"><span class="head">' + escapeHtml(denied.label) +
      " needs the " + escapeHtml(denied.perm.charAt(0).toUpperCase() + denied.perm.slice(1)) +
      " permission.</span> Ask someone with the Team permission to add it to your account. " +
      '<a href="#' + escapeHtml(fallback) + '">Go to ' +
      escapeHtml((allowed[0] || { label: "My Account" }).label) + "</a></div>";
    return;
  }
  if (view === "articles") location.href = "write.html";
  else if (view === "ai") viewAISetup(target);
  else if (view === "team") viewTeam(target);
  else if (view === "account") viewAccount(target);
  else if (view === "vintages") viewVintages(target, arg);
  else if (view === "traffic") viewTraffic(target, arg, arg2);
  else if (view === "audit") viewAudit(target);
  else if (view === "queue") viewQueue(target);
  else if (view === "companies") viewUsCompanies(target, arg, arg2);
  else if (view === "parties") {
    if (arg) viewPartyDetail(target, arg, arg2);
    else viewParties(target);
  } else if (view === "health") viewHealth(target);
  else target.innerHTML = '<p class="table-empty">Your account has no pages yet. Ask ' +
    "someone with the Team permission to give you one.</p>";
}

/* ---------- ingest health ---------- */

var HEALTH_BADGES_NOTE = {
  stale: "the newest period on the surface is older than this dataset should ever be",
  orphan: "a file was fetched after the published one and no release came of it",
};

function viewHealth(target) {
  target.innerHTML = '<div class="admin-loading">Loading ingest health…</div>';
  api("/overview").then(function (report) {
    var ds = report.datasets;
    var current = ds.filter(function (d) { return d.status === "ok"; }).length;
    var orphans = ds.filter(function (d) { return d.unpublished_artifact; }).length;
    var rejected = ds.reduce(function (n, d) { return n + (d.releases_rejected || 0); }, 0);
    var vintages = ds.reduce(function (n, d) { return n + (d.vintages || 0); }, 0);

    // refresh_overdue is tri-state: true, false, or null when nothing has
    // stamped a cycle yet. Unknown is not healthy and not a fault — it gets
    // the neutral tone and an em dash, never a reassuring green zero.
    var refreshTone = report.refresh_overdue === true ? "danger"
      : report.refresh_overdue === false ? "ok" : "";

    var kpis =
      kpi(current + " / " + ds.length, "Datasets Current", current === ds.length ? "ok" : "danger") +
      kpi(fmtAgo(report.hours_since_ingest), "Last Ingest", refreshTone) +
      kpi(orphans ? String(orphans) : "All clear", "Unpublished Files", orphans ? "danger" : "ok") +
      kpi(rejected ? String(rejected) : "None", "Rejected Ingests", rejected ? "danger" : "ok") +
      kpi(String(vintages), "Releases Stored", "");

    // Needs Attention: one row per problem, as a table to scan (the finance
    // redesign's admin board, 2026-10-26). The refresh comes first: a pipeline
    // that has stopped is the reason every dataset under it is ageing, so it
    // must not be buried among the symptoms.
    var faults = [];
    if (report.refresh_overdue) {
      faults.push("<tr><td><strong>Daily refresh</strong></td><td>The ingest last ran " +
        escapeHtml(fmtAgo(report.hours_since_ingest)) + ", past the " +
        escapeHtml(String(report.refresh_max_age_hours)) + "-hour limit</td>" +
        '<td class="num">' + MISSING + '</td><td class="num">' + MISSING + "</td></tr>");
    }
    ds.filter(function (d) { return d.status === "attention"; }).forEach(function (d) {
      var why = [];
      if (!d.published) why.push("No published release");
      if (d.stale) why.push("Newest data older than its limit");
      if (d.unpublished_artifact) why.push("A fetched file produced no release");
      var text = why.join("; ");
      faults.push('<tr class="clickable" data-ds="' + escapeHtml(d.dataset) + '">' +
        "<td><strong>" + escapeHtml(d.dataset) + "</strong></td>" +
        '<td class="attn">' + escapeHtml(text.charAt(0).toUpperCase() + text.slice(1)) + "</td>" +
        '<td class="num">' + (d.latest_period ? escapeHtml(d.latest_period) : MISSING) + "</td>" +
        '<td class="num">' + (d.published ? d.days_since_latest_period.toLocaleString("en-US") +
          ' <span class="muted">/ ' + d.stale_after_days + "</span>" : MISSING) + "</td></tr>");
    });
    var banner = faults.length
      ? '<div class="admin-section">Needs Attention <span class="note">' + faults.length +
        (faults.length === 1 ? " problem" : " problems") + " · click a dataset for its releases</span></div>" +
        '<div class="table-wrap"><table class="data" data-no-enhance><thead><tr><th>Dataset</th><th>Problem</th>' +
        '<th class="num">Data Through</th><th class="num">Age, Days</th></tr></thead><tbody>' +
        faults.join("") + "</tbody></table></div>"
      : "";

    var rows = ds.map(function (d) {
      var status;
      if (!d.published) status = '<span class="badge badge-danger">No Release</span>';
      else {
        status = d.stale ? '<span class="badge badge-danger">Stale</span>'
                         : '<span class="badge badge-ok">Current</span>';
        if (d.unpublished_artifact) status += ' <span class="badge badge-warn">Unpublished File</span>';
      }
      return '<tr class="clickable" data-ds="' + escapeHtml(d.dataset) + '">' +
        "<td><strong>" + escapeHtml(d.dataset) + "</strong></td>" +
        "<td>" + status + "</td>" +
        '<td class="num">' + (d.latest_period ? escapeHtml(d.latest_period) : MISSING) + "</td>" +
        '<td class="num">' + (d.published ? d.days_since_latest_period.toLocaleString("en-US") +
          ' <span class="muted">/ ' + d.stale_after_days + "</span>" : MISSING) + "</td>" +
        '<td class="num">' + (d.last_published_at ? fmtStamp(d.last_published_at) : MISSING) + "</td>" +
        '<td class="num">' + (d.series_active !== null && d.series_active !== undefined
          ? d.series_active.toLocaleString("en-US") : MISSING) + "</td>" +
        '<td class="num">' + (d.vintages || 0) + "</td>" +
        '<td class="mono">' + (d.artifact_sha256 ? escapeHtml(d.artifact_sha256.slice(0, 12)) +
          ' <span class="muted">· ' + fmtBytes(d.artifact_bytes) + "</span>" : MISSING) + "</td>" +
        "</tr>";
    }).join("");

    target.innerHTML =
      '<div class="admin-page-head"><h1>Ingest Health</h1><span class="spacer"></span>' +
      '<button type="button" class="btn" id="health-refresh">Refresh</button>' +
      '<p class="admin-page-sub">Whether each dataset is current, and whether an ingest went ' +
      "quiet — a fetched file that published nothing is either a validation failure or a crash. " +
      "Checked " + escapeHtml(fmtStamp(report.checked_at)) + " · last ingest " +
      escapeHtml(fmtStamp(report.last_ingest_at)) + ".</p></div>" +
      '<div class="kpi-row">' + kpis + "</div>" + banner +
      '<div class="admin-section">All Datasets <span class="note">age is days since the newest ' +
      "period / the limit before it counts as stale · click a row for its releases</span></div>" +
      '<div class="table-wrap"><table class="data" data-no-enhance>' +
      "<thead><tr><th>Dataset</th><th>Status</th>" +
      '<th class="num">Data Through</th><th class="num">Age, Days</th>' +
      '<th class="num">Last Published (UTC)</th><th class="num">Active Series</th>' +
      '<th class="num">Releases</th><th>Source File</th></tr></thead>' +
      "<tbody>" + rows + "</tbody></table></div>";

    document.getElementById("health-refresh").addEventListener("click", function () {
      viewHealth(target);
    });
    var trs = target.querySelectorAll("tr.clickable");
    for (var i = 0; i < trs.length; i++) {
      trs[i].addEventListener("click", function () {
        location.hash = "#vintages/" + this.getAttribute("data-ds");
      });
    }
  }).catch(function (err) {
    target.innerHTML = loadFailed("the ingest health report", err);
  });
}

function kpi(value, label, tone) {
  return '<div class="kpi"><div class="kpi-value ' + tone + '">' + escapeHtml(value) +
    '</div><div class="kpi-label">' + escapeHtml(label) + "</div></div>";
}

/* ---------- release history ---------- */

/* A dataset list on the left, grouped by section and searchable; the chosen
   dataset's releases on the right, each saying in words what it did. */

var RH = { datasets: [], market: "", query: "", slug: null };

/* "2026-08-01" as the dataset counts time: a day for daily and weekly data,
   a quarter for quarterly, otherwise the month. `short` abbreviates the month. */
function rhPeriod(iso, freq, short) {
  if (!iso) return MISSING;
  var y = iso.slice(0, 4), m = Number(iso.slice(5, 7)), d = Number(iso.slice(8, 10));
  var mon = short ? MONTHS[m - 1].slice(0, 3) : MONTHS[m - 1];
  if (freq === "daily" || freq === "weekly") return d + " " + MONTHS[m - 1].slice(0, 3) + " " + y;
  if (freq === "quarterly") return "Q" + Math.ceil(m / 3) + " " + y;
  return mon + " " + y;
}

/* The period after `iso` for monthly and quarterly data; null where the next
   period is not a fixed step (a daily series skips weekends and holidays). */
function rhNextPeriod(iso, freq) {
  var step = freq === "monthly" ? 1 : freq === "quarterly" ? 3 : 0;
  if (!step || !iso) return null;
  var y = Number(iso.slice(0, 4)), m = Number(iso.slice(5, 7)) + step;
  if (m > 12) { m -= 12; y += 1; }
  return y + "-" + (m < 10 ? "0" : "") + m + "-01";
}

/* "2026-09-20T13:57:00Z" -> "20 Sep 2026"; the year only when it is not this one. */
function rhDay(iso, withYear) {
  if (!iso) return MISSING;
  var y = iso.slice(0, 4);
  var out = Number(iso.slice(8, 10)) + " " + MONTHS[Number(iso.slice(5, 7)) - 1].slice(0, 3);
  return withYear || y !== String(new Date().getUTCFullYear()) ? out + " " + y : out;
}

function rhCount(n) { return Number(n || 0).toLocaleString("en-US"); }

function rhSigned(n) {
  if (!n) return "0";
  return (n > 0 ? "+" : MINUS) + Math.abs(n).toLocaleString("en-US");
}

function rhPlural(n, word) { return rhCount(n) + " " + word + (n === 1 ? "" : "s"); }

function viewVintages(target, slug) {
  target.innerHTML = '<div class="admin-loading">Loading datasets…</div>';
  api("/overview").then(function (report) {
    RH.datasets = report.datasets.slice().sort(function (a, b) {
      return (a.section_rank - b.section_rank) ||
        String(a.name || a.dataset).localeCompare(String(b.name || b.dataset));
    });
    var known = RH.datasets.some(function (d) { return d.dataset === slug; });
    RH.slug = known ? slug : (RH.datasets[0] && RH.datasets[0].dataset);

    var stale = RH.datasets.filter(function (d) { return d.published && d.stale; });
    var alert = stale.length
      ? '<div class="admin-alert" role="status"><span class="head">' +
        (stale.length === 1 ? "1 dataset is stale." : stale.length + " datasets are stale.") +
        "</span> " + stale.slice(0, 4).map(function (d) {
          return '<a href="#vintages/' + encodeURIComponent(d.dataset) + '">' +
            escapeHtml(d.name || d.dataset) + "</a> (data through " +
            escapeHtml(rhPeriod(d.latest_period, d.frequency)) + ")";
        }).join(" · ") +
        (stale.length > 4 ? " · and " + (stale.length - 4) + " more, marked Stale in the list" : "") +
        "</div>"
      : "";

    var markets = {};
    RH.datasets.forEach(function (d) {
      if (d.market) markets[d.market] = (markets[d.market] || 0) + 1;
    });
    var marketNames = Object.keys(markets).sort(function (a, b) { return markets[b] - markets[a]; });
    if (RH.market && !markets[RH.market]) RH.market = "";
    var marketBtns = '<button type="button" class="rh-pill" data-market=""' +
      ' aria-pressed="' + (RH.market ? "false" : "true") + '">All <span class="n">' +
      RH.datasets.length + "</span></button>" +
      marketNames.map(function (m) {
        return '<button type="button" class="rh-pill" data-market="' + escapeHtml(m) + '"' +
          ' aria-pressed="' + (RH.market === m ? "true" : "false") + '">' + escapeHtml(m) +
          ' <span class="n">' + markets[m] + "</span></button>";
      }).join("");

    target.innerHTML =
      '<div class="admin-page-head"><h1>Release History</h1>' +
      '<p class="admin-page-sub">Every ingest we accept is stored as a release and never ' +
      "edited. A correction arrives as a new release. Pick a dataset to see what each " +
      "release added or changed.</p></div>" + alert +
      '<div class="rh-layout">' +
      '<section class="rh-list" aria-label="Datasets">' +
      '<label class="rh-find-label" for="rh-find">Find a dataset</label>' +
      '<input id="rh-find" class="rh-find" type="search" autocomplete="off" ' +
      'placeholder="Name or code — e.g. CPI, GDP, jgb-yields" value="' + escapeHtml(RH.query) + '">' +
      (marketNames.length > 1
        ? '<div class="rh-pills" role="group" aria-label="Market">' + marketBtns + "</div>" : "") +
      '<div class="rh-list-head"><span>Dataset</span><span>Last release</span></div>' +
      '<div id="rh-groups"></div></section>' +
      '<section class="rh-detail" id="rh-detail" aria-label="Releases of the selected dataset">' +
      "</section></div>";

    var find = document.getElementById("rh-find");
    find.addEventListener("input", function () { RH.query = find.value; rhRenderList(); });
    var pills = target.querySelectorAll(".rh-pill");
    for (var i = 0; i < pills.length; i++) {
      pills[i].addEventListener("click", function () {
        RH.market = this.getAttribute("data-market");
        for (var j = 0; j < pills.length; j++) {
          pills[j].setAttribute("aria-pressed", pills[j] === this ? "true" : "false");
        }
        rhRenderList();
      });
    }
    document.getElementById("rh-groups").addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-ds]") : null;
      if (btn) rhSelect(btn.getAttribute("data-ds"), true);
      var clear = e.target.closest ? e.target.closest(".rh-clear") : null;
      if (clear) {
        RH.query = ""; RH.market = "";
        find.value = "";
        for (var j = 0; j < pills.length; j++) {
          pills[j].setAttribute("aria-pressed", pills[j].getAttribute("data-market") ? "false" : "true");
        }
        rhRenderList();
        find.focus();
      }
    });
    rhRenderList();
    rhSelect(RH.slug, false);
  }).catch(function (err) {
    target.innerHTML = loadFailed("the dataset list", err);
  });
}

function rhMatches(d) {
  if (RH.market && d.market !== RH.market) return false;
  // Each word typed must start a word of the name, code, section or market:
  // "rice" finds the rice datasets, not every "Price Index".
  var hay = " " + [d.name, d.dataset, d.section, d.market].join(" ").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ");
  return RH.query.toLowerCase().split(/[^a-z0-9]+/).every(function (w) {
    return !w || hay.indexOf(" " + w) !== -1;
  });
}

function rhRenderList() {
  var box = document.getElementById("rh-groups");
  if (!box) return;
  var shown = RH.datasets.filter(rhMatches);
  if (!shown.length) {
    box.innerHTML = '<p class="rh-empty">No dataset matches “' + escapeHtml(RH.query.trim()) +
      "”" + (RH.market ? " in " + escapeHtml(RH.market) : "") + '. <button type="button" ' +
      'class="rh-clear">Show all datasets</button></p>';
    return;
  }
  var html = "", section = null;
  shown.forEach(function (d, i) {
    if (d.section !== section) {
      section = d.section;
      var n = shown.filter(function (x) { return x.section === section; }).length;
      html += '<div class="rh-group"><span>' + escapeHtml(section || "Other") +
        '</span><span class="n">' + n + "</span></div>";
    }
    var flag = !d.published ? '<span class="badge badge-danger">No Release</span>'
      : d.stale ? '<span class="badge badge-danger">Stale</span>' : "";
    html += '<button type="button" class="rh-ds" data-ds="' + escapeHtml(d.dataset) + '"' +
      (d.dataset === RH.slug ? ' aria-current="true"' : "") +
      ' title="' + escapeHtml((d.name || d.dataset) + " — " + d.dataset) + '">' +
      '<span class="nm">' + escapeHtml(d.name || d.dataset) + "</span>" + flag +
      '<span class="dt">' + escapeHtml(d.last_release_at ? rhDay(d.last_release_at) : MISSING) +
      "</span></button>";
  });
  box.innerHTML = html;
}

function rhSelect(slug, byClick) {
  RH.slug = slug;
  if (byClick && history.replaceState) {
    history.replaceState(null, "", "#vintages/" + encodeURIComponent(slug));
  }
  var rows = document.querySelectorAll(".rh-ds");
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].getAttribute("data-ds") === slug) rows[i].setAttribute("aria-current", "true");
    else rows[i].removeAttribute("aria-current");
  }
  var box = document.getElementById("rh-detail");
  var d = RH.datasets.filter(function (x) { return x.dataset === slug; })[0];
  if (!box || !d) return;
  // The list is long: a pick far down it, or below it on a narrow screen,
  // would change a panel the reader cannot see. Bring the panel into view.
  if (byClick) {
    var top = box.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight - 120) box.scrollIntoView({ block: "start" });
  }

  var status = !d.published ? '<span class="badge badge-danger">No Release</span>'
    : d.stale ? '<span class="badge badge-danger">Stale</span>'
    : '<span class="badge badge-ok">Current</span>';
  if (d.unpublished_artifact) status += ' <span class="badge badge-warn">Unpublished File</span>';
  var freq = d.frequency ? d.frequency.charAt(0).toUpperCase() + d.frequency.slice(1) : "";

  box.innerHTML =
    '<div class="rh-detail-head"><div class="rh-titles">' +
    '<div class="rh-crumb">' + escapeHtml([d.section, d.market].filter(Boolean).join(" · ")) +
    "</div><h2>" + escapeHtml(d.name || d.dataset) + "</h2>" +
    '<div class="rh-meta"><code>' + escapeHtml(d.dataset) + "</code>" +
    (d.agency ? " · " + escapeHtml(d.agency) : "") + (freq ? " · " + escapeHtml(freq) : "") +
    "</div></div>" +
    (d.page ? '<a class="rh-open" href="' + escapeHtml(d.page) + '">Open dataset page →</a>' : "") +
    "</div>" +
    '<div class="rh-stats">' +
    '<div><div class="k">Data through</div><div class="v">' +
      escapeHtml(rhPeriod(d.latest_period, d.frequency, true)) + "</div></div>" +
    '<div><div class="k">Releases stored</div><div class="v">' + rhCount(d.vintages) + "</div></div>" +
    '<div><div class="k">Latest release</div><div class="v">' +
      escapeHtml(d.last_release_at ? rhDay(d.last_release_at, true) : MISSING) + "</div></div>" +
    '<div><div class="k">Status</div><div class="b">' + status + "</div></div></div>" +
    '<div class="rh-sub"><h3>Releases</h3><span>newest first · click a release for its details</span></div>' +
    '<div id="rh-releases"><div class="admin-loading">Loading releases…</div></div>';

  renderReleases(document.getElementById("rh-releases"), d);
}

var RH_STATUS = {
  published: { label: "Live", cls: "badge-ok" },
  superseded: { label: "Replaced", cls: "badge-neutral" },
  archived: { label: "Archive", cls: "badge-neutral" },
  rejected: { label: "Rejected", cls: "badge-danger" },
};

/* What a release did, in words: the main phrase and any qualifiers after it. */
function releaseSummary(r, freq) {
  var extra = [];
  var main;
  if (r.is_first_vintage) {
    main = "First release";
    extra.push("the history as first recorded");
  } else if (r.previous_period && r.latest_period > r.previous_period) {
    var next = rhNextPeriod(r.previous_period, freq);
    main = "Added " + (next && next < r.latest_period
      ? rhPeriod(next, freq) + " to " + rhPeriod(r.latest_period, freq)
      : rhPeriod(r.latest_period, freq));
    if (r.revised) extra.push(rhCount(r.revised) + " revised");
  } else if (r.previous_period && r.latest_period === r.previous_period) {
    if (r.revised) {
      main = "Revised " + rhPlural(r.revised, "value");
      if (r.new_values) extra.push(rhCount(r.new_values) + " new");
    } else if (r.new_values) {
      main = "Added " + rhPlural(r.new_values, "value");
    } else {
      main = "Re-issued, no value changed";
    }
  } else {
    main = "Data through " + rhPeriod(r.latest_period, freq);
    if (r.revised) extra.push(rhCount(r.revised) + " revised");
  }
  if (r.withdrawn) extra.push(rhCount(r.withdrawn) + " withdrawn");
  return { main: main, extra: extra };
}

var RH_SHOW = 25;

function renderReleases(box, d) {
  var slug = d.dataset;
  api("/releases/" + encodeURIComponent(slug)).then(function (data) {
    if (RH.slug !== slug) return;   // another dataset was picked meanwhile
    if (!data.releases.length) {
      box.innerHTML = '<p class="table-empty">No releases stored for this dataset yet.</p>';
      return;
    }
    var rows = data.releases.map(function (r, i) {
      var s = releaseSummary(r, d.frequency);
      var st = RH_STATUS[r.status] || { label: r.status, cls: "badge-neutral" };
      var when = r.known_at_basis === "agency publication"
        ? rhDay(r.known_at, true)
        : rhDay(r.known_at, true) + ' <span class="t">' + r.known_at.slice(11, 16) + "</span>";
      var change = r.is_first_vintage ? MISSING : rhSigned(r.new_values - r.withdrawn);
      return '<button type="button" class="rh-rel" aria-expanded="false" data-i="' + i + '"' +
        (i >= RH_SHOW ? " hidden" : "") +
        (r.known_at_basis === "agency publication"
          ? ' title="Dated by the agency’s own publication date"' : "") + ">" +
        '<span class="when"><span class="chev" aria-hidden="true">▸</span>' + when + "</span>" +
        '<span class="what"><strong>' + escapeHtml(s.main) + "</strong>" +
        (s.extra.length ? ' <span class="x">· ' + escapeHtml(s.extra.join(" · ")) + "</span>" : "") +
        "</span>" +
        '<span><span class="badge ' + st.cls + '">' + escapeHtml(st.label) + "</span></span>" +
        '<span class="num">' + change + "</span></button>";
    }).join("");
    var more = data.releases.length > RH_SHOW
      ? '<button type="button" class="btn rh-more">Show all ' + rhCount(data.releases.length) +
        " releases</button>" : "";

    box.innerHTML =
      '<div class="rh-rels"><div class="rh-rels-in">' +
      '<div class="rh-rel-head"><span>Released (UTC)</span><span>What it did</span>' +
      '<span>Status</span><span class="num">Change</span></div>' +
      rows + "</div></div>" + more +
      '<p class="rh-legend"><strong>Live</strong> is the release the site serves now. ' +
      "<strong>Replaced</strong> releases stay stored, unchanged, so any past view can be " +
      "rebuilt exactly. <strong>Change</strong> is the number of values added, less any " +
      "withdrawn; a revised value leaves it unchanged.</p>";

    var btns = box.querySelectorAll(".rh-rel");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        toggleReleaseDetail(this, d, data.releases[Number(this.getAttribute("data-i"))]);
      });
    }
    var moreBtn = box.querySelector(".rh-more");
    if (moreBtn) {
      moreBtn.addEventListener("click", function () {
        for (var j = 0; j < btns.length; j++) btns[j].hidden = false;
        moreBtn.parentNode.removeChild(moreBtn);
      });
    }
    // Open the newest release: it is the one a person usually came to check.
    if (btns.length) toggleReleaseDetail(btns[0], d, data.releases[0]);
  }).catch(function (err) {
    box.innerHTML = loadFailed("the releases of " + (d.name || slug), err);
  });
}

var CHECK_LABELS = {
  series: "Series", observations: "Values", latest_period: "Newest period",
  dates: "Dates", first_quarter: "First quarter", quarters: "Quarters",
};

/* The validation summary an accepted release carries, as label → value.
   Lists, objects and links stay out: they are evidence, not a check. */
function releaseChecks(v, freq) {
  if (!v) return [];
  return Object.keys(v).filter(function (k) {
    var x = v[k];
    return (typeof x === "number" || (typeof x === "string" && x.length <= 40 &&
      !/^https?:/.test(x))) && k !== "published_at";
  }).map(function (k) {
    var x = v[k];
    var label = CHECK_LABELS[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");
    var val = typeof x === "number"
      ? x.toLocaleString("en-US", { maximumFractionDigits: 4 }).replace(/-/g, MINUS)
      : /^\d{4}-\d{2}-\d{2}$/.test(x) ? rhPeriod(x, freq) : x;
    return [label, val];
  });
}

function kvList(pairs) {
  return '<dl class="rh-kv">' + pairs.map(function (p) {
    return "<dt>" + escapeHtml(p[0]) + "</dt><dd>" + p[1] + "</dd>";
  }).join("") + "</dl>";
}

function toggleReleaseDetail(btn, d, r) {
  var open = btn.nextElementSibling && btn.nextElementSibling.classList.contains("rh-rel-detail");
  if (open) {
    btn.parentNode.removeChild(btn.nextElementSibling);
    btn.setAttribute("aria-expanded", "false");
    btn.querySelector(".chev").textContent = "▸";
    return;
  }
  btn.setAttribute("aria-expanded", "true");
  btn.querySelector(".chev").textContent = "▾";
  var detail = document.createElement("div");
  detail.className = "rh-rel-detail";

  var changed = kvList([
    ["New values", rhCount(r.new_values)],
    ["Revised", rhCount(r.revised)],
    ["Withdrawn", rhCount(r.withdrawn)],
  ]);
  var checks = releaseChecks(r.validation, d.frequency).map(function (p) {
    return [p[0], escapeHtml(p[1])];
  });
  var file = kvList([
    ["Fetched", escapeHtml(fmtStamp(r.retrieved_at))],
    ["Size", escapeHtml(fmtBytes(r.bytes))],
    ["SHA-256", '<code title="' + escapeHtml(r.sha256) + '">' +
      escapeHtml(r.sha256.slice(0, 12)) + "…</code>"],
  ]);
  detail.innerHTML =
    '<div class="rh-blocks">' +
    '<div><div class="h">What it changed</div>' + changed + "</div>" +
    (checks.length ? '<div><div class="h">' +
      (r.status === "rejected" ? "Validation" : "Checks passed") + "</div>" +
      kvList(checks) + "</div>" : "") +
    '<div><div class="h">Source file</div>' + file + "</div></div>" +
    (r.is_first_vintage
      ? '<p class="rh-note">The dataset’s first release: all ' + rhCount(r.recorded) +
        " values are the history as it stood at first ingest. Later releases are compared " +
        "against it.</p>"
      : (r.new_values || r.revised || r.withdrawn
        ? '<button type="button" class="btn rh-values">See every value it changed</button>' +
          '<div class="rh-values-box"></div>'
        : '<p class="rh-note">No value changed: the source file differed, the numbers did not.</p>'));
  btn.parentNode.insertBefore(detail, btn.nextElementSibling);

  var see = detail.querySelector(".rh-values");
  if (see) {
    see.addEventListener("click", function () {
      var out = detail.querySelector(".rh-values-box");
      see.disabled = true;
      out.innerHTML = '<div class="admin-loading">Loading the changed values…</div>';
      api("/releases/" + encodeURIComponent(d.dataset) + "/" + r.release_id + "/changes")
        .then(function (c) { out.innerHTML = renderChanges(c); see.parentNode.removeChild(see); })
        .catch(function (err) {
          see.disabled = false;
          out.innerHTML = loadFailed("this release's changes", err);
        });
    });
  }
}

function changeTable(rows, withPrior) {
  var SHOW = 20;
  var shown = rows.slice(0, SHOW);
  var dp = shown.reduce(function (d, r) {
    return Math.max(d, decimalsOf(r.value), withPrior ? decimalsOf(r.prior) : 0);
  }, 0);
  var body = shown.map(function (r) {
    return "<tr><td>" + escapeHtml(r.code) + "</td>" +
      '<td class="cell-item"><span class="en">' + escapeHtml(r.name || "") + "</span></td>" +
      '<td class="num">' + escapeHtml(r.period) + "</td>" +
      '<td class="num">' + (withPrior
        ? '<span class="was">' + fmtVal(r.prior, dp) + "</span> → " +
          '<span class="now">' + fmtVal(r.value, dp) + "</span>"
        : '<span class="now">' + fmtVal(r.value, dp) + "</span>") + "</td></tr>";
  }).join("");
  var more = rows.length > SHOW
    ? '<p class="table-empty">…and ' + (rows.length - SHOW).toLocaleString("en-US") +
      " more not shown.</p>" : "";
  return '<div class="table-wrap"><table class="data" data-no-enhance>' +
    "<thead><tr><th>Code</th><th>Item</th>" +
    '<th class="num">Period</th><th class="num">' + (withPrior ? "Was → Now" : "Value") +
    "</th></tr></thead><tbody>" + body + "</tbody></table></div>" + more;
}

function renderChanges(c) {
  var parts = [];
  var rev = c.revisions, add = c.new_values, gone = c.withdrawals;

  parts.push('<div class="detail-block"><div class="h">Revisions — ' +
    rev.count.toLocaleString("en-US") + "</div>" +
    (rev.count
      ? changeTable(rev.rows, true) +
        (rev.truncated ? '<p class="table-empty">List capped at ' + rev.rows.length +
          " rows; the count above is complete.</p>" : "")
      : '<span class="muted">No previously published value was changed.</span>') + "</div>");

  if (add.count) {
    var periods = add.rows.map(function (r) { return r.period; });
    var lo = periods.slice().sort()[0];
    var hi = periods.slice().sort().slice(-1)[0];
    var range = add.truncated
      ? "listed sample runs " + lo + " to " + hi
      : (lo === hi ? "all for " + lo : "covering " + lo + " to " + hi);
    parts.push('<div class="detail-block"><div class="h">New Values — ' +
      add.count.toLocaleString("en-US") + ' <span style="text-transform:none;letter-spacing:0">(' +
      escapeHtml(range) + ")</span></div>" + changeTable(add.rows, false) + "</div>");
  } else {
    parts.push('<div class="detail-block"><div class="h">New Values — 0</div>' +
      '<span class="muted">No new periods or series.</span></div>');
  }

  if (gone.count) {
    parts.push('<div class="detail-block"><div class="h">Withdrawn — ' +
      gone.count.toLocaleString("en-US") + "</div>" + changeTable(gone.rows, true) + "</div>");
  }
  return parts.join("");
}

/* ---------- traffic ---------- */

var TRAFFIC_WINDOWS = [7, 30, 90];

/* One tab at a time, so the page is never longer than its longest tab. The
   tab sits in the address (#traffic/30/ask) and switching never refetches. */
var TRAFFIC_TABS = [
  { id: "overview", label: "Overview" },
  { id: "readers", label: "Readers" },
  { id: "pages", label: "Pages" },
  { id: "ask", label: "Ask Box" },
  { id: "arrivals", label: "Arrivals" },
  { id: "places", label: "Places" },
];
var TRAFFIC_ROWS = 10;     // a long table shows this many until Show all
var TRAFFIC_TOP = 5;       // the overview's short lists
var trafficState = { d: null, days: 30, tab: "overview", all: {} };

/* The first TRAFFIC_ROWS of a long table, unless Show all was pressed. */
function trafficFold(rows, key) {
  rows = rows || [];
  return trafficState.all[key] ? rows : rows.slice(0, TRAFFIC_ROWS);
}

function trafficMore(total, key) {
  if (trafficState.all[key] || total <= TRAFFIC_ROWS) return "";
  return '<button type="button" class="btn traffic-more" data-more="' + key + '">Show all ' +
    fmtCount(total) + " rows</button>";
}

/* The console's only chart. Held at module scope so a theme switch can redraw
   it and a move to another page can dispose it. */
var trafficChart = null;

/* "2026-09-12" -> "12 Sep 2026" */
function fmtDay(iso) {
  if (!iso) return MISSING;
  var month = (MONTHS[Number(String(iso).slice(5, 7)) - 1] || "").slice(0, 3);
  return Number(String(iso).slice(8, 10)) + " " + month + " " + String(iso).slice(0, 4);
}

function fmtCount(v) {
  if (v === null || v === undefined) return MISSING;
  return v.toLocaleString("en-US");
}

/* Seconds as a person reads them. Under a minute keeps its seconds, because
   the difference between 20 and 50 seconds on a page is the whole question. */
function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return MISSING;
  seconds = Math.round(seconds);
  if (seconds < 60) return seconds + "s";
  var mins = Math.floor(seconds / 60);
  if (mins < 60) return mins + "m " + (seconds % 60) + "s";
  return Math.floor(mins / 60) + "h " + (mins % 60) + "m";
}

/* The "right now" panel refreshes itself; the handle lives here so leaving the
   page can stop it. */
var liveTimer = null;
var LIVE_REFRESH_MS = 20000;

function disposeTrafficChart() {
  if (trafficChart) {
    trafficChart.dispose();
    trafficChart = null;
  }
  if (liveTimer) {
    window.clearInterval(liveTimer);
    liveTimer = null;
  }
}

/* Counting stops silently if the log cannot be written, so a quiet day and a
   broken counter look identical until the age of the last request is stated. */
var TRAFFIC_SILENT_HOURS = 36;

function viewTraffic(target, arg, tab) {
  var days = TRAFFIC_WINDOWS.indexOf(Number(arg)) === -1 ? 30 : Number(arg);
  if (!TRAFFIC_TABS.some(function (t) { return t.id === tab; })) tab = "overview";
  target.innerHTML = '<div class="admin-loading">Loading traffic…</div>';
  api("/visits?days=" + days).then(function (d) {
    trafficState = { d: d, days: days, tab: tab, all: {} };
    target.innerHTML = trafficMarkup(d, days);
    wireTraffic(target, d, days);
  }).catch(function (err) {
    target.innerHTML = loadFailed("the traffic summary", err);
  });
}

/* Readers on the site in the last few minutes, and what they have open.
   Its own endpoint rather than part of the summary: it answers in memory,
   costs nothing, and is the one figure here worth asking for again while the
   page is open. */
function liveMarkup(d) {
  if (!d) return '<p class="table-empty">Live readership is unavailable.</p>';
  var minutes = Math.round((d.window_seconds || 300) / 60);
  var rows = (d.pages || []).filter(function (r) { return r.key; }).map(function (r) {
    return '<tr><td class="mono"><a href="' + escapeHtml(r.key) + '" target="_blank" ' +
      'rel="noopener">' + escapeHtml(r.key) + "</a></td>" +
      '<td class="num">' + fmtCount(r.visitors) + "</td></tr>";
  }).join("");
  var places = (d.countries || []).map(function (r) {
    return escapeHtml(countryName(r.key)) + " " + fmtCount(r.visitors);
  }).join(" · ");

  return '<div class="kpi-row">' +
    kpi(fmtCount(d.visitors), "Reading Now", d.visitors ? "ok" : "") +
    kpi(fmtCount((d.pages || []).length), "Pages Open", "") +
    kpi(fmtCount((d.countries || []).length), "Countries", "") + "</div>" +
    (rows
      ? '<div class="table-wrap"><table class="data" data-no-enhance><thead><tr>' +
        '<th>Page Open Now</th><th class="num">Readers</th></tr></thead><tbody>' +
        rows + "</tbody></table></div>"
      : '<p class="table-empty">No real reader on the site in the last ' +
        minutes + " minutes.</p>") +
    '<p class="source-line">Real readers only: the page reported in from their browser, ' +
    "on a home, office or phone network. Robots and data centres are left out. A reader " +
    "counts as here for " + minutes + " minutes after their last request; an open page " +
    "reports itself every minute, so somebody sitting still on one page stays counted." +
    (places ? " Now: " + places + "." : "") + "</p>";
}

function refreshLive(target) {
  var box = target.querySelector("#traffic-live");
  if (!box) return;
  api("/visits/live").then(function (d) {
    var still = target.querySelector("#traffic-live");
    if (still) still.innerHTML = liveMarkup(d);
  }).catch(function () {
    var still = target.querySelector("#traffic-live");
    // Silent: the window figures beside it are still good, and a red box
    // every twenty seconds would say the console is broken when it is not.
    if (still) still.innerHTML = '<p class="table-empty">Live readership is ' +
      "unavailable — the figures below are unaffected.</p>";
  });
}

function trafficMarkup(d, days) {
  var seg = '<div class="seg" role="group" aria-label="Window" id="traffic-seg">' +
    TRAFFIC_WINDOWS.map(function (w) {
      return '<button type="button" data-days="' + w + '"' +
        (w === days ? ' aria-pressed="true"' : "") + ">" + w + "D</button>";
    }).join("") + "</div>";

  var head =
    '<div class="admin-page-head"><h1>Traffic</h1><span class="spacer"></span>' + seg +
    '<button type="button" class="btn" id="traffic-refresh">Refresh</button>' +
    '<p class="admin-page-sub">Readership counted by the server itself, so readers on ' +
    "networks that block analytics scripts are counted too. No addresses are stored; the " +
    "only cookie is the one a reader accepts, and it holds a random number and the day it " +
    "was issued. " + escapeHtml(fmtDay(d.from)) + " to " + escapeHtml(fmtDay(d.to)) +
    (d.last_event ? " · last request " + escapeHtml(fmtStamp(d.last_event)) : "") +
    ".</p></div>";

  var hoursSilent = d.last_event
    ? (Date.now() - Date.parse(d.last_event)) / 3600000 : null;
  var banner = "";
  if (hoursSilent !== null && hoursSilent > TRAFFIC_SILENT_HOURS) {
    banner = '<div class="admin-alert"><span class="head">Counting may have stopped:</span> ' +
      "the last request recorded was " + escapeHtml(fmtAgo(hoursSilent)) +
      ". Either nothing reached the site, or the visit log on the data volume cannot be " +
      "written — the server log reports the write failure if that is the cause.</div>";
  }

  var tabs = '<div class="co-tabs" id="traffic-tabs" role="tablist">' +
    TRAFFIC_TABS.map(function (t) {
      return '<button type="button" role="tab" data-tab="' + t.id + '">' +
        escapeHtml(t.label) + "</button>";
    }).join("") + "</div>";

  return '<div id="traffic-root">' + head + banner + tabs + '<div id="traffic-tab"></div></div>';
}

/* What one tab shows. */
function trafficTab(d, tab) {
  if (tab === "readers") return trafficTime(d) + trafficPeople(d);
  if (tab === "pages") {
    return '<div class="admin-section">Most-Read Pages <span class="note">page views in this ' +
      "window</span></div>" +
      trafficTable(trafficFold(d.top_pages, "pages"), "Page",
        { link: true, dwell: true, empty: "No page views recorded in this window." }) +
      trafficMore((d.top_pages || []).length, "pages") + trafficDirectPages(d);
  }
  if (tab === "ask") return trafficAsk(d.ask);
  if (tab === "arrivals") {
    return '<div class="admin-section">How Readers Arrived <span class="note">only the sending ' +
      "site is recorded, never the page a reader came from</span></div>" +
      trafficArrivals(d) + trafficSources(d) + trafficDirect(d);
  }
  if (tab === "places") return trafficPlaces(d);
  return trafficOverview(d);
}

function trafficOverview(d) {
  // Every figure on this page is real readers only (app/visits.py
  // _is_reader). "Readers" is the sum of each day's readers, not a headcount:
  // the identifier rotates daily.
  var kpis =
    kpi(fmtCount(d.visitors), "Readers", "") +
    kpi(fmtCount(d.pageviews), "Page Views", "") +
    kpi(fmtDuration((d.dwell || {}).median_seconds), "Median Time on Page", "");

  var chart = d.counting_since
    ? '<div class="chart-panel">' +
      '<div class="controls"><span class="spacer"></span>' +
      '<button type="button" class="btn" id="traffic-png">Download PNG</button>' +
      '<button type="button" class="btn" id="traffic-csv">Download CSV</button></div>' +
      '<div class="chart" id="traffic-chart"></div>' +
      '<p class="source-line">Counted by the Plover Analytics server from its own ' +
      "request log. Days before counting began on this deployment are shown as gaps, " +
      "not as zero.</p>" + trafficCalc(d) + "</div>"
    : '<p class="table-empty">No requests recorded yet. Counting begins the moment this ' +
      "build is deployed, and the window fills in from that day forward.</p>" + trafficCalc(d);

  return '<div class="admin-section">Right Now <span class="note">from the server\u2019s ' +
    "memory, refreshed every 20 seconds</span></div>" +
    '<div id="traffic-live"><p class="admin-loading">Loading live readership…</p></div>' +
    '<div class="admin-section">This Window <span class="note">' +
    escapeHtml(fmtDay(d.from)) + " to " + escapeHtml(fmtDay(d.to)) + "</span></div>" +
    '<div class="kpi-row">' + kpis + "</div>" + leftOutNote(d) +
    '<div class="admin-section">Daily Readers <span class="note">readers and ' +
    "page views per day</span></div>" + chart +
    '<div class="admin-section">Most-Read Pages</div>' +
    trafficTable((d.top_pages || []).slice(0, TRAFFIC_TOP), "Page",
      { link: true, dwell: true, empty: "No page views recorded in this window." }) +
    trafficGo("pages", "All Pages") +
    '<div class="admin-section">How Readers Arrived</div>' +
    trafficArrivals(d, TRAFFIC_TOP) + trafficGo("arrivals", "All Arrivals");
}

function trafficGo(tab, label) {
  return '<button type="button" class="btn traffic-more" data-go="' + tab + '">' +
    escapeHtml(label) + "</button>";
}

/* Links we shared with a tag on the end (ploveranalytics.com/?ref=yt): who
   came through each, who went on to ask the box something, and who signed up.
   The YouTube and TikTok apps send no referrer, so the tag is the only way to
   tell those readers apart from somebody typing the address. */
function trafficSources(d) {
  var rows = d.sources || [];
  var head = '<div class="admin-section">Tagged Links <span class="note">links shared as ' +
    "ploveranalytics.com/?ref=yt; a sign-up is a new account that arrived through the tag " +
    "the same day or the day before</span></div>";
  if (!rows.length) {
    return head + '<p class="table-empty">Nobody has arrived through a tagged link in this ' +
      "window. Add ?ref= and a short name to a link you share (for example ?ref=yt or " +
      "?ref=tiktok) and its visitors appear here.</p>";
  }
  var signups = d.signups;
  var body = rows.map(function (r) {
    return '<tr><td class="mono">' + escapeHtml(r.key) + "</td>" +
      '<td class="num">' + fmtCount(r.visitors) + "</td>" +
      '<td class="num">' + fmtCount(r.asked) + "</td>" +
      '<td class="num">' + (signups ? fmtCount(r.signups || 0) : MISSING) + "</td></tr>";
  }).join("");
  return head + '<div class="table-wrap"><table class="data">' +
    '<thead><tr><th>Tag</th><th class="num">Readers</th>' +
    '<th class="num">Asked a Question</th><th class="num">Signed Up</th></tr></thead><tbody>' +
    body + "</tbody></table></div>" +
    (signups ? "" : '<p class="source-line">Sign-ups are not shown: accounts are switched ' +
      "off on this server.</p>");
}

/* The front page's question box: how often it was asked, by how many
   people, and what. The questions come from a log that holds nothing about
   who asked (app/ask_log.py); people per day come from the visit log. */
var ASK_ROUTE = {
  steps: "Worked Out Step by Step", reader: "Question Type Read", patterns: "Word Patterns",
  stock: "Stock Answer", macro: "US Economy Answer", error: "Failed on Our Side",
  // why a question was not answered (app/ask_miss.py)
  not_held: "Not in Plover's Data", policy: "Outside What Plover Does", unclear: "Not Understood",
  other_market: "Another Market",
};
var ASK_WHERE = { full: "Full Answer", preview: "Signed-Out Preview" };
// who a question came from (app/ask_log.py): never more than this one word
var ASK_FROM = { reader: "Reader", example: "Example Click", us: "Us" };

function askResult(answered) {
  if (answered === true) return '<span class="badge badge-ok">Answered</span>';
  if (answered === false) return '<span class="badge badge-warn">Not Answered</span>';
  return MISSING;
}

function askLabel(map, key) {
  if (!key) return MISSING;
  return escapeHtml(map[key] || String(key).replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }));
}

function trafficAsk(a) {
  var head = '<div class="admin-section">Ask Box <span class="note">questions typed into the ' +
    "front page\u2019s box, kept without anything about who asked</span></div>";
  if (!a) return head + '<p class="table-empty">The ask log is not available on this server.</p>';
  if (!a.searches) {
    return head + '<p class="table-empty">Nobody asked the box a question in this window. ' +
      "Questions are recorded from the day this section was added; earlier searches were not kept.</p>";
  }
  var share = a.judged ? Math.round((a.answered / a.judged) * 100) + "%" : MISSING;
  var weeks = a.weekly || [];
  var thisWeek = weeks.length && weeks[0].partial ? weeks[0] : null;
  var kpis =
    kpi(thisWeek ? fmtCount(thisWeek.reader) : MISSING, "Typed by Readers This Week", "") +
    kpi(fmtCount(a.searches), "Searches", "") +
    kpi(fmtCount(a.people), "Readers", "") +
    kpi(fmtCount(a.distinct_questions), "Different Questions", "") +
    kpi(share, "Answered", "") +
    kpi(fmtDuration(a.median_seconds), "Median Time", "");

  var unmarked = weeks.some(function (r) { return r.unmarked; });
  var weekly = weeks.length
    ? '<div class="admin-section">Questions Typed by Readers, by Week <span class="note">weeks ' +
      "start Monday (UTC); example clicks and our own checks are counted apart</span></div>" +
      '<div class="table-wrap"><table class="data"><thead><tr><th>Week Starting</th>' +
      '<th class="num">Typed by Readers</th><th class="num">Answered</th>' +
      '<th class="num">Example Clicks</th><th class="num">Our Own Checks</th>' +
      (unmarked ? '<th class="num">Not Marked</th>' : "") + "</tr></thead><tbody>" +
      weeks.map(function (r) {
        return '<tr><td class="ask-when" data-sort="' + escapeHtml(r.week) + '">' +
          escapeHtml(fmtDay(r.week)) + (r.partial ? " (so far)" : "") + "</td>" +
          '<td class="num">' + fmtCount(r.reader) + "</td>" +
          '<td class="num">' + fmtCount(r.reader_answered) + "</td>" +
          '<td class="num">' + fmtCount(r.example) + "</td>" +
          '<td class="num">' + fmtCount(r.us) + "</td>" +
          (unmarked ? '<td class="num">' + fmtCount(r.unmarked) + "</td>" : "") + "</tr>";
      }).join("") + "</tbody></table></div>"
    : "";

  var ROUTE_WORDS = { quick: "Code, no model (quick lane)", reader: "Model reads, code answers", steps: "Model writes the steps (SQL)",
    patterns: "Word patterns (model off)", macro: "Economy series", stock: "Stock box (preview)", explicit: "Run with blanks filled", miss: "No answer" };
  var routes = (a.routes || []).length
    ? '<div class="admin-section">How Questions Were Answered <span class="note">which route answered, how many it answered, ' +
      "and the median wait; code answers in under a second, one model call takes a few seconds, the step route takes 10 to 30</span></div>" +
      '<div class="table-wrap"><table class="data"><thead><tr><th>Route</th><th class="num">Searches</th><th class="num">Share</th>' +
      '<th class="num">Answered</th><th class="num">Median Time</th></tr></thead><tbody>' +
      a.routes.map(function (r) {
        return "<tr><td>" + escapeHtml(ROUTE_WORDS[r.route] || r.route) + "</td>" +
          '<td class="num">' + fmtCount(r.searches) + "</td>" +
          '<td class="num">' + (r.share == null ? MISSING : escapeHtml(String(r.share)) + "%") + "</td>" +
          '<td class="num">' + fmtCount(r.answered) + "</td>" +
          '<td class="num">' + fmtDuration(r.median_seconds) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
    : "";

  var days = (a.daily || []).filter(function (r) { return r.searches; }).slice().reverse();
  var daily = '<div class="table-wrap"><table class="data"><thead><tr><th>Day</th>' +
    '<th class="num">Searches</th><th class="num">Readers</th><th class="num">Answered</th>' +
    '<th class="num">Not Answered</th></tr></thead><tbody>' +
    trafficFold(days, "ask_daily").map(function (r) {
      return '<tr><td class="ask-when" data-sort="' + escapeHtml(r.day) + '">' + escapeHtml(fmtDay(r.day)) + "</td>" +
        '<td class="num">' + fmtCount(r.searches) + "</td>" +
        '<td class="num">' + fmtCount(r.people) + "</td>" +
        '<td class="num">' + fmtCount(r.answered) + "</td>" +
        '<td class="num">' + fmtCount(r.not_answered) + "</td></tr>";
    }).join("") + "</tbody></table></div>" + trafficMore(days.length, "ask_daily");

  var most = (a.most_asked || []).length
    ? '<div class="admin-section">Most-Asked Questions <span class="note">asked more than ' +
      "once in this window</span></div>" +
      '<div class="table-wrap"><table class="data"><thead><tr><th>Question</th>' +
      '<th class="num">Times Asked</th><th class="num">Answered</th><th class="num">Last Asked</th>' +
      "</tr></thead><tbody>" +
      trafficFold(a.most_asked, "ask_most").map(function (r) {
        return "<tr><td>" + escapeHtml(r.q) + "</td>" +
          '<td class="num">' + fmtCount(r.times) + "</td>" +
          '<td class="num">' + fmtCount(r.answered) + "</td>" +
          '<td class="num ask-when" data-sort="' + escapeHtml(r.last) + '">' + escapeHtml(fmtStamp(r.last)) + "</td></tr>";
      }).join("") + "</tbody></table></div>" + trafficMore(a.most_asked.length, "ask_most")
    : "";

  var recent = '<div class="admin-section">Latest Questions <span class="note">newest first, ' +
    "the last " + fmtCount((a.recent || []).length) + "</span></div>" +
    '<div class="table-wrap"><table class="data"><thead><tr><th>Asked</th><th>Question</th>' +
    "<th>From</th><th>Result</th><th>Answered By</th><th>Seen As</th><th class=\"num\">Time</th></tr></thead><tbody>" +
    trafficFold(a.recent, "ask_recent").map(function (r) {
      return '<tr><td class="ask-when" data-sort="' + escapeHtml(r.at) + '">' + escapeHtml(fmtStamp(r.at)) + "</td>" +
        "<td>" + escapeHtml(r.q) + "</td>" +
        "<td>" + (r.from ? askLabel(ASK_FROM, r.from) : "Not Marked") + "</td>" +
        "<td>" + askResult(r.answered) + "</td>" +
        "<td>" + askLabel(ASK_ROUTE, r.route) + "</td>" +
        "<td>" + askLabel(ASK_WHERE, r.where) + "</td>" +
        '<td class="num" data-sort="' + (r.seconds == null ? "" : r.seconds) + '">' + fmtDuration(r.seconds) + "</td></tr>";
    }).join("") + "</tbody></table></div>" + trafficMore((a.recent || []).length, "ask_recent");

  return head + '<div class="kpi-row">' + kpis + "</div>" + weekly +
    '<div class="admin-section">Searches by Day <span class="note">days with at least one ' +
    "search</span></div>" + daily + most + recent +
    '<p class="source-line">Real readers only. Searches and questions from the ask log; readers ' +
    "from the visit log, counted per day, so one person on two days is two. A question not sent " +
    "by our own page, or sent from a data centre, is left out, as are robots. " +
    "A repeated question answered from the server\u2019s cache is still counted. Answered " +
    "means the box gave an answer, not that the answer was what the reader wanted. Typed by " +
    "readers leaves out clicks on the page\u2019s own example questions and our own checks " +
    "(our addresses and browsers signed in to this console); questions asked before 21 October 2026 were not " +
    "marked, and questions before 28 October 2026 could not be checked for machines.</p>";
}

/* What every figure on the page leaves out, so a small number can be read as
   "the rest were machines" rather than "counting is broken". A line under the
   row it qualifies, not a panel. */
function leftOutNote(d) {
  var o = d.left_out;
  if (!o) return "";
  return '<p class="admin-page-sub">Real readers only. Left out of every figure on this ' +
    "page: " + fmtCount(o.robot_requests) + " requests from robots, and " +
    fmtCount(o.visits) + " visits whose page never reported in from a browser or that " +
    "came from a data centre — see Show calculation.</p>";
}

/* A share of a total, where a real but tiny share must not read as zero. */
function share(part, total) {
  if (!total) return MISSING;
  var pct = (part || 0) / total * 100;
  if (pct > 0 && pct < 1) return "<1%";
  return Math.round(pct) + "%";
}

/* How long a page was open, and how long a visit ran. Medians, not averages:
   one tab left open over a weekend would carry an average by itself. */
function trafficTime(d) {
  var dwell = d.dwell || {};
  var sessions = d.sessions || {};
  var bounce = sessions.samples ? share(sessions.single_page, sessions.samples) : MISSING;
  var note = fmtCount(dwell.samples) + " page" + (dwell.samples === 1 ? "" : "s") +
    " reported a reading time · " + fmtCount(sessions.samples) + " visit" +
    (sessions.samples === 1 ? "" : "s") + " reconstructed from the request log";
  return '<div class="admin-section">How Long Readers Stayed ' +
    '<span class="note">' + escapeHtml(note) + "</span></div>" +
    '<div class="kpi-row">' +
    kpi(fmtDuration(dwell.median_seconds), "Median Time on Page", "") +
    kpi(fmtDuration(sessions.median_seconds), "Median Visit Length", "") +
    kpi(sessions.pages_median === null || sessions.pages_median === undefined
      ? MISSING : String(sessions.pages_median), "Pages per Visit (Median)", "") +
    kpi(bounce, "Left After One Page", "") +
    "</div>";
}

/* Readers carrying a cookie: the only population that can be followed from one
   day to the next, and how much of the readership it covers. */
function trafficPeople(d) {
  var people = d.people || {};
  var consent = d.consent || {};
  var answered = (consent.granted || 0) + (consent.denied || 0) + (consent.unanswered || 0);
  // Counts first, share after: with a long log behind it a real handful of
  // consented reads rounds to 0% and reads as "nobody", which is not the same
  // thing as "two people".
  var note = fmtCount(consent.granted || 0) + " of " + fmtCount(answered) +
    " page views (" + share(consent.granted, answered) + ") came from a reader who " +
    "accepted the cookie, " + fmtCount(consent.denied || 0) + " (" +
    share(consent.denied, answered) + ") from one who declined";
  return '<div class="admin-section">Returning Readers ' +
    '<span class="note">' + escapeHtml(note) + "</span></div>" +
    '<div class="kpi-row">' +
    kpi(fmtCount(people.known), "Readers With a Cookie", "") +
    kpi(fmtCount(people.returning), "Known Before This Window", "") +
    kpi(fmtCount(people.new), "First Seen in This Window", "") +
    kpi(fmtCount(people.repeat), "Read on More Than One Day", "") +
    kpi(people.days_median === null || people.days_median === undefined
      ? MISSING : String(people.days_median), "Days Read (Median)", "") +
    "</div>" +
    (people.known
      ? ""
      : '<p class="table-empty">No reader in this window is carrying a cookie yet, so ' +
        "none can be told apart from one day to the next. Every other figure on this " +
        "page covers the whole readership and is unaffected.</p>");
}

/* ISO country code -> "Japan". Built into the browser, so no country table
   ships with the page; the bare code stands in where it is unavailable. */
var REGION_NAMES = (function () {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" });
  } catch (e) {
    return null;
  }
})();

function countryName(code) {
  if (!code) return MISSING;
  try {
    return (REGION_NAMES && REGION_NAMES.of(code)) || code;
  } catch (e) {
    return code;
  }
}

function trafficTable(rows, label, opts) {
  opts = opts || {};
  if (!rows || !rows.length) {
    return '<p class="table-empty">' + escapeHtml(opts.empty || "Nothing recorded.") + "</p>";
  }
  var body = rows.map(function (r) {
    // A referring host or network name is text someone else chose: shown as
    // text, and only our own paths are ever turned into links.
    var name = opts.link
      ? '<a href="' + escapeHtml(r.key) + '" target="_blank" rel="noopener">' +
        escapeHtml(r.key) + "</a>"
      : escapeHtml(opts.label ? opts.label(r.key) : r.key);
    return "<tr><td" + (opts.plain ? "" : ' class="mono"') + ">" + name + "</td>" +
      '<td class="num">' + fmtCount(r.views) + "</td>" +
      '<td class="num">' + fmtCount(r.visitors) + "</td>" +
      (opts.dwell
        ? '<td class="num"' +
          (r.dwell_samples
            ? ' title="' + fmtCount(r.dwell_samples) + ' reading' +
              (r.dwell_samples === 1 ? "" : "s") + ' measured"'
            : ' title="No reading time was reported for this page"') + ">" +
          fmtDuration(r.dwell_median) + "</td>"
        : "") + "</tr>";
  }).join("");
  return '<div class="table-wrap"><table class="data">' +
    "<thead><tr><th>" + escapeHtml(label) + '</th><th class="num">' +
    escapeHtml(opts.viewsLabel || "Page Views") + '</th>' +
    '<th class="num">Readers</th>' +
    (opts.dwell ? '<th class="num">Median Time on Page</th>' : "") +
    "</tr></thead><tbody>" + body + "</tbody></table></div>";
}

/* Referring sites and direct arrivals in one table, so the sources account for
   every page read. Without the direct row an empty table reads as broken
   detection rather than "nobody followed a link". */
function trafficArrivals(d, max) {
  var rows = (d.top_referrers || []).map(function (r) {
    return { key: r.key, views: r.views, visitors: r.visitors, ai: r.ai, direct: false };
  });
  var direct = d.direct || { views: 0, visitors: 0 };
  if (direct.views) {
    rows.push({ key: "Direct — no referring link", views: direct.views,
                visitors: direct.visitors, direct: true });
  }
  if (!rows.length) {
    return '<p class="table-empty">No page reads in this window.</p>';
  }
  rows.sort(function (a, b) { return b.views - a.views; });
  var total = rows.reduce(function (n, r) { return n + r.views; }, 0);
  var count = rows.length;
  rows = max ? rows.slice(0, max) : trafficFold(rows, "arrivals");
  var body = rows.map(function (r) {
    var share = total ? Math.round((r.views / total) * 100) : 0;
    var name = r.direct
      ? "<td><strong>" + escapeHtml(r.key) + "</strong></td>"
      : '<td class="mono">' + escapeHtml(r.key) +
        (r.ai ? ' <span class="badge badge-ok" title="A reader who followed a ' +
          'citation out of an assistant\u2019s answer">AI Answer</span>' : "") + "</td>";
    return "<tr>" + name +
      '<td class="num">' + fmtCount(r.views) + "</td>" +
      '<td class="num">' + share + "%</td>" +
      '<td class="num">' + fmtCount(r.visitors) + "</td></tr>";
  }).join("");
  return '<div class="table-wrap"><table class="data">' +
    '<thead><tr><th>Source</th><th class="num">Page Views</th>' +
    '<th class="num">Share</th><th class="num">Readers</th></tr></thead><tbody>' +
    body + "</tbody></table></div>" + (max ? "" : trafficMore(count, "arrivals"));
}

/* A direct arrival carries no referrer by definition, so the network it came
   from and the page it opened are the only things that can stand in for one. */
function trafficDirect(d) {
  if (!d.direct || !d.direct.views) return "";
  return '<div class="admin-section">Direct Arrivals by Network ' +
    '<span class="note">the closest thing to a source a direct arrival has' +
    "</span></div>" +
    trafficTable(trafficFold(d.direct_networks, "direct_networks"), "Network",
      { plain: true, empty: "No direct arrival could be attributed to a network." }) +
    trafficMore((d.direct_networks || []).length, "direct_networks");
}

function trafficDirectPages(d) {
  if (!d.direct || !d.direct.views) return "";
  return '<div class="admin-section">Pages Opened Directly ' +
    '<span class="note">opened with no referring link, so typed, bookmarked or ' +
    "followed from a mail or messaging app</span></div>" +
    trafficTable(trafficFold(d.direct_pages, "direct_pages"), "Page",
      { link: true, empty: "No page recorded for direct arrivals." }) +
    trafficMore((d.direct_pages || []).length, "direct_pages");
}

/* Country and network operator. Both are derived from the address while the
   request is in flight; neither is stored, and neither is more than an
   indication — see the caveats under "Show calculation". */
function trafficPlaces(d) {
  var geo = d.geoip || {};
  if (!geo.loaded) {
    return '<div class="admin-section">Readers by Country</div>' +
      '<p class="table-empty">Location tables are not loaded' +
      (geo.error ? ", so nothing can be placed yet. The server reported: " +
        escapeHtml(geo.error) : " yet. They are fetched once a month and read in " +
        "the background after the site comes up, so this fills in a few seconds " +
        "after a restart.") + "</p>";
  }

  return '<div class="admin-section">Readers by Country</div>' +
    trafficTable(trafficFold(d.top_countries, "countries"), "Country",
      { plain: true, label: countryName,
        empty: "No reader in this window could be placed in a country." }) +
    trafficMore((d.top_countries || []).length, "countries") +
    '<div class="admin-section">Readers by Network Operator ' +
    '<span class="note">the network a reader came from, which is not the same ' +
    "as who they work for</span></div>" +
    trafficTable(trafficFold(d.top_networks, "networks"), "Network",
      { plain: true, empty: "No reader in this window." }) +
    trafficMore((d.top_networks || []).length, "networks") +
    '<p class="source-line">Location and network from ' +
    '<a href="https://db-ip.com" target="_blank" rel="noopener">IP Geolocation by ' +
    "DB-IP</a>, " + escapeHtml(geo.edition || "") + " edition, " +
    fmtCount(geo.ranges) + " address ranges.</p>";
}

function trafficCalc(d) {
  return '<details class="calc"><summary>Show calculation</summary><div class="calc-body">' +
    "<p><strong>Only real readers are counted, everywhere on this page.</strong> A " +
    "visitor is a reader when three things hold on the same day. Their page reported how " +
    "long it was open, which only the script in the page can send, so a real browser " +
    "showed it. Their address was placed on a network. And that network is not a cloud, " +
    "host or CDN — Amazon, Google, Microsoft, DigitalOcean, OVH, Hetzner, Tencent, " +
    "Cloudflare and the like. Each test alone is weak; a scraper rarely passes all three. " +
    "Robots that name themselves, Google’s own page renderers and our own machines " +
    "and console browsers are never counted at all. In this window that left out " +
    fmtCount((d.left_out || {}).robot_requests) + " robot requests and " +
    fmtCount((d.left_out || {}).visits) + " visits that failed the test.</p>" +
    "<p>It is deliberately an undercount. A reader on a phone using iCloud Private Relay " +
    "leaves through Cloudflare or Akamai and is not counted; nor is a page closed within " +
    "a second, or opened with scripts blocked. It is still not proof that a person read " +
    "anything: a scraper can drive a real browser from a home connection.</p>" +
    "<p>A reader is one address-and-browser combination on one UTC day, identified by a " +
    "salted one-way hash. The salt stays on the data volume, so a day’s readers can be " +
    "counted without being identified and cannot be matched across days. The figure for a " +
    "window is the sum of its days — not a headcount. Someone who reads on ten days is ten.</p>" +
    "<p>A reader who accepts the banner is counted differently: they are given a cookie " +
    "holding a random number and the day it was issued, and their events are keyed on a " +
    "hash of it instead of on the daily one. That is what makes <strong>returning " +
    "readers</strong> answerable, and it is the only thing the cookie is for — it holds " +
    "nothing about the reader, and no address is stored for them either. " +
    "<strong>Known before this window</strong> counts readers whose cookie was issued " +
    "before the window opened; <strong>first seen in this window</strong> accepted inside " +
    "it. Clearing cookies makes a reader new again, and a reader who declines, or who " +
    "never answers, is counted only by the daily identity.</p>" +
    "<p><strong>Right now</strong> is counted in the server's memory, not from the log, by " +
    "the same test: a page that has reported in from its browser, on a network that is " +
    "not a data centre, with a request in the last five minutes. An open page reports " +
    "itself as it loads and once a minute after, so a reader sitting on one page does not " +
    "vanish. Nothing about it is stored, so it cannot be asked about yesterday and a " +
    "restart empties it.</p>" +
    "<p><strong>Time on page</strong> is reported by the page itself when it is closed or " +
    "hidden: the seconds it was actually visible, so a tab in the background does not " +
    "accrue time. Anything over an hour is recorded as an hour — that page was left open, " +
    "not read. <strong>Visit length</strong> is the span of one reader's requests, ended " +
    "by half an hour of silence, counting page reads and the chart data those pages " +
    "fetched. A visit of one page has no span to measure, so it is reported as " +
    "<strong>left after one page</strong> rather than averaged in as a very short visit.</p>" +
    "<p>A <strong>page view</strong> is a successful page request. Styles, scripts, images, " +
    "the admin console itself and the internal cache warm-up are not counted; a page request " +
    "that returned an error is not counted as a page view.</p>" +
    "<p>A <strong>source</strong> is the site that linked a reader here. Arrivals with no " +
    "referring link are counted as direct rather than dropped: a typed address, a bookmark, " +
    "and a link opened from a mail or messaging app all send nothing. A reader moving " +
    "between our own pages is not a new arrival and is not counted again. A source marked " +
    "<span class=\"badge badge-ok\">AI Answer</span> is an assistant such as ChatGPT: the " +
    "reader followed a citation here out of its answer.</p>" +
    "<p>These are estimates, not exact headcounts. A whole office behind one address on the " +
    "same browser version counts as one reader, while one person reading on a laptop and a " +
    "phone counts as two.</p>" +
    "<p><strong>Country and network</strong> are looked up from the address while the request " +
    "is being handled, and only the result is kept — the address is still never written down. " +
    "Read the network as an indication, never as proof: a corporate VPN, a home connection " +
    "and a phone all resolve to whoever runs that network, so a reader at a bank shows as the " +
    "bank only when they browse from its own network. Cloud and CDN addresses resolve to the " +
    "provider, and to wherever that address is registered rather than where the reader sat — " +
    "which is why a Cloudflare address can read as Australia.</p>" +
    '<p class="muted">Visit log: ' + escapeHtml(String(d.log_files)) + " file" +
    (d.log_files === 1 ? "" : "s") + ", " + escapeHtml(fmtBytes(d.log_bytes)) +
    " on the data volume · " + escapeHtml(fmtCount(d.lines_read)) + " records read" +
    (d.counting_since ? " · counting since " + escapeHtml(fmtDay(d.counting_since)) : "") +
    (d.unreadable_lines
      ? " · " + escapeHtml(fmtCount(d.unreadable_lines)) + " unreadable records skipped" : "") +
    (d.truncated ? " · capped at the most recent records" : "") + "</p></div></details>";
}

function trafficChartCfg(d) {
  // With only a day or two recorded, every reading is an isolated point: a
  // line between neighbours that do not exist draws nothing at all.
  var known = d.daily.filter(function (r) { return r.visitors !== null; }).length;
  return {
    series: [
      { name: "Readers", slot: 1,
        points: d.daily.map(function (r) { return [r.date, r.visitors]; }) },
      { name: "Page Views", slot: 2,
        points: d.daily.map(function (r) { return [r.date, r.pageviews]; }) },
    ],
    dp: 0,
    yAxisDp: 0,
    yAxisMinInterval: 1,  // requests are whole things; no half-visitor gridlines
    showPoints: known <= 14,
    yAxisName: "Per Day",
    isoPeriods: true,
    trust: "derived",
    sourceLine: "Plover Analytics — counted by the server from its own request log, " +
      d.from + " to " + d.to + ". Not an official statistic.",
  };
}

function wireTraffic(target, d, days) {
  document.getElementById("traffic-refresh").addEventListener("click", function () {
    viewTraffic(target, days, trafficState.tab);
  });
  var seg = document.getElementById("traffic-seg");
  var btns = seg ? seg.querySelectorAll("button") : [];
  for (var i = 0; i < btns.length; i++) {
    btns[i].addEventListener("click", function () {
      location.hash = trafficHash(Number(this.getAttribute("data-days")), trafficState.tab);
    });
  }
  document.getElementById("traffic-root").addEventListener("click", function (e) {
    var tab = e.target.closest("[data-tab], [data-go]");
    if (tab) {
      showTrafficTab(target, tab.getAttribute("data-tab") || tab.getAttribute("data-go"));
      if (tab.hasAttribute("data-go")) window.scrollTo(0, 0);
      return;
    }
    var more = e.target.closest("[data-more]");
    if (more) {
      trafficState.all[more.getAttribute("data-more")] = true;
      showTrafficTab(target, trafficState.tab);
    }
  });
  showTrafficTab(target, trafficState.tab);
}

function trafficHash(days, tab) {
  return "#traffic/" + days + (tab && tab !== "overview" ? "/" + tab : "");
}

/* Draws one tab from the data already loaded. The address is replaced, not
   pushed, so switching tabs does not refetch and Back leaves the page. */
function showTrafficTab(target, tab) {
  var d = trafficState.d;
  disposeTrafficChart();
  trafficState.tab = tab;
  var tabs = target.querySelectorAll("#traffic-tabs [data-tab]");
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].setAttribute("aria-selected", tabs[i].getAttribute("data-tab") === tab ? "true" : "false");
  }
  var box = document.getElementById("traffic-tab");
  if (!box) return;
  box.innerHTML = trafficTab(d, tab);
  if (window.history && history.replaceState) {
    history.replaceState(null, "", trafficHash(trafficState.days, tab));
  }
  if (tab !== "overview") return;
  refreshLive(target);
  liveTimer = window.setInterval(function () {
    // The console can be left open for a day; polling a page nobody is looking
    // at would add the watcher to the count it is watching.
    if (document.visibilityState !== "hidden") refreshLive(target);
  }, LIVE_REFRESH_MS);
  trafficDrawChart(d);
}

function trafficDrawChart(d) {
  var box = document.getElementById("traffic-chart");
  if (!box) return;
  if (trafficChart) {
    trafficChart.dispose();
    trafficChart = null;
  }
  trafficChart = obsChart(box, "line", trafficChartCfg(d));
  var stem = "observatory-traffic-" + d.from + "-to-" + d.to;
  document.getElementById("traffic-png").addEventListener("click", function () {
    trafficChart.exportPNG(stem + ".png");
  });
  document.getElementById("traffic-csv").addEventListener("click", function () {
    trafficChart.exportCSV(stem + ".csv", [
      "Plover Analytics — internal traffic",
      "Counted by the server from its own request log. Not an official statistic.",
      "Window: " + d.from + " to " + d.to,
      "Real readers only: a visitor whose page reported a reading time (a script ran in a",
      "browser), placed on a network that is not a cloud, host or CDN; the strict reading,",
      "so an undercount: readers behind iCloud Private Relay leave through a CDN and are excluded",
      "Robots, Google's own renderers, our own machines and console browsers are never counted",
      "Readers = distinct address-and-browser hashes on that UTC day that pass the test above",
      "The hash rotates daily by design, so days cannot be summed into a headcount",
      "Page views = successful page requests by those readers; assets and the admin console excluded",
      "An empty cell is a day before counting began, which is not the same as zero",
      "Time on page is reported by the page when hidden or closed; visit length is the",
      "span of one reader's requests, ended by 30 minutes of silence",
      "Returning readers are those carrying an accepted cookie; the rest cannot be",
      "followed across days by design",
      "Country and network: IP Geolocation by DB-IP (https://db-ip.com), CC BY 4.0",
    ]);
  });
}


/* ---------- audit log ---------- */

function viewAudit(target) {
  target.innerHTML = '<div class="admin-loading">Loading the audit log…</div>';
  api("/audit?limit=200").then(function (data) {
    var entries = data.entries;
    var rows = entries.map(function (e) {
      return "<tr>" +
        '<td class="num">' + fmtStamp(e.at) + "</td>" +
        "<td>" + badge(AUDIT_ACTIONS, e.action) + "</td>" +
        "<td>" + escapeHtml(e.detail || "") + "</td>" +
        "<td>" + escapeHtml(e.by || MISSING) + "</td>" +
        '<td class="mono">' + escapeHtml(e.ip || MISSING) + "</td></tr>";
    }).join("");
    target.innerHTML =
      '<div class="admin-page-head"><h1>Audit Log</h1>' +
      '<p class="admin-page-sub">Every sign-in and administrative action, newest first. The log ' +
      "is an append-only file on the data volume; nothing here can be edited from this " +
      "console.</p></div>" +
      (entries.length
        ? '<div class="table-wrap"><table class="data" data-no-enhance>' +
          '<thead><tr><th class="num">When (UTC)</th><th>Action</th><th>Detail</th>' +
          "<th>By</th><th>Address</th></tr></thead><tbody>" + rows + "</tbody></table></div>" +
          (entries.length === 200
            ? '<p class="table-empty">Showing the most recent 200 entries.</p>' : "")
        : '<p class="table-empty">No admin activity recorded yet.</p>');
  }).catch(function (err) {
    target.innerHTML = loadFailed("the audit log", err);
  });
}

/* ---------- boot ---------- */

window.addEventListener("hashchange", function () {
  if (/^#setup=/.test(location.hash || "")) { location.reload(); return; }
  if (document.getElementById("admin-view")) route();
});

initThemeToggle();
(function boot() {
  /* A setup link: admin.html#setup=<token>. The token stays in the fragment,
     which the browser never sends to a server. */
  var m = /^#setup=([A-Za-z0-9_\-]+)$/.exec(location.hash || "");
  if (m) { renderSetup(m[1]); return; }
  api("/session").then(function (s) {
    SHARED_PASSWORD_ON = !!s.shared_password;
    if (!s.enabled) renderDisabled();
    else if (!s.authenticated) renderLogin("");
    else { ME = s.user; renderShell(); route(); }
  }).catch(function () {
    renderLogin("");
  });
})();
