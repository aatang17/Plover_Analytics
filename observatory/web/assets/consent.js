/* The cookie banner, and the two things a page tells the server about itself.
 *
 * Loaded on every page by the server (app/prerender.py), so it is written to
 * cost nothing: no dependencies, nothing drawn until the reader has to be
 * asked. Measurement requests are fire-and-forget; consent saves wait for
 * confirmation so the reader can retry a failed save. If counting fails the page is
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
  var EARLY_SECONDS = 5;
  var METHODOLOGY = "methodology.html#cookies";
  var ADS = !!document.querySelector('script[data-reddit-ads="enabled"]');
  var ADS_COOKIE = "pa_reddit_consent";
  var clickId = new URLSearchParams(location.search).get("rdt_cid") || "";
  if (!/^[A-Za-z0-9_-]{1,512}$/.test(clickId)) clickId = "";

  function saveAds(choice) {
    return fetch("/api/v1/ads/reddit-consent", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice: choice, click_id: clickId })
    }).then(function (response) {
      if (!response.ok) throw new Error("Could not save advertising choice");
    });
  }

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

  /* Five seconds in view: an early report, written to the log. The closing
     report is often lost (an app's built-in browser shut with the app, a
     phone that kills the tab), and without one a reader who stayed was taken
     for a machine. This one only says a browser showed the page; the closing
     report alone says how long. */
  var earlySent = false;
  function early() {
    if (earlySent) return;
    if (visibleSeconds() >= EARLY_SECONDS) {
      earlySent = true;
      send({ path: path, view: view, seconds: visibleSeconds(), early: true }, false);
      return;
    }
    window.setTimeout(early, 1000);
  }
  window.setTimeout(early, EARLY_SECONDS * 1000);

  /* Here, and still here. The first report is what puts a reader in the
     "right now" count at all (a page that never runs this is not counted);
     the rest keep somebody sitting on one page inside its five minutes.
     Writes nothing to the log. */
  if (document.visibilityState === "visible") send({ path: path }, false);
  window.setInterval(function () {
    if (document.visibilityState === "visible") send({ path: path }, false);
  }, HEARTBEAT_MS);

  /* ---------------- the banner ---------------- */

  async function choose(choice, banner) {
    var buttons = banner.querySelectorAll("button");
    if (banner.dataset.saving) return;
    banner.dataset.saving = "yes";
    buttons.forEach(function (button) { button.disabled = true; });
    banner.querySelector(".consent-status").textContent = "Saving choices…";
    try {
      var analytics = ADS ? banner.querySelector('[name="returning-visits"]').checked : choice === "granted";
      var response = await fetch(CONSENT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice: choice === "denied" ? "denied" : analytics ? "granted" : "denied" })
      });
      if (!response.ok) throw new Error("Could not save cookie choice");
      if (ADS) await saveAds(choice !== "denied" && !navigator.globalPrivacyControl &&
        banner.querySelector('[name="reddit-measurement"]').checked ? "granted" : "denied");
    } catch (e) {
      banner.querySelector(".consent-status").textContent = "Could not save. Please try again.";
      delete banner.dataset.saving;
      buttons.forEach(function (button) { button.disabled = false; });
      return;
    }
    if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    document.body.classList.remove("has-consent-bar");
    document.body.style.removeProperty("--pa-consent-height");
    var link = document.querySelector(".consent-reopen");
    if (link) link.textContent = ADS ? "Cookie settings" : choice === "granted" ? "Cookies: on" : "Cookies: off";
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
    if (ADS) {
      bar.innerHTML = '<div class="inner"><div class="consent-options">' +
        '<p>Optional cookies. Your choice does not affect access. <a href="' + METHODOLOGY + '">Privacy details</a></p>' +
        '<label><input type="checkbox" name="returning-visits"> Count returning visits with a random cookie.</label>' +
        '<label><input type="checkbox" name="reddit-measurement"> Measure Reddit ads: share the ad click ID and a completed signup with Reddit. No email or research activity.</label>' +
        '<p class="consent-status" role="status"></p></div><div class="consent-actions">' +
        '<button type="button" class="btn" data-choice="denied">Decline all</button>' +
        '<button type="button" class="btn btn-primary" data-choice="selected">Save choices</button></div></div>';
      bar.querySelector('[name="returning-visits"]').checked = cookie(CONSENT_COOKIE) === "granted";
      var adChoice = bar.querySelector('[name="reddit-measurement"]');
      adChoice.checked = !navigator.globalPrivacyControl && cookie(ADS_COOKIE) === "granted";
      adChoice.disabled = !!navigator.globalPrivacyControl;
      if (navigator.globalPrivacyControl) bar.querySelector(".consent-status").textContent = "Reddit measurement is off because your browser sends Global Privacy Control.";
    } else {
      bar.querySelector("p").insertAdjacentHTML("afterend", '<p class="consent-status" role="status"></p>');
    }
    bar.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("button[data-choice]") : null;
      if (button) choose(button.getAttribute("data-choice"), bar);
    });
    document.body.appendChild(bar);
    document.body.classList.add("has-consent-bar");
    function measure() {
      document.body.style.setProperty("--pa-consent-height", Math.ceil(bar.getBoundingClientRect().height) + "px");
    }
    measure();
    if (window.ResizeObserver) {
      var observer = new ResizeObserver(function () {
        if (bar.isConnected) measure(); else observer.disconnect();
      });
      observer.observe(bar);
    }
  }

  /* A way back to the choice, in the place a reader looks for it. Added to the
     footer every page already has, rather than a new page nobody visits. */
  function footerLink() {
    var footer = document.querySelector(".site-footer .inner");
    if (!footer || footer.querySelector(".consent-reopen")) return;
    // The contact address rides along: every page writes its own footer, and
    // this is the one script that reaches all of them.
    if (!footer.querySelector('a[href$="contact.html"]')) {
      var contact = document.createElement("a");
      contact.href = "/contact.html";
      contact.textContent = "Contact";
      footer.appendChild(document.createTextNode(" · "));
      footer.appendChild(contact);
    }
    var link = document.createElement("a");
    link.className = "consent-reopen";
    link.href = "#";
    link.textContent = ADS ? "Cookie settings" : cookie(CONSENT_COOKIE) === "granted" ? "Cookies: on" : "Cookies: off";
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
    if (navigator.globalPrivacyControl) {
      if (ADS && cookie(ADS_COOKIE) === "granted") saveAds("denied").catch(function () {});
      return;
    }
    if (ADS && clickId && cookie(ADS_COOKIE) === "granted") saveAds("granted").catch(function () {});
    if (!cookie(CONSENT_COOKIE) || (ADS && clickId && !cookie(ADS_COOKIE))) ask();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
