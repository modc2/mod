/* musica — wiring.
 *
 * Everything below binds the markup in index.html to the engine, the sequencer
 * and the crate. It is the only file that touches both sides, which is on
 * purpose: if a control does not work, it is broken here, and if it makes the
 * wrong sound, it is broken in engine.js or synth.js.
 */
(function (root) {
'use strict';
const M = root.MUSICA;
const doc = root.document;
const { $, $$, toast, knob, drawFull, drawZoom, drawRoll, rollHit, fmtTime, meter } = M.ui;

const app = {
  eng: null, seq: null, started: false,
  local: [],                 // decoded files, the only thing that can be mixed
  sel: 'bass',               // channel the piano roll is showing
  stepQueue: [],             // {step, time} the scheduler has queued ahead
  playStep: -1,
  meters: { l: {}, r: {} },
  rec: null, recStart: 0,
  taps: [],
};

/* ── boot ─────────────────────────────────────────────────────────────── */

/* Browsers refuse to start an AudioContext without a gesture, so the whole
 * console is built behind one button rather than being built and then silently
 * not making any sound. */
async function boot() {
  if (app.started) return;
  app.started = true;

  app.eng = new M.Engine();
  await app.eng.resume();
  app.seq = new M.Sequencer(app.eng);
  app.seq.onStep = (step, time) => app.stepQueue.push({ step, time });
  app.seq.setBpm(parseFloat($('#bpm').value) || 126);

  buildRack();
  buildRoll();
  bindTransport();
  bindDecks();
  bindMixer();
  bindStudio();
  bindCrate();
  bindKeys();

  $('#boot').classList.add('gone');
  requestAnimationFrame(render);

  // Restore a previous session's patterns, if the tab has been here before.
  try {
    const saved = localStorage.getItem('musica.studio');
    if (saved && app.seq.restore(JSON.parse(saved))) {
      $('#bpm').value = app.seq.bpm;
      $('#swing').value = app.seq.swing;
      syncStudioUI();
      toast('restored your last pattern');
    }
  } catch (e) { /* a corrupt save is not worth a broken console */ }

  version();
  spotifyNote();
}

async function version() {
  try {
    const info = await M.api.info();
    if (info && info.version) $('#ver').textContent = info.version;
  } catch (e) { /* the console works without the API; the crate will say so */ }
}

/* ── transport ────────────────────────────────────────────────────────── */

function bindTransport() {
  $('#seq-play').addEventListener('click', () => {
    app.seq.toggle();
    $('#seq-play').classList.toggle('on', app.seq.playing);
    $('#seq-play').textContent = app.seq.playing ? '■' : '▶';
    if (!app.seq.playing) { app.stepQueue.length = 0; app.playStep = -1; paintSteps(); }
  });

  $('#bpm').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    if (isFinite(v)) app.seq.setBpm(v);
  });

  /* Tap tempo over the last four intervals. Anything slower than two seconds
   * between taps starts a new count rather than averaging in a pause. */
  $('#tap').addEventListener('click', () => {
    const now = performance.now();
    if (app.taps.length && now - app.taps[app.taps.length - 1] > 2000) app.taps.length = 0;
    app.taps.push(now);
    if (app.taps.length > 5) app.taps.shift();
    if (app.taps.length < 2) return toast('keep tapping');
    const spans = [];
    for (let i = 1; i < app.taps.length; i++) spans.push(app.taps[i] - app.taps[i - 1]);
    const avg = spans.reduce((a, b) => a + b, 0) / spans.length;
    const bpm = Math.round(60000 / avg * 10) / 10;
    if (bpm >= 40 && bpm <= 220) {
      $('#bpm').value = bpm;
      app.seq.setBpm(bpm);
    }
  });

  $('#rec').addEventListener('click', toggleRecord);

  knob($('.master .knob[data-param="master"]'), (v) => app.eng.setMaster(v));
}

/* Record the master bus to a .webm you can drag out of the downloads shelf.
 * MediaRecorder is the only route that does not mean shipping an encoder. */
function toggleRecord() {
  const btn = $('#rec');
  if (app.rec) {
    app.rec.stop();
    return;
  }
  if (typeof MediaRecorder === 'undefined') return toast('this browser has no MediaRecorder');
  const stream = app.eng.recordStream();
  // Chrome and Firefox disagree on which container they will give you.
  const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg']
    .find(t => MediaRecorder.isTypeSupported(t));
  let rec;
  try {
    rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  } catch (e) {
    return toast('recording failed: ' + e.message);
  }
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  rec.onstop = () => {
    const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
    const url = URL.createObjectURL(blob);
    const a = doc.createElement('a');
    a.href = url;
    a.download = `musica-${new Date().toISOString().replace(/[:.]/g, '-')}.webm`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    app.rec = null;
    btn.classList.remove('on');
    $('#rec-time').textContent = '';
    toast('mix saved');
  };
  rec.start();
  app.rec = rec;
  app.recStart = performance.now();
  btn.classList.add('on');
  toast('recording the master bus');
}

/* ── decks ────────────────────────────────────────────────────────────── */

function deckEl(id) { return $(`.deck[data-deck="${id}"]`); }

function bindDecks() {
  for (const id of ['A', 'B']) {
    const el = deckEl(id), d = app.eng.decks[id];
    d.onended = () => paintDeck(id);

    $('.play', el).addEventListener('click', () => {
      if (!d.buffer) return toast(`deck ${id} is empty — drop a file on it`);
      d.toggle();
      paintDeck(id);
    });

    const cue = $('.cue-hold', el);
    cue.addEventListener('pointerdown', (e) => {
      if (!d.buffer) return;
      cue.setPointerCapture(e.pointerId);
      d.cueDown();
      paintDeck(id);
    });
    const release = (e) => {
      if (!d.buffer) return;
      try { cue.releasePointerCapture(e.pointerId); } catch (_) {}
      d.cueUp();
      paintDeck(id);
    };
    cue.addEventListener('pointerup', release);
    cue.addEventListener('pointercancel', release);

    $('.sync', el).addEventListener('click', () => {
      const other = app.eng.decks[id === 'A' ? 'B' : 'A'];
      if (!d.bpm) return toast(`deck ${id} has no tempo yet`);
      if (!other.liveBpm()) return toast('the other deck has no tempo to match');
      if (!d.syncTo(other)) return toast('that tempo is outside the pitch range');
      $('.pitch-fader', el).value = d.pitch.toFixed(2);
      paintDeck(id);
      toast(`deck ${id} synced to ${other.liveBpm().toFixed(1)}`);
    });

    const fader = $('.pitch-fader', el);
    fader.addEventListener('input', () => {
      d.setPitch(parseFloat(fader.value));
      $('.sync', el).classList.remove('on');
      paintDeck(id);
    });
    $('.pitch-reset', el).addEventListener('click', () => {
      d.setPitch(0); fader.value = 0; paintDeck(id);
    });
    $$('.nudge button[data-nudge]', el).forEach(b => {
      b.addEventListener('click', () => {
        const next = Math.max(-16, Math.min(16, d.pitch + parseFloat(b.dataset.nudge) * 0.05));
        d.setPitch(next); fader.value = next.toFixed(2); paintDeck(id);
      });
    });

    $$('.cue-btn', el).forEach(b => {
      const i = parseInt(b.dataset.cue, 10) - 1;
      b.addEventListener('click', () => {
        if (!d.buffer) return;
        // Set on first press, jump on every one after — and shift to clear.
        if (d.cues[i] == null) { d.setCuePoint(i); toast(`cue ${i + 1} set`); }
        else d.jumpCue(i);
        paintDeck(id);
      });
      b.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        d.cues[i] = null;
        paintDeck(id);
      });
    });

    $$('.loop-btn', el).forEach(b => {
      b.addEventListener('click', () => {
        if (!d.buffer) return;
        const beats = parseFloat(b.dataset.beats);
        if (d.loop && d.loop.beats === beats) d.clearLoop();
        else d.setLoop(beats);
        paintDeck(id);
      });
    });
    $('.loop-off', el).addEventListener('click', () => { d.clearLoop(); paintDeck(id); });

    // Click a waveform to seek. The zoom view is centred on the playhead, so a
    // click there is relative to now; the overview is absolute.
    $('.wave-full', el).addEventListener('pointerdown', (e) => {
      if (!d.buffer) return;
      const r = e.currentTarget.getBoundingClientRect();
      d.seek((e.clientX - r.left) / r.width * d.duration);
      if (!d.playing) d.cuePoint = d.position();
    });
    $('.wave-zoom', el).addEventListener('pointerdown', (e) => {
      if (!d.buffer) return;
      const r = e.currentTarget.getBoundingClientRect();
      d.seek(d.position() + ((e.clientX - r.left) / r.width - 0.5) * 4);
      if (!d.playing) d.cuePoint = d.position();
    });

    /* Every beat tracker gets the metrical level wrong sometimes, so the
     * readout is the control that fixes it: click to cycle ×2 and ÷2. Nothing
     * is re-analysed — only which level we are calling the beat changes, so the
     * grid, the loops and sync all follow. */
    const read = $('.bpm-read', el);
    read.title = 'detected tempo — click to halve or double it';
    read.style.cursor = 'pointer';
    const SCALES = [1, 2, 0.5];
    read.addEventListener('click', () => {
      if (!d.bpmBase) return;
      d.bpmScale = SCALES[(SCALES.indexOf(d.bpmScale || 1) + 1) % SCALES.length];
      d.bpm = d.bpmBase * d.bpmScale;
      // The grid's phase is a time, not a tempo, so it survives the change; the
      // loop is re-taken because its length is measured in beats.
      if (d.loop) { const b = d.loop.beats; d.clearLoop(); d.setLoop(b); }
      paintDeck(id);
      toast(`deck ${id} → ${d.bpm.toFixed(1)} BPM`);
    });

    dropTarget(el, 'drop', (files) => loadToDeck(id, files[0]));
  }
}

/* Drag and drop, with the highlight class the stylesheet already defines. */
function dropTarget(el, cls, onFiles) {
  let depth = 0;
  el.addEventListener('dragenter', (e) => {
    e.preventDefault(); depth++; el.classList.add(cls);
  });
  el.addEventListener('dragover', (e) => { e.preventDefault(); });
  el.addEventListener('dragleave', () => { if (--depth <= 0) el.classList.remove(cls); });
  el.addEventListener('drop', (e) => {
    // Decks and channels sit inside the body-wide drop target. Without this the
    // same file is handled twice — decoded twice, and listed twice in the crate.
    e.preventDefault(); e.stopPropagation();
    depth = 0; el.classList.remove(cls);
    const files = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith('audio/')
      || /\.(mp3|wav|flac|ogg|m4a|aac|aiff?)$/i.test(f.name));
    if (files.length) onFiles(files);
    else toast('that is not an audio file');
  });
}

async function decodeFile(file) {
  const buf = await file.arrayBuffer();
  return app.eng.decode(buf);
}

async function loadToDeck(id, entry) {
  const el = deckEl(id), d = app.eng.decks[id];
  $('.sub', el).textContent = 'decoding…';
  try {
    const rec = entry.buffer ? entry : await addLocal(entry);
    d.load(rec.buffer, rec.name);
    $('.title', el).textContent = rec.name;
    $('.sub', el).textContent = `${fmtTime(rec.buffer.duration)} · analysing…`;
    $('.bpm-read', el).textContent = '--.-';
    $('.key-read', el).textContent = '--';
    $('.pitch-fader', el).value = 0;
    paintDeck(id);
    const a = await analyzeBuffer(rec);
    d.bpm = a.bpm; d.bpmBase = a.bpm; d.bpmScale = 1;
    d.beatOffset = a.offset || 0; d.key = a.key;
    d.bpmConfidence = a.bpmConfidence; d.keyConfidence = a.keyConfidence;
    $('.sub', el).textContent = `${fmtTime(rec.buffer.duration)} · ${rec.name.split('.').pop()}`;
    paintDeck(id);
  } catch (e) {
    $('.sub', el).textContent = 'could not decode that file';
    toast('decode failed: ' + e.message);
  }
}

/* Analysis is a second or two of arithmetic on a long track, so it runs in a
 * worker built from analyze.js itself — the same file the page already loaded
 * and the same one tests/engine.mjs checks. If a worker cannot be made, it
 * falls back to the main thread and the tab just stutters once. */
let workerSrc = null;
async function analyzeBuffer(rec) {
  if (rec.analysis) return rec.analysis;
  const buffer = rec.buffer;
  const mono = M.analyze.toMono(buffer);
  let result = null;
  try {
    if (workerSrc === null) {
      workerSrc = await fetch('js/analyze.js').then(r => r.text());
    }
    result = await new Promise((res, rej) => {
      const src = workerSrc + `
self.onmessage = function (e) {
  var mono = e.data.mono, sr = e.data.sr;
  self.postMessage(self.MUSICA.analyze.analyze({
    length: mono.length, numberOfChannels: 1, sampleRate: sr,
    getChannelData: function () { return mono; },
  }));
};`;
      const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
      const w = new Worker(url);
      const done = (fn) => (v) => { w.terminate(); URL.revokeObjectURL(url); fn(v); };
      w.onmessage = (e) => done(res)(e.data);
      w.onerror = (e) => done(rej)(new Error(e.message || 'worker failed'));
      // The copy is deliberate: mono may be the AudioBuffer's own storage, and
      // transferring it would detach the buffer the deck is playing.
      const copy = new Float32Array(mono);
      w.postMessage({ mono: copy, sr: buffer.sampleRate }, [copy.buffer]);
    });
  } catch (e) {
    result = M.analyze.analyze(buffer);
  }
  rec.analysis = result;
  refreshLocal();
  return result;
}

function paintDeck(id) {
  const el = deckEl(id), d = app.eng.decks[id];
  $('.play', el).classList.toggle('on', d.playing);
  $('.play', el).textContent = d.playing ? '❚❚' : '▶';
  $('.pitch-read', el).textContent = (d.pitch >= 0 ? '+' : '') + d.pitch.toFixed(1) + '%';
  const live = d.liveBpm();
  const read = $('.bpm-read', el);
  read.textContent = live ? live.toFixed(1) : '--.-';
  if (d.bpm) {
    const pct = Math.round((d.bpmConfidence || 0) * 100);
    read.title = `detected ${d.bpmBase ? d.bpmBase.toFixed(1) : d.bpm.toFixed(1)} BPM`
      + `${d.bpmScale && d.bpmScale !== 1 ? ` ×${d.bpmScale}` : ''}`
      + ` · confidence ${pct}% — click to halve or double it`;
    // A tempo the detector is not sure of should not look like a fact.
    read.style.opacity = (d.bpmConfidence || 0) < 0.25 ? 0.55 : '';
  }
  const kel = $('.key-read', el);
  kel.textContent = d.key ? `${d.key} · ${keyCode(d)}` : '--';
  kel.style.opacity = d.key && (d.keyConfidence || 0) < 0.04 ? 0.5 : '';
  if (d.key) {
    kel.title = `key · Camelot code · confidence `
      + `${Math.round((d.keyConfidence || 0) * 100)}%`;
  }
  $$('.cue-btn', el).forEach((b, i) => b.classList.toggle('set', d.cues[i] != null));
  $$('.loop-btn', el).forEach(b => b.classList.toggle('on',
    !!d.loop && d.loop.beats === parseFloat(b.dataset.beats)));
  $('.sync', el).classList.toggle('on', Math.abs(d.pitch) > 0.001);
}

function keyCode(d) {
  if (!d.key) return '--';
  const pc = M.analyze.NOTES.indexOf(d.key.split(' ')[0]);
  return pc < 0 ? '--' : M.analyze.camelot(pc, /minor/.test(d.key));
}

/* ── mixer ────────────────────────────────────────────────────────────── */

function bindMixer() {
  $$('.mixer .knob').forEach(el => {
    const p = el.dataset.param, id = el.dataset.deck;
    knob(el, (v) => {
      if (p === 'fx') return app.eng.setEcho(v);
      const d = app.eng.decks[id];
      if (!d) return;
      if (p === 'trim') d.setTrim(v);
      else if (p === 'filter') d.setFilter(v);
      else d.setEq(p, v);
    });
  });

  $$('.fader').forEach(f => {
    const d = app.eng.decks[f.dataset.deck];
    f.addEventListener('input', () => d.setFader(parseFloat(f.value)));
    d.setFader(parseFloat(f.value));
  });

  $$('.cue-mon').forEach(b => {
    b.addEventListener('click', () => {
      const d = app.eng.decks[b.dataset.deck];
      const on = !b.classList.contains('on');
      b.classList.toggle('on', on);
      d.setCue(on);
      app.eng.updateCue();
    });
  });

  const xf = $('#xfade');
  xf.addEventListener('input', () => app.eng.setCrossfader(parseFloat(xf.value)));
  $('#xf-cut').addEventListener('click', () => {
    xf.value = 0.5; app.eng.setCrossfader(0.5);
  });
}

/* ── studio ───────────────────────────────────────────────────────────── */

function buildRack() {
  const rack = $('#rack');
  rack.textContent = '';
  for (const c of app.seq.channels) {
    const row = doc.createElement('div');
    row.className = 'chan';
    row.dataset.chan = c.name;

    const name = doc.createElement('div');
    name.className = 'chan-name';
    const mute = doc.createElement('span');
    mute.className = 'mute';
    mute.title = 'mute';
    const label = doc.createElement('span');
    label.textContent = c.label || c.name;
    name.append(mute, label);

    mute.addEventListener('click', (e) => {
      e.stopPropagation();
      c.mute = !c.mute;
      name.classList.toggle('muted', c.mute);
    });
    name.addEventListener('click', () => {
      app.sel = c.name;
      $('#roll-channel').value = c.name;
      $$('.chan-name').forEach(n => n.classList.remove('sel'));
      name.classList.add('sel');
      app.seq.preview(c.name);
      paintRoll();
    });
    // A file dropped on a channel replaces its voice with a sampler, which is
    // the one voice in synth.js with a buffer behind it.
    dropTarget(name, 'drop', async (files) => {
      try {
        const rec = await addLocal(files[0]);
        c.buffer = rec.buffer;
        c.voice = 'sampler';
        c.sample = true;
        c.pitched = true;
        c.label = files[0].name.replace(/\.[^.]+$/, '').slice(0, 14);
        label.textContent = c.label;
        buildRoll();
        toast(`${c.name} → ${c.label}`);
      } catch (e) {
        toast('decode failed: ' + e.message);
      }
    });

    const vol = doc.createElement('input');
    vol.type = 'range'; vol.className = 'chan-vol';
    vol.min = 0; vol.max = 1; vol.step = 0.01; vol.value = c.volume;
    vol.title = 'channel volume';
    vol.addEventListener('input', () => { c.volume = parseFloat(vol.value); });

    const steps = doc.createElement('div');
    steps.className = 'steps';
    row.append(name, vol, steps);
    rack.append(row);
  }
  paintSteps(true);
}

/* Rebuilding the step cells is cheap and happens only when the count changes;
 * painting their state happens every time one is clicked. */
function paintSteps(rebuild) {
  const n = app.seq.steps;
  for (const c of app.seq.channels) {
    const row = $(`.chan[data-chan="${c.name}"]`);
    if (!row) continue;
    const wrap = $('.steps', row);
    const rollOwned = (app.seq.pattern.notes[c.name] || []).length > 0;
    if (rebuild || wrap.children.length !== n) {
      wrap.textContent = '';
      for (let i = 0; i < n; i++) {
        const s = doc.createElement('div');
        s.className = 'step';
        s.dataset.i = i;
        s.addEventListener('click', () => {
          app.seq.toggleStep(c.name, i, false);
          paintSteps();
          save();
        });
        s.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          app.seq.toggleStep(c.name, i, true);
          paintSteps();
          save();
        });
        wrap.append(s);
      }
    }
    const data = app.seq.pattern.rows[c.name];
    Array.from(wrap.children).forEach((s, i) => {
      const v = data[i];
      s.classList.toggle('on', !!v && !rollOwned);
      s.classList.toggle('accent', v === 2 && !rollOwned);
      s.classList.toggle('beat', i % 4 === 0);
      s.classList.toggle('playing', i === app.playStep);
      s.style.opacity = rollOwned ? 0.35 : '';
      s.title = rollOwned ? 'the piano roll owns this channel' : '';
    });
  }
}

function buildRoll() {
  const sel = $('#roll-channel');
  sel.textContent = '';
  for (const c of app.seq.channels) {
    const o = doc.createElement('option');
    o.value = c.name;
    o.textContent = c.label || c.name;
    sel.append(o);
  }
  sel.value = app.sel;
  paintRoll();
}

/* Where a channel's 40 rows start. A bass line and a hat live in very different
 * octaves, and a roll that always shows C3–E6 is useless for one of them. */
function rollBase(name) {
  const c = app.seq.channel(name);
  if (!c) return 36;
  if (c.name === 'bass') return 24;
  if (c.pitched) return 48;
  return 36;
}

function paintRoll() {
  drawRoll($('#roll'), app.seq, app.sel, rollBase(app.sel), app.playStep);
}

function bindStudio() {
  const pats = $('#patterns');
  for (let i = 0; i < 8; i++) {
    const b = doc.createElement('button');
    b.textContent = i + 1;
    b.className = i === 0 ? 'on' : '';
    b.addEventListener('click', () => {
      app.seq.current = i;
      $$('#patterns button').forEach((x, k) => x.classList.toggle('on', k === i));
      $('#steps').value = String(app.seq.steps);
      paintSteps(true);
      paintRoll();
    });
    pats.append(b);
  }

  $('#steps').addEventListener('change', (e) => {
    app.seq.setSteps(parseInt(e.target.value, 10) || 16);
    paintSteps(true);
    paintRoll();
    save();
  });

  const swing = $('#swing');
  swing.addEventListener('input', () => {
    app.seq.setSwing(parseFloat(swing.value));
    $('#swing-read').textContent = Math.round(swing.value * 100) + '%';
    save();
  });

  $('#seq-clear').addEventListener('click', () => {
    app.seq.clear(); paintSteps(); paintRoll(); save();
    toast('pattern cleared');
  });
  $('#seq-rand').addEventListener('click', () => {
    app.seq.seed(); paintSteps(); paintRoll(); save();
    toast('seeded — now edit it');
  });

  $('#roll-channel').addEventListener('change', (e) => {
    app.sel = e.target.value;
    $$('.chan-name').forEach(n => n.classList.remove('sel'));
    const row = $(`.chan[data-chan="${app.sel}"] .chan-name`);
    if (row) row.classList.add('sel');
    paintRoll();
  });

  bindRoll();
}

function bindRoll() {
  const canvas = $('#roll');
  let drag = null;

  canvas.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const hit = rollHit(canvas, e, rollBase(app.sel), app.seq.steps);
    if (!hit) return;
    if (app.seq.removeNote(app.sel, hit.note, hit.step)) {
      paintRoll(); paintSteps(); save();
    }
  });

  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const base = rollBase(app.sel);
    const hit = rollHit(canvas, e, base, app.seq.steps);
    if (!hit) return;
    const existing = app.seq.noteAt(app.sel, hit.note, hit.step);
    if (existing) {
      // Grabbing the last few pixels of a note lengthens it; anywhere else on
      // an existing note is a no-op, so a mis-click does not double it.
      const endX = (existing.start + existing.length) * M.ui.ROLL.cellW;
      if (hit.x > endX - 6) {
        drag = { note: existing, base };
        canvas.setPointerCapture(e.pointerId);
      }
      return;
    }
    const n = app.seq.addNote(app.sel, hit.note, hit.step, 1, 0.85);
    app.seq.preview && app.seq._voice(app.seq.channel(app.sel),
      app.eng.ctx.currentTime + 0.01, 0.85, hit.note, 0.2);
    drag = { note: n, base };
    canvas.setPointerCapture(e.pointerId);
    paintRoll(); paintSteps(); save();
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const hit = rollHit(canvas, e, drag.base, app.seq.steps);
    if (!hit) return;
    const len = Math.max(1, hit.step - Math.floor(drag.note.start) + 1);
    if (len !== drag.note.length) { drag.note.length = len; paintRoll(); }
  });

  const stop = (e) => {
    if (!drag) return;
    drag = null;
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
    save();
  };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);
}

function syncStudioUI() {
  $('#steps').value = String(app.seq.steps);
  $('#swing-read').textContent = Math.round(app.seq.swing * 100) + '%';
  $$('#patterns button').forEach((x, k) => x.classList.toggle('on', k === app.seq.current));
  buildRoll();
  paintSteps(true);
}

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem('musica.studio', JSON.stringify(app.seq.serialize()));
    } catch (e) { /* a full quota should not interrupt a set */ }
  }, 400);
}

/* ── crate ────────────────────────────────────────────────────────────── */

const pending = new Map();
async function addLocal(file) {
  const key = file.name + ':' + file.size;
  const found = app.local.find(r => r.name === file.name && r.size === file.size);
  if (found) return found;
  // Decoding is async, so the dedupe has to cover the gap: two drops of the
  // same file land before either has finished and both would add a row.
  if (pending.has(key)) return pending.get(key);
  const job = (async () => {
    const buffer = await decodeFile(file);
    const rec = { name: file.name, size: file.size, buffer, analysis: null };
    app.local.push(rec);
    refreshLocal();
    // Work the tempo out now so the row can show it before it hits a deck.
    analyzeBuffer(rec).catch(() => {});
    return rec;
  })().finally(() => pending.delete(key));
  pending.set(key, job);
  return job;
}

function refreshLocal() {
  const ul = $('#local');
  ul.textContent = '';
  if (!app.local.length) {
    const li = doc.createElement('li');
    li.innerHTML = '<span class="who"><b>nothing loaded</b>'
      + '<span>drop files anywhere, or use LOAD FILES</span></span>';
    ul.append(li);
    return;
  }
  for (const rec of app.local) {
    const a = rec.analysis;
    ul.append(row({
      title: rec.name,
      sub: [fmtTime(rec.buffer.duration),
            a && a.bpm ? a.bpm.toFixed(1) + ' BPM' : 'analysing…',
            a && a.camelot ? `${a.key} · ${a.camelot}` : null].filter(Boolean).join(' · '),
    }, [
      { label: 'A', cls: 'a', fn: () => { loadToDeck('A', rec); tab('booth'); } },
      { label: 'B', cls: 'b', fn: () => { loadToDeck('B', rec); tab('booth'); } },
    ]));
  }
}

function row(item, actions) {
  const li = doc.createElement('li');
  if (item.art) {
    const img = doc.createElement('img');
    img.src = item.art; img.alt = ''; img.loading = 'lazy';
    li.append(img);
  }
  const who = doc.createElement('span');
  who.className = 'who';
  const b = doc.createElement('b');
  b.textContent = item.title;
  b.title = item.title;
  const s = doc.createElement('span');
  s.textContent = item.sub || '';
  who.append(b, s);
  const to = doc.createElement('span');
  to.className = 'to';
  for (const a of actions || []) {
    const btn = doc.createElement('button');
    btn.textContent = a.label;
    if (a.cls) btn.className = a.cls;
    if (a.title) btn.title = a.title;
    btn.addEventListener('click', a.fn);
    to.append(btn);
  }
  li.append(who, to);
  return li;
}

function bindCrate() {
  $('#files').addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    for (const f of files) {
      try { await addLocal(f); } catch (err) { toast(`${f.name}: ${err.message}`); }
    }
    e.target.value = '';
  });

  $('#go').addEventListener('click', search);
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  $('#kind').addEventListener('change', () => { if ($('#q').value.trim()) search(); });

  // Dropping a file anywhere that is not a deck or a channel still loads it.
  dropTarget(doc.body, 'body-drop', async (files) => {
    for (const f of files) {
      try { await addLocal(f); } catch (e) { toast(`${f.name}: ${e.message}`); }
    }
    toast(`${files.length} file${files.length > 1 ? 's' : ''} in the crate`);
  });

  refreshLocal();
}

/* The note above the results explains what Spotify will and will not do here,
 * straight from the module rather than hardcoded in the page: spotify.py owns
 * that list and it has changed once already. */
async function spotifyNote() {
  const note = $('#sp-note');
  try {
    const s = await M.api.status();
    if (s.configured) {
      note.innerHTML = 'Spotify is connected — metadata only. Streamed audio is '
        + 'DRM-protected and cannot be routed through Web Audio, so load a local '
        + 'file to actually mix it.';
      note.classList.add('show');
      return;
    }
    note.innerHTML = 'Spotify is not connected. Create an app at '
      + '<a href="https://developer.spotify.com/dashboard" target="_blank" '
      + 'rel="noreferrer">developer.spotify.com</a>, then run '
      + '<code>m musica/set_key client_id=… client_secret=…</code>. '
      + 'The decks and the studio work without it.';
    note.classList.add('show');
  } catch (e) {
    note.textContent = 'The module API is not answering, so the crate cannot '
      + 'search. Everything you drop in still works.';
    note.classList.add('show');
  }
}

async function search() {
  const q = $('#q').value.trim();
  const kind = $('#kind').value;
  const ul = $('#results');
  if (!q) return;
  ul.textContent = '';
  ul.append(row({ title: 'searching…', sub: q }, []));
  try {
    const res = await M.api.search(q, kind, 24);
    ul.textContent = '';
    if (!res.items || !res.items.length) {
      ul.append(row({ title: 'nothing found', sub: q }, []));
      return;
    }
    for (const it of res.items) {
      ul.append(row({
        title: it.name || '(untitled)',
        sub: M.crate.subtitle(it, res.kind),
        art: it.art,
      }, [
        it.url && { label: '↗', cls: '', title: 'open on Spotify',
                    fn: () => root.open(it.url, '_blank', 'noreferrer') },
        res.kind === 'playlist' && { label: 'OPEN', title: 'list its tracks',
                    fn: () => openPlaylist(it.id, it.name) },
      ].filter(Boolean)));
    }
  } catch (e) {
    ul.textContent = '';
    ul.append(row({ title: 'search failed', sub: e.message }, []));
  }
}

async function openPlaylist(id, name) {
  const ul = $('#results');
  ul.textContent = '';
  ul.append(row({ title: 'loading…', sub: name }, []));
  try {
    const res = await M.api.playlist(id, 50);
    ul.textContent = '';
    for (const t of res.items) {
      ul.append(row({ title: t.name, sub: M.crate.subtitle(t, 'track'), art: t.art },
        [t.url && { label: '↗', fn: () => root.open(t.url, '_blank', 'noreferrer') }]
          .filter(Boolean)));
    }
  } catch (e) {
    ul.textContent = '';
    ul.append(row({ title: 'could not open that playlist', sub: e.message }, []));
  }
}

/* ── tabs and keys ────────────────────────────────────────────────────── */

function tab(name) {
  $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === name));
  if (name === 'studio') { paintSteps(true); paintRoll(); }
}

$$('.tab').forEach(t => t.addEventListener('click', () => tab(t.dataset.tab)));

function bindKeys() {
  doc.addEventListener('keydown', (e) => {
    const el = doc.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const A = app.eng.decks.A, B = app.eng.decks.B;
    switch (e.key.toLowerCase()) {
      case ' ': e.preventDefault(); $('#seq-play').click(); break;
      case 'q': if (A.buffer) { A.toggle(); paintDeck('A'); } break;
      case 'p': if (B.buffer) { B.toggle(); paintDeck('B'); } break;
      case 'a': $('#xfade').value = 0; app.eng.setCrossfader(0); break;
      case 's': $('#xf-cut').click(); break;
      case 'd': $('#xfade').value = 1; app.eng.setCrossfader(1); break;
      case '1': tab('booth'); break;
      case '2': tab('studio'); break;
      case '3': tab('crate'); break;
      default: break;
    }
  });
}

/* ── the frame loop ───────────────────────────────────────────────────── */

function render() {
  const now = app.eng.ctx.currentTime;

  // Steps were scheduled ahead of time; light them up as the audio reaches them.
  let step = app.playStep;
  while (app.stepQueue.length && app.stepQueue[0].time <= now) {
    step = app.stepQueue.shift().step;
  }
  if (step !== app.playStep) {
    app.playStep = step;
    if ($('#studio').classList.contains('on')) { paintSteps(); paintRoll(); }
    const bb = M.grid.barsBeats(step < 0 ? 0 : step, app.seq.steps);
    $('#bars').textContent = bb.bar;
    $('#beats').textContent = bb.beat;
  }

  if ($('#booth').classList.contains('on')) {
    for (const id of ['A', 'B']) {
      const el = deckEl(id), d = app.eng.decks[id];
      drawFull($('.wave-full', el), d);
      drawZoom($('.wave-zoom', el), d);
    }
  }

  const lv = app.eng.levels();
  meter($('#meter-l'), lv.l, app.meters.l);
  meter($('#meter-r'), lv.r, app.meters.r);

  if (app.rec) {
    $('#rec-time').textContent = fmtTime((performance.now() - app.recStart) / 1000);
  }

  requestAnimationFrame(render);
}

/* ── go ───────────────────────────────────────────────────────────────── */

$('#boot-go').addEventListener('click', boot);
doc.addEventListener('keydown', function once(e) {
  if (e.key === 'Enter' && !app.started) { boot(); doc.removeEventListener('keydown', once); }
});

root.musica = app;   // a handle for the console, and for the browser tests

})(typeof globalThis !== 'undefined' ? globalThis : this);
