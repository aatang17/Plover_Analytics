/* The site header, rendered from one description of the platform.

   Every page once carried its own hand-copied header, and they had already
   drifted apart — the cross-shareholding page listed four destinations, the
   CPI pages three, and neither offered the AI connector at all. The structure
   below is now the only place a section, a page, or a label is named.

   A page declares where it sits and includes this script directly after its
   header shell, so the bar is in the DOM before any page script looks for it:

     <header class="site-header" data-section="macro" data-page="explorer"></header>
     <script src="assets/nav.js"></script>

   Two tiers. The navy bar carries the markets — Japan, Hong Kong, United
   States — in full words, plus Docs and the reader's controls at the right: which country
   you are looking at is the first thing every page says. The light strip
   beneath it carries the current market's sections; a section with more than
   one page opens a menu of them. A small trail at the top of the content
   (Japan / Macro / Yield Curve) repeats the place in words. The landing page
   belongs to no section and sets data-section="" — it gets the last market
   visited, with no section marked current.

   A third tier exists only where one destination has more than one view of the
   same data (Inflation: the headline page and the item table). Those are the
   page entry's `tabs`, and they are deliberately NOT a third sticky bar — the
   strip is rendered inside the content column, into a placeholder the page
   puts at the top of <main>:

     <div class="page-tabs" data-page-tabs></div>

   The placeholder is filled on DOMContentLoaded, since <main> does not exist
   yet when this script runs. A page with no placeholder simply gets no tabs. */

var NAV_BRAND = "Plover Analytics";
// The one dropdown arrow, used on every menu button. A drawn chevron rather
// than the "▾" glyph: at header sizes the glyph read as a speck.
var NAV_CHEVRON =
  '<svg class="chev" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">' +
  '<path d="M2.5 4.25 6 7.75l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.8" ' +
  'stroke-linecap="round" stroke-linejoin="round"/></svg>';
// Simplified Plover Analytics mark for the header. Decorative: the adjacent
// text already names the brand, so it is aria-hidden rather than repeating it
// to a screen reader.
var BRAND_MARK =
  '<svg class="brand-mark" viewBox="0 0 128 128" width="20" height="20" aria-hidden="true" focusable="false">' +
  '<g fill="none" stroke="currentColor" stroke-width="8" stroke-linejoin="round" stroke-linecap="round">' +
  '<circle cx="64" cy="64" r="54"/><path d="M22 86 L46 56 L62 70 L86 44 L106 58"/></g>' +
  '<g fill="currentColor"><circle cx="46" cy="56" r="8"/><circle cx="86" cy="44" r="8"/></g>' +
  '<g fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round" opacity=".55">' +
  '<path d="M30 100 H62"/><path d="M74 100 H98"/></g></svg>';


// Markets, separated at the top of the bar: a reader works in one market at
// a time, and each stands on its own sources (e-Stat, the BOJ and EDINET; the
// C&SD and the HKMA; the SEC and the Treasury). Each section belongs to one
// market or is "shared" (data access, methodology), and the bar shows only
// the current market's sections plus the shared ones. A shared page stays in
// the market the reader came from, remembered per browser.
//
// The Data menu groups markets into regions, one tab each: Asia holds Japan
// and Hong Kong, each still its own block with its own sections, exactly as a
// market was laid out when every market had a tab of its own.
var NAV_MARKETS = [
  { id: "jp", label: "Japan", entry: "macro.html", region: "asia" },
  { id: "hk", label: "Hong Kong", entry: "hk-economy.html", region: "asia" },
  { id: "us", label: "United States", entry: "us-treasury.html", region: "us" },
];
var NAV_REGIONS = [
  { id: "asia", label: "Asia" },
  { id: "us", label: "United States" },
];

var NAV_SECTIONS = [
  {
    id: "macro", market: "jp", label: "Macro", suffix: "Macro",
    pages: [
      { id: "overview", label: "Overview", href: "macro.html" },
      // Inflation is one destination with two views. The headline page and
      // the item table are the same dataset family read at two depths, and
      // as two peers in the strip they read as unrelated products. A page
      // with `tabs` owns them; nav.js renders the third tier in-page.
      { id: "inflation", label: "Inflation", href: "cpi.html",
        tabs: [
          { id: "inflation", label: "Overview", href: "cpi.html" },
          { id: "explorer", label: "Item Explorer", href: "explorer.html" },
          // The other cuts of the same index the Bureau publishes, each a
          // dataset of its own read by the same page script.
          { id: "tokyo", label: "Tokyo Advance", href: "tokyo.html" },
          { id: "goods-services", label: "Goods & Services", href: "goods-services.html" },
          { id: "cpi-sa", label: "Seasonally Adjusted", href: "cpi-sa.html" },
          { id: "cpi-long", label: "Since 1946", href: "cpi-long.html" },
        ] },
      // The national accounts and the corporate survey are the two
      // activity datasets: what the economy produced, and what companies
      // earned and invested doing it. They sit before the balance sheets.
      // Labour sits after Inflation: unemployment, job openings and wages
      // are the other half of what the Bank of Japan reads each month.
      { id: "labour", label: "Labour", href: "labour.html" },
      { id: "gdp", label: "GDP", href: "gdp.html" },
      // Public finance sits beside the national accounts: the general
      // account is one legal account of one tier of government, and the
      // GFS view on the same page is the whole of it.
      { id: "fiscal", label: "Public Finance", href: "fiscal.html" },
      { id: "corporate", label: "Corporate Finance", href: "corporate.html" },
      { id: "boj", label: "Bank of Japan", href: "boj.html" },
      // Banks sit beside the central bank: the FSA's bad-loan and earnings
      // summaries and the JBA's per-bank statements are the private-sector
      // half of the same credit story, and one page reads them together.
      { id: "banks", label: "Banks", href: "banks.html" },
      { id: "rates", label: "Yield Curve", href: "rates.html" },
    ],
  },
  {
    // Trade is customs data, not a macro aggregate: one industry's exports
    // and imports by partner country answer an industry question. Each page
    // is one slice of the same Ministry of Finance table, read by the same
    // script; the strip names the industry, not the dataset.
    id: "trade", market: "jp", label: "Trade", suffix: "Trade",
    pages: [
      { id: "semis", label: "Semiconductors", href: "semis.html" },
      { id: "autos", label: "Motor Vehicles", href: "autos.html" },
      { id: "energy", label: "Energy", href: "energy.html" },
      { id: "machinery", label: "Machinery", href: "machinery.html" },
      { id: "pharma", label: "Pharmaceuticals", href: "pharma.html" },
      { id: "food", label: "Food", href: "food.html" },
    ],
  },
  {
    // Population and the vote-weight page are one subject read two ways:
    // the register counts people, and Vote Weight divides seats by them.
    // Neither is a price, a rate or a balance sheet.
    id: "demographics", market: "jp", label: "Demographics", suffix: "Demographics",
    pages: [
      { id: "population", label: "Population", href: "population.html" },
      { id: "representation", label: "Vote Weight", href: "representation.html" },
    ],
  },
  {
    // Tourism left Macro once it was more than one page. Arrivals, guest
    // nights and the regional detail are three views of one story and were
    // pushing the macro strip past what a phone can show at all.
    id: "tourism", market: "jp", label: "Tourism", suffix: "Tourism",
    pages: [
      { id: "inbound", label: "Arrivals", href: "inbound.html" },
      { id: "accommodation", label: "Guest Nights", href: "accommodation.html" },
      { id: "regions", label: "Regions", href: "lodging-regions.html" },
    ],
  },
  {
    // Agriculture is its own section, not a macro page: rice price, stock,
    // cost of production and farm-input prices are four datasets that are
    // only useful read against each other, and none of them is a macro
    // aggregate.
    id: "agriculture", market: "jp", label: "Agriculture", suffix: "Agriculture",
    pages: [
      { id: "rice", label: "Rice & Farm Prices", href: "rice.html" },
      { id: "ja", label: "Co-operatives", href: "ja.html" },
    ],
  },
  {
    id: "equities", market: "jp", label: "Equities", suffix: "Equities",
    // Thirteen datasets as thirteen peers overran the strip at every width.
    // They are grouped by the question they answer — who holds it, who runs
    // it, what it reports — and each group's pages are its in-page tabs.
    // A group's id is its first tab's, so the strip entry and that page are
    // one destination. Every URL is unchanged.
    // The strip leads with the company page — one company is what a reader
    // arrives wanting — while the navy bar still lands on the Overview, which
    // is the directory of what the section holds.
    entry: "equities.html",
    pages: [
      // One destination, two views of one company: everything we hold on it,
      // and the segment-vs-customs lens that used to be the whole page.
      { id: "company", label: "Company Profile", href: "company.html",
        tabs: [
          { id: "company", label: "Profile", href: "company.html" },
          { id: "customs", label: "Customs Lens", href: "customs-lens.html" },
        ] },
      { id: "overview", label: "Overview", href: "equities.html" },
      { id: "holdings", label: "Ownership", href: "holdings.html",
        tabs: [
          { id: "holdings", label: "Cross-Shareholdings", href: "holdings.html" },
          { id: "ownership", label: "Register", href: "ownership.html" },
          { id: "stakes", label: "5% Filings", href: "stakes.html" },
          { id: "shorts", label: "Short Positions", href: "shorts.html" },
        ] },
      { id: "governance", label: "Governance", href: "governance.html",
        tabs: [
          { id: "governance", label: "Boards & Pay", href: "governance.html" },
          { id: "agm", label: "AGM Votes", href: "agm.html" },
          { id: "buyback", label: "Buybacks", href: "buyback.html" },
          { id: "risks", label: "Business Risks", href: "risks.html" },
        ] },
      { id: "financials", label: "Financials", href: "financials.html",
        tabs: [
          { id: "financials", label: "Statements", href: "financials.html" },
          { id: "earnings", label: "Earnings Releases", href: "earnings.html" },
          { id: "facilities", label: "Facilities & Land", href: "facilities.html" },
          { id: "customers", label: "Customers", href: "customers.html" },
        ] },
      // Market activity: what the whole market is doing, as opposed to what
      // one company filed. Both pages are weekly JPX aggregates.
      { id: "margin", label: "Market", href: "margin.html",
        tabs: [
          { id: "margin", label: "Margin Balances", href: "margin.html" },
          { id: "flows", label: "Investor Flows", href: "flows.html" },
        ] },
      { id: "screener", label: "Screens", href: "screener.html",
        tabs: [
          { id: "screener", label: "Screener", href: "screener.html" },
          { id: "cohorts", label: "Peer Groups", href: "cohorts.html" },
        ] },
    ],
  },
  {
    // Hong Kong's official statistics from the Census and Statistics
    // Department. The economy page switches between GDP, the labour force,
    // retail sales and trade; the prices page between the index levels and
    // the C&SD's published rates.
    id: "hk-macro", market: "hk", label: "Macro", suffix: "HK Macro",
    pages: [
      { id: "hk-economy", label: "Economy", href: "hk-economy.html" },
      { id: "hk-prices", label: "Prices", href: "hk-prices.html" },
    ],
  },
  {
    // US prices and pay, all from the BLS except the Atlanta Fed tracker.
    // Inflation owns its cuts as tabs, as Japan's does; retail prices and
    // wages are separate questions and separate pages.
    id: "us-prices", market: "us", label: "Prices & Wages", suffix: "US Prices",
    pages: [
      { id: "us-inflation", label: "Inflation", href: "us-inflation.html",
        tabs: [
          { id: "us-inflation", label: "Overview", href: "us-inflation.html" },
          { id: "us-explorer", label: "Item Explorer", href: "us-explorer.html" },
          { id: "us-cpi-sa", label: "Seasonally Adjusted", href: "us-cpi-sa.html" },
          { id: "us-cpi-areas", label: "Regions & Metro Areas", href: "us-cpi-areas.html" },
          { id: "us-weights", label: "Basket Weights", href: "us-weights.html" },
        ] },
      { id: "us-prices", label: "Retail Prices", href: "us-prices.html" },
      { id: "us-wages", label: "Wages", href: "us-wages.html" },
    ],
  },
  {
    id: "us-rates", market: "us", label: "Rates", suffix: "US Rates",
    pages: [
      { id: "us-treasury", label: "Treasury Yields", href: "us-treasury.html" },
    ],
  },
  {
    // US companies from their SEC filings. Each page is a list, with one
    // company's record behind ?t=: financials for the large caps held at
    // full filed depth (company-facts, 2009 on), pay for the S&P 500.
    id: "us-companies", market: "us", label: "Companies", suffix: "US Companies",
    pages: [
      { id: "us-companies", label: "Company Financials", href: "us-companies.html" },
      // Pay versus performance from every S&P 500 member's proxy statement.
      { id: "us-pay", label: "Executive Pay", href: "us-pay.html" },
    ],
  },
  {
    // Everything about using the data rather than the data itself — how to
    // connect, the manual, the API reference and the methodology — is one
    // section. As two sections ("Data Access", "Methodology") they took a
    // sixth of the bar and pushed it off the edge once the market switch and
    // a signed-in account were added. Every URL is unchanged, and every page
    // footer still links the methodology directly.
    id: "connect", market: "shared", label: "Docs", suffix: "Docs",
    pages: [
      { id: "connect", label: "Setup", href: "connect.html" },
      { id: "manual", label: "Manual", href: "manual.html" },
      { id: "api", label: "API Reference", href: "api.html" },
      { id: "methodology", label: "Methodology", href: "methodology.html" },
    ],
  },
];

(function renderSiteNav() {
  var header = document.querySelector("header.site-header");
  if (!header) return;

  var sectionId = header.getAttribute("data-section") || "";
  var pageId = header.getAttribute("data-page") || "";
  var section = null;
  for (var i = 0; i < NAV_SECTIONS.length; i++) {
    if (NAV_SECTIONS[i].id === sectionId) section = NAV_SECTIONS[i];
  }

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // The brand always returns to the landing page. It carries no section
  // suffix any more: the market tab, the strip and the trail at the top of
  // the page each name where the reader is, and a screenshot keeps all three.
  //
  // The mark is inlined rather than an <img> on purpose: an SVG loaded through
  // <img> is an isolated document, so `currentColor` resolves to black there
  // and the ring disappears against the navy header. Inline, it inherits
  // --obs-header-ink and is correct in both themes for free. It is also the
  // simplified line-art mark, not the full-colour disc: at 20px the detailed
  // version is an indistinct blob, and the header is the one place the mark is
  // always small. assets/favicon.svg is the same reasoning applied to the tab
  // icon; assets/logo.svg keeps the full-colour disc for large uses.
  var brand = '<a class="brand" href="index.html">' + BRAND_MARK + esc(NAV_BRAND) + "</a>";

  // Which market the reader is in. A market page says so itself; a shared
  // page (Docs) and the landing page keep the market last visited.
  var isDocs = !!section && section.market === "shared";
  var market = section && !isDocs ? section.market : null;
  try {
    if (market) localStorage.setItem("obs-market", market);
    else market = localStorage.getItem("obs-market");
  } catch (e) { /* storage blocked: fall back to Japan below */ }
  var marketObj = NAV_MARKETS[0];
  for (var mi = 0; mi < NAV_MARKETS.length; mi++) {
    if (NAV_MARKETS[mi].id === market) marketObj = NAV_MARKETS[mi];
  }
  market = marketObj.id;

  // First tier: the two products, Data and Research. Data opens the
  // markets; the country the reader is in is named in the trail at the top
  // of the content and marked in the Data menu.
  var marketLinks = NAV_MARKETS.map(function (m) {
    return '<a href="' + esc(m.entry) + '"' +
      (!isDocs && m.id === market ? ' aria-current="page"' : "") + ">" +
      esc(m.label) + "</a>";
  }).join("");
  var onData = !!section && !isDocs;
  // PloverResearch spans every market. Absolute: its pages live under /research/.
  var onResearch = pageId === "research";
  var primary = '<nav class="site-nav" aria-label="Main">' +
    '<span class="nav-dd nav-dd-mega">' +
    '<button type="button" class="nav-dd-btn" aria-haspopup="true" aria-expanded="false"' +
      ' aria-controls="nav-data-menu"' + (onData ? ' aria-current="true"' : "") + ">Data" +
      NAV_CHEVRON + "</button>" +
    '<span class="nav-dd-menu mega" id="nav-data-menu" hidden></span></span>' +
    '<a href="/research"' + (onResearch ? ' aria-current="page"' : "") + ">Research</a>" +
    "</nav>";

  var docs = NAV_SECTIONS.filter(function (s) { return s.market === "shared"; })[0];
  header.innerHTML =
    '<div class="inner">' + brand + primary +
    // Phones: the whole navigation, sections included, folds into this menu.
    '<button class="nav-burger" type="button" aria-label="Menu" aria-expanded="false" ' +
      'aria-controls="mnav"><svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" ' +
      'focusable="false"><path d="M3 6h18M3 12h18M3 18h18" fill="none" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round"/></svg></button>' +
    '<div class="header-right">' +
    // Docs: a short menu of its pages, since there is no strip to hold them.
    '<span class="nav-dd nav-dd-docs">' +
    '<button type="button" class="nav-dd-btn header-link" aria-haspopup="true" aria-expanded="false"' +
      ' aria-controls="nav-docs-menu"' + (isDocs ? ' aria-current="true"' : "") + ">Docs" +
      NAV_CHEVRON + "</button>" +
    '<span class="nav-dd-menu" id="nav-docs-menu" hidden>' +
    docs.pages.map(function (p) {
      return '<a href="' + esc(p.href) + '"' + (p.id === pageId ? ' aria-current="page"' : "") +
        ">" + esc(p.label) + "</a>";
    }).join("") + "</span></span>" +
    '<span class="header-asof" id="header-asof"></span>' +
    '<span class="header-account" id="header-account"></span>' +
    '<button class="theme-toggle" type="button">Dark Mode</button>' +
    "</div></div>" +
    '<div class="mnav" id="mnav"></div>';

  // Data and Docs menus (wide screens). One open at a time.
  (function dropdowns() {
    var dds = Array.prototype.slice.call(header.querySelectorAll(".nav-dd"));
    function set(dd, open) {
      dd.querySelector(".nav-dd-menu").hidden = !open;
      dd.querySelector(".nav-dd-btn").setAttribute("aria-expanded", open ? "true" : "false");
    }
    dds.forEach(function (dd) {
      dd.querySelector(".nav-dd-btn").addEventListener("click", function (e) {
        e.stopPropagation();
        var open = dd.querySelector(".nav-dd-menu").hidden;
        dds.forEach(function (o) { set(o, false); });
        set(dd, open);
      });
    });
    document.addEventListener("click", function (e) {
      dds.forEach(function (dd) {
        if (!dd.querySelector(".nav-dd-menu").contains(e.target)) set(dd, false);
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      dds.forEach(function (dd) {
        if (!dd.querySelector(".nav-dd-menu").hidden) {
          set(dd, false);
          dd.querySelector(".nav-dd-btn").focus();
        }
      });
    });
  })();

  // Hamburger (phones). The menu's contents are filled in below, once the
  // market's sections are known.
  (function burger() {
    var btn = header.querySelector(".nav-burger");
    var theme = header.querySelector(".header-right .theme-toggle");
    function set(open) {
      header.classList.toggle("nav-open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      // The page's theme script binds the bar's toggle; the menu's copy
      // relays to it and borrows its label.
      var mt = header.querySelector(".mnav-theme");
      if (open && mt && theme) mt.textContent = theme.textContent;
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      set(!header.classList.contains("nav-open"));
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
    document.addEventListener("click", function (e) {
      if (!header.contains(e.target)) set(false);
    });
    header.addEventListener("click", function (e) {
      if (e.target.closest(".mnav-theme") && theme) {
        theme.click();
        e.target.closest(".mnav-theme").textContent = theme.textContent;
        return;
      }
      var sec = e.target.closest(".mnav-sec");
      if (sec) {
        var open = sec.getAttribute("aria-expanded") !== "true";
        sec.setAttribute("aria-expanded", open ? "true" : "false");
        sec.nextElementSibling.hidden = !open;
      }
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 768) set(false);
    });
  })();

  // The account control asks the server who is signed in, and renders nothing
  // at all where accounts are not switched on — a "Sign in" link to a feature
  // that does not exist is worse than no link. The slot reserves its width, so
  // the bar cannot jump when the answer arrives.
  (function accountSlot() {
    var slot = document.getElementById("header-account");
    if (!slot || !window.fetch) return;
    fetch("/api/v1/account/me", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 404) return null;               // feature off
        // on, nobody signed in; while sign-in is invite-only the public
        // header offers nothing, except the research pages' way in for
        // invited writers (the Writer Desk emails them a sign-in link)
        if (r.status === 401) return r.json().catch(function () { return {}; })
          .then(function (b) {
            if (!(b && b.invite_only)) return { email: null };
            return onResearch ? { email: null, writers: true } : null;
          });
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (me) {
        if (!me) return;
        var here = location.pathname.replace(/^\//, "") + location.search;
        var signIn = me.writers
          ? '<a href="write.html">Sign in</a>'
          : '<a href="signin.html?returnTo=' + encodeURIComponent(here) + '">Sign in</a>';
        var desk = onResearch ? '<a href="write.html">Writer Desk</a>' : "";
        var mslot = document.getElementById("mnav-account");
        if (mslot) {
          mslot.innerHTML = me.email ? desk + '<a href="#" data-do="signout">Sign out</a>' : signIn;
          mslot.addEventListener("click", function (e) {
            if (!e.target.closest("[data-do='signout']")) return;
            e.preventDefault();
            fetch("/api/v1/account/signout", { method: "POST" })
              .then(function () { location.reload(); })
              .catch(function () { location.reload(); });
          });
        }
        if (!me.email) {
          slot.innerHTML = signIn;
          return;
        }
        // A fixed-width "Account" control, not the address: an email is as
        // long as its owner made it, and the bar has no room to give. The
        // address is the first line of the menu, and the button's title.
        slot.innerHTML = '<span class="acct-menu">' +
          '<button type="button" aria-expanded="false" title="Signed in as ' +
          esc(me.email) + '">Account' + NAV_CHEVRON + "</button>" +
          '<span class="acct-pop" hidden>' +
          '<span class="acct-who">' + esc(me.email) + "</span>" + desk +
          '<a href="#" data-do="signout">Sign out</a></span></span>';
        var btn = slot.querySelector("button");
        var pop = slot.querySelector(".acct-pop");
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var open = pop.hidden;
          pop.hidden = !open;
          btn.setAttribute("aria-expanded", String(open));
        });
        document.addEventListener("click", function (e) {
          if (!pop.hidden && !slot.contains(e.target)) {
            pop.hidden = true;
            btn.setAttribute("aria-expanded", "false");
          }
        });
        pop.addEventListener("click", function (e) {
          var item = e.target.closest("[data-do='signout']");
          if (!item) return;
          e.preventDefault();
          // A reload rather than a redirect: whichever page the reader is on
          // is still readable signed out, so they stay where they were.
          fetch("/api/v1/account/signout", { method: "POST" })
            .then(function () { location.reload(); })
            .catch(function () { location.reload(); });
        });
      })
      .catch(function () { /* the header is not the place to report this */ });
  })();

  // A page owning tabs stays marked current while any of its tabs is open —
  // otherwise opening the Item Explorer would un-highlight Inflation and the
  // strip would show no current page at all.
  function owns(p) {
    if (p.id === pageId) return true;
    for (var t = 0; p.tabs && t < p.tabs.length; t++) {
      if (p.tabs[t].id === pageId) return true;
    }
    return false;
  }

  // Second tier: every market's sections and their pages, in the Data
  // menu. There is no strip under the bar: the whole data catalogue is one
  // click away from anywhere, and the page keeps the screen.
  var stripSections = isDocs ? [] : NAV_SECTIONS.filter(function (s) {
    return s.market === market;
  });
  // The Data menu: one region at a time behind its tab, each market in it a
  // block of its sections in columns, and a preview pane on the right that
  // shows the page under the pointer — its main series as a small chart with
  // the latest value — from /api/v1/catalog/previews, fetched once on first
  // open.
  var dataMenu = header.querySelector("#nav-data-menu");
  var region = marketObj.region;
  function marketsIn(r) {
    return NAV_MARKETS.filter(function (m) { return m.region === r; });
  }
  dataMenu.innerHTML =
    '<span class="mega-main">' +
    '<span class="mega-tabs" role="tablist" aria-label="Region">' +
    NAV_REGIONS.map(function (r) {
      return '<button type="button" role="tab" class="mega-tab" data-region="' + esc(r.id) + '"' +
        ' aria-selected="' + (r.id === region) + '">' + esc(r.label) + "</button>";
    }).join("") + "</span>" +
    NAV_REGIONS.map(function (r) {
      return '<span class="mega-region" data-region="' + esc(r.id) + '"' +
        (r.id === region ? "" : " hidden") + ">" + marketsIn(r.id).map(marketBlock).join("") +
        "</span>";
    }).join("") +
    "</span>" +
    '<span class="mega-preview" aria-live="polite"></span>';

  function marketBlock(m) {
    return '<span class="mega-mkt mega-' + esc(m.id) + '" data-mkt="' + esc(m.id) + '">' +
      '<a class="mega-mkt-h" href="' + esc(m.entry) + '"' +
        (!isDocs && m.id === market ? ' aria-current="true"' : "") + ">" +
        esc(m.label) + " overview</a>" +
      '<span class="mega-secs">' +
      NAV_SECTIONS.filter(function (s) { return s.market === m.id; }).map(function (s) {
        return '<span class="mega-sec">' +
          '<a class="mega-sec-h" href="' + esc(s.entry || s.pages[0].href) + '"' +
            ' data-sec="' + esc(s.label) + '" data-label="' + esc(s.label) + '"' +
            (s === section ? ' aria-current="true"' : "") + ">" + esc(s.label) + "</a>" +
          (s.pages.length > 1 ? s.pages.map(function (p) {
            return '<a href="' + esc(p.href) + '" data-sec="' + esc(s.label) + '" data-label="' +
              esc(p.label) + '"' + (owns(p) ? ' aria-current="page"' : "") + ">" +
              esc(p.label) + "</a>";
          }).join("") : "") + "</span>";
      }).join("") + "</span></span>";
  }

  (function megaPreview() {
    var pane = dataMenu.querySelector(".mega-preview");
    var btn = header.querySelector(".nav-dd-mega .nav-dd-btn");
    var previews = null;
    var loading = null;
    var shown = null;
    var touchArmed = null;
    var lastPointer = "mouse";
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DEFAULT = { jp: "/cpi.html", hk: "/hk-economy.html", us: "/us-inflation.html" };

    function pathOf(href) {
      try { return new URL(href, location.origin + "/").pathname; } catch (e) { return href; }
    }
    function load() {
      if (previews || loading) return loading;
      loading = fetch("/api/v1/catalog/previews", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : { pages: {} }; })
        .then(function (d) { previews = d.pages || {}; })
        .catch(function () { previews = {}; });
      return loading;
    }
    function period(iso, freq) {
      if (!iso) return "";
      var y = iso.slice(0, 4), m = Number(iso.slice(5, 7)), d = Number(iso.slice(8, 10));
      if (freq === "daily") return d + " " + MONTHS[m - 1] + " " + y;
      if (freq === "quarterly") return "Q" + Math.ceil(m / 3) + " " + y;
      if (freq === "annual" || freq === "fiscal_year") return y;
      return MONTHS[m - 1] + " " + y;
    }
    // As published: every decimal kept, thousands grouped, a true minus.
    function num(v, dp) {
      var t = dp === undefined ? String(Math.abs(v)) : Math.abs(v).toFixed(dp);
      var parts = t.split(".");
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      return (v < 0 ? "−" : "") + parts.join(".");
    }
    function spark(points) {
      var W = 260, H = 76, P = 3;
      var vals = points.map(function (p) { return p[1]; }).filter(function (v) { return v !== null; });
      if (vals.length < 3) return "";
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      if (hi === lo) { hi += 1; lo -= 1; }
      var n = points.length;
      function x(i) { return P + (W - 2 * P) * (n === 1 ? 0 : i / (n - 1)); }
      function y(v) { return P + (H - 2 * P) * (1 - (v - lo) / (hi - lo)); }
      var d = "", pen = false, last = null;
      points.forEach(function (p, i) {
        if (p[1] === null) { pen = false; return; }   // a gap stays a gap
        d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(p[1]).toFixed(1);
        pen = true;
        last = [x(i), y(p[1])];
      });
      var zero = lo < 0 && hi > 0
        ? '<line class="mp-zero" x1="0" x2="' + W + '" y1="' + y(0).toFixed(1) + '" y2="' + y(0).toFixed(1) + '"/>' : "";
      return '<svg class="mp-spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" ' +
        'aria-hidden="true" focusable="false">' + zero + '<path d="' + d + '"/>' +
        (last ? '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="2.6"/>' : "") +
        "</svg>";
    }
    function render(link) {
      var path = pathOf(link.getAttribute("href"));
      var pv = previews && previews[path];
      var label = link.getAttribute("data-label") || link.textContent;
      var sec = link.getAttribute("data-sec") || "";
      var head = '<span class="mp-sec">' + esc(sec !== label ? sec : "") + "</span>" +
        '<span class="mp-title">' + esc(label) + "</span>";
      var body = "";
      if (pv && pv.kind === "series") {
        var yoy = pv.measure === "yoy";
        var pct = yoy || pv.unit === "%";
        var big = num(pv.latest.value, yoy ? 1 : undefined) + (pct ? "%" : "");
        var unit = yoy ? "on a year earlier" : (pct ? "" : pv.unit || "");
        body = '<span class="mp-figure"><span class="mp-value">' + esc(big) + "</span>" +
          '<span class="mp-unit">' + esc((unit ? unit + " · " : "") + period(pv.latest.period, pv.frequency)) +
          "</span></span>" +
          // a figure the page calculates (a world total, a net flow) says so
          '<span class="mp-series">' + esc(pv.name) +
            (pv.trust === "derived" && !yoy ? " \u00b7 calculated" : "") + "</span>" +
          spark(pv.points) +
          (pv.points.length > 2 ? '<span class="mp-axis"><span>' + esc(period(pv.points[0][0], pv.frequency)) +
            "</span><span>" + esc(period(pv.points[pv.points.length - 1][0], pv.frequency)) + "</span></span>" : "") +
          '<span class="mp-src">' + esc(pv.credit || "") + "</span>";
      } else if (pv && pv.kind === "text") {
        body = '<span class="mp-text">' + esc(pv.text) + "</span>" +
          (pv.through ? '<span class="mp-through">Filings read through ' +
            esc(period(pv.through, "daily")) + "</span>" : "");
      } else if (!previews) {
        body = '<span class="mp-text mp-wait">Loading…</span>';
      } else {
        body = '<span class="mp-text">Open the page to see its data.</span>';
      }
      pane.innerHTML = head + body +
        '<a class="mp-open" href="' + esc(link.getAttribute("href")) + '">Open ' + esc(label) +
        '<span aria-hidden="true"> →</span></a>';
      shown = link;
    }
    function show(link) {
      if (!link || link === shown) return;
      render(link);
      if (!previews) load().then(function () { if (shown === link) render(link); });
    }
    function defaultFor(mkt) {
      var cur = dataMenu.querySelector('.mega-mkt[data-mkt="' + mkt + '"] a[aria-current="page"]');
      // a page link before a section heading that points at the same page
      return cur || dataMenu.querySelector('.mega-mkt[data-mkt="' + mkt + '"] a:not(.mega-sec-h)[href$="' +
        DEFAULT[mkt].slice(1) + '"]') || dataMenu.querySelector('.mega-mkt[data-mkt="' + mkt + '"] a[href$="' +
        DEFAULT[mkt].slice(1) + '"]') || dataMenu.querySelector('.mega-mkt[data-mkt="' + mkt + '"] .mega-sec a');
    }
    function selectRegion(r) {
      Array.prototype.forEach.call(dataMenu.querySelectorAll(".mega-tab"), function (t) {
        t.setAttribute("aria-selected", String(t.getAttribute("data-region") === r));
      });
      Array.prototype.forEach.call(dataMenu.querySelectorAll(".mega-region"), function (b) {
        b.hidden = b.getAttribute("data-region") !== r;
      });
      shown = null;
      // the market the reader is in, if it is in this region; else its first
      var mkts = marketsIn(r);
      var mkt = mkts.some(function (m) { return m.id === market; }) ? market : mkts[0].id;
      show(defaultFor(mkt));
    }
    if (btn) btn.addEventListener("click", function () {
      load();
      if (!shown) show(defaultFor(market));
    });
    dataMenu.addEventListener("click", function (e) {
      var tab = e.target.closest(".mega-tab");
      if (tab) { e.stopPropagation(); selectRegion(tab.getAttribute("data-region")); return; }
      // touch: the first tap on a page previews it, the second opens it
      var a = e.target.closest(".mega-secs a");
      if (a && lastPointer === "touch" && touchArmed !== a) {
        e.preventDefault();
        touchArmed = a;
        show(a);
      }
    });
    dataMenu.addEventListener("pointerdown", function (e) { lastPointer = e.pointerType || "mouse"; });
    dataMenu.addEventListener("mouseover", function (e) {
      var a = e.target.closest(".mega-secs a");
      if (a && lastPointer !== "touch") show(a);
    });
    dataMenu.addEventListener("focusin", function (e) {
      var a = e.target.closest(".mega-secs a");
      if (a) show(a);
    });
    dataMenu.addEventListener("keydown", function (e) {
      var tab = e.target.closest(".mega-tab");
      if (!tab || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
      var tabs = Array.prototype.slice.call(dataMenu.querySelectorAll(".mega-tab"));
      var next = tabs[(tabs.indexOf(tab) + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      selectRegion(next.getAttribute("data-region"));
    });
  })();

  // The phone menu: Data, the sections of the market in view (each opening
  // to its pages, the current one already open), then Research and Docs.
  function pageLinks(s) {
    return s.pages.map(function (p) {
      var views = (p.tabs || []).filter(function (t) { return t.href !== p.href; });
      return '<a href="' + esc(p.href) + '"' + (owns(p) ? ' aria-current="page"' : "") + ">" +
        esc(p.label) + "</a>" +
        views.map(function (t) {
          return '<a class="view" href="' + esc(t.href) + '"' +
            (t.id === pageId ? ' aria-current="page"' : "") + ">" + esc(t.label) + "</a>";
        }).join("");
    }).join("");
  }
  var mSections = isDocs
    ? pageLinks(section)
    : stripSections.map(function (s) {
        var current = s === section;
        if (s.pages.length === 1) {
          return '<a href="' + esc(s.pages[0].href) + '"' +
            (current ? ' aria-current="page"' : "") + ">" + esc(s.label) + "</a>";
        }
        return '<button type="button" class="mnav-sec" aria-expanded="' + current + '">' +
          esc(s.label) + NAV_CHEVRON + "</button>" +
          '<div class="mnav-pages"' + (current ? "" : " hidden") + ">" + pageLinks(s) + "</div>";
      }).join("");
  header.querySelector(".mnav").innerHTML =
    '<div class="mnav-group"><div class="mnav-label">Data</div>' + marketLinks + "</div>" +
    '<div class="mnav-group"><div class="mnav-label">' +
      esc(isDocs ? "Docs" : marketObj.label) + "</div>" + mSections + "</div>" +
    '<div class="mnav-group">' +
      '<a href="/research"' + (onResearch ? ' aria-current="page"' : "") + ">Research</a>" +
      (isDocs ? "" : '<a href="connect.html">Docs</a>') +
      '<span id="mnav-account"></span>' +
      '<button type="button" class="mnav-theme">Dark Mode</button></div>';

  // The trail at the top of the content: market, section, page. It is what
  // the brand suffix used to do — name the place in words a screenshot keeps
  // — with the country first, since that is the distinction readers lose.
  if (section) {
    var pageLabel = "";
    section.pages.forEach(function (p) {
      if (p.id === pageId) pageLabel = p.label;
      (p.tabs || []).forEach(function (t) {
        if (t.id === pageId && t.href !== p.href) pageLabel = p.label + " \u00b7 " + t.label;
      });
    });
    var trail = isDocs
      ? [["Docs", "connect.html"]]
      : [[marketObj.label, marketObj.entry],
         [section.label, section.entry || section.pages[0].href]];
    if (pageLabel && pageLabel !== section.label) trail.push([pageLabel, null]);
    document.addEventListener("DOMContentLoaded", function () {
      var main = document.querySelector("main");
      if (!main || document.body.classList.contains("page-home")) return;
      var el = document.createElement("nav");
      el.className = "crumbs";
      el.setAttribute("aria-label", "You are here");
      el.innerHTML = trail.map(function (c) {
        return c[1] ? '<a href="' + esc(c[1]) + '">' + esc(c[0]) + "</a>"
                    : '<span aria-current="page">' + esc(c[0]) + "</span>";
      }).join('<span class="sep" aria-hidden="true">/</span>');
      main.insertAdjacentElement("afterbegin", el);
    });
  }

  // Third tier: the views of one destination, in the content column.
  var group = null;
  for (var j = 0; section && j < section.pages.length; j++) {
    if (section.pages[j].tabs && owns(section.pages[j])) group = section.pages[j];
  }
  if (!group) return;
  document.addEventListener("DOMContentLoaded", function () {
    var slot = document.querySelector("[data-page-tabs]");
    if (!slot) return;
    slot.setAttribute("role", "navigation");
    slot.setAttribute("aria-label", group.label);
    slot.innerHTML = group.tabs.map(function (t) {
      return '<a href="' + esc(t.href) + '"' +
        (t.id === pageId ? ' aria-current="page"' : "") + ">" +
        esc(t.label) + "</a>";
    }).join("");
  });
})();

/* Section picker: one slim bar under the page head (or the tab strip, where
   there is one) naming the section in view — "Section 03 What the server
   carries ▾  3 of 9" — that opens a numbered list of every section. It is
   generated from the page's own h2 headings, so no page maintains a list,
   and it is one line at every width, so it stays pinned on a phone too.
   Pages with fewer than two sections get nothing; the home page, whose bands
   are promotional rather than navigational, opts out with body.page-home. */
(function renderContents() {
  var toc = null, btn = null, menu = null, heads = [], links = [], sig = "", curIdx = -1;
  var wired = false;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function label(h) {
    var c = h.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll(".h2-note"), function (n) { n.remove(); });
    return c.textContent.replace(/\s+/g, " ").trim();
  }
  function num(i) { return (i < 9 ? "0" : "") + (i + 1); }

  function setOpen(open, focusCurrent) {
    if (!menu) return;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open && focusCurrent) (links[curIdx] || links[0]).focus();
  }

  function build() {
    // landing pages that are an index, not a document, opt out with no-toc
    if (document.body.classList.contains("page-home") ||
        document.body.classList.contains("no-toc")) return;
    var main = document.querySelector("main");
    if (!main) return;
    heads = Array.prototype.filter.call(main.querySelectorAll("h2"), function (h) {
      return h.offsetParent !== null && h.textContent.trim() && !(toc && toc.contains(h));
    });
    if (heads.length < 2) {
      if (toc) { toc.remove(); toc = null; sig = ""; }
      return;
    }

    var used = {};
    heads.forEach(function (h) {
      if (!h.id) {
        var base = "s-" + label(h).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        var id = base, k = 2;
        while (used[id] || document.getElementById(id)) id = base + "-" + (k++);
        h.id = id;
      }
      used[h.id] = true;
    });

    if (!toc) {
      toc = document.createElement("nav");
      toc.className = "page-toc";
      toc.setAttribute("aria-label", "Sections on this page");
      toc.innerHTML =
        '<span class="page-toc-label" id="page-toc-label">Section</span>' +
        '<span class="page-toc-pick">' +
        '<button type="button" class="page-toc-btn" aria-haspopup="true" aria-expanded="false"' +
        ' aria-controls="page-toc-menu" aria-describedby="page-toc-label"></button>' +
        '<div class="page-toc-menu" id="page-toc-menu" hidden></div></span>' +
        '<span class="page-toc-count"></span>';
      var after = main.querySelector(".page-tabs") || main.querySelector(".page-head") ||
        main.querySelector("#stale-banner");
      if (after) after.insertAdjacentElement("afterend", toc);
      else main.insertAdjacentElement("afterbegin", toc);
      btn = toc.querySelector(".page-toc-btn");
      menu = toc.querySelector(".page-toc-menu");

      btn.addEventListener("click", function () { setOpen(menu.hidden, false); });
      btn.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true, true); }
      });
      menu.addEventListener("click", function (e) {
        if (e.target.closest("a")) setOpen(false);
      });
      menu.addEventListener("keydown", function (e) {
        var i = links.indexOf(document.activeElement);
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          var n = links.length;
          links[((i < 0 ? curIdx : i) + (e.key === "ArrowDown" ? 1 : n - 1)) % n].focus();
        } else if (e.key === "Home") { e.preventDefault(); links[0].focus(); }
        else if (e.key === "End") { e.preventDefault(); links[links.length - 1].focus(); }
      });
      if (!wired) {
        wired = true;
        document.addEventListener("click", function (e) {
          if (toc && !menu.hidden && !toc.contains(e.target)) setOpen(false);
        });
        document.addEventListener("keydown", function (e) {
          if (e.key === "Escape" && toc && !menu.hidden) { setOpen(false); btn.focus(); }
        });
      }
    }

    // Rebuild the list only when the sections change, so an open menu is not
    // swapped out from under the reader by a late-arriving chart.
    var next = heads.map(function (h) { return h.id + "\u0000" + label(h); }).join("\u0001");
    if (next !== sig) {
      sig = next;
      menu.innerHTML = heads.map(function (h, i) {
        return '<a href="#' + esc(h.id) + '"><span class="n">' + num(i) + "</span>" +
          '<span class="t">' + esc(label(h)) + "</span></a>";
      }).join("");
      links = Array.prototype.slice.call(menu.querySelectorAll("a"));
      curIdx = -2;  // force the bar to repaint
    }
    mark();
  }

  // The section in view is the last heading that has reached the bar's
  // bottom edge. Above the first heading, the bar invites a jump instead.
  function mark() {
    if (!toc) return;
    var line = toc.getBoundingClientRect().bottom + 12, idx = -1;
    heads.forEach(function (h, i) { if (h.getBoundingClientRect().top <= line) idx = i; });
    if (idx === curIdx) return;
    curIdx = idx;
    links.forEach(function (a, i) {
      if (i === idx) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    btn.innerHTML = idx < 0
      ? '<span class="t">Jump to a section</span><span class="caret" aria-hidden="true">▾</span>'
      : '<span class="n">' + num(idx) + '</span><span class="t">' + esc(label(heads[idx])) +
        '</span><span class="caret" aria-hidden="true">▾</span>';
    toc.querySelector(".page-toc-count").textContent = idx < 0
      ? heads.length + " sections"
      : (idx + 1) + " of " + heads.length;
  }
  var ticking = false;
  window.addEventListener("scroll", function () {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () { mark(); ticking = false; });
  }, { passive: true });

  // Sections that only appear once data arrives are picked up by the
  // later passes; a page whose sections are all in the markup is unchanged.
  // The bar sticks under whatever is already stuck (header, and the
  // sub-nav where it is sticky), and a jump to a section lands below all of
  // it. Both are measured, not assumed: the header wraps on a phone and the
  // sub-nav only sticks on wider screens.
  function setStack() {
    var header = document.querySelector(".site-header");
    var sub = document.querySelector(".site-subnav");
    var stack = header ? header.offsetHeight : 0;
    if (sub && getComputedStyle(sub).position === "sticky") stack += sub.offsetHeight;
    var root = document.documentElement;
    root.style.setProperty("--obs-stack", stack + "px");
    var tocH = toc && getComputedStyle(toc).position === "sticky" ? toc.offsetHeight : 0;
    root.style.scrollPaddingTop = (stack + tocH + 8) + "px";
  }
  window.addEventListener("resize", setStack);

  document.addEventListener("DOMContentLoaded", function () {
    build();
    setStack();
    // Per-company views appear when a company is chosen; the bar follows.
    // Mutations inside the bar itself are its own rebuilds and are ignored.
    if (!window.MutationObserver) return;
    var main = document.querySelector("main"), timer = null;
    if (!main) return;
    new MutationObserver(function (records) {
      var outside = records.some(function (r) { return !(toc && toc.contains(r.target)); });
      if (!outside) return;
      clearTimeout(timer);
      timer = setTimeout(function () { build(); setStack(); }, 400);
    }).observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "style"] });
  });
  window.addEventListener("load", function () {
    build(); setStack();
    // A deep link scrolled before the stack was measured; land it again.
    if (location.hash) {
      var target = document.getElementById(location.hash.slice(1));
      if (target) target.scrollIntoView();
    }
    setTimeout(function () { build(); setStack(); }, 2500);
  });
})();

/* ---------- section notes: two lines, then a toggle ----------

   Every section carries a paragraph explaining what its numbers are. They
   average four or five lines, and stacked above a chart they push the thing
   the reader came for below the fold. The first two lines carry the
   definition; the rest are caveats — read once, then in the way.

   So the note is folded to two lines with a "More" toggle beside it. The
   text is never removed: it stays in the DOM, so find-on-page, a crawler,
   and the prerendered answer pages all see the whole paragraph. A note that
   already fits gets no toggle, and the fit is measured again on resize,
   because a note that fits on a wide screen will not on a phone. */
(function () {
  var COLLAPSED_LINES = 2;

  function wrap(p) {
    if (p.querySelector(".section-sub-text")) return p.querySelector(".section-sub-text");
    var span = document.createElement("span");
    span.className = "section-sub-text";
    while (p.firstChild) span.appendChild(p.firstChild);
    p.appendChild(span);
    return span;
  }

  function fits(p, text) {
    // Measured while clamped: a clamped box that is not overflowing is short
    // enough to show whole. Nothing to measure on a hidden section.
    if (!p.offsetParent && p.offsetHeight === 0) return null;
    var had = p.classList.contains("is-clamped");
    p.classList.add("is-clamped");
    var over = text.scrollHeight - text.clientHeight > 1;
    if (!had) p.classList.remove("is-clamped");
    return !over;
  }

  function apply(p) {
    var text = wrap(p);
    var short = fits(p, text);
    if (short === null) {
      // Laid out in a hidden section; measure it when it is on screen.
      p.classList.add("section-sub-unmeasured");
      return;
    }
    p.classList.remove("section-sub-unmeasured");
    var btn = p.querySelector(".section-sub-more");
    if (short) {
      p.classList.remove("is-clamped");
      if (btn) btn.remove();
      return;
    }
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "section-sub-more";
      btn.addEventListener("click", function () {
        var folded = p.classList.toggle("is-clamped");
        btn.textContent = folded ? "More" : "Less";
        btn.setAttribute("aria-expanded", folded ? "false" : "true");
      });
      p.appendChild(btn);
      // Opened by hand, a note stays open until its section is redrawn.
      p.classList.add("is-clamped");
      btn.textContent = "More";
      btn.setAttribute("aria-expanded", "false");
    }
  }

  // Measuring costs a layout read, so a note that still has its wrapper is
  // left alone unless the window changed width. Without that guard the sweep
  // and the contents strip above take turns mutating the page, and a page
  // that never stops mutating never gets swept at all.
  var busy = false;
  function sweep(remeasure) {
    busy = true;
    var notes = document.querySelectorAll("p.section-sub");
    Array.prototype.forEach.call(notes, function (p) {
      if (remeasure || !p.querySelector(".section-sub-text") ||
          p.classList.contains("section-sub-unmeasured")) apply(p);
    });
    // Our own wrapping and toggling mutate the page too; the records they
    // cause reach the observer before this clears the flag.
    setTimeout(function () { busy = false; }, 0);
  }

  document.addEventListener("DOMContentLoaded", function () { sweep(true); });
  window.addEventListener("load", function () {
    sweep(true);
    setTimeout(function () { sweep(true); }, 2500);
  });

  var timer = null;
  window.addEventListener("resize", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { sweep(true); }, 200);
  });

  // Sections that only appear once data arrives are picked up here, and so is
  // a note a page rewrites itself — the population history note changes with
  // the view, which drops our wrapper on the floor.
  document.addEventListener("DOMContentLoaded", function () {
    if (!window.MutationObserver) return;
    var main = document.querySelector("main");
    if (!main) return;
    var pending = null;
    new MutationObserver(function () {
      if (busy || pending) return;             // a fixed window, never pushed back
      pending = setTimeout(function () { pending = null; sweep(false); }, 400);
    }).observe(main, { childList: true, subtree: true });
  });
})();
