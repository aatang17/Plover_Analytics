/* Centralised formatters, plus the small shared UI pieces every page needs —
   the trust badge, the theme toggle, the image-export menu. Never format a
   number inline in page code.
   Missing is "—", never 0. Negative uses a true minus (U+2212). */
"use strict";

const MINUS = "−";
const MISSING = "—"; // em dash

/* value -> "1,653.4" with fixed decimals and a true minus */
function fmtNum(v, dp) {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING;
  const s = Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: dp, maximumFractionDigits: dp,
  });
  return (v < 0 ? MINUS : "") + s;
}

/* rate to 1 decimal with unit: "2.7%" */
function fmtRate(v, dp) {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING;
  return fmtNum(v, dp === undefined ? 1 : dp) + "%";
}

/* signed delta: "+0.3 pp" / "−0.2 pp" / "0.0 pp" (zero is unsigned) */
function fmtSigned(v, dp, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING;
  const d = dp === undefined ? 1 : dp;
  const rounded = Number(v.toFixed(d));
  const body = fmtNum(Math.abs(v), d);
  let sign = "";
  if (rounded > 0) sign = "+";
  else if (rounded < 0) sign = MINUS;
  // '%' binds to the number ("+3.2%"); word units get a space ("+0.2 pp")
  return sign + body + (unit ? (unit === "%" ? unit : " " + unit) : "");
}

/* index level to 1 decimal: "113.6" */
function fmtIndex(v) { return fmtNum(v, 1); }

/* "2026-06-01" -> "2026-06" (tables) */
function fmtPeriod(iso) { return iso ? iso.slice(0, 7) : MISSING; }

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

/* "2026-06-01" -> "June 2026" (prose) */
function fmtPeriodLong(iso) {
  if (!iso) return MISSING;
  const m = Number(iso.slice(5, 7));
  return MONTHS[m - 1] + " " + iso.slice(0, 4);
}

/* A company's fiscal year, named by the month it ends as the company does:
   "2025-08-31" -> "FY Aug-2025". Same rule as fiscal.company_year_label. */
function fmtFiscalYear(iso) {
  if (!iso) return MISSING;
  return "FY " + MONTHS[Number(iso.slice(5, 7)) - 1].slice(0, 3) + "-" + iso.slice(0, 4);
}

/* Sort order for fmtFiscalYear labels ("FY Aug-2025", "FY Aug-2025 Q1"): by
   year, then month, then quarter — not alphabetically, which would put
   "FY Dec-2024" before "FY Mar-2024" for a company that changed its year end. */
function cmpFiscalLabel(a, b) {
  const key = l => {
    const m = /^FY (\w{3})-(\d{4})(?: Q(\d))?/.exec(l || "");
    if (!m) return [0, 0, 0];
    return [Number(m[2]), MONTHS.findIndex(x => x.slice(0, 3) === m[1]), Number(m[3] || 0)];
  };
  const ka = key(a), kb = key(b);
  return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2];
}

/* ISO timestamp -> "2026-08-05 12:04 UTC" */
function fmtStamp(iso) {
  if (!iso) return MISSING;
  return iso.slice(0, 16).replace("T", " ") + " UTC";
}

/* raw enum -> Title-Case display label; never render the raw value.
   'derived' carries no label: rates of change are shown with their formula
   ("Show calculation") instead of a badge. */
const TRUST_LABELS = {
  official: "Official Statistic",
  model: "Model Estimate",
};
const TRUST_CLASS = {
  official: "badge-official",
  model: "badge-model",
};

/* returns "" for an unlabelled tier — callers must not pad around this */
function trustBadge(trust) {
  const label = TRUST_LABELS[trust];
  if (!label) return "";
  return '<span class="badge ' + TRUST_CLASS[trust] + '">' + label + "</span>";
}

/* ---- flags on the latest reading ----
   The API sends facts ({id, period, pct, share} / {id, index}); the wording and
   the marker live here. Markers are stable per flag type, so a given glyph means
   the same thing on every row and keeps its meaning when the table is re-sorted;
   the row's own numbers go in the marker tooltip and in the detail panel. */
const NOTE_DEFS = {
  step: {
    marker: "†",
    cols: ["yoy"],  // a statement about the 12-month move only
    label: "One-month step change",
    rule: "A single month accounts for at least 70% of the 12-month move and shifted the " +
          "index by at least 10%. Typical of an administered price, subsidy or tax that " +
          "changes on a set date rather than a move that accumulated over the year.",
    text: function (n) {
      return "A single-month change of " + fmtSigned(n.pct, 1, "%") + " in " +
        fmtPeriodLong(n.period) + " accounts for " + fmtNum(n.share * 100, 0) +
        "% of this 12-month move.";
    },
  },
  low_base: {
    marker: "‡",
    cols: ["yoy", "mom"],  // an unstable base distorts every rate off it
    label: "Index near zero",
    rule: "The index level is below 5.0 (2020 = 100). Percent changes on a base this " +
          "small are arithmetically unstable and are shown for completeness only.",
    text: function (n) {
      return "The index level is " + fmtIndex(n.index) + " (2020 = 100). Percent changes " +
        "on a base this small are arithmetically unstable.";
    },
  },
};

/* markers for one column, always in a fixed-width lane so that a flagged row and
   an unflagged row keep their digits in the same place */
function noteLane(notes, col) {
  const marks = (notes || [])
    .filter(n => NOTE_DEFS[n.id] && NOTE_DEFS[n.id].cols.indexOf(col) !== -1)
    .map(n => {
      const d = NOTE_DEFS[n.id];
      const full = d.label + " — " + d.text(n);
      // the glyph is decorative to a screen reader; the sentence is what carries
      return '<sup class="note-mark" title="' + escapeHtml(full) + '">' +
        '<span aria-hidden="true">' + d.marker + "</span>" +
        '<span class="visually-hidden">' + escapeHtml(full) + "</span></sup>";
    }).join("");
  return '<span class="note-lane">' + marks + "</span>";
}

/* distinct flag types present across rows, in NOTE_DEFS order */
function notesPresent(rows) {
  const seen = {};
  rows.forEach(r => (r.notes || []).forEach(n => { seen[n.id] = true; }));
  return Object.keys(NOTE_DEFS).filter(id => seen[id]);
}

const MEASURE_LABELS = {
  yoy: "Year over Year", mom: "Month over Month",
  ann3m: "3-Month Annualized", index: "Index Level",
};
const MEASURE_SHORT = {
  yoy: "YoY", mom: "MoM", ann3m: "3m Ann.", index: "Index",
};

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* A company no longer listed (app/listing.py): plain words beside its name, on
   the row of any search feed. Its filings stay reachable. */
function delistedTag(c) {
  return c && c.delisted ? ' <span class="co-delisted">Delisted</span>' : "";
}

/* ---- theme ---- */

function currentTheme() {
  const forced = document.documentElement.getAttribute("data-theme");
  if (forced) return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function initThemeToggle(onChange) {
  // explicit ?theme= wins (preview links, tests), then the saved preference
  const qp = new URLSearchParams(location.search).get("theme");
  const saved = qp === "dark" || qp === "light" ? qp : localStorage.getItem("obs-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  const btn = document.querySelector(".theme-toggle");
  if (!btn) return;
  const sync = () => { btn.textContent = currentTheme() === "dark" ? "Light Mode" : "Dark Mode"; };
  sync();
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("obs-theme", next);
    sync();
    if (onChange) onChange(next);
  });
}

/* read a design token's computed value (charts must use this, never hex) */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* ---------- image export: sizes, clipboard, menu ----------------------------

   Charts leave this platform inside other people's documents — a research
   note, a slide, a Substack post — so the export button offers a choice
   rather than one fixed file. Pick the shape once (it is remembered across
   pages and sessions), then copy it straight onto the clipboard or download
   it. Copy is first because an analyst assembling a deck wants the image in
   the slide, not in the downloads folder.

   Each preset fixes a logical canvas and an output width. Font sizes in the
   chart options are absolute pixels, so enlarging the canvas alone would
   shrink every label relative to the image; the pixel ratio does the
   enlarging instead, and the type grows with the plot. */
const EXPORT_SIZES = [
  { key: "report", label: "Report column", w: 1200, h: 560, out: 2400 },
  { key: "slide", label: "Slide, 16:9", w: 1200, h: 675, out: 3200 },
  { key: "half", label: "Half slide", w: 720, h: 540, out: 1600 },
];
const EXPORT_SIZE_KEY = "obs.exportSize";

function exportSize() {
  let key = null;
  try { key = window.localStorage.getItem(EXPORT_SIZE_KEY); } catch (e) { key = null; }
  return EXPORT_SIZES.filter(s => s.key === key)[0] || EXPORT_SIZES[0];
}

function rememberExportSize(key) {
  // Private browsing throws on write; the choice is a convenience, not state
  // anything depends on, so losing it must not break the export.
  try { window.localStorage.setItem(EXPORT_SIZE_KEY, key); } catch (e) { /* ignore */ }
}

/* "2,400 × 1,120 px" — what the file will actually be. */
function exportSizeNote(size) {
  const ratio = size.out / size.w;
  const px = n => Math.round(n).toLocaleString("en-US");
  return px(size.out) + " × " + px(size.h * ratio) + " px";
}

/* A data URL to a Blob, synchronously. Safari only accepts a clipboard write
   inside the gesture that triggered it, and an await — even on a data URL —
   is enough to leave it. */
function pngBlob(dataUrl) {
  const bin = atob(dataUrl.split(",")[1]);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Blob([buf], { type: "image/png" });
}

const CAN_COPY_IMAGE = !!(window.ClipboardItem && navigator.clipboard &&
  navigator.clipboard.write);

function downloadDataURL(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}

/* The element the reader just activated, so a menu can anchor to the button
   that opened it without every call site having to pass the event through. */
let lastPointer = { el: null, at: 0 };
document.addEventListener("mousedown", e => {
  lastPointer = { el: e.target, at: Date.now() };
}, true);

function exportAnchor(fallback) {
  const fresh = lastPointer.el && Date.now() - lastPointer.at < 2000;
  const clicked = fresh && lastPointer.el.closest
    ? lastPointer.el.closest("button, a, [role=button]") : null;
  if (clicked && document.contains(clicked)) return clicked;
  // Safari does not focus a button on click, so this is the keyboard path.
  const active = document.activeElement;
  if (active && active !== document.body && document.contains(active) &&
      active.getBoundingClientRect) return active;
  return fallback || null;
}

let exportMenu = null;

function closeExportMenu() {
  if (!exportMenu) return;
  const m = exportMenu;
  exportMenu = null;
  document.removeEventListener("mousedown", m.onDown, true);
  document.removeEventListener("keydown", m.onKey, true);
  window.removeEventListener("resize", closeExportMenu);
  window.removeEventListener("scroll", closeExportMenu, true);
  m.el.remove();
  if (m.anchor && m.anchor.setAttribute) m.anchor.setAttribute("aria-expanded", "false");
}

/* Position below the anchor, kept inside the viewport and clear of the sticky
   header. Fixed and parented to <body> so no panel's overflow can clip it and
   no later section's stacking context can paint over it. */
function placeExportMenu(el, anchor) {
  const r = anchor.getBoundingClientRect();
  const pad = 8;
  const header = document.querySelector("header");
  const ceiling = header ? header.getBoundingClientRect().bottom + 4 : pad;
  // The menu is wider than the button that opens it, so on the right-hand side
  // of the screen — where every chart's export button sits — it is hung from
  // the button's right edge. Left-aligning there would push it past the panel.
  let left = r.left + r.width / 2 > window.innerWidth / 2
    ? r.right - el.offsetWidth : r.left;
  if (left + el.offsetWidth > window.innerWidth - pad) {
    left = window.innerWidth - pad - el.offsetWidth;
  }
  let top = r.bottom + 4;
  if (top + el.offsetHeight > window.innerHeight - pad) {
    const above = r.top - 4 - el.offsetHeight;
    top = above > ceiling ? above : window.innerHeight - pad - el.offsetHeight;
  }
  el.style.left = Math.round(Math.max(pad, left)) + "px";
  el.style.top = Math.round(Math.max(ceiling, top)) + "px";
}

/* Open the image-export menu.

   renderPNG(size) must return a PNG data URL drawn at size.w × size.h with a
   pixel ratio of size.out / size.w. With no anchor to hang the menu on — a
   programmatic export, an unusual layout — the current size is downloaded
   straight away rather than leaving the reader with nothing. */
function obsExportMenu(filename, renderPNG, fallbackAnchor, fixedSize) {
  const anchor = exportAnchor(fallbackAnchor);
  const reopening = exportMenu && exportMenu.anchor === anchor;
  closeExportMenu();
  if (reopening) return;
  const sizeNow = () => fixedSize || exportSize();
  if (!anchor) { downloadDataURL(renderPNG(sizeNow()), filename); return; }

  const chosen = exportSize().key;
  const el = document.createElement("div");
  el.className = "export-menu";
  el.setAttribute("role", "menu");
  el.setAttribute("aria-label", "Export image");
  // A map or a population pyramid has a shape of its own: reflowing it to a
  // 16:9 slide would squash the thing it is drawing. Those pass a fixed size
  // and get the copy/download half of the menu without the size list.
  el.innerHTML =
    (fixedSize ? "" :
      '<p class="export-menu-head">Image size</p>' +
      '<div class="export-sizes">' +
      EXPORT_SIZES.map(s =>
        '<button type="button" class="export-size" data-size="' + s.key + '" ' +
        'aria-pressed="' + (s.key === chosen) + '">' +
        '<span>' + escapeHtml(s.label) + '</span>' +
        '<span class="export-size-note num">' + escapeHtml(exportSizeNote(s)) + "</span>" +
        "</button>").join("") +
      "</div>") +
    '<div class="export-menu-acts' + (fixedSize ? " export-menu-acts-only" : "") + '">' +
    (CAN_COPY_IMAGE
      ? '<button type="button" class="btn btn-primary" data-do="copy">Copy image</button>' : "") +
    '<button type="button" class="btn" data-do="png">Download PNG</button>' +
    "</div>" +
    '<p class="export-menu-foot">Light theme, source line included' +
    (fixedSize ? " \u00b7 " + escapeHtml(exportSizeNote(fixedSize)) : "") + ".</p>";
  document.body.appendChild(el);
  placeExportMenu(el, anchor);
  anchor.setAttribute("aria-expanded", "true");

  const onDown = e => { if (!el.contains(e.target) && e.target !== anchor) closeExportMenu(); };
  const onKey = e => {
    if (e.key !== "Escape") return;
    closeExportMenu();
    if (anchor.focus) anchor.focus();
  };
  document.addEventListener("mousedown", onDown, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", closeExportMenu);
  window.addEventListener("scroll", closeExportMenu, true);
  exportMenu = { el: el, anchor: anchor, onDown: onDown, onKey: onKey };

  el.addEventListener("click", e => {
    const pick = e.target.closest(".export-size");
    if (pick) {
      rememberExportSize(pick.dataset.size);
      Array.prototype.forEach.call(el.querySelectorAll(".export-size"), b =>
        b.setAttribute("aria-pressed", String(b === pick)));
      return;
    }
    const act = e.target.closest("[data-do]");
    if (!act) return;
    const size = sizeNow();
    if (act.dataset.do === "png") {
      downloadDataURL(renderPNG(size), filename);
      closeExportMenu();
      return;
    }
    let done;
    try {
      done = navigator.clipboard.write([
        new ClipboardItem({ "image/png": pngBlob(renderPNG(size)) }),
      ]);
    } catch (err) { done = Promise.reject(err); }
    done.then(() => {
      act.textContent = "Copied";
      setTimeout(closeExportMenu, 700);
    }, () => {
      // The clipboard rejects when the document is not focused and in browsers
      // that do not allow image writes. The file is what the reader was after,
      // so hand it over rather than report a failure and stop.
      act.textContent = "Copy blocked — downloading";
      downloadDataURL(renderPNG(size), filename);
      setTimeout(closeExportMenu, 1400);
    });
  });

  const first = el.querySelector("[data-do]");
  if (first) first.focus();
}
