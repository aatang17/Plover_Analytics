/* my.html: Your Plover, the signed-in home.

   Three things, one page: ask a question about the data (the Investment
   Assistant's Studies, /api/v1/assistant/studies), keep a list of companies
   (/coverage, with each one's next results date from the JPX calendar), and
   read what you saved from your browser (/reading, app/assistant/reading.py).
   Every delete has an Undo; every wait says what it is doing; every failure
   says what to do next. Charts are drawn from the server's chart spec
   exactly as the Assistant draws them (assistant.js chartCfg); nothing here
   computes a value. */
"use strict";

(function () {
  var A = "/api/v1/assistant";
  var EXAMPLES = [
    "How far apart are the US and Japanese 10-year yields since 2020?",
    "Is Japan's core inflation above or below America's?",
    "When was the JGB 10-year yield last above 3%?",
  ];
  var state = { hasKey: false, keyForSummaries: false, followUp: null, covered: {} };

  function $(s, r) { return (r || document).querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function api(method, path, body, extra) {
    var opts = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    if (extra) Object.keys(extra).forEach(function (k) { opts[k] = extra[k]; });
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        return { status: r.status, ok: r.ok, body: b };
      });
    });
  }
  function detail(r, fallback) {
    var d = r && r.body && r.body.detail;
    if (typeof d === "string" && d) return d;
    return fallback + (r && r.status ? " (" + r.status + ")" : "") + ".";
  }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function day(v) {
    if (!v) return "";
    var d = typeof v === "number" ? new Date(v * 1000) : new Date(String(v).slice(0, 10) + "T00:00:00");
    if (isNaN(d.getTime())) return "";
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }
  function settingsLink(text) {
    return /Settings/.test(text || "") ? ' <a href="assistant.html#/settings">Open Settings</a>' : "";
  }

  /* ---------------------------------------------------------- toast + undo */
  var toastTimer = null, pending = null;
  function flushPending() {
    if (pending) { var p = pending; pending = null; p.commit(); }
  }
  function toast(text, undo, commit) {
    flushPending();
    var box = $("#my-toast");
    clearTimeout(toastTimer);
    box.innerHTML = "<span>" + esc(text) + "</span>" +
      (undo ? '<button type="button" class="my-undo">Undo</button>' : "") +
      '<button type="button" class="my-x" aria-label="Close this message">×</button>';
    box.hidden = false;
    if (commit) pending = { commit: commit };
    var done = function () { box.hidden = true; flushPending(); };
    if (undo) $(".my-undo", box).onclick = function () {
      clearTimeout(toastTimer); pending = null; box.hidden = true; undo();
    };
    $(".my-x", box).onclick = function () { clearTimeout(toastTimer); done(); };
    toastTimer = setTimeout(done, 7000);
  }
  window.addEventListener("pagehide", flushPending);

  /* ---------------------------------------------------------------- charts
     The Assistant's chart spec (app/assistant/charts.py) into obsChart's
     config; a copy of assistant.js chartCfg, display scaling only. */
  function yenScale(spec) {
    if (!/¥/.test(spec.unit || "") || /per|\//.test(spec.unit)) return null;
    var max = 0;
    spec.series.forEach(function (s) { s.points.forEach(function (p) { if (p[1] != null) max = Math.max(max, Math.abs(p[1])); }); });
    if (max >= 1e12) return { div: 1e12, label: "¥tn" };
    if (max >= 1e9) return { div: 1e9, label: "¥bn" };
    if (max >= 1e6) return { div: 1e6, label: "¥mn" };
    return null;
  }
  function xLabel(spec, x) {
    if (spec.fiscal && typeof fmtFiscalYear === "function") return fmtFiscalYear(x);
    if (/^\d{4}-\d{2}-\d{2}/.test(x)) return spec.daily ? x.slice(0, 10) : fmtPeriod(x);
    return x;
  }
  function sourceLine(spec) {
    var credits = [], vint = [];
    spec.sources.forEach(function (s) {
      if (s.credit && credits.indexOf(s.credit) < 0) credits.push(s.credit);
      if (s.vintage && vint.indexOf(s.vintage) < 0) vint.push(s.vintage);
    });
    return credits.concat(vint).join(" · ") + (credits.length ? " · " : "") + "Plover Analytics";
  }
  function chartCfg(spec) {
    var sc = yenScale(spec), k = sc ? sc.div : 1, vals = [];
    var series = spec.series.map(function (s, i) {
      return { name: s.name, slot: i + 1, points: s.points.map(function (p) {
        var v = p[1] == null ? null : p[1] / k; if (v != null) vals.push(v); return [p[0], v]; }) };
    });
    var dp = sc || spec.unit === "%" || spec.unit === "index" ? 1 : (vals.every(function (v) { return Number.isInteger(v); }) ? 0 : 2);
    var unitLabel = sc ? sc.label : spec.unit;
    var official = spec.series.every(function (s) { return s.trust === "official"; });
    var cfg = { series: series, dp: dp, trust: official ? "official" : null,
      unit: spec.unit === "%" ? "%" : "x",
      unitSuffix: spec.unit === "%" || spec.unit === "index" ? "" : unitLabel,
      yAxisName: spec.unit === "index" ? "Index" : unitLabel,
      yAxisDp: dp, isoPeriods: spec.daily, sourceLine: sourceLine(spec),
      legendBottomNarrow: 34 + 20 * (spec.series.some(function (s) { return s.name.length > 18; })
        ? spec.series.length : Math.ceil(spec.series.length / 2)) };
    if (spec.kind === "bar") {
      var xs = [];
      spec.series.forEach(function (s) { s.points.forEach(function (p) { if (xs.indexOf(p[0]) < 0) xs.push(p[0]); }); });
      xs.sort();
      cfg.categories = xs.map(function (x) { return xLabel(spec, x); });
      cfg.series = series.map(function (s) {
        var m = {}; s.points.forEach(function (p) { m[p[0]] = p[1]; });
        return { name: s.name, slot: s.slot, points: xs.map(function (x) { return m[x] === undefined ? null : m[x]; }) };
      });
      return { kind: "cols", cfg: cfg };
    }
    if (spec.axis === "category") {
      cfg.xType = "category";
      cfg.series = series.map(function (s) {
        return { name: s.name, slot: s.slot, points: s.points.map(function (p) { return [xLabel(spec, p[0]), p[1]]; }) };
      });
    }
    return { kind: "line", cfg: cfg };
  }
  var drawn = null, lastSpec = null;
  function drawChart(el, spec) {
    lastSpec = spec;
    if (drawn) { try { drawn.dispose(); } catch (e) { /* gone */ } drawn = null; }
    if (!el || !spec || typeof obsChart !== "function") return;
    var o = chartCfg(spec);
    drawn = obsChart(el, o.kind, o.cfg);
  }

  /* ------------------------------------------------------------- answers */
  var STAT = { latest: "Latest", min: "Lowest", max: "Highest", mean: "Average", median: "Median",
    count: "Count", highest_since: "Highest since", lowest_since: "Lowest since" };
  function figure(v) {
    if (v == null) return "—";
    if (typeof v === "number") return fmtNum(v, Math.min(3, ((String(v).split(".")[1]) || "").length));
    return esc(v);
  }
  function figuresHtml(summary, sources) {
    if (!summary) return "";
    var titles = {};
    (sources || []).forEach(function (s) { titles[s.name] = s.title || s.series; });
    var cells = [];
    Object.keys(summary).forEach(function (col) {
      Object.keys(summary[col] || {}).forEach(function (k) {
        var v = summary[col][k], big, sub = "";
        if (v && typeof v === "object" && "since" in v) {
          big = v.since ? esc(day(v.since) || v.since) : "Never";
          sub = v.since ? "last at " + figure(v.that_value) + ", now " + figure(v.latest) : esc(v.note || "");
        } else if (v && typeof v === "object") { big = figure(v.value); sub = esc(day(v.at) || v.at || ""); }
        else big = figure(v);
        cells.push('<div class="my-fig"><dt>' + esc(titles[col] || col) + " · " + esc(STAT[k] || k) +
          "</dt><dd>" + big + (sub ? "<small>" + sub + "</small>" : "") + "</dd></div>");
      });
    });
    return cells.length ? '<dl class="my-figs">' + cells.join("") + "</dl>" : "";
  }
  function noteHtml(text) {
    return String(text || "").split(/\n{2,}/).map(function (p) {
      p = p.trim(); if (!p) return "";
      return "<p>" + esc(p).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>") + "</p>";
    }).join("");
  }
  function sourcesHtml(sources) {
    var out = [];
    (sources || []).forEach(function (s) {
      var c = String(s.credit || "").replace(/^\s*Source:\s*/, "").replace(/\.$/, "");
      var rel = String(s.release || "");
      rel = rel ? rel.charAt(0).toLowerCase() + rel.slice(1) : "";
      var bit = [c, rel].filter(Boolean).join(", ");
      if (bit && out.indexOf(bit) < 0) out.push(bit);
    });
    return out.length ? '<p class="my-src">Source: ' + out.map(esc).join("; ") + ". Values as published.</p>" : "";
  }

  function showAnswer(view) {
    var box = $("#my-answer"), st = view.study, run = view.run;
    if (!run) {
      box.innerHTML = '<div class="my-ans"><p class="my-asked">' + esc(st.question || st.title) + "</p>" +
        "<p>This question has no answer yet. Ask it again above.</p></div>";
      return;
    }
    box.innerHTML = '<article class="my-ans" data-study="' + st.id + '">' +
      '<p class="my-asked">' + esc(run.question || st.question || st.title) + "</p>" +
      (run.note ? '<div class="my-note-text">' + noteHtml(run.note) + "</div>" : "") +
      figuresHtml(run.summary, run.sources) +
      (run.chart ? '<figure class="my-chart"><div class="my-plot" id="my-plot"></div></figure>' : "") +
      (run.chart_error ? '<p class="my-err">' + esc(run.chart_error) + "</p>" : "") +
      sourcesHtml(run.sources) +
      '<p class="my-acts">' +
      '<button type="button" class="btn" data-act="follow">Ask a Follow-up</button>' +
      '<a class="btn" href="' + A + "/studies/" + st.id + "/runs/" + run.id + '/csv">Download Data</a>' +
      '<a class="btn" href="assistant.html#/studies/' + st.id + '">Open Full Answer</a>' +
      '<button type="button" class="btn" data-act="delete">Delete</button></p></article>';
    if (run.chart) drawChart($("#my-plot"), run.chart);
    $('[data-act="follow"]', box).onclick = function () { followUp(st); };
    $('[data-act="delete"]', box).onclick = function () { deleteAnswer(st); };
  }

  function followUp(st) {
    state.followUp = st;
    var q = $("#my-q");
    q.placeholder = "Your follow-up on: " + (st.title || st.question);
    var chip = $("#my-follow");
    if (!chip) {
      chip = document.createElement("p");
      chip.id = "my-follow";
      chip.className = "my-follow";
      $("#my-ask").insertAdjacentElement("afterend", chip);
    }
    chip.innerHTML = "Following up on <strong>" + esc(st.title || st.question) + "</strong>. " +
      '<button type="button" class="my-link">Ask a new question instead</button>';
    $("button", chip).onclick = clearFollowUp;
    q.focus();
  }
  function clearFollowUp() {
    state.followUp = null;
    $("#my-q").placeholder = "Ask a question about the data";
    var chip = $("#my-follow"); if (chip) chip.remove();
  }

  function setAsking(on) {
    $("#my-q").disabled = on;
    $("#my-go").disabled = on;
    $("#my-go").textContent = on ? "Working…" : "Ask";
  }

  function ask(question) {
    var q = String(question || "").trim();
    var box = $("#my-answer");
    if (!q) { $("#my-q").focus(); box.innerHTML = '<p class="my-err">Type a question first, or pick one of the examples.</p>'; return; }
    if (!state.hasKey) {
      box.innerHTML = '<p class="my-err">Answers run on your own AI key for now. ' +
        '<a href="assistant.html#/settings">Add your key in Settings</a>, then ask again.</p>';
      return;
    }
    setAsking(true);
    box.innerHTML = '<p class="my-wait">Working out the answer from the data. This can take up to a minute.</p>';
    var follow = state.followUp;
    var made = null;
    var start = follow ? Promise.resolve({ ok: true, body: { study: follow } })
      : api("POST", A + "/studies", { question: q });
    start.then(function (r) {
      if (!r.ok) throw r;
      made = follow ? null : r.body.study;
      return api("POST", A + "/studies/" + r.body.study.id + "/ask", { question: q });
    }).then(function (r) {
      if (!r.ok) throw r;
      $("#my-q").value = "";
      clearFollowUp();
      showAnswer(r.body);
      loadAnswers();
    }).catch(function (r) {
      // nothing half-made left behind: a new question that failed is put away
      if (made) api("DELETE", A + "/studies/" + made.id);
      var why = r && r.status ? detail(r, "Plover could not answer that") : "Plover could not be reached. Check your connection.";
      box.innerHTML = '<p class="my-err">' + esc(why) + settingsLink(why) + "</p>" +
        '<p><button type="button" class="btn" id="my-again">Try Again</button></p>';
      $("#my-again").onclick = function () { ask(q); };
    }).then(function () { setAsking(false); });
  }

  function deleteAnswer(st) {
    api("DELETE", A + "/studies/" + st.id).then(function (r) {
      if (!r.ok) { toast(detail(r, "That answer could not be deleted")); return; }
      var shown = $("#my-answer .my-ans");
      if (shown && Number(shown.getAttribute("data-study")) === st.id) $("#my-answer").innerHTML = "";
      loadAnswers();
      toast("Answer deleted.", function () {
        api("POST", A + "/studies/" + st.id + "/restore").then(function () { loadAnswers(); });
      });
    });
  }

  function loadAnswers() {
    return api("GET", A + "/studies").then(function (r) {
      var list = $("#my-answers");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your answers could not be loaded")) + "</li>"; return; }
      var items = r.body.studies || [];
      $("#my-answers-n").textContent = items.length ? items.length + (items.length === 1 ? " answer" : " answers") : "";
      if (!items.length) {
        list.innerHTML = '<li class="my-empty">Nothing yet. Ask a question above and the answer is kept here.</li>';
        return;
      }
      list.innerHTML = items.map(function (s) {
        var when = day(s.last_run_at || s.updated_at || s.created_at);
        return '<li class="my-row"><button type="button" class="my-open" data-id="' + s.id + '">' +
          '<span class="my-name">' + esc(s.title || s.question) + "</span>" +
          '<span class="my-meta">' + (when ? "Answered " + esc(when) : "No answer yet") + "</span></button>" +
          '<button type="button" class="my-del" data-id="' + s.id + '" aria-label="Delete this answer">Delete</button></li>';
      }).join("");
      Array.prototype.forEach.call(list.querySelectorAll(".my-open"), function (b) {
        b.onclick = function () {
          $("#my-answer").innerHTML = '<p class="my-wait">Opening the answer…</p>';
          api("GET", A + "/studies/" + b.getAttribute("data-id")).then(function (v) {
            if (!v.ok) { $("#my-answer").innerHTML = '<p class="my-err">' + esc(detail(v, "That answer could not be opened")) + "</p>"; return; }
            showAnswer(v.body);
            $("#my-answer").scrollIntoView({ block: "start", behavior: "smooth" });
          });
        };
      });
      Array.prototype.forEach.call(list.querySelectorAll(".my-del"), function (b) {
        var s = items.filter(function (x) { return String(x.id) === b.getAttribute("data-id"); })[0];
        b.onclick = function () { deleteAnswer(s); };
      });
    });
  }

  /* ----------------------------------------------------------- companies */
  function nextResults(code, cell) {
    fetch("/api/v1/equity/calendar/company/" + encodeURIComponent(code), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) {
        var cur = b && b.current && b.current[0];
        if (!cur) { cell.textContent = "No results date listed"; return; }
        cell.textContent = cur.announce_date ? "Results " + day(cur.announce_date) : "Results date not announced";
      })
      .catch(function () { cell.textContent = "—"; });
  }

  function loadCompanies() {
    return api("GET", A + "/coverage").then(function (r) {
      var list = $("#my-cos");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your companies could not be loaded")) + "</li>"; return; }
      var rows = r.body.coverage || [];
      state.covered = {};
      rows.forEach(function (c) { state.covered[c.sec_code] = true; });
      $("#my-cos-n").textContent = rows.length ? rows.length + (rows.length === 1 ? " company" : " companies") : "";
      if (!rows.length) {
        list.innerHTML = '<li class="my-empty">No companies yet. Add one above by name or code.</li>';
        return;
      }
      list.innerHTML = rows.map(function (c) {
        return '<li class="my-row"><a class="my-open" href="company.html?code=' + encodeURIComponent(c.sec_code) + '">' +
          '<span class="my-name">' + esc(c.name || c.name_ja || c.sec_code) + "</span>" +
          '<span class="my-meta">' + esc(c.sec_code) + (c.name_ja && c.name_ja !== c.name ? " · " + esc(c.name_ja) : "") + "</span></a>" +
          '<span class="my-when" data-code="' + esc(c.sec_code) + '">…</span>' +
          '<button type="button" class="my-del" data-code="' + esc(c.sec_code) + '" aria-label="Remove ' +
          esc(c.name || c.sec_code) + '">Remove</button></li>';
      }).join("");
      Array.prototype.forEach.call(list.querySelectorAll(".my-when"), function (cell) {
        nextResults(cell.getAttribute("data-code"), cell);
      });
      Array.prototype.forEach.call(list.querySelectorAll(".my-del"), function (b) {
        b.onclick = function () {
          var code = b.getAttribute("data-code");
          var row = rows.filter(function (c) { return c.sec_code === code; })[0];
          api("DELETE", A + "/coverage/" + encodeURIComponent(code)).then(function (x) {
            if (!x.ok) { toast(detail(x, "That company could not be removed")); return; }
            loadCompanies();
            toast((row.name || code) + " removed.", function () {
              api("POST", A + "/coverage", { code: code }).then(function () { loadCompanies(); });
            });
          });
        };
      });
    });
  }

  function setupCompanySearch() {
    var input = $("#my-co-q"), hits = $("#my-co-hits"), timer = null, seq = 0, found = [];
    function hide() { hits.hidden = true; hits.innerHTML = ""; found = []; }
    function add(c) {
      if (state.covered[c.code]) { toast((c.name_en || c.name_ja) + " is already on your list."); hide(); input.value = ""; return; }
      input.disabled = true;
      api("POST", A + "/coverage", { code: c.code }).then(function (r) {
        input.disabled = false;
        if (!r.ok) { toast(detail(r, "That company could not be added")); return; }
        input.value = ""; hide(); input.focus();
        loadCompanies();
        toast((c.name_en || c.name_ja || c.code) + " added.", function () {
          api("DELETE", A + "/coverage/" + encodeURIComponent(c.code)).then(function () { loadCompanies(); });
        });
      });
    }
    function search() {
      var q = input.value.trim(), mine = ++seq;
      if (!q) { hide(); return; }
      api("GET", A + "/companies?q=" + encodeURIComponent(q)).then(function (r) {
        if (mine !== seq) return;
        found = (r.ok && r.body.companies) || [];
        if (!found.length) {
          hits.innerHTML = '<li class="my-empty">No company found for “' + esc(q) + '”. Try its four-digit code, such as 7203.</li>';
          hits.hidden = false;
          return;
        }
        hits.innerHTML = found.map(function (c, i) {
          return '<li><button type="button" data-i="' + i + '"><span>' + esc(c.name_en || c.name_ja) + "</span>" +
            '<span class="my-meta">' + esc(c.code) + (c.covered || state.covered[c.code] ? " · on your list" : "") + "</span></button></li>";
        }).join("");
        hits.hidden = false;
        Array.prototype.forEach.call(hits.querySelectorAll("button"), function (b) {
          b.onclick = function () { add(found[Number(b.getAttribute("data-i"))]); };
        });
      });
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 180); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); if (found.length) add(found[0]); else search(); }
      if (e.key === "Escape") hide();
    });
    document.addEventListener("click", function (e) { if (!e.target.closest(".my-add")) hide(); });
  }

  /* ------------------------------------------------------------- reading */
  function bulletsHtml(text) {
    var lines = String(text || "").split("\n").map(function (l) { return l.replace(/^\s*-\s*/, "").trim(); }).filter(Boolean);
    return '<p class="my-label">Summary of this article</p><ul class="my-sum">' +
      lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul>";
  }

  function loadReading() {
    return api("GET", A + "/reading").then(function (r) {
      var list = $("#my-reading");
      if (!r.ok) { list.innerHTML = '<li class="my-empty">' + esc(detail(r, "Your reading could not be loaded")) + "</li>"; return; }
      var items = r.body.reading || [];
      $("#my-read-n").textContent = items.length ? items.length + (items.length === 1 ? " article" : " articles") : "";
      if (!items.length) {
        list.innerHTML = '<li class="my-empty">Nothing saved yet. Use the extension or the bookmark above on any article.</li>';
        return;
      }
      list.innerHTML = items.map(function (it) {
        return '<li class="my-art" data-id="' + it.id + '"><div class="my-art-head">' +
          '<a class="my-name" href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer">' + esc(it.title) + "</a>" +
          '<span class="my-meta">' + esc(it.site) + " · saved " + esc(day(it.saved_at)) + "</span></div>" +
          '<div class="my-art-sum">' + (it.summary ? bulletsHtml(it.summary) : "") + "</div>" +
          '<p class="my-acts">' + (it.summary ? "" : '<button type="button" class="btn" data-act="sum">Summarize</button>') +
          '<button type="button" class="btn" data-act="del">Delete</button></p></li>';
      }).join("");
      Array.prototype.forEach.call(list.querySelectorAll(".my-art"), function (li) {
        var id = li.getAttribute("data-id");
        var sum = $('[data-act="sum"]', li), box = $(".my-art-sum", li);
        if (sum) sum.onclick = function () {
          sum.disabled = true; sum.textContent = "Summarizing…";
          box.innerHTML = '<p class="my-wait">Reading the article. This takes a few seconds.</p>';
          api("POST", A + "/reading/" + id + "/summary").then(function (x) {
            if (x.ok) { box.innerHTML = bulletsHtml(x.body.summary); sum.remove(); return; }
            var why = detail(x, "The summary could not be made");
            box.innerHTML = '<p class="my-err">' + esc(why) + settingsLink(why) + "</p>";
            sum.disabled = false; sum.textContent = "Try Again";
          }).catch(function () {
            box.innerHTML = '<p class="my-err">Plover could not be reached. Check your connection, then try again.</p>';
            sum.disabled = false; sum.textContent = "Try Again";
          });
        };
        $('[data-act="del"]', li).onclick = function () {
          li.hidden = true;
          // removed for good when the message goes, so Undo needs no copy of the text
          toast("Article deleted.", function () { li.hidden = false; }, function () {
            api("DELETE", A + "/reading/" + id, undefined, { keepalive: true }).then(function () { loadReading(); });
          });
        };
      });
    });
  }

  function setupBookmark() {
    var bm = $("#my-bm");
    if (!bm || typeof ploverExtract !== "function") return;
    var src = "void(function(o){var p=(" + ploverExtract.toString() + ")();" +
      "window.open(o+'/save.html#'+encodeURIComponent(JSON.stringify(p)),'plover-save','width=600,height=680');})(" +
      JSON.stringify(location.origin) + ")";
    bm.setAttribute("href", "javascript:" + encodeURIComponent(src));
    bm.addEventListener("click", function (e) {
      e.preventDefault();
      toast("Drag this button to your bookmarks bar, then click it on an article.");
    });
  }

  /* ---------------------------------------------------------------- start */
  function gate(html) { $("#my-gate").innerHTML = html; $("#my-gate").hidden = false; $("#my-app").hidden = true; }

  function start() {
    if (typeof initThemeToggle === "function") {
      initThemeToggle(function () { if (lastSpec && $("#my-plot")) drawChart($("#my-plot"), lastSpec); });
    }
    // a question from the front page (index.html: my.html?q=…), kept through sign-in
    var asked = (new URLSearchParams(location.search).get("q") || "").trim().slice(0, 600);
    if (asked) history.replaceState(null, "", "my.html" + location.hash);
    api("GET", A + "/status").then(function (r) {
      if (r.status === 404) {
        gate("<p>Your Plover is not switched on for this server yet. The data pages are free to read without an account.</p>");
        return;
      }
      if (!r.ok) { gate('<p class="my-err">' + esc(detail(r, "Your Plover could not be loaded")) + ' <a href="my.html">Try again</a>.</p>'); return; }
      if (!r.body.signed_in) {
        var back = "my.html" + (asked ? "?q=" + encodeURIComponent(asked) : "");
        gate((asked ? '<p class="my-asked-out">Your question: <strong>' + esc(asked) + "</strong></p>" +
            "<p>Sign in to get the answer. It comes straight back to this question.</p>"
          : "<p>Sign in to ask questions about the data, keep a list of companies and save articles as you read.</p>") +
          '<p><a class="btn btn-primary" href="signin.html?returnTo=' + encodeURIComponent(back) + '">Sign In</a></p>');
        return;
      }
      $("#my-gate").hidden = true;
      $("#my-app").hidden = false;
      $("#my-tries").innerHTML = "<span>Try:</span>" + EXAMPLES.map(function (q, i) {
        return '<button type="button" class="my-try" data-i="' + i + '">' + esc(q) + "</button>";
      }).join("");
      Array.prototype.forEach.call(document.querySelectorAll(".my-try"), function (b) {
        b.onclick = function () { var q = EXAMPLES[Number(b.getAttribute("data-i"))]; $("#my-q").value = q; ask(q); };
      });
      $("#my-ask").addEventListener("submit", function (e) { e.preventDefault(); ask($("#my-q").value); });
      setupCompanySearch();
      setupBookmark();
      api("GET", A + "/desk").then(function (d) {
        var desk = d.ok && d.body.desk ? d.body.desk : {};
        state.hasKey = !!(desk.model_key_set || desk.codex_connected);
        state.keyForSummaries = !!desk.model_key_set;
        var note = $("#my-keynote");
        if (!state.hasKey) {
          note.innerHTML = "Answers and summaries run on your own AI key for now. " +
            '<a href="assistant.html#/settings">Add your key in Settings</a>. It takes a minute, and the key stays sealed on your account.';
          note.hidden = false;
        }
        if (asked) {
          $("#my-q").value = asked;
          if (state.hasKey) ask(asked);
          else $("#my-answer").innerHTML = '<p class="my-err">Your question is in the box above. ' +
            '<a href="assistant.html#/settings">Add your AI key in Settings</a>, then press Ask.</p>';
        }
      });
      Promise.all([loadAnswers(), loadCompanies(), loadReading()]).then(function () {
        var h = location.hash.slice(1);
        var el = h && document.getElementById(h);
        if (el) el.scrollIntoView({ block: "start" });
      });
    }).catch(function () {
      gate('<p class="my-err">Plover could not be reached. Check your connection, then <a href="my.html">try again</a>.</p>');
    });
  }

  start();
})();
