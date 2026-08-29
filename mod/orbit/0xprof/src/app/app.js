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
  systems: [], methods: [], proofs: [], sample: null, lastVerified: null,
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

// ── verdict rendering — the one component that matters ──────────────

function verdictRow(verdict) {
  const detail = verdict.detail || {};
  const words = verdict.error
    ? verdict.error
    : Object.entries(detail).filter(([k]) => k !== 'ms')
      .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join('  ');
  return `<div class="verdict ${verdict.status}">
    <span class="m">${escape(verdict.method)}${verdict.by ? `<br><span class="t">${escape(short(verdict.by, 12))}</span>` : ''}</span>
    <span class="s">${escape(verdict.status)}</span>
    <span class="d">${escape(words)}</span>
    <span class="t">${verdict.ms != null ? `${verdict.ms}ms` : ''}</span>
  </div>`;
}

function summaryBlock(result) {
  const witnesses = result.witnesses || { valid: [], invalid: [] };
  const extra = witnesses.valid.length || witnesses.invalid.length
    ? `<div class="hint">witnesses — ${witnesses.valid.length} say valid, ${witnesses.invalid.length} say invalid; witnesses never change the status, they only contest it</div>`
    : '';
  return `<div class="summary">
    <span class="status ${result.status}">${result.status}</span>
    <b> ${escape(result.why || '')}</b>
    ${result.contested ? '<div class="hint" style="color:var(--error)">contested — somebody\'s own verifier disagrees with this box</div>' : ''}
    ${extra}
  </div>`;
}

function renderVerdicts(target, result) {
  const rows = (result.verdicts || []).map(verdictRow).join('');
  $(target).innerHTML = summaryBlock(result) + rows;
}

// ── who checked it ──────────────────────────────────────────────────
//
// The methods do the maths; the addresses below only made them run. The two
// are shown next to each other and never merged, for the same reason the five
// verdict pips are never merged into one badge: "verified" is a claim about
// arithmetic, "0x89bc… re-ran it an hour ago" is a claim about a person, and a
// reader is entitled to weigh them separately.

const KIND_GLYPH = { published: '⊕', republished: '⊕', rechecked: '✎',
                     witnessed: '👁', 'refund check': '↺' };
const KIND_WORD = {
  published: 'published it — that first run is what the listing was built from',
  republished: 'published the same bytes again, which ran the methods again',
  rechecked: 'signed a re-verification: they asked, this box re-ran everything',
  witnessed: 'ran it in their own browser and reported what it said',
  'refund check': 'claimed a refund, which re-runs the methods before deciding',
};

function verifierChip(person) {
  const kinds = person.kinds || [];
  const glyphs = kinds.map((k) => KIND_GLYPH[k] || '·').join('');
  const words = kinds.map((k) => KIND_WORD[k] || k).join('; ');
  const signed = person.signed ? ` · ${person.signed} signed` : ' · unsigned';
  return `<span class="chip${person.contested ? ' contested' : ''}${person.signed ? ' signed' : ''}"
    title="${escape(person.address)} — ${escape(words)}${escape(signed)}, last ${ago(person.last)}">
    <span class="g">${glyphs}</span>${escape(short(person.address, 10))}${person.checks > 1 ? `<span class="n">×${person.checks}</span>` : ''}</span>`;
}

function verifierRow(proof) {
  const people = proof.verifiers || [];
  if (!people.length) return '';
  const more = (proof.verifier_count || people.length) - people.length;
  const contested = people.some((p) => p.contested);
  return `<div class="who-row">
    <span class="who-label">checked by</span>
    ${people.map(verifierChip).join('')}
    ${more > 0 ? `<span class="chip more">+${more}</span>` : ''}
    ${contested ? '<span class="chip contested">one of them disagreed</span>' : ''}
  </div>`;
}

function checkRow(entry) {
  const ran = (entry.methods || []).join(', ');
  return `<div class="check-entry${entry.saw === 'invalid' ? ' bad' : ''}">
    <span class="g">${KIND_GLYPH[entry.kind] || '·'}</span>
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
  const pips = (proof.methods || []).map((m) => {
    const cls = m.status === 'valid' ? 'up' : m.status === 'invalid' ? 'down'
      : m.status === 'error' ? 'warn' : '';
    return `<span class="pip ${cls}" title="${escape(m.method)}: ${escape(m.status)}"></span>`;
  }).join('');
  const price = proof.price > 0
    ? `<span class="price">${proof.price} credits</span>`
    : '<span class="free">free</span>';
  return `<div class="card" data-id="${proof.id}">
    <h4>${escape(proof.title)}</h4>
    <div class="meta">${escape(proof.system)} · ${proof.zero_knowledge ? 'zero-knowledge' : 'not zero-knowledge'} · ${escape(short(proof.id, 16))}</div>
    ${proof.description ? `<div class="desc">${escape(proof.description)}</div>` : ''}
    <div class="row" style="margin:8px 0 0">
      <span class="status ${proof.status}">${proof.status}</span>
      <span class="pips">${pips}</span>
      ${price}
    </div>
    <div class="hint" style="margin-top:6px">${escape(proof.why || '')}</div>
    ${verifierRow(proof)}
    <footer>
      <button class="ghost" data-act="open" data-id="${proof.id}">open</button>
      <button class="ghost" data-act="recheck" data-id="${proof.id}">sign &amp; re-verify</button>
      <button class="ghost" data-act="browser" data-id="${proof.id}">check in my browser</button>
      ${proof.price > 0 ? `<button data-act="buy" data-id="${proof.id}">buy</button>` : ''}
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
  const { proofs } = await api(`/proofs?${params}`);
  state.proofs = proofs;
  $('#proofList').innerHTML = proofs.length ? proofs.map(proofCard).join('')
    : '<div class="empty">no proofs yet — publish one from VERIFY, or make one in PROVE</div>';
  const status = await api('/status');
  setCounts(status);
  const counts = status.market.by_status || {};
  // The stats read off /status, which counts the whole market — so when a
  // filter is on, say how many of that total the list below is showing.
  const filtered = proofs.length !== status.market.proofs;
  $('#marketStats').innerHTML = [
    `<span><b>${status.market.proofs}</b> proofs${filtered ? ` <span class="hint">(${proofs.length} shown)</span>` : ''}</span>`,
    ...Object.entries(counts).map(([k, v]) => `<span><b>${v}</b> ${k}</span>`),
    `<span><b>${status.market.for_sale}</b> for sale</span>`,
    `<span><b>${status.market.volume}</b> credits traded</span>`,
    `<span><b>${status.market.checks ?? 0}</b> checks by <b>${status.market.verifiers ?? 0}</b> addresses</span>`,
    `<span><b>${status.bounties.open}</b> open bounties</span>`,
  ].join('');
  loadVerifiers().catch(() => {});
}

async function loadVerifiers() {
  const { verifiers } = await api('/verifiers?limit=12');
  if (!verifiers.length) { $('#verifierBoard').innerHTML = ''; return; }
  $('#verifierBoard').innerHTML = `
    <h3>Who has checked things here</h3>
    <p class="hint">Nobody on this list verified anything — the methods did. What
      an address can do is put its name on a run, and the last column is the only
      one that can contradict this box: somebody's own verifier, disagreeing.</p>
    <table>
      <tr>
        <th>address</th>
        <th title="a personal_sign naming one proof, spent on one re-run of every method">signed re-runs</th>
        <th title="listings whose first run of the methods this address caused">published</th>
        <th title="ran the proof in their own tab with the snarkjs wasm bundle">own browser</th>
        <th title="their own verifier said invalid about a proof this box likes — either they are wrong or this box is">disagreed</th>
        <th>last</th>
      </tr>
      ${verifiers.map((v) => `<tr>
        <td class="addr">${escape(short(v.address, 20))}${v.address.toLowerCase() === (state.address || '').toLowerCase() ? ' <span class="hint">(you)</span>' : ''}</td>
        <td>${v.signed || '—'}</td>
        <td>${v.published || '—'}</td>
        <td>${v.witnessed || '—'}</td>
        <td${v.contested ? ' class="bad"' : ''}>${v.contested || '—'}</td>
        <td class="hint">${ago(v.last)}</td>
      </tr>`).join('')}
    </table>`;
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
    return;
  }
  card.classList.add('open');
  const proof = await api(`/proofs/${id}`);
  const bytes = proof.locked
    ? `<div class="hint">the proof bytes are gated at ${proof.price} credits — sha256 ${escape(short(proof.proof_hash, 24))}</div>`
    : `<pre>${escape(JSON.stringify(proof.proof, null, 1).slice(0, 4000))}</pre>`;
  const checks = (proof.checks || []).slice().reverse();
  box.innerHTML = `
    <div class="verdicts">${(proof.verdicts || []).map(verdictRow).join('')}</div>
    <h4 style="margin-top:12px">who made those methods run</h4>
    <div class="checklog">${checks.length ? checks.map(checkRow).join('')
      : '<div class="hint">nothing but the publish — nobody has re-checked this yet</div>'}</div>
    <div class="hint">a signed row is a personal_sign over a message naming this
      proof: the address is recoverable from it without asking this box anything,
      which is the point. An unsigned row is this box’s own run at publish time.</div>
    <h4 style="margin-top:12px">statement (always public)</h4>
    <pre>${escape(JSON.stringify({ public_signals: proof.public_signals, statement: proof.statement }, null, 1).slice(0, 3000))}</pre>
    <h4>proof</h4>${bytes}
    ${proof.cid ? `<div class="hint">pinned as ${escape(proof.cid)}</div>` : ''}
    ${(proof.refundable_to || []).length ? `<div class="hint" style="color:var(--error)">${proof.refundable_to.length} purchase(s) can be refunded — this stopped verifying after it was sold</div>` : ''}`;
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
  $('#vOut').innerHTML = '<div class="empty"><span class="spin">◐</span> asking every method that can answer…</div>';
  try {
    const result = await api('/verify', { method: 'POST', body });
    state.lastVerified = { ...body, system: result.system };
    $('#vPublish').disabled = false;
    renderVerdicts('#vOut', result);
  } catch (e) {
    $('#vOut').innerHTML = '';
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
  const sample = await api('/sample');
  $('#vProof').value = JSON.stringify(sample.proof, null, 1);
  $('#vKey').value = JSON.stringify(sample.statement, null, 1);
  $('#vPublic').value = JSON.stringify(sample.public_signals);
  $('#vSystem').value = sample.system;
  toast(`${sample.system} sample loaded — ${sample.about}`);
}

// ── prove tab ───────────────────────────────────────────────────────

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
  $('#pOut').innerHTML = '<div class="empty"><span class="spin">◐</span> proving…</div>';
  try {
    const made = await api('/prove', { method: 'POST', body });
    state.lastVerified = { system: made.system, proof: made.proof,
                           statement: made.statement, public_signals: made.public_signals };
    $('#pOut').innerHTML = `
      ${summaryBlock(made.verification)}
      ${(made.verification.verdicts || []).map(verdictRow).join('')}
      <h4>statement</h4><pre>${escape(JSON.stringify(made.statement, null, 1))}</pre>
      <h4>proof</h4><pre>${escape(JSON.stringify(made.proof, null, 1))}</pre>
      <div class="row"><button id="proveSend">send this to VERIFY</button>
      <button class="ghost" id="provePublish">publish it</button></div>`;
    $('#proveSend').onclick = () => {
      $('#vProof').value = JSON.stringify(made.proof, null, 1);
      $('#vKey').value = JSON.stringify(made.statement, null, 1);
      $('#vPublic').value = JSON.stringify(made.public_signals || []);
      $('#vSystem').value = made.system;
      showTab('verify');
    };
    $('#provePublish').onclick = () => $('#publishDlg').showModal();
  } catch (e) { $('#pOut').innerHTML = ''; toast(e.message, true); }
}

// ── bounties ────────────────────────────────────────────────────────

function bountyCard(bounty) {
  const rules = (bounty.require || []).map((r) => {
    if ('equals' in r) return `signal ${r.index} == ${r.equals}`;
    if ('min' in r) return `signal ${r.index} ≥ ${r.min}`;
    if ('max' in r) return `signal ${r.index} ≤ ${r.max}`;
    return '';
  }).join(', ');
  return `<div class="card">
    <h4>${escape(bounty.title)}</h4>
    <div class="meta">${escape(bounty.system)} · posted by ${escape(short(bounty.poster, 12))} · ${escape(bounty.state)}</div>
    ${bounty.description ? `<div class="desc">${escape(bounty.description)}</div>` : ''}
    <div class="row" style="margin:8px 0 0">
      <span class="price">${bounty.reward} credits</span>
      <span class="hint">must reach <b>${escape(bounty.accept_status)}</b>${rules ? ` · ${escape(rules)}` : ''}</span>
    </div>
    <div class="hint">${bounty.submissions.length} submission(s)${bounty.winner ? ` · won by ${escape(short(bounty.winner, 12))}` : ''}</div>
    <footer>
      ${bounty.state === 'open' ? `<button data-act="submit" data-id="${bounty.id}">submit the proof in VERIFY</button>` : ''}
      ${bounty.state === 'open' && bounty.poster === state.address ? `<button class="ghost" data-act="cancel" data-id="${bounty.id}">cancel</button>` : ''}
    </footer>
  </div>`;
}

async function loadBounties() {
  const { bounties } = await api('/bounties');
  $('#bountyList').innerHTML = bounties.length ? bounties.map(bountyCard).join('')
    : '<div class="empty">no bounties — post one and the escrow makes it real</div>';
  setCounts(await api('/status'));
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
    <h4>${escape(method.method)} <span class="status ${method.available ? 'verified' : 'unverified'}">${method.available ? 'ready' : 'unavailable'}</span></h4>
    <div class="meta">${escape(method.runs_in)} · ${method.authoritative ? 'authoritative' : 'witness'}</div>
    <div class="desc">${escape(method.note || '')}</div>
    <div class="hint">verifies: ${(method.systems || []).join(', ') || '—'}</div>
    ${method.endpoint ? `<div class="hint">via ${escape(method.endpoint)}${method.chain_id ? ` (chain ${method.chain_id})` : ''}</div>` : ''}
  </div>`;
}

function systemCard(system) {
  return `<div class="card">
    <h4>${escape(system.label)}</h4>
    <div class="meta">${escape(system.family)}${system.curves.length ? ` · ${system.curves.join(', ')}` : ''} · ${system.zero_knowledge ? 'zero-knowledge' : 'NOT zero-knowledge'}</div>
    <div class="desc">“${escape(system.claims)}”</div>
    <div class="hint">setup: ${escape(system.trusted_setup || 'none')}</div>
    <div class="hint">proof: ${escape(system.proof_size)} · verifier: ${escape(system.verifier_cost)}</div>
    <div class="hint">checked here by: ${(system.available_here || system.methods).join(', ')}</div>
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
      <button class="ghost" id="keyShow">show the private key</button>
      <button class="ghost" id="keyCopy">copy it</button>
      <button class="ghost" id="keyForget">forget it</button>
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
  $('#keyForget').onclick = () => {
    if (!confirm('Forget this key? Anything it owns here becomes unreachable '
                 + 'unless you have a copy of it.')) return;
    forgetLocalKey(); setKind(''); signOut();
    toast('key forgotten');
  };
}

async function loadWallet() {
  if (!state.token) {
    $('#wallet').innerHTML = `<div class="empty">sign in to see credits, purchases and escrow
      <div class="row" style="justify-content:center;margin-top:14px">
        <button id="walletSignin">sign in</button></div>
      <div class="hint">a wallet, or anonymously with a key this tab makes for you</div></div>`;
    $('#walletSignin').onclick = openSignIn;
    return;
  }
  const me = await api('/me');
  $('#wallet').innerHTML = `
    <h3>${escape(me.address)}${me.owner ? ' — owner of this deployment' : ''}</h3>
    <div class="hint">signed in with ${kind() === 'local'
      ? 'a key made in this browser — the market cannot tell, and does not ask'
      : 'a wallet'}</div>
    ${keyPanel()}
    <div class="stats">
      <span><b>${me.credits}</b> credits</span>
      <span><b>${me.escrowed}</b> in escrow</span>
      <span><b>${me.earned}</b> earned</span>
      <span><b>${me.spent}</b> spent</span>
      ${me.in_debt ? '<span style="color:var(--invalid)"><b>in debt</b> — a proof you sold was refunded</span>' : ''}
    </div>
    ${me.owner ? `<div class="row"><input id="grantTo" placeholder="0x… address"><input id="grantAmt" type="number" value="100" style="max-width:110px"><button id="grantGo">grant credits</button></div>` : ''}
    <h4>purchases</h4>
    ${me.purchases.length ? me.purchases.map((p) => `<div class="hint">${escape(short(p.proof, 16))} · ${p.price} credits · sold as ${escape(p.sold_as)}${p.refunded ? ' · REFUNDED' : ''} <button class="ghost" data-act="refund" data-id="${p.proof}">refund if it stopped verifying</button></div>`).join('') : '<div class="hint">none yet</div>'}
    <h4>ledger</h4>
    ${me.history.slice().reverse().map((h) => `<div class="hint">${h.delta >= 0 ? '+' : ''}${h.delta} · ${escape(h.reason)} · ${ago(h.ts)}</div>`).join('') || '<div class="hint">nothing yet</div>'}`;
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
  $('#signin').textContent = address ? short(address, 8) + '…' : 'sign in';
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
  $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  $$('.tab').forEach((s) => s.classList.toggle('on', s.id === `tab-${name}`));
  location.hash = name;
  if (name === 'market') loadMarket().catch((e) => toast(e.message, true));
  if (name === 'bounties') loadBounties().catch((e) => toast(e.message, true));
  if (name === 'wallet') loadWallet().catch((e) => toast(e.message, true));
}

async function boot() {
  $$('#tabs button').forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
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
  api('/status').then(setCounts).catch(() => {});

  $('#methodPips').innerHTML = state.methods.map((m) =>
    `<span class="pip ${m.available ? 'up' : ''}" title="${escape(m.method)} — ${m.available ? 'ready' : 'unavailable'}"></span>`).join('');
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
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const { act, id } = button.dataset;
    try {
      if (act === 'open') await openProof(id, true);
      if (act === 'recheck') await signedRecheck(id);
      if (act === 'browser') await browserCheck(id);
      if (act === 'buy') { const receipt = await api(`/proofs/${id}/buy`, { method: 'POST' }); toast(`bought for ${receipt.charged} credits`); await loadMarket(); await openProof(id); }
      if (act === 'refund') { const receipt = await api(`/proofs/${id}/refund`, { method: 'POST' }); toast(`refunded ${receipt.refunded}`); await loadWallet(); }
      if (act === 'cancel') { await api(`/bounties/${id}/cancel`, { method: 'POST' }); toast('cancelled, escrow returned'); await loadBounties(); }
      if (act === 'submit') { state.bounty = id; showTab('verify'); toast('paste the proof, hit verify, then publish — submissions go to /bounties/' + id + '/submit'); }
    } catch (e) { toast(e.message, true); }
  });

  $$('input[type=file]').forEach((input) => {
    input.onchange = async () => {
      const file = input.files[0];
      if (file) $(`#${input.dataset.into}`).value = await file.text();
    };
  });
  $$('.drop').forEach((drop) => {
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
    drop.ondragleave = () => drop.classList.remove('over');
    drop.ondrop = async (e) => {
      e.preventDefault(); drop.classList.remove('over');
      const file = e.dataTransfer.files[0];
      if (file) $(`#${$('input[type=file]', drop).dataset.into}`).value = await file.text();
    };
  });

  $('#vRun').onclick = runVerify;
  $('#vBrowser').onclick = runBrowserVerify;
  $('#vSample').onclick = () => loadSample().catch((e) => toast(e.message, true));
  $('#vPublish').onclick = () => $('#publishDlg').showModal();
  $('#refresh').onclick = () => loadMarket();
  $('#q').onkeydown = (e) => { if (e.key === 'Enter') loadMarket(); };
  [$('#fSystem'), $('#fStatus'), $('#fSale')].forEach((el) => { el.onchange = loadMarket; });
  $$('button[data-prove]').forEach((b) => { b.onclick = () => prove(b.dataset.prove); });
  $('#publishOpen').onclick = () => {
    if (!state.lastVerified) { toast('verify something first — publish takes what is in VERIFY', true); showTab('verify'); return; }
    $('#publishDlg').showModal();
  };
  $('#bountyOpen').onclick = () => $('#bountyDlg').showModal();
  $('#bountySweep').onclick = async () => {
    const swept = await api('/bounties/sweep', { method: 'POST' });
    toast(`${swept.expired} expired bounties returned their escrow`);
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
        toast(answer.submission.accepted ? 'bounty won — reward released' : `not accepted: ${answer.submission.why}`);
        state.bounty = null;
      }
      showTab('market');
    } catch (e) { toast(e.message, true); }
  };

  $('#bGo').onclick = async () => {
    try {
      await api('/bounties', { method: 'POST', body: {
        title: $('#bTitle').value, system: $('#bSystem').value,
        reward: Number($('#bReward').value), vkey: parse($('#bVkey').value, {}),
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
