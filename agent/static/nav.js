// Shared shell: sidebar + top bar. Include on every page as the LAST script before the page script:
//   <script src="/static/nav.js" data-page="dashboard" data-title="Dashboard"></script>
// It wraps the page's existing body content into .content and exposes renderNavStatus(status), api(), toast(), esc(), money().
(function () {
  const script = document.currentScript;
  const page = script.dataset.page || '';
  const title = script.dataset.title || '';
  const I = {
    dash: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    inbox: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13l3.5 7v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/></svg>',
    people: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/></svg>',
    gear: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    help: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>',
    bell: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>',
    chev: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    logo: '<svg width="26" height="26" viewBox="0 0 28 28" fill="none" aria-hidden="true"><rect x="2" y="2" width="24" height="24" rx="7" fill="#15171A"/><path d="M8 14.5 12 18.5 20 9.5" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const links = [['dashboard', '/', 'Dashboard', I.dash], ['inbox', '/inbox', 'Inbox & review', I.inbox], ['directory', '/directory', 'Suppliers & customers', I.people], ['settings', '/settings', 'Settings', I.gear]];

  // Build the shell around whatever the page already put in <body>
  const existing = Array.from(document.body.childNodes).filter(n => n !== script && !(n.nodeType === 1 && n.tagName === 'SCRIPT' && n !== script));
  const shell = document.createElement('div'); shell.className = 'shell';
  const side = document.createElement('nav'); side.className = 'sidebar'; side.setAttribute('aria-label', 'Main');
  side.innerHTML = `<a class="brand" href="/">${I.logo}<span>Ops Agent</span></a>
    ${links.map(([k, href, label, ic]) => `<a class="nav ${k === page ? 'on' : ''}" href="${href}" ${k === page ? 'aria-current="page"' : ''}>${ic}<span>${label}</span>${k === 'inbox' ? '<span class="cnt" id="navHeld" hidden></span>' : ''}</a>`).join('')}
    <div class="foot">
      <div class="ws"><span class="mark">NF</span><span><b>Northwind Facilities</b><small>ops@northwind-facilities.example</small></span></div>
      <div class="badges" id="navBadges"></div>
      <a class="nav" href="/inside" ${page === 'inside' ? 'aria-current="page"' : ''}>${I.help}<span>Under the hood</span></a>
    </div>`;
  const content = document.createElement('div'); content.className = 'content';
  const top = document.createElement('header'); top.className = 'topbar';
  top.innerHTML = `<span>Northwind Facilities</span><span class="sep">/</span><span class="crumb">${links.find(l => l[0] === page)?.[3] || ''}${title}</span>
    <div class="right"><span class="mono muted" id="navCountdown"></span>
      <button type="button" class="iconbtn" aria-label="Notifications" id="navBell">${I.bell}</button>
      <button type="button" class="iconbtn" aria-label="Account" style="width:auto;padding:0 6px 0 3px;gap:6px"><span class="avatar">IA</span>${I.chev}</button></div>`;
  content.appendChild(top);
  existing.forEach(n => content.appendChild(n));
  shell.appendChild(side); shell.appendChild(content);
  document.body.appendChild(shell);

  window.renderNavStatus = function (s) {
    if (!s) return;
    const fake = s.llm === 'fake';
    const b = document.getElementById('navBadges');
    if (b) b.innerHTML = `<span class="badge ${s.app_mode === 'live' ? 'live' : 'demo'}" title="${s.app_mode === 'live' ? 'processing your real inbox' : 'seeded inbox, resets when idle'}">${s.app_mode || 'demo'}</span>${s.shadow_mode ? '<span class="badge" style="background:#5B21B6;color:#fff">shadow</span>' : ''}<span class="badge ${fake ? 'fake' : 'model'}" title="${fake ? 'no Anthropic key — keyword stand-in' : 'Claude is live'}">${fake ? 'fake model' : s.llm}</span>`;
    const held = document.getElementById('navHeld');
    if (held) { const n = (s.counts && (s.counts.held || 0) + (s.counts.failed || 0)) || 0; held.hidden = !n; held.textContent = n; }
    const el = document.getElementById('navCountdown');
    if (el) {
      clearInterval(window.__navTimer);
      if (s.app_mode !== 'live' && s.seconds_to_reset != null) {
        let secs = s.seconds_to_reset;
        const tick = () => { el.textContent = `next reset in ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`; if (secs > 0) secs--; };
        tick(); window.__navTimer = setInterval(tick, 1000);
      } else el.textContent = s.app_mode === 'live' ? 'live inbox' : '';
    }
  };
  window.toast = function (msg, ms) { let t = document.getElementById('toast'); if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); } t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), ms || 2200); };
  window.api = async function (path, opts) {
    const r = await fetch(path, Object.assign({ headers: { 'content-type': 'application/json' } }, opts || {}));
    if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  };
  window.esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  window.money = v => (v == null || v === '') ? '—' : '£' + Number(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  window.ICONS = I;
})();
