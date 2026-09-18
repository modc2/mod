/**
 * lighthouse console.
 *
 * Plain ES modules, no build step, no framework: the page asks its own origin
 * for `_api` (the app server proxies it to the API), so the same file works at
 * modc2.com/lighthouse and on :50681.
 *
 * Sign-in mints a mod **protocol token** — the `{data, time, key, signature}`
 * envelope every module in this fleet verifies. It is the same token the store
 * module wants, which is what lets this console push a CID into the store
 * without a second login and without the module signing for anybody.
 *
 * Two secrets, deliberately kept apart:
 *   the protocol token   time-bounded, scoped to signatures, kept in
 *                        localStorage under a namespaced key so a reload does
 *                        not mean re-signing.
 *   a BYOK Lighthouse key never persisted at all — it lives in a variable for
 *                        this tab. modc2.com is one origin shared by every
 *                        module, so a long-lived API key in storage would be
 *                        readable by all of them.
 */

const API = '_api';
const TOKEN_KEY = 'lighthouse.token';
const THEME_KEY = 'lighthouse.theme';

let token = read(TOKEN_KEY);
let address = null;
let byok = '';            // this tab only, on purpose
let listSource = 'module';
let state = { lh: null, store: null };

const $ = (id) => document.getElementById(id);

/* ── storage that never throws ──────────────────────────────── */

function read(k) { try { return localStorage.getItem(k) || ''; } catch { return ''; } }
function write(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* private mode */ } }

/* ── talking to the API ─────────────────────────────────────── */

function headers(extra = {}) {
  const h = { ...extra };
  if (token) h.Authorization = `Bearer ${token}`;
  if (byok) h['x-lh-key'] = byok;
  return h;
}

async function call(path, { method = 'GET', body, json, form } = {}) {
  const opts = { method, headers: headers() };
  if (json !== undefined) {
    opts.body = JSON.stringify(json);
    opts.headers['Content-Type'] = 'application/json';
  } else if (form) {
    opts.body = form;                      // browser sets the multipart boundary
  } else if (body !== undefined) {
    opts.body = body;
  }
  const res = await fetch(`${API}${path}`, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text.slice(0, 400) }; }
  if (!res.ok) throw Object.assign(new Error(data.detail || `${res.status}`), { status: res.status, data });
  return data;
}

/* ── the token a wallet signs ───────────────────────────────── */

function b64url(obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * The signed material is JSON.stringify({data, time}) with no spaces, which is
 * byte-for-byte what `mod core/server/auth` re-serializes and verifies
 * (sig_features = ["data","time"], separators (',',':')).
 */
async function buildToken(addr, data = { mod: 'lighthouse' }) {
  const time = (Date.now() / 1000).toString();
  const signature = await window.ethereum.request({
    method: 'personal_sign',
    params: [JSON.stringify({ data, time }), addr],
  });
  return b64url({ data, time, key: addr, signature });
}

async function signIn() {
  if (window.ethereum) {
    try {
      const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
      token = await buildToken(accounts[0]);
      write(TOKEN_KEY, token);
      await refresh();
      toast(`signed in as ${short(accounts[0])}`);
      return;
    } catch (e) {
      toast(e.message || 'the wallet refused', true);
      return;
    }
  }
  // No extension: a protocol token minted anywhere else is just as good —
  // `m.mod('auth')().token({})` on a box with the key, for instance.
  const pasted = prompt('No wallet extension found.\n\nPaste a mod-protocol token '
    + '(m.mod("auth")().token({}) on a box that holds your key):');
  if (!pasted) return;
  token = pasted.trim();
  write(TOKEN_KEY, token);
  await refresh();
}

function signOut() {
  token = ''; address = null; write(TOKEN_KEY, '');
  refresh();
  toast('signed out');
}

const short = (a) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || '');

/* ── rendering ──────────────────────────────────────────────── */

function toast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast on${bad ? ' bad' : ''}`;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.className = 'toast'; }, 4200);
}

function facts(el, rows) {
  el.innerHTML = rows.map(([k, v, cls]) =>
    `<dt>${esc(k)}</dt><dd class="${cls || ''}">${v}</dd>`).join('');
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const bytes = (n) => {
  if (!n && n !== 0) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
};

function dot(id, level) { $(id).className = `dot ${level}`; }

function renderLighthouse(s) {
  const configured = !!s.configured;
  const source = byok ? 'your key (this tab)' : (configured ? 'the deployment key' : 'none');
  dot('dot-lh', byok || configured ? 'ok' : 'warn');
  facts($('lh-facts'), [
    ['key', esc(source), byok || configured ? 'ok' : 'bad'],
    ['gateway', esc(s.gateway || '—')],
    ['indexed', `${s.objects ?? 0} object${s.objects === 1 ? '' : 's'}`],
    ['pinning', 'perpetual (IPFS + Filecoin)'],
  ]);
  $('byok-hint').style.display = byok || configured ? 'none' : '';
  document.body.classList.toggle('owner', !!(state.me && state.me.owner));
}

function renderStore(s) {
  const blockers = s.blockers || [];
  dot('dot-store', !s.reachable ? 'bad' : (s.can_push ? 'ok' : 'warn'));
  const q = s.quota || {};
  facts($('store-facts'), [
    ['api', esc(s.url), s.reachable ? 'ok' : 'bad'],
    // Why it is down belongs in the blocker line below, with the button that
    // acts on it — a fact cell is one line wide and has nowhere to put a fix.
    ['reachable', s.reachable ? 'yes' : 'no', s.reachable ? 'ok' : 'bad'],
    ['you', s.address ? esc(short(s.address)) : (token ? '—' : 'not signed in')],
    ['allowed to store', s.authenticated ? (s.authorized ? 'yes' : 'no') : '—',
      s.authorized ? 'ok' : (s.authenticated ? 'bad' : '')],
    ['terms', s.authenticated ? (s.terms_accepted ? `accepted v${esc((s.terms || {}).version || '')}` : 'not accepted') : '—',
      s.terms_accepted ? 'ok' : (s.authenticated ? 'bad' : '')],
    ['quota', q.unlimited ? 'unlimited'
      : (q.limit_bytes ? `${bytes(q.used_bytes)} of ${bytes(q.limit_bytes)}` : '—')],
  ]);
  $('store-blockers').innerHTML = blockers.map((b) => {
    // A store that is merely asleep is the common case, and asking again is
    // the whole fix: /status knocks on the activator on its way through.
    const act = /terms/.test(b) ? '<button class="ghost" data-accept="1">accept terms</button>'
      : (!s.reachable ? '<button class="ghost" data-retry="1">try again</button>' : '');
    return `<div class="blocker"><span>${esc(b)}</span>${act}</div>`;
  }).join('');
  const accept = $('store-blockers').querySelector('[data-accept]');
  if (accept) accept.onclick = acceptTerms;
  const retry = $('store-blockers').querySelector('[data-retry]');
  if (retry) {
    retry.onclick = () => {
      retry.disabled = true;
      retry.textContent = 'waking…';   // a cold start is seconds, not instant
      refresh();
    };
  }
}

async function acceptTerms() {
  try {
    await call('/store/terms/accept', { method: 'POST' });
    toast('terms accepted — your signed token is the proof');
    refresh();
  } catch (e) { toast(e.message, true); }
}

function result(el, rows, bad = false) {
  el.className = `out${bad ? ' err' : ''}`;
  el.innerHTML = rows.map(([k, v]) =>
    `<div class="line"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join('');
}

function putResult(el, r) {
  const url = r.url || '';
  const rows = [
    ['cid', `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(r.cid)}</a>`],
    ['name', esc(r.key || '—')],
    ['size', bytes(r.size)],
  ];
  if (r.source_cid) {
    rows.push(['from store', esc(r.source_cid)]);
    rows.push(['same cid', r.same_cid ? 'yes — identical content hash'
      : 'no — different chunking, the Lighthouse CID is the one registered']);
  }
  const s = r.store;
  if (s) {
    rows.push(['store', s.registered
      ? `registered${s.public ? ' (public)' : ' (private)'}`
      : `<span style="color:var(--bad)">not registered — ${esc(s.error || '')}</span>`]);
  }
  result(el, rows);
  refreshObjects();
}

/* ── objects ────────────────────────────────────────────────── */

function objectCard(o, source) {
  const url = o.url || `${(state.lh && state.lh.gateway) || 'https://gateway.lighthouse.storage'}/ipfs/${o.cid}`;
  const when = o.timestamp ? new Date(o.timestamp * 1000).toISOString().slice(0, 16).replace('T', ' ') : '';
  const pills = [];
  if (source === 'store') {
    pills.push(`<span class="pill ${o.visibility === 'public' ? 'pub' : ''}">${esc(o.visibility || 'private')}</span>`);
    pills.push(`<span class="pill">${esc(o.backend || '')}</span>`);
  } else {
    pills.push('<span class="pill">perpetual</span>');
  }
  return `<div class="obj">
    <div>
      <div class="cid">${esc(o.cid)}</div>
      <div class="meta">${esc(o.key || 'unnamed')} · ${bytes(o.size)}${when ? ` · ${esc(when)}` : ''} ${pills.join(' ')}</div>
    </div>
    <div class="acts">
      <button data-copy="${esc(o.cid)}">copy</button>
      <a href="${esc(url)}" target="_blank" rel="noreferrer"><button>open</button></a>
      ${source === 'module' ? `<button data-rm="${esc(o.cid)}">forget</button>` : ''}
      ${source === 'store' && o.backend !== 'lighthouse' ? `<button data-mirror="${esc(o.cid)}">mirror</button>` : ''}
    </div>
  </div>`;
}

async function refreshObjects() {
  const box = $('objects');
  if (!token) { box.innerHTML = '<p class="hint">sign in to see your objects.</p>'; return; }
  try {
    let objects;
    if (listSource === 'module') {
      objects = (await call('/list?limit=100')).objects || [];
    } else {
      objects = (await call('/store/objects?all_backends=1&limit=100')).objects || [];
    }
    box.innerHTML = objects.length
      ? objects.map((o) => objectCard(o, listSource)).join('')
      : `<p class="hint">${listSource === 'module'
        ? 'nothing uploaded through this module yet.'
        : 'no objects in the store for this address yet.'}</p>`;
    box.querySelectorAll('[data-copy]').forEach((b) => {
      b.onclick = () => { navigator.clipboard.writeText(b.dataset.copy); toast('CID copied'); };
    });
    box.querySelectorAll('[data-rm]').forEach((b) => {
      b.onclick = async () => {
        try {
          const r = await call(`/rm?cid=${encodeURIComponent(b.dataset.rm)}`, { method: 'DELETE' });
          toast(r.note || 'forgotten');
          refreshObjects();
        } catch (e) { toast(e.message, true); }
      };
    });
    box.querySelectorAll('[data-mirror]').forEach((b) => {
      b.onclick = () => { $('mirror-cid').value = b.dataset.mirror; $('mirror').click(); };
    });
  } catch (e) {
    box.innerHTML = `<p class="hint" style="color:var(--bad)">${esc(e.message)}</p>`;
  }
}

/* ── the mcp server, shown rather than described ─────────────── */

/**
 * The schema is rendered from `GET /mcp` itself, not from a copy kept here.
 * A tool table written by hand is a table that drifts; this one is wrong only
 * if the server is wrong, and it needs no token — a client should be able to
 * see what it would be adopting before it authenticates.
 */
function argRows(schema) {
  const props = (schema && schema.properties) || {};
  const required = new Set((schema && schema.required) || []);
  const names = Object.keys(props);
  if (!names.length) return '<div class="arg none">no arguments</div>';
  return names.map((n) => {
    const p = props[n] || {};
    const type = Array.isArray(p.type) ? p.type.join(' | ') : (p.type || 'any');
    const enums = p.enum ? ` · ${p.enum.join(' | ')}` : '';
    return `<div class="arg">
      <span class="an">${esc(n)}${required.has(n) ? '<em>*</em>' : ''}</span>
      <span class="at">${esc(type)}${esc(enums)}</span>
      <span class="ad">${esc(p.description || '')}</span>
    </div>`;
  }).join('');
}

function toolCard(t) {
  const read = t.annotations && t.annotations.readOnlyHint;
  const pills = [
    `<span class="pill ${read ? '' : 'wr'}">${read ? 'read' : 'write'}</span>`,
    `<span class="pill">${t.auth === 'none' ? 'no token' : 'token'}</span>`,
    ...(t.transports.includes('http') ? [] : ['<span class="pill loc">stdio only</span>']),
  ];
  return `<details class="tool">
    <summary>
      <code>${esc(t.name)}</code>${pills.join(' ')}
      <span class="tsum">${esc(t.description.split('. ')[0])}.</span>
    </summary>
    <p class="tdesc">${esc(t.description)}</p>
    <div class="args">${argRows(t.inputSchema)}</div>
    <details class="raw"><summary>inputSchema</summary><pre class="code">${
  esc(JSON.stringify(t.inputSchema, null, 2))}</pre></details>
  </details>`;
}

async function loadMcp() {
  const box = $('mcp-tools');
  try {
    const d = await call('/mcp');
    const url = d.transports.http.url;
    $('mcp-url').textContent = url;
    $('mcp-count').textContent = `${d.count} tools · protocol ${d.protocol.default}`;
    $('mcp-conf').textContent = JSON.stringify(d.config.http, null, 2);
    $('mcp-auth').innerHTML = `${esc(d.auth.protocol_token)} <br>`
      + `Or on stdio: <code>${esc(d.transports.stdio.command)}</code> — `
      + `${esc(d.transports.stdio.note)}.`;
    box.innerHTML = d.tools.map(toolCard).join('');
    dot('dot-mcp', 'ok');
    $('mcp-copy').onclick = () => {
      navigator.clipboard.writeText(JSON.stringify(d.config.http, null, 2));
      toast('MCP client config copied');
    };
  } catch (e) {
    dot('dot-mcp', 'bad');
    $('mcp-conf').textContent = '';
    box.innerHTML = `<p class="hint" style="color:var(--bad)">${esc(e.message)}</p>`;
  }
}

/* ── the whole page ─────────────────────────────────────────── */

async function refresh() {
  $('who').textContent = token ? 'checking…' : 'not signed in';
  try {
    const s = await call('/status');
    state.lh = s;
    state.store = s.store;
    renderLighthouse(s);
    renderStore(s.store || { url: '?', reachable: false });
  } catch (e) {
    dot('dot-lh', 'bad'); dot('dot-store', 'bad');
    facts($('lh-facts'), [['api', esc(e.message), 'bad']]);
    return;
  }
  if (token) {
    try {
      state.me = await call('/me');
      address = state.me.address;
      $('who').textContent = `${short(address)}${state.me.owner ? ' · owner' : ''}`;
      $('signin').textContent = 'sign out';
      $('signin').onclick = signOut;
      document.body.classList.toggle('owner', !!state.me.owner);
      renderLighthouse(state.lh);
    } catch (e) {
      // A stale or rejected token is a sign-in problem, not a page failure.
      $('who').textContent = 'token rejected';
      $('signin').textContent = 'sign in';
      $('signin').onclick = signIn;
      token = ''; write(TOKEN_KEY, '');
      toast(e.message, true);
    }
  } else {
    $('signin').textContent = 'sign in';
    $('signin').onclick = signIn;
    document.body.classList.remove('owner');
  }
  refreshObjects();
}

/* ── wiring ─────────────────────────────────────────────────── */

function tabs(container, attr, onPick) {
  container.querySelectorAll('.tab').forEach((tab) => {
    tab.onclick = () => {
      container.querySelectorAll('.tab').forEach((t) => t.classList.remove('on'));
      tab.classList.add('on');
      onPick(tab.dataset[attr]);
    };
  });
}

function init() {
  const saved = read(THEME_KEY);
  if (saved) document.documentElement.dataset.theme = saved;
  $('theme').textContent = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  $('theme').onclick = () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    write(THEME_KEY, next);
    $('theme').textContent = next === 'light' ? 'dark' : 'light';
  };

  $('signin').onclick = signIn;

  tabs($('put-tabs'), 'pane', (pane) => {
    document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === pane));
  });
  tabs($('list-tabs'), 'src', (src) => { listSource = src; refreshObjects(); });

  // file picking, by click or by drop
  const drop = $('drop');
  const file = $('file');
  file.onchange = () => {
    $('drop-label').textContent = file.files[0] ? `${file.files[0].name} · ${bytes(file.files[0].size)}` : 'choose a file, or drop one here';
  };
  ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
  drop.addEventListener('drop', (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer.files[0]) { file.files = ev.dataTransfer.files; file.onchange(); }
  });

  $('byok-use').onclick = () => {
    byok = $('byok').value.trim();
    $('byok').value = byok ? '••••••••' : '';
    toast(byok ? 'using your key for this tab only' : 'back to the deployment key');
    refresh();
  };

  $('modkey-save').onclick = async () => {
    const api_key = $('modkey').value.trim();
    if (!api_key) return;
    try {
      await call('/key', { method: 'POST', json: { api_key } });
      $('modkey').value = '';
      toast('deployment key saved off-chain');
      refresh();
    } catch (e) { toast(e.message, true); }
  };

  $('put').onclick = async () => {
    if (!token) return toast('sign in first', true);
    const btn = $('put');
    const out = $('put-out');
    const textPane = $('pane-text').classList.contains('on');
    const key = $('objkey').value.trim();
    const pool = $('pool').value.trim();
    const register = $('register').checked;
    const isPublic = $('public').checked;
    btn.disabled = true;
    out.className = 'out';
    out.innerHTML = '<div class="line"><span class="k">uploading</span><span class="v">to Lighthouse…</span></div>';
    try {
      let r;
      if (textPane) {
        const text = $('text').value;
        if (!text) throw new Error('nothing to store');
        r = await call('/put/text', { method: 'POST', json: { text, key: key || null, register, public: isPublic, pool: pool || null } });
      } else {
        const f = $('file').files[0];
        if (!f) throw new Error('choose a file first');
        const form = new FormData();
        form.append('file', f);
        if (key) form.append('key', key);
        if (pool) form.append('pool', pool);
        form.append('register', String(register));
        form.append('public', String(isPublic));
        r = await call('/put', { method: 'POST', form });
      }
      putResult(out, r);
      toast('stored forever');
    } catch (e) {
      result(out, [['error', esc(e.message)]], true);
    } finally { btn.disabled = false; }
  };

  $('mirror').onclick = async () => {
    if (!token) return toast('sign in first', true);
    const cid = $('mirror-cid').value.trim();
    if (!cid) return toast('paste a store CID', true);
    const out = $('mirror-out');
    $('mirror').disabled = true;
    out.className = 'out';
    out.innerHTML = '<div class="line"><span class="k">mirroring</span><span class="v">store → Lighthouse…</span></div>';
    try {
      const r = await call('/store/mirror', { method: 'POST', json: { cid, public: $('mirror-public').checked } });
      putResult(out, r);
      toast('now perpetual');
    } catch (e) {
      result(out, [['error', esc(e.message)]], true);
    } finally { $('mirror').disabled = false; }
  };

  $('refresh').onclick = refresh;
  refresh();
  // The tool schema is the same for everyone and needs no token, so it loads
  // once and does not ride along with sign-in or the objects refresh.
  loadMcp();
}

init();
