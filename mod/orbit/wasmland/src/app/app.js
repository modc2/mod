// The wasmland console.
//
// Two things here are worth knowing before reading the rest:
//
// 1. The browser is a real venue, not a preview. `runHere()` imports the same
//    runtime the server runner imports — off /runtime/*.mjs, in a Worker — so
//    a run in this tab is the same computation the server would perform, and
//    can be checked by asking the server to replay it.
//
// 2. A run performed here is posted as a *claim*. Nothing in this file can
//    make something verified; only an independent replay can, which is why the
//    verify button asks the server rather than doing anything locally.

const API = '_api';                       // rewritten server-side to the API
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of [].concat(kids)) node.append(kid);
  return node;
};

const state = {
  view: 'market',
  address: localStorage.getItem('wl.address') || null,
  token: localStorage.getItem('wl.token') || null,
  venue: localStorage.getItem('wl.venue') || 'server',
  engines: [],
  venues: null,
};

// ── plumbing ────────────────────────────────────────────────────

async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {};
  if (state.token) headers.authorization = state.token;
  if (body) headers['content-type'] = 'application/json';
  const res = await fetch(`${API}${path}`, {
    method, headers,
    body: form ? form : body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || data.error || `${res.status}`);
  return data;
}

let toastTimer;
function toast(message, bad = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('bad', !!bad);
  node.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('on'), 4200);
}

const short = (h, n = 10) => (h || '').slice(0, n);
const shortAddr = (a) => !a ? '—' : a.startsWith('0x') ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;

function verdictTag(v) {
  const status = (v && v.status) || 'unverified';
  const cls = { verified: 'acc', disputed: 'bad', claimed: 'claim' }[status] || '';
  return el('span', { className: `tag ${cls}`, textContent: status, title: (v && v.why) || '' });
}

// ── the browser venue ───────────────────────────────────────────

async function runHere({ artifact, engine, entry, input, seed, listing, limits }) {
  const query = listing ? `?listing=${encodeURIComponent(listing)}` : '';
  const res = await fetch(`${API}/artifacts/${artifact}/raw${query}`,
    state.token ? { headers: { authorization: state.token } } : {});
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`cannot fetch the bytes: ${detail.slice(0, 200)}`);
  }
  const bytes = new Uint8Array(await res.arrayBuffer());

  // Served by the API, not from the app directory — the worker imports
  // ./engines.mjs and ./host.mjs beside it, which is what makes this tab and
  // the server the same execution layer rather than two of them.
  //
  // Resolved against document.baseURI, not location.href: the page pins a
  // <base> of /wasmland/ and location.href is /wasmland, so location-relative
  // resolution drops a path segment and the Worker 404s — which surfaces as an
  // error event with no message at all.
  const url = new URL(`${API}/runtime/worker.mjs`, document.baseURI).href;
  const w = new Worker(url, { type: 'module' });
  const timeout = (limits && limits.ms) || 20000;
  const result = await new Promise((resolve, reject) => {
    // Wasm cannot be interrupted from outside, so the tab's only real timeout
    // is killing the thread — the same thing the server does with the process.
    const timer = setTimeout(() => { w.terminate(); reject(new Error(`timed out after ${timeout}ms — worker terminated`)); }, timeout);
    w.onmessage = (e) => { clearTimeout(timer); w.terminate(); resolve(e.data); };
    w.onerror = (e) => { clearTimeout(timer); w.terminate(); reject(new Error(e.message || 'worker failed')); };
    w.postMessage({ engine, artifact: bytes, entry, input, seed });
  });
  if (!result.ok) throw new Error(result.error || 'run failed');

  return api('/runs/claim', {
    method: 'POST',
    body: { artifact, engine, entry, input, seed, result, listing: listing || null },
  });
}

// ── sign-in ─────────────────────────────────────────────────────

async function signIn() {
  if (!window.ethereum) {
    toast('No wallet in this browser — the CLI signs with `m wasmland/token`', true);
    return;
  }
  try {
    const [address] = await window.ethereum.request({ method: 'eth_requestAccounts' });
    const challenge = await api('/auth/challenge', { method: 'POST', body: { address } });
    const signature = await window.ethereum.request({
      method: 'personal_sign', params: [challenge.message, address],
    });
    const session = await api('/auth/verify', {
      method: 'POST', body: { address, message: challenge.message, signature },
    });
    state.address = session.address;
    state.token = session.token;
    localStorage.setItem('wl.address', session.address);
    localStorage.setItem('wl.token', session.token);
    paintChrome();
    toast(`signed in as ${shortAddr(session.address)}`);
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

function signOut() {
  state.address = state.token = null;
  localStorage.removeItem('wl.address');
  localStorage.removeItem('wl.token');
  paintChrome();
  render();
}

// ── views ───────────────────────────────────────────────────────

const views = {};

views.market = async (root) => {
  const { listings } = await api('/listings?limit=100');
  root.append(el('h2', { textContent: 'Market' }),
    el('p', { className: 'sub', textContent: 'Everything published here. Run one in your tab or on the box, then have the other side check it.' }));
  if (!listings.length) {
    root.append(el('div', { className: 'empty', textContent: 'Nothing listed yet — Publish is the tab above.' }));
    return;
  }
  const grid = el('div', { className: 'grid' });
  for (const item of listings) {
    const card = el('div', { className: 'card click' });
    card.append(
      el('h4', {}, [item.title, el('span', { className: 'tag', textContent: item.engine })]),
      el('p', { textContent: item.description || 'No description.' }),
      el('div', { className: 'meta' }, [
        el('span', { className: `tag ${item.price ? 'warn' : 'acc'}`, textContent: item.price ? `${item.price} credits` : 'free' }),
        el('span', { className: 'tag', textContent: item.role }),
        ...(item.verified_runs ? [el('span', { className: 'tag acc', textContent: `${item.verified_runs} verified` })] : []),
        el('span', { className: 'hash', textContent: short(item.artifact, 12) }),
      ]));
    card.onclick = () => openListing(item.id);
    grid.append(card);
  }
  root.append(grid);
};

async function openListing(id) {
  const item = await api(`/listings/${id}`);
  const root = $('#view');
  root.replaceChildren();
  const wrap = el('div', { className: 'wrap' });
  wrap.append(
    el('button', { className: 'btn small', textContent: '← market', onclick: () => go('market') }),
    el('h2', { textContent: item.title, style: 'margin-top:14px' }),
    el('p', { className: 'sub', textContent: item.description || '' }),
    el('div', { className: 'meta' }, [
      el('span', { className: 'tag', textContent: item.engine }),
      el('span', { className: 'tag', textContent: `entry ${item.entry}` }),
      el('span', { className: `tag ${item.price ? 'warn' : 'acc'}`, textContent: item.price ? `${item.price} credits` : 'free' }),
      el('span', { className: 'tag', textContent: `by ${shortAddr(item.seller)}` }),
      el('span', { className: 'hash', textContent: item.artifact }),
    ]));

  const input = el('textarea', { placeholder: 'input passed to the entry point', value: '' });
  const seed = el('input', { type: 'number', value: '1' });
  const venue = el('select');
  for (const v of (item.venues || ['server'])) venue.append(el('option', { value: v, textContent: v }));
  venue.value = item.venues && item.venues.includes(state.venue) ? state.venue : (item.venues || ['server'])[0];

  wrap.append(el('label', { textContent: 'input' }), input,
    el('div', { className: 'row' }, [
      el('div', {}, [el('label', { textContent: 'seed' }), seed]),
      el('div', {}, [el('label', { textContent: 'venue' }), venue]),
    ]));

  const out = el('div');
  const runBtn = el('button', { className: 'btn primary small', textContent: 'Run' });
  runBtn.onclick = async () => {
    runBtn.disabled = true;
    runBtn.textContent = 'running…';
    try {
      const job = {
        artifact: item.artifact, engine: item.engine, entry: item.entry,
        input: input.value, seed: Number(seed.value) || 0, listing: item.id,
      };
      const run = venue.value === 'browser'
        ? await runHere(job)
        : await api('/run', { method: 'POST', body: { listing: item.id, entry: item.entry, input: input.value, seed: Number(seed.value) || 0 } });
      state.venue = venue.value;
      localStorage.setItem('wl.venue', venue.value);
      out.replaceChildren(runCard(run));
      toast(`ran in ${run.ms ?? '?'}ms — ${run.verdict.status}`);
    } catch (e) {
      toast(e.message, true);
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = 'Run';
    }
  };

  const buttons = el('div', { className: 'btns' }, [runBtn]);
  if (item.price) {
    buttons.append(el('button', {
      className: 'btn small', textContent: `Buy for ${item.price}`,
      onclick: async () => {
        try { const r = await api(`/listings/${item.id}/buy`, { method: 'POST' }); toast(r.note || `charged ${r.charged}`); }
        catch (e) { toast(e.message, true); }
      },
    }));
  }
  if (item.role === 'game') {
    buttons.append(el('button', {
      className: 'btn small', textContent: 'Send to arena',
      title: 'Publish this game as its own mod so agents can play it',
      onclick: async () => {
        try { const r = await api(`/listings/${item.id}/arena`, { method: 'POST' }); toast(`published as the mod "${r.name}"`); }
        catch (e) { toast(e.message, true); }
      },
    }));
  }
  wrap.append(buttons, out);

  const { runs } = await api(`/runs?listing=${item.id}&limit=10`);
  if (runs.length) {
    wrap.append(el('h3', { textContent: 'Runs' }));
    for (const run of runs) wrap.append(runCard(run));
  }
  root.append(wrap);
}

function runCard(run) {
  const card = el('div', { className: 'card', style: 'margin-bottom:10px' });
  card.append(el('div', { className: 'meta' }, [
    verdictTag(run.verdict),
    el('span', { className: 'tag', textContent: run.venue }),
    el('span', { className: 'tag', textContent: `seed ${run.seed}` }),
    el('span', { className: 'tag', textContent: `${run.ms ?? '?'}ms` }),
    el('span', { className: 'hash', textContent: `receipt ${short(run.receipt, 16)}` }),
  ]));
  if (run.output) card.append(el('pre', { textContent: run.output.slice(0, 4000) }));
  if (run.logs && run.logs.length) card.append(el('pre', { textContent: run.logs.join('\n').slice(0, 2000) }));
  if (run.verdict && run.verdict.why) card.append(el('p', { className: 'sub', style: 'margin:8px 0 0', textContent: run.verdict.why }));

  const verify = el('button', { className: 'btn small', textContent: 'Verify by replay' });
  verify.onclick = async () => {
    verify.disabled = true; verify.textContent = 'replaying…';
    try {
      const out = await api(`/runs/${run.id}/verify`, { method: 'POST' });
      card.replaceWith(runCard(out));
      toast(out.verdict.status === 'verified' ? 'replay agrees — verified'
        : out.verdict.status === 'disputed' ? 'replay disagrees — disputed' : out.verdict.why, out.verdict.status === 'disputed');
    } catch (e) { toast(e.message, true); verify.disabled = false; verify.textContent = 'Verify by replay'; }
  };
  card.append(el('div', { className: 'btns' }, [verify]));
  return card;
}

views.upload = async (root) => {
  root.append(el('h2', { textContent: 'Publish' }),
    el('p', { className: 'sub', textContent: 'Upload something that computes. The registry reads the binary rather than taking your word for what it is.' }));

  const file = el('input', { type: 'file' });
  const title = el('input', { placeholder: 'name it' });
  const description = el('input', { placeholder: 'what does it do?' });
  const price = el('input', { type: 'number', value: '0', min: '0', step: '0.5' });
  const entry = el('input', { placeholder: 'entry point (default: run)' });
  const tags = el('input', { placeholder: 'tags, comma separated' });
  const report = el('div');

  file.onchange = async () => {
    const f = file.files[0];
    if (!f) return;
    const b64 = await toB64(f);
    try {
      const manifest = await api('/inspect', { method: 'POST', body: { b64, filename: f.name } });
      report.replaceChildren(el('div', { className: 'card' }, [
        el('div', { className: 'meta' }, [
          el('span', { className: 'tag acc', textContent: manifest.engine }),
          el('span', { className: 'tag', textContent: manifest.role }),
          el('span', { className: 'tag', textContent: `${manifest.bytes} bytes` }),
        ]),
        el('p', { className: 'sub', style: 'margin:10px 0 0', textContent: `exports: ${(manifest.entries || []).join(', ') || 'none'}` }),
        manifest.role === 'game'
          ? el('p', { className: 'sub', style: 'margin:4px 0 0', textContent: 'This implements the game ABI — you can send it to the arena after publishing.' })
          : el('span', {}),
      ]));
      if (!title.value) title.value = f.name.replace(/\.(wasm|js|mjs)$/, '');
      if (!entry.value && manifest.entries && manifest.entries.includes('run')) entry.value = 'run';
    } catch (e) { report.replaceChildren(el('div', { className: 'card' }, [el('p', { className: 'sub', textContent: e.message })])); }
  };

  const submit = el('button', { className: 'btn primary', textContent: 'Publish' });
  submit.onclick = async () => {
    const f = file.files[0];
    if (!f) return toast('pick a file first', true);
    submit.disabled = true;
    try {
      const form = new FormData();
      form.append('file', f);
      form.append('filename', f.name);
      const artifact = await api('/artifacts', { method: 'POST', form });
      const listing = await api('/listings', {
        method: 'POST',
        body: {
          artifact: artifact.id, title: title.value || f.name,
          description: description.value, entry: entry.value,
          price: Number(price.value) || 0,
          tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        },
      });
      toast(`published ${listing.title}`);
      openListing(listing.id);
    } catch (e) { toast(e.message, true); } finally { submit.disabled = false; }
  };

  root.append(
    el('label', { textContent: 'artifact' }), file, report,
    el('label', { textContent: 'title' }), title,
    el('label', { textContent: 'description' }), description,
    el('div', { className: 'row' }, [
      el('div', {}, [el('label', { textContent: 'entry point' }), entry]),
      el('div', {}, [el('label', { textContent: 'price per buyer (credits)' }), price]),
    ]),
    el('label', { textContent: 'tags' }), tags,
    el('div', { className: 'btns' }, [submit]));
};

function toB64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

views.runs = async (root) => {
  const { runs } = await api('/runs?limit=40');
  root.append(el('h2', { textContent: 'Runs' }),
    el('p', { className: 'sub', textContent: 'Every recorded execution. A run is claimed until somebody who did not perform it gets the same answer.' }));
  if (!runs.length) return root.append(el('div', { className: 'empty', textContent: 'No runs yet.' }));
  for (const run of runs) root.append(runCard(run));
};

views.engines = async (root) => {
  const [{ engines: list, note }, venues] = await Promise.all([api('/engines'), api('/venues')]);
  root.append(el('h2', { textContent: 'Compute types' }),
    el('p', { className: 'sub', textContent: note }));
  const grid = el('div', { className: 'grid' });
  for (const e of list) {
    grid.append(el('div', { className: 'card' }, [
      el('h4', {}, [e.name, el('span', { className: `tag ${e.status === 'live' ? 'acc' : 'plan'}`, textContent: e.status })]),
      el('p', { textContent: e.summary }),
      el('div', { className: 'meta' }, [
        el('span', { className: 'tag', textContent: `venues: ${e.venues.join(', ') || 'none'}` }),
        el('span', { className: 'tag', textContent: `verify: ${e.verify}` }),
        el('span', { className: 'tag', textContent: `determinism: ${e.determinism}` }),
      ]),
      e.needs ? el('p', { className: 'sub', style: 'margin:10px 0 0', textContent: `needs: ${e.needs}` }) : el('span', {}),
    ]));
  }
  root.append(grid, el('h3', { textContent: 'Venues' }),
    el('pre', { textContent: JSON.stringify(venues, null, 2) }));
};

views.games = async (root) => {
  const { arena, games: list } = await api('/games');
  root.append(el('h2', { textContent: 'Arena' }),
    el('p', { className: 'sub', textContent: 'A game published here becomes its own mod, stored in the store mod, and the arena seats agents against it.' }));
  root.append(el('div', { className: 'card' }, [
    el('div', { className: 'meta' }, [
      el('span', { className: `tag ${arena.arena ? 'acc' : 'bad'}`, textContent: arena.arena ? 'arena installed' : 'no arena' }),
      el('span', { className: `tag ${arena.server_up ? 'acc' : 'warn'}`, textContent: arena.server_up ? 'server up' : 'server stopped' }),
    ]),
    el('p', { className: 'sub', style: 'margin:10px 0 0', textContent: arena.note || arena.why || 'ready' }),
  ]));
  if (!list.length) return root.append(el('div', { className: 'empty', textContent: 'No games published from here yet — upload a module exporting the game ABI.' }));
  const grid = el('div', { className: 'grid' });
  for (const g of list) {
    grid.append(el('div', { className: 'card' }, [
      el('h4', {}, [g.id, el('span', { className: 'tag acc', textContent: 'mod' })]),
      el('p', { textContent: (g.card && g.card.description) || '' }),
      el('div', { className: 'meta' }, [
        el('span', { className: 'tag', textContent: `m ${g.id}` }),
        el('span', { className: 'hash', textContent: short(g.artifact, 12) }),
      ]),
    ]));
  }
  root.append(grid);
};

views.account = async (root) => {
  root.append(el('h2', { textContent: 'Account' }));
  if (!state.address) {
    root.append(el('p', { className: 'sub', textContent: 'Sign in with a wallet to publish, buy, and be credited for what you sell.' }),
      el('div', { className: 'btns' }, [el('button', { className: 'btn primary small', textContent: 'Sign in', onclick: signIn })]));
    return;
  }
  const me = await api('/auth/me');
  const acct = me.account || {};
  root.append(el('p', { className: 'sub', textContent: state.address }),
    el('div', { className: 'grid' }, [
      el('div', { className: 'card' }, [el('h4', { textContent: 'Credits' }), el('p', { textContent: String(acct.credits ?? 0) })]),
      el('div', { className: 'card' }, [el('h4', { textContent: 'Earned' }), el('p', { textContent: String(acct.earned ?? 0) })]),
      el('div', { className: 'card' }, [el('h4', { textContent: 'Spent' }), el('p', { textContent: String(acct.spent ?? 0) })]),
    ]));
  const mine = await api(`/listings?seller=${state.address}`);
  if (mine.listings.length) {
    root.append(el('h3', { textContent: 'Published by you' }));
    const table = el('table');
    table.append(el('tr', {}, [el('th', { textContent: 'listing' }), el('th', { textContent: 'runs' }), el('th', { textContent: 'verified' }), el('th', { textContent: 'earned' })]));
    for (const item of mine.listings) {
      table.append(el('tr', {}, [
        el('td', {}, [el('a', { href: '#', textContent: item.title, onclick: (e) => { e.preventDefault(); openListing(item.id); } })]),
        el('td', { className: 'mono', textContent: String(item.runs || 0) }),
        el('td', { className: 'mono', textContent: String(item.verified_runs || 0) }),
        el('td', { className: 'mono', textContent: String(item.earned || 0) }),
      ]));
    }
    root.append(table);
  }
  root.append(el('div', { className: 'btns' }, [el('button', { className: 'btn small', textContent: 'Sign out', onclick: signOut })]));
};

// ── chrome ──────────────────────────────────────────────────────

function paintChrome() {
  $('#signin').textContent = state.address ? shortAddr(state.address) : 'Sign in';
  $('#signin').onclick = state.address ? () => go('account') : signIn;
}

async function paintVenues() {
  try {
    const v = await api('/venues');
    state.venues = v;
    const server = v.server;
    $('#chain').textContent = server.ok ? 'browser + server' : 'browser only';
    $('#chain').title = server.ok
      ? `server venue: node ${server.node}, network isolated: ${server.network_isolated}`
      : 'this box has no node — runs happen in your tab';
    $('#venue-note').textContent = [
      `venue  browser + ${server.ok ? 'server' : '—'}`,
      `netns  ${server.network_isolated ? 'isolated' : 'shared'}`,
      `user   ${server.drops_privileges ? server.sandbox_user : 'unconfined'}`,
    ].join('\n');
  } catch { /* the API is the thing that's down; the views will say so */ }
}

function go(view) {
  state.view = view;
  for (const button of document.querySelectorAll('.nav')) {
    button.setAttribute('aria-current', String(button.dataset.view === view));
  }
  if (window.matchMedia('(max-width: 720px)').matches) document.body.classList.add('rail-closed');
  render();
}

async function render() {
  const host = $('#view');
  host.replaceChildren();
  const root = el('div', { className: 'wrap' });
  host.append(root);
  try {
    await views[state.view](root);
  } catch (e) {
    root.append(el('div', { className: 'empty', textContent: e.message }));
  }
}

// The logo is the sidebar toggle — the one control that never moves.
$('#logo').onclick = () => document.body.classList.toggle('rail-closed');
for (const button of document.querySelectorAll('.nav')) {
  button.onclick = () => go(button.dataset.view);
}
if (window.matchMedia('(max-width: 720px)').matches) document.body.classList.add('rail-closed');

paintChrome();
paintVenues();
render();
