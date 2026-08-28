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
let FLEET = null;           // {classes, totals} — every hardware class, flattened
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
  if (b.dataset.tab === 'evidence') loadTrustedKeys();
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
  } catch (e) {
    $('profiles-raw').textContent = e.message;
  }
  try {
    FLEET = await api('GET', '/inventory');
    renderFleet();
    const t = FLEET.totals || {};
    chip('chip-upstream', `upstream ok · ${t.hardware_classes} classes · ${t.shapes} shapes`, 'ok');
  } catch (e) {
    $('fleet').innerHTML = `<div class="card"><span class="empty">${esc(e.message)}</span></div>`;
    $('fleet-sum').textContent = e.message;
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

/* One card per hardware class, one row per orderable shape. The catalog hides
   both GPUs and the four worker sizes inside profiles; this is the whole list,
   with the gate that shuts a class printed next to it rather than left implied. */
function renderFleet() {
  const t = FLEET.totals || {};
  const range = (t.cheapest_usd != null)
    ? ` · ${usd(t.cheapest_usd)}–${usd(t.dearest_usd)}` : '';
  $('fleet-sum').innerHTML =
    `<b>${t.hardware_classes}</b> hardware classes · <b>${t.shapes}</b> shapes ·
     <b>${t.orderable_shapes}</b> orderable${range}
     <span class="dots">${['live', 'preview', 'unavailable']
       .filter((s) => t[s]).map((s) => `<span class="pill ${pillOf(s)}">${t[s]} ${s}</span>`).join('')}</span>`;

  $('fleet').innerHTML = (FLEET.classes || []).map(rig).join('');
}

const pillOf = (status) => status === 'live' ? 'pass' : (status === 'preview' ? 'warn' : 'fail');

function rig(c) {
  const hw = c.hardware || {};
  const ev = c.evidence || {};
  const cpus = c.shapes.map((s) => s.cpu).filter(Boolean);
  const mems = c.shapes.map((s) => s.memory_gib).filter(Boolean);
  const specs = [
    cpus.length ? `${Math.min(...cpus)}–${Math.max(...cpus)} vCPU` : null,
    mems.length ? `${Math.min(...mems)}–${Math.max(...mems)} GiB` : null,
    hw.gpu_type ? `${hw.gpu_count || 1}× ${hw.gpu_type.replace(/_/g, ' ')}` : null,
    hw.gpu_memory_gib ? `${hw.gpu_memory_gib} GiB VRAM` : null,
    hw.cpu_tee, hw.machine_type, hw.provider,
    (hw.provisioning || []).join('/') || null,
    hw.network_egress ? `egress ${hw.network_egress}` : null,
    hw.capacity, ev.attests,
  ].filter(Boolean);

  return `<div class="card rig">
    <div class="row between rig-head">
      <div><span class="rig-name">${esc(c.name)}</span>
        <code>${esc(c.execution_class)}</code>
        <span class="fine inline-fine">via ${esc((c.profiles || []).join(', ') || '—')}</span></div>
      <span class="pill ${pillOf(c.status)}">${esc(c.status)}</span>
    </div>
    <p class="fine">${esc(c.what || '')}</p>
    <div class="specs">${specs.map((s) => `<span>${esc(s)}</span>`).join('')}</div>
    <table><tr><th>shape</th><th>size</th><th>lifetime</th><th class="num">price</th><th></th></tr>
      ${c.shapes.map((s) => shapeRow(c, s)).join('')}</table>
    ${(c.blockers || []).length
      ? `<div class="blocked">shut upstream: ${c.blockers.map(esc).join(' · ')}</div>` : ''}
    ${(c.notes || []).map((n) => `<p class="fine">⚠ ${esc(n)}</p>`).join('')}
    ${ev.checks && Object.keys(ev.checks).length ? `<p class="fine">evidence
      <span class="pill pass">${ev.pass.length} PASS</span>
      ${ev.unproven.length ? `<span class="pill fail">${ev.unproven.length} not proven</span>
        — ${ev.unproven.map((k) => esc(k.replace(/_evidence_status|_status/, ''))).join(', ')}` : ''}</p>` : ''}
    ${hw.runtime_image ? `<p class="fine">runtime <code>${esc(hw.runtime_image)}</code></p>` : ''}
    ${hw.requires_scope ? `<p class="fine">key must carry <code>${esc(hw.requires_scope)}</code></p>` : ''}
  </div>`;
}

function shapeRow(c, s) {
  const size = [s.cpu ? `${s.cpu} vCPU` : null, s.memory_gib ? `${s.memory_gib} GiB` : null,
    s.gpu ? `gpu ${s.gpu}` : null].filter(Boolean).join(' · ')
    || (c.hardware.machine_type || '—');
  const price = s.price_usd == null
    ? (s.quoted ? 'quoted before reservation' : '—')
    : `${usd(s.price_usd)} / ${esc(s.unit || 'execution')}`;
  const included = s.minutes_included ? `<div class="unit">${s.minutes_included} min included</div>` : '';
  return `<tr><td>${esc(s.name)}<div class="unit">${esc(s.profile || '')}</div></td>
    <td>${esc(size)}</td><td>${esc((s.lifetimes || []).join(', ') || '—')}</td>
    <td class="num">${price}${included}</td>
    <td><button class="ghost" data-order="${esc(s.order || '')}"
      data-shape="${s.cpu && s.memory_gib ? esc(s.cpu + 'x' + s.memory_gib) : ''}"
      ${s.orderable && s.order ? '' : 'disabled'}>order</button></td></tr>`;
}

/* "Order" is a jump, never a submission: it opens the Run tab on the right card
   with the shape selected, and the payer still authorizes the spend there. */
$('fleet').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-order]');
  if (!btn) return;
  const { order, shape } = btn.dataset;
  if (shape && order === 'rent') {
    const sel = $('rent-shape');
    if ([...sel.options].some((o) => o.value === shape)) sel.value = shape;
  }
  document.querySelector('.rail .tab[data-tab="run"]').click();
  quotes();
  const card = { run: 'cpu-image', rent: 'rent-name', gpu: 'gpu-command' }[order];
  const el = card && $(card);
  if (el) { el.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'center' }); el.focus(); }
});

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

// ── evidence ───────────────────────────────────────────────────────────

/* Verification needs no key: the receipt is the whole input. That is the point
   of keeping one — it stays checkable after the worker, the key, and the
   credits are gone. */
async function verifyReceipt(doc, into) {
  const el = $(into);
  el.innerHTML = '<p class="fine">verifying…</p>';
  try {
    const out = await api('POST', '/receipts/verify', doc);
    const ok = !!out.verified;
    const rows = (out.structure?.checks || []).map((c) =>
      `<tr><td>${esc(c.check)}</td><td>${esc(c.detail ?? '')}</td>
       <td><span class="pill ${c.ok ? 'pass' : 'fail'}">${c.ok ? 'PASS' : 'FAIL'}</span></td></tr>`).join('');
    el.innerHTML = `
      <div class="blocked ${ok ? 'ok' : ''}">${ok
        ? `signature valid — signed by ${esc(out.signed_by)}`
        : esc(out.error || 'not verified') }</div>
      ${rows ? `<table><tr><th>check</th><th>value</th><th></th></tr>${rows}</table>` : ''}
      ${(out.structure?.notes || []).map((n) => `<p class="fine">${esc(n)}</p>`).join('')}
      ${out.proves ? `<p class="fine">Proves: ${esc(out.proves)}</p>` : ''}`;
    toast(ok ? 'signature valid' : (out.error || 'not verified'), ok ? 'ok' : 'bad');
  } catch (e) {
    el.innerHTML = `<p class="fine" style="color:var(--fail)">${esc(e.message)}</p>`;
    toast(e.message, 'bad');
  }
}

$('do-verify').addEventListener('click', () => {
  const text = $('verify-receipt').value.trim();
  if (!text) { toast('paste a receipt first', 'bad'); return; }
  let doc;
  try { doc = JSON.parse(text); } catch (e) { toast('that is not valid JSON', 'bad'); return; }
  verifyReceipt(doc, 'verify-result');
});

async function loadTrustedKeys() {
  try {
    const out = await api('GET', '/receipts/trusted-keys');
    const keys = out.keys || {};
    $('trusted-keys').innerHTML = '<table><tr><th>key id</th><th>alg</th><th>status</th>'
      + '<th>valid until</th></tr>' + Object.entries(keys).map(([id, k]) => `
        <tr><td><code>${esc(id)}</code></td><td>${esc(k.algorithm)}</td>
          <td><span class="pill ${k.status === 'active' ? 'pass' : 'fail'}">${esc(k.status)}</span></td>
          <td>${esc(k.valid_until || '—')}</td></tr>`).join('') + '</table>';
  } catch (e) {
    $('trusted-keys').textContent = e.message;
  }
}

// ── modal ──────────────────────────────────────────────────────────────

let SHOWN = null;

/* Only offer Verify on something shaped like a signed receipt — a teardown
   acknowledgement has nothing to check. */
const verifiable = (p) => !!(p && typeof p === 'object'
  && (p.signature || String(p.schema || p.receipt_schema || '').startsWith('cathedral_customer_receipt')));

function show(title, payload) {
  SHOWN = { title, payload };
  $('modal-title').textContent = title;
  $('modal-body').textContent = JSON.stringify(payload, null, 2);
  $('modal-verify').hidden = !verifiable(payload);
  $('modal').hidden = false;
}

$('modal-verify').addEventListener('click', () => {
  if (!SHOWN) return;
  $('verify-receipt').value = JSON.stringify(SHOWN.payload, null, 2);
  $('modal').hidden = true;
  document.querySelector('.rail .tab[data-tab="evidence"]').click();
  verifyReceipt(SHOWN.payload, 'verify-result');
});
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
loadTrustedKeys();
if (KEY) { $('key').value = KEY; useKey(KEY); }
