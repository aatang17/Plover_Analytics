/* Research Desk: the PloverResearch editor.

   A block editor. The article is a list of blocks — paragraph, heading,
   list, quote, table, chart, image, divider — each edited in place with its
   formatting visible, the way it will read. The server (app/research_doc.py)
   is the only thing that turns blocks into the published page, and it cleans
   every block to an allowlist, so nothing typed or pasted here can put a
   script on the site.

   Writing
     "/" in an empty line opens the block menu; "+" in the margin does too.
     "## ", "### ", "- ", "1. ", "> " and "---" at the start of a line turn it
     into a heading, list, quote or rule. Select text for bold, italic, link,
     code and footnote; Cmd/Ctrl+B, I and K work as usual. Enter starts a new
     paragraph, Shift+Enter a new line. Drag the ⋮⋮ handle to move a block, or
     Alt+Shift+Up/Down.
   Pasting
     From Word, Google Docs or a web page, headings, lists, tables, bold,
     italic and links come across; everything else is reduced to text. Cells
     copied from a spreadsheet become a table.
   Saving
     Automatic, a moment after you stop typing, and kept in this browser until
     the server confirms it. Every save carries the revision it was based on:
     if a co-writer saved in between, you are asked which version to keep, and
     nothing is overwritten silently.
   Publishing
     Checks the article, freezes every chart's data as it stands today, and
     puts a new numbered version on the site. Published versions never change.
*/
"use strict";

var ID = Number(new URLSearchParams(location.search).get("id"));
var ROOT = document.getElementById("dk-root");
var SAVE_DELAY = 1200;
var PRESENCE_EVERY = 30000;

var S = {
  article: null,        // the article as last loaded
  base: 0,              // the revision our edits are based on
  seq: 0,               // bumps on every change
  savedSeq: 0,          // the change the last good save covered
  saving: false,
  again: false,
  timer: null,
  retry: null,
  authors: [],
  datasets: null,
  models: {},           // block id -> block object (non-text fields)
  charts: {},           // block id -> obsChart handle
  undo: [],
  me: null,
  others: [],
  plan: null,           // the approved plan, kept beside the draft (Research › Plan)
  notes: [],            // the writer's research notes (Research › Notes)
  slotTarget: null,     // a planned chart that charts from the research panel go into
};

function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
function bid() { return Math.random().toString(16).slice(2, 12); }

/* ---------------------------------------------------------------- API */

function api(path, opts) {
  return fetch("/admin/api" + path, Object.assign({ credentials: "same-origin" }, opts || {}))
    .then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (r.status === 401) { signedOut(); }
        if (!r.ok) {
          var err = new Error(body.detail || "The request failed (" + r.status + ")");
          err.status = r.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
}

function send(method, path, body) {
  return api(path, {
    method: method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function signedOut() {
  showBanner("warn", "You were signed out. Sign in again in another tab " +
    '(<a href="write.html" target="_blank" rel="noopener">Writer Desk</a>), then come back: ' +
    "your changes are kept in this browser and will save.");
}

/* The article list this article belongs on, in the Writer Desk. */
function listUrl() {
  var pub = S.article && S.article.publication;
  return "write.html" + (pub ? "#/p/" + pub.id : "");
}

/* Where the publication's pages live: /research, or /p/<publication>. */
function pubBase() {
  var pub = S.article && S.article.publication;
  return pub ? pub.base : "/research";
}

function canDo(action) {
  return !!(S.article && S.article.can && S.article.can[action]);
}

/* ---------------------------------------------------------------- inline cleaning */

var INLINE_TAG = { STRONG: "strong", B: "strong", EM: "em", I: "em", CODE: "code",
                   SUP: "sup", SUB: "sub", A: "a" };
var DROP = /^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT|IFRAME|OBJECT|EMBED|SVG|MATH|HEAD|TITLE|META|LINK|IMG|VIDEO|AUDIO|CANVAS|INPUT|BUTTON|SELECT|TEXTAREA)$/;
var BLOCKISH = /^(P|DIV|LI|TR|TD|TH|H[1-6]|BLOCKQUOTE|SECTION|ARTICLE)$/;

function safeHref(href) {
  href = (href || "").trim();
  if (/^(https?:\/\/[^\s<>"]+|mailto:[^\s<>"]+|\/(?!\/)[^\s<>"]*|#[A-Za-z0-9_\-]*)$/i.test(href)) return href;
  if (/^[A-Za-z0-9_\-]+\.html([?#][^\s<>"]*)?$/.test(href)) return href;
  return null;
}

function noteSup(text) {
  return '<sup data-note="' + escapeHtml(text || "") + '" contenteditable="false" class="dk-fn"></sup>';
}

/* The same allowlist the server applies, so what sits in the editor is what
   will be stored. Parsed in an inert <template>: nothing in it runs or loads. */
function cleanInline(html) {
  var t = document.createElement("template");
  t.innerHTML = html || "";
  var out = [];
  (function walk(node) {
    var kids = node.childNodes;
    for (var i = 0; i < kids.length; i++) {
      var n = kids[i];
      if (n.nodeType === 3) {
        out.push(escapeHtml(n.nodeValue.replace(/[\r\n]+/g, " ")));
        continue;
      }
      if (n.nodeType !== 1) continue;
      var tag = n.tagName.toUpperCase();
      if (DROP.test(tag)) continue;
      if (tag === "BR") { out.push("<br>"); continue; }
      if (tag === "SUP" && n.hasAttribute("data-note")) {
        out.push(noteSup(n.getAttribute("data-note")));
        continue;
      }
      var style = n.getAttribute("style") || "";
      var open = [];
      var want = INLINE_TAG[tag];
      if (want === "a") {
        var href = safeHref(n.getAttribute("href"));
        if (href) { out.push('<a href="' + escapeHtml(href) + '">'); open.push("a"); }
      } else if (want === "strong" && /font-weight\s*:\s*(normal|[1-4]00)/i.test(style)) {
        // Google Docs wraps a whole paste in <b style="font-weight:normal">
      } else if (want) {
        out.push("<" + want + ">"); open.push(want);
      } else if (tag === "SPAN" || tag === "FONT") {
        if (/font-weight\s*:\s*(bold|[6-9]00)/i.test(style)) { out.push("<strong>"); open.push("strong"); }
        if (/font-style\s*:\s*italic/i.test(style)) { out.push("<em>"); open.push("em"); }
        if (/vertical-align\s*:\s*super/i.test(style)) { out.push("<sup>"); open.push("sup"); }
        if (/vertical-align\s*:\s*sub/i.test(style)) { out.push("<sub>"); open.push("sub"); }
      }
      walk(n);
      while (open.length) out.push("</" + open.pop() + ">");
      if (BLOCKISH.test(tag) && n.nextSibling) out.push(" ");
    }
  })(t.content);
  var html2 = out.join("").replace(/\s{2,}/g, " ").trim();
  for (var k = 0; k < 3; k++) html2 = html2.replace(/<(strong|em|code|sub|sup)><\/\1>/g, "");
  return html2.replace(/(<br>)+$/, "");
}

function textOf(html) {
  var t = document.createElement("template");
  t.innerHTML = html || "";
  return t.content.textContent || "";
}

/* ---------------------------------------------------------------- caret helpers */

function sel() { return window.getSelection(); }

function placeCaret(el, atEnd) {
  el.focus();
  var r = document.createRange();
  r.selectNodeContents(el);
  r.collapse(!atEnd);
  var s = sel();
  s.removeAllRanges();
  s.addRange(r);
}

function caretRange() {
  var s = sel();
  return s.rangeCount ? s.getRangeAt(0) : null;
}

function atStart(el) {
  var r = caretRange();
  if (!r || !r.collapsed || !el.contains(r.startContainer)) return false;
  var pre = document.createRange();
  pre.selectNodeContents(el);
  pre.setEnd(r.startContainer, r.startOffset);
  var frag = pre.cloneContents();
  return !frag.textContent.length && !(frag.querySelector && frag.querySelector("sup,br"));
}

function atEnd(el) {
  var r = caretRange();
  if (!r || !r.collapsed || !el.contains(r.endContainer)) return false;
  var post = document.createRange();
  post.selectNodeContents(el);
  post.setStart(r.endContainer, r.endOffset);
  var frag = post.cloneContents();
  return !frag.textContent.length && !(frag.querySelector && frag.querySelector("sup"));
}

/* Remove everything after the caret from `el` and return it as HTML. */
function splitAtCaret(el) {
  var r = caretRange();
  if (!r || !el.contains(r.startContainer)) return "";
  r.deleteContents();
  var tail = document.createRange();
  tail.setStart(r.startContainer, r.startOffset);
  tail.setEnd(el, el.childNodes.length);
  var box = document.createElement("div");
  box.appendChild(tail.extractContents());
  return cleanInline(box.innerHTML);
}

function caretAtMarker(root) {
  var m = root.querySelector("#dk-caret");
  if (!m) return;
  var r = document.createRange();
  r.setStartBefore(m);
  r.collapse(true);
  m.parentNode.removeChild(m);
  var s = sel();
  s.removeAllRanges();
  s.addRange(r);
}

function isEmpty(el) {
  return !(el.textContent || "").trim() && !el.querySelector("sup[data-note]");
}

/* ---------------------------------------------------------------- blocks */

var TEXT_TYPES = { p: 1, heading: 1, quote: 1, list: 1 };
var BLOCK_MENU = [
  { key: "p", label: "Text", hint: "A paragraph", make: function () { return { type: "p", html: "" }; } },
  { key: "h2", label: "Heading", hint: "Section heading", make: function () { return { type: "heading", level: 2, text: "" }; } },
  { key: "h3", label: "Subheading", hint: "Smaller heading", make: function () { return { type: "heading", level: 3, text: "" }; } },
  { key: "bullet", label: "Bulleted List", hint: "- item", make: function () { return { type: "list", style: "bullet", items: [""] }; } },
  { key: "number", label: "Numbered List", hint: "1. item", make: function () { return { type: "list", style: "number", items: [""] }; } },
  { key: "quote", label: "Quote", hint: "Block quotation", make: function () { return { type: "quote", html: "" }; } },
  { key: "chart", label: "Chart From Plover Data", hint: "Live series, frozen on publication", make: function () { return { type: "chart", dataset: "", series: [], measure: "index", start: "", end: "", title: "", note: "", _open: true }; } },
  { key: "page", label: "Chart From a Plover Page", hint: "Any chart on the site, company pages too", make: function () { return { type: "snapshot", url: "", chart: 0, title: "", note: "" }; } },
  { key: "data", label: "Chart From Your Own Data", hint: "Paste from Excel, Google Sheets or a CSV", make: function () { return { type: "datachart", rows: [], kind: "line", unit: "", title: "", note: "", source: "" }; } },
  { key: "table", label: "Table", hint: "Rows and columns", make: function () { return { type: "table", rows: [["", "", ""], ["", "", ""], ["", "", ""]], header: true, align: ["auto", "auto", "auto"], caption: "", source: "" }; } },
  { key: "image", label: "Image", hint: "PNG, JPEG or WebP", make: function () { return { type: "image", media: "", ext: "png", alt: "", caption: "", source: "", pending: "" }; } },
  { key: "divider", label: "Divider", hint: "A rule", make: function () { return { type: "divider" }; } },
];

function blocksEl() { return document.getElementById("dk-blocks"); }

function blockOf(node) { return node && node.closest ? node.closest(".dk-block") : null; }

function makeBlock(b) {
  b = Object.assign({}, b);
  if (!b.id) b.id = bid();
  var el = document.createElement("div");
  el.className = "dk-block";
  el.setAttribute("data-id", b.id);
  el.setAttribute("data-type", b.type === "heading" ? "h" + b.level : b.type);
  el.innerHTML =
    '<div class="dk-gutter">' +
    '<button type="button" class="dk-add" title="Add a block below" aria-label="Add a block below">+</button>' +
    '<button type="button" class="dk-handle" draggable="true" title="Drag to move, click for options" aria-label="Block options">⋮⋮</button>' +
    "</div>" +
    '<div class="dk-content"></div>';
  var c = el.lastChild;
  S.models[b.id] = b;
  if (b.type === "p" || b.type === "quote") {
    c.innerHTML = '<div class="dk-text dk-' + b.type + '" contenteditable="true" role="textbox" ' +
      'aria-multiline="true" data-ph="' + (b.type === "p" ? "Write, or type / for a block" : "Quotation") +
      '"></div>';
    c.firstChild.innerHTML = cleanInline(b.html);
  } else if (b.type === "heading") {
    c.innerHTML = '<div class="dk-text dk-h' + b.level + '" contenteditable="true" role="textbox" ' +
      'data-ph="' + (b.level === 2 ? "Heading" : "Subheading") + '"></div>';
    c.firstChild.textContent = b.text || "";
  } else if (b.type === "list") {
    var tag = b.style === "number" ? "ol" : "ul";
    c.innerHTML = "<" + tag + ' class="dk-text dk-list" contenteditable="true" role="textbox" ' +
      'aria-multiline="true"></' + tag + ">";
    c.firstChild.innerHTML = (b.items && b.items.length ? b.items : [""]).map(function (i) {
      return "<li>" + (cleanInline(i) || "<br>") + "</li>";
    }).join("");
  } else if (b.type === "divider") {
    c.innerHTML = '<hr class="dk-hr" tabindex="0" aria-label="Divider">';
  } else if (b.type === "table") {
    renderTable(c, b);
  } else if (b.type === "chart") {
    renderChartBlock(c, b);
  } else if (b.type === "image") {
    renderImageBlock(c, b);
  } else if (b.type === "snapshot") {
    renderSnapshotBlock(c, b);
  } else if (b.type === "datachart") {
    renderDataChart(c, b);
  } else if (b.type === "placeholder") {
    renderPlaceholder(c, b);
  }
  // A figure gets a Delete button in plain sight: the ⋮⋮ menu is not where
  // anyone looks to remove a chart. It sits outside .dk-content, which the
  // figure's own renderers rewrite.
  if (FIGURE_TYPES[b.type]) {
    el.insertAdjacentHTML("beforeend", '<button type="button" class="dk-mini dk-del">Delete</button>');
  }
  return el;
}

var FIGURE_TYPES = { chart: true, image: true, snapshot: true, datachart: true, table: true };

/* Delete a block, say which, and offer Undo. */
function deleteBlock(el) {
  var lab = el.querySelector(".dk-fig-label");
  var name = lab && lab.textContent ? lab.textContent : "Block";
  pushUndo("delete");
  var prev = el.previousElementSibling;
  removeBlock(el);
  if (prev) focusBlock(prev, true);
  toast(name + " deleted.", true);
}

function readBlock(el) {
  var id = el.getAttribute("data-id");
  var m = S.models[id] || {};
  var t = m.type;
  var c = el.querySelector(".dk-content");
  if (t === "p" || t === "quote") return { id: id, type: t, html: cleanInline(c.firstChild.innerHTML) };
  if (t === "heading") return { id: id, type: t, level: m.level, text: (c.firstChild.textContent || "").replace(/\s+/g, " ").trim() };
  if (t === "list") {
    return { id: id, type: t, style: m.style, items: $all("li", c).map(function (li) { return cleanInline(li.innerHTML); }) };
  }
  if (t === "divider") return { id: id, type: t };
  if (t === "placeholder") return { id: id, type: t, role: m.role, text: m.text || "" };
  if (t === "table") {
    var rows = $all("tr", c).map(function (tr) {
      return $all("td", tr).map(function (td) { return cleanInline(td.innerHTML); });
    });
    return { id: id, type: t, rows: rows, header: m.header, align: m.align.slice(0, rows[0] ? rows[0].length : 0),
             caption: m.caption || "", source: m.source || "" };
  }
  if (t === "chart") {
    return { id: id, type: t, dataset: m.dataset, series: m.series.slice(), measure: m.measure,
             start: m.start, end: m.end, title: m.title, note: m.note };
  }
  if (t === "datachart") {
    return { id: id, type: t, rows: m.rows.map(function (r) { return r.slice(); }), kind: m.kind,
             unit: m.unit, title: m.title, note: m.note, source: m.source, origin: m.origin || "author" };
  }
  if (t === "image") {
    return { id: id, type: t, media: m.media, ext: m.ext, alt: m.alt, caption: m.caption,
             source: m.source, width: m.width, height: m.height, pending: m.pending, link: m.link || "" };
  }
  if (t === "snapshot") {
    return { id: id, type: t, url: m.url || "", chart: m.chart || 0, title: m.title || "", note: m.note || "",
             kind: m.kind || "", cfg: m.cfg || null, page_title: m.page_title || "", source: m.source || "",
             calc: m.calc || "", captured_at: m.captured_at || "" };
  }
  return { id: id, type: "p", html: "" };
}

function serialize() {
  return {
    title: ($("#dk-title").value || "").replace(/\s+/g, " ").trim(),
    dek: ($("#dk-dek").value || "").replace(/\s+/g, " ").trim(),
    summary: ($("#dk-summary").value || "").replace(/\s+/g, " ").trim(),
    slug: ($("#dk-slug").value || "").trim(),
    authors: $all('input[name="dk-author"]').filter(function (x) { return x.checked; })
      .map(function (x) { return Number(x.value); }),
    market: ($("#dk-market") || {}).value || "",
    topics: $all('input[name="dk-topic"]').filter(function (x) { return x.checked; })
      .map(function (x) { return x.value; }),
    lead: ($("#dk-lead") || {}).value || "",
    feature: !!($("#dk-feature") || {}).checked,
    blocks: $all(".dk-block", blocksEl()).map(readBlock),
    plan: S.plan,
    notes: S.notes.map(function (n) { return Object.assign({}, n); }),
  };
}

function insertBlock(b, after, focusIt) {
  var el = makeBlock(b);
  var box = blocksEl();
  if (after && after.parentNode === box) box.insertBefore(el, after.nextSibling);
  else box.appendChild(el);
  afterStructure();
  if (focusIt !== false) focusBlock(el, false);
  return el;
}

function replaceBlock(oldEl, b, caretEnd) {
  var el = makeBlock(b);
  oldEl.parentNode.replaceChild(el, oldEl);
  disposeChart(oldEl.getAttribute("data-id"));
  afterStructure();
  focusBlock(el, caretEnd);
  return el;
}

function removeBlock(el) {
  var id = el.getAttribute("data-id");
  disposeChart(id);
  el.parentNode.removeChild(el);
  if (!blocksEl().children.length) insertBlock({ type: "p", html: "" }, null, true);
  afterStructure();
}

function focusBlock(el, end) {
  if (!el) return;
  var editable = el.querySelector("[contenteditable=true]");
  if (editable) {
    if (editable.tagName === "UL" || editable.tagName === "OL") {
      var lis = editable.querySelectorAll("li");
      placeCaret(end ? lis[lis.length - 1] : lis[0], end);
    } else placeCaret(editable, end);
    return;
  }
  // an empty chart starts in its series search, ready to type
  var m = S.models[el.getAttribute("data-id")] || {};
  var f = (m.type === "chart" && !m.series.length && el.querySelector('[data-f="q"]')) ||
    (m.type === "datachart" && !m.rows.length && el.querySelector('[data-f="cells"]')) ||
    el.querySelector("input, .dk-hr, button");
  if (f) f.focus();
}

function textBlockBefore(el) {
  var p = el.previousElementSibling;
  while (p && !TEXT_TYPES[(S.models[p.getAttribute("data-id")] || {}).type]) p = p.previousElementSibling;
  return p;
}

function textBlockAfter(el) {
  var n = el.nextElementSibling;
  while (n && !TEXT_TYPES[(S.models[n.getAttribute("data-id")] || {}).type]) n = n.nextElementSibling;
  return n;
}

function afterStructure() {
  updateCounts();
  planBar();
  changed();
}

/* Convert a text block to another text type, keeping its content. */
function convert(el, key) {
  var cur = readBlock(el);
  var html = cur.type === "list" ? cur.items.join("<br>") :
    cur.type === "heading" ? escapeHtml(cur.text) : cur.html;
  var made = BLOCK_MENU.filter(function (m) { return m.key === key; })[0].make();
  made.id = cur.id;
  if (made.type === "p" || made.type === "quote") made.html = html;
  else if (made.type === "heading") made.text = textOf(html.replace(/<br>/g, " "));
  else if (made.type === "list") {
    made.items = cur.type === "list" ? cur.items : html.split(/<br>/).filter(function (x) { return x.trim(); });
    if (!made.items.length) made.items = [""];
  }
  return replaceBlock(el, made, true);
}

/* ---------------------------------------------------------------- undo (structure) */

function pushUndo(label) {
  S.undo.push({ label: label, draft: serialize() });
  if (S.undo.length > 50) S.undo.shift();
}

function undo() {
  var snap = S.undo.pop();
  if (!snap) return;
  renderDraft(snap.draft);
  changed();
  toast("Restored.");
}

/* ---------------------------------------------------------------- tables */

function renderTable(c, b) {
  var rows = b.rows && b.rows.length ? b.rows : [[""]];
  var width = rows[0].length;
  b.align = (b.align || []).slice(0, width);
  while (b.align.length < width) b.align.push("auto");
  c.innerHTML =
    '<div class="dk-fig-label dk-table-label"></div>' +
    '<div class="dk-table-tools" role="toolbar" aria-label="Table">' +
    '<button type="button" class="dk-mini" data-t="row">Add Row</button>' +
    '<button type="button" class="dk-mini" data-t="col">Add Column</button>' +
    '<button type="button" class="dk-mini" data-t="delrow">Delete Row</button>' +
    '<button type="button" class="dk-mini" data-t="delcol">Delete Column</button>' +
    '<label class="dk-mini-l">Column <select data-t="align">' +
    '<option value="auto">Auto Align</option><option value="left">Left</option>' +
    '<option value="right">Right</option><option value="center">Centre</option></select></label>' +
    '<label class="dk-mini-l"><input type="checkbox" data-t="header"' + (b.header ? " checked" : "") +
    "> Header Row</label></div>" +
    '<div class="dk-table-wrap"><table class="dk-grid' + (b.header ? " has-head" : "") + '"><tbody>' +
    rows.map(function (r) {
      return "<tr>" + r.map(function (cell) {
        return '<td contenteditable="true">' + cleanInline(cell) + "</td>";
      }).join("") + "</tr>";
    }).join("") + "</tbody></table></div>" +
    '<div class="dk-fig-fields">' +
    '<label>Caption <input type="text" data-f="caption" maxlength="200" placeholder="What the table shows"></label>' +
    '<label><span>Source <span class="req" aria-hidden="true">*</span></span><input type="text" data-f="source" maxlength="400" placeholder="e.g. Japanese Bankers Association, FY2025 results"></label>' +
    "</div>";
  $('[data-f="caption"]', c).value = b.caption || "";
  $('[data-f="source"]', c).value = b.source || "";
  applyAlign(c, b);
}

function applyAlign(c, b) {
  $all("tr", c).forEach(function (tr) {
    $all("td", tr).forEach(function (td, i) {
      td.setAttribute("data-align", b.align[i] || "auto");
    });
  });
}

function cellPos(td) {
  var tr = td.parentNode;
  return { r: Array.prototype.indexOf.call(tr.parentNode.children, tr),
           c: Array.prototype.indexOf.call(tr.children, td) };
}

function tableOp(blockEl, op, value) {
  var m = S.models[blockEl.getAttribute("data-id")];
  var c = blockEl.querySelector(".dk-content");
  var tbody = $("tbody", c);
  var focused = S.cell && blockEl.contains(S.cell) ? S.cell : $("td", c);
  var pos = cellPos(focused);
  var width = tbody.rows[0].cells.length;
  if (op === "row") {
    var tr = document.createElement("tr");
    for (var i = 0; i < width; i++) tr.insertAdjacentHTML("beforeend", '<td contenteditable="true"></td>');
    tbody.insertBefore(tr, tbody.rows[pos.r].nextSibling);
    focusCell(tr.cells[pos.c]);
  } else if (op === "col") {
    if (width >= 14) { toast("A table can have up to 14 columns."); return; }
    $all("tr", tbody).forEach(function (row) {
      row.insertBefore(Object.assign(document.createElement("td"), { contentEditable: "true" }),
                       row.cells[pos.c].nextSibling);
    });
    m.align.splice(pos.c + 1, 0, "auto");
    focusCell(tbody.rows[pos.r].cells[pos.c + 1]);
  } else if (op === "delrow") {
    if (tbody.rows.length <= 1) { toast("A table needs at least one row."); return; }
    tbody.removeChild(tbody.rows[pos.r]);
    focusCell(tbody.rows[Math.min(pos.r, tbody.rows.length - 1)].cells[pos.c]);
  } else if (op === "delcol") {
    if (width <= 1) { toast("A table needs at least one column."); return; }
    $all("tr", tbody).forEach(function (row) { row.removeChild(row.cells[pos.c]); });
    m.align.splice(pos.c, 1);
    focusCell(tbody.rows[pos.r].cells[Math.min(pos.c, width - 2)]);
  } else if (op === "align") {
    m.align[pos.c] = value;
  } else if (op === "header") {
    m.header = !!value;
    $("table", c).classList.toggle("has-head", m.header);
  }
  applyAlign(c, m);
  changed();
}

function focusCell(td) {
  if (!td) return;
  S.cell = td;
  placeCaret(td, true);
}

/* Spreadsheet cells pasted into a table: fill from the focused cell, growing
   the table as needed. */
function pasteGrid(td, text) {
  var lines = text.replace(/\r/g, "").replace(/\n$/, "").split("\n").map(function (l) { return l.split("\t"); });
  var blockEl = blockOf(td);
  var m = S.models[blockEl.getAttribute("data-id")];
  var tbody = td.closest("tbody");
  var pos = cellPos(td);
  var need = pos.c + Math.max.apply(null, lines.map(function (l) { return l.length; }));
  if (need > 14) { toast("A table can have up to 14 columns."); return; }
  while (tbody.rows[0].cells.length < need) {
    $all("tr", tbody).forEach(function (row) {
      row.appendChild(Object.assign(document.createElement("td"), { contentEditable: "true" }));
    });
    m.align.push("auto");
  }
  lines.forEach(function (cells, i) {
    var r = pos.r + i;
    while (tbody.rows.length <= r) {
      var tr = document.createElement("tr");
      for (var k = 0; k < tbody.rows[0].cells.length; k++) {
        tr.appendChild(Object.assign(document.createElement("td"), { contentEditable: "true" }));
      }
      tbody.appendChild(tr);
    }
    cells.forEach(function (v, j) { tbody.rows[r].cells[pos.c + j].textContent = v.trim(); });
  });
  applyAlign(blockEl.querySelector(".dk-content"), m);
  changed();
}

/* ---------------------------------------------------------------- charts */

/* A chart block is the chart itself with one row of controls above it: the
   series as chips, a search box to add one, and buttons for the measure and
   the date range. Nothing is hidden behind a settings panel, and nothing has
   to be chosen before the search: an empty chart searches every dataset, and
   the first series picked fixes the dataset the rest come from. */

var MEASURES = [
  { key: "index", label: "As Published", full: "As published" },
  { key: "yoy", label: "YoY %", full: "Year-on-year % change" },
  { key: "mom", label: "MoM %", full: "Month-on-month % change" },
  { key: "ann3m", label: "3M Ann. %", full: "3-month annualised % change" },
];

var RANGES = [
  { key: "1", label: "1Y" }, { key: "3", label: "3Y" }, { key: "5", label: "5Y" },
  { key: "10", label: "10Y" }, { key: "all", label: "All" }, { key: "custom", label: "Dates" },
];

function loadDatasets() {
  if (S.datasets) return Promise.resolve(S.datasets);
  return fetch("/api/v1/catalog/datasets").then(function (r) { return r.json(); }).then(function (d) {
    S.datasets = d.datasets.slice();
    return S.datasets;
  });
}

function datasetName(slug) {
  var d = (S.datasets || []).filter(function (x) { return x.slug === slug; })[0];
  return d ? d.title : slug;
}

/* The first month of a range ending this month: 5 years from October 2026 is 2021-10. */
function rangeStart(years) {
  var d = new Date();
  return (d.getFullYear() - Number(years)) + "-" + String(d.getMonth() + 1).padStart(2, "0");
}

function rangeKey(b) {
  if (!b.start && !b.end) return "all";
  if (!b.end) {
    for (var i = 0; i < RANGES.length; i++) {
      if (/^\d+$/.test(RANGES[i].key) && b.start === rangeStart(RANGES[i].key)) return RANGES[i].key;
    }
  }
  return "custom";
}

function renderChartBlock(c, b) {
  c.innerHTML =
    '<div class="dk-fig-label dk-chart-label"></div>' +
    '<input type="text" class="dk-chart-title" data-f="title" maxlength="200" placeholder="Chart title">' +
    '<div class="dk-series-row"><div class="dk-chips"></div>' +
    '<div class="dk-search"><input type="text" data-f="q" placeholder="Search any series, e.g. core CPI, 10-year yield, births" autocomplete="off" aria-label="Search series">' +
    '<div class="dk-results" hidden></div></div></div>' +
    '<div class="dk-chart-opts">' +
    '<div class="dk-opt" role="group" aria-label="Measure">' + MEASURES.map(function (m) {
      return '<button type="button" data-measure="' + m.key + '" title="' + m.full + '">' + m.label + "</button>";
    }).join("") + "</div>" +
    '<div class="dk-opt" role="group" aria-label="Date range">' + RANGES.map(function (r) {
      return '<button type="button" data-range="' + r.key + '">' + r.label + "</button>";
    }).join("") + "</div>" +
    '<span class="dk-dates" hidden><label>From <input type="text" data-f="start" placeholder="YYYY-MM" maxlength="10"></label>' +
    '<label>to <input type="text" data-f="end" placeholder="latest" maxlength="10"></label></span>' +
    "</div>" +
    '<div class="dk-chart-plot" aria-label="Chart preview"></div>' +
    '<p class="source-line dk-chart-src"></p>' +
    '<input type="text" class="dk-chart-note" data-f="note" maxlength="400" placeholder="Add a note under the chart (optional)">' +
    '<p class="dk-chart-err" hidden></p>';
  $('[data-f="title"]', c).value = b.title || "";
  $('[data-f="start"]', c).value = b.start || "";
  $('[data-f="end"]', c).value = b.end || "";
  $('[data-f="note"]', c).value = b.note || "";
  delete b._open;
  if (rangeKey(b) === "custom") $(".dk-dates", c).hidden = false;
  loadDatasets().then(function () { chartControls(c, b); });
  chartControls(c, b);
  drawChart(c, b);
}

/* Chips, search hint and pressed buttons, from the model. */
function chartControls(c, b) {
  renderChips(c, b);
  var has = b.series.length > 0;
  var q = $('[data-f="q"]', c);
  q.placeholder = has
    ? (b.series.length >= 6 ? "Six series is the most a chart can show" : "Add another series")
    : "Search any series, e.g. core CPI, 10-year yield, births";
  q.disabled = b.series.length >= 6;
  $(".dk-chart-opts", c).hidden = !has;
  $all("[data-measure]", c).forEach(function (x) {
    x.setAttribute("aria-pressed", String(x.getAttribute("data-measure") === (b.measure || "index")));
  });
  var rk = rangeKey(b);
  var dates = $(".dk-dates", c);
  $all("[data-range]", c).forEach(function (x) {
    var k = x.getAttribute("data-range");
    x.setAttribute("aria-pressed", String(k === "custom" ? !dates.hidden : k === rk && dates.hidden));
  });
}

function renderChips(c, b) {
  var names = b._names || {};
  $(".dk-chips", c).innerHTML = b.series.map(function (code) {
    return '<span class="dk-chip"><span>' + escapeHtml(names[code] || code) + "</span>" +
      '<button type="button" data-rm="' + escapeHtml(code) + '" aria-label="Remove ' +
      escapeHtml(names[code] || code) + '">×</button></span>';
  }).join("");
}

/* The title follows the series until the writer types their own. */
function autoTitle(b) {
  var names = b._names || {};
  return b.series.map(function (s) { return names[s] || s; }).join(", ").slice(0, 200);
}

function retitle(c, b, before) {
  if (b.title && b.title !== before) return;
  b.title = autoTitle(b);
  $('[data-f="title"]', c).value = b.title;
}

var searchTimer = null;
function searchSeries(c, b, q) {
  var res = $(".dk-results", c);
  clearTimeout(searchTimer);
  if (!b.dataset && !q.trim()) {
    res.hidden = false;
    res.innerHTML = '<p class="muted">Type a name, e.g. core CPI or guest nights.</p>';
    return;
  }
  searchTimer = setTimeout(function () {
    // an empty chart searches everything; after the first pick, that dataset only
    var url = b.dataset
      ? "/api/v1/" + encodeURIComponent(b.dataset) + "/series?q=" + encodeURIComponent(q || "")
      : "/admin/api/research/data/search?q=" + encodeURIComponent(q);
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
      })
      .then(function (r) {
        if ($('[data-f="q"]', c).value !== q) return;   // typed on while loading
        res.hidden = false;
        if (!r.ok) {
          res.innerHTML = '<p class="muted">' + escapeHtml(r.status === 422
            ? "This dataset is large: type part of a name or code to search."
            : (r.body.detail || "Search failed.")) + "</p>";
          return;
        }
        var list = b.dataset ? (r.body.series || r.body.items || r.body || []) : (r.body.series || []);
        if (!Array.isArray(list)) list = [];
        list = list.filter(function (s) { return b.series.indexOf(s.code) === -1; });
        res.innerHTML = list.slice(0, 40).map(function (s) {
          var name = s.name_en || s.name || s.code;
          var ds = s.dataset || b.dataset;
          var where = b.dataset ? s.code : (s.dataset_name || s.dataset);
          return '<button type="button" class="dk-result" data-code="' + escapeHtml(s.code) + '" data-dataset="' +
            escapeHtml(ds) + '" data-name="' + escapeHtml(name) + '"><span>' + escapeHtml(name) +
            '</span><span class="muted dk-result-where">' + escapeHtml(where) + "</span></button>";
        }).join("") || '<p class="muted">' + (b.dataset
          ? "No series in " + escapeHtml(datasetName(b.dataset)) + " match. To chart another dataset, start a new chart."
          : "No series match. Try a shorter name, e.g. CPI.") + "</p>";
      }).catch(function () {
        res.hidden = false;
        res.innerHTML = '<p class="muted">Search failed. Check the connection and try again.</p>';
      });
  }, 220);
}

function pickSeries(c, b, pick) {
  var code = pick.getAttribute("data-code");
  var before = autoTitle(b);
  if (!b.dataset) b.dataset = pick.getAttribute("data-dataset");
  if (b.series.indexOf(code) === -1) {
    if (b.series.length >= 6) { toast("A chart can show up to six series."); return; }
    b.series.push(code);
    b._names = b._names || {};
    b._names[code] = pick.getAttribute("data-name");
  }
  $(".dk-results", c).hidden = true;
  var q = $('[data-f="q"]', c);
  q.value = "";
  retitle(c, b, before);
  chartControls(c, b);
  drawChart(c, b);
  if (!q.disabled) q.focus();
}

function removeSeries(c, b, code) {
  var before = autoTitle(b);
  b.series = b.series.filter(function (s) { return s !== code; });
  if (!b.series.length) b.dataset = "";   // an empty chart searches everything again
  retitle(c, b, before);
  chartControls(c, b);
  drawChart(c, b);
}

function setRange(c, b, key) {
  var dates = $(".dk-dates", c);
  if (key === "custom") {
    dates.hidden = false;
    chartControls(c, b);
    $('[data-f="start"]', c).focus();
    return;
  }
  dates.hidden = true;
  b.start = key === "all" ? "" : rangeStart(key);
  b.end = "";
  $('[data-f="start"]', c).value = b.start;
  $('[data-f="end"]', c).value = "";
  chartControls(c, b);
  drawChart(c, b);
}

function chartQuery(b) {
  var q = "series=" + encodeURIComponent(b.series.join(",")) + "&measure=" + b.measure;
  if (b.start) q += "&start=" + encodeURIComponent(b.start);
  if (b.end) q += "&end=" + encodeURIComponent(b.end);
  return "/api/v1/" + encodeURIComponent(b.dataset) + "/observations?" + q;
}

function disposeChart(id) {
  if (S.charts[id]) { S.charts[id].dispose(); delete S.charts[id]; }
}

function drawChart(c, b) {
  var plot = $(".dk-chart-plot", c);
  var err = $(".dk-chart-err", c);
  var src = $(".dk-chart-src", c);
  disposeChart(b.id);
  plot.innerHTML = "";
  err.hidden = true;
  src.textContent = "";
  if (!b.dataset || !b.series.length) {
    plot.classList.add("empty");
    plot.innerHTML = '<p class="muted">Type a series name in the box above. The chart appears here.</p>';
    return;
  }
  plot.classList.remove("empty");
  plot.innerHTML = '<div class="skeleton" style="height:100%"></div>';
  var want = chartQuery(b);
  fetch(want).then(function (r) {
    return r.json().then(function (body) { return { ok: r.ok, body: body }; });
  }).then(function (r) {
    if (chartQuery(b) !== want) return;   // changed while loading
    plot.innerHTML = "";
    if (!r.ok) {
      err.hidden = false;
      err.textContent = r.body.detail || "This chart could not be drawn.";
      return;
    }
    var names = {};
    r.body.series.forEach(function (s) { names[s.code] = s.name_en || s.code; });
    var wasAuto = !b.title || b.title === autoTitle(b);
    b._names = names;
    if (wasAuto) retitle(c, b, b.title);
    chartControls(c, b);
    if (!r.body.series.some(function (s) { return s.points.some(function (p) { return p[1] !== null; }); })) {
      err.hidden = false;
      err.textContent = "No values in the chosen date range.";
      return;
    }
    r.body.credit = "";
    S.charts[b.id] = researchChart.draw(plot, b, r.body);
    src.textContent = researchChart.sourceLine(r.body) +
      " Publishing freezes the data as it stands that day.";
  }).catch(function () {
    plot.innerHTML = "";
    err.hidden = false;
    err.textContent = "The data could not be loaded. Check the connection and try again.";
  });
}

/* ---------------------------------------------------------------- own data */

/* A chart of the writer's own numbers: paste cells from a spreadsheet, a CSV
   or another publisher's download, give the source, done. The cells are kept
   as pasted; researchChart (research.js) reads and draws them, here and on
   the published page alike. */

/* Pasted text as rows of cells: tabs from a spreadsheet, else commas or
   semicolons from a CSV, with quoted cells kept whole. */
function parseCells(text) {
  text = (text || "").replace(/\r\n?/g, "\n").replace(/\n+$/, "");
  if (!text.trim()) return [];
  var sep = text.indexOf("\t") !== -1 ? "\t" : (text.split("\n")[0].split(";").length > text.split("\n")[0].split(",").length ? ";" : ",");
  var rows = [[]], cell = "", quoted = false;
  for (var i = 0; i < text.length; i++) {
    var ch = text.charAt(i);
    if (quoted) {
      if (ch === '"' && text.charAt(i + 1) === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && !cell) quoted = true;
    else if (ch === sep) { rows[rows.length - 1].push(cell.trim()); cell = ""; }
    else if (ch === "\n") { rows[rows.length - 1].push(cell.trim()); rows.push([]); cell = ""; }
    else cell += ch;
  }
  rows[rows.length - 1].push(cell.trim());
  return rows.filter(function (r) { return r.some(function (c) { return c; }); });
}

function cellsText(rows) {
  return (rows || []).map(function (r) { return r.join("\t"); }).join("\n");
}

function renderDataChart(c, b) {
  var has = (b.rows || []).length > 0;
  c.innerHTML =
    '<div class="dk-fig-label dk-chart-label"></div>' +
    '<input type="text" class="dk-chart-title" data-f="title" maxlength="200" placeholder="Chart title">' +
    '<div class="dk-paste"' + (has ? " hidden" : "") + '>' +
    '<textarea data-f="cells" rows="6" spellcheck="false" aria-label="Your numbers" placeholder="' +
    'Paste cells from Excel, Google Sheets or a CSV.\nFirst column: dates or labels. Then up to six columns of numbers.\n' +
    'First row: series names, e.g.\nDate\tUS\tJapan\n2025-01\t3.0\t4.0"></textarea></div>' +
    '<div class="dk-chart-opts dk-data-opts"' + (has ? "" : " hidden") + '>' +
    '<div class="dk-opt" role="group" aria-label="Chart type">' +
    '<button type="button" data-kind="line">Line</button><button type="button" data-kind="bar">Bars</button></div>' +
    '<label class="dk-unit">Unit <input type="text" data-f="unit" maxlength="40" placeholder="e.g. %, US$ bn"></label>' +
    '<button type="button" class="dk-mini" data-editcells>Edit Data</button>' +
    '<span class="dk-data-sum muted"></span></div>' +
    '<div class="dk-chart-plot" aria-label="Chart preview"></div>' +
    '<p class="dk-chart-err" hidden></p>' +
    '<label class="dk-src"><span>Source <span class="req" aria-hidden="true">*</span></span>' +
    '<input type="text" data-f="source" maxlength="400" placeholder="Who published the numbers, e.g. IMF World Economic Outlook, April 2026"></label>' +
    '<input type="text" class="dk-chart-note" data-f="note" maxlength="400" placeholder="Add a note under the chart (optional)">';
  $('[data-f="title"]', c).value = b.title || "";
  $('[data-f="cells"]', c).value = cellsText(b.rows);
  $('[data-f="unit"]', c).value = b.unit || "";
  $('[data-f="source"]', c).value = b.source || "";
  $('[data-f="note"]', c).value = b.note || "";
  // a paste puts the chart first: the cells fold away behind Edit Data
  $('[data-f="cells"]', c).addEventListener("paste", function () {
    setTimeout(function () { if (b.rows.length) showCells(c, false); }, 0);
  });
  showCells(c, !has);
  drawDataChart(c, b);
}

function showCells(c, open) {
  $(".dk-paste", c).hidden = !open;
  $("[data-editcells]", c).textContent = open ? "Hide Data" : "Edit Data";
}

function drawDataChart(c, b) {
  disposeChart(b.id);
  var plot = $(".dk-chart-plot", c);
  var err = $(".dk-chart-err", c);
  var sum = $(".dk-data-sum", c);
  plot.innerHTML = "";
  err.hidden = true;
  var t = researchChart.table(b.rows);
  var dated = !!t.dates;
  // a line needs dates: labels such as countries are drawn as bars
  $all("[data-kind]", c).forEach(function (x) {
    var k = x.getAttribute("data-kind");
    x.disabled = k === "line" && !dated && t.labels.length > 0;
    x.title = x.disabled ? "A line needs dates in the first column" : "";
    x.setAttribute("aria-pressed", String(k === (b.kind === "bar" || !dated ? "bar" : "line")));
  });
  var d = researchChart.dataCfg(b);
  if (!d) {
    plot.classList.add("empty");
    plot.innerHTML = '<p class="muted">' + (b.rows.length
      ? "No numbers found. The first column holds dates or labels; the next columns hold numbers."
      : "Paste your cells in the box above. The chart appears here.") + "</p>";
    sum.textContent = "";
    return;
  }
  plot.classList.remove("empty");
  S.charts[b.id] = obsChart(plot, d.kind, d.cfg);
  sum.textContent = t.names.length + (t.names.length === 1 ? " series, " : " series, ") +
    t.labels.length + " rows" + (dated && t.labels.length ? ", " + t.labels[0] + " to " + t.labels[t.labels.length - 1] : "");
  if (t.bad) {
    err.hidden = false;
    err.textContent = t.bad + (t.bad === 1 ? " cell is" : " cells are") +
      " not a number and shows as a gap. Click Edit Data to check it.";
  }
}

/* New cells from the box: kept as rows, at most six series. */
function setCells(c, b, text) {
  b.origin = "author";
  var rows = parseCells(text);
  var wide = rows.some(function (r) { return r.length > 7; });
  if (wide) {
    rows = rows.map(function (r) { return r.slice(0, 7); });
    toast("A chart shows up to six series: only the first six columns of numbers were kept.");
  }
  if (rows.length > 3000) { rows = rows.slice(0, 3000); toast("Only the first 3,000 rows were kept."); }
  var first = !b.rows.length;
  b.rows = rows;
  if (first && rows.length) {
    var t = researchChart.table(rows);
    b.kind = t.dates ? "line" : "bar";
    if (!b.title && t.names.length === 1 && t.header) {
      b.title = t.names[0];
      $('[data-f="title"]', c).value = b.title;
    }
  }
  $(".dk-data-opts", c).hidden = !rows.length;
  drawDataChart(c, b);
}

/* ---------------------------------------------------------------- images */

function renderImageBlock(c, b) {
  c.innerHTML =
    '<div class="dk-fig-label dk-chart-label"></div>' +
    (b.media
      ? '<div class="dk-img"><img src="/research/media/' + b.media + "." + b.ext + '" alt="' +
        escapeHtml(b.alt || "") + '"><span class="dk-img-acts"><button type="button" class="dk-mini" data-img="replace">Replace Image</button>' +
        '<button type="button" class="dk-mini" data-img="rebuild" title="For a screenshot of another publisher\u2019s chart: ' +
        'the AI finds the public data behind it and puts a chart of that data here instead">Rebuild From Public Data</button></span></div>'
      : '<div class="dk-drop" tabindex="0" role="button" aria-label="Upload an image">' +
        "<strong>Drop a PNG, JPEG or WebP here</strong>, or " +
        '<button type="button" class="linkish" data-img="choose">choose a file</button>' +
        " (up to 8 MB)." +
        (b.pending ? '<span class="dk-pending">Waiting for: ' + escapeHtml(b.pending) + "</span>" : "") +
        "</div>") +
    '<input type="file" accept="image/png,image/jpeg,image/webp" hidden data-img="file">' +
    '<div class="dk-fig-fields">' +
    '<label>Caption <input type="text" data-f="caption" maxlength="200" placeholder="What the chart shows"></label>' +
    '<label><span>Source <span class="req" aria-hidden="true">*</span></span><input type="text" data-f="source" maxlength="400" placeholder="e.g. Bank of Japan; PloverResearch calculations"></label>' +
    '<label><span>Description for screen readers <span class="req" aria-hidden="true">*</span></span><input type="text" data-f="alt" maxlength="400" placeholder="One sentence: what a reader would see"></label>' +
    '<p class="dk-img-status" hidden></p></div>';
  $('[data-f="caption"]', c).value = b.caption || "";
  $('[data-f="source"]', c).value = b.source || "";
  $('[data-f="alt"]', c).value = b.alt || "";
}

function uploadImage(blockEl, file) {
  var m = S.models[blockEl.getAttribute("data-id")];
  var status = $(".dk-img-status", blockEl);
  status.hidden = false;
  status.className = "dk-img-status";
  status.textContent = "Uploading " + file.name + "…";
  return fetch("/admin/api/research/media", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": file.type || "application/octet-stream", "X-File-Name": file.name || "" },
    body: file,
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (!r.ok) throw new Error(body.detail || "The upload failed (" + r.status + ")");
      return body;
    });
  }).then(function (info) {
    var fields = readBlock(blockEl);
    Object.assign(m, fields, { media: info.media, ext: info.ext, width: info.width, height: info.height, pending: "" });
    if (!m.caption && file.name) m.caption = "";
    renderImageBlock(blockEl.querySelector(".dk-content"), m);
    updateCounts();
    changed();
  }).catch(function (err) {
    status.className = "dk-img-status bad";
    status.textContent = err.message;
  });
}

/* ---------------------------------------------------------------- paste */

function htmlToBlocks(html) {
  var doc = new DOMParser().parseFromString(html, "text/html");
  var out = [];
  var buf = [];
  var images = 0;
  function flush() {
    var h = cleanInline(buf.join(""));
    buf = [];
    if (textOf(h).trim()) out.push({ type: "p", html: h });
  }
  function hasBlockKids(n) {
    for (var i = 0; i < n.children.length; i++) {
      if (/^(P|DIV|H[1-6]|UL|OL|TABLE|BLOCKQUOTE|HR|SECTION|ARTICLE|PRE)$/.test(n.children[i].tagName)) return true;
    }
    return false;
  }
  function listItems(listEl) {
    var items = [];
    $all(":scope > li", listEl).forEach(function (li) {
      var copy = li.cloneNode(true);
      $all("ul,ol", copy).forEach(function (x) { x.parentNode.removeChild(x); });
      items.push(cleanInline(copy.innerHTML));
      $all(":scope > ul, :scope > ol", li).forEach(function (sub) { items = items.concat(listItems(sub)); });
    });
    return items;
  }
  (function walk(node) {
    for (var i = 0; i < node.childNodes.length; i++) {
      var n = node.childNodes[i];
      if (n.nodeType === 3) { buf.push(escapeHtml(n.nodeValue)); continue; }
      if (n.nodeType !== 1) continue;
      var tag = n.tagName;
      if (/^(SCRIPT|STYLE|META|LINK|TITLE|HEAD|TEMPLATE)$/.test(tag)) continue;
      if (/^H[1-6]$/.test(tag)) {
        flush();
        var txt = (n.textContent || "").replace(/\s+/g, " ").trim();
        if (txt) out.push({ type: "heading", level: tag === "H1" || tag === "H2" ? 2 : 3, text: txt });
      } else if (tag === "UL" || tag === "OL") {
        flush();
        var items = listItems(n).filter(function (x) { return textOf(x).trim(); });
        if (items.length) out.push({ type: "list", style: tag === "OL" ? "number" : "bullet", items: items });
      } else if (tag === "TABLE") {
        flush();
        var rows = $all("tr", n).map(function (tr) {
          return $all("th,td", tr).map(function (cell) { return cleanInline(cell.innerHTML); });
        }).filter(function (r) { return r.length; });
        if (rows.length) {
          var width = Math.min(14, Math.max.apply(null, rows.map(function (r) { return r.length; })));
          rows = rows.slice(0, 80).map(function (r) { r = r.slice(0, width); while (r.length < width) r.push(""); return r; });
          var head = !!n.querySelector("th") || !!n.querySelector("thead");
          out.push({ type: "table", rows: rows, header: head, align: rows[0].map(function () { return "auto"; }), caption: "", source: "" });
        }
      } else if (tag === "BLOCKQUOTE") {
        flush();
        out.push({ type: "quote", html: cleanInline(n.innerHTML) });
      } else if (tag === "HR") {
        flush();
        out.push({ type: "divider" });
      } else if (tag === "IMG") {
        images++;
      } else if (tag === "BR") {
        buf.push("<br>");
      } else if (/^(P|DIV|SECTION|ARTICLE|PRE|LI)$/.test(tag) || hasBlockKids(n)) {
        if (hasBlockKids(n)) { flush(); walk(n); flush(); }
        else { flush(); buf.push(n.innerHTML); flush(); }
      } else {
        buf.push(n.outerHTML);
      }
    }
  })(doc.body);
  flush();
  if (images) toast(images === 1 ? "The pasted image was left out: add it with an Image block."
    : images + " pasted images were left out: add them with Image blocks.");
  return out;
}

function textToBlocks(text) {
  text = text.replace(/\r/g, "");
  var lines = text.replace(/\n$/, "").split("\n");
  if (lines.length > 1 && lines.every(function (l) { return l.indexOf("\t") !== -1; })) {
    var rows = lines.map(function (l) { return l.split("\t").map(function (c) { return escapeHtml(c.trim()); }); });
    var width = Math.min(14, Math.max.apply(null, rows.map(function (r) { return r.length; })));
    rows = rows.map(function (r) { r = r.slice(0, width); while (r.length < width) r.push(""); return r; });
    return [{ type: "table", rows: rows, header: true, align: rows[0].map(function () { return "auto"; }), caption: "", source: "" }];
  }
  return text.split(/\n{2,}/).map(function (p) { return p.trim(); }).filter(Boolean).map(function (p) {
    return { type: "p", html: escapeHtml(p).replace(/\n/g, "<br>") };
  });
}

function onPaste(e) {
  var target = e.target.closest ? e.target.closest("[contenteditable=true]") : null;
  if (!target) return;
  var cd = e.clipboardData;
  if (!cd) return;
  var blockEl = blockOf(target);
  var model = S.models[blockEl.getAttribute("data-id")];
  var files = Array.prototype.slice.call(cd.files || []).filter(function (f) { return /^image\//.test(f.type); });
  e.preventDefault();
  if (files.length && model.type !== "table") {
    var img = insertBlock({ type: "image", media: "", ext: "png", alt: "", caption: "", source: "" }, blockEl, false);
    uploadImage(img, files[0]);
    return;
  }
  var html = cd.getData("text/html");
  var text = cd.getData("text/plain");
  if (model.type === "heading") {
    document.execCommand("insertText", false, text.replace(/\s+/g, " "));
    return;
  }
  if (pasteAsChart(blockEl, model, target, text)) return;
  if (target.tagName === "TD") {
    if (/[\t\n]/.test(text.replace(/\n$/, ""))) { pasteGrid(target, text); return; }
    document.execCommand("insertHTML", false, html ? cleanInline(html) : escapeHtml(text));
    return;
  }
  var blocks = html ? htmlToBlocks(html) : textToBlocks(text);
  if (!blocks.length) return;
  if (blocks.length === 1 && blocks[0].type === "p") {
    document.execCommand("insertHTML", false, blocks[0].html);
    return;
  }
  if (model.type === "list" && blocks.every(function (b) { return b.type === "list" || b.type === "p"; })) {
    var items = [];
    blocks.forEach(function (b) { items = items.concat(b.type === "list" ? b.items : [b.html]); });
    var li = currentLi(target);
    if (li) {
      var rest = splitAtCaret(li);
      li.insertAdjacentHTML("beforeend", items[0]);
      var at = li;
      items.slice(1).forEach(function (it) {
        var n = document.createElement("li");
        n.innerHTML = it || "<br>";
        at.parentNode.insertBefore(n, at.nextSibling);
        at = n;
      });
      at.insertAdjacentHTML("beforeend", '<span id="dk-caret"></span>' + rest);
      caretAtMarker(at);
      changed();
      return;
    }
  }
  pushUndo("paste");
  var tailHtml = model.type === "list" ? "" : splitAtCaret(target);
  var anchor = blockEl;
  var headEmpty = model.type !== "list" && isEmpty(target);
  blocks.forEach(function (b) { anchor = insertBlock(b, anchor, false); });
  if (tailHtml && textOf(tailHtml).trim()) anchor = insertBlock({ type: model.type === "quote" ? "quote" : "p", html: tailHtml }, anchor, false);
  if (headEmpty) removeBlock(blockEl);
  focusBlock(anchor, true);
  toast("Pasted " + blocks.length + " blocks.", true);
}

/* ---------------------------------------------------------------- keys */

function onKeyDown(e) {
  var mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); saveNow(); return; }
  if (slash.open && handleSlashKey(e)) return;

  var target = e.target;
  var blockEl = blockOf(target);
  if (!blockEl) {
    if (mod && e.key.toLowerCase() === "z" && !e.shiftKey && S.undo.length &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) { e.preventDefault(); undo(); }
    return;
  }
  var model = S.models[blockEl.getAttribute("data-id")];

  // chart series search: Enter takes the top result, arrows walk the list, Escape closes it
  if (model.type === "chart" && target.closest(".dk-search")) {
    var res = blockEl.querySelector(".dk-results");
    var hits = $all(".dk-result", res);
    var at = hits.indexOf(target);
    if (e.key === "Enter" && target.matches('[data-f="q"]')) {
      e.preventDefault();
      if (!res.hidden && hits[0]) hits[0].click();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      var next = hits[e.key === "ArrowDown" ? at + 1 : at - 1];
      if (next) next.focus();
      else if (e.key === "ArrowUp") blockEl.querySelector('[data-f="q"]').focus();
      return;
    }
    if (e.key === "Escape") { res.hidden = true; blockEl.querySelector('[data-f="q"]').focus(); return; }
  }

  if (e.altKey && e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
    e.preventDefault();
    moveBlock(blockEl, e.key === "ArrowUp" ? -1 : 1);
    return;
  }
  if (target.classList.contains("dk-hr") && (e.key === "Backspace" || e.key === "Delete")) {
    e.preventDefault();
    var prev = blockEl.previousElementSibling;
    pushUndo("delete");
    removeBlock(blockEl);
    focusBlock(prev, true);
    toast("Divider deleted.", true);
    return;
  }
  if (target.tagName === "TD") { cellKeys(e, target); return; }
  if (target.getAttribute("data-snap") === "url" && e.key === "Enter") {
    e.preventDefault();
    snapClick(blockEl, model, blockEl.querySelector('[data-snap="capture"]'));
    return;
  }
  if (target.getAttribute("contenteditable") !== "true") return;

  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); toolbar.link(); return; }
  if (model.type === "heading" && mod && /^[biu]$/i.test(e.key)) { e.preventDefault(); return; }

  if (model.type === "list") { listKeys(e, blockEl, target); return; }

  if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    if (e.shiftKey && model.type !== "heading") { document.execCommand("insertLineBreak"); return; }
    var tail = splitAtCaret(target);
    insertBlock({ type: model.type === "quote" && !isEmpty(target) && textOf(tail).trim() ? "quote" : "p", html: tail }, blockEl, false);
    focusBlock(blockEl.nextElementSibling, false);
    changed();
    return;
  }
  if (e.key === "Backspace" && atStart(target)) {
    if (model.type !== "p") { e.preventDefault(); convert(blockEl, "p"); return; }
    var before = blockEl.previousElementSibling;
    if (!before) return;
    var bm = S.models[before.getAttribute("data-id")];
    e.preventDefault();
    if (bm.type === "p" || bm.type === "quote" || bm.type === "heading") {
      var into = before.querySelector("[contenteditable=true]");
      var html = cleanInline(target.innerHTML);
      if (bm.type === "heading") into.insertAdjacentHTML("beforeend", '<span id="dk-caret"></span>' + escapeHtml(textOf(html)));
      else into.insertAdjacentHTML("beforeend", '<span id="dk-caret"></span>' + html);
      into.focus();
      caretAtMarker(into);
      removeBlock(blockEl);
    } else if (bm.type === "list") {
      var lis = before.querySelectorAll("li");
      var last = lis[lis.length - 1];
      last.insertAdjacentHTML("beforeend", '<span id="dk-caret"></span>' + cleanInline(target.innerHTML));
      before.querySelector("[contenteditable=true]").focus();
      caretAtMarker(last);
      removeBlock(blockEl);
    } else if (isEmpty(target)) {
      removeBlock(blockEl);
      focusBlock(before, true);
    } else {
      before.classList.add("dk-flash");
      setTimeout(function () { before.classList.remove("dk-flash"); }, 600);
    }
    return;
  }
  if (e.key === "Delete" && atEnd(target)) {
    var next = blockEl.nextElementSibling;
    if (next && (S.models[next.getAttribute("data-id")] || {}).type === "p") {
      e.preventDefault();
      var ne = next.querySelector("[contenteditable=true]");
      target.insertAdjacentHTML("beforeend", '<span id="dk-caret"></span>' + cleanInline(ne.innerHTML));
      caretAtMarker(target);
      removeBlock(next);
    }
    return;
  }
  if (e.key === "ArrowUp" && atStart(target)) {
    var up = textBlockBefore(blockEl);
    if (up) { e.preventDefault(); focusBlock(up, true); }
    return;
  }
  if (e.key === "ArrowDown" && atEnd(target)) {
    var down = textBlockAfter(blockEl);
    if (down) { e.preventDefault(); focusBlock(down, false); }
  }
}

function currentLi(listEl) {
  var r = caretRange();
  if (!r) return null;
  var n = r.startContainer;
  while (n && n !== listEl) { if (n.tagName === "LI") return n; n = n.parentNode; }
  return null;
}

function listKeys(e, blockEl, listEl) {
  var li = currentLi(listEl);
  if (!li) return;
  if (e.key === "Tab") { e.preventDefault(); return; }
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && isEmpty(li)) {
    e.preventDefault();
    var lis = $all("li", listEl);
    var idx = lis.indexOf(li);
    var after = lis.slice(idx + 1).map(function (x) { return cleanInline(x.innerHTML); });
    lis.slice(idx).forEach(function (x) { x.parentNode.removeChild(x); });
    var p = insertBlock({ type: "p", html: "" }, blockEl, false);
    if (after.length) {
      var m = S.models[blockEl.getAttribute("data-id")];
      insertBlock({ type: "list", style: m.style, items: after }, p, false);
    }
    if (!listEl.querySelector("li")) removeBlock(blockEl);
    focusBlock(p, false);
    return;
  }
  if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); document.execCommand("insertLineBreak"); return; }
  if (e.key === "Backspace" && li === listEl.querySelector("li") && atStart(li)) {
    e.preventDefault();
    var html = cleanInline(li.innerHTML);
    li.parentNode.removeChild(li);
    var para = makeBlock({ type: "p", html: html });
    blocksEl().insertBefore(para, blockEl);
    if (!listEl.querySelector("li")) removeBlock(blockEl);
    afterStructure();
    focusBlock(para, false);
    return;
  }
  if (e.key === "ArrowUp" && li === listEl.querySelector("li") && atStart(li)) {
    var up = textBlockBefore(blockEl);
    if (up) { e.preventDefault(); focusBlock(up, true); }
  }
  if (e.key === "ArrowDown") {
    var all = listEl.querySelectorAll("li");
    if (li === all[all.length - 1] && atEnd(li)) {
      var down = textBlockAfter(blockEl);
      if (down) { e.preventDefault(); focusBlock(down, false); }
    }
  }
}

function cellKeys(e, td) {
  S.cell = td;
  var tr = td.parentNode;
  var tbody = tr.parentNode;
  var pos = cellPos(td);
  if (e.key === "Tab") {
    e.preventDefault();
    var cells = $all("td", tbody);
    var i = cells.indexOf(td) + (e.shiftKey ? -1 : 1);
    if (i >= cells.length) { tableOp(blockOf(td), "row"); return; }
    if (i >= 0) focusCell(cells[i]);
  } else if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    if (e.shiftKey) { document.execCommand("insertLineBreak"); return; }
    var below = tbody.rows[pos.r + 1];
    if (!below) { tableOp(blockOf(td), "row"); return; }
    focusCell(below.cells[pos.c]);
  } else if (e.key === "ArrowUp" && atStart(td) && tbody.rows[pos.r - 1]) {
    e.preventDefault(); focusCell(tbody.rows[pos.r - 1].cells[pos.c]);
  } else if (e.key === "ArrowDown" && atEnd(td) && tbody.rows[pos.r + 1]) {
    e.preventDefault(); focusCell(tbody.rows[pos.r + 1].cells[pos.c]);
  }
}

function moveBlock(el, dir) {
  var box = blocksEl();
  if (dir < 0 && el.previousElementSibling) box.insertBefore(el, el.previousElementSibling);
  else if (dir > 0 && el.nextElementSibling) box.insertBefore(el, el.nextElementSibling.nextSibling);
  else return;
  el.scrollIntoView({ block: "nearest" });
  var f = el.querySelector("[contenteditable=true]") || el.querySelector(".dk-handle");
  if (f) f.focus();
  afterStructure();
}

/* ---------------------------------------------------------------- input */

var SHORTCUTS = [
  [/^###\s/, "h3"], [/^##?\s/, "h2"], [/^[-*]\s/, "bullet"], [/^1[.)]\s/, "number"], [/^>\s/, "quote"],
];

function onInput(e) {
  var target = e.target;
  var blockEl = blockOf(target);
  if (!blockEl) return;
  var model = S.models[blockEl.getAttribute("data-id")];
  if (target.getAttribute && target.getAttribute("contenteditable") === "true") {
    if (target.innerHTML === "<br>") target.innerHTML = "";
    if (model.type === "list" && !target.querySelector("li")) target.innerHTML = "<li><br></li>";
    if (model.type === "p") {
      var text = target.textContent || "";
      var onlyText = !target.querySelector("*:not(br)");
      if (onlyText) {
        for (var i = 0; i < SHORTCUTS.length; i++) {
          if (SHORTCUTS[i][0].test(text.replace(/ /g, " "))) {
            var rest = escapeHtml(text.replace(/ /g, " ").replace(SHORTCUTS[i][0], ""));
            target.innerHTML = rest;
            convert(blockEl, SHORTCUTS[i][1]);
            return;
          }
        }
        if (text === "---") {
          var p = insertBlock({ type: "p", html: "" }, blockEl, false);
          replaceBlock(blockEl, { type: "divider" });
          focusBlock(p, false);
          return;
        }
      }
      if (/^\/[\w ]{0,24}$/.test(text) && onlyText) slashOpen(blockEl, text.slice(1), true);
      else if (slash.open) slashClose();
    }
  }
  if (target.tagName === "SELECT") return;   // handled on change
  if (target.matches && target.matches(".dk-chart-title, [data-f]")) { fieldInput(blockEl, model, target); return; }
  changed();
}

function fieldInput(blockEl, model, input) {
  var f = input.getAttribute("data-f") || "title";
  var c = blockEl.querySelector(".dk-content");
  if (model.type === "chart") {
    if (f === "q") { searchSeries(c, model, input.value); return; }
    if (f === "start" || f === "end") {
      model[f] = input.value.trim();
      if (model[f] && !/^\d{4}-\d{2}(-\d{2})?$/.test(model[f])) { changed(); return; }
      clearTimeout(model._t);
      model._t = setTimeout(function () { drawChart(c, model); }, 500);
    } else model[f] = input.value;
    if (f === "title" && S.charts[model.id]) {
      clearTimeout(model._tt);
      model._tt = setTimeout(function () { drawChart(c, model); }, 800);
    }
  } else if (model.type === "datachart") {
    if (f === "cells") setCells(c, model, input.value);
    else {
      model[f] = input.value;
      // the title and unit are drawn on the chart and its image
      if (f === "title" || f === "unit") {
        clearTimeout(model._t);
        model._t = setTimeout(function () { drawDataChart(c, model); }, 400);
      }
    }
  } else {
    model[f] = input.value;
  }
  changed();
}

function onChange(e) {
  var t = e.target;
  var blockEl = blockOf(t);
  if (!blockEl) return;
  var model = S.models[blockEl.getAttribute("data-id")];
  var c = blockEl.querySelector(".dk-content");
  if (model.type === "table" && t.getAttribute("data-t") === "align") {
    tableOp(blockEl, "align", t.value);
  } else if (model.type === "table" && t.getAttribute("data-t") === "header") {
    tableOp(blockEl, "header", t.checked);
  } else if (model.type === "image" && t.getAttribute("data-img") === "file" && t.files[0]) {
    uploadImage(blockEl, t.files[0]);
  } else if (model.type === "placeholder" && t.getAttribute("data-ph") === "file" && t.files[0]) {
    rebuildFromPicture(blockEl, model, t.files[0]);
    t.value = "";
  }
}

function onClick(e) {
  var t = e.target;
  var blockEl = blockOf(t);
  var fn = t.closest && t.closest("sup.dk-fn");
  if (fn) { e.preventDefault(); notes.open(fn); return; }
  if (!blockEl) return;
  var model = S.models[blockEl.getAttribute("data-id")];
  if (t.closest(".dk-del")) { deleteBlock(blockEl); return; }
  if (t.closest(".dk-add")) { slashOpen(blockEl, "", false); return; }
  if (t.closest(".dk-handle")) { blockMenu(blockEl, t.closest(".dk-handle")); return; }
  var c = blockEl.querySelector(".dk-content");
  if (model.type === "table" && t.getAttribute("data-t") && t.tagName === "BUTTON") {
    tableOp(blockEl, t.getAttribute("data-t"));
    return;
  }
  if (model.type === "chart") {
    var rm = t.closest("[data-rm]");
    if (rm) { removeSeries(c, model, rm.getAttribute("data-rm")); changed(); return; }
    var pick = t.closest(".dk-result");
    if (pick) { pickSeries(c, model, pick); changed(); return; }
    var ms = t.closest("[data-measure]");
    if (ms) {
      model.measure = ms.getAttribute("data-measure");
      chartControls(c, model);
      drawChart(c, model);
      changed();
      return;
    }
    var rg = t.closest("[data-range]");
    if (rg) { setRange(c, model, rg.getAttribute("data-range")); changed(); return; }
    if (t.getAttribute("data-f") === "q") searchSeries(c, model, t.value);
  }
  if (model.type === "datachart") {
    var kd = t.closest("[data-kind]");
    if (kd && !kd.disabled) { model.kind = kd.getAttribute("data-kind"); drawDataChart(c, model); changed(); return; }
    if (t.closest("[data-editcells]")) {
      var open = $(".dk-paste", c).hidden;
      showCells(c, open);
      if (open) $('[data-f="cells"]', c).focus();
      return;
    }
  }
  if (model.type === "snapshot" && snapClick(blockEl, model, t.closest("[data-snap]") || t)) return;
  if (model.type === "placeholder") { placeholderClick(blockEl, model, t); return; }
  if (model.type === "image" && t.getAttribute("data-img") === "rebuild") { rebuildImage(blockEl, model, t); return; }
  if (model.type === "image") {
    var act = t.getAttribute("data-img");
    if (act === "choose" || act === "replace" || t.closest(".dk-drop")) {
      $('[data-img="file"]', c).click();
    }
  }
}

/* ---------------------------------------------------------------- slash menu */

var slash = { open: false, block: null, items: [], index: 0, replace: false, el: null };

function slashOpen(blockEl, query, replace) {
  var items = BLOCK_MENU.filter(function (m) {
    return !query || (m.label + " " + m.key).toLowerCase().indexOf(query.toLowerCase().trim()) !== -1;
  });
  if (!items.length) { slashClose(); return; }
  slash.open = true;
  slash.block = blockEl;
  slash.items = items;
  slash.index = Math.min(slash.index, items.length - 1);
  if (!replace || query === "") slash.index = 0;
  slash.replace = replace;
  if (!slash.el) {
    slash.el = document.createElement("div");
    slash.el.className = "dk-pop dk-slash";
    slash.el.setAttribute("role", "listbox");
    document.body.appendChild(slash.el);
    slash.el.addEventListener("mousedown", function (e) {
      var b = e.target.closest("[data-k]");
      if (!b) return;
      e.preventDefault();
      slashChoose(Number(b.getAttribute("data-k")));
    });
  }
  slash.el.innerHTML = '<p class="dk-pop-head">Insert a block</p>' + items.map(function (m, i) {
    return '<button type="button" role="option" data-k="' + i + '" aria-selected="' + (i === slash.index) + '">' +
      "<span>" + escapeHtml(m.label) + '</span><span class="muted">' + escapeHtml(m.hint) + "</span></button>";
  }).join("");
  slash.el.hidden = false;
  placePop(slash.el, blockEl.querySelector(".dk-content"));
}

function slashClose() {
  slash.open = false;
  if (slash.el) slash.el.hidden = true;
}

function handleSlashKey(e) {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    slash.index = (slash.index + (e.key === "ArrowDown" ? 1 : -1) + slash.items.length) % slash.items.length;
    $all("[data-k]", slash.el).forEach(function (b, i) { b.setAttribute("aria-selected", String(i === slash.index)); });
    return true;
  }
  if (e.key === "Enter") { e.preventDefault(); slashChoose(slash.index); return true; }
  if (e.key === "Escape") { e.preventDefault(); slashClose(); return true; }
  return false;
}

function slashChoose(i) {
  var item = slash.items[i];
  var blockEl = slash.block;
  slashClose();
  if (!item || !blockEl) return;
  var model = S.models[blockEl.getAttribute("data-id")];
  var editable = blockEl.querySelector("[contenteditable=true]");
  var made = item.make();
  if (slash.replace && editable && model.type === "p") {
    editable.innerHTML = "";
    if (TEXT_TYPES[made.type]) { convert(blockEl, item.key); return; }
    var el = replaceBlock(blockEl, made);
    if (!el.nextElementSibling) insertBlock({ type: "p", html: "" }, el, false);
    focusBlock(el, false);
    return;
  }
  if (model.type === "p" && editable && isEmpty(editable) && !slash.replace) {
    if (TEXT_TYPES[made.type]) { convert(blockEl, item.key); return; }
    var el2 = replaceBlock(blockEl, made);
    if (!el2.nextElementSibling) insertBlock({ type: "p", html: "" }, el2, false);
    focusBlock(el2, false);
    return;
  }
  var added = insertBlock(made, blockEl, true);
  if (!TEXT_TYPES[made.type] && !added.nextElementSibling) insertBlock({ type: "p", html: "" }, added, false);
}

/* ---------------------------------------------------------------- block menu */

var bmenu = { el: null, block: null };

function blockMenu(blockEl, anchor) {
  var model = S.models[blockEl.getAttribute("data-id")];
  if (!bmenu.el) {
    bmenu.el = document.createElement("div");
    bmenu.el.className = "dk-pop dk-bmenu";
    bmenu.el.setAttribute("role", "menu");
    document.body.appendChild(bmenu.el);
    bmenu.el.addEventListener("click", function (e) {
      var b = e.target.closest("[data-do]");
      if (!b) return;
      var el = bmenu.block;
      bmenuClose();
      var what = b.getAttribute("data-do");
      if (what === "up") moveBlock(el, -1);
      else if (what === "down") moveBlock(el, 1);
      else if (what === "dup") {
        var copy = readBlock(el);
        copy.id = bid();
        insertBlock(copy, el, true);
      } else if (what === "del") {
        deleteBlock(el);
      } else if (what.indexOf("to-") === 0) {
        convert(el, what.slice(3));
      }
    });
  }
  bmenu.block = blockEl;
  var turn = TEXT_TYPES[model.type]
    ? '<p class="dk-pop-head">Turn into</p>' + ["p", "h2", "h3", "bullet", "number", "quote"].map(function (k) {
        var m = BLOCK_MENU.filter(function (x) { return x.key === k; })[0];
        return '<button type="button" role="menuitem" data-do="to-' + k + '">' + escapeHtml(m.label) + "</button>";
      }).join("") + '<hr class="dk-pop-rule">'
    : "";
  bmenu.el.innerHTML = turn +
    '<button type="button" role="menuitem" data-do="up">Move Up <span class="muted">Alt+Shift+↑</span></button>' +
    '<button type="button" role="menuitem" data-do="down">Move Down <span class="muted">Alt+Shift+↓</span></button>' +
    '<button type="button" role="menuitem" data-do="dup">Duplicate</button>' +
    '<button type="button" role="menuitem" data-do="del" class="danger">Delete</button>';
  bmenu.el.hidden = false;
  placePop(bmenu.el, anchor);
  var first = bmenu.el.querySelector("button");
  if (first) first.focus();
}

function bmenuClose() {
  if (bmenu.el) bmenu.el.hidden = true;
}

/* Below the anchor, inside the viewport, clear of the top bar. */
function placePop(el, anchor) {
  var r = anchor.getBoundingClientRect();
  var bar = $(".dk-bar");
  var ceiling = bar ? bar.getBoundingClientRect().bottom + 4 : 8;
  el.style.left = "0px";
  el.style.top = "0px";
  var left = Math.min(r.left, window.innerWidth - el.offsetWidth - 8);
  var top = r.bottom + 4;
  if (top + el.offsetHeight > window.innerHeight - 8) {
    var above = r.top - 4 - el.offsetHeight;
    top = above > ceiling ? above : Math.max(ceiling, window.innerHeight - 8 - el.offsetHeight);
  }
  el.style.left = Math.max(8, left) + "px";
  el.style.top = Math.max(ceiling, top) + "px";
}

/* ---------------------------------------------------------------- drag to move */

var drag = { el: null };

function onDragStart(e) {
  var h = e.target.closest && e.target.closest(".dk-handle");
  if (!h) return;
  drag.el = blockOf(h);
  drag.el.classList.add("dk-dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", drag.el.getAttribute("data-id"));
  try { e.dataTransfer.setDragImage(drag.el, 20, 16); } catch (err) { /* older browsers */ }
}

function dropTarget(e) {
  var over = blockOf(e.target);
  if (!over || over === drag.el) return null;
  var r = over.getBoundingClientRect();
  return { el: over, before: e.clientY < r.top + r.height / 2 };
}

function onDragOver(e) {
  if (!drag.el) return;
  e.preventDefault();
  $all(".dk-drop-before, .dk-drop-after").forEach(function (x) { x.classList.remove("dk-drop-before", "dk-drop-after"); });
  var t = dropTarget(e);
  if (t) t.el.classList.add(t.before ? "dk-drop-before" : "dk-drop-after");
}

function onDrop(e) {
  if (!drag.el) return;
  e.preventDefault();
  var t = dropTarget(e);
  $all(".dk-drop-before, .dk-drop-after").forEach(function (x) { x.classList.remove("dk-drop-before", "dk-drop-after"); });
  if (t) {
    blocksEl().insertBefore(drag.el, t.before ? t.el : t.el.nextSibling);
    afterStructure();
  }
  onDragEnd();
}

function onDragEnd() {
  if (drag.el) drag.el.classList.remove("dk-dragging");
  drag.el = null;
}

/* ---------------------------------------------------------------- selection toolbar */

var toolbar = (function () {
  var el = null;
  var saved = null;

  function make() {
    el = document.createElement("div");
    el.className = "dk-pop dk-tb";
    el.setAttribute("role", "toolbar");
    el.setAttribute("aria-label", "Text formatting");
    document.body.appendChild(el);
    el.addEventListener("mousedown", function (e) {
      if (e.target.tagName !== "INPUT") e.preventDefault();
    });
    el.addEventListener("click", function (e) {
      var b = e.target.closest("[data-cmd]");
      if (!b) return;
      run(b.getAttribute("data-cmd"));
    });
  }

  function buttons() {
    el.innerHTML =
      '<button type="button" data-cmd="bold" title="Bold (Cmd/Ctrl+B)"><strong>B</strong></button>' +
      '<button type="button" data-cmd="italic" title="Italic (Cmd/Ctrl+I)"><em>I</em></button>' +
      '<button type="button" data-cmd="link" title="Link (Cmd/Ctrl+K)">Link</button>' +
      '<button type="button" data-cmd="code" title="Code">Code</button>' +
      '<button type="button" data-cmd="sup" title="Superscript">x²</button>' +
      '<button type="button" data-cmd="note" title="Add a footnote after the selection">Note</button>' +
      '<button type="button" data-cmd="clear" title="Clear formatting">Clear</button>';
  }

  function editableOf(node) {
    var n = node && (node.nodeType === 1 ? node : node.parentNode);
    var ed = n && n.closest ? n.closest("[contenteditable=true]") : null;
    if (!ed) return null;
    var b = blockOf(ed);
    var m = b && S.models[b.getAttribute("data-id")];
    return m && m.type !== "heading" ? ed : null;
  }

  function update() {
    if (!el) make();
    if (el.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
    var s = sel();
    if (!s.rangeCount || s.isCollapsed || !editableOf(s.anchorNode) || !editableOf(s.focusNode)) {
      el.hidden = true;
      return;
    }
    buttons();
    el.hidden = false;
    var rect = s.getRangeAt(0).getBoundingClientRect();
    var bar = $(".dk-bar");
    var ceiling = bar ? bar.getBoundingClientRect().bottom + 4 : 8;
    var top = rect.top - el.offsetHeight - 8;
    if (top < ceiling) top = rect.bottom + 8;
    el.style.top = top + "px";
    el.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - el.offsetWidth - 8)) + "px";
  }

  function restore() {
    if (!saved) return;
    var s = sel();
    s.removeAllRanges();
    s.addRange(saved);
  }

  function link() {
    var s = sel();
    if (!s.rangeCount || s.isCollapsed) { toast("Select the text to link first."); return; }
    if (!el) make();
    saved = s.getRangeAt(0).cloneRange();
    var existing = s.anchorNode && s.anchorNode.parentNode && s.anchorNode.parentNode.closest &&
      s.anchorNode.parentNode.closest("a");
    el.hidden = false;
    el.innerHTML = '<form class="dk-link-form"><input type="text" placeholder="https://… or cpi.html" aria-label="Link address" value="' +
      escapeHtml(existing ? existing.getAttribute("href") : "") + '">' +
      '<button type="submit" class="dk-mini">Apply</button>' +
      (existing ? '<button type="button" class="dk-mini" data-unlink>Remove</button>' : "") +
      '<span class="dk-link-err" hidden></span></form>';
    var input = el.querySelector("input");
    input.focus();
    el.querySelector("form").addEventListener("submit", function (e) {
      e.preventDefault();
      var href = safeHref(input.value);
      if (!href) {
        var er = el.querySelector(".dk-link-err");
        er.hidden = false;
        er.textContent = "Use a web address (https://…), an email (mailto:…) or a page on this site.";
        return;
      }
      restore();
      document.execCommand("createLink", false, href);
      el.hidden = true;
      changed();
    });
    var un = el.querySelector("[data-unlink]");
    if (un) un.addEventListener("click", function () {
      restore();
      document.execCommand("unlink");
      el.hidden = true;
      changed();
    });
  }

  function run(cmd) {
    if (cmd === "bold" || cmd === "italic") document.execCommand(cmd);
    else if (cmd === "sup") document.execCommand("superscript");
    else if (cmd === "link") { link(); return; }
    else if (cmd === "code") {
      var text = sel().toString();
      document.execCommand("insertHTML", false, "<code>" + escapeHtml(text) + "</code>");
    } else if (cmd === "clear") {
      document.execCommand("removeFormat");
      document.execCommand("unlink");
    } else if (cmd === "note") {
      var s = sel();
      var r = s.getRangeAt(0);
      r.collapse(false);
      var holder = document.createElement("span");
      holder.innerHTML = noteSup("");
      var sup = holder.firstChild;
      r.insertNode(sup);
      r.setStartAfter(sup);
      r.collapse(true);
      s.removeAllRanges();
      s.addRange(r);
      el.hidden = true;
      notes.open(sup);
      return;
    }
    changed();
    setTimeout(update, 0);
  }

  return { update: update, link: link, hide: function () { if (el) el.hidden = true; } };
})();

/* ---------------------------------------------------------------- footnotes */

var notes = (function () {
  var el = null;
  var target = null;

  function open(sup) {
    target = sup;
    if (!el) {
      el = document.createElement("div");
      el.className = "dk-pop dk-note";
      document.body.appendChild(el);
    }
    el.innerHTML = '<label for="dk-note-text">Footnote</label>' +
      '<textarea id="dk-note-text" rows="3" maxlength="1500" placeholder="The note readers see at the end of the article"></textarea>' +
      '<div class="dk-note-acts"><button type="button" class="dk-mini" data-n="done">Done</button>' +
      '<button type="button" class="dk-mini danger" data-n="del">Delete Note</button></div>';
    var ta = el.querySelector("textarea");
    ta.value = sup.getAttribute("data-note") || "";
    el.hidden = false;
    placePop(el, sup);
    ta.focus();
    el.querySelector('[data-n="done"]').onclick = close;
    el.querySelector('[data-n="del"]').onclick = function () {
      target.parentNode.removeChild(target);
      target = null;
      el.hidden = true;
      changed();
    };
    ta.onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); close(); }
      if (e.key === "Escape") { e.preventDefault(); close(); }
    };
  }

  function close() {
    if (!target) { if (el) el.hidden = true; return; }
    var text = el.querySelector("textarea").value.replace(/\s+/g, " ").trim();
    if (!text) target.parentNode.removeChild(target);
    else target.setAttribute("data-note", text);
    target = null;
    el.hidden = true;
    changed();
  }

  function outside(e) {
    if (el && !el.hidden && !el.contains(e.target) && !(e.target.closest && e.target.closest("sup.dk-fn"))) close();
  }

  return { open: open, close: close, outside: outside };
})();

/* ---------------------------------------------------------------- saving */

function changed() {
  S.seq++;
  setStatus("dirty");
  backupLocal();
  clearTimeout(S.timer);
  S.timer = setTimeout(save, SAVE_DELAY);
  updateWordCount();
}

function backupKey() { return "dk-backup-" + ID; }

function backupLocal() {
  try {
    localStorage.setItem(backupKey(), JSON.stringify({ at: new Date().toISOString(), base: S.base, draft: serialize() }));
  } catch (e) { /* storage full or blocked: the server save still runs */ }
}

function clearBackup() {
  try { localStorage.removeItem(backupKey()); } catch (e) { /* ignore */ }
}

function saveNow() {
  clearTimeout(S.timer);
  return save();
}

function save(opts) {
  if (S.saving) { S.again = true; return Promise.resolve(); }
  if (S.seq === S.savedSeq && !(opts && opts.force)) return Promise.resolve();
  var seq = S.seq;
  var draft = serialize();
  S.saving = true;
  setStatus("saving");
  return send("PUT", "/research/articles/" + ID, { draft: draft, base_revision: S.base }).then(function (res) {
    S.base = res.revision;
    S.savedSeq = Math.max(S.savedSeq, seq);
    S.article.updated_at = res.updated_at;
    if (res.draft && res.draft.slug !== draft.slug && document.activeElement !== $("#dk-slug")) {
      $("#dk-slug").value = res.draft.slug;
    }
    if (S.seq === seq) clearBackup();
    setStatus(S.seq === S.savedSeq ? "saved" : "dirty");
    hideBanner("conflict");
  }).catch(function (err) {
    if (err.status === 409) {
      conflict(err.body.current);
    } else if (err.status === 401) {
      setStatus("error", "Not saved: signed out");
    } else if (err.status === 400) {
      setStatus("error", "Not saved: " + err.message);
    } else {
      setStatus("error", "Not saved — retrying");
      clearTimeout(S.retry);
      S.retry = setTimeout(save, 5000);
    }
  }).then(function () {
    S.saving = false;
    if (S.again) { S.again = false; save(); }
  });
}

function conflict(current) {
  setStatus("error", "Not saved: newer version exists");
  showBanner("warn", escapeHtml(current.updated_by) + " saved a newer version at " +
    escapeHtml(fmtStamp(current.updated_at)) + " UTC. " +
    '<button type="button" class="btn" data-conflict="theirs">Load Their Version</button> ' +
    '<button type="button" class="btn" data-conflict="mine">Keep Mine</button>', "conflict");
  $all("[data-conflict]").forEach(function (b) {
    b.addEventListener("click", function () {
      hideBanner("conflict");
      if (b.getAttribute("data-conflict") === "mine") {
        S.base = current.revision;
        save({ force: true });
      } else {
        pushUndo("theirs");
        S.base = current.revision;
        renderDraft(current.draft);
        S.savedSeq = S.seq;
        clearBackup();
        setStatus("saved");
        toast("Loaded " + current.updated_by + "’s version. Your version is one Undo away.", true);
      }
    });
  });
}

var STATUS_TEXT = { dirty: "Unsaved changes", saving: "Saving…", saved: "Saved" };

function setStatus(kind, text) {
  var el = $("#dk-status");
  if (!el) return;
  el.className = "dk-status " + kind;
  if (kind === "saved") {
    var t = new Date();
    el.textContent = "Saved " + String(t.getHours()).padStart(2, "0") + ":" + String(t.getMinutes()).padStart(2, "0");
  } else el.textContent = text || STATUS_TEXT[kind] || "";
}

window.addEventListener("beforeunload", function (e) {
  if (S.seq !== S.savedSeq) {
    try {
      fetch("/admin/api/research/articles/" + ID, {
        method: "PUT", keepalive: true, credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft: serialize(), base_revision: S.base }),
      });
    } catch (err) { /* the local copy remains */ }
    e.preventDefault();
    e.returnValue = "";
  }
});

/* ---------------------------------------------------------------- presence */

function presence() {
  send("POST", "/research/articles/" + ID + "/presence").then(function (res) {
    S.others = res.others;
    var chip = $("#dk-others");
    if (chip) {
      chip.hidden = !res.others.length;
      chip.textContent = res.others.length
        ? "Also open: " + res.others.map(function (o) { return o.name; }).join(", ") : "";
    }
    if (res.revision > S.base && S.seq === S.savedSeq && !S.saving) {
      api("/research/articles/" + ID).then(function (a) {
        if (S.seq !== S.savedSeq) return;
        S.article = a;
        S.base = a.revision;
        renderDraft(a.draft);
        S.savedSeq = S.seq;
        setStatus("saved");
        toast("Updated with " + a.updated_by + "’s changes.");
      });
    }
  }).catch(function () { /* next tick tries again */ });
}

/* ---------------------------------------------------------------- banners, toasts */

function showBanner(kind, html, key) {
  var box = $("#dk-banners");
  if (!box) return;
  hideBanner(key || kind);
  var d = document.createElement("div");
  d.className = "dk-banner " + kind;
  d.setAttribute("data-key", key || kind);
  d.setAttribute("role", "status");
  d.innerHTML = html;
  box.appendChild(d);
}

function hideBanner(key) {
  $all('.dk-banner[data-key="' + key + '"]').forEach(function (x) { x.parentNode.removeChild(x); });
}

var toastTimer = null;
function toast(text, withUndo) {
  var t = $("#dk-toast");
  if (!t) return;
  t.innerHTML = "<span>" + escapeHtml(text) + "</span>" +
    (withUndo && S.undo.length ? '<button type="button" class="linkish" id="dk-undo">Undo</button>' : "");
  t.hidden = false;
  var u = $("#dk-undo");
  if (u) u.addEventListener("click", function () { t.hidden = true; undo(); });
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.hidden = true; }, withUndo ? 8000 : 4000);
}

/* ---------------------------------------------------------------- counts */

var MARKETS = [["jp", "Japan"], ["us", "United States"], ["global", "Global"]];
var TOPICS = [["inflation", "Inflation"], ["monetary-policy", "Monetary Policy"], ["rates", "Rates"],
  ["banks", "Banks"], ["equities", "Equities"], ["companies", "Companies"], ["governance", "Governance"],
  ["growth", "Growth"], ["labour", "Labour"], ["trade", "Trade"], ["public-finance", "Public Finance"],
  ["tourism", "Tourism"]];

/* The homepage-chart choices: every figure in the article, by number and title. */
function fillLeadOptions() {
  var sel = $("#dk-lead");
  if (!sel || !blocksEl()) return;
  var n = 0;
  var opts = ['<option value="">First chart in the note</option>'];
  $all(".dk-block", blocksEl()).forEach(function (b) {
    var type = b.getAttribute("data-type");
    if (type !== "chart" && type !== "image" && type !== "snapshot" && type !== "datachart") return;
    n++;
    var m = S.models[b.getAttribute("data-id")] || {};
    var title = m.title || m.caption || "";
    opts.push('<option value="' + escapeHtml(m.id) + '">Chart ' + n + (title ? " \u2014 " + escapeHtml(title.slice(0, 60)) : "") + "</option>");
  });
  sel.innerHTML = opts.join("");
  sel.value = S.leadChoice || "";
  if (sel.value !== (S.leadChoice || "")) sel.value = "";
  $("#dk-feature").disabled = n === 0;
}

function updateCounts() {
  var c = 0;
  var t = 0;
  $all(".dk-block", blocksEl()).forEach(function (b) {
    var type = b.getAttribute("data-type");
    var lab = b.querySelector(".dk-fig-label");
    if (type === "chart" || type === "image" || type === "snapshot" || type === "datachart") { c++; if (lab) lab.textContent = "Chart " + c; }
    if (type === "table") { t++; if (lab) lab.textContent = "Table " + t; }
    var del = b.querySelector(":scope > .dk-del");
    if (del && lab) del.setAttribute("aria-label", "Delete " + lab.textContent);
  });
  fillLeadOptions();
}

function updateWordCount() {
  var el = $("#dk-words");
  if (!el) return;
  var words = 0;
  $all(".dk-text, .dk-grid td", blocksEl()).forEach(function (x) {
    words += ((x.textContent || "").match(/\S+/g) || []).length;
  });
  el.textContent = words.toLocaleString("en-US") + " words · " + Math.max(1, Math.round(words / 230)) + " min read";
}

/* ---------------------------------------------------------------- side panel */

function slugify(text) {
  return (text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80).replace(/-+$/, "");
}

function renderSide() {
  var a = S.article;
  var d = a.draft;
  var locked = a.slug_locked;
  $("#dk-pane-details").innerHTML =
    '<div class="field"><label for="dk-summary">Summary <span class="req" aria-hidden="true">*</span></label>' +
    '<textarea id="dk-summary" maxlength="700" rows="5"></textarea>' +
    '<span class="hint">Shown in search results and used as the Substack lead. ' +
    '<span id="dk-sum-n" class="num"></span></span></div>' +
    '<div class="field"><label for="dk-slug">Web Address <span class="req" aria-hidden="true">*</span></label>' +
    '<div class="dk-slug-row"><span class="muted">' + escapeHtml(pubBase()) + '/</span><input type="text" id="dk-slug" maxlength="80"' +
    (locked ? " readonly" : "") + "></div>" +
    (locked ? '<span class="hint">Fixed since first publication: citations point at it.</span>'
      : '<span class="hint">Lower-case words joined by hyphens. <button type="button" class="linkish" id="dk-slug-title">Use the title</button></span>') +
    "</div>" +
    '<div class="field"><span class="dk-flabel">Authors <span class="req" aria-hidden="true">*</span></span>' +
    '<div id="dk-authors" class="dk-authors"></div>' +
    '<span class="hint">Members of ' + escapeHtml(S.article.publication ? S.article.publication.name : "the publication") +
    '. Its owners add people on the <a href="' + escapeHtml(listUrl().replace(/#\/p\/(\d+)$/, "#/p/$1/members")) + '">Members</a> page.</span></div>' +
    '<p class="dk-sub-h">Homepage</p>' +
    '<div class="field"><label for="dk-market">Market <span class="req" aria-hidden="true">*</span></label>' +
    '<select id="dk-market"><option value="">Choose…</option>' + MARKETS.map(function (m) {
      return '<option value="' + m[0] + '">' + m[1] + "</option>";
    }).join("") + "</select></div>" +
    '<div class="field"><span class="dk-flabel">Topics <span class="muted">up to 3</span></span>' +
    '<div class="dk-topics">' + TOPICS.map(function (t) {
      return '<label><input type="checkbox" name="dk-topic" value="' + t[0] + '"> ' + t[1] + "</label>";
    }).join("") + "</div></div>" +
    '<div class="field"><label for="dk-lead">Homepage Chart</label><select id="dk-lead"></select>' +
    '<span class="hint">Shown beside the note on the PloverResearch homepage.</span></div>' +
    '<label class="dk-check"><input type="checkbox" id="dk-feature"> Offer it as Chart of the Week</label>' +
    '<p class="dk-words" id="dk-words"></p>';
  $("#dk-summary").value = d.summary || "";
  $("#dk-market").value = d.market || "";
  $all('input[name="dk-topic"]').forEach(function (x) { x.checked = (d.topics || []).indexOf(x.value) !== -1; });
  $("#dk-feature").checked = !!d.feature;
  S.leadChoice = d.lead || "";
  fillLeadOptions();
  $("#dk-market").addEventListener("change", changed);
  $("#dk-lead").addEventListener("change", function () { S.leadChoice = $("#dk-lead").value; changed(); });
  $("#dk-feature").addEventListener("change", changed);
  $all('input[name="dk-topic"]').forEach(function (x) {
    x.addEventListener("change", function () {
      var on = $all('input[name="dk-topic"]').filter(function (y) { return y.checked; });
      if (on.length > 3) { x.checked = false; toast("Choose up to three topics."); return; }
      changed();
    });
  });
  $("#dk-slug").value = (locked ? a.slug : d.slug) || "";
  $("#dk-authors").innerHTML = S.authors.map(function (p) {
    return '<label><input type="checkbox" name="dk-author" value="' + p.id + '"' +
      (d.authors.indexOf(p.id) !== -1 ? " checked" : "") + "> " + escapeHtml(p.name) + "</label>";
  }).join("") || '<span class="muted">Nobody writes for this publication yet.</span>';
  var sumN = function () { $("#dk-sum-n").textContent = ($("#dk-summary").value.length) + " / 700"; };
  sumN();
  $("#dk-summary").addEventListener("input", function () { sumN(); changed(); });
  $("#dk-slug").addEventListener("input", function () { changed(); });
  $("#dk-slug").addEventListener("blur", function () {
    var v = slugify($("#dk-slug").value);
    if (v !== $("#dk-slug").value) { $("#dk-slug").value = v; changed(); }
  });
  var st = $("#dk-slug-title");
  if (st) st.addEventListener("click", function () { $("#dk-slug").value = slugify($("#dk-title").value); changed(); });
  $all('input[name="dk-author"]').forEach(function (x) { x.addEventListener("change", changed); });
  updateWordCount();
  renderVersions();
}

function renderVersions() {
  var a = S.article;
  var box = $("#dk-pane-versions");
  var rows = (a.versions || []).map(function (v) {
    return '<li><a href="' + escapeHtml(pubBase()) + "/" + escapeHtml(a.slug) + "/v" + v.version + '" target="_blank" rel="noopener">Version ' +
      v.version + "</a> <span class=\"muted num\">" + escapeHtml(fmtStamp(v.published_at)) + "</span>" +
      '<span class="dk-v-note">' + escapeHtml(v.change_note) + " — " + escapeHtml(v.published_by) + "</span></li>";
  }).join("");
  var status = a.status === "withdrawn"
    ? '<div class="dk-banner warn inline">Withdrawn on ' + escapeHtml(fmtStamp(a.withdrawn_at)) + ": " +
      escapeHtml(a.withdrawn_reason || "") + "</div>"
    : "";
  box.innerHTML = status +
    (rows ? '<ol class="dk-versions">' + rows + "</ol>" : '<p class="muted">Not published yet.</p>') +
    (a.status === "published" && canDo("publish")
      ? '<div class="field"><label for="dk-wd">Withdraw</label><input type="text" id="dk-wd" maxlength="400" ' +
        'placeholder="Reason readers will see"><button type="button" class="btn btn-danger" id="dk-wd-btn">Withdraw Article</button>' +
        '<span class="hint">Takes the page down and shows the reason instead. Versions stay stored.</span></div>'
      : "") +
    (a.status === "withdrawn" && canDo("publish") ? '<button type="button" class="btn" id="dk-reinstate">Reinstate Article</button>' : "") +
    (!a.published_version && canDo("delete") ? '<div class="field"><button type="button" class="btn btn-danger" id="dk-delete">Delete Draft</button>' +
      '<span class="hint">Only possible before the first publication.</span></div>' : "");
  var wd = $("#dk-wd-btn");
  if (wd) wd.addEventListener("click", function () {
    send("POST", "/research/articles/" + ID + "/withdraw", { reason: $("#dk-wd").value }).then(function (res) {
      S.article = Object.assign(S.article, res);
      renderVersions();
      renderBar();
      toast("Withdrawn. The page now shows the reason.");
    }).catch(function (err) { toast(err.message); });
  });
  var ri = $("#dk-reinstate");
  if (ri) ri.addEventListener("click", function () {
    send("POST", "/research/articles/" + ID + "/reinstate").then(function (res) {
      S.article = Object.assign(S.article, res);
      renderVersions();
      renderBar();
      toast("Reinstated.");
    }).catch(function (err) { toast(err.message); });
  });
  var del = $("#dk-delete");
  if (del) {
    // one click: the article list deletes it with a few seconds to Undo
    del.addEventListener("click", function () {
      try {
        sessionStorage.setItem("plover-delete-draft", JSON.stringify({ id: ID, title: $("#dk-title").value.trim() || "Untitled" }));
      } catch (e) {
        // no session storage: no Undo either, so ask first
        if (!window.confirm("Delete this draft? This cannot be undone.")) return;
        api("/research/articles/" + ID, { method: "DELETE" }).then(function () {
          S.savedSeq = S.seq;
          location.href = listUrl();
        }).catch(function (err) { toast(err.message); });
        return;
      }
      S.savedSeq = S.seq;
      location.href = listUrl();
    });
  }
}

function renderHistory() {
  var box = $("#dk-pane-history");
  box.innerHTML = '<p class="muted">Loading…</p>';
  api("/research/articles/" + ID + "/history").then(function (res) {
    box.innerHTML = '<p class="hint">A copy is kept at most every two minutes per writer, and at every publication.</p>' +
      '<ol class="dk-history">' + res.history.map(function (h) {
        return '<li><span class="num">' + escapeHtml(fmtStamp(h.saved_at)) + "</span> " +
          '<span class="muted">' + escapeHtml(h.saved_by) + "</span>" +
          (h.label ? ' <span class="badge badge-neutral">' + escapeHtml(h.label) + "</span>" : "") +
          ' <button type="button" class="linkish" data-restore="' + h.id + '">Restore</button></li>';
      }).join("") + "</ol>";
    $all("[data-restore]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        api("/research/articles/" + ID + "/history/" + b.getAttribute("data-restore")).then(function (res2) {
          pushUndo("restore");
          renderDraft(res2.draft);
          changed();
          toast("Restored that copy. It saves as a new revision.", true);
        }).catch(function (err) { toast(err.message); });
      });
    });
  }).catch(function (err) { box.innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>"; });
}

function renderChecklist() {
  var box = $("#dk-pane-check");
  box.innerHTML = '<p class="muted">Checking…</p>';
  saveNow().then(function () { return api("/research/articles/" + ID + "/check"); })
    .then(renderCheckPane)
    .catch(function (err) { box.innerHTML = '<p class="bad">' + escapeHtml(err.message) + "</p>"; });
}

function showPane(name) {
  $all(".dk-tab").forEach(function (t) { t.setAttribute("aria-selected", String(t.getAttribute("data-pane") === name)); });
  $all(".dk-pane").forEach(function (p) { p.hidden = p.id !== "dk-pane-" + name; });
  if (name === "history") renderHistory();
  if (name === "research") renderResearchPane();
  if (name === "ai") renderAIPane();
  if (name === "check") renderChecklist();
  if (name === "versions") renderVersions();
}

/* ---------------------------------------------------------------- publish, Substack */

function openPublish() {
  var dlg = $("#dk-publish");
  var body = $("#dk-publish-body");
  body.innerHTML = '<p class="muted">Saving and checking…</p>';
  dlg.showModal();
  saveNow().then(function () { return api("/research/articles/" + ID + "/check"); }).then(function (res) {
    var first = res.version === 1;
    if (res.problems.length) {
      body.innerHTML = "<h2>Not Ready to Publish</h2><ul class=\"dk-problems\">" +
        res.problems.map(function (p) { return "<li>" + escapeHtml(p) + "</li>"; }).join("") + "</ul>" +
        '<div class="dk-dlg-acts"><button type="button" class="btn" data-close>Back to Editing</button></div>';
      return;
    }
    body.innerHTML =
      "<h2>Publish Version " + res.version + "</h2>" +
      "<p>" + (first
        ? "The article goes live at <strong>" + escapeHtml(res.url) + "</strong>, credited to " +
          escapeHtml(res.authors.join(", ")) + "."
        : "Version " + res.version + " replaces the current page. Earlier versions stay at their own addresses.") + "</p>" +
      "<p class=\"hint\">Every chart’s data is frozen as it stands today. A published version cannot be changed; a correction is a new version.</p>" +
      (first ? "" : '<div class="field"><label for="dk-change">What Changed <span class="req" aria-hidden="true">*</span></label>' +
        '<textarea id="dk-change" rows="3" maxlength="400" placeholder="Shown to readers, e.g. Corrected the FY2024 figure in Table 1."></textarea></div>') +
      '<p class="dk-dlg-err bad" hidden></p>' +
      '<div class="dk-dlg-acts"><button type="button" class="btn" data-close>Cancel</button>' +
      '<button type="button" class="btn btn-primary" id="dk-do-publish">Publish Version ' + res.version + "</button></div>";
    $("#dk-do-publish").addEventListener("click", function () {
      var btn = $("#dk-do-publish");
      btn.disabled = true;
      btn.textContent = "Publishing…";
      send("POST", "/research/articles/" + ID + "/publish", {
        base_revision: S.base, change_note: first ? "" : ($("#dk-change").value || ""),
      }).then(function (done) {
        body.innerHTML = "<h2>Published</h2><p>Version " + done.version + " is live at " +
          '<a href="' + escapeHtml(done.url) + '" target="_blank" rel="noopener">' + escapeHtml(done.url) + "</a>.</p>" +
          '<p class="hint">Search engines have been notified, and a backup runs in a minute.</p>' +
          '<div class="dk-dlg-acts"><button type="button" class="btn" data-close>Close</button>' +
          '<button type="button" class="btn btn-primary" id="dk-to-substack">Prepare Substack Post</button></div>';
        $("#dk-to-substack").addEventListener("click", function () { dlg.close(); openSubstack(); });
        reload();
      }).catch(function (err) {
        btn.disabled = false;
        btn.textContent = "Publish Version " + res.version;
        var e = $(".dk-dlg-err", body);
        e.hidden = false;
        e.innerHTML = escapeHtml(err.message) + (err.body && err.body.problems
          ? "<ul>" + err.body.problems.map(function (p) { return "<li>" + escapeHtml(p) + "</li>"; }).join("") + "</ul>" : "");
      });
    });
  }).catch(function (err) {
    body.innerHTML = '<p class="bad">' + escapeHtml(err.message) + '</p><div class="dk-dlg-acts"><button type="button" class="btn" data-close>Close</button></div>';
  });
}

function copyRich(html, text, btn) {
  var done = function () { btn.textContent = "Copied"; };
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }),
      "text/plain": new Blob([text], { type: "text/plain" }),
    })]).then(done, function () { navigator.clipboard.writeText(text).then(done); });
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(done);
  }
}

function openSubstack() {
  var dlg = $("#dk-substack");
  var body = $("#dk-substack-body");
  body.innerHTML = '<p class="muted">Loading…</p>';
  dlg.showModal();
  api("/research/articles/" + ID + "/substack").then(function (res) {
    body.innerHTML =
      "<h2>Substack Post</h2>" +
      '<p class="hint">Paste each part into a new Substack post. The post gives the summary and links here for the full note.</p>' +
      '<div class="dk-sub-row"><span class="dk-flabel">Title</span><p>' + escapeHtml(res.title) + '</p><button type="button" class="btn" data-copy="title">Copy</button></div>' +
      (res.subtitle ? '<div class="dk-sub-row"><span class="dk-flabel">Subtitle</span><p>' + escapeHtml(res.subtitle) + '</p><button type="button" class="btn" data-copy="subtitle">Copy</button></div>' : "") +
      '<div class="dk-sub-row"><span class="dk-flabel">Body</span><div class="dk-sub-body">' + res.html + '</div><button type="button" class="btn btn-primary" data-copy="body">Copy Body</button></div>' +
      (res.charts.length ? '<div class="dk-sub-row"><span class="dk-flabel">Charts</span><p class="hint">Images of the charts as published, with the source line, to place in the post.</p><div class="dk-sub-charts">' +
        res.charts.map(function (c, i) {
          return '<button type="button" class="btn" data-chart="' + i + '">' + escapeHtml(c.title || "Chart") + " — Image</button>";
        }).join("") + "</div></div>" : "") +
      '<div class="dk-dlg-acts"><button type="button" class="btn" data-close>Close</button></div>';
    $all("[data-copy]", body).forEach(function (b) {
      b.addEventListener("click", function () {
        var k = b.getAttribute("data-copy");
        if (k === "body") copyRich(res.html, res.text, b);
        else navigator.clipboard.writeText(k === "title" ? res.title : res.subtitle).then(function () { b.textContent = "Copied"; });
      });
    });
    $all("[data-chart]", body).forEach(function (b) {
      b.addEventListener("click", function () {
        // Drawn from the frozen data of the published version, not the draft.
        var entry = res.charts[Number(b.getAttribute("data-chart"))];
        var off = document.createElement("div");
        off.style.cssText = "position:fixed;left:-99999px;width:900px;height:420px";
        document.body.appendChild(off);
        var ch = researchChart.draw(off, entry.block, entry.snap);
        ch.exportPNG((res.url.split("/").pop() || "chart") + "-" + (Number(b.getAttribute("data-chart")) + 1) + ".png");
        setTimeout(function () { ch.dispose(); off.remove(); }, 60000);
      });
    });
  }).catch(function (err) {
    body.innerHTML = '<p class="bad">' + escapeHtml(err.message) + '</p><div class="dk-dlg-acts"><button type="button" class="btn" data-close>Close</button></div>';
  });
}

function reload() {
  return api("/research/articles/" + ID).then(function (a) {
    S.article = a;
    S.base = a.revision;
    renderBar();
    renderVersions();
  });
}

/* ---------------------------------------------------------------- render */

function renderBar() {
  var a = S.article;
  var state = a.status === "published"
    ? '<span class="badge badge-ok">Published v' + a.published_version + "</span>" +
      (a.unpublished_changes ? ' <span class="badge badge-info">Changes Not Published</span>' : "")
    : a.status === "withdrawn" ? '<span class="badge badge-warn">Withdrawn</span>'
      : '<span class="badge badge-neutral">Draft</span>';
  $("#dk-state").innerHTML = state;
  $("#dk-crumb-title").textContent = $("#dk-title").value || "Untitled";
  $("#dk-sub-btn").hidden = !a.published_version;
  $("#dk-view").hidden = !(a.published_version && a.status === "published");
  if (a.published_version) $("#dk-view").href = pubBase() + "/" + a.slug;
  var pb = $("#dk-publish-btn");
  if (!canDo("publish")) {
    // a writer hands the article to the publication's editors instead
    pb.textContent = a.review_requested_at ? "Asked to Publish" : "Ask to Publish";
    pb.title = a.review_requested_at
      ? "Sent to the editors on " + fmtStamp(a.review_requested_at) + " UTC. Ask again after more changes."
      : "Writers do not publish: this tells the publication's editors it is ready.";
  }
}

function renderBlocks(blocks) {
  Object.keys(S.charts).forEach(disposeChart);
  S.models = {};
  var box = blocksEl();
  box.innerHTML = "";
  (blocks && blocks.length ? blocks : [{ type: "p", html: "" }]).forEach(function (b) {
    box.appendChild(makeBlock(b));
  });
  updateCounts();
  updateWordCount();
}

function renderDraft(d) {
  $("#dk-title").value = d.title || "";
  $("#dk-dek").value = d.dek || "";
  autoGrow($("#dk-title"));
  autoGrow($("#dk-dek"));
  S.article.draft = d;
  S.plan = d.plan || null;
  S.notes = (d.notes || []).map(function (n) { return Object.assign({}, n); });
  renderSide();
  renderBlocks(d.blocks);
  renderBar();
  planBar();
  refreshResearch();
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

function shell() {
  ROOT.innerHTML =
    '<div class="dk-bar">' +
    '<nav class="dk-crumbs" aria-label="Breadcrumb"><a href="write.html">Writer Desk</a><span aria-hidden="true">/</span>' +
    '<a href="' + escapeHtml(listUrl()) + '">' + escapeHtml(S.article.publication ? S.article.publication.name : "Articles") +
    '</a><span aria-hidden="true">/</span><span id="dk-crumb-title" aria-current="page"></span></nav>' +
    '<span id="dk-state" class="dk-state"></span>' +
    '<span class="dk-spacer"></span>' +
    '<span id="dk-others" class="dk-others" hidden></span>' +
    '<span id="dk-status" class="dk-status" role="status" aria-live="polite"></span>' +
    '<button type="button" class="dk-barbtn theme-toggle">Dark Mode</button>' +
    '<a class="dk-barbtn" id="dk-view" target="_blank" rel="noopener" hidden>View Live</a>' +
    '<button type="button" class="dk-barbtn" id="dk-preview">Preview</button>' +
    '<button type="button" class="dk-barbtn" id="dk-sub-btn" hidden>Substack</button>' +
    '<button type="button" class="dk-publish" id="dk-publish-btn">Publish</button>' +
    "</div>" +
    '<div id="dk-atabs" class="dk-atabs"></div>' +
    '<div id="dk-banners" class="dk-banners"></div>' +
    '<div class="dk-layout">' +
    '<main class="dk-canvas" id="dk-canvas">' +
    '<p class="dk-kicker">' + escapeHtml(S.article.publication ? S.article.publication.name : "PloverResearch") + '</p>' +
    '<textarea id="dk-title" class="dk-title" rows="1" maxlength="200" placeholder="Title" aria-label="Title"></textarea>' +
    '<textarea id="dk-dek" class="dk-dek" rows="1" maxlength="400" placeholder="Standfirst: one sentence under the title" aria-label="Standfirst"></textarea>' +
    '<div id="dk-planbar" class="dk-planbar" hidden></div>' +
    '<div id="dk-blocks" class="dk-blocks rs-body"></div>' +
    '<button type="button" class="dk-addend" id="dk-addend">+ Add a block</button>' +
    '<div id="dk-planbar-end" class="dk-planbar" hidden></div>' +
    "</main>" +
    '<aside class="dk-side" aria-label="Article settings">' +
    '<div class="dk-tabs" role="tablist">' +
    '<button type="button" class="dk-tab" role="tab" data-pane="details" aria-selected="true">Details</button>' +
    '<button type="button" class="dk-tab" role="tab" data-pane="research">Research</button>' +
    '<button type="button" class="dk-tab" role="tab" data-pane="ai">AI</button>' +
    '<button type="button" class="dk-tab" role="tab" data-pane="check">Check</button>' +
    '<button type="button" class="dk-tab" role="tab" data-pane="history">History</button>' +
    '<button type="button" class="dk-tab" role="tab" data-pane="versions">Versions</button>' +
    "</div>" +
    '<div class="dk-pane" id="dk-pane-details" role="tabpanel"></div>' +
    '<div class="dk-pane" id="dk-pane-research" role="tabpanel" hidden></div>' +
    '<div class="dk-pane" id="dk-pane-ai" role="tabpanel" hidden></div>' +
    '<div class="dk-pane" id="dk-pane-check" role="tabpanel" hidden></div>' +
    '<div class="dk-pane" id="dk-pane-history" role="tabpanel" hidden></div>' +
    '<div class="dk-pane" id="dk-pane-versions" role="tabpanel" hidden></div>' +
    "</aside></div>" +
    '<div id="dk-toast" class="dk-toast" role="status" hidden></div>' +
    '<dialog id="dk-publish" class="dk-dialog"><div id="dk-publish-body"></div></dialog>' +
    '<dialog id="dk-substack" class="dk-dialog wide"><div id="dk-substack-body"></div></dialog>' +
    '<dialog id="dk-capture" class="dk-dialog wide"><div id="dk-capture-body"></div></dialog>';
}

function wire() {
  var box = blocksEl();
  box.addEventListener("keydown", onKeyDown);
  document.addEventListener("keydown", function (e) {
    if (!blockOf(e.target)) onKeyDown(e);
    if (e.key === "Escape") { bmenuClose(); slashClose(); toolbar.hide(); }
  });
  box.addEventListener("input", onInput);
  box.addEventListener("change", onChange);
  box.addEventListener("click", onClick);
  box.addEventListener("paste", onPaste);
  box.addEventListener("dragstart", onDragStart);
  // a note dragged from Research › Notes, or a screenshot dropped on a planned chart
  box.addEventListener("dragover", function (e) {
    if (noteDragOver(e)) return;
    var ph = e.target.closest && e.target.closest(".dk-ph-chart");
    if (ph && !drag.el && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") !== -1) {
      e.preventDefault();
      ph.classList.add("over");
    }
  });
  box.addEventListener("dragleave", function (e) {
    var ph = e.target.closest && e.target.closest(".dk-ph-chart");
    if (ph) ph.classList.remove("over");
  });
  box.addEventListener("drop", function (e) {
    if (noteDrop(e)) { e.stopImmediatePropagation(); return; }
    if (placeholderDrop(e)) e.stopImmediatePropagation();
  });
  document.addEventListener("paste", placeholderPaste, true);
  box.addEventListener("dragover", onDragOver);
  box.addEventListener("drop", onDrop);
  box.addEventListener("dragend", onDragEnd);
  box.addEventListener("focusin", function (e) {
    if (e.target.tagName === "TD") S.cell = e.target;
  });
  // image drop zones
  box.addEventListener("dragover", function (e) {
    var dz = e.target.closest && e.target.closest(".dk-drop");
    if (dz && !drag.el) { e.preventDefault(); dz.classList.add("over"); }
  });
  box.addEventListener("dragleave", function (e) {
    var dz = e.target.closest && e.target.closest(".dk-drop");
    if (dz) dz.classList.remove("over");
  });
  box.addEventListener("drop", function (e) {
    var dz = e.target.closest && e.target.closest(".dk-drop");
    if (!dz || drag.el) return;
    e.preventDefault();
    dz.classList.remove("over");
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadImage(blockOf(dz), f);
  });

  document.addEventListener("selectionchange", function () { toolbar.update(); rememberCaret(); });
  document.addEventListener("mousedown", function (e) {
    if (bmenu.el && !bmenu.el.hidden && !bmenu.el.contains(e.target) && !e.target.closest(".dk-handle")) bmenuClose();
    if (slash.el && !slash.el.hidden && !slash.el.contains(e.target) && !e.target.closest(".dk-add")) slashClose();
    notes.outside(e);
    $all(".dk-results").forEach(function (r) {
      if (!r.hidden && !r.parentNode.contains(e.target)) r.hidden = true;
    });
  });
  window.addEventListener("scroll", function () { toolbar.update(); }, true);

  ["dk-title", "dk-dek"].forEach(function (id) {
    var ta = document.getElementById(id);
    ta.addEventListener("input", function () {
      autoGrow(ta);
      if (id === "dk-title") $("#dk-crumb-title").textContent = ta.value || "Untitled";
      changed();
    });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        if (id === "dk-title") $("#dk-dek").focus();
        else focusBlock(blocksEl().firstElementChild, false);
      }
    });
  });
  $("#dk-addend").addEventListener("click", function () {
    var last = blocksEl().lastElementChild;
    var lm = last && S.models[last.getAttribute("data-id")];
    if (lm && lm.type === "p" && isEmpty(last.querySelector("[contenteditable]"))) focusBlock(last, false);
    else insertBlock({ type: "p", html: "" }, last, true);
  });
  $all(".dk-tab").forEach(function (t) {
    t.addEventListener("click", function () { showPane(t.getAttribute("data-pane")); });
  });
  $("#dk-preview").addEventListener("click", function () {
    var w = window.open("about:blank", "_blank");
    saveNow().then(function () {
      if (w) w.location = "/admin/api/research/articles/" + ID + "/preview";
    });
  });
  $("#dk-publish-btn").addEventListener("click", function () {
    if (canDo("publish")) openPublish(); else askToPublish();
  });
  $("#dk-sub-btn").addEventListener("click", openSubstack);
  $all("dialog").forEach(function (d) {
    d.addEventListener("click", function (e) {
      if (e.target.closest("[data-close]") || e.target === d) d.close();
    });
  });
}

/* ---------------------------------------------------------------- boot */

function fail(message) {
  ROOT.innerHTML = '<div class="dk-fail"><h1>Research Desk</h1><p>' + message + '</p>' +
    '<p><a class="btn" href="write.html">Back to the Writer Desk</a></p></div>';
}

/* A writer cannot publish: save, then tell the editors it is ready. The
   button stays off until the server answers (lock.js does the same for
   every page). */
function askToPublish() {
  var btn = $("#dk-publish-btn");
  btn.disabled = true;
  btn.textContent = "Sending…";
  saveNow().then(function () {
    return send("POST", "/research/articles/" + ID + "/review");
  }).then(function (res) {
    S.article = Object.assign(S.article, res);
    toast("Sent. The publication's editors see it marked Ready for Review.");
  }).catch(function (err) {
    toast(err.message);
  }).then(function () {
    btn.disabled = false;
    renderBar();
  });
}

function boot() {
  initThemeToggle(function () {
    Object.keys(S.charts).forEach(function (k) { S.charts[k].render(); });
  });
  if (!ID) { fail("No article was given. Open one from the article list."); return; }
  api("/write/me").then(function (s) {
    if (!s.signed_in || !s.person) {
      fail('Sign in to the <a href="write.html">Writer Desk</a> first, then open the article again.');
      return null;
    }
    S.me = s.person;
    if (!s.writer) {
      fail("You are not a member of any publication yet. Ask the owner of the publication you write for to invite you.");
      return null;
    }
    return Promise.all([api("/research/articles/" + ID), api("/research/authors?article_id=" + ID)]);
  }).then(function (res) {
    if (!res) return;
    S.article = res[0];
    S.authors = res[1].authors;
    S.base = S.article.revision;
    shell();
    initThemeToggle(function () {
      Object.keys(S.charts).forEach(function (k) { S.charts[k].render(); });
    });
    wire();
    renderDraft(S.article.draft);
    initArticleTabs();
    setStatus("saved");
    document.title = (S.article.draft.title || "Untitled") + " · Research Desk";
    offerLocalCopy();
    capturePending();
    presence();
    setInterval(presence, PRESENCE_EVERY);
    if (!S.article.draft.title) $("#dk-title").focus();
  }).catch(function (err) {
    fail(escapeHtml(err.status === 404 ? "This article does not exist, or was deleted." : err.message));
  });
}

/* Edits that never reached the server (a closed tab, a lost connection) are
   kept in this browser; offer them back when they differ from the saved draft. */
function offerLocalCopy() {
  var raw = null;
  try { raw = localStorage.getItem(backupKey()); } catch (e) { return; }
  if (!raw) return;
  var local;
  try { local = JSON.parse(raw); } catch (e) { clearBackup(); return; }
  var same = JSON.stringify(local.draft.blocks) === JSON.stringify(serialize().blocks) &&
    local.draft.title === S.article.draft.title;
  if (same) { clearBackup(); return; }
  showBanner("warn", "Changes from this browser at " + escapeHtml(fmtStamp(local.at)) +
    " UTC were not saved. " +
    '<button type="button" class="btn" data-local="restore">Restore Them</button> ' +
    '<button type="button" class="btn" data-local="discard">Discard</button>', "local");
  $all("[data-local]").forEach(function (b) {
    b.addEventListener("click", function () {
      hideBanner("local");
      if (b.getAttribute("data-local") === "restore") {
        pushUndo("local");
        renderDraft(local.draft);
        changed();
      } else clearBackup();
    });
  });
}

boot();
