/* crates — the owned half of the crate: playlists you keep and links you hand out.
 *
 * The set list at the bottom right has always been the plan for tonight. This
 * file makes it something you can KEEP: name it, come back to it tomorrow, and
 * give someone a link that opens it in their own booth.
 *
 * Identity, in the order the console tries it:
 *   1. a mod-protocol token from a browser wallet — signed once, works in any
 *      browser you sign in from;
 *   2. a guest key — 64 random hex characters minted by the module and kept in
 *      localStorage. Nobody is asked to sign anything to save a playlist, which
 *      is the whole point: the wallet is an upgrade, not a toll gate.
 * Both ride on every request as headers (see `headers()` below), which is why
 * crate.js asks this file for them rather than the other way round.
 *
 * The open playlist auto-saves. `setChanged()` is called by app.js whenever the
 * set list is touched, and writes the whole track order back — a set list you
 * have to remember to save is a set list you lose.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});
const doc = root.document;

const GUEST_KEY = 'crates.guest';
const TOKEN_KEY = 'crates.token';
const OPEN_KEY = 'crates.open';

const store = {
  get(k) { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } },
  set(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) { /* quota */ } },
};

/* What every API call carries. An empty object until the user has done
 * something that needs an owner — reading the public directory or a shared
 * link is nobody's business but the reader's. */
function headers() {
  const h = {};
  const t = store.get(TOKEN_KEY);
  const g = store.get(GUEST_KEY);
  if (t) h['Authorization'] = 'Bearer ' + t;
  if (g) h['X-Crates-Guest'] = g;
  return h;
}

/* Mint a guest key the first time the user keeps anything. Silent on purpose:
 * "sign in to save" is the step that stops people saving. */
async function ensureIdentity() {
  if (store.get(TOKEN_KEY) || store.get(GUEST_KEY)) return true;
  const out = await M.api.call('guest_key');
  store.set(GUEST_KEY, out.guest);
  return true;
}

const api = {
  mine: () => M.api.call('playlists'),
  open: (id) => M.api.call('playlist_open', { id }),
  openShare: (share) => M.api.call('playlist_open', { share }),
  create: (name, note, tracks) => M.api.post('playlist_new', { name, note, tracks }),
  edit: (id, name, note) => M.api.post('playlist_edit', { id, name, note }),
  del: (id) => M.api.call('playlist_delete', { id }),
  add: (id, tracks) => M.api.post('playlist_add', { id, tracks }),
  replace: (id, tracks) => M.api.post('playlist_set', { id, tracks }),
  share: (id, on, listed) => M.api.call('playlist_share', { id, on, listed }),
  copy: (share, name) => M.api.post('playlist_copy', { share, name }),
  feed: (limit) => M.api.call('playlist_feed', { limit }),
  whoami: () => M.api.call('whoami'),
};

/* ── state ────────────────────────────────────────────────────────────── */

const pl = {
  host: null,          // what app.js lends us: the set list, and how to paint it
  items: [],           // my playlists, as cards
  open: null,          // the one the set list is currently editing
  view: 'mine',        // mine | shared
  feed: [],
  me: null,
  saving: 0,
  guestSet: false,
  visitor: null,       // a shared playlist opened from a ?p= link
};

function $(sel) { return doc.querySelector(sel); }
function icon(name) { return `<svg><use href="#i-${name}"/></svg>`; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function toast(msg) { if (pl.host && pl.host.toast) pl.host.toast(msg); }

/* ── mounting ─────────────────────────────────────────────────────────── */

async function mount(host) {
  pl.host = host;
  bind();
  // A ?p=sh_… link is somebody handing you their set. That comes first: the
  // visitor has not asked for their own library yet, they clicked a link.
  const share = new URLSearchParams(root.location.search).get('p');
  if (share) await openShared(share);
  await refresh().catch(() => {});
}

function bind() {
  $('#pl-new').addEventListener('click', newPlaylist);
  $('#pl-save-set').addEventListener('click', saveSetAsPlaylist);
  $('#pl-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') newPlaylist();
    if (e.key === 'Escape') closeNameBox();
  });
  for (const b of doc.querySelectorAll('#pl-views button')) {
    b.addEventListener('click', () => setView(b.dataset.view));
  }
}

function setView(view) {
  pl.view = view;
  for (const b of doc.querySelectorAll('#pl-views button')) {
    b.classList.toggle('on', b.dataset.view === view);
  }
  if (view === 'shared' && !pl.feed.length) loadFeed();
  paint();
}

/* ── my library ───────────────────────────────────────────────────────── */

async function refresh() {
  if (!store.get(TOKEN_KEY) && !store.get(GUEST_KEY)) { paint(); return; }
  try {
    const out = await api.mine();
    pl.items = out.items || [];
    pl.me = { id: out.owner, kind: out.kind };
    // Re-open whatever was open in this browser last time, so a reload lands
    // back in the same set rather than an empty one.
    const last = store.get(OPEN_KEY);
    if (!pl.open && last && pl.items.some(x => x.id === last)) await openPlaylist(last, true);
  } catch (e) {
    pl.items = [];
  }
  paint();
}

async function loadFeed() {
  try {
    const out = await api.feed(30);
    pl.feed = out.items || [];
  } catch (e) {
    pl.feed = [];
  }
  paint();
}

function openNameBox(placeholder, onSave) {
  const box = $('#pl-namebox');
  const input = $('#pl-name');
  box.hidden = false;
  input.placeholder = placeholder;
  input.value = '';
  input.focus();
  pl.nameSave = onSave;
}

function closeNameBox() {
  $('#pl-namebox').hidden = true;
  pl.nameSave = null;
}

/* NEW asks for a name and nothing else. A playlist with a note, a share link
 * and a cover is a playlist you never started. */
function newPlaylist() {
  const box = $('#pl-namebox');
  if (box.hidden) return openNameBox('name it — "Friday warmup"', create);
  const name = $('#pl-name').value.trim();
  if (!name) return closeNameBox();
  (pl.nameSave || create)(name);
  closeNameBox();
}

async function create(name, tracks) {
  await ensureIdentity();
  try {
    const doc_ = await api.create(name, '', tracks || []);
    pl.items.unshift(summary(doc_));
    pl.open = doc_;
    store.set(OPEN_KEY, doc_.id);
    if (tracks && tracks.length) toast(`saved ${tracks.length} tracks as “${doc_.name}”`);
    else toast(`“${doc_.name}” — add tracks and they save themselves`);
    paint();
    if (pl.host) pl.host.paintSet();
  } catch (e) {
    toast(e.message);
  }
}

/* SAVE THIS SET: the set list you have been building becomes a playlist, and
 * from then on it is the open one, so nothing has to be saved again. */
function saveSetAsPlaylist() {
  const set = pl.host ? pl.host.getSet() : [];
  if (!set.length) return toast('the set list is empty — add tracks first');
  if (pl.open) return toast(`already saving into “${pl.open.name}”`);
  openNameBox(`name for these ${set.length} tracks`, (name) => create(name, set));
}

function summary(d) {
  const tracks = d.tracks || [];
  return {
    id: d.id, name: d.name, note: d.note, count: tracks.length,
    duration_ms: tracks.reduce((a, t) => a + (t.duration_ms || 0), 0) || null,
    timed: tracks.filter(t => t.duration_ms).length,
    art: (tracks.find(t => t.art) || {}).art || null,
    shared: !!d.share_id, share_id: d.share_id, listed: !!d.listed,
    updated: d.updated, copied_from: d.copied_from,
  };
}

/* Opening a playlist points the set list at it. The set list IS the open
 * playlist from then on — one list on screen, not two that can disagree. */
async function openPlaylist(id, quiet) {
  try {
    const d = await api.open(id);
    pl.open = d;
    store.set(OPEN_KEY, id);
    if (pl.host) pl.host.loadSet(d.tracks || []);
    if (!quiet) toast(`“${d.name}” — ${(d.tracks || []).length} tracks in the set list`);
    paint();
  } catch (e) {
    toast(e.message);
    store.set(OPEN_KEY, '');
  }
}

function closePlaylist() {
  pl.open = null;
  store.set(OPEN_KEY, '');
  paint();
  if (pl.host) pl.host.paintSet();
}

async function rename(card) {
  openNameBox(`rename “${card.name}”`, async (name) => {
    try {
      const d = await api.edit(card.id, name, null);
      Object.assign(card, summary(d));
      if (pl.open && pl.open.id === card.id) pl.open = d;
      paint();
    } catch (e) { toast(e.message); }
  });
}

async function remove(card) {
  if (!root.confirm(`Delete “${card.name}”? The share link stops working too.`)) return;
  try {
    await api.del(card.id);
    pl.items = pl.items.filter(x => x.id !== card.id);
    if (pl.open && pl.open.id === card.id) closePlaylist();
    toast(`deleted “${card.name}”`);
    paint();
  } catch (e) { toast(e.message); }
}

/* ── sharing ──────────────────────────────────────────────────────────── */

async function share(card, listed) {
  try {
    const out = await api.share(card.id, true, listed === undefined ? card.listed : listed);
    Object.assign(card, { shared: true, share_id: out.share_id, listed: out.listed });
    if (pl.open && pl.open.id === card.id) {
      pl.open.share_id = out.share_id;
      pl.open.listed = out.listed;
    }
    paint();
    copyLink(out.url || shareUrl(out.share_id));
  } catch (e) { toast(e.message); }
}

async function unshare(card) {
  try {
    await api.share(card.id, false, false);
    Object.assign(card, { shared: false, share_id: null, listed: false });
    if (pl.open && pl.open.id === card.id) pl.open.share_id = null;
    toast('link revoked — it opens nothing now');
    paint();
  } catch (e) { toast(e.message); }
}

/* The link is built from where the console actually is, not from a configured
 * gateway: a console opened at localhost should hand out a localhost link. */
function shareUrl(shareId) {
  const base = new URL(doc.baseURI);
  return `${base.origin}${base.pathname.replace(/\/$/, '')}?p=${shareId}`;
}

function copyLink(url) {
  const done = () => toast('share link copied — paste it to anyone');
  if (root.navigator.clipboard && root.isSecureContext) {
    root.navigator.clipboard.writeText(url).then(done, () => prompt_(url));
  } else {
    prompt_(url);
  }
}

function prompt_(url) {
  // Clipboard access needs https; on a plain-http deployment show the link so
  // it can still be copied by hand rather than failing silently.
  root.prompt('Copy this link:', url);
}

/* ── someone else's playlist ──────────────────────────────────────────── */

async function openShared(shareId) {
  try {
    const d = await api.openShare(shareId);
    pl.visitor = d;
    paintVisitor();
    if (pl.host) pl.host.showTracks(d.name, d.tracks || [], 'shared with you');
  } catch (e) {
    pl.visitor = { error: e.message };
    paintVisitor();
  }
}

function paintVisitor() {
  const bar = $('#pl-visitor');
  if (!pl.visitor) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.textContent = '';
  if (pl.visitor.error) {
    bar.innerHTML = `<b>That link is dead.</b><span>${esc(pl.visitor.error)}</span>`;
    return;
  }
  const d = pl.visitor;
  const n = (d.tracks || []).length;
  const head = doc.createElement('span');
  head.className = 'v-who';
  head.innerHTML = `<b>${esc(d.name)}</b><span>shared with you · ${n} track${n === 1 ? '' : 's'}`
    + `${d.note ? ' · ' + esc(d.note) : ''}</span>`;
  const acts = doc.createElement('span');
  acts.className = 'v-acts';
  const open = doc.createElement('button');
  open.className = 'mini';
  open.innerHTML = icon('list') + 'SEE THE TRACKS';
  open.addEventListener('click', () => pl.host && pl.host.showTracks(d.name, d.tracks || [], 'shared with you'));
  const save = doc.createElement('button');
  save.className = 'mini go';
  save.innerHTML = icon('plus') + (d.mine ? 'ALREADY YOURS' : 'SAVE TO MY PLAYLISTS');
  save.disabled = !!d.mine;
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      await ensureIdentity();
      const copy = await api.copy(new URLSearchParams(root.location.search).get('p'), null);
      pl.items.unshift(summary(copy));
      toast(`“${copy.name}” is yours now — edit it freely`);
      pl.visitor = null;
      paintVisitor();
      paint();
    } catch (e) {
      toast(e.message);
      save.disabled = false;
    }
  });
  const x = doc.createElement('button');
  x.className = 'mini icon';
  x.innerHTML = icon('x');
  x.title = 'dismiss';
  x.addEventListener('click', () => { pl.visitor = null; paintVisitor(); });
  acts.append(open, save, x);
  bar.append(head, acts);
}

async function copyFeedItem(card, btn) {
  btn.disabled = true;
  try {
    await ensureIdentity();
    const copy = await api.copy(card.share_id, null);
    pl.items.unshift(summary(copy));
    toast(`“${copy.name}” copied into your playlists`);
    setView('mine');
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
  }
}

/* ── auto-save ────────────────────────────────────────────────────────── */

/* app.js calls this every time the set list changes. If a playlist is open,
 * the change is the playlist changing — write it back, coalescing the bursts
 * that come from dragging rows around. */
let saveTimer = null;
function setChanged(setlist) {
  if (!pl.open) { paint(); return; }
  pl.open.tracks = setlist.slice();
  const card = pl.items.find(x => x.id === pl.open.id);
  if (card) Object.assign(card, summary(pl.open));
  paint();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => flush(), 400);
}

async function flush() {
  if (!pl.open) return;
  const id = pl.open.id, tracks = (pl.open.tracks || []).slice();
  pl.saving++;
  paintStatus();
  try {
    await api.replace(id, tracks);
  } catch (e) {
    toast('could not save: ' + e.message);
  } finally {
    pl.saving--;
    paintStatus();
  }
}

function paintStatus() {
  const el = $('#pl-status');
  if (!el) return;
  if (!pl.open) { el.textContent = ''; return; }
  el.textContent = pl.saving ? 'saving…' : 'saved';
  el.classList.toggle('busy', !!pl.saving);
}

/* ── painting ─────────────────────────────────────────────────────────── */

function paint() {
  const list = $('#pl-list');
  if (!list) return;
  list.textContent = '';
  $('#pl-save-set').hidden = !!pl.open;
  $('#pl-open').textContent = '';

  if (pl.open) {
    const head = doc.createElement('div');
    head.className = 'pl-open';
    const who = doc.createElement('span');
    who.className = 'who';
    who.innerHTML = `<b>${esc(pl.open.name)}</b>`
      + `<span>the set list is saving into this one <i id="pl-status"></i></span>`;
    const acts = doc.createElement('span');
    acts.className = 'to';
    const card = pl.items.find(x => x.id === pl.open.id) || summary(pl.open);
    acts.append(
      btn(card.shared ? icon('link') + 'LINK' : icon('link') + 'SHARE', 'mini' + (card.shared ? ' on' : ''),
        card.shared ? `copy the link (${card.listed ? 'in the public list' : 'unlisted'})` : 'make a link anyone can open',
        () => card.shared ? copyLink(shareUrl(card.share_id)) : share(card)),
      btn(icon('x'), 'mini icon', 'close it — the set list stays as it is', closePlaylist),
    );
    head.append(who, acts);
    $('#pl-open').append(head);
    paintStatus();
  }

  const cards = pl.view === 'mine' ? pl.items : pl.feed;
  if (!cards.length) {
    const li = doc.createElement('li');
    li.className = 'state';
    li.textContent = pl.view === 'mine'
      ? 'no playlists yet — build a set list, then press SAVE THIS SET'
      : 'nobody has shared a playlist here yet';
    list.append(li);
    return;
  }
  for (const card of cards) list.append(pl.view === 'mine' ? mineRow(card) : feedRow(card));
}

function btn(html, cls, title, fn) {
  const b = doc.createElement('button');
  b.innerHTML = html;
  b.className = cls || '';
  if (title) b.title = title;
  b.addEventListener('click', (e) => { e.stopPropagation(); fn(e); });
  return b;
}

function cardSub(card) {
  // "6 min+" when some tracks never told us how long they are (Bandcamp
  // search rows do not), so a short-looking number is never a lie.
  const partial = card.timed !== undefined && card.timed < card.count;
  const mins = card.duration_ms ? runtime(card.duration_ms) + (partial ? '+' : '') : null;
  return [`${card.count} track${card.count === 1 ? '' : 's'}`, mins,
    card.shared ? (card.listed ? 'public' : 'link shared') : null,
    card.copied_from ? 'copied' : null].filter(Boolean).join(' · ');
}

/* A playlist's running time reads as "3h 06m" past the hour: "186 min" is a
 * number you have to do arithmetic on to know if it fills a set. */
function runtime(ms) {
  const m = Math.round(ms / 60000);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m} min`;
}

function mineRow(card) {
  const li = doc.createElement('li');
  li.className = 'pl-row' + (pl.open && pl.open.id === card.id ? ' on' : '');
  if (card.art) {
    const img = doc.createElement('img');
    img.src = card.art; img.alt = ''; img.loading = 'lazy';
    li.append(img);
  } else {
    const s = doc.createElement('span');
    s.className = 'noart';
    s.innerHTML = icon('list');
    li.append(s);
  }
  const who = doc.createElement('span');
  who.className = 'who';
  const b = doc.createElement('b');
  b.textContent = card.name;
  b.title = card.name;
  const sub = doc.createElement('span');
  sub.textContent = cardSub(card);
  who.append(b, sub);
  const to = doc.createElement('span');
  to.className = 'to';
  to.append(
    btn(icon('link'), 'mini icon' + (card.shared ? ' on' : ''),
      card.shared ? 'copy the share link' : 'share it',
      () => card.shared ? copyLink(shareUrl(card.share_id)) : share(card)),
    btn(icon('keys'), 'mini icon', 'rename', () => rename(card)),
    btn(icon('x'), 'mini icon', 'delete', () => remove(card)),
  );
  li.append(who, to);
  li.addEventListener('click', () => openPlaylist(card.id));
  return li;
}

function feedRow(card) {
  const li = doc.createElement('li');
  li.className = 'pl-row';
  const s = doc.createElement('span');
  s.className = 'noart';
  s.innerHTML = icon('disc');
  li.append(card.art ? Object.assign(doc.createElement('img'), { src: card.art, alt: '', loading: 'lazy' }) : s);
  const who = doc.createElement('span');
  who.className = 'who';
  const b = doc.createElement('b');
  b.textContent = card.name;
  const sub = doc.createElement('span');
  sub.textContent = [cardSub(card), (card.sources || []).join(', ')].filter(Boolean).join(' · ');
  who.append(b, sub);
  const to = doc.createElement('span');
  to.className = 'to';
  to.append(
    btn('OPEN', 'mini', 'see its tracks', async () => {
      try {
        const d = await api.openShare(card.share_id);
        if (pl.host) pl.host.showTracks(d.name, d.tracks || [], 'shared playlist');
      } catch (e) { toast(e.message); }
    }),
    btn(icon('plus'), 'mini icon', 'copy it into my playlists', (e) => copyFeedItem(card, e.currentTarget)),
  );
  li.append(who, to);
  return li;
}

/* ── the account card in the drawer ───────────────────────────────────── */

/* Shown next to the platforms: who you are here, and the one upgrade path.
 * A wallet is optional and says so — this console is usable without one. */
async function accountCard() {
  let me = null;
  try { me = await api.whoami(); } catch (e) { me = { anon: true, error: e.message }; }
  pl.me = me;
  const guest = store.get(GUEST_KEY);
  const wallet = me && me.kind === 'wallet';
  const wrap = doc.createElement('div');
  wrap.className = 'acct';
  wrap.innerHTML = `
    <h4>YOUR PLAYLISTS</h4>
    <p class="acct-who">${wallet
      ? `Signed in with a wallet — <code>${esc((me.address || '').slice(0, 10))}…</code>. Your playlists follow this address anywhere.`
      : guest
        ? 'Saved in this browser under a private key. Nobody had to sign anything.'
        : 'Nothing saved yet. A key is made for you the first time you keep a playlist.'}</p>`;
  const acts = doc.createElement('div');
  acts.className = 'acct-acts';
  if (!wallet && root.ethereum) {
    acts.append(btn('CONNECT WALLET', 'mini go', 'sign once — playlists follow the address', signIn));
  }
  if (guest) {
    acts.append(btn('COPY MY KEY', 'mini', 'back it up — it is the only way back to these playlists',
      () => copyLink(guest)));
    acts.append(btn('USE ANOTHER KEY', 'mini', 'paste a key from another browser', () => {
      const k = root.prompt('Paste the key you copied from your other browser:');
      if (k && k.trim().length >= 16) { store.set(GUEST_KEY, k.trim()); refresh(); toast('key swapped'); }
    }));
  }
  wrap.append(acts);
  return wrap;
}

/* The wallet path: personal_sign over exactly {data, time} — the protocol's
 * own token shape. No nonce, no challenge endpoint, no gas. */
async function signIn() {
  try {
    const [address] = await root.ethereum.request({ method: 'eth_requestAccounts' });
    const data = { mod: 'crates' };
    const time = (Date.now() / 1000).toString();
    const signature = await root.ethereum.request({
      method: 'personal_sign', params: [JSON.stringify({ data, time }), address],
    });
    const token = b64url(JSON.stringify({ data, time, key: address, signature }));
    store.set(TOKEN_KEY, token);
    const me = await api.whoami();
    if (me.error) throw new Error(me.error);
    toast('signed in — your playlists follow this wallet now');
    await refresh();
  } catch (e) {
    store.set(TOKEN_KEY, '');
    toast('wallet sign-in failed: ' + (e && e.message ? e.message : e));
  }
}

function b64url(s) {
  return btoa(unescape(encodeURIComponent(s)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

M.playlists = { mount, setChanged, refresh, headers, accountCard, openShared,
                get open() { return pl.open; } };

})(typeof globalThis !== 'undefined' ? globalThis : this);
