// Dependency-free QR decoder — the reading half of lib/qr.ts.
//
// Whitelisting someone means moving a 42-character address between two
// machines without a typo: they show the QR their wallet (or this console)
// draws, the owner points a camera at it. Decoding runs on raw pixels in the
// browser, so the frame never leaves the device — the same reason the encoder
// doesn't call an image service. Chromium's BarcodeDetector is tried first by
// the UI; this is the path for every other browser.
//
// Pipeline: binarize → locate the three finder patterns → sample the module
// grid through a perspective transform → strip the mask, deinterleave the
// blocks, Reed–Solomon correct, read the bitstream. Scope is versions 1–10
// (21×21 … 57×57) at all four EC levels, which covers every address, URI and
// invite QR in practice and mirrors what the encoder can write.

import { GF_EXP as EXP, GF_LOG as LOG, gfMul, qrMaskFn } from "./qr";

// ── GF(256) polynomial arithmetic (decoder-only helpers) ─────────────
// Coefficients run highest-degree first, as in the classic
// "Reed–Solomon for coders" formulation.

function gfPow(x: number, power: number): number {
  if (x === 0) return 0;
  const e = (((LOG[x] * power) % 255) + 255) % 255;
  return EXP[e];
}

function gfInv(x: number): number {
  return EXP[255 - LOG[x]];
}

function gfDiv(a: number, b: number): number {
  if (b === 0) throw new Error("divide by zero");
  if (a === 0) return 0;
  return EXP[(((LOG[a] - LOG[b]) % 255) + 255) % 255];
}

function polyScale(p: number[], x: number): number[] {
  return p.map((c) => gfMul(c, x));
}

function polyAdd(p: number[], q: number[]): number[] {
  const r = new Array<number>(Math.max(p.length, q.length)).fill(0);
  for (let i = 0; i < p.length; i++) r[i + r.length - p.length] = p[i];
  for (let i = 0; i < q.length; i++) r[i + r.length - q.length] ^= q[i];
  return r;
}

function polyMul(p: number[], q: number[]): number[] {
  const r = new Array<number>(p.length + q.length - 1).fill(0);
  for (let j = 0; j < q.length; j++)
    for (let i = 0; i < p.length; i++) r[i + j] ^= gfMul(p[i], q[j]);
  return r;
}

function polyEval(p: number[], x: number): number {
  let y = p[0];
  for (let i = 1; i < p.length; i++) y = gfMul(y, x) ^ p[i];
  return y;
}

// ── Reed–Solomon decode (Berlekamp–Massey + Chien + Forney) ──────────

function syndromes(msg: number[], nsym: number): number[] {
  const s = [0]; // leading 0 keeps the polynomial indices aligned below
  for (let i = 0; i < nsym; i++) s.push(polyEval(msg, gfPow(2, i)));
  return s;
}

function errorLocator(synd: number[], nsym: number): number[] | null {
  let errLoc = [1];
  let oldLoc = [1];
  const shift = synd.length - nsym;
  for (let i = 0; i < nsym; i++) {
    const k = i + shift;
    let delta = synd[k];
    for (let j = 1; j < errLoc.length; j++)
      delta ^= gfMul(errLoc[errLoc.length - 1 - j], synd[k - j]);
    oldLoc = oldLoc.concat([0]);
    if (delta !== 0) {
      if (oldLoc.length > errLoc.length) {
        const newLoc = polyScale(oldLoc, delta);
        oldLoc = polyScale(errLoc, gfInv(delta));
        errLoc = newLoc;
      }
      errLoc = polyAdd(errLoc, polyScale(oldLoc, delta));
    }
  }
  while (errLoc.length && errLoc[0] === 0) errLoc.shift();
  if ((errLoc.length - 1) * 2 > nsym) return null; // beyond correction capacity
  return errLoc;
}

function errorPositions(errLoc: number[], msgLen: number): number[] | null {
  const errs = errLoc.length - 1;
  const rev = errLoc.slice().reverse();
  const pos: number[] = [];
  for (let i = 0; i < msgLen; i++) if (polyEval(rev, gfPow(2, i)) === 0) pos.push(msgLen - 1 - i);
  return pos.length === errs ? pos : null;
}

function correctErrata(msg: number[], synd: number[], errPos: number[]): number[] {
  const coefPos = errPos.map((p) => msg.length - 1 - p);
  let errataLoc = [1];
  for (const p of coefPos) errataLoc = polyMul(errataLoc, polyAdd([1], [gfPow(2, p), 0]));

  const nsym = errataLoc.length - 1;
  const rsynd = synd.slice().reverse();
  let errEval = polyMul(rsynd, errataLoc);
  errEval = errEval.slice(errEval.length - (nsym + 1)).reverse();

  const X = coefPos.map((p) => gfPow(2, p));
  const E = new Array<number>(msg.length).fill(0);
  for (let i = 0; i < X.length; i++) {
    const xiInv = gfInv(X[i]);
    let denom = 1;
    for (let j = 0; j < X.length; j++)
      if (j !== i) denom = gfMul(denom, 1 ^ gfMul(xiInv, X[j]));
    if (denom === 0) return msg; // shouldn't happen; leave the block as-is
    let y = polyEval(errEval.slice().reverse(), xiInv);
    y = gfMul(X[i], y);
    E[errPos[i]] = gfDiv(y, denom);
  }
  return polyAdd(msg, E);
}

/** Correct a block of `data + ec` codewords in place; null if uncorrectable. */
function rsCorrect(block: number[], nsym: number): number[] | null {
  const synd = syndromes(block, nsym);
  if (synd.every((s) => s === 0)) return block;
  const loc = errorLocator(synd, nsym);
  if (!loc) return null;
  const pos = errorPositions(loc, block.length);
  if (!pos) return null;
  const fixed = correctErrata(block, synd, pos);
  return syndromes(fixed, nsym).every((s) => s === 0) ? fixed : null;
}

// ── Symbol tables (versions 1–10) ────────────────────────────────────

type EcLevel = "L" | "M" | "Q" | "H";
/** [ec codewords per block, [[block count, data codewords], …]] */
type BlockSpec = [number, [number, number][]];

const ALIGN: Record<number, number[]> = {
  1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
  6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
};

const BLOCKS: Record<number, Record<EcLevel, BlockSpec>> = {
  1:  { L: [7,  [[1, 19]]],  M: [10, [[1, 16]]],            Q: [13, [[1, 13]]],            H: [17, [[1, 9]]] },
  2:  { L: [10, [[1, 34]]],  M: [16, [[1, 28]]],            Q: [22, [[1, 22]]],            H: [28, [[1, 16]]] },
  3:  { L: [15, [[1, 55]]],  M: [26, [[1, 44]]],            Q: [18, [[2, 17]]],            H: [22, [[2, 13]]] },
  4:  { L: [20, [[1, 80]]],  M: [18, [[2, 32]]],            Q: [26, [[2, 24]]],            H: [16, [[4, 9]]] },
  5:  { L: [26, [[1, 108]]], M: [24, [[2, 43]]],            Q: [18, [[2, 15], [2, 16]]],   H: [22, [[2, 11], [2, 12]]] },
  6:  { L: [18, [[2, 68]]],  M: [16, [[4, 27]]],            Q: [24, [[4, 19]]],            H: [28, [[4, 15]]] },
  7:  { L: [20, [[2, 78]]],  M: [18, [[4, 31]]],            Q: [18, [[2, 14], [4, 15]]],   H: [26, [[4, 13], [1, 14]]] },
  8:  { L: [24, [[2, 97]]],  M: [22, [[2, 38], [2, 39]]],   Q: [22, [[4, 18], [2, 19]]],   H: [26, [[4, 14], [2, 15]]] },
  9:  { L: [30, [[2, 116]]], M: [22, [[3, 36], [2, 37]]],   Q: [20, [[4, 16], [4, 17]]],   H: [24, [[4, 12], [4, 13]]] },
  10: { L: [18, [[2, 68], [2, 69]]], M: [26, [[4, 43], [1, 44]]], Q: [24, [[6, 19], [2, 20]]], H: [28, [[6, 15], [2, 16]]] },
};

/** Format-info EC bits → level (the spec's order is not L,M,Q,H). */
const EC_BY_BITS: Record<number, EcLevel> = { 1: "L", 0: "M", 3: "Q", 2: "H" };

// ── Bit matrix helpers ───────────────────────────────────────────────

/** Which modules carry function patterns (and so no data) for a version. */
function reservedMatrix(version: number, size: number): boolean[][] {
  const res: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const mark = (r: number, c: number) => {
    if (r >= 0 && r < size && c >= 0 && c < size) res[r][c] = true;
  };
  // Finders + separators.
  for (const [row, col] of [[0, 0], [0, size - 7], [size - 7, 0]] as [number, number][])
    for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) mark(row + r, col + c);
  // Timing.
  for (let i = 0; i < size; i++) { mark(6, i); mark(i, 6); }
  // Alignment (skipping the ones overlapping finders).
  const ap = ALIGN[version] || [];
  for (const r of ap)
    for (const c of ap) {
      if ((r <= 7 && c <= 7) || (r <= 7 && c >= size - 8) || (r >= size - 8 && c <= 7)) continue;
      for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) mark(r + dr, c + dc);
    }
  // Dark module + format areas.
  mark(size - 8, 8);
  for (let i = 0; i <= 8; i++) { mark(8, i); mark(i, 8); }
  for (let i = 0; i < 8; i++) { mark(8, size - 1 - i); mark(size - 1 - i, 8); }
  // Version areas (7+).
  if (version >= 7)
    for (let i = 0; i < 6; i++)
      for (let j = 0; j < 3; j++) { mark(i, size - 11 + j); mark(size - 11 + j, i); }
  return res;
}

function hamming(a: number, b: number): number {
  let x = a ^ b;
  let n = 0;
  while (x) { n += x & 1; x >>>= 1; }
  return n;
}

/** Best-matching format code (5 data bits) for a read 15-bit sequence. */
function decodeFormat(raw: number): number | null {
  let best = -1;
  let bestDist = 4;
  for (let f = 0; f < 32; f++) {
    let bch = f << 10;
    for (let i = 4; i >= 0; i--) if ((bch >>> (i + 10)) & 1) bch ^= 0b10100110111 << i;
    const code = ((f << 10) | (bch & 0x3ff)) ^ 0b101010000010010;
    const d = hamming(code, raw);
    if (d < bestDist) { bestDist = d; best = f; }
  }
  return best < 0 ? null : best;
}

// ── Decoding a sampled module matrix ─────────────────────────────────

/** Read the two format-info copies; returns { ec, mask } or null. */
function readFormat(m: boolean[][], size: number): { ec: EcLevel; mask: number } | null {
  let vert = 0;
  let horiz = 0;
  for (let i = 0; i < 15; i++) {
    const r = i < 6 ? i : i < 8 ? i + 1 : size - 15 + i;
    if (m[r][8]) vert |= 1 << i;
    const c = i < 8 ? size - 1 - i : i < 9 ? 7 : 15 - i - 1;
    if (m[8][c]) horiz |= 1 << i;
  }
  const f = decodeFormat(vert) ?? decodeFormat(horiz);
  if (f === null) return null;
  return { ec: EC_BY_BITS[(f >> 3) & 3], mask: f & 7 };
}

/** Module matrix → the symbol's data codewords (mask stripped, RS-corrected). */
function readCodewords(m: boolean[][], version: number, ec: EcLevel, mask: number): number[] | null {
  const size = m.length;
  const res = reservedMatrix(version, size);

  // Unmask, then walk the encoder's zigzag to recover the codeword stream.
  const bits: number[] = [];
  let col = size - 1;
  let upward = true;
  while (col > 0) {
    if (col === 6) col = 5; // the vertical timing column carries no data
    for (let i = 0; i < size; i++) {
      const row = upward ? size - 1 - i : i;
      for (let dc = 0; dc < 2; dc++) {
        const cc = col - dc;
        if (res[row][cc]) continue;
        bits.push((m[row][cc] !== qrMaskFn(mask, row, cc)) ? 1 : 0);
      }
    }
    upward = !upward;
    col -= 2;
  }

  const stream: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    let byte = 0;
    for (let j = 0; j < 8; j++) byte = (byte << 1) | bits[i + j];
    stream.push(byte);
  }

  // Deinterleave into blocks (the inverse of the encoder's interleave).
  const spec = BLOCKS[version]?.[ec];
  if (!spec) return null;
  const [ecCount, ecb] = spec;
  const dataLens: number[] = [];
  for (const [count, dlen] of ecb) for (let i = 0; i < count; i++) dataLens.push(dlen);
  const total = dataLens.reduce((s, d) => s + d + ecCount, 0);
  if (stream.length < total) return null;

  const dataBlocks: number[][] = dataLens.map(() => []);
  const ecBlocks: number[][] = dataLens.map(() => []);
  let p = 0;
  const maxData = Math.max(...dataLens);
  for (let i = 0; i < maxData; i++)
    for (let b = 0; b < dataLens.length; b++) if (i < dataLens[b]) dataBlocks[b].push(stream[p++]);
  for (let i = 0; i < ecCount; i++)
    for (let b = 0; b < dataLens.length; b++) ecBlocks[b].push(stream[p++]);

  const out: number[] = [];
  for (let b = 0; b < dataBlocks.length; b++) {
    const fixed = rsCorrect(dataBlocks[b].concat(ecBlocks[b]), ecCount);
    if (!fixed) return null;
    out.push(...fixed.slice(0, dataLens[b]));
  }
  return out;
}

const ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";

/** Codewords → text (numeric / alphanumeric / byte segments; UTF-8 bytes). */
function readSegments(cw: number[], version: number): string | null {
  const bits: number[] = [];
  for (const b of cw) for (let i = 7; i >= 0; i--) bits.push((b >>> i) & 1);
  let p = 0;
  const take = (n: number): number => {
    if (p + n > bits.length) throw new Error("truncated");
    let v = 0;
    for (let i = 0; i < n; i++) v = (v << 1) | bits[p++];
    return v;
  };
  // Character-count widths differ by version band; 1–10 spans the first two.
  const countBits = (mode: number): number => {
    const small = version <= 9;
    if (mode === 1) return small ? 10 : 12;
    if (mode === 2) return small ? 9 : 11;
    if (mode === 4) return small ? 8 : 16;
    return small ? 8 : 10; // kanji
  };

  const bytes: number[] = [];
  let text = "";
  const flush = () => {
    if (bytes.length) {
      text += new TextDecoder("utf-8").decode(new Uint8Array(bytes));
      bytes.length = 0;
    }
  };

  try {
    for (;;) {
      if (p + 4 > bits.length) break;
      const mode = take(4);
      if (mode === 0) break; // terminator
      if (mode === 7) { take(8); continue; } // ECI — assume UTF-8/Latin-1
      const n = take(countBits(mode));
      if (mode === 4) {
        for (let i = 0; i < n; i++) bytes.push(take(8));
      } else if (mode === 2) {
        flush();
        for (let i = 0; i + 1 < n; i += 2) {
          const v = take(11);
          text += ALNUM[Math.floor(v / 45)] + ALNUM[v % 45];
        }
        if (n % 2) text += ALNUM[take(6)];
      } else if (mode === 1) {
        flush();
        let i = 0;
        for (; i + 3 <= n; i += 3) text += String(take(10)).padStart(3, "0");
        if (n - i === 2) text += String(take(7)).padStart(2, "0");
        else if (n - i === 1) text += String(take(4));
      } else {
        return null; // kanji / structured-append — not something we mint or read
      }
    }
  } catch {
    /* ran off the end — return whatever decoded cleanly */
  }
  flush();
  return text || null;
}

/** Decode an upright, already-sampled module matrix (true = dark). */
export function decodeMatrix(m: boolean[][]): string | null {
  const size = m.length;
  if (size < 21 || (size - 17) % 4 !== 0) return null;
  const version = (size - 17) / 4;
  if (!BLOCKS[version]) return null;
  const fmt = readFormat(m, size);
  if (!fmt) return null;
  const cw = readCodewords(m, version, fmt.ec, fmt.mask);
  if (!cw) return null;
  return readSegments(cw, version);
}

// ── Image → bits ─────────────────────────────────────────────────────

interface BitImage {
  bits: Uint8Array; // 1 = dark
  width: number;
  height: number;
}

const dark = (img: BitImage, x: number, y: number): boolean =>
  x >= 0 && y >= 0 && x < img.width && y < img.height && img.bits[y * img.width + x] === 1;

function luminance(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): Uint8Array {
  const lum = new Uint8Array(w * h);
  for (let i = 0, p = 0; i < lum.length; i++, p += 4) {
    // Same integer weights ZXing uses; alpha is ignored (camera frames are opaque).
    lum[i] = (rgba[p] * 77 + rgba[p + 1] * 150 + rgba[p + 2] * 29) >> 8;
  }
  return lum;
}

/**
 * Local (block-adaptive) threshold — a global one loses the code under the
 * uneven lighting of a phone photographing a screen.
 */
function binarize(lum: Uint8Array, w: number, h: number): Uint8Array {
  const bits = new Uint8Array(w * h);
  if (w < 40 || h < 40) {
    // Too small for blocks: one global threshold from the mid-range.
    let min = 255;
    let max = 0;
    for (const v of lum) { if (v < min) min = v; if (v > max) max = v; }
    const t = (min + max) >> 1;
    for (let i = 0; i < lum.length; i++) bits[i] = lum[i] <= t ? 1 : 0;
    return bits;
  }
  const B = 8;
  const subW = Math.max(1, (w >> 3) + ((w & 7) ? 1 : 0));
  const subH = Math.max(1, (h >> 3) + ((h & 7) ? 1 : 0));
  const black = new Int32Array(subW * subH);
  for (let by = 0; by < subH; by++) {
    const yo = Math.min(by << 3, h - B);
    for (let bx = 0; bx < subW; bx++) {
      const xo = Math.min(bx << 3, w - B);
      let sum = 0;
      let min = 255;
      let max = 0;
      for (let y = 0; y < B; y++) {
        const row = (yo + y) * w + xo;
        for (let x = 0; x < B; x++) {
          const v = lum[row + x];
          sum += v;
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      let avg = sum >> 6;
      if (max - min <= 24) {
        // Near-uniform block: no edge to split, so assume it is background
        // and take half its brightness — using `min` here turns a flat white
        // area entirely black. Neighbours override when they disagree, which
        // is what recovers a block that really is all dark.
        avg = max >> 1;
        if (by > 0 && bx > 0) {
          const n =
            (black[(by - 1) * subW + bx] +
              2 * black[by * subW + bx - 1] +
              black[(by - 1) * subW + bx - 1]) >> 2;
          if (min < n) avg = n;
        }
      }
      black[by * subW + bx] = avg;
    }
  }
  for (let by = 0; by < subH; by++) {
    const yo = Math.min(by << 3, h - B);
    for (let bx = 0; bx < subW; bx++) {
      const xo = Math.min(bx << 3, w - B);
      const l = Math.max(0, Math.min(subW - 3, bx - 2));
      const t = Math.max(0, Math.min(subH - 3, by - 2));
      let sum = 0;
      let n = 0;
      for (let dy = 0; dy < 5 && t + dy < subH; dy++)
        for (let dx = 0; dx < 5 && l + dx < subW; dx++) { sum += black[(t + dy) * subW + l + dx]; n++; }
      const thr = sum / n;
      for (let y = 0; y < B; y++) {
        const row = (yo + y) * w;
        for (let x = 0; x < B; x++) bits[row + xo + x] = lum[row + xo + x] <= thr ? 1 : 0;
      }
    }
  }
  return bits;
}

/**
 * Otsu's global threshold — the fallback for flat, evenly lit images.
 *
 * A screenshot of a QR is the awkward case for the block pass above: at whole
 * pixels-per-module every 8×8 block lands inside a single module, so no block
 * contains an edge and the local rule has nothing to measure. One global
 * split handles those, and costs a histogram.
 */
function binarizeGlobal(lum: Uint8Array, w: number, h: number): Uint8Array {
  const hist = new Int32Array(256);
  for (const v of lum) hist[v]++;
  const total = w * h;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0;
  let wB = 0;
  let best = 0;
  let bestVar = -1;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (wB === 0) continue;
    const wF = total - wB;
    if (wF === 0) break;
    sumB += t * hist[t];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > bestVar) { bestVar = between; best = t; }
  }
  const bits = new Uint8Array(lum.length);
  for (let i = 0; i < lum.length; i++) bits[i] = lum[i] <= best ? 1 : 0;
  return bits;
}

// ── Finder patterns ──────────────────────────────────────────────────

interface Finder { x: number; y: number; size: number; count: number }

/** Does a 5-run sequence match the finder's 1:1:3:1:1 ratio? */
function isFinderRatio(st: number[]): boolean {
  let total = 0;
  for (const c of st) { if (c === 0) return false; total += c; }
  if (total < 7) return false;
  const mod = total / 7;
  const v = mod / 2;
  return (
    Math.abs(mod - st[0]) < v &&
    Math.abs(mod - st[1]) < v &&
    Math.abs(3 * mod - st[2]) < 3 * v &&
    Math.abs(mod - st[3]) < v &&
    Math.abs(mod - st[4]) < v
  );
}

const centerFromEnd = (st: number[], end: number): number => end - st[4] - st[3] - st[2] / 2;

function crossCheckVertical(img: BitImage, cx: number, cy: number, maxCount: number, originalTotal: number): number | null {
  const st = [0, 0, 0, 0, 0];
  let y = cy;
  while (y >= 0 && dark(img, cx, y)) { st[2]++; y--; }
  if (y < 0) return null;
  while (y >= 0 && !dark(img, cx, y) && st[1] <= maxCount) { st[1]++; y--; }
  if (y < 0 || st[1] > maxCount) return null;
  while (y >= 0 && dark(img, cx, y) && st[0] <= maxCount) { st[0]++; y--; }
  if (st[0] > maxCount) return null;
  y = cy + 1;
  while (y < img.height && dark(img, cx, y)) { st[2]++; y++; }
  if (y === img.height) return null;
  while (y < img.height && !dark(img, cx, y) && st[3] < maxCount) { st[3]++; y++; }
  if (y === img.height || st[3] >= maxCount) return null;
  while (y < img.height && dark(img, cx, y) && st[4] < maxCount) { st[4]++; y++; }
  if (st[4] >= maxCount) return null;
  const total = st[0] + st[1] + st[2] + st[3] + st[4];
  if (5 * Math.abs(total - originalTotal) >= 2 * originalTotal) return null;
  return isFinderRatio(st) ? centerFromEnd(st, y) : null;
}

function crossCheckHorizontal(img: BitImage, cx: number, cy: number, maxCount: number, originalTotal: number): number | null {
  const st = [0, 0, 0, 0, 0];
  let x = cx;
  while (x >= 0 && dark(img, x, cy)) { st[2]++; x--; }
  if (x < 0) return null;
  while (x >= 0 && !dark(img, x, cy) && st[1] <= maxCount) { st[1]++; x--; }
  if (x < 0 || st[1] > maxCount) return null;
  while (x >= 0 && dark(img, x, cy) && st[0] <= maxCount) { st[0]++; x--; }
  if (st[0] > maxCount) return null;
  x = cx + 1;
  while (x < img.width && dark(img, x, cy)) { st[2]++; x++; }
  if (x === img.width) return null;
  while (x < img.width && !dark(img, x, cy) && st[3] < maxCount) { st[3]++; x++; }
  if (x === img.width || st[3] >= maxCount) return null;
  while (x < img.width && dark(img, x, cy) && st[4] < maxCount) { st[4]++; x++; }
  if (st[4] >= maxCount) return null;
  const total = st[0] + st[1] + st[2] + st[3] + st[4];
  if (5 * Math.abs(total - originalTotal) >= originalTotal) return null;
  return isFinderRatio(st) ? centerFromEnd(st, x) : null;
}

/** Scan rows for 1:1:3:1:1 runs, cross-check them, and merge duplicates. */
function findFinders(img: BitImage): Finder[] {
  const found: Finder[] = [];
  const add = (st: number[], row: number, endX: number) => {
    const total = st[0] + st[1] + st[2] + st[3] + st[4];
    let cx = centerFromEnd(st, endX);
    const cyv = crossCheckVertical(img, Math.round(cx), row, st[2], total);
    if (cyv === null) return;
    const cxh = crossCheckHorizontal(img, Math.round(cx), Math.round(cyv), st[2], total);
    if (cxh === null) return;
    cx = cxh;
    const size = total / 7;
    for (const f of found) {
      if (Math.abs(f.x - cx) <= f.size && Math.abs(f.y - cyv) <= f.size) {
        const n = f.count + 1;
        f.x = (f.count * f.x + cx) / n;
        f.y = (f.count * f.y + cyv) / n;
        f.size = (f.count * f.size + size) / n;
        f.count = n;
        return;
      }
    }
    found.push({ x: cx, y: cyv, size, count: 1 });
  };

  const skip = Math.max(1, Math.floor((3 * img.height) / (4 * 97)));
  for (let y = skip - 1; y < img.height; y += skip) {
    const st = [0, 0, 0, 0, 0];
    let state = 0;
    for (let x = 0; x < img.width; x++) {
      if (dark(img, x, y)) {
        if ((state & 1) === 1) state++;
        st[state]++;
      } else if ((state & 1) === 0) {
        if (state === 4) {
          if (isFinderRatio(st)) add(st, y, x);
          else {
            st[0] = st[2]; st[1] = st[3]; st[2] = st[4]; st[3] = 1; st[4] = 0;
            state = 3;
            continue;
          }
          st[0] = 0; st[1] = 0; st[2] = 0; st[3] = 0; st[4] = 0;
          state = 0;
        } else {
          state++;
          st[state]++;
        }
      } else {
        st[state]++;
      }
    }
    if (state === 4 && isFinderRatio(st)) add(st, y, img.width);
  }
  return found;
}

/** Pick the triple that best forms a QR's corner: two equal perpendicular legs. */
function pickCorners(fs: Finder[]): { tl: Finder; tr: Finder; bl: Finder } | null {
  if (fs.length < 3) return null;
  const cand = fs.slice().sort((a, b) => b.count - a.count).slice(0, 8);
  let best: { tl: Finder; tr: Finder; bl: Finder } | null = null;
  let bestScore = Infinity;
  const d = (a: Finder, b: Finder) => Math.hypot(a.x - b.x, a.y - b.y);
  for (let i = 0; i < cand.length; i++)
    for (let j = 0; j < cand.length; j++)
      for (let k = j + 1; k < cand.length; k++) {
        if (i === j || i === k) continue;
        const tl = cand[i];
        let a = cand[j];
        let b = cand[k];
        const l1 = d(tl, a);
        const l2 = d(tl, b);
        if (l1 < 10 || l2 < 10) continue;
        const legs = Math.abs(l1 - l2) / Math.max(l1, l2);
        if (legs > 0.2) continue;
        // Perpendicular legs at the top-left corner.
        const cos =
          ((a.x - tl.x) * (b.x - tl.x) + (a.y - tl.y) * (b.y - tl.y)) / (l1 * l2);
        if (Math.abs(cos) > 0.25) continue;
        const sizes = [tl.size, a.size, b.size];
        const spread = (Math.max(...sizes) - Math.min(...sizes)) / Math.min(...sizes);
        if (spread > 0.5) continue;
        // Orient: with y growing downward, a positive cross product means the
        // first leg is the bottom-left one.
        const cross = (b.x - tl.x) * (a.y - tl.y) - (b.y - tl.y) * (a.x - tl.x);
        if (cross < 0) { const t = a; a = b; b = t; }
        const score = legs + Math.abs(cos) + spread * 0.5;
        if (score < bestScore) { bestScore = score; best = { tl, bl: a, tr: b }; }
      }
  return best;
}

// ── Perspective sampling ─────────────────────────────────────────────

interface PT { a11: number; a21: number; a31: number; a12: number; a22: number; a32: number; a13: number; a23: number; a33: number }

function squareToQuad(x0: number, y0: number, x1: number, y1: number, x2: number, y2: number, x3: number, y3: number): PT {
  const dx3 = x0 - x1 + x2 - x3;
  const dy3 = y0 - y1 + y2 - y3;
  if (dx3 === 0 && dy3 === 0) {
    return { a11: x1 - x0, a21: x2 - x1, a31: x0, a12: y1 - y0, a22: y2 - y1, a32: y0, a13: 0, a23: 0, a33: 1 };
  }
  const dx1 = x1 - x2;
  const dx2 = x3 - x2;
  const dy1 = y1 - y2;
  const dy2 = y3 - y2;
  const denom = dx1 * dy2 - dx2 * dy1;
  const a13 = (dx3 * dy2 - dx2 * dy3) / denom;
  const a23 = (dx1 * dy3 - dx3 * dy1) / denom;
  return {
    a11: x1 - x0 + a13 * x1, a21: x3 - x0 + a23 * x3, a31: x0,
    a12: y1 - y0 + a13 * y1, a22: y3 - y0 + a23 * y3, a32: y0,
    a13, a23, a33: 1,
  };
}

function adjoint(t: PT): PT {
  return {
    a11: t.a22 * t.a33 - t.a23 * t.a32,
    a21: t.a23 * t.a31 - t.a21 * t.a33,
    a31: t.a21 * t.a32 - t.a22 * t.a31,
    a12: t.a13 * t.a32 - t.a12 * t.a33,
    a22: t.a11 * t.a33 - t.a13 * t.a31,
    a32: t.a12 * t.a31 - t.a11 * t.a32,
    a13: t.a12 * t.a23 - t.a13 * t.a22,
    a23: t.a13 * t.a21 - t.a11 * t.a23,
    a33: t.a11 * t.a22 - t.a12 * t.a21,
  };
}

function times(t: PT, o: PT): PT {
  return {
    a11: t.a11 * o.a11 + t.a21 * o.a12 + t.a31 * o.a13,
    a21: t.a11 * o.a21 + t.a21 * o.a22 + t.a31 * o.a23,
    a31: t.a11 * o.a31 + t.a21 * o.a32 + t.a31 * o.a33,
    a12: t.a12 * o.a11 + t.a22 * o.a12 + t.a32 * o.a13,
    a22: t.a12 * o.a21 + t.a22 * o.a22 + t.a32 * o.a23,
    a32: t.a12 * o.a31 + t.a22 * o.a32 + t.a32 * o.a33,
    a13: t.a13 * o.a11 + t.a23 * o.a12 + t.a33 * o.a13,
    a23: t.a13 * o.a21 + t.a23 * o.a22 + t.a33 * o.a23,
    a33: t.a13 * o.a31 + t.a23 * o.a32 + t.a33 * o.a33,
  };
}

const applyPT = (t: PT, x: number, y: number): [number, number] => {
  const den = t.a13 * x + t.a23 * y + t.a33;
  return [(t.a11 * x + t.a21 * y + t.a31) / den, (t.a12 * x + t.a22 * y + t.a32) / den];
};

/**
 * The bottom-right alignment pattern, searched around an estimated centre.
 *
 * A run-length scan for its 1:1:1 ratio picks up plenty of look-alike data
 * modules, and a centre that is two modules off ruins the whole sample grid —
 * so candidates are scored against the full 5×5 template (dark centre, light
 * ring, dark ring) and only a near-perfect match counts.
 */
function findAlignment(
  img: BitImage,
  estX: number,
  estY: number,
  axis: { ux: number; uy: number; vx: number; vy: number },
  allowance: number,
): { x: number; y: number } | null {
  const x0 = Math.max(2, Math.floor(estX - allowance));
  const x1 = Math.min(img.width - 3, Math.ceil(estX + allowance));
  const y0 = Math.max(2, Math.floor(estY - allowance));
  const y1 = Math.min(img.height - 3, Math.ceil(estY + allowance));
  if (x1 <= x0 || y1 <= y0) return null;

  let best: { x: number; y: number; score: number; d: number } | null = null;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      if (!dark(img, x, y)) continue; // centre module must be dark
      let score = 0;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const want = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
          // Step along the symbol's own axes — a rotated code's ring is not
          // axis-aligned in the image.
          const sx = Math.round(x + dc * axis.ux + dr * axis.vx);
          const sy = Math.round(y + dc * axis.uy + dr * axis.vy);
          if (dark(img, sx, sy) === want) score++;
        }
      }
      if (score < 24) continue;
      const d = Math.hypot(x - estX, y - estY);
      if (!best || score > best.score || (score === best.score && d < best.d))
        best = { x, y, score, d };
    }
  }
  return best ? { x: best.x, y: best.y } : null;
}

/**
 * Length of the black-white-black run walked from (fx,fy) toward (tx,ty).
 * Measuring along the axis between two finder centres — rather than along an
 * image row — is what keeps the module size honest when the code is held at
 * an angle: a horizontal cut through a finder rotated 33° is 19% too long.
 */
function blackWhiteBlackRun(img: BitImage, fx: number, fy: number, tx: number, ty: number): number {
  const steep = Math.abs(ty - fy) > Math.abs(tx - fx);
  let [x0, y0, x1, y1] = steep ? [fy, fx, ty, tx] : [fx, fy, tx, ty];
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  let error = -dx / 2;
  const xstep = x0 < x1 ? 1 : -1;
  const ystep = y0 < y1 ? 1 : -1;
  let state = 0; // 0: in the centre run, 1: in the light run, 2: past it
  const xLimit = x1 + xstep;
  let x = x0;
  let y = y0;
  for (; x !== xLimit; x += xstep) {
    const rx = steep ? y : x;
    const ry = steep ? x : y;
    if ((state === 1) === dark(img, rx, ry)) {
      if (state === 2) return Math.hypot(x - x0, y - y0);
      state++;
    }
    error += dy;
    if (error > 0) {
      if (y === y1) break;
      y += ystep;
      error -= dx;
    }
  }
  return state === 2 ? Math.hypot(x1 + xstep - x0, y1 - y0) : NaN;
}

/** Module size from one finder toward another, counted in both directions. */
function moduleSizeOneWay(img: BitImage, a: Finder, b: Finder): number {
  const both = (from: Finder, to: Finder): number => {
    const fx = Math.round(from.x);
    const fy = Math.round(from.y);
    const first = blackWhiteBlackRun(img, fx, fy, Math.round(to.x), Math.round(to.y));
    // …and the same distance the other way, clamped to the image.
    let otherX = fx - (Math.round(to.x) - fx);
    let scale = 1;
    if (otherX < 0) { scale = fx / (fx - otherX); otherX = 0; }
    else if (otherX >= img.width) { scale = (img.width - 1 - fx) / (otherX - fx); otherX = img.width - 1; }
    let otherY = Math.round(fy - (to.y - fy) * scale);
    scale = 1;
    if (otherY < 0) { scale = fy / (fy - otherY); otherY = 0; }
    else if (otherY >= img.height) { scale = (img.height - 1 - fy) / (otherY - fy); otherY = img.height - 1; }
    otherX = Math.round(fx + (otherX - fx) * scale);
    const second = blackWhiteBlackRun(img, fx, fy, otherX, otherY);
    return first + second - 1; // the centre pixel got counted twice
  };
  const e1 = both(a, b);
  const e2 = both(b, a);
  if (Number.isNaN(e1) && Number.isNaN(e2)) return NaN;
  if (Number.isNaN(e1)) return e2 / 7;
  if (Number.isNaN(e2)) return e1 / 7;
  return (e1 + e2) / 14;
}

/**
 * Sample `dim`×`dim` modules out of the image through `t`.
 *
 * Each module is a majority vote over a small cross rather than a single
 * pixel — one speck of sensor noise landing on a module centre otherwise
 * flips a bit, and enough of those exhaust the error correction.
 */
function sampleGrid(img: BitImage, t: PT, dim: number, moduleSize: number): boolean[][] | null {
  const d = Math.max(1, Math.round(moduleSize / 3));
  const m: boolean[][] = [];
  for (let y = 0; y < dim; y++) {
    const row = new Array<boolean>(dim);
    for (let x = 0; x < dim; x++) {
      const [px, py] = applyPT(t, x + 0.5, y + 0.5);
      const ix = Math.round(px);
      const iy = Math.round(py);
      if (ix < 0 || iy < 0 || ix >= img.width || iy >= img.height) return null;
      let votes = dark(img, ix, iy) ? 1 : 0;
      votes += dark(img, ix - d, iy) ? 1 : 0;
      votes += dark(img, ix + d, iy) ? 1 : 0;
      votes += dark(img, ix, iy - d) ? 1 : 0;
      votes += dark(img, ix, iy + d) ? 1 : 0;
      row[x] = votes >= 3;
    }
    m.push(row);
  }
  return m;
}

function detectAndDecode(img: BitImage): string | null {
  const corners = pickCorners(findFinders(img));
  if (!corners) return null;
  const { tl, tr, bl } = corners;

  const legs = (Math.hypot(tr.x - tl.x, tr.y - tl.y) + Math.hypot(bl.x - tl.x, bl.y - tl.y)) / 2;

  // Two independent module-size estimates: the run walked along the symbol's
  // axis (exact when the image is sharp, biased small when it isn't) and the
  // finders' own row-scan width, de-skewed by the symbol's tilt. Blur can put
  // either a few percent out, which at 20+ modules is a whole version — so
  // the neighbouring dimensions are tried too before giving up.
  const across = moduleSizeOneWay(img, tl, tr);
  const down = moduleSizeOneWay(img, tl, bl);
  const runSize = Number.isNaN(across) ? down : Number.isNaN(down) ? across : (across + down) / 2;
  let tilt = Math.atan2(tr.y - tl.y, tr.x - tl.x) % (Math.PI / 2);
  if (tilt > Math.PI / 4) tilt -= Math.PI / 2;
  if (tilt < -Math.PI / 4) tilt += Math.PI / 2;
  const finderSize = ((tl.size + tr.size + bl.size) / 3) * Math.cos(tilt);

  const dims: number[] = [];
  const sizeFor = new Map<number, number>();
  const pushDim = (ms: number) => {
    if (!(ms >= 1)) return;
    let d = Math.round(legs / ms) + 7;
    switch (d & 3) {
      case 0: d++; break;
      case 2: d--; break;
      case 3: return;
    }
    if (d < 21 || d > 57 || dims.includes(d)) return;
    dims.push(d);
    sizeFor.set(d, ms);
  };
  pushDim(runSize);
  pushDim(finderSize);
  for (const d of dims.slice()) for (const nd of [d - 4, d + 4]) pushDim(legs / (nd - 7));

  for (const dim of dims) {
    const moduleSize = sizeFor.get(dim) ?? legs / (dim - 7);
    const version = (dim - 17) / 4;

    // Fourth reference point: the bottom-right alignment pattern when the
    // version has one and we can find it (it carries the lens/paper warp);
    // otherwise the parallelogram estimate of the missing corner.
    const brX = tr.x - tl.x + bl.x;
    const brY = tr.y - tl.y + bl.y;
    let srcBR = dim - 3.5;
    let dstBRX = brX;
    let dstBRY = brY;
    if (version > 1) {
      const corr = 1 - 3 / (dim - 7);
      const ex = tl.x + corr * (brX - tl.x);
      const ey = tl.y + corr * (brY - tl.y);
      const span = dim - 7; // modules between finder centres
      const axis = {
        ux: (tr.x - tl.x) / span, uy: (tr.y - tl.y) / span,
        vx: (bl.x - tl.x) / span, vy: (bl.y - tl.y) / span,
      };
      for (let i = 4; i <= 16; i <<= 1) {
        const ap = findAlignment(img, ex, ey, axis, moduleSize * i);
        if (ap) { srcBR = dim - 6.5; dstBRX = ap.x; dstBRY = ap.y; break; }
      }
    }

    const src = squareToQuad(3.5, 3.5, dim - 3.5, 3.5, srcBR, srcBR, 3.5, dim - 3.5);
    const dst = squareToQuad(tl.x, tl.y, tr.x, tr.y, dstBRX, dstBRY, bl.x, bl.y);
    const m = sampleGrid(img, times(dst, adjoint(src)), dim, moduleSize);
    const text = m && decodeMatrix(m);
    if (text) return text;
  }
  return null;
}

/**
 * Decode the first QR in an RGBA frame (a canvas `ImageData`).
 * Returns the payload text, or null when no code is readable.
 */
export function decodeQr(rgba: Uint8ClampedArray | Uint8Array, width: number, height: number): string | null {
  if (width < 21 || height < 21) return null;
  const lum = luminance(rgba, width, height);
  for (const threshold of [binarize, binarizeGlobal]) {
    const bits = threshold(lum, width, height);
    const hit = detectAndDecode({ bits, width, height });
    if (hit) return hit;
    // Light-on-dark codes (some wallets invert for dark mode) — one more pass.
    const inv = new Uint8Array(bits.length);
    for (let i = 0; i < bits.length; i++) inv[i] = bits[i] ? 0 : 1;
    const flipped = detectAndDecode({ bits: inv, width, height });
    if (flipped) return flipped;
  }
  return null;
}

/**
 * Pull an EVM address out of whatever a wallet encoded: a bare address, an
 * EIP-681 `ethereum:0x…@1/transfer?…` URI, a block-explorer link, or one of
 * this console's own share links. Returns the 0x form, or null.
 */
export function extractAddress(text: string): string | null {
  if (!text) return null;
  const m = text.match(/0x[a-fA-F0-9]{40}/);
  return m ? m[0] : null;
}
