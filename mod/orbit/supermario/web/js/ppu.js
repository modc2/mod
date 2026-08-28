/*
 * ppu.js — the RP2C02 picture unit.
 *
 * Clocked per dot: 341 dots across 262 scanlines, with the background fetched
 * eight pixels ahead into shift registers exactly as the hardware does. That
 * detail is not pedantry — it is what makes mid-frame writes to $2005/$2006
 * land on the right pixel, which is how Super Mario Bros. keeps its status bar
 * still while the world scrolls underneath, and how SMB3 splits the screen.
 *
 * The scroll state is the "loopy" model, named for the reverse engineering that
 * worked it out: v and t are packed as
 *     yyy NN YYYYY XXXXX
 *      ||| || ||||| +++++-- coarse X (which tile)
 *      ||| || +++++-------- coarse Y
 *      ||| ++-------------- nametable select
 *      +++----------------- fine Y (which row of the tile)
 * so incrementing a scroll position is a handful of bit twiddles rather than
 * three separate counters that have to agree.
 */
(function (root) {
  'use strict';

  /* The 2C02's 64 colours. There is no single "correct" table — the chip
   * generates composite video directly — so this is the widely used Nestopia
   * measurement, which is what most people picture when they picture the NES. */
  var PALETTE = [
    0x666666, 0x002A88, 0x1412A7, 0x3B00A4, 0x5C007E, 0x6E0040, 0x6C0600, 0x561D00,
    0x333500, 0x0B4800, 0x005200, 0x004F08, 0x00404D, 0x000000, 0x000000, 0x000000,
    0xADADAD, 0x155FD9, 0x4240FF, 0x7527FE, 0xA01ACC, 0xB71E7B, 0xB53120, 0x994E00,
    0x6B6D00, 0x388700, 0x0C9300, 0x008F32, 0x007C8D, 0x000000, 0x000000, 0x000000,
    0xFFFEFF, 0x64B0FF, 0x9290FF, 0xC676FF, 0xF36AFF, 0xFE6ECC, 0xFE8170, 0xEA9E22,
    0xBCBE00, 0x88D800, 0x5CE430, 0x45E082, 0x48CDDE, 0x4F4F4F, 0x000000, 0x000000,
    0xFFFEFF, 0xC0DFFF, 0xD3D2FF, 0xE8C8FF, 0xFBC2FF, 0xFEC4EA, 0xFECCC5, 0xF7D8A5,
    0xE4E594, 0xCFEF96, 0xBDF4AB, 0xB3F3CC, 0xB5EBF2, 0xB8B8B8, 0x000000, 0x000000
  ];

  /* Colour emphasis dims the two channels it does not boost. Precompute all
   * eight combinations so the hot loop is one array index. */
  function buildPalettes() {
    var out = [];
    for (var e = 0; e < 8; e++) {
      var tab = new Uint32Array(64);
      var rf = (e & 1) ? 1.0 : ((e & 6) ? 0.75 : 1.0);
      var gf = (e & 2) ? 1.0 : ((e & 5) ? 0.75 : 1.0);
      var bf = (e & 4) ? 1.0 : ((e & 3) ? 0.75 : 1.0);
      for (var i = 0; i < 64; i++) {
        var c = PALETTE[i];
        var r = Math.min(255, ((c >> 16) & 0xFF) * rf) | 0;
        var g = Math.min(255, ((c >> 8) & 0xFF) * gf) | 0;
        var b = Math.min(255, (c & 0xFF) * bf) | 0;
        tab[i] = (0xFF << 24) | (b << 16) | (g << 8) | r;   // canvas byte order
      }
      out.push(tab);
    }
    return out;
  }

  var RGBA = buildPalettes();

  function PPU(nes) {
    this.nes = nes;
    this.vram = new Uint8Array(0x1000);   // 2K, or 4K on four-screen carts
    this.palette = new Uint8Array(32);
    this.oam = new Uint8Array(256);
    this.frame = new Uint32Array(256 * 240);
    this.reset();
  }

  PPU.prototype.reset = function () {
    this.ctrl = 0; this.mask = 0; this.status = 0;
    this.oamAddr = 0;
    this.v = 0; this.t = 0; this.x = 0; this.w = 0;
    this.readBuffer = 0;
    this.scanline = 261; this.dot = 0;
    this.frameCount = 0;
    this.oddFrame = false;
    this.cycles = 0;                 // total dots, for the MMC3 A12 filter
    this.nmiOccurred = false;
    this.nmiLine = false;
    this.vblSetCycle = -100;         // far enough back to never look like a race
    this.openBus = 0;

    // Background pipeline.
    this.ntByte = 0; this.atByte = 0; this.bgLo = 0; this.bgHi = 0;
    this.shiftLo = 0; this.shiftHi = 0; this.attrLo = 0; this.attrHi = 0;

    // Up to eight sprites are latched per scanline.
    this.sprites = [];
    for (var i = 0; i < 8; i++) {
      this.sprites.push({ x: 0, pattern: 0, palette: 0, priority: 0, zero: false });
    }
    this.spriteCount = 0;
    this.frame.fill(RGBA[0][0x0F]);
  };

  // ── register file ────────────────────────────────────────────────────────

  PPU.prototype.readRegister = function (addr) {
    switch (addr & 7) {
      case 2: {
        // Reading the status register clears vblank and resets the $2005/$2006
        // write toggle. The low five bits are whatever was last on the bus.
        var r = (this.status & 0xE0) | (this.openBus & 0x1F);
        // The race: a read landing on the same cycle the flag goes up reads it
        // back clear, and a read within a cycle or so of that cancels the NMI
        // the PPU was about to deliver. Games that poll $2002 in a tight loop
        // around vblank depend on both halves.
        var since = this.cycles - this.vblSetCycle;
        if (since <= 0) r &= ~0x80;
        if (since <= 2) this.nes.cpu.nmiPending = false;
        this.status &= ~0x80;
        this.nmiOccurred = false;
        this.updateNMI();
        this.w = 0;
        this.openBus = r;
        return r;
      }
      case 4: {
        var v = this.oam[this.oamAddr];
        // The unused attribute bits of every sprite read back as zero.
        if ((this.oamAddr & 3) === 2) v &= 0xE3;
        this.openBus = v;
        return v;
      }
      case 7: {
        var a = this.v & 0x3FFF;
        var out;
        if (a >= 0x3F00) {
          // Palette reads answer immediately, but still refill the buffer from
          // the nametable memory hiding underneath.
          out = this.paletteRead(a);
          this.readBuffer = this.busRead(a - 0x1000);
        } else {
          out = this.readBuffer;
          this.readBuffer = this.busRead(a);
        }
        this.v = (this.v + ((this.ctrl & 4) ? 32 : 1)) & 0x7FFF;
        this.nes.mapper.ppuA12(this.v & 0x3FFF, this.cycles);
        this.openBus = out;
        return out;
      }
    }
    return this.openBus;    // $2000/$2001/$2003/$2005/$2006 are write-only
  };

  PPU.prototype.writeRegister = function (addr, value) {
    this.openBus = value;
    switch (addr & 7) {
      case 0:
        this.ctrl = value;
        this.t = (this.t & 0xF3FF) | ((value & 3) << 10);
        // Toggling the enable while vblank is up re-triggers the line, so a
        // game can deliberately fire a second NMI in the same vblank.
        this.updateNMI();
        break;
      case 1:
        this.mask = value;
        break;
      case 3:
        this.oamAddr = value;
        break;
      case 4:
        this.oam[this.oamAddr] = value;
        this.oamAddr = (this.oamAddr + 1) & 0xFF;
        break;
      case 5:
        if (this.w === 0) {
          this.t = (this.t & 0x7FE0) | (value >> 3);
          this.x = value & 7;
          this.w = 1;
        } else {
          this.t = (this.t & 0x0C1F) |
                   ((value & 7) << 12) | ((value & 0xF8) << 2);
          this.w = 0;
        }
        break;
      case 6:
        if (this.w === 0) {
          this.t = (this.t & 0x00FF) | ((value & 0x3F) << 8);
          this.w = 1;
        } else {
          this.t = (this.t & 0x7F00) | value;
          this.v = this.t;
          this.w = 0;
          // Pointing $2006 at $1xxx drives A12 high with no fetch involved.
          // Games use exactly that to clock MMC3's counter with rendering off.
          this.nes.mapper.ppuA12(this.v & 0x3FFF, this.cycles);
        }
        break;
      case 7:
        this.busWrite(this.v & 0x3FFF, value);
        this.v = (this.v + ((this.ctrl & 4) ? 32 : 1)) & 0x7FFF;
        this.nes.mapper.ppuA12(this.v & 0x3FFF, this.cycles);
        break;
    }
  };

  /* /NMI is a level, not a pulse: the PPU holds it low for as long as the
   * vblank flag and the enable bit are both set, and the CPU triggers on the
   * edge. Modelling it as a one-shot gets $2000 toggling during vblank wrong. */
  PPU.prototype.updateNMI = function () {
    var line = this.nmiOccurred && (this.ctrl & 0x80) !== 0;
    if (line && !this.nmiLine) this.nes.cpu.nmi();
    this.nmiLine = line;
  };

  // ── PPU bus ──────────────────────────────────────────────────────────────

  PPU.prototype.nametableIndex = function (addr) {
    var map = this.nes.mapper.nametableMap();
    var slot = (addr >> 10) & 3;
    return map[slot] * 0x400 + (addr & 0x3FF);
  };

  /* Every PPU bus access puts its address on the pins, and MMC3 counts the
   * rises of A12 there. Nametable fetches matter as much as pattern fetches:
   * they are what holds A12 low long enough for the next rise to count. */
  PPU.prototype.busRead = function (addr) {
    addr &= 0x3FFF;
    this.nes.mapper.ppuA12(addr, this.cycles);
    if (addr < 0x2000) return this.nes.mapper.ppuRead(addr);
    if (addr < 0x3F00) return this.vram[this.nametableIndex(addr)];
    return this.paletteRead(addr);
  };

  PPU.prototype.busWrite = function (addr, v) {
    addr &= 0x3FFF;
    this.nes.mapper.ppuA12(addr, this.cycles);
    if (addr < 0x2000) { this.nes.mapper.ppuWrite(addr, v); return; }
    if (addr < 0x3F00) { this.vram[this.nametableIndex(addr)] = v; return; }
    this.paletteWrite(addr, v);
  };

  /* $3F10/$3F14/$3F18/$3F1C are mirrors of the backdrop entries, not colours
   * of their own — a sprite palette's "transparent" slot is the screen colour. */
  PPU.prototype.paletteAddr = function (addr) {
    addr &= 0x1F;
    if ((addr & 0x13) === 0x10) addr &= ~0x10;
    return addr;
  };

  PPU.prototype.paletteRead = function (addr) {
    var v = this.palette[this.paletteAddr(addr)];
    return (this.mask & 1) ? (v & 0x30) : v;
  };

  PPU.prototype.paletteWrite = function (addr, v) {
    this.palette[this.paletteAddr(addr)] = v & 0x3F;
  };

  // ── scroll counters ──────────────────────────────────────────────────────

  PPU.prototype.renderingEnabled = function () {
    return (this.mask & 0x18) !== 0;
  };

  PPU.prototype.incrementX = function () {
    if ((this.v & 0x001F) === 31) {
      this.v &= ~0x001F;
      this.v ^= 0x0400;            // step into the next nametable
    } else {
      this.v++;
    }
  };

  PPU.prototype.incrementY = function () {
    if ((this.v & 0x7000) !== 0x7000) {
      this.v += 0x1000;            // still inside the tile
    } else {
      this.v &= ~0x7000;
      var y = (this.v & 0x03E0) >> 5;
      if (y === 29) {
        y = 0;
        this.v ^= 0x0800;          // 30 rows of tiles, then the next nametable
      } else if (y === 31) {
        y = 0;                     // the attribute rows, if a game scrolls in
      } else {
        y++;
      }
      this.v = (this.v & ~0x03E0) | (y << 5);
    }
  };

  PPU.prototype.copyX = function () {
    this.v = (this.v & ~0x041F) | (this.t & 0x041F);
  };

  PPU.prototype.copyY = function () {
    this.v = (this.v & ~0x7BE0) | (this.t & 0x7BE0);
  };

  // ── background fetch ─────────────────────────────────────────────────────

  PPU.prototype.loadShifters = function () {
    this.shiftLo = (this.shiftLo & 0xFF00) | this.bgLo;
    this.shiftHi = (this.shiftHi & 0xFF00) | this.bgHi;
    // The attribute bits are expanded to one per pixel so they can shift with
    // the pattern instead of needing a parallel lookup.
    this.attrLo = (this.attrLo & 0xFF00) | ((this.atByte & 1) ? 0xFF : 0);
    this.attrHi = (this.attrHi & 0xFF00) | ((this.atByte & 2) ? 0xFF : 0);
  };

  PPU.prototype.fetchTile = function () {
    switch (this.dot & 7) {
      case 1:
        this.loadShifters();
        this.ntByte = this.busRead(0x2000 | (this.v & 0x0FFF));
        break;
      case 3: {
        // One attribute byte covers a 4x4 tile block; which two bits apply
        // comes from bit 1 of coarse X and coarse Y.
        var a = 0x23C0 | (this.v & 0x0C00) |
                ((this.v >> 4) & 0x38) | ((this.v >> 2) & 0x07);
        var at = this.busRead(a);
        var shift = ((this.v >> 4) & 4) | (this.v & 2);
        this.atByte = (at >> shift) & 3;
        break;
      }
      case 5:
        this.bgLo = this.busRead(this.patternAddr());
        break;
      case 7:
        this.bgHi = this.busRead(this.patternAddr() + 8);
        break;
      case 0:
        this.incrementX();
        break;
    }
  };

  PPU.prototype.patternAddr = function () {
    return ((this.ctrl & 0x10) << 8) + this.ntByte * 16 + ((this.v >> 12) & 7);
  };

  // ── sprite evaluation ────────────────────────────────────────────────────

  /* Hardware scans OAM during the visible line for the line after it, which is
   * why sprites never appear on scanline 0 and why the ninth sprite on a line
   * is dropped rather than drawn. Both fall out of doing it the same way here. */
  PPU.prototype.evaluateSprites = function (line) {
    var height = (this.ctrl & 0x20) ? 16 : 8;
    var count = 0;
    for (var i = 0; i < 64; i++) {
      var y = this.oam[i * 4];
      var row = line - y;
      if (row < 0 || row >= height) continue;
      if (count < 8) {
        var tile = this.oam[i * 4 + 1];
        var attr = this.oam[i * 4 + 2];
        var xpos = this.oam[i * 4 + 3];
        if (attr & 0x80) row = height - 1 - row;      // vertical flip

        var addr;
        if (height === 16) {
          // 8x16 sprites pick their pattern table from the tile's low bit and
          // step into the second tile for the bottom half.
          addr = ((tile & 1) << 12) + (tile & 0xFE) * 16;
          if (row >= 8) { addr += 16; row -= 8; }
        } else {
          addr = ((this.ctrl & 0x08) << 9) + tile * 16;
        }
        addr += row;

        var lo = this.busRead(addr), hi = this.busRead(addr + 8);
        if (attr & 0x40) { lo = reverseByte(lo); hi = reverseByte(hi); }

        var s = this.sprites[count];
        s.x = xpos;
        s.pattern = (hi << 8) | lo;
        s.palette = 4 + (attr & 3);
        s.priority = (attr >> 5) & 1;
        s.zero = i === 0;
        count++;
      } else {
        // The real overflow logic has a well-known bug that also produces false
        // positives; games only ever check "was there a ninth", so set and stop.
        this.status |= 0x20;
        break;
      }
    }
    this.spriteCount = count;

    /* Hardware always runs eight sprite fetches, reading tile $FF into the
     * empty slots. Nothing is drawn from them, but they still drive A12 — so
     * skipping them leaves MMC3's counter short a clock on any scanline with
     * fewer than eight sprites, and SMB3's split lands in the wrong place. */
    if (count < 8) {
      var dummy = (height === 16) ? 0x1FE0 : ((this.ctrl & 0x08) << 9) + 0xFF0;
      for (var s = count; s < 8; s++) {
        this.busRead(dummy);
        this.busRead(dummy + 8);
      }
    }
  };

  function reverseByte(b) {
    b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4);
    b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2);
    b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1);
    return b;
  }

  // ── pixel ────────────────────────────────────────────────────────────────

  PPU.prototype.renderPixel = function () {
    var x = this.dot - 1;
    var y = this.scanline;

    var bg = 0;
    if ((this.mask & 0x08) && (x >= 8 || (this.mask & 0x02))) {
      var bit = 15 - this.x;
      bg = (((this.shiftHi >> bit) & 1) << 1) | ((this.shiftLo >> bit) & 1);
      if (bg) {
        bg |= ((((this.attrHi >> bit) & 1) << 1) |
               ((this.attrLo >> bit) & 1)) << 2;
      }
    }

    var sp = 0, spPriority = 0, spZero = false;
    if ((this.mask & 0x10) && (x >= 8 || (this.mask & 0x04))) {
      for (var i = 0; i < this.spriteCount; i++) {
        var s = this.sprites[i];
        var off = x - s.x;
        if (off < 0 || off > 7) continue;
        var shift = 7 - off;
        var px = (((s.pattern >> (shift + 8)) & 1) << 1) |
                 ((s.pattern >> shift) & 1);
        if (px === 0) continue;             // transparent, keep looking
        sp = s.palette * 4 + px;
        spPriority = s.priority;
        spZero = s.zero;
        break;                              // lowest OAM index wins
      }
    }

    // Sprite 0 hit: both layers opaque, both enabled, and never at x=255.
    if (spZero && bg && x !== 255) this.status |= 0x40;

    var index;
    if (sp && (!bg || !spPriority)) index = this.palette[this.paletteAddr(0x10 + (sp & 0x0F))];
    else if (bg) index = this.palette[bg & 0x1F];
    else index = this.palette[0];

    if (this.mask & 1) index &= 0x30;       // greyscale
    this.frame[y * 256 + x] = RGBA[(this.mask >> 5) & 7][index & 0x3F];
  };

  // ── the tick ─────────────────────────────────────────────────────────────

  PPU.prototype.step = function () {
    this.cycles++;
    var rendering = this.renderingEnabled();
    var visible = this.scanline < 240;
    var pre = this.scanline === 261;

    if (rendering && (visible || pre)) {
      if (this.dot >= 1 && this.dot <= 256) {
        if (visible) this.renderPixel();
        this.shiftLo = (this.shiftLo << 1) & 0xFFFF;
        this.shiftHi = (this.shiftHi << 1) & 0xFFFF;
        this.attrLo = (this.attrLo << 1) & 0xFFFF;
        this.attrHi = (this.attrHi << 1) & 0xFFFF;
        this.fetchTile();
      } else if (this.dot >= 321 && this.dot <= 336) {
        // Prefetch the first two tiles of the next line.
        this.shiftLo = (this.shiftLo << 1) & 0xFFFF;
        this.shiftHi = (this.shiftHi << 1) & 0xFFFF;
        this.attrLo = (this.attrLo << 1) & 0xFFFF;
        this.attrHi = (this.attrHi << 1) & 0xFFFF;
        this.fetchTile();
      } else if (this.dot === 337 || this.dot === 339) {
        // Two junk nametable reads. They matter: MMC5 counts them, and on odd
        // frames the second one is where the skipped dot comes from.
        this.ntByte = this.busRead(0x2000 | (this.v & 0x0FFF));
      }

      if (this.dot === 256) this.incrementY();
      if (this.dot === 257) {
        this.copyX();
        this.evaluateSprites(pre ? -1 : this.scanline);
      }
      if (pre && this.dot >= 280 && this.dot <= 304) this.copyY();

    } else if (visible && this.dot >= 1 && this.dot <= 256) {
      /* Rendering off does not mean nothing comes out: the PPU still drives
       * the backdrop colour. And when the VRAM address happens to point into
       * palette memory it drives *that* entry instead, which is how
       * full_palette.nes gets all 64 colours on screen at once. */
      var idx = (this.v & 0x3F00) === 0x3F00
        ? this.palette[this.paletteAddr(this.v & 0x1F)]
        : this.palette[0];
      if (this.mask & 1) idx &= 0x30;
      this.frame[this.scanline * 256 + this.dot - 1] =
        RGBA[(this.mask >> 5) & 7][idx & 0x3F];
    }

    if (this.scanline === 241 && this.dot === 1) {
      this.status |= 0x80;
      this.nmiOccurred = true;
      this.vblSetCycle = this.cycles;
      this.updateNMI();
      this.frameReady = true;
    }
    if (pre && this.dot === 1) {
      this.status &= ~0xE0;       // vblank, sprite 0 and overflow all clear here
      this.nmiOccurred = false;
      this.updateNMI();
    }

    this.dot++;
    if (this.dot > 340) {
      this.dot = 0;
      this.scanline++;
      if (this.scanline > 261) {
        this.scanline = 0;
        this.frameCount++;
        this.oddFrame = !this.oddFrame;
      }
    }
    // On odd frames with rendering on, the pre-render line is one dot short.
    if (pre && this.dot === 340 && this.oddFrame && rendering) {
      this.dot = 0;
      this.scanline = 0;
      this.frameCount++;
      this.oddFrame = !this.oddFrame;
    }
  };

  PPU.prototype.saveState = function () {
    return {
      ctrl: this.ctrl, mask: this.mask, status: this.status,
      oamAddr: this.oamAddr, v: this.v, t: this.t, x: this.x, w: this.w,
      readBuffer: this.readBuffer, scanline: this.scanline, dot: this.dot,
      frameCount: this.frameCount, oddFrame: this.oddFrame, cycles: this.cycles,
      vram: this.vram.slice(), palette: this.palette.slice(),
      oam: this.oam.slice()
    };
  };

  PPU.prototype.loadState = function (s) {
    this.ctrl = s.ctrl; this.mask = s.mask; this.status = s.status;
    this.oamAddr = s.oamAddr; this.v = s.v; this.t = s.t; this.x = s.x;
    this.w = s.w; this.readBuffer = s.readBuffer;
    this.scanline = s.scanline; this.dot = s.dot;
    this.frameCount = s.frameCount; this.oddFrame = s.oddFrame;
    this.cycles = s.cycles;
    this.vram.set(s.vram); this.palette.set(s.palette); this.oam.set(s.oam);
  };

  PPU.PALETTE = PALETTE;
  PPU.RGBA = RGBA;
  root.NES = root.NES || {};
  root.NES.PPU = PPU;
})(typeof globalThis !== 'undefined' ? globalThis : this);
