/* A question box on a data page: the reader asks about the page's topic and the
   answer opens under the box, without leaving the page. The front page's box is
   us-stocks.js; both read the same answers (app/us_ask_api.py,
   app/assistant/preview.py), follow the same sign-up rule, and a full answer here
   links to its permanent address on the front page.

   Mark the place in the page's HTML:
     <div data-page-ask data-label="Ask About CEO Pay"
          data-examples="First question|Second question"
          data-topic="A whole question, so a short follow-up has something to read"
          data-on="prices or treasury: the economy page it sits on"></div>
   A box about one company waits for its name: data-company holds the wording with
   {company} in it ({asked} adds the ticker), and the page calls pageAsk.company(name, ticker).

   Filed values as filed; every calculated figure names its formula under
   "Sources and formulas"; missing is —, never 0. Needs format.js, charts.js and
   us-format.js. */
(function () {
  "use strict";

  var PREVIEW = "/api/v1/assistant/preview", FREE = "/api/v1/us/ask/free";
  var TOO_LONG_MS = 120000;
  var TABLE_ROWS = 10;
  var ERROR_TEXT = "That didn't work on our side. Try again in a moment; your question is still in the box.";
  var SLOW_TEXT = "That is taking too long. Try again, or ask a shorter question.";
  var esc = usFmt.esc, getJSON = usFmt.getJSON;
  // this file's attributes are single-quoted, and "Tesla's" must not end one
  function attr(s) { return esc(s).replace(/'/g, "&#39;"); }
  function askHref(q) { return "/?q=" + encodeURIComponent(q).replace(/'/g, "%27"); }

  /* ---- who is reading: all of an answer, or the start of it ------------ */
  // As on the front page. Signed out, a free answer shows in full while the
  // day's allowance lasts; with none left, or none on offer, the start shows
  // and the rest is one sign-up away. Signed in, or with accounts off, all of it.
  var me = { on: false, email: null, free: null }, previewOff = false;
  var reader = fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
    if (r.status === 404) return null;
    me.on = true;
    return r.ok ? r.json() : null;
  }).then(function (who) {
    me.email = (who && who.email) || null;
    if (!me.on || me.email) return null;
    return getJSON(FREE).then(function (f) { me.free = f && f.free != null ? f : null; }, function () { me.free = null; });
  }).catch(function () { /* nobody known: treated as signed out where accounts are on */ });

  function gated() {
    return me.on && !me.email && !previewOff && !(me.free && me.free.left > 0);
  }

  // A signed-out reader's typed questions share the day's free answers with the
  // front page, economy answers included. Once they are used, the next typed
  // question is not answered here: the reader signs up and Your Plover answers
  // it, with the question before it in the same chat and a link back to this
  // page. The page's own suggested questions stay free (via=example).
  function counted(via) {
    return me.on && !me.email && !previewOff && via !== "example" && me.free && me.free.free > 0;
  }

  function handOff(via) {
    return counted(via) && me.free.left <= 0;
  }

  function takeFree() {
    return fetch(FREE, { method: "POST", headers: { Accept: "application/json" } }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (f) {
      if (f && f.free != null) me.free = f;
    }).catch(function () { /* the count is the server's; a lost one costs the reader nothing */ });
  }

  // Your Plover, asked this question, with the one before it and the way back here
  function ploverHref(q, prev) {
    var h1 = document.querySelector("h1");
    var from = h1 ? h1.textContent.replace(/\s+/g, " ").trim().slice(0, 80) : "";
    return "my.html?q=" + encodeURIComponent(q) +
      (prev && prev !== q ? "&prev=" + encodeURIComponent(prev) : "") +
      "&back=" + encodeURIComponent(location.pathname + location.search) +
      (from ? "&from=" + encodeURIComponent(from) : "");
  }

  /* ---- one box --------------------------------------------------------- */
  function Box(host) {
    this.host = host;
    this.seq = 0;
    this.last = "";
    this.drawn = null;      // { chart, source, el, instance }, so a theme change can redraw it
    this.topic = host.getAttribute("data-topic") || "";
    this.on = host.getAttribute("data-on") || "";
    this.fill(host.getAttribute("data-examples"), host.getAttribute("data-label"));
  }

  Box.prototype.fill = function (examples, label) {
    var list = (examples || "").split("|").filter(Boolean);
    this.host.className = "pa";
    this.host.innerHTML = "<form class='pa-form' autocomplete='off'>" +
      "<label class='pa-label'><span>" + esc(label || "Ask About This Page") + "</span>" +
      "<input type='search' class='pa-q' maxlength='300' placeholder='e.g. " + attr(list[0] || "") + "'></label>" +
      "<button type='submit' class='btn pa-go'>Ask</button></form>" +
      (list.length ? "<div class='pa-examples'>" + list.map(function (q) {
        return "<a href='" + askHref(q) + "'>" + esc(q) + "</a>";
      }).join("") + "</div>" : "") +
      "<p class='pa-status' role='status' aria-live='polite' hidden></p><div class='pa-result' hidden></div>";
    var self = this;
    this.input = this.host.querySelector(".pa-q");
    this.button = this.host.querySelector(".pa-go");
    this.status = this.host.querySelector(".pa-status");
    this.result = this.host.querySelector(".pa-result");
    this.host.querySelector(".pa-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var q = self.input.value.trim();
      if (q) self.ask(q);
    });
    this.wireQuestions(this.host.querySelector(".pa-examples"));
  };

  // Suggested questions answer here; their links still work in a new tab.
  Box.prototype.wireQuestions = function (root) {
    var self = this;
    if (!root) return;
    Array.prototype.forEach.call(root.querySelectorAll("a"), function (a) {
      a.addEventListener("click", function (ev) {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
        ev.preventDefault();
        self.ask(new URL(a.href, location.href).searchParams.get("q"), "example");
      });
    });
  };

  Box.prototype.clearChart = function () {
    if (this.drawn) {
      try { this.drawn.instance.dispose(); } catch (e) { /* gone */ }
      (this.drawn.extra || []).forEach(function (c) { try { c.dispose(); } catch (e) { /* gone */ } });
    }
    this.drawn = null;
  };

  Box.prototype.say = function (text) {
    this.status.textContent = text;
    this.status.hidden = !text;
  };

  Box.prototype.ask = function (q, via) {
    if (this.asking === q) return;      // a double click on a suggested question asks it once
    this.asking = q;
    var self = this, seq = ++this.seq;
    if (this.input.value.trim() !== q) this.input.value = q;
    this.button.disabled = true;
    this.result.classList.add("pa-stale");
    this.say("Reading your question…");
    // The last question goes with this one, so "and Microsoft?" is read whole;
    // before any question, the page's own topic does that job.
    var prev = this.last && this.last !== q ? this.last : this.topic;
    var tail = (prev && prev !== q ? "&prev=" + encodeURIComponent(prev) : "") + (via ? "&via=" + encodeURIComponent(via) : "");
    var current = function () { return seq === self.seq; };

    var on = this.on;
    function preview() { return getJSON(PREVIEW + "?q=" + encodeURIComponent(q) + tail + (on ? "&on=" + encodeURIComponent(on) : "")); }

    function full() {
      return usFmt.askStream("/ask?q=" + encodeURIComponent(q) + tail, function (text) {
        if (current()) self.say(text);
      }).then(function (a) {
        return { full: a };
      }, function (e) {
        // not a stock question: the US economy may answer it (prices, yields, inflation)
        if (e.status !== 422 && e.status !== 404) throw e;
        return preview().then(function (p) {
          if (p.kind !== "macro" && p.kind !== "web") throw e;
          return { preview: p };
        }, function () { throw e; });
      });
    }

    var before = this.last;               // the question this one follows, for Your Plover's chat
    var answer = reader.then(function () {
      if (handOff(via)) return { handOff: true };
      // a box on an economy page asks the economy answers first: they know the page, and make no model call
      if (on && !gated()) {
        return preview().then(function (p) { return p.kind === "macro" || p.kind === "web" ? { preview: p } : full(); }, full);
      }
      if (!gated()) return full();
      return preview().then(function (p) { return { preview: p }; }, function (e) {
        if (e.status === 404) { previewOff = true; return full(); }  // no preview on this server: the full answer
        throw e;
      });
    });
    var timer;
    var slow = new Promise(function (resolve, reject) {
      timer = setTimeout(function () { var e = new Error(SLOW_TEXT); e.status = 504; e.slow = true; reject(e); }, TOO_LONG_MS);
    });

    return Promise.race([answer, slow]).then(function (got) {
      if (!current()) return;
      if (got.handOff) { self.drawHandOff(q, before); return; }
      self.last = (got.full && got.full.question) || (got.preview && got.preview.question) || q;
      // the stream counts its own answers; an economy answer from the preview route is counted here
      if (got.preview && (got.preview.kind === "macro" || got.preview.kind === "web") && counted(via)) {
        takeFree().then(function () { if (current()) self.noteFree(); });
      }
      if (got.full) self.drawFull(got.full, q);
      else if (got.preview.kind === "none") self.drawMiss(got.preview.message, got.preview.try, false, q);
      else self.drawPreview(got.preview, q);
    }).catch(function (e) {
      if (!current()) return;
      // a miss the server worded keeps its words; anything else is ours, and says so
      var ours = !e.miss && (!e.status || e.status >= 500 || !e.message);
      self.drawMiss(e.slow ? SLOW_TEXT : ours ? ERROR_TEXT : e.message, ours ? [] : e.tries, ours, q);
    }).then(function () {
      clearTimeout(timer);
      if (!current()) return;
      self.asking = "";
      self.button.disabled = false;
      self.say("");
    });
  };

  /* ---- what comes back ------------------------------------------------- */
  Box.prototype.show = function (html) {
    var self = this;
    this.clearChart();
    this.result.classList.remove("pa-stale");
    this.result.innerHTML = "<div class='pa-card'>" + html + "</div>";
    this.result.hidden = false;
    var close = this.result.querySelector(".pa-close");
    if (close) close.addEventListener("click", function () { self.reset(); });
  };

  // Close puts the page back as it was, and the next question starts from the page's topic.
  Box.prototype.reset = function () {
    this.seq++;
    this.asking = "";
    this.clearChart();
    this.last = "";
    this.result.innerHTML = "";
    this.result.hidden = true;
    this.input.value = "";
    this.button.disabled = false;
    this.say("");
    this.input.focus();
  };

  function topLine(q, when) {
    return "<div class='pa-top'><span class='pa-asked'><span>You asked</span> <b>" + esc(q) + "</b>" +
      (when ? " <span class='pa-when'>" + esc(when) + "</span>" : "") + "</span>" +
      "<button type='button' class='pa-close'>Close</button></div>";
  }

  function fullLink(q) {
    return "<a class='pa-more' href='" + askHref(q) + "'>Open the Full Answer</a>";
  }

  function figuresHtml(figs) {
    var cells = (figs || []).filter(function (f) { return f && f.value !== undefined; }).map(function (f) {
      var shown = f.unit === "USD" && f.value === 0 ? "$0" : f.unit === "%" ? usFmt.previewValue(f) : usFmt.fmtBy(f.unit, f.value);
      return "<div" + (f.formula ? " title='" + attr(f.formula) + "'" : "") + "><dt>" + esc(f.label) + "</dt><dd>" + shown +
        (f.as_of && f.unit !== "date" ? "<small>as of " + usFmt.dateWords(f.as_of) + "</small>" : "") + "</dd></div>";
    });
    return cells.length ? "<dl class='pa-figs'>" + cells.join("") + "</dl>" : "";
  }

  Box.prototype.drawFull = function (a, q) {
    var asked = a.question || q;
    var calc = a.calc || {};
    // the chart's source line names the data it draws, never news reports beside it
    var src = (a.sources || []).filter(function (s) { return !s.url; }).map(function (s) { return s.credit; }).join(" ");
    var when = a.period && a.period.text ? a.period.text : "";
    var rows = a.table && a.table.rows ? a.table.rows : [];
    var table = rows.length ? { columns: a.table.columns, unit: a.table.unit, rows: rows.slice(0, TABLE_ROWS) } : null;
    var tableId = "pa-table-" + this.seq;
    var left = me.on && !me.email && a.free && a.free.free > 0 ? a.free.left : null;
    if (left != null) me.free = a.free;
    this.show(topLine(q, when) +
      (a.read && a.read.text ? "<p class='pa-read'><b>Read as</b> " + esc(a.read.text) + "</p>" : "") +
      "<p class='pa-head hit'>" + esc(a.headline) + "</p>" +
      (a.answer ? "<p class='pa-text'>" + esc(a.answer) + "</p>" : "") +
      figuresHtml(a.figures) +
      (a.chart ? "<div class='pa-chart-wrap'><div class='pa-chart'></div><div class='pa-chart-foot'><span>" + esc(src) + "</span>" +
        "<span class='pa-acts'><button type='button' class='pa-link pa-png'>Download PNG</button>" +
        "<button type='button' class='pa-link pa-csv'>Download CSV</button></span></div></div>" : "") +
      (table ? usFmt.tableHtml(table, tableId) : "") +
      (a.notes && a.notes.length ? "<ul class='pa-notes'>" + a.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>" : "") +
      "<div class='pa-foot'>" + fullLink(asked) +
      (rows.length > TABLE_ROWS ? "<span>Showing " + TABLE_ROWS + " of " + usFmt.count(rows.length) + " rows; the full answer has them all.</span>" : "") +
      (left != null ? "<span>" + usFmt.count(left) + " of " + usFmt.count(a.free.free) + " free answers left today. <a href='signin.html?returnTo=" +
        attr(encodeURIComponent(location.pathname + location.search)) + "'>Create a free account</a> for every answer in full.</span>" : "") + "</div>" +
      usFmt.sourcesHtml(a.sources, calc));
    if (a.chart) {
      var head = [a.headline, src, "Formulas: " + Object.keys(calc).filter(function (k) { return k !== "query" && k !== "steps"; })
        .map(function (k) { return k.replace(/_/g, " ") + " = " + calc[k]; }).join("; "),
        "Plover Analytics, " + location.origin + askHref(asked)];
      this.drawChart(a.chart, src, a.recipe, head);
    }
  };

  Box.prototype.drawChart = function (chart, src, name, head) {
    var self = this, wrap = this.result.querySelector(".pa-chart-wrap"), el = this.result.querySelector(".pa-chart");
    var extra = [];
    // one bar compares nothing: the headline and the table already say it
    if (chart.kind === "bars" && chart.series[0].points.length < 2) { wrap.remove(); return; }
    var c = usFmt.drawChart(el, chart, src, function (x) { extra.push(x); });
    if (!c) { wrap.remove(); return; }
    this.drawn = { instance: c, extra: extra, chart: chart, src: src, name: name, head: head };
    wrap.querySelector(".pa-png").onclick = function () { self.drawn.instance.exportPNG("plover-" + name + ".png"); };
    wrap.querySelector(".pa-csv").onclick = function () { self.drawn.instance.exportCSV("plover-" + name + ".csv", head); };
  };

  // Charts are drawn in the theme's palette, so a switch redraws the one showing.
  Box.prototype.redraw = function () {
    var d = this.drawn;
    if (!d) return;
    (d.extra || []).forEach(function (c) { try { c.dispose(); } catch (e) { /* gone */ } });
    try { d.instance.dispose(); } catch (e) { /* gone */ }
    var el = this.result.querySelector(".pa-chart");
    while (el.nextSibling && el.nextSibling.classList && el.nextSibling.classList.contains("chart-el")) el.nextSibling.remove();
    el.innerHTML = "";
    this.drawChart(d.chart, d.src, d.name, d.head);
  };

  Box.prototype.drawPreview = function (p, q) {
    var asked = p.question || q;
    var figs = (p.figures || []).map(function (f) {
      return "<div><dt>" + esc(f.label) + "</dt><dd>" + usFmt.previewValue(f) +
        (f.as_of ? "<small>" + esc(f.as_of_label || usFmt.dateWords(f.as_of)) + "</small>" : "") + "</dd></div>";
    }).join("");
    // an economy answer's full version is its own public page; a stock answer's waits behind the sign-up
    var signUp = p.kind !== "macro" && p.kind !== "web" && gated();
    var here = p.full_url && new URL(p.full_url, location.href);
    var sameView = here && here.pathname === location.pathname && here.search === location.search;
    var foot = p.kind === "web" ? "" : signUp ?
      "<div class='pa-gate'><p class='pa-gate-h'>The full answer also has</p><ul>" +
        (p.more || []).map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul>" +
        "<a class='btn pa-signup' href='signin.html?returnTo=" + encodeURIComponent(askHref(asked)) + "'>Sign Up Free to See the Full Answer</a>" +
        "<p class='pa-small'>" + (me.free && me.free.free > 0 ? "Today's " + usFmt.count(me.free.free) + " free answers are used. " : "") +
        "Your email is all it takes. No password, no card.</p></div>" :
      (p.kind === "macro" ? (p.full_url && !sameView ? "<div class='pa-foot'><a class='pa-more' href='" + attr(p.full_url) + "'>Open the Full Chart</a></div>" : "") :
        "<div class='pa-foot'>" + fullLink(asked) + "</div>");
    this.show(topLine(q, p.period && p.period.text) +
      "<p class='pa-head hit'>" + esc(p.headline) + "</p>" +
      (p.lead ? "<p class='pa-text'>" + esc(p.lead) + "</p>" : "") +
      (figs ? "<dl class='pa-figs'>" + figs + "</dl>" : "") +
      ((p.sources || []).length ? "<p class='pa-src'>" + p.sources.map(function (x) { return esc(String(x).replace(/\.\s*$/, "")); }).join(" · ") + "</p>" : "") +
      (p.calc ? "<p class='pa-src'>" + esc(p.calc) + "</p>" : "") +
      ((p.reports || []).length ? "<p class='pa-src'>" + p.reports.map(function (r) {
        return "<a href='" + attr(r.url) + "' rel='noopener nofollow' target='_blank'>" + esc(r.credit) + "</a> " + esc(r.note);
      }).join(" · ") + "</p>" : "") + foot);
  };

  // "your free answer", or "your 3 free answers": the allowance is a server setting
  function freeWords() {
    var n = me.free && me.free.free;
    return n > 1 ? "your " + usFmt.count(n) + " free answers" : "your free answer";
  }

  // The free answer is used: this question goes to Your Plover after a free sign-up.
  Box.prototype.drawHandOff = function (q, before) {
    this.show(topLine(q) +
      "<p class='pa-head'>Sign up free to keep asking</p>" +
      "<p class='pa-text'>You have used " + freeWords() + " for today. Sign up with your email and Plover answers this question " +
      "straight away" + (before ? ", in the same chat as your last one" : "") + ". A link there brings you back to this page.</p>" +
      "<div class='pa-gate'><a class='btn btn-primary pa-signup' href='signin.html?returnTo=" + attr(encodeURIComponent(ploverHref(q, before))) +
      "'>Sign Up Free and Ask</a><p class='pa-small'>Your email is all it takes. No password, no card.</p></div>");
  };

  // Under an answer that used the day's last free one: what the next question does.
  Box.prototype.noteFree = function () {
    var foot = this.result.querySelector(".pa-card");
    if (!foot || !me.free || me.free.left > 0 || this.result.querySelector(".pa-free-note")) return;
    foot.insertAdjacentHTML("beforeend", "<p class='pa-small pa-free-note'>That was your last free answer today. " +
      "Your next question carries on in Plover after a free sign-up, and you can come back here.</p>");
  };

  Box.prototype.drawMiss = function (text, tries, ours, q) {
    var self = this;
    this.show(topLine(q) + "<p class='pa-miss'>" + esc(text || ERROR_TEXT) + "</p>" +
      (ours ? "<button type='button' class='btn pa-retry'>Try Again</button>" : "") +
      ((tries || []).length ? "<p class='pa-try-h'>Try instead:</p><div class='pa-examples pa-tries'>" + tries.map(function (t) {
        return "<a href='" + askHref(t) + "'>" + esc(t) + "</a>";
      }).join("") + "</div>" : ""));
    this.wireQuestions(this.result.querySelector(".pa-tries"));
    var retry = this.result.querySelector(".pa-retry");
    if (retry) retry.addEventListener("click", function () { self.ask(q); });
  };

  /* ---- start ----------------------------------------------------------- */
  var boxes = [], waiting = [];
  Array.prototype.forEach.call(document.querySelectorAll("[data-page-ask]"), function (host) {
    if (host.hasAttribute("data-company")) waiting.push(host);
    else boxes.push(new Box(host));
  });

  // Questions read better with the name people use: "Apple", not "Apple Inc."
  function shortName(name) {
    var s = String(name).replace(/^the\s+/i, ""), was;
    // more than one ending, and an ampersand with it: "Manufacturing Co Ltd", "Chase & Co."
    do {
      was = s;
      s = s.replace(/[,\s]+(&\s*)?(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc)\.?$/i, "").trim();
    } while (s !== was);
    return s;
  }

  new MutationObserver(function () {
    boxes.forEach(function (b) { b.redraw(); });
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.addEventListener("resize", function () {
    boxes.forEach(function (b) {
      if (b.drawn && b.drawn.instance.resize) b.drawn.instance.resize();
      if (b.drawn) b.drawn.extra.forEach(function (c) { if (c.resize) c.resize(); });
    });
  });

  window.pageAsk = {
    // A box about one company takes its wording once the page knows the name.
    // {asked} carries the ticker, for questions: a small company's name alone
    // can read as another company ("Solaris Resources" answered with Sellas).
    company: function (name, ticker) {
      var co = shortName(name), asked = ticker ? co + " (" + ticker + ")" : co;
      waiting.splice(0).forEach(function (host) {
        var fill = function (attr) { return (host.getAttribute(attr) || "").replace(/\{company\}/g, co).replace(/\{asked\}/g, asked); };
        host.setAttribute("data-examples", fill("data-examples"));
        host.setAttribute("data-label", fill("data-label"));
        host.setAttribute("data-topic", fill("data-topic"));
        host.removeAttribute("data-company");
        boxes.push(new Box(host));
      });
    },
  };
})();
