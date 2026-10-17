/* musica — the voices the sequencer plays.
 *
 * Every drum here is oscillators and filtered noise built at play time, which
 * is why the module ships with no audio assets at all (Mod.KIT in mod.py is the
 * same list, and `m musica/kit` prints it). Drop a file on a channel and it
 * becomes a sampler instead — that is the one voice with a buffer behind it.
 *
 * Each voice is a function (ctx, dest, time, opts) that schedules its own nodes
 * and lets them fall off the graph when they finish. Nothing is pooled: a few
 * dozen short-lived nodes per bar is well inside what Web Audio is built for,
 * and the alternative is voice-stealing bugs at 2am.
 */
(function (root) {
'use strict';
const M = root.MUSICA || (root.MUSICA = {});

/* One second of noise, made once per context and shared by every voice that
 * needs it. Generating it per hit was measurably the most expensive thing the
 * sequencer did. */
const NOISE = new WeakMap();
function noiseBuffer(ctx) {
  let b = NOISE.get(ctx);
  if (!b) {
    b = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    NOISE.set(ctx, b);
  }
  return b;
}

function noise(ctx, t, dur) {
  const s = ctx.createBufferSource();
  s.buffer = noiseBuffer(ctx);
  s.loop = true;
  s.playbackRate.value = 1;
  s.start(t, Math.random() * 0.5);
  s.stop(t + dur + 0.02);
  return s;
}

/* An exponential decay to (near) silence. exponentialRampToValueAtTime cannot
 * reach zero, so every envelope lands on a small floor and is then cut. */
function decay(ctx, t, peak, dur) {
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t + 0.003);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  g.gain.setValueAtTime(0, t + dur + 0.005);
  return g;
}

function osc(ctx, type, freq, t) {
  const o = ctx.createOscillator();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  return o;
}

function band(ctx, type, freq, q) {
  const f = ctx.createBiquadFilter();
  f.type = type; f.frequency.value = freq;
  if (q != null) f.Q.value = q;
  return f;
}

/* ── the kit ──────────────────────────────────────────────────────────── */

const VOICES = {
  /* sine with a pitch drop, 110→45Hz */
  kick(ctx, dest, t, o) {
    const v = o.velocity, dur = 0.34 * (o.decay || 1);
    const s = osc(ctx, 'sine', 110, t);
    s.frequency.exponentialRampToValueAtTime(45, t + 0.09);
    const g = decay(ctx, t, 0.9 * v, dur);
    // A click of noise at the top, or it disappears on laptop speakers.
    const n = noise(ctx, t, 0.01);
    const nf = band(ctx, 'bandpass', 1800, 1);
    const ng = decay(ctx, t, 0.18 * v, 0.012);
    s.connect(g); g.connect(dest);
    n.connect(nf); nf.connect(ng); ng.connect(dest);
    s.start(t); s.stop(t + dur + 0.02);
  },

  /* noise burst through a bandpass plus a 190Hz body */
  snare(ctx, dest, t, o) {
    const v = o.velocity, dur = 0.19 * (o.decay || 1);
    const n = noise(ctx, t, dur);
    const nf = band(ctx, 'bandpass', 1750, 0.7);
    const ng = decay(ctx, t, 0.55 * v, dur);
    const b = osc(ctx, 'triangle', 190, t);
    b.frequency.exponentialRampToValueAtTime(140, t + 0.08);
    const bg = decay(ctx, t, 0.4 * v, 0.1);
    n.connect(nf); nf.connect(ng); ng.connect(dest);
    b.connect(bg); bg.connect(dest);
    b.start(t); b.stop(t + 0.12);
  },

  /* three noise bursts 9ms apart into a longer tail */
  clap(ctx, dest, t, o) {
    const v = o.velocity;
    const f = band(ctx, 'bandpass', 1100, 0.9);
    f.connect(dest);
    for (let i = 0; i < 3; i++) {
      const at = t + i * 0.009;
      const n = noise(ctx, at, 0.02);
      const g = decay(ctx, at, 0.4 * v, 0.022);
      n.connect(g); g.connect(f);
    }
    const n = noise(ctx, t + 0.027, 0.16);
    const g = decay(ctx, t + 0.027, 0.3 * v, 0.16);
    n.connect(g); g.connect(f);
  },

  /* six detuned squares through a highpass — closed or open */
  hat(ctx, dest, t, o) {
    const open = !!o.open;
    const dur = open ? 0.3 : 0.055;
    const v = o.velocity;
    const hp = band(ctx, 'highpass', 7200, 0.8);
    const g = decay(ctx, t, 0.26 * v, dur);
    hp.connect(g); g.connect(dest);
    // The classic 808 ratios — inharmonic on purpose, that is the metal.
    const ratios = [2, 3, 4.16, 5.43, 6.79, 8.21];
    for (const r of ratios) {
      const s = osc(ctx, 'square', 40 * r, t);
      s.connect(hp);
      s.start(t); s.stop(t + dur + 0.02);
    }
  },

  /* tuned sine drop */
  tom(ctx, dest, t, o) {
    const v = o.velocity, dur = 0.32;
    const base = o.freq || 200;
    const s = osc(ctx, 'sine', base, t);
    s.frequency.exponentialRampToValueAtTime(base * 0.45, t + 0.16);
    const g = decay(ctx, t, 0.6 * v, dur);
    s.connect(g); g.connect(dest);
    s.start(t); s.stop(t + dur + 0.02);
  },

  /* short bandpassed click at 1.7kHz */
  rim(ctx, dest, t, o) {
    const v = o.velocity;
    const n = noise(ctx, t, 0.03);
    const f = band(ctx, 'bandpass', 1700, 6);
    const g = decay(ctx, t, 0.5 * v, 0.03);
    const s = osc(ctx, 'square', 420, t);
    n.connect(f); s.connect(f); f.connect(g); g.connect(dest);
    s.start(t); s.stop(t + 0.03);
  },

  /* two squares at 540 and 800Hz */
  cowbell(ctx, dest, t, o) {
    const v = o.velocity, dur = 0.24;
    const f = band(ctx, 'bandpass', 2600, 1.2);
    const g = decay(ctx, t, 0.34 * v, dur);
    f.connect(g); g.connect(dest);
    for (const hz of [540, 800]) {
      const s = osc(ctx, 'square', hz, t);
      s.connect(f);
      s.start(t); s.stop(t + dur + 0.02);
    }
  },

  /* subtractive synth voice on the piano roll — saw/square into a resonant
   * lowpass with its own envelope */
  bass(ctx, dest, t, o) {
    return tone(ctx, dest, t, o, {
      type: 'sawtooth', cutoff: 260, env: 1500, q: 9, sub: true, release: 0.09,
    });
  },

  /* the same voice, brighter and polyphonic */
  lead(ctx, dest, t, o) {
    return tone(ctx, dest, t, o, {
      type: 'square', cutoff: 700, env: 3400, q: 5, detune: 7, release: 0.16,
    });
  },

  /* any file you drop on the channel, pitched by the roll */
  sampler(ctx, dest, t, o) {
    if (!o.buffer) return;
    const s = ctx.createBufferSource();
    s.buffer = o.buffer;
    // Middle C is the buffer's own pitch; the roll transposes from there.
    s.playbackRate.value = Math.pow(2, ((o.note == null ? 60 : o.note) - 60) / 12);
    const dur = o.dur ? Math.min(o.dur, o.buffer.duration / s.playbackRate.value)
                      : o.buffer.duration / s.playbackRate.value;
    const g = ctx.createGain();
    g.gain.setValueAtTime(o.velocity, t);
    // A short fade rather than a hard stop, which would click.
    g.gain.setValueAtTime(o.velocity, t + Math.max(0.01, dur - 0.01));
    g.gain.linearRampToValueAtTime(0, t + dur + 0.008);
    s.connect(g); g.connect(dest);
    s.start(t);
    s.stop(t + dur + 0.02);
  },
};

/* The shared subtractive voice behind bass and lead. */
function tone(ctx, dest, t, o, spec) {
  const note = o.note == null ? 48 : o.note;
  const freq = 440 * Math.pow(2, (note - 69) / 12);
  const dur = Math.max(0.04, o.dur || 0.2);
  const rel = spec.release;
  const v = o.velocity;

  const filt = band(ctx, 'lowpass', spec.cutoff, spec.q);
  const amp = ctx.createGain();
  filt.connect(amp); amp.connect(dest);

  // Amp envelope: quick attack, hold for the note, then release.
  amp.gain.setValueAtTime(0, t);
  amp.gain.linearRampToValueAtTime(0.34 * v, t + 0.008);
  amp.gain.setValueAtTime(0.34 * v, t + dur);
  amp.gain.linearRampToValueAtTime(0, t + dur + rel);

  // Filter envelope — the reason this sounds like a synth and not an organ.
  const peak = Math.min(ctx.sampleRate / 2 - 1000, spec.cutoff + spec.env * v);
  filt.frequency.setValueAtTime(spec.cutoff, t);
  filt.frequency.linearRampToValueAtTime(peak, t + 0.02);
  filt.frequency.exponentialRampToValueAtTime(
    Math.max(80, spec.cutoff), t + Math.min(dur + rel, 0.5));

  const oscs = [];
  const main = osc(ctx, spec.type, freq, t);
  oscs.push(main);
  if (spec.detune) {
    const b = osc(ctx, spec.type, freq, t);
    b.detune.value = spec.detune;
    const c = osc(ctx, spec.type, freq, t);
    c.detune.value = -spec.detune;
    oscs.push(b, c);
  }
  if (spec.sub) {
    const s = osc(ctx, 'sine', freq / 2, t);
    oscs.push(s);
  }
  for (const s of oscs) {
    s.connect(filt);
    s.start(t);
    s.stop(t + dur + rel + 0.05);
  }
}

/* The order the rack is built in. `sampler` is last because it is the one you
 * make yourself by dropping a file, and an empty sampler makes no sound. */
const KIT = [
  { name: 'kick',    voice: 'kick',    note: 36 },
  { name: 'snare',   voice: 'snare',   note: 38 },
  { name: 'clap',    voice: 'clap',    note: 39 },
  { name: 'hat',     voice: 'hat',     note: 42 },
  { name: 'open hat',voice: 'hat',     note: 46, opts: { open: true } },
  { name: 'tom',     voice: 'tom',     note: 45 },
  { name: 'rim',     voice: 'rim',     note: 37 },
  { name: 'cowbell', voice: 'cowbell', note: 56 },
  { name: 'bass',    voice: 'bass',    note: 36, pitched: true },
  { name: 'lead',    voice: 'lead',    note: 60, pitched: true },
  { name: 'sampler', voice: 'sampler', note: 60, pitched: true, sample: true },
];

M.synth = {
  KIT,
  voices: VOICES,
  noiseBuffer,
  /* Fire one voice. Unknown names are ignored rather than thrown: a pattern
   * loaded from an older build should not silence the whole sequencer. */
  play(ctx, dest, name, t, opts) {
    const fn = VOICES[name];
    if (!fn) return false;
    const o = Object.assign({ velocity: 0.8 }, opts || {});
    fn(ctx, dest, t, o);
    return true;
  },
};

})(typeof globalThis !== 'undefined' ? globalThis : this);
