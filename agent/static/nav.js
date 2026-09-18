// Shared top navigation + status badges. Include on every page: <script src="/static/nav.js" data-page="dashboard"></script>
(function () {
  const page = document.currentScript.dataset.page || '';
  const links = [['dashboard', '/', 'Dashboard'], ['inbox', '/inbox', 'Inbox & review'], ['directory', '/directory', 'Suppliers & customers'], ['settings', '/settings', 'Settings'], ['inside', '/inside', 'Under the hood']];
  const nav = document.createElement('nav');
  nav.className = 'top';
  nav.innerHTML = `<a class="brand" href="/">Ops Agent <span>· invoices &amp; disputes</span></a>
    <div class="links">${links.map(([k, href, label]) => `<a href="${href}" class="${k === page ? 'on' : ''}">${label}</a>`).join('')}</div>
    <div class="right" id="navRight"></div>`;
  document.body.prepend(nav);
  window.renderNavStatus = function (s) {
    const r = document.getElementById('navRight');
    if (!r || !s) return;
    const fake = s.llm === 'fake';
    r.innerHTML = `<span class="badge ${s.app_mode === 'live' ? 'live' : 'demo'}" title="${s.app_mode === 'live' ? 'processing your real inbox' : 'seeded inbox, auto reset when idle'}">${s.app_mode || 'demo'}</span>
      ${s.shadow_mode ? '<span class="badge" style="background:#5b21b6;color:#fff">shadow</span>' : ''}
      <span class="badge ${fake ? 'fake' : 'model'}" title="${fake ? 'no Anthropic key — keyword stand-in is running' : 'Claude is live'}">${fake ? 'fake model' : s.llm}</span>
      ${s.app_mode !== 'live' && s.seconds_to_reset != null ? `<span class="mono" id="navCountdown"></span>` : ''}`;
    if (s.app_mode !== 'live' && s.seconds_to_reset != null) {
      let secs = s.seconds_to_reset;
      const el = document.getElementById('navCountdown');
      const tick = () => { if (!el) return; el.textContent = `reset in ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`; if (secs > 0) secs--; };
      tick(); clearInterval(window.__navTimer); window.__navTimer = setInterval(tick, 1000);
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
})();
