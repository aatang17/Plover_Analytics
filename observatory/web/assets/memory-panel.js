/* Memory: the notes a person's assistant keeps about them (app/assistant/memory.py).

   One panel, shown under My Account in the Writer Desk and on the Investment
   Assistant's Memory page: the same notes in both, since they are the
   person's own. Switch memory on or off, add a note, edit or delete one,
   delete them all. The page that mounts it supplies the request function,
   so each page's own sign-in and error handling apply.

     MemoryPanel.mount(box, { call: (method, path, body) => Promise, toast: fn })

   `path` is "/memory…" under the page's own API prefix. */
(function () {
  "use strict";

  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var day = function (ts) {
    var d = new Date(ts * 1000);
    return d.getDate() + " " + MON[d.getMonth()] + " " + d.getFullYear();
  };
  var errText = function (e) { return (e && (e.message || e.detail)) || "That did not work. Try again."; };

  function mount(box, opts) {
    var call = opts.call, toast = opts.toast || function () {};
    var editing = null, confirmAll = false;

    function draw(v) {
      var n = v.notes.length;
      box.innerHTML =
        '<div class="memp">' +
        '<div class="memp-top"><button type="button" class="switch' + (v.enabled ? " on" : "") + '" data-memswitch aria-pressed="' + v.enabled + '"><i></i>' +
        (v.enabled ? "On: your assistant reads these notes and adds to them" : "Off: your assistant neither reads nor adds notes") +
        '</button><span class="memp-count">' + n + " of " + v.max_notes + " notes</span></div>" +
        '<form class="memp-add" data-memadd><input type="text" class="memp-new" maxlength="' + v.note_max + '" aria-label="New note" ' +
        'placeholder="Add a note, e.g. I cover Japanese regional banks" autocomplete="off">' +
        '<button type="submit" class="btn">Add Note</button></form>' +
        (n ? '<ul class="memp-list">' + v.notes.slice().reverse().map(function (x) {
          if (editing === x.id) {
            return '<li class="memp-row editing"><form class="memp-edit" data-memsave="' + x.id + '">' +
              '<input type="text" maxlength="' + v.note_max + '" value="' + esc(x.text) + '" aria-label="Edit note">' +
              '<button type="submit" class="btn primary sm">Save</button><button type="button" class="btn sm" data-memcancel>Cancel</button></form></li>';
          }
          return '<li class="memp-row"><span class="memp-text">' + esc(x.text) + "</span>" +
            '<span class="memp-when">' + esc(x.place) + " · " + esc(day(x.updated_at)) + "</span>" +
            '<span class="memp-acts"><button type="button" class="linkbtn" data-memedit="' + x.id + '">Edit</button>' +
            '<button type="button" class="linkbtn quiet" data-memdel="' + x.id + '">Delete</button></span></li>';
        }).join("") + "</ul>"
          : '<p class="empty memp-empty">No notes yet. Your assistant adds them as you talk to it, or add one above.</p>') +
        (n ? '<div class="memp-foot">' + (confirmAll
          ? '<span class="memp-ask">Delete all ' + n + " notes? Your assistant starts again knowing nothing about you.</span>" +
            '<button type="button" class="btn sm danger" data-memclearok>Delete All</button><button type="button" class="btn sm" data-memclearno>Cancel</button>'
          : '<button type="button" class="linkbtn quiet" data-memclear>Delete All Notes</button>') + "</div>" : "") +
        "</div>";
      wire(v);
    }

    function act(p, done) {
      return p.then(function (v) { if (done) toast(done); draw(v); })
        .catch(function (e) { toast(errText(e)); load(); });
    }

    function wire(v) {
      var q = function (sel) { return box.querySelector(sel); };
      q("[data-memswitch]").addEventListener("click", function (e) {
        e.currentTarget.disabled = true;
        act(call("PUT", "/memory/settings", { enabled: !v.enabled }), v.enabled ? "Memory switched off" : "Memory switched on");
      });
      q("[data-memadd]").addEventListener("submit", function (e) {
        e.preventDefault();
        var inp = q("[data-memadd] input"), btn = q("[data-memadd] button");
        var text = inp.value.trim();
        if (!text) { inp.focus(); return; }
        btn.disabled = true;
        btn.textContent = "Adding…";
        call("POST", "/memory", { text: text }).then(function (res) {
          toast("Note added");
          draw(res);
          var again = q("[data-memadd] input");
          if (again) again.focus();
        }).catch(function (err) {
          btn.disabled = false;
          btn.textContent = "Add Note";
          toast(errText(err));
        });
      });
      Array.prototype.forEach.call(box.querySelectorAll("[data-memedit]"), function (b) {
        b.addEventListener("click", function () {
          editing = Number(b.getAttribute("data-memedit"));
          draw(v);
          var inp = q(".memp-edit input");
          inp.focus();
          inp.setSelectionRange(inp.value.length, inp.value.length);
        });
      });
      var ed = q("[data-memsave]");
      if (ed) {
        ed.addEventListener("submit", function (e) {
          e.preventDefault();
          var id = ed.getAttribute("data-memsave");
          var btn = ed.querySelector("button[type=submit]");
          btn.disabled = true;
          btn.textContent = "Saving…";
          call("PATCH", "/memory/" + id, { text: ed.querySelector("input").value }).then(function (res) {
            editing = null;
            toast("Note saved");
            draw(res);
          }).catch(function (err) { btn.disabled = false; btn.textContent = "Save"; toast(errText(err)); });
        });
        var cancel = function () { editing = null; draw(v); };
        ed.querySelector("[data-memcancel]").addEventListener("click", cancel);
        ed.querySelector("input").addEventListener("keydown", function (e) { if (e.key === "Escape") cancel(); });
      }
      Array.prototype.forEach.call(box.querySelectorAll("[data-memdel]"), function (b) {
        b.addEventListener("click", function () {
          b.disabled = true;
          b.textContent = "Deleting…";
          act(call("DELETE", "/memory/" + b.getAttribute("data-memdel")), "Note deleted");
        });
      });
      var clr = q("[data-memclear]");
      if (clr) clr.addEventListener("click", function () { confirmAll = true; draw(v); });
      var no = q("[data-memclearno]");
      if (no) no.addEventListener("click", function () { confirmAll = false; draw(v); });
      var ok = q("[data-memclearok]");
      if (ok) ok.addEventListener("click", function () {
        ok.disabled = true;
        ok.textContent = "Deleting…";
        confirmAll = false;
        act(call("DELETE", "/memory"), "All notes deleted");
      });
    }

    function load() {
      return call("GET", "/memory").then(draw).catch(function (e) {
        box.innerHTML = '<div class="notice" role="alert">Could not load your memory: ' + esc(errText(e)) +
          ' <button type="button" class="linkbtn" data-memretry>Try Again</button></div>';
        box.querySelector("[data-memretry]").addEventListener("click", load);
      });
    }

    box.innerHTML = '<p class="muted">Loading your memory…</p>';
    return load();
  }

  window.MemoryPanel = { mount: mount };
})();
