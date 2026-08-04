/* cathedral console — talks to the BYOK gateway and nothing else.
 *
 * The key lives in this tab (sessionStorage) and leaves only as an
 * Authorization header on the same-origin /_api alias. Nothing is persisted:
 * every number on screen came from Cathedral this page load.
 */

const PREFIX = location.pathname.startsWith('/cathedral') ? '/cathedral' : '';
const API = PREFIX + '/_api';
const KEY_STORE = 'cathedral.key';
const CONFIRM_USD = 0.20;   // the API enforces this too; the UI just tells the truth about it

let KEY = sessionStorage.getItem(KEY_STORE) || '';
let PRICES = null;          // {per_execution_usd, hourly_keep_alive_usd, source}
let GATES = null;           // {ready, gates, runtime_image}
let PAYER = '';             // cat:… fingerprint of whoever is paying
let POLL = null;

// ── plumbing ───────────────────────────────────────────────────────────

async function api(method, path, body) {
  const headers = {};
  if (KEY) headers['Authorization'] = 'Bearer ' + KEY;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(API + path, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload;
  try { payload = await r.json(); } catch (e) { payload = { error: await r.text() }; }
  if (!r.ok) throw Object.assign(new Error(explain(payload, r.status)), { status: r.status, payload });
  return payload;
}

/* FastAPI wraps our errors in {detail: …}, and detail is often the dict the
   gateway built (error/why/how). Read it out rather than showing [object]. */
function explain(payload, status) {
  const d = (payload && payload.detail !== undefined) ? payload.detail : payload;
  if (typeof d === 'string') return `${status}: ${d}`;
  if (d && typeof d === 'object') {
    const head = d.error || d.message || `HTTP ${status}`;
    const tail = d.why || d.how || d.reason || (d.detail && (d.detail.message || d.detail.error));
    return tail ? `${head} — ${tail}` : head;
  }
  return `HTTP ${status}`;
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const usd = (n) => '$' + Number(n).toFixed(2);

function toast(msg, kind) {
  const el = document.createElement('div');
  if (kind) el.className = kind;
  el.textContent = msg;
  $('toast').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 9000 : 5000);
}

function chip(id, text, kind) {
  const el = $(id);
  el.textContent = text;
  el.className = 'chip' + (kind ? ' ' + kind : '');
}

/* argv, the way the CLI takes it: a JSON array, or a shell-ish line. */
function argv(text) {
  const s = (text || '').trim();
  if (s.startsWith('[')) {
    try { const a = JSON.parse(s); if (Array.isArray(a)) return a.map(String); } catch (e) { /* fall through */ }
  }
  const out = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(s))) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

function workerId(o) {
  if (!o || typeof o !== 'object') return null;
  return o.worker_id || o.id || o.workerId || (o.worker && (o.worker.worker_id || o.worker.id)) || null;
}

// ── tabs ───────────────────────────────────────────────────────────────

document.querySelectorAll('.rail .tab').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.rail .tab').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === b.dataset.tab));
  if (b.dataset.tab === 'workers') loadWorkers();
}));

// ── the key ────────────────────────────────────────────────────────────

async function useKey(k) {
  KEY = (k || '').trim();
  if (!KEY) { toast('paste a cat_sk_… key first', 'bad'); return; }
  if (!KEY.startsWith('cat_sk_')) toast('that does not look like a cat_sk_… key — trying anyway');
  try {
    const me = await api('GET', '/me');
    sessionStorage.setItem(KEY_STORE, KEY);
    PAYER = me.pays || '';
    chip('chip-payer', PAYER || 'key accepted', 'ok');
    $('key-out').innerHTML = `<p class="fine">Accepted. This session pays as
      <code>${esc(PAYER)}</code>.</p>`;
    renderBalance(me.credits);
    quotes();
    loadWorkers();
    toast('key accepted — ' + PAYER, 'ok');
  } catch (e) {
    KEY = '';
    sessionStorage.removeItem(KEY_STORE);
    chip('chip-payer', 'no key');
    $('key-out').innerHTML = `<p class="fine" style="color:var(--fail)">${esc(e.message)}</p>`;
    toast(e.message, 'bad');
  }
}

function forgetKey() {
  KEY = ''; PAYER = '';
  sessionStorage.removeItem(KEY_STORE);
  $('key').value = '';
  $('key-out').innerHTML = '';
  $('balance').innerHTML = '<span class="empty">No key yet.</span>';
  $('worker-list').innerHTML = '<span class="empty">No key yet.</span>';
  chip('chip-payer', 'no key');
  quotes();
  toast('key forgotten — revoke it at cathedral.computer/account if it leaked');
}

$('use-key').addEventListener('click', () => useKey($('key').value));
$('key').addEventListener('keydown', (e) => { if (e.key === 'Enter') useKey($('key').value); });
$('forget-key').addEventListener('click', forgetKey);

/* Credit payloads vary by upstream version, so show the headline number when
   one is recognisable and the whole record either way. */
function renderBalance(credits) {
  const c = credits || {};
  const field = ['balance_usd', 'credits_usd', 'available_usd', 'balance', 'amount_usd']
    .find((f) => typeof c[f] === 'number');
  const head = field
    ? `<div class="money">${usd(c[field])}</div><div class="fine">${esc(field)} · pays ${esc(PAYER)}</div>`
    : `<div class="fine">pays ${esc(PAYER)}</div>`;
  $('balance').innerHTML = head + `<pre class="json">${esc(JSON.stringify(c, null, 2))}</pre>`;
}

// ── catalog ────────────────────────────────────────────────────────────

async function loadCatalog() {
  try {
    PRICES = await api('GET', '/prices');
    renderPrices();
  } catch (e) {
    $('prices').textContent = e.message;
    $('hourly').textContent = e.message;
  }
  try {
    const profiles = await api('GET', '/profiles');
    $('profiles-raw').textContent = JSON.stringify(profiles, null, 2);
    const ids = (profiles.profiles || []).map((p) => p.id || p.profile_id).filter(Boolean);
    chip('chip-upstream', ids.length ? 'upstream ok · ' + ids.join(' ') : 'upstream ok', 'ok');
  } catch (e) {
    $('profiles-raw').textContent = e.message;
    chip('chip-upstream', 'upstream unreachable', 'bad');
  }
  try {
    GATES = await api('GET', '/gpu/ready');
    renderGates();
  } catch (e) {
    $('gates').textContent = e.message;
    $('gpu-blocked').textContent = e.message;
  }
  quotes();
}

function renderPrices() {
  const per = PRICES.per_execution_usd || {};
  $('prices').innerHTML = '<div class="grid">' + Object.entries(per).map(([k, v]) => `
    <div class="tile"><div class="unit">${esc(k)}</div>
      <div class="price">${usd(v)}</div>
      <div class="unit">per execution</div></div>`).join('') + '</div>';
  $('price-src').textContent = 'source: ' + (PRICES.source || 'unknown')
    + ' · confirmation required above ' + usd(PRICES.confirm_threshold_usd ?? CONFIRM_USD);

  const hourly = PRICES.hourly_keep_alive_usd || {};
  $('hourly').innerHTML = '<table><tr><th>shape (cpu × gib)</th><th class="num">usd / hour</th></tr>'
    + Object.entries(hourly).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${usd(v)}</td></tr>`).join('')
    + '</table>';

  const sel = $('rent-shape');
  const keep = sel.value;
  sel.innerHTML = Object.keys(hourly).map((k) => `<option>${esc(k)}</option>`).join('');
  if (keep && hourly[keep]) sel.value = keep;
}

function renderGates() {
  const g = GATES.gates || {};
  const row = (k, v) => {
    const pass = v === true || v === 'PASS' || (k === 'availability' && ['available', 'live_testing'].includes(v))
      || (k === 'live_evidence_digest' && !!v);
    return `<tr><td>${esc(k)}</td><td>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</td>
      <td><span class="pill ${pass ? 'pass' : 'fail'}">${pass ? 'PASS' : 'FAIL'}</span></td></tr>`;
  };
  const ops = g.operations || {};
  $('gates').innerHTML = '<table><tr><th>gate</th><th>value</th><th></th></tr>'
    + Object.entries(g).filter(([k]) => k !== 'operations').map(([k, v]) => row(k, v)).join('')
    + Object.entries(ops).map(([k, v]) => row('operations.' + k, v)).join('')
    + '</table>';

  const ready = !!GATES.ready;
  chip('chip-gpu', ready ? 'gpu submittable' : 'gpu gated', ready ? 'ok' : 'warn');
  const blocked = $('gpu-blocked');
  blocked.className = 'blocked' + (ready ? ' ok' : '');
  blocked.textContent = ready
    ? 'gates PASS — runtime ' + (GATES.runtime_image || 'from catalog')
    : 'refused upstream: ' + (GATES.reason || failedGates().join(', ') || 'gates not passing');
  $('gpu-command').disabled = !ready;
  document.querySelector('[data-run="gpu"]').disabled = !ready;
}

function failedGates() {
  const g = (GATES && GATES.gates) || {};
  const bad = [];
  if (!['available', 'live_testing'].includes(g.availability)) bad.push('availability=' + g.availability);
  if (g.customer_enabled !== true) bad.push('customer_enabled=' + g.customer_enabled);
  if (g.cathedral_evidence_status !== 'PASS') bad.push('cathedral_evidence_status=' + g.cathedral_evidence_status);
  if (g.verifier_log_digest_evidence_status !== 'PASS') bad.push('verifier_log_digest=' + g.verifier_log_digest_evidence_status);
  if (!g.live_evidence_digest) bad.push('no live_evidence_digest');
  return bad;
}

// ── the spend guard, said out loud ─────────────────────────────────────

function quote(kind) {
  const per = (PRICES && PRICES.per_execution_usd) || {};
  const hourly = (PRICES && PRICES.hourly_keep_alive_usd) || {};
  if (kind === 'cpu') return Math.min(Number($('cpu-spend').value || 0), per['attest.v1'] ?? 0.2);
  if (kind === 'gpu') return Math.max(Number($('gpu-spend').value || 0), per['cc_gpu'] ?? 3);
  if (kind === 'rent') return Number($('rent-spend').value || 0);
  return 0;
}

/* Redraw all three authorize blocks. Above the threshold the box arms itself
   and the submit stays refused until the payer ticks it — the same contract
   the API enforces, shown before the round trip instead of after. */
function quotes() {
  ['cpu', 'rent', 'gpu'].forEach((kind) => {
    const price = quote(kind);
    const box = $(kind + '-auth');
    const armed = price > (PRICES?.confirm_threshold_usd ?? CONFIRM_USD);
    const was = box.querySelector('input')?.checked || false;
    box.className = 'authorize' + (armed ? ' armed' : '');
    if (!armed) {
      box.innerHTML = `${usd(price)} — under the ${usd(CONFIRM_USD)} threshold, no confirmation needed.`;
      return;
    }
    box.innerHTML = `<label class="inline"><input type="checkbox" ${was ? 'checked' : ''}>
      I authorize <b>${usd(price)}</b> against ${esc(PAYER || 'my own')} credits</label>`;
  });
  if (PRICES) {
    const per = PRICES.per_execution_usd || {};
    $('tag-cpu').textContent = usd(per['attest.v1'] ?? 0.2);
    $('tag-gpu').textContent = usd(per['cc_gpu'] ?? 3);
    const h = PRICES.hourly_keep_alive_usd || {};
    const shape = $('rent-shape').value;
    if (h[shape]) $('tag-rent').textContent = usd(h[shape]) + '/hr';
  }
}

/* Never authorize on the payer's behalf. Below the threshold there is no box
   and we send false: the API asks for consent whenever the real price needs
   it, so a quote that has drifted stale gets refused upstream instead of
   silently confirmed here. */
function confirmed(kind) {
  const box = $(kind + '-auth').querySelector('input');
  return box ? box.checked : false;
}

['cpu-spend', 'gpu-spend', 'rent-spend', 'rent-shape', 'rent-minutes'].forEach((id) =>
  $(id).addEventListener('input', quotes));

// ── running work ───────────────────────────────────────────────────────

async function submit(kind) {
  if (!KEY) { toast('add your key on the Account tab first', 'bad'); return; }
  const btn = document.querySelector(`[data-run="${kind}"]`);
  btn.disabled = true;
  try {
    let out;
    if (kind === 'free') {
      out = await api('POST', '/free-test', {});
    } else if (kind === 'cpu') {
      out = await api('POST', '/run', {
        image: $('cpu-image').value.trim(),
        command: argv($('cpu-command').value),
        minutes: Number($('cpu-minutes').value),
        max_spend_usd: Number($('cpu-spend').value),
        egress: $('cpu-egress').value,
        confirm: confirmed('cpu'),
      });
    } else if (kind === 'rent') {
      const [cpu, gib] = ($('rent-shape').value || '').split('x').map(Number);
      out = await api('POST', '/rent', {
        name: $('rent-name').value.trim() || 'sealed-worker',
        image: $('rent-image').value.trim() || null,
        minutes: Number($('rent-minutes').value),
        cpu: cpu || null,
        memory_gib: gib || null,
        ssh_public_key: $('rent-ssh').value.trim() || null,
        max_spend_usd: Number($('rent-spend').value),
        confirm: confirmed('rent'),
      });
    } else if (kind === 'gpu') {
      out = await api('POST', '/gpu', {
        command: argv($('gpu-command').value),
        image: $('gpu-image').value.trim() || null,
        minutes: Number($('gpu-minutes').value),
        max_spend_usd: Number($('gpu-spend').value),
        max_attempts: Number($('gpu-attempts').value),
        confirm: confirmed('gpu'),
      });
    }
    $('submitted').hidden = false;
    $('submit-out').textContent = JSON.stringify(out, null, 2);
    const id = workerId(out);
    toast(id ? 'accepted — ' + id : 'accepted', 'ok');
    loadWorkers();
    refreshBalance();
  } catch (e) {
    $('submitted').hidden = false;
    $('submit-out').textContent = JSON.stringify(e.payload ?? { error: e.message }, null, 2);
    toast(e.message, 'bad');
  } finally {
    btn.disabled = kind === 'gpu' ? !(GATES && GATES.ready) : false;
  }
}

document.querySelectorAll('[data-run]').forEach((b) =>
  b.addEventListener('click', () => submit(b.dataset.run)));

// ── workers ────────────────────────────────────────────────────────────

async function loadWorkers() {
  if (!KEY) return;
  try {
    const out = await api('GET', '/workers');
    const list = Array.isArray(out) ? out : (out.workers || out.items || []);
    if (!list.length) {
      $('worker-list').innerHTML = '<span class="empty">No workers yet.</span>';
      return;
    }
    $('worker-list').innerHTML = '<table><tr><th>worker</th><th>profile</th><th>state</th>'
      + '<th>created</th><th></th></tr>' + list.map((w) => {
        const id = workerId(w) || '—';
        const state = w.state || w.status || w.worker_status || '—';
        return `<tr><td><code>${esc(id)}</code></td>
          <td>${esc(w.profile || w.name || '—')}</td>
          <td>${esc(state)}</td>
          <td>${esc(w.created_at || w.created || '—')}</td>
          <td><button class="ghost" data-worker="${esc(id)}">state</button>
              <button class="ghost" data-receipt="${esc(id)}">receipt</button>
              <button class="ghost" data-stop="${esc(id)}">stop</button></td></tr>`;
      }).join('') + '</table>';
  } catch (e) {
    $('worker-list').innerHTML = `<span class="empty">${esc(e.message)}</span>`;
  }
}

$('refresh-workers').addEventListener('click', loadWorkers);
$('poll').addEventListener('change', (e) => {
  clearInterval(POLL);
  POLL = e.target.checked ? setInterval(loadWorkers, 10000) : null;
});

$('worker-list').addEventListener('click', async (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;
  const { worker, receipt, stop } = btn.dataset;
  try {
    if (worker) show('Worker ' + worker, await api('GET', '/workers/' + encodeURIComponent(worker)));
    if (receipt) show('Receipt ' + receipt, await api('GET', '/receipts/' + encodeURIComponent(receipt)));
    if (stop) {
      if (!window.confirm('Tear down ' + stop + '? Hourly billing stops after provider cleanup.')) return;
      const out = await api('DELETE', '/workers/' + encodeURIComponent(stop));
      toast('teardown requested', 'ok');
      show('Teardown ' + stop, out);
      loadWorkers();
    }
  } catch (err) {
    if (receipt && err.status === 425) {
      toast('receipt not publishable yet — execution or teardown still pending', 'bad');
    } else {
      toast(err.message, 'bad');
    }
  }
});

// ── credits ────────────────────────────────────────────────────────────

async function loadPacks() {
  try {
    const out = await api('GET', '/credits/packs');
    const list = Array.isArray(out) ? out : (out.packs || out.items || []);
    if (!list.length) { $('packs').innerHTML = `<pre class="json">${esc(JSON.stringify(out, null, 2))}</pre>`; return; }
    $('packs').innerHTML = '<div class="grid">' + list.map((p) => {
      const id = p.id || p.pack_id || '';
      const amount = p.credits_usd ?? p.amount_usd ?? p.value_usd ?? p.credits;
      const price = p.price_usd ?? p.cost_usd ?? amount;
      return `<div class="tile"><div class="unit">${esc(id)}</div>
        <div class="price">${amount != null ? usd(amount) : '—'}</div>
        <div class="unit">costs ${price != null ? usd(price) : '—'}</div>
        <button class="ghost" data-pack="${esc(id)}" style="margin-top:8px">Top up</button></div>`;
    }).join('') + '</div>';
  } catch (e) {
    $('packs').textContent = e.message;
  }
}

$('packs').addEventListener('click', async (e) => {
  const id = e.target.closest('button')?.dataset.pack;
  if (!id) return;
  if (!KEY) { toast('add your key on the Account tab first', 'bad'); return; }
  try {
    const out = await api('POST', '/credits/checkout', { pack_id: id });
    const url = out.url || out.checkout_url || out.payment_url;
    const session = out.session_id || out.id || '';
    if (session) $('session-id').value = session;
    if (url) { window.open(url, '_blank', 'noopener'); toast('checkout opened — pay in your own name, then Verify', 'ok'); }
    show('Checkout ' + id, out);
  } catch (err) { toast(err.message, 'bad'); }
});

$('verify-payment').addEventListener('click', async () => {
  const session_id = $('session-id').value.trim();
  if (!session_id) { toast('paste the session_id from the checkout redirect', 'bad'); return; }
  try {
    const out = await api('POST', '/credits/verify', { session_id });
    $('verify-out').innerHTML = `<pre class="json">${esc(JSON.stringify(out, null, 2))}</pre>`;
    toast('settled', 'ok');
    refreshBalance();
  } catch (e) {
    $('verify-out').innerHTML = `<pre class="json">${esc(JSON.stringify(e.payload ?? { error: e.message }, null, 2))}</pre>`;
    toast(e.message, 'bad');
  }
});

async function refreshBalance() {
  if (!KEY) return;
  try { renderBalance(await api('GET', '/credits')); } catch (e) { /* the balance is a courtesy, not the job */ }
}

// ── modal ──────────────────────────────────────────────────────────────

let SHOWN = null;
function show(title, payload) {
  SHOWN = { title, payload };
  $('modal-title').textContent = title;
  $('modal-body').textContent = JSON.stringify(payload, null, 2);
  $('modal').hidden = false;
}
$('modal-close').addEventListener('click', () => { $('modal').hidden = true; });
$('modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('modal').hidden = true; });
$('modal-save').addEventListener('click', () => {
  if (!SHOWN) return;
  const blob = new Blob([JSON.stringify(SHOWN.payload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = SHOWN.title.replace(/\s+/g, '-').toLowerCase() + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
});

// ── boot ───────────────────────────────────────────────────────────────

loadCatalog();
loadPacks();
if (KEY) { $('key').value = KEY; useKey(KEY); }
