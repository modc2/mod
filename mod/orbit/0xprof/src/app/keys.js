// A wallet, in the tab, in one file.
//
// The server has exactly one way to learn who you are: an address recovered
// from a personal_sign signature. An extension is one way to produce one. This
// file is the other — keccak-256 and secp256k1 in BigInt, so a visitor with no
// wallet installed can hold a key of their own instead of borrowing an
// identity from somebody else's software.
//
// Nothing here talks to the network, the DOM or storage, and nothing here is
// 0xprof-specific: it takes bytes and a scalar and gives back a signature that
// eth_account recovers. Which is the point — the box cannot tell a signature
// made here from one made by MetaMask, and it should not be able to.
//
// The scalar multiplication is plain double-and-add and its timing depends on
// the key. That is a real weakness and it is stated rather than hidden: this
// is a key made in a browser tab for signing your own claims, and if you are
// holding anything of value, hold it in a wallet.

// ── keccak-256 ───────────────────────────────────────────────────────
//
// Ethereum's hash, and not SHA3 — same permutation, different padding byte.
// 64-bit lanes as BigInt: slower than the 32-bit-pair trick and short enough
// to read, which for a few hundred bytes per signature is the better trade.

const M64 = (1n << 64n) - 1n;
const rotl = (x, n) => ((x << n) | (x >> (64n - n))) & M64;

const RHO = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
             27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44];
const PI = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
            15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];
const RC = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n,
  0x000000000000808bn, 0x0000000080000001n, 0x8000000080008081n, 0x8000000000008009n,
  0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n,
  0x8000000000008002n, 0x8000000000000080n, 0x000000000000800an, 0x800000008000000an,
  0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];

function permute(lanes) {
  for (let round = 0; round < 24; round++) {
    const C = [0, 1, 2, 3, 4].map((x) =>
      lanes[x] ^ lanes[x + 5] ^ lanes[x + 10] ^ lanes[x + 15] ^ lanes[x + 20]);
    for (let x = 0; x < 5; x++) {
      const D = C[(x + 4) % 5] ^ rotl(C[(x + 1) % 5], 1n);
      for (let y = 0; y < 25; y += 5) lanes[x + y] ^= D;
    }
    let last = lanes[1];
    for (let i = 0; i < 24; i++) {
      const target = PI[i];
      const held = lanes[target];
      lanes[target] = rotl(last, BigInt(RHO[i]));
      last = held;
    }
    for (let y = 0; y < 25; y += 5) {
      const row = [lanes[y], lanes[y + 1], lanes[y + 2], lanes[y + 3], lanes[y + 4]];
      for (let x = 0; x < 5; x++) {
        lanes[y + x] = row[x] ^ ((~row[(x + 1) % 5] & M64) & row[(x + 2) % 5]);
      }
    }
    lanes[0] ^= RC[round];
  }
}

export function keccak256(input) {
  const data = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  const RATE = 136;                                   // 1600 bits − 2×256
  const padded = new Uint8Array(Math.ceil((data.length + 1) / RATE) * RATE);
  padded.set(data);
  padded[data.length] = 0x01;                         // keccak padding, not 0x06
  padded[padded.length - 1] |= 0x80;

  const lanes = new Array(25).fill(0n);
  for (let offset = 0; offset < padded.length; offset += RATE) {
    for (let lane = 0; lane < RATE / 8; lane++) {
      let value = 0n;
      for (let byte = 7; byte >= 0; byte--) {         // little-endian lanes
        value = (value << 8n) | BigInt(padded[offset + lane * 8 + byte]);
      }
      lanes[lane] ^= value;
    }
    permute(lanes);
  }

  const out = new Uint8Array(32);
  for (let lane = 0; lane < 4; lane++) {
    let value = lanes[lane];
    for (let byte = 0; byte < 8; byte++) {
      out[lane * 8 + byte] = Number(value & 0xffn);
      value >>= 8n;
    }
  }
  return out;
}

// ── secp256k1 ────────────────────────────────────────────────────────

const P = 2n ** 256n - 2n ** 32n - 977n;
export const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const G = {
  x: 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n,
  y: 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n,
};

const mod = (a, m = P) => ((a % m) + m) % m;

const inv = (a, m = P) => {
  let [oldR, r] = [mod(a, m), m];
  let [oldS, s] = [1n, 0n];
  while (r !== 0n) {
    const q = oldR / r;
    [oldR, r] = [r, oldR - q * r];
    [oldS, s] = [s, oldS - q * s];
  }
  return mod(oldS, m);
};

const add = (p1, p2) => {
  if (!p1) return p2;
  if (!p2) return p1;
  if (p1.x === p2.x && mod(p1.y + p2.y) === 0n) return null;
  const lam = p1.x === p2.x && p1.y === p2.y
    ? mod(3n * p1.x * p1.x * inv(2n * p1.y))
    : mod((p2.y - p1.y) * inv(p2.x - p1.x));
  const x = mod(lam * lam - p1.x - p2.x);
  return { x, y: mod(lam * (p1.x - x) - p1.y) };
};

const mul = (point, k) => {
  let scalar = mod(k, N);
  let result = null;
  let addend = point;
  while (scalar > 0n) {
    if (scalar & 1n) result = add(result, addend);
    addend = add(addend, addend);
    scalar >>= 1n;
  }
  return result;
};

// ── bytes ────────────────────────────────────────────────────────────

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
const be32 = (value) => {
  const out = new Uint8Array(32);
  let v = value;
  for (let i = 31; i >= 0; i--) { out[i] = Number(v & 0xffn); v >>= 8n; }
  return out;
};

function randomScalar() {
  // Rejection sampling on the platform CSPRNG. getRandomValues is available on
  // plain http as well as https, which matters: this console is served from a
  // bare port as often as from the gateway, and crypto.subtle is not.
  const source = globalThis.crypto;
  if (!source || !source.getRandomValues) {
    throw new Error('this browser has no crypto.getRandomValues — no key can be made here');
  }
  for (let tries = 0; tries < 64; tries++) {
    const raw = source.getRandomValues(new Uint8Array(32));
    const value = BigInt('0x' + hex(raw));
    if (value > 0n && value < N) return value;
  }
  throw new Error('the random source is not producing usable scalars');
}

export function scalarOf(key) {
  const text = String(key || '').trim().replace(/^0x/i, '');
  if (!/^[0-9a-fA-F]{64}$/.test(text)) {
    throw new Error('a private key is 32 bytes of hex — 64 characters, 0x optional');
  }
  const value = BigInt('0x' + text);
  if (value === 0n || value >= N) throw new Error('that scalar is outside the curve order');
  return value;
}

// ── the three things a caller wants ──────────────────────────────────

export function newKey() {
  return '0x' + hex(be32(randomScalar()));
}

export function addressOf(key) {
  const point = mul(G, scalarOf(key));
  const uncompressed = new Uint8Array(64);
  uncompressed.set(be32(point.x), 0);
  uncompressed.set(be32(point.y), 32);
  return checksum(hex(keccak256(uncompressed).slice(12)));
}

/** EIP-55: the capitalisation *is* the checksum, so wrong-key typos show up. */
export function checksum(address) {
  const lower = String(address).replace(/^0x/i, '').toLowerCase();
  const digest = hex(keccak256(lower));
  let out = '0x';
  for (let i = 0; i < lower.length; i++) {
    out += parseInt(digest[i], 16) >= 8 ? lower[i].toUpperCase() : lower[i];
  }
  return out;
}

/**
 * personal_sign, byte for byte what an extension would return.
 *
 * The EIP-191 prefix is not decoration: it is what stops a signature made over
 * a sign-in message from also being a valid signature over a transaction. The
 * server hashes the same way and recovers an address; if that address is the
 * one that asked for the challenge, the caller holds the key.
 */
export function personalSign(message, key) {
  const body = new TextEncoder().encode(message);
  const prefix = new TextEncoder().encode(`\x19Ethereum Signed Message:\n${body.length}`);
  const framed = new Uint8Array(prefix.length + body.length);
  framed.set(prefix);
  framed.set(body, prefix.length);
  const digest = BigInt('0x' + hex(keccak256(framed)));
  const secret = scalarOf(key);

  for (let tries = 0; tries < 64; tries++) {
    const k = randomScalar();
    const point = mul(G, k);
    const r = mod(point.x, N);
    if (r === 0n) continue;
    let s = mod(inv(k, N) * (digest + r * secret), N);
    if (s === 0n) continue;
    // Which of the two candidate public keys to recover, before and after the
    // low-s flip below — dropping this bit is what makes a signature
    // unrecoverable rather than merely non-standard.
    let recovery = (point.y & 1n ? 1 : 0) | (point.x >= N ? 2 : 0);
    if (s > N / 2n) { s = N - s; recovery ^= 1; }
    return '0x' + hex(be32(r)) + hex(be32(s)) + (27 + recovery).toString(16).padStart(2, '0');
  }
  throw new Error('could not produce a signature — the random source is failing');
}

export const bytesToHex = hex;
