/*
 * apu.js — the 2A03's five sound channels.
 *
 * Two pulses, a triangle, a noise generator and a DPCM sample player, mixed
 * through the same non-linear curves the hardware uses. Everything is clocked
 * from the CPU: pulses and noise tick every second CPU cycle, the triangle
 * every cycle, and a frame sequencer at ~240Hz drives the envelopes, the length
 * counters and the sweep units.
 *
 * Output is resampled on the fly to whatever rate WebAudio is running at and
 * handed over as a plain Float32Array; the browser side owns the ring buffer.
 */
(function (root) {
  'use strict';

  var LENGTH_TABLE = [
    10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
    12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30
  ];

  var DUTY = [
    [0, 1, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 1, 0, 0, 0],
    [1, 0, 0, 1, 1, 1, 1, 1]
  ];

  var TRIANGLE = [
    15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
  ];

  var NOISE_PERIOD = [
    4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068
  ];

  var DMC_RATE = [
    428, 380, 340, 320, 286, 254, 226, 214, 190, 160, 142, 128, 106, 84, 72, 54
  ];

  /* The mixer is not a sum: the DAC's output is non-linear in the channel
   * levels. These are the standard closed forms, tabulated once. */
  var PULSE_MIX = new Float32Array(31);
  var TND_MIX = new Float32Array(203);
  (function () {
    for (var i = 0; i < 31; i++) PULSE_MIX[i] = 95.52 / (8128.0 / i + 100);
    for (var j = 0; j < 203; j++) TND_MIX[j] = 163.67 / (24329.0 / j + 100);
    PULSE_MIX[0] = 0; TND_MIX[0] = 0;
  })();

  // ── envelope, shared by the pulses and the noise ─────────────────────────

  function Envelope() {
    this.start = false;
    this.loop = false;
    this.constant = false;
    this.volume = 0;      // doubles as the reload value for the divider
    this.divider = 0;
    this.decay = 0;
  }

  Envelope.prototype.clock = function () {
    if (this.start) {
      this.start = false;
      this.decay = 15;
      this.divider = this.volume;
      return;
    }
    if (this.divider > 0) { this.divider--; return; }
    this.divider = this.volume;
    if (this.decay > 0) this.decay--;
    else if (this.loop) this.decay = 15;
  };

  Envelope.prototype.output = function () {
    return this.constant ? this.volume : this.decay;
  };

  // ── pulse ────────────────────────────────────────────────────────────────

  function Pulse(channel) {
    this.channel = channel;    // 1 or 2 — they negate their sweep differently
    this.env = new Envelope();
    this.reset();
  }

  Pulse.prototype.reset = function () {
    this.enabled = false;
    this.duty = 0; this.step = 0;
    this.timer = 0; this.period = 0;
    this.length = 0; this.halt = false;
    this.sweepEnabled = false; this.sweepPeriod = 0; this.sweepNegate = false;
    this.sweepShift = 0; this.sweepReload = false; this.sweepDivider = 0;
  };

  Pulse.prototype.write = function (reg, v) {
    switch (reg) {
      case 0:
        this.duty = (v >> 6) & 3;
        this.halt = !!(v & 0x20);
        this.env.loop = this.halt;
        this.env.constant = !!(v & 0x10);
        this.env.volume = v & 0x0F;
        break;
      case 1:
        this.sweepEnabled = !!(v & 0x80);
        this.sweepPeriod = (v >> 4) & 7;
        this.sweepNegate = !!(v & 0x08);
        this.sweepShift = v & 7;
        this.sweepReload = true;
        break;
      case 2:
        this.period = (this.period & 0x700) | v;
        break;
      case 3:
        this.period = (this.period & 0xFF) | ((v & 7) << 8);
        if (this.enabled) this.length = LENGTH_TABLE[(v >> 3) & 0x1F];
        this.step = 0;
        this.env.start = true;
        break;
    }
  };

  Pulse.prototype.sweepTarget = function () {
    var delta = this.period >> this.sweepShift;
    if (this.sweepNegate) {
      // Pulse 1 negates with one's complement, pulse 2 with two's — the reason
      // a shared sweep setting detunes the two channels slightly.
      return this.period - delta - (this.channel === 1 ? 1 : 0);
    }
    return this.period + delta;
  };

  Pulse.prototype.clockSweep = function () {
    if (this.sweepReload) {
      this.sweepDivider = this.sweepPeriod;
      this.sweepReload = false;
    } else if (this.sweepDivider > 0) {
      this.sweepDivider--;
    } else {
      this.sweepDivider = this.sweepPeriod;
      var target = this.sweepTarget();
      if (this.sweepEnabled && this.sweepShift > 0 &&
          this.period >= 8 && target <= 0x7FF) {
        this.period = target;
      }
    }
  };

  Pulse.prototype.clockTimer = function () {
    if (this.timer > 0) { this.timer--; return; }
    this.timer = this.period;
    this.step = (this.step + 1) & 7;
  };

  Pulse.prototype.output = function () {
    if (!this.enabled || this.length === 0) return 0;
    if (this.period < 8 || this.sweepTarget() > 0x7FF) return 0;
    if (!DUTY[this.duty][this.step]) return 0;
    return this.env.output();
  };

  // ── triangle ─────────────────────────────────────────────────────────────

  function Triangle() { this.reset(); }

  Triangle.prototype.reset = function () {
    this.enabled = false;
    this.timer = 0; this.period = 0; this.step = 0;
    this.length = 0; this.halt = false;
    this.linear = 0; this.linearReload = 0; this.linearReloadFlag = false;
  };

  Triangle.prototype.write = function (reg, v) {
    switch (reg) {
      case 0:
        this.halt = !!(v & 0x80);
        this.linearReload = v & 0x7F;
        break;
      case 2:
        this.period = (this.period & 0x700) | v;
        break;
      case 3:
        this.period = (this.period & 0xFF) | ((v & 7) << 8);
        if (this.enabled) this.length = LENGTH_TABLE[(v >> 3) & 0x1F];
        this.linearReloadFlag = true;
        break;
    }
  };

  Triangle.prototype.clockLinear = function () {
    if (this.linearReloadFlag) this.linear = this.linearReload;
    else if (this.linear > 0) this.linear--;
    if (!this.halt) this.linearReloadFlag = false;
  };

  Triangle.prototype.clockTimer = function () {
    if (this.timer > 0) { this.timer--; return; }
    this.timer = this.period;
    if (this.length > 0 && this.linear > 0) this.step = (this.step + 1) & 31;
  };

  Triangle.prototype.output = function () {
    if (!this.enabled || this.length === 0 || this.linear === 0) return 0;
    // Periods below two run at ultrasonic frequency; hardware outputs a steady
    // half-level rather than a screech, and games use it to silence the channel.
    if (this.period < 2) return 7.5;
    return TRIANGLE[this.step];
  };

  // ── noise ────────────────────────────────────────────────────────────────

  function Noise() {
    this.env = new Envelope();
    this.reset();
  }

  Noise.prototype.reset = function () {
    this.enabled = false;
    this.shift = 1;           // the LFSR must never be allowed to reach zero
    this.mode = false;
    this.timer = 0; this.period = NOISE_PERIOD[0];
    this.length = 0; this.halt = false;
  };

  Noise.prototype.write = function (reg, v) {
    switch (reg) {
      case 0:
        this.halt = !!(v & 0x20);
        this.env.loop = this.halt;
        this.env.constant = !!(v & 0x10);
        this.env.volume = v & 0x0F;
        break;
      case 2:
        this.mode = !!(v & 0x80);
        this.period = NOISE_PERIOD[v & 0x0F];
        break;
      case 3:
        if (this.enabled) this.length = LENGTH_TABLE[(v >> 3) & 0x1F];
        this.env.start = true;
        break;
    }
  };

  Noise.prototype.clockTimer = function () {
    if (this.timer > 0) { this.timer--; return; }
    this.timer = this.period;
    // Tapping bit 6 instead of bit 1 shortens the sequence into a metallic
    // tone — that is the "buzz" mode games use for engines and hits.
    var feedback = (this.shift & 1) ^ ((this.shift >> (this.mode ? 6 : 1)) & 1);
    this.shift = (this.shift >> 1) | (feedback << 14);
  };

  Noise.prototype.output = function () {
    if (!this.enabled || this.length === 0 || (this.shift & 1)) return 0;
    return this.env.output();
  };

  // ── DMC ──────────────────────────────────────────────────────────────────

  function DMC(apu) {
    this.apu = apu;
    this.reset();
  }

  DMC.prototype.reset = function () {
    this.enabled = false;
    this.value = 0;
    this.sampleAddr = 0xC000; this.sampleLength = 0;
    this.currentAddr = 0xC000; this.remaining = 0;
    this.shift = 0; this.bits = 0; this.bufferEmpty = true; this.buffer = 0;
    this.timer = 0; this.period = DMC_RATE[0];
    this.loop = false; this.irqEnabled = false; this.irq = false;
  };

  DMC.prototype.write = function (reg, v) {
    switch (reg) {
      case 0:
        this.irqEnabled = !!(v & 0x80);
        if (!this.irqEnabled) this.irq = false;
        this.loop = !!(v & 0x40);
        this.period = DMC_RATE[v & 0x0F];
        break;
      case 1:
        this.value = v & 0x7F;
        break;
      case 2:
        this.sampleAddr = 0xC000 | (v << 6);
        break;
      case 3:
        this.sampleLength = (v << 4) | 1;
        break;
    }
  };

  DMC.prototype.restart = function () {
    this.currentAddr = this.sampleAddr;
    this.remaining = this.sampleLength;
  };

  /* Fetching a sample byte steals cycles from the CPU — enough of them that
   * games with heavy DPCM visibly slow down, and enough to corrupt a controller
   * read, which is the famous Zelda II / Mario 3 input glitch. */
  DMC.prototype.fill = function () {
    if (!this.bufferEmpty || this.remaining === 0) return;
    this.buffer = this.apu.nes.cpuRead(this.currentAddr);
    this.bufferEmpty = false;
    this.apu.nes.cpu.stall += 4;
    this.currentAddr = this.currentAddr === 0xFFFF ? 0x8000 : this.currentAddr + 1;
    this.remaining--;
    if (this.remaining === 0) {
      if (this.loop) this.restart();
      else if (this.irqEnabled) this.irq = true;
    }
  };

  DMC.prototype.clockTimer = function () {
    if (!this.enabled) return;
    this.fill();
    if (this.timer > 0) { this.timer--; return; }
    this.timer = this.period;

    if (this.bits === 0) {
      if (this.bufferEmpty) return;
      this.bits = 8;
      this.shift = this.buffer;
      this.bufferEmpty = true;
    }
    // Each bit nudges the 7-bit DAC up or down by two, clamped.
    if (this.shift & 1) { if (this.value <= 125) this.value += 2; }
    else if (this.value >= 2) this.value -= 2;
    this.shift >>= 1;
    this.bits--;
  };

  DMC.prototype.output = function () { return this.value; };

  // ── the APU ──────────────────────────────────────────────────────────────

  function APU(nes, sampleRate) {
    this.nes = nes;
    this.pulse1 = new Pulse(1);
    this.pulse2 = new Pulse(2);
    this.triangle = new Triangle();
    this.noise = new Noise();
    this.dmc = new DMC(this);
    this.setSampleRate(sampleRate || 44100);
    this.reset();
  }

  APU.CPU_HZ = 1789773;             // NTSC

  APU.prototype.setSampleRate = function (rate) {
    this.sampleRate = rate;
    this.cyclesPerSample = APU.CPU_HZ / rate;
  };

  APU.prototype.reset = function () {
    this.pulse1.reset(); this.pulse2.reset();
    this.triangle.reset(); this.noise.reset(); this.dmc.reset();
    this.cycle = 0;
    this.frameStep = 0;
    this.frameMode = 0;               // 0 = four-step, 1 = five-step
    this.frameIRQDisabled = false;
    this.frameIRQ = false;
    this.sampleAccum = 0;
    this.buffer = new Float32Array(4096);
    this.bufferLen = 0;
    this.lastSample = 0;              // state for the DC blocker
    this.lastOut = 0;
  };

  APU.prototype.writeRegister = function (addr, v) {
    switch (addr) {
      case 0x4000: case 0x4001: case 0x4002: case 0x4003:
        this.pulse1.write(addr & 3, v); break;
      case 0x4004: case 0x4005: case 0x4006: case 0x4007:
        this.pulse2.write(addr & 3, v); break;
      case 0x4008: case 0x4009: case 0x400A: case 0x400B:
        this.triangle.write(addr & 3, v); break;
      case 0x400C: case 0x400D: case 0x400E: case 0x400F:
        this.noise.write(addr & 3, v); break;
      case 0x4010: case 0x4011: case 0x4012: case 0x4013:
        this.dmc.write(addr & 3, v); break;
      case 0x4015:
        this.pulse1.enabled = !!(v & 1);
        this.pulse2.enabled = !!(v & 2);
        this.triangle.enabled = !!(v & 4);
        this.noise.enabled = !!(v & 8);
        if (!this.pulse1.enabled) this.pulse1.length = 0;
        if (!this.pulse2.enabled) this.pulse2.length = 0;
        if (!this.triangle.enabled) this.triangle.length = 0;
        if (!this.noise.enabled) this.noise.length = 0;
        this.dmc.enabled = !!(v & 0x10);
        if (!this.dmc.enabled) this.dmc.remaining = 0;
        else if (this.dmc.remaining === 0) this.dmc.restart();
        this.dmc.irq = false;
        break;
      case 0x4017:
        this.frameMode = (v >> 7) & 1;
        this.frameIRQDisabled = !!(v & 0x40);
        if (this.frameIRQDisabled) this.frameIRQ = false;
        this.frameStep = 0;
        this.cycle = 0;
        // Switching to five-step mode clocks everything once, immediately.
        if (this.frameMode) { this.clockQuarter(); this.clockHalf(); }
        break;
    }
    this.updateIRQ();
  };

  APU.prototype.readStatus = function () {
    var v = 0;
    if (this.pulse1.length > 0) v |= 1;
    if (this.pulse2.length > 0) v |= 2;
    if (this.triangle.length > 0) v |= 4;
    if (this.noise.length > 0) v |= 8;
    if (this.dmc.remaining > 0) v |= 0x10;
    if (this.frameIRQ) v |= 0x40;
    if (this.dmc.irq) v |= 0x80;
    this.frameIRQ = false;            // reading acknowledges the frame IRQ
    this.updateIRQ();
    return v;
  };

  APU.prototype.updateIRQ = function () {
    this.nes.cpu.setIRQ(1, this.frameIRQ || this.dmc.irq);
  };

  APU.prototype.clockQuarter = function () {
    this.pulse1.env.clock();
    this.pulse2.env.clock();
    this.noise.env.clock();
    this.triangle.clockLinear();
  };

  APU.prototype.clockHalf = function () {
    var ch = [this.pulse1, this.pulse2, this.triangle, this.noise];
    for (var i = 0; i < 4; i++) {
      var c = ch[i];
      if (c.length > 0 && !c.halt) c.length--;
    }
    this.pulse1.clockSweep();
    this.pulse2.clockSweep();
  };

  /* The frame sequencer's periods are in APU cycles (two CPU cycles each);
   * these are the CPU-cycle counts, which is what we are stepping. */
  APU.prototype.clockFrameCounter = function () {
    if (this.frameMode === 0) {
      switch (this.cycle) {
        case 7457: this.clockQuarter(); break;
        case 14913: this.clockQuarter(); this.clockHalf(); break;
        case 22371: this.clockQuarter(); break;
        case 29828:
          if (!this.frameIRQDisabled) { this.frameIRQ = true; this.updateIRQ(); }
          break;
        case 29829:
          this.clockQuarter(); this.clockHalf();
          if (!this.frameIRQDisabled) { this.frameIRQ = true; this.updateIRQ(); }
          break;
        case 29830:
          this.cycle = 0;
          if (!this.frameIRQDisabled) { this.frameIRQ = true; this.updateIRQ(); }
          break;
      }
    } else {
      switch (this.cycle) {
        case 7457: this.clockQuarter(); break;
        case 14913: this.clockQuarter(); this.clockHalf(); break;
        case 22371: this.clockQuarter(); break;
        case 37281: this.clockQuarter(); this.clockHalf(); break;
        case 37282: this.cycle = 0; break;
      }
    }
  };

  APU.prototype.step = function () {
    this.cycle++;
    this.clockFrameCounter();

    // Triangle runs at the full CPU rate; everything else at half.
    this.triangle.clockTimer();
    if (this.cycle & 1) {
      this.pulse1.clockTimer();
      this.pulse2.clockTimer();
      this.noise.clockTimer();
    }
    this.dmc.clockTimer();
    if (this.dmc.irq) this.updateIRQ();

    this.sampleAccum++;
    if (this.sampleAccum >= this.cyclesPerSample) {
      this.sampleAccum -= this.cyclesPerSample;
      this.pushSample();
    }
  };

  APU.prototype.mix = function () {
    var p = PULSE_MIX[(this.pulse1.output() + this.pulse2.output()) | 0];
    var t = 3 * this.triangle.output() + 2 * this.noise.output() +
            this.dmc.output();
    return p + TND_MIX[t | 0];
  };

  APU.prototype.pushSample = function () {
    var raw = this.mix();
    // The mix sits around +0.3; a one-pole high pass centres it so the output
    // does not eat headroom (and so pausing does not click).
    var out = raw - this.lastSample + 0.9995 * this.lastOut;
    this.lastSample = raw;
    this.lastOut = out;

    if (this.bufferLen < this.buffer.length) this.buffer[this.bufferLen++] = out;
  };

  /* Hand over everything generated since the last call. */
  APU.prototype.drain = function () {
    var out = this.buffer.slice(0, this.bufferLen);
    this.bufferLen = 0;
    return out;
  };

  APU.prototype.saveState = function () {
    var chan = function (c) {
      var o = {};
      for (var k in c) {
        if (!c.hasOwnProperty(k) || k === 'apu' || k === 'nes') continue;
        o[k] = (k === 'env') ? { start: c.env.start, loop: c.env.loop,
                                 constant: c.env.constant, volume: c.env.volume,
                                 divider: c.env.divider, decay: c.env.decay }
                             : c[k];
      }
      return o;
    };
    return {
      pulse1: chan(this.pulse1), pulse2: chan(this.pulse2),
      triangle: chan(this.triangle), noise: chan(this.noise),
      dmc: chan(this.dmc),
      cycle: this.cycle, frameStep: this.frameStep, frameMode: this.frameMode,
      frameIRQDisabled: this.frameIRQDisabled, frameIRQ: this.frameIRQ
    };
  };

  APU.prototype.loadState = function (s) {
    var put = function (c, o) {
      for (var k in o) {
        if (!o.hasOwnProperty(k)) continue;
        if (k === 'env') { for (var e in o.env) c.env[e] = o.env[e]; }
        else c[k] = o[k];
      }
    };
    put(this.pulse1, s.pulse1); put(this.pulse2, s.pulse2);
    put(this.triangle, s.triangle); put(this.noise, s.noise);
    put(this.dmc, s.dmc);
    this.cycle = s.cycle; this.frameStep = s.frameStep;
    this.frameMode = s.frameMode;
    this.frameIRQDisabled = s.frameIRQDisabled; this.frameIRQ = s.frameIRQ;
  };

  root.NES = root.NES || {};
  root.NES.APU = APU;
})(typeof globalThis !== 'undefined' ? globalThis : this);
