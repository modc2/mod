/**
 * eth console.
 *
 * Plain ES modules, no build step, no framework: the page asks its own origin
 * for `_api` (the app server proxies it to the API), so the same file works at
 * modc2.com/eth and on :50751.
 *
 * Sign-in mints a mod **protocol token** — the `{data, time, key, signature}`
 * envelope every module in this fleet verifies. That address is what accounts
 * and deployments are filed under; it is NOT the key that signs transactions.
 * The two are deliberately separate:
 *
 *   the protocol token   says who you are to this module. Time-bounded, kept
 *                        in localStorage under a namespaced key so a reload
 *                        does not mean re-signing.
 *   an account password  opens a keystore on the server for one call, or for
 *                        an unlock window. Never stored anywhere by this page —
 *                        modc2.com is one origin shared by every module, so a
 *                        password in storage would be readable by all of them.
 */

import { initBuild } from './build.js';

const API = '_api';
const TOKEN_KEY = 'ethdesk.token';
const THEME_KEY = 'ethdesk.theme';
const NET_KEY = 'ethdesk.network';

let token = read(TOKEN_KEY);
let address = null;
let network = read(NET_KEY) || '';
let status = null;
let accounts = [];
let current = null;         // the contract open in the interact panel

const $ = (id) => document.getElementById(id);

/* ── storage that never throws ──────────────────────────────── */

function read(k) { try { return localStorage.getItem(k) || ''; } catch { return ''; } }
function write(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch { /* private mode */ } }

/* ── talking to the API ─────────────────────────────────────── */

async function call(path, { method = 'GET', json } = {}) {
  const opts = { method, headers: {} };
  if (token) opts.headers.Authorization = `Bearer ${token}`;
  if (json !== undefined) {
    opts.body = JSON.stringify(json);
    opts.headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(`${API}${path}`, opts);
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text.slice(0, 500) }; }
  if (!res.ok) throw Object.assign(new Error(data.detail || `${res.status}`), { status: res.status, data });
  return data;
}

const q = (obj) => Object.entries(obj)
  .filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

/* ── the token a wallet signs ───────────────────────────────── */

function b64url(obj) {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * The signed material is JSON.stringify({data, time}) with no spaces, which is
 * byte-for-byte what the auth mod re-serializes and verifies.
 */
async function buildToken(addr, data = { mod: 'ethdesk' }) {
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
      const got = await window.ethereum.request({ method: 'eth_requestAccounts' });
      token = await buildToken(got[0]);
      write(TOKEN_KEY, token);
      await refresh();
      toast(`signed in as ${short(got[0])}`);
      return;
    } catch (e) {
      toast(e.message || 'the wallet refused', true);
      return;
    }
  }
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

const short = (a) => (a && a.length > 14 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a || '');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ── chrome ─────────────────────────────────────────────────── */

let toastTimer;
function toast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast on${bad ? ' bad' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, bad ? 6000 : 3200);
}

function setTheme(mode) {
  document.documentElement.dataset.theme = mode;
  write(THEME_KEY, mode);
  const next = mode === 'dark' ? 'light' : 'dark';
  $('theme').title = `switch to ${next}`;
  $('theme').setAttribute('aria-label', `switch to ${next}`);
}

/* The avatar is the address itself: a rotation and three hues read off its
   own bytes. The same key is the same picture on every visit, so signing in
   with the wrong account is visible before you have read one hex digit. */
function identicon(el, addr) {
  const h = (i) => parseInt(addr.slice(2 + i * 4, 6 + i * 4), 16) % 360;
  el.style.setProperty('--a', `${h(0)}deg`);
  el.style.setProperty('--c1', `hsl(${h(1)} 78% 62%)`);
  el.style.setProperty('--c2', `hsl(${(h(2) + 120) % 360} 68% 56%)`);
  el.style.setProperty('--c3', `hsl(${(h(3) + 240) % 360} 74% 60%)`);
}

function renderIdentity() {
  const on = !!address;
  $('who').hidden = !on;
  $('signout').hidden = !on;
  $('signin').hidden = on;
  if (!on) return;
  $('who-addr').textContent = short(address);
  $('who').title = `${address} — click to copy`;
  identicon($('who-pic'), address);
}

/* The ink bar is measured rather than drawn per tab: a label can change
   length (or a font can load late) without leaving the underline behind. */
function moveInk(tab) {
  const ink = $('tab-ink');
  const on = tab || document.querySelector('#tabs .tab.on');
  if (!ink || !on) return;
  ink.style.left = `${on.offsetLeft}px`;
  ink.style.width = `${on.offsetWidth}px`;
}

function tabs() {
  document.querySelectorAll('#tabs .tab').forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll('#tabs .tab').forEach((t) => t.classList.toggle('on', t === tab));
      moveInk(tab);
      document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === tab.dataset.pane));
      if (tab.dataset.pane === 'pane-agents') loadMcp();
      if (tab.dataset.pane === 'pane-contracts') loadContracts();
    };
  });
  moveInk();
  addEventListener('resize', () => moveInk());
  // A late font swap changes every label's width; measure once more after it.
  document.fonts?.ready.then(() => moveInk());
}

/* ── overview ───────────────────────────────────────────────── */

function dl(el, pairs) {
  el.innerHTML = pairs
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('');
}

async function refresh() {
  try {
    status = await call(`/status?${q({ network })}`);
  } catch (e) {
    // A network name saved in this browser can outlive the chain it named —
    // an owner removes a custom RPC, a fork renames one. Falling back to the
    // module's default beats an empty picker with no way to pick anything.
    status = null;
    if (network) {
      network = ''; write(NET_KEY, '');
      try { status = await call('/status'); } catch { status = null; }
    }
    if (!status) {
      dl($('chain-facts'), [['api', `<span class="err">${esc(e.message)}</span>`]]);
      $('chain-dot').className = 'dot bad';
      $('pulse').hidden = true;
      renderIdentity();
      toast(e.message, true);
      return;
    }
    toast(`${e.message} — back on the default chain`, true);
  }
  address = status.address || null;
  renderIdentity();

  fillNetworks(status.networks || []);
  renderChain(status.network);
  renderSolc(status.solc);
  accounts = status.accounts || [];
  renderAccounts();
  fillAccountPickers();
  renderGas();
  renderBalances();
  renderHistory();
  bench.sync(status);
}

function fillNetworks(list) {
  const select = $('network');
  const chosen = network || status.network?.network || 'local';
  // Grouped rather than suffixed: while the list is open the two kinds of
  // chain are separated by a heading, and while it is closed the pill itself
  // is the one that says which kind you landed on.
  // The name, not the label: `local` fits the pill, "anvil / hardhat (local)"
  // does not, and the name is what the CLI and every other face take anyway.
  const opt = (n) => `<option value="${esc(n.name)}"${n.name === chosen ? ' selected' : ''}`
    + ` title="${esc(n.label || n.name)}">${esc(n.name)}</option>`;
  const test = list.filter((n) => n.testnet !== false).map(opt).join('');
  const real = list.filter((n) => n.testnet === false).map(opt).join('');
  select.innerHTML = (test ? `<optgroup label="testnets">${test}</optgroup>` : '')
    + (real ? `<optgroup label="real money">${real}</optgroup>` : '');
  network = chosen;
  select.onchange = async () => {
    network = select.value;
    write(NET_KEY, network);
    await refresh();
    if (current) openContract(current.address);
  };
}

function renderChain(net) {
  if (!net) return;
  const badge = $('net-badge');
  badge.textContent = net.testnet === false ? 'real money' : 'testnet';
  badge.className = `badge ${net.testnet === false ? 'real' : 'test'}`;
  $('chain-dot').className = `dot ${net.ok ? 'ok' : 'bad'}`;
  $('chain-dot').title = net.ok ? `${net.rpc} · block ${net.block}` : (net.error || 'unreachable');
  // Real money is a state of the whole picker, not a footnote somewhere else.
  const real = net.testnet === false;
  $('chain').dataset.kind = real ? 'real' : 'test';
  $('chain-kind').hidden = !real;
  $('chain').title = `${net.label || net.network} · ${real ? 'real money' : 'testnet'}`
    + (net.ok ? ` · block ${net.block}` : ' · unreachable');
  dl($('chain-facts'), [
    ['network', esc(net.label || net.network)],
    ['chain id', net.chain_id ?? '—'],
    ['rpc', `${esc(net.rpc)} <span class="muted">(${esc(net.rpc_source || '')})</span>`],
    ['block', net.ok ? net.block : `<span class="err">${esc(net.error || 'unreachable')}</span>`],
    ['currency', esc(net.currency || 'ETH')],
    ['explorer', net.explorer ? `<a href="${esc(net.explorer)}" target="_blank" rel="noreferrer">${esc(net.explorer)}</a>` : '—'],
  ]);
}

function renderSolc(solc) {
  if (!solc) return;
  dl($('solc-facts'), [
    ['installed', esc((solc.installed || []).join(', ') || 'none yet')],
    ['default', esc(solc.default || '—')],
    ['fetches', solc.download ? 'yes, when a pragma needs one' : 'no (ETH_SOLC_DOWNLOAD=0)'],
    ['cache', esc(solc.cache)],
  ]);
}

let lastBlock = null;
function beat(gas) {
  const el = $('p-block');
  const el2 = $('p-gas');
  $('pulse').hidden = false;
  el.textContent = `#${gas.block}`;
  const fee = gas.base_fee_gwei ?? gas.gas_price_gwei;
  el2.textContent = fee === undefined ? '' : `${fee} gwei`;
  if (lastBlock !== null && gas.block !== lastBlock) {
    el.classList.add('tick');
    setTimeout(() => el.classList.remove('tick'), 700);
  }
  lastBlock = gas.block;
}

async function renderGas() {
  try {
    const gas = await call(`/gas?${q({ network })}`);
    beat(gas);
    dl($('gas-facts'), [
      ['model', gas.eip1559 ? 'EIP-1559' : 'legacy gasPrice'],
      ['base fee', gas.base_fee_gwei !== undefined ? `${gas.base_fee_gwei} gwei` : '—'],
      ['priority', gas.max_priority_fee_gwei !== undefined ? `${gas.max_priority_fee_gwei} gwei` : '—'],
      ['max fee', gas.max_fee_gwei !== undefined ? `${gas.max_fee_gwei} gwei` : `${gas.gas_price_gwei} gwei`],
      ['a transfer', gas.transfer_cost ? `${gas.transfer_cost} ${esc(gas.transfer_cost_symbol || '')}` : '—'],
      ['block', gas.block],
    ]);
  } catch (e) {
    dl($('gas-facts'), [['error', `<span class="err">${esc(e.message)}</span>`]]);
    $('pulse').hidden = true;
  }
}

async function renderBalances() {
  const el = $('balances');
  if (!address) { el.innerHTML = '<p class="hint">Sign in to see your accounts.</p>'; return; }
  if (!accounts.length) { el.innerHTML = '<p class="hint">No accounts yet — make one on the accounts tab.</p>'; return; }
  el.innerHTML = accounts.map((a) =>
    `<div class="item"><b>${esc(a.name)}</b><span class="grow muted">${esc(short(a.address))}</span>` +
    `<span class="amount" data-bal="${esc(a.address)}">…</span></div>`).join('');
  for (const a of accounts) {
    try {
      const got = await call(`/balance?${q({ address: a.address, network })}`);
      const span = el.querySelector(`[data-bal="${a.address}"]`);
      if (span) span.textContent = `${got.balance} ${got.symbol}`;
    } catch {
      const span = el.querySelector(`[data-bal="${a.address}"]`);
      if (span) span.textContent = '—';
    }
  }
}

async function renderHistory() {
  const el = $('history');
  if (!address) { el.innerHTML = '<p class="hint">Sign in to see what this box sent for you.</p>'; return; }
  try {
    const { txs } = await call(`/history?${q({ limit: 12 })}`);
    if (!txs.length) { el.innerHTML = '<p class="hint">Nothing sent yet.</p>'; return; }
    el.innerHTML = txs.map((t) =>
      `<div class="item ${t.status === 'success' ? 'ok' : t.status === 'reverted' ? 'bad' : ''}">` +
      `<b>${esc(t.kind)}</b><span class="muted">${esc(t.network)}</span>` +
      `<span class="grow">${esc(t.fn || '')} ${esc(short(t.hash))}</span>` +
      `<span class="muted">${esc(t.status || '')}</span></div>`).join('');
  } catch (e) {
    el.innerHTML = `<p class="hint err">${esc(e.message)}</p>`;
  }
}

/* ── accounts ───────────────────────────────────────────────── */

function renderAccounts() {
  const el = $('accounts');
  if (!address) { el.innerHTML = '<p class="hint">Sign in to manage accounts.</p>'; return; }
  if (!accounts.length) { el.innerHTML = '<p class="hint">No accounts yet. Make one below — it never leaves this box.</p>'; return; }
  el.innerHTML = accounts.map((a) => `
    <div class="item">
      <b>${esc(a.name)}</b>
      <span class="grow muted">${esc(a.address)}</span>
      <span class="muted">${a.unlocked ? 'unlocked' : 'locked'}</span>
      <button class="tiny ghost" data-unlock="${esc(a.name)}">${a.unlocked ? 'lock' : 'unlock'}</button>
      <button class="tiny ghost" data-copy="${esc(a.address)}">copy</button>
    </div>`).join('');
  el.querySelectorAll('[data-copy]').forEach((b) => {
    b.onclick = () => { navigator.clipboard?.writeText(b.dataset.copy); toast('address copied'); };
  });
  el.querySelectorAll('[data-unlock]').forEach((b) => {
    b.onclick = async () => {
      const name = b.dataset.unlock;
      const account = accounts.find((a) => a.name === name);
      try {
        if (account.unlocked) {
          await call(`/accounts/${name}/lock`, { method: 'POST' });
          toast(`${name} locked`);
        } else {
          const pw = prompt(`password for ${name} (held in memory for 5 minutes):`);
          if (!pw) return;
          await call(`/accounts/${name}/unlock`, { method: 'POST', json: { password: pw, ttl: 300 } });
          toast(`${name} unlocked for 5 minutes`);
        }
        await refresh();
      } catch (e) { toast(e.message, true); }
    };
  });
}

function fillAccountPickers() {
  const options = accounts.length
    ? accounts.map((a) => `<option value="${esc(a.name)}">${esc(a.name)} — ${esc(short(a.address))}</option>`).join('')
    : '<option value="">no accounts yet</option>';
  ['send-from', 'int-from'].forEach((id) => {
    const el = $(id);
    const chosen = el.value;
    el.innerHTML = options;
    if (chosen) el.value = chosen;
  });
}

function wireAccounts() {
  $('acct-create').onclick = async () => {
    const out = $('acct-out');
    try {
      const got = await call('/accounts', { method: 'POST', json: {
        name: $('acct-name').value.trim(),
        password: $('acct-pw').value,
        mnemonic: $('acct-mnemonic').checked,
      }});
      out.innerHTML = `<span class="ok">${esc(got.name)} → ${esc(got.address)}</span>` +
        (got.mnemonic ? `\n\nWRITE THIS DOWN — it is shown once and nowhere else:\n${esc(got.mnemonic)}` : '');
      $('acct-pw').value = '';
      await refresh();
    } catch (e) { out.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('acct-import').onclick = async () => {
    const out = $('acct-out');
    try {
      const got = await call('/accounts', { method: 'POST', json: {
        name: $('acct-name').value.trim(),
        password: $('acct-pw').value,
        secret: $('acct-secret').value.trim(),
      }});
      out.innerHTML = `<span class="ok">imported ${esc(got.name)} → ${esc(got.address)}</span>`;
      $('acct-secret').value = ''; $('acct-pw').value = '';
      await refresh();
    } catch (e) { out.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('send-go').onclick = async () => {
    const out = $('send-out');
    out.textContent = 'sending…';
    try {
      const got = await call('/send', { method: 'POST', json: {
        account: $('send-from').value,
        to: $('send-to').value.trim(),
        value: $('send-value').value.trim(),
        network,
        password: $('send-pw').value || undefined,
        confirm: $('send-confirm').checked,
      }});
      out.innerHTML = `<span class="${got.status === 'reverted' ? 'err' : 'ok'}">${esc(got.status)}</span> ` +
        `${esc(got.value)} → ${esc(short(got.to))}\n${esc(got.hash)}` +
        (got.explorer ? `\n${esc(got.explorer)}` : '');
      $('send-pw').value = '';
      renderBalances(); renderHistory();
    } catch (e) { out.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}

/* ── contracts + interact ───────────────────────────────────── */

async function loadContracts() {
  const el = $('contracts');
  if (!address) { el.innerHTML = '<p class="hint">Sign in to see what you deployed.</p>'; return; }
  try {
    const got = await call(`/contracts?${q({ network })}`);
    const rows = [
      ...got.deployed.map((r) => ({ ...r, kind: 'deployed' })),
      ...got.attached.map((r) => ({ ...r, kind: 'attached' })),
    ];
    if (!rows.length) { el.innerHTML = '<p class="hint">Nothing here yet — deploy something, or attach an ABI below.</p>'; return; }
    el.innerHTML = rows.map((r) => `
      <div class="item">
        <b>${esc(r.name || 'contract')}</b>
        <span class="muted">${esc(r.network)}</span>
        <span class="grow muted">${esc(r.address)}</span>
        <span class="muted">${esc(r.kind)}</span>
        <button class="tiny ghost" data-open="${esc(r.address)}">open</button>
      </div>`).join('');
    el.querySelectorAll('[data-open]').forEach((b) => {
      b.onclick = () => openContract(b.dataset.open);
    });
  } catch (e) { el.innerHTML = `<p class="hint err">${esc(e.message)}</p>`; }
}

async function openContract(addr) {
  const card = $('interact-card');
  try {
    const iface = await call(`/contracts/${addr}?${q({ network })}`);
    current = iface;
    card.hidden = false;
    $('interact-title').textContent = `${iface.name || ''} ${iface.address} · ${iface.network}`;
    $('reads').innerHTML = iface.reads.map((fn) => fnForm(fn, 'read')).join('') ||
      '<p class="hint">no view functions</p>';
    $('writes').innerHTML = iface.writes.map((fn) => fnForm(fn, 'write')).join('') ||
      '<p class="hint">no state-changing functions</p>';
    wireFunctions(iface.address);
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) { toast(e.message, true); }
}

function fnForm(fn, kind) {
  const args = (fn.inputs || []).map((input, i) =>
    `<input data-arg="${esc(fn.name)}:${i}" placeholder="${esc(input.name || `arg ${i}`)} — ${esc(input.type)}">`).join('');
  const payable = fn.mutability === 'payable'
    ? `<input data-value="${esc(fn.name)}" placeholder="value to send (payable)">` : '';
  return `<div class="fn">
      <div class="fn-name"><span>${esc(fn.name)}</span>
        <span class="sig">${esc(fn.signature)}</span></div>
      <div class="args">${args}${payable}</div>
      <div class="row"><button class="tiny ${kind === 'read' ? 'ghost' : 'primary'}"
        data-run="${esc(fn.name)}" data-kind="${kind}">${kind === 'read' ? 'call' : 'send'}</button></div>
      <div class="result" data-result="${esc(fn.name)}"></div>
    </div>`;
}

function wireFunctions(addr) {
  document.querySelectorAll('[data-run]').forEach((button) => {
    button.onclick = async () => {
      const name = button.dataset.run;
      const kind = button.dataset.kind;
      const result = document.querySelector(`[data-result="${name}"]`);
      const args = [...document.querySelectorAll(`[data-arg^="${name}:"]`)].map((i) => parseArg(i.value));
      const valueField = document.querySelector(`[data-value="${name}"]`);
      result.className = 'result';
      result.textContent = kind === 'read' ? 'calling…' : 'sending…';
      try {
        if (kind === 'read') {
          const got = await call(`/contracts/${addr}/read`, { method: 'POST',
            json: { function: name, args, network } });
          result.textContent = JSON.stringify(got.result);
        } else {
          const got = await call(`/contracts/${addr}/write`, { method: 'POST', json: {
            account: $('int-from').value, function: name, args, network,
            value: valueField ? valueField.value.trim() || 0 : 0,
            password: $('int-pw').value || undefined,
            confirm: $('int-confirm').checked,
          }});
          result.className = `result${got.status === 'reverted' ? ' err' : ''}`;
          result.textContent = `${got.status} · gas ${got.gas_used ?? '?'} · ${got.hash}`;
          renderHistory();
        }
      } catch (e) {
        result.className = 'result err';
        result.textContent = e.message;
      }
    };
  });
}

function wireContracts() {
  $('refresh-contracts').onclick = loadContracts;
  $('attach').onclick = async () => {
    const out = $('attach-out');
    try {
      const got = await call('/contracts', { method: 'POST', json: {
        address: $('attach-address').value.trim(),
        abi: JSON.parse($('attach-abi').value),
        name: $('attach-name').value.trim() || undefined,
        network,
      }});
      out.innerHTML = `<span class="ok">attached to ${esc(got.address)}</span>`;
      loadContracts();
    } catch (e) { out.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
}

/* ── explorer ───────────────────────────────────────────────── */

function wireExplorer() {
  const run = async () => {
    const out = $('lookup-out');
    const value = $('lookup').value.trim();
    if (!value) return;
    out.textContent = 'looking…';
    try {
      let got;
      if (/^0x[0-9a-fA-F]{64}$/.test(value)) {
        got = await call(`/tx?${q({ hash: value, network })}`);
      } else if (/^\d+$/.test(value) || ['latest', 'pending', 'finalized'].includes(value)) {
        got = await call(`/block?${q({ number: value, network })}`);
      } else {
        // An address is either an account or a contract, and which one it is
        // is the first thing worth knowing about it.
        const [balance, code] = await Promise.all([
          call(`/balance?${q({ address: value, network })}`),
          call(`/code?${q({ address: value, network })}`).catch(() => null),
        ]);
        got = { balance, code };
        if (code?.is_contract) {
          got.token = await call(`/tokens/${value}?${q({ network })}`).catch(() => undefined);
          got.interface = await call(`/contracts/${value}?${q({ network })}`).catch(() => undefined);
        }
      }
      out.textContent = JSON.stringify(got, null, 2).slice(0, 20000);
    } catch (e) { out.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $('lookup-go').onclick = run;
  $('lookup').onkeydown = (e) => { if (e.key === 'Enter') run(); };
}

/* ── agents ─────────────────────────────────────────────────── */

let mcpLoaded = false;
async function loadMcp() {
  if (mcpLoaded) return;
  try {
    const doc = await call('/mcp');
    mcpLoaded = true;
    $('mcp-count').textContent = `${doc.count} tools · MCP ${doc.protocol.default}`;
    $('mcp-config').textContent = JSON.stringify(doc.config.http, null, 2)
      + `\n\n# stdio\n${doc.transports.stdio.command}`
      + `\n\n# claude code\n${doc.config.claude_cli}`;
    $('mcp-copy').onclick = () => {
      navigator.clipboard?.writeText($('mcp-config').textContent);
      toast('client config copied');
    };
    $('mcp-tools').innerHTML = doc.tools.map((t) => `
      <details class="tool">
        <summary>${esc(t.name)}<span class="auth">${esc(t.auth)}</span></summary>
        <p>${esc(t.description)}</p>
      </details>`).join('');
  } catch (e) {
    $('mcp-tools').innerHTML = `<p class="hint err">${esc(e.message)}</p>`;
  }
}

/* ── boot ───────────────────────────────────────────────────── */

setTheme(read(THEME_KEY) || 'dark');
$('theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
$('signin').onclick = signIn;
$('signout').onclick = signOut;
$('who').onclick = () => {
  if (!address) return;
  navigator.clipboard?.writeText(address);
  toast('address copied');
};

// The bench is built before the first refresh so it exists to be handed a
// status; everything it needs from the shell is passed in rather than reached
// for, which is what keeps the two files independent.
const bench = initBuild({
  call, q, toast, esc, short, $,
  net: () => network,
  address: () => address,
  accounts: () => accounts,
  onActivity: () => { renderHistory(); renderBalances(); loadContracts(); },
});

/* Two shortcuts, because this is a code editor before it is a dashboard:
   ctrl/cmd-S saves the project, ctrl/cmd-enter compiles. Both are listened
   for on the window so they work from inside the editor textarea, and both
   only fire on the bench — cmd-S on the explorer tab should stay the
   browser's. */
addEventListener('keydown', (e) => {
  if (!(e.metaKey || e.ctrlKey)) return;
  if (!$('pane-build').classList.contains('on')) return;
  const hit = e.key === 's' ? 'pj-save' : (e.key === 'Enter' ? 'b-compile' : null);
  if (!hit) return;
  e.preventDefault();
  $(hit).click();
});

tabs();
wireAccounts();
wireContracts();
wireExplorer();
refresh();

// A shared project arrives as a link: /eth/?open=<cid>. Opening it is the
// whole point of the link, so it happens without a click and without a
// sign-in — the store serves a public object to anybody.
const openParam = new URLSearchParams(location.search).get('open');
if (openParam) bench.openCid(openParam);
// 12s rather than 30: this is what makes the block number in the bar a
// heartbeat instead of a stale figure somebody has to distrust.
setInterval(() => { if (document.visibilityState === 'visible') renderGas(); }, 12000);
