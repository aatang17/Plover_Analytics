/* Investment Assistant — the live desk.
   Everything comes from /api/v1/assistant/…; nothing is computed here. Three
   states before a desk shows: the feature is not switched on (the API answers
   404), nobody is signed in (401), or a desk. */
(function () {
  'use strict';
  const API = '/api/v1/assistant';
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const root = $('#app');

  let me = null;          // {email}
  let desk = null;        // /desk payload
  let theme = null;
  try { theme = localStorage.getItem('ia-theme'); } catch (e) { }

  /* ------------------------------------------------------------ helpers */
  function when(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000); const now = new Date();
    const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    if (d.toDateString() === now.toDateString()) return hm;
    return d.getDate() + ' ' + MON[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : '') + ' ' + hm;
  }
  const n = v => v == null ? '—' : Number(v).toLocaleString('en');
  let toastT;
  function toast(msg) { const t = $('#toast'); if (!t) return; t.textContent = msg; t.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), 2600); }

  async function api(path, opts) {
    const o = Object.assign({ headers: { 'Accept': 'application/json' }, credentials: 'same-origin' }, opts || {});
    if (o.body && typeof o.body !== 'string') { o.body = JSON.stringify(o.body); o.headers['Content-Type'] = 'application/json'; }
    const r = await fetch(API + path, o);
    if (r.status === 404 && path === '/status') throw { off: true };
    if (r.status === 401) throw { signin: true };
    let data = null;
    try { data = await r.json(); } catch (e) { }
    if (!r.ok) throw { status: r.status, detail: (data && data.detail) || r.statusText };
    return data;
  }

  // Light rendering of a model's reply: paragraphs, bullets, **bold**, `code`,
  // and pipe tables. Everything is escaped first.
  function md(text) {
    const lines = String(text || '').split(/\r?\n/);
    const out = []; let para = []; let list = []; let table = [];
    // [text](url) links are set aside first so the bare-URL rule cannot touch them
    const inline = s => {
      const links = [];
      const t = esc(s).replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, txt, url) => { links.push(`<a href="${url}" target="_blank" rel="noopener">${txt}</a>`); return '\u0000' + (links.length - 1) + '\u0000'; })
        .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/(https?:\/\/[^\s)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
      return t.replace(/\u0000(\d+)\u0000/g, (m, i) => links[+i]);
    };
    const flush = () => {
      if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; }
      if (list.length) { out.push('<ul>' + list.map(l => '<li>' + inline(l) + '</li>').join('') + '</ul>'); list = []; }
      if (table.length) {
        const rows = table.filter(r => !/^\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
        if (rows.length) out.push('<table><thead><tr>' + rows[0].map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>' + rows.slice(1).map(r => '<tr>' + r.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') + '</tbody></table>');
        table = [];
      }
    };
    lines.forEach(l => {
      const t = l.trim();
      if (!t) { flush(); return; }
      if (/^\|/.test(t)) { if (para.length || list.length) flush(); table.push(t); return; }
      if (/^[-*•]\s+/.test(t)) { if (para.length || table.length) flush(); list.push(t.replace(/^[-*•]\s+/, '')); return; }
      if (/^#{1,6}\s/.test(t)) { flush(); out.push('<p><b>' + inline(t.replace(/^#+\s/, '')) + '</b></p>'); return; }
      if (list.length || table.length) flush();
      para.push(t);
    });
    flush();
    return '<div class="md">' + out.join('') + '</div>';
  }

  /* ------------------------------------------------------------- charts
     A chart's numbers were read by the server from a data result of the same
     run (app/assistant/charts.py); nothing here computes a value. Display
     scaling (¥ into ¥bn or ¥tn) changes the axis labels only: the CSV carries
     the values exactly as the tool returned them. */
  const chartSpecs = {};
  const mounted = [];

  function yenScale(spec) {
    if (!/¥/.test(spec.unit) || /per|\//.test(spec.unit)) return null;
    let max = 0;
    spec.series.forEach(s => s.points.forEach(p => { if (p[1] != null) max = Math.max(max, Math.abs(p[1])); }));
    if (max >= 1e12) return { div: 1e12, label: '¥tn' };
    if (max >= 1e9) return { div: 1e9, label: '¥bn' };
    if (max >= 1e6) return { div: 1e6, label: '¥mn' };
    return null;
  }
  function xLabel(spec, x) {
    if (spec.fiscal) return fmtFiscalYear(x);
    if (/^\d{4}-\d{2}-\d{2}/.test(x)) return spec.daily ? x.slice(0, 10) : fmtPeriod(x);
    return x;
  }
  function chartCfg(spec) {
    const sc = yenScale(spec);
    const k = sc ? sc.div : 1;
    const vals = [];
    const series = spec.series.map((s, i) => ({ name: s.name, slot: i + 1,
      points: s.points.map(p => { const v = p[1] == null ? null : p[1] / k; if (v != null) vals.push(v); return [p[0], v]; }) }));
    // one precision per chart: a rate or a scaled amount to one decimal,
    // whole numbers where every value is whole
    const dp = sc || spec.unit === '%' || spec.unit === 'index' ? 1 : (vals.every(v => Number.isInteger(v)) ? 0 : 2);
    const unitLabel = sc ? sc.label : spec.unit;
    const official = spec.series.every(s => s.trust === 'official');
    const cfg = { series, dp, trust: official ? 'official' : null,
      unit: spec.unit === '%' ? '%' : 'x',
      unitSuffix: spec.unit === '%' || spec.unit === 'index' ? '' : unitLabel,
      yAxisName: spec.unit === 'index' ? 'Index' : unitLabel,
      yAxisDp: dp, isoPeriods: spec.daily, sourceLine: sourceLine(spec),
      // at phone width the legend sits under the plot, one long name a row;
      // reserve that room or it prints over the axis labels
      legendBottomNarrow: 34 + 20 * (spec.series.some(s => s.name.length > 18) ? spec.series.length : Math.ceil(spec.series.length / 2)) };
    if (spec.kind === 'bar') {
      const xs = Array.from(new Set(spec.series.reduce((a, s) => a.concat(s.points.map(p => p[0])), []))).sort();
      cfg.categories = xs.map(x => xLabel(spec, x));
      cfg.series = series.map(s => { const m = {}; s.points.forEach(p => { m[p[0]] = p[1]; });
        return { name: s.name, slot: s.slot, points: xs.map(x => m[x] === undefined ? null : m[x]) }; });
      return { kind: 'cols', cfg };
    }
    if (spec.axis === 'category') {
      cfg.xType = 'category';
      cfg.series = series.map(s => ({ name: s.name, slot: s.slot, points: s.points.map(p => [xLabel(spec, p[0]), p[1]]) }));
    }
    return { kind: 'line', cfg };
  }
  function sourceLine(spec) {
    const credits = Array.from(new Set(spec.sources.map(s => s.credit).filter(Boolean)));
    const vint = Array.from(new Set(spec.sources.map(s => s.vintage).filter(Boolean)));
    return credits.concat(vint).join(' · ') + (credits.length ? ' · ' : '') + 'Plover Analytics';
  }
  function chartCSV(spec) {
    const q = v => '"' + String(v).replace(/"/g, '""') + '"';
    const head = [spec.title, 'Unit: ' + (spec.unit || 'none') + ' (values exactly as returned, not rescaled)'];
    spec.sources.forEach(s => {
      head.push('Source: ' + [(s.credit || '').replace(/^Source:\s*/, '').replace(/\.$/, ''), s.document].filter(Boolean).join(' — '));
      if (s.vintage) head.push('Vintage: ' + s.vintage + (s.as_of ? ' (published ' + String(s.as_of).slice(0, 10) + ')' : ''));
      if (s.cite) head.push('Cite: ' + s.cite);
    });
    spec.series.forEach(s => head.push(s.name + ': ' + (s.trust === 'official' ? 'Official Statistic' : 'Calculated — ' + (s.formula || 'formula on the dataset card'))));
    head.push('Missing values are blank, never zero.');
    const xs = Array.from(new Set(spec.series.reduce((a, s) => a.concat(s.points.map(p => p[0])), []))).sort();
    const cols = spec.series.map(s => { const m = {}; s.points.forEach(p => { m[p[0]] = p[1]; }); return m; });
    let csv = head.map(l => '# ' + l).join('\n') + '\n' + ['period'].concat(spec.series.map(s => q(s.name))).join(',') + '\n';
    xs.forEach(x => { csv += x + ',' + cols.map(m => m[x] == null ? '' : m[x]).join(',') + '\n'; });
    return csv;
  }
  function slug(t) { return String(t).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'chart'; }

  function chartsHtml(list, key) {
    if (!list || !list.length) return '';
    return list.map((c, i) => {
      const id = 'ch-' + key + '-' + i;
      chartSpecs[id] = c;
      const derived = c.series.filter(s => s.trust !== 'official');
      const badge = derived.length ? '' : trustBadge('official');
      const calc = derived.length ? `<details class="calc"><summary>Show calculation</summary>${derived.map(s => `<p><b>${esc(s.name)}</b>: <code>${esc(s.formula || 'see the dataset card')}</code></p>`).join('')}</details>` : '';
      const src = c.sources.map(s => [(s.credit || '').replace(/\.$/, ''), s.document].filter(Boolean).map(esc).join(' — ') +
        (s.vintage ? ' · ' + esc(s.vintage) : '') + (s.cite ? ` · <a href="${esc(s.cite)}" target="_blank" rel="noopener">View on Plover</a>` : '')).join('<br>');
      return `<figure class="iachart"><figcaption><b>${esc(c.title)}</b>${badge}</figcaption>
        <div class="iachart-plot" id="${id}"></div>
        <div class="iachart-foot"><div class="iachart-src">${src}</div>
        <div class="iachart-acts"><button class="linkbtn" id="${id}-png">Export image ▾</button><button class="linkbtn" data-csv="${id}">Download CSV</button></div></div>${calc}</figure>`;
    }).join('');
  }
  function mountCharts() {
    while (mounted.length) { try { mounted.pop().dispose(); } catch (e) { } }
    $$('.iachart-plot').forEach(el => {
      const spec = chartSpecs[el.id];
      if (!spec || typeof obsChart !== 'function') return;
      const o = chartCfg(spec);
      const ch = obsChart(el, o.kind, o.cfg);
      mounted.push(ch);
      const png = document.getElementById(el.id + '-png');
      if (png) png.onclick = () => ch.exportPNG(slug(spec.title) + '.png');
    });
    $$('[data-csv]').forEach(b => b.onclick = () => {
      const spec = chartSpecs[b.dataset.csv]; if (!spec) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([chartCSV(spec)], { type: 'text/csv' }));
      a.download = slug(spec.title) + '.csv'; a.click(); URL.revokeObjectURL(a.href);
    });
  }

  function trailHtml(calls, id) {
    if (!calls || !calls.length) return '';
    return `<div class="prov"><button class="linkbtn toggle" data-t="t-${id}">${calls.length} tool call${calls.length > 1 ? 's' : ''}</button></div>` +
      `<div class="trail hidden" id="t-${id}">${calls.map(c => `<div><span class="n">${c.seq}</span><span class="fn">${esc(c.name)}</span><span class="args">${esc(JSON.stringify(c.args))}</span><span class="ms">${c.error ? 'error' : c.ms + ' ms'}</span></div>`).join('')}</div>`;
  }
  const refsHtml = refs => (refs && refs.length) ? `<div class="prov">${refs.map(r => refChip(r)).join('')}</div>` : '';
  // A source as a short chip: a cite URL shows its page and query, not the host.
  function refChip(r) {
    const s = String(r || '').trim();
    if (/^https?:\/\//.test(s)) {
      let label = s;
      try { const u = new URL(s); label = (u.pathname.replace(/^\//, '') + u.search) || u.host; } catch (e) { }
      return `<a class="src" href="${esc(s)}" target="_blank" rel="noopener">${esc(label)}</a>`;
    }
    return `<span class="src">${esc(s)}</span>`;
  }
  // One quiet line under a post: sources and tool calls, both folded.
  function metaHtml(refs, calls, id) {
    const parts = [];
    const uniq = Array.from(new Set((refs || []).map(r => String(r).trim()).filter(Boolean)));
    if (uniq.length) parts.push(`<button class="linkbtn toggle" data-t="s-${id}">${uniq.length} source${uniq.length > 1 ? 's' : ''}</button>`);
    if (calls && calls.length) parts.push(`<button class="linkbtn toggle" data-t="t-${id}">${calls.length} tool call${calls.length > 1 ? 's' : ''}</button>`);
    if (!parts.length) return '';
    return `<div class="prov">${parts.join('')}</div>` +
      (uniq.length ? `<div class="srcs hidden" id="s-${id}">${uniq.map(refChip).join('')}</div>` : '') +
      (calls && calls.length ? `<div class="trail hidden" id="t-${id}">${calls.map(c => `<div><span class="n">${c.seq}</span><span class="fn">${esc(c.name)}</span><span class="args">${esc(JSON.stringify(c.args))}</span><span class="ms">${c.error ? 'error' : c.ms + ' ms'}</span></div>`).join('')}</div>` : '');
  }
  // "TOYOTA MOTOR CORPORATION" -> "Toyota Motor"; "Mizuho Financial Group, Inc." -> "Mizuho Financial Group"
  function prettyName(name) {
    let s = String(name || '').replace(/,?\s*\b(Incorporated|Inc\.|Co\.,\s*Ltd\.|Ltd\.|Limited|CORPORATION|Corporation|Holdings, Inc\.)\s*$/i, '').replace(/[,\s]+$/, '').trim();
    if (s && s === s.toUpperCase()) s = s.toLowerCase().replace(/\b[a-z]/g, c => c.toUpperCase());
    return s || name;
  }
  // Who wrote a post: a specialist on the desk, or an outside connection.
  function authorOf(p) {
    if (String(p.author).startsWith('ext:')) {
      const label = p.author.slice(4);
      const ini = label.split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || 'EX';
      return { name: label, initials: ini, ext: true };
    }
    const sp = specOf(hireBySlug(p.author));
    return { name: sp.name || p.author, initials: sp.initials || '?' };
  }
  const specOf = h => (h && h.spec) || {};
  const hireBySlug = slug => (desk.hires || []).find(h => h.slug === slug);
  const hireById = id => (desk.analyst && desk.analyst.id === Number(id)) ? desk.analyst : (desk.hires || []).find(h => h.id === Number(id));

  /* ------------------------------------------------------------- icons */
  const I = d => `<svg viewBox="0 0 24 24">${d}</svg>`;
  const ICONS = {
    inbox: I('<path d="M3 13l2.5-8h13L21 13v6H3z"/><path d="M3 13h5l1.5 2.5h5L16 13h5"/>'),
    desk: I('<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>'),
    specialists: I('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.5c3 0 6 2 6 5.5"/>'),
    research: I('<path d="M4 5h16v11H9l-5 4z"/>'),
    chat: I('<path d="M3.5 4.5h12v8.5H8l-4.5 3.5z"/><path d="M8.5 16.5v.5H16l4.5 3.5V9h-5"/>'),
    close: I('<path d="M6 6l12 12M18 6L6 18"/>'),
    expand: I('<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/>'),
    calendar: I('<rect x="3.5" y="5" width="17" height="15" rx="1"/><path d="M3.5 10h17M8 3v4M16 3v4"/>'),
    files: I('<path d="M3 6h6l2 2h10v11H3z"/>'),
    audit: I('<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>'),
    settings: I('<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>'),
    theme: I('<path d="M12 3a9 9 0 1 0 9 9c-5 0-9-4-9-9z"/>'),
    plus: I('<path d="M12 5v14M5 12h14"/>'),
    check: I('<path d="M5 12.5l4 4 10-10"/>'),
    back: I('<path d="M15 5l-7 7 7 7"/>'),
    coverage: I('<path d="M4 6h10M4 12h10M4 18h7"/><circle cx="18" cy="16" r="3"/><path d="M20.2 18.2L22 20"/>'),
    tools: I('<path d="M9 3v6M15 3v6M7.5 9h9v4a4.5 4.5 0 0 1-9 0z"/><path d="M12 17.5V21"/>'),
    keys: I('<circle cx="8" cy="12" r="3.5"/><path d="M11.5 12H21M18 12v3M21 12v2.5"/>'),
    data: I('<path d="M4 19h16"/><path d="M5 15l4-5 4 3 6-8"/>'),
    memory: I('<path d="M6 4h12v16l-6-4-6 4z"/>'),
    search: I('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>'),
    drive: I('<path d="M3.5 14l2.8-8.5h11.4L20.5 14v5h-17z"/><path d="M3.5 14h17M7 16.5h.01M10 16.5h.01"/>'),
    shared: I('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M17 11v6M14 14h6"/>'),
    clock: I('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    trash: I('<path d="M4 6.5h16M9.5 6.5V4h5v2.5M6 6.5l1 13.5h10l1-13.5M10 10.5v6M14 10.5v6"/>'),
    cloud: I('<ellipse cx="12" cy="6" rx="7.5" ry="2.5"/><path d="M4.5 6v12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5V6M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>'),
    gdrive: I('<path d="M8.5 4h7l6 10.5-3.5 5.5h-12L2.5 14.5z"/><path d="M8.5 4l6 10.5h7M15.5 4l-9.5 16M2.5 14.5h12"/>'),
    folder: I('<path d="M3 6.5h6l2 2h10V19H3z"/>'),
    folderplus: I('<path d="M3 6.5h6l2 2h10V19H3z"/><path d="M12 11.5v5M9.5 14h5"/>'),
    file: I('<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'),
    upload: I('<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>'),
    studies: I('<path d="M9 3h6M10 3v6l-5.2 9.2A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.8L14 9V3"/><path d="M7.5 15h9"/>')
  };
  const NAV = [['inbox', 'Inbox'], ['desk', 'Desk'], ['chat', 'Chat'], ['coverage', 'Coverage'], ['calendar', 'Calendar'], ['specialists', 'Specialists'], ['research', 'Research'], ['studies', 'Studies'], ['files', 'Files'], ['drive', 'Drive']];
  const SYSTEM_NAV = [['data', 'Data sources'], ['memory', 'Memory'], ['tools', 'MCP tools'], ['keys', 'API keys'], ['audit', 'Audit'], ['settings', 'Settings']];

  /* ------------------------------------------------------------ routing */
  function route() { const p = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean); return { sec: p[0] || 'desk', id: p[1] ? decodeURIComponent(p[1]) : null, sub: p.slice(2).map(decodeURIComponent).join('/') || null }; }
  const go = path => { location.hash = '#/' + path; };

  function shell(navHtml, mainHtml) {
    document.documentElement.setAttribute('data-theme', theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
    root.innerHTML = `<div class="shell"><aside class="nav" id="nav">${navHtml}</aside><div class="main" id="main">${mainHtml}</div></div><div class="toast" id="toast"></div>`;
  }
  function navHtml(r) {
    const pending = desk ? desk.pending_approvals : 0;
    return `<div class="brand"><span class="logo">PA</span><div><b>Investment Assistant</b><small>${esc(me ? me.email : '')}</small></div></div>
      <div class="navsec first">Workspace</div><div class="navlist">${NAV.map(([k, l]) => `<button class="navitem ${r.sec === k ? 'on' : ''}" data-go="${k}">${ICONS[k]}<span>${l}</span>${k === 'inbox' && pending ? `<em class="count">${pending}</em>` : ''}</button>`).join('')}</div>
      <div class="navsec">System</div>
      <div class="navlist">${SYSTEM_NAV.map(([k, l]) => `<button class="navitem ${r.sec === k ? 'on' : ''}" data-go="${k}">${ICONS[k]}<span>${l}</span></button>`).join('')}</div>
      <div class="navsec">Your specialists</div>
      <div class="navlist mine">${(desk.hires || []).map(h => `<button class="navitem ${r.sec === 'specialists' && String(r.id) === String(h.id) ? 'on' : ''}" data-go="specialists/${h.id}"><span class="mini">${esc(specOf(h).initials || '?')}</span><span>${esc(specOf(h).name || h.slug)}</span></button>`).join('')}
        <button class="navitem add" data-go="specialists">${ICONS.plus}<span>Add a specialist</span></button></div>
      <div class="navfoot"><span class="av sm me">${esc((me.email || 'A')[0].toUpperCase())}</span><span><a href="/">Plover Analytics</a></span><button id="theme" title="Switch light / dark">${ICONS.theme}</button></div>`;
  }
  const topbar = (crumbs, extra) => `<header class="topbar"><nav class="crumbs">${crumbs.map((c, i) => (i ? '<span class="sep">/</span>' : '') + (c[1] ? `<a href="#/${c[1]}">${esc(c[0])}</a>` : `<b>${esc(c[0])}</b>`)).join('')}</nav><div class="grow"></div>${extra || ''}</header>`;
  const pageHead = (title, sub, actions) => `<div class="pagehead"><div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div>${actions ? `<div class="actions">${actions}</div>` : ''}</div>`;
  // A row of headline counts: one bordered strip, a cell per number.
  const stat = (label, value, sub, cls) => `<div class="stat${cls ? ' ' + cls : ''}"><span class="lbl">${label}</span><b>${value}</b><small>${sub}</small></div>`;
  const working = label => `<div class="working"><i></i>${esc(label || 'Working. This can take a minute.')}</div>`;

  function bindCommon() {
    $$('[data-go]').forEach(b => b.onclick = e => { e.preventDefault(); go(b.dataset.go); });
    $$('.toggle').forEach(b => b.onclick = () => { const el = document.getElementById(b.dataset.t); if (el) el.classList.toggle('hidden'); });
    const t = $('#theme'); if (t) t.onclick = () => { theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('ia-theme', theme); } catch (e) { } render(); };
    mountCharts();
  }

  /* ------------------------------------------------------------- states */
  function offState() {
    root.innerHTML = `<div class="state"><h1>Investment Assistant</h1><p>The desk is not switched on for this deployment.</p><a class="btn" href="/">Back to Plover Analytics</a></div>`;
  }
  function signinState() {
    root.innerHTML = `<div class="state"><h1>Investment Assistant</h1><p>Sign in to open your desk. There is no password: you get a single-use link by email.</p><a class="btn primary" href="signin.html?returnTo=${encodeURIComponent('/assistant.html' + location.hash)}">Sign in</a></div>`;
  }
  function errorState(e) {
    root.innerHTML = `<div class="state"><h1>Something went wrong</h1><p>${esc(e && e.detail || 'The desk could not be loaded.')}</p><button class="btn" onclick="location.reload()">Try again</button></div>`;
  }

  /* -------------------------------------------------------------- views */
  async function render() {
    const r = route();
    try {
      const st = await api('/status');
      if (!st.signed_in) return signinState();
      me = { email: st.email };
      desk = await api('/desk');
    } catch (e) {
      if (e.off) return offState();
      if (e.signin) return signinState();
      return errorState(e);
    }
    const views = { data: vData, memory: vMemory, tools: vTools, keys: vKeys, inbox: vInbox, desk: vDesk, coverage: vCoverage, calendar: vCalendar, specialists: r.id ? vSpecialist : vSpecialists, chat: vChat, research: r.id ? vThread : vResearch, studies: r.id ? vStudy : vStudies, files: vFiles, drive: vDrive, audit: vAudit, settings: vSettings };
    shell(navHtml(r), '<div class="content"><div class="page">' + working('Loading') + '</div></div>');
    bindCommon();
    try { await (views[r.sec] || vDesk)(r); } catch (e) { if (e.signin) return signinState(); console.error(e); $('#main').innerHTML = topbar([['Error']]) + `<div class="content"><div class="page"><p class="err">${esc(e.detail || e.message || 'Could not load this page.')}</p></div></div>`; }
    await renderDock(r);
    bindCommon();
  }

  function setupNotice() {
    const d = desk.desk;
    if (!desk.keychain) return `<div class="notice">Keys cannot be stored on this server yet: <code>ASSISTANT_SECRET</code> is not set. Runs will not start until it is.</div>`;
    if (!d.model_provider || !d.model_key_set) return `<div class="notice">No model key yet. Specialists run on your own key. <a href="#/settings">Add one in Settings</a>.</div>`;
    return '';
  }

  /* ---- Coverage editor, used on the Desk and on Coverage Monitor's page */
  // The add-a-company search. listId 0 means the main Coverage list.
  function searchBox(id, listId, placeholder, primary) {
    return `<div class="covsearch big">${ICONS.search}<input id="${id}" placeholder="${esc(placeholder || 'Add a company: search by name or code, e.g. Toyota, トヨタ, 7203, MUFG')}" autocomplete="off" spellcheck="false"><button class="btn${primary ? ' primary' : ''}" data-covadd="${id}" data-list="${listId || 0}">Add</button><div class="covmenu hidden" id="${id}-menu" role="listbox"></div></div>`;
  }
  // Used in the Get started card: search on top, what is on the list below, read-only.
  function coverageEditor(id, primary) {
    return `${searchBox(id, 0, 'Search by name or code, e.g. Toyota, トヨタ, 7203, MUFG', primary)}<div class="covlist ro">${desk.coverage.map(c => `<span class="chip ro">${esc(prettyName(c.name) || c.sec_code)} <span class="muted">${esc(c.sec_code)}</span></span>`).join('')}</div>`;
  }
  function bindCoverage() {
    $$('[data-covadd]').forEach(b => {
      const input = document.getElementById(b.dataset.covadd);
      const menu = document.getElementById(b.dataset.covadd + '-menu');
      let hits = [], active = -1, timer = null, seq = 0;
      const close = () => { menu.classList.add('hidden'); active = -1; };
      const draw = () => {
        if (!hits.length) { menu.innerHTML = input.value.trim().length >= 1 ? '<div class="covnone">No company matches.</div>' : ''; menu.classList.toggle('hidden', !input.value.trim()); return; }
        menu.innerHTML = hits.map((c, i) => `<button type="button" role="option" class="${i === active ? 'on' : ''}" data-i="${i}" ${c.covered ? 'disabled' : ''}><span class="cc">${esc(c.code)}</span><span class="cn">${esc(prettyName(c.name_en) || c.name_ja || '')}</span>${c.name_ja && c.name_en ? `<span class="cj">${esc(c.name_ja)}</span>` : ''}${c.covered ? '<span class="cd">Added</span>' : ''}</button>`).join('');
        menu.classList.remove('hidden');
        $$('button[data-i]', menu).forEach(o => o.onmousedown = ev => { ev.preventDefault(); pick(hits[+o.dataset.i]); });
      };
      const search = () => {
        const q = input.value.trim(); const mine = ++seq;
        if (!q) { hits = []; close(); return; }
        api('/companies?q=' + encodeURIComponent(q) + (+b.dataset.list ? '&list_id=' + b.dataset.list : '')).then(r => { if (mine !== seq) return; hits = r.companies || []; active = hits.findIndex(c => !c.covered); draw(); }).catch(() => {});
      };
      const pick = async c => {
        if (!c || c.covered) return;
        close(); b.disabled = true; input.disabled = true;
        try { await api('/coverage', { method: 'POST', body: { code: c.code, list_id: +b.dataset.list || 0 } }); toast('Added ' + (prettyName(c.name_en) || c.code)); render(); }
        catch (e) { toast(e.detail || 'Could not add'); b.disabled = false; input.disabled = false; }
      };
      // Add picks the highlighted match; a bare four-character code works too.
      const add = () => {
        if (active >= 0 && hits[active]) return pick(hits[active]);
        const v = input.value.trim().toUpperCase();
        const exact = hits.find(c => c.code === v);
        if (exact) return pick(exact);
        if (/^[0-9A-Z]{4}$/.test(v)) return pick({ code: v, name_en: v });
        if (input.value.trim()) toast('Choose a company from the list');
      };
      input.oninput = () => { clearTimeout(timer); timer = setTimeout(search, 120); };
      input.onkeydown = e => {
        if (e.key === 'ArrowDown' && hits.length) { e.preventDefault(); active = Math.min(hits.length - 1, active + 1); draw(); }
        else if (e.key === 'ArrowUp' && hits.length) { e.preventDefault(); active = Math.max(0, active - 1); draw(); }
        else if (e.key === 'Enter') { e.preventDefault(); add(); }
        else if (e.key === 'Escape') close();
      };
      input.onblur = () => setTimeout(close, 120);
      input.onfocus = () => { if (hits.length) draw(); };
      b.onclick = add;
    });
  }

  /* ---- Coverage lists page */
  let covUi = { mode: null };   // null | 'new' | 'rename' | 'delete'
  async function vCoverage(r) {
    const data = await api('/lists');
    const all = data.lists;
    const cur = all.find(l => String(l.id) === String(r.id)) || all.find(l => l.is_default) || all[0];
    const rows = (await api('/coverage?list_id=' + cur.id)).coverage;
    const watchers = desk.hires.filter(h => {
      const lid = data.watched_by[h.id];
      return lid ? lid === cur.id : cur.is_default && (specOf(h).tools || []).includes('my_coverage');
    }).map(h => specOf(h).name || h.slug);
    const fmtDate = ts => { const d = new Date(ts * 1000); return d.getDate() + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear(); };
    const tabs = `<nav class="ltabs">${all.map(l => `<a class="${l.id === cur.id ? 'on' : ''}" href="#/coverage/${l.id}">${esc(l.name)}<span>${l.count}</span></a>`).join('')}<button class="ltab-new" id="l-new">${ICONS.plus} New list</button></nav>`;
    let bar = '';
    if (covUi.mode === 'new') bar = `<div class="lbar"><input id="l-name" placeholder="List name, e.g. Banks or Activist targets" maxlength="60"><button class="btn primary" id="l-create">Create list</button><button class="btn" data-lcancel>Cancel</button></div>`;
    if (covUi.mode === 'rename') bar = `<div class="lbar"><input id="l-name" value="${esc(cur.name)}" maxlength="60"><button class="btn primary" id="l-rename">Save name</button><button class="btn" data-lcancel>Cancel</button></div>`;
    if (covUi.mode === 'delete') bar = `<div class="lbar warn"><span>Delete the list <b>${esc(cur.name)}</b>${cur.count ? ` and its ${cur.count} compan${cur.count === 1 ? 'y' : 'ies'}` : ''}? Specialists watching it go back to the main list. This cannot be undone.</span><button class="btn danger" id="l-delete">Delete list</button><button class="btn" data-lcancel>Cancel</button></div>`;
    const table = rows.length ? `<table class="t covtable"><thead><tr><th style="width:72px">Code</th><th>Company</th><th>Japanese name</th><th style="width:120px">Added</th><th style="width:150px"></th></tr></thead><tbody>
      ${rows.map(c => `<tr data-code="${esc(c.sec_code)}"><td><span class="code">${esc(c.sec_code)}</span></td><td class="ink">${esc(prettyName(c.name) || c.sec_code)}</td><td class="muted">${esc(c.name_ja || '')}</td><td class="muted nowrap">${fmtDate(c.added_at)}</td><td class="right rowact"><a class="linkbtn" href="company.html?code=${encodeURIComponent(c.sec_code)}" target="_blank" rel="noopener">Profile</a><button class="linkbtn quiet" data-rm="${esc(c.sec_code)}">Remove</button></td></tr>`).join('')}</tbody></table>`
      : `<div class="covempty"><b>This list is empty.</b><span>Search above to add the first company.</span></div>`;
    $('#main').innerHTML = topbar(cur.is_default ? [['Coverage']] : [['Coverage', 'coverage'], [cur.name]]) + `<div class="content"><div class="page">
      ${pageHead('Coverage', 'The companies your specialists watch. Keep your main list here, and group names into your own lists.')}
      ${tabs}${bar}
      <section class="panel covpanel">
        <header class="covhead"><div><b>${esc(cur.name)}</b><small>${cur.count} compan${cur.count === 1 ? 'y' : 'ies'}${cur.is_default ? ' · main list' : ''}${watchers.length ? ' · watched by ' + watchers.map(esc).join(', ') : ' · not watched by any specialist'}</small></div>
          <div class="covtools">${cur.is_default ? '' : `<button class="linkbtn" id="l-ren">Rename</button><button class="linkbtn quiet" id="l-del">Delete list</button>`}</div></header>
        <div class="covbody">${searchBox('cv-code', cur.id)}</div>
        ${table}
      </section></div></div>`;
    bindCoverage();
    // two-step removal: Remove -> Confirm / Cancel on the same row
    $$('[data-rm]').forEach(b => b.onclick = () => {
      const cell = b.parentElement; const code = b.dataset.rm;
      cell.innerHTML = `<span class="muted small">Remove?</span> <button class="btn sm danger" data-rmok="${esc(code)}">Confirm</button> <button class="btn sm" data-rmno>Cancel</button>`;
      cell.querySelector('[data-rmno]').onclick = () => vCoverage(r).then(bindCommon);
      cell.querySelector('[data-rmok]').onclick = async () => { await api('/coverage/' + code + '?list_id=' + cur.id, { method: 'DELETE' }); toast('Removed ' + code + ' from ' + cur.name); render(); };
    });
    const set = m => { covUi.mode = m; vCoverage(r).then(() => { bindCommon(); const i = $('#l-name'); if (i) { i.focus(); i.select(); } }); };
    $('#l-new').onclick = () => set('new');
    const ren = $('#l-ren'); if (ren) ren.onclick = () => set('rename');
    const del = $('#l-del'); if (del) del.onclick = () => set('delete');
    $$('[data-lcancel]').forEach(b => b.onclick = () => set(null));
    const create = $('#l-create');
    if (create) { const go2 = async () => { try { const l = await api('/lists', { method: 'POST', body: { name: $('#l-name').value } }); covUi.mode = null; toast('List created'); go('coverage/' + l.id); } catch (e) { toast(e.detail || 'Could not create'); } }; create.onclick = go2; $('#l-name').onkeydown = e => { if (e.key === 'Enter') go2(); if (e.key === 'Escape') set(null); }; }
    const rn = $('#l-rename');
    if (rn) { const save = async () => { try { await api('/lists/' + cur.id, { method: 'PATCH', body: { name: $('#l-name').value } }); covUi.mode = null; render(); } catch (e) { toast(e.detail || 'Could not rename'); } }; rn.onclick = save; $('#l-name').onkeydown = e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') set(null); }; }
    const dl = $('#l-delete');
    if (dl) dl.onclick = async () => { try { await api('/lists/' + cur.id, { method: 'DELETE' }); covUi.mode = null; toast('List deleted'); go('coverage'); } catch (e) { toast(e.detail || 'Could not delete'); } };
  }

  /* ---- Getting started: four steps, shown on the Desk until all are done.
     Coverage Monitor comes first because every other specialist reads the
     coverage list it keeps. */
  function gettingStarted() {
    const cm = hireBySlug('coverage-monitor');
    const hasCov = desk.coverage.length > 0;
    const hasKey = !!(desk.desk.model_provider && desk.desk.model_key_set);
    const ran = cm && (desk.runs || []).some(r => r.hire_id === cm.id && (r.outcome === 'done' || r.outcome === 'approval_waiting'));
    if (cm && hasCov && hasKey && ran) return '';
    // One step is current: the first not yet done. It alone is open and
    // carries the page's filled button. Finished steps fold to one line with
    // a way back; later steps show what they will do, with nothing to press.
    const done = [!!cm, hasCov, hasKey, !!ran];
    const cur = done.indexOf(false);
    const PROVIDER = { anthropic: 'Anthropic', openai: 'OpenAI', deepseek: 'DeepSeek' };
    const names = desk.coverage.slice(0, 4).map(c => prettyName(c.name) || c.sec_code);
    const steps = [
      ['Add Coverage Monitor', 'Added',
        'It keeps the list of companies you follow and tells you when any of them files something new. Every other specialist reads this list.',
        `<button class="btn primary" id="gs-hire">${ICONS.plus} Add Coverage Monitor</button>`],
      ['Choose the companies you cover',
        `${desk.coverage.length} compan${desk.coverage.length === 1 ? 'y' : 'ies'}: ${esc(names.join(', '))}${desk.coverage.length > 4 ? ' and more' : ''} · <a href="#/coverage">Add more</a>`,
        'Search by name or code and add each company. You can change the list any time on the Coverage page.',
        coverageEditor('gs-code', true)],
      ['Add your model key', `${esc(PROVIDER[desk.desk.model_provider] || 'Model')} key stored · <a href="#/settings">Change</a>`,
        'Specialists run on your own Anthropic, OpenAI or DeepSeek key.',
        desk.keychain ? `<div class="gs-key"><select id="gs-prov" aria-label="Provider">${Object.keys(PROVIDER).map(k => `<option value="${k}" ${k === (desk.desk.model_provider || 'anthropic') ? 'selected' : ''}>${PROVIDER[k]}</option>`).join('')}</select><input id="gs-key" type="password" placeholder="Paste your API key" autocomplete="off" spellcheck="false"><button class="btn primary" id="gs-savekey">Save key</button></div><p class="gs-msg" id="gs-keymsg">The key is stored encrypted and only used for your specialists' runs.</p>`
          : '<p class="err">This server cannot store keys yet: ASSISTANT_SECRET is not set.</p>'],
      ['Run it once', 'Done',
        'It reads the latest filings for each company and posts what it finds to #desk.',
        `<button class="btn primary" id="gs-run">Run Coverage Monitor</button><span class="muted small" id="gs-msg"></span>`],
    ];
    const li = (st, i) => {
      if (done[i]) return `<li class="gs-step done"><span class="gs-n">${ICONS.check}</span><div class="gs-line"><b>${st[0]}</b><span>${st[1]}</span></div></li>`;
      if (i === cur) return `<li class="gs-step now"><span class="gs-n">${i + 1}</span><div><b>${st[0]}</b><div class="gs-body"><p>${st[2]}</p>${st[3]}</div></div></li>`;
      return `<li class="gs-step later"><span class="gs-n">${i + 1}</span><div><b>${st[0]}</b><p>${st[2]}</p></div></li>`;
    };
    return `<section class="gs"><header><b>Get started</b><span class="muted small">Set up your first specialist. It takes about a minute.</span><span class="grow"></span><span class="gs-count">Step ${cur + 1} of 4</span></header><ol>${steps.map(li).join('')}</ol></section>`;
  }
  function bindGettingStarted() {
    // The key is saved and checked in place, so setup never leaves the Desk.
    const sk = $('#gs-savekey');
    if (sk) {
      const input = $('#gs-key'), msg = $('#gs-keymsg');
      const save = async () => {
        const key = input.value.trim();
        if (!key) { msg.className = 'gs-msg err'; msg.textContent = 'Paste your API key first.'; input.focus(); return; }
        sk.disabled = true; msg.className = 'gs-msg'; msg.textContent = 'Saving and checking the key with ' + $('#gs-prov').selectedOptions[0].text + '.';
        try {
          const body = { model_provider: $('#gs-prov').value, model_key: key };
          await api('/settings/test', { method: 'POST', body });
          await api('/settings', { method: 'POST', body });
          toast('Key saved and working'); render();
        } catch (e) {
          const who = $('#gs-prov').selectedOptions[0].text, d = String(e.detail || '');
          msg.className = 'gs-msg err';
          msg.textContent = /answered 40[13]/.test(d) ? who + ' did not accept that key. Check you pasted your ' + who + ' key and try again. Nothing was saved.'
            : /could not be reached/.test(d) ? who + ' could not be reached. Try again in a moment. Nothing was saved.'
            : 'The key could not be checked. Nothing was saved.';
          sk.disabled = false;
        }
      };
      sk.onclick = save; input.onkeydown = e => { if (e.key === 'Enter') save(); };
    }
    const h = $('#gs-hire');
    if (h) h.onclick = async () => { h.disabled = true; try { await api('/specialists/coverage-monitor/hire', { method: 'POST' }); toast('Coverage Monitor added'); render(); } catch (e) { toast(e.detail || 'Could not add'); h.disabled = false; } };
    const r = $('#gs-run');
    if (r) r.onclick = async () => {
      const cm = hireBySlug('coverage-monitor'); if (!cm) return;
      r.disabled = true; $('#gs-msg').textContent = ' Running. This can take a minute.';
      try { const out = await api('/hires/' + cm.id + '/run', { method: 'POST', body: {} }); if (out.error) toast('It stopped: ' + out.error); } catch (e) { toast(e.detail || 'The run could not start'); }
      render();
    };
  }

  /* ---- Desk */
  function statusOf(h) {
    const last = (desk.runs || []).find(r => r.hire_id === h.id);
    if (!last) return ['idle', 'Not run yet', specOf(h).schedule || ''];
    if (last.outcome === 'running') return ['wait', 'Running', ''];
    if (last.outcome === 'approval_waiting') return ['wait', 'Waiting for your approval', when(last.ended_at)];
    if (last.outcome === 'done') return ['ok', 'Ran ' + when(last.ended_at), (last.tool_calls || 0) + ' tool calls'];
    return ['wait', (last.outcome === 'failed' ? 'Failed ' : 'Stopped ') + when(last.ended_at), last.error || ''];
  }
  async function vDesk() {
    const [f, soon] = await Promise.all([api('/feed'), comingUp()]);
    const pend = desk.pending_approvals;
    const gs = gettingStarted();
    const today = new Date().toDateString();
    const isToday = ts => ts && new Date(ts * 1000).toDateString() === today;
    const found = f.posts.filter(p => p.author !== 'system' && p.author !== 'me');
    const ranToday = desk.hires.filter(h => (desk.runs || []).some(r => r.hire_id === h.id && isToday(r.ended_at))).length;
    const lastRun = (desk.runs || []).reduce((m, r) => Math.max(m, r.ended_at || 0), 0);
    const stats = `<section class="stats">
      ${stat('Waiting on you', pend, pend ? '<a href="#/inbox">Review in Inbox</a>' : 'Nothing to approve', pend ? 'warn' : '')}
      ${stat('New findings today', found.filter(p => isToday(p.created_at)).length, found.length + ' on the desk in total')}
      ${stat('Specialists', desk.hires.length, ranToday + ' ran today' + (lastRun ? ' · last at ' + when(lastRun) : ''))}
      ${stat('Companies covered', desk.coverage.length, '<a href="#/coverage">Manage lists</a>')}
    </section>`;
    const roster = desk.hires.map(h => {
      const st = statusOf(h);
      return `<li><button class="rrow" data-go="specialists/${h.id}"><span class="av">${esc(specOf(h).initials || '?')}</span><span class="who"><b>${esc(specOf(h).name || h.slug)}</b><small><i class="dot ${st[0]}"></i>${esc(st[1])}</small></span></button></li>`;
    }).join('');
    const COV_MAX = 8;
    const cov = desk.coverage.slice(0, COV_MAX).map(c => `<li><span class="code">${esc(c.sec_code)}</span><span class="cn">${esc(prettyName(c.name) || c.sec_code)}</span><a class="linkbtn" href="company.html?code=${encodeURIComponent(c.sec_code)}" target="_blank" rel="noopener">Profile</a></li>`).join('');
    const side = `<aside class="deskside">
      ${soon}
      <h2 class="band">Specialists<span class="h2-note">${desk.hires.length} on the desk</span></h2>
      <ul class="roster">${roster || '<li class="muted small">No specialists yet.</li>'}</ul>
      <button class="linkbtn addsp" data-go="specialists">${ICONS.plus} Add a specialist</button>
      <h2 class="band">Coverage<span class="h2-note">${desk.coverage.length} compan${desk.coverage.length === 1 ? 'y' : 'ies'}</span></h2>
      ${cov ? `<ul class="covside">${cov}</ul>` : '<p class="muted small">No companies yet.</p>'}
      <a class="linkbtn addsp" href="#/coverage">${desk.coverage.length > COV_MAX ? `All ${desk.coverage.length} companies` : 'Manage lists'}</a>
    </aside>`;
    const main = `<div class="deskmain"><h2 class="band">Latest from your specialists<span class="h2-note">${found.length ? 'Newest first' : ''}</span></h2>${feedHtml(f.posts)}</div>`;
    $('#main').innerHTML = topbar([['Desk']]) + `<div class="content"><div class="page wide">${gs ? '' : setupNotice()}${pageHead('Desk', 'What your specialists found, and what is waiting on you.')}${gs}${gs ? '' : stats}<div class="desk">${main}${side}</div></div></div>`;
    bindFeed();
    bindCoverage();
    bindGettingStarted();
  }
  /* ----------------------------------------------------------- calendar */
  // Results dates come from the public API (JPX's 決算発表予定日 lists). The
  // desk only chooses which companies to ask about; nothing is computed here.
  const CAL = '/api/v1/equity/calendar/upcoming';
  const WDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const PERIOD = { FY: 'Full Year', Q1: 'Q1', Q2: 'Q2', Q3: 'Q3', Q4: 'Q4' };
  const CAL_MARKETS = [['', 'All markets'], ['prime', 'Prime'], ['standard', 'Standard'], ['growth', 'Growth'], ['reit', 'REIT']];
  const CAL_DAYS = [7, 14, 30, 60];
  const tokyoToday = () => new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Tokyo' });
  const utc = iso => new Date(String(iso).slice(0, 10) + 'T00:00:00Z');
  const isoAdd = (iso, k) => { const d = utc(iso); d.setUTCDate(d.getUTCDate() + k); return d.toISOString().slice(0, 10); };
  const dayLbl = iso => { const d = utc(iso); return WDAY[d.getUTCDay()] + ' ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()]; };
  const monLbl = iso => { const d = utc(iso); return MON[d.getUTCMonth()] + ' ' + d.getUTCFullYear(); };
  const monthLong = iso => utc(iso).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  // prettyName drops a trailing "Co., Ltd.", which leaves "Naito &" hanging.
  const calName = e => { const s = prettyName(e.name_en) || calJa(e) || e.sec_code; return /&$/.test(s) ? s + ' Co.' : s; };
  const calJa = e => (e.name_ja || '').normalize('NFKC');
  const calPeriod = e => e.period_type ? (PERIOD[e.period_type] || e.period_type) : 'Fiscal Period';

  async function calFetch(q) {
    const r = await fetch(CAL + '?' + new URLSearchParams(q), { headers: { Accept: 'application/json' } });
    let data = null;
    try { data = await r.json(); } catch (e) { }
    if (!r.ok) throw { status: r.status, detail: r.status === 503 ? 'The earnings calendar has not been loaded on this server yet.' : ((data && data.detail) || r.statusText) };
    return data;
  }
  // What the calendar cannot see yet, said where the dates are shown: a quiet
  // day after the last published date may only be unpublished.
  function calHorizon(h) {
    if (!h || !h.known_through) return '';
    return `JPX's published dates run to ${dayLbl(h.known_through)}.` + (h.next_list_pending
      ? ` Companies whose quarter or year ends in ${monthLong(h.next_list_pending + '-01')} have no date here yet. JPX publishes that list early the following month.` : '');
  }
  function calSource(d) {
    const lists = (d.horizon.lists || []).slice(0, 3).map(l => `${monthLong(l.period_month)} period-ends as of ${dayLbl(l.as_of_date)}`).join('; ');
    return `<p class="srcline">${trustBadge('official')} Source: Japan Exchange Group, 決算発表予定日 (scheduled dates for earnings announcements)${lists ? ': ' + esc(lists) : ''}. A date is what the company notified the exchange and can change.</p>`;
  }

  // The desk's side column: the next two weeks for the main coverage list.
  async function comingUp() {
    const codes = desk.coverage.map(c => c.sec_code).slice(0, 500);
    const head = `<h2 class="band">Earnings Coming Up<span class="h2-note">Next 14 days</span></h2>`;
    const more = `<a class="linkbtn addsp" href="#/calendar">Full calendar</a>`;
    if (!codes.length) return head + `<p class="muted small">Add companies to your coverage to see their results dates.</p>` + more;
    const start = tokyoToday();
    let d;
    try { d = await calFetch({ start, end: isoAdd(start, 13), codes: codes.join(',') }); }
    catch (e) { return head + `<p class="muted small">${esc(e.detail || 'The earnings calendar could not be loaded.')}</p>` + more; }
    const MAX = 6;
    const items = d.events.slice(0, MAX).map(e => `<li><span class="code">${esc(e.sec_code)}</span><span class="cn">${esc(calName(e))} <small>${esc(calPeriod(e))}</small></span><span class="when">${dayLbl(e.announce_date)}</span></li>`).join('');
    const body = items ? `<ul class="covside calside">${items}</ul>`
      : `<p class="muted small">None of your ${codes.length} compan${codes.length === 1 ? 'y has' : 'ies have'} a published date in the next 14 days. ${esc(calHorizon(d.horizon))}</p>`;
    const extra = d.events.length > MAX ? `<a class="linkbtn addsp" href="#/calendar">All ${d.events.length} in the full calendar</a>` : more;
    return head + body + extra;
  }

  async function vCalendar(r) {
    const lists = desk.lists || [];
    const parts = (r.sub || '').split('/');
    const days = CAL_DAYS.indexOf(+parts[0]) >= 0 ? +parts[0] : 14;
    const market = CAL_MARKETS.some(m => m[0] && m[0] === parts[1]) ? parts[1] : '';
    const list = r.id === 'all' ? null : (lists.find(l => 'l' + l.id === r.id) || (r.id ? null : lists[0]) || null);
    const scope = list ? 'l' + list.id : 'all';
    const path = (s, k, m) => 'calendar/' + s + '/' + k + (m ? '/' + m : '');
    const members = list ? (await api('/coverage?list_id=' + list.id)).coverage : null;
    const start = tokyoToday(), end = isoAdd(start, days - 1);
    const d = members && !members.length ? null
      : await calFetch({ start, end, market, codes: members ? members.map(c => c.sec_code).slice(0, 500).join(',') : '' });

    const tabs = `<nav class="ltabs">${lists.map(l => `<a class="${scope === 'l' + l.id ? 'on' : ''}" href="#/${path('l' + l.id, days, market)}">${esc(l.name)}<span>${l.count}</span></a>`).join('')}<a class="${scope === 'all' ? 'on' : ''}" href="#/${path('all', days, market)}">All companies</a></nav>`;
    const bar = `<div class="calbar"><div class="tabs">${CAL_DAYS.map(k => `<button class="${k === days ? 'on' : ''}" data-go="${path(scope, k, market)}">Next ${k} days</button>`).join('')}</div>
      <label class="calmkt"><span>Market</span><select id="cal-mkt">${CAL_MARKETS.map(m => `<option value="${m[0]}"${m[0] === market ? ' selected' : ''}>${m[1]}</option>`).join('')}</select></label>
      <span class="grow"></span>${d && d.events.length ? '<button class="btn sm" id="cal-csv">Download CSV</button>' : ''}</div>`;
    const head = pageHead('Earnings Calendar', 'The days listed companies have told the exchange they will announce results.');
    const crumbs = topbar([['Calendar']]);
    if (!d) {
      $('#main').innerHTML = crumbs + `<div class="content"><div class="page wide">${head}${tabs}${bar}<p class="muted">This list has no companies yet. <a href="#/coverage/${list.id}">Add some in Coverage</a>, or switch to All companies.</p></div></div>`;
      return bindCalendar(null, scope, days);
    }

    const busiest = d.days.reduce((m, x) => x.companies > (m ? m.companies : 0) ? x : m, null);
    const moved = d.events.filter(e => e.previous_date).length;
    const dated = new Set(d.events.map(e => e.sec_code).concat(d.undecided.map(e => e.sec_code)));
    const missing = members ? members.filter(c => !dated.has(String(c.sec_code).slice(0, 4))) : [];
    const stats = `<section class="stats">
      ${stat('Announcing', n(d.events.length), `${d.days.length} day${d.days.length === 1 ? '' : 's'}, ${dayLbl(start)} to ${dayLbl(end)}`)}
      ${stat('Busiest day', busiest ? n(busiest.companies) : '—', busiest ? dayLbl(busiest.date) : 'No dates in this range')}
      ${stat('Moved dates', n(moved), 'Changed since the previous JPX list')}
      ${members ? stat('No date in range', n(missing.length), `Of ${members.length} on this list`) : stat('Date not set', n(d.undecided.length), 'Listed by JPX as undecided')}
    </section>`;

    let prev = null;
    const row = (e, withDate) => {
      const first = withDate && e.announce_date !== prev; prev = e.announce_date;
      return `<tr${first ? ' class="newday"' : ''}>${withDate ? `<td class="nowrap">${dayLbl(e.announce_date)}${e.previous_date ? `<small>Moved from ${dayLbl(e.previous_date)}</small>` : ''}</td>` : ''}
        <td><a class="code" href="company.html?code=${encodeURIComponent(e.sec_code)}" target="_blank" rel="noopener">${esc(e.sec_code)}</a></td>
        <td>${esc(calName(e))}${e.name_ja ? `<small>${esc(calJa(e))}</small>` : ''}</td>
        <td class="nowrap">${esc(calPeriod(e))}</td><td class="nowrap">${monLbl(e.fy_end)}</td>
        <td>${esc(e.market_en || e.market_ja || '—')}</td><td class="ind">${esc(e.industry_en || e.industry_ja || '—')}</td></tr>`;
    };
    const thead = withDate => `<thead><tr>${withDate ? '<th style="width:120px">Date</th>' : ''}<th style="width:64px">Code</th><th>Company</th><th style="width:96px">Period</th><th style="width:104px">Fiscal Year End</th><th style="width:96px">Market</th><th>Industry</th></tr></thead>`;
    const where = members ? 'on this list' : (market ? 'in this market' : 'on JPX\'s lists');
    const sched = d.events.length
      ? `<div class="panel calwrap"><table class="t caltable">${thead(true)}<tbody>${d.events.map(e => row(e, true)).join('')}</tbody></table></div>`
      : `<p class="muted">No company ${where} has a published date between ${dayLbl(start)} and ${dayLbl(end)}.</p>`;
    const undecided = d.undecided.length ? `<h2 class="band">Date Not Set<span class="h2-note">${n(d.undecided.length)} compan${d.undecided.length === 1 ? 'y' : 'ies'}</span></h2>
      <p class="muted small calnote">JPX lists these companies as 未定 (undecided). They have no date here until they set one.</p>
      <div class="panel calwrap"><table class="t caltable">${thead(false)}<tbody>${d.undecided.map(e => row(e, false)).join('')}</tbody></table></div>` : '';
    const nodate = missing.length ? `<h2 class="band">No Date in This Range<span class="h2-note">${n(missing.length)} compan${missing.length === 1 ? 'y' : 'ies'}</span></h2>
      <p class="muted small calnote">Either their date falls outside these ${days} days or JPX has not listed it yet.</p>
      <ul class="covside calmiss">${missing.map(c => `<li><span class="code">${esc(c.sec_code)}</span><span class="cn">${esc(prettyName(c.name) || c.sec_code)}</span><a class="linkbtn" href="company.html?code=${encodeURIComponent(c.sec_code)}" target="_blank" rel="noopener">Profile</a></li>`).join('')}</ul>` : '';
    const horizon = calHorizon(d.horizon);
    $('#main').innerHTML = crumbs + `<div class="content"><div class="page wide">${head}${tabs}${bar}
      ${horizon ? `<div class="notice">${esc(horizon)}</div>` : ''}${stats}
      <h2 class="band">Scheduled Announcements<span class="h2-note">${dayLbl(start)} to ${dayLbl(end)}</span></h2>${sched}
      ${undecided}${nodate}${calSource(d)}</div></div>`;
    bindCalendar(d, scope, days, { start, end, market, list });
  }
  function bindCalendar(d, scope, days, q) {
    const sel = $('#cal-mkt');
    if (sel) sel.onchange = () => go('calendar/' + scope + '/' + days + (sel.value ? '/' + sel.value : ''));
    const btn = $('#cal-csv');
    if (btn && d) btn.onclick = () => {
      const c = v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
      const head = ['Earnings calendar, ' + q.start + ' to ' + q.end + (q.list ? ', list: ' + q.list.name : ', all companies') + (q.market ? ', market: ' + q.market : ''),
        'Source: Japan Exchange Group, 決算発表予定日 (scheduled dates for earnings announcements), exactly as published',
        'Lists: ' + d.horizon.lists.map(l => String(l.period_month).slice(0, 7) + ' period-ends as of ' + l.as_of_date + ' (' + l.file_name + ')').join('; '),
        'A date is what the company notified the exchange and can change. previous_date: ' + d.calc.previous_date,
        'Undecided (未定) dates are blank, never guessed.', 'Retrieved ' + new Date().toISOString().slice(0, 19) + 'Z from ' + location.origin + CAL];
      const cols = ['announce_date', 'previous_date', 'sec_code', 'name_en', 'name_ja', 'period_type', 'period_type_raw', 'fy_end', 'market_en', 'industry_en', 'list_as_of'];
      const csv = head.map(l => '# ' + l).join('\n') + '\n' + cols.join(',') + '\n' +
        d.events.concat(d.undecided).map(e => cols.map(k => c(e[k])).join(',')).join('\n') + '\n';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
      a.download = 'earnings-calendar-' + q.start + '-to-' + q.end + '.csv';
      document.body.appendChild(a); a.click(); a.remove();
    };
  }

  function feedHtml(posts) {
    const items = posts.slice().reverse().map(p => {
      if (p.author === 'system') return `<div class="sysline">${esc(p.text)}</div>`;
      if (p.author === 'me') return `<article class="post mine"><span class="av me">${esc((me.email || 'A')[0].toUpperCase())}</span><div class="pbody"><header><b>You</b><time>${when(p.created_at)}</time></header><p>${esc(p.text)}</p></div></article>`;
      const au = authorOf(p);
      const a = p.approval;
      const waiting = a && a.status !== 'approved' && a.status !== 'declined';
      const same = a && a.text && a.text.trim() === String(p.text || '').trim();
      const appr = !a ? '' : a.status === 'approved' ? `<p class="done ok">${ICONS.check}Approved and posted to Slack</p>` : a.status === 'declined' ? '<p class="done muted">Declined. Nothing was sent.</p>'
        : `<div class="approval"><div class="aq"><b>Post this to Slack?</b>${same ? '' : `<p>${esc(a.text)}</p>`}</div><div class="ab"><button class="btn sm primary" data-approve="${a.id}">Approve</button><button class="btn sm" data-decline="${a.id}">Decline</button></div></div>`;
      return `<article class="post${waiting ? ' waiting' : ''}"><span class="av${au.ext ? ' ext' : ''}">${esc(au.initials)}</span><div class="pbody"><header><b>${esc(au.name)}</b>${au.ext ? '<span class="via">via connection</span>' : ''}<time>${when(p.created_at)}</time>${waiting ? '<span class="tag warn">Needs approval</span>' : ''}</header><div class="clamp">${md(p.text)}</div>${chartsHtml(p.charts, 'p' + p.id)}${metaHtml(p.refs, p.calls, 'p' + p.id)}${appr}</div></article>`;
    }).join('');
    return `<section class="feed"><div class="compose"><textarea id="compose" placeholder="Ask a specialist"></textarea><button class="btn" id="m-at" title="Mention a specialist">@</button><button class="btn primary" id="send">Send</button>
      <div class="mentions hidden" id="mentions">${desk.hires.map(h => `<button data-m="${esc(h.slug)}">${esc(specOf(h).name || h.slug)}</button>`).join('') || '<button disabled>No specialists on the desk yet</button>'}</div></div>
      <div class="posts" id="posts">${items || '<p class="empty">Nothing yet. When a specialist runs, what it finds appears here.</p>'}</div></section>`;
  }
  function bindFeed() {
    const c = $('#compose'); if (!c) return;
    const send = async () => {
      const v = c.value.trim(); if (!v) return;
      c.value = ''; $('#posts').insertAdjacentHTML('afterbegin', working('Asking the specialist.'));
      try { const out = await api('/feed', { method: 'POST', body: { text: v } }); if (out.note) toast(out.note); if (out.run && out.run.error) toast(out.run.error); }
      catch (e) { toast(e.detail || 'Could not send'); }
      render();
    };
    $('#send').onclick = send;
    c.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } if (e.key === '@') $('#mentions').classList.remove('hidden'); if (e.key === 'Escape') $('#mentions').classList.add('hidden'); };
    $('#m-at').onclick = () => $('#mentions').classList.toggle('hidden');
    $$('#mentions button[data-m]').forEach(b => b.onclick = () => { c.value = c.value.replace(/@[a-z-]*$/, '') + (c.value && !/[@\s]$/.test(c.value) ? ' ' : '') + '@' + b.dataset.m + ' '; $('#mentions').classList.add('hidden'); c.focus(); });
    bindApprovals();
    $$('#posts .clamp').forEach(el => {
      const md = el.querySelector('.md');
      if (md && md.children.length > 1) {
        const more = document.createElement('button'); more.className = 'linkbtn more'; more.textContent = 'Show more';
        more.onclick = () => { const open = el.classList.toggle('open'); more.textContent = open ? 'Show less' : 'Show more'; };
        el.after(more);
      } else el.classList.add('open');
    });
  }
  function bindApprovals() {
    $$('[data-approve]').forEach(b => b.onclick = async () => { b.disabled = true; try { await api('/approvals/' + b.dataset.approve + '/approve', { method: 'POST' }); toast('Sent to Slack'); } catch (e) { toast(e.detail || 'Could not send'); } render(); });
    $$('[data-decline]').forEach(b => b.onclick = async () => { try { await api('/approvals/' + b.dataset.decline + '/decline', { method: 'POST' }); toast('Declined. Nothing was sent.'); } catch (e) { toast(e.detail || 'Could not decline'); } render(); });
  }

  /* ---- Specialists */
  let catTab = 'all', catQ = '';
  async function vSpecialists() {
    const data = await api('/specialists');
    const draw = () => {
      const q = catQ.toLowerCase();
      const list = data.specialists.filter(s => catTab === 'added' ? s.hired : catTab === 'available' ? !s.hired : true).filter(s => !q || (s.name + ' ' + s.summary + ' ' + s.category).toLowerCase().includes(q));
      const updates = data.specialists.filter(s => s.update_available).length;
      $('#main').innerHTML = topbar([['Specialists']]) + `<div class="content"><div class="page wide">${pageHead('Specialists', 'Ready-made analysts for your desk. Each runs on your own model key and asks before anything goes out.', updates ? `<span class="muted small">${updates} update${updates > 1 ? 's' : ''} available</span>` : '')}
        <div class="toolbar"><input class="search" id="cat-q" placeholder="Search specialists" value="${esc(catQ)}"><div class="tabs">${[['all', 'All'], ['added', 'Added'], ['available', 'Available']].map(x => `<button class="${catTab === x[0] ? 'on' : ''}" data-tab="${x[0]}">${x[1]}</button>`).join('')}</div></div>
        <h2 class="band">${catTab === 'added' ? 'On your desk' : catTab === 'available' ? 'Available to add' : 'All specialists'}<span class="h2-note">${list.length} shown</span></h2>
        ${list.length ? `<ul class="catalogue">${list.map(s => `<li class="${s.hired ? 'hired' : ''}"><span class="av">${esc(s.initials)}</span><div class="what"><b>${esc(s.name)}</b><small>${esc(s.category)} · ${s.tools.length} tools · v${esc(s.hired_version || s.version)}</small></div><p>${esc(s.summary)}</p><div class="act">${s.update_available ? `<button class="linkbtn warn" data-update="${s.hire_id}">Update to v${esc(s.version)}</button>` : ''}${s.hired ? `<span class="tag ok">On desk</span><button class="btn sm" data-go="specialists/${s.hire_id}">Open</button>` : `<button class="btn sm" data-hire="${esc(s.slug)}">${ICONS.plus} Add</button>`}</div></li>`).join('')}</ul>` : '<p class="muted">No specialist matches.</p>'}</div></div>`;
      bindCommon();
      $$('[data-tab]').forEach(b => b.onclick = () => { catTab = b.dataset.tab; draw(); });
      const qi = $('#cat-q'); qi.oninput = () => { catQ = qi.value; const pos = qi.selectionStart; draw(); const q2 = $('#cat-q'); q2.focus(); q2.setSelectionRange(pos, pos); };
      $$('[data-hire]').forEach(b => b.onclick = async () => { try { const h = await api('/specialists/' + b.dataset.hire + '/hire', { method: 'POST' }); toast(specOf(h).name + ' added to your desk'); go('specialists/' + h.id); } catch (e) { toast(e.detail || 'Could not add'); } });
      $$('[data-update]').forEach(b => b.onclick = async () => { await api('/hires/' + b.dataset.update + '/update', { method: 'POST' }); toast('Updated'); render(); });
    };
    draw();
  }

  /* ---- Specialist page */
  async function vSpecialist(r) {
    const h = hireById(r.id);
    if (!h) { go('specialists'); return; }
    const s = specOf(h);
    const [th, fl] = await Promise.all([api('/threads'), api('/files')]);
    const threads = th.threads.filter(t => t.hire_id === h.id);
    const files = fl.files.filter(f => f.hire_id === h.id);
    const runs = (desk.runs || []).filter(x => x.hire_id === h.id);
    const setup = [['Model key', desk.desk.model_key_set ? 'Stored' : 'Missing', desk.desk.model_key_set], ['Delivery', desk.desk.slack_set ? (desk.desk.slack_label || 'Slack') : 'Not set', desk.desk.slack_set], ['Approval first', 'Always', true]];
    $('#main').innerHTML = topbar([['Specialists', 'specialists'], [s.name || h.slug]]) + `<div class="content"><div class="page wide">${setupNotice()}
      <div class="pagehead sp"><span class="av lg">${esc(s.initials || '?')}</span><div><h1>${esc(s.name || h.slug)}</h1><p>${esc(s.schedule || '')}${h.update_available ? ` · <button class="linkbtn warn" data-update="${h.id}">Update to v${esc(s.version)}</button>` : ''}</p></div><div class="actions"><button class="btn primary" id="run-now">Run now</button><button class="btn" id="remove">Remove</button></div></div>
      <div class="sp-layout"><div>
        <p class="brief">${esc(s.brief || '')}</p>
        ${(s.tools || []).includes('my_coverage') || s.prepare ? `<h2 class="band">Watches</h2><div class="watchpick"><select id="sp-list">${(desk.lists || []).map(l => `<option value="${l.is_default ? 0 : l.id}" ${((h.config || {}).list_id || 0) === (l.is_default ? 0 : l.id) ? 'selected' : ''}>${esc(l.name)} (${l.count})</option>`).join('')}</select><a class="linkbtn" href="#/coverage/${(h.config || {}).list_id || ''}">Edit this list</a></div>` : ''}
        <h2 class="band">Tools it may call</h2><div class="chips">${(s.tools || []).map(t => `<span class="chip" style="cursor:default">${esc(t)}</span>`).join('')}</div>
        <h2 class="band">Try asking</h2><div class="chips">${(s.tries || []).map(x => `<button class="chip" data-ask="${esc(x)}">${esc(x)}</button>`).join('')}</div>
        <div id="run-out"></div>
        ${runs.length ? `<h2 class="band">Recent runs</h2><ul class="rows">${runs.slice(0, 5).map(x => `<li><div><b>${esc(outcomeLabel(x.outcome))}</b><small>${esc(x.trigger)} · ${x.tool_calls} tool calls · ${n(x.tokens_in + x.tokens_out)} tokens${x.error ? ' · ' + esc(x.error) : ''}</small></div><time class="muted small">${when(x.started_at)}</time></li>`).join('')}</ul>` : ''}
      </div>
      <aside class="side-stack">
        <section class="panel"><header>Setup</header><ul class="kvlist">${setup.map(x => `<li><span>${x[0]}</span><b class="${x[2] ? 'ok' : ''}">${x[2] ? ICONS.check : ''}${esc(x[1])}</b></li>`).join('')}</ul></section>
        <section class="panel"><header>Threads<small>${threads.length}</small><span class="grow"></span><button class="linkbtn" id="new-thread">${ICONS.plus}New</button></header>${threads.length ? `<ul class="linklist">${threads.map(t => `<li><button data-go="research/${t.id}"><b>${esc(t.title)}</b><small>${when(t.updated_at)}</small></button></li>`).join('')}</ul>` : '<p class="empty">No threads yet.</p>'}</section>
        <section class="panel"><header>Files</header>${files.length ? `<ul class="linklist mono">${files.map(f => `<li><button data-go="files/${h.id}/${encodeURIComponent(f.path)}">${esc(f.path)}</button></li>`).join('')}</ul>` : '<p class="empty">Nothing written yet.</p>'}</section>
        <p class="fine">Version ${esc(h.version)} · ${esc(s.sha || '')} · ${(s.tools || []).length} tools via the Observatory</p>
      </aside></div></div></div>`;
    const ask = async q => { const id = await newThread(h, q); if (id) go('research/' + id); };
    $$('[data-ask]').forEach(b => b.onclick = () => ask(b.dataset.ask));
    $('#new-thread').onclick = () => { const q = prompt('Ask ' + (s.name || h.slug)); if (q) ask(q); };
    $('#run-now').onclick = async () => {
      const out = $('#run-out'); out.innerHTML = working('Running ' + (s.name || h.slug) + '.'); $('#run-now').disabled = true;
      try { const res = await api('/hires/' + h.id + '/run', { method: 'POST', body: {} }); out.innerHTML = `<h2 class="band">This run</h2><p><b>${esc(outcomeLabel(res.outcome))}</b>${res.error ? ' · ' + esc(res.error) : ''}</p>${res.text ? md(res.text) : ''}${chartsHtml(res.charts, 'run')}${trailHtml(res.calls, 'run')}`; bindCommon(); desk = await api('/desk'); }
      catch (e) { out.innerHTML = `<p class="err">${esc(e.detail || 'The run could not start.')}</p>`; }
      $('#run-now').disabled = false;
    };
    const sl = $('#sp-list'); if (sl) sl.onchange = async () => { await api('/hires/' + h.id + '/config', { method: 'POST', body: { list_id: +sl.value } }); toast('Now watching ' + sl.options[sl.selectedIndex].text.replace(/ \(\d+\)$/, '')); desk = await api('/desk'); };
    $('#remove').onclick = async () => { if (!confirm('Remove ' + (s.name || h.slug) + ' from your desk?')) return; await api('/hires/' + h.id, { method: 'DELETE' }); go('specialists'); };
    $$('[data-update]').forEach(b => b.onclick = async () => { await api('/hires/' + b.dataset.update + '/update', { method: 'POST' }); toast('Updated'); render(); });
  }
  const outcomeLabel = o => ({ done: 'Finished', approval_waiting: 'Waiting for approval', failed: 'Failed', budget: 'Stopped at budget', running: 'Running' }[o] || o);
  async function newThread(h, q) {
    const main = $('#main .content'); if (main) main.insertAdjacentHTML('afterbegin', working('Asking ' + (specOf(h).name || h.slug) + '.'));
    try { const t = await api('/threads', { method: 'POST', body: { hire_id: h.id, text: q } }); return t.id; }
    catch (e) { toast(e.detail || 'Could not start the thread'); render(); return null; }
  }

  /* ---- Inbox */
  async function vInbox() {
    const d = await api('/inbox');
    const card = a => { const h = hireById(a.hire_id); const sp = a.hire_id ? specOf(h) : { name: (a.payload && a.payload.from) || 'Connection', initials: 'EX' }; return `<article class="card${a.status === 'pending' ? ' waiting' : ''}"><header class="cardhead"><span class="av">${esc(sp.initials || '?')}</span><div><b>${esc(sp.name || 'Specialist')}</b><small>Wants to post to ${esc(desk.desk.slack_label || 'Slack')} · ${when(a.created_at)}</small></div></header>
      <blockquote>${esc(a.payload.text)}</blockquote>${refsHtml(a.payload.sources)}
      <div class="cardacts">${a.status !== 'pending' ? `<span class="${a.status === 'approved' ? 'ok' : 'muted'}">${a.status === 'approved' ? 'Approved and sent' : 'Declined. Nothing was sent.'}</span>` : `<button class="btn primary" data-approve="${a.id}">Approve and post</button><button class="btn" data-decline="${a.id}">Decline</button>${desk.desk.slack_set ? '' : '<span class="muted small">Add a Slack webhook in Settings first.</span>'}`}</div></article>`; };
    $('#main').innerHTML = topbar([['Inbox']]) + `<div class="content"><div class="page narrow">${pageHead('Inbox', d.pending.length ? `${d.pending.length} waiting for your approval.` : 'Nothing is waiting on you.')}
      ${d.pending.length ? `<h2 class="band">Waiting on you<span class="h2-note">${d.pending.length} to decide</span></h2>${d.pending.map(card).join('')}` : ''}
      <h2 class="band">Recent runs<span class="h2-note">${d.runs.length ? 'Newest first' : ''}</span></h2>${d.runs.length ? `<ul class="rows">${d.runs.map(x => { const h = hireById(x.hire_id); return `<li><span class="av sm">${esc(specOf(h).initials || '?')}</span><div><b>${esc(specOf(h).name || x.slug)}</b><small>${esc(outcomeLabel(x.outcome))} · ${x.tool_calls} tool calls${x.error ? ' · ' + esc(x.error) : ''}</small></div><time class="muted small">${when(x.started_at)}</time></li>`; }).join('')}</ul>` : '<p class="muted">No runs yet.</p>'}
      ${d.decided.length ? `<h2 class="band">Decided</h2>${d.decided.map(card).join('')}` : ''}</div></div>`;
    bindApprovals();
  }

  // A thread's messages, the same on the Research page, the Chat page and in
  // the chat pop-up. `key` keeps chart and trail ids apart between them.
  function msgsHtml(t, sp, key) {
    return t.messages.map((m, i) => m.role === 'user' ? `<div class="msg"><span class="av me">${esc((me.email || 'A')[0].toUpperCase())}</span><div class="mbody"><header><b>You</b><time>${when(m.created_at)}</time></header><div class="txt">${esc(m.text)}</div></div></div>`
      : `<div class="msg"><span class="av">${esc(sp.initials || '?')}</span><div class="mbody"><header><b>${esc(sp.name || '')}</b><time>${when(m.created_at)}</time></header><div class="txt">${md(m.text)}</div>${savedHtml(m.memory)}${chartsHtml(m.charts, key + i)}${trailHtml(m.calls, key + i)}</div></div>`).join('');
  }
  // Notes the reply saved to the person's memory, each with Undo.
  const savedHtml = notes => (notes || []).map(n => `<p class="memsaved"><span>Saved to memory: ${esc(n.text)}</span><button class="linkbtn" data-memundo="${n.id}">Undo</button></p>`).join('');
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('[data-memundo]');
    if (!b) return;
    b.disabled = true;
    api('/memory/' + b.dataset.memundo, { method: 'DELETE' })
      .then(() => { b.parentNode.innerHTML = '<span>Removed from memory.</span>'; })
      .catch(err => { if (err.status === 404) { b.parentNode.innerHTML = '<span>Removed from memory.</span>'; return; } b.disabled = false; toast(err.detail || 'Could not remove the note'); });
  });
  // The question just sent, shown at once while the answer is worked out.
  const pendingHtml = q => `<div class="msg"><span class="av me">${esc((me.email || 'A')[0].toUpperCase())}</span><div class="mbody"><header><b>You</b></header><div class="txt">${esc(q)}</div></div></div>` + working('Looking it up. This can take a minute.');
  const analystThreads = list => list.filter(t => desk.analyst && t.hire_id === desk.analyst.id);

  /* ---- Chat box: the question, the coverage companies it is about, Ask.
     Used on the Chat page ('chat') and in the pop-up ('dock'). Picked
     companies go into the question itself — "About Toyota Motor (7203): …" —
     so the thread shows exactly what was asked. */
  const picks = { chat: [], dock: [] };   // [{code, name}] per chat box
  function composerHtml(id, placeholder) {
    return `<div class="composer" id="cmp-${id}"><div class="picked"></div>
      <textarea rows="3" placeholder="${esc(placeholder)}"></textarea>
      <div class="cfoot"><div class="covpick"><button class="linkbtn" data-pick>${ICONS.coverage}<span>Companies</span></button><div class="pickmenu hidden"></div></div>
        <span class="grow"></span><span class="hint">Enter to send · Shift+Enter for a new line</span><button class="btn primary" data-send>Ask</button></div></div>`;
  }
  function bindComposer(id, onSend) {
    const box = $('#cmp-' + id); if (!box) return;
    const ta = $('textarea', box), menu = $('.pickmenu', box), pickBtn = $('[data-pick]', box), sendBtn = $('[data-send]', box);
    const drawPicked = () => {
      const p = picks[id];
      $('.picked', box).innerHTML = p.map(c => `<span class="pchip">${esc(c.name)}<span class="code">${esc(c.code)}</span><button data-unpick="${esc(c.code)}" aria-label="Remove ${esc(c.name)}">${ICONS.close}</button></span>`).join('');
      $('.picked', box).classList.toggle('hidden', !p.length);
      $('span', pickBtn).textContent = p.length ? 'Companies (' + p.length + ')' : 'Companies';
      $$('[data-unpick]', box).forEach(b => b.onclick = () => { picks[id] = picks[id].filter(c => c.code !== b.dataset.unpick); drawPicked(); drawMenu(); });
    };
    let listId = null;
    const drawMenu = async () => {
      if (menu.classList.contains('hidden')) return;
      const lists = desk.lists || [];
      if (listId == null) { const d = lists.find(l => l.is_default) || lists[0]; listId = d ? (d.is_default ? 0 : d.id) : 0; }
      let rows = [];
      try { rows = (await api('/coverage?list_id=' + listId)).coverage; } catch (e) { rows = []; }
      const on = new Set(picks[id].map(c => c.code));
      menu.innerHTML = `<header>${lists.length > 1 ? `<select data-list>${lists.map(l => { const v = l.is_default ? 0 : l.id; return `<option value="${v}" ${v === listId ? 'selected' : ''}>${esc(l.name)} (${l.count})</option>`; }).join('')}</select>` : `<b>${esc((lists[0] || {}).name || 'Coverage')}</b>`}
          ${rows.length ? `<button class="linkbtn" data-all>${rows.every(r => on.has(r.sec_code)) ? 'Clear all' : 'Select all'}</button>` : ''}</header>
        ${rows.length ? `<ul>${rows.map(r => `<li><label><input type="checkbox" value="${esc(r.sec_code)}" data-name="${esc(prettyName(r.name))}" ${on.has(r.sec_code) ? 'checked' : ''}><span>${esc(prettyName(r.name))}</span><span class="code">${esc(r.sec_code)}</span></label></li>`).join('')}</ul>`
          : '<p class="empty">No companies on this list. <a href="#/coverage">Add some on Coverage</a>.</p>'}`;
      const sel = $('[data-list]', menu); if (sel) sel.onchange = () => { listId = +sel.value; drawMenu(); };
      $$('input[type=checkbox]', menu).forEach(cb => cb.onchange = () => {
        picks[id] = cb.checked ? picks[id].concat([{ code: cb.value, name: cb.dataset.name }]) : picks[id].filter(c => c.code !== cb.value);
        drawPicked(); const all = $('[data-all]', menu); if (all) all.textContent = $$('input[type=checkbox]', menu).every(x => x.checked) ? 'Clear all' : 'Select all';
      });
      const all = $('[data-all]', menu);
      if (all) all.onclick = () => {
        const codes = rows.map(r => r.sec_code); const clear = rows.every(r => picks[id].some(c => c.code === r.sec_code));
        picks[id] = picks[id].filter(c => !codes.includes(c.code));
        if (!clear) picks[id] = picks[id].concat(rows.map(r => ({ code: r.sec_code, name: prettyName(r.name) })));
        drawPicked(); drawMenu();
      };
    };
    // up from a box at the foot of the screen, down from one near the top
    pickBtn.onclick = () => { menu.classList.toggle('down', pickBtn.getBoundingClientRect().top < 360); menu.classList.toggle('hidden'); drawMenu(); };
    const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, 240) + 'px'; };
    ta.oninput = grow;
    const send = () => {
      const q = ta.value.trim(); if (!q) return;
      const p = picks[id];
      const text = p.length ? 'About ' + p.map(c => c.name + ' (' + c.code + ')').join(', ') + ': ' + q : q;
      picks[id] = []; menu.classList.add('hidden');
      ta.value = ''; ta.disabled = true; sendBtn.disabled = true; drawPicked(); grow();
      onSend(text);
    };
    sendBtn.onclick = send;
    ta.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
    drawPicked();
    return ta;
  }
  // A click outside a company menu closes it.
  document.addEventListener('mousedown', e => $$('.pickmenu:not(.hidden)').forEach(m => { if (!m.parentNode.contains(e.target)) m.classList.add('hidden'); }));

  /* ---- Chat: a conversation with the desk's Analyst */
  async function vChat(r) {
    const a = desk.analyst; const sp = specOf(a);
    const [list, t] = await Promise.all([api('/threads'), r.id ? api('/threads/' + r.id) : null]);
    const chats = analystThreads(list.threads);
    const head = t ? pageHead(esc(t.title), 'With the Analyst. It reads the same data as this site, and every answer lists its lookups.')
      : pageHead('Chat', 'Ask about any dataset or company, or pick companies from your coverage. The Analyst looks up every figure and lists its lookups under the answer.');
    $('#main').innerHTML = topbar(t ? [['Chat', 'chat'], [t.title]] : [['Chat']]) + `<div class="content"><div class="page wide">${setupNotice()}
      <div class="chat-layout"><div class="thread">${head}
        <div id="chat-msgs">${t ? msgsHtml(t, sp, 'c') : ''}</div>
        ${composerHtml('chat', t ? 'Ask a follow-up' : 'Ask the Analyst')}</div>
      <aside class="side-stack"><section class="panel"><header>Chats<small>${chats.length}</small><span class="grow"></span>${t ? `<button class="linkbtn" data-go="chat">${ICONS.plus}New</button>` : ''}</header>${chats.length ? `<ul class="linklist">${chats.map(c => `<li><button class="${t && t.id === c.id ? 'on' : ''}" data-go="chat/${c.id}"><b>${esc(c.title)}</b><small>${when(c.updated_at)}</small></button></li>`).join('')}</ul>` : '<p class="empty">No chats yet.</p>'}</section></aside></div></div></div>`;
    const ta = bindComposer('chat', async q => {
      $('#chat-msgs').insertAdjacentHTML('beforeend', pendingHtml(q));
      const c = $('#main .content'); if (c) c.scrollTop = 1e6;
      try {
        if (t) { await api('/threads/' + t.id + '/messages', { method: 'POST', body: { text: q } }); render(); }
        else { const nt = await api('/threads', { method: 'POST', body: { hire_id: a.id, text: q } }); go('chat/' + nt.id); }
      } catch (e) { toast(e.detail || 'Could not ask'); render(); }
    });
    if (t) { const c = $('#main .content'); if (c) c.scrollTop = 1e6; } else if (ta) ta.focus();
  }

  /* ---- Chat pop-up: the same Analyst from any page, docked bottom right.
     It lives outside #app so a page change does not close it; which chat it
     shows is kept for the browser tab. */
  let dockState = { open: false, tid: null };
  try { dockState = Object.assign(dockState, JSON.parse(sessionStorage.getItem('ia-dock') || '{}')); } catch (e) { }
  const saveDock = () => { try { sessionStorage.setItem('ia-dock', JSON.stringify(dockState)); } catch (e) { } };
  let dockBusy = false;
  function dockEl() {
    let el = document.getElementById('chatdock');
    if (!el) { el = document.createElement('div'); el.id = 'chatdock'; document.body.appendChild(el); }
    return el;
  }
  async function renderDock(r) {
    const el = dockEl();
    // The Chat page is the same conversation at full size: no pop-up on it.
    if (!desk || !desk.analyst || (r || route()).sec === 'chat') { el.innerHTML = ''; return; }
    if (!dockState.open) {
      el.innerHTML = `<button class="dock-btn" id="dock-open" aria-label="Open chat">${ICONS.chat}<span>Chat</span></button>`;
      $('#dock-open').onclick = () => { dockState.open = true; saveDock(); renderDock(); };
      return;
    }
    // While a question is out, a page change leaves the pop-up as it is.
    if (dockBusy && $('.dock', el)) return;
    const sp = specOf(desk.analyst);
    let t = null;
    if (dockState.tid) { try { t = await api('/threads/' + dockState.tid); } catch (e) { dockState.tid = null; saveDock(); } }
    const noKey = !desk.desk.model_provider || !desk.desk.model_key_set;
    el.innerHTML = `<section class="dock" role="dialog" aria-label="Chat with the Analyst">
      <header><b>${t ? esc(t.title) : 'Analyst'}</b><span class="grow"></span>
        ${t ? `<button class="iconbtn" id="dock-new" title="New chat">${ICONS.plus}</button>` : ''}
        <button class="iconbtn" id="dock-full" title="Open in Chat">${ICONS.expand}</button>
        <button class="iconbtn" id="dock-close" title="Close">${ICONS.close}</button></header>
      <div class="dock-body" id="dock-body">${noKey ? '<div class="notice">No model key yet. <a href="#/settings">Add one in Settings</a>.</div>' : ''}
        ${t ? msgsHtml(t, sp, 'd') : '<p class="muted small">Ask about any dataset or company, or pick companies from your coverage. Every answer lists its lookups.</p>'}</div>
      ${composerHtml('dock', t ? 'Ask a follow-up' : 'Ask the Analyst')}</section>`;
    const body = $('#dock-body'); body.scrollTop = 1e6;
    $('#dock-close').onclick = () => { dockState.open = false; saveDock(); renderDock(); };
    $('#dock-full').onclick = () => { dockState.open = false; saveDock(); go(t ? 'chat/' + t.id : 'chat'); };
    const nb = $('#dock-new'); if (nb) nb.onclick = () => { dockState.tid = null; saveDock(); renderDock(); };
    const ta = bindComposer('dock', async q => {
      if (dockBusy) return;
      dockBusy = true;
      body.insertAdjacentHTML('beforeend', pendingHtml(q)); body.scrollTop = 1e6;
      try {
        if (t) await api('/threads/' + t.id + '/messages', { method: 'POST', body: { text: q } });
        else { const nt = await api('/threads', { method: 'POST', body: { hire_id: desk.analyst.id, text: q } }); dockState.tid = nt.id; saveDock(); }
      } catch (e) { toast(e.detail || 'Could not ask'); }
      dockBusy = false;
      await renderDock(); bindCommon();
    });
    ta.addEventListener('keydown', e => { if (e.key === 'Escape') $('#dock-close').click(); });
    if (!t) ta.focus();
  }

  /* ---- Research */
  async function vResearch() {
    const d = await api('/threads');
    $('#main').innerHTML = topbar([['Research']]) + `<div class="content"><div class="page narrow">${pageHead('Research', 'Conversations with the Analyst and your specialists. Every answer shows its tool calls.', desk.hires.length ? `<button class="btn" id="new-thread">${ICONS.plus} New thread</button>` : '')}
      ${d.threads.length ? `<ul class="rows">${d.threads.map(t => { const h = hireById(t.hire_id); return `<li data-go="research/${t.id}"><span class="av sm">${esc(specOf(h).initials || '?')}</span><div><b>${esc(t.title)}</b><small>${esc(specOf(h).name || '')} · ${t.messages} messages</small></div><time class="muted small">${when(t.updated_at)}</time></li>`; }).join('')}</ul>` : '<p class="muted">No threads yet. <a href="#/chat">Start a chat</a>, or open a specialist and ask it something.</p>'}</div></div>`;
    const nt = $('#new-thread'); if (nt) nt.onclick = () => go('specialists/' + desk.hires[0].id);
  }
  async function vThread(r) {
    const t = await api('/threads/' + r.id);
    const h = hireById(t.hire_id); const sp = specOf(h);
    $('#main').innerHTML = topbar([['Research', 'research'], [t.title]]) + `<div class="content"><div class="page"><div class="thread">${pageHead(esc(t.title), 'With ' + esc(sp.name || 'a specialist') + '. It only reads; nothing is sent without your approval.')}
      ${msgsHtml(t, sp, 'm')}
      <div id="thread-wait"></div>
      <div class="ask"><input id="ask" placeholder="Ask ${esc(sp.name || '')}"><button class="btn primary" id="ask-send">Ask</button></div>
      <div class="chips" style="margin-top:12px">${(sp.tries || []).map(x => `<button class="chip" data-askhere="${esc(x)}">${esc(x)}</button>`).join('')}</div></div></div></div>`;
    const send = async q => { if (!q) return; $('#thread-wait').innerHTML = working('Asking ' + (sp.name || '') + '.'); $('#ask').disabled = true; try { await api('/threads/' + t.id + '/messages', { method: 'POST', body: { text: q } }); } catch (e) { toast(e.detail || 'Could not ask'); } render(); };
    $('#ask-send').onclick = () => send($('#ask').value.trim()); $('#ask').onkeydown = e => { if (e.key === 'Enter') send($('#ask').value.trim()); };
    $$('[data-askhere]').forEach(b => b.onclick = () => send(b.dataset.askhere));
    const c = $('#main .content'); if (c) c.scrollTop = 1e6;
  }

  /* ---- Studies: analyses run on Plover's server (app/analysis.py). The AI
     writes the query and reads a preview; the rows, chart and CSV come from
     the server. A query run by hand uses no AI at all. Every run is kept. */
  const STUDY_EXAMPLE = 'How far apart are the US and Japanese 10-year yields since 2020, and when was the gap widest?';
  // the most tokens one question on a study may spend; empty = the server's cap
  const BUDGETS = [2000, 5000, 10000, 25000, 50000, 100000];
  // One precision per column: the most decimals any value carries, at most four.
  function colDp(rows, i) { let dp = 0; rows.forEach(r => { const v = r[i]; if (typeof v === 'number' && isFinite(v)) { const t = String(v).split('.')[1]; if (t) dp = Math.max(dp, Math.min(4, t.length)); } }); return dp; }
  const cell = (v, dp) => v == null ? '—' : (typeof v === 'number' ? fmtNum(v, dp) : esc(v));
  // a figure with the decimals it carries (at most three), never padded: 4.67 stays 4.67
  const fig = v => v == null ? '—' : (typeof v === 'number' ? fmtNum(v, Math.min(3, ((String(v).split('.')[1]) || '').length)) : esc(v));
  const STAT_LABEL = { latest: 'Latest', min: 'Lowest', max: 'Highest', mean: 'Average', median: 'Median', count: 'Count', highest_since: 'Highest since', lowest_since: 'Lowest since' };
  function figuresHtml(summary, sources) {
    if (!summary) return '';
    const cells = [];
    const titles = {}; (sources || []).forEach(s => { titles[s.name] = s.title || s.series; });
    Object.keys(summary).forEach(col => Object.keys(summary[col]).forEach(k => {
      const v = summary[col][k];
      let big, sub;
      if (v && typeof v === 'object' && 'since' in v) { big = v.since ? esc(v.since) : 'Never'; sub = v.since ? 'last at ' + fig(v.that_value) + ', now ' + fig(v.latest) : esc(v.note || ''); }
      else if (v && typeof v === 'object') { big = fig(v.value); sub = esc(v.at || ''); }
      else { big = fig(v); sub = ''; }
      cells.push(stat(esc(titles[col] || col) + ' · ' + esc(STAT_LABEL[k] || k), big, sub));
    }));
    return cells.length ? `<div class="stats study-figs">${cells.join('')}</div>` : '';
  }
  function studySpecForm(spec) {
    const series = Object.entries(spec.series || {});
    while (series.length < 2) series.push(['', '']);
    const ch = spec.chart || {};
    const figs = (spec.summary || []).map(x => x.column + ': ' + (x.stats || []).join(', ')).join('; ');
    return `<div class="study-form">
      <div class="study-series">${series.map(([n, ref], i) => `<div class="st-row"><input class="st-name" data-sn="${i}" value="${esc(n)}" placeholder="name, e.g. japan" aria-label="Series name"><input class="st-ref" data-sr="${i}" value="${esc(ref)}" placeholder="dataset:code, e.g. jgb-yields:10Y" aria-label="Series code"></div>`).join('')}
        <button class="linkbtn" id="st-addser">${ICONS.plus} Add a series</button></div>
      <div class="st-row study-opts"><label>From <input id="st-start" value="${esc(spec.start || '')}" placeholder="YYYY-MM-DD"></label>
        <label>To <input id="st-end" value="${esc(spec.end || '')}" placeholder="latest"></label>
        <label>Dates <select id="st-join"><option value="both"${spec.join !== 'any' ? ' selected' : ''}>Every series has a value</option><option value="any"${spec.join === 'any' ? ' selected' : ''}>Any series has a value</option></select></label>
        <label>Monthly <select id="st-monthly"><option value="">As published</option>${['average', 'end', 'sum'].map(m => `<option value="${m}"${spec.monthly === m ? ' selected' : ''}>${m === 'average' ? 'Average (rates, prices)' : m === 'end' ? 'Month end (stocks)' : 'Sum (flows)'}</option>`).join('')}</select></label></div>
      <label class="study-q">Query <textarea id="st-query" rows="4" spellcheck="false" placeholder="SELECT * FROM data">${esc(spec.query || '')}</textarea></label>
      <div class="st-row study-opts"><label>Figures <input id="st-figs" value="${esc(figs)}" placeholder="gap: latest, max, highest_since"></label>
        <label>Chart <input id="st-chcols" value="${esc((ch.columns || []).join(', '))}" placeholder="columns, e.g. japan, us"></label>
        <label>Chart title <input id="st-chtitle" value="${esc(ch.title || '')}"></label>
        <label>Unit <input id="st-chunit" value="${esc(ch.unit || '')}" placeholder="%"></label></div>
      <div class="study-run"><button class="btn" id="st-run">Run Query</button><span class="muted small">Runs on Plover's server. No AI, no tokens.</span></div>
      <p class="muted small">Helpers you can use in the query: yoy(x), mom(x), ann3m(x), pct_change(x, n), change(x, n), diff_pp(x, n), spread_pp(a, b), ratio(a, b), share(part, total), rebase(x, 'YYYY-MM-DD'), rolling_mean(x, n), rolling_sum(x, n), cumulative(x). Missing values stay blank.</p>
    </div>`;
  }
  function readSpecForm() {
    const spec = { series: {} };
    $$('[data-sn]').forEach(el => { const n = el.value.trim(), ref = ($('[data-sr="' + el.dataset.sn + '"]') || {}).value || ''; if (n && ref.trim()) spec.series[n] = ref.trim(); });
    const v = id => ($('#' + id) || {}).value || '';
    if (v('st-start').trim()) spec.start = v('st-start').trim();
    if (v('st-end').trim()) spec.end = v('st-end').trim();
    spec.join = v('st-join') || 'both';
    if (v('st-monthly')) spec.monthly = v('st-monthly');
    if (v('st-query').trim()) spec.query = v('st-query').trim();
    const figs = v('st-figs').split(';').map(x => x.trim()).filter(Boolean).map(x => { const p = x.split(':'); return { column: p[0].trim(), stats: (p[1] || 'latest').split(',').map(t => t.trim()).filter(Boolean) }; });
    if (figs.length) spec.summary = figs;
    const cols = v('st-chcols').split(',').map(x => x.trim()).filter(Boolean);
    if (cols.length || v('st-chtitle').trim()) spec.chart = Object.assign({}, cols.length ? { columns: cols } : {}, v('st-chtitle').trim() ? { title: v('st-chtitle').trim() } : {}, v('st-chunit').trim() ? { unit: v('st-chunit').trim() } : {});
    return spec;
  }
  const MEASURE_LABEL = { level: 'As published', yoy: 'Change on a year earlier, %', mom: 'Change on the month before, %', ann3m: 'Three-month change, annualised, %' };
  function recipePanel(cat, spec) {
    const was = (spec && spec.recipe && spec.recipe.id) || cat[0].id;   // reopen on the recipe that ran
    return `<div class="study-recipe">
      <div class="st-row"><label>Analysis <select id="rc-id">${cat.map(r => `<option value="${r.id}"${r.id === was ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select></label>
        <span class="muted small" id="rc-asks"></span></div>
      <div class="study-series" id="rc-slots"></div>
      <div class="st-row study-opts" id="rc-opts"></div>
      <div class="st-row study-opts"><label>From <input id="rc-start" value="${esc(spec.start || '')}" placeholder="YYYY-MM-DD"></label>
        <label>To <input id="rc-end" value="${esc(spec.end || '')}" placeholder="latest"></label>
        <label>Monthly <select id="rc-monthly"><option value="">As published</option><option value="average"${spec.monthly === 'average' ? ' selected' : ''}>Average (rates, prices)</option><option value="end"${spec.monthly === 'end' ? ' selected' : ''}>Month end (stocks)</option><option value="sum"${spec.monthly === 'sum' ? ' selected' : ''}>Sum (flows)</option></select></label></div>
      <div class="study-run"><button class="btn primary" id="rc-run">Run</button><span class="muted small">Built and run by Plover's server. No AI, no tokens.</span></div>
      <div id="rc-err"></div></div>`;
  }
  // A series picker: type a name, pick from the list; the pick is dataset:code.
  function slotHtml(slot, picked, shown) {
    return `<div class="st-row st-slot" data-slot="${esc(slot.name)}" data-code="${esc(picked || '')}"><label>${esc(slot.label)}
      <input class="st-pick" placeholder="Search a series, e.g. core CPI" value="${esc(picked ? (shown ? shown + ' (' + picked + ')' : picked) : '')}" autocomplete="off" aria-label="${esc(slot.label)}"></label>
      <div class="st-picklist" hidden></div></div>`;
  }
  function wireRecipePanel(cat, spec, sources, onRun) {
    const picks = Object.assign({}, (spec && spec.series) || {});
    const titles = {}; (sources || []).forEach(s => { titles[s.name] = s.title || ''; });
    let timer = null;
    function current() { return cat.find(r => r.id === $('#rc-id').value) || cat[0]; }
    function draw() {
      const r = current();
      $('#rc-asks').textContent = 'e.g. ' + r.asks;
      $('#rc-slots').innerHTML = r.slots.map(sl => slotHtml(sl, picks[sl.name], titles[sl.name])).join('');
      const o = r.options || {};
      const had = (spec && spec.recipe && spec.recipe.id === r.id && spec.recipe.options) || {};
      $('#rc-opts').innerHTML = (o.measure ? `<label>Measure <select id="rc-measure">${o.measure.map((m, i) => `<option value="${m}"${(had.measure ? m === had.measure : !i) ? ' selected' : ''}>${esc(MEASURE_LABEL[m] || m)}</option>`).join('')}</select></label>` : '') +
        (o.periods ? `<label>Periods <input id="rc-periods" type="number" min="1" max="240" value="${had.periods || (r.id === 'change' ? 12 : 3)}"></label>` : '') +
        (o.unit ? `<label>Change in <select id="rc-unit">${o.unit.map(u => `<option value="${u}"${had.unit === u ? ' selected' : ''}>${u === 'pp' ? 'percentage points' : 'percent'}</option>`).join('')}</select></label>` : '');
      $$('.st-pick').forEach(inp => {
        const box = inp.closest('.st-slot'); const list = $('.st-picklist', box);
        inp.oninput = () => {
          box.dataset.code = ''; delete picks[box.dataset.slot];
          clearTimeout(timer); const q = inp.value.trim();
          if (!q) { list.hidden = true; return; }
          timer = setTimeout(async () => {
            let found = [];
            try { found = (await api('/studies/series?q=' + encodeURIComponent(q))).series; } catch (e) { found = []; }
            if (inp.value.trim() !== q) return;
            list.hidden = false;
            list.innerHTML = found.length ? found.slice(0, 12).map(f => `<button type="button" data-code="${esc(f.dataset + ':' + f.code)}" data-name="${esc(f.name)}"><span>${esc(f.name)}</span><span class="muted">${esc(f.dataset + ':' + f.code)}${f.unit ? ' · ' + esc(f.unit) : ''}</span></button>`).join('')
              : '<p class="muted">No series match. Try a shorter name, e.g. CPI.</p>';
            $$('button', list).forEach(b => b.onclick = () => { box.dataset.code = b.dataset.code; picks[box.dataset.slot] = b.dataset.code; inp.value = b.dataset.name + ' (' + b.dataset.code + ')'; list.hidden = true; });
          }, 220);
        };
        inp.onkeydown = e => { if (e.key === 'Escape') list.hidden = true; };
      });
    }
    $('#rc-id').onchange = draw;
    draw();
    $('#rc-run').onclick = async () => {
      const r = current();
      const series = {};
      $$('.st-slot').forEach(b => { if (b.dataset.code) series[b.dataset.slot] = b.dataset.code; });
      const missing = r.slots.filter(sl => !sl.optional && !series[sl.name]);
      if (missing.length) { $('#rc-err').innerHTML = `<p class="err">Pick a series for ${esc(missing[0].label.toLowerCase())}: type a name and choose from the list.</p>`; return; }
      const options = {};
      if ($('#rc-measure')) options.measure = $('#rc-measure').value;
      if ($('#rc-periods')) options.periods = Number($('#rc-periods').value) || undefined;
      if ($('#rc-unit')) options.unit = $('#rc-unit').value;
      $('#rc-err').innerHTML = working('Running on Plover\'s server.');
      try { await onRun({ recipe: r.id, series, start: $('#rc-start').value.trim(), end: $('#rc-end').value.trim(), monthly: $('#rc-monthly').value, options }); }
      catch (e) { $('#rc-err').innerHTML = `<p class="err">${esc(e.detail || 'The analysis could not run.')}</p>`; }
    };
  }
  const BY_LABEL = { analyst: 'The Analyst', you: 'You', schedule: 'Scheduled refresh', library: 'Your past study' };
  const byLabel = by => BY_LABEL[by] || esc(String(by).replace(/^ext:/, ''));
  function resultHtml(st, run) {
    if (!run) return '<p class="muted">No run yet. Ask a question above, or fill in the analysis below and press Run Query.</p>';
    const tk = run.tokens || {};
    const by = run.by === 'analyst' || BY_LABEL[run.by] ? byLabel(run.by) : 'Your AI (' + byLabel(run.by) + ')';
    const cost = run.by === 'analyst' && tk.in != null ? ` · ${fmtNum(tk.in + (tk.out || 0), 0)} tokens${tk.cached ? ' (' + fmtNum(tk.cached, 0) + ' from cache)' : ''}` : (run.by === 'you' || run.by === 'schedule' || run.by === 'library' ? ' · no AI used' : '');
    const shown = run.rows.slice(0, 200);
    const dps = run.columns.map((c, i) => colDp(run.rows, i));
    const isNum = run.columns.map((c, i) => run.rows.some(r => typeof r[i] === 'number'));
    return `<p class="study-by muted small">${by} · ${when(run.created_at)}${cost}${run.question ? ' · asked: “' + esc(run.question) + '”' : ''}</p>
      ${run.note ? `<div class="study-note">${md(run.note)}</div>` : ''}
      ${figuresHtml(run.summary, run.sources)}
      ${run.chart ? chartsHtml([run.chart], 'study') : (run.chart_error ? `<p class="err">${esc(run.chart_error)}</p>` : '')}
      <h2 class="band">Result <span class="h2-note">${fmtNum(run.rows_total, 0)} rows</span></h2>
      <div class="study-table"><table class="t"><thead><tr>${run.columns.map((c, i) => `<th${isNum[i] ? ' class="right"' : ''}>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${shown.map(r => `<tr>${r.map((v, i) => `<td${isNum[i] ? ' class="right"' : ''}>${cell(v, dps[i])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="muted small study-foot">${run.rows_total > shown.length ? 'Showing the first ' + shown.length + ' of ' + fmtNum(run.rows_total, 0) + ' rows. ' : ''}<a href="${API}/studies/${st.id}/runs/${run.id}/csv">Download CSV</a> with every row, its sources and the query. <a href="${API}/studies/${st.id}/runs/${run.id}/python">Download for Python</a>: the inputs, the query as plain SQL and a script to run it on your own machine. Sources: ${run.sources.map(s => esc(s.name + ' = ' + (s.title || s.series) + ' (' + (s.release || 'latest') + ')')).join('; ')}.</p>`;
  }
  async function vStudies() {
    const d = await api('/studies');
    const arch = location.hash.indexOf('archived') !== -1;
    const list = arch ? (await api('/studies?archived=1')).studies : d.studies;
    $('#main').innerHTML = topbar([['Studies']]) + `<div class="content"><div class="page narrow">${pageHead('Studies', 'Ask a question about Plover\'s data in plain English. The Analyst writes the query and Plover\'s server runs it, so your AI reads a short preview, not the whole history. Every run is kept.', '<button class="btn" id="st-blank">Build a Study</button>')}
      ${setupNotice()}
      <div class="ask study-start"><input id="st-new" placeholder="${esc('e.g. ' + STUDY_EXAMPLE)}" aria-label="Question for a new study"><button class="btn primary" id="st-go">Start Study</button></div>
      <div id="st-wait"></div><div id="st-undo"></div>
      <h2 class="band">${arch ? 'Archived Studies' : 'Your Studies'} <span class="h2-note">${list.length}</span></h2>
      ${list.length ? `<ul class="rows">${list.map(x => `<li data-id="${x.id}"><div data-go="studies/${x.id}"><b>${esc(x.title)}</b></div><time class="muted small">${x.runs ? x.runs + (x.runs === 1 ? ' run' : ' runs') + ' · ' + when(x.last_run_at) : 'not run yet'}</time>${arch ? `<button class="btn sm" data-restore="${x.id}">Restore</button>` : `<button class="btn sm" data-archive="${x.id}" aria-label="Archive ${esc(x.title)}">Archive</button>`}</li>`).join('')}</ul>` : `<p class="muted">${arch ? 'Nothing archived.' : 'No studies yet. Ask a question above, or press Build a Study to pick an analysis yourself.'}</p>`}
      <p class="muted small" style="margin-top:12px">${arch ? '<a href="#/studies">Back to your studies</a>' : (d.archived ? `<a href="#/studies/archived">Archived (${d.archived})</a>` : '')}</p></div></div>`;
    const start = async () => {
      const q = $('#st-new').value.trim() || STUDY_EXAMPLE;
      $('#st-wait').innerHTML = working('Starting the study. The Analyst is finding the series and running the query on Plover\'s server.');
      $('#st-new').disabled = true;
      let made = null;
      try { made = await api('/studies', { method: 'POST', body: { question: q } }); await api('/studies/' + made.study.id + '/ask', { method: 'POST', body: { question: q } }); go('studies/' + made.study.id); }
      catch (e) { if (made) { toast(e.detail || 'The Analyst could not finish'); go('studies/' + made.study.id); } else { $('#st-wait').innerHTML = `<p class="err">${esc(e.detail || 'Could not start the study.')}</p>`; $('#st-new').disabled = false; } }
    };
    $('#st-go').onclick = start; $('#st-new').onkeydown = e => { if (e.key === 'Enter') start(); };
    $('#st-blank').onclick = async () => { const m = await api('/studies', { method: 'POST', body: { title: 'Untitled study' } }); go('studies/' + m.study.id); };
    $$('[data-archive]').forEach(b => b.onclick = async e => {
      e.stopPropagation(); const id = b.dataset.archive; const li = b.closest('li');
      await api('/studies/' + id, { method: 'DELETE' }); li.hidden = true;
      $('#st-undo').innerHTML = `<div class="notice">Archived “${esc($('b', li).textContent)}”. <button class="btn sm" id="st-undo-b">Undo</button></div>`;
      $('#st-undo-b').onclick = async () => { await api('/studies/' + id + '/restore', { method: 'POST' }); render(); };
    });
    $$('[data-restore]').forEach(b => b.onclick = async e => { e.stopPropagation(); await api('/studies/' + b.dataset.restore + '/restore', { method: 'POST' }); toast('Restored'); go('studies'); });
  }
  async function vStudy(r) {
    if (r.id === 'archived') return vStudies();
    const runId = r.sub && /^run\/\d+$/.test(r.sub) ? r.sub.split('/')[1] : '';
    const [d, cat] = await Promise.all([api('/studies/' + r.id + (runId ? '?run=' + runId : '')), api('/studies/recipes')]);
    const st = d.study, run = d.run;
    const spec = (run && run.spec) || st.spec || {};
    $('#main').innerHTML = topbar([['Studies', 'studies'], [st.title]]) + `<div class="content"><div class="page wide">
      <div class="pagehead"><div><input class="study-title" id="st-title" value="${esc(st.title)}" aria-label="Study title"></div>
        <div class="actions"><label class="study-refresh">Refresh <select id="st-refresh" aria-label="Refresh on a schedule"><option value="">Never</option><option value="daily"${st.refresh === 'daily' ? ' selected' : ''}>Daily</option><option value="weekly"${st.refresh === 'weekly' ? ' selected' : ''}>Weekly</option></select></label>
          <label class="study-refresh">Budget <select id="st-budget" aria-label="Most tokens one question may spend"><option value="">Default</option>${BUDGETS.map(b => `<option value="${b}"${st.token_budget === b ? ' selected' : ''}>${fmtNum(b, 0)} tokens</option>`).join('')}${st.token_budget && !BUDGETS.includes(st.token_budget) ? `<option value="${st.token_budget}" selected>${fmtNum(st.token_budget, 0)} tokens</option>` : ''}</select></label></div></div>
      ${setupNotice()}
      <div class="ask study-ask"><input id="st-ask" placeholder="${esc(st.question ? 'Ask a follow-up, e.g. now monthly averages since 2010' : 'e.g. ' + STUDY_EXAMPLE)}" aria-label="Question for the Analyst"><button class="btn primary" id="st-askb">Ask the Analyst</button></div>
      <div id="st-wait"></div>
      ${runId && st.runs.length && String(st.runs[0].id) !== runId ? `<div class="notice">An earlier run. <a href="#/studies/${st.id}">Show the newest</a></div>` : ''}
      <section class="study-result">${resultHtml(st, run)}</section>
      <h2 class="band">Ready-Made Analysis <span class="h2-note">pick and run, no AI</span></h2>
      ${recipePanel(cat.recipes, spec)}
      <h2 class="band">Query <span class="h2-note">the analysis as run; edit and run it yourself</span></h2>
      ${studySpecForm(spec)}
      <div id="st-runerr"></div>
      <h2 class="band">Runs <span class="h2-note">${st.runs.length}</span></h2>
      ${st.runs.length ? `<ul class="rows">${st.runs.map((x, i) => `<li data-go="studies/${st.id}${i ? '/run/' + x.id : ''}"${run && run.id === x.id ? ' class="on"' : ''}><div><b>${byLabel(x.by)}${x.question ? ': ' + esc(x.question) : ''}</b></div><time class="muted small">${fmtNum(x.rows, 0)} rows · ${when(x.created_at)}</time></li>`).join('')}</ul>` : '<p class="muted">No runs yet.</p>'}
    </div></div>`;
    $('#st-budget').onchange = async e => { try { await api('/studies/' + st.id, { method: 'PUT', body: { token_budget: Number(e.target.value) || 0 } }); toast(e.target.value ? 'One question on this study may spend up to ' + fmtNum(Number(e.target.value), 0) + ' tokens' : 'Budget: the server\'s default cap'); } catch (err) { toast(err.detail || 'Could not save'); } };
    $('#st-refresh').onchange = async e => { try { await api('/studies/' + st.id, { method: 'PUT', body: { refresh: e.target.value } }); toast(e.target.value ? 'Refreshes ' + e.target.value + ' on Plover\'s server, no AI' : 'Refresh off'); } catch (err) { toast(err.detail || 'Could not save'); } };
    $('#st-title').onchange = async e => { const t = e.target.value.trim(); if (!t) { e.target.value = st.title; toast('A study needs a title'); return; } try { await api('/studies/' + st.id, { method: 'PUT', body: { title: t } }); toast('Title saved'); } catch (err) { toast(err.detail || 'Could not save the title'); } };
    const ask = async () => {
      const q = $('#st-ask').value.trim(); if (!q) { $('#st-ask').focus(); toast('Type a question first'); return; }
      $('#st-wait').innerHTML = working('The Analyst is writing the query and running it on Plover\'s server. This can take a minute.');
      $('#st-ask').disabled = true;
      try { await api('/studies/' + st.id + '/ask', { method: 'POST', body: { question: q } }); go('studies/' + st.id); render(); }
      catch (e) { $('#st-wait').innerHTML = `<p class="err">${esc(e.detail || 'The Analyst could not finish.')} You can also fill in the analysis below and press Run Query.</p>`; $('#st-ask').disabled = false; }
    };
    $('#st-askb').onclick = ask; $('#st-ask').onkeydown = e => { if (e.key === 'Enter') ask(); };
    wireRecipePanel(cat.recipes, spec, run && run.sources, async body => { await api('/studies/' + st.id + '/recipe', { method: 'POST', body }); go('studies/' + st.id); render(); });
    $('#st-addser').onclick = e => { e.preventDefault(); const box = $('.study-series'); const i = $$('[data-sn]').length; if (i >= 6) { toast('Up to six series'); return; } const row = document.createElement('div'); row.className = 'st-row'; row.innerHTML = `<input class="st-name" data-sn="${i}" placeholder="name" aria-label="Series name"><input class="st-ref" data-sr="${i}" placeholder="dataset:code" aria-label="Series code">`; box.insertBefore(row, e.currentTarget); };
    $('#st-run').onclick = async () => {
      const spec2 = readSpecForm();
      if (!Object.keys(spec2.series).length) { $('#st-runerr').innerHTML = '<p class="err">Name at least one series, e.g. japan = jgb-yields:10Y. Find codes with Ask the Analyst or on the dataset\'s page.</p>'; return; }
      $('#st-runerr').innerHTML = working('Running on Plover\'s server.');
      try { await api('/studies/' + st.id + '/run', { method: 'POST', body: { spec: spec2 } }); go('studies/' + st.id); render(); }
      catch (e) { $('#st-runerr').innerHTML = `<p class="err">${esc(e.detail || 'The query could not run.')}</p>`; }
    };
  }

  /* ---- Files */
  async function vFiles(r) {
    const d = await api('/files');
    const sel = r.id && r.sub ? { hire_id: Number(r.id), path: r.sub } : (d.files[0] || null);
    let content = null;
    if (sel) { try { content = await api('/files/' + sel.hire_id + '/' + sel.path.split('/').map(encodeURIComponent).join('/')); } catch (e) { content = null; } }
    const byHire = {}; d.files.forEach(f => (byHire[f.hire_id] = byHire[f.hire_id] || []).push(f));
    const tree = Object.keys(byHire).map(id => `<div class="dir">${esc((specOf(hireById(id)).name || 'specialist'))}/</div>${byHire[id].map(f => `<button class="${sel && sel.hire_id === Number(id) && sel.path === f.path ? 'on' : ''}" data-go="files/${id}/${encodeURIComponent(f.path)}">${esc(f.path)}</button>`).join('')}`).join('');
    $('#main').innerHTML = topbar([['Files']]) + `<div class="content"><div class="page wide">${pageHead('Files', 'Everything a specialist writes is a file you can read.')}
      ${d.files.length ? `<div class="files"><nav class="filetree">${tree}</nav><section class="panel"><header>${esc(sel ? sel.path : '')}<span class="grow"></span><small>Read-only${content ? ' · ' + when(content.updated_at) : ''}</small></header><div class="body"><pre>${esc(content ? content.content : 'Could not read this file.')}</pre></div></section></div>` : '<p class="muted">No files yet. A specialist writes here when it is asked to keep notes or exports.</p>'}</div></div>`;
  }

  /* ---- Drive: files kept on the desk, and the person's Google Drive (read-only) */
  const G_TYPES = { 'application/vnd.google-apps.document': 'Google Doc', 'application/vnd.google-apps.spreadsheet': 'Google Sheet', 'application/vnd.google-apps.presentation': 'Google Slides', 'application/vnd.google-apps.form': 'Google Form', 'application/vnd.google-apps.shortcut': 'Shortcut' };
  function fsize(b) {
    if (b == null) return '—';
    if (b < 1024) return n(b) + ' B';
    const u = ['KB', 'MB', 'GB']; let v = b / 1024, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 ? 1 : 0) + ' ' + u[i];
  }
  const gWhen = iso => iso ? when(Math.floor(Date.parse(iso) / 1000)) : '—';
  let drvUi = { mode: null, id: null, sel: new Set(), crumbs: [] };
  let drvFlash = null;
  (function () {
    const p = new URLSearchParams(location.search);
    if (p.get('google') === 'connected') drvFlash = 'Google Workspace connected';
    if (p.get('google_error')) drvFlash = p.get('google_error');
    if (drvFlash) try { history.replaceState(null, '', location.pathname + location.hash); } catch (e) { }
  })();

  async function vDrive(r) {
    const view = !r.id ? 'mydrive' : r.id === 'f' ? 'mydrive' : r.id;
    const here = location.hash; if (drvUi.at !== here) { drvUi.sel.clear(); drvUi.mode = null; drvUi.at = here; }
    const folder = r.id === 'f' ? Number(r.sub) || 0 : 0;
    const gFolder = r.id === 'google' ? (r.sub || '') : '';
    const local = ['mydrive', 'recent', 'trash'].includes(view) ? await api('/drive?view=' + view + (folder ? '&folder=' + folder : '')) : null;
    const gs = local ? local.google : await api('/google/status');
    const usage = local ? local.usage : (await api('/drive?view=recent')).usage;
    let g = null, gErr = null;
    if (gs.connected && (view === 'shared' || view === 'google' || view === 'recent')) {
      try { g = await api('/google/files?view=' + (view === 'google' ? 'mydrive' : view) + (gFolder ? '&folder=' + encodeURIComponent(gFolder) : '')); } catch (e) { gErr = e.detail || 'Google Drive could not be read.'; }
    }
    const side = (k, label, icon, go_) => `<button class="${view === k ? 'on' : ''}" data-go="${go_}">${ICONS[icon]}<span>${label}</span></button>`;
    const sideHtml = `<nav class="dside"><div class="dsec">Files</div>${side('mydrive', 'My drive', 'drive', 'drive')}${side('shared', 'Shared with me', 'shared', 'drive/shared')}${side('recent', 'Recent', 'clock', 'drive/recent')}
      <div class="dsec">Storage</div>${side('trash', 'Trash', 'trash', 'drive/trash')}${side('cloud', 'Cloud storage', 'cloud', 'drive/cloud')}${gs.connected ? side('google', 'Google Drive', 'gdrive', 'drive/google') : ''}
      <div class="dusage"><div class="bar"><i style="width:${Math.min(100, usage.used / usage.limit * 100).toFixed(1)}%"></i></div><span>${fsize(usage.used)} of ${fsize(usage.limit)} used</span></div></nav>`;
    const needGoogle = what => `<div class="dempty">${ICONS.cloud}<b>Connect Google Workspace</b><p>${what}</p><a class="btn primary" href="#/drive/cloud">Open Cloud storage</a></div>`;

    let crumbs, actions = '', body;
    const localRow = it => {
      const trashView = view === 'trash';
      const open = it.kind === 'folder' ? (trashView ? `<span class="dname">${ICONS.folder}<b>${esc(it.name)}</b></span>` : `<a class="dname" href="#/drive/f/${it.id}">${ICONS.folder}<b>${esc(it.name)}</b></a>`)
        : `<a class="dname" href="${API}/drive/items/${it.id}/download">${ICONS.file}<b>${esc(it.name)}</b></a>`;
      const acts = trashView ? `<button class="linkbtn" data-restore="${it.id}">Restore</button><button class="linkbtn warn" data-purge="${it.id}">Delete forever</button>`
        : `${it.kind === 'file' ? `<a class="linkbtn" href="${API}/drive/items/${it.id}/download">Download</a>` : ''}<button class="linkbtn" data-rename="${it.id}">Rename</button><button class="linkbtn" data-trash="${it.id}">Move to Trash</button>`;
      return `<tr><td class="ck">${view === 'recent' ? '' : `<input type="checkbox" data-sel="${it.id}" ${drvUi.sel.has(it.id) ? 'checked' : ''} aria-label="Select ${esc(it.name)}">`}</td><td class="nm">${open}</td>${view === 'recent' ? '<td class="muted nowrap meta">My drive</td>' : ''}<td class="nowrap meta"><time>${when(trashView ? it.trashed_at : it.updated_at)}</time></td><td class="right">${it.kind === 'folder' ? '—' : fsize(it.size)}</td><td class="acts">${acts}</td></tr>`;
    };
    const gRow = it => {
      const type = G_TYPES[it.mime];
      const name = it.kind === 'folder' ? `<a class="dname" href="#/drive/google/${encodeURIComponent(it.id)}" data-gname="${esc(it.name)}">${ICONS.folder}<b>${esc(it.name)}</b></a>`
        : `<a class="dname" href="${esc(it.link || '#')}" target="_blank" rel="noopener">${ICONS.file}<b>${esc(it.name)}</b>${type ? `<small>${type}</small>` : ''}</a>`;
      return `<tr><td class="ck"></td><td class="nm">${name}</td>${view === 'recent' ? '<td class="muted nowrap meta">Google Drive</td>' : ''}${view === 'shared' ? `<td class="muted meta">${esc(it.owner || '—')}</td>` : ''}<td class="nowrap meta"><time>${gWhen(it.modified)}</time></td><td class="right" title="${it.size == null ? 'Google formats have no file size' : ''}">${fsize(it.size)}</td><td class="acts">${it.link ? `<a class="linkbtn" href="${esc(it.link)}" target="_blank" rel="noopener">Open in Google</a>` : ''}</td></tr>`;
    };
    const table = (rows, extraHead) => `<table class="t dtable"><thead><tr><th class="ck">${view === 'mydrive' || view === 'trash' ? '<input type="checkbox" id="d-all" aria-label="Select all">' : ''}</th><th>Name</th>${extraHead || ''}<th>${view === 'trash' ? 'Deleted' : 'Modified'}</th><th class="right">Size</th><th class="acts"><span class="sr">Actions</span></th></tr></thead><tbody>${rows}</tbody></table>`;
    const emptyHere = `<div class="dempty">${ICONS.drive}<b>This folder is empty</b><p>Drop files here or click Upload to add them to this folder.</p><div class="row"><button class="btn" data-dnew>${ICONS.folderplus} New folder</button><button class="btn primary" data-dup>${ICONS.upload} Upload</button></div></div>`;

    if (view === 'mydrive') {
      crumbs = [['My drive', folder ? 'drive' : null]].concat(local.path.map((p, i) => [p.name, i < local.path.length - 1 ? 'drive/f/' + p.id : null]));
      actions = local.items.length ? `<button class="btn" data-dnew>${ICONS.folderplus} New folder</button><button class="btn primary" data-dup>${ICONS.upload} Upload</button>` : '';
      body = local.items.length ? table(local.items.map(localRow).join('')) : emptyHere;
    } else if (view === 'recent') {
      crumbs = [['Recent']];
      const all = local.items.map(it => ({ t: it.updated_at, h: localRow(it) })).concat(g ? g.items.map(it => ({ t: Date.parse(it.modified) / 1000, h: gRow(it) })) : []).sort((a, b) => b.t - a.t);
      body = (gErr ? `<p class="err">${esc(gErr)}</p>` : '') + (all.length ? table(all.map(x => x.h).join(''), '<th>Location</th>') : `<div class="dempty">${ICONS.clock}<b>Nothing yet</b><p>Files you upload${gs.connected ? ' and files changed in your Google Drive' : ''} show here, newest first.</p></div>`);
    } else if (view === 'trash') {
      crumbs = [['Trash']];
      body = `<p class="muted small dnote">Items in Trash stay until you delete them forever. Specialists cannot see them.</p>` + (local.items.length ? table(local.items.map(localRow).join('')) : `<div class="dempty">${ICONS.trash}<b>Trash is empty</b><p>Things you move to Trash wait here until you restore them or delete them forever.</p></div>`);
    } else if (view === 'shared') {
      crumbs = [['Shared with me']];
      body = !gs.connected ? needGoogle('Shared with me lists the files other people have shared with your Google account.')
        : gErr ? `<p class="err">${esc(gErr)}</p>` : g.items.length ? table(g.items.map(gRow).join(''), '<th>Owner</th>') : `<div class="dempty">${ICONS.shared}<b>Nothing shared with you</b><p>Files shared with ${esc(gs.email || 'your Google account')} show here.</p></div>`;
    } else if (view === 'google') {
      if (!gFolder) drvUi.crumbs = [];
      else if (g && g.folder) { const i = drvUi.crumbs.findIndex(c => c.id === gFolder); drvUi.crumbs = i >= 0 ? drvUi.crumbs.slice(0, i + 1) : drvUi.crumbs.concat([{ id: gFolder, name: g.folder.name }]); }
      crumbs = [['Google Drive', gFolder ? 'drive/google' : null]].concat(drvUi.crumbs.map((c, i) => [c.name, i < drvUi.crumbs.length - 1 ? 'drive/google/' + encodeURIComponent(c.id) : null]));
      actions = `<span class="muted small">Read-only · ${esc(gs.email || '')}</span>`;
      body = !gs.connected ? needGoogle('Browse your Google Drive here, and let specialists read your Docs, Sheets and Slides.')
        : gErr ? `<p class="err">${esc(gErr)}</p>` : g.items.length ? table(g.items.map(gRow).join('')) + (g.next ? '<p class="muted small dnote">Showing the first 100 items.</p>' : '') : `<div class="dempty">${ICONS.folder}<b>This folder is empty</b><p>Add files to it in Google Drive; they show here.</p></div>`;
    } else {
      crumbs = [['Cloud storage']];
      const st = !gs.configured ? `<p class="muted">Google sign-in is not set up on this server yet.</p><p class="muted small">For the administrator: create an OAuth client (Web application) in Google Cloud, enable the Drive API, register <code>${esc(gs.redirect_uri || '')}</code> as a redirect URI, then set <code>GOOGLE_OAUTH_CLIENT_ID</code> and <code>GOOGLE_OAUTH_CLIENT_SECRET</code>.</p>`
        : !gs.keychain ? `<p class="err">This server cannot store keys yet: ASSISTANT_SECRET is not set.</p>`
        : gs.connected ? `<dl class="kv"><dt>Account</dt><dd>${esc(gs.email || '—')}</dd><dt>Connected</dt><dd><time>${when(gs.connected_at)}</time></dd><dt>Last used</dt><dd><time>${gs.last_used_at ? when(gs.last_used_at) : '—'}</time></dd></dl><div class="row"><a class="btn" href="#/drive/google">Browse Google Drive</a><button class="btn danger" id="g-off">Disconnect</button></div>`
        : `<div class="row"><a class="btn primary" href="${API}/google/connect">Connect Google Workspace</a></div><p class="muted small">You sign in on Google's own page and choose the account. Plover never sees your password.</p>`;
      body = `<section class="panel gcard"><header>${ICONS.gdrive}<span>Google Workspace</span><span class="grow"></span>${gs.connected ? '<span class="badge official">Connected</span>' : '<span class="badge">Not connected</span>'}</header><div class="body">
        <p>Drive, Docs, Sheets and Slides. Browse them here, and let your specialists read them when you ask.</p>
        <ul class="gfacts"><li><b>Read-only.</b> The desk can list and read your files. It never creates, edits, moves or deletes anything in your Drive.</li><li><b>Every read is logged.</b> When a specialist opens a file, the call shows in its tool calls and in Audit.</li><li><b>You can withdraw it.</b> Disconnect here, or remove Plover in your Google account's security settings.</li></ul>${st}</div></section>`;
    }
    const selBar = `<div id="d-selbar" class="${drvUi.sel.size ? '' : 'hidden'}"><div class="lbar dsel"><b id="d-seln">${drvUi.sel.size} selected</b><span class="grow"></span>${view === 'trash' ? '<button class="btn" id="d-brestore">Restore</button>' : '<button class="btn" id="d-btrash">Move to Trash</button>'}<button class="btn" id="d-bclear">Clear</button></div></div>`;
    const bar = drvUi.mode === 'new' ? `<div class="lbar"><input id="d-name" placeholder="Folder name" maxlength="200"><button class="btn primary" id="d-create">Create folder</button><button class="btn" data-dcancel>Cancel</button></div>`
      : drvUi.mode === 'rename' ? `<div class="lbar"><input id="d-name" value="${esc((local && local.items.find(x => x.id === drvUi.id) || {}).name || '')}" maxlength="200"><button class="btn primary" id="d-save">Save name</button><button class="btn" data-dcancel>Cancel</button></div>` : '';
    const crumbHtml = `<nav class="dcrumbs">${crumbs.map((c, i) => (i ? '<span class="sep">/</span>' : '') + (c[1] ? `<a href="#/${c[1]}">${esc(c[0])}</a>` : `<b>${esc(c[0])}</b>`)).join('')}</nav>`;
    $('#main').innerHTML = topbar([['Drive']]) + `<div class="content"><div class="page wide"><div class="drive">${sideHtml}<section class="dmain" id="dz"><header class="dhead">${crumbHtml}<div class="grow"></div><div class="actions">${actions}</div></header>${bar}${selBar}${body}<div class="dzhint">Drop to upload to ${esc(crumbs[crumbs.length - 1][0])}</div></section></div></div></div>
      <input type="file" id="d-file" multiple class="hidden">`;
    bindCommon();
    if (drvFlash) { toast(drvFlash); drvFlash = null; }

    const reload = () => render();
    const set = (mode, id) => { drvUi.mode = mode; drvUi.id = id || null; reload(); };
    $$('[data-dnew]').forEach(b => b.onclick = () => set('new'));
    $$('[data-dcancel]').forEach(b => b.onclick = () => set(null));
    $$('[data-rename]').forEach(b => b.onclick = () => set('rename', Number(b.dataset.rename)));
    const nm = $('#d-name'); if (nm) { nm.focus(); nm.select(); nm.onkeydown = e => { if (e.key === 'Enter') ($('#d-create') || $('#d-save')).click(); if (e.key === 'Escape') set(null); }; }
    const cr = $('#d-create'); if (cr) cr.onclick = async () => { try { await api('/drive/folders', { method: 'POST', body: { name: $('#d-name').value, parent_id: folder } }); drvUi.mode = null; toast('Folder created'); reload(); } catch (e) { toast(e.detail || 'Could not create the folder'); } };
    const sv = $('#d-save'); if (sv) sv.onclick = async () => { try { await api('/drive/items/' + drvUi.id, { method: 'PATCH', body: { name: $('#d-name').value } }); drvUi.mode = null; toast('Renamed'); reload(); } catch (e) { toast(e.detail || 'Could not rename'); } };
    $$('[data-trash]').forEach(b => b.onclick = async () => { await api('/drive/items/' + b.dataset.trash + '/trash', { method: 'POST' }); toast('Moved to Trash'); reload(); });
    $$('[data-restore]').forEach(b => b.onclick = async () => { await api('/drive/items/' + b.dataset.restore + '/restore', { method: 'POST' }); toast('Restored'); reload(); });
    $$('[data-purge]').forEach(b => b.onclick = async () => { if (!confirm('Delete this forever? It cannot be recovered.')) return; await api('/drive/items/' + b.dataset.purge, { method: 'DELETE' }); toast('Deleted'); reload(); });
    // Selection repaints only the bar and the boxes; no round trip.
    const ids = $$('[data-sel]').map(c => Number(c.dataset.sel));
    const all = $('#d-all');
    const paintSel = () => {
      $$('[data-sel]').forEach(c => { c.checked = drvUi.sel.has(Number(c.dataset.sel)); });
      if (all) all.checked = ids.length > 0 && ids.every(i => drvUi.sel.has(i));
      $('#d-selbar').classList.toggle('hidden', !drvUi.sel.size); $('#d-seln').textContent = drvUi.sel.size + ' selected';
    };
    $$('[data-sel]').forEach(c => c.onchange = () => { const id = Number(c.dataset.sel); c.checked ? drvUi.sel.add(id) : drvUi.sel.delete(id); paintSel(); });
    if (all) all.onchange = () => { ids.forEach(i => all.checked ? drvUi.sel.add(i) : drvUi.sel.delete(i)); paintSel(); };
    paintSel();
    const bulk = async (path, msg) => { for (const id of drvUi.sel) { try { await api('/drive/items/' + id + path, { method: 'POST' }); } catch (e) { } } toast(msg); drvUi.sel.clear(); reload(); };
    const bt = $('#d-btrash'); if (bt) bt.onclick = () => bulk('/trash', 'Moved to Trash');
    const br = $('#d-brestore'); if (br) br.onclick = () => bulk('/restore', 'Restored');
    const bc = $('#d-bclear'); if (bc) bc.onclick = () => { drvUi.sel.clear(); paintSel(); };
    const off = $('#g-off'); if (off) off.onclick = async () => { if (!confirm('Disconnect Google Workspace? Specialists lose access to your Drive at once.')) return; await api('/google', { method: 'DELETE' }); toast('Google Workspace disconnected'); reload(); };

    // Upload: the Upload button, or drop files anywhere on My drive.
    const upload = async files => {
      const list = Array.from(files || []); if (!list.length) return;
      let done = 0;
      for (const f of list) {
        toast('Uploading ' + (done + 1) + ' of ' + list.length);
        try {
          const res = await fetch(API + '/drive/upload?name=' + encodeURIComponent(f.name) + (folder ? '&parent_id=' + folder : ''), { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': f.type || 'application/octet-stream' }, body: f });
          if (!res.ok) { let d = {}; try { d = await res.json(); } catch (e) { } toast(f.name + ': ' + (d.detail || 'upload failed')); await new Promise(ok => setTimeout(ok, 1600)); continue; }
          done++;
        } catch (e) { toast(f.name + ': upload failed'); }
      }
      if (done) toast(done === 1 ? 'Uploaded' : done + ' files uploaded');
      reload();
    };
    $$('[data-dup]').forEach(b => b.onclick = () => $('#d-file').click());
    $('#d-file').onchange = e => upload(e.target.files);
    const dz = $('#dz');
    if (view === 'mydrive') {
      dz.ondragover = e => { if (Array.from(e.dataTransfer.types || []).includes('Files')) { e.preventDefault(); dz.classList.add('over'); } };
      dz.ondragleave = e => { if (!dz.contains(e.relatedTarget)) dz.classList.remove('over'); };
      dz.ondrop = e => { e.preventDefault(); dz.classList.remove('over'); upload(e.dataTransfer.files); };
    }
  }

  /* ---- Audit */
  const monthName = ym => { const [y, m] = ym.split('-').map(Number); return new Date(Date.UTC(y, m - 1, 1)).toLocaleString('en', { month: 'long', year: 'numeric', timeZone: 'UTC' }); };
  async function vAudit() {
    const d = await api('/audit');
    $('#main').innerHTML = topbar([['Audit']]) + `<div class="content"><div class="page">${pageHead('Audit', 'Every tool call your specialists made. They have no other way to reach a number.')}
      <div class="stats">${stat('Tool calls', n(d.counts.tool_calls), 'all runs')}${stat('Runs', n(d.counts.runs), 'scheduled and on request')}${stat('Waiting', n(d.counts.pending), 'for your approval')}${stat('Sent outward', n(d.counts.sent), 'all approved by you')}</div>
      <h2 class="band">AI use by month <span class="h2-note">your account</span></h2><div class="panel">${(d.months || []).some(m => m.questions) ? `<div class="tw"><table class="t"><thead><tr><th>Month</th><th class="right">Questions</th><th class="right">Tokens in</th><th class="right">Tokens out</th><th class="right">Per question</th></tr></thead><tbody>${d.months.map(m => `<tr><td class="nowrap">${esc(monthName(m.month))}</td><td class="right">${n(m.questions)}</td><td class="right">${n(m.tokens_in)}</td><td class="right">${n(m.tokens_out)}</td><td class="right">${m.per_question == null ? '—' : n(m.per_question)}</td></tr>`).join('')}</tbody></table></div><p class="muted small" style="margin:8px 12px 12px">Counts every question a model answered: chat, specialists and the Study Analyst. Ready-made analyses, hand-run queries, scheduled refreshes and answers reused from a past study use no model and are not counted.</p>` : '<p class="empty">No model has answered a question yet.</p>'}</div>
      <h2 class="band">Tools</h2><div class="panel">${d.tools.length ? `<div class="tw"><table class="t"><thead><tr><th>Tool</th><th>Used by</th><th class="right">Calls</th></tr></thead><tbody>${d.tools.map(t => `<tr><td><span class="code">${esc(t.name)}</span></td><td>${t.used_by.map(s => esc(s === 'connection' ? 'Outside agents' : (specOf(hireBySlug(s)).name || s))).join(', ')}</td><td class="right">${n(t.calls)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No tool calls yet.</p>'}</div>
      <h2 class="band">Recent runs</h2><div class="panel">${d.runs.length ? `<div class="tw"><table class="t"><thead><tr><th>When</th><th>Specialist</th><th>Trigger</th><th class="right">Calls</th><th class="right">Tokens</th><th>Result</th></tr></thead><tbody>${d.runs.map(x => `<tr><td class="nowrap">${when(x.started_at)}</td><td>${esc(x.slug === 'connection' ? x.trigger + ' (connection)' : (specOf(hireById(x.hire_id)).name || x.slug))}</td><td>${esc(x.slug === 'connection' ? 'outside agent' : x.trigger)}</td><td class="right">${n(x.tool_calls)}</td><td class="right">${n(x.tokens_in + x.tokens_out)}</td><td>${esc(outcomeLabel(x.outcome))}${x.error ? `<small>${esc(x.error)}</small>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No runs yet.</p>'}</div></div></div>`;
  }

  /* ---- System: MCP tools */
  let toolsUi = { adding: false };
  async function vTools() {
    const d = await api('/mcp-servers');
    const form = toolsUi.adding ? `<section class="panel addsrv"><header>Add an MCP server</header><div class="body">
        <dl class="kv">
          <dt>Name</dt><dd><input id="ms-label" placeholder="Our research server" maxlength="60"></dd>
          <dt>URL</dt><dd><input id="ms-url" placeholder="https://tools.yourfirm.com/mcp"><span class="hint">https only, and not an address inside a private network</span></dd>
          <dt>Authentication</dt><dd><select id="ms-auth"><option value="none">None</option><option value="bearer">Bearer token</option><option value="header">Custom header</option></select></dd>
          <dt id="ms-hname-l" class="hidden">Header name</dt><dd id="ms-hname-d" class="hidden"><input id="ms-hname" placeholder="X-API-Key"></dd>
          <dt id="ms-key-l" class="hidden">Key</dt><dd id="ms-key-d" class="hidden"><select id="ms-key">${d.secrets.map(k => `<option value="${k.id}">${esc(k.name)} · ends ${esc(k.last4)}</option>`).join('') || '<option value="">No keys stored yet</option>'}</select><a class="linkbtn" href="#/keys">Add a key</a></dd>
          <dt></dt><dd><button class="btn primary" id="ms-save">Connect server</button><button class="btn" id="ms-cancel">Cancel</button><span class="hint" id="ms-msg"></span></dd>
        </dl></div></section>` : '';
    const card = m => {
      const st = m.status === 'ok' ? `<span class="ok">${ICONS.check}Connected</span>` : m.status === 'error' ? `<span class="bad">Not connected</span>` : '<span class="muted">Not checked yet</span>';
      return `<article class="srv"><header><div><b>${esc(m.label)}</b><small class="mono">${esc(m.url)}</small></div><div class="srvstate">${st}<small>${m.status === 'ok' ? m.tools.length + ' tool' + (m.tools.length === 1 ? '' : 's') : esc(m.detail || '')}</small></div></header>
        ${m.tools.length ? `<div class="srvtools">${m.tools.slice(0, 12).map(t => `<span class="chip ro" title="${esc(t.description || '')}">${esc(t.name)}</span>`).join('')}${m.tools.length > 12 ? `<span class="muted small">and ${m.tools.length - 12} more</span>` : ''}</div>` : ''}
        <footer><label class="switch ${m.enabled ? 'on' : ''}" data-enable="${m.id}"><i></i>${m.enabled ? 'Specialists may use it' : 'Off'}</label>
          <span class="muted small">${m.auth_kind === 'none' ? 'No authentication' : 'Key: ' + esc((d.secrets.find(k => k.id === m.secret_id) || {}).name || 'stored')}${m.checked_at ? ' · checked ' + when(m.checked_at) : ''}</span>
          <span class="grow"></span><button class="linkbtn" data-check="${m.id}">Check now</button><button class="linkbtn quiet" data-delsrv="${m.id}">Remove</button></footer></article>`;
    };
    $('#main').innerHTML = topbar([['MCP tools']]) + `<div class="content"><div class="page">
      ${pageHead('MCP tools', 'Servers whose tools every specialist on this desk can use. Add one once and its tools appear in every run, with each call recorded in the Audit.', toolsUi.adding ? '' : `<button class="btn primary" id="ms-add">${ICONS.plus} Add MCP server</button>`)}
      ${d.keychain ? '' : '<div class="notice">Keys cannot be stored on this server yet (<code>ASSISTANT_SECRET</code> is not set), so only servers without authentication can be added.</div>'}
      ${form}
      ${d.servers.length ? d.servers.map(card).join('') : (toolsUi.adding ? '' : `<div class="covempty"><b>No MCP servers yet.</b><span>Connect your firm's own tools, or a vendor's, and every specialist here can call them.</span></div>`)}
      <p class="fine" style="margin-top:32px">A server you add is called by this desk, with the key you chose. Addresses inside a private network are refused. What an outside tool does with what it is sent is outside our control, so add only servers you trust; your specialists' own outward actions still wait for your approval.</p>
    </div></div>`;
    const setAdding = v => { toolsUi.adding = v; vTools().then(bindCommon); };
    const addBtn = $('#ms-add'); if (addBtn) addBtn.onclick = () => setAdding(true);
    const cancel = $('#ms-cancel'); if (cancel) cancel.onclick = () => setAdding(false);
    const auth = $('#ms-auth');
    if (auth) {
      const sync = () => {
        const kind = auth.value;
        ['ms-key-l', 'ms-key-d'].forEach(id => $('#' + id).classList.toggle('hidden', kind === 'none'));
        ['ms-hname-l', 'ms-hname-d'].forEach(id => $('#' + id).classList.toggle('hidden', kind !== 'header'));
      };
      auth.onchange = sync; sync();
      $('#ms-save').onclick = async () => {
        const msg = $('#ms-msg'); msg.textContent = 'Connecting';
        try {
          const srv = await api('/mcp-servers', { method: 'POST', body: {
            label: $('#ms-label').value, url: $('#ms-url').value, auth_kind: auth.value,
            header_name: $('#ms-hname').value, secret_id: +($('#ms-key').value || 0) } });
          toolsUi.adding = false;
          toast(srv.status === 'ok' ? `Connected: ${srv.tools.length} tools` : 'Saved, but it did not answer');
          render();
        } catch (e) { msg.textContent = e.detail || 'Could not add the server'; }
      };
    }
    $$('[data-check]').forEach(b => b.onclick = async () => { b.textContent = 'Checking'; try { const m = await api('/mcp-servers/' + b.dataset.check + '/check', { method: 'POST' }); toast(m.status === 'ok' ? m.tools.length + ' tools' : (m.detail || 'Not connected')); } catch (e) { toast(e.detail || 'Check failed'); } render(); });
    $$('[data-enable]').forEach(l => l.onclick = async () => { const on = !l.classList.contains('on'); await api('/mcp-servers/' + l.dataset.enable, { method: 'PATCH', body: { enabled: on } }); render(); });
    $$('[data-delsrv]').forEach(b => b.onclick = async () => {
      const foot = b.parentElement;
      foot.innerHTML = `<span class="muted small">Remove this server? Its tools disappear from every specialist.</span><span class="grow"></span><button class="btn sm danger" id="ds-ok">Remove</button><button class="btn sm" id="ds-no">Cancel</button>`;
      $('#ds-no').onclick = () => render();
      $('#ds-ok').onclick = async () => { await api('/mcp-servers/' + b.dataset.delsrv, { method: 'DELETE' }); toast('Server removed'); render(); };
    });
  }

  /* ---- System: data sources */
  // The desk owner's own market-data accounts. A key is tried against the
  // vendor before it is kept; "Show prices" proves the connection on a symbol.
  let dataUi = { connecting: '', tried: {} };
  /* ---- Memory: the notes the assistant keeps about this person (memory-panel.js) */
  async function vMemory() {
    $('#main').innerHTML = topbar([['Memory']]) + `<div class="content"><div class="page narrow">${pageHead('Memory', 'What your assistant has learned about you: what you cover, the views you hold, how you like things written. It saves a note on its own when you tell it something worth keeping, and shows each save under its reply with Undo. Chat starts with these notes, and so does the AI tab in the Writer Desk. Only you can see them. Notes are about you, never a source of figures.')}<div id="mem-box"></div></div></div>`;
    await MemoryPanel.mount($('#mem-box'), { call: (method, path, body) => api(path, { method: method, body: body }), toast: toast });
  }

  async function vData() {
    const d = await api('/data-sources');
    const bySlug = {}; d.providers.forEach(p => { bySlug[p.slug] = p; });
    const fmtDay = iso => { const x = new Date(iso + 'T00:00:00Z'); return x.getUTCDate() + ' ' + MON[x.getUTCMonth()] + ' ' + x.getUTCFullYear(); };
    const dpOf = bars => { let dp = 0; bars.forEach(b => ['open', 'high', 'low', 'close', 'adj_close'].forEach(k => { const v = b[k]; if (v != null) { const t = String(v).split('.')[1]; dp = Math.max(dp, t ? Math.min(t.length, 4) : 0); } })); return dp; };
    const priceTable = out => {
      const rows = out.bars.slice(-10).reverse(), dp = dpOf(rows);
      if (!rows.length) return `<p class="muted small">${esc(out.notes[0] || 'No prices in that range.')}</p>`;
      return `<div class="calwrap"><table class="t pricet"><thead><tr><th>Date</th><th class="right">Open</th><th class="right">High</th><th class="right">Low</th><th class="right">Close</th><th class="right">Adj. close</th><th class="right">Volume</th></tr></thead><tbody>${rows.map(b => `<tr><td class="nowrap">${fmtDay(b.date)}</td>${['open', 'high', 'low', 'close', 'adj_close'].map(k => `<td class="right">${fmtNum(b[k], dp)}</td>`).join('')}<td class="right">${fmtNum(b.volume, 0)}</td></tr>`).join('')}</tbody></table></div>
        <p class="srcline">${esc(out.symbol)} · latest ${rows.length} of ${n(out.bars.length)} trading days from ${fmtDay(out.from)} to ${fmtDay(out.to)}. Source: ${esc(out.source)}, through your own account. Your agreement with the vendor governs its use; not Plover data.${out.notes.length ? ' ' + out.notes.map(esc).join(' ') : ''}</p>`;
    };
    const okState = `<span class="ok">${ICONS.check}Connected</span>`, badState = '<span class="bad">Last try failed</span>';
    const card = s => {
      const p = bySlug[s.provider] || { name: s.provider, covers: '', example: '' };
      const st = s.status === 'error' ? badState : okState;
      const tried = dataUi.tried[s.id];
      return `<article class="srv"><header><div><b>${esc(p.name)}</b><small>${esc(p.covers)}</small></div><div class="srvstate"><span id="dsst-${s.id}">${st}</span><small>Key ending ${esc(s.last4)}${s.last_used_at ? ' · last used ' + when(s.last_used_at) : ''}</small></div></header>
        <div class="srvtry"><label for="try-${s.id}">Symbol</label><input id="try-${s.id}" data-trysym="${s.id}" value="${esc((tried && tried.symbol) || p.example)}" autocomplete="off" spellcheck="false" placeholder="${esc(p.example)}"><button class="btn" data-try="${s.id}">Show prices</button><span class="hint">${esc(p.symbol_hint ? 'Use ' + p.symbol_hint : '')}</span></div>
        <div class="srvout" id="tryout-${s.id}">${tried && tried.out ? priceTable(tried.out) : tried && tried.error ? `<p class="err">${esc(tried.error)}</p>` : (s.status === 'error' && s.detail ? `<p class="err">${esc(s.detail)}</p>` : '')}</div>
        <footer><label class="switch ${s.enabled ? 'on' : ''}" data-dsenable="${s.id}"><i></i>${s.enabled ? 'Specialists may use it' : 'Off'}</label>
          <span class="grow"></span><button class="linkbtn" data-dsrekey="${s.provider}">Replace key</button><button class="linkbtn quiet" data-dsdel="${s.id}">Remove</button></footer></article>`;
    };
    const have = new Set(d.sources.map(s => s.provider));
    const form = p => `<div class="dsform"><dl class="kv">
        <dt>${esc(p.name)} key</dt><dd><input id="ds-key" type="password" autocomplete="off" placeholder="Paste the key from your ${esc(p.name)} account"><span class="hint">${esc(p.key_help)} <a href="${esc(p.signup_url)}" target="_blank" rel="noopener">Open ${esc(p.name)}</a></span></dd>
        <dt></dt><dd><button class="btn primary" id="ds-save" data-provider="${p.slug}">${have.has(p.slug) ? 'Replace key' : 'Connect'}</button><button class="btn" id="ds-cancel">Cancel</button><span class="hint" id="ds-msg"></span></dd></dl></div>`;
    const avail = d.providers.map(p => `<li class="dsrow"><div><b>${esc(p.name)}</b><small>${esc(p.covers)}. ${p.notes.map(esc).join(' ')}</small></div>${have.has(p.slug) ? '<span class="muted small">Connected</span>' : dataUi.connecting === p.slug ? '' : `<button class="btn primary sm" data-dsconnect="${p.slug}">Connect</button>`}${dataUi.connecting === p.slug && !have.has(p.slug) ? form(p) : ''}</li>`).join('');
    const rekey = dataUi.connecting && have.has(dataUi.connecting) ? bySlug[dataUi.connecting] : null;
    $('#main').innerHTML = topbar([['Data sources']]) + `<div class="content"><div class="page">
      ${pageHead('Data sources', 'Price data from your own accounts with market-data services. Connect one and every specialist on this desk can read its daily prices. Read-only: nothing here can place or change an order.')}
      ${d.keychain ? '' : '<div class="notice">This server has no <code>ASSISTANT_SECRET</code>, so keys cannot be stored yet and no source can be connected.</div>'}
      <h2 class="band">Connected<span class="h2-note">${d.sources.length}</span></h2>
      ${rekey ? `<section class="panel addsrv"><header>Replace the ${esc(rekey.name)} key</header><div class="body">${form(rekey)}</div></section>` : ''}
      ${d.sources.length ? d.sources.map(card).join('') : '<div class="covempty"><b>No data sources connected.</b><span>Connect one below with a key from your account.</span></div>'}
      <h2 class="band">Available</h2>
      <ul class="rows dslist">${avail}</ul>
      <p class="fine" style="margin-top:32px">These connections are yours: the same ones show under My Account in the Writer Desk, and nobody else can use them. Prices come from your account with each service, under your agreement with it. They are fetched when asked for and are not stored in Plover's databases. Each result names the service as its source. A key is checked with the service before it is kept, then sealed with the server's secret and never shown again. Bloomberg, LSEG and Interactive Brokers need software on your own computer and are not available here yet.</p>
    </div></div>`;
    const setConnecting = v => { dataUi.connecting = v; vData().then(() => { bindCommon(); const k = $('#ds-key'); if (k) k.focus(); }); };
    $$('[data-dsconnect]').forEach(b => b.onclick = () => setConnecting(b.dataset.dsconnect));
    $$('[data-dsrekey]').forEach(b => b.onclick = () => setConnecting(b.dataset.dsrekey));
    const cancel = $('#ds-cancel'); if (cancel) cancel.onclick = () => setConnecting('');
    const save = $('#ds-save');
    if (save) {
      const submit = async () => {
        const msg = $('#ds-msg'), name = (bySlug[save.dataset.provider] || {}).name || 'the service';
        msg.classList.remove('err'); msg.textContent = 'Checking the key with ' + name;
        try {
          const src = await api('/data-sources', { method: 'POST', body: { provider: save.dataset.provider, key: $('#ds-key').value } });
          dataUi.connecting = ''; delete dataUi.tried[src.id];
          toast(name + ' connected'); render();
        } catch (e) { msg.classList.add('err'); msg.textContent = e.detail || 'Could not connect'; }
      };
      save.onclick = submit;
      $('#ds-key').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } };
    }
    const tryOne = async id => {
      const box = $('#tryout-' + id), sym = $('[data-trysym="' + id + '"]').value.trim();
      box.innerHTML = working('Asking the service for ' + (sym || 'prices'));
      try {
        const out = await api('/data-sources/' + id + '/try', { method: 'POST', body: { symbol: sym } });
        dataUi.tried[id] = { symbol: sym, out: out }; box.innerHTML = priceTable(out); $('#dsst-' + id).innerHTML = okState;
      } catch (e) { dataUi.tried[id] = { symbol: sym, error: e.detail || 'Could not get prices' }; box.innerHTML = `<p class="err">${esc(dataUi.tried[id].error)}</p>`; $('#dsst-' + id).innerHTML = badState; }
    };
    $$('[data-try]').forEach(b => b.onclick = () => tryOne(b.dataset.try));
    $$('[data-trysym]').forEach(i => i.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); const b = $('[data-try="' + i.dataset.trysym + '"]'); if (b && !b.disabled) b.click(); } });
    $$('[data-dsenable]').forEach(l => l.onclick = async () => { const on = !l.classList.contains('on'); await api('/data-sources/' + l.dataset.dsenable, { method: 'PATCH', body: { enabled: on } }); toast(on ? 'Specialists may use it' : 'Switched off for specialists'); render(); });
    $$('[data-dsdel]').forEach(b => b.onclick = () => {
      const foot = b.parentElement;
      foot.innerHTML = `<span class="muted small">Remove this source? Its key is deleted and specialists lose its prices.</span><span class="grow"></span><button class="btn sm danger" id="dd-ok">Remove</button><button class="btn sm" id="dd-no">Cancel</button>`;
      $('#dd-no').onclick = () => render();
      $('#dd-ok').onclick = async () => { await api('/data-sources/' + b.dataset.dsdel, { method: 'DELETE' }); delete dataUi.tried[b.dataset.dsdel]; toast('Data source removed'); render(); };
    });
  }

  /* ---- System: API keys */
  let keysUi = { adding: false, rotate: 0 };
  async function vKeys() {
    const d = await api('/secrets');
    const fmt = ts => { const x = new Date(ts * 1000); return x.getDate() + ' ' + MON[x.getMonth()] + ' ' + x.getFullYear(); };
    const form = keysUi.adding ? `<section class="panel addsrv"><header>Add a key</header><div class="body"><dl class="kv">
        <dt>Name</dt><dd><input id="k-name" placeholder="Firm research server" maxlength="60"><span class="hint">what it is for, so you can tell keys apart</span></dd>
        <dt>Key</dt><dd><input id="k-value" type="password" placeholder="Paste the key"><span class="hint">stored sealed; never shown again</span></dd>
        <dt></dt><dd><button class="btn primary" id="k-save">Store key</button><button class="btn" id="k-cancel">Cancel</button><span class="hint" id="k-msg"></span></dd>
      </dl></div></section>` : '';
    const rows = d.secrets.map(k => keysUi.rotate === k.id
      ? `<tr><td colspan="5"><div class="lbar"><span>New value for <b>${esc(k.name)}</b></span><input id="k-new" type="password" placeholder="Paste the new key"><button class="btn primary" id="k-rot">Replace</button><button class="btn" id="k-rotno">Cancel</button></div></td></tr>`
      : `<tr><td class="ink">${esc(k.name)}${k.note ? `<small class="muted">${esc(k.note)}</small>` : ''}</td><td class="mono">•••• ${esc(k.last4)}</td><td class="muted">${k.used_by.length ? k.used_by.map(esc).join(', ') : 'Not used yet'}</td><td class="muted nowrap">${fmt(k.created_at)}</td><td class="right rowact"><button class="linkbtn" data-rot="${k.id}">Replace</button> <button class="linkbtn quiet" data-delkey="${k.id}">Delete</button></td></tr>`).join('');
    $('#main').innerHTML = topbar([['API keys']]) + `<div class="content"><div class="page">
      ${pageHead('API keys', 'Keys this desk stores for you: sealed with the server\'s secret, never shown again and never returned by the API.', keysUi.adding ? '' : `<button class="btn primary" id="k-add">${ICONS.plus} Add key</button>`)}
      ${d.keychain ? '' : '<div class="notice">This server has no <code>ASSISTANT_SECRET</code>, so keys cannot be stored yet.</div>'}
      ${form}
      <h2 class="band">Your keys</h2>
      ${d.secrets.length ? `<div class="panel"><table class="t"><thead><tr><th>Name</th><th style="width:120px">Key</th><th>Used by</th><th style="width:120px">Added</th><th style="width:150px"></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<div class="covempty"><b>No keys stored.</b><span>Add one here, then choose it when you connect an MCP server.</span></div>`}
      <h2 class="band">Keys the desk already holds</h2>
      <div class="panel"><ul class="kvlist">
        <li><span>Model key${d.model_key.provider ? ' · ' + esc(d.model_key.provider) : ''}</span><b class="${d.model_key.set ? 'ok' : ''}">${d.model_key.set ? ICONS.check + '•••• ' + esc(d.model_key.last4) : 'Not set'}</b></li>
        <li><span>Slack webhook${d.slack.label ? ' · ' + esc(d.slack.label) : ''}</span><b class="${d.slack.set ? 'ok' : ''}">${d.slack.set ? ICONS.check + 'Stored' : 'Not set'}</b></li>
        ${(d.data_sources || []).map(x => `<li><span>Data source · ${esc(x.name)}</span><b class="ok">${ICONS.check}•••• ${esc(x.last4)}</b></li>`).join('')}
      </ul><div class="body" style="border-top:1px solid var(--obs-border)"><a class="linkbtn" href="#/settings">Change these in Settings</a></div></div>
      <p class="fine" style="margin-top:32px">Sealed with the server's own secret before they are written, so a copy of the database cannot be read into working keys. Changing <code>ASSISTANT_SECRET</code> makes every stored key unreadable and they must be entered again.</p>
    </div></div>`;
    const setAdd = v => { keysUi.adding = v; keysUi.rotate = 0; vKeys().then(bindCommon); };
    const add = $('#k-add'); if (add) add.onclick = () => setAdd(true);
    const cancel = $('#k-cancel'); if (cancel) cancel.onclick = () => setAdd(false);
    const save = $('#k-save');
    if (save) save.onclick = async () => { const m = $('#k-msg'); m.textContent = 'Storing'; try { await api('/secrets', { method: 'POST', body: { name: $('#k-name').value, value: $('#k-value').value } }); keysUi.adding = false; toast('Key stored'); render(); } catch (e) { m.textContent = e.detail || 'Could not store the key'; } };
    $$('[data-rot]').forEach(b => b.onclick = () => { keysUi.rotate = +b.dataset.rot; vKeys().then(() => { bindCommon(); const i = $('#k-new'); if (i) i.focus(); }); });
    const rot = $('#k-rot');
    if (rot) { rot.onclick = async () => { try { await api('/secrets/' + keysUi.rotate, { method: 'PATCH', body: { value: $('#k-new').value } }); keysUi.rotate = 0; toast('Key replaced'); render(); } catch (e) { toast(e.detail || 'Could not replace'); } }; $('#k-rotno').onclick = () => { keysUi.rotate = 0; vKeys().then(bindCommon); }; }
    $$('[data-delkey]').forEach(b => b.onclick = async () => {
      const cell = b.parentElement;
      cell.innerHTML = `<span class="muted small">Delete?</span> <button class="btn sm danger" id="dk-ok">Confirm</button> <button class="btn sm" id="dk-no">Cancel</button>`;
      $('#dk-no').onclick = () => vKeys().then(bindCommon);
      $('#dk-ok').onclick = async () => { try { await api('/secrets/' + b.dataset.delkey, { method: 'DELETE' }); toast('Key deleted'); render(); } catch (e) { toast(e.detail || 'Could not delete'); render(); } };
    });
  }

  /* ---- Settings */
  async function vSettings() {
    const [s, cx] = await Promise.all([api('/settings'), api('/connections')]);
    const prov = s.model_provider || 'anthropic';
    $('#main').innerHTML = topbar([['Settings']]) + `<div class="content"><div class="page narrow">${pageHead('Settings', 'Specialists run on your own model key. Plover Analytics never hosts a model.')}
      ${s.keychain ? '' : '<div class="notice">This server has no <code>ASSISTANT_SECRET</code>, so keys cannot be stored yet. Everything else works.</div>'}
      <h2 class="band">Model</h2><div class="panel"><dl class="kv body">
        <dt>Provider</dt><dd><select id="provider">${s.providers.map(p => `<option value="${p.id}" ${p.id === prov ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></dd>
        <dt>Model</dt><dd><input id="model" value="${esc(s.model_name || '')}" placeholder="${esc((s.providers.find(p => p.id === prov) || {}).default_model || '')}"><span class="hint">for research and questions</span></dd>
        <dt>Monitor model</dt><dd><input id="monitor-model" value="${esc(s.monitor_model || '')}" placeholder="same as above"><span class="hint">a cheaper model is enough: the desk does the bookkeeping</span></dd>
        <dt>Studies model</dt><dd><input id="study-model" value="${esc(s.study_model || '')}" placeholder="same as above"><span class="hint">only writes a short query; a cheaper model is enough</span></dd>
        <dt class="keyrow">API key</dt><dd class="keyrow"><input id="key" type="password" placeholder="${s.model_key_set ? 'Stored · ends ' + esc(s.model_key_last4) + ' · paste to replace' : 'Paste your key'}" ${s.keychain ? '' : 'disabled'}></dd>
        ${s.codex && s.codex.available ? `<dt>ChatGPT account</dt><dd id="codex-row">${codexRow(s.codex)}</dd>` : ''}
        <dt></dt><dd><button class="btn primary" id="save-model">Save</button><button class="btn keyrow" id="test-model" ${s.model_key_set ? '' : 'disabled'}>Test</button><span class="hint" id="model-msg"></span></dd></dl></div>
      <h2 class="band">Delivery</h2><div class="panel"><dl class="kv body">
        <dt>Slack webhook</dt><dd><input id="slack" type="password" placeholder="${s.slack_set ? 'Stored · paste to replace' : 'https://hooks.slack.com/services/…'}" ${s.keychain ? '' : 'disabled'}></dd>
        <dt>Channel label</dt><dd><input id="slack-label" value="${esc(s.slack_label || '')}" placeholder="#japan-desk"></dd>
        <dt></dt><dd><button class="btn primary" id="save-slack">Save</button><span class="hint" id="slack-msg"></span></dd></dl></div>
      <h2 class="band">Connections</h2>
      <p class="muted small" style="margin:-4px 0 12px">Connect your own Claude Code or Codex to this desk. It can read the data and your desk, post notes, and ask to post to Slack. Anything outward still waits for you in the Inbox.</p>
      ${cx.connections.length ? `<ul class="rows" style="margin-bottom:12px">${cx.connections.map(c => `<li><div><b>${esc(c.label)}</b><small>Key ending ${esc(c.last4)} · made ${when(c.created_at)} · ${c.last_used_at ? 'last used ' + when(c.last_used_at) : 'not used yet'}</small></div><button class="btn sm" data-revoke="${c.id}">Revoke</button></li>`).join('')}</ul>` : ''}
      <div class="panel"><div class="body"><div class="connect-row"><select id="cx-kind"><option value="Claude Code">Claude Code</option><option value="Codex">Codex</option><option value="Other MCP client">Other MCP client</option></select><button class="btn primary" id="cx-new">Create key</button></div><div id="cx-out"></div></div></div>
      <h2 class="band">Approval policy</h2><ul class="rows">
        <li><div><b>Read data</b><small>Specialists may call any read tool without asking.</small></div><span class="switch on locked"><i></i>Always</span></li>
        <li><div><b>Write to own files</b><small>Notes and exports inside the specialist's workspace.</small></div><span class="switch on locked"><i></i>Always</span></li>
        <li><div><b>Anything outward</b><small>A Slack post waits for you in the Inbox.</small></div><span class="switch on locked"><i></i>Always asks</span></li>
        <li><div><b>Placing orders</b><small>Not possible. No specialist has a trading tool.</small></div><span class="switch locked"><i></i>Never</span></li></ul>
      <p class="fine" style="margin-top:32px">Keys are sealed with the server's secret before they are stored and are never returned by the API. Scheduled runs: <code>python -m app.assistant.runner --all</code> on the server.</p></div></div>`;
    $$('[data-revoke]').forEach(b => b.onclick = async () => { if (!confirm('Revoke this key? Anything using it stops working at once.')) return; await api('/connections/' + b.dataset.revoke, { method: 'DELETE' }); toast('Key revoked'); render(); });
    $('#cx-new').onclick = async () => {
      const kind = $('#cx-kind').value;
      try {
        const t = await api('/connections', { method: 'POST', body: { label: kind } });
        const cmd = connectSteps(kind, t.endpoint, t.token);
        $('#cx-out').innerHTML = `<div class="cx-steps"><p><b>Copy this now.</b> The key is shown once; only its last four characters are kept.</p>${cmd}</div>`;
        $$('#cx-out [data-copy]').forEach(b => b.onclick = () => { const el = document.getElementById(b.dataset.copy); try { navigator.clipboard.writeText(el.textContent); toast('Copied'); } catch (e) { toast('Select and copy by hand'); } });
      } catch (e) { toast(e.detail || 'Could not create a key'); }
    };
    // ChatGPT (Codex) runs on the account connected below, not on a key
    const keyRows = () => { const cx = $('#provider').value === 'codex'; $$('.keyrow').forEach(el => el.classList.toggle('hidden', cx)); };
    $('#provider').onchange = () => { const p = s.providers.find(x => x.id === $('#provider').value); $('#model').placeholder = p ? (p.default_model || (p.id === 'codex' ? 'Codex default' : '')) : ''; keyRows(); };
    $('#provider').onchange();
    bindCodex();
    $('#save-model').onclick = async () => { const m = $('#model-msg'); m.textContent = 'Saving'; try { await api('/settings', { method: 'POST', body: { model_provider: $('#provider').value, model_name: $('#model').value, monitor_model: $('#monitor-model').value, study_model: $('#study-model').value, model_key: $('#key').value } }); m.textContent = 'Saved'; render(); } catch (e) { m.textContent = e.detail || 'Could not save'; } };
    $('#test-model').onclick = async () => { const m = $('#model-msg'); m.textContent = 'Testing'; try { const r = await api('/settings/test', { method: 'POST' }); m.textContent = 'The model answered: ' + (r.reply || 'ok'); } catch (e) { m.textContent = e.detail || 'The test failed'; } };
    $('#save-slack').onclick = async () => { const m = $('#slack-msg'); m.textContent = 'Saving'; try { await api('/settings', { method: 'POST', body: { slack_webhook: $('#slack').value, slack_label: $('#slack-label').value } }); m.textContent = 'Saved'; render(); } catch (e) { m.textContent = e.detail || 'Could not save'; } };
  }

  /* ChatGPT (Codex): the desk signs in with Codex's own device code. The
     person opens OpenAI's page and types the code; the server holds the
     sign-in, sealed, and never sees a password. */
  let codexPoll = null;
  function codexRow(c) {
    if (c.connected) return `<span>Connected${c.email ? ' as <b>' + esc(c.email) + '</b>' : ''}</span> <button class="linkbtn" id="codex-off">Disconnect</button>`;
    if (c.pending) return `<div class="codex-code"><p>1. Open <a href="${esc(c.pending.url)}" target="_blank" rel="noopener">${esc(c.pending.url)}</a> and sign in to ChatGPT.</p>
      <p>2. Enter this one-time code: <code class="big">${esc(c.pending.code)}</code></p>
      <p class="hint">Waiting for you to finish on OpenAI's page. The code expires in 15 minutes.</p></div>`;
    return `<button class="btn" id="codex-on">Connect ChatGPT</button>${c.error ? `<span class="hint err">${esc(c.error)}</span>` : '<span class="hint">answers run on your ChatGPT plan, through Codex on this server</span>'}`;
  }
  function bindCodex() {
    const on = $('#codex-on'), off = $('#codex-off');
    if (on) on.onclick = async () => {
      on.disabled = true; on.textContent = 'Starting';
      try { const r = await api('/settings/codex/login', { method: 'POST' }); $('#codex-row').innerHTML = codexRow({ pending: r }); watchCodex(); }
      catch (e) { toast(e.detail || 'Could not start the sign-in'); on.disabled = false; on.textContent = 'Connect ChatGPT'; }
    };
    if (off) off.onclick = async () => {
      if (!confirm('Disconnect ChatGPT from this desk? Answers stop until you connect again or choose another provider.')) return;
      await api('/settings/codex', { method: 'DELETE' }); toast('ChatGPT disconnected'); render();
    };
  }
  function watchCodex() {
    clearInterval(codexPoll);
    codexPoll = setInterval(async () => {
      const row = $('#codex-row');
      if (!row) { clearInterval(codexPoll); return; }
      try {
        const c = await api('/settings/codex');
        if (c.pending) return;
        clearInterval(codexPoll);
        if (c.connected) toast('ChatGPT connected');
        row.innerHTML = codexRow(c); bindCodex();
      } catch (e) { clearInterval(codexPoll); }
    }, 3000);
  }

  // Setup steps for each client, with the key filled in. Claude Code takes the
  // key as a header on one command; Codex reads it from an environment
  // variable named in its config, so the key never sits in the file.
  function connectSteps(kind, endpoint, token) {
    const block = (id, text, label) => `<div class="cx-block"><div class="cx-head"><span>${esc(label)}</span><button class="linkbtn" data-copy="${id}">Copy</button></div><pre id="${id}">${esc(text)}</pre></div>`;
    if (kind === 'Claude Code') {
      return block('cx-1', `claude mcp add --transport http plover-desk ${endpoint} \\\n  --header "Authorization: Bearer ${token}"`, 'Run in a terminal') +
        `<p class="muted small">Then start <code>claude</code> and ask, for example: “Read my desk and tell me which of my names has a buyback running.” Type <code>/mcp</code> in Claude Code to check it is connected.</p>`;
    }
    if (kind === 'Codex') {
      return block('cx-1', `export PLOVER_DESK_KEY="${token}"`, '1. Add to your shell profile (~/.zshrc)') +
        block('cx-2', `[mcp_servers.plover-desk]\nurl = "${endpoint}"\nbearer_token_env_var = "PLOVER_DESK_KEY"`, '2. Add to ~/.codex/config.toml') +
        `<p class="muted small">Open a new terminal, start <code>codex</code>, and ask it to read your desk.</p>`;
    }
    return block('cx-1', `URL: ${endpoint}\nHeader: Authorization: Bearer ${token}\nTransport: Streamable HTTP (stateless JSON-RPC)`, 'Server details');
  }

  /* ---------------------------------------------------------------- boot */
  window.addEventListener('hashchange', render);
  if (!location.hash) location.hash = '#/desk';
  render();
})();
