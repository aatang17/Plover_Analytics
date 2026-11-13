/* The header card and tabs on one US company's pages (co-page.css): logo,
   name, ticker and sector, the latest close with its one-year change, and a
   tab for each page this site holds for the company. Used by us-company.js
   (Overview) and us-companies.js (Financials).

   coHead.load(t) → Promise of {record, metrics}: the company record
   (/api/v1/us/company/{t}) and its latest annual figures and price
   (/api/v1/us/financials/metrics/{t}; null when the company has none).
   coHead.html(data, active) → the card's HTML; active is "overview",
   "financials", "price", "pay" or "owners". */
(function () {
  "use strict";

  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }
  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { var e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
      return r.json();
    });
  }
  function dayShort(iso) {
    var d = new Date(String(iso).slice(0, 10) + "T00:00:00Z");
    return isNaN(d) ? String(iso) : d.getUTCDate() + " " + MONTHS[d.getUTCMonth()].slice(0, 3) + " " + d.getUTCFullYear();
  }
  // "SERVICES-MISCELLANEOUS AMUSEMENT & RECREATION" → "Services-miscellaneous amusement & recreation"
  function sentenceCase(s) {
    s = String(s || "");
    if (s !== s.toUpperCase()) return s;
    s = s.toLowerCase();
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function load(t) {
    var rec = getJSON("/api/v1/us/company/" + encodeURIComponent(t));
    var met = getJSON("/api/v1/us/financials/metrics/" + encodeURIComponent(t)).catch(function () { return null; });
    return Promise.all([rec, met]).then(function (r) { return { record: r[0], metrics: r[1] }; });
  }

  function tabs(rec, active) {
    var c = rec.company, t = c.ticker || c.cik, items = {};
    (rec.items || []).forEach(function (i) { items[i.key] = i; });
    var page = function (k) { return items[k] && items[k].held && items[k].page ? items[k].page : null; };
    // Financials is the ten-year history where held; otherwise the latest
    // annual report's figures are on the Price tab, beside the valuation.
    var stock = "us-stock.html?t=" + encodeURIComponent(c.ticker || t);
    var held = function (k) { return !!(items[k] && items[k].held); };
    var list = [["overview", "Overview", "us-company.html?t=" + encodeURIComponent(t)],
                ["price", "Price", held("prices") || held("financials") ? stock : null],
                ["financials", "Financials", page("history")],
                ["pay", "Executive pay", page("pay")],
                ["owners", "Insiders and holders", held("insiders") || held("holders") ? stock + "&view=owners" : null]];
    var seen = {};
    return list.filter(function (x) {
      if (!x[2] || seen[x[2]]) return false;
      seen[x[2]] = true;
      return true;
    }).map(function (x) {
      return "<a href='" + esc(x[2]) + "'" + (x[0] === active ? " aria-current='page'" : "") + ">" + esc(x[1]) + "</a>";
    }).join("");
  }

  function html(data, active) {
    var rec = data.record, m = data.metrics, c = rec.company;
    var name = c.name || c.sec_name || "CIK " + c.cik;
    var tags = [];
    if (c.ticker) tags.push("<span class='tk'>" + esc(c.ticker) + "</span>");
    if (c.exchange) tags.push("<span>" + esc(c.exchange) + "</span>");
    if (c.industry) tags.push("<span>" + esc(sentenceCase(c.industry)) + "</span>");
    if (c.sp500_weight_pct != null) tags.push("<span title='" + esc(fmtNum(c.sp500_weight_pct, 2)) + "% of the S&amp;P 500 fund (SPY)'>S&amp;P 500</span>");
    var meta = ["SEC number " + c.cik];
    if (c.website) meta.push("<a href='https://" + esc(c.website) + "' rel='noopener'>" + esc(c.website) + "</a>");
    (c.predecessors || []).forEach(function (p) {
      meta.push("earlier years filed as " + esc(p.sec_name) + " (" + esc((p.first_filed || "").slice(0, 4)) + "–" +
        esc((p.last_filed || "").slice(0, 4)) + ")");
    });
    var px = m && m.price && m.price.close != null ? m.price : null;
    var chg = m && m.metrics ? m.metrics.price_change_1y_pct : null;
    var price = px ? "<div class='cp-price'><div class='px'>$" + fmtNum(px.close, 2) + "</div>" +
      "<div class='px-sub'>Close, " + esc(dayShort(px.date)) +
      (chg != null ? " · 1 year <span class='" + (chg >= 0 ? "up" : "down") + "' title='close ÷ the close a year earlier − 1, split-adjusted'>" +
        (chg >= 0 ? "▲ +" : "▼ " + MINUS) + fmtNum(Math.abs(chg), 1) + "%</span>" : "") + "</div></div>" : "";
    return "<section class='cp-card'><div class='cp-id'>" +
      (c.logo ? "<img class='cp-logo' src='" + esc(c.logo) + "' alt='' width='44' height='44'>" : "") +
      "<div class='cp-who'><div class='cp-title'><h1 id='co-name'>" + esc(name) + "</h1>" + followHtml(c, name) + "</div>" +
      "<div class='cp-tags'>" + tags.join("") + "</div>" +
      "<div class='cp-meta'>" + meta.join(" · ") + "</div></div>" + price + "</div>" +
      "<nav class='cp-tabs' aria-label='This company'>" + tabs(rec, active) + "</nav></section>";
  }

  // A phone shows the tabs as a strip that scrolls sideways: bring the current
  // tab into view, so the reader can see where they are.
  function reveal(root) {
    var nav = root && root.querySelector(".cp-tabs"), cur = nav && nav.querySelector("[aria-current]");
    if (cur && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, cur.offsetLeft - 12);
    follow(root);
  }

  // ---- Follow (2026-11-14) ---------------------------------------------------
  // The button beside the name adds the company to the reader's Watchlist, the
  // per-account list Your Plover shows (/api/v1/assistant/coverage), so
  // following is the reason to sign up. Signed out, it goes to sign-in and back
  // with ?follow=1, which follows the company on arrival. The button keeps its
  // space but stays invisible until the reader's state is known, so it never
  // flashes "Follow" at someone already following.
  var COV = "/api/v1/assistant/coverage";
  var WATCHLIST = "my.html#companies";

  function followHtml(c, name) {
    var cik = parseInt(c.cik, 10);
    if (!cik) return "";
    // the Watchlist keeps a US company as cik:<number>, no leading zeros
    return "<span class='cp-follow is-wait' data-code='cik:" + cik + "' data-name='" + esc(name) + "'>" +
      "<button type='button' class='btn cp-follow-btn' aria-pressed='false'>+ Follow</button>" +
      "<span class='cp-follow-note' aria-live='polite'></span></span>";
  }

  function withoutFollowParam() {
    var p = new URLSearchParams(location.search);
    p.delete("follow");
    var s = p.toString();
    return location.pathname + (s ? "?" + s : "") + location.hash;
  }

  function follow(root) {
    var box = root && root.querySelector(".cp-follow");
    if (!box || box.ploverWired) return;
    box.ploverWired = true;
    var btn = box.querySelector("button"), note = box.querySelector(".cp-follow-note");
    var code = box.getAttribute("data-code"), name = box.getAttribute("data-name");
    var asked = new URLSearchParams(location.search).get("follow") === "1";
    // "Apple Inc." already ends in a full stop
    function followingNow() { return "Following " + name + (/\.$/.test(name) ? "" : ".") + " It is on your Watchlist."; }

    function show(on, msg, link) {
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "✓ Following" : "+ Follow";
      btn.title = on ? "On your Watchlist. Click to stop following." : "Add " + name + " to your Watchlist";
      note.innerHTML = msg ? esc(msg) + (link ? " <a href='" + WATCHLIST + "'>Open Watchlist</a>" : "") : "";
      box.classList.remove("is-wait");
    }
    function failed(r, what) {
      box.classList.remove("is-wait");
      return (r && r.json ? r.json().catch(function () { return {}; }) : Promise.resolve({})).then(function (b) {
        note.textContent = (b && typeof b.detail === "string" ? b.detail : what + " just now.") + " Try again.";
      });
    }
    function add(arrived) {
      return fetch(COV, { method: "POST", headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ code: code }) }).then(function (r) {
        if (!r.ok) return failed(r, "Could not follow " + name);
        show(true, arrived ? followingNow() : "Added to your Watchlist.", true);
      });
    }
    function remove() {
      return fetch(COV + "/" + encodeURIComponent(code), { method: "DELETE" }).then(function (r) {
        if (!r.ok) return failed(r, "Could not stop following " + name);
        show(false, "Removed from your Watchlist.");
      });
    }

    fetch("/api/v1/account/me", { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 404) { box.remove(); return; }            // no accounts on this server
      if (!r.ok) {
        // signed out: sign in, then come back to this page and follow
        if (asked) history.replaceState(null, "", withoutFollowParam());
        var back = withoutFollowParam();
        back += (back.indexOf("?") < 0 ? "?" : "&") + "follow=1";
        show(false, "");
        btn.onclick = function () { location.href = "signin.html?returnTo=" + encodeURIComponent(back); };
        return;
      }
      return fetch(COV, { headers: { Accept: "application/json" } }).then(function (c) {
        return c.ok ? c.json() : { coverage: [] };
      }).then(function (body) {
        var on = (body.coverage || []).some(function (x) { return String(x.sec_code).toLowerCase() === code; });
        btn.onclick = function () { return btn.getAttribute("aria-pressed") === "true" ? remove() : add(false); };
        if (asked) {
          history.replaceState(null, "", withoutFollowParam());
          if (!on) return add(true);
          show(true, followingNow(), true);
          return;
        }
        show(on, "");
      });
    }).catch(function () { box.remove(); });
  }

  window.coHead = { load: load, html: html, dayShort: dayShort, reveal: reveal };
})();
