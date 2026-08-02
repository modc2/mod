/* audio.js — a tiny chiptune engine: two pulse voices, a triangle bass and a
 * noise channel, scheduled ahead of time on the WebAudio clock.
 *
 * Every melody here is original. Nothing is sampled and nothing is borrowed.
 */
(function (global) {
  'use strict';

  var NOTES = { C: 0, 'C#': 1, D: 2, 'D#': 3, E: 4, F: 5, 'F#': 6, G: 7,
                'G#': 8, A: 9, 'A#': 10, B: 11 };

  function freq(name) {
    if (!name || name === '-') return 0;
    var m = /^([A-G]#?)(-?\d)$/.exec(name);
    if (!m) return 0;
    var semi = NOTES[m[1]] + (parseInt(m[2], 10) + 1) * 12;   // C-1 = midi 0
    return 440 * Math.pow(2, (semi - 69) / 12);
  }

  /* ── the songs ──────────────────────────────────────────────────────
   * Each voice is a flat list of [note, beats]. A beat is a 16th at the
   * song's bpm. '-' is a rest.
   */

  /* "Fifth Avenue" — the overworld theme. Bouncy, major, a little swung. */
  var OVERWORLD = {
    bpm: 150,
    loop: true,
    lead: [
      ['G4',2],['-',1],['B4',1],['D5',2],['-',1],['B4',1],
      ['C5',2],['B4',1],['G4',1],['A4',2],['-',2],
      ['G4',2],['-',1],['B4',1],['D5',2],['-',1],['G5',1],
      ['F#5',2],['D5',1],['B4',1],['G4',2],['-',2],

      ['E5',2],['-',1],['E5',1],['D5',2],['B4',1],['G4',1],
      ['A4',2],['B4',1],['C5',1],['B4',2],['-',2],
      ['D5',1],['E5',1],['D5',1],['C5',1],['B4',2],['G4',2],
      ['A4',2],['G4',1],['F#4',1],['G4',4],

      ['G4',2],['-',1],['B4',1],['D5',2],['-',1],['B4',1],
      ['C5',2],['B4',1],['G4',1],['A4',2],['-',2],
      ['D5',1],['-',1],['D5',1],['-',1],['C5',1],['-',1],['B4',1],['-',1],
      ['A4',2],['B4',2],['G4',4],

      ['C5',2],['E5',2],['G5',2],['E5',2],
      ['D5',2],['B4',2],['G4',2],['-',2],
      ['C5',1],['D5',1],['E5',1],['F#5',1],['G5',4],
      ['D5',2],['B4',2],['G4',4]
    ],
    harm: [
      ['D4',2],['-',1],['G4',1],['B4',2],['-',1],['G4',1],
      ['E4',2],['D4',1],['B3',1],['C4',2],['-',2],
      ['D4',2],['-',1],['G4',1],['B4',2],['-',1],['D5',1],
      ['A4',2],['B4',1],['G4',1],['D4',2],['-',2],

      ['C5',2],['-',1],['C5',1],['B4',2],['D4',1],['B3',1],
      ['C4',2],['D4',1],['E4',1],['D4',2],['-',2],
      ['B4',1],['C5',1],['B4',1],['A4',1],['G4',2],['D4',2],
      ['C4',2],['B3',1],['A3',1],['B3',4],

      ['D4',2],['-',1],['G4',1],['B4',2],['-',1],['G4',1],
      ['E4',2],['D4',1],['B3',1],['C4',2],['-',2],
      ['B4',1],['-',1],['B4',1],['-',1],['A4',1],['-',1],['G4',1],['-',1],
      ['F#4',2],['G4',2],['B3',4],

      ['E4',2],['G4',2],['C5',2],['G4',2],
      ['B4',2],['G4',2],['D4',2],['-',2],
      ['E4',1],['F#4',1],['G4',1],['A4',1],['B4',4],
      ['A4',2],['G4',2],['B3',4]
    ],
    bass: [
      ['G2',2],['G2',2],['D3',2],['D3',2],
      ['C3',2],['C3',2],['D3',2],['D3',2],
      ['G2',2],['G2',2],['D3',2],['D3',2],
      ['D3',2],['D3',2],['G2',2],['G2',2],

      ['C3',2],['C3',2],['G2',2],['G2',2],
      ['A2',2],['A2',2],['D3',2],['D3',2],
      ['G2',2],['G2',2],['E3',2],['E3',2],
      ['C3',2],['C3',2],['D3',2],['D3',2],

      ['G2',2],['G2',2],['D3',2],['D3',2],
      ['C3',2],['C3',2],['D3',2],['D3',2],
      ['G2',2],['G2',2],['A2',2],['A2',2],
      ['D3',2],['D3',2],['G2',2],['G2',2],

      ['C3',2],['C3',2],['C3',2],['C3',2],
      ['G2',2],['G2',2],['D3',2],['D3',2],
      ['C3',2],['C3',2],['D3',2],['D3',2],
      ['G2',2],['D3',2],['G2',4]
    ],
    drums: 'x..x..x.x..x..x.'
  };

  /* "G Train" — the tunnel theme. Minor, sparse, walking bass. */
  var TUNNEL = {
    bpm: 132,
    loop: true,
    lead: [
      ['A4',2],['-',2],['C5',2],['-',2],
      ['B4',2],['-',2],['A4',2],['-',2],
      ['G4',2],['-',2],['A4',2],['-',2],
      ['E4',4],['-',4],

      ['A4',1],['C5',1],['E5',1],['C5',1],['A4',4],
      ['G4',1],['B4',1],['D5',1],['B4',1],['G4',4],
      ['F4',2],['E4',2],['D4',2],['C4',2],
      ['A3',4],['-',4]
    ],
    harm: [
      ['E4',2],['-',2],['A4',2],['-',2],
      ['G4',2],['-',2],['E4',2],['-',2],
      ['D4',2],['-',2],['E4',2],['-',2],
      ['C4',4],['-',4],

      ['E4',1],['A4',1],['C5',1],['A4',1],['E4',4],
      ['D4',1],['G4',1],['B4',1],['G4',1],['D4',4],
      ['C4',2],['B3',2],['A3',2],['G3',2],
      ['E3',4],['-',4]
    ],
    bass: [
      ['A2',2],['A2',2],['A2',2],['A2',2],
      ['E2',2],['E2',2],['E2',2],['E2',2],
      ['F2',2],['F2',2],['G2',2],['G2',2],
      ['A2',4],['A2',4],

      ['A2',2],['E2',2],['A2',2],['E2',2],
      ['G2',2],['D2',2],['G2',2],['D2',2],
      ['F2',2],['E2',2],['D2',2],['C2',2],
      ['A2',4],['A2',4]
    ],
    drums: 'x...x...x..xx...'
  };

  /* "Washington Park" — the last level. Brighter, marching. */
  var PARK = {
    bpm: 160,
    loop: true,
    lead: [
      ['C5',2],['E5',1],['G5',1],['E5',2],['C5',2],
      ['D5',2],['F5',1],['A5',1],['F5',2],['D5',2],
      ['E5',2],['G5',1],['C6',1],['G5',2],['E5',2],
      ['D5',2],['C5',2],['G4',4],

      ['A4',2],['C5',2],['E5',2],['C5',2],
      ['G4',2],['B4',2],['D5',2],['B4',2],
      ['F4',1],['G4',1],['A4',1],['B4',1],['C5',4],
      ['G4',2],['E4',2],['C4',4]
    ],
    harm: [
      ['G4',2],['C5',1],['E5',1],['C5',2],['G4',2],
      ['A4',2],['D5',1],['F5',1],['D5',2],['A4',2],
      ['C5',2],['E5',1],['G5',1],['E5',2],['C5',2],
      ['B4',2],['G4',2],['E4',4],

      ['E4',2],['A4',2],['C5',2],['A4',2],
      ['D4',2],['G4',2],['B4',2],['G4',2],
      ['C4',1],['D4',1],['E4',1],['F4',1],['G4',4],
      ['E4',2],['C4',2],['G3',4]
    ],
    bass: [
      ['C3',2],['C3',2],['G2',2],['G2',2],
      ['D3',2],['D3',2],['A2',2],['A2',2],
      ['C3',2],['C3',2],['E3',2],['E3',2],
      ['G2',2],['G2',2],['C3',4],

      ['A2',2],['A2',2],['E3',2],['E3',2],
      ['G2',2],['G2',2],['D3',2],['D3',2],
      ['F2',2],['G2',2],['A2',2],['B2',2],
      ['C3',4],['C3',4]
    ],
    drums: 'x.x.x.x.x.x.x.xx'
  };

  /* Star power — fast, frantic, three bars long. */
  var STAR = {
    bpm: 220,
    loop: true,
    lead: [
      ['C5',1],['E5',1],['G5',1],['C6',1],['G5',1],['E5',1],
      ['D5',1],['F5',1],['A5',1],['F5',1],
      ['C5',1],['E5',1],['G5',1],['E5',1],['C5',2]
    ],
    harm: [
      ['G4',1],['C5',1],['E5',1],['G5',1],['E5',1],['C5',1],
      ['A4',1],['D5',1],['F5',1],['D5',1],
      ['G4',1],['C5',1],['E5',1],['C5',1],['G4',2]
    ],
    bass: [
      ['C3',2],['C3',2],['G2',2],
      ['D3',2],['A2',2],
      ['C3',2],['G2',2],['C3',2]
    ],
    drums: 'x.x.x.x.'
  };

  /* Level-clear fanfare — plays once. */
  var CLEAR = {
    bpm: 150,
    loop: false,
    lead: [
      ['G4',2],['C5',2],['E5',2],['G5',2],
      ['E5',2],['G5',2],['C6',6],
      ['-',2],['A5',2],['B5',2],['C6',6]
    ],
    harm: [
      ['E4',2],['G4',2],['C5',2],['E5',2],
      ['C5',2],['E5',2],['G5',6],
      ['-',2],['F5',2],['G5',2],['E5',6]
    ],
    bass: [
      ['C3',4],['C3',4],['G2',4],['C3',4],['G2',4],['C3',8]
    ],
    drums: 'x.x.x.xx'
  };

  /* Game over. */
  var GAMEOVER = {
    bpm: 110,
    loop: false,
    lead: [['C5',3],['B4',3],['A4',3],['G4',3],['F#4',3],['F4',3],['E4',8]],
    harm: [['E4',3],['D4',3],['C4',3],['B3',3],['A#3',3],['A3',3],['G3',8]],
    bass: [['C3',6],['A2',6],['F2',6],['E2',6]],
    drums: '........'
  };

  var SONGS = { overworld: OVERWORLD, tunnel: TUNNEL, park: PARK,
                star: STAR, clear: CLEAR, gameover: GAMEOVER };

  /* ── the engine ────────────────────────────────────────────────────── */

  var ctx = null, master = null, musicGain = null, sfxGain = null;
  var muted = false;
  var cur = null;          // { song, voices:[{i,t}], startedAt }
  var timer = null;
  var LOOKAHEAD = 0.12;    // seconds of audio scheduled ahead

  function init() {
    if (ctx) return ctx;
    var AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : 0.55;
    master.connect(ctx.destination);
    musicGain = ctx.createGain();
    musicGain.gain.value = 0.32;
    musicGain.connect(master);
    sfxGain = ctx.createGain();
    sfxGain.gain.value = 0.5;
    sfxGain.connect(master);
    return ctx;
  }

  function resume() {
    if (!ctx) init();
    if (ctx && ctx.state === 'suspended') ctx.resume();
  }

  /* A single pulse-wave note. `duty` picks the timbre. */
  function pulse(f, t, dur, gain, dest, duty) {
    if (!f) return;
    var o = ctx.createOscillator();
    var g = ctx.createGain();
    o.type = 'square';
    if (duty && ctx.createPeriodicWave) {
      // approximate a 25% pulse with a short harmonic series
      var n = 12, real = new Float32Array(n), imag = new Float32Array(n);
      for (var i = 1; i < n; i++) imag[i] = Math.sin(Math.PI * i * duty) / (Math.PI * i);
      try { o.setPeriodicWave(ctx.createPeriodicWave(real, imag)); } catch (e) {}
    }
    o.frequency.setValueAtTime(f, t);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.008);
    g.gain.setValueAtTime(gain, t + Math.max(0.02, dur * 0.7));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.98);
    o.connect(g); g.connect(dest);
    o.start(t); o.stop(t + dur);
  }

  function tri(f, t, dur, gain, dest) {
    if (!f) return;
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'triangle';
    o.frequency.setValueAtTime(f, t);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.95);
    o.connect(g); g.connect(dest);
    o.start(t); o.stop(t + dur);
  }

  var noiseBuf = null;
  function noise(t, dur, gain, dest, hp) {
    if (!noiseBuf) {
      noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
      var d = noiseBuf.getChannelData(0);
      for (var i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    }
    var src = ctx.createBufferSource(); src.buffer = noiseBuf;
    var f = ctx.createBiquadFilter();
    f.type = hp ? 'highpass' : 'bandpass';
    f.frequency.value = hp || 1400;
    var g = ctx.createGain();
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f); f.connect(g); g.connect(dest);
    src.start(t); src.stop(t + dur);
  }

  /* ── the sequencer ─────────────────────────────────────────────────── */

  function play(name) {
    if (!init()) return;
    resume();
    var song = SONGS[name];
    if (!song) return;
    if (cur && cur.name === name) return;
    stopMusic();
    var beat = 60 / song.bpm / 4;              // one "beat" = a 16th
    cur = {
      name: name, song: song, beat: beat,
      t: ctx.currentTime + 0.06,
      voices: [
        { list: song.lead, i: 0, t: 0, kind: 'lead' },
        { list: song.harm, i: 0, t: 0, kind: 'harm' },
        { list: song.bass, i: 0, t: 0, kind: 'bass' }
      ],
      drumStep: 0, drumT: 0, done: false
    };
    var v;
    for (var i = 0; i < cur.voices.length; i++) { v = cur.voices[i]; v.t = cur.t; }
    cur.drumT = cur.t;
    tick();
    timer = setInterval(tick, 40);
  }

  function tick() {
    if (!cur || !ctx) return;
    var horizon = ctx.currentTime + LOOKAHEAD;
    var song = cur.song, beat = cur.beat, active = false;

    for (var i = 0; i < cur.voices.length; i++) {
      var v = cur.voices[i];
      if (!v.list || !v.list.length) continue;
      while (v.t < horizon) {
        if (v.i >= v.list.length) {
          if (!song.loop) break;
          v.i = 0;
        }
        var ev = v.list[v.i], dur = ev[1] * beat, f = freq(ev[0]);
        if (f) {
          if (v.kind === 'lead') pulse(f, v.t, dur, 0.20, musicGain, 0.5);
          else if (v.kind === 'harm') pulse(f, v.t, dur, 0.11, musicGain, 0.25);
          else tri(f, v.t, dur * 0.9, 0.30, musicGain);
        }
        v.t += dur;
        v.i++;
        active = true;
      }
      if (v.i < v.list.length || song.loop) active = true;
    }

    if (song.drums) {
      while (cur.drumT < horizon) {
        var ch = song.drums[cur.drumStep % song.drums.length];
        if (ch === 'x') noise(cur.drumT, 0.05, 0.10, musicGain, 3000);
        cur.drumT += beat * 2;
        cur.drumStep++;
      }
    }

    if (!song.loop && !active) stopMusic();
  }

  function stopMusic() {
    if (timer) { clearInterval(timer); timer = null; }
    cur = null;
  }

  /* ── sound effects ─────────────────────────────────────────────────── */

  function sweep(f0, f1, dur, gain, type, curve) {
    if (!init()) return;
    resume();
    var t = ctx.currentTime;
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || 'square';
    o.frequency.setValueAtTime(f0, t);
    if (curve === 'lin') o.frequency.linearRampToValueAtTime(f1, t + dur);
    else o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(sfxGain);
    o.start(t); o.stop(t + dur);
  }

  function arp(notes, step, gain, type) {
    if (!init()) return;
    resume();
    var t = ctx.currentTime;
    for (var i = 0; i < notes.length; i++) {
      pulse(freq(notes[i]), t + i * step, step * 1.1, gain, sfxGain, 0.5);
    }
  }

  var SFX = {
    jump: function () { sweep(320, 900, 0.16, 0.22); },
    bigjump: function () { sweep(260, 780, 0.22, 0.24); },
    coin: function () { arp(['B5', 'E6'], 0.075, 0.22); },
    stomp: function () { sweep(700, 120, 0.11, 0.25, 'square', 'lin'); noise(ctx.currentTime, 0.06, 0.12, sfxGain, 2000); },
    bump: function () { sweep(220, 90, 0.09, 0.18, 'square', 'lin'); },
    brick: function () { if (!init()) return; resume(); noise(ctx.currentTime, 0.22, 0.28, sfxGain, 900); sweep(400, 60, 0.2, 0.14, 'square', 'lin'); },
    sprout: function () { arp(['C4', 'E4', 'G4', 'C5', 'E5'], 0.05, 0.16); },
    powerup: function () { arp(['C5', 'G5', 'C6', 'E6', 'G6'], 0.055, 0.2); },
    onedown: function () { arp(['C5', 'C5', '-', 'G4', 'E4', 'C4'], 0.09, 0.2); },
    oneup: function () { arp(['E5', 'G5', 'C6', 'E6'], 0.07, 0.2); },
    cap: function () { sweep(880, 220, 0.1, 0.16, 'square'); },
    kick: function () { sweep(180, 520, 0.09, 0.2); },
    pipe: function () { sweep(600, 100, 0.35, 0.2, 'square', 'lin'); },
    flag: function () { arp(['C4','E4','G4','C5','E5','G5','C6'], 0.045, 0.16); },
    hurt: function () { sweep(500, 140, 0.3, 0.22, 'sawtooth', 'lin'); },
    boss: function () { if (!init()) return; resume(); sweep(140, 60, 0.45, 0.3, 'sawtooth', 'lin'); noise(ctx.currentTime, 0.3, 0.2, sfxGain, 400); },
    fire: function () { sweep(1200, 300, 0.12, 0.14, 'sawtooth'); },
    pause: function () { arp(['G5', 'D5'], 0.08, 0.18); },
    select: function () { arp(['E5','B5'], 0.05, 0.2); }
  };

  function sfx(name) {
    if (!init()) return;
    resume();
    var f = SFX[name];
    if (f) { try { f(); } catch (e) {} }
  }

  function setMuted(v) {
    muted = !!v;
    if (master) master.gain.value = muted ? 0 : 0.55;
    return muted;
  }

  global.SND = {
    init: init, resume: resume, play: play, stop: stopMusic, sfx: sfx,
    mute: setMuted,
    isMuted: function () { return muted; },
    toggle: function () { return setMuted(!muted); },
    current: function () { return cur && cur.name; }
  };
})(window);
