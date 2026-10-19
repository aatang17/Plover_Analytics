/* Research Desk, part two: charts copied from Plover pages, and the research
   panel (Plover data and the web).

   Copying a chart
     Every chart a Plover page draws goes through charts.js, which keeps a
     list of them with the config — data included — each is drawn from. The
     desk opens the page in an invisible frame (same site, light theme), waits
     for its charts to settle, and reads that list. The writer picks one; its
     kind and config are stored in the article and drawn by the same code, so
     it looks exactly as on the page, frozen on the day it was copied. A chart
     a page draws some other way (a map, the yield curve) comes across as an
     image. An agent working over MCP can only name the page; the desk copies
     those charts when a person opens the draft.

   Research panel
     Tabs, like a browser: Plan and Notes pinned, then one tab per search or
     page. Plover data: search series and companies, insert a chart, insert
     a latest value with a footnote saying where it came from, save it to
     Notes, or copy a chart from a company's pages. Web page: read a page by
     its address as text, cite it, quote from it, or save a passage to
     Notes. Notes are filed under the section being written and go into the
     draft with their footnotes (Insert, or drag). Searching the web is left
     to the writer's own Claude or Codex over /mcp/research.

   Loaded before desk.js; uses its helpers ($, S, api, send, insertBlock …)
   only inside functions, which run after both files have loaded. */
"use strict";

var SITE_HOSTS = ["ploveranalytics.com", "www.ploveranalytics.com"];

/* A Plover page address as "/page.html?query", or "" — the same rule the
   server applies (research_doc.page_path). */
function pagePath(url) {
  url = (url || "").trim();
  if (!url) return "";
  var m = /^https?:\/\/([^/]+)(\/.*)?$/i.exec(url);
  if (m) {
    var host = m[1].toLowerCase().split(":")[0];
    if (SITE_HOSTS.indexOf(host) === -1 && host !== "localhost" && host !== "127.0.0.1" &&
        host !== location.hostname) return "";
    url = m[2] || "/";
  }
  url = url.split("#")[0];
  if (url === "" || url === "/") url = "/index.html";
  return /^\/(?!admin|desk)[A-Za-z0-9_\-]+\.html(\?[^\s<>"#]*)?$/.test(url) ? url : "";
}

function chartIndexOf(url) {
  var m = /#chart-(\d+)$/.exec(url || "");
  return m ? Number(m[1]) : 0;
}

function todayIso() { return new Date().toISOString().slice(0, 10); }

function dateLong(iso) {
  if (!iso) return "";
  var d = new Date(iso.slice(0, 10) + "T00:00:00Z");
  return d.getUTCDate() + " " + d.toLocaleString("en-GB", { month: "long", timeZone: "UTC" }) +
    " " + d.getUTCFullYear();
}

/* ---------------------------------------------------------------- the block */

function snapshotHeight(model) {
  var c = model.cfg || {};
  var rows = (c.rows || c.items || []).length;
  if ((model.kind === "rank" || model.kind === "bar") && rows) {
    return Math.max(320, Math.min(1400, rows * 24 + 80));
  }
  return 320;
}

function renderSnapshotBlock(c, b) {
  var head = '<div class="dk-fig-label dk-chart-label"></div>' +
    '<input type="text" class="dk-chart-title" data-f="title" maxlength="200" placeholder="Chart title">';
  if (!b.url) {
    c.innerHTML = head +
      '<div class="dk-snap-ask"><label class="dk-flabel" for="snap-' + b.id + '">Plover page</label>' +
      '<div class="dk-snap-row"><input type="text" id="snap-' + b.id + '" data-snap="url" ' +
      'placeholder="Paste a page address, e.g. https://ploveranalytics.com/financials.html?c=7203">' +
      '<button type="button" class="dk-mini" data-snap="capture">Copy Chart</button></div>' +
      '<p class="dk-snap-status" hidden></p></div>';
  } else if (!b.cfg) {
    c.innerHTML = head +
      '<div class="dk-snap-ask"><p>Waiting to copy ' + (b.chart ? "chart " + b.chart : "a chart") +
      ' from <a href="' + escapeHtml(b.url) + '" target="_blank" rel="noopener">' +
      escapeHtml(b.url) + "</a>.</p>" +
      '<div class="dk-snap-row"><button type="button" class="dk-mini" data-snap="capture">Copy Now</button>' +
      '<button type="button" class="dk-mini" data-snap="clear">Use Another Page</button></div>' +
      '<p class="dk-snap-status" hidden></p></div>';
  } else {
    c.innerHTML = head +
      '<p class="dk-snap-meta">From <a href="' + escapeHtml(b.url) + '" target="_blank" rel="noopener">' +
      escapeHtml(b.page_title || b.url) + "</a> · copied " + escapeHtml(dateLong(b.captured_at)) +
      ' · <button type="button" class="linkish" data-snap="capture">Copy Again</button>' +
      ' · <button type="button" class="linkish" data-snap="choose">Choose Another Chart</button>' +
      ' · <button type="button" class="linkish" data-snap="save">Save Chart</button></p>' +
      '<div class="dk-chart-plot" style="height:' + snapshotHeight(b) + 'px"></div>' +
      '<p class="source-line">' + escapeHtml(b.source || "") + "</p>" +
      (b.calc ? '<details class="calc"><summary>Show calculation</summary><div class="calc-body">' +
        escapeHtml(b.calc) + "</div></details>" : "") +
      '<p class="dk-snap-status" hidden></p>' +
      '<div class="dk-fig-fields"><label class="wide"><span>Note under the chart <span class="muted">optional</span></span>' +
      '<input type="text" data-f="note" maxlength="400"></label></div>';
    var noteIn = $('[data-f="note"]', c);
    if (noteIn) noteIn.value = b.note || "";
    setTimeout(function () { drawSnapshotIn(c, b); }, 0);
  }
  $('[data-f="title"]', c).value = b.title || "";
}

function drawSnapshotIn(c, b) {
  var plot = $(".dk-chart-plot", c);
  if (!plot || !b.cfg) return;
  disposeChart(b.id);
  S.charts[b.id] = researchChart.drawSnapshot(plot, { kind: b.kind, cfg: b.cfg, title: b.title, source: b.source });
}

function snapStatus(blockEl, text, bad) {
  var st = $(".dk-snap-status", blockEl);
  if (!st) return;
  st.hidden = !text;
  st.className = "dk-snap-status" + (bad ? " bad" : "");
  st.textContent = text || "";
}

function snapClick(blockEl, model, t) {
  var act = t.getAttribute("data-snap");
  if (!act) return false;
  if (act === "capture" || act === "choose") {
    if (!model.url) {
      var input = $('[data-snap="url"]', blockEl);
      var path = pagePath(input ? input.value : "");
      if (!path) { snapStatus(blockEl, "Paste the address of a Plover page (ploveranalytics.com/…).", true); return true; }
      model.url = path;
      model.chart = chartIndexOf(input.value);
    }
    copyChart(blockEl, model, act === "choose");
    return true;
  }
  if (act === "save") { saveChart(model); return true; }
  if (act === "clear") {
    Object.assign(model, { url: "", chart: 0, cfg: null, kind: "" });
    renderSnapshotBlock(blockEl.querySelector(".dk-content"), model);
    updateCounts();
    changed();
    return true;
  }
  return false;
}

/* ---------------------------------------------------------------- capture */

var captureQueue = Promise.resolve();

/* One page at a time: a second capture waits for the first frame to close. */
function capturePage(path) {
  var job = captureQueue.then(function () { return captureNow(path); });
  captureQueue = job.catch(function () {});
  return job;
}

function captureNow(path) {
  return new Promise(function (resolve, reject) {
    var frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.tabIndex = -1;
    frame.className = "dk-capture-frame";
    frame.src = path + (path.indexOf("?") === -1 ? "?" : "&") + "theme=light";
    var started = Date.now();
    var last = -1;
    var stable = 0;
    var done = false;
    var timer = null;
    function finish(err) {
      if (done) return;
      done = true;
      clearInterval(timer);
      var out = null;
      if (!err) {
        try { out = collectCharts(frame); } catch (e) { err = new Error("The page's charts could not be read."); }
      }
      frame.parentNode && frame.parentNode.removeChild(frame);
      if (err) reject(err); else resolve(out);
    }
    timer = setInterval(function () {
      var w;
      try {
        w = frame.contentWindow;
        if (!w || !w.document || w.document.readyState === "loading") return;
        if (w.document.title === "" && Date.now() - started < 3000) return;
      } catch (e) { finish(new Error("That page could not be opened here.")); return; }
      try { w.scrollTo(0, w.document.body.scrollHeight); } catch (e) { /* ignore */ }
      var n = liveCharts(w).length + rawCharts(w, liveCharts(w)).length;
      if (n === last && n > 0) stable++; else { stable = 0; last = n; }
      if (stable >= 4) finish();
      else if (n === 0 && Date.now() - started > 12000) {
        // A page that has drawn nothing in 12 seconds shows no chart at this
        // address; waiting the full 25 only makes the writer wait to be told.
        finish(new Error("That page shows no chart at this address. If it charts one company " +
                         "at a time, open it, choose the company, and copy from that address."));
      } else if (Date.now() - started > 25000) {
        finish();
      }
    }, 500);
    document.body.appendChild(frame);
  });
}

function liveCharts(w) {
  var d = w.document;
  var byEl = new Map();
  (w.__obsCharts || []).forEach(function (x) {
    if (d.contains(x.el) && x.el.querySelector("canvas")) byEl.set(x.el, x);
  });
  return Array.from(byEl.values());
}

function rawCharts(w, live) {
  if (!w.echarts) return [];
  var taken = new Set(live.map(function (x) { return x.el; }));
  return Array.prototype.slice.call(w.document.querySelectorAll("[_echarts_instance_]")).filter(function (el) {
    return !taken.has(el) && el.offsetWidth >= 200 && w.echarts.getInstanceByDom(el);
  });
}

function cleanText(node) {
  var copy = node.cloneNode(true);
  Array.prototype.forEach.call(copy.querySelectorAll(".h2-note, .badge, button"), function (x) { x.remove(); });
  return (copy.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200);
}

function titleNear(el) {
  var sel = "h1, h2, h3, h4, .chart-title, .panel-title, figcaption, .card-title, .section-title";
  var n = el;
  for (var depth = 0; n && depth < 8; depth++) {
    var p = n.previousElementSibling;
    while (p) {
      if (p.matches(sel)) return cleanText(p);
      var q = p.querySelectorAll(sel);
      if (q.length) return cleanText(q[q.length - 1]);
      p = p.previousElementSibling;
    }
    n = n.parentElement;
  }
  return "";
}

function textNear(el, sel) {
  var n = el.parentElement;
  for (var depth = 0; n && depth < 6; depth++) {
    var hits = Array.prototype.slice.call(n.querySelectorAll(sel)).filter(function (h) {
      return el.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING;
    });
    if (hits.length) {
      // the page's own download links (JSON, Markdown, CSV) are not part of the source
      var copy = hits[0].cloneNode(true);
      Array.prototype.forEach.call(copy.querySelectorAll('a[href*="/api/"], a[href$=".md"], a[href*=".md?"], a[download], button'),
        function (a) { a.remove(); });
      return (copy.textContent || "").replace(/\s+/g, " ").replace(/(\s*\u00b7\s*)+\.?\s*$/, "")
        .replace(/(\s*\u00b7\s*){2,}/g, " \u00b7 ").trim().slice(0, 1000);
    }
    n = n.parentElement;
  }
  return "";
}

function collectCharts(frame) {
  var w = frame.contentWindow;
  var d = w.document;
  var live = liveCharts(w);
  var found = [];
  live.forEach(function (x) {
    var cfg;
    try { cfg = JSON.parse(JSON.stringify(x.cfg)); } catch (e) { return; }
    found.push({ el: x.el, kind: x.kind, cfg: cfg, title: titleNear(x.el),
                 source: textNear(x.el, ".source-line") || cfg.sourceLine || "",
                 calc: textNear(x.el, "details.calc .calc-body") });
  });
  rawCharts(w, live).forEach(function (el) {
    var url;
    try {
      url = w.echarts.getInstanceByDom(el).getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
    } catch (e) { return; }
    found.push({ el: el, image: url, title: titleNear(el), source: textNear(el, ".source-line"),
                 width: el.offsetWidth * 2, height: el.offsetHeight * 2 });
  });
  found.sort(function (a, b) {
    return a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  });
  found.forEach(function (f) { delete f.el; });
  return { page_title: (d.title || "").replace(/\s*[·|]\s*Plover Analytics\s*$/, ""), candidates: found };
}

var KIND_LABEL = { line: "Line chart", stack: "Stacked bars", cols: "Bars", dist: "Distribution",
                   rank: "Ranking", bar: "Bars" };

/* The picker: every chart the page drew, drawn small, newest choice first. */
function pickChart(found, wanted) {
  return new Promise(function (resolve) {
    var dlg = $("#dk-capture");
    var body = $("#dk-capture-body");
    var minis = [];
    // one picker at a time: an earlier one still open is cancelled, never
    // answered with this one's choice
    if (dlg.open) dlg.close();
    body.innerHTML = "<h2>Choose a Chart</h2>" +
      '<p class="hint">From ' + escapeHtml(found.page_title || "the page") + ". " +
      found.candidates.length + (found.candidates.length === 1 ? " chart" : " charts") + " found.</p>" +
      '<ol class="dk-pick">' + found.candidates.map(function (cnd, i) {
        var names = cnd.cfg && cnd.cfg.series ? cnd.cfg.series.slice(0, 3).map(function (s) { return s.name; }).join(", ") : "";
        return '<li' + (wanted === i + 1 ? ' class="wanted"' : "") + '><div class="dk-pick-prev" data-i="' + i + '">' +
          (cnd.image ? '<img alt="" src="' + cnd.image + '">' : "") + "</div>" +
          '<div class="dk-pick-txt"><strong>' + (i + 1) + ". " + escapeHtml(cnd.title || "Untitled chart") + "</strong>" +
          '<span class="muted">' + escapeHtml(cnd.image ? "Image" : (KIND_LABEL[cnd.kind] || cnd.kind)) +
          (names ? " · " + escapeHtml(names) : "") + "</span></div>" +
          '<button type="button" class="btn" data-pick="' + i + '">Use This Chart</button></li>';
      }).join("") + "</ol>" +
      '<div class="dk-dlg-acts"><button type="button" class="btn" data-close>Cancel</button></div>';
    dlg.showModal();
    found.candidates.forEach(function (cnd, i) {
      if (cnd.image) return;
      var box = $('.dk-pick-prev[data-i="' + i + '"]', body);
      try {
        var cfg = JSON.parse(JSON.stringify(cnd.cfg));
        cfg.legendFloor = 9999;
        minis.push(obsChart(box, cnd.kind, cfg));
        var inst = echarts.getInstanceByDom(box);
        if (inst) inst.setOption({ legend: { show: false } });
      } catch (e) { box.textContent = "Preview unavailable"; }
    });
    function close(choice) {
      minis.forEach(function (m) { m.dispose(); });
      dlg.removeEventListener("close", onClose);
      if (dlg.open) dlg.close();
      resolve(choice);
    }
    function onClose() { close(null); }
    dlg.addEventListener("close", onClose);
    $all("[data-pick]", body).forEach(function (b) {
      b.addEventListener("click", function () { close(Number(b.getAttribute("data-pick"))); });
    });
  });
}

function dataUrlBlob(url) {
  var bin = atob(url.split(",")[1]);
  var buf = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Blob([buf], { type: "image/png" });
}

/* Copy a chart into a snapshot block: open the page, choose (or take the
   one asked for), store it. `choose` forces the picker. */
function copyChart(blockEl, model, choose, quiet) {
  snapStatus(blockEl, "Opening " + model.url + " and reading its charts…");
  return capturePage(model.url).then(function (found) {
    if (!document.contains(blockEl)) return false;
    var n = found.candidates.length;
    var pick = null;
    if (!choose && model.chart && model.chart <= n) pick = model.chart - 1;
    else if (!choose && n === 1) pick = 0;
    if (pick === null) {
      if (quiet) { snapStatus(blockEl, n + " charts on that page: choose one.", false); return false; }
      return pickChart(found, model.chart).then(function (i) {
        if (i === null) { snapStatus(blockEl, ""); return false; }
        return applyCapture(blockEl, model, found, i);
      });
    }
    return applyCapture(blockEl, model, found, pick);
  }).catch(function (err) {
    snapStatus(blockEl, err.message, true);
    return false;
  });
}

function applyCapture(blockEl, model, found, i) {
  var cnd = found.candidates[i];
  if (cnd.image) {
    // a chart the page draws without charts.js: kept as an image of it
    return fetch("/admin/api/research/media", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "image/png", "X-File-Name": "plover-chart.png" },
      body: dataUrlBlob(cnd.image),
    }).then(function (r) {
      return r.json().then(function (b) { if (!r.ok) throw new Error(b.detail || "Upload failed"); return b; });
    }).then(function (info) {
      var title = model.title || cnd.title;
      replaceBlock(blockEl, { id: model.id, type: "image", media: info.media, ext: info.ext,
        width: info.width, height: info.height, caption: title, alt: title,
        source: cnd.source || ("Plover Analytics, " + (found.page_title || model.url)), link: model.url });
      return true;
    });
  }
  Object.assign(model, {
    kind: cnd.kind, cfg: cnd.cfg, page_title: found.page_title, source: cnd.source, calc: cnd.calc,
    captured_at: todayIso(), chart: i + 1, title: model.title || cnd.title,
  });
  renderSnapshotBlock(blockEl.querySelector(".dk-content"), model);
  updateCounts();
  changed();
  return Promise.resolve(true);
}

/* Charts an agent named over MCP: copy them as soon as the draft opens. */
function capturePending() {
  var pending = $all('.dk-block[data-type="snapshot"]', blocksEl()).filter(function (el) {
    var m = S.models[el.getAttribute("data-id")];
    return m && m.url && !m.cfg;
  });
  if (!pending.length) return;
  toast("Copying " + pending.length + (pending.length === 1 ? " chart" : " charts") + " from Plover pages…");
  var copied = 0;
  pending.reduce(function (p, el) {
    return p.then(function () {
      return copyChart(el, S.models[el.getAttribute("data-id")], false, true).then(function (ok) { if (ok) copied++; });
    });
  }, Promise.resolve()).then(function () {
    if (copied) toast("Copied " + copied + (copied === 1 ? " chart" : " charts") + " from Plover pages.");
  });
}

/* A Plover page address pasted on its own line becomes a copied chart. */
function pasteAsChart(blockEl, model, target, text) {
  var raw = (text || "").trim();
  if (/\s/.test(raw) || model.type !== "p" || !isEmpty(target)) return false;
  var path = pagePath(raw);
  if (!path) return false;
  var el = replaceBlock(blockEl, { type: "snapshot", url: path, chart: chartIndexOf(raw), title: "", note: "" });
  copyChart(el, S.models[el.getAttribute("data-id")], false);
  return true;
}

/* ---------------------------------------------------------------- caret memory */

/* The panel takes focus away from the draft; remember where the writer was,
   so a value, citation or quote lands there. */
function rememberCaret() {
  var s = window.getSelection();
  if (!s.rangeCount) return;
  var node = s.anchorNode;
  var el = node && (node.nodeType === 1 ? node : node.parentNode);
  var ed = el && el.closest ? el.closest("#dk-blocks [contenteditable=true]") : null;
  if (!ed) return;
  S.lastRange = s.getRangeAt(0).cloneRange();
  S.lastBlock = blockOf(ed);
}

function restoreCaret() {
  var r = S.lastRange;
  var ok = r && document.contains(r.startContainer);
  var ed;
  if (ok) {
    var n = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentNode;
    ed = n.closest("[contenteditable=true]");
  }
  if (!ed) {
    var texts = $all('.dk-block[data-type="p"] [contenteditable=true]', blocksEl());
    ed = texts[texts.length - 1];
    if (!ed) {
      var p = insertBlock({ type: "p", html: "" }, blocksEl().lastElementChild, false);
      ed = p.querySelector("[contenteditable=true]");
    }
    r = document.createRange();
    r.selectNodeContents(ed);
    r.collapse(false);
  }
  ed.focus();
  var s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  return ed;
}

function insertAtCaret(html) {
  restoreCaret();
  document.execCommand("insertHTML", false, html);
  rememberCaret();
  changed();
}

var SLOT_FILLERS = { chart: true, snapshot: true, datachart: true, image: true, table: true };

/* A block from the research panel goes in after the block the writer was
   in — or, when the open tab is finding a chart the plan asked for, in
   place of that planned chart (kept on the block as _slot, so a copy that
   comes to nothing can put it back). */
function insertAfterCaretBlock(b) {
  var slot = SLOT_FILLERS[b.type] ? slotEl(S.slotTarget) : null;
  if (slot) {
    var planned = readBlock(slot);
    var put = replaceBlock(slot, b, false);
    put._slot = planned;
    RT.tabs.forEach(function (t) { if (t.slot === planned.id) t.slot = ""; });
    S.slotTarget = null;
    var line = RT.active && $(".dk-rt-slot", RT.active.el);
    if (line) line.parentNode.removeChild(line);
    saveTabs();
    S.lastBlock = put;
    put.scrollIntoView({ block: "center", behavior: "smooth" });
    put.classList.add("dk-flash");
    setTimeout(function () { put.classList.remove("dk-flash"); }, 900);
    return put;
  }
  var after = S.lastBlock && document.contains(S.lastBlock) ? S.lastBlock : blocksEl().lastElementChild;
  var el = insertBlock(b, after, false);
  S.lastBlock = el;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.classList.add("dk-flash");
  setTimeout(function () { el.classList.remove("dk-flash"); }, 900);
  return el;
}

/* ---------------------------------------------------------------- research panel: tabs */

/* The Research tab works like a browser. Plan and Notes stay pinned on the
   left; every search and every page the writer opens is a tab of its own,
   kept until they close it, and remembered in this browser for this
   article. "+" opens a new tab every time: search Plover data, read a page,
   find a chart the plan asks for, or open a saved article. */

var RT = { tabs: [], active: null, closed: [], seq: 0, box: null, noteFilter: "all" };
var RT_MAX = 8;          // source tabs; opening one more closes the oldest

function rtKey() { return "dk-rtabs-" + ID; }

function renderResearchPane() {
  var box = $("#dk-pane-research");
  if (box.getAttribute("data-ready")) { refreshResearch(); return; }
  box.setAttribute("data-ready", "1");
  box.classList.add("dk-rt");
  box.innerHTML = '<div class="dk-rt-strip" role="tablist" aria-label="Research tabs"></div>' +
    '<div class="dk-rt-body"></div>';
  RT.box = box;
  RT.tabs = [];
  addTab({ kind: "plan", title: "Plan", pinned: true });
  addTab({ kind: "notes", title: "Notes", pinned: true });
  addTab({ kind: "charts", title: "Charts", pinned: true });
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(rtKey()) || "null"); } catch (e) { saved = null; }
  ((saved && saved.tabs) || []).filter(function (sp) { return sp.kind === "data" || sp.kind === "web"; })
    .slice(0, RT_MAX).forEach(function (sp) { addTab(sp); });
  var want = saved && typeof saved.active === "number" ? RT.tabs[saved.active] : null;
  // the plan when there is one; else the last search or page; else the chart library
  if (!want) want = S.plan || planInPage() ? RT.tabs[0] : (RT.tabs[3] || RT.tabs[2]);
  $(".dk-rt-strip", box).addEventListener("click", function (e) {
    var t = e.target.closest("button");
    if (!t) return;
    if (t.hasAttribute("data-rtnew")) { openResearchTab({ kind: "new", title: "New Tab" }); return; }
    var x = tabById(t.getAttribute("data-rtx"));
    if (x) { closeTab(x); return; }
    var a = tabById(t.getAttribute("data-rt"));
    if (a) activateTab(a);
  });
  activateTab(want);
}

function tabById(id) {
  return id ? RT.tabs.filter(function (t) { return String(t.id) === String(id); })[0] : null;
}

function tabOf(el) {
  return RT.tabs.filter(function (t) { return t.el === el || t.el.contains(el); })[0] || null;
}

function addTab(spec) {
  var t = { id: ++RT.seq, kind: spec.kind, title: spec.title || "New Tab", q: spec.q || "", url: spec.url || "",
            clip: spec.clip || 0, slot: spec.slot || "", pinned: !!spec.pinned, loaded: false };
  t.el = document.createElement("div");
  t.el.className = "dk-rt-pane";
  t.el.setAttribute("role", "tabpanel");
  t.el.hidden = true;
  $(".dk-rt-body", RT.box).appendChild(t.el);
  RT.tabs.push(t);
  var sources = RT.tabs.filter(function (x) { return !x.pinned; });
  if (sources.length > RT_MAX) closeTab(sources[0], true);
  drawStrip();
  return t;
}

/* Open (or go back to) a research tab: {kind: "data", q} · {kind: "web", url
   or clip} · {kind: "new"}; `slot` names a planned chart the tab's charts go
   into. */
function openResearchTab(spec, activate) {
  showPane("research");
  var same = spec.kind === "new" ? null : RT.tabs.filter(function (t) {
    return t.kind === spec.kind && ((spec.q && t.q === spec.q) || (spec.url && t.url === spec.url) ||
                                    (spec.clip && t.clip === spec.clip));
  })[0];
  var t = same || addTab(spec);
  if (same && spec.slot !== undefined && same.slot !== spec.slot) { same.slot = spec.slot; same.loaded = false; }
  if (activate !== false) activateTab(t);
  saveTabs();
  return t;
}

function activateTab(t) {
  if (!t) return;
  // "+" always opens a tab, like a browser; empty ones close once the writer
  // goes to a search, a page, Plan or Notes, so blank tabs never pile up
  if (t.kind !== "new") {
    RT.tabs.filter(function (x) { return x.kind === "new" && !typedIn(x); })
      .forEach(function (x) { closeTab(x, true); });
  }
  RT.active = t;
  RT.tabs.forEach(function (x) { x.el.hidden = x !== t; });
  S.slotTarget = t.slot && slotEl(t.slot) ? t.slot : null;
  if (t.kind === "plan" || t.kind === "notes" || t.kind === "charts" || !t.loaded) fillTab(t);
  drawStrip();
  saveTabs();
}

function typedIn(t) {
  return $all("input", t.el).some(function (i) { return i.value.trim(); });
}

function fillTab(t) {
  t.loaded = true;
  if (t.kind === "plan") return planTab(t.el);
  if (t.kind === "notes") return notesTab(t.el);
  if (t.kind === "charts") return chartsTab(t.el);
  if (t.kind === "data") return dataTab(t);
  if (t.kind === "web") return webTab(t);
  return newTab(t);
}

function closeTab(t, quiet) {
  var i = RT.tabs.indexOf(t);
  if (i < 0 || t.pinned) return;
  RT.tabs.splice(i, 1);
  t.el.parentNode.removeChild(t.el);
  if (t.kind === "data" || t.kind === "web") {
    RT.closed.unshift({ kind: t.kind, title: t.title, q: t.q, url: t.url, clip: t.clip });
    RT.closed = RT.closed.slice(0, 5);
  }
  if (RT.active === t && !quiet) {
    var next = RT.tabs[i] || RT.tabs[i - 1];
    activateTab(next);
  }
  drawStrip();
  saveTabs();
}

function drawStrip() {
  var strip = RT.box && $(".dk-rt-strip", RT.box);
  if (!strip) return;
  strip.innerHTML = RT.tabs.map(function (t) {
    var on = t === RT.active;
    var label = t.kind === "notes"
      ? 'Notes <span class="dk-rt-n">' + S.notes.length + "</span>" : escapeHtml(t.title);
    var kind = t.kind === "data" ? '<span class="dk-rt-k">Data</span>'
      : t.kind === "web" ? '<span class="dk-rt-k web">Web</span>' : "";
    return '<div class="dk-rt-tab' + (on ? " on" : "") + (t.pinned ? " pinned" : "") + '">' +
      '<button type="button" role="tab" class="dk-rt-b" aria-selected="' + on + '" data-rt="' + t.id + '" title="' +
      escapeHtml(t.title) + '">' + kind + '<span class="dk-rt-t">' + label + "</span></button>" +
      (t.pinned ? "" : '<button type="button" class="dk-rt-x" data-rtx="' + t.id + '" aria-label="Close ' +
        escapeHtml(t.title) + '" title="Close">×</button>') + "</div>";
  }).join("") +
    '<button type="button" class="dk-rt-plus" data-rtnew="1" aria-label="Open a new tab" title="New tab: search data or open a page">+</button>';
}

function saveTabs() {
  if (!RT.box) return;
  var src = RT.tabs.filter(function (t) { return !t.pinned && t.kind !== "new"; });
  var spec = src.map(function (t) { return { kind: t.kind, title: t.title, q: t.q, url: t.url, clip: t.clip, slot: t.slot }; });
  try {
    localStorage.setItem(rtKey(), JSON.stringify({ tabs: spec, active: RT.active && RT.active.kind !== "new"
      ? RT.tabs.filter(function (t) { return t.kind !== "new"; }).indexOf(RT.active) : -1 }));
  } catch (e) { /* storage blocked: tabs are not remembered, nothing else breaks */ }
}

/* The draft changed under the panel (loaded, restored, a note added): keep
   the counts and the Plan and Notes tabs current. */
function refreshResearch() {
  if (!RT.box) return;
  drawStrip();
  var t = RT.active;
  if (t && (t.kind === "plan" || t.kind === "notes") && !$("#dk-pane-research").hidden &&
      !t.el.contains(document.activeElement)) fillTab(t);
}

function slotEl(id) {
  var el = id && blocksEl().querySelector('.dk-block[data-id="' + id + '"]');
  return el && (S.models[id] || {}).type === "placeholder" ? el : null;
}

/* The data search box, on a new tab and on every data tab. Its examples are
   typed into the same search by tests/test_search_examples.py. */
var DATA_SEARCH_INPUT = '<input type="search" class="dk-rs-q" placeholder="Series, company or topic, e.g. core CPI, US yields, 7203, CEO pay" aria-label="Search Plover data">';

/* ---- a new tab ---- */

function newTab(t) {
  var el = t.el;
  var slots = plannedCharts();
  if (!slots.length && !S.plan && planInPage()) {
    // the plan is still in the page: its charts can be searched for already
    slots = parseOutline(pageBlocks()).filter(function (x) { return x.chart; })
      .map(function (x) { return { id: "", text: x.chart }; });
  }
  el.innerHTML =
    '<p class="dk-rt-lead">Search Plover data or open a web page. Each search or page you open becomes a tab of its own.</p>' +
    '<p class="dk-rs-h">Search Plover Data</p>' +
    '<form class="dk-rs-form" data-go="data">' + DATA_SEARCH_INPUT + '<button type="submit" class="dk-mini">Search</button></form>' +
    '<p class="dk-rs-h">Open a Web Page</p>' +
    '<form class="dk-rs-form" data-go="web"><input type="url" aria-label="Page address" placeholder="https://…">' +
    '<button type="submit" class="dk-mini">Read</button></form>' +
    (slots.length ? '<p class="dk-rs-h">Charts Your Plan Asks For</p><ul class="dk-rs-list">' + slots.map(function (s, i) {
      return '<li><span class="dk-rs-name">' + escapeHtml(s.text) + '</span><span class="dk-rs-acts">' +
        '<button type="button" class="dk-mini" data-slotfind="' + i + '">Find Data</button>' +
        (s.id ? '<button type="button" class="dk-mini" data-slotai="' + i + '">Suggest a Chart</button>' : "") + "</span></li>";
    }).join("") + "</ul>" : "") +
    (RT.closed.length ? '<p class="dk-rs-h">Recently Closed</p><ul class="dk-rs-list">' + RT.closed.map(function (c, i) {
      return '<li><span class="dk-rs-name">' + escapeHtml(c.title) + '</span><span class="dk-rs-acts">' +
        '<button type="button" class="dk-mini" data-reopen="' + i + '">Reopen</button></span></li>';
    }).join("") + "</ul>" : "") +
    '<div class="dk-rt-clips"></div>';
  var saved = document.createElement("div");
  $('[data-go="data"]', el).after(saved);
  api("/research/market").then(function (r) {
    if (!document.contains(saved)) return;
    saved.innerHTML = marketList(r.market || []);
    bindMarket(saved, r.market || []);
  }).catch(function () { saved.innerHTML = '<p class="hint">Saved futures could not load. Reopen this tab to try again.</p>'; });
  $('[data-go="data"]', el).addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("input", e.target).value.trim();
    if (!q) { $("input", e.target).focus(); return; }
    becomeTab(t, { kind: "data", q: q, title: q });
  });
  // results come as the writer types: the tab becomes a data tab, with the
  // cursor still in the box
  whenTyped($('[data-go="data"] input', el), function (q) {
    t.typing = true;
    becomeTab(t, { kind: "data", q: q, title: q });
  });
  $('[data-go="web"]', el).addEventListener("submit", function (e) {
    e.preventDefault();
    var url = $("input", e.target).value.trim();
    if (!url) { $("input", e.target).focus(); return; }
    becomeTab(t, { kind: "web", url: url, title: hostOf(url) });
  });
  $all("[data-slotfind]", el).forEach(function (b) {
    b.addEventListener("click", function () {
      var s = slots[Number(b.getAttribute("data-slotfind"))];
      becomeTab(t, { kind: "data", q: searchWords(s.text), title: searchWords(s.text), slot: s.id || "" });
    });
  });
  $all("[data-slotai]", el).forEach(function (b) {
    b.addEventListener("click", function () { suggestChart(slots[Number(b.getAttribute("data-slotai"))].id); });
  });
  $all("[data-reopen]", el).forEach(function (b) {
    b.addEventListener("click", function () {
      var c = RT.closed.splice(Number(b.getAttribute("data-reopen")), 1)[0];
      becomeTab(t, c);
    });
  });
  renderClips($(".dk-rt-clips", el));
  setTimeout(function () { var i = $("input", el); if (i && !el.hidden) i.focus(); }, 0);
}

/* A new tab turns into the search or page the writer asked for. */
function becomeTab(t, spec) {
  t.kind = spec.kind;
  t.title = spec.title || t.title;
  t.q = spec.q || "";
  t.url = spec.url || "";
  t.clip = spec.clip || 0;
  t.slot = spec.slot || "";
  S.slotTarget = t.slot || null;
  fillTab(t);
  drawStrip();
  saveTabs();
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return url.slice(0, 30); }
}

/* A planned chart's description, cut to the words a series search can use:
   "Total vs China arrivals, monthly levels" → "China arrivals". */
var SEARCH_STOP = /^(vs|versus|and|or|the|of|in|by|for|on|to|with|a|an|monthly|quarterly|annual|yearly|daily|level|levels|rate|rates|year-on-year|yoy|total|chart|change|changes|compared|share|index|series|from|since|over|per|cent|percent|\d+)$/i;
function searchWords(text) {
  var words = (text || "").replace(/[(),.:;—–/]/g, " ").split(/\s+/).filter(function (w) {
    return w && !SEARCH_STOP.test(w);
  });
  return words.slice(0, 3).join(" ") || text;
}

/* ---- Plover data ---- */

function dataTab(t) {
  var el = t.el;
  var slot = t.slot && slotEl(t.slot) ? S.models[t.slot] : null;
  el.innerHTML =
    (slot ? '<p class="dk-rt-slot">Charts you insert from this tab go into the planned chart “' +
      escapeHtml(slot.text) + '”. <button type="button" class="linkish" data-noslot="1">Insert where my cursor is instead</button></p>' : "") +
    '<form class="dk-rs-form">' + DATA_SEARCH_INPUT + '<button type="submit" class="dk-mini">Search</button></form>' +
    '<div class="dk-rs-res"><p class="hint">Find a series to chart or quote, or a company or topic whose charts to copy. ' +
    "A value goes in where your cursor was, with a footnote naming its source and date; Save to Notes keeps it for later.</p></div>";
  var input = $("input", el);
  input.value = t.q;
  function run(q) {
    t.q = q;
    t.title = q;
    drawStrip();
    saveTabs();
    dataSearch(q, $(".dk-rs-res", el));
  }
  var typed = whenTyped(input, run);
  $("form", el).addEventListener("submit", function (e) {
    e.preventDefault();
    typed.cancel();
    var q = input.value.trim();
    if (!q) { input.focus(); return; }
    run(q);
  });
  var ns = $("[data-noslot]", el);
  if (ns) ns.addEventListener("click", function () { t.slot = ""; S.slotTarget = null; saveTabs(); dataTab(t); });
  if (t.q) dataSearch(t.q, $(".dk-rs-res", el));
  if (!t.q || t.typing) {
    t.typing = false;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

/* Search as the writer types: a quarter-second after the last key, from two
   characters. Search (or Enter) still runs it at once. */
function whenTyped(input, fn) {
  var timer = null;
  var last = input.value.trim();
  input.addEventListener("input", function () {
    clearTimeout(timer);
    var q = input.value.trim();
    if (q.length < 2 || q === last) return;
    timer = setTimeout(function () { last = q; fn(q); }, 250);
  });
  return { cancel: function () { clearTimeout(timer); last = input.value.trim(); } };
}

/* A search that finds nothing is tried again with its last word dropped
   ("China Korea Taiwan" → "China"), and the panel says so. */
function dataSearch(q, res, asked) {
  if (!q.trim()) return;
  // while typing, the last results stay up (dimmed) under a line saying
  // what is being searched for; a reply to an older search that comes late
  // is dropped
  var seq = res._seq = (res._seq || 0) + 1;
  var busy = '<p class="muted dk-rs-busy">Searching for “' + escapeHtml(q) + "”…</p>";
  if ($(".dk-rs-list", res)) {
    res.setAttribute("aria-busy", "true");
    var line = $(".dk-rs-busy", res);
    if (line) line.outerHTML = busy;
    else res.insertAdjacentHTML("afterbegin", busy);
  } else {
    res.innerHTML = busy;
  }
  api("/research/data/search?q=" + encodeURIComponent(q)).then(function (r) {
    if (seq !== res._seq) return;
    res.removeAttribute("aria-busy");
    var pages = r.pages || [];
    if (!r.series.length && !r.companies.length && !pages.length && !(r.common || []).length &&
        !(r.places || []).length && !(r.market || []).length) {
      var shorter = q.trim().split(/\s+/).slice(0, -1).join(" ");
      if (shorter) { dataSearch(shorter, res, asked || q); return; }
      res.innerHTML = '<p class="muted">Nothing matches “' + escapeHtml(asked || q) + '”. Try a shorter name, ' +
        "or a series code. Or ask the AI to suggest a chart.</p>";
      return;
    }
    // a search for a company (its code, or the start of its name) lists companies first
    var ql = q.trim().toLowerCase();
    var firstCo = r.companies.some(function (c) {
      return String(c.code).toLowerCase() === ql || (c.name || "").toLowerCase().indexOf(ql) === 0;
    });
    // every series is one list (its index is what the buttons name), shown
    // grouped by dataset in the order the search ranked them, three a group
    // until the writer asks for more
    var places = r.places || [];
    var allSeries = r.series.concat(places);
    function seriesRow(s, i, hidden) {
      return '<li' + (hidden ? ' class="dk-rs-more" hidden' : "") + '><span class="dk-rs-name" title="' + escapeHtml(s.dataset_name + " · " + s.code) + '">' +
        escapeHtml(s.name) + "</span>" +
        '<span class="dk-rs-acts"><button type="button" class="dk-mini" data-schart="' + i + '">Insert Chart</button>' +
        '<button type="button" class="dk-mini" data-svalue="' + i + '">Insert Value</button>' +
        '<button type="button" class="dk-mini" data-snote="' + i + '">Save Value to Notes</button></span></li>';
    }
    function groupsHtml(from, to) {
      var order = [], by = {};
      for (var i = from; i < to; i++) {
        var s = allSeries[i];
        if (!by[s.dataset]) { by[s.dataset] = []; order.push(s.dataset); }
        by[s.dataset].push(i);
      }
      return order.map(function (ds) {
        var idx = by[ds];
        return '<div class="dk-rs-group"><p class="dk-rs-gh">' + escapeHtml(allSeries[idx[0]].dataset_name) +
          ' <span class="muted">' + idx.length + "</span></p><ul class=\"dk-rs-list\">" +
          idx.map(function (i, k) { return seriesRow(allSeries[i], i, k >= 3); }).join("") + "</ul>" +
          (idx.length > 3 ? '<button type="button" class="linkish dk-rs-showmore">Show ' + (idx.length - 3) + " more</button>" : "") +
          "</div>";
      }).join("");
    }
    var seriesHtml = r.series.length ? '<p class="dk-rs-h">Series</p>' + groupsHtml(0, r.series.length) : "";
    var placeHtml = places.length ? '<details class="dk-rs-places"><summary>Places named “' + escapeHtml(q) + "” (" +
      places.length + ")</summary>" + groupsHtml(r.series.length, allSeries.length) + "</details>" : "";
    var common = r.common || [];
    var commonHtml = common.length ? '<p class="dk-rs-h">Ready Charts</p><ul class="dk-rs-list">' + common.map(function (c, i) {
      return '<li><span class="dk-rs-name">' + escapeHtml(c.title) + "</span>" +
        '<span class="dk-rs-acts"><button type="button" class="dk-mini" data-cmins="' + i + '">Insert Chart</button>' +
        '<button type="button" class="dk-mini" data-cmsave="' + i + '">Save</button></span></li>';
    }).join("") + "</ul>" : "";
    var lead = r.company_first && r.companies.length ? r.companies[0] : null;
    var leadHtml = lead ? '<p class="dk-rs-h">' + escapeHtml(lead.name) + ' <span class="mono muted">' + escapeHtml(lead.code) + "</span></p>" +
      ((lead.charts || []).length ? '<ul class="dk-rs-list">' + lead.charts.map(function (c, i) {
        return '<li><span class="dk-rs-name">' + escapeHtml(c.title) + "</span>" +
          '<span class="dk-rs-acts"><button type="button" class="dk-mini" data-coins="' + i + '">Insert Chart</button>' +
          '<button type="button" class="dk-mini" data-cosave="' + i + '">Save</button></span></li>';
      }).join("") + "</ul>" : '<p class="muted">No filed figures to chart for this company yet.</p>') +
      '<p class="dk-rs-pages">Its pages: ' + lead.pages.map(function (p) {
        return '<a href="' + escapeHtml(p.url) + '" target="_blank" rel="noopener">' + escapeHtml(p.label) + "</a>";
      }).join(" · ") + "</p>" : "";
    var others = lead ? r.companies.slice(1) : r.companies;
    var companyHtml =
      (others.length ? '<p class="dk-rs-h">' + (lead ? "Other Companies" : "Companies") + '</p><ul class="dk-rs-list">' + others.map(function (c) {
        var i = r.companies.indexOf(c);
        return '<li><span class="dk-rs-name" title="' + escapeHtml(c.name_ja || "") + '">' + escapeHtml(c.name) +
          ' <span class="mono muted">' + escapeHtml(c.code) + "</span></span>" +
          '<span class="dk-rs-acts"><select data-cpage="' + i + '" aria-label="Page">' + c.pages.map(function (p) {
            return '<option value="' + escapeHtml(p.url) + '">' + escapeHtml(p.label) + "</option>";
          }).join("") + '</select><button type="button" class="dk-mini" data-ccopy="' + i + '">Copy a Chart</button>' +
          '<button type="button" class="dk-mini" data-copen="' + i + '">Open</button></span></li>';
      }).join("") + "</ul>" : "");
    // pages about the topic ("CEO compensation"): copy one of their charts
    var pageHtml =
      (pages.length ? '<p class="dk-rs-h">Pages</p><ul class="dk-rs-list">' + pages.map(function (pg, i) {
        return '<li><span class="dk-rs-name" title="' + escapeHtml(pg.description) + '">' + escapeHtml(pg.title) +
          ' <span class="mono muted">' + escapeHtml(pg.url.replace(/^\//, "")) + "</span></span>" +
          '<span class="dk-rs-acts"><button type="button" class="dk-mini" data-pcopy="' + i + '">Copy a Chart</button>' +
          '<button type="button" class="dk-mini" data-popen="' + i + '">Open</button></span></li>';
      }).join("") + "</ul>" : "");
    res.innerHTML = (asked ? '<p class="hint">Nothing for “' + escapeHtml(asked) + '”; showing “' +
      escapeHtml(q) + "”.</p>" : "") + marketList(r.market || []) + commonHtml + leadHtml +
      (firstCo ? companyHtml + seriesHtml : seriesHtml + companyHtml) + pageHtml + placeHtml;
    bindMarket(res, r.market || []);
    $all(".dk-rs-showmore", res).forEach(function (b) {
      b.addEventListener("click", function () {
        $all(".dk-rs-more", b.parentNode).forEach(function (li) { li.hidden = false; });
        b.remove();
      });
    });
    $all("[data-cmins]", res).forEach(function (b) {
      b.addEventListener("click", function () { insertCommon(common[Number(b.getAttribute("data-cmins"))]); });
    });
    $all("[data-cmsave]", res).forEach(function (b) {
      b.addEventListener("click", function () { saveChart(common[Number(b.getAttribute("data-cmsave"))].block, b); });
    });
    $all("[data-coins]", res).forEach(function (b) {
      b.addEventListener("click", function () { insertCompanyChart(lead, lead.charts[Number(b.getAttribute("data-coins"))]); });
    });
    $all("[data-cosave]", res).forEach(function (b) {
      b.addEventListener("click", function () {
        var c = lead.charts[Number(b.getAttribute("data-cosave"))];
        saveChart({ type: "snapshot", url: c.url, chart: 1, title: companyTitle(lead, c) }, b);
      });
    });
    $all("[data-pcopy]", res).forEach(function (b) {
      b.addEventListener("click", function () { copyFromSearch(pages[Number(b.getAttribute("data-pcopy"))].url); });
    });
    $all("[data-popen]", res).forEach(function (b) {
      b.addEventListener("click", function () {
        window.open(pages[Number(b.getAttribute("data-popen"))].url, "_blank", "noopener");
      });
    });
    $all("[data-schart]", res).forEach(function (b) {
      b.addEventListener("click", function () {
        var s = allSeries[Number(b.getAttribute("data-schart"))];
        var into = S.slotTarget;
        insertAfterCaretBlock({ type: "chart", dataset: s.dataset, series: [s.code], measure: "index",
                                start: "", end: "", title: s.name, note: "" });
        toast(into ? "Chart put in the planned place. Use the buttons above it to change the measure or the dates."
          : "Chart inserted. Use the buttons above it to change the measure or the dates.");
      });
    });
    $all("[data-svalue]", res).forEach(function (b) {
      b.addEventListener("click", function () { insertValue(allSeries[Number(b.getAttribute("data-svalue"))], b); });
    });
    $all("[data-snote]", res).forEach(function (b) {
      b.addEventListener("click", function () { noteValue(allSeries[Number(b.getAttribute("data-snote"))], b); });
    });
    $all("[data-ccopy]", res).forEach(function (b) {
      b.addEventListener("click", function () {
        copyFromSearch($('[data-cpage="' + b.getAttribute("data-ccopy") + '"]', res).value);
      });
    });
    $all("[data-copen]", res).forEach(function (b) {
      b.addEventListener("click", function () {
        window.open($('[data-cpage="' + b.getAttribute("data-copen") + '"]', res).value, "_blank", "noopener");
      });
    });
  }).catch(function (err) {
    if (seq !== res._seq) return;
    res.removeAttribute("aria-busy");
    res.innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>";
  });
}

/* Saved paid data is listed only when the server grants this account access.
   The chart freezes the selected values in the draft; the full CSV is read
   from the same saved version. A failed request leaves no empty chart. */
function marketList(cards) {
  if (!cards.length) return "";
  return '<p class="dk-rs-h">Your Saved Futures</p><p class="muted">Databento · Hourly UTC · Saved history; updates are manual.</p>' +
    cards.map(function (c, i) {
      return '<div class="dk-rs-group" data-market="' + i + '"><ul class="dk-rs-list"><li>' +
        '<span class="dk-rs-name">' + escapeHtml(c.name) + '</span><span class="dk-rs-acts">' +
        '<button type="button" class="dk-mini" data-minsert>Insert Chart</button>' +
        '<a class="dk-mini" data-mcsv href="/admin/api/research/market/' + encodeURIComponent(c.slug) + '/csv?revision=' + encodeURIComponent(c.revision) + '">Download CSV</a></span></li></ul>' +
        '<p class="hint">' + escapeHtml(c.first_bar_utc.slice(0, 10) + " to " + c.last_bar_utc.slice(0, 10) + " · " + c.unit) + '</p>' +
        '<details><summary>Chart Dates and Trade Feed</summary><div class="dk-market-controls">' +
        '<label>Start <input type="date" data-mstart aria-label="Chart start date"></label>' +
        '<label>End <input type="date" data-mend value="' + c.last_bar_utc.slice(0, 10) + '" aria-label="Chart end date"></label>' +
        '<label>Measure <select data-mfield aria-label="Futures measure"><option value="close">Close</option><option value="open">Open</option>' +
        '<option value="high">High</option><option value="low">Low</option><option value="volume">Volume</option></select></label>' +
        '<label>Trade Feed <select data-mpub aria-label="Trade feed">' + Object.keys(c.publisher_rows).map(function (p) {
          var names = { "1": "CME Globex", "57": "ICE Exchange Trades", "84": "ICE Off-Market Trades" };
          return '<option value="' + p + '"' + (Number(p) === c.default_publisher ? " selected" : "") + '>' + escapeHtml(names[p] || p) + '</option>';
        }).join("") + '</select></label></div><p class="hint">Blank start: last 30 days for charts, full history for CSV. Hourly charts show up to 120 days. Unadjusted prices; gaps at contract changes.</p></details></div>';
    }).join("");
}

function bindMarket(el, cards) {
  $all("[data-market]", el).forEach(function (row) {
    var c = cards[Number(row.getAttribute("data-market"))];
    function params() {
      return "?revision=" + encodeURIComponent(c.revision) + "&publisher=" + encodeURIComponent($("[data-mpub]", row).value) +
        "&start=" + encodeURIComponent($("[data-mstart]", row).value) + "&end=" + encodeURIComponent($("[data-mend]", row).value);
    }
    function csvLink() { $("[data-mcsv]", row).href = "/admin/api/research/market/" + encodeURIComponent(c.slug) + "/csv" + params(); }
    row.addEventListener("change", csvLink);
    csvLink();
    var b = $("[data-minsert]", row);
    b.addEventListener("click", function () {
      if (b.disabled) return;
      var article = ID;
      b.disabled = true;
      b.textContent = "Loading…";
      api("/research/market/" + encodeURIComponent(c.slug) + "/chart" + params() + "&field=" + encodeURIComponent($("[data-mfield]", row).value)).then(function (r) {
        if (ID !== article) { toast("The article changed while loading. Insert the chart again in the intended draft."); return; }
        insertAfterCaretBlock(r.block);
        toast("Hourly chart inserted from saved data.");
      }).catch(function (err) { toast(err.message || "Could not load this range. Try again."); })
        .then(function () { b.disabled = false; b.textContent = "Insert Chart"; });
    });
  });
}

/* Copy a chart from a page found in the search. The chart block goes in at
   once, so the writer sees where it will land; if no chart comes of it (the
   page has none, or the writer cancels the choice) the block is taken out
   again — or the planned chart it replaced is put back — and the reason
   shown, so nothing half-made is left in the draft. */
function copyFromSearch(url, chart, title) {
  var el = insertAfterCaretBlock({ type: "snapshot", url: url, chart: chart || 0, title: title || "", note: "" });
  // a page with one chart gives it straight away; with several, the writer chooses
  copyChart(el, S.models[el.getAttribute("data-id")], false).then(function (ok) {
    if (ok || !document.contains(el)) return;
    var st = el.querySelector(".dk-snap-status");
    var why = st && st.classList.contains("bad") ? st.textContent : "";
    if (el._slot) replaceBlock(el, el._slot, false);
    else removeBlock(el);
    toast(why ? why + " Nothing was added." : "No chart chosen. Nothing was added.");
  });
}

/* ---- Charts tab: the chart library (app/chart_library.py) ----
   Saved charts (the writer's own), the publication's coverage companies with
   their charts, the most used and the common charts by topic: each goes in
   with one click, where the cursor is. */

var LIB = { data: null, co: {} };

function libList(rows) { return '<ul class="dk-rs-list">' + rows.join("") + "</ul>"; }

function libRow(title, acts, sub) {
  return '<li><span class="dk-rs-name">' + escapeHtml(title) +
    (sub ? ' <span class="mono muted">' + escapeHtml(sub) + "</span>" : "") + "</span>" +
    '<span class="dk-rs-acts">' + acts + "</span></li>";
}

/* "TOYOTA MOTOR CORPORATION" as filed reads as shouting in a chart title */
function niceName(name) {
  if (!name || name !== name.toUpperCase()) return name;
  return name.toLowerCase().replace(/(^|[\s(&\-\/])([a-z])/g, function (m, a, b) { return a + b.toUpperCase(); })
    .replace(/\b(Co|Ltd|Inc|Corp)\b\.?/g, function (m) { return m; });
}

function companyTitle(co, chart) { return (niceName(co.name) || co.code) + ": " + chart.title; }

function insertCommon(card) {
  insertAfterCaretBlock(JSON.parse(JSON.stringify(card.block)));
  send("POST", "/research/library/used", { id: card.id }).catch(function () { /* a count is a nicety */ });
  toast("Chart inserted. Use the buttons above it to change the measure or the dates.");
}

function insertCompanyChart(co, chart) {
  copyFromSearch(chart.url, 1, companyTitle(co, chart));
}

function insertSaved(row) {
  var b = row.block;
  if (b.type === "snapshot") { copyFromSearch(b.url, b.chart, b.title); return; }
  insertAfterCaretBlock(JSON.parse(JSON.stringify(b)));
  toast("Chart inserted.");
}

/* Keep a chart for later: from the draft (its Save Chart button), a search
   or the library. Saved charts are the writer's own. */
function saveChart(model, btn) {
  var b = model.type === "snapshot"
    ? { type: "snapshot", url: model.url, chart: model.chart || 0, title: model.title || "" }
    : { type: "chart", dataset: model.dataset, series: model.series, measure: model.measure,
        start: model.start || "", end: model.end || "", title: model.title || "" };
  if (btn) btn.disabled = true;
  return send("POST", "/research/library/saved", { block: b }).then(function () {
    toast("Saved. Find it under Research → Charts → Your Saved Charts.");
    if (LIB.data) LIB.data = null;
    var t = RT.tabs.filter(function (x) { return x.kind === "charts"; })[0];
    if (t && RT.active === t) fillTab(t);
  }).catch(function (err) { toast(err.message || "The chart was not saved. Try again."); })
    .then(function () { if (btn) btn.disabled = false; });
}

function chartsTab(el) {
  if (!el.innerHTML) el.innerHTML = '<p class="muted">Loading the chart library…</p>';
  api("/research/library?article=" + encodeURIComponent(ID)).then(function (d) {
    LIB.data = d;
    drawLibrary(el, d);
  }).catch(function (err) { el.innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>"; });
}

function drawLibrary(el, d) {
  var openTopic = el._open || (d.topics[0] && d.topics[0].key);
  el.innerHTML =
    '<form class="dk-rs-form" data-go="data">' + DATA_SEARCH_INPUT + '<button type="submit" class="dk-mini">Search</button></form>' +
    '<p class="dk-rt-lead">Charts that go in with one click, where your cursor is. Search above for anything else.</p>' +
    marketList(d.market || []) +
    (d.saved.length ? '<p class="dk-rs-h">Your Saved Charts</p>' + libList(d.saved.map(function (r, i) {
      return libRow(r.title, '<button type="button" class="dk-mini" data-lsins="' + i + '">Insert</button>' +
        '<button type="button" class="dk-mini" data-lsdel="' + i + '">Remove</button>');
    })) : "") +
    (d.coverage.length ? '<p class="dk-rs-h">' + escapeHtml(d.publication || "Publication") + " Coverage</p>" +
      '<ul class="dk-rs-list dk-lib-cos">' + d.coverage.map(function (c, i) {
        return '<li class="dk-lib-co"><span class="dk-rs-name">' + escapeHtml(c.name) + ' <span class="mono muted">' +
          escapeHtml(c.code) + '</span></span><span class="dk-rs-acts"><button type="button" class="dk-mini" data-lco="' + i +
          '" aria-expanded="false">Charts</button></span><div class="dk-lib-cocharts" hidden></div></li>';
      }).join("") + "</ul>" : "") +
    (d.popular.length ? '<p class="dk-rs-h">Most Used</p>' + libList(d.popular.map(function (c, i) {
      return libRow(c.title, '<button type="button" class="dk-mini" data-lpins="' + i + '">Insert</button>' +
        '<button type="button" class="dk-mini" data-lpsave="' + i + '">Save</button>');
    })) : "") +
    '<p class="dk-rs-h">Common Charts</p>' +
    (d.topics.length ? d.topics.map(function (t) {
      return '<details class="dk-lib-topic" data-topic="' + t.key + '"' + (t.key === openTopic ? " open" : "") + "><summary>" +
        escapeHtml(t.label) + ' <span class="muted">' + t.charts.length + "</span></summary>" +
        libList(t.charts.map(function (c) {
          return libRow(c.title, '<button type="button" class="dk-mini" data-lcins="' + c.id + '">Insert</button>' +
            '<button type="button" class="dk-mini" data-lcsave="' + c.id + '">Save</button>');
        })) + "</details>";
    }).join("") : '<p class="muted">No data on this server yet.</p>');
  var byId = {};
  bindMarket(el, d.market || []);
  d.topics.forEach(function (t) { t.charts.forEach(function (c) { byId[c.id] = c; }); });
  var form = $('[data-go="data"]', el);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("input", form).value.trim();
    if (q) openResearchTab({ kind: "data", q: q, title: q });
  });
  $all("details.dk-lib-topic", el).forEach(function (x) {
    x.addEventListener("toggle", function () { if (x.open) el._open = x.getAttribute("data-topic"); });
  });
  $all("[data-lcins]", el).forEach(function (b) { b.addEventListener("click", function () { insertCommon(byId[b.getAttribute("data-lcins")]); }); });
  $all("[data-lcsave]", el).forEach(function (b) { b.addEventListener("click", function () { saveChart(byId[b.getAttribute("data-lcsave")].block, b); }); });
  $all("[data-lpins]", el).forEach(function (b) { b.addEventListener("click", function () { insertCommon(d.popular[Number(b.getAttribute("data-lpins"))]); }); });
  $all("[data-lpsave]", el).forEach(function (b) { b.addEventListener("click", function () { saveChart(d.popular[Number(b.getAttribute("data-lpsave"))].block, b); }); });
  $all("[data-lsins]", el).forEach(function (b) { b.addEventListener("click", function () { insertSaved(d.saved[Number(b.getAttribute("data-lsins"))]); }); });
  $all("[data-lsdel]", el).forEach(function (b) {
    b.addEventListener("click", function () {
      var row = d.saved[Number(b.getAttribute("data-lsdel"))];
      b.disabled = true;
      api("/research/library/saved/" + row.id, { method: "DELETE" }).then(function () {
        toast("Removed “" + row.title + "” from your saved charts.");
        chartsTab(el);
      }).catch(function (err) { b.disabled = false; toast(err.message); });
    });
  });
  $all("[data-lco]", el).forEach(function (b) {
    b.addEventListener("click", function () {
      var co = d.coverage[Number(b.getAttribute("data-lco"))];
      var box = $(".dk-lib-cocharts", b.closest("li"));
      var open = box.hidden;
      box.hidden = !open;
      b.setAttribute("aria-expanded", String(open));
      if (!open) return;
      function show(charts) {
        box.innerHTML = charts.length ? libList(charts.map(function (c, i) {
          return libRow(c.title, '<button type="button" class="dk-mini" data-ccins="' + i + '">Insert</button>' +
            '<button type="button" class="dk-mini" data-ccsave="' + i + '">Save</button>');
        })) : '<p class="muted">No filed figures to chart for this company yet.</p>';
        $all("[data-ccins]", box).forEach(function (x) { x.addEventListener("click", function () { insertCompanyChart(co, charts[Number(x.getAttribute("data-ccins"))]); }); });
        $all("[data-ccsave]", box).forEach(function (x) {
          x.addEventListener("click", function () {
            var c = charts[Number(x.getAttribute("data-ccsave"))];
            saveChart({ type: "snapshot", url: c.url, chart: 1, title: companyTitle(co, c) }, x);
          });
        });
      }
      if (LIB.co[co.code]) { show(LIB.co[co.code]); return; }
      box.innerHTML = '<p class="muted">Finding ' + escapeHtml(co.name) + "’s charts…</p>";
      api("/research/company-charts/" + encodeURIComponent(co.code)).then(function (res) {
        LIB.co[co.code] = res.charts;
        show(res.charts);
      }).catch(function (err) { box.innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>"; });
    });
  });
}

/* Thousands grouped, every published decimal kept, true minus. */
function grouped(v) {
  var parts = String(Math.abs(v)).split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (v < 0 ? "−" : "") + parts.join(".");
}

/* The latest published value, exactly as published, and a footnote that
   names the series, the period, the release and the day it was read. */
function readValue(s) {
  return fetch("/api/v1/" + encodeURIComponent(s.dataset) + "/observations?series=" + encodeURIComponent(s.code) + "&measure=index")
    .then(function (r) { return r.json().then(function (b) { if (!r.ok) throw new Error(b.detail || "Could not read the value"); return b; }); })
    .then(function (body) {
      var pts = body.series[0].points.filter(function (p) { return p[1] !== null; });
      if (!pts.length) throw new Error("This series has no published value.");
      var last = pts[pts.length - 1];
      var unit = body.unit && body.unit !== "index" ? body.unit : "";
      var period = last[0].length > 10 ? last[0].slice(0, 10) : (body.release && body.release.frequency === "daily" ? last[0] : last[0].slice(0, 7));
      var shown = grouped(last[1]) + (unit === "%" ? "%" : (unit ? " " + unit : ""));
      var rel = body.release || {};
      var note = s.name + " (" + s.dataset_name + ", series " + s.code + "), " + period + ": " + shown +
        (body.trust === "official" ? ", as published" : "") + ". " +
        (rel.source_name ? rel.source_name + (rel.label ? ", release " + rel.label : "") + ". " : "") +
        "Read from Plover Analytics on " + dateLong(todayIso()) + ".";
      return { shown: shown, note: note, period: period };
    });
}

function insertValue(s, btn) {
  btn.disabled = true;
  readValue(s).then(function (v) {
    insertAtCaret(escapeHtml(v.shown) + noteSup(v.note));
    toast("Inserted " + v.shown + " with its footnote.");
  }).catch(function (err) { toast(err.message); })
    .then(function () { btn.disabled = false; });
}

function noteValue(s, btn) {
  btn.disabled = true;
  readValue(s).then(function (v) {
    addNote({ kind: "number", text: s.name + ", " + v.period + ": " + v.shown,
              html: escapeHtml(v.shown) + noteSup(v.note), source: v.note });
    btn.textContent = "Saved";
  }).catch(function (err) { toast(err.message); btn.disabled = false; });
}

/* ---- the web ---- */

function citation(page) {
  var when = page.published && /^\d{4}-\d{2}-\d{2}/.test(page.published) ? ", " + dateLong(page.published) : "";
  // A page sent from the writer's browser was read on the day it was sent.
  var read = page.sent_at ? new Date(page.sent_at * 1000).toISOString().slice(0, 10) : todayIso();
  return (page.author ? page.author + ", " : "") + (page.title || page.url) + ". " +
    (page.site ? page.site + when + ". " : "") + page.url + " (accessed " + dateLong(read) + ").";
}

function citePage(page) {
  insertAtCaret(noteSup(citation(page)));
  toast("Footnote added where your cursor was.");
}

function webTab(t) {
  var el = t.el;
  el.innerHTML =
    '<form class="dk-rs-form"><input type="url" aria-label="Page address" placeholder="https://…">' +
    '<button type="submit" class="dk-mini">Read</button></form><div class="dk-rs-res"></div>';
  var input = $("input", el);
  input.value = t.url;
  $("form", el).addEventListener("submit", function (e) {
    e.preventDefault();
    var url = input.value.trim();
    if (!url) { input.focus(); return; }
    t.url = url;
    t.clip = 0;
    t.title = hostOf(url);
    drawStrip();
    saveTabs();
    webRead(url, el);
  });
  if (t.clip) {
    $(".dk-rs-res", el).innerHTML = '<p class="muted">Opening the page…</p>';
    api("/research/clips/" + t.clip).then(function (page) { showPage(page, el); }).catch(function (err) {
      $(".dk-rs-res", el).innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>";
    });
  } else if (t.url) webRead(t.url, el);
  else input.focus();
}

function webRead(url, root) {
  var res = $(".dk-rs-res", root);
  if (!(url || "").trim()) return;
  res.innerHTML = '<p class="muted">Reading the page…</p>';
  send("POST", "/research/web/read", { url: url.trim() }).then(function (page) { showPage(page, root); })
    .catch(function (err) {
      var host = "";
      try { host = new URL(url.trim()).hostname.replace(/^www\./, ""); } catch (e) { /* not an address */ }
      res.innerHTML = '<p class="bad">' + escapeHtml(/answered HTTP 40[13]/.test(err.message) && host
        ? host + " does not let Plover read its pages." : err.message) + "</p>";
      // The site turned the server away (FT and WSJ always do): the writer can
      // still paste the article from their own browser.
      if (/answered HTTP|could not be reached|redirected too many/.test(err.message)) {
        res.insertAdjacentHTML("beforeend", pasteBoxHtml("If you can read it in your browser, paste it here.", url.trim(), true));
        wirePasteBox({ url: url.trim() }, root);
      }
    });
}

/* A page in the reader of one tab: read by address, or sent from the
   writer's browser (desk-clip.js). */
function showPage(page, root) {
  var res = $(".dk-rs-res", root);
  var t = tabOf(root);
  if (t) {
    t.title = page.site || page.title || hostOf(page.url);
    if (page.sent && page.id) t.clip = page.id;
    t.url = page.url || t.url;
    drawStrip();
    saveTabs();
  }
  var chars = (page.blocks || []).reduce(function (n, b) { return n + b.text.length; }, 0);
  res.innerHTML =
    '<div class="dk-reader">' +
    '<p class="dk-reader-src"><a href="' + escapeHtml(page.url) + '" target="_blank" rel="noopener noreferrer">' +
    escapeHtml(page.site || page.url) + "</a>" + (page.published ? " · " + escapeHtml(dateLong(page.published) || page.published) : "") + "</p>" +
    '<p class="dk-reader-title">' + escapeHtml(page.title || page.url) + "</p>" +
    (page.sent ? '<p class="dk-reader-sent">Your own copy' + (page.selection ? " of a passage" : "") + ", saved on " +
      escapeHtml(new Date(page.sent_at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })) + ".</p>"
      : (page.pdf ? "" : pasteBoxHtml(isPaidSite(page.url)
        ? siteName(page) + " articles need your subscription, so only the start came through."
        : (chars < 1500 ? "Only the start of this page came through." : ""), page.url,
        isPaidSite(page.url) || chars < 1500))) +
    '<div class="dk-reader-acts"><button type="button" class="dk-mini" data-rd="cite">Cite This Page</button>' +
    '<button type="button" class="dk-mini" data-rd="quote">Quote Selection</button>' +
    '<button type="button" class="dk-mini" data-rd="note">Save Selection to Notes</button>' +
    '<button type="button" class="dk-mini" data-rd="page">Save Page to Notes</button></div>' +
    '<div class="dk-reader-text">' +
    (page.pdf ? '<p>This is a PDF. Open it to read it; citing it works from here.</p>'
      : page.blocks.map(function (b) {
        return b.kind === "h" ? "<p><strong>" + escapeHtml(b.text) + "</strong></p>"
          : (b.kind === "quote" ? "<blockquote>" + escapeHtml(b.text) + "</blockquote>" : "<p>" + escapeHtml(b.text) + "</p>");
      }).join("") || "<p>No readable text was found on this page.</p>") +
    "</div></div>";
  if (!page.sent && !page.pdf) wirePasteBox(page, root);
  var textEl = $(".dk-reader-text", res);
  function selected() {
    var s = window.getSelection();
    return s && s.rangeCount && textEl.contains(s.anchorNode) ? s.toString().replace(/\s+/g, " ").trim() : "";
  }
  $('[data-rd="cite"]', res).addEventListener("click", function () { citePage(page); });
  $('[data-rd="quote"]', res).addEventListener("click", function () {
    var text = selected();
    if (!text) { toast("Select the passage to quote in the page text first."); return; }
    insertAfterCaretBlock({ type: "quote", html: escapeHtml(text) + noteSup(citation(page)) });
    toast("Quotation added with its footnote.");
  });
  $('[data-rd="note"]', res).addEventListener("click", function () {
    var text = selected();
    if (!text) { toast("Select a passage in the page text first."); return; }
    addNote({ kind: "quote", text: text, html: escapeHtml(text) + noteSup(citation(page)),
              source: citation(page), url: page.url });
  });
  $('[data-rd="page"]', res).addEventListener("click", function () {
    addNote({ kind: "page", text: page.title || page.url, html: noteSup(citation(page)),
              source: citation(page), url: page.url });
  });
}

/* ---- the plan ---- */

function pageBlocks() { return $all(".dk-block", blocksEl()).map(readBlock); }

function planInPage() { return !S.plan && hasPlanHeading(pageBlocks()); }

function planTab(el) {
  if (!S.plan && planInPage()) {
    var outline = parseOutline(pageBlocks());
    el.innerHTML = '<p class="dk-rt-empty">Your plan is in the page, waiting for your approval.</p>' +
      '<p class="hint">When you are happy with it, press Start Writing: the plan moves here, and its outline ' +
      "becomes the sections of your draft.</p>" +
      (outline.length
        ? '<div class="dk-plan-h"><span class="dk-rs-h">Sections It Will Make</span><span class="muted num">' +
          outline.length + "</span></div>" +
          '<ol class="dk-plan-secs">' + outline.map(function (x) {
            return '<li><span class="dk-plan-row"><span class="dk-plan-t">' + escapeHtml(x.heading) + "</span>" +
              (x.chart ? '<span class="dk-plan-st" title="' + escapeHtml(x.chart) + '">Chart</span>' : "") + "</span></li>";
          }).join("") + "</ol>"
        : '<p class="hint">No outline was found in the plan, so Start Writing will give you a blank page. ' +
          "To get sections, put a numbered list under a line that says \u201cOutline\u201d, one section per item.</p>") +
      '<div class="dk-plan-acts"><button type="button" class="btn btn-primary" data-startw="1">Start Writing</button>' +
      '<button type="button" class="btn" data-goplan="1">Go to the Plan</button></div>';
    $("[data-startw]", el).addEventListener("click", startWriting);
    $("[data-goplan]", el).addEventListener("click", function () {
      var h = $all('.dk-block[data-type="h2"]', blocksEl()).filter(function (b) { return /^plan$/i.test(readBlock(b).text); })[0];
      if (h) goToBlock(h.getAttribute("data-id"));
    });
    return;
  }
  if (!S.plan) {
    el.innerHTML = '<p class="dk-rt-empty">No plan yet.</p>' +
      '<p class="hint">A plan states the question the article answers, the answer you expect and the sections, ' +
      "each with its evidence and chart. Write one in the page under a heading “Plan” and press " +
      "Start Writing, or let the AI draft one for you to approve.</p>" +
      '<button type="button" class="btn" data-planai="1">Plan With AI</button>';
    $("[data-planai]", el).addEventListener("click", function () {
      aiRunFor("Plan this article from what is in the draft.", "Plan");
    });
    return;
  }
  var secs = sectionStatus();
  var done = secs.filter(function (s) { return s.status === "Done"; }).length;
  el.innerHTML =
    '<div class="dk-plan-meta"><span>Approved ' + escapeHtml(whenLong(S.plan.approved_at)) +
    (S.plan.approved_by ? " by " + escapeHtml(S.plan.approved_by) : "") + "</span>" +
    '<button type="button" class="linkish" data-planedit="1">Edit the Plan</button></div>' +
    (secs.length
      ? '<div class="dk-plan-h"><span class="dk-rs-h">Your Sections</span><span class="muted num">' + done + " of " +
        secs.length + " done</span></div>" +
        '<ol class="dk-plan-secs">' + secs.map(function (s) {
          return '<li><button type="button" data-goto="' + s.id + '"><span class="dk-plan-t">' + escapeHtml(s.text) +
            '</span><span class="dk-plan-st ' + s.cls + '">' + s.status + "</span></button></li>";
        }).join("") + "</ol>"
      : "") +
    '<p class="dk-rs-h">The Plan</p><div class="dk-plan-text">' + planHtml(S.plan.blocks) + "</div>" +
    '<div class="dk-plan-editbar" hidden><button type="button" class="btn btn-primary" data-plansave="1">Done Editing</button></div>';
  $all("[data-goto]", el).forEach(function (b) {
    b.addEventListener("click", function () { goToBlock(b.getAttribute("data-goto")); });
  });
  var text = $(".dk-plan-text", el);
  $("[data-planedit]", el).addEventListener("click", function () {
    text.contentEditable = "true";
    text.classList.add("editing");
    $(".dk-plan-editbar", el).hidden = false;
    $("[data-planedit]", el).hidden = true;
    text.focus();
  });
  $("[data-plansave]", el).addEventListener("click", function () {
    var blocks = htmlToBlocks(text.innerHTML).filter(function (b) {
      return ["p", "heading", "list", "quote", "table", "divider"].indexOf(b.type) !== -1;
    });
    if (!blocks.length) { toast("The plan cannot be empty."); return; }
    pushUndo("plan");
    S.plan = Object.assign({}, S.plan, { blocks: blocks.map(function (b) { b.id = b.id || bid(); return b; }) });
    changed();
    toast("Plan saved.", true);
    planTab(el);
  });
}

function whenLong(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/* The plan's blocks as reading text (the same cleaning as the draft). */
function planHtml(blocks) {
  return blocks.map(function (b) {
    if (b.type === "heading") return "<h" + (b.level === 3 ? 4 : 3) + ">" + escapeHtml(b.text) + "</h" + (b.level === 3 ? 4 : 3) + ">";
    if (b.type === "p") return "<p>" + cleanInline(b.html) + "</p>";
    if (b.type === "quote") return "<blockquote><p>" + cleanInline(b.html) + "</p></blockquote>";
    if (b.type === "list") {
      var tag = b.style === "number" ? "ol" : "ul";
      return "<" + tag + ">" + b.items.map(function (i) { return "<li>" + cleanInline(i) + "</li>"; }).join("") + "</" + tag + ">";
    }
    if (b.type === "table") {
      return "<table>" + b.rows.map(function (r) {
        return "<tr>" + r.map(function (c) { return "<td>" + cleanInline(c) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</table>";
    }
    if (b.type === "divider") return "<hr>";
    return "";
  }).join("");
}

/* ---- notes ---- */

var NOTE_FILTERS = [["all", "All"], ["number", "Numbers"], ["quote", "Quotes"], ["page", "Pages"], ["mine", "Mine"]];
var NOTE_KIND = { number: "Number", quote: "Quote", chart: "Chart", page: "Page", mine: "Mine" };

/* A note is filed under the section the writer was working in when they
   took it; one taken before any section exists is "Not Yet Sorted". */
function addNote(n) {
  n = Object.assign({ id: bid(), kind: "mine", text: "", html: "", source: "", url: "",
                      section: currentSection(), at: new Date().toISOString(), used: false }, n);
  var same = S.notes.filter(function (x) { return x.kind === n.kind && x.text === n.text && x.source === n.source; })[0];
  if (same) { toast("Already in Notes."); return same; }
  S.notes.unshift(n);
  changed();
  refreshResearch();
  toast("Saved to Notes" + (sectionName(n.section) ? " under “" + sectionName(n.section) + "”" : "") + ".");
  return n;
}

function currentSection() {
  var el = S.lastBlock && document.contains(S.lastBlock) ? S.lastBlock : null;
  while (el) {
    var m = S.models[el.getAttribute("data-id")];
    if (m && m.type === "heading" && m.level === 2) return m.id;
    el = el.previousElementSibling;
  }
  return "";
}

function sectionName(id) {
  var s = sectionStatus().filter(function (x) { return x.id === id; })[0];
  return s ? s.text : "";
}

function noteById(id) { return S.notes.filter(function (n) { return n.id === id; })[0]; }

function notesTab(el) {
  var f = RT.noteFilter;
  var secs = sectionStatus();
  var known = {};
  secs.forEach(function (s) { known[s.id] = true; });
  var shown = S.notes.filter(function (n) { return f === "all" || n.kind === f; });
  var groups = secs.map(function (s) {
    return { id: s.id, name: s.text, notes: shown.filter(function (n) { return n.section === s.id; }) };
  }).filter(function (g) { return g.notes.length; });
  var loose = shown.filter(function (n) { return !known[n.section]; });
  if (loose.length) groups.push({ id: "", name: "Not Yet Sorted", notes: loose });
  var counts = {};
  S.notes.forEach(function (n) { counts[n.kind] = (counts[n.kind] || 0) + 1; });
  el.innerHTML =
    '<form class="dk-note-add"><textarea rows="2" aria-label="Write a note" placeholder="Jot a thought, a to-do or a source…"></textarea>' +
    '<button type="submit" class="dk-mini">Add</button></form>' +
    '<div class="dk-seg dk-note-filter" role="group" aria-label="Show">' + NOTE_FILTERS.map(function (x) {
      var n = x[0] === "all" ? S.notes.length : (counts[x[0]] || 0);
      return '<button type="button" data-nf="' + x[0] + '" aria-pressed="' + (f === x[0]) + '">' + x[1] +
        ' <span class="num">' + n + "</span></button>";
    }).join("") + "</div>" +
    (groups.length ? groups.map(function (g) {
      return '<div class="dk-note-grp"><p class="dk-note-gh"><span>' + escapeHtml(g.name) + '</span><span class="muted num">' +
        g.notes.length + "</span></p><ul class=\"dk-notes\">" + g.notes.map(function (n) { return noteRow(n, secs, !g.id); }).join("") + "</ul></div>";
    }).join("")
      : '<p class="hint dk-note-empty">' + (S.notes.length ? "No notes of this kind."
        : "Nothing saved yet. In a research tab, use Save Value to Notes beside a number, or Save Selection to Notes on a page; " +
          "or jot a thought above. Each note keeps its source, and Insert puts it in the draft with its footnote.") + "</p>");
  var form = $(".dk-note-add", el);
  var ta = $("textarea", form);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = ta.value.trim();
    if (!v) { ta.focus(); return; }
    addNote({ kind: "mine", text: v, source: "" });
    notesTab(el);
    $("textarea", el).focus();
  });
  ta.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
  });
  $all("[data-nf]", el).forEach(function (b) {
    b.addEventListener("click", function () { RT.noteFilter = b.getAttribute("data-nf"); notesTab(el); });
  });
  $all(".dk-note", el).forEach(function (li) {
    var n = noteById(li.getAttribute("data-note"));
    li.addEventListener("dragstart", function (e) {
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/x-dk-note", n.id);
      e.dataTransfer.setData("text/plain", n.text);
      li.classList.add("dragging");
    });
    li.addEventListener("dragend", function () { li.classList.remove("dragging"); });
    var ins = $("[data-nins]", li);
    if (ins) ins.addEventListener("click", function () { insertNote(n); });
    $("[data-ndel]", li).addEventListener("click", function () {
      pushUndo("note");
      S.notes = S.notes.filter(function (x) { return x !== n; });
      changed();
      refreshResearch();
      notesTab(el);
      toast("Note deleted.", true);
    });
    var mv = $("[data-nmove]", li);
    if (mv) mv.addEventListener("change", function () {
      if (!mv.value) return;
      n.section = mv.value;
      changed();
      notesTab(el);
    });
  });
}

function noteRow(n, secs, loose) {
  var text = n.text || textOf(n.html || "");
  return '<li class="dk-note' + (n.used ? " used" : "") + '" draggable="true" data-note="' + n.id + '" title="Drag into the draft, or press Insert">' +
    '<span class="dk-note-k">' + (NOTE_KIND[n.kind] || "Note") + "</span>" +
    '<div class="dk-note-b"><div class="dk-note-t">' + escapeHtml(text) + "</div>" +
    ((n.source && n.kind !== "mine") || n.used
      ? '<div class="dk-note-s" title="' + escapeHtml(n.source) + '">' +
        escapeHtml(n.source.length > 90 ? n.source.slice(0, 89) + "…" : n.source) +
        (n.used ? (n.source ? " · " : "") + "In the draft" : "") + "</div>"
      : "") +
    (loose && secs.length ? '<select data-nmove aria-label="File under a section"><option value="">File under…</option>' +
      secs.map(function (s) { return '<option value="' + s.id + '">' + escapeHtml(s.text) + "</option>"; }).join("") + "</select>" : "") +
    "</div>" +
    '<span class="dk-note-acts"><button type="button" class="dk-mini" data-nins="1">Insert</button>' +
    '<button type="button" class="dk-note-x" data-ndel="1" aria-label="Delete this note" title="Delete">×</button></span></li>';
}

/* A note into the draft: a number or a citation where the cursor was, a
   quotation or a thought as a block of its own after it. `asBlock` when it
   was dropped between blocks rather than into text. */
function insertNote(n, asBlock) {
  var html = n.html || escapeHtml(n.text);
  if ((n.kind === "number" || n.kind === "page") && !asBlock) insertAtCaret(html);
  else if (n.kind === "quote") insertAfterCaretBlock({ type: "quote", html: html });
  else if (n.kind === "chart" && n.block) insertAfterCaretBlock(Object.assign({}, n.block, { id: bid() }));
  else insertAfterCaretBlock({ type: "p", html: n.kind === "mine" ? escapeHtml(n.text) : html });
  n.used = true;
  changed();
  refreshResearch();
}

/* A note dragged from the panel onto the draft goes in after the block it
   is dropped on (or at the end of that paragraph, for a number). */
function noteDragOver(e) {
  if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "text/x-dk-note") === -1) return false;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
  $all(".dk-drop-after").forEach(function (x) { x.classList.remove("dk-drop-after"); });
  var over = blockOf(e.target);
  if (over) over.classList.add("dk-drop-after");
  return true;
}

function noteDrop(e) {
  var id = e.dataTransfer && e.dataTransfer.getData("text/x-dk-note");
  if (!id) return false;
  e.preventDefault();
  $all(".dk-drop-after").forEach(function (x) { x.classList.remove("dk-drop-after"); });
  var n = noteById(id);
  if (!n) return true;
  var over = blockOf(e.target);
  var m = over && S.models[over.getAttribute("data-id")];
  var intoText = m && m.type === "p";
  if (over) {
    S.lastBlock = over;
    S.lastRange = null;
    var ed = intoText && over.querySelector("[contenteditable=true]");
    if (ed) {
      var r = document.createRange();
      r.selectNodeContents(ed);
      r.collapse(false);
      S.lastRange = r;
    }
  }
  insertNote(n, !intoText);
  toast("Note added to the draft.");
  return true;
}
