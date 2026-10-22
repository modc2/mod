// 0xprof console.
//
// One rule runs through this file: a verdict belongs to a method, and the two
// are never separated. Every list, card and panel shows which verifier said
// what — the module's whole claim is that a single "verified" badge is a thing
// you have to trust, and five labelled ones are a thing you can check.
//
// The browser verifier is the point of the wasm bundle: the tab loads snarkjs
// itself, checks the proof in front of you, and posts the result back as a
// witness. If this box were lying, that is the button that would catch it —
// which is why the answer it produces is recorded as a claim by you and never
// promoted into a verification by the server.

// Signing in is account.js: a wallet if the visitor has one, and a key this
// tab makes for itself if they do not. Both end at a personal_sign.
import { forgetLocalKey, hasWallet, importLocalKey, kind, localAddress,
         localKey, setKind, signer } from './account.js';

// The page pins <base href="/0xprof/">, so the API is one segment off its own
// origin whether that origin is a bare port or the gateway. Nothing is baked in.
const API = new URL('_api', document.baseURI).href.replace(/\/$/, '');
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const state = {
  token: localStorage.getItem('zkprof_token') || '',
  address: localStorage.getItem('zkprof_address') || '',
  systems: [], methods: [], proofs: [], sample: null, lastVerified: null, lastResult: null, openId: null,
  activeTag: '', activeAuthor: '', lastCounts: 0,
};

// ── plumbing ────────────────────────────────────────────────────────

async function api(path, { method = 'GET', body, raw } = {}) {
  const headers = {};
  if (body && !raw) headers['content-type'] = 'application/json';
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(API + path, {
    method, headers, body: raw ? body : (body ? JSON.stringify(body) : undefined),
  });
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { error: text }; }
  if (!response.ok) throw new Error(payload.detail || payload.error || response.statusText);
  return payload;
}

function toast(message, bad = false) {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast on${bad ? ' bad' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = 'toast'; }, 4200);
}

// A yes/no question in the page's own dialog — never the browser's confirm().
function ask(text, { title = 'Are you sure?', ok = 'Yes' } = {}) {
  const dlg = $('#askDlg');
  $('#askTitle').textContent = title;
  $('#askText').textContent = text;
  $('#askOk').textContent = ok;
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === 'ok');
    dlg.showModal();
  });
}

const escape = (text) => String(text ?? '').replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const short = (text, n = 10) => (text || '').slice(0, n);
const ago = (ts) => {
  const seconds = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
};
const parse = (text, fallback) => {
  const trimmed = (text || '').trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed);
};

function sniffSystem(proof, statement) {
  if (statement.protocol) return statement.protocol;
  if (proof.protocol) return proof.protocol;
  if (proof.pi_a) return 'groth16';
  if (proof.leaf && proof.path) return 'merkle';
  if (proof.A && proof.B && proof.H) return 'dleq';
  if (proof.C && proof.r) return 'pedersen';
  if (proof.commitment && proof.response) return 'schnorr';
  return null;
}

// Line icons, drawn inline — the host has no emoji font and the page loads
// nothing it does not have to.
const svg = (d, size = 14, w = 2.2) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  valid: svg('<path d="M20 6 9 17l-5-5"/>'),
  invalid: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
  error: svg('<path d="M12 8v5M12 17h.01"/>'),
  unavailable: svg('<path d="M6 12h12"/>'),
  down: svg('<path d="m6 9 6 6 6-6"/>', 13),
  sign: svg('<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>', 13),
  tab: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>', 13),
  link: svg('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>', 13),
};
const methodIcon = (status) => ICON[status] || ICON.unavailable;

// Where each verifier runs, in three characters — so a row of them reads at
// a glance as "five different places", which is the point.
const METHOD_GLYPH = { native: 'py', snarkjs: 'js', node: 'big', evm: '0x06',
                       solidity: 'sol', browser: 'you', attest: 'sig' };
const glyphFor = (method) => `<span class="mg ${escape(method)}">${escape(METHOD_GLYPH[method] || method.slice(0, 3))}</span>`;

function renderMethodPreview(system) {
  const preview = $('#vMethodPreview');
  if (!preview) return;
  const sysInfo = (state.systems || []).find((s) => s.system === system);
  if (!sysInfo) { preview.innerHTML = ''; return; }
  const sysMethods = sysInfo.available_here || sysInfo.methods || [];
  if (!sysMethods.length) { preview.innerHTML = ''; return; }
  const methodAvail = {};
  (state.methods || []).forEach((m) => { methodAvail[m.method] = m.available; });
  preview.innerHTML = sysMethods.map((m) => {
    const avail = methodAvail[m] !== false;
    return `<span class="mg ${escape(m)}${avail ? '' : ' unavail'}" title="${escape(m)}">${escape(METHOD_GLYPH[m] || m.slice(0, 3))}</span>`;
  }).join('');
}

// A dot per address, its colour derived from the address itself — the same
// person is the same colour on every card.
function avatar(address, extra = '', title = '') {
  const text = String(address || '');
  let h = 0;
  for (const c of text.toLowerCase()) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const a = h % 360, b = (a + 40 + (h >> 9) % 80) % 360;
  const label = /^0x[0-9a-f]/i.test(text) ? text.slice(2, 4) : text.slice(0, 2);
  return `<span class="av ${extra}" title="${escape(title || text)}"
    style="background:linear-gradient(135deg,hsl(${a} 70% 66%),hsl(${b} 70% 54%))">${escape(label)}</span>`;
}

// ── verdict rendering — the one component that matters ──────────────

function verdictRow(verdict) {
  const detail = verdict.detail || {};
  const words = verdict.error
    ? verdict.error
    : Object.entries(detail).filter(([k]) => k !== 'ms')
      .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join('  ');
  return `<div class="verdict ${verdict.status}" data-method="${escape(verdict.method)}" data-ms="${verdict.ms || 0}">
    ${glyphFor(verdict.method)}
    <span class="m">${escape(verdict.method)}${verdict.by ? `<span class="t mono">${escape(short(verdict.by, 12))}</span>` : ''}</span>
    <span class="s">${escape(verdict.status)}</span>
    <span class="d" title="${escape(words)}">${escape(words)}</span>
    <span class="t">${verdict.ms != null ? `${verdict.ms}ms<span class="timing-bar"></span>` : ''}</span>
  </div>`;
}

function applyTimingBars(container) {
  if (!container) return;
  const rows = $$('.verdict[data-ms]', container);
  if (!rows.length) return;
  const max = Math.max(...rows.map((r) => Number(r.dataset.ms) || 0));
  if (!max) return;
  rows.forEach((r) => {
    r.style.setProperty('--timing-w', `${Math.round((Number(r.dataset.ms) / max) * 100)}%`);
  });
}

function pendingLane(method) {
  return `<div class="verdict pending" data-method="${escape(method)}">
    ${glyphFor(method)}
    <span class="m">${escape(method)}</span>
    <span class="s"></span>
    <span class="d" title=""></span>
    <span class="t"></span>
  </div>`;
}

function summaryBlock(result) {
  const witnesses = result.witnesses || { valid: [], invalid: [] };
  const extra = witnesses.valid.length || witnesses.invalid.length
    ? `<div class="hint">witnesses — ${witnesses.valid.length} say valid, ${witnesses.invalid.length} say invalid; witnesses never change the status, they only contest it</div>`
    : '';
  return `<div class="summary ${escape(result.status)}">
    <span class="status ${result.status}">${result.status}</span>
    <b> ${escape(result.why || '')}</b>
    ${result.contested ? '<div class="hint contested">contested — somebody\'s own verifier disagrees with this box</div>' : ''}
    ${extra}
  </div>`;
}

function renderVerdicts(target, result) {
  const rows = (result.verdicts || []).map(verdictRow).join('');
  $(target).innerHTML = summaryBlock(result) + rows;
  applyTimingBars($(target));
}

// ── who checked it ──────────────────────────────────────────────────
//
// The methods do the maths; the addresses below only made them run. The two
// are shown next to each other and never merged, for the same reason the five
// verdict pips are never merged into one badge: "verified" is a claim about
// arithmetic, "0x89bc… re-ran it an hour ago" is a claim about a person, and a
// reader is entitled to weigh them separately.

const KIND_GLYPH = { published: 'PUB', republished: 'PUB', rechecked: 'RUN',
                     witnessed: 'EYE', 'refund check': 'REF' };
const glyph = (kind) => `<i>${KIND_GLYPH[kind] || '·'}</i>`;
const KIND_WORD = {
  published: 'published it — that first run is what the listing was built from',
  republished: 'published the same bytes again, which ran the methods again',
  rechecked: 'signed a re-verification: they asked, this box re-ran everything',
  witnessed: 'ran it in their own browser and reported what it said',
  'refund check': 'claimed a refund, which re-runs the methods before deciding',
};

function verifierRow(proof) {
  const people = proof.verifiers || [];
  if (!people.length) return '';
  const total = proof.verifier_count || people.length;
  const more = total - people.length;
  const contested = people.some((p) => p.contested);
  const shown = people.slice(0, 5);
  const hidden = more + people.length - shown.length;
  const dots = shown.map((person) => {
    const words = (person.kinds || []).map((k) => KIND_WORD[k] || k).join('; ');
    const signed = person.signed ? ` · ${person.signed} signed` : ' · unsigned';
    return avatar(person.address,
      `${person.signed ? 'signed' : ''}${person.contested ? ' contested' : ''}`,
      `${person.address} — ${words}${signed}, last ${ago(person.last)}`);
  }).join('');
  return `<div class="who-row">
    <span class="av-stack">${dots}${hidden > 0 ? `<span class="av more">+${hidden}</span>` : ''}</span>
    <span class="hint">checked by ${total} address${total === 1 ? '' : 'es'}</span>
    ${contested ? '<span class="warn">· one disagreed</span>' : ''}
  </div>`;
}

function checkRow(entry) {
  const ran = (entry.methods || []).join(', ');
  return `<div class="check-entry${entry.saw === 'invalid' ? ' bad' : ''}">
    <span class="g">${glyph(entry.kind)}</span>
    <span class="addr">${escape(short(entry.by, 16))}</span>
    <span class="k">${escape(entry.kind)}</span>
    <span class="d">${escape(ran || '—')} → ${escape(entry.saw || entry.status || '')}</span>
    <span class="t">${ago(entry.at)}</span>
    <span class="sig" title="${entry.signature ? `personal_sign over the message naming this proof — recover it yourself:\n${escape(entry.signature)}` : 'no signature: this run predates signed re-verification, or came from the publish path'}">${
      entry.signature ? `signed ${escape(short(entry.signature.replace(/^0x/, ''), 12))}…` : 'unsigned'}</span>
  </div>`;
}

// ── market ──────────────────────────────────────────────────────────

function proofCard(proof) {
  const methods = proof.methods || [];
  const buy = proof.price > 0
    ? `<span class="end"><span class="price">${proof.price} credits</span><button class="accent small" data-act="buy" data-id="${proof.id}">Buy</button></span>`
    : '<span class="end"><span class="free">free</span></span>';

  let verdictSection;
  if (proof.status === 'disputed') {
    const validMethods = methods.filter((m) => m.status === 'valid');
    const invalidMethods = methods.filter((m) => m.status === 'invalid');
    const chip = (m) => `<span class="mc ${escape(m.status)}" title="${escape(m.method)}: ${escape(m.status)}${m.ms != null ? ` in ${m.ms}ms` : ''}">${methodIcon(m.status)}${escape(m.method)}</span>`;
    const validChips = validMethods.length ? validMethods.map(chip).join('') : '<span class="hint">none</span>';
    const invalidChips = invalidMethods.length ? invalidMethods.map(chip).join('') : '<span class="hint">none</span>';
    verdictSection = `<div class="duel-split">
      <div class="duel-valid"><div class="duel-label">SAID VALID</div><div class="duel-chips">${validChips}</div></div>
      <div class="duel-invalid"><div class="duel-label">SAID INVALID</div><div class="duel-chips">${invalidChips}</div></div>
    </div>
    <div class="duel-cta">One of these implementations is wrong. Run it in your own tab: <button class="ghost small" data-act="browser" data-id="${proof.id}" title="run the verifier in this tab — the one check that does not trust this server">${ICON.tab}In my browser</button></div>`;
  } else {
    const chips = methods.map((m) =>
      `<span class="mc ${escape(m.status)}" title="${escape(m.method)}: ${escape(m.status)}${m.ms != null ? ` in ${m.ms}ms` : ''}">${methodIcon(m.status)}${escape(m.method)}</span>`).join('');
    verdictSection = `<div class="verdict-strip" title="${escape(proof.why || '')}">${chips || '<span class="hint">no method has run yet</span>'}</div>`;
  }

  return `<div class="card proof s-${escape(proof.status)}" data-id="${proof.id}">
    <div class="top">
      <span class="tagchip sys">${escape(proof.system)}</span>
      <span class="tagchip ${proof.zero_knowledge ? 'zk' : 'nzk'}">${proof.zero_knowledge ? 'zero-knowledge' : 'not zero-knowledge'}</span>
      <span class="status ${proof.status}">${proof.status}</span>
    </div>
    <h4>${escape(proof.title)}</h4>
    ${proof.description ? `<div class="desc">${escape(proof.description)}</div>` : ''}
    ${(proof.tags || []).length ? `<div class="tags">${proof.tags.map((t) => `<span class="tagchip" data-act="tag" data-tag="${escape(t)}">${escape(t)}</span>`).join('')}</div>` : ''}
    <div class="spacer"></div>
    ${verdictSection}
    <div class="who-line">${proof.author ? `${avatar(proof.author, '', proof.author)}<button class="ghost small mono" data-act="author" data-addr="${escape(proof.author)}">${escape(short(proof.author, 10))}</button>` : ''}${verifierRow(proof) || '<span></span>'}${buy}</div>
    <footer>
      <button class="ghost" data-act="open" data-id="${proof.id}">Details ${ICON.down}</button>
      <button class="ghost" data-act="recheck" data-id="${proof.id}" title="sign a challenge naming this proof, and every method runs again under your address">${ICON.sign}Re-verify</button>
      <button class="ghost" data-act="browser" data-id="${proof.id}" title="run the verifier in this tab — the one check that does not trust this server">${ICON.tab}In my browser</button>
      ${(!proof.price || proof.public_proof) ? `<button class="ghost" data-act="run-live" data-id="${proof.id}" title="stream the five-verifier race live on this card">Run it live</button>` : ''}
      ${proof.created ? `<span class="hint">${ago(proof.created)}</span>` : ''}
      <button class="ghost" data-act="link" data-id="${proof.id}" title="copy a link to this proof">${ICON.link}</button>
      <button class="ghost" data-act="badge" data-id="${proof.id}" title="copy badge markdown for your README">${ICON.link}Badge</button>
    </footer>
    <div class="detail" id="detail-${proof.id}"></div>
  </div>`;
}

// How many proofs and how many open bounties there are, on the tabs that hold
// them — the one number a market page should never make you go looking for.
function setCounts(status) {
  const proofs = status?.market?.proofs;
  const bounties = status?.bounties?.open;
  $('#countProofs').textContent = proofs == null ? '' : proofs;
  $('#countBounties').textContent = bounties ? bounties : '';
}

async function loadMarket() {
  const params = new URLSearchParams();
  if ($('#q').value.trim()) params.set('q', $('#q').value.trim());
  if ($('#fSystem').value) params.set('system', $('#fSystem').value);
  if ($('#fStatus').value) params.set('status', $('#fStatus').value);
  if ($('#fSale').checked) params.set('for_sale', 'true');
  if (state.activeTag) params.set('tag', state.activeTag);
  if (state.activeAuthor) params.set('author', state.activeAuthor);
  const tagEl = $('#activeTag');
  if (tagEl) tagEl.innerHTML = state.activeTag
    ? `<span class="tagchip" style="cursor:default">${escape(state.activeTag)}<span data-act="cleartag" style="margin-left:4px;cursor:pointer" title="clear tag filter">×</span></span>`
    : '';
  const authorEl = $('#activeAuthor');
  if (authorEl) authorEl.innerHTML = state.activeAuthor
    ? `<span class="tagchip" style="cursor:default">${escape(short(state.activeAuthor, 10))}<span data-act="clearauthor" style="margin-left:4px;cursor:pointer" title="clear author filter">×</span></span>`
    : '';
  if (!state.proofs.length && !$('#proofList').children.length) {
    $('#proofList').innerHTML = '<div class="skel"></div>'.repeat(3);
  }
  const { proofs } = await api(`/proofs?${params}`);
  const sortKey = $('#fSort').value;
  const sorted = [...proofs];
  if (sortKey === 'oldest')       sorted.sort((a, b) => (a.created || 0) - (b.created || 0));
  else if (sortKey === 'checked') sorted.sort((a, b) => (b.verifier_count || 0) - (a.verifier_count || 0));
  else if (sortKey === 'price-lo') sorted.sort((a, b) => (a.price || 0) - (b.price || 0));
  else if (sortKey === 'price-hi') sorted.sort((a, b) => (b.price || 0) - (a.price || 0));
  else sorted.sort((a, b) => (b.created || b.updated || 0) - (a.created || a.updated || 0));
  state.proofs = sorted;
  $('#proofList').innerHTML = sorted.length ? sorted.map(proofCard).join('')
    : '<div class="empty">no proofs yet — publish one from VERIFY, or make one in PROVE</div>';
  if (state.openId) {
    const id = state.openId;
    state.openId = null;
    openProof(id).then(() => {
      const el = document.getElementById(`detail-${id}`);
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    }).catch(() => {});
  }
  const status = await api('/status');
  setCounts(status);
  state.lastCounts = (status.market.checks ?? 0) + (status.market.proofs ?? 0);
  $('#refresh').classList.remove('has-new');
  $('#refresh').title = 'Refresh';
  const counts = status.market.by_status || {};
  // The stats read off /status, which counts the whole market — so when a
  // filter is on, say how many of that total the list below is showing.
  const filtered = proofs.length !== status.market.proofs;
  const tone = { verified: 'good', invalid: 'bad', disputed: 'warn' };
  $('#marketStats').innerHTML = [
    `<span><b>${status.market.proofs}</b>proofs${filtered ? ` · ${proofs.length} shown` : ''}</span>`,
    ...['verified', 'claimed', 'disputed', 'invalid'].filter((k) => counts[k])
      .map((k) => `<span class="${tone[k] || ''}"><b>${counts[k]}</b>${k}</span>`),
    `<span><b>${status.market.for_sale}</b>for sale</span>`,
    `<span><b>${status.market.volume}</b>credits traded</span>`,
    `<span title="by ${status.market.verifiers ?? 0} addresses"><b>${status.market.checks ?? 0}</b>checks run</span>`,
    `<span><b>${status.bounties.open}</b>open bounties</span>`,
  ].join('');
  loadVerifiers().catch(() => {});
}

async function loadVerifiers() {
  const { verifiers } = await api('/verifiers?limit=12');
  if (!verifiers.length) { $('#verifierBoard').innerHTML = ''; return; }
  $('#verifierBoard').innerHTML = `
    <summary>Who has checked things here <span class="hint">${verifiers.length} addresses</span></summary>
    <div class="body">
    <p class="hint">Nobody on this list verified anything — the methods did. What
      an address can do is put its name on a run, and the last column is the only
      one that can contradict this box: somebody's own verifier, disagreeing.</p>
    <div class="wrap"><table>
      <tr>
        <th>address</th>
        <th title="a personal_sign naming one proof, spent on one re-run of every method">signed re-runs</th>
        <th title="listings whose first run of the methods this address caused">published</th>
        <th title="ran the proof in their own tab with the snarkjs wasm bundle">own browser</th>
        <th title="their own verifier said invalid about a proof this box likes — either they are wrong or this box is">disagreed</th>
        <th>last</th>
      </tr>
      ${verifiers.map((v) => `<tr>
        <td class="addr">${avatar(v.address)}${escape(short(v.address, 20))}${v.address.toLowerCase() === (state.address || '').toLowerCase() ? ' <span class="hint">(you)</span>' : ''}</td>
        <td>${v.signed || '—'}</td>
        <td>${v.published || '—'}</td>
        <td>${v.witnessed || '—'}</td>
        <td${v.contested ? ' class="bad"' : ''}>${v.contested || '—'}</td>
        <td class="hint">${ago(v.last)}</td>
      </tr>`).join('')}
    </table></div></div>`;
}

async function openProof(id, toggle = false) {
  const box = $(`#detail-${id}`);
  const card = box.closest('.card');
  // Opened, a card takes the whole row. A verdict table and a list of who ran
  // it do not fit in a third of one, and squeezing them there is how detail
  // ends up looking optional.
  if (toggle && box.innerHTML) {
    box.innerHTML = '';
    card.classList.remove('open');
    history.replaceState(null, '', '#market');
    return;
  }
  card.classList.add('open');
  history.replaceState(null, '', '#market/' + id);
  const proof = await api(`/proofs/${id}`);
  const statementObj = { public_signals: proof.public_signals, statement: proof.statement };
  const artifacts = { ‘verification_key.json’: statementObj };
  if (!proof.locked) artifacts[‘proof.json’] = proof.proof;
  const bytes = proof.locked
    ? `<div class="hint">the proof bytes are gated at ${proof.price} credits — sha256 ${escape(short(proof.proof_hash, 24))}</div>`
    : artifactPanel(‘proof.json’, proof.proof);
  const checks = (proof.checks || []).slice().reverse();
  box.innerHTML = `
    <div class="verdicts">${(proof.verdicts || []).map(verdictRow).join(‘’)}</div>
    <h4>who made those methods run</h4>
    <div class="checklog">${checks.length ? checks.map(checkRow).join(‘’)
      : ‘<div class="hint">nothing but the publish — nobody has re-checked this yet</div>’}</div>`;
  applyTimingBars(box);
  box.insertAdjacentHTML(‘beforeend’, `
    <div class="hint">a signed row is a personal_sign over a message naming this
      proof: the address is recoverable from it without asking this box anything,
      which is the point. An unsigned row is this box’s own run at publish time.</div>
    <h4>statement (always public)</h4>
    ${artifactPanel(‘verification_key.json’, statementObj)}
    <h4>proof</h4>${bytes}
    ${proof.cid ? `<div class="hint">pinned as ${escape(proof.cid)}</div>` : ‘’}
    ${(proof.refundable_to || []).length ? `<div class="hint contested">${proof.refundable_to.length} purchase(s) can be refunded — this stopped verifying after it was sold</div>` : ‘’}`);
  box.addEventListener(‘click’, (e) => {
    const btn = e.target.closest(‘[data-dl-name],[data-cp-name]’);
    if (!btn) return;
    const val = artifacts[btn.dataset.dlName || btn.dataset.cpName];
    if (!val) return;
    if (btn.dataset.dlName) downloadAs(btn.dataset.dlName, val);
    if (btn.dataset.cpName) navigator.clipboard?.writeText(JSON.stringify(val, null, 1))
      .then(() => toast(`${btn.dataset.cpName} copied`))
      .catch(() => toast(‘copy failed’, true));
  });
  if (!proof.locked) {
    box.insertAdjacentHTML('beforeend',
      '<div class="actionbar"><button class="ghost small js-verify-fresh">Verify it fresh →</button></div>');
    box.querySelector('.js-verify-fresh').onclick = () => {
      $('#vProof').value = JSON.stringify(proof.proof, null, 1);
      $('#vKey').value = JSON.stringify(proof.statement, null, 1);
      $('#vPublic').value = JSON.stringify(proof.public_signals || [], null, 1);
      $('#vSystem').value = proof.system;
      ['vProof', 'vKey', 'vPublic'].forEach((id) => state.footFor?.($(`#${id}`)));
      showTab('verify');
      runVerify();
    };
  }
}

// ── re-verifying, which costs a signature ───────────────────────────
//
// Checking a loose proof on the VERIFY tab is free and anonymous and stays that
// way. This is the other thing: it rewrites what a listing says its verifiers
// think, in front of the people deciding whether to buy it. So it is signed,
// the signature names this proof and nothing else, and it is filed next to the
// run it paid for — where anyone can recover the address from it themselves.

async function signedRecheck(id) {
  // Whichever door this session came through signs — a run filed under an
  // address has to be signed by that address, or the roster is fiction.
  let who;
  try {
    who = await signer();
  } catch {
    toast('re-verifying is signed — sign in first (a wallet, or anonymously), '
          + `or run \`m 0xprof/recheck id=${short(id, 12)}\` from the CLI`, true);
    return;
  }
  const { address } = who;
  const { message } = await api(`/proofs/${id}/verify/challenge`,
    { method: 'POST', body: { address } });
  toast(who.kind === 'wallet' ? 'sign it — the re-run is published under your address'
                              : 'signing with the key in this browser…');
  const signature = await who.sign(message);
  toast('re-running every method…');
  const out = await api(`/proofs/${id}/verify`, { method: 'POST',
    body: { address, message, signature } });
  await loadMarket();
  if ($(`#detail-${id}`) && $(`#detail-${id}`).innerHTML) await openProof(id);
  toast(`${out.status} — ${out.why}`);
}

// ── the browser method ──────────────────────────────────────────────

let snarkjsPromise = null;
function loadSnarkjs() {
  // The UMD bundle, loaded once, from this module's own origin. It is the same
  // code the server runs — which is exactly why running it here is worth
  // something: same question, different machine, and one of them is yours.
  if (!snarkjsPromise) {
    snarkjsPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `${API}/vendor/snarkjs.min.js`;
      script.onload = () => resolve(window.snarkjs);
      script.onerror = () => reject(new Error('the snarkjs bundle is not installed here'));
      document.head.appendChild(script);
    });
  }
  return snarkjsPromise;
}

async function verifyInBrowser({ system, proof, statement, publicSignals }) {
  if (['groth16', 'plonk', 'fflonk'].includes(system)) {
    const snarkjs = await loadSnarkjs();
    const started = performance.now();
    const ok = await snarkjs[system].verify(statement, (publicSignals || []).map(String), proof);
    return { ok, detail: { where: 'this browser', implementation: 'snarkjs wasm',
                           ms: Math.round(performance.now() - started) } };
  }
  if (system === 'merkle') return verifyMerkleHere(proof, statement);
  throw new Error(`the browser verifier here does not do ${system} yet`);
}

async function verifyMerkleHere(proof, statement) {
  // Written out rather than imported: the browser check has to be independent
  // of the server to be worth running, and SubtleCrypto is in every tab.
  const hex = (buffer) => [...new Uint8Array(buffer)]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
  const bytes = (value) => {
    const text = String(value);
    if (text.startsWith('0x')) {
      const hexText = text.slice(2);
      return new Uint8Array(hexText.match(/../g).map((h) => parseInt(h, 16)));
    }
    return new TextEncoder().encode(text);
  };
  const sha = async (data) => new Uint8Array(await crypto.subtle.digest('SHA-256', data));
  const started = performance.now();
  if ((statement.hash || 'sha256') !== 'sha256') {
    throw new Error('this tab only does sha256 trees');
  }
  let node = await sha(bytes(proof.leaf));
  for (const step of proof.path || []) {
    const sibling = bytes(step.sibling);
    const left = String(step.position).toLowerCase() === 'left';
    const pair = new Uint8Array(node.length + sibling.length);
    pair.set(left ? sibling : node, 0);
    pair.set(left ? node : sibling, left ? sibling.length : node.length);
    node = await sha(pair);
  }
  const root = String(statement.root).replace(/^0x/, '');
  return { ok: hex(node) === root,
           detail: { where: 'this browser', implementation: 'SubtleCrypto',
                     computed_root: `0x${hex(node)}`, ms: Math.round(performance.now() - started) } };
}

async function browserCheck(id) {
  const proof = await api(`/proofs/${id}`);
  if (proof.locked) { toast('buy it first — the bytes are gated', true); return; }
  toast('verifying in this tab…');
  try {
    const answer = await verifyInBrowser({
      system: proof.system, proof: proof.proof, statement: proof.statement,
      publicSignals: proof.public_signals });
    await api(`/proofs/${id}/attest`, { method: 'POST',
      body: { ok: answer.ok, method: 'browser', detail: answer.detail } });
    toast(`your browser says ${answer.ok ? 'valid' : 'INVALID'} — recorded as a witness`);
    await openProof(id);
    await loadMarket();
  } catch (e) { toast(e.message, true); }
}

async function inlineRace(id) {
  const card = document.querySelector(`.card.proof[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  const existing = card.querySelector('.inline-race');
  if (existing) { existing.remove(); return; }
  const panel = document.createElement('div');
  panel.className = 'inline-race';
  card.appendChild(panel);

  let proof;
  try {
    proof = await api(`/proofs/${id}`);
  } catch (e) { toast(e.message, true); panel.remove(); return; }

  const authMethods = (state.methods || []).filter((m) => m.authoritative);
  panel.innerHTML = authMethods.length
    ? authMethods.map((m) => pendingLane(m.method)).join('')
    : '<div class="placeholder"><span class="spin"></span>asking every method…</div>';

  const body = { proof: proof.proof, statement: proof.statement,
                 public_signals: proof.public_signals, system: proof.system };
  const headers = { 'content-type': 'application/json' };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  let response;
  try {
    response = await fetch(API + '/verify/stream', { method: 'POST', headers, body: JSON.stringify(body) });
    if (!response.ok) {
      const t = await response.text();
      let p; try { p = JSON.parse(t); } catch { p = { detail: t }; }
      throw new Error(p.detail || p.error || response.statusText);
    }
  } catch (e) { toast(e.message, true); panel.remove(); return; }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop();
      for (const block of blocks) {
        const line = block.trim();
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data.startsWith('[DONE] ')) {
          const result = JSON.parse(data.slice(7));
          panel.insertAdjacentHTML('afterbegin', summaryBlock(result));
          applyTimingBars(panel);
        } else {
          const verdict = JSON.parse(data);
          if (!verdict.error) {
            const existing = panel.querySelector(`.verdict[data-method="${CSS.escape(verdict.method)}"]`);
            if (existing) existing.outerHTML = verdictRow(verdict);
            else panel.insertAdjacentHTML('beforeend', verdictRow(verdict));
          }
        }
      }
    }
  } catch (e) { toast(e.message, true); }
}

async function inlineHeroRace(sample) {
  const container = $('#heroMethods');
  const authMethods = (state.methods || []).filter((m) => m.authoritative);
  container.innerHTML = authMethods.length
    ? authMethods.map((m) => pendingLane(m.method)).join('')
    : '<div class="placeholder"><span class="spin"></span>asking every method…</div>';

  const body = { proof: sample.proof, statement: sample.statement,
                 public_signals: sample.public_signals, system: sample.system };
  const headers = { 'content-type': 'application/json' };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  let response;
  try {
    response = await fetch(API + '/verify/stream', { method: 'POST', headers, body: JSON.stringify(body) });
    if (!response.ok) {
      const t = await response.text();
      let p; try { p = JSON.parse(t); } catch { p = { detail: t }; }
      throw new Error(p.detail || p.error || response.statusText);
    }
  } catch (e) { toast(e.message, true); return; }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop();
      for (const block of blocks) {
        const line = block.trim();
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data.startsWith('[DONE] ')) {
          const result = JSON.parse(data.slice(7));
          container.insertAdjacentHTML('afterbegin', summaryBlock(result));
          applyTimingBars(container);
        } else {
          const verdict = JSON.parse(data);
          if (!verdict.error) {
            const existing = container.querySelector(`.verdict[data-method="${CSS.escape(verdict.method)}"]`);
            if (existing) existing.outerHTML = verdictRow(verdict);
            else container.insertAdjacentHTML('beforeend', verdictRow(verdict));
          }
        }
      }
    }
  } catch (e) { toast(e.message, true); }
}

// ── verify tab ──────────────────────────────────────────────────────

async function runVerify() {
  let body;
  try {
    body = {
      proof: parse($('#vProof').value),
      statement: parse($('#vKey').value, {}),
      public_signals: parse($('#vPublic').value, []),
    };
  } catch (e) { toast(`that is not JSON: ${e.message}`, true); return; }
  if ($('#vSystem').value) body.system = $('#vSystem').value;
  const authMethods = (state.methods || []).filter(m => m.authoritative);
  if (authMethods.length) {
    $('#vOut').innerHTML = authMethods.map(m => pendingLane(m.method)).join('');
  } else {
    $('#vOut').innerHTML = '<div class="placeholder"><span class="spin"></span>asking every method that can answer…</div>';
  }

  const verifyStart = performance.now();
  const timers = new Map();
  for (const m of authMethods) {
    const methodName = m.method;
    timers.set(methodName, setInterval(() => {
      const lane = $('#vOut').querySelector(`.verdict[data-method="${CSS.escape(methodName)}"]`);
      if (lane) {
        const tSpan = lane.querySelector('.t');
        if (tSpan) tSpan.textContent = Math.round(performance.now() - verifyStart) + 'ms';
      }
    }, 100));
  }

  const headers = { 'content-type': 'application/json' };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  let response;
  try {
    response = await fetch(API + '/verify/stream', {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    if (!response.ok) {
      const text = await response.text();
      let p; try { p = JSON.parse(text); } catch { p = { detail: text }; }
      throw new Error(p.detail || p.error || response.statusText);
    }
  } catch (e) {
    timers.forEach((id) => clearInterval(id)); timers.clear();
    $('#vOut').innerHTML = ''; toast(e.message, true); return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop();
      for (const block of blocks) {
        const line = block.trim();
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data.startsWith('[DONE] ')) {
          timers.forEach((id) => clearInterval(id)); timers.clear();
          const result = JSON.parse(data.slice(7));
          if (result.system) $('#vSystem').value = result.system;
          state.lastVerified = { ...body, system: result.system };
          state.lastResult = result;
          $('#vPublish').disabled = false;
          $('#vPublish').textContent = state.bounty ? 'Publish and claim bounty' : 'Publish to the market';
          $('#vDownload').disabled = false;
          $('#vOut').insertAdjacentHTML('afterbegin', summaryBlock(result));
          applyTimingBars($('#vOut'));
        } else {
          const verdict = JSON.parse(data);
          if (verdict.error) toast(verdict.error, true);
          else {
            if (timers.has(verdict.method)) {
              clearInterval(timers.get(verdict.method));
              timers.delete(verdict.method);
            }
            const existing = $('#vOut').querySelector(`.verdict[data-method="${verdict.method}"]`);
            if (existing) existing.outerHTML = verdictRow(verdict);
            else $('#vOut').insertAdjacentHTML('beforeend', verdictRow(verdict));
          }
        }
      }
    }
  } catch (e) {
    timers.forEach((id) => clearInterval(id)); timers.clear();
    toast(e.message, true);
  }
}

async function runBrowserVerify() {
  let body;
  try {
    body = { proof: parse($('#vProof').value), statement: parse($('#vKey').value, {}),
             publicSignals: parse($('#vPublic').value, []) };
  } catch (e) { toast(`that is not JSON: ${e.message}`, true); return; }
  const system = $('#vSystem').value || body.statement.protocol || body.proof.protocol;
  if (!system) { toast('pick the system — this tab cannot sniff it', true); return; }
  try {
    const answer = await verifyInBrowser({ ...body, system });
    const row = { method: 'browser', status: answer.ok ? 'valid' : 'invalid',
                  detail: answer.detail, ms: answer.detail.ms };
    $('#vOut').insertAdjacentHTML('beforeend', verdictRow(row));
    toast(`your browser says ${answer.ok ? 'valid' : 'INVALID'}`);
  } catch (e) { toast(e.message, true); }
}

async function loadSample() {
  const system = $('#vSystem').value;
  const SIGMA_DEFAULTS = {
    schnorr:  { system: 'schnorr', secret: '424242', context: '0xprof demo' },
    merkle:   { system: 'merkle', leaves: ['alice', 'bob', 'carol', 'dave'], index: 2 },
    dleq:     { system: 'dleq', secret: '31337', context: '0xprof demo' },
    pedersen: { system: 'pedersen', value: '1000', context: '0xprof demo' },
  };
  const SIGMA_TOASTS = {
    schnorr:  'schnorr sample loaded — I hold this key',
    merkle:   'merkle sample loaded — this leaf is in that tree',
    dleq:     'dleq sample loaded — same log, two bases',
    pedersen: 'pedersen sample loaded — value hidden in a commitment',
  };
  const sample = SIGMA_DEFAULTS[system]
    ? await api('/prove', { method: 'POST', body: SIGMA_DEFAULTS[system] })
    : await api('/sample' + (system ? `?system=${encodeURIComponent(system)}` : ''));
  $('#vProof').value = JSON.stringify(sample.proof, null, 1);
  $('#vKey').value = JSON.stringify(sample.statement, null, 1);
  $('#vPublic').value = JSON.stringify(sample.public_signals);
  $('#vSystem').value = sample.system;
  ['vProof', 'vKey', 'vPublic'].forEach((id) => state.footFor?.($(`#${id}`)));
  toast(SIGMA_TOASTS[system] || `${sample.system} sample loaded — ${sample.about}`);
}

// ── prove tab ───────────────────────────────────────────────────────

function downloadAs(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 1)], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function artifactPanel(filename, obj) {
  return `<div class="artifact-panel"><div class="ap-head"><span class="fname">${escape(filename)}</span><button class="ghost small" data-dl-name="${escape(filename)}">Download</button><button class="ghost small" data-cp-name="${escape(filename)}">Copy</button></div><pre>${escape(JSON.stringify(obj, null, 1))}</pre></div>`;
}

// One card per system the box can prove without a trusted setup. The shapes
// differ enough that a generic form would be worse than four honest ones.
const PROVE_FORMS = {
  schnorr: () => ({ secret: $('#pSecret').value, context: $('#pContext').value }),
  dleq: () => ({ secret: $('#pdSecret').value, context: $('#pdContext').value }),
  pedersen: () => ({ value: $('#ppValue').value, context: $('#ppContext').value,
                     blinding: $('#ppBlinding').value.trim() || undefined }),
  merkle: () => ({ leaves: $('#pLeaves').value.split('\n').map((s) => s.trim()).filter(Boolean),
                   index: Number($('#pIndex').value) }),
};

async function prove(system) {
  const body = { system, ...(PROVE_FORMS[system] || PROVE_FORMS.merkle)() };
  $('#pOut').innerHTML = '<div class="placeholder"><span class="spin"></span>proving, then checking it twice…</div>';
  try {
    const made = await api('/prove', { method: 'POST', body });
    state.lastVerified = { system: made.system, proof: made.proof,
                           statement: made.statement, public_signals: made.public_signals };
    const artifacts = {
      'verification_key.json': made.statement,
      'proof.json': made.proof,
      'public.json': made.public_signals || [],
    };
    $('#pOut').innerHTML = `
      ${summaryBlock(made.verification)}
      ${(made.verification.verdicts || []).map(verdictRow).join('')}
      ${artifactPanel('verification_key.json', made.statement)}
      ${artifactPanel('proof.json', made.proof)}
      ${artifactPanel('public.json', made.public_signals || [])}
      <div class="row"><button id="proveSend">Send this to Verify</button>
      <button class="ghost" id="provePublish">Publish it</button>
      <button class="ghost" id="proveDownload">Download bundle</button></div>`;
    applyTimingBars($('#pOut'));
    $('#pOut').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-dl-name],[data-cp-name]');
      if (!btn) return;
      const val = artifacts[btn.dataset.dlName || btn.dataset.cpName];
      if (!val) return;
      if (btn.dataset.dlName) downloadAs(btn.dataset.dlName, val);
      if (btn.dataset.cpName) navigator.clipboard?.writeText(JSON.stringify(val, null, 1))
        .then(() => toast(`${btn.dataset.cpName} copied`))
        .catch(() => toast('copy failed', true));
    });
    $('#proveSend').onclick = () => {
      $('#vProof').value = JSON.stringify(made.proof, null, 1);
      $('#vKey').value = JSON.stringify(made.statement, null, 1);
      $('#vPublic').value = JSON.stringify(made.public_signals || []);
      $('#vSystem').value = made.system;
      ['vProof', 'vKey', 'vPublic'].forEach((id) => state.footFor?.($(`#${id}`)));
      showTab('verify');
    };
    $('#provePublish').onclick = () => { prefillPublish(); $('#publishDlg').showModal(); };
    $('#proveDownload').onclick = () => downloadAs('verified_bundle.json', { system: made.system, proof: made.proof, statement: made.statement, public_signals: made.public_signals || [], verdicts: made.verification.verdicts, status: made.verification.status, why: made.verification.why });
  } catch (e) { $('#pOut').innerHTML = ''; toast(e.message, true); }
}

// ── publish prefill ──────────────────────────────────────────────────
const PUBLISH_TITLES = {
  schnorr:    'Schnorr proof of knowledge',
  merkle:     'Merkle inclusion proof',
  dleq:       'DLEQ proof (shared discrete log)',
  pedersen:   'Pedersen commitment opening',
  groth16:    'Groth16 proof',
};

function prefillPublish() {
  if (!state.lastVerified) return;
  const sys = state.lastVerified.system || '';
  const titleEl = $('#pubTitle');
  const tagsEl = $('#pubTags');
  if (titleEl && !titleEl.value) {
    titleEl.value = PUBLISH_TITLES[sys] || `${sys} proof`;
  }
  if (tagsEl && !tagsEl.value) {
    const sysInfo = (state.systems || []).find((s) => s.system === sys);
    const zkTag = sysInfo?.zero_knowledge ? 'zk' : 'nzk';
    tagsEl.value = [sys, zkTag].filter(Boolean).join(', ');
  }
}

// ── bounties ────────────────────────────────────────────────────────

function settlesIn(ts) {
  const seconds = Math.max(0, Math.floor(ts - Date.now() / 1000));
  if (!seconds) return 'now — anyone can settle it';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours >= 24 ? `in ${Math.floor(hours / 24)}d ${hours % 24}h`
    : hours ? `in ${hours}h ${minutes}m` : `in ${minutes}m`;
}

function bountyCard(bounty) {
  const rules = (bounty.require || []).map((r) => {
    if ('equals' in r) return `signal[${r.index}] = ${r.equals}`;
    if ('min' in r) return `signal[${r.index}] ≥ ${r.min}`;
    if ('max' in r) return `signal[${r.index}] ≤ ${r.max}`;
    return '';
  }).filter(Boolean);
  const staked = Number(bounty.stake) > 0;
  const open = bounty.state === 'open';
  const seats = bounty.tokens || [];
  const mine = staked && seats.find((t) => t.state === 'locked'
    && (t.holder || '').toLowerCase() === (state.address || '').toLowerCase());
  const due = staked && open && bounty.settles <= Date.now() / 1000;
  const tone = { open: 'claimed', paid: 'verified', expired: 'unverified', cancelled: 'unverified' };
  const subs = bounty.submissions.length;
  return `<div class="card">
    <div class="top">
      <span class="tagchip sys">${escape(bounty.system)}</span>
      ${staked ? `<span class="tagchip">round ${bounty.round || 1}</span>` : ''}
      <span class="status ${tone[bounty.state] || 'unverified'}">${escape(bounty.state)}</span>
    </div>
    <h4>${escape(bounty.title)}</h4>
    ${bounty.description ? `<div class="desc">${escape(bounty.description)}</div>` : ''}
    <div class="reward"><b class="price">${bounty.reward}</b><span>credits in escrow · must reach <b>${escape(bounty.accept_status)}</b></span></div>
    ${rules.length ? `<div class="rules">${rules.map((r) => `<span class="tagchip">${escape(r)}</span>`).join('')}</div>` : ''}
    ${staked ? `<div class="facts hint"><b>${bounty.stake}</b> credit(s) buys one token, locked until the
      ${escape(bounty.settle || 'daily')} reset <b>${escape(settlesIn(bounty.settles))}</b> ·
      ${seats.length} prover(s) signed · pot ${bounty.pot || 0}. The first token among accepted
      proofs wins; a seat that never submits forfeits to the winner${mine ? ` · <b>you hold token #${mine.seq}</b>` : ''}</div>` : ''}
    <div class="spacer"></div>
    <div class="who-row">
      ${avatar(bounty.poster)}<span class="hint">posted by <span class="mono">${escape(short(bounty.poster, 12))}</span> · ${subs} submission${subs === 1 ? '' : 's'}${bounty.winner ? ` · won by <span class="mono">${escape(short(bounty.winner, 12))}</span>` : ''}</span>
    </div>
    <footer>
      ${open && staked && !mine ? `<button data-act="join" data-id="${bounty.id}">Put in ${bounty.stake} for a token</button>` : ''}
      ${open && (!staked || mine) ? `<button data-act="submit" data-id="${bounty.id}" data-system="${escape(bounty.system)}">Submit a proof</button>` : ''}
      ${due ? `<button data-act="settle" data-id="${bounty.id}">Settle the round</button>` : ''}
      ${open && bounty.poster === state.address ? `<button class="ghost" data-act="cancel" data-id="${bounty.id}">Cancel</button>` : ''}
      ${bounty.state === 'paid' && bounty.winning_proof ? `<button class="ghost" data-act="view-winning" data-id="${bounty.winning_proof}">View winning proof →</button>` : ''}
    </footer>
    ${open && subs > 0 ? `<div class="submission-log">${bounty.submissions.slice(-5).reverse().map((s) => `<div class="sub-row"><button class="ghost small mono" data-act="view-winning" data-id="${escape(s.proof)}">${escape(short(s.proof, 10))}</button><span class="hint mono">${escape(short(s.prover, 10))}</span><span class="status ${s.accepted ? 'claimed' : 'unverified'}">${s.accepted ? 'accepted' : 'rejected'}</span>${s.why ? `<span class="hint sub-why">${escape(s.why)}</span>` : ''}</div>`).join('')}</div>` : ''}
  </div>`;
}

async function loadBounties() {
  const { bounties } = await api('/bounties');
  $('#bountyList').innerHTML = bounties.length ? bounties.map(bountyCard).join('')
    : '<div class="empty">no bounties — post one and the escrow makes it real</div>';
  setCounts(await api('/status'));
}

function showBountyCtx(system) {
  const existing = $('#bountyCtx');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.id = 'bountyCtx';
  div.className = 'bounty-ctx';
  div.innerHTML = `<span>You are submitting a proof for <b>${escape(system)}</b> — verify it below, then hit Publish to claim the reward.</span>`
    + `<button class="ghost small" id="bountyCtxCancel" aria-label="Cancel bounty submission">×</button>`;
  $('#tab-verify .grid3').insertAdjacentElement('beforebegin', div);
  $('#bountyCtxCancel').onclick = () => {
    state.bounty = null;
    state.bountySystem = null;
    div.remove();
    $('#vPublish').textContent = 'Publish to the market';
  };
}

function parseRules(text) {
  return (text || '').split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
    const [index, op, value] = line.split(/\s+/);
    const rule = { index: Number(index) };
    if (op === 'equals' || op === '==') rule.equals = value;
    else if (op === 'min' || op === '>=') rule.min = Number(value);
    else if (op === 'max' || op === '<=') rule.max = Number(value);
    else throw new Error(`don't know the rule "${line}" — use equals, min or max`);
    return rule;
  });
}

// ── methods & systems ───────────────────────────────────────────────

function methodCard(method) {
  return `<div class="card">
    <div class="mhead">${glyphFor(method.method)}
      <div title="${method.authoritative ? 'counts toward the quorum' : 'can contest a proof, never promote one'}"><h4>${escape(method.method)}</h4><div class="sub">${method.authoritative ? 'authoritative' : 'witness'}</div></div>
      <span class="status ${method.available ? 'verified' : 'unverified'}">${method.available ? 'ready' : 'unavailable'}</span></div>
    <div class="desc" style="margin-top:12px">${escape(method.note || '')}</div>
    <dl class="kv">
      <dt>runs in</dt><dd>${escape(method.runs_in)}</dd>
      <dt>verifies</dt><dd>${(method.systems || []).map((x) => `<span class="tagchip sys">${escape(x)}</span>`).join('') || '—'}</dd>
      ${method.endpoint ? `<dt>via</dt><dd class="mono">${escape(method.endpoint)}${method.chain_id ? ` · chain ${method.chain_id}` : ''}</dd>` : ''}
    </dl>
  </div>`;
}

function systemCard(system) {
  const by = system.available_here || system.methods;
  return `<div class="card">
    <div class="head"><h4>${escape(system.label)}</h4><span class="tagchip ${system.zero_knowledge ? 'zk' : 'nzk'}">${system.zero_knowledge ? 'zero-knowledge' : 'not zero-knowledge'}</span></div>
    <div class="claim">“${escape(system.claims)}”</div>
    <dl class="kv">
      <dt>family</dt><dd>${escape(system.family)}${system.curves.length ? ` · ${system.curves.map(escape).join(', ')}` : ''}</dd>
      <dt>setup</dt><dd>${escape(system.trusted_setup || 'none')}</dd>
      <dt>proof</dt><dd>${escape(system.proof_size)}</dd>
      <dt>verifier</dt><dd>${escape(system.verifier_cost)}</dd>
      <dt>checked by</dt><dd>${by.map((m) => `<span class="tagchip sys">${escape(m)}</span>`).join('')}</dd>
    </dl>
  </div>`;
}

// ── wallet ──────────────────────────────────────────────────────────

// A key made in this tab is the account, and it is one cleared browser profile
// away from gone. So the console shows it, lets you take it with you, and only
// destroys it when told to twice.
function keyPanel() {
  if (kind() !== 'local' || !localKey()) return '';
  return `<div class="panel keybox">
    <h4>this account is a key in this browser</h4>
    <div class="hint">Nobody else has it — not this box, not the gateway. Clearing
      site data for this origin deletes it, and with it the account, its credits
      and the right to speak for the proofs it published. Copy it somewhere if
      you intend to come back.</div>
    <div class="row">
      <button class="ghost" id="keyShow">Show the private key</button>
      <button class="ghost" id="keyCopy">Copy it</button>
      <button class="ghost" id="keyForget">Forget it</button>
    </div>
    <pre id="keyOut" class="hidden"></pre>
  </div>`;
}

function wireKeyPanel() {
  if (!$('#keyShow')) return;
  $('#keyShow').onclick = () => {
    $('#keyOut').textContent = localKey();
    $('#keyOut').classList.toggle('hidden');
  };
  $('#keyCopy').onclick = async () => {
    try { await navigator.clipboard.writeText(localKey()); toast('key copied — keep it somewhere private'); }
    catch { $('#keyOut').textContent = localKey(); $('#keyOut').classList.remove('hidden');
            toast('this browser blocked the clipboard — copy it from below', true); }
  };
  $('#keyForget').onclick = async () => {
    const sure = await ask('Anything this key owns here becomes unreachable unless '
                           + 'you have a copy of it.', { title: 'Forget this key?', ok: 'Forget it' });
    if (!sure) return;
    forgetLocalKey(); setKind(''); signOut();
    toast('key forgotten');
  };
}

async function loadWallet() {
  if (!state.token) {
    $('#wallet').innerHTML = `<div class="empty">sign in to see credits, purchases and escrow
      <div class="row" style="justify-content:center;margin-top:14px">
        <button id="walletSignin">Sign in</button></div>
      <div class="hint">a wallet, or anonymously with a key this tab makes for you</div></div>`;
    $('#walletSignin').onclick = openSignIn;
    return;
  }
  const me = await api('/me');
  const { proofs: myProofs } = await api('/proofs?' + new URLSearchParams({ author: me.address })).catch(() => ({ proofs: [] }));
  $('#wallet').innerHTML = `
    <h3 class="mono" style="font-size:14px;word-break:break-all">${escape(me.address)}${me.owner ? ' <span class="status verified">owner</span>' : ''}</h3>
    <div class="hint">signed in with ${kind() === 'local'
      ? 'a key made in this browser — the market cannot tell, and does not ask'
      : 'a wallet'}</div>
    ${keyPanel()}
    <div class="stats" style="margin-top:16px">
      <span><b>${me.credits}</b>credits</span>
      <span><b>${me.escrowed}</b>in escrow</span>
      <span class="good"><b>${me.earned}</b>earned</span>
      <span><b>${me.spent}</b>spent</span>
      ${me.in_debt ? '<span class="bad"><b>in debt</b>a proof you sold was refunded</span>' : ''}
    </div>
    ${me.owner ? `<div class="row"><input id="grantTo" placeholder="0x… address" class="mono" style="flex:1;min-width:240px"><input id="grantAmt" type="number" value="100" style="max-width:110px"><button id="grantGo">Grant credits</button></div>` : ''}
    <h4>my proofs (${myProofs.length})</h4>
    ${myProofs.length ? myProofs.map((p) => `<div class="my-proof-row"><span class="status ${escape(p.status)}">${escape(p.status)}</span><span class="tagchip sys">${escape(p.system)}</span><b>${escape(p.title)}</b><span class="hint">${p.price > 0 ? p.price + ' credits' : 'free'}</span>${p.verifier_count ? `<span class="hint">${p.verifier_count} verifier${p.verifier_count !== 1 ? 's' : ''}</span>` : ''}<button class="ghost small" data-act="view-winning" data-id="${p.id}">View →</button></div>`).join('') : '<div class="hint">none yet</div>'}
    <h4>purchases</h4>
    ${me.purchases.length ? me.purchases.map((p) => `<div class="purchase"><span class="addr">${escape(short(p.proof, 16))}</span><span>${p.price} credits</span><span class="status ${escape(p.sold_as)}">${escape(p.sold_as)}</span>${p.refunded ? '<span class="tagchip nzk">refunded</span>' : ''} <button class="ghost small" data-act="refund" data-id="${p.proof}">Refund if it stopped verifying</button></div>`).join('') : '<div class="hint">none yet</div>'}
    <h4>ledger</h4>
    <div class="ledger">${me.history.slice().reverse().map((h) => `<div><span class="amt ${h.delta >= 0 ? 'pos' : 'neg'}">${h.delta >= 0 ? '+' : ''}${h.delta}</span><span>${escape(h.reason)}</span><span class="t">${ago(h.ts)}</span></div>`).join('') || '<div class="hint">nothing yet</div>'}</div>`;
  if (me.owner) {
    $('#grantGo').onclick = async () => {
      try {
        await api('/credits/grant', { method: 'POST',
          body: { address: $('#grantTo').value.trim(), amount: Number($('#grantAmt').value) } });
        toast('granted'); loadWallet();
      } catch (e) { toast(e.message, true); }
    };
  }
  wireKeyPanel();
}

// ── sign in ─────────────────────────────────────────────────────────
//
// Two doors, and the server sees one thing through both: an address that
// signed the challenge. The anonymous door makes a key in the tab rather than
// asking for one — no extension, no email, no account to create — and the
// signature it produces is the same signature a wallet would produce. That is
// why it is a real sign-in and not a guest mode: an anonymous account owns its
// proofs, holds its credits and can be contradicted by name like any other.

function signedInAs(address) {
  $('#signin').textContent = address ? short(address, 6) + '…' + address.slice(-4) : 'Sign in';
  $('#signin').classList.toggle('mono', !!address);
  $('#signin').title = address
    ? `${address} — ${kind() === 'local' ? 'a key made in this browser' : 'a wallet'}; click to sign out`
    : 'sign in with a wallet, or anonymously with a key this tab makes';
}

async function connect(door) {
  const who = await signer(door);
  const { message } = await api('/auth/challenge', { method: 'POST',
    body: { address: who.address } });
  if (who.kind === 'wallet') toast('sign the challenge in your wallet');
  const signature = await who.sign(message);
  const session = await api('/auth/verify', { method: 'POST',
    body: { address: who.address, message, signature } });
  state.token = session.token; state.address = who.address;
  setKind(who.kind);
  localStorage.setItem('zkprof_token', session.token);
  localStorage.setItem('zkprof_address', who.address);
  signedInAs(who.address);
  toast(session.owner ? 'signed in — you own this deployment'
    : (who.kind === 'local' ? 'signed in anonymously — the key is in this browser only'
                            : 'signed in'));
  loadWallet();
}

function signOut() {
  // The session goes; the key stays. Forgetting a key is a thing you do on
  // purpose, on the WALLET tab, after you have had the chance to copy it.
  state.token = ''; state.address = '';
  localStorage.removeItem('zkprof_token'); localStorage.removeItem('zkprof_address');
  signedInAs('');
  toast(localKey() ? 'signed out — the key made here is still in this browser'
                   : 'signed out');
  if ($('#tab-wallet').classList.contains('on')) loadWallet();
}

function openSignIn() {
  const returning = localAddress();
  $('#siWalletNote').textContent = hasWallet()
    ? 'an extension holds the key and signs the challenge'
    : 'no wallet extension in this browser';
  $('#siWallet').disabled = !hasWallet();
  $('#siAnon').textContent = returning ? 'use the key in this browser' : 'make a key in this tab';
  $('#siAnonNote').innerHTML = returning
    ? `this browser already holds <span class="addr">${escape(short(returning, 20))}</span> — signing in re-uses it`
    : 'a secp256k1 key generated here, kept in this browser, never sent anywhere. '
      + 'It signs exactly what a wallet would sign.';
  $('#siImportKey').value = '';
  $('#signinDlg').showModal();
}

async function fromDialog(door, extra) {
  try {
    if (extra) extra();
    await connect(door);
    $('#signinDlg').close();
  } catch (e) { toast(e.message, true); }
}

// ── wiring ──────────────────────────────────────────────────────────

function showTab(name) {
  const slash = name.indexOf('/');
  const tab = slash !== -1 ? name.slice(0, slash) : name;
  const deepId = slash !== -1 ? name.slice(slash + 1) || null : null;
  $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.tab').forEach((s) => s.classList.toggle('on', s.id === `tab-${tab}`));
  location.hash = tab;
  if (deepId) state.openId = deepId;
  if (tab === 'market') loadMarket().catch((e) => toast(e.message, true));
  if (tab === 'bounties') loadBounties().catch((e) => toast(e.message, true));
  if (tab === 'wallet') loadWallet().catch((e) => toast(e.message, true));
}

async function boot() {
  $$('#tabs button').forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
  $$('button[data-go]').forEach((b) => { b.onclick = () => showTab(b.dataset.go); });
  $('#heroDemo').onclick = async () => {
    const btn = $('#heroDemo');
    btn.disabled = true;
    try {
      const sample = await api('/sample');
      await inlineHeroRace(sample);
    } catch (e) { toast(e.message, true); }
    btn.disabled = false;
    btn.textContent = 'Watch it again';
  };
  // A session that predates the two doors says which address, not which door.
  // Work it out once rather than guessing later, when the answer decides which
  // key signs a re-verification.
  if (state.address && !kind()) {
    setKind(localAddress().toLowerCase() === state.address.toLowerCase() ? 'local' : 'wallet');
  }
  $('#signin').onclick = () => (state.token ? signOut() : openSignIn());
  signedInAs(state.address);
  $('#siWallet').onclick = () => fromDialog('wallet');
  $('#siAnon').onclick = () => fromDialog('local');
  $('#siImport').onclick = () => fromDialog('local',
    () => importLocalKey($('#siImportKey').value));

  const info = await api('/');
  state.methods = (await api('/methods')).methods;
  state.systems = (await api('/systems')).systems;
  api('/status').then((s) => { setCounts(s); state.lastCounts = (s.market.checks ?? 0) + (s.market.proofs ?? 0); }).catch(() => {});
  setInterval(() => {
    api('/status').then((s) => {
      const total = (s.market.checks ?? 0) + (s.market.proofs ?? 0);
      if (total > state.lastCounts) {
        const diff = total - state.lastCounts;
        $('#refresh').classList.add('has-new');
        $('#refresh').title = `${diff} new check${diff === 1 ? '' : 's'} · click to refresh`;
      }
    }).catch(() => {});
  }, 60000);

  const authoritative = state.methods.filter((m) => m.authoritative);
  $('#methodPips').innerHTML = authoritative.map((m) =>
    `<span class="pip ${m.available ? 'up' : ''}" title="${escape(m.method)} — ${m.available ? 'ready' : 'unavailable'}"></span>`).join('');
  $('#methodLabel').textContent = `${authoritative.filter((m) => m.available).length} of ${authoritative.length} verifiers ready`;
  $('#heroMethods').innerHTML = authoritative.map((m) => `<div class="hb-row" title="${escape(m.note || '')}">
    ${glyphFor(m.method)}
    <div style="min-width:0"><b>${escape(m.method)}</b><span class="w">${escape(m.runs_in)}</span></div>
    <span class="ok${m.available ? '' : ' off'}">${m.available ? 'ready' : 'offline'}</span></div>`).join('');
  $('#methodList').innerHTML = state.methods.map(methodCard).join('');
  $('#systemList').innerHTML = state.systems.map(systemCard).join('');

  const options = state.systems.map((s) => `<option value="${s.system}">${s.label}</option>`).join('');
  $('#fSystem').insertAdjacentHTML('beforeend', options);
  $('#vSystem').insertAdjacentHTML('beforeend', options);
  $('#bSystem').innerHTML = options;

  $('#info').innerHTML = `
    <h3>What this is</h3>
    <p>${escape(info.description)}. A proof arrives, and every verifier on this
    box that can check it does — separately. The status you see is a function of
    what they said, and it is spelled out rather than asserted:</p>
    <table>
      <tr><th>status</th><th>what it means</th></tr>
      <tr><td><span class="status verified">verified</span></td><td>${info.quorum} or more independent methods agree it is valid</td></tr>
      <tr><td><span class="status claimed">claimed</span></td><td>exactly one method has checked it — a claim, not a verification</td></tr>
      <tr><td><span class="status invalid">invalid</span></td><td>a method rejected it and none disagreed</td></tr>
      <tr><td><span class="status disputed">disputed</span></td><td>they disagree. One of these implementations is wrong, and finding out which is the point of running five</td></tr>
      <tr><td><span class="status unverified">unverified</span></td><td>nothing has checked it yet, or every method errored</td></tr>
    </table>
    <h3>Methods verify. People sign.</h3>
    <p>Every run of the methods is filed against the address that asked for it,
    and every listing shows that roster next to its verdicts. The two are not
    the same kind of thing and are never merged: <em>native said valid</em> is a
    claim about arithmetic, <em>0x89bc… re-ran it an hour ago</em> is a claim
    about a person. One of them you can check by doing the maths; the other you
    can check by recovering an address from a signature.</p>
    <p>Which is why re-verifying costs a <code>personal_sign</code>. Checking a
    loose proof on the VERIFY tab is free and anonymous and always will be —
    but re-running a <em>listing</em> overwrites what it says its verifiers
    think, in front of the people deciding whether to buy it. That gets a name
    on it. The message names the proof, so a signature cannot be moved to
    another one; it expires in ten minutes; and it is spent once, because a
    signature that can be replayed is a signature somebody else can keep
    spending in your name.</p>
    <p>The one row on the roster that can contradict this box is the browser
    one. A witness who ran the proof in their own tab and got <em>invalid</em>
    is either wrong or has caught this server lying, and there is no third
    option — so it is shown, in its own column, and it never changes the
    status by itself.</p>
    <h3>Signing in without a wallet</h3>
    <p>A name here is an address and nothing else — no password, no email, no
    account to create. If you have a wallet extension it signs the challenge.
    If you have not, this console makes a secp256k1 key in the tab and signs
    with that one: same curve, same <code>personal_sign</code>, same recovered
    address, and this box has no way to tell the two apart and no business
    asking. An anonymous account publishes, buys and re-verifies like any
    other, and its runs sit on the roster under their own address.</p>
    <p>The difference is custody, and it is the whole difference. A key made
    here lives in this browser's storage: clear the site data and the account
    is gone, along with its credits and its standing behind the proofs it
    published. The WALLET tab will show it to you so you can keep a copy, and
    take one back if you paste it into another browser.</p>
    <h3>What a purchase guarantees</h3>
    <p>That this box ran the proof through every method it has, published each
    answer, and is holding the same bytes it checked. After buying you can run
    those methods again — and the browser button runs one in your own tab,
    which is the only check that does not require trusting this server. If a
    proof you bought as verified stops verifying, the refund takes the credits
    back off the seller.</p>
    <p>What it cannot guarantee is that the <em>statement</em> is one you care
    about. A perfectly valid proof of an uninteresting claim is still valid.
    Reading the statement is your job, and it is free.</p>
    <h3>API</h3>
    <p>Same origin, at <code>${escape(API)}</code> — ${info.endpoints.map((e) => `<code>${escape(e)}</code>`).join(' ')}</p>`;

  document.body.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const { act, id } = button.dataset;
    try {
      if (act === 'open') await openProof(id, true);
      if (act === 'recheck') await signedRecheck(id);
      if (act === 'browser') await browserCheck(id);
      if (act === 'run-live') await inlineRace(id);
      if (act === 'link') { navigator.clipboard.writeText(location.origin + location.pathname + '#market/' + id); toast('link copied'); }
      if (act === 'badge') { navigator.clipboard.writeText(`[![verified by 0xprof](${API}/proofs/${id}/badge.svg)](${location.origin + location.pathname}#market/${id})`); toast('badge markdown copied — paste it in your README'); }
      if (act === 'buy') { const receipt = await api(`/proofs/${id}/buy`, { method: 'POST' }); toast(`bought for ${receipt.charged} credits`); await loadMarket(); await openProof(id); }
      if (act === 'refund') { const receipt = await api(`/proofs/${id}/refund`, { method: 'POST' }); toast(`refunded ${receipt.refunded}`); await loadWallet(); }
      if (act === 'cancel') { await api(`/bounties/${id}/cancel`, { method: 'POST' }); toast('cancelled, escrow returned'); await loadBounties(); }
      if (act === 'join') { const seat = await api(`/bounties/${id}/join`, { method: 'POST' }); toast(`token #${seat.token.seq} — ${seat.token.stake} credit(s) locked until the reset`); await loadBounties(); }
      if (act === 'settle') { const settled = await api(`/bounties/${id}/settle`, { method: 'POST' }); toast(settled.winner ? `settled — won by ${short(settled.winner, 12)}` : 'unwon — tokens returned, round reset'); await loadBounties(); }
      if (act === 'submit') { state.bounty = id; state.bountySystem = button.dataset.system || ''; showBountyCtx(state.bountySystem); showTab('verify'); }
      if (act === 'tag') { state.activeTag = button.dataset.tag; showTab('market'); }
      if (act === 'view-winning') { state.openId = id; showTab('market'); }
      if (act === 'cleartag') { state.activeTag = ''; await loadMarket(); }
      if (act === 'author') { state.activeAuthor = button.dataset.addr; showTab('market'); }
      if (act === 'clearauthor') { state.activeAuthor = ''; await loadMarket(); }
    } catch (e) { toast(e.message, true); }
  });

  const footFor = (area) => {
    const foot = $(`.drop-foot[data-for="${area.id}"]`);
    if (!foot) return;
    const text = area.value.trim();
    if (!text) {
      foot.className = 'drop-foot'; foot.textContent = 'paste, or drop a file here';
      if (area.id === 'vProof' || area.id === 'vKey') $('#vMethodPreview').innerHTML = '';
      return;
    }
    try {
      const value = JSON.parse(text);
      if (area.id === 'vProof' && value.proof && value.statement) {
        $('#vProof').value = JSON.stringify(value.proof, null, 1);
        $('#vKey').value = JSON.stringify(value.statement, null, 1);
        $('#vPublic').value = JSON.stringify(value.public_signals || [], null, 1);
        if (value.system) $('#vSystem').value = value.system;
        footFor($('#vProof')); footFor($('#vKey')); footFor($('#vPublic'));
        toast('bundle detected — proof, statement and signals loaded');
        return;
      }
      const what = Array.isArray(value) ? `${value.length} item array`
        : `${Object.keys(value).length} keys${value.protocol ? ` · ${value.protocol}` : ''}`;
      foot.className = 'drop-foot ok';
      foot.textContent = `valid JSON · ${what} · ${(new Blob([text]).size / 1024).toFixed(1)} KB`;
      if (area.id === 'vProof' || area.id === 'vKey') {
        try {
          const proof = JSON.parse($('#vProof').value);
          const statement = JSON.parse($('#vKey').value);
          const system = sniffSystem(proof, statement);
          if (system) { $('#vSystem').value = system; renderMethodPreview(system); }
          else { $('#vMethodPreview').innerHTML = ''; }
        } catch { $('#vMethodPreview').innerHTML = ''; }
      }
    } catch { foot.className = 'drop-foot bad'; foot.textContent = 'not valid JSON yet'; }
  };
  state.footFor = footFor;
  ['vProof', 'vKey', 'vPublic'].forEach((id) => { $(`#${id}`).addEventListener('input', () => footFor($(`#${id}`))); });
  $$('input[type=file]').forEach((input) => {
    input.onchange = async () => {
      const file = input.files[0];
      if (file) { const area = $(`#${input.dataset.into}`); area.value = await file.text(); footFor(area); }
    };
  });
  $$('.drop').forEach((drop) => {
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
    drop.ondragleave = () => drop.classList.remove('over');
    drop.ondrop = async (e) => {
      e.preventDefault(); drop.classList.remove('over');
      const file = e.dataTransfer.files[0];
      if (file) { const area = $(`#${$('input[type=file]', drop).dataset.into}`); area.value = await file.text(); footFor(area); }
    };
  });

  $('#vRun').onclick = runVerify;
  $('#vBrowser').onclick = runBrowserVerify;
  $('#vSample').onclick = () => loadSample().catch((e) => toast(e.message, true));
  $('#vSampleLink').onclick = (e) => { e.preventDefault(); loadSample().catch((err) => toast(err.message, true)); };
  $('#vPublish').onclick = () => { prefillPublish(); $('#publishDlg').showModal(); };
  $('#vDownload').onclick = () => {
    if (!state.lastVerified || !state.lastResult) return;
    downloadAs('verified_bundle.json', {
      system: state.lastVerified.system,
      proof: state.lastVerified.proof,
      statement: state.lastVerified.statement,
      public_signals: state.lastVerified.public_signals,
      verdicts: state.lastResult.verdicts,
      status: state.lastResult.status,
      why: state.lastResult.why,
    });
  };
  $('#refresh').onclick = () => loadMarket();
  $('#q').onkeydown = (e) => { if (e.key === 'Enter') loadMarket(); };
  [$('#fSystem'), $('#fStatus'), $('#fSale'), $('#fSort')].forEach((el) => { el.onchange = loadMarket; });
  $$('button[data-prove]').forEach((b) => { b.onclick = () => prove(b.dataset.prove); });
  $('#publishOpen').onclick = () => {
    if (!state.lastVerified) { toast('verify something first — publish takes what is in VERIFY', true); showTab('verify'); return; }
    prefillPublish();
    $('#publishDlg').showModal();
  };
  $('#bountyOpen').onclick = () => $('#bountyDlg').showModal();
  $('#bountySweep').onclick = async () => {
    const swept = await api('/bounties/sweep', { method: 'POST' });
    toast(`${swept.settled || 0} round(s) settled, ${swept.expired} expired bounties returned their escrow`);
    loadBounties();
  };

  $('#pubGo').onclick = async () => {
    if (!state.lastVerified) return;
    try {
      const published = await api('/proofs', { method: 'POST', body: {
        ...state.lastVerified,
        title: $('#pubTitle').value, description: $('#pubDesc').value,
        tags: $('#pubTags').value.split(',').map((t) => t.trim()).filter(Boolean),
        price: Number($('#pubPrice').value) } });
      toast(`published — ${published.status}`);
      if (state.bounty) {
        const answer = await api(`/bounties/${state.bounty}/submit`, { method: 'POST',
          body: { proof: state.lastVerified.proof,
                  public_signals: state.lastVerified.public_signals } });
        toast(answer.submission.accepted
          ? (answer.bounty && answer.bounty.stake
            ? 'accepted — settles at the reset, when the tokens liquidate'
            : 'bounty won — reward released')
          : `not accepted: ${answer.submission.why}`);
        state.bounty = null;
        state.bountySystem = null;
        const bc = $('#bountyCtx'); if (bc) bc.remove();
      }
      state.openId = published.id;
      showTab('market');
    } catch (e) { toast(e.message, true); }
  };

  $('#bGo').onclick = async () => {
    try {
      await api('/bounties', { method: 'POST', body: {
        title: $('#bTitle').value, system: $('#bSystem').value,
        reward: Number($('#bReward').value), vkey: parse($('#bVkey').value, {}),
        stake: Number($('#bStake').value) || 0, settle: $('#bSettle').value,
        require: parseRules($('#bRequire').value) } });
      toast('posted and funded'); showTab('bounties');
    } catch (e) { toast(e.message, true); }
  };

  showTab((location.hash || '#market').slice(1));
}

boot().catch((e) => {
  document.querySelector('main').innerHTML =
    `<div class="empty">the API is not answering — ${escape(e.message)}</div>`;
});
