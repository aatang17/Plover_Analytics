/* Plover Assistant (beta): a chat panel in the corner of a data page that answers from the
   page's own tables and then moves the page to show the answer (sets a screen,
   opens a company, switches the trend chart). Server: app/page_chat.py.

   It opens by itself once, a few seconds after the page loads, with three
   questions the page can answer; closing it leaves the Ask Plover Assistant button in
   the corner and it never opens by itself again on this browser. The thread
   survives the page moving to another company (sessionStorage), so a reader who
   asks about Sony from the market view lands on Sony's page with the answer
   still beside it. The reader resizes it by dragging its top-left grip or its top and
   left edges (or the grip with the arrow keys); the size is kept on this browser.

   Loaded with data-page: <script src="assets/page-chat.js" data-page="governance">.
   Needs format.js (escapeHtml). */
(function () {
  var me = document.currentScript;
  var PAGE = me && me.getAttribute("data-page");
  if (!PAGE) return;

  var OPEN_DELAY = 3500;
  var CLOSED_KEY = "pa_pc_closed";          // localStorage: the reader closed it once
  var THREAD_KEY = "pa_pc_thread_" + PAGE;  // sessionStorage: carried across a company change
  var STAY_KEY = "pa_pc_stay";              // sessionStorage: reopen after that move, then scroll here
  var SIZE_KEY = "pa_pc_size";              // localStorage: "w h", the size the reader dragged to
  var USED_KEY = "pa_pc_used";              // localStorage: "YYYY-MM-DD n", questions answered today
  var FREE = 1;                             // answers a day before a signed-out reader is asked to sign up
  var NAME = "Plover Assistant";

  function params() { return new URLSearchParams(location.search); }
  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function store(kind) { try { return window[kind]; } catch (e) { return null; } }
  function get(kind, k) { var s = store(kind); try { return s ? s.getItem(k) : null; } catch (e) { return null; } }
  function put(kind, k, v) { var s = store(kind); try { if (s) { v == null ? s.removeItem(k) : s.setItem(k, v); } } catch (e) {} }

  // ---- what each page can do -------------------------------------------------
  var PAGES = {
    governance: {
      intro: "Ask about boards and pay at Japanese companies. Answers come from this page's tables, and the page moves to show them.",
      placeholder: "Ask, e.g. boards with no women",
      market: ["Which boards have no women?", "Who are the highest-paid executives?",
               "How has the share of women on boards changed?"],
      company: function (name) {
        return ["Who sits on " + name + "'s board?", "Who is paid ¥100m or more at " + name + "?",
                "How much were " + name + "'s officers paid?"];
      },
      company_name: function () {
        var el = document.getElementById("co-name");
        var t = el && el.textContent.trim();
        return t && t !== params().get("c") ? t : null;
      },
      // where each answer lives on the page: the h2 holding this element
      sections: { screen: "screen-count", trend: "trend-panel", summary: "stat-strip",
                  board: "board-count", pay: "pay-count", named: "named-count", history: "hist-count" },
    },
  };
  var P = PAGES[PAGE];
  if (!P) return;

  // ---- the panel ------------------------------------------------------------------
  var thread = [];
  try { thread = JSON.parse(get("sessionStorage", THREAD_KEY) || "[]") || []; } catch (e) { thread = []; }

  var launcher = document.createElement("button");
  launcher.type = "button";
  launcher.className = "pc-launch";
  launcher.innerHTML = "Ask " + NAME + " <span class=pc-launch-beta>Beta</span>";
  launcher.setAttribute("aria-label", "Ask " + NAME + " (beta)");
  launcher.setAttribute("aria-expanded", "false");

  var panel = document.createElement("aside");
  panel.className = "pc-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", NAME);
  panel.innerHTML =
    "<div class=pc-grip tabindex=0 role=button title='Drag to resize. Double-click to restore the size.' " +
    "aria-label='Resize " + NAME + ": drag, or use the arrow keys. Double-click or press Home to restore the size.'></div>" +
    "<div class='pc-edge pc-edge-top' aria-hidden=true></div><div class='pc-edge pc-edge-left' aria-hidden=true></div>" +
    "<div class=pc-head><span class=pc-title>" + NAME + " <span class=pc-beta-tag>Beta</span></span>" +
    "<button type=button class=pc-close aria-label='Close " + NAME + "'>Close ×</button></div>" +
    "<p class=pc-beta>In beta: it can miss a question or read it wrongly. The tables on the page are the record.</p>" +
    "<div class=pc-body aria-live=polite></div>" +
    "<form class=pc-form><input class=pc-q type=text autocomplete=off maxlength=300 " +
    "aria-label='Your question' placeholder='" + esc(P.placeholder) + "'>" +
    "<button type=submit class='btn btn-primary pc-send'>Ask</button></form>";
  document.body.appendChild(launcher);
  document.body.appendChild(panel);

  var body = panel.querySelector(".pc-body");
  var input = panel.querySelector(".pc-q");
  var send = panel.querySelector(".pc-send");
  var grip = panel.querySelector(".pc-grip");
  var busy = false;

  // ---- resizing ---------------------------------------------------------------------
  // Anchored bottom-right, so the grip and the top and left edges move; on a phone the panel is
  // a full-width sheet and only its height changes.
  var MIN_W = 320, MIN_H = 320;
  function narrow() { return matchMedia("(max-width: 600px)").matches; }
  function limits() {
    var header = document.querySelector(".site-header");
    var top = (header ? header.offsetHeight : 0) + 16;
    var bar = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--pc-bar")) || 0;
    var bottom = narrow() ? bar : bar + 16;
    return { w: Math.max(MIN_W, window.innerWidth - 32), h: Math.max(MIN_H, window.innerHeight - top - bottom) };
  }
  function clampSize(w, h) {
    var m = limits();
    return { w: Math.round(Math.min(Math.max(w, MIN_W), m.w)), h: Math.round(Math.min(Math.max(h, MIN_H), m.h)) };
  }
  function setSize(w, h, keep) {
    var c = clampSize(w, h);
    if (!narrow()) panel.style.width = c.w + "px";
    panel.style.height = c.h + "px";
    panel.classList.add("pc-sized");
    if (keep) put("localStorage", SIZE_KEY, (narrow() ? Math.round(panel.offsetWidth) : c.w) + " " + c.h);
  }
  function resetSize() {
    panel.style.width = "";
    panel.style.height = "";
    panel.classList.remove("pc-sized");
    put("localStorage", SIZE_KEY, null);
  }
  function restoreSize() {
    var v = (get("localStorage", SIZE_KEY) || "").split(" ");
    var w = parseInt(v[0], 10), h = parseInt(v[1], 10);
    if (w > 0 && h > 0) setSize(w, h, false);
  }
  restoreSize();
  window.addEventListener("resize", function () { if (panel.classList.contains("pc-sized")) restoreSize(); });

  function startDrag(ev, ways) {
    if (ev.button > 0) return;
    ev.preventDefault();
    var r = panel.getBoundingClientRect();
    var x0 = ev.clientX, y0 = ev.clientY, target = ev.currentTarget;
    var wide = ways.indexOf("w") > -1 && !narrow(), tall = ways.indexOf("h") > -1;
    panel.classList.add("pc-dragging");
    try { target.setPointerCapture(ev.pointerId); } catch (e) {}
    function move(e) {
      setSize(wide ? r.width + (x0 - e.clientX) : r.width, tall ? r.height + (y0 - e.clientY) : r.height, false);
    }
    function end(e) {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", end);
      target.removeEventListener("pointercancel", end);
      panel.classList.remove("pc-dragging");
      var now = panel.getBoundingClientRect();
      if (Math.abs(now.width - r.width) > 1 || Math.abs(now.height - r.height) > 1) setSize(now.width, now.height, true);
    }
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  }
  grip.addEventListener("pointerdown", function (ev) { startDrag(ev, "wh"); });
  panel.querySelector(".pc-edge-top").addEventListener("pointerdown", function (ev) { startDrag(ev, "h"); });
  panel.querySelector(".pc-edge-left").addEventListener("pointerdown", function (ev) { startDrag(ev, "w"); });
  grip.addEventListener("dblclick", resetSize);
  grip.addEventListener("keydown", function (ev) {
    var step = ev.shiftKey ? 60 : 20, r = panel.getBoundingClientRect();
    var dw = { ArrowLeft: step, ArrowRight: -step }[ev.key] || 0;
    var dh = { ArrowUp: step, ArrowDown: -step }[ev.key] || 0;
    if (ev.key === "Home") { ev.preventDefault(); resetSize(); return; }
    if (!dw && !dh) return;
    ev.preventDefault();
    setSize(r.width + (narrow() ? 0 : dw), r.height + dh, true);
  });

  // ---- one free answer a day, then an account -------------------------------------
  // signedIn: true, false, or null while unknown / where accounts are switched off (no gate there)
  var signedIn = null;
  fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
    signedIn = r.ok ? true : r.status === 401 ? false : null;
    render();
  }).catch(function () {});

  function today() { return new Date().toISOString().slice(0, 10); }
  function usedToday() {
    var v = (get("localStorage", USED_KEY) || "").split(" ");
    return v[0] === today() ? parseInt(v[1], 10) || 0 : 0;
  }
  function countAnswer() { put("localStorage", USED_KEY, today() + " " + (usedToday() + 1)); }
  function gated() { return signedIn === false && usedToday() >= FREE; }

  function signupHref() {
    var h1 = document.querySelector("h1");
    var from = h1 ? h1.textContent.replace(/\s+/g, " ").trim().slice(0, 80) : "";
    var back = location.pathname + location.search;
    return "signin.html?returnTo=" + encodeURIComponent("my.html?back=" + encodeURIComponent(back) +
      (from ? "&from=" + encodeURIComponent(from) : ""));
  }

  function gateHtml(after) {
    return "<div class=pc-gate><p>" + (after
        ? "That was today's free question. Create a free account to keep asking, here and in Your Plover, your own research workspace."
        : "Create a free account to keep asking. Your Plover, your own research workspace, keeps your questions and the companies you follow.") +
      "</p><a class='btn btn-primary pc-signup' href='" + esc(signupHref()).replace(/'/g, "&#39;") + "'>Create Free Account</a>" +
      "<p class=pc-small>Your email is all it takes. No password, no card. A link brings you back to this page.</p></div>";
  }

  function examples() {
    var name = P.company_name && params().get("c") ? P.company_name() : null;
    return params().get("c") ? P.company(name || "this company") : P.market;
  }

  function introHtml() {
    if (gated()) return "<p class=pc-intro>" + esc(P.intro) + "</p>";
    return "<p class=pc-intro>" + esc(P.intro) + "</p><ul class=pc-try>" +
      examples().map(function (q) {
        return "<li><button type=button class=pc-ex data-q='" + esc(q).replace(/'/g, "&#39;") + "'>" + esc(q) + "</button></li>";
      }).join("") + "</ul>";
  }

  function cell(v) {
    if (v && typeof v === "object") {
      return "<a class=pc-link href='" + esc(v.href).replace(/'/g, "&#39;") + "'>" + esc(v.text) + "</a>";
    }
    return esc(v);
  }

  var SECTION_NAMES = { screen: "Governance Screens", trend: "Board Composition Over Time",
                        summary: "the summary at the top", board: "Board of Directors",
                        pay: "Officer Remuneration", named: "Individually Disclosed Remuneration",
                        history: "Five-Year History" };

  var NUMBER = /^(?:[¥\u2212+-]?[\d,.]+(?:m|%| years)?|—)$/;

  function answerHtml(a) {
    if (a.error) {
      return "<div class='pc-a pc-err'><p>" + esc(a.error) + "</p>" +
        "<button type=button class='btn pc-retry' data-q='" + esc(a.q).replace(/'/g, "&#39;") + "'>Try Again</button></div>";
    }
    var h = "<div class=pc-a><p>" + esc(a.reply) + "</p>";
    var t = a.table;
    if (t && t.rows && t.rows.length) {
      // a column of figures is right-aligned; a column of names wraps on the left
      var numeric = t.columns.map(function (c, i) {
        return t.rows.every(function (r) { return typeof r[i] === "string" && NUMBER.test(r[i]); });
      });
      h += "<table class=pc-tbl data-no-enhance><thead><tr>" + t.columns.map(function (c, i) {
        return "<th" + (numeric[i] ? " class=r" : "") + ">" + esc(c) + "</th>";
      }).join("") + "</tr></thead><tbody>" + t.rows.map(function (r) {
        return "<tr>" + r.map(function (v, i) { return "<td" + (numeric[i] ? " class=r" : "") + ">" + cell(v) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table>";
    }
    if (a.section) {
      h += "<button type=button class=pc-show data-section='" + esc(a.section) + "'>Shown on the page: " +
        esc(SECTION_NAMES[a.section] || "below") + " ↓</button>";
    }
    return h + "<p class=pc-src>" + esc(a.source || "") + "</p></div>";
  }

  function render() {
    var h = thread.length ? "" : introHtml();
    thread.forEach(function (turn) {
      h += "<p class=pc-you>" + esc(turn.q) + "</p>";
      h += turn.pending ? "<p class='pc-a pc-wait'>Reading this page's data…</p>" : answerHtml(turn);
    });
    var shut = gated();
    if (shut && !busy) h += gateHtml(thread.length > 0);
    body.innerHTML = h;
    body.scrollTop = body.scrollHeight;
    input.disabled = busy || shut;
    send.disabled = busy || shut;
    input.placeholder = shut ? "Create a free account to keep asking" : P.placeholder;
  }

  function save() {
    put("sessionStorage", THREAD_KEY, JSON.stringify(thread.filter(function (t) { return !t.pending; }).slice(-8)));
  }

  // The cookie bar is fixed to the bottom of the window; the panel and the button sit above it
  // while it is there, and drop back when the reader answers it.
  function clearBar() {
    var bar = document.body.classList.contains("has-consent-bar") && document.querySelector(".consent-bar");
    var h = bar ? bar.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty("--pc-bar", h ? Math.ceil(h) + "px" : "0px");
  }
  clearBar();
  new MutationObserver(clearBar).observe(document.body, { attributes: true, attributeFilter: ["class"], childList: true });
  window.addEventListener("resize", clearBar);

  function open(byReader) {
    panel.hidden = false;
    launcher.hidden = true;
    launcher.setAttribute("aria-expanded", "true");
    render();
    if (byReader) input.focus();
  }

  function fold() {
    panel.hidden = true;
    launcher.hidden = false;
    launcher.setAttribute("aria-expanded", "false");
  }

  function close() {
    panel.hidden = true;
    launcher.hidden = false;
    launcher.setAttribute("aria-expanded", "false");
    put("localStorage", CLOSED_KEY, "1");
    put("sessionStorage", STAY_KEY, null);
    launcher.focus();
  }

  // ---- moving the page --------------------------------------------------------------
  function sectionEl(key) {
    var id = P.sections[key];
    var el = id && document.getElementById(id);
    if (!el) return null;
    return el.closest("h2") || el;
  }

  function flash(key) {
    var el = sectionEl(key);
    if (!el || el.offsetParent === null) return false;
    el.classList.add("pc-target");
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    el.classList.add("pc-flash");
    setTimeout(function () { el.classList.remove("pc-flash"); }, 2400);
    return true;
  }

  function leaveFor(url, section) {
    save();
    put("sessionStorage", STAY_KEY, section || "1");
    location.href = url;
  }

  function setSelect(id, value) {
    var sel = document.getElementById(id);
    if (!sel) return false;
    var has = Array.prototype.some.call(sel.options, function (o) { return o.value === value; });
    if (!has) return false;
    if (sel.value !== value) {
      sel.value = value;
      sel.dispatchEvent(new Event("change"));
    }
    return true;
  }

  function apply(act) {
    if (!act) return null;
    var onCompany = !!params().get("c");
    if (act.type === "company") {
      if (params().get("c") !== act.code) {
        leaveFor("governance.html?c=" + encodeURIComponent(act.code), act.section);
        return act.section;
      }
      flash(act.section);
      return act.section;
    }
    if (act.type === "screen") {
      if (onCompany) {
        leaveFor("governance.html?screen=" + encodeURIComponent(act.metric) + (act.listed ? "" : "&listed=all"), "screen");
        return "screen";
      }
      setSelect("screen-select", act.metric);
      var want = act.listed ? "true" : "";
      var seg = document.querySelector("#listed-seg button[data-listed='" + want + "']");
      if (seg && seg.getAttribute("aria-pressed") !== "true") seg.click();
      flash("screen");
      return "screen";
    }
    if (act.type === "trend") {
      if (onCompany) { leaveFor("governance.html?m=" + encodeURIComponent(act.measure), "trend"); return "trend"; }
      setSelect("measure-select", act.measure);
      flash("trend");
      return "trend";
    }
    if (act.type === "section") {
      if (onCompany) return null;
      flash(act.section);
      return act.section;
    }
    return null;
  }

  // ---- asking ---------------------------------------------------------------------------
  function ask(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    if (gated()) { render(); return; }
    var prev = null;
    for (var i = thread.length - 1; i >= 0; i--) { if (!thread[i].error) { prev = thread[i].q; break; } }
    var turn = { q: q, pending: true };
    thread.push(turn);
    busy = true;
    input.value = "";
    render();
    var url = "/api/v1/page-chat?page=" + encodeURIComponent(PAGE) + "&q=" + encodeURIComponent(q) +
      (params().get("c") ? "&c=" + encodeURIComponent(params().get("c")) : "") +
      (prev ? "&prev=" + encodeURIComponent(prev) : "");
    fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          // the server's own sentence; a lost connection has none and gets the plain one below
          var err = new Error(typeof d.detail === "string" ? d.detail : "");
          err.fromServer = true;
          throw err;
        }
        return d;
      });
    }).then(function (d) {
      delete turn.pending;
      turn.reply = d.reply;
      turn.table = d.table;
      turn.source = d.source;
      busy = false;
      if (signedIn === false) countAnswer();
      turn.section = apply(d.page_action);
      save();
      render();
    }).catch(function (e) {
      delete turn.pending;
      turn.error = (e && e.fromServer && e.message) ? e.message + " Try again, or use the tables on this page."
        : "Couldn't get an answer just now. Check your connection and try again.";
      busy = false;
      render();
    });
  }

  panel.querySelector(".pc-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    ask(input.value);
  });
  panel.querySelector(".pc-close").addEventListener("click", close);
  launcher.addEventListener("click", function () { open(true); });
  body.addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-q]");
    if (b) {
      if (b.classList.contains("pc-retry")) {
        thread = thread.filter(function (t) { return !(t.error && t.q === b.getAttribute("data-q")); });
      }
      ask(b.getAttribute("data-q"));
      return;
    }
    var s = ev.target.closest("button[data-section]");
    if (s) {
      // on a phone the panel covers the page: fold it to its button (the thread stays) to show the answer
      if (matchMedia("(max-width: 600px)").matches) fold();
      flash(s.getAttribute("data-section"));
      return;
    }
    var a = ev.target.closest("a.pc-link");
    if (a) { save(); put("sessionStorage", STAY_KEY, "board"); }
    if (ev.target.closest("a.pc-signup")) save();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !panel.hidden && panel.contains(document.activeElement)) close();
  });

  // ---- opening ---------------------------------------------------------------------------
  var stay = get("sessionStorage", STAY_KEY);
  if (stay) {
    put("sessionStorage", STAY_KEY, null);
    open(false);
    // the company's tables arrive after the page loads; scroll once the heading is filled in
    var tries = 0;
    var wait = setInterval(function () {
      var id = P.sections[stay];
      var el = id && document.getElementById(id);
      if ((el && el.textContent.trim()) || ++tries > 40) {
        clearInterval(wait);
        if (stay !== "1") flash(stay);
        render();     // the company's name is known now, for the examples
      }
    }, 150);
  } else if (get("localStorage", CLOSED_KEY) !== "1") {
    setTimeout(function () { if (panel.hidden) open(false); }, OPEN_DELAY);
  }
})();
