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
 *
 * Neither is required. The deployment mints its own Lighthouse key from the
 * box wallet. Uploads are paid with **x402**: the console asks `/price` for
 * what this file costs THIS caller, the browser wallet signs that USDC offer
 * (EIP-3009, no gas), and the upload goes once with `X-PAYMENT`. The server
 * verifies before the bytes move and settles after the CID comes back.
 * Bringing your own Lighthouse key makes it free; sign-in only unlocks the
 * store half.
 */

const API = '_api';
const TOKEN_KEY = 'lighthouse.token';
const THEME_KEY = 'lighthouse.theme';

let token = read(TOKEN_KEY);
let address = null;
let byok = '';            // this tab only, on purpose
let listSource = 'module';
let typeFilter = 'all';
let sortBy = 'newest';
let state = { lh: null, store: null };
let picked = [];          // the files the button will send
let showPane = () => {};  // set in init(), once the tabs are wired
let priceTimer = 0;
let urlInfo = null;       // result of /fetch/info for the current url-input value
let urlTimer = 0;
let galleryMode = read('lighthouse.gallery') === '1';

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

async function call(path, { method = 'GET', body, json, form, extra } = {}) {
  const opts = { method, headers: headers(extra) };
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

/**
 * `call` for a multipart upload, over XHR because fetch still cannot report
 * upload progress. Same error shape as `call`, so callers treat them alike.
 */
function upload(path, form, onProgress, extra) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}${path}`);
    Object.entries(headers(extra)).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data;
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : {}; } catch { data = { detail: xhr.responseText.slice(0, 400) }; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(Object.assign(new Error(data.detail || `${xhr.status}`), { status: xhr.status, data }));
    };
    xhr.onerror = () => reject(new Error('the connection dropped'));
    xhr.send(form);
  });
}

/* ── x402: what an upload costs, and the wallet paying it ──── */

const usdText = (u) => `$${Number(u).toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`;

/** `/price` for this caller: {due, why, usd, x402: <the 402 body>}. */
const priceOf = (size) => call(`/price?size=${Math.max(0, Math.floor(size))}`);

/**
 * Sign one of the 402's `accepts` with the browser wallet: an EIP-3009
 * transferWithAuthorization for exactly the price, on the chain the wallet is
 * on when it is offered, else the first offer (the wallet is asked to switch).
 * Returns the base64 X-PAYMENT header. Nothing moves until the server settles.
 */
async function payHeader(req) {
  if (!window.ethereum) throw new Error(`this upload costs ${usdText(req.price.usd)} USDC — open the page in a browser with an EVM wallet (MetaMask, Rabby, Coinbase) to pay, or use your own Lighthouse key below`);
  const [from] = await window.ethereum.request({ method: 'eth_requestAccounts' });
  const nets = Object.fromEntries(((state.lh && state.lh.pay && state.lh.pay.networks) || []).map((n) => [n.network, n]));
  const current = window.ethereum.chainId ? parseInt(window.ethereum.chainId, 16) : null;
  const offers = (req.accepts || []).filter((a) => nets[a.network]);
  const offer = offers.find((a) => nets[a.network].chain_id === current) || offers[0];
  if (!offer) throw new Error('no payment offer on a chain this console knows');
  const chainId = nets[offer.network].chain_id;
  if (current !== chainId) {
    await window.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: `0x${chainId.toString(16)}` }] });
  }
  const now = Math.floor(Date.now() / 1000);
  const nonce = `0x${[...crypto.getRandomValues(new Uint8Array(32))].map((b) => b.toString(16).padStart(2, '0')).join('')}`;
  const authorization = {
    from, to: offer.payTo, value: String(offer.maxAmountRequired),
    validAfter: String(now - 600), validBefore: String(now + (offer.maxTimeoutSeconds || 300)), nonce,
  };
  const typed = {
    types: {
      EIP712Domain: [{ name: 'name', type: 'string' }, { name: 'version', type: 'string' }, { name: 'chainId', type: 'uint256' }, { name: 'verifyingContract', type: 'address' }],
      TransferWithAuthorization: [{ name: 'from', type: 'address' }, { name: 'to', type: 'address' }, { name: 'value', type: 'uint256' }, { name: 'validAfter', type: 'uint256' }, { name: 'validBefore', type: 'uint256' }, { name: 'nonce', type: 'bytes32' }],
    },
    primaryType: 'TransferWithAuthorization',
    domain: { name: offer.extra.name, version: offer.extra.version, chainId, verifyingContract: offer.asset },
    message: authorization,
  };
  const signature = await window.ethereum.request({ method: 'eth_signTypedData_v4', params: [from, JSON.stringify(typed)] });
  return btoa(JSON.stringify({ x402Version: 1, scheme: offer.scheme, network: offer.network, payload: { signature, authorization } }));
}

/** Headers that pay for `size` bytes, or {} when this caller uploads free. */
async function payFor(size, label) {
  const q = await priceOf(size);
  if (!q.due) return {};
  progress(0, `approve ${usdText(q.usd)} USDC in your wallet${label ? ` · ${label}` : ''}`);
  return { 'X-PAYMENT': await payHeader(q.x402) };
}

/** The price line under the button, for whatever is about to be sent. */
async function showPrice() {
  const el = $('put-price');
  if (!el) return;
  const textPane = $('pane-text').classList.contains('on');
  const urlPane = $('pane-url').classList.contains('on');
  const sizes = textPane ? [new TextEncoder().encode($('text').value).length]
    : urlPane ? (urlInfo ? [urlInfo.size] : [])
    : picked.map((f) => f.size);
  const pay = state.lh && state.lh.pay;
  if (!pay || !pay.enabled) { el.innerHTML = 'free on this node'; return; }
  if (!sizes.length || (textPane && !sizes[0])) {
    el.innerHTML = `x402 · ${usdText(pay.price_usd)} + ${usdText(pay.price_per_mib_usd)}/MiB USDC on ${esc(pay.networks.map((n) => n.network).join(', '))} · no account`;
    return;
  }
  try {
    const qs = await Promise.all(sizes.map(priceOf));
    if (!qs[0].due) { el.innerHTML = `free for you (${esc(qs[0].why)})`; return; }
    const total = qs.reduce((n, q) => n + q.usd, 0);
    el.innerHTML = `<b>${usdText(total)} USDC</b> · x402, your wallet signs${qs.length > 1 ? ` ${qs.length} payments` : ''} · charged only if it lands`;
  } catch { el.textContent = ''; }
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
  openSheet();
}

function openSheet() {
  $('sheet').hidden = false;
  $('sheet-token').value = '';
  $('sheet-token').focus();
}

function closeSheet() { $('sheet').hidden = true; }

function signOut() {
  token = ''; address = null; write(TOKEN_KEY, '');
  refresh();
  toast('signed out');
}

const short = (a) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || '');

function relTime(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

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

/** An 8-bit meter: `used` of `limit`, in blocks. Hot past 90%. */
function meter(used, limit) {
  if (!limit) return '';
  const pct = Math.min(100, Math.round((Number(used) / Number(limit)) * 100));
  return `<span class="meter${pct >= 90 ? ' hot' : ''}" title="${pct}%"><i style="width:${pct}%"></i></span>`;
}

function keyWords(s) {
  if (byok) return ['your key (this tab)', 'ok'];
  if (!s.configured) {
    const why = s.auto_key && s.auto_key.error;
    if (s.auto_key && !s.auto_key.enabled) return ['none — auto-enrolment is off', 'bad'];
    return [why ? `none — minting failed: ${why}` : 'none yet — minted on first upload', why ? 'bad' : 'warn'];
  }
  switch (s.key_source) {
    case 'wallet': return [`minted by this box${s.key_address ? ` · ${short(s.key_address)}` : ''}`, 'ok'];
    case 'env': return ['LIGHTHOUSE_API_KEY (env)', 'ok'];
    case 'pasted': return ['the deployment key (pasted by the owner)', 'ok'];
    default: return ['the deployment key', 'ok'];
  }
}

function renderLighthouse(s) {
  const configured = !!s.configured;
  const [source, level] = keyWords(s);
  dot('dot-lh', level === 'ok' ? 'ok' : (level === 'warn' ? 'warn' : 'bad'));
  const anon = s.anon || {};
  const rows = [
    ['key', esc(source), level],
    ['gateway', esc(s.gateway || '—')],
    ['indexed', `${s.objects ?? 0} object${s.objects === 1 ? '' : 's'}`],
    ['pinning', 'perpetual (IPFS + Filecoin)'],
  ];
  const pay = s.pay || {};
  rows.push(['x402', pay.enabled
    ? `${usdText(pay.price_usd)} + ${usdText(pay.price_per_mib_usd)}/MiB USDC · ${esc((pay.networks || []).map((n) => n.network).join(', '))} · to <code>${esc(short(pay.pay_to))}</code>`
    : 'off — uploads are free here', pay.enabled ? 'ok' : 'warn']);
  if (anon.enabled && !pay.enabled) {
    rows.push(['anon today', `${bytes(anon.used_today)} of ${bytes(anon.daily_bytes)} · ${bytes(anon.max_bytes)} per upload`
      + meter(anon.used_today, anon.daily_bytes)]);
  } else if (anon.owner && !pay.enabled) {
    rows.push(['anon', 'uploads without sign-in are off here', 'warn']);
  }
  facts($('lh-facts'), rows);
  // The BYOK hint is for when the box has nothing to spend; the key hint is
  // the keyless story and reads differently once a key has been minted.
  $('byok-hint').style.display = byok || configured ? 'none' : '';
  $('key-hint').style.display = byok ? 'none' : '';
  $('key-hint').textContent = configured && s.key_source === 'wallet'
    ? 'No key was pasted: this box signed a Lighthouse nonce with its own wallet and keeps the key it got back. Anyone can upload' + (s.pay && s.pay.enabled ? ', paying per upload with x402.' : '.')
    : (configured ? `A deployment key is set. Anyone can upload${s.pay && s.pay.enabled ? ' (x402, per upload)' : ''}; sign in to register in the store.`
      : 'No key to paste: on first upload this box signs a Lighthouse nonce with its own wallet and keeps the key it gets back.');
  document.body.classList.toggle('owner', !!(state.me && state.me.owner));
  const hint = $('put-hint');
  hint.innerHTML = token
    ? 'Signed in — the CID is registered in the store under your address.'
    : (s.pay && s.pay.enabled
      ? 'No account needed — pay per upload with your wallet (x402). Sign in to also register it in the store.'
      : `Not signed in — the upload still lands (recorded as <code>anon</code>${anon.enabled ? `, ${esc(bytes(anon.remaining_today))} left today` : ''}); sign in to register it in the store.`);
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
      : (q.limit_bytes ? `${bytes(q.used_bytes)} of ${bytes(q.limit_bytes)}${meter(q.used_bytes, q.limit_bytes)}` : '—')],
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

/** Copy, and say so on the button that did it — a toast is easy to miss. */
function copyTo(btn, text, label = 'copied') {
  navigator.clipboard.writeText(text).then(() => {
    const was = btn.textContent;
    btn.textContent = label;
    btn.classList.add('done');
    setTimeout(() => { btn.textContent = was; btn.classList.remove('done'); }, 1400);
  }, () => toast('the browser refused the clipboard', true));
}

function mountPeekImg(panel, src) {
  panel.innerHTML = '';
  const img = document.createElement('img');
  img.src = src;
  img.alt = '';
  img.style.cssText = 'width:100%;max-height:320px;object-fit:contain;margin-top:4px;cursor:zoom-in';
  img.onclick = () => {
    const lb = document.createElement('div');
    lb.className = 'lh-lightbox';
    const full = document.createElement('img');
    full.src = src;
    full.alt = '';
    full.style.cssText = 'max-width:95vw;max-height:95vh;object-fit:contain;border-radius:8px';
    lb.appendChild(full);
    const close = () => { lb.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    lb.onclick = close;
    document.addEventListener('keydown', onKey);
    document.body.appendChild(lb);
  };
  panel.appendChild(img);
}

/** Render any CID into `panel`. Uses extension first for media, then /preview. */
async function renderPeek(panel, cid, gwUrl, name) {
  const ext = name ? name.slice(name.lastIndexOf('.') + 1) : '';
  if (AUDIO.test(ext)) {
    panel.innerHTML = `<audio controls src="${esc(gwUrl)}" style="width:100%;margin-top:4px"></audio>`;
    return;
  }
  if (VIDEO.test(ext)) {
    panel.innerHTML = `<video controls src="${esc(gwUrl)}" style="width:100%;max-height:200px;margin-top:4px"></video>`;
    return;
  }
  if (IMAGE.test(ext)) {
    mountPeekImg(panel, esc(gwUrl));
    return;
  }
  try {
    const r = await call('/preview?cid=' + encodeURIComponent(cid));
    if (r.is_text && (r.content_type || '').startsWith('text/html')) {
      panel.innerHTML = `<iframe src="${esc(gwUrl)}" sandbox="allow-same-origin allow-forms" style="width:100%;height:320px;border:none;border-radius:4px" loading="lazy"></iframe>`;
    } else if (r.is_text) {
      panel.innerHTML = `<pre class="code">${esc(r.text)}</pre>${r.truncated ? '<p class="hint">truncated at 64 KB</p>' : ''}`;
    } else if ((r.content_type || '').startsWith('audio/')) {
      panel.innerHTML = `<audio controls src="${esc(r.gateway)}" style="width:100%;margin-top:4px"></audio>`;
    } else if ((r.content_type || '').startsWith('video/')) {
      panel.innerHTML = `<video controls src="${esc(r.gateway)}" style="width:100%;max-height:200px;margin-top:4px"></video>`;
    } else if ((r.content_type || '').startsWith('image/')) {
      mountPeekImg(panel, esc(r.gateway));
    } else {
      panel.innerHTML = `<p class="hint">${esc(r.content_type || 'binary')} · ${bytes(r.bytes ?? 0)}</p>`;
    }
  } catch (e) {
    panel.innerHTML = `<p class="hint" style="color:var(--bad)">${esc(e.message)}</p>`;
  }
}

function embedSnippet(url, name) {
  const ext = name ? name.slice(name.lastIndexOf('.') + 1) : '';
  if (IMAGE.test(ext)) return `<img src="${url}" alt="${name}">\n\n![${name}](${url})`;
  if (AUDIO.test(ext)) return `<audio controls src="${url}"></audio>`;
  if (VIDEO.test(ext)) return `<video controls src="${url}" playsinline></video>`;
  return `[${name}](${url})`;
}

/** One upload, kept: the link is the headline, everything else folds away. */
function keptCard(r) {
  const url = r.url || gatewayUrl(r.cid);
  const rows = [
    ['cid', `<code>${esc(r.cid)}</code>`],
    ['name', esc(r.key || '—')],
    ['size', bytes(r.size)],
  ];
  if (r.source_url) {
    rows.push(['source', `<a href="${esc(r.source_url)}" target="_blank" rel="noreferrer">${esc(r.source_url.replace(/^https?:\/\//, ''))}</a>`]);
  }
  if (r.source_cid) {
    rows.push(['from store', esc(r.source_cid)]);
    rows.push(['same cid', r.same_cid ? 'yes — identical content hash'
      : 'no — different chunking, the Lighthouse CID is the one registered']);
  }
  const p = r.payment;
  if (p && p.mode === 'x402') {
    rows.push(['paid', p.settled
      ? `${usdText(p.usd)} USDC on ${esc(p.network)}${p.tx ? ` · tx <code>${esc(p.tx.slice(0, 12))}…</code>` : ''}`
      : `<span style="color:var(--warn)">not settled — ${esc(p.error || 'the facilitator did not confirm')}</span>`]);
  } else if (p) {
    rows.push(['paid', `free${p.why ? ` (${esc(p.why)})` : ''}`]);
  }
  const s = r.store;
  if (s) {
    rows.push(['store', s.registered
      ? `registered${s.public ? ' (public)' : ' (private)'}`
      : `<span style="color:${s.status === 401 ? 'var(--warn)' : 'var(--bad)'}">not registered — ${esc(s.error || '')}</span>`]);
  }
  return `<div class="kept">
    <div class="kept-head"><span class="dot ok"></span><b>kept forever</b><span class="kept-name">${esc(r.key || '')}</span></div>
    <a class="kept-link" href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a>
    <div class="kept-acts">
      <button class="primary" data-copy-link="${esc(url)}">copy link</button>
      <button data-copy-cid="${esc(r.cid)}">copy CID</button>
      <a href="${esc(url)}" target="_blank" rel="noreferrer"><button>open</button></a>
      <button data-peek-kept="${esc(r.cid)}" data-peek-url="${esc(url)}" data-peek-name="${esc(r.key || '')}">peek</button>
      <button data-embed-kept="${esc(r.cid)}" data-embed-url="${esc(url)}" data-embed-name="${esc(r.key || '')}">embed</button>
      <button data-share-cid="${esc(r.cid)}">share</button>
    </div>
    <div class="kept-peek" style="display:none"></div>
    <div class="kept-embed" style="display:none"></div>
    <details class="kept-more"><summary>details</summary>${rows.map(([k, v]) =>
    `<div class="line"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join('')}</details>
  </div>`;
}

function putResult(el, results) {
  const list = Array.isArray(results) ? results : [results];
  el.className = 'out ok';
  el.innerHTML = list.map(keptCard).join('');
  el.querySelectorAll('[data-copy-link]').forEach((b) => { b.onclick = () => copyTo(b, b.dataset.copyLink, 'link copied'); });
  el.querySelectorAll('[data-copy-cid]').forEach((b) => { b.onclick = () => copyTo(b, b.dataset.copyCid); });
  el.querySelectorAll('[data-peek-kept]').forEach((b) => {
    b.onclick = async () => {
      const panel = b.closest('.kept').querySelector('.kept-peek');
      if (panel.style.display !== 'none') { panel.style.display = 'none'; b.textContent = 'peek'; return; }
      const cid = b.dataset.peekKept;
      b.textContent = '…';
      await renderPeek(panel, cid, b.dataset.peekUrl || gatewayUrl(cid), b.dataset.peekName || '');
      panel.style.display = '';
      b.textContent = 'peek';
    };
  });
  el.querySelectorAll('[data-peek-kept]').forEach((b) => {
    const name = b.dataset.peekName || '';
    const ext = getExt({ key: name });
    if (!IMAGE.test(ext) && !AUDIO.test(ext) && !VIDEO.test(ext)) return;
    const panel = b.closest('.kept').querySelector('.kept-peek');
    const cid = b.dataset.peekKept;
    const url = b.dataset.peekUrl || gatewayUrl(cid);
    renderPeek(panel, cid, url, name).then(() => {
      panel.style.display = '';
      b.textContent = 'hide';
    });
  });
  el.querySelectorAll('[data-embed-kept]').forEach((b) => {
    b.onclick = () => {
      const panel = b.closest('.kept').querySelector('.kept-embed');
      if (panel.style.display !== 'none') { panel.style.display = 'none'; b.textContent = 'embed'; return; }
      const snippet = embedSnippet(b.dataset.embedUrl || gatewayUrl(b.dataset.embedKept), b.dataset.embedName || '');
      panel.innerHTML = `<pre class="code">${esc(snippet)}</pre><button class="emb-cp">copy</button>`;
      panel.querySelector('.emb-cp').onclick = (ev) => copyTo(ev.currentTarget, snippet, 'copied');
      panel.style.display = '';
    };
  });
  el.querySelectorAll('[data-share-cid]').forEach((b) => {
    b.onclick = () => copyTo(b, `${location.origin}${location.pathname}?cid=${encodeURIComponent(b.dataset.shareCid)}`, 'link copied');
  });
  // the beam passes over the page: something was just kept forever
  document.body.classList.remove('lit-up');
  void document.body.offsetWidth;
  document.body.classList.add('lit-up');
  refresh();   // the shelf, and the anon budget the upload just spent
}

/* ── objects ────────────────────────────────────────────────── */

const gatewayUrl = (cid) => `${(state.lh && state.lh.gateway) || 'https://gateway.lighthouse.storage'}/ipfs/${cid}`;
const IMAGE = /^(png|jpe?g|gif|webp|avif|svg)$/i;
const AUDIO = /^(mp3|wav|ogg|flac|aac|m4a|opus|weba)$/i;
const VIDEO = /^(mp4|webm|mov|avi|mkv|ogv)$/i;
const TEXT  = /^(txt|md|json|csv|html?|xml|yaml|yml|toml|js|ts|py|sh|conf)$/i;
let shelf = { objects: [], source: 'module' };

function getExt(o) {
  const name = (o && o.key) || 'unnamed';
  const at = name.lastIndexOf('.');
  return at > 0 && name.length - at <= 5 ? name.slice(at + 1) : (o && o.mime || '').split('/')[1] || 'ipfs';
}

function objectCard(o, source) {
  const url = o.url || gatewayUrl(o.cid);
  const when = o.timestamp ? new Date(o.timestamp * 1000).toISOString().slice(0, 16).replace('T', ' ') : '';
  const pills = [];
  if (source === 'store') {
    pills.push(`<span class="pill ${o.visibility === 'public' ? 'pub' : ''}">${esc(o.visibility || 'private')}</span>`);
    pills.push(`<span class="pill">${esc(o.backend || '')}</span>`);
  } else {
    pills.push('<span class="pill">perpetual</span>');
  }
  const name = o.key || 'unnamed';
  const ext = getExt(o);
  // Images show themselves; the gateway is public, so the thumb costs nothing to sign.
  const face = IMAGE.test(ext)
    ? `<span class="ext thumb"><img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">${esc(ext.slice(0, 4).toUpperCase())}</span>`
    : `<span class="ext">${esc(ext.slice(0, 4).toUpperCase())}</span>`;
  return `<div class="obj">
    ${face}
    <div class="title" title="${esc(name)}">${esc(name)}</div>
    <div class="cid" title="${esc(o.cid)}">${esc(o.cid)}</div>
    <div class="meta">${bytes(o.size)}${when ? ` · ${esc(when)}` : ''} ${pills.join(' ')}${o.source_url ? ` · <a href="${esc(o.source_url)}" target="_blank" rel="noreferrer" title="archived from">${esc(o.source_url.replace(/^https?:\/\//, ''))}</a>` : ''}</div>
    <div class="acts">
      <button data-link="${esc(url)}">copy link</button>
      <button data-copy="${esc(o.cid)}">copy CID</button>
      <a href="${esc(url)}" target="_blank" rel="noreferrer"><button>open</button></a>
      <button data-peek="${esc(o.cid)}">peek</button>
      <button data-embed="${esc(o.cid)}" data-embed-url="${esc(url)}" data-embed-name="${esc(name)}">embed</button>
      <button data-share-cid="${esc(o.cid)}">share</button>
      ${source === 'module' && token ? `<button data-rm="${esc(o.cid)}">forget</button>` : ''}
      ${source === 'store' && o.backend !== 'lighthouse' ? `<button data-mirror="${esc(o.cid)}">mirror</button>` : ''}
    </div>
    <div class="obj-peek" style="display:none"></div>
    <div class="obj-embed" style="display:none"></div>
  </div>`;
}

async function refreshObjects() {
  const box = $('objects');
  if (!token && listSource === 'store') {
    box.innerHTML = '<p class="hint">the store only talks to a signer — sign in to see your store objects.</p>';
    return;
  }
  try {
    let objects;
    if (listSource === 'module') {
      objects = (await call('/list?limit=100')).objects || [];
    } else {
      objects = (await call('/store/objects?all_backends=1&limit=100')).objects || [];
    }
    shelf = { objects, source: listSource };
    renderShelf();
  } catch (e) {
    box.innerHTML = `<p class="hint" style="color:var(--bad)">${esc(e.message)}</p>`;
  }
}

/** Draw the shelf from what was last fetched, through the filter box. */
function renderShelf() {
  const box = $('objects');
  const { objects, source } = shelf;
  const q = $('shelf-q').value.trim().toLowerCase();
  const nameFiltered = q ? objects.filter((o) => `${o.key || ''} ${o.cid} ${o.source_url || ''}`.toLowerCase().includes(q)) : objects;
  const shown = typeFilter === 'all' ? nameFiltered : nameFiltered.filter((o) => {
    const ext = getExt(o);
    if (typeFilter === 'image') return IMAGE.test(ext);
    if (typeFilter === 'audio') return AUDIO.test(ext);
    if (typeFilter === 'video') return VIDEO.test(ext);
    if (typeFilter === 'text')  return TEXT.test(ext);
    if (typeFilter === 'archived') return !!o.source_url;
    return !IMAGE.test(ext) && !AUDIO.test(ext) && !VIDEO.test(ext) && !TEXT.test(ext);
  });
  const sorted = [...shown].sort((a, b) => {
    if (sortBy === 'oldest') {
      const d = (a.timestamp || 0) - (b.timestamp || 0);
      return d !== 0 ? d : (a.key || '').localeCompare(b.key || '');
    }
    if (sortBy === 'name') return (a.key || '').localeCompare(b.key || '');
    if (sortBy === 'size') {
      const d = (b.size || 0) - (a.size || 0);
      return d !== 0 ? d : (a.key || '').localeCompare(b.key || '');
    }
    // newest (default): descending timestamp, name as tiebreaker
    const d = (b.timestamp || 0) - (a.timestamp || 0);
    return d !== 0 ? d : (a.key || '').localeCompare(b.key || '');
  });
  const totalSize = sorted.reduce((n, o) => n + (o.size || 0), 0);
  const filterActive = q || typeFilter !== 'all';
  const statEl = $('shelf-count');
  if (statEl) {
    if (!sorted.length) {
      statEl.textContent = '';
    } else if (filterActive) {
      statEl.textContent = `${sorted.length} of ${objects.length} · ${bytes(totalSize)}`;
    } else {
      statEl.textContent = `${sorted.length} files · ${bytes(totalSize)}`;
    }
  }
  const lead = !token && source === 'module'
    ? '<p class=”hint”>uploads made without sign-in, by anyone — sign in to see yours.</p>' : '';
  const filterDesc = (q ? `”${esc(q)}”` : '') + (typeFilter !== 'all' ? (q ? ` · ${typeFilter}` : typeFilter) : '');
  let empty;
  if ((q || typeFilter !== 'all') && objects.length) empty = `nothing on the shelf matches${filterDesc ? ` ${filterDesc}` : ' the active filter'}.`;
  else if (source === 'module') empty = token ? 'nothing uploaded through this module yet — the first one is above.' : 'no anonymous uploads yet.';
  else empty = 'no objects in the store for this address yet.';
  box.innerHTML = lead + (sorted.length
    ? sorted.map((o) => objectCard(o, source)).join('')
    : `<p class=”hint”>${empty}</p>`);
  box.querySelectorAll('[data-copy]').forEach((b) => { b.onclick = () => copyTo(b, b.dataset.copy); });
  box.querySelectorAll('[data-link]').forEach((b) => { b.onclick = () => copyTo(b, b.dataset.link, 'link copied'); });
  box.querySelectorAll('[data-rm]').forEach((b) => {
    b.onclick = async () => {
      // two taps, no browser dialog: the first arms it, the second forgets
      if (!b.classList.contains('armed')) {
        b.classList.add('armed');
        b.textContent = 'tap again to forget';
        setTimeout(() => { b.classList.remove('armed'); b.textContent = 'forget'; }, 3000);
        return;
      }
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
  box.querySelectorAll('[data-peek]').forEach((b) => {
    b.onclick = async () => {
      const panel = b.closest('.obj').querySelector('.obj-peek');
      if (panel.style.display !== 'none') { panel.style.display = 'none'; b.textContent = 'peek'; return; }
      const cid = b.dataset.peek;
      const cardUrl = b.closest('.obj').querySelector('[data-link]')?.dataset?.link || gatewayUrl(cid);
      const cardName = b.closest('.obj').querySelector('.title')?.textContent || '';
      b.textContent = '…';
      await renderPeek(panel, cid, cardUrl, cardName);
      panel.style.display = '';
      b.textContent = 'peek';
    };
  });
  box.querySelectorAll('[data-embed]').forEach((b) => {
    b.onclick = () => {
      const panel = b.closest('.obj').querySelector('.obj-embed');
      if (panel.style.display !== 'none') { panel.style.display = 'none'; b.textContent = 'embed'; return; }
      const url = b.dataset.embedUrl || gatewayUrl(b.dataset.embed);
      const name = b.dataset.embedName || '';
      const snippet = embedSnippet(url, name);
      panel.innerHTML = `<pre class="code">${esc(snippet)}</pre><button class="emb-cp">copy</button>`;
      panel.querySelector('.emb-cp').onclick = (ev) => copyTo(ev.currentTarget, snippet, 'copied');
      panel.style.display = '';
    };
  });
  box.querySelectorAll('[data-share-cid]').forEach((b) => {
    b.onclick = () => copyTo(b, `${location.origin}${location.pathname}?cid=${encodeURIComponent(b.dataset.shareCid)}`, 'link copied');
  });
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
  const auth = { none: ['free', 'no token'], optional: ['free', 'token optional'], token: ['', 'token'], stdio: ['loc', 'stdio only'] }[t.auth] || ['', t.auth];
  const pills = [
    `<span class="pill ${read ? '' : 'wr'}">${read ? 'read' : 'write'}</span>`,
    `<span class="pill ${auth[0]}">${auth[1]}</span>`,
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
    $('mcp-count').textContent = `${d.count} tools · ${d.protocol.default}`;
    $('mcp-cli').textContent = d.config.claude_code;
    $('mcp-conf').textContent = JSON.stringify(d.config.http, null, 2);
    const names = (auth) => d.tools.filter((t) => auth.includes(t.auth)).map((t) => t.name.replace('lighthouse_', ''));
    $('mcp-auth').innerHTML = `<b>Without a token:</b> ${esc(names(['none', 'optional']).join(' · '))}. `
      + `<b>With one</b> (<code>Authorization: Bearer</code>, the store half): ${esc(names(['token']).join(' · '))}. `
      + `<b>Stdio only</b> (${esc(names(['stdio']).join(' · '))}): <code>${esc(d.transports.stdio.command)}</code> — ${esc(d.transports.stdio.note)}.`;
    box.innerHTML = d.tools.map(toolCard).join('');
    dot('dot-mcp', 'ok');
    $('mcp-copy').onclick = () => {
      navigator.clipboard.writeText(JSON.stringify(d.config.http, null, 2));
      toast('MCP client config copied');
    };
    $('mcp-cli-copy').onclick = () => {
      navigator.clipboard.writeText(d.config.claude_code);
      toast('claude mcp add … copied');
    };
  } catch (e) {
    dot('dot-mcp', 'bad');
    $('mcp-conf').textContent = '';
    $('mcp-cli').textContent = '';
    box.innerHTML = `<p class="hint" style="color:var(--bad)">${esc(e.message)}</p>`;
  }
}

/* ── x402 earnings (owner only) ─────────────────────────────── */

function renderEarnings(data) {
  const el = $('earnings-body');
  if (!el) return;
  if (!data.enabled) {
    el.innerHTML = '<p class="hint first">x402 is off — enable it with <code>POST /_api/pay</code> body <code>{"enabled":true,"pay_to":"0x…"}</code>.</p>';
    return;
  }
  const s = data.stats || {};
  const pmts = data.payments || [];
  const shown = pmts.slice(0, 20);
  const summary = `<dl>
    <dt>payments</dt><dd>${esc(String(s.payments ?? 0))}</dd>
    <dt>received</dt><dd class="ok">${usdText(s.received_usd ?? 0)}</dd>
    <dt>stored</dt><dd>${bytes(s.paid_bytes ?? 0)}</dd>
  </dl>`;
  if (!shown.length) {
    el.innerHTML = summary + '<p class="hint">no payments recorded yet.</p>';
    return;
  }
  const trows = shown.map((p) =>
    `<tr>
      <td><code>${esc(short(p.payer))}</code></td>
      <td>${esc(p.network)}</td>
      <td>${usdText(p.usd)}</td>
      <td><code>${esc(p.cid ? p.cid.slice(0, 10) + '…' : '—')}</code></td>
      <td>${esc(relTime(p.at))}</td>
    </tr>`).join('');
  el.innerHTML = summary + `
    <div class="earn-acts"><button id="earn-csv" class="ghost">export CSV</button></div>
    <div class="earn-scroll">
      <table class="earn-table">
        <thead><tr><th>payer</th><th>network</th><th>amount</th><th>CID</th><th>when</th></tr></thead>
        <tbody>${trows}</tbody>
      </table>
    </div>`;
  $('earn-csv').onclick = () => {
    const cols = ['payer', 'network', 'usd', 'cid', 'size', 'at', 'tx'];
    const lines = [cols.join(','), ...pmts.map((p) => cols.map((k) => JSON.stringify(String(p[k] ?? ''))).join(','))];
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = 'lighthouse-earnings.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  };
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
      if (state.me.owner) {
        try {
          const pay = await call('/pay');
          renderEarnings(pay);
        } catch { /* earnings panel is best-effort */ }
      }
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
    renderLighthouse(state.lh);
  }
  showPrice();
  refreshObjects();
}

/* ── the upload card ──────────────────────────────────────── */

function pick(files) {
  picked = files;
  if (files.length) showPane('pane-file');
  const size = files.reduce((n, f) => n + f.size, 0);
  $('drop-label').textContent = !files.length ? 'choose files, drop them anywhere, or paste'
    : files.length === 1 ? files[0].name : `${files.length} files`;
  $('drop-sub').textContent = files.length ? `${bytes(size)} · click to choose again` : '';
  $('drop').classList.toggle('has', !!files.length);
  // a name is for one file; with several, each keeps its own
  $('objkey').disabled = files.length > 1;
  $('objkey').placeholder = files.length > 1 ? 'each file keeps its own name' : 'name (optional)';
  if (!files.length) $('file').value = '';
  showPrice();
}

/** The bar under the button: a fraction 0..1, or null to put it away. */
function progress(frac, label = '') {
  const el = $('put-progress');
  if (frac === null) { setTimeout(() => { el.hidden = true; }, 600); return; }
  el.hidden = false;
  el.querySelector('i').style.width = `${Math.round(frac * 100)}%`;
  el.querySelector('span').textContent = frac >= 1 ? label
    : `${label ? `${label} · ` : ''}${Math.round(frac * 100)}%`;
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
    showPrice();
  });
  // the same switch, from code (a paste or a drop picks the pane for you)
  showPane = (pane) => {
    document.querySelectorAll('#put-tabs .tab').forEach((t) => t.classList.toggle('on', t.dataset.pane === pane));
    document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === pane));
  };
  tabs($('list-tabs'), 'src', (src) => { listSource = src; refreshObjects(); });
  tabs($('type-tabs'), 'type', (t) => { typeFilter = t; renderShelf(); });
  tabs($('sort-tabs'), 'sort', (s) => { sortBy = s; renderShelf(); });

  // Files arrive three ways — picked, dropped anywhere on the page, pasted —
  // and all land in `picked`, which is what the button sends.
  const drop = $('drop');
  const file = $('file');
  file.onchange = () => pick([...file.files]);
  let depth = 0;   // dragenter/leave fire per child element; count them
  const hasFiles = (ev) => [...(ev.dataTransfer?.types || [])].includes('Files');
  const hasUrl = (ev) => [...(ev.dataTransfer?.types || [])].includes('text/uri-list');
  window.addEventListener('dragenter', (ev) => {
    if (!hasFiles(ev) && !hasUrl(ev)) return;
    ev.preventDefault(); depth += 1; document.body.classList.add('dragging');
  });
  window.addEventListener('dragover', (ev) => { if (hasFiles(ev) || hasUrl(ev)) ev.preventDefault(); });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) document.body.classList.remove('dragging');
  });
  window.addEventListener('drop', (ev) => {
    if (!hasFiles(ev) && !hasUrl(ev)) return;
    ev.preventDefault(); depth = 0; document.body.classList.remove('dragging');
    if (ev.dataTransfer.files.length) {
      pick([...ev.dataTransfer.files]);
    } else if (hasUrl(ev)) {
      const url = ev.dataTransfer.getData('text/uri-list').split(/\r?\n/).find((l) => /^https?:\/\//i.test(l));
      if (url) {
        showPane('pane-url');
        $('url-input').value = url;
        $('url-input').dispatchEvent(new Event('input'));
        $('url-input').focus();
      }
    }
  });
  ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, () => drop.classList.add('over')));
  ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
  window.addEventListener('paste', (ev) => {
    const files = [...(ev.clipboardData?.files || [])];
    if (files.length) { ev.preventDefault(); pick(files); return; }
    const t = ev.target;
    if (t.closest && t.closest('input, textarea')) return;
    const text = ev.clipboardData?.getData('text');
    if (!text) return;
    ev.preventDefault();
    if (/^https?:\/\//i.test(text)) {
      showPane('pane-url');
      $('url-input').value = text;
      $('url-input').dispatchEvent(new Event('input'));
      $('url-input').focus();
    } else if (/^(Qm[1-9A-HJ-NP-Za-km-z]{44,}|baf[a-z0-9]{50,})/.test(text.trim())) {
      showPane('pane-lookup');
      $('lookup-cid').value = text.trim();
      lookupDo();
    } else {
      showPane('pane-text');
      $('text').value = text;
      $('text').oninput();
      $('text').focus();
    }
  });

  $('text').oninput = () => {
    const n = $('text').value.length;
    $('text-count').textContent = `${n.toLocaleString()} character${n === 1 ? '' : 's'}`;
    clearTimeout(priceTimer); priceTimer = setTimeout(showPrice, 400);
  };

  $('url-input').oninput = () => {
    const val = $('url-input').value.trim();
    urlInfo = null;
    $('url-preview').innerHTML = '';
    clearTimeout(urlTimer);
    if (!val) { showPrice(); return; }
    urlTimer = setTimeout(async () => {
      const preview = $('url-preview');
      preview.innerHTML = '<span class="cursor">checking…</span>';
      try {
        const info = await call(`/fetch/info?url=${encodeURIComponent(val)}`);
        urlInfo = info;
        let host = val;
        try { host = new URL(val).hostname; } catch { /* invalid URL */ }
        const title = info.title || host;
        let priceStr = '';
        const pay = state.lh && state.lh.pay;
        if (pay && pay.enabled && info.size) {
          try {
            const q = await priceOf(info.size);
            priceStr = ` · ${q.due ? usdText(q.usd) + ' USDC' : `free (${esc(q.why)})`}`;
          } catch { /* best-effort */ }
        }
        if (info.accessible) {
          preview.innerHTML = `<div class="line"><span class="k">${esc(title)}</span><span class="v">${esc(info.content_type || 'unknown')} · ${bytes(info.size)}${priceStr}</span></div>`;
          if (info.content_type && info.content_type.startsWith('image/')) {
            const img = document.createElement('img');
            img.src = val;
            img.style.cssText = 'width:100%;max-height:140px;object-fit:contain;margin-top:4px;border-radius:4px';
            img.onerror = function() { this.remove(); };
            preview.appendChild(img);
          } else if (info.og_image) {
            const img = document.createElement('img');
            img.src = info.og_image;
            img.style.cssText = 'width:100%;max-height:140px;object-fit:contain;margin-top:4px;border-radius:4px';
            img.onerror = function() { this.remove(); };
            preview.appendChild(img);
          }
        } else {
          preview.innerHTML = `<div class="line err"><span class="k">not reachable</span><span class="v">${esc(info.error || val)}</span></div>`;
        }
      } catch (e) {
        urlInfo = null;
        preview.innerHTML = `<div class="line err"><span class="k">error</span><span class="v">${esc(e.message)}</span></div>`;
      }
      showPrice();
    }, 600);
  };

  $('text').onkeydown = (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); $('put').click(); }
  };

  $('sheet-form').onsubmit = async (ev) => {
    ev.preventDefault();
    const pasted = $('sheet-token').value.trim();
    if (!pasted) return;
    token = pasted;
    write(TOKEN_KEY, token);
    closeSheet();
    await refresh();
  };
  $('sheet-cancel').onclick = closeSheet;
  $('sheet').onclick = (ev) => { if (ev.target === $('sheet')) closeSheet(); };
  window.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !$('sheet').hidden) closeSheet(); });

  $('shelf-q').oninput = renderShelf;

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
    const btn = $('put');
    const out = $('put-out');
    const textPane = $('pane-text').classList.contains('on');
    const urlPane = $('pane-url').classList.contains('on');
    const key = $('objkey').value.trim();
    const pool = $('pool').value.trim();
    const register = $('register').checked;
    const isPublic = $('public').checked;
    btn.disabled = true;
    out.className = 'out';
    out.innerHTML = '';
    if (!textPane && !urlPane) progress(0);
    try {
      const kept = [];
      if (textPane) {
        const text = $('text').value;
        if (!text.trim()) throw new Error('nothing to store — write something first');
        const extra = await payFor(new TextEncoder().encode(text).length);
        kept.push(await call('/put/text', { method: 'POST', extra, json: { text, key: key || null, register, public: isPublic, pool: pool || null } }));
        $('text').value = ''; $('text').oninput();
      } else if (urlPane) {
        const urlVal = $('url-input').value.trim();
        if (!urlVal) throw new Error('nothing to archive — paste a URL first');
        const autoKey = !key && (urlInfo?.content_type || '').startsWith('text/html') && urlInfo?.title
          ? urlInfo.title.slice(0, 200).trim()
          : null;
        progress(0.1, 'checking…');
        const extra = await payFor(urlInfo?.size ?? 0, urlVal);
        progress(0.3, 'fetching remote content…');
        kept.push(await call('/put/url', { method: 'POST', extra, json: { url: urlVal, key: key || autoKey, register, public: isPublic, pool: pool || null } }));
        $('url-input').value = '';
        urlInfo = null;
        $('url-preview').innerHTML = '';
      } else {
        if (!picked.length) throw new Error('choose a file first');
        const total = picked.reduce((n, f) => n + f.size, 0) || 1;
        let done = 0;
        // one at a time: the anon budget is per upload, and a failure part-way
        // should leave the ones already kept on screen, not hide them
        for (const [i, f] of picked.entries()) {
          const form = new FormData();
          form.append('file', f);
          // a name typed for a batch would collide; it names a single file only
          if (key && picked.length === 1) form.append('key', key);
          if (pool) form.append('pool', pool);
          form.append('register', String(register));
          form.append('public', String(isPublic));
          const label = picked.length > 1 ? `${i + 1} of ${picked.length} · ${f.name}` : f.name;
          try {
            const extra = await payFor(f.size, label);
            kept.push(await upload('/put', form, (frac) => progress((done + frac * f.size) / total, label), extra));
          } catch (e) {
            if (kept.length) putResult(out, kept);
            throw Object.assign(e, { message: `${f.name}: ${e.message}` });
          }
          done += f.size;
        }
        pick([]);
      }
      progress(1, 'pinned');
      putResult(out, kept);
      $('objkey').value = '';
      const what = kept.length > 1 ? `${kept.length} things stored forever` : 'stored forever';
      toast(token ? what : `${what} — sign in to register it in the store`);
    } catch (e) {
      const err = document.createElement('div');
      out.className = `out${out.querySelector('.kept') ? ' ok' : ' err'}`;
      if (!out.querySelector('.kept')) out.innerHTML = '';
      err.className = 'line fail';
      err.innerHTML = `<span class="k">error</span><span class="v">${esc(e.message)}</span>`;
      out.appendChild(err);
    } finally {
      btn.disabled = false;
      progress(null);
    }
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

  const lookupDo = async () => {
    const raw = $('lookup-cid').value.trim();
    if (!raw) return toast('enter a CID first', true);
    const cid = raw.replace(/^ipfs:\/\//i, '');
    const out = $('lookup-out');
    const btn = $('lookup-peek');
    btn.disabled = true;
    out.innerHTML = '';
    const gwUrl = gatewayUrl(cid);
    const panel = document.createElement('div');
    out.appendChild(panel);
    await renderPeek(panel, cid, gwUrl, cid);
    history.replaceState(null, '', '?cid=' + encodeURIComponent(cid));
    const matchedObj = shelf.objects.find((o) => o.cid === cid);
    document.title = `${matchedObj ? matchedObj.key || cid : cid} — lighthouse`;
    out.insertAdjacentHTML('beforeend',
      `<div class="line" style="margin-top:6px"><a href="${esc(gwUrl)}" target="_blank" rel="noreferrer">gateway</a> · <a href="${esc(`${API}/get?cid=${encodeURIComponent(cid)}&download=1`)}" download>download</a> · <button id="lookup-remember" style="font-size:12px;padding:2px 8px;line-height:1.5">remember</button></div>`);
    const remBtn = $('lookup-remember');
    remBtn.onclick = async () => {
      remBtn.disabled = true;
      try {
        await call('/pin', { method: 'POST', json: { cid } });
        remBtn.textContent = 'remembered';
        remBtn.classList.add('done');
        toast('remembered');
        refreshObjects();
      } catch (e) {
        toast(e.message || 'pin failed', true);
        remBtn.disabled = false;
      }
    };
    btn.disabled = false;
  };
  $('lookup-peek').onclick = lookupDo;
  $('lookup-cid').onkeydown = (ev) => { if (ev.key === 'Enter') lookupDo(); };

  $('refresh').onclick = refresh;

  const applyGallery = () => {
    $('objects').classList.toggle('gallery', galleryMode);
    $('gallery-toggle').textContent = galleryMode ? '≡' : '⊞';
  };
  applyGallery();
  $('gallery-toggle').onclick = () => {
    galleryMode = !galleryMode;
    write('lighthouse.gallery', galleryMode ? '1' : '');
    applyGallery();
  };

  refresh();
  // The tool schema is the same for everyone and needs no token, so it loads
  // once and does not ride along with sign-in or the objects refresh.
  loadMcp();

  const deepCid = new URLSearchParams(location.search).get('cid');
  if (deepCid) {
    showPane('pane-lookup');
    $('lookup-cid').value = deepCid;
    lookupDo();
  }
}

init();
