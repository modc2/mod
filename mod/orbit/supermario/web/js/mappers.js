/*
 * mappers.js — cartridge boards.
 *
 * A cart is more than ROM: the board decides which 8K of program and which 1K
 * of pattern data is visible at any moment, how the two nametables are mirrored,
 * and (on MMC3) when to raise an IRQ mid-frame. Every board here reduces to the
 * same shape — a table of offsets into prg[] and chr[] that its registers
 * rewrite — so the bus stays a plain array index in the hot path.
 *
 * Supported: 0 NROM, 1 MMC1, 2 UxROM, 3 CNROM, 4 MMC3, 7 AxROM, 66 GxROM.
 * That covers the Mario carts (SMB is NROM, Lost Levels/SMB2J NROM, SMB2 MMC1,
 * SMB3 MMC3, SMB/Duck Hunt GxROM) and most of the rest of the library.
 */
(function (root) {
  'use strict';

  // Nametable arrangements.
  var HORIZONTAL = 0, VERTICAL = 1, SINGLE0 = 2, SINGLE1 = 3, FOUR = 4;

  /* Which 1K of the PPU's 2K of VRAM each of the four nametable slots reads.
   * Four-screen carts bring their own second 2K and are handled in the PPU. */
  var MIRROR_MAP = {};
  MIRROR_MAP[HORIZONTAL] = [0, 0, 1, 1];
  MIRROR_MAP[VERTICAL]   = [0, 1, 0, 1];
  MIRROR_MAP[SINGLE0]    = [0, 0, 0, 0];
  MIRROR_MAP[SINGLE1]    = [1, 1, 1, 1];
  MIRROR_MAP[FOUR]       = [0, 1, 2, 3];

  // ── base ─────────────────────────────────────────────────────────────────

  function Mapper(cart) {
    this.cart = cart;
    this.prg = cart.prg;
    this.chr = cart.chr;
    this.chrRam = cart.chrRam;
    this.prgRam = new Uint8Array(cart.prgRamSize || 0x2000);
    this.mirroring = cart.mirroring;

    // Offsets into prg[] for the four 8K windows at $8000/$A000/$C000/$E000,
    // and into chr[] for the eight 1K windows across $0000-$1FFF.
    this.prgOff = new Int32Array(4);
    this.chrOff = new Int32Array(8);
    this.prgBanks = Math.max(1, (this.prg.length / 0x2000) | 0);
    this.chrBanks = Math.max(1, (this.chr.length / 0x400) | 0);

    this.irq = false;         // the board is holding the IRQ line low
    this.reset();
  }

  Mapper.prototype.reset = function () {
    for (var i = 0; i < 4; i++) this.prgBank(i, i);
    for (var j = 0; j < 8; j++) this.chrBank(j, j);
  };

  /* Point window `slot` (8K) at `bank`, counted from the end when negative —
   * `prgBank(3, -1)` is the idiom for "the last bank, whatever the size". */
  Mapper.prototype.prgBank = function (slot, bank) {
    var n = this.prgBanks;
    bank = ((bank % n) + n) % n;
    this.prgOff[slot] = bank * 0x2000;
  };

  Mapper.prototype.chrBank = function (slot, bank) {
    var n = this.chrBanks;
    bank = ((bank % n) + n) % n;
    this.chrOff[slot] = bank * 0x400;
  };

  Mapper.prototype.cpuRead = function (addr) {
    if (addr >= 0x8000) {
      return this.prg[this.prgOff[(addr >> 13) & 3] + (addr & 0x1FFF)];
    }
    if (addr >= 0x6000) return this.prgRam[(addr - 0x6000) & (this.prgRam.length - 1)];
    return 0;
  };

  Mapper.prototype.cpuWrite = function (addr, v) {
    if (addr >= 0x6000 && addr < 0x8000) {
      this.prgRam[(addr - 0x6000) & (this.prgRam.length - 1)] = v;
    }
    // $8000+ is ROM; boards that listen there override this.
  };

  Mapper.prototype.ppuRead = function (addr) {
    return this.chr[this.chrOff[(addr >> 10) & 7] + (addr & 0x3FF)];
  };

  Mapper.prototype.ppuWrite = function (addr, v) {
    if (this.chrRam) this.chr[this.chrOff[(addr >> 10) & 7] + (addr & 0x3FF)] = v;
  };

  Mapper.prototype.nametableMap = function () {
    return MIRROR_MAP[this.mirroring] || MIRROR_MAP[HORIZONTAL];
  };

  // Boards with no IRQ ignore the PPU's address line; MMC3 overrides this.
  Mapper.prototype.ppuA12 = function () {};

  Mapper.prototype.saveState = function () {
    return {
      prgOff: this.prgOff.slice(), chrOff: this.chrOff.slice(),
      mirroring: this.mirroring, irq: this.irq,
      prgRam: this.prgRam.slice(),
      chr: this.chrRam ? this.chr.slice() : null
    };
  };

  Mapper.prototype.loadState = function (s) {
    this.prgOff.set(s.prgOff); this.chrOff.set(s.chrOff);
    this.mirroring = s.mirroring; this.irq = s.irq;
    this.prgRam.set(s.prgRam);
    if (s.chr && this.chrRam) this.chr.set(s.chr);
  };

  function extend(Child, proto) {
    Child.prototype = Object.create(Mapper.prototype);
    Child.prototype.constructor = Child;
    for (var k in proto) if (proto.hasOwnProperty(k)) Child.prototype[k] = proto[k];
    return Child;
  }

  // ── 0: NROM ──────────────────────────────────────────────────────────────
  // No banking at all. 16K carts (Super Mario Bros. among them) see their
  // single bank mirrored into both halves of the address space.

  function NROM(cart) { Mapper.call(this, cart); }
  extend(NROM, {
    reset: function () {
      this.prgBank(0, 0); this.prgBank(1, 1);
      this.prgBank(2, this.prgBanks > 2 ? 2 : 0);
      this.prgBank(3, this.prgBanks > 2 ? 3 : 1);
      for (var i = 0; i < 8; i++) this.chrBank(i, i);
    }
  });

  // ── 1: MMC1 ──────────────────────────────────────────────────────────────
  // Serial: five writes shift a bit at a time into a register, and the address
  // of the fifth write picks which of the four internal registers it lands in.
  // A write with bit 7 set resets the shifter and forces 16K PRG mode.

  function MMC1(cart) { Mapper.call(this, cart); }
  extend(MMC1, {
    reset: function () {
      this.shift = 0x10;
      this.control = 0x0C;      // 16K PRG mode, $8000 switchable
      this.chrSel = [0, 0];
      this.prgSel = 0;
      this.sync();
    },

    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      if (v & 0x80) {
        this.shift = 0x10;
        this.control |= 0x0C;
        this.sync();
        return;
      }
      var done = this.shift & 1;      // the seeded 1 walks down to bit 0
      this.shift = (this.shift >> 1) | ((v & 1) << 4);
      if (!done) return;
      var value = this.shift & 0x1F;
      this.shift = 0x10;
      switch ((addr >> 13) & 3) {
        case 0:
          this.control = value;
          this.mirroring = [SINGLE0, SINGLE1, VERTICAL, HORIZONTAL][value & 3];
          break;
        case 1: this.chrSel[0] = value; break;
        case 2: this.chrSel[1] = value; break;
        // Bit 4 disables PRG-RAM. Nothing licensed depends on the disable
        // actually taking effect, so the bank select is all that is kept.
        case 3: this.prgSel = value & 0x0F; break;
      }
      this.sync();
    },

    sync: function () {
      var prgMode = (this.control >> 2) & 3;
      if (prgMode < 2) {
        // 32K mode: the low bit of the select is ignored.
        var b = (this.prgSel & 0x0E);
        this.prgBank(0, b); this.prgBank(1, b + 1);
        this.prgBank(2, b + 2); this.prgBank(3, b + 3);
      } else if (prgMode === 2) {
        // fixed first 16K at $8000
        this.prgBank(0, 0); this.prgBank(1, 1);
        this.prgBank(2, this.prgSel * 2); this.prgBank(3, this.prgSel * 2 + 1);
      } else {
        // fixed last 16K at $C000 — the common layout
        this.prgBank(0, this.prgSel * 2); this.prgBank(1, this.prgSel * 2 + 1);
        this.prgBank(2, -2); this.prgBank(3, -1);
      }
      if (this.control & 0x10) {      // two 4K CHR banks
        for (var i = 0; i < 4; i++) {
          this.chrBank(i, this.chrSel[0] * 4 + i);
          this.chrBank(i + 4, this.chrSel[1] * 4 + i);
        }
      } else {                        // one 8K bank
        var base = (this.chrSel[0] & 0x1E) * 4;
        for (var j = 0; j < 8; j++) this.chrBank(j, base + j);
      }
    },

    saveState: function () {
      var s = Mapper.prototype.saveState.call(this);
      s.mmc1 = { shift: this.shift, control: this.control,
                 chrSel: this.chrSel.slice(), prgSel: this.prgSel };
      return s;
    },

    loadState: function (s) {
      Mapper.prototype.loadState.call(this, s);
      if (s.mmc1) {
        this.shift = s.mmc1.shift; this.control = s.mmc1.control;
        this.chrSel = s.mmc1.chrSel.slice(); this.prgSel = s.mmc1.prgSel;
      }
    }
  });

  // ── 2: UxROM ─────────────────────────────────────────────────────────────
  // Switch the low 16K, hard-wire the last 16K so the reset vector never moves.

  function UxROM(cart) { Mapper.call(this, cart); }
  extend(UxROM, {
    reset: function () {
      this.prgBank(0, 0); this.prgBank(1, 1);
      this.prgBank(2, -2); this.prgBank(3, -1);
      for (var i = 0; i < 8; i++) this.chrBank(i, i);
    },
    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      this.prgBank(0, (v & 0x0F) * 2);
      this.prgBank(1, (v & 0x0F) * 2 + 1);
    }
  });

  // ── 3: CNROM ─────────────────────────────────────────────────────────────

  function CNROM(cart) { Mapper.call(this, cart); }
  extend(CNROM, {
    reset: function () {
      this.prgBank(0, 0); this.prgBank(1, 1);
      this.prgBank(2, this.prgBanks > 2 ? 2 : 0);
      this.prgBank(3, this.prgBanks > 2 ? 3 : 1);
      for (var i = 0; i < 8; i++) this.chrBank(i, i);
    },
    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      for (var i = 0; i < 8; i++) this.chrBank(i, (v & 3) * 8 + i);
    }
  });

  // ── 4: MMC3 ──────────────────────────────────────────────────────────────
  // Eight banking registers behind an index, plus the scanline counter that
  // Super Mario Bros. 3 uses to split the screen for its status bar. The
  // counter is clocked by PPU address line A12 rising, which happens once per
  // scanline while sprites and background fetch from different pattern tables.

  function MMC3(cart) { Mapper.call(this, cart); }
  extend(MMC3, {
    reset: function () {
      this.bankSelect = 0;
      this.regs = [0, 0, 0, 0, 0, 0, 6, 7];
      this.irqLatch = 0;
      this.irqCounter = 0;
      this.irqEnabled = false;
      this.irqReload = false;
      this.irq = false;
      this.a12Low = 0;          // how long A12 has been low, in PPU cycles
      this.lastA12 = 0;
      this.sync();
    },

    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      var even = (addr & 1) === 0;
      switch (addr & 0xE000) {
        case 0x8000:
          if (even) this.bankSelect = v;
          else this.regs[this.bankSelect & 7] = v;
          this.sync();
          break;
        case 0xA000:
          if (even) {
            if (this.mirroring !== FOUR) {
              this.mirroring = (v & 1) ? HORIZONTAL : VERTICAL;
            }
          }
          // odd: PRG-RAM protect, which no licensed game relies on
          break;
        case 0xC000:
          if (even) this.irqLatch = v;
          else { this.irqCounter = 0; this.irqReload = true; }
          break;
        case 0xE000:
          if (even) { this.irqEnabled = false; this.irq = false; }
          else this.irqEnabled = true;
          break;
      }
    },

    sync: function () {
      var r = this.regs;
      if (this.bankSelect & 0x40) {
        // $8000 fixed to the second-last bank, $C000 switchable
        this.prgBank(0, -2); this.prgBank(1, r[7]);
        this.prgBank(2, r[6]); this.prgBank(3, -1);
      } else {
        this.prgBank(0, r[6]); this.prgBank(1, r[7]);
        this.prgBank(2, -2); this.prgBank(3, -1);
      }
      // Two 2K banks then four 1K banks, or the same set swapped halves.
      var inv = (this.bankSelect & 0x80) ? 4 : 0;
      this.chrBank(0 ^ inv, r[0] & 0xFE); this.chrBank(1 ^ inv, (r[0] & 0xFE) + 1);
      this.chrBank(2 ^ inv, r[1] & 0xFE); this.chrBank(3 ^ inv, (r[1] & 0xFE) + 1);
      this.chrBank(4 ^ inv, r[2]); this.chrBank(5 ^ inv, r[3]);
      this.chrBank(6 ^ inv, r[4]); this.chrBank(7 ^ inv, r[5]);
    },

    /* Called by the PPU on every pattern fetch. A12 has to have been low for
     * about three dots for the board to see a real edge — the filter is what
     * stops the background's own fetches from clocking the counter. */
    ppuA12: function (addr, ppuCycle) {
      var a12 = (addr >> 12) & 1;
      if (a12 === 0) {
        this.a12Low = this.a12Low || ppuCycle;
        this.lastA12 = 0;
        return;
      }
      if (this.lastA12 === 0 && this.a12Low && (ppuCycle - this.a12Low) >= 3) {
        this.clockIRQ();
      }
      this.lastA12 = 1;
      this.a12Low = 0;
    },

    clockIRQ: function () {
      if (this.irqCounter === 0 || this.irqReload) {
        this.irqCounter = this.irqLatch;
        this.irqReload = false;
      } else {
        this.irqCounter--;
      }
      if (this.irqCounter === 0 && this.irqEnabled) this.irq = true;
    },

    saveState: function () {
      var s = Mapper.prototype.saveState.call(this);
      s.mmc3 = {
        bankSelect: this.bankSelect, regs: this.regs.slice(),
        irqLatch: this.irqLatch, irqCounter: this.irqCounter,
        irqEnabled: this.irqEnabled, irqReload: this.irqReload
      };
      return s;
    },

    loadState: function (s) {
      Mapper.prototype.loadState.call(this, s);
      if (s.mmc3) {
        this.bankSelect = s.mmc3.bankSelect; this.regs = s.mmc3.regs.slice();
        this.irqLatch = s.mmc3.irqLatch; this.irqCounter = s.mmc3.irqCounter;
        this.irqEnabled = s.mmc3.irqEnabled; this.irqReload = s.mmc3.irqReload;
      }
    }
  });

  // ── 7: AxROM ─────────────────────────────────────────────────────────────
  // 32K at a time and single-screen mirroring — the Rare boards.

  function AxROM(cart) { Mapper.call(this, cart); }
  extend(AxROM, {
    reset: function () {
      this.mirroring = SINGLE0;
      for (var i = 0; i < 4; i++) this.prgBank(i, i);
      for (var j = 0; j < 8; j++) this.chrBank(j, j);
    },
    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      var b = (v & 7) * 4;
      for (var i = 0; i < 4; i++) this.prgBank(i, b + i);
      this.mirroring = (v & 0x10) ? SINGLE1 : SINGLE0;
    }
  });

  // ── 66: GxROM ────────────────────────────────────────────────────────────
  // 32K PRG and 8K CHR selected by one register — the Super Mario Bros. /
  // Duck Hunt multicart.

  function GxROM(cart) { Mapper.call(this, cart); }
  extend(GxROM, {
    cpuWrite: function (addr, v) {
      if (addr < 0x8000) return Mapper.prototype.cpuWrite.call(this, addr, v);
      var p = ((v >> 4) & 3) * 4, c = (v & 3) * 8;
      for (var i = 0; i < 4; i++) this.prgBank(i, p + i);
      for (var j = 0; j < 8; j++) this.chrBank(j, c + j);
    }
  });

  var TABLE = {
    0: NROM, 1: MMC1, 2: UxROM, 3: CNROM, 4: MMC3, 7: AxROM, 66: GxROM
  };

  var NAMES = {
    0: 'NROM', 1: 'MMC1', 2: 'UxROM', 3: 'CNROM', 4: 'MMC3',
    7: 'AxROM', 66: 'GxROM'
  };

  function create(cart) {
    var Board = TABLE[cart.mapperId];
    if (!Board) {
      throw new Error('unsupported mapper ' + cart.mapperId +
                      ' — this build handles ' +
                      Object.keys(TABLE).join(', '));
    }
    return new Board(cart);
  }

  root.NES = root.NES || {};
  root.NES.Mapper = Mapper;
  root.NES.mappers = {
    create: create, table: TABLE, names: NAMES,
    supported: Object.keys(TABLE).map(Number),
    HORIZONTAL: HORIZONTAL, VERTICAL: VERTICAL,
    SINGLE0: SINGLE0, SINGLE1: SINGLE1, FOUR: FOUR
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
