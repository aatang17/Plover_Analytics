/* The cookie banner, and the two things a page tells the server about itself.
 *
 * Loaded on every page by the server (app/prerender.py), so it is written to
 * cost nothing: no dependencies, nothing drawn until the reader has to be
 * asked, and every request fire-and-forget. If any of it fails the page is
 * unaffected — the server still counts the visit on its own.
 *
 * What is measured without asking: how long this page stayed open, and a
 * keep-alive while it is. Neither stores anything on the reader's machine.
 * What needs the banner: a cookie holding one random number, which is the
 * only way to tell a reader who came back from a reader who is new.
 */
(function () {
  "use strict";

  var CONSENT_COOKIE = "pa_consent";
  var PING = "/api/v1/visit/ping";
  var CONSENT = "/api/v1/visit/consent";
  var HEARTBEAT_MS = 60000;
  var METHODOLOGY = "methodology.html#cookies";

  /* The console and the research desk are not readership and never ask
     their reader anything; nor does the Send to Plover window. */
  if (/\/(admin|desk|clip)(\.html)?$/.test(location.pathname)) return;

  function cookie(name) {
    var parts = ("; " + document.cookie).split("; " + name + "=");
    return parts.length === 2 ? parts.pop().split(";").shift() : "";
  }

  /* ---------------- how long this page was open ----------------
     The server sees requests, not reading: a reader who opens one page and
     stays on it makes exactly one. So the page keeps its own clock, counting
     only the time it was actually visible, and reports the total when it goes
     away. Each report carries the same id for this page view, so a reader who
     leaves a tab and comes back is one reading, not three. */
  var view = String(Math.random()).slice(2, 10);
  var visibleMs = 0;
  var since = document.visibilityState === "visible" ? Date.now() : 0;
  var path = location.pathname;

  function visibleSeconds() {
    var total = visibleMs + (since ? Date.now() - since : 0);
    return Math.round(total / 1000);
  }

  function send(body, beacon) {
    var payload = JSON.stringify(body);
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon(PING, payload);
        return;
      }
      fetch(PING, { method: "POST", body: payload, keepalive: true });
    } catch (e) { /* counting must never break a page */ }
  }

  function report(beacon) {
    var seconds = visibleSeconds();
    if (seconds > 0) send({ path: path, view: view, seconds: seconds, closed: true }, beacon);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      since = Date.now();
    } else {
      visibleMs += since ? Date.now() - since : 0;
      since = 0;
      report(true);
    }
  });
  window.addEventListener("pagehide", function () { report(true); });

  /* Here, and still here. The first report is what puts a reader in the
     "right now" count at all (a page that never runs this is not counted);
     the rest keep somebody sitting on one page inside its five minutes.
     Writes nothing to the log. */
  if (document.visibilityState === "visible") send({ path: path }, false);
  window.setInterval(function () {
    if (document.visibilityState === "visible") send({ path: path }, false);
  }, HEARTBEAT_MS);

  /* ---------------- the banner ---------------- */

  function choose(choice, banner) {
    try {
      fetch(CONSENT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice: choice })
      });
    } catch (e) { /* the banner still closes; the choice is simply not stored */ }
    if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    document.body.classList.remove("has-consent-bar");
    var link = document.querySelector(".consent-reopen");
    if (link) link.textContent = choice === "granted" ? "Cookies: on" : "Cookies: off";
  }

  function ask() {
    var bar = document.createElement("div");
    bar.className = "consent-bar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Cookies");
    bar.innerHTML =
      '<div class="inner">' +
      "<p>Counting visits here needs no cookies and stores no addresses. One cookie " +
      "holding a random number would additionally tell us when a reader comes back. " +
      'Declining changes nothing on this site. <a href="' + METHODOLOGY +
      '">How we count</a></p>' +
      '<div class="consent-actions">' +
      '<button type="button" class="btn" data-choice="denied">Decline</button>' +
      '<button type="button" class="btn btn-primary" data-choice="granted">Accept</button>' +
      "</div></div>";
    bar.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("button[data-choice]") : null;
      if (button) choose(button.getAttribute("data-choice"), bar);
    });
    document.body.appendChild(bar);
    document.body.classList.add("has-consent-bar");
  }

  /* A way back to the choice, in the place a reader looks for it. Added to the
     footer every page already has, rather than a new page nobody visits. */
  function footerLink() {
    var footer = document.querySelector(".site-footer .inner");
    if (!footer || footer.querySelector(".consent-reopen")) return;
    var link = document.createElement("a");
    link.className = "consent-reopen";
    link.href = "#";
    link.textContent = cookie(CONSENT_COOKIE) === "granted" ? "Cookies: on" : "Cookies: off";
    link.addEventListener("click", function (event) {
      event.preventDefault();
      if (!document.querySelector(".consent-bar")) ask();
    });
    // Separated, or it runs straight on from the Methodology link and the two
    // read as one phrase.
    footer.appendChild(document.createTextNode(" \u00b7 "));
    footer.appendChild(link);
  }

  function start() {
    footerLink();
    /* A browser sending Global Privacy Control has already answered. Asking
       again would be asking someone to repeat themselves. */
    if (navigator.globalPrivacyControl) return;
    if (!cookie(CONSENT_COOKIE)) ask();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
