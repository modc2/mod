/* crates — the widgets. Knobs, canvases, toasts.
 *
 * Nothing here knows what a deck is. app.js hands each widget a value and a
 * callback; these functions own the pixels and the pointer maths, which is the
 * part worth keeping in one place because getting canvas scaling wrong on a
 * retina screen is invisible until it is the only thing you can see.
 */
(function (root) {
'use strict';
const M = root.CRATES || (root.CRATES = {});
const doc = root.document;

const $ = (sel, el) => (el || doc).querySelector(sel);
const $$ = (sel, el) => Array.from((el || doc).querySelectorAll(sel));

/* ── toast ────────────────────────────────────────────────────────────── */

let toastTimer = null;
function toast(msg, ms) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms || 2400);
}

/* ── knob ─────────────────────────────────────────────────────────────── */

/* A knob is a div in the markup with its range in data attributes; the pointer
 * and the label are built here. Dragging is vertical and relative — an absolute
 * mapping from cursor position would make every grab jump the value. */
function knob(el, onChange) {
  const min = parseFloat(el.dataset.min ?? 0);
  const max = parseFloat(el.dataset.max ?? 1);
  const bipolar = el.dataset.bipolar === '1';
  let value = parseFloat(el.dataset.value ?? min);

  const ind = doc.createElement('i');
  const label = doc.createElement('b');
  label.textContent = el.dataset.label || '';
  el.append(ind, label);

  const norm = () => (value - min) / (max - min);
  function render() {
    ind.style.transform = `rotate(${-135 + norm() * 270}deg)`;
    el.style.setProperty('--p', norm().toFixed(3));   // the arc in the stylesheet
    const off = bipolar ? Math.abs(value) > 0.02 : Math.abs(norm() - 0.5) > 0.02;
    el.classList.toggle('live', off);
  }

  function set(v, fire) {
    value = Math.max(min, Math.min(max, v));
    render();
    if (fire !== false && onChange) onChange(value, el);
  }

  let dragging = false, lastY = 0;
  el.addEventListener('pointerdown', (e) => {
    dragging = true; lastY = e.clientY;
    el.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  el.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    // Fine mode on shift, because a filter sweep and a trim tweak want very
    // different sensitivities from the same 40 pixels of knob.
    const scale = (e.shiftKey ? 600 : 160);
    set(value + (lastY - e.clientY) / scale * (max - min));
    lastY = e.clientY;
  });
  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    try { el.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('dblclick', () => set(bipolar ? 0 : parseFloat(el.dataset.value ?? min)));
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    set(value - Math.sign(e.deltaY) * (max - min) / 50);
  }, { passive: false });

  render();
  return { set, get: () => value, el };
}

/* ── canvas ───────────────────────────────────────────────────────────── */

/* Match the backing store to the CSS box. Without this every waveform is
 * blurry on a retina screen and the beat grid lines land between pixels. */
function fit(canvas, flex) {
  const dpr = root.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.width;
  // Assigning canvas.height rewrites the height *attribute*, so reading the
  // attribute back on the next frame would return the last backing-store size
  // and the canvas would grow by dpr every frame. Capture the design height
  // once and work from that.
  if (!canvas.dataset.h) {
    canvas.dataset.h = parseInt(canvas.getAttribute('height'), 10)
      || canvas.clientHeight || 64;
  }
  // A flexed canvas is sized by the layout, so ask the box; a fixed one owns
  // its height and has to assert it, or the backing store would drive it.
  let h;
  if (flex) {
    h = canvas.clientHeight || parseInt(canvas.dataset.h, 10);
  } else {
    h = parseInt(canvas.dataset.h, 10);
    canvas.style.height = h + 'px';
  }
  const W = Math.max(1, Math.round(w * dpr)), H = Math.max(1, Math.round(h * dpr));
  if (canvas.width !== W || canvas.height !== H) {
    canvas.width = W; canvas.height = H;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

const COLOR = {
  A: '#35c8ff', B: '#ff5f8d', accent: '#c8ff2e',
  line: '#2b2f36', dim: '#4d545e', bg: '#0b0c0e',
};

/* The whole track at a glance: peaks, the loop region, hot cues, playhead. */
function drawFull(canvas, deck) {
  const { ctx, w, h } = fit(canvas);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = COLOR.bg;
  ctx.fillRect(0, 0, w, h);
  const p = deck.peaks;
  if (!p) {
    ctx.fillStyle = 'rgba(255,255,255,.07)';
    ctx.fillRect(0, Math.round(h / 2), w, 1);
    return;
  }
  const buckets = p.length / 2, mid = h / 2;
  ctx.fillStyle = deck.id === 'A' ? '#1d6b8a' : '#8a3450';
  for (let x = 0; x < w; x++) {
    const b = Math.floor(x / w * buckets);
    const min = p[b * 2], max = p[b * 2 + 1];
    ctx.fillRect(x, mid + min * mid, 1, Math.max(1, (max - min) * mid));
  }
  const dur = deck.duration || 1;
  if (deck.loop) {
    ctx.fillStyle = 'rgba(200,255,46,.16)';
    const x0 = deck.loop.start / dur * w, x1 = deck.loop.end / dur * w;
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
  }
  deck.cues.forEach((t, i) => {
    if (t == null) return;
    const x = t / dur * w;
    ctx.fillStyle = COLOR.accent;
    ctx.fillRect(x, 0, 1, h);
    ctx.fillRect(x, 0, 7, 7);
    ctx.fillStyle = '#14170a';
    ctx.font = '7px ui-monospace, monospace';
    ctx.fillText(String(i + 1), x + 2, 6);
  });
  const x = deck.position() / dur * w;
  ctx.fillStyle = '#fff';
  ctx.fillRect(x, 0, 1, h);
}

/* Four seconds around the playhead, with the detected beat grid over it. This
 * is the view you actually beatmatch on: if the grid lines do not sit on the
 * transients, the BPM the deck found is wrong and you can see that here. */
function drawZoom(canvas, deck) {
  const { ctx, w, h } = fit(canvas, true);
  const SPAN = 4;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = COLOR.bg;
  ctx.fillRect(0, 0, w, h);
  if (!deck.buffer) {
    ctx.fillStyle = 'rgba(255,255,255,.05)';
    for (let x = 0; x < w; x += w / 16) ctx.fillRect(Math.round(x), 0, 1, h);
    ctx.fillStyle = 'rgba(255,255,255,.07)';
    ctx.fillRect(0, Math.round(h / 2), w, 1);
    return;
  }

  const pos = deck.position();
  const t0 = pos - SPAN / 2, t1 = pos + SPAN / 2;
  const px = (t) => (t - t0) / SPAN * w;

  if (deck.loop) {
    ctx.fillStyle = 'rgba(200,255,46,.14)';
    ctx.fillRect(px(deck.loop.start), 0, Math.max(1, px(deck.loop.end) - px(deck.loop.start)), h);
  }

  // The fine peaks, re-bucketed for this window so the zoom is real detail
  // rather than the overview stretched. Each column takes the extreme of every
  // fine bucket it covers, so nothing between two columns is lost.
  const p = deck.fine || deck.peaks, buckets = p.length / 2, dur = deck.duration || 1, mid = h / 2;
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  const c = deck.id === 'A' ? COLOR.A : COLOR.B;
  grad.addColorStop(0, c); grad.addColorStop(0.5, '#ffffff'); grad.addColorStop(1, c);
  ctx.fillStyle = c;
  ctx.globalAlpha = 0.9;
  const perCol = SPAN / w;
  for (let x = 0; x < w; x++) {
    const ta = t0 + x * perCol, tb = ta + perCol;
    if (tb < 0 || ta > dur) continue;
    let b0 = Math.max(0, Math.floor(ta / dur * buckets));
    const b1 = Math.min(buckets - 1, Math.floor(tb / dur * buckets));
    let lo = 0, hi = 0;
    for (let b = b0; b <= b1; b++) {
      if (p[b * 2] < lo) lo = p[b * 2];
      if (p[b * 2 + 1] > hi) hi = p[b * 2 + 1];
    }
    ctx.fillRect(x, mid + lo * mid * 0.92, 1, Math.max(1, (hi - lo) * mid * 0.92));
  }
  ctx.globalAlpha = 1;
  // a bright centre line through the body of the wave, the way a booth display reads
  ctx.fillStyle = 'rgba(255,255,255,.18)';
  ctx.fillRect(0, mid, w, 1);

  if (deck.bpm) {
    const beat = 60 / deck.bpm;
    let n = Math.floor((t0 - deck.beatOffset) / beat);
    for (let t = deck.beatOffset + n * beat; t < t1; t += beat, n++) {
      if (t < 0) continue;
      const down = ((n % 4) + 4) % 4 === 0;
      ctx.fillStyle = down ? 'rgba(200,255,46,.85)' : 'rgba(200,255,46,.28)';
      ctx.fillRect(Math.round(px(t)), down ? 0 : h * 0.72, 1, down ? h : h * 0.28);
    }
  }

  ctx.fillStyle = '#fff';
  ctx.fillRect(w / 2, 0, 1, h);
}

/* ── piano roll ───────────────────────────────────────────────────────── */

const ROLL = { rows: 40, cellH: 12, cellW: 22 };

/* Which MIDI note a row shows. Row 0 is the top, so pitch runs downward the
 * way it does on a keyboard turned on its side. */
function rollNote(row, base) { return base + (ROLL.rows - 1 - row); }
function rollRow(note, base) { return ROLL.rows - 1 - (note - base); }

function drawRoll(canvas, seq, chanName, base, playStep) {
  const steps = seq.steps;
  const w = steps * ROLL.cellW, h = ROLL.rows * ROLL.cellH;
  canvas.style.width = w + 'px';
  canvas.setAttribute('height', h);
  const dpr = root.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  ctx.fillStyle = COLOR.bg;
  ctx.fillRect(0, 0, w, h);

  // Black keys shaded, so the scale is readable without a keyboard drawn.
  const BLACK = [1, 3, 6, 8, 10];
  for (let r = 0; r < ROLL.rows; r++) {
    const note = rollNote(r, base);
    if (BLACK.includes(((note % 12) + 12) % 12)) {
      ctx.fillStyle = '#121417';
      ctx.fillRect(0, r * ROLL.cellH, w, ROLL.cellH);
    }
    if (((note % 12) + 12) % 12 === 0) {          // every C
      ctx.fillStyle = '#22262c';
      ctx.fillRect(0, r * ROLL.cellH, w, 1);
    }
  }
  for (let s = 0; s <= steps; s++) {
    ctx.fillStyle = s % 16 === 0 ? '#3a4049' : (s % 4 === 0 ? '#2b2f36' : '#1b1e22');
    ctx.fillRect(s * ROLL.cellW, 0, 1, h);
  }
  if (playStep >= 0) {
    ctx.fillStyle = 'rgba(255,255,255,.10)';
    ctx.fillRect(playStep * ROLL.cellW, 0, ROLL.cellW, h);
  }

  for (const n of (seq.pattern.notes[chanName] || [])) {
    const r = rollRow(n.note, base);
    if (r < 0 || r >= ROLL.rows) continue;
    const x = n.start * ROLL.cellW, wd = Math.max(6, n.length * ROLL.cellW - 2);
    ctx.fillStyle = COLOR.accent;
    ctx.fillRect(x + 1, r * ROLL.cellH + 1, wd, ROLL.cellH - 2);
    ctx.fillStyle = 'rgba(0,0,0,.45)';
    ctx.fillRect(x + wd - 2, r * ROLL.cellH + 1, 2, ROLL.cellH - 2);  // drag handle
  }
}

/* Canvas pixels → (step, note). Rounding down on both axes is what makes a
 * click land in the cell you are looking at rather than the one after it. */
function rollHit(canvas, ev, base, steps) {
  const r = canvas.getBoundingClientRect();
  const x = ev.clientX - r.left, y = ev.clientY - r.top;
  const step = Math.floor(x / ROLL.cellW), row = Math.floor(y / ROLL.cellH);
  if (step < 0 || step >= steps || row < 0 || row >= ROLL.rows) return null;
  return { step, note: rollNote(row, base), row, x, y };
}

/* ── misc ─────────────────────────────────────────────────────────────── */

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
}

/* Peak meters with a slow fall. Instantaneous peaks are unreadable — the eye
 * needs the decay to see how loud something actually was. */
function meter(el, value, state) {
  state.v = Math.max(value, (state.v || 0) - 0.035);
  el.style.width = Math.round(Math.min(1, state.v) * 100) + '%';
}

M.ui = {
  $, $$, toast, knob, fit, drawFull, drawZoom, drawRoll, rollHit,
  rollNote, rollRow, ROLL, fmtTime, meter, COLOR,
};

})(typeof globalThis !== 'undefined' ? globalThis : this);
