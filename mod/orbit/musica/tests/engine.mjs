/* musica — the engine under node.
 *
 * `m musica/test` runs this. It loads the same files the browser loads and
 * drives the parts that are pure maths against synthetic audio we know the
 * answer for: a click track at a known tempo, and a chord built from a known
 * key profile. A beat grid that is subtly wrong is not visible on a waveform
 * and not audible until two tracks drift apart, so it gets asserted here.
 *
 * Prints a JSON summary as its last line — mod.py parses that. Exit 1 on any
 * failure, so pm2 and CI see it too.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInThisContext } from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, '..', 'web', 'js');

// The console's files are classic scripts that hang everything off
// globalThis.MUSICA, so evaluating them here is all the "import" they need.
for (const f of ['engine.js', 'analyze.js', 'synth.js', 'sequencer.js', 'crate.js']) {
  runInThisContext(readFileSync(join(WEB, f), 'utf8'), { filename: f });
}
const M = globalThis.MUSICA;

/* ── assertions ───────────────────────────────────────────────────────── */

const failures = [];
let checks = 0;

function ok(name, cond, detail) {
  checks++;
  if (!cond) failures.push(detail ? `${name}: ${detail}` : name);
  return cond;
}
function near(name, got, want, tol) {
  return ok(name, Math.abs(got - want) <= tol,
    `got ${Number(got).toFixed(4)}, wanted ${want} ±${tol}`);
}

/* ── synthetic audio ──────────────────────────────────────────────────── */

const SR = 22050;

/* A four-to-the-floor click track: a decaying 60Hz thump plus a transient,
 * accented on the downbeat. Enough of an onset for the envelope to find. */
function clickTrack(bpm, seconds, offset = 0) {
  const n = Math.floor(seconds * SR);
  const x = new Float32Array(n);
  const beat = 60 / bpm;
  const len = Math.floor(0.05 * SR);
  let rnd = 12345;
  const rand = () => ((rnd = (rnd * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) * 2 - 1;
  for (let b = 0; ; b++) {
    const t = offset + b * beat;
    const s = Math.floor(t * SR);
    if (s + len >= n) break;
    const amp = b % 4 === 0 ? 1 : 0.62;
    for (let i = 0; i < len; i++) {
      const e = Math.exp(-i / (0.010 * SR));
      x[s + i] += amp * e * (Math.sin(2 * Math.PI * 62 * i / SR) * 0.85 + rand() * 0.32);
    }
    // An offbeat hat, so the envelope has to reject a plausible wrong answer at
    // double time rather than only seeing the pulse we want.
    const h = Math.floor((t + beat / 2) * SR);
    if (h + 400 < n) {
      for (let i = 0; i < 400; i++) x[h + i] += Math.exp(-i / 220) * rand() * 0.14;
    }
  }
  return x;
}

/* Sustained tones weighted by a key profile: what a piece "in C major" looks
 * like to a chroma vector, without needing a real recording in the repo. */
function keySignal(tonicPc, minor, seconds) {
  const prof = minor
    ? [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    : [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
  const n = Math.floor(seconds * SR);
  const x = new Float32Array(n);
  for (let d = 0; d < 12; d++) {
    const pc = (tonicPc + d) % 12;
    const amp = (prof[d] / 6.35) * 0.05;
    for (const midi of [48 + pc, 60 + pc, 72 + pc]) {
      const w = 2 * Math.PI * 440 * Math.pow(2, (midi - 69) / 12) / SR;
      for (let i = 0; i < n; i++) x[i] += amp * Math.sin(w * i);
    }
  }
  return x;
}

/* ── tempo ────────────────────────────────────────────────────────────── */

const tempo = {};
for (const bpm of [128, 90, 174]) {
  const r = M.analyze.detectTempo(clickTrack(bpm, 20), SR);
  tempo[bpm] = r.bpm;
  near(`tempo ${bpm}`, r.bpm, bpm, 1.5);
  ok(`tempo ${bpm} confidence`, r.confidence > 0.15, `confidence ${r.confidence}`);
}

/* The downbeat, not just the period. Sync lines two tracks up by this number,
 * so an offset that is out by half a beat is a mix that never lands. */
{
  const bpm = 124, off = 0.31;
  const r = M.analyze.detectTempo(clickTrack(bpm, 20, off), SR);
  const beat = 60 / bpm;
  let d = Math.abs(((r.offset - off) % beat + beat) % beat);
  if (d > beat / 2) d = beat - d;
  ok('tempo phase', d < 0.03, `offset ${r.offset.toFixed(3)} vs ${off}, ${d.toFixed(3)}s out`);
  tempo.phaseError = Math.round(d * 1000) / 1000;
}

/* Silence has no tempo and must say so rather than inventing one. */
ok('tempo of silence', (() => {
  const r = M.analyze.detectTempo(new Float32Array(SR * 4), SR);
  return r.bpm === null || r.confidence < 0.2;
})(), 'silence produced a confident tempo');

/* ── key ──────────────────────────────────────────────────────────────── */

const keys = {};
for (const [pc, minor, want] of [[0, false, 'C major'], [7, false, 'G major'],
                                 [9, true, 'A minor'], [3, true, 'D# minor']]) {
  const r = M.analyze.detectKey(keySignal(pc, minor, 8), SR);
  keys[want] = `${r.key} ${r.camelot}`;
  ok(`key ${want}`, r.key === want, `got ${r.key}`);
}

/* The Camelot wheel is a lookup people mix by; a wrong entry sends you to a
 * clashing track. Relatives share a number, and a fifth is one step around. */
ok('camelot C major', M.analyze.camelot(0, false) === '8B');
ok('camelot A minor', M.analyze.camelot(9, true) === '8A');
ok('camelot relatives share a number', (() => {
  for (let pc = 0; pc < 12; pc++) {
    const maj = M.analyze.camelot(pc, false);
    const rel = M.analyze.camelot((pc + 9) % 12, true);   // relative minor
    if (maj.slice(0, -1) !== rel.slice(0, -1)) return false;
  }
  return true;
})());
ok('camelot fifths are adjacent', (() => {
  for (let pc = 0; pc < 12; pc++) {
    const a = parseInt(M.analyze.camelot(pc, false), 10);
    const b = parseInt(M.analyze.camelot((pc + 7) % 12, false), 10);
    if (((a % 12) + 1) !== b && !(a === 12 && b === 1)) return false;
  }
  return true;
})());
ok('camelot covers the wheel', (() => {
  const seen = new Set();
  for (let pc = 0; pc < 12; pc++) {
    seen.add(M.analyze.camelot(pc, false));
    seen.add(M.analyze.camelot(pc, true));
  }
  return seen.size === 24;
})());

/* ── the grid ─────────────────────────────────────────────────────────── */

near('16th at 120bpm', M.grid.stepDur(120), 0.125, 1e-9);
near('step 4 at 120bpm', M.grid.stepTime(4, 120, 0), 0.5, 1e-9);
ok('swing leaves downbeats alone', M.grid.stepTime(4, 120, 0.6) === M.grid.stepTime(4, 120, 0));
ok('swing delays the offbeat', M.grid.stepTime(1, 120, 0.6) > M.grid.stepTime(1, 120, 0));
ok('swing never reorders steps', (() => {
  for (const sw of [0, 0.25, 0.5, 0.75]) {
    for (let k = 1; k < 64; k++) {
      if (M.grid.stepTime(k, 126, sw) <= M.grid.stepTime(k - 1, 126, sw)) return false;
    }
  }
  return true;
})(), 'a step landed before the one before it');
ok('bars and beats count from 1', (() => {
  const a = M.grid.barsBeats(0, 16), b = M.grid.barsBeats(4, 16), c = M.grid.barsBeats(16, 32);
  return a.bar === 1 && a.beat === 1 && b.beat === 2 && c.bar === 2;
})());

/* ── the mixer's curves ───────────────────────────────────────────────── */

ok('crossfader is constant power', (() => {
  for (let x = 0; x <= 1.0001; x += 0.05) {
    const g = M.curves.crossGains(x);
    if (Math.abs(g.a * g.a + g.b * g.b - 1) > 1e-9) return false;
  }
  return true;
})());
near('crossfader centre', M.curves.crossGains(0.5).a, Math.SQRT1_2, 1e-9);
near('EQ centre is flat', M.curves.eqDb(0.5), 0, 1e-9);
ok('EQ kills at zero', M.curves.eqDb(0) <= -40);
near('EQ tops out at +6', M.curves.eqDb(1), 6, 1e-9);
ok('filter centre is a bypass', (() => {
  const f = M.curves.filterHz(0, 22050);
  return f.hp <= 20 && f.lp >= 20000;
})());
ok('filter left is a lowpass sweep', M.curves.filterHz(-0.9, 22050).lp < 800);
ok('filter right is a highpass sweep', M.curves.filterHz(0.9, 22050).hp > 3000);
near('phase wraps', M.phase(2.5, 1), 0.5, 1e-9);
near('phase of a negative time', M.phase(-0.25, 1), 0.75, 1e-9);

/* ── patterns ─────────────────────────────────────────────────────────── */

{
  const chans = M.synth.KIT;
  const p = M.pattern.make(16, chans);
  p.rows.kick[0] = 2; p.rows.kick[4] = 1;
  M.pattern.resize(p, 32);
  ok('resize keeps the pattern', p.rows.kick[0] === 2 && p.rows.kick[4] === 1);
  ok('growing repeats the bar', p.rows.kick[16] === 2 && p.rows.kick[20] === 1,
     'doubling a pattern should give the same bar twice');
  M.pattern.resize(p, 16);
  ok('shrinking keeps the first bar', p.rows.kick[0] === 2 && p.steps === 16);
  ok('every kit voice exists', chans.every(c => typeof M.synth.voices[c.voice] === 'function'),
     'a channel names a voice synth.js does not have');
  ok('the kit matches mod.py', ['kick', 'snare', 'clap', 'hat', 'tom', 'rim',
      'cowbell', 'bass', 'lead', 'sampler'].every(n => chans.some(c => c.voice === n)));
}

/* ── waveform peaks ───────────────────────────────────────────────────── */

{
  const n = 4096;
  const data = new Float32Array(n);
  for (let i = 0; i < n; i++) data[i] = Math.sin(2 * Math.PI * i / 64);
  const buffer = {
    length: n, numberOfChannels: 1, sampleRate: SR,
    getChannelData: () => data,
  };
  const pk = M.peaks(buffer, 64);
  ok('peaks are the right length', pk.length === 128);
  let good = true;
  for (let b = 0; b < 64; b++) {
    if (!(pk[b * 2] < -0.9 && pk[b * 2 + 1] > 0.9)) good = false;
  }
  ok('peaks bracket the waveform', good);
}

/* ── crate: links and the Camelot wheel ───────────────────────────────── */

{
  const C = M.crate;
  const links = [
    ['https://fourtet.bandcamp.com/album/three', 'bandcamp', 'album'],
    ['https://fourtet.bandcamp.com/track/loved-2?from=x', 'bandcamp', 'track'],
    ['https://soundcloud.com/four-tet/lost-village-23rd-august-2025', 'soundcloud', 'track'],
    ['https://m.soundcloud.com/clutchrecs/sets/tech-house', 'soundcloud', 'playlist'],
    ['https://soundcloud.com/four-tet', 'soundcloud', 'artist'],
    ['https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc', 'spotify', 'track'],
    ['spotify:playlist:37i9dQZF1DXcBWIGoYBM5M', 'spotify', 'playlist'],
    ['https://www.youtube.com/watch?v=SM4tQcUt_mQ', 'youtube', 'track'],
    ['https://www.youtube.com/watch?v=SM4tQcUt_mQ&list=PL123', 'youtube', 'track'],
    ['https://youtu.be/SM4tQcUt_mQ?t=30', 'youtube', 'track'],
    ['https://www.youtube.com/shorts/abc_DEF-123', 'youtube', 'track'],
    ['https://music.youtube.com/playlist?list=OLAK5uy_kabc', 'youtube', 'playlist'],
    ['https://www.youtube.com/@boardsofcanada', 'youtube', 'artist'],
    ['https://www.youtube.com/channel/UC3sZYInu3YYkyIXBif83ZCg', 'youtube', 'artist'],
    ['https://archive.org/details/gd1977-05-08.sbd', 'archive', 'album'],
    ['https://archive.org/download/gd1977-05-08.sbd/gd77-05-08d1t01.mp3', 'archive', 'track'],
  ];
  for (const [url, src, kind] of links) {
    const d = C.detect(url);
    ok(`detect ${url}`, d && d.source === src && d.kind === kind,
      d ? `got ${d.source}/${d.kind}` : 'got null');
  }
  ok('detect ignores plain text', C.detect('four tet') === null);
  ok('youtube id survives the query string',
    C.detect('https://www.youtube.com/watch?v=SM4tQcUt_mQ&list=PL1&index=2').id === 'SM4tQcUt_mQ');
  ok('archive track id is identifier/filename',
    C.detect('https://archive.org/download/x_item/02%20-%20Track.mp3').id === 'x_item/02 - Track.mp3');
  ok('only Spotify refuses to play',
    ['bandcamp', 'soundcloud', 'youtube', 'archive', 'local'].every(C.playable)
      && !C.playable('spotify'));
  ok('detect ignores other hosts', C.detect('https://example.com/album/x') === null);

  ok('camelot same key', C.camelotRel('8A', '8A').rel === 'same');
  ok('camelot relative major', C.camelotRel('8A', '8B').rel === 'relative');
  ok('camelot adjacent', C.camelotRel('8A', '9A').rel === 'adjacent');
  ok('camelot wraps the wheel', C.camelotRel('12A', '1A').rel === 'adjacent');
  ok('camelot energy boost', C.camelotRel('8A', '10A').rel === 'energy');
  ok('camelot clash', C.camelotRel('8A', '2B').rel === 'clash');
  ok('camelot unknown', C.camelotRel(null, '8A').rel === 'unknown');
  ok('camelot shift +1 semitone = +7', C.camelotShift('8A', 1) === '3A');
  ok('camelot shift -1 semitone', C.camelotShift('3A', -1) === '8A');
  ok('camelot shift keeps mode', C.camelotShift('12B', 2) === '2B');
  ok('duration formats hours', C.dur(6492336) === '1:48:12');
  ok('duration formats minutes', C.dur(243264) === '4:03');
  ok('stream url: direct wins', C.streamUrl({ direct: true, url: 'https://cdn/x.mp3', source: 'soundcloud', id: 1 }) === 'https://cdn/x.mp3');
  ok('stream url: youtube proxies by video id',
    C.streamUrl({ direct: false, source: 'youtube', id: 'SM4tQcUt_mQ' })
      === 'api/stream/youtube?id=SM4tQcUt_mQ');
  ok('stream url: archive fetches direct',
    C.streamUrl({ direct: true, source: 'archive', id: 'item/a.mp3',
                  url: 'https://archive.org/download/item/a.mp3' })
      === 'https://archive.org/download/item/a.mp3');
  ok('stream url: bandcamp proxies with track',
    C.streamUrl({ direct: false, source: 'bandcamp', id: 'https://a.bandcamp.com/album/b', bc_id: 42 })
      === 'api/stream/bandcamp?id=https%3A%2F%2Fa.bandcamp.com%2Falbum%2Fb&track=42');
}

/* ── report ───────────────────────────────────────────────────────────── */

const summary = {
  ok: failures.length === 0,
  checks,
  failures,
  tempo,
  keys,
};
if (failures.length) {
  for (const f of failures) console.error('FAIL  ' + f);
}
console.log(JSON.stringify(summary));
process.exit(failures.length ? 1 : 0);
