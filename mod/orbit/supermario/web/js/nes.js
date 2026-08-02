/*
 * nes.js — the console: address decoding, DMA, controllers, and the clock that
 * keeps the three chips in step.
 *
 * The NTSC machine runs the PPU at exactly three times the CPU rate, so the
 * loop is: run one instruction, then run three PPU dots and one APU cycle for
 * every cycle it took. Everything else here is the memory map.
 */
(function (root) {
  'use strict';

  var NES = root.NES = root.NES || {};

  // ── cartridge ────────────────────────────────────────────────────────────

  /* Parse an iNES / NES 2.0 image. The header is 16 bytes; NES 2.0 is signalled
   * in byte 7 and widens the size and mapper fields, which is the only part of
   * it that matters for the boards this build supports. */
  function parseROM(bytes) {
    var d = new Uint8Array(bytes);
    if (d.length < 16 || d[0] !== 0x4E || d[1] !== 0x45 || d[2] !== 0x53 || d[3] !== 0x1A) {
      throw new Error('not an iNES ROM (bad magic) — .nes files start with "NES\\x1a"');
    }
    var prg16k = d[4], chr8k = d[5];
    var flags6 = d[6], flags7 = d[7];
    var nes2 = (flags7 & 0x0C) === 0x08;

    var mapperId = (flags6 >> 4) | (flags7 & 0xF0);
    if (nes2) {
      mapperId |= (d[8] & 0x0F) << 8;
      prg16k |= (d[9] & 0x0F) << 8;
      chr8k |= (d[9] & 0xF0) << 4;
    }

    var offset = 16;
    if (flags6 & 0x04) offset += 512;          // trainer, which nothing needs

    var prgSize = prg16k * 16384;
    var chrSize = chr8k * 8192;
    if (offset + prgSize > d.length) {
      throw new Error('truncated ROM: header claims ' + (prgSize / 1024) +
                      'K of PRG but the file holds ' +
                      ((d.length - offset) / 1024 | 0) + 'K');
    }

    var prg = d.slice(offset, offset + prgSize);
    var chr = chrSize
      ? d.slice(offset + prgSize, offset + prgSize + chrSize)
      : new Uint8Array(8192);                  // no CHR ROM means CHR RAM

    var mirroring = (flags6 & 0x08) ? NES.mappers.FOUR
                                    : ((flags6 & 1) ? NES.mappers.VERTICAL
                                                    : NES.mappers.HORIZONTAL);

    var prgRamSize = 0x2000;
    if (nes2) {
      var shift = d[10] & 0x0F;
      if (shift) prgRamSize = 64 << shift;
    }

    return {
      prg: prg, chr: chr, chrRam: chrSize === 0,
      mapperId: mapperId, mirroring: mirroring,
      battery: !!(flags6 & 0x02),
      prgRamSize: Math.max(0x2000, prgRamSize),
      prgBanks: prg16k, chrBanks: chr8k, nes2: nes2
    };
  }

  // ── controllers ──────────────────────────────────────────────────────────

  /* A standard pad is one 8-bit shift register. While strobe is high it keeps
   * reloading from the buttons; on the falling edge it latches, and each read
   * of $4016 shifts one bit out, A first. */
  function Controller() {
    this.buttons = 0;
    this.shift = 0;
    this.strobe = false;
  }

  Controller.BUTTONS = ['a', 'b', 'select', 'start', 'up', 'down', 'left', 'right'];

  Controller.prototype.setButton = function (index, pressed) {
    if (pressed) this.buttons |= (1 << index);
    else this.buttons &= ~(1 << index);
  };

  Controller.prototype.write = function (v) {
    this.strobe = !!(v & 1);
    if (this.strobe) this.shift = this.buttons;
  };

  Controller.prototype.read = function () {
    if (this.strobe) this.shift = this.buttons;
    var bit = this.shift & 1;
    this.shift = (this.shift >> 1) | 0x80;   // reads past the eighth return 1
    return bit;
  };

  // ── console ──────────────────────────────────────────────────────────────

  function Console(options) {
    options = options || {};
    this.ram = new Uint8Array(0x800);
    this.cpu = new NES.CPU(null);
    this.cpu.bus = this;
    this.ppu = new NES.PPU(this);
    this.apu = new NES.APU(this, options.sampleRate || 44100);
    this.controllers = [new Controller(), new Controller()];
    this.mapper = null;
    this.cart = null;
    this.openBus = 0;
    this.frameCount = 0;
    this.ticked = 0;
    this.inDMA = false;
    this.masterCycle = 0;      // real elapsed CPU cycles, for interrupt timing
  }

  Console.prototype.load = function (bytes) {
    this.cart = parseROM(bytes);
    this.mapper = NES.mappers.create(this.cart);
    this.reset();
    return this.info();
  };

  Console.prototype.info = function () {
    if (!this.cart) return null;
    return {
      mapper: this.cart.mapperId,
      board: NES.mappers.names[this.cart.mapperId] || 'unknown',
      prg: this.cart.prg.length / 1024 + 'K',
      chr: this.cart.chrRam ? '8K RAM' : this.cart.chr.length / 1024 + 'K',
      mirroring: ['horizontal', 'vertical', 'single0', 'single1',
                  'four-screen'][this.cart.mirroring],
      battery: this.cart.battery,
      format: this.cart.nes2 ? 'NES 2.0' : 'iNES'
    };
  };

  Console.prototype.reset = function () {
    this.ram.fill(0);
    this.mapper.reset();
    this.ppu.reset();
    this.apu.reset();
    this.cpu.reset();          // reads the vector, so it must come last
  };

  // ── CPU bus ──────────────────────────────────────────────────────────────

  /* One CPU cycle of the other chips. The PPU runs at exactly 3x on NTSC.
   *
   * This is called from the bus accessors rather than after the instruction,
   * because a 6502 cycle *is* a memory access: by the time the CPU latches a
   * byte the PPU has already advanced through that cycle. Catching up only
   * between instructions leaves the PPU up to seven cycles behind, which is
   * enough to read $2002 on the wrong side of the vblank flag. */
  Console.prototype.tick = function () {
    this.masterCycle++;
    this.ppu.step(); this.ppu.step(); this.ppu.step();
    this.apu.step();
    this.ticked++;
  };

  Console.prototype.read = function (addr) {
    if (!this.inDMA) this.tick();
    var v;
    if (addr < 0x2000) {
      v = this.ram[addr & 0x7FF];
    } else if (addr < 0x4000) {
      v = this.ppu.readRegister(addr & 7);
    } else if (addr === 0x4015) {
      v = this.apu.readStatus();
    } else if (addr === 0x4016) {
      v = this.controllers[0].read() | (this.openBus & 0xE0);
    } else if (addr === 0x4017) {
      v = this.controllers[1].read() | (this.openBus & 0xE0);
    } else if (addr < 0x4020) {
      v = this.openBus;        // write-only APU registers read back as open bus
    } else {
      v = this.mapper.cpuRead(addr);
    }
    this.openBus = v;
    return v;
  };

  /* The DMC fetches through the same bus but must not disturb it — a read of
   * $2007 or $4016 here would drop a byte the game was about to use. */
  Console.prototype.cpuRead = function (addr) {
    if (addr < 0x2000) return this.ram[addr & 0x7FF];
    if (addr >= 0x4020) return this.mapper.cpuRead(addr);
    return this.openBus;
  };

  Console.prototype.write = function (addr, v) {
    if (!this.inDMA) this.tick();
    this.openBus = v;
    if (addr < 0x2000) {
      this.ram[addr & 0x7FF] = v;
    } else if (addr < 0x4000) {
      this.ppu.writeRegister(addr & 7, v);
    } else if (addr === 0x4014) {
      this.oamDMA(v);
    } else if (addr === 0x4016) {
      this.controllers[0].write(v);
      this.controllers[1].write(v);
    } else if (addr < 0x4018) {
      this.apu.writeRegister(addr, v);
    } else if (addr >= 0x4020) {
      this.mapper.cpuWrite(addr, v);
    }
  };

  /* $4014 copies a page into OAM and holds the CPU for 513 cycles (514 if it
   * lands on an odd cycle). Games rely on the stall to stay inside vblank. */
  Console.prototype.oamDMA = function (page) {
    var base = page << 8;
    // The copy is done up front but must not clock anything: the 513 stalled
    // cycles below are the DMA, and ticking here would run the PPU twice.
    this.inDMA = true;
    for (var i = 0; i < 256; i++) {
      this.ppu.oam[(this.ppu.oamAddr + i) & 0xFF] = this.read(base + i);
    }
    this.inDMA = false;
    this.cpu.stall += 513 + (this.cpu.cycles & 1);
  };

  // ── clock ────────────────────────────────────────────────────────────────

  Console.prototype.step = function () {
    this.ticked = 0;
    var cycles = this.cpu.step();
    // Cycles the CPU spends on internal work touch no memory, so nothing
    // ticked for them; pay them off at the end of the instruction.
    for (var i = this.ticked; i < cycles; i++) this.tick();
    // The mapper drives the same IRQ line as the APU, on its own bit.
    this.cpu.setIRQ(2, this.mapper.irq);
    return cycles;
  };

  /* Run until the PPU raises vblank. Returns the framebuffer. */
  Console.prototype.runFrame = function () {
    this.ppu.frameReady = false;
    var guard = 0;
    while (!this.ppu.frameReady) {
      this.step();
      // A jammed CPU or a game stuck with rendering off would otherwise spin
      // forever and hang the tab; one frame is ~29,780 cycles.
      if (++guard > 200000) break;
    }
    this.frameCount++;
    return this.ppu.frame;
  };

  Console.prototype.setButton = function (pad, index, pressed) {
    this.controllers[pad | 0].setButton(index, pressed);
  };

  Console.prototype.audio = function () {
    return this.apu.drain();
  };

  Console.prototype.setSampleRate = function (rate) {
    this.apu.setSampleRate(rate);
  };

  // ── save states ──────────────────────────────────────────────────────────

  /* Typed arrays are copied, not stringified: the result goes straight into
   * IndexedDB through structured clone, which keeps a state around 30K instead
   * of the ~400K JSON would cost. */
  Console.prototype.saveState = function () {
    return {
      version: 1,
      ram: this.ram.slice(),
      cpu: this.cpu.saveState(),
      ppu: this.ppu.saveState(),
      apu: this.apu.saveState(),
      mapper: this.mapper.saveState(),
      frameCount: this.frameCount
    };
  };

  Console.prototype.loadState = function (s) {
    if (!s || s.version !== 1) throw new Error('unrecognised save state');
    this.ram.set(s.ram);
    this.cpu.loadState(s.cpu);
    this.ppu.loadState(s.ppu);
    this.apu.loadState(s.apu);
    this.mapper.loadState(s.mapper);
    this.frameCount = s.frameCount;
  };

  /* Battery-backed saves are just the cart's RAM. */
  Console.prototype.getSaveRAM = function () {
    return this.cart && this.cart.battery ? this.mapper.prgRam.slice() : null;
  };

  Console.prototype.setSaveRAM = function (data) {
    if (data && this.mapper) this.mapper.prgRam.set(data.subarray(0, this.mapper.prgRam.length));
  };

  NES.Console = Console;
  NES.Controller = Controller;
  NES.parseROM = parseROM;
})(typeof globalThis !== 'undefined' ? globalThis : this);
