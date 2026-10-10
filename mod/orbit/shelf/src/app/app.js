/* shelf console — vanilla ES module, no dependencies, no build step.
 *
 * Every number on this page comes from the API, which computes it from the
 * directory at the moment it is asked. Nothing is cached here: an operator
 * tool that renders a stale picture of the thing it is inspecting is worse
 * than no tool, so a refresh is a re-read and the page holds no model of its
 * own beyond what it is currently drawing.
 *
 * Redaction is not this file's job. It happens on the API's read path, before
 * a value is ever serialised, because a page-side filter is one curl away from
 * irrelevant. What arrives here is already safe to draw.
 */

const API = '_api';                       // same origin — the app server proxies it
const $ = (id) => document.getElementById(id);

const state = { root: null, prefix: '', gcPlan: null };

// ── transport ────────────────────────────────────────────────

async function get(path) {
  const res = await fetch(`${API}${path}`);
  const body = await res.json().catch(() => ({ error: `bad response (${res.status})` }));
  if (body && body.error && !res.ok) throw new Error(body.error);
  return body;
}

async function post(path, payload) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  return res.json();
}

// ── formatting ───────────────────────────────────────────────

const bytes = (n) => {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'K', 'M', 'G', 'T'];
  let v = n, i = 0;
  while (Math.abs(v) >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return i === 0 ? `${v.toFixed(0)}B` : `${v.toFixed(1)}${units[i]}`;
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const days = (d) => (d === null || d === undefined ? '—'
  : d < 1 ? `${Math.round(d * 24)}h` : `${Math.round(d)}d`);

// ── tooltip: bars and cells get a hover layer, per the house rules ──

const tip = $('tip');
function showTip(html, evt) {
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 14;
  const box = tip.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + box.width > window.innerWidth) x = evt.clientX - box.width - pad;
  if (y + box.height > window.innerHeight) y = evt.clientY - box.height - pad;
  tip.style.left = `${Math.max(4, x)}px`;
  tip.style.top = `${Math.max(4, y)}px`;
}
const hideTip = () => { tip.hidden = true; };

function hoverable(el, html) {
  el.addEventListener('mousemove', (e) => showTip(html, e));
  el.addEventListener('mouseleave', hideTip);
}

// ── header ───────────────────────────────────────────────────

function setHealth(kind, text) {
  const glyph = { good: '●', warning: '▲', critical: '■', idle: '◌' }[kind] || '◌';
  const el = $('health');
  el.className = `pill pill-${kind}`;
  el.innerHTML = `<span class="pill-icon" aria-hidden="true">${glyph}</span><span class="pill-text">${esc(text)}</span>`;
}

async function loadHeader() {
  try {
    const info = await get('/');
    $('reads').textContent = info.reads;
    $('bound').textContent = info.bound + (info.public ? ' — PUBLIC' : '');
    if (info.public) setHealth('warning', 'bound off-box');
  } catch (err) {
    setHealth('critical', 'API down');
    throw err;
  }
}

// ── SPACE ────────────────────────────────────────────────────

async function loadSpace() {
  const data = await get('/space');
  const t = data.total;

  const busiest = data.modules.reduce(
    (best, m) => (m.idle_days !== null && (!best || m.idle_days < best.idle_days) ? m : best), null);

  $('space-kpis').innerHTML = [
    kpi('total state', bytes(t.bytes), `${t.modules} modules, ${t.files.toLocaleString()} files`),
    kpi('largest', data.modules[0] ? data.modules[0].size : '—',
        data.modules[0] ? data.modules[0].module : ''),
    kpi('vendored', bytes(t.vendor_bytes), 'caches, not authored data'),
    kpi('most recent write', busiest ? days(busiest.idle_days) + ' ago' : '—',
        busiest ? busiest.module : ''),
  ].join('');

  state.space = data.modules;
  drawBars(TOP_N);
  if (data.modules[0]) drill(data.modules[0].module);
  loadBig();
}

/* Seventy-two rows is not a chart, it is a directory listing with decoration:
 * past the first twenty every bar is a pixel wide and the tail says nothing
 * except "these are all small". So the tail is folded into one honest line
 * that names what it is hiding and how much, and can be opened. */
const TOP_N = 20;

function drawBars(limit) {
  const all = state.space || [];
  const shown = limit ? all.slice(0, limit) : all;
  const rest = all.slice(shown.length);

  const max = all.reduce((m, r) => Math.max(m, r.bytes), 0) || 1;
  $('space-legend').hidden = !all.some((r) => r.vendor_bytes > 0);

  const host = $('space-bars');
  host.innerHTML = '';
  shown.forEach((row) => {
    const el = document.createElement('div');
    el.className = 'bar-row';
    const own = Math.max(0, row.own_bytes);
    const ownPct = (own / max) * 100;
    const vendPct = (row.vendor_bytes / max) * 100;
    el.innerHTML = `
      <span class="bar-name" title="${esc(row.module)}">${esc(row.module)}</span>
      <span class="bar-track">
        <span class="bar-fill" style="width:${ownPct.toFixed(3)}%"></span>
        ${row.vendor_bytes > 0 ? `<span class="bar-fill vendor" style="width:${vendPct.toFixed(3)}%"></span>` : ''}
      </span>
      <span class="bar-val">${esc(row.size)}</span>`;
    hoverable(el, `<b>${esc(row.module)}</b><br>
      ${esc(row.own_size)} written${row.vendor_bytes > 0 ? ` · ${esc(row.vendor_size)} vendored` : ''}<br>
      ${row.files.toLocaleString()} files · idle ${days(row.idle_days)}`);
    el.addEventListener('click', () => {
      document.querySelectorAll('.bar-row').forEach((r) => r.classList.remove('is-on'));
      el.classList.add('is-on');
      drill(row.module);
    });
    host.appendChild(el);
  });

  if (rest.length) {
    const tail = rest.reduce((n, r) => n + r.bytes, 0);
    const more = document.createElement('button');
    more.className = 'btn tail';
    more.type = 'button';
    more.textContent = `+ ${rest.length} smaller modules, ${bytes(tail)} between them — show all`;
    more.addEventListener('click', () => drawBars(0));
    host.appendChild(more);
  } else if (all.length > TOP_N) {
    const less = document.createElement('button');
    less.className = 'btn tail';
    less.type = 'button';
    less.textContent = `show the top ${TOP_N} only`;
    less.addEventListener('click', () => drawBars(TOP_N));
    host.appendChild(less);
  }
}

function kpi(label, value, note, tone) {
  return `<div class="kpi">
    <div class="kpi-label">${esc(label)}</div>
    <div class="kpi-value${tone ? ` is-${tone}` : ''}">${esc(value)}</div>
    <div class="kpi-note">${esc(note || '')}</div>
  </div>`;
}

async function drill(module) {
  $('drill-cap').textContent = `~/.mod/${module}, one level in.`;
  $('drill-body').innerHTML = '<p class="loading">…</p>';
  const data = await get(`/usage/${encodeURIComponent(module)}`);
  if (!data.exists) { $('drill-body').innerHTML = '<p class="err">gone</p>'; return; }
  $('drill-body').innerHTML = table(
    ['entry', 'size', 'files'],
    data.entries.map((e) => [
      `${esc(e.name)}${e.vendor ? ' <span class="flag">vendor</span>' : ''}`,
      esc(e.size), e.files.toLocaleString()]),
    [false, true, true]);
}

async function loadBig() {
  const data = await get('/big?limit=12');
  $('big-body').innerHTML = table(
    ['file', 'size', 'age'],
    data.files.map((f) => [`<span class="mono-key">${esc(f.path)}</span>`, esc(f.size), days(f.age_days)]),
    [false, true, true]);
}

function table(head, rows, numeric = []) {
  if (!rows.length) return '<p class="muted">nothing here</p>';
  return `<table><thead><tr>${head.map((h, i) =>
    `<th${numeric[i] ? ' class="num"' : ''}>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) =>
      `<td${numeric[i] ? ' class="num"' : ''}>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// ── STORE ────────────────────────────────────────────────────

async function loadRoots() {
  const { roots } = await get('/roots');
  const options = roots.map((r) =>
    `<option value="${esc(r.name)}"${r.shared ? ' selected' : ''}>${esc(r.name)}${r.shared ? '  (shared)' : ''}</option>`).join('');
  $('root-pick').innerHTML = options;
  $('snap-root').innerHTML = options;
  state.root = roots.find((r) => r.shared)?.name || roots[0]?.name || 'store';
  $('root-pick').value = state.root;
  $('snap-root').value = state.root;
}

async function loadPrefixes() {
  const data = await get(`/prefixes?root=${encodeURIComponent(state.root)}`);
  const host = $('prefix-body');
  if (!data.exists || !data.prefixes.length) { host.innerHTML = '<p class="muted">empty</p>'; return; }
  host.innerHTML = table(
    ['namespace', 'keys', 'size'],
    [[`<a class="row-link-all" href="#" data-prefix="">all</a>`, String(data.keys), bytes(data.bytes)]].concat(
      data.prefixes.map((p) => [
        `<a href="#" data-prefix="${esc(p.prefix)}">${esc(p.prefix)}${p.shared ? ' <span class="flag">shared</span>' : ''}</a>`,
        String(p.keys), bytes(p.bytes)])),
    [false, true, true]);
  host.querySelectorAll('a[data-prefix]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      state.prefix = a.dataset.prefix;
      loadKeys();
    });
  });
}

async function loadKeys() {
  const search = $('key-search').value.trim();
  const url = `/keys?root=${encodeURIComponent(state.root)}&prefix=${encodeURIComponent(state.prefix)}`
    + `&search=${encodeURIComponent(search)}&limit=300`;
  const data = await get(url);
  $('key-count').textContent = `${data.total} under ${state.prefix || state.root}`;
  $('key-body').innerHTML = table(
    ['key', 'size', 'age'],
    data.keys.map((k) => [
      `<a href="#" data-key="${esc(k.key)}" class="mono-key">${esc(k.key)}</a>`
      + (k.secret ? ' <span class="flag flag-secret">secret</span>' : ''),
      bytes(k.bytes), days(k.age_days)]),
    [false, true, true]);
  $('key-body').querySelectorAll('a[data-key]').forEach((a) => {
    a.addEventListener('click', (e) => { e.preventDefault(); loadValue(a.dataset.key); });
  });
}

async function loadValue(key) {
  $('value-key').textContent = key;
  $('value-body').innerHTML = '<p class="loading">…</p>';
  const data = await get(`/read?root=${encodeURIComponent(state.root)}&key=${encodeURIComponent(key)}`);
  if (!data.found) { $('value-body').innerHTML = '<p class="err">no such key</p>'; return; }

  const meta = `<p class="cap">${esc(data.path)} · ${bytes(data.bytes)} · ${days(data.age_days)} old`
    + `${data.secret ? ' · <span class="flag flag-secret">not opened</span>' : ''}`
    + `${data.redacted ? ' · <span class="flag flag-secret">fields redacted</span>' : ''}</p>`;

  const body = data.secret
    ? '<p class="muted">A secret file. Its bytes were never read — not redacted after the fact, simply not opened.</p>'
    : data.json ? render(data.value)
      : `<pre class="out">${esc(String(data.value ?? ''))}</pre>`;
  $('value-body').innerHTML = meta + body;
}

/* A value renderer that shows shape rather than a wall of JSON. Fingerprints
 * arrive as strings from the API and are flagged here so a redacted field is
 * visibly different from one that merely happens to look like a hash. */
function render(value, depth = 0) {
  if (value === null || value === undefined) return '<span class="muted">null</span>';
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="muted">[]</span>';
    if (depth > 4) return `<span class="muted">[${value.length} items]</span>`;
    return `<div class="nest">${value.slice(0, 60).map((v) => render(v, depth + 1)).join('<br>')}
      ${value.length > 60 ? `<span class="muted">… ${value.length - 60} more</span>` : ''}</div>`;
  }
  if (typeof value === 'object') {
    if (depth > 4) return '<span class="muted">{…}</span>';
    return `<dl class="kv">${Object.entries(value).map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${render(v, depth + 1)}</dd>`).join('')}</dl>`;
  }
  const text = String(value);
  const redacted = text === '[redacted]' || /^sha256:[0-9a-f]{8} \(\d+b\)$/.test(text);
  return `<span class="${redacted ? 'redacted' : ''}">${esc(text)}</span>`;
}

async function runGrep() {
  const q = $('grep-q').value.trim();
  if (!q) return;
  $('value-key').textContent = `grep "${q}"`;
  $('value-body').innerHTML = '<p class="loading">scanning…</p>';
  const data = await get(`/grep?root=${encodeURIComponent(state.root)}&q=${encodeURIComponent(q)}`);
  if (!data.hits.length) {
    $('value-body').innerHTML = `<p class="muted">no key mentions that. ${data.scanned} scanned, ${data.skipped} skipped as secret or oversized.</p>`;
    return;
  }
  $('value-body').innerHTML = `<p class="cap">${data.hits.length} hit(s) · ${data.scanned} keys scanned · ${data.skipped} skipped</p>`
    + data.hits.map((h) => `<div class="nest">
        <a href="#" data-key="${esc(h.key)}" class="mono-key">${esc(h.key)}</a>
        <span class="muted"> ×${h.count}</span>
        <pre class="out">${esc(h.context)}</pre></div>`).join('');
  $('value-body').querySelectorAll('a[data-key]').forEach((a) => {
    a.addEventListener('click', (e) => { e.preventDefault(); loadValue(a.dataset.key); });
  });
}

// ── INTEGRITY ────────────────────────────────────────────────

async function loadIntegrity() {
  const root = encodeURIComponent(state.root || 'store');
  const data = await get(`/verify?root=${root}`);

  const bad = data.corrupt.length;
  $('int-kpis').innerHTML = [
    kpi('blobs checked', String(data.blobs), 'files named by their own hash'),
    kpi('prove their name', String(data.ok), 'rehashed and matched', bad ? null : 'good'),
    kpi('do not', String(bad), bad ? 'the name is a lie' : 'nothing corrupt', bad ? 'critical' : null),
    kpi('records', String(data.records), 'metadata filed under a blob id'),
  ].join('');

  setHealth(bad ? 'critical' : 'good',
    bad ? `${bad} corrupt` : `${data.ok} blobs verified`);

  const parts = [];
  if (bad) {
    parts.push(`<p class="err">These files do not hash to the names they are filed under. Anything that cited one of these ids attested to bytes other than the ones now stored.</p>`);
    parts.push(table(['key', 'filed under', 'actually hashes to', 'size'],
      data.corrupt.map((c) => [
        `<span class="mono-key">${esc(c.key)}</span>`,
        `<span class="mono-key">${esc(c.claimed.slice(0, 16))}…</span>`,
        `<span class="mono-key flag-bad">${esc(c.actual.slice(0, 16))}…</span>`,
        bytes(c.bytes)]),
      [false, false, false, true]));
  } else {
    parts.push('<p class="ok-note"><span aria-hidden="true">●</span> Every blob rehashes to its own name.</p>');
  }
  if (data.misfiled.length) {
    parts.push('<h2>Misfiled records</h2>');
    parts.push(table(['key', 'says its id is'],
      data.misfiled.map((m) => [esc(m.key), esc(m.says)])));
  }
  if (data.unreadable.length) {
    parts.push(`<p class="err">${data.unreadable.length} unreadable: ${data.unreadable.map(esc).join(', ')}</p>`);
  }
  $('verify-body').innerHTML = parts.join('');

  loadStrays(root);
  loadOrphans(root);
}

async function loadStrays(root) {
  const data = await get(`/strays?root=${root}`);
  $('stray-body').innerHTML = data.count
    ? table(['id', 'redundant copy'], data.strays.map((s) => [
        `<span class="mono-key">${esc(s.id.slice(0, 12))}…</span>`,
        `<span class="mono-key">${esc(s.copies.join(', '))}</span>`]))
    : '<p class="ok-note"><span aria-hidden="true">●</span> No bytes filed twice.</p>';
}

async function loadOrphans(root) {
  const data = await get(`/orphans?root=${root}`);
  $('orphan-count').textContent = data.count ? `${bytes(data.reclaimable_bytes)} reclaimable` : '';
  $('orphan-body').innerHTML = data.count
    ? table(['key', 'size', 'age'], data.orphans.slice(0, 40).map((o) => [
        `<span class="mono-key">${esc(o.key)}</span>`, bytes(o.bytes), days(o.age_days)]),
      [false, true, true])
    : '<p class="ok-note"><span aria-hidden="true">●</span> Every blob is referenced.</p>';
}

// ── SNAPSHOTS ────────────────────────────────────────────────

async function makeSnapshot() {
  const out = $('snap-out');
  out.hidden = false;
  out.textContent = 'tarring and hashing…';
  const data = await post('/snapshot', { root: $('snap-root').value, pin: true });
  out.textContent = data.ok
    ? `cid     ${data.cid || '(localfs unavailable — not pinned)'}\n`
      + `sha256  ${data.sha256}\n`
      + `size    ${bytes(data.bytes)} from ${data.files} files\n`
      + (data.skipped_secrets.length
        ? `\nexcluded ${data.skipped_secrets.length} secret file(s):\n  ${data.skipped_secrets.join('\n  ')}`
        : '\nno secret files in this root')
      + (data.note ? `\n\n${data.note}` : '')
    : `error: ${data.error}`;
}

async function inspectSnapshot() {
  const out = $('restore-out');
  out.hidden = false;
  out.textContent = 'fetching…';
  const cid = $('snap-cid').value.trim();
  if (!cid) { out.textContent = 'needs a cid'; return; }
  const data = await get(`/snapshot/${encodeURIComponent(cid)}`);
  out.textContent = data.ok
    ? `${data.files} files, ${bytes(data.bytes)}\n\n`
      + data.entries.slice(0, 60).map((e) => `${bytes(e.bytes).padStart(8)}  ${e.key}`).join('\n')
    : `error: ${data.error}`;
}

async function planRestore() {
  const out = $('restore-out');
  out.hidden = false;
  out.textContent = 'planning…';
  const cid = $('snap-cid').value.trim();
  if (!cid) { out.textContent = 'needs a cid'; return; }
  const data = await post('/restore', { cid, root: $('snap-root').value, confirm: false });
  out.textContent = data.ok
    ? `would write   ${data.would_write} file(s)\n`
      + `already there ${data.conflicts.length} (left alone unless overwrite)\n`
      + (data.unsafe.length ? `refused       ${data.unsafe.length} escaping the root\n` : '')
      + `\n${data.note}\n\nThis console only plans. Run the write from the CLI:\n`
      + `  m shelf/restore ${cid} root=${$('snap-root').value} confirm=True`
    : `error: ${data.error}`;
}

// ── views ────────────────────────────────────────────────────

const loaders = {
  space: loadSpace,
  store: async () => { await loadPrefixes(); await loadKeys(); },
  integrity: loadIntegrity,
  snapshots: async () => {},
};

async function show(view) {
  document.querySelectorAll('.rail-btn').forEach((b) =>
    b.classList.toggle('is-on', b.dataset.view === view));
  document.querySelectorAll('.view').forEach((v) =>
    v.classList.toggle('is-on', v.id === `view-${view}`));
  try {
    await loaders[view]();
  } catch (err) {
    console.error(err);
  }
}

// ── wiring ───────────────────────────────────────────────────

document.querySelectorAll('.rail-btn').forEach((b) =>
  b.addEventListener('click', () => show(b.dataset.view)));

$('refresh').addEventListener('click', () => {
  const on = document.querySelector('.rail-btn.is-on');
  boot(on ? on.dataset.view : 'space');
});

$('root-pick').addEventListener('change', (e) => {
  state.root = e.target.value;
  state.prefix = '';
  loadPrefixes();
  loadKeys();
});

let searchTimer;
$('key-search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadKeys, 220);
});
$('grep-go').addEventListener('click', runGrep);
$('grep-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runGrep(); });

$('gc-plan').addEventListener('click', async () => {
  const out = $('gc-out');
  out.hidden = false;
  out.textContent = 'planning…';
  const plan = await get(`/gc?root=${encodeURIComponent(state.root || 'store')}`);
  state.gcPlan = plan;
  out.textContent = `${plan.count} blob(s) older than ${plan.min_age_days}d, ${bytes(plan.bytes)}\n`
    + `${plan.skipped_young} skipped for being too young to be sure about\n\n`
    + (plan.candidates.length ? plan.candidates.map((c) => `  ${c.key}  ${bytes(c.bytes)}`).join('\n')
      : '  nothing to sweep');
  $('gc-go').disabled = !plan.count;
});

$('gc-go').addEventListener('click', async () => {
  if (!state.gcPlan || !state.gcPlan.count) return;
  if (!window.confirm(`Delete ${state.gcPlan.count} orphaned blob(s), ${bytes(state.gcPlan.bytes)}? This cannot be undone.`)) return;
  const out = $('gc-out');
  out.textContent = 'deleting…';
  const done = await post('/gc', { root: state.root || 'store', confirm: true });
  out.textContent = `removed ${done.removed ? done.removed.length : 0} file(s)\n`
    + (done.failed && done.failed.length ? `failed ${done.failed.length}\n` : '');
  $('gc-go').disabled = true;
  loadIntegrity();
});

$('snap-go').addEventListener('click', makeSnapshot);
$('snap-inspect').addEventListener('click', inspectSnapshot);
$('snap-restore').addEventListener('click', planRestore);

async function boot(view = 'space') {
  try {
    await loadHeader();
    if (!state.root) await loadRoots();
    await show(view);
    // The header pill reports store health, which only the integrity read
    // knows; ask for it once at boot so the badge is true before that tab is
    // ever opened.
    if (view !== 'integrity') {
      const v = await get(`/verify?root=${encodeURIComponent(state.root || 'store')}`);
      setHealth(v.corrupt.length ? 'critical' : 'good',
        v.corrupt.length ? `${v.corrupt.length} corrupt` : `${v.ok} blobs verified`);
    }
  } catch (err) {
    setHealth('critical', 'API down');
    document.querySelectorAll('.loading').forEach((el) => {
      el.className = 'err';
      el.textContent = `cannot reach the API — ${err.message}`;
    });
  }
}

boot();
