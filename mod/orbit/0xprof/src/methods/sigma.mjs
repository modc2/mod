// A second opinion on the small systems, written from the specification
// rather than from the Python.
//
// The sigma protocols and Merkle inclusion are cheap enough that the only
// reason to have one verifier for them is laziness — and one verifier means
// a proof can never get past `claimed` here, because there is nothing to
// agree with it. So this file re-implements them: secp256k1 in BigInt, the
// Fiat-Shamir transcript, and the tree walk.
//
// It shares a runtime with the snarkjs method and nothing else. The arithmetic
// below was written against the same protocol description as src/methods/
// native.py, not against native.py itself, which is the only way a second
// implementation is worth having.
//
// stdin:  {"system":"schnorr","proof":{…},"statement":{…},"publicSignals":[…]}
// stdout: {"ok":true,"detail":{…}}
import { createHash } from 'node:crypto';
import { keccak_256 } from '@noble/hashes/sha3';

const P = 2n ** 256n - 2n ** 32n - 977n;
const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
const G = {
  x: 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n,
  y: 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n,
};

const mod = (a, m = P) => ((a % m) + m) % m;
const inv = (a, m = P) => {
  // Extended Euclid — Fermat would need modpow and this is clearer.
  let [old_r, r] = [mod(a, m), m];
  let [old_s, s] = [1n, 0n];
  while (r !== 0n) {
    const q = old_r / r;
    [old_r, r] = [r, old_r - q * r];
    [old_s, s] = [s, old_s - q * s];
  }
  return mod(old_s, m);
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

const onCurve = (p) => p !== null && mod(p.y * p.y - p.x * p.x * p.x - 7n) === 0n;

const bytes = (value) => {
  if (value === null || value === undefined) return Buffer.alloc(0);
  const text = String(value);
  if (text.startsWith('0x')) return Buffer.from(text.slice(2).padStart(
    text.length % 2 ? text.length - 1 : text.length - 2, '0'), 'hex');
  return Buffer.from(text, 'utf8');
};

const point = (value) => {
  if (value && typeof value === 'object' && 'x' in value) {
    const p = { x: BigInt(value.x), y: BigInt(value.y) };
    if (!onCurve(p)) throw new Error('point is not on secp256k1');
    return p;
  }
  const raw = bytes(value);
  let p;
  if (raw.length === 65 && raw[0] === 4) {
    p = { x: BigInt('0x' + raw.subarray(1, 33).toString('hex')),
          y: BigInt('0x' + raw.subarray(33).toString('hex')) };
  } else if (raw.length === 64) {
    p = { x: BigInt('0x' + raw.subarray(0, 32).toString('hex')),
          y: BigInt('0x' + raw.subarray(32).toString('hex')) };
  } else if (raw.length === 33 && (raw[0] === 2 || raw[0] === 3)) {
    const x = BigInt('0x' + raw.subarray(1).toString('hex'));
    let y = powmod(mod(x * x * x + 7n), (P + 1n) / 4n, P);
    if (y % 2n !== BigInt(raw[0] % 2)) y = P - y;
    p = { x, y };
  } else {
    throw new Error(`${raw.length} bytes is not a secp256k1 point`);
  }
  if (!onCurve(p)) throw new Error('point is not on secp256k1');
  return p;
};

function powmod(base, exp, m) {
  let result = 1n;
  let b = mod(base, m);
  let e = exp;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % m;
    b = (b * b) % m;
    e >>= 1n;
  }
  return result;
}

const p32 = (p) => Buffer.concat([
  Buffer.from(p.x.toString(16).padStart(64, '0'), 'hex'),
  Buffer.from(p.y.toString(16).padStart(64, '0'), 'hex'),
]);

// The transcript: domain tag, then every part length-prefixed, then the
// context. Length prefixes matter — without them two different statements can
// serialise to the same bytes and one proof verifies against both.
const challenge = (parts, context = '') => {
  const hasher = createHash('sha256').update(Buffer.from('0xprof/sigma/v1'));
  for (const part of parts) {
    const raw = part && typeof part === 'object' && 'x' in part ? p32(part) : bytes(part);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(raw.length);
    hasher.update(length).update(raw);
  }
  const ctx = Buffer.from(String(context || ''), 'utf8');
  const length = Buffer.alloc(4);
  length.writeUInt32BE(ctx.length);
  hasher.update(length).update(ctx);
  return mod(BigInt('0x' + hasher.digest('toString' in Buffer ? 'hex' : 'hex')), N);
};

const eq = (a, b) => (a === null || b === null ? a === b : a.x === b.x && a.y === b.y);

const digestFor = (name) => {
  const which = String(name || 'sha256').toLowerCase();
  if (['keccak256', 'keccak-256', 'keccak'].includes(which)) {
    return (data) => Buffer.from(keccak_256(data));
  }
  if (which === 'sha256') return (data) => createHash('sha256').update(data).digest();
  if (which === 'blake2s') return (data) => createHash('blake2s256').update(data).digest();
  if (which === 'sha512-256') return (data) => createHash('sha512-256').update(data).digest();
  throw new Error(`unknown hash ${which}`);
};

const VERIFIERS = {
  schnorr({ proof, statement, publicSignals }) {
    const P_ = point(statement.P || statement.pubkey || (publicSignals || [])[0]);
    const R = point(proof.R);
    const s = BigInt(proof.s);
    const c = challenge([P_, R], statement.context || proof.context || '');
    return {
      ok: eq(mul(G, s), add(R, mul(P_, c))),
      detail: { curve: 'secp256k1', check: 'sG == R + cP', challenge: '0x' + c.toString(16) },
    };
  },

  dleq({ proof, statement }) {
    const baseG = statement.G ? point(statement.G) : G;
    const baseH = point(statement.H);
    const A = point(statement.A);
    const B = point(statement.B);
    const R1 = point(proof.R1);
    const R2 = point(proof.R2);
    const s = BigInt(proof.s);
    const c = challenge([baseG, baseH, A, B, R1, R2], statement.context || proof.context || '');
    return {
      ok: eq(mul(baseG, s), add(R1, mul(A, c))) && eq(mul(baseH, s), add(R2, mul(B, c))),
      detail: { curve: 'secp256k1', check: 'sG == R1 + cA and sH == R2 + cB' },
    };
  },

  pedersen({ proof, statement }) {
    const C = point(statement.C || statement.commitment);
    const H = point(statement.H);
    const recomputed = add(mul(G, BigInt(proof.v)), mul(H, BigInt(proof.r)));
    return { ok: eq(recomputed, C), detail: { curve: 'secp256k1', check: 'C == vG + rH' } };
  },

  merkle({ proof, statement, publicSignals }) {
    const digest = digestFor(statement.hash || proof.hash);
    const root = bytes(statement.root || proof.root);
    const leaf = proof.leaf !== undefined ? proof.leaf : (publicSignals || [])[0];
    const prehash = statement.hash_leaf !== undefined ? !!statement.hash_leaf
      : (proof.hash_leaf !== undefined ? !!proof.hash_leaf : true);
    const sortedPairs = !!(statement.sorted ?? proof.sorted ?? false);
    let node = prehash ? digest(bytes(leaf)) : bytes(leaf);
    for (const rawStep of proof.path || proof.siblings || []) {
      const step = typeof rawStep === 'string' ? { sibling: rawStep, position: 'right' } : rawStep;
      const sibling = bytes(step.sibling || step.hash);
      const position = String(step.position ?? step.side ?? 'right').toLowerCase();
      let pair;
      if (sortedPairs) {
        pair = Buffer.compare(node, sibling) <= 0
          ? Buffer.concat([node, sibling]) : Buffer.concat([sibling, node]);
      } else if (['left', '0', 'false'].includes(position)) {
        pair = Buffer.concat([sibling, node]);
      } else {
        pair = Buffer.concat([node, sibling]);
      }
      node = digest(pair);
    }
    return {
      ok: Buffer.compare(node, root) === 0,
      detail: { depth: (proof.path || proof.siblings || []).length,
                computed_root: '0x' + node.toString('hex') },
    };
  },
};

const read = async (stream) => {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
};
const done = (payload, code = 0) => {
  process.stdout.write(JSON.stringify(payload));
  process.exit(code);
};

try {
  const input = JSON.parse(await read(process.stdin));
  const verifier = VERIFIERS[input.system];
  if (!verifier) done({ ok: false, error: `node cannot verify ${input.system}` }, 3);
  const started = Date.now();
  const answer = verifier({ proof: input.proof || {}, statement: input.statement || {},
                            publicSignals: input.publicSignals || [] });
  done({ ok: !!answer.ok, detail: { ...answer.detail, implementation: 'node/bigint',
                                    ms: Date.now() - started } });
} catch (e) {
  done({ ok: false, error: String(e?.message || e), malformed: true }, 4);
}
