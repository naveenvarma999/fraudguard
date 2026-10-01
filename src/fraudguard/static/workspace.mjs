import {parseCSV, normalizeInput} from './data.mjs';
const $ = id => document.getElementById(id);
const state = {token: '', user: null, rows: [], audit: [], selected: null, busy: false, enrollment: '', events: [], nextEvent: null, flow: 0, overview: null, route: ''};
const pct = n => n == null ? 'Not measured' : `${(n * 100).toFixed(2)}%`;
const date = n => new Date(n * 1000).toLocaleString();
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function notice(text, error = false) { $('ops-message').textContent = text; $('ops-message').hidden = !text; $('ops-message').classList.toggle('ops-error', error); }
function clear() {
  state.token = ''; state.enrollment = ''; state.user = null; state.rows = []; state.audit = []; state.selected = null;
  $('workspace').hidden = true; $('signin').hidden = false; $('logout').hidden = true; $('identity').textContent = '';
  document.querySelectorAll('form').forEach(f => f.reset());
  ['queue-list','audit-list','monitor-content','release-list','user-list'].forEach(id => $(id).replaceChildren());
  $('upload-feedback').hidden = true; $('upload-result').replaceChildren(); state.events = []; state.overview = null; ['overview-metrics','overview-recent','detail-timeline','detail-metrics'].forEach(id => $(id).replaceChildren()); $('mfa-enrollment').hidden = true; $('mfa-secret').value = ''; $('recovery-codes').textContent = ''; $('mfa-recovery').hidden = true; $('mfa-confirm').hidden = false;
}
async function api(path, method = 'GET', body) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {method, signal: controller.signal, cache: 'no-store', headers: {'Content-Type': 'application/json', ...(state.token ? {Authorization: `Bearer ${state.token}`} : {})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) { if (response.status === 401 && state.token) clear(); const wait = response.headers.get('Retry-After'); throw Error((typeof data.detail === 'string' ? data.detail : (Array.isArray(data.detail) ? data.detail.map(e => `${(e.loc || []).filter(v => v !== 'body').join(' → ') || 'Input'}: ${e.type === 'missing' ? 'required value is missing' : 'check the format or allowed range'}`).join('; ') : `Request rejected (${response.status}). Check the input.`)) + (wait ? ` Try again in ${wait} seconds.` : ''));  }
    return data;
  } finally { clearTimeout(timer); }
}
function action(fn) { return async e => { e?.preventDefault(); if (state.busy) return; state.busy = true; const button = e?.submitter || (e?.currentTarget?.tagName === 'BUTTON' ? e.currentTarget : null); if (button) button.disabled = true; notice(''); try { await fn(e); } catch (err) { notice(err.name === 'AbortError' ? 'Request timed out. Refresh the record before retrying an action.' : err.message, true); if (!$('section-detail').hidden) $('review-message').textContent = err.message; } finally { state.busy = false; if (button) button.disabled = false; } }; }
function table(root, headers, records, cells) {
  root.replaceChildren(); if (!records.length) { root.append(node('p', 'No records yet.', 'ops-empty')); return; }
  const wrap = node('div', undefined, 'table-wrap'), table = node('table'), head = node('thead'), tr = node('tr');
  headers.forEach(h => tr.append(node('th', h))); head.append(tr); table.append(head); const body = node('tbody');
  records.forEach(row => { const tr = node('tr'); cells(row).forEach(value => { const td = node('td'); if (value instanceof Node) td.append(value); else td.textContent = value; tr.append(td); }); body.append(tr); });
  table.append(body); wrap.append(table); root.append(wrap);
}
function button(label, fn) { const b = node('button', label, 'text-button'); b.onclick = action(fn); return b; }
async function queue(older = false) {
  const last = state.rows.at(-1); const rows = await api(`/ops/predictions?decision=${$('decision-filter').value}${older && last ? `&before=${last.id}` : ''}`);
  state.rows = older ? [...state.rows, ...rows] : rows; $('more-history').hidden = rows.length < 50;
  table($('queue-list'), ['Transaction / time', 'Model', 'Amount', 'Probability', 'Recommendation', 'Review / outcome', 'Action'], state.rows, r => [r.transaction_id + '\n' + date(r.at), r.model, r.amount.toLocaleString(), pct(r.probability), r.decision, `${r.review} / ${r.fraud == null ? 'unlabeled' : r.fraud ? 'fraud' : 'not fraud'}`, button('Review ↗', () => openReview(r))]);
}
async function openReview(row) {
  $('review-message').textContent = ''; state.route = `transaction/${encodeURIComponent(row.id)}`;
  location.hash = `transaction/${encodeURIComponent(row.id)}`;
  await detail(row.id);
}
async function detail(identifier, older = false) {
  state.route = `transaction/${encodeURIComponent(identifier)}`;
  const result = await api(`/ops/predictions/${encodeURIComponent(identifier)}${older && state.nextEvent ? `?before=${state.nextEvent}` : ''}`);
  const row = result.prediction; state.selected = row;
  state.events = older ? [...state.events, ...result.events] : result.events; state.nextEvent = result.next_before;
  if (!older) {
    document.querySelectorAll('.ops-section').forEach(e => e.hidden = e.id !== 'section-detail');
    document.querySelectorAll('[data-section]').forEach(e => { e.classList.toggle('selected', e.dataset.section === 'queue'); e.setAttribute('aria-current', e.dataset.section === 'queue' ? 'page' : 'false'); });
    $('review-title').textContent = row.transaction_id; $('review-title').focus();
    $('review-summary').textContent = `Scored ${date(row.at)} · ${row.owner} · Model ${row.model}. Prediction ID: ${row.id}`;
    $('review-disposition').value = row.review; $('review-note').value = row.note;
    $('label-source').value = result.label?.source || ''; $('label-fraud').value = String(result.label?.fraud ?? 1);
    cards($('detail-metrics'), [['Fraud probability', pct(row.probability), `Review threshold ${pct(row.threshold)}`], ['Recommendation', row.decision === 'review' ? 'Review' : 'Pass', 'A recommendation, not a verified outcome'], ['Amount', row.amount.toLocaleString(), 'Currency is not provided by this dataset'], ['Verified outcome', result.label ? result.label.fraud ? 'Fraud' : 'Not fraud' : 'Not recorded', result.label ? `Recorded by ${result.label.actor}` : 'Requires evidence, such as a confirmed chargeback']]);
    $('detail-next').textContent = row.review === 'open' ? (row.decision === 'review' ? 'Investigate this flagged transaction. Record your review and reasoning below.' : 'No review was recommended. You can still investigate and record evidence if needed.') : 'A review has been recorded. Add a verified outcome when evidence arrives, or document a correction.';
  }
  $('detail-timeline').replaceChildren();
  for (const event of state.events) {
    const data = JSON.parse(event.detail), li = node('li');
    const title = event.action === 'prediction.reviewed' ? 'Review recorded' : event.action === 'label.corrected' ? 'Verified outcome corrected' : 'Verified outcome recorded';
    li.append(node('strong', title), node('small', `${date(event.at)} · ${event.actor}`), node('p', event.action === 'prediction.reviewed' ? `${data.disposition}: ${data.note}` : `${data.fraud ? 'Fraud' : 'Not fraud'} — ${data.source}`));
    if (data.previous) li.append(node('p', `Previous outcome: ${data.previous.fraud ? 'Fraud' : 'Not fraud'} — ${data.previous.source}`, 'fineprint'));
    $('detail-timeline').append(li);
  }
  if (!state.nextEvent) { const li = node('li'); li.append(node('strong','Transaction scored'),node('small',`${date(row.at)} · ${row.owner}`),node('p',`Model ${row.model} recommended ${row.decision}.`)); $('detail-timeline').append(li); }
  $('detail-older').hidden = !state.nextEvent;
}

async function audit(older = false) {
  const last = state.audit.at(-1); const rows = await api(`/ops/audit${older && last ? `?before=${last.id}` : ''}`);
  state.audit = older ? [...state.audit, ...rows] : rows; $('more-audit').hidden = rows.length < 50;
  table($('audit-list'), ['Time', 'Actor', 'Action', 'Resource', 'Details'], state.audit, r => { const d = node('details'); d.append(node('summary', 'Inspect'), node('pre', JSON.stringify(JSON.parse(r.detail), null, 2))); return [date(r.at), r.actor, r.action, r.resource, d]; });
}
async function monitoring() {
  const data = await api('/ops/monitoring'); const root = $('monitor-content'); root.replaceChildren();
  root.append(node('p', `Active model: ${data.model_version}`)); const t = data.telemetry, d = data.drift, q = data.quality;
  const grid = node('div', undefined, 'ops-metrics');
  for (const [label, value, note] of [['Prediction p95', t.p95_seconds == null ? 'No traffic' : `${(t.p95_seconds * 1000).toFixed(1)} ms`, `${t.requests} requests / 5 minutes`], ['5xx error rate', pct(t.error_rate), 'Prediction HTTP responses'], ['Memory use', pct(t.memory_ratio), t.rss_bytes == null ? 'Container counters available on Linux' : `${(t.rss_bytes / 1048576).toFixed(1)} MiB API resident memory`], ['Largest feature PSI', d.max_psi == null ? 'Collecting data' : d.max_psi.toFixed(3), `${d.rows} rows; minimum 1,000`], ['Observed fraud recall', pct(q.recall), 'Needs 50 labels, 5 fraud and 5 non-fraud'], ['Label coverage', pct(q.label_coverage), `${q.labeled ?? 0} labels / ${q.predictions ?? 0} predictions`]]) {
    const card = node('article', undefined, 'panel'); card.append(node('span', label), node('strong', value), node('small', note)); grid.append(card);
  } root.append(grid);
  const alerts = node('article', undefined, 'panel ops-card'); alerts.append(node('h2', 'Operational alerts'));
  const stale = !data.alerts.length || Math.max(...data.alerts.map(a => a.checked)) < Date.now()/1000 - 180;
  if (stale) alerts.append(node('p', 'Watchdog has no recent heartbeat. Start or inspect the monitor container.', 'ops-alert'));
  const active = data.alerts.filter(a => a.active);
  if (!active.length) alerts.append(node('p', 'No firing alerts in the latest stored checks.', 'ops-good'));
  active.forEach(a => alerts.append(node('p', `${a.name}: ${a.detail}. Last checked ${date(a.checked)}`, 'ops-alert')));
  root.append(alerts);
  const workers = node('article', undefined, 'panel ops-card'); workers.append(node('h2', 'Request protection & scoring workers'), node('p', 'Each account: burst of 60 prediction requests, refilling at one per second; transaction budget of 3,000, refilling at 50 per second.'));
  if (!data.workers?.length) workers.append(node('p', 'Scoring runs inside the API. Load balancing is not enabled for this deployment.'));
  for (const w of data.workers || []) workers.append(node('p', `Worker ${w.worker}: ${w.completed} completed batches · ${w.in_flight} in progress · ${w.circuit_open ? 'temporarily unavailable' : 'eligible for requests'}`));
  root.append(workers);
  const recovery = Object.fromEntries((data.recovery || []).map(r => [r.key, JSON.parse(r.value)]));
  const backup = recovery.backup_latest;
  const backupCard = node('article', undefined, 'panel ops-card');
  backupCard.append(node('h2', 'Backups & alert delivery'), node('p', backup ? `Last verified backup: ${date(backup.created)} · ${(backup.bytes / 1048576).toFixed(2)} MiB · ${backup.file}` : 'No verified backup has been recorded yet.'), node('p', recovery.delivery_enabled ? 'External HTTPS alert delivery is configured.' : 'External alert delivery is disabled until a receiver is configured.'));
  for (const item of data.delivery || []) backupCard.append(node('p', `${item.status}: ${item.count} alert deliveries`));
  backupCard.append(node('p', 'Backups reside in a separate local Docker volume. Copy them off-host to protect against instance loss.'));
  root.append(backupCard);

  const quality = node('article', undefined, 'panel ops-card'); quality.append(node('h2', 'Delayed-label performance'), node('p', q.cohort || q.detail), node('p', q.status === 'measured' ? `Precision ${pct(q.precision)} · AP ${q.average_precision.toFixed(3)} · ROC AUC ${q.roc_auc.toFixed(3)} · Median label delay ${q.median_label_delay_hours.toFixed(1)} hours` : 'Quality is not reported until there is enough labeled evidence.'), node('p', 'A review disposition does not automatically become a fraud label. Corrections remain in the audit trail.')); root.append(quality);
  const drift = node('article', undefined, 'panel ops-card'); drift.append(node('h2', 'Feature distribution drift'), node('p', d.interpretation)); const list = node('div');
  table(list, ['Feature', 'PSI', 'Status'], Object.entries(d.features).sort((a,b)=>b[1].psi-a[1].psi), ([name, f]) => [name, f.psi.toFixed(4), f.investigate ? 'Investigate' : 'Below threshold']); drift.append(list); root.append(drift);
}
async function releases() {
  const data = await api('/ops/releases'), root = $('release-list'); root.replaceChildren(); root.append(node('p', `Active: ${data.active}`));
  for (const r of data.releases) { const card = node('article', undefined, 'ops-card'); card.append(node('h3', r.id), node('p', `${r.status} · Submitted by ${r.submitted_by} · Approved by ${r.approved_by || '—'}`));
    const details = node('details'); details.append(node('summary', 'Quality gates'), node('pre', JSON.stringify(r.gates, null, 2))); card.append(details);
    if (r.status === 'pending') { const b = button('Approve release', async () => { await api(`/ops/releases/${r.id}/approve`, 'POST'); await releases(); notice('Release approved. It can now be activated.'); }); b.disabled = r.submitted_by === state.user.name; card.append(b); if (b.disabled) card.append(node('p', 'Another administrator must approve your submission.')); }
    if (r.status === 'approved' && r.id !== data.active) card.append(button('Activate release', async () => { if (!confirm(`Activate ${r.id}? New predictions will use this model.`)) return; await api(`/ops/releases/${r.id}/activate`, 'POST'); await releases(); notice('Model activated.'); })); root.append(card);
  }
}
async function users() {
  const rows = await api('/ops/users'); $('user-list').replaceChildren(); rows.forEach(u => { const row = node('div', undefined, 'ops-user'); row.append(node('span', `${u.name} · ${u.role} · ${u.active ? 'active' : 'disabled'}`)); if (u.name !== state.user.name) row.append(button(u.active ? 'Disable' : 'Enable', async () => { await api(`/ops/users/${u.name}`, 'PATCH', {active: !u.active}); await users(); })); $('user-list').append(row); });
}
async function section(name) {
  state.route = name;
  document.querySelectorAll('.ops-section').forEach(e => e.hidden = e.id !== `section-${name}`);
  document.querySelectorAll('[data-section]').forEach(e => (e.classList.toggle('selected', e.dataset.section === name), e.setAttribute('aria-current', e.dataset.section === name ? 'page' : 'false')));
  location.hash = name;
  await ({overview, flow, queue, monitor: monitoring, releases, audit, users}[name]?.());
}
$('login-form').onsubmit = action(async () => {
  try { const result = await api('/auth/login', 'POST', {username: $('username').value, password: $('password').value, otp: $('otp').value.trim()}); if (result.enrollment_required) { state.enrollment = result.enrollment_token; const setup = await api('/auth/mfa/setup', 'POST', {token: state.enrollment}); $('mfa-secret').value = setup.secret; $('mfa-account').textContent = setup.account; $('signin').hidden = true; $('mfa-enrollment').hidden = false; return; } state.token = result.access_token; state.user = result.user; $('signin').hidden = true; $('workspace').hidden = false; $('logout').hidden = false; $('identity').textContent = `${state.user.name} · ${state.user.role}`; document.querySelectorAll('[data-admin]').forEach(e => e.hidden = state.user.role !== 'admin'); const route = location.hash.slice(1); if (route.startsWith('transaction/')) await detail(decodeURIComponent(route.slice(12))); else await section('overview'); }
  finally { $('password').value = ''; $('otp').value = ''; }
});
$('logout').onclick = action(async () => { try { await api('/auth/logout', 'POST'); } finally { clear(); } });
document.querySelectorAll('[data-section]').forEach(b => b.onclick = action(() => section(b.dataset.section)));
$('score-form').onsubmit = action(async () => {
  $('upload-result').replaceChildren();
  let submitted = false, saved = false;
  try {
    uploadState('1 / 3 · Checking your file', 'Validating the format, fields and transaction count.', 0);
    const f = $('transaction-file').files[0];
    if (!f || f.size > 262144 || !/\.(csv|json)$/i.test(f.name)) throw Error('Choose a CSV or JSON file up to 256 KiB.');
    const csv = /\.csv$/i.test(f.name), text = await f.text();
    let parsed; try { parsed = csv ? parseCSV(text) : JSON.parse(text); } catch (e) { throw Error(csv ? e.message : 'This file is not valid JSON. Download the sample format and check brackets and commas.'); }
    const rows = normalizeInput(parsed, csv);
    uploadState('2 / 3 · Scoring transactions', `${rows.length} validated rows. Waiting for the server to score and save them…`, null);
    submitted = true;
    const result = await api('/v1/predict', 'POST', {transactions: rows}); saved = true;
    const flagged = result.predictions.filter(p => p.decision === 'review').length;
    uploadState('3 / 3 · Results saved', `${result.predictions.length} transactions · ${flagged} flagged for review · Model ${result.model_version}`, 100);
    const first = result.predictions.find(p => p.decision === 'review') || result.predictions[0];
    if (first?.prediction_id) $('upload-result').append(button('Open transaction details →', () => openReview({id: first.prediction_id})));
    $('score-form').reset();
    await queue(); notice(`Saved ${result.predictions.length} predictions from model ${result.model_version}.`);
  } catch (err) {
    const uncertain = submitted && !saved && (err.name === 'AbortError' || err instanceof TypeError);
    uploadState(saved ? 'Results saved; history refresh failed' : uncertain ? 'Response interrupted — check history' : 'Upload needs attention', saved ? 'Your results were saved. Refresh prediction history; do not resubmit the batch.' : uncertain ? 'The server may have saved this batch. Refresh history before trying again to avoid duplicates.' : err.message, 0);
    throw err;
  }
});
$('detail-back').onclick = action(() => section('queue'));
$('detail-older').onclick = action(() => detail(state.selected.id, true));
$('review-form').onsubmit = action(async () => { await api(`/ops/predictions/${state.selected.id}/review`, 'POST', {disposition: $('review-disposition').value, note: $('review-note').value}); $('review-message').textContent = 'Review saved in the audit trail.'; await detail(state.selected.id); });
$('label-form').onsubmit = action(async () => { await api(`/ops/predictions/${state.selected.id}/label`, 'POST', {fraud: Number($('label-fraud').value), source: $('label-source').value}); $('review-message').textContent = 'Verified outcome saved. Monitoring will use the latest label.'; await detail(state.selected.id); });
$('user-form').onsubmit = action(async () => { await api('/ops/users', 'POST', {username: $('new-username').value, password: $('new-password').value, role: $('new-role').value}); $('user-form').reset(); await users(); notice('Account created.'); });
$('password-form').onsubmit = action(async () => { await api('/auth/password', 'POST', {current_password: $('current-password').value, new_password: $('next-password').value}); clear(); notice('Password changed. Sign in with your new password.'); });
$('queue-refresh').onclick = action(() => queue()); $('decision-filter').onchange = action(() => queue()); $('more-history').onclick = action(() => queue(true));
$('audit-refresh').onclick = action(() => audit()); $('more-audit').onclick = action(() => audit(true));
$('monitor-refresh').onclick = action(monitoring); $('releases-refresh').onclick = action(releases);
$('rollback').onclick = action(async () => { if (!confirm('Restore the previously active model for new predictions?')) return; const r = await api('/ops/rollback', 'POST'); await releases(); notice(`Rolled back to ${r.active}.`); });
$('history-export').onclick = () => { const fields = ['id','transaction_id','at','model','amount','probability','threshold','decision','review','fraud']; const escape = v => '"' + String(v ?? '').replace(/^[=+@-]/,"'$&").replaceAll('"','""') + '"'; const csv = [fields, ...state.rows.map(r => fields.map(f => r[f]))].map(row => row.map(escape).join(',')).join('\r\n'); const url = URL.createObjectURL(new Blob([csv], {type:'text/csv'})); const a = node('a'); a.href = url; a.download = 'fraudguard-review-history.csv'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); };

$('mfa-confirm').onsubmit = action(async () => {
  const result = await api('/auth/mfa/confirm', 'POST', {token: state.enrollment, code: $('mfa-code').value});
  state.enrollment = ''; $('mfa-secret').value = ''; $('mfa-code').value = ''; $('mfa-confirm').hidden = true;
  $('recovery-codes').textContent = result.recovery_codes.join('\n'); $('mfa-recovery').hidden = false;
});
$('mfa-finish').onclick = () => { clear(); notice('Authenticator enabled. Sign in with your password and the next code from your phone.'); };

function uploadState(title, message, value) {
  $('upload-feedback').hidden = false; $('upload-stage').textContent = title; $('upload-note').textContent = message;
  if (value === null) $('upload-progress').removeAttribute('value'); else $('upload-progress').value = value;
}
function cards(root, values) {
  root.replaceChildren(); for (const [label, value, note] of values) { const card = node('article', undefined, 'panel metric-card'); card.append(node('span', label), node('strong', value), node('small', note)); root.append(card); }
}
async function overview() {
  const d = await api('/ops/overview'); state.overview = d;
  $('overview-scope').textContent = `${d.scope} · Last seven calendar days (UTC) · Updated ${date(d.as_of)}`;
  cards($('overview-metrics'), [['Transactions', d.transactions.toLocaleString(), 'Scored in the seven-day window'], ['Flagged for review', d.flagged.toLocaleString(), d.transactions ? `${pct(d.flagged/d.transactions)} of transactions in this window` : 'No transactions in this window'], ['Open review backlog', d.backlog.toLocaleString(), 'Flagged and unreviewed across retained history'], ['Model availability', d.model.available ? 'Available' : 'Unavailable', 'Availability is not an accuracy measure']]);
  const chart = $('activity-chart'); chart.replaceChildren(); const max = Math.max(1,...d.daily.map(r=>r.transactions));
  for (const r of d.daily) { const label = new Date(r.day*1000).toLocaleDateString(undefined,{month:'short',day:'numeric',timeZone:'UTC'}); const col = node('div', undefined, 'activity-day'); col.append(node('strong',String(r.transactions))); const meter = node('meter'); meter.min=0; meter.max=max; meter.value=r.transactions; meter.setAttribute('aria-label',`${label}: ${r.transactions} transactions, ${r.flagged} flagged`); col.append(meter,node('span',label),node('small',`${r.flagged} flagged`)); chart.append(col); }
  table($('activity-table'),['Day (UTC)','Transactions','Flagged'],d.daily,r=>[new Date(r.day*1000).toISOString().slice(0,10),r.transactions,r.flagged]);
  $('model-health').replaceChildren(node('strong', d.model.available ? 'Model loaded and ready to score' : 'Model unavailable', d.model.available ? 'ops-good' : 'ops-alert'),node('p',d.model.version || 'No active model'),node('p',d.worker_count ? `Scoring pool configured with ${d.worker_count} workers. See Monitoring for recent worker failures.` : 'Scoring runs inside the API process.'));
  table($('overview-recent'),['Transaction','Risk','Recommendation','Review','Details'],d.recent,r=>[r.transaction_id,pct(r.probability),r.decision,r.review,button('Open →',()=>openReview(r))]);
  if (!d.recent.length) $('overview-recent').append(node('p','Start by downloading the sample CSV and uploading your first batch from Transactions & upload.','onboarding-note'));
}
const stages = [
  ['Upload','Start with valid transaction features','Upload CSV or JSON containing Amount and V1–V28. The browser checks the format before sending up to 100 transactions. A card number or ordinary bank statement is not enough.'],
  ['Protect','Authenticate and control traffic','The API checks the named-user session or service key, validates the input, and applies request and transaction quotas. Excess requests receive retry guidance.'],
  ['Score','Select a scoring worker','The coordinator uses a least-busy worker when the worker pool is enabled. A failed worker is temporarily excluded; local deployments can score inside the API.'],
  ['Model','Use one approved model version','The batch keeps one model version and decision threshold throughout scoring, even if an administrator activates another version. A risk score is a recommendation.'],
  ['Review','Save predictions for human review','The coordinator saves scores and an audit event. Analysts investigate their submissions; administrators can review all retained records.'],
  ['Learn','Record evidence and monitor quality','Verified outcomes are separate from review decisions. Delayed fraud labels support performance checks; distribution monitoring helps detect changing inputs. Retraining is not automatic.']
];
async function flow() {
  if (!state.overview) state.overview = await api('/ops/overview');
  renderFlow();
}
function renderFlow() {
  $('flow-stages').replaceChildren(); stages.forEach(([label], i) => { const b=node('button',`${i+1}. ${label}`,'flow-stage'); b.type='button'; b.setAttribute('aria-pressed',String(i===state.flow)); b.onclick=()=>{state.flow=i;renderFlow();}; $('flow-stages').append(b); });
  const [_,title,description] = stages[state.flow]; $('flow-explanation').replaceChildren(node('p',`Stage ${state.flow+1} of ${stages.length}`,'eyebrow'),node('h3',title),node('p',description));
  $('flow-back').disabled=state.flow===0; $('flow-next').disabled=state.flow===stages.length-1;
  $('flow-mode').textContent=state.overview?.worker_count ? `This deployment is configured with ${state.overview.worker_count} scoring workers. It still has one workspace coordinator and shared storage.` : 'This deployment uses local scoring. No separate scoring workers are configured.';
}
$('overview-refresh').onclick=action(overview); $('overview-upload').onclick=action(()=>section('queue')); $('overview-monitor').onclick=action(()=>section('monitor'));
$('flow-back').onclick=()=>{state.flow=Math.max(0,state.flow-1);renderFlow();}; $('flow-next').onclick=()=>{state.flow=Math.min(stages.length-1,state.flow+1);renderFlow();};
$('copy-mfa').onclick=action(async()=>{await navigator.clipboard.writeText($('mfa-secret').value);notice('Setup key copied. Paste it into your authenticator and keep it private.');});
$('mfa-cancel').onclick=()=>{if (!$('mfa-recovery').hidden && !confirm('Have you saved your recovery codes? They will not be shown again.')) return; clear();};
$('username').addEventListener('input',()=>{ $('username').setCustomValidity($('username').value.includes('@') ? 'Use your assigned username, such as naveen, rather than an email address.' : ''); });

window.addEventListener('hashchange', action(async () => {
  const route = location.hash.slice(1); if (!state.user || route === state.route) return;
  if (route.startsWith('transaction/')) await detail(decodeURIComponent(route.slice(12)));
  else if (['overview','queue','flow','monitor','releases','audit','users','account'].includes(route)) await section(route);
}));
