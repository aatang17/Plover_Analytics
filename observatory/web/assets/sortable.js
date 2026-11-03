/* Click-to-sort for the platform's data tables.

   The Item Explorer already had this behaviour written inline; this is the
   same convention factored out so every other table can adopt it without
   restating it — same markup (`th.sortable[data-key]`, a `.arrow` span),
   same interaction (click a column to rank by it, click again to reverse),
   same `aria-sort`. The CSS it needs is already in app.css.

   What it adds over an inline version, and the reason it is worth sharing:

   - **Missing values never sort as zero.** They collect at the bottom in
     both directions. A market with no published figure is not the smallest
     one, and reversing the sort must not parade the gaps to the top.
   - **Ties keep their source order**, so a re-sort on a column with many
     equal values doesn't shuffle rows that did not move.
   - Numeric columns open on the largest value, because "which is biggest"
     is what a ranking is nearly always asked; text columns open A–Z.

   Deliberately generic: a page builds its own rows and markup, and nothing
   here knows what a market or a maturity is. */
"use strict";

/* Compare two values for a sort, gaps always last. dir is "asc" | "desc". */
function compareForSort(a, b, dir) {
  const aMissing = a === null || a === undefined ||
    (typeof a === "number" && Number.isNaN(a));
  const bMissing = b === null || b === undefined ||
    (typeof b === "number" && Number.isNaN(b));
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  let cmp;
  if (typeof a === "string" || typeof b === "string") {
    cmp = String(a).toLowerCase().localeCompare(String(b).toLowerCase());
  } else {
    cmp = a - b;
  }
  return dir === "asc" ? cmp : -cmp;
}

/* Sort row objects by key/dir. Returns a new array; the sort is stable. */
function sortRows(rows, key, dir) {
  if (!key) return rows.slice();
  return rows
    .map((row, i) => ({ row: row, i: i }))
    .sort((x, y) => compareForSort(x.row[key], y.row[key], dir) || (x.i - y.i))
    .map(d => d.row);
}

/* The direction a column should open in when first chosen. */
function defaultSortDir(type) {
  return type === "text" ? "asc" : "desc";
}

/* Header row from column definitions, in the Item Explorer's markup.

   cols: [{key, label, num, type: "num"|"text", nosort, title}]
   A column with no key, or nosort, renders as a plain header. */
function sortableHead(cols, key, dir) {
  return "<tr>" + cols.map(c => {
    const sortable = c.key && !c.nosort;
    const active = sortable && c.key === key;
    const cls = [c.num ? "num" : "", sortable ? "sortable" : ""]
      .filter(Boolean).join(" ");
    const arrow = active
      ? '<span class="arrow">' + (dir === "asc" ? "▲" : "▼") + "</span>" : "";
    return '<th scope="col"' + (cls ? ' class="' + cls + '"' : "") +
      (sortable ? ' data-key="' + escapeHtml(c.key) +
        '" data-type="' + (c.type || "num") + '"' : "") +
      (active ? ' aria-sort="' +
        (dir === "asc" ? "ascending" : "descending") + '"' : "") +
      (c.title ? ' title="' + escapeHtml(c.title) + '"' : "") +
      ">" + escapeHtml(c.label) + arrow + "</th>";
  }).join("") + "</tr>";
}

/* Wire the headers inside `root`. onSort(key, dir) fires on each click.

   Re-wiring after a re-render is safe: the listeners were attached to the
   header cells that the re-render just replaced. */
function wireSort(root, currentKey, currentDir, onSort) {
  if (!root) return;
  Array.prototype.forEach.call(root.querySelectorAll("th.sortable"), th => {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-key");
      const dir = key === currentKey
        ? (currentDir === "desc" ? "asc" : "desc")
        : defaultSortDir(th.getAttribute("data-type"));
      onSort(key, dir);
    });
  });
}

/* --- progressive enhancement for tables the page has already rendered -------

   Everything above works on row *objects*, which suits a page that builds its
   rows from data. Most tables on this platform build their markup as a string
   instead, and retrofitting each one to the object API would mean rewriting a
   dozen renderers. This half works on the rendered DOM: give it a table and it
   makes every column sortable and adds a filter box, whatever built the rows.

   It is deliberately conservative about types. A column is numeric only if
   the cells that carry a value all parse as numbers, so a column of company
   names is never sorted as though "3M" were three. Values come from a cell's
   `data-sort` when it has one — always prefer that for anything the eye reads
   differently from the machine (a date, a yen figure with a unit suffix). */

/* "−563,048" -> -563048 · "+0.54%" -> 0.54 · "—" -> null · "Tokyo" -> NaN */
function cellSortValue(td) {
  const explicit = td.getAttribute("data-sort");
  if (explicit !== null) {
    const n = Number(explicit);
    return explicit.trim() === "" ? null : (Number.isNaN(n) ? explicit : n);
  }
  const raw = (td.textContent || "").trim();
  if (raw === "" || raw === MISSING || raw === "-") return null;
  // strip grouping, the true minus, a leading plus, and a trailing unit
  const cleaned = raw
    .replace(/−/g, "-")
    .replace(/,/g, "")
    .replace(/^\+/, "")
    .replace(/\s*(%|pp|persons|households|人|世帯)$/i, "")
    .trim();
  if (cleaned === "" || !/^-?\d*\.?\d+$/.test(cleaned)) return raw;
  return Number(cleaned);
}

function columnIsNumeric(rows, index) {
  let seen = 0;
  for (const tr of rows) {
    const td = tr.cells[index];
    if (!td) continue;
    const v = cellSortValue(td);
    if (v === null) continue;
    if (typeof v !== "number") return false;
    seen++;
  }
  return seen > 0;
}

/* The row of <th> a reader clicks: the last one, so a table with a grouping
   band above its column names attaches to the names. */
function headerCells(table) {
  const head = table.tHead;
  if (!head || !head.rows.length) return [];
  const row = head.rows[head.rows.length - 1];
  return Array.prototype.slice.call(row.cells);
}

function bodyRows(table) {
  const out = [];
  Array.prototype.forEach.call(table.tBodies, tb => {
    Array.prototype.forEach.call(tb.rows, tr => {
      // A row that spans the table is a note or a sub-table, not a record.
      if (tr.cells.length && tr.cells[0].colSpan > 1) return;
      out.push(tr);
    });
  });
  return out;
}

/* Apply the stored query and sort to the live DOM. */
function applyTableState(state) {
  const table = state.table;
  const rows = bodyRows(table);
  rows.forEach((tr, i) => { if (tr._srcIndex === undefined) tr._srcIndex = i; });

  const q = state.query.trim().toLowerCase();
  let shown = 0;
  rows.forEach(tr => {
    const hit = !q || (tr.textContent || "").toLowerCase().indexOf(q) >= 0;
    tr.hidden = !hit;
    if (hit) shown++;
  });

  if (state.sortIndex !== null) {
    const dir = state.sortDir;
    const idx = state.sortIndex;
    const ordered = rows.slice().sort((a, b) => {
      const av = a.cells[idx] ? cellSortValue(a.cells[idx]) : null;
      const bv = b.cells[idx] ? cellSortValue(b.cells[idx]) : null;
      return compareForSort(av, bv, dir) || (a._srcIndex - b._srcIndex);
    });
    const tb = table.tBodies[0];
    ordered.forEach(tr => tb.appendChild(tr));
  }

  // Only touch the sort indicators on a table whose sort we own. A page that
  // sorts its own rows draws its own arrow, and stripping it would leave the
  // reader with no idea which column the table is ranked by.
  if (!state.pageSorted) headerCells(table).forEach((th, i) => {
    const arrow = th.querySelector(".arrow");
    if (arrow) arrow.remove();
    if (i === state.sortIndex) {
      th.setAttribute("aria-sort", state.sortDir === "asc" ? "ascending" : "descending");
      const span = document.createElement("span");
      span.className = "arrow";
      span.textContent = state.sortDir === "asc" ? "▲" : "▼";
      th.appendChild(span);
    } else {
      th.removeAttribute("aria-sort");
    }
  });

  if (state.count) {
    state.count.textContent = q
      ? shown + " of " + rows.length + " rows"
      : rows.length + " rows";
  }
  if (state.empty) {
    state.empty.hidden = shown !== 0 || !q;
    if (shown === 0 && q) {
      state.empty.textContent = "No rows match “" + state.query.trim() + "”.";
    }
  }
}

/* --- export: the table as the reader has it --------------------------------

   Tables here get read into research notes, spreadsheets and slides, and
   retyping one is where wrong numbers come from. Two routes, both on the
   toolbar every table already has:

   - **Copy table** writes TSV *and* HTML to the clipboard, so a paste lands
     as cells in Excel and as a real editable table in Word or PowerPoint —
     not as a picture that cannot be checked or corrected.
   - **Download CSV** writes the same rows with a provenance header block.

   Both follow the screen: the current filter, the current sort, the columns
   as displayed. An export that disagreed with the table above it would be
   worse than no export at all. A missing value exports empty, never zero. */

/* The visible text of a header or cell, without the sort arrow, the
   screen-reader-only labels, or any control that happens to sit in it. */
function exportText(node) {
  const explicit = node.getAttribute && node.getAttribute("data-export");
  if (explicit !== null && explicit !== undefined) return explicit.trim();
  const clone = node.cloneNode(true);
  Array.prototype.forEach.call(
    clone.querySelectorAll(".arrow, .visually-hidden, svg, button, input, select"),
    n => n.remove());
  // A cell that stacks two lines — an English name over the filed Japanese —
  // has no whitespace between them in the markup, and textContent would run
  // them together as "All items総合".
  Array.prototype.forEach.call(clone.querySelectorAll("div, p, li, br"),
    n => n.insertAdjacentText("afterend", " "));
  return (clone.textContent || "").replace(/\s+/g, " ").trim();
}

/* The heading a reader would cite this table by. */
function tableTitle(table) {
  const explicit = table.getAttribute("data-export-name");
  if (explicit) return explicit;
  const caption = table.querySelector("caption");
  if (caption) return exportText(caption);
  const section = table.closest("section, article");
  const heading = section && section.querySelector("h1, h2, h3, h4");
  if (heading) {
    const clone = heading.cloneNode(true);
    Array.prototype.forEach.call(clone.querySelectorAll(".h2-note"), n => n.remove());
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  }
  return document.title;
}

/* The nearest source line above the table: climb until an ancestor has one,
   so a page with several sourced sections cites the right section. */
function tableSourceLine(table) {
  let node = table.parentElement;
  let found = null;
  while (node && node !== document.body && !found) {
    found = node.querySelector(".source-line");
    node = node.parentElement;
  }
  // A page whose table sits outside any sourced section — the Item Explorer,
  // whose source line belongs to the detail panel beside it — still has one
  // source, and citing it beats exporting the table with none.
  if (!found) {
    const all = document.querySelectorAll(".source-line");
    found = all.length ? all[all.length - 1] : null;
  }
  // Last resort: the page's own credit. Every surface here carries one, so an
  // export should never leave without saying where the numbers came from.
  if (!found) found = document.querySelector(".site-footer .inner");
  if (!found) return "";
  const clone = found.cloneNode(true);
  Array.prototype.forEach.call(clone.querySelectorAll("a[href$='methodology.html']"),
    n => n.remove());
  return (clone.textContent || "").replace(/\s+/g, " ").trim();
}

function exportSlug(text) {
  const slug = (text || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return slug || "table";
}

function tableProvenance(table) {
  return {
    title: tableTitle(table),
    source: tableSourceLine(table),
    url: window.location.href,
    at: new Date().toISOString().slice(0, 19) + "Z",
  };
}

/* Columns and rows, split into what the eye reads and what a spreadsheet
   needs. `text` keeps the page's formatting — grouped thousands, the em-dash
   for a gap; `value` is the bare number, or empty where nothing is published.
   Never the two confused: a gap that exported as 0 would be a wrong number in
   someone's model. */
/* Every visible row, band rows included. bodyRows() drops a row that spans the
   table because it is not a record to be sorted — but it is what separates one
   block of a table from another, and a public-finance table whose "General
   account" and "Reference" bands were flattened into one list would invite
   exactly the addition its own note forbids. */
function exportRowsOf(table) {
  const out = [];
  Array.prototype.forEach.call(table.tBodies, tb => {
    Array.prototype.forEach.call(tb.rows, tr => { if (!tr.hidden) out.push(tr); });
  });
  return out;
}

function isBandRow(tr) {
  return !!(tr.cells.length && tr.cells[0].colSpan > 1);
}

function tableExportData(table) {
  const rows = bodyRows(table).filter(tr => !tr.hidden);
  // A sparkline or an actions column carries nothing a spreadsheet can hold,
  // and exporting it as a column of blanks would read as missing data rather
  // than as a column that never had any.
  const carriesData = i => rows.some(tr => {
    const td = tr.cells[i];
    return td && !td.querySelector("svg, button, input, select") &&
      exportText(td) !== "";
  });
  const cols = [];
  headerCells(table).forEach((th, i) => {
    if (rows.length && !carriesData(i)) return;
    cols.push({
      index: i,
      label: exportText(th),
      num: th.classList.contains("num") || columnIsNumeric(rows, i),
    });
  });
  const body = exportRowsOf(table).map(tr => cols.map((col, i) => {
    if (isBandRow(tr)) {
      const label = i === 0 ? exportText(tr.cells[0]) : "";
      return { text: label, value: label };
    }
    const td = tr.cells[col.index];
    if (!td) return { text: "", value: "" };
    const text = exportText(td);
    // A gap exports empty. Never as a dash a spreadsheet would read as text,
    // and never as zero.
    if (text === "" || text === MISSING || text === "-" || text === "\u2013") {
      return { text: text, value: "" };
    }
    const num = exportNumber(text);
    return { text: text, value: num === null ? text : num };
  }));
  return { cols: cols, rows: body };
}

/* The displayed figure as a spreadsheet would want it: grouping separators and
   the true minus stripped, everything else left alone. Returned as a string so
   the column keeps the precision it is printed at — "102.40" does not become
   102.4, and an item code "0001" does not become 1.

   Deliberately NOT cellSortValue(): that prefers a cell's `data-sort`, which
   pages set to the raw underlying figure so a column sorts correctly. Exporting
   it would put a raw-yen number under a "¥bn" header — a wrong number in
   someone's model. What is on screen is what leaves. */
function exportNumber(text) {
  const cleaned = text
    .replace(/\u2212/g, "-")
    .replace(/,/g, "")
    .replace(/^\+/, "")
    .replace(/\s*(%|pp|\u00d7)$/i, "")
    .trim();
  return /^-?\d*\.?\d+$/.test(cleaned) && cleaned !== "" ? cleaned : null;
}

function csvCell(value) {
  return /[",\n]/.test(value) ? '"' + value.replace(/"/g, '""') + '"' : value;
}

function tableCSV(table) {
  const data = tableExportData(table);
  const p = tableProvenance(table);
  const lines = [
    "# " + p.title,
    p.source ? "# " + p.source : null,
    "# Retrieved: " + p.at,
    "# View: " + p.url,
    "# Values as shown on the page. An empty cell is not published, never zero.",
    data.cols.map(c => csvCell(c.label)).join(","),
  ].filter(l => l !== null);
  data.rows.forEach(r => lines.push(r.map(c => csvCell(c.value)).join(",")));
  return lines.join("\n") + "\n";
}

/* Tab-separated, with the citation *after* the data: a paste lands the table
   at the cursor with its column names on the first row, and the provenance
   trails below where it can be kept or deleted. */
function tableTSV(table) {
  const data = tableExportData(table);
  const p = tableProvenance(table);
  const lines = [data.cols.map(c => c.label.replace(/\t/g, " ")).join("\t")];
  data.rows.forEach(r => lines.push(r.map(c => c.value.replace(/\t/g, " ")).join("\t")));
  lines.push("");
  lines.push([p.title, p.source, "Retrieved " + p.at, p.url].filter(Boolean).join(" · "));
  return lines.join("\n");
}

/* The clipboard's rich flavour. Word and PowerPoint ignore stylesheets, so
   this is the one place the platform writes literal colours: they are part of
   the exported document, not of any screen this repo styles. */
function tableRichHTML(table) {
  const data = tableExportData(table);
  const p = tableProvenance(table);
  const cell = (tag, text, num, weight) =>
    "<" + tag + ' style="border:1px solid #d7dde5;padding:4px 8px;text-align:' +
    (num ? "right" : "left") + ";" + (weight ? "font-weight:600;" : "") + '">' +
    escapeHtml(text) + "</" + tag + ">";
  const head = data.cols.map(c => cell("th", c.label, c.num, true)).join("");
  const body = data.rows.map(r =>
    "<tr>" + r.map((c, i) => cell("td", c.text, data.cols[i].num, false)).join("") + "</tr>"
  ).join("");
  return '<table style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;' +
    'font-size:10pt"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table>" +
    '<p style="font-family:Arial,Helvetica,sans-serif;font-size:8pt;color:#5b6675">' +
    escapeHtml([p.title, p.source, "Retrieved " + p.at].filter(Boolean).join(" · ")) +
    ' — <a href="' + escapeHtml(p.url) + '">' + escapeHtml(p.url) + "</a></p>";
}

function flashButton(btn, message) {
  if (btn._restore) clearTimeout(btn._restore);
  else btn._was = btn.textContent;
  btn.textContent = message;
  btn._restore = setTimeout(() => {
    btn.textContent = btn._was;
    btn._restore = null;
  }, 1500);
}

function copyTable(table, btn) {
  const tsv = tableTSV(table);
  const done = () => flashButton(btn, "Copied");
  const fail = () => flashButton(btn, "Copy failed");
  // The rich flavour is what makes a paste into a deck an editable table
  // rather than a wall of tab-separated text, so it is tried first.
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    let write;
    try {
      write = navigator.clipboard.write([new ClipboardItem({
        "text/plain": new Blob([tsv], { type: "text/plain" }),
        "text/html": new Blob([tableRichHTML(table)], { type: "text/html" }),
      })]);
    } catch (e) { write = Promise.reject(e); }
    write.then(done, () => copyPlain(tsv, done, fail));
    return;
  }
  copyPlain(tsv, done, fail);
}

/* The clipboard API also rejects when the document is not focused, so the
   textarea route is a fallback for failure, not only for absence. */
function copyPlain(text, done, fail) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => viaSelection(text, done, fail));
  } else {
    viaSelection(text, done, fail);
  }
}

function viaSelection(text, done, fail) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  if (ok) done(); else fail();
}

function downloadTableCSV(table) {
  // A BOM so Excel opens Japanese company and item names as UTF-8 rather than
  // as mojibake; every other tool ignores it.
  const blob = new Blob(["﻿" + tableCSV(table)],
    { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = exportSlug(tableTitle(table)) + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* Give a rendered table click-to-sort and a filter box.

   `table` may be the table or any element containing exactly one. Safe to call
   again after the page re-renders the rows: the toolbar lives outside the
   table, so it survives an innerHTML swap, and the query is re-applied.

   opts: { sort: bool (default true), filter: bool (default true),
           placeholder: string, noun: string } */
function enhanceTable(target, opts) {
  opts = opts || {};
  const table = target && (target.tagName === "TABLE" ? target : target.querySelector("table"));
  if (!table) return null;

  // The toolbar is a sibling of the table's wrapper so that re-rendering the
  // table's innerHTML — which every page here does — cannot destroy it.
  const anchor = table.closest(".table-wrap") || table;
  let state = anchor._tableState;
  if (!state) {
    state = { table: table, query: "", sortIndex: null, sortDir: "desc" };
    anchor._tableState = state;

    // The filter is built whether or not it is wanted yet: a table that grows
    // past the threshold on a later render gains its filter by being shown,
    // not by rebuilding a toolbar the reader may already be typing into.
    const bar = document.createElement("div");
    bar.className = "table-tools";
    const id = "tf-" + Math.random().toString(36).slice(2, 8);
    bar.innerHTML =
      '<label class="visually-hidden" for="' + id + '">Filter rows</label>' +
      '<input type="search" id="' + id + '" class="table-filter" ' +
        'placeholder="' + escapeHtml(opts.placeholder || "Filter rows…") + '" ' +
        'autocomplete="off" spellcheck="false">' +
      '<span class="table-count num"></span>' +
      '<span class="table-export-group">' +
      '<button type="button" class="table-export" data-export-do="copy">Copy table</button>' +
      // A section that already offers its own Download CSV (a richer file,
      // with codes and the filing's header) marks the table data-no-table-csv,
      // so the reader is not shown two buttons that look the same.
      (table.hasAttribute("data-no-table-csv") ? "" :
        '<button type="button" class="table-export" data-export-do="csv">Download CSV</button>') +
      "</span>";
    anchor.parentNode.insertBefore(bar, anchor);
    state.bar = bar;
    state.input = bar.querySelector("input");
    state.count = bar.querySelector(".table-count");
    state.input.addEventListener("input", () => {
      state.query = state.input.value;
      applyTableState(state);
    });

    const empty = document.createElement("p");
    empty.className = "table-empty";
    empty.hidden = true;
    anchor.parentNode.insertBefore(empty, anchor.nextSibling);
    state.empty = empty;

    // Bound to the state, not to the table element: every page here re-renders
    // its rows by replacing innerHTML, and state.table is updated each pass.
    bar.addEventListener("click", e => {
      const btn = e.target.closest("[data-export-do]");
      if (!btn || !state.table) return;
      if (btn.dataset.exportDo === "csv") downloadTableCSV(state.table);
      else copyTable(state.table, btn);
    });

    state.filterAllowed = opts.filter !== false;
  }
  state.table = table;
  if (state.input) state.query = state.input.value;

  // Below the threshold a filter box is clutter over a table the eye already
  // takes in whole; export is worth having on any table worth reading.
  // filterAllowed records a caller's explicit opt-out and never changes; the
  // row count is re-read each pass, so a table that grows past the threshold
  // on a later render gains its filter then.
  const wantFilter = state.filterAllowed &&
    bodyRows(table).length >= AUTO_ENHANCE_MIN_ROWS;
  if (state.input) state.input.hidden = !wantFilter;
  if (state.count) state.count.hidden = !wantFilter;
  if (!wantFilter && state.input && state.input.value) {
    state.input.value = "";
    state.query = "";
  }

  // A page that sorts its own rows marks its headers with `data-key` — both
  // sortableHead() above and the Item Explorer's own header builder do. Adding
  // a DOM sort on top would give those tables two handlers fighting over one
  // click, so the filter is added and the sort is left to the page.
  const pageSorted = headerCells(table).some(th => th.hasAttribute("data-key")) ||
    table.hasAttribute("data-no-enhance-sort");
  state.pageSorted = pageSorted || opts.sort === false;
  if (opts.sort !== false && !pageSorted) {
    const rows = bodyRows(table);
    headerCells(table).forEach((th, i) => {
      if (th.hasAttribute("data-nosort") || th.dataset.enhanced === "1") return;
      // A column where nothing is orderable — a sparkline, an actions cell —
      // must not offer a sort that silently does nothing.
      const orderable = rows.some(tr => tr.cells[i] && cellSortValue(tr.cells[i]) !== null);
      if (!orderable) return;
      th.dataset.enhanced = "1";
      const numeric = columnIsNumeric(rows, i);
      th.classList.add("sortable");
      th.addEventListener("click", () => {
        state.sortDir = state.sortIndex === i
          ? (state.sortDir === "desc" ? "asc" : "desc")
          : defaultSortDir(numeric ? "num" : "text");
        state.sortIndex = i;
        applyTableState(state);
      });
    });
  }

  applyTableState(state);
  return state;
}

/* --- the default, applied without per-table wiring --------------------------

   A page should not have to remember to make its tables sortable. Any table
   with a header row and enough rows to be worth ranking gets the treatment as
   soon as it is in the DOM, and again whenever a page re-renders its rows —
   which every page here does by replacing innerHTML.

   Opt out with `data-no-enhance` on the table. Two thresholds, because the
   behaviours earn their place at different sizes: a filter box is clutter and
   a sort is pointless on a five-row table, but any table a reader would quote
   is one they would rather copy than retype. */

const AUTO_ENHANCE_MIN_ROWS = 6;
const AUTO_EXPORT_MIN_ROWS = 2;
let autoEnhancing = false;

function autoEnhanceTables(root) {
  if (autoEnhancing) return;
  autoEnhancing = true;
  try {
    const scope = root && root.querySelectorAll ? root : document;
    Array.prototype.forEach.call(scope.querySelectorAll("table"), table => {
      if (table.hasAttribute("data-no-enhance")) return;
      if (!table.tHead || !table.tBodies.length) return;
      const rowCount = bodyRows(table).length;
      if (rowCount < AUTO_EXPORT_MIN_ROWS) return;
      enhanceTable(table, {
        sort: rowCount >= AUTO_ENHANCE_MIN_ROWS,
        placeholder: table.getAttribute("data-filter-placeholder") || "Filter rows…",
      });
    });
    // A toolbar whose table has been replaced wholesale is orphaned; drop it
    // rather than leave a filter box wired to nothing.
    Array.prototype.forEach.call(document.querySelectorAll(".table-tools"), bar => {
      const next = bar.nextElementSibling;
      if (!next || !next.querySelector || (!next.matches("table") && !next.querySelector("table"))) {
        bar.remove();
      }
    });
  } finally {
    autoEnhancing = false;
  }
}

/* Watch for tables arriving or being re-rendered. Coalesced to one pass per
   frame: a page that rebuilds five tables in a loop should cost one pass. */
(function watchForTables() {
  if (typeof MutationObserver === "undefined") return;
  const options = { childList: true, subtree: true };
  let queued = false;
  // The pass itself mutates the DOM — it inserts a toolbar and reorders rows —
  // so it must not be observing while it runs, or each pass would schedule the
  // next one and the page would re-sort itself sixty times a second.
  const run = () => {
    queued = false;
    observer.disconnect();
    try {
      autoEnhanceTables(document);
    } finally {
      observer.takeRecords();
      observer.observe(document.body, options);
    }
  };
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    (window.requestAnimationFrame || setTimeout)(run, 16);
  });
  const start = () => {
    autoEnhanceTables(document);
    observer.observe(document.body, options);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
