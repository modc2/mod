/*
 * romtest.js — run a test ROM headless and report what it says.
 *
 * blargg's test ROMs report through a fixed protocol: $6001-$6003 hold the
 * signature DE B0 61, $6000 holds $80 while the test runs and the result code
 * when it finishes, and a NUL-terminated string of human-readable output starts
 * at $6004. A status of $81 is a request for a soft reset.
 *
 * Older ROMs (the 2005 PPU set) only draw their result on screen, so this can
 * also dump the framebuffer as a PNG — which doubles as a check that the whole
 * pipeline renders, not just that the CPU agrees with itself.
 *
 *   node tests/romtest.js <rom.nes> [--frames 600] [--png out.png] [--json]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

global.window = global;
const JS = path.join(__dirname, '..', 'web', 'js');
for (const f of ['cpu.js', 'mappers.js', 'ppu.js', 'apu.js', 'nes.js']) {
  require(path.join(JS, f));
}

// ── PNG, so a failure can be looked at rather than guessed at ──────────────

function crc32(buf) {
  let c, table = crc32.table;
  if (!table) {
    table = crc32.table = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
  }
  c = -1;
  for (let i = 0; i < buf.length; i++) c = table[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function writePNG(file, rgba, w, h) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 4 + 1)] = 0;                       // no per-line filter
    Buffer.from(rgba.buffer, rgba.byteOffset + y * w * 4, w * 4)
      .copy(raw, y * (w * 4 + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6;                          // 8-bit RGBA
  fs.writeFileSync(file, Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0))
  ]));
}

// ── run ────────────────────────────────────────────────────────────────────

function run(romPath, opts = {}) {
  const frames = opts.frames || 600;
  const nes = new NES.Console();
  const info = nes.load(fs.readFileSync(romPath));

  let status = null, text = '', resets = 0;
  const read = a => nes.cpuRead(a);
  const hasSignature = () =>
    read(0x6001) === 0xDE && read(0x6002) === 0xB0 && read(0x6003) === 0x61;

  let ran = 0;
  for (let f = 0; f < frames; f++) {
    nes.runFrame();
    ran++;
    if (!hasSignature()) continue;
    const s = read(0x6000);
    if (s === 0x81 && resets < 4) {
      // The ROM is asking to be reset; hardware needs a moment first.
      for (let i = 0; i < 10; i++) nes.runFrame();
      nes.cpu.reset();
      resets++;
      continue;
    }
    if (s < 0x80) { status = s; break; }
  }

  if (hasSignature()) {
    for (let a = 0x6004; a < 0x8000; a++) {
      const c = read(a);
      if (c === 0) break;
      text += String.fromCharCode(c);
    }
  }

  if (opts.png) {
    writePNG(opts.png, new Uint8Array(nes.ppu.frame.buffer), 256, 240);
  }

  return {
    rom: path.basename(romPath),
    cart: info,
    frames: ran,
    status,
    passed: status === 0,
    text: text.trim(),
    // The 2005-era ROMs predate the $6000 protocol: they draw a result code on
    // screen and leave it in zero page, at $F0 on some and $F8 on others.
    // A code of 1 always means every test passed.
    zp: { f0: nes.ram[0xF0], f8: nes.ram[0xF8] },
    // A screen-only ROM still proves something: count the distinct colours it
    // put on screen, since a dead PPU renders one flat colour.
    colors: new Set(nes.ppu.frame).size
  };
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const rom = args.find(a => !a.startsWith('--'));
  const flag = (name, dflt) => {
    const i = args.indexOf('--' + name);
    return i >= 0 ? args[i + 1] : dflt;
  };
  if (!rom) {
    console.error('usage: node tests/romtest.js <rom.nes> [--frames N] [--png f] [--json]');
    process.exit(2);
  }
  const result = run(rom, {
    frames: parseInt(flag('frames', '600'), 10),
    png: flag('png', null)
  });
  if (args.includes('--json')) {
    console.log(JSON.stringify(result));
  } else {
    console.log(`${result.rom}  ${result.cart.board}  ${result.cart.prg}/${result.cart.chr}`);
    console.log(`  frames ${result.frames}  colors ${result.colors}  status ${result.status}`);
    if (result.text) console.log('  ' + result.text.replace(/\n/g, '\n  '));
  }
  process.exit(result.status === null || result.status === 0 ? 0 : 1);
}

module.exports = { run, writePNG };
