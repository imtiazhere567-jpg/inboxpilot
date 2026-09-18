// Shared review slide-over. Include after nav.js. Usage:
//   Review.open(documentId)   Review.close()   Review.onChange = () => reloadPage()
// Handles approve / reject / retry / add-party-from-document and shows extracted data, verifier, source, actions, timeline.
(function () {
  const css = `
  .rv .title{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:15px;font-weight:800}
  .rv .from{color:var(--muted);font-size:12px;margin-top:3px}
  .rv .kv{display:grid;grid-template-columns:130px 1fr;gap:4px 12px;font-size:12.5px;margin:10px 0 0}
  .rv .kv dt{color:var(--muted)}.rv .kv dd{margin:0;font-family:var(--mono);word-break:break-word}
  .rv .reason{background:var(--holdbg);color:var(--hold);border-radius:10px;padding:10px 14px;font-weight:700;margin:12px 0;font-size:13px}
  .rv .reason.ok{background:var(--okbg);color:var(--ok)}.rv .reason.bad{background:var(--badbg);color:var(--bad)}.rv .reason.grey{background:var(--greybg);color:var(--grey)}.rv .reason.shadow{background:var(--shadowbg);color:var(--shadow)}
  .rv .reason .rules{font-weight:500;font-size:11px;margin-top:3px;opacity:.8}
  .rv .box{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;font-size:12.5px}
  .rv .box h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
  .rv pre{margin:0;white-space:pre-wrap;font-family:var(--mono);font-size:11.5px;line-height:1.45;max-height:220px;overflow:auto}
  .rv mark{background:#FFF3BF;padding:0 .1em}
  .rv .verify.ok{color:var(--ok);font-weight:700}.rv .verify.bad{color:var(--bad);font-weight:700}
  .rv .actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .rv textarea{width:100%;font:inherit;border:1px solid var(--line-strong);border-radius:8px;padding:9px 11px;min-height:60px;margin-top:10px;font-size:13px}
  .rv textarea:focus{outline:2px solid var(--blue-soft);border-color:var(--blue)}
  .rv .timeline{list-style:none;margin:4px 0 0;padding:0;font-family:var(--mono);font-size:11.5px}
  .rv .timeline li{display:flex;gap:10px;padding:3px 0;border-left:2px solid var(--line);padding-left:10px;align-items:center}
  .rv .timeline li b{min-width:9.5em;font-weight:500;display:flex;gap:6px;align-items:center}
  .rv .timeline li span{color:var(--muted)}
  .rv .actrow{display:flex;gap:8px;align-items:center;font-family:var(--mono);font-size:12px;padding:3px 0;flex-wrap:wrap}
  .rv .addparty input{width:100%;font:inherit;border:1px solid var(--line-strong);border-radius:8px;padding:8px 10px;margin-top:3px}
  .rv .addparty label{display:block;font-size:12px;font-weight:700;margin-top:8px}`;
  const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  const scrim = document.createElement('div'); scrim.className = 'scrim'; scrim.id = 'rvScrim';
  const drawer = document.createElement('aside'); drawer.className = 'drawer'; drawer.id = 'rvDrawer'; drawer.setAttribute('aria-label', 'Review');
  drawer.innerHTML = `<div class="head"><b id="rvTitle">Review</b><span class="muted mono" id="rvN" style="font-size:11px"></span><button type="button" class="btn small close" id="rvClose">Close</button></div><div class="body rv" id="rvBody"></div>`;
  document.body.appendChild(scrim); document.body.appendChild(drawer);

  const $ = s => document.querySelector(s);
  const typeLabel = t => ({supplier_invoice:'invoice', customer_dispute:'complaint', remittance:'payment advice', credit_note:'credit note', ignore:'ignored', unknown:'unknown'})[t] || (t || '—');
  const statusLabel = s => ({auto_approved:'Auto', executed:'Executed', held:'Held', approved:'Approved', rejected:'Rejected', failed:'Failed', ignore:'Ignored', shadow:'Shadow', received:'Received'})[s] || s;
  const chip = (s, extra='') => `<span class="chip c-${s} ${extra}">${statusLabel(s)}</span>`;
  let current = null;

  const R = window.Review = {
    current: () => current,
    onChange: null,
    open(id) { current = id; drawer.classList.add('open'); scrim.classList.add('open'); history.replaceState(null, '', '#doc-' + id); R.load(id); },
    close() { current = null; drawer.classList.remove('open'); scrim.classList.remove('open'); if (/#doc-/.test(location.hash)) history.replaceState(null, '', location.pathname); if (R.onSelect) R.onSelect(null); },
    refresh() { if (current) R.load(current, true); },
    async load(id, quiet) {
      let d; try { d = await api('/review/' + id); } catch (e) { if (!quiet) toast(e.message); return; }
      if (R.onSelect) R.onSelect(id);
      const it = d.item, ex = it.extracted || {}, vr = it.verify_result;
      $('#rvTitle').textContent = typeLabel(it.doc_type).replace(/^./, c => c.toUpperCase()); $('#rvN').textContent = '#' + (it.seed_no ?? '') + ' · doc ' + it.document_id;
      const reasonClass = {executed:'ok', auto_approved:'ok', approved:'ok', failed:'bad', rejected:'bad', ignore:'grey', shadow:'shadow'}[it.status] || '';
      const snippet = highlight(esc(it.source_snippet || d.email_body || ''), ex);
      const extractedRows = Object.entries(ex).filter(([k,v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length)).map(([k,v]) =>
        `<dt>${esc(k.replace(/_/g,' '))}</dt><dd>${Array.isArray(v) ? esc(v.map(x => typeof x === 'object' ? (x.description + ' ' + (x.amount ?? '')) : x).join('; ')) : esc(v)}</dd>`).join('');
      const rulesDec = (d.decisions || []).filter(x => x.step === 'rules').pop();
      const triggered = rulesDec && rulesDec.output && rulesDec.output.triggered_rules ? rulesDec.output.triggered_rules.join(', ') : '';
      const canDecide = ['held','shadow'].includes(it.status), canRetry = it.status === 'failed', canReject = ['held','shadow','failed'].includes(it.status);
      const noMatch = /^no (supplier|customer) match/.test(it.reason || '');
      $('#rvBody').innerHTML = `
        <div class="title">${esc(it.subject)} ${chip(it.status)}</div>
        <div class="from">from <span class="mono">${esc(it.from_addr)}</span> · <span class="mono">${esc(it.filename === 'email-body.txt' ? 'email body' : it.filename)}</span></div>
        <div class="reason ${reasonClass}">${esc(it.reason || '—')}${triggered ? `<div class="rules">rules: ${esc(triggered)}</div>` : ''}</div>
        <dl class="kv">
          <dt>party</dt><dd>${esc(it.party_name || '—')}${it.party_kind ? ' <span class="muted">(' + it.party_kind + ')</span>' : ''}</dd>
          <dt>confidence</dt><dd>${it.confidence != null ? it.confidence.toFixed(2) : '—'}</dd>
          ${it.duplicate_of ? `<dt>duplicate of</dt><dd><a href="#" data-open="${it.duplicate_of}">doc #${it.duplicate_of}</a></dd>` : ''}
          <dt>AI cost</dt><dd>$${(it.cost_usd||0).toFixed(4)}</dd>
        </dl>
        ${noMatch ? `<div class="box addparty" id="rvAddParty"><h4>Not on file</h4><div class="muted" style="margin-bottom:8px">This sender is not in the directory. Add them once and this item is processed again automatically.</div><button class="btn small primary" type="button" data-suggest="${it.document_id}">Add as ${/customer/.test(it.reason) ? 'customer' : 'supplier'} →</button></div>` : ''}
        ${canDecide ? nextStep(it) : ''}
        ${(canDecide || canRetry) ? `<textarea id="rvNote" placeholder="${canRetry ? 'Optional note' : 'Required: one line on why (recorded in the ledger)'}"></textarea>` : ''}
        <div class="actions">
          ${canDecide ? `<button class="btn primary" type="button" data-decide="approve" data-id="${it.document_id}">Approve → execute</button>` : ''}
          ${canReject ? `<button class="btn" type="button" data-decide="reject" data-id="${it.document_id}">Reject</button>` : ''}
          ${canRetry ? `<button class="btn cta" type="button" data-retry="${it.document_id}">Retry failed action</button>` : ''}
        </div>
        ${extractedRows ? `<div class="box"><h4>Extracted</h4><dl class="kv" style="margin:0">${extractedRows}</dl></div>` : ''}
        ${vr ? `<div class="box"><h4>Verifier — second model</h4><div class="verify ${vr.all_fields_present?'ok':'bad'}">${vr.all_fields_present ? '✓ every extracted value is literally present in the source' : '✗ unsupported: ' + esc((vr.missing_or_unsupported||[]).join(', '))}</div>${vr.notes ? `<div class="muted" style="margin-top:3px">${esc(vr.notes)}</div>` : ''}</div>` : ''}
        <div class="box"><h4>Source — what the agent read</h4><pre>${snippet}</pre></div>
        ${it.draft_reply ? `<div class="box"><h4>Draft reply — held with the ticket, never auto-sent</h4><pre>${esc(it.draft_reply)}</pre></div>` : ''}
        ${it.actions.length ? `<div class="box"><h4>Where it went</h4>${it.actions.map(a => `<div class="actrow">${chip(a.status==='ok'?'executed':'failed')} <b>${a.status === 'ok' ? esc(actionLabel(a)) : a.system}</b> ${a.result && a.result.url ? `<a href="${esc(a.result.url)}" target="_blank" rel="noopener">open ↗</a>` : ''} ${a.result && a.result.simulated && !window.PRESENT ? '<span class="chip sim">simulated</span>' : ''} ${a.error ? `<span style="color:var(--bad)">${esc(a.error)}</span>` : ''}</div>`).join('')}</div>` : ''}
        ${d.notes && d.notes.length ? `<div class="box"><h4>Human notes</h4>${d.notes.map(n => `<div class="actrow"><b>${n.action}</b> ${esc(n.note)} <span class="muted">· ${n.by} · ${new Date(n.at).toLocaleTimeString('en-GB')}</span></div>`).join('')}</div>` : ''}
        <div class="box"><h4>Timeline</h4><ul class="timeline">${it.timeline.map(t => `<li><b>${esc(t.step)}${t.model ? (t.model === 'fake' ? (window.PRESENT ? '<span class="badge model">ai</span>' : '<span class="chip sim">fake ai</span>') : '<span class="badge model">claude</span>') : ''}</b><span>${new Date(t.at).toLocaleTimeString('en-GB')}${t.duration_ms != null ? ' · ' + t.duration_ms + ' ms' : ''}${t.model && t.model !== 'fake' ? ' · ' + t.model : ''}</span></li>`).join('')}</ul></div>`;
    },
  };

  function nextStep(it) {
    const ex = it.extracted || {}, party = it.party_name || 'the sender';
    let approve;
    if (it.doc_type === 'supplier_invoice') approve = `a bill for <b>${money(ex.total)}</b> against <b>${esc(party)}</b> is created in QuickBooks (ref ${esc(ex.invoice_number || '—')}) and a line is posted to Slack. No payment is made — the bill sits in QuickBooks for the normal payment run.`;
    else if (it.doc_type === 'customer_dispute') approve = `a ticket is opened in HubSpot on <b>${esc(party)}</b> with the complaint summary${ex.requested_refund_amount ? ` and the <b>${money(ex.requested_refund_amount)}</b> request` : ''}, the draft reply is saved on the ticket (not sent), and Slack is notified. No refund is paid — a person decides that from the ticket.`;
    else if (it.doc_type === 'remittance') approve = `the payment notice is recorded and Slack is told "payment received". Nothing else happens.`;
    else approve = `the item is recorded as approved and Slack is notified.`;
    return `<div class="box"><h4>What happens next</h4><div><b>Approve</b> → ${approve}</div><div style="margin-top:6px"><b>Reject</b> → nothing is posted anywhere; your note and the decision are kept in the ledger.</div></div>`;
  }
  function highlight(text, ex) {
    const vals = Object.values(ex || {}).filter(v => typeof v === 'string' || typeof v === 'number').map(String).filter(v => v.length > 2).sort((a,b)=>b.length-a.length);
    for (const v of vals) {
      const alt = [v, Number(v) ? Number(v).toLocaleString('en-GB',{minimumFractionDigits:2}) : null].filter(Boolean);
      for (const a of alt) { const re = new RegExp(a.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'), 'g'); text = text.replace(re, m => `<mark>${m}</mark>`); }
    }
    return text;
  }
  const changed = async () => { if (R.onChange) await R.onChange(); R.refresh(); };
  async function decide(id, action) {
    const note = ($('#rvNote') ? $('#rvNote').value.trim() : '');
    if (note.length < 3) { toast('Please write a short note first'); $('#rvNote').focus(); return; }
    try {
      const r = await api(`/${action}/${id}`, {method:'POST', body: JSON.stringify({note, by:'demo'})});
      if (action === 'approve') {
        const done = (r.actions || []).filter(a => a.status === 'ok').map(a => actionLabel(a));
        const failed = (r.actions || []).filter(a => a.status !== 'ok').map(a => a.system);
        toast(r.status === 'executed' ? `Approved — ${done.join(', ')}` : `Approved but ${failed.join(', ')} failed — retry from the panel`, 4500);
      } else toast('Rejected — nothing posted, decision recorded', 3500);
      await changed();
    } catch (e) { toast(e.message); }
  }
  async function retry(id) { try { const r = await api(`/retry/${id}`, {method:'POST'}); const done = (r.actions || []).filter(a => a.status === 'ok').map(a => actionLabel(a)); toast(r.status === 'executed' ? `Done — ${done.join(', ')}` : 'Still failing — the system is not reachable', 4500); await changed(); } catch (e) { toast(e.message); } }
  async function suggestParty(id) {
    const box = document.getElementById('rvAddParty');
    let sg; try { sg = await api('/api/parties/suggest/' + id); } catch (e) { toast(e.message); return; }
    box.innerHTML = `<h4>Add ${sg.kind}</h4>
      <div class="muted">${esc(sg.why)} — check the email the agent should recognise next time.</div>
      <label>Name<input id="p-name" value="${esc(sg.name)}"></label>
      <label>Email address(es)<input id="p-pat" class="mono" value="${esc(sg.emails.join(', '))}"></label>
      <label>Reference prefix <span class="muted" style="font-weight:500">(optional)</span><input id="p-prefix" class="mono" value="${esc((sg.reference_prefix || '').replace(/-$/, ''))}"></label>
      ${sg.kind === 'supplier' ? `<label>Expected invoice amount / PO <span class="muted" style="font-weight:500">(optional, ±5% check)</span><input id="p-po" class="mono" value="${sg.po_amount ?? ''}"></label>` : ''}
      <div class="actions"><button class="btn small primary" type="button" data-create="${id}" data-kind="${sg.kind}">Save &amp; process again</button><button class="btn small" type="button" data-cancel="${id}">Cancel</button></div>`;
  }
  async function createParty(id, kind) {
    const body = { name: $('#p-name').value, identifier_patterns: $('#p-pat').value, reference_prefix: $('#p-prefix').value };
    if (kind === 'supplier') body.po_amount = $('#p-po').value || null;
    try { const r = await api(`/api/parties/from_document/${id}?kind=${kind}`, { method: 'POST', body: JSON.stringify(body) }); toast(`added · now ${r.status}`); await changed(); } catch (e) { toast(e.message); }
  }
  drawer.addEventListener('click', e => {
    const t = e.target.closest('[data-decide],[data-retry],[data-suggest],[data-create],[data-cancel],[data-open]');
    if (!t) return;
    if (t.dataset.decide) decide(+t.dataset.id, t.dataset.decide);
    else if (t.dataset.retry) retry(+t.dataset.retry);
    else if (t.dataset.suggest) suggestParty(+t.dataset.suggest);
    else if (t.dataset.create) createParty(+t.dataset.create, t.dataset.kind);
    else if (t.dataset.cancel) R.load(+t.dataset.cancel);
    else if (t.dataset.open) { e.preventDefault(); R.open(+t.dataset.open); }
  });
  $('#rvClose').addEventListener('click', R.close); scrim.addEventListener('click', R.close);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawer.classList.contains('open')) R.close(); });
  window.addEventListener('hashchange', () => { const m = location.hash.match(/doc-(\d+)/); if (m) R.open(+m[1]); });
  const m = location.hash.match(/doc-(\d+)/); if (m) setTimeout(() => R.open(+m[1]), 50);
})();
