/**
 * lsag.mjs — client-side LSAG signer (Liu-Wei-Wong 2004) for ztensor.
 *
 * Byte-for-byte compatible with ring.py and api/src/lsag.rs:
 *   SHA-512 over [len(part) as u64 BE || part], 256-byte BE group elements,
 *   domain tags "ztensor-h2g" / "ztensor-lsag-c", RFC 3526 MODP-2048,
 *   order-q QR subgroup (p = 2q+1), generator 4.
 *
 * Plain ESM + WebCrypto so it runs in the browser AND in node >= 18
 * (scripts/crosstest.mjs). SECRET KEYS NEVER LEAVE THIS FILE'S CALLER:
 * the service only ever sees public keys, tags, and choices.
 */

const P_HEX =
  'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1' +
  '29024E088A67CC74020BBEA63B139B22514A08798E3404DD' +
  'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245' +
  'E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED' +
  'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D' +
  'C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F' +
  '83655D23DCA3AD961C62F356208552BB9ED529077096966D' +
  '670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B' +
  'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9' +
  'DE2BCBF6955817183995497CEA956AE515D2261898FA0510' +
  '15728E5A8AACAA68FFFFFFFFFFFFFFFF';

export const P = BigInt('0x' + P_HEX);
export const Q = (P - 1n) / 2n; // prime order of the QR subgroup
export const G = 4n;            // 2^2: a QR, generates the order-q subgroup

const ELEM_BYTES = 256;
const te = new TextEncoder();

function modpow(base, exp, mod) {
  let r = 1n;
  base %= mod;
  while (exp > 0n) {
    if (exp & 1n) r = (r * base) % mod;
    base = (base * base) % mod;
    exp >>= 1n;
  }
  return r;
}

function beBytes(n, len = ELEM_BYTES) {
  const out = new Uint8Array(len);
  for (let i = len - 1; i >= 0 && n > 0n; i--) {
    out[i] = Number(n & 0xffn);
    n >>= 8n;
  }
  if (n > 0n) throw new Error('integer too large for encoding');
  return out;
}

function bytesToBig(buf) {
  let n = 0n;
  for (const b of buf) n = (n << 8n) | BigInt(b);
  return n;
}

function len8(n) {
  const out = new Uint8Array(8);
  let v = BigInt(n);
  for (let i = 7; i >= 0; i--) {
    out[i] = Number(v & 0xffn);
    v >>= 8n;
  }
  return out;
}

function concat(chunks) {
  const total = chunks.reduce((a, c) => a + c.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

async function hInt(parts) {
  const chunks = [];
  for (const p of parts) chunks.push(len8(p.length), p);
  const digest = await crypto.subtle.digest('SHA-512', concat(chunks));
  return bytesToBig(new Uint8Array(digest));
}

async function hashToGroup(parts) {
  let x = (await hInt([te.encode('ztensor-h2g'), ...parts])) % P;
  if (x === 0n) x = 2n;
  const e = modpow(x, 2n, P);
  return e === 1n ? G : e;
}

function ringPayload(ring) {
  return concat(ring.map((y) => beBytes(y)));
}

async function challenge(ring, tag, msg, a, b) {
  return (
    (await hInt([te.encode('ztensor-lsag-c'), ringPayload(ring), beBytes(tag), msg, beBytes(a), beBytes(b)])) % Q
  );
}

/** Per-(ring, topic) base: linkable within a topic, unlinkable across. */
export async function topicBase(ring, topicBytes) {
  const sorted = ring.slice().sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return hashToGroup([ringPayload(sorted), topicBytes]);
}

function randScalar() {
  // uniform-enough in [1, Q-1]: 2048+64 bits of entropy mod (Q-1), +1
  const buf = new Uint8Array(ELEM_BYTES + 8);
  crypto.getRandomValues(buf);
  return (bytesToBig(buf) % (Q - 1n)) + 1n;
}

/** Return { secret, pub } as BigInt. The secret stays with the caller. */
export function keygen() {
  const x = randScalar();
  return { secret: x, pub: modpow(G, x, P) };
}

export async function keyImage(secret, ring, topicBytes) {
  return modpow(await topicBase(ring, topicBytes), secret, P);
}

/**
 * Sign msg as an anonymous member of `ring` (array of BigInt public keys)
 * for `topic`. Returns { c0, s, tag } as decimal strings, the wire format
 * the API accepts.
 */
export async function sign(secret, ring, topic, msg) {
  const topicBytes = typeof topic === 'string' ? te.encode(topic) : topic;
  const msgBytes = typeof msg === 'string' ? te.encode(msg) : msg;
  const pub = modpow(G, secret, P);
  const pi = ring.findIndex((y) => y === pub);
  if (pi < 0) throw new Error('signer public key is not in the ring');
  const n = ring.length;
  const h = await topicBase(ring, topicBytes);
  const tag = modpow(h, secret, P);

  const c = new Array(n).fill(0n);
  const s = new Array(n).fill(0n);
  const u = randScalar();
  c[(pi + 1) % n] = await challenge(ring, tag, msgBytes, modpow(G, u, P), modpow(h, u, P));

  let i = (pi + 1) % n;
  while (i !== pi) {
    s[i] = randScalar();
    const a = (modpow(G, s[i], P) * modpow(ring[i], c[i], P)) % P;
    const b = (modpow(h, s[i], P) * modpow(tag, c[i], P)) % P;
    c[(i + 1) % n] = await challenge(ring, tag, msgBytes, a, b);
    i = (i + 1) % n;
  }

  s[pi] = (((u - secret * c[pi]) % Q) + Q) % Q;
  return { c0: c[0].toString(), s: s.map((x) => x.toString()), tag: tag.toString() };
}

/** True iff sig ({c0, s, tag} — BigInt or decimal strings) verifies. */
export async function verify(ring, topic, msg, sig) {
  try {
    const topicBytes = typeof topic === 'string' ? te.encode(topic) : topic;
    const msgBytes = typeof msg === 'string' ? te.encode(msg) : msg;
    const n = ring.length;
    const s = sig.s.map(BigInt);
    const tag = BigInt(sig.tag);
    const c0 = BigInt(sig.c0) % Q;
    if (s.length !== n || n === 0) return false;
    const h = await topicBase(ring, topicBytes);
    let c = c0;
    for (let i = 0; i < n; i++) {
      const a = (modpow(G, s[i], P) * modpow(ring[i], c, P)) % P;
      const b = (modpow(h, s[i], P) * modpow(tag, c, P)) % P;
      c = await challenge(ring, tag, msgBytes, a, b);
    }
    return c === c0;
  } catch {
    return false;
  }
}

export const toHex = (n) => n.toString(16);
export const fromHex = (h) => BigInt('0x' + h);
export const pubOf = (secret) => modpow(G, secret, P);
