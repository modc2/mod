// Dependency-free QR Code encoder (byte mode, versions 1–10, EC level M).
//
// We render edit-grant invites as QR codes locally instead of calling an
// external QR-image service — the grant id is a capability (it confers edit
// access), so it must never leave the owner's browser. This is a compact,
// faithful implementation of the QR spec sufficient for short invite URLs
// (~200 chars max at level M / version 10). Output is a boolean matrix
// (true = dark module); callers render it (see qrSvg).

// ── GF(256) arithmetic (primitive polynomial 0x11d) ──────────────────
const EXP = new Array<number>(512);
const LOG = new Array<number>(256);
(function initGalois() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
})();

/** Exported so the decoder (lib/qrscan.ts) works in the same field. */
export { EXP as GF_EXP, LOG as GF_LOG };

export function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a] + LOG[b]];
}

/** Reed–Solomon generator polynomial for `ec` error-correction codewords. */
function rsGenerator(ec: number): number[] {
  let g = [1];
  for (let i = 0; i < ec; i++) {
    const ng = new Array<number>(g.length + 1).fill(0);
    for (let j = 0; j < g.length; j++) {
      ng[j] ^= g[j]; // * x
      ng[j + 1] ^= gfMul(g[j], EXP[i]); // * α^i
    }
    g = ng;
  }
  return g;
}

/** EC codewords (remainder) for a data block. */
function rsEncode(data: number[], ec: number): number[] {
  const gen = rsGenerator(ec);
  const buf = data.concat(new Array<number>(ec).fill(0));
  for (let i = 0; i < data.length; i++) {
    const coef = buf[i];
    if (coef !== 0) {
      for (let j = 0; j < gen.length; j++) buf[i + j] ^= gfMul(gen[j], coef);
    }
  }
  return buf.slice(data.length);
}

// ── Per-version tables (level M only) ────────────────────────────────
// blocks: [ [count, dataCodewords], ... ]; ec = EC codewords per block.
interface VInfo {
  ec: number;
  blocks: [number, number][];
  align: number[];
  remainder: number;
}
const VERSIONS: Record<number, VInfo> = {
  1: { ec: 10, blocks: [[1, 16]], align: [], remainder: 0 },
  2: { ec: 16, blocks: [[1, 28]], align: [6, 18], remainder: 7 },
  3: { ec: 26, blocks: [[1, 44]], align: [6, 22], remainder: 7 },
  4: { ec: 18, blocks: [[2, 32]], align: [6, 26], remainder: 7 },
  5: { ec: 24, blocks: [[2, 43]], align: [6, 30], remainder: 7 },
  6: { ec: 16, blocks: [[4, 27]], align: [6, 34], remainder: 7 },
  7: { ec: 18, blocks: [[4, 31]], align: [6, 22, 38], remainder: 0 },
  8: { ec: 22, blocks: [[2, 38], [2, 39]], align: [6, 24, 42], remainder: 0 },
  9: { ec: 22, blocks: [[3, 36], [2, 37]], align: [6, 26, 46], remainder: 0 },
  10: { ec: 26, blocks: [[4, 43], [1, 44]], align: [6, 28, 50], remainder: 0 },
};

function totalDataCodewords(v: VInfo): number {
  return v.blocks.reduce((s, [n, d]) => s + n * d, 0);
}

// ── Bit buffer ───────────────────────────────────────────────────────
class BitBuffer {
  bits: number[] = [];
  put(value: number, length: number) {
    for (let i = length - 1; i >= 0; i--) this.bits.push((value >>> i) & 1);
  }
  get length() {
    return this.bits.length;
  }
}

// ── Encode bytes → interleaved codeword stream for a version ─────────
function buildCodewords(bytes: number[], version: number): number[] {
  const info = VERSIONS[version];
  const dataCount = totalDataCodewords(info);

  const bb = new BitBuffer();
  bb.put(4, 4); // byte mode
  const lenBits = version >= 10 ? 16 : 8;
  bb.put(bytes.length, lenBits);
  for (const b of bytes) bb.put(b, 8);

  // Terminator + pad to byte boundary.
  const capacityBits = dataCount * 8;
  for (let i = 0; i < 4 && bb.length < capacityBits; i++) bb.put(0, 1);
  while (bb.length % 8 !== 0) bb.put(0, 1);

  // Build data codewords, then pad bytes 0xEC / 0x11.
  const dataCw: number[] = [];
  for (let i = 0; i < bb.length; i += 8) {
    let byte = 0;
    for (let j = 0; j < 8; j++) byte = (byte << 1) | bb.bits[i + j];
    dataCw.push(byte);
  }
  // Pad bytes alternate 0xEC / 0x11, always starting with 0xEC.
  const pads = [0xec, 0x11];
  for (let p = 0; dataCw.length < dataCount; p++) dataCw.push(pads[p % 2]);

  // Split into blocks; compute EC for each.
  const dataBlocks: number[][] = [];
  const ecBlocks: number[][] = [];
  let pos = 0;
  for (const [count, dlen] of info.blocks) {
    for (let i = 0; i < count; i++) {
      const block = dataCw.slice(pos, pos + dlen);
      pos += dlen;
      dataBlocks.push(block);
      ecBlocks.push(rsEncode(block, info.ec));
    }
  }

  // Interleave data codewords, then EC codewords.
  const result: number[] = [];
  const maxData = Math.max(...dataBlocks.map((b) => b.length));
  for (let i = 0; i < maxData; i++)
    for (const block of dataBlocks) if (i < block.length) result.push(block[i]);
  const maxEc = Math.max(...ecBlocks.map((b) => b.length));
  for (let i = 0; i < maxEc; i++)
    for (const block of ecBlocks) if (i < block.length) result.push(block[i]);

  return result;
}

// ── Matrix construction ──────────────────────────────────────────────
type Cell = boolean | null; // null = not yet set (function-pattern reservation done separately)

function buildMatrix(codewords: number[], version: number): boolean[][] {
  const info = VERSIONS[version];
  const size = version * 4 + 17;
  const modules: Cell[][] = Array.from({ length: size }, () =>
    new Array<Cell>(size).fill(null),
  );
  const reserved: boolean[][] = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  );

  const setF = (r: number, c: number, v: boolean) => {
    modules[r][c] = v;
    reserved[r][c] = true;
  };

  // Finder patterns + separators at three corners.
  const placeFinder = (row: number, col: number) => {
    for (let r = -1; r <= 7; r++) {
      for (let c = -1; c <= 7; c++) {
        const rr = row + r;
        const cc = col + c;
        if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
        // r,c outside 0..6 are the light separator ring around the 7x7 finder.
        if (r < 0 || r > 6 || c < 0 || c > 6) {
          setF(rr, cc, false);
          continue;
        }
        const isBorder = r === 0 || r === 6 || c === 0 || c === 6;
        const isCore = r >= 2 && r <= 4 && c >= 2 && c <= 4;
        setF(rr, cc, isBorder || isCore);
      }
    }
  };
  placeFinder(0, 0);
  placeFinder(0, size - 7);
  placeFinder(size - 7, 0);

  // Timing patterns.
  for (let i = 8; i < size - 8; i++) {
    if (!reserved[6][i]) setF(6, i, i % 2 === 0);
    if (!reserved[i][6]) setF(i, 6, i % 2 === 0);
  }

  // Alignment patterns.
  const ap = info.align;
  for (const r of ap) {
    for (const c of ap) {
      // Skip those overlapping finder patterns.
      if ((r <= 7 && c <= 7) || (r <= 7 && c >= size - 8) || (r >= size - 8 && c <= 7)) continue;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const isRing = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
          setF(r + dr, c + dc, isRing);
        }
      }
    }
  }

  // Dark module.
  setF(size - 8, 8, true);

  // Reserve format-info areas (filled after masking).
  const reserveFormat = () => {
    for (let i = 0; i <= 8; i++) {
      if (!reserved[8][i]) reserved[8][i] = true;
      if (!reserved[i][8]) reserved[i][8] = true;
    }
    for (let i = 0; i < 8; i++) {
      reserved[8][size - 1 - i] = true;
      reserved[size - 1 - i][8] = true;
    }
  };
  reserveFormat();

  // Reserve version-info areas (version >= 7).
  if (version >= 7) {
    for (let i = 0; i < 6; i++) {
      for (let j = 0; j < 3; j++) {
        reserved[i][size - 11 + j] = true;
        reserved[size - 11 + j][i] = true;
      }
    }
  }

  // Lay data bits in upward/downward zigzag, skipping column 6 (timing).
  const bits: number[] = [];
  for (const cw of codewords) for (let i = 7; i >= 0; i--) bits.push((cw >>> i) & 1);
  for (let i = 0; i < info.remainder; i++) bits.push(0);

  let bitIdx = 0;
  let upward = true;
  let col = size - 1;
  while (col > 0) {
    if (col === 6) col = 5; // skip the vertical timing column entirely
    for (let i = 0; i < size; i++) {
      const row = upward ? size - 1 - i : i;
      for (let dc = 0; dc < 2; dc++) {
        const cc = col - dc;
        if (reserved[row][cc]) continue;
        const bit = bitIdx < bits.length ? bits[bitIdx++] : 0;
        modules[row][cc] = bit === 1;
      }
    }
    upward = !upward;
    col -= 2;
  }

  // Choose the lowest-penalty mask.
  let best = 0;
  let bestPenalty = Infinity;
  let bestMatrix: boolean[][] = [];
  for (let mask = 0; mask < 8; mask++) {
    const m = applyMask(modules, reserved, size, mask);
    placeFormat(m, reserved, size, mask);
    if (version >= 7) placeVersion(m, size, version);
    const p = penalty(m, size);
    if (p < bestPenalty) {
      bestPenalty = p;
      best = mask;
      bestMatrix = m;
    }
  }
  void best;
  return bestMatrix;
}

/** The eight data masks. Exported as `qrMaskFn` so the decoder can undo one. */
export function qrMaskFn(mask: number, r: number, c: number): boolean {
  switch (mask) {
    case 0: return (r + c) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (r + c) % 3 === 0;
    case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
    case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
    case 7: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
    default: return false;
  }
}

function applyMask(
  modules: Cell[][],
  reserved: boolean[][],
  size: number,
  mask: number,
): boolean[][] {
  const out: boolean[][] = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  );
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      let v = modules[r][c] === true;
      if (!reserved[r][c] && qrMaskFn(mask, r, c)) v = !v;
      out[r][c] = v;
    }
  }
  return out;
}

// BCH(15,5) format info, level M = 0b00.
function placeFormat(m: boolean[][], reserved: boolean[][], size: number, mask: number) {
  const data = (0b00 << 3) | mask; // 5 bits
  let bch = data << 10;
  const g = 0b10100110111;
  for (let i = 4; i >= 0; i--) {
    if ((bch >>> (i + 10)) & 1) bch ^= g << i;
  }
  const format = ((data << 10) | bch) ^ 0b101010000010010;
  const bitOf = (i: number) => ((format >>> i) & 1) === 1; // i is LSB-first

  // Placement per QR spec (matches qrcode-generator's setupTypeInfo).
  // Vertical strip (left of / below top-left finder + up the bottom-left).
  for (let i = 0; i < 15; i++) {
    const b = bitOf(i);
    if (i < 6) m[i][8] = b;
    else if (i < 8) m[i + 1][8] = b;
    else m[size - 15 + i][8] = b;
  }
  // Horizontal strip (row 8: bottom of top-left finder + left of top-right).
  for (let i = 0; i < 15; i++) {
    const b = bitOf(i);
    if (i < 8) m[8][size - 1 - i] = b;
    else if (i < 9) m[8][7] = b;
    else m[8][15 - i - 1] = b;
  }
  m[size - 8][8] = true; // dark module stays set
  void reserved;
}

// BCH(18,6) version info (version >= 7).
function placeVersion(m: boolean[][], size: number, version: number) {
  let bch = version << 12;
  const g = 0b1111100100101;
  for (let i = 5; i >= 0; i--) {
    if ((bch >>> (i + 12)) & 1) bch ^= g << i;
  }
  const vinfo = (version << 12) | bch;
  for (let i = 0; i < 18; i++) {
    const bit = ((vinfo >>> i) & 1) === 1;
    const a = Math.floor(i / 3);
    const b = (i % 3) + size - 11;
    m[a][b] = bit;
    m[b][a] = bit;
  }
}

// ── Penalty scoring (mask selection) ─────────────────────────────────
function penalty(m: boolean[][], size: number): number {
  let score = 0;
  // Rule 1: runs of 5+ same-colour modules.
  for (let r = 0; r < size; r++) {
    let runC = 1, runR = 1;
    for (let c = 1; c < size; c++) {
      if (m[r][c] === m[r][c - 1]) { runC++; if (runC === 5) score += 3; else if (runC > 5) score++; }
      else runC = 1;
      if (m[c][r] === m[c - 1][r]) { runR++; if (runR === 5) score += 3; else if (runR > 5) score++; }
      else runR = 1;
    }
  }
  // Rule 2: 2x2 blocks of same colour.
  for (let r = 0; r < size - 1; r++)
    for (let c = 0; c < size - 1; c++)
      if (m[r][c] === m[r][c + 1] && m[r][c] === m[r + 1][c] && m[r][c] === m[r + 1][c + 1])
        score += 3;
  // Rule 3: finder-like 1:1:3:1:1 patterns.
  const pat1 = [true, false, true, true, true, false, true, false, false, false, false];
  const pat2 = [false, false, false, false, true, false, true, true, true, false, true];
  const matches = (arr: boolean[], pat: boolean[], i: number) => {
    for (let k = 0; k < pat.length; k++) if (arr[i + k] !== pat[k]) return false;
    return true;
  };
  for (let r = 0; r < size; r++) {
    const row = m[r];
    const col = m.map((rr) => rr[r]);
    for (let c = 0; c <= size - 11; c++) {
      if (matches(row, pat1, c) || matches(row, pat2, c)) score += 40;
      if (matches(col, pat1, c) || matches(col, pat2, c)) score += 40;
    }
  }
  // Rule 4: dark/light balance.
  let dark = 0;
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) if (m[r][c]) dark++;
  const ratio = (dark * 100) / (size * size);
  score += Math.floor(Math.abs(ratio - 50) / 5) * 10;
  return score;
}

// ── Public API ───────────────────────────────────────────────────────
function utf8Bytes(text: string): number[] {
  return Array.from(new TextEncoder().encode(text));
}

/** Smallest level-M version that fits `byteLen` data bytes. */
function pickVersion(byteLen: number): number {
  for (let v = 1; v <= 10; v++) {
    const lenBits = v >= 10 ? 16 : 8;
    const need = 4 + lenBits + byteLen * 8;
    if (need <= totalDataCodewords(VERSIONS[v]) * 8) return v;
  }
  throw new Error("QR payload too large");
}

/** Encode `text` to a boolean module matrix (true = dark). */
export function qrMatrix(text: string): boolean[][] {
  const bytes = utf8Bytes(text);
  const version = pickVersion(bytes.length);
  const codewords = buildCodewords(bytes, version);
  return buildMatrix(codewords, version);
}

/**
 * Render `text` as a crisp SVG string. `size` is the pixel side length;
 * `margin` is the quiet-zone width in modules (4 per spec).
 */
export function qrSvg(text: string, size = 240, margin = 4, dark = "#0b0b0c", light = "#ffffff"): string {
  const m = qrMatrix(text);
  const n = m.length;
  const total = n + margin * 2;
  const rects: string[] = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (m[r][c]) rects.push(`M${c + margin} ${r + margin}h1v1h-1z`);
    }
  }
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 ${total} ${total}" shape-rendering="crispEdges">` +
    `<rect width="${total}" height="${total}" fill="${light}"/>` +
    `<path d="${rects.join("")}" fill="${dark}"/>` +
    `</svg>`
  );
}
