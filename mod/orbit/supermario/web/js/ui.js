/*
 * ui.js — the shell around the console: screen, sound, pads and storage.
 *
 * The emulator core knows nothing about the browser. This file owns everything
 * that does: it puts the framebuffer on a canvas, feeds the APU's samples to
 * WebAudio, turns keys and gamepads into the eight bits a controller shifts
 * out, and keeps ROMs, save states and battery saves in IndexedDB so nothing
 * ever leaves the tab.
 */
(function (root, doc) {
  'use strict';

  var NES = root.NES;
  var $ = function (id) { return doc.getElementById(id); };

  var WIDTH = 256, HEIGHT = 240;
  var FPS = 60.0988;                  // NTSC, and the rate the APU is tuned to

  // ── storage ──────────────────────────────────────────────────────────────

  var STORES = ['states', 'sram', 'roms', 'prefs'];
  var dbPromise = null;

  function db() {
    if (!dbPromise) {
      dbPromise = new Promise(function (resolve, reject) {
        var req = indexedDB.open('supermario', 1);
        req.onupgradeneeded = function () {
          STORES.forEach(function (name) {
            if (!req.result.objectStoreNames.contains(name)) {
              req.result.createObjectStore(name);
            }
          });
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    }
    return dbPromise;
  }

  /* Save states hold typed arrays; IndexedDB stores them through structured
   * clone, so they stay binary instead of ballooning into JSON. */
  function idbPut(store, key, value) {
    return db().then(function (d) {
      return new Promise(function (resolve, reject) {
        var tx = d.transaction(store, 'readwrite');
        tx.objectStore(store).put(value, key);
        tx.oncomplete = resolve;
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function idbGet(store, key) {
    return db().then(function (d) {
      return new Promise(function (resolve, reject) {
        var tx = d.transaction(store, 'readonly');
        var r = tx.objectStore(store).get(key);
        r.onsuccess = function () { resolve(r.result); };
        r.onerror = function () { reject(r.error); };
      });
    });
  }

  /* FNV-1a over the ROM: enough to key save states by cartridge, and stable
   * across sessions in a way a filename is not. */
  function romId(bytes) {
    var h = 0x811C9DC5;
    for (var i = 0; i < bytes.length; i++) {
      h ^= bytes[i];
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return h.toString(16);
  }

  // ── audio ────────────────────────────────────────────────────────────────

  /* The worklet is a ring buffer and nothing else. Samples arrive from the
   * emulator a frame at a time; the audio thread drains them at its own rate,
   * and reports how full it is so the frame loop can pace itself against the
   * sound card rather than against requestAnimationFrame. */
  var WORKLET = [
    'class NESAPU extends AudioWorkletProcessor {',
    '  constructor() {',
    '    super();',
    '    this.buf = new Float32Array(32768);',
    '    this.r = 0; this.w = 0; this.last = 0; this.n = 0;',
    '    this.port.onmessage = (e) => {',
    '      const d = e.data;',
    '      if (d.clear) { this.r = this.w = 0; return; }',
    '      const s = d.samples;',
    '      for (let i = 0; i < s.length; i++) {',
    '        const next = (this.w + 1) % this.buf.length;',
    '        if (next === this.r) break;',      // full: drop rather than wrap
    '        this.buf[this.w] = s[i]; this.w = next;',
    '      }',
    '    };',
    '  }',
    '  process(inputs, outputs) {',
    '    const out = outputs[0][0];',
    '    for (let i = 0; i < out.length; i++) {',
    '      if (this.r !== this.w) {',
    '        this.last = this.buf[this.r];',
    '        this.r = (this.r + 1) % this.buf.length;',
    '      }',
    // An underrun holds the last sample: a click is far more audible than
    // a few milliseconds of flat line.
    '      out[i] = this.last;',
    '    }',
    '    if ((this.n++ & 7) === 0) {',
    '      this.port.postMessage((this.w - this.r + this.buf.length) % this.buf.length);',
    '    }',
    '    return true;',
    '  }',
    '}',
    'registerProcessor("nes-apu", NESAPU);'
  ].join('\n');

  function Sound() {
    this.ctx = null;
    this.node = null;
    this.gain = null;
    this.level = 0;
    this.enabled = true;
    this.ready = false;
  }

  Sound.prototype.start = function (onRate) {
    var self = this;
    if (this.ctx) {
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return Promise.resolve(this.ctx.sampleRate);
    }
    var Ctor = root.AudioContext || root.webkitAudioContext;
    if (!Ctor) return Promise.resolve(0);
    this.ctx = new Ctor({ latencyHint: 'interactive' });

    var url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }));
    return this.ctx.audioWorklet.addModule(url).then(function () {
      URL.revokeObjectURL(url);
      self.node = new AudioWorkletNode(self.ctx, 'nes-apu', { outputChannelCount: [1] });
      self.node.port.onmessage = function (e) { self.level = e.data; };
      self.gain = self.ctx.createGain();
      self.gain.gain.value = self.enabled ? 0.6 : 0;
      self.node.connect(self.gain).connect(self.ctx.destination);
      self.ready = true;
      if (onRate) onRate(self.ctx.sampleRate);
      return self.ctx.sampleRate;
    }).catch(function (e) {
      console.warn('audio unavailable:', e);
      return 0;
    });
  };

  Sound.prototype.push = function (samples) {
    if (this.ready && samples.length) {
      this.node.port.postMessage({ samples: samples }, [samples.buffer]);
    }
  };

  Sound.prototype.clear = function () {
    if (this.ready) this.node.port.postMessage({ clear: true });
    this.level = 0;
  };

  Sound.prototype.setEnabled = function (on) {
    this.enabled = on;
    if (this.gain) this.gain.gain.value = on ? 0.6 : 0;
    if (on && this.ctx && this.ctx.state === 'suspended') this.ctx.resume();
  };

  // ── input ────────────────────────────────────────────────────────────────

  // Bit order is the order the shift register hands them out.
  var A = 0, B = 1, SELECT = 2, START = 3, UP = 4, DOWN = 5, LEFT = 6, RIGHT = 7;

  var KEYMAP = {
    KeyX: A, KeyK: A, Space: A,
    KeyZ: B, KeyJ: B,
    ShiftRight: SELECT, ShiftLeft: SELECT,
    Enter: START,
    ArrowUp: UP, KeyW: UP,
    ArrowDown: DOWN, KeyS: DOWN,
    ArrowLeft: LEFT, KeyA: LEFT,
    ArrowRight: RIGHT, KeyD: RIGHT
  };

  // Standard-layout gamepad: face buttons, then start/select, then the d-pad.
  var PADMAP = { 0: A, 2: A, 1: B, 3: B, 8: SELECT, 9: START,
                 12: UP, 13: DOWN, 14: LEFT, 15: RIGHT };

  // ── the app ──────────────────────────────────────────────────────────────

  function App() {
    this.nes = new NES.Console();
    this.audio = new Sound();
    this.canvas = $('screen');
    this.ctx = this.canvas.getContext('2d', { alpha: false });
    this.image = this.ctx.createImageData(WIDTH, HEIGHT);
    // Draw straight out of the PPU's framebuffer — one array, no copy.
    this.pixels = new Uint8ClampedArray(this.nes.ppu.frame.buffer);

    this.running = false;
    this.loaded = false;
    this.slot = 1;
    this.turbo = false;
    this.romId = null;
    this.romName = null;
    this.frames = 0;
    this.fps = 0;
    this.lastFpsAt = 0;
    this.sramTimer = 0;

    this.bind();
    this.resize();
    this.draw();
    this.restoreLast();
  }

  /* Blow the 256x240 framebuffer up to fill the stage. Whole-number scales
   * only, once there is room for one: a fractional scale makes some rows of
   * pixels taller than others, which on 8x8 tiles is very visible. */
  App.prototype.resize = function () {
    var stage = $('stage');
    var avail = Math.min((stage.clientWidth - 24) / WIDTH,
                         (stage.clientHeight - 24) / HEIGHT);
    var scale = avail >= 1 ? Math.floor(avail) : Math.max(avail, 0.25);
    this.canvas.style.width = Math.round(WIDTH * scale) + 'px';
    this.canvas.style.height = Math.round(HEIGHT * scale) + 'px';
  };

  App.prototype.toast = function (msg, bad) {
    var el = $('toast');
    el.textContent = msg;
    el.className = 'show' + (bad ? ' bad' : '');
    clearTimeout(this._toast);
    this._toast = setTimeout(function () { el.className = ''; }, 1800);
  };

  // ── loading ──────────────────────────────────────────────────────────────

  App.prototype.loadROM = function (bytes, name) {
    var data = new Uint8Array(bytes);
    var info;
    try {
      info = this.nes.load(data);
    } catch (e) {
      this.toast(e.message, true);
      return false;
    }

    this.loaded = true;
    this.romName = name || 'cartridge';
    this.romId = romId(data);
    this.frames = 0;

    $('drop').classList.add('hide');
    $('cart').innerHTML =
      '<span class="chip"><b>' + escapeHTML(this.romName) + '</b></span>' +
      '<span class="chip">' + info.board + ' <b>#' + info.mapper + '</b></span>' +
      '<span class="chip">PRG <b>' + info.prg + '</b></span>' +
      '<span class="chip">CHR <b>' + info.chr + '</b></span>' +
      '<span class="chip">' + info.mirroring + '</span>' +
      (info.battery ? '<span class="chip">battery</span>' : '');
    ['pause', 'reset', 'save', 'load', 'slot', 'shot'].forEach(function (id) {
      $(id).disabled = false;
    });

    var self = this;
    // Keep the ROM (and its battery save) so a refresh does not mean going
    // hunting for the file again. Both stay in this browser.
    idbPut('roms', 'last', { bytes: data, name: this.romName }).catch(noop);
    idbGet('sram', this.romId).then(function (sram) {
      if (sram) self.nes.setSaveRAM(sram);
    }).catch(noop);

    this.start();
    this.toast(this.romName + ' — ' + info.board);
    return true;
  };

  App.prototype.restoreLast = function () {
    var self = this;
    idbGet('roms', 'last').then(function (rec) {
      if (rec && rec.bytes) {
        self.loadROM(rec.bytes, rec.name);
        self.pause();               // waiting for a gesture before making noise
        self.toast('resumed ' + rec.name + ' — press PAUSE to play');
      }
    }).catch(noop);
  };

  App.prototype.readFile = function (file) {
    var self = this;
    var reader = new FileReader();
    reader.onload = function () { self.loadROM(reader.result, file.name); };
    reader.onerror = function () { self.toast('could not read the file', true); };
    reader.readAsArrayBuffer(file);
  };

  // ── run loop ─────────────────────────────────────────────────────────────

  App.prototype.start = function () {
    var self = this;
    if (!this.loaded) return;
    this.audio.start(function (rate) {
      if (rate) self.nes.setSampleRate(rate);
    });
    if (this.running) return;
    this.running = true;
    $('pause').textContent = 'PAUSE';
    this.lastFpsAt = performance.now();
    this.tick();
  };

  App.prototype.pause = function () {
    this.running = false;
    $('pause').textContent = 'PLAY';
    this.audio.clear();
  };

  App.prototype.togglePause = function () {
    if (!this.loaded) return;
    if (this.running) this.pause(); else this.start();
  };

  App.prototype.tick = function () {
    var self = this;
    if (!this.running) return;
    root.requestAnimationFrame(function () { self.tick(); });

    this.pollPads();

    /* How many frames to run this repaint. With sound on, the audio ring is
     * the clock: hold it near 100ms of samples and the emulator runs at the
     * console's real speed no matter what the display is doing. */
    var now = performance.now();
    var elapsed = Math.min(now - (this.lastTickAt || now), 100);
    this.lastTickAt = now;

    var frames;
    if (this.audio.level > 0) this.audioSeen = true;
    if (this.audio.ready && this.audio.enabled && this.audioSeen) {
      var target = this.nes.apu.sampleRate * 0.10;
      frames = 1;
      if (this.audio.level > target * 1.6) frames = 0;
      else if (this.audio.level < target * 0.5) frames = 2;
    } else {
      // No sound to pace against — muted, or a browser that never started the
      // audio thread. Fall back to the wall clock, or the emulator runs at
      // whatever rate requestAnimationFrame happens to fire at.
      this.accum = (this.accum || 0) + elapsed;
      var period = 1000 / FPS;
      frames = 0;
      while (this.accum >= period && frames < 4) { this.accum -= period; frames++; }
    }
    if (this.turbo) frames *= 4;

    for (var i = 0; i < frames; i++) {
      this.nes.runFrame();
      this.frames++;
      var samples = this.nes.audio();
      // While fast-forwarding only the last frame's audio is kept, so the
      // pitch stays right instead of playing back four times as fast.
      if (!this.turbo || i === frames - 1) this.audio.push(samples);
    }

    this.draw();
    this.stats();
    this.maybePersistSRAM();
  };

  App.prototype.draw = function () {
    this.image.data.set(this.pixels);
    this.ctx.putImageData(this.image, 0, 0);
  };

  App.prototype.stats = function () {
    var now = performance.now();
    if (now - this.lastFpsAt < 500) return;
    this.fps = Math.round(this.frames * 1000 / (now - this.lastFpsAt));
    this.frames = 0;
    this.lastFpsAt = now;
    $('stats').innerHTML = '<b>' + this.fps + '</b> fps' +
      (this.turbo ? ' · fast' : '') +
      (this.audio.ready && this.audio.enabled
        ? ' · ' + Math.round(this.audio.level / this.nes.apu.sampleRate * 1000) + 'ms buf'
        : '');
  };

  /* Battery saves are written a couple of seconds after the game stops
   * touching them, rather than every frame. */
  App.prototype.maybePersistSRAM = function () {
    if (!this.nes.cart || !this.nes.cart.battery) return;
    if (++this.sramTimer < 180) return;
    this.sramTimer = 0;
    var sram = this.nes.getSaveRAM();
    if (sram) idbPut('sram', this.romId, sram).catch(noop);
  };

  // ── save states ──────────────────────────────────────────────────────────

  App.prototype.saveState = function () {
    if (!this.loaded) return;
    var self = this;
    idbPut('states', this.romId + ':' + this.slot, this.nes.saveState())
      .then(function () { self.toast('saved to slot ' + self.slot); })
      .catch(function (e) { self.toast('save failed: ' + e, true); });
  };

  App.prototype.loadState = function () {
    if (!this.loaded) return;
    var self = this;
    idbGet('states', this.romId + ':' + this.slot).then(function (s) {
      if (!s) { self.toast('slot ' + self.slot + ' is empty', true); return; }
      self.nes.loadState(s);
      self.audio.clear();
      self.draw();
      self.toast('loaded slot ' + self.slot);
    }).catch(function (e) { self.toast('load failed: ' + e, true); });
  };

  // ── input plumbing ───────────────────────────────────────────────────────

  App.prototype.pollPads = function () {
    if (!navigator.getGamepads) return;
    var pads = navigator.getGamepads();
    for (var p = 0; p < 2; p++) {
      var pad = pads[p];
      if (!pad) continue;
      for (var b in PADMAP) {
        if (!PADMAP.hasOwnProperty(b)) continue;
        var button = pad.buttons[b];
        this.nes.setButton(p, PADMAP[b], !!(button && button.pressed));
      }
      // Analogue sticks stand in for the d-pad, with a wide dead zone.
      var ax = pad.axes[0] || 0, ay = pad.axes[1] || 0;
      if (Math.abs(ax) > 0.4) this.nes.setButton(p, ax < 0 ? LEFT : RIGHT, true);
      if (Math.abs(ay) > 0.4) this.nes.setButton(p, ay < 0 ? UP : DOWN, true);
    }
  };

  App.prototype.bind = function () {
    var self = this;

    doc.addEventListener('keydown', function (e) {
      if (e.metaKey || e.ctrlKey) return;
      if (e.code === 'KeyP') { self.togglePause(); e.preventDefault(); return; }
      if (e.code === 'F2') { self.saveState(); e.preventDefault(); return; }
      if (e.code === 'F4') { self.loadState(); e.preventDefault(); return; }
      if (e.code === 'Tab') { self.turbo = true; e.preventDefault(); return; }
      var button = KEYMAP[e.code];
      if (button === undefined) return;
      e.preventDefault();
      self.nes.setButton(0, button, true);
      // The first key is as good a gesture as any to unmute the tab.
      if (!self.audio.ctx) self.audio.start(function (r) {
        if (r) self.nes.setSampleRate(r);
      });
    });

    doc.addEventListener('keyup', function (e) {
      if (e.code === 'Tab') { self.turbo = false; e.preventDefault(); return; }
      var button = KEYMAP[e.code];
      if (button === undefined) return;
      e.preventDefault();
      self.nes.setButton(0, button, false);
    });

    root.addEventListener('resize', function () { self.resize(); });
    doc.addEventListener('fullscreenchange', function () {
      setTimeout(function () { self.resize(); }, 60);
    });

    // Releasing every button on blur stops Mario running into a pit while the
    // tab is in the background.
    root.addEventListener('blur', function () {
      for (var i = 0; i < 8; i++) {
        self.nes.setButton(0, i, false);
        self.nes.setButton(1, i, false);
      }
      self.turbo = false;
    });

    var stage = $('stage'), drop = $('drop');
    ['dragenter', 'dragover'].forEach(function (ev) {
      stage.addEventListener(ev, function (e) {
        e.preventDefault();
        drop.classList.remove('hide');
        drop.classList.add('over');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      stage.addEventListener(ev, function (e) {
        e.preventDefault();
        drop.classList.remove('over');
        if (self.loaded) drop.classList.add('hide');
      });
    });
    stage.addEventListener('drop', function (e) {
      var file = e.dataTransfer && e.dataTransfer.files[0];
      if (file) self.readFile(file);
    });

    $('file').addEventListener('change', function (e) {
      if (e.target.files[0]) self.readFile(e.target.files[0]);
      e.target.value = '';                 // so the same file can be re-picked
    });

    $('pause').addEventListener('click', function () { self.togglePause(); });
    $('reset').addEventListener('click', function () {
      self.nes.reset();
      self.audio.clear();
      self.toast('reset');
    });
    $('save').addEventListener('click', function () { self.saveState(); });
    $('load').addEventListener('click', function () { self.loadState(); });
    $('slot').addEventListener('click', function () {
      self.slot = self.slot % 4 + 1;
      this.textContent = 'SLOT ' + self.slot;
    });

    $('mute').addEventListener('click', function () {
      var on = !self.audio.enabled;
      self.audio.setEnabled(on);
      this.textContent = on ? 'SOUND ON' : 'SOUND OFF';
      this.classList.toggle('on', !on);
      if (on && !self.audio.ctx) self.audio.start(function (r) {
        if (r) self.nes.setSampleRate(r);
      });
    });

    $('crt').addEventListener('click', function () {
      var on = self.canvas.classList.toggle('scanlines');
      this.classList.toggle('on', on);
    });

    $('shot').addEventListener('click', function () { self.screenshot(); });

    $('full').addEventListener('click', function () {
      if (doc.fullscreenElement) doc.exitFullscreen();
      else $('app').requestFullscreen().catch(noop);
    });
  };

  App.prototype.screenshot = function () {
    var self = this;
    this.canvas.toBlob(function (blob) {
      var a = doc.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (self.romName || 'screen').replace(/\.nes$/i, '') + '.png';
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
  };

  function escapeHTML(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function noop() {}

  // ── boot ─────────────────────────────────────────────────────────────────

  var app = new App();

  /* The test hook. Playwright drives the emulator through this: it can load a
   * ROM without a file dialog and read back what is actually on the screen. */
  root.__nes = {
    app: app,
    nes: app.nes,
    version: '1.0.0',
    loadBytes: function (arr, name) {
      return app.loadROM(new Uint8Array(arr), name || 'test.nes');
    },
    frames: function (n) {
      for (var i = 0; i < (n || 1); i++) app.nes.runFrame();
      app.draw();
      return app.nes.frameCount;
    },
    press: function (button, down) { app.nes.setButton(0, button, down !== false); },
    // Distinct colours on screen: a dead PPU renders one flat value.
    colors: function () { return new Set(app.nes.ppu.frame).size; },
    info: function () { return app.nes.info(); },
    pixel: function (x, y) { return app.nes.ppu.frame[y * WIDTH + x]; },
    running: function () { return app.running; }
  };
})(window, document);
