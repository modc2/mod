// artlist console — plain ES module, no build step.
// Talks to its own origin (base href=/artlist/): music?q=…, sfx?q=…, stream?u=…

const $ = (s) => document.querySelector(s);
const KINDS = {
  music:     { ph: 'search music…',     sorts: [['1', 'relevance'], ['3', 'newest'], ['2', 'popular']], // songSortType: 3 returns the newest ids, 2 old favourites (inferred live 2026-10-14)
               chips: ['epic cinematic', 'lofi study', 'upbeat indie', 'ambient piano', 'hip hop beat', 'acoustic folk'] },
  sfx:       { ph: 'search sound effects…', sorts: [['NEWEST', 'newest'], ['TOP_DOWNLOADS', 'top downloads'], ['STAFF_PICKS', 'staff picks']],
               chips: ['whoosh', 'click', 'rain', 'explosion', 'footsteps', 'notification'] },
  footage:   { ph: 'search stock footage…', sorts: [],
               chips: ['ocean drone', 'city night', 'forest', 'coffee', 'people walking', 'clouds timelapse'] },
  templates: { ph: 'search video templates…', sorts: [['TOP_DOWNLOADS', 'top downloads'], ['NEWEST', 'newest'], ['STAFF_PICKS', 'staff picks']],
               chips: ['intro', 'lower thirds', 'logo reveal', 'slideshow', 'titles', 'transitions'] },
  voices:    { ph: 'voices are listed, no search needed', sorts: [], chips: [] },
};

const state = { kind: 'music', q: '', sort: '', page: 1, items: [], total: null, busy: false };
const player = { list: [], idx: -1 };

// ── icons (inline SVG — the host has no emoji font) ──────────────
const svg = (d, vb = '0 0 16 16') =>
  `<svg viewBox="${vb}" width="14" height="14" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${d}</svg>`;
const ICON = {
  play:  svg('<path d="M4 2h2v2h2v2h2v2h2v2h-2v2H8v2H6v2H4z"/>'),
  pause: svg('<path d="M3 2h4v12H3zM9 2h4v12H9z"/>'),
  clip:  svg('<path d="M1 3h10v10H1zM11 6l4-2v8l-4-2z"/>'),
  tpl:   svg('<path d="M1 2h14v3H1zM1 6h6v8H1zM8 6h7v8H8z"/>'),
  mic:   svg('<path d="M6 1h4v8H6zM4 6h1v3h6V6h1v4H9v2h3v2H4v-2h3v-2H4z"/>'),
};

// ── theme ────────────────────────────────────────────────────────
const themeBtn = $('#theme');
function paintTheme() {
  const dark = document.documentElement.dataset.theme !== 'light';
  themeBtn.textContent = dark ? 'light' : 'dark';
}
themeBtn.onclick = () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('artlist.theme', next); } catch (e) {}
  paintTheme();
};
paintTheme();

// ── api ──────────────────────────────────────────────────────────
async function api(path, params) {
  const u = new URL(path, document.baseURI);
  for (const [k, v] of Object.entries(params || {})) if (v !== '' && v != null) u.searchParams.set(k, v);
  const r = await fetch(u);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
const streamUrl = (u) => `stream?u=${encodeURIComponent(u)}`;

async function health() {
  const el = $('#status');
  try {
    const h = await api('health');
    el.innerHTML = `<b class="dot ok"></b> live · ${h.cache.entries} cached`;
  } catch (e) {
    el.innerHTML = `<b class="dot bad"></b> api down`;
  }
}

// ── search ───────────────────────────────────────────────────────
const tabs = $('#tabs'), form = $('#form'), qEl = $('#q'), sortEl = $('#sort');
const results = $('#results'), meta = $('#meta'), more = $('#more'), chips = $('#chips');

function setKind(kind, { keepQuery = true } = {}) {
  state.kind = kind;
  for (const b of tabs.children) b.classList.toggle('on', b.dataset.kind === kind);
  const k = KINDS[kind];
  qEl.placeholder = k.ph;
  qEl.disabled = kind === 'voices';
  $('#go').classList.toggle('hidden', kind === 'voices');
  sortEl.innerHTML = k.sorts.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
  sortEl.classList.toggle('hidden', !k.sorts.length);
  state.sort = k.sorts[0]?.[0] || '';
  chips.innerHTML = k.chips.map((c) => `<button class="chip" type="button" data-q="${c}">${c}</button>`).join('');
  if (!keepQuery) qEl.value = '';
}

tabs.onclick = (e) => {
  const b = e.target.closest('button[data-kind]');
  if (!b) return;
  setKind(b.dataset.kind);
  if (b.dataset.kind === 'voices' || qEl.value.trim()) search();
  else { results.innerHTML = ''; meta.textContent = ''; more.classList.add('hidden'); }
  pushHash();
};
chips.onclick = (e) => {
  const c = e.target.closest('.chip');
  if (!c) return;
  qEl.value = c.dataset.q;
  search();
};
form.onsubmit = (e) => { e.preventDefault(); search(); };
sortEl.onchange = () => { state.sort = sortEl.value; if (state.items.length || state.kind === 'voices') search(); };
more.onclick = () => search({ append: true });

function pushHash() {
  const h = state.kind + (state.q ? '/' + encodeURIComponent(state.q) : '');
  if (location.hash.slice(1) !== h) history.replaceState(null, '', '#' + h);
}

async function search({ append = false } = {}) {
  if (state.busy) return;
  if (!append) { state.page = 1; state.items = []; state.total = null; }
  else state.page += 1;
  state.q = qEl.value.trim();
  state.busy = true;
  more.disabled = true;
  pushHash();
  if (!append) { results.innerHTML = skeleton(); meta.textContent = 'searching…'; }
  try {
    const params = { q: state.q, k: 24, page: state.page, sort: state.sort };
    const r = await api(state.kind, params);
    const items = r.results || [];
    state.items = append ? state.items.concat(items) : items;
    state.total = r.total ?? null;
    render(items, append);
    const n = state.items.length;
    meta.textContent = n
      ? (state.total != null ? `${n} of ${state.total.toLocaleString()} ${state.kind}` : `${n} ${state.kind}`)
        + (state.q ? ` for “${state.q}”` : '')
      : `nothing found${state.q ? ` for “${state.q}”` : ''} — try another word or kind`;
    const hasMore = items.length >= (state.kind === 'sfx' ? 1 : 24) && (state.total == null || n < state.total);
    more.classList.toggle('hidden', !hasMore || !items.length);
  } catch (e) {
    if (!append) results.innerHTML = '';
    meta.innerHTML = `<span class="bad-text">${esc(e.message)}</span>`;
  } finally {
    state.busy = false;
    more.disabled = false;
  }
}

function skeleton() {
  return Array.from({ length: 6 }, () => '<div class="item skel"><div class="sk-line w60"></div><div class="sk-line w40"></div></div>').join('');
}

// ── render ───────────────────────────────────────────────────────
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const chipList = (arr, n = 4) => (arr || []).filter(Boolean).slice(0, n).map((t) => `<span class="tagc">${esc(String(t).trim())}</span>`).join('');
// artlist.io keys pages on the id; the slug is cosmetic. (Song pages verified; sfx/template shapes follow the site's public URLs.)
const slug = (n) => encodeURIComponent((n || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')) || 'x';
const fmt = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

function render(items, append) {
  const html = items.map((it, i) => card(it, (append ? state.items.length - items.length : 0) + i)).join('');
  if (append) results.insertAdjacentHTML('beforeend', html);
  else results.innerHTML = html;
}

function card(it, idx) {
  const k = state.kind;
  if (k === 'music' || k === 'sfx') {
    const playable = !!it.preview_url;
    const url = k === 'music' ? it.page_url : `https://artlist.io/sfx/track/${slug(it.name)}/${it.id}`;
    return `<article class="item track${playable ? '' : ' mute'}" data-idx="${idx}">
      <button class="play" data-idx="${idx}" aria-label="play" ${playable ? '' : 'disabled'}>${ICON.play}</button>
      <div class="body">
        <div class="title">${esc(it.name)}</div>
        <div class="sub">${esc(it.artist)}${it.album && it.album !== it.name ? ` · ${esc(it.album)}` : ''} <span class="dur">${esc(it.duration || '')}</span></div>
        <div class="tags">${chipList(k === 'music' ? it.tags : it.categories)}</div>
      </div>
      <a class="open" href="${esc(url)}" target="_blank" rel="noreferrer">open</a>
    </article>`;
  }
  if (k === 'footage') {
    const url = `https://artgrid.io/clip/${it.id}`;
    return `<article class="item visual">
      <a class="thumb" href="${esc(url)}" target="_blank" rel="noreferrer" style="${it.thumbnail_url ? `background-image:url('${esc(it.thumbnail_url)}')` : ''}">
        <span class="dur-badge">${ICON.clip} ${fmt(it.duration_s)}</span></a>
      <div class="body">
        <div class="title">${esc(it.name)}</div>
        <div class="sub">${esc(it.story || '')}</div>
        <div class="tags">${chipList(it.categories, 3)}</div>
      </div>
      <a class="open" href="${esc(url)}" target="_blank" rel="noreferrer">open</a>
    </article>`;
  }
  if (k === 'templates') {
    const url = `https://artlist.io/video-templates/template/${slug(it.name)}/${it.id}`;
    return `<article class="item visual">
      <a class="thumb" href="${esc(url)}" target="_blank" rel="noreferrer" style="${it.thumbnail_url ? `background-image:url('${esc(it.thumbnail_url)}')` : ''}">
        <span class="dur-badge">${ICON.tpl} ${it.downloads ? it.downloads.toLocaleString() + ' dl' : 'template'}</span></a>
      <div class="body">
        <div class="title">${esc(it.name)}</div>
        <div class="sub">${esc(it.artist || '')}</div>
        <div class="tags">${chipList((it.software || []).map((s) => s.replace('Adobe ', '').replace('DaVinci ', '')), 4)}</div>
      </div>
      <a class="open" href="${esc(url)}" target="_blank" rel="noreferrer">open</a>
    </article>`;
  }
  // voices: one card, one play per accent
  const accents = (it.accents || []).filter((a) => a.preview_url);
  // Upstream often names every accent after the voice itself; number them so the buttons differ.
  const label = (a, j) => (!a.name || a.name === it.name) ? `sample ${j + 1}` : a.name;
  return `<article class="item voice" data-idx="${idx}">
    <span class="play static">${ICON.mic}</span>
    <div class="body">
      <div class="title">${esc(it.name)}</div>
      <div class="sub">${esc([it.gender, it.age, it.language].filter(Boolean).join(' · '))}</div>
      <div class="tags">${accents.map((a, j) => `<button class="tagc acc" data-idx="${idx}" data-acc="${j}">${ICON.play} ${esc(label(a, j))}</button>`).join('') || '<span class="tagc">no preview</span>'}</div>
    </div>
  </article>`;
}

// ── player ───────────────────────────────────────────────────────
const audio = $('#audio'), playerEl = $('#player'), pp = $('#pp'), fill = $('#fill'), timeEl = $('#time');
let current = null; // {url, title, sub, open, el}

results.onclick = (e) => {
  const acc = e.target.closest('.acc');
  if (acc) {
    const v = state.items[+acc.dataset.idx], a = v.accents.filter((x) => x.preview_url)[+acc.dataset.acc];
    const name = (!a.name || a.name === v.name) ? `sample ${+acc.dataset.acc + 1}` : a.name;
    return play({ url: a.preview_url, title: v.name, sub: `${name} · ${v.language || ''}`, open: 'https://artlist.io/voice-over', el: acc });
  }
  const b = e.target.closest('.play[data-idx]');
  if (!b || b.disabled) return;
  const it = state.items[+b.dataset.idx];
  const url = it.page_url || 'https://artlist.io/royalty-free-sound-effects';
  play({ url: it.preview_url, title: it.name, sub: it.artist || '', open: url, el: b });
};

function play(t) {
  if (current && current.url === t.url) return toggle();
  markPlaying(false);
  current = t;
  audio.src = streamUrl(t.url);
  $('#np-title').textContent = t.title;
  $('#np-sub').textContent = t.sub;
  $('#np-open').href = t.open;
  playerEl.classList.remove('hidden');
  fill.style.width = '0%';
  audio.play().catch((err) => { $('#np-sub').textContent = `could not play: ${err.message}`; });
}
function toggle() { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); }
pp.onclick = toggle;

function markPlaying(on) {
  if (!current) return;
  pp.innerHTML = on ? ICON.pause : ICON.play;
  for (const el of document.querySelectorAll('.playing')) el.classList.remove('playing');
  if (on && current.el?.isConnected) {
    current.el.classList.add('playing');
    current.el.closest('.item')?.classList.add('playing');
  }
  for (const el of document.querySelectorAll('.play[data-idx]')) {
    const isCur = current.el === el;
    el.innerHTML = isCur && on ? ICON.pause : ICON.play;
  }
}
audio.onplay = () => markPlaying(true);
audio.onpause = () => markPlaying(false);
audio.onended = () => { markPlaying(false); fill.style.width = '100%'; };
audio.ontimeupdate = () => {
  if (audio.duration) fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
  timeEl.textContent = `${fmt(audio.currentTime)}${audio.duration ? ' / ' + fmt(audio.duration) : ''}`;
};
audio.onerror = () => { $('#np-sub').textContent = 'preview unavailable'; markPlaying(false); };
$('#seek').onclick = (e) => {
  if (!audio.duration) return;
  const r = e.currentTarget.getBoundingClientRect();
  audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
};
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && current && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement?.tagName)) {
    e.preventDefault(); toggle();
  }
});

// ── boot ─────────────────────────────────────────────────────────
(function boot() {
  const [kind, q] = location.hash.slice(1).split('/');
  setKind(KINDS[kind] ? kind : 'music');
  if (q) qEl.value = decodeURIComponent(q);
  health();
  if (qEl.value || state.kind === 'voices') search();
})();
