/* US figures as words a reader can use: every number with its unit, tables
   whose cells guess their unit from the column header, and the sources block.
   Shared by the ask box (us-stocks.js) and a company's Price and Insiders and
   holders page (us-stock.js), so the two never format one figure two ways.
   Needs format.js (fmtNum, MISSING, MINUS, MONTHS, escapeHtml). */
(function () {
  "use strict";

  function esc(s) { return escapeHtml(String(s == null ? "" : s)); }

  /* ---- formatting: every number carries its unit ---------------------- */
  function money(v, dp) {
    if (v == null) return MISSING;
    var a = Math.abs(v), s;
    if (a >= 1e12) s = fmtNum(a / 1e12, 2) + "tn";
    else if (a >= 1e11) s = fmtNum(a / 1e9, 0) + "bn";
    else if (a >= 1e9) s = fmtNum(a / 1e9, 1) + "bn";
    else if (a >= 1e6) s = fmtNum(a / 1e6, 1) + "mn";
    else if (a >= 1e3) s = fmtNum(a, 0);
    else s = fmtNum(a, dp == null ? 2 : dp);
    return (v < 0 ? MINUS : "") + "$" + s;
  }
  function pct(v, dp, signed) {
    if (v == null) return MISSING;
    var s = fmtNum(Math.abs(v), dp == null ? 1 : dp) + "%";
    if (v < 0) return MINUS + s;
    return (signed && v > 0 ? "+" : "") + s;
  }
  function mult(v) { return v == null ? MISSING : fmtNum(v, 1) + "×"; }
  function count(v) { return v == null ? MISSING : fmtNum(v, 0); }
  function dateWords(iso) {
    if (!iso) return MISSING;
    var d = new Date(iso.slice(0, 10) + "T00:00:00Z");
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
  }
  function fmtBy(unit, v, dp) {
    if (v == null) return MISSING;
    if (unit === "USD") return money(v, dp);
    if (unit === "%") return pct(v, dp == null ? 1 : dp, false);
    if (unit === "×") return mult(v);
    if (unit === "count" || unit === "shares") return count(v);
    if (unit === "date") return dateWords(v);
    if (typeof v === "number") return fmtNum(v, dp == null ? 2 : dp);
    return esc(v);
  }
  // Table cells: the column header says what the column is; the cell guesses
  // its unit from the header words so a reader never sees a bare 5074023879600.
  function cellFor(col, v, unit) {
    if (v == null || v === "") return "<td class='r'>" + MISSING + "</td>";
    if (typeof v === "boolean") return "<td>" + (v ? "Yes" : "No") + "</td>";
    if (typeof v === "number") {
      var c = col.toLowerCase();
      if (/\(usd\)/.test(c)) return "<td class='r'>" + money(v) + "</td>";
      // a change counted in shares is a number of shares, not a percent ("Change in shares": +16,385,212)
      if (/change/.test(c) && /shares/.test(c) && !/%/.test(c)) return "<td class='r'>" + (v > 0 ? "+" : v < 0 ? MINUS : "") + fmtNum(Math.abs(v), 0) + "</td>";
      if (/change/.test(c) && !/close|price/.test(c)) return "<td class='r'>" + pct(v, 1, true) + "</td>";
      if (/\(\u00d7\)/.test(c)) return "<td class='r'>" + mult(v) + "</td>";
      if (/\(pp\)/.test(c)) return "<td class='r'>" + (v > 0 ? "+" : v < 0 ? "\u2212" : "") + fmtNum(Math.abs(v), 2) + " pp</td>";
      if (/share of|%|change %|pct|margin|yield|roe|growth|ratio/.test(c) && !/shares|rank/.test(c)) return "<td class='r'>" + pct(v, 1, /change/.test(c)) + "</td>";
      if (/p\/e/.test(c)) return "<td class='r'>" + mult(v) + "</td>";
      if (/rank|holders|insiders|count|trades|positions|days/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/shares|volume/.test(c)) return "<td class='r'>" + count(v) + "</td>";
      if (/price|close/.test(c) && !/change/.test(c)) return "<td class='r'>" + money(v, 2) + "</td>";
      if (/value|revenue|income|assets|equity|cap|buyback|dividend|net|cash|fcf|both|total|prior/.test(c) || unit === "USD") return "<td class='r'>" + money(v) + "</td>";
      return "<td class='r'>" + fmtNum(v, Math.abs(v) < 100 ? 2 : 0) + "</td>";
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return "<td>" + dateWords(v) + "</td>";
    if (/^\d{4}-\d{2}$/.test(String(v))) return "<td>" + esc(v) + "</td>";
    var s = String(v);
    if (/^cik:|^\d{10}-\d{2}-\d{6}$/.test(s)) return "<td class='quiet'>" + esc(s) + "</td>";
    if (/^[A-Z][A-Z0-9.-]{0,9}$/.test(s) && /ticker/.test(col.toLowerCase())) return "<td>" + (window.usLogos ? usLogos.img(s, 16) : "") + "<a href='/us-stock.html?t=" + encodeURIComponent(s) + "'>" + esc(s) + "</a></td>";
    return "<td class='txt'>" + esc(s.length > 60 ? s.slice(0, 57) + "…" : s) + "</td>";
  }

  function tableHtml(t, id) {
    if (!t || !t.rows || !t.rows.length) return "";
    var head = "<thead><tr>" + t.columns.map(function (c, i) {
      var numeric = t.rows.some(function (r) { return typeof r[i] === "number"; });
      return "<th" + (numeric ? " class='r'" : "") + ">" + esc(c) + "</th>";
    }).join("") + "</tr></thead>";
    var body = "<tbody>" + t.rows.map(function (r) {
      return "<tr class='hit'>" + r.map(function (v, i) { return cellFor(t.columns[i], v, t.unit); }).join("") + "</tr>";
    }).join("") + "</tbody>";
    return "<div class='table-wrap'><table class='tbl-fin' id='" + id + "'>" + head + body + "</table></div>";
  }

  function sourcesHtml(srcs, calc, entities, match) {
    var items = (srcs || []).map(function (s) {
      return "<li><b>" + esc(s.name) + ".</b> " + esc(s.credit) + (s.note ? " " + esc(s.note) : "") + "</li>";
    });
    var calcItems = Object.keys(calc || {}).filter(function (k) { return k !== "query" && k !== "steps"; }).map(function (k) {
      return "<li><b>" + esc(k.replace(/_/g, " ")) + ":</b> " + esc(calc[k]) + "</li>";
    });
    var read = [];
    if (entities) {
      if (entities.mentioned_companies && entities.mentioned_companies.length) {
        read.push("company: " + entities.mentioned_companies.map(function (c) { return c.name + " (" + (c.ticker || "cik " + c.cik) + ", by " + c.matched_by + ")"; }).join("; "));
      }
      if (entities.industry_read) read.push("industry: " + entities.industry_read);
      if (entities.manager_read) read.push("manager: " + entities.manager_read);
      if (entities.since_read) read.push("since: " + dateWords(entities.since_read));
      if (entities.amount_read) read.push("amount: " + money(entities.amount_read));
    }
    return "<details class='calc'><summary>Sources and formulas</summary><div class='calc-body'>" +
      (match ? "<p class='quiet'>Question shape: " + esc(match.label) + ". " + (read.length ? "Read as — " + esc(read.join("; ")) + "." : "") + "</p>" : "") +
      (calcItems.length ? "<ul class='sources'>" + calcItems.join("") + "</ul>" : "") +
      (calc && calc.query ? "<p class='quiet'>The query run on the data:</p><pre class='sql'>" + esc(calc.query) + "</pre>" : "") +
      "<ul class='sources'>" + items.join("") + "</ul></div></details>";
  }

  window.usFmt = { esc: esc, money: money, pct: pct, mult: mult, count: count, dateWords: dateWords,
                   fmtBy: fmtBy, cellFor: cellFor, tableHtml: tableHtml, sourcesHtml: sourcesHtml };
})();
