/*
 * cputest.js — run nestest.nes and diff every step against the gold log.
 *
 * nestest is the standard 6502 conformance ROM. Entered at $C000 it runs
 * without a PPU and exercises all 151 official opcodes and then the unofficial
 * ones, and kevtris' log records the register file and cycle count before each
 * instruction. Matching all 8991 lines means the core is right down to the
 * page-cross penalties.
 *
 *   node tests/cputest.js [nestest.nes] [nestest.log]
 *
 * The ROMs are not in the tree — they are fetched to ~/.mod/supermario/testroms
 * by `m supermario/fetch_test_roms`.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');

global.window = global;
require(path.join(__dirname, '..', 'web', 'js', 'cpu.js'));

const ROMS = path.join(os.homedir(), '.mod', 'supermario', 'testroms');
const romPath = process.argv[2] || path.join(ROMS, 'nestest.nes');
const logPath = process.argv[3] || path.join(ROMS, 'nestest.log');

for (const p of [romPath, logPath]) {
  if (!fs.existsSync(p)) {
    console.error(`missing ${p}\nrun: m supermario/fetch_test_roms`);
    process.exit(2);
  }
}

// nestest is NROM: 16K of PRG mirrored across $8000-$FFFF. No PPU is needed in
// automated mode, so unmapped reads just come back as open bus.
const rom = fs.readFileSync(romPath);
const prg = rom.subarray(16, 16 + 16384);
const ram = new Uint8Array(0x800);

const bus = {
  read(a) {
    if (a < 0x2000) return ram[a & 0x7FF];
    if (a >= 0x8000) return prg[(a - 0x8000) & 0x3FFF];
    return 0;
  },
  write(a, v) {
    if (a < 0x2000) ram[a & 0x7FF] = v;
  }
};

const cpu = new NES.CPU(bus);
cpu.pc = 0xC000;
cpu.sp = 0xFD;
cpu.setP(0x24);
cpu.cycles = 7;

const lines = fs.readFileSync(logPath, 'utf8').split('\n').filter(Boolean);
const field = (line, key) => {
  const m = line.match(new RegExp(key + ':([0-9A-F]+)'));
  return m ? parseInt(m[1], 16) : null;
};

let checked = 0;
for (let i = 0; i < lines.length; i++) {
  const want = lines[i];
  const expect = {
    pc: parseInt(want.slice(0, 4), 16),
    a: field(want, 'A'), x: field(want, 'X'), y: field(want, 'Y'),
    p: field(want, 'P'), sp: field(want, 'SP'),
    cyc: parseInt(want.match(/CYC:(\d+)/)[1], 10)
  };
  const got = {
    pc: cpu.pc, a: cpu.a, x: cpu.x, y: cpu.y,
    p: cpu.getP(false), sp: cpu.sp, cyc: cpu.cycles
  };
  for (const k of ['pc', 'a', 'x', 'y', 'p', 'sp', 'cyc']) {
    if (got[k] !== expect[k]) {
      console.error(`FAIL at log line ${i + 1}: ${k} = ${got[k]} want ${expect[k]}`);
      console.error(`  want: ${want.trim()}`);
      console.error(`  got:  ${cpu.trace(bus.read).trim()}`);
      process.exit(1);
    }
  }
  checked++;
  cpu.step();
}

// $02/$03 are nestest's result codes; both zero means every test passed.
const err = [bus.read(0x02), bus.read(0x03)];
if (err[0] !== 0 || err[1] !== 0) {
  console.error(`FAIL: nestest reported error codes ${err.map(e => e.toString(16))}`);
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, lines: checked, result: '$02/$03 clear' }));
