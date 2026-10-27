/* Company logos on any page that lists US companies: one small map from
   /api/v1/us/company/logos (ticker and "cik:N" -> the logo's address), fetched
   once per page. usLogos.img(key, size) gives the <img> for a ticker or
   "cik:N", an empty slot of the same size where the company has none (so
   names stay aligned), and "" until the map has arrived; usLogos.at(url,
   size) the same for a logo address already in hand. */
window.usLogos = (function () {
  "use strict";
  var map = null;
  var ready = fetch("/api/v1/us/company/logos", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : { logos: {} }; })
    .then(function (d) { map = d.logos || {}; return map; })
    .catch(function () { map = {}; return map; });

  function url(key) {
    if (!map || key == null) return null;
    var k = String(key).trim();
    return map[k] || map[k.toUpperCase()] || null;
  }

  function img(key, size) {
    if (!map) return "";
    return at(url(key), size);
  }

  // the same <img> (or empty slot) for a logo address already in hand: a
  // Japanese company's, which comes with the company's own record
  function at(u, size) {
    var s = size || 20;
    var box = "display:inline-block;width:" + s + "px;height:" + s + "px;margin-right:" + Math.round(s * 0.4) +
      "px;vertical-align:" + (-Math.round(s * 0.25)) + "px;border-radius:3px;";
    if (!u) return "<span aria-hidden='true' style='" + box + "'></span>";
    return "<img src='" + escapeHtml(u) + "' alt='' width='" + s + "' height='" + s + "' loading='lazy' style='" + box +
      "background:var(--obs-logo-bg);object-fit:contain'>";
  }

  return { ready: ready, url: url, img: img, at: at };
})();
