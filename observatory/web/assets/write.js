/* The Writer Desk (write.html): where a person who writes for a publication on
   PloverResearch signs in, finds their articles, and — as an owner — runs the
   publication and its members. Articles open in the editor (desk.html).

   Every number and name here comes from /admin/api/write and
   /admin/api/research; the server decides what this person may see and do
   (app/writers.py) and the page only hides what would be refused anyway.

   Routes (hash): #/p/<id> articles · #/p/<id>/members · #/p/<id>/assistant ·
   #/p/<id>/settings · #/account · #/new (open a publication: one of your own,
   or for anyone if you are on the Plover team). */
(function () {
  "use strict";

  const root = document.getElementById("app");
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.prototype.slice.call((el || document).querySelectorAll(sel));
  const esc = s => escapeHtml(s == null ? "" : s);

  let ME = null;
  let theme = null;
  try { theme = localStorage.getItem("wd-theme"); } catch (e) { /* private window */ }

  const ROLE = { owner: "Owner", editor: "Editor", writer: "Writer" };
  const ROLE_NOTE = {
    owner: "Publishes, runs the publication's settings, and invites and removes people.",
    editor: "Writes, edits and publishes every article in the publication.",
    writer: "Writes their own articles and asks an editor to publish them.",
  };
  const STATUS = { draft: "Draft", published: "Published", withdrawn: "Withdrawn" };

  const I = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICONS = {
    doc: I('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>'),
    people: I('<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.6c2.3.2 4 1.6 4.5 4.4"/>'),
    gear: I('<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>'),
    key: I('<circle cx="8" cy="15" r="3.5"/><path d="M10.5 12.5L19 4M16 7l2.5 2.5M14 9l2 2"/>'),
    plus: I('<path d="M12 5v14M5 12h14"/>'),
    theme: I('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'),
  };

  /* ------------------------------------------------------------ requests */

  function api(path, opts) {
    return fetch("/admin/api" + path, Object.assign({ credentials: "same-origin" }, opts || {}))
      .then(r => r.json().catch(() => ({})).then(body => {
        if (r.status === 401 && path !== "/login" && ME) {
          // signed out elsewhere, or the session ran out: back to the sign-in screen
          ME = null;
          signInScreen();
        }
        if (!r.ok) {
          const d = body.detail;
          const msg = Array.isArray(d) ? "Check the form: " + d.map(x => x.msg).join("; ") + "."
            : (d || "The request failed (" + r.status + "). Try again.");
          const err = new Error(msg);
          err.status = r.status;
          throw err;
        }
        return body;
      }));
  }
  const send = (method, path, body) => api(path, {
    method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });

  function toast(text) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("show"), 3500);
  }

  /* A button that sends a change stays off until the server answers. */
  function busy(btn, label, work) {
    if (btn.disabled) return Promise.resolve();
    const was = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    return work().then(
      v => { btn.disabled = false; btn.textContent = was; return v; },
      err => { btn.disabled = false; btn.textContent = was; throw err; });
  }

  const when = ts => {
    if (ts == null) return "";
    const iso = typeof ts === "number" ? new Date(ts * 1000).toISOString() : ts;
    return fmtStamp(iso);
  };
  const initials = name => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2)
    .map(w => w[0].toUpperCase()).join("");

  /* ------------------------------------------------------------ routing */

  function route() {
    const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if (p[0] === "p" && p[1]) return { sec: "pub", id: Number(p[1]), tab: p[2] || "articles" };
    if (p[0] === "account" || p[0] === "new") return { sec: p[0] };
    return { sec: "pub", id: null, tab: "articles" };
  }
  const go = path => { location.hash = "#/" + path; };

  function applyTheme() {
    const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }

  /* ------------------------------------------------------------ shell */

  function shell(r, main) {
    applyTheme();
    const pubs = ME.publications.map(p => `<button class="navitem ${r.sec === "pub" && r.id === p.id ? "on" : ""}" data-go="p/${p.id}">` +
      `<span class="mini">${esc(initials(p.name))}</span><span>${esc(p.name)}</span></button>`).join("");
    root.innerHTML = `<div class="shell"><aside class="nav">
      <div class="brand"><span class="logo">PR</span><div><b>Writer Desk</b><small>${esc(ME.person.email)}</small></div></div>
      <div class="navsec first">Publications</div><div class="navlist">${pubs}
        ${ME.can_create_publications ? `<button class="navitem add ${r.sec === "new" ? "on" : ""}" data-go="new">${ICONS.plus}<span>Open a Publication</span></button>` : ""}</div>
      <div class="navsec">Account</div><div class="navlist">
        <button class="navitem ${r.sec === "account" ? "on" : ""}" data-go="account">${ICONS.key}<span>My Account</span></button></div>
      <div class="navfoot"><span class="av sm me">${esc(initials(ME.person.name))}</span><span><a href="/research">PloverResearch</a></span>
        <button id="wd-theme" title="Switch light / dark" aria-label="Switch light or dark">${ICONS.theme}</button></div>
    </aside><div class="main">${main}</div></div><div class="toast" id="toast" role="status" aria-live="polite"></div>`;
    $$("[data-go]").forEach(b => b.addEventListener("click", e => { e.preventDefault(); go(b.dataset.go); }));
    $("#wd-theme").addEventListener("click", () => {
      theme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem("wd-theme", theme); } catch (e) { /* private window */ }
      applyTheme();
    });
  }

  const topbar = crumbs => `<header class="topbar"><nav class="crumbs" aria-label="Breadcrumb">${crumbs.map((c, i) =>
    (i ? '<span class="sep">/</span>' : "") + (c[1] ? `<a href="#/${c[1]}">${esc(c[0])}</a>` : `<b>${esc(c[0])}</b>`)).join("")}</nav></header>`;
  const pageHead = (title, sub, actions) => `<div class="pagehead"><div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}</div>` +
    (actions ? `<div class="actions">${actions}</div>` : "") + "</div>";
  const band = (title, note) => `<h2 class="band">${esc(title)}${note != null ? ` <span class="h2-note">${esc(note)}</span>` : ""}</h2>`;
  const page = inner => `<div class="content"><div class="page">${inner}</div></div>`;
  const loading = what => `<p class="muted wd-loading">Loading ${esc(what)}…</p>`;
  const failed = (what, err) => `<div class="notice wd-bad" role="alert">Could not load ${esc(what)}: ${esc(err.message)} ` +
    `<button type="button" class="linkbtn" data-reload>Try again</button></div>`;

  /* ------------------------------------------------------------ sign-in */

  function signInScreen() {
    applyTheme();
    const s = (ME && ME.sign_in) || {};
    const emailForm = s.email_links && s.email_delivery
      ? `<form id="wd-link" class="wd-form"><label for="wd-email">Email address</label>
          <input id="wd-email" type="email" autocomplete="email" required placeholder="name@company.com">
          <button class="btn primary" type="submit">Email Me a Sign-In Link</button></form>
          <div id="wd-link-out"></div>`
      : `<div class="notice">Sign-in links are not emailed from this site yet. Ask the owner of your
          publication for one: they make it on their Members page and send it to you.</div>`;
    root.innerHTML = `<div class="state wd-signin"><span class="logo wd-logo">PR</span><h1>Writer Desk</h1>
      <p>Write and publish research on PloverResearch.</p>${emailForm}
      <details class="wd-pw"${s.email_links && s.email_delivery ? "" : " open"}><summary>Plover team: sign in with your password</summary>
        <form id="wd-pwform" class="wd-form"><label for="wd-pwemail">Email address</label>
          <input id="wd-pwemail" type="email" autocomplete="username" required>
          <label for="wd-pw">Password</label><input id="wd-pw" type="password" autocomplete="current-password" required>
          <button class="btn" type="submit">Sign In</button><p class="err" id="wd-pw-err" role="alert"></p></form>
      </details></div><div class="toast" id="toast"></div>`;
    const link = $("#wd-link");
    if (link) link.addEventListener("submit", e => {
      e.preventDefault();
      const btn = $("button", link), out = $("#wd-link-out"), email = $("#wd-email").value.trim();
      busy(btn, "Sending…", () => fetch("/api/v1/account/signin-link", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, returnTo: "write.html" }),
      }).then(r => r.json().catch(() => ({})).then(body => {
        if (r.status === 403) throw new Error("That address does not write for any publication here. Ask the owner of your publication to invite you.");
        if (!r.ok) throw new Error(body.detail || "The link could not be sent. Try again in a few minutes.");
        out.innerHTML = `<div class="notice" role="status">Check your inbox at <b>${esc(email)}</b>. The link works once and expires in ${esc(body.expires_in_minutes || 30)} minutes.</div>`;
      }))).catch(err => { out.innerHTML = `<p class="err" role="alert">${esc(err.message)}</p>`; });
    });
    $("#wd-pwform").addEventListener("submit", e => {
      e.preventDefault();
      const btn = $("button", e.target);
      busy(btn, "Signing in…", () => send("POST", "/login", { email: $("#wd-pwemail").value, password: $("#wd-pw").value }))
        .then(start).catch(err => { $("#wd-pw-err").textContent = err.message; });
    });
  }

  function notWriterScreen() {
    applyTheme();
    root.innerHTML = `<div class="state wd-signin"><span class="logo wd-logo">PR</span><h1>Writer Desk</h1>
      <p>You are signed in as <b>${esc(ME.email || "")}</b>, but you do not write for any publication yet.
      Ask the owner of the publication you write for to invite you.</p>
      <button class="btn" id="wd-out" type="button">Sign Out</button></div>`;
    $("#wd-out").addEventListener("click", signOut);
  }

  function signOut() {
    send("POST", "/write/signout").then(() => { ME = null; start(); }).catch(err => toast(err.message));
  }

  /* ------------------------------------------------------------ a publication */

  function pubOf(id) { return ME.publications.find(p => p.id === id) || null; }

  function pubView(r) {
    const pub = pubOf(r.id) || ME.publications[0];
    if (!r.id || !pubOf(r.id)) { history.replaceState(null, "", "#/p/" + pub.id); r.id = pub.id; }
    const owner = pub.role === "owner";
    const known = ["articles", "members", "assistant"].concat(owner && !pub.home ? ["settings"] : []);
    const tab = known.indexOf(r.tab) === -1 ? "articles" : r.tab;
    const tabs = [["articles", "Articles"], ["members", "Members"], ["assistant", "Assistant Settings"]]
      .concat(owner && !pub.home ? [["settings", "Settings"]] : []);
    const actions = tab === "articles"
      ? `<button class="btn" id="wd-import" type="button">Import Markdown</button>
         <button class="btn primary" id="wd-new" type="button">New Article</button>
         <input type="file" id="wd-file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden>` : "";
    const head = pageHead(esc(pub.name),
      `${esc(ROLE[pub.role])} · Public page <a href="${esc(pub.base)}" target="_blank" rel="noopener">${esc(pub.base)}</a>`, actions) +
      `<div class="tabs wd-tabs" role="tablist">${tabs.map(t => `<button type="button" role="tab" aria-selected="${t[0] === tab}" class="${t[0] === tab ? "on" : ""}" data-go="p/${pub.id}${t[0] === "articles" ? "" : "/" + t[0]}">${t[1]}</button>`).join("")}</div>` +
      '<div id="wd-body"></div>';
    shell(r, topbar([[pub.name, tab === "articles" ? null : "p/" + pub.id]].concat(tab === "articles" ? [] : [[tabs.find(t => t[0] === tab)[1]]])) + page(head));
    const body = $("#wd-body");
    if (tab === "members") return membersTab(pub, body);
    if (tab === "settings") return settingsTab(pub, body);
    if (tab === "assistant") return assistantTab(pub, body);
    articlesTab(pub, body);
  }

  /* ---- articles ---- */

  const UNDO_MS = 8000;
  const HANDOFF_KEY = "plover-delete-draft";   // set by the editor's Delete Draft
  let PENDING = null;                           // {id, title, timer}

  function articleRow(a, pub) {
    const ask = !!a.review_requested_at;
    const addr = a.slug ? pub.base + "/" + a.slug : "";
    const tags = [`<span class="tag">${esc(STATUS[a.status] || a.status)}${a.status === "published" ? " v" + a.published_version : ""}</span>`];
    if (a.status === "published" && a.unpublished_changes) tags.push('<span class="tag">Unpublished Changes</span>');
    if (ask) tags.push('<span class="tag warn">Ready for Review</span>');
    const meta = [`Edited ${when(a.updated_at)} by ${esc(a.updated_by)}`];
    if (ask) meta.push(`asked to publish by ${esc(a.review_requested_by)}`);
    if (a.status === "published") meta.push(`<a href="${esc(addr)}" target="_blank" rel="noopener">${esc(addr)}</a>`);
    return `<li class="wd-art${ask && a.can.publish ? " wd-ask" : ""}" data-id="${a.id}">
      <div><b><a href="desk.html?id=${a.id}">${esc(a.title || "Untitled")}</a></b><small>${meta.join(" · ")}</small></div>
      <span class="wd-tags">${tags.join("")}</span>
      <span class="wd-acts"><a class="btn sm" href="desk.html?id=${a.id}">Edit</a>` +
      (a.can.delete && !a.published_version ? `<button class="btn sm danger" type="button" data-del="${a.id}">Delete</button>` : "") +
      "</span></li>";
  }

  function articlesTab(pub, body) {
    body.innerHTML = loading("the articles");
    $("#wd-new").addEventListener("click", e => {
      busy(e.target, "Starting…", () => send("POST", "/research/articles", { publication_id: pub.id }))
        .then(a => { location.href = "desk.html?id=" + a.id; }).catch(err => toast(err.message));
    });
    const file = $("#wd-file");
    $("#wd-import").addEventListener("click", () => file.click());
    file.addEventListener("change", () => {
      const f = file.files[0];
      if (!f) return;
      f.text().then(text => send("POST", "/research/import", { markdown: text, publication_id: pub.id }))
        .then(a => { location.href = "desk.html?id=" + a.id; }).catch(err => toast(err.message));
    });
    api("/research/articles?publication_id=" + pub.id).then(data => {
      const list = data.articles;
      const n = k => list.filter(a => a.status === k).length;
      const asked = list.filter(a => a.review_requested_at).length;
      const canPublish = pub.role !== "writer";
      const stats = `<div class="stats">
        <div class="stat"><span class="lbl">Drafts</span><b>${n("draft").toLocaleString()}</b><small>Not on the site yet</small></div>
        <div class="stat${asked && canPublish ? " warn" : ""}"><span class="lbl">Ready for Review</span><b>${asked.toLocaleString()}</b><small>${canPublish ? "Writers asked you to publish" : "Waiting for an editor"}</small></div>
        <div class="stat"><span class="lbl">Published</span><b>${n("published").toLocaleString()}</b><small>On ${esc(pub.base)}</small></div>
        <div class="stat"><span class="lbl">Withdrawn</span><b>${n("withdrawn").toLocaleString()}</b><small>Taken down, versions kept</small></div></div>`;
      const note = pub.role === "writer"
        ? '<div class="notice">You see the articles you started or are credited on. When one is ready, press <b>Ask to Publish</b> in the editor and an editor of this publication publishes it.</div>'
        : "";
      body.innerHTML = stats + band("Articles", list.length.toLocaleString()) + note + '<div id="wd-alert"></div>' +
        (list.length ? `<ul class="rows wd-list">${list.map(a => articleRow(a, pub)).join("")}</ul>`
          : '<p class="empty wd-empty">No articles yet. Start one with New Article, or import a Markdown draft.</p>') +
        (data.backup ? backupLine(data.backup) : "");
      $$("[data-del]", body).forEach(b => b.addEventListener("click", () => {
        const li = b.closest("li");
        deleteLater(body, Number(b.dataset.del), $("b", li).textContent);
      }));
      const bk = $("#wd-backup");
      if (bk) bk.addEventListener("click", () => busy(bk, "Backing up…", () => send("POST", "/research/backup"))
        .then(() => articlesTab(pub, body)).catch(err => toast(err.message)));
      // the editor's Delete Draft lands here with the article to delete
      let handoff = null;
      try { handoff = JSON.parse(sessionStorage.getItem(HANDOFF_KEY) || "null"); sessionStorage.removeItem(HANDOFF_KEY); } catch (e) { handoff = null; }
      if (handoff && handoff.id) deleteLater(body, handoff.id, handoff.title);
    }).catch(err => { body.innerHTML = failed("the articles", err); });
  }

  function backupLine(b) {
    let text;
    if (!b.last_run) text = "Backups: none yet. The first runs ten minutes after the server starts, then daily.";
    else if (!b.ok) text = "Backups: the last attempt (" + fmtStamp(b.last_run) + ") failed. " + (b.error || "");
    else text = "Backups: last run " + fmtStamp(b.last_run) + ", " + (b.offsite && b.offsite.configured
      ? "copied to the object store." : "kept on the server only; the object store is not configured.");
    return `<p class="wd-backup${b.last_run && !b.ok ? " err" : ""}">${esc(text)} <button type="button" class="linkbtn" id="wd-backup">Back Up Now</button></p>`;
  }

  /* Delete is one click with a few seconds to undo it: the row goes at once,
     and the draft is deleted when the Undo bar times out or the page is left. */
  function deleteLater(body, id, title) {
    if (PENDING) finishDelete(body);
    const li = $(`li[data-id="${id}"]`, body);
    if (li) li.hidden = true;
    PENDING = { id: id, title: title || "Untitled" };
    PENDING.timer = setTimeout(() => finishDelete(body), UNDO_MS);
    const box = $("#wd-alert", body);
    box.innerHTML = `<div class="undo-bar" role="status">Deleted “${esc(PENDING.title)}”. <button type="button" class="btn sm" id="wd-undo">Undo</button></div>`;
    $("#wd-undo", box).addEventListener("click", () => {
      const p = PENDING;
      if (!p) return;
      clearTimeout(p.timer);
      PENDING = null;
      const row = $(`li[data-id="${p.id}"]`, body);
      if (row) row.hidden = false;
      box.innerHTML = "";
    });
  }

  function finishDelete(body) {
    const p = PENDING;
    if (!p) return;
    clearTimeout(p.timer);
    PENDING = null;
    const box = $("#wd-alert", body);
    api("/research/articles/" + p.id, { method: "DELETE" }).then(() => {
      const row = $(`li[data-id="${p.id}"]`, body);
      if (row) row.parentNode.removeChild(row);
      if (box && $(".undo-bar", box)) box.innerHTML = "";
    }).catch(err => {
      const row = $(`li[data-id="${p.id}"]`, body);
      if (row) row.hidden = false;
      if (box) box.innerHTML = `<div class="notice wd-bad" role="alert">Could not delete “${esc(p.title)}”: ${esc(err.message)}</div>`;
    });
  }

  window.addEventListener("pagehide", () => {
    const p = PENDING;
    if (!p) return;
    clearTimeout(p.timer);
    PENDING = null;
    api("/research/articles/" + p.id, { method: "DELETE", keepalive: true }).catch(() => {});
  });

  /* ---- members ---- */

  /* Invitations are emailed when the server has a mailbox; otherwise, or
     when an email fails, the link comes back for the owner to hand over. */
  const mailOn = () => !!(ME && ME.sign_in && ME.sign_in.email_delivery);
  const handOver = () => mailOn()
    ? "The email could not be sent, so send them this sign-in link yourself, by email or chat."
    : "Send them this sign-in link yourself, by email or chat.";

  function linkBox(intro, link) {
    return `<div class="notice wd-linkbox" role="status"><p>${intro}</p>
      <div class="wd-copyrow"><input type="text" readonly value="${esc(link)}" aria-label="Sign-in link">
      <button type="button" class="btn sm" data-copy>Copy</button></div></div>`;
  }

  function wireCopy(el) {
    $$("[data-copy]", el).forEach(b => b.addEventListener("click", () => {
      const input = b.previousElementSibling;
      input.select();
      const done = () => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 2000); };
      if (navigator.clipboard) navigator.clipboard.writeText(input.value).then(done, () => document.execCommand("copy") && done());
      else if (document.execCommand("copy")) done();
    }));
  }

  function memberRow(m, manage, pub) {
    const fixed = m.source === "team";
    const role = manage && !fixed
      ? `<select data-role="${esc(m.email)}" aria-label="Role for ${esc(m.name)}">${Object.keys(ROLE).map(k =>
          `<option value="${k}"${k === m.role ? " selected" : ""}>${ROLE[k]}</option>`).join("")}</select>`
      : `<span class="tag">${esc(ROLE[m.role])}</span>`;
    const seen = m.last_login_at ? "Last signed in " + when(m.last_login_at) : "Has not signed in yet";
    return `<li><span class="av">${esc(initials(m.name))}</span>
      <div><b>${esc(m.name)}</b><small>${esc(m.email)} · ${esc(seen)}${fixed ? " · Writing permission on the admin Team page" : ""}</small></div>
      <span class="wd-acts">${role}` +
      (manage && !fixed ? `<button class="btn sm" type="button" data-link="${esc(m.email)}">${mailOn() ? "Email Sign-In Link" : "New Sign-In Link"}</button>
        <button class="btn sm danger" type="button" data-remove="${esc(m.email)}">Remove</button>` : "") +
      "</span></li>";
  }

  /* ``flash`` is HTML shown under the list once it has loaded: the sign-in
     link after an invitation must never be lost to the re-render. */
  function membersTab(pub, body, flash) {
    body.innerHTML = loading("the members");
    api(`/write/publications/${pub.id}/members`).then(data => {
      const manage = data.manage;
      const roleHelp = `<dl class="wd-roles">${Object.keys(ROLE).map(k => `<dt>${ROLE[k]}</dt><dd>${esc(ROLE_NOTE[k])}</dd>`).join("")}</dl>`;
      body.innerHTML = band("Members", data.members.length.toLocaleString()) +
        `<ul class="rows wd-list">${data.members.map(m => memberRow(m, manage, pub)).join("")}</ul><div id="wd-mout"></div>` +
        (manage ? band("Invite Someone") +
          `<form id="wd-invite"><dl class="kv wd-kv">
            <dt><label for="wd-iname">Name</label></dt><dd><input id="wd-iname" required maxlength="80" autocomplete="off"></dd>
            <dt><label for="wd-iemail">Email</label></dt><dd><input id="wd-iemail" type="email" required maxlength="254" autocomplete="off"></dd>
            <dt><label for="wd-irole">Role</label></dt><dd><select id="wd-irole">${Object.keys(ROLE).map(k => `<option value="${k}"${k === "writer" ? " selected" : ""}>${ROLE[k]}</option>`).join("")}</select></dd>
            <dt></dt><dd><button class="btn primary" type="submit">${mailOn() ? "Email Invitation" : "Invite"}</button></dd></dl></form>
            <div id="wd-iout"></div>` : "") + band("Roles") + roleHelp;
      const out = $("#wd-mout");
      if (flash) {
        out.innerHTML = flash;
        wireCopy(out);
        out.scrollIntoView({ block: "nearest" });
      }
      $$("[data-role]", body).forEach(sel => sel.addEventListener("change", () => {
        sel.disabled = true;
        send("PUT", `/write/publications/${pub.id}/members`, { email: sel.dataset.role, role: sel.value })
          .then(() => { toast("Role changed."); refreshMe().then(() => membersTab(pub, body)); })
          .catch(err => { toast(err.message); membersTab(pub, body); });
      }));
      $$("[data-link]", body).forEach(b => b.addEventListener("click", () => {
        busy(b, mailOn() ? "Sending…" : "Making…", () => send("POST", `/write/publications/${pub.id}/members/link`, { email: b.dataset.link }))
          .then(res => {
            out.innerHTML = res.emailed
              ? `<div class="notice" role="status">A new sign-in link is on its way to <b>${esc(res.email)}</b>. It works once, for 72 hours, and cancels their earlier link.</div>`
              : linkBox(`A new sign-in link for <b>${esc(res.email)}</b>. ${handOver()} It works once, for 72 hours, and cancels their earlier link.`, res.link);
            wireCopy(out);
          }).catch(err => toast(err.message));
      }));
      $$("[data-remove]", body).forEach(b => b.addEventListener("click", () => {
        // two clicks: removing someone ends their access to every draft here
        if (!b.classList.contains("armed")) { b.classList.add("armed"); b.textContent = "Click Again to Remove"; return; }
        busy(b, "Removing…", () => api(`/write/publications/${pub.id}/members?email=` + encodeURIComponent(b.dataset.remove), { method: "DELETE" }))
          .then(() => { toast("Removed. Their articles stay in the publication."); refreshMe().then(() => ME.publications.some(p => p.id === pub.id) ? membersTab(pub, body) : start()); })
          .catch(err => { b.classList.remove("armed"); toast(err.message); });
      }));
      const form = $("#wd-invite");
      if (form) form.addEventListener("submit", e => {
        e.preventDefault();
        const name = $("#wd-iname").value.trim(), email = $("#wd-iemail").value.trim();
        busy($("button", form), "Inviting…", () => send("POST", `/write/publications/${pub.id}/members`,
          { name: name, email: email, role: $("#wd-irole").value })).then(res => {
          const now = `<b>${esc(res.member.name)}</b> is now ${res.member.role === "writer" ? "a" : "an"} ${esc(ROLE[res.member.role].toLowerCase())} here.`;
          membersTab(pub, body, res.emailed
            ? `<div class="notice" role="status">${now} We emailed an invitation to <b>${esc(res.member.email)}</b>. Its link works once, for 72 hours, and opens this Writer Desk. After that they sign in at ${esc(location.host)}/write.html with their email.</div>`
            : res.link
            ? linkBox(`${now} ${handOver()} It works once, for 72 hours, and opens this Writer Desk.`, res.link)
            : `<div class="notice">${esc(res.member.name)} is now a member, but email sign-in is switched off on this server, so no link could be made.</div>`);
        }).catch(err => { $("#wd-iout").innerHTML = `<p class="err" role="alert">${esc(err.message)}</p>`; });
      });
    }).catch(err => { body.innerHTML = failed("the members", err); });
  }

  /* ---- assistant settings ----
     The publication's own skills, house style and connectors: what the AI tab
     in the editor uses on its articles (app/writer_api.py). Every member sees
     them; editors change skills and the house style; owners the connectors. */

  const AUTH = { none: "No key", bearer: "Bearer key", header: "Key in a header" };
  let REMOVE = null;                            // {path, label, row, box, list, note, timer}

  function assistantTab(pub, body, reopen) {
    flushRemove();
    body.innerHTML = loading("the assistant settings");
    const base = `/write/publications/${pub.id}/assistant`;
    api(base).then(d => {
      const who = d.can_manage ? "" : d.can_edit ? " Connectors are changed by the publication's owners."
        : " Only the publication's owners and editors change these.";
      body.innerHTML =
        `<p class="wd-para">What the assistant in the editor uses on this publication's articles: the skills a writer can pick, ` +
        `the house style sent with every run, and the outside tools it may call. Each person connects their own AI account in the editor's AI tab.${esc(who)}</p>` +
        band("Skills", d.skills.length.toLocaleString()) + '<div id="wd-skalert"></div>' +
        (d.skills.length ? `<ul class="rows wd-list" id="wd-skills">${d.skills.map(k => skillRow(k, d.can_edit)).join("")}</ul>`
          : '<p class="empty wd-empty">No skills. Add one to give writers a playbook they can pick in the AI tab.</p>') +
        (d.can_edit ? '<div class="wd-btnrow"><button class="btn" type="button" id="wd-skadd">Add Skill</button></div><div class="wd-edit wd-new" id="wd-sknew" hidden></div>' : "") +
        band("House Style") +
        '<p class="wd-para">Sent with every run: how anything the assistant writes into a draft should read. A new publication starts with Plover\'s institutional style; rewrite it in your own voice.</p>' +
        `<textarea id="wd-style" class="wd-style" rows="14" maxlength="8000" aria-label="House style"${d.can_edit ? "" : " readonly"}>${esc(d.house_style)}</textarea>` +
        (d.can_edit ? '<div class="wd-btnrow"><button class="btn primary" type="button" id="wd-stsave">Save House Style</button>' +
          '<button class="btn" type="button" id="wd-streset">Reset to Default</button><span class="hint" id="wd-stmsg" role="status"></span></div>' : "") +
        band("Connectors", d.connectors.length.toLocaleString()) + '<div id="wd-cnalert"></div>' +
        '<p class="wd-para">Outside tool servers (MCP) the assistant may call: a research database, a notes service, an internal tool. ' +
        "They use this publication's keys and serve its articles only. Each call is listed in the AI tab's steps.</p>" +
        (d.connectors.length ? `<ul class="rows wd-list" id="wd-conns">${d.connectors.map(c => connRow(c, d.can_manage)).join("")}</ul>`
          : '<p class="empty wd-empty">No connectors.</p>') +
        (d.can_manage ? '<div class="wd-btnrow"><button class="btn" type="button" id="wd-cnadd">Add Connector</button></div><div class="wd-edit wd-new" id="wd-cnnew" hidden></div>' : "");
      wireAssistant(pub, body, d, base);
      if (reopen) {
        const c = d.connectors.find(x => x.id === reopen);
        const box = $("#wd-conn-" + reopen);
        if (c && box) openConn(pub, body, d, base, c, box);
      }
    }).catch(err => { body.innerHTML = failed("the assistant settings", err); });
  }

  function skillRow(k, edit) {
    const tags = [];
    if (!k.writes) tags.push('<span class="tag">Answers Only</span>');
    if (!k.enabled) tags.push('<span class="tag">Off</span>');
    return `<li data-skill="${k.id}"><div><b>${esc(k.name)}</b><small>${esc(k.description)}</small></div>` +
      (tags.length ? `<span class="wd-tags">${tags.join("")}</span>` : "") +
      `<span class="wd-acts"><button class="btn sm" type="button" data-skopen="${k.id}" aria-expanded="false">${edit ? "Edit" : "Steps"}</button>` +
      (edit ? `<button class="btn sm danger" type="button" data-skdel="${k.id}">Delete</button>` : "") +
      `</span><div class="wd-edit" id="wd-skill-${k.id}" hidden></div></li>`;
  }

  function skillForm(k) {
    k = k || { name: "", description: "", instructions: "", enabled: true, writes: true };
    const id = k.id || "new";
    return `<dl class="kv wd-kv">
      <dt><label for="wd-skn-${id}">Name</label></dt><dd><input id="wd-skn-${id}" data-f="name" maxlength="60" autocomplete="off" value="${esc(k.name)}"></dd>
      <dt><label for="wd-skd-${id}">When to Use It</label></dt><dd><input id="wd-skd-${id}" data-f="description" maxlength="300" autocomplete="off" value="${esc(k.description)}"><span class="hint">One line. The assistant reads it to decide when the skill applies.</span></dd>
      <dt><label for="wd-ski-${id}">Steps</label></dt><dd><textarea id="wd-ski-${id}" class="wd-steps" data-f="instructions" rows="16" maxlength="20000">${esc(k.instructions)}</textarea>
        <span class="hint">Plain Markdown: what to do, in order, and what never to do. To end with a button for the next step, tell it to call <code>offer_next_step</code> with a label, a skill and an instruction.</span></dd>
      <dt>Options</dt><dd><label class="wd-check"><input type="checkbox" data-f="enabled"${k.enabled ? " checked" : ""}> Offered in the AI tab</label>
        <label class="wd-check"><input type="checkbox" data-f="writes"${k.writes ? " checked" : ""}> Can change the draft</label>
        <span class="hint">Untick “Can change the draft” for a skill that only answers, such as Brainstorm or Review: the writer keeps editing while it runs.</span></dd>
      <dt></dt><dd><button class="btn primary" type="button" data-act="save">${k.id ? "Save Skill" : "Add Skill"}</button>
        <button class="btn" type="button" data-act="cancel">Cancel</button></dd></dl><p class="err" role="alert" data-msg></p>`;
  }

  function values(box) {
    const out = {};
    $$("[data-f]", box).forEach(el => { out[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value; });
    return out;
  }

  function closeBox(box) {
    box.hidden = true;
    box.innerHTML = "";
    const li = box.closest("li");
    const btn = li && $("[aria-expanded]", li);
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function openSkill(pub, body, d, base, k, box) {
    if (!d.can_edit) {
      box.innerHTML = `<pre class="wd-pre">${esc(k.instructions)}</pre>`;
      box.hidden = false;
      return;
    }
    box.innerHTML = skillForm(k);
    box.hidden = false;
    const msg = $("[data-msg]", box);
    $('[data-act="cancel"]', box).addEventListener("click", () => closeBox(box));
    $('[data-act="save"]', box).addEventListener("click", e => {
      busy(e.target, "Saving…", () => send(k ? "PUT" : "POST", base + "/skills" + (k ? "/" + k.id : ""), values(box)))
        .then(() => { toast(k ? "Saved. The next run uses it." : "Added. Writers can pick it in the AI tab."); assistantTab(pub, body); })
        .catch(err => { msg.textContent = err.message; });
    });
    const first = $("input", box);
    if (first) first.focus();
  }

  function connRow(c, manage) {
    const on = c.tools.filter(t => t.on).length;
    const state = c.last_error ? '<span class="tag warn">Not Reachable</span>'
      : c.checked_at ? `<span class="tag">${on.toLocaleString()} of ${c.tools.length.toLocaleString()} Tools On</span>`
        : '<span class="tag">Not Checked</span>';
    return `<li data-conn="${c.id}"><div><b>${esc(c.label)}</b><small class="mono">${esc(c.url)}</small></div>
      <span class="wd-tags">${c.enabled ? "" : '<span class="tag">Off</span>'}${state}</span>
      <span class="wd-acts"><button class="btn sm" type="button" data-cnopen="${c.id}" aria-expanded="false">${manage ? "Manage" : "Tools"}</button>` +
      (manage ? `<button class="btn sm danger" type="button" data-cndel="${c.id}">Remove</button>` : "") +
      `</span><div class="wd-edit" id="wd-conn-${c.id}" hidden></div></li>`;
  }

  function toolList(c, manage) {
    if (!c.tools.length) return `<p class="wd-para">${esc(c.last_error || "No tools listed yet. Press Check Now.")}</p>`;
    return (c.last_error ? `<p class="err" role="alert">Last check: ${esc(c.last_error)}</p>` : "") +
      `<p class="wd-steplabel">Tools the assistant may call${c.server_name ? " (" + esc(c.server_name) + ")" : ""}</p>` +
      `<ul class="wd-tools">${c.tools.map(t => `<li><label class="wd-check"><input type="checkbox" data-tool="${esc(t.name)}"${t.on ? " checked" : ""}${manage ? "" : " disabled"}>` +
        ` <span class="mono">${esc(t.name)}</span></label> <span class="wd-tooldesc">${esc((t.description || "").slice(0, 160))}</span></li>`).join("")}</ul>`;
  }

  function connForm(c) {
    c = c || { label: "", url: "", auth_kind: "none", header_name: "", enabled: true };
    const id = c.id || "new";
    return `<dl class="kv wd-kv">
      <dt><label for="wd-cnl-${id}">Name</label></dt><dd><input id="wd-cnl-${id}" data-f="label" maxlength="40" autocomplete="off" value="${esc(c.label)}"></dd>
      <dt><label for="wd-cnu-${id}">Server Address</label></dt><dd><input id="wd-cnu-${id}" data-f="url" autocomplete="off" placeholder="https://…/mcp" value="${esc(c.url)}"></dd>
      <dt><label for="wd-cna-${id}">Sign-in</label></dt><dd><select id="wd-cna-${id}" data-f="auth_kind">${Object.keys(AUTH).map(k =>
        `<option value="${k}"${k === c.auth_kind ? " selected" : ""}>${AUTH[k]}</option>`).join("")}</select></dd>
      <dt data-hdr${c.auth_kind === "header" ? "" : " hidden"}><label for="wd-cnh-${id}">Header Name</label></dt><dd data-hdr${c.auth_kind === "header" ? "" : " hidden"}><input id="wd-cnh-${id}" data-f="header_name" autocomplete="off" placeholder="X-API-Key" value="${esc(c.header_name || "")}"></dd>
      <dt data-key${c.auth_kind === "none" ? " hidden" : ""}><label for="wd-cnk-${id}">Key</label></dt><dd data-key${c.auth_kind === "none" ? " hidden" : ""}><input id="wd-cnk-${id}" type="password" data-f="key" autocomplete="off" placeholder="${c.has_key ? "Stored key ending " + esc(c.key_last4 || "") + ". Paste to replace" : "Paste the key"}"><span class="hint">Stored encrypted and never shown again.</span></dd>` +
      (c.id ? `<dt>Status</dt><dd><label class="wd-check"><input type="checkbox" data-f="enabled"${c.enabled ? " checked" : ""}> Available to the assistant</label></dd>` : "") +
      `<dt></dt><dd><button class="btn primary" type="button" data-act="save">${c.id ? "Save" : "Add and Check"}</button>` +
      (c.id ? '<button class="btn" type="button" data-act="check">Check Now</button>' : "") +
      '<button class="btn" type="button" data-act="cancel">Cancel</button></dd></dl><p class="err" role="alert" data-msg></p>';
  }

  function openConn(pub, body, d, base, c, box) {
    const manage = d.can_manage;
    box.innerHTML = (c ? toolList(c, manage) : "") + (manage ? connForm(c) : "");
    box.hidden = false;
    const li = box.closest("li");
    if (li) $("[aria-expanded]", li).setAttribute("aria-expanded", "true");
    const msg = $("[data-msg]", box);
    const fail = err => { if (msg) msg.textContent = err.message; else toast(err.message); };
    $$("[data-tool]", box).forEach(cb => cb.addEventListener("change", () => {
      cb.disabled = true;
      send("PUT", `${base}/connectors/${c.id}/tools`, { tool: cb.dataset.tool, on: cb.checked })
        .then(() => { cb.disabled = false; })
        .catch(err => { cb.checked = !cb.checked; cb.disabled = false; fail(err); });
    }));
    if (!manage) return;
    const kind = $('[data-f="auth_kind"]', box);
    kind.addEventListener("change", () => {
      $$("[data-hdr]", box).forEach(el => { el.hidden = kind.value !== "header"; });
      $$("[data-key]", box).forEach(el => { el.hidden = kind.value === "none"; });
    });
    $('[data-act="cancel"]', box).addEventListener("click", () => closeBox(box));
    $('[data-act="save"]', box).addEventListener("click", e => {
      if (msg) msg.textContent = "";
      busy(e.target, c ? "Saving…" : "Connecting…", () => send(c ? "PUT" : "POST", base + "/connectors" + (c ? "/" + c.id : ""), values(box)))
        .then(res => { toast(res.last_error ? "Saved, but the server could not be reached." : "Saved."); assistantTab(pub, body, res.id); })
        .catch(fail);
    });
    const chk = $('[data-act="check"]', box);
    if (chk) chk.addEventListener("click", () => {
      busy(chk, "Connecting…", () => send("POST", `${base}/connectors/${c.id}/check`))
        .then(res => { toast(res.last_error ? "The server could not be reached." : "Checked: the tools are up to date."); assistantTab(pub, body, c.id); })
        .catch(fail);
    });
    if (!c) $("input", box).focus();
  }

  /* Delete and Remove are one click with a few seconds to undo: the row goes
     at once, and the server is asked when the Undo bar times out or the page
     is left (the same pattern as deleting a draft). */
  function removeLater(path, label, row, box) {
    flushRemove();
    row.hidden = true;
    const head = box.previousElementSibling;  // the section's band, whose count goes down
    REMOVE = { path: path, label: label, row: row, box: box, list: row.parentNode,
      note: head && $(".h2-note", head) };
    REMOVE.timer = setTimeout(finishRemove, UNDO_MS);
    box.innerHTML = `<div class="undo-bar" role="status">Removed “${esc(label)}”. <button type="button" class="btn sm" data-undo>Undo</button></div>`;
    $("[data-undo]", box).addEventListener("click", () => {
      const p = REMOVE;
      if (!p) return;
      clearTimeout(p.timer);
      REMOVE = null;
      p.row.hidden = false;
      p.box.innerHTML = "";
    });
  }

  function finishRemove() {
    const p = REMOVE;
    if (!p) return;
    clearTimeout(p.timer);
    REMOVE = null;
    api(p.path, { method: "DELETE" }).then(() => {
      if (p.row.parentNode) p.row.parentNode.removeChild(p.row);
      if ($(".undo-bar", p.box)) p.box.innerHTML = "";
      if (p.note) p.note.textContent = p.list.children.length.toLocaleString();
    }).catch(err => {
      p.row.hidden = false;
      p.box.innerHTML = `<div class="notice wd-bad" role="alert">Could not remove “${esc(p.label)}”: ${esc(err.message)}</div>`;
    });
  }

  function flushRemove() { if (REMOVE) finishRemove(); }

  window.addEventListener("pagehide", () => {
    const p = REMOVE;
    if (!p) return;
    clearTimeout(p.timer);
    REMOVE = null;
    api(p.path, { method: "DELETE", keepalive: true }).catch(() => {});
  });

  function wireAssistant(pub, body, d, base) {
    const toggle = (btn, box, open) => {
      if (!box.hidden) { closeBox(box); return; }
      btn.setAttribute("aria-expanded", "true");
      open(box);
    };
    $$("[data-skopen]", body).forEach(b => b.addEventListener("click", () => {
      const k = d.skills.find(x => x.id === Number(b.dataset.skopen));
      toggle(b, $("#wd-skill-" + k.id), box => openSkill(pub, body, d, base, k, box));
    }));
    $$("[data-skdel]", body).forEach(b => b.addEventListener("click", () => {
      const k = d.skills.find(x => x.id === Number(b.dataset.skdel));
      removeLater(`${base}/skills/${k.id}`, k.name, b.closest("li"), $("#wd-skalert"));
    }));
    const skadd = $("#wd-skadd");
    if (skadd) skadd.addEventListener("click", () => {
      const box = $("#wd-sknew");
      if (!box.hidden) { closeBox(box); return; }
      openSkill(pub, body, d, base, null, box);
    });
    const style = $("#wd-style"), stmsg = $("#wd-stmsg");
    const stsave = $("#wd-stsave");
    if (stsave) stsave.addEventListener("click", () => {
      stmsg.className = "hint";
      busy(stsave, "Saving…", () => send("PUT", base + "/house-style", { text: style.value })).then(res => {
        d.house_style = style.value = res.house_style;
        stmsg.textContent = "Saved. The next run uses it.";
      }).catch(err => { stmsg.className = "hint err"; stmsg.textContent = err.message; });
    });
    const streset = $("#wd-streset");
    if (streset) streset.addEventListener("click", () => {
      style.value = d.default_house_style;
      stmsg.className = "hint";
      stmsg.textContent = "Default restored here. Save to use it.";
    });
    $$("[data-cnopen]", body).forEach(b => b.addEventListener("click", () => {
      const c = d.connectors.find(x => x.id === Number(b.dataset.cnopen));
      toggle(b, $("#wd-conn-" + c.id), box => openConn(pub, body, d, base, c, box));
    }));
    $$("[data-cndel]", body).forEach(b => b.addEventListener("click", () => {
      const c = d.connectors.find(x => x.id === Number(b.dataset.cndel));
      removeLater(`${base}/connectors/${c.id}`, c.label, b.closest("li"), $("#wd-cnalert"));
    }));
    const cnadd = $("#wd-cnadd");
    if (cnadd) cnadd.addEventListener("click", () => {
      const box = $("#wd-cnnew");
      if (!box.hidden) { closeBox(box); return; }
      openConn(pub, body, d, base, null, box);
    });
  }

  /* ---- settings ---- */

  function settingsTab(pub, body) {
    body.innerHTML = band("Publication Settings") +
      `<form id="wd-set"><dl class="kv wd-kv">
        <dt><label for="wd-sname">Name</label></dt><dd><input id="wd-sname" required maxlength="80" value="${esc(pub.name)}"></dd>
        <dt><label for="wd-stag">Tagline</label></dt><dd><input id="wd-stag" maxlength="200" value="${esc(pub.tagline)}"><span class="hint">One line under the name on the front page.</span></dd>
        <dt><label for="wd-sabout">About</label></dt><dd><textarea id="wd-sabout" rows="5" maxlength="2000">${esc(pub.about)}</textarea></dd>
        <dt>Address</dt><dd><a href="${esc(pub.base)}" target="_blank" rel="noopener">${esc(pub.url)}</a><span class="hint">Fixed: it is part of every citation of every article.</span></dd>
        <dt></dt><dd><button class="btn primary" type="submit">Save Settings</button></dd></dl></form>`;
    $("#wd-set").addEventListener("submit", e => {
      e.preventDefault();
      busy($("button", e.target), "Saving…", () => send("PUT", "/write/publications/" + pub.id,
        { name: $("#wd-sname").value, tagline: $("#wd-stag").value, about: $("#wd-sabout").value }))
        .then(() => refreshMe()).then(() => { toast("Saved. The public pages show it now."); render(); })
        .catch(err => toast(err.message));
    });
  }

  /* ------------------------------------------------------------ account */

  function keySteps(kind, endpoint, token) {
    const row = (label, text) => `<p class="wd-steplabel">${esc(label)}</p><div class="wd-copyrow">` +
      (text.indexOf("\n") !== -1 ? `<textarea readonly rows="${text.split("\n").length}" aria-label="${esc(label)}">${esc(text)}</textarea>`
        : `<input type="text" readonly value="${esc(text)}" aria-label="${esc(label)}">`) +
      '<button type="button" class="btn sm" data-copy>Copy</button></div>';
    let steps;
    if (kind === "Claude Code") {
      steps = row("Run once in a terminal", `claude mcp add --scope user --transport http plover-research ${endpoint} --header "Authorization: Bearer ${token}"`) +
        "<p>Then start <code>claude</code> and type <code>/mcp</code>: plover-research should show as connected.</p>";
    } else if (kind === "Codex") {
      steps = row("Add to ~/.codex/config.toml", `[mcp_servers.plover-research]\nurl = "${endpoint}"\nhttp_headers = { "Authorization" = "Bearer ${token}" }`) +
        "<p>Restart Codex, then ask it to list your articles.</p>";
    } else {
      steps = row("Server address", endpoint) + row("Header", "Authorization: Bearer " + token);
    }
    return `<div class="notice wd-linkbox" role="status"><p><b>Key created.</b> Copy it now: it is shown once. Anyone holding it can edit your drafts, so keep it out of shared files.</p>${steps}</div>`;
  }

  function accountView(r) {
    shell(r, topbar([["My Account"]]) + page(pageHead("My Account", `${esc(ME.person.name)} · ${esc(ME.person.email)}`,
      '<button class="btn" id="wd-signout" type="button">Sign Out</button>') +
      band("AI Connections") +
      '<p class="wd-para">Connect your own Claude Code or Codex. It can read your articles, start and edit drafts and insert Plover charts, as you, in the publications you write for. It cannot publish.</p>' +
      '<div id="wd-keys">' + loading("your keys") + "</div>" +
      `<div class="wd-keynew"><select id="wd-kind" aria-label="Client"><option>Claude Code</option><option>Codex</option><option>Other MCP client</option></select>
        <button class="btn primary" id="wd-keymake" type="button">Create Key</button></div><div id="wd-keyout"></div>`));
    $("#wd-signout").addEventListener("click", signOut);
    const loadKeys = () => api("/write/keys").then(res => {
      $("#wd-keys").innerHTML = res.keys.length
        ? `<ul class="rows wd-list">${res.keys.map(k => `<li><div><b>${esc(k.label)}</b><small>Created ${when(k.created_at)} · ${k.last_used_at ? "last used " + when(k.last_used_at) : "not used yet"}</small></div>
            <span class="wd-acts"><button class="btn sm" type="button" data-revoke="${k.id}">Revoke</button></span></li>`).join("")}</ul>`
        : '<p class="empty wd-empty">No keys yet.</p>';
      $$("[data-revoke]").forEach(b => b.addEventListener("click", () =>
        busy(b, "Revoking…", () => api("/write/keys/" + b.dataset.revoke, { method: "DELETE" })).then(loadKeys).catch(err => toast(err.message))));
    }).catch(err => { $("#wd-keys").innerHTML = failed("your keys", err); });
    loadKeys();
    $("#wd-keymake").addEventListener("click", e => {
      const kind = $("#wd-kind").value;
      busy(e.target, "Creating…", () => send("POST", "/write/keys", { label: kind })).then(k => {
        const out = $("#wd-keyout");
        out.innerHTML = keySteps(kind, k.endpoint, k.token);
        wireCopy(out);
        loadKeys();
      }).catch(err => toast(err.message));
    });
  }

  /* ------------------------------------------------------------ open a publication */

  const slugify = t => (t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");

  function newView(r) {
    if (!ME.can_create_publications) { go(""); return; }
    const forOthers = ME.opens_for_others;
    shell(r, topbar([["Open a Publication"]]) + page(pageHead("Open a Publication",
      "A publication has its own pages at /p/&lt;address&gt;, its own feed, its own members and its own Assistant Settings. " +
      (forOthers ? "Its owner runs it from this desk." : "You will own it and run it from this desk.")) +
      band("Publication") +
      `<form id="wd-open"><dl class="kv wd-kv">
        <dt><label for="wd-oname">Name</label></dt><dd><input id="wd-oname" required maxlength="80" autocomplete="off"></dd>
        <dt><label for="wd-oslug">Address</label></dt><dd><span class="wd-prefix">/p/</span><input id="wd-oslug" required maxlength="40" pattern="[a-z0-9]+(-[a-z0-9]+)*" autocomplete="off"><span class="hint">Lower-case letters, digits and hyphens. Fixed once opened.</span></dd>
        <dt><label for="wd-otag">Tagline</label></dt><dd><input id="wd-otag" maxlength="200" autocomplete="off"></dd>
        ${forOthers ? `<dt><label for="wd-oown">Owner's name</label></dt><dd><input id="wd-oown" maxlength="80" autocomplete="off"></dd>
        <dt><label for="wd-oemail">Owner's email</label></dt><dd><input id="wd-oemail" type="email" required maxlength="254" autocomplete="off"><span class="hint">Use your own address to own it yourself.</span></dd>` : ""}
        <dt></dt><dd><button class="btn primary" type="submit">Open Publication</button></dd></dl></form><div id="wd-oout"></div>`));
    let touched = false;
    $("#wd-oslug").addEventListener("input", () => { touched = true; });
    $("#wd-oname").addEventListener("input", e => { if (!touched) $("#wd-oslug").value = slugify(e.target.value); });
    $("#wd-open").addEventListener("submit", e => {
      e.preventDefault();
      busy($("button", e.target), "Opening…", () => send("POST", "/write/publications", {
        name: $("#wd-oname").value, slug: $("#wd-oslug").value, tagline: $("#wd-otag").value,
        owner_name: forOthers ? $("#wd-oown").value : "", owner_email: forOthers ? $("#wd-oemail").value : "",
      })).then(res => refreshMe().then(() => {
        const pub = res.publication;
        if (!forOthers) { toast(pub.name + " is open. It is yours to run."); go("p/" + pub.id); return; }
        const out = $("#wd-oout");
        out.innerHTML = (res.emailed
          ? `<div class="notice" role="status"><b>${esc(pub.name)}</b> is open at <a href="${esc(pub.base)}" target="_blank" rel="noopener">${esc(pub.base)}</a>. We emailed its owner, ${esc(res.owner_email)}, a sign-in link. It works once, for 72 hours.</div>`
          : res.link
          ? linkBox(`<b>${esc(pub.name)}</b> is open at <a href="${esc(pub.base)}" target="_blank" rel="noopener">${esc(pub.base)}</a>. ${mailOn() ? "The email to its owner could not be sent, so send" : "Send"} ${esc(res.owner_email)} this sign-in link yourself. It works once, for 72 hours.`, res.link)
          : `<div class="notice" role="status"><b>${esc(pub.name)}</b> is open at <a href="${esc(pub.base)}" target="_blank" rel="noopener">${esc(pub.base)}</a>. ` +
            (pub.role ? `<a href="#/p/${pub.id}">Go to it</a>.` : "") + "</div>");
        wireCopy(out);
        $("#wd-open").reset();
        touched = false;
      })).catch(err => { $("#wd-oout").innerHTML = `<p class="err" role="alert">${esc(err.message)}</p>`; });
    });
  }

  /* ------------------------------------------------------------ start */

  function refreshMe() { return api("/write/me").then(me => { ME = me; return me; }); }

  function render() {
    if (!ME || !ME.signed_in) return signInScreen();
    if (!ME.writer) return notWriterScreen();
    const r = route();
    if (r.sec === "account") return accountView(r);
    if (r.sec === "new") return newView(r);
    pubView(r);
  }

  function start() {
    refreshMe().then(render).catch(err => {
      root.innerHTML = `<div class="state"><h1>Writer Desk</h1><p>The desk could not load: ${esc(err.message)}</p>` +
        '<button class="btn" type="button" data-reload>Try Again</button></div>';
    });
  }

  window.addEventListener("hashchange", () => { if (ME && ME.writer) render(); });
  document.addEventListener("click", e => { if (e.target.closest("[data-reload]")) location.reload(); });
  start();
})();
