/* Sign in. The question this screen answers: "how do I get into my account?"

   One card, one field, four states:

     ask        the email field and one button
     sent       "check your email" — replaces the form, keeps the address on
                screen so a typo is visible
     redeeming  ?token=… in the URL, being exchanged for a session
     dead       the link was used already, or is older than 30 minutes

   Signing in and signing up are the same action: clicking the emailed link
   proves the address and creates the session, so there is no second form and
   nothing here says "register".

   There is no password anywhere in this file, and never should be. An account
   holds the companies a reader follows; everything else on the platform is
   readable without one — so a failure here costs the reader their list, not
   the data, and the copy says so rather than pretending the site is locked.

   Accounts are a server feature that may not be switched on: when
   /api/v1/account/me is not there at all, the page says so plainly instead of
   showing a form that cannot work. */
"use strict";

const ACCOUNT_API = "/api/v1/account";
const LINK_MINUTES = 30;

const card = document.getElementById("card");

/* Only a same-site relative path is ever followed back. */
function safeReturnTo(raw) {
  if (raw && raw.charAt(0) === "/" && raw.slice(0, 2) !== "//") return raw;
  if (raw && /^[a-z0-9-]+\.html(\?[^#]*)?$/i.test(raw)) return raw;
  return "my.html";
}

const params = new URLSearchParams(location.search);
const token = (params.get("token") || "").trim();
const returnTo = safeReturnTo(params.get("returnTo"));

function render(html) { card.innerHTML = html; }

function field(value, error) {
  return (error ? '<div class="auth-alert" role="alert">' + escapeHtml(error) + "</div>" : "") +
    '<label class="auth-label" for="email">Email address</label>' +
    '<input class="auth-input" id="email" type="email" name="email" autocomplete="email" ' +
    'inputmode="email" spellcheck="false" required value="' + escapeHtml(value || "") + '">' +
    '<button class="btn btn-primary auth-btn" type="submit" id="send">Email Me a Sign-in Link</button>' +
    '<p class="auth-hint">An account saves the companies you follow. Everything else on the ' +
    "platform stays free to read without one.</p>";
}

function askState(value, error) {
  render('<h1 class="auth-title">Sign In</h1>' +
    '<p class="auth-sub">Enter your email and we will send a single-use sign-in link. ' +
    "There is no password.</p>" +
    '<form id="form" novalidate>' + field(value, error) + "</form>");
  const form = document.getElementById("form");
  const input = document.getElementById("email");
  input.focus();
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const address = input.value.trim();
    if (!address || address.indexOf("@") < 1) {
      return askState(address, "Enter the email address you want the link sent to.");
    }
    sending(address);
  });
}

function sending(address) {
  const btn = document.getElementById("send");
  btn.disabled = true;
  btn.textContent = "Sending…";
  fetch(ACCOUNT_API + "/signin-link", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: address, returnTo: returnTo }),
  }).then(function (r) {
    if (r.status === 404) return offState();
    if (r.ok) return sentState(address);
    // A delivery failure is a real error and says so. Telling a reader to check
    // an inbox that will never receive anything leaves them no way back in.
    return r.json().catch(function () { return null; }).then(function (body) {
      askState(address, (body && body.detail) ||
        "We could not send the email just now. Try again in a few minutes.");
    });
  }).catch(function () {
    askState(address, "We could not reach the server. Check your connection and try again.");
  });
}

function sentState(address) {
  render('<h1 class="auth-title">Check your email</h1>' +
    '<div class="auth-sent" role="status">' +
    "<p>We sent a sign-in link to <strong>" + escapeHtml(address) + "</strong>. " +
    "It works once and expires in " + LINK_MINUTES + " minutes.</p>" +
    '<button class="auth-link" type="button" id="again">Use a different address</button>' +
    "</div>" +
    '<p class="auth-hint">Nothing arrived? It can take a minute, and it may be filed as ' +
    "promotions. Requesting a new link cancels the old one.</p>");
  document.getElementById("again").addEventListener("click", function () { askState(""); });
}

function redeemingState() {
  render('<h1 class="auth-title">Signing you in…</h1>' +
    '<p class="auth-sub">One moment — we are confirming your link.</p>');
  fetch(ACCOUNT_API + "/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token }),
  }).then(function (r) {
    if (r.status === 404) return offState();
    if (!r.ok) return deadState();
    // A full navigation, not a pushState: the session cookie has just been set
    // and the header renders the account control against it on load.
    location.href = returnTo;
  }).catch(deadState);
}

function deadState() {
  render('<h1 class="auth-title">This link has expired</h1>' +
    '<p class="auth-sub">Sign-in links work once and expire after ' + LINK_MINUTES +
    " minutes. Ask for a new one and it will arrive in a moment.</p>" +
    '<button class="btn btn-primary auth-btn" type="button" id="new">Send Me a New Link</button>');
  document.getElementById("new").addEventListener("click", function () { askState(""); });
}

/* Accounts are not switched on for this deployment. Say that, rather than
   showing a form that can only fail. */
function offState() {
  render('<h1 class="auth-title">Accounts are not open yet</h1>' +
    '<p class="auth-sub">Sign-in is being built. Every dataset on the platform is readable ' +
    "without an account in the meantime.</p>" +
    '<p class="auth-hint"><a href="index.html">Back to the platform</a></p>');
}

/* Every state has to be looked at before this page can be called done, and
   three of the four need a server that may not exist yet. ?preview=ask|sent|
   dead|off renders one without touching the API. It reads no credential and
   creates no session — it is the design surface, not a way in. */
const PREVIEWS = {
  ask: function () { askState("you@firm.com"); },
  error: function () { askState("you@firm.com", "We could not send the email just now. Try again in a few minutes."); },
  sent: function () { sentState("you@firm.com"); },
  dead: deadState,
  off: offState,
};

document.addEventListener("DOMContentLoaded", function () {
  if (window.initThemeToggle) initThemeToggle();
  const preview = PREVIEWS[params.get("preview") || ""];
  if (preview) return preview();
  // Is the feature there at all? One HEAD-shaped call decides which screen a
  // reader gets, and a reader who is already signed in never sees the form.
  fetch(ACCOUNT_API + "/me", { headers: { "Accept": "application/json" } })
    .then(function (r) {
      if (r.status === 404) return offState();
      if (r.ok) return r.json().then(function (me) {
        render('<h1 class="auth-title">You are signed in</h1>' +
          '<p class="auth-sub">as <strong>' + escapeHtml(me.email || "") + "</strong></p>" +
          '<p class="auth-hint"><a href="' + escapeHtml(returnTo) + '">Continue</a></p>');
      });
      if (token) return redeemingState();
      return askState("");
    })
    .catch(function () {
      if (token) return redeemingState();
      askState("");
    });
});
