/* musica — the crate half: this module's own API, and Spotify behind it.
 *
 * Every call is relative to the page. index.html pins <base href="./"> and the
 * gateway serves the console at /musica with the prefix kept, so `api/search`
 * resolves to /musica/api/search there and to the same path locally. Making
 * these absolute is how this breaks behind the gateway — serve.py's docstring
 * says the same thing from the other side.
 *
 * serve.py wraps every answer as {result: …} or {error: …}; unwrapping happens
 * here so nothing above this file has to know that.
 */
(function (root) {
'use strict';
const M = root.MUSICA || (root.MUSICA = {});

async function call(fn, params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v);
  }
  const url = 'api/' + fn + (qs.toString() ? '?' + qs : '');
  let res;
  try {
    res = await fetch(url, { headers: { 'Accept': 'application/json' } });
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
  info: () => call('info'),
  status: () => call('spotify_status'),
  search: (q, kind, limit) => call('search', { q, kind, limit }),
  track: (id) => call('track', { id }),
  playlist: (id, limit) => call('playlist', { id, limit }),
  decks: () => call('decks'),
  kit: () => call('kit'),
};

/* mm:ss from Spotify's milliseconds. */
function dur(ms) {
  if (!ms && ms !== 0) return '';
  const s = Math.round(ms / 1000);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

/* The second line of a crate row — whatever this kind of result actually has. */
function subtitle(item, kind) {
  if (kind === 'track') {
    return [item.artists, item.album, dur(item.duration_ms)].filter(Boolean).join(' · ');
  }
  if (kind === 'album') {
    return [item.artists, item.release, item.tracks && item.tracks + ' tracks']
      .filter(Boolean).join(' · ');
  }
  if (kind === 'artist') {
    const f = item.followers ? item.followers.toLocaleString() + ' followers' : '';
    return [(item.genres || []).slice(0, 2).join(', '), f].filter(Boolean).join(' · ');
  }
  return [item.owner, item.tracks && item.tracks + ' tracks'].filter(Boolean).join(' · ');
}

M.api = api;
M.crate = { dur, subtitle };

})(typeof globalThis !== 'undefined' ? globalThis : this);
