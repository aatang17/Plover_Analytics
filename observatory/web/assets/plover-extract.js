/* The article on the page the reader is on, as Plover keeps it: title, site,
   date, byline and the text as blocks (heading, paragraph, quote).

   One function, used three ways, so they can never read a page differently:
   - the Plover browser extension runs it in the reader's tab
     (chrome.scripting.executeScript; the extension's zip carries this file);
   - the Save to Plover bookmark on my.html carries its source text;
   - nothing else: it never runs on Plover's own pages.

   It must stay self-contained (no outside names, no modern syntax a
   bookmark cannot carry): both callers send its source text into another
   page. Adapted from the Writer Desk's ploverClip (desk-clip.js). If the
   reader has selected a passage, that passage is what is kept. */
function ploverExtract() {
  var d = document, out = [], seen = {}, n = 0, MAX = 60000, cut = false;
  function meta(k) {
    var m = d.querySelector('meta[property="' + k + '"],meta[name="' + k + '"],meta[itemprop="' + k + '"]');
    return m ? (m.getAttribute("content") || "").trim() : "";
  }
  function push(kind, t) {
    t = (t || "").replace(/\s+/g, " ").trim();
    if (!t || seen[t]) return;
    if (n + t.length > MAX) { cut = true; return; }
    seen[t] = 1; n += t.length; out.push({ kind: kind, text: t });
  }
  function ptext(el) {
    var s = 0, ps = el.querySelectorAll("p");
    for (var i = 0; i < ps.length; i++) s += (ps[i].textContent || "").length;
    return s;
  }
  var sel = window.getSelection ? String(window.getSelection()) : "";
  if (sel.replace(/\s+/g, "").length > 40) {
    sel.split(/\n+/).forEach(function (t) { push("p", t); });
  } else if (d.body) {
    var all = ptext(d.body), root = d.body;
    var tries = ['[itemprop="articleBody"]', "#article-body", ".article-body", "article", "main", '[role="main"]'];
    for (var i = 0; i < tries.length && root === d.body; i++) {
      var els = d.querySelectorAll(tries[i]);
      for (var j = 0; j < els.length; j++) {
        var t = ptext(els[j]);
        if (t >= 300 && t >= all * 0.4) { root = els[j]; break; }
      }
    }
    var skip = "nav,footer,aside,form,figure,figcaption,button,[aria-hidden='true'],[hidden]";
    var els2 = root.querySelectorAll("h2,h3,h4,p,li,blockquote");
    for (var k = 0; k < els2.length; k++) {
      var el = els2[k], tag = el.tagName;
      if (el.closest(skip) || !el.getClientRects().length) continue;
      if (tag !== "BLOCKQUOTE" && el.parentElement && el.parentElement.closest("blockquote")) continue;
      if (tag === "LI" && el.querySelector("p")) continue;
      push(tag === "BLOCKQUOTE" ? "quote" : (tag.charAt(0) === "H" ? "h" : "p"), el.textContent);
    }
  }
  /* Sites that keep the date and byline only in their structured data. */
  var ld = {};
  var lds = d.querySelectorAll('script[type="application/ld+json"]');
  for (var q = 0; q < lds.length && !ld.datePublished; q++) {
    try {
      var x = JSON.parse(lds[q].textContent), xs = [].concat(x["@graph"] || x);
      for (var r = 0; r < xs.length; r++) if (xs[r] && xs[r].datePublished) { ld = xs[r]; break; }
    } catch (e) { /* not ours to fix */ }
  }
  var by = [].concat(ld.author || []).map(function (a) { return a && (a.name || a); })
    .filter(function (a) { return typeof a === "string"; }).join(", ");
  var canon = d.querySelector('link[rel="canonical"]'), url = location.href.split("#")[0];
  if (canon && canon.href && canon.href.indexOf(location.protocol + "//" + location.hostname) === 0) url = canon.href;
  return {
    url: url,
    title: meta("og:title") || d.title,
    site: meta("og:site_name") || location.hostname,
    published: meta("article:published_time") || meta("datePublished") || ld.datePublished || meta("date") || "",
    author: meta("author") || by || "",
    selection: sel.replace(/\s+/g, "").length > 40,
    cut: cut,
    blocks: out
  };
}
