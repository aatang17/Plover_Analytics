/* Investment Assistant — clickable prototype.
   Every figure comes from DATA (a snapshot of the local Plover Analytics API,
   23 Sep 2026). Nothing here talks to a model: specialist replies are computed
   from the snapshot so the demo works offline and every number is real. */
(function () {
  'use strict';
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const CO = DATA.companies;
  const TODAY = '2026-09-23';
  const STORE = 'ia-proto-v4';

  /* ------------------------------------------------------------ helpers */
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MINUS = '−';
  const DASH = '—';
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function yen(v, d) {
    if (v == null) return DASH;
    const neg = v < 0; v = Math.abs(v);
    let s;
    if (v >= 1e12) s = '¥' + (v / 1e12).toFixed(d == null ? 2 : d) + 'tn';
    else if (v >= 1e8) s = '¥' + (v / 1e9).toFixed(d == null ? 1 : d) + 'bn';
    else if (v >= 1e6) s = '¥' + (v / 1e6).toFixed(0) + 'm';
    else s = '¥' + Math.round(v).toLocaleString('en');
    return (neg ? MINUS : '') + s;
  }
  const pct = (v, d) => v == null ? DASH : (v < 0 ? MINUS : '') + Math.abs(v).toFixed(d == null ? 1 : d) + '%';
  const pp = v => v == null ? DASH : (v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(v).toFixed(1) + ' pp';
  const n = v => v == null ? DASH : Math.round(v).toLocaleString('en');
  const signed = v => v == null ? DASH : v === 0 ? '0' : (v > 0 ? '+' : MINUS) + Math.abs(Math.round(v)).toLocaleString('en');
  function dmy(iso) { if (!iso) return DASH; const d = new Date(iso + 'T00:00:00'); return d.getDate() + ' ' + MON[d.getMonth()] + (d.getFullYear() !== 2026 ? ' ' + d.getFullYear() : ''); }
  const monthName = iso => { const d = new Date(iso + 'T00:00:00'); return MONTH[d.getMonth()] + ' ' + d.getFullYear(); };
  function when(ts) { const [d, t] = ts.split(' '); return d === TODAY ? (t || 'Today') : dmy(d); }
  function nowStamp() { const d = new Date(); return TODAY + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
  const coName = code => CO[code] ? CO[code].short : code;
  const trim = (s, k) => { s = s || ''; return s.length > k ? s.slice(0, k - 1) + '…' : s; };
  function shortName(name, code) {
    if (code && CO[code]) return CO[code].short;
    let s = String(name || '').replace(/,?\s*\b(Incorporated|Inc\.|Co\.,\s*Ltd\.|Ltd\.|Limited|CORPORATION|Corporation|Holdings?|Group)\b\.?/gi, '').replace(/[,\s]+$/, '').trim();
    if (s && s === s.toUpperCase()) s = s.toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
    return s;
  }
  function findCompany(text) {
    const m = text.match(/\b(\d{4})\b/); if (m && CO[m[1]]) return m[1];
    const t = text.toLowerCase();
    if (/tokio/.test(t)) return '8766'; if (/mizuho/.test(t)) return '8411'; if (/mufg|mitsubishi/.test(t)) return '8306';
    if (/smfg|sumitomo/.test(t)) return '8316'; if (/ms&ad|msad|ms & ad/.test(t)) return '8725'; if (/toyota/.test(t)) return '7203';
    return null;
  }
  let toastT;
  function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2200); }

  /* --------------------------------------------------------- specialists */
  const TOOLS = {
    get_company: 'Every dataset’s view of one company',
    get_series: 'One series with its formula attached',
    get_vintages: 'How a figure moved, release by release',
    screen: 'Rank a universe on filed metrics',
    search: 'Find a company, series or dataset',
    check_claim: 'Test a sentence against the filings',
    get_breakdown: 'Contributions behind a headline',
    compare_cohort: 'One company against its peers',
    describe_dataset: 'A dataset’s source, measures and formulas',
    get_overview: 'The latest release at a glance',
    my_coverage: 'Your coverage list',
    slack_post: 'Post to Slack, after your approval'
  };
  const SP = [
    { id: 'coverage-monitor', ini: 'CM', name: 'Coverage Monitor', cat: 'Coverage', ver: '1.4.0', update: '1.5.0',
      desc: 'Re-reads every name on your list each night and files one event per change.',
      brief: 'Every night after the filings refresh, I re-read each company on your list and file one event for every change. Each event carries its filing id.',
      tools: ['get_company', 'get_vintages', 'search', 'my_coverage', 'describe_dataset', 'get_series'], workflows: ['nightly-refresh', 'new-filing', 'coverage-diff'],
      schedule: 'Nightly at 04:00 JST', delivery: 'Events to #desk, digest at 07:00',
      watch: [['Coverage list', 'The names you follow', 'my_coverage'], ['New filings', 'Each new document, the night it lands', 'get_company'], ['Revisions', 'A figure that changed between versions', 'get_vintages']],
      tries: ['Add Tokio Marine (8766) to my list', 'What changed on my names last night?', 'Which of my names has no buyback data?'] },
    { id: 'buyback-monitor', ini: 'BM', name: 'Buyback Monitor', cat: 'Capital returns', ver: '1.2.0',
      desc: 'Tracks every buyback on your names: yen bought against yen authorised, month by month.',
      brief: 'I read each monthly buyback report on your names and track what was bought against what the board authorised. I flag any window that closes unspent, and ask before posting to Slack.',
      tools: ['get_company', 'get_vintages', 'my_coverage', 'slack_post'], workflows: ['monthly-report', 'window-close', 'unspent-alert'],
      schedule: 'On each monthly buyback report', delivery: 'Slack #japan-desk, after approval',
      watch: [['Programmes', 'Window, yen and shares authorised', 'get_company'], ['Monthly progress', 'Yen bought to date, as filed', 'get_company'], ['Unspent at close', 'Money left when a window ends', 'get_vintages']],
      tries: ['Which of my names has a buyback running?', 'Did any window close unspent this year?', 'How much has Mizuho bought so far?'] },
    { id: 'macro-brief', ini: 'MR', name: 'Macro Release Brief', cat: 'Macro', ver: '1.1.0',
      desc: 'A short note on each CPI, BOJ and JGB release, with the formula behind every rate.',
      brief: 'On release mornings I post a short note: index levels as published, and the rates I derive with their formula. If a reading crosses your line, I say so first.',
      tools: ['get_overview', 'get_series', 'get_breakdown', 'get_vintages', 'describe_dataset'], workflows: ['release-note', 'threshold-check'],
      schedule: 'On each CPI, BOJ and JGB release', delivery: 'Note to #desk at 06:40 on release day',
      watch: [['CPI', 'Headline and the two cores, 2025 = 100', 'get_overview'], ['Your thresholds', 'Core inflation above 1.5%', 'get_series'], ['Revisions', 'A published index that changed', 'get_vintages']],
      tries: ['What did July CPI print?', 'Is core inflation above my line?', 'What is the 3-month annualised rate?'] },
    { id: 'cross-shareholding', ini: 'CS', name: 'Cross-Shareholding Monitor', cat: 'Ownership', ver: '1.3.0',
      desc: 'Ranks your names by how fast they are selling their cross-shareholdings.',
      brief: 'I read the cross-shareholding section of every annual report on your names. I track each holding, flag exits and sharp cuts, and rank your coverage by how fast it is selling.',
      tools: ['get_company', 'screen', 'get_vintages', 'my_coverage'], workflows: ['unwind-ranking', 'exit-alert', 'purpose-change-watch'],
      schedule: 'On each annual report', delivery: 'Threads, Slack on request',
      watch: [['Holdings', 'Shares and value, this year and last', 'get_company'], ['Exits and cuts', 'Positions sold out or cut sharply', 'screen'], ['Stated reason', 'Why each holding is kept', 'get_vintages']],
      tries: ['Which of my names cut the most holdings this year?', 'Who still holds MUFG, and why?', 'List every exit above ¥10bn'] },
    { id: 'filing-review', ini: 'FR', name: 'Filing Review', cat: 'Filings', ver: '1.0.0',
      desc: 'Lists what moved in each new annual report against last year.',
      brief: 'When a company on your list files its annual report, I compare it with last year’s and list what moved, section by section. Each line links to the source document.',
      tools: ['get_company', 'get_vintages', 'search', 'describe_dataset', 'check_claim', 'compare_cohort', 'my_coverage'], workflows: ['year-on-year'],
      schedule: 'On each annual report', delivery: 'One thread per filing',
      watch: [['Segments', 'Sales and profit by segment', 'get_company'], ['Customers', 'Customers named in the report', 'get_company'], ['Pay', 'Named officer pay', 'get_company']],
      tries: ['What moved in Toyota’s latest report?'] },
    { id: 'governance-monitor', ini: 'GM', name: 'Governance Monitor', cat: 'Governance', ver: '1.2.0',
      desc: 'Watches board changes, AGM votes and officer pay across your names.',
      brief: 'I watch the board and the AGM. When a director changes, a vote passes narrowly, or pay crosses your line, I tell you with the filing attached.',
      tools: ['get_company', 'screen', 'get_vintages', 'compare_cohort', 'my_coverage'], workflows: ['agm-season', 'board-change'],
      schedule: 'On each AGM result and annual report', delivery: 'Events to #desk',
      watch: [['Directors', 'Joiners, leavers, independence', 'get_company'], ['AGM votes', 'Approval rate per resolution', 'get_company']],
      tries: ['Which AGM votes on my names passed below 80%?'] },
    { id: 'disclosure-verification', ini: 'DV', name: 'Disclosure Verification', cat: 'Verification', ver: '1.1.0',
      desc: 'Checks a sentence from a broker note against the filings.',
      brief: 'Paste a sentence and I test it against the filings and official statistics. I answer supported, partly supported or not supported, and show the numbers that decide it.',
      tools: ['check_claim', 'get_series'], workflows: ['claim-check'],
      schedule: 'On request', delivery: 'Thread',
      watch: [['Claims', 'Each sentence you paste', 'check_claim'], ['Evidence', 'The figures behind each verdict', 'get_series']],
      tries: ['Check: “Toyota completed its ¥4.3tn buyback”'] },
    { id: 'financials-extract', ini: 'FE', name: 'Financials Extract', cat: 'Financials', ver: '0.9.0',
      desc: 'Fills your spreadsheet model from filed statements, cell by cell.',
      brief: 'I fill your model from the filed statements, in your row order. Every cell records the document it came from.',
      tools: ['get_company', 'get_series', 'describe_dataset', 'get_vintages'], workflows: ['model-fill'],
      schedule: 'On each annual and quarterly filing', delivery: 'CSV to your files',
      watch: [['Statements', 'Income, balance sheet, cash flow', 'get_company']],
      tries: ['Fill my model for MUFG FY2026'] },
    { id: 'research-draft', ini: 'RD', name: 'Research Draft', cat: 'Research', ver: '1.0.0',
      desc: 'Turns a research thread into a note with citable links.',
      brief: 'Point me at a thread and I draft the note, with every number linked to its source. Nothing is published without your approval.',
      tools: ['get_company', 'get_series', 'search'], workflows: ['note-draft'],
      schedule: 'On request', delivery: 'Draft to your files',
      watch: [['Threads', 'The threads you point me at', 'search']],
      tries: ['Draft a note from the bank unwind thread'] }
  ];
  const SPX = Object.fromEntries(SP.map(s => [s.id, s]));
  const HANDLE = id => '@' + id;

  /* --------------------------------------------------------------- state */
  const DEFAULT = () => ({
    theme: null,
    added: ['coverage-monitor', 'buyback-monitor', 'macro-brief'],
    versions: {},
    coverage: ['7203', '8725', '8316', '8306', '8411'],
    approvals: {},
    feed: [], threads: {}, threadOrder: [],
    policy: { read: true, workspace: true, outward: true },
    slack: '#japan-desk',
    settingsOpen: null,
    catTab: 'all', catQ: ''
  });
  let S = DEFAULT();   // replaced by load() at boot, once every helper exists
  function load() {
    try { const s = JSON.parse(localStorage.getItem(STORE)); if (s && s.feed && s.feed.length) return s; } catch (e) { }
    const s = DEFAULT(); S = s; seed(s); return s;
  }
  function save() { try { localStorage.setItem(STORE, JSON.stringify(S)); } catch (e) { } }
  const isAdded = id => S.added.includes(id);
  const ver = id => S.versions[id] || SPX[id].ver;

  /* ---------------------------------------------------------- data views */
  const programmes = code => (CO[code] && CO[code].programs) || [];
  function unwindRanking() {
    return S.coverage.map(c => {
      const co = CO[c];
      const listed = (co.flows || []).find(f => f.share_class === 'listed');
      const sh = co.scale_history || [];
      const open = sh.length > 1 ? sh[sh.length - 2] : null;
      const proceeds = listed ? listed.sale_proceeds_yen : null;
      return { code: c, name: co.short, proceeds, issuesDown: listed ? listed.issues_decreased : null,
        pace: proceeds != null && open ? proceeds / open.policy_total_yen * 100 : null,
        open: open ? open.policy_total_yen : null, doc: co.filing.doc_id };
    }).sort((a, b) => (b.pace == null ? -1 : b.pace) - (a.pace == null ? -1 : a.pace));
  }
  const cpiTile = k => DATA.cpi.tiles.find(t => t.key === k);
  function cpiIndex(code) { const s = DATA.cpi.index.series.find(x => x.code === code); return s ? s.points[s.points.length - 1][1] : null; }
  const LIFE = { running: 'Running', awaiting_final: 'Closed, final report due', completed: 'Completed', expired_unspent: 'Closed unspent', unknown: 'Not stated' };
  const FILED = ['official', 'Official · as filed'];
  const table = (head, rows) => `<table class="t"><thead><tr>${head.map(h => `<th${h[1] ? ' class="right"' : ''}>${h[0]}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;

  /* ------------------------------------------------------- answer engine */
  // Returns {html, trail, tags}. Every figure is computed from DATA.
  function answer(q, spId) {
    const t = q.toLowerCase(); const trail = []; const tags = [];
    const call = (fn, args) => trail.push({ fn, args, ms: 40 + Math.round(Math.random() * 120) });
    const code = findCompany(q);
    let html = '';

    if (/check:|completed its/.test(t)) {
      const p = programmes('7203')[0];
      call('check_claim', 'claim=' + trim(q, 40)); call('get_company', 'code=7203 dataset=buybacks');
      html = `<p><b>Partly supported.</b> Toyota’s board authorised ${yen(p.authorised_yen)}. When the window closed on ${dmy(p.window_end)} it had bought ${yen(p.cumulative_yen)}, or ${pct(p.completion_pct)}.</p><p>So the buyback did not complete: ${yen(p.unspent_yen)} was left unspent.</p>`;
      tags.push(FILED, ['src', 'EDINET ' + p.last_doc_id], ['calc', 'bought % = cumulative yen acquired ÷ yen authorised × 100; unspent = authorised − cumulative']);
    }
    else if (/\badd\b/.test(t) && code) {
      call('search', 'q=' + CO[code].short.toLowerCase()); call('get_company', 'code=' + code);
      const co = CO[code];
      if (S.coverage.includes(code)) html = `<p><b>${esc(co.short)}</b> is already on your list.</p>`;
      else {
        S.coverage.push(code);
        const running = programmes(code).filter(p => p.lifecycle === 'running').length;
        html = `<p>Added <b>${esc(co.short)} (${code})</b>. I read its latest annual report, for the year to ${dmy(co.filing.period_end)} 2026. ${running ? 'It has a buyback running.' : 'It has no buyback running.'}</p>`;
        tags.push(FILED, ['src', 'EDINET ' + co.filing.doc_id]);
      }
    }
    else if (/(no buyback|gap|missing)/.test(t)) {
      call('my_coverage', ''); S.coverage.forEach(c => call('describe_dataset', 'company=' + c));
      html = '<p>Datasets that do not hold each of your names:</p>' + table([['Company'], ['Missing']], S.coverage.map(c => `<tr><td>${esc(CO[c].short)}</td><td>${CO[c].coverage.coverage.missing.map(m => esc(m.dataset)).join(', ') || 'None'}</td></tr>`));
    }
    else if (/exit/.test(t)) {
      const th = (t.match(/¥\s?(\d+)\s?bn/) || [])[1]; const lim = th ? +th * 1e9 : 1e10;
      call('my_coverage', ''); S.coverage.forEach(c => call('get_company', 'code=' + c + ' dataset=cross-shareholdings'));
      const exits = [], cuts = [];
      S.coverage.forEach(c => (CO[c].holdings || []).forEach(h => {
        if ((h.prior_shares || 0) > 0 && !h.shares && (h.prior_book_value_yen || 0) >= lim) exits.push({ c, h });
        if (h.prior_shares && h.shares && h.shares < h.prior_shares && (h.prior_book_value_yen || 0) >= lim) cuts.push({ c, h });
      }));
      cuts.sort((a, b) => b.h.prior_book_value_yen - a.h.prior_book_value_yen);
      html = `<p>Holdings sold out entirely in the latest annual reports, worth ${yen(lim, 0)} or more a year earlier.</p>` +
        (exits.length ? table([['Seller'], ['Sold out of'], ['Value a year earlier', 1]], exits.map(x => `<tr><td>${esc(coName(x.c))}</td><td>${esc(shortName(x.h.held_name_en || x.h.held_name_raw, x.h.held_sec_code))}</td><td class="right">${yen(x.h.prior_book_value_yen)}</td></tr>`)) : '<p>None.</p>') +
        `<p>Largest partial cuts of the same size:</p>` +
        (cuts.length ? table([['Seller'], ['Holding'], ['Shares sold', 1], ['Value a year earlier', 1]], cuts.slice(0, 8).map(x => `<tr><td>${esc(coName(x.c))}</td><td>${esc(shortName(x.h.held_name_en || x.h.held_name_raw, x.h.held_sec_code))}</td><td class="right">${n(x.h.prior_shares - x.h.shares)}</td><td class="right">${yen(x.h.prior_book_value_yen)}</td></tr>`)) : '<p>None.</p>');
      tags.push(FILED, ['src', 'Annual reports, year to March 2026']);
    }
    else if (/(cut|reduc|unwind|sell|selling|sold|fastest|pace)/.test(t) && !code) {
      call('my_coverage', ''); S.coverage.forEach(c => call('get_company', 'code=' + c + ' dataset=cross-shareholdings'));
      const r = unwindRanking();
      html = `<p>Your names, ranked by how much of their listed cross-holdings they sold last year.</p>` +
        table([['Company'], ['Sold', 1], ['Stakes cut', 1], ['Pace', 1], ['Report']], r.map(x => `<tr><td>${esc(x.name)}</td><td class="right">${yen(x.proceeds)}</td><td class="right">${n(x.issuesDown)}</td><td class="right">${pct(x.pace)}</td><td><span class="code">${x.doc}</span></td></tr>`)) +
        `<p><b>${esc(r[0].name)}</b> is selling fastest: ${yen(r[0].proceeds)} sold, from ${yen(r[0].open)} held a year earlier.</p>`;
      tags.push(FILED, ['calc', 'pace = listed sale proceeds in the year ÷ cross-holdings at the prior year end × 100. Sale proceeds are as filed (売却価額); they are a flow, not a fall in book value.']);
    }
    else if (/(who (still )?holds|holders?)/.test(t) && code) {
      call('get_company', 'code=' + code + ' dataset=cross-shareholdings table=holders');
      const hs = (CO[code].holders || []).slice(0, 8);
      const cut = hs.filter(h => h.prior_shares != null && h.shares != null && h.shares < h.prior_shares).length;
      html = `<p>Companies that hold <b>${esc(CO[code].short)}</b> as a cross-shareholding, largest first.</p>` +
        table([['Holder'], ['Shares', 1], ['Change', 1], ['Value', 1], ['Reason given']], hs.map(h => `<tr><td>${esc(shortName(h.holder_name_en || h.holder_name, h.holder_sec_code))}</td><td class="right">${n(h.shares)}</td><td class="right">${h.shares != null && h.prior_shares != null ? signed(h.shares - h.prior_shares) : DASH}</td><td class="right">${yen(h.book_value_yen)}</td><td class="reason" title="${esc(h.purpose_ja || '')}">${esc(trim(h.purpose_ja, 22)) || DASH}</td></tr>`)) +
        `<p>${cut ? cut + ' of the ' + hs.length + ' cut their stake this year.' : 'None of the ' + hs.length + ' cut their stake this year.'}</p>`;
      tags.push(FILED);
    }
    else if (/(buyback|repurchase|window|bought|unspent|running)/.test(t)) {
      const list = code ? [code] : S.coverage;
      call('my_coverage', ''); list.forEach(c => call('get_company', 'code=' + c + ' dataset=buybacks'));
      const ps = []; list.forEach(c => programmes(c).forEach(p => ps.push(Object.assign({ code: c }, p))));
      const live = (code ? ps : ps.filter(p => p.lifecycle !== 'completed')).sort((a, b) => a.resolution_date < b.resolution_date ? 1 : -1);
      html = `<p>${code ? esc(CO[code].short) + '’s buyback programmes.' : 'Buybacks on your names that are running or closed without completing.'}</p>` +
        table([['Company'], ['Window'], ['Authorised', 1], ['Bought', 1], ['Done', 1], ['Status']], live.map(p => `<tr><td>${esc(coName(p.code))}</td><td class="nowrap">${dmy(p.window_start)} – ${dmy(p.window_end)}</td><td class="right">${yen(p.authorised_yen)}</td><td class="right">${yen(p.cumulative_yen)}</td><td class="right">${pct(p.completion_pct)}</td><td>${LIFE[p.lifecycle] || p.lifecycle}</td></tr>`));
      const un = live.filter(p => p.lifecycle === 'expired_unspent');
      if (un.length) html += un.map(p => `<p><b>${esc(coName(p.code))}</b> closed its window on ${dmy(p.window_end)} with ${yen(p.unspent_yen)} unspent.</p>`).join('');
      tags.push(FILED, ['calc', 'done = cumulative yen acquired ÷ yen authorised × 100. Left blank where the filing states one figure and not the other.']);
    }
    else if (/(cpi|inflation|price|macro|core|print|annuali[sz]ed|line)/.test(t)) {
      call('get_overview', 'dataset=cpi-jp'); call('get_series', 'dataset=cpi-jp series=0001,0161,0165 measure=yoy');
      const h = cpiTile('headline_yoy'), c = cpiTile('core_yoy'), cc = cpiTile('corecore_yoy'), m = cpiTile('headline_mom'), a3 = cpiTile('headline_ann3m');
      const rel = DATA.cpi.release;
      const row = (label, code, tile) => `<tr><td>${label}</td><td class="right">${cpiIndex(code) == null ? DASH : cpiIndex(code).toFixed(1)}</td><td class="right">${pct(tile.value)}</td><td class="right">${pct(tile.value - tile.delta_pp)}</td></tr>`;
      html = `<p><b>${monthName(rel.latest_period)} CPI</b>, national, 2025 = 100.</p>` +
        table([['Measure'], ['Index', 1], ['Year on year', 1], ['Prior month', 1]], [row('All items', '0001', h), row('Less fresh food', '0161', c), row('Less fresh food and energy', '0165', cc)]) +
        `<p>Month on month ${pct(m.value)}; three-month annualised ${pct(a3.value)}. Core is ${c.value >= 1.5 ? 'above' : 'below'} your 1.5% line by ${pp(c.value - 1.5)}.</p>`;
      tags.push(['official', 'Index · official statistic'], ['src', 'e-Stat ' + rel.source_id.split(':')[1]], ['calc', 'year on year = (index[t] ÷ index[t−12] − 1) × 100, from the published index. Three-month annualised = ((index[t] ÷ index[t−3])^4 − 1) × 100. A rate from rounded indices can differ from the Bureau’s by ±0.1 pp.']);
    }
    else if (/(purpose|reason|why)/.test(t) && code) {
      call('get_company', 'code=' + code + ' dataset=cross-shareholdings');
      const hs = (CO[code].holdings || []).filter(h => h.shares).slice(0, 8);
      html = `<p>Why <b>${esc(CO[code].short)}</b> says it keeps its largest holdings, in its own words.</p>` +
        table([['Holding'], ['Value', 1], ['Reason given']], hs.map(h => `<tr><td>${esc(shortName(h.held_name_en || h.held_name_raw, h.held_sec_code))}</td><td class="right">${yen(h.book_value_yen)}</td><td class="reason" title="${esc(h.purpose_ja || '')}">${esc(trim(h.purpose_ja, 34)) || DASH}</td></tr>`));
      tags.push(FILED, ['src', 'EDINET ' + CO[code].filing.doc_id]);
    }
    else if (/(changed|last night|moved|new filing)/.test(t)) {
      call('my_coverage', ''); S.coverage.forEach(c => call('get_vintages', 'code=' + c));
      html = `<p>No new filings since the last run at 04:03. The latest annual reports on file:</p>` +
        table([['Company'], ['Year end'], ['Report']], S.coverage.map(c => `<tr><td>${esc(CO[c].short)}</td><td>${dmy(CO[c].filing.period_end)} 2026</td><td><span class="code">${CO[c].filing.doc_id}</span></td></tr>`));
      tags.push(FILED);
    }
    else {
      const sp = SPX[spId] || SPX['coverage-monitor'];
      html = `<p>This prototype answers a set list of questions. Try: ${sp.tries.map(x => '“' + esc(x) + '”').join(' or ')}.</p>`;
    }
    return { html, trail, tags };
  }

  /* ----------------------------------------------------------------- seed */
  function seed(s) {
    const h = cpiTile('headline_yoy'), c = cpiTile('core_yoy'); const rel = DATA.cpi.release;
    const miz = programmes('8411').find(p => p.lifecycle === 'running'); const toy = programmes('7203')[0];
    const BOUGHT = ['calc', 'bought % = cumulative yen acquired ÷ yen authorised × 100; unspent = authorised − cumulative'];
    s.feed = [
      { id: 'f1', who: 'me', ts: '2026-06-02 09:14', text: '@coverage-monitor add Mizuho FG (8411) to my list', reply: 'Coverage Monitor added Mizuho FG.' },
      { id: 'f2', who: 'buyback-monitor', ts: '2026-07-13 06:05',
        text: `<b>Toyota</b> closed its buyback window with <b>${yen(toy.unspent_yen)} unspent</b>. It bought ${pct(toy.completion_pct)} of the ${yen(toy.authorised_yen)} authorised.`,
        tags: [FILED, ['src', 'EDINET ' + toy.last_doc_id], BOUGHT], approval: 'a1',
        trail: [{ fn: 'get_company', args: 'code=7203 dataset=buybacks', ms: 88 }, { fn: 'get_vintages', args: 'code=7203 doc=' + toy.last_doc_id, ms: 61 }] },
      { id: 'f3', who: 'buyback-monitor', ts: TODAY + ' 04:12',
        text: `<b>Mizuho</b> has bought ${yen(miz.cumulative_yen)} of its ${yen(miz.authorised_yen)} programme, or ${pct(miz.completion_pct)}. The window closes ${dmy(miz.window_end)}.`,
        tags: [FILED, ['src', 'EDINET ' + miz.last_doc_id], BOUGHT],
        trail: [{ fn: 'my_coverage', args: '', ms: 12 }, { fn: 'get_company', args: 'code=8411 dataset=buybacks', ms: 74 }] },
      { id: 'f4', who: 'macro-brief', ts: TODAY + ' 06:40',
        text: `<b>${monthName(rel.latest_period)} CPI ${pct(h.value)}</b> year on year, core ${pct(c.value)}. Core is above your 1.5% line.`,
        tags: [['src', 'e-Stat ' + rel.source_id.split(':')[1]], ['calc', 'year on year = (index[t] ÷ index[t−12] − 1) × 100, from the published index']],
        trail: [{ fn: 'get_overview', args: 'dataset=cpi-jp', ms: 52 }, { fn: 'get_series', args: 'series=0001,0161 measure=yoy', ms: 47 }] }
    ];
    const mk = (who, ts, q) => { const a = answer(q, who); return [{ who: 'me', ts, html: '<p>' + esc(q) + '</p>' }, { who, ts, html: a.html, trail: a.trail, tags: a.tags }]; };
    s.threads = {
      t1: { id: 't1', title: 'Bank unwind pace', sp: 'cross-shareholding', updated: '2026-06-02 15:20',
        msgs: [...mk('cross-shareholding', '2026-06-02 15:02', 'Which of my names cut the most holdings this year?'), ...mk('cross-shareholding', '2026-06-02 15:20', 'Who still holds MUFG, and why?')] },
      t2: { id: 't2', title: 'MS&AD holdings', sp: 'cross-shareholding', updated: '2026-06-30 11:04',
        msgs: mk('cross-shareholding', '2026-06-30 11:04', 'Why does MS&AD keep its largest holdings?') }
    };
    s.threadOrder = ['t2', 't1'];
  }

  /* -------------------------------------------------------------- render */
  const I = d => `<svg viewBox="0 0 24 24">${d}</svg>`;
  const ICONS = {
    inbox: I('<path d="M3 13l2.5-8h13L21 13v6H3z"/><path d="M3 13h5l1.5 2.5h5L16 13h5"/>'),
    desk: I('<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>'),
    specialists: I('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.5c3 0 6 2 6 5.5"/>'),
    research: I('<path d="M4 5h16v11H9l-5 4z"/>'),
    files: I('<path d="M3 6h6l2 2h10v11H3z"/>'),
    audit: I('<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>'),
    settings: I('<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>'),
    search: I('<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>'),
    theme: I('<path d="M12 3a9 9 0 1 0 9 9c-5 0-9-4-9-9z"/>'),
    plus: I('<path d="M12 5v14M5 12h14"/>'),
    up: I('<path d="M12 19V5M5 12l7-7 7 7"/>'),
    check: I('<path d="M5 12.5l4 4 10-10"/>')
  };
  const NAV = [['inbox', 'Inbox'], ['desk', 'Desk'], ['specialists', 'Specialists'], ['research', 'Research'], ['files', 'Files'], ['audit', 'Audit'], ['settings', 'Settings']];

  function route() { const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean); return { sec: parts[0] || 'desk', id: parts[1] ? decodeURIComponent(parts[1]) : null, sub: parts[2] || null }; }
  function go(path) { location.hash = '#/' + path; }
  const pendingApprovals = () => S.feed.filter(p => p.approval && !S.approvals[p.approval]);

  function render() {
    const r = route();
    document.documentElement.setAttribute('data-theme', S.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
    renderNav(r);
    const views = { inbox: vInbox, desk: vDesk, specialists: r.id ? vSpecialist : vSpecialists, research: r.id ? vThread : vResearch, files: vFiles, audit: vAudit, settings: vSettings };
    $('#main').innerHTML = (views[r.sec] || vDesk)(r);
    bind(r); save();
  }

  function renderNav(r) {
    const pending = pendingApprovals().length;
    $('#nav').innerHTML = `
      <div class="brand"><span class="logo">PA</span><div><b>Investment Assistant</b><small>[YOUR FIRM]</small></div></div>
      <div class="navlist">${NAV.map(([k, l]) => `<button class="navitem ${r.sec === k ? 'on' : ''}" data-go="${k}">${ICONS[k]}<span>${l}</span>${k === 'inbox' && pending ? `<em class="count">${pending}</em>` : ''}</button>`).join('')}</div>
      <div class="navsec">Your specialists</div>
      <div class="navlist mine">${S.added.map(id => SPX[id]).map(s => `<button class="navitem ${r.sec === 'specialists' && r.id === s.id ? 'on' : ''}" data-go="specialists/${s.id}"><span class="mini">${s.ini}</span><span>${s.name}</span></button>`).join('')}
        <button class="navitem add" data-go="specialists">${ICONS.plus}<span>Add a specialist</span></button></div>
      <div class="navfoot"><span class="av sm me">A</span><span>[YOUR NAME]</span><button id="theme" title="Switch light / dark">${ICONS.theme}</button></div>`;
  }
  function topbar(crumbs, extra) {
    return `<header class="topbar"><nav class="crumbs">${crumbs.map((c, i) => (i ? '<span class="sep">/</span>' : '') + (c[1] ? `<a href="#/${c[1]}">${esc(c[0])}</a>` : `<b>${esc(c[0])}</b>`)).join('')}</nav><div class="grow"></div>${extra || ''}<button class="searchbtn" id="open-search">${ICONS.search}<span>Search</span><kbd>⌘K</kbd></button></header>`;
  }
  const pageHead = (title, sub, actions) => `<div class="pagehead"><div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${actions ? `<div class="actions">${actions}</div>` : ''}</div>`;
  const wrapTables = h => h.replace(/<table/g, '<div class="tw"><table').replace(/<\/table>/g, '</table></div>');

  // one quiet line under any answer: trust badge, source, and two reveals
  function provHtml(tags, trail, id) {
    const parts = []; let formula = null;
    (tags || []).forEach(t => {
      if (t[0] === 'official') parts.push(`<span class="badge official">${esc(t[1])}</span>`);
      else if (t[0] === 'src') parts.push(`<span>${esc(t[1])}</span>`);
      else if (t[0] === 'calc') formula = t[1];
    });
    if (formula) parts.push(`<button class="linkbtn toggle" data-t="f-${id}">Show calculation</button>`);
    if (trail && trail.length) parts.push(`<button class="linkbtn toggle" data-t="t-${id}">${trail.length} tool call${trail.length > 1 ? 's' : ''}</button>`);
    if (!parts.length) return '';
    return `<div class="prov">${parts.join('')}</div>` +
      (formula ? `<div class="reveal hidden" id="f-${id}"><code>${esc(formula)}</code></div>` : '') +
      (trail && trail.length ? `<div class="trail hidden" id="t-${id}">${trail.map((c, i) => `<div><span class="n">${i + 1}</span><span class="fn">${c.fn}</span><span class="args">${esc(c.args)}</span><span class="ms">${c.ms} ms</span></div>`).join('')}</div>` : '');
  }

  /* ---- Desk */
  function spStatus(s) {
    const pend = pendingApprovals().filter(p => p.who === s.id).length;
    if (s.id === 'buyback-monitor') return pend ? ['wait', pend + ' waiting for approval', 'Next: each monthly report'] : ['ok', 'Ran 04:12', 'Next: each monthly report'];
    if (s.id === 'coverage-monitor') return ['ok', 'Ran 04:03, no changes', 'Next: tonight 04:00'];
    if (s.id === 'macro-brief') return ['ok', 'Posted CPI note 06:40', 'Next: BOJ accounts, 30 Sep'];
    return ['idle', 'Not run yet', s.schedule];
  }
  function vDesk() {
    const mine = S.added.map(id => SPX[id]);
    const pend = pendingApprovals().length;
    const tree = `<section class="tree">
      <div class="root"><span class="av me">A</span><div><b>[YOUR NAME]</b><small>${S.coverage.length} companies covered</small></div></div>
      <ul>${mine.map(s => { const st = spStatus(s); return `<li><button class="node" data-go="specialists/${s.id}"><span class="av">${s.ini}</span><span class="who"><b>${s.name}</b><small>${HANDLE(s.id)}</small></span><span class="st"><span><i class="dot ${st[0]}"></i>${st[1]}</span><small>${esc(st[2])}</small></span></button></li>`; }).join('')}</ul>
      <div class="addrow"><button class="btn" data-go="specialists">${ICONS.plus} Add a specialist</button></div></section>`;
    return topbar([['Desk']]) + `<div class="content"><div class="page wide fit">${pageHead('Desk', pend ? `${pend} post waiting for your approval.` : 'Your specialists and what they found.')}<div class="desk">${tree}${vFeed()}</div></div></div>`;
  }
  function vFeed() {
    const posts = S.feed.map(p => {
      if (p.who === 'system') return `<div class="sysline">${p.text}</div>`;
      if (p.who === 'me') return `<article class="post"><span class="av me">A</span><div class="pbody"><header><b>You</b><time>${when(p.ts)}</time></header><p>${esc(p.text)}</p>${p.reply ? `<p class="reply">${esc(p.reply)}</p>` : ''}</div></article>`;
      const sp = SPX[p.who]; const st = p.approval ? S.approvals[p.approval] : null;
      const appr = !p.approval ? '' : st === 'approved' ? `<p class="done ok">${ICONS.check}Posted to ${esc(S.slack)}</p>` : st === 'declined' ? '<p class="done muted">Declined. Nothing was sent.</p>'
        : `<div class="approval"><span title="${esc(S.slack)}">Post to Slack?</span><button class="btn sm primary" data-approve="${p.approval}">Approve</button><button class="btn sm" data-decline="${p.approval}">Decline</button></div>`;
      return `<article class="post"><span class="av">${sp.ini}</span><div class="pbody"><header><b>${sp.name}</b><time>${when(p.ts)}</time></header><div class="ptext">${wrapTables(p.text)}</div>${provHtml(p.tags, p.trail, p.id)}${appr}</div></article>`;
    }).join('');
    return `<section class="feed"><header class="feedhead"><b># desk</b><span>Type @ to bring in a specialist</span></header><div class="posts" id="posts">${posts}</div>
      <div class="compose"><textarea id="compose" placeholder="Message #desk"></textarea><button class="btn" id="m-at" title="Mention a specialist">@</button><button class="btn" id="send">Send</button>
      <div class="mentions hidden" id="mentions">${S.added.map(id => `<button data-m="${id}">${SPX[id].name}</button>`).join('')}</div></div></section>`;
  }

  /* ---- Specialists catalogue */
  function vSpecialists() {
    const tab = ['all', 'added', 'available'].includes(S.catTab) ? S.catTab : 'all';
    const q = (S.catQ || '').toLowerCase();
    const list = SP.filter(s => tab === 'added' ? isAdded(s.id) : tab === 'available' ? !isAdded(s.id) : true)
      .filter(s => !q || (s.name + ' ' + s.desc + ' ' + s.cat).toLowerCase().includes(q));
    const updates = S.added.filter(id => SPX[id].update && ver(id) !== SPX[id].update).length;
    const cards = list.map(s => {
      const a = isAdded(s.id); const upd = a && s.update && ver(s.id) !== s.update;
      return `<article class="spcard"><div class="top"><span class="av">${s.ini}</span><div><b>${s.name}</b><small>${s.cat}</small></div>${a ? '<span class="added-tag">Added</span>' : ''}</div>
        <p>${esc(s.desc)}</p>
        <footer><span>${s.tools.length} tools · v${ver(s.id)}</span><span class="grow"></span>${upd ? `<button class="linkbtn warn" data-update="${s.id}">Update to v${s.update}</button>` : ''}${a ? `<button class="btn sm" data-go="specialists/${s.id}">Open</button>` : `<button class="btn sm" data-add="${s.id}">${ICONS.plus} Add</button>`}</footer></article>`;
    }).join('');
    return topbar([['Specialists']]) + `<div class="content"><div class="page wide">${pageHead('Specialists', 'Ready-made analysts for your desk. Each runs on your own model key and asks before anything goes out.', updates ? `<button class="btn" id="update-all">${ICONS.up} Update all (${updates})</button>` : '')}
      <div class="toolbar"><input class="search" id="cat-q" placeholder="Search specialists" value="${esc(S.catQ || '')}"><div class="tabs">${[['all', 'All'], ['added', 'Added'], ['available', 'Available']].map(x => `<button class="${tab === x[0] ? 'on' : ''}" data-tab="${x[0]}">${x[1]}</button>`).join('')}</div></div>
      <div class="grid3">${cards || '<p class="muted">No specialist matches.</p>'}</div></div></div>`;
  }

  /* ---- Specialist page */
  function sha(s) { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; } return h.toString(16).padStart(8, '0').slice(0, 7); }
  function vSpecialist(r) {
    const s = SPX[r.id]; if (!s) return vSpecialists();
    const a = isAdded(s.id);
    const threads = S.threadOrder.filter(id => S.threads[id].sp === s.id).map(id => S.threads[id]);
    const files = ['brief.md', 'coverage.json', 'rules/exit-threshold.yaml', 'runs/' + TODAY + '.log'];
    const setup = [['Observatory key', 'Connected', true], ['Delivery', S.slack, false], ['Approval first', S.policy.outward ? 'On' : 'Off', S.policy.outward]];
    const drawer = S.settingsOpen === s.id ? `<div class="drawer"><dl class="kv"><dt>Schedule</dt><dd>${esc(s.schedule)}</dd><dt>Delivery</dt><dd>${esc(s.delivery)}</dd><dt>Model</dt><dd>claude-sonnet-5 on your key</dd><dt>Workflows</dt><dd>${s.workflows.map(w => `<code>${w}</code>`).join(' ')}</dd></dl></div>` : '';
    return topbar([['Specialists', 'specialists'], [s.name]]) + `<div class="content"><div class="page wide">
      <div class="pagehead sp"><span class="av lg">${s.ini}</span><div><h1>${s.name}</h1><p>${esc(s.schedule)}</p></div><div class="actions">${a ? `<button class="btn" id="sp-settings">Settings</button>` : `<button class="btn primary" data-add="${s.id}">Add to my desk</button>`}</div></div>
      ${drawer}
      <div class="sp-layout"><div>
        <p class="brief">${esc(s.brief)}</p>
        <h3 class="sec">What it watches</h3>
        <ul class="rows">${s.watch.map(w => `<li><div><b>${esc(w[0])}</b><small>${esc(w[1])}</small></div><code title="${esc(TOOLS[w[2]] || '')}">${w[2]}</code></li>`).join('')}</ul>
        <h3 class="sec">Try asking</h3>
        <div class="chips">${s.tries.map(x => `<button class="chip" data-ask="${esc(x)}" data-sp="${s.id}">${esc(x)}</button>`).join('')}</div>
      </div>
      <aside class="side-stack">
        <section class="panel"><header>Setup</header><ul class="kvlist">${setup.map(x => `<li><span>${x[0]}</span><b class="${x[2] ? 'ok' : ''}">${x[2] ? ICONS.check : ''}${esc(x[1])}</b></li>`).join('')}</ul></section>
        <section class="panel"><header>Threads<small>${threads.length}</small><span class="grow"></span><button class="linkbtn" data-new-thread="${s.id}">${ICONS.plus}New</button></header>${threads.length ? `<ul class="linklist">${threads.map(t => `<li><button data-go="research/${t.id}"><b>${esc(t.title)}</b><small>${when(t.updated)}</small></button></li>`).join('')}</ul>` : '<p class="empty">No threads yet.</p>'}</section>
        <section class="panel"><header>Files</header><ul class="linklist mono">${files.map(f => `<li><button data-go="files/${s.id}/${encodeURIComponent(f)}">${f}</button></li>`).join('')}</ul></section>
        <p class="fine">Version ${ver(s.id)} · ${sha(s.id + ver(s.id))} · ${s.tools.length} tools via the Observatory</p>
      </aside></div></div></div>`;
  }

  /* ---- Inbox */
  function vInbox() {
    const items = S.feed.filter(p => p.approval);
    const open = items.filter(p => !S.approvals[p.approval]); const done = items.filter(p => S.approvals[p.approval]);
    const card = p => {
      const st = S.approvals[p.approval]; const sp = SPX[p.who];
      return `<article class="card"><header class="cardhead"><span class="av">${sp.ini}</span><div><b>${sp.name}</b><small>Wants to post to ${esc(S.slack)} · ${when(p.ts)}</small></div></header>
        <blockquote>${p.text}</blockquote>${provHtml(p.tags, p.trail, 'ib' + p.id)}
        <div class="cardacts">${st ? `<span class="${st === 'approved' ? 'ok' : 'muted'}">${st === 'approved' ? 'Approved and posted' : 'Declined. Nothing was sent.'}</span>` : `<button class="btn primary" data-approve="${p.approval}">Approve and post</button><button class="btn" data-decline="${p.approval}">Decline</button>`}</div></article>`;
    };
    const runs = [['macro-brief', TODAY + ' 06:40', 'CPI note posted to #desk'], ['buyback-monitor', TODAY + ' 04:12', 'Two monthly reports read'], ['coverage-monitor', TODAY + ' 04:03', S.coverage.length + ' names read, no changes']];
    return topbar([['Inbox']]) + `<div class="content"><div class="page narrow">${pageHead('Inbox', open.length ? `${open.length} waiting for your approval.` : 'Nothing is waiting on you.')}
      ${open.length ? `<h3 class="sec">Waiting on you</h3>${open.map(card).join('')}` : ''}
      <h3 class="sec">Recent runs</h3>
      <ul class="rows">${runs.map(x => `<li><span class="av sm">${SPX[x[0]].ini}</span><div><b>${SPX[x[0]].name}</b><small>${esc(x[2])}</small></div><time class="muted small">${when(x[1])}</time></li>`).join('')}</ul>
      ${done.length ? `<h3 class="sec">Decided</h3>${done.map(card).join('')}` : ''}</div></div>`;
  }

  /* ---- Research */
  function vResearch() {
    const def = isAdded('cross-shareholding') ? 'cross-shareholding' : 'macro-brief';
    return topbar([['Research']]) + `<div class="content"><div class="page narrow">${pageHead('Research', 'Conversations with your specialists. Every answer shows its sources.', `<button class="btn" data-new-thread="${def}">${ICONS.plus} New thread</button>`)}
      <ul class="rows">${S.threadOrder.map(id => { const t = S.threads[id]; return `<li data-go="research/${id}"><span class="av sm">${SPX[t.sp].ini}</span><div><b>${esc(t.title)}</b><small>${SPX[t.sp].name}</small></div><time class="muted small">${when(t.updated)}</time></li>`; }).join('')}</ul></div></div>`;
  }
  function vThread(r) {
    const t = S.threads[r.id]; if (!t) return vResearch(); const sp = SPX[t.sp];
    const msgs = t.msgs.map((m, i) => m.who === 'me'
      ? `<div class="msg"><span class="av me">A</span><div class="mbody"><header><b>You</b><time>${when(m.ts)}</time></header><div class="txt">${m.html}</div></div></div>`
      : `<div class="msg"><span class="av">${sp.ini}</span><div class="mbody"><header><b>${sp.name}</b><time>${when(m.ts)}</time></header><div class="txt">${wrapTables(m.html)}</div>${provHtml(m.tags, m.trail, t.id + '-' + i)}</div></div>`).join('');
    return topbar([['Research', 'research'], [t.title]], `<button class="btn sm" id="pin">Pin</button>`) + `<div class="content"><div class="page"><div class="thread">${pageHead(esc(t.title), 'With ' + sp.name + '. It only reads; nothing is sent without your approval.')}
      ${msgs}
      <div class="ask"><input id="ask" placeholder="Ask ${sp.name}"><button class="btn primary" id="ask-send">Ask</button></div>
      <div class="chips" style="margin-top:12px">${sp.tries.map(x => `<button class="chip" data-ask="${esc(x)}" data-sp="${sp.id}" data-thread="${t.id}">${esc(x)}</button>`).join('')}</div></div></div></div>`;
  }

  /* ---- Files */
  function fileContent(spId, path) {
    const s = SPX[spId];
    if (path === 'brief.md') return `# ${s.name}\nversion: ${ver(spId)}\n\n${s.brief}\n\n## Tools\n${s.tools.map(x => '- ' + x).join('\n')}\n\n## Schedule\n${s.schedule}\n\n## Delivery\n${s.delivery}`;
    if (path === 'coverage.json') return JSON.stringify({ owner: '[YOUR NAME]', updated: TODAY, names: S.coverage.map(c => ({ code: c, name: CO[c].name_en, latest_report: CO[c].filing.doc_id })) }, null, 2);
    if (path === 'rules/exit-threshold.yaml') return `# Rules checked on every run.\n# A rule can only read; anything outward waits for approval.\nrules:\n  - id: exit\n    when: holding.shares == 0 and holding.prior_shares > 0\n    post: "#desk"\n  - id: sharp-cut\n    when: holding.shares < 0.5 * holding.prior_shares\n    post: "#desk"\n  - id: unspent-window\n    when: programme.lifecycle == "expired_unspent"\n    post: "${S.slack}"   # outward: approval first\n  - id: core-cpi-line\n    when: cpi.core_yoy >= 1.5\n    post: "#desk"\n`;
    if (path.startsWith('runs/')) return `${TODAY} 04:03:11  run start   ${HANDLE(spId)} v${ver(spId)}\n` + S.coverage.map(c => `${TODAY} 04:03:12  get_company code=${c}  ${CO[c].filing.doc_id} unchanged`).join('\n') + `\n${TODAY} 04:03:14  run end     ok, 0 events`;
    return '';
  }
  function vFiles(r) {
    const spId = r.id && SPX[r.id] ? r.id : S.added[0]; const path = r.sub ? decodeURIComponent(r.sub) : 'brief.md';
    const names = ['brief.md', 'coverage.json', 'rules/exit-threshold.yaml', 'runs/' + TODAY + '.log'];
    const tree = S.added.map(id => `<div class="dir">${id}/</div>${names.map(f => `<button class="${id === spId && f === path ? 'on' : ''}" data-go="files/${id}/${encodeURIComponent(f)}">${f}</button>`).join('')}`).join('');
    return topbar([['Files'], [SPX[spId].name], [path]]) + `<div class="content"><div class="page wide">${pageHead('Files', 'Everything a specialist writes is a file you can read.')}
      <div class="files"><nav class="filetree">${tree}</nav><section class="panel"><header>${esc(spId + '/' + path)}<span class="grow"></span><small>Read-only</small></header><div class="body"><pre>${esc(fileContent(spId, path))}</pre></div></section></div></div></div>`;
  }

  /* ---- Audit */
  function vAudit() {
    const calls = {}; let total = 0;
    const add = (fn, who) => { calls[fn] = calls[fn] || { n: 0, who: new Set() }; calls[fn].n++; calls[fn].who.add(who); total++; };
    S.feed.forEach(p => (p.trail || []).forEach(c => add(c.fn, p.who)));
    S.threadOrder.forEach(id => S.threads[id].msgs.forEach(m => (m.trail || []).forEach(c => add(c.fn, S.threads[id].sp))));
    const rows = Object.entries(calls).sort((a, b) => b[1].n - a[1].n);
    const sent = Object.values(S.approvals).filter(v => v === 'approved').length;
    const tile = (k, v, s) => `<div class="tile"><small>${k}</small><b>${v}</b><span>${s}</span></div>`;
    return topbar([['Audit']]) + `<div class="content"><div class="page">${pageHead('Audit', 'Every tool call your specialists made. They have no other way to reach a number.')}
      <div class="tiles">${tile('Tool calls', n(total), 'since 2 Jun')}${tile('Runs', n(3 + S.threadOrder.length), 'scheduled and on request')}${tile('Waiting', n(pendingApprovals().length), 'for your approval')}${tile('Sent outward', n(sent), 'all approved by you')}</div>
      <h3 class="sec">Tools</h3><div class="panel">${table([['Tool'], ['Used by'], ['Calls', 1]], rows.map(([fn, v]) => `<tr><td><span class="code" title="${esc(TOOLS[fn] || '')}">${fn}</span></td><td>${[...v.who].map(w => SPX[w] ? SPX[w].name : w).join(', ')}</td><td class="right">${n(v.n)}</td></tr>`))}</div>
      <h3 class="sec">Recent runs</h3><div class="panel">${table([['When'], ['Specialist'], ['Trigger'], ['Calls', 1], ['Result']], [
        `<tr><td class="nowrap">Today 06:40</td><td>Macro Release Brief</td><td>CPI release</td><td class="right">2</td><td>Posted to #desk</td></tr>`,
        `<tr><td class="nowrap">Today 04:12</td><td>Buyback Monitor</td><td>Monthly reports</td><td class="right">2</td><td>Posted to #desk</td></tr>`,
        `<tr><td class="nowrap">Today 04:03</td><td>Coverage Monitor</td><td>Nightly</td><td class="right">${S.coverage.length + 1}</td><td>No changes</td></tr>`,
        `<tr><td class="nowrap">13 Jul 06:05</td><td>Buyback Monitor</td><td>Window closed</td><td class="right">2</td><td>Asked for approval</td></tr>`])}</div>
      <h3 class="sec">Connections</h3><div class="panel">${table([['Service'], ['Credentials'], ['Status']], [
        `<tr><td>Plover Analytics</td><td>Your Observatory key, ending 4f2a</td><td class="ok">Connected</td></tr>`,
        `<tr><td>Slack ${esc(S.slack)}</td><td>Webhook you supplied</td><td class="ok">Connected</td></tr>`,
        `<tr><td>Your firm’s research</td><td>Never stored here</td><td class="muted">Not added</td></tr>`])}</div></div></div>`;
  }

  /* ---- Settings */
  function vSettings() {
    const P = S.policy;
    const sw = (k, on) => `<label class="switch ${on ? 'on' : ''}" data-policy="${k}"><i></i>${on ? 'On' : 'Off'}</label>`;
    const rules = [['Read data', 'Specialists may call any read tool without asking.', sw('read', P.read)], ['Write to own files', 'Rules, logs and exports inside the specialist’s folder.', sw('workspace', P.workspace)], ['Anything outward', 'Slack posts, emails and shared notes wait for you.', sw('outward', P.outward)], ['Placing orders', 'Not possible. No specialist has a trading tool.', '<span class="switch locked"><i></i>Never</span>']];
    return topbar([['Settings']]) + `<div class="content"><div class="page narrow">${pageHead('Settings', 'Specialists run on your own model key. Plover Analytics never hosts a model.')}
      <h3 class="sec">Keys</h3><div class="panel"><dl class="kv body"><dt>Model key</dt><dd><span class="mono">sk-ant-•••• 4f2a</span><button class="linkbtn">Replace</button></dd><dt>Observatory key</dt><dd><span class="mono">plv_•••• 9c1e</span><button class="linkbtn">Rotate</button></dd><dt>Default model</dt><dd>claude-sonnet-5</dd></dl></div>
      <h3 class="sec">Approval policy</h3><ul class="rows">${rules.map(x => `<li><div><b>${x[0]}</b><small>${x[1]}</small></div>${x[2]}</li>`).join('')}</ul>
      <h3 class="sec">Delivery</h3><div class="panel"><dl class="kv body"><dt>Slack channel</dt><dd><input id="slack" class="mono" value="${esc(S.slack)}"></dd><dt>Email digest</dt><dd>Daily at 07:00 JST</dd></dl></div>
      <p class="fine" style="margin-top:40px">Prototype. Figures are a snapshot of the Plover Analytics API from ${dmy(TODAY)}; replies are computed from it and no model is called. <button class="linkbtn warn" id="reset">Reset demo</button></p></div></div>`;
  }

  /* ----------------------------------------------------------- behaviour */
  function addThread(spId, title, q) {
    const id = 't' + Date.now(); const a = answer(q, spId); const ts = nowStamp();
    S.threads[id] = { id, title, sp: spId, updated: ts, msgs: [{ who: 'me', ts, html: '<p>' + esc(q) + '</p>' }, { who: spId, ts, html: a.html, trail: a.trail, tags: a.tags }] };
    S.threadOrder.unshift(id); return id;
  }
  function askInThread(t, q) {
    const a = answer(q, t.sp); const ts = nowStamp();
    t.msgs.push({ who: 'me', ts, html: '<p>' + esc(q) + '</p>' }, { who: t.sp, ts, html: a.html, trail: a.trail, tags: a.tags }); t.updated = ts;
    render(); const c = $('#main .content'); if (c) c.scrollTop = 1e6;
  }
  function bind(r) {
    $$('[data-go]').forEach(b => b.onclick = e => { e.preventDefault(); go(b.dataset.go); });
    $$('[data-tab]').forEach(b => b.onclick = () => { S.catTab = b.dataset.tab; render(); });
    const q = $('#cat-q'); if (q) q.oninput = () => { S.catQ = q.value; const pos = q.selectionStart; render(); const q2 = $('#cat-q'); q2.focus(); q2.setSelectionRange(pos, pos); };
    $$('[data-add]').forEach(b => b.onclick = () => {
      const id = b.dataset.add;
      if (!isAdded(id)) { S.added.push(id); S.feed.push({ id: 'f' + Date.now(), who: 'system', ts: nowStamp(), text: `${SPX[id].name} joined the desk` }); toast(SPX[id].name + ' added to your desk'); }
      render();
    });
    $$('[data-update]').forEach(b => b.onclick = () => { const id = b.dataset.update; S.versions[id] = SPX[id].update; toast(SPX[id].name + ' updated to v' + SPX[id].update); render(); });
    const ua = $('#update-all'); if (ua) ua.onclick = () => { S.added.forEach(id => { if (SPX[id].update) S.versions[id] = SPX[id].update; }); toast('All specialists are up to date'); render(); };
    $$('[data-approve]').forEach(b => b.onclick = () => { S.approvals[b.dataset.approve] = 'approved'; toast('Posted to ' + S.slack); render(); });
    $$('[data-decline]').forEach(b => b.onclick = () => { S.approvals[b.dataset.decline] = 'declined'; toast('Declined. Nothing was sent.'); render(); });
    $$('.toggle').forEach(b => b.onclick = () => { const el = document.getElementById(b.dataset.t); if (el) el.classList.toggle('hidden'); });
    $$('[data-ask]').forEach(b => b.onclick = () => {
      if (b.dataset.thread) askInThread(S.threads[b.dataset.thread], b.dataset.ask);
      else { const id = addThread(b.dataset.sp, trim(b.dataset.ask.replace(/[?.]$/, ''), 44), b.dataset.ask); go('research/' + id); }
    });
    $$('[data-new-thread]').forEach(b => b.onclick = () => { const s = SPX[b.dataset.newThread]; const id = addThread(s.id, trim(s.tries[0].replace(/[?.]$/, ''), 44), s.tries[0]); go('research/' + id); });
    const ask = $('#ask'); if (ask) { const send = () => { const v = ask.value.trim(); if (v) askInThread(S.threads[r.id], v); }; $('#ask-send').onclick = send; ask.onkeydown = e => { if (e.key === 'Enter') send(); }; }
    const pin = $('#pin'); if (pin) pin.onclick = () => toast('Pinned (not saved in this prototype)');
    const ss = $('#sp-settings'); if (ss) ss.onclick = () => { S.settingsOpen = S.settingsOpen === r.id ? null : r.id; render(); };
    const c = $('#compose');
    if (c) {
      const send = () => {
        const v = c.value.trim(); if (!v) return;
        const m = v.match(/@([a-z-]+)/); const spId = m && SPX[m[1]] && isAdded(m[1]) ? m[1] : null;
        const post = { id: 'f' + Date.now(), who: 'me', ts: nowStamp(), text: v };
        S.feed.push(post);
        if (spId) { const a = answer(v.replace(/@[a-z-]+/, ''), spId); S.feed.push({ id: 'f' + (Date.now() + 1), who: spId, ts: nowStamp(), text: a.html, tags: a.tags, trail: a.trail }); }
        else post.reply = 'No specialist mentioned. Type @ to bring one in.';
        render();
      };
      $('#send').onclick = send;
      c.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } if (e.key === '@') $('#mentions').classList.remove('hidden'); if (e.key === 'Escape') $('#mentions').classList.add('hidden'); };
      $('#m-at').onclick = () => $('#mentions').classList.toggle('hidden');
      $$('#mentions button').forEach(b => b.onclick = () => { c.value = c.value.replace(/@[a-z-]*$/, '') + (c.value && !/[@\s]$/.test(c.value) ? ' ' : '') + HANDLE(b.dataset.m) + ' '; $('#mentions').classList.add('hidden'); c.focus(); });
      const p = $('#posts'); if (p) p.scrollTop = 1e6;
    }
    $$('[data-policy]').forEach(l => l.onclick = () => { S.policy[l.dataset.policy] = !S.policy[l.dataset.policy]; render(); });
    const sl = $('#slack'); if (sl) sl.onchange = () => { S.slack = sl.value.trim() || '#japan-desk'; render(); toast('Delivery channel saved'); };
    const rs = $('#reset'); if (rs) rs.onclick = () => { try { localStorage.removeItem(STORE); } catch (e) { } S = DEFAULT(); seed(S); go('desk'); render(); toast('Demo reset'); };
    $('#theme').onclick = () => { S.theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'; render(); };
    $('#open-search').onclick = openSearch;
  }

  /* ---- search palette */
  function openSearch() { $('#overlay').classList.add('show'); const i = $('#pal-q'); i.value = ''; i.focus(); searchResults(''); }
  function closeSearch() { $('#overlay').classList.remove('show'); }
  function searchResults(q) {
    q = q.toLowerCase(); const out = [];
    SP.forEach(s => { if (!q || (s.name + s.desc).toLowerCase().includes(q)) out.push(['Specialist', s.name, isAdded(s.id) ? 'On your desk' : '', 'specialists/' + s.id]); });
    Object.values(CO).forEach(c => { if (!q || (c.short + c.name_en + c.code).toLowerCase().includes(q)) out.push(['Company', c.short, c.code + (S.coverage.includes(c.code) ? ' · covered' : ''), 'company:' + c.code]); });
    S.threadOrder.forEach(id => { const t = S.threads[id]; if (!q || t.title.toLowerCase().includes(q)) out.push(['Thread', t.title, SPX[t.sp].name, 'research/' + id]); });
    NAV.forEach(s => { if (q && s[1].toLowerCase().includes(q)) out.push(['Page', s[1], '', s[0]]); });
    $('#pal-res').innerHTML = out.slice(0, 12).map(o => `<button data-pal="${o[3]}"><span class="k">${o[0]}</span>${esc(o[1])}<small>${esc(o[2])}</small></button>`).join('') || '<p class="empty">Nothing matches.</p>';
    $$('[data-pal]').forEach(b => b.onclick = () => {
      closeSearch(); const p = b.dataset.pal;
      if (p.startsWith('company:')) { const code = p.split(':')[1]; const id = addThread('cross-shareholding', CO[code].short + ' holders', 'Who still holds ' + CO[code].short + ' (' + code + '), and why?'); go('research/' + id); }
      else go(p);
    });
  }

  /* ---------------------------------------------------------------- boot */
  S = load();
  const ROOT = document.createElement('div'); ROOT.id = 'app'; document.body.appendChild(ROOT);
  ROOT.innerHTML = `<div class="shell"><aside class="nav" id="nav"></aside><div class="main" id="main"></div></div>
    <div class="overlay" id="overlay"><div class="palette"><input id="pal-q" placeholder="Search specialists, companies, threads"><div class="res" id="pal-res"></div></div></div><div class="toast" id="toast"></div>`;
  $('#overlay').onclick = e => { if (e.target === $('#overlay')) closeSearch(); };
  $('#pal-q').oninput = e => searchResults(e.target.value);
  document.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); } if (e.key === 'Escape') closeSearch(); });
  window.addEventListener('hashchange', render);
  if (!location.hash) location.hash = '#/desk';
  render();
})();
