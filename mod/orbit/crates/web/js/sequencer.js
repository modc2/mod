/* crates — the step sequencer and piano roll, in the FL Studio idiom.
 *
 * Eight patterns, each a rack of channels over 16/32/64 steps, plus a piano
 * roll for the pitched voices. The clock is the standard Web Audio two-clock
 * arrangement: a coarse setInterval wakes up often enough to schedule ahead,
 * and every note is stamped with an exact sample-accurate time from the audio
 * context. setInterval alone would swing by tens of milliseconds and you would
 * hear it on every hat.
 *
 * The grid maths at the top is pure and has no context — tests/engine.mjs runs
 * it under node, because a beat grid that is subtly wrong is the kind of bug
 * that only shows up as "the loop feels off" three tracks later.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});

const LOOKAHEAD = 0.12;     // seconds of audio scheduled in advance
const TICK = 25;            // ms between scheduler wakeups

/* ── grid ─────────────────────────────────────────────────────────────── */

/* One step is a 16th note. */
function stepDur(bpm) { return 60 / bpm / 4; }

/* Where step k falls, in seconds from step 0.
 *
 * Swing delays the off-16ths and leaves the on-beats alone, which is the only
 * way it stays a groove rather than a tempo change. At the maximum of 0.75 the
 * offbeat sits 37.5% of a step late — past a triplet feel, not yet broken. */
function stepTime(k, bpm, swing) {
  const d = stepDur(bpm);
  return k * d + ((k % 2) ? (swing || 0) * d * 0.5 : 0);
}

/* Bars and beats for the transport readout, 1-indexed the way musicians count. */
function barsBeats(step, stepsPerPattern) {
  const s = ((step % stepsPerPattern) + stepsPerPattern) % stepsPerPattern;
  return { bar: Math.floor(s / 16) + 1, beat: Math.floor((s % 16) / 4) + 1 };
}

M.grid = { stepDur, stepTime, barsBeats, LOOKAHEAD, TICK };

/* ── pattern data ─────────────────────────────────────────────────────── */

const OFF = 0, ON = 1, ACCENT = 2;

function makePattern(steps, channels) {
  const p = { steps, rows: {}, notes: {} };
  for (const c of channels) {
    p.rows[c.name] = new Uint8Array(steps);
    p.notes[c.name] = [];
  }
  return p;
}

/* Resize keeps what fits and, when growing, repeats what was there — doubling
 * a 16-step pattern to 32 should give you the same bar twice, not a bar of
 * silence to fill in again. */
function resize(pattern, steps) {
  const out = {};
  for (const name of Object.keys(pattern.rows)) {
    const old = pattern.rows[name];
    const row = new Uint8Array(steps);
    for (let i = 0; i < steps; i++) row[i] = old.length ? old[i % old.length] : OFF;
    out[name] = row;
  }
  pattern.rows = out;
  for (const name of Object.keys(pattern.notes)) {
    pattern.notes[name] = pattern.notes[name].filter(n => n.start < steps);
  }
  pattern.steps = steps;
  return pattern;
}

/* ── the sequencer ────────────────────────────────────────────────────── */

class Sequencer {
  constructor(engine) {
    this.eng = engine;
    this.ctx = engine.ctx;

    // Its own bus into the program sum, so the decks and the studio can be
    // balanced against each other without touching either one's levels.
    this.out = this.ctx.createGain();
    this.out.gain.value = 0.9;
    this.out.connect(engine.program);

    this.channels = M.synth.KIT.map(k => Object.assign({
      mute: false, volume: 0.8, buffer: null, label: null,
    }, k));

    this.patterns = [];
    for (let i = 0; i < 8; i++) this.patterns.push(makePattern(16, this.channels));
    this.current = 0;

    this.bpm = 126;
    this.swing = 0;
    this.playing = false;
    this.step = 0;          // absolute step counter since start
    this.anchor = 0;        // ctx time of step 0 of this run
    this._timer = null;
    this.onStep = null;     // (stepInPattern, time) — the UI's playhead
  }

  get pattern() { return this.patterns[this.current]; }
  get steps() { return this.pattern.steps; }

  channel(name) { return this.channels.find(c => c.name === name); }

  /* Re-anchor so the tempo can change mid-bar without the next step jumping.
   * The step we are about to schedule keeps its wall-clock time; everything
   * after it is spaced by the new tempo. */
  setBpm(bpm) {
    bpm = Math.max(40, Math.min(220, bpm));
    if (this.playing) {
      const nextAt = this.anchor + stepTime(this.step, this.bpm, this.swing);
      this.anchor = nextAt - stepTime(this.step, bpm, this.swing);
    }
    this.bpm = bpm;
    this.eng.echoToBpm(bpm);
  }

  setSwing(v) {
    if (this.playing) {
      const nextAt = this.anchor + stepTime(this.step, this.bpm, this.swing);
      this.anchor = nextAt - stepTime(this.step, this.bpm, v);
    }
    this.swing = v;
  }

  start() {
    if (this.playing) return;
    this.playing = true;
    this.step = 0;
    // A beat of headroom: the first scheduled note must be in the future or the
    // context plays it late and the pattern starts with a stumble.
    this.anchor = this.ctx.currentTime + 0.08;
    this._timer = setInterval(() => this._schedule(), TICK);
    this._schedule();
  }

  stop() {
    if (!this.playing) return;
    this.playing = false;
    clearInterval(this._timer);
    this._timer = null;
    if (this.onStep) this.onStep(-1, this.ctx.currentTime);
  }

  toggle() { this.playing ? this.stop() : this.start(); }

  _schedule() {
    const horizon = this.ctx.currentTime + LOOKAHEAD;
    let guard = 0;
    while (this.playing && guard++ < 256) {
      const at = this.anchor + stepTime(this.step, this.bpm, this.swing);
      if (at >= horizon) break;
      this._fire(this.step % this.steps, at);
      if (this.onStep) this.onStep(this.step % this.steps, at);
      this.step++;
      // Fold the counter back every pattern so stepTime never works with a
      // number large enough to lose float precision in a long session.
      if (this.step >= this.steps) {
        this.anchor += stepTime(this.steps, this.bpm, this.swing);
        this.step = 0;
      }
    }
  }

  _fire(step, at) {
    const p = this.pattern;
    for (const c of this.channels) {
      if (c.mute) continue;
      const rollNotes = p.notes[c.name];
      // FL's rule: once a channel has piano roll data, the roll owns it and the
      // step row for that channel stops firing. Otherwise every note you draw
      // would double with the step underneath it.
      if (rollNotes && rollNotes.length) {
        for (const n of rollNotes) {
          if (Math.floor(n.start) !== step) continue;
          this._voice(c, at, n.velocity, n.note,
                      (n.length || 1) * stepDur(this.bpm));
        }
        continue;
      }
      const hit = p.rows[c.name] && p.rows[c.name][step];
      if (!hit) continue;
      this._voice(c, at, hit === ACCENT ? 1 : 0.72, c.note, null);
    }
  }

  _voice(c, at, vel, note, dur) {
    if (c.sample && !c.buffer) return;    // an empty sampler is silent
    const g = this.ctx.createGain();
    g.gain.value = c.volume * vel;
    g.connect(this.out);
    M.synth.play(this.ctx, g, c.voice, at, Object.assign({
      velocity: 1, note, dur, buffer: c.buffer,
    }, c.opts || {}));
  }

  /* Audition one channel now — what a click on its name should do. */
  preview(name) {
    const c = this.channel(name);
    if (!c) return;
    this._voice(c, this.ctx.currentTime + 0.01, 0.9, c.note, 0.25);
  }

  toggleStep(name, i, accent) {
    const row = this.pattern.rows[name];
    if (!row || i < 0 || i >= row.length) return null;
    if (accent) row[i] = row[i] === ACCENT ? OFF : ACCENT;
    else row[i] = row[i] ? OFF : ON;
    return row[i];
  }

  clear() {
    const p = this.pattern;
    for (const name of Object.keys(p.rows)) p.rows[name].fill(OFF);
    for (const name of Object.keys(p.notes)) p.notes[name] = [];
  }

  setSteps(n) { resize(this.pattern, n); }

  /* A beat to start from rather than a blank grid. Deterministic in shape and
   * random only in the details, so SEED always gives you something playable. */
  seed() {
    const p = this.pattern, n = p.steps;
    this.clear();
    const put = (name, fn) => {
      const row = p.rows[name];
      if (!row) return;
      for (let i = 0; i < n; i++) row[i] = fn(i) || OFF;
    };
    put('kick', i => (i % 4 === 0 ? ACCENT : (i % 16 === 14 && Math.random() < 0.5 ? ON : OFF)));
    put('clap', i => (i % 8 === 4 ? ON : OFF));
    put('hat', i => (i % 2 === 0 ? (i % 4 === 2 ? ACCENT : ON) : (Math.random() < 0.25 ? ON : OFF)));
    put('open hat', i => (i % 8 === 6 ? ON : OFF));
    put('rim', i => (Math.random() < 0.1 ? ON : OFF));
    // A bass line on the roll, so the piano roll is not empty on first look.
    const root = 36, scale = [0, 3, 5, 7, 10];
    p.notes['bass'] = [];
    for (let i = 0; i < n; i += 2) {
      if (Math.random() < 0.45) continue;
      p.notes['bass'].push({
        note: root + scale[Math.floor(Math.random() * scale.length)] - (i % 8 === 0 ? 0 : 0),
        start: i, length: 2, velocity: 0.85,
      });
    }
    return p;
  }

  addNote(name, note, start, length, velocity) {
    const list = this.pattern.notes[name];
    if (!list) return null;
    const n = {
      note, start,
      length: Math.max(1, length || 1),
      velocity: velocity == null ? 0.85 : velocity,
    };
    // One note per pitch per step — clicking a filled cell should not stack.
    const dup = list.findIndex(x => x.note === note && Math.floor(x.start) === Math.floor(start));
    if (dup >= 0) list.splice(dup, 1);
    list.push(n);
    return n;
  }

  removeNote(name, note, step) {
    const list = this.pattern.notes[name];
    if (!list) return false;
    const i = list.findIndex(x => x.note === note
      && step >= Math.floor(x.start) && step < x.start + x.length);
    if (i < 0) return false;
    list.splice(i, 1);
    return true;
  }

  noteAt(name, note, step) {
    const list = this.pattern.notes[name] || [];
    return list.find(x => x.note === note
      && step >= Math.floor(x.start) && step < x.start + x.length) || null;
  }

  /* Everything worth keeping across a reload. Buffers are deliberately left
   * out: a dropped file is not ours to persist. */
  serialize() {
    return {
      bpm: this.bpm, swing: this.swing, current: this.current,
      channels: this.channels.map(c => ({ name: c.name, mute: c.mute, volume: c.volume })),
      patterns: this.patterns.map(p => ({
        steps: p.steps,
        rows: Object.fromEntries(Object.entries(p.rows).map(([k, v]) => [k, Array.from(v)])),
        notes: p.notes,
      })),
    };
  }

  restore(data) {
    if (!data || !Array.isArray(data.patterns)) return false;
    try {
      this.bpm = data.bpm || this.bpm;
      this.swing = data.swing || 0;
      this.current = Math.min(7, Math.max(0, data.current | 0));
      (data.channels || []).forEach(s => {
        const c = this.channel(s.name);
        if (c) { c.mute = !!s.mute; c.volume = s.volume; }
      });
      data.patterns.forEach((s, i) => {
        const p = this.patterns[i];
        if (!p || !s) return;
        p.steps = s.steps || 16;
        for (const name of Object.keys(p.rows)) {
          const row = new Uint8Array(p.steps);
          const src = (s.rows || {})[name] || [];
          for (let k = 0; k < Math.min(p.steps, src.length); k++) row[k] = src[k] | 0;
          p.rows[name] = row;
        }
        for (const name of Object.keys(p.notes)) {
          p.notes[name] = ((s.notes || {})[name] || []).filter(
            n => n && typeof n.note === 'number');
        }
      });
      return true;
    } catch (e) {
      return false;
    }
  }
}

M.Sequencer = Sequencer;
M.pattern = { make: makePattern, resize, OFF, ON, ACCENT };

})(typeof globalThis !== 'undefined' ? globalThis : this);
