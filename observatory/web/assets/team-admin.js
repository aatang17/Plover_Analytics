/* Admin console: people. The Team page (add a person, change what they can
   open, issue a setup link), My Account (change your password) and the
   set-your-password screen a setup link opens.

   Loaded before admin.js, which owns the shell, routing and the api()/post()
   helpers; everything here is called from there. Nothing in this file sends
   an email: a setup link is shown to the person who made it, to pass on
   themselves. */
"use strict";

function fmtWhen(epochSeconds) {
  if (!epochSeconds) return MISSING;
  return fmtStamp(new Date(epochSeconds * 1000).toISOString());
}

function permTags(perms, labels) {
  if (!perms.length) return '<span class="muted">None</span>';
  return perms.map(function (k) {
    return '<span class="badge badge-neutral">' + escapeHtml(labels[k] || k) + "</span>";
  }).join(" ");
}

function permChecks(name, all, chosen) {
  return '<div class="check-grid">' + all.map(function (p) {
    return '<label><input type="checkbox" name="' + name + '" value="' + p.key + '"' +
      (chosen.indexOf(p.key) !== -1 ? " checked" : "") + "> " + escapeHtml(p.label) + "</label>";
  }).join("") + "</div>";
}

function checkedValues(root, name) {
  var out = [];
  var boxes = root.querySelectorAll('input[name="' + name + '"]');
  for (var i = 0; i < boxes.length; i++) if (boxes[i].checked) out.push(boxes[i].value);
  return out;
}

var PERM_HELP = {
  team: "add people and change their permissions",
  operations: "ingest health, release history, traffic and the audit log",
  classification: "party profiles and the classification queue",
  writing: "write, edit and publish PloverResearch articles",
};

/* The link box shown once after adding someone or issuing a new link. */
/* The server emails the setup link when it can (res.emailed) and hands it
   back only when it could not, for the owner to send by hand. */
function setupLinkBox(person, res, delivery) {
  var hours = res.expires_hours, url = res.setup_url;
  if (res.emailed) {
    return '<div class="admin-alert" role="status"><span class="head">Setup link emailed to ' +
      escapeHtml(person.email) + ".</span> It works once, for " + hours +
      " hours, and lets " + escapeHtml(person.name) + " choose their own password.</div>";
  }
  return '<div class="admin-alert setup-link" role="status">' +
    '<span class="head">Setup link for ' + escapeHtml(person.name) + ".</span> " +
    (delivery ? "The email could not be sent, so send" : "Send") + " it to " +
    escapeHtml(person.email) + " yourself. It works once, for " + hours +
    " hours, and lets them choose their own password." +
    '<div class="setup-link-row"><input type="text" readonly value="' + escapeHtml(url) +
    '" aria-label="Setup link"><button type="button" class="btn" data-copy>Copy Link</button></div>' +
    "</div>";
}

function wireCopy(root) {
  var btns = root.querySelectorAll("[data-copy]");
  for (var i = 0; i < btns.length; i++) {
    btns[i].addEventListener("click", function (e) {
      var input = e.target.parentNode.querySelector("input, textarea");
      input.select();
      var done = function () { e.target.textContent = "Copied"; };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(done, function () {
          document.execCommand("copy"); done();
        });
      } else { document.execCommand("copy"); done(); }
    });
  }
}

/* ---------- Team ---------- */

function viewTeam(target, flash) {
  target.innerHTML = '<div class="admin-loading">Loading the team…</div>';
  api("/team").then(function (data) {
    var labels = {};
    data.permissions.forEach(function (p) { labels[p.key] = p.label; });
    var notice = "";
    if (ME && ME.shared) {
      notice = '<div class="admin-alert"><span class="head">You are signed in with the shared ' +
        "admin password.</span> Add yourself below with the Team permission, open your setup " +
        "link and sign in with your own account. When everyone who needs access has one, " +
        "remove <code>ADMIN_PASSWORD</code> from the server settings to switch the shared " +
        "password off.</div>";
    } else if (data.shared_password) {
      notice = '<div class="admin-alert"><span class="head">The shared admin password still ' +
        "works.</span> Anyone who knows it signs in with every permission. Remove " +
        "<code>ADMIN_PASSWORD</code> from the server settings once everyone has an account.</div>";
    }
    var rows = data.people.map(function (p) {
      return '<tr data-id="' + p.id + '">' +
        "<td>" + escapeHtml(p.name) + "</td>" +
        "<td>" + escapeHtml(p.email) + "</td>" +
        "<td>" + permTags(p.permissions, labels) + "</td>" +
        '<td class="num">' + fmtWhen(p.last_login_at) + "</td>" +
        "<td>" + (p.status === "active"
          ? (p.password_set ? '<span class="badge badge-ok">Active</span>'
            : '<span class="badge badge-warn">Awaiting Setup</span>')
          : '<span class="badge badge-neutral">Disabled</span>') + "</td>" +
        '<td class="row-acts"><button type="button" class="btn" data-edit="' + p.id +
        '">Edit</button></td></tr>' +
        '<tr class="edit-row" id="edit-' + p.id + '" hidden><td colspan="6"></td></tr>';
    }).join("");
    target.innerHTML =
      '<div class="admin-page-head"><h1>Team</h1>' +
      '<p class="admin-page-sub">Everyone who can sign in to this console, and what each ' +
      "person can open.</p></div>" + notice + (flash || "") +
      '<div class="admin-section">People <span class="note">' + data.people.length + "</span></div>" +
      (data.people.length
        ? '<div class="table-wrap"><table class="data team-table" data-no-enhance><thead><tr>' +
          '<th>Name</th><th>Email</th><th>Permissions</th><th class="num">Last Sign-in (UTC)</th>' +
          "<th>Status</th><th></th></tr></thead><tbody>" + rows + "</tbody></table></div>"
        : '<p class="table-empty">No accounts yet. Add the first person below.</p>') +
      '<div class="admin-section">Add Person</div>' +
      '<form id="add-person" class="team-form">' +
      '<div class="form-grid">' +
      '<div class="field"><label for="np-name">Name</label>' +
      '<input type="text" id="np-name" maxlength="80" required></div>' +
      '<div class="field"><label for="np-email">Email</label>' +
      '<input type="text" id="np-email" inputmode="email" autocomplete="off" required></div>' +
      '<div class="field wide"><label>Permissions</label>' +
      permChecks("np-perm", data.permissions, ["writing"]) +
      '<span class="hint">' + data.permissions.map(function (p) {
        return escapeHtml(p.label) + ": " + escapeHtml(PERM_HELP[p.key] || "");
      }).join(" · ") + "</span></div></div>" +
      '<div id="add-alert"></div>' +
      '<button type="submit" class="btn btn-primary" id="np-btn">Add Person</button>' +
      "</form>";
    wireCopy(target);

    document.getElementById("add-person").addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = document.getElementById("np-btn");
      btn.disabled = true;
      post("/team", {
        name: document.getElementById("np-name").value,
        email: document.getElementById("np-email").value,
        permissions: checkedValues(target, "np-perm"),
      }).then(function (res) {
        viewTeam(target, setupLinkBox(res.person, res, data.email_delivery));
      }).catch(function (err) {
        btn.disabled = false;
        document.getElementById("add-alert").innerHTML =
          '<div class="login-alert" role="alert">' + escapeHtml(err.message) + "</div>";
      });
    });

    var edits = target.querySelectorAll("[data-edit]");
    for (var i = 0; i < edits.length; i++) {
      edits[i].addEventListener("click", function (e) {
        var id = Number(e.target.getAttribute("data-edit"));
        var person = data.people.filter(function (p) { return p.id === id; })[0];
        openEdit(target, person, data.permissions, data.email_delivery);
      });
    }
  }).catch(function (err) {
    target.innerHTML = loadFailed("the team", err);
  });
}

function openEdit(target, person, all, delivery) {
  var row = document.getElementById("edit-" + person.id);
  if (!row.hidden) { row.hidden = true; return; }
  var cell = row.firstChild;
  cell.innerHTML =
    '<div class="edit-panel"><div class="form-grid">' +
    '<div class="field"><label for="ep-name-' + person.id + '">Name</label>' +
    '<input type="text" id="ep-name-' + person.id + '" maxlength="80" value="' +
    escapeHtml(person.name) + '"></div>' +
    '<div class="field"><label for="ep-status-' + person.id + '">Status</label>' +
    '<select id="ep-status-' + person.id + '">' +
    '<option value="active"' + (person.status === "active" ? " selected" : "") + ">Active</option>" +
    '<option value="disabled"' + (person.status === "disabled" ? " selected" : "") +
    ">Disabled</option></select></div>" +
    '<div class="field wide"><label>Permissions</label>' +
    permChecks("ep-perm-" + person.id, all, person.permissions) + "</div></div>" +
    '<div class="edit-alert"></div>' +
    '<div class="edit-acts"><button type="button" class="btn btn-primary" data-save>Save Changes</button>' +
    '<button type="button" class="btn" data-link>' + (delivery ? "Email New Setup Link" : "New Setup Link") + "</button>" +
    '<span class="hint">A new setup link replaces their password when they use it, and ' +
    "signs them out everywhere.</span></div></div>";
  row.hidden = false;
  var alertBox = cell.querySelector(".edit-alert");
  function fail(err) {
    alertBox.innerHTML = '<div class="login-alert" role="alert">' + escapeHtml(err.message) + "</div>";
  }
  cell.querySelector("[data-save]").addEventListener("click", function () {
    api("/team/" + person.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("ep-name-" + person.id).value,
        status: document.getElementById("ep-status-" + person.id).value,
        permissions: checkedValues(cell, "ep-perm-" + person.id),
      }),
    }).then(function () {
      viewTeam(target, '<div class="admin-alert" role="status"><span class="head">Saved.</span> ' +
        escapeHtml(person.name) + "’s account was updated.</div>");
    }).catch(fail);
  });
  cell.querySelector("[data-link]").addEventListener("click", function () {
    post("/team/" + person.id + "/setup-link").then(function (res) {
      viewTeam(target, setupLinkBox(person, res, delivery));
    }).catch(fail);
  });
}

/* ---------- My Account ---------- */

function viewAccount(target) {
  if (!ME || ME.shared) {
    target.innerHTML = '<p class="table-empty">You are signed in with the shared password, ' +
      "which has no account to manage.</p>";
    return;
  }
  target.innerHTML =
    '<div class="admin-page-head"><h1>My Account</h1>' +
    '<p class="admin-page-sub">Your sign-in details. Permissions are changed on the Team page ' +
    "by someone with the Team permission.</p></div>" +
    '<dl class="acct-dl"><dt>Name</dt><dd>' + escapeHtml(ME.name) + "</dd>" +
    "<dt>Email</dt><dd>" + escapeHtml(ME.email) + "</dd>" +
    "<dt>Permissions</dt><dd>" + permTags(ME.permissions, {
      team: "Team", operations: "Operations", classification: "Classification", writing: "Writing",
    }) + "</dd></dl>" +
    '<div class="admin-section">Change Password</div>' +
    '<form id="pw-form" class="team-form"><div class="form-grid">' +
    '<div class="field wide"><label for="pw-cur">Current Password</label>' +
    '<input type="password" id="pw-cur" autocomplete="current-password" required></div>' +
    '<div class="field"><label for="pw-new">New Password</label>' +
    '<input type="password" id="pw-new" autocomplete="new-password" minlength="12" required>' +
    '<span class="hint">At least 12 characters.</span></div>' +
    '<div class="field"><label for="pw-again">New Password Again</label>' +
    '<input type="password" id="pw-again" autocomplete="new-password" required></div></div>' +
    '<div id="pw-alert"></div>' +
    '<button type="submit" class="btn btn-primary" id="pw-btn">Change Password</button></form>';
  if (ME.permissions.indexOf("writing") !== -1) {
    target.insertAdjacentHTML("beforeend",
      '<div class="admin-section">AI Connections</div>' +
      '<p class="admin-page-sub keys-sub">Connect your own Claude Code or Codex to the research desk. ' +
      "It can read articles, start and edit drafts, and insert Plover charts, as you. It cannot " +
      "publish: you do that in the desk.</p>" +
      '<div id="keys-list"></div>' +
      '<div class="keys-new"><select id="key-kind" aria-label="Client">' +
      '<option>Claude Code</option><option>Codex</option><option>Other MCP client</option></select>' +
      '<button type="button" class="btn btn-primary" id="key-make">Create Key</button></div>' +
      '<div id="key-out"></div>');
    loadKeys();
    document.getElementById("key-make").addEventListener("click", function () {
      // one click, one key: the button stays off until the server answers
      var btn = this;
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "Creating…";
      var kind = document.getElementById("key-kind").value;
      post("/me/keys", { label: kind }).then(function (k) {
        document.getElementById("key-out").innerHTML = keySteps(kind, k.endpoint, k.token);
        wireCopy(document.getElementById("key-out"));
        loadKeys();
      }).catch(function (err) {
        document.getElementById("key-out").innerHTML =
          '<div class="login-alert" role="alert">' + escapeHtml(err.message) + "</div>";
      }).then(function () {
        btn.disabled = false;
        btn.textContent = "Create Key";
      });
    });
  }
  document.getElementById("pw-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var box = document.getElementById("pw-alert");
    var nw = document.getElementById("pw-new").value;
    if (nw !== document.getElementById("pw-again").value) {
      box.innerHTML = '<div class="login-alert" role="alert">The two new passwords do not match.</div>';
      return;
    }
    post("/me/password", { current: document.getElementById("pw-cur").value, new: nw })
      .then(function () {
        e.target.reset();
        box.innerHTML = '<div class="admin-alert" role="status"><span class="head">Password ' +
          "changed.</span> Your other devices have been signed out.</div>";
      }).catch(function (err) {
        box.innerHTML = '<div class="login-alert" role="alert">' + escapeHtml(err.message) + "</div>";
      });
  });
}

/* ---------- AI connections (personal keys for /mcp/research) ---------- */

function loadKeys() {
  var box = document.getElementById("keys-list");
  if (!box) return;
  api("/me/keys").then(function (r) {
    box.innerHTML = r.keys.length
      ? '<div class="table-wrap"><table class="data" data-no-enhance><thead><tr><th>Key</th>' +
        '<th class="num">Created (UTC)</th><th class="num">Last Used (UTC)</th><th></th></tr></thead><tbody>' +
        r.keys.map(function (k) {
          return "<tr><td>" + escapeHtml(k.label) + '</td><td class="num">' + fmtWhen(k.created_at) +
            '</td><td class="num">' + fmtWhen(k.last_used_at) + '</td><td class="row-acts">' +
            '<button type="button" class="btn" data-revoke="' + k.id + '">Revoke</button></td></tr>';
        }).join("") + "</tbody></table></div>"
      : '<p class="table-empty">No keys yet.</p>';
    Array.prototype.forEach.call(box.querySelectorAll("[data-revoke]"), function (b) {
      b.addEventListener("click", function () {
        if (b.disabled) return;
        b.disabled = true;
        api("/me/keys/" + b.getAttribute("data-revoke"), { method: "DELETE" })
          .then(loadKeys, function () { b.disabled = false; });
      });
    });
  }).catch(function (err) { box.innerHTML = loadFailed("your keys", err); });
}

/* Setup steps with the key filled in. Shown once: only a hash is stored. */
function keySteps(kind, endpoint, token) {
  function block(label, text) {
    return '<div class="key-step"><span class="key-step-label">' + escapeHtml(label) + "</span>" +
      '<div class="setup-link-row">' + (text.indexOf("\n") !== -1
        ? '<textarea readonly rows="' + text.split("\n").length + '" aria-label="' + escapeHtml(label) + '">' +
          escapeHtml(text) + "</textarea>"
        : '<input type="text" readonly value="' + escapeHtml(text) + '" aria-label="' + escapeHtml(label) + '">') +
      '<button type="button" class="btn" data-copy>Copy</button></div></div>';
  }
  var head = '<div class="admin-alert" role="status"><span class="head">Key created.</span> ' +
    "Copy it now: it is shown once. Anyone holding it can edit drafts as you, so keep it out of " +
    "shared files.";
  if (kind === "Claude Code") {
    // --scope user: available in every folder, not only the one the command
    // was run in (Claude Code's default is the current folder).
    return head + block("Run once in a terminal",
      "claude mcp add --scope user --transport http plover-research " + endpoint +
      ' --header "Authorization: Bearer ' + token + '"') +
      '<p class="hint-line">Then start <code>claude</code> in any folder and type <code>/mcp</code>: ' +
      "plover-research should show as connected. Ask, for example: \u201cResearch the BoJ\u2019s " +
      "latest decision and draft a PloverResearch note with a chart of the 10-year JGB yield.\u201d</p></div>";
  }
  if (kind === "Codex") {
    // The key goes in the config itself: the Codex app does not read a shell
    // profile, so an environment variable set in ~/.zshrc never reaches it.
    return head + block("Add to ~/.codex/config.toml (works in the Codex app and the terminal)",
        '[mcp_servers.plover-research]\nurl = "' + endpoint + '"\nhttp_headers = { "Authorization" = "Bearer ' +
        token + '" }') +
      '<p class="hint-line">Restart Codex, then ask it to list the PloverResearch articles. If Codex ' +
      "answers that a model is not supported, set <code>model</code> in the same file to one your " +
      "ChatGPT plan offers.</p></div>";
  }
  return head + block("Address", endpoint) + block("Header", "Authorization: Bearer " + token) +
    '<p class="hint-line">Transport: Streamable HTTP (stateless JSON-RPC over POST).</p></div>';
}

/* ---------- Setup link ---------- */

function renderSetup(token) {
  ROOT.innerHTML =
    '<div class="admin-login-wrap"><div class="admin-login-card">' +
    "<h1>Set Your Password</h1>" +
    '<p class="sub" id="setup-sub">Checking your link…</p>' +
    '<div id="login-alert"></div>' +
    '<form id="setup-form" hidden>' +
    '<label for="setup-pw">New Password</label>' +
    '<input type="password" id="setup-pw" autocomplete="new-password" minlength="12" required>' +
    '<label for="setup-again">New Password Again</label>' +
    '<input type="password" id="setup-again" autocomplete="new-password" required>' +
    '<p class="login-hint">At least 12 characters.</p>' +
    '<button type="submit" class="btn btn-primary" id="setup-btn">Set Password and Sign In</button>' +
    "</form></div></div>";
  post("/setup/check", { token: token }).then(function (who) {
    document.getElementById("setup-sub").textContent =
      "For " + who.name + " (" + who.email + "). Choose the password you will sign in with.";
    var form = document.getElementById("setup-form");
    form.hidden = false;
    document.getElementById("setup-pw").focus();
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var pw = document.getElementById("setup-pw").value;
      if (pw !== document.getElementById("setup-again").value) {
        showLoginAlert("The two passwords do not match.");
        return;
      }
      var btn = document.getElementById("setup-btn");
      btn.disabled = true;
      post("/setup", { token: token, password: pw }).then(function (res) {
        ME = res.user;
        history.replaceState(null, "", location.pathname);
        renderShell();
        route();
      }).catch(function (err) {
        btn.disabled = false;
        showLoginAlert(err.message);
      });
    });
  }).catch(function (err) {
    document.getElementById("setup-sub").textContent = "";
    showLoginAlert(err.message);
  });
}
