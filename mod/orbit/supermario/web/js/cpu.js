/*
 * cpu.js — the MOS 6502 as the NES wired it (a 2A03 minus the decimal mode).
 *
 * Cycle counts are per-instruction and exact, including the +1 for an indexed
 * read that crosses a page and the +1/+2 a taken branch pays. Games lean on
 * this: the PPU is clocked from the CPU's cycle count, so a sloppy count here
 * shows up as a torn status bar rather than as a wrong number.
 *
 * The unofficial opcodes are implemented too. They are not exotica — several
 * commercial games use LAX and DCP — and nestest walks every one of them,
 * which is how this file is verified (see tests/cputest.js).
 */
(function (root) {
  'use strict';

  // Addressing modes.
  var IMP = 0, ACC = 1, IMM = 2, ZP = 3, ZPX = 4, ZPY = 5, REL = 6,
      ABS = 7, ABX = 8, ABY = 9, IND = 10, IZX = 11, IZY = 12;

  // How many bytes each mode consumes after the opcode — used by the
  // disassembler and to skip over the operands of a jammed instruction.
  var MODE_LEN = [0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 1, 1];

  /*
   * One row per opcode: [mnemonic, mode, cycles, +1 if an indexed read crosses
   * a page]. Stores and read-modify-writes never get that penalty — they always
   * pay for the extra fixup read — so the flag is only set on the loads.
   */
  var OPS = [
    ['BRK',IMP,7,0],['ORA',IZX,6,0],['KIL',IMP,0,0],['SLO',IZX,8,0],
    ['NOP',ZP ,3,0],['ORA',ZP ,3,0],['ASL',ZP ,5,0],['SLO',ZP ,5,0],
    ['PHP',IMP,3,0],['ORA',IMM,2,0],['ASL',ACC,2,0],['ANC',IMM,2,0],
    ['NOP',ABS,4,0],['ORA',ABS,4,0],['ASL',ABS,6,0],['SLO',ABS,6,0],
    ['BPL',REL,2,0],['ORA',IZY,5,1],['KIL',IMP,0,0],['SLO',IZY,8,0],
    ['NOP',ZPX,4,0],['ORA',ZPX,4,0],['ASL',ZPX,6,0],['SLO',ZPX,6,0],
    ['CLC',IMP,2,0],['ORA',ABY,4,1],['NOP',IMP,2,0],['SLO',ABY,7,0],
    ['NOP',ABX,4,1],['ORA',ABX,4,1],['ASL',ABX,7,0],['SLO',ABX,7,0],
    ['JSR',ABS,6,0],['AND',IZX,6,0],['KIL',IMP,0,0],['RLA',IZX,8,0],
    ['BIT',ZP ,3,0],['AND',ZP ,3,0],['ROL',ZP ,5,0],['RLA',ZP ,5,0],
    ['PLP',IMP,4,0],['AND',IMM,2,0],['ROL',ACC,2,0],['ANC',IMM,2,0],
    ['BIT',ABS,4,0],['AND',ABS,4,0],['ROL',ABS,6,0],['RLA',ABS,6,0],
    ['BMI',REL,2,0],['AND',IZY,5,1],['KIL',IMP,0,0],['RLA',IZY,8,0],
    ['NOP',ZPX,4,0],['AND',ZPX,4,0],['ROL',ZPX,6,0],['RLA',ZPX,6,0],
    ['SEC',IMP,2,0],['AND',ABY,4,1],['NOP',IMP,2,0],['RLA',ABY,7,0],
    ['NOP',ABX,4,1],['AND',ABX,4,1],['ROL',ABX,7,0],['RLA',ABX,7,0],
    ['RTI',IMP,6,0],['EOR',IZX,6,0],['KIL',IMP,0,0],['SRE',IZX,8,0],
    ['NOP',ZP ,3,0],['EOR',ZP ,3,0],['LSR',ZP ,5,0],['SRE',ZP ,5,0],
    ['PHA',IMP,3,0],['EOR',IMM,2,0],['LSR',ACC,2,0],['ALR',IMM,2,0],
    ['JMP',ABS,3,0],['EOR',ABS,4,0],['LSR',ABS,6,0],['SRE',ABS,6,0],
    ['BVC',REL,2,0],['EOR',IZY,5,1],['KIL',IMP,0,0],['SRE',IZY,8,0],
    ['NOP',ZPX,4,0],['EOR',ZPX,4,0],['LSR',ZPX,6,0],['SRE',ZPX,6,0],
    ['CLI',IMP,2,0],['EOR',ABY,4,1],['NOP',IMP,2,0],['SRE',ABY,7,0],
    ['NOP',ABX,4,1],['EOR',ABX,4,1],['LSR',ABX,7,0],['SRE',ABX,7,0],
    ['RTS',IMP,6,0],['ADC',IZX,6,0],['KIL',IMP,0,0],['RRA',IZX,8,0],
    ['NOP',ZP ,3,0],['ADC',ZP ,3,0],['ROR',ZP ,5,0],['RRA',ZP ,5,0],
    ['PLA',IMP,4,0],['ADC',IMM,2,0],['ROR',ACC,2,0],['ARR',IMM,2,0],
    ['JMP',IND,5,0],['ADC',ABS,4,0],['ROR',ABS,6,0],['RRA',ABS,6,0],
    ['BVS',REL,2,0],['ADC',IZY,5,1],['KIL',IMP,0,0],['RRA',IZY,8,0],
    ['NOP',ZPX,4,0],['ADC',ZPX,4,0],['ROR',ZPX,6,0],['RRA',ZPX,6,0],
    ['SEI',IMP,2,0],['ADC',ABY,4,1],['NOP',IMP,2,0],['RRA',ABY,7,0],
    ['NOP',ABX,4,1],['ADC',ABX,4,1],['ROR',ABX,7,0],['RRA',ABX,7,0],
    ['NOP',IMM,2,0],['STA',IZX,6,0],['NOP',IMM,2,0],['SAX',IZX,6,0],
    ['STY',ZP ,3,0],['STA',ZP ,3,0],['STX',ZP ,3,0],['SAX',ZP ,3,0],
    ['DEY',IMP,2,0],['NOP',IMM,2,0],['TXA',IMP,2,0],['XAA',IMM,2,0],
    ['STY',ABS,4,0],['STA',ABS,4,0],['STX',ABS,4,0],['SAX',ABS,4,0],
    ['BCC',REL,2,0],['STA',IZY,6,0],['KIL',IMP,0,0],['AHX',IZY,6,0],
    ['STY',ZPX,4,0],['STA',ZPX,4,0],['STX',ZPY,4,0],['SAX',ZPY,4,0],
    ['TYA',IMP,2,0],['STA',ABY,5,0],['TXS',IMP,2,0],['TAS',ABY,5,0],
    ['SHY',ABX,5,0],['STA',ABX,5,0],['SHX',ABY,5,0],['AHX',ABY,5,0],
    ['LDY',IMM,2,0],['LDA',IZX,6,0],['LDX',IMM,2,0],['LAX',IZX,6,0],
    ['LDY',ZP ,3,0],['LDA',ZP ,3,0],['LDX',ZP ,3,0],['LAX',ZP ,3,0],
    ['TAY',IMP,2,0],['LDA',IMM,2,0],['TAX',IMP,2,0],['LAX',IMM,2,0],
    ['LDY',ABS,4,0],['LDA',ABS,4,0],['LDX',ABS,4,0],['LAX',ABS,4,0],
    ['BCS',REL,2,0],['LDA',IZY,5,1],['KIL',IMP,0,0],['LAX',IZY,5,1],
    ['LDY',ZPX,4,0],['LDA',ZPX,4,0],['LDX',ZPY,4,0],['LAX',ZPY,4,0],
    ['CLV',IMP,2,0],['LDA',ABY,4,1],['TSX',IMP,2,0],['LAS',ABY,4,1],
    ['LDY',ABX,4,1],['LDA',ABX,4,1],['LDX',ABY,4,1],['LAX',ABY,4,1],
    ['CPY',IMM,2,0],['CMP',IZX,6,0],['NOP',IMM,2,0],['DCP',IZX,8,0],
    ['CPY',ZP ,3,0],['CMP',ZP ,3,0],['DEC',ZP ,5,0],['DCP',ZP ,5,0],
    ['INY',IMP,2,0],['CMP',IMM,2,0],['DEX',IMP,2,0],['AXS',IMM,2,0],
    ['CPY',ABS,4,0],['CMP',ABS,4,0],['DEC',ABS,6,0],['DCP',ABS,6,0],
    ['BNE',REL,2,0],['CMP',IZY,5,1],['KIL',IMP,0,0],['DCP',IZY,8,0],
    ['NOP',ZPX,4,0],['CMP',ZPX,4,0],['DEC',ZPX,6,0],['DCP',ZPX,6,0],
    ['CLD',IMP,2,0],['CMP',ABY,4,1],['NOP',IMP,2,0],['DCP',ABY,7,0],
    ['NOP',ABX,4,1],['CMP',ABX,4,1],['DEC',ABX,7,0],['DCP',ABX,7,0],
    ['CPX',IMM,2,0],['SBC',IZX,6,0],['NOP',IMM,2,0],['ISB',IZX,8,0],
    ['CPX',ZP ,3,0],['SBC',ZP ,3,0],['INC',ZP ,5,0],['ISB',ZP ,5,0],
    ['INX',IMP,2,0],['SBC',IMM,2,0],['NOP',IMP,2,0],['SBC',IMM,2,0],
    ['CPX',ABS,4,0],['SBC',ABS,4,0],['INC',ABS,6,0],['ISB',ABS,6,0],
    ['BEQ',REL,2,0],['SBC',IZY,5,1],['KIL',IMP,0,0],['ISB',IZY,8,0],
    ['NOP',ZPX,4,0],['SBC',ZPX,4,0],['INC',ZPX,6,0],['ISB',ZPX,6,0],
    ['SED',IMP,2,0],['SBC',ABY,4,1],['NOP',IMP,2,0],['ISB',ABY,7,0],
    ['NOP',ABX,4,1],['SBC',ABX,4,1],['INC',ABX,7,0],['ISB',ABX,7,0]
  ];

  function CPU(bus) {
    this.bus = bus;
    this.reset();
  }

  CPU.prototype.reset = function () {
    this.a = 0; this.x = 0; this.y = 0;
    this.sp = 0xFD;
    // Flags live unpacked; only PHP/PLP/interrupts pay to pack them.
    this.C = 0; this.Z = 0; this.I = 1; this.D = 0; this.V = 0; this.N = 0;
    this.cycles = 0;
    this.stall = 0;            // cycles owed to OAM DMA
    this.nmiPending = false;
    this.nmiAt = -1;
    this.irqLine = 0;          // level-triggered: a bitmask of IRQ sources
    this.jammed = false;
    if (this.bus) this.pc = this.read16(0xFFFC);
    else this.pc = 0;
  };

  // ── memory helpers ──────────────────────────────────────────────────────

  CPU.prototype.read = function (addr) { return this.bus.read(addr & 0xFFFF); };
  CPU.prototype.write = function (addr, v) { this.bus.write(addr & 0xFFFF, v & 0xFF); };

  CPU.prototype.read16 = function (addr) {
    return this.read(addr) | (this.read(addr + 1) << 8);
  };

  // The 6502's indirect fetch never carries into the high byte: a pointer at
  // $xxFF reads its high byte from $xx00. JMP ($10FF) depends on this.
  CPU.prototype.read16Bug = function (addr) {
    var hi = (addr & 0xFF00) | ((addr + 1) & 0xFF);
    return this.read(addr) | (this.read(hi) << 8);
  };

  CPU.prototype.push = function (v) {
    this.write(0x100 | this.sp, v);
    this.sp = (this.sp - 1) & 0xFF;
  };

  CPU.prototype.pull = function () {
    this.sp = (this.sp + 1) & 0xFF;
    return this.read(0x100 | this.sp);
  };

  CPU.prototype.getP = function (brk) {
    return this.C | (this.Z << 1) | (this.I << 2) | (this.D << 3) |
           (brk ? 0x10 : 0) | 0x20 | (this.V << 6) | (this.N << 7);
  };

  CPU.prototype.setP = function (v) {
    this.C = v & 1; this.Z = (v >> 1) & 1; this.I = (v >> 2) & 1;
    this.D = (v >> 3) & 1; this.V = (v >> 6) & 1; this.N = (v >> 7) & 1;
  };

  CPU.prototype.setZN = function (v) {
    this.Z = (v & 0xFF) === 0 ? 1 : 0;
    this.N = (v >> 7) & 1;
  };

  // ── interrupts ──────────────────────────────────────────────────────────

  CPU.prototype.nmi = function () {
    this.nmiPending = true;
    // Remember when, in real cycles: the CPU samples its interrupt inputs a
    // cycle before an instruction ends, so an NMI asserted after that point
    // waits for the *next* instruction to finish. Writing $2000 to enable NMI
    // during vblank is the case that shows it up.
    this.nmiAt = this.bus && this.bus.masterCycle !== undefined
      ? this.bus.masterCycle : -1;
  };

  CPU.prototype.nmiReady = function () {
    if (!this.nmiPending) return false;
    if (this.nmiAt < 0 || !this.bus || this.bus.masterCycle === undefined) return true;
    return (this.bus.masterCycle - this.nmiAt) >= 2;
  };

  /* IRQ is a level, not an edge: a source holds the line low until it is
   * acknowledged, so mappers and the APU set and clear their own bit. */
  CPU.prototype.setIRQ = function (source, active) {
    var bit = 1 << (source | 0);
    if (active) this.irqLine |= bit; else this.irqLine &= ~bit;
  };

  CPU.prototype.serviceNMI = function () {
    this.push((this.pc >> 8) & 0xFF);
    this.push(this.pc & 0xFF);
    this.push(this.getP(false));
    this.I = 1;
    this.pc = this.read16(0xFFFA);
    this.cycles += 7;
  };

  CPU.prototype.serviceIRQ = function () {
    this.push((this.pc >> 8) & 0xFF);
    this.push(this.pc & 0xFF);
    this.push(this.getP(false));
    this.I = 1;
    this.pc = this.read16(0xFFFE);
    this.cycles += 7;
  };

  // ── the step ────────────────────────────────────────────────────────────

  /* Runs one instruction (or one stalled DMA cycle) and returns the cycles it
   * took, which is what the bus uses to clock the PPU and APU forward. */
  CPU.prototype.step = function () {
    if (this.stall > 0) { this.stall--; this.cycles++; return 1; }

    if (this.nmiReady()) {
      this.nmiPending = false;
      this.jammed = false;
      var n = this.cycles;
      this.serviceNMI();
      return this.cycles - n;
    }
    if (this.irqLine && !this.I) {
      this.jammed = false;
      var i = this.cycles;
      this.serviceIRQ();
      return this.cycles - i;
    }
    if (this.jammed) { this.cycles += 2; return 2; }

    var start = this.cycles;
    var opcode = this.read(this.pc);
    this.pc = (this.pc + 1) & 0xFFFF;

    var op = OPS[opcode];
    var mode = op[1];
    this.cycles += op[2];

    var addr = 0, crossed = false;
    switch (mode) {
      case IMP:
      case ACC:
        break;
      case IMM:
        addr = this.pc; this.pc = (this.pc + 1) & 0xFFFF;
        break;
      case ZP:
        addr = this.read(this.pc); this.pc = (this.pc + 1) & 0xFFFF;
        break;
      case ZPX:
        addr = (this.read(this.pc) + this.x) & 0xFF;
        this.pc = (this.pc + 1) & 0xFFFF;
        break;
      case ZPY:
        addr = (this.read(this.pc) + this.y) & 0xFF;
        this.pc = (this.pc + 1) & 0xFFFF;
        break;
      case REL:
        var off = this.read(this.pc); this.pc = (this.pc + 1) & 0xFFFF;
        if (off & 0x80) off -= 0x100;
        addr = (this.pc + off) & 0xFFFF;
        break;
      case ABS:
        addr = this.read16(this.pc); this.pc = (this.pc + 2) & 0xFFFF;
        break;
      case ABX:
        var ba = this.read16(this.pc); this.pc = (this.pc + 2) & 0xFFFF;
        addr = (ba + this.x) & 0xFFFF;
        crossed = (ba & 0xFF00) !== (addr & 0xFF00);
        break;
      case ABY:
        var by = this.read16(this.pc); this.pc = (this.pc + 2) & 0xFFFF;
        addr = (by + this.y) & 0xFFFF;
        crossed = (by & 0xFF00) !== (addr & 0xFF00);
        break;
      case IND:
        addr = this.read16Bug(this.read16(this.pc));
        this.pc = (this.pc + 2) & 0xFFFF;
        break;
      case IZX:
        var zx = (this.read(this.pc) + this.x) & 0xFF;
        this.pc = (this.pc + 1) & 0xFFFF;
        addr = this.read(zx) | (this.read((zx + 1) & 0xFF) << 8);
        break;
      case IZY:
        var zy = this.read(this.pc); this.pc = (this.pc + 1) & 0xFFFF;
        var base = this.read(zy) | (this.read((zy + 1) & 0xFF) << 8);
        addr = (base + this.y) & 0xFFFF;
        crossed = (base & 0xFF00) !== (addr & 0xFF00);
        break;
    }
    if (crossed && op[3]) this.cycles++;

    this.exec(op[0], mode, addr);
    return this.cycles - start;
  };

  CPU.prototype.branch = function (addr, take) {
    if (!take) return;
    this.cycles++;
    if ((this.pc & 0xFF00) !== (addr & 0xFF00)) this.cycles++;
    this.pc = addr;
  };

  CPU.prototype.compare = function (reg, v) {
    var r = (reg - v) & 0xFF;
    this.C = reg >= v ? 1 : 0;
    this.setZN(r);
  };

  CPU.prototype.adc = function (v) {
    var sum = this.a + v + this.C;
    this.C = sum > 0xFF ? 1 : 0;
    // Overflow: the operands agreed on sign and the result disagrees.
    this.V = (~(this.a ^ v) & (this.a ^ sum) & 0x80) ? 1 : 0;
    this.a = sum & 0xFF;
    this.setZN(this.a);
  };

  /* SHY/SHX/SHA/TAS store their register ANDed with the high byte of the
   * address plus one. The oddity is what happens when the index carries into
   * that high byte: the AND corrupts the address as well as the value, so the
   * write lands somewhere else entirely. blargg's abs_xy test checks for it. */
  CPU.prototype.unstableStore = function (mode, addr, reg) {
    var index = (mode === ABX) ? this.x : this.y;
    var base = (addr - index) & 0xFFFF;
    var value = reg & (((base >> 8) + 1) & 0xFF);
    if ((base & 0xFF00) !== (addr & 0xFF00)) {
      addr = (value << 8) | (addr & 0xFF);
    }
    this.write(addr, value);
  };

  CPU.prototype.exec = function (name, mode, addr) {
    var v, r;
    switch (name) {
      // ── load / store ──
      case 'LDA': this.a = this.read(addr); this.setZN(this.a); break;
      case 'LDX': this.x = this.read(addr); this.setZN(this.x); break;
      case 'LDY': this.y = this.read(addr); this.setZN(this.y); break;
      case 'STA': this.write(addr, this.a); break;
      case 'STX': this.write(addr, this.x); break;
      case 'STY': this.write(addr, this.y); break;

      // ── transfers ──
      case 'TAX': this.x = this.a; this.setZN(this.x); break;
      case 'TAY': this.y = this.a; this.setZN(this.y); break;
      case 'TXA': this.a = this.x; this.setZN(this.a); break;
      case 'TYA': this.a = this.y; this.setZN(this.a); break;
      case 'TSX': this.x = this.sp; this.setZN(this.x); break;
      case 'TXS': this.sp = this.x; break;

      // ── stack ──
      case 'PHA': this.push(this.a); break;
      case 'PHP': this.push(this.getP(true)); break;
      case 'PLA': this.a = this.pull(); this.setZN(this.a); break;
      case 'PLP': this.setP(this.pull()); break;

      // ── arithmetic ──
      case 'ADC': this.adc(this.read(addr)); break;
      case 'SBC': this.adc(this.read(addr) ^ 0xFF); break;
      case 'CMP': this.compare(this.a, this.read(addr)); break;
      case 'CPX': this.compare(this.x, this.read(addr)); break;
      case 'CPY': this.compare(this.y, this.read(addr)); break;
      case 'INC':
        v = (this.read(addr) + 1) & 0xFF; this.write(addr, v); this.setZN(v);
        break;
      case 'DEC':
        v = (this.read(addr) - 1) & 0xFF; this.write(addr, v); this.setZN(v);
        break;
      case 'INX': this.x = (this.x + 1) & 0xFF; this.setZN(this.x); break;
      case 'INY': this.y = (this.y + 1) & 0xFF; this.setZN(this.y); break;
      case 'DEX': this.x = (this.x - 1) & 0xFF; this.setZN(this.x); break;
      case 'DEY': this.y = (this.y - 1) & 0xFF; this.setZN(this.y); break;

      // ── logic ──
      case 'AND': this.a &= this.read(addr); this.setZN(this.a); break;
      case 'ORA': this.a |= this.read(addr); this.setZN(this.a); break;
      case 'EOR': this.a ^= this.read(addr); this.setZN(this.a); break;
      case 'BIT':
        v = this.read(addr);
        this.Z = (this.a & v) === 0 ? 1 : 0;
        this.V = (v >> 6) & 1;
        this.N = (v >> 7) & 1;
        break;

      // ── shifts ──
      case 'ASL':
        if (mode === ACC) {
          this.C = (this.a >> 7) & 1;
          this.a = (this.a << 1) & 0xFF;
          this.setZN(this.a);
        } else {
          v = this.read(addr);
          this.C = (v >> 7) & 1;
          v = (v << 1) & 0xFF;
          this.write(addr, v); this.setZN(v);
        }
        break;
      case 'LSR':
        if (mode === ACC) {
          this.C = this.a & 1;
          this.a >>= 1;
          this.setZN(this.a);
        } else {
          v = this.read(addr);
          this.C = v & 1;
          v >>= 1;
          this.write(addr, v); this.setZN(v);
        }
        break;
      case 'ROL':
        if (mode === ACC) {
          r = ((this.a << 1) | this.C) & 0xFF;
          this.C = (this.a >> 7) & 1;
          this.a = r; this.setZN(r);
        } else {
          v = this.read(addr);
          r = ((v << 1) | this.C) & 0xFF;
          this.C = (v >> 7) & 1;
          this.write(addr, r); this.setZN(r);
        }
        break;
      case 'ROR':
        if (mode === ACC) {
          r = ((this.a >> 1) | (this.C << 7)) & 0xFF;
          this.C = this.a & 1;
          this.a = r; this.setZN(r);
        } else {
          v = this.read(addr);
          r = ((v >> 1) | (this.C << 7)) & 0xFF;
          this.C = v & 1;
          this.write(addr, r); this.setZN(r);
        }
        break;

      // ── flags ──
      case 'CLC': this.C = 0; break;
      case 'SEC': this.C = 1; break;
      case 'CLI': this.I = 0; break;
      case 'SEI': this.I = 1; break;
      case 'CLV': this.V = 0; break;
      case 'CLD': this.D = 0; break;
      case 'SED': this.D = 1; break;

      // ── branches ──
      case 'BCC': this.branch(addr, !this.C); break;
      case 'BCS': this.branch(addr, !!this.C); break;
      case 'BNE': this.branch(addr, !this.Z); break;
      case 'BEQ': this.branch(addr, !!this.Z); break;
      case 'BPL': this.branch(addr, !this.N); break;
      case 'BMI': this.branch(addr, !!this.N); break;
      case 'BVC': this.branch(addr, !this.V); break;
      case 'BVS': this.branch(addr, !!this.V); break;

      // ── jumps ──
      case 'JMP': this.pc = addr; break;
      case 'JSR':
        // The pushed address is the last byte of JSR, not the next opcode.
        r = (this.pc - 1) & 0xFFFF;
        this.push((r >> 8) & 0xFF);
        this.push(r & 0xFF);
        this.pc = addr;
        break;
      case 'RTS':
        this.pc = ((this.pull() | (this.pull() << 8)) + 1) & 0xFFFF;
        break;
      case 'RTI':
        this.setP(this.pull());
        this.pc = this.pull() | (this.pull() << 8);
        break;
      case 'BRK':
        r = (this.pc + 1) & 0xFFFF;
        this.push((r >> 8) & 0xFF);
        this.push(r & 0xFF);
        this.push(this.getP(true));
        this.I = 1;
        this.pc = this.read16(0xFFFE);
        break;

      case 'NOP': break;

      // ── unofficial ──
      // Most are a documented op paired with a read-modify-write on the same
      // address, which is exactly how they fall out of the decode ROM.
      case 'LAX':
        this.a = this.x = this.read(addr); this.setZN(this.a);
        break;
      case 'SAX':
        this.write(addr, this.a & this.x);
        break;
      case 'DCP':
        v = (this.read(addr) - 1) & 0xFF;
        this.write(addr, v);
        this.compare(this.a, v);
        break;
      case 'ISB':
        v = (this.read(addr) + 1) & 0xFF;
        this.write(addr, v);
        this.adc(v ^ 0xFF);
        break;
      case 'SLO':
        v = this.read(addr);
        this.C = (v >> 7) & 1;
        v = (v << 1) & 0xFF;
        this.write(addr, v);
        this.a |= v; this.setZN(this.a);
        break;
      case 'RLA':
        v = this.read(addr);
        r = ((v << 1) | this.C) & 0xFF;
        this.C = (v >> 7) & 1;
        this.write(addr, r);
        this.a &= r; this.setZN(this.a);
        break;
      case 'SRE':
        v = this.read(addr);
        this.C = v & 1;
        v >>= 1;
        this.write(addr, v);
        this.a ^= v; this.setZN(this.a);
        break;
      case 'RRA':
        v = this.read(addr);
        r = ((v >> 1) | (this.C << 7)) & 0xFF;
        this.C = v & 1;
        this.write(addr, r);
        this.adc(r);
        break;
      case 'ANC':
        this.a &= this.read(addr);
        this.setZN(this.a);
        this.C = (this.a >> 7) & 1;
        break;
      case 'ALR':
        this.a &= this.read(addr);
        this.C = this.a & 1;
        this.a >>= 1;
        this.setZN(this.a);
        break;
      case 'ARR':
        this.a = (this.a & this.read(addr));
        this.a = ((this.a >> 1) | (this.C << 7)) & 0xFF;
        this.setZN(this.a);
        this.C = (this.a >> 6) & 1;
        this.V = (((this.a >> 6) ^ (this.a >> 5)) & 1);
        break;
      case 'AXS':
        v = this.read(addr);
        r = (this.a & this.x) - v;
        this.C = r >= 0 ? 1 : 0;
        this.x = r & 0xFF;
        this.setZN(this.x);
        break;
      case 'LAS':
        v = this.read(addr) & this.sp;
        this.a = this.x = this.sp = v;
        this.setZN(v);
        break;
      case 'AHX': this.unstableStore(mode, addr, this.a & this.x); break;
      case 'SHX': this.unstableStore(mode, addr, this.x); break;
      case 'SHY': this.unstableStore(mode, addr, this.y); break;
      case 'TAS':
        this.sp = this.a & this.x;
        this.unstableStore(mode, addr, this.sp);
        break;
      case 'XAA':
        this.a = this.x & this.read(addr);
        this.setZN(this.a);
        break;
      case 'KIL':
        // A real 6502 hangs until reset. Back the PC up so a trace shows where.
        this.pc = (this.pc - 1) & 0xFFFF;
        this.jammed = true;
        break;
    }
  };

  // ── debugging ───────────────────────────────────────────────────────────

  function hex(v, n) {
    var s = (v >>> 0).toString(16).toUpperCase();
    while (s.length < n) s = '0' + s;
    return s;
  }

  /* Disassemble one instruction without touching bus state that matters —
   * reads go through a peek so a debugger view can't clear $2002 by accident. */
  CPU.prototype.disasm = function (pc, peek) {
    peek = peek || function (a) { return this.bus.read(a); }.bind(this);
    var opcode = peek(pc) & 0xFF;
    var op = OPS[opcode];
    var len = MODE_LEN[op[1]];
    var lo = len > 0 ? peek(pc + 1) : 0;
    var hi = len > 1 ? peek(pc + 2) : 0;
    var word = lo | (hi << 8);
    var text;
    switch (op[1]) {
      case IMP: text = ''; break;
      case ACC: text = 'A'; break;
      case IMM: text = '#$' + hex(lo, 2); break;
      case ZP:  text = '$' + hex(lo, 2); break;
      case ZPX: text = '$' + hex(lo, 2) + ',X'; break;
      case ZPY: text = '$' + hex(lo, 2) + ',Y'; break;
      case REL: text = '$' + hex((pc + 2 + (lo & 0x80 ? lo - 256 : lo)) & 0xFFFF, 4); break;
      case ABS: text = '$' + hex(word, 4); break;
      case ABX: text = '$' + hex(word, 4) + ',X'; break;
      case ABY: text = '$' + hex(word, 4) + ',Y'; break;
      case IND: text = '($' + hex(word, 4) + ')'; break;
      case IZX: text = '($' + hex(lo, 2) + ',X)'; break;
      case IZY: text = '($' + hex(lo, 2) + '),Y'; break;
    }
    var bytes = [hex(opcode, 2)];
    if (len > 0) bytes.push(hex(lo, 2));
    if (len > 1) bytes.push(hex(hi, 2));
    return {
      pc: pc, len: 1 + len, bytes: bytes.join(' '),
      text: (op[0] + ' ' + text).trim()
    };
  };

  /* One line in nestest's format, which is what the CPU test diffs against. */
  CPU.prototype.trace = function (peek) {
    var d = this.disasm(this.pc, peek);
    var line = hex(this.pc, 4) + '  ' + pad(d.bytes, 9) + pad(d.text, 32);
    return line + 'A:' + hex(this.a, 2) + ' X:' + hex(this.x, 2) +
      ' Y:' + hex(this.y, 2) + ' P:' + hex(this.getP(false), 2) +
      ' SP:' + hex(this.sp, 2) + ' CYC:' + this.cycles;
  };

  function pad(s, n) {
    while (s.length < n) s += ' ';
    return s;
  }

  CPU.prototype.saveState = function () {
    return {
      a: this.a, x: this.x, y: this.y, sp: this.sp, pc: this.pc,
      p: this.getP(false), cycles: this.cycles, stall: this.stall,
      nmi: this.nmiPending, irq: this.irqLine, jammed: this.jammed
    };
  };

  CPU.prototype.loadState = function (s) {
    this.a = s.a; this.x = s.x; this.y = s.y; this.sp = s.sp; this.pc = s.pc;
    this.setP(s.p);
    this.cycles = s.cycles; this.stall = s.stall;
    this.nmiPending = s.nmi; this.irqLine = s.irq; this.jammed = !!s.jammed;
  };

  CPU.OPS = OPS;
  root.NES = root.NES || {};
  root.NES.CPU = CPU;
})(typeof globalThis !== 'undefined' ? globalThis : this);
