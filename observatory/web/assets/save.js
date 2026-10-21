/* save.html: where the Plover browser extension and the Save to Plover
   bookmark land.

   The article's text arrives in the #fragment. It is read once and removed
   from the address straight away, so it does not stay in the browser's
   history, and it never reached the server in the first place. The page
   saves it to the signed-in person's reading and offers a summary, made on
   their own AI key (app/assistant/reading.py). */
"use strict";

(function () {
  var box = document.getElementById("sv-status");
  var API = "/api/v1/assistant/reading";
  var page = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function show(kind, html) {
    box.className = "sv-card sv-" + kind;
    box.innerHTML = html;
  }

  function words(p) {
    var n = 0;
    (p.blocks || []).forEach(function (b) { n += String(b.text || "").split(/\s+/).filter(Boolean).length; });
    return n;
  }

  function call(method, path, body) {
    return fetch(path, {
      method: method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        return { status: r.status, ok: r.ok, body: b };
      });
    });
  }

  function retry(label) {
    return '<p class="sv-acts"><button type="button" class="btn btn-primary sv-btn" id="sv-retry">' +
      esc(label || "Try Again") + "</button></p>";
  }
  function wireRetry() {
    var b = document.getElementById("sv-retry");
    if (b) b.addEventListener("click", save);
  }

  function bullets(text) {
    var lines = String(text || "").split("\n").map(function (l) { return l.replace(/^\s*-\s*/, "").trim(); })
      .filter(Boolean);
    return '<ul class="my-sum">' + lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>";
  }

  function settingsLink(detail) {
    return /Settings/.test(detail || "")
      ? ' <a href="assistant.html#/settings" target="_blank" rel="noopener">Open Settings</a>' : "";
  }

  function saved(item) {
    var html = '<p class="sv-ok-line"><strong>Saved to your reading.</strong></p>' +
      '<p class="sv-title">' + esc(item.title) + "</p>" +
      '<p class="sv-meta">' + esc(item.site) + " · " + words(page).toLocaleString("en-GB") + " words" +
      (item.selection ? ", the passage you selected" : "") + (page.cut ? ", the first part of a long article" : "") + "</p>" +
      '<div id="sv-sum"></div>' +
      '<p class="sv-acts">' +
      '<button type="button" class="btn btn-primary sv-btn" id="sv-summarise">Summarize It</button>' +
      '<a class="btn sv-btn" href="my.html#reading">Open Reading</a>' +
      '<button type="button" class="btn sv-btn" id="sv-close">Close</button></p>';
    show("ok", html);
    document.getElementById("sv-close").addEventListener("click", function () { window.close(); });
    var sumBtn = document.getElementById("sv-summarise");
    var sumBox = document.getElementById("sv-sum");
    sumBtn.addEventListener("click", function () {
      sumBtn.disabled = true;
      sumBtn.textContent = "Summarizing…";
      sumBox.innerHTML = '<p class="sv-meta">Reading the article. This takes a few seconds.</p>';
      call("POST", API + "/" + item.id + "/summary").then(function (r) {
        if (r.ok) {
          sumBox.innerHTML = '<p class="sv-label">Summary of this article</p>' + bullets(r.body.summary);
          sumBtn.remove();
          return;
        }
        var why = r.body.detail || "The summary could not be made (" + r.status + ").";
        sumBox.innerHTML = '<p class="sv-err">' + esc(why) + settingsLink(why) + "</p>";
        sumBtn.disabled = false;
        sumBtn.textContent = "Try Again";
      }).catch(function () {
        sumBox.innerHTML = '<p class="sv-err">Plover could not be reached. Check your connection, then try again.</p>';
        sumBtn.disabled = false;
        sumBtn.textContent = "Try Again";
      });
    });
  }

  function save() {
    show("busy", "<p>Saving <strong>" + esc(page.title || page.url) + "</strong> to Plover…</p>");
    call("POST", API, page).then(function (r) {
      if (r.status === 401) {
        show("bad", "<p>You are not signed in to Plover in this browser.</p>" +
          '<p><a href="signin.html?returnTo=my.html" target="_blank" rel="noopener">Sign in</a> in a new tab, ' +
          "then come back here and click Try Again. The article is kept on this page until then.</p>" + retry());
        wireRetry();
        return;
      }
      if (r.status === 404 && !r.body.id) {
        show("bad", "<p>Saving articles is not switched on for this Plover server.</p>");
        return;
      }
      if (!r.ok) {
        show("bad", "<p>" + esc(r.body.detail || "Plover could not save the article (" + r.status + ").") + "</p>" + retry());
        wireRetry();
        return;
      }
      saved(r.body);
    }).catch(function () {
      show("bad", "<p>Plover could not be reached. Check your connection.</p>" + retry());
      wireRetry();
    });
  }

  if (typeof initThemeToggle === "function") initThemeToggle();

  var raw = location.hash.slice(1);
  if (raw) history.replaceState(null, "", location.pathname);
  try { page = raw ? JSON.parse(decodeURIComponent(raw)) : null; } catch (e) { page = null; }
  if (page && page.error === "page") {
    show("bad", "<p>That page cannot be saved. Browser pages such as new tabs and settings have no article.</p>" +
      "<p>Open the article you want, then click the Plover button again.</p>");
    return;
  }
  if (!page || !page.url) {
    show("bad", "<p>Nothing arrived. This page opens when you click the Plover button or the Save to Plover bookmark on an article.</p>" +
      '<p>Both are kept under <a href="my.html#reading">Reading</a> on Plover.</p>');
    return;
  }
  if (!page.blocks || !page.blocks.length) {
    show("bad", "<p>No article text was found on that page.</p>" +
      "<p>Open the article itself, not a list of headlines. If the page still shows nothing, select the passage you want and click again.</p>");
    return;
  }
  save();
})();
