/* crates — the crate half: this module's API, and three platforms behind it.
 *
 * Every call is relative to the page. index.html pins <base href="./"> and the
 * gateway serves the console at /crates with the prefix kept, so `api/search`
 * resolves to /crates/api/search there and to the same path locally. Making
 * these absolute is how this breaks behind the gateway — serve.py's docstring
 * says the same thing from the other side.
 *
 * serve.py wraps every answer as {result: …} or {error: …}; unwrapping happens
 * here so nothing above this file has to know that. The pure helpers at the
 * bottom (link detection, Camelot compatibility) have no DOM and are what
 * tests/engine.mjs exercises.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});

async function call(fn, params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v);
  }
  const url = 'api/' + fn + (qs.toString() ? '?' + qs : '');
  return fetchJson(fn, url, { headers: { 'Accept': 'application/json', ...creds() } });
}

/* Who the caller is, on every request. playlists.js owns the credential — a
 * wallet token or a guest key — and this is where it gets attached, so no call
 * site has to remember to. Anonymous is a valid state: the crate itself, and
 * anything somebody has shared, answer without one. */
function creds() {
  const p = M.playlists;
  return p && p.headers ? p.headers() : {};
}

/* A call whose arguments do not fit in a query string — a playlist's whole
 * track list, for instance. Same envelope, same unwrapping. */
async function post(fn, body) {
  return fetchJson(fn, 'api/' + fn, {
    method: 'POST',
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', ...creds() },
    body: JSON.stringify(body || {}),
  });
}

async function fetchJson(fn, url, init) {
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    throw new Error('the module is not answering — is it still running?');
  }
  let body;
  try {
    body = await res.json();
  } catch (e) {
    throw new Error(`${fn} returned ${res.status} but not JSON`);
  }
  // The API reports its own failures inside a 200 as often as not, so check the
  // body before the status: an {error} in either place is the same failure.
  if (body && body.error) throw new Error(body.error);
  if (!res.ok) throw new Error(`${fn} failed (${res.status})`);
  const r = body && Object.prototype.hasOwnProperty.call(body, 'result')
    ? body.result : body;
  if (r && r.error) throw new Error(r.error);
  return r;
}

const api = {
  call,
  post,
  info: () => call('info'),
  platforms: () => call('platforms'),
  search: (q, source, kind, limit) => call('search', { q, source, kind, limit }),
  resolve: (url) => call('resolve', { url }),
  stream: (source, id, track) => call('stream', { source, id, track }),
  discover: (tag, slice, size) => call('discover', { tag, slice, size }),
  bandcampPage: (url) => call('bandcamp_page', { url }),
  soundcloudPlaylist: (id) => call('soundcloud_playlist', { id }),
  soundcloudUser: (id) => call('soundcloud_user', { id }),
  track: (id) => call('track', { id }),
  album: (id) => call('album', { id }),
  artist: (id) => call('artist', { id }),
  playlist: (id, limit) => call('playlist', { id, limit }),
  myPlaylists: () => call('my_playlists'),
  decks: () => call('decks'),
  kit: () => call('kit'),
};

/* The URL the browser should fetch a platform track's bytes from: SoundCloud
 * hands over a CORS-open CDN URL, Bandcamp goes through the module's proxy.
 * Both are answers to api.stream(); this just picks the right one. */
function streamUrl(where) {
  if (!where) return null;
  if (where.direct && where.url) return where.url;
  const qs = new URLSearchParams({ id: where.id });
  if (where.bc_id) qs.set('track', where.bc_id);
  return `api/stream/${where.source}?${qs}`;
}

/* ── row text ─────────────────────────────────────────────────────────── */

/* mm:ss from milliseconds. */
function dur(ms) {
  if (!ms && ms !== 0) return '';
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}

function n(x) { return typeof x === 'number' ? x.toLocaleString() : ''; }

/* The second line of a crate row — whatever this kind of result actually has. */
function subtitle(item) {
  const kind = item.kind || 'track';
  if (kind === 'track') {
    return [item.artists, item.album, dur(item.duration_ms),
            item.bpm && `${Math.round(item.bpm)} BPM`, item.genre]
      .filter(Boolean).join(' · ');
  }
  if (kind === 'album') {
    return [item.artists, item.release && String(item.release).slice(0, 10),
            item.tracks && item.tracks + ' tracks', item.genre]
      .filter(Boolean).join(' · ');
  }
  if (kind === 'artist') {
    const f = item.followers ? n(item.followers) + ' followers' : '';
    const g = (item.genres || item.tags || []).slice(0, 2).join(', ');
    return [g, f, item.location, item.tracks && item.tracks + ' tracks']
      .filter(Boolean).join(' · ');
  }
  return [item.artists || item.owner, item.tracks && item.tracks + ' tracks']
    .filter(Boolean).join(' · ');
}

const SOURCES = {
  spotify: { label: 'Spotify', short: 'SP', color: '#1db954', plays: false },
  bandcamp: { label: 'Bandcamp', short: 'BC', color: '#1da0c3', plays: true },
  soundcloud: { label: 'SoundCloud', short: 'SC', color: '#ff5500', plays: true },
  youtube: { label: 'YouTube', short: 'YT', color: '#ff3b30', plays: true },
  archive: { label: 'Internet Archive', short: 'IA', color: '#9c8cff', plays: true },
  local: { label: 'File', short: 'FILE', color: '#c8ff2e', plays: true },
  unknown: { label: 'Unknown', short: '?', color: '#8b90a0', plays: false },
};

/* Whether a deck can decode this source's audio at all. Spotify is the only
 * no: its stream is DRM-protected, which no amount of Web Audio gets around. */
function playable(source) {
  const s = SOURCES[source];
  return s ? s.plays !== false : true;
}

/* ── links ────────────────────────────────────────────────────────────── */

/* A client-side twin of platforms.detect(), so the search box can show what a
 * pasted link is before the request goes out. The server is the authority. */
function detect(text) {
  const s = (text || '').trim();
  let m = s.match(/^spotify:(track|album|artist|playlist):([A-Za-z0-9]+)$/);
  if (m) return { source: 'spotify', kind: m[1], id: m[2] };
  if (!/^https?:\/\//i.test(s)) return null;
  let u;
  try { u = new URL(s); } catch (e) { return null; }
  const host = u.hostname.toLowerCase(), path = u.pathname.replace(/\/+$/, '');
  if (host.endsWith('spotify.com')) {
    m = path.match(/\/(track|album|artist|playlist)\/([A-Za-z0-9]+)/);
    return m ? { source: 'spotify', kind: m[1], id: m[2] } : null;
  }
  if (host.endsWith('bandcamp.com')) {
    const kind = path.includes('/track/') ? 'track' : (path.includes('/album/') ? 'album' : 'artist');
    return { source: 'bandcamp', kind, id: `${u.protocol}//${u.host}${path}` };
  }
  if (/(^|\.)youtube\.com$/.test(host) || host.endsWith('youtu.be')) {
    if (host.endsWith('youtu.be')) {
      const vid = path.split('/').filter(Boolean)[0];
      return vid ? { source: 'youtube', kind: 'track', id: vid } : null;
    }
    if (path === '/watch' && u.searchParams.get('v')) {
      return { source: 'youtube', kind: 'track', id: u.searchParams.get('v') };
    }
    m = path.match(/^\/(?:shorts|embed|v|live)\/([\w-]+)/);
    if (m) return { source: 'youtube', kind: 'track', id: m[1] };
    if (path === '/playlist' && u.searchParams.get('list')) {
      return { source: 'youtube', kind: 'playlist', id: u.searchParams.get('list') };
    }
    m = path.match(/^\/(channel\/[\w-]+|@[^/]+|c\/[^/]+|user\/[^/]+)/);
    if (m) return { source: 'youtube', kind: 'artist', id: m[1].split('/').pop() };
    return null;
  }
  if (host.endsWith('archive.org')) {
    m = path.match(/^\/(?:details|download|embed)\/([^/]+)/);
    return m ? { source: 'archive', kind: 'album', id: decodeURIComponent(m[1]) } : null;
  }
  if (/^(www\.|m\.|on\.)?soundcloud\.com$/.test(host)) {
    const parts = path.split('/').filter(Boolean);
    if (!parts.length) return null;
    const kind = parts.length >= 3 && parts[1] === 'sets' ? 'playlist'
      : (parts.length >= 2 ? 'track' : 'artist');
    return { source: 'soundcloud', kind, id: `https://soundcloud.com${path}` };
  }
  return null;
}

/* ── harmonic mixing ──────────────────────────────────────────────────── */

/* Camelot codes are "8A" … "12B": the number is position on the wheel, the
 * letter minor (A) or major (B). Two keys mix cleanly when they are the same,
 * one step apart on the wheel, or the relative major/minor of each other. */
function parseCamelot(code) {
  const m = String(code || '').trim().toUpperCase().match(/^(1[0-2]|[1-9])([AB])$/);
  return m ? { n: parseInt(m[1], 10), l: m[2] } : null;
}

function camelotRel(a, b) {
  const A = parseCamelot(a), B = parseCamelot(b);
  if (!A || !B) return { rel: 'unknown', score: 0, label: '' };
  const d = Math.min((A.n - B.n + 12) % 12, (B.n - A.n + 12) % 12);
  if (d === 0 && A.l === B.l) return { rel: 'same', score: 3, label: 'same key' };
  if (d === 0) return { rel: 'relative', score: 3, label: 'relative' };
  if (d === 1 && A.l === B.l) return { rel: 'adjacent', score: 2, label: 'perfect' };
  if (d === 2 && A.l === B.l) return { rel: 'energy', score: 1, label: 'energy' };
  if (d === 1) return { rel: 'near', score: 1, label: 'near' };
  return { rel: 'clash', score: 0, label: 'clash' };
}

/* Semitone-adjusted: a pitched deck is not in the key its file was in. Each
 * ±6% of pitch is very roughly a semitone, which is 7 steps on the wheel. */
function camelotShift(code, semitones) {
  const c = parseCamelot(code);
  if (!c || !semitones) return code;
  const n = ((c.n - 1 + semitones * 7) % 12 + 12) % 12 + 1;
  return `${n}${c.l}`;
}

M.api = api;
M.crate = { dur, subtitle, detect, streamUrl, SOURCES, playable, parseCamelot,
            camelotRel, camelotShift };

})(typeof globalThis !== 'undefined' ? globalThis : this);
