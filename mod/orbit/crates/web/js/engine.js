/* crates — the audio engine.
 *
 * One AudioContext, two decks, and a master bus. The graph is built exactly as
 * Mod.CHAIN in mod.py describes it, because `m crates/decks` is documentation
 * that people read before they read this file:
 *
 *   source → trim → eq.low → eq.mid → eq.high → filter → fader → cross → master
 *
 * Nothing here touches the DOM. ui.js and app.js drive it; keeping the split
 * clean is what lets tests/engine.mjs load the analysis half under node.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});

/* Knob positions are 0..1 and the ear is logarithmic, so every mapping from a
 * control to a parameter lives here rather than being open-coded per caller. */

// EQ: 0 kills the band, 0.5 is flat, 1 is +6dB. Full cut is -40dB rather than
// -Infinity because BiquadFilter.gain takes a finite number.
function eqDb(v) {
  return v <= 0.5 ? -40 + (v / 0.5) * 40 : (v - 0.5) / 0.5 * 6;
}

// The filter knob is bipolar: one control, two filters. Left sweeps a lowpass
// down from open, right sweeps a highpass up from closed. Dead centre is both
// filters parked out of the way, which is a true bypass to the ear.
function filterHz(v, nyquist) {
  const top = Math.min(20000, nyquist - 1);
  if (v < 0) return { lp: 200 * Math.pow(top / 200, 1 + v), hp: 20 };
  if (v > 0) return { lp: top, hp: 20 * Math.pow(8000 / 20, v) };
  return { lp: top, hp: 20 };
}

// Constant power: both legs sit at -3dB in the centre, so a beat playing on
// both decks does not jump in level as the fader crosses.
function crossGains(x) {
  return { a: Math.cos(x * Math.PI / 2), b: Math.sin(x * Math.PI / 2) };
}

M.curves = { eqDb, filterHz, crossGains };

/* ── one deck ─────────────────────────────────────────────────────────── */

class Deck {
  constructor(engine, id) {
    this.id = id;
    this.eng = engine;
    const ctx = engine.ctx;

    this.buffer = null;
    this.name = '';
    this.playing = false;
    this.offset = 0;          // buffer position, in buffer seconds
    this.startedAt = 0;       // ctx time the current source was anchored at
    this.rate = 1;            // playbackRate — pitch fader and sync both write it
    this.pitch = 0;           // percent, what the fader shows
    this.src = null;
    this.cues = [null, null, null, null];
    this.cuePoint = 0;        // the transport cue — where CUE returns to
    this.loop = null;         // {start, end, beats}
    this.bpm = null;          // detected
    this.beatOffset = 0;      // seconds to the first detected beat
    this.key = null;
    this.peaks = null;

    this.trim = ctx.createGain();
    this.low = ctx.createBiquadFilter();
    this.low.type = 'lowshelf'; this.low.frequency.value = 120;
    this.mid = ctx.createBiquadFilter();
    this.mid.type = 'peaking'; this.mid.frequency.value = 1000; this.mid.Q.value = 0.9;
    this.high = ctx.createBiquadFilter();
    this.high.type = 'highshelf'; this.high.frequency.value = 6000;
    this.lp = ctx.createBiquadFilter();
    this.lp.type = 'lowpass'; this.lp.Q.value = 6;
    this.hp = ctx.createBiquadFilter();
    this.hp.type = 'highpass'; this.hp.Q.value = 6;
    this.fader = ctx.createGain(); this.fader.gain.value = 0.85;
    this.cross = ctx.createGain();
    this.cueTap = ctx.createGain(); this.cueTap.gain.value = 0;

    this.trim.connect(this.low); this.low.connect(this.mid);
    this.mid.connect(this.high); this.high.connect(this.lp);
    this.lp.connect(this.hp);
    this.hp.connect(this.fader);
    this.fader.connect(this.cross);
    this.cross.connect(engine.program);
    // Pre-fader, as a real booth cues it: the send is tapped before the channel
    // fader so you can hear a track you have not brought up yet.
    this.hp.connect(this.cueTap);
    this.cueTap.connect(engine.cueBus);

    this.setFilter(0);
  }

  get duration() { return this.buffer ? this.buffer.duration : 0; }

  /* Where the playhead is, in buffer seconds. Derived from the context clock
   * rather than tracked by a timer — the audio thread is the only clock that
   * does not drift. */
  position() {
    if (!this.buffer) return 0;
    if (!this.playing) return this.offset;
    let p = this.offset + (this.eng.ctx.currentTime - this.startedAt) * this.rate;
    const L = this.loop;
    if (L && L.end > L.start) {
      if (p >= L.start) p = L.start + ((p - L.start) % (L.end - L.start));
    }
    return Math.max(0, Math.min(p, this.duration));
  }

  load(buffer, name) {
    this.stop();
    this.buffer = buffer;
    this.name = name || '';
    this.offset = 0;
    this.cuePoint = 0;
    this.loop = null;
    this.cues = [null, null, null, null];
    this.peaks = M.peaks(buffer, 2400);
    // A second array at ~160 buckets a second for the zoom view: four seconds
    // of it is 640 columns, which is what makes a transient look like one.
    this.fine = M.peaks(buffer, Math.max(2400, Math.min(240000, Math.ceil(buffer.duration * 160))));
    return this;
  }

  /* Re-anchor the transport without an audible seam. Anything that changes the
   * rate has to do this first, or position() would rewrite history: it measures
   * elapsed context time times the *current* rate. */
  _reanchor() {
    if (!this.playing) return;
    this.offset = this.position();
    this.startedAt = this.eng.ctx.currentTime;
  }

  _spawn(when, from) {
    const ctx = this.eng.ctx;
    const src = ctx.createBufferSource();
    src.buffer = this.buffer;
    src.playbackRate.value = this.rate;
    if (this.loop && this.loop.end > this.loop.start) {
      src.loop = true;
      src.loopStart = this.loop.start;
      src.loopEnd = this.loop.end;
    }
    src.connect(this.trim);
    src.start(when, Math.max(0, Math.min(from, this.duration - 0.001)));
    src.onended = () => { if (src === this.src) this._ended(); };
    this.src = src;
    this.startedAt = when;
    this.offset = from;
    this.playing = true;
  }

  _ended() {
    // Reached the end of the buffer on its own — park at the end, stopped.
    if (!this.playing) return;
    this.playing = false;
    this.src = null;
    this.offset = this.duration;
    if (this.onended) this.onended();
  }

  play(when) {
    if (!this.buffer || this.playing) return;
    if (this.offset >= this.duration - 0.01) this.offset = 0;
    this._spawn(when || this.eng.ctx.currentTime, this.offset);
  }

  stop() {
    if (!this.playing) return;
    const p = this.position();
    this.playing = false;
    if (this.src) { try { this.src.onended = null; this.src.stop(); } catch (e) {} }
    this.src = null;
    this.offset = p;
  }

  toggle() { this.playing ? this.stop() : this.play(); }

  seek(t) {
    t = Math.max(0, Math.min(t, this.duration));
    if (this.playing) { this.stop(); this.offset = t; this.play(); }
    else this.offset = t;
  }

  setPitch(pct) {
    this.pitch = pct;
    this._reanchor();
    this.rate = 1 + pct / 100;
    if (this.src) this.src.playbackRate.value = this.rate;
  }

  /* Effective tempo: the detected BPM as it is actually coming out, pitched. */
  liveBpm() { return this.bpm ? this.bpm * this.rate : null; }

  setTrim(v) { this.trim.gain.value = v; }
  setEq(band, v) {
    const node = { low: this.low, mid: this.mid, high: this.high }[band];
    if (node) node.gain.value = eqDb(v);
  }
  setFilter(v) {
    const f = filterHz(v, this.eng.ctx.sampleRate / 2);
    this.lp.frequency.value = f.lp;
    this.hp.frequency.value = f.hp;
    // Resonance only when the filter is actually doing something, otherwise a
    // parked filter colours the signal it is meant to be passing through.
    const q = Math.abs(v) < 0.02 ? 0.0001 : 0.7 + Math.abs(v) * 6;
    this.lp.Q.value = q; this.hp.Q.value = q;
  }
  setFader(v) { this.fader.gain.value = v; }
  setCue(on) { this.cueTap.gain.value = on ? 1 : 0; }

  /* Loop over `beats` at the deck's own tempo, anchored to where the playhead
   * is now, quantised back to the nearest beat in the detected grid. */
  setLoop(beats) {
    if (!this.buffer) return null;
    const bpm = this.bpm || 120;
    const beat = 60 / bpm;
    const p = this.position();
    let start = p;
    // Snap to the beat grid so a loop taken mid-bar still lands musically.
    const n = Math.round((p - this.beatOffset) / beat);
    const snapped = this.beatOffset + n * beat;
    if (Math.abs(snapped - p) < beat * 0.5 && snapped >= 0) start = snapped;
    const end = Math.min(start + beats * beat, this.duration);
    if (end <= start) return null;
    this.loop = { start, end, beats };
    if (this.playing) {
      // Restart inside the loop so the source picks up loopStart/loopEnd.
      const at = start + ((p - start) % (end - start) + (end - start)) % (end - start);
      this.stop(); this.offset = at; this.play();
    }
    return this.loop;
  }

  clearLoop() {
    if (!this.loop) return;
    const p = this.position();
    this.loop = null;
    if (this.playing) { this.stop(); this.offset = p; this.play(); }
  }

  setCuePoint(i) { this.cues[i] = this.position(); return this.cues[i]; }
  jumpCue(i) { if (this.cues[i] != null) this.seek(this.cues[i]); }

  /* The CUE button, as a booth works it: pressed while playing it drops you
   * back to the cue point and stops; pressed while stopped it moves the cue
   * point here and previews from it until you let go. */
  cueDown() {
    if (this.playing) { this.stop(); this.seek(this.cuePoint); return 'return'; }
    this.cuePoint = this.position();
    this.play();
    return 'preview';
  }
  cueUp() {
    if (!this.playing) return;
    this.stop();
    this.seek(this.cuePoint);
  }

  /* Beat-match the other deck: take its live tempo, then shift this deck so the
   * two grids line up. Tempo alone is not sync — the downbeats have to land
   * together or you have two tracks at the same speed and no mix. */
  syncTo(other) {
    const mine = this.bpm, theirs = other.liveBpm();
    if (!mine || !theirs || !this.buffer) return false;
    let target = theirs;
    // Pull double- and half-time detections back into range before pitching.
    while (target / mine > 1.4) target /= 2;
    while (target / mine < 0.7) target *= 2;
    const pct = (target / mine - 1) * 100;
    if (Math.abs(pct) > 16) return false;   // outside the pitch fader's travel
    this.setPitch(pct);
    if (!other.playing || !this.playing) return true;

    const beat = 60 / target;
    const theirPhase = M.phase(other.position() - other.beatOffset, 60 / other.bpm);
    const mineAt = this.position();
    const minePhase = M.phase(mineAt - this.beatOffset, beat);
    let delta = (theirPhase - minePhase) * beat;
    if (delta > beat / 2) delta -= beat;
    if (delta < -beat / 2) delta += beat;
    this.seek(Math.max(0, mineAt + delta));
    return true;
  }
}

/* Fractional position within a beat, 0..1. */
M.phase = function (t, beat) {
  if (!beat) return 0;
  const p = (t / beat) % 1;
  return p < 0 ? p + 1 : p;
};

/* ── the engine ───────────────────────────────────────────────────────── */

class Engine {
  constructor(ctx) {
    const AC = root.AudioContext || root.webkitAudioContext;
    this.ctx = ctx || new AC({ latencyHint: 'interactive' });
    const c = this.ctx;

    this.program = c.createGain();     // the two decks + the sequencer
    this.cueBus = c.createGain();      // pre-fader monitor
    this.master = c.createGain(); this.master.gain.value = 0.9;

    // A gentle limiter, not a loudness war: it exists so a hot deck plus a
    // sequencer full of kicks does not clip the output device.
    this.limiter = c.createDynamicsCompressor();
    this.limiter.threshold.value = -3;
    this.limiter.knee.value = 0;
    this.limiter.ratio.value = 20;
    this.limiter.attack.value = 0.003;
    this.limiter.release.value = 0.25;

    // One global echo, fed from the program bus. Its time is set from the
    // sequencer's BPM so it stays in the pocket when the tempo changes.
    this.fxSend = c.createGain(); this.fxSend.gain.value = 0;
    this.delay = c.createDelay(2);
    this.delay.delayTime.value = 0.375;
    this.feedback = c.createGain(); this.feedback.gain.value = 0.38;
    this.fxTone = c.createBiquadFilter();
    this.fxTone.type = 'highpass'; this.fxTone.frequency.value = 260;

    this.program.connect(this.master);
    this.cueBus.connect(this.master);
    this.program.connect(this.fxSend);
    this.fxSend.connect(this.delay);
    this.delay.connect(this.fxTone);
    this.fxTone.connect(this.feedback);
    this.feedback.connect(this.delay);
    this.fxTone.connect(this.master);

    this.master.connect(this.limiter);
    this.limiter.connect(c.destination);

    // Metering taps the post-limiter signal, which is what actually leaves.
    this.split = c.createChannelSplitter(2);
    this.limiter.connect(this.split);
    this.anL = c.createAnalyser(); this.anL.fftSize = 1024;
    this.anR = c.createAnalyser(); this.anR.fftSize = 1024;
    this.split.connect(this.anL, 0);
    this.split.connect(this.anR, 1);
    this._mbuf = new Float32Array(this.anL.fftSize);

    this.decks = { A: new Deck(this, 'A'), B: new Deck(this, 'B') };
    this.setCrossfader(0.5);
    this.cueing = false;
  }

  get currentTime() { return this.ctx.currentTime; }
  resume() { return this.ctx.state === 'suspended' ? this.ctx.resume() : Promise.resolve(); }

  setCrossfader(x) {
    const g = crossGains(x);
    this.decks.A.cross.gain.value = g.a;
    this.decks.B.cross.gain.value = g.b;
    this.xfade = x;
  }

  setMaster(v) { this.master.gain.value = v; }
  setEcho(v) {
    this.fxSend.gain.value = v * 0.85;
    this.feedback.gain.value = 0.2 + v * 0.35;
  }
  /* A dotted-eighth echo, the one every DJ reaches for. */
  echoToBpm(bpm) { this.delay.delayTime.value = Math.min(2, 60 / bpm * 0.75); }

  /* With one output device there is no separate headphone bus, so soloing has
   * to duck the program rather than route somewhere else. mod.py says as much
   * in decks(); this is where that limitation actually lives. */
  updateCue() {
    const on = Object.values(this.decks).some(d => d.cueTap.gain.value > 0);
    this.cueing = on;
    const t = this.ctx.currentTime;
    this.program.gain.setTargetAtTime(on ? 0.12 : 1, t, 0.02);
  }

  /* Peak level per side, 0..1, for the meters. */
  levels() {
    const read = (an) => {
      an.getFloatTimeDomainData(this._mbuf);
      let peak = 0;
      for (let i = 0; i < this._mbuf.length; i++) {
        const v = Math.abs(this._mbuf[i]);
        if (v > peak) peak = v;
      }
      return Math.min(1, peak);
    };
    return { l: read(this.anL), r: read(this.anR) };
  }

  decode(arrayBuffer) {
    // Safari still wants the callback form; the promise form is the standard.
    return new Promise((res, rej) => {
      const p = this.ctx.decodeAudioData(arrayBuffer, res, rej);
      if (p && p.then) p.then(res, rej);
    });
  }

  /* A stream of the master for MediaRecorder. Created lazily — asking for one
   * on a context that is never recorded costs a node for nothing. */
  recordStream() {
    if (!this._rec) {
      this._rec = this.ctx.createMediaStreamDestination();
      this.limiter.connect(this._rec);
    }
    return this._rec.stream;
  }
}

/* Min/max peaks for waveform drawing, computed once per load. Drawing straight
 * from the buffer would read millions of samples on every animation frame. */
M.peaks = function (buffer, buckets) {
  const ch = buffer.numberOfChannels > 1
    ? [buffer.getChannelData(0), buffer.getChannelData(1)]
    : [buffer.getChannelData(0)];
  const n = buffer.length;
  const size = Math.max(1, Math.floor(n / buckets));
  const out = new Float32Array(buckets * 2);
  for (let b = 0; b < buckets; b++) {
    const s = b * size, e = Math.min(n, s + size);
    let min = 0, max = 0;
    for (let i = s; i < e; i++) {
      let v = ch[0][i];
      if (ch[1]) v = (v + ch[1][i]) * 0.5;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    out[b * 2] = min; out[b * 2 + 1] = max;
  }
  return out;
};

M.Engine = Engine;
M.Deck = Deck;

})(typeof globalThis !== 'undefined' ? globalThis : this);
