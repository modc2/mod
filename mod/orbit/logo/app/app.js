// The logo console.
//
// Reads need nothing. A write needs one wallet signature — a mod-protocol
// token, `base64url({data, time, key, signature})` where the signature is an
// EIP-191 personal_sign over exactly `JSON.stringify({data, time})`. The API
// verifies it with `m.mod('auth')` and checks the recovered address against
// the TARGET module's owner, so this page holds no authority of its own: it
// only carries the owner's signature to the module that checks it.

const API = '_api';
const TOKEN_KEY = 'logo_mod_token';

const $ = (id) => document.getElementById(id);
const state = { address: null, token: null, module: '', owner: null, limits: null };

// -- protocol token ----------------------------------------------------

const b64url = (s) =>
  btoa(unescape(encodeURIComponent(s)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

function cached() {
  try {
    const raw = JSON.parse(localStorage.getItem(TOKEN_KEY) || 'null');
    // Re-mint a day before the API's 7-day ceiling rather than letting a save
    // fail on an expired token the user cannot see.
    if (raw && raw.token && Date.now() - raw.at < 6 * 86400e3) return raw;
  } catch {}
  return null;
}

async function mint() {
  const eth = window.ethereum;
  if (!eth) throw new Error('no browser wallet found — install MetaMask, or use `m logo/glyph <module> X` on the host');
  const [address] = await eth.request({ method: 'eth_requestAccounts' });
  const payload = { data: { scope: 'logo' }, time: Math.floor(Date.now() / 1000) };
  const signature = await eth.request({
    method: 'personal_sign',
    params: [JSON.stringify(payload), address],
  });
  const token = b64url(JSON.stringify({ ...payload, key: address.toLowerCase(), signature }));
  try {
    localStorage.setItem(TOKEN_KEY, JSON.stringify({ token, address: address.toLowerCase(), at: Date.now() }));
  } catch {}
  return { token, address: address.toLowerCase() };
}

async function connect() {
  say('');
  try {
    const got = await mint();
    state.token = got.token;
    state.address = got.address;
    paintWho();
    refreshOwner();
  } catch (e) {
    say(e.message || String(e), 'err');
  }
}

// -- api ---------------------------------------------------------------

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['content-type'] = 'application/json';
  if (state.token) headers['authorization'] = `Bearer ${state.token}`;
  const r = await fetch(`${API}${path}`, { ...options, headers, cache: 'no-store' });
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text.slice(0, 200) }; }
  if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
  return data;
}

// -- painting ----------------------------------------------------------

const CUBE = `<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
  <path d="M12 2 21 7v10l-9 5-9-5V7z" fill="none" stroke="currentColor" stroke-width="1.4"/>
  <path d="M3 7l9 5 9-5M12 12v10" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>`;

function markHtml(logo) {
  if (!logo) return CUBE;
  if (logo.kind === 'glyph') return escapeHtml(logo.glyph);
  if (logo.kind === 'url' || logo.kind === 'image') {
    return `<img src="${escapeAttr(logo.src)}" alt="" onerror="this.replaceWith(document.createTextNode(''))" />`;
  }
  return CUBE;
}

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const escapeAttr = escapeHtml;

function paintWho() {
  const el = $('addr');
  el.textContent = state.address ? state.address.slice(0, 6) + '…' + state.address.slice(-4) : 'not signed in';
  el.classList.toggle('on', !!state.address);
  el.title = state.address || '';
  $('connect').textContent = state.address ? 're-sign' : 'connect wallet';
}

function say(text, kind) {
  const el = $('msg');
  el.textContent = text || '';
  el.className = 'msg' + (kind ? ' ' + kind : '');
}

// -- one module --------------------------------------------------------

let ownerTimer = null;

function onModuleTyped() {
  clearTimeout(ownerTimer);
  ownerTimer = setTimeout(refreshOwner, 250);
}

async function refreshOwner() {
  const name = $('module').value.trim();
  state.module = name;
  state.owner = null;
  if (!name) {
    $('ownerLine').innerHTML = '';
    paintMark(null, 'the protocol cube — nothing set');
    return;
  }
  try {
    const [who, mark] = await Promise.all([
      api(`/logo/${name}/owner`),
      api(`/logo/${name}`),
    ]);
    state.owner = who;
    const mine = state.address && (who.addresses || []).includes(state.address);
    $('ownerLine').innerHTML =
      `<b>${escapeHtml(who.module)}</b> — owner ` +
      (who.addresses.length
        ? `<span class="${mine ? 'yes' : 'no'}">${escapeHtml(who.addresses[0])}</span>` +
          ` <span>(${escapeHtml(who.source)})</span>` +
          (mine ? ' <span class="yes">· that is you</span>'
                : state.address ? ' <span class="no">· not you — this will refuse</span>' : '')
        : `<span class="no">${escapeHtml(who.source)}</span>`);
    paintMark(mark.logo, describe(mark.logo));
  } catch (e) {
    state.owner = null;
    $('ownerLine').innerHTML = `<span class="no">${escapeHtml(e.message)}</span>`;
    paintMark(null, '');
  }
}

function describe(logo) {
  if (!logo || logo.kind === 'cube') return 'the protocol cube — nothing set';
  const when = logo.updated ? new Date(logo.updated).toISOString().replace('T', ' ').slice(0, 16) : '';
  const by = logo.by ? ` by ${logo.by.slice(0, 6)}…${logo.by.slice(-4)}` : '';
  return `${logo.kind}${logo.glyph ? ' ' + logo.glyph : ''}${logo.src ? ' ' + logo.src : ''} — ${when}${by}`;
}

function paintMark(logo, meta) {
  $('mark').innerHTML = markHtml(logo);
  $('markMeta').textContent = meta || '';
}

// -- writes ------------------------------------------------------------

async function save(body) {
  const name = $('module').value.trim();
  if (!name) return say('name a module first', 'err');
  if (!state.token) {
    try {
      const got = await mint();
      state.token = got.token;
      state.address = got.address;
      paintWho();
    } catch (e) {
      return say(e.message || String(e), 'err');
    }
  }
  say('signing…');
  try {
    const out = await api(`/logo/${name}`, { method: 'POST', body: JSON.stringify(body) });
    paintMark(out.logo, describe(out.logo));
    say(`saved — ${out.module} now shows this mark to everyone`, 'ok');
    $('glyph').value = '';
    $('url').value = '';
    loadMarks();
    refreshOwner();
  } catch (e) {
    say(e.message || String(e), 'err');
  }
}

function onFile(file) {
  if (!file) return;
  const max = (state.limits && state.limits.max_image_bytes) || 512 * 1024;
  if (file.size > max) {
    return say(`${Math.round(file.size / 1024)}KB — the limit is ${max / 1024}KB. Host it and paste the URL instead.`, 'err');
  }
  const reader = new FileReader();
  reader.onerror = () => say('could not read that file', 'err');
  reader.onload = () => save({ dataUrl: String(reader.result || '') });
  reader.readAsDataURL(file);
}

// -- the wall ----------------------------------------------------------

async function loadMarks() {
  try {
    const out = await api('/marks');
    const box = $('marks');
    if (!out.marks.length) {
      box.textContent = 'no module has set a mark yet.';
      return;
    }
    box.innerHTML = '';
    for (const entry of out.marks) {
      const card = document.createElement('button');
      card.className = 'card';
      card.innerHTML =
        `<span class="mark">${markHtml(entry.logo)}</span>` +
        `<span><span class="mod">${escapeHtml(entry.module)}</span><br/>` +
        `<span class="who2">${escapeHtml(entry.logo.kind)}${entry.logo.by ? ' · ' + escapeHtml(entry.logo.by.slice(0, 10)) : ''}</span></span>`;
      card.onclick = () => { $('module').value = entry.module; refreshOwner(); };
      box.appendChild(card);
    }
  } catch (e) {
    $('marks').textContent = e.message;
  }
}

async function loadStatus() {
  try {
    const s = await api('/status');
    state.limits = s.limits;
    // `image/svg+xml` reads as SVG to a human; the media type is the API's
    // business, not the caption's.
    const types = s.limits.mime.map((m) => m.split('/')[1].split('+')[0].toUpperCase());
    $('limits').textContent =
      `${types.join(' · ')}, up to ${s.limits.max_image_bytes / 1024}KB · ` +
      `a glyph is 1-${s.limits.glyph_chars} characters`;
    $('status').textContent =
      `logo v${s.version} · ${s.auth.token_max_age / 86400}d tokens · ${s.auth.rule}`;
    if (s.auth.open_mode) {
      $('mode').textContent = 'open mode';
      $('mode').classList.remove('hidden');
      $('mode').title = 'LOGO_OPEN is set — every caller may repaint every module';
    }
  } catch (e) {
    $('status').textContent = `API unreachable: ${e.message}`;
  }
}

// -- wiring ------------------------------------------------------------

const saved = cached();
if (saved) { state.token = saved.token; state.address = saved.address; }
paintWho();

$('connect').onclick = connect;
$('module').oninput = onModuleTyped;
$('file').onchange = (e) => { onFile(e.target.files && e.target.files[0]); e.target.value = ''; };
for (const btn of document.querySelectorAll('.go')) {
  btn.onclick = () => {
    const act = btn.dataset.act;
    if (act === 'glyph') return save({ glyph: $('glyph').value });
    if (act === 'url') return save({ url: $('url').value });
  };
}
document.querySelector('[data-act="reset"]').onclick = () => save({ reset: true });

// Deep link: /logo/#build lands on that module.
if (location.hash.length > 1) {
  $('module').value = decodeURIComponent(location.hash.slice(1));
}

loadStatus();
loadMarks();
refreshOwner();
