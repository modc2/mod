/* crates — wiring.
 *
 * Everything below binds the markup in index.html to the engine, the sequencer
 * and the crate. It is the only file that touches both sides, which is on
 * purpose: if a control does not work, it is broken here, and if it makes the
 * wrong sound, it is broken in engine.js or synth.js.
 */
(function (root) {
'use strict';
const M = root.CRATES;
const doc = root.document;
const { $, $$, toast, knob, drawFull, drawZoom, drawRoll, rollHit, fmtTime, meter } = M.ui;
const C = M.crate;

const app = {
  eng: null, seq: null, started: false,
  library: [],               // decoded audio — the only thing that can be mixed
  setlist: [],               // the plan: platform rows and files, in order
  source: 'all',             // which platform the search box is pointed at
  results: [],               // what the crate is showing
  preview: null,             // the row in the preview player
  sel: 'bass',               // channel the piano roll is showing
  stepQueue: [],             // {step, time} the scheduler has queued ahead
  playStep: -1,
  meters: { l: {}, r: {} },
  rec: null, recStart: 0,
  taps: [],
  lastKeyPaint: 0,
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
  bindDrawer();
  bindKeys();

  $('#boot').classList.add('gone');
  requestAnimationFrame(render);

  // Restore a previous session's patterns and picks, if the tab has been here before.
  try {
    const saved = localStorage.getItem('crates.studio');
    if (saved && app.seq.restore(JSON.parse(saved))) {
      $('#bpm').value = app.seq.bpm;
      $('#swing').value = app.seq.swing;
      syncStudioUI();
      toast('restored your last pattern');
    }
  } catch (e) { /* a corrupt save is not worth a broken console */ }
  try {
    const set = JSON.parse(localStorage.getItem('crates.set') || '[]');
    if (Array.isArray(set)) app.setlist = set.filter(x => x && x.key);
  } catch (e) { app.setlist = []; }
  paintSet();

  // The playlist panel: MY SET is the working copy, a playlist is the kept
  // one. It borrows these four things from here rather than reaching into the
  // console's internals, which keeps the two files independent.
  if (M.playlists) {
    M.playlists.mount({
      getSet: () => app.setlist.slice(),
      loadSet: (tracks) => {
        app.setlist = (tracks || []).filter(x => x && x.key);
        saveSetLocal();
        paintSet();
        refreshLocal();
        tab('crate');
      },
      paintSet,
      showTracks,
      toast,
    }).catch(() => { /* the console works without a library */ });
  }

  version();
  platformNotes();
}

/* Save MY SET to this browser WITHOUT writing through to the open playlist —
 * used when the set list is being replaced BY a playlist, where writing back
 * would just echo what the server already has. */
function saveSetLocal() {
  try { localStorage.setItem('crates.set', JSON.stringify(app.setlist)); } catch (e) { /* quota */ }
}

/* Any list of tracks, shown in the results column: a shared playlist someone
 * sent you, or one of yours you asked to see. They are ordinary crate rows —
 * every one can go straight to a deck or into MY SET. */
function showTracks(title, tracks, note) {
  tab('crate');
  const ul = $('#results');
  ul.textContent = '';
  setResultsTitle('PLAYLIST', [title, note].filter(Boolean).join(' — '));
  app.results = (tracks || []).slice();
  if (!app.results.length) return ul.append(stateRow('nothing in it yet'));
  ul.append(headRow(`<b>${esc(title)}</b><span class="hint"> · ${app.results.length} `
    + `track${app.results.length === 1 ? '' : 's'}${note ? ' · ' + esc(note) : ''}</span>`));
  for (const t of app.results) ul.append(resultRow(t));
}

async function version() {
  try {
    const info = await M.api.info();
    if (info && info.version) $('#ver').textContent = info.version;
  } catch (e) { /* the console works without the API; the crate will say so */ }
}

/* ── transport ────────────────────────────────────────────────────────── */

function icon(name) { return `<svg><use href="#i-${name}"/></svg>`; }

function bindTransport() {
  $('#seq-play').addEventListener('click', () => {
    app.seq.toggle();
    $('#seq-play').classList.toggle('on', app.seq.playing);
    $('#seq-play').innerHTML = icon(app.seq.playing ? 'stop' : 'play');
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
    a.download = `crates-${new Date().toISOString().replace(/[:.]/g, '-')}.webm`;
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
        // Set on first press, jump on every one after — and right-click to clear.
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

/* Put a library record (decoded) or a File on a deck. Platform rows go through
 * loadPlatformToDeck, which ends up here once the bytes are in. */
async function loadToDeck(id, entry) {
  const el = deckEl(id), d = app.eng.decks[id];
  $('.sub', el).textContent = 'decoding…';
  try {
    const rec = entry.buffer ? entry : await addLocal(entry);
    d.load(rec.buffer, rec.name);
    d.rec = rec;
    el.classList.add('loaded');
    $('.title', el).textContent = rec.name;
    $('.sub', el).textContent = `${fmtTime(rec.buffer.duration)} · analysing…`;
    $('.bpm-read', el).textContent = '--.-';
    $('.key-read', el).textContent = '--';
    $('.pitch-fader', el).value = 0;
    paintDeckArt(id, rec);
    paintDeck(id);
    const a = await analyzeBuffer(rec);
    if (d.rec !== rec) return;               // something else landed meanwhile
    d.bpm = a.bpm; d.bpmBase = a.bpm; d.bpmScale = 1;
    d.beatOffset = a.offset || 0; d.key = a.key;
    d.bpmConfidence = a.bpmConfidence; d.keyConfidence = a.keyConfidence;
    $('.sub', el).textContent = [rec.artists, fmtTime(rec.buffer.duration),
      rec.source === 'local' ? rec.name.split('.').pop() : C.SOURCES[rec.source].label]
      .filter(Boolean).join(' · ');
    paintDeck(id);
    paintSet();
  } catch (e) {
    if (!d.rec) el.classList.remove('loaded');
    $('.sub', el).textContent = 'could not decode that';
    toast('decode failed: ' + e.message);
  }
}

function paintDeckArt(id, rec) {
  const el = deckEl(id);
  const img = $('.art img', el);
  if (rec && rec.art) { img.src = rec.art; img.hidden = false; }
  else { img.removeAttribute('src'); img.hidden = true; }
  const badge = $('.src-badge', el);
  badge.innerHTML = rec ? pill(rec.source) : '';
}

/* Analysis is a second or two of arithmetic on a long track, so it runs in a
 * worker built from analyze.js itself — the same file the page already loaded
 * and the same one tests/engine.mjs checks. If a worker cannot be made, it
 * falls back to the main thread and the tab just stutters once. */
let workerSrc = null;
async function analyzeBuffer(rec) {
  if (rec.analysis) return rec.analysis;
  if (rec.analysing) return rec.analysing;
  const buffer = rec.buffer;
  const mono = M.analyze.toMono(buffer);
  rec.analysing = (async () => {
    let result = null;
    try {
      if (workerSrc === null) {
        workerSrc = await fetch('js/analyze.js').then(r => r.text());
      }
      result = await new Promise((res, rej) => {
        const src = workerSrc + `
self.onmessage = function (e) {
  var mono = e.data.mono, sr = e.data.sr;
  self.postMessage(self.CRATES.analyze.analyze({
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
    rec.analysing = null;
    refreshLocal();
    paintSet();
    return result;
  })();
  return rec.analysing;
}

function paintDeck(id) {
  const el = deckEl(id), d = app.eng.decks[id];
  el.classList.toggle('playing', d.playing);
  $('.play', el).classList.toggle('on', d.playing);
  $('.play', el).innerHTML = icon(d.playing ? 'pause' : 'play');
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
  paintKeyMatch();
}

function keyCode(d) {
  if (!d.key) return '--';
  const pc = M.analyze.NOTES.indexOf(d.key.split(' ')[0]);
  return pc < 0 ? '--' : M.analyze.camelot(pc, /minor/.test(d.key));
}

/* The key a deck is actually playing in: its detected key shifted by however
 * many semitones the pitch fader has moved it. */
function liveKey(d) {
  if (!d || !d.key) return null;
  const semis = Math.round(12 * Math.log2(d.rate || 1));
  return C.camelotShift(keyCode(d), semis);
}

/* The wheel relationship between the two decks, on the pill in the mixer. */
function paintKeyMatch() {
  const A = app.eng.decks.A, B = app.eng.decks.B;
  const ka = liveKey(A), kb = liveKey(B);
  const el = $('#keymatch');
  $('.km-a', el).textContent = ka || '--';
  $('.km-b', el).textContent = kb || '--';
  const rel = C.camelotRel(ka, kb);
  $('.km-rel', el).textContent = rel.label || '·';
  el.classList.toggle('good', rel.score >= 2);
  el.classList.toggle('bad', rel.rel === 'clash');
  for (const [id, mine, other] of [['A', ka, kb], ['B', kb, ka]]) {
    const kel = $('.key-read', deckEl(id));
    const r = C.camelotRel(mine, other);
    kel.classList.toggle('match', r.score >= 2);
    kel.classList.toggle('clash', r.rel === 'clash');
  }
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
  const box = $('.roll-scroll');
  if (box && box.clientWidth) {
    M.ui.ROLL.cellW = Math.max(18, Math.floor((box.clientWidth - 2) / app.seq.steps));
  }
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
      localStorage.setItem('crates.studio', JSON.stringify(app.seq.serialize()));
    } catch (e) { /* a full quota should not interrupt a set */ }
  }, 400);
}

/* ── library: decoded audio ───────────────────────────────────────────── */

/* Every row in the library has one key. Files are name+size; platform tracks
 * are source:id. MY SET uses the same keys, which is how a plan made of
 * platform rows knows which of them have already been pulled in. */
function keyOf(x) {
  if (x.key) return x.key;
  if (x.source && x.source !== 'local') return `${x.source}:${x.id}`;
  return `local:${x.name}:${x.size}`;
}

const pending = new Map();
async function addLocal(file) {
  const key = `local:${file.name}:${file.size}`;
  const found = app.library.find(r => r.key === key);
  if (found) return found;
  // Decoding is async, so the dedupe has to cover the gap: two drops of the
  // same file land before either has finished and both would add a row.
  if (pending.has(key)) return pending.get(key);
  const job = (async () => {
    const buffer = await decodeFile(file);
    const rec = { key, source: 'local', id: key, name: file.name, size: file.size,
                  artists: '', art: null, buffer, analysis: null };
    app.library.push(rec);
    refreshLocal();
    // Work the tempo out now so the row can show it before it hits a deck.
    analyzeBuffer(rec).catch(() => {});
    return rec;
  })().finally(() => pending.delete(key));
  pending.set(key, job);
  return job;
}

/* Read a response body with progress, so a 100MB SoundCloud mix shows a bar
 * instead of a frozen "loading…". */
async function fetchAudio(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) {
    let why = '';
    try { why = (await res.json()).error || ''; } catch (e) { /* not JSON */ }
    throw new Error(why || `stream failed (${res.status})`);
  }
  const total = parseInt(res.headers.get('Content-Length') || '0', 10);
  if (!res.body || !total) return res.arrayBuffer();
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length;
    if (onProgress) onProgress(got / total);
  }
  const out = new Uint8Array(got);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out.buffer;
}

/* Pull a Bandcamp or SoundCloud track into the library. The module says where
 * the bytes are; SoundCloud's CDN is fetched directly and falls back to the
 * module's proxy if the browser is refused. */
async function addPlatform(item, onProgress) {
  const key = keyOf(item);
  const found = app.library.find(r => r.key === key);
  if (found) return found;
  if (pending.has(key)) return pending.get(key);
  const job = (async () => {
    const where = await M.api.stream(item.source, item.id, item.bc_id);
    let buf;
    try {
      buf = await fetchAudio(C.streamUrl(where), onProgress);
    } catch (e) {
      if (!where.direct) throw e;
      buf = await fetchAudio(C.streamUrl({ ...where, direct: false }), onProgress);
    }
    const buffer = await app.eng.decode(buf);
    const rec = {
      key, source: item.source, id: item.id, bc_id: item.bc_id,
      name: [item.artists, item.name].filter(Boolean).join(' – '),
      artists: item.artists, title: item.name, art: item.art || where.art,
      url: item.url, size: buf.byteLength, buffer, analysis: null,
    };
    app.library.push(rec);
    refreshLocal();
    analyzeBuffer(rec).catch(() => {});
    return rec;
  })().finally(() => pending.delete(key));
  pending.set(key, job);
  return job;
}

/* One entry point for every "→ A" / "→ B" button, whatever the row is. */
async function sendToDeck(id, item, btn) {
  if (item.buffer) { loadToDeck(id, item); tab('booth'); return; }
  const lib = app.library.find(r => r.key === keyOf(item));
  if (lib) { loadToDeck(id, lib); tab('booth'); return; }
  if (item.source === 'spotify') {
    return toast('Spotify audio is DRM-protected — preview it, then find it on Bandcamp or SoundCloud');
  }
  if (item.streamable === false) return toast('that one does not stream');
  if (item.kind && item.kind !== 'track') return toast('open it and pick a track');
  const el = deckEl(id);
  $('.title', el).textContent = [item.artists, item.name].filter(Boolean).join(' – ');
  $('.sub', el).textContent = `fetching from ${C.SOURCES[item.source].label}…`;
  paintDeckArt(id, item);
  el.classList.add('loading');
  if (btn) btn.classList.add('busy');
  const prog = btn && btn.closest('li') ? btn.closest('li').querySelector('.prog') : null;
  try {
    const rec = await addPlatform(item, (p) => {
      $('.sub', el).textContent = `fetching… ${Math.round(p * 100)}%`;
      if (prog) prog.style.width = Math.round(p * 100) + '%';
    });
    await loadToDeck(id, rec);
  } catch (e) {
    $('.sub', el).textContent = 'could not load that';
    toast(e.message);
  } finally {
    el.classList.remove('loading');
    if (btn) btn.classList.remove('busy');
    if (prog) prog.style.width = '0';
  }
}

function refreshLocal() {
  const ul = $('#local');
  ul.textContent = '';
  $('#crate-count').textContent = app.library.length || '';
  if (!app.library.length) {
    ul.append(stateRow('nothing decoded yet — drop files anywhere, or send a Bandcamp / SoundCloud track to a deck'));
    return;
  }
  for (const rec of app.library) {
    const a = rec.analysis;
    const inSet = app.setlist.some(s => s.key === rec.key);
    ul.append(row({
      title: rec.name, art: rec.art, source: rec.source,
      sub: [fmtTime(rec.buffer.duration),
            a && a.bpm ? a.bpm.toFixed(1) + ' BPM' : 'analysing…'].filter(Boolean).join(' · '),
      key: a && a.camelot ? `${a.key} · ${a.camelot}` : null,
      keyCode: a && a.camelot,
    }, [
      { label: 'A', cls: 'a', fn: (e) => sendToDeck('A', rec, e.currentTarget) },
      { label: 'B', cls: 'b', fn: (e) => sendToDeck('B', rec, e.currentTarget) },
      { html: icon(inSet ? 'check' : 'plus'), cls: 'plus' + (inSet ? ' on' : ''),
        title: inSet ? 'in MY SET — click to take it out' : 'add to MY SET (top of the page)',
        fn: () => toggleSet(rec) },
    ]));
  }
}

/* ── rows ─────────────────────────────────────────────────────────────── */

function pill(source, text) {
  const s = C.SOURCES[source] || C.SOURCES.local;
  return `<span class="pill ${source}">${text || s.short}</span>`;
}

function stateRow(text, err) {
  const li = doc.createElement('li');
  li.className = 'state' + (err ? ' err' : '');
  li.textContent = text;
  return li;
}

function headRow(html) {
  const li = doc.createElement('li');
  li.className = 'head';
  li.innerHTML = html;
  return li;
}

/* The key readout on a row is relative to whatever is playing: lime when it
 * would mix cleanly into the live deck, amber when it would clash. */
function keyClass(code) {
  const ref = referenceKey();
  if (!code || !ref) return 'plain';
  const r = C.camelotRel(code, ref);
  return r.score >= 2 ? '' : (r.rel === 'clash' ? 'clash' : 'plain');
}

function referenceKey() {
  const A = app.eng.decks.A, B = app.eng.decks.B;
  const d = A.playing ? A : (B.playing ? B : (A.key ? A : B));
  return liveKey(d);
}

function row(item, actions, onClick) {
  const li = doc.createElement('li');
  if (item.art) {
    const img = doc.createElement('img');
    img.src = item.art; img.alt = ''; img.loading = 'lazy';
    img.onerror = () => { img.replaceWith(noArt()); };
    li.append(img);
  } else {
    li.append(noArt());
  }
  const who = doc.createElement('span');
  who.className = 'who';
  const b = doc.createElement('b');
  b.innerHTML = (item.source ? pill(item.source) : '')
    + (item.kind && item.kind !== 'track' ? `<span class="pill kind">${item.kind.toUpperCase()}</span>` : '');
  b.append(doc.createTextNode(item.title || '(untitled)'));
  b.title = item.title || '';
  const s = doc.createElement('span');
  if (item.key) {
    const k = doc.createElement('em');
    k.className = 'key ' + keyClass(item.keyCode);
    k.textContent = item.key;
    s.append(k, doc.createTextNode(item.sub ? ' · ' + item.sub : ''));
  } else {
    s.textContent = item.sub || '';
  }
  who.append(b, s);
  const to = doc.createElement('span');
  to.className = 'to';
  for (const a of actions || []) {
    const btn = doc.createElement('button');
    if (a.html) btn.innerHTML = a.html; else btn.textContent = a.label;
    if (a.cls) btn.className = a.cls;
    if (a.title) btn.title = a.title;
    btn.addEventListener('click', (e) => { e.stopPropagation(); a.fn(e); });
    to.append(btn);
  }
  li.append(who, to);
  const prog = doc.createElement('i');
  prog.className = 'prog';
  li.append(prog);
  if (onClick) { li.style.cursor = 'pointer'; li.addEventListener('click', onClick); }
  return li;
}

function noArt() {
  const d = doc.createElement('span');
  d.className = 'noart';
  d.innerHTML = icon('disc');
  return d;
}

/* ── crate: search, links, discover ───────────────────────────────────── */

function bindCrate() {
  $('#files').addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    for (const f of files) {
      try { await addLocal(f); } catch (err) { toast(`${f.name}: ${err.message}`); }
    }
    e.target.value = '';
  });

  const q = $('#q');
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  q.addEventListener('input', () => {
    const link = C.detect(q.value);
    const tag = $('#q-link');
    tag.hidden = !link;
    if (link) $('span', tag).textContent = `${C.SOURCES[link.source].label} ${link.kind}`;
  });
  q.addEventListener('paste', () => setTimeout(() => { if (C.detect(q.value)) search(); }, 0));
  $('#kind').addEventListener('change', () => { if (q.value.trim()) search(); });

  $$('#sources .chip').forEach(ch => ch.addEventListener('click', () => {
    app.source = ch.dataset.source;
    $$('#sources .chip').forEach(x => x.classList.toggle('on', x === ch));
    if (q.value.trim()) search();
  }));

  $$('#discover-tags .tagbtn').forEach(b => b.addEventListener('click', () => discover(b.dataset.tag)));

  $('#set-clear').addEventListener('click', () => {
    if (!app.setlist.length) return;
    app.setlist = []; saveSet(); paintSet(); refreshLocal();
    repaintResults();
    toast('MY SET is empty again');
  });

  $$('[data-goto]').forEach(b => b.addEventListener('click', () => tab(b.dataset.goto)));

  // Dropping a file anywhere that is not a deck or a channel still loads it.
  dropTarget(doc.body, 'body-drop', async (files) => {
    for (const f of files) {
      try { await addLocal(f); } catch (e) { toast(`${f.name}: ${e.message}`); }
    }
    toast(`${files.length} file${files.length > 1 ? 's' : ''} in the library`);
  });

  refreshLocal();
}

/* What each platform will do here, straight from the module rather than
 * hardcoded in the page: mod.py owns that list and it has changed already. */
async function platformNotes() {
  const box = $('#source-notes');
  box.textContent = '';
  try {
    const p = await M.api.platforms();
    app.platforms = p;
    if (!p.spotify.configured) {
      const n = doc.createElement('div');
      n.className = 'note sp';
      n.innerHTML = 'Spotify search is off until this deployment has app keys — Bandcamp and '
        + 'SoundCloud work without any. Create an app at '
        + '<a href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer">developer.spotify.com</a>, '
        + 'then <code>m crates/set_key client_id=… client_secret=…</code>. Spotify links '
        + 'still preview in the embed.';
      box.append(n);
    }
    if (p.bandcamp.last_error) {
      const n = doc.createElement('div');
      n.className = 'note bc';
      n.textContent = 'Bandcamp: ' + p.bandcamp.last_error;
      box.append(n);
    }
  } catch (e) {
    const n = doc.createElement('div');
    n.className = 'note';
    n.textContent = 'The module API is not answering, so the crate cannot search. '
      + 'Everything you drop in still works.';
    box.append(n);
  }
}

function setResultsTitle(title, note) {
  $('#results-title').textContent = title;
  if (note !== undefined) $('#results-note').textContent = note;
}

async function search() {
  const q = $('#q').value.trim();
  if (!q) return;
  const ul = $('#results');
  ul.textContent = '';
  const link = C.detect(q);
  if (link) {
    ul.append(stateRow(`opening ${C.SOURCES[link.source].label} ${link.kind}…`));
    setResultsTitle('LINK', q);
    try {
      const res = await M.api.resolve(q);
      showCollection(res);
    } catch (e) {
      ul.textContent = '';
      ul.append(stateRow(e.message, true));
    }
    return;
  }
  const kind = $('#kind').value;
  setResultsTitle(app.source === 'all' ? 'EVERYWHERE' : C.SOURCES[app.source].label.toUpperCase(), q);
  ul.append(stateRow(`searching ${app.source === 'all' ? 'Spotify, Bandcamp and SoundCloud' : C.SOURCES[app.source].label}…`));
  try {
    const res = await M.api.search(q, app.source, kind, 30);
    ul.textContent = '';
    if (res.sources) {
      for (const [src, s] of Object.entries(res.sources)) {
        if (s.error && !(src === 'spotify' && app.platforms && !app.platforms.spotify.configured)) {
          ul.append(stateRow(`${C.SOURCES[src].label}: ${s.error}`, true));
        }
      }
    }
    if (res.items && res.items.length === 1 && res.kind && !res.query) { showCollection(res); return; }
    app.results = res.items || [];
    if (!app.results.length) return ul.append(stateRow(`nothing on ${app.source === 'all' ? 'any platform' : C.SOURCES[app.source].label} for "${q}"`));
    for (const it of app.results) ul.append(resultRow(it));
  } catch (e) {
    ul.textContent = '';
    ul.append(stateRow(e.message, true));
  }
}

async function discover(tag) {
  const ul = $('#results');
  ul.textContent = '';
  ul.append(stateRow(`digging Bandcamp for ${tag}…`));
  setResultsTitle('DISCOVER', `Bandcamp · ${tag} · what's selling this week`);
  $$('#discover-tags .tagbtn').forEach(b => b.classList.toggle('on', b.dataset.tag === tag));
  try {
    const res = await M.api.discover(tag, 'top', 30);
    ul.textContent = '';
    app.results = res.items || [];
    if (!app.results.length) return ul.append(stateRow('nothing there'));
    for (const it of app.results) ul.append(resultRow(it));
  } catch (e) {
    ul.textContent = '';
    ul.append(stateRow(e.message, true));
  }
}

/* An album, playlist or artist opened into its tracks. */
function showCollection(res) {
  const ul = $('#results');
  ul.textContent = '';
  const items = res.items || [];
  const title = [res.artists, res.name].filter(Boolean).join(' – ') || res.query || '';
  setResultsTitle((res.kind || 'link').toUpperCase(), title);
  if (res.name || res.artists) {
    ul.append(headRow(`${pill(res.source)}<b>${esc(title)}</b>`
      + `<span class="hint"> · ${items.length} track${items.length === 1 ? '' : 's'}`
      + (res.release ? ` · ${String(res.release).slice(0, 10)}` : '') + '</span>'));
  }
  if (res.embed && res.kind !== 'track') showPreview({ ...res, items: undefined });
  if (!items.length && res.kind === 'track') { app.results = [res]; ul.append(resultRow(res)); showPreview(res); return; }
  app.results = items;
  if (!items.length) return ul.append(stateRow('no tracks here'));
  for (const it of items) ul.append(resultRow(it));
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function resultRow(it) {
  const inSet = app.setlist.some(s => s.key === keyOf(it));
  const lib = app.library.find(r => r.key === keyOf(it));
  const a = lib && lib.analysis;
  const actions = [];
  const isTrack = !it.kind || it.kind === 'track';
  if (isTrack && it.source !== 'spotify' && it.streamable !== false) {
    actions.push({ label: 'A', cls: 'a', title: 'load onto deck A', fn: (e) => sendToDeck('A', it, e.currentTarget) });
    actions.push({ label: 'B', cls: 'b', title: 'load onto deck B', fn: (e) => sendToDeck('B', it, e.currentTarget) });
  }
  if (!isTrack) {
    actions.push({ label: 'OPEN', title: 'list its tracks', fn: () => openItem(it) });
  }
  if (isTrack) {
    actions.push({ html: icon(inSet ? 'check' : 'plus'), cls: 'plus' + (inSet ? ' on' : ''),
      title: inSet ? 'in MY SET — click to take it out' : 'add to MY SET (top of the page)',
      fn: () => toggleSet(it) });
  }
  if (it.url) actions.push({ html: icon('ext'), title: `open on ${C.SOURCES[it.source].label}`,
    fn: () => root.open(it.url, '_blank', 'noreferrer') });
  const li = row({
    title: it.name || '(untitled)', sub: C.subtitle(it), art: it.art, source: it.source, kind: it.kind,
    key: a && a.camelot ? `${a.key} · ${a.camelot}` : null, keyCode: a && a.camelot,
  }, actions, () => { $$('#results li').forEach(x => x.classList.remove('sel')); li.classList.add('sel'); showPreview(it); });
  return li;
}

/* Albums, playlists and artists open in place. Each platform has its own way
 * of listing tracks; the module normalises them, this just picks the call. */
async function openItem(it) {
  const ul = $('#results');
  ul.textContent = '';
  ul.append(stateRow(`opening ${it.name}…`));
  try {
    let res;
    if (it.source === 'bandcamp') res = it.kind === 'artist'
      ? await M.api.search(it.name, 'bandcamp', 'all', 40) : await M.api.bandcampPage(it.id);
    else if (it.source === 'soundcloud') res = it.kind === 'artist'
      ? await M.api.soundcloudUser(it.id) : await M.api.soundcloudPlaylist(it.id);
    else if (it.kind === 'album') res = await M.api.album(it.id);
    else if (it.kind === 'artist') res = await M.api.artist(it.id);
    else res = await M.api.playlist(it.id, 100);
    if (it.source === 'bandcamp' && it.kind === 'artist') {
      res = { ...res, name: it.name, kind: 'artist', source: 'bandcamp', items: (res.items || []).filter(x => x.kind !== 'artist') };
    }
    showCollection({ ...it, ...res });
  } catch (e) {
    ul.textContent = '';
    ul.append(stateRow(e.message, true));
  }
}

/* ── preview ──────────────────────────────────────────────────────────── */

/* The platform's own player in an iframe. Spotify's plays 30s logged out and
 * the full track logged in; Bandcamp and SoundCloud play the whole thing. */
function showPreview(it) {
  app.preview = it;
  const box = $('#preview');
  const meta = $('#preview-meta');
  let src = it.embed;
  if (!src) {
    box.className = 'preview empty';
    box.style.height = '';
    box.innerHTML = `<div class="preview-empty">${icon('disc')}<p>No player for this one.</p></div>`;
    meta.textContent = '';
    return;
  }
  if (it.source === 'soundcloud') src = src.replace('auto_play=false', 'auto_play=true');
  if (it.source === 'spotify') src += (src.includes('?') ? '&' : '?') + 'theme=0';
  // Each platform's player has a natural height; a stretched one is mostly blank.
  const isTrackEmbed = !it.kind || it.kind === 'track';
  const h = it.source === 'spotify' ? (isTrackEmbed ? 152 : 380)
    : it.source === 'bandcamp' ? (isTrackEmbed ? 120 : 470) : (isTrackEmbed ? 166 : 380);
  box.className = 'preview';
  box.style.height = h + 'px';
  box.innerHTML = `<iframe src="${esc(src)}" allow="autoplay; encrypted-media; clipboard-write" loading="lazy" title="preview"></iframe>`;

  meta.textContent = '';
  const t = doc.createElement('div');
  t.className = 'pm-title';
  t.innerHTML = pill(it.source);
  t.append(doc.createTextNode(it.name || ''));
  const s = doc.createElement('div');
  s.className = 'pm-sub';
  s.textContent = C.subtitle(it);
  const acts = doc.createElement('div');
  acts.className = 'pm-actions';
  const isTrack = !it.kind || it.kind === 'track';
  const add = (label, cls, fn, html) => {
    const b = doc.createElement('button');
    if (html) b.innerHTML = html + label; else b.textContent = label;
    b.className = cls || '';
    b.addEventListener('click', fn);
    acts.append(b);
  };
  if (isTrack && it.source !== 'spotify' && it.streamable !== false) {
    add('DECK A', 'a', (e) => sendToDeck('A', it, e.currentTarget));
    add('DECK B', 'b', (e) => sendToDeck('B', it, e.currentTarget));
  }
  if (isTrack) {
    const has = app.setlist.some(x => x.key === keyOf(it));
    add(has ? 'IN MY SET' : 'ADD TO MY SET', 'mini' + (has ? ' on' : ''),
      () => { toggleSet(it); showPreview(it); }, icon(has ? 'check' : 'plus'));
  }
  if (it.source === 'spotify' && isTrack) {
    // The bridge: a Spotify find is DRM'd, but the same track is often on a
    // platform that streams plainly. One click searches it there.
    const q = [it.artists, it.name].filter(Boolean).join(' ');
    add('FIND ON BANDCAMP', 'mini', () => { $('#q').value = q; setSource('bandcamp'); search(); });
    add('FIND ON SOUNDCLOUD', 'mini', () => { $('#q').value = q; setSource('soundcloud'); search(); });
  }
  if (it.url) add(C.SOURCES[it.source].label, 'mini', () => root.open(it.url, '_blank', 'noreferrer'), icon('ext'));
  meta.append(t, s, acts);
}

function setSource(src) {
  app.source = src;
  $$('#sources .chip').forEach(x => x.classList.toggle('on', x.dataset.source === src));
}

/* ── MY SET ───────────────────────────────────────────────────────────── */

function toggleSet(it) {
  const key = keyOf(it);
  const i = app.setlist.findIndex(s => s.key === key);
  if (i >= 0) {
    app.setlist.splice(i, 1);
    toast('taken out of MY SET');
  } else {
    app.setlist.push({
      key, source: it.source, id: it.id, bc_id: it.bc_id, kind: it.kind || 'track',
      name: it.title || it.name, artists: it.artists || '', art: it.art || null,
      url: it.url || null, embed: it.embed || null, duration_ms: it.duration_ms
        || (it.buffer ? Math.round(it.buffer.duration * 1000) : null),
      streamable: it.streamable !== false,
    });
    toast(`#${app.setlist.length} in MY SET — it is at the top of the page`);
    bumpPicks();
  }
  saveSet();
  paintSet();
  refreshLocal();
  repaintResults();
}

/* A pick lands in a rail the eye was not on, so say so once: the rail flashes
 * and the newest card scrolls into view. */
function bumpPicks() {
  const el = $('#picks');
  if (!el) return;
  el.classList.remove('bump');
  void el.offsetWidth;
  el.classList.add('bump');
  setTimeout(() => {
    const last = $('#picks-rows').lastElementChild;
    if (last && last.scrollIntoView) last.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, 0);
}

/* Re-render the results so every + reflects what is in the set now. */
function repaintResults() {
  const ul = $('#results');
  if (!ul) return;
  const sel = $('#results li.sel');
  const selIdx = sel ? Array.from(ul.children).indexOf(sel) : -1;
  const head = ul.querySelector('li.head');
  if (!app.results.length || ul.querySelector('li.state')) return;
  ul.textContent = '';
  if (head) ul.append(head);
  app.results.forEach(x => ul.append(resultRow(x)));
  if (selIdx >= 0 && ul.children[selIdx]) ul.children[selIdx].classList.add('sel');
}

function moveSet(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= app.setlist.length) return;
  [app.setlist[i], app.setlist[j]] = [app.setlist[j], app.setlist[i]];
  saveSet();
  paintSet();
}

function saveSet() {
  try { localStorage.setItem('crates.set', JSON.stringify(app.setlist)); } catch (e) { /* quota */ }
  // If a playlist is open, MY SET *is* that playlist — playlists.js writes the
  // new order back. Nothing here has to know whether that call succeeded; it
  // reports its own trouble, and the local copy above is the fallback.
  if (M.playlists) M.playlists.setChanged(app.setlist);
}

/* Paint MY SET: the rail across the top of the crate, and the "up next" strip
 * under the booth. Both are the same card — the rail carries the controls.
 * A pick that has been decoded shows its tempo and key, and the key lights up
 * against whichever deck is playing. */
function paintSet() {
  const rail = $('#picks-rows');
  const strip = $('#booth-set-rows');
  rail.textContent = '';
  strip.textContent = '';
  const n = app.setlist.length;
  $('#picks').classList.toggle('empty', !n);
  $('#picks-count').textContent = n;
  $('#set-hint').innerHTML = n
    ? `${n} track${n === 1 ? '' : 's'} · ${C.dur(app.setlist.reduce((a, s) => a + (s.duration_ms || 0), 0))} · drag order with the arrows`
    : `press ${icon('plus')} on any track below and it lands up here`;
  if (!n) {
    const e = doc.createElement('div');
    e.className = 'picks-empty';
    e.innerHTML = `${icon('plus')} nothing picked yet — the tracks you pick show up here, first in line`;
    rail.append(e);
    const s2 = doc.createElement('span');
    s2.className = 'empty';
    s2.textContent = 'nothing picked yet — add tracks in the crate';
    strip.append(s2);
    return;
  }
  const ref = referenceKey();
  app.setlist.forEach((s, i) => {
    rail.append(setCard(s, i, ref, true));
    strip.append(setCard(s, i, ref, false));
  });
}

/* One pick, as a card. `full` is the crate rail — numbered, with move and
 * remove; without it this is the compact strip under the booth. */
function setCard(s, i, ref, full) {
  const lib = app.library.find(r => r.key === s.key);
  const a = lib && lib.analysis;
  const code = a && a.camelot;
  const canPlay = !!lib || (s.source !== 'spotify' && s.streamable !== false);
  const rel = C.camelotRel(code, ref);
  const matches = !!(code && ref && rel.score >= 2);

  const card = doc.createElement('div');
  card.className = 'card' + (full ? ' full' : '');
  if (matches) card.classList.add('match');

  const num = doc.createElement('span');
  num.className = 'num';
  num.textContent = i + 1;
  card.append(num);

  if (s.art) {
    const img = doc.createElement('img');
    img.src = s.art; img.alt = ''; img.loading = 'lazy';
    img.onerror = () => { const d = doc.createElement('span'); d.className = 'noart'; d.innerHTML = icon('disc'); img.replaceWith(d); };
    card.append(img);
  } else {
    const d = doc.createElement('span'); d.className = 'noart'; d.innerHTML = icon('disc'); card.append(d);
  }

  const who = doc.createElement('span');
  who.className = 'who';
  const b = doc.createElement('b');
  b.innerHTML = pill(s.source);
  b.append(doc.createTextNode(s.name || '(untitled)'));
  b.title = s.name || '';
  const sub = doc.createElement('span');
  const state = a && a.bpm ? a.bpm.toFixed(1) + ' BPM'
    : (lib ? 'analysing…' : (canPlay ? 'loads on click' : 'preview only'));
  if (code) {
    const k = doc.createElement('em');
    k.className = 'key ' + keyClass(code);
    k.textContent = code + (matches ? ' ' + rel.label : '');
    sub.append(k, doc.createTextNode(' · '));
  }
  sub.append(doc.createTextNode(
    [s.artists, C.dur(s.duration_ms), state].filter(Boolean).join(' · ')));
  who.append(b, sub);
  card.append(who);

  const to = doc.createElement('span');
  to.className = 'to';
  const btn = (html, cls, title, fn) => {
    const el = doc.createElement('button');
    el.innerHTML = html;
    el.className = cls;
    el.title = title;
    el.addEventListener('click', (e) => { e.stopPropagation(); fn(e); });
    to.append(el);
    return el;
  };
  if (canPlay) {
    btn('A', 'a', 'load onto deck A', (e) => sendToDeck('A', lib || s, e.currentTarget));
    btn('B', 'b', 'load onto deck B', (e) => sendToDeck('B', lib || s, e.currentTarget));
  }
  if (full) {
    btn(icon('left'), 'move', 'earlier in the set', () => moveSet(i, -1));
    btn(icon('right'), 'move', 'later in the set', () => moveSet(i, 1));
    btn(icon('x'), 'drop', 'take it out of the set', () => {
      app.setlist.splice(i, 1); saveSet(); paintSet(); refreshLocal(); repaintResults();
      toast('taken out of MY SET');
    });
  }
  card.append(to);

  const prog = doc.createElement('i');
  prog.className = 'prog';
  card.append(prog);

  if (s.embed) {
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
      if (!full) tab('crate');
      showPreview({ ...s, kind: 'track' });
    });
  }
  return card;
}

/* ── platforms drawer ─────────────────────────────────────────────────── */

function bindDrawer() {
  const open = async () => {
    $('#drawer').classList.add('show');
    $('#drawer-veil').classList.add('show');
    const body = $('#drawer-body');
    body.textContent = 'loading…';
    try {
      const p = await M.api.platforms();
      app.platforms = p;
      body.textContent = '';
      // Who you are here comes first: it is the only thing in this drawer that
      // is about YOUR data rather than somebody else's API.
      if (M.playlists) body.append(await M.playlists.accountCard());
      body.append(platformCard('spotify', p.spotify), platformCard('bandcamp', p.bandcamp), platformCard('soundcloud', p.soundcloud));
      for (const src of ['youtube', 'archive']) {
        if (p[src]) body.append(platformCard(src, p[src]));
      }
      const foot = doc.createElement('p');
      foot.className = 'hint';
      foot.textContent = p.streams || '';
      body.append(foot);
    } catch (e) {
      body.textContent = 'the module API is not answering: ' + e.message;
    }
  };
  const close = () => { $('#drawer').classList.remove('show'); $('#drawer-veil').classList.remove('show'); };
  $('#platforms-btn').addEventListener('click', open);
  $('#drawer-close').addEventListener('click', close);
  $('#drawer-veil').addEventListener('click', close);
}

function platformCard(src, s) {
  const d = doc.createElement('div');
  d.className = 'pf';
  const ok = s.configured && !s.last_error;
  const rows = [];
  const dl = (k, v) => { if (v !== undefined && v !== null && v !== '') rows.push(`<dt>${esc(k)}</dt><dd>${v}</dd>`); };
  if (src === 'spotify') {
    dl('status', s.configured ? 'app keys present' : 'no app keys — search is off, embeds still play');
    dl('keys from', s.keys_source ? `<code>${esc(s.keys_source)}</code>` : '—');
    dl('client id', s.client_id ? `<code>${esc(s.client_id)}</code>` : '—');
    dl('your account', s.logged_in ? 'logged in via orbit/spotify — your playlists are readable' : 'not logged in (<code>m spotify/login</code> in orbit/spotify)');
    dl('audio', 'DRM-protected — preview in the embed, then find the track on Bandcamp or SoundCloud');
  } else if (src === 'bandcamp') {
    dl('status', s.last_error ? 'blocked' : 'reachable');
    dl('auth', s.auth);
    dl('streams', s.streams);
    dl('browser', s.browser ? 'headless Chromium available for the JS challenge' : 'no headless browser — a challenge cannot be cleared');
    if (s.last_error) dl('error', esc(s.last_error));
  } else {
    dl('status', s.last_error ? 'check' : 'reachable');
    dl('auth', s.auth);
    if (src === 'soundcloud') dl('client id', s.client_id ? `<code>${esc(s.client_id)}</code>` : 'scraped on first use');
    dl('streams', s.streams);
    if (s.tool) dl('tool', `<code>${esc(s.tool)}</code>`);
    if (s.last_error) dl('error', esc(s.last_error));
  }
  d.innerHTML = `<div class="pf-head"><span class="dot ${ok ? 'ok' : (s.configured ? 'warn' : '')}"></span>`
    + `${pill(src)}${(C.SOURCES[src] || C.SOURCES.local).label}<span class="st">${ok ? 'ready' : (src === 'spotify' ? 'keys needed' : 'check')}</span></div>`
    + `<dl>${rows.join('')}</dl>`
    + (src === 'spotify' && !s.configured
      ? `<p>Create an app at <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer">developer.spotify.com</a> and run <code>m crates/set_key client_id=… client_secret=…</code>. If orbit/spotify already has keys they are picked up automatically.</p>`
      : '');
  return d;
}

/* ── tabs and keys ────────────────────────────────────────────────────── */

function tab(name) {
  $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === name));
  if (name === 'studio') { paintSteps(true); paintRoll(); }
  if (name === 'crate') setTimeout(() => $('#q').focus(), 0);
}

$$('.tab').forEach(t => t.addEventListener('click', () => tab(t.dataset.tab)));

function bindKeys() {
  doc.addEventListener('keydown', (e) => {
    const el = doc.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
      if (e.key === 'Escape') el.blur();
      return;
    }
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
      case '/': e.preventDefault(); tab('crate'); break;
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
      if (d.buffer) {
        const p = d.position();
        $('.elapsed', el).textContent = fmtTime(p);
        $('.remain', el).textContent = '-' + fmtTime(d.duration - p);
      }
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

root.crates = app;   // a handle for the console, and for the browser tests
app.sendToDeck = sendToDeck;
app.search = search;
app.toggleSet = toggleSet;

})(typeof globalThis !== 'undefined' ? globalThis : this);
