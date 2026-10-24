/* Admin console, US Companies tab (#companies, #companies/<cik>): the logo,
   name and website each US company is shown with on every US page. Backend:
   app/us_company_admin_api.py; store: app/us_companies.py. Loaded before
   admin.js, which routes to viewUsCompanies(). */
"use strict";

var UC_LOGO_STATE = {
  built_in: { label: "Built-In Logo", cls: "badge-neutral" },
  uploaded: { label: "Uploaded Logo", cls: "badge-info" },
  hidden: { label: "Logo Hidden", cls: "badge-warn" },
  none: { label: "No Logo", cls: "badge-danger" },
};
var UC_FIELDS = { name: "Name", website: "Website", logo: "Logo", logo_sha: "Logo File" };
var UC_SEARCH = "";

function ucBadge(state) {
  var s = UC_LOGO_STATE[state] || UC_LOGO_STATE.none;
  return '<span class="badge ' + s.cls + '">' + s.label + "</span>";
}

function ucLogo(url, size) {
  var box = 'width="' + size + '" height="' + size + '" style="width:' + size + "px;height:" + size +
    "px;border-radius:3px;border:1px solid var(--obs-border);background:var(--obs-logo-bg);" +
    'object-fit:contain;vertical-align:middle"';
  return url ? '<img src="' + escapeHtml(url) + '" alt="" ' + box + ">"
             : '<span style="display:inline-block;width:' + size + "px;height:" + size + 'px;vertical-align:middle"></span>';
}

function ucRows(list, empty) {
  if (!list.length) return '<p class="table-empty">' + escapeHtml(empty) + "</p>";
  return '<div class="table-wrap"><table class="data" data-no-enhance><thead><tr>' +
    "<th>Company</th><th>Ticker</th><th>Logo</th><th>Name Shown</th>" +
    '<th class="num">Edited (UTC)</th></tr></thead><tbody>' +
    list.map(function (c) {
      return '<tr class="clickable" data-cik="' + c.cik + '">' +
        "<td>" + ucLogo(c.logo, 20) + " <strong>" + escapeHtml(c.sec_name || c.name || "CIK " + c.cik) + "</strong></td>" +
        '<td class="mono">' + escapeHtml(c.ticker || "—") + "</td>" +
        "<td>" + ucBadge(c.logo_state) + "</td>" +
        "<td>" + escapeHtml(c.name || "—") + "</td>" +
        '<td class="num">' + (c.updated_at ? escapeHtml(fmtStamp(c.updated_at)) : "—") + "</td></tr>";
    }).join("") + "</tbody></table></div>";
}

function ucWireRows(target) {
  var rows = target.querySelectorAll("tr[data-cik]");
  for (var i = 0; i < rows.length; i++) {
    rows[i].addEventListener("click", function () {
      location.hash = "#companies/" + this.getAttribute("data-cik");
    });
  }
}

function viewUsCompanies(target, arg) {
  if (arg) return viewUsCompany(target, arg);
  target.innerHTML = '<div class="admin-loading">Loading US companies…</div>';
  api("/us-companies").then(function (d) {
    target.innerHTML =
      '<div class="admin-page-head"><h1>US Companies</h1>' +
      '<p class="admin-page-sub">The logo, name and website each US company is shown with on ' +
      "every US page. Choose a company to change them. Every change is recorded with who made it, " +
      "and filed figures are never touched.</p></div>" +
      '<div class="kpi-row">' +
      kpi(String(d.missing_logo.length), "S&P 500 Members Without A Logo", d.missing_logo.length ? "danger" : "ok") +
      kpi(String(d.edited.length), "Companies Edited", "") + "</div>" +
      '<div class="curate-bar"><input type="search" class="grow" id="uca-q" ' +
      'placeholder="Search by name or ticker, e.g. Apple, NVDA" aria-label="Company name or ticker" value="' +
      escapeHtml(UC_SEARCH) + '"><span class="count" id="uca-count"></span></div>' +
      '<div id="uca-results"></div>' +
      '<div class="admin-section">S&amp;P 500 Members Without A Logo <span class="note">upload one, ' +
      "or leave it blank</span></div>" +
      ucRows(d.missing_logo, "Every S&P 500 member has a logo.") +
      '<div class="admin-section">Edited Companies <span class="note">newest first</span></div>' +
      ucRows(d.edited, "No company has been edited yet.");
    ucWireRows(target);
    var box = document.getElementById("uca-q"), timer = null, seq = 0;
    function run() {
      var q = box.value.trim(), mine = ++seq;
      UC_SEARCH = box.value;
      var out = document.getElementById("uca-results"), count = document.getElementById("uca-count");
      if (!q) { out.innerHTML = ""; count.textContent = ""; return; }
      count.textContent = "Searching…";
      api("/us-companies?q=" + encodeURIComponent(q)).then(function (r) {
        if (mine !== seq) return;
        count.textContent = r.companies.length ? r.companies.length + (r.companies.length === 1 ? " match" : " matches")
                                               : "";
        out.innerHTML = '<div class="admin-section">Search Results</div>' +
          ucRows(r.companies, "No US company matches “" + q + "”. Try a ticker, like AAPL, or part of the name.");
        ucWireRows(out);
      }).catch(function (e) {
        if (mine !== seq) return;
        count.textContent = "";
        out.innerHTML = loadFailed("the search", e);
      });
    }
    box.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 200); });
    if (UC_SEARCH) run();
    box.focus();
  }).catch(function (e) { target.innerHTML = loadFailed("US companies", e); });
}

function viewUsCompany(target, cik) {
  target.innerHTML = '<div class="admin-loading">Loading the company…</div>';
  api("/us-companies/" + encodeURIComponent(cik)).then(function (d) {
    ucDetail(target, d);
  }).catch(function (e) { target.innerHTML = loadFailed("the company", e); });
}

function ucDetail(target, d) {
  var c = d.company, e = d.edit || {};
  var shown = c.name || c.sec_name || "CIK " + c.cik;
  var hist = (d.history || []).map(function (h) {
    var what = (h.changed || []).filter(function (f) { return f !== "logo_sha"; }).map(function (f) {
      var to = h.to[f], from = h.from[f];
      if (f === "logo") return "Logo: " + ucStateWord(from) + " → " + ucStateWord(to);
      return (UC_FIELDS[f] || f) + ": " + (from || "filed") + " → " + (to || "filed");
    }).join("; ");
    return "<tr><td>" + escapeHtml(fmtStamp(h.at)) + "</td><td>" + escapeHtml(h.by || "—") + "</td><td>" +
      escapeHtml(what || "Logo file replaced") + "</td></tr>";
  }).join("");
  target.innerHTML =
    '<div class="admin-page-head"><h1>' + (c.logo ? ucLogo(c.logo, 32) + " " : "") + escapeHtml(shown) + "</h1>" +
    '<span class="spacer"></span>' +
    '<a class="btn" href="/us-company.html?t=' + encodeURIComponent(c.ticker || c.cik) + '" target="_blank" rel="noopener">View On Site</a>' +
    '<button type="button" class="btn" id="uc-back">Back To List</button>' +
    '<p class="admin-page-sub">SEC number (CIK) <span class="mono">' + c.cik + "</span>" +
    (c.ticker ? " · " + escapeHtml(c.ticker) : "") + " · Name as filed: " + escapeHtml(c.sec_name || "—") +
    "</p></div>" +
    '<div class="admin-section">Logo <span class="note">' + ucBadge(d.logo_state) + "</span></div>" +
    '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-bottom:6px">' +
    '<div style="text-align:center"><div>' + (c.logo ? ucLogo(c.logo, 64)
      : '<span style="display:inline-flex;align-items:center;justify-content:center;width:64px;height:64px;' +
        'border:1px dashed var(--obs-border);border-radius:3px;font-size:11px;color:var(--obs-text-muted)">No logo</span>') +
    '</div><div class="muted" style="font-size:12px">Shown now</div></div>' +
    (d.builtin_logo && d.logo_state !== "built_in"
      ? '<div style="text-align:center"><div>' + ucLogo(d.builtin_logo, 64) + '</div><div class="muted" style="font-size:12px">Built-in</div></div>' : "") +
    '<div class="field"><label for="uc-file">Upload a logo</label>' +
    '<input type="file" id="uc-file" accept="image/png,image/jpeg,image/gif,image/webp">' +
    '<span class="hint">PNG, JPEG, GIF or WebP, up to ' + d.max_logo_kb + " KB. A square icon reads best.</span></div>" +
    '<button type="button" class="btn btn-primary" id="uc-upload" disabled>Upload Logo</button>' +
    (d.logo_state === "uploaded" || d.logo_state === "hidden"
      ? '<button type="button" class="btn" id="uc-reset">' + (d.builtin_logo ? "Use Built-In Logo" : "Remove Edit") + "</button>" : "") +
    (d.logo_state !== "hidden" && c.logo ? '<button type="button" class="btn btn-danger" id="uc-hide">Show No Logo</button>' : "") +
    "</div>" +
    '<div class="admin-section">Name And Website</div>' +
    '<div class="form-grid">' +
    '<div class="field"><label for="uc-name">Name shown</label><input type="text" id="uc-name" maxlength="120" value="' +
    escapeHtml(e.name || "") + '" placeholder="' + escapeHtml(shown) + '">' +
    '<span class="hint">Leave blank to show the filed name, made readable.</span></div>' +
    '<div class="field"><label for="uc-site">Website</label><input type="text" id="uc-site" value="' +
    escapeHtml(e.website || "") + '" placeholder="' + escapeHtml(d.found_website || "apple.com") + '">' +
    '<span class="hint">' + (d.found_website ? "Found: " + escapeHtml(d.found_website) + ". " : "") +
    "Leave blank to use the website found.</span></div></div>" +
    '<div style="margin-top:12px;display:flex;gap:10px;align-items:center">' +
    '<button type="button" class="btn btn-primary" id="uc-save">Save Name And Website</button>' +
    '<span id="uc-status" class="muted" role="status" style="font-size:13px"></span></div>' +
    '<div class="admin-section">History <span class="note">newest first</span></div>' +
    (hist ? '<div class="table-wrap"><table class="data" data-no-enhance><thead><tr><th>When</th><th>By</th>' +
      "<th>Change</th></tr></thead><tbody>" + hist + "</tbody></table></div>"
          : '<p class="table-empty">No changes yet.</p>');

  var status = document.getElementById("uc-status");
  function done(msg) {
    return function (fresh) { ucDetail(target, fresh); document.getElementById("uc-status").textContent = msg; };
  }
  function failed(err) { status.textContent = "Not saved: " + (err.message || String(err)); }
  document.getElementById("uc-back").addEventListener("click", function () { location.hash = "#companies"; });
  var file = document.getElementById("uc-file"), upload = document.getElementById("uc-upload");
  file.addEventListener("change", function () { upload.disabled = !file.files.length; });
  upload.addEventListener("click", function () {
    var f = file.files[0];
    if (!f) return;
    if (f.size > d.max_logo_kb * 1024) {
      status.textContent = "Not saved: that file is " + Math.ceil(f.size / 1024) + " KB; a logo can be up to " +
        d.max_logo_kb + " KB. Save it smaller and try again.";
      return;
    }
    status.textContent = "Uploading…";
    api("/us-companies/" + c.cik + "/logo", { method: "POST", headers: { "Content-Type": f.type || "application/octet-stream" }, body: f })
      .then(done("Logo uploaded. Every US page shows it within a minute.")).catch(failed);
  });
  var reset = document.getElementById("uc-reset");
  if (reset) reset.addEventListener("click", function () {
    status.textContent = "Saving…";
    api("/us-companies/" + c.cik + "/logo/reset", { method: "POST" })
      .then(done(d.builtin_logo ? "Back to the built-in logo." : "Edit removed.")).catch(failed);
  });
  var hide = document.getElementById("uc-hide");
  if (hide) hide.addEventListener("click", function () {
    status.textContent = "Saving…";
    api("/us-companies/" + c.cik + "/logo", { method: "DELETE" })
      .then(done("No logo is shown for this company now. Use the built-in logo or upload one to undo.")).catch(failed);
  });
  document.getElementById("uc-save").addEventListener("click", function () {
    status.textContent = "Saving…";
    api("/us-companies/" + c.cik, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: document.getElementById("uc-name").value,
                             website: document.getElementById("uc-site").value }) })
      .then(done("Saved. Every US page shows it within a minute.")).catch(failed);
  });
}

function ucStateWord(v) {
  return v === "uploaded" ? "uploaded" : v === "hidden" ? "hidden" : "default";
}
