/* crates — tempo and key detection, done locally.
 *
 * Spotify closed /audio-features and /audio-analysis to new apps in November
 * 2024 (spotify.py says so where it asks), so the numbers on a deck have to be
 * worked out from the samples we already decoded. Two independent passes:
 *
 *   tempo — an onset envelope, autocorrelated with a comb that folds the
 *           harmonics back together, then a phase search for the downbeat
 *   key   — a chroma vector from Goertzel bins, correlated against the
 *           Krumhansl-Schmuckler profiles, reported in Camelot notation
 *
 * Pure functions over Float32Array. No AudioContext, no DOM — tests/engine.mjs
 * runs this exact file under node against synthetic audio.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});

/* ── framing helpers ──────────────────────────────────────────────────── */

/* Mono sum of anything shaped like an AudioBuffer. */
function toMono(buffer) {
  const n = buffer.length, chs = buffer.numberOfChannels;
  if (chs === 1) return buffer.getChannelData(0);
  const out = new Float32Array(n);
  for (let c = 0; c < chs; c++) {
    const d = buffer.getChannelData(c);
    for (let i = 0; i < n; i++) out[i] += d[i];
  }
  for (let i = 0; i < n; i++) out[i] /= chs;
  return out;
}

/* Decimate to a lower rate. Averaging over the whole span rather than picking
 * every Nth sample is a crude lowpass, but it is the difference between a
 * chroma vector and a bucket of aliases. */
function decimate(x, srIn, srOut) {
  if (srOut >= srIn) return x;
  const ratio = srIn / srOut;
  const n = Math.floor(x.length / ratio);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const s = Math.floor(i * ratio), e = Math.min(x.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = s; j < e; j++) sum += x[j];
    out[i] = e > s ? sum / (e - s) : 0;
  }
  return out;
}

/* The stretch worth analysing: the middle of the track, where an intro of
 * silence or a long ambient tail cannot skew the answer. */
function excerpt(x, sr, seconds) {
  const want = Math.floor(seconds * sr);
  if (x.length <= want) return x;
  const start = Math.floor((x.length - want) / 2);
  return x.subarray(start, start + want);
}

/* ── tempo ────────────────────────────────────────────────────────────── */

const ENV_RATE = 200;   // envelope frames per second

/* A percussive onset envelope: frame energy, log-compressed, then half-wave
 * rectified first difference. Log compression is what stops a loud chorus from
 * outvoting a quiet verse when the whole thing is autocorrelated. */
function onsetEnvelope(x, sr) {
  const hop = Math.max(1, Math.round(sr / ENV_RATE));
  const frames = Math.floor(x.length / hop);
  const env = new Float32Array(frames);
  let prev = 0;
  for (let f = 0; f < frames; f++) {
    let e = 0;
    const s = f * hop, end = s + hop;
    for (let i = s; i < end; i++) e += x[i] * x[i];
    const cur = Math.log(1 + 400 * Math.sqrt(e / hop));
    env[f] = Math.max(0, cur - prev);
    prev = cur;
  }
  // Subtract a local mean so a steady rise does not read as a run of onsets.
  const w = 12;
  const sm = new Float32Array(frames);
  for (let f = 0; f < frames; f++) {
    let sum = 0, n = 0;
    for (let j = Math.max(0, f - w); j < Math.min(frames, f + w + 1); j++) { sum += env[j]; n++; }
    sm[f] = Math.max(0, env[f] - sum / n);
  }
  return sm;
}

function autocorr(env, lag) {
  let sum = 0;
  const n = env.length - lag;
  if (n <= 0) return 0;
  for (let i = 0; i < n; i++) sum += env[i] * env[i + lag];
  return sum / n;
}

/* Interpolate the true peak between integer lags. Without this the answer is
 * quantised to the envelope rate — about 0.8 BPM of error at 128. */
function refine(f, lo, mid, hi) {
  const d = lo - 2 * mid + hi;
  if (!d) return f;
  return f + 0.5 * (lo - hi) / d;
}

/* A log-normal preference over tempo. Dance music clusters around 125 and the
 * ear halves or doubles anything far outside that, so a hypothesis is weighted
 * by how far it sits, in octaves, from the middle of where tempos actually are.
 * Wide on purpose: this breaks ties, it does not overrule evidence. */
function prior(bpm, sigma) {
  const d = Math.log2(bpm / 125) / sigma;
  return Math.exp(-0.5 * d * d);
}

/* The phase of a beat grid of period P that collects the most onset energy. */
function bestPhase(env, P) {
  let bestP = 0, bestSum = -1;
  for (let p = 0; p < Math.ceil(P); p++) {
    let sum = 0;
    for (let i = p; i < env.length; i += P) sum += env[Math.round(i)] || 0;
    if (sum > bestSum) { bestSum = sum; bestP = p; }
  }
  return bestP;
}

/* Mean onset energy per beat, on the best-aligned grid of period P. Unlike an
 * autocorrelation this cannot borrow evidence from another period: every beat
 * the hypothesis claims has to pay for itself. */
function beatEnergy(env, P) {
  let best = 0;
  for (let p = 0; p < Math.ceil(P); p++) {
    let sum = 0, n = 0;
    for (let i = p; i < env.length; i += P) { sum += env[Math.round(i)] || 0; n++; }
    if (n && sum / n > best) best = sum / n;
  }
  return best;
}

function detectTempo(x, sr, opts) {
  opts = opts || {};
  const minBpm = opts.minBpm || 70, maxBpm = opts.maxBpm || 185;
  const env = onsetEnvelope(x, sr);
  if (env.length < ENV_RATE * 2) return { bpm: null, confidence: 0, offset: 0 };

  const lagMin = Math.floor(60 / maxBpm * ENV_RATE);
  const lagMax = Math.ceil(60 / minBpm * ENV_RATE);
  const ac = new Float32Array(lagMax + 1);
  for (let l = lagMin; l <= lagMax; l++) ac[l] = autocorr(env, l);

  /* Score each lag with its own harmonics. A four-to-the-floor track correlates
   * just as well at half and double time; summing the comb is what breaks the
   * tie toward the pulse a person would tap. */
  let best = lagMin, bestScore = -1;
  for (let l = lagMin; l <= lagMax; l++) {
    let score = ac[l];
    for (const h of [2, 3, 4]) {
      const hl = l * h;
      if (hl <= lagMax) score += ac[hl] / h;
    }
    // A weak prior, only enough to stop a wild lag winning on noise. The real
    // octave decision happens below, where it can be made on evidence.
    score *= prior(60 / (l / ENV_RATE), 0.9);
    if (score > bestScore) { bestScore = score; best = l; }
  }

  let lag = best > lagMin && best < lagMax
    ? refine(best, ac[best - 1], ac[best], ac[best + 1]) : best;

  /* Octave check — the one error that matters.
   *
   * Autocorrelation cannot tell 90 from 180: a track with an offbeat hat is
   * genuinely periodic at both, and the harmonic comb makes it worse, because a
   * hypothesis at half the true period collects the true period's own peak as
   * its second harmonic. So the choice between octaves is made separately, on a
   * different measurement: how much onset energy lands on a beat, *averaged per
   * beat*, at each candidate's own grid. Double-time hypotheses have to spend
   * that average on the weak events in between, and lose. Where two octaves
   * really are equal — 174 against 87, in drum and bass — the prior decides,
   * and it decides the way a person tapping along would. */
  const candidates = [];
  for (const mult of [0.25, 0.5, 1, 2, 4]) {
    const L = lag * mult;
    const bpm = 60 / (L / ENV_RATE);
    if (bpm < minBpm || bpm > maxBpm || L < 2) continue;
    candidates.push({ L, bpm, score: beatEnergy(env, L) * prior(bpm, 0.55) });
  }
  candidates.sort((a, b) => b.score - a.score);
  if (candidates.length) lag = candidates[0].L;
  const bpm = 60 / (lag / ENV_RATE);

  // Confidence: how far the winning correlation stands above the field, and how
  // clearly its octave beat the runner-up.
  let mean = 0, n = 0;
  for (let l = lagMin; l <= lagMax; l++) { mean += ac[l]; n++; }
  mean /= n || 1;
  let confidence = mean > 0 ? Math.min(1, (ac[best] / mean - 1) / 3) : 0;
  if (candidates.length > 1 && candidates[0].score > 0) {
    confidence *= Math.min(1, 0.5 + 0.5 * (1 - candidates[1].score / candidates[0].score) * 3);
  }

  /* Phase: the alignment of the winning grid that collects the most onset
   * energy. That offset is the downbeat the deck draws its grid from, and the
   * one sync lines two tracks up by. */
  return {
    bpm: Math.round(bpm * 10) / 10,
    confidence: Math.round(confidence * 100) / 100,
    offset: bestPhase(env, lag) / ENV_RATE,
    beat: 60 / bpm,
  };
}

/* ── key ──────────────────────────────────────────────────────────────── */

const NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

// Krumhansl-Schmuckler key profiles: how strongly each scale degree is
// expected to sound in a piece in that key.
const MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];

// The Camelot wheel, indexed by pitch class. Adjacent numbers are a fifth
// apart, so 8A→8B, 7A or 9A are the moves that will not clash.
const CAMELOT_MAJOR = ['8B', '3B', '10B', '5B', '12B', '7B', '2B', '9B', '4B', '11B', '6B', '1B'];
const CAMELOT_MINOR = ['5A', '12A', '7A', '2A', '9A', '4A', '11A', '6A', '1A', '8A', '3A', '10A'];

function camelot(pc, minor) {
  return (minor ? CAMELOT_MINOR : CAMELOT_MAJOR)[((pc % 12) + 12) % 12];
}

function pearson(a, b) {
  const n = a.length;
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - ma, y = b[i] - mb;
    num += x * y; da += x * x; db += y * y;
  }
  const den = Math.sqrt(da * db);
  return den ? num / den : 0;
}

/* Goertzel magnitude at one frequency — a single-bin DFT. Cheaper than a full
 * FFT when you only want 60 known bins out of thousands. */
function goertzel(x, from, len, sr, freq) {
  const w = 2 * Math.PI * freq / sr;
  const coeff = 2 * Math.cos(w);
  let s0 = 0, s1 = 0, s2 = 0;
  for (let i = 0; i < len; i++) {
    s0 = x[from + i] + coeff * s1 - s2;
    s2 = s1; s1 = s0;
  }
  return Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - coeff * s1 * s2)) / len;
}

/* A 12-bin chroma: energy at every semitone from C2 to B6, folded by pitch
 * class. Frame-by-frame with each frame normalised, so a loud bar and a quiet
 * one contribute the same amount of evidence about the key. */
function chroma(x, sr) {
  const SR = 11025;
  const mono = decimate(x, sr, SR);
  const N = 4096;
  const out = new Float64Array(12);
  const MIDI_LO = 36, MIDI_HI = 91;      // C2 … G6, under the Nyquist at 11025
  const freqs = [];
  for (let m = MIDI_LO; m <= MIDI_HI; m++) {
    freqs.push({ f: 440 * Math.pow(2, (m - 69) / 12), pc: m % 12 });
  }
  let frames = 0;
  for (let s = 0; s + N <= mono.length; s += N) {
    const frame = new Float64Array(12);
    let tot = 0;
    for (const b of freqs) {
      const mag = goertzel(mono, s, N, SR, b.f);
      frame[b.pc] += mag;
      tot += mag;
    }
    if (tot > 1e-9) {
      for (let i = 0; i < 12; i++) out[i] += frame[i] / tot;
      frames++;
    }
  }
  if (frames) for (let i = 0; i < 12; i++) out[i] /= frames;
  return Array.from(out);
}

function detectKey(x, sr) {
  const c = chroma(x, sr);
  const total = c.reduce((a, b) => a + b, 0);
  if (!(total > 0)) return { key: null, camelot: null, confidence: 0, chroma: c };

  let best = null, second = -1;
  for (let pc = 0; pc < 12; pc++) {
    const rot = c.slice(pc).concat(c.slice(0, pc));
    for (const [profile, minor] of [[MAJOR, false], [MINOR, true]]) {
      const score = pearson(rot, profile);
      if (!best || score > best.score) {
        if (best) second = best.score;
        best = { pc, minor, score };
      } else if (score > second) second = score;
    }
  }
  return {
    key: NOTES[best.pc] + (best.minor ? ' minor' : ' major'),
    root: NOTES[best.pc],
    scale: best.minor ? 'minor' : 'major',
    camelot: camelot(best.pc, best.minor),
    // How clear the win was, not how good the fit was: a track that correlates
    // 0.9 with two neighbouring keys is not a confident answer.
    confidence: Math.round(Math.max(0, best.score - second) * 100) / 100,
    chroma: c,
  };
}

/* Both passes over one buffer. Tempo reads a long excerpt because it needs many
 * bars; key reads a shorter one because Goertzel over 60 bins is the expensive
 * half and 30 seconds is plenty of harmony. */
function analyze(buffer) {
  const sr = buffer.sampleRate;
  const mono = toMono(buffer);
  const tempo = detectTempo(excerpt(mono, sr, 60), sr);
  const key = detectKey(excerpt(mono, sr, 30), sr);
  // detectTempo saw a window from the middle; move its phase back to the start
  // of the buffer so the deck can lay a grid over the whole track.
  const used = Math.min(mono.length, Math.floor(60 * sr));
  const skipped = (mono.length - used) / 2 / sr;
  let offset = tempo.offset;
  if (tempo.bpm && skipped > 0) {
    const beat = 60 / tempo.bpm;
    offset = ((skipped + tempo.offset) % beat + beat) % beat;
  }
  return {
    bpm: tempo.bpm,
    bpmConfidence: tempo.confidence,
    offset,
    key: key.key,
    camelot: key.camelot,
    keyConfidence: key.confidence,
  };
}

M.analyze = {
  toMono, decimate, excerpt, onsetEnvelope, detectTempo,
  chroma, detectKey, camelot, analyze, NOTES,
};

})(typeof globalThis !== 'undefined' ? globalThis : this);
